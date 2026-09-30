# Helper bot

A Telegram bot for the hub: server health with alerts, the hub's links,
downloads through MeTube, and — for the owner only — the logins for the hub's
services. Profile `helper`; two containers, `helper` and `docker-proxy`.

Dependency-free Python like `crypto/` and `stats/`. It uses **long polling**, so
nothing new listens on the internet and there is no Caddy route.

## Setup

1. Create a bot with **@BotFather** (`/newbot`) and copy its token.
2. Put it in `.env`, with your numeric Telegram id as the owner:

   ```ini
   HELPER_BOT_TOKEN=123456:ABC…
   HELPER_OWNER_ID=123456789
   ```

3. Add `helper` to `COMPOSE_PROFILES`, `docker compose up -d`, then `/start`
   in Telegram.

Don't know your id? Leave `HELPER_OWNER_ID` empty, message the bot — it answers
strangers with their ID — then set it and `docker compose up -d helper`.

Without access to `.env` (e.g. from a phone shell), `ctl.py token` stores the
token in `helper/data/helper.db` instead; `.env` wins when both are set.
`ctl.py status` shows which one is in use.

## Who can do what

| | Owner | Allowed users | Anyone else |
|---|:-:|:-:|:-:|
| 🩺 Health, 🔗 Links, 🎬 Downloads | ✅ | ✅ | — |
| 🔑 Logins (`/pass`, `/setpass`, `/delpass`) | ✅ | — | — |
| Manage users (`/users`, `/allow`, `/deny`) | ✅ | — | — |

The bot is public on Telegram — anyone can find it and write to it. A stranger is
told they have no access and shown their own ID; the owner gets one message with
an **Allow** button. Allowed users **never** get logins, whatever they send: the
owner check runs again on every button press, because a callback carries the id
of whoever pressed it. The bot also **never answers in groups**.

## Health

**🩺 Health** runs every check now and answers with one report. The same checks
run in the background and **message the owner when something breaks** — and
again when it is fixed. A problem must be seen on **two consecutive runs**
before it alerts, so a container being recreated during a deploy stays quiet.

| Check | Every | Alerts when |
|---|---|---|
| SSD / HDD | 1 min | ≥ 85/92% SSD, ≥ 90/95% HDD (warn/crit) |
| RAM, CPU and GPU temperature | 1 min | RAM ≥ 92%, CPU ≥ 90°C, GPU ≥ 85°C |
| GPU present | 1 min | `nvidia-smi` stops answering after it had worked — typically a driver upgrade without a reboot |
| Containers | 1 min | restarting in a loop, `unhealthy`, crashed (non-zero exit that is not a stop signal, or OOM), or silently restarted after a crash |
| TCP connections | 1 min | ≥ 5,000 / 12,000 established sockets — the VPN socket-leak signature |
| Sites | 5 min | a hub card on your domain does not answer, or answers 5xx |
| Certificates | 6 h | expiring in < 14 / 5 days, or invalid |
| DNS | 5 min | an A record no longer points at the server's public IP |

Every threshold is an env var (`HEALTH_SSD_WARN`, `HEALTH_TCP_CRIT`, …; see
`helper/app/health.py`). `HELPER_REPORT_HOUR` sends the full report every
morning; empty disables it.

Design points:

- **The DNS check is the important one** on a home connection. If the ISP hands
  out a new address, every service and every VPN goes dark at once; DNS records
  updated by hand will not follow.
- **A stopped container is not a crash.** `docker stop` leaves exit code 0, 137 or
  143, and a stopped container stays listed under "stopped by hand" in the
  report. Only other exit codes or an OOM kill alert.
- **Restarts are caught through the restart counter.** A crash followed by an
  automatic restart leaves the container "running"; the counter is the only
  trace.
- **Sites and certificates are checked through Caddy on the compose network,
  with the real SNI.** Going out to the public IP from inside the LAN depends on
  the router's hairpin NAT.
- **Container states come through `docker-proxy`**
  (`tecnativa/docker-socket-proxy`), which allows only `GET /containers`. The
  raw Docker socket is root on the host, and this container talks to the
  internet. The proxy sits on an `internal: true` network shared with nothing
  else.

## Links

**🔗 Links** lists the hub page and every card on it, with a button for each.
The list is **`dashboard/dist/services.json`**, written by the dashboard build
from the same `src/services.js` the page renders — add a card, rebuild, and the
bot has it. The hub's own URL comes from `BASE_DOMAIN` + `DASHBOARD_PATH`.

## Downloads

Send any video link (YouTube and most sites yt-dlp supports) and pick **1080p**,
**best** or **MP3**. The bot queues it in MeTube, shows progress, and answers
with a tap-to-save link — plus the file itself when it is under 50 MB (the limit
for bot uploads). Files are purged by the MeTube janitor after `METUBE_TTL_MIN`.

The bot recognises its own download in MeTube's history by **timestamp**, not
by video id: MeTube re-downloads something already in its history under the same
id with a new timestamp.

## Logins

**🔑 Logins** shows a button per service; a press sends login and password:

- the password is under a **spoiler**, and the message is sent with
  `protect_content` (no forwarding, no saving);
- it **deletes itself after `HELPER_PASS_TTL` seconds** (default 60).

Where the logins come from:

| Source | What | Edit with |
|---|---|---|
| `.env` / `secrets/` | Nextcloud admin, crypto UI, VPN UI — mapped in compose as `VAULT_<ID>_USER` + `VAULT_<ID>_PASS` or `_PASS_FILE` | `.env`, then `docker compose up -d helper` |
| the bot | accounts that store only a hash: 3x-ui, Immich, n8n, Matrix users… | `/setpass <id> <login> <password>` |

`<id>` is the card id from `services.js` (`immich`, `3xui`, `n8n`, …); `/pass`
lists them. `/setpass` **deletes your message** as soon as it has read it, and
its reply does not repeat the password. A bot entry overrides an `.env` one;
`/delpass <id>` removes it (`.env` entries can only be changed in `.env`).
The Nextcloud entry is the *initial* admin password — if you changed it in
Nextcloud, store the new one with `/setpass nextcloud admin <new>`.

> **What this trades away.** A bot chat is not end-to-end encrypted, so a
> password sent there passes through Telegram's servers. The spoiler,
> `protect_content` and auto-delete limit what stays on your phone, not what
> Telegram saw. Bot-stored logins sit in plaintext in `helper/data/helper.db`
> (0600 in a 0700 directory). It is a convenience
> for your own logins, not a password manager.

## Operations

```bash
docker compose logs -f helper
docker compose exec helper python3 /app/app/ctl.py status
docker compose up -d --build helper      # after editing helper/app
```

Back up `helper/data/` — it holds the allowed users and the logins added with
`/setpass`.
