"""Asks n8n to explain a sharp move, after the alert itself has gone out.

The alert is the tracker's job and must never wait on anything; the
explanation needs fresh news and a language model, and takes a minute. So the
tracker only POSTs the facts of the move to an n8n webhook, in a background
thread, and n8n answers in Telegram as a reply under the alert.

Off unless CRYPTO_EXPLAIN_HOOK is set. The webhook is reachable from the
internet through n8n's route, so it is protected by a shared secret header
(CRYPTO_EXPLAIN_SECRET), matched by a Header Auth credential in n8n.
"""
import json
import logging
import os
import threading
import time
import urllib.request

log = logging.getLogger("explain")

HOOK = os.environ.get("CRYPTO_EXPLAIN_HOOK", "").strip()
SECRET = os.environ.get("CRYPTO_EXPLAIN_SECRET", "").strip()
# One explanation per coin per half hour, and a daily cap: a crash that
# alerts on every coin would otherwise burn the free model quota by noon.
MIN_GAP_S = int(os.environ.get("CRYPTO_EXPLAIN_MIN_GAP_S", "1800"))
DAILY_MAX = int(os.environ.get("CRYPTO_EXPLAIN_DAILY_MAX", "20"))

_last = {}
_day = [None, 0]
_lock = threading.Lock()


def enabled():
    return bool(HOOK)


def request(payload):
    if not HOOK:
        return False
    sym, now = payload.get("symbol"), time.time()
    with _lock:
        today = time.strftime("%Y-%m-%d")
        if _day[0] != today:
            _day[:] = [today, 0]
        if now - _last.get(sym, 0) < MIN_GAP_S and not payload.get("urgent_first"):
            log.info("explain %s skipped: explained %ds ago", sym, now - _last[sym])
            return False
        if _day[1] >= DAILY_MAX:
            log.info("explain %s skipped: daily cap %d reached", sym, DAILY_MAX)
            return False
        _last[sym] = now
        _day[1] += 1
    threading.Thread(target=_post, args=(payload,), daemon=True).start()
    return True


def _post(payload):
    req = urllib.request.Request(HOOK, data=json.dumps(payload).encode(), headers={
        "Content-Type": "application/json", "X-Hook-Secret": SECRET})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            log.info("explain requested for %s (%s)", payload.get("ticker"), r.status)
    except Exception as e:                                   # noqa: BLE001
        log.warning("explain hook failed for %s: %s", payload.get("ticker"), e)
