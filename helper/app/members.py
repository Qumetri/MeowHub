"""Membership: members, access codes, stats, and the Reconciler that enforces
access on the VPN panel (3x-ui) and the Matrix server.

A member is a Telegram user who redeemed an access code (or was granted by the
owner). Status is computed, never stored: suspended (owner's switch) beats
expired (expires_ts passed) beats active. Expiry never deletes a row -- the
services just stop, the accounts stay.
"""
import html
import json
import logging
import math
import os
import re
import secrets
import sqlite3
import threading
import time
from datetime import date, timedelta

log = logging.getLogger("members")

SERVICES = {
    "vpn": {"name_ru": "VPN", "name_en": "VPN",
            "desc_ru": "Личный VPN: одна ссылка-подписка с конфигами для телефона и компьютера",
            "desc_en": "Personal VPN: one subscription link with configs for phone and computer"},
    "matrix": {"name_ru": "Мессенджер", "name_en": "Messenger",
               "desc_ru": "Свой аккаунт в приватном мессенджере Matrix (Element)",
               "desc_en": "Your own account on the private Matrix messenger (Element)"},
    "tools": {"name_ru": "Инструменты бота", "name_en": "Bot tools",
              "desc_ru": "Здоровье сервера, ссылки и загрузка видео с YouTube",
              "desc_en": "Server health, links and YouTube downloads"},
}
DEFAULT_SERVICES = ["vpn", "matrix"]

DAY = 86400
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
SUB_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789"
RATE_MAX, RATE_WINDOW = 5, 3600
MEMBER_PROTOCOLS = {"vless", "vmess", "trojan", "shadowsocks", "hysteria", "mtproto"}
AWG_PROTOCOLS = {"wireguard", "amneziawg"}
MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа",
          "сентября", "октября", "ноября", "декабря"]


def esc(s):
    return html.escape(str(s), quote=False)


def owner_contact():
    """OWNER_CONTACT (a Telegram @handle) or '' when the operator set none."""
    return (os.environ.get("OWNER_CONTACT") or "").strip()


def ask_owner(cap=False):
    """'Напиши @handle' -- or 'Напиши владельцу' when no contact is configured."""
    c = owner_contact()
    return ("Напиши " if cap else "напиши ") + (esc(c) if c else "владельцу")


def norm_code(s):
    """User input -> bare code: uppercase, only A-Z0-9 (dashes/spaces dropped)."""
    return re.sub(r"[^A-Z0-9]", "", str(s or "").upper())


def _services(services):
    """Keep known ids only, deduplicated, in the canonical SERVICES order."""
    want = set(services or [])
    return [k for k in SERVICES if k in want]


def ru_date(ts):
    d = date.fromtimestamp(ts)
    return f"{d.day} {MONTHS[d.month - 1]}"


def awg_limited(ids, by_id):
    """Drop every wireguard/amneziawg inbound after the first (3x-ui rule)."""
    out, seen = [], False
    for i in ids:
        p = str((by_id.get(i) or {}).get("protocol", "")).lower()
        if p in AWG_PROTOCOLS:
            if seen:
                continue
            seen = True
        out.append(i)
    return out


# Inbound labels start with their creation date ("07.10.26 · ⚡ Speed …") so users can
# tell new configs from old ones; on the left because long labels get cut off on the right.
DATE_PREFIX = re.compile(r"^\d{2}\.\d{2}\.\d{2} · ")
DATE_SUFFIX = re.compile(r" · \d{2}\.\d{2}\.\d{2}$")     # old placement, migrated to the prefix


class Members:
    def __init__(self, store):
        self.s = store
        self.changed = threading.Event()
        self.vpn_emails = None            # set by the Reconciler: emails of existing 3x-ui clients
        self._fails = {}                  # uid -> [ts of failed redeems]
        self._fail_lock = threading.Lock()

    # ------------------------------------------------------------- internals --
    def _exec(self, sql, *args):
        with self.s.lock:
            return self.s.db.execute(sql, args).rowcount

    @staticmethod
    def _row(r):
        if r is None:
            return None
        d = dict(r)
        if "services" in d:
            try:
                d["services"] = json.loads(d["services"])
            except ValueError:
                d["services"] = []
        return d

    @staticmethod
    def _prof(frm):
        """Non-empty profile fields of a Telegram `from` dict."""
        frm = frm or {}
        out = {}
        for src, dst in (("username", "username"), ("first_name", "first_name"),
                         ("last_name", "last_name"), ("language_code", "lang")):
            v = frm.get(src)
            if v:
                out[dst] = str(v)
        return out

    @staticmethod
    def _log_db(db, uid, kind, detail=""):
        db.execute("INSERT INTO events(ts,uid,kind,detail) VALUES(?,?,?,?)",
                   (int(time.time()), uid, kind, str(detail)))

    def _apply(self, db, uid, frm, days, services, now):
        """Create or extend a member (lock held, inside the caller's transaction).
        Returns 'new' or 'extended'."""
        prof = self._prof(frm)
        r = db.execute("SELECT * FROM members WHERE id=?", (uid,)).fetchone()
        if r is None:
            cols = ["id", "services", "expires_ts", "created_ts", "last_seen_ts"] + list(prof)
            vals = [uid, json.dumps(_services(services)), now + days * DAY, now, now] + list(prof.values())
            db.execute(f"INSERT INTO members({','.join(cols)}) VALUES({','.join('?' * len(cols))})", vals)
            return "new"
        have = json.loads(r["services"] or "[]")
        exp = max(now, r["expires_ts"] or now) + days * DAY
        sets = ["services=?", "expires_ts=?", "reminded=0"] + [f"{k}=?" for k in prof]
        db.execute(f"UPDATE members SET {','.join(sets)} WHERE id=?",
                   [json.dumps(_services(set(have) | set(services))), exp] + list(prof.values()) + [uid])
        return "extended"

    def _limited(self, uid, now):
        with self._fail_lock:
            fails = [t for t in self._fails.get(uid, []) if now - t < RATE_WINDOW]
            self._fails[uid] = fails
            return len(fails) >= RATE_MAX

    def _fail(self, uid, now):
        with self._fail_lock:
            self._fails.setdefault(uid, []).append(now)

    # --------------------------------------------------------------- members --
    def get(self, uid):
        r = self.s.q("SELECT * FROM members WHERE id=?", int(uid))
        return self._row(r[0]) if r else None

    def status(self, m):
        if m.get("suspended"):
            return "suspended"
        exp = m.get("expires_ts")
        if exp is not None and exp <= time.time():
            return "expired"
        return "active"

    def has(self, uid, service):
        m = self.get(uid)
        return bool(m) and self.status(m) == "active" and service in m["services"]

    def view(self, m):
        now = time.time()
        exp = m["expires_ts"]
        if exp is None:
            left = None
        else:
            left = max(0, math.ceil((exp - now) / DAY))
        email = f"mh-{m['id']}"
        if self.vpn_emails is not None:
            has_client = email in self.vpn_emails
        else:
            has_client = bool(m["vpn_sub_id"])
        return {"id": m["id"], "username": m["username"], "first_name": m["first_name"],
                "last_name": m["last_name"], "lang": m["lang"], "status": self.status(m),
                "expires_ts": exp, "days_left": left, "services": m["services"],
                "created_ts": m["created_ts"], "last_seen_ts": m["last_seen_ts"], "note": m["note"],
                "vpn_email": email, "has_vpn_client": has_client,
                "matrix": [a["mxid"] for a in self.matrix_accounts(m["id"])]}

    def list(self):
        return [self._row(r) for r in self.s.q("SELECT * FROM members ORDER BY created_ts DESC, id DESC")]

    def touch(self, uid, frm=None, kind="message"):
        now = int(time.time())
        prof = self._prof(frm)
        sets = ["last_seen_ts=?"] + [f"{k}=?" for k in prof]
        col = "app" if kind == "app" else "messages"
        with self.s.lock:
            db = self.s.db
            n = db.execute(f"UPDATE members SET {','.join(sets)} WHERE id=?",
                           [now] + list(prof.values()) + [int(uid)]).rowcount
            if not n:
                return
            db.execute(f"INSERT INTO activity(day,uid,{col}) VALUES(?,?,1) "
                       f"ON CONFLICT(day,uid) DO UPDATE SET {col}={col}+1",
                       (time.strftime("%Y-%m-%d"), int(uid)))

    def redeem(self, uid, frm, code):
        uid = int(uid)
        now = int(time.time())
        if self._limited(uid, now):
            return "rate_limited", None
        norm = norm_code(code)
        res, member = None, None
        with self.s.lock:
            db = self.s.db
            db.execute("BEGIN IMMEDIATE")
            try:
                c = db.execute("SELECT * FROM codes WHERE REPLACE(code,'-','')=?", (norm,)).fetchone() if norm else None
                if c is None:
                    res = "invalid"
                elif c["revoked"]:
                    res = "revoked_code"
                elif c["valid_until"] <= now:
                    res = "expired_code"
                elif c["uses"] >= c["uses_max"]:
                    res = "used"
                else:
                    m = db.execute("SELECT suspended FROM members WHERE id=?", (uid,)).fetchone()
                    if m is not None and m["suspended"]:
                        res = "suspended"
                if res is None:
                    svcs = json.loads(c["services"] or "[]")
                    res = self._apply(db, uid, frm, c["days"], svcs, now)
                    db.execute("UPDATE codes SET uses=uses+1 WHERE code=?", (c["code"],))
                    db.execute("INSERT INTO redemptions(code,uid,ts,days) VALUES(?,?,?,?)",
                               (c["code"], uid, now, c["days"]))
                    self._log_db(db, uid, "redeem", f"{c['code']} +{c['days']}d {','.join(_services(svcs))}")
                    member = self._row(db.execute("SELECT * FROM members WHERE id=?", (uid,)).fetchone())
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise
        if member is None:
            if res in ("invalid", "used", "expired_code", "revoked_code"):
                self._fail(uid, now)
            return res, None
        self.changed.set()
        return res, member

    def grant(self, uid, frm=None, days=30, services=None):
        uid = int(uid)
        now = int(time.time())
        svcs = DEFAULT_SERVICES if services is None else services
        with self.s.lock:
            db = self.s.db
            db.execute("BEGIN IMMEDIATE")
            try:
                res = self._apply(db, uid, frm, int(days), svcs, now)
                self._log_db(db, uid, "grant", f"{res} +{int(days)}d {','.join(_services(svcs))}")
                member = self._row(db.execute("SELECT * FROM members WHERE id=?", (uid,)).fetchone())
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise
        self.changed.set()
        return member

    def extend(self, uid, days):
        now = int(time.time())
        with self.s.lock:
            r = self.s.db.execute("SELECT expires_ts FROM members WHERE id=?", (int(uid),)).fetchone()
            if r is None:
                return None
            exp = max(now, r["expires_ts"] or now) + int(days) * DAY
            self.s.db.execute("UPDATE members SET expires_ts=?, reminded=0 WHERE id=?", (exp, int(uid)))
            self._log_db(self.s.db, int(uid), "extend", f"+{int(days)}d")
        self.changed.set()
        return self.get(uid)

    def set_expiry(self, uid, ts):
        ts = None if ts is None else int(ts)
        if self._exec("UPDATE members SET expires_ts=?, reminded=0 WHERE id=?", ts, int(uid)):
            self.log(int(uid), "expire", "none" if ts is None else str(ts))
            self.changed.set()

    def suspend(self, uid):
        if self._exec("UPDATE members SET suspended=1 WHERE id=?", int(uid)):
            self.log(int(uid), "suspend")
            self.changed.set()

    def resume(self, uid):
        if self._exec("UPDATE members SET suspended=0 WHERE id=?", int(uid)):
            self.log(int(uid), "resume")
            self.changed.set()

    def set_services(self, uid, services):
        svcs = _services(services)
        if self._exec("UPDATE members SET services=? WHERE id=?", json.dumps(svcs), int(uid)):
            self.log(int(uid), "services", ",".join(svcs))
            self.changed.set()

    def set_note(self, uid, note):
        if self._exec("UPDATE members SET note=? WHERE id=?", str(note or ""), int(uid)):
            self.changed.set()

    def delete(self, uid):
        # matrix_accounts rows stay on purpose: the accounts stay locked and a
        # returning uid gets them back.
        if self._exec("DELETE FROM members WHERE id=?", int(uid)):
            self.log(int(uid), "delete")
            self.changed.set()

    def set_vpn_sub_id(self, uid, sub_id):
        self._exec("UPDATE members SET vpn_sub_id=? WHERE id=?", str(sub_id), int(uid))

    def set_reminded(self, uid, value):
        """Reminder bookkeeping only -- does not wake the reconciler."""
        self._exec("UPDATE members SET reminded=? WHERE id=?", int(value), int(uid))

    # ----------------------------------------------------------------- codes --
    def new_code(self, days=30, services=None, uses_max=1, valid_days=30, note=""):
        now = int(time.time())
        svcs = json.dumps(_services(DEFAULT_SERVICES if services is None else services))
        for _ in range(20):
            raw = "".join(secrets.choice(CODE_ALPHABET) for _ in range(8))
            code = f"MEOW-{raw[:4]}-{raw[4:]}"
            try:
                self._exec("INSERT INTO codes(code,days,services,uses_max,created_ts,valid_until,note) "
                           "VALUES(?,?,?,?,?,?,?)", code, int(days), svcs, int(uses_max), now,
                           now + int(valid_days) * DAY, str(note or ""))
                return code
            except sqlite3.IntegrityError:
                continue
        raise RuntimeError("could not generate a unique code")

    @staticmethod
    def _code_state(c, now):
        if c["revoked"]:
            return "revoked"
        if c["uses"] >= c["uses_max"]:
            return "used"
        if c["valid_until"] <= now:
            return "expired"
        return "live"

    def codes(self, include_dead=False):
        now = time.time()
        out = []
        for r in self.s.q("SELECT * FROM codes ORDER BY created_ts DESC, rowid DESC"):
            c = self._row(r)
            c["state"] = self._code_state(c, now)
            if include_dead or c["state"] == "live":
                out.append(c)
        return out

    def revoke_code(self, code):
        norm = norm_code(code)
        if not norm:
            return False
        return self._exec("UPDATE codes SET revoked=1 WHERE REPLACE(code,'-','')=?", norm) > 0

    # ---------------------------------------------------------------- matrix --
    def matrix_accounts(self, uid):
        return [{"mxid": r["mxid"], "created_ts": r["created_ts"]} for r in
                self.s.q("SELECT * FROM matrix_accounts WHERE uid=? ORDER BY created_ts", int(uid))]

    def add_matrix(self, uid, mxid):
        self._exec("INSERT OR REPLACE INTO matrix_accounts(mxid,uid,created_ts) VALUES(?,?,?)",
                   mxid, int(uid), int(time.time()))
        self.log(int(uid), "matrix_create", mxid)
        self.changed.set()

    def all_matrix(self):
        return [dict(r) for r in self.s.q("SELECT mxid,uid,created_ts FROM matrix_accounts ORDER BY created_ts")]

    # ----------------------------------------------------------------- stats --
    def log(self, uid, kind, detail=""):
        self._exec("INSERT INTO events(ts,uid,kind,detail) VALUES(?,?,?,?)",
                   int(time.time()), uid, kind, str(detail))

    def events(self, uid=None, limit=50):
        if uid is None:
            rows = self.s.q("SELECT * FROM events ORDER BY id DESC LIMIT ?", int(limit))
        else:
            rows = self.s.q("SELECT * FROM events WHERE uid=? ORDER BY id DESC LIMIT ?", int(uid), int(limit))
        return [dict(r) for r in rows]

    def activity(self, days=30):
        today = date.today()
        first = today - timedelta(days=days - 1)
        have = {r["day"]: r for r in self.s.q(
            "SELECT day, COUNT(*) AS users, SUM(messages) AS messages, SUM(app) AS app "
            "FROM activity WHERE day>=? GROUP BY day", first.isoformat())}
        out = []
        for i in range(days):
            d = (first + timedelta(days=i)).isoformat()
            r = have.get(d)
            out.append({"day": d, "users": r["users"] if r else 0,
                        "messages": r["messages"] if r else 0, "app": r["app"] if r else 0})
        return out

    def counts(self):
        c = {"members": 0, "active": 0, "expired": 0, "suspended": 0, "expiring_7d": 0}
        soon = time.time() + 7 * DAY
        for m in self.list():
            c["members"] += 1
            st = self.status(m)
            c[st] += 1
            if st == "active" and m["expires_ts"] is not None and m["expires_ts"] <= soon:
                c["expiring_7d"] += 1
        return c

    # ------------------------------------------------------ inbound settings --
    def _json_list(self, key):
        raw = self.s.get(key, "")
        if not raw:
            return None
        try:
            v = json.loads(raw)
        except ValueError:
            return None
        return [int(i) for i in v] if isinstance(v, list) else None

    def member_inbounds(self):
        return self._json_list("member_inbounds")

    def set_member_inbounds(self, ids):
        self.s.set("member_inbounds", json.dumps([int(i) for i in ids]))
        self.changed.set()

    def known_inbounds(self):
        return self._json_list("known_inbounds") or []

    def set_known_inbounds(self, ids):
        self.s.set("known_inbounds", json.dumps(sorted({int(i) for i in ids})))


class Reconciler:
    """Makes 3x-ui and Synapse match the members table. Idempotent: it reads
    what exists and only changes what differs, so it is safe to run any time."""

    def __init__(self, members, xui, synapse, send, owner_id):
        self.m = members
        self.xui = xui
        self.syn = synapse
        self.send = send
        self.owner_id = owner_id
        self.last_sync = {"ts": 0, "ok": True, "error": "", "vpn_clients": 0}
        self._applied = {}                # mxid -> locked state we last set
        self._last_err = {}               # uid -> last error text logged as an event
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ loop --
    def run(self):
        while True:
            for fn in (self.sync_all, self.reminders, self.detect_new_inbounds):
                try:
                    fn()
                except Exception:
                    log.exception("reconciler %s failed", fn.__name__)
            self.m.changed.wait(120)
            self.m.changed.clear()

    # ---------------------------------------------------------------- helpers --
    def _has(self, m, service):
        return self.m.status(m) == "active" and service in m["services"]

    @staticmethod
    def _name(m):
        n = f"{m['first_name']} {m['last_name']}".strip()
        return n or (f"@{m['username']}" if m["username"] else str(m["id"]))

    def _default_inbounds(self, inbounds):
        by_id = {i["id"]: i for i in inbounds}
        ids = []
        for i in inbounds:
            proto = str(i.get("protocol", "")).lower()
            ok = proto in MEMBER_PROTOCOLS or proto.startswith("hysteria")
            if i.get("enable") and ok and "🧪" not in str(i.get("remark", "")):
                ids.append(i["id"])
        return awg_limited(ids, by_id)

    def _member_inbounds(self):
        """The configured member inbound set; computes and saves the default on first run."""
        ids = self.m.member_inbounds()
        if ids is None:
            inbounds = self.xui.inbounds()
            ids = self._default_inbounds(inbounds)
            self.m.set_member_inbounds(ids)
            self.m.set_known_inbounds([i["id"] for i in inbounds])
            log.info("member inbounds defaulted to %s", ids)
        return ids

    def _client_record(self, m, client, sub_id):
        return {"email": f"mh-{m['id']}", "subId": sub_id, "enable": self._has(m, "vpn"),
                "expiryTime": (m["expires_ts"] * 1000) if m["expires_ts"] else 0,
                "tgId": m["id"], "comment": f"MeowHub @{m['username'] or m['first_name'] or m['id']}",
                "flow": "xtls-rprx-vision", "limitIp": 0, "totalGB": 0}

    # -------------------------------------------------------------------- vpn --
    def _sync_vpn(self, m, client, inbound_ids):
        """One member against its 3x-ui client (None = does not exist)."""
        if client is None and "vpn" not in m["services"]:
            return
        uid, email = m["id"], f"mh-{m['id']}"
        sub_id = m["vpn_sub_id"]
        if not sub_id:
            sub_id = (client or {}).get("subId") or "".join(secrets.choice(SUB_ALPHABET) for _ in range(16))
            self.m.set_vpn_sub_id(uid, sub_id)
        want = self._client_record(m, client, sub_id)
        if client is None:
            if not inbound_ids:
                raise RuntimeError("no member inbounds configured")
            self.xui.client_add(want, list(inbound_ids))
            return "created"
        diff = {k: v for k, v in want.items() if k in ("subId", "enable", "expiryTime", "tgId", "comment", "flow")
                and client.get(k) != v}
        if diff:
            rec = dict(client)
            rec.update(diff)
            self.xui.client_update(email, rec)
        cur, want_ids = set(client.get("inboundIds") or []), set(inbound_ids)
        if want_ids - cur:
            self.xui.client_attach(email, sorted(want_ids - cur))
        # an empty target set would orphan the client, so never detach down to nothing
        if want_ids and cur - want_ids:
            self.xui.client_detach(email, sorted(cur - want_ids))
        return "updated" if diff or want_ids != cur else None

    def _vpn_error(self, uid, e):
        msg = f"{type(e).__name__}: {e}"
        log.warning("vpn sync for %s failed: %s", uid, msg)
        if self._last_err.get(uid) != msg:       # one event per distinct error, not one per pass
            self._last_err[uid] = msg
            self.m.log(uid, "vpn_sync", msg)
        return f"vpn {uid}: {msg}"

    def _sync_matrix(self, accounts, members, errors):
        for a in accounts:
            m = members.get(a["uid"])
            locked = not (m and self._has(m, "matrix"))
            if self._applied.get(a["mxid"]) == locked:
                continue
            try:
                self.syn.set_locked(a["mxid"], locked)
                self._applied[a["mxid"]] = locked
            except Exception as e:
                log.warning("matrix lock %s failed: %s", a["mxid"], e)
                errors.append(f"matrix {a['mxid']}: {e}")

    # ------------------------------------------------------------------- sync --
    def sync_all(self):
        with self._lock:
            errors, n_clients = [], self.last_sync.get("vpn_clients", 0)
            members = {m["id"]: m for m in self.m.list()}
            if self.xui is not None:
                try:
                    clients = {c.get("email"): c for c in self.xui.clients()}
                    ids = self._member_inbounds()
                except Exception as e:
                    log.warning("xui read failed: %s", e)
                    errors.append(f"xui: {e}")
                else:
                    for uid, m in members.items():
                        try:
                            if self._sync_vpn(m, clients.get(f"mh-{uid}"), ids) == "created":
                                clients[f"mh-{uid}"] = {"email": f"mh-{uid}"}
                            self._last_err.pop(uid, None)
                        except Exception as e:
                            errors.append(self._vpn_error(uid, e))
                    for email in list(clients):
                        mo = re.fullmatch(r"mh-(\d+)", str(email))
                        if mo and int(mo.group(1)) not in members:
                            try:
                                self.xui.client_delete(email)
                                del clients[email]
                            except Exception as e:
                                errors.append(self._vpn_error(int(mo.group(1)), e))
                    mine = {e for e in clients if re.fullmatch(r"mh-\d+", str(e))}
                    self.m.vpn_emails = mine
                    n_clients = len(mine)
            if self.syn is not None:
                self._sync_matrix(self.m.all_matrix(), members, errors)
            self.last_sync = {"ts": int(time.time()), "ok": not errors,
                              "error": "; ".join(errors[:5]), "vpn_clients": n_clients}

    def sync_member(self, uid):
        with self._lock:
            uid = int(uid)
            m = self.m.get(uid)
            email = f"mh-{uid}"
            if self.xui is not None:
                client = self.xui.client_get(email)
                if m is None:
                    if client is not None:
                        self.xui.client_delete(email)
                else:
                    if self._sync_vpn(m, client, self._member_inbounds()) == "created" \
                            and self.m.vpn_emails is not None:
                        self.m.vpn_emails = set(self.m.vpn_emails) | {email}
                    self._last_err.pop(uid, None)
            if self.syn is not None:
                errors = []
                self._sync_matrix([a for a in self.m.all_matrix() if a["uid"] == uid],
                                  {uid: m} if m else {}, errors)
                if errors:
                    raise RuntimeError("; ".join(errors))

    # -------------------------------------------------------------- reminders --
    def _just_started(self, uid, now, window=2 * 3600):
        """True when the newest redeem/grant/extend is within `window` and no manual
        expiry change came after it (the owner moving the date should still remind)."""
        for e in self.m.events(uid, 20):                 # newest first
            if e["kind"] == "expire" and e["detail"] != "reminder":
                return False
            if e["kind"] in ("redeem", "grant", "extend"):
                return now - e["ts"] < window
        return False

    def reminders(self):
        now = time.time()
        owner = self.owner_id()
        for m in self.m.list():
            exp = m["expires_ts"]
            if exp is None or m["suspended"]:
                continue
            uid, left, flag = m["id"], exp - now, m["reminded"]
            text = None
            if left <= 0:
                if flag == -1:
                    continue
                text = ("⏸ Подписка MeowHub закончилась. Сервисы приостановлены, аккаунты сохранены. "
                        f"Пришли новый код или {ask_owner()}.")
                self.m.set_reminded(uid, -1)
                self.m.log(uid, "expire", "reminder")
                self.send(uid, text)
                text = None
                if owner:
                    self.send(owner, f"⌛ {esc(self._name(m))} — подписка истекла",
                              {"inline_keyboard": [[
                                  {"text": "➕ 30 дней", "callback_data": f"m:ext:{uid}:30"},
                                  {"text": "👤 Открыть", "callback_data": f"m:open:{uid}"}]]})
            elif left <= DAY:
                if flag in (0, 3):
                    # A membership started or extended moments ago with under a day left
                    # (a 1-day code) shouldn't greet the user with "expires tomorrow".
                    if not self._just_started(uid, now):
                        text = (f"⏳ Подписка MeowHub закончится через 24 часа — {ru_date(exp)} "
                                f"в {time.strftime('%H:%M', time.localtime(exp))}. "
                                "Чтобы продлить, пришли новый код.")
                    self.m.set_reminded(uid, 1)
            elif left <= 3 * DAY:
                if flag == 0:
                    text = (f"⏳ Подписка MeowHub закончится через 3 дня (до {ru_date(exp)}). "
                            "Чтобы продлить, пришли новый код.")
                    self.m.set_reminded(uid, 3)
            if text:
                self.send(uid, text)

    # ---------------------------------------------------------------- inbounds --
    def detect_new_inbounds(self):
        if self.xui is None:
            return
        with self._lock:
            inbounds = self.xui.inbounds()
            if self.m.member_inbounds() is None:
                self._member_inbounds()        # first run: defaults + known, nothing to announce
                return
            known = set(self.m.known_inbounds())
            owner = self.owner_id()
            if not owner:
                return
            for i in inbounds:
                if i["id"] in known:
                    continue
                # Every inbound's label starts with its creation date (see DATE_PREFIX).
                if not DATE_PREFIX.search(str(i.get("remark", ""))):
                    dated = f"{time.strftime('%d.%m.%y')} · {i.get('remark', '')}"
                    try:
                        self.xui.inbound_set_remark(i["id"], dated)
                        i["remark"] = dated
                    except Exception as e:
                        log.warning("dating inbound %s failed: %s", i["id"], e)
                self.send(owner,
                          f"🆕 Новый инбаунд в 3x-ui: <b>{esc(i.get('remark', ''))}</b> "
                          f"({esc(i.get('protocol', ''))}, :{esc(i.get('port', ''))}). Выдать его участникам?",
                          {"inline_keyboard": [[
                              {"text": "✅ Выдать", "callback_data": f"inb:add:{i['id']}"},
                              {"text": "Пропустить", "callback_data": f"inb:skip:{i['id']}"}]]})
                known.add(i["id"])
                self.m.set_known_inbounds(known)
