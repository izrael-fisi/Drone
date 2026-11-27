"""Validated site inventory, a shared draft, and immutable published versions."""

import json
from datetime import datetime
from math import isfinite
import time
from urllib.parse import urlsplit

from onvif import CameraError
from site_activity import valid_event_device
from site_collaboration import Conflict, iso_now, text_field
from analytics_contract import PLACEMENTS, rules as analytics_rules, unit_point
from site_geometry import EPSILON, cross, segments_intersect

STYLES = {"sage", "stone", "slate", "rose", "copper"}


def coordinates(value):
    if not isinstance(value, list) or len(value) != 2 or not all(number(axis, -limit, limit) for axis, limit in zip(value, (180, 85))):
        raise CameraError("Enter valid longitude and latitude coordinates.")
    return value


def number(value, lower, upper):
    return type(value) in (int, float) and lower <= value <= upper and isfinite(value)


def validate_ring(vertices, minimum=3, maximum=120, point_reader=coordinates):
    if not isinstance(vertices, list) or not minimum <= len(vertices) <= maximum:
        raise CameraError("A shape must have a valid number of vertices.")
    points = [point_reader(vertex) for vertex in vertices]
    longitudes = [point[0] for point in points]
    edges = list(zip(points, points[1:] + points[:1]))
    if len(set(map(tuple, points))) != len(points) or max(longitudes) - min(longitudes) >= 180 or abs(sum(cross(points[0], a, b) for a, b in edges)) <= EPSILON:
        raise CameraError("Use a local shape with distinct vertices and an interior area.")
    for i, (a, b) in enumerate(edges):
        for j in range(i + 2, len(edges)):
            if i == 0 and j == len(edges) - 1:
                continue
            c, d = edges[j]
            if segments_intersect(a, b, c, d):
                raise CameraError("Shape edges must not cross.")
    return points


def endpoint(value):
    if value in (None, ""):
        return None
    if not isinstance(value, str) or len(value) > 4096 or any(char.isspace() or ord(char) < 32 for char in value):
        raise CameraError("Use an HTTP(S) source URL without credentials.")
    try:
        url = urlsplit(value)
        if url.scheme in ("https", "http") and url.hostname and url.username is None and url.password is None:
            return value
    except ValueError:
        pass
    raise CameraError("Use an HTTP(S) source URL without credentials.")


def collection(payload, key, maximum):
    items = payload.get(key, [])
    if not isinstance(items, list) or len(items) > maximum or any(not isinstance(item, dict) or not valid_event_device(item.get("id")) for item in items):
        raise CameraError(f"Invalid {key} inventory or device IDs.")
    by_id = {item["id"]: item for item in items}
    if len(by_id) != len(items):
        raise CameraError(f"Each {key} ID must be unique.")
    return by_id


def labels(item):
    tags = item.get("labels", [])
    if not isinstance(tags, list) or len(tags) > 8 or any(not isinstance(tag, str) or not 1 <= len(tag.strip()) <= 32 for tag in tags):
        raise CameraError("Use up to eight short labels per item.")
    return list(dict.fromkeys(tag.strip() for tag in tags))


def reference_list(value, allowed):
    if not isinstance(value, list) or any(not valid_event_device(key) or key not in allowed for key in value):
        raise CameraError("A device, boundary, or dependency references an unknown ID.")
    return list(dict.fromkeys(value))


def normalize_configuration(payload):
    if not isinstance(payload, dict):
        raise CameraError("Provide site configuration as a JSON object.")
    sensor_defs, zone_defs, groups, floors, dependencies = (collection(payload, key, limit) for key, limit in (
        ("sensors", 512), ("boundaries", 256), ("groups", 128), ("floors", 128), ("dependencies", 128),
    ))
    rule = payload.get("correlationRule") or {"enabled": False, "windowSeconds": 45}
    if not isinstance(rule, dict) or type(rule.get("enabled")) is not bool or type(rule.get("windowSeconds")) is not int or not 5 <= rule["windowSeconds"] <= 180:
        raise CameraError("Use a door and camera correlation window from 5 to 180 seconds.")
    grace = rule.get("graceSeconds", 300)
    if type(grace) is not int or not 0 <= grace <= 3600:
        raise CameraError("Use a late-arrival grace period from 0 to 3600 seconds.")
    held_open = rule.get("includeHeldOpen", False)
    if type(held_open) is not bool:
        raise CameraError("Choose whether to include held-open door events.")
    sensors, inspection_stations = [], set()
    for key, sensor in sensor_defs.items():
        modality = sensor.get("modality")
        floor, group, linked = (sensor.get(field) for field in ("floorId", "groupId", "linkedCameraId"))
        if modality not in ("vision", "rf", "access") or (floor is not None and (not valid_event_device(floor) or floor not in floors)) or (
                group is not None and (not valid_event_device(group) or group not in groups)):
            raise CameraError(f"Invalid type, group or floor for device {key}.")
        if linked is not None and (modality != "access" or not valid_event_device(linked) or sensor_defs.get(linked, {}).get("modality") != "vision"):
            raise CameraError(f"Device {key} must link to a registered camera.")
        passage_rule = sensor.get("passageRuleId")
        if passage_rule is not None and (not linked or not valid_event_device(passage_rule)):
            raise CameraError("Choose an inbound tripwire on the linked camera.")
        camera_view = sensor.get("cameraView") if modality == "vision" else None
        if camera_view is not None:
            limits = {"bearingDeg": (0, 360), "fovDeg": (1, 360), "rangeM": (1, 5000)}
            if not isinstance(camera_view, dict) or not all(number(camera_view.get(field), *bounds) for field, bounds in limits.items()):
                raise CameraError(f"Invalid field of view for camera {key}.")
            camera_view = {field: camera_view[field] for field in limits}
        gateway = sensor.get("cameraGatewayKind")
        placement = sensor.get("analyticsPlacement")
        if placement is not None and (modality != "vision" or placement not in PLACEMENTS):
            raise CameraError("Choose cloud or on-premises analytics for a camera.")
        inspection_station = sensor.get("inspectionStationId")
        if inspection_station is not None:
            if modality != "vision" or not valid_event_device(inspection_station) or inspection_station in inspection_stations:
                raise CameraError("Assign each inspection station ID to one camera.")
            inspection_stations.add(inspection_station)
        verified = sensor.get("cameraVerifiedAt")
        verified_at = None
        if isinstance(verified, str) and len(verified) <= 40:
            try:
                if datetime.fromisoformat(verified).tzinfo:
                    verified_at = verified
            except ValueError:
                pass
        interval = sensor.get("reportIntervalSeconds", 60)
        if type(interval) is not int or not 5 <= interval <= 3600:
            raise CameraError(f"Invalid heartbeat interval for {key}.")
        sensors.append({"id": key, "name": text_field(sensor, "name", 60), "modality": modality, "position": coordinates(sensor.get("position")),
                        "demo": False, "groupId": group, "floorId": floor, "labels": labels(sensor), "mapLabel": text_field(sensor, "mapLabel", 24, True),
                        "cameraView": camera_view, "cameraFeedUrl": endpoint(sensor.get("cameraFeedUrl")) if modality == "vision" else None,
                        "cameraGridUrl": endpoint(sensor.get("cameraGridUrl")) if modality == "vision" else None,
                        "cameraGatewayKind": gateway if modality == "vision" and gateway in ("rtsp", "onvif") else None,
                        "cameraProvider": text_field(sensor, "cameraProvider", 80, True), "cameraChannel": text_field(sensor, "cameraChannel", 80, True),
                        "linkedCameraId": linked, "reportIntervalSeconds": interval, "healthUrl": endpoint(sensor.get("healthUrl")),
                        **({"passageRuleId": passage_rule} if passage_rule is not None else {}),
                        "gatewayId": text_field(sensor, "gatewayId", 96, True),
                        "dependencyIds": reference_list(sensor.get("dependencyIds", []), dependencies),
                        **({"analyticsPlacement": placement} if placement is not None else {}),
                        **({"inspectionStationId": inspection_station} if inspection_station is not None else {}),
                        **({"cameraVerifiedAt": verified_at} if verified_at and camera_view else {})})
    zones = []
    for key, zone in zone_defs.items():
        kind, floor = zone.get("kind"), zone.get("floorId")
        if kind not in ("Perimeter", "Restricted", "Access") or (floor is not None and (not valid_event_device(floor) or floor not in floors)) or type(zone.get("enabled")) is not bool:
            raise CameraError(f"Invalid type, floor or state for boundary {key}.")
        assigned = reference_list(zone.get("sensorIds"), sensor_defs)
        if any(sensor_defs[device].get("floorId") != floor for device in assigned):
            raise CameraError(f"Boundary {key} can only assign devices on the same level.")
        zones.append({"id": key, "name": text_field(zone, "name", 60), "kind": kind, "floorId": floor, "enabled": zone["enabled"],
                      "vertices": validate_ring(zone.get("vertices")), "sensorIds": assigned, "blocksCameraView": zone.get("blocksCameraView") is True,
                      "mapLabel": text_field(zone, "mapLabel", 32, True), "labels": labels(zone), "notes": text_field(zone, "notes", 280, True),
                      "responseTeam": text_field(zone, "responseTeam", 60, True),
                      **({"mapTone": zone["mapTone"]} if isinstance(zone.get("mapTone"), str) and zone["mapTone"] in STYLES else {})})
    inventory = {"sensors": sensors, "boundaries": zones, "correlationRule": {"enabled": rule["enabled"],
                 "windowSeconds": rule["windowSeconds"], "graceSeconds": grace, **({"includeHeldOpen": True} if held_open else {})}}
    for sensor in sensors:
        configured = sensor_defs[sensor["id"]].get("analyticsRules", [])
        if configured or configured != []:
            if sensor["modality"] != "vision":
                raise CameraError("Configure CCTV analytics on a camera.")
            assigned = {zone["id"] for zone in zones if sensor["id"] in zone["sensorIds"]}
            sensor["analyticsRules"] = analytics_rules(configured, assigned, lambda points: validate_ring(points, 3, 12, unit_point))
    camera_rules = {sensor["id"]: {rule["id"]: rule for rule in sensor.get("analyticsRules", ())} for sensor in sensors}
    used_passage_rules = set()
    for sensor in sensors:
        if passage := sensor.get("passageRuleId"):
            key = (sensor["linkedCameraId"], passage)
            rule = camera_rules[key[0]].get(passage)
            if (not rule or rule["kind"] != "line-crossing" or rule["object"] != "person" or rule["direction"] == "either" or rule.get("cameraNative")
                    or key in used_passage_rules or not any(zone["id"] == rule["boundaryId"] and sensor["id"] in zone["sensorIds"] for zone in zones)):
                raise CameraError("Give each passage door a unique directional person tripwire in its assigned boundary.")
            used_passage_rules.add(key)
    inventory["groups"] = [{"id": key, "name": text_field(item, "name", 60)} for key, item in groups.items()]
    inventory["floors"] = [{"id": key, "name": text_field(item, "name", 40), "building": text_field(item, "building", 60),
                            "imageUrl": endpoint(item.get("imageUrl")), "corners": validate_ring(item.get("corners"), 4, 4)} for key, item in floors.items()]
    inventory["dependencies"] = [{"id": key, "name": text_field(item, "name", 60), "kind": item["kind"]} for key, item in dependencies.items()
                                 if item.get("kind") in ("gateway", "switch", "ups")]
    if len(inventory["dependencies"]) != len(dependencies) or any(not floor["imageUrl"] for floor in inventory["floors"]):
        raise CameraError("Provide a supported dependency type and an image URL for every floor.")
    from response_procedures import normalize_procedures
    if "procedures" in payload:
        inventory["procedures"] = normalize_procedures(payload["procedures"], zone_defs)
    return inventory


def configuration_diff(before, after):
    result = []
    for name in ("sensors", "boundaries", "floors", "groups", "dependencies"):
        old, new = ({entry["id"]: entry for entry in snapshot.get(name, [])} for snapshot in (before, after))
        for key in sorted(old.keys() | new.keys()):
            if old.get(key) != new.get(key):
                earlier, later = old.get(key, {}), new.get(key, {})
                result.append({"collection": name, "id": key, "name": (new.get(key) or old[key])["name"],
                               "change": "added" if key not in old else "removed" if key not in new else "changed",
                               "fields": [{"name": field, "before": earlier.get(field), "after": later.get(field)}
                                          for field in sorted(earlier.keys() | later.keys()) if earlier.get(field) != later.get(field)]})
    if before.get("correlationRule") != after.get("correlationRule"):
        result.append({"collection": "rules", "id": "door-camera", "name": "Door and camera correlation", "change": "changed",
                       "fields": [{"name": "correlationRule", "before": before.get("correlationRule"), "after": after.get("correlationRule")} ]})
    if before.get("procedures", []) != after.get("procedures", []):
        result.append({"collection": "rules", "id": "response-procedures", "name": "Response procedures", "change": "changed",
                       "fields": [{"name": "procedures", "before": before.get("procedures", []), "after": after.get("procedures", [])}]})
    return result


class SiteConfiguration:
    def __init__(self, activity, time_zone="UTC"):
        self.activity = activity
        self.time_zone = time_zone
        with activity.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS configuration_versions (
                    version INTEGER PRIMARY KEY AUTOINCREMENT, published_at REAL NOT NULL, actor TEXT NOT NULL,
                    message TEXT NOT NULL, body TEXT NOT NULL, restored_from INTEGER
                );
                CREATE TABLE IF NOT EXISTS configuration_draft (
                    singleton INTEGER PRIMARY KEY CHECK (singleton=1), draft INTEGER NOT NULL, base INTEGER NOT NULL, body TEXT
                );
                INSERT OR IGNORE INTO configuration_draft VALUES(1,0,0,NULL);
                CREATE INDEX IF NOT EXISTS configuration_time ON configuration_versions(published_at);
            """)

    @staticmethod
    def published(db, event_time=None):
        query = "SELECT version,published_at,body FROM configuration_versions"
        return db.execute(query + " ORDER BY version DESC LIMIT 1").fetchone() if event_time is None else db.execute(
            query + " WHERE published_at <= ? ORDER BY version DESC LIMIT 1", (event_time,)).fetchone()

    def detail(self):
        with self.activity.connect() as db:
            db.execute("BEGIN")
            active = self.published(db)
            draft, base, body = db.execute("SELECT draft,base,body FROM configuration_draft").fetchone()
            saved = json.loads(body) if body else None
            current = json.loads(active[2]) if active else {}
            history = db.execute("SELECT version,published_at,actor,message,restored_from FROM configuration_versions ORDER BY version DESC LIMIT 30").fetchall()
        return {"publishedVersion": active[0] if active else 0, "draftRevision": draft, "draftBase": base,
                "draft": saved, "published": current if active else None, "diff": configuration_diff(current, saved) if saved is not None else [],
                "history": [{"version": version, "publishedAt": stamp, "actor": actor, "message": message, "restoredFrom": restored}
                            for version, stamp, actor, message, restored in history]}

    def change(self, actor, payload):
        mode = payload.get("operation")
        if mode not in ("save", "publish", "restore", "discard"):
            raise CameraError("Choose a supported configuration operation.")
        revision, base = (payload.get(field) for field in ("draftRevision", "publishedVersion"))
        if type(revision) is not int or type(base) is not int:
            raise CameraError("Include the published version and draft revision.")
        if mode == "save":
            configuration = normalize_configuration(payload.get("configuration"))
        with self.activity.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            active = self.published(db)
            current = active[0] if active else 0
            saved_revision, draft_base, draft_body = db.execute("SELECT draft,base,body FROM configuration_draft").fetchone()
            if revision != saved_revision or base != current or draft_body and draft_base != current:
                raise Conflict("Site configuration changed. Reload it before saving or publishing.")
            if mode in ("save", "discard"):
                encoded = None
                if mode == "save":
                    previous = {sensor["id"]: sensor for sensor in json.loads(active[2])["sensors"]} if active else {}
                    for sensor in configuration["sensors"]:
                        if sensor["modality"] != "vision" or "analyticsPlacement" in sensor:
                            continue
                        before = previous.get(sensor["id"], {})
                        if "analyticsPlacement" in before:
                            sensor["analyticsPlacement"] = before["analyticsPlacement"]
                        elif sensor.get("analyticsRules") and not before.get("analyticsRules"):
                            sensor["analyticsPlacement"] = "cloud"
                    encoded = json.dumps(configuration, separators=(",", ":"), allow_nan=False)
                db.execute("UPDATE configuration_draft SET draft=draft+1,base=?,body=?", (current, encoded))
            else:
                restored = None
                body = draft_body
                if mode == "restore":
                    restored = payload.get("restoreVersion")
                    if type(restored) is not int or not 0 < restored < current:
                        raise CameraError("Choose a prior published version to restore.")
                    row = db.execute("SELECT body FROM configuration_versions WHERE version=?", (restored,)).fetchone()
                    body = row[0] if row else None
                if body is None:
                    raise CameraError("Save a draft or select a published version first.")
                if mode == "publish":
                    snapshot = json.loads(body)
                    if any(rule.get("schedule") for sensor in snapshot["sensors"] for rule in sensor.get("analyticsRules", ())):
                        snapshot["analyticsTimeZone"] = self.time_zone
                        body = json.dumps(snapshot, separators=(",", ":"), allow_nan=False)
                label = text_field(payload, "message", 180)
                version = db.execute("INSERT INTO configuration_versions(published_at,actor,message,body,restored_from) VALUES(?,?,?,?,?)",
                                     (time.time(), actor["subject"], label, body, restored)).lastrowid
                db.execute("UPDATE configuration_draft SET draft=draft+1,base=?,body=NULL", (version,))
            db.execute("INSERT INTO activity_audit(occurred_at,actor,action,resource,detail) VALUES(?,?,?,?,?)",
                       (iso_now(), actor["subject"], "configuration-" + mode, "site", str(current)))
        return self.detail()
