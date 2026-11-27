"""Observe-only access and camera passage episodes, scored after late delivery."""

from datetime import datetime, timezone
import json
import sqlite3
import time

from site_activity import valid_event_device


ACCESS_FACTS = {"access-granted", "door-opened", "door-closed"}
MAX_OPEN = 30
LATE = 15
GRANT_BEFORE_OPEN = 10


def utc(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


def stamp(value):
    return datetime.fromisoformat(value).timestamp()


class DoorPassages:
    def __init__(self, activity):
        self.activity = activity
        with activity.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS door_passage_episodes (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, door TEXT NOT NULL, source_id TEXT NOT NULL,
                    camera TEXT NOT NULL, boundary TEXT NOT NULL, rule TEXT NOT NULL, version INTEGER NOT NULL,
                    anchor_kind TEXT NOT NULL, anchor_at REAL NOT NULL, due REAL NOT NULL,
                    state TEXT NOT NULL, body TEXT, UNIQUE(door,source_id)
                );
                CREATE INDEX IF NOT EXISTS door_episode_due ON door_passage_episodes(state,due);
                CREATE INDEX IF NOT EXISTS door_episode_scope ON door_passage_episodes(camera,seq);
                CREATE TABLE IF NOT EXISTS door_passage_heartbeat (
                    camera TEXT NOT NULL, version INTEGER NOT NULL, sampled REAL NOT NULL, healthy INTEGER NOT NULL,
                    PRIMARY KEY(camera,sampled)
                );
                CREATE INDEX IF NOT EXISTS door_heartbeat_time ON door_passage_heartbeat(sampled);
                CREATE INDEX IF NOT EXISTS door_event_time ON monitored_events(
                    sensor,json_extract(body,'$.observation.occurredAt')
                ) WHERE json_extract(body,'$.observation.eventType') IN ('access-granted','door-opened','door-closed','line-crossing');
            """)

    @staticmethod
    def heartbeat(db, camera, status):
        now = status["reportedAt"]
        healthy = (status["state"] == "running" and status["fps"] > 0
                   and status["frameAgeSeconds"] is not None and status["frameAgeSeconds"] <= 2)
        db.execute("INSERT OR REPLACE INTO door_passage_heartbeat VALUES(?,?,?,?)",
                   (camera, status["version"], now, healthy))
        db.execute("DELETE FROM door_passage_heartbeat WHERE sampled<?", (now - 300,))

    @staticmethod
    def observe(db, config, version, event, observed, detail):
        kind = event["eventType"]
        if kind not in ("door-opened", "access-granted"):
            return
        door = next((sensor for sensor in config["sensors"] if sensor["id"] == event["sensorId"]), {})
        camera_id, rule_id = door.get("linkedCameraId"), door.get("passageRuleId")
        if not camera_id or not rule_id:
            return
        camera = next((item for item in config["sensors"] if item["id"] == camera_id), {})
        rule = next((item for item in camera.get("analyticsRules", ()) if item["id"] == rule_id), None)
        if not rule or not any(zone["id"] == rule["boundaryId"] for zone in detail["boundaries"]):
            return
        due = observed + (MAX_OPEN + LATE + GRANT_BEFORE_OPEN if kind == "access-granted" else LATE)
        db.execute("""
            INSERT OR IGNORE INTO door_passage_episodes(door,source_id,camera,boundary,rule,version,anchor_kind,anchor_at,due,state)
            VALUES(?,?,?,?,?,?,?,?,?,'pending')
        """, (door["id"], event["id"], camera_id, rule["boundaryId"], rule_id, version, kind, observed, due))

    @staticmethod
    def camera_covered(db, camera, version, opened, closed):
        rows = db.execute("""
            SELECT sampled,healthy,version FROM door_passage_heartbeat
            WHERE camera=? AND sampled BETWEEN ? AND ? ORDER BY sampled
        """, (camera, opened - LATE, closed + LATE)).fetchall()
        left = [row for row in rows if row[0] <= opened]
        right = [row for row in rows if row[0] >= closed]
        if not left or not right:
            return False
        window = [left[-1], *(row for row in rows if opened < row[0] < closed), right[0]]
        return (all(healthy and seen_version == version for _, healthy, seen_version in window)
                and all(after[0] - before[0] <= LATE for before, after in zip(window, window[1:])))

    def settle(self, db, row, now):
        seq, door, source_id, camera, boundary, rule, version, kind, anchor = row
        bodies = db.execute("""
            SELECT body FROM monitored_events WHERE sensor IN (?,?)
              AND json_extract(body,'$.observation.eventType') IN ('access-granted','door-opened','door-closed','line-crossing')
              AND json_extract(body,'$.observation.occurredAt') BETWEEN ? AND ?
              AND json_extract(body,'$.configurationVersion')=?
            ORDER BY json_extract(body,'$.observation.occurredAt'), seq
        """, (door, camera, utc(anchor - MAX_OPEN), utc(anchor + MAX_OPEN), version)).fetchall()
        events = [value for (body,) in bodies if (value := json.loads(body))["cameraId"] == camera
                  and any(item["id"] == boundary for item in value["boundaries"])]
        access = [(item, stamp(value["occurredAt"]), value["eventType"]) for item in events if (value := item["observation"])["sensorId"] == door]
        if kind == "access-granted":
            has_open = any(event == "door-opened" and anchor <= at <= anchor + GRANT_BEFORE_OPEN for _, at, event in access)
            earlier_grant = any(event == "access-granted" and at >= anchor - GRANT_BEFORE_OPEN
                                and (at, item["observation"]["id"]) < (anchor, source_id)
                                for item, at, event in access)
            if has_open or earlier_grant:
                db.execute("UPDATE door_passage_episodes SET state='suppressed' WHERE seq=?", (seq,))
                return
        closes = [at for _, at, event in access if event == "door-closed" and anchor < at <= anchor + MAX_OPEN]
        close = closes[0] if closes and kind == "door-opened" else None
        cutoff = (close if close is not None else anchor + MAX_OPEN) + LATE
        if now < cutoff:
            db.execute("UPDATE door_passage_episodes SET due=? WHERE seq=?", (cutoff, seq))
            return
        end = close if close is not None else anchor + MAX_OPEN
        grants = [item for item, at, event in access if event == "access-granted" and anchor - GRANT_BEFORE_OPEN <= at <= end]
        previous_open = max((at for item, at, event in access if event == "door-opened"
                             and (at, item["observation"]["id"]) < (anchor, source_id)), default=None)
        unclosed_previous = previous_open is not None and not any(event == "door-closed" and previous_open < at <= anchor for _, at, event in access)
        other_opens = [at for item, at, event in access if event == "door-opened" and anchor <= at < end and item["observation"]["id"] != source_id]
        inbound = []
        for item in events:
            observation = item["observation"]
            evidence = observation.get("analytics", {})
            occurred = stamp(observation["occurredAt"])
            if (observation["sensorId"] == camera and observation["eventType"] == "line-crossing"
                    and evidence.get("ruleId") == rule and evidence.get("source") == "live" and anchor <= occurred <= end):
                inbound.append(item)
        timeline = grants + [item for item, at, event in access if anchor <= at <= end and event != "access-granted"] + inbound
        timeline.sort(key=lambda item: (item["observation"]["occurredAt"], item["observation"]["id"]))
        reliable_times = all(-2 <= item["observation"].get("gatewayReceivedAt", now) - stamp(item["observation"]["occurredAt"]) <= LATE
                             and item["observation"].get("gatewayReceivedAt", now) <= cutoff for item in timeline)
        tracks = {item["observation"]["analytics"]["trackId"] for item in inbound}
        if close is None:
            reason = "Door open and close evidence was incomplete."
        elif len(grants) != 1 or other_opens or unclosed_previous:
            reason = "The episode did not have one unambiguous access grant and door opening."
        elif not reliable_times or not self.camera_covered(db, camera, version, anchor, close):
            reason = "Camera coverage or event timing could not be verified."
        else:
            reason = ""
        result = "insufficient" if reason else "possible-extra" if len(tracks) > 1 else "normal"
        body = {"id": seq, "doorId": door, "cameraId": camera, "boundaryId": boundary, "version": version, "startedAt": anchor,
                "endedAt": close, "state": result, "reason": reason, "grants": len(grants), "inboundTracks": len(tracks), "events": timeline[:100]}
        db.execute("UPDATE door_passage_episodes SET state=?,body=? WHERE seq=?", (result, json.dumps(body), seq))

    def run_due(self, now=None):
        now = time.time() if now is None else now
        with self.activity.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            due = db.execute("""
                SELECT seq,door,source_id,camera,boundary,rule,version,anchor_kind,anchor_at FROM door_passage_episodes
                WHERE state='pending' AND due<=? ORDER BY due,seq LIMIT 32
            """, (now,)).fetchall()
            for row in due:
                self.settle(db, row, now)

    def poll(self, stopping):
        while not stopping.wait(1):
            try:
                self.run_due()
            except sqlite3.OperationalError:
                # A busy gateway keeps the durable episodes for the next tick.
                continue

    def read(self, actor, query):
        camera = query.get("camera", [""])[0]
        status = query.get("status", ["review"])[0]
        if (camera and not valid_event_device(camera)) or status not in ("review", "all"):
            raise ValueError("Choose valid passage review filters.")
        states = ("possible-extra", "insufficient", "normal") if status == "all" else ("possible-extra", "insufficient")
        conditions = ["state IN (" + ",".join("?" for _ in states) + ")"]
        args = list(states)
        if camera:
            conditions.append("camera=?")
            args.append(camera)
        if actor["cameras"] != "*":
            allowed = actor["cameras"]
            conditions.append("camera IN (" + ",".join("?" for _ in allowed) + ")" if allowed else "0")
            args.extend(allowed)
        with self.activity.connect() as db:
            rows = db.execute("SELECT body FROM door_passage_episodes WHERE " + " AND ".join(conditions)
                              + " ORDER BY anchor_at DESC,seq DESC LIMIT 40", args).fetchall()
        return {"episodes": [json.loads(row[0]) for row in rows]}
