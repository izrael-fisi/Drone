"""FFmpeg decode with bounded latest-frame buffering for live RTSP."""

from contextlib import suppress
from datetime import datetime, timezone
import subprocess
from threading import Condition, Thread
import time
from urllib.parse import urlsplit

import numpy as np


def command(source, width, height, fps, live, frame_limit=None):
    args = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-filter_threads", "1"]
    if live:
        url = urlsplit(source)
        if url.scheme != "rtsp" or not url.hostname or url.fragment or not 1 <= (url.port or 554) <= 65535:
            raise ValueError("Supply an approved local RTSP camera or NVR stream.")
        args += ["-rtsp_transport", "tcp", "-timeout", "7000000", "-analyzeduration", "1000000",
                 "-probesize", "131072", "-protocol_whitelist", "tcp,rtp,udp"]
    else:
        args += ["-protocol_whitelist", "file"]
    limit = ["-frames:v", str(frame_limit)] if frame_limit is not None else []
    return [*args, "-threads", "2", "-i", source, "-map", "0:v:0", "-an", "-sn", "-dn", *limit,
            "-vf", f"fps={fps},scale={width}:{height}", "-pix_fmt", "rgb24", "-threads", "1", "-f", "rawvideo", "pipe:1"]


def check_video(source, dimensions):
    try:
        run = subprocess.run(command(source, *dimensions, True, frame_limit=1), stdin=subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=12)
    except subprocess.TimeoutExpired:
        raise ValueError("Timed out decoding the approved RTSP source.") from None
    width, height, _ = dimensions
    if run.returncode or len(run.stdout) != width * height * 3:
        raise ValueError("Could not decode a frame from the approved RTSP source.")


def decoded(process, width, height):
    size = width * height * 3
    while (data := process.stdout.read(size)) and len(data) == size:
        yield np.frombuffer(data, dtype=np.uint8).reshape(height, width, 3)


def end(process):
    if process.poll() is None:
        with suppress(ProcessLookupError):
            process.terminate()
        try:
            process.wait(2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
    process.stdout.close()


class LiveVideo:
    def __init__(self, source, width, height, fps, stop, enabled):
        self.condition, self.latest = Condition(), None
        self.last_frame_at = None
        self.ready = False
        self.args, self.stop, self.enabled = command(source, width, height, fps, True), stop, enabled
        self.width, self.height = width, height
        self.thread = Thread(target=self.run, name="cctv-rtsp", daemon=True)

    def run(self):
        offset = 0
        while not self.stop.is_set():
            if not self.enabled():
                self.stop.wait(.5)
                continue
            self.last_frame_at = time.monotonic()
            self.ready = False
            process = subprocess.Popen(self.args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

            def watch():
                while process.poll() is None:
                    if self.stop.wait(.5) or not self.enabled() or time.monotonic() - self.last_frame_at > 12:
                        with suppress(ProcessLookupError):
                            process.kill()
                        return

            watchdog = Thread(target=watch, name="cctv-rtsp-watch", daemon=True)
            watchdog.start()
            try:
                for frame in decoded(process, self.width, self.height):
                    self.last_frame_at = time.monotonic()
                    self.ready = True
                    offset += 1
                    with self.condition:
                        self.latest = (offset, self.last_frame_at, time.time(), frame)
                        self.condition.notify_all()
            finally:
                end(process)
                watchdog.join()
                self.ready = False
                with self.condition:
                    self.latest = None
            self.stop.wait(2)

    def get(self, after):
        with self.condition:
            self.condition.wait_for(lambda: self.stop.is_set() or self.latest is not None and self.latest[0] > after, timeout=1)
            return self.latest if self.latest is not None and self.latest[0] > after else None


def replay(source, width, height, fps):
    process = subprocess.Popen(command(source, width, height, fps, False), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        for index, frame in enumerate(decoded(process, width, height)):
            yield index / fps, frame
        if process.wait():
            raise RuntimeError("FFmpeg could not finish reading the replay.")
    finally:
        end(process)


def utc(stamp):
    return datetime.fromtimestamp(stamp, timezone.utc).isoformat()
