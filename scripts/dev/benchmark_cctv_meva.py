"""Score Global View's entrance tripwire against a labeled public MEVA clip."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

from evaluate_cctv_replay import iou, sampled_frames
from prepare_cctv_analytics_demo import FILES, retrieve

ROOT = Path(__file__).resolve().parents[2]
BUCKET = "https://mevadata-public-01.s3.amazonaws.com/"
CLIP = "2018-03-07.16-50-00.16-55-00.admin.G329"
ANNOTATIONS = f"{BUCKET}examples/annotations/2018-03-07/16/{CLIP}"
ASSETS = {
    "g329.avi": (f"{BUCKET}drops-123-r13/2018-03-07/16/{CLIP}.r13.avi",
                 "880515f59d9c0d876c73272ba8f67f2194bcd15ac1f6873f4451646ac9663853"),
    "g329.activities.yml": (ANNOTATIONS + ".activities.yml", "a2e47e05c2f5464429cfea00023bdc6ef68927a7ffcbb6ce2e30026477e82d0c"),
    "g329.geom.yml": (ANNOTATIONS + ".geom.yml", "551c1e159e73f5aea20ed1b85be98e25a1500b74be284676ca51c6c157f190c7"),
    "g329.types.yml": (ANNOTATIONS + ".types.yml", "e3dbb904fe5ddbbb830a1e73bd3ec71f93946dd49cdeb2764c2589c6bdf54aea"),
}
MODELS = {
    "nano": FILES["yolox_nano.onnx"],
    "tiny": ("https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/yolox_tiny.onnx",
             "427cc366d34e27ff7a03e2899b5e3671425c262ea2291f88bb942bc1cc70b0f7"),
}
SOURCE_FPS = 30
WIDTH, HEIGHT = 1920, 1072
FIRST_FRAME, END_FRAME = 2580, 2820
SAMPLE_FPS = 4
START = datetime(2026, 1, 1, tzinfo=timezone.utc)
TOLERANCE_SECONDS = 1
MINIMUM_IOU = .30
RULE = {
    "id": "entrance", "name": "Person crosses entrance line", "kind": "line-crossing", "object": "person",
    "mode": "observe", "boundaryId": "demo", "points": [[.55, .42], [.55, .62]], "direction": "positive",
    "minimumScore": .25, "minimumPixels": 12, "seconds": .5,
}


def source_time(frame):
    return (frame - FIRST_FRAME) / SOURCE_FPS


def read_truth(output):
    import yaml

    def packets(kind, key):
        return [packet[key] for packet in yaml.safe_load((output / f"g329.{kind}.yml").read_text()) if key in packet]

    activities = [act for act in packets("activities", "act") if "Enter_Facility" in act["act3"]]
    if len(activities) != 1 or len(activities[0]["actors"]) != 1:
        raise ValueError("The seed clip requires one labeled facility entry by one actor.")
    actor = activities[0]["actors"][0]
    first, last = actor["timespan"][0]["tsr0"]
    if not any(item["id1"] == actor["id1"] and "Person" in item["cset3"] for item in packets("types", "types")):
        raise ValueError("The entry actor must have a person label.")
    boxes = {}
    for geometry in packets("geom", "geom"):
        frame = geometry["ts0"]
        if geometry["id1"] == actor["id1"] and first <= frame <= last:
            boxes[frame] = tuple(float(axis) / dimension for axis, dimension in zip(geometry["g0"].split(), (WIDTH, HEIGHT, WIDTH, HEIGHT)))
    if sorted(boxes) != list(range(first, last + 1)):
        raise ValueError("The annotated activity must have a complete actor track.")
    return boxes


def crossing_time(boxes, line):
    (x, top), (_, bottom) = line
    anchors = [(frame, (left + right) / 2, floor) for frame, (left, _, right, floor) in sorted(boxes.items())]
    crossings = []
    for (previous, before, y0), (current, after, y1) in zip(anchors, anchors[1:]):
        if before > x >= after:
            fraction = (before - x) / (before - after)
            if top <= y0 + fraction * (y1 - y0) <= bottom:
                crossings.append(source_time(previous + fraction * (current - previous)))
    if len(crossings) != 1:
        raise ValueError("Expected one directed entrance crossing in the reference track.")
    return crossings[0]


def score(events, boxes, expected, frame_map):
    matched = []
    unrelated = 0
    for event in events:
        seconds = (datetime.fromisoformat(event["occurredAt"]) - START).total_seconds()
        possible = {boxes.get(FIRST_FRAME + frame) for frame in frame_map[round(seconds * SAMPLE_FPS)]}
        box = possible.pop() if len(possible) == 1 else None
        if (event["analytics"]["ruleId"] == RULE["id"] and box is not None
                and abs(seconds - expected) <= TOLERANCE_SECONDS and iou(event["analytics"]["box"], box) >= MINIMUM_IOU):
            matched.append(seconds)
        else:
            unrelated += 1
    return {"matchedEvents": int(bool(matched)), "missedEvents": int(not matched), "duplicateMatches": max(0, len(matched) - 1),
            "unreviewedEvents": unrelated, "crossingTimeErrorSeconds": round(min(matched) - expected, 3) if matched else None}


def run_worker(output, video, name, policy):
    model = output / f"yolox_{name}.onnx"
    digest = MODELS[name][1]
    config = output / f"{name}-config.json"
    config.write_text(json.dumps({"sensorId": "meva-g329", "width": 640, "height": 358, "fps": SAMPLE_FPS,
                                  "models": {"ground": {"kind": "yolox", "path": str(model), "sha256": digest}}}))
    run = subprocess.run([sys.executable, str(ROOT / "services/camera-gateway/analytics_worker.py"), "--config", str(config),
                          "--replay", str(video), "--policy", str(policy), "--start-at", START.isoformat(),
                          "--detections", str(output / f"{name}-detections.jsonl")], capture_output=True, text=True)
    if run.returncode:
        raise ValueError(f"Replay failed for YOLOX-{name}: {run.stderr.strip()}")
    (output / f"{name}-events.jsonl").write_text(run.stdout)
    return [json.loads(row) for row in run.stdout.splitlines()], json.loads(run.stderr.splitlines()[-1])["ground"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="External data and results directory")
    parser.add_argument("--models", nargs="+", choices=MODELS, default=list(MODELS))
    args = parser.parse_args()
    output = args.output.expanduser().resolve()
    if output.is_relative_to(ROOT):
        parser.error("Keep the downloaded data and generated results outside the checkout.")
    models = list(dict.fromkeys(args.models))
    try:
        for name, (url, digest) in {**ASSETS, **{f"yolox_{name}.onnx": MODELS[name] for name in models}}.items():
            retrieve(output, name, url, digest)
        boxes = read_truth(output)
        expected = crossing_time(boxes, RULE["points"])
        segment = output / "g329-entrance.mkv"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(output / "g329.avi"), "-map", "0:v:0", "-an", "-sn", "-dn",
                        "-vf", f"trim=start_frame={FIRST_FRAME}:end_frame={END_FRAME},setpts=PTS-STARTPTS",
                        "-c:v", "ffv1", str(segment)], check=True)
        frame_map, width, height = sampled_frames(segment, SAMPLE_FPS)
        if (width, height) != (WIDTH, HEIGHT):
            raise ValueError("Unexpected source image size for the MEVA geometry.")
        policy = output / "tripwire-policy.json"
        policy.write_text(json.dumps({"version": 1, "rules": [RULE]}, indent=2) + "\n")
        results = []
        for name in models:
            events, runtime = run_worker(output, segment, name, policy)
            results.append({"model": f"YOLOX-{name}", "sha256": MODELS[name][1], **runtime, **score(events, boxes, expected, frame_map)})
    except (ImportError, OSError, ValueError, subprocess.CalledProcessError) as issue:
        parser.error(str(issue))
    report = {"clip": CLIP, "source": ASSETS["g329.avi"][0], "license": "CC BY 4.0",
              "sourceFrames": [FIRST_FRAME, END_FRAME - 1], "annotatedFrames": [min(boxes), max(boxes)],
              "sampleFps": SAMPLE_FPS, "expectedEvents": 1, "expectedCrossingSeconds": round(expected, 3),
              "matchToleranceSeconds": TOLERANCE_SECONDS, "minimumBoxIou": MINIMUM_IOU,
              "negativeFootageReviewed": False, "results": results}
    encoded = json.dumps(report, indent=2) + "\n"
    (output / "meva-report.json").write_text(encoded)
    print(encoded, end="")


if __name__ == "__main__":
    main()
