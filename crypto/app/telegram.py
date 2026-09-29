"""Telegram delivery. Stdlib only."""
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request

log = logging.getLogger("telegram")
API = "https://api.telegram.org"


class TelegramError(Exception):
    pass


def _call(token, method, params=None, timeout=20):
    if not token:
        raise TelegramError("no bot token configured")
    url = f"{API}/bot{token}/{method}"
    data = urllib.parse.urlencode(params or {}).encode()
    req = urllib.request.Request(url, data=data,
                                 headers={"User-Agent": "meowhub-crypto/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            payload = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            payload = json.loads(e.read().decode())
        except Exception:
            raise TelegramError(f"HTTP {e.code}") from None
        raise TelegramError(payload.get("description", f"HTTP {e.code}"))
    except urllib.error.URLError as e:
        raise TelegramError(f"network: {e.reason}") from None
    if not payload.get("ok"):
        raise TelegramError(payload.get("description", "unknown error"))
    return payload["result"]


def verify(token):
    """-> bot username. Raises TelegramError if the token is wrong."""
    me = _call(token, "getMe")
    return me.get("username", "?")


def discover_chat_id(token):
    """Read recent updates and return candidate chats, so the user does not
    have to hunt for their numeric chat id by hand."""
    out, seen = [], set()
    for u in _call(token, "getUpdates", {"limit": 50, "timeout": 0}):
        msg = u.get("message") or u.get("channel_post") or {}
        chat = msg.get("chat") or {}
        cid = chat.get("id")
        if cid is None or cid in seen:
            continue
        seen.add(cid)
        name = (chat.get("title")
                or " ".join(x for x in (chat.get("first_name"), chat.get("last_name")) if x)
                or chat.get("username") or str(cid))
        out.append({"id": str(cid), "name": name, "type": chat.get("type", "")})
    return out


def send(token, chat_id, text, timeout=20):
    if not chat_id:
        raise TelegramError("no chat id configured")
    return _call(token, "sendMessage", {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }, timeout=timeout)


class Notifier:
    """Sends through Telegram and records the outcome on the event row.

    Delivery failures are never fatal: the event stays in the log marked
    unsent with the error, so a bad token shows up in the UI instead of
    silently losing alerts.
    """

    def __init__(self, store):
        self.store = store

    def enabled(self):
        s = self.store.settings()
        return s.get("tg_enabled") == "1" and s.get("tg_token") and s.get("tg_chat_id")

    def dispatch(self, kind, text, symbol="", price=None, force=False):
        eid = self.store.add_event(kind, text, symbol=symbol, price=price)
        if not force and not self.enabled():
            self.store.mark_event(eid, 0, "telegram disabled or unconfigured")
            return eid, False
        s = self.store.settings()
        last = ""
        for attempt in range(3):
            try:
                send(s["tg_token"], s["tg_chat_id"], text)
                self.store.mark_event(eid, 1, "")
                return eid, True
            except TelegramError as e:
                last = str(e)
                log.warning("send failed (attempt %d): %s", attempt + 1, last)
                # A rejected token or chat will not fix itself; do not retry.
                if "chat not found" in last.lower() or "unauthorized" in last.lower():
                    break
                time.sleep(1.5 * (attempt + 1))
        self.store.mark_event(eid, 0, last)
        return eid, False
