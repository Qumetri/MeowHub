"""HTTP server behind the MeowHub Telegram Mini App and the owner's hub admin
page. Stdlib only. It serves the built frontend and a small JSON API.

Two ways in (see authenticate()):
  * X-Admin-Key == BOT_ADMIN_KEY  -> owner, mode "browser". Caddy injects the
    header on the basic_auth admin route only and strips it on the public one.
  * X-Tg-Init-Data                -> Telegram Mini App initData, HMAC-checked
    against the bot token. role = owner | member | stranger, mode "tg".

Caddy strips the secret path prefix, so paths here are `/`, `/api/...`,
`/assets/...`, `/go/...`. Writes go through Members (which wakes the
Reconciler); the request thread only talks to 3x-ui for the two cases the
contract allows (/api/vpn pending client, /api/admin/sync).
"""
import base64
import hashlib
import hmac
import html
import json
import logging
import mimetypes
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import members as mem
import tokens
from synapse import SynapseError
from tg import Bot, TelegramError
from xui import XUIError

log = logging.getLogger("webapp")

MAX_BODY = 64 * 1024
DRAIN_MAX = 1024 * 1024
TOUCH_EVERY = 600                    # seconds between "app" activity bumps per uid
AVATAR_TTL = 24 * 3600
GETME_TTL = 600
LOCKED_TTL = 60
XUI_CHECK_TTL = 60
RESTART_DELAY = 1.5
SYNC_TIMEOUT = 20

mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/javascript", ".mjs")
mimetypes.add_type("image/svg+xml", ".svg")
mimetypes.add_type("font/woff2", ".woff2")
mimetypes.add_type("application/json", ".json")

# id, title, platforms, deep link built from the (raw) subscription URL
VPN_APPS = [
    ("happ", "Happ", "iOS · Android · macOS · Windows", lambda s: "happ://add/" + s),
    ("v2raytun", "v2RayTun", "iOS · Android", lambda s: "v2raytun://import/" + s),
    ("hiddify", "Hiddify", "Android · iOS · Windows · macOS · Linux", lambda s: "hiddify://import/" + s),
    ("streisand", "Streisand", "iOS · macOS", lambda s: "streisand://import/" + s),
    ("v2rayng", "v2rayNG", "Android",
     lambda s: "v2rayng://install-config?url=" + urllib.parse.quote(s, safe="")),
]
APPS_BY_ID = {a[0]: a for a in VPN_APPS}

RESULT_MESSAGES = {
    "new": "Доступ открыт.",
    "extended": "Подписка продлена.",
    "invalid": "Код не найден. Проверь, что он введён без ошибок.",
    "used": "Этот код уже использован.",
    "expired_code": "Срок действия кода истёк.",
    "revoked_code": "Этот код отозван.",
    "suspended": "Доступ приостановлен владельцем — по коду его не вернуть.",
    "rate_limited": "Слишком много неверных попыток. Попробуй через час.",
}
MESSAGES = {
    "unauthorized": "Нужна авторизация.",
    "forbidden": "Недостаточно прав.",
    "not_found": "Не найдено.",
    "bad_request": "Некорректный запрос.",
    "internal": "Внутренняя ошибка.",
    "bad_token": "Токен не подошёл.",
    "same_bot": "Это токен того же бота, что и служебный: нужен отдельный бот.",
    "crypto_apply_failed": "Не удалось передать токен крипто-трекеру.",
    "no_override": "Токен не менялся на странице — сбрасывать нечего.",
    "no_fallback": "Без токена бот не запустится: в .env его нет.",
}
CRYPTO_NOTE = ("n8n хранит свою копию токена крипто-бота: после смены обнови токен "
               "в его Telegram-credential в n8n.")


def esc(s):
    return html.escape(str(s))


def env(name, default=""):
    v = os.environ.get(name)
    return default if v is None or not v.strip() else v.strip()


class ApiError(Exception):
    def __init__(self, status, code, message=None):
        super().__init__(code)
        self.status, self.code = status, code
        self.message = message or MESSAGES.get(code, code)


class Resp:
    def __init__(self, body, ctype, status=200, headers=None):
        self.body, self.ctype, self.status, self.headers = body, ctype, status, headers or {}


def jr(obj, status=200, headers=None):
    return Resp(json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8",
                status, headers)


class Ctx:
    def __init__(self, uid, role, mode, user, via="browser"):
        self.uid, self.role, self.mode, self.user, self.via = uid, role, mode, user, via


# --------------------------------------------------------------- initData --
class InitDataError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def verify_init_data(raw, token, max_age, now=None):
    """Validate Telegram Mini App initData; returns the `user` dict."""
    if not raw or not token:
        raise InitDataError("bad_init_data")
    pairs = urllib.parse.parse_qsl(raw, keep_blank_values=True)
    data = dict(pairs)
    if len(data) != len(pairs):
        raise InitDataError("bad_init_data")
    got = data.pop("hash", "")
    if not got:
        raise InitDataError("bad_init_data")
    check = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    calc = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calc.encode(), got.lower().encode()):
        raise InitDataError("bad_init_data")
    try:
        auth_date = int(data.get("auth_date", ""))
    except ValueError:
        raise InitDataError("bad_init_data") from None
    age = (now if now is not None else time.time()) - auth_date
    if age > max_age or age < -300:
        raise InitDataError("init_data_expired")
    try:
        user = json.loads(data.get("user", ""))
        uid = user["id"]
    except (ValueError, KeyError, TypeError):
        raise InitDataError("bad_init_data") from None
    if not isinstance(user, dict) or isinstance(uid, bool) or not isinstance(uid, int):
        raise InitDataError("bad_init_data")
    return user


# ---------------------------------------------------------------- helpers --
def name_of(m):
    n = f"{m.get('first_name', '')} {m.get('last_name', '')}".strip()
    return n or (f"@{m['username']}" if m.get("username") else str(m["id"]))


def svc_names(services):
    return " + ".join(mem.SERVICES[s]["name_ru"] for s in services if s in mem.SERVICES) or "—"


def as_int(v, lo, hi, field):
    if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
        raise ApiError(400, "bad_request", f"Поле {field}: ожидается число {lo}..{hi}.")
    return v


def as_services(v, field="services"):
    if not isinstance(v, list) or not all(isinstance(s, str) and s in mem.SERVICES for s in v):
        raise ApiError(400, "bad_request", f"Поле {field}: неизвестный сервис.")
    return v


def run_timeout(fn, timeout=SYNC_TIMEOUT):
    """Run fn in a worker thread and wait up to `timeout` s; it keeps running past that."""
    box = {}

    def work():
        try:
            box["v"] = fn()
        except BaseException as e:           # noqa: BLE001 - re-raised in the caller
            box["e"] = e

    t = threading.Thread(target=work, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        raise ApiError(504, "timeout", "Операция идёт слишком долго, попробуй позже.")
    if "e" in box:
        raise box["e"]
    return box.get("v")


def traffic_of(rec):
    if not isinstance(rec, dict):
        return None
    t = rec.get("traffic") if isinstance(rec.get("traffic"), dict) else rec
    if "up" not in t and "down" not in t:
        return None
    return {"up": int(t.get("up") or 0), "down": int(t.get("down") or 0)}


def sub_base():
    return env("VPN_SUB_BASE", f"https://{env('BASE_DOMAIN')}:2096/sub/")


def schedule_restart():
    """Exit shortly, after the HTTP response went out; compose `restart: always`
    brings the container back with the new token."""
    threading.Timer(RESTART_DELAY, os._exit, (0,)).start()


def crypto_apply(token):
    """Hand the crypto tracker its bot-token override ("" clears it). True on 2xx."""
    url = env("CRYPTO_URL", "http://crypto:9102/api/settings")
    if urllib.parse.urlsplit(url).path in ("", "/"):
        url = url.rstrip("/") + "/api/settings"
    req = urllib.request.Request(url, data=json.dumps({"tg_token_override": token}).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return 200 <= r.status < 300
    except (urllib.error.URLError, OSError, ValueError):     # HTTPError is a URLError
        return False


def plural_inbounds(n):
    if n % 10 == 1 and n % 100 != 11:
        return f"{n} инбаунд"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return f"{n} инбаунда"
    return f"{n} инбаундов"


# ------------------------------------------------------- VPN link grouping --
# The panel builds share links with the host of the request it answered, so links the
# bot fetches say host.docker.internal / localhost: swap those for the public domain.
BAD_HOSTS = {"host.docker.internal", "localhost", "127.0.0.1", "0.0.0.0", "::1", "[::1]"}
AUTHORITY_RE = re.compile(r"^([A-Za-z][A-Za-z0-9+.-]*://)([^@/?#]*@)?(\[[^\]]*\]|[^:/?#]*)")
UDP_SCHEMES = ("hysteria2", "hy2", "hysteria")
TG_PREFIXES = ("tg://proxy", "https://t.me/proxy")
LINK_GROUPS = [
    ("main", "VLESS и Shadowsocks", ["Happ", "v2RayTun", "v2rayNG", "Hiddify"],
     "Скопируй → в приложении «+» → «Импорт из буфера».", "copy"),
    ("new", "🧪 Новые протоколы (тест)", ["Happ", "v2RayTun"],
     "Нужна последняя версия Happ или v2RayTun. Скопируй → «+» → «Из буфера».", "copy"),
    ("udp", "Hysteria2 (UDP)", ["Happ", "Hiddify", "v2RayTun"],
     "Если обычные не работают. Скопируй → «+» → «Из буфера».", "copy"),
    ("tg", "Прокси для Telegram", ["Telegram"],
     "Нажми — Telegram сам предложит включить.", "telegram"),
]


def _query_of(url):
    return url.split("#", 1)[0].partition("?")[2]


def _fragment_of(url):
    return urllib.parse.unquote(url.split("#", 1)[1]) if "#" in url else ""


def link_group(url):
    """Which app family a share link needs: 'tg' | 'udp' | 'new' | 'main'."""
    low = url.lower()
    if low.startswith(TG_PREFIXES):
        return "tg"
    scheme = low.split("://", 1)[0]
    if scheme in UDP_SCHEMES:
        return "udp"
    if scheme == "vless":
        q = urllib.parse.parse_qs(_query_of(url), keep_blank_values=True)
        enc = (q.get("encryption") or ["none"])[0].strip().lower()
        if enc not in ("", "none") or "fm" in q or "🧪" in _fragment_of(url):
            return "new"
    return "main"


def _fix_vmess(url, domain):
    """vmess:// carries its host inside a base64 JSON blob (`add`)."""
    body, sep, frag = url[len("vmess://"):].partition("#")
    try:
        raw = body + "=" * (-len(body) % 4)
        obj = json.loads(base64.b64decode(raw, validate=False).decode())
        if str(obj.get("add", "")).lower() not in BAD_HOSTS:
            return url
        obj["add"] = domain
        enc = base64.b64encode(json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode()).decode()
    except (ValueError, TypeError, AttributeError):
        return url
    return "vmess://" + enc + sep + frag


def fix_host(url, domain):
    """Replace a local host (authority part) with the public `domain`."""
    if not domain:
        return url
    if url.lower().startswith("vmess://"):
        return _fix_vmess(url, domain)
    m = AUTHORITY_RE.match(url)
    if m and m.group(3).lower() in BAD_HOSTS:
        return url[:m.start(3)] + domain + url[m.end(3):]
    return url


def tg_proxy_url(url, domain):
    """tg://proxy?... or https://t.me/proxy?... -> https://t.me/proxy?... with a
    public `server=`; the other parameters are kept byte for byte."""
    parts = []
    for kv in _query_of(url).split("&"):
        k, eq, v = kv.partition("=")
        if k == "server" and domain and urllib.parse.unquote(v).lower() in BAD_HOSTS:
            v = domain
        parts.append(k + eq + v)
    return "https://t.me/proxy?" + "&".join(p for p in parts if p)


def link_name(url, email, i):
    name = _fragment_of(url).strip()
    if email and name.endswith("-" + email):
        name = name[:-len(email) - 1].strip()
    return name or ("MTProto-прокси" if link_group(url) == "tg" else f"Конфиг {i}")


def build_links(urls, email, domain):
    """-> (links, groups) for /api/vpn: host-fixed, named, grouped by app family."""
    links, by_group = [], {}
    for i, url in enumerate(urls or [], 1):
        if not isinstance(url, str) or not url.strip():
            continue
        url = url.strip()
        g = link_group(url)
        url = tg_proxy_url(url, domain) if g == "tg" else fix_host(url, domain)
        item = {"name": link_name(url, email, i), "url": url}
        links.append(item)
        by_group.setdefault(g, []).append(item)
    groups = [{"id": gid, "title": title, "apps": list(apps), "hint": hint,
               "links": [dict(l, action=action) for l in by_group[gid]]}
              for gid, title, apps, hint, action in LINK_GROUPS if by_group.get(gid)]
    return links, groups


# -------------------------------------------------------------------- app --
class App:
    def __init__(self, helper):
        self.h = helper
        self._touched = {}
        self._lock = threading.Lock()
        self._locked = {}                   # mxid -> (ts, bool|None)
        self._getme = {}                    # bot id -> (ts, dict)
        self._alocks = {}
        self._create_lock = threading.Lock()
        self._integ_lock = threading.Lock()
        self._xui_chk = None                # (key, ts, result) of the last 3x-ui token check

    # ------------------------------------------------------------- auth --
    def role_of(self, uid, via="helper"):
        """owner | member | stranger. Through the member bot even the owner is
        just a member (or a stranger): that bot shows the member experience."""
        if via != "member" and uid is not None and uid == self.h.owner_id():
            return "owner"
        return "member" if uid is not None and self.h.members.get(uid) else "stranger"

    def authenticate(self, headers):
        key = env("BOT_ADMIN_KEY")
        sent = headers.get("X-Admin-Key", "")
        if key and sent and hmac.compare_digest(sent.encode(), key.encode()):
            uid = self.h.owner_id()
            m = self.h.members.get(uid) if uid is not None else None
            user = ({"id": m["id"], "first_name": m["first_name"], "last_name": m["last_name"],
                     "username": m["username"], "language_code": m["lang"]} if m else
                    {"id": uid, "first_name": "Владелец", "last_name": "", "username": "",
                     "language_code": "ru"})
            return Ctx(uid, "owner", "browser", user, "browser")
        raw = headers.get("X-Tg-Init-Data", "")
        if not raw:
            raise ApiError(401, "unauthorized")
        cands = [(via, t) for via, t in self.h.bot_tokens().items() if t]
        if not cands:
            raise ApiError(503, "bot_not_ready", "Бот ещё запускается, попробуй через минуту.")
        try:
            max_age = int(env("WEBAPP_INITDATA_MAX_AGE", "86400"))
        except ValueError:
            max_age = 86400
        user = via = None
        for via, tok in cands:                  # valid if the HMAC matches either bot's token
            try:
                user = verify_init_data(raw, tok, max_age)
                break
            except InitDataError as e:
                if e.code != "bad_init_data":   # right token, but expired
                    raise ApiError(401, e.code, "Сессия недействительна, открой приложение заново.") from None
        if user is None:
            raise ApiError(401, "bad_init_data", "Сессия недействительна, открой приложение заново.")
        uid = user["id"]
        ctx = Ctx(uid, self.role_of(uid, via), "tg", user, via)
        now = time.time()
        with self._lock:
            due = now - self._touched.get(uid, 0) >= TOUCH_EVERY
            if due:
                self._touched[uid] = now
        if due:
            self.h.members.touch(uid, user, "app")
        return ctx

    def need_owner(self, ctx):
        if ctx.role != "owner":
            raise ApiError(403, "forbidden")

    def need_service(self, ctx, svc):
        """Member endpoints: the caller's own membership only (owner included)."""
        if ctx.uid is None or not self.h.members.has(ctx.uid, svc):
            raise ApiError(403, "no_access", "Этот сервис недоступен: нет активной подписки.")
        return ctx.uid

    # ---------------------------------------------------------- notifying --
    def tell(self, uid, text):
        if uid == self.h.owner_id():
            return
        try:
            self.h.notify(uid, text)
        except Exception:
            log.warning("notify %s failed", uid)

    def tell_owner(self, text):
        try:
            self.h.notify_owner(text)
        except Exception:
            log.warning("notify_owner failed")

    # ------------------------------------------------------------- matrix --
    def locked_of(self, mxid):
        """Synapse `locked` flag, cached; None when it cannot be read."""
        syn = self.h.syn
        if syn is None:
            return None
        now = time.time()
        with self._lock:
            hit = self._locked.get(mxid)
        if hit and now - hit[0] < LOCKED_TTL:
            return hit[1]
        try:
            u = syn.user(mxid)
            val = bool(u.get("locked")) if u else None
        except Exception as e:
            log.warning("synapse user lookup failed: %s", type(e).__name__)
            val = None
        with self._lock:
            self._locked[mxid] = (now, val)
        return val

    def accounts(self, uid):
        return [{"mxid": a["mxid"], "created_ts": a["created_ts"], "locked": self.locked_of(a["mxid"])}
                for a in self.h.members.matrix_accounts(uid)]

    @staticmethod
    def matrix_max():
        try:
            return max(0, int(env("MATRIX_MAX_PER_MEMBER", "2")))
        except ValueError:
            return 2

    # ------------------------------------------------------------ avatars --
    def _known_bots(self):
        """Every bot token we talk to: helper, member (when running), crypto (page > env)."""
        toks = self.h.bot_tokens()
        out = [t for t in (toks.get("helper"), toks.get("member")) if t]
        crypto = tokens.resolve(self.h.store, "crypto")[0]
        if crypto and crypto not in out:
            out.append(crypto)
        return out

    def _bot_obj(self, token):
        """The live tg.Bot for `token` when it is one of ours, else a fresh one."""
        for own in (self.h.bot, self.h.member_bot):
            if own is not None and own.token == token:
                return own
        return Bot(token)

    def bot_for(self, bot_id):
        """tg.Bot for one of our tokens, by numeric id."""
        for tok in self._known_bots():
            if tok.split(":", 1)[0] == str(bot_id):
                return self._bot_obj(tok)
        return None

    @staticmethod
    def _me_val(token, me):
        return {"id": tokens.bot_id(token) or int(me.get("id") or 0), "name": me.get("first_name", ""),
                "username": me.get("username", ""), "ok": True, "error": ""}

    def get_me(self, token):
        bid = token.split(":", 1)[0]
        now = time.time()
        with self._lock:
            hit = self._getme.get(bid)
        if hit and now - hit[0] < GETME_TTL:
            return hit[1]
        try:
            me = self._bot_obj(token).call("getMe", _timeout=10)
            val = self._me_val(token, me)
        except TelegramError as e:
            val = {"id": int(bid) if bid.isdigit() else 0, "name": "", "username": "",
                   "ok": False, "error": tokens.scrub(str(e), token)[:200]}
        with self._lock:
            self._getme[bid] = (now, val)
        return val

    def avatar(self, key, bot, user_id):
        """JPEG bytes of a profile photo, or None when there is none. Cached 24 h
        on disk, 'no photo' too. Raises ApiError(502) when Telegram fails and
        nothing cached exists."""
        d = env("HELPER_AVATAR_DIR", "/data/avatars")
        path, none = os.path.join(d, key + ".jpg"), os.path.join(d, key + ".none")

        def fresh(p):
            try:
                return time.time() - os.path.getmtime(p) < AVATAR_TTL
            except OSError:
                return False

        def read(p):
            try:
                with open(p, "rb") as f:
                    return f.read()
            except OSError:
                return None

        def cached():
            if fresh(path):
                data = read(path)
                if data:
                    return True, data
            if fresh(none):
                return True, None
            return False, None

        hit, data = cached()
        if hit:
            return data
        with self._lock:
            lk = self._alocks.setdefault(key, threading.Lock())
        with lk:
            hit, data = cached()
            if hit:
                return data
            try:
                photos = bot.call("getUserProfilePhotos", _timeout=15, user_id=user_id, limit=1)
                sets = photos.get("photos") or []
                data = None
                if sets and sets[0]:
                    best = min(sets[0], key=lambda s: abs(max(s.get("width", 0), s.get("height", 0)) - 320))
                    f = bot.call("getFile", _timeout=15, file_id=best["file_id"])
                    data = bot.file_bytes(f["file_path"])
            except (TelegramError, KeyError, TypeError, AttributeError) as e:
                log.warning("avatar %s: %s", key, type(e).__name__)
                stale = read(path)
                if stale:
                    return stale
                raise ApiError(502, "telegram_error", "Не удалось получить аватар.") from None
            try:
                os.makedirs(d, mode=0o700, exist_ok=True)
                target, other = (path, none) if data else (none, path)
                tmp = target + ".tmp"
                fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, "wb") as f:
                    f.write(data or b"")
                os.replace(tmp, target)
                if os.path.exists(other):
                    os.remove(other)
            except OSError as e:
                log.warning("avatar cache write failed: %s", e.strerror)
            return data

    def member_username(self):
        return (self.h.member_bot_username or "") if self.h.member_bot is not None else ""

    # ---------------------------------------------------------- endpoints --
    def api_me(self, req, ctx):
        m = self.h.members.get(ctx.uid) if ctx.uid is not None else None
        lang = str(ctx.user.get("language_code") or (m or {}).get("lang") or "")
        loc = "ru" if lang.lower()[:2] in ("ru", "uk", "be", "kk") else "en"
        user = {k: ctx.user.get(k, "") for k in ("id", "first_name", "last_name", "username", "language_code")}
        return jr({
            "role": ctx.role, "mode": ctx.mode, "user": user,
            "member": self.h.members.view(m) if m else None,
            "contact": env("OWNER_CONTACT"),
            "services": [{"id": k, "name": v["name_" + loc], "description": v["desc_" + loc]}
                         for k, v in mem.SERVICES.items()],
            "bot_username": self.h.bot_username or "",
            "via": ctx.via, "member_bot_username": self.member_username(),
            "can_preview": ctx.role == "owner" and ctx.via in ("helper", "browser")})

    def api_redeem(self, req, ctx):
        if ctx.mode != "tg":
            raise ApiError(403, "tg_only", "Код активируется в Telegram.")
        code = req.json().get("code")
        if not isinstance(code, str) or not code.strip() or len(code) > 64:
            raise ApiError(400, "bad_request", "Введи код доступа.")
        res, row = self.h.members.redeem(ctx.uid, ctx.user, code)
        if row is not None and res in ("new", "extended"):
            self._announce_redeem(ctx, row, res)
        cur = self.h.members.get(ctx.uid)
        return jr({"result": res, "message": RESULT_MESSAGES.get(res, ""),
                   "member": self.h.members.view(cur) if cur else None})

    def _announce_redeem(self, ctx, row, res):
        if ctx.uid == self.h.owner_id():
            return
        r = self.h.store.q("SELECT code, days FROM redemptions WHERE uid=? ORDER BY ts DESC, rowid DESC LIMIT 1",
                           ctx.uid)
        code, days = (r[0]["code"], r[0]["days"]) if r else ("?", 0)
        c = self.h.store.q("SELECT services FROM codes WHERE code=?", code)
        try:
            svcs = json.loads(c[0]["services"]) if c else row["services"]
        except ValueError:
            svcs = row["services"]
        who = f"<b>{esc(name_of(row))}</b> ("
        who += f"@{esc(row['username'])}, " if row["username"] else ""
        who += f"<code>{ctx.uid}</code>)"
        verb = "активировал(а) код" if res == "new" else "продлил(а) подписку кодом"
        self.tell_owner(f"🎉 {who} {verb} <code>{esc(code)}</code>: +{days} д, {esc(svc_names(svcs))}")

    def api_vpn(self, req, ctx):
        uid = self.need_service(ctx, "vpn")
        xui = self.h.xui
        if xui is None:
            raise ApiError(503, "vpn_unconfigured", "VPN пока не настроен.")
        email = f"mh-{uid}"
        client = xui.client_get(email)
        m = self.h.members.get(uid)
        if client is None or not m["vpn_sub_id"]:
            rec = self.h.rec
            if rec is not None:
                run_timeout(lambda: rec.sync_member(uid))
                m = self.h.members.get(uid)
                client = xui.client_get(email)
            if client is None or not (m["vpn_sub_id"] or client.get("subId")):
                raise ApiError(409, "pending", "Конфиг ещё создаётся, повтори через несколько секунд.")
        sub = sub_base() + (m["vpn_sub_id"] or client.get("subId"))
        links, groups = build_links(xui.client_links(email), email, env("BASE_DOMAIN"))
        tr = traffic_of(client)
        if tr is None:
            tr = next((traffic_of(c) for c in xui.clients() if c.get("email") == email), None)
        try:
            online = email in set(xui.onlines())
        except Exception:
            online = False
        apps = [{"id": a[0], "name": a[1], "platforms": a[2],
                 "go_url": f"./go/{a[0]}?u={urllib.parse.quote(sub, safe='')}"} for a in VPN_APPS]
        return jr({"sub_url": sub, "links": links, "groups": groups,
                   "traffic": tr or {"up": 0, "down": 0}, "online": online, "apps": apps})

    def api_matrix(self, req, ctx):
        uid = self.need_service(ctx, "matrix")
        client = "https://matrix." + env("BASE_DOMAIN")
        accts = self.accounts(uid)
        mx = self.matrix_max()
        syn = self.h.syn
        return jr({"server_name": syn.server_name if syn else env("MATRIX_SERVER_NAME", env("BASE_DOMAIN")),
                   "client_url": client, "element_url": env("ELEMENT_URL", client),
                   "accounts": accts, "max": mx, "can_create": syn is not None and len(accts) < mx})

    def api_matrix_create(self, req, ctx):
        uid = self.need_service(ctx, "matrix")
        body = req.json()
        syn = self.h.syn
        if syn is None:
            raise ApiError(503, "matrix_unconfigured", "Мессенджер пока не настроен.")
        username, password, display = body.get("username"), body.get("password"), body.get("displayname")
        with self._create_lock:
            if len(self.h.members.matrix_accounts(uid)) >= self.matrix_max():
                raise ApiError(409, "limit", "Достигнут лимит аккаунтов.")
            local = username.strip().lower() if isinstance(username, str) else ""
            if not syn.valid_localpart(local):
                raise ApiError(400, "bad_username",
                               "Имя: 3–24 символа, строчные латинские буквы, цифры и . _ = -")
            if not isinstance(password, str) or not 10 <= len(password) <= 128:
                raise ApiError(400, "weak_password", "Пароль должен быть от 10 до 128 символов.")
            if not isinstance(display, str) or not display.strip():
                display = (self.h.members.get(uid) or {}).get("first_name", "")
            try:
                mxid = syn.create(local, password, display.strip()[:64])
            except SynapseError as e:
                if e.code == "M_USER_IN_USE":
                    raise ApiError(409, "taken", "Это имя уже занято.") from None
                if e.code == "M_INVALID_USERNAME":
                    raise ApiError(400, "bad_username", "Это имя недопустимо.") from None
                log.warning("matrix create failed: %s %s", type(e).__name__, e.code)
                raise ApiError(502, "matrix_error", "Мессенджер не отвечает, попробуй позже.") from None
            self.h.members.add_matrix(uid, mxid)     # also writes the matrix_create event
        m = self.h.members.get(uid) or {"id": uid, "first_name": "", "last_name": "", "username": ""}
        self.tell_owner(f"💬 {esc(name_of(m))} создал(а) аккаунт <code>{esc(mxid)}</code>")
        return jr({"mxid": mxid})

    def api_matrix_password(self, req, ctx):
        uid = self.need_service(ctx, "matrix")
        body = req.json()
        syn = self.h.syn
        if syn is None:
            raise ApiError(503, "matrix_unconfigured", "Мессенджер пока не настроен.")
        mxid, password = body.get("mxid"), body.get("password")
        if not isinstance(mxid, str) or mxid not in {a["mxid"] for a in self.h.members.matrix_accounts(uid)}:
            raise ApiError(403, "not_yours", "Это не твой аккаунт.")
        if not isinstance(password, str) or not 10 <= len(password) <= 128:
            raise ApiError(400, "weak_password", "Пароль должен быть от 10 до 128 символов.")
        try:
            syn.reset_password(mxid, password)
        except SynapseError as e:
            log.warning("matrix reset failed: %s %s", type(e).__name__, e.code)
            raise ApiError(502, "matrix_error", "Мессенджер не отвечает, попробуй позже.") from None
        return jr({"ok": True})

    def api_avatar(self, req, ctx, uid):
        uid = int(uid)
        if ctx.role != "owner" and uid != ctx.uid:
            raise ApiError(403, "forbidden")
        # members talk to the member bot (when there is one); a profile photo is only
        # reachable for users the asking bot has met.
        bot = self.h.member_bot if self.h.member_bot is not None and uid != self.h.owner_id() else self.h.bot
        return self._avatar_resp(str(uid), bot, uid)

    def api_bot_avatar(self, req, ctx, bot_id):
        """By numeric bot id, or by integration id (helper | member | crypto)."""
        self.need_owner(ctx)
        if bot_id in tokens.IDS:
            tok = (self.h.bot_tokens().get(bot_id) if bot_id in ("helper", "member")
                   else tokens.resolve(self.h.store, bot_id)[0] if bot_id == "crypto" else "")
            if not tok:
                raise ApiError(404, "not_found")
            bot_id = str(tokens.bot_id(tok))
        bot = self.bot_for(bot_id)
        if bot is None:
            raise ApiError(404, "not_found")
        return self._avatar_resp("bot_" + bot_id, bot, int(bot_id))

    def _avatar_resp(self, key, bot, user_id):
        if bot is None:
            raise ApiError(503, "bot_not_ready", "Бот ещё запускается.")
        data = self.avatar(key, bot, user_id)
        if not data:
            raise ApiError(404, "not_found", "Аватара нет.")
        return Resp(data, "image/jpeg", headers={"Cache-Control": "private, max-age=3600"})

    # -------------------------------------------------------------- admin --
    def api_overview(self, req, ctx):
        self.need_owner(ctx)
        toks = self.h.bot_tokens()
        shown = [(tid, tokens.resolve(self.h.store, "crypto")[0] if tid == "crypto" else toks.get(tid))
                 for tid in ("helper", "member", "crypto")]
        bots, seen = [], set()
        for tid, tok in shown:
            if not tok or tok in seen:
                continue
            seen.add(tok)
            me = dict(self.get_me(tok), integration=tid)
            me["avatar_url"] = f"./api/admin/bot-avatar/{me['id']}" if me["ok"] else None
            bots.append(me)
        rec = self.h.rec
        return jr({"counts": self.h.members.counts(), "activity": self.h.members.activity(30),
                   "bots": bots, "sync": rec.last_sync if rec is not None else None,
                   "vpn_configured": self.h.xui is not None, "matrix_configured": self.h.syn is not None})

    def _xui_state(self):
        """({email: traffic}, set(online emails), warning|None)."""
        xui = self.h.xui
        if xui is None:
            return {}, set(), "vpn_unconfigured"
        try:
            traffic = {c.get("email"): traffic_of(c) for c in xui.clients()}
        except Exception as e:
            log.warning("xui clients failed: %s", type(e).__name__)
            return {}, set(), "vpn_unavailable"
        try:
            online = set(xui.onlines())
        except Exception:
            online = set()
        return traffic, online, None

    def api_members(self, req, ctx):
        self.need_owner(ctx)
        traffic, online, warn = self._xui_state()
        first = time.strftime("%Y-%m-%d", time.localtime(time.time() - 29 * 86400))
        act = {r["uid"]: r for r in self.h.store.q(
            "SELECT uid, SUM(messages) AS m, SUM(app) AS a FROM activity WHERE day>=? GROUP BY uid", first)}
        out = []
        for m in self.h.members.list():
            v = self.h.members.view(m)
            a = act.get(m["id"])
            v.update(traffic=traffic.get(v["vpn_email"]), online=v["vpn_email"] in online,
                     messages_30d=(a["m"] or 0) if a else 0, app_30d=(a["a"] or 0) if a else 0)
            out.append(v)
        return jr(out, headers={"X-Warning": warn} if warn else None)

    def api_member(self, req, ctx, uid):
        self.need_owner(ctx)
        uid = int(uid)
        m = self.h.members.get(uid)
        if m is None:
            raise ApiError(404, "not_found", "Участник не найден.")
        v = self.h.members.view(m)
        traffic = links = None
        online = False
        xui = self.h.xui
        if xui is not None:
            try:
                traffic = traffic_of(xui.client_get(v["vpn_email"]))
                if traffic is None:
                    traffic = next((traffic_of(c) for c in xui.clients() if c.get("email") == v["vpn_email"]), None)
                links = len(xui.client_links(v["vpn_email"]) or [])
                online = v["vpn_email"] in (xui.onlines() or [])
            except Exception as e:
                log.warning("xui member lookup failed: %s", type(e).__name__)
        v.update(events=self.h.members.events(uid, 50), vpn_links_count=links, traffic=traffic,
                 online=online, matrix_accounts=self.accounts(uid))
        return jr(v)

    def api_member_action(self, req, ctx, uid):
        self.need_owner(ctx)
        uid = int(uid)
        body = req.json()
        ms = self.h.members
        if ms.get(uid) is None:
            raise ApiError(404, "not_found", "Участник не найден.")
        act = body.get("action")
        contact = env("OWNER_CONTACT")
        ask = f" Вопросы — {esc(contact)}." if contact else ""
        if act == "extend":
            row = ms.extend(uid, as_int(body.get("days"), 1, 3650, "days"))
            self.tell(uid, f"✅ Подписка продлена до {mem.ru_date(row['expires_ts'])}.")
        elif act == "set_expiry":
            ts = body.get("ts")
            if ts is not None:
                ts = as_int(ts, 1, 4102444800, "ts")
            ms.set_expiry(uid, ts)
            self.tell(uid, "✅ Подписка теперь без срока." if ts is None
                      else f"📅 Подписка действует до {mem.ru_date(ts)}.")
        elif act == "suspend":
            ms.suspend(uid)
            self.tell(uid, f"⏸ Доступ приостановлен владельцем.{ask}")
        elif act == "resume":
            ms.resume(uid)
            self.tell(uid, "▶️ Доступ снова открыт.")
        elif act == "services":
            svcs = as_services(body.get("services"))
            ms.set_services(uid, svcs)
            self.tell(uid, f"🔧 Твои сервисы обновлены: {esc(svc_names(ms.get(uid)['services']))}.")
        elif act == "note":
            note = body.get("note", "")
            if not isinstance(note, str) or len(note) > 200:
                raise ApiError(400, "bad_request", "Заметка: не больше 200 символов.")
            ms.set_note(uid, note)
        elif act == "delete":
            ms.delete(uid)
            self.tell(uid, f"🚫 Доступ к MeowHub закрыт владельцем.{ask}")
            return jr({"deleted": True})
        else:
            raise ApiError(400, "bad_request", "Неизвестное действие.")
        return jr(ms.view(ms.get(uid)))

    def api_grant(self, req, ctx):
        self.need_owner(ctx)
        body = req.json()
        uid = as_int(body.get("uid"), 1, 2 ** 53, "uid")
        days = as_int(body.get("days", 30), 1, 3650, "days")
        svcs = body.get("services")
        svcs = mem.DEFAULT_SERVICES if svcs is None else as_services(svcs)
        row = self.h.members.grant(uid, None, days, svcs)
        self.tell(uid, f"🎁 Тебе открыт доступ к MeowHub до {mem.ru_date(row['expires_ts'])}: "
                       f"{esc(svc_names(row['services']))}.")
        return jr(self.h.members.view(row))

    def share_url(self, code):
        u = self.member_username() or self.h.bot_username
        return f"https://t.me/{u}?start={code}" if u else None

    def api_codes(self, req, ctx):
        self.need_owner(ctx)
        return jr([dict(c, share_url=self.share_url(c["code"])) for c in self.h.members.codes(include_dead=True)])

    def api_code_new(self, req, ctx):
        self.need_owner(ctx)
        body = req.json()
        days = as_int(body.get("days", 30), 1, 3650, "days")
        svcs = body.get("services")
        svcs = mem.DEFAULT_SERVICES if svcs is None else as_services(svcs)
        uses = as_int(body.get("uses_max", 1), 1, 100, "uses_max")
        valid = as_int(body.get("valid_days", 30), 1, 365, "valid_days")
        note = body.get("note", "")
        if not isinstance(note, str) or len(note) > 200:
            raise ApiError(400, "bad_request", "Заметка: не больше 200 символов.")
        code = self.h.members.new_code(days, svcs, uses, valid, note)
        return jr({"code": code, "share_url": self.share_url(code)})

    def api_code_revoke(self, req, ctx, code):
        self.need_owner(ctx)
        if not self.h.members.revoke_code(code):
            raise ApiError(404, "not_found", "Код не найден.")
        return jr({"ok": True})

    def _inbounds(self):
        if self.h.xui is None:
            raise ApiError(503, "vpn_unconfigured", "VPN пока не настроен.")
        return self.h.xui.inbounds()

    def _inbound_rows(self, inbounds):
        chosen = self.h.members.member_inbounds()
        if chosen is None:               # never set: the Reconciler's first-run default
            by_id = {i["id"]: i for i in inbounds}
            chosen = mem.awg_limited([
                i["id"] for i in inbounds
                if i.get("enable") and str(i.get("protocol", "")).lower() in mem.MEMBER_PROTOCOLS
                and "🧪" not in str(i.get("remark", ""))], by_id)
        return [dict(i, member=i["id"] in chosen) for i in inbounds]

    def api_inbounds(self, req, ctx):
        self.need_owner(ctx)
        return jr(self._inbound_rows(self._inbounds()))

    def api_inbounds_set(self, req, ctx):
        self.need_owner(ctx)
        ids = req.json().get("member_ids")
        if not isinstance(ids, list) or not all(isinstance(i, int) and not isinstance(i, bool) for i in ids):
            raise ApiError(400, "bad_request", "member_ids: список чисел.")
        inbounds = self._inbounds()
        by_id = {i["id"]: i for i in inbounds}
        ids = list(dict.fromkeys(ids))
        if any(i not in by_id for i in ids):
            raise ApiError(400, "unknown_inbound", "Такого инбаунда нет в 3x-ui.")
        if sum(str(by_id[i].get("protocol", "")).lower() in mem.AWG_PROTOCOLS for i in ids) > 1:
            raise ApiError(400, "two_awg", "Можно выдать не больше одного WireGuard/AmneziaWG инбаунда.")
        self.h.members.set_member_inbounds(ids)
        self.h.members.set_known_inbounds(set(self.h.members.known_inbounds()) | set(ids))
        return jr(self._inbound_rows(inbounds))

    # ------------------------------------------------------- integrations --
    def _bot_info(self, token):
        if not token:
            return None
        me = self.get_me(token)
        return {k: me[k] for k in ("id", "username", "name", "ok", "error")}

    @staticmethod
    def _with_avatar(info, tid):
        if info is not None:
            info["avatar_url"] = f"./api/admin/bot-avatar/{tid}" if info["ok"] else None
        return info

    def _xui_check(self, token):
        """{"ok", "error", "detail"} from a real inbounds() call, cached a minute."""
        if not token:
            return {"ok": False, "error": "", "detail": "не настроен"}
        ck = hashlib.sha256(token.encode()).hexdigest()
        with self._lock:
            hit = self._xui_chk
        if hit and hit[0] == ck and time.time() - hit[1] < XUI_CHECK_TTL:
            return hit[2]
        client = self.h.xui if self.h.xui is not None and getattr(self.h.xui, "token", None) == token \
            else tokens.make_xui(token)
        try:
            n = len(client.inbounds())
            res = {"ok": True, "error": "", "detail": plural_inbounds(n)}
        except XUIError as e:
            res = {"ok": False, "error": tokens.scrub(e, token)[:200], "detail": ""}
        with self._lock:
            self._xui_chk = (ck, time.time(), res)
        return res

    def _integration(self, tid):
        st = self.h.store
        tok, src = tokens.resolve(st, tid)
        item = {"id": tid, "kind": "api" if tid == "xui" else "bot", "configured": bool(tok),
                "source": src, "masked": tokens.mask(tok), "updated_ts": tokens.updated_ts(st, tid),
                "restart_on_change": tid in ("helper", "member")}
        if tid == "xui":
            item["check"] = self._xui_check(tok)
        else:
            item["bot"] = self._with_avatar(self._bot_info(tok), tid)
        if tid == "crypto":
            item["note"] = CRYPTO_NOTE
        return item

    def api_integrations(self, req, ctx):
        self.need_owner(ctx)
        return jr({"items": [self._integration(t) for t in tokens.IDS]})

    def _check_bot(self, token):
        """getMe on a candidate token -> the raw answer; ApiError 400 bad_token otherwise."""
        if not tokens.BOT_TOKEN_RE.match(token):
            raise ApiError(400, "bad_token", "Это не похоже на токен бота: нужен вид 123456789:AAH…")
        try:
            return self._bot_obj(token).call("getMe", _timeout=10)
        except TelegramError as e:
            raise ApiError(400, "bad_token", f"Telegram ответил: {tokens.scrub(e, token)[:150]}") from None

    def _check_xui(self, token):
        if not tokens.API_TOKEN_RE.match(token):
            raise ApiError(400, "bad_token", "Токен 3x-ui не должен содержать пробелов и спецсимволов.")
        try:
            n = len(tokens.make_xui(token).inbounds())
        except XUIError as e:
            raise ApiError(400, "bad_token", f"3x-ui ответил: {tokens.scrub(e, token)[:150]}") from None
        return n

    def _other_bot_ids(self, tid):
        """Numeric bot id behind the *other* of helper/member (0 when none)."""
        other = "member" if tid == "helper" else "helper"
        tok = tokens.resolve(self.h.store, other)[0]
        ids = {tokens.bot_id(tok)} if tok else set()
        if other == "helper" and getattr(self.h, "helper_id", None):
            ids.add(self.h.helper_id)
        return {i for i in ids if i}

    def _swap_xui(self, token):
        """Hot swap: the next 3x-ui call, from the API or a running sync, uses it."""
        new = tokens.make_xui(token)
        self.h.xui = new
        if self.h.rec is not None:
            self.h.rec.xui = new

    def _apply_integration(self, tid, new, reset):
        st = self.h.store
        old = tokens.resolve(st, tid)[0]
        me = None
        if reset:
            if not (st.get(tokens.key(tid)) or "").strip():
                raise ApiError(404, "no_override")
            new = tokens.fallback(st, tid)[0]
            if tid == "helper" and not new:
                raise ApiError(400, "no_fallback")
        elif tid == "xui":
            self._check_xui(new)
        else:
            me = self._check_bot(new)
            if tid in ("helper", "member") and int(me.get("id") or 0) in self._other_bot_ids(tid):
                raise ApiError(400, "same_bot")
        if tid == "crypto" and not crypto_apply("" if reset else new):
            raise ApiError(502, "crypto_apply_failed")
        if reset:
            tokens.clear_override(st, tid)
        else:
            tokens.set_override(st, tid, new)
        with self._lock:
            self._getme.clear()
            self._xui_chk = None
            if me is not None and tid in ("helper", "member"):
                self._getme[new.split(":", 1)[0]] = (time.time(), self._me_val(new, me))
        if tid == "xui":
            self._swap_xui(new)
        restart = tid in ("helper", "member") and new != old
        if restart:
            schedule_restart()
        return jr({"item": self._integration(tid), "restarting": restart})

    def api_integration_set(self, req, ctx, tid):
        self.need_owner(ctx)
        if tid not in tokens.IDS:
            raise ApiError(404, "not_found")
        tok = req.json().get("token")
        if not isinstance(tok, str):
            raise ApiError(400, "bad_request", "Поле token: ожидается строка.")
        tok = tok.strip()
        if not tok:
            raise ApiError(400, "bad_token", "Токен пустой.")
        if len(tok) > tokens.MAX_LEN:
            raise ApiError(400, "bad_token", "Токен слишком длинный.")
        with self._integ_lock:
            return self._apply_integration(tid, tok, False)

    def api_integration_reset(self, req, ctx, tid):
        self.need_owner(ctx)
        if tid not in tokens.IDS:
            raise ApiError(404, "not_found")
        with self._integ_lock:
            return self._apply_integration(tid, "", True)

    def api_sync(self, req, ctx):
        self.need_owner(ctx)
        rec = self.h.rec
        if rec is None:
            raise ApiError(503, "bot_not_ready", "Синхронизация ещё не запущена.")
        run_timeout(rec.sync_all)
        return jr(rec.last_sync)

    # ----------------------------------------------------------- /go page --
    def go_page(self, app_id, query):
        spec = APPS_BY_ID.get(app_id)
        u = (urllib.parse.parse_qs(query).get("u") or [""])[0]
        base = sub_base()
        if spec is None or not u.startswith(base) or len(u) > 2000 or re.search(r"[\x00-\x20\"'<>\\`]", u):
            raise ApiError(400, "bad_request", "Некорректная ссылка.")
        scheme = spec[3](u)
        title = f"Открыть в {spec[1]}"
        js = json.dumps(scheme).replace("<", "\\u003c")
        page = f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="0;url={esc(scheme)}">
<title>{esc(title)}</title>
<style>
:root{{color-scheme:light dark;--bg:#fff;--fg:#111;--ac:#2563eb}}
@media (prefers-color-scheme:dark){{:root{{--bg:#111;--fg:#eee;--ac:#3b82f6}}}}
body{{margin:0;min-height:100vh;display:flex;flex-direction:column;align-items:center;justify-content:center;
gap:16px;padding:16px;box-sizing:border-box;font:16px system-ui,sans-serif;background:var(--bg);color:var(--fg);text-align:center}}
a.b{{display:inline-block;padding:14px 28px;border-radius:12px;background:var(--ac);color:#fff;text-decoration:none;font-weight:600}}
p{{margin:0;opacity:.7}}
</style></head><body>
<a class="b" href="{esc(scheme)}">{esc(title)}</a>
<p>Если приложение не открылось, установи {esc(spec[1])} и нажми кнопку ещё раз.</p>
<script>location.href={js};</script>
</body></html>"""
        return Resp(page.encode(), "text/html; charset=utf-8", headers={
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'",
            "Cache-Control": "no-store"})

    # ------------------------------------------------------------- static --
    def static(self, path):
        root = os.path.realpath(env("WEBAPP_DIR", "/webapp"))
        rel = urllib.parse.unquote(path).lstrip("/")
        if "\x00" in rel:
            raise ApiError(404, "not_found")
        full = os.path.realpath(os.path.join(root, rel))
        if full != root and not full.startswith(root + os.sep):
            raise ApiError(404, "not_found")
        if not os.path.isfile(full):
            # SPA fallback, but never hand HTML to something asking for a file (/assets/x.js)
            if "." in os.path.basename(rel) or rel.startswith(("assets/", "api/")):
                raise ApiError(404, "not_found")
            full = os.path.join(root, "index.html")
            if not os.path.isfile(full):
                raise ApiError(503, "not_built", "frontend not built")
        try:
            with open(full, "rb") as f:
                data = f.read()
        except OSError:
            raise ApiError(404, "not_found") from None
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/json", "image/svg+xml"):
            ctype += "; charset=utf-8"
        name = os.path.basename(full)
        rel_real = os.path.relpath(full, root)
        if name == "index.html":
            cache = "no-cache"
        elif rel_real.startswith("assets" + os.sep):
            cache = "public, max-age=31536000, immutable"
        else:
            cache = "public, max-age=3600"
        return Resp(data, ctype, headers={"Cache-Control": cache})


# ------------------------------------------------------------ route table --
# (method, regex, auth, handler name); auth: "public" | "any"
ROUTES = [
    ("GET", r"/api/me", "any", "api_me"),
    ("POST", r"/api/redeem", "any", "api_redeem"),
    ("GET", r"/api/vpn", "any", "api_vpn"),
    ("GET", r"/api/matrix", "any", "api_matrix"),
    ("POST", r"/api/matrix/create", "any", "api_matrix_create"),
    ("POST", r"/api/matrix/password", "any", "api_matrix_password"),
    ("GET", r"/api/avatar/(-?\d{1,20})", "any", "api_avatar"),
    ("GET", r"/api/admin/overview", "any", "api_overview"),
    ("GET", r"/api/admin/bot-avatar/(\d{1,20}|helper|member|crypto)", "any", "api_bot_avatar"),
    ("GET", r"/api/admin/members", "any", "api_members"),
    ("GET", r"/api/admin/members/(\d{1,20})", "any", "api_member"),
    ("POST", r"/api/admin/members/(\d{1,20})", "any", "api_member_action"),
    ("POST", r"/api/admin/grant", "any", "api_grant"),
    ("GET", r"/api/admin/codes", "any", "api_codes"),
    ("POST", r"/api/admin/codes", "any", "api_code_new"),
    ("POST", r"/api/admin/codes/([A-Za-z0-9-]{1,40})/revoke", "any", "api_code_revoke"),
    ("GET", r"/api/admin/inbounds", "any", "api_inbounds"),
    ("POST", r"/api/admin/inbounds", "any", "api_inbounds_set"),
    ("POST", r"/api/admin/sync", "any", "api_sync"),
    ("GET", r"/api/admin/integrations", "any", "api_integrations"),
    ("POST", r"/api/admin/integrations/([a-z]{1,10})", "any", "api_integration_set"),
    ("POST", r"/api/admin/integrations/([a-z]{1,10})/reset", "any", "api_integration_reset"),
]
ROUTES = [(m, re.compile(p + r"\Z"), a, f) for m, p, a, f in ROUTES]
GO_RE = re.compile(r"/go/([a-z0-9]{1,20})\Z")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "meowhub"
    sys_version = ""
    timeout = 30

    # ---- logging: never initData, headers, bodies or query strings ----
    def log_message(self, fmt, *args):
        log.debug("%s", fmt % args if args else fmt)

    def log_request(self, code="-", size="-"):
        log.debug("%s %s -> %s", self.command, self.path.split("?", 1)[0], code)

    # ---- request body ----
    def json(self):
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ctype != "application/json":
            raise ApiError(400, "bad_content_type", "Ожидается application/json.")
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise ApiError(400, "bad_request") from None
        if n < 0:
            raise ApiError(400, "bad_request")
        if n > MAX_BODY:
            self.close_connection = True
            left = min(n, DRAIN_MAX)
            while left > 0:                  # let the client finish sending so it can read the 413
                chunk = self.rfile.read(min(left, 65536))
                if not chunk:
                    break
                left -= len(chunk)
            raise ApiError(413, "too_large", "Слишком большой запрос.")
        raw = self.rfile.read(n) if n else b""
        self._body_read = True
        if not raw.strip():
            return {}
        try:
            body = json.loads(raw)
        except ValueError:
            raise ApiError(400, "bad_json", "Тело запроса — не JSON.") from None
        if not isinstance(body, dict):
            raise ApiError(400, "bad_json", "Тело запроса должно быть объектом.")
        return body

    # ---- dispatch ----
    def _handle(self, method):
        self._body_read = False
        path, _, query = self.path.partition("#")[0].partition("?")
        app = self.server.app
        try:
            resp = self._route(app, method, path, query)
        except ApiError as e:
            resp = jr({"error": e.code, "message": e.message}, e.status)
            if e.status == 503 and e.code == "not_built":
                resp = Resp(b"frontend not built", "text/plain; charset=utf-8", 503)
        except XUIError as e:
            log.warning("xui error on %s: %s", path, e)
            resp = jr({"error": "vpn_error", "message": "VPN-панель не отвечает, попробуй позже."}, 502)
        except SynapseError as e:
            log.warning("synapse error on %s: %s", path, type(e).__name__)
            resp = jr({"error": "matrix_error", "message": "Мессенджер не отвечает, попробуй позже."}, 502)
        except Exception:
            log.exception("unhandled error on %s %s", method, path)
            resp = jr({"error": "internal", "message": MESSAGES["internal"]}, 500)
        if method == "POST" and not self._body_read and (self.headers.get("Content-Length") or "0").strip() != "0":
            self.close_connection = True
        self._send(resp)

    def _route(self, app, method, path, query):
        if path.startswith("/api/"):
            for m, rx, auth, fn in ROUTES:
                mo = rx.match(path)
                if mo and m == method:
                    ctx = app.authenticate(self.headers)
                    return getattr(app, fn)(self, ctx, *mo.groups())
            if any(rx.match(path) for _, rx, _, _ in ROUTES):
                raise ApiError(405, "method_not_allowed", "Метод не поддерживается.")
            raise ApiError(404, "not_found")
        if method != "GET":
            raise ApiError(405, "method_not_allowed", "Метод не поддерживается.")
        mo = GO_RE.match(path)
        if mo:
            return app.go_page(mo.group(1), query)
        return app.static(path)

    def _send(self, resp):
        self.send_response(resp.status)
        self.send_header("Content-Type", resp.ctype)
        self.send_header("Content-Length", str(len(resp.body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        if resp.ctype.startswith("application/json"):
            self.send_header("Cache-Control", "no-store")
        for k, v in resp.headers.items():
            self.send_header(k, v)
        if self.close_connection:
            self.send_header("Connection", "close")
        self.end_headers()
        try:
            self.wfile.write(resp.body)
        except OSError:
            pass

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def start(helper, port=None, host="0.0.0.0"):
    """Start the server in a daemon thread; returns the server (`server_address[1]` = port)."""
    if port is None:
        port = int(env("WEBAPP_PORT", "8095"))
    srv = Server((host, port), Handler)
    srv.app = App(helper)
    threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.1},
                     name="webapp", daemon=True).start()
    log.info("webapp listening on %s:%s", host, srv.server_address[1])
    return srv
