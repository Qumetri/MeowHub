"""Photo upscaling for members (service `upscale`): a simplified front to the `upscaler` service.

The upscaler (service `upscaler` in this compose project, upscaler/app/server.py) runs
upscayl-bin on the GPU, one picture at a time, and deletes results after KEEP_HOURS. This module
is the gatekeeper in between:

  * who owns which job (table `upscales`; the upscaler only knows job ids and an owner id),
  * limits: UPSCALE_PER_DAY pictures per person in any 24 h (failed and cancelled ones don't
    count), UPSCALE_ACTIVE waiting at once, UPSCALE_MAX_MB per file; the owner is exempt,
  * four presets instead of the full model list,
  * the queue as a person sees it: how long until their picture is done.

Pictures are small and quick (seconds to a minute), so there is no watcher thread: the Mini App
polls while something is in progress. Files are read from the upscaler's work dir, mounted
read-only at UPSCALE_FILES, and handed out through signed /dl/ links like the downloader's.
"""
import html
import http.client
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import i18n
from downloads import DlError as UpError

# preset -> upscayl model (ids from upscaler/app/server.py MODELS)
PRESETS = [
    ("photo", "upscayl-standard-4x"),
    ("detail", "ultramix-balanced-4x"),
    ("soft", "high-fidelity-4x"),
    ("art", "digital-art-4x"),
]
MODEL_OF = dict(PRESETS)
FORMATS = ("auto", "jpg", "png")
LIVE = ("queued", "running")
EXTS = (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".gif")
ENGINE_ERRORS = {"bad_image": "api.up_bad_image", "too_many_pixels": "api.up_too_many_pixels",
                 "already_large": "api.up_already_large", "too_large": "api.up_too_large"}


def _int_env(name, default):
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


def limits():
    return {"per_day": _int_env("UPSCALE_PER_DAY", 20), "active": _int_env("UPSCALE_ACTIVE", 3),
            "max_mb": _int_env("UPSCALE_MAX_MB", 25)}


class Engine:
    """Tiny client for the upscaler's HTTP API."""

    def __init__(self, base=None, timeout=15):
        self.base = (base or os.environ.get("UPSCALE_URL", "http://upscaler:8099")).rstrip("/")
        self.timeout = timeout

    def call(self, method, path):
        req = urllib.request.Request(self.base + path, method=method, headers={"X-Upscale": "1"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise UpError(404, "not_found", "api.up_not_found") from None
            raise UpError(502, "up_engine", "api.up_engine") from None
        except OSError:
            raise UpError(503, "up_down", "api.up_down") from None

    def upload(self, name, stream, length, query, owner_id, owner_name):
        """Stream the request body straight through (no temp file)."""
        u = urllib.parse.urlsplit(self.base)
        conn = http.client.HTTPConnection(u.hostname, u.port or 80, timeout=120)
        try:
            conn.putrequest("POST", "/api/jobs?" + urllib.parse.urlencode(query))
            for k, v in (("Content-Length", str(length)), ("X-Upscale", "1"),
                         ("X-Filename", urllib.parse.quote(name)), ("X-Owner-Id", str(owner_id)),
                         ("X-Owner-Name", urllib.parse.quote(owner_name or ""))):
                conn.putheader(k, v)
            conn.endheaders()
            left = length
            while left > 0:
                chunk = stream.read(min(1 << 20, left))
                if not chunk:
                    raise UpError(400, "up_upload_cut", "api.up_upload_cut")
                conn.send(chunk)
                left -= len(chunk)
            r = conn.getresponse()
            data = json.loads(r.read() or b"{}")
        except OSError:
            raise UpError(503, "up_down", "api.up_down") from None
        finally:
            conn.close()
        if r.status >= 400:
            err = data.get("error", "")
            raise UpError(400, err or "up_engine", ENGINE_ERRORS.get(err, "api.up_engine"),
                          mp=data.get("max_mp", ""), mb=data.get("max_mb", ""))
        return data


class Upscale:
    def __init__(self, store, owner_id, client=None, root=None):
        self.s = store
        self.owner_id = owner_id            # callable -> int | None
        self.engine = client or Engine()
        self.root = root or os.environ.get("UPSCALE_FILES", "/upscale")
        self.lock = threading.Lock()        # limit check + insert
        self.clock = time.time
        self.s.db.executescript("""
CREATE TABLE IF NOT EXISTS upscales (
    id INTEGER PRIMARY KEY AUTOINCREMENT, uid INTEGER NOT NULL, job TEXT NOT NULL, name TEXT NOT NULL DEFAULT '',
    preset TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'queued', deleted INTEGER NOT NULL DEFAULT 0,
    created_ts INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS upscales_uid ON upscales(uid, created_ts);
""")

    # ------------------------------------------------------------ db plumbing --
    def _rows(self, where="", *args):
        return [dict(r) for r in self.s.q("SELECT * FROM upscales " + where, *args)]

    def _get(self, uid, uid_row_id):
        rows = self._rows("WHERE id=? AND deleted=0", int(uid_row_id))
        if not rows or (rows[0]["uid"] != uid and uid != self.owner_id()):
            raise UpError(404, "not_found", "api.up_not_found")
        return rows[0]

    def _upd(self, rid, **kw):
        if kw:
            self.s.q(f"UPDATE upscales SET {', '.join(k + '=?' for k in kw)} WHERE id=?", *kw.values(), int(rid))

    def is_owner(self, uid):
        return uid is not None and uid == self.owner_id()

    def used(self, uid):
        """Pictures in the last 24 h that count: everything except failed and cancelled ones."""
        return self.s.q("SELECT COUNT(*) AS n FROM upscales WHERE uid=? AND created_ts>? AND "
                        "status NOT IN ('error','cancelled')", uid, int(self.clock()) - 86400)[0]["n"]

    def active(self, uid):
        return self.s.q("SELECT COUNT(*) AS n FROM upscales WHERE uid=? AND deleted=0 AND "
                        "status IN ('queued','running')", uid)[0]["n"]

    def quota(self, uid):
        lim = limits()
        used = self.used(uid)
        nxt = None
        if used >= lim["per_day"]:
            oldest = self.s.q("SELECT MIN(created_ts) AS t FROM upscales WHERE uid=? AND created_ts>? AND "
                              "status NOT IN ('error','cancelled')", uid, int(self.clock()) - 86400)[0]["t"]
            nxt = int(oldest + 86400 - self.clock()) if oldest else None
        return {"per_day": lim["per_day"], "used": used, "left": max(lim["per_day"] - used, 0),
                "next_in": nxt, "max_mb": lim["max_mb"], "unlimited": self.is_owner(uid)}

    # ---------------------------------------------------------------- upload --
    def upload(self, uid, name, stream, length, preset, scale, fmt, owner_name=""):
        name = os.path.basename(str(name or "image.jpg"))[:200]
        if os.path.splitext(name)[1].lower() not in EXTS:
            raise UpError(400, "bad_type", "api.up_bad_type")
        if preset not in MODEL_OF:
            raise UpError(400, "bad_preset", "api.up_bad_preset")
        if scale not in (2, 3, 4) or fmt not in FORMATS:
            raise UpError(400, "bad_request", "api.up_bad_preset")
        lim = limits()
        if length <= 0 or length > lim["max_mb"] * 1024 * 1024:
            raise UpError(413, "too_large", "api.up_too_large", mb=lim["max_mb"])
        with self.lock:
            if not self.is_owner(uid):
                if self.used(uid) >= lim["per_day"]:
                    raise UpError(429, "limit", "api.up_limit", n=lim["per_day"])
                if self.active(uid) >= lim["active"]:
                    raise UpError(429, "busy", "api.up_active", n=lim["active"])
            job = self.engine.upload(name, stream, length, {"model": MODEL_OF[preset], "scale": scale, "format": fmt},
                                     uid, owner_name)
            with self.s.lock:
                rid = self.s.db.execute("INSERT INTO upscales(uid, job, name, preset, status, created_ts) "
                                        "VALUES(?,?,?,?,?,?)", (uid, job["id"], job.get("name") or name, preset,
                                                                "queued", int(self.clock()))).lastrowid
        self._report(uid, job, preset)
        return rid

    def _report(self, uid, job, preset):
        """Usage notice to the owner/admins (usage.py): who upscales what."""
        u = getattr(self, "usage", None)
        if u is None:
            return
        q = self.quota(uid)
        args = {k: job.get(k) for k in ("scale", "in_w", "in_h", "out_w", "out_h")}
        name = html.escape(job.get("name") or "")
        u.report("upscale", uid, lambda lang, who: i18n.tr(
            lang, "usage.upscale", who=who, name=name, preset=i18n.tr(lang, "usage.preset." + preset),
            used=q["used"], total=q["per_day"], **args))

    def cancel(self, uid, rid):
        row = self._get(uid, rid)
        if row["status"] not in LIVE:
            raise UpError(409, "not_running", "api.up_not_running")
        self.engine.call("POST", f"/api/jobs/{row['job']}/cancel")
        self._upd(rid, status="cancelled")
        return {"ok": True}

    def delete(self, uid, rid):
        row = self._get(uid, rid)
        try:
            self.engine.call("DELETE", f"/api/jobs/{row['job']}")
        except UpError as e:
            if e.code != "not_found":                # already gone there (expired): fine
                raise
        self._upd(rid, deleted=1, **({"status": "cancelled"} if row["status"] in LIVE else {}))
        return {"ok": True}

    # ------------------------------------------------------------- listing --
    def list_for(self, uid):
        lim = limits()
        rows = self._rows("WHERE uid=? AND deleted=0 AND created_ts>? ORDER BY id DESC", uid,
                          int(self.clock()) - 2 * 86400)
        jobs, queue, engine_ok = {}, None, True
        if rows:
            try:
                d = self.engine.call("GET", "/api/jobs?ids=" + ",".join(r["job"] for r in rows))
                jobs = {j["id"]: j for j in d["jobs"]}
                queue = d["queue"]
            except UpError:
                engine_ok = False
        else:
            try:
                queue = self.engine.call("GET", "/api/queue")
            except UpError:
                engine_ok = False
        out = []
        for r in rows:
            j = jobs.get(r["job"])
            if j is None:
                if not engine_ok:
                    continue
                self._upd(r["id"], deleted=1, **({"status": "error"} if r["status"] in LIVE else {}))
                continue                                    # expired on the upscaler (KEEP_HOURS)
            if j["status"] != r["status"]:
                self._upd(r["id"], status=j["status"])
            out.append(self._view(r, j, queue))
        return {"jobs": out, "quota": self.quota(uid), "presets": [p for p, _ in PRESETS], "limits": lim,
                "queue": self.queue_summary(queue), "online": engine_ok}

    @staticmethod
    def _wait(job_id, queue):
        if not queue:
            return None
        cur = queue.get("current")
        if cur and cur["id"] == job_id:
            return {"ahead": 0, "ready_in": cur["eta"]}
        t, ahead = (cur["eta"] if cur else 0), (1 if cur else 0)
        for item in queue.get("queue", []):
            t += item["est"]
            if item["id"] == job_id:
                return {"ahead": ahead, "ready_in": round(t)}
            ahead += 1
        return None

    def _view(self, r, j, queue):
        out = {"id": r["id"], "name": j.get("name") or r["name"], "preset": r["preset"], "status": j["status"],
               "progress": j.get("progress", 0), "scale": j["scale"], "scale_asked": j["scale_asked"],
               "format": j["format"], "in_w": j["in_w"], "in_h": j["in_h"], "out_w": j["out_w"], "out_h": j["out_h"],
               "out_size": j.get("out_size"), "took": j.get("took"), "expires": j.get("expires"),
               "created_ts": r["created_ts"]}
        if j["status"] in LIVE:
            out["wait"] = self._wait(j["id"], queue)
        if j["status"] == "error":
            out["error"] = True
        return out

    @staticmethod
    def queue_summary(queue):
        """What a new picture would wait right now: pictures in line and the seconds until they're through."""
        if not queue:
            return None
        cur = queue.get("current")
        return {"jobs": len(queue.get("queue", [])) + (1 if cur else 0),
                "wait": round((cur or {}).get("eta") or 0) + round(sum(i["est"] for i in queue.get("queue", [])))}

    # ------------------------------------------------------------------ files --
    def _dir(self, row):
        if not re.fullmatch(r"[0-9a-f]{16}", row["job"]):
            raise UpError(404, "not_found", "api.up_not_found")
        return os.path.join(self.root, row["job"])

    def thumb_path(self, uid, rid, which):
        row = self._get(uid, rid)
        return os.path.join(self._dir(row), "thumb_in.jpg" if which == "before" else "thumb_out.jpg")

    def result_path(self, uid, rid):
        """-> (path, download name); only for a finished picture still on disk."""
        row = self._get(uid, rid)
        d = self._dir(row)
        try:
            with open(os.path.join(d, "meta.json")) as f:
                meta = json.load(f)
        except (OSError, ValueError):
            raise UpError(404, "not_found", "api.up_not_found") from None
        if meta.get("status") != "done":
            raise UpError(404, "not_found", "api.up_not_found")
        ext = meta["format"]
        name = re.sub(r'[\\/:*?"<>|]+', " ", meta.get("name") or "image").strip()[:120] or "image"
        return os.path.join(d, "out." + ext), f"{name}-x{meta['scale']}.{ext}"
