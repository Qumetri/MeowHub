"""Language support: Russian and English for the bots and the Mini App backend.

One preference per Telegram user id (`prefs` table): `auto` | `ru` | `en`.
`resolve()` is the single rule for the effective language; `lang_of()` adds
the lookups (stored preference, the last Telegram language_code seen for that
id, the membership row). `tr(lang, key, **vars)` renders a catalog entry; the
Russian texts are the originals, the English ones are written by hand.

Catalog entries use str.format placeholders and are formatted only when
variables are passed, so a text with literal braces and no variables is fine.
"""
import time
from datetime import date, datetime

LANGS = ("ru", "en")
PREFS = ("auto", "ru", "en")
RU_CODES = ("ru", "uk", "be", "kk")           # Telegram language codes that get Russian
MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа",
          "сентября", "октября", "ноября", "декабря"]
MONTHS_EN = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


# ------------------------------------------------------------------ rules --
def resolve(pref, language_code):
    """Effective language: the preference if it is ru/en, else from Telegram's
    language_code (ru/uk/be/kk -> ru, anything else non-empty -> en, empty -> ru)."""
    if pref in LANGS:
        return pref
    lc = str(language_code or "").strip().lower().replace("_", "-")
    if not lc:
        return "ru"
    return "ru" if lc.split("-")[0] in RU_CODES else "en"


def norm(value):
    """'ru' | 'en' for a client-supplied value (X-Lang, ?l=), else None."""
    v = str(value or "").strip().lower()[:2]
    return v if v in LANGS else None


def get_pref(store, uid):
    """The stored preference of `uid`: 'auto' | 'ru' | 'en'."""
    if uid is None:
        return "auto"
    rows = store.q("SELECT lang FROM prefs WHERE uid=?", int(uid))
    return rows[0]["lang"] if rows and rows[0]["lang"] in PREFS else "auto"


def set_pref(store, uid, pref):
    if pref not in PREFS:
        raise ValueError(pref)
    store.q("INSERT INTO prefs(uid,lang,updated_ts) VALUES(?,?,?) "
            "ON CONFLICT(uid) DO UPDATE SET lang=excluded.lang, updated_ts=excluded.updated_ts",
            int(uid), pref, int(time.time()))


def remember_code(store, uid, language_code):
    """Keep the last Telegram language_code seen for `uid` (owner and strangers
    have no members row), without touching their preference."""
    lc = str(language_code or "").strip()[:16]
    if uid is None or not lc:
        return
    store.q("INSERT INTO prefs(uid,lc) VALUES(?,?) ON CONFLICT(uid) DO UPDATE SET lc=excluded.lc",
            int(uid), lc)


def lang_of(store, uid, language_code=None):
    """Effective language of `uid`: the stored preference, else `language_code`
    (fresh from Telegram), else the one remembered in prefs / the members row, else ru."""
    pref, lc = "auto", ""
    if uid is not None:
        rows = store.q("SELECT lang, lc FROM prefs WHERE uid=?", int(uid))
        if rows:
            pref, lc = rows[0]["lang"], rows[0]["lc"]
            if pref in LANGS:
                return pref
        if not language_code and not lc:
            r = store.q("SELECT lang FROM members WHERE id=?", int(uid))
            lc = r[0]["lang"] if r else ""
    return resolve("auto", language_code or lc)


# ------------------------------------------------------------------ dates --
def ru_date(ts):
    d = date.fromtimestamp(ts)
    return f"{d.day} {MONTHS[d.month - 1]}"


def fmt_date(lang, ts):
    """'12 ноября' / '12 Nov 2026'."""
    if lang == "en":
        d = date.fromtimestamp(ts)
        return f"{d.day} {MONTHS_EN[d.month - 1]} {d.year}"
    return ru_date(ts)


def fmt_when(lang, ts):
    """Date with the time of day: '12 ноября в 14:05' / '12 Nov 2026, 14:05'."""
    hm = time.strftime("%H:%M", time.localtime(ts))
    if lang == "en":
        return f"{fmt_date('en', ts)}, {hm}"
    return f"{ru_date(ts)} в {hm}"


# ---------------------------------------------------------------- catalog --
T = {}


def _(key, ru, en):
    assert key not in T, key
    T[key] = {"ru": ru, "en": en}


# Messages that point the user at the owner's contact via {c}. When OWNER_CONTACT is unset the
# caller passes an empty c and we say "the owner" instead; _PLAIN_C keys need the bare form.
_PLAIN_C = {"help.member_q", "code.expired_code", "n.suspended", "n.closed"}
_NO_CONTACT = {"ru": "владельцу", "en": "the owner"}
_NO_CONTACT_PLAIN = {"ru": "у владельца", "en": "the owner"}


def tr(lang, key, /, **vars):
    entry = T.get(key)
    if entry is None:
        return key
    if "c" in vars and not vars["c"] and key != "wiz.revoked":
        table = _NO_CONTACT_PLAIN if key in _PLAIN_C else _NO_CONTACT
        vars = dict(vars, c=table.get(lang) or table["ru"])
    s = entry.get(lang) or entry["ru"]
    return s.format(**vars) if vars else s


def has(key):
    return key in T


def localize_job_error(lang, text):
    """Downloader job errors are stored as the Russian text; show a known one in `lang`."""
    if lang != "en":
        return text
    return _JOB_REV.get(text) and T[_JOB_REV[text]]["en"] or text


# -- common --------------------------------------------------------------------
_("common.no_expiry", "без срока", "no expiry")
_("common.owner", "Владелец", "Owner")
_("common.yes", "да", "yes")
_("common.no", "нет", "no")
_("common.days", "{n} д", "{n} d")
_("common.back", "« Назад", "« Back")
_("common.cancel", "Отмена", "Cancel")
_("common.t_not_found", "Не найден", "Not found")
_("common.owner_nosub", "У владельца нет подписки.", "The owner has no subscription.")

# -- reply-keyboard buttons ----------------------------------------------------
_("btn.health", "🩺 Здоровье", "🩺 Health")
_("btn.links", "🔗 Ссылки", "🔗 Links")
_("btn.yt", "🎬 Скачать видео", "🎬 Download video")
_("btn.pass", "🔑 Пароли", "🔑 Passwords")
_("btn.vpn", "🔐 VPN", "🔐 VPN")
_("btn.mx", "💬 Мессенджер", "💬 Messenger")
_("btn.sub", "🗓 Подписка", "🗓 Subscription")
_("btn.help", "❓ Помощь", "❓ Help")
_("btn.members", "👥 Участники", "👥 Members")
_("btn.code", "🎟 Код", "🎟 Code")
_("btn.app", "📱 MeowHub", "📱 MeowHub")
_("btn.svc", "⚙️ Сервисы", "⚙️ Services")
_("btn.lang", "🌐 Язык", "🌐 Language")
# inline buttons
_("btn.ext30", "➕ 30 д", "➕ 30 d")
_("btn.ext90", "➕ 90 д", "➕ 90 d")
_("btn.ext30_long", "➕ 30 дней", "➕ 30 days")
_("btn.open", "👤 Открыть", "👤 Open")
_("btn.resume", "▶️ Возобновить", "▶️ Resume")
_("btn.suspend", "⏸ Приостановить", "⏸ Pause")
_("btn.delete", "🗑 Удалить", "🗑 Delete")
_("btn.del_yes", "🗑 Да, удалить", "🗑 Yes, delete")
_("btn.inapp", "📱 В приложении", "📱 In the app")
_("btn.share", "📤 Поделиться", "📤 Share")
_("btn.revoke", "🚫 Отозвать", "🚫 Revoke")
_("btn.create", "🎟 Создать", "🎟 Create")
_("btn.create_account", "➕ Создать аккаунт", "➕ Create account")
_("btn.open_hub", "🏠 Открыть хаб", "🏠 Open the hub")
_("btn.open_dl", "Открыть загрузчик", "Open the downloader")
_("btn.give30", "✅ Дать доступ на 30 дней", "✅ Give 30 days of access")
_("btn.ignore", "Игнор", "Ignore")
_("btn.share_inb", "✅ Выдать", "✅ Share")
_("btn.skip", "Пропустить", "Skip")
_("short.vpn", "VPN", "VPN")
_("short.matrix", "Мессенджер", "Messenger")
_("short.tools", "Инструменты", "Tools")

# -- menus, errors, callbacks ----------------------------------------------------
_("menu.open_app", "📱 Открыть MeowHub", "📱 Open MeowHub")
_("menu.open_app_in", "📱 Открыть в MeowHub", "📱 Open in MeowHub")
_("menu.hint", "👇 Меню — кнопками внизу.", "👇 Menu — use the buttons below.")
_("err.not_in_sub", "⛔ Эта функция не входит в твою подписку.",
  "⛔ This feature isn't part of your subscription.")
_("err.owner_only", "⛔ Эта функция доступна только владельцу.", "⛔ This feature is for the owner only.")
_("cb.no_access", "Нет доступа", "No access")
_("cb.owner_only", "Только для владельца", "Owner only")
_("cb.private_only", "Только в личном чате", "Private chat only")
_("cb.stale", "Кнопка устарела", "This button has expired")
_("cb.error", "Ошибка", "Error")
_("cb.bad_data", "Неверные данные", "Invalid data")

# -- help ------------------------------------------------------------------------
_("help.common",
  "🐱 <b>MeowHub помощник</b>\n\n{health} — состояние сервера: диски, контейнеры, сайты, сертификаты, DNS\n"
  "{links} — хаб и все сервисы\n{yt} — пришли ссылку на видео, и я его скачаю",
  "🐱 <b>MeowHub helper</b>\n\n{health} — server status: disks, containers, sites, certificates, DNS\n"
  "{links} — the hub and all services\n{yt} — send a video link and I'll download it")
_("help.owner",
  "{pass} — логины к сервисам (только тебе, сообщение исчезает через {ttl} с)\n"
  "{members} / <code>/members</code> — участники: продлить, приостановить, сервисы\n"
  "{code} — мастер кода приглашения\n"
  "{svc} / <code>/services</code> — включать и выключать сервисы для участников\n"
  "\n<b>Участники</b>\n"
  "<code>/code [дней] [vpn,matrix,tools]</code> — код приглашения (по умолчанию 30 дней, VPN + Мессенджер)\n"
  "Участник присылает код боту (или открывает ссылку-приглашение) — и получает доступ. "
  "Когда срок кончается, сервисы встают на паузу, аккаунты остаются.\n"
  "<code>/allow ID [дней]</code> — выдать доступ без кода, <code>/deny ID</code> — приостановить\n"
  "{app} — Mini App: ссылки, VPN, мессенджер и админка\n"
  "\n<b>Пароли</b>\n"
  "<code>/setpass сервис логин пароль</code> — сохранить логин\n"
  "<code>/delpass сервис</code> — удалить\n"
  "\nПроблемы с сервером я присылаю сам, сразу как замечу.",
  "{pass} — logins for the services (visible to you only; the message disappears after {ttl} s)\n"
  "{members} / <code>/members</code> — members: extend, pause, services\n"
  "{code} — invitation code wizard\n"
  "{svc} / <code>/services</code> — turn services on and off for members\n"
  "\n<b>Members</b>\n"
  "<code>/code [days] [vpn,matrix,tools]</code> — invitation code (30 days, VPN + Messenger by default)\n"
  "A member sends the code to the bot (or opens the invitation link) and gets access. "
  "When the term ends, services are paused and accounts are kept.\n"
  "<code>/allow ID [days]</code> — grant access without a code, <code>/deny ID</code> — pause\n"
  "{app} — Mini App: links, VPN, messenger and the admin area\n"
  "\n<b>Passwords</b>\n"
  "<code>/setpass service login password</code> — save a login\n"
  "<code>/delpass service</code> — delete it\n"
  "\nI'll message you about server problems myself as soon as I notice them.")
_("help.lang_line", "{lang} / <code>/lang</code> — язык интерфейса",
  "{lang} / <code>/lang</code> — interface language")
_("help.member_head", "🐱 <b>MeowHub</b> — закрытый сервис по приглашениям.",
  "🐱 <b>MeowHub</b> — an invite-only service.")
_("help.member_tail",
  "Когда подписка заканчивается, сервисы приостанавливаются, а аккаунты и настройки "
  "сохраняются. Чтобы продолжить, пришли новый код сюда.",
  "When your subscription ends, services are paused, but your accounts and settings "
  "are kept. To continue, send a new code here.")
_("help.member_q", "Вопросы: {c}", "Questions: {c}")

# -- subscription status ---------------------------------------------------------
_("st.suspended", "⏸ приостановлена владельцем", "⏸ paused by the owner")
_("st.expired", "⚪ закончилась {date}", "⚪ ended {date}")
_("st.open", "🟢 активна, без срока", "🟢 active, no expiry")
_("st.active", "🟢 активна до {date} (осталось {n} д)", "🟢 active until {date} ({n} d left)")
_("st.word.active", "активен", "active")
_("st.word.expired", "истёк", "expired")
_("st.word.suspended", "на паузе", "paused")

_("member.start",
  "🐱 <b>MeowHub</b> — привет, {name}!\n\nПодписка: {status}\nСервисы: {svcs}\n\nМеню — кнопками внизу.",
  "🐱 <b>MeowHub</b> — hi, {name}!\n\nSubscription: {status}\nServices: {svcs}\n\nMenu — use the buttons below.")
_("member.paused",
  "⏸ <b>Доступ приостановлен</b>\nПричина: {why}.\n"
  "Аккаунты и настройки сохранены. Пришли новый код или напиши {c}.",
  "⏸ <b>Access paused</b>\nReason: {why}.\n"
  "Your accounts and settings are kept. Send a new code or contact {c}.")
_("member.why_susp", "владелец приостановил доступ", "the owner paused your access")
_("member.why_exp", "подписка закончилась {date}", "your subscription ended {date}")
_("member.welcome",
  "🎉 <b>Добро пожаловать в MeowHub, {name}!</b>\n\nПодписка: {status}\nСервисы: {svcs}\n\n"
  "Меню — кнопками внизу, а всё в одном месте — в приложении.",
  "🎉 <b>Welcome to MeowHub, {name}!</b>\n\nSubscription: {status}\nServices: {svcs}\n\n"
  "Use the buttons below for the menu, or find everything in one place in the app.")
_("member.sub",
  "🗓 <b>Подписка</b>\n\nСтатус: {status}\nСервисы: {svcs}\n\nЧтобы продлить — пришли новый код.",
  "🗓 <b>Subscription</b>\n\nStatus: {status}\nServices: {svcs}\n\nTo renew, send a new code.")

# -- access codes ----------------------------------------------------------------
_("code.invalid", "❌ Такого кода нет. Проверь, что он набран без ошибок.",
  "❌ No such code. Check that you typed it correctly.")
_("code.used", "❌ Этот код уже использован.", "❌ This code has already been used.")
_("code.expired_code", "❌ Срок действия кода истёк. Попроси новый: {c}.",
  "❌ This code has expired. Ask for a new one: {c}.")
_("code.revoked_code", "❌ Этот код отозван.", "❌ This code was revoked.")
_("code.rate_limited", "⏳ Слишком много неверных попыток. Попробуй через час.",
  "⏳ Too many wrong attempts. Try again in an hour.")
_("code.suspended", "⏸ Доступ приостановлен владельцем — код не поможет. Напиши {c}.",
  "⏸ The owner has paused your access, so a code won't help. Contact {c}.")
_("code.fail", "❌ Не удалось применить код.", "❌ Couldn't apply the code.")
_("code.extended", "✅ Подписка продлена до {until}.", "✅ Subscription extended until {until}.")
_("code.owner_note", "🎉 {who} активировал(а) код <code>{code}</code>{extra}",
  "🎉 {who} redeemed the code <code>{code}</code>{extra}")
_("code.owner_extra", ": +{days} д, {svcs}", ": +{days} d, {svcs}")
_("code.owner_verb_new", "активировал(а) код", "redeemed the code")
_("code.owner_verb_ext", "продлил(а) подписку кодом", "extended the subscription with the code")
_("code.owner_line", "🎉 {who} {verb} <code>{code}</code>: +{days} д, {svcs}",
  "🎉 {who} {verb} <code>{code}</code>: +{days} d, {svcs}")

# -- strangers, redirect ---------------------------------------------------------
_("redirect.text", "🔒 Это служебный бот. MeowHub — в @{u}.",
  "🔒 This is a service bot. MeowHub lives in @{u}.")
_("redirect.open", "Открыть @{u}", "Open @{u}")
_("redirect.activate", "Активировать в @{u}", "Activate in @{u}")
_("stranger.text",
  "🔒 MeowHub — закрытый сервис.\nЕсли у тебя есть код приглашения — просто пришли его сюда.\n"
  "Нет кода? Напиши {c}.",
  "🔒 MeowHub is an invite-only service.\nIf you have an invitation code, just send it here.\n"
  "No code? Contact {c}.")
_("stranger.owner", "👤 Боту пишет {name}{un} (ID <code>{uid}</code>).",
  "👤 {name}{un} is writing to the bot (ID <code>{uid}</code>).")

# -- VPN / Matrix in chat ----------------------------------------------------------
_("vpn.owner_none", "У владельца нет подписки — конфиги в панели 3x-ui.",
  "The owner has no subscription — the configs are in the 3x-ui panel.")
_("vpn.no_access", "🔐 VPN не входит в твою подписку или она не активна. Напиши {c}.",
  "🔐 VPN isn't part of your subscription, or it isn't active. Contact {c}.")
_("vpn.not_ready", "🔐 VPN пока не настроен на сервере. Попробуй позже.",
  "🔐 VPN isn't set up on the server yet. Try again later.")
_("vpn.preparing", "⏳ Готовлю конфиги…", "⏳ Preparing your configs…")
_("vpn.prep_fail", "❌ Не получилось подготовить конфиги. Попробуй позже или напиши {c}.",
  "❌ Couldn't prepare your configs. Try again later or contact {c}.")
_("vpn.card",
  "🔐 <b>Твой VPN</b>\n\nСсылка-подписка:\n<code>{url}</code>\n\n"
  "1. Установи Happ, v2RayTun или Hiddify.\n"
  "2. Добавь эту ссылку в приложении как подписку.\n"
  "3. Конфиги обновляются сами, новые серверы появятся автоматически. "
  "Никому не передавай ссылку.",
  "🔐 <b>Your VPN</b>\n\nSubscription link:\n<code>{url}</code>\n\n"
  "1. Install Happ, v2RayTun or Hiddify.\n"
  "2. Add this link in the app as a subscription.\n"
  "3. Configs update on their own and new servers appear automatically. "
  "Don't share the link with anyone.")
_("matrix.no_access", "💬 Мессенджер не входит в твою подписку или она не активна. Напиши {c}.",
  "💬 Messenger isn't part of your subscription, or it isn't active. Contact {c}.")
_("matrix.head", "💬 <b>Мессенджер</b>", "💬 <b>Messenger</b>")
_("matrix.accounts", "Твои аккаунты:", "Your accounts:")
_("matrix.none", "Аккаунтов пока нет.", "No accounts yet.")
_("matrix.login", "Вход: приложение Element X → сервер <code>{server}</code> → логин и пароль.",
  "Sign in: the Element X app → server <code>{server}</code> → username and password.")
_("matrix.note", "Аккаунт создаётся в приложении MeowHub — так пароль не остаётся в чате.",
  "The account is created in the MeowHub app, so the password never sits in the chat.")

# -- owner: services -----------------------------------------------------------------
_("svc.head", "⚙️ <b>Сервисы</b>", "⚙️ <b>Services</b>")
_("svc.line", "{mark} <b>{name}</b> — доступ у {n}", "{mark} <b>{name}</b> — {n} with access")
_("svc.all", "для всех участников", "for all members")
_("svc.grant", "по выдаче", "granted individually")
_("svc.foot1", "Вкл = доступно всем активным участникам (для сервисов «для всех участников»).",
  "On = available to every active member (for services marked “for all members”).")
_("svc.foot2",
  "Главный выключатель: выключишь — пропадёт у всех (VPN-клиенты отключатся, "
  "Matrix-аккаунты заблокируются).",
  "Master switch: turn it off and it disappears for everyone (VPN clients get disconnected, "
  "Matrix accounts get locked).")
_("svc.on", "включено", "enabled")
_("svc.off", "выключено", "disabled")
_("svc.toast", "{name}: {state}", "{name}: {state}")

# -- owner: codes ----------------------------------------------------------------------
_("card.code", "🎟 Код: <code>{code}</code>", "🎟 Code: <code>{code}</code>")
_("card.link", "Ссылка: {link}", "Link: {link}")
_("card.share_text", "Приглашение в MeowHub", "Invitation to MeowHub")
_("card.gives", "Даёт {days} д: {svcs}. Действует 30 дней, одноразовый.",
  "Gives {days} d: {svcs}. Valid for 30 days, single use.")
_("code.usage",
  "Формат: <code>/code [дней] [vpn,matrix,tools]</code>\nНапример: <code>/code 90 vpn</code>",
  "Usage: <code>/code [days] [vpn,matrix,tools]</code>\nExample: <code>/code 90 vpn</code>")
_("wiz.view",
  "🎟 <b>Новый код</b>\n\nВыбери сервисы и срок подписки, потом «Создать».\nСейчас: {svcs} · {days} д",
  "🎟 <b>New code</b>\n\nPick the services and the subscription length, then tap “Create”.\n"
  "Now: {svcs} · {days} d")
_("wiz.nothing", "ничего", "nothing")
_("wiz.revoked", "🚫 Код <code>{c}</code> отозван.", "🚫 Code <code>{c}</code> revoked.")
_("wiz.not_found", "Код не найден.", "Code not found.")
_("wiz.t_revoked", "Отозван", "Revoked")
_("wiz.stale", "Мастер устарел — нажми 🎟 Код ещё раз.", "This wizard has expired — tap 🎟 Code again.")
_("wiz.t_stale", "Мастер устарел", "Wizard expired")
_("wiz.pick_one", "Выбери хотя бы один сервис", "Pick at least one service")
_("wiz.created", "Код создан", "Code created")

# -- owner: members ------------------------------------------------------------------------
_("mem.head",
  "👥 <b>Участники</b>: {members} · 🟢 {active} активных · "
  "⚪ {expired} истекло · ⏸ {suspended} на паузе · ⏳ {exp7} истекают за 7 дн",
  "👥 <b>Members</b>: {members} · 🟢 {active} active · "
  "⚪ {expired} expired · ⏸ {suspended} paused · ⏳ {exp7} expiring within 7 d")
_("mem.empty", "Пока никого нет. 🎟 Код — создать приглашение.",
  "No one yet. 🎟 Code creates an invitation.")
_("mem.not_found", "Участник не найден.", "Member not found.")
_("card.status", "Статус: {dot} {word}", "Status: {dot} {word}")
_("card.until", "До: {v}", "Until: {v}")
_("card.until_val", "{date} ({n} д)", "{date} ({n} d)")
_("card.services", "Сервисы: {v}", "Services: {v}")
_("card.vpn", "VPN-клиент: {v}", "VPN client: {v}")
_("card.seen", "Был: {v}", "Last seen: {v}")
_("card.note", "Заметка: {v}", "Note: {v}")
_("mem.toast_ext", "Продлено на {n} д", "Extended by {n} d")
_("mem.toast_susp", "Приостановлен", "Paused")
_("mem.toast_res", "Возобновлён", "Resumed")
_("mem.toast_svc", "Сервисы обновлены", "Services updated")
_("mem.toast_del", "Удалён", "Deleted")
_("mem.del_confirm",
  "🗑 Удалить {name} (<code>{uid}</code>)? VPN-клиент будет удалён, "
  "Matrix-аккаунты останутся заблокированными.",
  "🗑 Delete {name} (<code>{uid}</code>)? The VPN client will be removed; "
  "Matrix accounts will stay locked.")
_("mem.deleted", "🗑 {name} удалён.", "🗑 {name} deleted.")
_("allow.usage", "Формат: <code>{cmd} 123456789{days}</code>", "Usage: <code>{cmd} 123456789{days}</code>")
_("allow.days_opt", " [дней]", " [days]")
_("allow.no_member", "Такого участника нет.", "No such member.")
_("allow.denied", "⏸ {uid} приостановлен. Вернуть: 👥 Участники.", "⏸ {uid} paused. To restore: 👥 Members.")
_("allow.ok", "✅ {ident} — доступ на {days} д, до {date}.", "✅ {ident} — access for {days} d, until {date}.")
_("grant.ignored", "Игнор: <code>{uid}</code>.", "Ignored: <code>{uid}</code>.")
_("grant.ok_toast", "Ок", "OK")
_("grant.done", "✅ Доступ выдан {name} (<code>{uid}</code>) на 30 дней, до {date}.",
  "✅ Access granted to {name} (<code>{uid}</code>) for 30 days, until {date}.")
_("grant.t_done", "Доступ выдан", "Access granted")

# -- notifications to members ------------------------------------------------------------------
_("n.extended", "✅ Подписка продлена до {date}.", "✅ Your subscription has been extended until {date}.")
_("n.suspended_bot", "⏸ Доступ к MeowHub приостановлен владельцем. Напиши {c}.",
  "⏸ The owner has paused your MeowHub access. Contact {c}.")
_("n.resumed_bot", "▶️ Доступ к MeowHub восстановлен.", "▶️ Your MeowHub access has been restored.")
_("n.suspended", "⏸ Доступ приостановлен владельцем. Вопросы — {c}.",
  "⏸ The owner has paused your access. Questions: {c}.")
_("n.resumed", "▶️ Доступ снова открыт.", "▶️ Your access is open again.")
_("n.noexp", "✅ Подписка теперь без срока.", "✅ Your subscription no longer has an expiry date.")
_("n.until", "📅 Подписка действует до {date}.", "📅 Your subscription is valid until {date}.")
_("n.svcs", "🔧 Твои сервисы обновлены: {svcs}.", "🔧 Your services were updated: {svcs}.")
_("n.closed", "🚫 Доступ к MeowHub закрыт владельцем. Вопросы — {c}.",
  "🚫 The owner has closed your MeowHub access. Questions: {c}.")
_("n.granted", "🎁 Тебе открыт доступ к MeowHub до {date}: {svcs}.",
  "🎁 You've been given MeowHub access until {date}: {svcs}.")
_("n.matrix_created", "💬 {name} создал(а) аккаунт <code>{mxid}</code>",
  "💬 {name} created the account <code>{mxid}</code>")
_("rem.expired",
  "⏸ Подписка MeowHub закончилась. Сервисы приостановлены, аккаунты сохранены. "
  "Пришли новый код или напиши {c}.",
  "⏸ Your MeowHub subscription has ended. Services are paused and your accounts are kept. "
  "Send a new code or contact {c}.")
_("rem.owner_expired", "⌛ {name} — подписка истекла", "⌛ {name} — subscription expired")
_("rem.24h",
  "⏳ Подписка MeowHub закончится через 24 часа — {when}. Чтобы продлить, пришли новый код.",
  "⏳ Your MeowHub subscription ends in 24 hours — {when}. To renew, send a new code.")
_("rem.3d",
  "⏳ Подписка MeowHub закончится через 3 дня (до {date}). Чтобы продлить, пришли новый код.",
  "⏳ Your MeowHub subscription ends in 3 days (until {date}). To renew, send a new code.")

# -- owner: 3x-ui inbounds -------------------------------------------------------------------------
_("inb.new",
  "🆕 Новый инбаунд в 3x-ui: <b>{r}</b> ({proto}, :{port}). Выдать его участникам?",
  "🆕 New inbound in 3x-ui: <b>{r}</b> ({proto}, :{port}). Share it with members?")
_("inb.skipped", "Пропущено.", "Skipped.")
_("inb.t_skipped", "Пропущено", "Skipped")
_("inb.no_xui", "⛔ 3x-ui не настроен.", "⛔ 3x-ui isn't configured.")
_("inb.t_no_xui", "3x-ui не настроен", "3x-ui isn't configured")
_("inb.gone", "⛔ Такого инбаунда уже нет.", "⛔ That inbound no longer exists.")
_("inb.not_init", "⏳ Список инбаундов ещё не инициализирован — повтори через минуту.",
  "⏳ The inbound list isn't initialized yet — try again in a minute.")
_("inb.t_later", "Повтори позже", "Try again later")
_("inb.already", "Инбаунд <b>{r}</b> уже выдан.", "Inbound <b>{r}</b> is already shared with members.")
_("inb.t_already", "Уже выдан", "Already shared")
_("inb.two_awg",
  "⛔ Нельзя: у участников уже есть один wireguard/amneziawg инбаунд — "
  "на двух клиент получает неверный адрес.",
  "⛔ Not allowed: members already have one wireguard/amneziawg inbound — "
  "with two, the client gets a wrong address.")
_("inb.t_two_awg", "Второй WireGuard/AWG нельзя", "A second WireGuard/AWG isn't allowed")
_("inb.shared", "✅ Инбаунд <b>{r}</b> выдан участникам.", "✅ Inbound <b>{r}</b> shared with members.")
_("inb.t_shared", "Выдан", "Shared")

# -- health, links, downloader, passwords ------------------------------------------------------------
_("health.checking", "🩺 Проверяю…", "🩺 Checking…")
_("links.hub", "Хаб", "Hub")
_("links.none", "Список сервисов недоступен — пересобери dashboard (npm run build).",
  "The service list is unavailable — rebuild the dashboard (npm run build).")
_("dl.off", "Скачивание видео сейчас выключено.", "Video downloads are currently turned off.")
_("dl.paste", "Пришли ссылку — открою загрузчик (YouTube, RuTube, VK Видео).",
  "Send me a link and I'll open the downloader (YouTube, RuTube, VK Video).")
_("dl.usage", "Формат: <code>/yt https://youtu.be/…</code>", "Usage: <code>/yt https://youtu.be/…</code>")
_("dl.moved",
  "🎬 Загрузчик теперь работает в мини-приложении MeowHub — "
  "открой приложение и вставь ссылку на странице «Скачать».",
  "🎬 The downloader now lives in the MeowHub mini app — "
  "open the app and paste the link on the “Download” page.")
_("dl.offer", "🎬 Скачать можно в MeowHub — ссылка уже подставлена.",
  "🎬 You can download it in MeowHub — the link is already filled in.")
_("pass.none", "Сохранённых логинов нет.\n<code>/setpass сервис логин пароль</code>",
  "No saved logins.\n<code>/setpass service login password</code>")
_("pass.pick", "Выбери сервис:", "Pick a service:")
_("pass.missing", "Без сохранённого логина: {v}", "Without a saved login: {v}")
_("pass.ids", "ID для /setpass: {v}", "IDs for /setpass: {v}")
_("pass.nologin", "Нет такого логина.", "No such login.")
_("pass.login", "Логин: <code>{v}</code>", "Login: <code>{v}</code>")
_("pass.password", "Пароль: <tg-spoiler><code>{v}</code></tg-spoiler>",
  "Password: <tg-spoiler><code>{v}</code></tg-spoiler>")
_("pass.ttl", "<i>Сообщение удалится через {ttl} с.</i>", "<i>This message will be deleted in {ttl} s.</i>")
_("setpass.usage",
  "Формат: <code>/setpass сервис логин пароль</code>\nСообщение с паролем я удалил.",
  "Usage: <code>/setpass service login password</code>\nI've deleted your message with the password.")
_("setpass.ok",
  "✅ Сохранил логин для <b>{name}</b>. Твоё сообщение с паролем удалено.",
  "✅ Saved the login for <b>{name}</b>. Your message with the password has been deleted.")
_("delpass.ok", "🗑 Удалил {vid}.", "🗑 Deleted {vid}.")
_("delpass.env", "Этот логин приходит из .env — удалить можно только там.",
  "This login comes from .env — it can only be removed there.")

# -- monitor -------------------------------------------------------------------------------------------
_("mon.resolved", "✅ Решено: {text}", "✅ Resolved: {text}")
_("mon.report", "☀️ <b>Утренний отчёт</b>", "☀️ <b>Morning report</b>")

# -- language picker ---------------------------------------------------------------------------------------
_("lang.picker", "🌐 Язык / Language", "🌐 Язык / Language")
_("lang.ru", "Русский", "Русский")
_("lang.en", "English", "English")
_("lang.auto", "Авто (как в Telegram)", "Auto (Telegram)")
_("lang.set.ru", "Язык: русский", "Язык: русский")
_("lang.set.en", "Language: English", "Language: English")
_("lang.set.auto", "Язык: авто (как в Telegram), сейчас русский",
  "Language: auto (as in Telegram), now English")

# -- bot command lists (setMyCommands) --------------------------------------------------------------------------
_("cmd.start", "Начало", "Start")
_("cmd.help", "Помощь", "Help")
_("cmd.vpn", "Мой VPN", "My VPN")
_("cmd.matrix", "Мессенджер", "Messenger")
_("cmd.sub", "Моя подписка", "My subscription")
_("cmd.lang", "Язык / Language", "Язык / Language")
_("cmd.health", "Состояние сервера", "Server status")
_("cmd.links", "Ссылки хаба", "Hub links")
_("cmd.yt", "Скачать видео: /yt <ссылка>", "Download a video: /yt <link>")
_("cmd.code", "/code [дней] [vpn,matrix,tools]", "/code [days] [vpn,matrix,tools]")
_("cmd.members", "Участники", "Members")
_("cmd.services", "Включить/выключить сервисы", "Turn services on/off")
_("cmd.pass", "Пароли к сервисам", "Service passwords")
_("cmd.setpass", "/setpass <сервис> <логин> <пароль>", "/setpass <service> <login> <password>")
_("cmd.delpass", "/delpass <сервис>", "/delpass <service>")

# -- health findings (monitor + report) ---------------------------------------------------------------------------
_("health.stats_down", "hub-stats не отвечает: {e}", "hub-stats isn't responding: {e}")
_("health.disk", "{name} заполнен на {p}% (свободно {free} GB)", "{name} is {p}% full ({free} GB free)")
_("health.ram", "RAM занята на {p}%", "RAM is {p}% used")
_("health.cpu_temp", "CPU {t}°C", "CPU {t}°C")
_("health.gpu_temp", "GPU {t}°C", "GPU {t}°C")
_("health.gpu_ok", "GPU отвечает", "GPU is responding")
_("health.gpu_lost",
  "GPU не отвечает (nvidia-smi). Частая причина — обновился драйвер NVIDIA без перезагрузки",
  "The GPU isn't responding (nvidia-smi). A common cause is an NVIDIA driver update without a reboot")
_("health.tcp", "{n} открытых TCP-соединений", "{n} open TCP connections")
_("health.tcp_leak", "{n} открытых TCP-соединений — похоже на утечку соединений VPN (xray); "
  "помогает перезапуск 3x-ui",
  "{n} open TCP connections — looks like a VPN (xray) connection leak; restarting 3x-ui helps")
_("health.docker_down", "docker-proxy не отвечает: {e}", "docker-proxy isn't responding: {e}")
_("health.ct_loop", "контейнер {name} перезапускается по кругу", "container {name} is restarting in a loop")
_("health.ct_unhealthy", "контейнер {name} unhealthy", "container {name} is unhealthy")
_("health.ct_oom", "контейнер {name} упал: нехватка памяти (OOM)", "container {name} crashed: out of memory (OOM)")
_("health.ct_exit", "контейнер {name} упал: код выхода {code}", "container {name} crashed: exit code {code}")
_("health.ct_ok", "{name} работает", "{name} is running")
_("health.ct_restarted", "контейнер {name} упал и был перезапущен (раз: {rc})",
  "container {name} crashed and was restarted (count: {rc})")
_("health.tls_bad", "{name}: сертификат недействителен ({msg})", "{name}: invalid certificate ({msg})")
_("health.route_down", "{name} недоступен: {e}", "{name} is unreachable: {e}")
_("health.cert", "сертификат {host} истекает через {days} дн.", "certificate {host} expires in {days} d")
_("health.route_noans", "{name} не ответил: {e}", "{name} didn't respond: {e}")
_("health.route_http", "{name} отвечает ошибкой HTTP {code}", "{name} returns HTTP error {code}")
_("health.route_ok", "{name} отвечает ({code})", "{name} responds ({code})")
_("health.ip_unknown", "не удалось узнать внешний IP", "couldn't determine the public IP")
_("health.dns_none", "не резолвится", "doesn't resolve")
_("health.dns_bad", "DNS не совпадает с внешним IP {ip}: {bad}. Обнови A-записи у регистратора",
  "DNS doesn't match the public IP {ip}: {bad}. Update the A records at your registrar")
_("health.dns_ok", "DNS указывает на {ip}", "DNS points to {ip}")
_("health.check_failed", "проверка упала: {e}", "check failed: {e}")
_("health.head_ok", "✅ <b>Сервер в порядке</b>", "✅ <b>The server is fine</b>")
_("health.head_crit", "🔴 <b>Проблем: {n}</b>", "🔴 <b>Problems: {n}</b>")
_("health.head_crit_warn", ", предупреждений: {n}", ", warnings: {n}")
_("health.head_warn", "⚠️ <b>Предупреждений: {n}</b>", "⚠️ <b>Warnings: {n}</b>")
_("health.dur_dh", "{d} д {h} ч", "{d} d {h} h")
_("health.dur_hm", "{h} ч {m} мин", "{h} h {m} min")
_("health.sys", "🖥 <b>Система</b>", "🖥 <b>System</b>")
_("health.uptime", "Аптайм {v}", "Uptime {v}")
_("health.net", "🌐 <b>Сеть</b>", "🌐 <b>Network</b>")
_("health.wifi_q4", "отличный", "excellent")
_("health.wifi_q3", "хороший", "good")
_("health.wifi_q2", "слабый", "weak")
_("health.wifi_q1", "плохой", "poor")
_("health.tcp_line", "TCP-соединений: {n}", "TCP connections: {n}")
_("health.ip_line", "Внешний IP {ip}", "Public IP {ip}")
_("health.ct_head", "🐳 <b>Контейнеры</b>\nРаботают {run} из {total}",
  "🐳 <b>Containers</b>\n{run} of {total} running")
_("health.ct_stopped", "Остановлены вручную: {v}", "Stopped manually: {v}")
_("health.sites_head", "🔗 <b>Сайты</b>\nОтвечают {ok} из {n}", "🔗 <b>Sites</b>\n{ok} of {n} responding")
_("health.cert_soon", "Ближайший сертификат истекает через {days} дн. ({host})",
  "The nearest certificate expires in {days} d ({host})")

# -- API: result / error messages ---------------------------------------------------------------------------------
_("result.new", "Доступ открыт.", "Access granted.")
_("result.extended", "Подписка продлена.", "Subscription extended.")
_("result.invalid", "Код не найден. Проверь, что он введён без ошибок.",
  "Code not found. Check that you typed it correctly.")
_("result.used", "Этот код уже использован.", "This code has already been used.")
_("result.expired_code", "Срок действия кода истёк.", "This code has expired.")
_("result.revoked_code", "Этот код отозван.", "This code was revoked.")
_("result.suspended", "Доступ приостановлен владельцем — по коду его не вернуть.",
  "The owner has paused your access — a code can't restore it.")
_("result.rate_limited", "Слишком много неверных попыток. Попробуй через час.",
  "Too many wrong attempts. Try again in an hour.")
# default message of an error code (no explicit message)
_("msg.unauthorized", "Нужна авторизация.", "Authorization required.")
_("msg.forbidden", "Недостаточно прав.", "Not enough permissions.")
_("msg.not_found", "Не найдено.", "Not found.")
_("msg.bad_request", "Некорректный запрос.", "Invalid request.")
_("msg.internal", "Внутренняя ошибка.", "Internal error.")
_("msg.bad_token", "Токен не подошёл.", "The token was rejected.")
_("msg.same_bot", "Это токен того же бота, что и служебный: нужен отдельный бот.",
  "This is the same bot as the service bot — a separate bot is needed.")
_("msg.crypto_apply_failed", "Не удалось передать токен крипто-трекеру.",
  "Couldn't pass the token to the crypto tracker.")
_("msg.no_override", "Токен не менялся на странице — сбрасывать нечего.",
  "The token wasn't changed on the page — nothing to reset.")
_("msg.no_fallback", "Без токена бот не запустится: в .env его нет.",
  "The bot can't start without a token, and there's none in .env.")
_("msg.not_grantable",
  "Этот сервис не выдаётся участникам по одному: он включается для всех главным выключателем.",
  "This service can't be granted to members individually: it's turned on for everyone with the master switch.")
_("msg.bad_link", "Ссылка недействительна или устарела.", "This link is invalid or has expired.")
# explicit messages
_("api.bad_int", "Поле {field}: ожидается число {lo}..{hi}.", "Field {field}: a number from {lo} to {hi} is expected.")
_("api.bad_service", "Поле {field}: неизвестный сервис.", "Field {field}: unknown service.")
_("api.timeout", "Операция идёт слишком долго, попробуй позже.", "The operation is taking too long, try again later.")
_("api.bot_starting", "Бот ещё запускается, попробуй через минуту.", "The bot is still starting, try again in a minute.")
_("api.bot_starting_short", "Бот ещё запускается.", "The bot is still starting.")
_("api.sync_not_started", "Синхронизация ещё не запущена.", "Sync hasn't started yet.")
_("api.dl_starting", "Загрузчик ещё запускается, попробуй через минуту.",
  "The downloader is still starting, try again in a minute.")
_("api.session_invalid", "Сессия недействительна, открой приложение заново.", "Your session is invalid, reopen the app.")
_("api.no_access", "Этот сервис недоступен: нет активной подписки.", "This service isn't available: no active subscription.")
_("api.avatar_failed", "Не удалось получить аватар.", "Couldn't fetch the avatar.")
_("api.no_avatar", "Аватара нет.", "No avatar.")
_("api.tg_only", "Код активируется в Telegram.", "Codes are redeemed in Telegram.")
_("api.enter_code", "Введи код доступа.", "Enter an access code.")
_("api.vpn_unconfigured", "VPN пока не настроен.", "VPN isn't set up yet.")
_("api.pending", "Конфиг ещё создаётся, повтори через несколько секунд.",
  "Your config is still being created, try again in a few seconds.")
_("api.matrix_unconfigured", "Мессенджер пока не настроен.", "Messenger isn't set up yet.")
_("api.matrix_limit", "Достигнут лимит аккаунтов.", "You've reached the account limit.")
_("api.bad_username", "Имя: 3–24 символа, строчные латинские буквы, цифры и . _ = -",
  "Username: 3–24 characters, lowercase Latin letters, digits and . _ = -")
_("api.username_invalid", "Это имя недопустимо.", "This username isn't allowed.")
_("api.username_taken", "Это имя уже занято.", "This username is already taken.")
_("api.weak_password", "Пароль должен быть от 10 до 128 символов.", "The password must be 10 to 128 characters long.")
_("api.matrix_error", "Мессенджер не отвечает, попробуй позже.", "Messenger isn't responding, try again later.")
_("api.not_yours", "Это не твой аккаунт.", "This isn't your account.")
_("api.member_not_found", "Участник не найден.", "Member not found.")
_("api.note_long", "Заметка: не больше 200 символов.", "Note: 200 characters at most.")
_("api.bad_action", "Неизвестное действие.", "Unknown action.")
_("api.code_not_found", "Код не найден.", "Code not found.")
_("api.bad_enabled", "Поле enabled: ожидается true или false.", "Field enabled: true or false is expected.")
_("api.bad_member_ids", "member_ids: список чисел.", "member_ids: a list of numbers is expected.")
_("api.unknown_inbound", "Такого инбаунда нет в 3x-ui.", "There's no such inbound in 3x-ui.")
_("api.two_awg", "Можно выдать не больше одного WireGuard/AmneziaWG инбаунда.",
  "At most one WireGuard/AmneziaWG inbound can be shared.")
_("api.token_format", "Это не похоже на токен бота: нужен вид 123456789:AAH…",
  "That doesn't look like a bot token — it should look like 123456789:AAH…")
_("api.tg_replied", "Telegram ответил: {e}", "Telegram replied: {e}")
_("api.xui_token_format", "Токен 3x-ui не должен содержать пробелов и спецсимволов.",
  "The 3x-ui token must not contain spaces or special characters.")
_("api.xui_replied", "3x-ui ответил: {e}", "3x-ui replied: {e}")
_("api.token_str", "Поле token: ожидается строка.", "Field token: a string is expected.")
_("api.token_empty", "Токен пустой.", "The token is empty.")
_("api.token_long", "Токен слишком длинный.", "The token is too long.")
_("api.awg_missing", "AmneziaWG-конфиг не найден.", "AmneziaWG config not found.")
_("api.bad_url", "Некорректная ссылка.", "Invalid link.")
_("api.bad_lang", "Поле lang: auto, ru или en.", "Field lang: auto, ru or en.")
_("api.bad_content_type", "Ожидается application/json.", "Expected application/json.")
_("api.too_large", "Слишком большой запрос.", "Request too large.")
_("api.bad_json", "Тело запроса — не JSON.", "The request body is not JSON.")
_("api.bad_json_obj", "Тело запроса должно быть объектом.", "The request body must be an object.")
_("api.method", "Метод не поддерживается.", "Method not allowed.")
_("api.vpn_error", "VPN-панель не отвечает, попробуй позже.", "The VPN panel isn't responding, try again later.")
_("integ.crypto_note",
  "n8n хранит свою копию токена крипто-бота: после смены обнови токен в его Telegram-credential в n8n.",
  "n8n keeps its own copy of the crypto bot token: after changing it, update the token in its "
  "Telegram credential in n8n.")
_("integ.not_configured", "не настроен", "not configured")
_("integ.inbounds_one", "{n} инбаунд", "{n} inbound")
_("integ.inbounds_few", "{n} инбаунда", "{n} inbounds")
_("integ.inbounds_many", "{n} инбаундов", "{n} inbounds")

# -- VPN link groups and names -------------------------------------------------------------------------------------------
_("grp.main.title", "VLESS и Shadowsocks", "VLESS and Shadowsocks")
_("grp.main.hint", "Скопируй → в приложении «+» → «Импорт из буфера».",
  "Copy it → in the app tap “+” → “Import from clipboard”.")
_("grp.new.title", "🧪 Новые протоколы (тест)", "🧪 New protocols (test)")
_("grp.new.hint", "Нужна последняя версия Happ или v2RayTun. Скопируй → «+» → «Из буфера».",
  "Needs the latest Happ or v2RayTun. Copy it → “+” → “From clipboard”.")
_("grp.udp.title", "Hysteria2 (UDP)", "Hysteria2 (UDP)")
_("grp.udp.hint", "Если обычные не работают. Скопируй → «+» → «Из буфера».",
  "Use it if the regular ones don't work. Copy it → “+” → “From clipboard”.")
_("grp.awg.title", "AmneziaWG", "AmneziaWG")
_("grp.awg.hint",
  "AmneziaVPN: «Открыть в AmneziaVPN» → «Подключиться». "
  "AmneziaWG: скачай .conf → «+» → «Импорт из файла» (или QR).",
  "AmneziaVPN: “Open in AmneziaVPN” → “Connect”. "
  "AmneziaWG: download the .conf → “+” → “Import from file” (or scan the QR).")
_("grp.tg.title", "Прокси для Telegram", "Telegram proxy")
_("grp.tg.hint", "Нажми — Telegram сам предложит включить.", "Tap it — Telegram will offer to enable it.")
_("link.mtproto", "MTProto-прокси", "MTProto proxy")
_("link.config", "Конфиг {i}", "Config {i}")
_("link.open_amnezia", "Открыть в AmneziaVPN", "Open in AmneziaVPN")
_("link.conf", "Файл .conf", ".conf file")
_("link.qr", "QR для AmneziaWG", "QR for AmneziaWG")

# -- server-rendered pages -------------------------------------------------------------------------------------------------------
_("go.title", "Открыть в {app}", "Open in {app}")
_("go.hint", "Если приложение не открылось, установи {app} и нажми кнопку ещё раз.",
  "If the app didn't open, install {app} and tap the button again.")
_("awg.open", "Открыть в AmneziaVPN", "Open in AmneziaVPN")
_("awg.retry", "Не открылось? Попробовать ещё так", "Didn't open? Try this way")
_("awg.how_android", "Откроется AmneziaVPN — нажми «Подключиться».", "AmneziaVPN will open — tap “Connect”.")
_("awg.download", "Скачать ключ для AmneziaVPN", "Download the key for AmneziaVPN")
_("awg.how_ios", "Открой файл → «Поделиться» → AmneziaVPN.", "Open the file → “Share” → AmneziaVPN.")
_("awg.how_other", "Открой скачанный файл в AmneziaVPN (или «+» → «Файл с настройками»).",
  "Open the downloaded file in AmneziaVPN (or “+” → “File with settings”).")
_("awg.copy", "Скопировать ключ", "Copy the key")
_("awg.noapp", "Нет приложения? {a}, потом нажми кнопку ещё раз.",
  "Don't have the app? {a}, then tap the button again.")
_("awg.install", "Установить AmneziaVPN", "Install AmneziaVPN")
_("awg.copied", "Скопировано — «+» → «Вставить» в AmneziaVPN", "Copied — “+” → “Paste” in AmneziaVPN")
_("awg.copy_failed", "Не удалось скопировать", "Couldn't copy")

# -- downloader ----------------------------------------------------------------------------------------------------------------------
_("dl.preset.best", "Лучшее", "Best")
_("dl.site.vk", "VK Видео", "VK Video")
_("dl.err.bad_field", "Фрагмент: поле {field} некорректно.", "Clip: field {field} is invalid.")
_("dl.err.range", "Фрагмент: поле {field} вне диапазона.", "Clip: field {field} is out of range.")
_("dl.err.subs", "Субтитры: ru или en.", "Subtitles: ru or en.")
_("dl.err.clip_obj", "Фрагмент: ожидается {start, end}.", "Clip: {start, end} is expected.")
_("dl.err.clip_order", "Фрагмент: конец должен быть позже начала.", "Clip: the end must be after the start.")
_("dl.err.playlist_bool", "Поле playlist: ожидается true или false.", "Field playlist: true or false is expected.")
_("dl.err.bad_url", "Нужна ссылка http:// или https://.", "A link starting with http:// or https:// is required.")
_("dl.err.bad_preset", "Неизвестный пресет.", "Unknown preset.")
_("dl.err.playlist", "Это плейлист — включи «Плейлист (до 10)»", "This is a playlist — turn on “Playlist (up to 10)”")
_("dl.err.disk", "На сервере мало места — попробуй позже.", "The server is low on disk space — try again later.")
_("dl.err.limit_active", "Одновременно можно качать не больше {n} — дождись завершения.",
  "You can download at most {n} at a time — wait for one to finish.")
_("dl.err.limit_hour", "Лимит: не больше {n} загрузок в час. Попробуй позже.",
  "Limit: at most {n} downloads per hour. Try again later.")
_("dl.err.limit_day", "Лимит: не больше {n} загрузок в сутки.", "Limit: at most {n} downloads per day.")
_("dl.err.not_found", "Загрузка не найдена.", "Download not found.")
_("dl.err.not_active", "Загрузка уже завершена.", "This download has already finished.")
_("dl.err.file_gone", "Файл уже удалён.", "The file has already been deleted.")
# job errors: stored in the database as the Russian text, shown through localize_job_error()
_("dl.job.restart", "прервано перезапуском", "interrupted by a restart")
_("dl.job.nostart", "MeTube не начал загрузку — ссылка не распознана?",
  "The download didn't start — is the link recognized?")
_("dl.job.nofile", "файл не найден на диске", "file not found on disk")
_("dl.job.queue", "ошибка очереди, повтори", "queue error, try again")
_("dl.job.unavail", "загрузчик недоступен, попробуй позже", "the downloader is unavailable, try again later")
_("dl.job.default", "ошибка загрузки", "download failed")
_("dl.job.rejected", "MeTube отклонил ссылку", "The link was rejected")

_JOB_REV = {v["ru"]: k for k, v in T.items() if k.startswith("dl.job.")}
