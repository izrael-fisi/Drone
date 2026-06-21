#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
venv_python="${VISION_NAV_PYTHON:-$HOME/drone_vision_nav_venv/bin/python}"
python_bin="$venv_python"
endpoint="${VISION_NAV_MAVLINK_ENDPOINT:-${1:-serial:/dev/ttyAMA0:921600}}"
probe="${VISION_NAV_MAVLINK_PROBE:-0}"

if [[ ! -x "$python_bin" ]]; then
  python_bin="$(command -v python3 || true)"
fi
if [[ -z "$python_bin" ]]; then
  echo "[FAIL] No Python interpreter found." >&2
  exit 1
fi

PYTHONPATH="$repo_root/src" "$python_bin" - "$endpoint" "$probe" <<'PY'
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import time

from vision_nav.mavlink_bridge import parse_mavlink_endpoint

endpoint = sys.argv[1]
probe = sys.argv[2] == "1"

try:
    conn_str, baud = parse_mavlink_endpoint(endpoint)
except Exception as exc:
    print(f"[FAIL] Invalid MAVLink endpoint: {exc}", file=sys.stderr)
    raise SystemExit(1)

summary = {
    "endpoint": endpoint,
    "connection": conn_str,
    "baud": baud,
    "probe_enabled": probe,
}
print("[OK] MAVLink endpoint syntax is valid")
print(json.dumps(summary, indent=2))

if baud is not None:
    device = Path(conn_str)
    if not device.exists():
        print(f"[FAIL] Serial device does not exist: {device}", file=sys.stderr)
        raise SystemExit(1)
    if not os.access(device, os.R_OK | os.W_OK):
        print(f"[FAIL] Serial device is not readable/writable by this user: {device}", file=sys.stderr)
        print("Add the user to dialout/tty as appropriate, then log out/in or reboot.", file=sys.stderr)
        raise SystemExit(1)
    print(f"[OK] Serial device exists and is readable/writable: {device}")

if not probe:
    print("[WARN] Live MAVLink heartbeat probe skipped. Set VISION_NAV_MAVLINK_PROBE=1 to require telemetry.")
    raise SystemExit(0)

try:
    from pymavlink import mavutil
except Exception as exc:
    print(f"[FAIL] pymavlink is required for live MAVLink probing: {exc}", file=sys.stderr)
    raise SystemExit(1)

kwargs = {"source_system": 42, "source_component": 197}
if baud is not None:
    kwargs["baud"] = baud
conn = mavutil.mavlink_connection(conn_str, **kwargs)
deadline = time.monotonic() + 5.0
message = None
while time.monotonic() < deadline:
    message = conn.recv_match(type=["HEARTBEAT", "ATTITUDE", "LOCAL_POSITION_NED"], blocking=True, timeout=0.5)
    if message is not None:
        break
conn.close()

if message is None:
    print("[FAIL] No MAVLink heartbeat/attitude/local-position telemetry received within 5 seconds.", file=sys.stderr)
    raise SystemExit(1)

print(f"[OK] Received MAVLink telemetry: {message.get_type()}")
PY
