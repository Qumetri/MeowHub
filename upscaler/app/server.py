"""Image upscaler: upscayl-bin (the Real-ESRGAN family of models on ncnn + Vulkan, the engine
inside the Upscayl desktop app) behind a small HTTP API, plus the owner's web page (static/).

  * One job at a time on the GPU (the card is shared with Immich and Ollama), FIFO queue.
  * Uploads are normalised with Pillow first: EXIF rotation applied, odd modes converted, the
    colour profile kept for the result; GPS and other EXIF are dropped.
  * Size guards: MAX_MB per file, MAX_IN_MP input pixels, MAX_OUT_MP output pixels (the scale is
    lowered to fit, and a picture already too large even for x2 is refused).
  * Results live KEEP_HOURS, then the janitor deletes them: this is a tool, not storage.
  * The queue knows how long each model takes per input megapixel (learned from finished jobs,
    rates.json), so callers can show a wait.

Callers: the helper bot (members, over the compose network) and the owner's page through
Caddy (secret path + basic_auth). Mutating requests need the header X-Upscale: 1, which a
cross-site form can't send.
"""
import io
import json
import logging
import os
import re
import secrets
import shutil
import subprocess
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from PIL import Image, ImageOps

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("upscaler")


def _env(name, default, cast=int):
    try:
        return cast(os.environ.get(name, "") or default)
    except ValueError:
        return default


PORT = _env("PORT", 8099)
DATA = os.environ.get("DATA", "/data")
MODELS_DIR = os.path.join(DATA, "models")
WORK = os.path.join(DATA, "work")
RATES = os.path.join(DATA, "rates.json")
BIN = os.environ.get("UPSCAYL_BIN", "/usr/local/bin/upscayl-bin")
STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
MAX_MB = _env("MAX_MB", 25)
MAX_IN_MP = _env("MAX_IN_MP", 24, float)
MAX_OUT_MP = _env("MAX_OUT_MP", 64, float)
KEEP_HOURS = _env("KEEP_HOURS", 24, float)
TILE = _env("TILE", 0)
JOB_TIMEOUT = _env("JOB_TIMEOUT", 900)
THUMB = 1280                                   # previews for the before/after slider
STARTUP = 3.0                                  # seconds upscayl-bin needs to load a model, whatever the size

# What is installed, in the order the owner's page lists it. `sec_mp` seeds the queue estimate:
# seconds per input megapixel at any scale (the model always runs at x4 on the input), on top of
# STARTUP; measured on the RTX 3060 on 2026-10-28, finished jobs refine it.
MODELS = [
    {"id": "upscayl-standard-4x", "name": "Standard", "kind": "photo", "sec_mp": 8,
     "desc": "Upscayl's default: balanced detail for photos"},
    {"id": "ultramix-balanced-4x", "name": "Ultramix Balanced", "kind": "photo", "sec_mp": 8,
     "desc": "More natural texture (fur, foliage, fabric)"},
    {"id": "high-fidelity-4x", "name": "High Fidelity", "kind": "photo", "sec_mp": 8,
     "desc": "Soft and faithful: portraits, skin, already-good photos"},
    {"id": "remacri-4x", "name": "Remacri", "kind": "photo", "sec_mp": 8,
     "desc": "Crisp, contrasty detail"},
    {"id": "ultrasharp-4x", "name": "UltraSharp", "kind": "photo", "sec_mp": 8,
     "desc": "Sharpest, can overdo hard edges"},
    {"id": "upscayl-lite-4x", "name": "Lite", "kind": "photo", "sec_mp": 2.5,
     "desc": "Fast, a little less detail"},
    {"id": "digital-art-4x", "name": "Digital Art", "kind": "art", "sec_mp": 4,
     "desc": "Drawings, anime, flat colours, line art"},
]
FORMATS = ("auto", "jpg", "png", "webp")
ID_RE = re.compile(r"[0-9a-f]{16}\Z")


class Fail(Exception):
    def __init__(self, status, code, **extra):
        super().__init__(code)
        self.status, self.code, self.extra = status, code, extra


def installed():
    return [m for m in MODELS if os.path.exists(os.path.join(MODELS_DIR, m["id"] + ".bin"))]


# ------------------------------------------------------------------------- jobs --
class Jobs:
    def __init__(self):
        self.lock = threading.Lock()
        self.wake = threading.Condition(self.lock)
        self.jobs = {}                 # id -> dict (also written to work/<id>/meta.json)
        self.queue = []                # ids waiting, FIFO
        self.current = None            # id running
        self.proc = None
        self.rates = {}
        os.makedirs(WORK, exist_ok=True)
        try:
            with open(RATES) as f:
                self.rates = json.load(f)
        except (OSError, ValueError):
            pass
        for jid in sorted(os.listdir(WORK)):
            try:
                with open(os.path.join(WORK, jid, "meta.json")) as f:
                    j = json.load(f)
            except (OSError, ValueError):
                continue
            if j["status"] in ("queued", "running"):        # interrupted by a restart: run again
                j.update(status="queued", progress=0, started=None)
            self.jobs[jid] = j
        self.queue = sorted((k for k, j in self.jobs.items() if j["status"] == "queued"),
                            key=lambda k: self.jobs[k]["created"])

    # -- helpers --
    def dir(self, jid):
        return os.path.join(WORK, jid)

    def save(self, j):
        tmp = os.path.join(self.dir(j["id"]), "meta.json.tmp")
        with open(tmp, "w") as f:
            json.dump(j, f)
        os.replace(tmp, os.path.join(self.dir(j["id"]), "meta.json"))

    def rate(self, model):
        return self.rates.get(model) or next((m["sec_mp"] for m in MODELS if m["id"] == model), 12)

    def estimate(self, j):
        """Seconds for job j: the model on the input, plus encoding the output (~0.12 s per MP)."""
        return round(STARTUP + j["in_mp"] * self.rate(j["model"]) + j["out_mp"] * 0.12, 1)

    def get(self, jid):
        if not ID_RE.match(jid or "") or jid not in self.jobs:
            raise Fail(404, "not_found")
        return self.jobs[jid]

    # -- create --
    def create(self, data, name, model, scale, fmt, owner_id, owner_name):
        if model not in {m["id"] for m in installed()}:
            raise Fail(400, "bad_model")
        if scale not in (2, 3, 4):
            raise Fail(400, "bad_scale")
        if fmt not in FORMATS:
            raise Fail(400, "bad_format")
        try:
            im = Image.open(io.BytesIO(data))
            w, h = im.size
        except Exception:                                   # noqa: BLE001 - anything Pillow can't read
            raise Fail(400, "bad_image") from None
        if w * h > MAX_IN_MP * 1e6:
            raise Fail(400, "too_many_pixels", max_mp=MAX_IN_MP)
        in_mp = w * h / 1e6
        if in_mp * 4 > MAX_OUT_MP:
            raise Fail(400, "already_large", max_mp=MAX_OUT_MP)
        asked = scale
        while scale > 2 and in_mp * scale * scale > MAX_OUT_MP:
            scale -= 1
        src_fmt = (im.format or "").upper()
        try:
            im = ImageOps.exif_transpose(im)
            icc = im.info.get("icc_profile")
            alpha = im.mode in ("RGBA", "LA", "PA") or (im.mode == "P" and "transparency" in im.info)
            im = im.convert("RGBA" if alpha else "RGB")
        except Exception:                                   # noqa: BLE001
            raise Fail(400, "bad_image") from None
        w, h = im.size
        if fmt == "auto":
            fmt = "png" if alpha else {"JPEG": "jpg", "WEBP": "webp"}.get(src_fmt, "png")
        if fmt == "jpg" and alpha:
            fmt = "png"                                     # JPEG has no transparency
        jid = secrets.token_hex(8)
        d = self.dir(jid)
        os.makedirs(d)
        im.save(os.path.join(d, "in.png"), compress_level=1)
        if icc:
            with open(os.path.join(d, "icc"), "wb") as f:
                f.write(icc)
        th = im.copy()
        th.thumbnail((THUMB, THUMB))
        th.convert("RGB").save(os.path.join(d, "thumb_in.jpg"), quality=85)
        base = os.path.splitext(os.path.basename(name or "image"))[0][:120] or "image"
        j = {"id": jid, "name": base, "src": src_fmt.lower(), "model": model, "scale": scale, "scale_asked": asked,
             "format": fmt, "alpha": alpha, "owner_id": owner_id, "owner_name": owner_name[:120],
             "in_w": w, "in_h": h, "out_w": w * scale, "out_h": h * scale, "in_mp": round(w * h / 1e6, 3),
             "out_mp": round(w * h * scale * scale / 1e6, 2), "in_size": len(data), "out_size": None,
             "status": "queued", "progress": 0, "error": "", "created": time.time(), "started": None,
             "finished": None, "took": None}
        with self.lock:
            self.jobs[jid] = j
            self.queue.append(jid)
            self.save(j)
            self.wake.notify()
        log.info("job %s: %s %dx%d x%d %s for %s", jid, model, w, h, scale, fmt, owner_id)
        return j

    def cancel(self, jid):
        with self.lock:
            j = self.get(jid)
            if j["status"] == "queued":
                self.queue.remove(jid)
                j["status"] = "cancelled"
                self.save(j)
            elif j["status"] == "running" and self.proc:
                j["status"] = "cancelled"                  # the worker sees it after the kill
                self.proc.kill()
            return j

    def delete(self, jid):
        with self.lock:
            j = self.get(jid)
            if j["status"] == "running" and self.proc:
                j["status"] = "cancelled"
                self.proc.kill()
            if jid in self.queue:
                self.queue.remove(jid)
            self.jobs.pop(jid)
        if j["status"] != "running":
            shutil.rmtree(self.dir(jid), ignore_errors=True)
        return {"ok": True}

    # -- queue view --
    def queue_view(self):
        with self.lock:
            cur = None
            if self.current:
                j = self.jobs[self.current]
                left = max(self.estimate(j) * (1 - j["progress"] / 100), 1)
                if j["started"]:
                    left = max(self.estimate(j) - (time.time() - j["started"]), 1)
                cur = {"id": self.current, "eta": round(left)}
            return {"current": cur, "queue": [{"id": k, "est": self.estimate(self.jobs[k])} for k in self.queue]}

    # -- worker --
    def work(self):
        while True:
            with self.lock:
                while not self.queue:
                    self.wake.wait()
                jid = self.queue.pop(0)
                j = self.jobs[jid]
                self.current = jid
                j.update(status="running", started=time.time(), progress=0)
                self.save(j)
            try:
                self.run(j)
            except Exception as e:                          # noqa: BLE001 - one bad job never stops the queue
                log.exception("job %s", jid)
                with self.lock:
                    if j["status"] == "running":
                        j.update(status="error", error=str(e)[:300] or "failed")
            finally:
                with self.lock:
                    self.current, self.proc = None, None
                    j["finished"] = time.time()
                    if jid in self.jobs:
                        self.save(j)
                    elif os.path.isdir(self.dir(jid)):       # deleted while running
                        shutil.rmtree(self.dir(jid), ignore_errors=True)

    def run(self, j):
        d = self.dir(j["id"])
        raw = os.path.join(d, "raw.png")
        cmd = [BIN, "-i", os.path.join(d, "in.png"), "-o", raw, "-m", MODELS_DIR, "-n", j["model"],
               "-s", str(j["scale"]), "-f", "png"]
        if TILE:
            cmd += ["-t", str(TILE)]
        t0 = time.time()
        with self.lock:
            self.proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
                                         errors="replace")
            proc = self.proc
        killer = threading.Timer(JOB_TIMEOUT, proc.kill)
        killer.start()
        tail = []
        try:
            for line in proc.stderr:                        # "12.50%" per tile, plus a few status lines
                line = line.strip()
                m = re.fullmatch(r"(\d+(?:\.\d+)?)%", line)
                if m:
                    j["progress"] = min(float(m.group(1)), 99.0)
                elif line:
                    tail = (tail + [line])[-5:]
            proc.wait()
        finally:
            killer.cancel()
        if j["status"] == "cancelled":
            return
        if proc.returncode != 0 or not os.path.exists(raw):
            err = next((t for t in reversed(tail) if "rror" in t or "fail" in t), "upscayl-bin failed")
            raise RuntimeError(err)
        took_model = time.time() - t0
        out = os.path.join(d, "out." + j["format"])
        im = Image.open(raw)
        icc = None
        if os.path.exists(os.path.join(d, "icc")):
            with open(os.path.join(d, "icc"), "rb") as f:
                icc = f.read()
        kw = {"icc_profile": icc} if icc else {}
        if j["format"] == "jpg":
            im.convert("RGB").save(out, quality=92, optimize=True, **kw)
        elif j["format"] == "webp":
            im.save(out, quality=90, method=4, **kw)
        else:
            im.save(out, compress_level=6, **kw)
        th = im.copy()
        th.thumbnail((THUMB, THUMB))
        th.convert("RGB").save(os.path.join(d, "thumb_out.jpg"), quality=85)
        w, h = im.size
        im.close()
        for f in ("raw.png", "in.png"):
            try:
                os.remove(os.path.join(d, f))
            except OSError:
                pass
        with self.lock:
            j.update(status="done", progress=100, out_w=w, out_h=h, out_size=os.path.getsize(out),
                     took=round(time.time() - j["started"], 1))
            old = self.rates.get(j["model"])
            if j["in_mp"] >= 0.25:                         # tiny pictures are all model loading
                sec_mp = max((took_model - STARTUP) / j["in_mp"], 0.5)
                self.rates[j["model"]] = round(sec_mp if old is None else old * 0.7 + sec_mp * 0.3, 2)
        try:
            with open(RATES + ".tmp", "w") as f:
                json.dump(self.rates, f)
            os.replace(RATES + ".tmp", RATES)
        except OSError:
            pass
        log.info("job %s done in %.1fs (%dx%d)", j["id"], j["took"], w, h)

    # -- janitor --
    def janitor(self):
        while True:
            time.sleep(600)
            cutoff = time.time() - KEEP_HOURS * 3600
            with self.lock:
                old = [k for k, j in self.jobs.items()
                       if j["status"] not in ("queued", "running") and (j["finished"] or j["created"]) < cutoff]
                for k in old:
                    self.jobs.pop(k)
            for k in old:
                shutil.rmtree(self.dir(k), ignore_errors=True)
            if old:
                log.info("janitor: removed %d old jobs", len(old))


JOBS = None


def public(j):
    out = {k: v for k, v in j.items() if k != "alpha"}
    out["expires"] = (j["finished"] or j["created"]) + KEEP_HOURS * 3600 if j["status"] != "queued" else None
    return out


# ------------------------------------------------------------------------- http --
class Handler(BaseHTTPRequestHandler):
    server_version = "upscaler"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def send(self, status, body, ctype="application/json", headers=None):
        if not isinstance(body, (bytes, bytearray)):
            body = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def send_path(self, path, ctype, name=None, attachment=False):
        try:
            size = os.path.getsize(path)
        except OSError:
            raise Fail(404, "not_found") from None
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(size))
        self.send_header("Cache-Control", "private, max-age=3600")
        self.send_header("X-Content-Type-Options", "nosniff")
        if name:
            disp = "attachment" if attachment else "inline"
            self.send_header("Content-Disposition", f"{disp}; filename*=UTF-8''{urllib.parse.quote(name, safe='')}")
        self.end_headers()
        if self.command == "HEAD":
            return
        with open(path, "rb") as f:
            shutil.copyfileobj(f, self.wfile, 1 << 20)

    def route(self):
        u = urllib.parse.urlsplit(self.path)
        p, q = u.path, urllib.parse.parse_qs(u.query)
        m = self.command
        if m in ("POST", "DELETE") and self.headers.get("X-Upscale") != "1":
            raise Fail(403, "csrf")
        if m in ("GET", "HEAD"):
            if p in ("/", "/index.html"):
                return self.static("index.html")
            if p.startswith("/static/"):
                return self.static(p[len("/static/"):])
            if p == "/api/health":
                return self.send(200, {"ok": True, "models": len(installed())})
            if p == "/api/models":
                return self.send(200, {"models": installed(), "formats": FORMATS, "max_mb": MAX_MB,
                                       "max_in_mp": MAX_IN_MP, "max_out_mp": MAX_OUT_MP, "keep_hours": KEEP_HOURS})
            if p == "/api/queue":
                return self.send(200, JOBS.queue_view())
            if p == "/api/jobs":
                owner = (q.get("owner") or [None])[0]
                ids = set((q.get("ids") or [""])[0].split(",")) - {""}
                with JOBS.lock:
                    items = [public(j) for j in JOBS.jobs.values()
                             if (owner is None or str(j["owner_id"]) == owner) and (not ids or j["id"] in ids)]
                items.sort(key=lambda j: j["created"], reverse=True)
                return self.send(200, {"jobs": items, "queue": JOBS.queue_view()})
            mm = re.fullmatch(r"/api/jobs/([0-9a-f]{16})", p)
            if mm:
                return self.send(200, public(JOBS.get(mm.group(1))))
            mm = re.fullmatch(r"/api/jobs/([0-9a-f]{16})/(before|after|result)", p)
            if mm:
                j = JOBS.get(mm.group(1))
                d = JOBS.dir(j["id"])
                if mm.group(2) == "before":
                    return self.send_path(os.path.join(d, "thumb_in.jpg"), "image/jpeg")
                if j["status"] != "done":
                    raise Fail(404, "not_ready")
                if mm.group(2) == "after":
                    return self.send_path(os.path.join(d, "thumb_out.jpg"), "image/jpeg")
                ext = j["format"]
                ctype = {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp"}[ext]
                name = f"{j['name']}-x{j['scale']}.{ext}"
                return self.send_path(os.path.join(d, "out." + ext), ctype, name, attachment="download" in q)
        if m == "POST" and p == "/api/jobs":
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                n = 0
            if n <= 0:
                raise Fail(400, "empty")
            if n > MAX_MB * 1024 * 1024:
                self.close_connection = True
                raise Fail(413, "too_large", max_mb=MAX_MB)
            data = self.rfile.read(n)
            if len(data) != n:
                raise Fail(400, "upload_cut")
            try:
                scale = int((q.get("scale") or ["4"])[0])
            except ValueError:
                scale = 0
            j = JOBS.create(data, urllib.parse.unquote(self.headers.get("X-Filename") or ""),
                            (q.get("model") or ["upscayl-standard-4x"])[0], scale, (q.get("format") or ["auto"])[0],
                            self.headers.get("X-Owner-Id") or "web",
                            urllib.parse.unquote(self.headers.get("X-Owner-Name") or ""))
            return self.send(201, public(j))
        mm = re.fullmatch(r"/api/jobs/([0-9a-f]{16})/cancel", p)
        if m == "POST" and mm:
            return self.send(200, public(JOBS.cancel(mm.group(1))))
        mm = re.fullmatch(r"/api/jobs/([0-9a-f]{16})", p)
        if m == "DELETE" and mm:
            return self.send(200, JOBS.delete(mm.group(1)))
        raise Fail(404, "not_found")

    def static(self, rel):
        path = os.path.normpath(os.path.join(STATIC, rel))
        if not path.startswith(STATIC + os.sep) or not os.path.isfile(path):
            raise Fail(404, "not_found")
        ctype = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                 ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml"}.get(os.path.splitext(path)[1],
                                                                                  "application/octet-stream")
        with open(path, "rb") as f:
            body = f.read()
        self.send(200, body, ctype, {"Cache-Control": "no-cache"})

    def handle_one(self):
        try:
            self.route()
        except Fail as e:
            self.send(e.status, {"error": e.code, **e.extra})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:                                   # noqa: BLE001
            log.exception("%s %s", self.command, self.path)
            self.send(500, {"error": "internal"})

    do_GET = do_HEAD = do_POST = do_DELETE = handle_one


def main():
    global JOBS
    Image.MAX_IMAGE_PIXELS = int(MAX_OUT_MP * 1e6 * 1.1)   # our own results must still open
    JOBS = Jobs()
    threading.Thread(target=JOBS.work, daemon=True, name="worker").start()
    threading.Thread(target=JOBS.janitor, daemon=True, name="janitor").start()
    log.info("upscaler on :%d, %d models, %d queued", PORT, len(installed()), len(JOBS.queue))
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
