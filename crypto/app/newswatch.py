"""Breaking-news watcher: polls exchange and news sources, scores what it finds,
and pushes the few items that can move a tracked coin to Telegram.

The digest (news.py) only looks at news when something asks for it, so a
delisting announced at 14:02 reached the owner at 09:00 next day. This thread
polls the sources that publish such things first, within seconds of the item
appearing:

  Binance CMS catalogs 48 (listings), 161 (delistings), 157 (maintenance/upgrades)   30 s
  Upbit announcements (trading-warning designation, KRW listings)                   60 s
  Telegram channel previews t.me/s/<channel> (default WatcherGuru)                  60 s
  Kraken and Coinbase status RSS (deposit/withdrawal halts; XMR is on Kraken)       120 s
  OKX, Bybit, KuCoin, Bitget announcement APIs (fetchers shared with news.py)       120 s

Every source has its own interval and its own failure handling: one dead source
never delays another (each poll runs in a worker, timeout <= 10 s per request).
Each source's first successful run SEEDS: its current items are stored and
nothing is sent, so a fresh database or a long pause never floods Telegram.

Per item: coin filter -> keyword score -> dedupe against everything seen in the
last 6 h (word-trigram Jaccard, same coins) -> push decision.

Push rule (documented in crypto.md):
  push  <=>  fresh (published < 6 h ago, or undated)
         and not a repeat of a story already seen (cluster)
         and ( it names a tracked coin and score >= news_min_score
               or it comes from a Telegram channel / status page, names no
                  tracked coin, scores >= max(90, news_min_score) and mentions
                  a crypto-market keyword (MACRO_KW) )
         and ( score >= 90 or not in quiet hours )
         and fewer than news_max_per_day pushes in the last 24 h.
Everything else is only stored; the digest picks it up.

Stdlib only, like the rest of the tracker.
"""
import html
import json
import logging
import math
import re
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import xml.etree.ElementTree as ET

import news
from alerts import in_quiet_hours

log = logging.getLogger("newswatch")

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) meowhub-crypto-newswatch/1.0 "
                    "(personal tracker; polite polling)"}
TIMEOUT = 10                    # per request, seconds
PUSH_MAX_AGE_S = 6 * 3600       # an item published longer ago is never pushed
BODY_MAX_AGE_S = 48 * 3600      # Binance bodies are only read for recent notices
HEALTH_WINDOW_S = 7 * 86400
STRONG = 90                     # at or above: ignores quiet hours, may push without a coin

UPBIT_URL = ("https://api-manager.upbit.com/api/v1/announcements"
             "?os=web&page=1&per_page=20&category=trade")
UPBIT_NOTICE = "https://upbit.com/service_center/notice?id={}"
KRAKEN_STATUS = "https://status.kraken.com/history.rss"
COINBASE_STATUS = "https://status.coinbase.com/history.rss"
TG_PREVIEW = "https://t.me/s/{}"
BINANCE_CATS = ((48, 30), (161, 30), (157, 30))     # (catalogId, poll seconds)
# Catalog 161 is the delisting catalog: whatever it carries is a removal.
BINANCE_FLOOR = {161: 100}

LABELS = {"upbit": "Upbit", "kraken_status": "Kraken status",
          "coinbase_status": "Coinbase status", "okx": "OKX", "bybit": "Bybit",
          "kucoin": "KuCoin", "bitget": "Bitget"}


def label_of(key):
    if key.startswith("tg:"):
        return "Telegram @" + key[3:]
    if key.startswith("binance"):
        return "Binance"
    return LABELS.get(key, key)


def kind_of(key):
    """'exchange' = an exchange's own notice (official), 'social' = a channel."""
    return "social" if key.startswith("tg:") else "exchange"


# ------------------------------------------------------------------- scoring --
# (score, regex). The highest match wins. Case-insensitive and word-bounded
# except where noted: "SEC" is matched case-sensitively, or every "10 sec" would
# count as a regulator.
_I = re.I
SCORE_RULES = [
    (100, re.compile(
        r"\bdelist(?:s|ed|ing)?\b|\bmonitoring tag\b|"
        r"\bremoval of (?:[\w-]+ ){0,3}trading pairs?\b|"
        r"유의\s*종목|상장\s*폐지|거래\s*지원\s*종료", _I)),
    (90, re.compile(
        r"\bhack(?:ed|er|ers|s)?\b|\bexploit(?:ed|s)?\b|\bdrain(?:ed|s)?\b|"
        r"\b(?:chain|network|mainnet) (?:halt(?:ed|s)?|stall(?:ed)?|outage)\b|"
        r"\bhalts? (?:the )?(?:chain|network|mainnet)\b", _I)),
    (70, re.compile(
        r"\bban(?:s|ned|ning)?\b|\blawsuits?\b|\bsu(?:es|ed)\b|"
        r"\bsanction(?:s|ed)?\b|\bMiCA\b|\bAMLR\b", _I)),
    (70, re.compile(r"\bSEC\b")),
    (60, re.compile(
        r"\bETFs?\b[^.]{0,60}\b(?:approv\w*|record\w*|inflows?|outflows?)\b|"
        r"\b(?:approv\w*|record\w*)\b[^.]{0,60}\bETFs?\b", _I)),
    (50, re.compile(
        r"\bupgrades?\b|\bhard ?forks?\b|"
        r"\b(?:deposits?|withdrawals?)\b[^.]{0,40}\b(?:suspen\w*|halt\w*|paus\w*|delay\w*)|"
        r"\b(?:suspen\w*|halt\w*|paus\w*)\b[^.]{0,40}\b(?:deposits?|withdrawals?)|"
        r"\bfunding delays?\b|입출금\s*(?:일시\s*)?중단", _I)),
    (30, re.compile(r"\blist(?:s|ed|ing)?\b|신규\s*거래\s*지원|디지털\s*자산\s*추가", _I)),
]
# "Designation lifted" / "tag removed" are good news, not alarms.
LIFTED = re.compile(r"해제|\bremov(?:es|ed|al of)\b[^.]{0,20}\bmonitoring tag|"
                    r"\bmonitoring tag\b[^.]{0,20}\bremov", _I)


def score_text(text, floor=0):
    """-> 0..100 by the keyword table; `floor` is the source's own minimum."""
    best = max([sc for sc, rx in SCORE_RULES if rx.search(text or "")] + [0])
    if best >= 100 and LIFTED.search(text or ""):
        best = 40
    return max(best, floor)


def score_label(score):
    if score >= 100:
        return "🔴 critical"
    if score >= STRONG:
        return "🔴 urgent"
    if score >= 70:
        return "🟠 important"
    if score >= 50:
        return "🟡 notable"
    return "⚪ background"


# Words that make a coin-less item market news. Acronyms are case-sensitive
# ("Fed", not "fed up").
MACRO_KW_CS = re.compile(r"\b(?:SEC|ETF|ETFs|Fed|FOMC|CFTC|DOJ)\b")
MACRO_KW_CI = re.compile(
    r"\b(?:binance|coinbase|kraken|okx|bybit|tether|stablecoin|crypto\w*|bitcoin|ethereum)\b",
    _I)


def macro_keyword(text):
    return bool(MACRO_KW_CS.search(text or "") or MACRO_KW_CI.search(text or ""))


# ------------------------------------------------------------------ matching --
def match_coins(title, coins, body="", upbit=False):
    """-> tickers of tracked coins the item is about.

    Titles use news._patterns (so a bare "ETC" needs $ETC/(ETC)/the full name; pair
    notation "ETC/USDT" or "ETCUSDT" is unambiguous and also counts). In a pair
    "ABC/BTC" only ABC counts. A body (Binance delisting and maintenance notices
    name their pairs only there) matches on TICKER/..., (TICKER) or TICKERUSDT.
    Upbit titles carry the ticker in parentheses; that is required."""
    out = []
    for c in coins:
        tick = re.escape(c["ticker"])
        if upbit:
            if re.search(r"\(%s\)" % tick, title):
                out.append(c["ticker"])
            continue
        inc, exc = news._patterns(c, coins)
        as_quote = re.compile(r"\b[A-Z0-9]{2,10}/%s\b" % tick)
        base = re.compile(r"\b%s/[A-Z0-9]{2,10}\b|\(%s\)|\b%s(?:USDT|USDC|BUSD|USD)\b"
                          % (tick, tick, tick))
        tq = as_quote.sub(" ", title)
        hit = ((any(p.search(tq) for p in inc) or base.search(tq))
               and not any(p.search(tq) for p in exc))
        if not hit and body:
            hit = bool(base.search(as_quote.sub(" ", body)))
        if hit:
            out.append(c["ticker"])
    return out


def norm_title(title):
    return " ".join(news.words(title))


# ------------------------------------------------------------------- parsers --
def _http(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read()


def _iso(s):
    try:
        return int(datetime.fromisoformat(s).timestamp())
    except (TypeError, ValueError):
        return 0


def _strip_html(s):
    s = re.sub(r"<br\s*/?>|</p>", " ", s or "", flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


def parse_binance_list(d, cat):
    out = []
    for c in (d.get("data") or {}).get("catalogs") or []:
        for a in c.get("articles") or []:
            out.append({"ext_id": a["code"], "title": a["title"],
                        "url": news.BINANCE_URL.format(a["code"]),
                        "published": news._ms(a.get("releaseDate")),
                        "floor": BINANCE_FLOOR.get(cat, 0), "body": ""})
    return out


def parse_upbit(d):
    out = []
    for n in (d.get("data") or {}).get("notices") or []:
        out.append({"ext_id": str(n["id"]), "title": n["title"],
                    "url": UPBIT_NOTICE.format(n["id"]),
                    "published": _iso(n.get("first_listed_at") or n.get("listed_at"))})
    return out


def parse_status_rss(raw):
    """Statuspage history feed. pubDate is the incident's latest update, and a
    scheduled maintenance carries its (future) start time -- such an item has
    published > first_seen and is simply left out of latency figures."""
    out = []
    for it in ET.fromstring(raw).iter("item"):
        try:
            ts = int(parsedate_to_datetime(it.findtext("pubDate") or "").timestamp())
        except (TypeError, ValueError):
            ts = 0
        link = (it.findtext("link") or "").strip()
        out.append({"ext_id": (it.findtext("guid") or link or "").strip(),
                    "title": html.unescape((it.findtext("title") or "").strip()),
                    "url": link, "published": ts,
                    "desc": _strip_html(it.findtext("description"))[:1500]})
    return [o for o in out if o["ext_id"] and o["title"]]


_TG_PREFIX = re.compile(r"^\W*(?:just in|breaking|alert)\s*:?\s*", re.I)


def parse_tg(page, channel):
    """t.me/s/<channel>: one div.tgme_widget_message_wrap per post, id in
    data-post, body in .js-message_text, time in <time datetime=...>."""
    out = []
    for blk in re.split(r'<div class="tgme_widget_message_wrap', page)[1:]:
        m = re.search(r'data-post="([^"]+)"', blk)
        t = re.search(r'class="tgme_widget_message_text js-message_text"[^>]*>(.*?)</div>',
                      blk, re.S)
        if not m or not t:                       # photo-only posts have no text
            continue
        tm = re.search(r'<time[^>]*datetime="([^"]+)"', blk)
        text = _strip_html(t.group(1))
        text = re.sub(r"\s*@%s\s*$" % re.escape(channel), "", text, flags=re.I)
        text = _TG_PREFIX.sub("", text).strip()
        if not text:
            continue
        if len(text) > 280:
            text = text[:279].rstrip() + "…"
        out.append({"ext_id": m.group(1), "title": text,
                    "url": "https://t.me/" + m.group(1),
                    "published": _iso(tm.group(1)) if tm else 0})
    return out


def channel_list(raw):
    """'WatcherGuru, @WuBlockchain' -> ['WatcherGuru', 'WuBlockchain'] (valid names only)."""
    out = []
    for c in re.split(r"[,\s]+", raw or ""):
        c = re.sub(r"^(?:https?://)?(?:t\.me/(?:s/)?)?@?", "", c.strip())
        if re.fullmatch(r"[A-Za-z0-9_]{4,32}", c) and c.lower() not in {x.lower() for x in out}:
            out.append(c)
    return out[:5]


# ------------------------------------------------------------------ sources --
class Source:
    """A pollable feed. fetch(known) -> items; `known` are the ext_ids already
    stored, so a fetcher can skip expensive per-item work (article bodies)."""

    def __init__(self, key, interval, fetch, macro=False, exch_noise=False, upbit=False):
        self.key = key
        self.label = label_of(key)
        self.kind = kind_of(key)
        self.interval = interval
        self.fetch = fetch
        self.macro = macro            # coin-less items may push (see module doc)
        self.exch_noise = exch_noise  # drop promotions/competitions
        self.upbit = upbit


def _binance_fetch(cat):
    def fetch(known):
        items = parse_binance_list(json.loads(_http(news.BINANCE_LIST.format(cat))), cat)
        now = time.time()
        if cat in news.BINANCE_READ_BODY:
            for it in items:
                if it["ext_id"] in known or now - it["published"] > BODY_MAX_AGE_S:
                    continue
                try:
                    det = json.loads(_http(news.BINANCE_DETAIL.format(it["ext_id"])))["data"]
                    it["body"] = news._body_text(det.get("body"))[:6000]
                except Exception as e:                       # noqa: BLE001
                    log.info("binance article %s: %s", it["ext_id"], e)
        return items
    return fetch


def _news_fn(fn):
    """Adapt a news.py exchange fetcher (-> [{title, ts, url}]) to Source.fetch."""
    def fetch(known):
        return [{"ext_id": a["url"] or f"{a['ts']}:{a['title']}", "title": a["title"],
                 "url": a["url"], "published": a["ts"]} for a in fn(0, timeout=TIMEOUT)]
    return fetch


def static_sources():
    # Promotions are dropped from listings and maintenance notices; a delisting
    # (161) is never filtered out.
    src = [Source(f"binance-{cat}", iv, _binance_fetch(cat), exch_noise=cat != 161)
           for cat, iv in BINANCE_CATS]
    src.append(Source("upbit", 60, lambda known: parse_upbit(json.loads(_http(UPBIT_URL))),
                      upbit=True))
    src.append(Source("kraken_status", 120,
                      lambda known: parse_status_rss(_http(KRAKEN_STATUS)), macro=True))
    src.append(Source("coinbase_status", 120,
                      lambda known: parse_status_rss(_http(COINBASE_STATUS)), macro=True))
    for key, fn in (("okx", news._okx), ("bybit", news._bybit),
                    ("kucoin", news._kucoin), ("bitget", news._bitget)):
        src.append(Source(key, 120, _news_fn(fn), exch_noise=True))
    return src


def tg_source(channel):
    def fetch(known):
        page = _http(TG_PREVIEW.format(channel)).decode("utf-8", "replace")
        items = parse_tg(page, channel)
        if not items:
            # A private/removed channel, or Telegram serving a block page.
            raise ValueError("no messages in the channel preview")
        return items
    return Source("tg:" + channel, 60, fetch, macro=True)


# ------------------------------------------------------------------- watcher --
def _pct(sorted_vals, p):
    if not sorted_vals:
        return None
    return sorted_vals[max(0, math.ceil(p / 100 * len(sorted_vals)) - 1)]


class Newswatch:
    def __init__(self, store, notifier, clock=time.time, sources=None):
        self.store, self.notifier, self.clock = store, notifier, clock
        self._fixed = sources                       # tests inject sources
        self._static = static_sources() if sources is None else []
        self._tg = {}
        self._lock = threading.RLock()              # health / schedule state
        self._ingest_lock = threading.Lock()        # one ingest at a time: dedupe + cap
        self._h = {}                                # key -> health counters
        self._next, self._running = {}, set()
        self._ignored = {}                          # key -> ext_ids dropped by filters
        self._epoch, self._src_epoch, self._was_disabled = 0, {}, False
        self._stop = threading.Event()
        self._pool = None

    # ---------- configuration ----------
    def cfg(self):
        s = self.store.settings()

        def num(k, d):
            try:
                return int(float(s.get(k, d)))
            except (TypeError, ValueError):
                return d
        return {"enabled": s.get("news_enabled", "1") != "0",
                "min_score": num("news_min_score", 70),
                "max_per_day": num("news_max_per_day", 10),
                "channels": channel_list(s.get("news_tg_channels", "WatcherGuru")),
                "quiet": s.get("quiet_hours", "")}

    def sources(self, cfg=None):
        if self._fixed is not None:
            return list(self._fixed)
        cfg = cfg or self.cfg()
        out = list(self._static)
        for ch in cfg["channels"]:
            if ch not in self._tg:
                self._tg[ch] = tg_source(ch)
            out.append(self._tg[ch])
        return out

    # ---------- thread ----------
    def start(self):
        threading.Thread(target=self._loop, daemon=True, name="newswatch").start()

    def stop(self):
        self._stop.set()

    def _loop(self):
        self._pool = ThreadPoolExecutor(8, thread_name_prefix="newswatch")
        log.info("newswatch started")
        while not self._stop.is_set():
            try:
                cfg = self.cfg()
                if not cfg["enabled"]:
                    # Idle. Whatever is published while we are off must not be
                    # pushed as "breaking" on re-enable, so every source re-seeds.
                    if not self._was_disabled:
                        self._was_disabled = True
                        self._epoch += 1
                    self._stop.wait(5)
                    continue
                self._was_disabled = False
                now = self.clock()
                for src in self.sources(cfg):
                    with self._lock:
                        if src.key in self._running or self._next.get(src.key, 0) > now:
                            continue
                        self._running.add(src.key)
                        self._next[src.key] = now + src.interval
                    self._pool.submit(self._run, src)
            except Exception:                                # noqa: BLE001
                log.exception("newswatch loop")
            self._stop.wait(1)

    def _run(self, src):
        try:
            self.poll(src)
        except Exception:                                    # noqa: BLE001
            log.exception("newswatch %s", src.key)
        finally:
            with self._lock:
                self._running.discard(src.key)

    # ---------- one poll ----------
    def _hl(self, key):
        return self._h.setdefault(key, {"last_ok": 0, "last_try": 0, "errors": 0,
                                        "last_error": "", "polls": 0})

    def poll(self, src):
        """Fetch one source and ingest it. A failure is recorded and swallowed."""
        now = int(self.clock())
        with self._lock:
            h = self._hl(src.key)
            h["last_try"] = now
            ignored = set(self._ignored.get(src.key, ()))
        try:
            known = self.store.news_known(src.key) | ignored
            items = src.fetch(known)
        except Exception as e:                               # noqa: BLE001
            with self._lock:
                h["errors"] += 1
                h["last_error"] = str(e)[:200]
            log.warning("newswatch %s failed (%d in a row): %s", src.key, h["errors"], e)
            return None
        with self._lock:
            h["errors"], h["last_error"], h["last_ok"] = 0, "", now
            h["polls"] += 1
        return self.ingest(src, items, known, now)

    def ingest(self, src, items, known, now):
        """Store the unseen items, cluster them, push what qualifies.
        -> list of the new rows (dicts)."""
        cfg = self.cfg()
        new_rows, to_push = [], []
        with self._ingest_lock:
            # First run = this source has never completed a poll in this process
            # AND the database holds nothing from it (a restart keeps its history,
            # so what appeared during a redeploy is still announced). Re-enabling
            # after a pause bumps the epoch and re-seeds every source.
            epoch = self._epoch
            if src.key in self._src_epoch:
                seed = self._src_epoch[src.key] < epoch
            else:
                seed = not (known - self._ignored.get(src.key, set()))
            coins = self.store.coins()
            cands = [{"id": c["id"], "cluster": c["cluster"], "sh": news.shingles(c["title_norm"]),
                      "coins": frozenset(filter(None, c["coins"].split(",")))}
                     for c in self.store.news_cluster_candidates(now - news.CLUSTER_WINDOW_S)]
            ign = self._ignored.setdefault(src.key, set())
            if len(ign) > 2000:
                ign.clear()
            for it in sorted(items, key=lambda x: x.get("published") or 0):
                ext = it["ext_id"]
                title = re.sub(r"\s+", " ", it["title"]).strip()
                if ext in known or not title:
                    continue
                if (news._noise({"title": title, "source": src.label})
                        or (src.exch_noise and news.EXCH_NOISE.search(title))):
                    ign.add(ext)
                    continue
                tickers = match_coins(title + (" " + it["desc"] if it.get("desc") else ""),
                                      coins, body=it.get("body", ""), upbit=src.upbit)
                score = score_text(title, it.get("floor", 0))
                norm = norm_title(title)
                sh, cs = news.shingles(norm), frozenset(tickers)
                cluster = 0
                for c in reversed(cands):       # newest first
                    if c["coins"] == cs and news.jaccard(c["sh"], sh) >= news.JACCARD_MIN:
                        cluster = c["cluster"]
                        break
                rid = self.store.news_add(
                    src.key, ext, title, norm, it.get("url", ""), it.get("published") or 0,
                    now, ",".join(tickers), score, cluster=cluster, seeded=1 if seed else 0)
                if rid is None:
                    continue
                row = {"id": rid, "source": src.key, "title": title, "url": it.get("url", ""),
                       "published": it.get("published") or 0, "first_seen": now,
                       "coins": tickers, "score": score, "dup": bool(cluster)}
                cands.append({"id": rid, "cluster": cluster or rid, "sh": sh, "coins": cs})
                new_rows.append(row)
                if not seed and not cluster:
                    to_push.append(row)
            self._src_epoch[src.key] = epoch
            if seed and new_rows:
                log.info("newswatch %s: seeded %d items silently", src.key, len(new_rows))
            for row in sorted(to_push, key=lambda r: -r["score"]):
                ok, why = self.should_push(src, row, cfg, now)
                if ok:
                    self._push(src, row, now)
                elif row["score"] >= 50:
                    log.info("newswatch %s: not pushed (%s): %s", src.key, why, row["title"][:80])
        return new_rows

    # ---------- push ----------
    def should_push(self, src, row, cfg, now):
        """-> (bool, reason). See the module docstring for the rule."""
        if row["published"] and now - row["published"] > PUSH_MAX_AGE_S:
            return False, "old"
        score = row["score"]
        if row["coins"]:
            if score < cfg["min_score"]:
                return False, "below min score"
        else:
            if not (src.macro and score >= max(STRONG, cfg["min_score"])
                    and macro_keyword(row["title"])):
                return False, "no tracked coin"
        if score < STRONG and in_quiet_hours(cfg["quiet"], datetime.fromtimestamp(now)):
            return False, "quiet hours"
        if self.store.news_pushed_count(now - 86400) >= cfg["max_per_day"]:
            return False, "daily cap"
        return True, ""

    def format_push(self, src, row, now):
        tick = ", ".join(row["coins"]) if row["coins"] else "MARKET"
        title = html.escape(row["title"])
        head = (f"📰 <b>{html.escape(tick)}</b> · {html.escape(src.label)} · "
                f"{score_label(row['score'])}")
        body = (f'<a href="{html.escape(row["url"], quote=True)}">{title}</a>'
                if row["url"] else title)
        out = [head, body]
        pub = row["published"]
        if pub:
            line = "published " + datetime.fromtimestamp(pub, timezone.utc).strftime("%H:%M UTC")
            if now >= pub:
                mins = (now - pub) // 60
                line += " · seen after " + ("<1 min" if mins < 1 else f"{mins} min")
            out.append(line)
        return "\n".join(out)

    def _push(self, src, row, now):
        if not self.notifier.enabled():
            return
        sym = ""
        if row["coins"]:
            sym = next((c["symbol"] for c in self.store.coins()
                        if c["ticker"] == row["coins"][0]), "")
        _, ok = self.notifier.dispatch("news", self.format_push(src, row, now), symbol=sym)
        self.store.news_set(row["id"], pushed=1 if ok else 0)
        log.info("newswatch push %s (%s, score %d): %s", "sent" if ok else "FAILED",
                 src.key, row["score"], row["title"][:80])

    # ---------- health ----------
    def health(self):
        """Per source: last successful poll, consecutive errors, last new item, and
        p50/p90 of (first_seen - published) over the last 7 days -- how long after
        publication this server noticed (bounded below by the poll interval)."""
        now = int(self.clock())
        cfg = self.cfg()
        stats = self.store.news_source_stats(now - HEALTH_WINDOW_S)
        last_new = self.store.news_last_new()
        out = []
        for src in self.sources(cfg):
            with self._lock:
                h = dict(self._hl(src.key))
            st = stats.get(src.key, {"n": 0, "lat": []})
            lat = sorted(st["lat"])
            out.append({
                "source": src.key, "label": src.label, "kind": src.kind,
                "interval_s": src.interval,
                "last_ok": h["last_ok"] or None, "last_try": h["last_try"] or None,
                "consecutive_errors": h["errors"], "last_error": h["last_error"],
                "last_new_item": last_new.get(src.key),
                "items_7d": st["n"], "latency_samples": len(lat),
                "latency_p50_s": _pct(lat, 50), "latency_p90_s": _pct(lat, 90),
            })
        return {"enabled": cfg["enabled"], "generated": now,
                "pushed_24h": self.store.news_pushed_count(now - 86400),
                "sources": out}
