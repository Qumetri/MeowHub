"""The long-poll loops must survive any error: a dead poller thread leaves its bot
silently deaf while the container still looks healthy (2026-10-11, member bot)."""
import os
import sys
import tempfile
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

try:
    import webapp                         # noqa: F401
except ImportError:
    stub = types.ModuleType("webapp")
    stub.start = lambda helper, port=None: None
    sys.modules["webapp"] = stub

import bot as botmod                      # noqa: E402
import tg                                 # noqa: E402
from store import Store                   # noqa: E402


class Stop(BaseException):
    pass


class PollTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(os.path.join(self.tmp.name, "h.db"))
        self.h = botmod.Helper(self.store)
        hb = os.path.join(self.tmp.name, "heartbeat")
        p = mock.patch.object(botmod, "HEARTBEAT", hb)
        p.start()
        self.addCleanup(p.stop)
        self.hb = hb
        s = mock.patch.object(botmod.time, "sleep", lambda _s: None)
        s.start()
        self.addCleanup(s.stop)

    def test_poll_survives_non_telegram_errors(self):
        calls = []

        def call(method, **kw):
            calls.append(method)
            if len(calls) == 1:
                raise TimeoutError("The read operation timed out")
            if len(calls) == 2:
                raise RuntimeError("boom")
            if len(calls) == 3:
                return [{"update_id": 41, "message": {}}]
            raise Stop

        front = types.SimpleNamespace(kind="member", bot=types.SimpleNamespace(call=call), _safe=lambda u: None)
        with self.assertRaises(Stop):
            self.h.poll(front, "offset_member")
        self.assertEqual(len(calls), 4)                     # kept polling after both errors
        self.assertEqual(self.store.get("offset_member"), "42")

    def test_heartbeat_is_the_oldest_poller(self):
        self.h._beats = {"helper": 1000}
        with mock.patch.object(botmod.time, "time", return_value=5000):
            self.h._beat("member")
        with open(self.hb) as f:
            self.assertEqual(f.read(), "1000")              # a stuck helper poller is not masked


class TgErrorsTest(unittest.TestCase):
    def test_read_timeout_becomes_telegram_error(self):
        b = tg.Bot("x")
        with mock.patch.object(tg.urllib.request, "urlopen", side_effect=TimeoutError("timed out")):
            with self.assertRaises(tg.TelegramError) as cm:
                b.call("getMe")
        self.assertTrue(str(cm.exception).startswith("network"))

    def test_reset_and_bad_json_become_telegram_error(self):
        b = tg.Bot("x")
        for exc in (ConnectionResetError(104, "reset"), ValueError("bad json")):
            with mock.patch.object(tg.urllib.request, "urlopen", side_effect=exc):
                with self.assertRaises(tg.TelegramError):
                    b.call("getMe")


if __name__ == "__main__":
    unittest.main()
