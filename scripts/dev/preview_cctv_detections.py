"""Run a local browser preview with live YOLO11 detection boxes on recorded CCTV."""

import argparse
from base64 import b64encode
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
import json
import math
from pathlib import Path
import re
import subprocess
import sys
from threading import Lock
import time
from urllib.parse import parse_qs, urlsplit

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services/camera-gateway"))

from video_analytics.detectors import load_detector
from video_analytics.video import command, decoded, end


PAGE = Path(__file__).with_suffix(".html")
CONFIDENCE = .25
OBJECTS = {"person", "vehicle"}


@dataclass
class Clip:
    path: Path = field(repr=False)
    width: int
    height: int
    duration: float

    def info(self):
        return {"name": self.path.name, "duration": self.duration, "width": self.width, "height": self.height}


def probe(path, width):
    result = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                             "-show_entries", "stream=width,height:format=duration", "-of", "json", str(path)],
                            check=True, capture_output=True, text=True)
    info = json.loads(result.stdout)
    stream, = info["streams"]
    duration = float(info["format"]["duration"])
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError(f"Missing video duration for {path.name}")
    height = max(2, 2 * round(stream["height"] * width / stream["width"] / 2))
    return Clip(path, width, height, duration)


class PreviewServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port, clips, selected, detector, fps):
        self.clips = clips
        self.selected = selected
        self.detector = detector
        self.detector_lock = Lock()
        self.fps = fps
        super().__init__(("127.0.0.1", port), PreviewRequest)


class PreviewRequest(BaseHTTPRequestHandler):
    def reply(self, body, content_type):
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        expected_host = f"127.0.0.1:{self.server.server_port}"
        if self.headers.get("Host") != expected_host or self.headers.get("Sec-Fetch-Site") == "cross-site":
            self.send_error(403)
            return
        url = urlsplit(self.path)
        try:
            if url.path == "/":
                self.reply(PAGE.read_bytes(), "text/html; charset=utf-8")
            elif url.path == "/clips":
                profile = {"clips": [clip.info() for clip in self.server.clips], "selected": self.server.selected,
                           "fps": self.server.fps, "confidence": CONFIDENCE}
                self.reply(json.dumps(profile).encode(), "application/json")
            elif url.path == "/stream":
                self.stream(parse_qs(url.query))
            else:
                self.send_error(404)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def stream(self, params):
        try:
            index = int(params["clip"][0])
            start = float(params.get("start", ["0"])[0])
            if not 0 <= index < len(self.server.clips) or not math.isfinite(start):
                raise ValueError("Invalid playback position")
        except (KeyError, ValueError):
            self.send_error(400)
            return
        clip = self.server.clips[index]
        rate = self.server.fps
        start = min(max(0., start), max(0., clip.duration - 1 / rate))
        once = params.get("once") == ["1"]
        args = command(str(clip.path), clip.width, clip.height, rate, False, frame_limit=1 if once else None)
        seek = args.index("-i")
        args[seek:seek] = ["-ss", f"{start:.6f}"]
        process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
        try:
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            beginning = time.monotonic()
            count = 0
            for number, frame in enumerate(decoded(process, clip.width, clip.height)):
                with self.server.detector_lock:
                    detections = self.server.detector.infer(frame, CONFIDENCE)
                jpeg = BytesIO()
                Image.fromarray(frame).save(jpeg, format="JPEG", quality=84)
                frame_info = {
                    "time": round(start + number / rate, 3),
                    "jpeg": b64encode(jpeg.getvalue()).decode("ascii"),
                    "detections": [{"label": obj.label, "score": round(obj.score, 3), "box": obj.box}
                                   for obj in detections if obj.label in OBJECTS],
                }
                time.sleep(max(0., beginning + number / rate - time.monotonic()))
                self.wfile.write(json.dumps(frame_info, separators=(",", ":")).encode() + b"\n")
                count += 1
            if process.wait() or not count:
                self.wfile.write(b'{"error":"The selected video could not be decoded."}\n')
        finally:
            end(process)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("media", type=Path, help="A video or a folder of local recordings")
    parser.add_argument("--model", type=Path, required=True, help="YOLO11 ONNX export")
    parser.add_argument("--sha256", required=True, help="SHA-256 of the approved export")
    parser.add_argument("--provider", default="CPUExecutionProvider")
    parser.add_argument("--initial", help="Initial recording filename or camera ID")
    parser.add_argument("--port", type=int, default=0, help="Local HTTP port; choose an available port by default")
    parser.add_argument("--fps", type=int, default=4, choices=range(1, 13), metavar="1-12")
    args = parser.parse_args()

    try:
        if not re.fullmatch(r"[a-f0-9]{64}", args.sha256) or not 0 <= args.port <= 65535:
            raise ValueError("Supply a valid model digest and local port.")
        source = args.media.resolve(strict=True)
        files = sorted(path for path in source.iterdir()
                       if path.is_file() and path.suffix.lower() in {".avi", ".mp4", ".mkv", ".mov"}) if source.is_dir() else [source]
        if not files:
            raise ValueError("No supported video recordings were found.")
        selected = 0
        if args.initial:
            matching = [index for index, path in enumerate(files)
                        if args.initial == path.name or args.initial in path.name.split(".")]
            if len(matching) != 1:
                raise ValueError("The initial recording must match one filename or camera ID.")
            selected = matching[0]
        clips = [probe(path, 960) for path in files]
        detector = load_detector({"kind": "yolo11", "path": str(args.model.resolve(strict=True)),
                                  "sha256": args.sha256, "provider": args.provider})
        with PreviewServer(args.port, clips, selected, detector, args.fps) as server:
            print(f"http://127.0.0.1:{server.server_port}/", flush=True)
            server.serve_forever()
    except KeyboardInterrupt:
        pass
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
