# Home Assistant

Optional, its own compose project in `homeassistant/` (like `amneziawg/`), so it can never
recreate the main stack. Built for devices that are **not** on the server's LAN (another flat,
another country): they reach Home Assistant over HTTPS, the way the ESP32 room sensor reaches
its dashboard. Nothing needs to be opened at the devices' end.

```
ESP32 elsewhere ──HTTPS──► caddy :443 ── /api/webhook/<id> ──► homeassistant:8123 (proxy network)
browser ── /${HA_PATH}/ (sets cookie) ──► caddy ── cookie ──► homeassistant:8123
```

## Setup

```bash
# main .env: ENABLE_HOMEASSISTANT=true, then
./bootstrap.sh                                  # randomises HA_PATH, generates HA_GATE_TOKEN, renders Caddy routes
docker compose up -d --no-deps caddy            # caddy reads HA_PATH / HA_GATE_TOKEN from its env
cd homeassistant && docker compose up -d
```

Then **at once** open `https://${BASE_DOMAIN}/${HA_PATH}/` and create the owner account: until
you do, whoever opens the onboarding page first can claim the instance (the secret path is the
only thing in front of it). Turn on TOTP under Profile → Security.

**First start, HTTP settings:** HA 2026.10 moved the `http:` settings to Settings → System →
Network. The `http:` block in `homeassistant/config/configuration.yaml` is imported once as a
**trial that auto-reverts in about 3 minutes** unless you confirm the dialog in the UI. Confirm
it, then delete the block. Without "Trust X-Forwarded-For" HA refuses requests that come through
Caddy.

## Why a secret path and a cookie (not a sub-path)

HA can't run under a URL sub-path (its frontend asks for `/frontend_latest`, `/auth`,
`/api/websocket` at the root). So without a subdomain:

- `/${HA_PATH}/` sets an HttpOnly cookie `meow_ha=${HA_GATE_TOKEN}` for 90 days and redirects to
  `/lovelace/0`.
- The **last `handle`** of the `${BASE_DOMAIN}` block proxies to HA only for requests carrying
  that cookie; everyone else gets the usual redirect. Every other route has its own `handle`
  and wins first, so nothing else changes.
- `/api/webhook/*` reaches HA without the cookie: HA's own public entry point for devices.
- HA's service worker files answer 404: with scope `/` it would claim every path on the domain.
- Trade-off: HA shares its origin (and so localStorage) with the other root-domain pages. With
  a spare DNS name, a plain `home.example.org { reverse_proxy homeassistant:8123 }` is cleaner.

**No host network:** host mode would publish :8123 on every interface, and local discovery
(mDNS, Bluetooth) only finds devices on the server's own LAN anyway.

## A remote ESP32 (ESPHome)

ESPHome:

```yaml
http_request:
  verify_ssl: true
interval:
  - interval: 60s
    then:
      - http_request.post:
          url: https://example.org/api/webhook/<long-random-id>
          request_headers:
            Content-Type: application/json
          json:
            temperature: !lambda 'return id(temp).state;'
```

Home Assistant (`configuration.yaml`, then restart; put the id in `secrets.yaml`):

```yaml
template:
  - trigger:
      - trigger: webhook
        webhook_id: !secret kitchen_webhook
        allowed_methods: [POST]
        local_only: false        # required: requests come from the internet
    sensor:
      - name: Kitchen temperature
        state: "{{ trigger.json.temperature }}"
        unit_of_measurement: "°C"
        device_class: temperature
```

The sensor dashboard (docs/SENSOR.md) can be read back too, with HA's REST integration on
`https://${BASE_DOMAIN}/${SENSOR_PATH}/api/series?range=1h` (`value_json.stats.temperature.current`).

**Switching things and Zigbee** need a small local box at the devices' end (e.g. an Orange Pi
Zero 3 running Mosquitto + Zigbee2MQTT) that connects out to the server as a VPN client, with
its Mosquitto bridged to HA. It only dials out, so nothing opens at that end.

## Operations

```bash
cd homeassistant && docker compose up -d       # start / apply
docker logs -f homeassistant
```

Pin a newer tag with `HA_IMAGE` in `homeassistant/.env` after reading the release notes.
Rollback: `cd homeassistant && docker compose down`, set `ENABLE_HOMEASSISTANT=false`, re-run
`./bootstrap.sh`, `docker compose up -d --no-deps caddy`.
