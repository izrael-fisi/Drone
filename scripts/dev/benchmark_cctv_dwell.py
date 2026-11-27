"""Measure YOLO11 on labeled CAVIAR person boxes and time-in-zone fixtures."""

import argparse
from collections import defaultdict
from datetime import datetime, timezone
from itertools import groupby
import json
from pathlib import Path
import subprocess
import sys
from xml.etree import ElementTree as ET

from evaluate_cctv_replay import iou, sampled_frames
from prepare_cctv_analytics_demo import retrieve

ROOT = Path(__file__).resolve().parents[2]
SOURCE = "https://homepages.inf.ed.ac.uk/rbf/"
FPS = 4
CAVIAR_FPS = 25
START = datetime(2026, 1, 1, tzinfo=timezone.utc)
STOREFRONT = (.39, .49, .61, .77)
STOREFRONT_CASES = {"OneShopOneWait1front", "OneShopOneWait2front"}
FIXTURES = {
    "Browse_WhileWaiting1": (
        "CAVIARDATA1", "bww1gt.xml", "7193e2abde457ab12d3ebb56e22f330b0d138116f20194ba0fa7d1929812d1fc",
        "63e0be4b155714de18ee2f8866ce4e4df6d72102c41002dc054a3c2cb4b1176d", 0,
    ),
    "Browse_WhileWaiting2": (
        "CAVIARDATA1", "bww2gt.xml", "975324fe121725a0cf74e47acf111ec6d403e46107d5d2742df126a1f3567bd8",
        "13d80d8cba1f2ede1d3ab5f5803bde078d1ee03d1c2831f348d4b85d52082fd8", 1,
    ),
    "Walk1": (
        "CAVIARDATA1", "wk1gt.xml", "a5d6dc7d7def01c1bdaeea8ff620195c148b366e1a52589bf4e46f15b6daa0c2",
        "dfcb8135fe46e7fbbcb7616d93f4f16aee040570185227ecd8c81aaa87de06cd", 0,
    ),
    "Browse2": (
        "CAVIARDATA1", "br2gt.xml", "c1decc9a72a48133674227cf64ad79adbde5dbd9239c45f1e868056ada9a0851",
        "c5755b19aef64f35a08b79f6effb83c1bdcadf446f4a82344296cb201c7d9939", None,
    ),
    "OneShopOneWait2front": (
        "CAVIARDATA2", "fosow2gt.xml", "f3857f8d0b39b7677de26def9d7ef4669a3bd99836b3805b6d872907942cbe75",
        "3fe770546f34d1f0aa836eb77951e3e1877fb5eab23d12c2be5e8873dbb32173", None,
    ),
    "OneShopOneWait1front": (
        "CAVIARDATA2", "fosow1gt.xml", "834694ba2b44e7d3f746e6ffa99da93cc14b608ce3ae0a4101d9af01fb58c3b1",
        "42a8f9ca31c8b1966150fe6cbbc784213f68a8df780dd31765ebe3c12d38d8c5", None,
    ),
    "Rest_InChair": (
        "CAVIARDATA1", "ricgt.xml", "19b32edd0fe6ad0c0243f3c94adc787ffee28d8a74cb8ca73473fdfb996c423a",
        "15be1e83afe70162e6a2e14464918c5e267e3b12d9d6463eaa0f57a10d8be96f", None,
    ),
}


def labeled_tracks(annotation, width, height):
    frames = {}
    for frame in ET.parse(annotation).getroot().findall("./frame"):
        boxes = []
        for subject in frame.findall("./objectlist/object"):
            item = subject.find("box")
            if item is None:
                continue
            x, y, w, h = (int(item.get(key)) for key in ("xc", "yc", "w", "h"))
            bounds = ((x - w / 2) / width, (y - h / 2) / height, (x + w / 2) / width, (y + h / 2) / height)
            boxes.append((subject.get("id"), bounds))
        frames[int(frame.get("number"))] = tuple(boxes)
    return frames


def matching_boxes(detections, references):
    candidates = [(iou(box["box"], ref), i, j) for i, box in enumerate(detections) for j, ref in enumerate(references)]
    predicted, labeled = set(), set()
    for overlap, i, j in sorted(candidates, reverse=True):
        if overlap < .30:
            break
        if i not in predicted and j not in labeled:
            predicted.add(i)
            labeled.add(j)
    return len(labeled)


def storefront_visits(truth, seconds):
    left, top, right, bottom = STOREFRONT
    inside = defaultdict(list)
    for frame, subjects in truth.items():
        for actor, (a, _, c, d) in subjects:
            if left <= (a + c) / 2 <= right and top <= d <= bottom:
                inside[actor].append(frame)
    visits = []
    for actor, frames in inside.items():
        for _, run in groupby(enumerate(sorted(frames)), lambda row: row[1] - row[0]):
            indices = [frame for _, frame in run]
            first, last = indices[0], indices[-1]
            if last - first >= seconds * CAVIAR_FPS:
                visits.append({"actor": actor, "entryFrame": first, "eligibleFrame": first + seconds * CAVIAR_FPS, "exitFrame": last})
    return visits


def score_storefront(events, frames, truth, threshold):
    visits = storefront_visits(truth, threshold)
    hits = [[] for _ in visits]
    unmatched = []
    for event in events:
        seconds = (datetime.fromisoformat(event["occurredAt"]) - START).total_seconds()
        aligned = frames.get(round(seconds * FPS), ())
        box = event["analytics"]["box"]
        matches = [index for index, visit in enumerate(visits) if aligned and all(
            visit["eligibleFrame"] <= frame <= visit["exitFrame"] and any(
                subject == visit["actor"] and iou(bounds, box) >= .30 for subject, bounds in truth.get(frame, ()))
            for frame in aligned)]
        if len(matches) == 1:
            hits[matches[0]].append(seconds)
        else:
            unmatched.append(seconds)
    return {"visits": [{**visit, "matchingObservationSeconds": times} for visit, times in zip(visits, hits)],
            "matchedVisits": sum(bool(times) for times in hits), "duplicateObservations": sum(max(0, len(times) - 1) for times in hits),
            "unmatchedObservationSeconds": unmatched}


def run_case(directory, case, ground, gap, storefront):
    collection, annotation, video_digest, xml_digest, expected = FIXTURES[case]
    clip_name = f"{case}.mpg"
    for file, checksum in ((clip_name, video_digest), (annotation, xml_digest)):
        retrieve(directory, file, f"{SOURCE}{collection}/{case}/{file}", checksum)
    video = directory / clip_name
    frames, width, height = sampled_frames(video, FPS)
    truth = labeled_tracks(directory / annotation, width, height)
    threshold = 25 if storefront else 30
    left, top, right, bottom = STOREFRONT if storefront else (0, 0, 1, 1)
    worker = {"sensorId": "caviar-test", "width": width, "height": height, "fps": FPS,
              "models": {"ground": ground}}
    rule_name = "25 seconds in storefront zone" if storefront else "30 seconds in view"
    policy = {"version": 1, "rules": [{"id": "dwell", "name": rule_name, "kind": "zone-dwell", "object": "person", "mode": "observe",
                                       "boundaryId": "storefront" if storefront else "lobby", "points": [[left, top], [right, top], [right, bottom], [left, bottom]],
                                       "minimumScore": .25, "minimumPixels": 12, "seconds": threshold, "dwellGapSeconds": gap}]}
    config_file, policy_file = directory / "worker.json", directory / "policy.json"
    trace, out = directory / "detections.jsonl", directory / "events.jsonl"
    config_file.write_text(json.dumps(worker))
    policy_file.write_text(json.dumps(policy))
    run = subprocess.run([sys.executable, str(ROOT / "services/camera-gateway/analytics_worker.py"), "--config", str(config_file),
                          "--replay", str(video), "--policy", str(policy_file), "--start-at", START.isoformat(),
                          "--detections", str(trace)], capture_output=True, text=True, check=True)
    out.write_text(run.stdout)
    references = {}
    aligned, annotated, labeled_boxes, matched_boxes = [], 0, 0, 0
    with trace.open() as stream:
        for line in stream:
            row = json.loads(line)
            index = round(row["seconds"] * FPS)
            choices = {truth.get(source_index, ()) for source_index in frames[index]}
            boxes = [box for _, box in choices.pop()] if len(choices) == 1 else ()
            references[index] = boxes
            annotated += bool(boxes)
            labeled_boxes += len(boxes)
            matches = matching_boxes(row["detections"], boxes)
            matched_boxes += matches
            aligned.append(bool(matches))
    events = [json.loads(line) for line in run.stdout.splitlines()]
    event_matches = sum(
        any(iou(event["analytics"]["box"], box) >= .30 for box in references.get(
            round((datetime.fromisoformat(event["occurredAt"]) - START).total_seconds() * FPS), ()))
        for event in events
    )
    runtime = json.loads(run.stderr.splitlines()[-1])["ground"]
    storefront_scores = score_storefront(events, frames, truth, threshold) if storefront else None
    if storefront_scores is not None:
        expected = len(storefront_scores["visits"])
    return {"fixture": case, "analyzedFrames": len(aligned), "annotatedSampleFrames": annotated, "framesWithMatchedActor": sum(aligned),
            "labeledBoxes": labeled_boxes, "matchedBoxes": matched_boxes,
            "longestMatchingRunSeconds": max((sum(1 for _ in group) - 1 for matched, group in groupby(aligned) if matched), default=0) / FPS,
            "meanProcessingMs": runtime["meanProcessingMs"],
            "expectedEvents": expected, "observedEvents": len(events), "eventsWithActorBoxAgreement": event_matches,
            "observedDwellSeconds": [event["analytics"]["dwellSeconds"] for event in events],
            **({"storefrontDwell": storefront_scores} if storefront_scores is not None else {}),
            **({"poseFallbackFrames": runtime["poseFallbackFrames"]} if "poseFallbackFrames" in runtime else {})}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--yolo11", required=True, type=Path)
    parser.add_argument("--yolo11-sha256", required=True)
    parser.add_argument("--provider", default="CPUExecutionProvider")
    parser.add_argument("--case", action="append", choices=FIXTURES, help="Choose clips to replay. Repeat for multiple clips.")
    parser.add_argument("--detection-gap", type=float, default=.75, help="Maximum gap in seconds (0 to 1)")
    parser.add_argument("--storefront-dwell", action="store_true", help="Score 25-second visits to the labeled shopping-centre zone.")
    parser.add_argument("--detail-region", nargs=4, type=float, metavar=("LEFT", "TOP", "RIGHT", "BOTTOM"))
    parser.add_argument("--detail-pose", type=Path)
    parser.add_argument("--detail-pose-sha256")
    parser.add_argument("--detail-pose-fallback", action="store_true")
    parser.add_argument("--rotation-degrees", type=int, choices=(0, 90, 180, 270), default=0,
                        help="Rotate frames clockwise before model inference.")
    args = parser.parse_args()
    directory = args.output.expanduser().resolve()
    if directory.is_relative_to(ROOT):
        parser.error("Keep the downloaded clips and benchmark outputs outside the checkout.")
    if not 0 <= args.detection_gap <= 1:
        parser.error("Choose a detection gap from zero to one second.")
    defaults = ("OneShopOneWait2front", "OneShopOneWait1front") if args.storefront_dwell else ("Browse_WhileWaiting2", "Walk1")
    cases = dict.fromkeys(args.case or defaults)
    if args.storefront_dwell and cases.keys() - STOREFRONT_CASES:
        parser.error("Use the two front-view shopping-centre waiting fixtures for --storefront-dwell.")
    if (args.detail_pose or args.detail_pose_sha256 or args.detail_pose_fallback) and not (
            args.detail_pose and args.detail_pose_sha256 and args.detail_region):
        parser.error("Set the pose export, SHA-256, and detail region together.")
    try:
        model = args.yolo11.expanduser().resolve(strict=True)
        ground = {"kind": "yolo11", "path": str(model), "sha256": args.yolo11_sha256, "provider": args.provider,
                  "rotationDegrees": args.rotation_degrees}
        if args.detail_region:
            ground["detailRegion"] = args.detail_region
        if args.detail_pose:
            ground["detailPose"] = {"path": str(args.detail_pose.expanduser().resolve(strict=True)), "sha256": args.detail_pose_sha256}
        if args.detail_pose_fallback:
            ground["detailPoseFallback"] = True
        rows = [run_case(directory / case, case, ground, args.detection_gap, args.storefront_dwell) for case in cases]
    except subprocess.CalledProcessError as error:
        parser.error(error.stderr or str(error))
    except (OSError, ValueError, ET.ParseError) as error:
        parser.error(str(error))
    collections = dict.fromkeys(FIXTURES[case][0] for case in cases)
    report = {"source": f"{SOURCE}{next(iter(collections))}/", "attribution": "EC Funded CAVIAR project/IST 2001 37540",
              "sourceCollections": [f"{SOURCE}{collection}/" for collection in collections],
              "modelSha256": args.yolo11_sha256, "provider": args.provider, "sampleFps": FPS,
              "rotationDegrees": args.rotation_degrees,
              "thresholdSeconds": 25 if args.storefront_dwell else 30, "detectionGapSeconds": args.detection_gap,
              "detailRegion": args.detail_region, "detailPoseSha256": args.detail_pose_sha256,
              "detailPoseFallback": args.detail_pose_fallback, "negativeCameraHoursReviewed": False, "cases": rows}
    result = json.dumps(report, indent=2) + "\n"
    (directory / "dwell-report.json").write_text(result)
    print(result, end="")


if __name__ == "__main__":
    main()
