#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
python_bin="${VISION_NAV_PYTHON:-python3}"
endpoint="${VISION_NAV_SITL_MAVLINK_ENDPOINT:-udp:14550}"
message_type="${VISION_NAV_SITL_MAVLINK_MESSAGE:-odometry}"
rate_hz="${VISION_NAV_SITL_RATE_HZ:-5.0}"
repeat_count="${VISION_NAV_SITL_REPEAT:-6}"
out_dir="${VISION_NAV_SITL_SMOKE_DIR:-$(mktemp -d "${TMPDIR:-/tmp}/vision-nav-sitl-smoke.XXXXXX")}"
log_path="$out_dir/synthetic_external_vision.jsonl"

case "$message_type" in
  vision_position_estimate|odometry) ;;
  *)
    echo "Unsupported VISION_NAV_SITL_MAVLINK_MESSAGE=$message_type" >&2
    echo "Use vision_position_estimate or odometry." >&2
    exit 2
    ;;
esac

mkdir -p "$out_dir"

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

cat <<EOF
PX4 external-vision SITL smoke
  endpoint:     $endpoint
  message:      $message_type
  rate_hz:      $rate_hz
  repeat:       $repeat_count
  synthetic log $log_path

Start PX4 SITL separately, for example:
  cd ~/PX4-Autopilot
  make px4_sitl gz_x500

In the PX4 shell or QGroundControl MAVLink console, watch:
  listener vehicle_visual_odometry 5
  mavlink status

Sending synthetic accepted records now. The rejected record should be skipped.
EOF

PYTHONPATH="$repo_root/src" "$python_bin" -m vision_nav.mavlink_bridge \
  --log "$log_path" \
  --endpoint "$endpoint" \
  --message-type "$message_type" \
  --rate-hz "$rate_hz" \
  --repeat "$repeat_count"
