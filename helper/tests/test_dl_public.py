"""The guest video page: downloads without Telegram (negative uids, X-Guest, /api/pub/*)."""
import json
import os
import time
import unittest
import urllib.parse
from unittest import mock

import downloads as dlm
import links
import test_downloads as td
import test_webapp as tw
import webapp

URL, URL2 = td.URL, td.URL2
G1, G2 = -101, -102


class TestEngine(td.EngineBase):
    def setUp(self):
        super().setUp()
        p = mock.patch.dict(os.environ, {"DL_GUEST_ACTIVE": "1", "DL_GUEST_PER_HOUR": "3", "DL_GUEST_PER_DAY": "4",
                                         "DL_GUEST_ALL_ACTIVE": "2", "DL_GUEST_ALL_DAY": "6"})
        p.start()
        self.addCleanup(p.stop)
        self.dl.set_public_enabled(True)

    def err(self, uid, url=URL, ip="1.1.1.1", opts=None):
        with self.assertRaises(dlm.DlError) as cm:
            self.dl.submit(uid, url, "v720", opts, ip=ip)
        return cm.exception

    def test_switch_is_off_by_default(self):
        self.store.q("DELETE FROM settings WHERE key='dl_public'")
        self.assertFalse(self.dl.public_enabled())
        e = self.err(G1)
        self.assertEqual((e.status, e.code), (403, "public_off"))
        self.dl.submit(10, URL, "v720")                          # members are not affected

    def test_ip_is_stored_for_guests_only(self):
        a = self.dl.submit(G1, URL, "v720", ip="1.2.3.4")["id"]
        b = self.dl.submit(10, URL2, "v720", ip="9.9.9.9")["id"]
        self.assertEqual((self.job(a)["ip"], self.job(b)["ip"]), ("1.2.3.4", ""))

    def test_limits_follow_the_ip_across_new_ids(self):
        self.dl.submit(G1, URL, "v720", ip="1.1.1.1")
        e = self.err(G2, URL2, ip="1.1.1.1")                   # a fresh id from the same address
        self.assertEqual((e.status, e.code), (429, "limit"))
        self.assertIn("не больше 1", e.message)
        self.dl.submit(G2, URL2, "v720", ip="2.2.2.2")           # another address is independent

    def test_hourly_and_daily_per_guest(self):
        for i in range(3):
            jid = self.dl.submit(G1, f"{URL}{i}", "v720", ip="1.1.1.1")["id"]
            self.dl._upd(jid, status="done")
        self.assertIn("в час", self.err(G1, URL2).message)
        self.store.q("UPDATE downloads SET created_ts=created_ts-3700")
        jid = self.dl.submit(G1, URL2, "v720", ip="1.1.1.1")["id"]
        self.dl._upd(jid, status="done")
        self.assertIn("в сутки", self.err(G1, URL2 + "x").message)

    def test_all_guests_together(self):
        self.dl.submit(G1, URL, "v720", ip="1.1.1.1")
        self.dl.submit(G2, URL2, "v720", ip="2.2.2.2")
        e = self.err(-103, URL + "3", ip="3.3.3.3")
        self.assertEqual((e.status, e.code), (429, "busy"))
        self.dl.submit(10, URL + "4", "v720")                    # members don't count
        self.store.q("UPDATE downloads SET status='done' WHERE uid<0")
        for i in range(4):                                       # 6 guest downloads today in all
            self.dl.submit(-104 - i, f"{URL}g{i}", "v720", ip=f"4.4.4.{i}")
            self.store.q("UPDATE downloads SET status='done' WHERE uid<0")
        e = self.err(-108, URL + "9", ip="8.8.8.8")
        self.assertEqual(e.code, "busy")
        self.assertIn("завтра", e.message)

    def test_no_playlists_for_guests(self):
        e = self.err(G1, opts={"playlist": True})
        self.assertEqual((e.status, e.code), (400, "playlist"))
        e = self.err(G1, "https://www.youtube.com/playlist?list=PL123")
        self.assertIn("одно видео", e.message)
        self.assertEqual(dlm.presets_payload("ru", guest=True)["extras"]["playlist_max"], 0)
        self.assertEqual(dlm.presets_payload("ru", guest=True)["limits"]["active"], 1)

    def test_members_start_before_guests(self):
        os.environ["DL_GUEST_ALL_ACTIVE"] = "5"
        self.dl.submit(G1, URL, "v720", ip="1.1.1.1")
        self.dl.submit(10, URL2, "v720")
        self.tick()
        self.assertEqual([a["folder"] for a in self.fake.adds], ["u10", f"u{G1}"])

    def test_guest_files_have_relative_links_and_the_switch_guards_them(self):
        jid = self.dl.submit(G1, URL, "v720", ip="1.1.1.1")["id"]
        self.tick()
        self.fake.finish(URL, size=300)
        self.tick()
        j = self.dl.job_json(self.job(jid))
        self.assertTrue(j["files"][0]["url"].startswith("./dl/"))
        kind, ident, uid = links.verify(j["files"][0]["url"][len("./dl/"):])
        path, name = self.dl.file_for_link(uid, ident)
        self.assertEqual((uid, name, os.path.basename(os.path.dirname(path))), (G1, "clip.mp4", f"u{G1}"))
        self.dl.set_public_enabled(False)
        with self.assertRaises(dlm.DlError) as cm:
            self.dl.file_for_link(uid, ident)
        self.assertEqual(cm.exception.code, "public_off")
        self.assertIsNone(self.dl._safe_path("u-x/clip.mp4"))

    def test_usage_notice_names_the_guest_and_its_ip(self):
        reps = []

        class U:
            def report(self, service, uid, make, user=None):
                reps.append((service, uid, make("ru", "WHO")))
        self.dl.usage = U()
        self.dl.submit(G1, URL, "mp3", ip="1.2.3.4")
        self.tick()
        self.fake.finish(URL, size=3 * td.MB)
        self.tick()
        self.assertEqual(len(reps), 1)
        svc, uid, text = reps[0]
        self.assertEqual((svc, uid), ("ytpub", G1))
        self.assertIn("1.2.3.4", text)
        self.assertNotIn("WHO", text)


class TestPublicApi(td.WebBase):
    def setUp(self):
        super().setUp()
        self.dl.set_public_enabled(True)

    def guest(self, ip=None):
        st, d = self.j("POST", "/api/pub/hello", {}, {"X-Forwarded-For": ip} if ip else None)
        self.assertEqual(st, 200)
        return {"X-Guest": d["token"]}

    def test_hello_gives_a_negative_signed_id(self):
        h = self.guest()
        kind, _i, uid = links.verify(h["X-Guest"])
        self.assertEqual(kind, "guest")
        self.assertLess(uid, 0)
        self.assertNotEqual(links.verify(self.guest()["X-Guest"])[2], uid)

    def test_hello_is_rate_limited_per_ip(self):
        with mock.patch.object(webapp, "GUEST_NEW_PER_HOUR", 2):
            self.guest("5.5.5.5")
            self.guest("5.5.5.5")
            st, d = self.j("POST", "/api/pub/hello", {}, {"X-Forwarded-For": "5.5.5.5"})
            self.assertEqual((st, d["error"]), (429, "guest_new"))
            self.guest("6.6.6.6")

    def test_auth(self):
        self.assertEqual(self.j("GET", "/api/pub/dl")[1]["error"], "guest_session")
        self.assertEqual(self.j("GET", "/api/pub/dl", headers=self.tgh(200))[0], 401)      # Telegram isn't a guest
        self.assertEqual(self.j("GET", "/api/pub/dl", headers=self.admin())[0], 401)
        forged = {"X-Guest": links.token("file", "web", -5)}                                # another kind of token
        self.assertEqual(self.j("GET", "/api/pub/dl", headers=forged)[0], 401)
        positive = {"X-Guest": links.token("guest", "web", 200)}                            # never a real uid
        self.assertEqual(self.j("GET", "/api/pub/dl", headers=positive)[0], 401)
        expired = {"X-Guest": links.token("guest", "web", -5, ttl=-1)}
        self.assertEqual(self.j("GET", "/api/pub/dl", headers=expired)[0], 401)
        # and a guest is nobody on the member API
        h = self.guest()
        self.assertEqual(self.j("GET", "/api/dl", headers=h)[0], 401)
        self.assertEqual(self.j("GET", "/api/me", headers=h)[0], 401)

    def test_switch_off(self):
        h = self.guest()
        self.dl.set_public_enabled(False)
        st, d = self.j("GET", "/api/pub/dl/presets", headers=h)
        self.assertEqual((st, d["error"]), (403, "public_off"))
        self.assertIn("выключена", d["message"])
        st, d = self.j("GET", "/api/pub/dl/presets", headers=dict(h, **{"X-Lang": "en"}))
        self.assertIn("switched off", d["message"])

    def test_create_list_download_delete(self):
        h = self.guest("1.2.3.4")
        st, p = self.j("GET", "/api/pub/dl/presets", headers=h)
        self.assertEqual((st, p["extras"]["playlist_max"]), (200, 0))
        st, job = self.j("POST", "/api/pub/dl", {"url": URL, "preset": "v720"}, dict(h, **{"X-Forwarded-For": "1.2.3.4"}))
        self.assertEqual((st, job["status"], job["origin"]), (200, "queued", "web"))
        self.assertEqual(self.dl._get(job["id"])["ip"], "1.2.3.4")
        self.tick()
        self.fake.finish(URL, size=400)
        self.tick()
        lst = self.j("GET", "/api/pub/dl", headers=h)[1]
        self.assertEqual([x["id"] for x in lst], [job["id"]])
        url = lst[0]["files"][0]["url"]
        self.assertTrue(url.startswith("./dl/"))
        st, r, body = self.req("GET", url[1:])
        self.assertEqual((st, len(body)), (200, 400))
        # another guest sees nothing and cannot touch it
        other = self.guest("7.7.7.7")
        self.assertEqual(self.j("GET", "/api/pub/dl", headers=other)[1], [])
        self.assertEqual(self.j("POST", f"/api/pub/dl/{job['id']}/delete", {}, other)[0], 404)
        self.assertEqual(self.j("POST", f"/api/pub/dl/{job['id']}/delete", {}, h), (200, {"ok": True}))

    def test_cancel_and_limit_errors(self):
        h = dict(self.guest(), **{"X-Forwarded-For": "1.1.1.1"})
        job = self.j("POST", "/api/pub/dl", {"url": URL, "preset": "mp3"}, h)[1]
        st, d = self.j("POST", "/api/pub/dl", {"url": URL2, "preset": "mp3"}, h)
        self.assertEqual((st, d["error"]), (429, "limit"))
        st, c = self.j("POST", f"/api/pub/dl/{job['id']}/cancel", {}, h)
        self.assertEqual((st, c["status"]), (200, "canceled"))
        st, d = self.j("POST", "/api/pub/dl", {"url": URL2, "preset": "v720", "playlist": True}, h)
        self.assertEqual((st, d["error"]), (400, "playlist"))

    def test_admin_switch(self):
        self.assertEqual(self.j("GET", "/api/admin/dl-public", headers=self.tgh(200))[0], 403)
        st, d = self.j("GET", "/api/admin/dl-public", headers=self.admin())
        self.assertEqual((st, d["enabled"], d["today"]), (200, True, 0))
        st, d = self.j("POST", "/api/admin/dl-public", {"enabled": False}, self.admin())
        self.assertEqual((st, d["enabled"]), (200, False))
        self.assertFalse(self.dl.public_enabled())
        self.assertEqual(self.j("POST", "/api/admin/dl-public", {"enabled": "yes"}, self.admin())[0], 400)

    def test_admin_list_names_guests(self):
        h = self.guest()
        self.j("POST", "/api/pub/dl", {"url": URL, "preset": "v720"}, h)
        jobs = self.j("GET", "/api/admin/dl", headers=self.admin())[1]["jobs"]
        self.assertIn("Гость", jobs[0]["user"])


class TestVideoHtml(tw.Base):
    def test_video_html_is_not_cached(self):
        with open(os.path.join(self.tmp.name, "www", "video.html"), "w") as f:
            f.write("<html>VIDEO</html>")
        st, r, body = self.req("GET", "/video.html")
        self.assertEqual((st, body, r.getheader("Cache-Control")), (200, b"<html>VIDEO</html>", "no-cache"))


if __name__ == "__main__":
    unittest.main()
