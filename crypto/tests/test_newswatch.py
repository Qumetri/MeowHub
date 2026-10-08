"""Newswatch and the digest's news half. Stdlib only, no network, no real clock:

    python3 -m unittest discover -s crypto/tests       (or: python3 crypto/tests/test_newswatch.py)
    python3 -m pytest -q crypto/tests                   (where pytest is installed)

Fixture payloads in tests/fixtures/ are trimmed captures of the live endpoints
(2026-10-08). time.time is replaced per test because test_fluctuation.py
replaces it globally at import.
"""
import json
import os
import shutil
import sys
import tempfile
import time
import types
import unittest
from datetime import datetime, timezone
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "app"))
sys.modules.setdefault("explain", types.SimpleNamespace(enabled=lambda: False,
                                                        request=lambda *a: None))

import news                      # noqa: E402
import newswatch as nw           # noqa: E402
import server                    # noqa: E402
import telegram                  # noqa: E402
from store import Store          # noqa: E402
from telegram import Notifier    # noqa: E402

NOW = int(datetime(2026, 10, 8, 7, 0, tzinfo=timezone.utc).timestamp())


def fixture(name, text=False):
    with open(os.path.join(HERE, "fixtures", name), "rb") as f:
        raw = f.read()
    return raw.decode() if text else raw


class FakeNotifier:
    def __init__(self, enabled=True, ok=True):
        self.on, self.ok, self.sent = enabled, ok, []

    def enabled(self):
        return self.on

    def dispatch(self, kind, text, symbol="", **kw):
        self.sent.append({"kind": kind, "text": text, "symbol": symbol})
        return len(self.sent), self.ok


class Base(unittest.TestCase):
    def setUp(self):
        self.clock = [NOW]
        p = mock.patch.object(time, "time", lambda: self.clock[0])
        p.start()
        self.addCleanup(p.stop)
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.store = Store(os.path.join(self.tmp, "t.db"))
        news._cache.clear()

    def make(self, sources, notifier=None, **settings):
        self.notifier = notifier or FakeNotifier()
        self.store.set_many(settings)
        return nw.Newswatch(self.store, self.notifier, clock=lambda: self.clock[0],
                            sources=sources)


def src(key, items, macro=False, upbit=False, exch_noise=False):
    """A source whose feed is the (mutable) list `items`."""
    return nw.Source(key, 60, lambda known: list(items), macro=macro, upbit=upbit,
                     exch_noise=exch_noise)


def item(ext, title, age_s=60, **kw):
    d = {"ext_id": ext, "title": title, "url": "https://x/" + ext,
         "published": NOW - age_s if age_s is not None else 0}
    d.update(kw)
    return d


# --------------------------------------------------------------------- scoring --
class TestScore(unittest.TestCase):
    def test_table(self):
        cases = [
            ("Binance Will Delist RVN on 2026-10-20", 100),
            ("Notice of Removal of Spot Trading Pairs - 2026-10-02", 100),
            ("Binance Will Add a Monitoring Tag to FOO", 100),
            ("블라스트(BLAST) 거래 유의 종목 지정 안내", 100),
            ("Bybit hacked, $1.4B drained", 90),
            ("Solana network halt, validators stalled", 90),
            ("SEC sues exchange", 70),
            ("EU MiCA ban on privacy coins", 70),
            ("Spot Bitcoin ETF sees record inflows", 60),
            ("Network Upgrade on Ravencoin", 50),
            ("Kraken: Monero deposits suspended", 50),
            ("Binance Will List FOO", 30),
            ("Bitcoin falls under $84,000", 0),
        ]
        for text, want in cases:
            self.assertEqual(nw.score_text(text), want, text)

    def test_word_boundaries_and_case(self):
        self.assertEqual(nw.score_text("price held for 10 sec"), 0)     # "sec" != SEC
        self.assertEqual(nw.score_text("Bank urges calm"), 0)           # "ban" inside "Bank"
        self.assertEqual(nw.score_text("Relisting planned"), 0)         # no word boundary
        self.assertEqual(nw.score_text("HACKED wallet"), 90)            # case-insensitive

    def test_lifted_is_not_an_alarm(self):
        self.assertEqual(nw.score_text("샌드박스(SAND) 거래 유의 종목 지정 해제 안내"), 40)

    def test_source_floor(self):
        self.assertEqual(nw.score_text("Notice of something", floor=100), 100)


class TestMatch(unittest.TestCase):
    COINS = [{"ticker": "BTC", "name": "Bitcoin"}, {"ticker": "ETH", "name": "Ethereum"},
             {"ticker": "ETC", "name": "Ethereum Classic"}, {"ticker": "RVN", "name": "Ravencoin"},
             {"ticker": "XMR", "name": "Monero"}]

    def test_title(self):
        self.assertEqual(nw.match_coins("Ravencoin upgrade", self.COINS), ["RVN"])
        self.assertEqual(nw.match_coins("Ethereum Classic halts", self.COINS), ["ETC"])
        self.assertEqual(nw.match_coins("Bitcoin ETC fund", self.COINS), ["BTC"])   # bare ETC is ambiguous

    def test_upbit_needs_parentheses(self):
        self.assertEqual(nw.match_coins("모네로(XMR) 거래 유의 종목 지정 안내", self.COINS, upbit=True), ["XMR"])
        self.assertEqual(nw.match_coins("XMR notice", self.COINS, upbit=True), [])

    def test_body_pairs(self):
        body = "The following pairs will be removed: RVN/USDT, ABC/BTC, ABC/ETH"
        self.assertEqual(nw.match_coins("Notice of Removal of Spot Trading Pairs", self.COINS, body=body),
                         ["RVN"])                       # BTC/ETH are only quote currencies
        self.assertEqual(nw.match_coins("Futures delist", self.COINS, body="XMRUSDT perpetual"), ["XMR"])


# --------------------------------------------------------------------- parsers --
class TestParsers(unittest.TestCase):
    def test_binance(self):
        its = nw.parse_binance_list(json.loads(fixture("binance_161.json")), 161)
        self.assertEqual(len(its), 3)
        self.assertEqual(its[0]["published"], 1790822713)
        self.assertEqual(its[0]["floor"], 100)
        self.assertTrue(its[0]["url"].endswith(its[0]["ext_id"]))
        self.assertEqual(nw.parse_binance_list(json.loads(fixture("binance_161.json")), 48)[0]["floor"], 0)

    def test_upbit(self):
        its = nw.parse_upbit(json.loads(fixture("upbit.json")))
        self.assertEqual(its[0]["ext_id"], "6644")
        self.assertEqual(its[0]["published"], int(datetime(2026, 10, 8, 2, 37, 41, tzinfo=timezone.utc).timestamp()))
        self.assertIn("(PONS)", its[0]["title"])

    def test_status_rss(self):
        its = nw.parse_status_rss(fixture("kraken_status.rss"))
        self.assertGreaterEqual(len(its), 5)
        self.assertTrue(all(i["ext_id"].startswith("https://status.kraken.com/incidents/") for i in its))
        # the first item is a scheduled maintenance dated in the future
        self.assertGreater(its[0]["published"], NOW)
        cb = nw.parse_status_rss(fixture("coinbase_status.rss"))
        self.assertIn("BCH", cb[2]["title"])
        self.assertNotIn("<", cb[2]["desc"])

    def test_telegram(self):
        its = nw.parse_tg(fixture("tg_watcherguru.html", text=True), "WatcherGuru")
        self.assertEqual([i["ext_id"] for i in its],
                         ["WatcherGuru/15378", "WatcherGuru/15381", "WatcherGuru/15387"])
        self.assertTrue(its[0]["title"].startswith("Bitcoin falls under $84,000"))   # "JUST IN:" dropped, & unescaped
        self.assertNotIn("@WatcherGuru", its[0]["title"])
        self.assertEqual(its[0]["published"], int(datetime(2026, 10, 7, 2, 5, 20, tzinfo=timezone.utc).timestamp()))
        self.assertEqual(its[0]["url"], "https://t.me/WatcherGuru/15378")

    def test_channel_list(self):
        self.assertEqual(nw.channel_list("WatcherGuru, @WuBlockchain https://t.me/s/Foo_Bar x"),
                         ["WatcherGuru", "WuBlockchain", "Foo_Bar"])
        self.assertEqual(nw.channel_list(""), [])

    def test_okx_adapter(self):
        d = json.loads(fixture("okx_delistings.json"))
        with mock.patch.object(news, "_get_json", lambda url, timeout=15: d):
            out = news._okx(0)
        self.assertEqual(len(out), 4)                    # both annTypes return the same urls: deduped
        self.assertEqual(out[0]["source"], "OKX")


# ---------------------------------------------------------------------- ingest --
class TestIngest(Base):
    def run_poll(self, nwatch, s):
        return nwatch.poll(s)

    def test_first_run_seeds_silently_then_pushes(self):
        feed = [item("a", "Binance Will Delist RVN/USDT", age_s=300, floor=100)]
        s = src("binance-161", feed)
        w = self.make([s])
        w.poll(s)
        self.assertEqual(self.notifier.sent, [])
        rows = self.store.news_list()
        self.assertEqual((len(rows), rows[0]["seeded"], rows[0]["pushed"]), (1, 1, 0))
        # same feed again: nothing new
        w.poll(s)
        self.assertEqual(self.notifier.sent, [])
        # a new delisting shows up
        feed.append(item("b", "Binance Will Delist Ravencoin (RVN) perpetuals", age_s=120, floor=100))
        w.poll(s)
        self.assertEqual(len(self.notifier.sent), 1)
        msg = self.notifier.sent[0]
        self.assertEqual(msg["kind"], "news")
        self.assertEqual(msg["symbol"], "RVNUSDT")
        lines = msg["text"].split("\n")
        self.assertEqual(lines[0], "📰 <b>RVN</b> · Binance · 🔴 critical")
        self.assertEqual(lines[1], '<a href="https://x/b">Binance Will Delist Ravencoin (RVN) perpetuals</a>')
        self.assertEqual(lines[2], "published 06:58 UTC · seen after 2 min")
        self.assertEqual(self.store.news_list()[0]["pushed"], 1)

    def test_restart_does_not_reseed_or_repush(self):
        feed = [item("a", "Binance Will Delist RVN/USDT", floor=100)]
        s = src("binance-161", feed)
        w = self.make([s])
        w.poll(s)
        w2 = self.make([s])                         # "restart": new object, same database
        w2.poll(s)
        self.assertEqual(self.notifier.sent, [])
        feed.append(item("b", "Binance Will Delist ETC/USDT", floor=100))
        w2.poll(s)
        self.assertEqual(len(self.notifier.sent), 1)

    def test_filters_and_thresholds(self):
        feed = []
        s = src("okx", feed)
        w = self.make([s])
        w.poll(s)                                    # seed (empty)
        feed += [
            item("1", "OKX will list Ravencoin (RVN) spot"),                    # coin, score 30
            item("2", "Delist ABC, DEF"),                                       # score 100, no tracked coin
            item("3", "Network upgrade for Monero (XMR)"),                      # coin, score 50
            item("4", "SEC sues Solana over token sale"),                       # coin, score 70
        ]
        w.poll(s)
        self.assertEqual(len(self.notifier.sent), 1)
        self.assertIn("SOL", self.notifier.sent[0]["text"].split("\n")[0])
        rows = {r["ext_id"]: r for r in self.store.news_list()}
        self.assertEqual({k: (r["score"], r["pushed"]) for k, r in rows.items()},
                         {"1": (30, 0), "2": (100, 0), "3": (50, 0), "4": (70, 1)})
        # lowering the bar pushes the 50
        self.store.set_many({"news_min_score": "50"})
        feed.append(item("5", "Hard fork scheduled for Ravencoin"))
        w.poll(s)
        self.assertEqual(len(self.notifier.sent), 2)

    def test_stale_item_is_stored_not_pushed(self):
        feed = []
        s = src("okx", feed)
        w = self.make([s])
        w.poll(s)
        feed.append(item("1", "Ravencoin delisted from OKX", age_s=7 * 3600))
        w.poll(s)
        self.assertEqual(self.notifier.sent, [])
        self.assertEqual(len(self.store.news_list()), 1)

    def test_cross_source_dedupe_and_cluster_count(self):
        a_feed, b_feed = [], []
        a, b = src("okx", a_feed), src("bybit", b_feed)
        w = self.make([a, b])
        w.poll(a)
        w.poll(b)
        a_feed.append(item("1", "Ravencoin (RVN) to be delisted from spot trading on October 20"))
        b_feed.append(item("9", "Ravencoin (RVN) to be delisted from spot trading on October 20"))
        w.poll(a)
        w.poll(b)
        self.assertEqual(len(self.notifier.sent), 1)               # the second source is a repeat
        rows = self.store.news_list()
        self.assertEqual(len({r["cluster"] for r in rows}), 1)
        self.assertEqual(len(rows), 2)
        self.assertEqual(sorted(self.store.news_since(NOW - 3600)[0]["cluster_sources"]), ["bybit", "okx"])

    def test_same_template_different_coin_is_not_a_repeat(self):
        feed = []
        s = src("binance-161", feed)
        w = self.make([s])
        w.poll(s)
        t = "Notice of Removal of Spot Trading Pairs - 2026-10-09"
        feed.append(item("1", t, floor=100, body="RVN/USDT will be removed"))
        feed.append(item("2", t, floor=100, body="ETC/USDT will be removed"))
        w.poll(s)
        self.assertEqual(len(self.notifier.sent), 2)

    def test_quiet_hours_spare_only_strong(self):
        h = datetime.fromtimestamp(NOW).hour
        feed = []
        s = src("okx", feed)
        w = self.make([s], quiet_hours=f"{h}-{(h + 2) % 24}")
        w.poll(s)
        feed.append(item("1", "SEC sues Solana"))                  # 70: muted
        feed.append(item("2", "Solana delisted from OKX"))         # 100: goes through
        w.poll(s)
        self.assertEqual(len(self.notifier.sent), 1)
        self.assertIn("delisted", self.notifier.sent[0]["text"])

    def test_daily_cap(self):
        feed = []
        s = src("okx", feed)
        w = self.make([s], news_max_per_day="2")
        w.poll(s)
        feed += [item(str(i), f"Ravencoin delisted from pair {i}") for i in range(4)]
        w.poll(s)
        self.assertEqual(len(self.notifier.sent), 2)
        self.clock[0] += 25 * 3600                                 # a day later the budget is back
        feed.append(item("late", "Monero delisted", age_s=30 - 25 * 3600))
        w.poll(s)
        self.assertEqual(len(self.notifier.sent), 3)

    def test_macro_rule(self):
        tg_feed, ex_feed = [], []
        tg, ex = src("tg:WatcherGuru", tg_feed, macro=True), src("okx", ex_feed)
        w = self.make([tg, ex])
        w.poll(tg)
        w.poll(ex)
        tg_feed += [item("t1", "Major exchange Binance hacked, $500M drained"),   # 90 + keyword
                    item("t2", "SEC sues a lender"),                              # 70: no coin, no push
                    item("t3", "Fed holds rates steady"),                         # 0
                    item("t4", "Some hack on an obscure bridge")]                 # 90 but no market keyword
        ex_feed.append(item("e1", "Exchange hacked, funds drained"))              # not a macro source
        w.poll(tg)
        w.poll(ex)
        self.assertEqual(len(self.notifier.sent), 1)
        self.assertTrue(self.notifier.sent[0]["text"].startswith("📰 <b>MARKET</b> · Telegram @WatcherGuru"))

    def test_notifier_off_still_stores(self):
        feed = []
        s = src("okx", feed)
        w = self.make([s], notifier=FakeNotifier(enabled=False))
        w.poll(s)
        feed.append(item("1", "Ravencoin delisted"))
        w.poll(s)
        self.assertEqual(self.notifier.sent, [])
        self.assertEqual(len(self.store.news_list()), 1)

    def test_noise_dropped_and_not_stored(self):
        feed = []
        s = src("okx", feed, exch_noise=True)
        w = self.make([s])
        w.poll(s)
        feed += [item("1", "Convert 10 ZAR to ETC"), item("2", "Win a prize: Ravencoin trading competition")]
        w.poll(s)
        w.poll(s)
        self.assertEqual(self.store.news_list(), [])

    def test_real_notifier_logs_event(self):
        self.store.set_many({"tg_token": "123:abc", "tg_chat_id": "42", "tg_enabled": "1"})
        calls = []
        feed = []
        s = src("okx", feed)
        w = self.make([s], notifier=Notifier(self.store))
        w.poll(s)
        feed.append(item("1", "Ravencoin delisted from OKX"))
        with mock.patch.object(telegram, "send", lambda tok, chat, text, **k: calls.append((tok, chat, text)) or {"message_id": 7}):
            w.poll(s)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][:2], ("123:abc", "42"))
        ev = self.store.events(limit=5)[0]
        self.assertEqual((ev["kind"], ev["symbol"], ev["sent"]), ("news", "RVNUSDT", 1))
        self.assertEqual(self.store.news_list()[0]["pushed"], 1)

    def test_reenable_reseeds(self):
        feed = [item("a", "Ravencoin delisted from OKX")]
        s = src("okx", feed)
        w = self.make([s])
        w.poll(s)
        w._epoch += 1                                # what the loop does while news_enabled=0
        feed.append(item("b", "Monero delisted from OKX"))
        w.poll(s)
        self.assertEqual(self.notifier.sent, [])
        self.assertEqual([r["seeded"] for r in self.store.news_list()], [1, 1])
        feed.append(item("c", "Ethereum Classic delisted from OKX"))
        w.poll(s)
        self.assertEqual(len(self.notifier.sent), 1)


class TestIsolationAndHealth(Base):
    def test_a_dead_source_does_not_touch_others(self):
        def boom(known):
            raise OSError("connection refused")
        dead = nw.Source("kucoin", 120, boom)
        feed = []
        live = src("okx", feed)
        w = self.make([dead, live])
        self.assertIsNone(w.poll(dead))
        self.assertIsNone(w.poll(dead))
        w.poll(live)
        feed.append(item("1", "Ravencoin delisted"))
        w.poll(live)
        self.assertEqual(len(self.notifier.sent), 1)
        h = {x["source"]: x for x in w.health()["sources"]}
        self.assertEqual((h["kucoin"]["consecutive_errors"], h["kucoin"]["last_ok"]), (2, None))
        self.assertIn("connection refused", h["kucoin"]["last_error"])
        self.assertEqual((h["okx"]["consecutive_errors"], h["okx"]["last_ok"]), (0, NOW))

    def test_latency_percentiles_skip_seeded_and_future(self):
        feed = [item("seed", "Old story one", age_s=5 * 3600)]
        s = src("okx", feed)
        w = self.make([s])
        w.poll(s)                                    # seeded: 5 h "latency" must not count
        for i, lag in enumerate((30, 60, 600)):
            feed.append(item(f"n{i}", f"Unrelated headline number {i} about nothing", age_s=lag))
        feed.append(item("fut", "Scheduled maintenance later", age_s=-3600))   # published in the future
        w.poll(s)
        h = w.health()["sources"][0]
        self.assertEqual((h["latency_samples"], h["latency_p50_s"], h["latency_p90_s"]), (3, 60, 600))
        self.assertEqual(h["items_7d"], 4)
        self.assertEqual(h["last_new_item"], NOW)

    def test_disabled_loop_polls_nothing(self):
        calls = []
        s = nw.Source("okx", 1, lambda known: calls.append(1) or [])
        w = self.make([s], news_enabled="0")
        w.start()
        threading_wait(1.3)
        w.stop()
        self.assertEqual(calls, [])
        self.assertEqual(w._epoch, 1)                # re-enabling will re-seed

    def test_enabled_loop_polls(self):
        calls = []
        s = nw.Source("okx", 1, lambda known: calls.append(1) or [])
        w = self.make([s], news_enabled="1")
        w.start()
        threading_wait(1.5)
        w.stop()
        self.assertGreaterEqual(len(calls), 1)


def threading_wait(sec):
    import threading
    threading.Event().wait(sec)


# ---------------------------------------------------------------------- digest --
class FakeApp:
    def __init__(self, store, watch=None):
        self.store, self.newswatch = store, watch
        self.rows = [
            {"symbol": "BTCUSDT", "ticker": "BTC", "name": "Bitcoin", "price": 80000.0, "change24h": 1.0},
            {"symbol": "RVNUSDT", "ticker": "RVN", "name": "Ravencoin", "price": 0.01, "change24h": -2.0},
            {"symbol": "SOLUSDT", "ticker": "SOL", "name": "Solana", "price": 150.0, "change24h": 0.5},
        ]

    def coin_rows(self):
        return self.rows


class TestDigest(Base):
    def test_reads_news_seen_first_and_gates_google(self):
        w = self.make([])
        add = lambda src_, ext, title, coins, seen_age, **kw: self.store.news_add(   # noqa: E731
            src_, ext, title, nw.norm_title(title), "https://x/" + ext, NOW - seen_age,
            NOW - seen_age + 90, coins, kw.get("score", 0), seeded=kw.get("seeded", 0))
        add("binance-161", "a1", "Binance Will Delist Bitcoin pair one", "BTC", 600, score=100)
        add("okx", "a2", "OKX lists a Bitcoin product", "BTC", 900)
        add("tg:WatcherGuru", "a3", "Bitcoin whales accumulate", "BTC", 1200, seeded=1)
        add("binance-161", "r1", "Binance Will Delist Ravencoin pair", "RVN", 600, score=100)
        # two sources, one story
        a = add("okx", "s1", "Solana network upgrade scheduled for next week", "SOL", 700)
        self.store.news_add("bybit", "s2", "Solana network upgrade scheduled for next week",
                            nw.norm_title("Solana network upgrade scheduled for next week"),
                            "https://x/s2", NOW - 650, NOW - 600, "SOL", 50, cluster=a)
        asked = []

        def g(coin, all_coins, since):
            asked.append(coin["ticker"])
            return [{"title": "Google story about " + coin["name"], "source": "Gnews", "ts": NOW - 100,
                     "url": "https://g/" + coin["ticker"]}], ""
        fed = {"title": "Minutes of the FOMC", "source": "Federal Reserve", "ts": NOW - 3600,
               "url": "https://fed/min"}
        with mock.patch.object(news, "_coin_headlines", g), \
                mock.patch.object(news, "_exchange_items", lambda since: ([], [])), \
                mock.patch.object(news, "_market_headlines", lambda since, tracked=(): ([], [])), \
                mock.patch.object(news, "_fed_latest", lambda: fed):
            w2 = self.make([])
            d = news.build_digest(FakeApp(self.store, w2), hours=24, fresh=True)
        # BTC has 3 newswatch items (>= 3): no Google; RVN is always asked; SOL has only 1 (cluster rows count 2 -> still < 3)
        self.assertEqual(sorted(asked), ["RVN", "SOL"])
        coins = {c["ticker"]: c for c in d["coins"]}
        btc = coins["BTC"]["headlines"]
        self.assertEqual(btc[0]["title"], "Binance Will Delist Bitcoin pair one")     # highest score first
        self.assertEqual(btc[0]["kind"], "exchange")
        self.assertEqual(btc[0]["first_seen"], NOW - 510)
        self.assertEqual(btc[0]["source"], "Binance")
        seeded = [h for h in btc if h["title"] == "Bitcoin whales accumulate"][0]
        self.assertNotIn("first_seen", seeded)                                    # seeded rows have no real first_seen
        sol = coins["SOL"]["headlines"]
        up = [h for h in sol if "upgrade" in h["title"]]
        self.assertEqual(len(up), 1)
        self.assertEqual((up[0]["cluster_size"], up[0]["cluster_note"]), (2, "2 sources"))
        self.assertEqual(up[0]["cluster_sources"], ["Bybit", "OKX"])
        self.assertEqual(sol[-1]["source"], "Gnews")
        self.assertEqual(coins["RVN"]["headlines"][0]["title"], "Binance Will Delist Ravencoin pair")
        # additive fields only: the old keys are all still there
        for h in btc + sol:
            self.assertTrue({"title", "source", "ts", "url"} <= set(h))
        self.assertEqual(d["macro"]["fed_press"]["title"], "Minutes of the FOMC")
        self.assertIn("Next FOMC Oct 27-28 (in 19 days)", d["macro"]["line"])
        self.assertEqual(d["news_health"]["enabled"], True)
        self.assertEqual(d["news_errors"], [])

    def test_empty_news_seen_falls_back_to_google_for_all(self):
        asked = []

        def g(coin, all_coins, since):
            asked.append(coin["ticker"])
            return [], ""
        with mock.patch.object(news, "_coin_headlines", g), \
                mock.patch.object(news, "_exchange_items", lambda since: ([], [])), \
                mock.patch.object(news, "_market_headlines", lambda since, tracked=(): ([], [])), \
                mock.patch.object(news, "_fed_latest", lambda: None):
            d = news.build_digest(FakeApp(self.store), hours=24, fresh=True)
        self.assertEqual(sorted(asked), ["BTC", "RVN", "SOL"])
        self.assertIsNone(d["news_health"])
        self.assertIsNone(d["macro"]["fed_press"])

    def test_no_news_mode_keeps_shape(self):
        d = news.build_digest(FakeApp(self.store), hours=24, with_news=False)
        self.assertEqual(set(d), {"generated", "hours", "market", "macro", "coins", "news_errors", "news_health"})
        self.assertEqual(d["coins"][0]["headlines"], [])

    def test_fetcher_gap_fill_dedupes_by_url_and_title(self):
        title = "Binance Will Delist Ravencoin pair"
        self.store.news_add("binance-161", "r1", title, nw.norm_title(title), "https://x/r1",
                            NOW - 600, NOW - 500, "RVN", 100)
        exch = [{"title": title, "source": "Binance", "ts": NOW - 600, "url": "https://x/r1", "body": ""},
                {"title": "Bybit will delist Ravencoin (RVN) perpetual", "source": "Bybit", "ts": NOW - 300,
                 "url": "https://b/1"}]
        with mock.patch.object(news, "_coin_headlines", lambda c, a, s: ([], "")), \
                mock.patch.object(news, "_exchange_items", lambda since: (exch, [])), \
                mock.patch.object(news, "_market_headlines", lambda since, tracked=(): ([], [])), \
                mock.patch.object(news, "_fed_latest", lambda: None):
            d = news.build_digest(FakeApp(self.store), hours=24, fresh=True)
        rvn = [c for c in d["coins"] if c["ticker"] == "RVN"][0]["headlines"]
        self.assertEqual(sorted(h["source"] for h in rvn), ["Binance", "Bybit"])


class TestNewsFilters(Base):
    def test_converter_titles_are_noise(self):
        for t in ("Convert 10 ZAR to ETC", "ETC: Convert 5 USD to ETC", "convert 1 ETC to BTC",
                  "Ethereum Classic: Convert ETC to USD"):
            self.assertTrue(news._noise({"title": t, "source": "Bybit"}), t)
        self.assertFalse(news._noise({"title": "Ethereum Classic upgrade to convert fees", "source": "x"}))

    def test_market_feed_rules(self):
        decrypt = """<rss><channel>
          <item><title>OpenAI launches a new model for coding</title><link>https://d/1</link>
                <pubDate>Thu, 08 Oct 2026 06:00:00 +0000</pubDate></item>
          <item><title>Bitcoin miners add record hashrate</title><link>https://d/2</link>
                <pubDate>Thu, 08 Oct 2026 05:00:00 +0000</pubDate></item>
          <item><title>Podcast: Bitcoin and the future</title><link>https://d/3</link>
                <pubDate>Mon, 05 Oct 2026 05:00:00 +0000</pubDate></item>
          <item><title>Fed minutes spark rate-cut bets</title><link>https://d/4</link>
                <pubDate>Thu, 08 Oct 2026 04:00:00 +0000</pubDate></item></channel></rss>"""
        routes = {"https://decrypt.co/feed": decrypt.encode(),
                  "https://www.theblock.co/rss.xml": fixture("theblock.xml")}
        with mock.patch.object(news, "_get",
                               lambda url, timeout=15: routes.get(url, b"<rss><channel></channel></rss>")):
            out, errs = news._market_headlines(NOW - 72 * 3600,
                                               [{"ticker": "SOL", "name": "Solana"}])
        titles = [h["title"] for h in out]
        self.assertEqual(errs, [])
        self.assertNotIn("OpenAI launches a new model for coding", titles)
        self.assertIn("Bitcoin miners add record hashrate", titles)
        self.assertNotIn("Podcast: Bitcoin and the future", titles)              # Decrypt older than 48 h
        self.assertIn("Fed minutes spark rate-cut bets", titles)
        self.assertTrue(any(h["source"] == "The Block" for h in out))
        self.assertTrue(any("Solana" in t for t in titles))                       # tracked coin name passes

    def test_cluster_merges_near_duplicates(self):
        items = [{"title": "Polygon and TRON launch cross chain bridge for stablecoins", "source": "A", "ts": NOW},
                 {"title": "Polygon and TRON launch cross chain bridge for stablecoins today", "source": "B", "ts": NOW - 60},
                 {"title": "Polygon and TRON launch cross chain bridge for stablecoins", "source": "C", "ts": NOW - 90},
                 {"title": "Something else entirely different happened", "source": "D", "ts": NOW}]
        out = news._cluster(items)
        self.assertEqual(len(out), 2)
        self.assertEqual((out[0]["cluster_size"], out[0]["cluster_note"]), (3, "3 sources"))
        self.assertNotIn("cluster_size", out[1])
        self.assertEqual([news._plural_sources(n) for n in (2, 5, 11, 21)] + [news._plural_sources(1)],
                         ["2 sources", "5 sources", "11 sources", "21 sources", "1 source"])

    def test_cluster_respects_window(self):
        t = "Same words same words same words same words"
        out = news._cluster([{"title": t, "source": "A", "ts": NOW},
                             {"title": t, "source": "B", "ts": NOW - 7 * 3600}])
        self.assertEqual(len(out), 2)


class TestMacro(unittest.TestCase):
    def ts(self, y, m, d, h=12):
        return int(datetime(y, m, d, h, tzinfo=timezone.utc).timestamp())

    def test_next_meeting(self):
        m = news.macro_info(self.ts(2026, 10, 8))
        self.assertEqual((m["fomc_next"], m["days_to_fomc"], m["fomc_today"]),
                         ("2026-10-27..2026-10-28", 19, False))
        self.assertEqual(m["line"], "Next FOMC Oct 27-28 (in 19 days)")

    def test_meeting_days(self):
        m = news.macro_info(self.ts(2026, 10, 27))
        self.assertTrue(m["fomc_today"])
        self.assertIn("decision Oct 28", m["line"])
        self.assertIn("decision today", news.macro_info(self.ts(2026, 12, 9))["line"])

    def test_after_last_meeting_and_fed_item(self):
        m = news.macro_info(self.ts(2027, 1, 5))
        self.assertIsNone(m["fomc_next"])
        now = self.ts(2026, 10, 8)
        fresh = {"title": "Fed releases minutes", "ts": now - 3 * 3600, "url": "u"}
        old = {"title": "Old release", "ts": now - 30 * 3600, "url": "u"}
        self.assertIn("Fed: Fed releases minutes (3 h ago)", news.macro_info(now, fresh)["line"])
        self.assertIsNone(news.macro_info(now, old)["fed_press"])

    def test_fed_feed_parses(self):
        with mock.patch.object(news, "_get", lambda url, timeout=15: fixture("fed_press.xml")):
            it = news._fed_latest()
        self.assertTrue(it["title"].startswith("Minutes of the Federal Open Market Committee"))


class TestSettingsApi(unittest.TestCase):
    def test_validation(self):
        self.assertEqual(server._news_settings({"news_enabled": 0, "news_min_score": "80",
                                                "news_max_per_day": 5, "news_tg_channels": "WatcherGuru, @WuBlockchain"}),
                         {"news_enabled": "0", "news_min_score": "80", "news_max_per_day": "5",
                          "news_tg_channels": "WatcherGuru,WuBlockchain"})
        self.assertEqual(server._news_settings({"tg_chat_id": "1"}), {})
        for bad in ({"news_min_score": "abc"}, {"news_min_score": 101}, {"news_max_per_day": -1},
                    {"news_tg_channels": "ok_channel, bad name!"}):
            with self.assertRaises(ValueError):
                server._news_settings(bad)


class TestStore(Base):
    def test_prune_and_unique(self):
        self.assertIsNotNone(self.store.news_add("okx", "1", "t", "t", "", NOW, NOW - 15 * 86400, "", 0))
        self.assertIsNone(self.store.news_add("okx", "1", "t", "t", "", NOW, NOW, "", 0))
        self.assertIsNotNone(self.store.news_add("okx", "2", "t2", "t2", "", NOW, NOW - 86400, "", 0))
        self.store.prune()
        self.assertEqual([r["ext_id"] for r in self.store.news_list()], ["2"])

    def test_defaults_exist(self):
        s = self.store.settings()
        self.assertEqual((s["news_enabled"], s["news_min_score"], s["news_max_per_day"], s["news_tg_channels"]),
                         ("1", "70", "10", "WatcherGuru"))


if __name__ == "__main__":
    unittest.main(verbosity=1)
