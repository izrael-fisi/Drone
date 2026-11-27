"""Validate and preview site inventory uploads through the versioned draft."""

import copy
import csv
from io import StringIO
import json

from onvif import CameraError
from site_activity import valid_event_device
from site_collaboration import Conflict
from site_configuration import configuration_diff, normalize_configuration, validate_ring

FIELDS = {"id", "name", "type", "longitude", "latitude", "floorId", "groupId", "boundaryIds", "linkedCameraId", "labels"}
REQUIRED = {"id", "name", "type", "longitude", "latitude"}
TYPES = {"camera": "vision", "vision": "vision", "door": "access", "access": "access", "rf": "rf"}


def build_import(source, device_csv, boundaries_json):
    inventory = copy.deepcopy(source or {"sensors": [], "boundaries": [], "groups": [], "floors": [], "dependencies": []})
    sensors = {row["id"]: row for row in inventory["sensors"]}
    boundaries = {row["id"]: row for row in inventory["boundaries"]}
    errors, unchanged, assigned, seen = [], [], {}, set()
    if boundaries_json.strip():
        try:
            collection = json.loads(boundaries_json)
            if not isinstance(collection, dict) or collection.get("type") != "FeatureCollection" or "crs" in collection:
                raise CameraError("Use a WGS 84 GeoJSON FeatureCollection.")
            features = collection.get("features")
            if not isinstance(features, list) or len(features) > 256:
                raise CameraError("Include up to 256 boundary features.")
            for index, item in enumerate(features, 1):
                try:
                    if not isinstance(item, dict) or item.get("type") != "Feature":
                        raise CameraError("Choose a GeoJSON Feature.")
                    identifier = item.get("id")
                    if not valid_event_device(identifier) or identifier in seen:
                        raise CameraError("Supply a unique string feature ID.")
                    seen.add(identifier)
                    geometry, properties = item.get("geometry"), item.get("properties")
                    rings = geometry.get("coordinates") if isinstance(geometry, dict) else None
                    if not isinstance(rings, list) or len(rings) != 1 or geometry.get("type") != "Polygon":
                        raise CameraError("Use a single exterior Polygon ring.")
                    ring = rings[0]
                    if not isinstance(ring, list) or len(ring) < 4 or ring[0] != ring[-1]:
                        raise CameraError("Close the polygon ring.")
                    points = validate_ring(ring[:-1])
                    if (not isinstance(properties, dict) or not isinstance(properties.get("name"), str)
                            or not properties["name"].strip() or len(properties["name"].strip()) > 60
                            or properties.get("kind") not in ("Perimeter", "Restricted", "Access")):
                        raise CameraError("Supply the boundary name and kind.")
                    previous = boundaries.get(identifier, {})
                    row = {**previous, "id": identifier, "name": properties["name"].strip(), "kind": properties["kind"],
                           "vertices": points, "enabled": previous.get("enabled", False), "sensorIds": previous.get("sensorIds", [])}
                    if "floorId" in properties:
                        row["floorId"] = properties["floorId"] or None
                    if row.get("floorId") is not None and row["floorId"] not in {floor["id"] for floor in inventory.get("floors", ())}:
                        raise CameraError("Use a floor ID from the current site draft.")
                    boundaries[identifier] = row
                except CameraError as issue:
                    errors.append({"source": "GeoJSON", "row": index, "message": str(issue)})
        except (ValueError, CameraError) as issue:
            errors.append({"source": "GeoJSON", "row": 0, "message": str(issue)})
    seen.clear()
    if device_csv.strip():
        try:
            reader = csv.DictReader(StringIO(device_csv.removeprefix("\ufeff")), strict=True)
            columns = reader.fieldnames or []
            if not REQUIRED <= set(columns) or set(columns) - FIELDS or len(columns) != len(set(columns)):
                raise CameraError("Use the required device headers and supported optional fields.")
            for line, values in enumerate(reader, 2):
                if line > 513:
                    raise CameraError("Include up to 512 devices per import.")
                try:
                    if None in values or any(value is None for value in values.values()):
                        raise CameraError("The row does not match the CSV header.")
                    values = {name: value.strip() for name, value in values.items()}
                    identifier = values["id"]
                    if not valid_event_device(identifier) or identifier in seen:
                        raise CameraError("Supply a unique device ID.")
                    seen.add(identifier)
                    if not values["name"] or len(values["name"]) > 60:
                        raise CameraError("Use a device name of up to 60 characters.")
                    modality = TYPES.get(values["type"].lower())
                    previous = sensors.get(identifier, {})
                    if not modality or previous and previous["modality"] != modality:
                        raise CameraError("Use a supported device type and keep an existing device's type.")
                    try:
                        longitude, latitude = (float(values[field]) for field in ("longitude", "latitude"))
                        if not -180 <= longitude <= 180 or not -90 <= latitude <= 90:
                            raise ValueError()
                    except ValueError:
                        raise CameraError("Provide WGS 84 longitude and latitude in the supported range.") from None
                    updated = {**previous, "id": identifier, "name": values["name"], "modality": modality, "position": [longitude, latitude]}
                    if previous and previous["position"] != updated["position"]:
                        updated.pop("cameraVerifiedAt", None)
                    for field, collection_name in (("floorId", "floors"), ("groupId", "groups")):
                        if field in values:
                            updated[field] = values[field] or None
                        if updated.get(field) and updated[field] not in {item["id"] for item in inventory.get(collection_name, ())}:
                            raise CameraError(f"Unknown {field} in this site draft.")
                    if "linkedCameraId" in values:
                        updated["linkedCameraId"] = values["linkedCameraId"] or None
                        if updated["linkedCameraId"] and modality != "access":
                            raise CameraError("Only access devices can link to a camera.")
                    if "labels" in values:
                        updated["labels"] = [part.strip() for part in values["labels"].split("|") if part.strip()]
                    if "boundaryIds" in values:
                        targets = [part.strip() for part in values["boundaryIds"].split("|") if part.strip()]
                        if any(target not in boundaries or boundaries[target].get("floorId") != updated.get("floorId") for target in targets):
                            raise CameraError("Assign known boundaries on the same floor.")
                        assigned[identifier] = set(targets)
                    sensors[identifier] = updated
                except CameraError as issue:
                    errors.append({"source": "CSV", "row": line, "message": str(issue)})
        except (csv.Error, CameraError) as issue:
            errors.append({"source": "CSV", "row": 0, "message": str(issue)})
    for identifier, requested in assigned.items():
        for key, boundary in boundaries.items():
            devices = boundary["sensorIds"]
            if key in requested and identifier not in devices:
                devices.append(identifier)
            elif key not in requested and identifier in devices:
                devices.remove(identifier)
    if errors:
        return None, {"errors": errors, "changes": [], "unchanged": []}
    try:
        inventory = normalize_configuration({**inventory, "sensors": list(sensors.values()), "boundaries": list(boundaries.values())})
    except CameraError as issue:
        return None, {"errors": [{"source": "Site references", "row": 0, "message": str(issue)}], "changes": [], "unchanged": []}
    changes = configuration_diff(source or {}, inventory)
    changed = {(item["collection"], item["id"]) for item in changes}
    unchanged = [identifier for identifier in seen if ("sensors", identifier) not in changed]
    return inventory, {"errors": [], "changes": changes, "unchanged": sorted(unchanged)}


def change_import(configuration, actor, payload):
    operation = payload.get("operation")
    csv_text, geo_text = payload.get("devicesCsv", ""), payload.get("boundariesGeoJson", "")
    if (operation not in ("preview", "apply") or not isinstance(csv_text, str) or not isinstance(geo_text, str)
            or not (csv_text.strip() or geo_text.strip())):
        raise CameraError("Choose a supported import operation and at least one inventory source.")
    live = configuration.detail()
    if payload.get("draftRevision") != live["draftRevision"] or payload.get("publishedVersion") != live["publishedVersion"]:
        raise Conflict("Site configuration changed. Refresh before importing.")
    inventory, review = build_import(live["draft"] or live["published"], csv_text, geo_text)
    if operation == "apply" and inventory is not None:
        if not review["changes"]:
            return {"review": review, "configuration": live}
        result = configuration.change(actor, {**payload, "operation": "save", "configuration": inventory})
        return {"review": review, "configuration": result}
    return {"review": review, **({"inventory": inventory} if inventory is not None else {})}
