"""Multi-user video/audio downloader (service `youtube`) on top of MeTube.

MeTube keys its queue and history by URL, so one global queue cannot serve
several people. This module keeps its own job table (`downloads`) and feeds
MeTube from it:

  * at most one MeTube item per URL at a time; identical requests (same url +
    preset + options) that arrive while one runs share it (fan-out), a
    different preset for the same URL waits;
  * every user downloads into their own MeTube folder `u<uid>` (visible
    read-only in this container as /downloads/u<uid>/);
  * ONE worker thread polls MeTube's /history and updates the jobs; finished
    files are fetched by the user from the Mini App through signed
    /dl/<token> links (there is no Telegram upload). The janitor deletes the
    files after 30 min; an explicit delete through the API also removes the
    item in MeTube;
  * every URL goes through canonical_url() before it is stored or sent to
    MeTube: MeTube rewrites youtu.be/shorts/... to youtube.com/watch?v=..., and
    jobs are matched to MeTube items by URL.

Presets are defined once here (presets_payload) and used by the Mini App API
and by both bots (which only hand the link over to the Mini App). MeTube URLs
are never handed out -- files are served by webapp's /dl/<token>.
"""
import json
import logging
import os
import re
import shutil
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import parse_qs, parse_qsl, urlencode, urlsplit

import i18n
import links

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
HK_EVERY = 15.0
DAY = 86400


def _int_env(name, default):
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


def limits():
    return {"active": _int_env("DL_ACTIVE", 2), "per_hour": _int_env("DL_PER_HOUR", 10),
            "per_day": _int_env("DL_PER_DAY", 30), "ttl_min": _int_env("METUBE_TTL_MIN", 30)}


def presets_payload(lang="ru"):
    """GET /api/dl/presets, and what the bots build their buttons from."""
    best = i18n.tr(lang, "dl.preset.best")
    return {"video": [{"id": i, "label": best if i == "vbest" else lb} for i, lb, _q in VIDEO],
            "audio": [{"id": i, "label": lb} for i, lb, _f, _q in AUDIO],
            "extras": {"subs": list(SUBS), "clip": True, "playlist_max": PLAYLIST_MAX},
            "limits": limits(),
            "sites": [i18n.tr(lang, "dl.site.vk") if x == "VK Видео" else x for x in SITES]}


class DlError(Exception):
    """`message` is an i18n catalog key (or plain text); `.message` is its Russian
    rendering, `render(lang)` any language."""

    def __init__(self, status, code, message=None, **vars):
        super().__init__(code)
        self.status, self.code, self.key, self.vars = status, code, message, vars

    def render(self, lang="ru"):
        if not self.key:
            return self.code
        return i18n.tr(lang, self.key, **self.vars) if i18n.has(self.key) else self.key

    @property
    def message(self):
        return self.render("ru")


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
            raise MeTubeError(msg or i18n.tr("ru", "dl.job.rejected"))
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


YT_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com",
            "youtu.be", "www.youtu.be", "youtube-nocookie.com", "www.youtube-nocookie.com"}
YT_ID = re.compile(r"[A-Za-z0-9_-]{6,32}\Z")
YT_ID_PATHS = ("shorts", "live", "embed", "v")           # /<kind>/<id>
TRACKING = ("si", "is", "feature", "pp", "ab_channel", "t", "utm_")


def _tracking(key):
    return any(key == k or (k.endswith("_") and key.startswith(k)) for k in TRACKING)


def canonical_url(u, keep_list=False):
    """One spelling per video, the one MeTube itself reports back in /history.

    YouTube (youtu.be, /shorts, /live, /embed, /v, m./music., watch?v=) ->
    https://www.youtube.com/watch?v=<id>; a pure playlist ->
    https://www.youtube.com/playlist?list=<id>. `list`/`index` are kept only with
    keep_list (a playlist was asked for), so a watch?v=X&list=Y share downloads
    just the video otherwise. Tracking parameters (si, is, feature, pp, ab_channel,
    utm_*) and `t` (MeTube would treat it as a clip start) are dropped. Other sites
    only lose utm_*/si; anything unparseable comes back as it was."""
    if not isinstance(u, str):
        return u
    u = u.strip()
    try:
        p = urlsplit(u)
        host = (p.hostname or "").lower()
    except ValueError:
        return u
    if host not in YT_HOSTS:
        if not p.query:
            return u
        parts = p.query.split("&")
        kept = [x for x in parts if x.split("=", 1)[0] not in ("si",) and not x.startswith("utm_")]
        return u if kept == parts else p._replace(query="&".join(kept)).geturl()
    q = parse_qsl(p.query, keep_blank_values=False)
    qd = {k: v for k, v in q if not _tracking(k)}
    segs = [x for x in p.path.split("/") if x]
    vid = None
    if host.endswith("youtu.be"):
        vid = segs[0] if segs else None
    elif len(segs) >= 2 and segs[0] in YT_ID_PATHS:
        vid = segs[1]
    elif segs == ["watch"]:
        vid = qd.get("v")
    lst = qd.get("list")
    if vid and YT_ID.match(vid):
        out = {"v": vid}
        if keep_list and lst:
            out["list"] = lst
        return "https://www.youtube.com/watch?" + urlencode(out)
    if segs == ["playlist"] and lst:
        return "https://www.youtube.com/playlist?" + urlencode({"list": lst})
    return u


def _secs(v, field):
    if isinstance(v, bool):
        raise DlError(400, "bad_request", "dl.err.bad_field", field=field)
    if isinstance(v, (int, float)):
        out = float(v)
    elif isinstance(v, str) and re.fullmatch(r"\d+(:\d{1,2}){0,2}", v.strip()):
        out = 0.0
        for part in v.strip().split(":"):
            out = out * 60 + int(part)
    else:
        raise DlError(400, "bad_request", "dl.err.bad_field", field=field)
    if out < 0 or out > DAY:
        raise DlError(400, "bad_request", "dl.err.range", field=field)
    return int(out)


def norm_opts(raw):
    raw = raw or {}
    subs = raw.get("subs") or None
    if subs not in (None,) + SUBS:
        raise DlError(400, "bad_request", "dl.err.subs")
    clip = raw.get("clip") or None
    if clip is not None:
        if not isinstance(clip, dict):
            raise DlError(400, "bad_request", "dl.err.clip_obj")
        start, end = _secs(clip.get("start", 0), "start"), _secs(clip.get("end"), "end")
        if end <= start:
            raise DlError(400, "bad_request", "dl.err.clip_order")
        clip = {"start": start, "end": end}
    if raw.get("playlist") is not None and not isinstance(raw["playlist"], bool):
        raise DlError(400, "bad_request", "dl.err.playlist_bool")
    return {"subs": subs, "clip": clip, "playlist": bool(raw.get("playlist"))}


def optkey(opts):
    """Two requests share a MeTube item only when this matches."""
    return json.dumps([opts.get("subs"), opts.get("clip"), bool(opts.get("playlist"))], sort_keys=True)


def clean_error(msg):
    msg = re.sub(r"\s+", " ", str(msg or "")).strip()
    path = os.environ.get("METUBE_PATH", "").strip("/")
    if path:
        msg = msg.replace(path, "…")
    return msg[:300] or i18n.tr("ru", "dl.job.default")


def _row(r):
    d = dict(r)
    for k, empty in (("opts", {}), ("files", []), ("murls", [])):
        try:
            d[k] = json.loads(d.get(k) or "") if d.get(k) else empty
        except ValueError:
            d[k] = empty
    return d


class Downloads:
    def __init__(self, store, members, owner_id, base_url=lambda: "", metube=None, root=None):
        self.s = store
        self.members = members
        self.owner_id = owner_id            # callable -> int | None
        self.base_url = base_url            # callable -> the Mini App URL ("" when none)
        self.mt = metube or MeTube()
        self.root = root or os.environ.get("METUBE_FILES", "/downloads")
        self.min_free_gb = float(os.environ.get("DL_MIN_FREE_GB", "") or 50)
        self.clock = time.time
        self.ns = time.time_ns
        self.tlock = threading.RLock()      # worker + cancel/delete
        self.sublock = threading.Lock()     # limit check + insert
        self.wake = threading.Event()
        self._mine = set()                  # leads submitted by this process
        self._stable = {}                   # job id -> consecutive all-terminal ticks (playlists)
        self._hk = 0.0
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

    def submit(self, uid, url, preset, raw_opts=None, origin="app", chat_id=None, chat_msg_id=None, lang="ru"):
        """-> job JSON. Raises DlError (400 bad input, 429 limit, 503 disk_full)."""
        url = url.strip() if isinstance(url, str) else url
        if not valid_url(url):
            raise DlError(400, "bad_url", "dl.err.bad_url")
        if preset not in PRESETS:
            raise DlError(400, "bad_preset", "dl.err.bad_preset")
        opts = norm_opts(raw_opts)
        url = canonical_url(url, keep_list=opts["playlist"])
        if is_playlist(url) and not opts["playlist"]:
            raise DlError(400, "playlist", "dl.err.playlist")
        if self.free_gb() < self.min_free_gb:
            raise DlError(503, "disk_full", "dl.err.disk")
        now = int(self.clock())
        with self.sublock:
            if uid != self.owner_id():
                self._check_limits(uid, now)
            with self.s.lock:
                cur = self.s.db.execute(
                    "INSERT INTO downloads(uid,url,preset,opts,origin,chat_id,chat_msg_id,created_ts) "
                    "VALUES(?,?,?,?,?,?,?,?)",
                    (int(uid), url, preset, json.dumps(opts), origin, chat_id, chat_msg_id, now))
                jid = cur.lastrowid
        self.wake.set()
        return self.job_json(self._get(jid), lang=lang)

    def _check_limits(self, uid, now):
        lim = limits()
        n = lambda sql, *a: self.s.q(sql, *a)[0][0]                       # noqa: E731
        if n("SELECT COUNT(*) FROM downloads WHERE uid=? AND status IN ('queued','running')", uid) >= lim["active"]:
            raise DlError(429, "limit", "dl.err.limit_active", n=lim["active"])
        if n("SELECT COUNT(*) FROM downloads WHERE uid=? AND created_ts>=? AND status!='canceled'",
             uid, now - 3600) >= lim["per_hour"]:
            raise DlError(429, "limit", "dl.err.limit_hour", n=lim["per_hour"])
        if n("SELECT COUNT(*) FROM downloads WHERE uid=? AND created_ts>=? AND status!='canceled'",
             uid, now - DAY) >= lim["per_day"]:
            raise DlError(429, "limit", "dl.err.limit_day", n=lim["per_day"])

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
        """MeTube items belonging to `lead`: same folder, added after the submit, same kind
        (captions or not) and -- for a single video -- the same URL once both are canonical
        (MeTube may report another spelling than the one it was given). Failing that, one
        unclaimed item of that folder and kind is taken as ours."""
        folder, since = lead["folder"] or f"u{lead['uid']}", lead["submit_ns"] - 2 * 10**9
        want_caps = lead["phase"] == "subs"
        playlist = bool(lead["opts"].get("playlist"))
        mine = canonical_url(lead["url"])
        out, seen, cand = [], set(), []
        for lst in ("queue", "pending", "done"):
            for i in hist.get(lst) or []:
                if (i.get("folder") or "").strip("/") != folder or (i.get("timestamp") or 0) < since:
                    continue
                if (i.get("download_type") == "captions") != want_caps:
                    continue
                if not playlist and canonical_url(i.get("url")) != mine:
                    cand.append(i)
                    continue
                key = i.get("url") or id(i)
                if key not in seen:
                    seen.add(key)
                    out.append(i)
        if out or playlist or not cand:
            return out
        claimed = set()
        for o in self._rows("WHERE status='running' AND lead=id AND id!=?", lead["id"]):
            if (o["folder"] or f"u{o['uid']}") == folder:
                claimed.update(canonical_url(u) for u in o["murls"] + [o["url"]])
        free = {i.get("url") or id(i): i for i in cand if canonical_url(i.get("url")) not in claimed}
        return list(free.values()) if len(free) == 1 else []

    # -- one running lead --
    def _advance(self, lead, hist, now):
        jid = lead["id"]
        items = self._match(lead, hist)
        if not items:
            if jid not in self._mine:
                return self._fail(lead, i18n.tr("ru", "dl.job.restart"))
            if now - (lead["started_ts"] or now) > STALE_START_S:
                return self._fail(lead, i18n.tr("ru", "dl.job.nostart"))
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
            upd.update(percent=0.0, title=lead["title"])
        self._upd_group(jid, **upd)
        if not all(i.get("status") in TERMINAL for i in items):
            self._stable.pop(jid, None)
            return None
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
        return self._finish(lead, fin)

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
            return self._fail(lead, i18n.tr("ru", "dl.job.queue") if str(e) == "queue_conflict" else clean_error(e))
        self._upd_group(lead["id"], phase="main", submit_ns=ns - 10**9, files=files)

    def _finish(self, lead, fin):
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
            return self._fail(lead, i18n.tr("ru", "dl.job.nofile"))
        total = sum(f["size"] for f in files if f["kind"] != "subs")
        self._upd_group(lead["id"], status="done", files=files, size=total, percent=100.0, eta=None,
                        error="", finished_ts=int(self.clock()))

    def _fail(self, lead, msg):
        self._upd_group(lead["id"], status="error", error=msg, eta=None, finished_ts=int(self.clock()))

    # -- queued jobs --
    def _assign(self, now):
        queued = self._rows("WHERE status='queued' ORDER BY id")
        if not queued:
            return
        leads = [j for j in self._rows("WHERE status='running' AND lead=id ORDER BY id")]
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
            if any(self._conflict(j, o) for o in leads):
                continue
            phase = "subs" if j["opts"].get("subs") else "main"
            ns = self.ns()
            try:
                self.mt.add(self._add_body(j, phase))
            except MeTubeError as e:
                if e.transient and now - j["created_ts"] < QUEUE_RETRY_S:
                    continue
                msg = (i18n.tr("ru", "dl.job.queue") if str(e) == "queue_conflict"
                       else i18n.tr("ru", "dl.job.unavail") if e.transient else clean_error(e))
                self._upd(j["id"], status="error", error=msg, finished_ts=int(now))
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

    def file_url(self, j, idx):
        ttl = max(60, int((j["finished_ts"] or self.clock()) + limits()["ttl_min"] * 60 - self.clock()))
        rel = links.sign("file", f"{j['id']}:{idx}", j["uid"], ttl, store=self.s)
        return links.absolute(rel, self.base_url())

    # ------------------------------------------------------------ housekeeping --
    def _housekeeping(self, now):
        ttl = limits()["ttl_min"] * 60
        for j in self._rows("WHERE status='done' AND finished_ts>? ORDER BY id", int(now) - DAY):
            if not j["files"]:
                continue
            gone = any(self._safe_path(f["rel"]) is None for f in j["files"])
            if gone or now > (j["finished_ts"] or 0) + ttl + 120:
                self._upd(j["id"], status="expired", files=[])

    # --------------------------------------------------------------- user ops --
    def _own(self, uid, jid):
        j = self._get(jid)
        if j is None or j["uid"] != uid:
            raise DlError(404, "not_found", "dl.err.not_found")
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

    def cancel(self, uid, jid, lang="ru"):
        with self.tlock:
            j = self._own(uid, jid)
            if j["status"] not in ACTIVE:
                raise DlError(409, "not_active", "dl.err.not_active")
            self._cancel_row(j)
            return self.job_json(self._get(jid), lang=lang)

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
            return {"ok": True}

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
            raise DlError(403, "no_access", "api.no_access")
        if j["status"] != "done" or not 0 <= idx < len(j["files"]):
            raise DlError(404, "not_found")
        path = self._safe_path(j["files"][idx]["rel"], j["folder"] or f"u{j['uid']}")
        if path is None:
            raise DlError(404, "not_found", "dl.err.file_gone")
        return path, j["files"][idx]["name"]

    # ------------------------------------------------------------------- views --
    def job_json(self, j, urls=True, lang="ru"):
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
                "size": j["size"], "eta": j["eta"], "error": i18n.localize_job_error(lang, j["error"]),
                "created_ts": j["created_ts"],
                "finished_ts": j["finished_ts"],
                "expires_ts": (j["finished_ts"] or 0) + limits()["ttl_min"] * 60 if done else None,
                "files": files, "origin": j["origin"]}

    def jobs_for(self, uid, lang="ru"):
        rows = self._rows("WHERE uid=? AND created_ts>=? ORDER BY id DESC", int(uid), int(self.clock()) - DAY)
        return [self.job_json(j, lang=lang) for j in rows]

    def admin(self, name_of, lang="ru"):
        now = int(self.clock())
        rows = self._rows("WHERE created_ts>=? ORDER BY id DESC", now - DAY)
        jobs = []
        for j in rows:
            d = self.job_json(j, urls=False, lang=lang)
            d.update(uid=j["uid"], user=name_of(j["uid"]))
            jobs.append(d)
        free = self.free_gb()
        return {"jobs": jobs, "disk_free_gb": None if free == float("inf") else round(free, 1),
                "active": sum(1 for j in rows if j["status"] in ACTIVE),
                "today": {"jobs": len(rows), "bytes": sum(j["size"] for j in rows if j["status"] == "done")},
                "limits": limits()}
