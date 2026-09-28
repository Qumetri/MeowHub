#!/usr/bin/env bash
# One-time AmneziaWG server setup. Generates the server keypair and the
# SERVER-SIDE obfuscation values, then writes config/awg0.conf and
# config/params.env.
#
#   cd amneziawg && ./awg-init.sh && docker compose up -d
#
# Refuses to run twice. The values it generates are the ones that must be
# byte-identical on server and client — regenerating them would invalidate
# every config already handed out.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

CONF=config/awg0.conf
PARAMS=config/params.env
IMG=${AWG_IMAGE:-amneziavpn/amneziawg-go:3.1.20260828}

[[ -e $CONF ]] && { echo "config/awg0.conf already exists — refusing to overwrite."; exit 1; }

# Settings come from the main .env; fall back to sane defaults standalone.
[[ -f ../.env ]] && { set -a; . ../.env; set +a; }
AWG_PORT=${AWG_PORT:-20443}
AWG_SUBNET=${AWG_SUBNET:-10.8.2.0/24}
AWG_SERVER_IP=${AWG_SERVER_IP:-10.8.2.1}
AWG_MTU=${AWG_MTU:-1280}
AWG_ENDPOINT=${AWG_ENDPOINT:-${BASE_DOMAIN:?set BASE_DOMAIN or AWG_ENDPOINT}}

awgtool() { docker run --rm -i --entrypoint awg "$IMG" "$@"; }

echo "generating server keypair…"
PRIV=$(awgtool genkey </dev/null)
PUB=$(printf '%s' "$PRIV" | awgtool pubkey)

# S1-S4 are random prefixes on the four message types; H1-H4 replace the
# message-type identifiers. Both are server-side: they MUST match on every
# client, which is why they are generated once, here, and never touched again.
#
# S2-S1 must not equal 56, or S1+sizeof(Init) == S2+sizeof(Response) and the
# two packet types become distinguishable by length again — defeating the point.
echo "generating obfuscation values…"
while :; do
  S1=$((RANDOM % 128 + 15)); S2=$((RANDOM % 128 + 15))
  [[ $((S2 - S1)) -ne 56 ]] && break
done
S3=$((RANDOM % 128 + 15)); S4=$((RANDOM % 128 + 15))
# Header values must be >= 5 (1-4 are WireGuard's own message types).
h() { python3 -c 'import random;print(random.randint(5,2**31))'; }
H1=$(h); H2=$(h); H3=$(h); H4=$(h)

umask 077
cat > "$PARAMS" <<EOF
AWG_PORT=$AWG_PORT
AWG_SUBNET=$AWG_SUBNET
AWG_SERVER_IP=$AWG_SERVER_IP
AWG_ENDPOINT=$AWG_ENDPOINT
AWG_MTU=$AWG_MTU
SERVER_PRIVKEY=$PRIV
SERVER_PUBKEY=$PUB
# --- server-side: MUST be byte-identical on server and every client ---
# Changing any of these breaks every already-distributed config at once.
S1=$S1
S2=$S2
S3=$S3
S4=$S4
H1=$H1
H2=$H2
H3=$H3
H4=$H4

# --- client-side obfuscation (AWG 3.1) is NOT here ---
# Jc/Jmin/Jmax, I1-I5, ContentPaddingAddition and the timing ranges do not have
# to match between the two ends, so they are generated fresh per client by
# config/awg31.py instead of being pinned globally. That is deliberate: one
# shared set makes every peer emit an identical flow signature.
EOF

cat > "$CONF" <<EOF
# AmneziaWG server. Generated $(date -u +%Y-%m-%dT%H:%M:%SZ) by awg-init.sh.
# Add peers with ./awg-client.sh add <name> — never hand-edit past the marker.
[Interface]
Address    = $AWG_SERVER_IP/${AWG_SUBNET#*/}
ListenPort = $AWG_PORT
PrivateKey = $PRIV
MTU        = $AWG_MTU

# Server-side obfuscation — must match every client config exactly.
S1   = $S1
S2   = $S2
S3   = $S3
S4   = $S4
H1   = $H1
H2   = $H2
H3   = $H3
H4   = $H4

PostUp   = iptables -t nat -A POSTROUTING -s $AWG_SUBNET -o eth0 -j MASQUERADE; iptables -A FORWARD -i %i -j ACCEPT; iptables -A FORWARD -o %i -j ACCEPT
PostDown = iptables -t nat -D POSTROUTING -s $AWG_SUBNET -o eth0 -j MASQUERADE; iptables -D FORWARD -i %i -j ACCEPT; iptables -D FORWARD -o %i -j ACCEPT

# --- peers appended below by awg-client.sh; do not hand-edit past this line ---
EOF

chmod 600 "$CONF" "$PARAMS"
echo
echo "  server pubkey : $PUB"
echo "  endpoint      : $AWG_ENDPOINT:$AWG_PORT/udp   (forward this on the router)"
echo "  subnet        : $AWG_SUBNET"
echo
echo "Next:  docker compose up -d  &&  ./awg-client.sh add <name>"
