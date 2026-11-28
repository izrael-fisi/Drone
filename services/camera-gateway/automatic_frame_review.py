"""Site-owned frame reviews for new camera incidents with recorded footage."""

import json
import time
from hashlib import sha256
from secrets import token_hex

from ai_jobs import LIFETIME
from ai_quota import AUTOMATIC_FRAME_KIND, AUTOMATIC_LIMIT_MESSAGE
from axis_archive import RECORDING_READY_SECONDS
from onvif import CameraError
from site_accounts import manages_site
from site_collaboration import Conflict, SiteCollaboration, iso_now


MAX_DELAY_SECONDS = 300


class AutomaticFrameReview:
    def __init__(self, jobs):
        self.jobs = jobs
        self.activity = jobs.activity
        with self.activity.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS automatic_frame_review_setting (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1), enabled INTEGER NOT NULL,
                    revision INTEGER NOT NULL, cursor INTEGER NOT NULL
                );
                INSERT OR IGNORE INTO automatic_frame_review_setting VALUES(1,0,0,0);
                CREATE UNIQUE INDEX IF NOT EXISTS one_automatic_frame_per_incident
                    ON site_ai_jobs(incident) WHERE kind='automatic-frame-review';
            """)

    @staticmethod
    def setting(db):
        return db.execute("SELECT enabled,revision,cursor FROM automatic_frame_review_setting WHERE singleton=1").fetchone()

    def status(self, actor, db):
        enabled, revision, _ = self.setting(db)
        return {"available": True, "enabled": bool(enabled), "revision": revision, "canManage": manages_site(actor)}

    def eligible(self, camera, body):
        event = body["observation"]
        return (bool(camera) and body["modality"] == "vision" and event["sensorId"] == camera
                and body["zoneId"] in self.jobs.clip_boundaries
                and event["eventType"] not in ("coverage-loss", "coverage-recovered")
                and event.get("analytics", {}).get("source") != "replay")

    def read(self, actor, identifier=None):
        with self.activity.connect() as db:
            db.execute("BEGIN")
            if identifier is None:
                return self.status(actor, db)
            alert = db.execute("SELECT seq,camera,body FROM monitored_alerts WHERE id=? AND member_of IS NULL",
                               (identifier,)).fetchone()
            if not alert or actor["role"] == "viewer" or not SiteCollaboration.can_view(actor, alert[1]):
                raise PermissionError("This incident review is unavailable to your account.")
            row = db.execute("""SELECT state,output FROM site_ai_jobs WHERE incident=? AND kind=? AND created>?""",
                             (identifier, AUTOMATIC_FRAME_KIND, time.time() - LIFETIME)).fetchone()
            if row:
                state, output = row
                return {"review": {"state": state, **(json.loads(output) if output else {})}}
            enabled, _, cursor = self.setting(db)
            if enabled and alert[0] > cursor and self.eligible(alert[1], json.loads(alert[2])):
                return {"review": {"state": "collecting"}}
            return {"review": None}

    def change(self, actor, payload):
        if not manages_site(actor):
            raise PermissionError("A site supervisor is required to change automatic frame review.")
        enabled, requested = payload.get("enabled"), payload.get("revision")
        if type(enabled) is not bool or type(requested) is not int:
            raise CameraError("Supply the current setting revision and whether to enable automatic frame review.")
        with self.activity.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM organization_accounts WHERE subject=? AND active=1", (actor["subject"],)).fetchone()
            account = self.jobs.gateway.collaboration.accounts.public_entry(row) if row else None
            if not account or not manages_site(account) or account["changedAt"] != actor["changedAt"]:
                raise PermissionError("Your site permissions changed. Sign in again.")
            current, revision, _ = self.setting(db)
            if requested != revision:
                raise Conflict("The automatic frame review setting changed. Refresh and try again.")
            if current != enabled:
                cursor = db.execute("SELECT coalesce(max(seq),0) FROM monitored_alerts").fetchone()[0]
                db.execute("UPDATE automatic_frame_review_setting SET enabled=?,revision=revision+1,cursor=? WHERE singleton=1",
                           (int(enabled), cursor))
                db.execute("INSERT INTO activity_audit(occurred_at,actor,action,resource,detail) VALUES(?,?,?,?,?)",
                           (iso_now(), actor["subject"], AUTOMATIC_FRAME_KIND + "-setting", self.jobs.gateway.monitor.site,
                            "enabled" if enabled else "disabled"))
            return self.status(actor, db)

    def context(self, db, identifier, camera, inputs):
        enabled, revision, _ = self.setting(db)
        if not enabled or revision != inputs["revision"]:
            raise Conflict("The site changed its automatic frame review setting.")
        alert = db.execute("SELECT body,event_at FROM monitored_alerts WHERE id=? AND camera=? AND member_of IS NULL",
                           (identifier, camera)).fetchone()
        if not alert or sha256(alert[0].encode()).hexdigest() != inputs["anchor"] or not self.eligible(camera, json.loads(alert[0])):
            raise Conflict("The incident evidence changed. Check the recording in replay.")
        return alert[1]

    def take(self, now):
        with self.activity.connect() as db:
            enabled, revision, cursor = self.setting(db)
            if not enabled:
                return None
            row = db.execute("""SELECT seq,id,camera,event_at,body FROM monitored_alerts
                                WHERE seq>? AND member_of IS NULL AND camera IS NOT NULL
                                ORDER BY seq LIMIT 1""", (cursor,)).fetchone()
            if not row:
                return None
            sequence, incident, camera, occurred, raw = row
            body = json.loads(raw)
            eligible = self.eligible(camera, body)
            if eligible and now < occurred + RECORDING_READY_SECONDS:
                return None

            db.execute("BEGIN IMMEDIATE")
            if self.setting(db) != (enabled, revision, cursor) or self.jobs.operator_waiting(db, now):
                return None
            db.execute("UPDATE automatic_frame_review_setting SET cursor=? WHERE singleton=1", (sequence,))
            if not eligible:
                return None
            reason = None
            code = None
            if now - occurred >= MAX_DELAY_SECONDS:
                reason = "This incident is outside the automatic frame review window. Check its recording in replay."
                code = "expired"
            elif not self.jobs.quota.snapshot(db, now)["automaticRemaining"]:
                reason = AUTOMATIC_LIMIT_MESSAGE
                code = "limit"
            job = token_hex(16)
            inputs = json.dumps({"revision": revision, "anchor": sha256(raw.encode()).hexdigest()} if not reason else {})
            work = (job, "", "", incident, camera, AUTOMATIC_FRAME_KIND, inputs)
            entry = db.execute("INSERT OR IGNORE INTO site_ai_jobs VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                               (*work, "failed" if reason else "running", now, job,
                                json.dumps({"message": reason, "reason": code}) if reason else None,
                                json.dumps({"provider": "none"}) if reason else None))
            if entry.rowcount and not reason:
                return work
