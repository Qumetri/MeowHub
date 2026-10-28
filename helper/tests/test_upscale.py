"""Photo upscaling (service `upscale`): limits, ownership, listing against a fake upscaler."""
import io
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import upscale as upm                     # noqa: E402
from store import Store                   # noqa: E402

OWNER = 1


class FakeEngine:
    def __init__(self):
        self.jobs = {}
        self.n = 0
        self.calls = []

    def upload(self, name, stream, length, query, owner_id, owner_name):
        stream.read(length)
        self.n += 1
        jid = f"{self.n:016x}"
        self.jobs[jid] = {"id": jid, "name": os.path.splitext(name)[0], "status": "queued", "progress": 0,
                          "scale": query["scale"], "scale_asked": query["scale"], "format": "jpg", "in_w": 100,
                          "in_h": 80, "out_w": 400, "out_h": 320, "model": query["model"]}
        return self.jobs[jid]

    def call(self, method, path):
        self.calls.append((method, path))
        if path.startswith("/api/jobs?ids="):
            ids = path.split("=", 1)[1].split(",")
            return {"jobs": [j for k, j in self.jobs.items() if k in ids],
                    "queue": {"current": None, "queue": [{"id": k, "est": 5} for k, j in self.jobs.items()
                                                         if j["status"] == "queued"]}}
        if path == "/api/queue":
            return {"current": None, "queue": []}
        jid = path.split("/")[3]
        if method == "DELETE":
            self.jobs.pop(jid, None)
        elif path.endswith("/cancel"):
            self.jobs[jid]["status"] = "cancelled"
        return {}


class UpscaleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.st = Store(os.path.join(self.tmp.name, "h.db"))
        self.eng = FakeEngine()
        self.u = upm.Upscale(self.st, lambda: OWNER, client=self.eng, root=self.tmp.name)
        os.environ.update(UPSCALE_PER_DAY="3", UPSCALE_ACTIVE="2", UPSCALE_MAX_MB="1")

    def tearDown(self):
        for k in ("UPSCALE_PER_DAY", "UPSCALE_ACTIVE", "UPSCALE_MAX_MB"):
            os.environ.pop(k, None)
        self.tmp.cleanup()

    def send(self, uid, name="a.jpg", preset="photo", size=10):
        return self.u.upload(uid, name, io.BytesIO(b"x" * size), size, preset, 4, "auto")

    def test_upload_and_list(self):
        rid = self.send(5)
        d = self.u.list_for(5)
        self.assertEqual([j["id"] for j in d["jobs"]], [rid])
        self.assertEqual(d["jobs"][0]["preset"], "photo")
        self.assertEqual(d["jobs"][0]["wait"], {"ahead": 0, "ready_in": 5})
        self.assertEqual(self.eng.jobs[f"{1:016x}"]["model"], "upscayl-standard-4x")
        self.assertEqual(self.u.list_for(6)["jobs"], [])          # someone else's

    def test_rejects(self):
        with self.assertRaises(upm.UpError) as e:
            self.send(5, name="a.pdf")
        self.assertEqual(e.exception.code, "bad_type")
        with self.assertRaises(upm.UpError) as e:
            self.send(5, preset="nope")
        self.assertEqual(e.exception.code, "bad_preset")
        with self.assertRaises(upm.UpError) as e:
            self.send(5, size=2 * 1024 * 1024)
        self.assertEqual(e.exception.code, "too_large")

    def test_active_and_daily_limit(self):
        self.send(5)
        self.send(5)
        with self.assertRaises(upm.UpError) as e:
            self.send(5)
        self.assertEqual(e.exception.code, "busy")
        for j in self.eng.jobs.values():
            j["status"] = "done"
        self.u.list_for(5)                                         # mirrors the status
        self.send(5)
        for j in self.eng.jobs.values():
            j["status"] = "done"
        self.u.list_for(5)
        with self.assertRaises(upm.UpError) as e:
            self.send(5)
        self.assertEqual(e.exception.code, "limit")
        q = self.u.quota(5)
        self.assertEqual((q["used"], q["left"]), (3, 0))
        self.assertGreater(q["next_in"], 0)
        for _ in range(4):                                         # the owner is exempt
            self.send(OWNER)

    def test_cancelled_does_not_count(self):
        rid = self.send(5)
        self.u.cancel(5, rid)
        self.assertEqual(self.u.used(5), 0)

    def test_expired_on_engine_disappears(self):
        rid = self.send(5)
        self.eng.jobs.clear()
        self.assertEqual(self.u.list_for(5)["jobs"], [])
        with self.assertRaises(upm.UpError):
            self.u.delete(5, rid)

    def test_result_path_owner_only(self):
        rid = self.send(5)
        jid = f"{1:016x}"
        os.makedirs(os.path.join(self.tmp.name, jid))
        with open(os.path.join(self.tmp.name, jid, "meta.json"), "w") as f:
            json.dump({"status": "done", "format": "png", "name": "a/b", "scale": 4}, f)
        path, name = self.u.result_path(5, rid)
        self.assertEqual(name, "a b-x4.png")
        self.assertTrue(path.endswith(jid + "/out.png"))
        with self.assertRaises(upm.UpError):
            self.u.result_path(6, rid)
        self.u.result_path(OWNER, rid)                             # the owner may


if __name__ == "__main__":
    unittest.main()
