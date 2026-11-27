"""Corroborate anonymous motion between observed body boxes across an occlusion."""

import argparse
from dataclasses import dataclass
from itertools import permutations
import json
import math
from pathlib import Path
from statistics import median
import sys

from prepare_cctv_analytics_demo import checksum

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services/camera-gateway"))

from site_geometry import cross
from video_analytics.tracking import overlap
from video_analytics.video import replay


@dataclass
class MotionTrack:
    points: object
    center: object
    motion: object


def inside(box, region):
    x, y = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    return region[0] <= x <= region[2] and region[1] <= y <= region[3]


def body_samples(path, reference):
    width, height = reference["frameSize"]
    samples = {}
    with path.open() as lines:
        for line in lines:
            row = json.loads(line)
            if row["lane"] != "ground":
                continue
            if row["frame"] != reference["frameSize"] or row["model"] != reference["bodyModel"]:
                raise ValueError("Use a trace with the reviewed body model and frame dimensions.")
            stamp = row["seconds"]
            if stamp in samples:
                raise ValueError("Use a trace with one ground sample per frame.")
            samples[stamp] = [
                tuple(value * size for value, size in zip(item["box"], (width, height, width, height)))
                for item in row["detections"]
                if item["label"] == "person" and item["score"] >= reference["minimumScore"]
            ]
    return samples


def seed_motion(image, now, before, roi, elapsed_frames):
    import cv2
    import numpy as np

    left, top = roi[:2]
    x1, y1, x2, y2 = now
    height = y2 - y1
    center = np.array([(x1 + x2) / 2 - left, y1 + .10 * height - top], dtype=np.float32)
    half_width = max(5, round((x2 - x1) * .27))
    mask = np.zeros(image.shape, dtype=np.uint8)
    start, end = max(0, round(center[0]) - half_width), round(center[0]) + half_width
    mask[max(0, round(y1 - top) - 1):round(y1 + .22 * height - top), start:end] = 255
    points = cv2.goodFeaturesToTrack(image, maxCorners=16, qualityLevel=.015, minDistance=2, mask=mask, blockSize=3)
    if points is None or len(points) < 3:
        return None
    motion = np.array([(x1 + x2 - before[0] - before[2]) / 2, y1 - before[1]], dtype=np.float32) / elapsed_frames
    return MotionTrack(points, center, motion)


def advance(previous, current, track, fps):
    import cv2
    import numpy as np

    if track is None:
        return None
    points = track.points
    window = {"winSize": (15, 15), "maxLevel": 2, "flags": cv2.OPTFLOW_USE_INITIAL_FLOW}
    updated, valid, _ = cv2.calcOpticalFlowPyrLK(previous, current, points, points + track.motion, **window)
    reverse, verified, _ = cv2.calcOpticalFlowPyrLK(current, previous, updated, points.copy(), **window)
    forwarded = (updated - points).reshape(-1, 2)
    accepted = valid.ravel().astype(bool) & verified.ravel().astype(bool)
    accepted &= np.linalg.norm((reverse - points).reshape(-1, 2), axis=1) <= 1.2
    accepted &= np.linalg.norm(forwarded - track.motion, axis=1) <= max(3.5, 26 / fps)
    if accepted.sum() < 3:
        return None
    motion = np.median(forwarded[accepted], axis=0)
    accepted &= np.linalg.norm(forwarded - motion, axis=1) <= 2.8
    if accepted.sum() < 3:
        return None
    return MotionTrack(updated[accepted], track.center + motion, .4 * track.motion + .6 * motion)


def opposite_anchors(source, target, line, direction):
    before = ((source[0] + source[2]) / 2, source[3])
    after = ((target[0] + target[2]) / 2, target[3])
    return crossing_point(before, after, line, direction)


def crossing_point(before, after, line, direction):
    a, b = line
    first, last = cross(a, b, before), cross(a, b, after)
    if first * last >= 0 or direction != ("positive" if last > 0 else "negative"):
        return None
    fraction = first / (first - last)
    crossing = tuple(x + (y - x) * fraction for x, y in zip(before, after))
    if all(min(x, y) - 1e-7 <= value <= max(x, y) + 1e-7 for x, y, value in zip(a, b, crossing)):
        return crossing
    return None


def confirm_pair(source, predicted, observed, line, direction):
    # Flow proposes associations. Only observed body boxes supply crossing endpoints.
    candidates = [(tuple(overlap(x, y) for x, y in zip(predicted, pair)), pair)
                  for pair in permutations(observed, 2)]
    if not candidates:
        return None
    candidates.sort(key=lambda item: sum(item[0]), reverse=True)
    ious, pair = candidates[0]
    if min(ious) < .35 or sum(ious) - sum(candidates[1][0]) < .18:
        return None
    crossings = [opposite_anchors(before, after, line, direction) for before, after in zip(source, pair)]
    if any(point is None for point in crossings):
        return None
    return {"bodyBoxes": pair, "pairIoU": [float(iou) for iou in ious], "crossingPoints": crossings}


def evaluate(reference, video, trace):
    import cv2

    samples = body_samples(trace, reference)
    fps = reference["sampleFps"]
    width, height = reference["frameSize"]
    left, top, right, bottom = reference["motionRegion"]
    previous_at, seed_at = reference["bodySeedSeconds"]
    destinations = set(reference["destinationSeconds"])
    head_labels = {float(stamp): points for stamp, points in reference["headCenters"].items()}
    if not destinations or not destinations | {previous_at, seed_at} <= samples.keys():
        raise ValueError("The trace must include the reviewed seed and destination samples.")
    start_region = reference["bodySeedRegion"]
    source, before = (sorted((box for box in samples[stamp] if inside(box, start_region)), key=lambda box: box[1])
                      for stamp in (seed_at, previous_at))
    if len(source) != 2 or len(before) != 2:
        raise ValueError("The two seed samples must each contain two observed body boxes.")
    last_needed = max(destinations | head_labels.keys())
    previous, tracks, initial = None, [], []
    confirmations, errors, coverage = [], [], 0
    current_confirmation = None
    confirmed_at = None
    for stamp, image in replay(str(video), width, height, fps):
        if stamp < seed_at:
            continue
        if stamp > last_needed:
            break
        frame = cv2.cvtColor(image[top:bottom, left:right], cv2.COLOR_RGB2GRAY)
        if previous is None:
            tracks = [seed_motion(frame, now, earlier, reference["motionRegion"], (seed_at - previous_at) * fps)
                      for now, earlier in zip(source, before)]
            initial = [track.center if track is not None else None for track in tracks]
        else:
            tracks = [advance(previous, frame, track, fps) for track in tracks]
        previous = frame
        if stamp in head_labels:
            coverage += all(tracks)
            for track, head in zip(tracks, head_labels[stamp]):
                if track is not None:
                    errors.append(math.dist((track.center[0] + left, track.center[1] + top), head))
        if stamp in destinations:
            evidence = None
            if all(tracks):
                predicted = []
                for box, start, track in zip(source, initial, tracks):
                    dx, dy = track.center - start
                    predicted.append((box[0] + dx, box[1] + dy, box[2] + dx, box[3] + dy))
                observed = [box for box in samples.get(stamp, ()) if inside(box, reference["destinationRegion"])]
                evidence = confirm_pair(source, predicted, observed, reference["line"], reference["direction"])
            if evidence is not None:
                confirmations.append({"seconds": stamp, **evidence})
                if current_confirmation is not None and math.isclose(stamp - current_confirmation, 1 / fps) and confirmed_at is None:
                    confirmed_at = stamp
                current_confirmation = stamp
            else:
                current_confirmation = None
    if previous is None or stamp < last_needed:
        raise ValueError("The recording ended before the reviewed destination samples.")
    return {"source": reference["source"], "bodyModel": reference["bodyModel"], "seedSeconds": seed_at,
            "headReviewFrames": len(head_labels), "framesWithBothPaths": coverage,
            "headCentersWithinNinePixels": sum(error <= 9 for error in errors),
            "medianHeadCenterErrorPixels": round(median(errors), 2) if errors else None,
            "confirmedAtSeconds": confirmed_at, "oppositeSideBodyPairs": confirmations}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reference", type=Path)
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--body-trace", type=Path, required=True)
    args = parser.parse_args()
    try:
        reference = json.loads(args.reference.read_text())
        if checksum(args.video) != reference["sourceSha256"]:
            raise ValueError("The recording SHA-256 does not match the reviewed source.")
        report = evaluate(reference, args.video, args.body_trace)
    except (ImportError, OSError, RuntimeError, ValueError, KeyError) as error:
        parser.error(str(error))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
