from __future__ import annotations

import argparse
import subprocess
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Capture one frame using Raspberry Pi camera tools.")
    parser.add_argument("--output", required=True, help="Output image path.")
    parser.add_argument("--width", type=int, default=1456)
    parser.add_argument("--height", type=int, default=1088)
    parser.add_argument("--timeout-ms", type=int, default=1000)
    return parser.parse_args()


def command_exists(command: str) -> bool:
    return subprocess.run(["which", command], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0


def build_capture_command(output: Path, width: int, height: int, timeout_ms: int) -> list[str]:
    if command_exists("rpicam-still"):
        return [
            "rpicam-still",
            "--width",
            str(width),
            "--height",
            str(height),
            "--timeout",
            str(timeout_ms),
            "-o",
            str(output),
        ]
    if command_exists("libcamera-still"):
        return [
            "libcamera-still",
            "--width",
            str(width),
            "--height",
            str(height),
            "--timeout",
            str(timeout_ms),
            "-o",
            str(output),
        ]
    raise RuntimeError("Neither rpicam-still nor libcamera-still was found.")


def capture_frame(output: Path, width: int = 1456, height: int = 1088, timeout_ms: int = 1000) -> Path:
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(build_capture_command(output, width, height, timeout_ms), check=True)
    return output


def main() -> None:
    args = parse_args()
    try:
        output = capture_frame(Path(args.output), args.width, args.height, args.timeout_ms)
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc
    print(output)


if __name__ == "__main__":
    main()
