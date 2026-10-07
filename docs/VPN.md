# VPN paths

Two independent VPNs, both optional, both separate Compose projects so a
mistake in either cannot touch the main stack.

| | AmneziaWG | 3x-ui |
|---|---|---|
| Protocol | Obfuscated WireGuard (UDP) | VLESS/Reality, Hysteria2, Shadowsocks, more |
| Transport | UDP on its own port | TCP, can ride :443 by SNI |
| Client | AmneziaWG app (stock WireGuard will **not** work) | Any Xray client (Hiddify, Happ, v2rayN) |
| Config delivery | `.conf` file or QR | Subscription URL |
| Managed by | `awg-client.sh` + web UI | Its own web panel |

Run each from inside its own directory:

```bash
cd amneziawg && docker compose up -d
cd 3xpanel   && docker compose up -d
```

---

## AmneziaWG

### Setup

```bash
cd amneziawg
./awg-init.sh            # once: server keypair + server-side obfuscation
docker compose up -d
./awg-client.sh add alice
```

`awg-init.sh` refuses to run twice. The values it writes into
`config/params.env` — `S1-S4` and `H1-H4` — must be byte-identical on the
server and every client. Regenerating them invalidates every config already
handed out, which is why it is a one-time operation and why that file is the
most important thing to back up.

Forward `AWG_PORT` (default 20443/udp) on the router.

### Managing peers

```bash
./awg-client.sh add <name>      # create, apply live, print config + QR
./awg-client.sh list            # peers with handshake and transfer
./awg-client.sh show <name>     # re-print an existing config
./awg-client.sh remove <name>   # revoke, live
```

Changes are applied with `awg set` over the UAPI socket, so adding or revoking
a peer **never disconnects anyone else**.

There is also a web UI at `https://<domain>/<AWG_ADMIN_PATH>/`, behind basic
auth as `AWG_ADMIN_USER`. Password is in `secrets/awg-admin-password`. It and
the CLI share `awg0.conf` under an `flock`, so they cannot corrupt each other.

### Obfuscation: which settings are free, and which are a flag day

AmneziaWG splits its parameters into two classes, and the split governs
everything about how you roll out changes:

| Class | Parameters | Must match? |
|---|---|---|
| **server-side** | `S1-S4`, `H1-H4`, `HeaderProtectionKey` | **yes, byte-identical** |
| **client-side** | `Jc/Jmin/Jmax`, `I1-I5`, `ContentPaddingAddition`, `Rekey*`, `Reject*`, `Keepalive*`, `MaxHandshakeAttempts` | no |

Client-side parameters can differ per peer and the server neither needs nor
notices them. That means you can hand one user an upgraded config without
touching the server and without breaking anyone still on an old one.

Server-side parameters are the opposite: adding `HeaderProtectionKey` to a
running server drops every existing peer at once. If you want it, stand up a
*second* instance on another port and migrate people one at a time.

`config/awg31.py` generates the client-side block, fresh per client. Both the
CLI and the web UI import it, so they cannot drift. The per-client randomisation
is deliberate: with one shared set every peer emits an identical flow signature,
and a signature burned on one user describes all of them.

### Signature packets (I1/I2)

`I1`/`I2` imitate a QUIC v1 client Initial — a real 1200-byte datagram shape
with correct `Length` field, with the connection IDs and ciphertext supplied by
`<r N>` random tags. Sent before each handshake, they make the opening of a
session look like ordinary QUIC rather than an unclassifiable UDP blob.

`Jc` is set to 0 alongside them on purpose: junk packets are small random blobs,
and "QUIC Initial + N random blobs" is less coherent than either alone.

To re-roll signatures after one is blocked, regenerate the client configs. No
server change is needed and old configs keep working until each user switches.

### QR codes have a size limit

Configs are minified before being encoded — comments and alignment stripped —
because the full file needs a much denser QR than phone cameras reliably scan.
A partial decode does not report itself as a scan failure: the app receives a
config starting mid-file and complains about an *unknown section*, which sends
you hunting through the config text for a problem that isn't there.

`qr_payload()` in `config/awg31.py` handles this. If you add fields to the
config template, check the QR still scans.

### Client apps

The **AmneziaWG** app, not stock WireGuard — the obfuscation is a protocol
change WireGuard cannot parse. Signature packets need app **v3.0.1 or newer**;
older builds reject a config containing `I1`.

---

## 3x-ui

A general Xray panel: VLESS/Reality, Hysteria2, Shadowsocks, AmneziaWG and
others, with subscription links.

```bash
cd 3xpanel && docker compose up -d
docker compose logs 3xui | head    # first-run credentials
```

Host networking, on purpose: Xray binds many ports and Reality inbounds need
real source addresses. It is therefore **not** behind Caddy and serves its own
HTTPS on `XUI_PANEL_PORT`.

### Certificates

The panel cannot renew its own certificate, because Caddy owns 80/443 and ACME
needs one of them. `scripts/3xui-cert-sync.sh` copies Caddy's auto-renewed cert
into the panel and restarts it only when it changed:

```bash
crontab -e
17 4 * * * /path/to/meowstack/scripts/3xui-cert-sync.sh >> /path/to/meowstack/scripts/cert-sync.log 2>&1
```

Because the certificate is issued for the domain, reach the panel by hostname —
`https://<domain>:<port>/<path>/` — not by raw IP, or it will not validate.

### AmneziaWG inbounds

- **AmneziaWG app:** imports only a plain `.conf` (QR or file). A `vpn://` link or a
  subscription URL fails with **"Unknown section"**, whatever the version. In the panel,
  use the inbound's **"Peer N config"**.
- **AmneziaVPN app:** reads the `vpn://` link. It needs version 5.0.1.5 or later; older
  versions drop the AWG 3.x keys and hang on "Connecting…".
- **Don't attach one client to two AmneziaWG inbounds** (3x-ui 3.8.5). The server keeps a
  separate address per inbound, but subscription links use the client's single shared
  address. One of the two tunnels then gets the other's address: it handshakes but passes
  no traffic. Use one client per AWG inbound.

### XHTTP behind Caddy

A VLESS + XHTTP inbound published on `:443` under Caddy's own certificate. Unlike a
Reality config that borrows someone else's SNI, the SNI here is **your own domain on
your own IP** and a real site answers every probe, which is what survives censors
that tie well-known SNIs to their real IPs ("grey lists"). It also needs no extra
router port-forward.

The VPN profile already carries the Caddy side: `handle /{$XHTTP_PATH}/*` ->
`reverse_proxy h2c://host.docker.internal:20470 { flush_interval -1 }`, with
`XHTTP_PATH` generated by `bootstrap.sh` (a secret path, with a `:?` guard in
compose like the other paths). `handle`, not `handle_path`, so the path reaches the
inbound intact; `flush_interval -1` keeps stream-one and stream-up full-duplex. The
route does nothing until you create the matching inbound in the panel:

1. **Add inbound**: protocol VLESS, transport **XHTTP**, mode `auto`, **security
   none**, listen `0.0.0.0` port **20470** (the panel is on the host network and
   Caddy reaches it through the host gateway; do **not** forward 20470 on the
   router or open it in a firewall), path `/<XHTTP_PATH>` (the value from `.env`).
2. **External proxy**: add one with `forceTls` = tls, `dest` = `<your domain>`,
   port `443`. Share links and subscriptions then advertise 443 + TLS instead of
   the internal port.
3. Optional xhttp `extra`, which works well behind Caddy: `xPaddingBytes`
   `100-1000`, `scMaxEachPostBytes` `500000-1000000`, `scMinPostsIntervalMs`
   `10-50`. Subscriptions do not carry xmux, so clients on older Xray may need it
   pasted by hand (see the [research notes](research/russia-2026-10.md)).
4. Attach the inbound to clients (and, for the helper bot, to the member inbounds).

If you use a free shared DNS domain, a domain you own is safer. Check from a network
outside your own: a request made on the server hairpins through the router.

### Upgrading

The panel's own Update button does not work in Docker. Xray is bundled in the
image, so upgrading the panel also upgrades Xray. A newer Xray switched in from
the panel is lost when the container is recreated.

```bash
scripts/3xui-upgrade.sh --test ghcr.io/mhsanaei/3x-ui:vX.Y.Z   # migrate a copy of the DB
scripts/3xui-upgrade.sh ghcr.io/mhsanaei/3x-ui:vX.Y.Z          # the real thing
scripts/3xui-upgrade.sh --confirm                               # keep it, within 15 min
scripts/3xui-upgrade.sh --rollback                              # or go back now
```

`--test` runs the new image against a **copy** of `x-ui.db`, in a bridge-network
container (no live ports, Telegram bot off), and prints what it started and which
ports it bound.

The real run backs up `x-ui.db` + `-wal` + `-shm` together, because the DB is in
WAL mode and a hot copy of the main file alone is stale. It then pins the new
digest in `3xpanel/docker-compose.yml`, restarts the panel (about 15 s of VPN
downtime) and arms a detached watchdog. Without `--confirm` within 15 minutes
(`WATCH_MIN`), the watchdog restores the old compose file and DB. So a dropped
SSH session mid-upgrade cannot leave the VPN down.

Before confirming:

- the logs show `Xray … started` with no errors;
- `ss -lntu` lists every inbound port;
- subscriptions return the same links;
- a real client passes traffic through each Reality inbound.

Read the release notes' **"Before you upgrade"** section first. The trap in this
project's history: Xray 26.7.11+ under panel 3.7.x treated an empty REALITY
`minClientVer` as "26.3.27", silently refusing older clients. Setting it to
`1.0.0` on every Reality inbound keeps old apps working.

### Riding :443

Inbounds can share port 443 with the web stack. Caddy's layer4 router matches
on SNI and passes the raw TLS stream through — no termination, because Reality
does its own TLS. Enable in `.env`:

```ini
VPN_PASSTHROUGH=true
VPN_SNI_A=www.icloud.com
VPN_PORT_A=57971
```

then `./bootstrap.sh && docker compose restart caddy`. The SNI names must match
what the panel is configured with, and must be names nothing else here serves.
This is worth doing because many networks block outbound to non-standard ports.

The subscription endpoint is also served on 443 at `/sub/*` for the same reason.

---

## Host tuning

Install once:

```bash
sudo cp scripts/host-tuning.conf /etc/sysctl.d/99-meowstack.conf
sudo sysctl --system
```

This shortens the conntrack established timeout. VPN clients leak post-handshake
sockets with no TCP timer; with the 5-day default they fill the router's NAT
table until it is full, at which point existing connections keep working while
*new* ones are silently dropped. It presents as "the server is down for some
people" and is genuinely hard to diagnose from the symptom.

It must live in `/etc/sysctl.d/` — a live `sysctl` reverts on reboot and the
problem reappears weeks later looking like something new.
