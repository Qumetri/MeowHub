"""Downloader (downloads.py + /api/dl, /dl/<token> in webapp + the bots' hand-off to the Mini App).

An in-process fake MeTube (real HTTP, so the real client runs) and a fake bot
that records messages. Files are written into a temp "downloads" root."""
import json
import os
import re
import tempfile
import threading
import time
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

import downloads as dlm
import links
import members as mm
import test_bot as tb
import test_round2 as t2
import test_webapp as tw
from store import Store

OWNER = 1000
PATH = "sekret"
# canonical spellings: this is what the engine stores and what MeTube reports back
URL = "https://www.youtube.com/watch?v=AAAAAAAAAAA"
URL2 = "https://www.youtube.com/watch?v=BBBBBBBBBBB"
MB = 1024 * 1024


# --------------------------------------------------------------- fakes --
class FakeMeTube:
    """Just enough of MeTube: URL-keyed queue/history, 'Already in queue', delete."""

    def __init__(self, root):
        self.root = root
        self.items = {}
        self.adds = []
        self.deletes = []
        self.fail_add = None
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, obj, code=200):
                data = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                if self.path != f"/{PATH}/history":
                    return self._send({}, 404)
                self._send(outer.history())

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                if self.path == f"/{PATH}/add":
                    return self._send(outer.add(body))
                if self.path == f"/{PATH}/delete":
                    return self._send(outer.delete(body))
                self._send({}, 404)

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()

    def client(self):
        return dlm.MeTube(self.base, PATH)

    # -- MeTube behaviour --
    def history(self):
        out = {"queue": [], "pending": [], "done": []}
        for i in list(self.items.values()):
            out["done" if i["status"] in ("finished", "error") else "queue"].append(dict(i))
        return out

    def add(self, body):
        if self.fail_add:
            return {"status": "error", "msg": self.fail_add}
        ex = self.items.get(body["url"])
        if ex and ex["status"] not in ("finished", "error"):
            return {"status": "ok", "msg": f"Already in queue: {body['url']}"}
        self.adds.append(body)
        self.items[body["url"]] = {
            "url": body["url"], "title": "Title " + body["url"][-4:], "status": "downloading", "percent": 0,
            "eta": 30, "size": None, "filename": None, "folder": body.get("folder", ""),
            "timestamp": time.time_ns(), "download_type": body.get("download_type"),
            "quality": body.get("quality"), "format": body.get("format"), "msg": ""}
        return {"status": "ok"}

    def delete(self, body):
        self.deletes.append(body)
        for u in body["ids"]:
            it = self.items.get(u)
            if not it:
                continue
            if body["where"] == "done" and it["status"] in ("finished", "error"):
                for f in [it.get("filename")] + list(it.get("subtitle_files") or []):
                    if f:
                        try:
                            os.remove(os.path.join(self.root, it["folder"], f))
                        except OSError:
                            pass
                del self.items[u]
            elif body["where"] == "queue" and it["status"] not in ("finished", "error"):
                del self.items[u]
        return {"status": "ok"}

    # -- test controls --
    def progress(self, url, pct, size=1000):
        self.items[url].update(percent=pct, size=size)

    def finish(self, url, size=1000, name=None, subs=None):
        it = self.items[url]
        name = name or ("clip.mp3" if it["download_type"] == "audio" else
                        "clip.srt" if it["download_type"] == "captions" else "clip.mp4")
        d = os.path.join(self.root, it["folder"])
        os.makedirs(d, exist_ok=True)
        for n, sz in [(name, size)] + [(s, 10) for s in (subs or [])]:
            with open(os.path.join(d, n), "wb") as f:
                f.truncate(sz)
        it.update(status="finished", percent=100, filename=name, size=size, eta=None,
                  subtitle_files=list(subs or []))

    def fail(self, url, msg="boom"):
        self.items[url].update(status="error", msg=msg)


class UpBot:
    """Records everything it is asked to send."""
    token = "1:UP"

    def __init__(self):
        self.log = []
        self.n = 500

    def send(self, chat_id, text, **kw):
        self.n += 1
        self.log.append(("send", chat_id, text, kw))
        return {"message_id": self.n}

    def edit(self, chat_id, message_id, text, **kw):
        self.log.append(("edit", chat_id, text, dict(kw, message_id=message_id)))

    def delete(self, chat_id, message_id):
        self.log.append(("delete", chat_id, "", {"message_id": message_id}))

    def answer(self, callback_id, text=None, alert=False):
        self.log.append(("answer", callback_id, text, {"alert": alert}))

    def call(self, method, **kw):
        self.log.append((method, None, "", kw))
        return {}

    def sent(self, chat_id):
        return [e for e in self.log if e[0] == "send" and e[1] == chat_id]

    def edits(self, chat_id):
        return [e for e in self.log if e[0] == "edit" and e[1] == chat_id]

    def answers(self):
        return [e for e in self.log if e[0] == "answer"]


# ------------------------------------------------------------ engine base --
class EngineBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        p = mock.patch.dict(os.environ, {"METUBE_PATH": PATH, "METUBE_TTL_MIN": "30", "DL_ACTIVE": "2",
                                         "DL_PER_HOUR": "10", "DL_PER_DAY": "30"})
        p.start()
        self.addCleanup(p.stop)
        self.root = os.path.join(self.tmp.name, "dl")
        os.makedirs(self.root)
        self.store = Store(os.path.join(self.tmp.name, "h.db"))
        links.configure(self.store)
        self.mem = mm.Members(self.store)
        self.mem.set_service_enabled("youtube", True)
        self.fake = FakeMeTube(self.root)
        self.addCleanup(self.fake.close)
        self.bot = UpBot()
        self.dl = self.make_dl()
        for uid in (10, 11):
            self.mem.grant(uid, {"first_name": f"U{uid}", "username": f"u{uid}"}, 30, [])

    def make_dl(self):
        d = dlm.Downloads(self.store, self.mem, lambda: OWNER, lambda: "https://example.org/app/",
                          self.fake.client(), self.root)
        d.free_gb = lambda: 500.0
        return d

    def tick(self, n=1):
        for _ in range(n):
            self.dl.tick()

    def job(self, jid):
        return self.dl._get(jid)

    def sub(self, uid, url=URL, preset="v720", opts=None):
        return self.dl.submit(uid, url, preset, opts)["id"]


# ------------------------------------------------------------------ tests --
class TestPresets(EngineBase):
    def test_payload_shape(self):
        p = dlm.presets_payload()
        self.assertEqual([x["id"] for x in p["video"]], ["v360", "v720", "v1080", "vbest"])
        self.assertEqual([x["id"] for x in p["audio"]], ["mp3", "m4a", "opus"])
        self.assertEqual(p["extras"], {"subs": ["ru", "en"], "clip": True, "playlist_max": 10})
        self.assertEqual(p["limits"], {"active": 2, "per_hour": 10, "per_day": 30, "ttl_min": 30})
        self.assertEqual(p["sites"], ["YouTube", "RuTube", "VK Видео"])

    def test_add_bodies_force_h264_and_user_folder(self):
        want = {"v360": ("360", "video", "mp4"), "v720": ("720", "video", "mp4"),
                "v1080": ("1080", "video", "mp4"), "vbest": ("best", "video", "mp4")}
        for i, (preset, (q, typ, fmt)) in enumerate(want.items()):
            u = f"{URL}{i}"
            self.dl.submit(OWNER, u, preset)
            self.tick()
            body = self.fake.adds[-1]
            self.assertEqual((body["download_type"], body["format"], body["codec"], body["quality"]),
                             (typ, fmt, "h264", q), preset)
            self.assertEqual((body["folder"], body["auto_start"], body["url"]), (f"u{OWNER}", True, u))
        for i, (preset, fmt, q) in enumerate([("mp3", "mp3", "320"), ("m4a", "m4a", "best"), ("opus", "opus", "best")]):
            self.dl.submit(OWNER, f"{URL2}{i}", preset)
            self.tick()
            body = self.fake.adds[-1]
            self.assertEqual((body["download_type"], body["format"], body["quality"]), ("audio", fmt, q), preset)
            self.assertNotIn("codec", body)

    def test_clip_and_playlist_limit_in_body(self):
        self.dl.submit(10, URL, "v720", {"clip": {"start": 90, "end": "2:30"}})
        self.tick()
        b = self.fake.adds[-1]
        self.assertEqual((b["clip_start"], b["clip_end"], b["playlist_item_limit"]), (90, 150, 1))

    def test_subtitles_use_a_captions_item_first_then_the_video(self):
        jid = self.sub(10, opts={"subs": "ru"})
        self.tick()
        self.assertEqual(len(self.fake.adds), 1)
        c = self.fake.adds[0]
        self.assertEqual((c["download_type"], c["format"], c["subtitle_language"], c["subtitle_mode"]),
                         ("captions", "srt", "ru", "prefer_manual"))
        self.fake.finish(URL)
        self.tick()
        self.assertEqual(len(self.fake.adds), 2)               # the video is added only now (URL-keyed queue)
        v = self.fake.adds[1]
        self.assertEqual((v["download_type"], v["codec"], v["subtitle_language"]), ("video", "h264", "ru"))
        self.assertEqual(self.job(jid)["status"], "running")
        self.fake.finish(URL)
        self.tick()
        j = self.job(jid)
        self.assertEqual(j["status"], "done")
        self.assertEqual([f["kind"] for f in j["files"]], ["subs", "video"])


class TestValidation(EngineBase):
    def assertErr(self, status, code, *a, **kw):
        with self.assertRaises(dlm.DlError) as cm:
            self.dl.submit(*a, **kw)
        self.assertEqual((cm.exception.status, cm.exception.code), (status, code))
        return cm.exception

    def test_urls_presets_options(self):
        for bad in ("ftp://x.org/a", "javascript:alert(1)", "not a url", "", None, "https://", "https://a.b/ c"):
            self.assertErr(400, "bad_url", 10, bad, "v720")
        self.assertErr(400, "bad_preset", 10, URL, "v9000")
        self.assertErr(400, "bad_request", 10, URL, "v720", {"subs": "fr"})
        self.assertErr(400, "bad_request", 10, URL, "v720", {"clip": {"start": 50, "end": 10}})
        self.assertErr(400, "bad_request", 10, URL, "v720", {"clip": {"start": "x", "end": 10}})
        self.assertErr(400, "bad_request", 10, URL, "v720", {"playlist": "yes"})
        self.assertEqual(self.store.q("SELECT COUNT(*) FROM downloads")[0][0], 0)

    def test_pure_playlist_rejected_without_the_flag(self):
        for u in ("https://www.youtube.com/playlist?list=PLxyz", "https://youtube.com/playlist"):
            e = self.assertErr(400, "playlist", 10, u, "v720")
            self.assertEqual(e.message, "Это плейлист — включи «Плейлист (до 10)»")
        self.assertEqual(self.store.q("SELECT COUNT(*) FROM downloads")[0][0], 0)

    def test_watch_link_with_list_downloads_just_the_video_without_the_flag(self):
        j = self.dl.submit(10, "https://www.youtube.com/watch?v=abcdefghijk&list=PLxyz&index=3", "v720")
        self.assertEqual(j["url"], "https://www.youtube.com/watch?v=abcdefghijk")
        self.tick()
        self.assertEqual((self.fake.adds[-1]["url"], self.fake.adds[-1]["playlist_item_limit"]),
                         ("https://www.youtube.com/watch?v=abcdefghijk", 1))

    def test_watch_link_with_list_keeps_the_list_with_the_flag(self):
        j = self.dl.submit(10, "https://www.youtube.com/watch?v=abcdefghijk&list=PLxyz&index=3", "v720", {"playlist": True})
        self.assertEqual(j["url"], "https://www.youtube.com/watch?v=abcdefghijk&list=PLxyz")

    def test_playlist_accepted_with_the_flag_and_capped(self):
        u = "https://www.youtube.com/playlist?list=PLxyz"
        self.dl.submit(10, u, "v720", {"playlist": True})
        self.tick()
        self.assertEqual(self.fake.adds[-1]["playlist_item_limit"], 10)

    def test_add_error_from_metube_becomes_a_job_error(self):
        self.fake.fail_add = "Unsupported URL: https://x"
        jid = self.dl.submit(10, URL, "v720")["id"]
        self.tick()
        j = self.job(jid)
        self.assertEqual((j["status"], j["error"]), ("error", "Unsupported URL: https://x"))


class TestLimits(EngineBase):
    def assertLimit(self, uid, url):
        with self.assertRaises(dlm.DlError) as cm:
            self.dl.submit(uid, url, "v720")
        self.assertEqual((cm.exception.status, cm.exception.code), (429, "limit"))
        return cm.exception.message

    def test_active_limit_and_owner_exempt(self):
        self.dl.submit(10, URL, "v720")
        self.dl.submit(10, URL2, "v720")
        self.assertIn("не больше 2", self.assertLimit(10, URL + "3"))
        for i in range(5):                                       # the owner is exempt
            self.dl.submit(OWNER, f"{URL}o{i}", "v720")
        self.dl.submit(11, URL, "v720")                          # others are independent

    def test_hourly_limit(self):
        with mock.patch.dict(os.environ, {"DL_PER_HOUR": "3"}):
            for i in range(3):
                jid = self.dl.submit(10, f"{URL}{i}", "v720")["id"]
                self.dl._upd(jid, status="done")
            self.assertIn("в час", self.assertLimit(10, URL2))
            self.dl.submit(OWNER, URL2, "v720")
            self.store.q("UPDATE downloads SET created_ts=created_ts-3700")        # an hour later
            self.dl.submit(10, URL2, "v720")

    def test_daily_limit(self):
        with mock.patch.dict(os.environ, {"DL_PER_DAY": "4", "DL_PER_HOUR": "99"}):
            for i in range(4):
                jid = self.dl.submit(10, f"{URL}{i}", "v720")["id"]
                self.dl._upd(jid, status="done", created_ts=int(time.time()) - 7200)
            self.assertIn("в сутки", self.assertLimit(10, URL2))
            self.dl.submit(OWNER, URL2, "v720")

    def test_canceled_jobs_do_not_count(self):
        a = self.dl.submit(10, URL, "v720")["id"]
        self.dl.submit(10, URL2, "v720")
        self.dl.cancel(10, a)
        self.dl.submit(10, URL + "3", "v720")

    def test_disk_guard_applies_to_everyone(self):
        self.dl.free_gb = lambda: 12.0
        for uid in (10, OWNER):
            with self.assertRaises(dlm.DlError) as cm:
                self.dl.submit(uid, URL, "v720")
            self.assertEqual((cm.exception.status, cm.exception.code), (503, "disk_full"))
        self.dl.free_gb = lambda: 50.0
        self.dl.submit(10, URL, "v720")


class TestWorker(EngineBase):
    def test_progress_is_recorded(self):
        jid = self.dl.submit(10, URL, "v720")["id"]
        self.tick()
        self.assertEqual(self.job(jid)["status"], "running")
        self.fake.progress(URL, 40, size=5000)
        self.tick()
        j = self.job(jid)
        self.assertEqual((j["percent"], j["size"], j["title"], j["eta"]), (40, 5000, "Title AAAA", 30))
        out = self.dl.job_json(j)
        self.assertEqual((out["percent"], out["status"], out["preset_label"]), (40, "running", "720p"))

    def test_fan_out_identical_requests_share_one_item(self):
        a = self.sub(10)
        b = self.sub(11)
        self.tick()
        self.assertEqual(len(self.fake.adds), 1)                 # one MeTube add
        self.assertEqual(self.job(a)["lead"], a)
        self.assertEqual((self.job(b)["lead"], self.job(b)["status"]), (a, "running"))
        self.fake.progress(URL, 50)
        self.tick()
        self.assertEqual(self.job(b)["percent"], 50)
        self.fake.finish(URL, size=2000)
        self.tick()
        self.assertEqual([self.job(x)["status"] for x in (a, b)], ["done", "done"])
        self.assertEqual(self.fake.deletes, [])                  # the janitor cleans up, not us
        self.assertTrue(self.job(a)["files"] and self.job(b)["files"])
        self.assertEqual(len(self.fake.adds), 1)

    def test_different_preset_for_the_same_url_waits(self):
        a = self.dl.submit(10, URL, "v720")["id"]
        b = self.dl.submit(11, URL, "v1080")["id"]
        c = self.dl.submit(11, URL2, "v720")["id"]              # another URL is not held back
        self.tick()
        self.assertEqual([x["url"] for x in self.fake.adds], [URL, URL2])
        self.assertEqual(self.job(b)["status"], "queued")
        self.tick(3)
        self.assertEqual(len(self.fake.adds), 2)                 # still waiting, never "Already in queue"
        self.fake.finish(URL)
        self.tick(2)
        self.assertEqual(self.job(a)["status"], "done")
        self.assertEqual([x["quality"] for x in self.fake.adds if x["url"] == URL], ["720", "1080"])
        self.assertEqual(self.job(b)["status"], "running")
        self.assertEqual(self.job(c)["status"], "running")

    def test_metube_error_item_fails_the_job(self):
        a = self.sub(10)
        self.tick()
        self.fake.fail(URL, "Video unavailable")
        self.tick()
        j = self.job(a)
        self.assertEqual((j["status"], j["error"]), ("error", "Video unavailable"))

    def test_error_text_never_contains_the_metube_path(self):
        self.fake.fail_add = f"bad {PATH} thing"
        a = self.dl.submit(10, URL, "v720")["id"]
        self.tick()
        self.assertNotIn(PATH, self.job(a)["error"])

    def test_item_that_never_appears_times_out(self):
        a = self.dl.submit(10, URL, "v720")["id"]
        self.tick()
        self.fake.items.clear()
        self.dl.clock = lambda: time.time() + 200
        self.tick()
        self.assertEqual(self.job(a)["status"], "error")
        self.assertIn("не начал", self.job(a)["error"])

    def test_metube_down_keeps_jobs_queued_then_fails(self):
        self.fake.close()
        a = self.dl.submit(10, URL, "v720")["id"]
        self.tick()
        self.assertEqual(self.job(a)["status"], "queued")
        self.dl.clock = lambda: time.time() + 500
        self.tick()
        self.assertEqual(self.job(a)["status"], "error")
        self.fake.srv = mock.Mock()                              # (cleanup calls close() again)

    def test_worker_thread_runs_the_loop(self):
        self.dl.start()
        self.dl.start()                                          # idempotent
        jid = self.dl.submit(10, URL, "v720")["id"]
        for _ in range(100):
            if self.fake.adds:
                break
            time.sleep(0.05)
        self.assertEqual(len(self.fake.adds), 1)
        self.assertEqual(self.job(jid)["status"], "running")

    def test_legacy_chat_rows_still_load(self):
        jid = self.store.db.execute(
            "INSERT INTO downloads(uid,url,preset,opts,origin,chat_id,chat_msg_id,status,delivery,created_ts) "
            "VALUES(10,?,?,?,?,?,?,?,?,?)",
            ("https://youtu.be/AAAAAAAAAAA", "v720", '{"to_chat": true}', "chat", 10, 77, "done", "upload",
             int(time.time()))).lastrowid
        out = self.dl.jobs_for(10)
        self.assertEqual([(j["id"], j["status"], j["origin"]) for j in out], [(jid, "done", "chat")])
        self.assertNotIn("can_send", out[0])
        self.dl.tick()                                           # nothing to deliver or resume
        self.assertEqual(self.job(jid)["delivery"], "upload")

    def test_expired_when_the_file_is_gone(self):
        jid = self.dl.submit(10, URL, "v720")["id"]
        self.tick()
        self.fake.finish(URL)
        self.tick()
        j = self.job(jid)
        self.assertEqual(j["status"], "done")
        os.remove(os.path.join(self.root, "u10", "clip.mp4"))
        self.dl._housekeeping(time.time())
        j = self.job(jid)
        self.assertEqual((j["status"], j["files"]), ("expired", []))

    def test_expired_after_the_ttl(self):
        jid = self.dl.submit(10, URL, "v720")["id"]
        self.tick()
        self.fake.finish(URL)
        self.tick()
        self.dl._housekeeping(time.time() + 30 * 60 + 200)
        self.assertEqual(self.job(jid)["status"], "expired")


class TestCanonicalUrl(unittest.TestCase):
    W = "https://www.youtube.com/watch?v=yTA4bhZpHDw"

    def check(self, src, want, **kw):
        self.assertEqual(dlm.canonical_url(src, **kw), want, src)

    def test_youtu_be_share_links(self):
        self.check("https://youtu.be/yTA4bhZpHDw?is=ExX5atY71NIVN6rj", self.W)
        self.check("https://youtu.be/yTA4bhZpHDw?si=abc&t=42", self.W)
        self.check("  http://youtu.be/yTA4bhZpHDw  ", self.W)

    def test_other_youtube_shapes(self):
        for src in ("https://www.youtube.com/shorts/yTA4bhZpHDw?feature=share",
                    "https://m.youtube.com/watch?v=yTA4bhZpHDw&pp=ygU",
                    "https://music.youtube.com/watch?v=yTA4bhZpHDw&si=x",
                    "https://youtube.com/live/yTA4bhZpHDw?si=x",
                    "https://www.youtube.com/embed/yTA4bhZpHDw",
                    "https://www.youtube-nocookie.com/embed/yTA4bhZpHDw",
                    "https://www.youtube.com/v/yTA4bhZpHDw",
                    "https://www.youtube.com/watch?v=yTA4bhZpHDw&ab_channel=Foo&t=10s#x",
                    self.W):
            self.check(src, self.W)

    def test_list_is_dropped_unless_a_playlist_was_asked_for(self):
        src = "https://www.youtube.com/watch?v=yTA4bhZpHDw&list=PL123&index=2"
        self.check(src, self.W)
        self.check(src, self.W + "&list=PL123", keep_list=True)
        self.check("https://youtu.be/yTA4bhZpHDw?list=PL123&si=x", self.W + "&list=PL123", keep_list=True)

    def test_playlist_page(self):
        want = "https://www.youtube.com/playlist?list=PL123"
        self.check("https://www.youtube.com/playlist?list=PL123&si=x", want)
        self.check("https://music.youtube.com/playlist?list=PL123", want, keep_list=True)
        self.assertTrue(dlm.is_playlist(dlm.canonical_url("https://www.youtube.com/playlist?list=PL123")))
        self.assertFalse(dlm.is_playlist(dlm.canonical_url("https://www.youtube.com/watch?v=yTA4bhZpHDw&list=PL1")))

    def test_other_sites_only_lose_tracking(self):
        self.check("https://rutube.ru/video/abc123/", "https://rutube.ru/video/abc123/")
        self.check("https://rutube.ru/video/abc123/?utm_source=x&r=1&si=2", "https://rutube.ru/video/abc123/?r=1")
        self.check("https://vkvideo.ru/video-1_2?t=5", "https://vkvideo.ru/video-1_2?t=5")
        self.check("https://rutube.ru/video/abc123/?utm_source=x", "https://rutube.ru/video/abc123/")

    def test_unrecognised_youtube_pages_and_junk_are_untouched(self):
        for src in ("https://www.youtube.com/@channel", "https://youtu.be/", "https://www.youtube.com/watch",
                    "https://www.youtube.com/watch?v=", "not a url"):
            self.check(src, src)
        self.assertIsNone(dlm.canonical_url(None))

class TestMatching(EngineBase):
    SHARE = "https://youtu.be/AAAAAAAAAAA?is=ExX5atY71NIVN6rj"

    def test_youtu_be_share_link_completes(self):
        jid = self.sub(10, self.SHARE)
        self.assertEqual(self.job(jid)["url"], URL)
        self.tick()
        self.assertEqual(self.fake.adds[-1]["url"], URL)         # MeTube gets the spelling it will report back
        self.fake.progress(URL, 30)
        self.tick()
        self.assertEqual(self.job(jid)["percent"], 30)
        self.fake.finish(URL)
        self.tick()
        self.assertEqual(self.job(jid)["status"], "done")

    def test_same_video_in_two_spellings_is_one_item(self):
        a = self.sub(10, self.SHARE)
        b = self.sub(11, "https://www.youtube.com/shorts/AAAAAAAAAAA")
        self.tick()
        self.assertEqual(len(self.fake.adds), 1)
        self.assertEqual(self.job(b)["lead"], a)

    def test_item_reported_under_another_spelling_still_matches_by_canonical_url(self):
        jid = self.sub(10)
        self.tick()
        self.fake.items[URL]["url"] = "https://youtu.be/AAAAAAAAAAA"
        self.fake.progress(URL, 20)
        self.tick()
        self.assertEqual((self.job(jid)["status"], self.job(jid)["percent"]), ("running", 20))

    def test_fallback_takes_a_single_unclaimed_item_of_the_folder(self):
        jid = self.sub(10)
        self.tick()
        it = self.fake.items.pop(URL)
        it["url"] = "https://example.org/some-other-spelling"    # nothing canonical-equal to ours
        self.fake.items[it["url"]] = it
        self.fake.progress(it["url"], 55)
        self.tick()
        j = self.job(jid)
        self.assertEqual((j["status"], j["percent"], j["murls"]), ("running", 55, [it["url"]]))
        # cancel/delete go by the URL MeTube really uses
        self.dl.cancel(10, jid)
        self.assertEqual(self.fake.deletes, [{"ids": [it["url"]], "where": "queue"}])

    def test_fallback_ignores_items_claimed_by_another_job(self):
        a = self.sub(10, URL)
        b = self.sub(10, URL2)
        self.tick()
        for u in (URL, URL2):                                    # MeTube reports both under foreign spellings
            it = self.fake.items.pop(u)
            it["url"] = "https://example.org/" + u[-4:]
            self.fake.items[it["url"]] = it
        self.dl._upd(a, murls=["https://example.org/AAAA"])
        self.dl._upd(b, murls=["https://example.org/BBBB"])
        self.tick()
        self.assertEqual([self.job(x)["status"] for x in (a, b)], ["running", "running"])
        self.assertEqual([self.job(x)["murls"] for x in (a, b)], [["https://example.org/AAAA"], ["https://example.org/BBBB"]])

    def test_fallback_is_not_used_when_it_is_ambiguous(self):
        jid = self.sub(10)
        self.tick()
        base = self.fake.items.pop(URL)
        for n in ("x", "y"):
            self.fake.items["https://example.org/" + n] = dict(base, url="https://example.org/" + n)
        self.tick()
        self.assertEqual(self.job(jid)["status"], "running")      # unmatched: waits for the stale timeout
        self.dl.clock = lambda: time.time() + 200
        self.tick()
        self.assertEqual(self.job(jid)["status"], "error")

    def test_fallback_respects_kind_and_folder(self):
        jid = self.sub(10)
        self.tick()
        it = self.fake.items.pop(URL)
        self.fake.items["https://example.org/c"] = dict(it, url="https://example.org/c", download_type="captions")
        self.fake.items["https://example.org/o"] = dict(it, url="https://example.org/o", folder="u11")
        self.dl.clock = lambda: time.time() + 200
        self.tick()
        self.assertEqual(self.job(jid)["status"], "error")


class TestRestart(EngineBase):
    def test_running_job_reattaches_by_url_and_folder(self):
        jid = self.sub(10)
        self.tick()
        self.fake.progress(URL, 25)
        dl2 = self.make_dl()                                     # "the helper restarted"
        dl2.tick()
        self.assertEqual((self.job(jid)["status"], self.job(jid)["percent"]), ("running", 25))
        self.assertEqual(len(self.fake.adds), 1)
        self.fake.finish(URL)
        dl2.tick()
        self.assertEqual(self.job(jid)["status"], "done")

    def test_item_lost_during_the_restart_becomes_an_error(self):
        jid = self.sub(10)
        self.tick()
        self.fake.items.clear()
        dl2 = self.make_dl()
        dl2.tick()
        j = self.job(jid)
        self.assertEqual((j["status"], j["error"]), ("error", "прервано перезапуском"))

    def test_other_users_item_with_the_same_url_is_not_mistaken_for_ours(self):
        a = self.dl.submit(10, URL, "v720")["id"]
        self.tick()
        self.fake.items[URL]["folder"] = "u11"                   # someone else's folder
        dl2 = self.make_dl()
        dl2.tick()
        self.assertEqual(self.job(a)["status"], "error")

    def test_old_finished_record_is_not_taken_for_a_new_request(self):
        a = self.dl.submit(10, URL, "v720")["id"]
        self.tick()
        self.fake.finish(URL)
        self.tick()
        b = self.dl.submit(10, URL, "v720")["id"]                # same URL again: MeTube replaces the record
        self.tick()
        self.assertEqual(len(self.fake.adds), 2)
        self.assertEqual(self.job(b)["status"], "running")      # the old 'finished' record has an older stamp
        self.assertEqual(self.job(a)["status"], "done")


class TestCancelDelete(EngineBase):
    def test_cancel_queued_and_running(self):
        a = self.dl.submit(10, URL, "v720")["id"]
        b = self.dl.submit(10, URL2, "v720")["id"]
        self.dl.cancel(10, a)                                    # still queued: nothing to tell MeTube
        self.assertEqual(self.job(a)["status"], "canceled")
        self.tick()
        self.assertEqual(self.fake.adds[0]["url"], URL2)
        self.dl.cancel(10, b)
        self.assertEqual(self.fake.deletes, [{"ids": [URL2], "where": "queue"}])
        self.assertNotIn(URL2, self.fake.items)
        with self.assertRaises(dlm.DlError) as cm:
            self.dl.cancel(10, b)
        self.assertEqual(cm.exception.status, 409)
        with self.assertRaises(dlm.DlError) as cm:
            self.dl.cancel(11, b)                                # not yours
        self.assertEqual(cm.exception.status, 404)

    def test_canceling_a_lead_with_followers_keeps_the_item_running(self):
        a, b = self.sub(10), self.sub(11)
        self.tick()
        self.dl.cancel(10, a)
        self.assertEqual(self.fake.deletes, [])
        self.assertEqual((self.job(b)["lead"], self.job(b)["status"]), (b, "running"))
        self.fake.finish(URL)
        self.tick()
        self.assertEqual(self.job(b)["status"], "done")
        self.assertEqual(self.job(a)["status"], "canceled")

    def test_delete_removes_the_item_unless_shared(self):
        a = self.dl.submit(10, URL, "v720")["id"]
        b = self.dl.submit(11, URL, "v720")["id"]
        self.tick()
        self.fake.finish(URL)
        self.tick()
        self.dl.delete(10, a)                                    # b still shares it
        self.assertEqual(self.fake.deletes, [])
        self.assertIsNone(self.job(a))
        self.assertEqual(self.job(b)["lead"], b)
        self.assertTrue(self.job(b)["files"])
        self.dl.delete(11, b)
        self.assertEqual(self.fake.deletes, [{"ids": [URL], "where": "done"}])
        self.assertEqual(self.store.q("SELECT COUNT(*) FROM downloads")[0][0], 0)
        with self.assertRaises(dlm.DlError):
            self.dl.delete(11, b)

    def test_list_is_per_user_newest_first_within_24h(self):
        a = self.dl.submit(10, URL, "v720")["id"]
        b = self.dl.submit(10, URL2, "v720")["id"]
        self.dl.submit(11, URL, "v720")
        self.dl._upd(a, status="done")
        self.dl._upd(b, status="done")
        old = self.dl.submit(10, URL + "3", "v720")["id"]
        self.dl._upd(old, created_ts=int(time.time()) - 90000, status="done")
        self.assertEqual([j["id"] for j in self.dl.jobs_for(10)], [b, a])

    def test_admin_view(self):
        self.dl.submit(10, URL, "v720")
        self.tick()
        self.fake.finish(URL, size=4000)
        self.tick()
        self.dl.submit(OWNER, URL2, "v720")
        out = self.dl.admin(lambda uid: f"name{uid}")
        self.assertEqual(out["disk_free_gb"], 500.0)
        self.assertEqual((out["active"], out["today"]), (1, {"jobs": 2, "bytes": 4000}))
        self.assertEqual([(j["user"], j["uid"]) for j in out["jobs"]], [(f"name{OWNER}", OWNER), ("name10", 10)])
        for j in out["jobs"]:
            for f in j["files"]:
                self.assertNotIn("url", f)


# ---------------------------------------------------------- web: /dl + API --
class WebBase(tw.Base):
    def setUp(self):
        super().setUp()
        p = mock.patch.dict(os.environ, {"METUBE_PATH": PATH, "METUBE_TTL_MIN": "30", "DL_ACTIVE": "2",
                                         "DL_PER_HOUR": "10", "DL_PER_DAY": "30"})
        p.start()
        self.addCleanup(p.stop)
        self.root = os.path.join(self.tmp.name, "dl")
        os.makedirs(self.root)
        self.fake = FakeMeTube(self.root)
        self.addCleanup(self.fake.close)
        self.dl = dlm.Downloads(self.store, self.h.members, lambda: tw.OWNER, lambda: "https://example.org/app/",
                                self.fake.client(), self.root)
        self.dl.free_gb = lambda: 500.0
        self.h.downloads = self.dl
        self.h.members.set_service_enabled("youtube", True)
        self.make_member(200)
        self.make_member(201)

    def tick(self, n=1):
        for _ in range(n):
            self.dl.tick()

    def done_job(self, uid=200, url=URL, size=700 * 1024, preset="v720"):
        jid = self.dl.submit(uid, url, preset)["id"]
        self.tick()
        self.fake.finish(url, size=size)
        self.tick()
        self.assertEqual(self.dl._get(jid)["status"], "done")
        return jid

    @staticmethod
    def path_of(url):
        return "/dl/" + urllib.parse.urlsplit(url).path.rsplit("/dl/", 1)[1]

    def file_path(self, jid, idx=0):
        j = self.dl._get(jid)
        return self.path_of(self.dl.job_json(j)["files"][idx]["url"])

    def content(self, uid=200, name="clip.mp4"):
        with open(os.path.join(self.root, f"u{uid}", name), "rb") as f:
            return f.read()


class TestFileServing(WebBase):
    def setUp(self):
        super().setUp()
        self.jid = self.done_job()
        path = os.path.join(self.root, "u200", "clip.mp4")
        with open(path, "wb") as f:                       # recognisable bytes, bigger than one chunk
            f.write(bytes(range(256)) * (700 * 4))
        self.data = self.content()
        self.dl._upd(self.jid, files=[{"rel": "u200/clip.mp4", "name": "clip.mp4", "size": len(self.data),
                                       "kind": "video"}])
        self.path = self.file_path(self.jid)

    def test_full_download_headers(self):
        st, r, body = self.req("GET", self.path)
        self.assertEqual(st, 200)
        self.assertEqual(body, self.data)
        self.assertEqual(r.getheader("Content-Length"), str(len(self.data)))
        self.assertEqual(r.getheader("Content-Disposition"), "attachment; filename*=UTF-8''clip.mp4")
        self.assertEqual(r.getheader("Access-Control-Allow-Origin"), "https://web.telegram.org")
        self.assertEqual(r.getheader("Accept-Ranges"), "bytes")
        self.assertEqual(r.getheader("Content-Type"), "video/mp4")

    def test_unicode_filename_is_percent_encoded(self):
        os.rename(os.path.join(self.root, "u200", "clip.mp4"), os.path.join(self.root, "u200", "Кино №1.mp4"))
        self.dl._upd(self.jid, files=[{"rel": "u200/Кино №1.mp4", "name": "Кино №1.mp4", "size": len(self.data),
                                       "kind": "video"}])
        st, r, body = self.req("GET", self.file_path(self.jid))
        self.assertEqual(st, 200)
        self.assertEqual(r.getheader("Content-Disposition"),
                         "attachment; filename*=UTF-8''" + urllib.parse.quote("Кино №1.mp4", safe=""))

    def test_range_requests(self):
        n = len(self.data)
        st, r, body = self.req("GET", self.path, headers={"Range": "bytes=10-19"})
        self.assertEqual((st, body), (206, self.data[10:20]))
        self.assertEqual((r.getheader("Content-Range"), r.getheader("Content-Length")), (f"bytes 10-19/{n}", "10"))
        st, r, body = self.req("GET", self.path, headers={"Range": "bytes=262000-"})       # crosses the chunk size
        self.assertEqual((st, body), (206, self.data[262000:]))
        st, r, body = self.req("GET", self.path, headers={"Range": "bytes=-5"})
        self.assertEqual((st, body, r.getheader("Content-Range")), (206, self.data[-5:], f"bytes {n - 5}-{n - 1}/{n}"))
        st, r, body = self.req("GET", self.path, headers={"Range": f"bytes=0-{n * 3}"})
        self.assertEqual((st, len(body)), (206, n))
        st, r, body = self.req("GET", self.path, headers={"Range": f"bytes={n}-"})
        self.assertEqual((st, r.getheader("Content-Range")), (416, f"bytes */{n}"))
        st, r, body = self.req("GET", self.path, headers={"Range": "bytes=9-3"})
        self.assertEqual(st, 416)
        st, r, body = self.req("GET", self.path, headers={"Range": "lines=1-2"})
        self.assertEqual(st, 416)

    def link_for(self, uid, ident, **kw):
        return "/dl/" + links.token("file", ident, uid, store=self.store, **kw)

    def test_other_user_gets_403(self):
        self.assertEqual(self.req("GET", self.link_for(201, f"{self.jid}:0"))[0], 403)     # job is 200's
        self.assertEqual(self.req("GET", self.link_for(200, f"{self.jid}:0"))[0], 200)

    def test_forged_and_expired_tokens(self):
        self.assertEqual(self.req("GET", self.path[:-3] + "AAA")[0], 403)
        st, r, body = self.req("GET", self.link_for(200, f"{self.jid}:0", ttl=-5))
        self.assertEqual((st, json.loads(body)["error"]), (403, "bad_link"))

    def test_revoked_service_gives_403_and_owner_is_always_in(self):
        self.h.members.set_service_enabled("youtube", False)
        st, r, body = self.req("GET", self.path)
        self.assertEqual((st, json.loads(body)["error"]), (403, "no_access"))
        self.h.members.set_service_enabled("youtube", True)
        self.h.members.suspend(200)
        self.assertEqual(self.req("GET", self.path)[0], 403)
        self.h.members.resume(200)
        self.assertEqual(self.req("GET", self.path)[0], 200)
        # the owner needs no membership and no switch
        self.h.members.set_service_enabled("youtube", False)
        oj = self.done_job(tw.OWNER, URL2, size=100)
        self.assertEqual(self.req("GET", self.file_path(oj))[0], 200)

    def test_traversal_and_foreign_paths_give_404(self):
        secret = os.path.join(self.root, "u201")
        os.makedirs(secret)
        with open(os.path.join(secret, "other.mp4"), "wb") as f:
            f.write(b"theirs")
        with open(os.path.join(self.tmp.name, "secret.txt"), "w") as f:
            f.write("SECRET")
        os.symlink(os.path.join(self.tmp.name, "secret.txt"), os.path.join(self.root, "u200", "ln.mp4"))
        for rel in ("u200/../../secret.txt", "u200/../u201/other.mp4", "u201/other.mp4", "../secret.txt",
                    "/etc/passwd", "u200/ln.mp4", "other/clip.mp4", "u200/missing.mp4", "u200"):
            self.dl._upd(self.jid, files=[{"rel": rel, "name": "x.mp4", "size": 1, "kind": "video"}])
            st, r, body = self.req("GET", self.link_for(200, f"{self.jid}:0"))
            self.assertEqual(st, 404, rel)
            self.assertNotIn(b"SECRET", body)
            self.assertNotIn(b"theirs", body)

    def test_bad_idents_and_states_give_404(self):
        for ident in (f"{self.jid}:5", f"{self.jid}:-1", "999:0", "x:y", f"{self.jid}", "a"):
            self.assertEqual(self.req("GET", self.link_for(200, ident))[0], 404, ident)
        self.dl._upd(self.jid, status="expired")
        self.assertEqual(self.req("GET", self.path)[0], 404)

    def test_deleted_file_gives_404(self):
        os.remove(os.path.join(self.root, "u200", "clip.mp4"))
        self.assertEqual(self.req("GET", self.path)[0], 404)

    def test_follower_is_served_from_the_leads_folder(self):
        os.makedirs(os.path.join(self.root, "u201"), exist_ok=True)
        a = self.dl.submit(200, URL2, "v720")["id"]
        b = self.dl.submit(201, URL2, "v720")["id"]
        self.tick()
        self.fake.finish(URL2, size=300)
        self.tick()
        st, r, body = self.req("GET", self.file_path(b))
        self.assertEqual((st, len(body)), (200, 300))
        self.assertEqual(self.dl._get(b)["files"][0]["rel"], "u200/clip.mp4")


class TestApi(WebBase):
    def test_access_rules(self):
        st, d = self.j("GET", "/api/dl/presets", headers=self.tgh(200))
        self.assertEqual((st, d["video"][0]), (200, {"id": "v360", "label": "360p"}))
        self.assertEqual(self.j("GET", "/api/dl/presets", headers=self.admin())[0], 200)
        self.assertEqual(self.j("GET", "/api/dl/presets")[0], 401)
        self.assertEqual(self.j("GET", "/api/dl/presets", headers=self.tgh(999))[0], 403)          # stranger
        self.h.members.set_service_enabled("youtube", False)
        st, d = self.j("GET", "/api/dl", headers=self.tgh(200))
        self.assertEqual((st, d["error"]), (403, "no_access"))
        self.assertEqual(self.j("GET", "/api/dl", headers=self.admin())[0], 200)                   # owner always

    def test_create_list_cancel_delete(self):
        h = self.tgh(200)
        st, job = self.j("POST", "/api/dl", {"url": "https://youtu.be/AAAAAAAAAAA?is=zzz", "preset": "v720", "subs": None}, h)
        self.assertEqual(st, 200)
        self.assertEqual((job["status"], job["preset"], job["preset_label"], job["files"], job["url"]),
                         ("queued", "v720", "720p", [], URL))
        self.assertEqual(set(job), {"id", "url", "preset", "preset_label", "title", "status", "percent", "size", "eta",
                                    "error", "created_ts", "finished_ts", "expires_ts", "files", "origin"})
        self.tick()
        self.fake.finish(URL, size=500)
        self.tick()
        st, lst = self.j("GET", "/api/dl", headers=h)
        self.assertEqual([x["id"] for x in lst], [job["id"]])
        j = lst[0]
        self.assertEqual((j["status"], j["title"]), ("done", "Title AAAA"))
        self.assertTrue(j["files"][0]["url"].startswith("https://example.org/app/dl/"))
        self.assertEqual((j["files"][0]["name"], j["files"][0]["kind"], j["files"][0]["size"]), ("clip.mp4", "video", 500))
        self.assertEqual(j["expires_ts"], j["finished_ts"] + 1800)
        # other users see nothing, cannot touch it
        self.assertEqual(self.j("GET", "/api/dl", headers=self.tgh(201))[1], [])
        for act in ("cancel", "delete"):
            self.assertEqual(self.j("POST", f"/api/dl/{job['id']}/{act}", {}, self.tgh(201))[0], 404, act)
        self.assertEqual(self.j("POST", f"/api/dl/{job['id']}/cancel", {}, h)[1]["error"], "not_active")
        self.assertEqual(self.j("POST", f"/api/dl/{job['id']}/send", {}, h)[0], 404)          # chat delivery is gone
        self.assertEqual(self.j("POST", f"/api/dl/{job['id']}/delete", {}, h), (200, {"ok": True}))
        self.assertEqual(self.fake.deletes, [{"ids": [URL], "where": "done"}])
        self.assertEqual(self.j("GET", "/api/dl", headers=h)[1], [])

    def test_cancel_over_the_api(self):
        h = self.tgh(200)
        job = self.j("POST", "/api/dl", {"url": URL, "preset": "mp3"}, h)[1]
        self.tick()
        st, c = self.j("POST", f"/api/dl/{job['id']}/cancel", {}, h)
        self.assertEqual((st, c["status"]), (200, "canceled"))
        self.assertEqual(self.fake.deletes, [{"ids": [URL], "where": "queue"}])

    def test_errors_are_mapped(self):
        h = self.tgh(200)
        st, d = self.j("POST", "/api/dl", {"url": "https://www.youtube.com/playlist?list=PL1", "preset": "v720"}, h)
        self.assertEqual((st, d["error"], d["message"]), (400, "playlist", "Это плейлист — включи «Плейлист (до 10)»"))
        self.assertEqual(self.j("POST", "/api/dl", {"url": "ftp://x", "preset": "v720"}, h)[1]["error"], "bad_url")
        self.assertEqual(self.j("POST", "/api/dl", {"url": URL, "preset": "zzz"}, h)[1]["error"], "bad_preset")
        self.assertEqual(self.j("POST", "/api/dl", {"url": URL, "preset": "v720", "clip": {"start": 9, "end": 1}},
                                h)[0], 400)
        self.assertEqual(self.j("POST", "/api/dl", {"url": URL, "preset": "v720", "playlist": True}, h)[0], 200)
        self.dl.free_gb = lambda: 3.0
        st, d = self.j("POST", "/api/dl", {"url": URL2, "preset": "v720"}, h)
        self.assertEqual((st, d["error"]), (503, "disk_full"))
        self.dl.free_gb = lambda: 300.0
        self.j("POST", "/api/dl", {"url": URL2, "preset": "v720"}, h)
        st, d = self.j("POST", "/api/dl", {"url": URL + "x", "preset": "v720"}, h)
        self.assertEqual((st, d["error"]), (429, "limit"))
        self.assertEqual(self.j("POST", "/api/dl", {"url": URL, "preset": "v720"}, self.admin())[0], 200)   # owner

    def test_admin_dl(self):
        self.j("POST", "/api/dl", {"url": URL, "preset": "v720"}, self.tgh(200))
        self.assertEqual(self.j("GET", "/api/admin/dl", headers=self.tgh(200))[0], 403)
        st, d = self.j("GET", "/api/admin/dl", headers=self.admin())
        self.assertEqual(st, 200)
        self.assertEqual((d["disk_free_gb"], d["active"], d["today"]), (500.0, 1, {"jobs": 1, "bytes": 0}))
        self.assertEqual((d["jobs"][0]["user"], d["jobs"][0]["uid"]), ("Bob", 200))
        self.assertEqual(d["limits"]["per_day"], 30)

    def test_engine_missing_gives_503(self):
        del self.h.downloads
        self.assertEqual(self.j("GET", "/api/dl", headers=self.admin())[0], 503)


# ------------------------------------------------------------------- bots --
class ChatBase(unittest.TestCase):
    """One bot (the helper bot); the owner and a member talk to it."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        p = mock.patch.dict(os.environ, dict(tb.ENV, METUBE_PATH=PATH, METUBE_TTL_MIN="30"))
        p.start()
        self.addCleanup(p.stop)
        self.root = os.path.join(self.tmp.name, "dl")
        os.makedirs(self.root)
        self.fake = FakeMeTube(self.root)
        self.addCleanup(self.fake.close)
        self.h = tb.botmod.Helper(Store(os.path.join(self.tmp.name, "t.db")))
        self.h.bot = self.b = UpBot()
        self.h.bot_username = "TestBot"
        self.mm = self.h.members
        self.dl = self.h.downloads
        self.dl.mt, self.dl.root = self.fake.client(), self.root
        self.dl.free_gb = lambda: 500.0

    def tick(self, n=1):
        for _ in range(n):
            self.dl.tick()

    @staticmethod
    def flat(entry):
        return [b for r in entry[3]["reply_markup"]["inline_keyboard"] for b in r]


class TestChatFlow(ChatBase):
    APP = "https://example.org/app/"

    def handoff(self, entry, url=URL):
        self.assertEqual(entry[2], "🎬 Скачать можно в MeowHub — ссылка уже подставлена.")
        rows = entry[3]["reply_markup"]["inline_keyboard"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(rows[0]), 1)
        btn = rows[0][0]
        self.assertEqual(btn["text"], "Открыть загрузчик")
        self.assertEqual(set(btn), {"text", "web_app"})              # a web_app button, no callback
        self.assertEqual(btn["web_app"]["url"], self.APP + "?p=downloads&url=" + urllib.parse.quote(url, safe=""))

    def test_link_gets_one_message_with_the_app_button_and_no_job(self):
        self.h.on_message(tb.msg(tb.OWNER, URL))
        sent = self.b.sent(tb.OWNER)
        self.assertEqual(len(sent), 1)
        self.handoff(sent[0])
        self.assertEqual(self.dl._rows(""), [])
        self.assertEqual(self.fake.adds, [])

    def test_the_prefilled_url_is_canonical(self):
        self.h.on_message(tb.msg(tb.OWNER, "https://youtu.be/AAAAAAAAAAA?is=ExX5atY71NIVN6rj"))
        self.handoff(self.b.sent(tb.OWNER)[0])
        self.h.on_message(tb.msg(tb.OWNER, "/yt https://m.youtube.com/watch?v=AAAAAAAAAAA&list=PL1&si=x"))
        self.handoff(self.b.sent(tb.OWNER)[1], URL + "&list=PL1")     # the list survives: the app decides

    def test_playlist_link_is_handed_over_too(self):
        self.h.on_message(tb.msg(tb.OWNER, "https://www.youtube.com/playlist?list=PL1"))
        self.handoff(self.b.sent(tb.OWNER)[0], "https://www.youtube.com/playlist?list=PL1")

    def test_old_chat_callbacks_are_gone(self):
        for data in ("dl:abcd1234:v720", "dlc:1"):
            self.h.on_callback(tb.cb(tb.OWNER, data, 600))
            self.assertEqual(self.b.answers()[-1][2], "Кнопка устарела")
        self.assertEqual(self.dl._rows(""), [])

    def test_button_and_command_variants(self):
        self.h.on_message(tb.msg(tb.OWNER, tb.botmod.B_YT))
        self.assertEqual(self.b.sent(tb.OWNER)[-1][2], "Пришли ссылку — открою загрузчик (YouTube, RuTube, VK Видео).")
        self.h.on_message(tb.msg(tb.OWNER, "/yt"))
        self.assertIn("/yt https://", self.b.sent(tb.OWNER)[-1][2])
        self.h.on_message(tb.msg(tb.OWNER, f"смотри {URL} круто"))
        self.handoff(self.b.sent(tb.OWNER)[-1])
        n = len(self.b.sent(tb.OWNER))
        self.h.on_message(tb.msg(tb.OWNER, f"/setpass nextcloud login https://x.org/{PATH}"))
        self.assertNotIn("Скачать можно", "".join(e[2] for e in self.b.sent(tb.OWNER)[n:]))

    def test_member_without_youtube_is_told_once_per_hour(self):
        self.mm.grant(50, tb.frm(50), 30, ["vpn", "tools"])
        self.h.on_message(tb.msg(50, URL))
        self.h.on_message(tb.msg(50, URL2))
        self.h.on_message(tb.msg(50, "/yt " + URL))
        self.assertEqual([e[2] for e in self.b.sent(50)], ["Скачивание видео сейчас выключено."])
        self.assertEqual(self.dl._rows(""), [])
        self.h.yt_nag[50] -= 3601
        self.h.on_message(tb.msg(50, URL))
        self.assertEqual(len(self.b.sent(50)), 2)

    def test_member_with_youtube_gets_the_handoff_and_the_keyboard_button(self):
        self.mm.set_service_enabled("youtube", True)
        self.mm.grant(50, tb.frm(50), 30, [])
        labels = [b["text"] for r in self.h.keyboard(50)["keyboard"] for b in r]
        self.assertIn(tb.botmod.B_YT, labels)
        self.assertEqual(tb.botmod.B_YT, "🎬 Скачать видео")
        self.h.on_message(tb.msg(50, tb.botmod.B_YT))
        self.assertIn("Пришли ссылку", self.b.sent(50)[-1][2])
        self.h.on_message(tb.msg(50, URL))
        self.handoff(self.b.sent(50)[-1])
        self.assertEqual(self.dl._rows(""), [])
        owner_labels = [b["text"] for r in self.h.keyboard(tb.OWNER)["keyboard"] for b in r]
        self.assertIn(tb.botmod.B_YT, owner_labels)

    def test_no_miniapp_means_plain_text(self):
        self.h.webapp_url = ""
        self.h.on_message(tb.msg(tb.OWNER, URL))
        e = self.b.sent(tb.OWNER)[0]
        self.assertIn("мини-приложении MeowHub", e[2])
        self.assertIsNone(e[3].get("reply_markup"))


class TestTwoBotRouting(t2.TwoBots):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        p = mock.patch.dict(os.environ, {"METUBE_PATH": PATH, "METUBE_TTL_MIN": "30"})
        p.start()
        self.addCleanup(p.stop)
        self.root = os.path.join(self.tmp.name, "dl")
        os.makedirs(self.root)
        self.fake = FakeMeTube(self.root)
        self.addCleanup(self.fake.close)
        self.h.bot = self.hb = UpBot()
        self.h.bot_username = "HelperBot"
        self.mb = UpBot()
        self.mb.token = t2.MTOKEN
        self.h.attach_member_bot(self.mb, "MemberBot")
        self.front = self.h.member_front
        self.dl = self.h.downloads
        self.dl.mt, self.dl.root = self.fake.client(), self.root
        self.dl.free_gb = lambda: 500.0
        self.mm.set_service_enabled("youtube", True)

    def test_owner_uses_the_helper_bot_and_members_the_member_bot(self):
        uid = self.make_member(50, [])
        self.h.on_message(tb.msg(t2.OWNER_ID, URL))
        self.front.on_message(tb.msg(uid, URL))
        self.assertEqual(len(self.hb.sent(t2.OWNER_ID)), 1)
        self.assertEqual(len(self.mb.sent(uid)), 1)
        self.assertEqual(self.hb.sent(uid), [])
        for e in (self.hb.sent(t2.OWNER_ID)[0], self.mb.sent(uid)[0]):
            btn = e[3]["reply_markup"]["inline_keyboard"][0][0]
            self.assertEqual(btn["text"], "Открыть загрузчик")
            self.assertIn("?p=downloads&url=", btn["web_app"]["url"])
        self.assertEqual(self.dl._rows(""), [])


if __name__ == "__main__":
    unittest.main()
