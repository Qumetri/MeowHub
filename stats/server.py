#!/usr/bin/env python3
"""Tiny dependency-free host-metrics server for the hub page.

Reads host stats from a bind-mounted /host/proc (host root netns via PID 1),
host root filesystem at /host/root, and nvidia-smi (injected by the NVIDIA
container runtime). Serves JSON at /stats. Deltas (CPU, network) are computed
between successive requests.
"""
import glob
import json
import os
import subprocess
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PROC = "/host/proc"
SYS = "/host/sys"
ROOT_FS = "/host/root"
MEDIA_FS = "/host/media"

# rolling samples for delta computation
_prev = {"cpu": None, "net": None}


def read_cpu():
    with open(f"{PROC}/stat") as f:
        parts = f.readline().split()[1:8]
    vals = list(map(int, parts))
    idle = vals[3] + vals[4]  # idle + iowait
    total = sum(vals)
    prev = _prev["cpu"]
    _prev["cpu"] = (idle, total)
    if not prev:
        return 0.0
    didle, dtotal = idle - prev[0], total - prev[1]
    if dtotal <= 0:
        return 0.0
    return round((1 - didle / dtotal) * 100, 1)


def _read_int(path):
    with open(path) as f:
        return int(f.read().strip())


def read_cpu_temp():
    # Preferred: coretemp hwmon, "Package id 0" label (robust to hwmon renumbering)
    for name_file in glob.glob(f"{SYS}/class/hwmon/hwmon*/name"):
        try:
            if open(name_file).read().strip() != "coretemp":
                continue
            hwmon = os.path.dirname(name_file)
            for lbl in glob.glob(f"{hwmon}/temp*_label"):
                if open(lbl).read().strip().lower().startswith("package"):
                    return round(_read_int(lbl.replace("_label", "_input")) / 1000, 1)
        except OSError:
            continue
    # Fallback: thermal zone of type x86_pkg_temp
    for zone in glob.glob(f"{SYS}/class/thermal/thermal_zone*"):
        try:
            if open(f"{zone}/type").read().strip() == "x86_pkg_temp":
                return round(_read_int(f"{zone}/temp") / 1000, 1)
        except OSError:
            continue
    return None


def read_mem():
    info = {}
    with open(f"{PROC}/meminfo") as f:
        for line in f:
            k, v = line.split(":")
            info[k] = int(v.strip().split()[0]) * 1024  # kB -> bytes
    total = info["MemTotal"]
    avail = info.get("MemAvailable", info["MemFree"])
    used = total - avail
    return {"used": used, "total": total, "percent": round(used / total * 100, 1)}


def read_disk(path):
    try:
        s = os.statvfs(path)
    except OSError:
        return None
    total = s.f_blocks * s.f_frsize
    free = s.f_bavail * s.f_frsize
    used = total - free
    if total == 0:
        return None
    return {"used": used, "total": total, "percent": round(used / total * 100, 1)}


def read_net():
    rx = tx = 0
    with open(f"{PROC}/1/net/dev") as f:
        for line in f.readlines()[2:]:
            iface, data = line.split(":", 1)
            iface = iface.strip()
            if iface == "lo" or iface.startswith(("veth", "br-", "docker", "virbr")):
                continue
            cols = data.split()
            rx += int(cols[0])
            tx += int(cols[8])
    now = time.time()
    prev = _prev["net"]
    _prev["net"] = (rx, tx, now)
    if not prev:
        return {"rxBps": 0, "txBps": 0}
    dt = now - prev[2]
    if dt <= 0:
        return {"rxBps": 0, "txBps": 0}
    return {
        "rxBps": max(0, int((rx - prev[0]) / dt)),
        "txBps": max(0, int((tx - prev[1]) / dt)),
    }


def read_gpu():
    try:
        out = subprocess.run(
            ["nvidia-smi",
             "--query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=4,
        ).stdout.strip().splitlines()[0]
        util, mused, mtotal, temp = [x.strip() for x in out.split(",")]
        return {
            "percent": float(util),
            "memUsed": int(mused) * 1024 * 1024,
            "memTotal": int(mtotal) * 1024 * 1024,
            "temp": float(temp),
        }
    except Exception:
        return None


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.rstrip("/") not in ("/stats", ""):
            self.send_response(404)
            self.end_headers()
            return
        payload = json.dumps({
            "cpu": {"percent": read_cpu(), "temp": read_cpu_temp()},
            "mem": read_mem(),
            "disk": read_disk(ROOT_FS),
            "hdd": read_disk(MEDIA_FS),
            "gpu": read_gpu(),
            "net": read_net(),
            "ts": int(time.time()),
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass  # quiet


if __name__ == "__main__":
    # prime the delta baselines so the first real request isn't zero
    read_cpu(); read_net()
    ThreadingHTTPServer(("0.0.0.0", 9101), Handler).serve_forever()
