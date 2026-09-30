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
