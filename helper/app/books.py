"""Audiobooks for members (service `books`, beta): a simplified front to book2audio.

book2audio (/home/server/book2audio, own compose project) does the work: it splits an EPUB/PDF
into chapters, narrates one book at a time on the GPU and assembles an M4B. This module is the
gatekeeper in between:

  * who owns which book (table `books`; book2audio itself only knows slugs),
  * the limit: BOOKS_PER_USER audiobooks per person (finished ones count even after deletion,
    queued/running ones count too; cancelled and failed ones don't), BOOKS_PENDING uploads that
    were never narrated, BOOKS_MAX_MB per file; the owner is exempt,
  * a short, curated voice list per language instead of the full catalogue,
  * the queue as a person sees it: how many books are ahead, when theirs starts and ends
    (from book2audio's /api/queue, which knows each engine's measured speed),
  * a watcher thread that tells the owner of a book when it is ready or failed.

The API talks to book2audio over the internal docker network `books` (BOOKS_URL); finished
files are read from its work dir, mounted read-only at BOOKS_FILES, and handed out through
signed /dl/ links like the downloader's.
"""
import html
import http.client
import json
import logging
import os
import re
import threading
import time
import urllib.parse
import urllib.request

import i18n
from downloads import DlError as BooksError

log = logging.getLogger("books")

ACTIVE = ("queued", "narrating", "assembling")     # narration in progress (counts toward the limit)
BUSY = ("preparing",) + ACTIVE
EXTS = (".epub", ".pdf")
CHARS_PER_SEC = 15.5                               # speech at 1.0x (Kokoro: 471 516 chars -> 8 h 26 min)

# The simplified choice: a few good voices per language (ids from book2audio's catalogue).
VOICES = {
    "en": [("kokoro", "af_heart"), ("kokoro", "af_bella"), ("kokoro", "bf_emma"),
           ("kokoro", "am_michael"), ("kokoro", "am_fenrir"), ("kokoro", "bm_george")],
    "ru": [("silero", "xenia"), ("silero", "baya"), ("silero", "aidar"), ("silero", "eugene")],
}


def _int_env(name, default):
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


def limits():
    return {"per_user": _int_env("BOOKS_PER_USER", 3), "pending": _int_env("BOOKS_PENDING", 2),
            "max_mb": _int_env("BOOKS_MAX_MB", 100)}


def _norm_title(s):
    return re.sub(r"[\W_]+", "", (s or "").lower())


class B2A:
    """Tiny client for book2audio's HTTP API."""

    def __init__(self, base=None, timeout=20):
        self.base = (base or os.environ.get("BOOKS_URL", "http://book2audio:8098")).rstrip("/")
        self.timeout = timeout

    def call(self, method, path, body=None, raw=False, timeout=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"} if data else {})
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as r:
                payload = r.read()
        except urllib.error.HTTPError as e:
            try:
                msg = json.loads(e.read()).get("error", "")
            except ValueError:
                msg = ""
            raise BooksError(502 if e.code >= 500 else 400, "books_engine", "api.books_engine", detail=msg) from None
        except OSError:
            raise BooksError(503, "books_down", "api.books_down") from None
        return payload if raw else json.loads(payload or b"{}")

    def upload(self, name, stream, length, owner_id, owner_name):
        """Stream the request body straight through (no temp file)."""
        u = urllib.parse.urlsplit(self.base)
        conn = http.client.HTTPConnection(u.hostname, u.port or 80, timeout=120)
        try:
            conn.putrequest("POST", "/api/books")
            conn.putheader("Content-Length", str(length))
            conn.putheader("X-Filename", urllib.parse.quote(name))
            conn.putheader("X-Owner-Id", str(owner_id))
            conn.putheader("X-Owner-Name", urllib.parse.quote(owner_name or ""))
            conn.endheaders()
            left = length
            while left > 0:
                chunk = stream.read(min(1 << 20, left))
                if not chunk:
                    raise BooksError(400, "books_upload_cut", "api.books_upload_cut")
                conn.send(chunk)
                left -= len(chunk)
            r = conn.getresponse()
            data = json.loads(r.read() or b"{}")
        except OSError:
            raise BooksError(503, "books_down", "api.books_down") from None
        finally:
            conn.close()
        if r.status >= 400:
            raise BooksError(400, "books_bad_file", "api.books_bad_file")
        return data


class Books:
    def __init__(self, store, members, owner_id, notify=None, client=None, root=None, notify_owner=None):
        self.s = store
        self.members = members
        self.owner_id = owner_id            # callable -> int | None
        self.notify = notify                # notify(uid, make(lang)) -> sends in the recipient's language
        self.notify_owner = notify_owner    # notify_owner(make(lang)) -> the owner, in the owner's language
        self.b2a = client or B2A()
        self.root = root or os.environ.get("BOOKS_FILES", "/books")
        self.lock = threading.Lock()        # limit check + insert
        self.clock = time.time
        self._catalog = (0, None)
        self._thread = None
        self.s.db.executescript("""
CREATE TABLE IF NOT EXISTS books (
    id INTEGER PRIMARY KEY AUTOINCREMENT, uid INTEGER NOT NULL, slug TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '', author TEXT NOT NULL DEFAULT '', lang TEXT NOT NULL DEFAULT '',
    engine TEXT NOT NULL DEFAULT '', voice TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'preparing',
    chars INTEGER NOT NULL DEFAULT 0, chars_todo INTEGER NOT NULL DEFAULT 0, chapters INTEGER NOT NULL DEFAULT 0,
    seconds REAL, size INTEGER, error TEXT NOT NULL DEFAULT '', deleted INTEGER NOT NULL DEFAULT 0,
    created_ts INTEGER NOT NULL, queued_ts INTEGER, finished_ts INTEGER, notified TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS books_uid ON books(uid, created_ts);
""")

    # ------------------------------------------------------------ db plumbing --
    def _rows(self, where="", *args):
        return [dict(r) for r in self.s.q("SELECT * FROM books " + where, *args)]

    def _get(self, uid, bid):
        rows = self._rows("WHERE id=? AND deleted=0", int(bid))
        if not rows or (rows[0]["uid"] != uid and uid != self.owner_id()):
            raise BooksError(404, "not_found", "api.books_not_found")
        return rows[0]

    def _upd(self, bid, **kw):
        if kw:
            self.s.q(f"UPDATE books SET {', '.join(k + '=?' for k in kw)} WHERE id=?", *kw.values(), int(bid))

    def is_owner(self, uid):
        return uid is not None and uid == self.owner_id()

    def used(self, uid):
        """Audiobooks that count toward the limit: finished (even deleted) + in progress."""
        return self.s.q("SELECT COUNT(*) AS n FROM books WHERE uid=? AND (status='done' OR "
                        "(deleted=0 AND status IN ('queued','narrating','assembling')))", uid)[0]["n"]

    def pending(self, uid):
        return self.s.q("SELECT COUNT(*) AS n FROM books WHERE uid=? AND deleted=0 AND "
                        "status IN ('preparing','ready','cancelled','error')", uid)[0]["n"]

    def quota(self, uid):
        lim = limits()
        used = self.used(uid)
        return {"per_user": lim["per_user"], "used": used, "left": max(lim["per_user"] - used, 0),
                "max_mb": lim["max_mb"], "unlimited": self.is_owner(uid)}

    # ---------------------------------------------------------------- voices --
    def catalog(self):
        ts, cat = self._catalog
        if cat is None or self.clock() - ts > 600:
            cat = self.b2a.call("GET", "/api/voices")
            self._catalog = (self.clock(), cat)
        return cat

    def voices(self, lang):
        """The curated voices for `lang` that book2audio has installed."""
        try:
            cat = self.catalog()
        except BooksError:
            return []
        by_key = {(v["engine"], v["id"], v["lang"]): v for v in cat.get("voices", [])}
        out = []
        for engine, vid in VOICES.get(lang, []):
            v = by_key.get((engine, vid, lang))
            if v and cat["engines"].get(engine, {}).get("available"):
                accent = v.get("accent", "")
                out.append({"key": f"{engine}:{vid}", "engine": engine, "id": vid, "name": v["name"],
                            "gender": v["gender"], "accent": "gb" if "🇬🇧" in accent else "us" if "🇺🇸" in accent else ""})
        return out

    def preview(self, key, lang):
        engine, _, vid = str(key).partition(":")
        if (engine, vid) not in VOICES.get(lang, []):
            raise BooksError(400, "bad_voice", "api.books_bad_voice")
        return self.b2a.call("GET", f"/api/preview/{engine}/{urllib.parse.quote(vid)}?lang={lang}", raw=True,
                             timeout=120)

    # ---------------------------------------------------------------- upload --
    def upload(self, uid, name, stream, length, owner_name=""):
        name = os.path.basename(str(name or "book.epub"))[:200]
        if os.path.splitext(name)[1].lower() not in EXTS:
            raise BooksError(400, "bad_type", "api.books_bad_type")
        lim = limits()
        if length <= 0 or length > lim["max_mb"] * 1024 * 1024:
            raise BooksError(413, "too_large", "api.books_too_large", mb=lim["max_mb"])
        with self.lock:
            if not self.is_owner(uid):
                if self.used(uid) >= lim["per_user"]:
                    raise BooksError(429, "limit", "api.books_limit", n=lim["per_user"])
                if self.pending(uid) >= lim["pending"]:
                    raise BooksError(429, "pending", "api.books_pending", n=lim["pending"])
            res = self.b2a.upload(name, stream, length, uid, owner_name)
            now = int(self.clock())
            with self.s.lock:
                bid = self.s.db.execute("INSERT INTO books(uid, slug, title, status, created_ts) VALUES(?,?,?,?,?)",
                                        (uid, res["slug"], os.path.splitext(name)[0], "preparing", now)).lastrowid
        self.ensure_watcher()
        return bid

    # ------------------------------------------------------------- narration --
    def _chapters_for(self, row, detail):
        """Every chapter except a title/copyright page named like the book itself."""
        title = _norm_title(detail.get("title") or row["title"])
        keep = [c["index"] for c in detail.get("chapters", [])
                if not (c["chars"] < 6000 and _norm_title(c["title"]) == title)]
        return keep or [c["index"] for c in detail.get("chapters", [])]

    def narrate(self, uid, bid, voice_key, lang="ru", who=""):
        row = self._get(uid, bid)
        if row["status"] in BUSY:
            raise BooksError(409, "busy", "api.books_busy")
        if row["status"] == "done":
            raise BooksError(409, "done", "api.books_done")
        engine, _, vid = str(voice_key or "").partition(":")
        if (engine, vid) not in VOICES.get(row["lang"], []):
            raise BooksError(400, "bad_voice", "api.books_bad_voice")
        lim = limits()
        with self.lock:
            if not self.is_owner(row["uid"]) and self.used(row["uid"]) >= lim["per_user"]:
                raise BooksError(429, "limit", "api.books_limit", n=lim["per_user"])
            detail = self.b2a.call("GET", f"/api/books/{row['slug']}")
            chapters = self._chapters_for(row, detail)
            self.b2a.call("POST", f"/api/books/{row['slug']}/narrate",
                          {"engine": engine, "voice": vid, "language": row["lang"], "speed": 1.0,
                           "chapters": chapters})
            todo = sum(c["chars"] for c in detail.get("chapters", []) if c["index"] in chapters)
            self._upd(bid, engine=engine, voice=vid, status="queued", error="", chars_todo=todo,
                      queued_ts=int(self.clock()), notified="")
        self.ensure_watcher()
        view = self.view_one(uid, bid, lang)
        if not self.is_owner(row["uid"]):
            self._tell_owner_queued(row, view, who, len(chapters))
        return view

    def _tell_owner_queued(self, row, view, who, n_chapters):
        """The owner hears about every book a member sends to the GPU: who, what, how long, the queue."""
        u = getattr(self, "usage", None)
        if u is None and self.notify_owner is None:
            return
        voice = next((v["name"] for v in self.voices(row["lang"]) if v["key"] == view.get("voice")), view.get("voice"))
        w = view.get("wait") or {}
        q = self.quota(row["uid"])
        args = {"who": who or f"<code>{row['uid']}</code>", "title": html.escape(view["title"] or row["slug"]),
                "author": html.escape(view["author"] or "—"), "voice": html.escape(voice or ""),
                "chapters": n_chapters, "used": q["used"], "total": q["per_user"]}
        listen_h, ready_s, ahead = view["listen_s"] / 3600, w.get("starts_in", 0) + w.get("takes", 0), w.get("ahead", 0)
        make = lambda lang, actor=args["who"]: i18n.tr(lang, "books.owner_queued", listen=_dur(listen_h, lang),  # noqa: E731
                                                       ahead=ahead, ready=_dur(ready_s / 3600, lang), **dict(args, who=actor))
        if u is not None:
            return u.report("books", row["uid"], make)
        try:
            self.notify_owner(make)
        except Exception:                                   # noqa: BLE001 - a notice never breaks the request
            log.warning("owner notice for book %s failed", row["id"])

    def cancel(self, uid, bid, lang="ru"):
        row = self._get(uid, bid)
        if row["status"] not in ACTIVE:
            raise BooksError(409, "not_running", "api.books_not_running")
        self.b2a.call("POST", f"/api/books/{row['slug']}/cancel")
        self._upd(bid, status="cancelled")
        return self.view_one(uid, bid, lang)

    def delete(self, uid, bid):
        row = self._get(uid, bid)
        if row["status"] in BUSY:
            raise BooksError(409, "busy", "api.books_busy_delete")
        try:
            self.b2a.call("DELETE", f"/api/books/{row['slug']}")
        except BooksError as e:
            if e.code != "books_engine":            # already gone there: fine
                raise
        self._upd(bid, deleted=1)
        return {"ok": True}

    # ------------------------------------------------------------- refreshing --
    STATUS_MAP = {"ready": "ready", "narrating": "narrating", "assembling": "assembling", "done": "done",
                  "error": "error", "cancelled": "cancelled"}

    def _merge(self, row, b):
        """book2audio's summary `b` -> fields to update on our row."""
        st = b.get("status")
        if st in ("queued", "preparing"):
            st = "preparing" if row["status"] == "preparing" else "queued"
        else:
            st = self.STATUS_MAP.get(st, row["status"])
        upd = {}
        if st != row["status"]:
            upd["status"] = st
        for ours, theirs in (("title", "title"), ("author", "author"), ("lang", "language")):
            if b.get(theirs) and b[theirs] != row[ours] and row["status"] == "preparing":
                upd[ours] = b[theirs]
        if b.get("chars_total") and b["chars_total"] != row["chars"]:
            upd["chars"] = b["chars_total"]
        if b.get("chapters_total") and b["chapters_total"] != row["chapters"]:
            upd["chapters"] = b["chapters_total"]
        if st == "error" and (b.get("error") or "") != row["error"]:
            upd["error"] = (b.get("error") or "")[:300]
        audio = b.get("audio")
        if st == "done" and audio:
            upd.update(seconds=audio["seconds"], size=audio["size"])
            if not row["finished_ts"]:
                upd["finished_ts"] = int(self.clock())
        if row["status"] == "preparing" and st == "ready" and not row["lang"] and b.get("language"):
            upd["lang"] = b["language"]
        return upd

    def refresh(self, rows=None):
        """Pull book2audio's state for our live rows; returns (rows, queue) — queue may be None."""
        rows = self._rows("WHERE deleted=0 ORDER BY id") if rows is None else rows
        try:
            lib = {b["slug"]: b for b in self.b2a.call("GET", "/api/books")["books"]}
            queue = self.b2a.call("GET", "/api/queue")
        except BooksError:
            return rows, None
        out = []
        for r in rows:
            b = lib.get(r["slug"])
            if b is None:
                if r["status"] != "error":
                    self._upd(r["id"], status="error", error="gone")
                    r = dict(r, status="error", error="gone")
            else:
                upd = self._merge(r, b)
                if upd:
                    self._upd(r["id"], **upd)
                    r = dict(r, **upd)
                r["_b2a"] = b
            out.append(r)
        return out, queue

    @staticmethod
    def wait_info(slug, queue):
        """For a queued book: {ahead, starts_in, takes} in seconds (None when unknown)."""
        if not queue:
            return None
        cur = queue.get("current")
        start = cur["eta"] if cur and cur.get("eta") is not None and cur["slug"] != slug else 0
        ahead = 1 if cur and cur["slug"] != slug else 0
        for item in queue.get("queue", []):
            if item["slug"] == slug:
                return {"ahead": ahead, "starts_in": int(start), "takes": int(item["est"])}
            start += item["est"]
            ahead += 1 if item["kind"] == "narrate" else 0
        return None

    def _view(self, r, queue, lang):
        b = r.get("_b2a") or {}
        out = {"id": r["id"], "title": r["title"], "author": r["author"], "lang": r["lang"],
               "status": r["status"], "voice": f"{r['engine']}:{r['voice']}" if r["engine"] else None,
               "chapters": r["chapters"], "listen_s": round((r["chars_todo"] or r["chars"]) / CHARS_PER_SEC),
               "created_ts": r["created_ts"], "finished_ts": r["finished_ts"], "has_cover": bool(b.get("has_cover")),
               "error": "", "seconds": r["seconds"], "size": r["size"]}
        if r["status"] == "error":
            out["error"] = i18n.tr(lang, "books.err_gone" if r["error"] == "gone" else "books.err_failed")
        if r["status"] == "queued":
            out["wait"] = self.wait_info(r["slug"], queue)
        if r["status"] in ("ready", "cancelled", "error") and queue and r["chars"]:
            rt = queue.get("rates") or {}
            out["takes"] = {e: round(r["chars"] / rt.get(e, 500) + r["chars"] / 1000 * rt.get("asm_per_kchar", 0.2) + 20)
                            for e in {e for e, _ in VOICES.get(r["lang"], [])}}
        if r["status"] in ("narrating", "assembling"):
            p = b.get("progress") or {}
            cur = (queue or {}).get("current") or {}
            out["percent"] = round(100 * (p.get("chars_done") or 0) / max(p.get("chars_total") or 1, 1), 1)
            out["eta"] = cur.get("eta") if cur.get("slug") == r["slug"] else p.get("eta")
        return out

    def view_one(self, uid, bid, lang="ru"):
        rows, queue = self.refresh([self._get(uid, bid)])
        return self._view(rows[0], queue, lang)

    def list_for(self, uid, lang="ru"):
        rows, queue = self.refresh(self._rows("WHERE uid=? AND deleted=0 ORDER BY id DESC", uid))
        if any(r["status"] in BUSY for r in rows):
            self.ensure_watcher()
        langs = sorted({r["lang"] for r in rows if r["lang"]} | {"ru", "en"})
        return {"books": [self._view(r, queue, lang) for r in rows], "quota": self.quota(uid),
                "voices": {lg: self.voices(lg) for lg in langs}, "queue": self.queue_summary(queue)}

    @staticmethod
    def queue_summary(queue):
        """What a new book would wait right now: books in line and the seconds until they're through."""
        if not queue:
            return None
        cur = queue.get("current")
        books = [i for i in queue.get("queue", []) if i["kind"] == "narrate"]
        return {"books": len(books) + (1 if cur else 0),
                "wait": int((cur or {}).get("eta") or 0) + sum(int(i["est"]) for i in queue.get("queue", []))}

    # ------------------------------------------------------------------ files --
    def audio_path(self, uid, bid):
        row = self._get(uid, bid)
        if row["status"] != "done":
            raise BooksError(404, "not_found", "api.books_not_found")
        path = os.path.join(self.root, row["slug"], f"{row['slug']}.m4b")
        name = re.sub(r'[\\/:*?"<>|]+', " ", row["title"] or row["slug"]).strip()[:120] + ".m4b"
        return path, name

    def cover_path(self, uid, bid):
        row = self._get(uid, bid)
        return os.path.join(self.root, row["slug"], "cover.jpg")

    # ---------------------------------------------------------------- watcher --
    def ensure_watcher(self):
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self.run, daemon=True, name="books")
            self._thread.start()

    def run(self):
        """Follow live books every 15 s; tell their owners when one is ready or failed.
        Exits when nothing is in progress (started again on the next upload/narration)."""
        while True:
            try:
                live = self._rows("WHERE deleted=0 AND status IN ('preparing','queued','narrating','assembling') "
                                  "OR (deleted=0 AND status IN ('done','error') AND notified='' AND queued_ts IS NOT NULL)")
                if not live:
                    return
                rows, queue = self.refresh(live)
                if queue is not None:
                    for r in rows:
                        self._announce(r)
            except Exception:                                   # noqa: BLE001 - keep following
                log.exception("books watcher")
            time.sleep(15)

    def _announce(self, r):
        if r["notified"] or r["queued_ts"] is None or r["status"] not in ("done", "error"):
            return
        self._upd(r["id"], notified=r["status"])
        if self.notify is None:
            return
        title = r["title"]
        if r["status"] == "done":
            hours = (r["seconds"] or 0) / 3600
            self.notify(r["uid"], lambda lang: i18n.tr(lang, "books.ready", title=title, dur=_dur(hours, lang)))
        else:
            self.notify(r["uid"], lambda lang: i18n.tr(lang, "books.failed", title=title))


def _dur(hours, lang):
    h, m = int(hours), round((hours - int(hours)) * 60)
    if lang == "en":
        return f"{h} h {m} min" if h else f"{m} min"
    return f"{h} ч {m} мин" if h else f"{m} мин"
