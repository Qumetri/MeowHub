import os
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import members as mm                      # noqa: E402
from store import Store                   # noqa: E402

DAY = 86400
FRM = {"username": "bob", "first_name": "Bob", "last_name": "", "language_code": "ru"}


class FakeXUI:
    def __init__(self, inbounds=None):
        self._inbounds = [dict(i) for i in inbounds] if inbounds is not None else []   # copy: tests mutate it
        self.store = {}                   # email -> client record (with inboundIds)
        self.calls = []

    def inbounds(self):
        self.calls.append(("inbounds",))
        return [dict(i) for i in self._inbounds]

    def inbound_set_remark(self, inbound_id, remark):
        self.calls.append(("inbound_set_remark", inbound_id, remark))
        for i in self._inbounds:
            if i["id"] == inbound_id:
                i["remark"] = remark

    def clients(self):
        self.calls.append(("clients",))
        return [dict(c) for c in self.store.values()]

    def client_get(self, email):
        c = self.store.get(email)
        return dict(c) if c else None

    def client_add(self, client, inbound_ids):
        self.calls.append(("add", client["email"]))
        self.store[client["email"]] = dict(client, inboundIds=list(inbound_ids))
        self.added = (dict(client), list(inbound_ids))

    def client_update(self, email, client):
        self.calls.append(("update", email))
        self.store[email] = dict(client)

    def client_attach(self, email, ids):
        self.calls.append(("attach", email, tuple(ids)))
        self.store[email]["inboundIds"] = sorted(set(self.store[email]["inboundIds"]) | set(ids))

    def client_detach(self, email, ids):
        self.calls.append(("detach", email, tuple(ids)))
        self.store[email]["inboundIds"] = [i for i in self.store[email]["inboundIds"] if i not in ids]

    def client_delete(self, email):
        self.calls.append(("delete", email))
        del self.store[email]

    def names(self, kind):
        return [c for c in self.calls if c[0] == kind]


class FakeSyn:
    def __init__(self):
        self.calls = []

    def set_locked(self, mxid, locked):
        self.calls.append((mxid, locked))


INBOUNDS = [
    {"id": 1, "remark": "Speed", "protocol": "vless", "port": 443, "enable": True},
    {"id": 2, "remark": "🧪 lab", "protocol": "vless", "port": 444, "enable": True},
    {"id": 3, "remark": "awg", "protocol": "wireguard", "port": 51820, "enable": True},
    {"id": 4, "remark": "off", "protocol": "vmess", "port": 445, "enable": False},
    {"id": 5, "remark": "Resistance", "protocol": "vless", "port": 59200, "enable": True},
]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(os.path.join(self.tmp.name, "h.db"))
        self.m = mm.Members(self.store)

    def tearDown(self):
        self.tmp.cleanup()


class MembersTest(Base):
    def test_code_format_and_redeem_new(self):
        code = self.m.new_code()
        self.assertRegex(code, r"^MEOW-[A-HJ-NP-Z2-9]{4}-[A-HJ-NP-Z2-9]{4}$")
        t0 = time.time()
        res, mem = self.m.redeem(7, FRM, code)
        self.assertEqual(res, "new")
        self.assertAlmostEqual(mem["expires_ts"], t0 + 30 * DAY, delta=5)
        self.assertEqual(mem["services"], ["vpn", "matrix"])
        self.assertEqual((mem["username"], mem["lang"]), ("bob", "ru"))
        self.assertTrue(self.m.changed.is_set())
        self.assertEqual(self.m.events(7)[0]["kind"], "redeem")
        self.assertIn(code, self.m.events(7)[0]["detail"])
        self.assertIn("+30d", self.m.events(7)[0]["detail"])

    def test_normalisation(self):
        code = self.m.new_code()
        messy = "  meow " + code[5:9].lower() + " " + code[10:].lower() + " "
        self.assertEqual(self.m.redeem(1, FRM, messy)[0], "new")
        code2 = self.m.new_code()
        self.assertEqual(self.m.redeem(2, FRM, code2.replace("-", "").lower())[0], "new")

    def test_extend_math_and_union(self):
        self.m.redeem(7, FRM, self.m.new_code(days=10, services=["vpn"]))
        before = self.m.get(7)["expires_ts"]
        res, mem = self.m.redeem(7, FRM, self.m.new_code(days=5, services=["matrix", "tools"]))
        self.assertEqual(res, "extended")
        self.assertAlmostEqual(mem["expires_ts"], before + 5 * DAY, delta=2)      # active: from expiry
        self.assertEqual(mem["services"], ["vpn", "matrix", "tools"])
        # expired member extends from now
        self.m.set_expiry(7, int(time.time()) - 100)
        self.m.set_reminded(7, -1)
        t0 = time.time()
        res, mem = self.m.redeem(7, FRM, self.m.new_code(days=3))
        self.assertAlmostEqual(mem["expires_ts"], t0 + 3 * DAY, delta=5)
        self.assertEqual(mem["reminded"], 0)

    def test_single_use(self):
        code = self.m.new_code()
        self.assertEqual(self.m.redeem(1, FRM, code)[0], "new")
        self.assertEqual(self.m.redeem(2, FRM, code)[0], "used")
        self.assertIsNone(self.m.get(2))
        self.assertEqual(len(self.m.codes()), 0)
        self.assertEqual(self.m.codes(include_dead=True)[0]["state"], "used")

    def test_multi_use(self):
        code = self.m.new_code(uses_max=2)
        self.assertEqual(self.m.redeem(1, FRM, code)[0], "new")
        self.assertEqual(self.m.redeem(2, FRM, code)[0], "new")
        self.assertEqual(self.m.redeem(3, FRM, code)[0], "used")

    def test_revoked_and_expired_codes(self):
        code = self.m.new_code()
        self.assertTrue(self.m.revoke_code(code.lower()))
        self.assertEqual(self.m.redeem(1, FRM, code)[0], "revoked_code")
        self.assertEqual(self.m.codes(include_dead=True)[0]["state"], "revoked")
        self.assertFalse(self.m.revoke_code("MEOW-ZZZZ-ZZZZ"))
        old = self.m.new_code()
        self.store.q("UPDATE codes SET valid_until=? WHERE code=?", int(time.time()) - 1, old)
        self.assertEqual(self.m.redeem(1, FRM, old)[0], "expired_code")
        self.assertEqual(self.m.codes(include_dead=True)[0]["state"], "expired")
        self.assertEqual(self.m.redeem(1, FRM, "nonsense")[0], "invalid")
        self.assertEqual(self.m.redeem(1, FRM, "")[0], "invalid")

    def test_codes_newest_first(self):
        a = self.m.new_code()
        self.store.q("UPDATE codes SET created_ts=created_ts-100 WHERE code=?", a)
        b = self.m.new_code()
        self.assertEqual([c["code"] for c in self.m.codes()], [b, a])

    def test_rate_limit(self):
        for _ in range(5):
            self.assertEqual(self.m.redeem(9, FRM, "MEOW-AAAA-BBBB")[0], "invalid")
        good = self.m.new_code()
        self.assertEqual(self.m.redeem(9, FRM, good), ("rate_limited", None))
        self.assertIsNone(self.m.get(9))
        # other users are not affected, and the code was not burned
        self.assertEqual(self.m.redeem(10, FRM, good)[0], "new")

    def test_suspended_cannot_redeem(self):
        self.m.redeem(7, FRM, self.m.new_code())
        self.m.suspend(7)
        code = self.m.new_code()
        self.assertEqual(self.m.redeem(7, FRM, code), ("suspended", None))
        self.assertEqual(self.m.codes()[0]["uses"], 0)           # not consumed
        # suspended attempts do not count toward the rate limit
        for _ in range(6):
            self.assertEqual(self.m.redeem(7, FRM, code)[0], "suspended")

    def test_status_and_has(self):
        self.m.redeem(7, FRM, self.m.new_code(services=["vpn"]))
        m = self.m.get(7)
        self.assertEqual(self.m.status(m), "active")
        self.assertTrue(self.m.has(7, "vpn"))
        self.assertFalse(self.m.has(7, "matrix"))
        self.assertFalse(self.m.has(99, "vpn"))
        self.m.suspend(7)
        self.assertEqual(self.m.status(self.m.get(7)), "suspended")
        self.assertFalse(self.m.has(7, "vpn"))
        self.m.resume(7)
        self.assertTrue(self.m.has(7, "vpn"))
        self.m.set_expiry(7, int(time.time()) - 1)
        self.assertEqual(self.m.status(self.m.get(7)), "expired")
        self.assertFalse(self.m.has(7, "vpn"))
        self.m.suspend(7)                                          # suspended beats expired
        self.assertEqual(self.m.status(self.m.get(7)), "suspended")
        self.m.resume(7)
        self.m.set_expiry(7, None)
        self.assertEqual(self.m.status(self.m.get(7)), "active")
        self.assertIsNone(self.m.view(self.m.get(7))["days_left"])

    def test_view(self):
        self.m.redeem(7, FRM, self.m.new_code(days=10))
        self.m.add_matrix(7, "@bob:x.org")
        v = self.m.view(self.m.get(7))
        self.assertEqual(v["status"], "active")
        self.assertEqual(v["days_left"], 10)
        self.assertEqual(v["vpn_email"], "mh-7")
        self.assertEqual(v["matrix"], ["@bob:x.org"])
        self.assertFalse(v["has_vpn_client"])
        self.m.set_expiry(7, int(time.time()) - 5)
        self.assertEqual(self.m.view(self.m.get(7))["days_left"], 0)

    def test_grant_extend_services_note_delete(self):
        mem = self.m.grant(5, {"first_name": "Eve"}, days=7)
        self.assertEqual(mem["services"], ["vpn", "matrix"])
        e0 = mem["expires_ts"]
        self.m.extend(5, 3)
        self.assertAlmostEqual(self.m.get(5)["expires_ts"], e0 + 3 * DAY, delta=2)
        self.m.set_services(5, ["tools", "bogus", "vpn"])
        self.assertEqual(self.m.get(5)["services"], ["vpn", "tools"])
        self.m.set_note(5, "friend")
        self.assertEqual(self.m.get(5)["note"], "friend")
        self.m.add_matrix(5, "@eve:x.org")
        self.m.delete(5)
        self.assertIsNone(self.m.get(5))
        self.assertEqual(self.m.matrix_accounts(5)[0]["mxid"], "@eve:x.org")   # kept
        kinds = {e["kind"] for e in self.m.events(5)}
        self.assertTrue({"grant", "extend", "services", "delete", "matrix_create"} <= kinds)

    def test_touch(self):
        self.m.touch(1, FRM)                                       # unknown uid: no-op
        self.assertEqual(self.store.q("SELECT COUNT(*) c FROM activity")[0]["c"], 0)
        self.m.redeem(1, FRM, self.m.new_code())
        self.m.touch(1, {"username": "", "first_name": "Robert"})
        mem = self.m.get(1)
        self.assertEqual((mem["username"], mem["first_name"]), ("bob", "Robert"))   # empty ignored
        self.m.touch(1, None, "callback")
        self.m.touch(1, None, "app")
        today = self.m.activity(1)[0]
        self.assertEqual((today["messages"], today["app"], today["users"]), (2, 1, 1))

    def test_activity_zero_filled(self):
        self.m.redeem(1, FRM, self.m.new_code())
        self.m.redeem(2, FRM, self.m.new_code())
        self.m.touch(1)
        self.m.touch(2)
        rows = self.m.activity(30)
        self.assertEqual(len(rows), 30)
        self.assertEqual([r["day"] for r in rows], sorted(r["day"] for r in rows))
        self.assertEqual(rows[-1]["day"], time.strftime("%Y-%m-%d"))
        self.assertEqual(rows[-1]["users"], 2)
        self.assertTrue(all(r["messages"] == 0 for r in rows[:-1]))

    def test_counts(self):
        for uid in (1, 2, 3, 4):
            self.m.grant(uid, FRM, days=30)
        self.m.set_expiry(2, int(time.time()) + 3 * DAY)
        self.m.set_expiry(3, int(time.time()) - 5)
        self.m.suspend(4)
        self.assertEqual(self.m.counts(), {"members": 4, "active": 2, "expired": 1,
                                           "suspended": 1, "expiring_7d": 1})

    def test_list_newest_first_and_inbound_settings(self):
        self.m.grant(1, FRM)
        self.store.q("UPDATE members SET created_ts=created_ts-50 WHERE id=1")
        self.m.grant(2, FRM)
        self.assertEqual([x["id"] for x in self.m.list()], [2, 1])
        self.assertIsNone(self.m.member_inbounds())
        self.assertEqual(self.m.known_inbounds(), [])
        self.m.set_member_inbounds([1, 5])
        self.m.set_known_inbounds([1, 5, 2])
        self.assertEqual(self.m.member_inbounds(), [1, 5])
        self.assertEqual(self.m.known_inbounds(), [1, 2, 5])
        self.m.set_member_inbounds([])
        self.assertEqual(self.m.member_inbounds(), [])


class ReconcilerTest(Base):
    def setUp(self):
        super().setUp()
        self.xui = FakeXUI(INBOUNDS)
        self.syn = FakeSyn()
        self.sent = []
        self.owner = 1000
        self.rec = mm.Reconciler(self.m, self.xui, self.syn,
                                 lambda chat, text, markup=None: self.sent.append((chat, text, markup)),
                                 lambda: self.owner)

    def test_defaults_computed_and_known_saved(self):
        self.rec.sync_all()
        self.assertEqual(self.m.member_inbounds(), [1, 5])
        self.assertEqual(self.m.known_inbounds(), [1, 2, 3, 4, 5])
        self.rec.detect_new_inbounds()
        self.assertEqual(self.sent, [])                            # first run: no spam
        self.assertTrue(self.rec.last_sync["ok"])

    def test_create_client(self):
        self.m.grant(7, FRM, days=30, services=["vpn"])
        self.rec.sync_all()
        client, ids = self.xui.added
        mem = self.m.get(7)
        self.assertEqual(ids, [1, 5])
        self.assertEqual(client["email"], "mh-7")
        self.assertRegex(client["subId"], r"^[a-z0-9]{16}$")
        self.assertEqual(client["subId"], mem["vpn_sub_id"])
        self.assertIs(client["enable"], True)
        self.assertEqual(client["expiryTime"], mem["expires_ts"] * 1000)
        self.assertEqual(client["tgId"], 7)
        self.assertEqual(client["comment"], "MeowHub @bob")
        self.assertEqual((client["limitIp"], client["totalGB"]), (0, 0))
        self.assertEqual(self.rec.last_sync["vpn_clients"], 1)
        self.assertEqual(self.m.vpn_emails, {"mh-7"})
        # second pass: nothing to change
        self.xui.calls.clear()
        self.rec.sync_all()
        self.assertEqual([c for c in self.xui.calls if c[0] not in ("clients", "inbounds")], [])

    def test_member_without_vpn_gets_no_client(self):
        self.m.grant(7, FRM, services=["matrix"])
        self.rec.sync_all()
        self.assertEqual(self.xui.store, {})

    def test_no_expiry_means_zero(self):
        self.m.grant(7, FRM)
        self.m.set_expiry(7, None)
        self.rec.sync_all()
        self.assertEqual(self.xui.store["mh-7"]["expiryTime"], 0)

    def test_suspend_and_resume(self):
        self.m.grant(7, FRM)
        self.rec.sync_all()
        self.m.suspend(7)
        self.rec.sync_all()
        self.assertIs(self.xui.store["mh-7"]["enable"], False)
        self.assertEqual(len(self.xui.names("update")), 1)
        self.m.resume(7)
        self.rec.sync_all()
        self.assertIs(self.xui.store["mh-7"]["enable"], True)
        self.assertEqual(len(self.xui.names("update")), 2)

    def test_expired_disabled_and_services_removed_keeps_client(self):
        self.m.grant(7, FRM)
        self.rec.sync_all()
        sub = self.xui.store["mh-7"]["subId"]
        self.m.set_services(7, ["matrix"])                         # client exists -> still managed
        self.rec.sync_all()
        self.assertIs(self.xui.store["mh-7"]["enable"], False)
        self.assertEqual(self.xui.store["mh-7"]["subId"], sub)

    def test_inbound_differences(self):
        self.m.grant(7, FRM)
        self.rec.sync_all()
        self.m.set_member_inbounds([5, 2])
        self.rec.sync_all()
        self.assertEqual(self.xui.names("attach"), [("attach", "mh-7", (2,))])
        self.assertEqual(self.xui.names("detach"), [("detach", "mh-7", (1,))])
        self.assertEqual(sorted(self.xui.store["mh-7"]["inboundIds"]), [2, 5])

    def test_existing_sub_id_adopted(self):
        self.xui.store["mh-7"] = {"email": "mh-7", "subId": "abc123", "enable": True, "expiryTime": 0,
                                  "tgId": 0, "comment": "", "inboundIds": [1, 5]}
        self.m.grant(7, FRM)
        self.rec.sync_all()
        self.assertEqual(self.m.get(7)["vpn_sub_id"], "abc123")
        self.assertEqual(self.xui.store["mh-7"]["tgId"], 7)

    def test_delete_member_deletes_client_only_for_mh(self):
        self.m.grant(7, FRM)
        self.xui.store["admin-guy"] = {"email": "admin-guy", "inboundIds": [1]}
        self.xui.store["mh-99"] = {"email": "mh-99", "inboundIds": [1]}
        self.rec.sync_all()
        self.assertNotIn("mh-99", self.xui.store)
        self.assertIn("admin-guy", self.xui.store)
        self.assertIn("mh-7", self.xui.store)
        self.m.delete(7)
        self.rec.sync_all()
        self.assertNotIn("mh-7", self.xui.store)
        self.assertEqual(self.m.vpn_emails, set())

    def test_errors_isolated_per_member(self):
        self.m.grant(1, FRM)
        self.m.grant(2, FRM)
        orig = self.xui.client_add

        def flaky(client, ids):
            if client["email"] == "mh-1":
                raise RuntimeError("boom")
            orig(client, ids)
        self.xui.client_add = flaky
        self.rec.sync_all()
        self.assertIn("mh-2", self.xui.store)
        self.assertFalse(self.rec.last_sync["ok"])
        self.assertIn("boom", self.rec.last_sync["error"])
        self.assertEqual([e["kind"] for e in self.m.events(1)].count("vpn_sync"), 1)
        self.rec.sync_all()                                       # same error: no new event
        self.assertEqual([e["kind"] for e in self.m.events(1)].count("vpn_sync"), 1)

    def test_matrix_lock_only_on_change(self):
        self.m.grant(7, FRM, services=["matrix"])
        self.m.add_matrix(7, "@bob:x.org")
        self.rec.sync_all()
        self.assertEqual(self.syn.calls, [("@bob:x.org", False)])  # first pass applies all
        self.rec.sync_all()
        self.assertEqual(self.syn.calls, [("@bob:x.org", False)])
        self.m.suspend(7)
        self.rec.sync_all()
        self.rec.sync_all()
        self.assertEqual(self.syn.calls[1:], [("@bob:x.org", True)])
        self.m.resume(7)
        self.rec.sync_all()
        self.assertEqual(self.syn.calls[2:], [("@bob:x.org", False)])
        self.m.delete(7)                                           # deleted member: account stays locked
        self.rec.sync_all()
        self.assertEqual(self.syn.calls[3:], [("@bob:x.org", True)])

    def test_sync_member(self):
        self.m.grant(7, FRM)
        self.m.add_matrix(7, "@bob:x.org")
        self.rec.sync_member(7)
        self.assertIn("mh-7", self.xui.store)
        self.assertEqual(self.syn.calls, [("@bob:x.org", False)])
        self.m.delete(7)
        self.rec.sync_member(7)
        self.assertNotIn("mh-7", self.xui.store)

    def test_unconfigured_halves_skipped(self):
        rec = mm.Reconciler(self.m, None, None, lambda *a, **k: None, lambda: None)
        self.m.grant(7, FRM)
        rec.sync_all()
        rec.detect_new_inbounds()
        self.assertTrue(rec.last_sync["ok"])

    def test_new_inbound_gets_dated_once(self):
        self.rec.sync_all()
        self.xui._inbounds.append({"id": 6, "remark": "Fresh", "protocol": "vless", "port": 1, "enable": True})
        self.xui._inbounds.append({"id": 9, "remark": "01.02.26 · Old", "protocol": "vless", "port": 2, "enable": True})
        self.rec.detect_new_inbounds()
        self.rec.detect_new_inbounds()
        renames = [c for c in self.xui.calls if c[0] == "inbound_set_remark"]
        self.assertEqual(renames, [("inbound_set_remark", 6, mm.time.strftime("%d.%m.%y") + " · Fresh")])

    def test_new_inbound_notified_once(self):
        self.rec.sync_all()                                        # defaults + known
        self.xui._inbounds.append({"id": 6, "remark": "New <one>", "protocol": "trojan",
                                   "port": 8443, "enable": True})
        self.rec.detect_new_inbounds()
        self.rec.detect_new_inbounds()
        self.assertEqual(len(self.sent), 1)
        chat, text, markup = self.sent[0]
        self.assertEqual(chat, 1000)
        today = mm.time.strftime("%d.%m.%y")
        self.assertIn(f"🆕 Новый инбаунд в 3x-ui: <b>{today} · New &lt;one&gt;</b> (trojan, :8443). Выдать его участникам?", text)
        btns = markup["inline_keyboard"][0]
        self.assertEqual([b["callback_data"] for b in btns], ["inb:add:6", "inb:skip:6"])
        self.assertIn(6, self.m.known_inbounds())
        self.assertEqual(self.m.member_inbounds(), [1, 5])         # not added until the owner says so

    def test_ask_owner_without_contact(self):
        with mock.patch.dict(os.environ, {"OWNER_CONTACT": ""}):
            self.assertEqual(mm.ask_owner(True), "Напиши владельцу")
            self.assertEqual(mm.ask_owner(), "напиши владельцу")
        with mock.patch.dict(os.environ, {"OWNER_CONTACT": "@boss"}):
            self.assertEqual(mm.ask_owner(), "напиши @boss")

    @mock.patch.dict(os.environ, {"OWNER_CONTACT": "@boss"})
    def test_reminders(self):
        self.m.grant(7, {"first_name": "Bob <b>"}, days=30)
        self.rec.reminders()
        self.assertEqual(self.sent, [])
        now = int(time.time())
        self.m.set_expiry(7, now + 2 * DAY + 100)
        self.rec.reminders()
        self.rec.reminders()
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sent[0][0], 7)
        self.assertIn("через 3 дня", self.sent[0][1])
        self.assertEqual(self.m.get(7)["reminded"], 3)
        self.m.set_expiry(7, now + 3600)
        self.m.set_reminded(7, 3)
        self.rec.reminders()
        self.rec.reminders()
        self.assertEqual(len(self.sent), 2)
        self.assertIn("через 24 часа", self.sent[1][1])
        self.m.set_expiry(7, now - 10)
        self.rec.reminders()
        self.rec.reminders()
        self.assertEqual(len(self.sent), 4)                        # member + owner, once
        self.assertIn("закончилась", self.sent[2][1])
        self.assertIn("@boss", self.sent[2][1])
        chat, text, markup = self.sent[3]
        self.assertEqual(chat, 1000)
        self.assertIn("Bob &lt;b&gt;", text)
        self.assertIn("подписка истекла", text)
        self.assertEqual([b["callback_data"] for b in markup["inline_keyboard"][0]],
                         ["m:ext:7:30", "m:open:7"])
        # renewing re-arms the reminders
        self.m.extend(7, 30)
        self.assertEqual(self.m.get(7)["reminded"], 0)

    def test_no_reminders_for_suspended_or_unlimited(self):
        self.m.grant(1, FRM)
        self.m.grant(2, FRM)
        self.m.set_expiry(1, int(time.time()) - 10)
        self.m.suspend(1)
        self.m.set_expiry(2, None)
        self.rec.reminders()
        self.assertEqual(self.sent, [])

    def test_24h_reminder_skipped_right_after_short_redeem(self):
        code = self.m.new_code(days=1)
        self.m.redeem(8, FRM, code)                                # 1-day code: already inside 24 h
        self.rec.reminders()
        self.assertEqual(self.sent, [])                            # no "expires in 24 h" on day one
        self.assertEqual(self.m.get(8)["reminded"], 1)             # and none later for this expiry

    def test_24h_reminder_sent_after_window(self):
        self.m.grant(9, FRM, days=30)
        with self.m.s.lock:                                         # pretend the grant was 3 h ago
            self.m.s.db.execute("UPDATE events SET ts=ts-3*3600 WHERE uid=9")
        self.m._exec("UPDATE members SET expires_ts=? WHERE id=9", int(time.time()) + 20 * 3600)
        self.rec.reminders()
        self.assertEqual(len(self.sent), 1)
        self.assertIn("через 24 часа", self.sent[0][1])


if __name__ == "__main__":
    unittest.main()
