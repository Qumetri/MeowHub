"""Binance market data: a minimal stdlib WebSocket client plus REST backfill.

No third-party dependencies, matching the rest of this stack. The WebSocket
subset implemented here is exactly what the public market streams use: text
frames, server pings, close frames, and (defensively) continuation frames.

Robustness comes from three places, not from the framing code:
  * a watchdog that forces a reconnect when ticks stop arriving,
  * exponential backoff with jitter so a Binance outage is not hammered,
  * a periodic REST snapshot that repairs anything the stream missed.
"""
import base64
import json
import logging
import os
import random
import socket
import ssl
import struct
import threading
import time
import urllib.parse
import urllib.request

log = logging.getLogger("binance")

WS_HOST = os.environ.get("BINANCE_WS_HOST", "stream.binance.com")
WS_PORT = int(os.environ.get("BINANCE_WS_PORT", "9443"))
REST = os.environ.get("BINANCE_REST", "https://api.binance.com")

# Binance drops an idle stream connection after 24h; reconnect well before that.
MAX_CONN_AGE = 20 * 3600
STALL_TIMEOUT = 45          # no frame for this long -> reconnect
CONNECT_TIMEOUT = 15


class WSError(Exception):
    pass


class WebSocket:
    """Barely enough RFC 6455 for a read-only market data stream."""

    def __init__(self, host, port, path):
        self.host, self.port, self.path = host, port, path
        self.sock = None
        self._buf = b""

    def connect(self):
        raw = socket.create_connection((self.host, self.port), timeout=CONNECT_TIMEOUT)
        raw.settimeout(STALL_TIMEOUT)
        ctx = ssl.create_default_context()
        self.sock = ctx.wrap_socket(raw, server_hostname=self.host)
        key = base64.b64encode(os.urandom(16)).decode()
        req = (
            f"GET {self.path} HTTP/1.1\r\n"
            f"Host: {self.host}\r\n"
            f"Upgrade: websocket\r\n"
            f"Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            f"Sec-WebSocket-Version: 13\r\n"
            f"User-Agent: meowhub-crypto/1.0\r\n\r\n"
        )
        self.sock.sendall(req.encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self.sock.recv(1024)
            if not chunk:
                raise WSError("connection closed during handshake")
            head += chunk
            if len(head) > 65536:
                raise WSError("handshake response too large")
        status = head.split(b"\r\n", 1)[0].decode("latin-1")
        if "101" not in status:
            raise WSError(f"handshake failed: {status}")
        # Anything after the header belongs to the frame stream.
        self._buf = head.split(b"\r\n\r\n", 1)[1]

    def _read(self, n):
        while len(self._buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise WSError("connection closed")
            self._buf += chunk
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def _send_frame(self, opcode, payload=b""):
        # Client frames must be masked (RFC 6455 §5.3).
        mask = os.urandom(4)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        n = len(payload)
        if n < 126:
            hdr = struct.pack("!BB", 0x80 | opcode, 0x80 | n)
        elif n < (1 << 16):
            hdr = struct.pack("!BBH", 0x80 | opcode, 0x80 | 126, n)
        else:
            hdr = struct.pack("!BBQ", 0x80 | opcode, 0x80 | 127, n)
        self.sock.sendall(hdr + mask + masked)

    def recv_message(self):
        """Return the next complete text message, transparently handling
        control frames and fragmentation. None means 'server closed'."""
        chunks = []
        while True:
            b0, b1 = self._read(2)
            fin = b0 & 0x80
            opcode = b0 & 0x0F
            length = b1 & 0x7F
            if length == 126:
                length = struct.unpack("!H", self._read(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self._read(8))[0]
            if b1 & 0x80:                       # server frames are never masked
                raise WSError("masked frame from server")
            if length > 8 << 20:
                raise WSError("frame too large")
            payload = self._read(length) if length else b""

            if opcode == 0x8:                   # close
                self._send_frame(0x8, payload[:2])
                return None
            if opcode == 0x9:                   # ping -> pong with same payload
                self._send_frame(0xA, payload)
                continue
            if opcode == 0xA:                   # pong, ignore
                continue
            if opcode in (0x1, 0x0):            # text / continuation
                chunks.append(payload)
                if fin:
                    return b"".join(chunks).decode("utf-8", "replace")
                continue
            if opcode == 0x2:                   # binary: not used by these streams
                chunks = []
                continue
            raise WSError(f"unexpected opcode {opcode}")

    def ping(self):
        self._send_frame(0x9, b"hb")

    def close(self):
        try:
            if self.sock:
                self._send_frame(0x8, struct.pack("!H", 1000))
        except Exception:
            pass
        try:
            if self.sock:
                self.sock.close()
        except Exception:
            pass
        self.sock = None


def rest_json(path, params=None, timeout=20):
    url = REST + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "meowhub-crypto/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def fetch_klines(symbol, interval="1m", limit=1000, start_ms=None):
    """-> [(ts_seconds, o, h, l, c, v), ...] oldest first."""
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    if start_ms:
        params["startTime"] = start_ms
    raw = rest_json("/api/v3/klines", params)
    return [
        (int(k[0]) // 1000, float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5]))
        for k in raw
    ]


def fetch_tickers(symbols):
    """24h stats for a set of symbols -> {symbol: {...}}."""
    q = json.dumps(list(symbols), separators=(",", ":"))
    raw = rest_json("/api/v3/ticker/24hr", {"symbols": q})
    return {r["symbol"]: r for r in raw}


def symbol_statuses(symbols):
    """-> {symbol: status} for many symbols in ONE call.

    Raises on transport failure rather than returning a value. This matters:
    "the exchange says this pair is not trading" and "I could not reach the
    exchange" must never look the same to the caller, or a rate-limit gets
    reported to the user as a delisting.

    One batched call also keeps the hourly health check well clear of the
    weight limits -- exchangeInfo is expensive, and asking per-coin is what
    provoked the rate-limiting in the first place.
    """
    symbols = list(symbols)
    if not symbols:
        return {}
    q = json.dumps(symbols, separators=(",", ":"))
    info = rest_json("/api/v3/exchangeInfo", {"symbols": q}, timeout=25)
    return {s["symbol"]: s.get("status") for s in info.get("symbols", [])}


def symbol_exists(symbol):
    """True only if the symbol is actually TRADING.

    /ticker/price is not enough: a delisted symbol keeps answering with the
    price it was frozen at. XMRUSDT still returns ~118 while its real market
    price elsewhere is ~540, because Binance halted it in February 2024 and the
    symbol sits at status BREAK. Checking the status is the difference between
    tracking a market and tracking a fossil.
    """
    try:
        info = rest_json("/api/v3/exchangeInfo", {"symbol": symbol}, timeout=15)
    except Exception:
        return False
    for s in info.get("symbols", []):
        if s.get("symbol") == symbol:
            return s.get("status") == "TRADING" and s.get("isSpotTradingAllowed", True)
    return False


class Feed(threading.Thread):
    """Streams miniTicker for a set of symbols and calls on_tick(symbol, price, stats).

    `symbols_fn` is re-read on every (re)connect so adding a coin in the UI
    takes effect on the next cycle without a restart.
    """

    daemon = True

    def __init__(self, symbols_fn, on_tick, on_state=None):
        super().__init__(name="binance-feed")
        self.symbols_fn = symbols_fn
        self.on_tick = on_tick
        self.on_state = on_state or (lambda *_: None)
        self.last_msg = 0.0
        self.connected = False
        self._stopping = threading.Event()
        self._wake = threading.Event()
        self._current = []

    def stop(self):
        self._stopping.set()
        self._wake.set()

    def resubscribe(self):
        """Ask the loop to reconnect (picks up a changed symbol list)."""
        self._wake.set()

    def run(self):
        backoff = 1.0
        while not self._stopping.is_set():
            symbols = [s.lower() for s in self.symbols_fn()]
            if not symbols:
                time.sleep(5)
                continue
            self._current = symbols
            streams = "/".join(f"{s}@miniTicker" for s in symbols)
            ws = WebSocket(WS_HOST, WS_PORT, f"/stream?streams={streams}")
            try:
                ws.connect()
                self.connected = True
                self.last_msg = time.time()
                self.on_state(True, "")
                log.info("stream connected: %d symbols", len(symbols))
                backoff = 1.0
                opened = time.time()
                while not self._stopping.is_set():
                    if self._wake.is_set():
                        self._wake.clear()
                        log.info("resubscribe requested")
                        break
                    if time.time() - opened > MAX_CONN_AGE:
                        log.info("recycling connection at max age")
                        break
                    try:
                        msg = ws.recv_message()
                    except socket.timeout:
                        # No frame within STALL_TIMEOUT. Probe once; if the
                        # socket is really dead this raises and we reconnect.
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
            except Exception as e:                      # noqa: BLE001 - keep the thread alive
                log.warning("stream error: %s", e)
                self.on_state(False, str(e))
            finally:
                self.connected = False
                ws.close()
            if self._stopping.is_set():
                break
            self.on_state(False, "reconnecting")
            # Full jitter backoff: avoids a thundering reconnect after an outage.
            delay = random.uniform(0, min(backoff, 60))
            self._stopping.wait(delay)
            backoff = min(backoff * 2, 60)

    def _handle(self, msg):
        try:
            payload = json.loads(msg)
        except ValueError:
            return
        data = payload.get("data", payload)
        if not isinstance(data, dict) or data.get("e") != "24hrMiniTicker":
            return
        sym = data.get("s")
        try:
            price = float(data["c"])
            stats = {
                "open": float(data["o"]),
                "high": float(data["h"]),
                "low": float(data["l"]),
                "vol": float(data.get("q", 0) or 0),
                "ts": int(data.get("E", time.time() * 1000)) // 1000,
            }
        except (KeyError, TypeError, ValueError):
            return
        if sym and price > 0:
            self.on_tick(sym, price, stats)
