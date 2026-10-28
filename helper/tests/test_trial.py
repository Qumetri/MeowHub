"""Free trial for newcomers: the bot's first message offers it, once per person, capped per day."""
import os
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
sys.path.insert(0, os.path.dirname(__file__))

import members as mm                      # noqa: E402
from test_bot import OWNER, BotTest, cb, msg   # noqa: E402


class TrialTest(unittest.TestCase):
    setUp, make_member = BotTest.setUp, BotTest.make_member     # BotTest's harness, not its tests

    def wait_owner(self, n=1):
        for _ in range(50):                       # usage notices go out on a thread
            if len(self.b.sent(OWNER)) >= n:
                break
            time.sleep(0.01)
        return self.b.sent(OWNER)

    def test_start_offers_trial_button(self):
        self.h.on_message(msg(90, "/start", first="Zed"))
        out = self.b.sent(90)[0]
        self.assertIn("Попробуй бесплатно: 1 день", out[2])
        self.assertIn("@boss", out[2])
        self.assertIn("MEOW-XXXX-XXXX", out[2])
        rows = out[3]["reply_markup"]["inline_keyboard"]
        self.assertEqual(rows, [[{"text": "🎁 Попробовать бесплатно 1 день", "callback_data": "trial:go"}]])

    def test_button_starts_one_day_and_tells_owner(self):
        self.h.on_message(msg(91, "/start"))
        self.h.on_callback(cb(91, "trial:go"))
        m = self.mm.get(91)
        self.assertEqual(self.mm.status(m), "active")
        self.assertAlmostEqual(m["expires_ts"] - time.time(), mm.DAY, delta=60)
        self.assertEqual(m["services"], mm.DEFAULT_SERVICES)
        welcome = self.b.sent(91)[-1]
        self.assertIn("Добро пожаловать", welcome[2])
        self.assertIn("Пробный доступ до", welcome[2])
        self.assertIn("keyboard", welcome[3]["reply_markup"])          # the member keyboard is back
        self.assertTrue(any(e[0] == "editMessageReplyMarkup" for e in self.b.log))  # button removed
        notes = self.wait_owner(2)                                      # stranger notice + trial notice
        self.assertIn("пробный доступ на 1 день", notes[-1][2])
        self.assertIn("<code>91</code>", notes[-1][2])

    def test_once_per_person_even_after_delete(self):
        self.h.on_callback(cb(92, "trial:go"))
        self.assertEqual(self.mm.trial_state(92), "used")
        self.mm.delete(92)
        self.assertEqual(self.mm.trial_state(92), "used")
        self.h.on_callback(cb(92, "trial:go", mid=501))
        self.assertTrue(self.b.answers()[-1][3]["alert"])
        self.assertIn("уже использован", self.b.answers()[-1][2])
        self.assertIsNone(self.mm.get(92))

    def test_former_member_gets_no_offer(self):
        self.make_member(93, days=1)
        self.mm.delete(93)
        self.h.on_message(msg(93, "/start"))
        out = self.b.sent(93)[0]
        self.assertNotIn("inline_keyboard", out[3]["reply_markup"])
        self.assertNotIn("Попробуй бесплатно", out[2])

    def test_switch_off_and_daily_cap(self):
        self.mm.set_trial_enabled(False)
        self.assertEqual(self.mm.trial_state(94), "off")
        self.assertEqual(self.mm.start_trial(94), ("off", None))
        self.mm.set_trial_enabled(True)
        with mock.patch.object(mm, "TRIAL_PER_DAY", 2):
            self.assertEqual(self.mm.start_trial(95)[0], "new")
            self.assertEqual(self.mm.start_trial(96)[0], "new")
            self.assertEqual(self.mm.trial_state(97), "full")
            self.assertEqual(self.mm.start_trial(97), ("full", None))

    def test_services_view_toggles_trial(self):
        text, kb = self.h.services_view("ru")
        self.assertIn("✅ Пробный доступ новичкам", text)
        self.assertIn([{"text": "✅ 🎁 Пробный доступ новичкам — 1 день", "callback_data": "svc:trial"}],
                      kb["inline_keyboard"])
        self.h.on_callback(cb(OWNER, "svc:trial"))
        self.assertFalse(self.mm.trial_enabled())

    def test_expired_trial_says_trial_and_points_at_owner(self):
        self.h.on_callback(cb(98, "trial:go"))
        self.assertTrue(self.mm.on_trial(98))
        with self.mm.s.lock:                           # ran out (set_expiry would be the owner's change)
            self.mm.s.db.execute("UPDATE members SET expires_ts=? WHERE id=98", (int(time.time()) - 10,))
        self.h.on_message(msg(98, "/vpn"))
        self.assertIn("пробный период закончился", self.b.sent(98)[-1][2])
        self.mm.extend(98, 30)                         # the owner extends: no longer a trial
        self.assertFalse(self.mm.on_trial(98))

    def test_reminder_text_on_trial_expiry(self):
        self.h.on_callback(cb(99, "trial:go"))
        with self.mm.s.lock:
            self.mm.s.db.execute("UPDATE members SET expires_ts=? WHERE id=99", (int(time.time()) - 5,))
        sent = []
        self.h.rec.send = lambda uid, text, kb=None: sent.append((uid, text))
        self.h.rec.reminders()
        self.assertIn("Пробный период MeowHub закончился", dict(sent)[99])


if __name__ == "__main__":
    unittest.main()
