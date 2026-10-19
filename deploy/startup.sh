#!/usr/bin/env bash
# GCE startup script — runs as root on every boot. Idempotent.
set -eux
# 2 GB swap: headless Chromium + Postgres + API on 1 GB RAM needs it.
if ! swapon --show | grep -q /swapfile; then
  [ -f /swapfile ] || { fallocate -l 2G /swapfile; chmod 600 /swapfile; mkswap /swapfile; }
  swapon /swapfile
  grep -q /swapfile /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
  sysctl -w vm.swappiness=20
fi
if ! command -v docker >/dev/null; then
  apt-get update
  apt-get install -y ca-certificates curl git
  curl -fsSL https://get.docker.com | sh
  systemctl enable --now docker
fi
# Keep logs/images from slowly filling the free 30 GB disk
cat > /etc/docker/daemon.json <<'J'
{"log-driver":"json-file","log-opts":{"max-size":"10m","max-file":"3"}}
J
systemctl restart docker
[ -d /opt/webinfo ] && (cd /opt/webinfo && docker compose up -d) || true
