# Architecture

Why the stack is shaped the way it is. Most of these decisions exist because
the obvious alternative failed in a way that was expensive to diagnose.

## One entry point

Caddy owns 80 and 443 and is the only thing exposed. Everything else is either
on the internal Docker network or bound to `127.0.0.1` for debugging. Adding a
service means adding a route, not opening a port.

Caddy also handles certificates — issuing and renewal are automatic, and the
`caddy/data` volume is what makes them survive a recreate.

## The layer4 SNI router

Port 443 has to carry three incompatible things:

1. Ordinary HTTPS, which Caddy should terminate
2. TURN-over-TLS for Matrix calls, which LiveKit must terminate
3. VPN inbounds, which must not be terminated at all — Reality does its own TLS

A normal reverse proxy cannot do this: it terminates TLS before it can tell
what it is looking at. So the stack uses a custom Caddy build with the
`mholt/caddy-l4` module. Layer4 owns `:443`, peeks at the SNI, and routes:

```
SNI turn.<domain>   -> terminate TLS -> LiveKit (plaintext internally)
SNI <vpn names>     -> RAW passthrough to the VPN inbound on the host
everything else     -> RAW passthrough to Caddy's own HTTPS on an internal port
```

Caddy still terminates all web TLS exactly as it would normally. Layer4 is only
a router in front of it, and the PROXY protocol carries the real client IP
across the hop.

**HTTP/3 can never work through this.** Layer4 SNI demux is TCP-only. If the
internal server advertises `alt-svc: h3`, browsers try QUIC against a dead UDP
port and fail — and cache that failure for 30 days. Hence `protocols h1 h2`.
It looks like a performance regression; it is a correctness requirement.

## Templates, because some programs cannot read environment variables

Synapse, coturn, LiveKit and Element all read config files with no variable
substitution. Rather than ask you to edit four files consistently, they are
`.template` files rendered by `bootstrap.sh` from `.env`.

Caddy *does* substitute `{$VAR}` at load time, so the Caddyfile keeps those
inline and a path change needs only a restart. The template step for the
Caddyfile exists for a different reason: to drop whole blocks when a profile is
off, so a disabled service leaves no route pointing at a container that isn't
there.

A consequence worth internalising: **edit the template, not the rendered file.**
`bootstrap.sh` skips rendered files newer than their template so it will not
silently eat a hand edit, but the change won't survive a template update either.

## `.env` is resolved, not just read

`bootstrap.sh` writes derived values like `MATRIX_HOST=matrix.${BASE_DOMAIN}`
back as literal strings. Compose expands those itself, but nothing else does —
not `docker run --env-file`, not `envsubst`, not a person reading the file.
Resolving them means `.env` says the same thing to every consumer.

For the same reason there are no inline comments after values: Compose strips
them, plain `--env-file` does not, and a hostname with a trailing comment glued
on produces errors a long way from the cause.

## Secrets that cannot live in `.env`

The VPN admin password's bcrypt hash starts with `$2a$`. Shell sourcing and
Compose interpolation both mangle that, so it lives in `secrets/awg-admin.hash`
and is injected into the Caddyfile at render time instead.

## Compose profiles

Services with no `profiles:` key always run — Caddy and the stats backend.
Everything else is opt-in via `COMPOSE_PROFILES`, so the same repo runs a
files-only box or the full thing.

Caddy's routes are generated to match, which is what keeps a disabled profile
from leaving a 502 behind.

## GPU is an overlay, not a requirement

The base compose requests no devices, so it runs on any machine. GPU support is
`docker-compose.gpu.yml`, added via `COMPOSE_FILE`. Making it the default would
mean the stack fails to start on hardware that has no NVIDIA card — a bad
default for something meant to be portable.

## The VPNs are separate projects

`amneziawg/` and `3xpanel/` have their own `name:` and their own compose files.
This is deliberate: `docker compose up -d` in the main directory cannot recreate
or destroy them, and vice versa. They share only the external `proxy` network,
declared external so neither project owns — or deletes — it.

The cost is that you must `cd` into the right directory. That is the point.

## Storage split

Code, databases and caches sit next to the compose file; bulk user data goes to
`DATA_ROOT`. Databases are small and benefit from fast disk; user files are
large and want capacity. On a single-disk machine, point `DATA_ROOT` anywhere —
the split still keeps backups sensibly separated.

## Pinned images

Nothing uses `:latest` except a few genuinely stateless services (MeTube is
pinned too: some builds broke YouTube's token helper, so it is bumped on purpose). Nextcloud
requires sequential major upgrades, and an unattended jump leaves an instance
that will not start. 3x-ui is pinned by *digest* rather than tag, because a
panel upgrade migrates its own database and is not reliably reversible.

## Secret paths as credentials

The hub page has no login. Its URL prefix is the credential,
randomised on first run. This is weak authentication and it is chosen knowingly:
it costs nothing, and the alternative for a single-user service is a login
screen nobody wants. It is not appropriate for anything sensitive, which is why
the VPN peer manager — which hands out working VPN keys — sits behind basic
auth instead, and so does MeTube once several people use the downloader.

## Testing from the server lies to you

A request made on the host goes out to the router and back. It can succeed
while the service is unreachable from the internet. Always confirm from
somewhere else. This one costs more debugging time than any other item here.
