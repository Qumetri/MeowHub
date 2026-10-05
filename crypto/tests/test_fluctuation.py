"""Replays synthetic price paths through the fluctuation rules and checks what
would have been sent. Stdlib only, no network, no database:

    python3 crypto/tests/test_fluctuation.py
"""
import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
sys.modules["explain"] = types.SimpleNamespace(enabled=lambda: False, request=lambda *a: None)
import alerts  # noqa: E402

clock = [0]
alerts.time.time = lambda: clock[0]
timers = []
alerts.threading.Timer = lambda delay, fn, args=(): types.SimpleNamespace(
    start=lambda: timers.append(delay), daemon=True)


class Store:
    def __init__(self, quiet=""):
        self.quiet = quiet
        self.f = {"X": dict(symbol="X", pct=5.0, window_s=3600, cooldown_s=0, enabled=1,
                            last_fired=0, last_price=0, urgent_pct=10.0, urgent_repeat=3,
                            ep_dir=0, ep_anchor=0, ep_step=0, ep_peak=0, ep_pb=0)}

    def targets(self): return []
    def fluctuation(self): return {k: dict(v) for k, v in self.f.items()}
    def coins(self): return [dict(symbol="X", ticker="X")]
    def set_fluctuation(self, sym, **kw): self.f[sym].update(kw)
    def get(self, k): return self.quiet if k == "quiet_hours" else ""


def run(path, every=5, legs=60, quiet=""):
    """Walks linearly between waypoints, `legs` ticks per leg, `every` s apart.
    Returns the first line of every message sent."""
    sent = []

    class N:
        def dispatch(self, kind, text, **kw):
            sent.append(text.split("\n")[0].replace("<b>", "").replace("</b>", ""))

    timers.clear()
    clock[0] = 1000
    e = alerts.Engine(Store(quiet), N())
    e.on_tick("X", path[0])
    for a, b in zip(path, path[1:]):
        for i in range(legs):
            clock[0] += every
            e.on_tick("X", a + (b - a) * (i + 1) / legs)
    return sent


fails = 0


def check(name, cond, got):
    global fails
    print(("ok   " if cond else "FAIL ") + name + ("" if cond else f"\n     got: {got}"))
    fails += not cond


m = run([100, 100.5, 99.6, 100.2, 99.8])
check("flat market is silent", m == [], m)

m = run([100, 96, 102])
check("V-shape: down 4% then up 6% off the bottom alerts once, up", len(m) == 1 and "up" in m[0], m)

m = run([100, 116])
check("a rise reports each 5% step: 3 messages", len(m) == 3 and all("up" in x for x in m), m)
check("crossing 10% schedules the 2 urgent follow-ups", len(timers) == 2, timers)

m = run([100, 88], legs=1)
check("flash crash to -12%: one urgent alert + burst", len(m) == 1 and "🚨📉" in m[0] and len(timers) == 2, m)

# 2026-10-05: RVN +20% then chopping 5-6% under the top alternated
# +8% / -5.7% on every tick (176 messages in 10 minutes).
m = run([100, 120, 114, 120, 114, 120, 114, 119, 113])
pb = [x for x in m if "pulling back" in x]
check("+20% then chop under the top: 4 rise steps, ONE pullback", len(m) == 5 and len(pb) == 1, m)

m = run([100, 121, 114, 121.5, 110, 100, 94, 88])
check("a top that rolls over reports each step down, no burst", len(m) == 9 and
      [x for x in m if "pulling back" in x][1:2] and "-10" in [x for x in m if "pulling back" in x][1], m)
check("...and turns into a down move once the rise is given back", "down -20" in m[-2] and "down -25" in m[-1], m)

m = run([100, 96, 102, 96, 102, 96, 102])
check("sideways 6% chop: the first swing and one pullback, then quiet", len(m) == 2, m)

m = run([100, 106, 99, 106, 99] + [99] * 12 + [106])
check("chop resumes after a quiet hour: one more message, not a stream", len(m) == 3 and "down" not in m[-1], m)

m = run([100, 80, 85, 79, 84, 70])
check("a crash with dead-cat bounces: one bounce note, every step down", len(m) == 7 and
      sum("bouncing" in x for x in m) == 1, m)

m = run([100, 106], quiet="0-24")
check("quiet hours mute a 5% alert", m == [], m)
m = run([100, 112], quiet="0-24")
check("quiet hours never mute an urgent move", len(m) == 1 and "🚨" in m[0], m)

sys.exit(1 if fails else 0)
