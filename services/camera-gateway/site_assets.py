"""Shared assistance for site dependencies and commissioning records."""

import json
from hashlib import sha256
import time

from onvif import CameraError
from site_accounts import manages_site
from site_activity import valid_event_device
from site_collaboration import Conflict, iso_now, text_field
from site_configuration import SiteConfiguration, endpoint
from rule_acceptance import fingerprint as acceptance_fingerprint, qualification


def rule_fingerprint(camera, boundary, rule, models):
    camera_fields = {key: camera.get(key) for key in ("position", "floorId", "cameraView", "cameraFeedUrl", "cameraGridUrl",
                                                      "cameraProvider", "cameraChannel", "analyticsPlacement")}
    return sha256(json.dumps([camera_fields, boundary["vertices"], boundary.get("floorId"), rule, models],
                             sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class SiteAssets:
    def __init__(self, activity):
        self.activity = activity
        with activity.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS outage_tickets (
                    dependency TEXT NOT NULL, outage_start TEXT NOT NULL, url TEXT NOT NULL, actor TEXT NOT NULL,
                    PRIMARY KEY(dependency,outage_start)
                );
                CREATE TABLE IF NOT EXISTS commissioning_results (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT, version INTEGER NOT NULL, sensor TEXT NOT NULL,
                    camera TEXT NOT NULL, boundary TEXT NOT NULL, actor TEXT NOT NULL, created TEXT NOT NULL, result TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS commissioning_by_camera
                    ON commissioning_results(version,boundary,camera,sequence);
                CREATE TABLE IF NOT EXISTS validated_camera_rules (
                    camera TEXT NOT NULL, rule TEXT NOT NULL, fingerprint TEXT NOT NULL, created TEXT NOT NULL,
                    actor TEXT NOT NULL, note TEXT NOT NULL, PRIMARY KEY(camera,rule,fingerprint)
                );
            """)

    def status(self, actor):
        from site_monitoring import scoped_inventory
        with self.activity.connect() as db:
            db.execute("BEGIN")
            active = SiteConfiguration.published(db)
            if not active:
                return {"tickets": [], "commissioning": [], "recordingChecks": []}
            snapshot = scoped_inventory(json.loads(active[2]), actor)
            dependencies = {item["id"] for item in snapshot["dependencies"]}
            tickets = [{"dependencyId": key, "outageStart": start, "url": url, "actor": author}
                       for key, start, url, author in db.execute("SELECT * FROM outage_tickets ORDER BY outage_start DESC LIMIT 500") if key in dependencies]
            commissioning = [{"version": version, "sensorId": sensor, "cameraId": camera, "boundaryId": boundary,
                              "actor": author, "createdAt": created, **json.loads(result)}
                             for version, sensor, camera, boundary, author, created, result in db.execute(
                               "SELECT version,sensor,camera,boundary,actor,created,result FROM commissioning_results ORDER BY sequence DESC LIMIT 100")
                             if actor["cameras"] == "*" or camera in actor["cameras"]]
            allowed_cameras = {item["id"] for item in snapshot["sensors"] if item["modality"] == "vision"}
            recording_checks = [{"version": active[0], "cameraId": camera, "boundaryId": boundary, "createdAt": created,
                                 "recording": json.loads(result)["checks"]["recording"]}
                                for camera, boundary, created, result in db.execute("""
                                    SELECT camera,boundary,created,result FROM commissioning_results
                                    WHERE sequence IN (SELECT max(sequence) FROM commissioning_results
                                        WHERE version=? GROUP BY boundary,camera)
                                """, (active[0],)) if camera in allowed_cameras]
            rules = self.validated_rules(db, snapshot, active[0])
            acceptances = self.acceptances(db, snapshot, active[0])
        return {"tickets": tickets, "commissioning": commissioning, "recordingChecks": recording_checks,
                "validatedRules": rules, "ruleAcceptances": acceptances}

    @staticmethod
    def acceptances(db, snapshot, version):
        zones = {item["id"]: item for item in snapshot["boundaries"]}
        records = []
        for camera in snapshot["sensors"]:
            for rule in camera.get("analyticsRules", ()):
                if not (boundary := zones.get(rule["boundaryId"])):
                    continue
                key = acceptance_fingerprint(db, version, camera, boundary, rule, snapshot.get("analyticsTimeZone"))
                row = db.execute("SELECT created,actor,note,measurements FROM camera_rule_acceptance WHERE camera=? AND rule=? AND fingerprint=?",
                                 (camera["id"], rule["id"], key)).fetchone() if key else None
                if row:
                    records.append({"cameraId": camera["id"], "ruleId": rule["id"], "createdAt": row[0],
                                    "actor": row[1], "note": row[2], **json.loads(row[3])})
        return records

    @staticmethod
    def fingerprint(db, version, camera, boundary, rule):
        worker = db.execute("SELECT body FROM analytics_workers WHERE camera=?", (camera["id"],)).fetchone()
        state = json.loads(worker[0]) if worker and not rule.get("cameraNative") else {}
        models = state.get("models", []) if state.get("version") == version else []
        return rule_fingerprint(camera, boundary, rule, models)

    def validated_rules(self, db, snapshot, version):
        boundaries = {item["id"]: item for item in snapshot["boundaries"]}
        records = []
        for camera in snapshot["sensors"]:
            for rule in camera.get("analyticsRules", ()):
                if not (boundary := boundaries.get(rule["boundaryId"])):
                    continue
                fingerprint = self.fingerprint(db, version, camera, boundary, rule)
                row = db.execute("SELECT created,actor,note FROM validated_camera_rules WHERE camera=? AND rule=? AND fingerprint=?",
                                 (camera["id"], rule["id"], fingerprint)).fetchone()
                if row:
                    records.append({"cameraId": camera["id"], "ruleId": rule["id"], "createdAt": row[0], "actor": row[1], "note": row[2]})
        return records

    def change(self, actor, payload):
        operation = payload.get("operation")
        if operation != "check-camera" and not manages_site(actor):
            raise PermissionError("A site supervisor is required to update site assets.")
        with self.activity.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            active = SiteConfiguration.published(db)
            if not active or active[0] != payload.get("version"):
                raise Conflict("The published configuration changed. Refresh the site first.")
            version, _, body = active
            site = json.loads(body)
            if operation == "check-camera":
                resource = payload.get("cameraId")
                if actor["role"] == "viewer" or not valid_event_device(resource) or actor["cameras"] != "*" and resource not in actor["cameras"]:
                    raise PermissionError("This operator cannot update the camera inspection.")
                camera = next((item for item in site["sensors"] if item["id"] == resource and item["modality"] == "vision"), None)
                if not camera:
                    raise CameraError("Choose a published camera.")
                policy = payload.get("mode")
                previous = db.execute("SELECT verified_at,inspection FROM camera_checks WHERE camera=? AND version=?", (resource, version)).fetchone()
                verified, inspection = previous or (camera.get("cameraVerifiedAt"), None)
                if policy == "verify" and camera["cameraView"]:
                    verified = iso_now()
                elif policy == "clear":
                    inspection = None
                elif policy == "inspect" and payload.get("issue") in ("frozen", "obstructed", "dark", "defocused"):
                    inspection = json.dumps({"issue": payload["issue"], "confirmedAt": iso_now(), "note": text_field(payload, "note", 250, True)})
                else:
                    raise CameraError("Choose an inspection issue or verify a configured field of view.")
                db.execute("INSERT OR REPLACE INTO camera_checks VALUES(?,?,?,?,?)", (resource, version, verified, inspection, time.time()))
            elif operation == "link-ticket":
                resource, start, url = payload.get("dependencyId"), text_field(payload, "outageStart", 80), endpoint(payload.get("url"))
                if not valid_event_device(resource) or not any(item["id"] == resource for item in site["dependencies"]) or not url:
                    raise CameraError("Choose a published dependency and an HTTP(S) maintenance ticket.")
                db.execute("INSERT INTO outage_tickets VALUES(?,?,?,?) ON CONFLICT(dependency,outage_start) DO UPDATE SET url=excluded.url,actor=excluded.actor",
                           (resource, start, url, actor["subject"]))
            elif operation == "commission":
                resource, camera, boundary = (payload.get(field) for field in ("sensorId", "cameraId", "boundaryId"))
                if not all(map(valid_event_device, (resource, camera, boundary))):
                    raise CameraError("Select a door, its linked camera, and a boundary.")
                sensors = {item["id"]: item for item in site["sensors"]}
                area = next((item for item in site["boundaries"] if item["id"] == boundary), None)
                device = sensors.get(resource, {})
                if device.get("modality") != "access" or camera not in sensors or device.get("linkedCameraId") != camera or not area or not area["enabled"] or not {resource, camera} <= set(area["sensorIds"]):
                    raise CameraError("The door and its linked camera must share one enabled boundary.")
                tests = payload.get("checks")
                required = ("door", "incident", "recording", "notification")
                if not isinstance(tests, dict) or not all(type(tests.get(key)) is bool for key in required):
                    raise CameraError("Record each commissioning check.")
                result = {"checks": {key: tests[key] for key in required}, "note": text_field(payload, "note", 300, True),
                          "passed": all(tests[key] for key in required)}
                db.execute("INSERT INTO commissioning_results(version,sensor,camera,boundary,actor,created,result) VALUES(?,?,?,?,?,?,?)",
                           (version, resource, camera, boundary, actor["subject"], iso_now(), json.dumps(result)))
            elif operation in ("verify-rule", "accept-rule"):
                resource = text_field(payload, "cameraId", 80)
                camera = next((row for row in site["sensors"] if row["id"] == resource and row["modality"] == "vision"), None)
                selected = next((rule for rule in (camera or {}).get("analyticsRules", ()) if rule["id"] == payload.get("ruleId")), None)
                boundary = next((row for row in site["boundaries"] if selected and row["id"] == selected["boundaryId"] and row["enabled"]), None)
                if not selected or not boundary:
                    raise CameraError("Choose a published rule for an enabled boundary.")
                note = text_field(payload, "note", 500)
                if operation == "accept-rule":
                    measurements = qualification(payload.get("measurements"))
                    fingerprint = acceptance_fingerprint(db, version, camera, boundary, selected,
                                                         site.get("analyticsTimeZone"), fresh=True)
                    if selected["mode"] != "observe" or not fingerprint:
                        raise Conflict("Publish this rule in Observe and wait for its current worker profile before recording acceptance.")
                    db.execute("INSERT OR REPLACE INTO camera_rule_acceptance VALUES(?,?,?,?,?,?,?,?)",
                               (resource, selected["id"], fingerprint, iso_now(), actor["subject"], note,
                                json.dumps(measurements), int(measurements["passed"])))
                else:
                    fingerprint = self.fingerprint(db, version, camera, boundary, selected)
                    db.execute("INSERT OR REPLACE INTO validated_camera_rules VALUES(?,?,?,?,?,?)",
                               (resource, selected["id"], fingerprint, iso_now(), actor["subject"], note))
            else:
                raise CameraError("Choose an available site asset operation.")
            db.execute("INSERT INTO activity_audit(occurred_at,actor,action,resource,detail) VALUES(?,?,?,?,?)",
                       (iso_now(), actor["subject"], operation, resource, str(version)))
        return self.status(actor)
