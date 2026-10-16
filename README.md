# TriStar Weather Station V2

> Because an overworked Arduino just isn't getting the job done. Literally. It's dead.

A Raspberry Pi–based weather station for **TriStar Observatory**, with an Arduino Nano handling the electrically annoying bits, a FastAPI service providing current conditions, local SQLite storage, and periodic synchronization to MySQL.

The original Arduino weather station has retired from active duty, somewhat involuntarily. Version 2 divides the work between two computers: the Nano counts pulses and measures analog signals; the Pi does the calculations, networking, API, and data management. This is a weather station, not a distributed computing thesis. The goal is to know whether the sky is worth pointing a telescope at.

## What it does

- Samples the main weather/environment sensors every **5 seconds**.
- Polls the Arduino Nano over **9600-baud UART** approximately every **1 second**.
- Publishes the current combined reading at **`GET /api/weather/current`** on port **8000**.
- Stores five-second snapshots and per-sensor statuses in a local **SQLite** database.
- Synchronizes unsent observations to **MySQL** approximately every **300 seconds** (5 minutes).
- Retains synchronized local observations for roughly **24 hours**, while keeping unsynchronized observations until they can be transferred.
- Reports each enabled sensor as `available` or `error`, and disabled sensors as `unavailable`.

**Current implementation note:** The code supports several sensor channels that may be disabled, unpopulated, or reserved for future hardware. A field appearing in the API does **not** mean that a corresponding sensor is installed and reporting.

## System architecture

```text
 SparkFun weather meters                  Environmental sensors
 ┌──────────────────────┐                 ┌───────────────────────────┐
 │ Anemometer reed      │                 │ BME280 (I²C)             │
 │ Rain bucket reed     │                 │ MLX90614 #1 (I²C)        │
 │ Wind vane resistor   │                 │ LM393 light digital GPIO │
 └──────────┬───────────┘                 └─────────────┬─────────────┘
            │                                           │
 ┌──────────▼───────────┐                               │
 │ Arduino Nano         │   UART 9600                   │
 │ - counts pulses      ├───────────────────┐           │
 │ - reads analog       │                   │           │
 │ - classifies vane    │                   │           │
 │ - switches rain plate│                   │           │
 └──────────────────────┘                   │           │
                                  ┌────────▼───────────▼─────┐
                                  │ Raspberry Pi / wxstation │
                                  │ Docker: tristar-weather  │
                                  │ FastAPI + sensor polling │
                                  └────────────┬─────────────┘
                                               │
                            ┌──────────────────┼───────────────────┐
                            │                  │                   │
                     GET /api/weather/     SQLite             MySQL
                         current        weather.sqlite3   weather_station_v2
                        port 8000       every 5 seconds   every ~5 minutes
                            │
                     Observatory UI
```

The Nano does not independently broadcast weather readings. The Pi sends `READ\n`; the Nano responds with one JSON line and resets the accumulated anemometer/rain-bucket counters. No unsolicited serial chatter, no mystery packets, no interpretive dance.

## Hardware and interfaces

| Device | Connection / purpose | Implementation |
|---|---|---|
| Raspberry Pi | Main station computer | Raspberry Pi OS Lite, Docker |
| Arduino Nano (ATmega328P) | Pulse counting and analog acquisition | `arduino/wxstation_nano/wxstation_nano.ino` |
| BME280 | Temperature, humidity, station pressure | I²C bus 1, address `0x76` |
| MLX90614 #1 | Ambient and sky/object IR temperature | I²C bus 1, address `0x5A` |
| LM393 light sensor | Digital dark/light input; analog level from Nano | Pi GPIO17 and Nano A1 |
| SparkFun anemometer | Reed-switch pulses | Nano D2 / interrupt |
| SparkFun rain bucket | Reed-switch tips | Nano D3 / interrupt |
| SparkFun wind vane | Resistor ladder / direction | Nano A0 |
| LM393 rain detection plate | Digital wet state and analog level | Nano D5 and A2; switched power on D4 |

### Pi ↔ Nano UART wiring

| Raspberry Pi | Arduino Nano | Notes |
|---|---|---|
| Physical pin 2 — 5V | 5V | Nano power |
| Physical pin 6 — GND | GND | Shared ground is required |
| Physical pin 8 — GPIO14 / TX | D0 / RX | Pi transmits commands |
| Physical pin 10 — GPIO15 / RX | D1 / TX | **Through voltage divider** |

The Nano's TX is a 5V signal; the Pi's RX is **not** 5V tolerant. The tested divider is:

```text
Nano D1 / TX ── 2.2 kΩ ──┬── Pi GPIO15 / RX (physical pin 10)
                          │
                         3.3 kΩ
                          │
                         GND
```

This produces approximately 3V at the Pi's RX input when Nano TX is HIGH. The station uses `/dev/serial0`, which the working setup resolves to the Pi's hardware UART.

**Important:** The wiring above documents the tested UART connection. Other sensor connections should be verified against the actual installed harness before changing anything. Don't casually reconnect a 5V Nano output directly to a Pi GPIO unless you enjoy involuntary hardware shopping.

### Nano firmware behavior

- Serial speed: **9600 baud**.
- Command: ASCII `READ` followed by a newline.
- Response: a single JSON object followed by a newline.
- Anemometer: falling-edge interrupt, rearmed after **8 ms** continuously released.
- Rain bucket: falling-edge interrupt, rearmed after **100 ms** continuously released.
- Wind vane: 16 compass directions in **22.5°** increments, using calibrated ADC ranges with nearest-midpoint fallback.
- LM393 rain plate: switched on for each reading, allowed **40 ms** to settle, then switched off again.
- Pulse/tip counters are atomically copied and reset when responding to `READ`.

The Nano firmware is intended for an ATmega328P Nano. The working clone was programmed using the Arduino IDE's **Old Bootloader** option.

Example Nano response (illustrative values):

```json
{
  "anemometer_pulses": 1,
  "rain_bucket_tips": 0,
  "wind_direction_deg": 225.0,
  "light_analog": 512,
  "rain_sensor_digital": 1,
  "rain_sensor_analog": 800
}
```

The Pi validates the expected fields and uses their values to update the current API snapshot. A missing, malformed, or timed-out serial response is treated as a Nano polling error.

## Software layout

| File | Responsibility |
|---|---|
| `weather_api.py` | FastAPI application, I²C/GPIO sampling, calculated environmental readings, status aggregation, background tasks |
| `nano_poll.py` | UART polling, wind speed, gust windows, rain totals, Nano sensor statuses |
| `database.py` | SQLite schema, initialization, atomic observation/status writes |
| `mysql_sync.py` | Retry-safe transfer from SQLite to MySQL and local retention cleanup |
| `arduino/wxstation_nano/wxstation_nano.ino` | Nano firmware for pulses, wind vane, light and rain-plate readings |
| `compose.yaml` | Docker service, port, device mappings, data volume, environment |
| `Dockerfile` | Python 3.13 container image and dependencies |
| `requirements.txt` | Python packages |
| `.env.example` | Configuration template; **not** real credentials |
| `mysql_schema.sql` | Reference definition of the existing MySQL tables |
| `data/` | Persistent local SQLite database, mounted into the container |

## Deployment

The repository is configured to run on a **64-bit Raspberry Pi** with Docker and Docker Compose. The Dockerfile uses `python:3.13-slim-bookworm` and installs an **aarch64** `lgpio` wheel, so it is not a generic build for every CPU architecture.

### Prerequisites

- A Raspberry Pi with working I²C and UART interfaces.
- Docker Engine and the Docker Compose plugin.
- Connected and correctly powered sensors/Nano as appropriate.
- A reachable MySQL server with the expected `weather_station_v2` schema.
- A writable `data/` directory for SQLite persistence.

### Configure

Copy `.env.example` to `.env`, then edit the values for your installation:

```bash
cp .env.example .env
```

The file requires real values for MySQL connection settings and **`STATION_ELEVATION`** in meters. `weather_api.py` reads the elevation using `os.environ["STATION_ELEVATION"]`; leaving it unset or nonnumeric prevents startup.

| Variable | Meaning |
|---|---|
| `MYSQL_HOST` | Hostname/IP of the MySQL server |
| `MYSQL_PORT` | MySQL port, normally `3306` |
| `MYSQL_DATABASE` | Database name; existing deployment uses `weather_station_v2` |
| `MYSQL_USER` | MySQL login |
| `MYSQL_PASSWORD` | MySQL password |
| `STATION_ELEVATION` | Station elevation in **meters**, used for sea-level pressure correction |
| `READ_BME` | Enable BME280 |
| `READ_LIGHT` | Enable LM393 light processing |
| `READ_RAIN_SENSOR` | Enable LM393 rain plate |
| `READ_MLX_1` | Enable first MLX90614 |
| `READ_MLX_2` | Reserve/enable second MLX90614 status channel; **no acquisition routine implemented** |
| `READ_ANEMOMETER` | Enable wind speed/gust calculations |
| `READ_WINDVANE` | Enable wind direction |
| `READ_RAINBUCKET` | Enable rain bucket totals |

Sensor flags are interpreted as `true` or `false` (case-insensitive after whitespace trimming). Disabled channels remain `unavailable` in the status object. **Enabling a flag does not magically install a sensor.** This is software, not a replicator.

`READ_LIGHT` currently enables **two** paths: digital GPIO light state (`is_dark`) and Nano analog light level (`light_value`). They share the `lm393_light` status key. Keep this in mind when diagnosing that status; it reflects a shared implementation rather than independent per-interface statuses.

### Build and run

From the repository directory:

```bash
docker compose up -d --build
```

Useful operational commands:

```bash
docker compose ps
docker compose logs -f weather
docker compose restart weather
docker compose down
```

The service is named `weather`, with container name **`tristar-weather`**, and uses Docker's `unless-stopped` restart policy. Host port **8000** maps to container port **8000**. Compose passes through `/dev/i2c-1`, `/dev/serial0`, and `/dev/gpiochip0`, and mounts `./data` at `/app/data`.

**Rebuild vs restart:** `docker compose restart weather` restarts the existing container; it does not rebuild an image after Python source or dependency changes. Use `docker compose up -d --build` when deploying code changes.

### Check the API

On the Pi:

```bash
curl http://localhost:8000/api/weather/current
```

From another machine, substitute the Pi's IP or hostname:

```text
http://wxstation:8000/api/weather/current
```

Hostname resolution depends on your local network. The endpoint has no authentication in the current application; do not expose it directly to the public Internet without suitable network controls.

## API reference

### `GET /api/weather/current`

Returns the latest in-memory observation as JSON. There are no query parameters. The API returns the current snapshot, **not historical observations**.

The following is an **illustrative shape**, not a recorded measurement or a claim that every sensor is operational:

```json
{
  "timestamp": "2026-10-16T19:30:00+00:00",
  "bme_temp_c": 22.4,
  "temp_c": 22.1,
  "mlx_temp_c": 21.8,
  "humidity_pct": 54.2,
  "pressure_hpa": 972.4,
  "pressure_sealevel_hpa": 1010.3,
  "dew_point_c": 12.7,
  "is_dark": true,
  "light_value": 480,
  "is_wet": false,
  "wet_value": 820,
  "sky_temperature_c": -8.5,
  "ground_temperature_c": null,
  "wind_speed_mps": 1.2,
  "wind_dir_deg": 225.0,
  "rain_amount_mm": 0.0,
  "rain_hourly_mm": 0.0,
  "rain_daily_mm": 0.0,
  "wind_gust_10min": 2.3,
  "wind_gust_hour": 3.1,
  "wind_gust_today": 4.0,
  "status": {
    "system": "available",
    "bme280": "available",
    "lm393_light": "available",
    "lm393_rain": "available",
    "mlx90614_1": "available",
    "mlx90614_2": "unavailable",
    "anemometer": "available",
    "windvane": "available",
    "rainbucket": "available"
  }
}
```

### Observation fields

| Field | Units / interpretation | Source |
|---|---|---|
| `timestamp` | ISO-8601 UTC timestamp | Pi sampling loop |
| `bme_temp_c` | °C; BME280 temperature | BME280 |
| `mlx_temp_c` | °C; MLX90614 ambient temperature | MLX90614 #1 |
| `temp_c` | °C; rolling mean of up to 10 valid samples of the **average of BME280 and MLX ambient temperatures** | Pi calculation |
| `humidity_pct` | Relative humidity, % | BME280 |
| `pressure_hpa` | Station pressure, hPa | BME280 |
| `pressure_sealevel_hpa` | Temperature-adjusted sea-level pressure, hPa | Pi calculation; requires station elevation |
| `dew_point_c` | Dew point, °C | Pi calculation from BME temperature and RH |
| `is_dark` | Boolean; digital LM393 light output (`true` for HIGH) | Pi GPIO17 |
| `light_value` | Raw analog ADC value, 0–1023 | Nano A1 |
| `is_wet` | Boolean; `true` when Nano rain digital input reads LOW | Nano D5 |
| `wet_value` | Raw rain-plate ADC value, 0–1023 | Nano A2 |
| `sky_temperature_c` | °C; MLX90614 object/sky temperature | MLX90614 #1 |
| `ground_temperature_c` | Reserved; currently not populated | — |
| `wind_speed_mps` | m/s; calculated from anemometer pulses and actual poll interval | Nano/Pi |
| `wind_dir_deg` | Degrees, 0 ≤ direction < 360 | Nano wind vane |
| `rain_amount_mm` | mm from tips received in the **most recent Nano poll** | Nano/Pi |
| `rain_hourly_mm` | mm; rolling last hour | Pi calculation |
| `rain_daily_mm` | mm; since local midnight | Pi calculation |
| `wind_gust_10min` | m/s; highest computed three-second average in last 10 minutes | Pi calculation |
| `wind_gust_hour` | m/s; highest computed three-second average in last hour | Pi calculation |
| `wind_gust_today` | m/s; highest computed three-second average since local midnight | Pi calculation |
| `status` | Per-sensor and overall availability object | Pi |

The code uses **`America/Chicago`** for the local-day boundaries of `rain_daily_mm` and `wind_gust_today`. Timestamps themselves are UTC. The three-second gust value is a weighted average over recent poll intervals once roughly 2.5 seconds of data have accumulated; the reported gust fields are the maxima of those computed averages, **not** instantaneous peak gusts.

### Wind and rain conversions

- **Wind:** `wind_speed_mps = pulses / elapsed_seconds × 1.492 × 0.44704`. The 1.492 factor expresses mph per pulse/second; `0.44704` converts mph to m/s.
- **Rain:** `millimeters = bucket_tips × 0.2794`.
- **Direction:** one of 16 classified wind-vane directions in 22.5° increments.

These constants are in `nano_poll.py`. The wind-vane ADC classification table is in the Nano sketch.

### Status semantics

| Status | Meaning |
|---|---|
| `available` | The enabled sensor path has reported a successful read |
| `error` | An enabled sensor has not yet reported success, or its read/initialization failed |
| `unavailable` | The sensor channel is disabled |

`status.system` is `available` only when **all enabled sensors** are currently marked `available`; it is `unavailable` when no sensors are enabled, otherwise `error`.

The station keeps an in-memory snapshot updated by both the five-second environmental loop and the approximately one-second Nano loop. Consequently, individual fields can reflect slightly different acquisition moments even though the snapshot carries a single `timestamp` set by the five-second loop. There is no per-field acquisition timestamp.

## Local storage and MySQL synchronization

### SQLite

The local database lives at:

```text
data/weather.sqlite3
```

Because `./data` is mounted into the container, data survives ordinary container replacement/rebuilds. `database.py` creates two tables if needed:

- **`observations`**: one row per saved weather snapshot, with an auto-incrementing `observation_id` and `synced` flag (`0` or `1`).
- **`status`**: one row per observation, linked by `observation_id`, with individual sensor and overall status values.

Both inserts occur in the same SQLite transaction. Foreign keys are enabled and status rows use `ON DELETE CASCADE`, so deleting a retained observation also deletes its matching status row.

The schema includes `future_obs_1` through `future_obs_5` and `future_device_1` through `future_device_5` as reserved database columns. They are not part of the current API response.

### MySQL

`mysql_sync.py` connects using the configured `MYSQL_*` environment variables. Every ~5 minutes it drains pending local rows in batches of up to **1,000** until the backlog is caught up.

The sync process:

1. Selects local observations where `synced = 0`.
2. Loads their matching local status rows and aborts the batch if any are missing.
3. Inserts/updates both sets of rows in MySQL using their SQLite IDs.
4. **Commits MySQL first.**
5. Only after a successful remote commit, marks those SQLite observations `synced = 1`.
6. Deletes **already-synchronized** SQLite observations older than 24 hours.

MySQL inserts use `ON DUPLICATE KEY UPDATE`, so retrying after a crash between the remote commit and local flag update does not require duplicate records. Unsynced local records are not subject to the 24-hour purge.

The MySQL schema differs slightly in naming: SQLite `timestamp` maps to MySQL **`obs_timestamp`** (`DATETIME(6)` UTC), and SQLite `system` maps to MySQL **`sys`**. Remote observations are inserted with `synced = 1`.

**Do not blindly execute `mysql_schema.sql` against the running server.** The repository labels it as a **reference schema for tables that already exist**, not a migration. Treat the live database as authoritative and inspect it before making schema changes.

### Retention is not a backup policy

The local 24-hour purge applies only to successfully synced rows. MySQL retains historical observations according to whatever external retention/backup arrangements are made; this repository does **not** implement a MySQL history purge or a MySQL backup job.

The station also does not offer an HTTP historical-data API. For historical analysis, query the database directly or build a separate consumer. The API's job is to answer **“what's happening now?”**, not **“what was the dew point during that one night I forgot to close the roof?”**

## Failure handling and troubleshooting

### The API doesn't respond

Check the container and logs:

```bash
docker compose ps
docker compose logs --tail=100 weather
```

Verify that port 8000 is mapped and that `.env` includes a numeric `STATION_ELEVATION`. A running container with sensor errors may still serve the API; check `status` before assuming every `null` is an application crash.

### BME280 or MLX90614 reads fail

The Pi uses I²C bus **1**. Verify the physical bus, power, wiring, and configured addresses (`0x76` for BME280 and `0x5A` for MLX90614 #1). The service retries I²C initialization and BME calibration in later sample cycles; it can close and reopen the bus after sensor errors.

Useful host-side check if I²C tools are installed:

```bash
i2cdetect -y 1
```

The code currently reads **one** MLX90614 at `0x5A`. `READ_MLX_2` exists as a configuration/status flag but does not activate a second read routine.

### Nano readings fail or go stale

Check the Pi's serial device, UART wiring, common ground, voltage divider, and Nano firmware. The Python side expects `/dev/serial0` at **9600 baud**, newline-delimited JSON with all six expected keys.

A direct UART smoke test on the **host**, while the weather service is stopped or otherwise not using the port:

```bash
python3 -c 'import serial; s=serial.Serial("/dev/serial0",9600,timeout=2); s.write(b"READ\n"); print(repr(s.readline().decode())); s.close()'
```

Do **not** run competing serial readers while the service is actively polling; the Nano resets its pulse/tip counters on every `READ`, so a second reader can steal measurements. The system's troubleshooting strategy should not become its newest source of bogus rainfall.

On a Nano polling exception, the Pi closes the serial port, clears affected live readings, marks enabled Nano sensor statuses as `error`, and attempts to reconnect on a later cycle. Gust and rainfall reporting windows continue to age out during outages rather than preserving old maxima indefinitely.

### MySQL isn't receiving new rows

Look for `MySQL synchronization error` in container logs. Verify credentials, host/port reachability, database/schema, and server permissions. Local SQLite writes are independent of the remote connection: a temporary MySQL outage should leave observations locally queued with `synced = 0` for later retry.

To inspect pending rows locally, using a host with `sqlite3` installed:

```bash
sqlite3 data/weather.sqlite3 'SELECT synced, COUNT(*) FROM observations GROUP BY synced;'
```

To inspect the latest local records:

```bash
sqlite3 data/weather.sqlite3 'SELECT observation_id, timestamp, synced FROM observations ORDER BY observation_id DESC LIMIT 10;'
```

### Light status seems inconsistent

`READ_LIGHT` drives both a Pi GPIO digital read and a Nano analog read, but both paths write to the **same** `lm393_light` status. The current implementation does not expose separate status flags for the two acquisition methods. Compare `is_dark`, `light_value`, and the logs before concluding which interface failed.

### `null` doesn't always mean broken

A `null` value can mean an unimplemented field, disabled sensor, unavailable measurement, a failed read, or a calculated metric that has not yet accumulated enough samples. Consult `status` and the field descriptions. A brand-new gust window, for example, does not instantly contain three seconds of history. Time remains stubbornly linear.

## Development and project conventions

The current Python files use the project's header comment boxes, with original creation dates and purpose/notes. The house style favors:

- Plain, explicit code instead of abstraction for abstraction's sake.
- `import ...` statements before `from ... import ...`, alphabetized within each section.
- Alphabetized function definitions, including private/name-mangled functions in the agreed order.
- Alphabetized independent declarations where reordering is safe; **never** reorder something in a way that requires a functional change or breaks a dependency.
- Comments that explain intent or responsibility, readable vertical formatting, and limited unnecessary machinery.

**Repository workflow:** Read-only by default. Changes to GitHub require explicit authorization for the particular task. Work on a feature branch, inspect/diff changes, and leave merges to the maintainer unless explicitly requested otherwise. Having a token is not the same as having permission. A lesson learned the loud way.

## Known implementation boundaries

- The application exposes a **current-state** endpoint only, not historical API endpoints.
- The second MLX90614 channel is a status/configuration placeholder, not a working reader.
- `ground_temperature_c` is reserved and currently not populated.
- The station's local day uses `America/Chicago`, while timestamps are UTC.
- Light digital and analog reads share a single status key.
- The current endpoint is unauthenticated; network exposure is an operator responsibility.
- The code and schema describe the system's implemented behavior; actual hardware installation and enabled sensor selection are deployment-specific.

## Why V2 exists

The first station was an Arduino doing all the things, right up until it stopped doing any of the things. V2 gives the Nano a job it is good at—counting electrical events without getting distracted—and gives the Pi the jobs it is good at: networking, calculation, storage, and convincing an observatory dashboard that the weather is, in fact, weather.

The long-term ambition is straightforward: reliable conditions data for TriStar Observatory, especially the information that matters before opening a roof over expensive astronomical equipment.

The immediate ambition is even more straightforward: **keep the damned thing running.**
