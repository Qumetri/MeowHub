"""MeowHub helper bot: server health, hub links, YouTube downloads, the
membership system (access codes, VPN + Matrix for members) and -- for the
owner only -- the logins for the hub's services.

Roles:
  owner     HELPER_OWNER_ID. Everything, including passwords, codes and the
            member list.
  member    anyone who redeemed an access code (or was granted by the owner).
            While the membership is active: the services on it (VPN, Matrix
            account via the Mini App, optionally the bot "tools"). Expired or
            suspended members get a pause notice; codes still work.
  stranger  told the bot is closed and to send an access code; the owner gets
            one notice with a "give access" button.

Two bots (when a member bot token is configured -- tokens.py): this helper bot
is then the owner's ops bot and everyone else is sent to the member bot, which
gives even the owner the member/stranger experience. Both run in this process
and share one Helper (store, Members, 3x-ui, Synapse, Reconciler, web server);
the member bot's handlers are the same methods run through a MemberFront.

Long polling, not a webhook: nothing new listens on the internet for the bot
itself. The Mini App web server (webapp.py) is started from here.
"""
import logging
import math
import os
import re
import threading
import time
import types
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from urllib.parse import quote

import downloads
import health
import hub
import i18n
import links
import tokens
import webapp
from i18n import tr
from members import AWG_PROTOCOLS, DEFAULT_SERVICES, GRANTABLE, SERVICES, Members, Reconciler, norm_code, owner_contact, ru_date
from store import Store
from synapse import Synapse
from tg import Bot, TelegramError

log = logging.getLogger("bot")

PASS_TTL = int(os.environ.get("HELPER_PASS_TTL", "60"))     # seconds a password stays visible
REPORT_HOUR = os.environ.get("HELPER_REPORT_HOUR", "9").strip()
HEARTBEAT = "/tmp/heartbeat"
DAY = 86400
WIZ_TTL = 3600
PAGE = 8

# Reply-keyboard buttons: B_* are the Russian labels; a pressed button is matched through btn_key(),
# which knows the labels of BOTH languages (a user may still have the old keyboard on screen).
BTN_KEYS = ("health", "links", "yt", "pass", "vpn", "mx", "sub", "help", "members", "code", "app", "svc", "lang")
B_HEALTH, B_LINKS, B_YT, B_PASS = (tr("ru", "btn." + k) for k in ("health", "links", "yt", "pass"))
B_VPN, B_MX, B_SUB, B_HELP = (tr("ru", "btn." + k) for k in ("vpn", "mx", "sub", "help"))
B_MEMBERS, B_CODE, B_APP = (tr("ru", "btn." + k) for k in ("members", "code", "app"))
B_SVC, B_LANG = tr("ru", "btn.svc"), tr("ru", "btn.lang")
BTN_BY_LABEL = {tr(lg, "btn." + k): k for k in BTN_KEYS for lg in i18n.LANGS}


def btn_key(text):
    """'health' | 'vpn' | ... for the text of a pressed reply-keyboard button, else None."""
    return BTN_BY_LABEL.get(text)
URL_RE = re.compile(r"https?://\S+")
SERVICE_ID = re.compile(r"^[a-z0-9_-]{1,32}$")
CODE_RE = re.compile(r"(?i)^\s*meow[\s-]*[a-z0-9]{4}[\s-]*[a-z0-9]{4}\s*$")
OWNER_CMDS = ("/pass", "/setpass", "/delpass", "/users", "/members", "/code", "/allow", "/deny", "/services")
SHORT = {s: tr("ru", "short." + s) for s in ("vpn", "matrix", "tools")}


def short(s, lang="ru"):
    return tr(lang, "short." + s)
WIZ_DAYS = (30, 90, 180, 365)
SEEN_MAX = 500
YT_NAG_EVERY = 3600


def esc(s):
    return health.esc(s)


def code_fmt(norm):
    """Bare code -> MEOW-XXXX-XXXX."""
    return f"{norm[:4]}-{norm[4:8]}-{norm[8:]}" if len(norm) == 12 else norm


def svc_names(svcs, sep=", ", lang="ru"):
    return sep.join(SERVICES[s]["name_" + lang] for s in SERVICES if s in (svcs or []))


def days_left(m):
    exp = m.get("expires_ts")
    return None if exp is None else max(0, math.ceil((exp - time.time()) / DAY))


def person(m):
    """Display name of a members row."""
    n = f"{m.get('first_name', '')} {m.get('last_name', '')}".strip()
    return n or (f"@{m['username']}" if m.get("username") else str(m["id"]))


def from_name(frm):
    return " ".join(x for x in (frm.get("first_name"), frm.get("last_name")) if x) or "?"


def ident(m):
    """<b>Name</b> (@user, <code>id</code>) for owner notices."""
    un = f"@{esc(m['username'])}, " if m.get("username") else ""
    return f"<b>{esc(person(m))}</b> ({un}<code>{m['id']}</code>)"


class Helper:
    kind = "helper"

    def __init__(self, store):
        self.store = store
        self.bot = None                   # the helper bot (tg.Bot) once its token is known
        self.member_bot = None            # the member bot, only when configured and valid
        self.member_bot_username = ""
        self.member_front = None          # MemberFront for member_bot
        self.helper_id = None             # numeric id of the helper bot (from getMe)
        self.pool = ThreadPoolExecutor(8)
        self.lc = {}                      # uid -> last Telegram language_code seen
        self.yt_nag = {}                  # uid -> last "downloads are off" notice
        self.nagged = set()               # strangers already reported to the owner
        self.seen_from = {}               # stranger uid -> Telegram `from` (for the grant welcome)
        self.wiz = {}                     # message id -> code wizard state
        self.members = Members(store)
        links.configure(store)
        self.xui = tokens.xui_from(store)
        self.syn = Synapse.from_env(lambda: store.get("matrix_admin_token"),
                                    lambda t: store.set("matrix_admin_token", t))
        self.rec = Reconciler(self.members, self.xui, self.syn, send=self.notify, owner_id=self.owner_id)
        self.bot_username = ""
        base = os.environ.get("BASE_DOMAIN", "").strip()
        path = os.environ.get("BOT_APP_PATH", "").strip().strip("/")
        self.webapp_url = f"https://{base}/{path}/" if base and path else ""
        self.downloads = downloads.Downloads(store, self.members, self.owner_id, lambda: self.webapp_url)

    @property
    def core(self):
        """The shared object (a MemberFront returns the Helper it fronts)."""
        return self

    def bot_tokens(self):
        """{"helper": token|None, "member": token|None} for Mini App initData checks."""
        return {"helper": self.bot.token if self.bot is not None else None,
                "member": self.member_bot.token if self.member_bot is not None else None}

    # ---------------------------------------------------------------- access --
    def owner_id(self):
        v = os.environ.get("HELPER_OWNER_ID", "").strip() or self.store.get("owner_id")
        return int(v) if v.lstrip("-").isdigit() else None

    def role(self, uid):
        """'owner' | 'member' (a row exists, any status) | None.

        Only the helper bot knows an owner. With a member bot configured it is
        closed to everyone else, and the member bot treats the owner by their
        membership like anybody."""
        if uid is None:
            return None
        if self.kind == "helper":
            if uid == self.owner_id():
                return "owner"
            if self.core.member_bot is not None:
                return None
        return "member" if self.members.get(uid) else None

    def tools_ok(self, uid, role=None):
        role = role or self.role(uid)
        return role == "owner" or (role == "member" and self.members.has(uid, "tools"))

    @staticmethod
    def contact():
        return owner_contact()

    def app_url(self, page=None):
        return self.webapp_url + (f"?p={page}" if page else "") if self.webapp_url else ""

    def app_markup(self, label, page=None):
        """Inline keyboard with one web_app button, or None without a Mini App."""
        if not self.webapp_url:
            return None
        return {"inline_keyboard": [[{"text": label, "web_app": {"url": self.app_url(page)}}]]}

    def lang(self, uid, frm=None):
        """Effective language of `uid` (preference, else Telegram's language_code, else ru).
        Passing the update's `from` also remembers its language_code."""
        core = self.core
        if frm is not None and uid is not None:
            code = frm.get("language_code")
            if code and core.lc.get(uid) != code:
                core.lc[uid] = code
                try:
                    i18n.remember_code(core.store, uid, code)
                except Exception:                           # noqa: BLE001 - never break an update for this
                    log.warning("remember language_code failed")
        return i18n.lang_of(core.store, uid, core.lc.get(uid))

    def olang(self):
        """The owner's language (for notices to the owner)."""
        return self.lang(self.core.owner_id())

    def keyboard(self, uid):
        role = self.role(uid)
        L = self.lang(uid)
        lb = lambda k: {"text": tr(L, "btn." + k)}               # noqa: E731
        app = [[{"text": tr(L, "btn.app"), "web_app": {"url": self.app_url()}}]] if self.webapp_url else []
        if role == "owner":
            rows = [[lb("health"), lb("links"), lb("pass")], [lb("yt")],
                    [lb("members"), lb("code"), lb("svc")]]
            rows += [app[0] + [lb("lang")]] if app else [[lb("lang")]]
        elif role == "member":
            m = self.members.get(uid)
            if self.members.status(m) != "active":
                rows = app + [[lb("help"), lb("lang")]]
            else:
                rows = list(app)
                svc = [lb(k) for s, k in (("vpn", "vpn"), ("matrix", "mx")) if self.members.has(uid, s)]
                if svc:
                    rows.append(svc)
                rows.append([lb("sub"), lb("help"), lb("lang")])
                if self.members.has(uid, "tools"):
                    rows.append([lb("health"), lb("links")])
                if self.members.has(uid, "youtube"):
                    rows.append([lb("yt")])
        else:
            return None
        return {"keyboard": rows, "resize_keyboard": True, "is_persistent": True}

    # ------------------------------------------------------------- messaging --
    def bot_for_chat(self, chat_id):
        """Owner -> the helper bot; everybody else -> the member bot (the helper
        bot again when there is none)."""
        core = self.core
        if core.member_bot is not None and chat_id != core.owner_id():
            return core.member_bot
        return core.bot

    def front_for(self, chat_id):
        """The Helper / MemberFront whose bot talks to `chat_id` (see bot_for_chat)."""
        core = self.core
        if core.member_bot is not None and core.member_front is not None and chat_id != core.owner_id():
            return core.member_front
        return core

    def share_username(self):
        """Bot that access-code links point to: the member bot when there is one."""
        core = self.core
        return (core.member_bot_username if core.member_bot is not None else "") or core.bot_username

    def notify(self, chat_id, text, reply_markup=None):
        """HTML message that never raises; the message dict or None. Routed by
        recipient (bot_for_chat); this is also what the Reconciler sends through."""
        bot = self.bot_for_chat(chat_id)
        if bot is None:
            return None
        try:
            return bot.send(chat_id, text, reply_markup=reply_markup)
        except TelegramError as e:
            log.info("notify %s: %s", chat_id, e)
            return None

    def notify_owner(self, text, reply_markup=None):
        oid = self.owner_id()
        return self.notify(oid, text, reply_markup) if oid else None

    def _edit(self, cid, mid, text, markup=None):
        if cid is None or mid is None:
            return
        try:
            self.bot.edit(cid, mid, text, reply_markup=markup)
        except TelegramError as e:
            log.info("edit %s/%s: %s", cid, mid, e)

    def send_menu(self, cid, uid, text, page=None):
        """Text with the inline app button, then the reply keyboard (one
        message cannot carry both kinds of markup)."""
        L = self.lang(uid)
        self.bot.send(cid, text, reply_markup=self.app_markup(tr(L, "menu.open_app"), page))
        self.bot.send(cid, tr(L, "menu.hint"), reply_markup=self.keyboard(uid))

    # ------------------------------------------------------------------ loop --
    def run(self):
        while True:
            # Page override, then .env (HELPER_BOT_TOKEN); a token saved with ctl.py is the last fallback.
            token = tokens.resolve(self.store, "helper")[0]
            if not token:
                log.warning("no bot token yet -- set HELPER_BOT_TOKEN in .env, or on the Bots page "
                            "(or: docker compose exec -it helper python3 /app/app/ctl.py token)")
                self._beat()
                time.sleep(30)
                continue
            self.bot = Bot(token)
            try:
                me = self.bot.call("getMe")
                log.info("running as @%s", me.get("username"))
                self.bot_username = me.get("username") or ""
                self.helper_id = me.get("id") or tokens.bot_id(token)
                break
            except TelegramError as e:
                log.error("token rejected: %s", e)
                self._beat()
                time.sleep(60)
        self._start_member_bot()
        for front in (self, self.member_front):
            if front is not None:
                try:
                    front._commands()
                except TelegramError as e:
                    log.warning("setMyCommands (%s): %s", front.kind, e)
        try:
            webapp.start(self)
        except Exception:                                   # noqa: BLE001
            log.exception("webapp failed to start")
        threading.Thread(target=self.rec.run, daemon=True, name="reconciler").start()
        self.downloads.start()
        self._menu_button()
        if self.member_front is not None:
            self.member_front._menu_button()
            threading.Thread(target=self.poll, args=(self.member_front, "offset_member"),
                             daemon=True, name="member-poll").start()
        threading.Thread(target=Monitor(self).run, daemon=True, name="monitor").start()
        self.poll(self, "offset")

    def _start_member_bot(self):
        """Attach the member bot when its token is configured and sane; otherwise
        stay in single-bot mode (never fatal)."""
        token = tokens.resolve(self.store, "member")[0]
        if not token:
            return
        bot = Bot(token)
        me = None
        for attempt in range(3):
            try:
                me = bot.call("getMe")
                break
            except TelegramError as e:
                log.error("member bot token rejected: %s", e)
                if not str(e).startswith("network") or attempt == 2:
                    return
                time.sleep(3)
        if me.get("id") == self.helper_id:
            log.error("member bot token is the helper bot's own -- ignoring it")
            return
        log.info("member bot running as @%s", me.get("username"))
        self.attach_member_bot(bot, me.get("username") or "")

    def attach_member_bot(self, bot, username):
        self.member_bot = bot
        self.member_bot_username = username
        self.member_front = MemberFront(self, bot, username)

    def poll(self, front, offset_key):
        """Long-poll one bot; its updates are handled by `front` (Helper or MemberFront)."""
        offset = int(self.store.get(offset_key, "0") or 0)
        while True:
            self._beat()
            try:
                ups = front.bot.call("getUpdates", _timeout=70, offset=offset or None,
                                     allowed_updates=["message", "callback_query"], timeout=50)
            except TelegramError as e:
                log.warning("getUpdates (%s): %s", front.kind, e)
                time.sleep(5)
                continue
            for u in ups:
                offset = u["update_id"] + 1
                self.pool.submit(front._safe, u)
            if ups:
                self.store.set(offset_key, offset)

    def _beat(self):
        try:
            with open(HEARTBEAT, "w") as f:
                f.write(str(int(time.time())))
        except OSError:
            pass

    def _menu_button(self):
        if not self.webapp_url:
            return
        web_app = {"type": "web_app", "text": "MeowHub", "web_app": {"url": self.webapp_url}}
        try:
            if self.kind == "helper" and self.core.member_bot is not None:
                # the ops bot: only the owner's chat gets the app button
                self.bot.call("setChatMenuButton", menu_button={"type": "commands"})
                oid = self.owner_id()
                if oid:
                    self.bot.call("setChatMenuButton", chat_id=oid, menu_button=web_app)
            else:
                self.bot.call("setChatMenuButton", menu_button=web_app)
        except TelegramError as e:
            log.warning("setChatMenuButton (%s): %s", self.kind, e)

    @staticmethod
    def command_list(lang, owner=False):
        """setMyCommands payload in `lang`: the member list, plus the ops commands for the owner."""
        d = lambda c: {"command": c, "description": tr(lang, "cmd." + c)}      # noqa: E731
        base = [d("start"), d("help"), d("vpn"), d("matrix"), d("sub"), d("lang")]
        if not owner:
            return base
        return base + [d("health"), d("links"), d("yt"), d("code"), d("members"), d("services"),
                       d("pass"), d("setpass"), d("delpass")]

    def _commands(self):
        """Default-scope commands: English text, plus Russian for the Russian-speaking
        Telegram languages; the owner's chat gets its own list in the owner's language."""
        def default(lang):
            cmds = self.command_list(lang)
            return cmds[:2] if self.kind == "helper" and self.core.member_bot is not None else cmds

        if self.kind == "member":
            try:
                self.bot.call("setMyCommands", commands=default("en"))
                for code in i18n.RU_CODES:
                    self.bot.call("setMyCommands", commands=default("ru"), language_code=code)
            except TelegramError as e:
                log.warning("member bot commands: %s", e)
            return
        self.bot.call("setMyCommands", commands=default("en"))
        for code in i18n.RU_CODES:
            self.bot.call("setMyCommands", commands=default("ru"), language_code=code)
        oid = self.owner_id()
        if oid:
            try:
                self.bot.call("setMyCommands", scope={"type": "chat", "chat_id": oid},
                              commands=self.command_list(self.lang(oid), owner=True))
            except TelegramError as e:
                log.info("owner commands: %s (has the owner pressed /start yet?)", e)

    def refresh_commands(self, uid):
        """After a language change: this chat's own command list in the user's language
        (the owner's full list; for everybody else an explicit list, or none for `auto`
        so Telegram's language-coded defaults apply)."""
        L = self.lang(uid)
        owner = self.kind == "helper" and uid == self.owner_id()
        scope = {"type": "chat", "chat_id": uid}
        try:
            if owner or i18n.get_pref(self.store, uid) != "auto":
                self.bot.call("setMyCommands", scope=scope, commands=self.command_list(L, owner=owner))
            else:
                self.bot.call("deleteMyCommands", scope=scope)
        except TelegramError as e:
            log.info("commands for %s: %s", uid, e)

    def lang_changed(self, uid):
        """Hook for the Mini App (POST /api/lang): refresh the chat commands of whichever
        bot talks to `uid`. Best effort."""
        front = self.front_for(uid)
        front.refresh_commands(uid)

    def _safe(self, u):
        try:
            if "callback_query" in u:
                self.on_callback(u["callback_query"])
            elif "message" in u:
                self.on_message(u["message"])
        except Exception:                                   # noqa: BLE001
            log.exception("update failed")

    # -------------------------------------------------------------- messages --
    def on_message(self, m):
        frm, chat = m.get("from") or {}, m["chat"]
        uid, cid = frm.get("id"), chat["id"]
        text = (m.get("text") or "").strip()
        if chat.get("type") != "private":
            return                                       # never answer in groups
        L = self.lang(uid, frm)
        btn = btn_key(text)
        role = self.role(uid)
        if role == "member":
            self.members.touch(uid, frm, "message")

        cmd, _, arg = text.partition(" ")
        cmd = cmd.split("@")[0].lower()
        if role is None and self.kind == "helper" and self.core.member_bot is not None:
            return self.redirect_to_member_bot(cid, arg if cmd == "/start" and CODE_RE.match(arg) else text, L)
        # An access code works from anyone, also as the /start deep-link payload.
        if cmd == "/start" and CODE_RE.match(arg):
            return self.redeem_code(cid, uid, frm, arg)
        if CODE_RE.match(text):
            return self.redeem_code(cid, uid, frm, text)
        # The language picker works for everybody (also strangers and paused members).
        if cmd == "/lang" or btn == "lang":
            return self.cmd_lang(cid, uid)
        if role is None:
            return self.stranger(frm, cid)

        if role == "member":
            mem = self.members.get(uid)
            if self.members.status(mem) != "active":
                if cmd == "/help" or btn == "help":
                    return self.bot.send(cid, self.member_help(mem, L), reply_markup=self.keyboard(uid))
                return self.paused(cid, uid, mem)

        # ----- features shared by owner and members (gated per service) -----
        if cmd == "/start":
            if role == "owner":
                return self.bot.send(cid, self.help_text(role, L), reply_markup=self.keyboard(uid))
            return self.member_start(cid, uid)
        if cmd == "/help" or btn == "help":
            help_ = self.help_text(role, L) if role == "owner" else self.member_help(self.members.get(uid), L)
            return self.bot.send(cid, help_, reply_markup=self.keyboard(uid))
        if cmd == "/vpn" or btn == "vpn":
            return self.cmd_vpn(cid, uid)
        if cmd == "/matrix" or btn == "mx":
            return self.cmd_matrix(cid, uid)
        if cmd == "/sub" or btn == "sub":
            return self.cmd_sub(cid, uid)

        if cmd == "/yt" or btn == "yt" or (URL_RE.search(text) and not cmd.startswith("/")):
            return self.cmd_download(cid, uid, role, cmd, arg, text)
        tools = cmd in ("/health", "/links") or btn in ("health", "links")
        if tools and not self.tools_ok(uid, role):
            return self.bot.send(cid, tr(L, "err.not_in_sub"))
        if cmd == "/health" or btn == "health":
            return self.cmd_health(cid)
        if cmd == "/links" or btn == "links":
            return self.cmd_links(cid)
        if role != "owner":
            if cmd in OWNER_CMDS or btn in ("pass", "members", "code", "svc"):
                return self.bot.send(cid, tr(L, "err.owner_only"))
            return self.member_start(cid, uid)

        # ----- owner only below -----
        if cmd == "/pass" or btn == "pass":
            return self.cmd_pass(cid)
        if cmd == "/setpass":
            return self.cmd_setpass(cid, m["message_id"], arg)
        if cmd == "/delpass":
            return self.cmd_delpass(cid, arg.strip().lower())
        if cmd in ("/members", "/users") or btn == "members":
            return self.cmd_members(cid)
        if cmd == "/services" or btn == "svc":
            return self.cmd_services(cid)
        if cmd == "/code":
            return self.cmd_code(cid, arg)
        if btn == "code":
            return self.cmd_code_wizard(cid)
        if cmd in ("/allow", "/deny"):
            return self.cmd_allow(cid, cmd, arg)
        return self.bot.send(cid, self.help_text(role, L), reply_markup=self.keyboard(uid))

    # --------------------------------------------------------------- language --
    def cmd_lang(self, cid, uid):
        L = self.lang(uid)
        self.bot.send(cid, tr(L, "lang.picker"), reply_markup={"inline_keyboard": [
            [{"text": tr(L, "lang.ru"), "callback_data": "lang:ru"},
             {"text": tr(L, "lang.en"), "callback_data": "lang:en"}],
            [{"text": tr(L, "lang.auto"), "callback_data": "lang:auto"}]]})

    def cb_lang(self, q, frm, cid, rest):
        """lang:ru|en|auto -> save, confirm in the new language and re-send the reply keyboard."""
        uid = frm.get("id")
        if rest not in i18n.PREFS or uid is None:
            return self.bot.answer(q["id"], tr(self.lang(uid, frm), "cb.bad_data"), alert=True)
        i18n.set_pref(self.store, uid, rest)
        L = self.lang(uid, frm)
        text = tr(L, "lang.set." + rest)
        self.bot.answer(q["id"], text)
        self.bot.send(cid, text, reply_markup=self.keyboard(uid))
        self.core.pool.submit(self.refresh_commands, uid)

    # ------------------------------------------------------------------ texts --
    def help_text(self, role, lang="ru"):
        L = lang
        lb = {k: tr(L, "btn." + k) for k in ("health", "links", "yt", "pass", "members", "code", "svc", "app", "lang")}
        t = tr(L, "help.common", **{k: lb[k] for k in ("health", "links", "yt")})
        if role == "owner":
            t += "\n" + tr(L, "help.owner", ttl=PASS_TTL, app=lb["app"],
                           **{"pass": lb["pass"], "members": lb["members"], "code": lb["code"], "svc": lb["svc"]})
        return t + "\n" + tr(L, "help.lang_line", lang=lb["lang"])

    def status_line(self, m, lang=None):
        L = lang or self.lang(m["id"])
        st = self.members.status(m)
        exp = m.get("expires_ts")
        if st == "suspended":
            return tr(L, "st.suspended")
        if st == "expired":
            return tr(L, "st.expired", date=i18n.fmt_date(L, exp))
        if exp is None:
            return tr(L, "st.open")
        return tr(L, "st.active", date=i18n.fmt_date(L, exp), n=days_left(m))

    def member_help(self, m, lang=None):
        L = lang or (self.lang(m["id"]) if m else "ru")
        have = self.members.effective_services(m) if m else []
        t = [tr(L, "help.member_head"), ""]
        t += [f"• <b>{esc(SERVICES[s]['name_' + L])}</b> — {esc(SERVICES[s]['desc_' + L])}" for s in have]
        t += ["", tr(L, "help.member_tail"), tr(L, "help.member_q", c=esc(self.contact())),
              tr(L, "help.lang_line", lang=tr(L, "btn.lang"))]
        return "\n".join(t)

    def member_start(self, cid, uid):
        m = self.members.get(uid)
        L = self.lang(uid)
        self.send_menu(cid, uid, tr(L, "member.start", name=esc(person(m)), status=self.status_line(m, L),
                                    svcs=esc(svc_names(self.members.effective_services(m), lang=L) or "—")))

    def paused(self, cid, uid, m):
        L = self.lang(uid)
        st = self.members.status(m)
        why = (tr(L, "member.why_susp") if st == "suspended"
               else tr(L, "member.why_exp", date=i18n.fmt_date(L, m["expires_ts"])))
        self.bot.send(cid, tr(L, "member.paused", why=why, c=esc(self.contact())),
                      reply_markup=self.keyboard(uid))

    def welcome(self, m):
        L = self.lang(m["id"])
        return tr(L, "member.welcome", name=esc(person(m)), status=self.status_line(m, L),
                  svcs=esc(svc_names(self.members.effective_services(m), lang=L) or "—"))

    # ------------------------------------------------------------------ codes --
    def redeem_code(self, cid, uid, frm, text):
        L = self.lang(uid, frm)
        if uid is not None and self.role(uid) is None:
            self.seen_from[uid] = frm
            if len(self.seen_from) > SEEN_MAX:
                self.seen_from.pop(next(iter(self.seen_from)))
        res, mem = self.members.redeem(uid, frm, text)
        c = esc(self.contact())
        if mem is None:
            key = "code." + res
            msg = tr(L, key, c=c) if i18n.has(key) else tr(L, "code.fail")
            return self.bot.send(cid, msg)
        if res == "new":
            self.send_menu(cid, uid, self.welcome(mem))
        else:
            until = i18n.fmt_date(L, mem["expires_ts"]) if mem["expires_ts"] is not None \
                else tr(L, "common.no_expiry")
            self.bot.send(cid, tr(L, "code.extended", until=until), reply_markup=self.keyboard(uid))
        if uid != self.owner_id():
            OL = self.olang()
            norm = norm_code(text)
            extra = ""
            for k in self.members.codes(include_dead=True):
                if norm_code(k["code"]) == norm:
                    extra = tr(OL, "code.owner_extra", days=k["days"], svcs=esc(svc_names(k["services"], " + ", OL)))
                    break
            self.notify_owner(tr(OL, "code.owner_note", who=ident(mem), code=esc(code_fmt(norm)), extra=extra))

    def redirect_to_member_bot(self, cid, text, lang=None):
        """The ops bot is for the owner only: point everyone else at the member bot,
        with a one-tap activation when they sent (or deep-linked) an access code."""
        L = lang or self.lang(cid)
        u = self.core.member_bot_username
        rows = [[{"text": tr(L, "redirect.open", u=u), "url": f"https://t.me/{u}"}]]
        if CODE_RE.match(text):
            code = code_fmt(norm_code(text))
            rows.append([{"text": tr(L, "redirect.activate", u=u), "url": f"https://t.me/{u}?start={code}"}])
        self.bot.send(cid, tr(L, "redirect.text", u=esc(u)),
                      reply_markup={"inline_keyboard": rows})

    def stranger(self, frm, cid):
        uid = frm.get("id")
        L = self.lang(uid, frm)
        name = from_name(frm)
        self.seen_from[uid] = frm
        if len(self.seen_from) > SEEN_MAX:
            self.seen_from.pop(next(iter(self.seen_from)))
        self.bot.send(cid, tr(L, "stranger.text", c=esc(self.contact())),
                      reply_markup=self.app_markup(tr(L, "menu.open_app")))
        if uid not in self.nagged and uid != self.owner_id():
            self.nagged.add(uid)
            OL = self.olang()
            un = f" @{frm['username']}" if frm.get("username") else ""
            self.notify_owner(tr(OL, "stranger.owner", name=esc(name), un=esc(un), uid=uid),
                              {"inline_keyboard": [[
                                  {"text": tr(OL, "btn.give30"), "callback_data": f"g:{uid}:30"},
                                  {"text": tr(OL, "btn.ignore"), "callback_data": f"g:{uid}:x"}]]})

    # --------------------------------------------------------- member features --
    def cmd_vpn(self, cid, uid):
        L = self.lang(uid)
        c = esc(self.contact())
        m = self.members.get(uid)
        if not m:
            return self.bot.send(cid, tr(L, "vpn.owner_none"))
        if not self.members.has(uid, "vpn"):
            return self.bot.send(cid, tr(L, "vpn.no_access", c=c))
        wait = None
        if not m["vpn_sub_id"]:
            if self.xui is None:
                return self.bot.send(cid, tr(L, "vpn.not_ready"))
            wait = self.bot.send(cid, tr(L, "vpn.preparing"))
            try:
                self.rec.sync_member(uid)               # already on a pool thread
            except Exception as e:                      # noqa: BLE001
                log.warning("sync_member %s: %s", uid, e)
            m = self.members.get(uid) or m
            if not m["vpn_sub_id"]:
                return self.bot.edit(cid, wait["message_id"], tr(L, "vpn.prep_fail", c=c))
        base = os.environ.get("VPN_SUB_BASE", "").strip() or f"https://{hub.base_domain()}:2096/sub/"
        url = base + m["vpn_sub_id"]
        if wait:
            self.bot.delete(cid, wait["message_id"])
        self.bot.send(cid, tr(L, "vpn.card", url=esc(url)),
                      reply_markup=self.app_markup(tr(L, "menu.open_app_in"), "vpn"), protect_content=True)

    def cmd_matrix(self, cid, uid):
        L = self.lang(uid)
        m = self.members.get(uid)
        if not m:
            return self.bot.send(cid, tr(L, "common.owner_nosub"))
        if not self.members.has(uid, "matrix"):
            return self.bot.send(cid, tr(L, "matrix.no_access", c=esc(self.contact())))
        server = os.environ.get("MATRIX_SERVER_NAME", "").strip() or hub.base_domain()
        accs = self.members.matrix_accounts(uid)
        t = [tr(L, "matrix.head"), ""]
        t += [tr(L, "matrix.accounts")] + [f"• <code>{esc(a['mxid'])}</code>" for a in accs] if accs \
            else [tr(L, "matrix.none")]
        t += ["", tr(L, "matrix.login", server=esc(server)), tr(L, "matrix.note")]
        self.bot.send(cid, "\n".join(t), reply_markup=self.app_markup(tr(L, "btn.create_account"), "matrix"))

    def cmd_sub(self, cid, uid):
        L = self.lang(uid)
        m = self.members.get(uid)
        if not m:
            return self.bot.send(cid, tr(L, "common.owner_nosub"))
        self.bot.send(cid, tr(L, "member.sub", status=self.status_line(m, L),
                              svcs=esc(svc_names(self.members.effective_services(m), lang=L) or "—")))

    # ---------------------------------------------------------- owner: services --
    def services_view(self, lang="ru"):
        L = lang
        state = self.members.services_state(L)
        t = [tr(L, "svc.head"), ""]
        kb = []
        for st in state:
            who = tr(L, "svc.all") if st["mode"] == "all" else tr(L, "svc.grant")
            mark = "✅" if st["enabled"] else "⬜"
            t.append(tr(L, "svc.line", mark=mark, name=esc(st["name"]), n=st["members_with_access"]))
            kb.append([{"text": f"{mark} {st['name']} — {who}", "callback_data": f"svc:t:{st['id']}"}])
        t += ["", tr(L, "svc.foot1"), tr(L, "svc.foot2")]
        return "\n".join(t), {"inline_keyboard": kb}

    def cmd_services(self, cid):
        text, kb = self.services_view(self.lang(cid))
        self.bot.send(cid, text, reply_markup=kb)

    def cb_services(self, cid, mid, rest):
        L = self.lang(cid)
        act, _, svc = rest.partition(":")
        if act != "t" or svc not in SERVICES:
            return None
        on = not self.members.service_enabled(svc)
        self.members.set_service_enabled(svc, on)
        self._edit(cid, mid, *self.services_view(L))
        return tr(L, "svc.toast", name=SERVICES[svc]["name_" + L], state=tr(L, "svc.on" if on else "svc.off"))

    # ------------------------------------------------------------ owner: codes --
    def code_card(self, code, days, svcs, lang="ru"):
        L = lang
        t = [tr(L, "card.code", code=esc(code))]
        rows = []
        if self.share_username():
            link = f"https://t.me/{self.share_username()}?start={code}"
            t.append(tr(L, "card.link", link=esc(link)))
            rows.append([{"text": tr(L, "btn.share"), "url": "https://t.me/share/url?url=" + quote(link, safe="")
                          + "&text=" + quote(tr(L, "card.share_text"), safe="")}])
        t.append(tr(L, "card.gives", days=days, svcs=esc(svc_names(svcs, lang=L))))
        rows.append([{"text": tr(L, "btn.revoke"), "callback_data": f"c:rv:{code}"}])
        return "\n".join(t), {"inline_keyboard": rows}

    def cmd_code(self, cid, arg):
        L = self.lang(cid)
        days, svcs = 30, []
        for tok in arg.replace(";", " ").split():
            if tok.isdigit():
                days = int(tok)
                continue
            svcs += [p for p in re.split(r"[,+\s]+", tok.lower()) if p]
        if not 1 <= days <= 3650 or any(s not in GRANTABLE for s in svcs):
            return self.bot.send(cid, tr(L, "code.usage"))
        svcs = [s for s in GRANTABLE if s in svcs] or list(DEFAULT_SERVICES)
        code = self.members.new_code(days, svcs)
        text, kb = self.code_card(code, days, svcs, L)
        self.bot.send(cid, text, reply_markup=kb)

    def wiz_gc(self):
        now = time.time()
        self.wiz = {k: v for k, v in self.wiz.items() if now - v["ts"] < WIZ_TTL}

    def wiz_view(self, st, lang="ru"):
        L = lang
        row1 = [{"text": ("✅ " if s in st["svcs"] else "⬜ ") + short(s, L), "callback_data": f"c:t:{s}"}
                for s in GRANTABLE]
        row2 = [{"text": ("✅ " if d == st["days"] else "") + tr(L, "common.days", n=d), "callback_data": f"c:d:{d}"}
                for d in WIZ_DAYS]
        text = tr(L, "wiz.view", svcs=esc(svc_names(st["svcs"], lang=L) or tr(L, "wiz.nothing")), days=st["days"])
        return text, {"inline_keyboard": [row1, row2, [{"text": tr(L, "btn.create"), "callback_data": "c:mk"}]]}

    def cmd_code_wizard(self, cid):
        self.wiz_gc()
        st = {"svcs": set(DEFAULT_SERVICES), "days": 30, "ts": time.time()}
        text, kb = self.wiz_view(st, self.lang(cid))
        msg = self.bot.send(cid, text, reply_markup=kb)
        if msg:
            self.wiz[msg["message_id"]] = st

    def cb_code(self, cid, mid, rest):
        L = self.lang(cid)
        act, _, arg = rest.partition(":")
        if act == "rv":
            ok = self.members.revoke_code(arg)
            self._edit(cid, mid, tr(L, "wiz.revoked", c=esc(arg)) if ok else tr(L, "wiz.not_found"))
            return (tr(L, "wiz.t_revoked") if ok else tr(L, "common.t_not_found")), not ok
        self.wiz_gc()
        st = self.wiz.get(mid)
        if st is None:
            self._edit(cid, mid, tr(L, "wiz.stale"))
            return tr(L, "wiz.t_stale"), True
        if act == "t" and arg in GRANTABLE:
            st["svcs"] ^= {arg}
        elif act == "d" and arg.isdigit() and int(arg) in WIZ_DAYS:
            st["days"] = int(arg)
        elif act == "mk":
            if not st["svcs"]:
                return tr(L, "wiz.pick_one"), True
            svcs = [s for s in GRANTABLE if s in st["svcs"]]
            code = self.members.new_code(st["days"], svcs)
            self.wiz.pop(mid, None)
            text, kb = self.code_card(code, st["days"], svcs, L)
            self._edit(cid, mid, text, kb)
            return tr(L, "wiz.created"), False
        else:
            return None
        st["ts"] = time.time()
        self._edit(cid, mid, *self.wiz_view(st, L))
        return None

    # --------------------------------------------------------- owner: members --
    @staticmethod
    def dot(members, m):
        st = members.status(m)
        if st == "suspended":
            return "⏸"
        if st == "expired":
            return "⚪"
        left = days_left(m)
        return "🟡" if left is not None and left <= 3 else "🟢"

    def members_view(self, page=0, lang="ru"):
        L = lang
        rows = self.members.list()
        pages = max(1, math.ceil(len(rows) / PAGE))
        page = min(max(page, 0), pages - 1)
        c = self.members.counts()
        head = tr(L, "mem.head", members=c["members"], active=c["active"], expired=c["expired"],
                  suspended=c["suspended"], exp7=c["expiring_7d"])
        if not rows:
            head += "\n\n" + tr(L, "mem.empty")
        kb = []
        for m in rows[page * PAGE:(page + 1) * PAGE]:
            left = days_left(m)
            kb.append([{"text": f"{self.dot(self.members, m)} {person(m)[:24]} · "
                                f"{'∞' if left is None else tr(L, 'common.days', n=left)}",
                        "callback_data": f"m:open:{m['id']}"}])
        if pages > 1:
            kb.append([{"text": "‹", "callback_data": f"m:pg:{max(page - 1, 0)}"},
                       {"text": f"{page + 1}/{pages}", "callback_data": f"m:pg:{page}"},
                       {"text": "›", "callback_data": f"m:pg:{min(page + 1, pages - 1)}"}])
        return head, {"inline_keyboard": kb}

    def cmd_members(self, cid):
        text, kb = self.members_view(0, self.lang(cid))
        self.bot.send(cid, text, reply_markup=kb)

    def member_card(self, uid, lang="ru"):
        L = lang
        back = {"inline_keyboard": [[{"text": tr(L, "common.back"), "callback_data": "m:pg:0"}]]}
        m = self.members.get(uid)
        if not m:
            return tr(L, "mem.not_found"), back
        v = self.members.view(m)
        st = self.members.status(m)
        exp = m["expires_ts"]
        t = [f"👤 <b>{esc(person(m))}</b>" + (f" @{esc(m['username'])}" if m["username"] else ""),
             f"ID: <code>{uid}</code>",
             tr(L, "card.status", dot=self.dot(self.members, m), word=tr(L, "st.word." + st)),
             tr(L, "card.until", v=tr(L, "common.no_expiry") if exp is None
                else tr(L, "card.until_val", date=i18n.fmt_date(L, exp), n=days_left(m))),
             tr(L, "card.services", v=esc(svc_names(m["services"], lang=L) or "—")),
             "Matrix: " + (", ".join(f"<code>{esc(x)}</code>" for x in v["matrix"]) or "—"),
             tr(L, "card.vpn", v=tr(L, "common.yes" if v["has_vpn_client"] else "common.no")),
             tr(L, "card.seen", v=datetime.fromtimestamp(m["last_seen_ts"]).strftime("%d.%m %H:%M")
                if m["last_seen_ts"] else "—")]
        if m["note"]:
            t.append(tr(L, "card.note", v=esc(m["note"])))
        kb = [[{"text": tr(L, "btn.ext30"), "callback_data": f"m:ext:{uid}:30"},
               {"text": tr(L, "btn.ext90"), "callback_data": f"m:ext:{uid}:90"}],
              [{"text": tr(L, "btn.resume"), "callback_data": f"m:res:{uid}"} if st == "suspended"
               else {"text": tr(L, "btn.suspend"), "callback_data": f"m:sus:{uid}"}],
              [{"text": ("✅ " if s in m["services"] else "⬜ ") + short(s, L), "callback_data": f"m:svc:{uid}:{s}"}
               for s in GRANTABLE]]
        last = [{"text": tr(L, "btn.delete"), "callback_data": f"m:del:{uid}"}]
        if self.webapp_url:
            last.insert(0, {"text": tr(L, "btn.inapp"), "web_app": {"url": self.app_url("admin")}})
        kb += [last, back["inline_keyboard"][0]]
        return "\n".join(t), {"inline_keyboard": kb}

    def cb_member(self, cid, mid, rest):
        L = self.lang(cid)
        back = {"inline_keyboard": [[{"text": tr(L, "common.back"), "callback_data": "m:pg:0"}]]}
        act, _, arg = rest.partition(":")
        if act == "pg":
            self._edit(cid, mid, *self.members_view(int(arg) if arg.isdigit() else 0, L))
            return None
        suid, _, extra = arg.partition(":")
        if not suid.isdigit():
            return tr(L, "cb.bad_data"), True
        uid = int(suid)
        m = self.members.get(uid)
        if not m:
            self._edit(cid, mid, tr(L, "mem.not_found"), back)
            return tr(L, "common.t_not_found"), True
        toast = None
        if act == "open":
            pass
        elif act == "ext" and extra in ("30", "90"):
            m = self.members.extend(uid, int(extra))
            UL = self.lang(uid)
            self.notify(uid, tr(UL, "n.extended", date=i18n.fmt_date(UL, m["expires_ts"])))
            toast = tr(L, "mem.toast_ext", n=extra)
        elif act == "sus":
            self.members.suspend(uid)
            self.notify(uid, tr(self.lang(uid), "n.suspended_bot", c=esc(self.contact())))
            toast = tr(L, "mem.toast_susp")
        elif act == "res":
            self.members.resume(uid)
            self.notify(uid, tr(self.lang(uid), "n.resumed_bot"))
            toast = tr(L, "mem.toast_res")
        elif act == "svc" and extra in GRANTABLE:
            self.members.set_services(uid, [s for s in GRANTABLE if (s in m["services"]) != (s == extra)])
            toast = tr(L, "mem.toast_svc")
        elif act == "del":
            self._edit(cid, mid, tr(L, "mem.del_confirm", name=esc(person(m)), uid=uid),
                       {"inline_keyboard": [[{"text": tr(L, "btn.del_yes"), "callback_data": f"m:delok:{uid}"},
                                             {"text": tr(L, "common.cancel"), "callback_data": f"m:open:{uid}"}]]})
            return None
        elif act == "delok":
            self.members.delete(uid)
            self._edit(cid, mid, tr(L, "mem.deleted", name=esc(person(m))), back)
            return tr(L, "mem.toast_del")
        else:
            return None
        self._edit(cid, mid, *self.member_card(uid, L))
        return toast

    def cmd_allow(self, cid, cmd, arg):
        L = self.lang(cid)
        parts = arg.split()
        if not parts or not parts[0].lstrip("-").isdigit() or (len(parts) > 1 and not parts[1].isdigit()):
            return self.bot.send(cid, tr(L, "allow.usage", cmd=cmd,
                                         days=tr(L, "allow.days_opt") if cmd == "/allow" else ""))
        uid = int(parts[0])
        if cmd == "/deny":
            if not self.members.get(uid):
                return self.bot.send(cid, tr(L, "allow.no_member"))
            self.members.suspend(uid)
            self.notify(uid, tr(self.lang(uid), "n.suspended_bot", c=esc(self.contact())))
            return self.bot.send(cid, tr(L, "allow.denied", uid=uid))
        days = int(parts[1]) if len(parts) > 1 else 30
        mem = self.members.grant(uid, self.seen_from.get(uid), days)
        self.notify_welcome(mem)
        self.bot.send(cid, tr(L, "allow.ok", ident=ident(mem), days=days,
                              date=i18n.fmt_date(L, mem["expires_ts"])))

    def notify_welcome(self, mem):
        """Welcome a freshly granted member (never raises), from the bot they use."""
        try:
            self.front_for(mem["id"]).send_menu(mem["id"], mem["id"], self.welcome(mem))
        except TelegramError as e:
            log.info("welcome %s: %s", mem["id"], e)

    # ----------------------------------------------------- owner: access notices --
    def cb_grant(self, cid, mid, rest):
        L = self.lang(cid)
        suid, _, act = rest.partition(":")
        if not suid.lstrip("-").isdigit():
            return tr(L, "cb.bad_data"), True
        uid = int(suid)
        if act != "30":
            self._edit(cid, mid, tr(L, "grant.ignored", uid=uid))
            return tr(L, "grant.ok_toast")
        frm = self.seen_from.get(uid)
        mem = self.members.grant(uid, frm, 30)
        name = person(mem) if frm is None else from_name(frm)
        self._edit(cid, mid, tr(L, "grant.done", name=esc(name), uid=uid, date=i18n.fmt_date(L, mem["expires_ts"])))
        self.notify_welcome(mem)
        return tr(L, "grant.t_done")

    def cb_inbound(self, cid, mid, rest):
        L = self.lang(cid)
        act, _, arg = rest.partition(":")
        if not arg.lstrip("-").isdigit():
            return tr(L, "cb.bad_data"), True
        iid = int(arg)
        if act == "skip":
            self._edit(cid, mid, tr(L, "inb.skipped"))
            return tr(L, "inb.t_skipped")
        if act != "add":
            return None
        if self.xui is None:
            self._edit(cid, mid, tr(L, "inb.no_xui"))
            return tr(L, "inb.t_no_xui"), True
        by_id = {i["id"]: i for i in self.xui.inbounds()}
        cur = self.members.member_inbounds()
        if iid not in by_id:
            self._edit(cid, mid, tr(L, "inb.gone"))
            return tr(L, "common.t_not_found"), True
        if cur is None:
            self._edit(cid, mid, tr(L, "inb.not_init"))
            return tr(L, "inb.t_later"), True
        if iid in cur:
            self._edit(cid, mid, tr(L, "inb.already", r=esc(by_id[iid].get("remark", iid))))
            return tr(L, "inb.t_already")
        proto = str(by_id[iid].get("protocol", "")).lower()
        if proto in AWG_PROTOCOLS and any(str((by_id.get(i) or {}).get("protocol", "")).lower() in AWG_PROTOCOLS
                                          for i in cur):
            self._edit(cid, mid, tr(L, "inb.two_awg"))
            return tr(L, "inb.t_two_awg"), True
        self.members.set_member_inbounds(cur + [iid])
        self.members.set_known_inbounds(set(self.members.known_inbounds()) | {iid})
        self._edit(cid, mid, tr(L, "inb.shared", r=esc(by_id[iid].get("remark", iid))))
        return tr(L, "inb.t_shared")

    # -------------------------------------------------------------- features --
    def cmd_health(self, cid):
        L = self.lang(cid)
        msg = self.bot.send(cid, tr(L, "health.checking"))
        f, i = health.full(self.store)
        self.bot.edit(cid, msg["message_id"], health.render(f, i, L))

    def cmd_links(self, cid):
        L = self.lang(cid)
        svcs = hub.services()
        lines = []
        h = hub.hub_url()
        if h:
            lines.append(f"🏠 <b><a href=\"{esc(h)}\">{tr(L, 'links.hub')}</a></b>\n{esc(h)}\n")
        for s in svcs:
            if s.get("status", "live") != "live":
                continue
            lines.append(f"• <a href=\"{esc(s['url'])}\">{esc(s.get('name', s['id']))}</a> — "
                         f"{esc(s.get('description', ''))}")
        if not svcs:
            lines.append(tr(L, "links.none"))
        buttons = ([[{"text": tr(L, "btn.open_hub"), "url": h}]] if h else []) + [
            [{"text": s.get("name", s["id"]), "url": s["url"]} for s in svcs[i:i + 2]]
            for i in range(0, len(svcs), 2)]
        self.bot.send(cid, "\n".join(lines), reply_markup={"inline_keyboard": buttons} if buttons else None)

    def yt_ok(self, uid, role=None):
        """The downloader: the owner always (in the ops bot), a member while `youtube` is on."""
        role = role or self.role(uid)
        return role == "owner" or (role == "member" and self.members.has(uid, "youtube"))

    def cmd_download(self, cid, uid, role, cmd, arg, text):
        L = self.lang(uid)
        if not self.yt_ok(uid, role):
            now = time.time()
            if now - self.yt_nag.get(uid, 0) >= YT_NAG_EVERY:
                self.yt_nag[uid] = now
                self.bot.send(cid, tr(L, "dl.off"))
            return None
        if btn_key(text) == "yt":
            return self.bot.send(cid, tr(L, "dl.paste"))
        url = URL_RE.search(arg if cmd == "/yt" else text)
        if not url:
            return self.bot.send(cid, tr(L, "dl.usage"))
        return self.offer_download(cid, uid, url.group(0))

    def offer_download(self, cid, uid, url):
        """The downloader lives in the Mini App: hand the (canonical) link over, nothing else."""
        L = self.lang(uid)
        if not self.webapp_url:
            return self.bot.send(cid, tr(L, "dl.moved"))
        link = self.webapp_url + "?p=downloads&url=" + quote(downloads.canonical_url(url, keep_list=True), safe="")
        self.bot.send(cid, tr(L, "dl.offer"),
                      reply_markup={"inline_keyboard": [[{"text": tr(L, "btn.open_dl"), "web_app": {"url": link}}]]})

    # ------------------------------------------------------------ passwords --
    def cmd_pass(self, cid):
        L = self.lang(cid)
        v = hub.vault(self.store)
        if not v:
            return self.bot.send(cid, tr(L, "pass.none"))
        ids = sorted(v, key=lambda k: hub.service_name(k).lower())
        kb = [[{"text": f"🔑 {hub.service_name(k)}", "callback_data": f"pw:{k}"} for k in ids[i:i + 2]]
              for i in range(0, len(ids), 2)]
        known = {s["id"] for s in hub.services()}
        missing = [s.get("name", s["id"]) for s in hub.services() if s["id"] not in v]
        t = tr(L, "pass.pick")
        if missing:
            t += "\n<i>" + tr(L, "pass.missing", v=esc(", ".join(missing))) + "</i>"
        if known:
            t += "\n<i>" + tr(L, "pass.ids", v=esc(", ".join(sorted(known)))) + "</i>"
        self.bot.send(cid, t, reply_markup={"inline_keyboard": kb})

    def show_password(self, cid, vid):
        L = self.lang(cid)
        e = hub.vault(self.store).get(vid)
        if not e:
            return self.bot.send(cid, tr(L, "pass.nologin"))
        url = hub.service_url(vid)
        t = [f"🔑 <b>{esc(hub.service_name(vid))}</b>",
             tr(L, "pass.login", v=esc(e["login"])),
             tr(L, "pass.password", v=esc(e["password"]))]
        if url:
            t.append(esc(url))
        if e.get("note"):
            t.append(f"<i>{esc(e['note'])}</i>")
        t.append(tr(L, "pass.ttl", ttl=PASS_TTL))
        log.info("password shown for %s", vid)
        msg = self.bot.send(cid, "\n".join(t), protect_content=True)
        threading.Timer(PASS_TTL, self.bot.delete, args=(cid, msg["message_id"])).start()

    def cmd_setpass(self, cid, mid, arg):
        L = self.lang(cid)
        # The command itself contains the password: delete it right away.
        self.bot.delete(cid, mid)
        parts = arg.split(maxsplit=2)
        if len(parts) < 3 or not SERVICE_ID.match(parts[0].lower()):
            return self.bot.send(cid, tr(L, "setpass.usage"))
        vid, login, pw = parts[0].lower(), parts[1], parts[2].strip()
        self.store.vault_set(vid, login, pw)
        self.bot.send(cid, tr(L, "setpass.ok", name=esc(hub.service_name(vid))))

    def cmd_delpass(self, cid, vid):
        L = self.lang(cid)
        if self.store.vault_del(vid):
            return self.bot.send(cid, tr(L, "delpass.ok", vid=esc(vid)))
        if vid in hub.vault(self.store):
            return self.bot.send(cid, tr(L, "delpass.env"))
        self.bot.send(cid, tr(L, "pass.nologin"))


    # ------------------------------------------------------------- callbacks --
    def on_callback(self, q):
        frm = q.get("from") or {}
        uid = frm.get("id")
        data = q.get("data") or ""
        m = q.get("message") or {}
        cid, mid = (m.get("chat") or {}).get("id"), m.get("message_id")
        L = self.lang(uid, frm)
        kind, _, rest = data.partition(":")
        if kind == "lang":                                  # everybody may pick a language
            return self.cb_lang(q, frm, cid, rest)
        role = self.role(uid)
        if role is None:
            return self.bot.answer(q["id"], tr(L, "cb.no_access"), alert=True)
        if role == "member":
            self.members.touch(uid, frm, "callback")

        # ----- owner only below: checked again here, per press, because a
        # callback carries the presser's id, not the original recipient's.
        if role != "owner":
            return self.bot.answer(q["id"], tr(L, "cb.owner_only"), alert=True)
        if kind == "pw":
            if (m.get("chat") or {}).get("type") != "private":
                return self.bot.answer(q["id"], tr(L, "cb.private_only"), alert=True)
            self.bot.answer(q["id"])
            return self.show_password(cid, rest)
        handler = {"m": self.cb_member, "c": self.cb_code, "g": self.cb_grant, "inb": self.cb_inbound,
                   "svc": self.cb_services}.get(kind)
        if handler is None:
            return self.bot.answer(q["id"], tr(L, "cb.stale"), alert=True)
        try:
            res = handler(cid, mid, rest)
        except Exception:                                   # noqa: BLE001
            log.exception("callback %s failed", kind)
            return self.bot.answer(q["id"], tr(L, "cb.error"), alert=True)
        text, alert = res if isinstance(res, tuple) else (res, False)
        self.bot.answer(q["id"], text, alert=alert)



class MemberFront:
    """The member bot's side of the shared Helper.

    The handlers are the Helper's own methods, re-bound to this object so that
    `self.bot` is the member bot and `self.kind` is "member"; everything else
    (store, members, xui, ...) is read from -- and assigned to -- the Helper, so a
    hot-swapped 3x-ui client or a new nag map is seen by both bots."""
    _OWN = ("core", "bot", "kind", "bot_username")

    def __init__(self, core, bot, username):
        object.__setattr__(self, "core", core)
        object.__setattr__(self, "bot", bot)
        object.__setattr__(self, "kind", "member")
        object.__setattr__(self, "bot_username", username)

    def __getattr__(self, name):
        value = getattr(self.core, name)
        func = getattr(value, "__func__", None)
        if func is not None and getattr(value, "__self__", None) is self.core:
            return types.MethodType(func, self)
        return value

    def __setattr__(self, name, value):
        if name in self._OWN:
            object.__setattr__(self, name, value)
        else:
            setattr(self.core, name, value)


class Monitor:
    """Background checks, alerting the owner on change."""
    FAST, SLOW, CERTS = 60, 300, 6 * 3600

    def __init__(self, helper):
        self.h = helper
        self.bad = {}          # key -> consecutive bad runs
        self.alerted = {}      # key -> text of the alert sent
        self.last = {"fast": 0, "slow": 0, "certs": 0}

    def notify(self, text):
        """`text` is a string, or a callable building it in the owner's language."""
        oid = self.h.owner_id()
        if not oid:
            return
        try:
            self.h.bot.send(oid, text(self.h.lang(oid)) if callable(text) else text)
        except TelegramError as e:
            log.warning("notify: %s", e)

    def process(self, findings, scope_prefixes):
        """Debounce: a problem must be seen twice in a row before it alerts;
        a cleared problem sends one "resolved"."""
        seen = set()
        for f in findings:
            if f.level == "event":
                self.notify(lambda L, f=f: f"⚠️ {esc(f.t(L))}")
                continue
            seen.add(f.key)
            if f.level in ("warn", "crit"):
                self.bad[f.key] = self.bad.get(f.key, 0) + 1
                if self.bad[f.key] >= 2 and self.alerted.get(f.key) != f.level:
                    self.alerted[f.key] = f.level
                    self.notify(lambda L, f=f: f"{'🔴' if f.level == 'crit' else '⚠️'} {esc(f.t(L))}")
            else:
                self.bad.pop(f.key, None)
                if f.key in self.alerted:
                    self.alerted.pop(f.key)
                    self.notify(lambda L, f=f: tr(L, "mon.resolved", text=esc(f.t(L))))
        # A key that stopped being reported at all (a container removed on
        # purpose) is dropped quietly.
        for k in [k for k in list(self.bad) + list(self.alerted)
                  if k.startswith(scope_prefixes) and k not in seen]:
            self.bad.pop(k, None)
            self.alerted.pop(k, None)

    def run(self):
        time.sleep(20)
        last_report = self.h.store.get("last_report")
        while True:
            now = time.time()
            try:
                if now - self.last["fast"] >= self.FAST:
                    self.last["fast"] = now
                    f1, _ = health.check_system(self.h.store)
                    f2, _ = health.check_host()
                    f3, _ = health.check_containers()
                    self.process(f1, ("disk:", "ram", "cpu:", "gpu", "stats"))
                    self.process(f2, ("tcp",))
                    self.process(f3, ("ct:", "docker"))
                if now - self.last["slow"] >= self.SLOW:
                    self.last["slow"] = now
                    certs = now - self.last["certs"] >= self.CERTS
                    if certs:
                        self.last["certs"] = now
                    f4, _ = health.check_tls_routes(with_certs=certs)
                    f5, _ = health.check_dns()
                    self.process(f4, ("route:", "tls:") + (("cert:",) if certs else ()))
                    self.process(f5, ("dns", "ip"))
                if REPORT_HOUR.isdigit():
                    today = datetime.now().strftime("%Y-%m-%d")
                    if datetime.now().hour == int(REPORT_HOUR) and last_report != today:
                        last_report = today
                        self.h.store.set("last_report", today)
                        f, i = health.full(self.h.store)
                        self.notify(lambda L: tr(L, "mon.report") + "\n\n" + health.render(f, i, L))
            except Exception:                                  # noqa: BLE001
                log.exception("monitor pass failed")
            time.sleep(10)


def main():
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    Helper(Store(os.environ.get("HELPER_DB", "/data/helper.db"))).run()


if __name__ == "__main__":
    main()
