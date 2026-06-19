#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$repo_root"

if docker compose version >/dev/null 2>&1; then
  docker compose -f docker/pi/docker-compose.yml run --rm vision-nav-pi "$@"
elif command -v docker-compose >/dev/null 2>&1; then
  docker-compose -f docker/pi/docker-compose.yml run --rm vision-nav-pi "$@"
else
  echo "Neither 'docker compose' nor 'docker-compose' is available." >&2
  exit 1
fi

