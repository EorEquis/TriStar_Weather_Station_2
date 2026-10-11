
import asyncio
import math
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timezone

import bme280
import smbus2
from fastapi import FastAPI
from gpiozero import DigitalInputDevice
import uvicorn


I2C_BUS = 1
BME280_ADDRESS = 0x76
LIGHT_GPIO = 17
SAMPLE_INTERVAL = 5

latest = {
    "timestamp": None,
    "temperature_c": None,
    "humidity_pct": None,
    "pressure_hpa": None,
    "dew_point_c": None,
    "is_dark": None,
    "status": "initializing",
}


def calculate_dew_point(temp_c, humidity):
    if humidity <= 0:
        return None

    a = 17.625
    b = 243.04
    gamma = math.log(humidity / 100) + a * temp_c / (b + temp_c)
    return b * gamma / (a - gamma)


async def collect_weather():
    with smbus2.SMBus(I2C_BUS) as bus, DigitalInputDevice(
        LIGHT_GPIO, pull_up=False
    ) as light:
        calibration = bme280.load_calibration_params(
            bus, BME280_ADDRESS
        )

        while True:
            try:
                reading = bme280.sample(
                    bus, BME280_ADDRESS, calibration
                )

                latest.update({
                    "timestamp": datetime.now(
                        timezone.utc
                    ).isoformat(),
                    "temperature_c": round(reading.temperature, 2),
                    "humidity_pct": round(reading.humidity, 2),
                    "pressure_hpa": round(reading.pressure, 2),
                    "dew_point_c": round(
                        calculate_dew_point(
                            reading.temperature,
                            reading.humidity
                        ), 2
                    ),
                    "is_dark": bool(light.value),
                    "status": "ok",
                })

            except Exception as exc:
                latest["status"] = "error"
                print(f"Sensor error: {exc}", flush=True)

            await asyncio.sleep(SAMPLE_INTERVAL)


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(collect_weather())
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


app = FastAPI(title="TriStar Weather Station 2.0", lifespan=lifespan)


@app.get("/api/weather/current")
def current_weather():
    return latest


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
