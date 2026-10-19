#!/usr/bin/env bash
# Pull latest main and rebuild:  sudo bash deploy/update.sh
set -euo pipefail
cd "$(dirname "$0")/.."
git pull --ff-only
docker compose up -d --build
docker image prune -f
