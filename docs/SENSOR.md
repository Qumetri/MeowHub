# ESP32 room sensor

Temperature and humidity charts for an ESP32 + DHT-22 that may sit anywhere
with WiFi — another apartment, a parent's house — and posts over HTTPS to this
stack. The app is its own project,
[Qumetri/esp32-sensor-dashboard](https://github.com/Qumetri/esp32-sensor-dashboard)
(Express + TypeScript, SQLite, Chart.js); its README covers the hardware,
flashing and firmware. This stack builds, runs and publishes it.

Turn it on with the `sensor` profile in `COMPOSE_PROFILES`, then
`./bootstrap.sh` (generates `SENSOR_PATH` and `SENSOR_TOKEN`, renders the
Caddy route) and `docker compose up -d`.

| | |
|---|---|
| Page | `https://<BASE_DOMAIN>/<SENSOR_PATH>/` (hub card *Room Sensor*) |
| ESP32 posts to | `https://<BASE_DOMAIN>/<SENSOR_PATH>/api/readings` |
| Database | `sensor/data/readings.db` (~1,440 rows a day, a few MB a month) |
| Local debug | `127.0.0.1:${SENSOR_LOCAL_PORT}` (8085) |

## How a reading gets here

```
ESP32 ──HTTPS POST + X-Sensor-Token──> :443 Caddy ──/<SENSOR_PATH>/*──> sensor:3001 ──> readings.db
```

No VPN and no extra port forward: it rides the 443 that's already open.

- **Posting needs the token.** The app rejects `POST /api/readings` without
  `X-Sensor-Token: <SENSOR_TOKEN>` (`401`). An empty token would let anyone
  post — bootstrap.sh always generates one.
- **Viewing doesn't.** No basic auth: the page only shows room temperature and
  the secret path keeps it unlisted. Temperature patterns can hint when
  someone is home; add a `basic_auth` block to the route (like the crypto
  tracker's) if that matters to you.
- **The ESP32 checks the certificate** against Let's Encrypt's roots
  (`ESP/ca.pem` in the app repo: ISRG Root YE, YR, X1, X2), so nothing on the
  device's network can pose as the server and collect the token. If Caddy
  ever served a certificate from another authority, the sensor would stop
  posting until `ca.pem` is updated.
- **Outages are gaps.** If either internet line or the server is down, those
  minutes are lost and the charts show breaks. The firmware reconnects WiFi by
  itself.

## Pointing the ESP32 here

In the device's `secrets.py` (template `ESP/secrets.example.py` in the app repo):

```python
SERVER_URL = "https://<BASE_DOMAIN>/<SENSOR_PATH>/api/readings"
SENSOR_TOKEN = "<SENSOR_TOKEN from .env>"
VERIFY_TLS = True
```

Copy `secrets.py`, `ca.pem` and `main.py` to the device with `mpremote cp`
(app README, step 7). The serial output should say `Server responded: 201`.

## Upgrading

The image is built from GitHub at a **pinned commit** — `SENSOR_BUILD` and
`SENSOR_IMAGE` in compose (overridable in `.env`). To upgrade, put the new
commit hash in both, then `docker compose up -d --build sensor`. Nothing changes
until you do, the same idea as pinned image tags.

Changing `SENSOR_PATH`: `./bootstrap.sh` (rebuilds the hub card), then
`docker compose up -d --no-deps caddy` — it's in Caddy's own environment, so a
plain restart won't pick it up — and update `SERVER_URL` on the device.
Changing `SENSOR_TOKEN`: `docker compose up -d sensor` and update the device;
it gets `401` until then.

## Bringing readings from another install

```bash
scripts/sensor-import.sh <exported readings.db>
```

Merges, never overwrites: a reading whose timestamp is already here is
skipped, so you can re-run it with a newer export. Values a DHT-22 can't
produce are skipped. The service stops for the second or two the merge takes,
and the database is backed up next to itself first (`readings.db.bak-<time>`).

Export from the old install with its server stopped — the app doesn't use
SQLite's WAL, so a stopped database is one complete file — and upload it over
your own Nextcloud's WebDAV, e.g. from Windows:

```powershell
Copy-Item WebServer\data\readings.db readings-export.db
curl.exe -u <user>:<app password> -T readings-export.db "https://<CLOUD_HOST>/remote.php/dav/files/<user>/readings-export.db"
```

Point the ESP32 at the new server *before* exporting, then nothing falls
between the two; re-run the import with a final export afterwards.

## Checks

```bash
docker compose ps sensor                        # (healthy)
curl -s 127.0.0.1:8085/health
docker compose logs --tail 20 sensor            # one "POST /api/readings -> 201" a minute
```

Whether the sensor itself is alive: the status dot on the page (Live, Delayed
after 90 s, Offline after 5 min).
