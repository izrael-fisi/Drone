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
from analytics_contract import LABELS as ANALYTICS_OBJECTS, RULE_LABELS, STATES, effective_rule, matches_evidence, number, policy_zone
from analytics_review import AnalyticsReview


def encode(value):
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def seconds(value):
    return datetime.fromisoformat(value).timestamp()


def camera_for(sensor):
    return sensor["id"] if sensor["modality"] == "vision" else sensor.get("linkedCameraId")


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
    rule = None
    if evidence:
        rule = next((rule for rule in (sensor or {}).get("analyticsRules", []) if rule["id"] == evidence["ruleId"]), None)
        if rule:
            rule = effective_rule(rule, seconds(event["occurredAt"]), zone)
        if not rule or version != evidence["policyVersion"] or rule["kind"] != event["eventType"] or not matches_evidence(rule, evidence):
            raise CameraError("CCTV evidence does not match the camera rule published at the observation time.")
    if not sensor:
        return None, []
    modality = sensor["modality"]
    position = event.get("position")
    basis = "reported" if position else None
    if position is None and modality in ("vision", "access"):
        position, basis = sensor["position"], "device"
    camera, floor = camera_for(sensor), sensor.get("floorId")
    original = {key: value for key, value in event.items() if key != "type"}
    if rule:
        original["analytics"] = {**evidence, "ruleName": rule["name"], "profile": rule.get("profile", "all-day"),
                                 "mode": "observe" if evidence["source"] == "replay" else rule["mode"]}
        original["summary"] = f"{ANALYTICS_OBJECTS[evidence['object']]} {RULE_LABELS[rule['kind']]} · {rule['name']}"
    if position is not None:
        original["position"] = position
    common = {"observation": original, "sensorName": sensor["name"], "modality": modality, "origin": "Site gateway",
              "cameraId": camera, "floorId": floor, "configurationVersion": version, **({"locationBasis": basis} if basis else {})}
    matches = [zone for zone in config["boundaries"] if position and zone["enabled"] and zone.get("floorId") == floor and sensor["id"] in zone["sensorIds"]
               and (not rule or zone["id"] == rule["boundaryId"]) and (basis == "device" or contains(zone["vertices"], position))]
    alerts = [{**common, "id": encode([sensor["id"], event["id"], zone["id"]]), "zoneId": zone["id"], "zoneName": zone["name"],
               "severity": "High" if zone["kind"] == "Restricted" else "Medium", "status": "new",
               **({"responseTeam": zone["responseTeam"]} if zone.get("responseTeam") else {})} for zone in matches]
    if rule and original["analytics"]["mode"] == "observe":
        alerts = []
    return {**common, "boundaries": [{"id": zone["id"], "name": zone["name"]} for zone in matches]}, alerts


def pair_kind(alert):
    event = alert["observation"]["eventType"]
    if (alert["modality"], event) == ("access", "door-forced-open"):
        return "door"
    if (alert["modality"], event) == ("vision", "line-crossing"):
        return "camera"


class SiteMonitoring:
    def __init__(self, activity, organization, site, time_zone="UTC"):
        self.activity, self.organization, self.site = activity, organization, site
        self.time_zone_name, self.time_zone = time_zone, policy_zone(time_zone)
        self.configuration = SiteConfiguration(activity, time_zone)
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
                CREATE TABLE IF NOT EXISTS monitored_health (
                    sensor TEXT PRIMARY KEY, event_at REAL NOT NULL, change_id INTEGER NOT NULL, body TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS alert_deliveries (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT, payload TEXT NOT NULL
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
            """)
        self.analytics_review = AnalyticsReview(activity)

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
                "rules": [rule for rule in (sensor or {}).get("analyticsRules", []) if rule["boundaryId"] in enabled]}

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
        now = time.time()
        result = {"state": state, "version": version, "models": models, "pending": pending, "fps": fps, "frameAgeSeconds": lag, "reportedAt": now,
                  **({"placement": placement} if placement else {})}
        with self.activity.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self.analytics_policy(db, camera, placement)
            db.execute("INSERT INTO analytics_workers VALUES(?,?,?) ON CONFLICT(camera) DO UPDATE SET updated_at=excluded.updated_at,body=excluded.body",
                       (camera, now, encode(result)))
        return {"accepted": True}

    def record(self, db, event, sequence):
        observed = seconds(event["occurredAt"])
        active = self.configuration.published(db, observed)
        if not active:
            if "analytics" in event:
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
        zone = policy_zone(config["analyticsTimeZone"]) if "analyticsTimeZone" in config else self.time_zone
        detail, alerts = evaluate(config, event, version, zone)
        if not detail:
            return
        db.execute("INSERT OR IGNORE INTO monitored_events(sensor,source_id,camera,body) VALUES(?,?,?,?)",
                   (event["sensorId"], event["id"], detail["cameraId"], encode(detail)))
        for alert in alerts:
            if db.execute("SELECT 1 FROM monitored_alerts WHERE id=?", (alert["id"],)).fetchone():
                continue
            rule = config["correlationRule"]
            window, grace = rule["windowSeconds"], rule["graceSeconds"]
            kind, camera, member_of = pair_kind(alert), alert["cameraId"], None
            if rule["enabled"] and kind and camera and time.time() - observed <= grace + window:
                match = db.execute("""
                    SELECT candidate.id,candidate.event_at FROM monitored_alerts candidate
                    LEFT JOIN shared_incidents decision ON decision.id=candidate.id
                    WHERE candidate.camera=? AND candidate.boundary=? AND candidate.version=? AND candidate.pair_type=?
                      AND candidate.event_at BETWEEN ? AND ? AND candidate.member_of IS NULL AND coalesce(decision.closed,0)=0
                      AND NOT EXISTS (SELECT 1 FROM monitored_alerts companion WHERE companion.member_of=candidate.id)
                    ORDER BY abs(candidate.event_at-?),candidate.seq LIMIT 1
                """, (camera, alert["zoneId"], version, "camera" if kind == "door" else "door", observed - window, observed + window, observed)).fetchone()
                if match:
                    member_of, other_time = match
                    offset = observed - other_time if kind == "camera" else other_time - observed
                    alert["correlation"] = {"incidentId": member_of, "seconds": round(offset, 3)}
            db.execute("INSERT INTO monitored_alerts(id,camera,boundary,event_at,version,member_of,pair_type,body) VALUES(?,?,?,?,?,?,?,?)",
                       (alert["id"], camera, alert["zoneId"], observed, version, member_of, kind, encode(alert)))
            if camera:
                db.execute("INSERT OR IGNORE INTO recovery_jobs VALUES(?,?,?,?,?,0,?,?)",
                           (member_of or alert["id"], camera, observed, alert["severity"] == "High", "awaiting", max(observed + 12, time.time()), time.time()))
            if member_of is None and time.time() - observed <= grace + window:
                notice = {"type": "site-security-incident", "organizationId": self.organization, "siteId": self.site, "incidentId": alert["id"],
                          "boundaryId": alert["zoneId"], "deviceId": event["sensorId"], "cameraId": camera, "severity": alert["severity"],
                          "eventType": event["eventType"], "summary": alert["observation"]["summary"], "occurredAt": event["occurredAt"],
                          "configurationVersion": version, "deliveryId": token_hex(16)}
                db.execute("INSERT INTO alert_deliveries(payload) VALUES(?)", (encode(notice),))

    def pending(self):
        with self.activity.connect() as db:
            return db.execute("SELECT sequence,payload FROM alert_deliveries ORDER BY sequence LIMIT 1").fetchone()

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
        used = set()
        if rule["enabled"]:
            for row in rows:
                if pair_kind(row) != "door" or not row["cameraId"]:
                    continue
                stamp = seconds(row["observation"]["occurredAt"])
                matches = [(abs(seconds(candidate["observation"]["occurredAt"]) - stamp), candidate["id"]) for candidate in rows
                           if candidate["id"] not in used and pair_kind(candidate) == "camera" and candidate["cameraId"] == row["cameraId"]
                           and candidate["zoneId"] == row["zoneId"]]
                if matches:
                    delta, member = min(matches)
                    if delta <= rule["windowSeconds"]:
                        used.add(member)
        return {"events": len(events), "matches": [{"source": row["sensorName"], "boundary": row["zoneName"],
            "summary": row["observation"]["summary"], "severity": row["severity"]} for row in rows[:100]],
                "matchCount": len(rows), "incidentCount": len(rows) - len(used)}
