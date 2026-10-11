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
        p = mock.patch.object(botmod, "SSH_FEED", self.feed)
        p.start()
        self.addCleanup(p.stop)
        self.store = Store(os.path.join(self.tmp.name, "h.db"))
        self.h = botmod.Helper(self.store)
        self.h.bot = self.b = FakeBot()
        self.mon = botmod.Monitor(self.h)

    def put(self, *ips, ts=1000):
        with open(self.feed, "a") as f:
            f.write(json.dumps({"ts": ts, "ips": list(ips)}) + "\n")

    def alerts(self):
        return [e for e in self.b.sent(OWNER) if "SSH" in e[2]]

    def test_history_is_skipped_then_new_addresses_alert_once_a_day(self):
        self.put({"ip": "9.9.9.9", "n": 50, "users": ["root"], "banned": True})
        self.mon.ssh_tick(10_000)                            # first run: starts at the end
        self.assertEqual(self.alerts(), [])
        self.put({"ip": "1.2.3.4", "n": 7, "users": ["root", "admin"], "banned": True},
                 {"ip": "5.6.7.8", "n": 1, "users": ["ubuntu"], "banned": False})
        self.mon.ssh_tick(10_060)
        a = self.alerts()
        self.assertEqual(len(a), 1)                          # one batched message
        self.assertIn("<code>1.2.3.4</code>", a[0][2])
        self.assertIn("root, admin", a[0][2])
        self.assertIn("бан", a[0][2])
        self.assertIn("5.6.7.8", a[0][2])
        self.assertEqual(a[0][3]["reply_markup"]["inline_keyboard"][0][0]["callback_data"], "ssh:off")
        self.put({"ip": "1.2.3.4", "n": 3, "users": ["root"], "banned": True})
        self.mon.ssh_tick(10_120)
        self.assertEqual(len(self.alerts()), 1)              # same address, same day: quiet
        self.put({"ip": "1.2.3.4", "n": 3, "users": ["root"], "banned": True})
        self.mon.ssh_tick(10_060 + botmod.SSH_REPEAT)
        self.assertEqual(len(self.alerts()), 2)              # a day later: again

    def test_rotation_reads_the_new_file_from_the_start(self):
        self.put(ts=1)
        self.mon.ssh_tick(10_000)
        os.replace(self.feed, self.feed + ".1")
        self.put({"ip": "4.4.4.4", "n": 2, "users": [], "banned": False})
        self.mon.ssh_tick(10_060)
        self.assertIn("4.4.4.4", self.alerts()[0][2])

    def test_mute_by_button_and_command(self):
        self.put(ts=1)
        self.mon.ssh_tick(10_000)
        self.h.on_callback(cb(OWNER, "ssh:off"))
        self.assertFalse(self.h.ssh_alerts_on())
        self.put({"ip": "4.4.4.4", "n": 2, "users": [], "banned": False})
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
