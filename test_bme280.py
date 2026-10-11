import smbus2
import bme280

BUS = 1
ADDRESS = 0x76

with smbus2.SMBus(BUS) as bus:
    chip_id = bus.read_byte_data(ADDRESS, 0xD0)
    print(f"Chip ID: 0x{chip_id:02X} (expected 0x60)")

    if chip_id != 0x60:
        raise RuntimeError("Unexpected chip ID")

    calibration = bme280.load_calibration_params(bus, ADDRESS)
    reading = bme280.sample(bus, ADDRESS, calibration)

    print(f"Temperature: {reading.temperature:.2f} °C")
    print(f"Humidity:    {reading.humidity:.2f} %")
    print(f"Pressure:    {reading.pressure:.2f} hPa")