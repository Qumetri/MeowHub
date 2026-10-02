#!/usr/bin/env bash
# Upgrade the 3x-ui panel (and the Xray it bundles) with an automatic rollback.
#
#   scripts/3xui-upgrade.sh <image>     e.g. ghcr.io/mhsanaei/3x-ui:v3.8.5
#   scripts/3xui-upgrade.sh --test <image>   dry run against a COPY of the DB, live untouched
#   scripts/3xui-upgrade.sh --confirm   keep the new version (disarms the watchdog)
#   scripts/3xui-upgrade.sh --rollback  go back now
#
# What it does:
#   1. pulls the image and resolves it to a digest (compose.yml stays pinned by digest)
#   2. stops the panel, backs up x-ui.db + -wal + -shm together (WAL mode: a hot copy is stale)
#   3. pins compose.yml to the new digest and starts it
#   4. arms a detached watchdog: unless --confirm runs within WATCH_MIN minutes (default 15),
#      the old compose.yml and DB are restored. A dropped SSH session can't leave the VPN down.
#
# The panel's own "Update" button cannot work in Docker (it fails with exit 2) — use this.
# Run --test first and read the release notes' "Before you upgrade" section.
set -euo pipefail

SELF=$(readlink -f "$0")
PANEL_DIR=${PANEL_DIR:-$(dirname "$(dirname "$SELF")")/3xpanel}
DB_DIR=$PANEL_DIR/db
STATE=$PANEL_DIR/.upgrade        # holds the pending rollback info
WATCH_MIN=${WATCH_MIN:-15}
COMPOSE=$(ls "$PANEL_DIR"/compose.yml "$PANEL_DIR"/docker-compose.yml 2>/dev/null | head -1 || true)
[ -n "$COMPOSE" ] || { echo "no compose file in $PANEL_DIR (set PANEL_DIR)"; exit 1; }
dc() { (cd "$PANEL_DIR" && docker compose "$@"); }

alp() { docker run --rm -v "$DB_DIR":/db alpine:3 sh -c "$1"; }

rollback() {
  [ -f "$STATE/backup" ] || { echo "nothing to roll back"; exit 1; }
  local bdir; bdir=$(cat "$STATE/backup")
  echo "==> rolling back to $(grep -o 'sha256:[0-9a-f]*' "$STATE/compose.yml") with DB $bdir"
  dc stop >/dev/null 2>&1 || true
  alp "rm -f /db/x-ui.db /db/x-ui.db-wal /db/x-ui.db-shm; cp -a /db/$bdir/. /db/"
  cp "$STATE/compose.yml" "$COMPOSE"
  dc up -d
  rm -rf "$STATE"
  echo "==> rolled back"
}

# Dry run: the new image migrates a copy of the DB in an isolated container (bridge network,
# so no live ports; Telegram bot off in the copy so it can't fight the live bot for updates).
test_image() {
  local t c=xui-upgrade-test; t=$(mktemp -d)
  docker pull "$1" >/dev/null
  docker run --rm -v "$DB_DIR":/db:ro -v "$t":/out python:3-slim python -c "
import sqlite3
d=sqlite3.connect('/out/x-ui.db'); sqlite3.connect('file:/db/x-ui.db?mode=ro',uri=True).backup(d)
d.execute(\"update settings set value='false' where key='tgBotEnable'\"); d.commit()"
  docker run -d --name $c -v "$t":/etc/x-ui -v "$PANEL_DIR/cert":/root/cert:ro "$1" >/dev/null
  sleep 15
  docker logs $c 2>&1 | grep -E 'Starting x-ui|started|ERROR|panic|refus|migrat' | grep -v 'context canceled' || true
  echo "--- listening in the test container:"
  docker exec $c sh -c 'netstat -tlnu 2>/dev/null' | awk 'NR>2{print $1, $4}' | sort -u | tr '\n' ' '; echo
  docker rm -f $c >/dev/null; docker run --rm -v "$t":/t alpine:3 rm -rf /t/x-ui.db /t/x-ui.db-wal /t/x-ui.db-shm; rmdir "$t" 2>/dev/null || true
}

# Not pkill -f: that also matches any shell whose command line mentions the watchdog.
disarm() { [ -f "$STATE/watchdog.pid" ] && kill "$(cat "$STATE/watchdog.pid")" 2>/dev/null || true; }

case "${1:-}" in
  --confirm)  [ -d "$STATE" ] || { echo "no upgrade pending"; exit 0; }
              disarm; rm -rf "$STATE"; echo "kept; watchdog disarmed"; exit 0 ;;
  --rollback) disarm; rollback; exit 0 ;;
  --watchdog-fired) [ -d "$STATE" ] && rollback; exit 0 ;;   # internal: the watchdog's call
  --test)     test_image "${2:?image}"; exit 0 ;;
  ""|-h|--help) sed -n '2,17p' "$0"; exit 0 ;;
esac

[ -d "$STATE" ] && { echo "an upgrade is already pending: --confirm or --rollback first"; exit 1; }
IMAGE=$1
docker pull "$IMAGE" >/dev/null
DIGEST=$(docker image inspect "$IMAGE" --format '{{index .RepoDigests 0}}')
VERSION=$(docker image inspect "$IMAGE" --format '{{index .Config.Labels "org.opencontainers.image.version"}}')
grep -q "${DIGEST#*@}" "$COMPOSE" && { echo "already on $DIGEST"; exit 0; }
echo "==> target $VERSION ($DIGEST)"

TS=$(date +%Y%m%d-%H%M%S)
BDIR=x-ui.backup-pre${VERSION//./}-$TS
mkdir -p "$STATE"
cp "$COMPOSE" "$STATE/compose.yml"
cp "$COMPOSE" "$COMPOSE.bak-$TS"
echo "$BDIR" > "$STATE/backup"

dc stop >/dev/null
alp "mkdir -p /db/$BDIR && cp -a /db/x-ui.db /db/$BDIR/ && for f in wal shm; do [ -f /db/x-ui.db-\$f ] && cp -a /db/x-ui.db-\$f /db/$BDIR/ || true; done; ls /db/$BDIR"
# Keeps an ${XUI_IMAGE:-...} wrapper if the file has one (a set XUI_IMAGE still wins).
python3 - "$COMPOSE" "$DIGEST" "$VERSION" <<'PY'
import re, sys
f, digest, ver = sys.argv[1:]
s = open(f).read()
def sub(m):
    ref = "${XUI_IMAGE:-%s}" % digest if "${XUI_IMAGE" in m.group(2) else digest
    return "%s%s   # %s" % (m.group(1), ref, ver)
s, n = re.subn(r"^(\s*image:\s*)(.*)$", sub, s, count=1, flags=re.M)
assert n == 1, "no image: line"
open(f, "w").write(s)
PY
[ -n "${XUI_IMAGE:-}" ] && echo "!! XUI_IMAGE is set in the environment and overrides compose.yml"
dc up -d

setsid nohup bash -c "exec -a 3xui-upgrade-watchdog bash -c 'sleep $((WATCH_MIN * 60)); \"$SELF\" --watchdog-fired'" \
  >> "$PANEL_DIR/upgrade-watchdog.log" 2>&1 < /dev/null &
echo $! > "$STATE/watchdog.pid"
echo "==> started $VERSION. Verify, then: $SELF --confirm  (auto-rollback in $WATCH_MIN min)"
