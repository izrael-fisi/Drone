"""Search retained site evidence and maintain cases in the site database."""

from base64 import urlsafe_b64decode, urlsafe_b64encode
from datetime import timedelta
import json
import re
from secrets import token_hex
import time

from onvif import CameraError
from site_accounts import manages_site
from site_collaboration import Conflict, SiteCollaboration, due_date, iso_now, text_field

SOURCE_TABLES = {
    "event": ("monitored_events", "seq", "body"),
    "incident": ("monitored_alerts", "seq", "body"),
    "bookmark": ("shared_activity", "sequence", "payload"),
}


class EvidenceCases:
    def __init__(self, activity):
        self.activity = activity
        with activity.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS evidence_index (
                    row_id INTEGER PRIMARY KEY, source TEXT NOT NULL, source_row INTEGER NOT NULL, camera TEXT,
                    floor TEXT, occurred TEXT NOT NULL, event_type TEXT NOT NULL, object_class TEXT,
                    indexed REAL NOT NULL, label TEXT NOT NULL, body TEXT NOT NULL, UNIQUE(source,source_row)
                );
                CREATE INDEX IF NOT EXISTS evidence_time ON evidence_index(occurred DESC,row_id DESC);
                CREATE INDEX IF NOT EXISTS evidence_camera_time ON evidence_index(camera,occurred DESC,row_id DESC);
                CREATE TABLE IF NOT EXISTS evidence_index_progress (
                    source TEXT PRIMARY KEY, cursor INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS site_cases (
                    id TEXT PRIMARY KEY, revision INTEGER NOT NULL, incident TEXT, owner TEXT NOT NULL,
                    title TEXT NOT NULL, finding TEXT NOT NULL, closed INTEGER NOT NULL, updated TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS case_attachments (
                    case_id TEXT NOT NULL, id TEXT NOT NULL, camera TEXT, source TEXT NOT NULL,
                    source_row INTEGER NOT NULL, body TEXT NOT NULL, media TEXT,
                    PRIMARY KEY(case_id,id), UNIQUE(case_id,source,source_row)
                );
                CREATE TABLE IF NOT EXISTS case_activity (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT, case_id TEXT NOT NULL, revision INTEGER NOT NULL,
                    subject TEXT NOT NULL, actor TEXT NOT NULL, at TEXT NOT NULL, operation TEXT NOT NULL, details TEXT
                );
                CREATE INDEX IF NOT EXISTS case_activity_case ON case_activity(case_id,sequence);
            """)
            self.full_text = bool(db.execute("SELECT sqlite_compileoption_used('ENABLE_FTS5')").fetchone()[0])
            if self.full_text:
                rebuild = not db.execute("SELECT 1 FROM sqlite_master WHERE name='evidence_text_insert'").fetchone()
                db.executescript("""
                    CREATE VIRTUAL TABLE IF NOT EXISTS evidence_text USING fts5(label,content='evidence_index',content_rowid='row_id');
                    CREATE TRIGGER IF NOT EXISTS evidence_text_insert AFTER INSERT ON evidence_index BEGIN
                        INSERT INTO evidence_text(rowid,label) VALUES (new.row_id,new.label);
                    END;
                    CREATE TRIGGER IF NOT EXISTS evidence_text_delete AFTER DELETE ON evidence_index BEGIN
                        INSERT INTO evidence_text(evidence_text,rowid,label) VALUES ('delete',old.row_id,old.label);
                    END;
                """)
                if rebuild:
                    db.execute("INSERT INTO evidence_text(evidence_text) VALUES('rebuild')")
            else:
                db.executescript("DROP TRIGGER IF EXISTS evidence_text_insert; DROP TRIGGER IF EXISTS evidence_text_delete;")

    @staticmethod
    def write_index_row(db, source, row, camera, payload):
        value = json.loads(payload)
        if source == "bookmark":
            action = value.get("action", {})
            if action.get("kind") != "bookmark":
                return
            incident = db.execute("SELECT body FROM monitored_alerts WHERE id=?", (value["incidentId"],)).fetchone()
            incident = json.loads(incident[0]) if incident else {}
            value = {**incident, "incidentId": value["incidentId"], "observation": {
                **incident.get("observation", {}), "occurredAt": action["startAt"], "summary": action["label"],
            }, "endAt": action["endAt"]}
        observation = value["observation"]
        try:
            when = due_date(observation["occurredAt"]).isoformat(timespec="microseconds")
        except CameraError:
            return
        analytics = observation.get("analytics", {})
        label = " ".join([observation["summary"], value.get("sensorName") or "", value.get("zoneName") or "",
                          *(item["name"] for item in value.get("boundaries", ()))])
        db.execute("""INSERT OR IGNORE INTO evidence_index
                   (source,source_row,camera,floor,occurred,event_type,object_class,indexed,label,body) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                   (source, row, camera, value.get("floorId"), when, observation.get("eventType") or "bookmark",
                    analytics.get("object"), time.time(), label, json.dumps(value)))

    def index_batch(self, budget=200):
        more = False
        for source, (table, key, body) in SOURCE_TABLES.items():
            with self.activity.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                progress = db.execute("SELECT cursor FROM evidence_index_progress WHERE source=?", (source,)).fetchone()
                after = progress[0] if progress else 0
                rows = db.execute(f"SELECT {key},camera,{body} FROM {table} WHERE {key}>? ORDER BY {key} LIMIT ?", (after, budget)).fetchall()
                for sequence, camera, payload in rows:
                    self.write_index_row(db, source, sequence, camera, payload)
                if rows:
                    db.execute("INSERT OR REPLACE INTO evidence_index_progress VALUES(?,?)", (source, rows[-1][0]))
                db.execute(f"""DELETE FROM evidence_index WHERE row_id IN (
                    SELECT row_id FROM evidence_index e WHERE source=? AND NOT EXISTS
                    (SELECT 1 FROM {table} s WHERE s.{key}=e.source_row) LIMIT ?)""", (source, budget))
                more = more or len(rows) == budget
        return more

    def progress(self, db):
        done = dict(db.execute("SELECT source,cursor FROM evidence_index_progress"))
        return all(done.get(source, 0) >= db.execute(f"SELECT coalesce(max({key}),0) FROM {table}").fetchone()[0]
                   for source, (table, key, _) in SOURCE_TABLES.items())

    @staticmethod
    def summary(source, sequence, camera, occurred, body, label):
        value = json.loads(body)
        observation = value["observation"]
        return {"id": f"{source}:{sequence}", "source": source, "cameraId": camera, "floorId": value.get("floorId"),
                "boundaryId": value.get("zoneId") or next((boundary["id"] for boundary in value.get("boundaries", ())), None),
                "incidentId": value.get("incidentId") or (value.get("correlation", {}).get("incidentId") or value.get("id") if source == "incident" else None),
                "occurredAt": occurred, "label": observation["summary"], "device": value.get("sensorName"),
                "eventType": observation.get("eventType"), "configurationVersion": value.get("configurationVersion"),
                **({"vendor": observation["vendor"]} if "vendor" in observation else {}),
                **({"endAt": value["endAt"]} if "endAt" in value else {})}

    def search(self, actor, query):
        one = lambda key, default="": query.get(key, [default])[0]
        limit = int(one("limit", "30"))
        if not 1 <= limit <= 100:
            raise CameraError("Choose a result limit from 1 to 100.")
        where, params = [], []
        if actor["cameras"] != "*":
            where.append("e.camera IN (SELECT value FROM json_each(?))")
            params.append(json.dumps(actor["cameras"]))
        with self.activity.connect() as db:
            visibility, visible_args = " AND ".join(where) or "1", list(params)
            minimum = db.execute("SELECT min(e.occurred) FROM evidence_index e WHERE " + visibility, visible_args).fetchone()[0]
            text = one("q").strip()
            if len(text) > 160:
                raise CameraError("Use at most 160 characters in a search.")
            tokens = re.findall(r"[^\W_]+", text, re.UNICODE)
            if tokens and self.full_text:
                where.append("e.row_id IN (SELECT rowid FROM evidence_text WHERE evidence_text MATCH ?)")
                params.append(" AND ".join(f'"{word}"*' for word in tokens))
            elif tokens:
                where.extend("e.label LIKE ?" for _ in tokens)
                params.extend(f"%{word}%" for word in tokens)
            for field, column in (("cameraId", "camera"), ("floorId", "floor"), ("eventType", "event_type"), ("object", "object_class")):
                if value := one(field):
                    where.append(f"e.{column}=?")
                    params.append(value)
            if boundary := one("boundaryId"):
                where.append("(json_extract(e.body,'$.zoneId')=? OR EXISTS (SELECT 1 FROM json_each(e.body,'$.boundaries') b WHERE json_extract(b.value,'$.id')=?))")
                params.extend((boundary, boundary))
            for field, comparison in (("startAt", ">="), ("endAt", "<")):
                if value := one(field):
                    where.append(f"e.occurred {comparison} ?")
                    params.append(due_date(value).isoformat(timespec="microseconds"))
            cutoff = time.time()
            if cursor := one("cursor"):
                if len(cursor) > 240:
                    raise CameraError("Invalid evidence search cursor.")
                cutoff, last_at, last_id = json.loads(urlsafe_b64decode(cursor.encode()).decode())
                if type(cutoff) not in (float, int) or not 0 < cutoff <= time.time() or type(last_id) is not int:
                    raise CameraError("Invalid evidence search cursor.")
                where.append("(e.occurred,e.row_id)<(?,?)")
                params.extend((due_date(last_at).isoformat(timespec="microseconds"), last_id))
            where.append("e.indexed<=?")
            rows = db.execute("SELECT source,source_row,camera,occurred,body,label,row_id FROM evidence_index e WHERE "
                              + " AND ".join(where) + " ORDER BY occurred DESC,row_id DESC LIMIT ?", (*params, cutoff, limit + 1)).fetchall()
            next_cursor = None
            if len(rows) > limit:
                end = rows[limit - 1]
                next_cursor = urlsafe_b64encode(json.dumps([cutoff, end[3], end[6]]).encode()).decode()
            return {"results": [self.summary(*row[:6]) for row in rows[:limit]], "cursor": next_cursor,
                    "searchableFrom": minimum, "indexCurrent": self.progress(db)}

    @staticmethod
    def incident_camera(db, incident):
        row = db.execute("SELECT camera FROM monitored_alerts WHERE id=? AND member_of IS NULL", (incident,)).fetchone()
        if not row:
            raise CameraError("Choose a published root incident.")
        return row[0]

    def allowed(self, db, actor, row):
        incident = row[2]
        if incident is not None and not SiteCollaboration.can_view(actor, self.incident_camera(db, incident)):
            return False
        if actor["cameras"] == "*":
            return True
        cameras = [item[0] for item in db.execute("SELECT camera FROM case_attachments WHERE case_id=?", (row[0],))]
        return bool(incident or cameras or row[3] == actor["subject"]) and all(camera in actor["cameras"] for camera in cameras)

    def row(self, db, actor, identifier):
        row = db.execute("SELECT id,revision,incident,owner,title,finding,closed,updated FROM site_cases WHERE id=?", (identifier,)).fetchone()
        if not row or not self.allowed(db, actor, row):
            raise PermissionError("Choose a case available to your account.")
        return row

    @staticmethod
    def header(row):
        return dict(zip(("id", "revision", "incidentId", "ownerSubject", "title", "finding", "closed", "updatedAt"), row))

    def read(self, gateway, actor, identifier=None):
        with self.activity.connect() as db:
            db.execute("BEGIN")
            if identifier is None:
                rows = db.execute("SELECT id,revision,incident,owner,title,finding,closed,updated FROM site_cases ORDER BY updated DESC,id")
                available = []
                for row in rows:
                    if self.allowed(db, actor, row):
                        available.append(self.header(row))
                        if len(available) == 100:
                            break
                return {"cases": available}
            row = self.row(db, actor, identifier)
            entries = db.execute("SELECT id,camera,body,media FROM case_attachments WHERE case_id=? ORDER BY rowid", (identifier,)).fetchall()
            activity = db.execute("SELECT actor,at,operation,details FROM case_activity WHERE case_id=? ORDER BY sequence DESC LIMIT 100",
                                  (identifier,)).fetchall()
        attachments = []
        for key, camera, body, media in entries:
            evidence = json.loads(body)
            archive = json.loads(media) if media else {"state": "awaiting"}
            clip = None
            if archive["state"] == "available":
                with gateway.archive.lock:
                    clip = gateway.archive.entry(archive["cameraKey"], archive["clipId"])
                if not clip or gateway.device_for_key(archive["cameraKey"]) != camera:
                    archive = {"state": "expired"}
            recording = None
            if clip and archive["state"] == "available":
                recording = {"cameraId": camera, "path": f'streams/{archive["cameraKey"]}/clips/{archive["clipId"]}.mp4',
                             "durationSeconds": clip["durationSeconds"], "eventOffsetSeconds": clip["eventOffsetSeconds"]}
            attachments.append({"attachmentId": key, **evidence, "state": archive["state"], "recording": recording,
                                "heldUntil": clip.get("heldUntil") if clip and recording else None})
        return {"case": {**self.header(row), "attachments": attachments,
                         "activity": [{"actor": name, "at": at, "operation": operation, "detail": detail}
                                      for name, at, operation, detail in reversed(activity)]}}

    @staticmethod
    def source(db, actor, selector):
        match = re.fullmatch(r"(event|incident|bookmark):([0-9]{1,18})", str(selector))
        if not match:
            raise CameraError("Choose a recorded evidence result.")
        row = db.execute("SELECT source,source_row,camera,occurred,body,label FROM evidence_index WHERE source=? AND source_row=?",
                         (match[1], int(match[2]))).fetchone()
        if not row or not SiteCollaboration.can_view(actor, row[2]):
            raise PermissionError("The evidence result is unavailable to your account.")
        return row

    def change(self, gateway, actor, body):
        op = body.get("operation")
        if actor["role"] == "viewer":
            raise PermissionError("Your role cannot change cases.")
        if op in ("retrieve", "hold"):
            return self.prepare_media(gateway, actor, body)
        identifier = token_hex(16) if op == "create" else text_field(body, "caseId", 32)
        now = iso_now()
        with self.activity.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if op == "create":
                incident = body.get("incidentId")
                if incident is not None and (not isinstance(incident, str) or len(incident) > 450
                        or not SiteCollaboration.can_view(actor, self.incident_camera(db, incident))):
                    raise PermissionError("The incident is unavailable to your account.")
                title = text_field(body, "title", 120)
                db.execute("INSERT INTO site_cases VALUES(?,0,?,?,?,'',0,?)", (identifier, incident, actor["subject"], title, now))
                row = self.row(db, actor, identifier)
            else:
                row = self.row(db, actor, identifier)
                if type(body.get("revision")) is not int or body["revision"] != row[1]:
                    raise Conflict("The case changed. Refresh it before editing.")
                if row[6]:
                    raise Conflict("This case is closed.")
            detail = None
            if op in ("create", "attach") and (op == "attach" or body.get("sourceId")):
                source = self.source(db, actor, body.get("sourceId"))
                summary = self.summary(*source)
                start = due_date(summary["occurredAt"])
                end = due_date(summary["endAt"]) if summary.get("endAt") else start + timedelta(seconds=5)
                summary.update(startAt=(start if summary.get("endAt") else start - timedelta(seconds=5)).isoformat(), endAt=end.isoformat())
                count = db.execute("SELECT count(*) FROM case_attachments WHERE case_id=?", (identifier,)).fetchone()[0]
                if count >= 30:
                    raise CameraError("This case already has 30 evidence attachments.")
                db.execute("INSERT OR IGNORE INTO case_attachments VALUES(?,?,?,?,?,?,NULL)",
                           (identifier, token_hex(12), source[2], source[0], source[1], json.dumps(summary)))
                detail = summary["label"]
            elif op == "detach":
                removed = db.execute("DELETE FROM case_attachments WHERE case_id=? AND id=?", (identifier, body.get("attachmentId")))
                if not removed.rowcount:
                    raise CameraError("Choose an attachment from this case.")
            elif op == "update":
                title, finding = text_field(body, "title", 120), text_field(body, "finding", 1000, True)
                db.execute("UPDATE site_cases SET title=?,finding=? WHERE id=?", (title, finding, identifier))
                detail = finding or None
            elif op == "close":
                db.execute("UPDATE site_cases SET closed=1 WHERE id=?", (identifier,))
            elif op == "request-export":
                prior = db.execute("SELECT 1 FROM case_activity WHERE case_id=? AND operation=?", (identifier, op)).fetchone()
                if not prior:
                    payload = {"type": "case-export-requested", "siteId": gateway.monitor.site,
                               "organizationId": gateway.monitor.organization, "caseId": identifier,
                               "occurredAt": now, "deliveryId": token_hex(16)}
                    db.execute("INSERT INTO alert_deliveries(payload) VALUES(?)", (json.dumps(payload),))
            elif op != "create":
                raise CameraError("Choose a supported case operation.")
            self.log(db, identifier, row[1] + 1, actor, op, detail, now)
        return self.read(gateway, actor, identifier)

    def recording(self, gateway, actor, selector):
        with self.activity.connect() as db:
            row = self.source(db, actor, selector)
        with gateway.lock:
            stream = gateway.cameras.get(row[2])
        stamp = int(due_date(row[3]).timestamp() * 1000)
        result = gateway.archive.retrieve(stream, stamp)
        if result["state"] != "available":
            return {"state": result["state"]}
        return {"state": "available", "recording": {
            "cameraId": row[2], "path": f'streams/{result["cameraKey"]}/clips/{result["clipId"]}.mp4',
            "durationSeconds": result["durationSeconds"], "eventOffsetSeconds": result["eventOffsetSeconds"]}}

    @staticmethod
    def log(db, identifier, revision, actor, operation, detail, now):
        db.execute("UPDATE site_cases SET revision=?,updated=? WHERE id=?", (revision, now, identifier))
        db.execute("INSERT INTO case_activity(case_id,revision,subject,actor,at,operation,details) VALUES(?,?,?,?,?,?,?)",
                   (identifier, revision, actor["subject"], actor["name"], now, operation, detail))

    def prepare_media(self, gateway, actor, body):
        identifier, attachment = text_field(body, "caseId", 32), text_field(body, "attachmentId", 24)
        op = body["operation"]
        if op == "hold" and not manages_site(actor):
            raise PermissionError("A site supervisor is required to hold a case recording.")
        with self.activity.connect() as db:
            row = self.row(db, actor, identifier)
            entry = db.execute("SELECT camera,body,media FROM case_attachments WHERE case_id=? AND id=?", (identifier, attachment)).fetchone()
            if not entry or entry[0] is None:
                raise CameraError("Choose a camera evidence attachment.")
            if type(body.get("revision")) is not int or body["revision"] != row[1]:
                raise Conflict("The case changed. Refresh it before editing.")
        camera, evidence, media = entry
        state = json.loads(media) if media else {}
        if op == "retrieve":
            with gateway.lock:
                stream = gateway.cameras.get(camera)
            stamp = int(due_date(json.loads(evidence)["occurredAt"]).timestamp() * 1000)
            result = gateway.archive.retrieve(stream, stamp)
            state = {key: result[key] for key in ("state", "cameraKey", "clipId") if key in result}
        elif state.get("state") != "available" or gateway.device_for_key(state["cameraKey"]) != camera:
            raise CameraError("Retrieve the recording before adding a hold.")
        with self.activity.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current = self.row(db, actor, identifier)
            if current[1] != row[1] or not db.execute("SELECT 1 FROM case_attachments WHERE case_id=? AND id=?", (identifier, attachment)).fetchone():
                raise Conflict("The case changed during recording retrieval. Refresh it before editing.")
            if op == "hold":
                gateway.archive.hold(state["cameraKey"], state["clipId"], actor["name"])
            if op == "hold" or not media or json.loads(media) != state:
                db.execute("UPDATE case_attachments SET media=? WHERE case_id=? AND id=?", (json.dumps(state), identifier, attachment))
                self.log(db, identifier, row[1] + 1, actor, op, attachment, iso_now())
        return self.read(gateway, actor, identifier)

    def export(self, actor, gateway, request):
        if not manages_site(actor):
            raise PermissionError("A site supervisor is required to export a case.")
        identifier = text_field(request, "caseId", 32)
        selected = request.get("attachmentIds")
        if not isinstance(selected, list) or not 1 <= len(selected) <= 4 or not all(isinstance(item, str) for item in selected) or len(set(selected)) != len(selected):
            raise CameraError("Choose up to four distinct held recordings.")
        with self.activity.connect() as db:
            db.execute("BEGIN")
            row = self.row(db, actor, identifier)
            if type(request.get("revision")) is not int or request["revision"] != row[1]:
                raise Conflict("The case changed. Refresh it before exporting.")
            entries = db.execute("SELECT id,camera,body,media FROM case_attachments WHERE case_id=? ORDER BY rowid", (identifier,)).fetchall()
            events = []
            if row[2]:
                incident_rows = db.execute("SELECT body,camera FROM monitored_alerts WHERE id=? OR member_of=? ORDER BY event_at", (row[2], row[2]))
                for json_body, camera in incident_rows:
                    if SiteCollaboration.can_view(actor, camera):
                        item = json.loads(json_body)["observation"]
                        events.append({"at": item["occurredAt"], "device": item["sensorId"], "event": item["summary"],
                                       **({"vendor": item["vendor"]} if "vendor" in item else {})})
            audit = db.execute("SELECT revision,subject,actor,at,operation,details FROM case_activity WHERE case_id=? ORDER BY sequence DESC LIMIT 30",
                               (identifier,)).fetchall()
            incident_audit = db.execute("""SELECT payload FROM shared_activity WHERE resource='incident'
                AND json_extract(payload,'$.incidentId')=? ORDER BY sequence DESC LIMIT 20""", (row[2],)).fetchall() if row[2] else ()
        clips = []
        evidence = []
        for key, camera, body, media in entries:
            item = json.loads(body)
            evidence.append({"attachmentId": key, "included": key in selected, **item})
            events.append({"at": item["occurredAt"], "device": item["device"], "event": item["label"],
                           **({"vendor": item["vendor"]} if "vendor" in item else {})})
            if key in selected:
                value = json.loads(media) if media else {}
                if value.get("state") != "available" or gateway.device_for_key(value["cameraKey"]) != camera:
                    raise CameraError("Each selected recording must be available and held.")
                clips.append({"cameraKey": value["cameraKey"], "clipId": value["clipId"]})
        if len(clips) != len(selected):
            raise CameraError("Choose recordings attached to this case.")
        report = {**self.header(row), "attachments": evidence, "activity": [dict(zip(("revision", "subject", "actor", "at", "operation", "detail"), item))
                  for item in reversed(audit)], "incidentActivity": [json.loads(item[0]) for item in reversed(incident_audit)]}
        return {"incidentId": row[2] or identifier, "clips": clips, "timeline": sorted(events, key=lambda item: item["at"]),
                "caseReport": report}, identifier
