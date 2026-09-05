#!/usr/bin/env bash
set -euo pipefail

ROOTS="${VISION_NAV_RUNTIME_STATUS_ROOTS:-$HOME/DroneTransfer/outgoing:$HOME/drone-data:$HOME/Drone}"
MAX_BYTES="${VISION_NAV_RUNTIME_STATUS_MAX_BYTES:-262144}"

python3 - "$ROOTS" "$MAX_BYTES" <<'PY'
import json
import os
import sys
import time
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
active_model = data.get("active_model") if isinstance(data.get("active_model"), dict) else {}
inference = data.get("inference") if isinstance(data.get("inference"), dict) else {}
position = data.get("position") if isinstance(data.get("position"), dict) else {}
camera = data.get("camera_health") if isinstance(data.get("camera_health"), dict) else {}
compatibility = data.get("compatibility") if isinstance(data.get("compatibility"), dict) else {}
publication = data.get("px4_publication") if isinstance(data.get("px4_publication"), dict) else {}
heartbeat = data.get("heartbeat") if isinstance(data.get("heartbeat"), dict) else {}
attitude = data.get("attitude_health") if isinstance(data.get("attitude_health"), dict) else {}
metrics = data.get("runtime_metrics") if isinstance(data.get("runtime_metrics"), dict) else {}

print(f"__VISION_NAV_RUNTIME_STATUS__={latest}")
print("__VISION_NAV_RUNTIME_STATUS_JSON__=" + json.dumps(data, separators=(",", ":"), sort_keys=True))
print("Runtime status summary:")
if data.get("schema_version") == "vision_nav_runtime_status_v3":
    candidates = position.get("top_k_candidates") if isinstance(position.get("top_k_candidates"), list) else []
    candidate = position.get("shadow_tracking_candidate") if isinstance(position.get("shadow_tracking_candidate"), dict) else {}
    print(f"  runtime: {data.get('runtime_state') or 'n/a'} shadow={data.get('shadow_mode') is True}")
    print(f"  map: {active_map.get('release_id') or active_map.get('map_id') or 'n/a'}")
    print(f"  model: {active_model.get('release_id') or 'n/a'} profile={active_model.get('profile_id') or 'n/a'}")
    print(f"  inference: provider={inference.get('provider') or 'n/a'} ready={inference.get('provider_ready')}")
    print(f"  camera: {camera.get('status') or 'n/a'}")
    print(f"  attitude: {attitude.get('status') or 'n/a'} age_ms={attitude.get('last_attitude_age_ms')}")
    if heartbeat:
        last_result = inference.get("last_completed_result") if isinstance(inference.get("last_completed_result"), dict) else {}
        print(f"  heartbeat: phase={heartbeat.get('phase')} sequence={data.get('sequence')} file_age_s={time.time() - latest.stat().st_mtime:.2f}")
        print(f"  inference activity: iteration={heartbeat.get('result_iteration')} phase_age_ms={heartbeat.get('phase_age_ms')} overdue={heartbeat.get('inference_overdue')}")
        print(f"  last completed result: iteration={last_result.get('sequence')} capture_age_ms={last_result.get('capture_age_ms')} diagnostic_age_ok={last_result.get('capture_within_diagnostic_age_limit')}")
        print(f"  status delivery: publisher_error={heartbeat.get('publisher_error')} previous_transport_error={heartbeat.get('previous_transport_error')}")
    if metrics:
        sample = metrics.get("latest") if isinstance(metrics.get("latest"), dict) else {}
        resources = sample.get("resources") if isinstance(sample.get("resources"), dict) else {}
        print(f"  metrics: {metrics.get('status')} samples={metrics.get('sample_count')} age_ms={metrics.get('sample_age_ms')}")
        print(f"  measured load: cpu_one_core_percent={sample.get('process_cpu_percent_one_core_scale')} rss_bytes={resources.get('rss_bytes')} temperature_c={resources.get('max_temperature_c')}")
        print(f"  metrics evidence: {metrics.get('path')} error={metrics.get('error')}")
    print(f"  position: {position.get('navigation_mode') or 'n/a'} source={position.get('source_state') or 'n/a'} candidates={len(candidates)}")
    print(f"  shadow candidate: {candidate.get('status') or 'not_available'}")
    print(f"  compatibility: {compatibility.get('compatible')} reasons={','.join(str(item) for item in compatibility.get('reasons') or []) or 'none'}")
    print(f"  PX4 publication: enabled={publication.get('enabled')} reason={publication.get('reason') or 'n/a'}")
else:
    print(f"  map: {active_map.get('bundle_id') or active_map.get('map_id') or 'n/a'}")
    print(f"  match: {last_match.get('status') or 'n/a'} reason={last_match.get('reason') or 'n/a'}")
    print(f"  estimator: {estimator.get('health') or estimator.get('status') or 'n/a'}")
    print(f"  external position: {external.get('status') or 'n/a'} message={external.get('message_type') or 'n/a'}")
PY
