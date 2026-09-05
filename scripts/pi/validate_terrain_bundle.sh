#!/usr/bin/env bash
set -euo pipefail

echo "Terrain descriptor bundles are legacy-only. Validating the learned map/model release chain." >&2
exec "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/validate_neural_runtime.sh" "$@"
