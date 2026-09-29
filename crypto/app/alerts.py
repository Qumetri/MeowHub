"""Alert evaluation: price targets and outsized moves.

Two rule kinds:

  targets       a price level with a direction. Fires on a *crossing*, not on
                a level, so a target created while already satisfied does not
                fire immediately -- it arms first and fires on the next real
                crossing. Hysteresis keeps a price hovering on the boundary
                from producing a stream of alerts.

  fluctuation   percent move over a rolling window. After firing, the move is
                re-measured from the price at which it fired, so a cascade
                (3% -> another 3% -> another 3%) keeps alerting instead of
                being swallowed by the cooldown, while a flat market stays
                quiet.
"""
import logging
import threading
import time
from collections import deque, defaultdict
from datetime import datetime, timezone

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
    def _check_fluctuation(self, symbol, price, now):
        r = self._fluct.get(symbol)
        if not r or not r["enabled"] or not r["pct"]:
            return

        # Inside the cooldown, only a *further* move of the same size re-alerts.
        if now - r["last_fired"] < r["cooldown_s"]:
            base = r["last_price"]
            if not base:
                return
            move = (price - base) / base * 100
            if abs(move) < r["pct"]:
                return
            self._fire_fluct(symbol, price, move, r, cascade=True, now=now)
            return

        with self._lock:
            d = self.history[symbol]
            win = now - r["window_s"]
            ref = None
            for ts, p in d:
                if ts >= win:
                    ref = p
                    break
        if ref is None or ref <= 0:
            return
        move = (price - ref) / ref * 100
        if abs(move) < r["pct"]:
            return
        self._fire_fluct(symbol, price, move, r, cascade=False, now=now)

    def _fire_fluct(self, symbol, price, move, r, cascade, now):
        if in_quiet_hours(self.store.get("quiet_hours")):
            # Deliberate: targets are levels the user chose and always fire.
            # Volatility noise is what quiet hours are for.
            log.info("fluctuation suppressed by quiet hours: %s", symbol)
            return
        self.store.set_fluctuation(symbol, last_fired=now, last_price=price)
        r["last_fired"], r["last_price"] = now, price

        tick = self.ticker(symbol)
        up = move > 0
        icon = "🚀" if up else "⚠️"
        mins = r["window_s"] // 60
        window = f"{mins} min" if mins else f"{r['window_s']}s"
        head = (f"{icon} <b>{tick} {'spiked' if up else 'dropped'} {fmt_pct(move)}</b>"
                f"{' (continuing)' if cascade else ''}")
        lines = [
            head, "",
            f"<b>Price</b>   ${fmt_price(price)}",
            f"<b>Move</b>    {fmt_pct(move)} "
            + (f"since last alert" if cascade else f"in {window}"),
        ]
        ch = self.change_24h(symbol)
        if ch is not None:
            lines.append(f"<b>24h</b>     {fmt_pct(ch)}")
        lines.append("")
        lines.append(f"<code>{datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S} UTC</code>")
        log.info("fluctuation fired: %s %.2f%%", tick, move)
        self.notifier.dispatch("fluctuation", "\n".join(lines), symbol=symbol, price=price)

    # ---------- daily summary ----------
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
