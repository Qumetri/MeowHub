"""Failed-SSH alerts: the bot reads scripts/ssh-watch.py's feed (ssh-guard.md)."""
import json
import os
import sys
import tempfile
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

try:
    import webapp                         # noqa: F401
except ImportError:
    stub = types.ModuleType("webapp")
    stub.start = lambda helper, port=None: None
    sys.modules["webapp"] = stub

import bot as botmod                      # noqa: E402
from store import Store                   # noqa: E402
from tests.test_bot import FakeBot, msg, cb, ENV, OWNER   # noqa: E402


class SshAlertTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = mock.patch.dict(os.environ, ENV)
        env.start()
        self.addCleanup(env.stop)
        self.feed = os.path.join(self.tmp.name, "ssh.jsonl")
        for name, value in (("SSH_FEED", self.feed),
                            ("SSH_REQUESTS", os.path.join(self.tmp.name, "req.jsonl"))):
            p = mock.patch.object(botmod, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.store = Store(os.path.join(self.tmp.name, "h.db"))
        self.h = botmod.Helper(self.store)
        self.h.bot = self.b = FakeBot()
        self.mon = botmod.Monitor(self.h)

    def put(self, *ips, ts=1000, logins=(), jails=None):
        rec = {"ts": ts, "ips": list(ips), "logins": list(logins)}
        if jails is not None:
            rec["jails"] = jails
        with open(self.feed, "a") as f:
            f.write(json.dumps(rec) + "\n")

    def alerts(self):
        return [e for e in self.b.sent(OWNER) if "SSH" in e[2]]

    def real(self, ip, n=1, users=("server",), banned=False):
        return {"ip": ip, "n": n, "users": list(users), "banned": banned, "junk": False}

    def junk(self, ip, n=1, users=("root",), banned=True):
        return {"ip": ip, "n": n, "users": list(users), "banned": banned, "junk": True}

    def test_junk_is_silent_real_guesses_alert_once_a_day(self):
        self.put(self.real("9.9.9.9", 50))
        self.mon.ssh_tick(10_000)                            # first run: starts at the end
        self.assertEqual(self.alerts(), [])
        self.put(self.junk("1.2.3.4", 7, ["root", "admin"]), self.junk("2.2.2.2", 3, ["ubuntu"]))
        self.mon.ssh_tick(10_060)
        self.assertEqual(self.alerts(), [])                  # root / made-up users: no message
        self.put(self.real("5.6.7.8", 4, ["server"], banned=False), self.junk("1.2.3.4"))
        self.mon.ssh_tick(10_120)
        a = self.alerts()
        self.assertEqual(len(a), 1)
        self.assertIn("<code>5.6.7.8</code>", a[0][2])
        self.assertNotIn("1.2.3.4", a[0][2])
        self.assertEqual(a[0][3]["reply_markup"]["inline_keyboard"][0][0]["callback_data"], "ssh:off")
        self.put(self.real("5.6.7.8", 3))
        self.mon.ssh_tick(10_180)
        self.assertEqual(len(self.alerts()), 1)              # same address, same day: quiet
        self.put(self.real("5.6.7.8", 3))
        self.mon.ssh_tick(10_120 + botmod.SSH_REPEAT)
        self.assertEqual(len(self.alerts()), 2)              # a day later: again

    def test_old_watcher_lines_without_junk_count_as_junk(self):
        self.put(ts=1)
        self.mon.ssh_tick(10_000)
        self.put({"ip": "4.4.4.4", "n": 2, "users": ["root"], "banned": False})
        self.mon.ssh_tick(10_060)
        self.assertEqual(self.alerts(), [])
        self.assertEqual(self.mon.ssh_day()["attempts"], 2)

    def test_new_login_alerts_with_ban_button_known_login_is_quiet(self):
        self.put(ts=1)
        self.mon.ssh_tick(10_000)
        self.put(logins=[{"ip": "37.1.1.1", "user": "server", "method": "password", "new": False},
                         {"ip": "8.8.4.4", "user": "server", "method": "password", "new": True}])
        self.mon.ssh_tick(10_060)
        a = self.alerts()
        self.assertEqual(len(a), 1)
        self.assertIn("8.8.4.4", a[0][2])
        self.assertNotIn("37.1.1.1", a[0][2])
        self.assertEqual(a[0][3]["reply_markup"]["inline_keyboard"][0][0]["callback_data"], "ssh:ban:8.8.4.4")

    def test_ban_button_writes_a_request(self):
        self.h.on_callback(cb(OWNER, "ssh:ban:8.8.4.4"))
        with open(botmod.SSH_REQUESTS) as f:
            self.assertEqual([json.loads(x) for x in f], [{"ban": "8.8.4.4"}])
        self.h.on_callback(cb(OWNER, "ssh:ban:not-an-ip"))
        with open(botmod.SSH_REQUESTS) as f:
            self.assertEqual(len(f.readlines()), 1)

    def test_unban_command_owner_only_and_validated(self):
        self.h.on_message(msg(OWNER, "/sshunban 1.2.3.4"))
        self.h.on_message(msg(OWNER, "/sshunban banana"))
        self.h.on_message(msg(77, "/sshunban 5.5.5.5"))     # a stranger can't
        with open(botmod.SSH_REQUESTS) as f:
            self.assertEqual([json.loads(x) for x in f], [{"unban": "1.2.3.4"}])
        self.assertTrue(any("/sshunban 1.2.3.4" in e[2] for e in self.b.sent(OWNER)))

    def test_daily_summary_counts_and_resets(self):
        self.put(ts=1)
        self.mon.ssh_tick(10_000)
        self.put(self.junk("1.1.1.1", 5, ["root"]), self.junk("2.2.2.2", 2, ["admin", "root"]),
                 logins=[{"ip": "37.1.1.1", "user": "server", "method": "password", "new": False}],
                 jails={"sshd": 1, "sshd-instant": 40})
        self.put(self.junk("1.1.1.1", 1, ["root"]), self.real("3.3.3.3", 2),
                 logins=[{"ip": "37.1.1.1", "user": "server", "method": "password", "new": False}])
        self.mon.ssh_tick(10_060)
        text = self.mon.ssh_summary("ru")
        self.assertIn("10 с 3 адресов", text)               # 5 + 2 + 1 + 2 attempts, 3 addresses
        self.assertIn("забанено: 2", text)
        self.assertIn("root", text.split("Чаще всего:")[1])
        self.assertIn("настоящие учётки: 2", text)
        self.assertIn("Сейчас в бане: 41", text)
        self.assertIn("<code>37.1.1.1</code> ×2", text)
        again = self.mon.ssh_summary("ru")
        self.assertIn("не было", again)                      # a new day starts empty
        self.assertIn("Сейчас в бане: 41", again)            # the jail count is kept

    def test_summary_is_part_of_the_daily_report_and_off_when_muted(self):
        self.store.set("ssh_alerts", "0")
        self.assertIsNone(self.mon.ssh_summary("ru"))

    def test_mute_by_button_and_command(self):
        self.put(ts=1)
        self.mon.ssh_tick(10_000)
        self.h.on_callback(cb(OWNER, "ssh:off"))
        self.assertFalse(self.h.ssh_alerts_on())
        self.put(self.real("4.4.4.4", 2), logins=[{"ip": "8.8.4.4", "user": "server", "method": "password", "new": True}])
        self.mon.ssh_tick(10_060)
        self.assertEqual(self.alerts(), [])
        self.h.on_message(msg(OWNER, "/sshalerts on"))
        self.assertTrue(self.h.ssh_alerts_on())
        self.h.on_message(msg(77, "/sshalerts off"))         # a stranger can't switch it
        self.assertTrue(self.h.ssh_alerts_on())

    def test_silent_watcher_warns_after_two_checks(self):
        self.put(ts=1)
        old = os.stat(self.feed).st_mtime
        self.mon.ssh_tick(old + 60)
        self.mon.ssh_tick(old + botmod.SSH_SILENT + 60)
        self.mon.ssh_tick(old + botmod.SSH_SILENT + 120)
        self.assertTrue(any("ssh-watch" in e[2] for e in self.b.sent(OWNER)))

    def test_no_feed_no_noise(self):
        self.mon.ssh_tick(10_000)
        self.assertEqual(self.b.sent(OWNER), [])


if __name__ == "__main__":
    unittest.main()
