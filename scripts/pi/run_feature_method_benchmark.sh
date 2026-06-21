#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
venv_python="${VISION_NAV_PYTHON:-$HOME/drone_vision_nav_venv/bin/python}"
bundle="${VISION_NAV_FEATURE_BENCH_BUNDLE:-${VISION_NAV_BUNDLE:-$HOME/drone-data/map_bundles/mission_bundle}}"
replay_log="${VISION_NAV_FEATURE_BENCH_REPLAY_LOG:-$HOME/DroneTransfer/outgoing/terrain-match/terrain_matches.jsonl}"
output_dir="${VISION_NAV_FEATURE_METHOD_BENCHMARK:-$HOME/DroneTransfer/outgoing/feature-method-bench}"
case_name="${VISION_NAV_FEATURE_BENCH_CASE_NAME:-feature-method-benchmark}"
expected="${VISION_NAV_FEATURE_BENCH_EXPECTED:-good_map}"
methods="${VISION_NAV_FEATURE_BENCH_METHODS:-orb,akaze,sift,neural}"
max_features="${VISION_NAV_FEATURE_BENCH_MAX_FEATURES:-3000}"
ratio="${VISION_NAV_FEATURE_BENCH_RATIO:-0.75}"
min_inliers="${VISION_NAV_FEATURE_BENCH_MIN_INLIERS:-18}"
ransac_threshold="${VISION_NAV_FEATURE_BENCH_RANSAC_THRESHOLD:-4.0}"
max_candidates="${VISION_NAV_FEATURE_BENCH_MAX_CANDIDATES:-64}"
search_radius_m="${VISION_NAV_FEATURE_BENCH_SEARCH_RADIUS_M:-80.0}"
camera_calibration="${VISION_NAV_FEATURE_BENCH_CAMERA_CALIBRATION:-}"
allow_failed="${VISION_NAV_FEATURE_BENCH_ALLOW_FAILED:-0}"

if [[ "$venv_python" == */* ]]; then
  if [[ ! -x "$venv_python" ]]; then
    echo "Missing Python venv: $venv_python" >&2
    echo "Run ./scripts/pi/bootstrap_pi5.sh first, then reboot." >&2
    exit 1
  fi
elif ! command -v "$venv_python" >/dev/null 2>&1; then
  echo "Python command not found: $venv_python" >&2
  echo "Run ./scripts/pi/bootstrap_pi5.sh first, then reboot, or set VISION_NAV_PYTHON." >&2
  exit 1
fi

if [[ ! -e "$bundle" ]]; then
  echo "Missing terrain bundle: $bundle" >&2
  echo "Set VISION_NAV_FEATURE_BENCH_BUNDLE or VISION_NAV_BUNDLE." >&2
  exit 1
fi

if [[ ! -f "$replay_log" ]]; then
  echo "Missing replay/runtime log: $replay_log" >&2
  echo "Run ./scripts/pi/run_terrain_nav_loop.sh or set VISION_NAV_FEATURE_BENCH_REPLAY_LOG." >&2
  exit 1
fi

mkdir -p "$output_dir"
safe_case="$(printf '%s' "$case_name" | tr -cs '[:alnum:]_.-' '-' | sed 's/^-//; s/-$//')"
if [[ -z "$safe_case" ]]; then
  safe_case="feature-method-benchmark"
fi
report="$output_dir/${safe_case}.json"

args=(
  -m vision_nav.feature_method_benchmark
  --bundle "$bundle"
  --replay-log "$replay_log"
  --methods "$methods"
  --expected "$expected"
  --case-name "$case_name"
  --output-dir "$output_dir"
  --output "$report"
  --max-features "$max_features"
  --ratio "$ratio"
  --min-inliers "$min_inliers"
  --ransac-threshold "$ransac_threshold"
  --max-candidates "$max_candidates"
  --search-radius-m "$search_radius_m"
)

if [[ -n "$camera_calibration" ]]; then
  args+=(--camera-calibration "$camera_calibration")
fi

set +e
PYTHONPATH="$repo_root/src" "$venv_python" "${args[@]}"
benchmark_status=$?
set -e

cat <<EOF

Feature-method benchmark outputs:
  report: $report
  output: $output_dir

The support-bundle wrapper auto-includes this directory when present:
  ./scripts/pi/create_support_bundle.sh
EOF

echo "__VISION_NAV_FEATURE_METHOD_REPORT__=$report"

if [[ "$benchmark_status" -ne 0 ]]; then
  echo
  echo "Feature-method benchmark did not pass. Review the report before using it as readiness evidence." >&2
  if [[ "$allow_failed" != "1" && "$allow_failed" != "true" ]]; then
    exit "$benchmark_status"
  fi
fi
