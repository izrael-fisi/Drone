"""Evaluate YOLO11 on annotated people and vehicles in the public MEVA evaluation set."""

import argparse
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from fractions import Fraction
from hashlib import sha256
import json
from pathlib import Path
import re
import subprocess
import sys
import tarfile
from tempfile import TemporaryFile

import yaml

from benchmark_cctv_meva import BUCKET, ROOT
from evaluate_cctv_replay import iou
from prepare_cctv_analytics_demo import retrieve

sys.path.insert(0, str(ROOT / "services/camera-gateway"))

from video_analytics.detectors import load_detector
from video_analytics.tracking import Analytics
from video_analytics.video import decoded, end


MANIFEST = ROOT / "tests/fixtures/cctv-meva-corpus.json"
ACTIVITIES = {
    "person_enters_scene_through_structure": "person",
    "person_exits_scene_through_structure": "person",
    "person_enters_vehicle": "person",
    "person_exits_vehicle": "person",
    "person_opens_facility_door": "person",
    "person_sits_down": "person",
    "person_stands_up": "person",
    "person_talks_to_person": "person",
    "vehicle_stops": "vehicle",
    "vehicle_starts": "vehicle",
    "vehicle_turns_left": "vehicle",
    "vehicle_turns_right": "vehicle",
    "vehicle_reverses": "vehicle",
    "vehicle_makes_u_turn": "vehicle",
}
ENTRANCES = {"person_enters_scene_through_structure", "person_exits_scene_through_structure"}
CASE_SAMPLES = 5
MINIMUM_CONFIDENCE = .25
MINIMUM_IOU = .50
TRIPWIRE_FPS = 4
LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


def annotations(archive_path, clips):
    result = defaultdict(dict)
    with tarfile.open(archive_path, "r|gz") as archive:
        for member in archive:
            parts = Path(member.name).name.rsplit(".", 2)
            if len(parts) == 3 and parts[0] in clips and member.isfile() and parts[2] == "yml":
                with archive.extractfile(member) as stream:
                    result[parts[0]][parts[1]] = stream.read()
    for clip in clips:
        if not {"activities", "geom", "types"} <= result[clip].keys():
            raise ValueError(f"Incomplete evaluation annotations for {clip}")
    return result


def label_rows(raw, key):
    return (row[key] for row in yaml.load(raw, Loader=LOADER) or [] if key in row)


def video_shape(path):
    fields = "stream=width,height,avg_frame_rate,r_frame_rate,nb_frames"
    result = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", fields,
                             "-of", "json", str(path)], check=True, capture_output=True, text=True)
    stream, = json.loads(result.stdout)["streams"]
    rate = Fraction(stream["avg_frame_rate"])
    if rate <= 0 or rate != Fraction(stream["r_frame_rate"]):
        raise ValueError(f"Expected a constant source frame rate in {path.name}")
    return (stream["width"], stream["height"]), rate, int(stream["nb_frames"])


def reference_tracks(geometry, actor_ids, shape, frame_count):
    tracks = defaultdict(dict)
    for row in label_rows(geometry, "geom"):
        actor, frame = row["id1"], row["ts0"]
        if actor not in actor_ids or not 0 <= frame < frame_count:
            continue
        box = tuple(min(1., max(0., float(value) / size)) for value, size in zip(row["g0"].split(), shape * 2))
        if len(box) != 4 or box[0] >= box[2] or box[1] >= box[3]:
            continue
        tracks[actor][frame] = box
    return tracks


def choose_cases(clip, raw, shape, frame_count, limit):
    classes = {row["id1"]: names.pop() for row in label_rows(raw["types"], "types")
               if len(names := set(row["cset3"]).intersection(("person", "vehicle"))) == 1}
    tracks = reference_tracks(raw["geom"], classes, shape, frame_count)
    candidates = defaultdict(list)
    for activity in label_rows(raw["activities"], "act"):
        names = set(activity.get("act2", {})).intersection(ACTIVITIES)
        if len(names) != 1:
            continue
        name = names.pop()
        target = ACTIVITIES[name]
        eligible = []
        for actor in activity["actors"]:
            actor_id = actor["id1"]
            if classes.get(actor_id) != target:
                continue
            spans = [span["tsr0"] for span in actor["timespan"]]
            frames = sorted(frame for frame in tracks[actor_id] if any(lo <= frame <= hi for lo, hi in spans))
            if len(frames) >= CASE_SAMPLES:
                eligible.append((len(frames), actor_id, frames))
        if eligible:
            _, actor_id, frames = max(eligible)
            sampled = [frames[round(i * (len(frames) - 1) / (CASE_SAMPLES - 1))] for i in range(CASE_SAMPLES)]
            case_id = f"{clip}/{activity['id2']}/{actor_id}"
            candidates[name].append({"id": case_id, "activity": name, "actor": actor_id, "object": target,
                                     "frames": sampled, "activityFrames": frames})
    # Interleave activities and keep the ordering independent of model output.
    queues = [deque(sorted(group, key=lambda case: sha256(case["id"].encode()).digest()))
              for _, group in sorted(candidates.items())]
    chosen, assigned = [], defaultdict(set)
    while queues and len(chosen) < limit:
        remaining = []
        for queue in queues:
            case = queue.popleft()
            frames = set(case["activityFrames"])
            if not frames.intersection(assigned[case["actor"]]):
                chosen.append(case)
                assigned[case["actor"]].update(frames)
            if len(chosen) == limit:
                break
            if queue:
                remaining.append(queue)
        queues = remaining
    return chosen, tracks


def tripwire_fixture(case, track, source_rate, shape):
    if case["activity"] not in ENTRANCES:
        return None
    first, last = case["activityFrames"][0], case["activityFrames"][-1]
    frames = [first + round(index * source_rate / TRIPWIRE_FPS)
              for index in range(1 + int((last - first) * TRIPWIRE_FPS / source_rate))]
    if len(frames) < 5 or len(frames) > 48 or not all(frame in track for frame in frames):
        return None
    width, height = shape
    if any(max((track[frame][2] - track[frame][0]) * width, (track[frame][3] - track[frame][1]) * height) < 12
           for frame in frames):
        return None
    anchors = [((track[frame][0] + track[frame][2]) / 2, track[frame][3]) for frame in frames]
    axis = max(range(2), key=lambda dim: abs(anchors[-1][dim] - anchors[0][dim]))
    start, finish = anchors[0][axis], anchors[-1][axis]
    line = (start + finish) / 2
    direction = 1 if finish > start else -1
    sides = [(anchor[axis] - line) * direction for anchor in anchors]
    outside = [(index, side > 0) for index, side in enumerate(sides) if abs(side) >= .012]
    before = [index for index, after in outside if not after]
    after = [index for index, after in outside if after]
    if (not before or not after or max(before) >= min(after)
            or (frames[after[0]] - frames[before[0]]) / source_rate < Fraction(1, 2)):
        return None
    points = [[line, 0.], [line, 1.]] if axis == 0 else [[0., line], [1., line]]
    forward = "positive" if direction == (1 if axis == 1 else -1) else "negative"
    reverse = "negative" if forward == "positive" else "positive"
    rules = [{"id": name, "name": name, "direction": heading, "kind": "line-crossing", "object": "person",
              "boundaryId": "evaluation", "mode": "observe", "points": points,
              "minimumScore": MINIMUM_CONFIDENCE, "minimumPixels": 12, "seconds": .5}
             for name, heading in (("forward", forward), ("reverse", reverse))]
    return frames, {"version": 1, "rules": rules}


def frame_selector(frame_ids):
    if len(frame_ids) == 1:
        return f"eq(n,{frame_ids[0]})"
    middle = len(frame_ids) // 2
    return f"if(lt(n,{frame_ids[middle]}),{frame_selector(frame_ids[:middle])},{frame_selector(frame_ids[middle:])})"


def infer_frames(video, frame_ids, shape, detector):
    width, height = shape
    indices = frame_selector(frame_ids)
    command = ["ffmpeg", "-nostdin", "-v", "error", "-filter_threads", "1", "-threads", "2", "-i", str(video),
               "-map", "0:v:0", "-an", "-sn", "-dn", "-vf", f"select='{indices}',scale={width}:{height}",
               "-fps_mode", "passthrough", "-pix_fmt", "rgb24", "-threads", "1", "-f", "rawvideo", "pipe:1"]
    with TemporaryFile() as errors:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=errors)
        try:
            boxes = {}
            for sample, frame in enumerate(decoded(process, width, height)):
                if sample >= len(frame_ids):
                    raise ValueError(f"Decoded unexpected frames from {video.name}")
                boxes[frame_ids[sample]] = detector.infer(frame, MINIMUM_CONFIDENCE)
            if process.wait():
                errors.seek(0)
                raise ValueError(f"Unable to decode {video.name}: {errors.read().decode(errors='replace').strip()}")
            if len(boxes) != len(frame_ids):
                raise ValueError(f"Decoded {len(boxes)} of {len(frame_ids)} selected frames from {video.name}")
        finally:
            end(process)
    return boxes


def score_tripwire(fixture, track, boxes, shape, source_rate):
    frames, policy = fixture
    engine = Analytics(1.2)
    hits = defaultdict(list)
    other = 0
    for frame in frames:
        stamp = float((frame - frames[0]) / source_rate)
        for rule, evidence in engine.sample(boxes[frame], policy, stamp, shape):
            if iou(track[frame], evidence["box"]) >= .30:
                hits[rule["id"]].append(frame)
            else:
                other += 1
    return {"matched": bool(hits["forward"]), "duplicates": max(0, len(hits["forward"]) - 1),
            "reverseReports": len(hits["reverse"]), "otherReports": other,
            "sourceFrames": frames, "points": policy["rules"][0]["points"],
            "forwardDirection": policy["rules"][0]["direction"], "matchedFrames": hits["forward"]}


def match_boxes(references, detections):
    """Assign each detection to at most one labeled actor sample."""
    options = {}
    for case_id, label, reference in references:
        ranked = sorted(((iou(reference, item.box), index) for index, item in enumerate(detections)
                         if item.label == label), reverse=True)
        options[case_id] = [index for overlap, index in ranked if overlap >= MINIMUM_IOU]
    assigned = {}

    def assign(case_id, visited):
        for index in options[case_id]:
            if index in visited:
                continue
            visited.add(index)
            if index not in assigned or assign(assigned[index], visited):
                assigned[index] = case_id
                return True
        return False

    for case_id in sorted(options, key=lambda key: (len(options[key]), key)):
        assign(case_id, set())
    return set(assigned.values())


def benchmark(video, clip, raw, width, limit, detector):
    source_shape, rate, frame_count = video_shape(video)
    shape = (width, max(2, 2 * round(source_shape[1] * width / source_shape[0] / 2)))
    cases, tracks = choose_cases(clip, raw, source_shape, frame_count, limit)
    if not cases:
        raise ValueError(f"No eligible boxed activities in {clip}")
    if len({case["id"] for case in cases}) != len(cases):
        raise ValueError(f"Duplicate actor activity IDs in {clip}")
    tripwires = {case["id"]: fixture for case in cases
                 if (fixture := tripwire_fixture(case, tracks[case["actor"]], rate, shape)) is not None}
    frames = {frame for case in cases for frame in case["frames"]}
    frames.update(frame for window, _ in tripwires.values() for frame in window)
    detections = infer_frames(video, sorted(frames), shape, detector)
    references = defaultdict(list)
    for case in cases:
        for frame in case["frames"]:
            references[frame].append((case["id"], case["object"], tracks[case["actor"]][frame]))
    matched = {frame: match_boxes(expected, detections[frame]) for frame, expected in references.items()}
    for case in cases:
        track = tracks[case["actor"]]
        case["samples"] = [{"frame": frame, "referenceBox": track[frame], "matched": case["id"] in matched[frame]}
                           for frame in case.pop("frames")]
        case.pop("activityFrames")
        if case["id"] in tripwires:
            case["tripwire"] = score_tripwire(tripwires[case["id"]], track, detections, shape, rate)
    return {"clip": clip, "sourceSize": source_shape, "sourceFps": str(rate), "decodeSize": shape,
            "inferenceFrames": len(frames), "cases": cases}


def summarize(cases):
    tripwires = [case["tripwire"] for case in cases if "tripwire" in case]
    samples = [sum(sample["matched"] for sample in case["samples"]) for case in cases]
    return {"cases": len(cases), "boxes": CASE_SAMPLES * len(cases), "matchedBoxes": sum(samples),
            "casesWithMajorityMatched": sum(n > CASE_SAMPLES / 2 for n in samples),
            "casesWithoutMatches": sum(n == 0 for n in samples), "tripwireCases": len(tripwires),
            "matchedTripwires": sum(x["matched"] for x in tripwires),
            "duplicateTripwireReports": sum(x["duplicates"] for x in tripwires),
            "reverseReports": sum(x["reverseReports"] for x in tripwires),
            "otherTripwireReports": sum(x["otherReports"] for x in tripwires)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, help="External data cache and report directory")
    parser.add_argument("--model", required=True, type=Path)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--provider", default="CPUExecutionProvider")
    parser.add_argument("--clip", action="append", help="Score only this MEVA clip ID; repeat to select more")
    parser.add_argument("--width", type=int, default=960)
    args = parser.parse_args()
    directory = args.directory.resolve()
    if directory.is_relative_to(ROOT) or not re.fullmatch(r"[a-f0-9]{64}", args.sha256) or not 320 <= args.width <= 1920:
        parser.error("Use an external cache, a pinned model digest, and a decode width from 320 to 1920.")
    manifest = json.loads(MANIFEST.read_text())
    assets = {Path(asset["key"]).name.removesuffix(".r13.avi"): asset for asset in manifest["videos"]}
    clips = args.clip or list(assets)
    if set(clips) - assets.keys() or len(set(clips)) != len(clips):
        parser.error("Select unique clip IDs from the MEVA corpus manifest.")
    try:
        detector = load_detector({"kind": "yolo11", "path": str(args.model.resolve(strict=True)),
                                  "sha256": args.sha256, "provider": args.provider})
        archive = manifest["annotations"]
        retrieve(directory, "meva-eval.tar.gz", archive["url"], archive["sha256"])
        labels = annotations(directory / "meva-eval.tar.gz", set(clips))
        media = directory / "media"

        def fetch(clip):
            asset = assets[clip]
            filename = Path(asset["key"]).name
            retrieve(media, filename, BUCKET + asset["key"], asset["sha256"])
            return media / filename

        results = []
        with ThreadPoolExecutor(max_workers=4) as downloads:
            futures = {clip: downloads.submit(fetch, clip) for clip in clips}
            for clip in clips:
                entry = benchmark(futures[clip].result(), clip, labels.pop(clip), args.width,
                                  manifest["casesPerRecording"], detector)
                results.append(entry)
                print(f"{clip}: scored {len(entry['cases'])} cases", file=sys.stderr, flush=True)
        all_cases = [case for entry in results for case in entry["cases"]]
        by_activity = defaultdict(list)
        for case in all_cases:
            by_activity[case["activity"]].append(case)
        report = {"annotationSha256": archive["sha256"], "modelSha256": args.sha256, "provider": args.provider,
                  "minimumConfidence": MINIMUM_CONFIDENCE, "minimumBoxIou": MINIMUM_IOU,
                  "sourceRecordings": len(results), "cameraViews": len({clip.split(".")[-1] for clip in clips}),
                  "inferenceFrames": sum(entry["inferenceFrames"] for entry in results),
                  "summary": summarize(all_cases), "byActivity": {name: summarize(group)
                      for name, group in sorted(by_activity.items())}, "recordings": results}
        destination = directory / "meva-corpus-report.json"
        temporary = destination.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(report, indent=2) + "\n")
        temporary.replace(destination)
        print(json.dumps({**report["summary"], "report": str(destination)}, indent=2))
    except (OSError, ValueError, KeyError, RuntimeError, tarfile.TarError, subprocess.CalledProcessError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
