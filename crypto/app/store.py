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
NEWS_KEEP_DAYS = 14

DEFAULT_COINS = [
    ("BTCUSDT", "BTC", "Bitcoin", "binance"),
    ("ETHUSDT", "ETH", "Ethereum", "binance"),
    ("ETCUSDT", "ETC", "Ethereum Classic", "binance"),
    ("RVNUSDT", "RVN", "Ravencoin", "binance"),
    # Toncoin rebranded to Gram; Binance halted every TON* pair on 2026-06-30.
    ("GRAMUSDT", "GRAM", "Gram", "binance"),
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
    sort     INTEGER NOT NULL DEFAULT 0,
    -- Listing health. A pair can be halted long after it was added (Toncoin
    -- was, on 2026-06-30) and the exchange keeps serving its frozen last
    -- price, so tracked coins are re-checked, not trusted forever.
    health      TEXT NOT NULL DEFAULT 'ok',      -- ok | halted | stale
    health_note TEXT NOT NULL DEFAULT '',
    health_at   INTEGER NOT NULL DEFAULT 0
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

-- Per-symbol "huge move" rule: a swing of `pct` within a rolling window,
-- reported again at every further `pct` step while the move keeps going. At
-- `urgent_pct` the alert is sent `urgent_repeat` times (follow-ups carry the
-- live price). ep_* is the open episode: direction, the price it is measured
-- from, the last step reported, the move's extreme so far (ep_peak) and the
-- last pullback step reported from that extreme (ep_pb). cooldown_s is unused since episodes; it
-- stays so older databases keep working.
CREATE TABLE IF NOT EXISTS fluctuation (
    symbol        TEXT PRIMARY KEY,
    pct           REAL NOT NULL DEFAULT 5.0,
    window_s      INTEGER NOT NULL DEFAULT 3600,
    cooldown_s    INTEGER NOT NULL DEFAULT 3600,
    enabled       INTEGER NOT NULL DEFAULT 1,
    last_fired    INTEGER NOT NULL DEFAULT 0,
    last_price    REAL NOT NULL DEFAULT 0,
    urgent_pct    REAL NOT NULL DEFAULT 10.0,
    urgent_repeat INTEGER NOT NULL DEFAULT 3,
    ep_dir        INTEGER NOT NULL DEFAULT 0,
    ep_anchor     REAL NOT NULL DEFAULT 0,
    ep_step       INTEGER NOT NULL DEFAULT 0,
    ep_peak       REAL NOT NULL DEFAULT 0,
    ep_pb         INTEGER NOT NULL DEFAULT 0
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

-- Breaking-news watcher (newswatch.py): every item any source has ever shown us,
-- so a restart or a re-listed article is never announced twice. `cluster` is the
-- id of the first row of the same story (word-trigram Jaccard >= 0.6 within 6 h);
-- the cluster's size is just COUNT(*) per cluster. `seeded` marks rows stored by
-- a source's silent first run: their first_seen is the seeding time, not the
-- time the story appeared, so they are left out of latency figures.
CREATE TABLE IF NOT EXISTS news_seen (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    source     TEXT NOT NULL,            -- binance-161 | upbit | tg:WatcherGuru | ...
    ext_id     TEXT NOT NULL,            -- the source's own id (article code, post id, guid)
    title      TEXT NOT NULL DEFAULT '',
    title_norm TEXT NOT NULL DEFAULT '', -- lowercase words, for the trigram match
    url        TEXT NOT NULL DEFAULT '',
    published  INTEGER NOT NULL DEFAULT 0,   -- unix s, 0 = unknown
    first_seen INTEGER NOT NULL,
    coins      TEXT NOT NULL DEFAULT '', -- tracked tickers it names, comma-separated
    score      INTEGER NOT NULL DEFAULT 0,
    cluster    INTEGER NOT NULL DEFAULT 0,
    pushed     INTEGER NOT NULL DEFAULT 0,
    seeded     INTEGER NOT NULL DEFAULT 0,
    UNIQUE (source, ext_id)
);
CREATE INDEX IF NOT EXISTS idx_news_first_seen ON news_seen (first_seen DESC);
"""

DEFAULT_SETTINGS = {
    "tg_token": "",
    "tg_token_override": "",    # set by the helper bot's Bots page; wins over .env
    "tg_chat_id": "",
    "tg_enabled": "0",
    "summary_enabled": "0",
    "summary_hour": "9",
    "quiet_hours": "",          # e.g. "23-7"; empty = always notify
    "retention_days": "45",
    "last_summary_date": "",
    # Newswatch (breaking-news pushes). See newswatch.py.
    "news_enabled": "1",
    "news_min_score": "70",
    "news_max_per_day": "10",
    "news_tg_channels": "WatcherGuru",
}


# Settings that may come from the environment (.env via compose) instead of
# the UI. When set there, the environment wins and the UI shows the field as
# managed by .env; a value saved in the UI is kept only as a fallback.
ENV_SETTINGS = {"tg_token": "CRYPTO_TG_TOKEN", "tg_chat_id": "CRYPTO_TG_CHAT_ID"}


def env_managed():
    return {k for k, var in ENV_SETTINGS.items() if os.environ.get(var, "").strip()}


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
            add = {
                "source": "TEXT NOT NULL DEFAULT 'binance'",
                "health": "TEXT NOT NULL DEFAULT 'ok'",
                "health_note": "TEXT NOT NULL DEFAULT ''",
                "health_at": "INTEGER NOT NULL DEFAULT 0",
            }
            for col, decl in add.items():
                if col not in cols:
                    self.db.execute(f"ALTER TABLE coins ADD COLUMN {col} {decl}")
            cols = {r["name"] for r in self.db.execute("PRAGMA table_info(fluctuation)")}
            add = {
                "urgent_pct": "REAL NOT NULL DEFAULT 10.0",
                "urgent_repeat": "INTEGER NOT NULL DEFAULT 3",
                "ep_dir": "INTEGER NOT NULL DEFAULT 0",
                "ep_anchor": "REAL NOT NULL DEFAULT 0",
                "ep_step": "INTEGER NOT NULL DEFAULT 0",
                "ep_peak": "REAL NOT NULL DEFAULT 0",
                "ep_pb": "INTEGER NOT NULL DEFAULT 0",
            }
            for col, decl in add.items():
                if col not in cols:
                    self.db.execute(f"ALTER TABLE fluctuation ADD COLUMN {col} {decl}")
            self.db.commit()

    def _seed(self):
        with self._lock:
            for k, v in DEFAULT_SETTINGS.items():
                self.db.execute(
                    "INSERT OR IGNORE INTO settings (key,value) VALUES (?,?)", (k, v)
                )
            # A token and chat given in .env mean "deliver alerts": switch
            # delivery on once. Only once, so turning it off in the UI sticks.
            if {"tg_token", "tg_chat_id"} <= env_managed():
                done = self.db.execute(
                    "SELECT 1 FROM settings WHERE key='tg_env_enabled'").fetchone()
                if not done:
                    self.db.execute("UPDATE settings SET value='1' WHERE key='tg_enabled'")
                    self.db.execute("INSERT INTO settings (key,value) VALUES ('tg_env_enabled','1')")
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
            out = {r["key"]: r["value"]
                   for r in self.db.execute("SELECT key,value FROM settings")}
        # Effective token: page override > .env > value saved in the UI.
        ui_tok = (out.get("tg_token") or "").strip()
        for k, var in ENV_SETTINGS.items():
            v = os.environ.get(var, "").strip()
            if v:
                out[k] = v
        override = (out.get("tg_token_override") or "").strip()
        if override:
            out["tg_token"] = override
            out["tg_token_source"] = "override"
        elif os.environ.get("CRYPTO_TG_TOKEN", "").strip():
            out["tg_token_source"] = "env"
        elif ui_tok:
            out["tg_token_source"] = "ui"
        else:
            out["tg_token_source"] = "none"
        return out

    def get(self, key, default=""):
        return self.settings().get(key, default)

    def set_many(self, mapping):
        mapping = {k: v for k, v in mapping.items() if k not in env_managed()}
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

    def set_health(self, symbol, health, note=""):
        with self._lock:
            self.db.execute(
                "UPDATE coins SET health=?, health_note=?, health_at=? WHERE symbol=?",
                (health, note, int(time.time()), symbol))
            self.db.commit()

    def rename_coin(self, old, new, ticker, name, source):
        """Carry targets and rules across a ticker rename, so a rebrand does
        not silently drop the alerts you set up."""
        with self._lock:
            n = self.db.execute(
                "SELECT sort FROM coins WHERE symbol=?", (old,)).fetchone()
            sort = n["sort"] if n else 0
            self.db.execute(
                "INSERT OR IGNORE INTO coins (symbol,ticker,name,source,enabled,sort)"
                " VALUES (?,?,?,?,1,?)", (new, ticker, name, source, sort))
            self.db.execute("INSERT OR IGNORE INTO fluctuation (symbol) VALUES (?)", (new,))
            self.db.execute(
                "UPDATE fluctuation SET pct=(SELECT pct FROM fluctuation WHERE symbol=?),"
                " window_s=(SELECT window_s FROM fluctuation WHERE symbol=?),"
                " cooldown_s=(SELECT cooldown_s FROM fluctuation WHERE symbol=?),"
                " urgent_pct=(SELECT urgent_pct FROM fluctuation WHERE symbol=?),"
                " urgent_repeat=(SELECT urgent_repeat FROM fluctuation WHERE symbol=?)"
                " WHERE symbol=?", (old, old, old, old, old, new))
            self.db.execute("UPDATE targets SET symbol=? WHERE symbol=?", (new, old))
            self.db.execute("DELETE FROM coins WHERE symbol=?", (old,))
            self.db.execute("DELETE FROM fluctuation WHERE symbol=?", (old,))
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
        allowed = {"pct", "window_s", "cooldown_s", "enabled", "last_fired", "last_price",
                   "urgent_pct", "urgent_repeat", "ep_dir", "ep_anchor", "ep_step",
                   "ep_peak", "ep_pb"}
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
            self.db.execute("DELETE FROM news_seen WHERE first_seen<?",
                            (int(time.time()) - NEWS_KEEP_DAYS * 86400,))
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

    def count_events(self, kind, since):
        with self._lock:
            return self.db.execute(
                "SELECT COUNT(*) AS n FROM events WHERE kind=? AND ts>=?",
                (kind, since)).fetchone()["n"]

    # ---------- news_seen ----------
    def news_known(self, source):
        """-> set of ext_ids already stored for this source."""
        with self._lock:
            return {r["ext_id"] for r in self.db.execute(
                "SELECT ext_id FROM news_seen WHERE source=?", (source,))}

    def news_add(self, source, ext_id, title, title_norm, url, published,
                 first_seen, coins, score, cluster=0, seeded=0):
        """-> new row id, or None if (source, ext_id) was already stored."""
        with self._lock:
            cur = self.db.execute(
                "INSERT OR IGNORE INTO news_seen (source,ext_id,title,title_norm,url,"
                " published,first_seen,coins,score,cluster,pushed,seeded)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,0,?)",
                (source, ext_id, title, title_norm, url, int(published or 0),
                 int(first_seen), coins, int(score), int(cluster), int(seeded)))
            if not cur.rowcount:
                self.db.commit()
                return None
            rid = cur.lastrowid
            if not cluster:
                self.db.execute("UPDATE news_seen SET cluster=id WHERE id=?", (rid,))
            self.db.commit()
            return rid

    def news_set(self, rid, **fields):
        allowed = {"pushed", "cluster", "score", "coins"}
        sets = [f"{k}=?" for k in fields if k in allowed]
        if not sets:
            return
        args = [v for k, v in fields.items() if k in allowed] + [rid]
        with self._lock:
            self.db.execute(f"UPDATE news_seen SET {','.join(sets)} WHERE id=?", args)
            self.db.commit()

    def news_cluster_candidates(self, since):
        """Rows first seen at or after `since`: what a new item can be a repeat of."""
        with self._lock:
            return [dict(r) for r in self.db.execute(
                "SELECT id,source,title_norm,coins,cluster,first_seen FROM news_seen"
                " WHERE first_seen>=? ORDER BY id", (since,))]

    def news_pushed_count(self, since):
        with self._lock:
            return self.db.execute(
                "SELECT COUNT(*) AS n FROM news_seen WHERE pushed=1 AND first_seen>=?",
                (since,)).fetchone()["n"]

    def news_list(self, limit=50):
        with self._lock:
            return [dict(r) for r in self.db.execute(
                "SELECT id,source,ext_id,title,url,published,first_seen,coins,score,cluster,"
                "pushed,seeded FROM news_seen ORDER BY first_seen DESC, id DESC LIMIT ?",
                (limit,))]

    def news_since(self, since, limit=3000):
        """Rows about stories dated at or after `since` (publish time, else the
        time we saw them), each with the size of its cluster and the sources in it."""
        with self._lock:
            rows = [dict(r) for r in self.db.execute(
                "SELECT id,source,ext_id,title,url,published,first_seen,coins,score,cluster,"
                "pushed,seeded FROM news_seen"
                " WHERE (CASE WHEN published>0 THEN published ELSE first_seen END)>=?"
                " ORDER BY id DESC LIMIT ?", (since, limit))]
            if rows:
                ids = {r["cluster"] for r in rows}
                srcs = {}
                for r in self.db.execute("SELECT cluster,source FROM news_seen"):
                    if r["cluster"] in ids:
                        srcs.setdefault(r["cluster"], []).append(r["source"])
                for r in rows:
                    r["cluster_sources"] = srcs.get(r["cluster"], [r["source"]])
        return rows

    def news_source_stats(self, since):
        """-> {source: {"n": count, "last_new": ts, "lat": [first_seen - published]}}
        over live (non-seeded) rows since `since`."""
        out = {}
        with self._lock:
            for r in self.db.execute(
                    "SELECT source,published,first_seen FROM news_seen"
                    " WHERE seeded=0 AND first_seen>=?", (since,)):
                o = out.setdefault(r["source"], {"n": 0, "last_new": 0, "lat": []})
                o["n"] += 1
                o["last_new"] = max(o["last_new"], r["first_seen"])
                if r["published"] > 0 and r["first_seen"] >= r["published"]:
                    o["lat"].append(r["first_seen"] - r["published"])
        return out

    def news_last_new(self):
        with self._lock:
            return {r["source"]: r["t"] for r in self.db.execute(
                "SELECT source, MAX(first_seen) AS t FROM news_seen WHERE seeded=0"
                " GROUP BY source")}
