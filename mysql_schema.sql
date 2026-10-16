-- Reference schema for existing MySQL database (weather_station_v2).
-- Tables already exist; do not apply this file to the running database.
-- IDs come from SQLite: neither MySQL table uses AUTO_INCREMENT.
-- obs_timestamp stores UTC as MySQL DATETIME(6).
CREATE TABLE IF NOT EXISTS observations (
    observation_id BIGINT NOT NULL PRIMARY KEY,
    obs_timestamp DATETIME(6) NOT NULL,
    bme_temp_c DOUBLE NULL,
    temp_c DOUBLE NULL,
    mlx_temp_c DOUBLE NULL,
    humidity_pct DOUBLE NULL,
    pressure_hpa DOUBLE NULL,
    pressure_sealevel_hpa DOUBLE NULL,
    dew_point_c DOUBLE NULL,
    is_dark TINYINT NULL,
    light_value INT NULL,
    is_wet TINYINT NULL,
    wet_value INT NULL,
    sky_temperature_c DOUBLE NULL,
    ground_temperature_c DOUBLE NULL,
    wind_speed_mps DOUBLE NULL,
    wind_dir_deg DOUBLE NULL,
    rain_amount_mm DOUBLE NULL,
    rain_hourly_mm DOUBLE NULL,
    rain_daily_mm DOUBLE NULL,
    wind_gust_10min DOUBLE NULL,
    wind_gust_hour DOUBLE NULL,
    wind_gust_today DOUBLE NULL,
    future_obs_1 DOUBLE NULL,
    future_obs_2 DOUBLE NULL,
    future_obs_3 DOUBLE NULL,
    future_obs_4 DOUBLE NULL,
    future_obs_5 DOUBLE NULL,
    synced TINYINT NOT NULL DEFAULT 0
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS status (
    status_id BIGINT NOT NULL PRIMARY KEY,
    observation_id BIGINT NOT NULL UNIQUE,
    sys VARCHAR(32) NULL,
    bme280 VARCHAR(32) NULL,
    lm393_light VARCHAR(32) NULL,
    lm393_rain VARCHAR(32) NULL,
    mlx90614_1 VARCHAR(32) NULL,
    mlx90614_2 VARCHAR(32) NULL,
    anemometer VARCHAR(32) NULL,
    windvane VARCHAR(32) NULL,
    rainbucket VARCHAR(32) NULL,
    future_device_1 VARCHAR(32) NULL,
    future_device_2 VARCHAR(32) NULL,
    future_device_3 VARCHAR(32) NULL,
    future_device_4 VARCHAR(32) NULL,
    future_device_5 VARCHAR(32) NULL,
    CONSTRAINT fk_status_observation FOREIGN KEY (observation_id)
        REFERENCES observations(observation_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
