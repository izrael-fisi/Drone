"""Score Global View's detector and rules against labeled public MEVA clips."""

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
MODELS = {
    "nano": FILES["yolox_nano.onnx"],
    "tiny": ("https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/yolox_tiny.onnx",
             "427cc366d34e27ff7a03e2899b5e3671425c262ea2291f88bb942bc1cc70b0f7"),
    "small": ("https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/yolox_s.onnx",
              "c5c2d13e59ae883e6af3b45daea64af4833a4951c92d116ec270d9ddbe998063"),
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
G301_DWELL_RULE = {**RULE, "id": "exit-dwell", "name": "15 seconds near the exit", "kind": "zone-dwell",
                   "points": [[.64, .39], [.72, .39], [.72, .56], [.64, .56]], "seconds": 15, "dwellGapSeconds": .75}
G299_RULE = {**RULE, "id": "distant-door", "name": "Person at distant doorway", "kind": "zone-entry",
             "points": [[.74, .23], [.79, .23], [.79, .30], [.74, .30]]}
G336_RULE = {**RULE, "id": "roadway-stop", "name": "Vehicle stopped in road", "kind": "restricted-stop", "object": "vehicle",
             "points": [[.326, .214], [.40, .214], [.40, .269], [.326, .269]]}
G336_CLIP = "2018-03-07.16-50-06.16-55-06.school.G336"
G336_DIGESTS = ("8f4a4698c1b9ac72fb3f74cc4a31c6895cbce3f94d074739a3329a0d9f052ae3",
                "d4c1838940aed5adea8ee683a7afc2814e1c2b044b3ac1c94203291b83ee92fc",
                "744fc0271e24bc2323cf3c849fb3715d342b6bc622f962fdb24c39e23bcc348a",
                "34ec792d04ca15a6054d8e4c76ed503a97e21d350b971a0c96a0e13dc6a3efaf")
ZONE_CASE_COUNTS = {"g299-march07": 1, "g299-march13": 1, "g328": 1, "g336-stop": 1, "g336-turn": 0, "g638": 1}
CASES = {
    "g329": (CLIP, "Enter_Facility", FIRST_FRAME, END_FRAME, RULE,
             ("880515f59d9c0d876c73272ba8f67f2194bcd15ac1f6873f4451646ac9663853",
              "a2e47e05c2f5464429cfea00023bdc6ef68927a7ffcbb6ce2e30026477e82d0c",
              "551c1e159e73f5aea20ed1b85be98e25a1500b74be284676ca51c6c157f190c7",
              "e3dbb904fe5ddbbb830a1e73bd3ec71f93946dd49cdeb2764c2589c6bdf54aea")),
    "g326": ("2018-03-07.16-50-01.16-55-01.admin.G326", "Enter_Facility", 2530, 2770,
             {**RULE, "points": [[.52, .50], [.52, 1]], "direction": "negative"},
             ("7208e768d896e34a2c908c826b14f61f33584254b15fc04894c82a193912023e",
              "3a133255a4723c3fc89e53c00b01f26e3bf2a0bd87523405bac1421856d53d48",
              "992a3d8bec5b0b993e1c69493f01fdbdef6d84a96c8cd883fa7fb3322ecd5591",
              "dc764b08d75a7ef2051e3553a141ae558606d11ddd4638a43170378441f4ce06")),
    "g328": ("2018-03-07.16-50-01.16-55-01.school.G328", "Vehicle_Stopping", 7950, 8190,
             {**RULE, "id": "vehicle-stop", "name": "Vehicle stopped", "kind": "restricted-stop", "object": "vehicle",
              "points": [[.54, .58], [.70, .58], [.70, .73], [.54, .73]]},
             ("722e6636c3f859b2855b796cdc91899cb8c80284cf4f0b7e5c95ef06bf3c2c41",
              "cf910e2ec26ebe8b20b3b266bc8397a1af53426e018af0c67dfbc47580b725ff",
              "30b4c26b6b088f5d161700c04bd901c00233265d966c0f79e832eaf700432e4e",
              "25cbef61c076eaf59b18aa6fab840a5b930635ef9d2a2c5828545ebee0c02d79")),
    "g301": ("2018-03-07.16-50-07.16-55-07.hospital.G301", "Exit_Facility", 2820, 3620,
             {**RULE, "id": "exit-observation", "name": "Person in exit view", "kind": "zone-entry",
              "points": [[0, 0], [1, 0], [1, 1], [0, 1]]},
             ("0085509e8895bae57c9acd9042eae2bbb7e5d64faa1112f66491cb6a59801dd9",
              "acc206c220074fb6344eeb128688058e95afd970754631a1ed0dee2225645f4a",
              "b226cd4a692ff8f8842be3aa28a1b378a420c4e98494c2908e1353e662e66ec3",
              "41a77e654fcae7e542d68434e611eeed6b178f2521cbb91384b9e71067695bcf")),
    "g299-march07": ("2018-03-07.17-05-00.17-10-00.school.G299", "Exit_Facility", 4410, 4520, G299_RULE,
                    ("e518b406c685afc875614055d148fb4a983ba72f02f376d17b26923fd5684ce9",
                     "820afe41fcfb8273358a6390e4c510d6d96d7e8ad3d6ba3358c1f8fba82bcf3f",
                     "3069b21d6e3a68fe58ec33856e1041f3b8fbeee95a7c920982ec60cecd5d0cbc",
                     "72171debcd3980c75f34a841153785c268f42ba77aa3306a9f4163fb52df8d13")),
    "g299-march13": ("2018-03-13.17-35-00.17-40-00.school.G299", "Exit_Facility", 5734, 5806, G299_RULE,
                    ("cb23b3261e5e156774c02599d0450839666a232eef2ecf66fa45e4105f23a38a",
                     "b17f80926c3cc28d0bca6587ff21f6fdbff1ba657cac19ce64e63f27359a63bd",
                     "f935cd14f89a149b11e22faa0b111c6036df6e6d3f1ee6d8bbdbffb8e34f1c73",
                     "c26f1ac982cb34bcbea731a211986542d90a8486098fbcd86bdb28441af04cb6")),
    "g336-stop": (G336_CLIP, "Vehicle_Stopping", 1249, 1280, G336_RULE, G336_DIGESTS),
    "g336-turn": (G336_CLIP, "Vehicle_Turning_Right", 1519, 1577, G336_RULE, G336_DIGESTS),
    "g638": ("2018-03-12.10-45-00.10-50-00.school.G638", "Enter_Facility", 8660, 8800,
             {**RULE, "id": "shadow-door", "name": "Person at shadowed doorway", "kind": "zone-entry",
              "points": [[.22, .49], [.32, .49], [.32, .56], [.22, .56]]},
             ("0a882ac18f107dd20895612318fb464c8b0b534649127b0a89c6c2d4d072679a",
              "6919f2609ee6de13a0b748739121ed819ad9041c8ec4e11c3edf5b244c51e563",
              "5c96ec7fdc690db425c9cc81ebb9c1c1061dc1ab3e86f4c54fffaa02de35f7a2",
              "66ed13462c6f3775e0470406841b4b6ec7a159044cd93ff6c35d15ef379e3e29")),
}


def case_assets(camera, clip, digests):
    day, begin = clip.split(".")[:2]
    folder = f"{day}/{begin[:2]}/{clip}"
    annotation = f"{BUCKET}examples/annotations/{folder}"
    return {
        f"{camera}.avi": (f"{BUCKET}drops-123-r13/{folder}.r13.avi", digests[0]),
        **{f"{camera}.{kind}.yml": (f"{annotation}.{kind}.yml", digest)
           for kind, digest in zip(("activities", "geom", "types"), digests[1:])},
    }


def read_truth(output, camera="g329", activity="Enter_Facility", object_type="person", dimensions=(WIDTH, HEIGHT)):
    import yaml

    def packets(kind, key):
        return [packet[key] for packet in yaml.safe_load((output / f"{camera}.{kind}.yml").read_text()) if key in packet]

    activities = [act for act in packets("activities", "act") if activity in act["act3"]]
    if len(activities) != 1 or len(activities[0]["actors"]) != 1:
        raise ValueError("The fixture requires one labeled activity by one actor.")
    actor = activities[0]["actors"][0]
    first, last = actor["timespan"][0]["tsr0"]
    if not any(item["id1"] == actor["id1"] and object_type.title() in item["cset3"] for item in packets("types", "types")):
        raise ValueError("The actor's label must match the fixture's object class.")
    boxes = {}
    for geometry in packets("geom", "geom"):
        frame = geometry["ts0"]
        if geometry["id1"] == actor["id1"] and first <= frame <= last:
            boxes[frame] = tuple(float(axis) / dimension for axis, dimension in zip(geometry["g0"].split(), dimensions * 2))
    if sorted(boxes) != list(range(first, last + 1)):
        raise ValueError("The annotated activity must have a complete actor track.")
    return boxes


def crossing_reference(boxes, line, first_frame=FIRST_FRAME, direction="positive"):
    (x, top), (_, bottom) = line
    anchors = [(frame, (left + right) / 2, floor) for frame, (left, _, right, floor) in sorted(boxes.items())]
    crossings = []
    for (previous, before, y0), (current, after, y1) in zip(anchors, anchors[1:]):
        crossed = before > x >= after if direction == "positive" else before < x <= after
        if crossed:
            fraction = (before - x) / (before - after)
            floor = y0 + fraction * (y1 - y0)
            if top <= floor <= bottom:
                seconds = (previous + fraction * (current - previous) - first_frame) / SOURCE_FPS
                crossings.append((seconds, (x, floor)))
    if len(crossings) != 1:
        raise ValueError("Expected one directed entrance crossing in the reference track.")
    return crossings[0]


def crossing_time(boxes, line, first_frame=FIRST_FRAME, direction="positive"):
    return crossing_reference(boxes, line, first_frame, direction)[0]


def aligned_box(boxes, frame_map, index, first_frame):
    possible = {boxes.get(first_frame + frame) for frame in frame_map[index]}
    return possible.pop() if len(possible) == 1 else None


def g301_dwell_window(boxes, first_frame):
    (left, top), _, (right, bottom), _ = G301_DWELL_RULE["points"]
    inside = sorted(frame for frame, (a, _, c, d) in boxes.items() if left <= (a + c) / 2 <= right and top <= d <= bottom)
    if not inside or len(inside) != inside[-1] - inside[0] + 1:
        raise ValueError("Expected a continuous doorway dwell in the reference track.")
    threshold = (inside[0] - first_frame) / SOURCE_FPS + G301_DWELL_RULE["seconds"]
    end = (inside[-1] - first_frame) / SOURCE_FPS
    if end < threshold:
        raise ValueError("The labeled actor leaves before the benchmark dwell threshold.")
    return threshold, end


def score_zone_events(events, boxes, window, frame_map, fps, first_frame, rule_id, expected_count):
    matched = []
    for event in events:
        seconds = (datetime.fromisoformat(event["occurredAt"]) - START).total_seconds()
        box = aligned_box(boxes, frame_map, round(seconds * fps), first_frame)
        if (event["analytics"]["ruleId"] == rule_id and box is not None
                and window[0] <= seconds <= window[1] and iou(event["analytics"]["box"], box) >= MINIMUM_IOU):
            matched.append(seconds)
    return {"matchedEvents": int(bool(matched) and expected_count > 0), "missedEvents": int(expected_count > 0 and not matched),
            "duplicateMatches": max(0, len(matched) - 1) if expected_count else 0,
            "falseEventsOnLabeledActor": len(matched) if expected_count == 0 else 0,
            "unreviewedEvents": len(events) - len(matched),
            "zoneTimeErrorSeconds": round(min(matched) - window[0], 3) if matched and expected_count else None}


def score(events, boxes, expected, frame_map, fps=SAMPLE_FPS, first_frame=FIRST_FRAME, rule_id=RULE["id"],
          *, reference_point=None, source_dimensions=(WIDTH, HEIGHT)):
    matched = []
    unrelated = 0
    for event in events:
        seconds = (datetime.fromisoformat(event["occurredAt"]) - START).total_seconds()
        box = aligned_box(boxes, frame_map, round(seconds * fps), first_frame)
        if (event["analytics"]["ruleId"] == rule_id and box is not None
                and abs(seconds - expected) <= TOLERANCE_SECONDS and iou(event["analytics"]["box"], box) >= MINIMUM_IOU):
            matched.append((seconds, event["analytics"].get("crossing")))
        else:
            unrelated += 1
    seconds, point = min(matched, key=lambda item: item[0]) if matched else (None, None)
    result = {"matchedEvents": int(bool(matched)), "missedEvents": int(not matched),
              "duplicateMatches": max(0, len(matched) - 1), "unreviewedEvents": unrelated,
              "crossingTimeErrorSeconds": round(seconds - expected, 3) if seconds is not None else None}
    if reference_point is not None:
        result["crossingPoint"] = point
        result["crossingLocationErrorSourcePixels"] = (
            round(sum(((axis - target) * size) ** 2 for axis, target, size in zip(point, reference_point, source_dimensions)) ** .5, 2)
            if point is not None else None)
    return result


def score_boxes(trace, boxes, frame_map, fps, first_frame, object_type):
    annotated = matched = 0
    with trace.open() as stream:
        for line in stream:
            row = json.loads(line)
            box = aligned_box(boxes, frame_map, round(row["seconds"] * fps), first_frame)
            if box is None:
                continue
            annotated += 1
            if any(item["label"] == object_type and iou(item["box"], box) >= MINIMUM_IOU for item in row["detections"]):
                matched += 1
    available = sum(aligned_box(boxes, frame_map, index, first_frame) is not None for index in frame_map)
    return {"availableAnnotatedSampleFrames": available, "annotatedSampleFrames": annotated, "framesWithMatchedActor": matched}


def run_worker(output, video, camera, name, policy, fps, shape, model, *, sensor_id=None):
    config = output / f"{name}-config.json"
    config.write_text(json.dumps({"sensorId": sensor_id or f"meva-{camera}", "width": shape[0], "height": shape[1], "fps": fps,
                                  "models": {"ground": model}}))
    run = subprocess.run([sys.executable, str(ROOT / "services/camera-gateway/analytics_worker.py"), "--config", str(config),
                          "--replay", str(video), "--policy", str(policy), "--start-at", START.isoformat(),
                          "--detections", str(output / f"{name}-detections.jsonl")], capture_output=True, text=True)
    if run.returncode:
        raise ValueError(f"Replay failed for {name}: {run.stderr.strip()}")
    (output / f"{name}-events.jsonl").write_text(run.stdout)
    return [json.loads(row) for row in run.stdout.splitlines()], json.loads(run.stderr.splitlines()[-1])["ground"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="External data and results directory")
    parser.add_argument("--models", nargs="+", choices=[*MODELS, "yolo11s", "yolo11l"], default=["nano", "tiny"])
    parser.add_argument("--yolo11", type=Path, help="Path to an approved YOLO11s ONNX export")
    parser.add_argument("--yolo11-sha256", help="SHA-256 of the YOLO11s ONNX export")
    parser.add_argument("--yolo11l", type=Path, help="Path to an approved YOLO11l ONNX export")
    parser.add_argument("--yolo11l-sha256", help="SHA-256 of the YOLO11l ONNX export")
    parser.add_argument("--provider", default="CPUExecutionProvider")
    parser.add_argument("--native-input", action="store_true", help="Keep the source resolution during video decode.")
    parser.add_argument("--detail-region", nargs=4, type=float, metavar=("LEFT", "TOP", "RIGHT", "BOTTOM"),
                        help="Run a second YOLO11 pass in a normalized rectangle.")
    parser.add_argument("--detail-objects", nargs="+", choices=("person", "vehicle"),
                        help="Choose which crop detections to merge with the full frame (default: person).")
    parser.add_argument("--detail-pose", type=Path, help="Use a separate YOLO11 pose ONNX export for the person crop.")
    parser.add_argument("--detail-pose-sha256", help="SHA-256 of the approved pose ONNX export.")
    parser.add_argument("--detail-pose-fallback", action="store_true", help="Run pose when ordinary detections leave a person zone without a qualifying box.")
    parser.add_argument("--fps", type=int, choices=range(1, SOURCE_FPS + 1), default=SAMPLE_FPS, metavar="1–30")
    parser.add_argument("--inference-fps", type=int, metavar="1–30", help="Set the baseline inference rate separately from decode.")
    parser.add_argument("--case", choices=CASES, default="g329", help="Labeled MEVA camera fixture")
    parser.add_argument("--g301-dwell", action="store_true", help="Also score a 15-second doorway dwell event in the G301 view.")
    parser.add_argument("--require-movement", action="store_true", help="Confirm observed movement before a vehicle stop.")
    parser.add_argument("--vehicle-stop-confirmation-boost", action="store_true", help="Double inference sampling while confirming a vehicle stop.")
    args = parser.parse_args()
    output = args.output.expanduser().resolve()
    if output.is_relative_to(ROOT):
        parser.error("Keep the downloaded data and generated results outside the checkout.")
    models = list(dict.fromkeys(args.models))
    yolo_exports = {"yolo11s": (args.yolo11, args.yolo11_sha256), "yolo11l": (args.yolo11l, args.yolo11l_sha256)}
    inference_fps = args.fps if args.inference_fps is None else args.inference_fps
    if not 1 <= inference_fps <= args.fps:
        parser.error("Set an inference rate from one fps up to the decode rate.")
    if args.vehicle_stop_confirmation_boost and (not args.require_movement or 2 * inference_fps > args.fps
                                               or any(name not in yolo_exports for name in models)):
        parser.error("Boost requires a YOLO11 movement-confirmation rule and a decode rate at least twice the inference rate.")
    if any(not all(yolo_exports[name]) for name in models if name in yolo_exports):
        parser.error("Supply the selected YOLO11 export and its SHA-256.")
    if args.detail_region and any(name not in yolo_exports for name in models):
        parser.error("A detail region requires YOLO11 models.")
    if args.detail_objects and not args.detail_region:
        parser.error("Choose a detail region before setting its object classes.")
    if args.detail_pose or args.detail_pose_sha256 or args.detail_pose_fallback:
        if (not args.detail_pose or not args.detail_pose_sha256 or not args.detail_region
                or args.detail_objects not in (None, ["person"])):
            parser.error("Set the pose export, SHA-256, and person detail region together.")
    if args.g301_dwell and args.case != "g301":
        parser.error("The doorway dwell rule requires --case g301.")
    camera = args.case
    clip, activity, first_frame, end_frame, rule, digests = CASES[camera]
    if args.require_movement:
        if rule["kind"] != "restricted-stop":
            parser.error("Movement confirmation applies to vehicle stop fixtures.")
        rule = {**rule, "requireMovement": True}
        if camera.startswith("g336-"):
            first_frame = 806  # Include the approach before the short stop and turn annotations.
            end_frame += SOURCE_FPS  # Keep the final labeled sample on the inference grid.
    if args.g301_dwell:
        rule = G301_DWELL_RULE
    if camera != "g329":
        output = output / camera
    assets = case_assets(camera, clip, digests)
    is_1080 = camera == "g301" or camera.startswith(("g299-", "g336-"))
    source_shape = (WIDTH, 1080 if is_1080 else HEIGHT)
    try:
        for name, (url, digest) in {**assets, **{f"yolox_{name}.onnx": MODELS[name] for name in models if name in MODELS}}.items():
            retrieve(output, name, url, digest)
        boxes = read_truth(output, camera, activity, rule["object"], source_shape)
        if args.g301_dwell:
            first_frame = min(boxes)
        expected, reference_point = (crossing_reference(boxes, rule["points"], first_frame, rule["direction"])
                                     if rule["kind"] == "line-crossing" else (None, None))
        zone_count = 1 if args.g301_dwell else ZONE_CASE_COUNTS.get(camera)
        zone_window = None
        if args.g301_dwell:
            zone_window = g301_dwell_window(boxes, first_frame)
        elif zone_count is not None:
            reference_start = (max(first_frame, min(boxes)) - first_frame) / SOURCE_FPS
            reference_end = (min(end_frame - 1, max(boxes)) - first_frame) / SOURCE_FPS
            zone_window = (reference_start + (rule["seconds"] if zone_count else 0), reference_end)
        segment_name = {"g328": "vehicle", "g301": "exit"}.get(camera, "entrance")
        segment = output / f"{camera}-{segment_name}.mkv"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(output / f"{camera}.avi"), "-map", "0:v:0", "-an", "-sn", "-dn",
                        "-vf", f"trim=start_frame={first_frame}:end_frame={end_frame},setpts=PTS-STARTPTS",
                        "-c:v", "ffv1", str(segment)], check=True)
        frame_map, width, height = sampled_frames(segment, args.fps)
        if (width, height) != source_shape:
            raise ValueError("Unexpected source image size for the MEVA geometry.")
        policy = output / "tripwire-policy.json"
        policy.write_text(json.dumps({"version": 1, "rules": [rule]}, indent=2) + "\n")
        decode_shape = source_shape if args.native_input else (640, round(320 * height / width) * 2)
        results = []
        for name in models:
            if name in yolo_exports:
                path, digest = yolo_exports[name]
                kind, path = "yolo11", path.expanduser().resolve(strict=True)
                model_name = "YOLO11" + name[-1]
            else:
                kind, path, digest = "yolox", output / f"yolox_{name}.onnx", MODELS[name][1]
                model_name = f"YOLOX-{name}"
            spec = {"kind": kind, "path": str(path), "sha256": digest, "provider": args.provider, "fps": inference_fps}
            if args.vehicle_stop_confirmation_boost:
                spec["vehicleStopConfirmationBoost"] = True
            if args.detail_region:
                spec["detailRegion"] = args.detail_region
            if args.detail_objects:
                spec["detailObjects"] = args.detail_objects
            if args.detail_pose:
                spec["detailPose"] = {"path": str(args.detail_pose.expanduser().resolve(strict=True)), "sha256": args.detail_pose_sha256}
            if args.detail_pose_fallback:
                spec["detailPoseFallback"] = True
            events, runtime = run_worker(output, segment, camera, name, policy, args.fps, decode_shape, spec)
            metrics = score_boxes(output / f"{name}-detections.jsonl", boxes, frame_map, args.fps, first_frame, rule["object"])
            if expected is not None:
                metrics.update(score(events, boxes, expected, frame_map, args.fps, first_frame, rule["id"],
                                     reference_point=reference_point, source_dimensions=source_shape))
            if zone_window is not None:
                metrics.update(score_zone_events(events, boxes, zone_window, frame_map, args.fps, first_frame, rule["id"], zone_count))
                if args.g301_dwell:
                    metrics["dwellTimeErrorSeconds"] = metrics["zoneTimeErrorSeconds"]
            results.append({"model": model_name, "sha256": digest, **runtime, **metrics})
    except (ImportError, OSError, ValueError, subprocess.CalledProcessError) as issue:
        parser.error(str(issue))
    report = {"clip": clip, "source": assets[f"{camera}.avi"][0], "license": "CC BY 4.0", "activity": activity,
              "sourceFrames": [first_frame, end_frame - 1], "annotatedFrames": [min(boxes), max(boxes)],
              "sampleFps": args.fps, "baselineInferenceFps": inference_fps, "provider": args.provider,
              "decodeSize": decode_shape, "detailRegion": args.detail_region,
              "detailObjects": (args.detail_objects or ["person"]) if args.detail_region else None,
              "detailPoseSha256": args.detail_pose_sha256,
              "detailPoseFallback": args.detail_pose_fallback,
              "expectedEvents": 1 if expected is not None else zone_count,
              "requireMovement": args.require_movement,
              "vehicleStopConfirmationBoost": args.vehicle_stop_confirmation_boost,
              "expectedCrossingSeconds": round(expected, 3) if expected is not None else None,
              "expectedCrossingPoint": reference_point,
              "expectedDwellWindowSeconds": [round(value, 3) for value in zone_window] if args.g301_dwell else None,
              "matchToleranceSeconds": TOLERANCE_SECONDS if expected is not None else None, "minimumBoxIou": MINIMUM_IOU,
              "negativeFootageReviewed": False, "results": results}
    encoded = json.dumps(report, indent=2) + "\n"
    (output / "meva-report.json").write_text(encoded)
    print(encoded, end="")


if __name__ == "__main__":
    main()
