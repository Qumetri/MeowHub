"""Multi-user video/audio downloader (service `youtube`) on top of MeTube.

MeTube keys its queue and history by URL, so one global queue cannot serve
several people. This module keeps its own job table (`downloads`) and feeds
MeTube from it:

  * at most one MeTube item per URL at a time; identical requests (same url +
    preset + options) that arrive while one runs share it (fan-out), a
    different preset for the same URL waits;
  * every user downloads into their own MeTube folder `u<uid>` (visible
    read-only in this container as /downloads/u<uid>/);
  * ONE worker thread polls MeTube's /history, updates the jobs and delivers
    finished files (Telegram upload up to 49 MB, else a signed link);
  * once every requester of an item got it as a Telegram upload, the item is
    deleted through MeTube (which deletes the file). Anything else is left to
    the janitor (30 min).

Presets are defined once here (presets_payload) and used by the Mini App API
and by both bots. MeTube URLs are never handed out -- files are served by
webapp's /dl/<token>.
"""
import html
import json
import logging
import os
import re
import shutil
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qs, urlsplit

import links
from tg import TelegramError

log = logging.getLogger("downloads")

# id, label, quality
VIDEO = [("v360", "360p", "360"), ("v720", "720p", "720"),
         ("v1080", "1080p", "1080"), ("vbest", "Лучшее", "best")]
# id, label, format, quality
AUDIO = [("mp3", "MP3", "mp3", "320"), ("m4a", "M4A", "m4a", "best"), ("opus", "Opus", "opus", "best")]
PRESETS = {p[0]: {"type": "video", "label": p[1], "quality": p[2]} for p in VIDEO}
PRESETS.update({p[0]: {"type": "audio", "label": p[1], "format": p[2], "quality": p[3]} for p in AUDIO})
SUBS = ("ru", "en")
PLAYLIST_MAX = 10
SITES = ["YouTube", "RuTube", "VK Видео"]
TERMINAL = ("finished", "error", "canceled")
ACTIVE = ("queued", "running")
STALE_START_S = 90           # MeTube showed no item for a job we submitted
QUEUE_RETRY_S = 120          # MeTube unreachable: keep a job queued this long
EDIT_EVERY = 3.0             # seconds between edits of one progress message
HK_EVERY = 15.0
DAY = 86400


def _int_env(name, default):
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


def limits():
    return {"active": _int_env("DL_ACTIVE", 2), "per_hour": _int_env("DL_PER_HOUR", 10),
            "per_day": _int_env("DL_PER_DAY", 30), "ttl_min": _int_env("METUBE_TTL_MIN", 30),
            "chat_max_mb": _int_env("DL_CHAT_MAX_MB", 49)}


def presets_payload():
    """GET /api/dl/presets, and what the bots build their buttons from."""
    return {"video": [{"id": i, "label": lb} for i, lb, _q in VIDEO],
            "audio": [{"id": i, "label": lb} for i, lb, _f, _q in AUDIO],
            "extras": {"subs": list(SUBS), "clip": True, "playlist_max": PLAYLIST_MAX},
            "limits": limits(), "sites": list(SITES)}


class DlError(Exception):
    def __init__(self, status, code, message=None):
        super().__init__(code)
        self.status, self.code, self.message = status, code, message or code


class MeTubeError(Exception):
    def __init__(self, msg, transient=False):
        super().__init__(msg)
        self.transient = transient


class MeTube:
    """The few MeTube routes we use. Never exposed to users."""

    def __init__(self, base=None, path=None, timeout=15):
        base = (base or os.environ.get("METUBE_URL") or "http://metube:8081").rstrip("/")
        path = (os.environ.get("METUBE_PATH", "") if path is None else path).strip("/")
        self.api = base + (f"/{path}" if path else "")
        self.timeout = timeout

    def _call(self, route, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(f"{self.api}/{route}", data=data,
                                     headers={"Content-Type": "application/json"} if data is not None else {})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read().decode() or "{}")
        except urllib.error.HTTPError as e:
            raise MeTubeError(f"HTTP {e.code}", transient=e.code >= 500) from None
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise MeTubeError(f"{type(e).__name__}", transient=True) from None

    def add(self, body):
        r = self._call("add", body)
        msg = str(r.get("msg") or "")
        if "already in queue" in msg.lower():
            # Per-URL serialization should make this impossible: a bug, never a user message.
            log.error("MeTube said 'Already in queue' for %s -- serialization bug", body.get("url"))
            raise MeTubeError("queue_conflict")
        if r.get("status") == "error":
            raise MeTubeError(msg or "MeTube отклонил ссылку")
        return r

    def delete(self, ids, where):
        return self._call("delete", {"ids": list(ids), "where": where})

    def history(self):
        h = self._call("history")
        return {k: h.get(k) or [] for k in ("queue", "pending", "done")}


# ----------------------------------------------------------------- helpers --
def valid_url(u):
    if not isinstance(u, str):
        return False
    u = u.strip()
    if not u or len(u) > 2000 or re.search(r"[\x00-\x20\x7f]", u):
        return False
    try:
        p = urlsplit(u)
    except ValueError:
        return False
    return p.scheme in ("http", "https") and bool(p.hostname)


def is_playlist(url):
    p = urlsplit(url)
    return "list" in parse_qs(p.query) or "/playlist" in p.path


def _secs(v, field):
    if isinstance(v, bool):
        raise DlError(400, "bad_request", f"Фрагмент: поле {field} некорректно.")
    if isinstance(v, (int, float)):
        out = float(v)
    elif isinstance(v, str) and re.fullmatch(r"\d+(:\d{1,2}){0,2}", v.strip()):
        out = 0.0
        for part in v.strip().split(":"):
            out = out * 60 + int(part)
    else:
        raise DlError(400, "bad_request", f"Фрагмент: поле {field} некорректно.")
    if out < 0 or out > DAY:
        raise DlError(400, "bad_request", f"Фрагмент: поле {field} вне диапазона.")
    return int(out)


def norm_opts(raw):
    raw = raw or {}
    subs = raw.get("subs") or None
    if subs not in (None,) + SUBS:
        raise DlError(400, "bad_request", "Субтитры: ru или en.")
    clip = raw.get("clip") or None
    if clip is not None:
        if not isinstance(clip, dict):
            raise DlError(400, "bad_request", "Фрагмент: ожидается {start, end}.")
        start, end = _secs(clip.get("start", 0), "start"), _secs(clip.get("end"), "end")
        if end <= start:
            raise DlError(400, "bad_request", "Фрагмент: конец должен быть позже начала.")
        clip = {"start": start, "end": end}
    for k in ("playlist", "to_chat"):
        if raw.get(k) is not None and not isinstance(raw[k], bool):
            raise DlError(400, "bad_request", f"Поле {k}: ожидается true или false.")
    return {"subs": subs, "clip": clip, "playlist": bool(raw.get("playlist")),
            "to_chat": bool(raw.get("to_chat"))}


def optkey(opts):
    """Two requests share a MeTube item only when this matches (to_chat is a delivery detail)."""
    return json.dumps([opts.get("subs"), opts.get("clip"), bool(opts.get("playlist"))], sort_keys=True)


def esc(s):
    return html.escape(str(s), quote=False)


def caption(title):
    title = (title or "")[:1000]
    while len(esc(title)) > 1000:
        title = title[:-25]
    return esc(title)


def fmt_size(n):
    n = n or 0
    return f"{n / 1e9:.1f} ГБ" if n >= 1e9 else f"{n / 1e6:.0f} МБ" if n >= 1e6 else f"{max(n, 0) // 1000} КБ"


def fmt_eta(s):
    if s is None:
        return ""
    s = int(s)
    return f"{s} с" if s < 60 else f"{s // 60} мин {s % 60:02d} с" if s < 3600 else f"{s // 3600} ч {(s % 3600) // 60} мин"


def clean_error(msg):
    msg = re.sub(r"\s+", " ", str(msg or "")).strip()
    path = os.environ.get("METUBE_PATH", "").strip("/")
    if path:
        msg = msg.replace(path, "…")
    return msg[:300] or "ошибка загрузки"


def _row(r):
    d = dict(r)
    for k, empty in (("opts", {}), ("files", []), ("murls", [])):
        try:
            d[k] = json.loads(d.get(k) or "") if d.get(k) else empty
        except ValueError:
            d[k] = empty
    return d


def _chat_target(j):
    return j["origin"] == "chat" or bool(j["opts"].get("to_chat"))


def cancel_markup(jid):
    return {"inline_keyboard": [[{"text": "✖ Отмена", "callback_data": f"dlc:{jid}"}]]}


NO_MARKUP = {"inline_keyboard": []}


class Downloads:
    def __init__(self, store, members, owner_id, bot_for, base_url=lambda: "", metube=None, root=None):
        self.s = store
        self.members = members
        self.owner_id = owner_id            # callable -> int | None
        self.bot_for = bot_for              # chat_id -> tg.Bot-like | None (the bot the user talks to)
        self.base_url = base_url            # callable -> the Mini App URL ("" when none)
        self.mt = metube or MeTube()
        self.root = root or os.environ.get("METUBE_FILES", "/downloads")
        self.min_free_gb = float(os.environ.get("DL_MIN_FREE_GB", "") or 50)
        self.clock = time.time
        self.ns = time.time_ns
        self.tlock = threading.RLock()      # worker + cancel/delete/send
        self.sublock = threading.Lock()     # limit check + insert
        self.wake = threading.Event()
        self.pool = ThreadPoolExecutor(2, thread_name_prefix="dl-send")
        self._futs = []
        self._delivering = set()
        self._edited = {}                   # job id -> (ts, text)
        self._mine = set()                  # leads submitted by this process
        self._stable = {}                   # job id -> consecutive all-terminal ticks (playlists)
        self._hk = 0.0
        self._resumed = False
        self._thread = None

    # ------------------------------------------------------------ db plumbing --
    def _get(self, jid):
        rows = self.s.q("SELECT * FROM downloads WHERE id=?", int(jid))
        return _row(rows[0]) if rows else None

    def _rows(self, where="", *args):
        return [_row(r) for r in self.s.q("SELECT * FROM downloads " + where, *args)]

    def _upd(self, jid, **kw):
        if not kw:
            return
        vals = [json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v for v in kw.values()]
        self.s.q(f"UPDATE downloads SET {', '.join(k + '=?' for k in kw)} WHERE id=?", *vals, int(jid))

    def _group(self, lead_id):
        return self._rows("WHERE lead=? ORDER BY id", int(lead_id))

    def _upd_group(self, lead_id, **kw):
        """Apply to the lead and to every follower that has not been canceled."""
        for g in self._group(lead_id):
            if g["status"] != "canceled":
                self._upd(g["id"], **kw)

    # ------------------------------------------------------------------ submit --
    def free_gb(self):
        try:
            return shutil.disk_usage(self.root).free / 1e9
        except OSError as e:
            log.warning("disk_usage(%s): %s", self.root, e)
            return float("inf")

    def submit(self, uid, url, preset, raw_opts=None, origin="app", chat_id=None, chat_msg_id=None):
        """-> job JSON. Raises DlError (400 bad input, 429 limit, 503 disk_full)."""
        url = url.strip() if isinstance(url, str) else url
        if not valid_url(url):
            raise DlError(400, "bad_url", "Нужна ссылка http:// или https://.")
        if preset not in PRESETS:
            raise DlError(400, "bad_preset", "Неизвестный пресет.")
        opts = norm_opts(raw_opts)
        if is_playlist(url) and not opts["playlist"]:
            raise DlError(400, "playlist", "Это плейлист — включи «Плейлист (до 10)»")
        if self.free_gb() < self.min_free_gb:
            raise DlError(503, "disk_full", "На сервере мало места — попробуй позже.")
        if origin == "chat":
            opts["to_chat"] = True
        now = int(self.clock())
        with self.sublock:
            if uid != self.owner_id():
                self._check_limits(uid, now)
            with self.s.lock:
                cur = self.s.db.execute(
                    "INSERT INTO downloads(uid,url,preset,opts,origin,chat_id,chat_msg_id,created_ts) "
                    "VALUES(?,?,?,?,?,?,?,?)",
                    (int(uid), url, preset, json.dumps(opts), origin,
                     (chat_id or uid) if opts["to_chat"] else chat_id, chat_msg_id, now))
                jid = cur.lastrowid
        self.wake.set()
        return self.job_json(self._get(jid))

    def _check_limits(self, uid, now):
        lim = limits()
        n = lambda sql, *a: self.s.q(sql, *a)[0][0]                       # noqa: E731
        if n("SELECT COUNT(*) FROM downloads WHERE uid=? AND status IN ('queued','running')", uid) >= lim["active"]:
            raise DlError(429, "limit", f"Одновременно можно качать не больше {lim['active']} — дождись завершения.")
        if n("SELECT COUNT(*) FROM downloads WHERE uid=? AND created_ts>=? AND status!='canceled'",
             uid, now - 3600) >= lim["per_hour"]:
            raise DlError(429, "limit", f"Лимит: не больше {lim['per_hour']} загрузок в час. Попробуй позже.")
        if n("SELECT COUNT(*) FROM downloads WHERE uid=? AND created_ts>=? AND status!='canceled'",
             uid, now - DAY) >= lim["per_day"]:
            raise DlError(429, "limit", f"Лимит: не больше {lim['per_day']} загрузок в сутки.")

    # ------------------------------------------------------------- MeTube body --
    def _add_body(self, j, phase):
        p, o = PRESETS[j["preset"]], j["opts"]
        body = {"url": j["url"], "folder": f"u{j['uid']}", "auto_start": True,
                "playlist_item_limit": PLAYLIST_MAX if o.get("playlist") else 1}
        if phase == "subs":
            body.update(download_type="captions", format="srt", quality="best",
                        subtitle_language=o["subs"], subtitle_mode="prefer_manual")
            return body
        if p["type"] == "video":
            body.update(download_type="video", format="mp4", codec="h264", quality=p["quality"])
        else:
            body.update(download_type="audio", format=p["format"], quality=p["quality"])
        if o.get("subs"):
            body.update(subtitle_language=o["subs"], subtitle_mode="prefer_manual")
        if o.get("clip"):
            body.update(clip_start=o["clip"]["start"], clip_end=o["clip"]["end"])
        return body

    # ------------------------------------------------------------------- worker --
    def start(self):
        if self._thread is None:
            self._thread = threading.Thread(target=self.run, daemon=True, name="downloads")
            self._thread.start()

    def run(self):
        while True:
            busy = False
            try:
                busy = self.tick()
            except Exception:                                   # noqa: BLE001
                log.exception("downloads tick failed")
            self.wake.wait(2 if busy else 15)
            self.wake.clear()

    def tick(self):
        """One pass. -> True while anything is queued or running."""
        with self.tlock:
            now = self.clock()
            if not self._resumed:
                self._resumed = True
                for j in self._rows("WHERE status='done' AND delivery='' ORDER BY id"):
                    if _chat_target(j) and j["files"]:
                        self._queue_delivery(j["id"])
            active = self._rows("WHERE status IN ('queued','running') ORDER BY id")
            leads = [j for j in active if j["status"] == "running" and j["lead"] == j["id"]]
            if leads:
                try:
                    hist = self.mt.history()
                except MeTubeError as e:
                    log.info("history: %s", e)
                else:
                    for lead in leads:
                        try:
                            self._advance(lead, hist, now)
                        except Exception:                      # noqa: BLE001
                            log.exception("advance job %s", lead["id"])
            self._assign(now)
            if now - self._hk >= HK_EVERY:
                self._hk = now
                self._housekeeping(now)
            return bool(self.s.q("SELECT 1 FROM downloads WHERE status IN ('queued','running') LIMIT 1"))

    # -- matching --
    def _match(self, lead, hist):
        folder, since = lead["folder"] or f"u{lead['uid']}", lead["submit_ns"] - 2 * 10**9
        want_caps = lead["phase"] == "subs"
        out, seen = [], set()
        for lst in ("queue", "pending", "done"):
            for i in hist.get(lst) or []:
                if (i.get("folder") or "").strip("/") != folder or (i.get("timestamp") or 0) < since:
                    continue
                if (i.get("download_type") == "captions") != want_caps:
                    continue
                if not lead["opts"].get("playlist") and i.get("url") != lead["url"]:
                    continue
                key = i.get("url") or id(i)
                if key not in seen:
                    seen.add(key)
                    out.append(i)
        return out

    # -- one running lead --
    def _advance(self, lead, hist, now):
        jid = lead["id"]
        items = self._match(lead, hist)
        if not items:
            if jid not in self._mine:
                return self._fail(lead, "прервано перезапуском")
            if now - (lead["started_ts"] or now) > STALE_START_S:
                return self._fail(lead, "MeTube не начал загрузку — ссылка не распознана?")
            return None
        n = len(items)
        pcts = [100.0 if i.get("status") == "finished" else float(i.get("percent") or 0) for i in items]
        etas = [i.get("eta") for i in items if i.get("status") not in TERMINAL and i.get("eta") is not None]
        first = items[0].get("title") or lead["title"]
        upd = {"percent": round(sum(pcts) / n, 1), "eta": int(max(etas)) if etas else None,
               "size": sum(int(i.get("size") or 0) for i in items) if lead["phase"] != "subs" else lead["size"],
               "title": first if n == 1 else f"{first} (+{n - 1})",
               "murls": [i.get("url") for i in items if i.get("url")]}
        if lead["phase"] == "subs":
            upd.update(percent=0.0, title=lead["title"], murls=lead["murls"])
        self._upd_group(jid, **upd)
        if not all(i.get("status") in TERMINAL for i in items):
            self._stable.pop(jid, None)
            return self._progress_group(jid)
        if lead["opts"].get("playlist"):           # more entries may still be appearing
            self._stable[jid] = self._stable.get(jid, 0) + 1
            if self._stable[jid] < 2:
                return None
        self._stable.pop(jid, None)
        fin = [i for i in items if i.get("status") == "finished"]
        if lead["phase"] == "subs":
            return self._subs_done(lead, fin)
        if not fin:
            msg = next((i.get("msg") or i.get("error") for i in items if i.get("msg") or i.get("error")), "")
            return self._fail(lead, clean_error(msg))
        return self._finish(lead, fin, first)

    def _entry(self, rel, kind):
        path = self._safe_path(rel)
        if not path:
            return None
        return {"rel": rel, "name": os.path.basename(rel), "size": os.path.getsize(path), "kind": kind}

    @staticmethod
    def _rel(item, name):
        folder = (item.get("folder") or "").strip("/")
        return f"{folder}/{name}" if folder else name

    def _sub_files(self, item):
        out = []
        for sf in item.get("subtitle_files") or []:
            name = sf.get("filename") if isinstance(sf, dict) else sf
            if isinstance(name, str) and name:
                e = self._entry(self._rel(item, name), "subs")
                if e:
                    out.append(e)
        return out

    def _subs_done(self, lead, fin):
        files = []
        for i in fin:
            if i.get("filename"):
                e = self._entry(self._rel(i, i["filename"]), "subs")
                if e:
                    files.append(e)
        self._start_main(lead, files)

    def _start_main(self, lead, files):
        """Submit the video/audio item after the captions item finished."""
        ns = self.ns()
        try:
            self.mt.add(self._add_body(lead, "main"))
        except MeTubeError as e:
            return self._fail(lead, "ошибка очереди, повтори" if str(e) == "queue_conflict" else clean_error(e))
        self._upd_group(lead["id"], phase="main", submit_ns=ns - 10**9, files=files)

    def _finish(self, lead, fin, title):
        kind = "audio" if PRESETS[lead["preset"]]["type"] == "audio" else "video"
        files, seen = list(lead["files"]), {f["rel"] for f in lead["files"]}
        for i in fin:
            rels = []
            if i.get("filename"):
                rels.append((self._rel(i, i["filename"]), kind))
            for e in self._sub_files(i):
                rels.append((e["rel"], "subs"))
            for rel, k in rels:
                if rel in seen:
                    continue
                e = self._entry(rel, k)
                if e:
                    seen.add(rel)
                    files.append(e)
        if not any(f["kind"] != "subs" for f in files):
            return self._fail(lead, "файл не найден на диске")
        total = sum(f["size"] for f in files if f["kind"] != "subs")
        self._upd_group(lead["id"], status="done", files=files, size=total, percent=100.0, eta=None,
                        error="", finished_ts=int(self.clock()))
        for g in self._group(lead["id"]):
            if g["status"] != "done":
                continue
            self._notify(g, f"✅ Готово: <b>{esc(g['title'] or title)}</b>", NO_MARKUP, force=True)
            if _chat_target(g):
                self._queue_delivery(g["id"])

    def _fail(self, lead, msg):
        self._upd_group(lead["id"], status="error", error=msg, eta=None, finished_ts=int(self.clock()))
        for g in self._group(lead["id"]):
            if g["status"] == "error":
                self._notify(g, f"❌ {esc(msg)}", NO_MARKUP, force=True)

    # -- queued jobs --
    def _assign(self, now):
        queued = self._rows("WHERE status='queued' ORDER BY id")
        if not queued:
            return
        leads = [j for j in self._rows("WHERE status='running' AND lead=id ORDER BY id")]
        settling = [j for j in self._rows("WHERE status='done' AND delivery='' ORDER BY id")
                    if _chat_target(j) and j["files"]]
        for j in queued:
            fresh = self._get(j["id"])
            if fresh is None or fresh["status"] != "queued":
                continue                                    # canceled meanwhile
            j = fresh
            key = optkey(j["opts"])
            follow = next((L for L in leads if L["url"] == j["url"] and L["preset"] == j["preset"]
                           and optkey(L["opts"]) == key), None)
            if follow is not None:
                cur = self._get(follow["id"]) or follow
                self._upd(j["id"], lead=cur["id"], status="running", started_ts=int(now), phase=cur["phase"],
                          percent=cur["percent"], title=cur["title"], size=cur["size"], eta=cur["eta"],
                          submit_ns=cur["submit_ns"], murls=cur["murls"], folder=cur["folder"])
                continue
            if any(self._conflict(j, o) for o in leads + settling):
                continue
            phase = "subs" if j["opts"].get("subs") else "main"
            ns = self.ns()
            try:
                self.mt.add(self._add_body(j, phase))
            except MeTubeError as e:
                if e.transient and now - j["created_ts"] < QUEUE_RETRY_S:
                    continue
                msg = ("ошибка очереди, повтори" if str(e) == "queue_conflict"
                       else "загрузчик недоступен, попробуй позже" if e.transient else clean_error(e))
                self._upd(j["id"], status="error", error=msg, finished_ts=int(now))
                self._notify(self._get(j["id"]), f"❌ {esc(msg)}", NO_MARKUP, force=True)
                continue
            self._upd(j["id"], lead=j["id"], status="running", started_ts=int(now), phase=phase,
                      submit_ns=ns - 10**9, murls=[j["url"]], folder=f"u{j['uid']}")
            self._mine.add(j["id"])
            leads.append(self._get(j["id"]))

    @staticmethod
    def _conflict(j, o):
        """MeTube cannot hold two items for one URL; a playlist expands to unknown URLs, so
        it also excludes anything else in the same folder."""
        if o["url"] == j["url"]:
            return True
        return o["uid"] == j["uid"] and (j["opts"].get("playlist") or o["opts"].get("playlist"))

    # -- progress messages --
    def _progress_group(self, lead_id):
        for g in self._group(lead_id):
            if g["status"] == "running":
                self._notify(g, self.progress_text(g))

    @staticmethod
    def progress_text(j):
        pct = int(j["percent"] or 0)
        bar = "▰" * (pct // 10) + "▱" * (10 - pct // 10)
        t = [f"⏳ <b>{esc(j['title'] or j['url'])}</b>", f"{bar} {pct}%"]
        extra = " · ".join(x for x in (fmt_size(j["size"]) if j["size"] else "",
                                       f"ещё {fmt_eta(j['eta'])}" if j["eta"] is not None else "") if x)
        if j["opts"].get("subs") and j["phase"] == "subs":
            extra = "субтитры…"
        return "\n".join(t) + (f" · {extra}" if extra else "")

    def _notify(self, j, text, markup=None, force=False):
        if not j or not j.get("chat_msg_id"):
            return
        now = self.clock()
        last = self._edited.get(j["id"])
        if not force and last and (now - last[0] < EDIT_EVERY or last[1] == text):
            return
        chat = j["chat_id"] or j["uid"]
        bot = self.bot_for(chat)
        if bot is None:
            return
        try:
            bot.edit(chat, j["chat_msg_id"], text, reply_markup=markup if markup is not None else cancel_markup(j["id"]))
        except Exception as e:                                  # noqa: BLE001
            log.info("progress edit %s: %s", j["id"], e)
        self._edited[j["id"]] = (now, text)

    # ---------------------------------------------------------------- delivery --
    def _queue_delivery(self, jid):
        if jid in self._delivering:
            return
        self._delivering.add(jid)
        self._futs = [f for f in self._futs if not f.done()]
        self._futs.append(self.pool.submit(self._deliver, jid))

    def drain(self, timeout=30):
        """Wait for pending deliveries (tests, shutdown)."""
        for f in list(self._futs):
            f.result(timeout)

    def _deliver(self, jid):
        lead = None
        try:
            j = self._get(jid)
            if not j or j["status"] != "done" or j["delivery"]:
                return
            lead = j["lead"]
            outcome = self._send_files(j)
            self._set_delivered(j, outcome)
        except Exception:                                       # noqa: BLE001
            log.exception("delivery of job %s failed", jid)
            j = self._get(jid)
            if j and j["status"] == "done" and not j["delivery"]:
                self._upd(jid, delivery="link")                 # settle: never block the URL forever
        finally:
            self._delivering.discard(jid)
        if lead:
            self._maybe_purge(lead)

    def _set_delivered(self, j, outcome):
        cur = self._get(j["id"])
        if cur is None:
            return
        keep = "upload" if cur["delivery"] == "upload" and outcome != "none" else outcome
        self._upd(j["id"], delivery=keep, delivered_ts=int(self.clock()))

    def _send_files(self, j):
        """Send every file of `j` to its chat. -> 'upload' (all as uploads) | 'link' | 'none'."""
        chat = j["chat_id"] or j["uid"]
        bot = self.bot_for(chat)
        if bot is None:
            return "link"
        limit = limits()["chat_max_mb"] * 1024 * 1024
        outcome = "upload"
        sent = 0
        for idx, f in sorted(enumerate(j["files"]), key=lambda t: t[1]["kind"] == "subs"):
            path = self._safe_path(f["rel"])
            if path is None:
                outcome = "link"
                continue
            ok = False
            if os.path.getsize(path) <= limit:
                try:
                    self._upload(bot, chat, path, f, j)
                    ok = True
                except (TelegramError, OSError) as e:
                    log.info("upload job %s: %s", j["id"], e)
            if not ok:
                outcome = "link"
                self._send_link(bot, chat, j, idx, f)
            sent += 1
        return outcome if sent else "none"

    @staticmethod
    def _upload(bot, chat, path, f, j):
        ext = os.path.splitext(path)[1].lower()
        cap = caption(j["title"] or os.path.splitext(f["name"])[0]) if f["kind"] != "subs" else "Субтитры"
        if f["kind"] == "video" and ext == ".mp4":
            return bot.upload("sendVideo", "video", path, chat_id=chat, caption=cap, parse_mode="HTML",
                              supports_streaming="true")
        if f["kind"] == "audio" and ext in (".mp3", ".m4a"):
            return bot.upload("sendAudio", "audio", path, chat_id=chat, caption=cap, parse_mode="HTML")
        return bot.upload("sendDocument", "document", path, chat_id=chat, caption=cap, parse_mode="HTML")

    def file_url(self, j, idx):
        ttl = max(60, int((j["finished_ts"] or self.clock()) + limits()["ttl_min"] * 60 - self.clock()))
        rel = links.sign("file", f"{j['id']}:{idx}", j["uid"], ttl, store=self.s)
        return links.absolute(rel, self.base_url())

    def _send_link(self, bot, chat, j, idx, f):
        ttl = limits()["ttl_min"]
        big = f["size"] > limits()["chat_max_mb"] * 1024 * 1024
        url = self.file_url(j, idx)
        text = (f"📥 <b>{esc(j['title'] or f['name'])}</b>\n"
                + (f"<a href=\"{esc(url)}\">{esc(f['name'])}</a>" if url.startswith("http") else esc(f["name"]))
                + f" · {fmt_size(f['size'])}\n"
                + ("<i>Файл больше лимита Telegram для ботов — скачай по ссылке. </i>" if big else "")
                + f"<i>Файлы удаляются через {ttl} мин.</i>")
        try:
            bot.send(chat, text)
        except TelegramError as e:
            log.info("link message to %s: %s", chat, e)

    def _maybe_purge(self, lead_id):
        """Every requester got the file as a Telegram upload -> delete the item (and file) in MeTube."""
        with self.tlock:
            rows = [g for g in self._group(lead_id) if g["status"] != "canceled"]
            if not rows or not any(g["files"] for g in rows):       # nothing left / already purged
                return
            if any(g["status"] != "done" or g["delivery"] != "upload" for g in rows):
                return
            ids = next((g["murls"] for g in rows if g["murls"]), [rows[0]["url"]])
            try:
                self.mt.delete(ids, "done")
            except MeTubeError as e:
                log.warning("purge %s: %s", ids, e)
                return
            for g in rows:
                self._upd(g["id"], files=[])

    # ------------------------------------------------------------ housekeeping --
    def _housekeeping(self, now):
        ttl = limits()["ttl_min"] * 60
        for j in self._rows("WHERE status='done' AND finished_ts>? ORDER BY id", int(now) - DAY):
            if not j["files"]:
                continue
            gone = any(self._safe_path(f["rel"]) is None for f in j["files"])
            if gone or now > (j["finished_ts"] or 0) + ttl + 120:
                self._upd(j["id"], status="expired", files=[])
        for k in [k for k in self._edited if k not in {r[0] for r in self.s.q(
                "SELECT id FROM downloads WHERE status IN ('queued','running')")}]:
            self._edited.pop(k, None)

    # --------------------------------------------------------------- user ops --
    def _own(self, uid, jid):
        j = self._get(jid)
        if j is None or j["uid"] != uid:
            raise DlError(404, "not_found", "Загрузка не найдена.")
        return j

    def _promote(self, gone):
        """`gone` was the lead of a shared item: hand the item to another job of the group."""
        rest = [g for g in self._group(gone["lead"]) if g["id"] != gone["id"] and g["status"] != "canceled"]
        if not rest:
            return None
        new = rest[0]
        for g in rest:
            self._upd(g["id"], lead=new["id"])
        self._upd(new["id"], phase=gone["phase"], submit_ns=gone["submit_ns"], murls=gone["murls"],
                  folder=gone["folder"])
        if gone["id"] in self._mine:
            self._mine.add(new["id"])
        return new

    def _cancel_row(self, j):
        if j["status"] == "running":
            if j["lead"] == j["id"] and not self._promote(j):
                try:
                    self.mt.delete(j["murls"] or [j["url"]], "queue")
                except MeTubeError as e:
                    log.warning("cancel in MeTube: %s", e)
        self._upd(j["id"], status="canceled", eta=None, finished_ts=int(self.clock()))
        self._notify(self._get(j["id"]), "✖ Отменено.", NO_MARKUP, force=True)

    def cancel(self, uid, jid):
        with self.tlock:
            j = self._own(uid, jid)
            if j["status"] not in ACTIVE:
                raise DlError(409, "not_active", "Загрузка уже завершена.")
            self._cancel_row(j)
            return self.job_json(self._get(jid))

    def delete(self, uid, jid):
        with self.tlock:
            j = self._own(uid, jid)
            if j["status"] in ACTIVE:
                self._cancel_row(j)
                j = self._get(jid)
            shared = False
            if j["lead"]:
                if j["lead"] == j["id"]:
                    shared = self._promote(j) is not None
                else:
                    shared = any(g["id"] != j["id"] and g["status"] != "canceled" for g in self._group(j["lead"]))
            if not shared and j["status"] == "done" and j["files"]:
                try:
                    self.mt.delete(j["murls"] or [j["url"]], "done")
                except MeTubeError as e:
                    log.warning("delete in MeTube: %s", e)
            self.s.q("DELETE FROM downloads WHERE id=?", j["id"])
            self._edited.pop(j["id"], None)
            return {"ok": True}

    def send(self, uid, jid):
        """The "В чат" button: send a finished job to the caller's chat (49 MB rule, else a link)."""
        with self.tlock:
            j = self._own(uid, jid)
        if j["status"] != "done" or not j["files"]:
            raise DlError(409, "not_ready", "Файла уже нет или загрузка не закончена.")
        if self.bot_for(j["chat_id"] or j["uid"]) is None:
            raise DlError(503, "no_bot", "Бот сейчас недоступен.")
        outcome = self._send_files(j)
        self._set_delivered(j, outcome)
        self._maybe_purge(j["lead"] or j["id"])
        return self.job_json(self._get(jid))

    # --------------------------------------------------------------- serving --
    def _safe_path(self, rel, folder=None):
        """Real path of `rel` (relative to the downloads root) or None. Confined to
        <root>/<folder>, folder = `u<digits>` (the rel's first component by default)."""
        if not isinstance(rel, str) or not rel or "\x00" in rel or rel.startswith("/"):
            return None
        first = rel.split("/", 1)[0]
        if not re.fullmatch(r"u\d+", first) or (folder is not None and first != folder):
            return None
        root = os.path.realpath(self.root)
        base = os.path.realpath(os.path.join(root, first))
        full = os.path.realpath(os.path.join(root, rel))
        if not full.startswith(base + os.sep) or not os.path.isfile(full):
            return None
        return full

    def file_for_link(self, uid, ident):
        """(path, name) for a signed `file` link, re-checking everything. Raises DlError."""
        try:
            jid, idx = (int(x) for x in str(ident).split(":"))
        except ValueError:
            raise DlError(404, "not_found") from None
        j = self._get(jid)
        if j is None:
            raise DlError(404, "not_found")
        if j["uid"] != uid:
            raise DlError(403, "forbidden")
        if uid != self.owner_id() and not self.members.has(uid, "youtube"):
            raise DlError(403, "no_access", "Этот сервис недоступен: нет активной подписки.")
        if j["status"] != "done" or not 0 <= idx < len(j["files"]):
            raise DlError(404, "not_found")
        path = self._safe_path(j["files"][idx]["rel"], j["folder"] or f"u{j['uid']}")
        if path is None:
            raise DlError(404, "not_found", "Файл уже удалён.")
        return path, j["files"][idx]["name"]

    # ------------------------------------------------------------------- views --
    def job_json(self, j, urls=True):
        files = []
        for idx, f in enumerate(j["files"]):
            d = {"name": f["name"], "size": f["size"], "kind": f["kind"]}
            if urls:
                d["url"] = self.file_url(j, idx)
            files.append(d)
        done = j["status"] == "done"
        return {"id": j["id"], "url": j["url"], "preset": j["preset"],
                "preset_label": PRESETS[j["preset"]]["label"] if j["preset"] in PRESETS else j["preset"],
                "title": j["title"], "status": j["status"], "percent": int(j["percent"] or 0),
                "size": j["size"], "eta": j["eta"], "error": j["error"], "created_ts": j["created_ts"],
                "finished_ts": j["finished_ts"],
                "expires_ts": (j["finished_ts"] or 0) + limits()["ttl_min"] * 60 if done else None,
                "files": files, "can_send": bool(done and files),
                "delivered": j["delivery"] == "upload", "origin": j["origin"]}

    def jobs_for(self, uid):
        rows = self._rows("WHERE uid=? AND created_ts>=? ORDER BY id DESC", int(uid), int(self.clock()) - DAY)
        return [self.job_json(j) for j in rows]

    def admin(self, name_of):
        now = int(self.clock())
        rows = self._rows("WHERE created_ts>=? ORDER BY id DESC", now - DAY)
        jobs = []
        for j in rows:
            d = self.job_json(j, urls=False)
            d.update(uid=j["uid"], user=name_of(j["uid"]))
            jobs.append(d)
        free = self.free_gb()
        return {"jobs": jobs, "disk_free_gb": None if free == float("inf") else round(free, 1),
                "active": sum(1 for j in rows if j["status"] in ACTIVE),
                "today": {"jobs": len(rows), "bytes": sum(j["size"] for j in rows if j["status"] == "done")},
                "limits": limits()}
