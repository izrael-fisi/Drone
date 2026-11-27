"""Durable, camera-scoped comparisons of two tripwire policies on one clip."""

from contextlib import suppress
from datetime import datetime, timezone
from functools import lru_cache
from hashlib import sha256
import json
import os
from pathlib import Path
import re
from secrets import token_hex
import signal
import sqlite3
import subprocess
import tempfile
import time

from axis_archive import parse_utc
from onvif import CameraError
from site_accounts import manages_site
from site_collaboration import Conflict
from site_activity import valid_event_device


class ReplayInterrupted(Exception):
    pass


def compare_labels(labels, detections):
    @lru_cache(None)
    def best(i, j):
        if i == len(labels) or j == len(detections):
            return 0, 0, ()
        choices = [best(i + 1, j), best(i, j + 1)]
        distance = abs(labels[i] - detections[j])
        if distance <= 1000:
            count, error, matches = best(i + 1, j + 1)
            choices.append((count + 1, error - distance, ((i, j),) + matches))
        return max(choices)

    pairs = best(0, 0)[2]
    assigned = dict(pairs)
    used = set(assigned.values())
    return {"matches": [{"expectedMs": labels[i], "detectedMs": detections[j]} for i, j in pairs],
            "misses": [stamp for i, stamp in enumerate(labels) if i not in assigned],
            "extras": [stamp for j, stamp in enumerate(detections) if j not in used]}


class CommissioningRuns:
    def __init__(self, gateway, config=None):
        self.gateway, self.activity = gateway, gateway.activity
        self.worker = None
        if config:
            source = json.loads(config.read_text())
            model = source["model"]
            python = Path(source["python"]).expanduser().absolute()
            path = Path(model["path"]).resolve(strict=True)
            digest = model["sha256"]
            if not python.is_file() or not re.fullmatch(r"[a-f0-9]{64}", digest) or model.get("provider", "CPUExecutionProvider") not in (
                    "CPUExecutionProvider", "CoreMLExecutionProvider", "CUDAExecutionProvider"):
                raise CameraError("Configure an approved YOLO11 commissioning model and its SHA-256.")
            with path.open("rb") as handle:
                if sha256_stream(handle) != digest:
                    raise CameraError("The commissioning model does not match the configured SHA-256.")
            self.worker = (str(python), {"kind": "yolo11", "path": str(path), "sha256": digest,
                                        "provider": model.get("provider", "CPUExecutionProvider")})
        with self.activity.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS commissioning_runs (
                    id TEXT PRIMARY KEY, camera TEXT NOT NULL, created REAL NOT NULL, revision INTEGER NOT NULL,
                    state TEXT NOT NULL, body TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS commissioning_queue ON commissioning_runs(state,created);
                UPDATE commissioning_runs SET state='queued' WHERE state='running';
            """)

    def check_scope(self, actor, camera):
        if not valid_event_device(camera) or not self.gateway.collaboration.can_view(actor, camera):
            raise PermissionError("This camera is outside your site permissions.")

    def read(self, actor, query):
        identifier = query.get("id", [""])[0]
        camera = query.get("camera", [""])[0]
        if identifier and not re.fullmatch(r"[a-f0-9]{32}", identifier):
            raise ValueError("Choose a valid commissioning run.")
        if camera:
            self.check_scope(actor, camera)
        conditions, args = [], []
        for column, value in (("id", identifier), ("camera", camera)):
            if value:
                conditions.append(column + "=?")
                args.append(value)
        if actor["cameras"] != "*":
            allowed = actor["cameras"]
            conditions.append("camera IN (" + ",".join("?" for _ in allowed) + ")" if allowed else "0")
            args.extend(allowed)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        with self.activity.connect() as db:
            rows = db.execute("SELECT id,camera,created,revision,state,body FROM commissioning_runs" + where
                              + " ORDER BY created DESC LIMIT 50", args).fetchall()
        return {"enabled": bool(self.worker), "runs": [self.view(row) for row in rows]}

    @staticmethod
    def view(row):
        identifier, camera, created, revision, state, body = row
        return {**json.loads(body), "id": identifier, "cameraId": camera, "createdAt": created, "revision": revision, "state": state}

    def camera_stream(self, camera):
        with self.gateway.lock:
            record = self.gateway.cameras.get(camera)
            if record:
                return {**record, "deviceId": camera}
        raise CameraError("Register the camera recorder before commissioning.")

    def clip(self, stream, clip_id):
        if not isinstance(clip_id, str) or not re.fullmatch(r"[a-f0-9]{32}", clip_id):
            raise CameraError("Select an authorized recorder clip.")
        archive = self.gateway.archive
        with archive.lock:
            entry = archive.entry(stream["key"], clip_id)
            if not entry:
                raise CameraError("This recording has expired or is unavailable.")
            clip = (archive.directory / (clip_id + ".mp4")).read_bytes()
        return {key: entry[key] for key in ("clipId", "occurredAt", "durationSeconds", "eventOffsetSeconds")}, clip

    def change(self, actor, payload):
        if not manages_site(actor):
            raise PermissionError("Commissioning changes require a site supervisor.")
        operation = payload.get("operation")
        if operation in ("clip", "create"):
            camera = payload.get("cameraId")
            self.check_scope(actor, camera)
            stream = self.camera_stream(camera)
        if operation == "clip":
            timestamp = parse_utc(payload.get("occurredAt"))
            if timestamp is None or not 0 < timestamp <= time.time():
                raise CameraError("Choose a recording time in UTC.")
            result = self.gateway.archive.retrieve(stream, round(timestamp * 1000))
            return {key: value for key, value in result.items() if key != "cameraKey"}
        if operation not in ("create", "annotate", "queue"):
            raise CameraError("Choose a commissioning operation.")
        with self.activity.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if operation == "create":
                if not self.worker:
                    raise CameraError("Configure the site commissioning worker first.")
                clip, media = self.clip(stream, payload.get("clipId"))
                active = self.gateway.monitor.configuration.published(db)
                draft_revision, base, draft = db.execute("SELECT draft,base,body FROM configuration_draft").fetchone()
                if not active or not draft or base != active[0] or payload.get("draftRevision") != draft_revision:
                    raise Conflict("Save the current candidate draft and refresh before opening a comparison.")
                rule_id = payload.get("ruleId")
                policies = []
                for version, body in ((active[0], active[2]), (active[0] + 1, draft)):
                    config = json.loads(body)
                    sensor = next((row for row in config["sensors"] if row["id"] == camera), {})
                    enabled = {row["id"] for row in config["boundaries"] if row["enabled"]}
                    rule = next((row for row in sensor.get("analyticsRules", []) if row["id"] == rule_id
                                 and row["kind"] == "line-crossing" and row["direction"] != "either" and not row.get("cameraNative")
                                 and row["boundaryId"] in enabled), None)
                    if not rule:
                        raise CameraError("Choose a directional tripwire present in both enabled configurations.")
                    policies.append({"version": version, "rules": [rule],
                                     "timeZone": config.get("analyticsTimeZone", self.gateway.collaboration.time_zone)})
                left, right = (policy["rules"][0] for policy in policies)
                if (left["direction"], left["object"], left["boundaryId"]) != (right["direction"], right["object"], right["boundaryId"]):
                    raise CameraError("Compare policies for the same direction, object, and boundary.")
                identifier, created = token_hex(16), time.time()
                body = {"clip": clip, "clipSha256": sha256(media).hexdigest(), "draftRevision": draft_revision,
                        "publishedVersion": active[0], "policies": policies, "modelSha256": self.worker[1]["sha256"],
                        "provider": self.worker[1]["provider"], "replayProfile": {"fps": 4, "width": 640, "height": 360},
                        "annotations": None}
                db.execute("INSERT INTO commissioning_runs VALUES(?,?,?,0,'draft',?)",
                           (identifier, camera, created, json.dumps(body)))
                revision, state = 0, "draft"
            else:
                identifier = payload.get("id")
                row = db.execute("SELECT id,camera,created,revision,state,body FROM commissioning_runs WHERE id=?",
                                 (identifier if isinstance(identifier, str) else "",)).fetchone()
                if not row:
                    raise CameraError("This commissioning run is unavailable.")
                _, camera, created, revision, state, encoded = row
                self.check_scope(actor, camera)
                body = json.loads(encoded)
                if payload.get("revision") != revision or state in ("queued", "running"):
                    raise Conflict("The commissioning run changed. Refresh it before editing.")
                if operation == "annotate":
                    start, end, labels = (payload.get(key) for key in ("startMs", "endMs", "expectedMs"))
                    if (type(start) is not int or type(end) is not int
                            or not 0 <= start < end <= int(body["clip"]["durationSeconds"] * 1000)
                            or not isinstance(labels, list) or len(labels) > 100
                            or any(type(value) is not int or not start <= value < end for value in labels)
                            or len(set(labels)) != len(labels)):
                        raise CameraError("Mark a valid reviewed interval and distinct expected crossing times.")
                    body["annotations"] = {"startMs": start, "endMs": end, "expectedMs": sorted(labels)}
                    state = "draft"
                else:
                    if not self.worker or not body["annotations"]:
                        raise CameraError("Save the reviewed interval before queuing replay.")
                    if body["modelSha256"] != self.worker[1]["sha256"] or body["provider"] != self.worker[1]["provider"]:
                        raise CameraError("Re-enroll the run's approved model and provider before replay.")
                    state = "queued"
                body.pop("result", None)
                body.pop("error", None)
                revision += 1
                db.execute("UPDATE commissioning_runs SET revision=?,state=?,body=? WHERE id=?",
                           (revision, state, json.dumps(body), identifier))
            db.execute("INSERT INTO activity_audit(occurred_at,actor,action,resource,detail) VALUES(?,?,?,?,?)",
                       (datetime.now(timezone.utc).isoformat(), actor["subject"], "commissioning-" + operation, camera, identifier))
        return {**body, "id": identifier, "cameraId": camera, "createdAt": created, "revision": revision, "state": state}

    def replay(self, run):
        clip, data = self.clip(self.camera_stream(run["cameraId"]), run["clip"]["clipId"])
        python, model = self.worker
        if sha256(data).hexdigest() != run["clipSha256"] or model["sha256"] != run["modelSha256"] or model["provider"] != run["provider"]:
            raise CameraError("The saved clip or model fingerprint changed.")
        start = clip["occurredAt"] / 1000 - clip["eventOffsetSeconds"]
        stamp = datetime.fromtimestamp(start, timezone.utc).isoformat()
        annotation = run["annotations"]
        results = []
        with tempfile.TemporaryDirectory(prefix="proxigo-commissioning-") as directory:
            source, config, policy = (Path(directory) / name for name in ("clip.mp4", "worker.json", "policy.json"))
            source.write_bytes(data)
            profile = run.get("replayProfile", {"fps": 4, "width": 640, "height": 360})
            config.write_text(json.dumps({"sensorId": run["cameraId"], **profile,
                                          "models": {"ground": model}}))
            for snapshot in run["policies"]:
                policy.write_text(json.dumps(snapshot))
                args = [python, str(Path(__file__).with_name("analytics_worker.py")), "--config", str(config), "--policy",
                        str(policy), "--replay", str(source), "--start-at", stamp]
                output = self.run_process(args)
                detections = []
                for line in output.splitlines():
                    event = json.loads(line)
                    if event["eventType"] != "line-crossing":
                        continue
                    offset = round((parse_utc(event["occurredAt"]) - start) * 1000)
                    if annotation["startMs"] <= offset < annotation["endMs"]:
                        detections.append(offset)
                results.append(compare_labels(annotation["expectedMs"], sorted(detections)))
        return dict(zip(("published", "candidate"), results))

    def run_process(self, args):
        grouped = os.name == "posix"
        with subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=grouped) as child:
            deadline = time.monotonic() + 90
            try:
                while not self.gateway.stopping.is_set():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise subprocess.TimeoutExpired(args, 90)
                    try:
                        output, _ = child.communicate(timeout=min(.5, remaining))
                    except subprocess.TimeoutExpired:
                        continue
                    if child.returncode:
                        raise subprocess.CalledProcessError(child.returncode, args)
                    return output
                raise ReplayInterrupted()
            finally:
                if child.poll() is None:
                    with suppress(ProcessLookupError):
                        if grouped:
                            os.killpg(child.pid, signal.SIGKILL)
                        else:
                            child.kill()
                    child.wait()

    def work_once(self):
        with self.activity.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT id,camera,created,revision,state,body FROM commissioning_runs"
                             " WHERE state='queued' AND NOT EXISTS (SELECT 1 FROM commissioning_runs WHERE state='running')"
                             " ORDER BY created LIMIT 1").fetchone()
            if not row:
                return False
            db.execute("UPDATE commissioning_runs SET state='running' WHERE id=?", (row[0],))
        run, body = self.view(row), json.loads(row[5])
        try:
            body["result"] = self.replay(run)
            state = "completed"
        except ReplayInterrupted:
            with self.activity.connect() as db:
                db.execute("UPDATE commissioning_runs SET state='queued' WHERE id=?", (row[0],))
            return False
        except (CameraError, OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError) as error:
            body["error"] = str(error) if isinstance(error, CameraError) else "Replay failed. Check the enrolled model and local recorder."
            state = "failed"
        with self.activity.connect() as db:
            db.execute("UPDATE commissioning_runs SET state=?,body=?,revision=revision+1 WHERE id=?",
                       (state, json.dumps(body), row[0]))
        return True

    def poll(self):
        while not self.gateway.stopping.wait(.5):
            with suppress(OSError, sqlite3.OperationalError):
                self.work_once()


def sha256_stream(handle):
    digest = sha256()
    for chunk in iter(lambda: handle.read(1 << 20), b""):
        digest.update(chunk)
    return digest.hexdigest()
