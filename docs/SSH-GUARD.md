# SSH guard: fail2ban + Telegram alerts

Optional, host-level (outside compose). If your server's SSH is reachable from the
internet, scanners will guess passwords all day; on the reference install that was
about 7,300 failed attempts a day from about 40 addresses before this was set up.
This bans them with fail2ban (root and made-up usernames on the first try) and tells you on
Telegram through the helper bot: at once only about a login from a new address or guesses at a
real account, everything else as a short 🛡 summary in the daily report.

```bash
sudo apt install fail2ban
sudo install -m 755 scripts/ssh-watch.py /usr/local/sbin/ssh-watch     # first: the jails ask it
sudo /usr/bin/python3 -I /usr/local/sbin/ssh-watch --seed "90 days ago"  # your past logins: never banned
sudo install -m 644 scripts/fail2ban/sshd-instant.conf /etc/fail2ban/filter.d/
sudo install -m 644 scripts/fail2ban/sshd-meowhub.local /etc/fail2ban/jail.d/
sudo systemctl restart fail2ban
sed "s|__REPO__|$PWD|" scripts/ssh-watch.service | sudo tee /etc/systemd/system/ssh-watch.service >/dev/null
sudo systemctl daemon-reload && sudo systemctl enable --now ssh-watch
```

## fail2ban (host package)

- Config: `/etc/fail2ban/jail.d/sshd-meowhub.local`, with the source in
  `scripts/fail2ban/sshd-meowhub.local`. Ubuntu's `defaults-debian.conf` sets
  `backend = systemd` and `banaction = nftables`.
- **Jail `sshd-instant`** (filter `scripts/fail2ban/sshd-instant.conf`): a password for
  `root` or **any user that doesn't exist** is banned on the **first** try, 1 day doubling
  up to **4 weeks**. On the reference install that was 100 % of failures (root and made-up
  names; none on the real account). Port 22 only. If you log in as root by password, remove
  the root line from the filter first.
- Jail `sshd`, `mode = aggressive`. The aggressive mode also counts invalid users
  and pre-auth disconnects, not only wrong passwords.
- **5 failures within 10 min → ban.** The ban is 1 h at first and doubles for each
  repeat ban of the same address (`bantime.increment`), up to **1 week**.
- **Never banned** (`ignoreip`): loopback, private LAN ranges and the docker
  bridges. Adjust the LAN range to yours. Also (`ignorecommand = ssh-watch --is-known <ip>`)
  every address with a successful login in the last 90 days, kept by ssh-watch in
  `/var/lib/ssh-watch/known.json`, so a typo from your usual networks can't lock you out.
- Bans live in the nftables table `inet f2b-table`. That table is fail2ban's own,
  separate from Docker's iptables-nft rules, and it rejects only `tcp dport 22`.
- From a **new** network, a mistyped username bans that address for a day and five wrong
  passwords for an hour: send the bot `/sshunban <ip>`, use another network, or wait.

```bash
sudo fail2ban-client status sshd              # counts + banned list
sudo fail2ban-client set sshd unbanip <ip>    # lift a ban
sudo fail2ban-client get sshd banip --with-time
```

## Telegram alerts

```
journal (sshd) ─► ssh-watch.service (root) ─► helper/data/ssh-events.jsonl ─► helper bot Monitor ─► owner
                        ▲                                                            │
                        └──── helper/data/ssh-requests.jsonl ◄── /sshunban, 🚫 button ┘
```

**`ssh-watch`** is `scripts/ssh-watch.py`. It is installed as a root-owned copy in
`/usr/local/sbin/ssh-watch`, with the unit `scripts/ssh-watch.service` →
`/etc/systemd/system/` (with `__REPO__` replaced).
- It follows `journalctl -f _COMM=sshd`.
- It counts `Failed … for <user> from <ip>` and `Invalid user <user> from <ip>`
  lines per address, marking an address `junk` when every attempt was on root or a
  made-up user, and records `Accepted … for <user> from <ip>` logins (`new` when the
  address isn't in `known.json`).
- Once a minute it appends one JSON line: addresses (attempts, usernames, banned, junk),
  logins, and how many addresses each jail bans right now.
- It carries out the bot's `{"unban": ip}` / `{"ban": ip}` requests from
  `ssh-requests.jsonl` next to the feed (only root can talk to fail2ban).
- When nothing happens, it writes a heartbeat line every 10 min.
- It rotates the file at 1 MB.
- Dependency-free, and it holds no token.

**After editing the script**, reinstall it and restart the service:
`sudo install -m 755 scripts/ssh-watch.py /usr/local/sbin/ssh-watch && sudo systemctl restart ssh-watch`.

**The bot** (`Monitor.ssh_tick` in `helper/app/bot.py`, run every 60 s; it does nothing while the feed file doesn't exist) reads the
new lines of the feed.
- **At once:** 🔑 a login from a **new** address (with a 🚫 "Not me — ban it" button), and
  🚨 failed guesses at a **real** account, batched, each address at most once a day.
- **Everything else** goes only into the **🛡 SSH summary at the end of the daily report**
  (`HELPER_REPORT_HOUR`): attempts, addresses, banned, most-tried usernames, banned right now,
  and who logged in from where.
- On its very first read it skips the existing history.
- If the feed hasn't been written for 30 min, the owner gets "ssh-watch молчит"
  as a normal health warning.

**Muting**: the 🔕 button under an alert, or `/sshalerts off` / `/sshalerts on`
(owner only, helper bot); off also drops the summary. **Unban**: `/sshunban <ip>`.

**Test** the request path with a documentation address:
`echo '{"ban": "192.0.2.1"}' >> helper/data/ssh-requests.jsonl`, a minute later
`sudo fail2ban-client get sshd-instant banip` lists it; the same with `unban` lifts it.

**Rollback**:
`sudo systemctl disable --now ssh-watch fail2ban`.
