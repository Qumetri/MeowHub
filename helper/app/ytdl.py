"""Downloads through MeTube's HTTP API.

The bot adds the URL, then follows MeTube's /history until the item finishes,
and answers with a tap-to-save link -- plus the file itself when it is small
enough for Telegram (bots may upload up to 50 MB).
"""
import json
import logging
import os
import time
import urllib.request
from urllib.parse import quote

import hub

log = logging.getLogger("ytdl")

PATH = os.environ.get("METUBE_PATH", "").strip("/")
API = os.environ.get("METUBE_URL", "http://metube:8081").rstrip("/") + (f"/{PATH}" if PATH else "")
FILES = os.environ.get("METUBE_FILES", "/downloads")
UPLOAD_MAX = 49 * 1024 * 1024
TTL_H = int(os.environ.get("METUBE_TTL_MIN", "720")) // 60

PRESETS = {
    "v1080": {"download_type": "video", "format": "mp4", "quality": "1080", "label": "видео 1080p"},
    "vbest": {"download_type": "video", "format": "any", "quality": "best", "label": "видео, лучшее качество"},
    "a": {"download_type": "audio", "format": "mp3", "quality": "best", "label": "аудио MP3"},
}


def _req(path, body=None, timeout=30):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{API}/{path}", data=data,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode() or "{}")


def history():
    h = _req("history")
    return {k: h.get(k) or [] for k in ("queue", "pending", "done")}


def add(url, preset):
    """-> the moment the request was made (ns). MeTube stamps every item it
    queues, and a re-download of something already in its history gets a new
    stamp -- so "newer than this" is how our items are told apart."""
    t0 = time.time_ns() - 2 * 10**9
    p = {k: v for k, v in PRESETS[preset].items() if k != "label"}
    r = _req("add", {"url": url, **p, "auto_start": True})
    if r.get("status") == "error":
        raise RuntimeError(r.get("msg") or "MeTube отклонил ссылку")
    return t0


def follow(t0, url, on_progress, timeout_s=3 * 3600):
    """Poll until every new item is finished or failed. -> list of items."""
    start = time.time()
    while time.time() - start < timeout_s:
        time.sleep(4)
        try:
            h = history()
        except Exception as e:                            # noqa: BLE001
            log.info("history: %s", e)
            continue
        mine = {}
        for lst in h.values():
            for i in lst:
                if (i.get("timestamp") or 0) >= t0:
                    mine[(i.get("id"), i.get("download_type"), i.get("quality"), i.get("format"))] = i
        if not mine and time.time() - start > 90:
            raise RuntimeError("MeTube не начал загрузку — ссылка не распознана?")
        if mine:
            on_progress(list(mine.values()))
            if all(i.get("status") in ("finished", "error") for i in mine.values()):
                return list(mine.values())
    raise RuntimeError("загрузка идёт дольше 3 часов — брошено ожидание")


def link(item):
    fn = item.get("filename")
    if not fn:
        return ""
    route = "audio_download" if item.get("download_type") == "audio" else "download"
    folder = (item.get("folder") or "").strip("/")
    rel = f"{folder}/{fn}" if folder else fn
    return f"https://{hub.base_domain()}/{PATH}/{route}/{quote(rel)}"


def local_file(item):
    fn = item.get("filename")
    if not fn:
        return None
    folder = (item.get("folder") or "").strip("/")
    p = os.path.join(FILES, folder, fn) if folder else os.path.join(FILES, fn)
    p = os.path.realpath(p)
    if not p.startswith(os.path.realpath(FILES) + os.sep) or not os.path.isfile(p):
        return None
    return p
