###########################################################################
# Created : 2026-10-11 GB
# Purpose : Weather API for TriStar WXStation V2
# Notes   : Most code was generated with assistance from ChatGPT.
#           Chat title: Wx Station N
#           OpenAI model/version: GPT-6
###########################################################################

import asyncio
import bme280
import math
import os
import smbus2
import uvicorn

from contextlib import asynccontextmanager, suppress
from database import initialize_database, save_observation
from datetime import datetime, timezone
from dotenv import load_dotenv
from fastapi import FastAPI
from mysql_sync import SYNC_INTERVAL_SECONDS, sync_pending
from nano_poll import collect_nano
from system_metrics import HostSystemMetrics

BME280_ADDRESS = 0x76
I2C_BUS = 1
MLX90614_1_ADDRESS = 0x5A
MLX90614_2_ADDRESS = 0x5B
MLX90614_AMBIENT_TEMP_REGISTER = 0x06
MLX90614_OBJECT_TEMP_REGISTER = 0x07
SAMPLE_INTERVAL = 5
I2C_RETRY_DELAY_SECONDS = 0.1

load_dotenv()

STATION_ELEVATION_M = float(os.environ["STATION_ELEVATION"])

READ_ANEMOMETER = os.getenv("READ_ANEMOMETER", "false").strip().lower() == "true"
READ_BME = os.getenv("READ_BME", "false").strip().lower() == "true"
READ_LIGHT = os.getenv("READ_LIGHT", "false").strip().lower() == "true"
READ_MLX_1 = os.getenv("READ_MLX_1", "false").strip().lower() == "true"
READ_MLX_2 = os.getenv("READ_MLX_2", "false").strip().lower() == "true"
READ_RAINBUCKET = os.getenv("READ_RAINBUCKET", "false").strip().lower() == "true"
READ_RAIN_SENSOR = os.getenv("READ_RAIN_SENSOR", "false").strip().lower() == "true"
READ_WINDVANE = os.getenv("READ_WINDVANE", "false").strip().lower() == "true"

# Allsky overlay temperature color thresholds (degrees Celsius).
ALLSKY_TEMP_COLD_C = float(os.getenv("ALLSKY_TEMP_COLD_C", "10"))
ALLSKY_TEMP_WARM_C = float(os.getenv("ALLSKY_TEMP_WARM_C", "25"))
ALLSKY_TEMP_HOT_C = float(os.getenv("ALLSKY_TEMP_HOT_C", "32"))

if not (
    math.isfinite(ALLSKY_TEMP_COLD_C)
    and math.isfinite(ALLSKY_TEMP_WARM_C)
    and math.isfinite(ALLSKY_TEMP_HOT_C)
    and ALLSKY_TEMP_COLD_C < ALLSKY_TEMP_WARM_C < ALLSKY_TEMP_HOT_C
):
    raise ValueError("Allsky temperature thresholds must be finite and strictly increasing")


ALLSKY_DEWPOINT_YELLOW_C = float(os.getenv("ALLSKY_DEWPOINT_YELLOW_C", "16"))
ALLSKY_DEWPOINT_RED_C = float(os.getenv("ALLSKY_DEWPOINT_RED_C", "21"))
ALLSKY_HUMIDITY_YELLOW_PCT = float(os.getenv("ALLSKY_HUMIDITY_YELLOW_PCT", "60"))
ALLSKY_HUMIDITY_RED_PCT = float(os.getenv("ALLSKY_HUMIDITY_RED_PCT", "80"))
ALLSKY_RAIN_YELLOW_MM_HR = float(os.getenv("ALLSKY_RAIN_YELLOW_MM_HR", "0"))
ALLSKY_RAIN_RED_MM_HR = float(os.getenv("ALLSKY_RAIN_RED_MM_HR", "2"))

ALLSKY_RAIN_DAILY_BLUE_MM = float(os.getenv("ALLSKY_RAIN_DAILY_BLUE_MM", "0"))
ALLSKY_RAIN_DAILY_RED_MM = float(os.getenv("ALLSKY_RAIN_DAILY_RED_MM", "5"))

ALLSKY_WIND_YELLOW_MPS = float(os.getenv("ALLSKY_WIND_YELLOW_MPS", "5"))
ALLSKY_WIND_RED_MPS = float(os.getenv("ALLSKY_WIND_RED_MPS", "10"))
ALLSKY_GUST_YELLOW_MPS = float(os.getenv("ALLSKY_GUST_YELLOW_MPS", "8"))
ALLSKY_GUST_RED_MPS = float(os.getenv("ALLSKY_GUST_RED_MPS", "15"))

SENSORS = {
    "bme280": READ_BME,
    "lm393_light": READ_LIGHT,
    "lm393_rain": READ_RAIN_SENSOR,
    "mlx90614_1": READ_MLX_1,
    "mlx90614_2": READ_MLX_2,
    "anemometer": READ_ANEMOMETER,
    "windvane": READ_WINDVANE,
    "rainbucket": READ_RAINBUCKET,
}

system_metrics = HostSystemMetrics()

latest = {
    "timestamp": None,
    "bme_temp_c": None,
    "temp_c": None,
    "mlx1_temp_c": None,
    "mlx2_temp_c": None,
    "humidity_pct": None,
    "pressure_hpa": None,
    "pressure_sealevel_hpa": None,
    "dew_point_c": None,
    "is_dark": None,
    "light_value": None,
    "is_wet": None,
    "wet_value": None,
    "sky_temperature_c": None,
    "ground_temperature_c": None,
    "wind_speed_mps": None,
    "wind_dir_deg": None,
    "rain_rate_mm_hr": None,
    "rain_hourly_mm": None,
    "rain_daily_mm": None,
    "wind_gust_10min": None,
    "wind_gust_hour": None,
    "wind_gust_today": None,
    "system": system_metrics.empty(),
    "status": {
        "system": "unavailable",
        **{
            name: "unavailable" if not enabled else "error"
            for name, enabled in SENSORS.items()
        },
    },
}


def calculate_dew_point(temp_c, humidity):
    if humidity <= 0:
        return None

    a = 17.625
    b = 243.04
    gamma = math.log(humidity / 100) + a * temp_c / (b + temp_c)
    return b * gamma / (a - gamma)


def calculate_sea_level_pressure(pressure_hpa, temperature_c):
    """Reduce station pressure to sea level using measured ambient temperature."""
    temperature_k = temperature_c + 273.15
    lapse_rate = 0.0065  # K/m, standard tropospheric lapse rate
    exponent = 9.80665 / (287.05 * lapse_rate)
    return pressure_hpa * (
        (temperature_k + lapse_rate * STATION_ELEVATION_M) / temperature_k
    ) ** exponent


async def read_i2c_with_retry(read):
    """Retry a failed sensor transaction once, after 100 ms."""
    try:
        return read()
    except Exception:
        await asyncio.sleep(I2C_RETRY_DELAY_SECONDS)
        return read()


def read_mlx90614(bus, address):
    ambient_raw = bus.read_word_data(
        address, MLX90614_AMBIENT_TEMP_REGISTER
    )
    raw = bus.read_word_data(
        address, MLX90614_OBJECT_TEMP_REGISTER
    )
    if ambient_raw & 0x8000:
        raise ValueError("MLX90614 ambient read flagged invalid")
    if raw & 0x8000:
        raise ValueError("MLX90614 temperature read flagged invalid")
    return ambient_raw, raw


async def collect_weather():
    bus = None
    calibration = None

    try:
        while True:
            # Retry I2C bus and BME280 calibration on subsequent sample cycles.
            if (READ_BME or READ_MLX_1 or READ_MLX_2) and bus is None:
                try:
                    bus = smbus2.SMBus(I2C_BUS)
                except Exception as exc:
                    print(f"I2C initialization error: {exc}", flush=True)

            if READ_BME and bus is not None and calibration is None:
                try:
                    calibration = bme280.load_calibration_params(
                        bus, BME280_ADDRESS
                    )
                except Exception as exc:
                    latest["status"]["bme280"] = "error"
                    print(f"BME280 initialization error: {exc}", flush=True)

            latest["timestamp"] = datetime.now(
                timezone.utc
            ).isoformat()

            if READ_BME:
                if bus is not None and calibration is not None:
                    try:
                        reading = await read_i2c_with_retry(
                            lambda: bme280.sample(bus, BME280_ADDRESS, calibration)
                        )

                        latest["bme_temp_c"] = round(
                            reading.temperature, 2
                        )
                        latest["humidity_pct"] = round(
                            reading.humidity, 2
                        )
                        latest["pressure_hpa"] = round(
                            reading.pressure, 2
                        )
                        latest["pressure_sealevel_hpa"] = round(
                            calculate_sea_level_pressure(
                                reading.pressure, reading.temperature
                            ), 2
                        )
                        latest["dew_point_c"] = round(
                            calculate_dew_point(
                                reading.temperature,
                                reading.humidity
                            ), 2
                        )

                        latest["status"]["bme280"] = "available"

                    except Exception as exc:
                        latest["bme_temp_c"] = None
                        latest["humidity_pct"] = None
                        latest["pressure_hpa"] = None
                        latest["pressure_sealevel_hpa"] = None
                        latest["dew_point_c"] = None
                        latest["status"]["bme280"] = "error"
                        print(f"BME280 read error: {exc}", flush=True)

            if READ_MLX_1:
                try:
                    if bus is None:
                        raise RuntimeError("I2C bus unavailable")

                    ambient_raw, raw = await read_i2c_with_retry(
                        lambda: read_mlx90614(bus, MLX90614_1_ADDRESS)
                    )

                    latest["mlx1_temp_c"] = round(
                        (ambient_raw & 0x7FFF) * 0.02 - 273.15, 2
                    )
                    latest["sky_temperature_c"] = round(
                        (raw & 0x7FFF) * 0.02 - 273.15, 2
                    )
                    latest["status"]["mlx90614_1"] = "available"

                except Exception as exc:
                    latest["mlx1_temp_c"] = None
                    latest["sky_temperature_c"] = None
                    latest["status"]["mlx90614_1"] = "error"
                    print(f"MLX90614 read error: {exc}", flush=True)

            if READ_MLX_2:
                try:
                    if bus is None:
                        raise RuntimeError("I2C bus unavailable")

                    ambient_raw, raw = await read_i2c_with_retry(
                        lambda: read_mlx90614(bus, MLX90614_2_ADDRESS)
                    )

                    latest["mlx2_temp_c"] = round(
                        (ambient_raw & 0x7FFF) * 0.02 - 273.15, 2
                    )
                    latest["ground_temperature_c"] = round(
                        (raw & 0x7FFF) * 0.02 - 273.15, 2
                    )
                    latest["status"]["mlx90614_2"] = "available"

                except Exception as exc:
                    latest["mlx2_temp_c"] = None
                    latest["ground_temperature_c"] = None
                    latest["status"]["mlx90614_2"] = "error"
                    print(f"MLX90614 #2 read error: {exc}", flush=True)

            # Reopen the bus after an I/O failure instead of requiring
            # a container restart. Calibration is reloaded with the new bus.
            if bus is not None and (
                (READ_BME and latest["status"]["bme280"] == "error" and calibration is not None)
                or (READ_MLX_1 and latest["status"]["mlx90614_1"] == "error")
                or (READ_MLX_2 and latest["status"]["mlx90614_2"] == "error")
            ):
                bus.close()
                bus = None
                calibration = None

            latest["temp_c"] = latest["bme_temp_c"]

            for name, enabled in SENSORS.items():
                if not enabled:
                    latest["status"][name] = "unavailable"

            enabled_sensors = [
                name for name, enabled in SENSORS.items()
                if enabled
            ]

            if not enabled_sensors:
                latest["status"]["system"] = "unavailable"
            elif all(
                latest["status"][name] == "available"
                for name in enabled_sensors
            ):
                latest["status"]["system"] = "available"
            else:
                latest["status"]["system"] = "error"

            latest["system"] = await asyncio.to_thread(system_metrics.collect)
            snapshot = {**latest, "status": latest["status"].copy(), "system": latest["system"].copy()}
            try:
                await asyncio.to_thread(save_observation, snapshot)
            except Exception as exc:
                print(f"SQLite observation write error: {exc}", flush=True)

            await asyncio.sleep(SAMPLE_INTERVAL)

    finally:
        if bus is not None:
            bus.close()



@asynccontextmanager
async def lifespan(app: FastAPI):
    initialize_database()
    task = asyncio.create_task(collect_weather())
    nano_task = asyncio.create_task(collect_nano(latest, SENSORS))
    sync_task = asyncio.create_task(sync_weather())

    try:
        yield
    finally:
        task.cancel()
        nano_task.cancel()
        sync_task.cancel()

        with suppress(asyncio.CancelledError):
            await task
        with suppress(asyncio.CancelledError):
            await nano_task
        with suppress(asyncio.CancelledError):
            await sync_task


async def sync_weather():
    while True:
        try:
            transferred = await asyncio.to_thread(sync_pending)
            if transferred:
                print(f"MySQL synchronized {transferred} observations", flush=True)
        except Exception as exc:
            print(f"MySQL synchronization error: {exc}", flush=True)
        await asyncio.sleep(SYNC_INTERVAL_SECONDS)


app = FastAPI(
    title="TriStar Weather Station 2.0",
    lifespan=lifespan
)

app.add_api_route(
    "/api/weather/current",
    lambda: latest,
    methods=["GET"]
)


def overlay_item(label, value, color="#FFFFFF"):
    return {
        "description": label,
        "type": "string",
        "value": {"value": value, "fill": color if value is not None else None, "expires": 0},
    }


def traffic_color(value, yellow, red):
    if value >= red:
        return "#FF0000"
    if value >= yellow:
        return "#FFFF00"
    return "#00FF00"


@app.get("/api/weather/allsky")
def allsky_weather():
    """Return display-formatted weather readings for the Allsky overlay."""
    result = {}
    temp = latest["temp_c"]
    if temp is None or not math.isfinite(temp):
        result["temp_c"] = overlay_item("Temperature °C", None)
    else:
        if temp <= ALLSKY_TEMP_COLD_C:
            color = "#0000FF"
        elif temp < ALLSKY_TEMP_WARM_C:
            color = "#00FF00"
        elif temp < ALLSKY_TEMP_HOT_C:
            color = "#FFFF00"
        else:
            color = "#FF0000"
        result["temp_c"] = overlay_item("Temperature °C", f"{temp:.1f}° C", color)

    for output_key, source_key, label in (
        ("sky_temp_c", "sky_temperature_c", "Sky Temperature"),
        ("ground_temp_c", "ground_temperature_c", "Ground Temperature"),
    ):
        reading = latest[source_key]
        result[output_key] = overlay_item(
            label,
            f"{reading:.1f}° C" if reading is not None and math.isfinite(reading) else None,
        )

    readings = (
        ("dew_point_c", "Dew Point", "{:.2f}°C",
         ALLSKY_DEWPOINT_YELLOW_C, ALLSKY_DEWPOINT_RED_C),
        ("humidity_pct", "Humidity", "{:.0f}%",
         ALLSKY_HUMIDITY_YELLOW_PCT, ALLSKY_HUMIDITY_RED_PCT),
        ("rain_rate_mm_hr", "Rain Rate", "{:.2f} mm/hr",
         ALLSKY_RAIN_YELLOW_MM_HR, ALLSKY_RAIN_RED_MM_HR),
    )
    for key, label, fmt, yellow, red in readings:
        reading = latest[key]
        if reading is None or not math.isfinite(reading):
            result[key] = overlay_item(label, None)
        else:
            color = traffic_color(reading, yellow, red)
            if key == "rain_rate_mm_hr" and reading <= yellow:
                color = "#00FF00"
            result[key] = overlay_item(label, fmt.format(reading), color)

    pressure = latest["pressure_hpa"]
    result["pressure_hpa"] = overlay_item(
        "Station Pressure",
        f"{pressure * 0.0295299830714:.2f} inHg"
        if pressure is not None and math.isfinite(pressure) else None,
    )
    daily = latest["rain_daily_mm"]
    if daily is None or not math.isfinite(daily):
        result["rain_daily_mm"] = overlay_item("Daily Rainfall", None)
    else:
        if daily <= ALLSKY_RAIN_DAILY_BLUE_MM:
            daily_color = "#00FF00"
        elif daily <= ALLSKY_RAIN_DAILY_RED_MM:
            daily_color = "#0000FF"
        else:
            daily_color = "#FF0000"
        result["rain_daily_mm"] = overlay_item(
            "Daily Rainfall", f"{daily:.2f} mm", daily_color
        )
    wind_values = (
        ("wind_speed_mps", "Wind Speed", ALLSKY_WIND_YELLOW_MPS, ALLSKY_WIND_RED_MPS),
        ("wind_gust_hour", "Hourly Wind Gust", ALLSKY_GUST_YELLOW_MPS, ALLSKY_GUST_RED_MPS),
        ("wind_gust_today", "Daily Wind Gust", ALLSKY_GUST_YELLOW_MPS, ALLSKY_GUST_RED_MPS),
    )
    for key, label, yellow, red in wind_values:
        speed = latest[key]
        if speed is None or not math.isfinite(speed):
            result[key] = overlay_item(label, None)
        else:
            result[key] = overlay_item(
                label, f"{speed:.2f} m/s", traffic_color(speed, yellow, red)
            )

    direction = latest["wind_dir_deg"]
    if direction is None or not math.isfinite(direction):
        result["wind_dir_deg"] = overlay_item("Wind Direction", None)
    else:
        compass = (
            "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
            "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW",
        )
        index = int((direction % 360 + 11.25) // 22.5) % 16
        result["wind_dir_deg"] = overlay_item("Wind Direction", compass[index])
    return result


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
