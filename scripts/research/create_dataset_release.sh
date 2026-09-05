#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
python_bin="${VISION_NAV_RESEARCH_PYTHON:-python3}"
output="${VISION_NAV_DATASET_RELEASE_OUTPUT:-$repo_root/data/datasets/holybro-captures-release.json}"
release_id="${VISION_NAV_DATASET_RELEASE_ID:-}"
version="${VISION_NAV_DATASET_RELEASE_VERSION:-}"
source_revision="${VISION_NAV_SOURCE_REVISION:-$(git -C "$repo_root" rev-parse HEAD)}"
license_summary="${VISION_NAV_DATASET_LICENSE_SUMMARY:-proprietary Holybro capture data; no redistribution}"

if [[ -z "$release_id" || -z "$version" || "$#" -eq 0 ]]; then
  echo "Usage: VISION_NAV_DATASET_RELEASE_ID=... VISION_NAV_DATASET_RELEASE_VERSION=... $0 /path/to/capture [more captures]" >&2
  exit 1
fi

args=(-m vision_nav.neural.dataset_release --output "$output" --release-id "$release_id" --version "$version" --source-revision "$source_revision" --license-summary "$license_summary")
for session in "$@"; do
  args+=(--session "$session")
done

PYTHONPATH="$repo_root/src" "$python_bin" "${args[@]}"
