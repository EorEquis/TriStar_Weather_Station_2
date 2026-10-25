###########################################################################
# Created : 2026-10-16 GB
# Purpose : SQLite database for weather observations and sensor statuses
# Notes   : Most code was generated with assistance from ChatGPT.
#           Chat title: Wx Station N
#           OpenAI model/version: GPT-6
###########################################################################

"""Local SQLite schema for weather observations and sensor statuses."""

import sqlite3
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from pathlib import Path

DATABASE_PATH = Path(__file__).resolve().parent / "data" / "weather.sqlite3"
STATION_TIMEZONE = ZoneInfo("America/Chicago")

SCHEMA = """
CREATE TABLE IF NOT EXISTS observations (
    observation_id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    bme_temp_c REAL,
    temp_c REAL,
    mlx_temp_c REAL,
    humidity_pct REAL,
    pressure_hpa REAL,
    pressure_sealevel_hpa REAL,
    dew_point_c REAL,
    is_dark INTEGER,
    light_value INTEGER,
    is_wet INTEGER,
    wet_value INTEGER,
    sky_temperature_c REAL,
    ground_temperature_c REAL,
    wind_speed_mps REAL,
    wind_dir_deg REAL,
    rain_rate_mm_hr REAL,
    rain_hourly_mm REAL,
    rain_daily_mm REAL,
    wind_gust_10min REAL,
    wind_gust_hour REAL,
    wind_gust_today REAL,
    future_obs_1 REAL,
    future_obs_2 REAL,
    future_obs_3 REAL,
    future_obs_4 REAL,
    future_obs_5 REAL,
    synced INTEGER NOT NULL DEFAULT 0 CHECK (synced IN (0, 1))
);

CREATE TABLE IF NOT EXISTS rain_tip_samples (
    sample_id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    tips INTEGER NOT NULL CHECK (tips > 0)
);
CREATE INDEX IF NOT EXISTS rain_tip_samples_timestamp_idx
    ON rain_tip_samples(timestamp);

CREATE TABLE IF NOT EXISTS status (
    status_id INTEGER PRIMARY KEY AUTOINCREMENT,
    observation_id INTEGER NOT NULL UNIQUE,
    system TEXT,
    bme280 TEXT,
    lm393_light TEXT,
    lm393_rain TEXT,
    mlx90614_1 TEXT,
    mlx90614_2 TEXT,
    anemometer TEXT,
    windvane TEXT,
    rainbucket TEXT,
    future_device_1 TEXT,
    future_device_2 TEXT,
    future_device_3 TEXT,
    future_device_4 TEXT,
    future_device_5 TEXT,
    FOREIGN KEY (observation_id)
        REFERENCES observations(observation_id) ON DELETE CASCADE
);
"""


def connect_database(path=DATABASE_PATH):
    # Open SQLite with foreign-key enforcement enabled.
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize_database(path=DATABASE_PATH):
    # Create the SQLite database and tables if they do not exist.
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with connect_database(path) as connection:
        connection.executescript(SCHEMA)
        # Preserve existing observation rows when upgrading the schema.
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(observations)")
        }
        if "rain_amount_mm" in columns and "rain_rate_mm_hr" not in columns:
            connection.execute(
                "ALTER TABLE observations RENAME COLUMN rain_amount_mm TO rain_rate_mm_hr"
            )
    return path


OBSERVATION_FIELDS = (
    "timestamp", "bme_temp_c", "temp_c", "mlx_temp_c",
    "humidity_pct", "pressure_hpa", "pressure_sealevel_hpa",
    "dew_point_c", "is_dark", "light_value", "is_wet", "wet_value",
    "sky_temperature_c", "ground_temperature_c", "wind_speed_mps",
    "wind_dir_deg", "rain_rate_mm_hr", "rain_hourly_mm",
    "rain_daily_mm", "wind_gust_10min", "wind_gust_hour",
    "wind_gust_today",
)

STATUS_FIELDS = (
    "system", "bme280", "lm393_light", "lm393_rain",
    "mlx90614_1", "mlx90614_2", "anemometer", "windvane",
    "rainbucket",
)


def save_observation(reading, path=DATABASE_PATH):
    # Atomically store one weather snapshot and its sensor statuses.
    values = [reading.get(field) for field in OBSERVATION_FIELDS]
    status = reading.get("status", {})
    status_values = [status.get(field) for field in STATUS_FIELDS]

    with connect_database(path) as connection:
        cursor = connection.execute(
            f"INSERT INTO observations ({', '.join(OBSERVATION_FIELDS)}) "
            f"VALUES ({', '.join('?' for _ in OBSERVATION_FIELDS)})",
            values,
        )
        connection.execute(
            f"INSERT INTO status (observation_id, {', '.join(STATUS_FIELDS)}) "
            f"VALUES (?, {', '.join('?' for _ in STATUS_FIELDS)})",
            [cursor.lastrowid, *status_values],
        )


def rain_history_cutoff(now):
    """Retain samples from the earlier of local midnight and 60 minutes ago."""
    midnight = now.astimezone(STATION_TIMEZONE).replace(
        hour=0, minute=0, second=0, microsecond=0
    ).astimezone(timezone.utc)
    return min(midnight, now - timedelta(hours=1))


def load_rain_samples(path=DATABASE_PATH):
    """Recover tip history relevant to today's and rolling rainfall totals."""
    now = datetime.now(timezone.utc)
    cutoff = rain_history_cutoff(now).isoformat()
    with connect_database(path) as connection:
        rows = connection.execute(
            "SELECT timestamp, tips FROM rain_tip_samples "
            "WHERE timestamp >= ? ORDER BY timestamp, sample_id", (cutoff,)
        ).fetchall()
    return [(datetime.fromisoformat(stamp), tips) for stamp, tips in rows]


def save_rain_sample(timestamp, tips, path=DATABASE_PATH):
    """Durably store each nonzero Nano tip count before reporting it."""
    if tips <= 0:
        raise ValueError("Rain sample must contain positive tips")
    cutoff = rain_history_cutoff(timestamp).isoformat()
    with connect_database(path) as connection:
        connection.execute(
            "INSERT INTO rain_tip_samples (timestamp, tips) VALUES (?, ?)",
            (timestamp.isoformat(), tips),
        )
        connection.execute(
            "DELETE FROM rain_tip_samples WHERE timestamp < ?", (cutoff,)
        )


if __name__ == "__main__":
    print(initialize_database())
