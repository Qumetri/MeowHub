"""Paid extensions of a membership: Telegram Stars and CryptoBot (Crypto Pay).

Both providers end in Payments.credit(): one `payments` row per payment (primary key
"<provider>:<ref>", so a repeated Telegram update or a second poll never credits twice), the
membership extended from its current end exactly like a code (Members._apply, default services
added), an event `pay`, and a usage notice to the owner and admins.

  * Stars: the Mini App asks /api/pay for an invoice link (createInvoiceLink, currency XTR,
    issued by the bot the app was opened from) and opens it with WebApp.openInvoice. The bot
    answers pre_checkout_query (validated here, within Telegram's 10 s) and credits on the
    `successful_payment` message. Stars land on that bot's balance.
  * CryptoBot: /api/pay creates a Crypto Pay invoice priced in USD (paid in USDT/TON/BTC/…),
    the app opens it in @CryptoBot, and a poller here checks pending invoices every 20 s
    (no webhook, no new route). Needs CRYPTOPAY_TOKEN in .env (@CryptoBot → Crypto Pay →
    Create App); CRYPTOPAY_TESTNET=1 talks to @CryptoTestnetBot instead.

Owner settings: settings `payments` = {"stars": {"enabled", "prices"}, "crypto": {...}}.
Both providers are off until the owner switches them on (service toggle convention).
"""
import json
import logging
import os
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

log = logging.getLogger("payments")

DAY = 86400
PLANS = {"m1": 30, "m3": 90, "m12": 365}              # plan id -> days, in the order shown
PROVIDERS = ("stars", "crypto")
DEFAULT_PRICES = {"stars": {"m1": 100, "m3": 270, "m12": 960},   # ★ (reports/stars-payments-2026-10.md)
                  "crypto": {"m1": 2.0, "m3": 5.0, "m12": 18.0}}  # USD
CRYPTO_ASSETS = "USDT,TON,BTC,ETH,LTC,TRX,USDC"
CRYPTO_TTL = 3600                                     # an unpaid CryptoBot invoice expires after an hour
POLL_EVERY = 20

SCHEMA = """
CREATE TABLE IF NOT EXISTS payments (
  id TEXT PRIMARY KEY, uid INTEGER NOT NULL, provider TEXT NOT NULL, plan TEXT NOT NULL,
  days INTEGER NOT NULL, amount TEXT NOT NULL, bot TEXT NOT NULL DEFAULT '', ts INTEGER NOT NULL,
  refunded INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS crypto_invoices (
  invoice_id INTEGER PRIMARY KEY, uid INTEGER NOT NULL, plan TEXT NOT NULL, amount TEXT NOT NULL,
  url TEXT NOT NULL, status TEXT NOT NULL, created_ts INTEGER NOT NULL, expires_ts INTEGER NOT NULL,
  user_json TEXT NOT NULL DEFAULT '{}');
"""


class PayError(Exception):
    """A refusal with an i18n key (pay.err.<code>) and an HTTP status for the API."""

    def __init__(self, code, status=400):
        super().__init__(code)
        self.code, self.status = code, status


def _price(provider, value):
    if provider == "stars":
        v = int(value)
        if not 1 <= v <= 10000:
            raise ValueError("stars price must be 1..10000")
        return v
    v = round(float(value), 2)
    if not 0.1 <= v <= 1000:
        raise ValueError("crypto price must be 0.1..1000 USD")
    return v


def fmt_amount(provider, amount):
    return f"{int(float(amount))} ★" if provider == "stars" else f"${float(amount):g}"


class Payments:
    def __init__(self, store, members, announce=None):
        """announce(provider, uid, plan, amount, member, user) runs after a new credit
        (the bot thanks the payer and tells the owner); never inside the DB lock."""
        self.s = store
        self.m = members
        self.announce = announce
        with self.s.lock:
            self.s.db.executescript(SCHEMA)
        self._poll_lock = threading.Lock()

    # ------------------------------------------------------------ settings --
    def settings(self):
        try:
            raw = json.loads(self.s.get("payments", "") or "{}")
        except ValueError:
            raw = {}
        out = {}
        for p in PROVIDERS:
            cur = raw.get(p) if isinstance(raw.get(p), dict) else {}
            prices = dict(DEFAULT_PRICES[p])
            for plan, v in (cur.get("prices") or {}).items():
                if plan in PLANS:
                    try:
                        prices[plan] = _price(p, v)
                    except (TypeError, ValueError):
                        pass
            out[p] = {"enabled": cur.get("enabled") is True, "prices": prices}
        return out

    def configure(self, provider, enabled=None, prices=None):
        if provider not in PROVIDERS:
            raise PayError("unknown_provider", 404)
        cfg = self.settings()
        if enabled is not None:
            if not isinstance(enabled, bool):
                raise PayError("bad_request")
            if enabled and provider == "crypto" and not self.crypto_token():
                raise PayError("crypto_no_token")
            cfg[provider]["enabled"] = enabled
        if prices is not None:
            if not isinstance(prices, dict) or set(prices) - set(PLANS):
                raise PayError("bad_request")
            try:
                for plan, v in prices.items():
                    cfg[provider]["prices"][plan] = _price(provider, v)
            except (TypeError, ValueError):
                raise PayError("bad_price") from None
        self.s.set("payments", json.dumps(cfg))
        self.m.log(None, "pay_settings", f"{provider} {'on' if cfg[provider]['enabled'] else 'off'} "
                   + ",".join(f"{k}={v}" for k, v in cfg[provider]["prices"].items()))
        return cfg

    @staticmethod
    def crypto_token():
        return os.environ.get("CRYPTOPAY_TOKEN", "").strip()

    def enabled(self, provider):
        cfg = self.settings()[provider]
        return cfg["enabled"] and (provider != "crypto" or bool(self.crypto_token()))

    def offer(self, uid):
        """What /api/me shows on the extend screen; None when no provider is on."""
        cfg = self.settings()
        on = {p: self.enabled(p) for p in PROVIDERS}
        if not any(on.values()) or uid is None:
            return None
        return {"stars": on["stars"], "crypto": on["crypto"],
                "plans": [{"id": k, "days": d, "stars": cfg["stars"]["prices"][k],
                           "usd": cfg["crypto"]["prices"][k]} for k, d in PLANS.items()]}

    def check(self, provider, uid, plan):
        """Raise PayError unless `uid` may buy `plan` through `provider` right now."""
        if provider not in PROVIDERS:
            raise PayError("unknown_provider", 404)
        if plan not in PLANS:
            raise PayError("bad_plan")
        if not self.enabled(provider):
            raise PayError("off", 403)
        m = self.m.get(uid)
        if m and m.get("suspended"):
            raise PayError("suspended", 403)
        return self.settings()[provider]["prices"][plan]

    # --------------------------------------------------------------- credit --
    def credit(self, provider, ref, uid, plan, amount, user=None, bot=""):
        """('paid', member) the first time, ('dup', member) for a payment already counted."""
        uid, days, now = int(uid), PLANS[plan], int(time.time())
        db = self.s.db
        with self.s.lock:
            db.execute("BEGIN IMMEDIATE")
            try:
                cur = db.execute("INSERT OR IGNORE INTO payments(id,uid,provider,plan,days,amount,bot,ts) "
                                 "VALUES(?,?,?,?,?,?,?,?)",
                                 (f"{provider}:{ref}", uid, provider, plan, days, str(amount), bot, now))
                fresh = cur.rowcount == 1
                if fresh:
                    import members as mem                   # the module, for DEFAULT_SERVICES
                    self.m._apply(db, uid, user, days, mem.DEFAULT_SERVICES, now)
                    self.m._log_db(db, uid, "pay", f"{provider} {plan} +{days}d {fmt_amount(provider, amount)}")
                row = db.execute("SELECT * FROM members WHERE id=?", (uid,)).fetchone()
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise
        member = self.m._row(row)
        if not fresh:
            return "dup", member
        self.m.changed.set()                                 # the Reconciler (re)opens access now
        log.info("payment %s:%s uid=%s %s %s", provider, ref, uid, plan, amount)
        if self.announce is not None:
            try:
                self.announce(provider, uid, plan, amount, member, user)
            except Exception:                               # noqa: BLE001 - the credit stands
                log.exception("payment announce failed")
        return "paid", member

    def last_payment(self, uid, provider=None):
        sql = "SELECT * FROM payments WHERE uid=? AND refunded=0" + (" AND provider=?" if provider else "")
        r = self.s.q(sql + " ORDER BY ts DESC LIMIT 1", *([int(uid)] + ([provider] if provider else [])))
        return dict(r[0]) if r else None

    def mark_refunded(self, pid):
        """Flag a payment refunded and take its days back. Returns the payment row or None."""
        r = self.s.q("SELECT * FROM payments WHERE id=? AND refunded=0", pid)
        if not r:
            return None
        p = dict(r[0])
        self.s.q("UPDATE payments SET refunded=1 WHERE id=?", pid)
        self.m.extend(p["uid"], -p["days"])
        self.m.log(p["uid"], "pay_refund", f"{p['provider']} {p['plan']} -{p['days']}d {fmt_amount(p['provider'], p['amount'])}")
        return p

    def stats(self, limit=20):
        rows = [dict(r) for r in self.s.q("SELECT * FROM payments ORDER BY ts DESC LIMIT ?", limit)]
        tot = self.s.q("SELECT provider, COUNT(*) AS n, SUM(CAST(amount AS REAL)) AS total "
                       "FROM payments WHERE refunded=0 GROUP BY provider")
        return {"totals": {r["provider"]: {"count": r["n"], "amount": r["total"] or 0} for r in tot},
                "recent": rows}

    # ---------------------------------------------------------------- stars --
    @staticmethod
    def stars_payload(uid, plan, price):
        return f"st:{int(uid)}:{plan}:{int(price)}:{secrets.token_hex(4)}"

    @staticmethod
    def parse_stars(payload):
        """(uid, plan, price) from our invoice payload, else None."""
        parts = str(payload or "").split(":")
        if len(parts) != 5 or parts[0] != "st" or parts[2] not in PLANS:
            return None
        try:
            return int(parts[1]), parts[2], int(parts[3])
        except ValueError:
            return None

    def precheckout(self, q):
        """(ok, error_code) for a pre_checkout_query. The price is the one on the invoice:
        a price change after the link was made does not break a payment in progress."""
        p = self.parse_stars(q.get("invoice_payload"))
        frm = (q.get("from") or {}).get("id")
        if p is None or q.get("currency") != "XTR":
            return False, "bad_invoice"
        uid, plan, price = p
        if frm != uid or q.get("total_amount") != price:
            return False, "bad_invoice"
        try:
            self.check("stars", uid, plan)
        except PayError as e:
            return False, e.code
        return True, None

    # --------------------------------------------------------------- crypto --
    def crypto_call(self, method, **params):
        tok = self.crypto_token()
        if not tok:
            raise PayError("crypto_no_token", 503)
        base = "https://testnet-pay.crypt.bot" if os.environ.get("CRYPTOPAY_TESTNET") == "1" else "https://pay.crypt.bot"
        query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        # Cloudflare in front of Crypto Pay rejects Python's default User-Agent (403).
        req = urllib.request.Request(f"{base}/api/{method}" + (f"?{query}" if query else ""),
                                     headers={"Crypto-Pay-API-Token": tok, "User-Agent": "MeowHub-helper/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                body = json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            try:
                body = json.loads(e.read() or b"{}")
            except ValueError:
                body = {}
            log.warning("crypto %s: HTTP %s %s", method, e.code, (body.get("error") or {}).get("name"))
            raise PayError("crypto_failed", 502) from None
        except (OSError, ValueError) as e:
            log.warning("crypto %s: %s", method, e)
            raise PayError("crypto_failed", 502) from None
        if not body.get("ok"):
            log.warning("crypto %s: %s", method, body.get("error"))
            raise PayError("crypto_failed", 502)
        return body["result"]

    def crypto_invoice(self, uid, plan, user=None, description="", back_url=None):
        """Create (or reuse a fresh unpaid) CryptoBot invoice; returns its URL."""
        price = self.check("crypto", uid, plan)
        now = int(time.time())
        old = self.s.q("SELECT url FROM crypto_invoices WHERE uid=? AND plan=? AND amount=? AND status='active' "
                       "AND expires_ts>? ORDER BY created_ts DESC LIMIT 1", int(uid), plan, str(price), now + 300)
        if old:
            return old[0]["url"]
        inv = self.crypto_call("createInvoice", currency_type="fiat", fiat="USD", amount=f"{price:.2f}",
                               accepted_assets=CRYPTO_ASSETS, description=description[:1024] or None,
                               payload=f"cr:{int(uid)}:{plan}", expires_in=CRYPTO_TTL, allow_comments="false",
                               paid_btn_name="openBot" if back_url else None, paid_btn_url=back_url)
        url = inv.get("mini_app_invoice_url") or inv.get("bot_invoice_url") or ""
        if not url:
            raise PayError("crypto_failed", 502)
        self.s.q("INSERT OR REPLACE INTO crypto_invoices(invoice_id,uid,plan,amount,url,status,created_ts,expires_ts,user_json) "
                 "VALUES(?,?,?,?,?,'active',?,?,?)", int(inv["invoice_id"]), int(uid), plan, str(price), url,
                 now, now + CRYPTO_TTL, json.dumps(user or {}))
        return url

    def crypto_poll(self, uid=None):
        """Check active invoices (all, or one person's); credit the paid ones. Returns how many paid."""
        if not self.crypto_token() or not self._poll_lock.acquire(blocking=False):
            return 0
        try:
            now = int(time.time())
            sql = "SELECT * FROM crypto_invoices WHERE status='active' AND expires_ts>?"
            rows = [dict(r) for r in self.s.q(sql + (" AND uid=?" if uid else ""), *([now - 600] + ([int(uid)] if uid else [])))]
            # anything past its expiry by more than the grace window is simply closed
            self.s.q("UPDATE crypto_invoices SET status='expired' WHERE status='active' AND expires_ts<=?", now - 600)
            if not rows:
                return 0
            paid = 0
            for i in range(0, len(rows), 100):
                chunk = {r["invoice_id"]: r for r in rows[i:i + 100]}
                res = self.crypto_call("getInvoices", invoice_ids=",".join(map(str, chunk)))
                for inv in res.get("items", []):
                    row = chunk.get(int(inv.get("invoice_id", 0)))
                    if row is None:
                        continue
                    st = inv.get("status")
                    if st == "paid":
                        self.s.q("UPDATE crypto_invoices SET status='paid' WHERE invoice_id=?", row["invoice_id"])
                        user = json.loads(row["user_json"] or "{}")
                        state, _ = self.credit("crypto", row["invoice_id"], row["uid"], row["plan"], row["amount"],
                                               user=user, bot="cryptobot")
                        paid += state == "paid"
                    elif st == "expired":
                        self.s.q("UPDATE crypto_invoices SET status='expired' WHERE invoice_id=?", row["invoice_id"])
            return paid
        except PayError:
            return 0
        finally:
            self._poll_lock.release()

    def run(self):
        """Background poller for CryptoBot invoices (cheap: one request per 20 s while any is open)."""
        while True:
            try:
                self.crypto_poll()
            except Exception:                               # noqa: BLE001 - keep polling
                log.exception("crypto poll failed")
            time.sleep(POLL_EVERY)
