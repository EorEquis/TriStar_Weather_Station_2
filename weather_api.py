
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
from datetime import datetime, timezone
from dotenv import load_dotenv
from fastapi import FastAPI
from gpiozero import DigitalInputDevice

I2C_BUS = 1
BME280_ADDRESS = 0x76
LIGHT_GPIO = 17
SAMPLE_INTERVAL = 5

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
    "temperature_c": None,
    "humidity_pct": None,
    "pressure_hpa": None,
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


async def collect_weather():
    bus = None
    calibration = None
    light = None

    try:
        if READ_BME:
            try:
                bus = smbus2.SMBus(I2C_BUS)
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

                        latest["temperature_c"] = round(
                            reading.temperature, 2
                        )
                        latest["humidity_pct"] = round(
                            reading.humidity, 2
                        )
                        latest["pressure_hpa"] = round(
                            reading.pressure, 2
                        )
                        latest["dew_point_c"] = round(
                            calculate_dew_point(
                                reading.temperature,
                                reading.humidity
                            ), 2
                        )

                        latest["status"]["bme280"] = "available"

                    except Exception as exc:
                        latest["temperature_c"] = None
                        latest["humidity_pct"] = None
                        latest["pressure_hpa"] = None
                        latest["dew_point_c"] = None
                        latest["status"]["bme280"] = "error"
                        print(f"BME280 read error: {exc}", flush=True)

            if READ_LIGHT:
                if light is not None:
                    try:
                        latest["is_dark"] = bool(light.value)
                        latest["status"]["lm393_light"] = "available"

                    except Exception as exc:
                        latest["is_dark"] = None
                        latest["status"]["lm393_light"] = "error"
                        print(f"LM393 light read error: {exc}", flush=True)

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

            await asyncio.sleep(SAMPLE_INTERVAL)

    finally:
        if bus is not None:
            bus.close()

        if light is not None:
            light.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(collect_weather())

    try:
        yield
    finally:
        task.cancel()

        with suppress(asyncio.CancelledError):
            await task


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
