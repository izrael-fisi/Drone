"""Read an installed CCTV stream, evaluate camera rules, and deliver site observations."""

import argparse
from contextlib import closing, suppress
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import re
import shutil
import signal
import sqlite3
import sys
from threading import Event, Thread
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener
from uuid import uuid4

from onvif import NoRedirect
from site_activity import valid_event_device
from analytics_contract import LINK_CHECK, PLACEMENTS, detection_summary, effective_rule, policy_zone, unit_point, rules
from site_configuration import validate_ring
from video_analytics.detectors import load_detector, person_color_signature, yolo11_detail_region
from video_analytics.tracking import Analytics
from video_analytics.video import LiveVideo, check_video, replay, utc

MAX_PENDING = 10_000
POLICY_LEASE_SECONDS = 15
HTTP = build_opener(ProxyHandler({}), NoRedirect())
LANES = {"ground": {"person", "vehicle"}, "drone": {"drone"}}


def report(sensor, rule, item, occurred_at, source):
    return {"type": "observation", "id": "cctv-" + uuid4().hex, "sensorId": sensor, "occurredAt": utc(occurred_at),
            "eventType": rule["kind"], "summary": detection_summary(rule, item),
            "analytics": {**item, "source": source}}


def json_request(url, token, payload=None):
    headers = {"Authorization": "Bearer " + token}
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload, allow_nan=False).encode()
    with HTTP.open(Request(url, data=data, headers=headers), timeout=3) as response:
        return json.loads(response.read(65_536))


class Outbox:
    def __init__(self, path):
        self.path = path
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with closing(self.connect()) as db, db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS outbox (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, payload TEXT NOT NULL
                );
            """)
        path.chmod(0o600)

    def connect(self):
        return sqlite3.connect(self.path, timeout=5)

    def put(self, payload):
        with closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT count(*) FROM outbox").fetchone()[0] >= MAX_PENDING:
                return False
            db.execute("INSERT INTO outbox(payload) VALUES(?)", (json.dumps(payload, allow_nan=False),))
        return True

    def batch(self):
        with closing(self.connect()) as db:
            return db.execute("SELECT id,payload FROM outbox ORDER BY id LIMIT 12").fetchall()

    def count(self):
        with closing(self.connect()) as db:
            return db.execute("SELECT count(*) FROM outbox").fetchone()[0]

    def complete(self, identifier):
        with closing(self.connect()) as db, db:
            db.execute("DELETE FROM outbox WHERE id=?", (identifier,))


def gateway_address(raw, placement=None):
    parsed = urlsplit(raw)
    if placement == "cloud" and parsed.scheme != "https":
        raise ValueError("Cloud analytics requires an HTTPS site gateway.")
    if (parsed.scheme not in ("https", "http") or not parsed.hostname or parsed.username is not None
            or parsed.scheme == "http" and parsed.hostname not in ("127.0.0.1", "::1", "localhost")
            or parsed.path not in ("", "/") or parsed.fragment or parsed.query):
        raise ValueError("Use an HTTPS site gateway or a loopback HTTP site gateway.")
    return raw.rstrip("/")


def policy_for_lane(policy, lane):
    return {**policy, "rules": [rule for rule in policy["rules"] if rule["object"] in LANES[lane]]}


def policy_at(policy, occurred_at):
    return {**policy, "rules": [effective_rule(rule, occurred_at, policy["zone"]) for rule in policy["rules"]]}


def approved_model(model, error):
    if (not isinstance(model, dict) or model.keys() != {"path", "sha256"}
            or not isinstance(model["path"], str) or not isinstance(model["sha256"], str)
            or not re.fullmatch(r"[a-f0-9]{64}", model["sha256"])):
        raise ValueError(error)
    path = Path(model["path"]).expanduser()
    if not path.is_absolute():
        raise ValueError(error)
    return {**model, "path": str(path)}


def model_specs(config, decode_fps):
    models = config["models"]
    if not isinstance(models, dict) or not models or models.keys() - LANES.keys():
        raise ValueError("Configure a ground model, a drone model, or both.")
    result = {}
    for lane, spec in models.items():
        if not isinstance(spec, dict) or spec.get("kind") not in ("yolo11", "yolox", "owlv2"):
            raise ValueError("Choose a supported detector for each model lane.")
        path = Path(spec["path"]).expanduser()
        if spec["kind"] == "yolo11" and (lane != "ground" or not isinstance(spec.get("sha256"), str)
                                            or not re.fullmatch(r"[a-f0-9]{64}", spec["sha256"])):
            raise ValueError("YOLO11 requires a ground model and its approved SHA-256.")
        if "coremlCacheDirectory" in spec:
            cache = spec["coremlCacheDirectory"]
            if (spec["kind"] != "yolo11" or spec.get("provider") != "CoreMLExecutionProvider"
                    or not isinstance(cache, str) or not Path(cache).expanduser().is_absolute()):
                raise ValueError("Set an absolute coremlCacheDirectory for a YOLO11 CoreML worker.")
            spec = {**spec, "coremlCacheDirectory": str(Path(cache).expanduser())}
        if "rotationDegrees" in spec:
            rotation = spec["rotationDegrees"]
            if spec["kind"] != "yolo11" or type(rotation) is not int or rotation not in (0, 90, 180, 270):
                raise ValueError("YOLO11 rotation must be 0, 90, 180, or 270 degrees clockwise.")
        if "personTrackSupport" in spec and (spec["kind"] != "yolo11" or type(spec["personTrackSupport"]) is not bool):
            raise ValueError("Set personTrackSupport to true or false for a YOLO11 ground model.")
        if "personMotionAssociation" in spec and (spec["kind"] != "yolo11" or type(spec["personMotionAssociation"]) is not bool):
            raise ValueError("Set personMotionAssociation to true or false for a YOLO11 ground model.")
        if "personTripwireRecovery" in spec and (spec["kind"] != "yolo11" or type(spec["personTripwireRecovery"]) is not bool):
            raise ValueError("Set personTripwireRecovery to true or false for a YOLO11 ground model.")
        if "personColorAssociation" in spec and (spec["kind"] != "yolo11" or type(spec["personColorAssociation"]) is not bool):
            raise ValueError("Set personColorAssociation to true or false for a YOLO11 ground model.")
        rule_detail = spec.get("detailFromRules", False)
        if "detailFromRules" in spec and (spec["kind"] != "yolo11" or type(rule_detail) is not bool):
            raise ValueError("Set detailFromRules to true or false for a YOLO11 ground model.")
        if "detailRegionLimit" in spec:
            limit = spec["detailRegionLimit"]
            if not rule_detail or type(limit) is not int or limit not in (1, 2):
                raise ValueError("Set detailRegionLimit to 1 or 2 with detailFromRules enabled.")
        if rule_detail and "detailRegion" in spec:
            raise ValueError("Choose a rule-derived or a manual YOLO11 detail region.")
        detail = {}
        if "personModel" in spec:
            invalid_person_model = "Set personModel to an absolute path and approved SHA-256 for a single-class YOLO11 person export."
            if spec["kind"] != "yolo11":
                raise ValueError(invalid_person_model)
            detail["personModel"] = approved_model(spec["personModel"], invalid_person_model)
        if "personModelMode" in spec and ("personModel" not in detail or spec["personModelMode"] not in ("always", "monochrome")):
            raise ValueError("Set personModelMode to always or monochrome with an approved YOLO11 person model.")
        if "detailRegion" in spec:
            if spec["kind"] != "yolo11":
                raise ValueError("A detail region requires a YOLO11 ground model.")
            detail["detailRegion"] = yolo11_detail_region(spec["detailRegion"], config.get("width", 640), config.get("height", 360))
        if "detailObjects" in spec:
            objects = spec["detailObjects"]
            if ((not rule_detail and "detailRegion" not in detail) or not isinstance(objects, list) or not 1 <= len(objects) <= 2
                    or any(item not in ("person", "vehicle") for item in objects) or len(set(objects)) != len(objects)
                    or rule_detail and objects != ["person"]):
                raise ValueError("Choose person, vehicle, or both for manual detail objects, or person for rule-derived detail.")
            detail["detailObjects"] = tuple(sorted(objects))
        if "detailPose" in spec:
            invalid_pose = "Choose an absolute path and approved SHA-256 for the YOLO11 pose model in a person detail region."
            if (not rule_detail and "detailRegion" not in detail) or detail.get("detailObjects", ("person",)) != ("person",):
                raise ValueError(invalid_pose)
            detail["detailPose"] = approved_model(spec["detailPose"], invalid_pose)
        fallback = spec.get("detailPoseFallback", False)
        if "detailPoseFallback" in spec and (spec["kind"] != "yolo11" or type(fallback) is not bool or fallback and "detailPose" not in detail):
            raise ValueError("Use a boolean detailPoseFallback with an approved YOLO11 person pose crop.")
        if lane == "drone" and spec["kind"] == "yolox":
            classes = spec.get("classes")
            if not isinstance(classes, dict) or "drone" not in classes.values():
                raise ValueError("A YOLOX drone model needs an explicit drone class index.")
        default_rate = 4 if lane == "ground" and spec["kind"] in ("yolo11", "yolox") else .5
        rate = spec.get("fps", min(decode_fps, default_rate))
        threads = spec.get("threads", 2)
        if type(rate) not in (float, int) or not .1 <= rate <= decode_fps or type(threads) is not int or not 1 <= threads <= 8 or not path.is_absolute():
            raise ValueError("Set an absolute model path, an inference rate up to the stream rate, and up to 8 inference threads.")
        boost = spec.get("vehicleStopConfirmationBoost", False)
        if "vehicleStopConfirmationBoost" in spec and (type(boost) is not bool or spec["kind"] != "yolo11"):
            raise ValueError("Set vehicleStopConfirmationBoost to true or false for a YOLO11 ground model.")
        if boost and decode_fps < 2 * rate:
            raise ValueError("Vehicle stop confirmation boost requires a decode rate at least twice the inference rate.")
        result[lane] = {**spec, **detail, "path": str(path), "fps": rate, "lane": lane, "threads": threads}
    return result


def next_sample_after(sequence, due, interval):
    # Keep the sampling phase when the latest-frame buffer skips old frames.
    return due + interval * (math.floor((sequence - due + 1e-8) / interval) + 1)


class SampleSchedule:
    def __init__(self, first, interval):
        self.due, self.interval = first, interval
        self.last = first - 1

    def take(self, sequence, confirming_stop):
        """Return None for a skipped frame, otherwise whether a baseline sample was missed."""
        position = sequence + 1e-8
        if position >= self.due:
            missed = position >= self.due + self.interval
            self.due = next_sample_after(sequence, self.due, self.interval)
        elif confirming_stop and self.last + 1e-8 < self.due - self.interval / 2 <= position:
            # Add one midpoint sample without moving the regular sampling grid.
            missed = False
        else:
            return None
        self.last = sequence
        return missed


def inspect_frame(detector, frame, policy, engine, stamp, width, height, missed_sample=False, *, source_sequence=None):
    if not policy["rules"]:
        return [], [], None
    if getattr(detector, "detail_from_rules", False):
        detector.apply_rule_detail(policy, width, height)
    person_model_changed = getattr(detector, "automatic_person_model", False) and detector.select_person_model(frame, missed_sample)
    requested = {rule["object"] for rule in policy["rules"]}
    minimum = min(rule["minimumScore"] for rule in policy["rules"])
    support = engine.person_track_support and minimum > .10 and any(
        rule["object"] == "person" and rule["kind"] == "zone-entry" for rule in policy["rules"])
    floor = .10 if support else minimum
    inference = {"capture_floor": floor} if support else {}
    if getattr(detector, "person_detector", None) is not None:
        inference["objects"] = requested
    if getattr(detector, "detail_pose_fallback", False):
        inference["fallback_rules"] = [rule for rule in policy["rules"] if rule["object"] == "person"
                                       and rule["kind"] in ("zone-entry", "zone-dwell")]
    found = detector.infer(frame, minimum, **inference)
    detections = [item for item in found if item.label in requested]
    strong = [item for item in detections if item.score >= minimum] if support else detections
    weak = [item for item in detections if item.label == "person" and item.score < minimum] if support else []
    colors = {index: person_color_signature(frame, item.box) for index, item in enumerate(strong)
              if item.label == "person"} if engine.person_color_association else None
    return detections, engine.sample(strong, policy, stamp, (width, height), missed_sample,
                                     weak=weak, source_sequence=source_sequence, person_model_changed=person_model_changed,
                                     colors=colors), floor


def run_replay(args, config, dimensions, models):
    if not args.policy:
        raise ValueError("Supply an offline policy JSON with --policy for replay.")
    width, height, fps = dimensions
    policy = json.loads(args.policy.read_text())
    version = policy.get("version")
    if type(version) is not int or version < 1:
        raise ValueError("A replay policy needs a positive version number.")
    boundaries = {rule.get("boundaryId") for rule in policy["rules"] if isinstance(rule, dict)}
    policy["rules"] = rules(policy["rules"], boundaries, lambda points: validate_ring(points, 3, 12, unit_point))
    policy["zone"] = policy_zone(policy.get("timeZone", "UTC"))
    missing = [lane for lane in LANES if lane not in models and policy_for_lane(policy, lane)["rules"]]
    if missing:
        raise ValueError("The replay policy enables an unconfigured model lane.")
    lanes = {lane: (load_detector(spec), Analytics(max(1.2, 2.8 / spec["fps"]),
                                                 person_track_support=spec.get("personTrackSupport", False),
                                                 person_motion_association=spec.get("personMotionAssociation", False),
                                                 person_tripwire_recovery=spec.get("personTripwireRecovery", False),
                                                 person_color_association=spec.get("personColorAssociation", False)),
                     policy_for_lane(policy, lane))
             for lane, spec in models.items() if policy_for_lane(policy, lane)["rules"]}
    counters = {lane: [0, 0., 0, 0] for lane in lanes}
    schedules = {lane: SampleSchedule(0., fps / models[lane]["fps"]) for lane in lanes}
    base = time.time()
    if args.start_at:
        start = datetime.fromisoformat(args.start_at)
        if start.utcoffset() is None:
            raise ValueError("Set --start-at to a timestamp with a time zone offset.")
        base = start.timestamp()
    stream = args.detections.open("w") if args.detections else None
    try:
        for sequence, (timestamp, frame) in enumerate(replay(str(args.replay.expanduser().resolve(strict=True)), *dimensions)):
            for lane, (detector, engine, local_policy) in lanes.items():
                boost = models[lane].get("vehicleStopConfirmationBoost") and engine.confirming_vehicle_stop
                missed = schedules[lane].take(sequence, boost)
                if missed is None:
                    continue
                started = time.perf_counter()
                active_policy = policy_at(local_policy, base + timestamp)
                detections, matched, floor = inspect_frame(detector, frame, active_policy, engine, timestamp, width, height, missed)
                elapsed = time.perf_counter() - started
                counters[lane][0] += 1
                counters[lane][1] += elapsed
                counters[lane][2] += len(matched)
                counters[lane][3] += getattr(detector, "last_pose_fallback", False)
                for rule, item in matched:
                    event = report(config["sensorId"], rule, item, base + timestamp, "replay")
                    if rule.get("trackOccupancy"):
                        engine.dock_occupancy[rule["id"]].arrival_event_id = event["id"]
                    print(json.dumps(event), flush=True)
                if stream:
                    stream.write(json.dumps({"seconds": timestamp, "lane": lane, "model": detector.id, "frame": [width, height],
                                            **({"configuredModel": detector.specialist_frame_id + ":monochrome"}
                                               if getattr(detector, "automatic_person_model", False) else {}),
                                            "decodeFps": fps, "captureFloor": floor,
                                            "processingMs": round(1000 * elapsed, 3),
                                            **({"dockOccupancy": [dock.snapshot(identifier, timestamp, base + timestamp)
                                                for identifier, dock in engine.dock_occupancy.items()]} if engine.dock_occupancy else {}),
                                            "detections": [vars(item) for item in detections]}) + "\n")
    finally:
        if stream:
            stream.close()
    print(json.dumps({lane: {"frames": count, "meanProcessingMs": round(1000 * elapsed / max(count, 1), 1), "matches": matches,
                            **({"poseFallbackFrames": fallbacks} if models[lane].get("detailPoseFallback") else {})}
                      for lane, (count, elapsed, matches, fallbacks) in counters.items()}), file=sys.stderr)


def gateway_credentials(config):
    placement = config.get("placement")
    if placement is not None and placement not in PLACEMENTS:
        raise ValueError("Choose cloud or on-premises analytics placement.")
    gateway = gateway_address(config["gateway"], placement)
    token = Path(config["tokenFile"]).expanduser().read_text().strip()
    if not re.fullmatch(r"[a-f0-9]{64}", token):
        raise ValueError("The device token file must contain a single 64-character hexadecimal token.")
    return gateway, token


def gateway_policy(updated, config):
    if (not isinstance(updated, dict) or updated.get("placement") != config.get("placement")
            or not isinstance(updated.get("rules"), list) or type(updated.get("version")) is not int or updated["version"] < 0):
        raise ValueError("Gateway policy differs from the worker compute placement or is unavailable.")
    expected = {"cameraId": config["sensorId"], "siteId": config.get("siteId"), "organizationId": config.get("organizationId")}
    if any(expected[key] is not None and updated.get(key) != expected[key]
           for key in ("siteId", "organizationId")) or ("cameraId" in updated and updated["cameraId"] != expected["cameraId"]):
        raise ValueError("Gateway policy belongs to a different organization, site, or camera.")
    return {**updated, "zone": policy_zone(updated.get("timeZone", "UTC"))}


def check_connection(config, dimensions, models):
    if not all(config.get(field) for field in ("placement", "siteId", "organizationId")):
        raise ValueError("Set placement, siteId and organizationId for the connection check.")
    gateway, token = gateway_credentials(config)
    camera = config["sensorId"]
    try:
        policy = gateway_policy(json_request(f"{gateway}/api/cameras/{camera}/analytics", token), config)
        if not policy["version"] or not policy["rules"]:
            raise ValueError("Publish this camera with an active CCTV rule before checking the connection.")
        if any(lane not in models and policy_for_lane(policy, lane)["rules"] for lane in LANES):
            raise ValueError("Configure model lanes for all active camera rules.")
        check_video(config["source"], dimensions)
        result = json_request(f"{gateway}/api/site/telemetry", token,
                              {"type": LINK_CHECK, "sensorId": camera, "policyVersion": policy["version"]})
    except HTTPError as issue:
        raise ValueError(f"The site gateway rejected the link check (HTTP {issue.code}).") from None
    if not isinstance(result, dict) or result.get("accepted") is not True or result.get("policyVersion") != policy["version"]:
        raise ValueError("The site gateway did not confirm the telemetry link.")
    print(json.dumps({"connected": True, "organizationId": config["organizationId"], "siteId": config["siteId"],
                      "cameraId": camera, "placement": config["placement"], "policyVersion": policy["version"],
                      "activeRules": len(policy["rules"]), "videoFrameDecoded": True, "telemetryVerified": True}))


def run_live(config, dimensions, model_configs, buffer_ms=0):
    gateway, token = gateway_credentials(config)
    placement = config.get("placement")
    outbox = Outbox(Path(config["state"]).expanduser())
    if "outboxQueueUrl" in config:
        if placement != "cloud":
            raise ValueError("Use the local outbox for on-premises analytics.")
        from sqs_outbox import SqsOutbox

        outbox = SqsOutbox(config["outboxQueueUrl"], outbox, MAX_PENDING)
    camera = config["sensorId"]
    worker_endpoint = f"{gateway}/api/cameras/{camera}/analytics"
    event_endpoint = f"{gateway}/api/site/telemetry"
    width, height, fps = dimensions
    source = urlsplit(config["source"])
    profile = json.dumps([[source.scheme, source.hostname, source.port, source.path, source.query],
                          dimensions, config.get("inferenceFps", fps), buffer_ms, config["models"]],
                         sort_keys=True, separators=(",", ":"))
    stop = Event()
    old_handlers = {signum: signal.signal(signum, lambda *_: stop.set()) for signum in (signal.SIGINT, signal.SIGTERM)}
    unavailable = {"version": 0, "rules": [], "zone": timezone.utc, "validUntil": 0}
    policy = unavailable
    statuses = {lane: {"model": (None, None), "failed": False, "samples": 0} for lane in model_configs}
    delivery_issue = False
    saturated = False

    def active():
        current = policy
        return bool(current["rules"]) and time.monotonic() < current["validUntil"]

    video = LiveVideo(config["source"], *dimensions, stop, active, buffer_ms=buffer_ms)

    def detector_worker(lane, spec):
        nonlocal saturated
        status = statuses[lane]
        try:
            detector = load_detector(spec)
            status["artifact"] = detector.id
            variable_profile = spec.get("detailFromRules") or getattr(detector, "automatic_person_model", False)
            if not variable_profile:
                status["model"] = (None, detector.id)
            engine = Analytics(max(1.2, 2.8 / spec["fps"]), 1.75 / spec["fps"],
                               person_track_support=spec.get("personTrackSupport", False),
                               person_motion_association=spec.get("personMotionAssociation", False), frame_period=1 / fps,
                               person_tripwire_recovery=spec.get("personTripwireRecovery", False),
                               person_color_association=spec.get("personColorAssociation", False))
            sequence = 0
            schedule = SampleSchedule(1., fps / spec["fps"])
            while not stop.is_set():
                item = video.get(sequence, ordered=lane == "ground")
                if item is None:
                    continue
                sequence, stamp, occurred_at, frame = item
                local = policy_for_lane(policy, lane)
                if (not local["rules"] or time.monotonic() - stamp > 2
                        or time.monotonic() >= local["validUntil"]
                        or occurred_at < local.get("publishedAt", 0)):
                    continue
                boost = (spec.get("vehicleStopConfirmationBoost") and engine.version == local["version"]
                         and engine.confirming_vehicle_stop)
                missed_sample = schedule.take(sequence, boost)
                if missed_sample is None:
                    continue
                _, matches, _ = inspect_frame(detector, frame, policy_at(local, occurred_at), engine, stamp, width, height,
                                              missed_sample, source_sequence=sequence)
                if variable_profile:
                    status["model"] = (local["version"], detector.id)
                status["samples"] += 1
                for rule, item in matches:
                    event = report(camera, rule, item, occurred_at, "live")
                    if not outbox.put(event):
                        saturated = True
                    elif rule.get("trackOccupancy"):
                        engine.dock_occupancy[rule["id"]].arrival_event_id = event["id"]
                if lane == "ground":
                    status["docks"] = (local["version"], [dock.snapshot(identifier, stamp, occurred_at)
                                       for identifier, dock in engine.dock_occupancy.items()])
        except (Exception, KeyboardInterrupt) as exc:
            status["failed"] = True
            print(f"CCTV {lane} detector stopped ({type(exc).__name__}). Check the model and runtime settings.", file=sys.stderr, flush=True)

    def heartbeat(now, last_at, last_samples):
        samples = sum(status["samples"] for status in statuses.values())
        age = None if not video.ready else round(max(0, now - video.last_frame_at), 2)
        current = policy
        version = current["version"]
        needed = {lane for lane in LANES if policy_for_lane(current, lane)["rules"]}
        models = {}
        for lane, status in statuses.items():
            reported_version, model = status["model"]
            if model and reported_version in (None, version):
                models[lane] = model
        if not current["rules"] or now >= current["validUntil"]:
            state = "idle" if version else "starting"
        elif delivery_issue or saturated:
            state = "delivery-delayed"
        elif any(lane not in statuses or statuses[lane]["failed"] for lane in needed):
            state = "model-error"
        elif not needed <= models.keys():
            state = "starting"
        else:
            state = "running" if age is not None and age <= 8 else "no-video"
        dock_version, docks = statuses.get("ground", {}).get("docks", (None, []))
        artifacts = json.dumps([statuses.get(lane, {}).get("artifact") for lane in sorted(needed)])
        json_request(worker_endpoint, token, {"version": version, "state": state, "models": list(models.values()),
                     "profileDigest": sha256((profile + artifacts).encode()).hexdigest(),
                     "pending": outbox.count(), "fps": round(min(60, (samples - last_samples) / max(now - last_at, .1)), 2),
                     "frameAgeSeconds": age, **({"dockOccupancy": docks} if docks and dock_version == version else {})})
        return samples

    def communicate():
        nonlocal policy, delivery_issue, saturated
        next_policy = next_status = 0.
        last_at, last_samples = time.monotonic(), 0
        while not stop.is_set():
            now = time.monotonic()
            if now >= next_policy:
                next_policy = now + 5
                try:
                    updated = json_request(worker_endpoint, token)
                    policy = {**gateway_policy(updated, config), "validUntil": now + POLICY_LEASE_SECONDS}
                except HTTPError as error:
                    if error.code in (401, 403, 404):
                        policy = unavailable
                except OSError:
                    pass
                except (ValueError, KeyError):
                    policy = unavailable
            if now >= next_status:
                next_status = now + 10
                with suppress(OSError, ValueError, KeyError):
                    last_samples = heartbeat(now, last_at, last_samples)
                    last_at = now
            try:
                for identifier, encoded in outbox.batch():
                    if stop.is_set():
                        return
                    try:
                        json_request(event_endpoint, token, json.loads(encoded))
                    except HTTPError as error:
                        if error.code not in (400, 422):
                            raise
                        print(f"A CCTV observation was rejected by the site gateway ({error.code}).", file=sys.stderr, flush=True)
                    outbox.complete(identifier)
                delivery_issue = False
                if saturated and outbox.count() < MAX_PENDING:
                    print("CCTV delivery recovered after a full local outbox. Some observations were dropped.", file=sys.stderr, flush=True)
                    saturated = False
            except (OSError, ValueError, sqlite3.Error):
                delivery_issue = True
            stop.wait(2 if delivery_issue else .35)

    threads = [video.thread, Thread(target=communicate, name="cctv-delivery", daemon=True)]
    threads.extend(Thread(target=detector_worker, args=(lane, spec), name=f"cctv-{lane}", daemon=True) for lane, spec in model_configs.items())
    try:
        for thread in threads:
            thread.start()
        stop.wait()
    finally:
        stop.set()
        for thread in threads:
            thread.join(5)
        for signum, old in old_handlers.items():
            signal.signal(signum, old)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--replay", type=Path, help="Read a local file and print observations without connecting to a gateway.")
    mode.add_argument("--check-connection", action="store_true", help="Verify the published policy, RTSP decode and private telemetry route.")
    parser.add_argument("--policy", type=Path, help="Offline rule policy for replay.")
    parser.add_argument("--start-at", help="Timestamp of the first replay frame, with time zone offset.")
    parser.add_argument("--detections", type=Path, help="Optional per-frame JSONL trace for local replay evaluation.")
    args = parser.parse_args()
    if args.start_at and not args.replay:
        parser.error("Use --start-at with a local replay.")
    os.umask(0o077)
    os.environ["ORT_DISABLE_TELEMETRY"] = "1"
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    try:
        config = json.loads(args.config.read_text())
        if not valid_event_device(config.get("sensorId")):
            raise ValueError("Set the camera ID to its exact Global View device ID.")
        if any(field in config and not valid_event_device(config[field]) for field in ("siteId", "organizationId")):
            raise ValueError("Use the site and organization IDs from Global View.")
        dimensions = config.get("width", 640), config.get("height", 360), config.get("fps", 6)
        width, height, fps = dimensions
        if (any(type(side) is not int or not 32 <= side <= 4096 for side in (width, height))
                or type(fps) is not int or not 1 <= fps <= 30 or not shutil.which("ffmpeg")):
            raise ValueError("Set a valid sampled frame size and rate, and install FFmpeg.")
        buffer_ms = config.get("liveFrameBufferMs", 0)
        if type(buffer_ms) is not int or not 0 <= buffer_ms <= 500:
            raise ValueError("Set liveFrameBufferMs to an integer from 0 to 500 milliseconds.")
        models = model_specs(config, fps)
        if args.replay:
            run_replay(args, config, dimensions, models)
        elif args.check_connection:
            check_connection(config, dimensions, models)
        else:
            run_live(config, dimensions, models, buffer_ms)
    except (ImportError, KeyError, OSError, ValueError, RuntimeError) as error:
        # Private input URLs and credentials never appear in command output.
        if isinstance(error, ValueError) and not isinstance(error, json.JSONDecodeError):
            parser.error(str(error))
        parser.error("Check the private worker configuration, model files, gateway token, and video source.")


if __name__ == "__main__":
    main()
