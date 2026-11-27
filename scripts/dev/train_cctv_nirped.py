"""Fine-tune a YOLO11s person model on the date-separated miniNIRPed experiment."""

import argparse
from collections import defaultdict
import os
from pathlib import Path

from PIL import Image, ImageDraw

from benchmark_cctv_nirped import ROOT, load_split
from prepare_cctv_analytics_demo import checksum


CALIBRATION_DAYS = {"20190508", "20200529"}
HOLDOUT_DAYS = {"20190326", "20200401", "20200525", "20200624", "20210315"}


def bounds(item, width, height):
    x, y, w, h = item["bbox"]
    return max(0, x), max(0, y), min(width, x + w), min(height, y + h)


def prepare_training(source, output, data):
    by_image = defaultdict(list)
    for row in data["annotations"]:
        by_image[row["image_id"]].append(row)
    for split in ("train", "val"):
        (output / "images" / split).mkdir(parents=True, exist_ok=True)
        (output / "labels" / split).mkdir(parents=True, exist_ok=True)
    for frame in data["images"]:
        day = frame["file_name"][4:12]
        if day in HOLDOUT_DAYS:
            continue
        split = "val" if day in CALIBRATION_DAYS else "train"
        width, height = frame["width"], frame["height"]
        objects = by_image[frame["id"]]
        labels = [bounds(item, width, height) for item in objects if item["category_id"] == 1 and not item["Ign"]]
        labels = [(left, top, right, bottom) for left, top, right, bottom in labels if left < right and top < bottom]
        ignored = [bounds(item, width, height) for item in objects if item["Ign"] or item["category_id"] == 2]
        name = frame["file_name"]
        raw, prepared = source / name, output / "images" / split / name
        if split == "train" and ignored:
            with Image.open(raw) as original:
                image = original.convert("RGB")
            # Keep the pixels of every positive box, including where it overlaps an ignored group.
            mask = Image.new("L", image.size)
            draw = ImageDraw.Draw(mask)
            for left, top, right, bottom in ignored:
                if left < right and top < bottom:
                    draw.rectangle((left, top, right, bottom), fill=255)
            for rectangle in labels:
                draw.rectangle(rectangle, fill=0)
            image.paste((114, 114, 114), (0, 0, width, height), mask)
            image.save(prepared)
        elif not prepared.exists():
            prepared.symlink_to(raw)
        lines = [f"0 {(left + right) / (2 * width):.8f} {(top + bottom) / (2 * height):.8f} "
                 f"{(right - left) / width:.8f} {(bottom - top) / height:.8f}" for left, top, right, bottom in labels]
        (output / "labels" / split / (Path(name).stem + ".txt")).write_text("\n".join(lines), encoding="ascii")
    config = output / "data.yaml"
    config.write_text("train: images/train\nval: images/val\nnames: [person]\n", encoding="ascii")
    return config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cache", type=Path, help="Location of the checksummed miniNIRPed cache")
    parser.add_argument("output", type=Path, help="Run and export directory outside the checkout")
    parser.add_argument("--base", type=Path, required=True, help="Approved Ultralytics YOLO11s training checkpoint")
    parser.add_argument("--base-sha256", required=True)
    parser.add_argument("--device", default="cpu")
    args = parser.parse_args()
    output, cache = args.output.expanduser().resolve(), args.cache.expanduser().resolve()
    if output.is_relative_to(ROOT) or cache.is_relative_to(ROOT):
        parser.error("Keep downloaded data and model artifacts outside the checkout.")
    base = args.base.expanduser().resolve(strict=True)
    if checksum(base) != args.base_sha256:
        parser.error("The approved base checkpoint did not match its SHA-256.")
    data, _ = load_split(cache / "train", "train")
    config = prepare_training(cache / "train" / "images", output / "training", data)

    os.environ.setdefault("YOLO_CONFIG_DIR", str(output / "ultralytics"))
    from ultralytics import YOLO, settings

    settings.update({"sync": False, "weights_dir": str(output / "weights"), "datasets_dir": str(output / "datasets")})
    model = YOLO(str(base))
    model.train(data=str(config), device=args.device, imgsz=640, batch=8, epochs=8,
                freeze=10, optimizer="AdamW", lr0=.001, seed=42, workers=0, mosaic=0, mixup=0,
                hsv_h=0, hsv_s=0, hsv_v=.20, translate=.05, scale=.20, fliplr=.5, amp=False, plots=False,
                project=str(output / "runs"), name="yolo11s-nir")
    exported = Path(YOLO(str(model.trainer.last)).export(format="onnx", imgsz=640, batch=1, device="cpu",
                                                       dynamic=False, simplify=False, opset=17, nms=False))
    print(f"{exported}\nSHA-256 {checksum(exported)}")


if __name__ == "__main__":
    main()
