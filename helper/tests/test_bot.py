import os
import sys
import tempfile
import time
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

try:
    import webapp                         # noqa: F401
except ImportError:                       # written by another work package
    stub = types.ModuleType("webapp")
    stub.start = lambda helper, port=None: None
    sys.modules["webapp"] = stub

import bot as botmod                      # noqa: E402
from store import Store                   # noqa: E402

DAY = 86400
OWNER = 1
ENV = {"HELPER_OWNER_ID": str(OWNER), "BASE_DOMAIN": "example.org", "BOT_APP_PATH": "app",
       "OWNER_CONTACT": "@boss", "XUI_API_TOKEN": "", "MATRIX_ADMIN_PASSWORD": "", "MATRIX_ADMIN_USER": ""}


class FakeBot:
    def __init__(self):
        self.log = []                     # (method, chat_id, text, kwargs)
        self.n = 100

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

    def answers(self):
        return [e for e in self.log if e[0] == "answer"]


class FakeXUI:
    def __init__(self, inbounds):
        self._i = inbounds

    def inbounds(self):
        return list(self._i)


def frm(uid, first="Bob", user="bob"):
    return {"id": uid, "first_name": first, "username": user, "language_code": "ru"}


def msg(uid, text, **kw):
    return {"message_id": 7, "from": frm(uid, **kw), "chat": {"id": uid, "type": "private"}, "text": text}


def cb(uid, data, mid=500):
    return {"id": f"q{uid}{mid}", "from": frm(uid), "data": data,
            "message": {"message_id": mid, "chat": {"id": uid, "type": "private"}}}


class BotTest(unittest.TestCase):
    def setUp(self):
        p = mock.patch.dict(os.environ, ENV)
        p.start()
        self.addCleanup(p.stop)
        d = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        self.h = botmod.Helper(Store(os.path.join(d, "t.db")))
        self.h.bot = FakeBot()
        self.h.bot_username = "TestBot"
        self.b = self.h.bot
        self.mm = self.h.members

    def texts(self, chat_id):
        return "\n".join(e[2] for e in self.b.sent(chat_id))

    def make_member(self, uid=50, days=30, services=None, **kw):
        self.mm.grant(uid, frm(uid, **kw), days, services)
        return uid

    # ------------------------------------------------------------ strangers --
    def test_stranger_gets_lock_and_owner_one_notice(self):
        self.h.on_message(msg(77, "привет"))
        self.h.on_message(msg(77, "ещё раз"))
        out = self.b.sent(77)
        self.assertEqual(len(out), 2)
        self.assertIn("закрытый сервис", out[0][2])
        self.assertIn("@boss", out[0][2])
        notes = self.b.sent(OWNER)
        self.assertEqual(len(notes), 1)
        self.assertIn("<code>77</code>", notes[0][2])
        data = [b["callback_data"] for r in notes[0][3]["reply_markup"]["inline_keyboard"] for b in r]
        self.assertEqual(data, ["g:77:30", "g:77:x"])
        # the inline app button appears only because BOT_APP_PATH is set
        self.assertEqual(out[0][3]["reply_markup"]["inline_keyboard"][0][0]["web_app"]["url"],
                         "https://example.org/app/")

    def test_no_webapp_url_omits_buttons(self):
        self.h.webapp_url = ""
        self.h.on_message(msg(77, "hi"))
        self.assertIsNone(self.b.sent(77)[0][3].get("reply_markup"))

    def test_stranger_redeems_code_with_spaces_and_lowercase(self):
        code = self.mm.new_code(30, ["vpn", "matrix"])
        spaced = " " + code.lower().replace("-", " ") + " "
        self.h.on_message(msg(77, spaced))
        m = self.mm.get(77)
        self.assertIsNotNone(m)
        self.assertEqual(m["services"], ["vpn", "matrix"])
        self.assertIn("Добро пожаловать", self.texts(77))
        note = self.texts(OWNER)
        self.assertIn("активировал", note)
        self.assertIn(code, note)
        self.assertIn("+30 д", note)
        self.assertEqual(self.h.role(77), "member")

    def test_start_deep_link(self):
        code = self.mm.new_code(30)
        self.h.on_message(msg(78, f"/start {code}"))
        self.assertIsNotNone(self.mm.get(78))

    def test_bad_code_messages(self):
        self.h.on_message(msg(79, "MEOW-AAAA-BBBB"))
        self.assertIn("Такого кода нет", self.texts(79))
        self.assertIsNone(self.mm.get(79))

    def test_grant_button_welcomes_user(self):
        self.h.on_message(msg(77, "hello", first="Zed", user="zed"))
        self.h.on_callback(cb(OWNER, "g:77:30"))
        m = self.mm.get(77)
        self.assertEqual(m["first_name"], "Zed")
        self.assertIn("Zed", self.texts(77))
        edits = [e for e in self.b.log if e[0] == "edit"]
        self.assertIn("Доступ выдан", edits[-1][2])

    # -------------------------------------------------------------- members --
    def test_expired_member_paused_and_code_extends(self):
        uid = self.make_member(50)
        self.mm.set_expiry(uid, int(time.time()) - 5 * DAY)
        self.h.on_message(msg(uid, "что там"))
        out = self.texts(uid)
        self.assertIn("Доступ приостановлен", out)
        self.assertIn("подписка закончилась", out)
        kb = self.b.sent(uid)[-1][3]["reply_markup"]["keyboard"]
        self.assertTrue(any(b["text"] == botmod.B_HELP for r in kb for b in r))
        self.assertFalse(any(b["text"] == botmod.B_VPN for r in kb for b in r))
        code = self.mm.new_code(30)
        self.h.on_message(msg(uid, code))
        self.assertIn("Подписка продлена", self.texts(uid))
        self.assertEqual(self.mm.status(self.mm.get(uid)), "active")

    def test_suspended_member_code_refused(self):
        uid = self.make_member(51)
        self.mm.suspend(uid)
        self.h.on_message(msg(uid, "hi"))
        self.assertIn("владелец приостановил", self.texts(uid))
        self.h.on_message(msg(uid, self.mm.new_code(30)))
        self.assertIn("@boss", self.b.sent(uid)[-1][2])
        self.assertEqual(self.mm.status(self.mm.get(uid)), "suspended")

    def test_member_without_tools(self):
        uid = self.make_member(52)
        with mock.patch.object(self.h, "cmd_health") as health:
            self.h.on_message(msg(uid, "/health"))
            self.h.on_message(msg(uid, botmod.B_HEALTH))
            health.assert_not_called()
        self.h.on_message(msg(uid, "https://youtu.be/x"))
        self.h.on_message(msg(uid, "/pass"))
        self.h.on_message(msg(uid, "/code 90"))
        outs = [e[2] for e in self.b.sent(uid)]
        self.assertIn("не входит в твою подписку", outs[0])
        self.assertIn("только владельцу", outs[3])
        self.assertIn("только владельцу", outs[4])
        self.assertEqual(self.mm.codes(), [])

    def test_member_with_tools_gets_health(self):
        uid = self.make_member(53, services=["vpn", "tools"])
        with mock.patch.object(self.h, "cmd_health") as health:
            self.h.on_message(msg(uid, "/health"))
            health.assert_called_once()
        kb = self.h.keyboard(uid)["keyboard"]
        self.assertTrue(any(b["text"] == botmod.B_HEALTH for r in kb for b in r))

    def test_member_keyboard_matches_services(self):
        uid = self.make_member(54, services=["vpn"])
        texts = [b["text"] for r in self.h.keyboard(uid)["keyboard"] for b in r]
        self.assertIn(botmod.B_VPN, texts)
        self.assertNotIn(botmod.B_MX, texts)
        self.assertNotIn(botmod.B_HEALTH, texts)
        self.assertEqual(self.h.keyboard(uid)["keyboard"][0][0]["web_app"]["url"], "https://example.org/app/")

    def test_vpn_button_shows_sub_url(self):
        uid = self.make_member(55)
        self.mm.set_vpn_sub_id(uid, "abc123")
        self.h.on_message(msg(uid, botmod.B_VPN))
        e = self.b.sent(uid)[-1]
        self.assertIn("https://example.org:2096/sub/abc123", e[2])
        self.assertTrue(e[3]["protect_content"])
        self.assertEqual(e[3]["reply_markup"]["inline_keyboard"][0][0]["web_app"]["url"],
                         "https://example.org/app/?p=vpn")

    def test_vpn_without_xui_and_sub_id(self):
        uid = self.make_member(56)
        self.h.on_message(msg(uid, "/vpn"))
        self.assertIn("не настроен", self.texts(uid))

    def test_vpn_prepares_configs_first(self):
        uid = self.make_member(58)
        self.h.xui = FakeXUI([])
        self.h.rec.sync_member = lambda u: self.mm.set_vpn_sub_id(u, "newsub")
        self.h.on_message(msg(uid, "/vpn"))
        out = self.b.sent(uid)
        self.assertIn("Готовлю", out[0][2])
        self.assertTrue([e for e in self.b.log if e[0] == "delete" and e[1] == uid])
        self.assertIn("newsub", out[-1][2])

    def test_matrix_and_sub(self):
        uid = self.make_member(57)
        self.mm.add_matrix(uid, "@bob:example.org")
        self.h.on_message(msg(uid, botmod.B_MX))
        self.assertIn("@bob:example.org", self.b.sent(uid)[-1][2])
        self.h.on_message(msg(uid, botmod.B_SUB))
        self.assertIn("VPN, Мессенджер", self.b.sent(uid)[-1][2])

    # ---------------------------------------------------------------- owner --
    def test_owner_code_command(self):
        self.h.on_message(msg(OWNER, "/code 90 vpn"))
        codes = self.mm.codes()
        self.assertEqual(len(codes), 1)
        self.assertEqual((codes[0]["days"], codes[0]["services"]), (90, ["vpn"]))
        out = self.b.sent(OWNER)[-1]
        self.assertIn(f"<code>{codes[0]['code']}</code>", out[2])
        self.assertIn(f"https://t.me/TestBot?start={codes[0]['code']}", out[2])
        btns = [b for r in out[3]["reply_markup"]["inline_keyboard"] for b in r]
        self.assertTrue(any("t.me/share/url?url=" in b.get("url", "") for b in btns))
        self.assertIn(f"c:rv:{codes[0]['code']}", [b.get("callback_data") for b in btns])

    def test_owner_code_bad_args(self):
        self.h.on_message(msg(OWNER, "/code 30 wifi"))
        self.assertEqual(self.mm.codes(), [])
        self.assertIn("Формат", self.texts(OWNER))

    def test_owner_revoke_code(self):
        self.h.on_message(msg(OWNER, "/code"))
        code = self.mm.codes()[0]["code"]
        self.h.on_callback(cb(OWNER, f"c:rv:{code}"))
        self.assertEqual(self.mm.codes(), [])

    def test_wizard(self):
        self.h.on_message(msg(OWNER, botmod.B_CODE))
        mid = self.b.n
        self.h.on_callback(cb(OWNER, "c:t:vpn", mid))
        self.h.on_callback(cb(OWNER, "c:t:tools", mid))
        self.h.on_callback(cb(OWNER, "c:d:90", mid))
        edit = [e for e in self.b.log if e[0] == "edit"][-1]
        flat = {b["callback_data"]: b["text"] for r in edit[3]["reply_markup"]["inline_keyboard"] for b in r}
        self.assertTrue(flat["c:t:vpn"].startswith("⬜"))
        self.assertTrue(flat["c:t:matrix"].startswith("✅"))
        self.assertTrue(flat["c:t:tools"].startswith("✅"))
        self.assertTrue(flat["c:d:90"].startswith("✅"))
        self.assertTrue(all(len(k.encode()) <= 64 for k in flat))
        self.h.on_callback(cb(OWNER, "c:mk", mid))
        c = self.mm.codes()[0]
        self.assertEqual((c["days"], c["services"]), (90, ["matrix", "tools"]))
        self.assertIn(c["code"], [e for e in self.b.log if e[0] == "edit"][-1][2])
        # state is gone: pressing again reports it as expired
        self.h.on_callback(cb(OWNER, "c:mk", mid))
        self.assertEqual(len(self.mm.codes()), 1)
        self.assertTrue(self.b.answers()[-1][3]["alert"])

    def test_wizard_expires(self):
        self.h.on_message(msg(OWNER, botmod.B_CODE))
        mid = self.b.n
        self.h.wiz[mid]["ts"] -= 2 * 3600
        self.h.on_callback(cb(OWNER, "c:mk", mid))
        self.assertEqual(self.mm.codes(), [])

    def test_wizard_needs_a_service(self):
        self.h.on_message(msg(OWNER, botmod.B_CODE))
        mid = self.b.n
        self.h.on_callback(cb(OWNER, "c:t:vpn", mid))
        self.h.on_callback(cb(OWNER, "c:t:matrix", mid))
        self.h.on_callback(cb(OWNER, "c:mk", mid))
        self.assertEqual(self.mm.codes(), [])

    def test_members_list_and_card(self):
        for i in range(10):
            self.make_member(200 + i, first=f"M{i}", user=f"m{i}")
        self.mm.suspend(200)
        self.h.on_message(msg(OWNER, "/members"))
        out = self.b.sent(OWNER)[-1]
        rows = out[3]["reply_markup"]["inline_keyboard"]
        self.assertEqual(len([r for r in rows if r[0]["callback_data"].startswith("m:open:")]), 8)
        self.assertIn("m:pg:1", [b["callback_data"] for b in rows[-1]])
        self.assertIn("10", out[2])
        self.h.on_callback(cb(OWNER, "m:pg:1"))
        self.assertEqual(len([r for r in [e for e in self.b.log if e[0] == "edit"][-1][3]["reply_markup"]
                              ["inline_keyboard"] if r[0]["callback_data"].startswith("m:open:")]), 2)
        self.h.on_callback(cb(OWNER, "m:open:200"))
        card = [e for e in self.b.log if e[0] == "edit"][-1]
        self.assertIn("M0", card[2])
        datas = [b.get("callback_data") for r in card[3]["reply_markup"]["inline_keyboard"] for b in r]
        for want in ("m:ext:200:30", "m:ext:200:90", "m:res:200", "m:svc:200:vpn", "m:del:200", "m:pg:0"):
            self.assertIn(want, datas)
        self.assertTrue(all(len(d.encode()) <= 64 for d in datas if d))

    def test_card_extend_and_suspend_notify_member(self):
        uid = self.make_member(60)
        before = self.mm.get(uid)["expires_ts"]
        self.h.on_callback(cb(OWNER, f"m:ext:{uid}:30"))
        self.assertEqual(self.mm.get(uid)["expires_ts"], before + 30 * DAY)
        self.assertIn("Подписка продлена", self.texts(uid))
        self.h.on_callback(cb(OWNER, f"m:sus:{uid}"))
        self.assertEqual(self.mm.status(self.mm.get(uid)), "suspended")
        self.assertIn("приостановлен", self.b.sent(uid)[-1][2])
        edit = [e for e in self.b.log if e[0] == "edit"][-1]
        self.assertIn(f"m:res:{uid}", str(edit[3]["reply_markup"]))
        self.h.on_callback(cb(OWNER, f"m:res:{uid}"))
        self.assertEqual(self.mm.status(self.mm.get(uid)), "active")
        self.assertIn("восстановлен", self.b.sent(uid)[-1][2])

    def test_service_toggle_and_delete_flow(self):
        uid = self.make_member(61)
        self.h.on_callback(cb(OWNER, f"m:svc:{uid}:tools"))
        self.assertIn("tools", self.mm.get(uid)["services"])
        self.h.on_callback(cb(OWNER, f"m:svc:{uid}:vpn"))
        self.assertNotIn("vpn", self.mm.get(uid)["services"])
        self.h.on_callback(cb(OWNER, f"m:del:{uid}"))
        self.assertIsNotNone(self.mm.get(uid))                  # only asks
        self.h.on_callback(cb(OWNER, f"m:delok:{uid}"))
        self.assertIsNone(self.mm.get(uid))
        self.assertEqual(self.b.sent(uid), [])                  # nothing sent to the member

    def test_non_owner_cannot_use_member_buttons(self):
        uid = self.make_member(62)
        before = self.mm.get(uid)["expires_ts"]
        self.h.on_callback(cb(uid, f"m:ext:{uid}:30"))
        self.h.on_callback(cb(uid, f"pw:vpn"))
        self.h.on_callback(cb(uid, "c:mk"))
        self.h.on_callback(cb(uid, "inb:add:1"))
        self.h.on_callback(cb(uid, f"g:{uid}:30"))
        self.assertEqual(self.mm.get(uid)["expires_ts"], before)
        ans = self.b.answers()
        self.assertEqual(len(ans), 5)
        self.assertTrue(all(a[3]["alert"] and "владельц" in a[2] for a in ans))
        # a stranger is refused too, and still gets an answer
        self.h.on_callback(cb(999, "m:ext:62:30"))
        self.assertEqual(self.b.answers()[-1][2], "Нет доступа")

    def test_expiry_notice_buttons_work_from_any_message(self):
        uid = self.make_member(63)
        self.mm.set_expiry(uid, int(time.time()) - 1)
        self.h.on_callback(cb(OWNER, f"m:open:{uid}", mid=999))
        self.h.on_callback(cb(OWNER, f"m:ext:{uid}:30", mid=999))
        self.assertEqual(self.mm.status(self.mm.get(uid)), "active")

    def test_allow_and_deny(self):
        self.h.on_message(msg(OWNER, "/allow 300 10"))
        m = self.mm.get(300)
        self.assertAlmostEqual(m["expires_ts"], time.time() + 10 * DAY, delta=5)
        self.h.on_message(msg(OWNER, "/deny 300"))
        self.assertEqual(self.mm.status(self.mm.get(300)), "suspended")
        self.h.on_message(msg(OWNER, "/allow abc"))
        self.assertIn("Формат", self.b.sent(OWNER)[-1][2])

    def test_inbound_add_refuses_second_awg(self):
        self.h.xui = FakeXUI([{"id": 1, "remark": "a", "protocol": "amneziawg"},
                              {"id": 2, "remark": "b", "protocol": "wireguard"},
                              {"id": 3, "remark": "c", "protocol": "vless"}])
        self.mm.set_member_inbounds([1])
        self.h.on_callback(cb(OWNER, "inb:add:2"))
        self.assertEqual(self.mm.member_inbounds(), [1])
        self.assertIn("Нельзя", [e for e in self.b.log if e[0] == "edit"][-1][2])
        self.assertTrue(self.b.answers()[-1][3]["alert"])
        self.h.on_callback(cb(OWNER, "inb:add:3"))
        self.assertEqual(self.mm.member_inbounds(), [1, 3])
        self.assertIn(3, self.mm.known_inbounds())
        self.h.on_callback(cb(OWNER, "inb:skip:2"))
        self.assertEqual(self.mm.member_inbounds(), [1, 3])
        self.assertEqual([e for e in self.b.log if e[0] == "edit"][-1][2], "Пропущено.")

    def test_passwords_stay_owner_only(self):
        self.h.store.vault_set("immich", "me", "secret")
        self.h.on_message(msg(OWNER, "/pass"))
        self.assertIn("pw:immich", str(self.b.sent(OWNER)[-1][3]["reply_markup"]))
        with mock.patch.object(botmod.threading, "Timer"):
            self.h.on_callback(cb(OWNER, "pw:immich"))
        self.assertIn("secret", self.b.sent(OWNER)[-1][2])
        uid = self.make_member(64, services=["vpn", "tools"])
        self.h.on_callback(cb(uid, "pw:immich"))
        self.assertEqual(self.b.sent(uid), [])

    # ------------------------------------------------------------------ run --
    def test_commands_and_menu_button(self):
        self.h._commands()
        scopes = [e for e in self.b.log if e[0] == "setMyCommands"]
        self.assertEqual(len(scopes), 2)
        owner_cmds = {c["command"] for c in scopes[1][3]["commands"]}
        self.assertTrue({"code", "members", "pass", "setpass", "delpass", "vpn"} <= owner_cmds)
        self.assertNotIn("code", {c["command"] for c in scopes[0][3]["commands"]})
        self.h._menu_button()
        mb = [e for e in self.b.log if e[0] == "setChatMenuButton"][0][3]["menu_button"]
        self.assertEqual(mb["web_app"]["url"], "https://example.org/app/")

    def test_notify_swallows_errors(self):
        def boom(*a, **k):
            raise botmod.TelegramError("blocked")
        self.b.send = boom
        self.assertIsNone(self.h.notify(5, "x"))
        self.assertIsNone(self.h.notify_owner("x"))


if __name__ == "__main__":
    unittest.main()
