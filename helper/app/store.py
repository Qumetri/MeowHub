"""sqlite state: bot token, owner, allowed users, password vault entries added
through the bot. The token lives here, not in .env -- same rule as the crypto
tracker -- and the file is created 0600 in a 0700 directory."""
import os
import sqlite3
import threading
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL DEFAULT '',
    added_ts INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS vault (
    id TEXT PRIMARY KEY, login TEXT NOT NULL, password TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '', updated_ts INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS members (
    id INTEGER PRIMARY KEY, username TEXT NOT NULL DEFAULT '', first_name TEXT NOT NULL DEFAULT '',
    last_name TEXT NOT NULL DEFAULT '', lang TEXT NOT NULL DEFAULT '',
    services TEXT NOT NULL DEFAULT '[]', expires_ts INTEGER, suspended INTEGER NOT NULL DEFAULT 0,
    created_ts INTEGER NOT NULL, last_seen_ts INTEGER NOT NULL DEFAULT 0,
    vpn_sub_id TEXT NOT NULL DEFAULT '', reminded INTEGER NOT NULL DEFAULT 0,
    note TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS codes (
    code TEXT PRIMARY KEY, days INTEGER NOT NULL, services TEXT NOT NULL,
    uses_max INTEGER NOT NULL DEFAULT 1, uses INTEGER NOT NULL DEFAULT 0,
    created_ts INTEGER NOT NULL, valid_until INTEGER NOT NULL,
    revoked INTEGER NOT NULL DEFAULT 0, note TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS redemptions (code TEXT NOT NULL, uid INTEGER NOT NULL, ts INTEGER NOT NULL, days INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS matrix_accounts (mxid TEXT PRIMARY KEY, uid INTEGER NOT NULL, created_ts INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL, uid INTEGER, kind TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS activity (day TEXT NOT NULL, uid INTEGER NOT NULL, messages INTEGER NOT NULL DEFAULT 0, app INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(day, uid));
CREATE TABLE IF NOT EXISTS downloads (
    id INTEGER PRIMARY KEY AUTOINCREMENT, uid INTEGER NOT NULL, url TEXT NOT NULL,
    preset TEXT NOT NULL, opts TEXT NOT NULL DEFAULT '{}', origin TEXT NOT NULL DEFAULT 'app',
    chat_id INTEGER, chat_msg_id INTEGER, status TEXT NOT NULL DEFAULT 'queued',
    title TEXT NOT NULL DEFAULT '', percent REAL NOT NULL DEFAULT 0, size INTEGER NOT NULL DEFAULT 0,
    eta INTEGER, files TEXT NOT NULL DEFAULT '[]', error TEXT NOT NULL DEFAULT '',
    lead INTEGER, phase TEXT NOT NULL DEFAULT '', submit_ns INTEGER NOT NULL DEFAULT 0,
    murls TEXT NOT NULL DEFAULT '[]', folder TEXT NOT NULL DEFAULT '', delivery TEXT NOT NULL DEFAULT '',
    created_ts INTEGER NOT NULL, started_ts INTEGER, finished_ts INTEGER, delivered_ts INTEGER);
CREATE INDEX IF NOT EXISTS downloads_uid ON downloads(uid, created_ts);
"""


class Store:
    def __init__(self, path):
        new = not os.path.exists(path)
        old = os.umask(0o077)
        try:
            self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        finally:
            os.umask(old)
        if new:
            os.chmod(path, 0o600)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        self.lock = threading.Lock()

    def q(self, sql, *args):
        with self.lock:
            return self.db.execute(sql, args).fetchall()

    def get(self, key, default=""):
        r = self.q("SELECT value FROM settings WHERE key=?", key)
        return r[0]["value"] if r else default

    def set(self, key, value):
        self.q("INSERT INTO settings(key,value) VALUES(?,?) "
               "ON CONFLICT(key) DO UPDATE SET value=excluded.value", key, str(value))

    # users --------------------------------------------------------------
    def users(self):
        return [dict(r) for r in self.q("SELECT * FROM users ORDER BY added_ts")]

    def allow(self, uid, name=""):
        self.q("INSERT INTO users(id,name,added_ts) VALUES(?,?,?) "
               "ON CONFLICT(id) DO UPDATE SET name=excluded.name", int(uid), name, int(time.time()))

    def deny(self, uid):
        self.q("DELETE FROM users WHERE id=?", int(uid))

    def is_user(self, uid):
        return bool(self.q("SELECT 1 FROM users WHERE id=?", int(uid)))

    # vault --------------------------------------------------------------
    def vault(self):
        return {r["id"]: dict(r) for r in self.q("SELECT * FROM vault")}

    def vault_set(self, vid, login, password, note=""):
        self.q("INSERT INTO vault(id,login,password,note,updated_ts) VALUES(?,?,?,?,?) "
               "ON CONFLICT(id) DO UPDATE SET login=excluded.login, password=excluded.password, "
               "note=excluded.note, updated_ts=excluded.updated_ts",
               vid, login, password, note, int(time.time()))

    def vault_del(self, vid):
        with self.lock:
            return self.db.execute("DELETE FROM vault WHERE id=?", (vid,)).rowcount
