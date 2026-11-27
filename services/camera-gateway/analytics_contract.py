"""Versioned rule and evidence contract shared by the gateway and CCTV worker."""

import json
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from onvif import CameraError
from site_activity import valid_event_device
from site_geometry import contains, on_segment

KINDS = {"line-crossing", "zone-entry", "zone-dwell", "restricted-stop", "drone-sighting"}
LABELS = {"person": "Person", "vehicle": "Vehicle", "drone": "Possible drone"}
RULE_LABELS = {"line-crossing": "crossed line", "zone-entry": "entered zone", "zone-dwell": "dwelled in zone",
               "restricted-stop": "stopped in zone", "drone-sighting": "sighted"}
STATES = {"starting", "running", "no-video", "model-error", "idle", "delivery-delayed"}
PROFILES = ("all-day", "day", "night")
PLACEMENTS = ("cloud", "on-premises")
LINK_CHECK = "analytics-link-check"
SAFETY_RULES = {"person-vehicle-lane": ("zone-entry", "person"),
                "vehicle-marked-walkway": ("restricted-stop", "vehicle")}


def detection_summary(rule, item):
    subject = LABELS[item["object"]]
    if "dwellSeconds" in item:
        return f"{subject} remained in zone for {item['dwellSeconds']:.2f}".rstrip("0").rstrip(".") + f" s · {rule['name']}"
    return f"{subject} {RULE_LABELS[rule['kind']]} · {rule['name']}"


def worker_tokens(path):
    if path is None:
        return {}
    tokens = json.loads(path.read_text())
    if not isinstance(tokens, dict):
        raise CameraError("Map each camera ID to its cloud and on-premises worker tokens.")
    seen = set()
    for camera, placements in tokens.items():
        if not valid_event_device(camera) or not isinstance(placements, dict) or not placements or placements.keys() - set(PLACEMENTS):
            raise CameraError("Map each camera ID to its cloud and on-premises worker tokens.")
        for key in placements.values():
            if not isinstance(key, str) or not re.fullmatch(r"[a-f0-9]{64}", key) or key in seen:
                raise CameraError("Generate a distinct 64-character hexadecimal token for each camera and compute placement.")
            seen.add(key)
    return tokens


def number(value, low, high):
    return type(value) in (int, float) and low <= value <= high


def unit_point(value):
    if not isinstance(value, list) or len(value) != 2 or not all(number(item, 0, 1) for item in value):
        raise CameraError("Enter image points between 0 and 1.")
    return value


def thresholds(rule):
    score, seconds, pixels = (rule.get(field) for field in ("minimumScore", "seconds", "minimumPixels"))
    if not number(score, .05, 1) or not number(seconds, .25, 600) or type(pixels) is not int or not 4 <= pixels <= 2000:
        raise CameraError("Set a confidence from 5% to 100%, confirmation from .25 to 600 seconds, and minimum object size from 4 to 2,000 pixels.")
    return {"minimumScore": score, "seconds": seconds, "minimumPixels": pixels}


def profile_schedule(value):
    if not isinstance(value, dict) or not isinstance(value.get("night"), dict):
        raise CameraError("Set the day and night start times and the night thresholds.")
    day, night = (value.get(field) for field in ("dayStartsAt", "nightStartsAt"))
    if not all(isinstance(mark, str) and re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", mark) for mark in (day, night)) or day == night:
        raise CameraError("Choose distinct day and night start times.")
    return {"dayStartsAt": day, "nightStartsAt": night, "night": thresholds(value["night"])}


def policy_zone(name):
    return timezone.utc if name == "UTC" else ZoneInfo(name)


def effective_rule(rule, stamp, zone):
    schedule = rule.get("schedule")
    if not schedule:
        return rule
    local = datetime.fromtimestamp(stamp, zone).strftime("%H:%M")
    day, night = schedule["dayStartsAt"], schedule["nightStartsAt"]
    daytime = day <= local < night if day < night else local >= day or local < night
    return {**rule, "profile": "day"} if daytime else {**rule, **schedule["night"], "profile": "night"}


def detection_anchor(box, label):
    x0, y0, x1, y1 = box
    return (x0 + x1) / 2, (y0 + y1) / 2 if label == "drone" else y1


def excluded(rule, point):
    return any(contains(shape, point) for shape in rule.get("exclusions", ()))


def matches_evidence(rule, item):
    profile = rule.get("profile", "all-day")
    anchor = detection_anchor(item["box"], item["object"])
    if (item.get("profile", "all-day") != profile or rule.get("exclusions") and not item.get("profile")
            or excluded(rule, anchor) or not qualifies(rule, item)):
        return False
    if bool(rule.get("requireMovement")) != (item.get("movementObserved") is True):
        return False
    if (rule.get("requireMovement") or rule.get("trackOccupancy") or rule.get("safetyRule")) and not contains(rule["points"], anchor):
        return False
    if "dwellSeconds" in item:
        return (rule["kind"] == "zone-dwell" and item["dwellSeconds"] + 1e-6 >= rule["seconds"]
                and contains(rule["points"], anchor))
    if rule.get("dwellGapSeconds"):
        return False
    if rule["kind"] == "line-crossing" and (rule.get("exclusions") or "crossing" in item):
        point = item.get("crossing")
        return point is not None and on_segment(*rule["points"], point) and not excluded(rule, point)
    return True


def rules(raw, boundaries, polygon):
    if not isinstance(raw, list) or len(raw) > 12 or any(not isinstance(rule, dict) for rule in raw):
        raise CameraError("A camera supports up to 12 analytics rules.")
    result, seen = [], set()
    for rule in raw:
        identifier, name, kind, label, mode, boundary = (rule.get(field) for field in
            ("id", "name", "kind", "object", "mode", "boundaryId"))
        if (not valid_event_device(identifier) or identifier in seen or not isinstance(name, str) or not 1 <= len(name.strip()) <= 48
                or not isinstance(kind, str) or kind not in KINDS or mode not in ("observe", "alert")
                or not valid_event_device(boundary) or boundary not in boundaries):
            raise CameraError("Choose a unique CCTV rule, its mode, and a boundary assigned to this camera.")
        allowed = ("drone",) if kind == "drone-sighting" else ("vehicle",) if kind == "restricted-stop" else ("person", "vehicle")
        if label not in allowed:
            raise CameraError("The CCTV rule does not support this object type.")
        points, direction = rule.get("points"), rule.get("direction", "either")
        if kind == "line-crossing":
            if not isinstance(points, list) or len(points) != 2 or unit_point(points[0]) == unit_point(points[1]):
                raise CameraError("A tripwire needs two distinct image points.")
            if direction not in ("either", "positive", "negative"):
                raise CameraError("Choose a tripwire direction.")
        else:
            points, direction = polygon(points), "either"
        exclusions = rule.get("exclusions", [])
        if not isinstance(exclusions, list) or len(exclusions) > 4:
            raise CameraError("Use up to four exclusion zones per CCTV rule.")
        gap = rule.get("dwellGapSeconds", 0)
        if not number(gap, 0, 1) or gap and kind != "zone-dwell":
            raise CameraError("Time in zone supports up to one second between sightings when detection is interrupted.")
        require_movement = rule.get("requireMovement", False)
        if type(require_movement) is not bool or require_movement and kind != "restricted-stop":
            raise CameraError("Movement confirmation applies to vehicle stops.")
        scale_crossing_buffer = rule.get("scaleCrossingBuffer", False)
        if type(scale_crossing_buffer) is not bool or scale_crossing_buffer and (kind != "line-crossing" or label != "person"):
            raise CameraError("An adaptive crossing buffer applies to person tripwires.")
        track_occupancy = rule.get("trackOccupancy", False)
        if type(track_occupancy) is not bool or track_occupancy and (kind, label, mode) != ("zone-entry", "vehicle", "observe"):
            raise CameraError("Dock occupancy uses an Observe-only vehicle zone-entry rule.")
        safety_rule = rule.get("safetyRule")
        if safety_rule is not None and (not isinstance(safety_rule, str) or SAFETY_RULES.get(safety_rule) != (kind, label)
                                        or mode != "observe" or require_movement):
            raise CameraError("Use the matching Observe-only behavior for a factory safety rule.")
        native = rule.get("cameraNative", False)
        if type(native) is not bool or native and (kind != "line-crossing" or mode != "observe"):
            raise CameraError("Camera-native analytics uses an Observe-only line-crossing rule.")
        seen.add(identifier)
        result.append({"id": identifier, "name": name.strip(), "kind": kind, "object": label, "mode": mode,
                       "boundaryId": boundary, "points": points, "direction": direction, **thresholds(rule),
                       **({"exclusions": [polygon(shape) for shape in exclusions]} if exclusions else {}),
                       **({"dwellGapSeconds": gap} if gap else {}),
                       **({"requireMovement": True} if require_movement else {}),
                       **({"scaleCrossingBuffer": True} if scale_crossing_buffer else {}),
                       **({"trackOccupancy": True} if track_occupancy else {}),
                       **({"safetyRule": safety_rule} if safety_rule else {}),
                       **({"cameraNative": True} if native else {}),
                       **({"schedule": profile_schedule(rule["schedule"])} if "schedule" in rule else {})})
    return result


def evidence(raw):
    if not isinstance(raw, dict):
        raise CameraError("Supply CCTV analytics provenance.")
    version, rule, label, model, track, score, box, frame, source = (raw.get(field) for field in
        ("policyVersion", "ruleId", "object", "model", "trackId", "score", "box", "frame", "source"))
    if (type(version) is not int or not 1 <= version < 2**53 or not valid_event_device(rule) or label not in ("person", "vehicle", "drone")
            or not isinstance(model, str) or not re.fullmatch(r"[A-Za-z0-9_.:+/-]{1,96}", model)
            or not valid_event_device(track) or not number(score, 0, 1) or source not in ("live", "replay")
            or not isinstance(box, list) or len(box) != 4 or not all(number(axis, 0, 1) for axis in box)
            or not (box[0] < box[2] and box[1] < box[3])
            or not isinstance(frame, list) or len(frame) != 2 or any(type(side) is not int or not 32 <= side <= 4096 for side in frame)):
        raise CameraError("Provide valid CCTV rule, model, track and image evidence.")
    result = {"policyVersion": version, "ruleId": rule, "object": label, "model": model, "trackId": track,
              "score": score, "box": box, "frame": frame, "source": source}
    if "profile" in raw:
        if raw["profile"] not in PROFILES:
            raise CameraError("Choose a supported CCTV profile.")
        result["profile"] = raw["profile"]
    if "crossing" in raw:
        result["crossing"] = unit_point(raw["crossing"])
    if "dwellSeconds" in raw:
        if not number(raw["dwellSeconds"], 0, 660):
            raise CameraError("Provide a valid observed dwell duration.")
        result["dwellSeconds"] = raw["dwellSeconds"]
    if "movementObserved" in raw:
        if label != "vehicle" or raw["movementObserved"] is not True:
            raise CameraError("Provide valid vehicle movement confirmation.")
        result["movementObserved"] = True
    return result


def qualifies(rule, item):
    x1, y1, x2, y2 = item["box"]
    width, height = item["frame"]
    return (item["object"] == rule["object"] and item["score"] >= rule["minimumScore"]
            and max((x2 - x1) * width, (y2 - y1) * height) + 1e-6 >= rule["minimumPixels"])
