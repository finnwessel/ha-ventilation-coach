"""Home Assistant tests: config flow, setup, a full airing session."""

from __future__ import annotations

from datetime import timedelta

from freezegun.api import FrozenDateTimeFactory
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
    async_fire_time_changed,
)

from custom_components.ventilation_coach.const import (
    CONF_HUMIDITY,
    CONF_INITIAL_RUNS,
    CONF_INITIAL_TAU,
    CONF_TEMPERATURE,
    CONF_WEATHER,
    CONF_WINDOWS,
    CONF_ZONE_NAME,
    DEFAULTS,
    DOMAIN,
)
from custom_components.ventilation_coach.model import dew_point

WINDOW = "binary_sensor.window_living"
KITCHEN_WINDOW = "binary_sensor.window_kitchen"
HUMIDITY = "sensor.living_humidity"
KITCHEN_HUMIDITY = "sensor.kitchen_humidity"
TEMPERATURE = "sensor.living_temperature"
WEATHER = "weather.home"


def _set_states(hass: HomeAssistant, humidity: float = 70.0) -> None:
    hass.states.async_set(WINDOW, "off", {"device_class": "window"})
    hass.states.async_set(KITCHEN_WINDOW, "off", {"device_class": "window"})
    hass.states.async_set(HUMIDITY, str(humidity), {"device_class": "humidity"})
    hass.states.async_set(KITCHEN_HUMIDITY, str(humidity + 2), {"device_class": "humidity"})
    hass.states.async_set(TEMPERATURE, "21.0", {"device_class": "temperature"})
    hass.states.async_set(
        WEATHER, "cloudy", {"temperature": 8.0, "dew_point": round(dew_point(21.0, 50.0), 2)}
    )


def _entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="Living area",
        data={CONF_ZONE_NAME: "Living area", CONF_INITIAL_TAU: 60.0, CONF_INITIAL_RUNS: 5},
        options={
            CONF_WINDOWS: [WINDOW, KITCHEN_WINDOW],
            CONF_HUMIDITY: [HUMIDITY, KITCHEN_HUMIDITY],
            CONF_TEMPERATURE: [TEMPERATURE],
            CONF_WEATHER: WEATHER,
            **DEFAULTS,
        },
    )


def _entity_id(hass: HomeAssistant, entry: MockConfigEntry, platform: str, key: str) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(platform, DOMAIN, f"{entry.entry_id}_{key}")
    assert entity_id is not None
    return entity_id


async def test_user_flow_creates_zone(hass: HomeAssistant) -> None:
    _set_states(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_ZONE_NAME: "Living area",
            CONF_WINDOWS: [WINDOW, KITCHEN_WINDOW],
            CONF_HUMIDITY: [HUMIDITY, KITCHEN_HUMIDITY],
            CONF_TEMPERATURE: [TEMPERATURE],
            CONF_WEATHER: WEATHER,
            CONF_INITIAL_TAU: 107,
            CONF_INITIAL_RUNS: 2,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_INITIAL_TAU] == 107.0
    assert result["options"][CONF_WINDOWS] == [WINDOW, KITCHEN_WINDOW]
    await hass.async_block_till_done()


async def test_user_flow_requires_outdoor_source(hass: HomeAssistant) -> None:
    _set_states(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_ZONE_NAME: "Living area",
            CONF_WINDOWS: [WINDOW],
            CONF_HUMIDITY: [HUMIDITY],
            CONF_TEMPERATURE: [TEMPERATURE],
            CONF_INITIAL_TAU: 60,
            CONF_INITIAL_RUNS: 0,
        },
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "no_outdoor"}


async def test_airing_session(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    _set_states(hass)
    entry = _entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    status = _entity_id(hass, entry, "sensor", "status")
    humidity = _entity_id(hass, entry, "sensor", "humidity")
    gap = _entity_id(hass, entry, "sensor", "humidity_gap")
    worthwhile = _entity_id(hass, entry, "binary_sensor", "airing_worthwhile")
    close_now = _entity_id(hass, entry, "binary_sensor", "close_now")

    assert hass.states.get(status).state == "idle"
    assert float(hass.states.get(humidity).state) == 71.0  # mean of 70 and 72
    assert float(hass.states.get(gap).state) > 8
    assert hass.states.get(worthwhile).state == "on"

    started = async_capture_events(hass, f"{DOMAIN}_started")
    closing = async_capture_events(hass, f"{DOMAIN}_close_now")
    finished = async_capture_events(hass, f"{DOMAIN}_finished")

    hass.states.async_set(WINDOW, "on", {"device_class": "window"})
    await hass.async_block_till_done()
    freezer.tick(timedelta(seconds=31))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get(status).state == "airing"
    assert len(started) == 1

    # Humidity drops while airing, then the planned time passes.
    for minute in range(1, 21):
        freezer.tick(timedelta(minutes=1))
        if minute % 3 == 0:
            value = 70 - minute / 3
            hass.states.async_set(HUMIDITY, str(value), {"device_class": "humidity"})
            hass.states.async_set(KITCHEN_HUMIDITY, str(value + 2), {"device_class": "humidity"})
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
    assert hass.states.get(status).state == "close_now"
    assert hass.states.get(close_now).state == "on"
    assert len(closing) == 1

    hass.states.async_set(WINDOW, "off", {"device_class": "window"})
    await hass.async_block_till_done()
    assert hass.states.get(status).state == "settling"

    freezer.tick(timedelta(minutes=3))
    hass.states.async_set(HUMIDITY, "62.5", {"device_class": "humidity"})
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get(status).state == "idle"
    assert len(finished) == 1
    assert finished[0].data["learned"]

    # State survives reload.
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    tau = _entity_id(hass, entry, "sensor", "time_constant")
    assert hass.states.get(tau).attributes["runs"] == 6


async def test_set_time_constant_service(hass: HomeAssistant) -> None:
    _set_states(hass)
    entry = _entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    await hass.services.async_call(
        DOMAIN,
        "set_time_constant",
        {"config_entry_id": entry.entry_id, "time_constant": 90, "runs": 3},
        blocking=True,
    )
    tau = hass.states.get(_entity_id(hass, entry, "sensor", "time_constant"))
    assert float(tau.state) == 90
    assert tau.attributes["runs"] == 3
