"""Immutable dataset and model provenance records used by learned navigation."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
from pathlib import Path
from typing import Any


def _canonical_digest(payload: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class DatasetRelease:
    release_id: str
    version: str
    source_revision: str
    sources: tuple[str, ...]
    license_summary: str
    checksum_sha256: str
    proprietary: bool = False
    capture_session_ids: tuple[str, ...] = field(default_factory=tuple)
    calibration_ids: tuple[str, ...] = field(default_factory=tuple)
    map_release_ids: tuple[str, ...] = field(default_factory=tuple)
    extrinsic_ids: tuple[str, ...] = field(default_factory=tuple)
    frame_count: int = 0
    gnss_truth_policy: str = "offline_training_only"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "DatasetRelease":
        required = ("release_id", "version", "source_revision", "sources", "license_summary", "checksum_sha256")
        missing = [key for key in required if not raw.get(key)]
        if missing:
            raise ValueError(f"Dataset release is missing: {', '.join(missing)}")
        return cls(
            release_id=str(raw["release_id"]), version=str(raw["version"]),
            source_revision=str(raw["source_revision"]), sources=tuple(str(item) for item in raw["sources"]),
            license_summary=str(raw["license_summary"]), checksum_sha256=str(raw["checksum_sha256"]),
            proprietary=bool(raw.get("proprietary", False)),
            capture_session_ids=tuple(str(item) for item in raw.get("capture_session_ids", [])),
            calibration_ids=tuple(str(item) for item in raw.get("calibration_ids", [])),
            map_release_ids=tuple(str(item) for item in raw.get("map_release_ids", [])),
            extrinsic_ids=tuple(str(item) for item in raw.get("extrinsic_ids", [])),
            frame_count=int(raw.get("frame_count", 0)),
            gnss_truth_policy=str(raw.get("gnss_truth_policy", "offline_training_only")),
        )


@dataclass(frozen=True)
class ModelRelease:
    release_id: str
    profile_id: str
    semantic_version: str
    checkpoint_digest_sha256: str
    onnx_digest_sha256: str
    training_dataset_releases: tuple[str, ...]
    source_revision: str
    license_summary: str
    redistribution_allowed: bool
    calibration_ids: tuple[str, ...]
    artifact_paths: dict[str, str]
    teacher_lineage: tuple[str, ...] = field(default_factory=tuple)
    upstream_lock_digest_sha256: str | None = None
    artifact_interfaces: dict[str, dict[str, Any]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def digest(self) -> str:
        return _canonical_digest(self.to_dict())

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ModelRelease":
        required = (
            "release_id", "profile_id", "semantic_version", "checkpoint_digest_sha256", "onnx_digest_sha256",
            "training_dataset_releases", "source_revision", "license_summary", "calibration_ids", "artifact_paths",
        )
        missing = [key for key in required if raw.get(key) in (None, "", [])]
        if missing:
            raise ValueError(f"Model release is missing: {', '.join(missing)}")
        for field_name in ("checkpoint_digest_sha256", "onnx_digest_sha256"):
            if len(str(raw[field_name])) != 64:
                raise ValueError(f"Model release {field_name} must be a SHA-256 digest.")
        upstream_lock_digest = raw.get("upstream_lock_digest_sha256")
        if upstream_lock_digest is not None and len(str(upstream_lock_digest)) != 64:
            raise ValueError("Model release upstream_lock_digest_sha256 must be a SHA-256 digest.")
        interfaces = raw.get("artifact_interfaces", {})
        if not isinstance(interfaces, dict):
            raise ValueError("Model release artifact_interfaces must be an object when provided.")
        if any(not isinstance(contract, dict) for contract in interfaces.values()):
            raise ValueError("Model release artifact_interfaces entries must be objects.")
        return cls(
            release_id=str(raw["release_id"]), profile_id=str(raw["profile_id"]),
            semantic_version=str(raw["semantic_version"]), checkpoint_digest_sha256=str(raw["checkpoint_digest_sha256"]),
            onnx_digest_sha256=str(raw["onnx_digest_sha256"]),
            training_dataset_releases=tuple(str(item) for item in raw["training_dataset_releases"]),
            source_revision=str(raw["source_revision"]), license_summary=str(raw["license_summary"]),
            redistribution_allowed=bool(raw.get("redistribution_allowed", False)),
            calibration_ids=tuple(str(item) for item in raw["calibration_ids"]),
            artifact_paths={str(key): str(value) for key, value in raw["artifact_paths"].items()},
            teacher_lineage=tuple(str(item) for item in raw.get("teacher_lineage", [])),
            upstream_lock_digest_sha256=(
                str(raw["upstream_lock_digest_sha256"]) if raw.get("upstream_lock_digest_sha256") else None
            ),
            artifact_interfaces={
                str(role): dict(contract) for role, contract in interfaces.items()
            },
        )


def load_model_release(path: str | Path) -> ModelRelease:
    return ModelRelease.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
