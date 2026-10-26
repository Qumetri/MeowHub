"""Audiobooks (service `books`): the limit, the queue estimate, the chapter pick, voices,
the ready/failed notices — against a fake book2audio client."""
import io
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import books as bkm                       # noqa: E402
import members as mm                      # noqa: E402
from store import Store                   # noqa: E402

OWNER = 1


class FakeB2A:
    def __init__(self):
        self.lib = {}
        self.queue = {"current": None, "queue": []}
        self.calls = []
        self.n = 0

    def upload(self, name, stream, length, owner_id, owner_name):
        stream.read(length)
        self.n += 1
        slug = f"book{self.n}"
        self.lib[slug] = {"slug": slug, "status": "ready", "title": "Clean Architecture", "author": "R. C. Martin",
                          "language": "en", "chars_total": 470000, "chapters_total": 3}
        return {"slug": slug}

    def call(self, method, path, body=None, raw=False, timeout=None):
        self.calls.append((method, path, body))
        if path == "/api/voices":
            return {"engines": {"kokoro": {"available": True}, "silero": {"available": False}},
                    "voices": [{"engine": "kokoro", "id": "af_heart", "lang": "en", "name": "Heart", "gender": "f",
                                "accent": "🇺🇸"},
                               {"engine": "kokoro", "id": "bf_emma", "lang": "en", "name": "Emma", "gender": "f",
                                "accent": "🇬🇧"},
                               {"engine": "silero", "id": "xenia", "lang": "ru", "name": "Ксения", "gender": "f"}]}
        if path == "/api/books":
            return {"books": list(self.lib.values())}
        if path == "/api/queue":
            return self.queue
        slug = path.split("/")[3]
        if method == "GET":
            return dict(self.lib[slug], chapters=[
                {"index": 0, "title": "Clean Architecture", "chars": 2200},
                {"index": 1, "title": "Foreword", "chars": 6700},
                {"index": 2, "title": "1 What is design", "chars": 20000}])
        if path.endswith("/narrate"):
            self.lib[slug]["status"] = "queued"
        if method == "DELETE":
            del self.lib[slug]
        return {}


class BooksBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(os.path.join(self.tmp.name, "h.db"))
        self.members = mm.Members(self.store)
        self.sent = []
        self.owner_sent = []
        self.b2a = FakeB2A()
        self.b = bkm.Books(self.store, self.members, lambda: OWNER,
                           notify=lambda uid, make: self.sent.append((uid, make("ru"))), client=self.b2a,
                           root=self.tmp.name, notify_owner=lambda make: self.owner_sent.append(make("ru")))
        self.b.ensure_watcher = lambda: None             # no background thread in tests

    def tearDown(self):
        self.tmp.cleanup()

    def up(self, uid=2):
        return self.b.upload(uid, "clean.epub", io.BytesIO(b"x" * 10), 10, "Bob")


class TestLimits(BooksBase):
    def test_three_books_then_limit(self):
        for _ in range(3):
            bid = self.up()
            self.b.refresh()
            self.b.narrate(2, bid, "kokoro:af_heart")
        with self.assertRaises(bkm.BooksError) as e:
            self.up()
        self.assertEqual(e.exception.code, "limit")

    def test_done_and_deleted_still_counts_failed_does_not(self):
        bid = self.up()
        self.b.refresh()
        self.b.narrate(2, bid, "kokoro:af_heart")
        self.b._upd(bid, status="done")
        self.b.delete(2, bid)
        self.assertEqual(self.b.used(2), 1)
        bid2 = self.up()
        self.b._upd(bid2, status="error", queued_ts=1)
        self.assertEqual(self.b.used(2), 1)

    def test_pending_uploads_capped(self):
        self.up()
        self.up()
        with self.assertRaises(bkm.BooksError) as e:
            self.up()
        self.assertEqual(e.exception.code, "pending")

    def test_owner_exempt(self):
        for _ in range(4):
            bid = self.up(OWNER)
            self.b.refresh()
            self.b.narrate(OWNER, bid, "kokoro:af_heart")
        self.assertTrue(self.b.quota(OWNER)["unlimited"])

    def test_bad_type_and_size(self):
        with self.assertRaises(bkm.BooksError) as e:
            self.b.upload(2, "x.docx", io.BytesIO(b"x"), 1)
        self.assertEqual(e.exception.code, "bad_type")
        with self.assertRaises(bkm.BooksError) as e:
            self.b.upload(2, "x.pdf", io.BytesIO(b""), 101 * 1024 * 1024)
        self.assertEqual(e.exception.code, "too_large")

    def test_other_users_book_is_404(self):
        bid = self.up(2)
        with self.assertRaises(bkm.BooksError):
            self.b.narrate(3, bid, "kokoro:af_heart")


class TestNarrate(BooksBase):
    def test_title_page_skipped_and_voice_checked(self):
        bid = self.up()
        self.b.refresh()
        with self.assertRaises(bkm.BooksError):
            self.b.narrate(2, bid, "silero:xenia")           # Russian voice on an English book
        self.b.narrate(2, bid, "kokoro:af_heart")
        body = next(c[2] for c in self.b2a.calls if c[1].endswith("/narrate"))
        self.assertEqual(body["chapters"], [1, 2])
        self.assertEqual(body["voice"], "af_heart")

    def test_voices_only_installed_curated(self):
        keys = [v["key"] for v in self.b.voices("en")]
        self.assertEqual(keys, ["kokoro:af_heart", "kokoro:bf_emma"])
        self.assertEqual(self.b.voices("ru"), [])              # silero not available in the fake
        self.assertEqual(self.b.voices("en")[1]["accent"], "gb")


class TestQueue(BooksBase):
    def test_wait_info(self):
        q = {"current": {"slug": "a", "eta": 300},
             "queue": [{"slug": "b", "kind": "narrate", "est": 700}, {"slug": "c", "kind": "prepare", "est": 15},
                       {"slug": "d", "kind": "narrate", "est": 500}]}
        self.assertEqual(bkm.Books.wait_info("b", q), {"ahead": 1, "starts_in": 300, "takes": 700})
        self.assertEqual(bkm.Books.wait_info("d", q), {"ahead": 2, "starts_in": 1015, "takes": 500})
        self.assertIsNone(bkm.Books.wait_info("zz", q))
        self.assertEqual(bkm.Books.wait_info("b", {"current": None, "queue": q["queue"]})["starts_in"], 0)


class TestOwnerNotice(BooksBase):
    def test_member_queue_tells_owner_once(self):
        self.b2a.queue = {"current": {"slug": "x", "eta": 600}, "queue": [{"slug": "book1", "kind": "narrate", "est": 700}]}
        bid = self.up()
        self.b.refresh()
        self.b.narrate(2, bid, "kokoro:af_heart", who="<b>Bob</b>")
        self.assertEqual(len(self.owner_sent), 1)
        msg = self.owner_sent[0]
        for part in ("<b>Bob</b>", "Clean Architecture", "Heart", "глав: 2", "очереди: 1", "22 мин", "1 из 3"):
            self.assertIn(part, msg)

    def test_owner_own_book_is_silent(self):
        bid = self.up(OWNER)
        self.b.refresh()
        self.b.narrate(OWNER, bid, "kokoro:af_heart")
        self.assertEqual(self.owner_sent, [])

    def test_title_is_escaped(self):
        bid = self.up()
        self.b.refresh()
        self.b2a.lib["book1"]["title"] = "A <b> & C"
        self.b._upd(bid, title="A <b> & C")
        self.b.narrate(2, bid, "kokoro:af_heart", who="X")
        self.assertIn("A &lt;b&gt; &amp; C", self.owner_sent[0])


class TestNotices(BooksBase):
    def test_ready_notice_once(self):
        bid = self.up()
        self.b.refresh()
        self.b.narrate(2, bid, "kokoro:af_heart")
        self.b2a.lib["book1"].update(status="done", audio={"seconds": 3 * 3600 + 1800, "size": 10})
        rows, _ = self.b.refresh()
        for r in rows:
            self.b._announce(r)
        rows, _ = self.b.refresh()
        for r in rows:
            self.b._announce(r)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sent[0][0], 2)
        self.assertIn("3 ч 30 мин", self.sent[0][1])

    def test_upload_without_narration_is_silent(self):
        self.up()
        rows, _ = self.b.refresh()
        for r in rows:
            self.b._announce(r)
        self.assertEqual(self.sent, [])


if __name__ == "__main__":
    unittest.main()
