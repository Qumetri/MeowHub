# Helper bot

A Telegram bot for the hub: server health with alerts, the hub's links,
downloads through MeTube, the owner's logins — and a small **membership system**:
people you invite redeem an access code and get a VPN subscription link and a
Matrix account for as long as their subscription runs, managed from a Telegram
**Mini App** and an owner-only **Bots** admin page. Profile `helper`; two
containers, `helper` and `docker-proxy`.

Dependency-free Python like `crypto/` and `stats/`. The bot itself uses **long
polling**, so nothing new listens on the internet for it. The Mini App/admin
server (`:8095`) is published only through two Caddy routes on secret paths
(see [Mini App and Bots page](#mini-app-and-bots-page)).

## Setup

1. Create a bot with **@BotFather** (`/newbot`) and copy its token.
2. Put it in `.env`, with your numeric Telegram id as the owner:

   ```ini
   HELPER_BOT_TOKEN=123456:ABC…
   HELPER_OWNER_ID=123456789
   ```

3. Add `helper` to `COMPOSE_PROFILES`, run `./bootstrap.sh` (it generates the
   Mini App / admin paths, the admin key and password, and builds the Mini App
   — needs `npm`), then `docker compose up -d` and `/start` in Telegram.

Don't know your id? Leave `HELPER_OWNER_ID` empty, message the bot — it answers
strangers with their ID — then set it and `docker compose up -d helper`.

Without access to `.env` (e.g. from a phone shell), `ctl.py token` stores the
token in `helper/data/helper.db` instead; `.env` wins when both are set.
`ctl.py status` shows which one is in use.

For a separate bot for members, see [Two bots](#two-bots) below.

## Who can do what

| | Owner | Member (active) | Stranger |
|---|:-:|:-:|:-:|
| 🩺 Health, 🔗 Links (`tools`) | ✅ | if the membership has `tools` | — |
| 🎬 Downloader (`youtube`) | ✅ (always) | while the owner has switched `youtube` on | — |
| VPN link (`vpn`), Matrix accounts (`matrix`) | ✅ | if the membership has the service | — |
| Redeem an access code | ✅ | ✅ (extends) | ✅ (becomes a member) |
| 🔑 Logins (`/pass`, `/setpass`, `/delpass`) | ✅ | — | — |
| Members and codes (`/members`, `/code`, `/allow`, `/deny`, 🎟) | ✅ | — | — |
| Bots page | ✅ | — | — |

The bot is public on Telegram — anyone can find it and write to it. A stranger is
told to send an access code or to contact `OWNER_CONTACT` (a Telegram `@handle`;
leave it empty and the bot just says "contact the owner"), and the owner gets one
notice about the new person. Members **never** get logins, whatever they send:
the owner check runs again on every button press, because a callback carries the
id of whoever pressed it. The bot also **never answers in groups**.

`/allow ID [days]` grants a membership without a code, `/deny ID` suspends it.

## Members and access codes

**Services** (per membership):

| id | Gives |
|---|---|
| `vpn` | one auto-updating 3x-ui subscription link |
| `matrix` | up to `MATRIX_MAX_PER_MEMBER` (default 2) Matrix accounts, created in the Mini App |
| `tools` | server health and hub links only (downloads are the separate `youtube` service) |
| `youtube` | the downloader; mode `all`, so it is **not grantable** and cannot go in a code — see [Service toggles](#service-toggles) |

**Status** — suspended (the owner's manual switch) beats expired (`expires_ts`
passed) beats active. **Expiry never deletes a member**: the row and the Matrix
accounts stay, the services stop, and a new code brings everything back.

**Access codes** look like `MEOW-XXXX-XXXX`. They are not bound to a user,
single-use by default, valid 30 days, and by default give 30 days of VPN +
Matrix.

- Redeeming starts or extends a membership: `max(now, expiry) + days`, services
  are the union. **A suspended member cannot un-suspend with a code.**
- 5 failed attempts per hour per user, then rate-limited.
- Create one with `/code [days] [vpn,matrix,tools]`, the 🎟 button wizard in the
  bot, or the Codes tab of the Bots page.
- Share it as the deep link `https://t.me/<your bot>?start=<CODE>` (redeems on
  tap; the admin page builds it for you) or as plain text.

**Enforcement** — what actually switches access off:

- **VPN**: one 3x-ui client per member (email `mh-<telegram id>`, a random
  `subId`) attached to the **member inbounds**. `enable` follows the membership
  and `expiryTime` mirrors the expiry, so 3x-ui cuts access at expiry on its own
  even if the bot is down. Deleting a member deletes the client. The link given
  to the member is `VPN_SUB_BASE` + subId, by default
  `https://<BASE_DOMAIN>/sub/…`, which Caddy proxies to the panel on 443 (many
  networks block the panel's own `:2096`).
- **Member inbounds**: the set of inbounds a member's client is attached to. On
  first run it is every enabled inbound of a member-friendly protocol (vless,
  vmess, trojan, shadowsocks, hysteria, mtproto) without a 🧪 in its remark; edit
  it on the Bots page afterwards. When a new inbound appears the owner gets
  "New inbound … give it to members?" with Add / Skip buttons. At most **one**
  WireGuard/AmneziaWG inbound may be in the set: 3x-ui hands a client that sits
  on two of them a wrong address.
- **Matrix**: a member's accounts are **locked** through the Synapse admin API
  while access is off (the account's rooms and history are kept; a locked user
  gets `M_USER_LOCKED`) and unlocked when it returns, which restores existing
  sessions. Deleting a member leaves the accounts locked.
- **Reconciler** (`members.py`): runs every 120 s and immediately on any change,
  and brings 3x-ui and Synapse in line with the database. Don't edit the `mh-…`
  clients in the panel by hand — the next pass reverts it.
- **Reminders**: to the member 3 days before expiry, 24 hours before (with the exact
  date and time) and at expiry; the owner gets an expiry notice with a "+30 days"
  button. The 24-hour one is skipped when the membership was started or extended in
  the last 2 hours (e.g. a 1-day code), so nobody is told "expires in 24 h" right
  after joining.

### Setting it up

The bot, codes and Mini App work as soon as the profile runs. The VPN and
Matrix halves are optional and each needs one manual step; without it that
half shows "not configured" and the rest keeps working.

1. **`OWNER_CONTACT`** — your `@handle`, shown to strangers.
2. **VPN** — in the 3x-ui panel (Settings → Security → API token) create a
   token and put it in `.env` as `XUI_API_TOKEN`. The panel stores only a hash,
   so a lost token means making a new one. Needs the panel from
   [VPN.md](VPN.md) running with a valid certificate (the bot verifies the TLS
   chain but not the hostname, because it connects through
   `host.docker.internal`); if your panel serves plain HTTP set `XUI_URL=http://…`.
   `XUI_URL` defaults to `https://host.docker.internal:$XUI_PANEL_PORT/$XUI_PANEL_PATH`.
3. **Matrix** — create an admin on your Synapse and put it in `.env`:

   ```bash
   docker compose exec matrix-synapse register_new_matrix_user \
       -c /data/homeserver.yaml -a -u admin       # asks for a password
   ```

   ```ini
   MATRIX_ADMIN_USER=admin
   MATRIX_ADMIN_PASSWORD=…
   ```

   The bot logs in once and caches the token (it re-logs in only on a 401,
   because Synapse rate-limits logins). `MATRIX_SERVER_NAME` defaults to
   `BASE_DOMAIN`.
4. `docker compose up -d helper`, then open the Mini App from the bot's menu
   button and the Bots page from the hub card, and mint a first code.

## Service toggles

Every member service has a **global on/off switch**, set by the owner in the helper
bot (`⚙️ Сервисы` / `/services`) or on the Bots page (Overview, "Сервисы для
участников"). It is stored in the `helper.db` setting `svc_enabled`. Defaults:
`vpn`, `matrix` and `tools` on, **`youtube` off** until you switch it on. The one
access rule (`members.has()`): the membership is active **and** the switch is on
**and** (the service is mode `all` **or** the member was granted it).

| Mode | Services | Meaning |
|---|---|---|
| `grant` | `vpn`, `matrix`, `tools` | given per member (codes, member card). The switch is a master kill-switch: off means VPN clients are disabled and Matrix accounts locked for everyone, and flipping it back restores them |
| `all` | `youtube` | switch on means every active member has it. No per-member grant, and asking for it in a code or on a member card is refused (`not_grantable`) |

A service that is switched off disappears from the member's app and keyboard.
API: `GET /api/admin/services` and `POST /api/admin/services/<id>` with
`{"enabled": true|false}`.

**Convention: every new member-facing service ships with such an owner toggle,
default off.** Add it to `SERVICES` and `SERVICE_DEFAULTS` in
`helper/app/members.py` (mode `grant` or `all`), gate every entry point with
`members.has(uid, "<id>")`, and the switch, the member app catalogue and the admin
card follow.

## Two bots

By default one bot does everything. If you also set a **member bot**
(`MEMBER_BOT_TOKEN`, or paste it on the Bots page — see [Tokens on the Bots
page](#tokens-on-the-bots-page)), the roles split:

| | Helper bot (`HELPER_BOT_TOKEN`) | Member bot (`MEMBER_BOT_TOKEN`) |
|---|---|---|
| For | you, the owner: health, links, downloads, logins, members and codes | members and strangers — **including you**: you are treated by your own membership (a member if you have one, else a stranger), with no owner menus |
| Anyone else writes to it | gets "this is a service bot, MeowHub is in @member_bot" with an *Open* button, and — if the text was an access code — a one-tap *Activate in @member_bot* | gets the member or stranger experience |
| Menu button | only your chat gets the "MeowHub" Mini App button; everyone else's is the plain command list | "MeowHub" → the Mini App |
| Commands | the full set | `start`, `help`, `vpn`, `matrix`, `sub` |

Both bots long-poll **in the same process** and share the database, the 3x-ui
and Synapse clients, the reconciler and the web server; each has its own update
offset. Messages to members (welcome, reminders, extend/suspend notices) go out
through the member bot, messages to you through the helper bot — one routing
function decides by recipient. "A stranger wrote to the bot" notices, with their
grant buttons, always come from the **helper** bot. Access-code share links
(`https://t.me/<member_bot>?start=<CODE>`) use the member bot's username.

The Mini App accepts Telegram's signed `initData` from **either** bot and
remembers which one it came from (`via`: `helper`, `member`, or `browser`
without Telegram). Opened through the member bot, even the owner is shown the
member or stranger view. With no member bot configured everything behaves as a
single-bot install. A member token that is invalid, or the same bot as the
helper's, is refused (and ignored at startup) rather than breaking the helper.

## Tokens on the Bots page

The owner's Bots page (and the Mini App's admin view) has a **Bots and keys**
block with four secrets. Each shows where its current value comes from
(`page`, `env`, `ctl` or none) and a masked form (first and last four
characters — a full token is never sent to the browser). **Change** validates
the new value *before* saving; **Reset** drops the page override so the `.env`
value applies again.

| Secret | `.env` fallback | Validated by | Applying a change |
|---|---|---|---|
| Helper bot | `HELPER_BOT_TOKEN` (then the legacy `ctl.py token`) | `getMe` | the container **restarts itself** about 1.5 s later (`restart:` policy brings it back with the new token) |
| Member bot | `MEMBER_BOT_TOKEN` | `getMe`, and it must not be the helper's bot | same restart |
| Crypto bot | `CRYPTO_TG_TOKEN` | `getMe` | **no restart** — pushed to the crypto tracker (below) |
| 3x-ui API token | `XUI_API_TOKEN` | listing inbounds with it | **hot-swapped** in the running process, no restart |

**Precedence everywhere: page override > `.env` > (helper bot only) the old
`ctl.py token` value.** A token that fails validation is never saved. Telegram
`getMe` answers are cached for 10 minutes and dropped on any change.

**Crypto bot.** The tracker is a separate container, so the helper hands the
override to the tracker's settings API (`CRYPTO_URL`, default
`http://crypto:9102/api/settings`, i.e. the `crypto` profile on the same
network) as `tg_token_override`. If the tracker is not running or refuses it,
the change fails with `crypto_apply_failed` and **nothing is saved**. In the
tracker the override wins over `CRYPTO_TG_TOKEN` and over a token typed in its
UI (see [CRYPTO.md](CRYPTO.md#setting-up-telegram)).

> **n8n caveat.** n8n keeps its **own encrypted copy** of the Telegram bot
> token in a credential. Changing the crypto bot here does not touch it, so if
> your n8n workflows send through that bot, update the token in the Telegram
> credential in n8n too. The Bots page shows this reminder next to the crypto
> row.

Because Telegram only lets one `getUpdates` poller per token, never put the
same token in two places that both poll.

## Previewing the member experience

The owner (opening the Mini App through the helper bot, or in a browser) gets
**View as a member** with three choices: *Guest*, *Member*, *Expired*. It
renders the real member screens with sample data inside a clear "Preview"
banner and an exit button. It makes **no API calls** — nothing is created and
nothing is sent — and the sample data is loaded only when a preview starts, so
it is not part of the main bundle. To see the real thing, open the member bot.

## VPN configs by app

`GET /api/vpn` still returns the flat `links` list and adds `groups`, which the
Mini App shows as sections named after what the user must install:

| Group | Contains | Apps |
|---|---|---|
| VLESS and Shadowsocks | plain `vless://`, `vmess://`, `trojan://`, `ss://` | Happ, v2RayTun, v2rayNG, Hiddify |
| 🧪 New protocols (test) | `vless://` with `encryption` other than `none`, or an `fm` parameter, or 🧪 in its name | recent Happ / v2RayTun |
| Hysteria2 (UDP) | `hysteria2://`, `hy2://`, `hysteria://` | Happ, Hiddify, v2RayTun |
| AmneziaWG | `vpn://` (an AmneziaVPN share link carrying a whole `.conf`) | AmneziaWG, AmneziaVPN |
| Telegram proxy | `tg://proxy` / `https://t.me/proxy` (MTProto), always rewritten to the `https://t.me/proxy?…` form so one tap opens Telegram | Telegram |

Empty groups are omitted. A link's name is its URL-decoded `#fragment` with the
trailing `-<client email>` removed (`MTProto-прокси` for an unnamed proxy link).

**Gotcha: the link host.** The panel builds share links with the host of the
*request*, and the helper asks over the Docker network, so raw links come back
as `host.docker.internal` (or `localhost`/`127.0.0.1`). The helper rewrites
those hosts to `BASE_DOMAIN` — both in the `@host:port` part and in `server=` of
proxy links, and inside a `vmess://` JSON blob — for `links` and `groups` alike.
A host that is already a real name is left alone. If a client imports a config
that points at `host.docker.internal`, that rewrite did not run.

### AmneziaWG for members

Put one AmneziaWG inbound in the member inbounds (at most one, see above) and each
member gets one peer, which expires with the membership. The "AmneziaWG" group
offers four things: **Открыть в AmneziaVPN** (one tap, below), **copy the `vpn://`
link** (AmneziaVPN), **download `meowhub-awg.conf`** (AmneziaWG) and a **QR** that
carries the `.conf` text (a long
config falls back to error-correction level L, and past that the app says to
download the file). The panel writes its own host (`localhost`) into the peer's
`Endpoint`; the helper rewrites it to `BASE_DOMAIN` in both the `vpn://` link and the
`.conf`, re-encoding the link exactly like its input. The `.conf` download is a
[signed link](#downloader) (kind `awg_conf`) that re-checks the member's `vpn`
access at download time.

**"Открыть в AmneziaVPN" (one tap).** The first link of the group is a signed link
(kind `awg_open`, 1 h) to an HTML hand-off page served by `/dl/<token>` (the app opens
it with `openLink`, since Telegram will not open custom schemes). AmneziaVPN registers
`vpn://` **only on Android**, where it shows the config for confirmation (not a silent
import). So the page looks at the User-Agent: Android gets an
`intent://<key>#Intent;scheme=vpn;package=org.amnezia.vpn;S.browser_fallback_url=<Google Play>;end`
link plus a plain `vpn://` fallback; iOS and desktop get a signed `meowhub.vpn` file
(kind `awg_vpn`, the key text -- AmneziaVPN opens `.vpn` files, not `.conf`, which iOS
hands to the AmneziaWG app; never put "backup" in the name, iOS AmneziaVPN would treat
it as a backup restore). Every variant also has "Скопировать ключ" and a store link;
there is no auto-redirect (Chrome needs a tap). The key is sent without its `#name`.
Hint shown in the group: "AmneziaVPN: «Открыть в AmneziaVPN» → «Подключиться».
AmneziaWG: скачай .conf → «+» → «Импорт из файла» (или QR)." See
[the research note](research/amneziavpn-deeplink-2026-10.md).

## Inbound auto-dating

Members cannot tell new configs from old ones in their apps, so every inbound's
remark starts with its creation date, `07.10.26 · Name` (`"DD.MM.YY · "`, on the
left because apps truncate long labels on the right). The
reconciler adds it **once**, the first time it sees an inbound it does not know
yet: it renames the inbound through the panel API (the full inbound is sent
back with only `remark` changed, so clients and keys survive) and the "new
inbound" notice already shows the dated name. An inbound whose remark already
starts with such a date is left alone, so renaming is idempotent; inbounds that
existed before the feature keep their names. A failed rename is logged and
skipped, never fatal.

## Mini App and Bots page

One backend (`helper/app/webapp.py`, stdlib HTTP server on `:8095`, also on
`127.0.0.1:$HELPER_LOCAL_PORT` for debugging), two Caddy routes, both
`handle_path`, both on a secret path generated by `bootstrap.sh`:

| | Path (`.env`) | Who | Auth |
|---|---|---|---|
| Mini App | `/$BOT_APP_PATH/` | members and the owner, opened from the bot's "MeowHub" menu button and its buttons | Telegram's signed `initData` (HMAC with the bot token, 24 h max age). The public route **strips `X-Admin-Key`** |
| Bots page | `/$BOT_ADMIN_PATH/` — hub card **Bots** | the owner, in a browser | basic auth as `BOT_ADMIN_USER`; Caddy then injects `X-Admin-Key: $BOT_ADMIN_KEY`, which the app treats as the owner |

The password is in `secrets/bot-admin-password` (the bcrypt hash in
`secrets/bot-admin.hash`, injected into the Caddyfile by `bootstrap.sh`), and the
owner can also get it from the bot with `/pass` → "bots".

Inside the Mini App a member sees their subscription, a VPN screen (the
subscription link with QR code and one-tap import for Happ, Hiddify, v2RayTun,
Streisand and v2rayNG), a Matrix screen (create and reset-password for their
accounts) and a place to enter a new code. The Bots page shows KPIs, a 30-day
activity chart, the bots (helper, member and the crypto tracker's, via `getMe`),
the tokens block, the reconciler's sync status, the member-inbounds editor, members with their
profile photos (cached 24 h in `helper/data/avatars/`) and codes.

In Telegram mode the avatars are fetched with the `initData` header and shown as
blobs, because an `<img>` tag cannot send headers (the endpoint would answer 401).

**`BOT_APP_PATH`, `BOT_ADMIN_PATH` and `BOT_ADMIN_KEY` carry `:?` guards in
caddy's `environment:`** — an empty value would turn the route into `/*` and
swallow the root domain, so `docker compose` refuses to run instead. They are
compose-level, so apply a change with `docker compose up -d --no-deps caddy`.

**Frontend**: `helper/webapp/` (Vite + React, `base: './'` so the bundle does not
depend on the secret paths). `bootstrap.sh` builds it to `helper/webapp/dist`
when `npm` is available (same as the dashboard); the container mounts it
read-only at `/webapp`. To rebuild by hand: `cd helper/webapp && npm install &&
npm run build` — no container restart. For development, `npm run dev` and open
`?mock=member|owner|stranger|expired&mode=tg&theme=dark&p=vpn|matrix|admin`
(the mock is dev-only and not in the production bundle). Every contact or
domain the UI shows comes from `/api/me`; nothing is hardcoded.
Append `&member_bot=1` to simulate a configured member bot, `&via=member` for the owner
opening the app through it, `&preview=guest|member|expired` to start a preview.

**API**: every POST must be `Content-Type: application/json` (the CSRF guard).
Modules: `webapp.py`, `members.py` (domain + reconciler), `xui.py`, `synapse.py`.

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

## Language

Everything the bots and the Mini App backend say exists in **Russian and English**
(`app/i18n.py`: the catalog `T = {"key": {"ru": …, "en": …}}`, `tr(lang, key, **vars)`; ~400 keys).
The Russian texts are the originals; add a key with both languages whenever you add a message
(`tests/test_lang.py` fails on a missing language or mismatched `{placeholders}`).

- **Preference** per Telegram id: `auto` | `ru` | `en` in table `prefs` (`uid, lang, lc, updated_ts`;
  `lc` = the last Telegram `language_code` seen, for ids without a members row). Default `auto`.
  `members.lang` is still Telegram's `language_code` and is not the preference.
- **One rule** — `i18n.resolve(pref, language_code)`: `ru`/`en` pref wins; `auto` follows Telegram
  (`ru`, `uk`, `be`, `kk` → Russian, any other non-empty code → English, empty → Russian).
  `i18n.lang_of(store, uid, code)` adds the lookups (pref → fresh code → remembered `lc` → members row → ru).
- **Switching**: `/lang` or the **🌐 Язык / 🌐 Language** button (member and owner keyboards, strangers
  too) → inline `Русский · English · Авто`, callback `lang:ru|en|auto`; it saves, confirms in the
  new language and re-sends the reply keyboard. In the app: `POST /api/lang {"lang": "auto|ru|en"}`
  → `{"lang": <effective>, "lang_pref": <pref>}`; `GET /api/me` carries both. The browser admin
  page (no Telegram user) reads and writes the **owner's** preference, so it is one setting.
- **Per request** (`webapp.py`): the header `X-Lang: ru|en` (the frontend's current UI language) wins,
  else the stored preference, else `language_code`. That language renders every JSON `message`
  (`ApiError(status, code, "api.key", **vars)` / `DlError` carry a catalog key and are rendered in
  `Handler._handle`), `result` messages, service names/descriptions, VPN group titles/hints and link
  names, downloader labels and job errors. `X-Warning` stays a machine code. Server-rendered pages:
  `/go/<app>?l=ru|en` (default ru), the AmneziaVPN hand-off page by the token's uid preference with
  `?l=` override. Data (ids, statuses, 3x-ui inbound remarks, Telegram profile names) is never translated.
- **Notifications** to someone (welcome, 24 h / 3 day / expired reminders, owner extend / suspend,
  Reconciler notices) use the *recipient's* language; owner notices (new inbound, stranger, health
  alerts, morning report) use the owner's.
- **Reply-keyboard routing** matches the labels of both languages (`bot.btn_key()`), so a keyboard
  left on screen after a switch keeps working.
- **Command lists** (`setMyCommands`): default scope = English, plus Russian lists for `ru`/`uk`/`be`/`kk`;
  the owner's chat has its own full list in the owner's language. A language change re-sends that
  chat's list (`refresh_commands`; for `auto` on a non-owner chat it deletes the chat scope so the
  language-coded defaults apply again) — best effort, async.
- Dates: Russian `12 ноября`, English `12 Nov 2026` (with time `12 Nov 2026, 14:05`).
- Left in Russian on purpose: the hub card names/descriptions from `services.json` (shown by 🔗 Links),
  `ctl.py` output, container logs.
- No `OWNER_CONTACT` set: messages that say "write {contact}" fall back to "the owner" / "владельцу"
  (the catalog gets an empty `c`, `i18n.tr` fills the wording); the Mini App hides the contact cell.
- Frontend strings live in `webapp/src/i18n.js` (`ru` and `en` dictionaries); `npm run build` runs
  `scripts/check-i18n.mjs` first and fails on a key or `{placeholder}` missing in either language.

## Downloader

Service `youtube` (an owner-switched, all-members service, off by default -- see
[Service toggles](#service-toggles); the owner always has it, in the helper bot).
Downloads happen in the Mini App page "Скачать" (`?p=downloads&url=…`). The bots do
**not** download or upload anything: a link sent to a bot is handed off to the app.

- **Engine**: MeTube at the internal `METUBE_URL` (default `http://metube:8081`) plus
  `/{METUBE_PATH}/`, pinned in compose (`METUBE_IMAGE`), with `DOWNLOAD_DIRS_INDEXABLE`
  off, 2 concurrent downloads and yt-dlp upgraded nightly. The public MeTube route is
  behind **basic_auth** (the same login as the Bots page); users never see it. MeTube
  links are never handed out.
- **Queue**: MeTube keys its queue and history by URL, so one global queue cannot
  serve several people. The helper keeps its own job table (`downloads` in
  `helper.db`): identical requests share one MeTube download (fan-out), and a different
  preset for the same URL waits its turn.
- **Folders**: one per user, `<download dir>/u<telegram id>`.
- **Presets**: video 360 / 720 / 1080 / best (mp4, h264 forced -- "1080" otherwise
  picks AV1 that phones cannot play), audio mp3 / m4a / opus. Extras in the app:
  subtitles (separate `.srt`), a from-to clip, and a playlist (first 10).
- **Limits** per person (the owner is exempt): `DL_ACTIVE` 2, `DL_PER_HOUR` 10,
  `DL_PER_DAY` 30; new jobs are refused below `DL_MIN_FREE_GB` (50) free on the
  download disk. All optional, see `.env.example`.
- **URLs**: `downloads.canonical_url()` runs in `submit()` before anything is stored or
  sent to MeTube. MeTube rewrites `youtu.be/<id>` to `youtube.com/watch?v=<id>` and jobs
  are matched to its items by URL, so a share link used to sit "running" forever. YouTube
  (`youtu.be`, `/shorts`, `/live`, `/embed`, `/v`, `m.`/`music.`, `watch?v=`) becomes
  `https://www.youtube.com/watch?v=<id>`; `list`/`index` are kept only with the
  "Плейлист" option (a `watch?v=X&list=Y` share otherwise downloads just the video; a
  bare `/playlist?list=` still needs the option); `si`, `is`, `feature`, `pp`,
  `ab_channel`, `utm_*` and `t` are dropped (MeTube would take `t` as a clip start).
  Other sites only lose `utm_*`/`si`. Matching compares canonical URLs; as a second line
  of defence a single unclaimed item of the same folder and kind added after the submit
  is taken as the job's.
- **Delivery**: only a **signed link** `https://<domain>/<BOT_APP_PATH>/dl/<token>`: an
  HMAC token (key in the `link_secret` setting) bound to the user and the job, valid
  about `METUBE_TTL_MIN`, shown as "Сохранить" in the app. The helper serves the file
  itself with `Range` support, `Content-Disposition` and CORS for web.telegram.org (what
  the Mini App's native `downloadFile` needs), re-checks the job and the service on
  every request, and the token never appears in logs. There is no Telegram upload and no
  chat progress message (Bot API caps uploads at 50 MB anyway).
- **Cleanup**: the `metube-janitor` deletes files `METUBE_TTL_MIN` minutes (default 30)
  after they were last changed, by ctime; "Удалить" in the app also removes the item in
  MeTube.
- **Bot**: a link (or `/yt <url>`) from the owner or a member with the service gets one
  message with an "Открыть загрузчик" `web_app` button that opens the app with the
  canonical URL filled in (plain text when no Mini App URL is configured). Members with
  the service get a "Скачать видео" keyboard button; members without it are told at most
  hourly that downloads are switched off.
- **Admin**: the Bots page "Загрузки" section (disk free, today's jobs and bytes,
  recent jobs).

## Logins

**🔑 Logins** shows a button per service; a press sends login and password:

- the password is under a **spoiler**, and the message is sent with
  `protect_content` (no forwarding, no saving);
- it **deletes itself after `HELPER_PASS_TTL` seconds** (default 60).

Where the logins come from:

| Source | What | Edit with |
|---|---|---|
| `.env` / `secrets/` | Nextcloud admin, crypto UI, VPN UI, bots admin page — mapped in compose as `VAULT_<ID>_USER` + `VAULT_<ID>_PASS` or `_PASS_FILE` | `.env`, then `docker compose up -d helper` |
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
docker compose up -d --build --no-deps helper   # after editing helper/app
cd helper/webapp && npm run build               # after editing the frontend, no restart
docker compose up -d --no-deps caddy            # after changing the app/admin paths or key
cd helper && PYTHONPATH=app python3 -m unittest discover -s tests
```

- **Forcing a sync**: the reconciler runs every 120 s and on every change; run it
  now with the sync button on the Bots page (the last result is shown there).
- **Backups**: back up `helper/data/` — it holds the members, codes, settings
  (cached Matrix admin token, member inbounds) and the logins added with
  `/setpass`.
- **Upgrading from an older checkout**: pull, run `./bootstrap.sh` (it adds the
  new `BOT_*` keys to your `.env` and builds the Mini App), then
  `docker compose up -d --build helper caddy`. Until then `docker compose` stops
  with "BOT_APP_PATH must be set" — that is the guard doing its job. The old
  `users` table is no longer used; anyone allowed there has to redeem a code or
  be granted with `/allow`.

## Gotchas

**3x-ui API** (Bearer `XUI_API_TOKEN`, 3.8.x — the notes in `helper/app/xui.py`
have the details):

- In `/clients/list` and `/get`, `id` is the DB row id and `uuid` is the VLESS
  id, while `/update` wants the VLESS id in `id`; `allowedIPs` reads back as a
  string but must be written as a list; `/get` wraps the row as
  `{client:{…}, inboundIds, …}`. `xui.py` maps all of this.
- `/clients/update` **replaces** the row, it does not patch, so the bot sends back
  the full record it read.
- The XTLS `flow` is decided by the panel per inbound (Vision only where the
  transport supports it), so one client attached to a Vision-Reality inbound and
  a plain XHTTP-Reality one works on both; the bot does not send a flow.
- `/clients/onlines` is a POST; deleting a client takes a required `keepTraffic`
  query parameter.
- One client on two WireGuard/AmneziaWG inbounds gets a wrong address — hence the
  one-AWG limit on member inbounds.

**Synapse**: the admin token comes from logging in once as `MATRIX_ADMIN_USER`.
`M_LIMIT_EXCEEDED` means something is logging in repeatedly; the bot caches the
token to avoid it.

**Telegram**: a Mini App button opens only `https` pages, so the one-tap VPN
import buttons go through a tiny redirect page (`/go/<app>?u=…`) in the app that
refuses any target not starting with `VPN_SUB_BASE`.
