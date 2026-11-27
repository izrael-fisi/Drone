"""Site-local drift warnings over existing camera and recording measurements."""

from datetime import datetime
from hashlib import sha256
import json
import sqlite3
from statistics import median
import time

from site_collaboration import SiteCollaboration

BUCKET = 300
DAY = 86400
METRICS = {
    "probe-failures": ("Video probe failure rate", 0.2),
    "frame-age": ("Analysis frame age", 2.0),
    "reporting-gap": ("Analytics reporting gap", 12.0),
    "clock-offset": ("Absolute clock offset", 0.5),
    "recording-failures": ("Recorder recovery failure rate", 0.2),
}


def regime(zone, timestamp):
    return "day" if 6 <= datetime.fromtimestamp(timestamp, zone).hour < 18 else "night"


def threshold(history, floor):
    center = median(history)
    spread = median(abs(value - center) for value in history)
    return center, center + max(floor, 4 * 1.4826 * spread)


class DeviceDegradation:
    def __init__(self, gateway):
        self.gateway = gateway
        self.activity = gateway.activity
        self.zone = gateway.monitor.time_zone
        with self.activity.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS camera_drift_buckets (
                    camera TEXT NOT NULL, fingerprint TEXT NOT NULL, metric TEXT NOT NULL,
                    bucket INTEGER NOT NULL, regime TEXT NOT NULL, total REAL NOT NULL,
                    samples INTEGER NOT NULL, eligible INTEGER NOT NULL,
                    PRIMARY KEY(camera,fingerprint,metric,bucket)
                );
                CREATE TABLE IF NOT EXISTS camera_drift_samples (
                    camera TEXT NOT NULL, fingerprint TEXT NOT NULL, metric TEXT NOT NULL, last_sample REAL NOT NULL,
                    PRIMARY KEY(camera,fingerprint,metric)
                );
                CREATE TABLE IF NOT EXISTS camera_drift_state (
                    camera TEXT NOT NULL, metric TEXT NOT NULL, fingerprint TEXT NOT NULL,
                    checked INTEGER NOT NULL, state TEXT NOT NULL, abnormal INTEGER NOT NULL, normal INTEGER NOT NULL,
                    value REAL, expected REAL, limit_value REAL, PRIMARY KEY(camera,metric)
                );
                CREATE TABLE IF NOT EXISTS camera_drift_warnings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, camera TEXT NOT NULL, metric TEXT NOT NULL,
                    fingerprint TEXT NOT NULL, opened INTEGER NOT NULL, recovered INTEGER, reason TEXT
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_camera_drift_warning
                    ON camera_drift_warnings(camera,metric) WHERE recovered IS NULL;
                CREATE INDEX IF NOT EXISTS camera_drift_warning_history ON camera_drift_warnings(camera,opened);
            """)

    @staticmethod
    def ingest(db, camera, fingerprint, metric, bucket, period, sample_at, value, eligible):
        last = db.execute("SELECT last_sample FROM camera_drift_samples WHERE camera=? AND fingerprint=? AND metric=?",
                          (camera, fingerprint, metric)).fetchone()
        if last and last[0] >= sample_at:
            return
        db.execute("INSERT OR REPLACE INTO camera_drift_samples VALUES(?,?,?,?)", (camera, fingerprint, metric, sample_at))
        db.execute("""
            INSERT INTO camera_drift_buckets VALUES(?,?,?,?,?,?,1,?)
            ON CONFLICT(camera,fingerprint,metric,bucket) DO UPDATE SET
            total=total+excluded.total,samples=samples+1,eligible=min(eligible,excluded.eligible)
        """, (camera, fingerprint, metric, bucket, period, value, int(eligible)))

    @staticmethod
    def evaluate(db, camera, fingerprint, metric, at, period):
        prior = db.execute("""SELECT fingerprint,checked,abnormal,normal FROM camera_drift_state
                              WHERE camera=? AND metric=?""", (camera, metric)).fetchone()
        if prior and prior[:2] == (fingerprint, at):
            return
        abnormal, normal = prior[2:] if prior and prior[0] == fingerprint and prior[1] + BUCKET == at else (0, 0)
        if prior and prior[0] != fingerprint:
            db.execute("""UPDATE camera_drift_warnings SET recovered=?,reason='configuration changed'
                          WHERE camera=? AND metric=? AND recovered IS NULL""", (at, camera, metric))
        current = db.execute("""SELECT total/samples,samples,eligible FROM camera_drift_buckets
                                WHERE camera=? AND fingerprint=? AND metric=? AND bucket=?""",
                             (camera, fingerprint, metric, at)).fetchone()
        expected, limit, state = None, None, "insufficient-data"
        value = current[0] if current else None
        if current and current[1] >= 2 and current[2]:
            rows = db.execute("""SELECT bucket,total/samples FROM camera_drift_buckets WHERE camera=? AND fingerprint=?
                                AND metric=? AND bucket>=? AND bucket<? AND regime=? AND eligible=1 AND samples>=2""",
                              (camera, fingerprint, metric, at - 14 * DAY, at, period)).fetchall()
            if len({start // DAY for start, _ in rows}) >= 7 and len(rows) >= 48:
                expected, limit = threshold([score for _, score in rows], METRICS[metric][1])
                state = "abnormal" if value > limit else "normal"
            else:
                state = "learning"
        if state == "abnormal":
            db.execute("UPDATE camera_drift_buckets SET eligible=0 WHERE camera=? AND fingerprint=? AND metric=? AND bucket=?",
                       (camera, fingerprint, metric, at))
        abnormal = abnormal + 1 if state == "abnormal" else 0
        normal = normal + 1 if state == "normal" else 0
        if abnormal >= 3:
            db.execute("INSERT OR IGNORE INTO camera_drift_warnings(camera,metric,fingerprint,opened) VALUES(?,?,?,?)",
                       (camera, metric, fingerprint, at + BUCKET))
        if normal >= 3:
            db.execute("""UPDATE camera_drift_warnings SET recovered=?,reason='normal'
                          WHERE camera=? AND metric=? AND fingerprint=? AND recovered IS NULL""",
                       (at + BUCKET, camera, metric, fingerprint))
        db.execute("INSERT OR REPLACE INTO camera_drift_state VALUES(?,?,?,?,?,?,?,?,?,?)",
                   (camera, metric, fingerprint, at, state, abnormal, normal, value, expected, limit))

    def collect(self, now):
        gateway, monitor = self.gateway, self.gateway.monitor
        with gateway.lock:
            registered = {device: entry["key"] for device, entry in gateway.cameras.items()}
        video = {device: gateway.video_status(key) or {} for device, key in registered.items()}
        bucket = int(now // BUCKET) * BUCKET
        with self.activity.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            published = monitor.configuration.published(db)
            if not published:
                return
            version, _, raw = published
            policy = json.loads(raw)
            workers = {camera: json.loads(body) for camera, body in db.execute("SELECT camera,body FROM analytics_workers")}
            maintenance = {row[0] for row in db.execute("SELECT sensor FROM shared_maintenance WHERE active=1 AND starts<? AND ends>?",
                                                         (bucket + BUCKET, bucket))}
            outage = {camera for (devices,) in db.execute("SELECT devices FROM video_outage") for camera in json.loads(devices)}
            for (raw_cameras,) in db.execute("SELECT cameras FROM boundary_coverage WHERE state='unavailable'"):
                outage.update(json.loads(raw_cameras))
            recorder = {
                camera: (failed, count) for camera, failed, count in db.execute(
                    """SELECT camera,sum(state IN ('gap','unavailable')),count(*) FROM recovery_jobs
                       WHERE updated_at>=? AND state IN ('available','gap','unavailable') GROUP BY camera""", (bucket,))}
            enabled = {sensor["id"]: sensor for sensor in policy["sensors"] if sensor["modality"] == "vision"}
            for device, key in registered.items():
                if device not in enabled:
                    continue
                sensor = enabled[device]
                identity = {name: sensor.get(name) for name in ("position", "cameraView", "analyticsPlacement", "analyticsRules")}
                fingerprint = sha256(json.dumps([key, identity], sort_keys=True).encode()).hexdigest()
                check, worker = video[device], workers.get(device, {})
                eligible = device not in maintenance and device not in outage
                samples = {}
                checked = (check.get("checkedAt") or 0) / 1000
                if checked and check.get("state") in ("receiving", "unavailable"):
                    samples["probe-failures"] = (checked, float(check["state"] == "unavailable"))
                if worker.get("version") == version:
                    report = worker["reportedAt"]
                    samples["reporting-gap"] = (now, min(3600, max(0, now - report)))
                    if worker.get("frameAgeSeconds") is not None and now - report <= 60:
                        samples["frame-age"] = (report, worker["frameAgeSeconds"])
                if (clock := check.get("clockCheckedAt")) and (offset := check.get("clockOffsetSeconds")) is not None:
                    # Clock checks are sparse and contribute only when renewed by the gateway.
                    clock = clock / 1000 if clock > 10**11 else clock
                    samples["clock-offset"] = (clock, abs(offset))
                period = regime(self.zone, bucket)
                for metric, (measured, value) in samples.items():
                    if bucket <= measured < bucket + BUCKET:
                        self.ingest(db, device, fingerprint, metric, bucket, period, measured, value, eligible)
                if device in recorder:
                    failed, count = recorder[device]
                    db.execute("""INSERT OR REPLACE INTO camera_drift_buckets VALUES(?,?,?,?,?,?,?,?)""",
                               (device, fingerprint, "recording-failures", bucket, period, failed, count, int(eligible)))
                if not eligible:
                    db.execute("UPDATE camera_drift_buckets SET eligible=0 WHERE camera=? AND bucket=? AND fingerprint=?",
                               (device, bucket, fingerprint))
                for metric in METRICS:
                    self.evaluate(db, device, fingerprint, metric, bucket - BUCKET, regime(self.zone, bucket - BUCKET))
            db.execute("DELETE FROM camera_drift_buckets WHERE bucket<?", (bucket - 15 * DAY,))
            db.execute("DELETE FROM camera_drift_warnings WHERE recovered<?", (bucket - 30 * DAY,))
            db.execute("DELETE FROM camera_drift_samples WHERE last_sample<?", (bucket - 15 * DAY,))

    def poll(self):
        stopping = self.gateway.stopping
        while not stopping.is_set():
            try:
                self.collect(time.time())
            except sqlite3.Error:
                pass
            stopping.wait(30)

    def read(self, actor):
        now = time.time()
        with self.activity.connect() as db:
            published = self.gateway.monitor.configuration.published(db)
            if not published:
                return {"enabled": True, "cameras": []}
            policy = json.loads(published[2])
            zones = [row for row in policy["boundaries"] if row["enabled"]]
            configured = {row["id"] for row in policy["sensors"] if row["modality"] == "vision"}
            states = db.execute("""SELECT s.camera,s.metric,s.fingerprint,s.checked,s.state,s.value,s.expected,s.limit_value,
                                   w.opened FROM camera_drift_state s LEFT JOIN camera_drift_warnings w
                                   ON s.camera=w.camera AND s.metric=w.metric AND s.fingerprint=w.fingerprint AND w.recovered IS NULL
                                   ORDER BY w.opened IS NULL,s.camera,s.metric""").fetchall()
            result = {}
            for camera, metric, fingerprint, at, state, value, expected, limit, opened in states:
                if camera not in configured or not SiteCollaboration.can_view(actor, camera):
                    continue
                device = result.setdefault(camera, {"cameraId": camera, "metrics": [], "recentRecoveries": [],
                    "boundaries": [{"id": zone["id"], "name": zone["name"]} for zone in zones if camera in zone["sensorIds"]]})
                history = db.execute("""SELECT bucket,total/samples,samples FROM camera_drift_buckets
                                      WHERE camera=? AND fingerprint=? AND metric=? AND bucket<=? ORDER BY bucket DESC LIMIT 12""",
                                     (camera, fingerprint, metric, at)).fetchall()
                device["metrics"].append({"id": metric, "label": METRICS[metric][0],
                                          "state": state if now - at <= 3 * BUCKET else "insufficient-data",
                                          "checkedAt": at, "value": value, "expected": expected, "limit": limit,
                                          "openedAt": opened, "history": [{"at": t, "value": v, "samples": n} for t, v, n in reversed(history)]})
            for camera, value in result.items():
                value["recentRecoveries"] = [{"metric": METRICS[metric][0], "at": recovered, "reason": reason}
                    for metric, recovered, reason in db.execute("""SELECT metric,recovered,reason FROM camera_drift_warnings
                        WHERE camera=? AND recovered IS NOT NULL ORDER BY recovered DESC LIMIT 5""", (camera,))]
        return {"enabled": True, "cameras": list(result.values())}
