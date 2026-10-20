#!/usr/bin/env bash
# Run ON THE VM:  sudo bash /opt/webinfo/deploy/set-keys.sh
# Hidden prompts; keys are written only to /opt/webinfo/.env (mode 600), then the API restarts.
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] || { echo "Run deploy/install.sh first"; exit 1; }
read -rsp "Gemini API key #1: " G1; echo
read -rsp "Gemini API key #2 (Enter to skip): " G2; echo
read -rsp "Tavily API key: " TV; echo
[ -n "$G1" ] && [ -n "$TV" ] || { echo "Gemini key #1 and Tavily key are required"; exit 1; }
KEYS="$G1"; [ -n "$G2" ] && KEYS="$G1,$G2"
umask 077
python3 - "$KEYS" "$TV" <<'PY'
import re, sys
keys, tv = sys.argv[1], sys.argv[2]
s = open(".env").read()
s = re.sub(r"(?m)^GEMINI_API_KEYS=.*$", lambda m: "GEMINI_API_KEYS=" + keys, s)
s = re.sub(r"(?m)^TAVILY_API_KEY=.*$", lambda m: "TAVILY_API_KEY=" + tv, s)
open(".env", "w").write(s)
PY
chmod 600 .env
docker compose up -d --force-recreate api
echo "Restarting API…"; sleep 8
for i in $(seq 1 30); do curl -fs localhost/api/health && echo && break; sleep 3; done
