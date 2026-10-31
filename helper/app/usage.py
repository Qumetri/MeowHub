"""Usage notifications: the owner and admins hear, through the helper bot, when someone uses a
member service (a video downloaded, a book sent to narration, a photo upscaled …).

  * Recipients: the owner + HELPER_ADMIN_IDS, minus the person who acted. The owner's own actions
    are not reported (the owner runs the place).
  * Every recipient switches each service on/off for themselves (`/notify` in the helper bot);
    default on. Stored in settings `usage_notify` as {"<uid>": {"<service>": false, ...}}.
  * Sending happens on a background thread: a stalled Telegram upload never slows a request.

Convention (helper.md "Usage notifications"): every new member service calls `Usage.report`
for each meaningful action and is listed in SERVICES below, with ru/en texts in i18n.py.
"""
import html
import json
import logging
import threading

import i18n
from members import SERVICES as MEMBER_SERVICES

log = logging.getLogger("usage")

# Services that report usage, in the order /notify lists them (ids from members.SERVICES, plus
# "trial": a newcomer starting the free trial; "pay": a paid extension).
SERVICES = ("youtube", "books", "upscale", "trial", "pay")
KEY = "usage_notify"


def label(service, lang):
    """Display name of a notice switch: the member service's own name, else `usage.svc.<id>`."""
    m = MEMBER_SERVICES.get(service)
    return m["name_" + lang] if m else i18n.tr(lang, "usage.svc." + service)


def who(m, uid, user=None):
    """'<b>Name</b> (@user, <code>id</code>)' from a member row, else from Telegram's user dict."""
    src = m or user or {}
    name = f"{src.get('first_name', '')} {src.get('last_name', '')}".strip() or str(uid)
    tail = (f"@{html.escape(src['username'])}, " if src.get("username") else "") + f"<code>{uid}</code>"
    return f"<b>{html.escape(name)}</b> ({tail})"


def who(m, uid, user=None):
    """'<b>Name</b> (@user, <code>id</code>)' from a member row, else from Telegram's user dict."""
    src = m or user or {}
    name = f"{src.get('first_name', '')} {src.get('last_name', '')}".strip() or str(uid)
    tail = (f"@{html.escape(src['username'])}, " if src.get("username") else "") + f"<code>{uid}</code>"
    return f"<b>{html.escape(name)}</b> ({tail})"


class Usage:
    def __init__(self, store, members, owner_id, admin_ids, send):
        self.s = store
        self.members = members
        self.owner_id = owner_id            # callable -> int | None
        self.admin_ids = admin_ids          # callable -> set[int]
        self.send = send                    # send(chat_id, html_text) through the helper bot
        self.lock = threading.Lock()

    # ------------------------------------------------------------- prefs --
    def _prefs(self):
        try:
            v = json.loads(self.s.get(KEY) or "{}")
            return v if isinstance(v, dict) else {}
        except ValueError:
            return {}

    def enabled(self, uid, service):
        return self._prefs().get(str(uid), {}).get(service, True) is not False

    def toggle(self, uid, service):
        if service not in SERVICES:
            raise ValueError(service)
        with self.lock:
            p = self._prefs()
            mine = p.setdefault(str(uid), {})
            mine[service] = not self.enabled(uid, service)
            self.s.set(KEY, json.dumps(p))
            return mine[service]

    def recipients(self):
        return {x for x in {self.owner_id(), *self.admin_ids()} if x is not None}

    # ------------------------------------------------------------ report --
    def actor(self, uid, user=None):
        return who(self.members.get(uid) if uid is not None else None, uid, user)

    def report(self, service, uid, make, user=None):
        """`make(lang, who)` -> the notice text. Never raises, never blocks."""
        try:
            if uid is None or uid == self.owner_id():
                return
            to = [r for r in self.recipients() if r != uid and self.enabled(r, service)]
            if not to:
                return
            actor = self.actor(uid, user)
        except Exception:                                   # noqa: BLE001 - a notice never breaks the action
            log.exception("usage %s", service)
            return

        def go():
            for r in to:
                try:
                    self.send(r, make(i18n.lang_of(self.s, r), actor))
                except Exception:                           # noqa: BLE001
                    log.warning("usage notice %s to %s failed", service, r)
        threading.Thread(target=go, daemon=True, name="usage").start()
