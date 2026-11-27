"""Score complete CCTV replays against reviewed event and empty intervals."""

import argparse
from datetime import datetime
from hashlib import file_digest
import json
import math
from pathlib import Path


def json_lines(path):
    with path.open() as stream:
        for number, line in enumerate(stream, 1):
            try:
                yield json.loads(line)
            except ValueError as error:
                raise ValueError(f"{path.name}:{number}: invalid JSON") from error


def timestamp(value):
    parsed = datetime.fromisoformat(value)
    if parsed.utcoffset() is None:
        raise ValueError("Replay timestamps must include a time zone.")
    return parsed


def intervals(rule, duration):
    events, empty = rule["events"], rule["reviewedEmpty"]
    windows = [(start, end, event) for event, ranges in events.items() for start, end in ranges]
    windows.extend((start, end, None) for start, end in empty)
    windows.sort(key=lambda row: row[:2])
    previous_end = 0
    for start, end, _ in windows:
        if not previous_end <= start < end <= duration:
            raise ValueError("Review intervals must be within the clip and cannot overlap for the same rule.")
        previous_end = end
    if any(not ranges for ranges in events.values()):
        raise ValueError("Each reference event needs at least one time interval.")
    return windows


def check_trace(trace, reference):
    rate, duration = reference["sampleFps"], reference["durationSeconds"]
    if not math.isfinite(rate) or rate <= 0 or not math.isfinite(duration) or duration <= 0:
        raise ValueError("Reference duration and sample rate must be positive and finite.")
    sample_models, fingerprint = [], None
    for row in json_lines(trace):
        if row["lane"] != reference["lane"]:
            continue
        count = len(sample_models)
        if not math.isclose(row["seconds"], count / rate, rel_tol=0, abs_tol=1e-6):
            raise ValueError(f"The trace is missing an inference sample at {count / rate:.3f} seconds.")
        profile = row.get("configuredModel", row["model"])
        if fingerprint is None:
            fingerprint = profile
        if profile != fingerprint:
            raise ValueError("Use one configured model profile per replay and lane.")
        sample_models.append(row["model"])
    if len(sample_models) < math.ceil(duration * rate - 1e-6) or not fingerprint:
        raise ValueError("The detection trace does not cover the reference duration.")
    return sample_models, fingerprint


def score_replay(reference, trace, event_file):
    duration = reference["durationSeconds"]
    started = timestamp(reference["replayStart"])
    sample_models, fingerprint = check_trace(trace, reference)
    windows = {name: intervals(rule, duration) for name, rule in reference["rules"].items()}
    group_counts = {}
    group_tracks = {}
    for name, rule in reference["rules"].items():
        if "expectedCounts" not in rule:
            continue
        expected = rule["expectedCounts"]
        if (not isinstance(expected, dict) or set(expected) != set(rule["events"])
                or any(type(value) is not int or value < 1 for value in expected.values())):
            raise ValueError("Expected counts must give a positive integer for every reviewed event group.")
        group_counts[name] = expected
        group_tracks[name] = {label: set() for label in expected}
    results = {name: {"observationsByEvent": {event: [] for event in rule["events"]},
                      "reviewedEmptySeconds": sum(end - start for start, end in rule["reviewedEmpty"]),
                      "falseInReviewedEmpty": [], "unreviewed": []}
               for name, rule in reference["rules"].items()}
    other_rules = 0
    ids = set()
    for event in json_lines(event_file):
        analytics = event["analytics"]
        name = analytics["ruleId"]
        if name not in results:
            other_rules += 1
            continue
        if (event["id"] in ids or event["sensorId"] != reference["sensorId"] or analytics["source"] != "replay"):
            raise ValueError("Observations must be unique and belong to the scored replay and sensor.")
        ids.add(event["id"])
        seconds = (timestamp(event["occurredAt"]) - started).total_seconds()
        sample = round(seconds * reference["sampleFps"])
        if not 0 <= sample < len(sample_models) or not math.isclose(seconds, sample / reference["sampleFps"], rel_tol=0, abs_tol=1e-6):
            raise ValueError("An observation timestamp does not align with the detection trace.")
        if analytics["model"] != sample_models[sample]:
            raise ValueError("An observation model does not match its inference sample.")
        result = results[name]
        matches = result["unreviewed"]
        for start, end, label in windows[name]:
            if start <= seconds < end:
                matches = result["falseInReviewedEmpty"] if label is None else result["observationsByEvent"][label]
                if label is not None and name in group_tracks:
                    track = analytics.get("trackId")
                    if not isinstance(track, str) or not track:
                        raise ValueError("Counting a group requires a track ID for each matched observation.")
                    group_tracks[name][label].add(track)
                break
        matches.append(seconds)
    for name, result in results.items():
        episodes = result["observationsByEvent"]
        result["matched"] = sum(bool(samples) for samples in episodes.values())
        result["missed"] = [label for label, samples in episodes.items() if not samples]
        if name in group_counts:
            expected = group_counts[name]
            counts = {label: min(target, len(group_tracks[name][label])) for label, target in expected.items()}
            result["expectedObservations"] = sum(expected.values())
            result["countedObservations"] = sum(counts.values())
            result["countShortfallByEvent"] = {label: target - counts[label] for label, target in expected.items()}
        else:
            counts = {label: int(bool(samples)) for label, samples in episodes.items()}
        result["duplicates"] = sum(len(samples) - counts[label] for label, samples in episodes.items())
        empty_seconds = result["reviewedEmptySeconds"]
        result["falsePerReviewedEmptyHour"] = round(len(result["falseInReviewedEmpty"]) * 3600 / empty_seconds, 3) if empty_seconds else None
    return {"source": reference["source"], "durationSeconds": duration, "samples": len(sample_models),
            "model": fingerprint, "rules": results, "otherRuleObservations": other_rules}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reference", type=Path, help="Reviewed intervals JSON")
    parser.add_argument("--video", type=Path, required=True, help="Recording used by the worker")
    parser.add_argument("--trace", type=Path, required=True, help="Worker --detections JSONL")
    parser.add_argument("--events", type=Path, required=True, help="Worker replay stdout JSONL")
    args = parser.parse_args()
    try:
        reference = json.loads(args.reference.read_text())
        with args.video.open("rb") as video:
            if file_digest(video, "sha256").hexdigest() != reference["sourceSha256"]:
                raise ValueError("The recording SHA-256 does not match the reviewed reference.")
        print(json.dumps(score_replay(reference, args.trace, args.events), indent=2))
    except (OSError, KeyError, TypeError, ValueError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
