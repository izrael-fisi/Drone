"""Measured camera-to-body extrinsics contract for camera/PX4 attitude fusion."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
from typing import Any

import yaml


EXTRINSICS_SCHEMA = "vision_nav_camera_to_body_v1"
BODY_FRAME_ID = "body_frd"
CAMERA_OPTICAL_FRAME_ID = "down_camera_optical"
TRANSFORM_DIRECTION = "body_frd_T_camera_optical"


@dataclass(frozen=True)
class CameraToBodyExtrinsics:
    path: Path
    extrinsic_id: str | None
    status: str
    frame_id: str
    child_frame_id: str
    camera_calibration_id: str | None
    translation_m: tuple[float, float, float]
    rotation_quat_xyzw: tuple[float, float, float, float]
    transform_direction: str | None
    method: str | None
    uncertainty: dict[str, float]

    def to_log_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "extrinsic_id": self.extrinsic_id,
            "status": self.status,
            "frame_id": self.frame_id,
            "child_frame_id": self.child_frame_id,
            "camera_calibration_id": self.camera_calibration_id,
            "translation_m": {"x": self.translation_m[0], "y": self.translation_m[1], "z": self.translation_m[2]},
            "rotation_quat_xyzw": {
                "x": self.rotation_quat_xyzw[0], "y": self.rotation_quat_xyzw[1],
                "z": self.rotation_quat_xyzw[2], "w": self.rotation_quat_xyzw[3],
            },
            "transform_direction": self.transform_direction,
            "method": self.method,
            "uncertainty": self.uncertainty,
        }


def _finite_vector(raw: dict[str, Any], keys: tuple[str, ...], label: str) -> tuple[float, ...]:
    if not isinstance(raw, dict) or any(type(raw.get(key)) not in (int, float) for key in keys):
        raise ValueError(f"{label} must define finite numeric {', '.join(keys)}.")
    try:
        values = tuple(float(raw[key]) for key in keys)
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"{label} must define {', '.join(keys)}.") from exc
    if not all(math.isfinite(value) for value in values):
        raise ValueError(f"{label} must contain finite values.")
    return values


def load_camera_to_body(path: str | Path) -> CameraToBodyExtrinsics:
    source = Path(path).expanduser().resolve()
    raw = yaml.safe_load(source.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"Camera-to-body extrinsics is not a YAML mapping: {source}")
    status = str(raw.get("extrinsic_status", "unknown"))
    frame_id = str(raw.get("frame_id", "body"))
    child_frame_id = str(raw.get("child_frame_id", "down_camera"))
    if status == "measured":
        if raw.get("schema_version") != EXTRINSICS_SCHEMA:
            raise ValueError(f"Measured camera-to-body extrinsics must use {EXTRINSICS_SCHEMA}.")
        if frame_id != BODY_FRAME_ID or child_frame_id != CAMERA_OPTICAL_FRAME_ID:
            raise ValueError(
                "Measured camera-to-body extrinsics must declare frame_id=body_frd and "
                "child_frame_id=down_camera_optical."
            )
        if raw.get("transform_direction") != TRANSFORM_DIRECTION:
            raise ValueError(
                "Measured camera-to-body extrinsics must declare "
                f"transform_direction={TRANSFORM_DIRECTION}."
            )
        for key in ("extrinsic_id", "camera_calibration_id", "measurement_method"):
            if not isinstance(raw.get(key), str) or not raw[key].strip():
                raise ValueError(f"Measured camera-to-body extrinsics require {key}.")
        method = raw.get("measurement_method")
        uncertainty = raw.get("uncertainty")
        if not isinstance(method, str) or not method.strip():
            raise ValueError("Measured camera-to-body extrinsics require measurement_method.")
        if not isinstance(uncertainty, dict) or not all(
            type(uncertainty.get(key)) in (int, float)
            and math.isfinite(float(uncertainty[key])) and float(uncertainty[key]) >= 0.0
            for key in ("translation_sigma_m", "rotation_sigma_deg")
        ):
            raise ValueError("Measured camera-to-body extrinsics require finite non-negative translation_sigma_m and rotation_sigma_deg.")
    translation = _finite_vector(raw.get("translation_m", {}), ("x", "y", "z"), "translation_m")
    rotation = _finite_vector(raw.get("rotation_quat_xyzw", {}), ("x", "y", "z", "w"), "rotation_quat_xyzw")
    norm = math.sqrt(sum(value * value for value in rotation))
    if not math.isclose(norm, 1.0, abs_tol=1e-3):
        raise ValueError(f"rotation_quat_xyzw must be unit length; got {norm:.6f}.")
    return CameraToBodyExtrinsics(
        path=source,
        extrinsic_id=str(raw["extrinsic_id"]) if raw.get("extrinsic_id") else None,
        status=status,
        frame_id=frame_id,
        child_frame_id=child_frame_id,
        camera_calibration_id=str(raw["camera_calibration_id"]) if raw.get("camera_calibration_id") else None,
        translation_m=translation,
        rotation_quat_xyzw=rotation,
        transform_direction=str(raw["transform_direction"]) if raw.get("transform_direction") else None,
        method=str(raw["measurement_method"]) if raw.get("measurement_method") else None,
        uncertainty={str(key): float(value) for key, value in (raw.get("uncertainty") or {}).items()},
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def create_extrinsics_document(
    *,
    extrinsic_id: str,
    camera_calibration_id: str,
    translation_m: tuple[float, float, float],
    rotation_quat_xyzw: tuple[float, float, float, float],
    measurement_method: str,
    translation_sigma_m: float,
    rotation_sigma_deg: float,
    mount_id: str | None = None,
) -> dict[str, Any]:
    if any(not isinstance(value, str) or not value.strip()
           for value in (extrinsic_id, camera_calibration_id, measurement_method)):
        raise ValueError("extrinsic_id, camera_calibration_id, and measurement_method are required.")
    if any(type(value) not in (int, float) or not math.isfinite(value) or value < 0
           for value in (translation_sigma_m, rotation_sigma_deg)):
        raise ValueError("Extrinsics uncertainty must be finite, non-negative numbers.")
    if len(translation_m) != 3 or len(rotation_quat_xyzw) != 4:
        raise ValueError("Extrinsics require three translation and four quaternion components.")
    if any(type(value) not in (int, float) or not math.isfinite(value)
           for value in (*translation_m, *rotation_quat_xyzw)):
        raise ValueError("Extrinsics transform must contain finite numbers.")
    rotation_norm = math.sqrt(sum(value * value for value in rotation_quat_xyzw))
    if not math.isclose(rotation_norm, 1.0, abs_tol=1e-3):
        raise ValueError("rotation_quat_xyzw must be unit length.")
    return {
        "schema_version": EXTRINSICS_SCHEMA,
        "extrinsic_id": extrinsic_id,
        "extrinsic_status": "measured",
        "measured_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "frame_id": BODY_FRAME_ID,
        "child_frame_id": CAMERA_OPTICAL_FRAME_ID,
        # Translation is the camera optical origin expressed in PX4 body FRD.
        # Quaternion rotates camera-optical vectors into PX4 body FRD.
        "transform_direction": TRANSFORM_DIRECTION,
        "coordinate_convention": {
            "body_frd": {"x": "forward", "y": "right", "z": "down"},
            "camera_optical": {"x": "right", "y": "down", "z": "forward"},
            "quaternion": "xyzw; rotates camera_optical vectors into body_frd",
        },
        "camera_calibration_id": camera_calibration_id,
        "mount_id": mount_id,
        "measurement_method": measurement_method,
        "translation_m": {"x": translation_m[0], "y": translation_m[1], "z": translation_m[2]},
        "rotation_quat_xyzw": {
            "x": rotation_quat_xyzw[0], "y": rotation_quat_xyzw[1],
            "z": rotation_quat_xyzw[2], "w": rotation_quat_xyzw[3],
        },
        "uncertainty": {"translation_sigma_m": translation_sigma_m, "rotation_sigma_deg": rotation_sigma_deg},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a measured camera-to-body extrinsics release from documented mount measurements.")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--extrinsic-id", required=True)
    parser.add_argument("--camera-calibration-id", required=True)
    parser.add_argument("--translation-m", required=True, nargs=3, type=float, metavar=("X", "Y", "Z"))
    parser.add_argument("--rotation-quat-xyzw", required=True, nargs=4, type=float, metavar=("X", "Y", "Z", "W"))
    parser.add_argument("--measurement-method", required=True, help="For example: measured_mount_jig_v1.")
    parser.add_argument("--translation-sigma-m", required=True, type=float)
    parser.add_argument("--rotation-sigma-deg", required=True, type=float)
    parser.add_argument("--mount-id")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output already exists; create a new calibration release instead of overwriting it.")
    try:
        document = create_extrinsics_document(
            extrinsic_id=args.extrinsic_id,
            camera_calibration_id=args.camera_calibration_id,
            translation_m=tuple(args.translation_m),
            rotation_quat_xyzw=tuple(args.rotation_quat_xyzw),
            measurement_method=args.measurement_method,
            translation_sigma_m=args.translation_sigma_m,
            rotation_sigma_deg=args.rotation_sigma_deg,
            mount_id=args.mount_id,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        stream.write(yaml.safe_dump(document, sort_keys=False))
    print(json.dumps({"status": "measured", "extrinsic_id": args.extrinsic_id, "output": str(args.output)}))


if __name__ == "__main__":
    main()
