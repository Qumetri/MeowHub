"""Wiring: feed -> engine -> telegram, plus candle building and schedules."""
import logging
import os
import signal
import sys
import threading
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import binance                      # noqa: E402
import server                       # noqa: E402
from alerts import Engine, fmt_price   # noqa: E402
from store import Store             # noqa: E402
from telegram import Notifier       # noqa: E402

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)-7s %(name)-9s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("main")

BACKFILL_MINUTES = int(os.environ.get("CRYPTO_BACKFILL_MIN", "1440"))
BROADCAST_HZ = 1.0
RECONCILE_S = 60


class CandleBuilder:
    """Aggregates ticks into 1-minute candles, flushed on rollover.

    Writing every tick would mean thousands of sqlite commits an hour for no
    gain -- a minute is the finest resolution the charts show.
    """

    def __init__(self, store):
        self.store = store
        self.cur = {}                # symbol -> [minute_ts, o, h, l, c, v]
        self.lock = threading.Lock()

    def on_tick(self, symbol, price, stats=None):
        # Volume is deliberately left at 0 here: miniTicker carries a rolling
        # 24h figure, not this minute's. refresh_recent() overwrites the last
        # few candles with Binance's authoritative per-minute OHLCV.
        m = int(time.time()) // 60 * 60
        vol = 0.0
        with self.lock:
            c = self.cur.get(symbol)
            if c is None or c[0] != m:
                if c is not None:
                    self.store.upsert_candle(symbol, c[0], c[1], c[2], c[3], c[4], c[5])
                self.cur[symbol] = [m, price, price, price, price, vol]
                return
            c[2] = max(c[2], price)
            c[3] = min(c[3], price)
            c[4] = price
            c[5] = vol

    def flush(self):
        with self.lock:
            items = list(self.cur.items())
        for sym, c in items:
            try:
                self.store.upsert_candle(sym, c[0], c[1], c[2], c[3], c[4], c[5])
            except Exception as e:                      # noqa: BLE001
                log.warning("candle flush failed for %s: %s", sym, e)


class App:
    def __init__(self):
        self.store = Store()
        self.notifier = Notifier(self.store)
        self.engine = Engine(self.store, self.notifier)
        self.candles = CandleBuilder(self.store)
        self.hub = server.Hub()
        self.feed = binance.Feed(self._symbols, self._on_tick, self._on_state)
        self.feed_error = ""
        self._dirty = set()
        self._dirty_lock = threading.Lock()
        self._stopping = threading.Event()

    # ---------- feed plumbing ----------
    def _symbols(self):
        return [c["symbol"] for c in self.store.coins()]

    def _on_state(self, connected, err):
        self.feed_error = "" if connected else err
        self.hub.broadcast({"type": "status", "connected": connected, "error": err})

    def _on_tick(self, symbol, price, stats):
        self.engine.on_tick(symbol, price, stats)
        self.candles.on_tick(symbol, price, stats)
        with self._dirty_lock:
            self._dirty.add(symbol)

    # ---------- coin management ----------
    def add_coin(self, symbol, ticker, name):
        self.store.add_coin(symbol, ticker, name)
        self.engine.refresh_rules(force=True)
        threading.Thread(target=self._backfill_one, args=(symbol,), daemon=True).start()
        self.feed.resubscribe()

    def remove_coin(self, symbol):
        self.store.remove_coin(symbol)
        self.engine.refresh_rules(force=True)
        self.feed.resubscribe()

    # ---------- snapshots for the UI ----------
    def public_settings(self):
        s = self.store.settings()
        return {
            "tg_token": server.mask_token(s.get("tg_token", "")),
            "tg_token_set": bool(s.get("tg_token")),
            "tg_chat_id": s.get("tg_chat_id", ""),
            "tg_enabled": s.get("tg_enabled", "0"),
            "summary_enabled": s.get("summary_enabled", "0"),
            "summary_hour": s.get("summary_hour", "9"),
            "quiet_hours": s.get("quiet_hours", ""),
            "retention_days": s.get("retention_days", "45"),
        }

    def coin_rows(self):
        fl = self.store.fluctuation()
        out = []
        for c in self.store.coins():
            sym = c["symbol"]
            st = self.engine.stats.get(sym, {})
            out.append({
                "symbol": sym, "ticker": c["ticker"], "name": c["name"],
                "price": self.engine.prices.get(sym),
                "change24h": self.engine.change_24h(sym),
                "high": st.get("high"), "low": st.get("low"), "vol": st.get("vol"),
                "fluctuation": fl.get(sym, {}),
            })
        return out

    def state(self):
        return {
            "coins": self.coin_rows(),
            "targets": self.store.targets(),
            "settings": self.public_settings(),
            "events": self.store.events(limit=40),
            "feed": {"connected": self.feed.connected,
                     "error": self.feed_error,
                     "last_tick": self.feed.last_msg},
            "server_time": int(time.time()),
        }

    # ---------- background loops ----------
    def _broadcast_loop(self):
        while not self._stopping.is_set():
            self._stopping.wait(1.0 / BROADCAST_HZ)
            with self._dirty_lock:
                dirty, self._dirty = self._dirty, set()
            if not dirty:
                continue
            payload = {"type": "prices", "t": int(time.time()), "prices": {}}
            for sym in dirty:
                payload["prices"][sym] = {
                    "price": self.engine.prices.get(sym),
                    "change24h": self.engine.change_24h(sym),
                }
            self.hub.broadcast(payload)

    def _backfill_one(self, symbol):
        try:
            newest = self.store.newest_candle_ts(symbol)
            start = None
            if newest:
                start = (newest + 60) * 1000
            rows = binance.fetch_klines(symbol, "1m", limit=1000, start_ms=start)
            if rows:
                self.store.upsert_candles(symbol, rows)
                self.engine.seed_history(
                    symbol, [{"ts": r[0], "c": r[4]} for r in rows[-240:]])
            log.info("backfilled %s: %d candles", symbol, len(rows))
        except Exception as e:                          # noqa: BLE001
            log.warning("backfill failed for %s: %s", symbol, e)

    def refresh_recent(self):
        """Overwrite the last few minutes with Binance's own OHLCV.

        Locally built candles are assembled from sampled ticks, so their
        volume is unknown and their high/low can miss a wick between samples.
        This also repairs any gap left by a reconnect.
        """
        for sym in self._symbols():
            try:
                rows = binance.fetch_klines(sym, "1m", limit=15)
                if rows:
                    self.store.upsert_candles(sym, rows)
            except Exception as e:                      # noqa: BLE001
                log.debug("refresh_recent %s: %s", sym, e)

    def _reconcile(self):
        """REST snapshot. Thin coins can go minutes without a miniTicker push,
        so the stream alone is not enough to keep 24h stats honest."""
        syms = self._symbols()
        if not syms:
            return
        try:
            tickers = binance.fetch_tickers(syms)
        except Exception as e:                          # noqa: BLE001
            log.debug("reconcile failed: %s", e)
            return
        for sym, t in tickers.items():
            try:
                price = float(t["lastPrice"])
                stats = {"open": float(t["openPrice"]), "high": float(t["highPrice"]),
                         "low": float(t["lowPrice"]), "vol": float(t["quoteVolume"]),
                         "ts": int(time.time())}
            except (KeyError, ValueError):
                continue
            if price <= 0:
                continue
            # Feed through the engine so a level crossed during a stream gap
            # is still caught.
            self._on_tick(sym, price, stats)

    def _scheduler(self):
        last_reconcile = 0.0
        last_prune = 0.0
        last_flush = 0.0
        last_refresh = time.time()
        while not self._stopping.is_set():
            self._stopping.wait(5)
            now = time.time()
            try:
                if now - last_reconcile >= RECONCILE_S:
                    last_reconcile = now
                    self._reconcile()
                if now - last_flush >= 30:
                    last_flush = now
                    self.candles.flush()
                if now - last_refresh >= 300:
                    last_refresh = now
                    self.candles.flush()
                    self.refresh_recent()
                if now - last_prune >= 6 * 3600:
                    last_prune = now
                    self.store.prune()
                self._maybe_summary()
            except Exception as e:                      # noqa: BLE001
                log.warning("scheduler: %s", e)

    def _maybe_summary(self):
        s = self.store.settings()
        if s.get("summary_enabled") != "1":
            return
        try:
            hour = int(s.get("summary_hour", "9"))
        except ValueError:
            hour = 9
        now = datetime.now()
        today = now.strftime("%Y-%m-%d")
        # Persisted, not in-memory: a restart during the summary hour would
        # otherwise send a second copy.
        if now.hour == hour and s.get("last_summary_date") != today:
            self.store.set_many({"last_summary_date": today})
            log.info("sending daily summary")
            self.engine.send_summary()

    # ---------- lifecycle ----------
    def start(self):
        for c in self.store.coins():
            self._backfill_one(c["symbol"])
        self.feed.start()
        threading.Thread(target=self._broadcast_loop, daemon=True, name="broadcast").start()
        threading.Thread(target=self._scheduler, daemon=True, name="scheduler").start()
        self.store.add_event("system", "Tracker started")
        log.info("tracking %s", ", ".join(self._symbols()))

    def stop(self, *_):
        log.info("shutting down")
        self._stopping.set()
        self.feed.stop()
        self.candles.flush()
        sys.exit(0)


def run():
    app = App()
    signal.signal(signal.SIGTERM, app.stop)
    signal.signal(signal.SIGINT, app.stop)
    app.start()
    server.serve(app, port=int(os.environ.get("CRYPTO_PORT", "9102")))


if __name__ == "__main__":
    run()
