"""Score person detections on a checksummed Open Images crowd cohort."""

import argparse
from collections import Counter
import json
from pathlib import Path
import re
import subprocess
import sys

from benchmark_cctv_nirped import ROOT, score
from prepare_cctv_analytics_demo import checksum, retrieve


SPLITS = {"val": "validation", "holdout": "test"}
BUCKET = "https://open-images-dataset.s3.amazonaws.com"


def label_category(cohort, occluded):
    if cohort == "unoccluded":
        return "clearCohort"
    return "occluded" if occluded else "otherCrowd"


def benchmark(cache, phase, detector, confidences):
    import numpy as np
    from PIL import Image

    manifest = json.loads((cache / "open-images-selection.json").read_text())
    image_ids = {name: {item["ImageID"] for item in rows} for name, rows in manifest.items()}
    rows = manifest[phase]
    if (not rows or len(image_ids[phase]) != len(rows)
            or any(image_ids[phase] & ids for name, ids in image_ids.items() if name != phase)):
        raise ValueError("Use a nonempty cohort with unique image IDs disjoint from the other cohorts.")
    labels = Counter()
    results = {confidence: Counter() for confidence in confidences}
    images = []
    for row in rows:
        key, digest = row["ImageID"], row["sha256"]
        if (row["sourceSplit"] != SPLITS[phase] or not re.fullmatch(r"[a-f0-9]{16}", key)
                or not re.fullmatch(r"[a-f0-9]{64}", digest)):
            raise ValueError("Use Open Images IDs and SHA-256 digests from the published source split.")
        name = f"data/images/{phase}/{key}.jpg"
        retrieve(cache, name, f"{BUCKET}/{SPLITS[phase]}/{key}.jpg", digest)
        with Image.open(cache / name) as photo:
            frame = np.array(photo.convert("RGB"))
        truth = [(bounds, label_category(row["cohort"], occluded)) for bounds, occluded in row["people"]]
        labels.update(category for _, category in truth)
        detections = sorted((item for item in detector.detect(frame, min(confidences)) if item.label == "person"),
                            key=lambda item: -item.score)
        scores = {}
        for confidence, aggregate in results.items():
            matched = score([item for item in detections if item.score >= confidence], truth, [], .50)
            aggregate.update(matched)
            scores[confidence] = dict(matched)
        images.append({"id": key, "cohort": row["cohort"], "scores": scores})
    return {"source": "Open Images", "sourceSplit": SPLITS[phase], "cohort": phase,
            "manifestSha256": checksum(cache / "open-images-selection.json"), "model": detector.id,
            "images": len(images), "labels": labels, "iou": .50, "scores": results, "perImage": images}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cache", type=Path, help="Directory containing the checksummed cohort manifest")
    parser.add_argument("--phase", choices=SPLITS, default="holdout")
    parser.add_argument("--weights", required=True, type=Path)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--provider", default="CPUExecutionProvider")
    parser.add_argument("--person-only", action="store_true")
    parser.add_argument("--confidence", type=float, action="append")
    args = parser.parse_args()
    cache = args.cache.expanduser().resolve()
    confidences = sorted(set(args.confidence or [.25]))
    if (cache.is_relative_to(ROOT) or not re.fullmatch(r"[a-f0-9]{64}", args.sha256)
            or any(not 0 < confidence <= 1 for confidence in confidences)):
        parser.error("Use a cache outside the checkout, a pinned model digest, and confidence values above 0 and at most 1.")
    sys.path.insert(0, str(ROOT / "services/camera-gateway"))
    from video_analytics.detectors import Yolo11View

    try:
        detector = Yolo11View({"kind": "yolo11", "path": str(args.weights.expanduser().resolve()),
                               "sha256": args.sha256, "provider": args.provider},
                              person_only=args.person_only)
        report = benchmark(cache, args.phase, detector, confidences)
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        parser.error(str(error))
    print(json.dumps({**report, "modelSha256": args.sha256, "provider": args.provider}, indent=2))


if __name__ == "__main__":
    main()
