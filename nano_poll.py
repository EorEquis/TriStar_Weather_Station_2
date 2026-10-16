###########################################################################
# Created : 2026-10-16 GB
# Purpose : Poll the Nano UART and calculate wind and rain observations
# Notes   : Most code was generated with assistance from ChatGPT.
#           Chat title: Wx Station N
#           OpenAI model/version: GPT-6
###########################################################################

"""Poll the Nano UART once per second; compute wind, gust, and rain observations."""

import asyncio
import json
import math
import serial
import time

from collections import deque
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

RAIN_MM_PER_TIP = 0.2794
STATION_TIMEZONE = ZoneInfo("America/Chicago")
WIND_MPS_PER_HZ = 1.492 * 0.44704


async def collect_nano(latest, enabled, port_name="/dev/serial0"):
    """Update the shared API snapshot without blocking the five-second sensors."""
    wind_enabled = enabled["anemometer"]
    direction_enabled = enabled["windvane"]
    rain_enabled = enabled["rainbucket"]
    light_enabled = enabled["lm393_light"]
    rain_sensor_enabled = enabled["lm393_rain"]
    if not (
        wind_enabled or direction_enabled or rain_enabled
        or light_enabled or rain_sensor_enabled
    ):
        return

    port = None
    last_poll = None
    wind_samples = deque()  # (monotonic timestamp, speed, elapsed)
    gust_samples = deque()  # (UTC datetime, 3-second average)
    rain_samples = deque()  # (UTC datetime, bucket tips)
    try:
        while True:
            cycle_start = time.monotonic()
            try:
                if port is None:
                    port = await asyncio.to_thread(
                        serial.Serial, port_name, 9600, timeout=0.5, write_timeout=0.5
                    )
                    # Opening a serial port starts a fresh measurement interval.
                    last_poll = time.monotonic()
                (
                    pulses, tips, direction, light_analog,
                    rain_digital, rain_analog,
                ) = await asyncio.to_thread(poll_once, port)
                now = time.monotonic()
                utc_now = datetime.now(timezone.utc)
                elapsed = now - last_poll
                last_poll = now
                if elapsed <= 0:
                    raise ValueError("Non-positive Nano polling interval")

                if light_enabled:
                    if not 0 <= light_analog <= 1023:
                        raise ValueError(f"Invalid light ADC reading: {light_analog}")
                    latest["light_value"] = light_analog
                    latest["status"]["lm393_light"] = "available"
                if rain_sensor_enabled:
                    if rain_digital not in (0, 1):
                        raise ValueError(f"Invalid rain sensor digital reading: {rain_digital}")
                    if not 0 <= rain_analog <= 1023:
                        raise ValueError(f"Invalid rain sensor ADC reading: {rain_analog}")
                    latest["is_wet"] = rain_digital == 0
                    latest["wet_value"] = rain_analog
                    latest["status"]["lm393_rain"] = "available"
                if wind_enabled:
                    speed = pulses / elapsed * WIND_MPS_PER_HZ
                    latest["wind_speed_mps"] = round(speed, 3)
                    wind_samples.append((now, speed, elapsed))
                    while wind_samples and now - wind_samples[0][0] > 3:
                        wind_samples.popleft()
                    # Weighted average for uneven intervals; require approximately
                    # three seconds of samples before reporting a gust.
                    covered = sum(item[2] for item in wind_samples)
                    if covered >= 2.5:
                        average = sum(
                            item[1] * item[2] for item in wind_samples
                        ) / covered
                        gust_samples.append((utc_now, average))
                    latest["status"]["anemometer"] = "available"
                if direction_enabled:
                    if not math.isfinite(direction) or not 0 <= direction < 360:
                        raise ValueError(f"Invalid wind direction: {direction}")
                    latest["wind_dir_deg"] = direction
                    latest["status"]["windvane"] = "available"
                if rain_enabled:
                    if tips < 0:
                        raise ValueError("Negative rain tip count")
                    rain_samples.append((utc_now, tips))
                    latest["rain_amount_mm"] = round(tips * RAIN_MM_PER_TIP, 4)
                    latest["status"]["rainbucket"] = "available"
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(f"Nano polling error: {exc}", flush=True)
                if port is not None:
                    await asyncio.to_thread(port.close)
                    port = None
                last_poll = None
                for name, active in (
                    ("anemometer", wind_enabled),
                    ("windvane", direction_enabled),
                    ("rainbucket", rain_enabled),
                    ("lm393_rain", rain_sensor_enabled),
                ):
                    if active:
                        latest["status"][name] = "error"
                if light_enabled:
                    latest["light_value"] = None
                    latest["status"]["lm393_light"] = "error"
                if rain_sensor_enabled:
                    latest["is_wet"] = None
                    latest["wet_value"] = None
                if wind_enabled:
                    latest["wind_speed_mps"] = None
                if direction_enabled:
                    latest["wind_dir_deg"] = None
                if rain_enabled:
                    latest["rain_amount_mm"] = None
            # Advance reporting windows even when the Nano cannot be read.
            utc_now = datetime.now(timezone.utc)
            local_midnight = utc_now.astimezone(STATION_TIMEZONE).replace(
                hour=0, minute=0, second=0, microsecond=0
            ).astimezone(timezone.utc)
            if wind_enabled:
                cutoff = min(utc_now - timedelta(hours=24), local_midnight)
                while gust_samples and gust_samples[0][0] < cutoff:
                    gust_samples.popleft()
                for key, start in (
                    ("wind_gust_10min", utc_now - timedelta(minutes=10)),
                    ("wind_gust_hour", utc_now - timedelta(hours=1)),
                    ("wind_gust_today", local_midnight),
                ):
                    values = [
                        value for stamp, value in gust_samples if stamp >= start
                    ]
                    latest[key] = round(max(values), 3) if values else None
            if rain_enabled:
                cutoff = min(utc_now - timedelta(hours=1), local_midnight)
                while rain_samples and rain_samples[0][0] < cutoff:
                    rain_samples.popleft()
                hourly = [
                    count for stamp, count in rain_samples
                    if stamp >= utc_now - timedelta(hours=1)
                ]
                daily = [
                    count for stamp, count in rain_samples
                    if stamp >= local_midnight
                ]
                # A successful zero-tip reading is still a valid measurement.
                latest["rain_hourly_mm"] = (
                    round(sum(hourly) * RAIN_MM_PER_TIP, 4) if hourly else None
                )
                latest["rain_daily_mm"] = (
                    round(sum(daily) * RAIN_MM_PER_TIP, 4) if daily else None
                )
            await asyncio.sleep(max(0, 1 - (time.monotonic() - cycle_start)))
    finally:
        if port is not None:
            await asyncio.to_thread(port.close)


def poll_once(port):
    port.write(b"READ\n")
    port.flush()
    response = port.readline()
    if not response:
        raise TimeoutError("Nano READ timed out")
    payload = json.loads(response.decode("ascii"))
    return (
        int(payload["anemometer_pulses"]),
        int(payload["rain_bucket_tips"]),
        float(payload["wind_direction_deg"]),
        int(payload["light_analog"]),
        int(payload["rain_sensor_digital"]),
        int(payload["rain_sensor_analog"]),
    )
