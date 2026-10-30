#!/usr/bin/env bash
# Deploy (or update) the OSINT platform on a fresh Ubuntu/Debian server.
#
#   scripts/deploy.sh root@203.0.113.10
#
# Ships the committed code with `git archive` (the server needs no GitHub
# access), copies your local .env, installs Docker if missing, opens only
# 22/80/443, and runs `docker compose up -d --build`. Safe to re-run for
# updates. See docs/09-deployment.md.
set -euo pipefail

host="${1:?usage: scripts/deploy.sh user@server}"
dir="/opt/osint-platform"
root="$(git -C "$(dirname "$0")/.." rev-parse --show-toplevel)"
cd "$root"

[ -f .env ] || { echo "no .env here: copy .env.example to .env and fill it in" >&2; exit 1; }
for var in OSINT_DOMAIN OSINT_BASIC_AUTH_USER OSINT_BASIC_AUTH_HASH SEARXNG_SECRET; do
  grep -Eq "^${var}=.+" .env || { echo ".env is missing ${var}" >&2; exit 1; }
done
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo "note: uncommitted changes are NOT deployed (git archive ships HEAD $(git rev-parse --short HEAD))"
fi

echo "==> preparing ${host}"
ssh "$host" "bash -s" <<'REMOTE'
set -euo pipefail
if ! command -v docker >/dev/null; then
  curl -fsSL https://get.docker.com | sh
fi
if command -v ufw >/dev/null; then
  ufw allow 22/tcp >/dev/null; ufw allow 80/tcp >/dev/null
  ufw allow 443/tcp >/dev/null; ufw allow 443/udp >/dev/null
  yes | ufw enable >/dev/null || true
fi
mkdir -p /opt/osint-platform
REMOTE

echo "==> shipping $(git rev-parse --short HEAD)"
git archive --format=tar HEAD | ssh "$host" "tar -x -C ${dir}"
scp -q .env "${host}:${dir}/.env"
ssh "$host" "chmod 600 ${dir}/.env"

echo "==> building and starting"
ssh "$host" "cd ${dir} && docker compose up -d --build && docker compose ps"
domain="$(grep -E '^OSINT_DOMAIN=' .env | cut -d= -f2)"
echo "==> done: https://${domain} (first start fetches a TLS certificate; give it a minute)"
