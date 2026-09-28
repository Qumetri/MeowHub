# Deploying meowstack on a new server

From a bare Linux box to a running stack. Budget about 30 minutes, most of it
waiting for images to pull.

## 1. Prepare the host

```bash
# Docker Engine + Compose v2
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker "$USER"   # log out and back in

# What bootstrap.sh needs
sudo apt install -y gettext-base openssl python3 git

# Optional: to build the hub page
sudo apt install -y nodejs npm
```

Verify before going further:

```bash
docker compose version     # must be v2.x
docker run --rm hello-world
```

### Storage

The stack separates code from bulk data. Code and databases are small and want
fast disk; user files are large and want capacity. If you have one disk, that's
fine — point `DATA_ROOT` anywhere with room.

```bash
sudo mkdir -p /srv/media
sudo chown "$USER":"$USER" /srv/media
```

Rough sizing: Nextcloud and Immich grow with what you put in them. The
databases, model cache and Matrix media add maybe 5–10 GB. Immich's CUDA ML
image alone is several GB.

## 2. DNS

You need four A records, all pointing at the server's public IP:

| Record | Purpose |
|---|---|
| `example.com` | Hub page, well-knowns, redirect to Nextcloud |
| `cloud.example.com` | Nextcloud |
| `photos.example.com` | Immich |
| `matrix.example.com` | Synapse + Element |
| `turn.example.com` | TURN-over-TLS for calls (Matrix profile only) |

A wildcard `*.example.com` covers all of them.

**Wait for DNS to propagate before starting the stack.** Caddy requests
certificates on first boot, and Let's Encrypt rate-limits failures. Check with
`dig +short cloud.example.com` from somewhere other than the server.

On a dynamic IP, you need a dynamic-DNS updater — the stack does not ship one,
and a stale record means expired certificates a few weeks later.

## 3. Router / firewall

| Port | Protocol | Needed for | Required? |
|---|---|---|---|
| 80 | TCP | ACME certificate challenges | **yes** |
| 443 | TCP | Everything web, plus TURN-over-TLS | **yes** |
| 7881 | TCP | Matrix call media (LiveKit) | matrix profile |
| 7882 | UDP | Matrix call media (LiveKit) | matrix profile |
| 3478 | UDP | Legacy 1:1 calls (coturn) | matrix profile |
| 49160–49200 | UDP | coturn relay range | matrix profile |
| 20443 | UDP | AmneziaWG VPN | VPN only |

Nothing else should be exposed. In particular do **not** forward the database
ports, SSH from the whole internet without key-only auth, or any of the
`127.0.0.1:` debug ports in the compose file.

> If the host sits in a DMZ, every port is forwarded whether you meant it or
> not. Audit with `ss -lntup` from the host and close what you don't recognise.

## 4. Configure

```bash
git clone <your-repo> meowstack && cd meowstack
cp .env.example .env
$EDITOR .env
```

Only two values *must* change:

```ini
BASE_DOMAIN=example.com
ACME_EMAIL=you@example.com
```

`BASE_DOMAIN` is baked into every Matrix user ID permanently. Changing it later
orphans every account, so decide now.

Worth reviewing while you're in there:

- `COMPOSE_PROFILES` — which services run
- `DATA_ROOT` — where bulk data lives
- `TZ` — affects Nextcloud cron and log timestamps
- `COMPOSE_FILE` — add `:docker-compose.gpu.yml` if you have an NVIDIA card

## 5. Bootstrap

```bash
./bootstrap.sh
```

It will:

1. Generate every password and shared secret (into `.env`)
2. Give each secret path a random suffix
3. Generate the VPN admin password (plaintext into `secrets/`)
4. Resolve derived values like `MATRIX_HOST` into literal strings
5. Render Synapse, coturn, LiveKit and Element configs from templates
6. Render the Caddyfile, dropping blocks for profiles you disabled
7. Create data directories and the shared docker network
8. Build the custom Caddy image and the hub page

Safe to re-run. It skips anything already set and won't clobber a config file
you have edited by hand since it was rendered.

### Permissions

Run as root (`sudo ./bootstrap.sh`) and ownership is set for you. Otherwise do
it yourself — these matter:

```bash
sudo chown -R 33:33     /srv/media/nextcloud        # www-data inside the container
sudo chown -R 1000:1000 /srv/media/matrix/media     # Synapse runs as PUID
sudo chown -R 1000:1000 /srv/media/youtube          # MeTube runs as PUID
sudo chown -R 1000:1000 ./matrix/synapse            # so config stays host-editable
```

Nextcloud's data dir owned by anything but uid 33 gives a blank page with
permission errors in the logs, and it is the single most common first-run
mistake.

## 6. Start

```bash
docker compose up -d
docker compose ps
docker compose logs -f caddy
```

Watch Caddy until you see certificates obtained for each hostname. If ACME
fails, it is nearly always DNS or port 80 — not Caddy.

Then open `https://<domain>/<DASHBOARD_PATH>/`. The exact URL is printed at the
end of the bootstrap output, and lives in `.env`.

## 7. First-run setup

**Nextcloud** — log in as `admin`; the password is `NEXTCLOUD_ADMIN_PASSWORD`
in `.env`. Change it in the UI.

**Immich** — the first account you register becomes the administrator. Register
it immediately: until you do, anyone reaching the URL can claim it.

**Matrix** — registration is closed by design. Create accounts from the host:

```bash
docker compose exec matrix-synapse \
  register_new_matrix_user -c /data/homeserver.yaml http://localhost:8008
```

Then sign in at `https://matrix.<domain>/`. Test a call — if media fails,
it is the 7881/7882 forwards.

**VPN** (optional):

```bash
cd amneziawg
./awg-init.sh              # generates server keys, once
docker compose up -d
./awg-client.sh add alice  # prints a config and a QR
```

See [VPN.md](VPN.md).

## 8. Back up what cannot be regenerated

```bash
cp .env ~/somewhere-safe/
cp -r secrets/ ~/somewhere-safe/
cp matrix/synapse/signing.key ~/somewhere-safe/
cp -r amneziawg/config/ ~/somewhere-safe/
```

`.env` holds every password. The Synapse signing key cannot be regenerated —
lose it and the homeserver's identity is gone. The AmneziaWG server key is what
every distributed client config points at; lose it and every user needs a new
config.

See [OPERATIONS.md](OPERATIONS.md) for backing up the actual data.

## Moving an existing install

The stack is portable, but the *data* is what matters:

1. Stop the old stack: `docker compose down`
2. Copy `DATA_ROOT`, `./nextcloud/db`, `./immich/postgres`, `./caddy/data`
3. Copy `.env`, `secrets/`, `matrix/synapse/signing.key`, `amneziawg/config/`
4. Repoint DNS, wait for propagation
5. `./bootstrap.sh && docker compose up -d`

Keep `BASE_DOMAIN` identical or Matrix accounts break. Copying `caddy/data`
carries the certificates over and avoids re-issuing them, which matters if you
are near a Let's Encrypt rate limit.

## Troubleshooting first boot

**Caddy won't get certificates** — check `dig +short <host>` resolves to this
server from *outside*, and that port 80 reaches it. Let's Encrypt rate-limits
repeated failures for an hour, so fix DNS before retrying.

**"Secure Connection Failed" in a browser** — almost always HTTP/3. The
Caddyfile pins `protocols h1 h2` on the internal server for exactly this
reason; if you removed that line, Caddy advertises h3 on a port where layer4
(TCP-only) can never deliver it, and browsers cache that for 30 days.

**A service 502s** — its profile is probably off. `docker compose ps` shows
what is actually running; `COMPOSE_PROFILES` decides.

**Immich won't start with GPU enabled** — verify the toolkit independently:
`docker run --rm --gpus all nvidia/cuda:12.4.0-base-ubuntu22.04 nvidia-smi`.
If a driver was upgraded without a reboot, kernel and userspace are out of sync
and every GPU container fails until you reboot.
