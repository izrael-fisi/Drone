"""Verify learned model artifacts before a Pi runtime consumes them.

This module deliberately validates deployment evidence only. It does not infer a
pose or publish MAVLink output, and it does not assume a model is safe simply
because a release manifest names it.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any

from .releases import ModelRelease, load_model_release


REQUIRED_ONNX_ARTIFACTS = ("global_retriever", "local_features", "local_matcher", "temporal_student")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def artifact_aggregate_digest(entries: list[dict[str, str]]) -> str:
    canonical = json.dumps(entries, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


@dataclass(frozen=True)
class ModelArtifactValidation:
    status: str
    model_release_id: str
    artifact_root: str
    aggregate_digest_sha256: str | None
    artifacts: tuple[dict[str, Any], ...]
    errors: tuple[str, ...]

    @property
    def valid(self) -> bool:
        return self.status == "valid"

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "model_release_id": self.model_release_id,
            "artifact_root": self.artifact_root,
            "aggregate_digest_sha256": self.aggregate_digest_sha256,
            "artifacts": list(self.artifacts),
            "errors": list(self.errors),
        }


def validate_model_artifacts(
    model_release: ModelRelease,
    *,
    artifact_root: str | Path,
    inspect_onnx: bool = False,
    require_interfaces: bool = False,
) -> ModelArtifactValidation:
    """Validate the real files named by a ModelRelease.

    `onnx_digest_sha256` is the aggregate SHA-256 over ordered artifact role,
    relative path, and per-file digest records. This makes replacement of one
    model artifact detectable without relying on filenames.
    """

    root = Path(artifact_root).expanduser().resolve()
    errors: list[str] = []
    artifacts: list[dict[str, Any]] = []
    errors.extend(validate_artifact_interfaces(model_release, required=require_interfaces))
    if model_release.onnx_digest_sha256 == "0" * 64:
        errors.append("model_release_uses_placeholder_onnx_digest")

    for role in REQUIRED_ONNX_ARTIFACTS:
        location = model_release.artifact_paths.get(role)
        if not location:
            errors.append(f"missing_model_artifact_path:{role}")
            continue
        path = root / location
        record: dict[str, Any] = {"role": role, "path": location, "exists": path.is_file()}
        if not path.is_file():
            errors.append(f"missing_model_artifact:{role}")
            artifacts.append(record)
            continue
        digest = sha256_file(path)
        record["sha256"] = digest
        record["bytes"] = path.stat().st_size
        if path.suffix.lower() != ".onnx":
            errors.append(f"model_artifact_not_onnx:{role}")
        if inspect_onnx:
            try:
                record["onnx"] = _inspect_onnx(path)
                errors.extend(_validate_onnx_interface(role, record["onnx"], model_release.artifact_interfaces.get(role)))
            except RuntimeError as exc:
                errors.append(f"onnx_inspection_failed:{role}:{exc}")
        artifacts.append(record)

    aggregate: str | None = None
    if len(artifacts) == len(REQUIRED_ONNX_ARTIFACTS) and all("sha256" in item for item in artifacts):
        digest_entries = [{"role": item["role"], "path": item["path"], "sha256": item["sha256"]} for item in artifacts]
        aggregate = artifact_aggregate_digest(digest_entries)
        if aggregate != model_release.onnx_digest_sha256:
            errors.append("onnx_aggregate_digest_mismatch")

    return ModelArtifactValidation(
        status="valid" if not errors else "invalid",
        model_release_id=model_release.release_id,
        artifact_root=str(root),
        aggregate_digest_sha256=aggregate,
        artifacts=tuple(artifacts),
        errors=tuple(errors),
    )


def validate_artifact_interfaces(model_release: ModelRelease, *, required: bool = False) -> list[str]:
    """Validate logical I/O mappings independently of the optional ONNX Runtime."""

    interfaces = model_release.artifact_interfaces
    errors: list[str] = []
    if not interfaces:
        return ["model_release_missing_artifact_interfaces"] if required else []
    for role in REQUIRED_ONNX_ARTIFACTS:
        contract = interfaces.get(role)
        if not isinstance(contract, dict):
            errors.append(f"missing_artifact_interface:{role}")
            continue
        for direction in ("inputs", "outputs"):
            values = contract.get(direction)
            if not isinstance(values, dict) or not values:
                errors.append(f"invalid_artifact_interface:{role}:{direction}")
                continue
            for logical_name, spec in values.items():
                if not isinstance(logical_name, str) or not isinstance(spec, dict) or not isinstance(spec.get("name"), str):
                    errors.append(f"invalid_artifact_interface_binding:{role}:{direction}")
                    continue
                rank = spec.get("rank")
                if rank is not None and (not isinstance(rank, int) or rank < 0):
                    errors.append(f"invalid_artifact_interface_rank:{role}:{direction}:{logical_name}")
    return errors


def _validate_onnx_interface(role: str, observed: dict[str, Any], contract: dict[str, Any] | None) -> list[str]:
    if not contract:
        return []
    errors: list[str] = []
    for direction in ("inputs", "outputs"):
        expected = contract.get(direction, {})
        observed_by_name = {item["name"]: item for item in observed.get(direction, [])}
        for logical_name, spec in expected.items():
            actual = observed_by_name.get(spec["name"])
            if actual is None:
                errors.append(f"onnx_interface_missing_{direction[:-1]}:{role}:{logical_name}")
                continue
            if spec.get("rank") is not None and len(actual.get("shape", [])) != spec["rank"]:
                errors.append(f"onnx_interface_rank_mismatch:{role}:{logical_name}")
    return errors


def _inspect_onnx(path: Path) -> dict[str, Any]:
    try:
        import onnxruntime as ort
    except ImportError as exc:
        raise RuntimeError("onnxruntime_not_installed") from exc
    try:
        session = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
    except Exception as exc:  # ONNX Runtime exception types vary by version.
        raise RuntimeError(f"onnxruntime_load_failed:{exc}") from exc
    return {
        "inputs": [
            {"name": value.name, "shape": list(value.shape), "type": value.type}
            for value in session.get_inputs()
        ],
        "outputs": [
            {"name": value.name, "shape": list(value.shape), "type": value.type}
            for value in session.get_outputs()
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate real ONNX artifacts for a learned Pi model release.")
    parser.add_argument("--model-release", required=True, type=Path)
    parser.add_argument("--artifact-root", type=Path, help="Defaults to the model release directory.")
    parser.add_argument("--inspect-onnx", action="store_true", help="Load each ONNX artifact with ONNX Runtime.")
    parser.add_argument("--require-interfaces", action="store_true", help="Require complete logical I/O contracts for all model artifacts.")
    args = parser.parse_args()
    model_path = args.model_release.expanduser().resolve()
    result = validate_model_artifacts(
        load_model_release(model_path),
        artifact_root=args.artifact_root or model_path.parent,
        inspect_onnx=args.inspect_onnx,
        require_interfaces=args.require_interfaces,
    )
    print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
    if not result.valid:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
