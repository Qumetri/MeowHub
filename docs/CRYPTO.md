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
| Bot token, chat id | the Telegram tab, or `.env` (`CRYPTO_TG_TOKEN`, `CRYPTO_TG_CHAT_ID`) — `.env` wins and locks the UI fields |

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

**Or in `.env`**:

```ini
CRYPTO_TG_TOKEN=123456:ABC…
CRYPTO_TG_CHAT_ID=123456789
```

then `docker compose up -d crypto`. Set there, they **override** the UI and the
Token / Chat ID fields become read-only ("set in .env"); delivery is switched on
once automatically, and the *Alerts enabled* switch still works after that.
A value saved in the UI remains only as a fallback for when the variable is
empty. **Send test** and **Detect chat** use the effective (`.env`) token.

**Third source: the helper bot's Bots page.** With the [helper bot](HELPER.md#tokens-on-the-bots-page)
you can change the crypto bot's token from the owner's Bots page; it is sent to
the tracker as `tg_token_override` (empty clears it). Effective token:

1. the override from the Bots page,
2. `CRYPTO_TG_TOKEN` in `.env`,
3. the value saved in the Telegram tab.

The Telegram tab says which one is in use and read-only-locks the field whenever
the override or `.env` supplies it. Resetting the override on the Bots page falls
back to `.env`. The override is stored in `crypto.db` like the other settings and
is applied immediately, no restart. n8n keeps its own copy of the token in its
Telegram credential — change it there too if workflows send through this bot.

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

A swing of **5% within an hour**, per coin (both are editable per coin in the
Alerts tab: *Step*, *Window*, *Urgent*, *Pings*).

It works in **episodes**. An episode opens when price moves one step away from
the window's **low** (a rise) or **high** (a fall). It is then measured from
that fixed anchor, and every further whole step is reported again:

| Move within the hour | What you get |
|---|---|
| 5% | one alert |
| 10% | an 🚨 alert **sent 3 times**, a minute apart |
| 15%, 20% … | one more 🚨 alert per step |

Rises and falls are symmetric and marked so they can be told apart at a glance
in a notification: 📈 up / 📉 down, 🚨📈 / 🚨📉 past the urgent level. (The
first version used 🚀 for rises and ⚠️ for falls, which read as "rocket" and
"generic warning" rather than as a direction.)

The repeats are not copies. Each follow-up re-reads the live price, so the
second and third message say whether the move is *still* running or has
retraced — that is the thing you want to know a minute later.

Design points, each of which exists because the simpler version was wrong:

- **Low/high of the window, not "price one hour ago".** Down 4% and then up 6%
  off the bottom is a 6% move inside the hour, even though it is +2% end to end.
  Comparing with the price exactly an hour ago misses it.
- **Steps from a fixed anchor, not a cooldown.** A cooldown would swallow the
  second half of a 5% → 10% crash — the part you most need.
- **A reported move is never reported twice.** When no episode is open, only
  prices *since the last alert* are considered, so a move that stops and goes
  flat does not re-fire when the episode expires.
- **A counter-move is measured from the move's own extreme — one reference.**
  Up 20% and then 5% off the top is reported **once**, as
  "↘️ pulling back −5% from the high" (a fall: "↗️ bouncing"). Another note comes
  only at the next whole step off the top (−10%, −15% …), and the count resets
  only when the move makes a new step. A pullback is never a burst and is not
  sent to n8n for an explanation.
  *Why:* the first version measured a reversal against the window's opposite
  extreme. Each reversal then became the reference for the next one, and on
  2026-10-05 RVN (+20%, then chopping ±6% under the top) alternated
  "up +8%" / "down −5.7%" on **every tick** — 176 messages in 10 minutes.
- **The move only turns when it is given back.** Once price crosses back
  through the anchor, the episode becomes a move the other way, measured from
  the old extreme; the pullback steps already sent count as its steps, so a
  20% rise that rolls over into a crash reports −5, −10, −15 off the top and
  then "down −20% — still going".
- **Chop is muted.** Within one window only a *deeper* pullback/bounce than the
  last one sent goes out, so a coin swinging 6% back and forth gives one note an
  hour, not one per swing. The steps of the move itself are never muted.
- **Urgent moves ignore quiet hours.** Ordinary 5% alerts respect them — but the
  episode is still recorded, so the same move is not announced when quiet hours
  end.

Covered by `crypto/tests/test_fluctuation.py` (stdlib, no network or DB:
`python3 crypto/tests/test_fluctuation.py`) — flat market, V-shape, steps,
urgent burst, flash crash, the 2026-10-05 chop, a top that rolls over, sideways
chop, dead-cat bounces, quiet hours. Run it after any change to `alerts.py`.

Changing *Step* or *Window* on a coin closes its open episode.

### Why did it move — explanation under the alert

After a volatility alert the tracker asks n8n to explain it, and the answer
arrives **as a reply under the alert** a few seconds to a minute later:

> 🔎 📉 **Почему SOL −5.12% за 60 мин**
> Явной новостной причины пока нет… похоже на локальное движение, рынок почти стоял.
> *Рынок за час: BTC +0.30% · в среднем по остальным +0.35% · в ту же сторону 0 из 5*

- **When:** on the first alert of a move, and again when it first turns urgent
  (10%). Not on every intermediate step. At most once per coin per 30 min and
  20 a day (`CRYPTO_EXPLAIN_MIN_GAP_S`, `CRYPTO_EXPLAIN_DAILY_MAX`), so a
  market-wide crash cannot burn the free model quota. Alerts silenced by quiet
  hours are not explained.
- **How:** `app/explain.py` POSTs the facts of the move (ticker, direction, %,
  from/to price, window, the alert's Telegram `message_id`) to
  `CRYPTO_EXPLAIN_HOOK` in a background thread — **the alert never waits for
  it**. n8n's *Crypto move explainer* reads `/api/digest?fresh=1` (bypasses the
  15-min news cache — a cached copy could predate the headline that caused the
  move) and decides **in code** whether it was the whole market or just this coin
  (share of coins that moved ≥1% the same way within the hour, and the average).
- **The model only adds the reason**, citing numbered headlines — each with its
  age ("10 мин назад"), exchange announcements marked as official — which become
  links; with no fitting headline it says so. Same honesty rules as the daily
  summary ([N8N.md](N8N.md)).
- **Auth:** the webhook is reachable through n8n's public route, so it requires
  header `X-Hook-Secret` = `CRYPTO_EXPLAIN_SECRET` (`.env`), matched by the n8n
  credential *Crypto hook secret*. Without it: 403.
- Empty `CRYPTO_EXPLAIN_HOOK` turns the feature off (the default).

**Turning it on** (needs the `n8n` profile):

1. n8n → *Import from File* → `n8n/workflows/crypto-move-explainer.json`.
2. **Webhook** node → new *Header Auth* credential: name `X-Hook-Secret`, value
   = `CRYPTO_EXPLAIN_SECRET` from `.env`.
3. **OpenRouter** node → your OpenRouter credential (without it every run uses
   the local model). **Reply under the alert** → the same Telegram bot and chat
   id as the tracker.
4. Publish the workflow, set `CRYPTO_EXPLAIN_HOOK=http://n8n:5678/webhook/crypto-move`
   in `.env`, and `docker compose up -d crypto`.

### Daily summary

The tracker's own summary (Telegram tab) is a plain price list and is **left
off**: the daily summary is the n8n workflow, which explains the moves — see
[N8N.md](N8N.md). It reads everything it needs from one endpoint:

### `GET /api/digest?hours=24`

Internal only (`crypto:9102` on the compose network, `127.0.0.1:8084` on the
host). One call returns, per coin:

- **numbers from the tracker's own candles** — 24h change, range, high/low and
  when they happened, the sharpest 60-minute move and its time window, 1h/4h
  momentum, and `relative`: `with_market` / `outperformed` / `underperformed`
  against the tracked average (±1.5pp);
- the coin's **alerts** in the period, one line each;
- up to 6 **headlines** from the last 24h — what [newswatch](#newswatch-breaking-news)
  has already stored first (highest score first, each with `first_seen`, the time
  this server first saw it, absent on rows stored by a source's silent first run),
  then exchange announcements fetched now (at most 3 in all), then Google News;
- every headline keeps `title`, `source`, `ts`, `url`, `kind` and gains `score`,
  `first_seen`, and — when two or more sources carried the same story
  (word-trigram Jaccard ≥ 0.6 within 6 h) — `cluster_size`, `cluster_note`
  (`"5 источников"`) and `cluster_sources`. `kind` is `exchange` for an
  exchange's own notice or status page, `social` for a Telegram channel.

Plus market-wide headlines (CoinDesk, Cointelegraph, Decrypt, The Block — each
must contain a crypto/macro keyword or a tracked coin, which drops Decrypt's AI
stories; Decrypt items older than 48 h are dropped, its feed carries podcast
backfill), aggregate stats, and two blocks added in 2026-10:

- `macro` — `{line, fomc_next, days_to_fomc, fomc_today, fed_press}`: the 2026 FOMC
  calendar is hard-coded in `news.FOMC_2026` (Jan 27–28, Mar 17–18, Apr 28–29,
  Jun 16–17, Jul 28–29, Sep 15–16, Oct 27–28, Dec 8–9; checked against
  federalreserve.gov 2026-10-08 — **add 2027 before January**), and `fed_press`
  is the newest item of `federalreserve.gov/feeds/press_all.xml` when under 24 h old;
- `news_health` — per-source freshness, see Newswatch below. Also at `GET /api/news/health`.

News is cached for 15 minutes; all sources are fetched in parallel, so a
cold call takes ~1–4s. `news=0` skips it. `hours` is capped at 72.

**Google News is now only for sparse coins** — RVN, ETC, XMR, GRAM, plus any coin
for which newswatch stored fewer than 3 items in the last 24 h (`news.GNEWS_ALWAYS`,
`GNEWS_MIN_WATCH`). Converter SEO pages ("Convert 10 ZAR to ETC") are dropped
as noise.

**Exchange announcements** come from the exchanges' own public APIs — no key:
Binance (listings, news, delisting, maintenance/upgrades — not activities,
airdrops or API notices), OKX, Bybit, KuCoin and Bitget. A delisting, a network
upgrade or a deposit halt moves a price and rarely makes the news the same day.
They carry `"kind": "exchange"` and are matched to a coin by title, except
Binance delisting/upgrade notices, whose titles are generic ("Notice of Removal
of Spot Trading Pairs") — for those the article body is read and `RVN/…` or
`(RVN)` counts, and the title gets "(affects RVN)" appended. In a pair only the
**base** counts: "ABC/BTC" is about ABC, never BTC. Promotions, competitions,
airdrops, "earn"/APR offers and wallet maintenance/resumption are dropped.
Most days none match the tracked coins; that is expected. Coinbase's and Kraken's
*blogs* are absent: both sit behind a bot challenge (403, Kraken's
intermittently) and have no announcements API — their **status pages'** RSS is
polled by newswatch instead. OKX is queried by type (`announcements-delistings`,
`announcements-deposit-withdrawal-suspension-resumption`); from an EU IP it still
returns mostly OKX-EEA notices, so global OKX delistings may be missed.

Headlines come from Google News RSS per coin, then are **filtered by title**,
because the search is loose:

- `ETC` returns "Bitcoin **ETC**" — an exchange-traded commodity. Ambiguous
  tickers (`ETC`, `TON`, …) only count as `$ETC`, `(ETC)` or by full name.
- "Ethereum" returns Ethereum Classic news. A coin never claims headlines naming
  another tracked coin whose name contains its own.
- Price-prediction listicles, prediction-market tickers ("BTC price on Sep 30 at
  3am"), "crypto to buy" promos and paid press-release wires are dropped. They
  never explain a move, and left in they give a model something
  plausible-sounding to cite.

Small coins (RVN, ETC) often have **no** relevant headline on a given day. That
is reported as such, never padded.

### Quiet hours

`23-7` suppresses **volatility** alerts overnight. Price targets always fire —
those are levels you chose deliberately.

## Newswatch (breaking news)

`app/newswatch.py` — a thread started from `main.py` that polls the sources which
publish delistings, halts and hacks first, and pushes the few items that can move a
tracked coin to Telegram within seconds to minutes, instead of waiting for the 09:00
digest. Design and the measurements behind it:
[`research/crypto-news-2026-10.md`](research/crypto-news-2026-10.md) §3.

**Sources** (all keyless; each has its own interval and failure handling — a dead
source never delays another, every request has a 10 s timeout):

| Key | Endpoint | Every |
|---|---|---|
| `binance-48` / `-161` / `-157` | Binance CMS catalogs: new listings / delistings / maintenance & upgrades. Bodies of 161 and 157 are read (only for new items < 48 h old) to find the pairs | 30 s |
| `upbit` | `api-manager.upbit.com/api/v1/announcements?category=trade`. A coin counts only as `(TICKER)` in the title | 60 s |
| `tg:<channel>` | `t.me/s/<channel>` preview HTML (`data-post` id, `.js-message_text`, `<time datetime>`). Channels: setting `news_tg_channels`, default `WatcherGuru` | 60 s |
| `kraken_status`, `coinbase_status` | `status.{kraken,coinbase}.com/history.rss` — deposit/withdrawal halts (XMR is on Kraken only) | 120 s |
| `okx`, `bybit`, `kucoin`, `bitget` | the same fetchers as the digest (`news._okx` …) | 120 s |

**Coin filter**: `news._patterns` (so a bare "ETC" is ambiguous and needs `$ETC`,
`(ETC)` or "Ethereum Classic"); pair notation `ETC/USDT`/`ETCUSDT` counts, a quote
position (`ABC/BTC`) does not; Binance notice bodies are matched on `TICKER/…`,
`(TICKER)`, `TICKERUSDT`. Promotions (`news.EXCH_NOISE`) and noise
(`news._noise`: price predictions, converter pages, press-release wires) are dropped.

**Score** (highest matching rule wins; case-insensitive, word-bounded, `SEC` is
case-sensitive; Binance catalog 161 floors at 100; an Upbit designation or a Binance
tag being *lifted* scores 40):

| Score | Keywords |
|---|---|
| 100 | delist, monitoring tag, removal of … trading pairs, 유의 종목, 상장폐지, 거래지원 종료 |
| 90 | hack, exploit, drained, chain/network halt/stall/outage |
| 70 | SEC, ban, lawsuit, sue, sanction, MiCA, AMLR |
| 60 | ETF with approval / record / inflow / outflow |
| 50 | upgrade, hard fork, deposit/withdrawal suspension or delay, funding delays, 입출금 중단 |
| 30 | listing (incl. 신규 거래지원) |

**Push rule** — all must hold:

1. published < 6 h ago (or undated) and not a repeat of a story already seen;
2. it names a tracked coin and `score ≥ news_min_score` (default 70) — **or**, for
   Telegram-channel and status-page items only, it names no tracked coin, scores
   `≥ max(90, news_min_score)` and mentions a market keyword (`SEC ETF Fed FOMC CFTC DOJ`,
   Binance/Coinbase/Kraken/OKX/Bybit, Tether, stablecoin, crypto, Bitcoin, Ethereum);
   such a message says **РЫНОК** instead of a ticker. SEC/ETF/Fed-only items (70/60)
   therefore stay in the digest unless they name a tracked coin;
3. `score ≥ 90`, or not in `quiet_hours` (the same setting as volatility alerts);
4. fewer than `news_max_per_day` (default 10) pushes in the last 24 h.

Anything else is only stored and shows in the digest and the **Новости** tab. A push is
sent with the tracker's own bot and chat (including the `tg_token_override` from the
Bots page) and logged as an event of kind `news`:
`📰 <b>RVN</b> · Binance · 🔴 критично` / linked title / `опубликовано 06:58 UTC · замечено через 2 мин`.

**Seeding**: a source's first run (empty database for it, or the first run after
`news_enabled` was switched back on) stores what is there and sends nothing, so a
restart never floods Telegram; a restart *with* history announces whatever appeared
while the container was down (subject to rule 1).

**Dedupe**: `news_seen` rows whose titles share ≥ 60 % of word trigrams (Jaccard) within
6 h **and name the same tracked coins** form a cluster; a later member is stored
(raising the cluster's count) but never pushed. The coin condition keeps
"Notice of Removal of Spot Trading Pairs" for RVN from swallowing the one for ETC.

**Table** `news_seen` (sqlite, rows pruned after 14 days): `source, ext_id` (unique),
`title, title_norm, url, published, first_seen, coins` (comma-separated tickers),
`score, cluster` (id of the cluster's first row), `pushed`, `seeded`. `seeded` marks
silent first-run rows, whose `first_seen` is the seeding time and which are left out of
latency figures.

**Settings** (Новости tab, `POST /api/settings`): `news_enabled` (`1`; `0` leaves the
thread idle), `news_min_score` (70), `news_max_per_day` (10), `news_tg_channels`
(`WatcherGuru`; up to 5 public channels, comma-separated).

**Health** — `GET /api/news/health` and `news_health` in `/api/digest`: per source
`last_ok`, `consecutive_errors`, `last_error`, `last_new_item`, `items_7d` and
`latency_p50_s` / `latency_p90_s` (first_seen − published over 7 days, live rows only;
floor = the poll interval). No alerting on it yet — the data is there for the helper
bot (3 errors in a row, or silence beyond the normal gap).

`GET /api/news?limit=50` returns the latest rows for the tab. Tests:
`python3 -m unittest discover -s crypto/tests` (fixtures are trimmed live captures).

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
