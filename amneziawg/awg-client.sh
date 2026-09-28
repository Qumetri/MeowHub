#!/usr/bin/env bash
# AmneziaWG client management.  Usage:
#   ./awg-client.sh add <name>      create a peer, hot-apply it, print the config
#   ./awg-client.sh list            list peers with last-handshake / transfer
#   ./awg-client.sh show <name>     re-print an existing client config
#   ./awg-client.sh remove <name>   revoke a peer (hot, no tunnel restart)
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONF="$DIR/config/awg0.conf"
CLIENTS="$DIR/clients"
IFACE=awg0
die() { echo "error: $*" >&2; exit 1; }

# Settings come from the main .env (project name, image pin) and this project's
# params.env (keys, server-side obfuscation). Both are optional at read time so
# `list` still works before init.
[[ -f "$DIR/../.env" ]] && { set -a; . "$DIR/../.env"; set +a; }
[[ -f "$DIR/config/params.env" ]] || die "no config/params.env — run ./awg-init.sh first"
set -a; . "$DIR/config/params.env"; set +a
CT=${AWG_PROJECT_NAME:-meowstack-awg}_tunnel
IMG=${AWG_IMAGE:-amneziavpn/amneziawg-go:3.1.20260828}

# The web UI mutates the same file. Take the same advisory lock it does.
lock() { exec 9>>"$CONF"; flock 9; }
# peer blocks are headed by "# client: <name>  (added ...)" — match name as a whole word
has_peer()  { grep -qE "^# client: $1([[:space:]]|\$)" "$CONF"; }
peer_field() { awk -v n="$1" -v k="$2" '$0 ~ "^# client: "n"([[:space:]]|$)" {f=1; next} f && $1==k {print $3; exit}' "$CONF"; }
awgtool() { docker run --rm -i --entrypoint awg "$IMG" "$@"; }
live()    { docker ps --format '{{.Names}}' | grep -qx "$CT"; }
inct()    { docker exec "$CT" "$@"; }

next_ip() {
  local base="${AWG_SERVER_IP%.*}" n
  for n in $(seq 2 254); do
    grep -q "AllowedIPs *= *$base.$n/32" "$CONF" || { echo "$base.$n"; return; }
  done
  die "no free address left in $AWG_SUBNET"
}

cmd_add() {
  lock
  local name="${1:-}"
  [[ "$name" =~ ^[a-zA-Z0-9_-]{1,32}$ ]] || die "name must be 1-32 chars of [a-zA-Z0-9_-]"
  [[ -e "$CLIENTS/$name.conf" ]] && die "client '$name' already exists"
  has_peer "$name" && die "client '$name' already in awg0.conf"

  local priv pub psk ip
  priv=$(awgtool genkey </dev/null)
  pub=$(printf '%s' "$priv" | awgtool pubkey)
  psk=$(awgtool genpsk </dev/null)
  ip=$(next_ip)

  cat >> "$CONF" <<EOF

# client: $name  (added $(date -u +%Y-%m-%dT%H:%M:%SZ))
[Peer]
PublicKey    = $pub
PresharedKey = $psk
AllowedIPs   = $ip/32
EOF

  # AWG 3.1 client-side obfuscation, drawn fresh per client. Shared with the web UI via
  # config/awg31.py so the two generators can't drift.
  local o31 ka31
  o31=$(python3 "$DIR/config/awg31.py")
  ka31=$(printf '%s' "$o31" | sed -n 's/^__KEEPALIVE__=//p')
  o31=$(printf '%s' "$o31" | grep -v '^__KEEPALIVE__=')

  umask 077
  cat > "$CLIENTS/$name.conf" <<EOF
# AmneziaWG client "$name" — import into the AmneziaWG app (NOT stock WireGuard).
# Requires app v3.0.1 (2026-07-24) or newer: older builds reject the I1/I2 lines.
# S1-S4 / H1-H4 must stay exactly as generated; changing one byte breaks the handshake.
# Everything below them is client-side only — it may differ per client, and the server
# does not need it. See amneziawg.md "Obfuscation".
[Interface]
PrivateKey = $priv
Address    = $ip/32
DNS        = 1.1.1.1, 8.8.8.8
MTU        = $AWG_MTU
S1   = $S1
S2   = $S2
S3   = $S3
S4   = $S4
H1   = $H1
H2   = $H2
H3   = $H3
H4   = $H4
$o31

[Peer]
PublicKey           = $SERVER_PUBKEY
PresharedKey        = $psk
Endpoint            = $AWG_ENDPOINT:$AWG_PORT
AllowedIPs          = 0.0.0.0/0, ::/0
PersistentKeepalive = $ka31
EOF

  if live; then
    inct sh -c "umask 077; printf '%s' '$psk' > /tmp/psk.$$; \
      awg set $IFACE peer '$pub' preshared-key /tmp/psk.$$ allowed-ips '$ip/32'; \
      rm -f /tmp/psk.$$" >/dev/null
    echo "peer '$name' ($ip) added and applied live."
  else
    echo "peer '$name' ($ip) written; container is down, it will load on next start."
  fi
  echo
  cmd_show "$name"
}

cmd_show() {
  local name="${1:-}"; local f="$CLIENTS/$name.conf"
  [[ -f "$f" ]] || die "no such client: $name"
  echo "--- $f ---"; cat "$f"; echo
  # Minified payload: the full 3.1 config is too dense to scan reliably (125 modules
  # vs 97). The file above is what you send; this QR carries the same config without
  # comments or alignment. See config/awg31.py:qr_payload.
  if command -v qrencode >/dev/null 2>&1; then
    python3 -c "import sys;sys.path.insert(0,'$DIR/config');import awg31;sys.stdout.write(awg31.qr_payload(open('$f').read()))" \
      | qrencode -t ANSIUTF8
  else echo "(install qrencode for a scannable QR; otherwise send the file above)"; fi
}

cmd_list() {
  printf '%-20s %-16s %s\n' NAME ADDRESS PUBKEY
  local name ip pub
  while read -r name; do
    ip=$(peer_field "$name" AllowedIPs)
    pub=$(peer_field "$name" PublicKey)
    printf '%-20s %-16s %s\n' "$name" "${ip:-?}" "${pub:-?}"
  done < <(grep -oP '^# client: \K\S+' "$CONF" || true)
  if live; then echo; echo "--- live ---"; inct awg show "$IFACE"; fi
}

cmd_remove() {
  lock
  local name="${1:-}"; local f="$CLIENTS/$name.conf"
  has_peer "$name" || die "no such client: $name"
  local pub; pub=$(peer_field "$name" PublicKey)
  cp -a "$CONF" "$CONF.bak-$(date +%Y%m%d-%H%M%S)"
  python3 - "$CONF" "$name" <<'PY'
import re,sys
p,name=sys.argv[1],sys.argv[2]
t=open(p).read()
t=re.sub(r"\n# client: %s .*?(?=\n# client: |\Z)"%re.escape(name),"",t,flags=re.S)
open(p,"w").write(t)
PY
  rm -f "$f"
  live && inct awg set "$IFACE" peer "$pub" remove && echo "peer '$name' revoked live." \
       || echo "peer '$name' removed from config."
}

case "${1:-}" in
  add)    shift; cmd_add "$@" ;;
  show)   shift; cmd_show "$@" ;;
  list)   shift; cmd_list "$@" ;;
  remove) shift; cmd_remove "$@" ;;
  *) sed -n '2,6p' "${BASH_SOURCE[0]}" | sed 's/^# \?//'; exit 1 ;;
esac
