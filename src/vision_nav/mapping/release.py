"""Immutable visual-map release contract and startup compatibility checks."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
from typing import Any

from ..contracts import CompatibilityReport


@dataclass(frozen=True)
class MapRelease:
    map_id: str
    version: str
    epoch_utc: str
    crs: str
    local_origin_enu: tuple[float, float, float]
    gsd_m_per_px: float
    tile_index_uri: str
    descriptor_dimension: int
    descriptor_model_version: str
    sparse_model_version: str
    dense_model_version: str
    compatible_calibration_ids: tuple[str, ...]
    checksums_sha256: dict[str, str]

    @property
    def release_id(self) -> str:
        return f"{self.map_id}@{self.version}"

    def digest(self) -> str:
        payload = json.dumps(asdict(self), sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "MapRelease":
        required = (
            "map_id", "version", "epoch_utc", "crs", "local_origin_enu", "gsd_m_per_px",
            "tile_index_uri", "descriptor_dimension", "descriptor_model_version",
            "sparse_model_version", "dense_model_version", "compatible_calibration_ids", "checksums_sha256",
        )
        missing = [key for key in required if key not in raw]
        if missing:
            raise ValueError(f"Map release is missing required fields: {', '.join(missing)}")
        origin = tuple(float(value) for value in raw["local_origin_enu"])
        if len(origin) != 3:
            raise ValueError("Map release local_origin_enu must contain [east, north, up] metres.")
        if float(raw["gsd_m_per_px"]) <= 0.0 or int(raw["descriptor_dimension"]) <= 0:
            raise ValueError("Map release GSD and descriptor dimension must be positive.")
        checksums = raw["checksums_sha256"]
        if not isinstance(checksums, dict) or not checksums:
            raise ValueError("Map release requires non-empty checksums_sha256.")
        return cls(
            map_id=str(raw["map_id"]),
            version=str(raw["version"]),
            epoch_utc=str(raw["epoch_utc"]),
            crs=str(raw["crs"]),
            local_origin_enu=origin,
            gsd_m_per_px=float(raw["gsd_m_per_px"]),
            tile_index_uri=str(raw["tile_index_uri"]),
            descriptor_dimension=int(raw["descriptor_dimension"]),
            descriptor_model_version=str(raw["descriptor_model_version"]),
            sparse_model_version=str(raw["sparse_model_version"]),
            dense_model_version=str(raw["dense_model_version"]),
            compatible_calibration_ids=tuple(str(value) for value in raw["compatible_calibration_ids"]),
            checksums_sha256={str(key): str(value) for key, value in checksums.items()},
        )


def load_map_release(path: str | Path) -> MapRelease:
    return MapRelease.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


def check_map_release_compatibility(
    release: MapRelease,
    *,
    calibration_id: str | None,
    descriptor_dimension: int,
    descriptor_model_version: str,
    sparse_model_version: str,
    dense_model_version: str,
) -> CompatibilityReport:
    """Reject mixed feature spaces, unknown calibration, and incomplete map releases."""

    reasons: list[str] = []
    if not calibration_id:
        reasons.append("missing_camera_calibration_id")
    elif calibration_id not in release.compatible_calibration_ids:
        reasons.append("camera_calibration_not_compatible_with_map")
    if descriptor_dimension != release.descriptor_dimension:
        reasons.append("global_descriptor_dimension_mismatch")
    if descriptor_model_version != release.descriptor_model_version:
        reasons.append("global_descriptor_model_version_mismatch")
    if sparse_model_version != release.sparse_model_version:
        reasons.append("sparse_model_version_mismatch")
    if dense_model_version != release.dense_model_version:
        reasons.append("dense_model_version_mismatch")
    return CompatibilityReport(
        compatible=not reasons,
        reasons=tuple(reasons),
        camera_calibration_id=calibration_id,
        map_version=release.release_id,
        model_version=release.descriptor_model_version,
    )


def parse_checksum(value: str) -> tuple[str, str]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("Checksums must use relative/path=sha256.")
    path, digest = value.split("=", 1)
    if not path or len(digest) != 64:
        raise argparse.ArgumentTypeError("Checksums must use a path and a 64-character SHA-256 digest.")
    return path, digest


def main() -> None:
    parser = argparse.ArgumentParser(description="Create an immutable visual-map release manifest.")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--map-id", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--epoch-utc", required=True)
    parser.add_argument("--crs", required=True)
    parser.add_argument("--origin-enu", required=True, nargs=3, type=float, metavar=("E", "N", "U"))
    parser.add_argument("--gsd-m-per-px", required=True, type=float)
    parser.add_argument("--tile-index-uri", required=True)
    parser.add_argument("--descriptor-dimension", required=True, type=int)
    parser.add_argument("--descriptor-model-version", required=True)
    parser.add_argument("--sparse-model-version", required=True)
    parser.add_argument("--dense-model-version", required=True)
    parser.add_argument("--calibration-id", action="append", required=True)
    parser.add_argument("--checksum", action="append", type=parse_checksum, required=True)
    args = parser.parse_args()
    release = MapRelease(
        map_id=args.map_id,
        version=args.version,
        epoch_utc=args.epoch_utc,
        crs=args.crs,
        local_origin_enu=tuple(args.origin_enu),
        gsd_m_per_px=args.gsd_m_per_px,
        tile_index_uri=args.tile_index_uri,
        descriptor_dimension=args.descriptor_dimension,
        descriptor_model_version=args.descriptor_model_version,
        sparse_model_version=args.sparse_model_version,
        dense_model_version=args.dense_model_version,
        compatible_calibration_ids=tuple(args.calibration_id),
        checksums_sha256=dict(args.checksum),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(release.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"release_id": release.release_id, "digest": release.digest(), "output": str(args.output)}))


if __name__ == "__main__":
    main()
