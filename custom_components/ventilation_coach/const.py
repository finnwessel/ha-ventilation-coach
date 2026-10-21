"""Constants for Ventilation Coach."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "ventilation_coach"

# Config entry data (fixed at creation)
CONF_ZONE_NAME: Final = "name"
CONF_INITIAL_TAU: Final = "initial_time_constant"
CONF_INITIAL_RUNS: Final = "initial_runs"

# Options (editable)
CONF_WINDOWS: Final = "windows"
CONF_HUMIDITY: Final = "humidity_sensors"
CONF_TEMPERATURE: Final = "temperature_sensors"
CONF_WEATHER: Final = "weather"
CONF_OUTDOOR_DEW_POINT: Final = "outdoor_dew_point_sensor"
CONF_OUTDOOR_TEMPERATURE: Final = "outdoor_temperature_sensor"

CONF_OPEN_DELAY: Final = "open_delay"
CONF_MIN_GAP: Final = "min_gap"
CONF_MARGINAL_RATE: Final = "marginal_rate"
CONF_MIN_GAP_FRACTION: Final = "min_gap_fraction"
CONF_SPIKE_DELTA: Final = "spike_delta"
CONF_REMINDER_AFTER: Final = "reminder_after"
CONF_SETTLE_MAX: Final = "settle_max"
CONF_COOLDOWN: Final = "cooldown"

DEFAULTS: Final = {
    CONF_OPEN_DELAY: 30,
    CONF_MIN_GAP: 8.0,
    CONF_MARGINAL_RATE: 0.3,
    CONF_MIN_GAP_FRACTION: 0.2,
    CONF_SPIKE_DELTA: 10.0,
    CONF_REMINDER_AFTER: 10,
    CONF_SETTLE_MAX: 15,
    CONF_COOLDOWN: 60,
}

DEFAULT_TAU: Final = 60.0

UPDATE_INTERVAL_SECONDS: Final = 15
STORAGE_VERSION: Final = 1
SAVE_DELAY_SECONDS: Final = 10

SERVICE_SET_TIME_CONSTANT: Final = "set_time_constant"
SERVICE_RESET_LEARNING: Final = "reset_learning"
ATTR_CONFIG_ENTRY_ID: Final = "config_entry_id"
ATTR_TIME_CONSTANT: Final = "time_constant"
ATTR_RUNS: Final = "runs"

# Bus events: ventilation_coach_started / _close_now / _reminder / _finished / _discarded
EVENT_PREFIX: Final = DOMAIN
