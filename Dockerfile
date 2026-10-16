
FROM python:3.13-slim-bookworm

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir \
    https://github.com/adafruit/lgpio-python-wheels/raw/main/wheels/lgpio-0.2.2.0-cp313-cp313-linux_aarch64.whl \
    && pip install --no-cache-dir -r requirements.txt

COPY weather_api.py nano_poll.py ./

CMD ["python", "weather_api.py"]
