"""Score person tripwires on three fixed-camera MOT17 sequences."""

import argparse
from collections import defaultdict
from configparser import ConfigParser
import csv
from datetime import datetime
import json
import math
from pathlib import Path
import re
import subprocess
import sys
from zipfile import ZipFile

from benchmark_cctv_meva import ROOT, START, run_worker
from evaluate_cctv_replay import sampled_frames
from prepare_cctv_analytics_demo import retrieve

sys.path.insert(0, str(ROOT / "services/camera-gateway"))

from site_geometry import cross
from video_analytics.tracking import Analytics, Detection, overlap


ORIGIN = "https://motchallenge.net"
LABEL_DIGEST = "0aa79322e91583369f42f17c4d79a0b145380d8732487bba59272048dc82b2b9"
VIDEOS = {
    "02": "d560bc50e1942d5c7335c937c8ac513aa87bb41ea51ae21fa02f661151d5a066",
    "04": "ab0d2fc03bce5e112abe9a173de7a2d70b39875c0af3eca49d6160c6e317e495",
    "09": "44472faa5643b173fa9b0d4837d3018cd1f5c4a3f13ab4bc9f865cf552022f9d",
}
POLICY = {"version": 1, "rules": [
    {"id": name, "name": name, "kind": "line-crossing", "object": "person", "boundaryId": "scene", "mode": "observe",
     "points": points, "direction": direction, "minimumScore": .25, "seconds": .5, "minimumPixels": 12}
    for name, points, direction in (
        ("vertical-right", [[.5, .4], [.5, .95]], "negative"),
        ("vertical-left", [[.5, .4], [.5, .95]], "positive"),
        ("horizontal-up", [[.2, .66], [.8, .66]], "negative"),
        ("horizontal-down", [[.2, .66], [.8, .66]], "positive"),
    )
]}


def read_labels(archive, camera):
    frames, tracks = defaultdict(dict), defaultdict(list)
    base = f"train/MOT17-{camera}-DPM"
    with ZipFile(archive) as labels:
        parser = ConfigParser()
        parser.read_string(labels.read(f"{base}/seqinfo.ini").decode())
        section = parser["Sequence"]
        width, height, rate, length = (section.getint(key) for key in ("imWidth", "imHeight", "frameRate", "seqLength"))
        for row in csv.reader(labels.read(f"{base}/gt/gt.txt").decode().splitlines()):
            frame, actor, x, y, w, h, marked, category, _ = map(float, row)
            if marked != 1 or category != 1:
                continue
            box = tuple(max(0., min(1., axis)) for axis in (x / width, y / height, (x + w) / width, (y + h) / height))
            if box[0] >= box[2] or box[1] >= box[3]:
                continue
            frames[int(frame)][int(actor)] = box
            tracks[int(actor)].append((int(frame), box))
    return dict(frames), {actor: sorted(path) for actor, path in tracks.items()}, rate, length


def reference_crossings(tracks, rate, rule, dimensions):
    start, end = rule["points"]
    length = math.dist(start, end)
    direction = 1 if rule["direction"] == "positive" else -1
    width, height = dimensions
    crossings = {}
    for actor, path in tracks.items():
        previous, since, anchor, side = None, None, None, 0
        for frame, box in path:
            if max((box[2] - box[0]) * width, (box[3] - box[1]) * height) < rule["minimumPixels"]:
                previous = None
                continue
            if previous is None or frame != previous + 1:
                since, anchor, side = None, None, 0
            previous = frame
            point = ((box[0] + box[2]) / 2, box[3])
            signed = cross(start, end, point) / length
            if abs(signed) < .012:
                continue
            observed_side = 1 if signed > 0 else -1
            if observed_side == direction and side == -direction and (frame - since) / rate >= rule["seconds"]:
                before = cross(start, end, anchor)
                fraction = before / (before - length * signed)
                intersection = tuple(x + fraction * (y - x) for x, y in zip(anchor, point))
                position = sum((value - a) * (b - a) for value, a, b in zip(intersection, start, end)) / length ** 2
                if 0 <= position <= 1:
                    crossings[actor] = (frame - 1) / rate
                    break
            if side != observed_side:
                since = frame
            anchor, side = point, observed_side
    return crossings


def score(events, frames, truth, frame_map, fps):
    options = []
    for seconds, event in events:
        rule = event["ruleId"]
        boxes = frames.get(frame_map[round(seconds * fps)], {})
        matches = [(overlap(event["box"], box), (rule, actor)) for actor, box in boxes.items()
                   if actor in truth[rule] and abs(seconds - truth[rule][actor]) <= 1.]
        options.append([key for quality, key in sorted(matches, reverse=True) if quality >= .30])
    assignments = {}

    def assign(index, visited):
        for key in options[index]:
            if key in visited:
                continue
            visited.add(key)
            if key not in assignments or assign(assignments[key], visited):
                assignments[key] = index
                return True
        return False

    for index in sorted(range(len(events)), key=lambda index: len(options[index])):
        assign(index, set())
    matched_indexes = set(assignments.values())
    extras = [{"seconds": seconds, "rule": event["ruleId"], "box": event["box"],
               "classification": "duplicate" if options[index] else "unmatched"}
              for index, (seconds, event) in enumerate(events) if index not in matched_indexes]
    return {"expected": sum(map(len, truth.values())), "matched": len(assignments),
            "missed": sum(map(len, truth.values())) - len(assignments),
            "duplicates": sum(event["classification"] == "duplicate" for event in extras),
            "unmatched": sum(event["classification"] == "unmatched" for event in extras),
            "matchedActorRules": sorted(assignments), "extraObservations": extras}


def evaluate(rows, frames, truth, frame_map, dimensions, fps, *, recovery=False, motion=False):
    engine = Analytics(1.2, person_tripwire_recovery=recovery, person_motion_association=motion)
    events = []
    for index, detections in enumerate(rows):
        stamp = index / fps
        events.extend((stamp, payload) for _, payload in engine.sample(detections, POLICY, stamp, dimensions))
    return score(events, frames, truth, frame_map, fps)


def benchmark(directory, camera, model, fps):
    basename = f"MOT17-{camera}.mp4"
    retrieve(directory, basename, f"{ORIGIN}/sequenceVideos/MOT17-{camera}-DPM-raw.mp4", VIDEOS[camera])
    video = directory / basename
    frames, tracks, source_rate, length = read_labels(directory / "MOT17Labels.zip", camera)
    mapped, width, height = sampled_frames(video, fps)
    expected_samples = round(length / source_rate * fps)
    if (set(mapped) != set(range(expected_samples))
            or any(len(indexes) != 1 or indexes[0] >= length for indexes in mapped.values())):
        raise ValueError(f"Cannot map every {basename} sample to one labeled source frame.")
    frame_map = {sample: indexes[0] + 1 for sample, indexes in mapped.items()}
    dimensions = width, height
    truth = {rule["id"]: reference_crossings(tracks, source_rate, rule, dimensions) for rule in POLICY["rules"]}
    color_enabled = model.get("personColorAssociation", False)
    suffix = ("-detail" if model.get("detailRegion") else "") + ("-color" if color_enabled else "")
    output = directory / f"mot17-{camera}-{model['sha256'][:8]}-{fps}fps{suffix}"
    output.mkdir(exist_ok=True)
    policy = output / "policy.json"
    policy.write_text(json.dumps(POLICY))
    observations, elapsed = run_worker(output, video, camera, "person", policy, fps, dimensions, model,
                                       sensor_id=f"mot17-{camera}")
    with (output / "person-detections.jsonl").open() as stream:
        trace = [json.loads(line) for line in stream]
    if len(trace) != expected_samples or any(
            not math.isclose(row["seconds"], index / fps) or row["frame"] != list(dimensions)
            for index, row in enumerate(trace)):
        raise ValueError(f"The worker trace does not match the sampled frames of {basename}.")
    rows = [[Detection(**item) for item in row["detections"]] for row in trace]
    labeled = [[Detection("person", 1., box, "reference") for box in frames.get(frame_map[i], {}).values()]
               for i in range(expected_samples)]
    result = {"sequence": f"MOT17-{camera}", "decodeSize": dimensions, "referenceCounts": {k: len(v) for k, v in truth.items()},
              "worker": elapsed, "baseline": evaluate(rows, frames, truth, frame_map, dimensions, fps),
              "recovery": evaluate(rows, frames, truth, frame_map, dimensions, fps, recovery=True),
              "motionAssociation": evaluate(rows, frames, truth, frame_map, dimensions, fps, motion=True),
              "recoveryAndMotion": evaluate(rows, frames, truth, frame_map, dimensions, fps, recovery=True, motion=True),
              "labeledBoxes": evaluate(labeled, frames, truth, frame_map, dimensions, fps)}
    if color_enabled:
        events = [((datetime.fromisoformat(item["occurredAt"]) - START).total_seconds(), item["analytics"])
                  for item in observations]
        result["colorAndRecovery"] = score(events, frames, truth, frame_map, fps)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--case", choices=VIDEOS, action="append")
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--provider", default="CPUExecutionProvider")
    parser.add_argument("--fps", type=int, default=4, choices=(4, 8))
    parser.add_argument("--detail-region", type=float, nargs=4, metavar=("LEFT", "TOP", "RIGHT", "BOTTOM"),
                        help="Test a normalized person detail crop for the selected views")
    parser.add_argument("--color-association", action="store_true",
                        help="Also score the worker's color-based association and gap recovery")
    args = parser.parse_args()
    directory = args.directory.expanduser().resolve()
    if directory.is_relative_to(ROOT) or not re.fullmatch(r"[a-f0-9]{64}", args.sha256):
        parser.error("Use a cache outside the checkout and a pinned YOLO11 export digest.")
    try:
        retrieve(directory, "MOT17Labels.zip", f"{ORIGIN}/data/MOT17Labels.zip", LABEL_DIGEST)
        model = {"kind": "yolo11", "path": str(args.model.expanduser().resolve(strict=True)), "sha256": args.sha256,
                 "provider": args.provider, "fps": args.fps}
        if args.detail_region is not None:
            model["detailRegion"] = args.detail_region
        if args.color_association:
            model.update(personColorAssociation=True, personTripwireRecovery=True)
        cases = dict.fromkeys(args.case or VIDEOS)
        results = [benchmark(directory, camera, model, args.fps) for camera in cases]
    except (KeyError, OSError, ValueError, subprocess.CalledProcessError) as error:
        parser.error(str(error))
    print(json.dumps({"modelSha256": args.sha256, "fps": args.fps, "detailRegion": args.detail_region,
                      "cases": results}, indent=2))


if __name__ == "__main__":
    main()
