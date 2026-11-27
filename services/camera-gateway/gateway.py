"""Proxigo site camera gateway. Requires FFmpeg with libx264."""

import argparse
from contextlib import suppress
from hashlib import sha256
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from ipaddress import ip_address, ip_network
import json
import os
from pathlib import Path
import re
from secrets import compare_digest, token_hex
import shutil
import signal
import sqlite3
import subprocess
import tempfile
import threading
import time
from urllib.parse import parse_qs, quote, urlsplit, urlunsplit
from urllib.request import ProxyHandler, Request, build_opener

from onvif import CameraError, NoRedirect, OnvifDevice
from analytics_contract import LINK_CHECK, worker_tokens
from axis_archive import AxisArchive
from site_activity import SiteActivity, valid_event_device
from video_integrity import FILTER, inspect_sample
from evidence_package import build_package
from inventory_import import change_import
from gateway_state import StateLock
from site_telemetry import SiteTelemetry, read_device_tokens
from site_collaboration import Conflict, SiteCollaboration
from site_accounts import manages_site
from site_monitoring import SiteMonitoring, camera_for
from site_assets import SiteAssets
from milestone_connector import MilestoneConnector
from genetec_connector import GenetecConnector
from onvif_archive import OnvifArchive, profile_g_recordings
from commissioning_runs import CommissioningRuns
from native_camera_events import enroll_native
from ai_jobs import AIJobs, JOB_ID
from device_degradation import DeviceDegradation
from sensor_fusion import SensorFusion

DEVICE_ID = re.compile(r"[A-Za-z0-9_-]{1,96}")
STREAM = re.compile(r"/streams/([0-9a-f]{48})/(index\.m3u8|segment-\d+\.ts)")
GRID_STREAM = re.compile(r"/streams/([0-9a-f]{48})/grid/(index\.m3u8|segment-\d+\.ts)")
VIDEO_STATUS = re.compile(r"/streams/([0-9a-f]{48})/status\.json")
RECORDING = re.compile(r"/streams/([0-9a-f]{48})/recording\.json")
CLIP = re.compile(r"/streams/([0-9a-f]{48})/clips/([0-9a-f]{32})\.mp4")
EVENTS = re.compile(r"/streams/([0-9a-f]{48})/events\.json")
PUSH = re.compile(r"/api/cameras/([A-Za-z0-9_-]{1,96})/events")
ANALYTICS = re.compile(r"/api/cameras/([A-Za-z0-9_-]{1,96})/analytics")
VIEW = re.compile(r"/view/([0-9a-f]{48})/(index\.m3u8|segment-\d+\.ts)")
AI_JOB_ROUTE = re.compile(r"/api/site/ai-jobs/(" + JOB_ID.pattern + ")")
MAX_CAMERAS = 128
START_TIMEOUT = 17
IDLE_TIMEOUT = 35
DEFAULT_ORIGINS = tuple(f"http://{host}:{port}" for host in ("127.0.0.1", "localhost") for port in (1420, 4173, 4180))


def private_write(path, value):
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as draft:
        temporary = Path(draft.name)
        try:
            draft.write(value)
            draft.flush()
            os.fsync(draft.fileno())
            draft.close()
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


class Relay:
    def __init__(self, ffmpeg, url, grid=False):
        self.directory = tempfile.TemporaryDirectory(prefix="proxigo-hls-")
        self.path = Path(self.directory.name)
        self.last_access = time.monotonic()
        self.deadline = self.last_access + START_TIMEOUT
        try:
            width, height, fps = (640, 360, 10) if grid else (1280, 720, 15)
            bitrate, peak, buffer = ("350k", "500k", "1000k") if grid else ("1200k", "1600k", "3200k")
            self.process = subprocess.Popen([
                ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin",
                "-rtsp_transport", "tcp", "-timeout", "5000000", "-allowed_media_types", "video",
                "-protocol_whitelist", "tcp,rtp,udp", "-i", url, "-map", "0:v:0", "-an",
                "-vf", f"scale=w='min({width},iw)':h='min({height},ih)':force_original_aspect_ratio=decrease:force_divisible_by=2,fps={fps}",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast", "-tune", "zerolatency",
                "-threads", "2", "-b:v", bitrate, "-maxrate", peak, "-bufsize", buffer,
                "-g", str(fps * 2), "-keyint_min", str(fps * 2), "-sc_threshold", "0",
                "-f", "hls", "-hls_time", "2", "-hls_list_size", "5", "-hls_start_number_source", "epoch_us",
                "-hls_flags", "delete_segments+independent_segments+omit_endlist+temp_file",
                "-hls_segment_filename", "segment-%d.ts", "index.m3u8",
            ], cwd=self.path, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            self.directory.cleanup()
            raise CameraError("The gateway could not start FFmpeg.") from None

    def await_playlist(self):
        while self.process.poll() is None and time.monotonic() < self.deadline:
            if (self.path / "index.m3u8").is_file():
                return
            time.sleep(0.1)
        raise CameraError("No video arrived. Check the RTSP address, camera credentials, and video profile.")

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        self.directory.cleanup()


class CameraGateway:
    def __init__(self, state, ffmpeg, networks, max_streams, probe_interval=90, axis_recorders=(), telemetry_upstream=None,
                 telemetry_secret="", telemetry_devices=None, telemetry_reader="", telemetry_receiver="", site_config=None,
                 identity_proxy_secret="", require_camera_session=False, profile_g=None, capture_python=None, analytics_workers=None,
                 commissioning_worker=None, site_intelligence=None):
        state.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.registry = state / "cameras.json"
        self.cameras = json.loads(self.registry.read_text(encoding="utf-8")) if self.registry.exists() else {}
        self.token_path = state / "management-token"
        if not self.token_path.exists():
            private_write(self.token_path, token_hex(32) + "\n")
        self.token = self.token_path.read_text(encoding="utf-8").strip()
        if not re.fullmatch(r"[0-9a-f]{64}", self.token):
            raise ValueError("The management token file must contain a generated 64-character hexadecimal token.")
        self.ffmpeg = ffmpeg
        self.networks = networks
        self.limit = max_streams
        self.active = {}
        self.pending = {}
        self.video_checks = {}
        self.integrity = {}
        self.probe_interval = probe_interval
        self.lock = threading.Lock()
        self.stopping = threading.Event()
        for origin in axis_recorders:
            url = urlsplit(self.validate_url(origin, ("http", "https")))
            if url.path not in ("", "/") or url.query:
                raise CameraError("Configure Axis recorders as HTTP(S) origins without a path or query.")
        onvif_archive = OnvifArchive(profile_g, self.validate_url, capture_python) if profile_g else None
        self.archive = AxisArchive(state / "clips", axis_recorders, ffmpeg, onvif_archive)
        self.activity = SiteActivity(state / "activity.sqlite3")
        self.telemetry_devices = telemetry_devices or {}
        self.analytics_workers = analytics_workers or {}
        reserved = {self.token, telemetry_reader, telemetry_receiver, telemetry_secret, identity_proxy_secret, *self.telemetry_devices.values()}
        if any(token in reserved for tokens in self.analytics_workers.values() for token in tokens.values()):
            raise CameraError("Analytics worker tokens must be distinct from gateway and adapter tokens.")
        self.telemetry_reader = telemetry_reader
        self.telemetry_receiver = telemetry_receiver
        self.collaboration = SiteCollaboration(self.activity, site_config, identity_proxy_secret) if site_config is not None else None
        self.monitor = None
        self.assets = None
        self.recording_recovery = False
        self.commissioning_webhook = None
        self.milestone = None
        self.genetec = None
        self.commissioning = None
        if self.collaboration:
            accounts = self.collaboration.accounts
            self.monitor = self.collaboration.monitor = SiteMonitoring(self.activity, accounts.organization["id"], accounts.site_id, self.collaboration.time_zone)
            self.assets = SiteAssets(self.activity)
            self.commissioning = CommissioningRuns(self, commissioning_worker)
        self.telemetry = SiteTelemetry(self.activity, telemetry_upstream, telemetry_secret, self.monitor)
        self.require_camera_session = require_camera_session
        self.state = state
        self.export_lock = threading.Lock()
        self.ai_jobs = self.device_degradation = self.sensor_fusion = None
        if site_intelligence is not None:
            if not isinstance(site_intelligence, dict) or type(site_intelligence.get("deviceDegradation", False)) is not bool:
                raise CameraError("Provide valid private site intelligence settings.")
            if "ai" in site_intelligence:
                settings = site_intelligence["ai"]
                if not isinstance(settings, dict):
                    raise CameraError("Provide an AI settings object.")
                self.ai_jobs = self.collaboration.ai_jobs = AIJobs(self, settings)
            if site_intelligence.get("deviceDegradation"):
                self.device_degradation = DeviceDegradation(self)
            if "gateRadars" in site_intelligence:
                self.sensor_fusion = self.monitor.sensor_fusion = SensorFusion(self, site_intelligence["gateRadars"])


    def operations(self, actor):
        with self.activity.connect() as db:
            db.execute("BEGIN")
            readiness = [{"boundaryId": boundary, "state": state, "cameras": json.loads(cameras), "updatedAt": updated, "incidentId": incident}
                         for boundary, state, cameras, updated, incident in db.execute(
                             "SELECT boundary,state,cameras,updated,incident FROM boundary_coverage ORDER BY boundary")
                         if actor["cameras"] == "*" or json.loads(cameras) and set(json.loads(cameras)) <= set(actor["cameras"])]
            if not manages_site(actor):
                return {"coverage": readiness}
            telemetry = db.execute("SELECT max(received_at) FROM telemetry").fetchone()[0]
            queued = db.execute("SELECT count(*),min(json_extract(payload,'$.occurredAt')) FROM alert_deliveries").fetchone()
            video = db.execute("SELECT state,count(*) FROM recovery_jobs GROUP BY state").fetchall()
            caught_up = self.monitor.cases.progress(db)
        try:
            backup = json.loads((self.state / "backup-status.json").read_text())
            backup_at = backup.get("createdAt")
        except (OSError, ValueError, TypeError):
            backup_at = None
        return {"coverage": readiness, "lastTelemetryAt": telemetry, "notificationBacklog": queued[0], "oldestNotificationAt": queued[1],
                "recorderJobs": dict(video), "indexCurrent": caught_up, "lastBackupAt": backup_at}

    def validate_url(self, raw, schemes, allow_credentials=False):
        requirement = "Enter an IP address and a valid " + " or ".join(schemes).upper() + " URL."
        if not isinstance(raw, str) or len(raw) > 4096 or re.search(r"[\x00-\x20\x7f\\]", raw):
            raise CameraError(requirement)
        try:
            url = urlsplit(raw)
            address = ip_address(url.hostname or "")
            if url.port == 0 or url.scheme not in schemes or url.fragment or "%" in str(address):
                raise ValueError()
        except ValueError:
            raise CameraError(requirement) from None
        if (url.username is not None or url.password is not None) and not allow_credentials:
            raise CameraError("Enter the camera username and password in the separate fields.")
        if address.is_unspecified or address.is_multicast or not any(address in network for network in self.networks):
            raise CameraError("The camera address is outside the gateway's allowed source networks.")
        return raw

    def source(self, payload):
        mode = payload.get("type")
        username, password = payload.get("username", ""), payload.get("password", "")
        if not all(isinstance(value, str) and len(value) <= 256 and not re.search(r"[\x00-\x1f\x7f]", value) for value in (username, password)):
            raise CameraError("Enter valid camera credentials.")
        if password and not username:
            raise CameraError("A camera username is required with a password.")
        address = payload.get("address")
        if mode == "onvif":
            address = self.validate_url(address, ("http", "https"))
            return OnvifDevice(address, username, password, self.validate_url), username, password
        if mode == "rtsp":
            return self.validate_url(address, ("rtsp",)), username, password
        raise CameraError("Select ONVIF or RTSP.")

    def profiles(self, payload):
        source, _, _ = self.source(payload)
        if not isinstance(source, OnvifDevice):
            raise CameraError("Profile selection is available for ONVIF cameras.")
        return source.media()[2]

    def connect(self, payload):
        device = payload.get("deviceId")
        if not isinstance(device, str) or not DEVICE_ID.fullmatch(device):
            raise CameraError("Invalid Global View device ID.")
        source, username, password = self.source(payload)
        profile = payload.get("profile", "")
        if not isinstance(profile, str) or len(profile) > 200:
            raise CameraError("Invalid ONVIF profile.")
        url = source.stream(profile) if isinstance(source, OnvifDevice) else source
        grid_value = payload.get("gridProfile" if isinstance(source, OnvifDevice) else "gridAddress", "")
        if not isinstance(grid_value, str) or len(grid_value) > (200 if isinstance(source, OnvifDevice) else 2048):
            raise CameraError("Invalid secondary camera stream.")
        grid_url = (source.stream(grid_value) if isinstance(source, OnvifDevice) else self.validate_url(grid_value, ("rtsp",))) if grid_value else None
        if grid_url and (grid_url == url or urlsplit(grid_url).hostname != urlsplit(url).hostname):
            raise CameraError("Use a different stream on the same camera for the live wall.")
        clock = source.clock() if isinstance(source, OnvifDevice) else None

        def authenticated(address):
            if not username or not address:
                return address
            parts = urlsplit(address)
            credentials = quote(username, safe="") + ":" + quote(password, safe="")
            return urlunsplit(parts._replace(netloc=f"{credentials}@{parts.netloc}"))

        url, grid_url = authenticated(url), authenticated(grid_url)

        candidate = None
        grid_candidate = None
        previous = None
        previous_grid = None
        grid_pending = "grid:" + device
        with self.lock:
            if self.stopping.is_set() or device in self.pending:
                raise CameraError("Camera connection is already in progress. Try again shortly.")
            if len(self.cameras) >= MAX_CAMERAS and device not in self.cameras:
                raise CameraError("This gateway has reached its camera registry limit.")
            self.check_capacity()
            candidate = self.pending[device] = Relay(self.ffmpeg, url)
        try:
            candidate.await_playlist()
            if grid_url:
                with self.lock:
                    self.check_capacity()
                    grid_candidate = self.pending[grid_pending] = Relay(self.ffmpeg, grid_url, grid=True)
                grid_candidate.await_playlist()
            with self.lock:
                if self.stopping.is_set():
                    raise CameraError("The gateway is shutting down.")
                key = self.cameras[device]["key"] if device in self.cameras else token_hex(24)
                if len(self.cameras) >= MAX_CAMERAS and device not in self.cameras:
                    raise CameraError("This gateway has reached its camera registry limit.")
                old_token = self.cameras.get(device, {}).get("eventToken")
                registry = {**self.cameras, device: {"key": key, "url": url, **(clock or {}),
                                                     **({"onvifAddress": source.address} if isinstance(source, OnvifDevice) else {}),
                                                     **({"gridUrl": grid_url} if grid_url else {}),
                                                     **({"eventToken": old_token} if old_token else {})}}
                private_write(self.registry, json.dumps(registry))
                self.cameras = registry
                previous = self.active.get(key)
                self.active[key] = candidate
                self.integrity.pop(key, None)
                grid_key = "grid:" + key
                previous_grid = self.active.pop(grid_key, None)
                if grid_candidate:
                    grid_candidate.last_access = time.monotonic()
                    self.active[grid_key] = grid_candidate
                    self.pending.pop(grid_pending)
                    grid_candidate = None
                self.record_video_check(key, True, time.time())
                candidate.last_access = time.monotonic()
                self.pending.pop(device)
                candidate = None
            paths = {"streamPath": f"streams/{key}/index.m3u8"}
            if grid_url:
                paths["gridStreamPath"] = f"streams/{key}/grid/index.m3u8"
            return paths
        finally:
            if candidate:
                with self.lock:
                    self.pending.pop(device, None)
                candidate.close()
            if grid_candidate:
                with self.lock:
                    self.pending.pop(grid_pending, None)
                grid_candidate.close()
            if previous:
                previous.close()
            if previous_grid:
                previous_grid.close()

    def check_capacity(self):
        if len(self.active) + len(self.pending) >= self.limit:
            raise CameraError("The gateway is at its active stream limit. Pause other camera feeds and try again.")

    def record_video_check(self, key, available, checked_at, video_at=None):
        prior = self.video_checks.get(key, {})
        self.video_checks[key] = {
            "state": "receiving" if available else "unavailable",
            "checkedAt": int(checked_at * 1000),
            "lastVideoAt": int((video_at or checked_at) * 1000) if available else prior.get("lastVideoAt"),
        }

    def video_status(self, key):
        with self.lock:
            camera = next((entry for entry in self.cameras.values() if entry["key"] == key), None)
            if not camera:
                return None
            result = self.video_checks.get(key, {"state": "unknown", "checkedAt": None, "lastVideoAt": None}).copy()
            if "clockOffsetSeconds" in camera:
                result.update(clockOffsetSeconds=camera["clockOffsetSeconds"], clockCheckedAt=camera["clockCheckedAt"])
            integrity = self.integrity.get(key)
            if integrity and time.time() * 1000 - integrity["checkedAt"] < max(120, self.probe_interval * 3) * 1000:
                result.update(integrity=integrity["state"], integritySince=integrity["since"], integrityCheckedAt=integrity["checkedAt"])
        if result["checkedAt"] and time.time() * 1000 - result["checkedAt"] > self.probe_interval * 3000:
            result["state"] = "unknown"
        return result

    def stream_for_key(self, key):
        with self.lock:
            return next(({**entry, "deviceId": device} for device, entry in self.cameras.items() if entry["key"] == key), None)

    def device_for_key(self, key):
        with self.lock:
            return next((device for device, entry in self.cameras.items() if entry["key"] == key), None)

    def event_token(self, device, rotate):
        with self.lock:
            entry = self.cameras.get(device)
            if entry is None:
                raise CameraError("Camera is not registered at this gateway.")
            if not rotate and entry.get("eventToken"):
                return entry["eventToken"]
            token = token_hex(32)
            registry = {**self.cameras, device: {**entry, "eventToken": token}}
            private_write(self.registry, json.dumps(registry))
            self.cameras = registry
            return token

    def event_authorized(self, device, authorization):
        with self.lock:
            token = self.cameras.get(device, {}).get("eventToken")
            return token is not None and compare_digest(authorization.encode(), ("Bearer " + token).encode())

    def issue_viewer(self, device, minutes, subject=None):
        with self.lock:
            entry = self.cameras.get(device)
        if not entry or type(minutes) is not int or not 1 <= minutes <= 30:
            raise CameraError("Choose a registered camera and a lifetime from 1 to 30 minutes.")
        return self.activity.issue_viewer(entry["key"], minutes, subject)

    def alert_notifications(self, webhook, secret):
        opener = build_opener(ProxyHandler({}), NoRedirect())
        while not self.stopping.wait(0.5):
            try:
                delivery = self.monitor.pending()
                if not delivery:
                    continue
                sequence, payload = delivery
                body = payload.encode()
                request = Request(webhook, body, {"Content-Type": "application/json",
                                                 "X-Proxigo-Signature": hmac.new(secret, body, sha256).hexdigest()})
                with opener.open(request, timeout=5):
                    self.monitor.delivered(sequence)
            except (OSError, sqlite3.Error):
                self.stopping.wait(3)

    def evaluate_site_response(self):
        while not self.stopping.wait(2):
            try:
                now = time.time()
                with self.lock:
                    video = {device: check["state"] for device, entry in self.cameras.items()
                             if (check := self.video_checks.get(entry["key"])) and check.get("checkedAt")
                             and now * 1000 - check["checkedAt"] < self.probe_interval * 3000}
                with self.activity.connect() as db:
                    db.execute("BEGIN IMMEDIATE")
                    self.monitor.coverage_operations.evaluate(db, video, now)
                    self.monitor.procedures.enqueue_overdue(db, now)
            except sqlite3.Error:
                self.stopping.wait(3)

    def index_site_evidence(self):
        delay = 0.1
        while not self.stopping.wait(delay):
            try:
                delay = 0.1 if self.monitor.cases.index_batch() else 2
            except sqlite3.Error:
                delay = 3

    def notifications(self, webhook, secret):
        opener = build_opener(ProxyHandler({}), NoRedirect())
        while not self.stopping.wait(5):
            with self.lock:
                states = {device: self.video_checks.get(entry["key"], {}) for device, entry in self.cameras.items()}
            now = time.time() * 1000
            fresh = {device: state["state"] for device, state in states.items()
                     if state.get("checkedAt") and now - state["checkedAt"] < self.probe_interval * 3000}
            try:
                if self.collaboration:
                    self.collaboration.enqueue_overdue()
                self.activity.group_outage({device for device, state in fresh.items() if state == "unavailable"},
                                           {device for device, state in fresh.items() if state == "receiving"})
                delivery = self.activity.next_notification()
                if delivery:
                    sequence, text = delivery
                    body = text.encode()
                    request = Request(webhook, body, {"Content-Type": "application/json",
                                                      "X-Proxigo-Signature": hmac.new(secret, body, sha256).hexdigest()})
                    with opener.open(request, timeout=5):
                        self.activity.sent(sequence)
            except (OSError, sqlite3.Error):
                self.stopping.wait(25)

    def probe_video(self):
        # One background decode at a time. Active viewers already provide segment timestamps.
        while not self.stopping.wait(2):
            checked_at = time.time()
            with self.lock:
                if len(self.active) + len(self.pending) >= self.limit:
                    continue
                sources = [
                    entry for device, entry in self.cameras.items()
                    if device not in self.pending and entry["key"] not in self.active
                ]
                source = min(sources, key=lambda entry: self.video_checks.get(entry["key"], {}).get("checkedAt") or 0, default=None)
                if not source or checked_at * 1000 - (self.video_checks.get(source["key"], {}).get("checkedAt") or 0) < self.probe_interval * 1000:
                    continue
            try:
                url = self.validate_url(source["url"], ("rtsp",), allow_credentials=True)
                result = subprocess.run([
                    self.ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-rtsp_transport", "tcp",
                    "-timeout", "5000000", "-allowed_media_types", "video", "-protocol_whitelist", "tcp,rtp,udp",
                    "-i", url, "-map", "0:v:0", "-an", "-vf", FILTER, "-frames:v", "3", "-threads", "1", "-f", "rawvideo", "-",
                ], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=10, check=True)
                available = bool(result.stdout)
            except (CameraError, OSError, subprocess.SubprocessError):
                available = False
            with self.lock:
                key = source["key"]
                if key not in self.active and any(entry is source for entry in self.cameras.values()):
                    self.record_video_check(key, available, time.time())
                    if available:
                        self.record_integrity(key, result.stdout)

    def record_integrity(self, key, raw):
        previous = self.integrity.get(key, {})
        sample, digest = inspect_sample(raw, previous.get("digest"))
        if not sample:
            return
        now = int(time.time() * 1000)
        count = previous.get("count", 0) + 1 if sample == previous.get("sample") else 1
        state = sample if sample == "clear" or count >= 2 else previous.get("state", "unverified")
        self.integrity[key] = {"checkedAt": now, "state": state, "sample": sample, "count": count, "digest": digest,
                               "since": previous.get("since", now) if state == previous.get("state") else now}

    def sample_active_video(self):
        while not self.stopping.wait(3):
            with self.lock:
                candidates = [(key, relay) for key, relay in self.active.items() if not key.startswith("grid:")]
                selected = min(candidates, key=lambda item: self.integrity.get(item[0], {}).get("checkedAt", 0), default=None)
                if not selected:
                    continue
                key, relay = selected
                if time.time() * 1000 - self.integrity.get(key, {}).get("checkedAt", 0) < max(10, self.probe_interval) * 1000:
                    continue
            with suppress(OSError, subprocess.SubprocessError):
                segment = max(relay.path.glob("segment-*.ts"), default=None)
                if segment is None:
                    continue
                sample = subprocess.run([self.ffmpeg, "-v", "error", "-nostdin", "-protocol_whitelist", "file", "-i", str(segment),
                    "-map", "0:v:0", "-an", "-vf", FILTER, "-frames:v", "3", "-threads", "1", "-f", "rawvideo", "-"],
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=4, check=True)
                with self.lock:
                    if self.active.get(key) is relay:
                        self.record_integrity(key, sample.stdout)

    def media(self, key, filename, grid=False):
        old = None
        relay_key = "grid:" + key if grid else key
        try:
            with self.lock:
                if self.stopping.is_set():
                    raise CameraError("The gateway is shutting down.")
                relay = self.active.get(relay_key)
                if filename == "index.m3u8" and (relay is None or relay.process.poll() is not None):
                    source = next((entry for entry in self.cameras.values() if entry["key"] == key), None)
                    source_url = source.get("gridUrl" if grid else "url") if source else None
                    if source_url is None:
                        return None
                    if relay:
                        old = self.active.pop(relay_key)
                    self.check_capacity()
                    url = self.validate_url(source_url, ("rtsp",), allow_credentials=True)
                    relay = self.active[relay_key] = Relay(self.ffmpeg, url, grid=grid)
                if relay is None:
                    return None
                relay.last_access = time.monotonic()
            path = relay.path / filename
            if filename == "index.m3u8" and not path.is_file():
                relay.await_playlist()
            with suppress(FileNotFoundError):
                return path.read_bytes()
            return None
        finally:
            if old:
                old.close()

    def clean_idle(self):
        while not self.stopping.wait(2):
            with self.lock:
                now = time.monotonic()
                wall_time = time.time()
                for key, relay in self.active.items():
                    if key.startswith("grid:"):
                        continue
                    playlist = relay.path / "index.m3u8"
                    with suppress(FileNotFoundError):
                        updated = playlist.stat().st_mtime
                        if relay.process.poll() is None and wall_time - updated < 8:
                            self.record_video_check(key, True, wall_time, updated)
                        else:
                            self.record_video_check(key, False, wall_time)
                    if relay.process.poll() is not None:
                        self.record_video_check(key, False, wall_time)
                stale = [
                    self.active.pop(key) for key, relay in list(self.active.items())
                    if now - relay.last_access > IDLE_TIMEOUT or relay.process.poll() is not None
                ]
            for relay in stale:
                relay.close()
            if self.archive.lock.acquire(blocking=False):
                try:
                    self.archive.expire()
                except (OSError, ValueError):
                    pass
                finally:
                    self.archive.lock.release()

    def close(self):
        self.stopping.set()
        with self.lock:
            relays = list(self.active.values()) + list(self.pending.values())
            self.active.clear()
            self.pending.clear()
        for relay in relays:
            relay.close()


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        self.request.settimeout(25)
        super().setup()

    def log_message(self, *_):
        # Request URLs contain private playback capabilities.
        return

    def reply(self, status, contents, content_type="application/json", headers=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(contents)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Vary", "Origin")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        origin = self.headers.get("Origin")
        if origin in self.server.origins:
            self.send_header("Access-Control-Allow-Origin", origin)
        if self.command == "OPTIONS":
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.end_headers()
        with suppress(BrokenPipeError, ConnectionResetError):
            self.wfile.write(contents)

    def json(self, status, payload):
        self.reply(status, json.dumps(payload).encode())

    def mp4(self, data):
        headers = {"Accept-Ranges": "bytes"}
        if not self.headers.get("Range"):
            self.reply(200, data, "video/mp4", headers)
            return
        match = re.fullmatch(r"bytes=(\d{0,16})-(\d{0,16})", self.headers["Range"])
        if match and (match[1] or match[2]):
            start = int(match[1]) if match[1] else max(0, len(data) - int(match[2]))
            end = min(int(match[2]), len(data) - 1) if match[1] and match[2] else len(data) - 1
            if start <= end:
                headers["Content-Range"] = f"bytes {start}-{end}/{len(data)}"
                self.reply(206, data[start:end + 1], "video/mp4", headers)
                return
        headers["Content-Range"] = f"bytes */{len(data)}"
        self.reply(416, b"", "video/mp4", headers)

    def permitted_origin(self):
        origin = self.headers.get("Origin")
        if origin is None or origin in self.server.origins:
            return True
        self.json(403, {"error": "This browser origin is not allowed by the camera gateway."})
        return False

    def do_OPTIONS(self):
        if self.permitted_origin():
            self.reply(204, b"")

    def site_actor(self):
        if hasattr(self, "portal_actor"):
            return self.portal_actor
        collaboration = self.server.gateway.collaboration
        return collaboration.authenticate(self.headers) if collaboration else None

    def permitted_portal_site(self):
        organization = self.headers.get("X-Proxigo-Organization")
        site = self.headers.get("X-Proxigo-Site")
        if organization is None and site is None:
            return True
        gateway = self.server.gateway
        accounts = gateway.collaboration.accounts if gateway.collaboration else None
        if (gateway.require_camera_session and accounts and accounts.tenant and accounts.proxy_secret
                and organization == accounts.organization["id"] and site == accounts.site_id
                and self.headers.get("X-Proxigo-Tenant") == accounts.tenant
                and compare_digest(self.headers.get("Authorization", "").encode(), ("Bearer " + accounts.proxy_secret).encode())):
            self.portal_actor = self.site_actor()
            if self.portal_actor:
                return True
        self.json(404, {"error": "Site resource unavailable."})
        return False

    def allowed_camera(self, key, action=None):
        gateway = self.server.gateway
        if not gateway.require_camera_session:
            return True
        actor = self.site_actor()
        device = gateway.device_for_key(key)
        if not device or not gateway.collaboration.can_view(actor, device):
            self.json(403, {"error": "Your site role cannot access this camera."})
            return False
        if action:
            gateway.activity.audit(actor["subject"], action, device)
        return True

    def allowed_adapter(self, device):
        token = self.server.gateway.telemetry_devices.get(device)
        return bool(token and compare_digest(self.headers.get("Authorization", "").encode(), ("Bearer " + token).encode()))

    def analytics_placement(self, camera):
        auth = self.headers.get("Authorization", "").encode()
        return next((placement for placement, token in self.server.gateway.analytics_workers.get(camera, {}).items()
                     if compare_digest(auth, ("Bearer " + token).encode())), None)

    def do_POST(self):
        if not self.permitted_origin() or not self.permitted_portal_site():
            return
        auth = self.headers.get("Authorization", "")
        gateway = self.server.gateway
        push = PUSH.fullmatch(self.path)
        analytics = ANALYTICS.fullmatch(self.path)
        placement = self.analytics_placement(analytics[1]) if analytics else None
        site_push = self.path == "/api/site/telemetry"
        site_write = self.path in ("/api/site/incident", "/api/site/maintenance", "/api/site/viewer", "/api/site/configuration",
                                   "/api/site/configuration/preview", "/api/site/accounts", "/api/site/assets", "/api/site/commissioning-test", "/api/site/vms-playback",
                                   "/api/site/analytics-review", "/api/site/commissioning-runs", "/api/site/dock-schedules",
                                   "/api/site/cases", "/api/site/case-export", "/api/site/configuration/import", "/api/site/evidence-recording",
                                   "/api/site/ai-jobs", "/api/site/automatic-triage", "/api/site/sensor-fusion")
        evidence = self.path == "/api/clips/package" or self.path == "/api/clips/hold" and hasattr(self, "portal_actor")
        actor = self.site_actor() if site_write or evidence else None
        management = compare_digest(auth.encode(), ("Bearer " + gateway.token).encode())
        can_manage_evidence = evidence and actor and actor["role"] in ("supervisor", "org-admin")
        allowed = (placement or self.allowed_adapter(analytics[1]) if analytics else gateway.event_authorized(push[1], auth) if push
                   else site_push or bool(actor) or management and not site_write)
        if not allowed:
            self.json(401, {"error": "Enter the camera event token." if push else "Enter the camera gateway's management token."})
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            limit = 1_048_576 if self.path in ("/api/site/configuration", "/api/site/configuration/preview", "/api/site/dock-schedules",
                                             "/api/site/configuration/import") else 16384
            if self.path.endswith("vms-playback"):
                limit = 131_072
            if not 1 <= size <= limit or self.headers.get_content_type() != "application/json":
                raise ValueError()
            payload = json.loads(self.rfile.read(size))
            if not isinstance(payload, dict):
                raise ValueError()
        except (OSError, ValueError):
            self.json(400, {"error": "Send a valid camera setup request."})
            return
        try:
            if site_push:
                sensor = payload.get("sensorId")
                check = payload.get("type") == LINK_CHECK
                placement = self.analytics_placement(sensor) if valid_event_device(sensor) and (check or "analytics" in payload) else None
                if check:
                    if not placement or not gateway.monitor:
                        raise PermissionError("Enroll an analytics worker for this camera and compute placement.")
                    self.json(200, gateway.monitor.analytics_link(sensor, payload.get("policyVersion"), placement))
                    return
                supplied = auth.encode()
                forwarded = bool(gateway.telemetry_receiver and compare_digest(supplied, ("Bearer " + gateway.telemetry_receiver).encode()))
                if not forwarded and not placement and not (valid_event_device(sensor) and self.allowed_adapter(sensor)):
                    self.json(401, {"error": "This adapter token is not authorized for the device."})
                    return
                if "analytics" in payload and not forwarded and not gateway.monitor:
                    raise CameraError("CCTV analytics requires a configured site gateway.")
                self.json(200, gateway.telemetry.push(payload, local=not forwarded, analytics_placement=placement))
            elif analytics and gateway.monitor:
                self.json(200, gateway.monitor.analytics_heartbeat(analytics[1], payload, placement))
            elif site_write:
                if self.path.endswith("ai-jobs"):
                    if not gateway.ai_jobs:
                        raise PermissionError("AI review is disabled at this site.")
                    self.json(202, gateway.ai_jobs.create(actor, payload))
                elif self.path.endswith("automatic-triage"):
                    if not gateway.ai_jobs or not gateway.ai_jobs.automatic:
                        raise PermissionError("Automatic triage is unavailable at this site.")
                    self.json(200, gateway.ai_jobs.automatic.change(actor, payload))
                elif self.path.endswith("sensor-fusion"):
                    if not gateway.sensor_fusion:
                        raise PermissionError("Gate sensor fusion is disabled at this site.")
                    self.json(200, gateway.sensor_fusion.review(actor, payload))
                elif self.path.endswith("analytics-review"):
                    self.json(200, gateway.monitor.analytics_review.change(actor, payload))
                elif self.path.endswith("commissioning-runs"):
                    self.json(200, gateway.commissioning.change(actor, payload))
                elif self.path.endswith("dock-schedules"):
                    self.json(200, gateway.monitor.dock_schedules.change(actor, payload))
                elif self.path.endswith("incident"):
                    self.json(200, gateway.collaboration.incident(actor, payload))
                elif self.path.endswith("cases"):
                    self.json(200, gateway.monitor.cases.change(gateway, actor, payload))
                elif self.path.endswith("evidence-recording"):
                    self.json(200, gateway.monitor.cases.recording(gateway, actor, payload.get("sourceId")))
                elif self.path.endswith("case-export"):
                    if not gateway.export_lock.acquire(blocking=False):
                        raise OverflowError("Another evidence package is being prepared. Try again shortly.")
                    try:
                        evidence, identifier = gateway.monitor.cases.export(actor, gateway, payload)
                        package, fingerprint = build_package(gateway.archive, gateway.state, evidence, actor["name"])
                        gateway.activity.audit(actor["subject"], "case-export", identifier, fingerprint)
                        self.reply(200, package, "application/zip", {"Content-Disposition": 'attachment; filename="proxigo-case-evidence.zip"'})
                    finally:
                        gateway.export_lock.release()
                elif self.path.endswith("configuration/import"):
                    if not manages_site(actor):
                        raise PermissionError("Importing site inventory requires a site supervisor.")
                    self.json(200, change_import(gateway.monitor.configuration, actor, payload))
                elif self.path.endswith("maintenance"):
                    self.json(200, gateway.collaboration.maintenance(actor, payload))
                elif self.path.endswith("viewer"):
                    camera = payload.get("deviceId")
                    if not valid_event_device(camera) or not gateway.collaboration.can_view(actor, camera):
                        raise PermissionError("Your site role cannot access this camera.")
                    viewer = gateway.issue_viewer(camera, 5, actor["subject"])
                    gateway.activity.audit(actor["subject"], "camera-view-session", camera)
                    self.json(200, viewer)
                elif self.path.endswith("accounts"):
                    self.json(200, gateway.collaboration.accounts.change(actor, payload))
                elif self.path.endswith("assets"):
                    self.json(200, gateway.assets.change(actor, payload))
                elif self.path.endswith("vms-playback"):
                    if not gateway.milestone:
                        raise CameraError("Configure the site XProtect connector first.")
                    try:
                        self.json(200, gateway.milestone.playback(actor, payload))
                    except PermissionError:
                        raise
                    except (OSError, ValueError):
                        self.json(502, {"error": "The XProtect recorder could not start or update playback."})
                elif self.path.endswith("commissioning-test"):
                    if not manages_site(actor):
                        raise PermissionError("Testing a site notification requires a site supervisor.")
                    if not gateway.commissioning_webhook:
                        raise CameraError("Configure a commissioning test destination at this gateway first.")
                    with gateway.activity.connect() as db:
                        active = gateway.monitor.configuration.published(db)
                    if not active or active[0] != payload.get("version"):
                        raise Conflict("The published configuration changed. Refresh the site first.")
                    key = token_hex(16)
                    data = json.dumps({"type": "site-commissioning-test", "organizationId": gateway.monitor.organization, "siteId": gateway.monitor.site,
                                       "configurationVersion": active[0], "deliveryId": key}).encode()
                    address, secret = gateway.commissioning_webhook
                    request = Request(address, data, {"Content-Type": "application/json", "X-Proxigo-Signature": hmac.new(secret, data, sha256).hexdigest()})
                    with build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=4):
                        gateway.activity.audit(actor["subject"], "commissioning-test-delivered", key, str(active[0]))
                    self.json(200, {"deliveryId": key})
                else:
                    if not manages_site(actor):
                        raise PermissionError("Publishing and testing site configuration requires a site supervisor.")
                    self.json(200, gateway.monitor.preview(payload) if self.path.endswith("preview") else gateway.monitor.configuration.change(actor, payload))
            elif push:
                self.json(200, {"accepted": gateway.telemetry.push_camera(push[1], payload)})
            elif self.path in ("/api/cameras/event-token", "/api/cameras/viewer-link"):
                device = payload.get("deviceId")
                if not valid_event_device(device):
                    raise CameraError("Provide the registered camera device ID.")
                if self.path.endswith("event-token"):
                    self.json(200, {"token": gateway.event_token(device, payload.get("rotate") is True)})
                else:
                    self.json(200, gateway.issue_viewer(device, payload.get("minutes")))
            elif self.path == "/api/profiles":
                self.json(200, {"profiles": self.server.gateway.profiles(payload)})
            elif self.path == "/api/cameras":
                self.json(200, self.server.gateway.connect(payload))
            elif self.path == "/api/clips/hold":
                key, clip_id, operator = (payload.get(field) for field in ("cameraKey", "clipId", "operator"))
                if evidence:
                    device = gateway.device_for_key(key)
                    if not can_manage_evidence or not device or not gateway.collaboration.can_view(actor, device):
                        raise PermissionError("Your site role cannot preserve footage from this camera.")
                    operator = actor["name"]
                if not all(isinstance(value, str) and value.strip() and len(value) <= 80 for value in (key, clip_id, operator)):
                    raise CameraError("Enter a valid evidence reference and operator name.")
                entry = self.server.gateway.archive.hold(key, clip_id, operator.strip())
                if evidence:
                    gateway.activity.audit(actor["subject"], "evidence-hold", clip_id, device)
                self.json(200, {name: entry[name] for name in ("heldUntil", "heldAt", "heldBy", "sha256")})
            elif self.path == "/api/clips/package":
                if not management and not can_manage_evidence:
                    raise PermissionError("A supervisor or gateway administrator must authorize evidence export.")
                if can_manage_evidence and not management:
                    clips = payload.get("clips")
                    if not isinstance(clips, list) or not all(isinstance(clip, dict) and gateway.collaboration.can_view(actor,
                        gateway.device_for_key(clip.get("cameraKey"))) and gateway.device_for_key(clip.get("cameraKey")) for clip in clips):
                        raise PermissionError("Your site role cannot export these cameras.")
                operator = actor["name"] if can_manage_evidence and not management else payload.get("operator")
                if not isinstance(operator, str) or not 1 <= len(operator.strip()) <= 80:
                    raise CameraError("Enter an evidence operator name.")
                if not gateway.export_lock.acquire(blocking=False):
                    self.json(503, {"error": "Another evidence package is being prepared. Try again shortly."})
                    return
                try:
                    payload.pop("caseReport", None)
                    package, fingerprint = build_package(gateway.archive, gateway.state, payload, operator.strip())
                    gateway.activity.audit(actor["subject"] if can_manage_evidence and not management else operator.strip(), "evidence-export", payload["incidentId"], fingerprint)
                    self.reply(200, package, "application/zip", {"Content-Disposition": 'attachment; filename="proxigo-incident-evidence.zip"'})
                finally:
                    gateway.export_lock.release()
            else:
                self.json(404, {"error": "Unknown gateway endpoint."})
        except PermissionError as exc:
            self.json(403, {"error": str(exc)})
        except Conflict as exc:
            self.json(409, {"error": str(exc)})
        except CameraError as exc:
            self.json(422, {"error": str(exc)})
        except OverflowError as exc:
            self.json(503, {"error": str(exc)})
        except (OSError, sqlite3.Error, subprocess.SubprocessError):
            self.json(500, {"error": "The camera gateway could not save its local registry."})

    def do_GET(self):
        if not self.permitted_origin() or not self.permitted_portal_site():
            return
        if self.path == "/health":
            self.json(200, {"status": "ready"})
            return
        status = VIDEO_STATUS.fullmatch(self.path)
        if status:
            if not self.allowed_camera(status[1]):
                return
            result = self.server.gateway.video_status(status[1])
            self.json(200, result) if result else self.json(404, {"error": "Camera stream not found."})
            return
        request = urlsplit(self.path)
        analytics = ANALYTICS.fullmatch(request.path)
        if analytics:
            gateway = self.server.gateway
            placement = self.analytics_placement(analytics[1])
            if not placement and not self.allowed_adapter(analytics[1]):
                self.json(401, {"error": "This adapter token is not authorized for the camera."})
            elif not gateway.monitor:
                self.json(404, {"error": "CCTV analytics requires a configured site gateway."})
            else:
                try:
                    with gateway.activity.connect() as db:
                        policy = gateway.monitor.analytics_policy(db, analytics[1], placement)
                    self.json(200, policy)
                except PermissionError as issue:
                    self.json(403, {"error": str(issue)})
            return
        job = AI_JOB_ROUTE.fullmatch(request.path)
        if job or request.path in ("/api/site/ai-jobs", "/api/site/device-degradation", "/api/site/automatic-triage", "/api/site/sensor-fusion"):
            gateway = self.server.gateway
            actor = self.site_actor()
            if not actor:
                self.json(401, {"error": "Sign in with an authorized site operator account."})
                return
            try:
                if job:
                    if not gateway.ai_jobs:
                        raise PermissionError("This AI job is unavailable to your account.")
                    data = gateway.ai_jobs.read(actor, job[1])
                elif request.path.endswith("ai-jobs"):
                    data = gateway.ai_jobs.capabilities(actor) if gateway.ai_jobs else {"handoff": False, "clipReview": False}
                elif request.path.endswith("automatic-triage"):
                    identifier = parse_qs(request.query, max_num_fields=1).get("incidentId", [None])[0]
                    if identifier is not None and not 1 <= len(identifier) <= 450:
                        raise CameraError("Choose a valid incident.")
                    automatic = gateway.ai_jobs.automatic if gateway.ai_jobs else None
                    data = automatic.read(actor, identifier) if automatic else {"available": False, "review": None}
                elif request.path.endswith("device-degradation"):
                    data = gateway.device_degradation.read(actor) if gateway.device_degradation else {"enabled": False}
                else:
                    identifier = parse_qs(request.query, max_num_fields=1).get("incidentId", [""])[0]
                    data = gateway.sensor_fusion.read(actor, identifier) if gateway.sensor_fusion else {"enabled": False}
                self.json(200, data)
            except PermissionError as issue:
                self.json(403, {"error": str(issue)})
            except (CameraError, ValueError) as issue:
                self.json(400, {"error": str(issue)})
            except (OSError, sqlite3.Error):
                self.json(500, {"error": "The camera gateway could not read its site intelligence records."})
            return
        if request.path in ("/api/site/session", "/api/site/activity", "/api/site/monitoring", "/api/site/configuration", "/api/site/accounts", "/api/site/assets", "/api/site/vms-channels", "/api/site/vms-status", "/api/site/analytics-review", "/api/site/analytics-report", "/api/site/inspection-results", "/api/site/commissioning-runs", "/api/site/dock-schedules", "/api/site/door-passages", "/api/site/camera-native", "/api/site/response-procedure", "/api/site/evidence-search", "/api/site/cases", "/api/site/operations", "/api/site/dispatch", "/api/site/incident-history"):
            collaboration = self.server.gateway.collaboration
            actor = self.site_actor()
            if not collaboration or not actor:
                self.json(401, {"error": "Sign in with an authorized site operator account."})
                return
            if request.path.endswith("session"):
                self.json(200, collaboration.session(actor))
            elif request.path.endswith("operations"):
                self.json(200, self.server.gateway.operations(actor))
            elif request.path.endswith("dispatch"):
                self.json(200, {**collaboration.monitor.dispatch(actor), "account": collaboration.session(actor)})
            elif request.path.endswith("incident-history"):
                try:
                    identifier = parse_qs(request.query, max_num_fields=1).get("incidentId", [""])[0]
                    self.json(200, collaboration.monitor.incident_history(actor, identifier))
                except PermissionError as issue:
                    self.json(403, {"error": str(issue)})
                except (CameraError, ValueError):
                    self.json(400, {"error": "Choose a valid incident."})
            elif request.path.endswith(("evidence-search", "cases")):
                try:
                    query = parse_qs(request.query, max_num_fields=12)
                    cases = collaboration.monitor.cases
                    value = cases.search(actor, query) if request.path.endswith("evidence-search") else cases.read(
                        self.server.gateway, actor, query.get("id", [None])[0])
                    self.json(200, value)
                except PermissionError as issue:
                    self.json(403, {"error": str(issue)})
                except (CameraError, ValueError, TypeError):
                    self.json(400, {"error": "Choose a valid case or evidence search."})
            elif request.path.endswith("response-procedure"):
                try:
                    identifier = parse_qs(request.query, max_num_fields=1).get("incidentId", [""])[0]
                    self.json(200, collaboration.monitor.procedures.read(actor, identifier))
                except PermissionError as issue:
                    self.json(403, {"error": str(issue)})
                except (CameraError, ValueError):
                    self.json(400, {"error": "Choose a valid incident."})
            elif request.path.endswith("commissioning-runs"):
                try:
                    self.json(200, self.server.gateway.commissioning.read(actor, parse_qs(request.query, max_num_fields=2)))
                except PermissionError as issue:
                    self.json(403, {"error": str(issue)})
                except ValueError as issue:
                    self.json(400, {"error": str(issue)})
            elif request.path.endswith("analytics-review"):
                try:
                    self.json(200, collaboration.monitor.analytics_review.read(actor, parse_qs(request.query, max_num_fields=6)))
                except ValueError:
                    self.json(400, {"error": "Choose valid CCTV review filters."})
            elif request.path.endswith("analytics-report"):
                try:
                    self.json(200, collaboration.monitor.analytics_review.report(actor, parse_qs(request.query, max_num_fields=2)))
                except ValueError as issue:
                    self.json(400, {"error": str(issue)})
            elif request.path.endswith("inspection-results"):
                try:
                    self.json(200, collaboration.monitor.inspections.read(actor, parse_qs(request.query, max_num_fields=6)))
                except ValueError:
                    self.json(400, {"error": "Choose valid inspection result filters."})
            elif request.path.endswith("dock-schedules"):
                try:
                    self.json(200, collaboration.monitor.dock_schedules.read(actor, parse_qs(request.query, max_num_fields=1)))
                except (CameraError, ValueError) as issue:
                    self.json(400, {"error": str(issue)})
            elif request.path.endswith("door-passages"):
                try:
                    self.json(200, collaboration.monitor.door_passages.read(actor, parse_qs(request.query, max_num_fields=2)))
                except ValueError as issue:
                    self.json(400, {"error": str(issue)})
            elif request.path.endswith("camera-native"):
                self.json(200, collaboration.monitor.native_events.read(actor))
            elif request.path.endswith("configuration"):
                if not manages_site(actor):
                    self.json(403, {"error": "Viewing site drafts requires a site supervisor."})
                else:
                    self.json(200, collaboration.monitor.configuration.detail())
            elif request.path.endswith("accounts"):
                try:
                    self.json(200, collaboration.accounts.list(actor))
                except PermissionError as exc:
                    self.json(403, {"error": str(exc)})
            elif request.path.endswith("assets"):
                gateway = self.server.gateway
                self.json(200, {**gateway.assets.status(actor), "testNotificationsEnabled": bool(gateway.commissioning_webhook)})
            elif request.path.endswith("vms-channels"):
                if not manages_site(actor):
                    self.json(403, {"error": "Importing camera channels requires a site supervisor."})
                else:
                    try:
                        gateway = self.server.gateway
                        provider = parse_qs(request.query, max_num_fields=1).get("provider", ["xprotect"])[0]
                        connector = {"xprotect": gateway.milestone, "genetec": gateway.genetec}.get(provider)
                        if connector:
                            self.json(200, connector.channels())
                        else:
                            self.json(404, {"error": "Configure this VMS connector at the site gateway first."})
                    except (OSError, ValueError, TypeError, CameraError, KeyError):
                        self.json(502, {"error": "The VMS channel catalog could not be read."})
            elif request.path.endswith("vms-status"):
                if not manages_site(actor):
                    self.json(403, {"error": "A site supervisor is required to view VMS connector status."})
                else:
                    gateway = self.server.gateway
                    self.json(200, {"connectors": [connector.state.status() for connector in (gateway.milestone, gateway.genetec) if connector]})
            elif request.path.endswith("monitoring"):
                try:
                    query = parse_qs(request.query, max_num_fields=5)
                    cursors = [int(query.get(field, ["0"])[0]) for field in ("version", "event", "health", "media")]
                    if not all(0 <= value < 2**63 for value in cursors):
                        raise ValueError()
                    if query.get("scope", [""])[0] != actor["changedAt"]:
                        cursors = [0, 0, 0, 0]
                    self.json(200, {**collaboration.monitor.view(actor, *cursors), "account": collaboration.session(actor), "scopeRevision": actor["changedAt"],
                                    "recoveryEnabled": self.server.gateway.recording_recovery})
                except ValueError:
                    self.json(400, {"error": "Invalid site monitoring cursor."})
            else:
                try:
                    after = int(parse_qs(request.query, max_num_fields=3).get("after", ["0"])[0])
                    if not 0 <= after < 2**63:
                        raise ValueError()
                    self.json(200, collaboration.page(actor, after))
                except ValueError:
                    self.json(400, {"error": "Invalid site activity cursor."})
            return
        if request.path == "/api/site/telemetry":
            gateway = self.server.gateway
            reader = gateway.telemetry_reader
            actor = self.site_actor()
            privileged = bool(reader and compare_digest(self.headers.get("Authorization", "").encode(), ("Bearer " + reader).encode()))
            if not privileged and not actor:
                self.json(401, {"error": "Enter the site telemetry reader token."})
                return
            allowed = None
            if not privileged and actor["cameras"] != "*":
                with gateway.activity.connect() as db:
                    current = gateway.monitor.configuration.published(db)
                mapped = ({entry["id"]: camera_for(entry) for entry in json.loads(current[2])["sensors"]} if current
                          else gateway.collaboration.device_cameras)
                allowed = set(actor["cameras"]) | {device for device, camera in mapped.items() if camera in actor["cameras"]}
            try:
                after = int(parse_qs(request.query, max_num_fields=3).get("after", ["0"])[0])
                if not 0 <= after < 2**63:
                    raise ValueError()
                self.json(200, gateway.telemetry.read(after, allowed))
            except ValueError:
                self.json(400, {"error": "Invalid site telemetry cursor."})
            return
        events = EVENTS.fullmatch(request.path)
        if events:
            if not self.allowed_camera(events[1]):
                return
            try:
                after = int(parse_qs(request.query, max_num_fields=3).get("after", ["0"])[0])
                if not 0 <= after < 2**63:
                    raise ValueError()
            except ValueError:
                self.json(400, {"error": "Invalid event cursor."})
                return
            with self.server.gateway.lock:
                device = next((device for device, entry in self.server.gateway.cameras.items() if entry["key"] == events[1]), None)
            if device:
                self.json(200, {"events": self.server.gateway.activity.events(device, after)})
            else:
                self.json(404, {"error": "Camera not found."})
            return
        recording = RECORDING.fullmatch(request.path)
        if recording:
            if not self.allowed_camera(recording[1]):
                return
            try:
                timestamp = int(parse_qs(request.query, max_num_fields=3).get("at", [""])[0])
            except ValueError:
                timestamp = 0
            if not 0 < timestamp <= (time.time() + 5) * 1000:
                self.json(400, {"error": "Provide an observation time in UTC milliseconds."})
                return
            entry = self.server.gateway.archive.retrieve(self.server.gateway.stream_for_key(recording[1]), timestamp)
            self.json(200, {field: value for field, value in entry.items() if field != "cameraKey"})
            return
        clip = CLIP.fullmatch(self.path)
        if clip:
            if not self.allowed_camera(clip[1], "recording-view" if not self.headers.get("Range") or self.headers.get("Range", "").startswith("bytes=0-") else None):
                return
            with suppress(OSError, ValueError):
                data = self.server.gateway.archive.media(clip[1], clip[2])
                if data is not None:
                    self.mp4(data)
                    return
        grid = GRID_STREAM.fullmatch(self.path)
        match = STREAM.fullmatch(self.path) or grid
        viewer = VIEW.fullmatch(self.path)
        if match or viewer:
            key, filename = (match or viewer).groups()
            if viewer:
                key = self.server.gateway.activity.viewer(key)
                if not key:
                    self.json(403, {"error": "This camera view link is invalid or expired."})
                    return
            elif not self.allowed_camera(key, "camera-live-view" if filename == "index.m3u8" else None):
                return
            try:
                contents = self.server.gateway.media(key, filename, grid=bool(grid))
            except (OSError, CameraError):
                self.json(503, {"error": "The camera stream is unavailable."})
                return
            if contents is not None:
                self.reply(200, contents, "application/vnd.apple.mpegurl" if filename.endswith(".m3u8") else "video/mp2t")
                return
        self.json(404, {"error": "Camera stream not found."})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--state-dir", type=Path, default=Path.home() / ".local/share/proxigo-camera-gateway")
    parser.add_argument("--allow-source", action="append", type=ip_network, required=True, help="Approved camera subnet or IP. Repeat as needed.")
    parser.add_argument("--origin", action="append", default=[], help="Exact additional browser origin. Repeat as needed.")
    parser.add_argument("--max-streams", type=int, choices=range(1, 33), default=12)
    parser.add_argument("--probe-interval", type=int, default=90, help="Minimum seconds between background video checks per camera.")
    parser.add_argument("--axis-recorder", action="append", default=[], help="Approved Axis edge recorder HTTP(S) origin. Repeat per camera.")
    parser.add_argument("--onvif-recordings", type=Path, help="JSON device ID to Profile G recording token mapping for H.264 replay.")
    parser.add_argument("--onvif-capture-python", help="Python executable with PyGObject and GStreamer H.264 replay plugins.")
    parser.add_argument("--video-webhook", help="HTTPS endpoint for grouped gateway video outage notifications.")
    parser.add_argument("--alert-webhook", help="HTTPS endpoint for always-on security incident notifications.")
    parser.add_argument("--commissioning-webhook", help="Separate HTTPS test destination for site commissioning notifications.")
    parser.add_argument("--recording-recovery", action="store_true", help="Retrieve incident recording windows after a camera reconnects.")
    parser.add_argument("--milestone-origin", help="Trusted XProtect HTTPS API origin.")
    parser.add_argument("--milestone-token-file", type=Path, help="Private file containing a read-only XProtect user token.")
    parser.add_argument("--milestone-event-map", type=Path, help="Private mapping of XProtect event and source identifiers.")
    parser.add_argument("--genetec-origin", help="Trusted HTTPS Security Center Web SDK origin.")
    parser.add_argument("--genetec-credentials", type=Path, help="Private Security Center Web SDK credential JSON file.")
    parser.add_argument("--genetec-device-map", type=Path, help="Private map from Security Center entity GUIDs to site device IDs.")
    parser.add_argument("--genetec-stream-port", type=int, default=4591, help="Approved TLS port for Web SDK event streaming.")
    parser.add_argument("--vms-ca", type=Path, help="Trusted private CA for VMS HTTPS connections.")
    parser.add_argument("--recovery-kbps", type=int, default=4096, help="Maximum average Axis recovery download rate in kilobits per second.")
    parser.add_argument("--telemetry-devices", type=Path, help="Private JSON file mapping device IDs to ingest tokens.")
    parser.add_argument("--analytics-workers", type=Path, help="Private JSON mapping camera IDs to compute-placement worker tokens.")
    parser.add_argument("--commissioning-worker", type=Path, help="Private JSON with the local replay Python and approved YOLO11 model.")
    parser.add_argument("--onvif-analytics", type=Path, help="Private JSON array enrolling read-only ONVIF camera event sources.")
    parser.add_argument("--telemetry-upstream", help="HTTPS upstream endpoint for durable site telemetry forwarding.")
    parser.add_argument("--site-intelligence", type=Path, help="Private site intelligence settings for AI, drift warnings and gate sensors.")
    parser.add_argument("--site-access", type=Path, help="Private site and operator access configuration JSON.")
    parser.add_argument("--require-camera-session", action="store_true", help="Require authenticated site camera authorization for private feeds and recordings.")
    args = parser.parse_args()
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        parser.error("Install FFmpeg with libx264 support.")

    if args.probe_interval < 5:
        parser.error("The probe interval must be at least five seconds.")
    webhook_secret = os.environ.get("PROXIGO_WEBHOOK_SECRET", "")
    telemetry_secret = os.environ.get("PROXIGO_TELEMETRY_UPSTREAM_TOKEN", "")
    telemetry_reader = os.environ.get("PROXIGO_TELEMETRY_READER_TOKEN", "")
    telemetry_receiver = os.environ.get("PROXIGO_TELEMETRY_RECEIVER_TOKEN", "")
    identity_proxy_secret = os.environ.get("PROXIGO_IDENTITY_PROXY_TOKEN", "")
    if args.require_camera_session and not args.site_access:
        parser.error("Camera session enforcement requires --site-access.")
    if args.analytics_workers and not args.site_access:
        parser.error("Analytics worker enrollment requires --site-access.")
    if args.commissioning_worker and not args.site_access:
        parser.error("Commissioning replay requires --site-access.")
    if args.site_intelligence and not args.site_access:
        parser.error("Site intelligence requires --site-access.")
    if args.onvif_analytics and not args.site_access:
        parser.error("Native ONVIF events require --site-access.")
    if identity_proxy_secret and not re.fullmatch(r"[a-f0-9]{64}", identity_proxy_secret):
        parser.error("PROXIGO_IDENTITY_PROXY_TOKEN must contain 64 hexadecimal characters.")
    for secret in (telemetry_secret, telemetry_reader, telemetry_receiver):
        if secret and not re.fullmatch(r"[a-f0-9]{64}", secret):
            parser.error("Telemetry tokens must contain 64 hexadecimal characters.")
    if args.telemetry_upstream:
        target = urlsplit(args.telemetry_upstream)
        if target.scheme != "https" or not target.hostname or target.username or target.password or target.fragment or not telemetry_secret:
            parser.error("Set an HTTPS telemetry endpoint and PROXIGO_TELEMETRY_UPSTREAM_TOKEN.")
    if (args.alert_webhook or args.commissioning_webhook or args.recording_recovery) and not args.site_access:
        parser.error("Alert notifications, commissioning and recording recovery require --site-access.")
    if args.recording_recovery and (not 1024 <= args.recovery_kbps <= 20000 or not (args.axis_recorder or args.onvif_recordings)):
        parser.error("Recording recovery needs a recorder and a rate of 1024 to 20000 kbps.")
    if args.commissioning_webhook and args.commissioning_webhook in (args.alert_webhook, args.video_webhook):
        parser.error("Use a separate commissioning test destination.")
    if args.milestone_origin and (not args.milestone_token_file or not args.site_access):
        parser.error("XProtect integration requires a site and a private read-only user-token file.")
    for webhook in filter(None, (args.video_webhook, args.alert_webhook, args.commissioning_webhook)):
        url = urlsplit(webhook)
        if url.scheme != "https" or not url.hostname or url.username or url.password or url.fragment or len(webhook_secret) < 24:
            parser.error("Set an HTTPS webhook and PROXIGO_WEBHOOK_SECRET of at least 24 characters.")
    try:
        args.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        state_lock = StateLock(args.state_dir)
        gateway = CameraGateway(args.state_dir, ffmpeg, args.allow_source, args.max_streams, args.probe_interval, args.axis_recorder,
                               args.telemetry_upstream, telemetry_secret, read_device_tokens(args.telemetry_devices), telemetry_reader, telemetry_receiver,
                               json.loads(args.site_access.read_text()) if args.site_access else None, identity_proxy_secret, args.require_camera_session,
                               profile_g_recordings(args.onvif_recordings), args.onvif_capture_python, worker_tokens(args.analytics_workers),
                               args.commissioning_worker, json.loads(args.site_intelligence.read_text()) if args.site_intelligence else None)
        native_sources = enroll_native(gateway, args.onvif_analytics)
        if args.milestone_origin:
            gateway.milestone = MilestoneConnector(args.milestone_origin, args.milestone_token_file, args.milestone_event_map, gateway.telemetry, args.vms_ca)
        if args.genetec_origin:
            if not gateway.monitor or not args.genetec_credentials or not 1 <= args.genetec_stream_port <= 65535:
                raise CameraError("Security Center requires a configured site, a private credentials file and a TLS streaming port.")
            gateway.genetec = GenetecConnector(args.genetec_origin, args.genetec_credentials, args.genetec_device_map,
                                              gateway.telemetry, args.vms_ca, args.genetec_stream_port)
    except (CameraError, KeyError, OSError, TypeError, ValueError) as error:
        parser.error(str(error))
    gateway.recording_recovery = args.recording_recovery
    if args.commissioning_webhook:
        gateway.commissioning_webhook = (args.commissioning_webhook, webhook_secret.encode())
    with ThreadingHTTPServer((args.host, args.port), Handler) as server:
        server.gateway = gateway
        server.origins = set(DEFAULT_ORIGINS + tuple(args.origin))
        cleanup = threading.Thread(target=gateway.clean_idle, daemon=True)
        cleanup.start()
        probe = threading.Thread(target=gateway.probe_video, daemon=True)
        probe.start()
        integrity = threading.Thread(target=gateway.sample_active_video, daemon=True)
        integrity.start()
        forwarding = None
        if args.telemetry_upstream:
            forwarding = threading.Thread(target=gateway.telemetry.forward, args=(gateway.stopping,), daemon=True)
            forwarding.start()
        notifications = None
        if args.video_webhook:
            notifications = threading.Thread(target=gateway.notifications, args=(args.video_webhook, webhook_secret.encode()), daemon=True)
            notifications.start()
        alert_notifications = None
        if args.alert_webhook:
            alert_notifications = threading.Thread(target=gateway.alert_notifications, args=(args.alert_webhook, webhook_secret.encode()), daemon=True)
            alert_notifications.start()
        recovery = None
        if args.recording_recovery:
            recovery = threading.Thread(target=gateway.monitor.recover_recordings, args=(gateway, args.recovery_kbps), daemon=True)
            recovery.start()
        vms_threads = [threading.Thread(target=connector.poll, args=(gateway.stopping,), daemon=True)
                       for connector in (gateway.milestone, gateway.genetec) if connector]
        for worker in vms_threads:
            worker.start()
        commissioning = None
        if gateway.commissioning and gateway.commissioning.worker:
            commissioning = threading.Thread(target=gateway.commissioning.poll, daemon=True)
            commissioning.start()
        passages = None
        if gateway.monitor:
            passages = threading.Thread(target=gateway.monitor.door_passages.poll, args=(gateway.stopping,), daemon=True)
            passages.start()
            site_response = threading.Thread(target=gateway.evaluate_site_response, daemon=True)
            site_response.start()
            evidence_index = threading.Thread(target=gateway.index_site_evidence, daemon=True)
            evidence_index.start()
        native_threads = [threading.Thread(target=source.poll, args=(gateway.stopping,), daemon=True) for source in native_sources]
        for worker in native_threads:
            worker.start()
        intelligence = [service.poll for service in (gateway.ai_jobs, gateway.device_degradation, gateway.sensor_fusion) if service]
        intelligence_threads = [threading.Thread(target=poll, daemon=True) for poll in intelligence]
        if gateway.sensor_fusion:
            intelligence_threads.extend(threading.Thread(target=radar.poll, args=(gateway.stopping,), daemon=True)
                                        for radar in gateway.sensor_fusion.gates)
        for worker in intelligence_threads:
            worker.start()
        print(f"Camera gateway listening on {server.server_address[0]}:{server.server_address[1]}", flush=True)
        print(f"Management token file: {gateway.token_path}", flush=True)

        def stop(*_):
            raise KeyboardInterrupt()

        signal.signal(signal.SIGTERM, stop)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            gateway.close()
            cleanup.join()
            probe.join()
            integrity.join()
            if forwarding:
                forwarding.join()
            if notifications:
                notifications.join()
            if alert_notifications:
                alert_notifications.join()
            if recovery:
                recovery.join(timeout=5)
            for worker in vms_threads:
                worker.join(timeout=8)
            if commissioning:
                commissioning.join(timeout=5)
            if passages:
                passages.join(timeout=5)
                site_response.join(timeout=5)
                evidence_index.join(timeout=5)
            for worker in (*native_threads, *intelligence_threads):
                worker.join(timeout=5)
            state_lock.file.close()


if __name__ == "__main__":
    main()
