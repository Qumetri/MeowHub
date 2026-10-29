#!/usr/bin/env python3
"""Keep the FreeDNS (afraid.org) A records on the server's current public IP.

Runs from the server user's crontab every 5 minutes. Quiet while nothing has
changed; when the ISP hands out a new address it calls every record's update
URL, restarts LiveKit (it learns the external IP only at startup) and tells the
owner through tg-report.py.

Config in the stack's .env (one of the two):
  FREEDNS_SHA          sha1("<username lowercase>|<password>") -- the script
                       asks the FreeDNS API for every dynamic record and its
                       update URL, so new subdomains are picked up by themselves
  FREEDNS_UPDATE_URLS  space-separated "Direct URL"s from the Dynamic DNS page,
                       each optionally prefixed "host=" to name it in messages

  ddns-update.py            normal run (cron)
  ddns-update.py --check    print what it sees, change nothing
  ddns-update.py --force    push the update URLs even if nothing changed
"""
import fcntl
import ipaddress
import json
import os
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ENV = os.path.join(HERE, '..', '.env')
STATE = os.path.join(HERE, 'ddns-state.json')
REPORT = os.path.join(HERE, 'tg-report.py')
# IPv4-only echo services: the host also has IPv6, and a v6 answer is no use for an A record
IP_SOURCES = ('https://api.ipify.org', 'https://ipv4.icanhazip.com', 'https://v4.ident.me')
API = 'https://freedns.afraid.org/api/?action=getdyndns&v=2&sha='
REPUSH = 24 * 3600        # re-send once a day anyway, in case a record was edited by hand


def log(msg):
    print(time.strftime('%Y-%m-%d %H:%M:%S'), msg, flush=True)


def load_env():
    cfg = {}
    with open(ENV) as f:
        for line in f:
            if '=' in line and not line.lstrip().startswith('#'):
                k, v = line.rstrip('\n').split('=', 1)
                cfg[k.strip()] = v.strip().strip('"\'')
    return cfg


def get(url, timeout=15):
    req = urllib.request.Request(url, headers={'User-Agent': 'meowhub-ddns/1'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode(errors='replace').strip()


def public_ip():
    """Two sources must agree -- one flaky echo service must never repoint DNS."""
    seen = []
    for u in IP_SOURCES:
        try:
            ip = str(ipaddress.IPv4Address(get(u, 8)))
        except Exception:                                     # noqa: BLE001
            continue
        if ip in seen:
            return ip
        seen.append(ip)
    return None


def records(cfg):
    """-> [(host, ip_in_dns or None, update_url)]"""
    if cfg.get('FREEDNS_SHA'):
        out = []
        for line in get(API + cfg['FREEDNS_SHA']).splitlines():
            parts = line.split('|')
            if len(parts) >= 3 and parts[2].startswith('http'):
                out.append((parts[0], parts[1], parts[2]))
        if not out:
            raise RuntimeError('FreeDNS API returned no records (wrong FREEDNS_SHA?)')
        return out
    urls = cfg.get('FREEDNS_UPDATE_URLS', '').split()
    if not urls:
        raise RuntimeError('set FREEDNS_SHA or FREEDNS_UPDATE_URLS in .env')
    out = []
    for i, u in enumerate(urls):
        host, _, url = u.rpartition('=http')
        out.append((host or f'record {i + 1}', None, 'http' + url if host else u))
    return out


def with_ip(url, ip):
    """Name the address explicitly: left to FreeDNS it would take the request's
    source address, which can be the host's IPv6."""
    if 'sync.afraid.org' in url:
        return url + ('&' if '?' in url else '?') + 'ip=' + ip
    return url + ('&' if '?' in url else '?') + 'address=' + ip


def load_state():
    try:
        with open(STATE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(st):
    tmp = STATE + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(st, f)
    os.replace(tmp, STATE)


def notify(text):
    try:
        subprocess.run([sys.executable, REPORT, text], timeout=320, check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:                                    # noqa: BLE001
        log(f'telegram failed: {e}')


def main():
    check = '--check' in sys.argv
    force = '--force' in sys.argv
    lock = open(STATE + '.lock', 'w')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return 0
    cfg = load_env()
    if not (cfg.get('FREEDNS_SHA') or cfg.get('FREEDNS_UPDATE_URLS')) and not check:
        return 0                                  # not set up yet: stay quiet in cron
    ip = public_ip()
    if not ip:
        log('public IP unknown (sources disagree or are down) -- skipped')
        return 0
    st = load_state()
    try:
        recs = records(cfg)
    except Exception as e:                                    # noqa: BLE001
        log(f'no records: {e}')
        return 1
    stale = [h for h, dns_ip, _ in recs if dns_ip and dns_ip != ip]
    changed = ip != st.get('ip')
    due = time.time() - st.get('pushed', 0) > REPUSH
    if check:
        log(f'public {ip}, last pushed {st.get("ip")}')
        for h, dns_ip, _ in recs:
            log(f'  {h}: {dns_ip or "?"}')
        return 0
    if not (force or changed or stale or due):
        return 0

    failed = []
    for h, _, url in recs:
        try:
            res = get(with_ip(url, ip))
            log(f'{h}: {res[:120]}')
            # an unchanged address is answered "ERROR: Address ... has not changed."
            if 'ERROR' in res.upper() and not any(k in res.upper() for k in ('NO IP CHANGE', 'HAS NOT CHANGED')):
                failed.append(h)
        except Exception as e:                                # noqa: BLE001
            log(f'{h}: update failed: {e}')
            failed.append(h)
    old = st.get('ip')
    if not failed:
        st.update(ip=ip, pushed=time.time())
        save_state(st)
    moved = old and old != ip
    if moved and not failed:
        # containers that learn the public IP only at startup (DDNS_RESTART overrides)
        for c in (cfg.get('DDNS_RESTART') or cfg.get('STACK_NAME', 'meowstack') + '_livekit').split():
            subprocess.run(['docker', 'restart', c], check=False,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if failed:
        notify(f'🌐 DDNS: could not point {", ".join(failed)} at {ip}. '
               f'Log: scripts/ddns-update.log')
    elif moved or stale:
        names = ', '.join(h for h, _, _ in recs)
        notify(f'🌐 Public IP changed: {old or "?"} → {ip}. DNS updated: {names}. '
               f'Resolvers pick up the new address within the record TTL.')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
