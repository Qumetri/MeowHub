"""SQLite persistence for the crypto tracker. Stdlib only.

One connection shared across threads under a lock. WAL so the HTTP thread can
read while the feed thread writes. Every public method is safe to call from
any thread.
"""
import json
import os
import sqlite3
import threading
import time

DB_PATH = os.environ.get("CRYPTO_DB", "/data/crypto.db")

DEFAULT_COINS = [
    ("BTCUSDT", "BTC", "Bitcoin", "binance"),
    ("ETHUSDT", "ETH", "Ethereum", "binance"),
    ("ETCUSDT", "ETC", "Ethereum Classic", "binance"),
    ("RVNUSDT", "RVN", "Ravencoin", "binance"),
    ("TONUSDT", "TON", "Toncoin", "binance"),
    ("TRXUSDT", "TRX", "TRON", "binance"),
    ("SOLUSDT", "SOL", "Solana", "binance"),
    # Binance halted XMRUSDT in Feb 2024, so Monero comes from Kraken.
    ("XMRUSD", "XMR", "Monero", "kraken"),
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS coins (
    symbol   TEXT PRIMARY KEY,   -- BTCUSDT (binance) / XMRUSD (kraken)
    ticker   TEXT NOT NULL,      -- BTC
    name     TEXT NOT NULL,      -- Bitcoin
    source   TEXT NOT NULL DEFAULT 'binance',   -- binance | kraken
    enabled  INTEGER NOT NULL DEFAULT 1,
    sort     INTEGER NOT NULL DEFAULT 0
);

-- A price level to watch. direction 'above' fires on an upward crossing,
-- 'below' on a downward one. `armed` is what stops a target that is already
-- satisfied at creation time from firing immediately.
CREATE TABLE IF NOT EXISTS targets (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol      TEXT NOT NULL,
    direction   TEXT NOT NULL CHECK (direction IN ('above','below')),
    price       REAL NOT NULL,
    note        TEXT NOT NULL DEFAULT '',
    enabled     INTEGER NOT NULL DEFAULT 1,
    repeat      INTEGER NOT NULL DEFAULT 0,
    cooldown_s  INTEGER NOT NULL DEFAULT 900,
    armed       INTEGER NOT NULL DEFAULT 1,
    last_fired  INTEGER NOT NULL DEFAULT 0,
    fire_count  INTEGER NOT NULL DEFAULT 0,
    created     INTEGER NOT NULL
);

-- Per-symbol "huge move" rule: percent change over a rolling window.
CREATE TABLE IF NOT EXISTS fluctuation (
    symbol      TEXT PRIMARY KEY,
    pct         REAL NOT NULL DEFAULT 3.0,
    window_s    INTEGER NOT NULL DEFAULT 300,
    cooldown_s  INTEGER NOT NULL DEFAULT 1800,
    enabled     INTEGER NOT NULL DEFAULT 1,
    last_fired  INTEGER NOT NULL DEFAULT 0,
    last_price  REAL NOT NULL DEFAULT 0
);

-- 1-minute candles, used for the charts and the daily summary.
CREATE TABLE IF NOT EXISTS candles (
    symbol TEXT NOT NULL,
    ts     INTEGER NOT NULL,       -- unix seconds, minute-aligned
    o REAL NOT NULL, h REAL NOT NULL, l REAL NOT NULL, c REAL NOT NULL,
    v REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (symbol, ts)
);

CREATE TABLE IF NOT EXISTS events (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    ts       INTEGER NOT NULL,
    kind     TEXT NOT NULL,        -- target | fluctuation | summary | system
    symbol   TEXT NOT NULL DEFAULT '',
    message  TEXT NOT NULL,
    price    REAL,
    sent     INTEGER NOT NULL DEFAULT 0,
    error    TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events (ts DESC);
"""

DEFAULT_SETTINGS = {
    "tg_token": "",
    "tg_chat_id": "",
    "tg_enabled": "0",
    "summary_enabled": "0",
    "summary_hour": "9",
    "quiet_hours": "",          # e.g. "23-7"; empty = always notify
    "retention_days": "45",
    "last_summary_date": "",
}


class Store:
    def __init__(self, path=DB_PATH):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self._lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.executescript(SCHEMA)
        self._migrate()
        self._seed()

    def _migrate(self):
        """Additive column migrations. CREATE TABLE IF NOT EXISTS silently does
        nothing on an existing table, so a new column has to be added here or
        every query referencing it fails on an upgraded install."""
        with self._lock:
            cols = {r["name"] for r in self.db.execute("PRAGMA table_info(coins)")}
            if "source" not in cols:
                self.db.execute(
                    "ALTER TABLE coins ADD COLUMN source TEXT NOT NULL DEFAULT 'binance'")
                self.db.commit()

    def _seed(self):
        with self._lock:
            for k, v in DEFAULT_SETTINGS.items():
                self.db.execute(
                    "INSERT OR IGNORE INTO settings (key,value) VALUES (?,?)", (k, v)
                )
            # Default coins seed the FIRST run only. Re-seeding on every start
            # would resurrect a coin the user deliberately removed, which makes
            # the remove button a lie.
            have = self.db.execute("SELECT COUNT(*) AS n FROM coins").fetchone()["n"]
            if not have:
                for i, (sym, tick, name, src) in enumerate(DEFAULT_COINS):
                    self.db.execute(
                        "INSERT INTO coins (symbol,ticker,name,source,enabled,sort)"
                        " VALUES (?,?,?,?,1,?)", (sym, tick, name, src, i)
                    )
                    self.db.execute(
                        "INSERT OR IGNORE INTO fluctuation (symbol) VALUES (?)", (sym,)
                    )
            self.db.commit()

    # ---------- settings ----------
    def settings(self):
        with self._lock:
            return {r["key"]: r["value"]
                    for r in self.db.execute("SELECT key,value FROM settings")}

    def get(self, key, default=""):
        return self.settings().get(key, default)

    def set_many(self, mapping):
        with self._lock:
            for k, v in mapping.items():
                self.db.execute(
                    "INSERT INTO settings (key,value) VALUES (?,?)"
                    " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (k, str(v)),
                )
            self.db.commit()

    # ---------- coins ----------
    def coins(self, enabled_only=True):
        q = "SELECT * FROM coins"
        if enabled_only:
            q += " WHERE enabled=1"
        q += " ORDER BY sort, symbol"
        with self._lock:
            return [dict(r) for r in self.db.execute(q)]

    def add_coin(self, symbol, ticker, name, source="binance"):
        with self._lock:
            n = self.db.execute("SELECT COALESCE(MAX(sort),0)+1 AS s FROM coins").fetchone()["s"]
            self.db.execute(
                "INSERT OR IGNORE INTO coins (symbol,ticker,name,source,enabled,sort)"
                " VALUES (?,?,?,?,1,?)", (symbol, ticker, name, source, n))
            self.db.execute("INSERT OR IGNORE INTO fluctuation (symbol) VALUES (?)", (symbol,))
            self.db.commit()

    def remove_coin(self, symbol):
        with self._lock:
            self.db.execute("DELETE FROM coins WHERE symbol=?", (symbol,))
            self.db.execute("DELETE FROM fluctuation WHERE symbol=?", (symbol,))
            self.db.execute("DELETE FROM targets WHERE symbol=?", (symbol,))
            self.db.commit()

    # ---------- targets ----------
    def targets(self, symbol=None):
        q = "SELECT * FROM targets"
        a = ()
        if symbol:
            q += " WHERE symbol=?"
            a = (symbol,)
        q += " ORDER BY symbol, price"
        with self._lock:
            return [dict(r) for r in self.db.execute(q, a)]

    def add_target(self, symbol, direction, price, note="", repeat=0,
                   cooldown_s=900, armed=1):
        with self._lock:
            cur = self.db.execute(
                "INSERT INTO targets (symbol,direction,price,note,repeat,cooldown_s,armed,created)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (symbol, direction, float(price), note, int(repeat),
                 int(cooldown_s), int(armed), int(time.time())),
            )
            self.db.commit()
            return cur.lastrowid

    def update_target(self, tid, **fields):
        allowed = {"enabled", "price", "note", "repeat", "cooldown_s",
                   "armed", "direction", "last_fired", "fire_count"}
        sets, args = [], []
        for k, v in fields.items():
            if k in allowed:
                sets.append(f"{k}=?")
                args.append(v)
        if not sets:
            return
        args.append(tid)
        with self._lock:
            self.db.execute(f"UPDATE targets SET {','.join(sets)} WHERE id=?", args)
            self.db.commit()

    def delete_target(self, tid):
        with self._lock:
            self.db.execute("DELETE FROM targets WHERE id=?", (tid,))
            self.db.commit()

    # ---------- fluctuation ----------
    def fluctuation(self):
        with self._lock:
            return {r["symbol"]: dict(r) for r in self.db.execute("SELECT * FROM fluctuation")}

    def set_fluctuation(self, symbol, **fields):
        allowed = {"pct", "window_s", "cooldown_s", "enabled", "last_fired", "last_price"}
        sets, args = [], []
        for k, v in fields.items():
            if k in allowed:
                sets.append(f"{k}=?")
                args.append(v)
        if not sets:
            return
        args.append(symbol)
        with self._lock:
            self.db.execute("INSERT OR IGNORE INTO fluctuation (symbol) VALUES (?)", (symbol,))
            self.db.execute(f"UPDATE fluctuation SET {','.join(sets)} WHERE symbol=?", args)
            self.db.commit()

    # ---------- candles ----------
    def upsert_candle(self, symbol, ts, o, h, l, c, v=0.0):
        with self._lock:
            self.db.execute(
                "INSERT INTO candles (symbol,ts,o,h,l,c,v) VALUES (?,?,?,?,?,?,?)"
                " ON CONFLICT(symbol,ts) DO UPDATE SET"
                "   h=MAX(h,excluded.h), l=MIN(l,excluded.l),"
                "   c=excluded.c, v=excluded.v",
                (symbol, ts, o, h, l, c, v),
            )
            self.db.commit()

    def upsert_candles(self, symbol, rows):
        with self._lock:
            self.db.executemany(
                "INSERT INTO candles (symbol,ts,o,h,l,c,v) VALUES (?,?,?,?,?,?,?)"
                " ON CONFLICT(symbol,ts) DO UPDATE SET"
                "   o=excluded.o, h=excluded.h, l=excluded.l,"
                "   c=excluded.c, v=excluded.v",
                [(symbol,) + tuple(r) for r in rows],
            )
            self.db.commit()

    def candles(self, symbol, since=None, limit=1500):
        q = "SELECT ts,o,h,l,c,v FROM candles WHERE symbol=?"
        a = [symbol]
        if since:
            q += " AND ts>=?"
            a.append(since)
        q += " ORDER BY ts DESC LIMIT ?"
        a.append(limit)
        with self._lock:
            rows = [dict(r) for r in self.db.execute(q, a)]
        return list(reversed(rows))

    def newest_candle_ts(self, symbol):
        with self._lock:
            r = self.db.execute(
                "SELECT MAX(ts) AS t FROM candles WHERE symbol=?", (symbol,)
            ).fetchone()
        return r["t"] if r and r["t"] else None

    def delete_candles(self, symbol):
        with self._lock:
            self.db.execute("DELETE FROM candles WHERE symbol=?", (symbol,))
            self.db.commit()

    def prune(self):
        days = int(self.get("retention_days", "45") or 45)
        cutoff = int(time.time()) - days * 86400
        with self._lock:
            self.db.execute("DELETE FROM candles WHERE ts<?", (cutoff,))
            self.db.execute("DELETE FROM events WHERE ts<?", (cutoff,))
            self.db.commit()

    # ---------- events ----------
    def add_event(self, kind, message, symbol="", price=None, sent=0, error=""):
        with self._lock:
            cur = self.db.execute(
                "INSERT INTO events (ts,kind,symbol,message,price,sent,error)"
                " VALUES (?,?,?,?,?,?,?)",
                (int(time.time()), kind, symbol, message, price, sent, error),
            )
            self.db.commit()
            return cur.lastrowid

    def mark_event(self, eid, sent, error=""):
        with self._lock:
            self.db.execute("UPDATE events SET sent=?,error=? WHERE id=?", (sent, error, eid))
            self.db.commit()

    def events(self, limit=100):
        with self._lock:
            return [dict(r) for r in self.db.execute(
                "SELECT * FROM events ORDER BY ts DESC, id DESC LIMIT ?", (limit,))]
