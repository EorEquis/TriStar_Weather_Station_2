"""Local SQLite schema for weather observations and sensor statuses."""

import sqlite3
from pathlib import Path

DATABASE_PATH = Path(__file__).resolve().parent / "data" / "weather.sqlite3"

SCHEMA = """
CREATE TABLE IF NOT EXISTS observations (
    observation_id INTEGER PRIMARY KEY,
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
    rain_amount_mm REAL,
    rain_hourly_mm REAL,
    rain_daily_mm REAL,
    wind_gust_10min REAL,
    wind_gust_hour REAL,
    wind_gust_today REAL,
    synced INTEGER NOT NULL DEFAULT 0 CHECK (synced IN (0, 1)),
    future_obs_1 REAL,
    future_obs_2 REAL,
    future_obs_3 REAL,
    future_obs_4 REAL,
    future_obs_5 REAL
);

CREATE TABLE IF NOT EXISTS status (
    status_id INTEGER PRIMARY KEY,
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


def initialize_database(path=DATABASE_PATH):
    """Create the SQLite database and tables if they do not exist."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(SCHEMA)
    return path


if __name__ == "__main__":
    print(initialize_database())
