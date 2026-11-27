#!/usr/bin/env python3
"""Replay completed inspection cycles into an enrolled loopback site gateway."""

import argparse
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services/camera-gateway"))
from inspection_results import EVENT_TYPE, normalize_inspection
from onvif import CameraError, NoRedirect
from site_activity import valid_event_device

FIXTURE = ROOT / "tests/fixtures/industry-inspection-cycles.json"
OUTCOMES = {1: "pass", 2: "fail"}
INCOMPLETE = {0, 3, 255}


def events(fixture, camera, run_id, start):
    station, rows = fixture["stationId"], fixture["cycles"]
    if not valid_event_device(camera) or not valid_event_device(run_id) or not valid_event_device(station):
        raise ValueError("Use valid camera, station and run IDs.")
    if start.tzinfo is None or not isinstance(rows, list) or not 1 <= len(rows) <= 1000:
        raise ValueError("Use a timezone-aware start and from 1 to 1,000 fixture cycles.")
    completed, skipped = [], 0
    for row in rows:
        step, identifier, status = (row[key] for key in ("elapsedSeconds", "resultId", "statusCode"))
        if type(step) is not int or not 0 <= step <= 3600 or not valid_event_device(identifier) or type(status) is not int:
            raise ValueError("Each fixture cycle requires a result ID, time offset and integer status.")
        if status in INCOMPLETE:
            skipped += 1
            continue
        if status not in OUTCOMES:
            raise ValueError("Unrecognized inspection cycle status.")
        inspection = normalize_inspection({"stationId": station, "source": "simulated", "outcome": OUTCOMES[status],
                                          **{key: row[key] for key in ("recipeId", "partId", "lotId", "componentId") if key in row}})
        identifier = sha256(json.dumps([run_id, station, identifier], separators=(",", ":")).encode()).hexdigest()
        completed.append({"id": "inspection-" + identifier, "sensorId": camera, "type": "observation",
                          "eventType": EVENT_TYPE, "occurredAt": (start + timedelta(seconds=step)).astimezone(timezone.utc).isoformat(),
                          "inspection": inspection})
    return completed, skipped


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, default=FIXTURE)
    parser.add_argument("--gateway", default="http://127.0.0.1:8765")
    parser.add_argument("--camera", required=True)
    parser.add_argument("--token-file", type=Path, required=True)
    parser.add_argument("--run-id", required=True, help="Reuse this identifier to retry the same set of station results.")
    parser.add_argument("--start-at", required=True, help="First simulated timestamp with a timezone, after the station was published.")
    args = parser.parse_args()
    try:
        gateway = urlsplit(args.gateway)
        if (gateway.scheme != "http" or gateway.hostname not in ("localhost", "127.0.0.1", "::1") or gateway.username is not None
                or gateway.path not in ("", "/") or gateway.fragment or gateway.query):
            raise ValueError("Use the local loopback gateway for this simulator.")
        token = args.token_file.read_text().strip()
        if not re.fullmatch(r"[a-f0-9]{64}", token):
            raise ValueError("Use the enrolled camera adapter token.")
        rows, skipped = events(json.loads(args.fixture.read_text()), args.camera, args.run_id, datetime.fromisoformat(args.start_at))
        opener = build_opener(ProxyHandler({}), NoRedirect())
        accepted = 0
        for item in rows:
            request = Request(args.gateway.rstrip("/") + "/api/site/telemetry", json.dumps(item).encode(),
                              headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
            with opener.open(request, timeout=5) as response:
                accepted += json.load(response)["accepted"] is True
        print(json.dumps({"accepted": accepted, "duplicates": len(rows) - accepted, "skippedIncomplete": skipped}))
    except (CameraError, KeyError, TypeError, ValueError, OSError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
