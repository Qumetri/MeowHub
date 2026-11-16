#!/bin/bash
# Real Element-Call-style call test against the live server (2026-11-16): two temporary Matrix accounts get LiveKit
# tokens through the public route (well-known -> jwt service), join the SFU from a headless browser with fake media,
# once with normal ICE and once relay-only (TURN over TLS on turn.<domain>:443 = the Russia path). Prints the ICE
# candidates and the bytes the second participant received, then deactivates the accounts.
# Needs: Node 22+ and Playwright with a Chromium (set NODE_BIN, PLAYWRIGHT_MJS and CHROME below or in the environment),
# BASE_DOMAIN from .env, and the Synapse container name in SYNAPSE (default: <STACK_NAME>-synapse or matrix_synapse).
# Run after every LiveKit / Synapse / lk-jwt / layer4 change. Note: it runs from the server itself, so it proves the
# server side, not reachability from Russia.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"; W="${TMPDIR:-/tmp}/matrix-call-test.$$"; mkdir -p "$W"; cd "$W"
set -a; . "$HERE/../../.env"; set +a
export BASE_DOMAIN MATRIX_HOST="${MATRIX_HOST:-matrix.$BASE_DOMAIN}"
SYNAPSE="${SYNAPSE:-matrix_synapse}"
[ -n "${NODE_BIN:-}" ] && export PATH="$NODE_BIN:$PATH"
python3 -c "import secrets;print(secrets.token_urlsafe(18))" > pw
SFX=_$(date +%m%d%H%M%S)   # a deactivated Matrix user id can never be registered again, so every run uses new names
cp "$HERE"/*.py "$HERE"/*.mjs .
npm init -y >/dev/null; npm i livekit-client@2 >/dev/null 2>&1
for u in calltest_a calltest_b; do docker exec "$SYNAPSE" register_new_matrix_user -c /data/homeserver.yaml -u $u$SFX -p "$(cat pw)" --no-admin http://localhost:8008 | tail -1; done
trap 'python3 cleanup.py pw tokens.json $SFX; cd /; rm -r "$W"' EXIT
python3 tok.py pw tokens.json $SFX
for m in all relay; do timeout 80 node diag.mjs "$W" $m 2>&1 | sed -E 's/([0-9]{1,3}\.){3}[0-9]{1,3}/IP/g' | grep -v -E 'twilio|^cfg' | cut -c1-200; done
timeout 100 node run.mjs "$W" relay 2>&1 | grep -E '^(A|B|ERR)' | cut -c1-600 || echo 'two-participant test printed nothing'
