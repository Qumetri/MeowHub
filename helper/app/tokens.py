"""The four secrets the Bots page can change: where they come from and in
which order. Stdlib only.

  id      env fallback        also
  helper  HELPER_BOT_TOKEN    legacy `tg_token` (ctl.py)
  member  MEMBER_BOT_TOKEN
  crypto  CRYPTO_TG_TOKEN
  xui     XUI_API_TOKEN

Precedence: page override (settings `tok_<id>`) > env > legacy. A token is
never logged or returned whole -- see mask().
"""
import os
import re
import time

from xui import DEFAULT_URL, XUI

IDS = ("helper", "member", "crypto", "xui")
ENV = {"helper": "HELPER_BOT_TOKEN", "member": "MEMBER_BOT_TOKEN",
       "crypto": "CRYPTO_TG_TOKEN", "xui": "XUI_API_TOKEN"}
LEGACY = {"helper": "tg_token"}
BOT_TOKEN_RE = re.compile(r"\d{1,20}:[A-Za-z0-9_-]{1,100}\Z")
API_TOKEN_RE = re.compile(r"[\x21-\x7e]+\Z")
MAX_LEN = 200


def key(tid):
    return "tok_" + tid


def fallback(store, tid):
    """(token, source) without the page override: env, then the legacy ctl.py value."""
    env = (os.environ.get(ENV[tid]) or "").strip()
    if env:
        return env, "env"
    if tid in LEGACY:
        legacy = (store.get(LEGACY[tid]) or "").strip()
        if legacy:
            return legacy, "ctl"
    return "", "none"


def resolve(store, tid):
    """(token, source); source is 'page' | 'env' | 'ctl' | 'none'."""
    page = (store.get(key(tid)) or "").strip()
    if page:
        return page, "page"
    return fallback(store, tid)


def updated_ts(store, tid):
    """When the page override was saved, or None when there is none."""
    if not (store.get(key(tid)) or "").strip():
        return None
    v = store.get(key(tid) + "_ts")
    return int(v) if v.isdigit() else None


def set_override(store, tid, token):
    store.set(key(tid), token)
    store.set(key(tid) + "_ts", int(time.time()))


def clear_override(store, tid):
    """Drop the page override; True when there was one."""
    had = bool((store.get(key(tid)) or "").strip())
    store.q("DELETE FROM settings WHERE key IN (?, ?)", key(tid), key(tid) + "_ts")
    return had


def mask(token):
    """First 4 + '…' + last 4; nothing useful for a short value."""
    if len(token) < 12:
        return "…" if token else ""
    return token[:4] + "…" + token[-4:]


def scrub(text, token):
    """Error text without the token in it."""
    s = str(text)
    return s.replace(token, "***") if token else s


def bot_id(token):
    head = token.split(":", 1)[0]
    return int(head) if head.isdigit() else 0


def make_xui(token):
    """An XUI client for `token` (None when empty); URL from XUI_URL."""
    if not token:
        return None
    return XUI((os.environ.get("XUI_URL") or "").strip() or DEFAULT_URL, token)


def xui_from(store):
    return make_xui(resolve(store, "xui")[0])
