"""Server health checks.

Each check returns Findings. A finding with level "ok" carries report text
only; "warn" and "crit" are problems. The monitor (see bot.py) alerts on a
problem only after it has been seen on two consecutive runs of its check, so
a container being recreated during a deploy, or one slow HTTP answer, does
not page anyone -- and sends a "resolved" note when it clears.

Sources, all read-only:
  hub-stats      CPU/RAM/disk/GPU, already computed for the hub page
  docker-proxy   container states, through a socket proxy that only allows
                 GET on /containers -- never the raw docker socket
  /host/proc     the host's (PID 1's) network namespace: TCP sockets, Wi-Fi
  caddy:443      TLS certificates and routes, with the real SNI
  DNS + ipify    do our A records still point at our public IP?
"""
import json
import logging
import os
import socket
import ssl
import time
import urllib.request
from datetime import datetime, timezone
from urllib.parse import urlsplit

import hub
import i18n

log = logging.getLogger("health")

STATS = os.environ.get("HUB_STATS_URL", "http://hub-stats:9101/stats")
DOCKER = os.environ.get("DOCKER_PROXY_URL", "http://docker-proxy:2375")
PROXY_HOST = os.environ.get("EDGE_HOST", "caddy")   # who terminates :443
PROC = os.environ.get("HOST_PROC", "/host/proc")
IP_SOURCES = ("https://api.ipify.org", "https://ifconfig.me/ip", "https://icanhazip.com")

# Thresholds. Env-overridable so a bigger disk or a hotter room can be tuned
# without a code change.
def _f(name, default):
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default

SSD_WARN, SSD_CRIT = _f("HEALTH_SSD_WARN", 85), _f("HEALTH_SSD_CRIT", 92)
HDD_WARN, HDD_CRIT = _f("HEALTH_HDD_WARN", 90), _f("HEALTH_HDD_CRIT", 95)
RAM_WARN = _f("HEALTH_RAM_WARN", 92)
GPU_TEMP_WARN = _f("HEALTH_GPU_TEMP_WARN", 85)
CPU_TEMP_WARN = _f("HEALTH_CPU_TEMP_WARN", 90)
# The VPN socket leak (see vpn docs) shows up as tens of thousands of
# ESTABLISHED sockets; a healthy day is a few hundred.
TCP_WARN, TCP_CRIT = _f("HEALTH_TCP_WARN", 5000), _f("HEALTH_TCP_CRIT", 12000)
CERT_WARN_D, CERT_CRIT_D = _f("HEALTH_CERT_WARN_DAYS", 14), _f("HEALTH_CERT_CRIT_DAYS", 5)


class Finding:
    """`text` is the Russian rendering (also what plain tests compare); a finding
    built with `tkey`/`args` can be rendered in any language through `t(lang)`."""
    __slots__ = ("key", "level", "text", "tkey", "args")

    def __init__(self, key, level, text, tkey=None, args=None):
        self.key, self.level, self.text = key, level, text
        self.tkey, self.args = tkey, args or {}

    def t(self, lang="ru"):
        if not self.tkey:
            return self.text
        args = {k: (v.get(lang) or v["ru"] if isinstance(v, dict) else v) for k, v in self.args.items()}
        return i18n.tr(lang, self.tkey, **args)

    def __repr__(self):
        return f"<{self.level} {self.key}: {self.text}>"


def F(key, level, tkey, **args):
    """A Finding whose text comes from the i18n catalog."""
    return Finding(key, level, i18n.tr("ru", tkey, **args), tkey, args)


def _json(url, timeout=8):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode())


def gb(n):
    """GiB, so the numbers match the hardware: 31 GB of RAM, a 12 GB card."""
    g = n / 2**30
    return f"{g:.0f}" if g >= 10 else f"{g:.1f}"


# ----------------------------------------------------------------- system --
def check_system(store):
    out, info = [], {}
    try:
        s = _json(STATS)
    except Exception as e:                                    # noqa: BLE001
        return [F("stats", "warn", "health.stats_down", e=e)], info
    info["stats"] = s
    for key, name, warn, crit in (("disk", "SSD", SSD_WARN, SSD_CRIT),
                                  ("hdd", "HDD", HDD_WARN, HDD_CRIT)):
        d = s.get(key)
        if not d:
            continue
        p = d["percent"]
        lvl = "crit" if p >= crit else "warn" if p >= warn else "ok"
        out.append(F(f"disk:{key}", lvl, "health.disk", name=name, p=f"{p:.0f}",
                     free=gb(d["total"] - d["used"])))
    m = s.get("mem") or {}
    if m:
        out.append(F("ram", "warn" if m["percent"] >= RAM_WARN else "ok", "health.ram",
                     p=f"{m['percent']:.0f}"))
    t = (s.get("cpu") or {}).get("temp")
    if t is not None:
        out.append(F("cpu:temp", "warn" if t >= CPU_TEMP_WARN else "ok", "health.cpu_temp", t=f"{t:.0f}"))
    g = s.get("gpu")
    if g:
        store.set("gpu_seen", "1")
        out.append(F("gpu:temp", "warn" if g["temp"] >= GPU_TEMP_WARN else "ok", "health.gpu_temp",
                     t=f"{g['temp']:.0f}"))
        out.append(F("gpu", "ok", "health.gpu_ok"))
    elif store.get("gpu_seen") == "1":
        # The failure class this exists for: an unattended NVIDIA driver
        # upgrade without a reboot leaves nvidia-smi unable to talk to the
        # kernel module, and Immich ML + Ollama silently lose the GPU.
        out.append(F("gpu", "crit", "health.gpu_lost"))
    return out, info


# ------------------------------------------------------------------- host --
def _tcp_established():
    n = 0
    for f in ("tcp", "tcp6"):
        try:
            with open(f"{PROC}/1/net/{f}") as fh:
                next(fh)
                n += sum(1 for line in fh if line.split()[3] == "01")
        except (OSError, IndexError, StopIteration):
            pass
    return n


def _wifi():
    try:
        with open(f"{PROC}/1/net/wireless") as fh:
            lines = fh.readlines()[2:]
    except OSError:
        return None
    for line in lines:
        iface, rest = line.split(":", 1)
        cols = rest.split()
        try:
            return iface.strip(), float(cols[2].rstrip("."))
        except (IndexError, ValueError):
            continue
    return None


def _uptime():
    try:
        with open(f"{PROC}/uptime") as fh:
            return float(fh.read().split()[0])
    except (OSError, ValueError):
        return None


def _load():
    try:
        with open(f"{PROC}/loadavg") as fh:
            return fh.read().split()[:3]
    except OSError:
        return None


def check_host():
    info = {"tcp": _tcp_established(), "wifi": _wifi(), "uptime": _uptime(), "load": _load()}
    n = info["tcp"]
    lvl = "crit" if n >= TCP_CRIT else "warn" if n >= TCP_WARN else "ok"
    return [F("tcp", lvl, "health.tcp" if lvl == "ok" else "health.tcp_leak", n=n)], info


# ------------------------------------------------------------- containers --
_seen_restarts = {}


def check_containers():
    out, info = [], {"running": 0, "total": 0, "stopped": []}
    try:
        cs = _json(f"{DOCKER}/containers/json?all=1")
    except Exception as e:                                    # noqa: BLE001
        return [F("docker", "warn", "health.docker_down", e=e)], info
    info["total"] = len(cs)
    for c in cs:
        name = (c.get("Names") or ["?"])[0].lstrip("/")
        state, status = c.get("State", ""), c.get("Status", "")
        if state == "running":
            info["running"] += 1
        key = f"ct:{name}"
        if state == "restarting":
            out.append(F(key, "crit", "health.ct_loop", name=name))
        elif "(unhealthy)" in status:
            out.append(F(key, "crit", "health.ct_unhealthy", name=name))
        elif state in ("exited", "dead"):
            # A stopped container is usually deliberate (docker stop, a
            # one-shot job). Only a non-zero exit that is not a stop signal
            # (137 SIGKILL, 143 SIGTERM) or an OOM kill counts as a crash.
            try:
                st = _json(f"{DOCKER}/containers/{c['Id']}/json")["State"]
            except Exception:                                 # noqa: BLE001
                st = {}
            code, oom = st.get("ExitCode", 0), st.get("OOMKilled", False)
            if oom or code not in (0, 137, 143):
                out.append(F(key, "crit", "health.ct_oom" if oom else "health.ct_exit", name=name, code=code))
            else:
                info["stopped"].append(name)
        else:
            out.append(F(key, "ok", "health.ct_ok", name=name))
        if state == "running":
            # A crash followed by an automatic restart leaves the container
            # "running" -- the restart counter is the only trace.
            try:
                rc = _json(f"{DOCKER}/containers/{c['Id']}/json").get("RestartCount", 0)
            except Exception:                                 # noqa: BLE001
                rc = None
            prev = _seen_restarts.get(name)
            if rc is not None:
                _seen_restarts[name] = rc
                if prev is not None and rc > prev:
                    out.append(F(f"restart:{name}:{rc}", "event", "health.ct_restarted", name=name, rc=rc))
    return out, info


# ------------------------------------------------------------ TLS + routes --
def _tls_connect(host, port, timeout=10):
    """TLS to our own edge with the real SNI. Connecting to the public IP from
    inside the LAN depends on the router's hairpin NAT; the edge is right
    here on the compose network."""
    target = PROXY_HOST if port == 443 else "host.docker.internal"
    ctx = ssl.create_default_context()
    raw = socket.create_connection((target, port), timeout=timeout)
    return ctx.wrap_socket(raw, server_hostname=host)


def check_tls_routes(with_certs=True):
    out, info = [], {"routes_ok": 0, "routes": 0, "certs": []}
    seen_hosts = set()
    for sid, name, host, port, url in hub.own_hosts():
        info["routes"] += 1
        path = urlsplit(url).path or "/"
        try:
            s = _tls_connect(host, port)
        except ssl.SSLCertVerificationError as e:
            out.append(F(f"tls:{host}:{port}", "crit", "health.tls_bad", name=name, msg=e.verify_message))
            continue
        except OSError as e:
            out.append(F(f"route:{sid}", "crit", "health.route_down", name=name, e=e))
            continue
        try:
            if with_certs and (host, port) not in seen_hosts:
                seen_hosts.add((host, port))
                exp = ssl.cert_time_to_seconds(s.getpeercert()["notAfter"])
                days = (exp - time.time()) / 86400
                info["certs"].append((days, host if port == 443 else f"{host}:{port}"))
                lvl = "crit" if days < CERT_CRIT_D else "warn" if days < CERT_WARN_D else "ok"
                out.append(F(f"cert:{host}:{port}", lvl, "health.cert",
                             host=f"{host}{'' if port == 443 else ':' + str(port)}", days=f"{days:.0f}"))
            s.sendall(f"GET {path} HTTP/1.1\r\nHost: {host}\r\nUser-Agent: meowhub-helper\r\n"
                      f"Connection: close\r\n\r\n".encode())
            line = s.recv(256).split(b"\r\n", 1)[0].decode(errors="replace")
            code = int(line.split()[1]) if len(line.split()) > 1 else 0
        except (OSError, ValueError) as e:
            out.append(F(f"route:{sid}", "crit", "health.route_noans", name=name, e=e))
            continue
        finally:
            s.close()
        if code >= 500 or code == 0:
            out.append(F(f"route:{sid}", "crit", "health.route_http", name=name, code=code))
        else:
            info["routes_ok"] += 1
            out.append(F(f"route:{sid}", "ok", "health.route_ok", name=name, code=code))
    return out, info


# -------------------------------------------------------------------- DNS --
def public_ip():
    for u in IP_SOURCES:
        try:
            with urllib.request.urlopen(urllib.request.Request(
                    u, headers={"User-Agent": "curl/8"}), timeout=8) as r:
                ip = r.read().decode().strip()
            socket.inet_aton(ip)
            return ip
        except Exception:                                     # noqa: BLE001
            continue
    return None


def check_dns():
    """Our A records must point at our public IP. The DNS here is updated by
    hand; if the ISP hands out a new address, every service and both VPNs
    silently go dark -- this is the early warning."""
    info = {"ip": public_ip(), "hosts": []}
    ip = info["ip"]
    if not ip:
        return [F("ip", "warn", "health.ip_unknown")], info
    hosts = sorted({h for _, _, h, _, _ in hub.own_hosts()} | ({hub.base_domain()} - {""}))
    bad = []
    for h in hosts:
        try:
            addrs = sorted({a[4][0] for a in socket.getaddrinfo(h, None, socket.AF_INET)})
        except OSError:
            addrs = []
        info["hosts"].append((h, addrs))
        if ip not in addrs:
            bad.append((h, addrs))
    if bad:
        # the "doesn't resolve" wording is language-dependent: render the list per language
        ru = "; ".join(f"{h} → {', '.join(a) or i18n.tr('ru', 'health.dns_none')}" for h, a in bad)
        en = "; ".join(f"{h} → {', '.join(a) or i18n.tr('en', 'health.dns_none')}" for h, a in bad)
        f = F("dns", "crit", "health.dns_bad", ip=ip, bad=ru)
        f.args = {"ip": ip, "bad": {"ru": ru, "en": en}}
        return [f], info
    return [F("dns", "ok", "health.dns_ok", ip=ip)], info


# ----------------------------------------------------------------- report --
def full(store):
    """Run everything now -> (findings, info) for the /health report."""
    findings, info = [], {}
    for fn in (lambda: check_system(store), check_host, check_containers,
               check_tls_routes, check_dns):
        try:
            f, i = fn()
        except Exception as e:                                # noqa: BLE001
            log.exception("check failed")
            f, i = [F(f"check:{getattr(fn, '__name__', '?')}", "warn", "health.check_failed", e=e)], {}
        findings += f
        info.update(i)
    return findings, info


def _dur(sec, lang="ru"):
    d, h = int(sec // 86400), int(sec % 86400 // 3600)
    return i18n.tr(lang, "health.dur_dh", d=d, h=h) if d else i18n.tr(lang, "health.dur_hm", h=h, m=int(sec % 3600 // 60))


def render(findings, info, lang="ru"):
    tr = lambda key, **kw: i18n.tr(lang, key, **kw)                  # noqa: E731
    probs = [f for f in findings if f.level in ("warn", "crit")]
    crit = sum(1 for f in probs if f.level == "crit")
    if not probs:
        head = tr("health.head_ok")
    elif crit:
        head = tr("health.head_crit", n=crit) + (tr("health.head_crit_warn", n=len(probs) - crit)
                                                 if len(probs) > crit else "")
    else:
        head = tr("health.head_warn", n=len(probs))
    lines = [head]
    for f in sorted(probs, key=lambda f: f.level != "crit"):
        lines.append(f"{'🔴' if f.level == 'crit' else '⚠️'} {esc(f.t(lang))}")
    lines = ["\n".join(lines)]

    s = info.get("stats") or {}
    sysl = []
    cpu = s.get("cpu") or {}
    load = info.get("load")
    if cpu:
        t = f" · {cpu['temp']:.0f}°C" if cpu.get("temp") is not None else ""
        sysl.append(f"CPU {cpu.get('percent', 0):.0f}%{t}" + (f" · load {load[0]}" if load else ""))
    if s.get("mem"):
        m = s["mem"]
        sysl.append(f"RAM {gb(m['used'])}/{gb(m['total'])} GB ({m['percent']:.0f}%)")
    for k, n in (("disk", "SSD"), ("hdd", "HDD")):
        if s.get(k):
            d = s[k]
            sysl.append(f"{n} {gb(d['used'])}/{gb(d['total'])} GB ({d['percent']:.0f}%)")
    g = s.get("gpu")
    if g:
        sysl.append(f"GPU {g['percent']:.0f}% · VRAM {gb(g['memUsed'])}/{gb(g['memTotal'])} GB · {g['temp']:.0f}°C")
    if info.get("uptime"):
        sysl.append(tr("health.uptime", v=_dur(info["uptime"], lang)))
    if sysl:
        lines.append(tr("health.sys") + "\n" + "\n".join(sysl))

    netl = []
    if info.get("wifi"):
        iface, lvl = info["wifi"]
        q = tr("health.wifi_q4" if lvl > -55 else "health.wifi_q3" if lvl > -67
               else "health.wifi_q2" if lvl > -75 else "health.wifi_q1")
        netl.append(f"Wi-Fi {iface}: {lvl:.0f} dBm ({q})")
    if "tcp" in info:
        netl.append(tr("health.tcp_line", n=info["tcp"]))
    if info.get("ip"):
        netl.append(tr("health.ip_line", ip=info["ip"]))
    if netl:
        lines.append(tr("health.net") + "\n" + "\n".join(netl))

    if info.get("total"):
        t = tr("health.ct_head", run=info["running"], total=info["total"])
        if info.get("stopped"):
            t += "\n" + tr("health.ct_stopped", v=", ".join(info["stopped"]))
        lines.append(t)
    if info.get("routes"):
        t = tr("health.sites_head", ok=info["routes_ok"], n=info["routes"])
        if info.get("certs"):
            days, host = min(info["certs"])
            t += "\n" + tr("health.cert_soon", days=f"{days:.0f}", host=host)
        lines.append(t)
    lines.append(f"<i>{datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC</i>")
    return "\n\n".join(lines)


def esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
