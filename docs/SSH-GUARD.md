# SSH guard: fail2ban + Telegram alerts

Optional, host-level (outside compose). If your server's SSH is reachable from the
internet, scanners will guess passwords all day; on the reference install that was
about 7,300 failed attempts a day from about 40 addresses before this was set up.
This bans them with fail2ban and tells you on Telegram through the helper bot.

```bash
sudo apt install fail2ban
sudo install -m 644 scripts/fail2ban/sshd-meowhub.local /etc/fail2ban/jail.d/
sudo systemctl restart fail2ban
sudo install -m 755 scripts/ssh-watch.py /usr/local/sbin/ssh-watch
sed "s|__REPO__|$PWD|" scripts/ssh-watch.service | sudo tee /etc/systemd/system/ssh-watch.service >/dev/null
sudo systemctl daemon-reload && sudo systemctl enable --now ssh-watch
```

## fail2ban (host package)

- Config: `/etc/fail2ban/jail.d/sshd-meowhub.local`, with the source in
  `scripts/fail2ban/sshd-meowhub.local`. Ubuntu's `defaults-debian.conf` sets
  `backend = systemd` and `banaction = nftables`.
- Jail `sshd`, `mode = aggressive`. The aggressive mode also counts invalid users
  and pre-auth disconnects, not only wrong passwords.
- **5 failures within 10 min → ban.** The ban is 1 h at first and doubles for each
  repeat ban of the same address (`bantime.increment`), up to **1 week**.
- **Never banned** (`ignoreip`): loopback, private LAN ranges and the docker
  bridges. Adjust the LAN range to yours.
- Bans live in the nftables table `inet f2b-table`. That table is fail2ban's own,
  separate from Docker's iptables-nft rules, and it rejects only `tcp dport 22`.
- Your own logins are not affected while they succeed on the first try. Five
  mistyped passwords in 10 min lock your IP out for an hour: use another network,
  or wait.

```bash
sudo fail2ban-client status sshd              # counts + banned list
sudo fail2ban-client set sshd unbanip <ip>    # lift a ban
sudo fail2ban-client get sshd banip --with-time
```

## Telegram alerts

```
journal (sshd) ─► ssh-watch.service (root) ─► helper/data/ssh-events.jsonl ─► helper bot Monitor ─► owner
```

**`ssh-watch`** is `scripts/ssh-watch.py`. It is installed as a root-owned copy in
`/usr/local/sbin/ssh-watch`, with the unit `scripts/ssh-watch.service` →
`/etc/systemd/system/` (with `__REPO__` replaced).
- It follows `journalctl -f _COMM=sshd`.
- It counts `Failed … for <user> from <ip>` and `Invalid user <user> from <ip>`
  lines per address.
- Once a minute it appends one JSON line with the addresses seen, how many
  attempts each made, the usernames tried, and whether fail2ban currently bans
  that address.
- When nothing happens, it writes a heartbeat line every 10 min.
- It rotates the file at 1 MB.
- Dependency-free, and it holds no token.

**After editing the script**, reinstall it and restart the service:
`sudo install -m 755 scripts/ssh-watch.py /usr/local/sbin/ssh-watch && sudo systemctl restart ssh-watch`.

**The bot** (`Monitor.ssh_tick` in `helper/app/bot.py`, run every 60 s; it does nothing while the feed file doesn't exist) reads the
new lines of the feed.
- It sends **one batched message** listing every address it hasn't reported in
  the last 24 h: IP, attempts, usernames and 🚫 when banned. A message lists 15
  addresses at most.
- Later attempts from an already-reported address stay quiet until a day has
  passed.
- On its very first read it skips the existing history.
- If the feed hasn't been written for 30 min, the owner gets "ssh-watch молчит"
  as a normal health warning.

**Muting**: the 🔕 button under an alert, or `/sshalerts off` / `/sshalerts on`
(owner only, helper bot). Stored as `ssh_alerts` in `helper.db` settings.

**Test** (localhost is never banned):
`ssh -o BatchMode=yes f2btest@127.0.0.1 true`. An alert for `127.0.0.1` arrives
within about 2 min.

**Rollback**:
`sudo systemctl disable --now ssh-watch fail2ban`.
