"""Daily digest: what each tracked coin did over the last N hours, and the news
headlines that might explain it. Consumed by the n8n summary workflow.

Two halves, deliberately separate:

  numbers    computed here from stored 1-minute candles -- range, the sharpest
             hour and when it happened, the 1h/4h momentum, and how the coin
             moved relative to the rest of the tracked market. A language model
             is handed these as settled facts and never derives a figure.

  headlines  last-24h news per coin from Google News RSS, plus market-wide
             headlines from crypto outlets, plus the exchanges' own
             announcements (Binance, OKX, Bybit, KuCoin, Bitget) --
             a delisting, a network upgrade or a deposit halt moves a price
             and rarely makes the news the same day. Filtered by *title* against the
             coin's name and aliases, because the search itself is loose: a
             query for "ETC" happily returns "Bitcoin ETC" (an exchange-traded
             commodity), and one for "Ethereum" returns Ethereum Classic news.

Stdlib only, like the rest of the tracker.
"""
import html
import json
import logging
import re
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

log = logging.getLogger("news")

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) meowhub-crypto/1.0"}
GNEWS = "https://news.google.com/rss/search?"
MARKET_FEEDS = [
    ("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("Cointelegraph", "https://cointelegraph.com/rss"),
    ("Decrypt", "https://decrypt.co/feed"),
]
PER_COIN = 6
PER_EXCHANGE = 3        # of a coin's PER_COIN, at most this many announcements
PER_MARKET = 8
CACHE_S = 900

# Names a coin also goes by in headlines. The coin's own name and ticker are
# always included; this is only for what cannot be derived from them.
ALIASES = {
    "GRAM": ["Toncoin", "TON", "Telegram Open Network"],
    "TRX": ["Tron"],
    "XMR": ["Monero"],
}
# Tickers that are ordinary words or other products. For these a bare ticker in
# a title proves nothing; only "$ETC", "(ETC)" or the full name count.
AMBIGUOUS = {"ETC", "TON", "ONE", "GAS", "SUN", "OM", "ATOM", "NEAR", "GRT", "IO"}

# Headlines that never explain a move: price-prediction listicles, prediction-
# market tickers ("BTC price on Sep 30 at 3am"), "best coin to buy" promos and
# paid press-release wires. Left in, they crowd out real news and give the model
# something plausible-sounding to cite as a reason.
NOISE_TITLE = re.compile(
    r"price prediction|prediction market|price on \w+ \d+|current price of|"
    r"(crypto|coin|altcoin)s? to buy|top \d+ (crypto|coin|altcoin)|"
    r"price analysis|should you buy|could (turn|make) \$|presale", re.I)
NOISE_SOURCE = {"openpr.com", "globenewswire", "prnewswire", "accesswire", "einpresswire"}

# Exchange announcements are mostly promotions. What is left after this filter
# is what can move a price: listings, delistings, network upgrades and forks,
# deposit/withdrawal suspensions, futures launches.
EXCH_NOISE = re.compile(
    r"reward|prize|campaign|competition|giveaway|voucher|airdrop|\bwin\b|"
    r"\bearn\b|\bAPR\b|promotion|referral|challenge|quiz|leaderboard|lucky|"
    r"red packet|bonus|cashback|trading fee|new user|carnival|festival|"
    r"wallet maintenance|resum", re.I)
BINANCE_LIST = ("https://www.binance.com/bapi/composite/v1/public/cms/article/"
                "list/query?type=1&catalogId={}&pageNo=1&pageSize=20")
BINANCE_DETAIL = ("https://www.binance.com/bapi/composite/v1/public/cms/article/"
                  "detail/query?articleCode={}")
BINANCE_URL = "https://www.binance.com/en/support/announcement/detail/{}"
# 48 new listings, 49 latest news, 161 delisting, 157 maintenance/upgrades.
# Activities (93), airdrops (128) and API notices (51) never explain a move.
BINANCE_CATALOGS = (48, 49, 161, 157)
# Delisting and upgrade notices name the coin in the body, not the title
# ("Notice of Removal of Spot Trading Pairs - 2026-10-02").
BINANCE_READ_BODY = (161, 157)

_cache = {}
_cache_lock = threading.Lock()


# ------------------------------------------------------------------ fetching --
def _get(url, timeout=15):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _parse_rss(raw, default_source=""):
    out = []
    for it in ET.fromstring(raw).iter("item"):
        title = html.unescape((it.findtext("title") or "").strip())
        src_el = it.find("source")
        source = (src_el.text or "").strip() if src_el is not None else default_source
        # Google News appends " - Source" to every title; the source is kept
        # separately, so strip it rather than feed it to the model twice.
        if source and title.endswith(" - " + source):
            title = title[: -len(" - " + source)].strip()
        try:
            ts = int(parsedate_to_datetime(it.findtext("pubDate") or "").timestamp())
        except (TypeError, ValueError):
            ts = 0
        if title:
            out.append({"title": title, "source": source or default_source,
                        "ts": ts, "url": (it.findtext("link") or "").strip()})
    return out


def _noise(it):
    return bool(NOISE_TITLE.search(it["title"])) or it["source"].lower() in NOISE_SOURCE


def _norm(title):
    return re.sub(r"[^a-z0-9]+", "", title.lower())[:70]


def _get_json(url, timeout=15):
    return json.loads(_get(url, timeout))


def _ms(x):
    try:
        return int(int(x) / 1000)
    except (TypeError, ValueError):
        return 0


def _body_text(body):
    """Binance article bodies are a JSON tree of {node, text, child}; flatten
    the text. Falls back to stripping tags if it is ever plain HTML."""
    try:
        tree = json.loads(body)
    except (TypeError, ValueError):
        return re.sub(r"<[^>]+>", " ", body or "")
    out = []

    def walk(n):
        if isinstance(n, dict):
            if isinstance(n.get("text"), str):
                out.append(n["text"])
            for v in n.values():
                if isinstance(v, (list, dict)):
                    walk(v)
        elif isinstance(n, list):
            for v in n:
                walk(v)
    walk(tree)
    return " ".join(out)


def _binance(since):
    out = []
    for cat in BINANCE_CATALOGS:
        d = _get_json(BINANCE_LIST.format(cat))
        for c in (d.get("data") or {}).get("catalogs") or []:
            for a in c.get("articles") or []:
                ts = _ms(a.get("releaseDate"))
                if ts < since:
                    continue
                it = {"title": a["title"], "source": "Binance", "ts": ts,
                      "url": BINANCE_URL.format(a["code"])}
                if cat in BINANCE_READ_BODY:
                    try:
                        det = _get_json(BINANCE_DETAIL.format(a["code"]))["data"]
                        it["body"] = _body_text(det.get("body"))[:6000]
                    except Exception as e:           # noqa: BLE001
                        log.info("binance article %s: %s", a["code"], e)
                out.append(it)
    return out


def _okx(since):
    d = _get_json("https://www.okx.com/api/v5/support/announcements?page=1")
    return [{"title": a["title"], "source": "OKX", "ts": _ms(a.get("pTime")), "url": a["url"]}
            for blk in d.get("data") or [] for a in blk.get("details") or []]


def _bybit(since):
    d = _get_json("https://api.bybit.com/v5/announcements/index?locale=en-US&limit=50")
    return [{"title": a["title"], "source": "Bybit", "ts": _ms(a.get("publishTime")),
             "url": a["url"]} for a in (d.get("result") or {}).get("list") or []]


def _kucoin(since):
    d = _get_json("https://api.kucoin.com/api/v3/announcements?pageSize=50&lang=en_US")
    return [{"title": a["annTitle"], "source": "KuCoin", "ts": _ms(a.get("cTime")),
             "url": a["annUrl"]} for a in (d.get("data") or {}).get("items") or []]


def _bitget(since):
    d = _get_json("https://api.bitget.com/api/v2/public/annoucements?language=en_US")
    return [{"title": a["annTitle"], "source": "Bitget", "ts": _ms(a.get("cTime")),
             "url": a["annUrl"]} for a in d.get("data") or []]


# Coinbase and Kraken are absent on purpose: their blogs sit behind a bot
# challenge that answers 403 to anything that is not a browser (Kraken's
# intermittently, which is worse -- a feed that fails some days), and neither
# publishes an announcements API.
EXCHANGES = [("Binance", _binance), ("OKX", _okx), ("Bybit", _bybit),
             ("KuCoin", _kucoin), ("Bitget", _bitget)]


def _exchange_items(since):
    """-> (announcements from every exchange in the window, errors)."""
    items, errors = [], []
    with ThreadPoolExecutor(len(EXCHANGES)) as ex:
        futs = [(name, ex.submit(fn, since)) for name, fn in EXCHANGES]
        for name, f in futs:
            try:
                items += f.result()
            except Exception as e:                   # noqa: BLE001
                log.warning("%s announcements failed: %s", name, e)
                errors.append(f"{name}: {e}")
    items = [it for it in items
             if it["ts"] >= since and it["title"] and not EXCH_NOISE.search(it["title"])]
    return items, errors


# ------------------------------------------------------------------ matching --
def _patterns(coin, all_coins):
    """-> (include regexes, exclude regexes) for titles about this coin."""
    tick, name = coin["ticker"], coin["name"]
    inc = [re.compile(r"\b" + re.escape(name) + r"\b", re.I),
           re.compile(r"\$" + re.escape(tick) + r"\b"),
           re.compile(r"\(" + re.escape(tick) + r"\)")]
    if tick not in AMBIGUOUS and len(tick) >= 3:
        inc.append(re.compile(r"\b" + re.escape(tick) + r"\b"))
    for a in ALIASES.get(tick, []):
        # An all-caps alias is matched case-sensitively: "TON" the coin, not "ton".
        flags = 0 if a.isupper() else re.I
        inc.append(re.compile(r"\b" + re.escape(a) + r"\b", flags))
    # "Ethereum" must not claim "Ethereum Classic" headlines when both are
    # tracked: exclude any tracked coin whose name contains this one's.
    exc = [re.compile(r"\b" + re.escape(o["name"]) + r"\b", re.I)
           for o in all_coins
           if o["ticker"] != tick and len(o["name"]) > len(name)
           and re.search(r"\b" + re.escape(name) + r"\b", o["name"], re.I)]
    return inc, exc


def _query(coin):
    terms = ['"%s"' % coin["name"]]
    for a in ALIASES.get(coin["ticker"], []):
        terms.append('"%s"' % a)
    if coin["ticker"] not in AMBIGUOUS:
        terms.append(coin["ticker"])
    return "(" + " OR ".join(terms) + ") crypto when:1d"


def _coin_headlines(coin, all_coins, since):
    url = GNEWS + urllib.parse.urlencode(
        {"q": _query(coin), "hl": "en-US", "gl": "US", "ceid": "US:en"})
    try:
        items = _parse_rss(_get(url))
    except Exception as e:                       # noqa: BLE001
        log.warning("news for %s failed: %s", coin["ticker"], e)
        return [], str(e)
    inc, exc = _patterns(coin, all_coins)
    seen, out = set(), []
    for it in sorted(items, key=lambda x: -x["ts"]):
        if it["ts"] and it["ts"] < since:
            continue
        t = it["title"]
        if _noise(it) or not any(p.search(t) for p in inc) or any(p.search(t) for p in exc):
            continue
        k = _norm(t)
        if k in seen:
            continue
        seen.add(k)
        out.append(it)
        if len(out) >= PER_COIN:
            break
    return out, ""


def _exchange_headlines(coin, all_coins, items):
    """Announcements naming this coin: in the title, or -- for delisting and
    upgrade notices -- in the body, where Binance lists the affected pairs."""
    inc, exc = _patterns(coin, all_coins)
    tick = re.escape(coin["ticker"])
    # In "ABC/BTC" the coin being listed or removed is ABC; BTC is only the
    # quote currency. Strip quote positions before matching, or every pair
    # removal against BTC/ETH would be reported as BTC/ETH news.
    as_quote = re.compile(r"\b[A-Z0-9]{2,10}/%s\b" % tick)
    base = re.compile(r"\b%s/[A-Z0-9]{2,10}\b|\(%s\)" % (tick, tick))
    seen, out = set(), []
    for it in sorted(items, key=lambda x: -x["ts"]):
        t = it["title"]
        tq = as_quote.sub(" ", t)
        hit = any(p.search(tq) for p in inc) and not any(p.search(tq) for p in exc)
        if not hit and it.get("body") and base.search(it["body"]):
            hit = True
            t = f"{t} (affects {coin['ticker']})"
        if not hit:
            continue
        k = _norm(t)
        if k in seen:
            continue
        seen.add(k)
        out.append({"title": t, "source": it["source"], "ts": it["ts"],
                    "url": it["url"], "kind": "exchange"})
        if len(out) >= PER_EXCHANGE:
            break
    return out


def _market_headlines(since):
    items, errors = [], []
    for name, url in MARKET_FEEDS:
        try:
            items += _parse_rss(_get(url), default_source=name)
        except Exception as e:                   # noqa: BLE001
            errors.append(f"{name}: {e}")
    seen, out = set(), []
    for it in sorted(items, key=lambda x: -x["ts"]):
        if (it["ts"] and it["ts"] < since) or _noise(it):
            continue
        k = _norm(it["title"])
        if k in seen:
            continue
        seen.add(k)
        out.append(it)
        if len(out) >= PER_MARKET:
            break
    return out, errors


# ------------------------------------------------------------------- numbers --
def _hhmm(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%H:%M")


def _stats(store, symbol, price, hours, now):
    cs = store.candles(symbol, since=now - hours * 3600, limit=hours * 60 + 30)
    if len(cs) < 30 or price is None:
        return {"coverage_h": round(len(cs) / 60, 1)}
    hi_c = max(cs, key=lambda c: c["h"])
    lo_c = min(cs, key=lambda c: c["l"])

    def since(sec):
        cut = now - sec
        for c in cs:
            if c["ts"] >= cut:
                return round((price - c["o"]) / c["o"] * 100, 2)
        return None

    # Sharpest 60-minute move, two pointers over the timestamps so a gap in
    # the candles cannot stretch a "1 hour" comparison across several hours.
    best, best_at, j = 0.0, None, 0
    for i, c in enumerate(cs):
        while cs[j]["ts"] < c["ts"] - 3600:
            j += 1
        if c["ts"] - cs[j]["ts"] >= 3000:
            m = (c["c"] - cs[j]["c"]) / cs[j]["c"] * 100
            if abs(m) > abs(best):
                best, best_at = m, (cs[j]["ts"], c["ts"])
    out = {
        "high": hi_c["h"], "high_at": _hhmm(hi_c["ts"]),
        "low": lo_c["l"], "low_at": _hhmm(lo_c["ts"]),
        "range_pct": round((hi_c["h"] - lo_c["l"]) / lo_c["l"] * 100, 2),
        "change_1h": since(3600), "change_4h": since(4 * 3600),
        "coverage_h": round((cs[-1]["ts"] - cs[0]["ts"]) / 3600, 1),
    }
    if best_at:
        out["sharpest_hour"] = {"pct": round(best, 2),
                                "from": _hhmm(best_at[0]), "to": _hhmm(best_at[1])}
    return out


def _one_line(msg):
    s = re.sub(r"<[^>]*>", "", msg or "")
    s = re.sub(r"\s*\n+\s*", " · ", s)
    return re.sub(r"\s{2,}", " ", s).strip()[:160]


# --------------------------------------------------------------------- build --
def build_digest(app, hours=24, with_news=True, fresh=False):
    now = int(time.time())
    since = now - hours * 3600
    rows = [r for r in app.coin_rows() if r.get("price") is not None]

    changes = [r["change24h"] for r in rows if r.get("change24h") is not None]
    avg = sum(changes) / len(changes) if changes else 0.0

    news, market, errors = {}, [], []
    if with_news:
        key = (tuple(sorted(r["symbol"] for r in rows)), hours)
        with _cache_lock:
            hit = _cache.get(key)
        # fresh: explaining a move that happened a minute ago -- a 15-minute-old
        # cache could predate the very headline that caused it.
        if hit and not fresh and now - hit[0] < CACHE_S:
            news, market, errors = hit[1]
        else:
            with ThreadPoolExecutor(8) as ex:
                per_coin = {r["symbol"]: ex.submit(_coin_headlines, r, rows, since) for r in rows}
                f_market = ex.submit(_market_headlines, since)
                f_exch = ex.submit(_exchange_items, since)
                exch, xerr = f_exch.result()
                for r in rows:
                    items, err = per_coin[r["symbol"]].result()
                    # Exchange notices first: rarer, and more often the cause.
                    ann = _exchange_headlines(r, rows, exch)
                    news[r["symbol"]] = (ann + items)[:PER_COIN]
                    if err:
                        errors.append(f"{r['ticker']}: {err}")
                market, merr = f_market.result()
            errors += merr + xerr
            with _cache_lock:
                _cache[key] = (now, (news, market, errors))

    events = app.store.events(limit=300)
    coins = []
    for r in rows:
        ch = r.get("change24h")
        vs = round(ch - avg, 2) if ch is not None else None
        rel = ("with_market" if vs is None or abs(vs) < 1.5
               else "outperformed" if vs > 0 else "underperformed")
        alerts = [_one_line(e["message"]) for e in events
                  if e["symbol"] == r["symbol"] and e["ts"] >= since
                  and e["kind"] in ("target", "fluctuation")][:5]
        coins.append({
            "symbol": r["symbol"], "ticker": r["ticker"], "name": r["name"],
            "health": r.get("health", "ok"), "health_note": r.get("health_note", ""),
            "price": r["price"],
            "change24h": round(ch, 2) if ch is not None else None,
            "vs_market_pp": vs, "relative": rel,
            **_stats(app.store, r["symbol"], r["price"], hours, now),
            "alerts": alerts,
            "headlines": news.get(r["symbol"], []),
        })
    coins.sort(key=lambda c: -(c["change24h"] or 0))
    btc = next((c for c in coins if c["ticker"] == "BTC"), None)
    return {
        "generated": now, "hours": hours,
        "market": {
            "avg_change": round(avg, 2),
            "up": sum(1 for c in changes if c > 0), "count": len(changes),
            "btc_change": btc["change24h"] if btc else None,
            "best": coins[0]["ticker"] if coins else None,
            "worst": coins[-1]["ticker"] if coins else None,
            "headlines": market,
        },
        "coins": coins,
        "news_errors": errors,
    }
