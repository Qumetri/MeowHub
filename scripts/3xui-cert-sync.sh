#!/bin/sh
# Keep the 3x-ui panel's TLS cert in sync with Caddy's auto-renewed one.
#
# The panel serves its own HTTPS on a high port and cannot renew a certificate
# itself, because Caddy owns 80/443 and ACME needs one of them. Caddy already
# holds a valid, auto-renewing cert for the domain, so this copies it into the
# path the panel reads and restarts the panel ONLY when the cert changed.
#
# Install (as the user who owns the stack — docker only, no sudo):
#   crontab -e
#   17 4 * * * /path/to/meowstack/scripts/3xui-cert-sync.sh >> /path/to/meowstack/scripts/cert-sync.log 2>&1
#
# The cert is issued for the DOMAIN, so the panel must be reached by hostname
# — https://<domain>:<port>/<path>/ — not by raw IP, or it will not validate.
set -eu

ENV_FILE="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)/.env"
[ -f "$ENV_FILE" ] || { echo "no .env at $ENV_FILE"; exit 1; }
# shellcheck disable=SC1090
. "$ENV_FILE"

DOMAIN=${BASE_DOMAIN:?BASE_DOMAIN not set in .env}
CADDY_CT=${STACK_NAME:-meowstack}_caddy
XUI_CT=${XUI_PROJECT_NAME:-meowstack-xui}_panel
PANEL_DIR=/root/cert/ip          # must match webCertFile/webKeyFile in x-ui.db

# The ACME directory name can change, so find the cert rather than hardcode it.
SRC_CRT=$(docker exec "$CADDY_CT" sh -c "find /data -name '${DOMAIN}.crt' -path '*certificates*' | head -1")
SRC_KEY=$(docker exec "$CADDY_CT" sh -c "find /data -name '${DOMAIN}.key' -path '*certificates*' | head -1")
[ -n "$SRC_CRT" ] && [ -n "$SRC_KEY" ] || { echo "caddy cert for ${DOMAIN} not found"; exit 1; }

NEW=$(docker exec "$CADDY_CT" cat "$SRC_CRT")
CUR=$(docker exec "$XUI_CT" cat "${PANEL_DIR}/fullchain.pem" 2>/dev/null || true)

[ "$NEW" = "$CUR" ] && exit 0   # unchanged — don't restart the panel for nothing

echo "$(date -Is) 3x-ui cert differs from Caddy's — updating"
docker exec "$CADDY_CT" cat "$SRC_CRT" | docker exec -i "$XUI_CT" sh -c "cat > ${PANEL_DIR}/fullchain.pem"
docker exec "$CADDY_CT" cat "$SRC_KEY" | docker exec -i "$XUI_CT" sh -c "cat > ${PANEL_DIR}/privkey.pem"
docker exec "$XUI_CT" x-ui restart
echo "$(date -Is) 3x-ui cert updated and panel restarted"
