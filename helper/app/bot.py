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
import secrets
import threading
import time
import types
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from urllib.parse import quote

import health
import hub
import tokens
import webapp
import ytdl
from members import (AWG_PROTOCOLS, DEFAULT_SERVICES, SERVICES, Members, Reconciler, ask_owner, norm_code,
                     owner_contact, ru_date)
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

B_HEALTH, B_LINKS, B_YT, B_PASS = "🩺 Здоровье", "🔗 Ссылки", "🎬 YouTube", "🔑 Пароли"
B_VPN, B_MX, B_SUB, B_HELP = "🔐 VPN", "💬 Мессенджер", "🗓 Подписка", "❓ Помощь"
B_MEMBERS, B_CODE, B_APP = "👥 Участники", "🎟 Код", "📱 MeowHub"
URL_RE = re.compile(r"https?://\S+")
SERVICE_ID = re.compile(r"^[a-z0-9_-]{1,32}$")
CODE_RE = re.compile(r"(?i)^\s*meow[\s-]*[a-z0-9]{4}[\s-]*[a-z0-9]{4}\s*$")
OWNER_CMDS = ("/pass", "/setpass", "/delpass", "/users", "/members", "/code", "/allow", "/deny")
SHORT = {"vpn": "VPN", "matrix": "Мессенджер", "tools": "Инструменты"}
WIZ_DAYS = (30, 90, 180, 365)
SEEN_MAX = 500


def esc(s):
    return health.esc(s)


def code_fmt(norm):
    """Bare code -> MEOW-XXXX-XXXX."""
    return f"{norm[:4]}-{norm[4:8]}-{norm[8:]}" if len(norm) == 12 else norm


def svc_names(svcs, sep=", "):
    return sep.join(SERVICES[s]["name_ru"] for s in SERVICES if s in (svcs or []))


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
        self.pending_urls = {}            # token -> (url, user id, ts)
        self.nagged = set()               # strangers already reported to the owner
        self.seen_from = {}               # stranger uid -> Telegram `from` (for the grant welcome)
        self.wiz = {}                     # message id -> code wizard state
        self.members = Members(store)
        self.xui = tokens.xui_from(store)
        self.syn = Synapse.from_env(lambda: store.get("matrix_admin_token"),
                                    lambda t: store.set("matrix_admin_token", t))
        self.rec = Reconciler(self.members, self.xui, self.syn, send=self.notify, owner_id=self.owner_id)
        self.bot_username = ""
        base = os.environ.get("BASE_DOMAIN", "").strip()
        path = os.environ.get("BOT_APP_PATH", "").strip().strip("/")
        self.webapp_url = f"https://{base}/{path}/" if base and path else ""

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

    def keyboard(self, uid):
        role = self.role(uid)
        app = [[{"text": B_APP, "web_app": {"url": self.app_url()}}]] if self.webapp_url else []
        if role == "owner":
            rows = [[{"text": B_HEALTH}, {"text": B_LINKS}, {"text": B_PASS}], [{"text": B_YT}],
                    [{"text": B_MEMBERS}, {"text": B_CODE}]] + app
        elif role == "member":
            m = self.members.get(uid)
            if self.members.status(m) != "active":
                rows = app + [[{"text": B_HELP}]]
            else:
                rows = list(app)
                svc = [{"text": t} for s, t in (("vpn", B_VPN), ("matrix", B_MX)) if s in m["services"]]
                if svc:
                    rows.append(svc)
                rows.append([{"text": B_SUB}, {"text": B_HELP}])
                if "tools" in m["services"]:
                    rows.append([{"text": B_HEALTH}, {"text": B_LINKS}, {"text": B_YT}])
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
        self.bot.send(cid, text, reply_markup=self.app_markup("📱 Открыть MeowHub", page))
        self.bot.send(cid, "👇 Меню — кнопками внизу.", reply_markup=self.keyboard(uid))

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

    def _commands(self):
        base = [{"command": "start", "description": "Начало"},
                {"command": "help", "description": "Помощь"},
                {"command": "vpn", "description": "Мой VPN"},
                {"command": "matrix", "description": "Мессенджер"},
                {"command": "sub", "description": "Моя подписка"}]
        if self.kind == "member":
            try:
                self.bot.call("setMyCommands", commands=base)
            except TelegramError as e:
                log.warning("member bot commands: %s", e)
            return
        two = self.core.member_bot is not None
        self.bot.call("setMyCommands", commands=base[:2] if two else base)
        oid = self.owner_id()
        if oid:
            try:
                self.bot.call("setMyCommands", scope={"type": "chat", "chat_id": oid}, commands=base + [
                    {"command": "health", "description": "Состояние сервера"},
                    {"command": "links", "description": "Ссылки хаба"},
                    {"command": "yt", "description": "Скачать видео: /yt <ссылка>"},
                    {"command": "code", "description": "/code [дней] [vpn,matrix,tools]"},
                    {"command": "members", "description": "Участники"},
                    {"command": "pass", "description": "Пароли к сервисам"},
                    {"command": "setpass", "description": "/setpass <сервис> <логин> <пароль>"},
                    {"command": "delpass", "description": "/delpass <сервис>"},
                ])
            except TelegramError as e:
                log.info("owner commands: %s (has the owner pressed /start yet?)", e)

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
        role = self.role(uid)
        if role == "member":
            self.members.touch(uid, frm, "message")

        cmd, _, arg = text.partition(" ")
        cmd = cmd.split("@")[0].lower()
        if role is None and self.kind == "helper" and self.core.member_bot is not None:
            return self.redirect_to_member_bot(cid, arg if cmd == "/start" and CODE_RE.match(arg) else text)
        # An access code works from anyone, also as the /start deep-link payload.
        if cmd == "/start" and CODE_RE.match(arg):
            return self.redeem_code(cid, uid, frm, arg)
        if CODE_RE.match(text):
            return self.redeem_code(cid, uid, frm, text)
        if role is None:
            return self.stranger(frm, cid)

        if role == "member":
            mem = self.members.get(uid)
            if self.members.status(mem) != "active":
                if cmd == "/help" or text == B_HELP:
                    return self.bot.send(cid, self.member_help(mem), reply_markup=self.keyboard(uid))
                return self.paused(cid, uid, mem)

        # ----- features shared by owner and members (gated per service) -----
        if cmd == "/start":
            if role == "owner":
                return self.bot.send(cid, self.help_text(role), reply_markup=self.keyboard(uid))
            return self.member_start(cid, uid)
        if cmd == "/help" or text == B_HELP:
            help_ = self.help_text(role) if role == "owner" else self.member_help(self.members.get(uid))
            return self.bot.send(cid, help_, reply_markup=self.keyboard(uid))
        if cmd == "/vpn" or text == B_VPN:
            return self.cmd_vpn(cid, uid)
        if cmd == "/matrix" or text == B_MX:
            return self.cmd_matrix(cid, uid)
        if cmd == "/sub" or text == B_SUB:
            return self.cmd_sub(cid, uid)

        tools = cmd in ("/health", "/links", "/yt") or text in (B_HEALTH, B_LINKS, B_YT) or URL_RE.search(text)
        if tools and not self.tools_ok(uid, role):
            return self.bot.send(cid, "⛔ Эта функция не входит в твою подписку.")
        if cmd == "/health" or text == B_HEALTH:
            return self.cmd_health(cid)
        if cmd == "/links" or text == B_LINKS:
            return self.cmd_links(cid)
        if text == B_YT:
            return self.bot.send(cid, "Пришли ссылку на видео (YouTube и почти любой другой сайт) — "
                                      "я предложу качество и пришлю файл или ссылку на него.")
        if cmd == "/yt" or URL_RE.search(text):
            url = URL_RE.search(arg if cmd == "/yt" else text)
            if not url:
                return self.bot.send(cid, "Формат: <code>/yt https://youtu.be/…</code>")
            return self.offer_download(cid, uid, url.group(0))

        if role != "owner":
            if cmd in OWNER_CMDS or text in (B_PASS, B_MEMBERS, B_CODE):
                return self.bot.send(cid, "⛔ Эта функция доступна только владельцу.")
            return self.member_start(cid, uid)

        # ----- owner only below -----
        if cmd == "/pass" or text == B_PASS:
            return self.cmd_pass(cid)
        if cmd == "/setpass":
            return self.cmd_setpass(cid, m["message_id"], arg)
        if cmd == "/delpass":
            return self.cmd_delpass(cid, arg.strip().lower())
        if cmd in ("/members", "/users") or text == B_MEMBERS:
            return self.cmd_members(cid)
        if cmd == "/code":
            return self.cmd_code(cid, arg)
        if text == B_CODE:
            return self.cmd_code_wizard(cid)
        if cmd in ("/allow", "/deny"):
            return self.cmd_allow(cid, cmd, arg)
        return self.bot.send(cid, self.help_text(role), reply_markup=self.keyboard(uid))

    # ------------------------------------------------------------------ texts --
    def help_text(self, role):
        t = ["🐱 <b>MeowHub помощник</b>", "",
             f"{B_HEALTH} — состояние сервера: диски, контейнеры, сайты, сертификаты, DNS",
             f"{B_LINKS} — хаб и все сервисы",
             f"{B_YT} — пришли ссылку на видео, и я его скачаю"]
        if role == "owner":
            t += [f"{B_PASS} — логины к сервисам (только тебе, сообщение исчезает через {PASS_TTL} с)",
                  f"{B_MEMBERS} / <code>/members</code> — участники: продлить, приостановить, сервисы",
                  f"{B_CODE} — мастер кода приглашения",
                  "", "<b>Участники</b>",
                  "<code>/code [дней] [vpn,matrix,tools]</code> — код приглашения (по умолчанию 30 дней, VPN + Мессенджер)",
                  "Участник присылает код боту (или открывает ссылку-приглашение) — и получает доступ. "
                  "Когда срок кончается, сервисы встают на паузу, аккаунты остаются.",
                  "<code>/allow ID [дней]</code> — выдать доступ без кода, <code>/deny ID</code> — приостановить",
                  f"{B_APP} — Mini App: ссылки, VPN, мессенджер и админка",
                  "", "<b>Пароли</b>",
                  "<code>/setpass сервис логин пароль</code> — сохранить логин",
                  "<code>/delpass сервис</code> — удалить",
                  "", "Проблемы с сервером я присылаю сам, сразу как замечу."]
        return "\n".join(t)

    def status_line(self, m):
        st = self.members.status(m)
        exp = m.get("expires_ts")
        if st == "suspended":
            return "⏸ приостановлена владельцем"
        if st == "expired":
            return f"⚪ закончилась {ru_date(exp)}"
        if exp is None:
            return "🟢 активна, без срока"
        return f"🟢 активна до {ru_date(exp)} (осталось {days_left(m)} д)"

    def member_help(self, m):
        have = [s for s in SERVICES if m and s in m["services"]]
        t = ["🐱 <b>MeowHub</b> — закрытый сервис по приглашениям.", ""]
        t += [f"• <b>{esc(SERVICES[s]['name_ru'])}</b> — {esc(SERVICES[s]['desc_ru'])}" for s in have]
        t += ["", "Когда подписка заканчивается, сервисы приостанавливаются, а аккаунты и настройки "
                  "сохраняются. Чтобы продолжить, пришли новый код сюда.",
              f"Вопросы: {esc(self.contact())}" if self.contact() else "Вопросы — к владельцу."]
        return "\n".join(t)

    def member_start(self, cid, uid):
        m = self.members.get(uid)
        t = [f"🐱 <b>MeowHub</b> — привет, {esc(person(m))}!", "",
             f"Подписка: {self.status_line(m)}",
             f"Сервисы: {esc(svc_names(m['services']) or '—')}", "",
             "Меню — кнопками внизу."]
        self.send_menu(cid, uid, "\n".join(t))

    def paused(self, cid, uid, m):
        st = self.members.status(m)
        why = ("владелец приостановил доступ" if st == "suspended"
               else f"подписка закончилась {ru_date(m['expires_ts'])}")
        self.bot.send(cid, f"⏸ <b>Доступ приостановлен</b>\nПричина: {why}.\n"
                           f"Аккаунты и настройки сохранены. Пришли новый код или {ask_owner()}.",
                      reply_markup=self.keyboard(uid))

    def welcome(self, m):
        return (f"🎉 <b>Добро пожаловать в MeowHub, {esc(person(m))}!</b>\n\n"
                f"Подписка: {self.status_line(m)}\n"
                f"Сервисы: {esc(svc_names(m['services']) or '—')}\n\n"
                "Меню — кнопками внизу, а всё в одном месте — в приложении.")

    # ------------------------------------------------------------------ codes --
    def redeem_code(self, cid, uid, frm, text):
        if uid is not None and self.role(uid) is None:
            self.seen_from[uid] = frm
            if len(self.seen_from) > SEEN_MAX:
                self.seen_from.pop(next(iter(self.seen_from)))
        res, mem = self.members.redeem(uid, frm, text)
        c = self.contact()
        msgs = {"invalid": "❌ Такого кода нет. Проверь, что он набран без ошибок.",
                "used": "❌ Этот код уже использован.",
                "expired_code": f"❌ Срок действия кода истёк. Попроси новый: {esc(c)}." if c else "❌ Срок действия кода истёк. Попроси новый у владельца.",
                "revoked_code": "❌ Этот код отозван.",
                "rate_limited": "⏳ Слишком много неверных попыток. Попробуй через час.",
                "suspended": f"⏸ Доступ приостановлен владельцем — код не поможет. {ask_owner(True)}."}
        if mem is None:
            return self.bot.send(cid, msgs.get(res, "❌ Не удалось применить код."))
        if res == "new":
            self.send_menu(cid, uid, self.welcome(mem))
        else:
            until = ru_date(mem["expires_ts"]) if mem["expires_ts"] is not None else "без срока"
            self.bot.send(cid, f"✅ Подписка продлена до {until}.", reply_markup=self.keyboard(uid))
        if uid != self.owner_id():
            norm = norm_code(text)
            extra = ""
            for k in self.members.codes(include_dead=True):
                if norm_code(k["code"]) == norm:
                    extra = f": +{k['days']} д, {esc(svc_names(k['services'], ' + '))}"
                    break
            self.notify_owner(f"🎉 {ident(mem)} активировал(а) код <code>{esc(code_fmt(norm))}</code>{extra}")

    def redirect_to_member_bot(self, cid, text):
        """The ops bot is for the owner only: point everyone else at the member bot,
        with a one-tap activation when they sent (or deep-linked) an access code."""
        u = self.core.member_bot_username
        rows = [[{"text": f"Открыть @{u}", "url": f"https://t.me/{u}"}]]
        if CODE_RE.match(text):
            code = code_fmt(norm_code(text))
            rows.append([{"text": f"Активировать в @{u}", "url": f"https://t.me/{u}?start={code}"}])
        self.bot.send(cid, f"🔒 Это служебный бот. MeowHub — в @{esc(u)}.",
                      reply_markup={"inline_keyboard": rows})

    def stranger(self, frm, cid):
        uid = frm.get("id")
        name = from_name(frm)
        self.seen_from[uid] = frm
        if len(self.seen_from) > SEEN_MAX:
            self.seen_from.pop(next(iter(self.seen_from)))
        self.bot.send(cid, "🔒 MeowHub — закрытый сервис.\n"
                           "Если у тебя есть код приглашения — просто пришли его сюда.\n"
                           f"Нет кода? {ask_owner(True)}.",
                      reply_markup=self.app_markup("📱 Открыть MeowHub"))
        if uid not in self.nagged and uid != self.owner_id():
            self.nagged.add(uid)
            un = f" @{frm['username']}" if frm.get("username") else ""
            self.notify_owner(f"👤 Боту пишет {esc(name)}{esc(un)} (ID <code>{uid}</code>).",
                              {"inline_keyboard": [[
                                  {"text": "✅ Дать доступ на 30 дней", "callback_data": f"g:{uid}:30"},
                                  {"text": "Игнор", "callback_data": f"g:{uid}:x"}]]})

    # --------------------------------------------------------- member features --
    def cmd_vpn(self, cid, uid):
        m = self.members.get(uid)
        if not m:
            return self.bot.send(cid, "У владельца нет подписки — конфиги в панели 3x-ui.")
        if not self.members.has(uid, "vpn"):
            return self.bot.send(cid, "🔐 VPN не входит в твою подписку или она не активна. "
                                      f"{ask_owner(True)}.")
        wait = None
        if not m["vpn_sub_id"]:
            if self.xui is None:
                return self.bot.send(cid, "🔐 VPN пока не настроен на сервере. Попробуй позже.")
            wait = self.bot.send(cid, "⏳ Готовлю конфиги…")
            try:
                self.rec.sync_member(uid)               # already on a pool thread
            except Exception as e:                      # noqa: BLE001
                log.warning("sync_member %s: %s", uid, e)
            m = self.members.get(uid) or m
            if not m["vpn_sub_id"]:
                return self.bot.edit(cid, wait["message_id"], "❌ Не получилось подготовить конфиги. "
                                     f"Попробуй позже или {ask_owner()}.")
        base = os.environ.get("VPN_SUB_BASE", "").strip() or f"https://{hub.base_domain()}:2096/sub/"
        url = base + m["vpn_sub_id"]
        if wait:
            self.bot.delete(cid, wait["message_id"])
        self.bot.send(cid, "🔐 <b>Твой VPN</b>\n\nСсылка-подписка:\n"
                           f"<code>{esc(url)}</code>\n\n"
                           "1. Установи Happ, v2RayTun или Hiddify.\n"
                           "2. Добавь эту ссылку в приложении как подписку.\n"
                           "3. Конфиги обновляются сами, новые серверы появятся автоматически. "
                           "Никому не передавай ссылку.",
                      reply_markup=self.app_markup("📱 Открыть в MeowHub", "vpn"), protect_content=True)

    def cmd_matrix(self, cid, uid):
        m = self.members.get(uid)
        if not m:
            return self.bot.send(cid, "У владельца нет подписки.")
        if not self.members.has(uid, "matrix"):
            return self.bot.send(cid, "💬 Мессенджер не входит в твою подписку или она не активна. "
                                      f"{ask_owner(True)}.")
        server = os.environ.get("MATRIX_SERVER_NAME", "").strip() or hub.base_domain()
        accs = self.members.matrix_accounts(uid)
        t = ["💬 <b>Мессенджер</b>", ""]
        t += ["Твои аккаунты:"] + [f"• <code>{esc(a['mxid'])}</code>" for a in accs] if accs \
            else ["Аккаунтов пока нет."]
        t += ["", f"Вход: приложение Element X → сервер <code>{esc(server)}</code> → логин и пароль.",
              "Аккаунт создаётся в приложении MeowHub — так пароль не остаётся в чате."]
        self.bot.send(cid, "\n".join(t), reply_markup=self.app_markup("➕ Создать аккаунт", "matrix"))

    def cmd_sub(self, cid, uid):
        m = self.members.get(uid)
        if not m:
            return self.bot.send(cid, "У владельца нет подписки.")
        self.bot.send(cid, "🗓 <b>Подписка</b>\n\n"
                           f"Статус: {self.status_line(m)}\n"
                           f"Сервисы: {esc(svc_names(m['services']) or '—')}\n\n"
                           "Чтобы продлить — пришли новый код.")

    # ------------------------------------------------------------ owner: codes --
    def code_card(self, code, days, svcs):
        t = [f"🎟 Код: <code>{esc(code)}</code>"]
        rows = []
        if self.share_username():
            link = f"https://t.me/{self.share_username()}?start={code}"
            t.append(f"Ссылка: {esc(link)}")
            rows.append([{"text": "📤 Поделиться", "url": "https://t.me/share/url?url=" + quote(link, safe="")
                          + "&text=" + quote("Приглашение в MeowHub", safe="")}])
        t.append(f"Даёт {days} д: {esc(svc_names(svcs))}. Действует 30 дней, одноразовый.")
        rows.append([{"text": "🚫 Отозвать", "callback_data": f"c:rv:{code}"}])
        return "\n".join(t), {"inline_keyboard": rows}

    def cmd_code(self, cid, arg):
        days, svcs = 30, []
        for tok in arg.replace(";", " ").split():
            if tok.isdigit():
                days = int(tok)
                continue
            svcs += [p for p in re.split(r"[,+\s]+", tok.lower()) if p]
        if not 1 <= days <= 3650 or any(s not in SERVICES for s in svcs):
            return self.bot.send(cid, "Формат: <code>/code [дней] [vpn,matrix,tools]</code>\n"
                                      "Например: <code>/code 90 vpn</code>")
        svcs = [s for s in SERVICES if s in svcs] or list(DEFAULT_SERVICES)
        code = self.members.new_code(days, svcs)
        text, kb = self.code_card(code, days, svcs)
        self.bot.send(cid, text, reply_markup=kb)

    def wiz_gc(self):
        now = time.time()
        self.wiz = {k: v for k, v in self.wiz.items() if now - v["ts"] < WIZ_TTL}

    def wiz_view(self, st):
        row1 = [{"text": ("✅ " if s in st["svcs"] else "⬜ ") + SHORT[s], "callback_data": f"c:t:{s}"}
                for s in SERVICES]
        row2 = [{"text": ("✅ " if d == st["days"] else "") + f"{d} д", "callback_data": f"c:d:{d}"}
                for d in WIZ_DAYS]
        text = ("🎟 <b>Новый код</b>\n\nВыбери сервисы и срок подписки, потом «Создать».\n"
                f"Сейчас: {esc(svc_names(st['svcs']) or 'ничего')} · {st['days']} д")
        return text, {"inline_keyboard": [row1, row2, [{"text": "🎟 Создать", "callback_data": "c:mk"}]]}

    def cmd_code_wizard(self, cid):
        self.wiz_gc()
        st = {"svcs": set(DEFAULT_SERVICES), "days": 30, "ts": time.time()}
        text, kb = self.wiz_view(st)
        msg = self.bot.send(cid, text, reply_markup=kb)
        if msg:
            self.wiz[msg["message_id"]] = st

    def cb_code(self, cid, mid, rest):
        act, _, arg = rest.partition(":")
        if act == "rv":
            ok = self.members.revoke_code(arg)
            self._edit(cid, mid, f"🚫 Код <code>{esc(arg)}</code> отозван." if ok else "Код не найден.")
            return ("Отозван" if ok else "Не найден"), not ok
        self.wiz_gc()
        st = self.wiz.get(mid)
        if st is None:
            self._edit(cid, mid, "Мастер устарел — нажми 🎟 Код ещё раз.")
            return "Мастер устарел", True
        if act == "t" and arg in SERVICES:
            st["svcs"] ^= {arg}
        elif act == "d" and arg.isdigit() and int(arg) in WIZ_DAYS:
            st["days"] = int(arg)
        elif act == "mk":
            if not st["svcs"]:
                return "Выбери хотя бы один сервис", True
            svcs = [s for s in SERVICES if s in st["svcs"]]
            code = self.members.new_code(st["days"], svcs)
            self.wiz.pop(mid, None)
            text, kb = self.code_card(code, st["days"], svcs)
            self._edit(cid, mid, text, kb)
            return "Код создан", False
        else:
            return None
        st["ts"] = time.time()
        self._edit(cid, mid, *self.wiz_view(st))
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

    def members_view(self, page=0):
        rows = self.members.list()
        pages = max(1, math.ceil(len(rows) / PAGE))
        page = min(max(page, 0), pages - 1)
        c = self.members.counts()
        head = (f"👥 <b>Участники</b>: {c['members']} · 🟢 {c['active']} активных · "
                f"⚪ {c['expired']} истекло · ⏸ {c['suspended']} на паузе · ⏳ {c['expiring_7d']} истекают за 7 дн")
        if not rows:
            head += "\n\nПока никого нет. 🎟 Код — создать приглашение."
        kb = []
        for m in rows[page * PAGE:(page + 1) * PAGE]:
            left = days_left(m)
            kb.append([{"text": f"{self.dot(self.members, m)} {person(m)[:24]} · "
                                f"{'∞' if left is None else f'{left} д'}",
                        "callback_data": f"m:open:{m['id']}"}])
        if pages > 1:
            kb.append([{"text": "‹", "callback_data": f"m:pg:{max(page - 1, 0)}"},
                       {"text": f"{page + 1}/{pages}", "callback_data": f"m:pg:{page}"},
                       {"text": "›", "callback_data": f"m:pg:{min(page + 1, pages - 1)}"}])
        return head, {"inline_keyboard": kb}

    def cmd_members(self, cid):
        text, kb = self.members_view(0)
        self.bot.send(cid, text, reply_markup=kb)

    def member_card(self, uid):
        m = self.members.get(uid)
        if not m:
            return "Участник не найден.", {"inline_keyboard": [[{"text": "« Назад", "callback_data": "m:pg:0"}]]}
        v = self.members.view(m)
        st = self.members.status(m)
        exp = m["expires_ts"]
        t = [f"👤 <b>{esc(person(m))}</b>" + (f" @{esc(m['username'])}" if m["username"] else ""),
             f"ID: <code>{uid}</code>",
             f"Статус: {self.dot(self.members, m)} " + {"active": "активен", "expired": "истёк",
                                                         "suspended": "на паузе"}[st],
             "До: " + ("без срока" if exp is None else f"{ru_date(exp)} ({days_left(m)} д)"),
             f"Сервисы: {esc(svc_names(m['services']) or '—')}",
             "Matrix: " + (", ".join(f"<code>{esc(x)}</code>" for x in v["matrix"]) or "—"),
             f"VPN-клиент: {'да' if v['has_vpn_client'] else 'нет'}",
             "Был: " + (datetime.fromtimestamp(m["last_seen_ts"]).strftime("%d.%m %H:%M")
                        if m["last_seen_ts"] else "—")]
        if m["note"]:
            t.append(f"Заметка: {esc(m['note'])}")
        kb = [[{"text": "➕ 30 д", "callback_data": f"m:ext:{uid}:30"},
               {"text": "➕ 90 д", "callback_data": f"m:ext:{uid}:90"}],
              [{"text": "▶️ Возобновить", "callback_data": f"m:res:{uid}"} if st == "suspended"
               else {"text": "⏸ Приостановить", "callback_data": f"m:sus:{uid}"}],
              [{"text": ("✅ " if s in m["services"] else "⬜ ") + SHORT[s], "callback_data": f"m:svc:{uid}:{s}"}
               for s in SERVICES]]
        last = [{"text": "🗑 Удалить", "callback_data": f"m:del:{uid}"}]
        if self.webapp_url:
            last.insert(0, {"text": "📱 В приложении", "web_app": {"url": self.app_url("admin")}})
        kb += [last, [{"text": "« Назад", "callback_data": "m:pg:0"}]]
        return "\n".join(t), {"inline_keyboard": kb}

    def cb_member(self, cid, mid, rest):
        act, _, arg = rest.partition(":")
        if act == "pg":
            self._edit(cid, mid, *self.members_view(int(arg) if arg.isdigit() else 0))
            return None
        suid, _, extra = arg.partition(":")
        if not suid.isdigit():
            return "Неверные данные", True
        uid = int(suid)
        m = self.members.get(uid)
        if not m:
            self._edit(cid, mid, "Участник не найден.",
                       {"inline_keyboard": [[{"text": "« Назад", "callback_data": "m:pg:0"}]]})
            return "Не найден", True
        toast = None
        if act == "open":
            pass
        elif act == "ext" and extra in ("30", "90"):
            m = self.members.extend(uid, int(extra))
            self.notify(uid, f"✅ Подписка продлена до {ru_date(m['expires_ts'])}.")
            toast = f"Продлено на {extra} д"
        elif act == "sus":
            self.members.suspend(uid)
            self.notify(uid, "⏸ Доступ к MeowHub приостановлен владельцем. "
                             f"{ask_owner(True)}.")
            toast = "Приостановлен"
        elif act == "res":
            self.members.resume(uid)
            self.notify(uid, "▶️ Доступ к MeowHub восстановлен.")
            toast = "Возобновлён"
        elif act == "svc" and extra in SERVICES:
            self.members.set_services(uid, [s for s in SERVICES if (s in m["services"]) != (s == extra)])
            toast = "Сервисы обновлены"
        elif act == "del":
            self._edit(cid, mid, f"🗑 Удалить {esc(person(m))} (<code>{uid}</code>)? VPN-клиент будет удалён, "
                                 "Matrix-аккаунты останутся заблокированными.",
                       {"inline_keyboard": [[{"text": "🗑 Да, удалить", "callback_data": f"m:delok:{uid}"},
                                             {"text": "Отмена", "callback_data": f"m:open:{uid}"}]]})
            return None
        elif act == "delok":
            self.members.delete(uid)
            self._edit(cid, mid, f"🗑 {esc(person(m))} удалён.",
                       {"inline_keyboard": [[{"text": "« Назад", "callback_data": "m:pg:0"}]]})
            return "Удалён"
        else:
            return None
        self._edit(cid, mid, *self.member_card(uid))
        return toast

    def cmd_allow(self, cid, cmd, arg):
        parts = arg.split()
        if not parts or not parts[0].lstrip("-").isdigit() or (len(parts) > 1 and not parts[1].isdigit()):
            return self.bot.send(cid, f"Формат: <code>{cmd} 123456789{' [дней]' if cmd == '/allow' else ''}</code>")
        uid = int(parts[0])
        if cmd == "/deny":
            if not self.members.get(uid):
                return self.bot.send(cid, "Такого участника нет.")
            self.members.suspend(uid)
            self.notify(uid, f"⏸ Доступ к MeowHub приостановлен владельцем. {ask_owner(True)}.")
            return self.bot.send(cid, f"⏸ {uid} приостановлен. Вернуть: 👥 Участники.")
        days = int(parts[1]) if len(parts) > 1 else 30
        mem = self.members.grant(uid, self.seen_from.get(uid), days)
        self.notify_welcome(mem)
        self.bot.send(cid, f"✅ {ident(mem)} — доступ на {days} д, до "
                           f"{ru_date(mem['expires_ts'])}.")

    def notify_welcome(self, mem):
        """Welcome a freshly granted member (never raises), from the bot they use."""
        try:
            self.front_for(mem["id"]).send_menu(mem["id"], mem["id"], self.welcome(mem))
        except TelegramError as e:
            log.info("welcome %s: %s", mem["id"], e)

    # ----------------------------------------------------- owner: access notices --
    def cb_grant(self, cid, mid, rest):
        suid, _, act = rest.partition(":")
        if not suid.lstrip("-").isdigit():
            return "Неверные данные", True
        uid = int(suid)
        if act != "30":
            self._edit(cid, mid, f"Игнор: <code>{uid}</code>.")
            return "Ок"
        frm = self.seen_from.get(uid)
        mem = self.members.grant(uid, frm, 30)
        name = person(mem) if frm is None else from_name(frm)
        self._edit(cid, mid, f"✅ Доступ выдан {esc(name)} (<code>{uid}</code>) на 30 дней, "
                             f"до {ru_date(mem['expires_ts'])}.")
        self.notify_welcome(mem)
        return "Доступ выдан"

    def cb_inbound(self, cid, mid, rest):
        act, _, arg = rest.partition(":")
        if not arg.lstrip("-").isdigit():
            return "Неверные данные", True
        iid = int(arg)
        if act == "skip":
            self._edit(cid, mid, "Пропущено.")
            return "Пропущено"
        if act != "add":
            return None
        if self.xui is None:
            self._edit(cid, mid, "⛔ 3x-ui не настроен.")
            return "3x-ui не настроен", True
        by_id = {i["id"]: i for i in self.xui.inbounds()}
        cur = self.members.member_inbounds()
        if iid not in by_id:
            self._edit(cid, mid, "⛔ Такого инбаунда уже нет.")
            return "Не найден", True
        if cur is None:
            self._edit(cid, mid, "⏳ Список инбаундов ещё не инициализирован — повтори через минуту.")
            return "Повтори позже", True
        if iid in cur:
            self._edit(cid, mid, f"Инбаунд <b>{esc(by_id[iid].get('remark', iid))}</b> уже выдан.")
            return "Уже выдан"
        proto = str(by_id[iid].get("protocol", "")).lower()
        if proto in AWG_PROTOCOLS and any(str((by_id.get(i) or {}).get("protocol", "")).lower() in AWG_PROTOCOLS
                                          for i in cur):
            self._edit(cid, mid, "⛔ Нельзя: у участников уже есть один wireguard/amneziawg инбаунд — "
                                 "на двух клиент получает неверный адрес.")
            return "Второй WireGuard/AWG нельзя", True
        self.members.set_member_inbounds(cur + [iid])
        self.members.set_known_inbounds(set(self.members.known_inbounds()) | {iid})
        self._edit(cid, mid, f"✅ Инбаунд <b>{esc(by_id[iid].get('remark', iid))}</b> выдан участникам.")
        return "Выдан"

    # -------------------------------------------------------------- features --
    def cmd_health(self, cid):
        msg = self.bot.send(cid, "🩺 Проверяю…")
        f, i = health.full(self.store)
        self.bot.edit(cid, msg["message_id"], health.render(f, i))

    def cmd_links(self, cid):
        svcs = hub.services()
        lines = []
        h = hub.hub_url()
        if h:
            lines.append(f"🏠 <b><a href=\"{esc(h)}\">Хаб</a></b>\n{esc(h)}\n")
        for s in svcs:
            if s.get("status", "live") != "live":
                continue
            lines.append(f"• <a href=\"{esc(s['url'])}\">{esc(s.get('name', s['id']))}</a> — "
                         f"{esc(s.get('description', ''))}")
        if not svcs:
            lines.append("Список сервисов недоступен — пересобери dashboard (npm run build).")
        buttons = ([[{"text": "🏠 Открыть хаб", "url": h}]] if h else []) + [
            [{"text": s.get("name", s["id"]), "url": s["url"]} for s in svcs[i:i + 2]]
            for i in range(0, len(svcs), 2)]
        self.bot.send(cid, "\n".join(lines), reply_markup={"inline_keyboard": buttons} if buttons else None)

    def offer_download(self, cid, uid, url):
        tok = secrets.token_hex(4)
        now = time.time()
        self.pending_urls = {k: v for k, v in self.pending_urls.items() if now - v[2] < 3600}
        self.pending_urls[tok] = (url, uid, now)
        kb = [[{"text": "🎬 1080p", "callback_data": f"yt:{tok}:v1080"},
               {"text": "🎬 Лучшее", "callback_data": f"yt:{tok}:vbest"},
               {"text": "🎵 MP3", "callback_data": f"yt:{tok}:a"}],
              [{"text": "✖ Отмена", "callback_data": f"yt:{tok}:x"}]]
        self.bot.send(cid, f"Скачать?\n{esc(url)}", reply_markup={"inline_keyboard": kb})

    def run_download(self, cid, mid, url, preset):
        label = ytdl.PRESETS[preset]["label"]
        self.bot.edit(cid, mid, f"⏳ Ставлю в очередь ({label})…\n{esc(url)}")
        try:
            t0 = ytdl.add(url, preset)
        except Exception as e:                              # noqa: BLE001
            return self.bot.edit(cid, mid, f"❌ MeTube: {esc(e)}")
        last = [0.0]

        def progress(items):
            if time.time() - last[0] < 10:
                return
            last[0] = time.time()
            parts = []
            for i in items[:5]:
                pct = i.get("percent")
                st = f"{pct:.0f}%" if isinstance(pct, (int, float)) else (i.get("status") or "")
                parts.append(f"⏳ {esc(i.get('title') or url)} — {st}")
            try:
                self.bot.edit(cid, mid, "\n".join(parts))
            except TelegramError:
                pass

        try:
            items = ytdl.follow(t0, url, progress)
        except Exception as e:                              # noqa: BLE001
            return self.bot.edit(cid, mid, f"❌ {esc(e)}")
        out = []
        for i in items[:10]:
            title = esc(i.get("title") or "?")
            if i.get("status") != "finished":
                out.append(f"❌ {title}: {esc(i.get('msg') or i.get('error') or 'ошибка')}")
                continue
            ln = ytdl.link(i)
            size = i.get("size") or 0
            out.append(f"✅ <a href=\"{esc(ln)}\">{title}</a> · {size / 1e6:.0f} MB")
            p = ytdl.local_file(i)
            if p and os.path.getsize(p) <= ytdl.UPLOAD_MAX:
                audio = i.get("download_type") == "audio"
                try:
                    self.bot.upload("sendAudio" if audio else "sendVideo", "audio" if audio else "video",
                                    p, chat_id=cid, caption=i.get("title", "")[:1000],
                                    supports_streaming=None if audio else "true")
                except TelegramError as e:
                    log.info("upload failed: %s", e)
        out.append(f"<i>Файлы удаляются с сервера через {ytdl.TTL_H} ч.</i>")
        self.bot.edit(cid, mid, "\n".join(out))

    # ------------------------------------------------------------ passwords --
    def cmd_pass(self, cid):
        v = hub.vault(self.store)
        if not v:
            return self.bot.send(cid, "Сохранённых логинов нет.\n<code>/setpass сервис логин пароль</code>")
        ids = sorted(v, key=lambda k: hub.service_name(k).lower())
        kb = [[{"text": f"🔑 {hub.service_name(k)}", "callback_data": f"pw:{k}"} for k in ids[i:i + 2]]
              for i in range(0, len(ids), 2)]
        known = {s["id"] for s in hub.services()}
        missing = [s.get("name", s["id"]) for s in hub.services() if s["id"] not in v]
        t = "Выбери сервис:"
        if missing:
            t += "\n<i>Без сохранённого логина: " + esc(", ".join(missing)) + "</i>"
        if known:
            t += "\n<i>ID для /setpass: " + esc(", ".join(sorted(known))) + "</i>"
        self.bot.send(cid, t, reply_markup={"inline_keyboard": kb})

    def show_password(self, cid, vid):
        e = hub.vault(self.store).get(vid)
        if not e:
            return self.bot.send(cid, "Нет такого логина.")
        url = hub.service_url(vid)
        t = [f"🔑 <b>{esc(hub.service_name(vid))}</b>",
             f"Логин: <code>{esc(e['login'])}</code>",
             f"Пароль: <tg-spoiler><code>{esc(e['password'])}</code></tg-spoiler>"]
        if url:
            t.append(esc(url))
        if e.get("note"):
            t.append(f"<i>{esc(e['note'])}</i>")
        t.append(f"<i>Сообщение удалится через {PASS_TTL} с.</i>")
        log.info("password shown for %s", vid)
        msg = self.bot.send(cid, "\n".join(t), protect_content=True)
        threading.Timer(PASS_TTL, self.bot.delete, args=(cid, msg["message_id"])).start()

    def cmd_setpass(self, cid, mid, arg):
        # The command itself contains the password: delete it right away.
        self.bot.delete(cid, mid)
        parts = arg.split(maxsplit=2)
        if len(parts) < 3 or not SERVICE_ID.match(parts[0].lower()):
            return self.bot.send(cid, "Формат: <code>/setpass сервис логин пароль</code>\n"
                                      "Сообщение с паролем я удалил.")
        vid, login, pw = parts[0].lower(), parts[1], parts[2].strip()
        self.store.vault_set(vid, login, pw)
        self.bot.send(cid, f"✅ Сохранил логин для <b>{esc(hub.service_name(vid))}</b>. "
                           "Твоё сообщение с паролем удалено.")

    def cmd_delpass(self, cid, vid):
        if self.store.vault_del(vid):
            return self.bot.send(cid, f"🗑 Удалил {esc(vid)}.")
        if vid in hub.vault(self.store):
            return self.bot.send(cid, "Этот логин приходит из .env — удалить можно только там.")
        self.bot.send(cid, "Нет такого логина.")


    # ------------------------------------------------------------- callbacks --
    def on_callback(self, q):
        frm = q.get("from") or {}
        uid = frm.get("id")
        data = q.get("data") or ""
        m = q.get("message") or {}
        cid, mid = (m.get("chat") or {}).get("id"), m.get("message_id")
        role = self.role(uid)
        if role is None:
            return self.bot.answer(q["id"], "Нет доступа", alert=True)
        if role == "member":
            self.members.touch(uid, frm, "callback")
        kind, _, rest = data.partition(":")

        if kind == "yt":
            tok, _, preset = rest.partition(":")
            p = self.pending_urls.get(tok)
            if not self.tools_ok(uid, role) or (p and p[1] != uid):
                return self.bot.answer(q["id"], "Недоступно", alert=True)
            self.pending_urls.pop(tok, None)
            self.bot.answer(q["id"])
            if preset == "x" or not p:
                return self.bot.edit(cid, mid, "Отменено." if preset == "x" else "Ссылка устарела, пришли ещё раз.")
            return self.run_download(cid, mid, p[0], preset)

        # ----- owner only below: checked again here, per press, because a
        # callback carries the presser's id, not the original recipient's.
        if role != "owner":
            return self.bot.answer(q["id"], "Только для владельца", alert=True)
        if kind == "pw":
            if (m.get("chat") or {}).get("type") != "private":
                return self.bot.answer(q["id"], "Только в личном чате", alert=True)
            self.bot.answer(q["id"])
            return self.show_password(cid, rest)
        handler = {"m": self.cb_member, "c": self.cb_code, "g": self.cb_grant, "inb": self.cb_inbound}.get(kind)
        if handler is None:
            return self.bot.answer(q["id"], "Кнопка устарела", alert=True)
        try:
            res = handler(cid, mid, rest)
        except Exception:                                   # noqa: BLE001
            log.exception("callback %s failed", kind)
            return self.bot.answer(q["id"], "Ошибка", alert=True)
        text, alert = res if isinstance(res, tuple) else (res, False)
        self.bot.answer(q["id"], text, alert=alert)



class MemberFront:
    """The member bot's side of the shared Helper.

    The handlers are the Helper's own methods, re-bound to this object so that
    `self.bot` is the member bot and `self.kind` is "member"; everything else
    (store, members, xui, ...) is read from -- and assigned to -- the Helper, so a
    hot-swapped 3x-ui client or a new pending-download map is seen by both bots."""
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
        oid = self.h.owner_id()
        if not oid:
            return
        try:
            self.h.bot.send(oid, text)
        except TelegramError as e:
            log.warning("notify: %s", e)

    def process(self, findings, scope_prefixes):
        """Debounce: a problem must be seen twice in a row before it alerts;
        a cleared problem sends one "resolved"."""
        seen = set()
        for f in findings:
            if f.level == "event":
                self.notify(f"⚠️ {esc(f.text)}")
                continue
            seen.add(f.key)
            if f.level in ("warn", "crit"):
                self.bad[f.key] = self.bad.get(f.key, 0) + 1
                if self.bad[f.key] >= 2 and self.alerted.get(f.key) != f.level:
                    self.alerted[f.key] = f.level
                    self.notify(f"{'🔴' if f.level == 'crit' else '⚠️'} {esc(f.text)}")
            else:
                self.bad.pop(f.key, None)
                if f.key in self.alerted:
                    self.alerted.pop(f.key)
                    self.notify(f"✅ Решено: {esc(f.text)}")
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
                        self.notify("☀️ <b>Утренний отчёт</b>\n\n" + health.render(f, i))
            except Exception:                                  # noqa: BLE001
                log.exception("monitor pass failed")
            time.sleep(10)


def main():
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    Helper(Store(os.environ.get("HELPER_DB", "/data/helper.db"))).run()


if __name__ == "__main__":
    main()
