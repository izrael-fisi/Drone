#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
venv_python="${VISION_NAV_PYTHON:-$HOME/drone_vision_nav_venv/bin/python}"
map_release="${VISION_NAV_MAP_RELEASE:-$HOME/drone-data/map_releases/active/map_release.json}"
model_release="${VISION_NAV_MODEL_RELEASE:-$HOME/drone-data/model_releases/pi5_crossview_nav_v1/model_release.json}"
calibration_id="${VISION_NAV_CALIBRATION_ID:-down-camera-v1}"
provider="${VISION_NAV_INFERENCE_PROVIDER:-onnxruntime_cpu}"
model_artifact_root="${VISION_NAV_MODEL_ARTIFACT_ROOT:-}"

"$repo_root/scripts/pi/validate_learned_map_assets.sh" "$map_release"

args=(
  --map-release "$map_release"
  --model-release "$model_release"
  --calibration-id "$calibration_id"
  --provider "$provider"
  --output-dir /tmp/drone-vision-nav-validation
  --once
)
if [[ -n "$model_artifact_root" ]]; then
  args+=(--model-artifact-root "$model_artifact_root")
fi

PYTHONPATH="$repo_root/src" "$venv_python" -m vision_nav.neural.shadow_runtime \
  "${args[@]}"
