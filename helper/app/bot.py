"""MeowHub helper bot: server health, hub links, YouTube downloads and --
for the owner only -- the logins for the hub's services.

Access:
  owner   HELPER_OWNER_ID (or set with ctl.py). Everything, including
          passwords and user management.
  users   added by the owner (/allow <id> or the button on an access
          request). Health, links, downloads. Never passwords.
  others  told they have no access and shown their ID; the owner gets one
          notice with an "allow" button.

Long polling, not a webhook: nothing new listens on the internet.
"""
import logging
import os
import re
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import health
import hub
import ytdl
from store import Store
from tg import Bot, TelegramError

log = logging.getLogger("bot")

PASS_TTL = int(os.environ.get("HELPER_PASS_TTL", "60"))     # seconds a password stays visible
REPORT_HOUR = os.environ.get("HELPER_REPORT_HOUR", "9").strip()
HEARTBEAT = "/tmp/heartbeat"

B_HEALTH, B_LINKS, B_YT, B_PASS = "🩺 Здоровье", "🔗 Ссылки", "🎬 YouTube", "🔑 Пароли"
URL_RE = re.compile(r"https?://\S+")
SERVICE_ID = re.compile(r"^[a-z0-9_-]{1,32}$")


def esc(s):
    return health.esc(s)


class Helper:
    def __init__(self, store):
        self.store = store
        self.bot = None
        self.pool = ThreadPoolExecutor(8)
        self.pending_urls = {}            # token -> (url, user id, ts)
        self.nagged = set()               # unknown users already reported to the owner

    # ---------------------------------------------------------------- access --
    def owner_id(self):
        v = os.environ.get("HELPER_OWNER_ID", "").strip() or self.store.get("owner_id")
        return int(v) if v.lstrip("-").isdigit() else None

    def role(self, uid):
        if uid is not None and uid == self.owner_id():
            return "owner"
        if uid is not None and self.store.is_user(uid):
            return "user"
        return None

    def keyboard(self, role):
        rows = [[{"text": B_HEALTH}, {"text": B_LINKS}], [{"text": B_YT}]]
        if role == "owner":
            rows[1].append({"text": B_PASS})
        return {"keyboard": rows, "resize_keyboard": True, "is_persistent": True}

    # ------------------------------------------------------------------ loop --
    def run(self):
        while True:
            token = self.store.get("tg_token")
            if not token:
                log.warning("no bot token yet -- run: docker compose exec -it helper python3 /app/app/ctl.py token")
                self._beat()
                time.sleep(30)
                continue
            self.bot = Bot(token)
            try:
                me = self.bot.call("getMe")
                log.info("running as @%s", me.get("username"))
                self._commands()
                break
            except TelegramError as e:
                log.error("token rejected: %s", e)
                self._beat()
                time.sleep(60)
        threading.Thread(target=Monitor(self).run, daemon=True, name="monitor").start()
        offset = int(self.store.get("offset", "0") or 0)
        while True:
            self._beat()
            try:
                ups = self.bot.call("getUpdates", _timeout=70, offset=offset or None,
                                    allowed_updates=["message", "callback_query"], timeout=50)
            except TelegramError as e:
                log.warning("getUpdates: %s", e)
                time.sleep(5)
                continue
            for u in ups:
                offset = u["update_id"] + 1
                self.pool.submit(self._safe, u)
            if ups:
                self.store.set("offset", offset)

    def _beat(self):
        try:
            with open(HEARTBEAT, "w") as f:
                f.write(str(int(time.time())))
        except OSError:
            pass

    def _commands(self):
        base = [{"command": "health", "description": "Состояние сервера"},
                {"command": "links", "description": "Ссылки хаба"},
                {"command": "yt", "description": "Скачать видео: /yt <ссылка>"}]
        self.bot.call("setMyCommands", commands=base)
        oid = self.owner_id()
        if oid:
            try:
                self.bot.call("setMyCommands", scope={"type": "chat", "chat_id": oid}, commands=base + [
                    {"command": "pass", "description": "Пароли к сервисам"},
                    {"command": "setpass", "description": "/setpass <сервис> <логин> <пароль>"},
                    {"command": "delpass", "description": "/delpass <сервис>"},
                    {"command": "users", "description": "Пользователи бота"},
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
        role = self.role(uid)
        if chat.get("type") != "private":
            return                                       # never answer in groups
        if role is None:
            return self.stranger(frm, cid)

        cmd, _, arg = text.partition(" ")
        cmd = cmd.split("@")[0].lower()
        if cmd in ("/start", "/help"):
            return self.bot.send(cid, self.help_text(role), reply_markup=self.keyboard(role))
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
            if cmd in ("/pass", "/setpass", "/delpass", "/users", "/allow", "/deny") or text == B_PASS:
                return self.bot.send(cid, "⛔ Эта функция доступна только владельцу.")
            return self.bot.send(cid, self.help_text(role), reply_markup=self.keyboard(role))

        # ----- owner only below -----
        if cmd == "/pass" or text == B_PASS:
            return self.cmd_pass(cid)
        if cmd == "/setpass":
            return self.cmd_setpass(cid, m["message_id"], arg)
        if cmd == "/delpass":
            return self.cmd_delpass(cid, arg.strip().lower())
        if cmd == "/users":
            return self.cmd_users(cid)
        if cmd in ("/allow", "/deny"):
            a = arg.strip()
            if not a.lstrip("-").isdigit():
                return self.bot.send(cid, f"Формат: <code>{cmd} 123456789</code>")
            if cmd == "/allow":
                self.store.allow(int(a))
                return self.bot.send(cid, f"✅ {a} добавлен. Пароли ему недоступны.")
            self.store.deny(int(a))
            return self.bot.send(cid, f"🚫 {a} удалён.")
        return self.bot.send(cid, self.help_text(role), reply_markup=self.keyboard(role))

    def help_text(self, role):
        t = ["🐱 <b>MeowHub помощник</b>", "",
             f"{B_HEALTH} — состояние сервера: диски, контейнеры, сайты, сертификаты, DNS",
             f"{B_LINKS} — хаб и все сервисы",
             f"{B_YT} — пришли ссылку на видео, и я его скачаю"]
        if role == "owner":
            t += [f"{B_PASS} — логины к сервисам (только тебе, сообщение исчезает через {PASS_TTL} с)",
                  "", "<code>/setpass сервис логин пароль</code> — сохранить логин",
                  "<code>/delpass сервис</code> — удалить",
                  "<code>/users</code>, <code>/allow ID</code>, <code>/deny ID</code> — доступ к боту",
                  "", "Проблемы с сервером я присылаю сам, сразу как замечу."]
        return "\n".join(t)

    def stranger(self, frm, cid):
        uid = frm.get("id")
        name = " ".join(x for x in (frm.get("first_name"), frm.get("last_name")) if x) or "?"
        self.bot.send(cid, f"⛔ У тебя нет доступа к этому боту.\nТвой ID: <code>{uid}</code> — "
                           "передай его владельцу.")
        oid = self.owner_id()
        if oid and uid not in self.nagged:
            self.nagged.add(uid)
            un = f" @{frm['username']}" if frm.get("username") else ""
            try:
                self.bot.send(oid, f"👤 Боту пишет {esc(name)}{esc(un)} (ID <code>{uid}</code>).",
                              reply_markup={"inline_keyboard": [[
                                  {"text": "✅ Разрешить (без паролей)", "callback_data": f"allow:{uid}:{name[:30]}"}]]})
            except TelegramError:
                pass

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

    def cmd_users(self, cid):
        us = self.store.users()
        t = [f"👑 Владелец: <code>{self.owner_id()}</code>"]
        t += [f"• {esc(u['name'] or '?')} — <code>{u['id']}</code>" for u in us] or ["Других пользователей нет."]
        t += ["", "<code>/allow ID</code> · <code>/deny ID</code>. Пользователям недоступны пароли."]
        self.bot.send(cid, "\n".join(t))

    # ------------------------------------------------------------- callbacks --
    def on_callback(self, q):
        uid = (q.get("from") or {}).get("id")
        data = q.get("data") or ""
        m = q.get("message") or {}
        cid, mid = (m.get("chat") or {}).get("id"), m.get("message_id")
        role = self.role(uid)
        if role is None:
            return self.bot.answer(q["id"], "Нет доступа", alert=True)
        kind, _, rest = data.partition(":")

        if kind == "yt":
            tok, _, preset = rest.partition(":")
            p = self.pending_urls.pop(tok, None)
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
        if kind == "allow":
            nid, _, name = rest.partition(":")
            self.store.allow(int(nid), name)
            self.bot.answer(q["id"], "Добавлен")
            self.bot.edit(cid, mid, f"✅ {esc(name)} (<code>{nid}</code>) добавлен. Пароли ему недоступны.")
            try:
                self.bot.send(int(nid), "✅ Доступ открыт. Нажми /start.")
            except TelegramError:
                pass


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
