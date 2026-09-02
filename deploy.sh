#!/usr/bin/env bash
#
# Deploy the server (Fly) and the client (Cloudflare Workers).
#
# Order matters. The client bundle bakes in VITE_SERVER_URL at build time and
# the server's ALLOWED_ORIGINS names the client's hostname, so the server has to
# be able to accept the new client before that client exists.
#
# Deploying replaces the single Fly machine, and rooms live in its memory: every
# game in progress ends. Clients now retry and then say so, but there is no way
# to make this seamless without persisting rooms or running two machines.
set -euo pipefail
cd "$(dirname "$0")"

step() { printf '\n== %s ==\n' "$1"; }

step "protocol version"
py_ver=$(sed -n 's/^VERSION = \([0-9]\+\)$/\1/p' shared/protocol.py)
ts_ver=$(sed -n 's/^export const VERSION = \([0-9]\+\);$/\1/p' web/src/protocol.ts)
if [ -z "$py_ver" ] || [ "$py_ver" != "$ts_ver" ]; then
  echo "protocol VERSION mismatch: python='$py_ver' typescript='$ts_ver'" >&2
  exit 1
fi
echo "both at $py_ver"

step "typecheck"
(cd web && npm run typecheck)

step "geometry parity"
python3 tools/parity_test.py

step "server"
# --ha=false: a second machine would strand the two halves of a game.
fly deploy --remote-only --ha=false

step "client"
(cd web && npx wrangler deploy)

printf '\nDeployed. Open tabs still hold the old bundle until reloaded.\n'
