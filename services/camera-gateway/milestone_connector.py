"""Read XProtect channel inventory and ingest explicitly mapped REST events."""

from datetime import datetime, timezone
import json
import re
from secrets import token_hex
import sqlite3
import threading
import time
import ssl
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPSHandler, ProxyHandler, Request, build_opener

from onvif import CameraError, NoRedirect
from site_activity import valid_event_device
from site_telemetry import TYPES
from vms_status import ConnectorState

GUID = re.compile(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}")


class MilestoneConnector:
    def __init__(self, origin, token_path, mapping_path, telemetry, ca_file=None):
        target = urlsplit(origin)
        if target.scheme != "https" or not target.hostname or target.path not in ("", "/") or target.username or target.password or target.query or target.fragment:
            raise CameraError("Set a trusted XProtect HTTPS origin.")
        self.origin, self.token_path, self.telemetry = origin.rstrip("/"), token_path, telemetry
        self.connect = build_opener(ProxyHandler({}), HTTPSHandler(context=ssl.create_default_context(cafile=ca_file)), NoRedirect())
        self.sessions = {}
        self.session_lock = threading.Lock()
        self.mapping = json.loads(mapping_path.read_text()) if mapping_path else {}
        if not isinstance(self.mapping, dict):
            raise CameraError("Provide an XProtect connector mapping object.")
        self.event_types = self.mapping.get("eventTypes", {})
        self.sources = self.mapping.get("sources", {})
        if not isinstance(self.event_types, dict) or not all(GUID.fullmatch(key) and isinstance(value, str) and value in TYPES
            for key, value in self.event_types.items()) or not isinstance(self.sources, dict) or not all(
                len(key) <= 100 and valid_event_device(value) for key, value in self.sources.items()):
            raise CameraError("Map XProtect event GUIDs and sources to supported Proxigo events and device IDs.")
        if not self.token_path.is_file():
            raise CameraError("Provide a private XProtect user-token file.")
        self.state = ConnectorState(telemetry, "xprotect", self.origin, playback=True)

    def request(self, resource, query=None, body=None, method="GET"):
        token = self.token_path.read_text().strip()
        if not token or len(token) > 16000 or any(char.isspace() for char in token):
            raise CameraError("The XProtect access token is unavailable.")
        query = "?" + urlencode(query) if query else ""
        request = Request(self.origin + "/api/rest/v1/" + resource + query, data=json.dumps(body).encode() if body is not None else None,
                          headers={"Authorization": "Bearer " + token, "Accept": "application/json", "Content-Type": "application/json"}, method=method)
        with self.connect.open(request, timeout=6) as response:
            body = response.read(2_097_153)
        if len(body) > 2_097_152:
            raise CameraError("The XProtect response exceeded the connector size limit.")
        if not body:
            return {}
        return json.loads(body)

    def read(self, resource, query=None):
        value = self.request(resource, query)
        rows = value.get("array") if isinstance(value, dict) else value
        if not isinstance(rows, list):
            raise CameraError("XProtect returned an unexpected response.")
        return [row for row in rows if isinstance(row, dict)]

    def channels(self):
        return {"channels": [{"id": row["id"], "name": row["name"].strip()[:120]} for row in self.read("cameras")
                             if isinstance(row.get("id"), str) and GUID.fullmatch(row["id"]) and isinstance(row.get("name"), str) and row["name"].strip()][:512]}

    def start_playback(self, actor, incident):
        if not isinstance(incident, str) or not 1 <= len(incident) <= 450:
            raise CameraError("Choose a published site incident.")
        with self.telemetry.activity.connect() as db:
            row = db.execute("SELECT alert.camera,alert.body,versions.body FROM monitored_alerts alert JOIN configuration_versions versions"
                             " ON alert.version=versions.version WHERE alert.id=?", (incident,)).fetchone()
        if not row or not row[0] or actor["cameras"] != "*" and row[0] not in actor["cameras"]:
            raise PermissionError("This recording is outside your site camera access.")
        camera, alert, config = row[0], json.loads(row[1]), json.loads(row[2])
        device = next((item for item in config["sensors"] if item["id"] == camera), None)
        channel = device.get("cameraChannel", "") if device and device.get("cameraProvider") == "Milestone XProtect" else ""
        if not GUID.fullmatch(channel):
            raise CameraError("The incident camera has no published XProtect channel.")
        identifier = token_hex(24)
        with self.session_lock:
            self.sessions = {key: value for key, value in self.sessions.items() if value["expires"] > time.monotonic()}
            if len(self.sessions) >= 24 or sum(value["subject"] == actor["subject"] for value in self.sessions.values()) >= 3:
                raise CameraError("The gateway is at its recording session limit.")
            self.sessions[identifier] = {"subject": actor["subject"], "camera": camera, "expires": time.monotonic() + 90, "remote": None}
        try:
            result = self.request("webRTC/session", body={"cameraId": channel, "playbackTimeNode": {
                "playbackTime": alert["observation"]["occurredAt"], "skipGaps": False, "speed": 1}}, method="POST")
            remote, sdp = result.get("sessionId"), result.get("offerSDP")
            if not isinstance(remote, str) or not GUID.fullmatch(remote) or not isinstance(sdp, str) or len(sdp) > 65_536 or not sdp.startswith("v=0"):
                raise CameraError("XProtect did not return a valid recorded-video session.")
            with self.session_lock:
                self.sessions[identifier]["remote"] = remote
            self.telemetry.activity.audit(actor["subject"], "xprotect-recording-view", camera, incident)
            return {"session": identifier, "offer": sdp, "iceServers": result.get("iceServers", [])}
        except Exception:
            with self.session_lock:
                self.sessions.pop(identifier, None)
            raise

    def playback(self, actor, payload):
        operation = payload.get("operation")
        if operation == "start":
            return self.start_playback(actor, payload.get("incidentId"))
        identifier = payload.get("session")
        with self.session_lock:
            session = self.sessions.get(identifier) if isinstance(identifier, str) else None
            if not session or session["subject"] != actor["subject"] or session["expires"] <= time.monotonic() or (
                actor["cameras"] != "*" and session["camera"] not in actor["cameras"]):
                raise PermissionError("This recording session has expired or access has changed.")
            if operation == "close":
                self.sessions.pop(identifier)
                return {"closed": True}
            remote = session["remote"]
        if not remote:
            raise CameraError("Wait for the recorder playback session.")
        if operation == "answer":
            answer, candidates = payload.get("answer"), payload.get("candidates", [])
            if not isinstance(answer, str) or len(answer) > 65_536 or not answer.startswith("v=0") or not isinstance(candidates, list) or len(candidates) > 40 or any(
                    not isinstance(candidate, str) or not candidate.startswith("candidate:") or len(candidate) > 1000 for candidate in candidates):
                raise CameraError("Invalid WebRTC answer or ICE candidates.")
            self.request("webRTC/session/" + remote, body={"answerSDP": answer}, method="PATCH")
            if candidates:
                self.request("webRTC/iceCandidates/" + remote, body={"sessionId": remote, "candidates": candidates}, method="POST")
            return {"accepted": True}
        if operation == "candidates":
            candidates = self.request("webRTC/iceCandidates/" + remote).get("candidates")
            return {"candidates": (candidates or [])[:40] if isinstance(candidates, list) else []}
        raise CameraError("Choose a supported recorder playback operation.")

    def poll(self, stopping):
        with self.telemetry.activity.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS milestone_cursor(origin TEXT PRIMARY KEY, local_id INTEGER NOT NULL)")
            stored = db.execute("SELECT local_id FROM milestone_cursor WHERE origin=?", (self.origin,)).fetchone()
        start = datetime.fromtimestamp(datetime.now(timezone.utc).timestamp() - 300, timezone.utc).isoformat().replace("+00:00", "Z")
        delay = 1
        while not stopping.wait(delay):
            try:
                backlog = self.poll_once(start, stored[0] if stored else 0)
                delay = 1 if backlog else 6
            except (OSError, ValueError, OverflowError, CameraError, sqlite3.Error):
                self.state.check("Could not ingest XProtect events. The last accepted position is retained.")
                delay = 15

    def poll_once(self, start, legacy_cursor=0):
        if not self.sources or not self.event_types:
            self.channels()
            self.state.check()
            return False
        after = self.state.cursor("events", legacy_cursor)
        query = {"localId": f"gt:{after}", "orderBy": "asc:'localId'", "size": "200"}
        if not after:
            query["time"] = f"gt:'{start}'"
        rows = self.read("events", query)
        if any(type(row.get("localId")) is not int or not 0 < row["localId"] < 2**63 for row in rows):
            raise CameraError("XProtect returned an invalid event sequence.")
        for row in sorted((item for item in rows if item["localId"] > after), key=lambda item: item["localId"]):
            source, raw_type, identifier = (row.get(key) for key in ("source", "type", "id"))
            device = self.sources.get(source) if isinstance(source, str) else None
            event_type = self.event_types.get(raw_type) if isinstance(raw_type, str) else None
            if device and event_type and isinstance(identifier, str) and GUID.fullmatch(identifier):
                self.state.ingest("events", row["localId"], {"type": "observation", "sensorId": device, "id": "xprotect-" + identifier.lower(),
                    "occurredAt": row.get("time"), "eventType": event_type, "summary": "XProtect " + event_type.replace("-", " ")},
                    {"eventId": identifier.lower(), "entityId": source, "eventType": raw_type, "position": row["localId"]})
            else:
                self.state.ingest("events", row["localId"], rejected=bool(event_type))
        self.state.check(backlog=len(rows) >= 200)
        return len(rows) >= 200
