###########################################################################
# Created : 2026-10-16 GB
# Purpose : Synchronize local weather observations to MySQL
# Notes   : Most code was generated with assistance from ChatGPT.
#           Chat title: Wx Station N
#           OpenAI model/version: GPT-6
###########################################################################

"""Synchronize locally recorded weather observations to MySQL."""

import os
import pymysql
import sqlite3

from contextlib import closing
from datetime import datetime, timedelta, timezone
from database import DATABASE_PATH, connect_database
from dotenv import load_dotenv

BATCH_SIZE = 1000
load_dotenv()

def sync_interval_seconds():
    value = float(os.getenv("MYSQL_SYNC_INTERVAL_SECONDS", "300"))
    if not (0 < value < float("inf")):
        raise ValueError("MYSQL_SYNC_INTERVAL_SECONDS must be a positive finite number")
    return value


SYNC_INTERVAL_SECONDS = sync_interval_seconds()

# Explicit column lists preserve the existing SQLite/MySQL name mappings.
OBSERVATION_COLUMNS = (
    "observation_id", "timestamp", "bme_temp_c", "temp_c", "mlx1_temp_c",
    "humidity_pct", "pressure_hpa", "pressure_sealevel_hpa", "dew_point_c",
    "is_dark", "light_value", "is_wet", "wet_value", "sky_temperature_c",
    "ground_temperature_c", "wind_speed_mps", "wind_dir_deg", "rain_rate_mm_hr",
    "rain_hourly_mm", "rain_daily_mm", "wind_gust_10min", "wind_gust_hour",
    "wind_gust_today", "mlx2_temp_c", "future_obs_2", "future_obs_3",
    "future_obs_4", "future_obs_5",
)
STATUS_COLUMNS = (
    "status_id", "observation_id", "system", "bme280", "lm393_light",
    "lm393_rain", "mlx90614_1", "mlx90614_2", "anemometer", "windvane",
    "rainbucket", "future_device_1", "future_device_2", "future_device_3",
    "future_device_4", "future_device_5",
)

MYSQL_OBSERVATION_COLUMNS = tuple(
    "obs_timestamp" if column == "timestamp" else column
    for column in OBSERVATION_COLUMNS
) + ("synced",)
MYSQL_STATUS_COLUMNS = tuple(
    "sys" if column == "system" else column
    for column in STATUS_COLUMNS
)


def insert_sql(table, columns):
    quoted = ", ".join(f"`{column}`" for column in columns)
    placeholders = ", ".join(["%s"] * len(columns))
    updates = ", ".join(
        f"`{column}` = VALUES(`{column}`)"
        for column in columns if column not in ("observation_id", "status_id")
    )
    return (
        f"INSERT INTO `{table}` ({quoted}) VALUES ({placeholders}) "
        f"ON DUPLICATE KEY UPDATE {updates}"
    )


OBSERVATION_INSERT = insert_sql("observations", MYSQL_OBSERVATION_COLUMNS)
STATUS_INSERT = insert_sql("status", MYSQL_STATUS_COLUMNS)


def mysql_connection():
    return pymysql.connect(
        host=os.environ["MYSQL_HOST"],
        port=int(os.getenv("MYSQL_PORT", "3306")),
        user=os.environ["MYSQL_USER"],
        password=os.environ["MYSQL_PASSWORD"],
        database=os.environ["MYSQL_DATABASE"],
        charset="utf8mb4",
        connect_timeout=10,
        read_timeout=30,
        write_timeout=30,
        autocommit=False,
    )


def mysql_observation(row):
    values = [row[column] for column in OBSERVATION_COLUMNS]
    # SQLite stores UTC ISO-8601 text; MySQL DATETIME stores UTC without tz.
    values[1] = datetime.fromisoformat(values[1]).astimezone(
        timezone.utc
    ).replace(tzinfo=None)
    # This row is being inserted into MySQL, so it is synchronized by definition.
    values.append(1)
    return values


def sync_once(path=DATABASE_PATH):
    # Transfer a batch, mark it synced after MySQL commit, then purge old data.
    # Safe to retry after a crash between the remote commit and local update.
    with closing(connect_database(path)) as local, local:
        local.row_factory = sqlite3.Row
        observations = local.execute(
            "SELECT * FROM observations WHERE synced = 0 "
            "ORDER BY observation_id LIMIT ?", (BATCH_SIZE,)
        ).fetchall()

        if observations:
            ids = [row["observation_id"] for row in observations]
            placeholders = ", ".join("?" for _ in ids)
            statuses = local.execute(
                f"SELECT * FROM status WHERE observation_id IN ({placeholders})",
                ids,
            ).fetchall()
            if len(statuses) != len(observations):
                raise RuntimeError("Missing local status row; sync aborted")

            remote = mysql_connection()
            try:
                with remote.cursor() as cursor:
                    cursor.executemany(
                        OBSERVATION_INSERT,
                        [mysql_observation(row) for row in observations],
                    )
                    cursor.executemany(
                        STATUS_INSERT,
                        [
                            [row[column] for column in STATUS_COLUMNS]
                            for row in statuses
                        ],
                    )
                remote.commit()
            except Exception:
                remote.rollback()
                raise
            finally:
                remote.close()

            local.executemany(
                "UPDATE observations SET synced = 1 WHERE observation_id = ?",
                [(id_,) for id_ in ids],
            )
            local.commit()

        cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        local.execute(
            "DELETE FROM observations WHERE synced = 1 AND timestamp < ?",
            (cutoff,),
        )
        local.commit()
        return len(observations)


def sync_pending(path=DATABASE_PATH):
    # Drain a backlog in bounded batches; no rows are lost on retry.
    total = 0
    while True:
        count = sync_once(path)
        total += count
        if count < BATCH_SIZE:
            return total
