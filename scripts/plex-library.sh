#!/usr/bin/env bash
# Wire the Plex media folder into Nextcloud and Plex. Safe to re-run: every step
# checks before it changes anything.
#
#   1. Nextcloud: enables "External storage" and mounts /mnt/plex (the
#      PLEX_MEDIA bind mount) as the folder "Plex" for the admin group.
#   2. Plex (host install): creates the Movies / TV Shows / Anime libraries on
#      PLEX_MEDIA and turns on "scan when files change".
#
# Plex accepts unauthenticated calls from localhost only while it is unclaimed.
# On a claimed server pass its token: PLEX_TOKEN=... scripts/plex-library.sh
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; . ./.env; set +a

NC=${NEXTCLOUD_CONTAINER:-${STACK_NAME:-meowstack}_nextcloud}
PLEX=${PLEX_URL:-http://127.0.0.1:32400}
MEDIA=${PLEX_MEDIA:?PLEX_MEDIA is not set in .env}
occ() { docker exec -u www-data "$NC" php occ "$@"; }
plex() { curl -fsS -H "X-Plex-Token: ${PLEX_TOKEN:-}" -H 'X-Plex-Container-Size: 200' -H 'X-Plex-Container-Start: 0' "$@"; }

echo "nextcloud:"
occ app:enable files_external >/dev/null && echo "  ✓ external storage enabled"
if occ files_external:list --output=json | grep -q '"datadir":"\\/mnt\\/plex"'; then
  echo "  · /Plex mount exists"
else
  id=$(occ files_external:create /Plex local null::null -c datadir=/mnt/plex | grep -o '[0-9]\+$')
  occ files_external:applicable "$id" --add-group admin >/dev/null
  # Pick up files that land on disk without going through Nextcloud.
  occ files_external:option "$id" filesystem_check_changes 1
  echo "  ✓ /Plex mounted for group admin (id $id)"
fi

echo "plex:"
if ! sections=$(plex "$PLEX/library/sections" 2>/dev/null); then
  echo "  ! $PLEX not reachable or refused (claimed server? set PLEX_TOKEN) — skipped"; exit 0
fi
add() {  # name type agent scanner folder
  if grep -q "path=\"$MEDIA/$5\"" <<<"$sections"; then echo "  · $1 exists"; return; fi
  plex -X POST -G "$PLEX/library/sections" --data-urlencode "name=$1" --data-urlencode "type=$2" \
    --data-urlencode "agent=$3" --data-urlencode "scanner=$4" --data-urlencode "language=${PLEX_LANGUAGE:-en-US}" \
    --data-urlencode "location=$MEDIA/$5" >/dev/null && echo "  ✓ $1 -> $MEDIA/$5"
}
add Movies   movie tv.plex.agents.movie  "Plex Movie"     Movies
add "TV Shows" show tv.plex.agents.series "Plex TV Series" "TV Shows"
add Anime    show  tv.plex.agents.series "Plex TV Series" Anime
# Full scan on change, not partial: a partial scan of a brand-new
# "Show/Season 01" folder can finish without adding the show. Libraries this
# size rescan in a second. The hourly scan is the safety net.
plex -X PUT "$PLEX/:/prefs?FSEventLibraryUpdatesEnabled=1&FSEventLibraryPartialScanEnabled=0&ScheduledLibraryUpdatesEnabled=1" >/dev/null \
  && echo "  ✓ scan on change (full) + hourly"
