"""Field qualification for published camera rules."""

from hashlib import sha256
import json
import time

from onvif import CameraError
from analytics_contract import number
from site_collaboration import Conflict


def shape(camera, boundary, rule, zone):
    view = {key: camera.get(key) for key in ("position", "floorId", "cameraView", "cameraFeedUrl", "cameraGridUrl",
                                             "cameraProvider", "cameraChannel", "analyticsPlacement")}
    condition = {key: value for key, value in rule.items() if key not in ("name", "mode")}
    return [view, boundary["vertices"], boundary.get("floorId"), boundary["enabled"], condition,
            zone if rule.get("schedule") else None]


def fingerprint(db, version, camera, boundary, rule, zone, *, fresh=False):
    profile = {"native": True}
    if not rule.get("cameraNative"):
        row = db.execute("SELECT body,updated_at FROM analytics_workers WHERE camera=?", (camera["id"],)).fetchone()
        status = json.loads(row[0]) if row else {}
        if not status.get("profileDigest") or not status.get("models"):
            return None
        if fresh and (status["version"] != version or status["state"] != "running" or time.time() - row[1] > 60):
            return None
        if status.get("placement") != camera.get("analyticsPlacement"):
            return None
        profile = {"digest": status["profileDigest"]}
    return sha256(json.dumps([shape(camera, boundary, rule, zone), profile], sort_keys=True,
                             separators=(",", ":")).encode()).hexdigest()


def qualification(payload):
    if not isinstance(payload, dict):
        raise CameraError("Enter the reviewed counts, latency and customer thresholds.")
    tp, fp, fn, hours, latency, recall, rate, maximum_latency = (payload.get(key) for key in (
        "truePositives", "falsePositives", "missedEvents", "cameraHours", "p95LatencySeconds",
        "minimumRecall", "maximumFalseAlarmsPerHour", "maximumP95Seconds"))
    if (any(type(count) is not int or not 0 <= count <= 1_000_000 for count in (tp, fp, fn))
            or not tp + fn or not number(hours, .01, 100_000) or not number(latency, 0, 3600)
            or not number(recall, .01, 1) or not number(rate, 0, 10000) or not number(maximum_latency, .01, 3600)):
        raise CameraError("Enter valid qualification measurements, including at least one labeled event and reviewed camera time.")
    measurements = {"truePositives": tp, "falsePositives": fp, "missedEvents": fn, "cameraHours": hours,
                    "p95LatencySeconds": latency, "minimumRecall": recall, "maximumFalseAlarmsPerHour": rate,
                    "maximumP95Seconds": maximum_latency}
    return {**measurements, "passed": tp / (tp + fn) >= recall and fp / hours <= rate and latency <= maximum_latency}


def require_approval(db, active, snapshot):
    prior = json.loads(active[2]) if active else {}
    old_cameras = {camera["id"]: camera for camera in prior.get("sensors", ())}
    old_zones = {boundary["id"]: boundary for boundary in prior.get("boundaries", ())}
    zones = {boundary["id"]: boundary for boundary in snapshot["boundaries"]}
    for camera in snapshot["sensors"]:
        old_camera = old_cameras.get(camera["id"], {})
        old_rules = {rule["id"]: rule for rule in old_camera.get("analyticsRules", ())}
        for rule in camera.get("analyticsRules", ()):
            boundary = zones[rule["boundaryId"]]
            if rule["mode"] != "alert" or not boundary["enabled"]:
                continue
            old = old_rules.get(rule["id"])
            candidate = shape(camera, boundary, rule, snapshot.get("analyticsTimeZone"))
            if old and old["mode"] == "alert" and candidate == shape(old_camera, old_zones[old["boundaryId"]], old,
                                                                     prior.get("analyticsTimeZone")):
                continue
            key = fingerprint(db, active[0] if active else 0, camera, boundary, rule,
                              snapshot.get("analyticsTimeZone"), fresh=True) if active else None
            if key and db.execute("SELECT 1 FROM camera_rule_acceptance WHERE camera=? AND rule=? AND fingerprint=? AND passed=1",
                                  (camera["id"], rule["id"], key)).fetchone():
                continue
            raise Conflict(f"Publish {camera['name']} · {rule['name']} in Observe and record passing acceptance before enabling Alert.")
