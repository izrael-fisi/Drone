"""Site-scoped review of CCTV observations collected during commissioning."""

import json

from onvif import CameraError
from site_activity import valid_event_device
from site_collaboration import Conflict, iso_now, text_field

OUTCOMES = ("confirmed", "false-detection", "unverifiable")
OBSERVED = "json_extract(event.body,'$.observation.analytics.mode')='observe'"
PAGE_SIZE = 25
EMPTY_COUNTS = dict.fromkeys(("pending", *OUTCOMES), 0)


class AnalyticsReview:
    def __init__(self, activity):
        self.activity = activity
        with activity.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS analytics_reviews (
                    event_seq INTEGER PRIMARY KEY, revision INTEGER NOT NULL, outcome TEXT NOT NULL,
                    actor TEXT NOT NULL, reviewed_at TEXT NOT NULL, note TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS observed_cctv_events ON monitored_events(camera,seq)
                    WHERE json_extract(body,'$.observation.analytics.mode')='observe';
                CREATE INDEX IF NOT EXISTS observed_cctv_versions
                    ON monitored_events(json_extract(body,'$.configurationVersion'),camera)
                    WHERE json_extract(body,'$.observation.analytics.mode')='observe';
            """)

    def read(self, actor, query):
        camera = query.get("camera", [""])[0]
        source = query.get("source", ["live"])[0]
        status = query.get("status", ["pending"])[0]
        before = int(query.get("before", [str(2**63 - 1)])[0])
        version = int(query.get("version", ["0"])[0])
        if ((camera and not valid_event_device(camera)) or source not in ("live", "replay")
                or status not in ("all", "pending", *OUTCOMES) or not 0 < before < 2**63 or not 0 <= version < 2**53):
            raise ValueError("Choose valid CCTV review filters.")
        conditions = [OBSERVED, "json_extract(event.body,'$.observation.analytics.source')=?"]
        args = [source]
        if actor["cameras"] != "*":
            allowed = actor["cameras"]
            conditions.append("event.camera IN (" + ",".join("?" for _ in allowed) + ")" if allowed else "0")
            args.extend(allowed)
        if camera:
            conditions.append("event.camera=?")
            args.append(camera)
        if version:
            conditions.append("json_extract(event.body,'$.configurationVersion')=?")
            args.append(version)
        joined = " FROM monitored_events event LEFT JOIN analytics_reviews review ON review.event_seq=event.seq WHERE " + " AND ".join(conditions)
        with self.activity.connect() as db:
            db.execute("BEGIN")
            counts = EMPTY_COUNTS.copy()
            for outcome, count in db.execute("SELECT coalesce(review.outcome,'pending'),count(*)" + joined + " GROUP BY 1", args):
                counts[outcome] = count
            if status != "all":
                joined += " AND review.outcome IS NULL" if status == "pending" else " AND review.outcome=?"
                if status != "pending":
                    args.append(status)
            rows = db.execute("SELECT event.seq,event.body,review.revision,review.outcome,review.actor,review.reviewed_at,review.note"
                              + joined + " AND event.seq<? ORDER BY event.seq DESC LIMIT ?", [*args, before, PAGE_SIZE + 1]).fetchall()
        return {"entries": [{"sequence": seq, "event": json.loads(body),
                            "review": {"revision": revision, "outcome": outcome, "reviewer": author, "reviewedAt": stamp, "note": note} if revision else None}
                            for seq, body, revision, outcome, author, stamp, note in rows[:PAGE_SIZE]],
                "counts": counts, "before": rows[PAGE_SIZE - 1][0] if len(rows) > PAGE_SIZE else None}

    def report(self, actor, query):
        source = query.get("source", ["live"])[0]
        version = int(query.get("version", ["0"])[0])
        if source not in ("live", "replay") or not 0 <= version < 2**53:
            raise ValueError("Choose a valid report version and source.")
        with self.activity.connect() as db:
            db.execute("BEGIN")
            versions = [row[0] for row in db.execute("SELECT version FROM configuration_versions ORDER BY version DESC LIMIT 30")]
            if not versions:
                return {"version": 0, "versions": [], "rules": []}
            version = version or versions[0]
            row = db.execute("SELECT body FROM configuration_versions WHERE version=?", (version,)).fetchone()
            if not row:
                raise ValueError("Choose an available published version.")
            snapshot = json.loads(row[0])
            cameras = [sensor for sensor in snapshot["sensors"] if sensor.get("analyticsRules")
                       and (actor["cameras"] == "*" or sensor["id"] in actor["cameras"])]
            groups = {}
            if cameras:
                ids = [sensor["id"] for sensor in cameras]
                visible = ",".join("?" for _ in ids)
                rows = db.execute(f"""
                    WITH observations AS (
                        SELECT event.camera, json_extract(event.body,'$.observation.analytics.ruleId') AS rule_id,
                               coalesce(json_extract(event.body,'$.observation.analytics.profile'),'all-day') AS profile,
                               coalesce(review.outcome,'pending') AS outcome,
                               json_extract(event.body,'$.observation.gatewayReceivedAt') -
                               (julianday(json_extract(event.body,'$.observation.occurredAt')) - 2440587.5) * 86400.0 AS delay
                        FROM monitored_events event LEFT JOIN analytics_reviews review ON review.event_seq=event.seq
                        WHERE {OBSERVED} AND json_extract(event.body,'$.configurationVersion')=?
                              AND json_extract(event.body,'$.observation.analytics.source')=? AND event.camera IN ({visible})
                    )
                    SELECT camera,rule_id,profile,outcome,count(*),
                           sum(CASE WHEN delay >= 0 THEN delay END),count(CASE WHEN delay >= 0 THEN 1 END)
                    FROM observations GROUP BY camera,rule_id,profile,outcome
                """, [version, source, *ids])
                for camera, rule_id, profile, outcome, count, delay, samples in rows:
                    group = groups.setdefault((camera, rule_id, profile), {"counts": EMPTY_COUNTS.copy(), "delay": 0, "samples": 0})
                    group["counts"][outcome] = count
                    group["delay"] += delay or 0
                    group["samples"] += samples
        rules = []
        for camera in cameras:
            for rule in camera["analyticsRules"]:
                profiles = []
                for profile in ("day", "night") if rule.get("schedule") else ("all-day",):
                    group = groups.get((camera["id"], rule["id"], profile))
                    samples = group["samples"] if group and source == "live" else 0
                    profiles.append({"profile": profile, "counts": group["counts"] if group else EMPTY_COUNTS.copy(), "latencySamples": samples,
                                     "meanGatewaySeconds": round(group["delay"] / samples, 3) if samples else None})
                rules.append({"cameraId": camera["id"], "cameraName": camera["name"], "ruleId": rule["id"], "ruleName": rule["name"],
                              "mode": rule["mode"], "profiles": profiles})
        return {"version": version, "versions": versions, "rules": rules}

    def change(self, actor, payload):
        sequence, revision, outcome = (payload.get(field) for field in ("sequence", "revision", "outcome"))
        if actor["role"] == "viewer":
            raise PermissionError("This account has read-only CCTV review access.")
        if (type(sequence) is not int or not 0 < sequence < 2**53 or type(revision) is not int
                or not 0 <= revision < 2**53 or outcome not in OUTCOMES):
            raise CameraError("Choose a CCTV observation and review decision.")
        note = text_field(payload, "note", 350, True)
        now = iso_now()
        with self.activity.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            entry = db.execute("SELECT event.camera,event.source_id,review.revision FROM monitored_events event"
                               " LEFT JOIN analytics_reviews review ON review.event_seq=event.seq"
                               f" WHERE event.seq=? AND {OBSERVED}", (sequence,)).fetchone()
            if not entry or actor["cameras"] != "*" and entry[0] not in actor["cameras"]:
                raise PermissionError("This observation is unavailable to your account.")
            if revision != (entry[2] or 0):
                raise Conflict("Another operator reviewed this observation. Refresh it before saving again.")
            result = {"revision": revision + 1, "outcome": outcome, "reviewer": actor["name"], "reviewedAt": now, "note": note}
            db.execute("INSERT INTO analytics_reviews VALUES(?,?,?,?,?,?) ON CONFLICT(event_seq) DO UPDATE SET"
                       " revision=excluded.revision,outcome=excluded.outcome,actor=excluded.actor,reviewed_at=excluded.reviewed_at,note=excluded.note",
                       (sequence, result["revision"], outcome, actor["name"], now, note))
            db.execute("INSERT INTO activity_audit(occurred_at,actor,action,resource,detail) VALUES(?,?,?,?,?)",
                       (now, actor["subject"], "cctv-review", entry[0], json.dumps({"event": entry[1], **result})))
        return result
