"""Capture a traceable chessboard session for measured Pi camera calibration."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time
from typing import Any

from vision_nav.camera_ingress import create_camera_ingress
from vision_nav.training_capture import sha256_file


CALIBRATION_CAPTURE_SCHEMA = "vision_nav_calibration_capture_v1"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def capture_calibration_session(
    output_dir: str | Path,
    *,
    count: int,
    delay_s: float,
    width: int,
    height: int,
    timeout_ms: int,
    camera_backend: str,
    board_cols: int | None = None,
    board_rows: int | None = None,
    square_size_m: float | None = None,
) -> dict[str, Any]:
    root = Path(output_dir).expanduser().resolve()
    if root.exists() and any(root.iterdir()):
        raise ValueError(f"Calibration output directory must be empty: {root}")
    if count < 5:
        raise ValueError("Capture at least 5 calibration images; 20 or more is recommended.")
    root.mkdir(parents=True, exist_ok=True)
    frames: list[dict[str, Any]] = []
    ingress = create_camera_ingress(camera_backend, width=width, height=height, timeout_ms=timeout_ms)
    try:
        for sequence in range(count):
            started = time.monotonic_ns()
            path = root / f"calib_{sequence + 1:03d}.jpg"
            captured = ingress.capture(path)
            record = captured.to_dict()
            record.update(
                {
                    "sequence": sequence,
                    "captured_utc": _utc_now(),
                    "host_monotonic_ns": started,
                    "path": path.name,
                    "sha256": sha256_file(path),
                }
            )
            frames.append(record)
            if sequence < count - 1 and delay_s:
                time.sleep(delay_s)
    finally:
        ingress.close()
    manifest = {
        "schema_version": CALIBRATION_CAPTURE_SCHEMA,
        "status": "captured",
        "captured_at_utc": _utc_now(),
        "camera": {"backend_requested": camera_backend, "width": width, "height": height, "timeout_ms": timeout_ms},
        "chessboard": {"cols": board_cols, "rows": board_rows, "square_size_m": square_size_m},
        "frame_count": len(frames),
        "frames": frames,
    }
    (root / "capture_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"output_dir": str(root), "manifest": manifest}


def main() -> None:
    parser = argparse.ArgumentParser(description="Capture a traceable Pi chessboard session for camera calibration.")
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--count", type=int, default=20)
    parser.add_argument("--delay-s", type=float, default=2.0)
    parser.add_argument("--width", type=int, default=1456)
    parser.add_argument("--height", type=int, default=1088)
    parser.add_argument("--timeout-ms", type=int, default=1000)
    parser.add_argument("--camera-backend", choices=("auto", "picamera2", "rpicam_still"), default="auto")
    parser.add_argument("--cols", type=int)
    parser.add_argument("--rows", type=int)
    parser.add_argument("--square-size-m", type=float)
    args = parser.parse_args()
    try:
        summary = capture_calibration_session(
            args.output_dir,
            count=args.count,
            delay_s=args.delay_s,
            width=args.width,
            height=args.height,
            timeout_ms=args.timeout_ms,
            camera_backend=args.camera_backend,
            board_cols=args.cols,
            board_rows=args.rows,
            square_size_m=args.square_size_m,
        )
    except (RuntimeError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
