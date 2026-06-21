#!/usr/bin/env bash
set -euo pipefail

ROOTS="${VISION_NAV_RUNTIME_STATUS_ROOTS:-$HOME/DroneTransfer/outgoing:$HOME/drone-data:$HOME/Drone}"
MAX_BYTES="${VISION_NAV_RUNTIME_STATUS_MAX_BYTES:-262144}"

python3 - "$ROOTS" "$MAX_BYTES" <<'PY'
import json
import os
import sys
from pathlib import Path

roots = [Path(item).expanduser() for item in sys.argv[1].split(os.pathsep) if item]
try:
    max_bytes = int(sys.argv[2])
except ValueError:
    max_bytes = 262144

candidates = []
for root in roots:
    if not root.exists():
        continue
    try:
        candidates.extend(path for path in root.rglob("runtime_status.json") if path.is_file())
    except OSError:
        continue

if not candidates:
    print("__VISION_NAV_RUNTIME_STATUS_MISSING__=1")
    print("No runtime_status.json was found under:")
    for root in roots:
        print(f"  {root}")
    raise SystemExit(2)

latest = max(candidates, key=lambda path: path.stat().st_mtime)
size = latest.stat().st_size
if size > max_bytes:
    print(f"__VISION_NAV_RUNTIME_STATUS__={latest}")
    print(f"Runtime status file is too large to preview safely: {size} bytes > {max_bytes} bytes")
    raise SystemExit(3)

try:
    data = json.loads(latest.read_text(encoding="utf-8"))
except Exception as exc:
    print(f"__VISION_NAV_RUNTIME_STATUS__={latest}")
    print(f"Runtime status file is not valid JSON: {exc}")
    raise SystemExit(4)

last_match = data.get("last_match") if isinstance(data.get("last_match"), dict) else {}
estimator = data.get("estimator") if isinstance(data.get("estimator"), dict) else {}
external = data.get("external_position_health") if isinstance(data.get("external_position_health"), dict) else {}
active_map = data.get("active_map") if isinstance(data.get("active_map"), dict) else {}

print(f"__VISION_NAV_RUNTIME_STATUS__={latest}")
print("__VISION_NAV_RUNTIME_STATUS_JSON__=" + json.dumps(data, separators=(",", ":"), sort_keys=True))
print("Runtime status summary:")
print(f"  map: {active_map.get('bundle_id') or active_map.get('map_id') or 'n/a'}")
print(f"  match: {last_match.get('status') or 'n/a'} reason={last_match.get('reason') or 'n/a'}")
print(f"  estimator: {estimator.get('health') or estimator.get('status') or 'n/a'}")
print(f"  external position: {external.get('status') or 'n/a'} message={external.get('message_type') or 'n/a'}")
PY
