"""Compare YOLO11 person detection on the authors' labeled miniNIRPed night images."""

import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import json
import os
from pathlib import Path
import re
from statistics import median, quantiles
import subprocess
import sys
from time import perf_counter

from evaluate_cctv_replay import iou
from prepare_cctv_analytics_demo import checksum, retrieve

ROOT = Path(__file__).resolve().parents[2]
REVISION = "5531eb05a204537236483aae3c91772208f4b4d1"
SOURCE = f"https://raw.githubusercontent.com/XiaobiaoDai/NIRPed/{REVISION}/data/miniNIRPed/"
SPLITS = {
    "val": ("c9a82f18a52725c4198d39fdcaeac3898c13de83318fc258ce19a34554034673",
            "f536b9e908b4862707071263335791256d2cfbdcccedc37e8f3a53557d684920"),
    "train": ("ebb72ff1f597602e2b69c3a1a4622b10ac8b22d77424332985ba47241e2053e2",
              "5699a7e19c580103527f088d57d5255fb2f6bed5f09590b0bf9337e7c6fc5fe7"),
}
OVERLAPS = (.30, .50)


def load_split(output, split):
    label_hash, images_hash = SPLITS[split]
    retrieve(output, "annotations.json", SOURCE + f"labels/{split}_mini.json", label_hash)
    data = json.loads((output / "annotations.json").read_text())
    names = sorted(item["file_name"] for item in data["images"])
    if len(names) != len(set(names)) or any(not re.fullmatch(r"[A-Za-z0-9_-]+\.png", name) for name in names):
        raise ValueError("The pinned NIRPed split contains unexpected image names.")

    def fetch(name):
        path = "images/" + name
        retrieve(output, path, SOURCE + f"images%26pickles/{split}/{name}", None)
        return f"{name} {checksum(output / path)}\n".encode()

    digest = sha256()
    with ThreadPoolExecutor(8) as pool:
        for entry in pool.map(fetch, names):
            digest.update(entry)
    if digest.hexdigest() != images_hash:
        raise ValueError("An image differs from the pinned split. Restage it in a fresh directory.")
    return data, images_hash


def references(annotations, width, height):
    targets, excluded = [], []
    for item in annotations:
        category = item["category_id"]
        if category == 6:  # Background label
            continue
        x, y, w, h = item["bbox"]
        box = (x / width, y / height, (x + w) / width, (y + h) / height)
        if category == 1 and not item["Ign"]:
            targets.append((box, "under 96 px" if h < 96 else "96+ px"))
        else:
            excluded.append(box)
    return targets, excluded


def score(detections, targets, excluded, minimum_iou):
    remaining = dict(enumerate(targets))
    result = Counter()
    for detection in detections:
        best, index = max(((iou(detection.box, box), i) for i, (box, _) in remaining.items()), default=(0., -1))
        if best >= minimum_iou:
            _, size = remaining.pop(index)
            result["matched"] += 1
            result[size] += 1
        elif any(iou(detection.box, box) >= .30 for box in excluded):
            result["excludedProposals"] += 1
        else:
            result["unmatchedProposals"] += 1
    return result


def benchmark(output, data, model, digest, provider, preprocessing, confidence, *, person_only=False):
    import numpy as np
    from PIL import Image, ImageOps

    sys.path.insert(0, str(ROOT / "services/camera-gateway"))
    from video_analytics.detectors import Yolo11View

    detector = Yolo11View({"kind": "yolo11", "path": str(model), "sha256": digest, "provider": provider},
                          person_only=person_only)
    by_image = defaultdict(list)
    for row in data["annotations"]:
        by_image[row["image_id"]].append(row)
    counts = Counter()
    metrics = {threshold: Counter() for threshold in OVERLAPS}
    elapsed = []
    gamma = np.rint(255 * (np.arange(256) / 255) ** .65).astype(np.uint8)
    for frame in sorted(data["images"], key=lambda row: row["id"]):
        with Image.open(output / "images" / frame["file_name"]) as source:
            image = source.convert("RGB")
        width, height = image.size
        if (width, height) != (frame["width"], frame["height"]):
            raise ValueError("An image does not match its recorded dimensions.")
        targets, excluded = references(by_image[frame["id"]], width, height)
        counts.update(size for _, size in targets)
        started = perf_counter()
        if preprocessing == "autocontrast":
            image = ImageOps.autocontrast(image, cutoff=1, preserve_tone=True)
        pixels = np.asarray(image)
        if preprocessing == "gamma":
            pixels = gamma[pixels]
        detections = sorted((item for item in detector.detect(pixels, confidence) if item.label == "person"),
                            key=lambda item: -item.score)
        elapsed.append(1000 * (perf_counter() - started))
        for threshold, row in metrics.items():
            row.update(score(detections, targets, excluded, threshold))
    total = sum(counts.values())
    warm = elapsed[3:] or elapsed
    p95 = quantiles(warm, n=20, method="inclusive")[18] if len(warm) > 1 else warm[0]
    return {
        "model": detector.id, "modelSha256": digest, "provider": provider,
        "modelInput": [detector.input_width, detector.input_height], "preprocessing": preprocessing,
        "images": len(elapsed), "targets": total, "targetsByHeight": dict(counts),
        "p50Milliseconds": round(median(warm), 2),
        "p95Milliseconds": round(p95, 2),
        "scores": [{"confidence": confidence, "iou": threshold, "matched": row["matched"],
                    "missed": total - row["matched"], "matchedByHeight": {size: row[size] for size in counts},
                    "unmatchedProposals": row["unmatchedProposals"], "excludedProposals": row["excludedProposals"]}
                   for threshold, row in metrics.items()],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cache", type=Path, help="Cache for pinned public images and annotations")
    parser.add_argument("--split", choices=SPLITS, default="val")
    parser.add_argument("--capture-day", action="append", metavar="YYYYMMDD",
                        help="Score only this capture day. Repeat for a date-separated holdout.")
    parser.add_argument("--weights", type=Path, required=True)
    parser.add_argument("--person-only", action="store_true", help="Evaluate a single-class YOLO11 person export.")
    parser.add_argument("--sha256", required=True, help="SHA-256 of the YOLO11 ONNX export")
    parser.add_argument("--provider", default="CPUExecutionProvider")
    parser.add_argument("--confidence", type=float, default=.25)
    parser.add_argument("--preprocessing", choices=("original", "gamma", "autocontrast"), default="original")
    args = parser.parse_args()
    output = args.cache.expanduser().resolve() / args.split
    if output.is_relative_to(ROOT) or not re.fullmatch(r"[0-9a-f]{64}", args.sha256):
        parser.error("Use a cache outside the checkout and an approved lowercase SHA-256.")
    if not 0 < args.confidence <= 1:
        parser.error("Choose a positive confidence threshold at most 1.")
    os.umask(0o077)
    try:
        data, images_hash = load_split(output, args.split)
        days = set(args.capture_day or [])
        if days:
            available = {image["file_name"][4:12] for image in data["images"]}
            if days - available:
                raise ValueError("Choose capture dates present in the selected miniNIRPed split.")
            data["images"] = [image for image in data["images"] if image["file_name"][4:12] in days]
        result = benchmark(output, data, args.weights.expanduser().resolve(), args.sha256, args.provider,
                           args.preprocessing, args.confidence, person_only=args.person_only)
    except (OSError, TypeError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        parser.error(str(error))
    print(json.dumps({"source": "miniNIRPed, Dai et al., CC BY 4.0", "revision": REVISION,
                      "authorSplit": args.split, "captureDays": sorted(days) if days else None,
                      "imageSetSha256": images_hash, **result}, indent=2))


if __name__ == "__main__":
    main()
