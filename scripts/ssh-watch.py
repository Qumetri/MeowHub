#!/usr/bin/env python3
"""SSH login feed for the helper bot (docs/SSH-GUARD.md).

Runs on the host as root (systemd unit ssh-watch.service), follows sshd in the
journal and once a minute appends one JSON line to the helper's data dir:

    {"ts": 1791727000,
     "ips": [{"ip": "1.2.3.4", "n": 7, "users": ["root"], "banned": true, "junk": true}],
     "logins": [{"ip": "5.6.7.8", "user": "server", "method": "password", "new": false}],
     "jails": {"sshd": 3, "sshd-instant": 41}}

"junk" means every attempt from that address was on root or on a user that does
not exist: fail2ban's sshd-instant jail bans those on the first try and the bot
only counts them in the daily summary. A successful login from an address not
seen in KNOWN_DAYS is "new"; the bot alerts on those at once.

A line with empty lists is written every BEAT seconds as a heartbeat, so the bot
can tell a quiet hour from a dead watcher. The bot owns all Telegram logic
(language, mute, summary); this side only extracts and aggregates.

Two small jobs ride along:
  - KNOWN keeps the addresses of successful logins. fail2ban asks
    `ssh-watch --is-known <ip>` (ignorecommand) and never bans them, so a typo
    from your own address can't lock you out.
  - The bot asks for unbans and bans by appending {"unban": "<ip>"} / {"ban": "<ip>"}
    lines to REQUESTS; they are carried out here, because only root can talk to fail2ban.

Dependency-free, like stats/server.py.
"""
import ipaddress
import json
import os
import re
import subprocess
import sys
import threading
import time

OUT = (sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("--")
       else "helper/data/ssh-events.jsonl")   # pass the absolute path
REQUESTS = os.path.join(os.path.dirname(OUT), "ssh-requests.jsonl")   # next to the feed, in the bot's /data
KNOWN = "/var/lib/ssh-watch/known.json"   # ip -> last successful login (unix time)
KNOWN_DAYS = 90
OWNER = (1000, 1000)                      # the helper container's uid/gid
FLUSH, BEAT, MAX_BYTES, MAX_USERS = 60, 600, 1_000_000, 8
JAILS = ("sshd", "sshd-instant")

FAILED = re.compile(r"Failed \S+ for (invalid user )?(\S*) from ([0-9A-Fa-f.:]+) port")
INVALID = re.compile(r"Invalid user (\S*) from ([0-9A-Fa-f.:]+)")
ACCEPTED = re.compile(r"Accepted (\S+) for (\S+) from ([0-9A-Fa-f.:]+) port")

lock = threading.Lock()
agg = {}                                  # ip -> {"failed": n, "invalid": n, "users": [..], "real": bool}
logins = []


def load_known():
    try:
        with open(KNOWN) as f:
            return {k: float(v) for k, v in json.load(f).items()}
    except (OSError, ValueError, AttributeError):
        return {}


def save_known(known):
    cut = time.time() - KNOWN_DAYS * 86400
    known = {ip: t for ip, t in known.items() if t >= cut}
    os.makedirs(os.path.dirname(KNOWN), mode=0o700, exist_ok=True)
    tmp = KNOWN + ".tmp"
    with open(tmp, "w") as f:
        json.dump(known, f)
    os.replace(tmp, KNOWN)


def is_known(ip):
    t = load_known().get(ip)
    return t is not None and t >= time.time() - KNOWN_DAYS * 86400


def follow():
    cmd = ["journalctl", "-f", "-n", "0", "-o", "cat", "_COMM=sshd", "_COMM=sshd-session"]
    while True:
        with subprocess.Popen(cmd, stdout=subprocess.PIPE, text=True, errors="replace") as p:
            for line in p.stdout:
                m = ACCEPTED.search(line)
                if m:
                    method, user, ip = m.groups()
                    known = load_known()
                    new = ip not in known
                    known[ip] = time.time()
                    save_known(known)
                    with lock:
                        logins.append({"ip": ip, "user": user, "method": method, "new": new})
                    continue
                m = FAILED.search(line)
                kind = "failed"
                if m:
                    invalid, user, ip = m.groups()
                    real = not invalid and user != "root"     # a real account: someone guessing it
                else:
                    m = INVALID.search(line)
                    if not m:
                        continue
                    user, ip = m.groups()
                    kind, real = "invalid", False
                with lock:
                    e = agg.setdefault(ip, {"failed": 0, "invalid": 0, "users": [], "real": False})
                    e[kind] += 1
                    e["real"] = e["real"] or real
                    if user and user not in e["users"] and len(e["users"]) < MAX_USERS:
                        e["users"].append(user)
        time.sleep(5)                     # journalctl exited: start it again


def f2b(*args):
    try:
        return subprocess.run(["fail2ban-client", *args], capture_output=True, text=True,
                              timeout=15).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def banned():
    """jail -> set of banned addresses."""
    return {jail: set(f2b("get", jail, "banip").split()) for jail in JAILS}


def requests_tick():
    """Carry out the bot's unban requests, then empty the file."""
    try:
        with open(REQUESTS) as f:
            lines = f.read().splitlines()
        os.truncate(REQUESTS, 0)
    except OSError:
        return
    for line in lines:
        try:
            req = json.loads(line)
            action = "ban" if "ban" in req else "unban"
            ip = str(ipaddress.ip_address(req.get(action, "")))
        except (ValueError, AttributeError, TypeError):
            continue
        if action == "unban":
            for jail in JAILS:
                f2b("set", jail, "unbanip", ip)
        else:
            # "Not me": forget the address first, or ignorecommand would protect it.
            known = load_known()
            known.pop(ip, None)
            save_known(known)
            f2b("set", "sshd-instant", "banip", ip)
        print(f"ssh-watch: {action} {ip} on the bot's request", flush=True)


def write(rec):
    if os.path.exists(OUT) and os.path.getsize(OUT) > MAX_BYTES:
        os.replace(OUT, OUT + ".1")      # the bot notices the new inode and starts over
    new = not os.path.exists(OUT)
    with open(OUT, "a") as f:
        f.write(json.dumps(rec, separators=(",", ":")) + "\n")
    if new:
        os.chmod(OUT, 0o644)
        os.chown(OUT, *OWNER)


def main():
    threading.Thread(target=follow, daemon=True).start()
    last = 0
    while True:
        time.sleep(FLUSH)
        requests_tick()
        with lock:
            batch, done = dict(agg), list(logins)
            agg.clear()
            logins.clear()
        now = int(time.time())
        if not batch and not done and now - last < BEAT:
            continue
        ban = banned()
        every = set().union(*ban.values())
        # A bot that quits at the username prompt leaves only "Invalid user" lines.
        count = lambda e: e["failed"] or e["invalid"]                       # noqa: E731
        ips = [{"ip": ip, "n": count(e), "users": e["users"], "banned": ip in every, "junk": not e["real"]}
               for ip, e in sorted(batch.items(), key=lambda kv: -count(kv[1]))]
        try:
            write({"ts": now, "ips": ips, "logins": done, "jails": {j: len(s) for j, s in ban.items()}})
            last = now
        except OSError as e:
            print(f"ssh-watch: write failed: {e}", file=sys.stderr, flush=True)


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--is-known":
        sys.exit(0 if is_known(sys.argv[2]) else 1)   # fail2ban ignorecommand: 0 = never ban
    if len(sys.argv) == 3 and sys.argv[1] == "--seed":
        # Addresses of successful logins in the journal so far (journalctl --since value).
        out = subprocess.run(["journalctl", "-o", "short-unix", "--since", sys.argv[2],
                              "_COMM=sshd", "_COMM=sshd-session"], capture_output=True, text=True).stdout
        known = load_known()
        for line in out.splitlines():
            m = ACCEPTED.search(line)
            if m:
                known[m.group(3)] = max(known.get(m.group(3), 0), float(line.split()[0]))
        save_known(known)
        print(f"{len(known)} known addresses")
        sys.exit(0)
    main()
