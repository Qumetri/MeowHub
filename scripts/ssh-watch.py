#!/usr/bin/env python3
"""Failed-SSH-login feed for the helper bot (docs/SSH-GUARD.md).

Runs on the host as root (systemd unit ssh-watch.service), follows sshd in the
journal and once a minute appends one JSON line to the helper's data dir:

    {"ts": 1791727000, "ips": [{"ip": "1.2.3.4", "n": 7, "users": ["root"], "banned": true}]}

A line with an empty "ips" list is written every BEAT seconds as a heartbeat, so
the bot can tell a quiet hour from a dead watcher. The bot owns all Telegram
logic (dedupe, language, mute); this side only extracts and aggregates.
Dependency-free, like stats/server.py.
"""
import json
import os
import re
import subprocess
import sys
import threading
import time

OUT = sys.argv[1] if len(sys.argv) > 1 else "helper/data/ssh-events.jsonl"   # pass the absolute path
OWNER = (1000, 1000)                 # the helper container's uid/gid
FLUSH, BEAT, MAX_BYTES, MAX_USERS = 60, 600, 1_000_000, 8

FAILED = re.compile(r"Failed \S+ for (?:invalid user )?(\S*) from ([0-9A-Fa-f.:]+) port")
INVALID = re.compile(r"Invalid user (\S*) from ([0-9A-Fa-f.:]+)")

lock = threading.Lock()
agg = {}                             # ip -> {"failed": n, "invalid": n, "users": [..]}


def follow():
    cmd = ["journalctl", "-f", "-n", "0", "-o", "cat", "_COMM=sshd", "_COMM=sshd-session"]
    while True:
        with subprocess.Popen(cmd, stdout=subprocess.PIPE, text=True, errors="replace") as p:
            for line in p.stdout:
                m = FAILED.search(line)
                kind = "failed"
                if not m:
                    m = INVALID.search(line)
                    kind = "invalid"
                if not m:
                    continue
                user, ip = m.group(1), m.group(2)
                with lock:
                    e = agg.setdefault(ip, {"failed": 0, "invalid": 0, "users": []})
                    e[kind] += 1
                    if user and user not in e["users"] and len(e["users"]) < MAX_USERS:
                        e["users"].append(user)
        time.sleep(5)                # journalctl exited: start it again


def banned():
    try:
        out = subprocess.run(["fail2ban-client", "get", "sshd", "banip"], capture_output=True,
                             text=True, timeout=15).stdout
        return set(out.split())
    except (OSError, subprocess.SubprocessError):
        return set()


def write(rec):
    if os.path.exists(OUT) and os.path.getsize(OUT) > MAX_BYTES:
        os.replace(OUT, OUT + ".1")  # the bot notices the new inode and starts over
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
        with lock:
            batch = dict(agg)
            agg.clear()
        now = int(time.time())
        if not batch and now - last < BEAT:
            continue
        ban = banned() if batch else set()
        ips = [{"ip": ip, "n": e["failed"] or e["invalid"], "users": e["users"], "banned": ip in ban}
               for ip, e in sorted(batch.items(), key=lambda kv: -(kv[1]["failed"] or kv[1]["invalid"]))]
        try:
            write({"ts": now, "ips": ips})
            last = now
        except OSError as e:
            print(f"ssh-watch: write failed: {e}", file=sys.stderr, flush=True)


if __name__ == "__main__":
    main()
