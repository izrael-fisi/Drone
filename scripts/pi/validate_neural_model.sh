#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
venv_python="${VISION_NAV_PYTHON:-$HOME/drone_vision_nav_venv/bin/python}"
model_release="${VISION_NAV_MODEL_RELEASE:-$HOME/drone-data/model_releases/pi5_crossview_nav_v1/model_release.json}"
artifact_root="${VISION_NAV_MODEL_ARTIFACT_ROOT:-$(dirname "$model_release")}"

if [[ ! -x "$venv_python" ]]; then
  echo "Missing Python venv: $venv_python" >&2
  exit 1
fi

args=(-m vision_nav.neural.artifacts --model-release "$model_release" --artifact-root "$artifact_root")
if [[ "${VISION_NAV_REQUIRE_MODEL_INTERFACES:-1}" == "1" ]]; then
  args+=(--require-interfaces)
fi
if [[ "${VISION_NAV_INSPECT_ONNX:-0}" == "1" ]]; then
  args+=(--inspect-onnx)
fi
PYTHONPATH="$repo_root/src" "$venv_python" "${args[@]}"
