#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$repo_root"

mkdir -p data logs map_bundles transfer/outgoing

if docker compose version >/dev/null 2>&1; then
  docker compose -f docker/pi/docker-compose.yml up -d --build vision-nav-api
elif command -v docker-compose >/dev/null 2>&1; then
  docker-compose -f docker/pi/docker-compose.yml up -d --build vision-nav-api
else
  echo "Neither 'docker compose' nor 'docker-compose' is available." >&2
  exit 1
fi

cat <<EOF
Companion API container requested.

Check:
  curl http://127.0.0.1:5000/health
  curl http://127.0.0.1:5000/api/v1/device

Logs:
  docker logs -f drone-vision-nav-vision-nav-api-1
EOF
