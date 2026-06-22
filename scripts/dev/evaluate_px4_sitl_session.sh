#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
python_bin="${VISION_NAV_PYTHON:-python3}"
session_path="${1:-}"

if [[ -z "$session_path" ]]; then
  cat >&2 <<EOF
Usage:
  $0 /path/to/px4-sitl-evidence-session-dir
  $0 /path/to/px4_sitl_evidence_session.json

The session folder is created by:
  VISION_NAV_SITL_SMOKE_DIR=/path/to/session ./scripts/dev/px4_sitl_external_vision_smoke.sh
EOF
  exit 2
fi

args=(
  -m vision_nav.px4_sitl_session
  --session "$session_path"
)

if [[ -n "${VISION_NAV_PX4_SITL_SESSION_REPORT:-}" ]]; then
  args+=(--output "$VISION_NAV_PX4_SITL_SESSION_REPORT")
fi
if [[ -n "${VISION_NAV_PX4_SITL_MIN_SAMPLES:-}" ]]; then
  args+=(--min-samples "$VISION_NAV_PX4_SITL_MIN_SAMPLES")
fi
if [[ -n "${VISION_NAV_PX4_SITL_MAX_SAMPLE_AGE_S:-}" ]]; then
  args+=(--max-sample-age-s "$VISION_NAV_PX4_SITL_MAX_SAMPLE_AGE_S")
fi
if [[ -n "${VISION_NAV_SITL_RATE_HZ:-}" ]]; then
  args+=(--expected-rate-hz "$VISION_NAV_SITL_RATE_HZ")
fi
if [[ -n "${VISION_NAV_PX4_SITL_MIN_RATE_RATIO:-}" ]]; then
  args+=(--min-rate-ratio "$VISION_NAV_PX4_SITL_MIN_RATE_RATIO")
fi
if [[ "${VISION_NAV_ALLOW_DEGRADED:-0}" == "1" ]]; then
  args+=(--allow-degraded)
fi

PYTHONPATH="$repo_root/src" "$python_bin" "${args[@]}"
