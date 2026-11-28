"""Always-on boundary evaluation in the same transaction as accepted telemetry."""

from datetime import datetime, timezone
import json
import re
from secrets import token_hex
import sqlite3
import time

from onvif import CameraError
from site_configuration import SiteConfiguration, normalize_configuration
from site_geometry import contains
from site_telemetry import normalize
from analytics_contract import STATES, detection_summary, effective_rule, matches_evidence, number, policy_zone
from analytics_review import AnalyticsReview
from inspection_results import InspectionResults
from dock_schedules import DockSchedules
from door_passages import ACCESS_FACTS, DoorPassages
from native_camera_events import NativeEvents
from site_accounts import manages_site


def encode(value):
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def seconds(value):
    return datetime.fromisoformat(value).timestamp()


def camera_for(sensor):
    return sensor["id"] if sensor["modality"] == "vision" else sensor.get("linkedCameraId")


def dock_snapshots(raw, policy, now):
    enabled = {rule["id"] for rule in policy["rules"] if rule.get("trackOccupancy")}
    if not isinstance(raw, list) or len(raw) > len(enabled):
        raise CameraError("Provide one dock snapshot per active dock rule.")
    result = {}
    for item in raw:
        if not isinstance(item, dict):
            raise CameraError("Invalid dock snapshot.")
        rule_id, state, sampled, changed, event = (
            item.get(key) for key in ("ruleId", "state", "sampledAt", "changedAt", "arrivalEventId"))
        if (not isinstance(rule_id, str) or rule_id not in enabled or rule_id in result
                or state not in ("unknown", "vacant", "occupied") or not number(sampled, 0, now + 2)
                or (state == "unknown" and changed is not None)
                or (state != "unknown" and not number(changed, 0, sampled))
                or event is not None and (state != "occupied" or not isinstance(event, str)
                                         or not re.fullmatch(r"cctv-[a-f0-9]{32}", event))):
            raise CameraError("Dock status must match a current rule and valid sample time.")
        result[rule_id] = {"ruleId": rule_id, "state": state, "sampledAt": sampled,
                           **({"changedAt": changed} if changed is not None else {}),
                           **({"arrivalEventId": event} if event is not None else {})}
    return result


def scoped_inventory(snapshot, actor):
    if actor["cameras"] == "*":
        return snapshot
    allowed = set(actor["cameras"])
    sensors = [sensor for sensor in snapshot["sensors"] if camera_for(sensor) in allowed]
    sensor_ids = {sensor["id"] for sensor in sensors}
    zones = [{**zone, "sensorIds": [device for device in zone["sensorIds"] if device in sensor_ids]} for zone in snapshot["boundaries"]
             if sensor_ids.intersection(zone["sensorIds"])]
    visible_floors = {item.get("floorId") for item in zones + sensors}
    visible_dependencies = {dep for sensor in sensors for dep in sensor.get("dependencyIds", [])}
    group_ids = {sensor.get("groupId") for sensor in sensors}
    return {**snapshot, "sensors": sensors, "boundaries": zones, "groups": [item for item in snapshot["groups"] if item["id"] in group_ids],
            "floors": [item for item in snapshot["floors"] if item["id"] in visible_floors],
            "dependencies": [item for item in snapshot["dependencies"] if item["id"] in visible_dependencies]}


def evaluate(config, event, version, zone=timezone.utc):
    sensor = next((item for item in config["sensors"] if item["id"] == event["sensorId"]), None)
    evidence = event.get("analytics")
    native = event.get("nativeAnalytics")
    inspection = event.get("inspection")
    if inspection and (not sensor or sensor["modality"] != "vision" or sensor.get("inspectionStationId") != inspection["stationId"]):
        raise CameraError("This station result does not match the camera configuration published at the inspection time.")
    rule = None
    if evidence:
        rule = next((rule for rule in (sensor or {}).get("analyticsRules", []) if rule["id"] == evidence["ruleId"]), None)
        if rule:
            rule = effective_rule(rule, seconds(event["occurredAt"]), zone)
        if not rule or rule.get("cameraNative") or version != evidence["policyVersion"] or rule["kind"] != event["eventType"] or not matches_evidence(rule, evidence):
            raise CameraError("CCTV evidence does not match the camera rule published at the observation time.")
    if native:
        rule = next((row for row in (sensor or {}).get("analyticsRules", ()) if row["id"] == native["ruleId"] and row.get("cameraNative")), None)
        if not rule or not any(row["id"] == rule["boundaryId"] and row["enabled"] for row in config["boundaries"]):
            raise CameraError("The camera-native rule was not enabled at the observation time.")
    if not sensor:
        return None, []
    modality = sensor["modality"]
    if event["eventType"] in ACCESS_FACTS and modality != "access":
        raise CameraError("Only enrolled access devices can report passage events.")
    position = event.get("position")
    basis = "reported" if position else None
    radar = event.get("radarEvidence")
    if position is None and (modality in ("vision", "access") or radar):
        position, basis = sensor["position"], "device"
    camera = radar["cameraId"] if radar else camera_for(sensor)
    floor = sensor.get("floorId")
    original = {key: value for key, value in event.items() if key != "type"}
    if evidence and rule:
        original["analytics"] = {**evidence, "ruleName": rule["name"], "profile": rule.get("profile", "all-day"),
                                 **({"safetyRule": rule["safetyRule"]} if rule.get("safetyRule") else {}),
                                 "mode": "observe" if evidence["source"] == "replay" else rule["mode"]}
        original["summary"] = detection_summary(rule, evidence)
    if native and rule:
        original["summary"] = "Camera-native line crossing · " + rule["name"]
    if position is not None:
        original["position"] = position
    common = {"observation": original, "sensorName": sensor["name"], "modality": modality, "origin": "Site gateway",
              "cameraId": camera, "floorId": floor, "configurationVersion": version, **({"locationBasis": basis} if basis else {})}
    matches = [zone for zone in config["boundaries"] if position and zone["enabled"] and zone.get("floorId") == floor and sensor["id"] in zone["sensorIds"]
               and (not rule or zone["id"] == rule["boundaryId"]) and (not radar or zone["id"] == radar["boundaryId"]) and (basis == "device" or contains(zone["vertices"], position))]
    alerts = [{**common, "id": encode([sensor["id"], event["id"], zone["id"]]), "zoneId": zone["id"], "zoneName": zone["name"],
               "severity": "High" if zone["kind"] == "Restricted" else "Medium", "status": "new",
               **({"responseTeam": zone["responseTeam"]} if zone.get("responseTeam") else {})} for zone in matches]
    if inspection or native or radar or event["eventType"] in ACCESS_FACTS or evidence and rule and original["analytics"]["mode"] == "observe":
        alerts = []
    return {**common, "boundaries": [{"id": zone["id"], "name": zone["name"]} for zone in matches]}, alerts


def pair_kind(alert):
    event = alert["observation"]["eventType"]
    if (alert["modality"], event) == ("access", "door-forced-open"):
        return "door"
    if (alert["modality"], event) == ("access", "door-held-open"):
        return "held"
    if (alert["modality"], event) == ("vision", "line-crossing"):
        return "camera"


def choose_companion(kind, stamp, rule, candidates):
    if kind == "door":
        return next(iter(candidates("camera", stamp)), None)
    if kind == "camera":
        forced = candidates("door", stamp)
        if forced:
            return forced[0]
        if rule.get("includeHeldOpen"):
            held = candidates("held", stamp)
            if len(held) == 1 and not candidates("camera", held[0][1]):
                return held[0]
    if kind == "held" and rule.get("includeHeldOpen"):
        cameras = candidates("camera", stamp)
        if len(cameras) == 1:
            camera_at = cameras[0][1]
            if not candidates("held", camera_at) and not candidates("door", camera_at):
                return cameras[0]


class SiteMonitoring:
    def __init__(self, activity, organization, site, time_zone="UTC"):
        self.activity, self.organization, self.site = activity, organization, site
        self.time_zone_name, self.time_zone = time_zone, policy_zone(time_zone)
        self.configuration = SiteConfiguration(activity, time_zone)
        self.sensor_fusion = None
        with activity.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS monitored_events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, sensor TEXT NOT NULL, source_id TEXT NOT NULL, camera TEXT,
                    body TEXT NOT NULL, UNIQUE(sensor,source_id)
                );
                CREATE TABLE IF NOT EXISTS monitored_alerts (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL, camera TEXT, boundary TEXT NOT NULL,
                    event_at REAL NOT NULL, version INTEGER NOT NULL, member_of TEXT, pair_type TEXT, body TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS alert_one_companion ON monitored_alerts(member_of) WHERE member_of IS NOT NULL;
                CREATE INDEX IF NOT EXISTS alert_correlation ON monitored_alerts(camera,boundary,pair_type,event_at);
                CREATE INDEX IF NOT EXISTS monitored_alert_camera ON monitored_alerts(camera,seq);
                CREATE INDEX IF NOT EXISTS monitored_event_camera ON monitored_events(camera,seq);
                CREATE INDEX IF NOT EXISTS monitored_event_camera_time
                    ON monitored_events(camera,julianday(json_extract(body,'$.observation.occurredAt')));
                CREATE TABLE IF NOT EXISTS monitored_health (
                    sensor TEXT PRIMARY KEY, event_at REAL NOT NULL, change_id INTEGER NOT NULL, body TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS alert_deliveries (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT, payload TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0, next_at REAL NOT NULL DEFAULT 0,
                    failure TEXT, blocked INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS recovery_jobs (
                    incident TEXT PRIMARY KEY, camera TEXT NOT NULL, event_at REAL NOT NULL, high INTEGER NOT NULL,
                    state TEXT NOT NULL, attempts INTEGER NOT NULL, check_after REAL NOT NULL, updated_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS recovery_queue ON recovery_jobs(check_after,high);
                CREATE TABLE IF NOT EXISTS camera_checks (
                    camera TEXT NOT NULL, version INTEGER NOT NULL, verified_at TEXT, inspection TEXT, updated_at REAL NOT NULL,
                    PRIMARY KEY(camera,version)
                );
                CREATE TABLE IF NOT EXISTS analytics_workers (
                    camera TEXT PRIMARY KEY, updated_at REAL NOT NULL, body TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS camera_rule_acceptance (
                    camera TEXT NOT NULL, rule TEXT NOT NULL, fingerprint TEXT NOT NULL, created TEXT NOT NULL,
                    actor TEXT NOT NULL, note TEXT NOT NULL, measurements TEXT NOT NULL, passed INTEGER NOT NULL,
                    PRIMARY KEY(camera,rule,fingerprint)
                );
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(alert_deliveries)")}
            for name, definition in (("attempts", "INTEGER NOT NULL DEFAULT 0"), ("next_at", "REAL NOT NULL DEFAULT 0"),
                                     ("failure", "TEXT"), ("blocked", "INTEGER NOT NULL DEFAULT 0")):
                if name not in columns:
                    db.execute(f"ALTER TABLE alert_deliveries ADD COLUMN {name} {definition}")
            db.execute("CREATE INDEX IF NOT EXISTS alert_deliveries_ready ON alert_deliveries(sequence) WHERE blocked=0")
        from response_procedures import ResponseProcedures
        self.procedures = ResponseProcedures(activity, organization, site)
        from evidence_cases import EvidenceCases
        self.cases = EvidenceCases(activity)
        from coverage_operations import CoverageOperations
        self.coverage_operations = CoverageOperations(activity, self.procedures, organization, site)
        self.analytics_review = AnalyticsReview(activity)
        self.inspections = InspectionResults(activity)
        self.dock_schedules = DockSchedules(activity, self.configuration, self.time_zone)
        self.door_passages = DoorPassages(activity)
        self.native_events = NativeEvents(activity)

    def analytics_policy(self, db, camera, placement=None):
        identity = {"organizationId": self.organization, "siteId": self.site, "cameraId": camera}
        current = self.configuration.published(db)
        if not current:
            return {**identity, "version": 0, "rules": []}
        snapshot = json.loads(current[2])
        sensor = next((item for item in snapshot["sensors"] if item["id"] == camera and item["modality"] == "vision"), None)
        if sensor and sensor.get("analyticsPlacement") != placement:
            raise PermissionError("This worker is unavailable for the camera's published compute placement.")
        enabled = {boundary["id"] for boundary in snapshot["boundaries"] if boundary["enabled"]}
        return {**identity, "version": current[0], "publishedAt": current[1], "timeZone": snapshot.get("analyticsTimeZone", self.time_zone_name),
                **({"placement": placement} if placement else {}),
                "rules": [rule for rule in (sensor or {}).get("analyticsRules", []) if rule["boundaryId"] in enabled and not rule.get("cameraNative")]}

    def analytics_link(self, camera, requested_version, placement):
        with self.activity.connect() as db:
            policy = self.analytics_policy(db, camera, placement)
        if type(requested_version) is not int or requested_version != policy["version"] or not policy["rules"]:
            raise CameraError("Verify against the current published policy with an active CCTV rule.")
        return {"accepted": True, "policyVersion": policy["version"]}

    def analytics_heartbeat(self, camera, payload, placement=None):
        state, version, models, pending, fps, lag = (payload.get(key) for key in ("state", "version", "models", "pending", "fps", "frameAgeSeconds"))
        if (not isinstance(state, str) or state not in STATES or type(version) is not int or not 0 <= version < 2**53
                or not isinstance(models, list) or len(models) > 2 or any(not isinstance(model, str) or not re.fullmatch(r"[A-Za-z0-9_.:+/-]{1,96}", model) for model in models)
                or type(pending) is not int or not 0 <= pending <= 10000 or not number(fps, 0, 60) or (lag is not None and not number(lag, 0, 86400))):
            raise CameraError("Provide a valid CCTV worker status.")
        profile = payload.get("profileDigest")
        if profile is not None and (not isinstance(profile, str) or not re.fullmatch(r"[a-f0-9]{64}", profile)):
            raise CameraError("Provide a valid camera stream and model profile digest.")
        now = time.time()
        result = {"state": state, "version": version, "models": models, "pending": pending, "fps": fps, "frameAgeSeconds": lag, "reportedAt": now,
                  **({"placement": placement} if placement else {}), **({"profileDigest": profile} if profile else {})}
        with self.activity.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            policy = self.analytics_policy(db, camera, placement)
            if version == policy["version"] and "dockOccupancy" in payload:
                current = dock_snapshots(payload["dockOccupancy"], policy, now)
                previous = db.execute("SELECT body FROM analytics_workers WHERE camera=?", (camera,)).fetchone()
                if previous and state == "running":
                    before = json.loads(previous[0])
                    if before["version"] == version and before.get("placement") == placement and before["state"] == "running":
                        for item in before.get("dockOccupancy", ()):
                            same = current.get(item["ruleId"])
                            if same and item["sampledAt"] > same["sampledAt"]:
                                current[item["ruleId"]] = item
                result["dockOccupancy"] = list(current.values())
            db.execute("INSERT INTO analytics_workers VALUES(?,?,?) ON CONFLICT(camera) DO UPDATE SET updated_at=excluded.updated_at,body=excluded.body",
                       (camera, now, encode(result)))
            if active := self.configuration.published(db):
                sensors = json.loads(active[2])["sensors"]
                if any(row.get("linkedCameraId") == camera and row.get("passageRuleId") for row in sensors):
                    self.door_passages.heartbeat(db, camera, result)
        return {"accepted": True}

    def record_inspection(self, db, event):
        active = self.configuration.published(db, seconds(event["occurredAt"]))
        if not active:
            raise CameraError("Publish the station camera before sending inspection results.")
        version, _, snapshot = active
        detail, _ = evaluate(json.loads(snapshot), event, version)
        return self.inspections.record(db, detail)

    def record(self, db, event, sequence):
        observed = seconds(event["occurredAt"])
        active = self.configuration.published(db, observed)
        if not active:
            if "analytics" in event or "nativeAnalytics" in event:
                raise CameraError("Publish this camera rule before sending CCTV observations.")
            return
        version, _, snapshot = active
        config = json.loads(snapshot)
        if event["type"] == "health":
            if any(row["id"] == event["sensorId"] for row in config["sensors"] + config["dependencies"]):
                db.execute("INSERT INTO monitored_health VALUES(?,?,?,?) ON CONFLICT(sensor) DO UPDATE SET"
                           " event_at=excluded.event_at,body=excluded.body,change_id=excluded.change_id"
                           " WHERE excluded.event_at>monitored_health.event_at", (event["sensorId"], observed, sequence, encode(event)))
            return
        if "analytics" in event:
            camera = next((sensor for sensor in config["sensors"] if sensor["id"] == event["sensorId"]), None)
            if camera and camera.get("analyticsPlacement") != event["analytics"].get("placement"):
                raise CameraError("CCTV observations require the worker authorized for the compute placement at the observation time.")
        if "radarEvidence" in event and (not self.sensor_fusion or not any(
                gate.sensor == event["sensorId"] and gate.mapped(config) for gate in self.sensor_fusion.gates)):
            raise CameraError("Radar evidence requires its published site and gate mapping.")
        zone = policy_zone(config["analyticsTimeZone"]) if "analyticsTimeZone" in config else self.time_zone
        detail, alerts = evaluate(config, event, version, zone)
        if not detail:
            return
        db.execute("INSERT OR IGNORE INTO monitored_events(sensor,source_id,camera,body) VALUES(?,?,?,?)",
                   (event["sensorId"], event["id"], detail["cameraId"], encode(detail)))
        self.door_passages.observe(db, config, version, event, observed, detail)
        for alert in alerts:
            if db.execute("SELECT 1 FROM monitored_alerts WHERE id=?", (alert["id"],)).fetchone():
                continue
            rule = config["correlationRule"]
            window, grace = rule["windowSeconds"], rule["graceSeconds"]
            kind, camera, member_of = pair_kind(alert), alert["cameraId"], None
            if rule["enabled"] and kind and camera and time.time() - observed <= grace + window:
                def candidates(pair_type, center):
                    return db.execute("""
                        SELECT candidate.id,candidate.event_at FROM monitored_alerts candidate
                        LEFT JOIN shared_incidents decision ON decision.id=candidate.id
                        WHERE candidate.camera=? AND candidate.boundary=? AND candidate.version=? AND candidate.pair_type=?
                          AND candidate.event_at BETWEEN ? AND ? AND candidate.member_of IS NULL AND coalesce(decision.closed,0)=0
                          AND NOT EXISTS (SELECT 1 FROM monitored_alerts companion WHERE companion.member_of=candidate.id)
                        ORDER BY abs(candidate.event_at-?),candidate.seq LIMIT 2
                    """, (camera, alert["zoneId"], version, pair_type, center - window, center + window, center)).fetchall()

                match = choose_companion(kind, observed, rule, candidates)
                if match:
                    member_of, other_time = match
                    offset = observed - other_time if kind == "camera" else other_time - observed
                    alert["correlation"] = {"incidentId": member_of, "seconds": round(offset, 3)}
            db.execute("INSERT INTO monitored_alerts(id,camera,boundary,event_at,version,member_of,pair_type,body) VALUES(?,?,?,?,?,?,?,?)",
                       (alert["id"], camera, alert["zoneId"], observed, version, member_of, kind, encode(alert)))
            self.procedures.start(db, config, version, member_of or alert["id"], event["eventType"], alert["zoneId"])
            if camera:
                db.execute("INSERT OR IGNORE INTO recovery_jobs VALUES(?,?,?,?,?,0,?,?)",
                           (member_of or alert["id"], camera, observed, alert["severity"] == "High", "awaiting", max(observed + 12, time.time()), time.time()))
            if member_of is None and time.time() - observed <= grace + window:
                notice = {"type": "site-security-incident", "organizationId": self.organization, "siteId": self.site, "incidentId": alert["id"],
                          "boundaryId": alert["zoneId"], "deviceId": event["sensorId"], "cameraId": camera, "severity": alert["severity"],
                          "eventType": event["eventType"], "summary": alert["observation"]["summary"], "occurredAt": event["occurredAt"],
                          "configurationVersion": version, "deliveryId": token_hex(16)}
                db.execute("INSERT INTO alert_deliveries(payload) VALUES(?)", (encode(notice),))
        if self.sensor_fusion:
            self.sensor_fusion.observe(db, config, version, event, observed, detail, alerts)

    def pending(self):
        with self.activity.connect() as db:
            return db.execute("""SELECT sequence,payload FROM alert_deliveries
                                 WHERE blocked=0 AND next_at<=? ORDER BY sequence LIMIT 1""", (time.time(),)).fetchone()

    def delivery_failed(self, sequence, failure, retryable):
        with self.activity.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT attempts FROM alert_deliveries WHERE sequence=?", (sequence,)).fetchone()
            if row:
                attempts = row[0] + 1
                db.execute("UPDATE alert_deliveries SET attempts=?,next_at=?,failure=?,blocked=? WHERE sequence=?",
                           (attempts, time.time() + min(300, 3 * 2 ** min(attempts - 1, 7)), failure,
                            int(not retryable or attempts >= 10), sequence))

    def retry_delivery(self, actor, sequence):
        if not manages_site(actor):
            raise PermissionError("A site supervisor is required to retry notification delivery.")
        if type(sequence) is not int or not 1 <= sequence < 2**53:
            raise CameraError("Choose a failed notification.")
        with self.activity.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            changed = db.execute("""UPDATE alert_deliveries SET attempts=0,next_at=0,failure=NULL,blocked=0
                                    WHERE sequence=? AND blocked=1""", (sequence,))
            if changed.rowcount != 1:
                raise CameraError("This notification is already queued or was delivered.")
            from site_collaboration import iso_now
            db.execute("INSERT INTO activity_audit(occurred_at,actor,action,resource,detail) VALUES(?,?,?,?,?)",
                       (iso_now(), actor["subject"], "retry-notification", str(sequence), "Queued for retry"))
        return {"queued": True}

    def delivered(self, sequence):
        with self.activity.connect() as db:
            db.execute("DELETE FROM alert_deliveries WHERE sequence=?", (sequence,))

    def next_recovery(self):
        with self.activity.connect() as db:
            return db.execute("SELECT incident,camera,event_at,attempts FROM recovery_jobs WHERE check_after<=? AND state IN ('awaiting','retrieving')"
                              " ORDER BY high DESC,event_at DESC LIMIT 1", (time.time(),)).fetchone()

    def set_recovery(self, identifier, state, attempts, delay):
        with self.activity.connect() as db:
            db.execute("UPDATE recovery_jobs SET state=?,attempts=?,check_after=?,updated_at=? WHERE incident=?",
                       (state, attempts, time.time() + delay, time.time(), identifier))

    def recover_recordings(self, gateway, kbps):
        while not gateway.stopping.wait(2):
            try:
                job = self.next_recovery()
                if not job:
                    continue
                identifier, camera, observed, attempts = job
                with gateway.lock:
                    registered = gateway.cameras.get(camera)
                    stream = {**registered, "deviceId": camera} if registered else None
                if stream and gateway.video_status(stream["key"])["state"] == "unavailable":
                    self.set_recovery(identifier, "awaiting", attempts, 15)
                    continue
                if not stream:
                    self.set_recovery(identifier, "awaiting", attempts, 60)
                    continue
                self.set_recovery(identifier, "retrieving", attempts, 180)
                status = gateway.archive.retrieve(stream, int(round(observed * 1000)), rate_kbps=kbps)["state"]
                if status == "available":
                    self.set_recovery(identifier, "available", attempts + 1, 0)
                elif status == "unsupported":
                    self.set_recovery(identifier, "unsupported", attempts, 0)
                elif status == "pending":
                    self.set_recovery(identifier, "awaiting", attempts, 10)
                elif status == "missing" and attempts >= 4:
                    self.set_recovery(identifier, "gap", attempts + 1, 0)
                elif attempts >= 4:
                    self.set_recovery(identifier, "unavailable", attempts + 1, 0)
                else:
                    self.set_recovery(identifier, "awaiting", attempts + 1, min(120, 20 * (attempts + 1)))
            except (OSError, ValueError, sqlite3.Error):
                gateway.stopping.wait(2)

    def view(self, actor, known_version, known_event, known_health, known_media):
        with self.activity.connect() as db:
            db.execute("BEGIN")
            active = self.configuration.published(db)
            if not active:
                return {"publishedVersion": 0}
            version = active[0]
            config = scoped_inventory(json.loads(active[2]), actor)
            args = () if actor["cameras"] == "*" else tuple(actor["cameras"])
            where = "" if actor["cameras"] == "*" else " WHERE camera IN (" + ",".join("?" for _ in args) + ")" if args else " WHERE 0"
            newest = db.execute("SELECT coalesce(max(seq),0) FROM monitored_events" + where, args).fetchone()[0]
            health_ids = {sensor["id"] for sensor in config["sensors"] + config["dependencies"]}
            health_rows = [(change_id, body) for sensor, change_id, body in db.execute("SELECT sensor,change_id,body FROM monitored_health ORDER BY sensor") if sensor in health_ids]
            health_cursor = max((change_id for change_id, _ in health_rows), default=0)
            media_cursor = db.execute("SELECT coalesce(max(CAST(updated_at*1000 AS INTEGER)),0) FROM recovery_jobs" + where, args).fetchone()[0]
            checks = {camera: (verified, json.loads(inspection) if inspection else None, updated) for camera, verified, inspection, updated in db.execute(
                "SELECT camera,verified_at,inspection,updated_at FROM camera_checks WHERE version=?", (version,)) if camera in health_ids}
            workers = {camera: (updated, json.loads(body)) for camera, updated, body in db.execute(
                "SELECT camera,updated_at,body FROM analytics_workers") if camera in health_ids}
            media_cursor = max(media_cursor, max((int(row[2] * 1000) for row in checks.values()), default=0),
                               max((int(updated * 1000) for updated, _ in workers.values()), default=0))
            if known_version == version and known_event == newest and known_health == health_cursor and known_media == media_cursor:
                return {"publishedVersion": version, "unchanged": True}
            rows = db.execute("WITH groups AS (SELECT coalesce(member_of,id) AS root,max(seq) AS newest FROM monitored_alerts" + where
                              + " GROUP BY root ORDER BY newest DESC LIMIT 250)"
                              " SELECT alert.body,decision.owner,decision.closed FROM groups"
                              " JOIN monitored_alerts alert ON alert.id=groups.root OR alert.member_of=groups.root"
                              " LEFT JOIN shared_incidents decision ON decision.id=alert.id ORDER BY groups.newest DESC,alert.seq DESC", args).fetchall()
            events = db.execute("SELECT body FROM monitored_events" + where + " ORDER BY seq DESC LIMIT 500", args).fetchall()
            media = [{"incidentId": identifier, "state": state, "checkedAt": checked} for identifier, state, checked in db.execute(
                "SELECT incident,state,updated_at FROM recovery_jobs" + where + " ORDER BY event_at DESC LIMIT 500", args)]
        return {"publishedVersion": version, "eventCursor": newest, "healthCursor": health_cursor, "mediaCursor": media_cursor,
                "recordingCoverage": media, "configuration": config,
                "analyticsWorkers": {camera: body for camera, (_, body) in workers.items()},
                "cameraChecks": {camera: {"cameraVerifiedAt": row[0], "cameraInspection": row[1]} for camera, row in checks.items()},
                "alerts": [{**json.loads(body), **({"owner": owner} if owner else {}), "status": "resolved" if closed else "acknowledged" if owner else "new"}
                           for body, owner, closed in rows], "siteEvents": [json.loads(row[0]) for row in events], "health": [json.loads(body) for _, body in health_rows]}

    def dispatch(self, actor, cursor=None):
        after = ()
        if cursor is not None:
            if not re.fullmatch(r"[01]:-?[0-9]{1,16}:[0-9]{1,16}", cursor):
                raise CameraError("Choose a valid incident page.")
            priority, stamp, sequence = map(int, cursor.split(":"))
            after = (priority, stamp, sequence)
        with self.activity.connect() as db:
            db.execute("BEGIN")
            active = self.configuration.published(db)
            restriction = "" if actor["cameras"] == "*" else " AND a.camera IN (SELECT value FROM json_each(?))"
            params = () if not restriction else (encode(actor["cameras"]),)
            scope = " WHERE a.member_of IS NULL AND coalesce(i.closed,0)=0" + restriction
            count = db.execute("SELECT count(*) FROM monitored_alerts a LEFT JOIN shared_incidents i ON i.id=a.id" + scope,
                               params).fetchone()[0]
            order = "(json_extract(a.body,'$.severity')='High'),CAST(a.event_at*1000 AS INTEGER),a.seq"
            rows = db.execute("""SELECT a.id,a.body,i.owner,r.state,p.definition,p.step,
                (json_extract(a.body,'$.severity')='High') AS priority,CAST(a.event_at*1000 AS INTEGER) AS occurred,a.seq FROM monitored_alerts a
                LEFT JOIN shared_incidents i ON i.id=a.id
                LEFT JOIN recovery_jobs r ON r.incident=a.id
                LEFT JOIN incident_procedures p ON p.incident=a.id
                """ + scope + (" AND (" + order + ") < (?,?,?)" if after else "") +
                " ORDER BY priority DESC,occurred DESC,a.seq DESC LIMIT 31", params + after).fetchall()
            alerts = []
            for identifier, body, owner, recording, definition, step, *_ in rows[:30]:
                alert = json.loads(body)
                next_step = None
                if step:
                    next_step = next((item["text"] for item in json.loads(definition)["steps"] if item["id"] == step), None)
                handoff = db.execute("""SELECT json_extract(s.payload,'$.action.nextAction') FROM shared_activity s
                    JOIN shared_incidents i ON i.id=json_extract(s.payload,'$.incidentId')
                    WHERE i.id=? AND i.handoff=1 AND json_extract(s.payload,'$.action.kind')='handoff'
                    ORDER BY s.sequence DESC LIMIT 1""", (identifier,)).fetchone()
                alerts.append({**alert, "owner": owner, "status": "acknowledged" if owner else "new",
                               "recordingState": recording or ("available" if alert["observation"].get("recording") else "unverified"),
                               "nextAction": handoff[0] if handoff else next_step})
        return {"publishedVersion": active[0] if active else 0, "alerts": alerts, "openCount": count,
                "nextCursor": ":".join(map(str, rows[29][-3:])) if len(rows) > 30 else None}

    def incident_history(self, actor, identifier):
        from site_collaboration import SiteCollaboration
        with self.activity.connect() as db:
            db.execute("BEGIN")
            root = db.execute("SELECT body,camera FROM monitored_alerts WHERE id=? AND member_of IS NULL", (identifier,)).fetchone()
            if not root or not SiteCollaboration.can_view(actor, root[1]):
                raise PermissionError("This incident is unavailable to your account.")
            alert = json.loads(root[0])
            companions = [json.loads(body) for body, camera in db.execute("SELECT body,camera FROM monitored_alerts WHERE member_of=?", (identifier,))
                          if SiteCollaboration.can_view(actor, camera)]
            actions = [{"sequence": sequence, "resource": "incident", **json.loads(payload)} for sequence, payload, camera in db.execute(
                """SELECT sequence,payload,camera FROM shared_activity WHERE resource='incident' AND json_extract(payload,'$.incidentId')=?
                    ORDER BY sequence""", (identifier,)) if SiteCollaboration.can_view(actor, camera)]
            restriction = "" if actor["cameras"] == "*" else " AND e.camera IN (SELECT value FROM json_each(?))"
            params = (alert["observation"]["occurredAt"], alert["zoneId"]) + ((encode(actor["cameras"]),) if restriction else ())
            events = [json.loads(row[0]) for row in db.execute("""SELECT body FROM monitored_events e WHERE
                abs((julianday(json_extract(e.body,'$.observation.occurredAt'))-julianday(?))*86400)<=900
                AND EXISTS(SELECT 1 FROM json_each(e.body,'$.boundaries') b WHERE json_extract(b.value,'$.id')=?)"""
                + restriction + " ORDER BY e.seq DESC LIMIT 120", params)]
        return {"alerts": [alert, *companions], "actions": actions, "events": events}

    def preview(self, payload):
        configuration = normalize_configuration(payload.get("configuration"))
        raw = payload.get("observations")
        if not isinstance(raw, list) or len(raw) > 100 or any(not isinstance(item, dict) for item in raw):
            raise CameraError("Provide up to 100 observations for the configuration test.")
        events = [normalize({**item, "type": "observation"}) for item in raw]
        events = list({(row["sensorId"], row["id"]): row for row in events}.values())
        for event in events:
            if item := event.get("analytics"):
                event["analytics"] = {**item, "policyVersion": 0}
        rows = [row for event in events for row in evaluate(configuration, event, 0, self.time_zone)[1]]
        rule = configuration["correlationRule"]
        paired, available = 0, {}
        if rule["enabled"]:
            for row in rows:
                kind = pair_kind(row)
                if not kind or not row["cameraId"]:
                    continue
                stamp = seconds(row["observation"]["occurredAt"])
                scope = (row["cameraId"], row["zoneId"])

                def candidates(pair_type, center):
                    return sorted(((identifier, at) for identifier, (candidate_scope, candidate_kind, at) in available.items()
                                   if candidate_scope == scope and candidate_kind == pair_type
                                   and abs(at - center) <= rule["windowSeconds"]), key=lambda item: abs(item[1] - center))

                match = choose_companion(kind, stamp, rule, candidates)
                if match:
                    del available[match[0]]
                    paired += 1
                else:
                    available[row["id"]] = (scope, kind, stamp)
        return {"events": len(events), "matches": [{"source": row["sensorName"], "boundary": row["zoneName"],
            "summary": row["observation"]["summary"], "severity": row["severity"]} for row in rows[:100]],
                "matchCount": len(rows), "incidentCount": len(rows) - paired}
