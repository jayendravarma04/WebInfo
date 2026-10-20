#!/usr/bin/env bash
# Run ON THE VM:  sudo bash deploy/install.sh
# Builds and starts everything. API keys are added separately with deploy/set-keys.sh.
set -euo pipefail
cd "$(dirname "$0")/.."
if [ ! -f .env ]; then
  umask 077
  PW=$(head -c 18 /dev/urandom | base64 | tr -dc 'A-Za-z0-9')
  sed -e "s|^GEMINI_API_KEYS=.*|GEMINI_API_KEYS=|" -e "s|^TAVILY_API_KEY=.*|TAVILY_API_KEY=|" \
      -e "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=$PW|" .env.example > .env
  echo ".env created (no API keys yet — run: sudo bash deploy/set-keys.sh)"
fi
docker compose up -d --build
for i in $(seq 1 60); do curl -fs localhost/api/health && echo && break; sleep 5; done
