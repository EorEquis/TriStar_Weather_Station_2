from gpiozero import DigitalInputDevice
from time import sleep

sensor = DigitalInputDevice(17, pull_up=False)

try:
    while True:
        print(f"Digital output: {sensor.value}")
        sleep(0.5)
except KeyboardInterrupt:
    pass