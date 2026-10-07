"""Round 3: owner on/off switches for member services, AmneziaWG in the members'
VPN screen (vpn:// host fix, "awg" group, signed .conf download), signed links."""
import base64
import json
import os
import tempfile
import unittest
import urllib.parse
from unittest import mock

import links
import members as mm
import test_bot as tb
import test_members as tm
import test_round2 as t2
import test_webapp as tw
import webapp
from store import Store

OWNER_BOT = tb.OWNER                     # owner id in the bot fixtures
OWNER_WEB = tw.OWNER                     # owner id in the web fixtures
DAY = 86400

CONF = """[Interface]
Address = 10.8.2.5/32
PrivateKey = YFc3+lJeP4kM2sXhq8gU0hCjQ1v2nBq5H1GtZ0o0Z3k=
DNS = 1.1.1.1, 8.8.8.8
Jc = 4
Jmin = 10
Jmax = 50
S1 = 74
S2 = 112
H1 = 1077326114
H2 = 1920285718

[Peer]
PublicKey = Mw3b9yS5L1sQ0dQp5Zn6bXn0P5q0yq0Zl0j0j2dC3Hw=
PresharedKey = 6l2Rr2sQ0fQ0x0yT3X2pT2o0n0D0u0Q0Z0L0d0W0v0A=
Endpoint = localhost:20454
AllowedIPs = 0.0.0.0/0, ::/0
PersistentKeepalive = 25
"""
PUBLIC_CONF = CONF.replace("Endpoint = localhost:20454", "Endpoint = example.org:20454")


def vpn_url(conf, padded=True, frag=""):
    enc = base64.urlsafe_b64encode(conf.encode()).decode()
    return "vpn://" + (enc if padded else enc.rstrip("=")) + frag


def decode(url):
    body = url[len("vpn://"):].partition("#")[0]
    return base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)).decode()


# ------------------------------------------------------------ links.py --
class TestSignedLinks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(os.path.join(self.tmp.name, "l.db"))

    def test_sign_verify_roundtrip(self):
        rel = links.sign("awg_conf", "awg", 42, store=self.store)
        self.assertTrue(rel.startswith("./dl/"))
        tok = rel[len("./dl/"):]
        self.assertRegex(tok, r"^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$")
        self.assertEqual(links.verify(tok, store=self.store), ("awg_conf", "awg", 42))

    def test_mac_is_16_bytes(self):
        tok = links.token("k", "i", 1, store=self.store)
        mac = tok.partition(".")[2]
        self.assertEqual(len(base64.urlsafe_b64decode(mac + "=" * (-len(mac) % 4))), 16)

    def test_expired(self):
        tok = links.token("k", "i", 1, ttl=600, store=self.store, now=1000)
        self.assertEqual(links.verify(tok, store=self.store, now=1599), ("k", "i", 1))
        with self.assertRaises(links.LinkError):
            links.verify(tok, store=self.store, now=1600)
        self.assertRaises(links.LinkError, links.verify, links.token("k", "i", 1, ttl=-1, store=self.store),
                          store=self.store)

    def test_tamper(self):
        tok = links.token("awg_conf", "awg", 42, store=self.store)
        body, _, mac = tok.partition(".")
        forged_payload = base64.urlsafe_b64encode(b"awg_conf|awg|43|99999999999").decode().rstrip("=")
        for bad in (forged_payload + "." + mac,                      # other uid, old mac
                    body + "." + mac[:-2] + ("AA" if not mac.endswith("AA") else "BB"),
                    body, "." + mac, body + ".", "", "!!!.???", body + "." + body):
            with self.assertRaises(links.LinkError, msg=bad):
                links.verify(bad, store=self.store)

    def test_secret_generated_once_and_stored_hex(self):
        self.assertEqual(self.store.get("link_secret"), "")
        links.token("k", "i", 1, store=self.store)
        hexed = self.store.get("link_secret")
        self.assertEqual(len(bytes.fromhex(hexed)), 32)
        links.token("k", "i", 2, store=self.store)
        self.assertEqual(self.store.get("link_secret"), hexed)

    def test_other_secret_rejects(self):
        tok = links.token("k", "i", 1, store=self.store)
        other = Store(os.path.join(self.tmp.name, "o.db"))
        with self.assertRaises(links.LinkError):
            links.verify(tok, store=other)

    def test_bad_fields_and_absolute(self):
        with self.assertRaises(ValueError):
            links.token("a|b", "i", 1, store=self.store)
        self.assertEqual(links.absolute("./dl/x", "https://h.org/app/"), "https://h.org/app/dl/x")
        self.assertEqual(links.absolute("./dl/x", ""), "./dl/x")


# ------------------------------------------------------------ members --
class TestServiceSwitches(tm.Base):
    def test_defaults(self):
        self.assertEqual([self.m.service_enabled(s) for s in ("vpn", "matrix", "tools", "youtube")],
                         [True, True, True, False])
        self.assertFalse(self.m.service_enabled("nope"))
        self.assertEqual(mm.GRANTABLE, ["vpn", "matrix", "tools"])
        self.assertEqual(mm.SERVICES["youtube"]["mode"], "all")
        self.assertEqual(mm.SERVICES["youtube"]["name_ru"], "YouTube и видео")

    def test_has_grant_vs_all_mode(self):
        self.m.grant(1, tm.FRM, 30, ["vpn"])
        self.m.grant(2, tm.FRM, 30, [])
        self.assertTrue(self.m.has(1, "vpn"))
        self.assertFalse(self.m.has(1, "matrix"))          # not granted
        self.assertFalse(self.m.has(1, "youtube"))         # all-mode but switched off
        self.m.set_service_enabled("youtube", True)
        self.assertTrue(self.m.has(1, "youtube"))
        self.assertTrue(self.m.has(2, "youtube"))          # no grant needed
        self.assertFalse(self.m.has(2, "vpn"))
        self.m.suspend(2)
        self.assertFalse(self.m.has(2, "youtube"))         # inactive members get nothing
        self.assertFalse(self.m.has(99, "youtube"))        # nor strangers
        self.m.set_service_enabled("vpn", False)
        self.assertFalse(self.m.has(1, "vpn"))             # master-off beats the grant
        self.assertEqual(self.m.get(1)["services"], ["vpn"])    # the grant itself is kept

    def test_set_logs_and_wakes(self):
        self.m.changed.clear()
        self.m.set_service_enabled("youtube", True)
        self.assertTrue(self.m.changed.is_set())
        ev = [e for e in self.m.events() if e["kind"] == "service_toggle"]
        self.assertEqual(ev[0]["detail"], "youtube on")
        self.m.set_service_enabled("youtube", False)
        self.assertEqual(self.m.events()[0]["detail"], "youtube off")
        self.assertTrue(self.m.service_enabled("vpn"))          # others untouched
        with self.assertRaises(KeyError):
            self.m.set_service_enabled("nope", True)

    def test_effective_and_view(self):
        self.m.grant(1, tm.FRM, 30, ["vpn", "tools"])
        self.m.set_service_enabled("youtube", True)
        self.m.set_service_enabled("tools", False)
        m = self.m.get(1)
        self.assertEqual(self.m.effective_services(m), ["vpn", "youtube"])
        v = self.m.view(m)
        self.assertEqual(v["effective_services"], ["vpn", "youtube"])
        self.assertEqual(v["services"], ["vpn", "tools"])        # granted, unchanged

    def test_services_state_counts(self):
        self.m.grant(1, tm.FRM, 30, ["vpn", "matrix"])
        self.m.grant(2, tm.FRM, 30, ["vpn"])
        self.m.grant(3, tm.FRM, 30, ["vpn"])
        self.m.suspend(3)
        st = {s["id"]: s for s in self.m.services_state()}
        self.assertEqual(list(st), ["vpn", "matrix", "tools", "youtube"])
        self.assertEqual(set(st["vpn"]), {"id", "name", "description", "mode", "enabled", "members_with_access"})
        self.assertEqual((st["vpn"]["members_with_access"], st["matrix"]["members_with_access"]), (2, 1))
        self.assertEqual((st["youtube"]["enabled"], st["youtube"]["members_with_access"]), (False, 0))
        self.m.set_service_enabled("youtube", True)
        st = {s["id"]: s for s in self.m.services_state()}
        self.assertEqual((st["youtube"]["enabled"], st["youtube"]["members_with_access"]), (True, 2))
        self.assertEqual(st["youtube"]["mode"], "all")
        self.assertEqual(st["youtube"]["name"], "YouTube и видео")

    def test_youtube_is_never_stored_as_a_grant(self):
        self.m.grant(1, tm.FRM, 30, ["vpn", "youtube"])
        self.assertEqual(self.m.get(1)["services"], ["vpn"])
        self.m.set_services(1, ["youtube", "matrix"])
        self.assertEqual(self.m.get(1)["services"], ["matrix"])
        code = self.m.new_code(30, ["youtube", "tools"])
        self.assertEqual(self.m.codes()[0]["services"], ["tools"])
        self.assertTrue(code)


class TestReconcilerSwitches(tm.Base):
    def setUp(self):
        super().setUp()
        self.xui = tm.FakeXUI(tm.INBOUNDS)
        self.syn = tm.FakeSyn()
        self.rec = mm.Reconciler(self.m, self.xui, self.syn, lambda *a, **k: None, lambda: 1000)

    def test_master_off_vpn_disables_every_client_and_back(self):
        self.m.grant(7, tm.FRM, 30, ["vpn"])
        self.m.grant(8, tm.FRM, 30, ["vpn"])
        self.rec.sync_all()
        self.assertTrue(self.xui.store["mh-7"]["enable"] and self.xui.store["mh-8"]["enable"])
        self.m.set_service_enabled("vpn", False)
        self.rec.sync_all()
        self.assertFalse(self.xui.store["mh-7"]["enable"])
        self.assertFalse(self.xui.store["mh-8"]["enable"])
        self.assertIn("mh-7", self.xui.store)                    # disabled, not deleted
        self.m.set_service_enabled("vpn", True)
        self.rec.sync_all()
        self.assertTrue(self.xui.store["mh-7"]["enable"])

    def test_master_off_vpn_creates_no_new_clients(self):
        self.m.set_service_enabled("vpn", False)
        self.m.grant(7, tm.FRM, 30, ["vpn"])
        self.rec.sync_all()
        self.assertNotIn("mh-7", self.xui.store)
        self.m.set_service_enabled("vpn", True)
        self.rec.sync_all()
        self.assertTrue(self.xui.store["mh-7"]["enable"])

    def test_master_off_matrix_locks_accounts(self):
        self.m.grant(7, tm.FRM, 30, ["matrix"])
        self.m.add_matrix(7, "@bob:x")
        self.rec.sync_all()
        self.assertEqual(self.syn.calls[-1], ("@bob:x", False))
        self.m.set_service_enabled("matrix", False)
        self.rec.sync_all()
        self.assertEqual(self.syn.calls[-1], ("@bob:x", True))
        self.m.set_service_enabled("matrix", True)
        self.rec.sync_all()
        self.assertEqual(self.syn.calls[-1], ("@bob:x", False))


# ------------------------------------------------------------ vpn:// --
class TestVpnRewrite(unittest.TestCase):
    D = "example.org"

    def test_endpoint_rewritten_rest_identical(self):
        out = webapp.fix_host(vpn_url(CONF), self.D)
        want = CONF.replace("Endpoint = localhost:20454", f"Endpoint = {self.D}:20454")
        self.assertEqual(decode(out), want)
        self.assertEqual(decode(out).replace(self.D, "localhost"), CONF)     # nothing else moved
        self.assertTrue(out.startswith("vpn://"))

    def test_encoding_matches_input(self):
        for extra in ("", "#", "##", "###"):                  # shifts the length mod 3
            conf = CONF + extra
            for padded in (True, False):
                url = vpn_url(conf, padded=padded)
                out = webapp.fix_host(url, self.D)
                body = out[len("vpn://"):]
                if padded:
                    self.assertEqual(len(body) % 4, 0, (extra, padded))      # still padded
                elif len(url) % 4 != len("vpn://") % 4:     # input not a multiple of 4: provably bare
                    self.assertNotIn("=", out)                              # still bare
                self.assertNotIn("+", out[len("vpn://"):])
                self.assertNotIn("/", out[len("vpn://"):])
                self.assertEqual(decode(out), conf.replace("localhost", self.D))
        # a padded input really exists among the cases above
        self.assertTrue(any(vpn_url(CONF + e).endswith("=") for e in ("", "#", "##")))

    def test_standard_alphabet_input_stays_standard(self):
        conf = CONF.replace("DNS = 1.1.1.1", "DNS = 1.1.1.1 ??>>")
        std = "vpn://" + base64.b64encode(conf.encode()).decode()
        self.assertTrue("+" in std or "/" in std)
        out = webapp.fix_host(std, self.D)
        self.assertEqual(base64.b64decode(out[len("vpn://"):]).decode(), conf.replace("localhost", self.D))

    def test_other_bad_hosts_and_port_kept(self):
        for host in ("127.0.0.1", "host.docker.internal", "0.0.0.0", "LOCALHOST"):
            out = webapp.fix_host(vpn_url(CONF.replace("localhost", host)), self.D)
            self.assertIn(f"Endpoint = {self.D}:20454\n", decode(out), host)

    def test_real_host_and_junk_untouched(self):
        real = vpn_url(CONF.replace("localhost", "vpn.example.com"))
        self.assertEqual(webapp.fix_host(real, self.D), real)
        self.assertEqual(webapp.fix_host("vpn://!!!not base64!!!", self.D), "vpn://!!!not base64!!!")
        self.assertEqual(webapp.fix_host(vpn_url("hello"), self.D), vpn_url("hello"))
        self.assertEqual(webapp.fix_host(vpn_url(CONF), ""), vpn_url(CONF))

    def test_fragment_kept(self):
        out = webapp.fix_host(vpn_url(CONF, frag="#AWG-mh-1"), self.D)
        self.assertTrue(out.endswith("#AWG-mh-1"))
        self.assertEqual(decode(out), CONF.replace("localhost", self.D))

    def test_classifier_and_name(self):
        self.assertEqual(webapp.link_group(vpn_url(CONF)), "awg")
        self.assertEqual(webapp.link_name(vpn_url(CONF), "mh-1", 3), "AmneziaWG")
        self.assertEqual(webapp.link_name(vpn_url(CONF, frag="#AWG-A-mh-1"), "mh-1", 3), "AWG-A")


class TestAwgGroup(unittest.TestCase):
    def test_position_and_links(self):
        url = vpn_url(CONF)
        dl = "https://example.org/app/dl/TOKEN"
        _, groups = webapp.build_links([t2.TG, t2.HY2, url, t2.PQ, t2.SS], "mh-1", "example.org", dl)
        self.assertEqual([g["id"] for g in groups], ["main", "new", "udp", "awg", "tg"])
        g = groups[3]
        self.assertEqual(g["title"], "AmneziaWG")
        self.assertEqual(g["apps"], ["AmneziaWG", "AmneziaVPN"])
        self.assertEqual(g["hint"], "AmneziaWG: скачай .conf → «+» → «Импорт из файла» (или сканируй QR). "
                                    "AmneziaVPN: скопируй ссылку → «+» → «Вставить».")
        copy, down, qr = g["links"]
        self.assertEqual(copy["action"], "copy")
        self.assertEqual(copy["name"], "AmneziaWG")
        self.assertEqual(decode(copy["url"]), PUBLIC_CONF)
        self.assertEqual(down, {"name": "Файл .conf", "url": dl, "action": "download",
                                "file_name": "meowhub-awg.conf"})
        self.assertEqual(qr, {"name": "QR для AmneziaWG", "action": "qr", "text": PUBLIC_CONF})
        # other groups keep their actions
        self.assertEqual({l["action"] for l in groups[0]["links"]}, {"copy"})
        self.assertEqual(groups[4]["links"][0]["action"], "telegram")

    def test_without_conf_url_no_download(self):
        _, groups = webapp.build_links([vpn_url(CONF)], "mh-1", "example.org")
        self.assertEqual([l["action"] for l in groups[0]["links"]], ["copy", "qr"])


# --------------------------------------------------------------- web --
class Web3(t2.Round2Base):
    def awg_member(self, uid=200, services=None):
        self.make_member(uid, services)
        self.h.members.set_vpn_sub_id(uid, "abc123")
        self.h.xui.client_links = lambda email: [t2.REALITY.replace("mh-1", email), vpn_url(CONF)]

    def dl_path(self, url):
        """The absolute URL the app hands out -> the path the app server sees (Caddy strips /app)."""
        self.assertTrue(url.startswith("https://example.org/app/dl/"), url)
        return url[len("https://example.org/app"):]


class TestApiServices(Web3):
    def test_get_and_toggle(self):
        st, d = self.j("GET", "/api/admin/services", headers=self.admin())
        self.assertEqual(st, 200)
        self.assertEqual([s["id"] for s in d], ["vpn", "matrix", "tools", "youtube"])
        self.assertEqual(d[3], {"id": "youtube", "name": "YouTube и видео", "description": "Скачивание видео и музыки по ссылке",
                                "mode": "all", "enabled": False, "members_with_access": 0})
        self.make_member(200)
        self.make_member(201)
        st, d = self.j("POST", "/api/admin/services/youtube", {"enabled": True}, self.admin())
        self.assertEqual(st, 200)
        self.assertEqual((d[3]["enabled"], d[3]["members_with_access"]), (True, 2))
        self.assertTrue(self.h.members.service_enabled("youtube"))
        st, d = self.j("POST", "/api/admin/services/vpn", {"enabled": False}, self.admin())
        self.assertEqual((d[0]["enabled"], d[0]["members_with_access"]), (False, 0))
        self.assertEqual(self.h.members.events()[0]["detail"], "vpn off")

    def test_owner_only_and_validation(self):
        self.make_member(200)
        for hdr in (self.tgh(200), {}):
            st, _ = self.j("GET", "/api/admin/services", headers=hdr)
            self.assertIn(st, (401, 403))
            st, _ = self.j("POST", "/api/admin/services/youtube", {"enabled": True}, hdr)
            self.assertIn(st, (401, 403))
        self.assertFalse(self.h.members.service_enabled("youtube"))
        st, d = self.j("POST", "/api/admin/services/youtube", {"enabled": "yes"}, self.admin())
        self.assertEqual((st, d["error"]), (400, "bad_request"))
        st, d = self.j("POST", "/api/admin/services/nope", {"enabled": True}, self.admin())
        self.assertEqual(st, 404)

    def test_not_grantable(self):
        self.make_member(200)
        st, d = self.j("POST", "/api/admin/codes", {"services": ["vpn", "youtube"]}, self.admin())
        self.assertEqual((st, d["error"]), (400, "not_grantable"))
        st, d = self.j("POST", "/api/admin/grant", {"uid": 300, "services": ["youtube"]}, self.admin())
        self.assertEqual((st, d["error"]), (400, "not_grantable"))
        st, d = self.j("POST", "/api/admin/members/200", {"action": "services", "services": ["youtube"]}, self.admin())
        self.assertEqual((st, d["error"]), (400, "not_grantable"))
        self.assertIsNone(self.h.members.get(300))
        self.assertEqual(self.h.members.get(200)["services"], ["vpn", "matrix"])
        # unknown ids stay a plain bad_request
        st, d = self.j("POST", "/api/admin/codes", {"services": ["bogus"]}, self.admin())
        self.assertEqual((st, d["error"]), (400, "bad_request"))
        # grantable ones still work
        st, d = self.j("POST", "/api/admin/codes", {"services": ["vpn", "tools"]}, self.admin())
        self.assertEqual(st, 200)

    def test_member_view_has_effective_services(self):
        self.make_member(200, ["vpn"])
        self.h.members.set_service_enabled("youtube", True)
        st, d = self.j("GET", "/api/admin/members/200", headers=self.admin())
        self.assertEqual((d["services"], d["effective_services"]), (["vpn"], ["vpn", "youtube"]))


class TestMeCatalog(Web3):
    def ids(self, d):
        return [s["id"] for s in d["services"]]

    def test_only_enabled_services_with_availability(self):
        self.make_member(200, ["vpn"])
        st, d = self.j("GET", "/api/me", headers=self.tgh(200))
        self.assertEqual(self.ids(d), ["vpn", "matrix", "tools"])         # youtube is off
        self.assertEqual({s["id"]: s["available"] for s in d["services"]},
                         {"vpn": True, "matrix": False, "tools": False})
        self.assertEqual(set(d["services"][0]), {"id", "name", "description", "available"})
        self.h.members.set_service_enabled("youtube", True)
        self.h.members.set_service_enabled("matrix", False)
        st, d = self.j("GET", "/api/me", headers=self.tgh(200))
        self.assertEqual(self.ids(d), ["vpn", "tools", "youtube"])        # matrix disappeared
        self.assertTrue({s["id"]: s["available"] for s in d["services"]}["youtube"])

    def test_stranger_and_expired(self):
        st, d = self.j("GET", "/api/me", headers=self.tgh(555))
        self.assertEqual(self.ids(d), ["vpn", "matrix", "tools"])
        self.assertFalse(any(s["available"] for s in d["services"]))
        self.make_member(200)
        self.h.members.set_service_enabled("youtube", True)
        self.h.members.suspend(200)
        st, d = self.j("GET", "/api/me", headers=self.tgh(200))
        self.assertFalse(any(s["available"] for s in d["services"]))

    def test_master_off_vpn_blocks_the_api(self):
        self.awg_member()
        self.assertEqual(self.j("GET", "/api/vpn", headers=self.tgh(200))[0], 200)
        self.h.members.set_service_enabled("vpn", False)
        st, d = self.j("GET", "/api/vpn", headers=self.tgh(200))
        self.assertEqual((st, d["error"]), (403, "no_access"))


class TestAwgDownload(Web3):
    def vpn(self):
        st, d = self.j("GET", "/api/vpn", headers=self.tgh(200))
        self.assertEqual(st, 200, d)
        return d

    def test_group_in_api_vpn_and_download(self):
        self.awg_member()
        d = self.vpn()
        self.assertEqual([g["id"] for g in d["groups"]], ["main", "awg"])
        copy, down, qr = d["groups"][1]["links"]
        self.assertEqual((copy["action"], down["action"], qr["action"]), ("copy", "download", "qr"))
        self.assertEqual(decode(copy["url"]), PUBLIC_CONF)                 # BASE_DOMAIN = example.org
        self.assertEqual(qr["text"], PUBLIC_CONF)
        self.assertEqual(down["file_name"], "meowhub-awg.conf")
        # flat list still has every link, host fixed
        self.assertEqual(len(d["links"]), 2)
        self.assertEqual(decode(d["links"][1]["url"]), PUBLIC_CONF)
        # the signed link works with no auth header at all
        st, r, body = self.req("GET", self.dl_path(down["url"]))
        self.assertEqual(st, 200)
        self.assertEqual(r.getheader("Content-Type"), "text/plain; charset=utf-8")
        self.assertEqual(r.getheader("Content-Disposition"), 'attachment; filename="meowhub-awg.conf"')
        self.assertEqual(body.decode(), PUBLIC_CONF)

    def test_conf_is_built_fresh_each_time(self):
        self.awg_member()
        path = self.dl_path(self.vpn()["groups"][1]["links"][1]["url"])
        self.assertEqual(self.req("GET", path)[2].decode(), PUBLIC_CONF)
        newer = CONF.replace("PrivateKey = YFc3", "PrivateKey = ROTATED")
        self.h.xui.client_links = lambda email: [vpn_url(newer)]
        self.assertIn("PrivateKey = ROTATED", self.req("GET", path)[2].decode())

    def test_403_after_vpn_revoked_or_switched_off(self):
        self.awg_member()
        path = self.dl_path(self.vpn()["groups"][1]["links"][1]["url"])
        self.assertEqual(self.req("GET", path)[0], 200)
        self.h.members.set_services(200, ["matrix"])
        st, r, body = self.req("GET", path)
        self.assertEqual(st, 403)
        self.assertNotIn(b"PrivateKey", body)
        self.h.members.set_services(200, ["vpn"])
        self.assertEqual(self.req("GET", path)[0], 200)
        self.h.members.set_service_enabled("vpn", False)
        self.assertEqual(self.req("GET", path)[0], 403)
        self.h.members.set_service_enabled("vpn", True)
        self.h.members.suspend(200)
        self.assertEqual(self.req("GET", path)[0], 403)

    def test_bad_tokens(self):
        self.awg_member()
        good = self.dl_path(self.vpn()["groups"][1]["links"][1]["url"])
        for bad in (good[:-3] + ("AAA" if not good.endswith("AAA") else "BBB"), "/dl/abc", "/dl/a.b",
                    "/dl/" + links.token("awg_conf", "awg", 200, ttl=-5, store=self.store)):
            st, r, body = self.req("GET", bad)
            self.assertEqual(st, 403, bad)
            self.assertEqual(json.loads(body)["error"], "bad_link")
        self.assertEqual(self.req("POST", good, body={})[0], 405)

    def test_unknown_kind_and_no_awg_link(self):
        self.awg_member()
        tok = links.token("bogus", "x", 200, store=self.store)
        self.assertEqual(self.req("GET", "/dl/" + tok)[0], 404)
        self.h.xui.client_links = lambda email: [t2.REALITY]
        tok = links.token("awg_conf", "awg", 200, store=self.store)
        self.assertEqual(self.req("GET", "/dl/" + tok)[0], 404)

    def test_token_for_another_uid_is_still_checked_for_that_uid(self):
        self.awg_member()                                  # 200 has vpn, 201 does not
        self.make_member(201, ["matrix"])
        tok = links.token("awg_conf", "awg", 201, store=self.store)
        self.assertEqual(self.req("GET", "/dl/" + tok)[0], 403)


# --------------------------------------------------------------- bot --
class Bot3(unittest.TestCase):
    def setUp(self):
        p = mock.patch.dict(os.environ, tb.ENV)
        p.start()
        self.addCleanup(p.stop)
        d = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        self.h = tb.botmod.Helper(Store(os.path.join(d, "t.db")))
        self.h.bot = self.b = tb.FakeBot()
        self.h.bot_username = "TestBot"
        self.mm = self.h.members

    def make_member(self, uid=50, services=None):
        self.mm.grant(uid, tb.frm(uid), 30, services)
        return uid

    @staticmethod
    def buttons(entry):
        return [b for r in entry[3]["reply_markup"]["inline_keyboard"] for b in r]


class TestServicesBot(Bot3):
    def test_owner_screen(self):
        self.h.on_message(tb.msg(OWNER_BOT, "/services"))
        out = self.b.sent(OWNER_BOT)[-1]
        btns = {b["callback_data"]: b["text"] for b in self.buttons(out)}
        self.assertEqual(btns["svc:t:youtube"], "⬜ YouTube и видео — для всех участников")
        self.assertTrue(btns["svc:t:vpn"].startswith("✅ VPN"))
        self.assertEqual(list(btns), ["svc:t:vpn", "svc:t:matrix", "svc:t:tools", "svc:t:youtube"])
        self.assertIn("Вкл = доступно всем активным участникам", out[2])
        self.assertIn("Главный выключатель: выключишь — пропадёт у всех", out[2])

    def test_button_and_keyboard(self):
        self.assertEqual(tb.botmod.B_SVC, "⚙️ Сервисы")
        kb = self.h.keyboard(OWNER_BOT)["keyboard"]
        self.assertTrue(any(b["text"] == tb.botmod.B_SVC for r in kb for b in r))
        self.h.on_message(tb.msg(OWNER_BOT, tb.botmod.B_SVC))
        self.assertIn("Сервисы", self.b.sent(OWNER_BOT)[-1][2])

    def test_owner_toggle_flips_and_edits_in_place(self):
        self.assertFalse(self.mm.service_enabled("youtube"))
        self.h.on_callback(tb.cb(OWNER_BOT, "svc:t:youtube", mid=321))
        self.assertTrue(self.mm.service_enabled("youtube"))
        edit = [e for e in self.b.log if e[0] == "edit"][-1]
        self.assertEqual(edit[3]["message_id"], 321)
        self.assertIn("✅ YouTube и видео — для всех участников",
                      [b["text"] for r in edit[3]["reply_markup"]["inline_keyboard"] for b in r])
        self.assertIn("включено", self.b.answers()[-1][2])
        self.h.on_callback(tb.cb(OWNER_BOT, "svc:t:youtube", mid=321))
        self.assertFalse(self.mm.service_enabled("youtube"))
        self.h.on_callback(tb.cb(OWNER_BOT, "svc:t:vpn", mid=321))
        self.assertFalse(self.mm.service_enabled("vpn"))
        self.assertEqual(self.mm.events()[0]["detail"], "vpn off")

    def test_non_owners_refused(self):
        uid = self.make_member(50)
        self.h.on_callback(tb.cb(uid, "svc:t:youtube"))
        self.assertFalse(self.mm.service_enabled("youtube"))
        ans = self.b.answers()[-1]
        self.assertEqual((ans[2], ans[3]["alert"]), ("Только для владельца", True))
        self.h.on_callback(tb.cb(77, "svc:t:youtube"))                  # a stranger
        self.assertFalse(self.mm.service_enabled("youtube"))
        self.assertEqual(self.b.answers()[-1][2], "Нет доступа")
        self.h.on_message(tb.msg(uid, "/services"))
        self.h.on_message(tb.msg(uid, tb.botmod.B_SVC))
        self.assertEqual(sum("только владельцу" in e[2] for e in self.b.sent(uid)), 2)
        self.assertFalse(any(e[0] == "edit" for e in self.b.log))

    def test_unknown_service_ignored(self):
        self.h.on_callback(tb.cb(OWNER_BOT, "svc:t:nope"))
        self.h.on_callback(tb.cb(OWNER_BOT, "svc:x:vpn"))
        self.assertTrue(self.mm.service_enabled("vpn"))

    def test_owner_command_list(self):
        self.h._commands()
        calls = [e for e in self.b.log if e[0] == "setMyCommands"]
        owner_cmds = [c["command"] for c in calls[-1][3]["commands"]]
        self.assertIn("services", owner_cmds)
        self.assertNotIn("services", [c["command"] for c in calls[0][3]["commands"]])

    def test_help_mentions_it_for_owner_only(self):
        self.assertIn("/services", self.h.help_text("owner"))
        self.assertNotIn("/services", self.h.help_text("member"))

    def test_code_command_rejects_youtube(self):
        self.h.on_message(tb.msg(OWNER_BOT, "/code 30 youtube"))
        self.assertIn("Формат", self.b.sent(OWNER_BOT)[-1][2])
        self.assertEqual(self.mm.codes(), [])
        self.h.on_message(tb.msg(OWNER_BOT, tb.botmod.B_CODE))
        data = [b["callback_data"] for b in self.buttons(self.b.sent(OWNER_BOT)[-1])]
        self.assertNotIn("c:t:youtube", data)
        self.assertEqual([d for d in data if d.startswith("c:t:")], ["c:t:vpn", "c:t:matrix", "c:t:tools"])
        self.h.on_callback(tb.cb(OWNER_BOT, "c:t:youtube", mid=self.b.n))
        self.h.on_callback(tb.cb(OWNER_BOT, "c:mk", mid=self.b.n))
        self.assertTrue(all("youtube" not in c["services"] for c in self.mm.codes()))

    def test_member_card_has_no_youtube_toggle(self):
        uid = self.make_member(50)
        _, kb = self.h.member_card(uid)
        data = [b.get("callback_data", "") for r in kb["inline_keyboard"] for b in r]
        self.assertEqual([d for d in data if d.startswith("m:svc:")], [f"m:svc:{uid}:{s}" for s in mm.GRANTABLE])
        self.h.on_callback(tb.cb(OWNER_BOT, f"m:svc:{uid}:youtube"))
        self.assertEqual(self.mm.get(uid)["services"], ["vpn", "matrix"])


class TestMemberChatUsesHas(Bot3):
    @staticmethod
    def labels(kb):
        return [b["text"] for r in kb["keyboard"] for b in r]

    def test_master_off_removes_buttons(self):
        uid = self.make_member(50, ["vpn", "matrix", "tools"])
        lab = self.labels(self.h.keyboard(uid))
        for b in (tb.botmod.B_VPN, tb.botmod.B_MX, tb.botmod.B_HEALTH):
            self.assertIn(b, lab)
        self.mm.set_service_enabled("vpn", False)
        self.mm.set_service_enabled("tools", False)
        lab = self.labels(self.h.keyboard(uid))
        self.assertNotIn(tb.botmod.B_VPN, lab)
        self.assertNotIn(tb.botmod.B_HEALTH, lab)
        self.assertIn(tb.botmod.B_MX, lab)

    def test_youtube_adds_the_download_button(self):
        uid = self.make_member(50)
        self.assertNotIn(tb.botmod.B_YT, self.labels(self.h.keyboard(uid)))
        self.mm.set_service_enabled("youtube", True)
        self.assertIn(tb.botmod.B_YT, self.labels(self.h.keyboard(uid)))

    def test_help_lists_effective_services(self):
        uid = self.make_member(50, ["vpn"])
        self.assertNotIn("YouTube", self.h.member_help(self.mm.get(uid)))
        self.mm.set_service_enabled("youtube", True)
        self.assertIn("YouTube и видео", self.h.member_help(self.mm.get(uid)))
        self.mm.set_service_enabled("vpn", False)
        self.assertNotIn("Личный VPN", self.h.member_help(self.mm.get(uid)))

    def test_commands_follow_has(self):
        uid = self.make_member(50, ["vpn"])
        self.mm.set_service_enabled("vpn", False)
        self.h.on_message(tb.msg(uid, "/vpn"))
        self.assertIn("не входит в твою подписку", self.b.sent(uid)[-1][2])
        self.mm.set_service_enabled("tools", True)
        with mock.patch.object(self.h, "cmd_health") as health:
            self.h.on_message(tb.msg(uid, "/health"))
            health.assert_not_called()                 # tools was never granted


if __name__ == "__main__":
    unittest.main()
