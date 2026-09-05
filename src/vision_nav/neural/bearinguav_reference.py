"""Pinned, offline Bearing-UAV checkpoint evaluation; never a vehicle pose source.

Run in a dedicated process/environment, not inside the Pi runtime. The upstream
network regresses block-relative coordinates and a direction vector, not a
calibrated global measurement or a probability of correct localization.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import io
import json
from pathlib import Path
import subprocess
import sys
import time
from unittest.mock import patch

import numpy as np


SOURCE_COMMIT = "d16e558a14bd0a9142c6901793fe6a6813e4e954"
HF_REVISION = "31098d2e44749817b624b6a9f6a8b5165e5d4357"
ARCHIVE_SHA256 = "f3b161273cd063aeeea84d9bf8fce2faea40d3ca615cc6b6424dbc75d53a4f35"
CHECKPOINT_SHA256 = "31dc15f9fa48cfcd5e58c22387ea5509ba932bb6f70be0d2dfc3c9bac7c45644"
CONFIG_SHA256 = "1d8028236cf7abeafc65b60ff867689dc9e41b9f73474231131fa2d643c766a5"
PATCH_ORDER = ("p1", "p2", "p3", "p4", "uav")
DATASET_CONVENTION = "bearinguav_90k_row_major_xy_pixel_down_quarter_block_v1"
REQUEST_SCHEMA = "vision_nav_bearinguav_sample_v1"


def checked_bytes(path: Path, expected: str, limit: int) -> bytes:
    if path.stat().st_size > limit:
        raise ValueError(f"Artifact exceeds size limit: {path}")
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit or hashlib.sha256(data).hexdigest() != expected:
        raise ValueError(f"Artifact checksum mismatch: {path}")
    return data


def verify_source(source: Path) -> None:
    def git(*args):
        return subprocess.check_output(["git", "-C", str(source), *args], text=True).strip()

    if Path(git("rev-parse", "--show-toplevel")).resolve() != source.resolve():
        raise ValueError("Source must be the root of its own pinned checkout")
    if git("rev-parse", "HEAD") != SOURCE_COMMIT or git("diff", "HEAD", "--"):
        raise ValueError("Bearing-UAV source revision or tracked contents differ from the pin")
    tracked = set(git("ls-files").splitlines())
    # Untracked Python can shadow a pinned package even when ignored by Git.
    for path in source.rglob("*.py"):
        if path.relative_to(source).as_posix() not in tracked:
            raise ValueError(f"Unexpected Python source in upstream checkout: {path}")


def load_patches(request: dict, root: Path):
    import cv2
    import torch

    if not isinstance(request, dict) or request.get("schema_version") != REQUEST_SCHEMA:
        raise ValueError(f"Expected {REQUEST_SCHEMA}")
    for key in ("sample_id", "dataset_release_id", "split"):
        if not isinstance(request.get(key), str) or not request[key].strip():
            raise ValueError(f"Missing sample provenance: {key}")
    if request.get("input_kind") not in {"synthetic", "recorded"}:
        raise ValueError("input_kind must explicitly identify synthetic or recorded imagery")
    patches = request.get("patches")
    if not isinstance(patches, dict) or set(patches) != set(PATCH_ORDER):
        raise ValueError("Exactly p1, p2, p3, p4, uav patches are required")
    images, provenance = [], []
    root = root.resolve()
    for name in PATCH_ORDER:
        item = patches[name]
        if (not isinstance(item, dict) or not isinstance(item.get("path"), str)
                or not isinstance(item.get("sha256"), str) or len(item["sha256"]) != 64):
            raise ValueError("Each patch requires a path and SHA-256 digest")
        relative = Path(item["path"])
        path = (root / relative).resolve()
        if relative.is_absolute() or not path.is_relative_to(root):
            raise ValueError("Image paths must remain inside the sample directory")
        data = checked_bytes(path, item["sha256"], 16 * 1024 * 1024)
        # Match official cv2.imread -> RGB -> ToTensor -> ImageNet Normalize.
        image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        if image is None or not all(32 <= value <= 512 for value in image.shape[:2]):
            raise ValueError("Each decoded patch must be between 32 and 512 pixels per axis")
        image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        images.append(torch.from_numpy(image.transpose(2, 0, 1).copy()).float() / 255.0)
        provenance.append({"role": name, "path": relative.as_posix(), "sha256": item["sha256"]})
    if len({tuple(image.shape) for image in images}) != 1:
        raise ValueError("All five patches must have identical shape; no implicit resize is allowed")
    tensor = torch.stack(images).unsqueeze(0)
    mean = tensor.new_tensor([0.485, 0.456, 0.406]).view(1, 1, 3, 1, 1)
    std = tensor.new_tensor([0.229, 0.224, 0.225]).view(1, 1, 3, 1, 1)
    return (tensor - mean) / std, provenance


def load_model(source: Path, checkpoint: Path, configuration: Path, device: str,
               *, checkpoint_sha256: str = CHECKPOINT_SHA256):
    import torch
    import torchvision.models as models

    if device not in {"cpu", "cuda"} or (device == "cuda" and not torch.cuda.is_available()):
        raise ValueError("Requested CPU/CUDA execution device is unavailable")
    verify_source(source)
    config = json.loads(checked_bytes(configuration, CONFIG_SHA256, 64 * 1024))
    if (len(checkpoint_sha256) != 64
            or any(c not in "0123456789abcdef" for c in checkpoint_sha256)):
        raise ValueError("Expected an explicit lowercase SHA-256 checkpoint digest")
    weights = checked_bytes(checkpoint, checkpoint_sha256, 100 * 1024 * 1024)
    if config["model_class"] != "PARCASGM_v5a" or config["model_kwargs"]["backbone_name"] != "vgg16":
        raise ValueError("Only the pinned official cross-view model is supported")
    for name, module in list(sys.modules.items()):
        if name == "cvphr" or name.startswith("cvphr."):
            origin = getattr(module, "__file__", None)
            if origin and not Path(origin).resolve().is_relative_to(source.resolve()):
                raise ValueError("A different cvphr checkout is already imported; use a fresh process")
    sys.path.insert(0, str(source.resolve()))
    try:
        module = importlib.import_module("cvphr.models.posaglreg.models")
        original = models.vgg16

        def without_pretraining(**kwargs):
            kwargs.pop("pretrained", None)
            kwargs["weights"] = None
            return original(**kwargs)

        # The checkpoint includes the backbone. Do not fetch ImageNet weights
        # just to overwrite them. Strict loading below rejects incomplete data.
        with patch.object(models, "vgg16", without_pretraining), patch.object(
            torch.hub, "download_url_to_file", side_effect=RuntimeError("Implicit download forbidden")
        ):
            model = module.PARCASGM_v5a(**config["model_kwargs"])
        saved = torch.load(io.BytesIO(weights), map_location="cpu", weights_only=True)
        if not isinstance(saved, dict) or set(saved) != {"model_state_dict"}:
            raise ValueError("Unexpected checkpoint structure")
        state = saved["model_state_dict"]
        if not all(isinstance(value, torch.Tensor) and torch.isfinite(value).all() for value in state.values()):
            raise ValueError("Checkpoint contains non-finite or non-tensor values")
        model.load_state_dict(state, strict=True)
        return model.eval().to(device)
    finally:
        sys.path.pop(0)


def predict(model, tensor, device: str = "cpu") -> tuple[np.ndarray, list[float]]:
    """Run only the image tensor through the model; evaluation labels stay outside."""
    import torch
    tensor = tensor.to(device)
    outputs, elapsed = [], []
    with torch.inference_mode():
        for _ in range(2):
            if device == "cuda":
                torch.cuda.synchronize()
            start = time.perf_counter()
            position, direction = model(tensor)
            if device == "cuda":
                torch.cuda.synchronize()
            elapsed.append((time.perf_counter() - start) * 1000)
            if position.shape != (1, 2) or direction.shape != (1, 2):
                raise ValueError("Unexpected model output dimensions")
            outputs.append(np.concatenate((position.cpu().numpy(), direction.cpu().numpy()), axis=1)[0])
    if not np.isfinite(outputs).all() or not np.array_equal(outputs[0], outputs[1]):
        raise ValueError("Model output is non-finite or repeated inference was not deterministic")
    return outputs[0], elapsed


def evaluate(request: dict, root: Path, source: Path, checkpoint: Path, configuration: Path,
             *, device: str = "cpu", threads: int = 2) -> dict:
    import cv2
    import torch
    import torchvision

    if type(threads) is not int or not 1 <= threads <= 16:
        raise ValueError("threads must be an integer from 1 to 16")
    torch.set_num_threads(threads)
    torch.manual_seed(0)
    torch.use_deterministic_algorithms(True)
    tensor, inputs = load_patches(request, root)
    model = load_model(source, checkpoint, configuration, device)
    prediction, elapsed = predict(model, tensor, device)
    result = {
        "schema_version": "vision_nav_bearinguav_reference_v1",
        "status": "research_inference_only", "navigation_mode": "NO_GLOBAL_FIX",
        "position_authority": False, "px4_external_vision": "disabled",
        "sample_id": request["sample_id"], "dataset_release_id": request["dataset_release_id"],
        "input_kind": request["input_kind"], "split": request["split"],
        "patch_order": list(PATCH_ORDER), "inputs": inputs, "tensor_shape": list(tensor.shape),
        "relative_position_xy": prediction[:2].tolist(), "direction_xy": prediction[2:].tolist(),
        "coordinate_frame": "official_normalized_satellite_block_not_ENU",
        "metric_pose": None, "covariance": None, "heading_confidence": None,
        "source_revision": SOURCE_COMMIT, "weights_revision": HF_REVISION,
        "adapter_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "preprocessing": "opencv_RGB_float32_div255_imagenet_normalization_no_resize",
        "checkpoint_sha256": CHECKPOINT_SHA256, "configuration_sha256": CONFIG_SHA256,
        "weights_license_status": "unverified", "field_ready": False,
        "request_sha256": hashlib.sha256(json.dumps(request, sort_keys=True, allow_nan=False).encode()).hexdigest(),
        "environment": {"torch": torch.__version__, "torchvision": torchvision.__version__,
                        "numpy": np.__version__, "opencv": cv2.__version__, "device": device,
                        "threads": torch.get_num_threads(), "python": sys.version.split()[0]},
        "inference_ms": elapsed, "repeat_exact": True,
        "parameter_count": sum(parameter.numel() for parameter in model.parameters()),
        "limitations": ["Known four-tile context, not global recovery",
                        "No no-match head or calibrated confidence",
                        "Single camera image, not a temporal or visual-inertial estimator",
                        "Compatibility evidence only, not independently reproduced paper accuracy"],
    }
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--configuration", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
    parser.add_argument("--threads", type=int, default=2)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output already exists; research evidence is immutable")
    if args.sample.stat().st_size > 64 * 1024:
        parser.error("Sample manifest exceeds 64 KiB")
    request = json.loads(args.sample.read_text())
    try:
        result = evaluate(request, args.sample.parent, args.source, args.checkpoint, args.configuration,
                          device=args.device, threads=args.threads)
    except (ValueError, KeyError, OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        result = {"status": "withheld", "reason": str(exc), "navigation_mode": "NO_GLOBAL_FIX",
                  "position_authority": False, "px4_external_vision": "disabled"}
    with args.output.open("x") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({key: result[key] for key in ("status", "navigation_mode", "position_authority")}))
    if result["status"] == "withheld":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
