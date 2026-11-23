"""Compare drone replay traces at common frames against Halmstad labels."""

import argparse
from collections import defaultdict
import json
from pathlib import Path
import re
import subprocess

FRAME = re.compile(r"\[Parsed_showinfo_([02]) @ [^\]]+\].*?n:\s*(\d+).*?pts_time:\s*([\d.e+-]+).*?checksum:([A-F0-9]+)")
SIZE = re.compile(r"\bs:(\d+)x(\d+)\b")
SIZE_BANDS = ("under 12 px", "12–23 px", "24–47 px", "48+ px")


def read_trace(path, fps):
    rows = {}
    with path.open() as stream:
        for line in stream:
            row = json.loads(line)
            if row.get("lane") != "drone":
                continue
            index = round(row["seconds"] * fps)
            if row.get("decodeFps", fps) != fps or index in rows or abs(index - row["seconds"] * fps) > 1e-5:
                raise ValueError(f"{path.name} does not match the declared decode rate.")
            rows[index] = row
    if not rows:
        raise ValueError(f"{path.name} contains no drone frames.")
    return rows


def sampled_frames(video, fps):
    process = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(video), "-map", "0:v:0", "-vf", f"showinfo,fps={fps},showinfo",
                              "-an", "-f", "null", "-"], check=True, capture_output=True, text=True)
    source, output = defaultdict(list), {}
    for line in process.stderr.splitlines():
        match = FRAME.search(line)
        if not match:
            continue
        tag, index, _, checksum = match.groups()
        if tag == "0":
            source[checksum].append(int(index))
            width, height = map(int, SIZE.search(line).groups())
        else:
            output[int(index)] = checksum
    if not source or not output:
        raise ValueError("FFmpeg did not report source and sampled frames.")
    return {index: source[checksum] for index, checksum in output.items()}, width, height


def iou(a, b):
    shared = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))
    if shared == 0:
        return 0.0
    area_a = (a[2] - a[0]) * (a[3] - a[1])
    area_b = (b[2] - b[0]) * (b[3] - b[1])
    return shared / (area_a + area_b - shared)


def evaluate(rows, labels, size_band, scores, overlaps):
    report = []
    for threshold in scores:
        for minimum_iou in overlaps:
            correct = missed = unmatched = negative_candidates = 0
            matched_by_size = dict.fromkeys(SIZE_BANDS, 0)
            for index, label in labels.items():
                boxes = [item["box"] for item in rows[index]["detections"]
                         if item["label"] == "drone" and item["score"] >= threshold]
                matched = label is not None and any(iou(label, box) >= minimum_iou for box in boxes)
                if matched:
                    matched_by_size[size_band[index]] += 1
                correct += matched
                missed += label is not None and not matched
                unmatched += len(boxes) - matched
                negative_candidates += bool(boxes) and label is None
            report.append({"score": threshold, "iou": minimum_iou, "matchedFrames": correct, "missedFrames": missed,
                           "unmatchedBoxes": unmatched, "negativeFramesWithCandidates": negative_candidates, "matchedBySize": matched_by_size})
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", type=Path, required=True)
    truth = parser.add_mutually_exclusive_group(required=True)
    truth.add_argument("--halmstad-labels", type=Path)
    truth.add_argument("--negative-clip", action="store_true", help="The supplied clip contains no target drone.")
    parser.add_argument("--fps", type=int, required=True, help="Decode rate used for the worker traces.")
    parser.add_argument("--captured-at", type=float, required=True, help="Lowest confidence retained in the input traces.")
    parser.add_argument("--score", type=float, nargs="+", default=[.15, .35, .55, .75])
    parser.add_argument("--iou", type=float, nargs="+", default=[.3, .5])
    parser.add_argument("traces", type=Path, nargs="+")
    args = parser.parse_args()
    if (not 1 <= args.fps <= 30 or not 0 <= args.captured_at <= 1 or any(not args.captured_at <= s <= 1 for s in args.score)
            or any(not 0 < item <= 1 for item in args.iou)):
        parser.error("Choose a valid decode rate and thresholds at or above the trace capture floor.")
    traces = [read_trace(path, args.fps) for path in args.traces]
    common = set.intersection(*(set(rows) for rows in traces))
    if not common:
        parser.error("The traces do not share any sampled frames.")
    if any(rows[index].get("captureFloor", args.captured_at) > min(args.score) for rows in traces for index in common):
        parser.error("A score threshold is below the confidence floor recorded in a trace.")
    mapped, width, height = sampled_frames(args.video, args.fps)
    if not common <= mapped.keys():
        parser.error("The sampled frame indexes do not match the supplied video.")
    labels = dict.fromkeys(sorted(common))
    size_band = {}
    skipped = 0
    if args.halmstad_labels:
        from mcos_decoder import load_groundtruth

        truth = load_groundtruth(args.halmstad_labels)
        for index in sorted(common):
            candidates = mapped[index]
            if any(frame >= len(truth) for frame in candidates):
                parser.error("The annotations do not cover the video frames.")
            possible = {truth[frame] for frame in candidates}
            if len(possible) != 1:
                del labels[index]
                skipped += 1
                continue
            box = possible.pop()
            if box is not None:
                x, y, w, h = box
                labels[index] = (x / width, y / height, (x + w) / width, (y + h) / height)
                size = max(w, h)
                size_band[index] = SIZE_BANDS[sum(size >= edge for edge in (12, 24, 48))]
    if not labels:
        parser.error("No sampled frames had an unambiguous label.")
    results = []
    for path, rows in zip(args.traces, traces):
        times = [rows[index].get("processingMs") for index in labels]
        result = {"trace": str(path), "metrics": evaluate(rows, labels, size_band, args.score, args.iou)}
        if all(isinstance(elapsed, (int, float)) and elapsed >= 0 for elapsed in times):
            result["meanProcessingMs"] = round(sum(times) / len(times), 1)
        results.append(result)
    print(json.dumps({"frames": len(labels), "positiveFrames": sum(box is not None for box in labels.values()),
                      "positiveBySize": {band: sum(value == band for value in size_band.values()) for band in SIZE_BANDS},
                      "ambiguousFramesExcluded": skipped, "results": results}, indent=2))


if __name__ == "__main__":
    main()
