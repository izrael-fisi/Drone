"""Authenticated, versioned incident and maintenance actions for one site."""

from datetime import datetime, timezone
import json
from secrets import token_hex
import time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from onvif import CameraError
from site_activity import valid_event_device
from site_accounts import SiteAccounts, manages_site


class Conflict(CameraError):
    pass


def iso_now():
    return datetime.now(timezone.utc).isoformat()


def text_field(payload, field, limit, optional=False):
    value = payload.get(field, "")
    if not isinstance(value, str) or len(value.strip()) > limit or not (value.strip() or optional):
        raise CameraError(f"Invalid {field}.")
    return value.strip()


def due_date(value):
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if not stamp.tzinfo or stamp.year < 2000:
            raise ValueError()
        return stamp.astimezone(timezone.utc)
    except (AttributeError, ValueError, OverflowError):
        raise CameraError("Use a date and time with a timezone.") from None


class SiteCollaboration:
    def __init__(self, activity, config, proxy_secret=""):
        self.activity = activity
        if not isinstance(config, dict):
            raise CameraError("Site access configuration must be a JSON object.")
        self.site = text_field(config, "site", 80)
        self.time_zone = config.get("timeZone", "UTC")
        if not isinstance(self.time_zone, str) or not 1 <= len(self.time_zone) <= 80:
            raise CameraError("Set an IANA time zone for the site.")
        if self.time_zone != "UTC":
            try:
                ZoneInfo(self.time_zone)
            except (ZoneInfoNotFoundError, ValueError) as issue:
                raise CameraError("Set a valid IANA time zone supported by this gateway.") from issue
        self.device_cameras = config.get("deviceCameras", {})
        if not isinstance(self.device_cameras, dict) or not all(valid_event_device(device) and valid_event_device(camera)
            for device, camera in self.device_cameras.items()):
            raise CameraError("Device camera mappings must use registered device IDs.")
        self.accounts = SiteAccounts(activity, config, self.site, proxy_secret)
        self.monitor = None
        self.ai_jobs = None
        with activity.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS shared_incidents (
                    id TEXT PRIMARY KEY, camera TEXT, revision INTEGER NOT NULL, owner TEXT, closed INTEGER NOT NULL, handoff INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS shared_activity (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT, resource TEXT NOT NULL, camera TEXT, payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS shared_maintenance (
                    sensor TEXT NOT NULL, starts REAL NOT NULL, ends REAL NOT NULL, active INTEGER NOT NULL,
                    PRIMARY KEY(sensor, starts)
                );
                CREATE TABLE IF NOT EXISTS shared_handoff_due (
                    incident TEXT PRIMARY KEY, due REAL NOT NULL, notified INTEGER NOT NULL, owner TEXT NOT NULL
                );
            """)

    def authenticate(self, headers):
        return self.accounts.authenticate(headers)

    def session(self, actor):
        return {"site": self.site, "siteId": self.accounts.site_id, "timeZone": self.time_zone, "organization": self.accounts.organization,
                "automaticTriageAvailable": bool(self.ai_jobs and self.ai_jobs.automatic),
                **{key: actor[key] for key in ("subject", "name", "role", "cameras")}}

    @staticmethod
    def can_view(actor, camera):
        return bool(actor and (actor["cameras"] == "*" or camera and camera in actor["cameras"]))

    def page(self, actor, after):
        with self.activity.connect() as db:
            rows = db.execute("SELECT sequence,resource,camera,payload FROM shared_activity WHERE sequence>? ORDER BY sequence LIMIT 250", (after,)).fetchall()
        activity = [{**json.loads(payload), "sequence": sequence, "resource": resource} for sequence, resource, camera, payload in rows
                    if self.can_view(actor, camera)]
        return {"events": activity, "after": rows[-1][0] if rows else after, "hasMore": len(rows) == 250}

    def incident(self, actor, payload):
        identifier = text_field(payload, "incidentId", 450)
        camera = payload.get("cameraId")
        version = payload.get("revision")
        action = payload.get("action")
        if (camera is not None and not valid_event_device(camera)) or not self.can_view(actor, camera) or actor["role"] == "viewer":
            raise PermissionError("Your role cannot change this incident.")
        if type(version) is not int or version < 0 or not isinstance(action, dict):
            raise CameraError("Include the current incident revision and one action.")
        kind = action.get("kind")
        name = actor["name"]
        current_time = iso_now()
        normalized = {"kind": kind}
        with self.activity.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if self.monitor and self.monitor.configuration.published(db):
                source = db.execute("SELECT camera,member_of,body FROM monitored_alerts WHERE id=?", (identifier,)).fetchone()
                if not source or source[1] is not None or source[0] != camera:
                    raise PermissionError("Choose a published site incident available to your account.")
                payload = {**payload, "priority": json.loads(source[2])["severity"]}
            existing = db.execute("SELECT camera,revision,owner,closed,handoff FROM shared_incidents WHERE id=?", (identifier,)).fetchone()
            existing_camera, revision, owner, closed, handoff = existing or (camera, 0, None, False, False)
            if existing_camera != camera or not self.can_view(actor, existing_camera):
                raise PermissionError("Your role cannot change this incident.")
            guided = kind in ("procedure-step", "procedure-override")
            if guided:
                action_id = text_field(action, "actionId", 96)
                request = json.dumps(action, sort_keys=True)
                duplicate = db.execute("SELECT subject,request,response FROM procedure_actions WHERE incident=? AND action_id=?",
                                       (identifier, action_id)).fetchone()
                if duplicate:
                    if duplicate[:2] != (actor["subject"], request):
                        raise Conflict("This procedure action ID was already used.")
                    return json.loads(duplicate[2])
            if revision != version:
                raise Conflict("This incident changed on another workstation. Refreshing its activity.")
            if closed and kind != "bookmark":
                raise Conflict("This incident is already resolved.")
            if kind == "claim" and owner in (None, name):
                owner = name
                normalized["name"] = name
            elif kind == "accept-handoff" and handoff and owner != name:
                owner, handoff = name, False
                normalized["name"] = name
            elif kind == "acknowledge":
                pass
            elif kind == "procedure-override" and self.monitor:
                normalized = self.monitor.procedures.advance(db, identifier, action, actor)
            elif owner != name:
                raise Conflict("This change requires ownership. Use the pending handoff to transfer the incident.")
            elif kind == "note":
                normalized["text"] = text_field(action, "text", 500)
            elif kind == "resolve" and action.get("disposition") in ("confirmed", "false-alarm", "maintenance", "inconclusive"):
                procedure = self.monitor.procedures.detail(db, identifier) if self.monitor else None
                if procedure and procedure["step"] is not None:
                    raise Conflict("Complete the active response procedure before resolving this incident.")
                closed, handoff = True, False
                normalized["disposition"] = action["disposition"]
            elif kind == "procedure-step" and self.monitor:
                normalized = self.monitor.procedures.advance(db, identifier, action, actor)
            elif kind == "checklist" and action.get("step") in ("verify", "contact", "document") and type(action.get("done")) is bool:
                normalized.update(step=action["step"], done=action["done"])
            elif kind == "handoff":
                handoff = True
                normalized.update(nextAction=text_field(action, "nextAction", 180), note=text_field(action, "note", 500, True))
                if action.get("dueAt"):
                    normalized["dueAt"] = due_date(action["dueAt"]).isoformat()
                if "aiJobId" in action:
                    if not self.ai_jobs:
                        raise Conflict("AI handoff is disabled at this site.")
                    account = db.execute("SELECT * FROM organization_accounts WHERE subject=? AND active=1", (actor["subject"],)).fetchone()
                    if not account or self.accounts.public_entry(account) != actor:
                        raise PermissionError("Your site permissions changed. Sign in again.")
                    normalized["aiDraft"] = self.ai_jobs.approve(db, actor, identifier, action["aiJobId"], revision)
            elif kind == "bookmark":
                start, end = (due_date(action.get(field)) for field in ("startAt", "endAt"))
                if not 0 < (end - start).total_seconds() <= 1800:
                    raise CameraError("Choose an evidence range of up to 30 minutes.")
                normalized.update(label=text_field(action, "label", 80), startAt=start.isoformat(), endAt=end.isoformat())
            elif kind != "claim":
                raise CameraError("Choose a supported incident action.")
            revision += 1
            db.execute("INSERT OR REPLACE INTO shared_incidents VALUES(?,?,?,?,?,?)", (identifier, camera, revision, owner, int(closed), int(handoff)))
            if kind in ("handoff", "accept-handoff", "resolve"):
                db.execute("DELETE FROM shared_handoff_due WHERE incident=?", (identifier,))
                if kind == "handoff" and normalized.get("dueAt") and payload.get("priority") == "High":
                    db.execute("INSERT INTO shared_handoff_due VALUES(?,?,0,?)", (identifier, due_date(normalized["dueAt"]).timestamp(), owner))
            result = self.append(db, "incident", camera, {"incidentId": identifier, "revision": revision, "actor": name,
                                                          "at": current_time, "action": normalized})
            if guided:
                db.execute("INSERT INTO procedure_actions VALUES(?,?,?,?,?)",
                           (identifier, action_id, actor["subject"], request, json.dumps(result)))
            return result

    def enqueue_overdue(self):
        with self.activity.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            pending = db.execute("SELECT incident,due,owner FROM shared_handoff_due WHERE notified=0 AND due<=? LIMIT 50", (time.time(),)).fetchall()
            for identifier, due, owner in pending:
                self.activity.enqueue_notification(db, {"type": "incident-handoff-overdue", "site": self.site, "incidentId": identifier,
                    "owner": owner, "dueAt": datetime.fromtimestamp(due, timezone.utc).isoformat(), "deliveryId": token_hex(16), "occurredAt": iso_now()})
                db.execute("UPDATE shared_handoff_due SET notified=1 WHERE incident=?", (identifier,))

    def maintenance(self, actor, payload):
        if not manages_site(actor):
            raise PermissionError("Site maintenance requires a site supervisor.")
        sensor = payload.get("sensorId")
        mode = payload.get("mode")
        if not valid_event_device(sensor) or mode not in ("schedule", "stop"):
            raise CameraError("Choose a device and maintenance operation.")
        starts = due_date(payload.get("startsAt"))
        at = iso_now()
        start = starts.timestamp()
        now = time.time()
        body = {"sensorId": sensor, "mode": mode, "startsAt": starts.isoformat(), "at": at, "actor": actor["name"]}
        with self.activity.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if mode == "schedule":
                ends = due_date(payload.get("endsAt"))
                end = ends.timestamp()
                if end <= max(start, now) or end - start > 31 * 86400 or start < int(now / 60) * 60:
                    raise CameraError("Maintenance must end in the future and last at most 31 days.")
                if db.execute("SELECT 1 FROM shared_maintenance WHERE sensor=? AND active=1 AND starts<? AND ends>?", (sensor, end, start)).fetchone():
                    raise Conflict("Another maintenance window already overlaps this time.")
                db.execute("INSERT OR REPLACE INTO shared_maintenance VALUES(?,?,?,1)", (sensor, start, end))
                body.update(endsAt=ends.isoformat(), reason=text_field(payload, "reason", 200))
            else:
                result = db.execute("UPDATE shared_maintenance SET active=0 WHERE sensor=? AND starts=? AND active=1 AND ends>?", (sensor, start, now))
                if not result.rowcount:
                    raise Conflict("The maintenance window has already ended.")
            return self.append(db, "maintenance", sensor, body)

    @staticmethod
    def append(db, resource, camera, payload):
        result = db.execute("INSERT INTO shared_activity(resource,camera,payload) VALUES(?,?,?)", (resource, camera, json.dumps(payload)))
        return {**payload, "sequence": result.lastrowid, "resource": resource}
