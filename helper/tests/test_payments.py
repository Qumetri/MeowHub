"""Paid extensions: Stars (pre-checkout + successful_payment) and CryptoBot (polled invoices)."""
import os
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
sys.path.insert(0, os.path.dirname(__file__))

import members as mm                      # noqa: E402
import payments as pm                     # noqa: E402
import test_bot as tb                     # noqa: E402  (its harness; importing the class would rerun its tests)
from test_bot import OWNER, msg            # noqa: E402


def paid_msg(uid, payload, amount, charge="ch-1", currency="XTR"):
    m = msg(uid, "")
    m.pop("text", None)
    m["successful_payment"] = {"currency": currency, "total_amount": amount, "invoice_payload": payload,
                               "telegram_payment_charge_id": charge, "provider_payment_charge_id": ""}
    return m


class PaymentsTest(unittest.TestCase):
    setUp, make_member = tb.BotTest.setUp, tb.BotTest.make_member

    def pay(self):
        return self.h.payments

    def wait_owner(self, n=1):
        for _ in range(50):                       # usage notices go out on a thread
            if len(self.b.sent(OWNER)) >= n:
                break
            time.sleep(0.01)
        return self.b.sent(OWNER)

    # ---------------------------------------------------------------- settings --
    def test_off_by_default_and_no_offer(self):
        self.assertFalse(self.pay().enabled("stars"))
        self.assertIsNone(self.pay().offer(5))

    def test_offer_lists_plans_with_both_prices(self):
        self.pay().configure("stars", enabled=True, prices={"m1": 150})
        o = self.pay().offer(5)
        self.assertTrue(o["stars"])
        self.assertFalse(o["crypto"])
        self.assertEqual([p["id"] for p in o["plans"]], ["m1", "m3", "m12"])
        self.assertEqual(o["plans"][0]["stars"], 150)
        self.assertEqual(o["plans"][1]["stars"], 270)

    def test_crypto_needs_a_token(self):
        with mock.patch.dict(os.environ, {"CRYPTOPAY_TOKEN": ""}):
            with self.assertRaises(pm.PayError) as e:
                self.pay().configure("crypto", enabled=True)
        self.assertEqual(e.exception.code, "crypto_no_token")

    def test_bad_prices_rejected(self):
        for bad in ({"m1": 0}, {"m1": 20000}, {"x": 5}, {"m1": "abc"}):
            with self.assertRaises(pm.PayError):
                self.pay().configure("stars", prices=bad)

    # ------------------------------------------------------------------ credit --
    def test_credit_extends_from_current_end_once(self):
        self.make_member(60, days=10)
        before = self.mm.get(60)["expires_ts"]
        state, m = self.pay().credit("stars", "c1", 60, "m1", 100)
        self.assertEqual(state, "paid")
        self.assertAlmostEqual(m["expires_ts"] - before, 30 * mm.DAY, delta=5)
        state, m2 = self.pay().credit("stars", "c1", 60, "m1", 100)       # the same update again
        self.assertEqual(state, "dup")
        self.assertEqual(m2["expires_ts"], m["expires_ts"])
        kinds = [e["kind"] for e in self.mm.events(60)]
        self.assertEqual(kinds.count("pay"), 1)

    def test_credit_makes_a_stranger_a_member(self):
        state, m = self.pay().credit("crypto", 77, 61, "m3", "5.00", user={"first_name": "Zed"})
        self.assertEqual(state, "paid")
        self.assertEqual(self.mm.status(m), "active")
        self.assertEqual(m["services"], mm.DEFAULT_SERVICES)
        self.assertEqual(m["first_name"], "Zed")
        self.assertEqual(self.mm.trial_state(61), "used")                # a payer is no trial candidate

    # --------------------------------------------------------------- precheckout --
    def q(self, uid, payload, amount, currency="XTR"):
        return {"id": "pq1", "from": {"id": uid}, "currency": currency, "total_amount": amount,
                "invoice_payload": payload}

    def test_precheckout_rules(self):
        self.pay().configure("stars", enabled=True)
        good = pm.Payments.stars_payload(62, "m1", 100)
        self.assertEqual(self.pay().precheckout(self.q(62, good, 100)), (True, None))
        self.assertEqual(self.pay().precheckout(self.q(63, good, 100)), (False, "bad_invoice"))   # someone else
        self.assertEqual(self.pay().precheckout(self.q(62, good, 99)), (False, "bad_invoice"))    # wrong amount
        self.assertEqual(self.pay().precheckout(self.q(62, good, 100, "USD")), (False, "bad_invoice"))
        self.assertEqual(self.pay().precheckout(self.q(62, "junk", 100)), (False, "bad_invoice"))
        self.make_member(62)
        self.mm.suspend(62)
        self.assertEqual(self.pay().precheckout(self.q(62, good, 100)), (False, "suspended"))
        self.mm.resume(62)
        self.pay().configure("stars", enabled=False)
        self.assertEqual(self.pay().precheckout(self.q(62, good, 100)), (False, "off"))

    def test_bot_answers_precheckout(self):
        self.pay().configure("stars", enabled=True)
        self.h.on_precheckout(self.q(64, pm.Payments.stars_payload(64, "m1", 100), 100))
        self.h.on_precheckout(self.q(64, pm.Payments.stars_payload(64, "m1", 100), 5))
        calls = [e[3] for e in self.b.log if e[0] == "answerPreCheckoutQuery"]
        self.assertEqual(calls[0], {"pre_checkout_query_id": "pq1", "ok": True})
        self.assertFalse(calls[1]["ok"])
        self.assertIn("устарел", calls[1]["error_message"])

    # ---------------------------------------------------------- successful payment --
    def test_successful_payment_credits_thanks_and_tells_owner(self):
        self.pay().configure("stars", enabled=True)
        self.make_member(65, days=2)
        payload = pm.Payments.stars_payload(65, "m3", 270)
        self.h.on_message(paid_msg(65, payload, 270, charge="tg-abc"))
        self.h.on_message(paid_msg(65, payload, 270, charge="tg-abc"))     # redelivered update
        m = self.mm.get(65)
        self.assertAlmostEqual(m["expires_ts"] - time.time(), 92 * mm.DAY, delta=120)
        thanks = [s for s in self.b.sent(65) if "Оплата получена" in s[2]]
        self.assertEqual(len(thanks), 1)
        self.assertIn("3 месяца", thanks[0][2])
        notes = self.wait_owner(1)
        self.assertIn("оплатил(а) 3 месяца звёздами (270 ★)", notes[-1][2])
        row = self.pay().last_payment(65)
        self.assertEqual((row["id"], row["provider"], row["amount"]), ("stars:tg-abc", "stars", "270"))

    def test_unrecognised_payment_alerts_owner(self):
        self.h.on_message(paid_msg(66, "something-else", 50, charge="zz"))
        self.assertIsNone(self.mm.get(66))
        self.assertIn("не узнал", self.wait_owner(1)[-1][2])

    def test_paysupport_for_anyone(self):
        self.h.on_message(msg(67, "/paysupport"))
        self.assertIn("Вопросы по оплате", self.b.sent(67)[-1][2])
        self.assertIn("/paysupport", self.wait_owner(1)[-1][2])

    # ------------------------------------------------------------------ refund --
    def test_owner_refunds_last_stars_payment(self):
        self.pay().configure("stars", enabled=True)
        self.make_member(68, days=5)
        payload = pm.Payments.stars_payload(68, "m1", 100)
        self.h.on_message(paid_msg(68, payload, 100, charge="r-1"))
        after_pay = self.mm.get(68)["expires_ts"]
        self.h.on_message(msg(OWNER, "/refund 68"))
        refund = [e[3] for e in self.b.log if e[0] == "refundStarPayment"]
        self.assertEqual(refund, [{"user_id": 68, "telegram_payment_charge_id": "r-1"}])
        self.assertAlmostEqual(after_pay - self.mm.get(68)["expires_ts"], 30 * mm.DAY, delta=5)
        self.assertIn("Возвращено 100 ★", self.b.sent(OWNER)[-1][2])
        self.h.on_message(msg(OWNER, "/refund 68"))                       # nothing left to refund
        self.assertIn("нет оплат", self.b.sent(OWNER)[-1][2])

    def test_refund_is_owner_only(self):
        self.make_member(69)
        self.h.on_message(msg(69, "/refund 69"))
        self.assertFalse([e for e in self.b.log if e[0] == "refundStarPayment"])

    # ------------------------------------------------------------------ crypto --
    def test_crypto_invoice_then_poll_credits_once(self):
        calls = []

        def fake(method, **p):
            calls.append((method, p))
            if method == "createInvoice":
                return {"invoice_id": 501, "status": "active", "mini_app_invoice_url": "https://t.me/CryptoBot/app?startapp=x"}
            return {"items": [{"invoice_id": 501, "status": "paid"}]}

        with mock.patch.dict(os.environ, {"CRYPTOPAY_TOKEN": "t"}):
            self.pay().configure("crypto", enabled=True, prices={"m1": 2.5})
            with mock.patch.object(self.pay(), "crypto_call", side_effect=fake):
                url = self.pay().crypto_invoice(70, "m1", user={"first_name": "Ann"}, back_url="https://t.me/TestBot")
                self.assertEqual(url, "https://t.me/CryptoBot/app?startapp=x")
                again = self.pay().crypto_invoice(70, "m1")               # an open invoice is reused
                self.assertEqual(again, url)
                self.assertEqual(self.pay().crypto_poll(), 1)
                self.assertEqual(self.pay().crypto_poll(), 0)             # already paid, nothing open
        create = [p for m, p in calls if m == "createInvoice"]
        self.assertEqual(len(create), 1)
        self.assertEqual((create[0]["currency_type"], create[0]["fiat"], create[0]["amount"]), ("fiat", "USD", "2.50"))
        m = self.mm.get(70)
        self.assertEqual(self.mm.status(m), "active")
        self.assertEqual(m["first_name"], "Ann")
        self.assertIn("через CryptoBot ($2.5)", self.wait_owner(1)[-1][2])

    def test_crypto_expired_invoice_is_closed(self):
        with mock.patch.dict(os.environ, {"CRYPTOPAY_TOKEN": "t"}):
            self.pay().configure("crypto", enabled=True)
            fake = mock.Mock(side_effect=[{"invoice_id": 9, "status": "active", "bot_invoice_url": "https://t.me/CryptoBot?start=x"},
                                          {"items": [{"invoice_id": 9, "status": "expired"}]}])
            with mock.patch.object(self.pay(), "crypto_call", fake):
                self.pay().crypto_invoice(71, "m12")
                self.assertEqual(self.pay().crypto_poll(), 0)
        st = self.h.store.q("SELECT status FROM crypto_invoices WHERE invoice_id=9")[0]["status"]
        self.assertEqual(st, "expired")
        self.assertIsNone(self.mm.get(71))


if __name__ == "__main__":
    unittest.main()
