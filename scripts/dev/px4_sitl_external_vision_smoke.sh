#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
python_bin="${VISION_NAV_PYTHON:-python3}"
endpoint="${VISION_NAV_SITL_MAVLINK_ENDPOINT:-udp:14580}"
message_type="${VISION_NAV_SITL_MAVLINK_MESSAGE:-odometry}"
rate_hz="${VISION_NAV_SITL_RATE_HZ:-5.0}"
repeat_count="${VISION_NAV_SITL_REPEAT:-6}"
out_dir="${VISION_NAV_SITL_SMOKE_DIR:-$(mktemp -d "${TMPDIR:-/tmp}/vision-nav-sitl-smoke.XXXXXX")}"
log_path="$out_dir/synthetic_external_vision.jsonl"
capture_dir="$out_dir/receiver_capture"
session_manifest="$out_dir/px4_sitl_evidence_session.json"
capture_readme="$capture_dir/README.md"
listener_capture="$capture_dir/vehicle_visual_odometry.txt"
mavlink_status_capture="$capture_dir/mavlink_status.txt"
receiver_report="$out_dir/receiver_evidence.json"
dry_run="${VISION_NAV_SITL_DRY_RUN:-0}"
evaluate_receiver="${VISION_NAV_SITL_EVALUATE_RECEIVER:-1}"

case "$message_type" in
  vision_position_estimate|odometry) ;;
  *)
    echo "Unsupported VISION_NAV_SITL_MAVLINK_MESSAGE=$message_type" >&2
    echo "Use vision_position_estimate or odometry." >&2
    exit 2
    ;;
esac

mkdir -p "$out_dir"
mkdir -p "$capture_dir"

PYTHONPATH="$repo_root/src" "$python_bin" - "$log_path" <<'PY'
from __future__ import annotations

import json
from pathlib import Path
import sys
import time

path = Path(sys.argv[1])
now_us = int(time.time() * 1_000_000)
records = []
for index in range(8):
    records.append(
        {
            "sequence": index + 1,
            "timestamp_us": now_us + index * 200_000,
            "result": {
                "status": "accepted",
                "timestamp_us": now_us + index * 200_000,
                "map_id": "px4-sitl-smoke",
                "tile_id": "synthetic",
                "confidence": 0.82,
                "measurement": {
                    "frame": "local_enu",
                    "x_m": 0.25 * index,
                    "y_m": 0.10 * index,
                    "z_m": 1.5,
                    "yaw_rad": 0.0,
                    "covariance": {
                        "x_m2": 1.5,
                        "y_m2": 1.5,
                        "z_m2": 4.0,
                        "yaw_rad2": 0.25,
                    },
                },
                "estimator": {"reset_counter": 0},
            },
        }
    )
records.append(
    {
        "sequence": len(records) + 1,
        "timestamp_us": now_us + len(records) * 200_000,
        "result": {
            "status": "rejected",
            "reason": "synthetic_rejection_should_not_send",
        },
    }
)
path.write_text("\n".join(json.dumps(record, sort_keys=True) for record in records) + "\n")
print(path)
PY

cat > "$capture_readme" <<EOF
# PX4 SITL External-Vision Receiver Capture

Start PX4 SITL separately:

\`\`\`bash
cd ~/PX4-Autopilot
make px4_sitl_default sihsim_quadx
\`\`\`

Send the synthetic stream from this repo:

\`\`\`bash
VISION_NAV_SITL_SMOKE_DIR="$out_dir" \\
VISION_NAV_SITL_MAVLINK_ENDPOINT="$endpoint" \\
VISION_NAV_SITL_MAVLINK_MESSAGE="$message_type" \\
./scripts/dev/px4_sitl_external_vision_smoke.sh
\`\`\`

In the PX4 shell or QGroundControl MAVLink console, capture:

\`\`\`text
listener vehicle_visual_odometry
listener vehicle_visual_odometry
mavlink status
\`\`\`

Save those outputs as:

\`\`\`text
$listener_capture
$mavlink_status_capture
\`\`\`

Then evaluate:

\`\`\`bash
./scripts/dev/evaluate_px4_sitl_session.sh "$out_dir"
\`\`\`
EOF

PYTHONPATH="$repo_root/src" "$python_bin" - "$session_manifest" "$log_path" "$capture_readme" "$listener_capture" "$mavlink_status_capture" "$receiver_report" "$endpoint" "$message_type" "$rate_hz" "$repeat_count" "$dry_run" <<'PY'
from __future__ import annotations

import json
from pathlib import Path
import sys
from datetime import datetime, timezone

manifest_path, log_path, readme_path, listener_path, mavlink_status_path, report_path, endpoint, message_type, rate_hz, repeat_count, dry_run = sys.argv[1:]
session = {
    "version": "0.1.0",
    "generated_at": datetime.now(timezone.utc).isoformat(),
    "endpoint": endpoint,
    "message_type": message_type,
    "rate_hz": float(rate_hz),
    "repeat_count": int(repeat_count),
    "dry_run": dry_run == "1",
    "synthetic_log": str(Path(log_path)),
    "capture_instructions": str(Path(readme_path)),
    "expected_captures": {
        "vehicle_visual_odometry": str(Path(listener_path)),
        "mavlink_status": str(Path(mavlink_status_path)),
    },
    "receiver_report": str(Path(report_path)),
    "evaluate_command": [
        "./scripts/dev/evaluate_px4_sitl_receiver_evidence.sh",
        str(Path(listener_path)),
        str(Path(mavlink_status_path)),
    ],
}
Path(manifest_path).write_text(json.dumps(session, indent=2, sort_keys=True) + "\n")
PY

cat <<EOF
PX4 external-vision SITL smoke
  endpoint:     $endpoint
  message:      $message_type
  rate_hz:      $rate_hz
  repeat:       $repeat_count
  synthetic log $log_path
  session:      $session_manifest
  capture help: $capture_readme
  report:       $receiver_report

Start PX4 SITL separately, for example:
  cd ~/PX4-Autopilot
  make px4_sitl_default sihsim_quadx

In the PX4 shell or QGroundControl MAVLink console, watch:
  listener vehicle_visual_odometry
  listener vehicle_visual_odometry
  mavlink status

Sending synthetic accepted records now. The rejected record should be skipped.
EOF

echo "__VISION_NAV_PX4_SITL_SESSION__=$out_dir"
echo "__VISION_NAV_PX4_SITL_MANIFEST__=$session_manifest"
echo "__VISION_NAV_PX4_SITL_REPORT__=$receiver_report"

if [[ "$dry_run" == "1" ]]; then
  echo "Dry run enabled; not sending MAVLink records."
  exit 0
fi

PYTHONPATH="$repo_root/src" "$python_bin" -m vision_nav.mavlink_bridge \
  --log "$log_path" \
  --endpoint "$endpoint" \
  --message-type "$message_type" \
  --rate-hz "$rate_hz" \
  --repeat "$repeat_count"

if [[ "$evaluate_receiver" == "1" && -f "$listener_capture" ]]; then
  eval_args=(
    -m vision_nav.px4_sitl_evidence
    --listener "$listener_capture"
    --expected-message "$message_type"
    --expected-rate-hz "$rate_hz"
    --json
    --allow-degraded
  )
  if [[ -f "$mavlink_status_capture" ]]; then
    eval_args+=(--mavlink-status "$mavlink_status_capture")
  fi
  PYTHONPATH="$repo_root/src" "$python_bin" "${eval_args[@]}" > "$receiver_report"
  echo "Receiver evidence report: $receiver_report"
fi
