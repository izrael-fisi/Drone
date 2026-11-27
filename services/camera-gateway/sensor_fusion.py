"""Observe-mode corroboration of a mapped gate's door, radar and camera events."""

from hashlib import sha256
import json
import re
import sqlite3
import ssl
import time
from urllib.parse import urlsplit

from axis_archive import parse_utc
from native_camera_events import EVENTS, WSN, WSTOP, PullPointSource
from onvif import CameraError, OnvifDevice, SCHEMA, add, tag
from site_activity import valid_event_device
from site_collaboration import Conflict, SiteCollaboration
from site_accounts import manages_site

TOPIC_NS = "http://www.axis.com/2009/event/topics"
PROFILE = re.compile(r"Channel[0-9]{1,3}Profile[0-9]{1,8}")
RADAR = "axis-radar"
WINDOW = 10
GRACE = 30
MAX_AGE = 30 * 86400
OUTCOMES = ("confirmed", "false-corroboration", "unverifiable")


class GateRadar(PullPointSource):
    topic_namespace = TOPIC_NS

    def __init__(self, fusion, config):
        self.fusion, self.gateway = fusion, fusion.gateway
        self.sensor, self.boundary, self.camera, self.door, self.profile = (
            config.get(key) for key in ("sensorId", "boundaryId", "cameraId", "doorId", "axisProfile"))
        if (not all(valid_event_device(value) for value in (self.sensor, self.boundary, self.camera, self.door))
                or len({self.sensor, self.camera, self.door}) != 3
                or not PROFILE.fullmatch(str(self.profile)) or type(config.get("doorClockVerified", False)) is not bool):
            raise CameraError("Map each Axis radar zone to a boundary, door, camera and specific radar profile.")
        self.door_clock_verified = config.get("doorClockVerified", False)
        self.address = self.gateway.validate_url(config.get("address"), ("https", "http"))
        # Loopback permits reproducible fixture testing. Real devices must be approved TLS origins.
        origin = urlsplit(self.address)
        if origin.scheme == "http" and origin.hostname not in ("127.0.0.1", "::1"):
            raise CameraError("Configure a trusted HTTPS address for the radar.")
        username, password = config.get("username", ""), config.get("password", "")
        if any(not isinstance(value, str) or len(value) > 256 or any(ord(char) < 32 for char in value) for value in (username, password)) or password and not username:
            raise CameraError("Supply valid private radar credentials.")
        context = ssl.create_default_context(cafile=config.get("caFile"))
        self.device = OnvifDevice(self.address, username, password, self.gateway.validate_url, context)
        self.topic = "RadarSource/MotionAlarm/" + self.profile
        self.active = None
        self.last_property_time = 0

    def mapped(self, config):
        sensors = {value["id"]: value for value in config["sensors"]}
        zone = next((row for row in config["boundaries"] if row["id"] == self.boundary and row["enabled"]), {})
        return (bool({self.sensor, self.camera, self.door} <= set(zone.get("sensorIds", [])))
                and sensors.get(self.sensor, {}).get("modality") == "rf"
                and sensors.get(self.camera, {}).get("modality") == "vision"
                and sensors.get(self.door, {}).get("modality") == "access"
                and sensors[self.door].get("linkedCameraId") == self.camera
                and all(sensors[name].get("floorId") == zone.get("floorId") for name in (self.sensor, self.camera, self.door)))

    def enabled(self):
        with self.gateway.activity.connect() as db:
            active = self.gateway.monitor.configuration.published(db)
        return bool(active and self.mapped(json.loads(active[2])))

    def notifications(self, response, now):
        for note in response.findall(tag(WSN, "NotificationMessage")):
            topic = note.findtext(tag(WSN, "Topic"), "").strip()
            name = "/".join(part.split(":")[-1] for part in topic.split("/"))
            message = note.find(f"./{tag(WSN, 'Message')}/{tag(SCHEMA, 'Message')}")
            if name != self.topic or message is None:
                continue
            operation = message.get("PropertyOperation")
            stamp = message.get("UtcTime")
            occurred = parse_utc(stamp)
            value = next((row.get("Value") for row in message.findall(f"./{tag(SCHEMA, 'Data')}/{tag(SCHEMA, 'SimpleItem')}")
                          if row.get("Name") == "active"), None)
            if (operation not in ("Initialized", "Changed") or value not in ("true", "false", "1", "0")
                    or occurred is None or not -2 <= now - occurred <= GRACE or occurred < self.last_property_time):
                continue
            active = value in ("true", "1")
            if operation == "Changed" and active and self.active is not True:
                statement = json.dumps([self.sensor, self.profile, occurred])
                event = {"type": "observation", "sensorId": self.sensor, "id": "axis-radar-" + sha256(statement.encode()).hexdigest(),
                         "occurredAt": stamp, "eventType": "rf-detection", "summary": "Radar motion in configured gate area"}
                self.gateway.telemetry.push(event, radar=self)
            self.active, self.last_property_time = active, occurred

    def status(self, state):
        with self.gateway.activity.connect() as db:
            db.execute("INSERT OR REPLACE INTO site_radar_status VALUES(?,?,?)", (self.sensor, state, time.time()))

    def poll(self, stopping):
        while not stopping.is_set():
            try:
                if not self.enabled():
                    self.status("unmapped")
                    stopping.wait(5)
                    continue
                endpoint = self.local_endpoint(self.device.services().get(EVENTS))
                properties = self.request(endpoint, "GetEventProperties", lambda operation: None)
                path = "./" + tag(WSTOP, "TopicSet") + "/" + "/".join(tag(TOPIC_NS, name) for name in self.topic.split("/"))
                if properties.find(path) is None:
                    self.status("unsupported")
                    stopping.wait(5)
                    continue
                clock = self.device.clock()
                if clock is None or abs(clock["clockOffsetSeconds"]) > 2:
                    self.status("clock-unverified")
                    stopping.wait(5)
                    continue
                target, refs, ttl = self.subscription(endpoint)
                deadline = time.monotonic() + ttl
                while not stopping.is_set() and time.monotonic() < deadline and self.enabled():
                    def limits(operation):
                        add(operation, EVENTS, "Timeout", "PT2S")
                        add(operation, EVENTS, "MessageLimit", "32")
                    result = self.request(target, "PullMessages", limits, manager=True, references=refs)
                    self.notifications(result, time.time())
                    self.status("ready")
                    stopping.wait(.25)
            except (CameraError, OSError, ValueError, sqlite3.Error):
                self.status("disconnected")
                stopping.wait(2)


class SensorFusion:
    def __init__(self, gateway, records):
        self.gateway, self.activity = gateway, gateway.activity
        if not isinstance(records, list) or len(records) > 12 or any(not isinstance(record, dict) for record in records):
            raise CameraError("Configure up to twelve private gate radar enrollments.")
        self.gates = [GateRadar(self, record) for record in records]
        keys = ("sensor", "boundary", "camera", "door")
        if any(len({getattr(gate, key) for gate in self.gates}) != len(self.gates) for key in keys):
            raise CameraError("Use a distinct radar, boundary, camera and door per pilot gate.")
        with self.activity.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS site_radar_status (sensor TEXT PRIMARY KEY, state TEXT NOT NULL, checked REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS site_fusion_signals (
                    boundary TEXT NOT NULL, version INTEGER NOT NULL, sensor TEXT NOT NULL, source TEXT NOT NULL,
                    occurred REAL NOT NULL, received REAL NOT NULL, trusted INTEGER NOT NULL, body TEXT NOT NULL,
                    PRIMARY KEY(boundary,version,sensor,source)
                );
                CREATE INDEX IF NOT EXISTS fusion_gate_time ON site_fusion_signals(boundary,version,occurred);
                CREATE INDEX IF NOT EXISTS fusion_signal_expiry ON site_fusion_signals(occurred);
                CREATE TABLE IF NOT EXISTS site_fusion_episodes (
                    incident TEXT PRIMARY KEY, camera TEXT NOT NULL, boundary TEXT NOT NULL,
                    version INTEGER NOT NULL, observed REAL NOT NULL, due REAL NOT NULL,
                    anchor TEXT NOT NULL, result TEXT
                );
                CREATE INDEX IF NOT EXISTS fusion_due ON site_fusion_episodes(due) WHERE result IS NULL;
                CREATE TABLE IF NOT EXISTS site_fusion_reviews (
                    incident TEXT PRIMARY KEY, subject TEXT NOT NULL, reviewer TEXT NOT NULL,
                    outcome TEXT NOT NULL, reviewed REAL NOT NULL
                );
            """)

    def observe(self, db, config, version, event, occurred, detail, alerts):
        sensor, kind = event["sensorId"], event["eventType"]
        gate = next((row for row in self.gates if sensor in (row.sensor, row.camera, row.door)
                     and any(zone["id"] == row.boundary for zone in detail["boundaries"]) and row.mapped(config)), None)
        if not gate or not -2 <= event["gatewayReceivedAt"] - occurred <= GRACE or time.time() - occurred > GRACE + WINDOW:
            return
        if sensor == gate.sensor and event.get("radarEvidence"):
            label, trusted = "radar", True
        elif sensor == gate.camera and kind == "line-crossing":
            label = "camera"
            worker = db.execute("SELECT updated_at,body FROM analytics_workers WHERE camera=?", (gate.camera,)).fetchone()
            trusted = bool(event.get("nativeAnalytics") or (
                event.get("analytics", {}).get("source") == "live" and worker and abs(worker[0] - occurred) <= 30
                and json.loads(worker[1])["state"] == "running"))
        elif sensor == gate.door and kind in ("door-forced-open", "door-held-open"):
            label, trusted = "door", gate.door_clock_verified
        else:
            return
        evidence = {"sensorId": sensor, "eventId": event["id"], "occurredAt": event["occurredAt"],
                    "receivedAt": event["gatewayReceivedAt"], "source": label, "summary": detail["observation"]["summary"], "trustedClock": trusted}
        db.execute("INSERT OR IGNORE INTO site_fusion_signals VALUES(?,?,?,?,?,?,?,?)",
                   (gate.boundary, version, sensor, event["id"], occurred, event["gatewayReceivedAt"], int(trusted), json.dumps(evidence)))
        if label != "door":
            return
        root = next((alert for alert in alerts if alert["zoneId"] == gate.boundary), None)
        if not root:
            return
        identifier = root.get("correlation", {}).get("incidentId", root["id"])
        db.execute("INSERT OR IGNORE INTO site_fusion_episodes VALUES(?,?,?,?,?,?,?,NULL)",
                   (identifier, gate.camera, gate.boundary, version, occurred, occurred + WINDOW + GRACE, json.dumps(evidence)))

    def complete(self, now):
        with self.activity.connect() as db:
            due = db.execute("""SELECT incident,camera,boundary,version,observed,due,anchor FROM site_fusion_episodes
                                WHERE result IS NULL AND due<=? ORDER BY due LIMIT 25""", (now,)).fetchall()
        if not due:
            return
        with self.gateway.lock:
            keys = {camera: entry["key"] for camera, entry in self.gateway.cameras.items()}
        video = {camera: self.gateway.video_status(keys[camera]) for _, camera, *_ in due if camera in keys}
        with self.activity.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for identifier, camera, boundary, version, observed, cutoff, raw in due:
                anchor = json.loads(raw)
                gate = next((row for row in self.gates if row.boundary == boundary and row.camera == camera), None)
                if not gate:
                    continue
                signals = [json.loads(row[0]) for row in db.execute(
                    """SELECT body FROM site_fusion_signals WHERE boundary=? AND version=? AND occurred BETWEEN ? AND ?
                       AND received<=? ORDER BY occurred,source LIMIT 60""", (boundary, version, observed - WINDOW, observed + WINDOW, cutoff))]
                support = {name: [row for row in signals if row["source"] == name and row["trustedClock"]] for name in ("camera", "radar")}
                radar = db.execute("SELECT state,checked FROM site_radar_status WHERE sensor=?", (gate.sensor,)).fetchone()
                radar_ready = bool(radar and radar[0] == "ready" and abs(radar[1] - cutoff) <= 30)
                worker = db.execute("SELECT updated_at,body FROM analytics_workers WHERE camera=?", (camera,)).fetchone()
                native = db.execute("SELECT state,checked FROM native_camera_status WHERE camera=?", (camera,)).fetchone()
                body = json.loads(worker[1]) if worker else {}
                worker_ready = bool(worker and body.get("version") == version and body.get("state") == "running"
                                    and abs(worker[0] - cutoff) <= 30)
                native_ready = bool(native and native[0] == "supported" and abs(native[1] - cutoff) <= 20)
                video_ready = ((video.get(camera) or {}).get("state") == "receiving" and now - cutoff <= 30
                               and (worker_ready or native_ready))
                multiple = (len(signals) >= 60 or sum(row["source"] == "door" for row in signals) > 1
                            or any(len(rows) > 1 for rows in support.values()))
                untrusted = any(not row["trustedClock"] for row in signals)
                if multiple:
                    state, reason = "ambiguous", "Multiple events could refer to different activity in this gate window."
                elif anchor["trustedClock"] and any(support.values()):
                    state, reason = "corroborated", "Independent sources reported activity in the same gate window."
                elif untrusted or not radar_ready or not video_ready or not anchor["trustedClock"]:
                    state, reason = "insufficient-evidence", "A required source was unavailable or its clock was unverified."
                else:
                    state, reason = "single-source", "Only the access event was observed in this gate window."
                maintenance = db.execute("SELECT 1 FROM shared_maintenance WHERE sensor IN (?,?,?) AND active=1 AND starts<=? AND ends>? LIMIT 1",
                                         (gate.sensor, gate.camera, gate.door, observed, observed)).fetchone()
                result = {"state": state, "reason": reason, "evidence": signals, "cameraAvailable": video_ready,
                          "radarAvailable": radar_ready, "maintenance": bool(maintenance)}
                db.execute("UPDATE site_fusion_episodes SET result=? WHERE incident=? AND result IS NULL", (json.dumps(result), identifier))

    def poll(self):
        stopping = self.gateway.stopping
        cleanup = 0
        while not stopping.wait(2):
            try:
                now = time.time()
                self.complete(now)
                if now - cleanup >= 3600:
                    with self.activity.connect() as db:
                        db.execute("DELETE FROM site_fusion_signals WHERE occurred<?", (now - MAX_AGE,))
                        db.execute("DELETE FROM site_fusion_reviews WHERE reviewed<?", (now - MAX_AGE,))
                        db.execute("DELETE FROM site_fusion_episodes WHERE observed<?", (now - MAX_AGE,))
                    cleanup = now
            except sqlite3.Error:
                stopping.wait(2)

    @staticmethod
    def lookup(db, actor, identifier):
        row = db.execute("SELECT camera,boundary,version,observed,anchor,result FROM site_fusion_episodes WHERE incident=?", (identifier,)).fetchone()
        if not row or not SiteCollaboration.can_view(actor, row[0]):
            return None
        review = db.execute("SELECT reviewer,outcome,reviewed FROM site_fusion_reviews WHERE incident=?", (identifier,)).fetchone()
        return {"cameraId": row[0], "boundaryId": row[1], "configurationVersion": row[2], "occurredAt": row[3],
                **(json.loads(row[5]) if row[5] else {"state": "collecting", "evidence": [json.loads(row[4])]}),
                "review": {"reviewer": review[0], "outcome": review[1], "at": review[2]} if review else None}

    def read(self, actor, identifier):
        if identifier:
            with self.activity.connect() as db:
                return {"enabled": True, "episode": self.lookup(db, actor, identifier)}
        with self.activity.connect() as db:
            published = self.gateway.monitor.configuration.published(db)
            names = {row["id"]: row["name"] for row in json.loads(published[2])["sensors"]} if published else {}
            receivers = dict((sensor, (state, checked)) for sensor, state, checked in db.execute("SELECT * FROM site_radar_status"))
        result = []
        for gate in self.gates:
            if not SiteCollaboration.can_view(actor, gate.camera):
                continue
            state, checked = receivers.get(gate.sensor, ("disconnected", 0))
            result.append({"cameraId": gate.camera, "boundaryId": gate.boundary, "sensorId": gate.sensor, "sensorName": names.get(gate.sensor, gate.sensor),
                           "state": state if time.time() - checked <= 30 else "disconnected", "checkedAt": checked or None})
        return {"enabled": True, "gates": result}

    def review(self, actor, payload):
        identifier, outcome = payload.get("incidentId"), payload.get("outcome")
        if not isinstance(identifier, str) or outcome not in OUTCOMES:
            raise CameraError("Choose an episode and valid review label.")
        if actor["role"] == "viewer":
            raise PermissionError("Your role cannot review gate evidence.")
        with self.activity.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            episode = self.lookup(db, actor, identifier)
            if not episode:
                raise PermissionError("This episode is unavailable to your account.")
            if episode["state"] == "collecting":
                raise Conflict("Let the gate evidence window finish before reviewing.")
            prior = db.execute("SELECT subject FROM site_fusion_reviews WHERE incident=?", (identifier,)).fetchone()
            if prior and prior[0] != actor["subject"] and not manages_site(actor):
                raise Conflict("A different operator has reviewed this episode.")
            db.execute("INSERT OR REPLACE INTO site_fusion_reviews VALUES(?,?,?,?,?)", (identifier, actor["subject"], actor["name"], outcome, time.time()))
            return {"enabled": True, "episode": self.lookup(db, actor, identifier)}
