"""Extra admins (HELPER_ADMIN_IDS): the owner's controls on the ops bot and in the
Mini App, without the owner's personal logins (PRIVATE_VAULT) or the bot tokens."""
import os
import unittest
from unittest import mock

import test_bot as tb
import test_round2 as t2
import test_webapp as tw

ADMIN = 4242


class AdminBot(t2.TwoBots):
    def setUp(self):
        p = mock.patch.dict(os.environ, {"HELPER_ADMIN_IDS": f" {ADMIN}, x ",
                                         "VAULT_IMMICH_USER": "me", "VAULT_IMMICH_PASS": "p1",
                                         "VAULT_METUBE_USER": "dl", "VAULT_METUBE_PASS": "p2"})
        p.start()
        self.addCleanup(p.stop)
        super().setUp()

    def pw_buttons(self, uid):
        self.h.on_message(tb.msg(uid, "/pass"))
        last = self.hb.sent(uid)[-1]
        return [b["callback_data"] for r in last[3]["reply_markup"]["inline_keyboard"] for b in r]

    def test_admin_has_owner_role_on_ops_bot_only(self):
        self.assertEqual(self.h.admin_ids(), {ADMIN})
        self.assertEqual(self.h.role(ADMIN), "owner")
        self.assertEqual(self.h.role(tb.OWNER), "owner")
        self.assertIsNone(self.h.role(77))
        self.assertIsNone(self.front.role(ADMIN))            # member bot: membership as usual

    def test_admin_does_not_see_private_logins(self):
        self.assertIn("pw:immich", self.pw_buttons(tb.OWNER))
        btns = self.pw_buttons(ADMIN)
        self.assertIn("pw:metube", btns)
        self.assertNotIn("pw:immich", btns)

    def test_admin_cannot_open_or_change_private_login(self):
        self.h.on_callback(tb.cb(ADMIN, "pw:immich"))
        self.assertNotIn("p1", "\n".join(e[2] for e in self.hb.sent(ADMIN)))
        self.h.on_message(tb.msg(ADMIN, "/setpass immich x y"))
        self.h.on_message(tb.msg(ADMIN, "/delpass immich"))
        self.assertNotIn("immich", self.h.store.vault())
        self.h.on_callback(tb.cb(tb.OWNER, "pw:immich"))
        self.assertIn("p1", self.hb.sent(tb.OWNER)[-1][2])

    def test_failing_admin_chat_does_not_block_owner_and_retries_on_message(self):
        real = self.hb.call

        def call(method, **kw):
            if kw.get("chat_id") == ADMIN and not getattr(self, "started", False):
                raise tb.botmod.TelegramError("Bad Request: chat not found")
            return real(method, **kw)
        self.hb.call = call
        self.h._menu_button()
        menu = lambda: [c[3].get("chat_id") for c in self.hb.log if c[0] == "setChatMenuButton"]   # noqa: E731
        self.assertIn(tb.OWNER, menu())
        self.assertNotIn(ADMIN, menu())
        self.started = True                                  # the admin presses /start
        self.h.on_message(tb.msg(ADMIN, "/start"))
        self.assertIn(ADMIN, menu())
        n = len(self.hb.log)
        self.h.on_message(tb.msg(ADMIN, "/help"))
        self.assertFalse([c for c in self.hb.log[n:] if c[0] == "setChatMenuButton"])   # once only

    def test_admin_gets_app_menu_button(self):
        self.h._menu_button()
        chats = {c[3].get("chat_id") for c in self.hb.log if c[0] == "setChatMenuButton"}
        self.assertTrue({tb.OWNER, ADMIN} <= chats)


class AdminWeb(tw.Base):
    def setUp(self):
        super().setUp()
        self.h.admins = {ADMIN}

    def test_admin_is_owner_in_app_but_no_tokens(self):
        st, d = self.j("GET", "/api/me", headers=self.tgh(ADMIN))
        self.assertEqual((st, d["role"]), (200, "owner"))
        st, _ = self.j("GET", "/api/admin/members", headers=self.tgh(ADMIN))
        self.assertEqual(st, 200)
        st, _ = self.j("GET", "/api/admin/integrations", headers=self.tgh(ADMIN))
        self.assertEqual(st, 403)
        st, _ = self.j("POST", "/api/admin/integrations/crypto", {"token": "1:x"}, headers=self.tgh(ADMIN))
        self.assertEqual(st, 403)
        st, d = self.j("GET", "/api/admin/overview", headers=self.tgh(ADMIN))
        self.assertEqual((st, d["can_tokens"]), (200, False))

    def test_owner_keeps_tokens(self):
        for h in (self.tgh(tw.OWNER), self.admin()):
            st, d = self.j("GET", "/api/admin/overview", headers=h)
            self.assertEqual((st, d["can_tokens"]), (200, True))
            self.assertEqual(self.j("GET", "/api/admin/integrations", headers=h)[0], 200)


if __name__ == "__main__":
    unittest.main()
