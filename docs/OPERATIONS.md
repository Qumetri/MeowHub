# Operating meowstack

Day-to-day commands, backups, upgrades, and the failure modes worth knowing
about in advance.

## Everyday commands

```bash
docker compose ps                    # what is running
docker compose logs -f caddy         # follow one service
docker compose up -d                 # apply compose or .env changes
docker compose restart caddy         # Caddyfile-only change, no recreate
docker compose pull && docker compose up -d   # update images
```

Nextcloud's CLI:

```bash
docker compose exec -u www-data nextcloud php occ status
docker compose exec -u www-data nextcloud php occ user:list
```

## Changing settings

Almost everything lives in `.env`. What you change decides what you run after:

| Changed | Do this |
|---|---|
| A secret path (`DASHBOARD_PATH`, …) | `docker compose restart caddy` |
| Hostnames, `COMPOSE_PROFILES` | `./bootstrap.sh && docker compose up -d` |
| Anything in a `matrix/*.template` | `./bootstrap.sh && docker compose up -d` |
| Hub cards (`dashboard/src/services.js`) | `cd dashboard && npm run build` |
| Metrics (`stats/server.py`) | `docker compose restart hub-stats` |
| Crypto tracker code (`crypto/app`, `crypto/web`) | `docker compose up -d --build crypto` |

Caddy reads `{$VAR}` at load time, which is why a path change needs only a
restart. Synapse, coturn, LiveKit and Element have no env substitution at all —
that is why they are templates, and why editing the *rendered* file is a trap:
the next `bootstrap.sh` overwrites it. Edit the `.template`.

(As a safety net, `bootstrap.sh` skips any rendered file that is newer than its
template, so a hand edit is not silently destroyed. It also won't pick up your
template change until you touch it — if a render seems ignored, that's why.)

## Adding a service to the hub page

Edit `dashboard/src/services.js`, then `cd dashboard && npm run build`. Cards
are data — id, name, description, url, icon, gradient, status. `status: 'soon'`
renders a dimmed placeholder.

URLs come from build-time env vars written by `bootstrap.sh` into
`dashboard/.env.local`, so the bundle is not tied to one domain.

## Backups

Three tiers, by how bad losing them is.

### Irreplaceable — back up now, keep offsite

```
.env                          every password and secret
secrets/                      VPN admin password
matrix/synapse/signing.key    the homeserver's identity; cannot be regenerated
amneziawg/config/             VPN server key + the obfuscation values
```

Small enough to keep in a password manager. Without `amneziawg/config/params.env`
every client config you handed out stops working, and the `S1-S4`/`H1-H4` values
in it cannot be recovered from anything else.

### Data — back up on a schedule

```
$DATA_ROOT/nextcloud          user files
$DATA_ROOT/immich             photos and videos
$DATA_ROOT/matrix/media       chat attachments
./nextcloud/db                Nextcloud MariaDB
./immich/postgres             Immich Postgres
$DATA_ROOT/matrix/postgres    Synapse Postgres
```

Dump databases rather than copying files from under a running server:

```bash
docker compose exec nextcloud-db \
  mariadb-dump -u root -p"$NEXTCLOUD_DB_ROOT_PASSWORD" --single-transaction nextcloud \
  > backup/nextcloud-$(date +%F).sql

docker compose exec immich-db \
  pg_dump -U "$DB_USERNAME" "$DB_DATABASE_NAME" > backup/immich-$(date +%F).sql

docker compose exec matrix-db \
  pg_dump -U synapse synapse > backup/synapse-$(date +%F).sql
```

### Replaceable

`caddy/data` (certificates re-issue), `immich/model-cache` (re-downloads),
`dashboard/dist` (rebuilds). Copying `caddy/data` on a migration is still worth
it to avoid re-issuing certificates against a rate limit.

## Upgrades

Images are pinned in `.env` on purpose. To upgrade, change the tag there, then
`docker compose up -d <service>`.

**Nextcloud must go one major at a time.** 34 → 35 → 36, never 34 → 36. A
skipped major leaves the instance unable to start and the fix is a restore.
This is the reason nothing here uses `:latest`.

**3x-ui is pinned by digest**, not tag, because a panel upgrade migrates its own
database and is not reliably reversible. Use `scripts/3xui-upgrade.sh`: it dry-runs
on a copy, backs up, and rolls back by itself unless you confirm. See
[VPN.md](VPN.md#upgrading).

Before any upgrade: back up the database, note the current tag, and read the
project's release notes for migration steps.

## MeTube downloader

The `metube` profile runs MeTube (a yt-dlp web UI) plus a janitor sidecar:

- **Pinned image** (`METUBE_IMAGE`, not `:latest`): some builds broke YouTube's
  PO-token helper. Bump it deliberately after reading the release notes.
- **basic_auth on the route** at `/<METUBE_PATH>/`, the same login as the bots admin
  page (`BOT_ADMIN_USER`, `secrets/bot-admin-password`). MeTube's API can queue and
  delete downloads and upload cookies, so a secret path alone is too little once
  several people use it. Members never open this route: the helper bot's downloader
  talks to `metube:8081` on the compose network and serves files through its own
  signed links ([HELPER.md](HELPER.md#downloader)).
- **No directory listings** (`DOWNLOAD_DIRS_INDEXABLE: false`), 2 concurrent
  downloads (`MAX_CONCURRENT_DOWNLOADS`), and yt-dlp upgraded daily at 04:30
  (`YTDL_NIGHTLY_UPDATE_TIME`) because YouTube breaks old versions regularly.
- **Janitor** (`metube-janitor`, every 2 minutes): deletes files in the download
  dir and its per-user `u<id>/` folders that were last changed more than
  `METUBE_TTL_MIN` (default 30) minutes ago, then removes empty `u*` folders.
  It ages by **ctime** because yt-dlp may stamp a file with the video's upload date
  (mtime), which would make a fresh download look years old. `keep/` and
  `.metube/` (state) are never touched; move a file into `keep/` to retain it.
- Needs the download dir owned by `PUID`:`PGID` (see DEPLOY.md). A change to the
  route's auth or path needs `docker compose up -d --no-deps caddy` (compose-level
  env), not just a restart.

## Failure modes worth knowing

**"Secure Connection Failed", cached for weeks.** If the internal Caddy server
advertises HTTP/3, browsers try QUIC against a UDP port where the TCP-only
layer4 router can never answer, and cache that for 30 days. The `protocols h1 h2`
line in the Caddyfile prevents it. Don't remove it.

**Works for existing users, fails for new connections.** The router's NAT table
is full of leaked sockets. Install `scripts/host-tuning.conf` into
`/etc/sysctl.d/` — a live `sysctl` alone reverts on reboot and the problem
returns weeks later, looking like something else entirely.

**Nextcloud blank page after moving data.** Its data dir must be owned by uid
33 (`www-data`). See DEPLOY.md.

**A GPU container won't start after a system update.** A driver upgrade without
a reboot leaves the kernel module and userspace mismatched. Reboot; consider
holding the driver package.

**Immich's first user is whoever registers first.** Claim it immediately after
exposing the service.

## Health checks

```bash
# everything up?
docker compose ps --format 'table {{.Name}}\t{{.Status}}'

# certificates present?
docker compose exec caddy find /data -name '*.crt' -path '*certificates*'

# reachable from outside (run this off the server — from the host it can
# hairpin through your router and pass while the internet sees failure)
curl -sI https://cloud.example.com | head -1
```

That last point costs more debugging time than anything else here: a test run
*on* the server goes out to the router and back, so it can succeed while the
service is unreachable from the internet. Always confirm externally.
