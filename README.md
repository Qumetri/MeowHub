# meowstack

A self-hosted home cloud behind a single reverse proxy: files, photos, private
chat with voice/video, a media downloader, a hub page with live host metrics,
and two VPN paths for censored networks.

Everything is Docker Compose. Everything configurable lives in one `.env`.

```
                          ┌──────────── :443 ────────────┐
                          │   Caddy (layer4 SNI router)  │
                          └───┬──────────┬──────────┬────┘
              TURN-over-TLS   │          │          │  raw passthrough
              ┌───────────────┘          │          └───────────────┐
              ▼                          ▼                          ▼
        LiveKit (calls)        Caddy HTTPS (internal)         VPN inbounds
                                         │
        ┌────────────┬───────────────────┼───────────┬──────────────┐
        ▼            ▼                   ▼           ▼              ▼
    Nextcloud     Immich            Synapse +     MeTube        Hub page
    (files)      (photos)           Element      (yt-dlp)     + live stats
```

## Quick start

```bash
cp .env.example .env
$EDITOR .env          # set BASE_DOMAIN and ACME_EMAIL — nothing else is required
./bootstrap.sh        # generates secrets, renders configs, creates dirs
docker compose up -d
```

`bootstrap.sh` is idempotent: it fills in blanks and never overwrites a value
you set. Run it again after changing hostnames or which services you want.

Full instructions, including DNS and router setup: **[docs/DEPLOY.md](docs/DEPLOY.md)**.

## What you get

| Service | URL | Notes |
|---|---|---|
| Hub page | `https://<domain>/<DASHBOARD_PATH>/` | Service cards + live CPU/RAM/GPU/disk/network |
| Nextcloud | `https://cloud.<domain>/` | Files, sync, office |
| Immich | `https://photos.<domain>/` | Photos and video, optional GPU ML |
| Element | `https://matrix.<domain>/` | Private Matrix chat, voice and video |
| MeTube | `https://<domain>/<METUBE_PATH>/` | yt-dlp downloader, auto-purging |
| VPN peers | `https://<domain>/<AWG_ADMIN_PATH>/` | AmneziaWG peer manager (password-protected) |

Pick what runs with `COMPOSE_PROFILES` in `.env`:

```
COMPOSE_PROFILES=nextcloud,immich,matrix,metube    # all of it
COMPOSE_PROFILES=nextcloud,immich                  # just cloud + photos
```

Caddy and the stats backend always run. Dropping a profile also removes its
routes from the generated Caddyfile, so nothing 502s.

## Requirements

- Linux host with Docker Engine and the Compose v2 plugin
- A domain, with DNS pointing at the server (see [docs/DEPLOY.md](docs/DEPLOY.md))
- Ports 80 and 443 reachable from the internet
- `gettext-base` (for `envsubst`), `openssl`, `python3`
- Optional: Node/npm to build the hub page, NVIDIA Container Toolkit for GPU

Roughly 4 GB RAM for the full stack; 2 GB without Immich machine learning.

## Layout

```
.env                      every setting, generated from .env.example
bootstrap.sh              one-time setup + re-render after changes
docker-compose.yml        the main stack
docker-compose.gpu.yml    optional NVIDIA overlay
caddy/                    custom image (Caddy + layer4) and Caddyfile template
dashboard/                hub page (Vite + React); cards in src/services.js
stats/                    dependency-free host-metrics backend
matrix/                   Synapse, Element, coturn, LiveKit — as templates
amneziawg/                obfuscated WireGuard VPN (separate compose project)
3xpanel/                  3x-ui VPN panel (separate compose project)
scripts/                  cert sync for the VPN panel, host sysctl tuning
docs/                     deployment, operations, VPN, architecture notes
```

The two VPN projects are deliberately separate Compose projects. A mistake in
one cannot recreate or destroy anything in the main stack.

## Documentation

- **[docs/DEPLOY.md](docs/DEPLOY.md)** — first deployment, DNS, router, permissions
- **[docs/OPERATIONS.md](docs/OPERATIONS.md)** — day-to-day, backups, upgrades, troubleshooting
- **[docs/VPN.md](docs/VPN.md)** — the two VPN paths, obfuscation, handing out configs
- **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** — why it is built this way

## A note on secret paths

The hub page, downloader and Element Call endpoints have no login of their own.
Their URL prefix *is* the credential. `bootstrap.sh` gives each a random suffix
on first run — treat those URLs like passwords and don't paste them anywhere
public. The VPN peer manager is the exception: it sits behind HTTP basic auth.
