"""Admin panels: off the hub (2026-11-14), on a signed page the helper bot hands out with /admin."""
import json
import os
import tempfile
import unittest
from unittest import mock

import hub
import links
import test_bot as tb
import test_webapp as tw

CARDS = [
    {"id": "metube", "name": "Downloader", "description": "videos", "url": "https://example.org/v/",
     "status": "live", "section": "services", "gradient": ["#ff0000", "#ff6d6d"]},
    {"id": "3xui", "name": "3x-ui", "description": "Xray panel", "url": "https://example.org:10358/p/",
     "status": "live", "section": "admin", "gradient": ["#7928ca", "#ff0080"]},
    {"id": "bots", "name": "Bots", "description": "Members", "url": "https://example.org/b/",
     "status": "live", "section": "admin", "gradient": ["red;}</style><script>", "#7dd3fc"]},
]


def services_file(test):
    d = tempfile.mkdtemp()
    test.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
    path = os.path.join(d, "services.json")
    with open(path, "w") as f:
        json.dump(CARDS, f)
    p = mock.patch.object(hub, "SERVICES", path)
    p.start()
    test.addCleanup(p.stop)


class TestHubSplit(unittest.TestCase):
    def test_split(self):
        services_file(self)
        self.assertEqual([s["id"] for s in hub.public_services()], ["metube"])
        self.assertEqual([s["id"] for s in hub.admin_services()], ["3xui", "bots"])
        self.assertEqual(len(hub.services()), 3)              # health checks still see every card


class TestBotAdmin(unittest.TestCase):
    make_member = tb.BotTest.make_member                       # borrow, so BotTest's own tests don't rerun here

    def setUp(self):
        tb.BotTest.setUp(self)
        services_file(self)

    def test_owner_gets_a_signed_link(self):
        self.h.on_message(tb.msg(tb.OWNER, "/admin"))
        m = self.b.sent(tb.OWNER)[-1]
        self.assertIn("3x-ui, Bots", m[2])
        self.assertTrue(m[3]["protect_content"])
        url = m[3]["reply_markup"]["inline_keyboard"][0][0]["url"]
        self.assertTrue(url.startswith("https://example.org/app/dl/"), url)
        kind, _ident, uid = links.verify(url.rsplit("/", 1)[1], store=self.h.store)
        self.assertEqual((kind, uid), ("admin", tb.OWNER))

    def test_button_and_keyboard(self):
        kb = [b["text"] for row in self.h.keyboard(tb.OWNER)["keyboard"] for b in row]
        self.assertIn("🛠 Админка", kb)
        self.h.on_message(tb.msg(tb.OWNER, "🛠 Админка"))
        self.assertIn("Админ-панели", self.b.sent(tb.OWNER)[-1][2])

    def test_members_cannot(self):
        uid = self.make_member(60, services=["vpn", "tools"])
        self.h.on_message(tb.msg(uid, "/admin"))
        self.assertNotIn("dl/", json.dumps(self.b.sent(uid)))

    def test_links_leave_admin_panels_out(self):
        self.h.on_message(tb.msg(tb.OWNER, "/links"))
        out = json.dumps(self.b.sent(tb.OWNER)[-1], ensure_ascii=False)
        self.assertIn("Downloader", out)
        self.assertNotIn("3x-ui", out)
        self.assertNotIn("10358", out)


class TestAdminPage(tw.Base):
    def setUp(self):
        super().setUp()
        services_file(self)

    def path(self, uid, ttl=3600):
        return "/" + links.sign("admin", "panels", uid, ttl=ttl, store=self.store)[2:]

    def test_owner_sees_the_panels(self):
        st, r, body = self.req("GET", self.path(tw.OWNER))
        page = body.decode()
        self.assertEqual(st, 200)
        self.assertIn("text/html", r.getheader("Content-Type"))
        self.assertIn("https://example.org:10358/p/", page)
        self.assertIn("Bots", page)
        self.assertNotIn("https://example.org/v/", page)       # public cards stay on the hub
        self.assertNotIn("<script>", page)                     # a bad colour never reaches the CSS
        csp = r.getheader("Content-Security-Policy")
        self.assertTrue(csp.startswith("default-src 'none'") and "script-src" not in csp, csp)
        self.assertEqual(r.getheader("Cache-Control"), "no-store")

    def test_others_and_bad_links(self):
        self.assertEqual(self.req("GET", self.path(200))[0], 403)          # signed, but not an admin
        self.assertEqual(self.req("GET", self.path(tw.OWNER)[:-3] + "xyz")[0], 403)
        self.assertEqual(self.req("GET", self.path(tw.OWNER, ttl=-1))[0], 403)
        self.h.admins = {300}
        self.assertEqual(self.req("GET", self.path(300))[0], 200)          # helper-bot admins too
        self.h.admins = set()
