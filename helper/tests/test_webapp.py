"""webapp.py: real Store + Members on a temp file, fake xui/syn/bot/helper,
real HTTP server on an ephemeral port."""
import hashlib
import hmac
import http.client
import http.server
import json
import os
import re
import tempfile
import threading
import time
import unittest
import urllib.parse
from unittest import mock

import members as mem
import tg
import webapp
from store import Store
from synapse import SynapseError
from tg import TelegramError

TOKEN = "123456:FAKE-TOKEN"
OWNER = 1000
ADMIN_KEY = "k" * 32


def init_data(token, user, auth_date=None, tamper=False):
    fields = {"auth_date": str(int(time.time()) if auth_date is None else auth_date),
              "query_id": "AAHdF6IQAAAAAN0XohDhrOrc", "signature": "sigsigsig",
              "user": json.dumps(user, separators=(",", ":"))}
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if tamper:
        fields["query_id"] = "tampered"
    return urllib.parse.urlencode(fields)


class FakeBot:
    token = TOKEN

    def __init__(self):
        self.calls = []
        self.photos = True
        self.fail = False

    def call(self, method, _timeout=30, **params):
        self.calls.append(method)
        if self.fail:
            raise TelegramError("boom")
        if method == "getUserProfilePhotos":
            if not self.photos:
                return {"total_count": 0, "photos": []}
            return {"total_count": 1, "photos": [[
                {"file_id": "small", "width": 160, "height": 160},
                {"file_id": "mid", "width": 320, "height": 320},
                {"file_id": "big", "width": 640, "height": 640}]]}
        if method == "getFile":
            return {"file_path": f"photos/{params['file_id']}.jpg"}
        if method == "getMe":
            return {"first_name": "Helper", "username": "HelperBot"}
        raise AssertionError(method)

    def file_bytes(self, file_path):
        self.calls.append("file:" + file_path)
        return b"\xff\xd8JPEG:" + file_path.encode()


class FakeSyn:
    server_name = "example.org"

    def __init__(self):
        self.created = []
        self.resets = []

    def valid_localpart(self, s):
        return isinstance(s, str) and bool(re.fullmatch(r"[a-z0-9._=-]{3,24}", s)) and s != "admin"

    def create(self, localpart, password, displayname=""):
        if localpart == "taken":
            raise SynapseError("taken", code="M_USER_IN_USE")
        self.created.append((localpart, password, displayname))
        return f"@{localpart}:{self.server_name}"

    def user(self, mxid):
        return {"locked": False}

    def reset_password(self, mxid, password):
        self.resets.append((mxid, password))


class FakeXui:
    def __init__(self):
        self.client = None
        self.fail = False
        self.inb = [{"id": 1, "remark": "reality", "protocol": "vless", "port": 443, "enable": True},
                    {"id": 2, "remark": "wg", "protocol": "wireguard", "port": 51820, "enable": True},
                    {"id": 3, "remark": "awg", "protocol": "amneziawg", "port": 20443, "enable": True}]

    def inbounds(self):
        return self.inb

    def clients(self):
        if self.fail:
            raise RuntimeError("down")
        return [{"email": "mh-200", "traffic": {"up": 5, "down": 7}}]

    def onlines(self):
        return ["mh-200"]

    def client_get(self, email):
        return self.client

    def client_links(self, email):
        return ["vless://u@h:443?x=1#My%20Config", "vless://u@h:444?x=1"]


class FakeRec:
    def __init__(self, xui):
        self.xui = xui
        self.synced = []
        self.last_sync = {"ts": 1, "ok": True, "error": "", "vpn_clients": 0}

    def sync_member(self, uid):
        self.synced.append(uid)
        self.xui.client = {"email": f"mh-{uid}", "subId": "abc123", "traffic": {"up": 1, "down": 2}}

    def sync_all(self):
        self.last_sync = {"ts": 2, "ok": True, "error": "", "vpn_clients": 1}


class FakeHelper:
    bot_username = "MeowBot"
    webapp_url = "https://example.org/app/"

    def __init__(self, store):
        self.store = store
        self.members = mem.Members(store)
        self.xui = FakeXui()
        self.syn = FakeSyn()
        self.rec = FakeRec(self.xui)
        self.bot = FakeBot()
        self.sent = []

    def owner_id(self):
        return OWNER

    def notify(self, chat_id, text, reply_markup=None):
        self.sent.append((chat_id, text))

    def notify_owner(self, text, reply_markup=None):
        self.sent.append((OWNER, text))


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        t = self.tmp.name
        os.makedirs(os.path.join(t, "www", "assets"))
        with open(os.path.join(t, "www", "index.html"), "w") as f:
            f.write("<html>SPA</html>")
        with open(os.path.join(t, "www", "assets", "a.js"), "w") as f:
            f.write("console.log(1)")
        with open(os.path.join(t, "secret.txt"), "w") as f:
            f.write("SECRET")
        patcher = mock.patch.dict(os.environ, {
            "BOT_ADMIN_KEY": ADMIN_KEY, "WEBAPP_DIR": os.path.join(t, "www"),
            "HELPER_AVATAR_DIR": os.path.join(t, "avatars"), "BASE_DOMAIN": "example.org",
            "VPN_SUB_BASE": "https://example.org:2096/sub/", "OWNER_CONTACT": "@owner",
            "MATRIX_MAX_PER_MEMBER": "2", "CRYPTO_TG_TOKEN": ""})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.store = Store(os.path.join(t, "h.db"))
        self.h = FakeHelper(self.store)
        self.srv = webapp.start(self.h, port=0, host="127.0.0.1")
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)
        self.port = self.srv.server_address[1]

    # -- helpers --
    def req(self, method, path, body=None, headers=None, raw=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        hdrs = dict(headers or {})
        data = raw
        if body is not None:
            data = json.dumps(body)
        if method == "POST":
            hdrs.setdefault("Content-Type", "application/json")
        c.request(method, path, body=data, headers=hdrs)
        r = c.getresponse()
        payload = r.read()
        c.close()
        return r.status, r, payload

    def j(self, method, path, body=None, headers=None):
        st, r, payload = self.req(method, path, body, headers)
        try:
            return st, json.loads(payload)
        except ValueError:
            return st, payload

    @staticmethod
    def admin():
        return {"X-Admin-Key": ADMIN_KEY}

    @staticmethod
    def tgh(uid, first="Bob", username="bob", token=TOKEN, **kw):
        return {"X-Tg-Init-Data": init_data(token, {"id": uid, "first_name": first, "username": username,
                                                    "language_code": "ru"}, **kw)}

    def make_member(self, uid=200, services=None, days=30):
        return self.h.members.grant(uid, {"first_name": "Bob", "username": "bob"}, days, services)


class TestAuth(Base):
    def test_no_auth_401(self):
        st, d = self.j("GET", "/api/me")
        self.assertEqual(st, 401)
        self.assertEqual(d["error"], "unauthorized")

    def test_valid_init_data_stranger(self):
        st, d = self.j("GET", "/api/me", headers=self.tgh(555))
        self.assertEqual(st, 200)
        self.assertEqual((d["role"], d["mode"], d["member"]), ("stranger", "tg", None))
        self.assertEqual(d["contact"], "@owner")
        self.assertEqual(d["bot_username"], "MeowBot")
        self.assertEqual([s["id"] for s in d["services"]], ["vpn", "matrix", "tools"])
        self.assertEqual(d["services"][0]["description"][:6], "Личный")

    def test_forged_hash(self):
        st, d = self.j("GET", "/api/me", headers=self.tgh(555, tamper=True))
        self.assertEqual((st, d["error"]), (401, "bad_init_data"))

    def test_wrong_token(self):
        st, d = self.j("GET", "/api/me", headers=self.tgh(555, token="999:OTHER"))
        self.assertEqual((st, d["error"]), (401, "bad_init_data"))

    def test_expired(self):
        st, d = self.j("GET", "/api/me", headers=self.tgh(555, auth_date=int(time.time()) - 90000))
        self.assertEqual((st, d["error"]), (401, "init_data_expired"))

    def test_max_age_env(self):
        with mock.patch.dict(os.environ, {"WEBAPP_INITDATA_MAX_AGE": "60"}):
            st, _ = self.j("GET", "/api/me", headers=self.tgh(555, auth_date=int(time.time()) - 120))
        self.assertEqual(st, 401)

    def test_bot_not_ready_503(self):
        self.h.bot = None
        st, d = self.j("GET", "/api/me", headers=self.tgh(555))
        self.assertEqual((st, d["error"]), (503, "bot_not_ready"))

    def test_admin_key_good(self):
        st, d = self.j("GET", "/api/me", headers=self.admin())
        self.assertEqual((st, d["role"], d["mode"]), (200, "owner", "browser"))

    def test_admin_key_bad(self):
        st, _ = self.j("GET", "/api/me", headers={"X-Admin-Key": "nope"})
        self.assertEqual(st, 401)

    def test_admin_key_empty_env_never_matches(self):
        with mock.patch.dict(os.environ, {"BOT_ADMIN_KEY": ""}):
            for hdr in ("", "x"):
                st, _ = self.j("GET", "/api/me", headers={"X-Admin-Key": hdr})
                self.assertEqual(st, 401)

    def test_owner_via_telegram_with_membership(self):
        self.make_member(OWNER)
        st, d = self.j("GET", "/api/me", headers=self.tgh(OWNER, "Boss"))
        self.assertEqual((st, d["role"], d["mode"]), (200, "owner", "tg"))
        self.assertEqual(d["member"]["id"], OWNER)

    def test_member_touch_once(self):
        self.make_member(200)
        for _ in range(3):
            self.j("GET", "/api/me", headers=self.tgh(200))
        rows = self.store.q("SELECT app FROM activity WHERE uid=200")
        self.assertEqual(rows[0]["app"], 1)

    def test_member_cannot_use_admin(self):
        self.make_member(200)
        st, d = self.j("GET", "/api/admin/members", headers=self.tgh(200))
        self.assertEqual((st, d["error"]), (403, "forbidden"))


class TestRedeem(Base):
    def test_stranger_redeems(self):
        code = self.h.members.new_code(30, ["vpn", "matrix"])
        st, d = self.j("POST", "/api/redeem", {"code": code.lower()}, self.tgh(555, "Eve <b>", "eve"))
        self.assertEqual(st, 200)
        self.assertEqual(d["result"], "new")
        self.assertEqual(d["member"]["services"], ["vpn", "matrix"])
        self.assertIsNotNone(self.h.members.get(555))
        chat, text = self.h.sent[-1]
        self.assertEqual(chat, OWNER)
        self.assertIn(code, text)
        self.assertIn("Eve &lt;b&gt;", text)
        self.assertIn("+30 д", text)
        self.assertIn("VPN + Мессенджер", text)
        st, me = self.j("GET", "/api/me", headers=self.tgh(555))
        self.assertEqual(me["role"], "member")

    def test_invalid_code(self):
        st, d = self.j("POST", "/api/redeem", {"code": "MEOW-XXXX-XXXX"}, self.tgh(555))
        self.assertEqual((st, d["result"], d["member"]), (200, "invalid", None))
        self.assertEqual(self.h.sent, [])

    def test_extend_notifies_owner(self):
        self.make_member(200)
        code = self.h.members.new_code(30)
        _, d = self.j("POST", "/api/redeem", {"code": code}, self.tgh(200))
        self.assertEqual(d["result"], "extended")
        self.assertTrue(self.h.sent and self.h.sent[-1][0] == OWNER)

    def test_browser_mode_rejected(self):
        st, d = self.j("POST", "/api/redeem", {"code": "x"}, self.admin())
        self.assertEqual((st, d["error"]), (403, "tg_only"))

    def test_bad_body(self):
        st, d = self.j("POST", "/api/redeem", {"code": 5}, self.tgh(555))
        self.assertEqual((st, d["error"]), (400, "bad_request"))

    def test_non_json_and_content_type(self):
        h = dict(self.tgh(555))
        st, r, p = self.req("POST", "/api/redeem", raw="not json", headers=h)
        self.assertEqual(st, 400)
        self.assertEqual(json.loads(p)["error"], "bad_json")
        h["Content-Type"] = "text/plain"
        st, r, p = self.req("POST", "/api/redeem", raw='{"code":"x"}', headers=h)
        self.assertEqual(st, 400)
        self.assertEqual(json.loads(p)["error"], "bad_content_type")

    def test_large_body_413(self):
        st, r, p = self.req("POST", "/api/redeem", raw=json.dumps({"code": "A" * 70000}),
                            headers=self.tgh(555))
        self.assertEqual(st, 413)
        self.assertEqual(json.loads(p)["error"], "too_large")


class TestVpn(Base):
    def test_no_service_403(self):
        self.make_member(200, ["matrix"])
        st, d = self.j("GET", "/api/vpn", headers=self.tgh(200))
        self.assertEqual((st, d["error"]), (403, "no_access"))

    def test_stranger_403(self):
        st, d = self.j("GET", "/api/vpn", headers=self.tgh(555))
        self.assertEqual((st, d["error"]), (403, "no_access"))

    def test_vpn_pending_then_sync(self):
        self.make_member(200)
        self.h.members.set_vpn_sub_id(200, "abc123")
        st, d = self.j("GET", "/api/vpn", headers=self.tgh(200))
        self.assertEqual(st, 200, d)
        self.assertEqual(self.h.rec.synced, [200])
        self.assertEqual(d["sub_url"], "https://example.org:2096/sub/abc123")
        self.assertEqual([l["name"] for l in d["links"]], ["My Config", "Конфиг 2"])
        self.assertEqual(d["traffic"], {"up": 1, "down": 2})
        self.assertTrue(d["online"])
        self.assertEqual([a["id"] for a in d["apps"]], ["happ", "v2raytun", "hiddify", "streisand", "v2rayng"])
        self.assertTrue(d["apps"][0]["go_url"].startswith("./go/happ?u=https%3A%2F%2Fexample.org"))

    def test_vpn_online(self):
        self.make_member(200)
        self.h.xui.client = {"email": "mh-200", "subId": "s1", "traffic": {"up": 1, "down": 2}}
        self.h.members.set_vpn_sub_id(200, "s1")
        _, d = self.j("GET", "/api/vpn", headers=self.tgh(200))
        self.assertTrue(d["online"])
        self.assertEqual(self.h.rec.synced, [])

    def test_vpn_409_when_still_missing(self):
        self.make_member(200)
        self.h.rec.sync_member = lambda uid: None
        st, d = self.j("GET", "/api/vpn", headers=self.tgh(200))
        self.assertEqual((st, d["error"]), (409, "pending"))

    def test_vpn_unconfigured(self):
        self.make_member(200)
        self.h.xui = None
        st, d = self.j("GET", "/api/vpn", headers=self.tgh(200))
        self.assertEqual((st, d["error"]), (503, "vpn_unconfigured"))

    def test_expired_member_403(self):
        self.make_member(200)
        self.h.members.set_expiry(200, int(time.time()) - 10)
        st, _ = self.j("GET", "/api/vpn", headers=self.tgh(200))
        self.assertEqual(st, 403)


class TestGo(Base):
    SUB = urllib.parse.quote("https://example.org:2096/sub/abc", safe="")

    def test_ok(self):
        st, r, p = self.req("GET", f"/go/happ?u={self.SUB}")
        body = p.decode()
        self.assertEqual(st, 200)
        self.assertIn("happ://add/https://example.org:2096/sub/abc", body)
        self.assertIn("Открыть в Happ", body)
        self.assertIn("charset=utf-8", r.getheader("Content-Type"))
        self.assertIn("default-src 'none'", r.getheader("Content-Security-Policy"))
        self.assertIn('http-equiv="refresh"', body)

    def test_v2rayng_scheme(self):
        _, _, p = self.req("GET", f"/go/v2rayng?u={self.SUB}")
        self.assertIn("v2rayng://install-config?url=https%3A%2F%2Fexample.org%3A2096%2Fsub%2Fabc", p.decode())

    def test_foreign_u_rejected(self):
        for u in ("https://evil.example/sub/abc", "javascript:alert(1)", ""):
            st, r, _ = self.req("GET", f"/go/happ?u={urllib.parse.quote(u, safe='')}")
            self.assertEqual(st, 400, u)
        st, _, _ = self.req("GET", "/go/happ")
        self.assertEqual(st, 400)

    def test_unknown_app_and_injection(self):
        st, _, _ = self.req("GET", f"/go/nope?u={self.SUB}")
        self.assertEqual(st, 400)
        evil = urllib.parse.quote('https://example.org:2096/sub/a"><script>x</script>', safe="")
        st, _, _ = self.req("GET", f"/go/happ?u={evil}")
        self.assertEqual(st, 400)


class TestMatrix(Base):
    def post(self, body, uid=200):
        return self.j("POST", "/api/matrix/create", body, self.tgh(uid))

    def test_requires_service(self):
        self.make_member(200, ["vpn"])
        st, d = self.j("GET", "/api/matrix", headers=self.tgh(200))
        self.assertEqual((st, d["error"]), (403, "no_access"))
        st, d = self.post({"username": "bob12345", "password": "x" * 12})
        self.assertEqual(st, 403)

    def test_info(self):
        self.make_member(200)
        st, d = self.j("GET", "/api/matrix", headers=self.tgh(200))
        self.assertEqual(st, 200)
        self.assertEqual((d["server_name"], d["max"], d["can_create"]), ("example.org", 2, True))
        self.assertEqual(d["client_url"], "https://matrix.example.org")
        self.assertEqual(d["accounts"], [])

    def test_create_ok_and_errors(self):
        self.make_member(200)
        st, d = self.post({"username": "Bob_One", "password": "correct horse"})
        self.assertEqual((st, d), (200, {"mxid": "@bob_one:example.org"}))
        self.assertEqual(self.h.syn.created[0][2], "Bob")             # displayname defaults to first_name
        self.assertNotIn("correct horse", json.dumps(d))
        self.assertEqual([a["mxid"] for a in self.h.members.matrix_accounts(200)], ["@bob_one:example.org"])
        self.assertEqual(sum(e["kind"] == "matrix_create" for e in self.h.members.events(200)), 1)
        chat, text = self.h.sent[-1]
        self.assertEqual(chat, OWNER)
        self.assertIn("💬 Bob создал(а) аккаунт", text)
        self.assertNotIn("correct horse", text)

        st, d = self.post({"username": "x", "password": "correct horse"})
        self.assertEqual((st, d["error"]), (400, "bad_username"))
        st, d = self.post({"username": "okname", "password": "short"})
        self.assertEqual((st, d["error"]), (400, "weak_password"))
        st, d = self.post({"username": "taken", "password": "correct horse"})
        self.assertEqual((st, d["error"]), (409, "taken"))
        st, d = self.post({"username": "second1", "password": "correct horse"})
        self.assertEqual(st, 200)
        st, d = self.post({"username": "third12", "password": "correct horse"})
        self.assertEqual((st, d["error"]), (409, "limit"))

    def test_unconfigured(self):
        self.make_member(200)
        self.h.syn = None
        st, d = self.post({"username": "okname", "password": "correct horse"})
        self.assertEqual((st, d["error"]), (503, "matrix_unconfigured"))

    def test_password_reset_own_only(self):
        self.make_member(200)
        self.make_member(201)
        self.h.members.add_matrix(200, "@mine:example.org")
        self.h.members.add_matrix(201, "@theirs:example.org")
        st, d = self.j("POST", "/api/matrix/password",
                       {"mxid": "@theirs:example.org", "password": "correct horse"}, self.tgh(200))
        self.assertEqual((st, d["error"]), (403, "not_yours"))
        st, d = self.j("POST", "/api/matrix/password",
                       {"mxid": "@mine:example.org", "password": "short"}, self.tgh(200))
        self.assertEqual((st, d["error"]), (400, "weak_password"))
        st, d = self.j("POST", "/api/matrix/password",
                       {"mxid": "@mine:example.org", "password": "correct horse"}, self.tgh(200))
        self.assertEqual((st, d), (200, {"ok": True}))
        self.assertEqual(self.h.syn.resets, [("@mine:example.org", "correct horse")])

    def test_locked_null_on_failure(self):
        self.make_member(200)
        self.h.members.add_matrix(200, "@mine:example.org")
        self.h.syn.user = mock.Mock(side_effect=SynapseError("down"))
        _, d = self.j("GET", "/api/matrix", headers=self.tgh(200))
        self.assertIsNone(d["accounts"][0]["locked"])


class TestAdmin(Base):
    def test_extend_notifies_member(self):
        self.make_member(200)
        before = self.h.members.get(200)["expires_ts"]
        st, d = self.j("POST", "/api/admin/members/200", {"action": "extend", "days": 10}, self.admin())
        self.assertEqual(st, 200)
        self.assertEqual(d["expires_ts"], before + 10 * 86400)
        chat, text = self.h.sent[-1]
        self.assertEqual(chat, 200)
        self.assertTrue(text.startswith("✅ Подписка продлена до "))
        self.assertTrue(self.h.members.changed.is_set())

    def test_suspend_resume_services_delete(self):
        self.make_member(200)
        self.j("POST", "/api/admin/members/200", {"action": "suspend"}, self.admin())
        self.assertEqual(self.h.sent[-1], (200, "⏸ Доступ приостановлен владельцем. Вопросы — @owner."))
        st, d = self.j("POST", "/api/admin/members/200", {"action": "resume"}, self.admin())
        self.assertEqual((d["status"], self.h.sent[-1]), ("active", (200, "▶️ Доступ снова открыт.")))
        st, d = self.j("POST", "/api/admin/members/200", {"action": "services", "services": ["tools"]}, self.admin())
        self.assertEqual(d["services"], ["tools"])
        self.assertEqual(self.h.sent[-1][0], 200)
        st, d = self.j("POST", "/api/admin/members/200", {"action": "note", "note": "x" * 201}, self.admin())
        self.assertEqual(st, 400)
        st, d = self.j("POST", "/api/admin/members/200", {"action": "delete"}, self.admin())
        self.assertEqual((st, d), (200, {"deleted": True}))
        self.assertIsNone(self.h.members.get(200))
        self.assertEqual(self.h.sent[-1][0], 200)

    def test_validation(self):
        self.make_member(200)
        for body in ({"action": "extend", "days": 0}, {"action": "extend", "days": 3651},
                     {"action": "extend", "days": "5"}, {"action": "extend", "days": True},
                     {"action": "services", "services": ["bogus"]}, {"action": "services", "services": "vpn"},
                     {"action": "wat"}):
            st, d = self.j("POST", "/api/admin/members/200", body, self.admin())
            self.assertEqual(st, 400, body)
        st, d = self.j("POST", "/api/admin/members/999", {"action": "suspend"}, self.admin())
        self.assertEqual(st, 404)

    def test_set_expiry(self):
        self.make_member(200)
        st, d = self.j("POST", "/api/admin/members/200", {"action": "set_expiry", "ts": None}, self.admin())
        self.assertEqual((st, d["expires_ts"], d["days_left"]), (200, None, None))

    def test_members_list_with_traffic(self):
        self.make_member(200)
        self.h.members.touch(200, None, "app")
        st, r, p = self.req("GET", "/api/admin/members", headers=self.admin())
        d = json.loads(p)
        self.assertEqual(st, 200)
        self.assertEqual((d[0]["traffic"], d[0]["online"], d[0]["app_30d"]), ({"up": 5, "down": 7}, True, 1))
        self.assertIsNone(r.getheader("X-Warning"))

    def test_members_list_xui_down(self):
        self.make_member(200)
        self.h.xui.fail = True
        st, r, p = self.req("GET", "/api/admin/members", headers=self.admin())
        d = json.loads(p)
        self.assertEqual((st, d[0]["traffic"], d[0]["online"]), (200, None, False))
        self.assertEqual(r.getheader("X-Warning"), "vpn_unavailable")

    def test_member_detail(self):
        self.make_member(200)
        self.h.members.add_matrix(200, "@mine:example.org")
        st, d = self.j("GET", "/api/admin/members/200", headers=self.admin())
        self.assertEqual(st, 200)
        self.assertEqual(d["vpn_links_count"], 2)
        self.assertEqual(d["matrix_accounts"][0]["locked"], False)
        self.assertTrue(d["events"])
        st, _ = self.j("GET", "/api/admin/members/9", headers=self.admin())
        self.assertEqual(st, 404)

    def test_grant_and_codes(self):
        st, d = self.j("POST", "/api/admin/grant", {"uid": 300, "days": 5, "services": ["vpn"]}, self.admin())
        self.assertEqual((st, d["services"]), (200, ["vpn"]))
        self.assertEqual(self.h.sent[-1][0], 300)
        st, d = self.j("POST", "/api/admin/codes", {"days": 7, "uses_max": 3, "note": "n"}, self.admin())
        self.assertEqual(st, 200)
        self.assertEqual(d["share_url"], f"https://t.me/MeowBot?start={d['code']}")
        st, lst = self.j("GET", "/api/admin/codes", headers=self.admin())
        self.assertEqual((lst[0]["code"], lst[0]["state"]), (d["code"], "live"))
        self.assertEqual(lst[0]["share_url"], d["share_url"])
        st, _ = self.j("POST", "/api/admin/codes", {"uses_max": 101}, self.admin())
        self.assertEqual(st, 400)
        st, _ = self.j("POST", f"/api/admin/codes/{d['code']}/revoke", None, self.admin())
        self.assertEqual(st, 200)
        st, _ = self.j("POST", "/api/admin/codes/MEOW-NOPE-NOPE/revoke", None, self.admin())
        self.assertEqual(st, 404)

    def test_inbounds(self):
        st, d = self.j("GET", "/api/admin/inbounds", headers=self.admin())
        self.assertEqual((st, [i["member"] for i in d]), (200, [True, False, False]))
        st, d = self.j("POST", "/api/admin/inbounds", {"member_ids": [1, 2, 3]}, self.admin())
        self.assertEqual((st, d["error"]), (400, "two_awg"))
        st, d = self.j("POST", "/api/admin/inbounds", {"member_ids": [1, 99]}, self.admin())
        self.assertEqual(st, 400)
        st, d = self.j("POST", "/api/admin/inbounds", {"member_ids": [1, 3]}, self.admin())
        self.assertEqual((st, [i["member"] for i in d]), (200, [True, False, True]))
        self.assertEqual(self.h.members.member_inbounds(), [1, 3])
        self.assertEqual(self.h.members.known_inbounds(), [1, 3])

    def test_sync(self):
        st, d = self.j("POST", "/api/admin/sync", None, self.admin())
        self.assertEqual((st, d["ts"]), (200, 2))

    def test_overview_and_bot_avatar(self):
        self.make_member(200)
        st, d = self.j("GET", "/api/admin/overview", headers=self.admin())
        self.assertEqual(st, 200)
        self.assertEqual(d["counts"]["members"], 1)
        self.assertEqual(len(d["activity"]), 30)
        self.assertEqual(d["bots"][0]["username"], "HelperBot")
        self.assertEqual(d["bots"][0]["avatar_url"], "./api/admin/bot-avatar/123456")
        self.assertTrue(d["vpn_configured"] and d["matrix_configured"])
        st, r, p = self.req("GET", "/api/admin/bot-avatar/123456", headers=self.admin())
        self.assertEqual((st, r.getheader("Content-Type")), (200, "image/jpeg"))
        st, _, _ = self.req("GET", "/api/admin/bot-avatar/777", headers=self.admin())
        self.assertEqual(st, 404)


class TestAvatar(Base):
    def test_fetch_cache_and_access(self):
        self.make_member(200)
        self.make_member(201)
        st, r, p = self.req("GET", "/api/avatar/200", headers=self.tgh(200))
        self.assertEqual(st, 200)
        self.assertEqual(p, b"\xff\xd8JPEG:photos/mid.jpg")        # 320 px size picked
        n = len(self.h.bot.calls)
        st, _, p2 = self.req("GET", "/api/avatar/200", headers=self.tgh(200))
        self.assertEqual((st, p2, len(self.h.bot.calls)), (200, p, n))
        path = os.path.join(self.tmp.name, "avatars", "200.jpg")
        self.assertTrue(os.path.isfile(path))
        self.assertEqual(os.stat(os.path.dirname(path)).st_mode & 0o777, 0o700)
        st, _, _ = self.req("GET", "/api/avatar/200", headers=self.tgh(201))
        self.assertEqual(st, 403)
        st, _, _ = self.req("GET", "/api/avatar/200", headers=self.admin())
        self.assertEqual(st, 200)

    def test_no_photo_marker(self):
        self.make_member(200)
        self.h.bot.photos = False
        st, _, _ = self.req("GET", "/api/avatar/200", headers=self.tgh(200))
        self.assertEqual(st, 404)
        n = len(self.h.bot.calls)
        st, _, _ = self.req("GET", "/api/avatar/200", headers=self.tgh(200))
        self.assertEqual((st, len(self.h.bot.calls)), (404, n))

    def test_telegram_failure_is_not_cached(self):
        self.make_member(200)
        self.h.bot.fail = True
        st, _, _ = self.req("GET", "/api/avatar/200", headers=self.tgh(200))
        self.assertEqual(st, 502)
        self.h.bot.fail = False
        st, _, _ = self.req("GET", "/api/avatar/200", headers=self.tgh(200))
        self.assertEqual(st, 200)


class TestStatic(Base):
    def test_index_and_assets(self):
        st, r, p = self.req("GET", "/")
        self.assertEqual((st, p), (200, b"<html>SPA</html>"))
        self.assertEqual(r.getheader("Cache-Control"), "no-cache")
        self.assertEqual(r.getheader("X-Content-Type-Options"), "nosniff")
        self.assertEqual(r.getheader("Referrer-Policy"), "no-referrer")
        st, r, p = self.req("GET", "/assets/a.js?v=1")
        self.assertEqual(st, 200)
        self.assertEqual(r.getheader("Content-Type"), "text/javascript; charset=utf-8")
        self.assertIn("immutable", r.getheader("Cache-Control"))

    def test_spa_fallback(self):
        st, _, p = self.req("GET", "/some/client/route")
        self.assertEqual((st, p), (200, b"<html>SPA</html>"))

    def test_missing_asset_is_404_not_html(self):
        st, _, _ = self.req("GET", "/assets/missing.js")
        self.assertEqual(st, 404)

    def test_traversal(self):
        for p in ("/../secret.txt", "/%2e%2e/secret.txt", "/..%2fsecret.txt", "/assets/../../secret.txt",
                  "/%2e%2e/%2e%2e/etc/passwd", "/assets/%00.js"):
            st, _, body = self.req("GET", p)
            self.assertEqual(st, 404, p)
            self.assertNotIn(b"SECRET", body)

    def test_not_built(self):
        os.remove(os.path.join(self.tmp.name, "www", "index.html"))
        st, _, p = self.req("GET", "/")
        self.assertEqual((st, p), (503, b"frontend not built"))

    def test_unknown_api_404_and_method(self):
        st, d = self.j("GET", "/api/nope", headers=self.admin())
        self.assertEqual(st, 404)
        st, _, _ = self.req("POST", "/", raw="{}")
        self.assertEqual(st, 405)


class TestUnit(unittest.TestCase):
    def test_signature_field_stays_in_check_string(self):
        user = webapp.verify_init_data(init_data(TOKEN, {"id": 5}), TOKEN, 3600)
        self.assertEqual(user["id"], 5)

    def test_duplicate_keys_rejected(self):
        raw = init_data(TOKEN, {"id": 5}) + "&auth_date=1"
        with self.assertRaises(webapp.InitDataError):
            webapp.verify_init_data(raw, TOKEN, 3600)


class TestFileBytes(unittest.TestCase):
    """tg.Bot.file_bytes against a local server (tg.API patched)."""

    def setUp(self):
        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(s):
                if s.path.endswith("/big.bin"):
                    data = b"x" * (5 * 1024 * 1024 + 10)
                elif s.path.endswith("/ok.jpg"):
                    data = b"JPEGDATA"
                else:
                    s.send_error(404)
                    return
                s.send_response(200)
                s.send_header("Content-Length", str(len(data)))
                s.end_headers()
                s.wfile.write(data)

            def log_message(s, *a):
                pass

        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)
        p = mock.patch.object(tg, "API", f"http://127.0.0.1:{self.srv.server_address[1]}")
        p.start()
        self.addCleanup(p.stop)

    def test_ok(self):
        self.assertEqual(tg.Bot(TOKEN).file_bytes("photos/ok.jpg"), b"JPEGDATA")

    def test_too_large(self):
        with self.assertRaises(TelegramError):
            tg.Bot(TOKEN).file_bytes("photos/big.bin")

    def test_errors_do_not_leak_token(self):
        with self.assertRaises(TelegramError) as cm:
            tg.Bot(TOKEN).file_bytes("photos/missing.jpg")
        self.assertNotIn("FAKE-TOKEN", str(cm.exception))
        with mock.patch.object(tg, "API", "http://127.0.0.1:1"):
            with self.assertRaises(TelegramError) as cm:
                tg.Bot(TOKEN).file_bytes("photos/ok.jpg")
        self.assertNotIn("FAKE-TOKEN", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
