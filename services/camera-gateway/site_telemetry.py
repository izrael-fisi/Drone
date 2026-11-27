"""Site-local adapter ingestion with durable cursor and optional HTTPS forwarding."""

from datetime import datetime, timezone
from hashlib import sha256
import json
from math import isfinite
import re
import sqlite3
import time
from urllib.request import ProxyHandler, Request, build_opener

from onvif import CameraError, NoRedirect
from site_activity import EVENT_LABELS, valid_event_device
from analytics_contract import KINDS as ANALYTICS_TYPES, evidence
from inspection_results import EVENT_TYPE as INSPECTION_RESULT, normalize_inspection
from door_passages import ACCESS_FACTS
from native_camera_events import native_evidence

TYPES = {"door-forced-open", "door-held-open", "motion", "tamper", "rf-detection", INSPECTION_RESULT} | ANALYTICS_TYPES | ACCESS_FACTS
MAX_EVENTS = 10000


def normalize(payload):
    kind, sensor, identifier, stamp = (payload.get(field) for field in ("type", "sensorId", "id", "occurredAt"))
    if kind not in ("observation", "health") or not valid_event_device(sensor):
        raise CameraError("Supply a registered device ID and observation or health type.")
    if not isinstance(identifier, str) or not identifier.strip() or len(identifier) > 128:
        raise CameraError("Supply a stable event ID up to 128 characters.")
    try:
        event_time = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        if not event_time.tzinfo or not 0 < event_time.timestamp() <= time.time() + 60:
            raise ValueError()
    except (AttributeError, ValueError, OverflowError, OSError):
        raise CameraError("Supply an event time with a timezone and a synchronized device clock.") from None
    record = {"type": kind, "sensorId": sensor, "id": identifier, "occurredAt": event_time.astimezone(timezone.utc).isoformat()}
    if kind == "health":
        if payload.get("state") not in ("online", "offline"):
            raise CameraError("Health state is online or offline.")
        return {**record, "state": payload["state"]}
    summary, event_type, position = (payload.get(field) for field in ("summary", "eventType", "position"))
    if event_type == INSPECTION_RESULT:
        if position is not None or "analytics" in payload:
            raise CameraError("Inspection results use their published station camera location.")
        inspection = normalize_inspection(payload.get("inspection"))
        if inspection["source"] == "opc-ua":
            identity = json.dumps([inspection["stationId"], inspection["resultId"]], separators=(",", ":"))
            record["id"] = "inspection-opc-" + sha256(identity.encode()).hexdigest()
        label = {"pass": "Component presence passed", "fail": "Component presence failed", "incomplete": "Inspection incomplete"}
        return {**record, "eventType": event_type, "inspection": inspection,
                "summary": label[inspection["outcome"]]}
    if "inspection" in payload:
        raise CameraError("Use the inspection-result type for a component presence result.")
    if not isinstance(summary, str) or not 1 <= len(summary.strip()) <= 160 or not isinstance(event_type, str) or event_type not in TYPES:
        raise CameraError("Supply a supported event type and short summary.")
    record.update(summary=summary.strip(), eventType=event_type)
    if "nativeAnalytics" in payload:
        if event_type != "line-crossing" or position is not None or "analytics" in payload:
            raise CameraError("Provide camera-native analytics on its enrolled crossing rule.")
        record["nativeAnalytics"] = native_evidence(payload["nativeAnalytics"])
    if event_type in ACCESS_FACTS and position is not None:
        raise CameraError("Access passage events use the registered door location.")
    if "analytics" in payload:
        if event_type not in ANALYTICS_TYPES or position is not None:
            raise CameraError("CCTV evidence uses its camera location and a supported rule type.")
        record["analytics"] = evidence(payload["analytics"])
    elif event_type in ANALYTICS_TYPES - {"line-crossing"}:
        raise CameraError("Supply CCTV analytics provenance for this event type.")
    if position is not None:
        if not isinstance(position, list) or len(position) != 2 or not all(type(axis) in (float, int) and -limit <= axis <= limit
            for axis, limit in zip(position, (180, 85))):
            raise CameraError("Position must be a longitude, latitude pair.")
        record["position"] = position
    return record


class SiteTelemetry:
    def __init__(self, activity, upstream=None, secret="", monitor=None):
        self.activity, self.upstream, self.secret = activity, upstream, secret
        self.monitor = monitor
        with activity.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS telemetry (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT, sensor TEXT NOT NULL, kind TEXT NOT NULL, event_id TEXT NOT NULL,
                    received_at REAL NOT NULL, payload TEXT NOT NULL, UNIQUE(sensor, kind, event_id)
                );
                CREATE TABLE IF NOT EXISTS telemetry_delivery (
                    sequence INTEGER PRIMARY KEY, payload TEXT NOT NULL, received_at REAL NOT NULL
                );
            """)

    def push(self, payload, local=True, analytics_placement=None, native_camera=None, radar=None):
        event = normalize(payload)
        if "nativeAnalytics" in event and (not local or not self.monitor or native_camera != event["sensorId"]):
            raise PermissionError("Camera-native events require the enrolled local connector.")
        if radar:
            if not local or not self.monitor or event["sensorId"] != radar.sensor or event.get("eventType") != "rf-detection":
                raise PermissionError("Radar evidence requires the enrolled local connector.")
            event["radarEvidence"] = {"source": "axis-radar", "cameraId": radar.camera, "boundaryId": radar.boundary, "profile": radar.profile}
        if "inspection" in event:
            if not local or not self.monitor:
                raise CameraError("Inspection results require this site gateway and its enrolled station adapter.")
            with self.activity.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                return self.monitor.record_inspection(db, {**event, "gatewayReceivedAt": time.time()})
        if analytics_placement:
            if "analytics" not in event:
                raise CameraError("An analytics worker can submit only CCTV observations.")
            event["analytics"]["placement"] = analytics_placement
        with self.activity.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            return self.write(db, event, local, payload.get("gatewayReceivedAt") if not local else None)

    def push_camera(self, camera, payload):
        received = self.activity.camera_event(payload)
        event = normalize({"type": "observation", "sensorId": camera, "id": "gateway-" + received["id"], "occurredAt": received["occurredAt"],
                           "eventType": received["event"], "summary": EVENT_LABELS[received["event"]]})
        with self.activity.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            accepted = self.activity.write_camera_event(db, camera, received)
            self.write(db, event, True)
        return accepted

    def write(self, db, event, local, original=None):
        now = time.time()
        received = original if original is not None else now
        if type(received) not in (float, int) or not isfinite(received) or not 0 < received <= now + 60:
            raise CameraError("Invalid original gateway receipt time.")
        encoded = json.dumps({**event, "gatewayReceivedAt": received}, separators=(",", ":"))
        found = db.execute("SELECT sequence FROM telemetry WHERE sensor=? AND kind=? AND event_id=?",
                           (event["sensorId"], event["type"], event["id"])).fetchone()
        if found:
            return {"accepted": False, "sequence": found[0]}
        forward = local and self.upstream and "nativeAnalytics" not in event and "radarEvidence" not in event
        if forward and db.execute("SELECT count(*) FROM telemetry_delivery").fetchone()[0] >= MAX_EVENTS:
            raise OverflowError("The upstream telemetry queue is full. Retry the event later.")
        sequence = db.execute("INSERT INTO telemetry(sensor,kind,event_id,received_at,payload) VALUES(?,?,?,?,?)",
                              (event["sensorId"], event["type"], event["id"], now, encoded)).lastrowid
        if forward:
            db.execute("INSERT INTO telemetry_delivery VALUES(?,?,?)", (sequence, encoded, received))
        if self.monitor and (local or "analytics" not in event):
            self.monitor.record(db, {**event, "gatewayReceivedAt": received}, sequence)
        db.execute("DELETE FROM telemetry WHERE sequence <= ?", (sequence - MAX_EVENTS,))
        return {"accepted": True, "sequence": sequence}

    def read(self, after, allowed_sensors=None):
        with self.activity.connect() as db:
            first = db.execute("SELECT min(sequence) FROM telemetry").fetchone()[0]
            rows = db.execute("SELECT sequence,sensor,payload FROM telemetry WHERE sequence>? ORDER BY sequence LIMIT 200", (after,)).fetchall()
            count, oldest = db.execute("SELECT count(*), min(received_at) FROM telemetry_delivery").fetchone()
        return {"events": [{**json.loads(body), "sequence": sequence} for sequence, sensor, body in rows if allowed_sensors is None or sensor in allowed_sensors],
                "firstSequence": first if allowed_sensors is None else None, "after": rows[-1][0] if rows else after, "hasMore": len(rows) == 200,
                "backlogCount": count if allowed_sensors is None else 0, "oldestQueuedAt": oldest if allowed_sensors is None else None, "forwarding": bool(self.upstream)}

    def forward(self, stopping):
        opener = build_opener(ProxyHandler({}), NoRedirect())
        while not stopping.wait(0.1):
            try:
                with self.activity.connect() as db:
                    queued = db.execute("SELECT sequence,payload FROM telemetry_delivery ORDER BY sequence LIMIT 1").fetchone()
                if not queued:
                    stopping.wait(2)
                    continue
                sequence, payload = queued
                request = Request(self.upstream, payload.encode(), {"Content-Type": "application/json", "Authorization": "Bearer " + self.secret})
                with opener.open(request, timeout=5) as response:
                    if response.status != 200:
                        raise OSError("Unexpected upstream status")
                with self.activity.connect() as db:
                    db.execute("DELETE FROM telemetry_delivery WHERE sequence=?", (sequence,))
            except (OSError, sqlite3.Error):
                stopping.wait(10)


def read_device_tokens(path):
    if not path:
        return {}
    tokens = json.loads(path.read_text())
    if not isinstance(tokens, dict) or any(not valid_event_device(device) or not isinstance(token, str) or not re.fullmatch(r"[a-f0-9]{64}", token)
        for device, token in tokens.items()):
        raise CameraError("The telemetry token file must map device IDs to 64-character hexadecimal tokens.")
    return tokens
