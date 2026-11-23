"""Durable camera events, scoped viewer links, and gateway video outage delivery."""

from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
import json
import re
from secrets import token_hex
import sqlite3
import time

from onvif import CameraError

EVENT_LABELS = {"motion": "Camera motion", "line-crossing": "Camera line crossing", "tamper": "Camera tampering"}


class SiteActivity:
    def __init__(self, database):
        database.touch(mode=0o600, exist_ok=True)
        self.database = database
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS camera_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT, camera TEXT NOT NULL,
                    event_id TEXT NOT NULL, occurred_at TEXT NOT NULL, kind TEXT NOT NULL, UNIQUE(camera,event_id)
                );
                CREATE INDEX IF NOT EXISTS camera_events_camera ON camera_events(camera,sequence);
                CREATE TABLE IF NOT EXISTS viewer_links (
                    digest TEXT PRIMARY KEY, camera TEXT NOT NULL, expires INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS site_viewer_issuers (digest TEXT PRIMARY KEY, subject TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS video_outage (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1), incident TEXT NOT NULL, devices TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS notification_outbox (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT, payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS activity_audit (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT, occurred_at TEXT NOT NULL, actor TEXT NOT NULL,
                    action TEXT NOT NULL, resource TEXT NOT NULL, detail TEXT NOT NULL
                );
            """)

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.database, timeout=5)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def push(self, camera, payload):
        event = self.camera_event(payload)
        with self.connect() as db:
            return self.write_camera_event(db, camera, event)

    @staticmethod
    def camera_event(payload):
        identifier, occurred, kind = (payload.get(field) for field in ("id", "occurredAt", "event"))
        if not isinstance(identifier, str) or not identifier.strip() or len(identifier) > 112:
            raise CameraError("Supply a stable camera event ID up to 112 characters.")
        if not isinstance(kind, str) or kind not in EVENT_LABELS:
            raise CameraError("Supported camera events are motion, line-crossing, and tamper.")
        try:
            timestamp = datetime.fromisoformat(occurred.replace("Z", "+00:00"))
            if not timestamp.tzinfo or not time.time() - 604800 <= timestamp.timestamp() <= time.time() + 60:
                raise ValueError()
        except (ValueError, AttributeError, OverflowError):
            raise CameraError("Supply an event timestamp within the last seven days with a timezone.") from None
        return {"id": identifier, "occurredAt": timestamp.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"), "event": kind}

    @staticmethod
    def write_camera_event(db, camera, event):
        result = db.execute("INSERT OR IGNORE INTO camera_events(camera,event_id,occurred_at,kind) VALUES(?,?,?,?)",
                            (camera, event["id"], event["occurredAt"], event["event"]))
        db.execute("DELETE FROM camera_events WHERE sequence <= (SELECT MAX(sequence) - 5000 FROM camera_events)")
        return result.rowcount > 0

    def events(self, camera, after):
        with self.connect() as db:
            rows = db.execute("SELECT sequence,event_id,occurred_at,kind FROM camera_events WHERE camera=? AND sequence>? ORDER BY sequence LIMIT 100",
                              (camera, after)).fetchall()
        return [{"sequence": sequence, "id": "gateway-" + identifier, "occurredAt": occurred, "summary": EVENT_LABELS[kind], "event": kind}
                for sequence, identifier, occurred, kind in rows]

    def issue_viewer(self, camera, minutes, subject=None):
        token = token_hex(24)
        now = int(time.time())
        with self.connect() as db:
            db.execute("DELETE FROM viewer_links WHERE expires <= ?", (now,))
            db.execute("DELETE FROM site_viewer_issuers WHERE digest NOT IN (SELECT digest FROM viewer_links)")
            if db.execute("SELECT COUNT(*) FROM viewer_links").fetchone()[0] >= 500:
                raise CameraError("The gateway has reached its active viewer-link limit.")
            expires = now + minutes * 60
            digest = sha256(token.encode()).hexdigest()
            db.execute("INSERT INTO viewer_links VALUES(?,?,?)", (digest, camera, expires))
            if subject:
                db.execute("INSERT INTO site_viewer_issuers VALUES(?,?)", (digest, subject))
        return {"streamPath": f"view/{token}/index.m3u8", "expiresAt": expires}

    def viewer(self, token):
        with self.connect() as db:
            row = db.execute("SELECT camera FROM viewer_links WHERE digest=? AND expires>?",
                             (sha256(token.encode()).hexdigest(), int(time.time()))).fetchone()
        return row[0] if row else None

    def group_outage(self, unavailable, receiving):
        with self.connect() as db:
            prior = db.execute("SELECT incident,devices FROM video_outage").fetchone()
            if not prior and unavailable:
                incident = token_hex(12)
                db.execute("INSERT INTO video_outage VALUES(1,?,?)", (incident, json.dumps(sorted(unavailable))))
                self.enqueue(db, incident, "started", unavailable)
            elif prior:
                incident, stored = prior
                devices = set(json.loads(stored)) | unavailable
                if devices <= receiving:
                    db.execute("DELETE FROM video_outage WHERE singleton=1")
                    self.enqueue(db, incident, "recovered", devices)
                elif devices != set(json.loads(stored)):
                    db.execute("UPDATE video_outage SET devices=? WHERE singleton=1", (json.dumps(sorted(devices)),))
                    self.enqueue(db, incident, "updated", devices)

    def enqueue(self, db, incident, state, devices):
        payload = {"type": "gateway-video-outage", "incidentId": incident, "state": state, "deviceIds": sorted(devices),
                   "occurredAt": datetime.now(timezone.utc).isoformat(), "deliveryId": token_hex(16)}
        self.enqueue_notification(db, payload)

    @staticmethod
    def enqueue_notification(db, payload):
        db.execute("INSERT INTO notification_outbox(payload) VALUES(?)", (json.dumps(payload),))
        db.execute("DELETE FROM notification_outbox WHERE sequence <= (SELECT MAX(sequence)-1000 FROM notification_outbox)")

    def next_notification(self):
        with self.connect() as db:
            return db.execute("SELECT sequence,payload FROM notification_outbox ORDER BY sequence LIMIT 1").fetchone()

    def sent(self, sequence):
        with self.connect() as db:
            db.execute("DELETE FROM notification_outbox WHERE sequence=?", (sequence,))

    def audit(self, actor, action, resource, detail=""):
        with self.connect() as db:
            db.execute("INSERT INTO activity_audit(occurred_at,actor,action,resource,detail) VALUES(?,?,?,?,?)",
                       (datetime.now(timezone.utc).isoformat(), actor, action, resource, detail))


def valid_event_device(value):
    return isinstance(value, str) and bool(re.fullmatch(r"[A-Za-z0-9_-]{1,96}", value))
