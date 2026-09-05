#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
config="${1:-$repo_root/research/configs/teacher.yaml}"
experiment_dir="${VISION_NAV_TEACHER_EXPERIMENT_DIR:-}"
map_pairs_dir="${VISION_NAV_TRAINING_MAP_PAIRS_DIR:-}"
temporal_windows_dir="${VISION_NAV_TEMPORAL_WINDOWS_DIR:-}"
upstream_workflow="${VISION_NAV_UPSTREAM_WORKFLOW:-}"

if [[ -n "$experiment_dir" ]]; then
  test -f "$experiment_dir/manifest.json" || {
    echo "Teacher experiment manifest is missing: $experiment_dir/manifest.json" >&2
    echo "Run ./scripts/research/prepare_teacher_experiment.sh first." >&2
    exit 1
  }
fi
if [[ -n "$map_pairs_dir" ]]; then
  VISION_NAV_REQUIRE_HEADING_LABELS="${VISION_NAV_REQUIRE_HEADING_LABELS:-1}" \
  VISION_NAV_REQUIRE_POSE_CONTEXT="${VISION_NAV_REQUIRE_POSE_CONTEXT:-1}" \
    "$repo_root/scripts/research/validate_teacher_dataset.sh" "$map_pairs_dir" >/dev/null
fi
if [[ -n "$temporal_windows_dir" ]]; then
  "$repo_root/scripts/research/validate_temporal_training_windows.sh" "$temporal_windows_dir" >/dev/null
fi
if [[ -n "$upstream_workflow" ]]; then
  "$repo_root/scripts/research/preflight_upstream_training.sh" \
    "$upstream_workflow" \
    "${VISION_NAV_UPSTREAM_CHECKOUT:?Set VISION_NAV_UPSTREAM_CHECKOUT for an immutable upstream checkout.}" \
    "${VISION_NAV_UPSTREAM_DATASET_ROOT:?Set VISION_NAV_UPSTREAM_DATASET_ROOT for the workflow dataset.}" >/dev/null
fi

cat <<EOF
Teacher training is intentionally launched from a CUDA research environment.
Config: $config

There are two deliberately separate input tracks:

1. Official upstream reproductions use their upstream-specific dataset layout.
   Materialize the exact stage checkout and preflight it before following that
   project's documented command. Video2BEV's first and second stages are
   distinct immutable workflows; the second stage requires an explicit first
   stage checkpoint.

   PYTHONPATH=src python -m vision_nav.neural.upstreams \
     --lock research/upstreams.lock.json \
     --materialize-workflow video2bev_first_stage_v1 \
     --workflow-dir research/vendor/workflows

   VISION_NAV_UPSTREAM_WORKFLOW=video2bev_first_stage_v1 \
   VISION_NAV_UPSTREAM_CHECKOUT=research/vendor/workflows/video2bev_first_stage_v1 \
   VISION_NAV_UPSTREAM_DATASET_ROOT=/data/univ \
   ./scripts/research/preflight_upstream_training.sh

2. Proprietary Holybro captures use the project's audited map-pair contract.
   Before starting a project-owned teacher or XFeat adapter, require an
   immutable DatasetRelease, a passing training-index readiness report,
   offline-only ENU/heading labels, and a capture-session-disjoint input package:

  ./scripts/research/create_training_splits.sh \
    /data/training-index/<release> /data/training-splits/<experiment>

  ./scripts/research/prepare_teacher_experiment.sh \
    /data/training-splits/<experiment> /data/training-labels/<release> \
    /data/teacher-experiments/<experiment>

  ./scripts/research/validate_teacher_dataset.sh \
    /data/training-map-pairs/<experiment>

  ./scripts/research/build_temporal_training_windows.sh \
    /data/training-map-pairs/<experiment> /data/temporal-windows/<experiment>

  VISION_NAV_TEMPORAL_WINDOWS_DIR=/data/temporal-windows/<experiment> \
    ./scripts/research/train_teacher.sh

Set MLFLOW_TRACKING_URI and the DVC remote before starting a run. Record the
exact upstream revision, official/public protocol used, and teacher-input
manifest hash in the resulting ModelRelease. MegaLoc is a pinned pretrained
retrieval baseline, not a generic fine-tuning target. This script does not
download licensed datasets, launch a trainer, or pretend to reproduce a paper
without its official protocol.
EOF
