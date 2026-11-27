"""Site-scoped metadata from enrolled inspection stations."""

import csv
from io import StringIO
import json
import re

from onvif import CameraError
from site_activity import valid_event_device

EVENT_TYPE = "inspection-result"
OUTCOMES = ("pass", "fail", "incomplete")
PAGE_SIZE = 25


def normalize_inspection(raw):
    required = ("stationId", "recipeId", "componentId")
    identifiers = (*required, "partId", "lotId")
    if (not isinstance(raw, dict) or raw.keys() - {*identifiers, "source", "outcome", "resultId", "simulated"}
            or raw.get("source") not in ("simulated", "opc-ua") or raw.get("outcome") not in OUTCOMES
            or any(not valid_event_device(raw.get(field)) for field in required)
            or any(field in raw and not valid_event_device(raw[field]) for field in ("partId", "lotId"))
            or not ("partId" in raw or "lotId" in raw)):
        raise CameraError("Supply an inspection outcome, station, recipe, component and part or lot ID.")
    if raw["source"] == "simulated":
        if raw["outcome"] == "incomplete" or "resultId" in raw or "simulated" in raw:
            raise CameraError("The original station simulator submits completed cycles only.")
    elif (not isinstance(raw.get("resultId"), str) or not re.fullmatch(r"[A-Za-z0-9_.:/-]{1,120}", raw["resultId"])
          or type(raw.get("simulated")) is not bool):
        raise CameraError("OPC UA results require a stable source result ID and simulation indicator.")
    return {field: raw[field] for field in (*identifiers, "source", "outcome", "resultId", "simulated") if field in raw}


class InspectionResults:
    def __init__(self, activity):
        self.activity = activity
        with activity.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS inspection_results (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    camera TEXT NOT NULL, source_id TEXT NOT NULL, outcome TEXT NOT NULL, body TEXT NOT NULL,
                    UNIQUE(camera,source_id)
                );
                CREATE INDEX IF NOT EXISTS inspection_by_camera ON inspection_results(camera,seq);
                CREATE INDEX IF NOT EXISTS inspection_by_outcome ON inspection_results(outcome,camera,seq);
                CREATE TABLE IF NOT EXISTS inspection_opc_results (
                    station TEXT NOT NULL, result TEXT NOT NULL, sequence INTEGER NOT NULL, PRIMARY KEY(station,result)
                );
            """)

    def record(self, db, detail):
        observation = detail["observation"]
        inspection = observation["inspection"]
        if inspection["source"] == "opc-ua":
            external = (inspection["stationId"], inspection["resultId"])
            found = db.execute("SELECT sequence FROM inspection_opc_results WHERE station=? AND result=?", external).fetchone()
            if found:
                return {"accepted": False, "sequence": found[0]}
        before = db.execute("SELECT seq FROM inspection_results WHERE camera=? AND source_id=?",
                            (detail["cameraId"], observation["id"])).fetchone()
        if before:
            return {"accepted": False, "sequence": before[0]}
        sequence = db.execute("INSERT INTO inspection_results(camera,source_id,outcome,body) VALUES(?,?,?,?)",
                              (detail["cameraId"], observation["id"], inspection["outcome"],
                               json.dumps(detail, separators=(",", ":"), ensure_ascii=False))).lastrowid
        if inspection["source"] == "opc-ua":
            db.execute("INSERT INTO inspection_opc_results VALUES(?,?,?)", (*external, sequence))
        return {"accepted": True, "sequence": sequence}

    def read(self, actor, query):
        camera = query.get("camera", [""])[0]
        outcome = query.get("outcome", ["all"])[0]
        recipe, lot = (query.get(key, [""])[0] for key in ("recipe", "lot"))
        export = query.get("export", [""])[0]
        before = int(query.get("before", [str(2**63 - 1)])[0])
        if (any(value and not valid_event_device(value) for value in (camera, recipe, lot))
                or outcome not in ("all", *OUTCOMES) or export not in ("", "csv") or not 0 < before < 2**63):
            raise ValueError("Choose valid inspection result filters.")
        conditions, args = ["seq<?"], [before]
        if actor["cameras"] != "*":
            allowed = actor["cameras"]
            conditions.append("camera IN (" + ",".join("?" for _ in allowed) + ")" if allowed else "0")
            args.extend(allowed)
        if camera:
            conditions.append("camera=?")
            args.append(camera)
        if outcome != "all":
            conditions.append("outcome=?")
            args.append(outcome)
        for key, value in (("recipeId", recipe), ("lotId", lot)):
            if value:
                conditions.append(f"json_extract(body,'$.observation.inspection.{key}')=?")
                args.append(value)
        limit = 1000 if export == "csv" else PAGE_SIZE
        with self.activity.connect() as db:
            items = db.execute("SELECT seq,body FROM inspection_results WHERE " + " AND ".join(conditions)
                               + " ORDER BY seq DESC LIMIT ?", [*args, limit + 1]).fetchall()
        if export == "csv":
            output = StringIO()
            writer = csv.writer(output)
            writer.writerow(("occurredAt", "cameraId", "stationId", "resultId", "recipeId", "componentId", "partId",
                             "lotId", "outcome", "source", "simulated", "configurationVersion"))
            for _, raw in items[:limit]:
                detail = json.loads(raw)
                observation = detail["observation"]
                inspection = observation["inspection"]
                fields = [inspection.get(key, "") for key in ("stationId", "resultId", "recipeId", "componentId", "partId",
                                                               "lotId", "outcome", "source")]
                fields = [f"'{value}" if value.startswith(("=", "-", "+", "@")) else value for value in [detail["cameraId"], *fields]]
                writer.writerow([observation["occurredAt"], *fields, inspection.get("simulated", inspection["source"] == "simulated"),
                                 detail["configurationVersion"]])
            return {"csv": output.getvalue(), "count": min(len(items), limit), "truncated": len(items) > limit}
        return {"entries": [{"sequence": sequence, "event": json.loads(body)} for sequence, body in items[:PAGE_SIZE]],
                "before": items[PAGE_SIZE - 1][0] if len(items) > PAGE_SIZE else None}
