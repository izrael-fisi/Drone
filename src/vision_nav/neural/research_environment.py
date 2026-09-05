"""Audit a research host before starting a teacher-training job."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import importlib.util
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys
from typing import Any, Callable


RESEARCH_ENVIRONMENT_SCHEMA = "vision_nav_research_environment_v1"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _module_status(module_name: str, finder: Callable[[str], Any]) -> dict[str, Any]:
    return {"module": module_name, "available": finder(module_name) is not None}


def exercise_torch_backend(torch: Any, device: str) -> dict[str, Any]:
    """Small, analytic convolution/backward and matrix probes; no model download."""
    if device not in {"cpu", "cuda"}:
        raise ValueError("Probe device must be CPU or CUDA")
    if device == "cuda" and not torch.cuda.is_available():
        return {"status": "failed", "device": device, "reason": "cuda_unavailable_no_cpu_fallback"}
    try:
        image = torch.ones((1, 1, 8, 8), dtype=torch.float32, device=device)
        kernel = torch.ones((1, 1, 3, 3), dtype=torch.float32, device=device, requires_grad=True)
        loss = torch.nn.functional.conv2d(image, kernel).square().mean()
        loss.backward()
        matrix = torch.eye(3, dtype=torch.float32, device=device)
        vector = torch.tensor([1., 2., 3.], device=device)
        result = matrix @ vector
        if device == "cuda":
            torch.cuda.synchronize()
        if (not torch.allclose(kernel.grad, torch.full_like(kernel, 18.))
                or not torch.allclose(loss, torch.tensor(81., device=device))
                or not torch.equal(result, vector)):
            raise RuntimeError("Analytic tensor/gradient check disagrees")
        return {"status": "passed", "device": device, "convolution_loss": float(loss.detach()),
                "kernel_gradient": 18., "matrix_result": result.cpu().tolist(),
                "scope": "tiny_backend_execution_not_model_parity_or_capacity"}
    except Exception as exc:
        return {"status": "failed", "device": device, "reason": f"{type(exc).__name__}: {exc}"}


def audit_bearing_requirements(path: Path) -> dict[str, Any]:
    """Check the explicitly supplied pinned core requirements and Linux host."""
    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name

    data = path.read_bytes()
    if len(data) > 64 * 1024:
        raise ValueError("Core requirements exceed 64 KiB")
    required, errors = {}, []
    for line in data.decode().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        item = Requirement(line)
        pins = list(item.specifier)
        name = canonicalize_name(item.name)
        if (item.marker or item.url or item.extras or len(pins) != 1
                or pins[0].operator != "==" or "*" in pins[0].version or name in required):
            raise ValueError("Expected unique exact core package pins without markers/URLs")
        try:
            actual = importlib.metadata.version(item.name)
        except importlib.metadata.PackageNotFoundError:
            actual = None
        matches = actual is not None and item.specifier.contains(actual)
        required[name] = {"expected": pins[0].version, "actual": actual, "matches": matches}
        if not matches:
            errors.append(f"bearing_dependency_mismatch:{name}")
    if not required:
        raise ValueError("Core requirements are empty")
    if sys.version_info[:2] != (3, 11):
        errors.append("bearing_requires_python_3_11")
    if platform.system() != "Linux" or platform.machine() not in {"x86_64", "AMD64"}:
        errors.append("bearing_runpod_requires_linux_x86_64")
    # Metadata alone does not prove ABI compatibility of native extensions.
    modules = ("numpy", "scipy", "pandas", "cv2", "torch", "torchvision", "PIL", "yaml", "imgaug")
    for name in modules:
        try:
            __import__(name)
        except Exception as exc:
            errors.append(f"bearing_import_failed:{name}:{type(exc).__name__}")
    return {"requirements_sha256": hashlib.sha256(data).hexdigest(),
            "distributions": required, "errors": errors,
            "scope": "core_versions_and_imports_not_full_lock_or_security_audit"}


def evaluate_research_environment(
    *,
    require_cuda: bool = False,
    require_map_production: bool = False,
    require_onnx: bool = False,
    require_lighterglue: bool = False,
    exercise_torch: bool = False,
    bearing_reference_requirements: Path | None = None,
    module_finder: Callable[[str], Any] = importlib.util.find_spec,
    torch_loader: Callable[[], Any] | None = None,
    command_runner: Callable[..., Any] = subprocess.run,
    command_finder: Callable[[str], str | None] = shutil.which,
) -> dict[str, Any]:
    """Return a machine-readable readiness report without mutating the host."""

    dependencies = {
        name: _module_status(name, module_finder)
        for name in ("numpy", "cv2", "torch", "mlflow", "dvc", "rasterio", "pyproj", "onnx", "onnxruntime", "kornia")
    }
    torch_info: dict[str, Any] = {"available": False, "cuda_available": False, "cuda_device_count": 0, "version": None}
    torch = None
    if dependencies["torch"]["available"]:
        try:
            torch = torch_loader() if torch_loader is not None else __import__("torch")
            torch_info = {
                "available": True,
                "version": str(torch.__version__),
                "cuda_available": bool(torch.cuda.is_available()),
                "cuda_device_count": int(torch.cuda.device_count()) if torch.cuda.is_available() else 0,
                "cuda_device_name": str(torch.cuda.get_device_name(0)) if torch.cuda.is_available() and torch.cuda.device_count() else None,
            }
        except Exception as exc:
            torch_info["load_error"] = f"{type(exc).__name__}: {exc}"
    nvidia_smi: dict[str, Any] = {"available": command_finder("nvidia-smi") is not None}
    if nvidia_smi["available"]:
        try:
            result = command_runner(
                ["nvidia-smi", "--query-gpu=name,driver_version,memory.total", "--format=csv,noheader"],
                check=False, capture_output=True, text=True, timeout=10,
            )
            nvidia_smi.update({"returncode": int(result.returncode), "gpus": [line for line in result.stdout.splitlines() if line.strip()]})
        except Exception as exc:
            nvidia_smi["error"] = f"{type(exc).__name__}: {exc}"
    errors: list[str] = []
    if not torch_info["available"]:
        errors.append("pytorch_unavailable")
    if require_cuda and not torch_info["cuda_available"]:
        errors.append("cuda_pytorch_unavailable")
    if require_map_production:
        for name in ("rasterio", "pyproj"):
            if not dependencies[name]["available"]:
                errors.append(f"missing_map_production_dependency:{name}")
    if require_onnx:
        for name in ("onnx", "onnxruntime"):
            if not dependencies[name]["available"]:
                errors.append(f"missing_onnx_dependency:{name}")
    if require_lighterglue and not dependencies["kornia"]["available"]:
        errors.append("missing_lighterglue_dependency:kornia")
    bearing = None
    if bearing_reference_requirements is not None:
        try:
            bearing = audit_bearing_requirements(bearing_reference_requirements)
            errors.extend(bearing["errors"])
        except Exception as exc:
            errors.append(f"invalid_bearing_requirements:{type(exc).__name__}:{exc}")
    probe = {"status": "not_requested"}
    if exercise_torch or bearing_reference_requirements is not None:
        if torch is None or not torch_info["available"]:
            probe = {"status": "failed", "reason": "pytorch_unavailable"}
        else:
            probe = exercise_torch_backend(torch, "cuda" if require_cuda else "cpu")
        if probe["status"] != "passed":
            errors.append("torch_execution_probe_failed")
    return {
        "schema_version": RESEARCH_ENVIRONMENT_SCHEMA,
        "generated_at_utc": _utc_now(),
        "read_only": True,
        "status": "ready" if not errors else "not_ready",
        "requirements": {
            "cuda": require_cuda,
            "map_production": require_map_production,
            "onnx": require_onnx,
            "lighterglue": require_lighterglue,
            "torch_execution": exercise_torch or bearing_reference_requirements is not None,
            "bearing_reference": bearing_reference_requirements is not None,
        },
        "host": {"platform": platform.platform(), "python": sys.version.split()[0], "executable": sys.executable},
        "dependencies": dependencies,
        "pytorch": torch_info,
        "nvidia_smi": nvidia_smi,
        "torch_execution_probe": probe,
        "bearing_reference": bearing,
        "errors": errors,
        "next_action": (
            "Resolve the reported platform, dependency or backend errors on the intended research host, then rerun this check."
            if errors else
            "Environment is ready for a bounded teacher smoke run; keep all resulting artifacts research-only until promotion gates pass."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit a CUDA research environment for learned temporal teacher work.")
    parser.add_argument("--require-cuda", action="store_true")
    parser.add_argument("--require-map-production", action="store_true")
    parser.add_argument("--require-onnx", action="store_true")
    parser.add_argument("--require-lighterglue", action="store_true")
    parser.add_argument("--exercise-torch", action="store_true")
    parser.add_argument("--bearing-reference-requirements", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = evaluate_research_environment(
        require_cuda=args.require_cuda, require_map_production=args.require_map_production, require_onnx=args.require_onnx,
        require_lighterglue=args.require_lighterglue,
        exercise_torch=args.exercise_torch, bearing_reference_requirements=args.bearing_reference_requirements,
    )
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))
    if result["status"] != "ready":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
