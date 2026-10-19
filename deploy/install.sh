#!/usr/bin/env bash
# Run ON THE VM after SSH:  curl/clone then:  sudo bash deploy/install.sh
# Prompts for API keys (hidden input) — keys stay on the VM in /opt/webinfo/.env (chmod 600).
set -euo pipefail
cd "$(dirname "$0")/.."
if [ ! -f .env ]; then
  read -rsp "Gemini API key #1: " G1; echo
  read -rsp "Gemini API key #2 (Enter to skip): " G2; echo
  read -rsp "Tavily API key: " TV; echo
  PW=$(head -c 18 /dev/urandom | base64 | tr -dc 'A-Za-z0-9')
  KEYS="$G1"; [ -n "$G2" ] && KEYS="$G1,$G2"
  umask 077
  sed -e "s|^GEMINI_API_KEYS=.*|GEMINI_API_KEYS=$KEYS|" \
      -e "s|^TAVILY_API_KEY=.*|TAVILY_API_KEY=$TV|" \
      -e "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=$PW|" .env.example > .env
  echo ".env written"
fi
docker compose up -d --build
echo "Waiting for API…"
for i in $(seq 1 60); do curl -fs localhost/api/health && echo && break; sleep 5; done
echo "Open: http://$(curl -fs -H 'Metadata-Flavor: Google' http://169.254.169.254/computeMetadata/v1/instance/network-interfaces/0/access-configs/0/external-ip)/"
