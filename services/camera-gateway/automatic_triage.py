"""Optional, site-local automatic incident review using recorded timeline facts."""

import json

from ai_jobs import INSTRUCTIONS, LIFETIME, references, schema_object
from axis_archive import parse_utc
from onvif import CameraError
from site_activity import EVENT_LABELS
from site_accounts import manages_site
from site_collaboration import Conflict, SiteCollaboration, iso_now

SETTLE_SECONDS = 30
MAX_DELAY = 300
RETENTION = 30 * LIFETIME
TRIAGE_INSTRUCTIONS = INSTRUCTIONS + (
    " Select up to six relevant timeline records, including corroborating events when available. "
    "Fill keyEvents with exact copies of the selected records' ref fields."
)
RECORDED_FACTS = {
    **{kind: f"{label} reported" for kind, label in EVENT_LABELS.items()},
    "door-forced-open": "Forced-open door reported",
    "door-held-open": "Held-open door reported",
    "access-granted": "Access grant recorded",
    "door-opened": "Door opening reported",
    "door-closed": "Door closure reported",
    "rf-detection": "RF detection reported",
    "zone-entry": "Zone entry reported",
    "zone-dwell": "Time-in-zone event recorded",
    "restricted-stop": "Restricted stop reported",
    "drone-sighting": "Possible drone sighting reported",
    "coverage-loss": "Loss of camera coverage reported",
    "coverage-recovered": "Camera coverage restoration recorded",
}


def eligible(camera, raw):
    return bool(camera and raw.get("observation", {}).get("eventType") != "coverage-loss"
                and raw.get("observation", {}).get("analytics", {}).get("source") != "replay")


def select_events(sources, keys):
    by_ref = {event["ref"]: event for event in sources}
    selected = dict.fromkeys(["incident", *keys])
    return sorted((by_ref[key] for key in selected), key=lambda event: (parse_utc(event["occurredAt"]), event["ref"]))


def recorded_summary(events):
    facts = {}
    for event in events:
        text = RECORDED_FACTS.get(event.get("type"), "Device event recorded")
        facts.setdefault(text, []).append(event["ref"])
    return [{"text": text, "evidenceRefs": refs} for text, refs in facts.items()]


class AutomaticTriage:
    def __init__(self, jobs, limit, daily_limit):
        self.jobs, self.activity = jobs, jobs.activity
        if limit is not None and (type(limit) is not int or not 1 <= limit <= daily_limit):
            raise CameraError("Set an automatic triage limit within the site's daily AI quota.")
        self.limit, self.last_cleanup = limit, 0
        with self.activity.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS automatic_triage_setting (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1), enabled INTEGER NOT NULL,
                    revision INTEGER NOT NULL, cursor INTEGER NOT NULL
                );
                INSERT OR IGNORE INTO automatic_triage_setting VALUES (1,0,0,0);
                CREATE TABLE IF NOT EXISTS automatic_triage_reports (
                    incident TEXT PRIMARY KEY, state TEXT NOT NULL, created REAL NOT NULL, submitted REAL,
                    result TEXT NOT NULL, reason TEXT, model TEXT
                );
                CREATE INDEX IF NOT EXISTS automatic_triage_time ON automatic_triage_reports(submitted);
                CREATE INDEX IF NOT EXISTS automatic_triage_processing ON automatic_triage_reports(incident) WHERE state='processing';
                UPDATE automatic_triage_reports SET state='timeline',reason='restart' WHERE state='processing';
            """)

    @staticmethod
    def setting(db):
        return db.execute("SELECT enabled,revision,cursor FROM automatic_triage_setting WHERE singleton=1").fetchone()

    def status(self, actor, db):
        enabled, revision, _ = self.setting(db)
        limit = self.jobs.quota.automatic_limit(db)
        if self.limit is not None:
            limit = min(limit, self.limit)
        return {"available": True, "enabled": bool(enabled), "revision": revision, "canManage": manages_site(actor),
                "placement": self.jobs.provider.name, "model": self.jobs.provider.model, "dailyLimit": limit}

    def read(self, actor, identifier=None):
        with self.activity.connect() as db:
            db.execute("BEGIN")
            if not identifier:
                return self.status(actor, db)
            alert = db.execute("SELECT seq,camera,body FROM monitored_alerts WHERE id=? AND member_of IS NULL", (identifier,)).fetchone()
            if not alert or not SiteCollaboration.can_view(actor, alert[1]):
                raise PermissionError("This incident is unavailable to your account.")
            row = db.execute("SELECT state,result,reason FROM automatic_triage_reports WHERE incident=?", (identifier,)).fetchone()
            if row:
                state, stored, reason = row
                events = json.loads(stored)["events"]
                # Rebuild facts even for reviews saved before the model output was limited to references.
                return {"review": {"state": state, "events": events, "reason": reason,
                                   "summary": recorded_summary(events) if state == "ready" else []}}
            enabled, _, cutoff = self.setting(db)
            if enabled and alert[0] > cutoff and eligible(alert[1], json.loads(alert[2])):
                return {"review": {"state": "collecting"}}
            return {"review": None}

    def change(self, actor, payload):
        if not manages_site(actor):
            raise PermissionError("A site supervisor is required to change automatic triage.")
        enabled, request_revision = payload.get("enabled"), payload.get("revision")
        if type(enabled) is not bool or type(request_revision) is not int:
            raise CameraError("Supply the current setting revision and whether to enable automatic triage.")
        with self.activity.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if not (row := db.execute("SELECT * FROM organization_accounts WHERE subject=? AND active=1", (actor["subject"],)).fetchone()):
                raise PermissionError("Your site access changed.")
            account = self.jobs.gateway.collaboration.accounts.public_entry(row)
            if not manages_site(account) or actor["changedAt"] != account["changedAt"]:
                raise PermissionError("Your site permissions changed. Sign in again.")
            current, revision, _ = self.setting(db)
            if request_revision != revision:
                raise Conflict("The automatic triage setting changed. Refresh and try again.")
            if current != enabled:
                cursor = db.execute("SELECT coalesce(max(seq),0) FROM monitored_alerts").fetchone()[0]
                db.execute("UPDATE automatic_triage_setting SET enabled=?,revision=?,cursor=? WHERE singleton=1",
                           (int(enabled), revision + 1, cursor))
                if not enabled:
                    db.execute("UPDATE automatic_triage_reports SET state='timeline',reason='disabled' WHERE state='processing'")
                db.execute("INSERT INTO activity_audit(occurred_at,actor,action,resource,detail) VALUES(?,?,?,?,?)",
                           (iso_now(), actor["subject"], "automatic-triage-setting", self.jobs.gateway.monitor.site,
                            "enabled" if enabled else "disabled"))
            return self.status(actor, db)

    def take(self, now):
        with self.activity.connect() as db:
            enabled, _, cursor = self.setting(db)
            row = db.execute("""SELECT seq,id,camera,event_at,body FROM monitored_alerts
                                WHERE seq>? AND member_of IS NULL ORDER BY seq LIMIT 1""", (cursor,)).fetchone() if enabled else None
            if not row:
                return None
            sequence, identifier, camera, occurred, raw = row
            alert = json.loads(raw)
            received = alert["observation"].get("gatewayReceivedAt", occurred)
            use = eligible(camera, alert)
            if use and now < received + SETTLE_SECONDS:
                return None
            if use:
                sources = self.jobs.timeline_sources(db, identifier, raw, camera, triage=True)
                sources.sort(key=lambda event: (parse_utc(event["occurredAt"]), event["ref"]))
                fallback_keys = [event["ref"] for event in sources if event["ref"] != "incident"][:7]
                fallback = select_events(sources, fallback_keys)
            db.execute("BEGIN IMMEDIATE")
            enabled, revision, current = self.setting(db)
            if not enabled or current != cursor or self.jobs.operator_waiting(db, now):
                return None
            db.execute("UPDATE automatic_triage_setting SET cursor=? WHERE singleton=1", (sequence,))
            if not use:
                return None
            budget = self.jobs.quota.snapshot(db, now)
            reason = None
            if now - received >= MAX_DELAY or received - occurred >= MAX_DELAY:
                reason = "expired"
            elif not budget["automaticRemaining"] or (self.limit is not None and budget["usage"]["triage"] >= self.limit):
                reason = "limit"
            entry = db.execute("INSERT OR IGNORE INTO automatic_triage_reports VALUES(?,?,?,?,?,?,NULL)",
                               (identifier, "timeline" if reason else "processing", now, None if reason else now,
                                json.dumps({"events": fallback}), reason))
            if entry.rowcount and reason is None:
                return identifier, revision, sources

    def run_one(self, now):
        # One worker runs this synchronously. A leftover processing row was
        # claimed on a previous tick and already contains its recorded timeline.
        with self.activity.connect() as db:
            if db.execute("SELECT 1 FROM automatic_triage_reports WHERE state='processing' LIMIT 1").fetchone():
                db.execute("UPDATE automatic_triage_reports SET state='timeline',reason='interrupted' WHERE state='processing'")
            if now - self.last_cleanup >= 60:
                db.execute("DELETE FROM automatic_triage_reports WHERE created<?", (now - RETENTION,))
                self.last_cleanup = now
        job = self.take(now)
        if not job or self.jobs.gateway.stopping.is_set():
            return
        identifier, expected_revision, sources = job
        with self.activity.connect() as db:
            enabled, revision, _ = self.setting(db)
        if not enabled or revision != expected_revision or self.jobs.gateway.stopping.is_set():
            return
        try:
            available = {event["ref"] for event in sources}
            schema = schema_object(keyEvents={"type": "array", "items": {"type": "string", "enum": sorted(available)}})
            raw, _ = self.jobs.provider.generate(
                TRIAGE_INSTRUCTIONS, json.dumps(sources, ensure_ascii=False), [], schema)
            if not isinstance(raw, dict) or set(raw) != {"keyEvents"}:
                raise CameraError("The model returned an invalid incident review.")
            keys = references(raw["keyEvents"], available)
            if len(keys) > 6:
                raise CameraError("The review included too many key events.")
            events = select_events(sources, keys)
            with self.activity.connect() as db:
                db.execute("""UPDATE automatic_triage_reports SET state='ready',result=?,model=?
                              WHERE incident=? AND state='processing'""", (json.dumps({"events": events}), self.jobs.provider.model, identifier))
        except (CameraError, OSError, ValueError, KeyError, TypeError, IndexError):
            with self.activity.connect() as db:
                db.execute("UPDATE automatic_triage_reports SET state='timeline',reason='provider' WHERE incident=? AND state='processing'",
                           (identifier,))
