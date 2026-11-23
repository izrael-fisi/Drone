"""CPU YOLOX and optional OWLv2 image inference for site-owned video."""

from hashlib import file_digest
from pathlib import Path

import numpy as np
from PIL import Image

from video_analytics.tracking import Detection, overlap


def suppress_duplicates(detections, threshold=.45):
    keep = []
    for box in sorted(detections, key=lambda item: -item.score)[:500]:
        if all(box.label != old.label or overlap(box.box, old.box) < threshold for old in keep):
            keep.append(box)
        if len(keep) == 100:
            break
    return keep


def fingerprint(path, expected=None):
    with Path(path).open("rb") as stream:
        digest = file_digest(stream, "sha256").hexdigest()
    if expected and digest != expected:
        raise ValueError("A configured model file did not match its expected SHA-256.")
    return digest[:16]


def normalized_box(box, width, height):
    values = tuple(float(min(max(axis / scale, 0), 1)) for axis, scale in zip(box, (width, height, width, height)))
    return values if values[0] < values[2] and values[1] < values[3] else None


class YoloX:
    # YOLOX export uses the contiguous class indices in the authors' COCO demo.
    COCO = {0: "person", 1: "vehicle", 2: "vehicle", 3: "vehicle", 5: "vehicle", 7: "vehicle"}

    def __init__(self, spec):
        import onnxruntime as ort

        ort.disable_telemetry_events()
        self.id = "yolox:" + fingerprint(spec["path"], spec.get("sha256"))
        options = ort.SessionOptions()
        options.intra_op_num_threads = spec.get("threads", 2)
        options.inter_op_num_threads = 1
        provider = spec.get("provider", "CPUExecutionProvider")
        if provider not in ort.get_available_providers():
            raise ValueError("The configured ONNX execution provider is unavailable.")
        self.session = ort.InferenceSession(spec["path"], sess_options=options,
                                           providers=list(dict.fromkeys([provider, "CPUExecutionProvider"])))
        input_type = self.session.get_inputs()[0]
        self.input = input_type.name
        self.height, self.width = input_type.shape[-2:]
        if not all(type(side) is int and 32 <= side <= 2048 and side % 32 == 0 for side in (self.height, self.width)):
            raise ValueError("Use a fixed-size YOLOX ONNX export divisible by 32.")
        self.decoded = spec.get("decoded", False)
        if not self.decoded:
            grids, strides = [], []
            for stride in (8, 16, 32):
                y, x = np.mgrid[:self.height // stride, :self.width // stride]
                grids.append(np.stack((x, y), axis=-1).reshape(-1, 2))
                strides.append(np.full((x.size, 1), stride, dtype=np.float32))
            self.grid, self.strides = np.concatenate(grids), np.concatenate(strides)
        self.classes = ({int(key): label for key, label in spec["classes"].items()} if "classes" in spec else self.COCO)
        if not self.classes or any(key < 0 or label not in ("person", "vehicle", "drone") for key, label in self.classes.items()):
            raise ValueError("Map model output class indices to person, vehicle, or drone.")

    def infer(self, frame, minimum):
        height, width = frame.shape[:2]
        ratio = min(self.width / width, self.height / height)
        resized = Image.fromarray(frame).resize((max(1, int(width * ratio)), max(1, int(height * ratio))), Image.Resampling.BILINEAR)
        canvas = np.full((self.height, self.width, 3), 114, dtype=np.uint8)
        canvas[:resized.height, :resized.width] = np.asarray(resized)
        # Authors' ONNX export expects unscaled BGR channels and top-left padding.
        tensor = np.ascontiguousarray(canvas[:, :, ::-1].transpose(2, 0, 1)[None], dtype=np.float32)
        prediction = self.session.run(None, {self.input: tensor})[0][0]
        if prediction.ndim != 2 or max(self.classes) + 5 >= prediction.shape[1]:
            raise ValueError("YOLOX output does not match the configured class map.")
        if not self.decoded and prediction.shape[0] != len(self.grid):
            raise ValueError("YOLOX output does not match the configured stride grid.")
        class_scores = prediction[:, 5:]
        best = np.argmax(class_scores, axis=1)
        scores = prediction[:, 4] * class_scores[np.arange(len(best)), best]
        eligible = np.flatnonzero(np.isfinite(scores) & (scores >= minimum) & np.isin(best, list(self.classes)))
        detections = []
        for index in eligible[np.argsort(scores[eligible])[-500:]]:
            x, y, w, h = prediction[index, :4]
            if not self.decoded:
                x, y = (prediction[index, :2] + self.grid[index]) * self.strides[index]
                w, h = np.exp(np.clip(prediction[index, 2:4], -10, 10)) * self.strides[index]
            if not np.isfinite([x, y, w, h]).all():
                continue
            box = normalized_box(((x - w / 2) / ratio, (y - h / 2) / ratio, (x + w / 2) / ratio, (y + h / 2) / ratio), width, height)
            if box:
                detections.append(Detection(self.classes[int(best[index])], float(scores[index]), box, self.id))
        return suppress_duplicates(detections)


class OwlDrone:
    QUERIES = ["a photo of a flying quadcopter drone", "a photo of a small unmanned drone",
               "a photo of a flying bird", "a photo of an airplane", "a photo of a helicopter", "a photo of a kite"]

    def __init__(self, spec):
        import torch
        from transformers import Owlv2ForObjectDetection, Owlv2Processor

        directory = Path(spec["path"])
        self.id = "owlv2:" + fingerprint(directory / "model.safetensors", spec.get("sha256"))
        self.device = spec.get("device", "cpu")
        torch.set_num_threads(spec.get("threads", 2))
        self.torch = torch
        self.processor = Owlv2Processor.from_pretrained(directory, local_files_only=True, use_fast=False)
        self.model = Owlv2ForObjectDetection.from_pretrained(directory, local_files_only=True, use_safetensors=True).to(self.device).eval()
        self.tiles = spec.get("tiles", 1)
        if self.tiles not in (1, 2):
            raise ValueError("Choose one full frame or a 2 × 2 overlapping drone search.")

    def infer(self, frame, minimum):
        height, width = frame.shape[:2]
        xs = [(0, width)] if self.tiles == 1 else [(0, (width * 3) // 5), ((width * 2) // 5, width)]
        ys = [(0, height)] if self.tiles == 1 else [(0, (height * 3) // 5), ((height * 2) // 5, height)]
        results = []
        for x0, x1 in xs:
            for y0, y1 in ys:
                image = Image.fromarray(frame[y0:y1, x0:x1])
                inputs = self.processor(text=[self.QUERIES], images=image, return_tensors="pt").to(self.device)
                with self.torch.inference_mode():
                    output = self.model(**inputs)
                    target_size = self.torch.tensor([[image.height, image.width]], device=self.device)
                    detections = self.processor.image_processor.post_process_object_detection(output, threshold=minimum, target_sizes=target_size)[0]
                for score, label, box in zip(detections["scores"].tolist(), detections["labels"].tolist(), detections["boxes"].tolist()):
                    if label < 2:
                        crop_box = normalized_box(box, image.width, image.height)
                        if crop_box:
                            left, top, right, bottom = crop_box
                            full = normalized_box((x0 + left * image.width, y0 + top * image.height,
                                                   x0 + right * image.width, y0 + bottom * image.height), width, height)
                            if full:
                                results.append(Detection("drone", score, full, self.id))
        return suppress_duplicates(results)


def load_detector(spec):
    return {"yolox": YoloX, "owlv2": OwlDrone}[spec["kind"]](spec)
