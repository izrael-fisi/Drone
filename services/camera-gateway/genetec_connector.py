"""Read-only Security Center Web SDK inventory and durable access-event ingestion."""

from base64 import b64encode
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from http.client import HTTPException
import json
import re
import sqlite3
import ssl
import threading
import time
from urllib.error import HTTPError
from urllib.parse import urlencode, urljoin, urlsplit
from urllib.request import HTTPSHandler, ProxyHandler, Request, build_opener

from milestone_connector import GUID
from onvif import CameraError, NoRedirect
from site_activity import valid_event_device
from vms_status import ConnectorState

EVENTS = {24: ("DoorOpenedForTooLong", "door-held-open"), 27: ("DoorOpen", "door-opened"),
          28: ("DoorClose", "door-closed"), 29: ("DoorForcedOpen", "door-forced-open"),
          32: ("DoorOpenWhileLockSecure", "door-forced-open"), 35: ("AccessGranted", "access-granted")}


class GenetecConnector:
    def __init__(self, origin, credentials, mapping, telemetry, ca_file=None, stream_port=4591):
        target = urlsplit(origin)
        if (target.scheme != "https" or not target.hostname or target.path not in ("", "/", "/WebSdk", "/WebSdk/")
                or target.username or target.password or target.query or target.fragment):
            raise CameraError("Set a trusted Security Center Web SDK HTTPS origin.")
        self.origin = f"https://{target.netloc}/WebSdk/"
        self.hostname, self.stream_port = target.hostname, stream_port
        self.credentials = credentials
        self.sources = json.loads(mapping.read_text()) if mapping else {}
        if not credentials.is_file() or not isinstance(self.sources, dict) or len(self.sources) > 512 or not all(
                GUID.fullmatch(key) and valid_event_device(device) for key, device in self.sources.items()):
            raise CameraError("Provide a private Web SDK credential file and a map of entity GUIDs to site device IDs.")
        self.sources = {key.lower(): value for key, value in self.sources.items()}
        self.connect = build_opener(ProxyHandler({}), HTTPSHandler(context=ssl.create_default_context(cafile=ca_file)), NoRedirect())
        self.state = ConnectorState(telemetry, "genetec", self.origin)
        self.wake = threading.Event()

    def open(self, path, query=None, timeout=8, base=None):
        config = json.loads(self.credentials.read_text())
        username, application, password = (config.get(key) for key in ("username", "applicationId", "password"))
        if not all(isinstance(value, str) and 0 < len(value) <= 512 for value in (username, application, password)) or any(
                char in username + application for char in ";:\r\n"):
            raise CameraError("Use valid private Web SDK credentials.")
        authorization = "Basic " + b64encode(f"{username};{application}:{password}".encode()).decode()
        url = (base or self.origin + path) + ("?" + urlencode({"q": query}) if query else "")
        return self.connect.open(Request(url, headers={"Authorization": authorization, "Accept": "text/json"}), timeout=timeout)

    def request(self, path, query=None, allow_paging=False):
        with self.open(path, query) as response:
            payload = response.read(2_097_153)
        if len(payload) > 2_097_152:
            raise CameraError("The Web SDK response exceeded the connector limit.")
        envelope = json.loads(payload)["Rsp"]
        state = envelope.get("Status")
        if state != "Ok" and not (allow_paging and state == "TooManyResults"):
            raise CameraError("The configured Security Center account could not read this resource.")
        return envelope.get("Result"), state

    def entities(self, kind):
        collected = {}
        for page in range(1, 7):
            rows, state = self.request("report/EntityConfiguration",
                f'EntityTypes@{kind},ReturnFields@Name{"@Type" if kind == "Role" else ""},PageSize=100,Page={page}', allow_paging=True)
            if not isinstance(rows, list):
                raise CameraError("The Web SDK returned an invalid inventory.")
            for row in rows:
                if isinstance(row, dict) and isinstance(row.get("Guid"), str) and GUID.fullmatch(row["Guid"]):
                    collected[row["Guid"].lower()] = row
            if state == "Ok" and len(rows) < 100:
                break
        return collected

    def channels(self):
        channels = []
        for kind, modality in (("Camera", "vision"), ("Door", "access")):
            for identifier, row in self.entities(kind).items():
                if isinstance(row.get("Name"), str) and row["Name"].strip():
                    channels.append({"id": identifier, "name": row["Name"].strip()[:80], "modality": modality,
                                     "deviceId": self.sources.get(identifier) or "genetec-" + identifier})
        return {"channels": channels[:512]}

    def poll_once(self, initial_time):
        if not self.sources:
            self.channels()
            self.state.check()
            return False
        managers = [key for key, row in self.entities("Role").items() if row.get("Type") == "AccessManager"]
        if not managers:
            raise CameraError("Security Center returned no readable Access Manager roles.")
        backlog = False
        for manager in managers:
            after = self.state.cursor(manager)
            query = f"StartingAfterIndexes@RawEventIndex({manager},{after}),MaximumResultCount=200,QueryTimeout=5000"
            if not after:
                query += f",InsertionStartTimeUtc={initial_time}"
            rows, _ = self.request("report/AccessControlRawEvent", query)
            if not isinstance(rows, list) or any(not isinstance(row, dict) or type(row.get("Position")) is not int
                    or not 0 < row["Position"] < 2**63 or str(row.get("AccessManagerGuid")).lower() != manager for row in rows):
                raise CameraError("Security Center returned invalid access stream positions.")
            for row in sorted((item for item in rows if item["Position"] > after), key=lambda item: item["Position"]):
                code = row.get("EventType")
                source = next((str(row.get(key)).lower() for key in ("SourceGuid", "AccessPointGroupGuid", "AccessPointGuid")
                               if str(row.get(key)).lower() in self.sources), None)
                selected = EVENTS.get(code) if type(code) is int else None
                position = row["Position"]
                if not selected or not source:
                    self.state.ingest(manager, position, rejected=selected is not None)
                    continue
                name, event_type = selected
                vendor_id = f"{manager}:{position}"
                stamp = str(row.get("EventTimestamp"))
                if re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?", stamp):
                    stamp += "Z"
                event_id = "genetec-" + sha256(f"{self.state.id}:{vendor_id}".encode()).hexdigest()
                self.state.ingest(manager, position, {"type": "observation", "sensorId": self.sources[source], "id": event_id,
                    "eventType": event_type, "occurredAt": stamp, "summary": "Genetec " + event_type.replace("-", " ")},
                    {"eventId": vendor_id, "entityId": source, "eventType": name, "position": position})
            backlog = backlog or len(rows) >= 200
        self.state.check(backlog=backlog)
        return backlog

    def subscribe(self, stopping):
        """Use the live stream to wake the position-based report reader."""
        names = [name for name, _ in EVENTS.values()]
        subscribed = []
        while not stopping.is_set():
            try:
                for source in self.sources:
                    self.request("events/subscribe", ",".join(f"event({source},{name})" for name in names))
                    subscribed.append(source)
                try:
                    response = self.open("events", timeout=5)
                except HTTPError as error:
                    with error:
                        if error.code not in (301, 302, 307, 308) or not error.headers.get("Location"):
                            raise
                        url = urljoin(self.origin + "events", error.headers["Location"])
                    target = urlsplit(url)
                    if (target.scheme != "https" or target.hostname != self.hostname or target.port != self.stream_port
                            or target.username or target.password or not target.path.startswith("/WebSdk/streaming/events/")):
                        raise CameraError("The Web SDK streaming redirect is outside the configured HTTPS endpoint.")
                    response = self.open("", timeout=5, base=url)
                with response:
                    self.wake.set()
                    while not stopping.is_set():
                        line = response.readline(65_537)
                        if not line or len(line) > 65_536:
                            break
                        if line.lstrip().startswith(b"{"):
                            self.wake.set()
            except (OSError, ValueError, TypeError, KeyError, HTTPException, CameraError):
                pass
            finally:
                for source in subscribed:
                    if stopping.is_set():
                        break
                    try:
                        self.request("events/unsubscribe", ",".join(f"event({source},{name})" for name in names))
                    except (OSError, ValueError, TypeError, KeyError, HTTPException, CameraError):
                        break
                subscribed.clear()
            stopping.wait(10)

    def poll(self, stopping):
        subscription = None
        if self.sources:
            subscription = threading.Thread(target=self.subscribe, args=(stopping,), daemon=True)
            subscription.start()
        start = (datetime.now(timezone.utc) - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%S")
        while not stopping.is_set():
            try:
                delay = 1 if self.poll_once(start) else 6
            except (OSError, ValueError, TypeError, KeyError, OverflowError, HTTPException, CameraError, sqlite3.Error):
                self.state.check("Could not read the Security Center access event stream. Check the role and event capture settings.")
                delay = 12
            if stopping.wait(0.5):
                break
            self.wake.wait(delay - 0.5)
            self.wake.clear()
        if subscription:
            subscription.join(timeout=5)
