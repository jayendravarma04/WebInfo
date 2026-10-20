#!/usr/bin/env bash
# Rebuild after new code lands:  sudo bash deploy/update.sh
# Pulls from GitHub when /opt/webinfo is a git checkout (needs a read-only deploy key for the private repo);
# otherwise rebuilds whatever code is on disk (e.g. after a patch was applied with `git apply`).
set -euo pipefail
cd "$(dirname "$0")/.."
if [ -d .git ]; then git pull --ff-only; else echo "Not a git checkout: rebuilding the code on disk"; fi
docker compose up -d --build
docker image prune -f
