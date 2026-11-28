"""Read and cache event clips from a configured Axis edge recorder."""

from contextlib import suppress
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
import time
from urllib.parse import unquote, urlencode, urlsplit
from urllib.request import HTTPDigestAuthHandler, HTTPPasswordMgrWithDefaultRealm, ProxyHandler, build_opener
from xml.etree import ElementTree as ET

from onvif import CameraError, NoRedirect

MAX_BYTES = 24 * 1024 * 1024
CACHE_SECONDS = 3600
HOLD_SECONDS = 7 * 86400
MAX_CLIPS = 40
RECORDING_READY_SECONDS = 10


def utc(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_utc(value):
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return timestamp.timestamp() if timestamp.tzinfo else None
    except (ValueError, OverflowError, AttributeError):
        return None


class AxisArchive:
    def __init__(self, directory, origins, ffmpeg, onvif=None):
        self.directory = directory
        self.origins = {urlsplit(origin).hostname: origin.rstrip("/") for origin in origins}
        self.ffmpeg = ffmpeg
        self.onvif = onvif
        self.ffprobe = shutil.which("ffprobe")
        self.lock = threading.Lock()
        self.fetch_lock = threading.Lock()
        self.failures = {}

    def retrieve(self, stream, occurred_at, rate_kbps=None):
        if not stream:
            return {"state": "unsupported"}
        source = urlsplit(stream["url"])
        origin = self.origins.get(source.hostname)
        use_onvif = not origin and self.onvif and self.onvif.supports(stream)
        if not origin and not use_onvif:
            return {"state": "unsupported"}
        event = occurred_at / 1000
        if event > time.time() - RECORDING_READY_SECONDS:
            return {"state": "pending"}
        key = stream["key"]
        clip = sha256(f"{key}:{occurred_at}".encode()).hexdigest()[:32]
        base = self.directory / clip
        catalog = base.with_suffix(".json")
        pending = base.with_suffix(".pending")
        with self.lock, suppress(OSError, ValueError):
            entry = self.entry(key, clip)
            if entry:
                return {"state": "available", **entry}
        if not self.fetch_lock.acquire(blocking=False):
            return {"state": "pending"}
        try:
            with self.lock:
                self.directory.mkdir(mode=0o700, exist_ok=True)
                self.expire()
                entry = self.entry(key, clip)
                if entry:
                    return {"state": "available", **entry}
                previous = self.failures.get(clip)
                if previous and time.monotonic() < previous[0]:
                    return {"state": previous[1]}
                if len(list(self.directory.glob("*.mp4"))) >= MAX_CLIPS:
                    return {"state": "error", "message": "Gateway clip cache is full."}
            candidate = self.fetch_onvif(stream, event, pending) if use_onvif else self.fetch(origin, source, event, pending, rate_kbps)
            if candidate is None:
                self.remember_failure(clip, "missing")
                return {"state": "missing"}
            record = {"clipId": clip, "cameraKey": key, "occurredAt": occurred_at, "eventOffsetSeconds": candidate[0],
                      "durationSeconds": candidate[1], "expiresAt": int(time.time()) + CACHE_SECONDS}
            with self.lock:
                os.replace(pending, base.with_suffix(".mp4"))
                self.save_catalog(catalog, record)
            return {"state": "available", **record}
        except (OSError, ValueError, subprocess.SubprocessError, ET.ParseError, CameraError):
            self.remember_failure(clip, "error")
            return {"state": "error", "message": "Could not retrieve video from the configured recorder."}
        finally:
            try:
                pending.unlink(missing_ok=True)
            finally:
                self.fetch_lock.release()

    def fetch_onvif(self, stream, event, destination):
        with tempfile.TemporaryDirectory(dir=self.directory) as scratch:
            incoming, output = Path(scratch) / "input.mkv", Path(scratch) / "clip.mp4"
            times = self.onvif.capture(stream, event, incoming)
            if not times or not incoming.exists() or incoming.stat().st_size > MAX_BYTES:
                return None
            start, stop = times
            if not event - 10 <= start <= event < stop <= event + 9:
                return None
            return self.encode(incoming, output, destination, stop - start, event - start)

    def remember_failure(self, clip, status):
        now = time.monotonic()
        self.failures = {key: entry for key, entry in self.failures.items() if entry[0] > now}
        if len(self.failures) < 256:
            self.failures[clip] = (now + 15, status)

    def fetch(self, origin, source, event, destination, rate_kbps=None):
        credentials = HTTPPasswordMgrWithDefaultRealm()
        if source.username:
            credentials.add_password(None, origin, unquote(source.username), unquote(source.password or ""))
        opener = build_opener(ProxyHandler({}), NoRedirect(), HTTPDigestAuthHandler(credentials))

        def request(path, query):
            return opener.open(origin + path + "?" + urlencode(query), timeout=5)

        with request("/axis-cgi/record/list.cgi", {"starttime": utc(event - 1), "stoptime": utc(event + 1), "maxnumberofresults": 20}) as response:
            catalog = response.read(262_145)
        if len(catalog) > 262_144 or b"<!DOCTYPE" in catalog.upper():
            raise CameraError("Invalid Axis recording list.")
        for entry in ET.fromstring(catalog).findall(".//recording"):
            start = parse_utc(entry.get("starttime"))
            stop = parse_utc(entry.get("stoptime")) or time.time()
            record, disk = entry.get("recordingid"), entry.get("diskid")
            if start is None or not start <= event < stop or not record or not disk:
                continue
            left, right = max(start, event - 8), min(stop, event + 8)
            with tempfile.TemporaryDirectory(dir=self.directory) as scratch:
                incoming, output = Path(scratch) / "input.mkv", Path(scratch) / "clip.mp4"
                with request("/axis-cgi/record/export/exportrecording.cgi", {
                    "schemaversion": 1, "recordingid": record, "diskid": disk, "exportformat": "matroska",
                    "starttime": utc(left), "stoptime": utc(right),
                }) as response, incoming.open("wb") as dest:
                    remaining = MAX_BYTES
                    started = time.monotonic()
                    bytes_per_second = rate_kbps * 1000 / 8 if rate_kbps else None
                    deadline = started + (MAX_BYTES / bytes_per_second if bytes_per_second else 0) + 12
                    while True:
                        chunk = response.read(min(65536, remaining + 1))
                        if not chunk:
                            break
                        remaining -= len(chunk)
                        if remaining < 0 or time.monotonic() > deadline:
                            raise CameraError("Axis recording exceeded the clip limits.")
                        dest.write(chunk)
                        if bytes_per_second:
                            time.sleep(max(0, started + (MAX_BYTES - remaining) / bytes_per_second - time.monotonic()))
                return self.encode(incoming, output, destination, right - left, event - left)
        return None

    def encode(self, incoming, output, destination, max_duration, offset):
        subprocess.run([
            self.ffmpeg, "-v", "error", "-nostdin", "-protocol_whitelist", "file", "-i", str(incoming),
            "-map", "0:v:0", "-an", "-t", str(max_duration), "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-preset", "veryfast", "-threads", "2", "-movflags", "+faststart", str(output),
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15, check=True)
        if not self.ffprobe:
            raise CameraError("FFprobe is required to verify recorder clips.")
        info = subprocess.run([self.ffprobe, "-v", "error", "-show_entries", "format=duration", "-of",
                               "default=noprint_wrappers=1:nokey=1", str(output)], capture_output=True, text=True, timeout=3, check=True)
        duration = min(float(info.stdout.strip()), max_duration)
        if not 0 <= offset < duration or output.stat().st_size > MAX_BYTES:
            return None
        output.chmod(0o600)
        os.replace(output, destination)
        return offset, duration

    def expire(self):
        if not self.directory.exists():
            return
        for catalog in self.directory.glob("*.json"):
            with_file = json.loads(catalog.read_text())
            if max(with_file["expiresAt"], with_file.get("heldUntil", 0)) <= time.time():
                catalog.with_suffix(".mp4").unlink(missing_ok=True)
                catalog.unlink(missing_ok=True)
        for video in self.directory.glob("*.mp4"):
            if not video.with_suffix(".json").exists():
                video.unlink(missing_ok=True)
        if not self.fetch_lock.locked():
            for pending in self.directory.glob("*.pending"):
                pending.unlink(missing_ok=True)

    def save_catalog(self, catalog, value):
        with tempfile.NamedTemporaryFile(mode="w", dir=self.directory, delete=False) as out:
            temporary = Path(out.name)
            try:
                json.dump(value, out)
                out.flush()
                out.close()
                os.replace(temporary, catalog)
            finally:
                temporary.unlink(missing_ok=True)

    def media(self, key, clip_id):
        with self.lock:
            entry = self.entry(key, clip_id)
            return (self.directory / (clip_id + ".mp4")).read_bytes() if entry else None

    def entry(self, key, clip_id):
        if not re.fullmatch(r"[0-9a-f]{32}", clip_id):
            return None
        catalog = self.directory / (clip_id + ".json")
        if not catalog.exists() or not catalog.with_suffix(".mp4").exists():
            return None
        entry = json.loads(catalog.read_text())
        if entry["cameraKey"] != key or max(entry["expiresAt"], entry.get("heldUntil", 0)) <= time.time():
            return None
        return entry

    def hold(self, key, clip_id, operator):
        with self.lock:
            entry = self.entry(key, clip_id)
            if entry is None:
                raise CameraError("The clip is unavailable or expired.")
            if not entry.get("heldUntil"):
                content = self.directory / (clip_id + ".mp4")
                entry.update(heldUntil=int(time.time()) + HOLD_SECONDS, heldBy=operator, heldAt=int(time.time()),
                             sha256=sha256(content.read_bytes()).hexdigest())
                self.save_catalog(content.with_suffix(".json"), entry)
            return entry
