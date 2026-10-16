
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

from collections import deque
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timezone
from dotenv import load_dotenv
from fastapi import FastAPI
from gpiozero import DigitalInputDevice
from nano_poll import collect_nano
from database import initialize_database, save_observation

I2C_BUS = 1
BME280_ADDRESS = 0x76
LIGHT_GPIO = 17
MLX90614_ADDRESS = 0x5A
MLX90614_AMBIENT_TEMP_REGISTER = 0x06
MLX90614_OBJECT_TEMP_REGISTER = 0x07
SAMPLE_INTERVAL = 5
TEMP_AVERAGE_SAMPLES = 10
STATION_ELEVATION_M = 321.34  # Ground elevation 318.34 m + sensor height 3 m

load_dotenv()

READ_BME = os.getenv("READ_BME", "false").strip().lower() == "true"
READ_LIGHT = os.getenv("READ_LIGHT", "false").strip().lower() == "true"
READ_RAIN_SENSOR = os.getenv("READ_RAIN_SENSOR", "false").strip().lower() == "true"
READ_MLX_1 = os.getenv("READ_MLX_1", "false").strip().lower() == "true"
READ_MLX_2 = os.getenv("READ_MLX_2", "false").strip().lower() == "true"
READ_ANEMOMETER = os.getenv("READ_ANEMOMETER", "false").strip().lower() == "true"
READ_WINDVANE = os.getenv("READ_WINDVANE", "false").strip().lower() == "true"
READ_RAINBUCKET = os.getenv("READ_RAINBUCKET", "false").strip().lower() == "true"

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

latest = {
    "timestamp": None,
    "bme_temp_c": None,
    "temp_c": None,
    "mlx_temp_c": None,
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
    "rain_amount_mm": None,
    "rain_hourly_mm": None,
    "rain_daily_mm": None,
    "wind_gust_10min": None,
    "wind_gust_hour": None,
    "wind_gust_today": None,
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


async def collect_weather():
    bus = None
    calibration = None
    light = None
    temperature_history = deque(maxlen=TEMP_AVERAGE_SAMPLES)

    try:
        if READ_BME or READ_MLX_1:
            try:
                bus = smbus2.SMBus(I2C_BUS)
            except Exception as exc:
                print(f"I2C initialization error: {exc}", flush=True)

        if READ_BME and bus is not None:
            try:
                calibration = bme280.load_calibration_params(
                    bus, BME280_ADDRESS
                )
            except Exception as exc:
                latest["status"]["bme280"] = "error"
                print(f"BME280 initialization error: {exc}", flush=True)

        if READ_LIGHT:
            try:
                light = DigitalInputDevice(
                    LIGHT_GPIO, pull_up=False
                )
            except Exception as exc:
                latest["status"]["lm393_light"] = "error"
                print(f"LM393 light initialization error: {exc}", flush=True)

        while True:
            latest["timestamp"] = datetime.now(
                timezone.utc
            ).isoformat()

            if READ_BME:
                if bus is not None and calibration is not None:
                    try:
                        reading = bme280.sample(
                            bus, BME280_ADDRESS, calibration
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

                    ambient_raw = bus.read_word_data(
                        MLX90614_ADDRESS, MLX90614_AMBIENT_TEMP_REGISTER
                    )
                    raw = bus.read_word_data(
                        MLX90614_ADDRESS, MLX90614_OBJECT_TEMP_REGISTER
                    )
                    if ambient_raw & 0x8000:
                        raise ValueError("MLX90614 ambient read flagged invalid")
                    if raw & 0x8000:
                        raise ValueError("MLX90614 temperature read flagged invalid")

                    latest["mlx_temp_c"] = round(
                        (ambient_raw & 0x7FFF) * 0.02 - 273.15, 2
                    )
                    latest["sky_temperature_c"] = round(
                        (raw & 0x7FFF) * 0.02 - 273.15, 2
                    )
                    latest["status"]["mlx90614_1"] = "available"

                except Exception as exc:
                    latest["mlx_temp_c"] = None
                    latest["sky_temperature_c"] = None
                    latest["status"]["mlx90614_1"] = "error"
                    print(f"MLX90614 read error: {exc}", flush=True)

            if READ_LIGHT:
                if light is not None:
                    try:
                        latest["is_dark"] = bool(light.value)
                        latest["status"]["lm393_light"] = "available"

                    except Exception as exc:
                        latest["is_dark"] = None
                        latest["status"]["lm393_light"] = "error"
                        print(f"LM393 light read error: {exc}", flush=True)

            if (
                latest["status"]["bme280"] == "available"
                and latest["status"]["mlx90614_1"] == "available"
                and latest["bme_temp_c"] is not None
                and latest["mlx_temp_c"] is not None
            ):
                temperature_history.append(
                    (latest["bme_temp_c"] + latest["mlx_temp_c"]) / 2
                )

            latest["temp_c"] = (
                round(sum(temperature_history) / len(temperature_history), 2)
                if temperature_history else None
            )

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

            snapshot = {**latest, "status": latest["status"].copy()}
            try:
                await asyncio.to_thread(save_observation, snapshot)
            except Exception as exc:
                print(f"SQLite observation write error: {exc}", flush=True)

            await asyncio.sleep(SAMPLE_INTERVAL)

    finally:
        if bus is not None:
            bus.close()

        if light is not None:
            light.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    initialize_database()
    task = asyncio.create_task(collect_weather())
    nano_task = asyncio.create_task(collect_nano(latest, SENSORS))

    try:
        yield
    finally:
        task.cancel()
        nano_task.cancel()

        with suppress(asyncio.CancelledError):
            await task
        with suppress(asyncio.CancelledError):
            await nano_task


app = FastAPI(
    title="TriStar Weather Station 2.0",
    lifespan=lifespan
)

app.add_api_route(
    "/api/weather/current",
    lambda: latest,
    methods=["GET"]
)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
