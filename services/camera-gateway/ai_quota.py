"""Daily site budget shared by operator requests and automatic reviews."""

from datetime import datetime, timezone
import time

from onvif import CameraError
from site_accounts import manages_site
from site_collaboration import Conflict, iso_now


DAY_SECONDS = 86400
MAX_DAILY_CALLS = 500
OPERATOR_SOURCES = ("handoff", "clip-review", "video-review")
AUTOMATIC_FRAME_KIND = "automatic-frame-review"
AUTOMATIC_LIMIT_MESSAGE = "The site has reached its automatic AI review limit. Operator requests can use the remaining site quota."


def day_start(now):
    return int(now) // DAY_SECONDS * DAY_SECONDS


def job_usage(db, start, end):
    counts = dict(db.execute("""SELECT kind,count(*) FROM site_ai_jobs WHERE created>=? AND created<?
                                AND coalesce(json_extract(audit,'$.provider'),'') NOT IN ('template','none')
                                GROUP BY kind""", (start, end)))
    fallbacks = db.execute("""SELECT count(*) FROM site_ai_jobs WHERE
                              json_extract(audit,'$.videoFallback.submittedAt')>=?
                              AND json_extract(audit,'$.videoFallback.submittedAt')<?""", (start, end)).fetchone()[0]
    return sum(counts.get(source, 0) for source in OPERATOR_SOURCES), counts.get(AUTOMATIC_FRAME_KIND, 0), fallbacks


def carry_job_usage(db, now):
    """Keep today's quota counts when a backup strips private job rows."""
    db.execute("""CREATE TABLE IF NOT EXISTS site_ai_quota_carried (
        day INTEGER PRIMARY KEY, operator_calls INTEGER NOT NULL, frame_calls INTEGER NOT NULL, fallback_calls INTEGER NOT NULL
    )""")
    start = day_start(now)
    db.execute("DELETE FROM site_ai_quota_carried WHERE day<>?", (start,))
    db.execute("""INSERT INTO site_ai_quota_carried VALUES(?,?,?,?)
                  ON CONFLICT(day) DO UPDATE SET operator_calls=operator_calls+excluded.operator_calls,
                  frame_calls=frame_calls+excluded.frame_calls, fallback_calls=fallback_calls+excluded.fallback_calls""",
               (start, *job_usage(db, start, start + DAY_SECONDS)))


class AIQuota:
    def __init__(self, gateway, daily_limit):
        self.gateway, self.activity = gateway, gateway.activity
        self.default = (daily_limit, (daily_limit + 2) // 3, 0)
        with self.activity.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS site_ai_quota_setting (
                singleton INTEGER PRIMARY KEY CHECK(singleton=1), daily_limit INTEGER NOT NULL,
                operator_reserve INTEGER NOT NULL, revision INTEGER NOT NULL
            )""")
            history = {name for (name,) in db.execute("""SELECT name FROM sqlite_master WHERE type='table'
                AND name IN ('automatic_triage_reports','site_ai_quota_carried')""")}
            self.has_triage_history = "automatic_triage_reports" in history
            self.has_carried_usage = "site_ai_quota_carried" in history

    def policy(self, db):
        return db.execute("""SELECT daily_limit,operator_reserve,revision
                             FROM site_ai_quota_setting WHERE singleton=1""").fetchone() or self.default

    def automatic_limit(self, db):
        total, reserve, _ = self.policy(db)
        return total - reserve

    def usage(self, db, now):
        start = day_start(now)
        end = start + DAY_SECONDS
        operator, frame, fallback = job_usage(db, start, end)
        if self.has_carried_usage:
            carried = db.execute("SELECT operator_calls,frame_calls,fallback_calls FROM site_ai_quota_carried WHERE day=?",
                                 (start,)).fetchone()
            if carried:
                operator += carried[0]
                frame += carried[1]
                fallback += carried[2]
        triage = db.execute("SELECT count(*) FROM automatic_triage_reports WHERE submitted>=? AND submitted<?",
                            (start, end)).fetchone()[0] if self.has_triage_history else 0
        return {"operator": operator, "frame": frame, "triage": triage, "fallback": fallback}

    @staticmethod
    def remaining(usage, total, reserve):
        manual = usage["operator"]
        automatic = usage["frame"] + usage["triage"] + usage["fallback"]
        operator_remaining = max(0, total - manual - automatic)
        automatic_remaining = min(operator_remaining, max(0, total - reserve - automatic))
        protected_remaining = min(operator_remaining, max(0, reserve - manual))
        return operator_remaining, automatic_remaining, protected_remaining

    def snapshot(self, db, now):
        total, reserve, revision = self.policy(db)
        usage = self.usage(db, now)
        operator, automatic, protected = self.remaining(usage, total, reserve)
        return {"dailyLimit": total, "operatorReserve": reserve, "revision": revision, "usage": usage,
                "operatorRemaining": operator, "automaticRemaining": automatic, "protectedRemaining": protected}

    def read(self, actor, payload=None):
        can_manage = manages_site(actor)
        if payload is not None:
            if not can_manage:
                raise PermissionError("A site supervisor is required to change the AI quota.")
            total, reserve, revision = (payload.get(key) for key in ("dailyLimit", "operatorReserve", "revision"))
            if (type(total) is not int or not 1 <= total <= MAX_DAILY_CALLS or
                    type(reserve) is not int or not 1 <= reserve <= total or type(revision) is not int):
                raise CameraError("Enter a daily limit from 1 to 500 and reserve between 1 and that limit for operators.")
        with self.activity.connect() as db:
            db.execute("BEGIN" if payload is None else "BEGIN IMMEDIATE")
            if payload is not None:
                row = db.execute("SELECT * FROM organization_accounts WHERE subject=? AND active=1", (actor["subject"],)).fetchone()
                account = self.gateway.collaboration.accounts.public_entry(row) if row else None
                if not account or not manages_site(account) or account["changedAt"] != actor["changedAt"]:
                    raise PermissionError("Your site permissions changed. Sign in again.")
                old_total, old_reserve, current = self.policy(db)
                if revision != current:
                    raise Conflict("Another supervisor changed the AI quota. Review the current settings and try again.")
                if (total, reserve) != (old_total, old_reserve):
                    db.execute("INSERT OR REPLACE INTO site_ai_quota_setting VALUES(1,?,?,?)", (total, reserve, current + 1))
                    db.execute("INSERT INTO activity_audit(occurred_at,actor,action,resource,detail) VALUES(?,?,?,?,?)",
                               (iso_now(), actor["subject"], "ai-quota-setting", self.gateway.monitor.site,
                                f"Daily limit {old_total} to {total}, operator reserve {old_reserve} to {reserve}"))
            now = time.time()
            midnight = day_start(now) + DAY_SECONDS
            return {"available": True, "canManage": can_manage, **self.snapshot(db, now),
                    "resetsAt": datetime.fromtimestamp(midnight, timezone.utc).isoformat()}
