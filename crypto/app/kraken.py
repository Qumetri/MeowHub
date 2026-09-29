"""Kraken market data — the second source.

Exists because Binance does not trade everything. Monero is the case that
forced it: Binance still *lists* XMRUSDT and its /ticker/price still answers,
but the symbol's status is BREAK and the last trade was February 2024, so the
price it returns is years stale. A tracker that believed it would alert on a
number that no longer means anything.

Symbols are stored without the slash (XMRUSD) so they are safe in URLs and
database keys; Kraken's wire format (XMR/USD) is derived when talking to it.
"""
import json
import logging
import random
import socket
import threading
import time
import urllib.parse
import urllib.request

from binance import WebSocket, WSError, STALL_TIMEOUT, MAX_CONN_AGE

log = logging.getLogger("kraken")

WS_HOST = "ws.kraken.com"
WS_PORT = 443
WS_PATH = "/v2"
REST = "https://api.kraken.com"

QUOTES = ("USDT", "USDC", "USD", "EUR", "XBT", "BTC")


def to_pair(symbol):
    """XMRUSD -> XMR/USD (Kraken's wire format)."""
    s = symbol.upper().replace("/", "")
    for q in QUOTES:
        if s.endswith(q) and len(s) > len(q):
            return f"{s[:-len(q)]}/{q}"
    return s


def from_pair(pair):
    """XMR/USD -> XMRUSD."""
    return pair.replace("/", "").upper()


def rest_json(path, params=None, timeout=20):
    url = REST + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "meowhub-crypto/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.loads(r.read().decode())
    if d.get("error"):
        raise RuntimeError("; ".join(d["error"]))
    return d.get("result", {})


def symbol_exists(symbol):
    """True only if the pair is actually tradable right now."""
    try:
        res = rest_json("/0/public/AssetPairs", {"pair": to_pair(symbol)}, timeout=15)
    except Exception:
        return False
    for _, v in res.items():
        if v.get("status", "online") == "online":
            return True
    return False


def fetch_ticker(symbol):
    res = rest_json("/0/public/Ticker", {"pair": to_pair(symbol)})
    for _, v in res.items():
        last = float(v["c"][0])
        op = float(v["o"])
        return {
            "price": last,
            "open": op,
            "high": float(v["h"][1]),
            "low": float(v["l"][1]),
            "vol": float(v["v"][1]),
            "ts": int(time.time()),
        }
    raise RuntimeError(f"no ticker for {symbol}")


def fetch_klines(symbol, limit=720, since=None):
    """-> [(ts_seconds, o, h, l, c, v), ...] oldest first.

    Kraken's OHLC endpoint returns at most 720 one-minute candles, so charts
    for a Kraken-sourced coin start with 12h of history rather than Binance's
    1000 minutes. Live candles extend it from there.
    """
    params = {"pair": to_pair(symbol), "interval": 1}
    if since:
        params["since"] = since
    res = rest_json("/0/public/OHLC", params)
    for k, v in res.items():
        if k == "last":
            continue
        rows = [(int(r[0]), float(r[1]), float(r[2]), float(r[3]),
                 float(r[4]), float(r[6])) for r in v]
        return rows[-limit:]
    return []


class Feed(threading.Thread):
    """Kraken WS v2 ticker stream. Same contract as binance.Feed."""

    daemon = True

    def __init__(self, symbols_fn, on_tick, on_state=None):
        super().__init__(name="kraken-feed")
        self.symbols_fn = symbols_fn
        self.on_tick = on_tick
        self.on_state = on_state or (lambda *_: None)
        self.last_msg = 0.0
        self.connected = False
        self._stopping = threading.Event()
        self._wake = threading.Event()

    def stop(self):
        self._stopping.set()
        self._wake.set()

    def resubscribe(self):
        self._wake.set()

    def run(self):
        backoff = 1.0
        while not self._stopping.is_set():
            symbols = self.symbols_fn()
            if not symbols:
                # Nothing to watch; idle cheaply until a Kraken coin is added.
                self._wake.wait(10)
                self._wake.clear()
                continue
            pairs = [to_pair(s) for s in symbols]
            ws = WebSocket(WS_HOST, WS_PORT, WS_PATH)
            try:
                ws.connect()
                ws._send_frame(0x1, json.dumps({
                    "method": "subscribe",
                    "params": {"channel": "ticker", "symbol": pairs},
                }).encode())
                self.connected = True
                self.last_msg = time.time()
                self.on_state(True, "")
                log.info("stream connected: %d pairs", len(pairs))
                backoff = 1.0
                opened = time.time()
                while not self._stopping.is_set():
                    if self._wake.is_set():
                        self._wake.clear()
                        log.info("resubscribe requested")
                        break
                    if time.time() - opened > MAX_CONN_AGE:
                        break
                    try:
                        msg = ws.recv_message()
                    except socket.timeout:
                        if time.time() - self.last_msg > STALL_TIMEOUT:
                            log.warning("stream stalled, reconnecting")
                            break
                        ws.ping()
                        continue
                    if msg is None:
                        log.info("server closed the stream")
                        break
                    self.last_msg = time.time()
                    self._handle(msg)
            except Exception as e:                      # noqa: BLE001
                log.warning("stream error: %s", e)
                self.on_state(False, str(e))
            finally:
                self.connected = False
                ws.close()
            if self._stopping.is_set():
                break
            self.on_state(False, "reconnecting")
            self._stopping.wait(random.uniform(0, min(backoff, 60)))
            backoff = min(backoff * 2, 60)

    def _handle(self, msg):
        try:
            d = json.loads(msg)
        except ValueError:
            return
        if d.get("channel") != "ticker":
            return
        for row in d.get("data", []):
            try:
                sym = from_pair(row["symbol"])
                price = float(row["last"])
            except (KeyError, TypeError, ValueError):
                continue
            if price <= 0:
                continue
            stats = {
                "high": _f(row.get("high")),
                "low": _f(row.get("low")),
                "vol": _f(row.get("volume")),
                "ts": int(time.time()),
            }
            # Kraken gives the 24h change directly; derive `open` from it so the
            # rest of the app can treat both sources identically.
            pct = _f(row.get("change_pct"))
            stats["open"] = price / (1 + pct / 100) if pct not in (None, -100) else price
            self.on_tick(sym, price, stats)


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
