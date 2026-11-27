"""A durable cursor and health summary for read-only VMS adapters."""

from hashlib import sha256
import json
import time

from onvif import CameraError
from site_configuration import SiteConfiguration


class ConnectorState:
    def __init__(self, telemetry, provider, origin, playback=False):
        self.telemetry, self.provider, self.playback = telemetry, provider, playback
        self.id = provider + "-" + sha256(origin.encode()).hexdigest()[:16]
        with telemetry.activity.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS vms_cursor (
                    connector TEXT NOT NULL, stream TEXT NOT NULL, position INTEGER NOT NULL, PRIMARY KEY(connector,stream)
                );
                CREATE TABLE IF NOT EXISTS vms_health (
                    connector TEXT PRIMARY KEY, checked REAL, succeeded REAL, accepted REAL,
                    backlog INTEGER NOT NULL DEFAULT 0, rejected INTEGER NOT NULL DEFAULT 0, problem TEXT
                );
            """)
            db.execute("INSERT OR IGNORE INTO vms_health(connector) VALUES(?)", (self.id,))

    def cursor(self, stream, default=0):
        with self.telemetry.activity.connect() as db:
            row = db.execute("SELECT position FROM vms_cursor WHERE connector=? AND stream=?", (self.id, stream)).fetchone()
            return row[0] if row else default

    def check(self, problem=None, backlog=False):
        with self.telemetry.activity.connect() as db:
            now = time.time()
            db.execute("""UPDATE vms_health SET checked=?,succeeded=CASE WHEN ? IS NULL THEN ? ELSE succeeded END,
                       problem=?,backlog=? WHERE connector=?""", (now, problem, now, problem, int(backlog), self.id))

    def ingest(self, stream, position, payload=None, vendor=None, rejected=False):
        from site_telemetry import normalize
        with self.telemetry.activity.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if payload:
                try:
                    record = normalize(payload)
                    from site_monitoring import seconds
                    published = SiteConfiguration.published(db, seconds(record["occurredAt"]))
                    if not published or not any(device["id"] == record["sensorId"] for device in json.loads(published[2])["sensors"]):
                        raise CameraError("Publish the mapped VMS device before accepting its events.")
                    record["vendor"] = {"provider": self.provider, "connectorId": self.id, **vendor}
                except CameraError:
                    rejected = True
                else:
                    db.execute("SAVEPOINT accept_vms_event")
                    try:
                        result = self.telemetry.write(db, record, True)
                    except CameraError:
                        db.execute("ROLLBACK TO accept_vms_event")
                        rejected = True
                    else:
                        if result["accepted"]:
                            db.execute("UPDATE vms_health SET accepted=? WHERE connector=?", (time.time(), self.id))
                    db.execute("RELEASE accept_vms_event")
            if rejected:
                db.execute("UPDATE vms_health SET rejected=rejected+1 WHERE connector=?", (self.id,))
            db.execute("""INSERT INTO vms_cursor VALUES(?,?,?) ON CONFLICT(connector,stream) DO UPDATE SET
                       position=max(position,excluded.position)""", (self.id, stream, position))

    def status(self):
        with self.telemetry.activity.connect() as db:
            row = db.execute("SELECT checked,succeeded,accepted,backlog,rejected,problem FROM vms_health WHERE connector=?", (self.id,)).fetchone()
        return {"id": self.id, "provider": self.provider, "playback": self.playback, "qualification": "vendor-validation-required",
                **dict(zip(("checkedAt", "lastSuccessfulPoll", "lastAcceptedEvent", "backlog", "rejected", "problem"), row))}
