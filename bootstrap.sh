#!/usr/bin/env bash
# meowstack bootstrap — turns a fresh checkout into a runnable stack.
#
#   cp .env.example .env    # edit BASE_DOMAIN and ACME_EMAIL
#   ./bootstrap.sh
#   docker compose up -d
#
# Idempotent by design. It fills in blank secrets, randomises placeholder
# secret paths, renders the config files that cannot read environment
# variables, and creates the directories and network the stack needs.
# Re-running it never overwrites a value you have already set.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

ENV_FILE=.env
SECRETS_DIR=./secrets

c_ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
c_new()  { printf '  \033[33m+\033[0m %s\n' "$*"; }
c_skip() { printf '  \033[90m·\033[0m %s\n' "$*"; }
die()    { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------- preflight --

[[ -f $ENV_FILE ]] || die "no .env — run: cp .env.example .env && \$EDITOR .env"
command -v docker >/dev/null || die "docker not found"
docker compose version >/dev/null 2>&1 || die "docker compose v2 plugin not found"

# Load .env with variable expansion, so DATA_ROOT=... feeding NEXTCLOUD_DATA=\${DATA_ROOT}/... works.
set -a; . "$ENV_FILE"; set +a

[[ ${BASE_DOMAIN:-} && $BASE_DOMAIN != example.com ]] \
  || die "set BASE_DOMAIN in .env (it is baked into Matrix user IDs forever — choose carefully)"
[[ ${ACME_EMAIL:-} && $ACME_EMAIL != admin@example.com ]] \
  || die "set ACME_EMAIL in .env (Let's Encrypt needs a real address)"

echo
echo "meowstack bootstrap — ${BASE_DOMAIN}"
echo

# ------------------------------------------------------------------ secrets --

# Writes KEY=value into .env only when the key is currently empty.
set_env() {
  local key=$1 val=$2
  # A line like `KEY=          # GENERATED` has no value — the comment is not one.
  if grep -qE "^${key}=[^[:space:]#]" "$ENV_FILE"; then
    c_skip "$key already set"
  else
    # Match with or without a trailing comment, keep the comment.
    if grep -qE "^${key}=" "$ENV_FILE"; then
      python3 - "$ENV_FILE" "$key" "$val" <<'PY'
import re,sys
p,k,v=sys.argv[1],sys.argv[2],sys.argv[3]
s=open(p).read()
s=re.sub(rf'^{re.escape(k)}=.*$', f"{k}={v}", s, count=1, flags=re.M)
open(p,'w').write(s)
PY
    else
      printf '%s=%s\n' "$key" "$val" >> "$ENV_FILE"
    fi
    c_new "$key generated"
  fi
}

pw()  { openssl rand -base64 "${1:-24}" | tr -d '\n=+/' | cut -c1-"${2:-24}"; }
hex() { openssl rand -hex "${1:-16}"; }

echo "secrets:"
mkdir -p "$SECRETS_DIR"; chmod 700 "$SECRETS_DIR"
set_env NEXTCLOUD_ADMIN_PASSWORD   "$(pw 24)"
set_env NEXTCLOUD_DB_PASSWORD      "$(pw 24)"
set_env NEXTCLOUD_DB_ROOT_PASSWORD "$(pw 24)"
set_env DB_PASSWORD                "$(pw 24)"
set_env MATRIX_DB_PASSWORD         "$(pw 24)"
set_env COTURN_SECRET              "$(hex 32)"
set_env SYNAPSE_REGISTRATION_SECRET "$(hex 32)"
set_env SYNAPSE_MACAROON_SECRET    "$(hex 32)"
set_env SYNAPSE_FORM_SECRET        "$(hex 32)"
set_env LIVEKIT_KEY                "key$(hex 4)"
set_env LIVEKIT_SECRET             "$(hex 32)"
# Encrypts every credential stored in n8n. Losing it does not lose the
# workflows, but every saved credential becomes unreadable.
set_env N8N_ENCRYPTION_KEY         "$(hex 32)"
# Shared secret between the crypto tracker and n8n's "explain this move"
# webhook (header X-Hook-Secret). See docs/CRYPTO.md.
set_env CRYPTO_EXPLAIN_SECRET      "$(hex 24)"
# The ESP32 sends this as X-Sensor-Token; the sensor app rejects posts without
# it. Also goes into the device's secrets.py. See docs/SENSOR.md.
set_env SENSOR_TOKEN               "$(hex 24)"

# Secret paths: the placeholder defaults are guessable, so give each a random
# suffix on first run. Once randomised they look nothing like the default and
# are left alone on re-runs.
echo
echo "secret paths:"
randomise_path() {
  local key=$1 default=$2 cur
  cur=$(grep -E "^${key}=" "$ENV_FILE" | head -1 | cut -d= -f2 | awk '{print $1}')
  if [[ -z $cur || $cur == "$default" ]]; then
    python3 - "$ENV_FILE" "$key" "${default}-$(hex 6)" <<'PY'
import re,sys
p,k,v=sys.argv[1],sys.argv[2],sys.argv[3]
s=open(p).read()
s=re.sub(rf'^{re.escape(k)}=.*$', f"{k}={v}", s, count=1, flags=re.M)
open(p,'w').write(s)
PY
    c_new "$key randomised"
  else
    c_skip "$key already customised"
  fi
}
randomise_path DASHBOARD_PATH  hub
randomise_path METUBE_PATH     dl
randomise_path MATRIXRTC_PATH  call
randomise_path AWG_ADMIN_PATH  awg
randomise_path CRYPTO_PATH     crypto
randomise_path XUI_PANEL_PATH  panel
randomise_path N8N_PUBLIC_PATH n8n
randomise_path SENSOR_PATH     sensor

# Reload so the rendering below sees everything we just wrote.
set -a; . "$ENV_FILE"; set +a

# Resolve derived values (MATRIX_HOST=matrix.${BASE_DOMAIN} and friends) into
# literal strings. Compose expands these itself, but nothing else does — not
# `docker run --env-file`, not envsubst, not a human reading the file. Writing
# them back makes .env mean the same thing to every consumer.
echo
echo "derived values:"
resolve() {
  local k=$1 v=${!1}
  grep -qE "^${k}=.*[$]{" "$ENV_FILE" || return 0
  K="$k" V="$v" F="$ENV_FILE" python3 -c '
import os, re
k, v, f = os.environ["K"], os.environ["V"], os.environ["F"]
s = open(f).read()
s = re.sub(r"^" + re.escape(k) + r"=.*$", k + "=" + v, s, count=1, flags=re.M)
open(f, "w").write(s)
'
  c_ok "$k = $v"
}
for k in CLOUD_HOST PHOTOS_HOST MATRIX_HOST TURN_HOST AWG_ENDPOINT \
         NEXTCLOUD_DATA IMMICH_UPLOAD IMMICH_DB_DATA MATRIX_MEDIA MATRIX_DB_DATA \
         METUBE_DOWNLOADS PLEX_MEDIA PHOTOS_EXTERNAL UPLOAD_LOCATION DB_DATA_LOCATION; do
  resolve "$k"
done
set -a; . "$ENV_FILE"; set +a

# VPN admin password. The bcrypt hash is deliberately NOT stored in .env: it
# starts with "$2a$", which both `source` and compose interpolation mangle. It
# lives in secrets/ and is injected into the Caddyfile at render time instead.
if [[ ! -s $SECRETS_DIR/awg-admin.hash ]]; then
  awg_pw=$(pw 24)
  printf '%s\n' "$awg_pw" > "$SECRETS_DIR/awg-admin-password"
  chmod 600 "$SECRETS_DIR/awg-admin-password"
  if docker image inspect caddy:2 >/dev/null 2>&1 || docker pull -q caddy:2 >/dev/null 2>&1; then
    docker run --rm caddy:2 caddy hash-password --plaintext "$awg_pw" \
      > "$SECRETS_DIR/awg-admin.hash"
    chmod 600 "$SECRETS_DIR/awg-admin.hash"
    c_new "VPN admin password generated (plaintext in $SECRETS_DIR/awg-admin-password)"
  else
    c_skip "could not reach caddy:2 to hash the VPN password — VPN UI will not authenticate"
  fi
else
  c_skip "VPN admin password already set"
fi

# Crypto tracker admin password. Same reasoning as above: the bcrypt hash stays
# out of .env and is injected into the Caddyfile at render time.
if [[ ! -s $SECRETS_DIR/crypto-admin.hash ]]; then
  crypto_pw=$(pw 24)
  printf '%s\n' "$crypto_pw" > "$SECRETS_DIR/crypto-admin-password"
  chmod 600 "$SECRETS_DIR/crypto-admin-password"
  if docker image inspect caddy:2 >/dev/null 2>&1 || docker pull -q caddy:2 >/dev/null 2>&1; then
    docker run --rm caddy:2 caddy hash-password --plaintext "$crypto_pw" \
      > "$SECRETS_DIR/crypto-admin.hash"
    chmod 600 "$SECRETS_DIR/crypto-admin.hash"
    c_new "crypto admin password generated (plaintext in $SECRETS_DIR/crypto-admin-password)"
  else
    c_skip "could not reach caddy:2 to hash the crypto password — the tracker UI will not authenticate"
  fi
else
  c_skip "crypto admin password already set"
fi

# ---------------------------------------------------------------- rendering --

echo
echo "rendering config (these programs cannot read env vars themselves):"

# envsubst only substitutes names we list, so a literal ${...} elsewhere in a
# config survives untouched.
VARS='$BASE_DOMAIN $CLOUD_HOST $PHOTOS_HOST $MATRIX_HOST $TURN_HOST $ACME_EMAIL
$MATRIX_DB_PASSWORD $COTURN_SECRET $SYNAPSE_REGISTRATION_SECRET $SYNAPSE_MACAROON_SECRET
$SYNAPSE_FORM_SECRET $LIVEKIT_KEY $LIVEKIT_SECRET $MATRIX_OPEN_REGISTRATION
$COTURN_PORT $COTURN_RELAY_MIN $COTURN_RELAY_MAX $LIVEKIT_TCP_PORT $LIVEKIT_UDP_PORT
$ELEMENT_BRAND $ELEMENT_COUNTRY_CODE'

render() {  # render <template> <output>   — never clobbers a newer hand edit
  local tpl=$1 out=$2
  [[ -f $tpl ]] || return 0
  if [[ -f $out && $out -nt $tpl ]]; then
    c_skip "$(basename "$out") is newer than its template, left alone"
    return 0
  fi
  envsubst "$VARS" < "$tpl" > "$out"
  c_ok "$(basename "$out")"
}

command -v envsubst >/dev/null || die "envsubst not found — install gettext (apt install gettext-base)"

render matrix/synapse/homeserver.yaml.template matrix/synapse/homeserver.yaml
render matrix/coturn/turnserver.conf.template  matrix/coturn/turnserver.conf
render matrix/livekit/livekit.yaml.template    matrix/livekit/livekit.yaml
render matrix/element/config.json.template     matrix/element/config.json

# Caddyfile: strip the blocks for profiles that are switched off, then keep
# Caddy's own {$VAR} references intact so path changes need only a restart.
echo
echo "Caddyfile (profiles: ${COMPOSE_PROFILES:-none}):"
python3 - <<'PY'
import os, re
profiles = set(p.strip() for p in os.environ.get("COMPOSE_PROFILES","").split(",") if p.strip())
# The VPN blocks key off whether the VPN projects are wanted, not a compose profile.
if os.environ.get("ENABLE_VPN","true").lower() in ("1","true","yes"):
    profiles.add("vpn")
if os.environ.get("VPN_PASSTHROUGH","false").lower() in ("1","true","yes"):
    profiles.add("vpn_passthrough")

src = open("caddy/Caddyfile.l4.template").read()
out, skip, dropped = [], None, []
for line in src.splitlines(True):
    m = re.match(r"\s*#IF:(\w+)\s*$", line)
    if m:
        skip = None if m.group(1) in profiles else m.group(1)
        if skip: dropped.append(skip)
        continue
    if re.match(r"\s*#ENDIF\s*$", line):
        skip = None
        continue
    if skip is None:
        out.append(line)
rendered = "".join(out)

# The bcrypt hash is injected here rather than passed as an env var: it begins
# with "$2a$", which compose interpolation and shell sourcing both mangle.
try:
    h = open("secrets/awg-admin.hash").read().strip()
except OSError:
    h = ""
if "@@AWG_ADMIN_HASH@@" in rendered and not h:
    print("  \033[31m!\033[0m no secrets/awg-admin.hash — the VPN UI will reject every login")
rendered = rendered.replace("@@AWG_ADMIN_HASH@@", h)
try:
    ch = open("secrets/crypto-admin.hash").read().strip()
except OSError:
    ch = ""
if "@@CRYPTO_ADMIN_HASH@@" in rendered and not ch:
    print("  \033[31m!\033[0m no secrets/crypto-admin.hash — the crypto UI will reject every login")
rendered = rendered.replace("@@CRYPTO_ADMIN_HASH@@", ch)
open("caddy/Caddyfile.l4","w").write(rendered)
kept = sorted(profiles)
print(f"  \033[32m✓\033[0m Caddyfile.l4  kept: {', '.join(kept) or 'base only'}")
if dropped:
    print(f"  \033[90m·\033[0m omitted: {', '.join(sorted(set(dropped)))}")
PY

# --------------------------------------------------------------- filesystem --

echo
echo "directories:"
mk() { if [[ -d $1 ]]; then c_skip "$1"; else mkdir -p "$1" && c_new "$1"; fi; }
mk "$DATA_ROOT"
mk "$NEXTCLOUD_DATA"
mk "$IMMICH_UPLOAD"
mk "$MATRIX_MEDIA"
mk "$MATRIX_DB_DATA"
mk "$METUBE_DOWNLOADS"
mk "$PHOTOS_EXTERNAL"
mk "$PLEX_MEDIA"; mk "$PLEX_MEDIA/Movies"; mk "$PLEX_MEDIA/TV Shows"; mk "$PLEX_MEDIA/Anime"
mk ./caddy/data
mk ./caddy/config
mk ./dashboard/dist
mk ./immich/model-cache
mk ./crypto/data
mk ./sensor/data
mk ./n8n/data
mk ./helper/data
[[ -n ${OLLAMA_MODELS:-} ]] && mk "$OLLAMA_MODELS"

# Ownership the containers expect. Nextcloud runs as www-data (uid 33); Synapse
# and MeTube run as PUID/PGID so their dirs stay host-editable.
if [[ $(id -u) -eq 0 ]]; then
  chown -R 33:33 "$NEXTCLOUD_DATA"                            && c_ok "chown $NEXTCLOUD_DATA -> 33:33 (www-data)"
  # Nextcloud writes the Plex folder, Plex reads it: owner www-data, group plex
  # (when Plex is installed), setgid so new subfolders keep the group.
  pg=$(getent group plex >/dev/null && echo plex || echo 33)
  chown -R "33:$pg" "$PLEX_MEDIA" && chmod -R 2775 "$PLEX_MEDIA" && c_ok "chown $PLEX_MEDIA -> 33:$pg, setgid"
  chown -R "${PUID:-1000}:${PGID:-1000}" "$MATRIX_MEDIA" "$METUBE_DOWNLOADS" \
                                                              && c_ok "chown matrix media + downloads -> ${PUID:-1000}"
  chown -R "${PUID:-1000}:${PGID:-1000}" ./matrix/synapse     && c_ok "chown ./matrix/synapse -> ${PUID:-1000}"
  # The crypto container runs as uid 1000; without this its sqlite db is
  # unwritable, because Docker creates a missing bind-mount source as root.
  chown -R 1000:1000 ./crypto/data                            && c_ok "chown ./crypto/data -> 1000"
  # The sensor app runs as uid 1000 (node) and owns its sqlite db.
  chown -R 1000:1000 ./sensor/data                            && c_ok "chown ./sensor/data -> 1000"
  # n8n runs as uid 1000 (node) and owns its sqlite db the same way.
  chown -R 1000:1000 ./n8n/data                               && c_ok "chown ./n8n/data -> 1000"
  # The helper bot (uid 1000) keeps its token in ./helper/data and reads the
  # generated UI passwords from ./secrets to hand them to the owner.
  chown -R 1000:1000 ./helper/data && chmod 700 ./helper/data && c_ok "chown ./helper/data -> 1000"
  chgrp 1000 "$SECRETS_DIR" "$SECRETS_DIR"/*-password 2>/dev/null \
    && chmod 750 "$SECRETS_DIR" && chmod 640 "$SECRETS_DIR"/*-password \
    && c_ok "secrets/*-password readable by the helper (group 1000)"
else
  c_skip "not root — set ownership yourself (see docs/DEPLOY.md 'Permissions')"
fi

# ------------------------------------------------------------------ network --

echo
echo "network:"
NET=${PROXY_NETWORK:-meowstack-proxy}
if docker network inspect "$NET" >/dev/null 2>&1; then
  c_skip "network $NET exists"
else
  docker network create "$NET" >/dev/null && c_new "network $NET created"
fi

# -------------------------------------------------------------- build steps --

echo
echo "build:"
if docker image inspect caddy-l4:local >/dev/null 2>&1; then
  c_skip "caddy-l4:local exists (rebuild: docker compose build caddy)"
else
  docker compose build caddy >/dev/null && c_new "caddy-l4:local built (stock Caddy + layer4 module)"
fi

# The hub cards are built from these, so the bundle is not tied to one domain.
cat > dashboard/.env.local <<EOF
VITE_BASE_DOMAIN=${BASE_DOMAIN}
VITE_HUB_NAME=${HUB_NAME:-Home Hub}
VITE_CLOUD_HOST=${CLOUD_HOST}
VITE_PHOTOS_HOST=${PHOTOS_HOST}
VITE_MATRIX_HOST=${MATRIX_HOST}
VITE_AWG_ADMIN_PATH=${AWG_ADMIN_PATH}
VITE_METUBE_PATH=${METUBE_PATH}
VITE_CRYPTO_PATH=${CRYPTO_PATH}
VITE_N8N_PATH=${N8N_PUBLIC_PATH}
VITE_SENSOR_PATH=${SENSOR_PATH}
VITE_XUI_PANEL_PORT=${XUI_PANEL_PORT}
VITE_XUI_PANEL_PATH=${XUI_PANEL_PATH}
EOF
c_ok "dashboard/.env.local"

if [[ -f dashboard/dist/index.html && dashboard/dist/index.html -nt dashboard/.env.local ]]; then
  c_skip "dashboard already built"
elif command -v npm >/dev/null; then
  ( cd dashboard && npm install --silent && npm run build --silent ) >/dev/null 2>&1 \
    && c_new "dashboard built" || c_skip "dashboard build failed — run it by hand in ./dashboard"
else
  c_skip "npm not found — hub page will 404 until you build ./dashboard"
fi

# ------------------------------------------------------------------- report --

echo
echo "───────────────────────────────────────────────────────────"
echo " Ready. Start with:   docker compose up -d"
echo
echo "   hub         https://${BASE_DOMAIN}/${DASHBOARD_PATH}/"
[[ $COMPOSE_PROFILES == *nextcloud* ]] && echo "   nextcloud   https://${CLOUD_HOST}/   (${NEXTCLOUD_ADMIN_USER:-admin})"
[[ $COMPOSE_PROFILES == *immich*    ]] && echo "   photos      https://${PHOTOS_HOST}/"
[[ $COMPOSE_PROFILES == *matrix*    ]] && echo "   chat        https://${MATRIX_HOST}/"
[[ $COMPOSE_PROFILES == *metube*    ]] && echo "   downloader  https://${BASE_DOMAIN}/${METUBE_PATH}/"
[[ $COMPOSE_PROFILES == *crypto*    ]] && echo "   crypto      https://${BASE_DOMAIN}/${CRYPTO_PATH}/"
[[ $COMPOSE_PROFILES == *n8n*       ]] && echo "   automation  https://${BASE_DOMAIN}/${N8N_PUBLIC_PATH}/"
[[ $COMPOSE_PROFILES == *helper*    ]] && [[ -z ${HELPER_BOT_TOKEN:-} ]] && echo "   bot         set HELPER_BOT_TOKEN in .env (docs/HELPER.md)"
echo
echo " Secret paths are the only access control on the hub and"
echo " downloader. Treat those URLs as passwords."
echo
echo " Passwords are in .env; the VPN admin password is also in"
echo " ./secrets/. Back both up — they are not recoverable."
echo "───────────────────────────────────────────────────────────"
