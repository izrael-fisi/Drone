"""Evaluate exported ONNX stages against immutable cloud-reference tensors."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from .onnx_runtime import LearnedModelRunner
from .releases import ModelRelease, load_model_release


ONNX_PARITY_CASES_SCHEMA = "vision_nav_onnx_parity_cases_v1"
ONNX_PARITY_REPORT_SCHEMA = "vision_nav_onnx_parity_report_v1"


class LogicalRunner(Protocol):
    def run(self, role: str, logical_inputs: dict[str, np.ndarray]) -> dict[str, np.ndarray]: ...


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _load_npz(path: Path) -> dict[str, np.ndarray]:
    if not path.is_file():
        raise ValueError(f"Parity tensor file does not exist: {path}")
    try:
        with np.load(path, allow_pickle=False) as archive:
            return {name: np.asarray(archive[name]) for name in archive.files}
    except (OSError, ValueError) as exc:
        raise ValueError(f"Could not load parity tensor file: {path}") from exc


def _resolve_case_path(case_root: Path, value: Any, field: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError(f"Parity case requires {field}.")
    return (case_root / value).resolve()


def _output_metrics(reference: np.ndarray, actual: np.ndarray, *, atol: float, rtol: float) -> dict[str, Any]:
    reference = np.asarray(reference)
    actual = np.asarray(actual)
    if reference.shape != actual.shape:
        return {
            "status": "failed",
            "reason": "shape_mismatch",
            "reference_shape": list(reference.shape),
            "actual_shape": list(actual.shape),
        }
    if not np.isfinite(reference).all() or not np.isfinite(actual).all():
        return {
            "status": "failed",
            "reason": "nonfinite_tensor",
            "reference_shape": list(reference.shape),
            "actual_shape": list(actual.shape),
        }
    absolute = np.abs(actual.astype(np.float64) - reference.astype(np.float64))
    denominator = np.maximum(np.abs(reference.astype(np.float64)), np.finfo(np.float64).eps)
    relative = absolute / denominator
    passed = bool(np.allclose(actual, reference, atol=atol, rtol=rtol, equal_nan=False))
    return {
        "status": "passed" if passed else "failed",
        "reference_shape": list(reference.shape),
        "actual_shape": list(actual.shape),
        "max_absolute_error": float(absolute.max()) if absolute.size else 0.0,
        "mean_absolute_error": float(absolute.mean()) if absolute.size else 0.0,
        "max_relative_error": float(relative.max()) if relative.size else 0.0,
    }


def evaluate_onnx_parity_cases(
    cases_path: str | Path,
    *,
    model_release: ModelRelease,
    runner: LogicalRunner,
    atol: float = 1e-4,
    rtol: float = 1e-3,
) -> dict[str, Any]:
    """Run ONNX stages against training-side reference tensors recorded in NPZ."""

    if atol < 0.0 or rtol < 0.0:
        raise ValueError("atol and rtol must be non-negative.")
    source = Path(cases_path).expanduser().resolve()
    if not source.is_file():
        raise ValueError(f"Parity cases manifest does not exist: {source}")
    raw = json.loads(source.read_text(encoding="utf-8"))
    if raw.get("schema_version") != ONNX_PARITY_CASES_SCHEMA:
        raise ValueError(f"Parity cases must use {ONNX_PARITY_CASES_SCHEMA}.")
    if raw.get("model_release_id") != model_release.release_id:
        raise ValueError("Parity cases model_release_id does not match the exported model release.")
    cases = raw.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("Parity cases manifest requires a non-empty cases array.")

    results: list[dict[str, Any]] = []
    seen_case_ids: set[str] = set()
    for raw_case in cases:
        if not isinstance(raw_case, dict):
            raise ValueError("Parity cases must be objects.")
        case_id = raw_case.get("case_id")
        role = raw_case.get("role")
        if not isinstance(case_id, str) or not case_id or case_id in seen_case_ids:
            raise ValueError("Parity cases require unique non-empty case_id values.")
        if not isinstance(role, str) or role not in model_release.artifact_interfaces:
            raise ValueError(f"Parity case {case_id} names an unsupported model role: {role!r}")
        seen_case_ids.add(case_id)
        inputs_path = _resolve_case_path(source.parent, raw_case.get("inputs_npz"), "inputs_npz")
        reference_path = _resolve_case_path(source.parent, raw_case.get("reference_outputs_npz"), "reference_outputs_npz")
        inputs = _load_npz(inputs_path)
        references = _load_npz(reference_path)
        actuals = runner.run(role, inputs)
        expected_outputs = set(model_release.artifact_interfaces[role]["outputs"])
        if set(references) != expected_outputs:
            raise ValueError(
                f"Parity case {case_id} reference outputs do not match the {role} release contract: "
                f"expected {sorted(expected_outputs)}, got {sorted(references)}."
            )
        if set(actuals) != expected_outputs:
            raise ValueError(f"Runner outputs do not match the {role} release contract for parity case {case_id}.")
        outputs = {
            name: _output_metrics(references[name], actuals[name], atol=atol, rtol=rtol)
            for name in sorted(expected_outputs)
        }
        failures = [name for name, metrics in outputs.items() if metrics["status"] != "passed"]
        results.append(
            {
                "case_id": case_id,
                "role": role,
                "inputs_npz": str(inputs_path),
                "reference_outputs_npz": str(reference_path),
                "status": "passed" if not failures else "failed",
                "failed_outputs": failures,
                "outputs": outputs,
            }
        )
    failures = [result["case_id"] for result in results if result["status"] != "passed"]
    return {
        "schema_version": ONNX_PARITY_REPORT_SCHEMA,
        "generated_at_utc": _utc_now(),
        "status": "passed" if not failures else "failed",
        "model_release_id": model_release.release_id,
        "profile_id": model_release.profile_id,
        "case_manifest": str(source),
        "thresholds": {"atol": atol, "rtol": rtol},
        "case_count": len(results),
        "failed_case_ids": failures,
        "cases": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare ONNX Pi artifacts with cloud-side reference tensors.")
    parser.add_argument("--model-release", required=True, type=Path)
    parser.add_argument("--artifact-root", type=Path, help="Defaults to the model release directory.")
    parser.add_argument("--cases", required=True, type=Path, help="JSON manifest referencing logical NPZ inputs and outputs.")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--provider", default="onnxruntime_cpu")
    parser.add_argument("--atol", type=float, default=1e-4)
    parser.add_argument("--rtol", type=float, default=1e-3)
    args = parser.parse_args()
    model_path = args.model_release.expanduser().resolve()
    model = load_model_release(model_path)
    try:
        runner = LearnedModelRunner(
            model,
            artifact_root=args.artifact_root or model_path.parent,
            provider=args.provider,
        )
        report = evaluate_onnx_parity_cases(
            args.cases,
            model_release=model,
            runner=runner,
            atol=args.atol,
            rtol=args.rtol,
        )
    except (RuntimeError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    if report["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
