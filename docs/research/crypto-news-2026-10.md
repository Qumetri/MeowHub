# Crypto tracker news: audit and faster sources

Measured 2026-10-07, 22:00–23:00 UTC, from this server. **[V]** = verified live (GET); **[R]** = reported by docs or third parties.

## 1. Audit

**What `news.py` fetches.** It runs only when `/api/digest` is called, with a 15-minute cache. Callers are n8n at 09:00 and the move explainer (`fresh=1`, at most 20 a day). Each call fetches:
- 8 Google News searches, one per coin (English/US only);
- CoinDesk, Cointelegraph and Decrypt RSS;
- Binance catalogs 48, 49, 161 and 157, plus the OKX, Bybit, KuCoin and Bitget announcement APIs.

**There is no breaking-news path.** News appears only as context for a price alert or in the morning digest.

**Measured staleness [V]:**

| Feed | Result |
|---|---|
| Google News BTC | newest 10 min, 5th newest 58 min, median 7.4 h |
| Google News SOL | newest 57 min |
| Google News XMR | 9 items, newest 60 min |
| Google News ETC | 5 items, newest 6 h |
| Google News RVN | **0 items** |
| Google News indexing | Decrypt indexed within 34 min. **Cointelegraph is barely indexed:** `site:` query found 9 items, newest 12 h old, and none of its 5 articles from the last 6 h. |
| Exchange APIs | all 5 answer HTTP 200 in under 0.6 s. Newest item: Binance listings 18 h, delistings 6.8 d, Bybit 11 h, Bitget 10 h, KuCoin 18 h, OKX 63 h |

**Weaknesses (from the live digest) [V]:**
1. **Converter SEO pages pass the filter.** ETC's only headlines were "Convert 10 ZAR to ETC" from Bybit and OKX; GRAM got two more. They arrive via Google News under the exchange's name, so they look official.
2. **No near-duplicate dedupe.** 5 of TRX's 6 slots were one Polygon–TRON story.
3. **Off-topic market headlines.** 3 of 8 were Decrypt AI stories.
4. **Low-quality sources dominate.** SOL's top sources were Coin Gabbar (16) and openPR (8).
5. **OKX shows only OKX-Europe news.** From this EU IP it returns OKX-EEA items, and its newest "new listing" is 90 days old (inferred from content). Global OKX delistings are missed.
6. **Matching misses `TRXUSDT`-style titles.** Only Binance bodies are read.
7. **Gaps:** no feed for Kraken (XMR's only exchange), although `status.kraken.com/history.rss` works and mentions Monero [V]. No Korean exchanges and no macro calendar.
8. **Unstable feeds.** Decrypt RSS mixes in podcast items up to 9 months old [V]. Google News RSS is unofficial with no SLA [R]. Bithumb answered once, then returned 302 [V].

## 2. Sources compared

| Source | Latency | Coverage | Cost / auth | Protocol | Verdict |
|---|---|---|---|---|---|
| **Binance CMS list** [V] | seconds + poll interval | listings, delistings, upgrades, halts | free, no key | REST | **core** |
| Binance announcement WS [R] | push | same, with body | free; needs Binance API key (HMAC); 5 msg/s; 24 h connection | WS | optional |
| **Upbit notices** [V] | seconds | KRW listings, 유의 종목 (warning designation), deposit halts | free; ≤20 per page | REST | **use** |
| Bithumb notices [V] | seconds | Korean listings | free; 302 after the first call | REST | skip |
| **Kraken/Coinbase status** [V] | minutes | deposit halts (XMR!) | free | RSS | **use** |
| OKX/Bybit/KuCoin/Bitget [V] | seconds | own notices | free | REST | keep; fix OKX |
| Tree of Alpha [V/R] | seconds with key [R]; free WS sent 1 message in 10 min [V] | X, blogs, exchanges, US government | TREE tokens; delayed `/api/news` returned 502 [V] | WS | skip (cost, fragile) |
| Phoenix News [R] | seconds | 1,500+ sources | subscription/NFT, price not public | WS | skip |
| CryptoPanic [V] | minutes | aggregator | **free plan gone** (API rejects `developer`); Growth $50/wk [R]; RSS returns 410 | REST | skip |
| CoinDesk Data news [V] | minutes | broad | key required (401); free tier reportedly ended 2026-05 [R] | REST | skip |
| CoinGecko `/news` [V] | minutes | 100+ outlets | Pro only (401) | REST | skip |
| Messari news [R] | — | — | enterprise | REST | skip |
| **Publisher RSS** [V] | minutes; The Block's newest was 20 min old | quality outlets | free | RSS | **use; add The Block** |
| Google News [V] | 10 min to hours | broad, spammy | free, unofficial | RSS | small coins only |
| **Telegram `t.me/s/<channel>`** [V] | channel + poll interval; newest post: WatcherGuru 89 min, WuBlockchain 147 min | hacks, ETF, regulation | free, no account | HTML | **use 1–2 channels** |
| Telegram MTProto [R] | seconds | any channel | user account + library; ban risk | MTProto | no |
| X API [R] | seconds | everything | ~$0.005/post read; no free tier | REST | too costly |
| Nitter bridges [R] | — | — | X sent a cease-and-desist 2026-08-24 | RSS | dead |
| Whale Alert [R] | seconds | large transfers | $29.95/mo; no free tier | WS | low signal |
| Fed press RSS [V] | minutes | rate decisions | free | RSS | **use** |
| BLS CPI / Farside ETF flows [V] | — | macro dates / ETF flows | 403 to bots | HTML | hard-code dates |

Remaining FOMC meetings in 2026: Oct 27–28 and Dec 8–9 [V].

Sources: developers.binance.com/docs/cms/announcement · developers.binance.com/en/docs/products/announcements/general-info · docs.treeofalpha.com/websockets.md · docs.treeofalpha.com/welcome/how-to-subscribe.md · docs.phoenixnews.io/websocket/authentication.md · cryptopanic.com/developers/api/plans/ · coinstats.app/blog/top-coindesk-api-alternatives-for-crypto-data/ · docs.coingecko.com/reference/news-and-insights-overview.md · docs.messari.io/api-reference/permissions · developer.whale-alert.io/pricing.html · postproxy.dev/blog/x-api-pricing-2026/ · en.wikipedia.org/wiki/Nitter · federalreserve.gov/monetarypolicy/fomccalendars.htm · research.4pillars.io/en/data/content/the-dying-upbit-listing-pump

All 8 tracked coins are already listed everywhere, so new listings matter little. What moves them:
- **delistings and warning tags** (RVN, ETC and XMR are at risk);
- **deposit and withdrawal halts**;
- **hacks**;
- **regulation and macro**.


## 3. Recommended design

### (a) Breaking alerts: a `newswatch` thread in the tracker (stdlib, ~200 lines)

**Sources** (all keyless):

| Source | Endpoint | Poll |
|---|---|---|
| Binance | `bapi/composite/v1/public/cms/article/list/query?type=1&catalogId={48,161,157}&pageNo=1&pageSize=20`. Payload: `data.catalogs[].articles[]{code,title,releaseDate}` | every 30 s |
| Upbit | `api-manager.upbit.com/api/v1/announcements?os=web&page=1&per_page=20&category=trade`. Titles look like `블라스트(BLAST) 거래 유의 종목 지정 안내` (BLAST trading-warning notice) | every 60 s |
| Kraken and Coinbase status | `/history.rss` | every 2 min |
| One Telegram channel preview | `t.me/s/WatcherGuru` | every 60 s |

**Coin filter:**
- Reuse `_patterns()`.
- Upbit requires `(TICKER)`.
- Delisting notices are matched in the body on `TICKER/` or `TICKERUSDT`.

**Dedupe:**
- Store items in sqlite: `news_seen(source, ext_id, title_norm, url, published, first_seen, coins, score)`.
- Unique key: `(source, ext_id)`.
- Across sources, word-trigram Jaccard ≥0.6 within 6 h counts as one story. A story seen elsewhere raises the score instead of re-alerting.

**Score** (keyword rules):

| Event | Score |
|---|---|
| delist, monitoring tag, 유의 종목 (warning designation) | 100 |
| hack, exploit, drained, chain halt | 90 |
| SEC, ban, lawsuit, sanction, MiCA/AMLR | 70 |
| ETF approval or record flow | 60 |
| upgrade, hard fork, deposit suspension | 50 |
| listing | 30 |

**Sending:**
- **≥70 and the item matches a tracked coin:** push to Telegram.
- **≥90:** ignores quiet hours.
- **Everything else:** goes to the digest.
- At most 10 pushes a day.

### (b) Better context for the digest and the explainer

- `/api/digest` reads `news_seen` first, so the explainer gets `first_seen` stamps ("seen 4 min before the move") without refetching.
- Drop titles matching `^convert \d|: convert`.
- Cluster near-duplicates and report the cluster size ("5 outlets").
- Add The Block. Market headlines must contain a crypto keyword or coin.
- OKX: query `annType=announcements-delistings` and `…-deposit-withdrawal-suspension-resumption`. If still EEA-only, drop OKX.
- Add a macro line: today's FOMC or CPI from a hard-coded yearly list, plus the latest Fed press item.
- Keep Google News only for sparse coins (RVN, ETC, XMR, GRAM).

### (c) Ongoing freshness measurement

- Log `first_seen − published` per source. Expose p50/p90 and "last new item" as `news_health` in `/api/digest`.
- The helper bot alerts on 3 errors in a row, or on silence beyond the normal gap. Examples: a Binance catalog with nothing new for 72 h; Google News returning 0 for BTC.
- Weekly **explain coverage**: the share of volatility alerts with a headline within ±60 min.

## 4. What the owner must sign up for

- **Nothing** for the recommended core.
- **Optional, free:** a read-only Binance API key (needs a KYC'd account) for the push WebSocket.
- **Not recommended:**

| Option | Cost |
|---|---|
| CryptoPanic Growth | $50/wk or $199/mo |
| Tree of Alpha | TREE tokens |
| Whale Alert | $29.95/mo |
| X API | pay-per-use |
| Telegram MTProto user account | free, but fragile |

## What we did

Implemented §3 on 2026-10-08 (details in [`../CRYPTO.md`](../crypto.md), "Newswatch (breaking news)"):
- `crypto/app/newswatch.py`: Binance catalogs 48/161/157, Upbit, Kraken and Coinbase status, Telegram channel previews (`WatcherGuru`), and OKX/Bybit/KuCoin/Bitget, with the scoring table above, a sqlite `news_seen` table, trigram dedupe and pushes under `news_min_score` / `news_max_per_day` / quiet hours. OKX now queries the delisting and deposit/withdrawal types.
- `/api/digest` reads `news_seen` first, drops converter pages, clusters near-duplicates, adds The Block, uses Google News only for sparse coins, and carries `macro` (hard-coded 2026 FOMC dates, verified on federalreserve.gov, plus the newest Fed release) and `news_health`.
- Not done: helper-bot alerting on `news_health`, the weekly explain-coverage figure, an update of the n8n explainer prompt to use `first_seen` ("seen N min before the move"), 2027 FOMC dates, Binance's push WebSocket.
- Open: from this server OKX still looks EEA-only; WuBlockchain was the second channel considered (add it in the Новости tab).
