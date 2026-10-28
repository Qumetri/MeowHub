"""Usage notices (usage.py): recipients, per-person switches, the actor and the owner left out."""
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import usage as um                        # noqa: E402
from members import Members               # noqa: E402
from store import Store                   # noqa: E402

OWNER, ADMIN, MEMBER = 1, 2, 3


class UsageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.st = Store(os.path.join(self.tmp.name, "h.db"))
        self.sent = []
        self.u = um.Usage(self.st, Members(self.st), lambda: OWNER, lambda: {ADMIN},
                          send=lambda chat, text: self.sent.append((chat, text)))

    def tearDown(self):
        self.tmp.cleanup()

    def report(self, uid, svc="upscale"):
        self.sent.clear()
        self.u.report(svc, uid, lambda lang, who: f"{lang}:{who}", user={"first_name": "Ann", "username": "ann"})
        for _ in range(50):                       # sent on a thread
            time.sleep(0.01)
            if self.sent:
                break
        time.sleep(0.02)
        return sorted(c for c, _ in self.sent)

    def test_member_action_goes_to_owner_and_admin(self):
        self.assertEqual(self.report(MEMBER), [OWNER, ADMIN])
        self.assertIn("<b>Ann</b> (@ann, <code>3</code>)", self.sent[0][1])

    def test_actor_and_owner_left_out(self):
        self.assertEqual(self.report(ADMIN), [OWNER])
        self.assertEqual(self.report(OWNER), [])

    def test_switches_are_per_person_and_service(self):
        self.assertFalse(self.u.toggle(ADMIN, "upscale"))
        self.assertEqual(self.report(MEMBER), [OWNER])
        self.assertEqual(self.report(MEMBER, "books"), [OWNER, ADMIN])
        self.assertTrue(self.u.toggle(ADMIN, "upscale"))
        self.assertEqual(self.report(MEMBER), [OWNER, ADMIN])
        with self.assertRaises(ValueError):
            self.u.toggle(ADMIN, "nope")

    def test_bad_make_never_raises(self):
        self.u.report("upscale", MEMBER, lambda lang, who: 1 / 0)
        time.sleep(0.05)


if __name__ == "__main__":
    unittest.main()
