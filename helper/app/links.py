"""Signed short-lived download links, for files a client fetches without any
header: Telegram's `downloadFile` and a plain browser tab can't send initData,
so the token in the URL is the authentication.

  token = urlsafe-b64(kind|ident|uid|exp) + "." + urlsafe-b64(HMAC-SHA256(payload)[:16])

`kind` picks the handler in webapp (`awg_conf`; `file` = a downloader result,
ident `<job id>:<file index>`). The handler must re-check what the member may do at download time --
a token only proves the app issued it for that uid a few minutes ago.

The HMAC key is 32 random bytes generated on first use and kept (hex) in the
settings table as `link_secret`. It is never logged or returned.
"""
import base64
import hashlib
import hmac
import secrets
import threading
import time
import urllib.parse

MAC_LEN = 16
DEFAULT_TTL = 600

_store = None
_lock = threading.Lock()


class LinkError(ValueError):
    """Forged, malformed or expired token."""


def configure(store):
    """Default store for sign()/verify() callers that don't pass one."""
    global _store
    _store = store


def _use(store):
    store = store or _store
    if store is None:
        raise RuntimeError("links: no store configured")
    return store


def _secret(store):
    with _lock:
        hexed = store.get("link_secret", "")
        try:
            key = bytes.fromhex(hexed) if hexed else b""
        except ValueError:
            key = b""
        if len(key) != 32:
            key = secrets.token_bytes(32)
            store.set("link_secret", key.hex())
        return key


def _b64(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _mac(key, payload):
    return hmac.new(key, payload, hashlib.sha256).digest()[:MAC_LEN]


def token(kind, ident, uid, ttl=DEFAULT_TTL, store=None, now=None):
    kind, ident = str(kind), str(ident)
    if "|" in kind or "|" in ident or not kind:
        raise ValueError("bad link field")
    exp = int((time.time() if now is None else now) + ttl)
    payload = f"{kind}|{ident}|{int(uid)}|{exp}".encode()
    return _b64(payload) + "." + _b64(_mac(_secret(_use(store)), payload))


def sign(kind, ident, uid, ttl=DEFAULT_TTL, store=None):
    """A relative URL for the Mini App's own origin: ./dl/<token>."""
    return "./dl/" + token(kind, ident, uid, ttl, store)


def verify(tok, store=None, now=None):
    """-> (kind, ident, uid); LinkError when forged, malformed or expired."""
    try:
        body, _, mac = str(tok).partition(".")
        if not body or not mac:
            raise LinkError("malformed")
        payload, got = _unb64(body), _unb64(mac)
        kind, ident, uid, exp = payload.decode().split("|")
        uid, exp = int(uid), int(exp)
    except LinkError:
        raise
    except (ValueError, UnicodeError):
        raise LinkError("malformed") from None
    if not hmac.compare_digest(got, _mac(_secret(_use(store)), payload)):
        raise LinkError("forged")
    if (time.time() if now is None else now) >= exp:
        raise LinkError("expired")
    return kind, ident, uid


def absolute(rel, base):
    """./dl/<token> -> https://host/app/dl/<token> (Telegram's downloadFile needs
    an absolute HTTPS URL). Without a base the relative form is returned."""
    return urllib.parse.urljoin(base, rel) if base else rel
