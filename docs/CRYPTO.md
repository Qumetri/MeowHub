# Crypto tracker

Live prices, price-target and volatility alerts to Telegram, and a web UI on the
hub. Runs as the `crypto` service (container `${STACK_NAME}_crypto`), behind
the `crypto` compose profile.

Tracked out of the box: **BTC, ETH, ETC, RVN, GRAM, TRX, SOL** (Binance) and
**XMR** (Kraken). Coins are managed from the **Coins** tab — add, replace or
remove them yourself, no restart and no config file.

## Where it lives

| | |
|---|---|
| Web UI | `https://<domain>/${CRYPTO_PATH}/` — basic auth, user `CRYPTO_ADMIN_USER` |
| Loopback | `http://127.0.0.1:${CRYPTO_LOCAL_PORT}/` — no auth, for local debugging |
| Code | `crypto/app/` (backend), `crypto/web/` (page) |
| Database | `crypto/data/crypto.db` (sqlite — small, so it sits with the code rather than in `DATA_ROOT`) |
| Secrets | `CRYPTO_PATH`, `CRYPTO_ADMIN_USER` in `.env`; password in `secrets/crypto-admin-password` |
| Bot token | in the sqlite db, entered through the UI — **not** in `.env` |

The bcrypt hash is injected into the Caddyfile by `bootstrap.sh` from
`secrets/crypto-admin.hash` — kept out of `.env` because it starts with `$2a$`,
which shell sourcing and compose interpolation both mangle. Rotate with:

```bash
docker compose exec caddy caddy hash-password --plaintext '<new>' \
  > secrets/crypto-admin.hash && ./bootstrap.sh && docker compose restart caddy
```

Unlike the hub page and MeTube, this one is **not** secret-path-only. It stores a
Telegram bot token and can send messages as you, so it sits behind basic auth.

## Managing coins

The **Coins** tab lists everything tracked, with live price, 24h change, which
exchange it comes from, and how many targets reference it.

**Adding**: type a ticker (`XMR`) or a full symbol (`XMRUSDT`, `SOLUSD`). The
symbol is checked against the exchanges before it is accepted, then backfilled
and joined to the live stream — no restart.

**Removing**: the ✕ button. It also deletes that coin's price targets and
volatility rule, and says so before it does. Stored candles are kept, so adding
a coin back restores its chart history.

Removing a coin that ships by default makes it stay removed. Defaults seed the
first run only; re-seeding on every start would resurrect a coin you deliberately
deleted, which would make the remove button a lie.

**Logos** come from the [cryptocurrency-icons](https://github.com/spothq/cryptocurrency-icons)
pack (CC0), vendored into `crypto/web/icons/` — 483 of them, so a coin you add
later almost certainly has one. They are bundled rather than loaded from a CDN
on purpose: this page is behind auth on a private server, and pulling icons from
a third party would disclose which coins you track on every page load.

A coin with no logo falls back to a coloured ticker badge — GRAM is one, being
newer than the pack. The server sends the page the list of icons it actually
has, so the browser never requests one that isn't there.

## Listing health — why a tracked coin is re-checked

Validating a symbol when you add it is not enough. **A pair can be halted months
later, and the exchange keeps serving the price it froze at.** Both of the coins
this tracker started with hit exactly that:

| Coin | What happened | Frozen at | Real price |
|---|---|---|---|
| Monero | Binance halted `XMRUSDT`, Feb 2024 | ~$118 | ~$540 (Kraken) |
| Toncoin | rebranded to **Gram**; all `TON*` halted 2026-06-30 | $1.60 | `GRAMUSDT`, live |

Neither looked broken. Both showed a plausible number that simply never moved,
and alerts on them could never fire — the worst failure mode for an alerting
tool, because it is indistinguishable from a quiet market.

So every tracked coin is re-validated against its exchange **hourly, and at
startup**. A coin whose status is no longer tradable — or whose newest candle is
more than 3 hours old while the exchange claims otherwise — is marked in the
**Feed** column, struck through on its card, and announced once over Telegram:

```
🛑 TON is no longer reporting live prices
Symbol  TONUSDT (binance)
Reason  Binance status BREAK
```

**Replacing a renamed coin**: a halted row gets a **⇄** button. Give it the new
ticker and the coin is swapped in place — your price targets and volatility
settings move across with it. A rebrand should not cost you the alerts you set
up, which is why this is a distinct action rather than remove-then-add.

**An unreachable exchange is never treated as a delisting.** The two must not
look alike: reporting a rate-limit as "this coin is dead" would be a worse bug
than the one this check exists to catch. If an exchange cannot be reached the
cycle is skipped and every verdict is left untouched. Only a *successful*
response that reports a non-trading status, or omits the symbol entirely, marks
a coin halted.

Binance statuses are fetched for every coin in **one** batched `exchangeInfo`
call. Asking per-coin is what provoked the rate-limiting that exposed this in
the first place.

## Two exchanges, and why

| Source | Used for | History depth |
|---|---|---|
| **Binance** | everything it actually trades | 1000 one-minute candles |
| **Kraken** | what Binance does not | 720 one-minute candles |

Binance is tried first because its backfill is deeper. Kraken is the fallback.

This is not architecture for its own sake — **Monero forced it**. Binance halted
`XMRUSDT` in February 2024. The symbol is still listed, `/exchangeInfo` still
returns it, and `/ticker/price` still answers with a number. That number is the
price it froze at: about **$118**, while XMR actually trades near **$540**. A
tracker that believed it would have backfilled candles from 2024 and fired every
alert against a price that stopped meaning anything two years ago.

So a symbol is only accepted if its Binance status is `TRADING`; otherwise
Kraken is tried; if neither trades it, the coin is rejected with a message
saying why. Existence is not the same as being tradable, and the difference is
silent unless you check for it.

Everything downstream — alerts, charts, the daily summary — treats both sources
identically. Kraken publishes 24h change directly, so `open` is derived from it
to match Binance's shape.

## Setting up Telegram

In the **Telegram** tab:

1. Message **@BotFather** → `/newbot` → copy the token.
2. Paste it, press **Save**.
3. Send your new bot any message, then press **Detect chat** — it reads
   `getUpdates` and offers the chat IDs it finds, so you never have to look up a
   numeric ID by hand.
4. **Send test**, then tick *Alerts enabled*.

A bot cannot message you first; Telegram requires you to open the conversation.
That is why step 3 needs you to send something.

## Alert types

### Price targets

A level plus a direction. Fires on a **crossing**, not on a level:

- A target you add while the price is already past it does **not** fire
  immediately. It arms once the price moves back, then fires on a genuine
  crossing. The UI tells you when this happens.
- **Repeating** targets re-arm after firing, but only once price moves 0.3% clear
  of the level — otherwise a price hovering on the boundary would fire endlessly.
- **One-shot** targets disable themselves after firing (still listed, with a hit count).
- Per-target cooldown, default 15 minutes.

Both directions are supported because of short positions: `above` and `below` are
symmetric, not "alert" and "stop-loss".

### Fluctuation

Percent move over a rolling window, per coin. Default **3% in 5 minutes**, 30 min
cooldown.

The cooldown has a deliberate exception. After firing, the move is re-measured
**from the price at which it fired**, so a cascade — 3%, then another 3%, then
another — keeps alerting, while a market that spikes once and goes flat stays
quiet. A plain cooldown would hide exactly the move you most want to know about.

### Daily summary

Off by default. Enable it and pick an hour in the Telegram tab; it lists every
tracked coin with its price and 24h change, sorted by performance. **Send summary
now** fires one immediately.

### Quiet hours

`23-7` suppresses **volatility** alerts overnight. Price targets always fire —
those are levels you chose deliberately.

## How the data works

- **Live prices**: one WebSocket to `stream.binance.com` carrying `miniTicker`
  for every tracked symbol, roughly one update per second per active coin.
- **Thin coins go quiet.** `miniTicker` only pushes on change, so RVN or TON can
  go minutes without a tick. "No tick" is never treated as "no data" — a REST
  snapshot every 60s keeps 24h stats honest and catches any level crossed during
  a stream gap.
- **Charts**: 1-minute candles, backfilled 1000 deep per coin on first start.
  Live candles are built from ticks, then overwritten every 5 minutes with
  Binance's authoritative OHLCV (tick-built candles have unknown volume and can
  miss a wick between samples).
- **Retention**: 45 days of candles and events, pruned every 6 hours.

Everything is **stdlib Python** — the WebSocket client, HTTP server and Telegram
sender included — the same philosophy as `stats/server.py`. No dependencies to
audit or pull at build time for a path that alerts on money.

## Robustness

The feed is designed to survive weeks unattended:

- a watchdog reconnects when frames stop arriving for 45s,
- full-jitter exponential backoff (max 60s) so a Binance outage is not hammered,
- the connection is recycled at 20h, before Binance's own 24h cutoff,
- the REST snapshot repairs anything the stream missed.

Delivery failures never lose an alert: the event is recorded in the **Log** tab
marked unsent with the reason, so a bad token is visible rather than silent.

## Operations

```bash
docker compose ps crypto
docker compose logs -f crypto
docker compose up -d --build crypto      # after editing crypto/app or crypto/web
docker compose restart crypto
```

The page is static files served by the app, so a UI edit needs a rebuild
(`--build`) but no dashboard rebuild — it is not part of the Vite bundle.

**Adding a coin**: Alerts tab → *Track another coin* → any Binance spot symbol
(`ADA` or `ADAUSDT`). It is validated against Binance before being accepted, then
backfilled and added to the stream without a restart.

**Changing `CRYPTO_PATH`**: edit `.env`, then `./bootstrap.sh` (it re-renders the
Caddyfile and rewrites `dashboard/.env.local`, from which the hub card's URL is
built), then `docker compose up -d caddy` and rebuild the dashboard.

> **Caution:** an **empty `CRYPTO_PATH` turns the route into `/*`**, which would
> swallow the entire root domain. `bootstrap.sh` always gives it a value, so this
> only bites if you blank it by hand.

## Backups

`crypto/data/crypto.db` holds the bot token, every target and all price history.
Losing it means re-entering the token and the targets; the candles re-backfill
from Binance on their own.
