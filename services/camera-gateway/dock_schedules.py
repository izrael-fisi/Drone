"""Published dock appointments and scoped operator check-ins."""

import csv
from collections import Counter
from datetime import date, datetime, time as midnight, timedelta, timezone
from hashlib import sha256
from io import StringIO
import json
import time

from onvif import CameraError
from site_accounts import manages_site
from site_activity import valid_event_device
from site_collaboration import Conflict


HEADER = ["appointmentId", "boundaryId", "startsAt", "endsAt"]


def stamp(value):
    result = datetime.fromisoformat(value)
    if result.utcoffset() is None:
        raise ValueError("Use timestamps with a timezone.")
    return result.timestamp()


def parse_schedule(text, docks, start, end):
    if not isinstance(text, str) or len(text) > 100_000:
        raise CameraError("Choose a dock CSV up to 100 KB.")
    try:
        reader = csv.reader(StringIO(text.removeprefix("\ufeff")), strict=True)
        if next(reader, None) != HEADER:
            raise ValueError()
        seen, rows = set(), []
        for raw in reader:
            identifier, bay, begins, finishes = (part.strip() for part in raw)
            lower, upper = stamp(begins), stamp(finishes)
            if (not valid_event_device(identifier) or identifier in seen or bay not in docks or len(rows) >= 100
                    or not start <= lower < end or not lower < upper <= lower + 86400):
                raise ValueError()
            rows.append({"id": identifier, "boundaryId": bay, "startsAt": lower, "endsAt": upper})
            seen.add(identifier)
        if not rows:
            raise ValueError()
    except (csv.Error, ValueError, OverflowError, OSError):
        raise CameraError("Use appointmentId,boundaryId,startsAt,endsAt and 1–100 unique appointments for assigned bays on the selected site day.") from None
    return sorted(rows, key=lambda row: (row["startsAt"], row["id"]))


def dock_rules(config, actor):
    enabled = {row["id"]: row["name"] for row in config["boundaries"] if row["enabled"]}
    active = {}
    for camera in config["sensors"]:
        if actor["cameras"] != "*" and camera["id"] not in actor["cameras"]:
            continue
        for rule in camera.get("analyticsRules", ()):
            if rule.get("trackOccupancy") and rule["boundaryId"] in enabled:
                active[camera["id"], rule["id"]] = (rule["boundaryId"], camera.get("analyticsPlacement"))
    return active, {bay: enabled[bay] for bay, _ in active.values()}


class DockSchedules:
    def __init__(self, activity, configuration, zone):
        self.activity, self.configuration, self.zone = activity, configuration, zone
        with activity.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS dock_schedules (
                    revision INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT NOT NULL, base INTEGER NOT NULL,
                    digest TEXT NOT NULL, imported REAL NOT NULL, published REAL, body TEXT NOT NULL,
                    UNIQUE(day,base,digest)
                );
                CREATE TABLE IF NOT EXISTS dock_schedule_heads (day TEXT PRIMARY KEY, revision INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS dock_checkins (
                    id TEXT PRIMARY KEY, day TEXT NOT NULL, revision INTEGER, boundary TEXT NOT NULL,
                    appointment TEXT, reference TEXT NOT NULL, actor TEXT NOT NULL, checked_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS dock_checkin_day ON dock_checkins(day,boundary,checked_at);
                CREATE INDEX IF NOT EXISTS dock_event_time ON monitored_events(json_extract(body,'$.observation.occurredAt'))
                    WHERE json_extract(body,'$.observation.eventType')='zone-entry'
                      AND json_extract(body,'$.observation.analytics.source')='live';
            """)

    def day_range(self, raw):
        today = datetime.now(self.zone).date()
        try:
            value = date.fromisoformat(raw) if raw else today
            start = datetime.combine(value, midnight.min, self.zone).timestamp()
            end = datetime.combine(value + timedelta(days=1), midnight.min, self.zone).timestamp()
        except (TypeError, ValueError, OverflowError, OSError):
            raise CameraError("Choose a calendar day in the site's timezone.") from None
        return value.isoformat(), start, end, value == today

    def inventory(self, db, actor):
        version = self.configuration.published(db)
        if not version:
            return 0, {}, {}
        return version[0], *dock_rules(json.loads(version[2]), actor)

    @staticmethod
    def head(db, day):
        result = db.execute("SELECT revision FROM dock_schedule_heads WHERE day=?", (day,)).fetchone()
        return result[0] if result else 0

    def change(self, actor, payload):
        operation = payload.get("operation")
        if operation not in ("preview", "publish", "check-in"):
            raise CameraError("Choose a valid dock schedule operation.")
        if operation != "check-in" and not manages_site(actor) or operation == "check-in" and actor["role"] == "viewer":
            raise PermissionError("Your role cannot change the dock schedule.")
        day, lower, upper, today = self.day_range(payload.get("day"))
        with self.activity.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            revision = self.head(db, day)
            _, _, bays = self.inventory(db, actor)
            if operation == "preview":
                appointments = parse_schedule(payload.get("csv"), bays, lower, upper)
                body = json.dumps(appointments, separators=(",", ":"))
                digest = sha256(body.encode()).hexdigest()
                current = db.execute("SELECT digest FROM dock_schedules WHERE revision=?", (revision,)).fetchone()
                if current and current[0] == digest:
                    return {"revision": revision, "baseRevision": revision, "appointments": appointments, "published": True}
                db.execute("INSERT OR IGNORE INTO dock_schedules(day,base,digest,imported,body) VALUES(?,?,?,?,?)",
                           (day, revision, digest, time.time(), body))
                candidate = db.execute("SELECT revision FROM dock_schedules WHERE day=? AND base=? AND digest=?",
                                       (day, revision, digest)).fetchone()[0]
                return {"revision": candidate, "baseRevision": revision, "appointments": appointments, "published": False}
            if operation == "publish":
                candidate = payload.get("revision")
                if type(candidate) is not int or candidate <= 0:
                    raise CameraError("Choose a saved schedule preview.")
                if candidate == revision:
                    return {"revision": revision}
                if payload.get("baseRevision") != revision:
                    raise Conflict("The dock schedule changed. Import against the current revision.")
                record = db.execute("SELECT base,body FROM dock_schedules WHERE day=? AND revision=? AND published IS NULL",
                                    (day, candidate)).fetchone()
                if not record or record[0] != revision or any(row["boundaryId"] not in bays for row in json.loads(record[1])):
                    raise Conflict("The schedule preview or current dock assignments changed.")
                db.execute("UPDATE dock_schedules SET published=? WHERE revision=?", (time.time(), candidate))
                db.execute("INSERT INTO dock_schedule_heads VALUES(?,?) ON CONFLICT(day) DO UPDATE SET revision=excluded.revision",
                           (day, candidate))
                self.audit(db, actor, operation, day, str(candidate))
                return {"revision": candidate}
            identifier, bay, reference, appointment = (payload.get(key) for key in ("id", "boundaryId", "reference", "appointmentId"))
            if (not valid_event_device(identifier) or not isinstance(bay, str) or bay not in bays or not isinstance(reference, str)
                    or not 1 <= len(reference.strip()) <= 80 or any(ord(symbol) < 32 for symbol in reference)
                    or appointment is not None and not valid_event_device(appointment)):
                raise CameraError("Choose an assigned bay and enter a shipment or appointment reference.")
            existing = db.execute("SELECT day,boundary,appointment,reference,actor FROM dock_checkins WHERE id=?", (identifier,)).fetchone()
            if existing:
                if existing != (day, bay, appointment, reference.strip(), actor["subject"]):
                    raise Conflict("This check-in ID belongs to another check-in.")
                return {"id": identifier}
            if not today or payload.get("revision") != revision:
                raise Conflict("Use the current site day and refresh the schedule before check-in.")
            if appointment:
                row = db.execute("SELECT body FROM dock_schedules WHERE revision=?", (revision,)).fetchone()
                if not row or not any(item["id"] == appointment and item["boundaryId"] == bay for item in json.loads(row[0])):
                    raise CameraError("Choose a published appointment at this bay.")
            db.execute("INSERT INTO dock_checkins VALUES(?,?,?,?,?,?,?,?)",
                       (identifier, day, revision, bay, appointment, reference.strip(), actor["subject"], time.time()))
            self.audit(db, actor, operation, bay, identifier)
            return {"id": identifier}

    @staticmethod
    def audit(db, actor, operation, resource, detail):
        db.execute("INSERT INTO activity_audit(occurred_at,actor,action,resource,detail) VALUES(?,?,?,?,?)",
                   (datetime.now(timezone.utc).isoformat(), actor["subject"], "dock-" + operation, resource, detail))

    def arrivals(self, db, actor, bays, lower, upper):
        iso = lambda value: datetime.fromtimestamp(value, timezone.utc).isoformat()
        rows = db.execute("""
            SELECT json_extract(body,'$.configurationVersion'),camera,source_id,
                   json_extract(body,'$.observation.analytics.ruleId'),json_extract(body,'$.observation.occurredAt')
            FROM monitored_events WHERE json_extract(body,'$.observation.eventType')='zone-entry'
              AND json_extract(body,'$.observation.analytics.source')='live'
              AND json_extract(body,'$.observation.occurredAt') >= ? AND json_extract(body,'$.observation.occurredAt') < ?
            ORDER BY json_extract(body,'$.observation.occurredAt')
        """, (iso(lower), iso(upper))).fetchall()
        releases, result = {}, []
        for version, camera, identifier, rule, occurred in rows:
            if version not in releases:
                policy = db.execute("SELECT body FROM configuration_versions WHERE version=?", (version,)).fetchone()
                releases[version] = dock_rules(json.loads(policy[0]), actor)[0] if policy else {}
            historical = releases[version].get((camera, rule))
            if historical and historical[0] in bays:
                result.append({"id": identifier, "cameraId": camera, "boundaryId": historical[0], "occurredAt": stamp(occurred)})
        return result

    def read(self, actor, query):
        day, lower, upper, today = self.day_range(query.get("day", [""])[0])
        now = time.time()
        with self.activity.connect() as db:
            db.execute("BEGIN")
            version, rules, bays = self.inventory(db, actor)
            revision = self.head(db, day)
            body = db.execute("SELECT body FROM dock_schedules WHERE revision=?", (revision,)).fetchone()
            appointments = [row for row in json.loads(body[0]) if row["boundaryId"] in bays] if body else []
            workers = {camera: json.loads(value) for camera, value in db.execute("SELECT camera,body FROM analytics_workers")
                       if any(pair[0] == camera for pair in rules)}
            occupancy = {bay: [] for bay in bays}
            expected_samples = Counter(bay for bay, _ in rules.values())
            for (camera, rule), (bay, placement) in rules.items():
                worker = workers.get(camera, {})
                if worker.get("state") != "running" or worker.get("version") != version or worker.get("placement") != placement:
                    continue
                occupancy[bay].extend({**row, "cameraId": camera} for row in worker.get("dockOccupancy", ())
                                      if row["ruleId"] == rule and now - row["sampledAt"] <= 15 and row["state"] != "unknown")
            arrivals = self.arrivals(db, actor, bays, lower, max([upper, *(row["endsAt"] for row in appointments)]))
            checkins = [{"id": row[0], "boundaryId": row[1], "appointmentId": row[2], "reference": row[3], "checkedAt": row[4]}
                        for row in db.execute("SELECT id,boundary,appointment,reference,checked_at FROM dock_checkins"
                                              " WHERE day=? ORDER BY checked_at DESC LIMIT 500", (day,)) if row[1] in bays]
            history = [{"revision": row[0], "publishedAt": row[1]} for row in db.execute(
                "SELECT revision,published FROM dock_schedules WHERE day=? AND published IS NOT NULL ORDER BY published DESC LIMIT 20",
                (day,))] if manages_site(actor) else []
        by_appointment = {row["id"]: [] for row in appointments}
        for arrival in arrivals:
            matching = [row for row in appointments if row["boundaryId"] == arrival["boundaryId"]
                        and row["startsAt"] <= arrival["occurredAt"] < row["endsAt"]]
            arrival["appointments"] = [row["id"] for row in matching]
            for row in matching:
                by_appointment[row["id"]].append(arrival)
        for appointment in appointments:
            bay = appointment["boundaryId"]
            candidates = by_appointment[appointment["id"]]
            exact = candidates[0] if len(candidates) == 1 and len(candidates[0]["appointments"]) == 1 else None
            overstay = exact and now > appointment["endsAt"] and any(
                sample["state"] == "occupied" and sample["cameraId"] == exact["cameraId"] and sample.get("arrivalEventId") == exact["id"]
                for sample in occupancy[bay])
            appointment["status"] = "Overstay" if overstay else "Arrived" if exact else "Ambiguous match" if candidates else "No arrival observed"
            appointment["arrival"] = exact
        exceptions = [row for row in arrivals if not row["appointments"] and row["occurredAt"] < upper]
        return {"day": day, "timeZone": str(self.zone), "today": today, "revision": revision, "history": history,
                "bays": [{"id": bay, "name": name, "occupancy": "Occupied" if any(x["state"] == "occupied" for x in occupancy[bay])
                          else "Vacant" if len(occupancy[bay]) == expected_samples[bay] else "Unknown",
                          "changedAt": min((x["changedAt"] for x in occupancy[bay] if x["state"] == "occupied"), default=None)}
                         for bay, name in bays.items()],
                "appointments": appointments, "unassigned": exceptions, "checkins": checkins}
