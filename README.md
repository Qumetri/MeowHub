<div align="center">

# 🐱🦆 MeowHub

**Your self-hosted corner of the internet.**

Files, photos, private chat with voice and video, a media downloader, a live
dashboard — and two VPN paths for networks that don't want you to have any of it.

One `docker compose up`. One `.env`. One reverse proxy.

![Docker Compose](https://img.shields.io/badge/Docker-Compose_v2-2496ED?logo=docker&logoColor=white)
![Caddy](https://img.shields.io/badge/TLS-automatic-00C7B7?logo=caddy&logoColor=white)
![Services](https://img.shields.io/badge/services-18-7928CA)
![Config](https://img.shields.io/badge/settings-one_.env-FF0080)

<img src="docs/img/hub.png" alt="The MeowHub dashboard: service cards and live host metrics" width="100%">

</div>

---

## What this is

A complete home server in one repository. Clone it, set two values, run one
script, and you get your own cloud — with real certificates, on your own domain,
on hardware you control.

|  | Service | Lives at |
|---|---|---|
| ☁️ | **Nextcloud** — files, sync, calendar, office | `cloud.yourdomain` |
| 🖼️ | **Immich** — photos and video, face recognition | `photos.yourdomain` |
| 💬 | **Matrix + Element** — private chat, voice, video | `matrix.yourdomain` |
| ⬇️ | **MeTube** — yt-dlp downloader, self-cleaning | `yourdomain/<secret>` |
| 📊 | **Hub page** — service cards + live CPU/RAM/GPU/disk/network | `yourdomain/<secret>` |
| 🛡️ | **AmneziaWG** — obfuscated WireGuard VPN | optional |
| 🚀 | **3x-ui** — VLESS/Reality, Hysteria2, Shadowsocks | optional |

## Quick start

```bash
git clone https://github.com/Qumetri/MeowHub.git && cd MeowHub
cp .env.example .env
$EDITOR .env          # set BASE_DOMAIN and ACME_EMAIL — that's all that's required
./bootstrap.sh
docker compose up -d
```

`bootstrap.sh` does the tedious parts: generates every password, randomises the
secret URLs, renders the config files for programs that can't read environment
variables, builds the Caddy image and the dashboard, and creates the directories.
It's safe to re-run — it never overwrites something you set.

Then open the URL it prints. Certificates are issued automatically.

> **Before you start:** point DNS at the server and make sure ports 80 and 443
> are reachable. Certificate issuance fails otherwise, and Let's Encrypt
> rate-limits retries. Full walkthrough in **[docs/DEPLOY.md](docs/DEPLOY.md)**.

## How the traffic flows

Port 443 carries three things that normally can't share a port: ordinary HTTPS,
TURN-over-TLS for calls, and VPN traffic that must *not* be decrypted. A custom
Caddy build with the layer4 module peeks at the SNI and routes accordingly.

```mermaid
flowchart TB
    net([Internet]) -->|":443"| l4

    subgraph edge ["Caddy · layer4 SNI router"]
        l4{{"which SNI?"}}
    end

    l4 -->|"turn.domain<br/>terminate TLS"| lk["LiveKit<br/>call media"]
    l4 -->|"VPN hostnames<br/>raw passthrough"| vpn["VPN inbounds<br/>Reality does its own TLS"]
    l4 -->|"everything else"| web["Caddy HTTPS<br/>internal port"]

    web --> nc["Nextcloud"]
    web --> im["Immich"]
    web --> mx["Synapse + Element"]
    web --> mt["MeTube"]
    web --> hub["Hub + live stats"]

    classDef router fill:#7928ca,stroke:#a855f7,color:#ffffff
    classDef svc fill:#0f172a,stroke:#38bdf8,color:#e2e8f0
    classDef vpn fill:#0f172a,stroke:#f43f5e,color:#e2e8f0
    class l4,web router
    class nc,im,mx,mt,hub,lk svc
    class vpn vpn
```

Everything behind Caddy is on an internal Docker network. Nothing else is
exposed. Adding a service means adding a route, not opening a port.

## Pick what you run

<table>
<tr><td width="58%" valign="top">

Not everyone wants all of it. One line in `.env` decides:

```ini
# everything
COMPOSE_PROFILES=nextcloud,immich,matrix,metube

# just files and photos
COMPOSE_PROFILES=nextcloud,immich

# a photo server and nothing else
COMPOSE_PROFILES=immich
```

| Profile | Services |
|---|---|
| `nextcloud` | 6 |
| `nextcloud,immich` | 10 |
| all four | 18 |

Caddy's routes are **generated to match**, so a service you turned off leaves
no dead route behind — no 502s, no half-configured vhosts.

The dashboard is responsive, so the hub works from a phone as well as a desk.

</td><td width="42%" valign="top">

<img src="docs/img/hub-mobile.png" alt="The dashboard on a phone" width="100%">

</td></tr>
</table>

## Everything is in one file

`.env` holds **94 settings**, each documented where it sits. Only two have no
sensible default, because they can't:

```ini
BASE_DOMAIN=example.com
ACME_EMAIL=you@example.com
```

Everything else already works: hostnames derive from your domain, images are
pinned to tested versions, ports and storage paths have defaults, and every
password is generated for you.

<details>
<summary><b>What's in there</b></summary>

| Group | Examples |
|---|---|
| Identity | `BASE_DOMAIN`, `CLOUD_HOST`, `MATRIX_HOST`, `ACME_EMAIL`, `TZ` |
| What runs | `COMPOSE_PROFILES`, `COMPOSE_FILE` (GPU overlay) |
| Storage | `DATA_ROOT` and per-service paths |
| Secret paths | `DASHBOARD_PATH`, `METUBE_PATH`, `MATRIXRTC_PATH`, `AWG_ADMIN_PATH` |
| Ports | web, debug, call media, TURN relay range |
| Versions | every image tag, pinned |
| Secrets | 11 passwords and shared secrets, all generated |
| VPN | subnet, endpoint, MTU, port, SNI passthrough |
| Branding | `HUB_NAME`, `ELEMENT_BRAND` |

</details>

## Requirements

- Linux, Docker Engine, Compose v2
- A domain with DNS pointing at the server
- Ports 80 and 443 open
- `gettext-base`, `openssl`, `python3` — and `npm` to build the dashboard
- ~4 GB RAM for everything, ~2 GB without Immich's ML

**GPU is optional.** The base stack requests no devices and runs anywhere. With
an NVIDIA card, add one line to `.env` for hardware transcoding and CUDA face
recognition:

```ini
COMPOSE_FILE=docker-compose.yml:docker-compose.gpu.yml
```

## Layout

```
.env                    every setting
bootstrap.sh            setup + re-render
docker-compose.yml      the stack        docker-compose.gpu.yml   NVIDIA overlay
caddy/                  custom image + Caddyfile template
dashboard/              hub page (Vite + React) — cards in src/services.js
stats/                  host metrics, dependency-free Python
matrix/                 Synapse · Element · coturn · LiveKit  (templates)
amneziawg/              obfuscated WireGuard      ⟵ separate compose project
3xpanel/                3x-ui panel               ⟵ separate compose project
docs/                   deploy · operations · vpn · architecture
```

The VPNs are **separate Compose projects on purpose**. `docker compose up -d` in
the main directory cannot recreate or destroy them, and vice versa.

## Documentation

| | |
|---|---|
| 🚀 **[DEPLOY.md](docs/DEPLOY.md)** | First deployment — host prep, DNS, router ports, permissions, migrating an existing install |
| 🔧 **[OPERATIONS.md](docs/OPERATIONS.md)** | Daily commands, backups, upgrade rules, and the failure modes worth knowing in advance |
| 🛡️ **[VPN.md](docs/VPN.md)** | Both VPN paths, obfuscation, handing out configs |
| 🏗️ **[ARCHITECTURE.md](docs/ARCHITECTURE.md)** | Why it's built this way — mostly stories about what broke first |

## Security, honestly

The hub page and downloader have **no login**. Their URL prefix is the
credential — `bootstrap.sh` gives each a random suffix like
`hub-a1b2c3d4e5f6`, and treating those URLs as passwords is the whole security
model. That's deliberate: it costs nothing, and for a single-user service a
login screen is friction nobody wants.

It is *not* appropriate for anything sensitive, which is why the VPN peer
manager — it hands out working VPN keys — sits behind HTTP basic auth instead.

Nextcloud, Immich and Matrix have real authentication of their own.

Back up `.env`, `secrets/`, `matrix/synapse/signing.key` and `amneziawg/config/`.
The last two cannot be regenerated: lose the signing key and your homeserver's
identity is gone, lose the VPN config and every client key you handed out stops
working.
