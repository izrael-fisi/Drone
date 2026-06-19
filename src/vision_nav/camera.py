from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import yaml


@dataclass(frozen=True)
class CameraCalibration:
    path: Path
    camera_name: str
    image_width: int
    image_height: int
    camera_matrix: np.ndarray
    distortion_coefficients: np.ndarray
    distortion_model: str

    @property
    def image_size(self) -> tuple[int, int]:
        return self.image_width, self.image_height

    def to_log_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "camera_name": self.camera_name,
            "image_width": self.image_width,
            "image_height": self.image_height,
            "distortion_model": self.distortion_model,
        }


def _matrix_from_yaml(value: dict[str, Any], rows: int, cols: int, field_name: str) -> np.ndarray:
    data = value.get("data")
    if data is None:
        raise ValueError(f"Missing {field_name}.data")
    array = np.asarray(data, dtype=np.float64)
    expected = rows * cols
    if array.size != expected:
        raise ValueError(f"{field_name}.data must contain {expected} values, got {array.size}")
    return array.reshape(rows, cols)


def load_camera_calibration(path: str | Path) -> CameraCalibration:
    calibration_path = Path(path).expanduser()
    with calibration_path.open("r", encoding="utf-8") as stream:
        raw = yaml.safe_load(stream)
    if not isinstance(raw, dict):
        raise ValueError(f"Camera calibration is not a YAML mapping: {calibration_path}")

    camera_matrix = _matrix_from_yaml(raw["camera_matrix"], 3, 3, "camera_matrix")
    distortion = _matrix_from_yaml(
        raw["distortion_coefficients"],
        int(raw["distortion_coefficients"].get("rows", 1)),
        int(raw["distortion_coefficients"].get("cols", 5)),
        "distortion_coefficients",
    ).reshape(-1)

    return CameraCalibration(
        path=calibration_path,
        camera_name=str(raw.get("camera_name", "camera")),
        image_width=int(raw["image_width"]),
        image_height=int(raw["image_height"]),
        camera_matrix=camera_matrix,
        distortion_coefficients=distortion,
        distortion_model=str(raw.get("distortion_model", "plumb_bob")),
    )


def validate_image_size(image: np.ndarray, calibration: CameraCalibration) -> None:
    height, width = image.shape[:2]
    expected_width, expected_height = calibration.image_size
    if (width, height) != (expected_width, expected_height):
        raise ValueError(
            "Frame size does not match calibration: "
            f"frame={width}x{height}, calibration={expected_width}x{expected_height}. "
            "Calibrate at the same resolution used for navigation."
        )
