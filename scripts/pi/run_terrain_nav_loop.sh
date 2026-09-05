#!/usr/bin/env bash
set -euo pipefail

echo "The classical terrain loop is retired from the active product path. Starting learned shadow runtime instead." >&2
exec "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/run_vision_nav_shadow.sh" "$@"
