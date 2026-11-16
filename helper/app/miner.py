"""The GPU miner's switch (miner.md). The bot only writes a wish and reads a status file; the root
daemon gpu-miner on the host (scripts/gpu-miner.py) starts and stops the miner and watches the GPU."""
import json
import os
import time

DATA = os.environ.get("MINER_DATA", "/data")
WANT, STATUS = os.path.join(DATA, "miner-want.json"), os.path.join(DATA, "miner-status.json")
STALE = 30            # the daemon writes every few seconds; older than this = it is not running


def status():
    """The daemon's last status ({} when it never ran); adds `alive`."""
    try:
        with open(STATUS) as f:
            st = json.load(f)
    except (OSError, ValueError):
        return {"alive": False, "installed": False}
    st["installed"] = True
    st["alive"] = time.time() - st.get("ts", 0) < STALE and st.get("state") != "guard_down"
    return st


def wanted():
    try:
        with open(WANT) as f:
            return json.load(f).get("on") is True
    except (OSError, ValueError, AttributeError):
        return False


def set_wanted(on, by=None):
    tmp = WANT + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"on": bool(on), "by": by, "ts": int(time.time())}, f)
    os.replace(tmp, WANT)
