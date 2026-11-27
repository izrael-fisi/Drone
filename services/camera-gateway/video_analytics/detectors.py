"""YOLO11, YOLOX and optional OWLv2 image inference for site-owned video."""

from hashlib import file_digest, sha256
import math
import os
from pathlib import Path
import platform

import numpy as np
from PIL import Image

from analytics_contract import excluded, qualifies
from site_geometry import contains
from video_analytics.tracking import Detection, overlap

COCO_CLASSES = {0: "person", 1: "vehicle", 2: "vehicle", 3: "vehicle", 5: "vehicle", 7: "vehicle"}


def person_color_signature(frame, box):
    height, width = frame.shape[:2]
    left, top, right, bottom = (axis * size for axis, size in zip(box, (width, height, width, height)))
    w, h = right - left, bottom - top
    if w < 7 or h < 20:
        return None
    # Sample two bands on the body, away from the head and box edges.
    columns = np.rint(np.linspace(left + .27 * w, right - .27 * w, 12)).astype(int).clip(0, width - 1)
    rows = np.rint(np.linspace(top + .25 * h, top + .81 * h, 24)).astype(int).clip(0, height - 1)
    bins = frame[np.ix_(rows, columns)] >> 6
    codes = 16 * bins[..., 0] + 4 * bins[..., 1] + bins[..., 2]
    return np.concatenate([np.sqrt(np.bincount(band.ravel(), minlength=64) / band.size)
                           for band in np.split(codes, 2)]).astype(np.float32)


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


def yolo11_detail_region(value, width, height):
    if (not isinstance(value, (list, tuple)) or len(value) != 4 or
            not all(type(axis) in (float, int) and 0 <= axis <= 1 for axis in value)):
        raise ValueError("Use four normalized coordinates for the YOLO11 detail region.")
    left, top, right, bottom = value
    if (right - left) * width < 32 or (bottom - top) * height < 32:
        raise ValueError("The YOLO11 detail region must be at least 32 pixels wide and high.")
    return tuple(map(float, value))


def person_rule_detail_region(rules, width, height):
    person_rules = [rule for rule in rules if rule["object"] == "person"]
    points = [point for rule in person_rules for point in rule["points"]]
    if not points:
        return None
    xs, ys = zip(*points)
    # Person rules use foot positions. Leave more room above the rule for the body.
    region = tuple(round(value, 6) for value in (
        max(0., min(xs) - .075), max(0., min(ys) - .145),
        min(1., max(xs) + .075), min(1., max(ys) + .105),
    ))
    left, top, right, bottom = region
    crop_width, crop_height = right - left, bottom - top
    if crop_width > .65 or crop_height > .65 or crop_width * width < 32 or crop_height * height < 32:
        return None
    if (len(person_rules) == 1 and person_rules[0].get("kind") == "line-crossing"
            and (max(ys) - min(ys)) * height >= 2 * (max(xs) - min(xs)) * width
            and crop_height * height > 2.2 * crop_width * width):
        # A tall wire needs approach space on both sides. Keep the padding above
        # the feet and reduce padding below the lower endpoint to limit downscaling.
        bottom = min(bottom, max(ys) + .03)
        span = min(.65, max(crop_width, (bottom - top) * height / (2.2 * width)))
        left = min(max((left + right - span) / 2, 0.), 1 - span)
        return tuple(round(value, 6) for value in (left, top, left + span, bottom))
    return region


def person_rule_detail_regions(rules, width, height, limit):
    combined = person_rule_detail_region(rules, width, height)
    if combined:
        return (combined,)
    if limit == 1:
        return ()
    candidates = {person_rule_detail_region((rule,), width, height) for rule in rules}
    candidates.discard(None)
    # Favor tight crops when there are more separated rules than the inference budget.
    ordered = sorted(candidates, key=lambda box: ((box[2] - box[0]) * (box[3] - box[1]), box))
    return tuple(ordered[:limit])


def onnx_session(spec):
    import onnxruntime as ort

    ort.disable_telemetry_events()
    options = ort.SessionOptions()
    options.intra_op_num_threads = spec.get("threads", 2)
    options.inter_op_num_threads = 1
    if spec["kind"] == "yolo11":
        options.add_session_config_entry("session.intra_op.allow_spinning", "0")
    provider = spec.get("provider", "CPUExecutionProvider")
    if provider not in ort.get_available_providers():
        raise ValueError("The configured ONNX execution provider is unavailable.")
    providers = list(dict.fromkeys([provider, "CPUExecutionProvider"]))
    if "coremlCacheDirectory" not in spec:
        return ort.InferenceSession(spec["path"], sess_options=options, providers=providers)

    from fcntl import LOCK_EX, flock

    root = Path(spec["coremlCacheDirectory"])
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    # A replaced export or changed compiler must receive a fresh cache.
    cache = root / f"{platform.release()}-{ort.__version__}-{spec['sha256']}"
    cache.mkdir(mode=0o700, exist_ok=True)
    metadata = cache.stat()
    if metadata.st_uid != os.geteuid() or metadata.st_mode & 0o077:
        raise ValueError("Use a private CoreML cache owned by the worker account.")
    providers[0] = (provider, {"ModelCacheDirectory": str(cache)})
    with (cache / ".load.lock").open("a") as guard:
        flock(guard, LOCK_EX)
        return ort.InferenceSession(spec["path"], sess_options=options, providers=providers)


class YoloX:
    def __init__(self, spec):
        self.id = "yolox:" + fingerprint(spec["path"], spec.get("sha256"))
        self.session = onnx_session(spec)
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
        self.classes = ({int(key): label for key, label in spec["classes"].items()} if "classes" in spec else COCO_CLASSES)
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


class Yolo11View:
    def __init__(self, spec, *, pose=False, person_only=False):
        self.pose = pose
        self.id = ("yolo11-pose:" if pose else "yolo11:") + fingerprint(spec["path"], spec["sha256"])
        self.session = onnx_session(spec)
        inputs, outputs = self.session.get_inputs(), self.session.get_outputs()
        shape = inputs[0].shape if len(inputs) == 1 else None
        task = "COCO pose" if pose else "single-class person detection" if person_only else "COCO detection"
        invalid_export = f"Use a static FP32 YOLO11 {task} export without embedded NMS (320–1280 pixels per side, divisible by 32)."
        if (not shape or len(shape) != 4 or shape[:2] != [1, 3] or inputs[0].type != "tensor(float)"
                or any(type(side) is not int or side < 320 or side > 1280 or side % 32 for side in shape[2:])):
            raise ValueError(invalid_export)
        self.input_height, self.input_width = shape[2:]
        anchors = sum(self.input_height // stride * (self.input_width // stride) for stride in (8, 16, 32))
        if (len(outputs) != 1 or outputs[0].type != "tensor(float)"
                or outputs[0].shape != [1, 56 if pose else 5 if person_only else 84, anchors]):
            raise ValueError(invalid_export)
        self.input = inputs[0].name
        self.rotation = spec.get("rotationDegrees", 0)
        if self.rotation:
            self.id += f":r{self.rotation}"

    def detect(self, frame, minimum, *, capture_floor=None):
        view = np.rot90(frame, -(self.rotation // 90)) if self.rotation else frame
        detections = self.detect_upright(view, minimum, capture_floor=capture_floor)
        if not self.rotation:
            return detections
        restored = []
        for item in detections:
            left, top, right, bottom = item.box
            if self.rotation == 90:
                box = (top, 1 - right, bottom, 1 - left)
            elif self.rotation == 180:
                box = (1 - right, 1 - bottom, 1 - left, 1 - top)
            else:
                box = (1 - bottom, left, 1 - top, right)
            restored.append(Detection(item.label, item.score, box, item.model))
        return restored

    def detect_upright(self, frame, minimum, *, capture_floor=None):
        height, width = frame.shape[:2]
        ratio = min(self.input_width / width, self.input_height / height)
        size = max(1, round(width * ratio)), max(1, round(height * ratio))
        left, top = (self.input_width - size[0]) // 2, (self.input_height - size[1]) // 2
        resized = Image.fromarray(frame).resize(size, Image.Resampling.BILINEAR)
        canvas = np.full((self.input_height, self.input_width, 3), 114, dtype=np.uint8)
        canvas[top:top + size[1], left:left + size[0]] = np.asarray(resized)
        tensor = np.ascontiguousarray(canvas.transpose(2, 0, 1)[None], dtype=np.float32) / 255
        prediction = self.session.run(None, {self.input: tensor})[0][0].T
        if self.pose:
            category = np.zeros(len(prediction), dtype=int)
            scores = prediction[:, 4]
        else:
            category = np.argmax(prediction[:, 4:], axis=1)
            scores = prediction[np.arange(len(category)), category + 4]
        valid = np.isfinite(scores) & np.isin(category, list(COCO_CLASSES))
        strong = np.flatnonzero(valid & (scores >= minimum))
        ranked = strong[np.argsort(scores[strong])[-500:][::-1]]
        if capture_floor is not None and capture_floor < minimum and len(ranked) < 500:
            # Preserve the original ordering of tied strong scores when weak candidates are present.
            weak = np.flatnonzero(valid & (scores >= capture_floor) & (scores < minimum))
            remaining = 500 - len(ranked)
            ranked = np.concatenate((ranked, weak[np.argsort(scores[weak])[-remaining:][::-1]]))
        selected = []
        for index in ranked:
            x, y, w, h = prediction[index, :4]
            if not np.isfinite([x, y, w, h]).all() or w <= 0 or h <= 0:
                continue
            bounds = ((x - w / 2 - left) / ratio, (y - h / 2 - top) / ratio,
                      (x + w / 2 - left) / ratio, (y + h / 2 - top) / ratio)
            cls = int(category[index])
            if any(cls == previous and overlap(bounds, box) >= .45 for previous, box, _ in selected):
                continue
            box = normalized_box(bounds, width, height)
            if box:
                selected.append((cls, bounds, Detection(COCO_CLASSES[cls], float(scores[index]), box, self.id)))
            if len(selected) == 100:
                break
        return [detection for _, _, detection in selected]


def same_pose_subject(full, pose):
    a, b = full.box, pose.box
    aw, ah, bw, bh = a[2] - a[0], a[3] - a[1], b[2] - b[0], b[3] - b[1]
    if abs(full.anchor[0] - pose.anchor[0]) > .5 * min(aw, bw) or abs(a[3] - b[3]) > .15 * min(ah, bh):
        return False
    width = max(0., min(a[2], b[2]) - max(a[0], b[0]))
    height = max(0., min(a[3], b[3]) - max(a[1], b[1]))
    return width * height >= .75 * min(aw * ah, bw * bh)


def person_qualifies_in_zone(item, rule, dimensions):
    return (item.label == "person" and contains(rule["points"], item.anchor) and not excluded(rule, item.anchor)
            and qualifies(rule, {"object": item.label, "box": item.box, "score": item.score, "frame": dimensions}))


class Yolo11(Yolo11View):
    person_detector = None
    automatic_person_model = False

    def __init__(self, spec):
        super().__init__(spec)
        self.general_frame_id = self.id
        if person_model := spec.get("personModel"):
            self.person_detector = Yolo11View({**spec, **person_model}, person_only=True)
            self.id += ":person:" + person_model["sha256"][:16]
        self.specialist_frame_id = self.id
        self.automatic_person_model = spec.get("personModelMode") == "monochrome"
        self.person_model_active = not self.automatic_person_model
        self.monochrome_samples = 0
        self.full_frame_id = self.general_frame_id if self.automatic_person_model else self.id
        self.detail_from_rules = spec.get("detailFromRules", False)
        self.detail_region_limit = spec.get("detailRegionLimit", 1)
        self.detail_policy_version = None
        self.detail_objects = tuple(spec.get("detailObjects", ("person",)))
        pose = spec.get("detailPose")
        self.detail_detector = Yolo11View({**spec, **pose}, pose=True) if pose else None
        self.detail_pose_fallback = spec.get("detailPoseFallback", False)
        region = spec.get("detailRegion")
        self.configure_detail((region,) if region else ())

    def configure_detail(self, regions, source="detail"):
        self.detail_regions = regions
        self.detail_source = source
        self.id = self.full_frame_id
        if not regions:
            return
        region = regions[0] if len(regions) == 1 else regions
        profile = region if self.detail_objects == ("person",) else (region, self.detail_objects)
        if self.detail_detector:
            profile = (profile, self.detail_detector.id)
        if self.detail_pose_fallback:
            profile = (profile, "pose-fallback")
        digest = sha256(repr(profile).encode("ascii")).hexdigest()[:8]
        self.id += f":{source}:{digest}"

    def apply_rule_detail(self, policy, width, height):
        if self.detail_policy_version != policy["version"]:
            regions = person_rule_detail_regions(policy["rules"], width, height, self.detail_region_limit)
            self.configure_detail(regions, "rule-detail")
            self.detail_policy_version = policy["version"]

    def select_person_model(self, frame, missed_sample):
        if missed_sample:
            self.monochrome_samples = 0
        pixels = frame[::8, ::8]
        chromatic = np.count_nonzero(np.ptp(pixels, axis=2) > 8) / pixels[..., 0].size
        active = self.person_model_active
        if chromatic >= .08:
            active = False
            self.monochrome_samples = 0
        elif not active:
            dark, light = np.percentile(pixels[..., 1], (5, 95))
            self.monochrome_samples = self.monochrome_samples + 1 if chromatic <= .02 and light - dark >= 16 else 0
            active = self.monochrome_samples >= 3
        if active == self.person_model_active:
            return False
        self.person_model_active = active
        self.full_frame_id = self.specialist_frame_id if active else self.general_frame_id
        self.configure_detail(self.detail_regions, self.detail_source)
        return True

    def detect_objects(self, frame, minimum, capture_floor, objects):
        if self.person_detector is None:
            return self.detect(frame, minimum, capture_floor=capture_floor)
        if self.automatic_person_model and not self.person_model_active:
            return [item for item in self.detect(frame, minimum, capture_floor=capture_floor) if item.label in objects]
        general = ([item for item in self.detect(frame, minimum, capture_floor=capture_floor) if item.label == "vehicle"]
                   if "vehicle" in objects else [])
        if "person" not in objects:
            return general
        people = self.person_detector.detect(frame, minimum, capture_floor=capture_floor)
        return general + [Detection(item.label, item.score, item.box, self.id) for item in people]

    def crop_detections(self, frame, region, detector, minimum, capture_floor, objects):
        height, width = frame.shape[:2]
        x0, y0, x1, y1 = region
        left, top = math.floor(x0 * width), math.floor(y0 * height)
        right, bottom = math.ceil(x1 * width), math.ceil(y1 * height)
        crop = frame[top:bottom, left:right]
        crop_height, crop_width = crop.shape[:2]
        internal_edges = (left > 0, top > 0, right < width, bottom < height)
        found = (self.detect_objects(crop, minimum, capture_floor, objects) if detector is self
                 else detector.detect(crop, minimum, capture_floor=capture_floor))
        crop_boxes = []
        for item in found:
            if item.label not in self.detail_objects:
                continue
            a, b, c, d = item.box
            border_distances = (a * crop_width, b * crop_height, (1 - c) * crop_width, (1 - d) * crop_height)
            # A truncated person box can shift the estimated foot position used by a rule.
            if item.label == "person" and any(internal and distance <= 4 + 1e-6
                                              for internal, distance in zip(internal_edges, border_distances)):
                continue
            box = normalized_box((left + a * crop_width, top + b * crop_height,
                                  left + c * crop_width, top + d * crop_height), width, height)
            if box:
                crop_boxes.append(Detection(item.label, item.score, box, self.id))
        return crop_boxes

    def merge_crop(self, detections, crop_boxes, minimum, use_pose):
        candidates = [item for item in detections if item.label in self.detail_objects]
        if use_pose:
            # The same partly occluded person can have different box heights in the two models.
            candidates = [full for full in candidates if not any(
                (pose.score >= minimum or full.score < minimum) and same_pose_subject(full, pose) for pose in crop_boxes)]
        return [item for item in detections if item.label not in self.detail_objects] + suppress_duplicates(candidates + crop_boxes)

    def infer(self, frame, minimum, *, capture_floor=None, fallback_rules=None, objects=("person", "vehicle")):
        self.last_pose_fallback = False
        detections = self.detect_objects(frame, minimum, capture_floor, objects)
        if not self.detail_regions:
            return detections
        cropped_objects = set(self.detail_objects).intersection(objects)
        if self.person_detector is not None and not cropped_objects:
            return detections
        detector = self if fallback_rules is not None else (self.detail_detector or self)
        crop_boxes = [item for region in self.detail_regions
                      for item in self.crop_detections(frame, region, detector, minimum, capture_floor, cropped_objects)]
        base = self.merge_crop(detections, crop_boxes, minimum, detector is self.detail_detector)
        if not fallback_rules:
            return base

        height, width = frame.shape[:2]
        dimensions = width, height
        for region in self.detail_regions:
            left, top, right, bottom = region
            missing = [rule for rule in fallback_rules
                       if all(left <= x <= right and top <= y <= bottom for x, y in rule["points"])
                       and not any(person_qualifies_in_zone(item, rule, dimensions) for item in base)]
            if not missing:
                continue
            self.last_pose_fallback = True
            crop_boxes = self.crop_detections(frame, region, self.detail_detector, minimum, capture_floor, cropped_objects)
            extra = self.merge_crop(detections, crop_boxes, minimum, True)
            base += [item for item in extra if any(person_qualifies_in_zone(item, rule, dimensions) for rule in missing)
                     and not any(old.label == "person" and overlap(item.box, old.box) >= .45 for old in base)]
        return base


class OwlDetector:
    PROMPTS = {
        "drone": (
            ("a photo of a flying quadcopter drone", "drone"), ("a photo of a small unmanned drone", "drone"),
            ("a photo of a flying bird", None), ("a photo of an airplane", None),
            ("a photo of a helicopter", None), ("a photo of a kite", None),
        ),
        "ground": (
            ("a photo of a person", "person"), ("a photo of a car", "vehicle"),
            ("a photo of a truck", "vehicle"), ("a photo of a bus", "vehicle"),
            ("a photo of a person riding a bicycle", "vehicle"), ("a photo of a motorcycle", "vehicle"),
            ("a photo of a person on a sign", None), ("a photo of a mannequin", None),
        ),
    }

    def __init__(self, spec):
        import torch
        from transformers import Owlv2ForObjectDetection, Owlv2Processor

        directory = Path(spec["path"])
        lane = spec.get("lane", "drone")
        queries, self.labels = zip(*self.PROMPTS[lane])
        self.queries = list(queries)
        prefix = "owlv2:ground-v1:" if lane == "ground" else "owlv2:"
        self.id = prefix + fingerprint(directory / "model.safetensors", spec.get("sha256"))
        self.device = spec.get("device", "cpu")
        torch.set_num_threads(spec.get("threads", 2))
        self.torch = torch
        self.processor = Owlv2Processor.from_pretrained(directory, local_files_only=True, use_fast=False)
        self.model = Owlv2ForObjectDetection.from_pretrained(directory, local_files_only=True, use_safetensors=True).to(self.device).eval()
        self.tiles = spec.get("tiles", 1)
        if self.tiles not in (1, 2):
            raise ValueError("Choose one full frame or a 2 × 2 overlapping image search.")

    def infer(self, frame, minimum):
        height, width = frame.shape[:2]
        xs = [(0, width)] if self.tiles == 1 else [(0, (width * 3) // 5), ((width * 2) // 5, width)]
        ys = [(0, height)] if self.tiles == 1 else [(0, (height * 3) // 5), ((height * 2) // 5, height)]
        results = []
        for x0, x1 in xs:
            for y0, y1 in ys:
                image = Image.fromarray(frame[y0:y1, x0:x1])
                inputs = self.processor(text=[self.queries], images=image, return_tensors="pt").to(self.device)
                with self.torch.inference_mode():
                    output = self.model(**inputs)
                    target_size = self.torch.tensor([[image.height, image.width]], device=self.device)
                    detections = self.processor.image_processor.post_process_object_detection(output, threshold=minimum, target_sizes=target_size)[0]
                for score, label, box in zip(detections["scores"].tolist(), detections["labels"].tolist(), detections["boxes"].tolist()):
                    category = self.labels[label]
                    if category:
                        crop_box = normalized_box(box, image.width, image.height)
                        if crop_box:
                            left, top, right, bottom = crop_box
                            full = normalized_box((x0 + left * image.width, y0 + top * image.height,
                                                   x0 + right * image.width, y0 + bottom * image.height), width, height)
                            if full:
                                results.append(Detection(category, score, full, self.id))
        return suppress_duplicates(results)


def load_detector(spec):
    return {"yolo11": Yolo11, "yolox": YoloX, "owlv2": OwlDetector}[spec["kind"]](spec)
