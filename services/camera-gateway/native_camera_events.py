"""Read-only ONVIF capability probing and PullPoint line-crossing ingest."""

from hashlib import sha256
import json
import re
import sqlite3
import time
from urllib.parse import urlsplit

from axis_archive import parse_utc
from onvif import CameraError, OnvifDevice, SCHEMA, add, tag
from site_activity import valid_event_device

EVENTS = "http://www.onvif.org/ver10/events/wsdl"
TOPICS = "http://www.onvif.org/ver10/topics"
WSN = "http://docs.oasis-open.org/wsn/b-2"
WSTOP = "http://docs.oasis-open.org/wsn/t-1"
WSA = "http://www.w3.org/2005/08/addressing"
LINE = "RuleEngine/LineDetector/Crossed"
NATIVE_SOURCE = "onvif-profile-m"


def native_evidence(value):
    if (not isinstance(value, dict) or set(value) != {"source", "ruleId", "objectId"}
            or value["source"] != NATIVE_SOURCE or not valid_event_device(value["ruleId"])
            or not isinstance(value["objectId"], str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,96}", value["objectId"])):
        raise CameraError("Supply a valid camera-native event and object reference.")
    return value


class NativeEvents:
    def __init__(self, activity):
        self.activity = activity
        with activity.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS native_camera_status (
                    camera TEXT PRIMARY KEY, state TEXT NOT NULL, checked REAL NOT NULL, last_event REAL, issue TEXT
                );
                CREATE INDEX IF NOT EXISTS native_event_camera ON monitored_events(camera,seq)
                    WHERE json_extract(body,'$.observation.nativeAnalytics.source')='onvif-profile-m';
            """)

    def status(self, camera, state, issue="", occurred=None):
        with self.activity.connect() as db:
            db.execute("""
                INSERT INTO native_camera_status VALUES(?,?,?,?,?) ON CONFLICT(camera) DO UPDATE SET
                state=excluded.state,checked=excluded.checked,issue=excluded.issue,
                last_event=CASE WHEN excluded.last_event IS NULL THEN last_event
                  ELSE max(excluded.last_event,coalesce(last_event,0)) END
            """, (camera, state, time.time(), occurred, issue))

    def read(self, actor):
        allowed = actor["cameras"]
        args = [] if allowed == "*" else list(allowed)
        where = "" if allowed == "*" else " AND camera IN (" + ",".join("?" for _ in args) + ")" if args else " AND 0"
        with self.activity.connect() as db:
            rows = db.execute("SELECT camera,state,checked,last_event,issue FROM native_camera_status WHERE 1=1" + where, args).fetchall()
            events = db.execute("SELECT body FROM monitored_events WHERE json_extract(body,'$.observation.nativeAnalytics.source')='onvif-profile-m'"
                                + where + " ORDER BY seq DESC LIMIT 40", args).fetchall()
        return {"cameras": [{"cameraId": camera, "state": state if time.time() - checked <= 20 else "disconnected",
                            "checkedAt": checked, "lastEventAt": last, "issue": issue}
                            for camera, state, checked, last, issue in rows],
                "events": [json.loads(row[0]) for row in events]}


class PullPointSource:
    def local_endpoint(self, raw):
        checked = self.gateway.validate_url(raw, ("http", "https"))
        left, right = urlsplit(checked), urlsplit(self.address)
        port = lambda value: value.port or (443 if value.scheme == "https" else 80)
        if (left.scheme, left.hostname, port(left)) != (right.scheme, right.hostname, port(right)):
            raise CameraError("The advertised ONVIF subscription is outside the enrolled camera origin.")
        return checked

    def request(self, url, operation, fill, manager=False, references=()):
        action = EVENTS + ("/PullPointSubscription/" if manager else "/EventPortType/") + operation + "Request"
        return self.device.request(url, EVENTS, operation, fill, action=action, references=references)

    def subscription(self, events_url):
        def fill(operation):
            filter_node = add(operation, EVENTS, "Filter")
            add(filter_node, WSN, "TopicExpression", "topic:" + self.topic,
                Dialect="http://www.onvif.org/ver10/tev/topicExpression/ConcreteSet", **{"xmlns:topic": self.topic_namespace})
            add(operation, EVENTS, "InitialTerminationTime", "PT60S")

        response = self.request(events_url, "CreatePullPointSubscription", fill)
        reference = response.find(tag(EVENTS, "SubscriptionReference"))
        if reference is None:
            raise CameraError("The camera did not return an ONVIF PullPoint subscription.")
        endpoint = self.local_endpoint(reference.findtext(tag(WSA, "Address")))
        values = reference.find(tag(WSA, "ReferenceParameters"))
        references = list(values) if values is not None else []
        start = parse_utc(response.findtext(tag(EVENTS, "CurrentTime")))
        end = parse_utc(response.findtext(tag(EVENTS, "TerminationTime")))
        ttl = min(60, end - start) if start is not None and end is not None else 0
        if ttl <= 0:
            raise CameraError("The ONVIF subscription has expired.")
        return endpoint, references, ttl


class NativeCamera(PullPointSource):
    topic = LINE
    topic_namespace = TOPICS

    def __init__(self, gateway, record):
        camera, address, rule, device_rule, video_source = (record.get(key) for key in
            ("cameraId", "address", "ruleId", "deviceRule", "videoSourceToken"))
        username, password = record.get("username", ""), record.get("password", "")
        if (not valid_event_device(camera) or not valid_event_device(rule)
                or any(not isinstance(value, str) or not value or len(value) > 200 for value in (device_rule, video_source))
                or any(not isinstance(value, str) or len(value) > 256 or any(ord(char) < 32 for char in value)
                       for value in (username, password)) or password and not username):
            raise CameraError("Supply camera IDs, mapped rule, source token and valid ONVIF credentials.")
        self.address = gateway.validate_url(address, ("http", "https"))
        self.gateway = gateway
        self.device = OnvifDevice(self.address, username, password, gateway.validate_url)
        self.camera, self.rule, self.device_rule, self.video_source = camera, rule, device_rule, video_source

    @property
    def registry(self):
        return self.gateway.monitor.native_events

    def enabled(self):
        with self.gateway.activity.connect() as db:
            version = self.gateway.monitor.configuration.published(db)
        if not version:
            return False
        policy = json.loads(version[2])
        camera = next((entry for entry in policy["sensors"] if entry["id"] == self.camera and entry["modality"] == "vision"), {})
        return any(rule["id"] == self.rule and rule.get("cameraNative") and
                   any(zone["id"] == rule["boundaryId"] and zone["enabled"] for zone in policy["boundaries"])
                   for rule in camera.get("analyticsRules", ()))

    def notifications(self, response):
        latest = None
        for note in response.findall(tag(WSN, "NotificationMessage")):
            topic = note.findtext(tag(WSN, "Topic"), "").strip().split(":")[-1]
            item = note.find(f"./{tag(WSN, 'Message')}/{tag(SCHEMA, 'Message')}")
            if topic != LINE or item is None or item.get("PropertyOperation") in ("Initialized", "Deleted"):
                continue
            source = {node.get("Name"): node.get("Value") for node in item.findall(f"./{tag(SCHEMA, 'Source')}/{tag(SCHEMA, 'SimpleItem')}")}
            data = {node.get("Name"): node.get("Value") for node in item.findall(f"./{tag(SCHEMA, 'Data')}/{tag(SCHEMA, 'SimpleItem')}")}
            if source.get("Rule") != self.device_rule or source.get("VideoSourceConfigurationToken") != self.video_source:
                continue
            time_text = item.get("UtcTime")
            occurred = parse_utc(time_text)
            if occurred is None or not -2 <= time.time() - occurred <= 60:
                continue
            try:
                evidence = native_evidence({"source": NATIVE_SOURCE, "ruleId": self.rule, "objectId": data.get("ObjectId")})
            except CameraError:
                continue
            identity = json.dumps([self.camera, self.rule, self.video_source, evidence["objectId"], occurred])
            event = {"type": "observation", "sensorId": self.camera, "id": "onvif-" + sha256(identity.encode()).hexdigest(),
                     "occurredAt": time_text, "eventType": "line-crossing", "summary": "Camera-native line crossing", "nativeAnalytics": evidence}
            try:
                self.gateway.telemetry.push(event, native_camera=self.camera)
            except CameraError:
                continue
            latest = max(latest or occurred, occurred)
        return latest

    def poll(self, stopping):
        while not stopping.is_set():
            try:
                if not self.enabled():
                    stopping.wait(1)
                    continue
                advertised = self.device.services().get(EVENTS)
                if not advertised:
                    self.registry.status(self.camera, "unsupported", "The camera did not advertise an ONVIF event service.")
                    stopping.wait(5)
                    continue
                endpoint = self.local_endpoint(advertised)
                properties = self.request(endpoint, "GetEventProperties", lambda operation: None)
                topic = properties.find("./" + tag(WSTOP, "TopicSet") + "/" + "/".join(tag(TOPICS, name) for name in LINE.split("/")))
                if topic is None:
                    self.registry.status(self.camera, "unsupported", "The configured line-crossing topic was not advertised.")
                    stopping.wait(5)
                    continue
                clock = self.device.clock()
                if clock is None or abs(clock["clockOffsetSeconds"]) > 2:
                    self.registry.status(self.camera, "disconnected", "Check the camera clock before collecting events.")
                    stopping.wait(3)
                    continue
                pull, references, ttl = self.subscription(endpoint)
                deadline = time.monotonic() + ttl
                self.registry.status(self.camera, "supported")
                while not stopping.is_set() and time.monotonic() < deadline and self.enabled():
                    def limits(operation):
                        add(operation, EVENTS, "Timeout", "PT2S")
                        add(operation, EVENTS, "MessageLimit", "32")

                    response = self.request(pull, "PullMessages", limits, manager=True, references=references)
                    occurred = self.notifications(response)
                    self.registry.status(self.camera, "supported", occurred=occurred)
                    stopping.wait(.2)
            except (CameraError, OSError, ValueError, sqlite3.Error):
                self.registry.status(self.camera, "disconnected", "The camera event subscription is unavailable.")
                stopping.wait(1)


def enroll_native(gateway, path):
    if not path:
        return []
    records = json.loads(path.read_text())
    if not isinstance(records, list) or len(records) > 32 or any(not isinstance(row, dict) for row in records):
        raise CameraError("Configure up to 32 private ONVIF analytics camera entries.")
    cameras = [NativeCamera(gateway, row) for row in records]
    if len({camera.camera for camera in cameras}) != len(cameras):
        raise CameraError("Enroll each camera once for native analytics.")
    return cameras
