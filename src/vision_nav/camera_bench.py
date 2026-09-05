"""Capture repeatable, read-only camera evidence before calibration or training."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from statistics import median
import time
from typing import Any

from vision_nav.camera_health import (
    DEFAULT_EDGE_DENSITY_MIN,
    DEFAULT_ENTROPY_MIN,
    DEFAULT_SATURATION_MAX,
    DEFAULT_SHARPNESS_MIN,
    analyze_frame_file,
)
from vision_nav.camera_ingress import CameraIngress, create_camera_ingress


CAMERA_BENCH_SCHEMA = "vision_nav_camera_bench_v1"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _summary(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"min": None, "median": None, "max": None}
    return {
        "min": round(min(values), 3),
        "median": round(float(median(values)), 3),
        "max": round(max(values), 3),
    }


def summarize_camera_bench(
    frame_records: list[dict[str, Any]],
    *,
    max_warning_fraction: float = 0.0,
) -> dict[str, Any]:
    """Summarize a focus/exposure bench set without selecting a CV matcher."""

    if not 0.0 <= max_warning_fraction <= 1.0:
        raise ValueError("max_warning_fraction must be between 0 and 1.")
    analyses = [record.get("analysis", {}) for record in frame_records if isinstance(record.get("analysis"), dict)]
    warning_records = [record for record in frame_records if record.get("analysis", {}).get("warnings")]
    sharpness = [
        float(analysis["frame_quality"]["sharpness_laplacian_var"])
        for analysis in analyses
        if isinstance(analysis.get("frame_quality", {}).get("sharpness_laplacian_var"), (int, float))
    ]
    edge_density = [
        float(analysis["edge_density_per_megapixel"])
        for analysis in analyses
        if isinstance(analysis.get("edge_density_per_megapixel"), (int, float))
    ]
    dark_fraction = [
        float(analysis["exposure"]["dark_pixel_fraction"])
        for analysis in analyses
        if isinstance(analysis.get("exposure", {}).get("dark_pixel_fraction"), (int, float))
    ]
    bright_fraction = [
        float(analysis["exposure"]["bright_pixel_fraction"])
        for analysis in analyses
        if isinstance(analysis.get("exposure", {}).get("bright_pixel_fraction"), (int, float))
    ]
    sensor_timestamp_count = sum(
        1
        for record in frame_records
        if isinstance(record.get("camera_metadata", {}).get("sensor_timestamp_ns"), int)
    )
    warning_fraction = len(warning_records) / len(frame_records) if frame_records else 1.0
    errors: list[str] = []
    warnings: list[str] = []
    if len(frame_records) < 3:
        errors.append("insufficient_camera_bench_frames")
    if len(analyses) != len(frame_records):
        errors.append("camera_bench_analysis_missing")
    if warning_fraction > max_warning_fraction:
        errors.append("camera_quality_warnings_exceed_limit")
    if sensor_timestamp_count != len(frame_records):
        warnings.append("camera_sensor_timestamp_not_available_for_every_frame")
    return {
        "schema_version": CAMERA_BENCH_SCHEMA,
        "status": "passed" if not errors else "failed",
        "frame_count": len(frame_records),
        "quality": {
            "warning_frame_count": len(warning_records),
            "warning_fraction": round(warning_fraction, 4),
            "sharpness_laplacian_var": _summary(sharpness),
            "edge_density_per_megapixel": _summary(edge_density),
            "dark_pixel_fraction": _summary(dark_fraction),
            "bright_pixel_fraction": _summary(bright_fraction),
        },
        "timing": {
            "sensor_timestamp_frame_count": sensor_timestamp_count,
            "sensor_timestamp_coverage": round(sensor_timestamp_count / len(frame_records), 4) if frame_records else 0.0,
        },
        "requirements": {"min_frame_count": 3, "max_warning_fraction": max_warning_fraction},
        "errors": errors,
        "warnings": warnings,
    }


def capture_camera_bench(
    output_dir: str | Path,
    *,
    count: int = 8,
    delay_s: float = 0.25,
    width: int = 1456,
    height: int = 1088,
    timeout_ms: int = 1000,
    camera_backend: str = "auto",
    sharpness_min: float = DEFAULT_SHARPNESS_MIN,
    entropy_min: float = DEFAULT_ENTROPY_MIN,
    edge_density_min: float = DEFAULT_EDGE_DENSITY_MIN,
    saturation_max: float = DEFAULT_SATURATION_MAX,
    max_warning_fraction: float = 0.0,
    camera_ingress: CameraIngress | None = None,
) -> dict[str, Any]:
    if count < 3:
        raise ValueError("Capture at least three frames for a camera bench report.")
    if delay_s < 0.0:
        raise ValueError("delay_s cannot be negative.")
    root = Path(output_dir).expanduser().resolve()
    if root.exists() and any(root.iterdir()):
        raise ValueError(f"Camera bench output directory must be empty: {root}")
    frames_dir = root / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    ingress = camera_ingress or create_camera_ingress(camera_backend, width=width, height=height, timeout_ms=timeout_ms)
    frame_records: list[dict[str, Any]] = []
    started_utc = _utc_now()
    try:
        for sequence in range(count):
            path = frames_dir / f"frame_{sequence:03d}.jpg"
            capture_started_ns = time.monotonic_ns()
            captured = ingress.capture(path)
            analysis = analyze_frame_file(
                captured.path,
                sharpness_min=sharpness_min,
                entropy_min=entropy_min,
                edge_density_min=edge_density_min,
                saturation_max=saturation_max,
            )
            frame_records.append(
                {
                    "sequence": sequence,
                    "captured_utc": _utc_now(),
                    "host_monotonic_ns": capture_started_ns,
                    "path": captured.path.relative_to(root).as_posix(),
                    "camera_metadata": captured.to_dict(),
                    "analysis": analysis,
                }
            )
            if sequence < count - 1 and delay_s:
                time.sleep(delay_s)
    finally:
        ingress.close()
    report = summarize_camera_bench(frame_records, max_warning_fraction=max_warning_fraction)
    report.update(
        {
            "captured_at_utc": started_utc,
            "completed_utc": _utc_now(),
            "camera": {
                "backend_requested": camera_backend,
                "width": width,
                "height": height,
                "timeout_ms": timeout_ms,
            },
            "thresholds": {
                "sharpness_min": sharpness_min,
                "entropy_min": entropy_min,
                "edge_density_min": edge_density_min,
                "saturation_max": saturation_max,
            },
            "frames": frame_records,
            "read_only": True,
        }
    )
    report_path = root / "camera_bench_report.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"output_dir": str(root), "report_path": str(report_path), "report": report}


def main() -> None:
    parser = argparse.ArgumentParser(description="Capture a multi-frame Pi camera focus/exposure bench report.")
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--count", type=int, default=8)
    parser.add_argument("--delay-s", type=float, default=0.25)
    parser.add_argument("--width", type=int, default=1456)
    parser.add_argument("--height", type=int, default=1088)
    parser.add_argument("--timeout-ms", type=int, default=1000)
    parser.add_argument("--camera-backend", choices=("auto", "picamera2", "rpicam_still"), default="auto")
    parser.add_argument("--sharpness-min", type=float, default=DEFAULT_SHARPNESS_MIN)
    parser.add_argument("--entropy-min", type=float, default=DEFAULT_ENTROPY_MIN)
    parser.add_argument("--edge-density-min", type=float, default=DEFAULT_EDGE_DENSITY_MIN)
    parser.add_argument("--saturation-max", type=float, default=DEFAULT_SATURATION_MAX)
    parser.add_argument("--max-warning-fraction", type=float, default=0.0)
    args = parser.parse_args()
    try:
        result = capture_camera_bench(
            args.output_dir,
            count=args.count,
            delay_s=args.delay_s,
            width=args.width,
            height=args.height,
            timeout_ms=args.timeout_ms,
            camera_backend=args.camera_backend,
            sharpness_min=args.sharpness_min,
            entropy_min=args.entropy_min,
            edge_density_min=args.edge_density_min,
            saturation_max=args.saturation_max,
            max_warning_fraction=args.max_warning_fraction,
        )
    except (RuntimeError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(result["report"], indent=2, sort_keys=True))
    if result["report"]["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
