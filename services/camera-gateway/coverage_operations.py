"""Evaluate critical boundaries from current, field-checked camera views."""

from datetime import datetime, timezone
import json
from secrets import token_hex

from site_configuration import SiteConfiguration

LOSS_SECONDS = 60


class CoverageOperations:
    def __init__(self, activity, procedures, organization, site):
        self.procedures, self.organization, self.site = procedures, organization, site
        with activity.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS boundary_coverage (
                boundary TEXT PRIMARY KEY, version INTEGER NOT NULL, usable INTEGER NOT NULL,
                lost_since REAL, incident TEXT, state TEXT NOT NULL, cameras TEXT NOT NULL, updated REAL NOT NULL
            )""")

    def evaluate(self, db, video, now):
        active = SiteConfiguration.published(db)
        if not active:
            return
        version, _, body = active
        config = json.loads(body)
        critical = {item["boundaryId"] for item in config.get("procedures", ()) if item["trigger"] == "coverage-loss"}
        existing = {row[0]: row[1:] for row in db.execute("SELECT boundary,version,usable,lost_since,incident,state,cameras,updated FROM boundary_coverage")}
        checks = dict(db.execute("SELECT camera,verified_at FROM camera_checks WHERE version=?", (version,)))
        assignments = {item["id"]: item for item in config["sensors"] if item["modality"] == "vision" and item.get("cameraView")}
        for boundary in config["boundaries"]:
            key = boundary["id"]
            if key not in critical or not boundary["enabled"]:
                continue
            cameras = sorted(camera for camera in boundary["sensorIds"] if camera in assignments and
                             (checks.get(camera) or assignments[camera].get("cameraVerifiedAt")))
            states = [video.get(camera, "unknown") for camera in cameras]
            prior = existing.get(
                key, (version, False, None, None, "", "[]", 0))
            prior_version, usable, lost_since, incident, _, prior_cameras, _ = prior
            if prior_version != version or json.loads(prior_cameras) != cameras:
                usable, lost_since = False, None
            if "receiving" in states:
                state, usable, lost_since = "available", True, None
                if incident:
                    self.transition(db, incident, now)
                    incident = None
            elif states and all(state == "unavailable" for state in states):
                state = "unavailable"
                if usable:
                    lost_since = now if lost_since is None else lost_since
                    if not incident and now - lost_since >= LOSS_SECONDS:
                        incident = self.open(db, config, boundary, cameras, assignments, version, lost_since, now)
            else:
                state, lost_since = "unverified", None
            current = (version, int(usable), lost_since, incident, state, json.dumps(cameras))
            if current != prior[:-1]:
                db.execute("INSERT OR REPLACE INTO boundary_coverage VALUES(?,?,?,?,?,?,?,?)", (key, *current, now))
        for key in existing.keys() - {item["id"] for item in config["boundaries"] if item["enabled"] and item["id"] in critical}:
            db.execute("DELETE FROM boundary_coverage WHERE boundary=?", (key,))

    def open(self, db, config, boundary, cameras, assignments, version, since, now):
        identifier = "coverage-" + token_hex(16)
        key = boundary["id"]
        camera = cameras[0] if len(cameras) == 1 else None
        maintenance = [device for device in cameras if db.execute(
            "SELECT 1 FROM shared_maintenance WHERE sensor=? AND active=1 AND starts<=? AND ends>?", (device, now, now)).fetchone()]
        affected = sorted({value for device in cameras for value in assignments[device].get("dependencyIds", ())})
        occurred = datetime.fromtimestamp(since, timezone.utc).isoformat()
        detail = {"id": identifier, "zoneId": key, "zoneName": boundary["name"], "cameraId": camera, "severity": "High", "status": "new",
                  "configurationVersion": version, "modality": "vision", "floorId": boundary["floorId"], "origin": "Site gateway",
                  "sensorName": "Boundary coverage", "responseTeam": boundary.get("responseTeam"), "affectedCameras": cameras,
                  "affectedDependencies": affected, "maintenanceCameras": maintenance,
                  "observation": {"id": identifier, "sensorId": camera or key, "position": boundary["vertices"][0],
                                  "occurredAt": occurred, "eventType": "coverage-loss", "summary": "Critical camera coverage lost · " + boundary["name"]}}
        self.record_event(db, detail, identifier)
        db.execute("INSERT INTO monitored_alerts(id,camera,boundary,event_at,version,body) VALUES(?,?,?,?,?,?)",
                   (identifier, camera, key, since, version, json.dumps(detail)))
        self.procedures.start(db, config, version, identifier, "coverage-loss", key, now)
        notice = {"type": "site-security-incident", "eventType": "coverage-loss", "organizationId": self.organization,
                  "siteId": self.site, "incidentId": identifier, "boundaryId": key, "affectedCameras": cameras, "occurredAt": occurred,
                  "severity": "High", "configurationVersion": version, "deliveryId": identifier}
        db.execute("INSERT INTO alert_deliveries(payload) VALUES(?)", (json.dumps(notice),))
        return identifier

    @staticmethod
    def record_event(db, detail, identifier):
        db.execute("INSERT INTO monitored_events(sensor,source_id,camera,body) VALUES(?,?,?,?)",
                   (detail["observation"]["sensorId"], identifier, detail["cameraId"], json.dumps({
                       **detail, "boundaries": [{"id": detail["zoneId"], "name": detail["zoneName"]}]})))

    def transition(self, db, incident, now):
        row = db.execute("SELECT body FROM monitored_alerts WHERE id=?", (incident,)).fetchone()
        if not row:
            return
        detail = json.loads(row[0])
        occurred = datetime.fromtimestamp(now, timezone.utc).isoformat()
        detail["coverageRecoveredAt"] = occurred
        db.execute("UPDATE monitored_alerts SET body=? WHERE id=?", (json.dumps(detail), incident))
        event = {**detail, "observation": {**detail["observation"], "id": incident + "-recovered", "occurredAt": occurred,
                 "eventType": "coverage-recovered", "summary": "Critical camera coverage restored · " + detail["zoneName"]}}
        self.record_event(db, event, event["observation"]["id"])
