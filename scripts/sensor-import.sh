#!/usr/bin/env bash
# Merge readings exported from another install of the ESP32 sensor dashboard
# (its WebServer/data/readings.db) into this stack's database.
#
#   scripts/sensor-import.sh <exported readings.db>
#
# Safe to run again with a newer export: a reading whose timestamp is already
# here is skipped, so only what's missing is added. Rows a sensor could not
# have produced (non-numbers, outside the DHT-22's -40..80 °C / 0..100 %) are
# skipped too. The service is stopped while merging and started afterwards;
# the database is backed up next to itself first. See docs/SENSOR.md.
set -euo pipefail
cd "$(dirname "$0")/.."

[[ $# -eq 1 && -f $1 ]] || { echo "usage: $0 <exported readings.db>" >&2; exit 1; }
src=$(realpath "$1")
db=sensor/data/readings.db
[[ -f $db ]] || { echo "$db not found — start the sensor service once first, it creates the schema" >&2; exit 1; }

docker compose stop sensor
trap 'docker compose start sensor' EXIT
backup="$db.bak-$(date +%Y%m%d-%H%M%S)"
cp -p "$db" "$backup"
echo "Backed up to $backup"

python3 - "$src" "$db" <<'EOF'
import sqlite3, sys
src, dst = sys.argv[1:]
check = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
if check.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
    sys.exit(f"{src} is damaged, nothing imported")
check.close()

db = sqlite3.connect(f"file:{dst}", uri=True)
db.execute("ATTACH ? AS old", (f"file:{src}?mode=ro",))
before = db.execute("SELECT COUNT(*) FROM readings").fetchone()[0]
offered = db.execute("SELECT COUNT(*) FROM old.readings").fetchone()[0]
with db:
    db.execute("""
        INSERT INTO readings (timestamp, temperature, humidity)
        SELECT timestamp, temperature, humidity FROM old.readings
        WHERE typeof(timestamp) = 'integer'
          AND typeof(temperature) IN ('real', 'integer') AND temperature BETWEEN -40 AND 80
          AND typeof(humidity) IN ('real', 'integer') AND humidity BETWEEN 0 AND 100
          AND timestamp NOT IN (SELECT timestamp FROM main.readings)
        ORDER BY timestamp
    """)
after = db.execute("SELECT COUNT(*) FROM readings").fetchone()[0]
print(f"{offered} readings in the export, {after - before} added, {offered - (after - before)} already here or invalid; {after} in total")
EOF
