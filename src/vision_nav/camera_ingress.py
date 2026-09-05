"""Camera frame ingress with metadata and quality evidence for Pi training capture."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field, fields, replace
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from vision_nav.capture_frame import capture_frame
from vision_nav.capture_timing import sample_host_clocks


@dataclass(frozen=True)
class CapturedFrame:
    path: Path
    backend: str
    sensor_timestamp_ns: int | None = None
    exposure_time_us: int | None = None
    analogue_gain: float | None = None
    crop: tuple[int, int, int, int] | None = None
    quality: dict[str, Any] = field(default_factory=dict)
    sensor_clock_domain: str | None = None
    sensor_timestamp_reference: str | None = None
    host_clock_samples: list[dict[str, Any]] = field(default_factory=list)
    # Optional owned, read-only BGR buffer. The path is reserved, not yet written.
    bgr: np.ndarray | None = field(default=None, repr=False, compare=False)

    def to_dict(self) -> dict[str, Any]:
        raw = {item.name: deepcopy(getattr(self, item.name)) for item in fields(self) if item.name != "bgr"}
        raw["path"] = str(self.path)
        return raw


class CameraIngress(Protocol):
    backend: str

    def capture(self, output: Path) -> CapturedFrame: ...

    def close(self) -> None: ...


def frame_quality_metrics(path: str | Path) -> dict[str, Any]:
    """Return non-authoritative image health metrics without rejecting a frame."""

    try:
        import cv2
    except ImportError:
        return {"status": "unavailable", "reason": "opencv_not_installed"}
    image = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if image is None:
        return {"status": "unavailable", "reason": "image_decode_failed"}
    return image_quality_metrics(image)


def image_quality_metrics(image: np.ndarray) -> dict[str, Any]:
    """Measure a BGR image without a disk encode/decode round trip."""
    import cv2

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    laplacian_variance = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    mean_luma = float(np.mean(gray))
    saturated_fraction = float(np.mean((gray <= 3) | (gray >= 252)))
    return {
        "status": "measured",
        "width": int(image.shape[1]),
        "height": int(image.shape[0]),
        "laplacian_variance": round(laplacian_variance, 4),
        "mean_luma": round(mean_luma, 3),
        "saturated_fraction": round(saturated_fraction, 5),
    }


class RpiCamStillIngress:
    backend = "rpicam_still"

    def __init__(self, *, width: int, height: int, timeout_ms: int) -> None:
        self.width = width
        self.height = height
        self.timeout_ms = timeout_ms

    def capture(self, output: Path) -> CapturedFrame:
        path = capture_frame(output, self.width, self.height, self.timeout_ms)
        return CapturedFrame(path=path, backend=self.backend, quality=frame_quality_metrics(path))

    def close(self) -> None:
        return None


class Picamera2Ingress:
    backend = "picamera2"

    def __init__(self, *, width: int, height: int) -> None:
        try:
            from picamera2 import Picamera2
        except ImportError as exc:
            raise RuntimeError("Picamera2 is unavailable. Install python3-picamera2 or use rpicam_still.") from exc
        self.width = width
        self.height = height
        self._camera = Picamera2()
        configuration = self._camera.create_still_configuration(main={"size": (width, height), "format": "RGB888"})
        self._camera.configure(configuration)
        self._camera.start()

    def capture(self, output: Path) -> CapturedFrame:
        try:
            import cv2
        except ImportError as exc:
            raise RuntimeError("OpenCV is required to persist Picamera2 capture frames.") from exc
        output.parent.mkdir(parents=True, exist_ok=True)
        frame = self._capture_memory(output, measure_quality=False)
        if not cv2.imwrite(str(output), frame.bgr):
            raise RuntimeError(f"Could not write captured frame: {output}")
        return replace(frame, bgr=None, quality=frame_quality_metrics(output))

    def capture_memory(self, output: Path) -> CapturedFrame:
        """Copy the camera-owned buffer before releasing its request; do not write it."""
        return self._capture_memory(output, measure_quality=True)

    def _capture_memory(self, output: Path, *, measure_quality: bool) -> CapturedFrame:
        clock_before = sample_host_clocks()
        request = self._camera.capture_request()
        try:
            metadata = request.get_metadata() or {}
            # RGB888 in libcamera is BGR byte order, as expected by OpenCV.
            bgr = request.make_array("main").copy()
        finally:
            request.release()
        clock_after = sample_host_clocks()
        bgr.setflags(write=False)
        crop_value = metadata.get("ScalerCrop")
        crop = tuple(int(value) for value in crop_value) if crop_value and len(crop_value) == 4 else None
        return CapturedFrame(
            path=output,
            backend=self.backend,
            sensor_timestamp_ns=_as_optional_int(metadata.get("SensorTimestamp")),
            exposure_time_us=_as_optional_int(metadata.get("ExposureTime")),
            analogue_gain=_as_optional_float(metadata.get("AnalogueGain")),
            crop=crop,
            quality=image_quality_metrics(bgr) if measure_quality else {},
            sensor_clock_domain="linux_boottime_ns",
            sensor_timestamp_reference="first_row_exposure_start",
            host_clock_samples=[clock_before, clock_after],
            bgr=bgr,
        )

    def close(self) -> None:
        self._camera.stop()
        self._camera.close()


def create_camera_ingress(
    backend: str,
    *,
    width: int,
    height: int,
    timeout_ms: int,
) -> CameraIngress:
    normalized = backend.strip().lower()
    if normalized not in {"auto", "picamera2", "rpicam_still"}:
        raise ValueError("camera backend must be auto, picamera2, or rpicam_still.")
    if normalized in {"auto", "picamera2"}:
        try:
            return Picamera2Ingress(width=width, height=height)
        except RuntimeError:
            if normalized == "picamera2":
                raise
    return RpiCamStillIngress(width=width, height=height, timeout_ms=timeout_ms)


def _as_optional_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _as_optional_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
