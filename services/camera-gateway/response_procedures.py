"""Published incident procedures and gateway-owned execution state."""

import json
import time

from onvif import CameraError

TRIGGERS = ("door-forced-open", "door-held-open", "coverage-loss")
STEP_KINDS = ("review", "question", "finding", "contact", "outcome")


def normalize_procedures(raw, boundaries):
    from site_collaboration import text_field
    from site_activity import valid_event_device

    if not isinstance(raw, list) or len(raw) > 24:
        raise CameraError("Configure up to 24 response procedures.")
    result, assignments = [], set()
    for item in raw:
        if not isinstance(item, dict) or item.get("trigger") not in TRIGGERS:
            raise CameraError("Choose a supported procedure trigger.")
        trigger, boundary = item["trigger"], item.get("boundaryId") or None
        steps, deadline = item.get("steps"), item.get("dueSeconds", 300)
        if (boundary and (not isinstance(boundary, str) or boundary not in boundaries)
                or trigger == "coverage-loss" and not boundary or (trigger, boundary) in assignments
                or not isinstance(steps, list) or not 1 <= len(steps) <= 20
                or type(deadline) is not int or not 30 <= deadline <= 86400):
            raise CameraError("Choose a unique trigger and boundary, a valid due time, and up to 20 steps.")
        assignments.add((trigger, boundary))
        definition = {}
        for step in steps:
            if (not isinstance(step, dict) or not valid_event_device(step.get("id"))
                    or step["id"] in definition or step.get("kind") not in STEP_KINDS):
                raise CameraError("Each procedure step requires a unique ID and a supported type.")
            kind = step["kind"]
            edges = ("yes", "no") if kind == "question" else ("next",)
            destinations = {edge: step.get(edge) or None for edge in edges}
            if any(value is not None and not valid_event_device(value) for value in destinations.values()):
                raise CameraError("Choose valid next steps.")
            definition[step["id"]] = {"id": step["id"], "kind": kind, "text": text_field(step, "text", 300), **destinations}
        visiting, visited = set(), set()

        def walk(key):
            if key is None or key in visited:
                return
            if key in visiting or key not in definition:
                raise CameraError("Procedure branches must terminate and use existing step IDs.")
            visiting.add(key)
            step = definition[key]
            for edge in ("next", "yes", "no"):
                walk(step.get(edge))
            visiting.remove(key)
            visited.add(key)

        walk(steps[0]["id"])
        if len(visited) != len(steps):
            raise CameraError("Every procedure step must be reachable from the first step.")
        result.append({"name": text_field(item, "name", 80), "trigger": trigger, "boundaryId": boundary,
                       "dueSeconds": deadline, "steps": list(definition.values())})
    return result


class ResponseProcedures:
    def __init__(self, activity, organization, site):
        self.activity, self.organization, self.site = activity, organization, site
        with activity.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS incident_procedures (
                    incident TEXT PRIMARY KEY, version INTEGER NOT NULL, definition TEXT NOT NULL,
                    step TEXT, due REAL NOT NULL, escalated INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS procedure_actions (
                    incident TEXT NOT NULL, action_id TEXT NOT NULL, subject TEXT NOT NULL,
                    request TEXT NOT NULL, response TEXT NOT NULL, PRIMARY KEY(incident,action_id)
                );
            """)

    def start(self, db, snapshot, version, incident, trigger, boundary, now=None):
        candidates = [item for item in snapshot.get("procedures", ()) if item["trigger"] == trigger
                      and item["boundaryId"] in (None, boundary)]
        if candidates:
            chosen = max(candidates, key=lambda item: item["boundaryId"] == boundary)
            db.execute("INSERT OR IGNORE INTO incident_procedures(incident,version,definition,step,due) VALUES(?,?,?,?,?)",
                       (incident, version, json.dumps(chosen), chosen["steps"][0]["id"],
                        (time.time() if now is None else now) + chosen["dueSeconds"]))

    @staticmethod
    def detail(db, incident):
        row = db.execute("SELECT version,definition,step,due,escalated FROM incident_procedures WHERE incident=?",
                         (incident,)).fetchone()
        return {"version": row[0], "definition": json.loads(row[1]), "step": row[2], "dueAt": row[3],
                "escalated": bool(row[4])} if row else None

    def read(self, actor, incident):
        from site_collaboration import SiteCollaboration
        if not isinstance(incident, str) or not 1 <= len(incident) <= 450:
            raise CameraError("Choose a valid incident.")
        with self.activity.connect() as db:
            source = db.execute("SELECT camera FROM monitored_alerts WHERE id=? AND member_of IS NULL", (incident,)).fetchone()
            if not source or not SiteCollaboration.can_view(actor, source[0]):
                raise PermissionError("Choose an incident available to your account.")
            return {"procedure": self.detail(db, incident)}

    def advance(self, db, incident, action, actor):
        from site_collaboration import Conflict, text_field
        from site_accounts import manages_site

        current = self.detail(db, incident)
        if not current or current["step"] is None:
            raise Conflict("This incident has no active procedure step.")
        if action["kind"] == "procedure-override":
            if not manages_site(actor):
                raise PermissionError("Only a site supervisor can override a procedure.")
            result = {"kind": action["kind"], "reason": text_field(action, "reason", 500)}
            next_step = None
        else:
            step = next(item for item in current["definition"]["steps"] if item["id"] == current["step"])
            if action.get("step") != step["id"]:
                raise Conflict("The current response step changed. Refresh the incident.")
            allowed = {"question": ("yes", "no"), "review": ("reviewed", "unavailable", "inconclusive"),
                       "contact": ("contacted", "unavailable"), "finding": ("recorded",), "outcome": ("recorded",)}
            answer = action.get("answer")
            if answer not in allowed[step["kind"]]:
                raise CameraError("Choose a supported answer for the current step.")
            note = text_field(action, "text", 500, step["kind"] not in ("finding", "outcome"))
            next_step = step[answer] if step["kind"] == "question" else step["next"]
            result = {"kind": action["kind"], "step": step["id"], "label": step["text"], "answer": answer, "text": note}
        db.execute("UPDATE incident_procedures SET step=? WHERE incident=?", (next_step, incident))
        return {**result, "subject": actor["subject"], "name": actor["name"]}

    def enqueue_overdue(self, db, now):
        rows = db.execute("""
            SELECT p.incident,p.due,a.body FROM incident_procedures p
            JOIN monitored_alerts a ON a.id=p.incident
            LEFT JOIN shared_incidents i ON i.id=p.incident
            WHERE p.step IS NOT NULL AND p.escalated=0 AND p.due<=? AND coalesce(i.closed,0)=0
            ORDER BY p.due LIMIT 50
        """, (now,)).fetchall()
        from hashlib import sha256
        for incident, due, body in rows:
            alert = json.loads(body)
            delivery = sha256(f"{self.organization}/{self.site}/{incident}/procedure-deadline".encode()).hexdigest()
            payload = {"type": "incident-procedure-overdue", "organizationId": self.organization, "siteId": self.site,
                       "incidentId": incident, "boundaryId": alert["zoneId"], "responseTeam": alert.get("responseTeam"),
                       "dueAt": due, "deliveryId": delivery}
            db.execute("INSERT INTO alert_deliveries(payload) VALUES(?)", (json.dumps(payload),))
            db.execute("UPDATE incident_procedures SET escalated=1 WHERE incident=?", (incident,))
