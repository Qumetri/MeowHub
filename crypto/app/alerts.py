"""Alert evaluation: price targets and outsized moves.

Two rule kinds:

  targets       a price level with a direction. Fires on a *crossing*, not on
                a level, so a target created while already satisfied does not
                fire immediately -- it arms first and fires on the next real
                crossing. Hysteresis keeps a price hovering on the boundary
                from producing a stream of alerts.

  fluctuation   a swing of N% within a rolling window, then one more alert for
                every further N% while the move continues. Past the urgent
                level the alert is sent several times, a minute apart, with
                the live price in each.
"""
import logging
import threading
import time
from collections import deque, defaultdict
from datetime import datetime, timezone

import explain

log = logging.getLogger("alerts")

HYSTERESIS = 0.003      # 0.3% past the level before a repeating target re-arms
MAX_HISTORY = 4 * 3600  # seconds of tick history kept in memory per symbol


def fmt_price(p):
    """Format across nine orders of magnitude: BTC at 84000, RVN at 0.0023."""
    p = float(p)
    a = abs(p)
    if a >= 1000:
        return f"{p:,.2f}"
    if a >= 1:
        return f"{p:,.4f}".rstrip("0").rstrip(".")
    if a >= 0.01:
        return f"{p:.5f}".rstrip("0").rstrip(".")
    if a >= 0.0001:
        return f"{p:.7f}".rstrip("0").rstrip(".")
    return f"{p:.9f}".rstrip("0").rstrip(".")


def fmt_pct(x):
    return f"{x:+.2f}%"


def in_quiet_hours(spec, now=None):
    """spec is 'HH-HH' in local server time; empty means never quiet."""
    if not spec or "-" not in spec:
        return False
    try:
        a, b = (int(x) for x in spec.split("-", 1))
    except ValueError:
        return False
    h = (now or datetime.now()).hour
    return a <= h < b if a <= b else (h >= a or h < b)


class Engine:
    def __init__(self, store, notifier):
        self.store = store
        self.notifier = notifier
        self.prices = {}                       # symbol -> last price
        self.stats = {}                        # symbol -> 24h stats from the stream
        self.history = defaultdict(deque)      # symbol -> deque[(ts, price)]
        self._lock = threading.RLock()
        self._rules_at = 0.0
        self._targets = []
        self._fluct = {}
        self._coins = {}
        self.refresh_rules(force=True)

    # ---------- rule cache ----------
    def refresh_rules(self, force=False):
        """Re-read rules from sqlite at most once a second; the tick rate is
        far higher than the rate at which rules change."""
        now = time.time()
        if not force and now - self._rules_at < 1.0:
            return
        with self._lock:
            self._targets = [t for t in self.store.targets() if t["enabled"]]
            self._fluct = self.store.fluctuation()
            self._coins = {c["symbol"]: c for c in self.store.coins()}
            self._rules_at = now

    def ticker(self, symbol):
        c = self._coins.get(symbol)
        return c["ticker"] if c else symbol.replace("USDT", "")

    def seed_history(self, symbol, candles):
        """Warm the rolling window from stored candles so a restart does not
        blind the fluctuation rules for a full window."""
        with self._lock:
            d = self.history[symbol]
            for c in candles:
                d.append((c["ts"], c["c"]))
            if candles:
                self.prices.setdefault(symbol, candles[-1]["c"])

    # ---------- tick path ----------
    def on_tick(self, symbol, price, stats=None):
        now = int(time.time())
        with self._lock:
            self.prices[symbol] = price
            if stats:
                self.stats[symbol] = stats
            d = self.history[symbol]
            d.append((now, price))
            cutoff = now - MAX_HISTORY
            while d and d[0][0] < cutoff:
                d.popleft()
        self.refresh_rules()
        self._check_targets(symbol, price, now)
        self._check_fluctuation(symbol, price, now)

    def change_24h(self, symbol):
        st = self.stats.get(symbol)
        if not st or not st.get("open"):
            return None
        p = self.prices.get(symbol)
        if p is None:
            return None
        return (p - st["open"]) / st["open"] * 100

    # ---------- targets ----------
    def _check_targets(self, symbol, price, now):
        for t in [x for x in self._targets if x["symbol"] == symbol]:
            lvl = t["price"]
            up = t["direction"] == "above"
            hit = price >= lvl if up else price <= lvl

            if not t["armed"]:
                # Re-arm only once price has moved back clear of the level.
                back = price < lvl * (1 - HYSTERESIS) if up else price > lvl * (1 + HYSTERESIS)
                if back:
                    t["armed"] = 1
                    self.store.update_target(t["id"], armed=1)
                continue

            if not hit:
                continue
            if now - t["last_fired"] < t["cooldown_s"]:
                continue

            t["armed"] = 0
            fields = {"armed": 0, "last_fired": now, "fire_count": t["fire_count"] + 1}
            if not t["repeat"]:
                fields["enabled"] = 0
                t["enabled"] = 0
            t["last_fired"] = now
            t["fire_count"] += 1
            self.store.update_target(t["id"], **fields)
            self._fire_target(t, symbol, price)

    def _fire_target(self, t, symbol, price):
        tick = self.ticker(symbol)
        arrow = "🟢 ▲" if t["direction"] == "above" else "🔴 ▼"
        word = "rose above" if t["direction"] == "above" else "fell below"
        gap = (price - t["price"]) / t["price"] * 100
        ch = self.change_24h(symbol)
        lines = [
            f"{arrow} <b>{tick} {word} ${fmt_price(t['price'])}</b>",
            "",
            f"<b>Price</b>   ${fmt_price(price)}  ({fmt_pct(gap)} vs target)",
        ]
        if ch is not None:
            lines.append(f"<b>24h</b>     {fmt_pct(ch)}")
        if t["note"]:
            lines.append(f"<b>Note</b>    {t['note']}")
        if not t["repeat"]:
            lines.append("")
            lines.append("<i>One-shot target — now disabled.</i>")
        lines.append("")
        lines.append(f"<code>{datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S} UTC</code>")
        text = "\n".join(lines)
        log.info("target fired: %s %s %s", tick, t["direction"], t["price"])
        self.notifier.dispatch("target", text, symbol=symbol, price=price)

    # ---------- fluctuation ----------
    #
    # An *episode* is one directional move. It opens when price swings `pct`
    # away from the window's low (up) or high (down), and is then measured from
    # that fixed anchor: every further whole `pct` step is reported again, so a
    # move that runs 5% -> 10% -> 15% produces one message per step instead of
    # being hidden behind a cooldown. Crossing `urgent_pct` sends the alert
    # `urgent_repeat` times -- the follow-ups arrive a minute apart and carry
    # the live price, so they say whether it is still running or retracing.
    #
    # The window is scanned for its low AND high rather than compared with the
    # price exactly `window_s` ago: a V -- down 4%, then up 6% from the bottom --
    # is a 6% move within the hour even though it is +2% end to end.
    FOLLOWUP_GAP_S = 60

    def _window_extremes(self, symbol, since):
        with self._lock:
            pts = [p for ts, p in self.history[symbol] if ts >= since]
        if not pts:
            return None, None
        return min(pts), max(pts)

    def _check_fluctuation(self, symbol, price, now):
        r = self._fluct.get(symbol)
        if not r or not r["enabled"] or not r["pct"]:
            return
        step_pct = float(r["pct"])
        window = int(r["window_s"])
        d = int(r.get("ep_dir") or 0)
        open_ep = d != 0 and now - r["last_fired"] < window

        if open_ep:
            anchor = r["ep_anchor"]
            move = (price - anchor) / anchor * 100
            step = int(abs(move) // step_pct)
            if move * d > 0 and step > r["ep_step"]:
                self._fire_fluct(symbol, price, move, anchor, d, step, r, now, continuing=True)
                return
            # A full step back the other way is a new move, not noise.
            lo, hi = self._window_extremes(symbol, now - window)
            if lo is None:
                return
            rev_anchor = hi if d > 0 else lo
            rev = (price - rev_anchor) / rev_anchor * 100
            if abs(rev) >= step_pct and rev * d < 0:
                self._fire_fluct(symbol, price, rev, rev_anchor, -d,
                                 int(abs(rev) // step_pct), r, now, continuing=False)
            return

        # No open episode. Only look at prices since the last alert, so a move
        # that was already reported is never reported a second time.
        lo, hi = self._window_extremes(symbol, max(now - window, r["last_fired"]))
        if lo is None or lo <= 0:
            return
        up = (price - lo) / lo * 100
        dn = (price - hi) / hi * 100
        move, anchor, direction = (up, lo, 1) if abs(up) >= abs(dn) else (dn, hi, -1)
        if abs(move) < step_pct:
            return
        self._fire_fluct(symbol, price, move, anchor, direction,
                         int(abs(move) // step_pct), r, now, continuing=False)

    def _fire_fluct(self, symbol, price, move, anchor, direction, step, r, now, continuing):
        prev_step = r["ep_step"] if continuing else 0
        urgent_pct = float(r.get("urgent_pct") or 0)
        urgent = urgent_pct > 0 and abs(move) >= urgent_pct
        # The burst goes out once per episode: on the step that first crosses
        # the urgent level. Steps beyond it are single (still 🚨) messages.
        first_urgent = urgent and prev_step * float(r["pct"]) < urgent_pct

        state = {"last_fired": now, "last_price": price, "ep_dir": direction,
                 "ep_anchor": anchor, "ep_step": step}
        self.store.set_fluctuation(symbol, **state)
        r.update(state)

        # Quiet hours silence ordinary volatility, never an urgent move. The
        # episode is still recorded, so the same move is not announced later.
        if not urgent and in_quiet_hours(self.store.get("quiet_hours")):
            log.info("fluctuation suppressed by quiet hours: %s %.2f%%", symbol, move)
            return

        tick = self.ticker(symbol)
        up = move > 0
        icon = ("🚨📈" if up else "🚨📉") if urgent else ("📈" if up else "📉")
        mins = int(r["window_s"]) // 60
        window = f"{mins // 60}h" if mins and mins % 60 == 0 else f"{mins} min"
        verb = "up" if up else "down"
        head = f"{icon} <b>{tick} {verb} {fmt_pct(move)} within {window}</b>"
        if continuing:
            head += "  — still going"
        lines = [head, "",
                 f"<b>Price</b>   ${fmt_price(price)}",
                 f"<b>From</b>    ${fmt_price(anchor)}  ({'low' if up else 'high'} of the move)"]
        ch = self.change_24h(symbol)
        if ch is not None:
            lines.append(f"<b>24h</b>     {fmt_pct(ch)}")
        repeat = max(1, int(r.get("urgent_repeat") or 1)) if first_urgent else 1
        if first_urgent and repeat > 1:
            lines.append("")
            lines.append(f"<i>Past {urgent_pct:g}% — {repeat - 1} follow-up"
                         f"{'s' if repeat > 2 else ''} with the live price will follow.</i>")
        lines.append("")
        lines.append(f"<code>{datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S} UTC</code>")
        log.info("fluctuation fired: %s %.2f%% step=%d urgent=%s", tick, move, step, urgent)
        meta = {}
        self.notifier.dispatch("fluctuation", "\n".join(lines), symbol=symbol, price=price, meta=meta)

        # Ask n8n why -- on the first alert of a move, and again when it first
        # turns urgent. Intermediate steps of the same move are not re-explained.
        if explain.enabled() and (not continuing or first_urgent):
            explain.request({
                "symbol": symbol, "ticker": tick, "direction": "up" if up else "down",
                "move_pct": round(move, 2), "price": price, "anchor": anchor,
                "window_min": mins, "urgent": urgent, "urgent_first": first_urgent,
                "message_id": meta.get("message_id"), "ts": now,
            })

        for i in range(2, repeat + 1):
            t = threading.Timer(self.FOLLOWUP_GAP_S * (i - 1), self._followup,
                                args=(symbol, anchor, direction, i, repeat))
            t.daemon = True
            t.start()

    def _followup(self, symbol, anchor, direction, n, total):
        """A follow-up ping for an urgent move, re-measured at send time so it
        reports what the market is doing now rather than repeating itself."""
        price = self.prices.get(symbol)
        if price is None:
            return
        move = (price - anchor) / anchor * 100
        tick = self.ticker(symbol)
        still = move * direction > 0 and abs(move) >= float(self._fluct.get(symbol, {}).get("urgent_pct") or 0)
        state = "still" if still else "now"
        text = (f"🚨{'📈' if direction > 0 else '📉'} <b>{tick} {state} {fmt_pct(move)}</b> from ${fmt_price(anchor)}\n"
                f"Price ${fmt_price(price)} · follow-up {n}/{total}")
        self.notifier.dispatch("fluctuation", text, symbol=symbol, price=price)

    def build_summary(self):
        rows = []
        for c in self.store.coins():
            sym = c["symbol"]
            p = self.prices.get(sym)
            if p is None:
                continue
            ch = self.change_24h(sym)
            rows.append((c["ticker"], p, ch))
        if not rows:
            return None
        rows.sort(key=lambda r: (r[2] is None, -(r[2] or 0)))
        out = [f"📊 <b>Daily summary</b> — {datetime.now():%d %b %Y}", ""]
        for tick, p, ch in rows:
            chs = fmt_pct(ch) if ch is not None else "  n/a"
            mark = "🟢" if (ch or 0) > 0 else ("🔴" if (ch or 0) < 0 else "⚪")
            out.append(f"{mark} <b>{tick:<4}</b> ${fmt_price(p):<12} {chs}")
        active = [t for t in self.store.targets() if t["enabled"]]
        out.append("")
        out.append(f"<i>{len(active)} active target{'s' if len(active) != 1 else ''}</i>")
        return "\n".join(out)

    def send_summary(self, force=False):
        text = self.build_summary()
        if not text:
            return False
        self.notifier.dispatch("summary", text, force=force)
        return True
