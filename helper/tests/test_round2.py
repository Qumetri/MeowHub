"""Round 2: link grouping, token management, the second (member) bot.

Web part: same fixtures as test_webapp (real Store + Members, real HTTP server).
Bot part: same fixtures as test_bot; the member bot is a second FakeBot.
"""
import http.server
import json
import os
import tempfile
import threading
import time
import unittest
from unittest import mock

import test_bot
import test_webapp
import tokens
import webapp
from store import Store
from tg import TelegramError
from xui import XUIError

TOKEN, OWNER = test_webapp.TOKEN, test_webapp.OWNER
MTOKEN = "777000:MEMBER-BOT-TOKEN-0123456789abcdef"
CTOKEN = "555000:CRYPTO-BOT-TOKEN-0123456789abcdef"
XTOKEN = "xui-api-token-0123456789abcdef"
NEW_BOT = "888000:NEW-BOT-TOKEN-0123456789abcdefgh"
CLEAN_ENV = {"HELPER_BOT_TOKEN": "", "MEMBER_BOT_TOKEN": "", "CRYPTO_TG_TOKEN": "", "XUI_API_TOKEN": "",
             "CRYPTO_URL": ""}

PQ = ("vless://11111111-2222-3333-4444-555555555555@host.docker.internal:20448?type=xhttp"
      "&encryption=mlkem768x25519plus.random.600s.AAAABBBB&security=none#%F0%9F%A7%AA%20PQ-mh-1")
SUDOKU = ("vless://11111111-2222-3333-4444-555555555555@localhost:20451?type=tcp&encryption=none"
          "&security=none&fm=sudoku-v1#Sudoku-mh-1")
HY2 = "hysteria2://authpass@localhost:20453?sni=example.org&insecure=0#Hysteria2-mh-1"
TG = "tg://proxy?server=127.0.0.1&port=20445&secret=eedeadbeefdeadbeefdeadbeefdeadbeef7777772e676f6f676c652e636f6d#MTProto-mh-1"
SS = "ss://Y2hhY2hhMjAtaWV0Zi1wb2x5MTMwNTpzZWNyZXQ@host.docker.internal:20446?type=tcp#SS-mh-1"
REALITY = ("vless://11111111-2222-3333-4444-555555555555@host.docker.internal:8443?type=tcp&encryption=none"
           "&security=reality&pbk=KEY&fp=chrome&sni=www.icloud.com&sid=ab&flow=xtls-rprx-vision#Speed-mh-1")


class FakeGetMe:
    """Stands in for tg.Bot in webapp: getMe answers by token."""
    registry = {}

    def __init__(self, token):
        self.token = token

    def call(self, method, _timeout=30, **kw):
        if method != "getMe":
            raise AssertionError(method)
        ans = self.registry.get(self.token)
        if ans is None:
            raise TelegramError("Unauthorized")
        return ans


# ------------------------------------------------------------------ pure --
class TestLinkGroups(unittest.TestCase):
    def test_classifier(self):
        cases = [(PQ, "new"), (SUDOKU, "new"), (HY2, "udp"), (TG, "tg"), (SS, "main"), (REALITY, "main"),
                 ("https://t.me/proxy?server=a&port=1&secret=ee", "tg"),
                 ("hy2://x@h:1#n", "udp"), ("hysteria://x@h:1#n", "udp"),
                 ("vmess://eyJhZGQiOiJoIn0=#n", "main"), ("trojan://p@h:443?security=tls#n", "main"),
                 ("vless://u@h:1?encryption=none#%F0%9F%A7%AA%20exp", "new"),    # 🧪 in the name
                 ("vless://u@h:1?fm=&encryption=none#x", "new"),                  # blank fm still counts
                 ("vless://u@h:1?type=tcp#x", "main")]                            # no encryption at all
        for url, want in cases:
            self.assertEqual(webapp.link_group(url), want, url)

    def test_host_rewrite_authority(self):
        d = "example.org"
        self.assertTrue(webapp.fix_host(PQ, d).startswith("vless://11111111-2222-3333-4444-555555555555@example.org:20448?"))
        self.assertIn("@example.org:20453?", webapp.fix_host(HY2, d))
        self.assertIn("@example.org:20446?", webapp.fix_host(SS, d))
        self.assertIn("@example.org:8443?", webapp.fix_host(REALITY, d))
        # a real host is left alone, and so is a host name that merely contains a bad one
        keep = "vless://u@real.example.com:443?x=localhost#localhost"
        self.assertEqual(webapp.fix_host(keep, d), keep)
        self.assertEqual(webapp.fix_host("vless://u@localhost.example.com:1#n", d), "vless://u@localhost.example.com:1#n")
        self.assertEqual(webapp.fix_host(PQ, ""), PQ)

    def test_host_rewrite_vmess(self):
        import base64
        blob = base64.b64encode(json.dumps({"v": "2", "ps": "x", "add": "host.docker.internal", "port": 443}).encode()).decode()
        out = webapp.fix_host("vmess://" + blob + "#frag", "example.org")
        body, _, frag = out[len("vmess://"):].partition("#")
        self.assertEqual(json.loads(base64.b64decode(body))["add"], "example.org")
        self.assertEqual(frag, "frag")
        bad = "vmess://!!!notbase64!!!#x"
        self.assertEqual(webapp.fix_host(bad, "example.org"), bad)

    def test_tg_proxy_rewrite(self):
        out = webapp.tg_proxy_url(TG, "example.org")
        self.assertEqual(out, "https://t.me/proxy?server=example.org&port=20445&secret="
                              "eedeadbeefdeadbeefdeadbeefdeadbeef7777772e676f6f676c652e636f6d")
        # already https, real server: unchanged apart from the dropped fragment
        self.assertEqual(webapp.tg_proxy_url("https://t.me/proxy?server=x.org&port=1&secret=ab", "d.org"),
                         "https://t.me/proxy?server=x.org&port=1&secret=ab")

    def test_names(self):
        self.assertEqual(webapp.link_name(PQ, "mh-1", 1), "🧪 PQ")
        self.assertEqual(webapp.link_name(REALITY, "mh-1", 1), "Speed")
        self.assertEqual(webapp.link_name("vless://u@h:1#Other-mh-2", "mh-1", 3), "Other-mh-2")   # not our suffix
        self.assertEqual(webapp.link_name("vless://u@h:1", "mh-1", 3), "Конфиг 3")
        self.assertEqual(webapp.link_name("tg://proxy?server=a&port=1&secret=b", "mh-1", 2), "MTProto-прокси")

    def test_build_links_groups_order_and_actions(self):
        links, groups = webapp.build_links([TG, HY2, PQ, SS, REALITY, SUDOKU, ""], "mh-1", "example.org")
        self.assertEqual([g["id"] for g in groups], ["main", "new", "udp", "tg"])
        self.assertEqual(len(links), 6)
        by = {g["id"]: g for g in groups}
        self.assertEqual([l["name"] for l in by["main"]["links"]], ["SS", "Speed"])
        self.assertEqual([l["name"] for l in by["new"]["links"]], ["🧪 PQ", "Sudoku"])
        self.assertEqual({l["action"] for l in by["new"]["links"]} | {l["action"] for l in by["udp"]["links"]}, {"copy"})
        self.assertEqual([l["action"] for l in by["tg"]["links"]], ["telegram"])
        self.assertTrue(by["tg"]["links"][0]["url"].startswith("https://t.me/proxy?server=example.org&port=20445"))
        self.assertEqual(by["main"]["apps"], ["Happ", "v2RayTun", "v2rayNG", "Hiddify"])
        self.assertEqual(by["tg"]["apps"], ["Telegram"])
        self.assertTrue(by["udp"]["hint"] and by["udp"]["title"] == "Hysteria2 (UDP)")
        for g in groups:
            for l in g["links"]:
                self.assertNotIn("host.docker.internal", l["url"])
                self.assertNotIn("localhost", l["url"])
                self.assertNotIn("127.0.0.1", l["url"])
        # links keep every entry, host-fixed, no `action`
        self.assertTrue(all(set(l) == {"name", "url"} for l in links))
        self.assertEqual(webapp.build_links([], "mh-1", "d")[1], [])

    def test_plural(self):
        self.assertEqual([webapp.plural_inbounds(n) for n in (1, 2, 5, 11, 21, 25)],
                         ["1 инбаунд", "2 инбаунда", "5 инбаундов", "11 инбаундов", "21 инбаунд", "25 инбаундов"])

    def test_mask(self):
        self.assertEqual(tokens.mask("8123456789:AAHxxxxxxxxxxxxxxxxxxxxa9Zk"), "8123…a9Zk")
        self.assertEqual(tokens.mask(""), "")
        self.assertNotIn("abc", tokens.mask("abc"))


# --------------------------------------------------------------- web: auth --
class Round2Base(test_webapp.Base):
    def setUp(self):
        super().setUp()
        p = mock.patch.dict(os.environ, CLEAN_ENV)
        p.start()
        self.addCleanup(p.stop)

    def add_member_bot(self, username="MemberBot"):
        mb = test_webapp.FakeBot()
        mb.token = MTOKEN
        self.h.member_bot, self.h.member_bot_username = mb, username
        return mb


class TestVia(Round2Base):
    def test_either_token_validates_and_via(self):
        self.add_member_bot()
        _, d = self.j("GET", "/api/me", headers=self.tgh(555))
        self.assertEqual((d["via"], d["role"]), ("helper", "stranger"))
        st, d = self.j("GET", "/api/me", headers=self.tgh(555, token=MTOKEN))
        self.assertEqual((st, d["via"], d["member_bot_username"]), (200, "member", "MemberBot"))
        st, d = self.j("GET", "/api/me", headers=self.tgh(555, token="1:other"))
        self.assertEqual((st, d["error"]), (401, "bad_init_data"))
        st, d = self.j("GET", "/api/me", headers=self.admin())
        self.assertEqual(d["via"], "browser")

    def test_member_token_ignored_without_member_bot(self):
        st, d = self.j("GET", "/api/me", headers=self.tgh(555, token=MTOKEN))
        self.assertEqual((st, d["error"]), (401, "bad_init_data"))
        _, d = self.j("GET", "/api/me", headers=self.tgh(555))
        self.assertEqual(d["member_bot_username"], "")

    def test_expired_reported_for_the_matching_token(self):
        self.add_member_bot()
        st, d = self.j("GET", "/api/me", headers=self.tgh(555, token=MTOKEN, auth_date=int(time.time()) - 3 * 86400))
        self.assertEqual((st, d["error"]), (401, "init_data_expired"))

    def test_only_member_bot_running(self):
        self.add_member_bot()
        self.h.bot = None
        st, d = self.j("GET", "/api/me", headers=self.tgh(555, token=MTOKEN))
        self.assertEqual((st, d["via"]), (200, "member"))

    def test_owner_via_member_bot_is_a_member_or_stranger(self):
        self.add_member_bot()
        st, d = self.j("GET", "/api/me", headers=self.tgh(OWNER, token=MTOKEN))
        self.assertEqual((d["role"], d["via"], d["can_preview"], d["member"]), ("stranger", "member", False, None))
        self.make_member(OWNER)
        _, d = self.j("GET", "/api/me", headers=self.tgh(OWNER, token=MTOKEN))
        self.assertEqual((d["role"], d["can_preview"]), ("member", False))
        self.assertEqual(d["member"]["id"], OWNER)
        st, d = self.j("GET", "/api/admin/members", headers=self.tgh(OWNER, token=MTOKEN))
        self.assertEqual((st, d["error"]), (403, "forbidden"))
        # the same user through the helper bot is still the owner
        _, d = self.j("GET", "/api/me", headers=self.tgh(OWNER))
        self.assertEqual((d["role"], d["via"], d["can_preview"]), ("owner", "helper", True))

    def test_can_preview(self):
        self.add_member_bot()
        self.make_member(200)
        self.assertTrue(self.j("GET", "/api/me", headers=self.admin())[1]["can_preview"])
        self.assertFalse(self.j("GET", "/api/me", headers=self.tgh(200))[1]["can_preview"])
        self.assertFalse(self.j("GET", "/api/me", headers=self.tgh(555, token=MTOKEN))[1]["can_preview"])

    def test_vpn_for_owner_through_member_bot_needs_own_membership(self):
        self.add_member_bot()
        st, d = self.j("GET", "/api/vpn", headers=self.tgh(OWNER, token=MTOKEN))
        self.assertEqual((st, d["error"]), (403, "no_access"))

    def test_share_url_uses_member_bot(self):
        code = self.h.members.new_code(30)
        _, d = self.j("GET", "/api/admin/codes", headers=self.admin())
        self.assertTrue(d[0]["share_url"].startswith("https://t.me/MeowBot?start="))
        self.add_member_bot("MemberBot")
        _, d = self.j("GET", "/api/admin/codes", headers=self.admin())
        self.assertEqual(d[0]["share_url"], f"https://t.me/MemberBot?start={code}")
        _, d = self.j("POST", "/api/admin/codes", {"days": 5}, self.admin())
        self.assertTrue(d["share_url"].startswith("https://t.me/MemberBot?start="))

    def test_avatar_of_a_member_goes_through_the_member_bot(self):
        mb = self.add_member_bot()
        mb.call = lambda method, _timeout=30, **p: ({"total_count": 0, "photos": []}
                                                    if method == "getUserProfilePhotos" else {})
        self.make_member(200)
        st, _ = self.j("GET", "/api/avatar/200", headers=self.tgh(200))
        self.assertEqual(st, 404)                       # member bot: "no photo"
        self.assertNotIn("getUserProfilePhotos", self.h.bot.calls)
        st, _, _ = self.req("GET", f"/api/avatar/{OWNER}", headers=self.admin())
        self.assertEqual(st, 200)                       # the owner's photo comes from the helper bot
        self.assertIn("getUserProfilePhotos", self.h.bot.calls)

    def test_overview_lists_member_bot(self):
        self.add_member_bot()
        FakeGetMe.registry = {MTOKEN: {"id": 777000, "first_name": "Members", "username": "MemberBot"}}
        with mock.patch.object(webapp, "Bot", FakeGetMe):
            self.h.member_bot = FakeGetMe(MTOKEN)
            _, d = self.j("GET", "/api/admin/overview", headers=self.admin())
        self.assertEqual([(b["integration"], b["username"]) for b in d["bots"]],
                         [("helper", "HelperBot"), ("member", "MemberBot")])
        self.assertEqual(d["bots"][1]["avatar_url"], "./api/admin/bot-avatar/777000")


# --------------------------------------------------------------- web: vpn --
class TestVpnGroups(Round2Base):
    def test_groups_and_host_fix(self):
        self.make_member(200)
        self.h.members.set_vpn_sub_id(200, "abc123")
        self.h.xui.client_links = lambda email: [u.replace("mh-1", "mh-200") for u in (REALITY, SS, PQ, SUDOKU, HY2, TG)]
        st, d = self.j("GET", "/api/vpn", headers=self.tgh(200))
        self.assertEqual(st, 200, d)
        self.assertEqual([g["id"] for g in d["groups"]], ["main", "new", "udp", "tg"])
        self.assertEqual(len(d["links"]), 6)
        self.assertEqual(d["sub_url"], "https://example.org:2096/sub/abc123")      # old fields intact
        for l in d["links"]:
            self.assertNotIn("host.docker.internal", l["url"])
            self.assertNotIn("localhost", l["url"])
        self.assertIn("@example.org:8443?", d["groups"][0]["links"][0]["url"])
        self.assertEqual(d["groups"][3]["links"][0]["action"], "telegram")
        self.assertIn("server=example.org&port=20445", d["groups"][3]["links"][0]["url"])
        self.assertNotIn("-mh-", d["groups"][0]["links"][0]["name"] + d["groups"][1]["links"][0]["name"])

    def test_empty_groups_omitted(self):
        self.make_member(200)
        self.h.members.set_vpn_sub_id(200, "abc123")
        st, d = self.j("GET", "/api/vpn", headers=self.tgh(200))
        self.assertEqual([g["id"] for g in d["groups"]], ["main"])
        self.assertEqual([l["name"] for l in d["links"]], ["My Config", "Конфиг 2"])


# ------------------------------------------------------ web: integrations --
class TestIntegrations(Round2Base):
    def setUp(self):
        super().setUp()
        FakeGetMe.registry = {
            TOKEN: None,                                                     # helper: its own FakeBot answers
            MTOKEN: {"id": 777000, "first_name": "Members", "username": "MemberBot"},
            CTOKEN: {"id": 555000, "first_name": "Crypto", "username": "CryptBot"},
            NEW_BOT: {"id": 888000, "first_name": "Fresh", "username": "FreshBot"}}
        for patcher in (mock.patch.dict(os.environ, {"HELPER_BOT_TOKEN": TOKEN, "CRYPTO_TG_TOKEN": CTOKEN,
                                                    "XUI_API_TOKEN": XTOKEN}),
                        mock.patch.object(webapp, "Bot", FakeGetMe),
                        mock.patch.object(webapp, "schedule_restart"),
                        mock.patch.object(webapp, "crypto_apply", return_value=True),
                        mock.patch.object(tokens, "make_xui", side_effect=self.fake_xui)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.restart = webapp.schedule_restart
        self.crypto = webapp.crypto_apply
        self.xui_calls = []

    def fake_xui(self, token):
        if not token:
            return None
        x = test_webapp.FakeXui()
        x.token = token
        if token == "bad-xui-token":
            def boom():
                raise XUIError(f"unauthorized {token}")
            x.inbounds = boom
        return x

    def items(self):
        st, d = self.j("GET", "/api/admin/integrations", headers=self.admin())
        self.assertEqual(st, 200, d)
        return {i["id"]: i for i in d["items"]}

    def post(self, tid, token, path=""):
        return self.j("POST", f"/api/admin/integrations/{tid}{path}", {"token": token}, self.admin())

    # -- GET --
    def test_get_masks_everything(self):
        st, r, raw = self.req("GET", "/api/admin/integrations", headers=self.admin())
        for secret in (TOKEN, MTOKEN, CTOKEN, XTOKEN):
            self.assertNotIn(secret, raw.decode())
        it = self.items()
        self.assertEqual(list(it), ["helper", "member", "crypto", "xui"])
        h = it["helper"]
        self.assertEqual((h["kind"], h["configured"], h["source"], h["masked"], h["restart_on_change"]),
                         ("bot", True, "env", "1234…OKEN", True))
        self.assertEqual(h["bot"]["username"], "HelperBot")
        self.assertTrue(h["bot"]["ok"])
        self.assertEqual((it["member"]["configured"], it["member"]["source"], it["member"]["masked"]),
                         (False, "none", ""))
        self.assertIsNone(it["member"]["bot"])
        self.assertEqual(it["crypto"]["bot"]["username"], "CryptBot")
        self.assertFalse(it["crypto"]["restart_on_change"])
        self.assertIn("n8n", it["crypto"]["note"])
        x = it["xui"]
        self.assertEqual((x["kind"], x["masked"]), ("api", "xui-…cdef"))
        self.assertEqual(x["check"], {"ok": True, "error": "", "detail": "3 инбаунда"})

    def test_avatar_urls_by_integration_id(self):
        it = self.items()
        self.assertEqual(it["helper"]["bot"]["avatar_url"], "./api/admin/bot-avatar/helper")
        self.assertEqual(it["crypto"]["bot"]["avatar_url"], "./api/admin/bot-avatar/crypto")
        self.assertIsNone(it["member"]["bot"])
        self.add_member_bot()
        self.store.set("tok_member", MTOKEN)
        it = self.items()
        self.assertEqual(it["member"]["bot"]["avatar_url"], "./api/admin/bot-avatar/member")
        FakeGetMe.registry.pop(CTOKEN)
        self.webapp_app_cache_clear()
        self.assertIsNone(self.items()["crypto"]["bot"]["avatar_url"])

    def webapp_app_cache_clear(self):
        self.srv.app._getme.clear()

    def test_bot_avatar_accepts_integration_ids(self):
        self.add_member_bot()
        self.h.member_bot.call = lambda method, _timeout=30, **p: (
            {"total_count": 0, "photos": []} if method == "getUserProfilePhotos" else {})
        st, r, _ = self.req("GET", "/api/admin/bot-avatar/helper", headers=self.admin())
        self.assertEqual((st, r.getheader("Content-Type")), (200, "image/jpeg"))
        self.assertIn("getUserProfilePhotos", self.h.bot.calls)
        st, _, _ = self.req("GET", "/api/admin/bot-avatar/member", headers=self.admin())
        self.assertEqual(st, 404)                       # member bot answered "no photo"
        self.h.member_bot = None
        st, _, _ = self.req("GET", "/api/admin/bot-avatar/member", headers=self.admin())
        self.assertEqual(st, 404)
        st, _, _ = self.req("GET", "/api/admin/bot-avatar/bogus", headers=self.admin())
        self.assertEqual(st, 404)

    def test_get_requires_owner(self):
        self.make_member(200)
        st, d = self.j("GET", "/api/admin/integrations", headers=self.tgh(200))
        self.assertEqual((st, d["error"]), (403, "forbidden"))
        st, d = self.j("POST", "/api/admin/integrations/member", {"token": MTOKEN}, self.tgh(200))
        self.assertEqual(st, 403)

    def test_getme_is_cached(self):
        self.items()
        before = len(self.h.bot.calls)
        self.items()
        self.assertEqual(self.h.bot.calls.count("getMe"), before and 1)

    def test_getme_error_is_reported_without_the_token(self):
        FakeGetMe.registry.pop(CTOKEN)
        c = self.items()["crypto"]
        self.assertEqual((c["bot"]["ok"], c["bot"]["error"]), (False, "Unauthorized"))

    # -- POST helper / member --
    def test_set_member_validates_saves_and_restarts(self):
        st, d = self.post("member", MTOKEN)
        self.assertEqual(st, 200, d)
        self.assertTrue(d["restarting"])
        self.assertEqual((d["item"]["source"], d["item"]["masked"], d["item"]["bot"]["username"]),
                         ("page", "7770…cdef", "MemberBot"))
        self.assertIsNotNone(d["item"]["updated_ts"])
        self.assertNotIn(MTOKEN, json.dumps(d))
        self.assertEqual(self.store.get("tok_member"), MTOKEN)
        self.restart.assert_called_once()

    def test_set_strips_whitespace(self):
        st, d = self.post("member", f"  {MTOKEN}\n")
        self.assertEqual(st, 200)
        self.assertEqual(self.store.get("tok_member"), MTOKEN)

    def test_same_token_no_restart(self):
        st, d = self.post("helper", TOKEN)                # already the effective one (env)
        self.assertEqual((st, d["restarting"]), (200, False))
        self.restart.assert_not_called()
        self.assertEqual(d["item"]["source"], "page")

    def test_helper_change_restarts(self):
        st, d = self.post("helper", NEW_BOT)
        self.assertEqual((st, d["restarting"]), (200, True))
        self.assertEqual(self.store.get("tok_helper"), NEW_BOT)

    def test_same_bot_rejected(self):
        # a different token string for the helper's own bot id (rotated token)
        FakeGetMe.registry["123456:ROTATED-TOKEN-0123456789abcdef"] = {"id": 123456, "first_name": "H"}
        st, d = self.post("member", "123456:ROTATED-TOKEN-0123456789abcdef")
        self.assertEqual((st, d["error"]), (400, "same_bot"))
        self.assertEqual(self.store.get("tok_member"), "")
        self.restart.assert_not_called()
        # and the other direction: a helper token that is the member bot
        self.store.set("tok_member", MTOKEN)
        FakeGetMe.registry["777000:ROTATED-TOKEN-0123456789abcdef"] = {"id": 777000, "first_name": "M"}
        st, d = self.post("helper", "777000:ROTATED-TOKEN-0123456789abcdef")
        self.assertEqual((st, d["error"]), (400, "same_bot"))

    def test_bad_tokens(self):
        for bad, frag in (("999:NOT-REGISTERED-0123456789abcd", "Unauthorized"), ("hello world", "не похоже"),
                          ("", "пуст"), ("x" * 201, "длинный")):
            st, d = self.post("member", bad)
            self.assertEqual((st, d["error"]), (400, "bad_token"), bad)
            self.assertIn(frag, d["message"])
            if bad.strip():
                self.assertNotIn(bad, d["message"])
        st, d = self.j("POST", "/api/admin/integrations/member", {"token": 5}, self.admin())
        self.assertEqual((st, d["error"]), (400, "bad_request"))
        st, d = self.j("POST", "/api/admin/integrations/nope", {"token": MTOKEN}, self.admin())
        self.assertEqual(st, 404)
        self.assertEqual(self.store.get("tok_member"), "")
        self.restart.assert_not_called()

    # -- POST crypto --
    def test_crypto_applies_then_saves(self):
        st, d = self.post("crypto", NEW_BOT)
        self.assertEqual((st, d["restarting"]), (200, False))
        self.crypto.assert_called_once_with(NEW_BOT)
        self.assertEqual(self.store.get("tok_crypto"), NEW_BOT)
        self.assertEqual(d["item"]["bot"]["username"], "FreshBot")
        self.restart.assert_not_called()

    def test_crypto_apply_failed_saves_nothing(self):
        self.crypto.return_value = False
        st, d = self.post("crypto", NEW_BOT)
        self.assertEqual((st, d["error"]), (502, "crypto_apply_failed"))
        self.assertEqual(self.store.get("tok_crypto"), "")
        self.assertEqual(self.items()["crypto"]["source"], "env")

    def test_crypto_bad_token_never_reaches_crypto(self):
        st, d = self.post("crypto", "999:NOT-REGISTERED-0123456789abcd")
        self.assertEqual(st, 400)
        self.crypto.assert_not_called()

    # -- POST xui --
    def test_xui_swap_updates_helper_and_reconciler(self):
        old = self.h.xui
        st, d = self.post("xui", "new-xui-token-0123456789")
        self.assertEqual((st, d["restarting"]), (200, False), d)
        self.assertIsNot(self.h.xui, old)
        self.assertEqual(self.h.xui.token, "new-xui-token-0123456789")
        self.assertIs(self.h.rec.xui, self.h.xui)
        self.assertEqual(self.store.get("tok_xui"), "new-xui-token-0123456789")
        self.assertEqual(d["item"]["check"]["ok"], True)
        self.assertNotIn("new-xui-token-0123456789", json.dumps(d))
        self.restart.assert_not_called()

    def test_xui_bad_token_keeps_the_old_client(self):
        old = self.h.xui
        st, d = self.post("xui", "bad-xui-token")
        self.assertEqual((st, d["error"]), (400, "bad_token"))
        self.assertNotIn("bad-xui-token", d["message"])
        self.assertIs(self.h.xui, old)
        self.assertIs(self.h.rec.xui, old)
        self.assertEqual(self.store.get("tok_xui"), "")

    def test_xui_failed_check_shown_in_get(self):
        os.environ["XUI_API_TOKEN"] = "bad-xui-token"
        x = self.items()["xui"]
        self.assertFalse(x["check"]["ok"])
        self.assertNotIn("bad-xui-token", json.dumps(x))

    # -- reset --
    def test_reset_without_override_404(self):
        st, d = self.j("POST", "/api/admin/integrations/member/reset", {}, self.admin())
        self.assertEqual((st, d["error"]), (404, "no_override"))

    def test_reset_reverts_to_env(self):
        self.post("helper", NEW_BOT)
        self.restart.reset_mock()
        st, d = self.j("POST", "/api/admin/integrations/helper/reset", {}, self.admin())
        self.assertEqual((st, d["restarting"]), (200, True))
        self.assertEqual((d["item"]["source"], d["item"]["masked"]), ("env", "1234…OKEN"))
        self.assertEqual(self.store.get("tok_helper"), "")
        self.assertEqual(self.store.get("tok_helper_ts"), "")
        self.restart.assert_called_once()

    def test_reset_member_without_env_removes_the_bot(self):
        self.post("member", MTOKEN)
        self.restart.reset_mock()
        st, d = self.j("POST", "/api/admin/integrations/member/reset", {}, self.admin())
        self.assertEqual((st, d["restarting"], d["item"]["configured"]), (200, True, False))

    def test_reset_helper_without_fallback_refused(self):
        os.environ["HELPER_BOT_TOKEN"] = ""
        self.store.set("tok_helper", NEW_BOT)
        st, d = self.j("POST", "/api/admin/integrations/helper/reset", {}, self.admin())
        self.assertEqual((st, d["error"]), (400, "no_fallback"))
        self.assertEqual(self.store.get("tok_helper"), NEW_BOT)

    def test_reset_crypto_clears_override_at_the_tracker(self):
        self.post("crypto", NEW_BOT)
        self.crypto.reset_mock()
        st, d = self.j("POST", "/api/admin/integrations/crypto/reset", {}, self.admin())
        self.assertEqual((st, d["restarting"]), (200, False))
        self.crypto.assert_called_once_with("")
        self.assertEqual(d["item"]["source"], "env")
        # tracker unreachable: the override stays
        self.post("crypto", NEW_BOT)
        self.crypto.return_value = False
        st, d = self.j("POST", "/api/admin/integrations/crypto/reset", {}, self.admin())
        self.assertEqual((st, d["error"]), (502, "crypto_apply_failed"))
        self.assertEqual(self.store.get("tok_crypto"), NEW_BOT)

    def test_reset_xui_swaps_back_to_env(self):
        self.post("xui", "new-xui-token-0123456789")
        st, d = self.j("POST", "/api/admin/integrations/xui/reset", {}, self.admin())
        self.assertEqual(st, 200)
        self.assertEqual(self.h.xui.token, XTOKEN)
        self.assertIs(self.h.rec.xui, self.h.xui)

    def test_precedence_page_over_env_over_legacy(self):
        os.environ["HELPER_BOT_TOKEN"] = ""
        self.store.set("tg_token", "legacy:TOKEN-0123456789")
        self.assertEqual(tokens.resolve(self.store, "helper"), ("legacy:TOKEN-0123456789", "ctl"))
        os.environ["HELPER_BOT_TOKEN"] = TOKEN
        self.assertEqual(tokens.resolve(self.store, "helper"), (TOKEN, "env"))
        self.store.set("tok_helper", NEW_BOT)
        self.assertEqual(tokens.resolve(self.store, "helper"), (NEW_BOT, "page"))


class TestCryptoApply(unittest.TestCase):
    """The real HTTP call, against a throwaway local server."""

    def serve(self, status):
        seen = []

        class H(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                seen.append((self.path, self.headers.get("Content-Type"),
                             json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
                self.send_response(status)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *a):
                pass

        srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        return srv.server_address[1], seen

    def test_posts_json_and_checks_status(self):
        port, seen = self.serve(200)
        with mock.patch.dict(os.environ, {"CRYPTO_URL": f"http://127.0.0.1:{port}/api/settings"}):
            self.assertTrue(webapp.crypto_apply("tok"))
            self.assertTrue(webapp.crypto_apply(""))
        self.assertEqual(seen, [("/api/settings", "application/json", {"tg_token_override": "tok"}),
                                ("/api/settings", "application/json", {"tg_token_override": ""})])

    def test_base_url_gets_the_path(self):
        port, seen = self.serve(204)
        with mock.patch.dict(os.environ, {"CRYPTO_URL": f"http://127.0.0.1:{port}"}):
            self.assertTrue(webapp.crypto_apply("t"))
        self.assertEqual(seen[0][0], "/api/settings")

    def test_failures(self):
        port, _ = self.serve(500)
        with mock.patch.dict(os.environ, {"CRYPTO_URL": f"http://127.0.0.1:{port}/api/settings"}):
            self.assertFalse(webapp.crypto_apply("t"))
        with mock.patch.dict(os.environ, {"CRYPTO_URL": "http://127.0.0.1:1/api/settings"}):
            self.assertFalse(webapp.crypto_apply("t"))


# ------------------------------------------------------------------- bots --
OWNER_ID = test_bot.OWNER
frm, msg, cb = test_bot.frm, test_bot.msg, test_bot.cb


class TwoBots(unittest.TestCase):
    def setUp(self):
        p = mock.patch.dict(os.environ, dict(test_bot.ENV, **CLEAN_ENV))
        p.start()
        self.addCleanup(p.stop)
        d = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        self.h = test_bot.botmod.Helper(Store(os.path.join(d, "t.db")))
        self.h.bot = self.hb = test_bot.FakeBot()
        self.hb.token = TOKEN
        self.h.bot_username = "HelperBot"
        self.mb = test_bot.FakeBot()
        self.mb.token = MTOKEN
        self.h.attach_member_bot(self.mb, "MemberBot")
        self.front = self.h.member_front
        self.mm = self.h.members

    def make_member(self, uid=50, services=None, days=30):
        self.mm.grant(uid, frm(uid), days, services)
        return uid

    def btns(self, entry):
        return [b for r in entry[3]["reply_markup"]["inline_keyboard"] for b in r]


class TestHelperBotRedirect(TwoBots):
    def test_stranger_is_redirected(self):
        self.h.on_message(msg(77, "привет"))
        out = self.hb.sent(77)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0][2], "🔒 Это служебный бот. MeowHub — в @MemberBot.")
        self.assertEqual([b["url"] for b in self.btns(out[0])], ["https://t.me/MemberBot"])
        self.assertEqual(self.hb.sent(OWNER_ID), [])               # no stranger notice from here
        self.assertEqual(self.mb.log, [])
        self.assertIsNone(self.mm.get(77))

    def test_member_is_redirected_too(self):
        uid = self.make_member(50)
        seen = self.mm.get(uid)["last_seen_ts"]
        with mock.patch.object(self.mm, "touch") as touch:
            self.h.on_message(msg(uid, "/vpn"))
            touch.assert_not_called()                               # not counted on the helper bot
        self.assertIn("служебный бот", self.hb.sent(uid)[0][2])
        self.assertEqual(self.mm.get(uid)["last_seen_ts"], seen)

    def test_code_gets_an_activation_button_and_is_not_redeemed(self):
        code = self.mm.new_code(30)
        for text in (code.lower().replace("-", " "), f"/start {code}", code):
            self.hb.log.clear()
            self.h.on_message(msg(77, text))
            out = self.hb.sent(77)
            self.assertEqual(len(out), 1, text)
            btns = self.btns(out[0])
            self.assertEqual([b["url"] for b in btns],
                             ["https://t.me/MemberBot", f"https://t.me/MemberBot?start={code}"], text)
            self.assertIn("@MemberBot", btns[1]["text"])
        self.assertIsNone(self.mm.get(77))
        self.assertEqual(self.mm.codes()[0]["uses"], 0)

    def test_non_code_start_has_one_button(self):
        self.h.on_message(msg(77, "/start"))
        self.assertEqual(len(self.btns(self.hb.sent(77)[0])), 1)

    def test_callbacks_from_others_refused(self):
        uid = self.make_member(50)
        self.h.on_callback(cb(uid, f"yt:abc:x"))
        self.h.on_callback(cb(999, "m:ext:50:30"))
        self.assertTrue(all(a[3]["alert"] for a in self.hb.answers()))
        self.assertEqual(len(self.hb.answers()), 2)

    def test_owner_keeps_everything_and_code_links_use_the_member_bot(self):
        self.h.on_message(msg(OWNER_ID, "/code 90 vpn"))
        out = self.hb.sent(OWNER_ID)[-1]
        code = self.mm.codes()[0]["code"]
        self.assertIn(f"https://t.me/MemberBot?start={code}", out[2])
        self.assertNotIn("HelperBot?", out[2])
        share = [b for b in self.btns(out) if "share/url" in b.get("url", "")][0]["url"]
        self.assertIn("MemberBot", share)
        self.h.on_message(msg(OWNER_ID, botmod_B("B_MEMBERS")))
        self.assertIn("Участники", self.hb.sent(OWNER_ID)[-1][2])
        self.assertEqual(self.mb.log, [])

    def test_owner_redeem_via_helper_bot_is_not_a_redirect(self):
        code = self.mm.new_code(30)
        self.h.on_message(msg(OWNER_ID, code))
        self.assertIsNotNone(self.mm.get(OWNER_ID))

    def test_single_bot_mode_has_no_redirect(self):
        d = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        h = test_bot.botmod.Helper(Store(os.path.join(d, "s.db")))
        h.bot = b = test_bot.FakeBot()
        h.on_message(msg(77, "привет"))
        self.assertIn("закрытый сервис", b.sent(77)[0][2])


def botmod_B(name):
    return getattr(test_bot.botmod, name)


class TestMemberBot(TwoBots):
    def test_stranger_gets_lock_via_member_bot_and_owner_notice_via_helper_bot(self):
        self.front.on_message(msg(77, "привет", first="Zed", user="zed"))
        self.front.on_message(msg(77, "ещё"))
        self.assertEqual(len(self.mb.sent(77)), 2)
        self.assertIn("закрытый сервис", self.mb.sent(77)[0][2])
        self.assertEqual(self.mb.sent(OWNER_ID), [])
        notes = self.hb.sent(OWNER_ID)
        self.assertEqual(len(notes), 1)
        self.assertEqual([b["callback_data"] for b in self.btns(notes[0])], ["g:77:30", "g:77:x"])
        self.assertIn("Zed", notes[0][2])

    def test_grant_button_on_helper_bot_welcomes_through_member_bot(self):
        self.front.on_message(msg(77, "hello", first="Zed", user="zed"))
        self.h.on_callback(cb(OWNER_ID, "g:77:30"))
        self.assertIsNotNone(self.mm.get(77))
        self.assertTrue(any("Добро пожаловать" in e[2] for e in self.mb.sent(77)))
        self.assertEqual(self.hb.sent(77), [])
        self.assertIn("Доступ выдан", [e for e in self.hb.log if e[0] == "edit"][-1][2])

    def test_code_redeemed_on_member_bot(self):
        code = self.mm.new_code(30, ["vpn", "matrix"])
        self.front.on_message(msg(78, f"/start {code}", first="Ann", user="ann"))
        self.assertEqual(self.mm.get(78)["services"], ["vpn", "matrix"])
        self.assertTrue(any("Добро пожаловать" in e[2] for e in self.mb.sent(78)))
        self.assertEqual(self.hb.sent(78), [])
        note = self.hb.sent(OWNER_ID)
        self.assertEqual(len(note), 1)
        self.assertIn("активировал", note[0][2])
        # the reply keyboard is the member's, not the owner's
        kb = [b["text"] for r in self.mb.sent(78)[-1][3]["reply_markup"]["keyboard"] for b in r]
        self.assertIn(botmod_B("B_VPN"), kb)
        self.assertNotIn(botmod_B("B_PASS"), kb)

    def test_owner_on_member_bot_is_a_stranger_without_membership(self):
        self.front.on_message(msg(OWNER_ID, "/start"))
        self.assertIn("закрытый сервис", self.mb.sent(OWNER_ID)[0][2])
        self.assertEqual(self.hb.sent(OWNER_ID), [])                # no "👤 Боту пишет …" about themselves
        self.assertEqual(self.front.role(OWNER_ID), None)
        self.assertIsNone(self.front.keyboard(OWNER_ID))

    def test_owner_on_member_bot_with_membership_gets_member_menus_only(self):
        self.make_member(OWNER_ID)
        self.assertEqual(self.front.role(OWNER_ID), "member")
        self.front.on_message(msg(OWNER_ID, "/start"))
        texts = [b["text"] for e in self.mb.sent(OWNER_ID) if "keyboard" in str(e[3].get("reply_markup"))
                 for r in e[3]["reply_markup"].get("keyboard", []) for b in r]
        self.assertIn(botmod_B("B_VPN"), texts)
        for owner_only in ("B_PASS", "B_MEMBERS", "B_CODE", "B_HEALTH"):
            self.assertNotIn(botmod_B(owner_only), texts)
        with mock.patch.object(self.h, "cmd_health") as health:
            self.front.on_message(msg(OWNER_ID, "/health"))
            health.assert_not_called()
        self.front.on_message(msg(OWNER_ID, "/pass"))
        self.front.on_message(msg(OWNER_ID, "/code 30"))
        self.assertEqual(self.mm.codes(), [])
        self.assertIn("только владельцу", self.mb.sent(OWNER_ID)[-1][2])
        self.assertEqual(self.hb.sent(OWNER_ID), [])

    def test_member_tools_on_member_bot(self):
        uid = self.make_member(53, services=["vpn", "tools"])
        with mock.patch.object(test_bot.botmod.Helper, "cmd_health") as health:
            self.front.on_message(msg(uid, "/health"))
            health.assert_called_once()
        self.assertEqual(self.front.bot, self.mb)

    def test_owner_buttons_refused_on_member_bot(self):
        self.front.on_callback(cb(OWNER_ID, "m:ext:1:30"))
        self.assertEqual(self.mb.answers()[-1][2], "Нет доступа")
        uid = self.make_member(60)
        before = self.mm.get(uid)["expires_ts"]
        self.front.on_callback(cb(uid, f"m:ext:{uid}:30"))
        self.assertEqual(self.mm.get(uid)["expires_ts"], before)

    def test_vpn_flow_runs_on_member_bot_and_sees_hot_swapped_xui(self):
        uid = self.make_member(58)

        class X:
            token = "t"

            def inbounds(self):
                return []
        self.h.xui = X()
        self.assertIs(self.front.xui, self.h.xui)
        self.h.rec.sync_member = lambda u: self.mm.set_vpn_sub_id(u, "newsub")
        self.front.on_message(msg(uid, "/vpn"))
        self.assertIn("newsub", self.mb.sent(uid)[-1][2])
        self.assertEqual(self.hb.sent(uid), [])
        # assignments from handlers land on the shared Helper
        self.front.pending_urls = {"k": 1}
        self.assertEqual(self.h.pending_urls, {"k": 1})
        self.assertEqual(self.front.kind, "member")
        self.assertEqual(self.h.kind, "helper")
        self.assertIs(self.front.core, self.h)


class TestRouting(TwoBots):
    def test_notify_routes_owner_to_helper_and_others_to_member_bot(self):
        self.h.notify(OWNER_ID, "to owner")
        self.h.notify(50, "to member")
        self.h.notify_owner("to owner again")
        self.assertEqual([e[2] for e in self.hb.log if e[0] == "send"], ["to owner", "to owner again"])
        self.assertEqual([e[2] for e in self.mb.log if e[0] == "send"], ["to member"])

    def test_notify_without_member_bot_uses_helper_bot(self):
        self.h.member_bot, self.h.member_front = None, None
        self.h.notify(50, "x")
        self.assertEqual([e[2] for e in self.hb.log if e[0] == "send"], ["x"])

    def test_notify_from_the_member_front_routes_the_same_way(self):
        self.front.notify(OWNER_ID, "o")
        self.front.notify(50, "m")
        self.assertEqual([e[2] for e in self.hb.log if e[0] == "send"], ["o"])
        self.assertEqual([e[2] for e in self.mb.log if e[0] == "send"], ["m"])

    def test_notify_swallows_errors_and_missing_bots(self):
        def boom(*a, **k):
            raise test_bot.botmod.TelegramError("blocked")
        self.mb.send = boom
        self.assertIsNone(self.h.notify(50, "x"))
        self.h.bot = None
        self.assertIsNone(self.h.notify(OWNER_ID, "x"))

    def test_reconciler_sends_through_the_router(self):
        self.assertEqual(self.h.rec.send, self.h.notify)
        self.h.rec.send(50, "reminder")
        self.h.rec.send(OWNER_ID, "owner reminder")
        self.assertEqual([e[1] for e in self.mb.log if e[0] == "send"], [50])
        self.assertEqual([e[1] for e in self.hb.log if e[0] == "send"], [OWNER_ID])

    def test_owner_card_actions_notify_through_member_bot(self):
        uid = self.make_member(60)
        self.h.on_callback(cb(OWNER_ID, f"m:ext:{uid}:30"))
        self.h.on_callback(cb(OWNER_ID, f"m:sus:{uid}"))
        self.assertTrue(any("Подписка продлена" in e[2] for e in self.mb.sent(uid)))
        self.assertTrue(any("приостановлен" in e[2] for e in self.mb.sent(uid)))
        self.assertEqual(self.hb.sent(uid), [])

    def test_bot_tokens(self):
        self.assertEqual(self.h.bot_tokens(), {"helper": TOKEN, "member": MTOKEN})
        self.h.member_bot = None
        self.assertEqual(self.h.bot_tokens(), {"helper": TOKEN, "member": None})


class TestWiring(TwoBots):
    def test_commands(self):
        self.h._commands()
        self.front._commands()
        helper_cmds = [e for e in self.hb.log if e[0] == "setMyCommands"]
        self.assertEqual([c["command"] for c in helper_cmds[0][3]["commands"]], ["start", "help"])
        self.assertTrue({"code", "members", "pass"} <= {c["command"] for c in helper_cmds[1][3]["commands"]})
        member_cmds = [e for e in self.mb.log if e[0] == "setMyCommands"]
        self.assertEqual(len(member_cmds), 1)
        self.assertEqual([c["command"] for c in member_cmds[0][3]["commands"]],
                         ["start", "help", "vpn", "matrix", "sub"])

    def test_menu_buttons(self):
        self.h._menu_button()
        self.front._menu_button()
        mine = [e[3] for e in self.hb.log if e[0] == "setChatMenuButton"]
        self.assertEqual(mine[0], {"menu_button": {"type": "commands"}})
        self.assertEqual(mine[1]["chat_id"], OWNER_ID)
        self.assertEqual(mine[1]["menu_button"]["web_app"]["url"], "https://example.org/app/")
        theirs = [e[3] for e in self.mb.log if e[0] == "setChatMenuButton"]
        self.assertEqual(len(theirs), 1)
        self.assertNotIn("chat_id", theirs[0])
        self.assertEqual(theirs[0]["menu_button"]["type"], "web_app")
        self.assertEqual(theirs[0]["menu_button"]["text"], "MeowHub")

    def test_start_member_bot(self):
        class B:
            def __init__(self, token):
                self.token = token

            def call(self, method, **kw):
                return {"id": int(self.token.split(":")[0]), "username": "U" + self.token.split(":")[0]}

        self.h.member_bot, self.h.member_front = None, None
        self.h.helper_id = 123456
        with mock.patch.object(test_bot.botmod, "Bot", B):
            self.h._start_member_bot()                              # nothing configured
            self.assertIsNone(self.h.member_bot)
            self.h.store.set("tok_member", "123456:SAME-AS-HELPER-0123456789")
            self.h._start_member_bot()                              # same bot id: ignored
            self.assertIsNone(self.h.member_bot)
            self.h.store.set("tok_member", MTOKEN)
            self.h._start_member_bot()
        self.assertEqual((self.h.member_bot.token, self.h.member_bot_username), (MTOKEN, "U777000"))
        self.assertEqual(self.h.member_front.kind, "member")
        self.assertEqual(self.h.member_front.bot_username, "U777000")

    def test_start_member_bot_rejected_token_is_not_fatal(self):
        class B:
            def __init__(self, token):
                pass

            def call(self, method, **kw):
                raise test_bot.botmod.TelegramError("Unauthorized")

        self.h.member_bot, self.h.member_front = None, None
        self.h.store.set("tok_member", MTOKEN)
        with mock.patch.object(test_bot.botmod, "Bot", B):
            self.h._start_member_bot()
        self.assertIsNone(self.h.member_bot)
        self.assertIs(self.h.front_for(50), self.h)              # single-bot mode again


if __name__ == "__main__":
    unittest.main()
