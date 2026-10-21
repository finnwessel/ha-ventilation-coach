"""Config and options flow: one entry per ventilation zone."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_COOLDOWN,
    CONF_HUMIDITY,
    CONF_INITIAL_RUNS,
    CONF_INITIAL_TAU,
    CONF_MARGINAL_RATE,
    CONF_MIN_GAP,
    CONF_MIN_GAP_FRACTION,
    CONF_OPEN_DELAY,
    CONF_OUTDOOR_DEW_POINT,
    CONF_OUTDOOR_TEMPERATURE,
    CONF_REMINDER_AFTER,
    CONF_SETTLE_MAX,
    CONF_SPIKE_DELTA,
    CONF_TEMPERATURE,
    CONF_WEATHER,
    CONF_WINDOWS,
    CONF_ZONE_NAME,
    DEFAULT_TAU,
    DEFAULTS,
    DOMAIN,
)

ENTITY_KEYS = (
    CONF_WINDOWS,
    CONF_HUMIDITY,
    CONF_TEMPERATURE,
    CONF_WEATHER,
    CONF_OUTDOOR_DEW_POINT,
    CONF_OUTDOOR_TEMPERATURE,
)


def _number(minimum: float, maximum: float, step: float, unit: str | None = None) -> selector.NumberSelector:
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=minimum,
            max=maximum,
            step=step,
            unit_of_measurement=unit,
            mode=selector.NumberSelectorMode.BOX,
        )
    )


def _sensors_schema(defaults: dict[str, Any]) -> dict[vol.Marker, Any]:
    """Entities of a zone."""

    def opt(key: str) -> vol.Optional:
        if defaults.get(key):
            return vol.Optional(key, description={"suggested_value": defaults[key]})
        return vol.Optional(key)

    return {
        vol.Required(CONF_WINDOWS, default=defaults.get(CONF_WINDOWS, [])): selector.EntitySelector(
            selector.EntitySelectorConfig(
                domain="binary_sensor",
                device_class=[
                    BinarySensorDeviceClass.WINDOW,
                    BinarySensorDeviceClass.OPENING,
                    BinarySensorDeviceClass.DOOR,
                ],
                multiple=True,
            )
        ),
        vol.Required(CONF_HUMIDITY, default=defaults.get(CONF_HUMIDITY, [])): selector.EntitySelector(
            selector.EntitySelectorConfig(
                domain="sensor", device_class=SensorDeviceClass.HUMIDITY, multiple=True
            )
        ),
        vol.Required(
            CONF_TEMPERATURE, default=defaults.get(CONF_TEMPERATURE, [])
        ): selector.EntitySelector(
            selector.EntitySelectorConfig(
                domain="sensor", device_class=SensorDeviceClass.TEMPERATURE, multiple=True
            )
        ),
        opt(CONF_WEATHER): selector.EntitySelector(selector.EntitySelectorConfig(domain="weather")),
        opt(CONF_OUTDOOR_DEW_POINT): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="sensor", device_class=SensorDeviceClass.TEMPERATURE)
        ),
        opt(CONF_OUTDOOR_TEMPERATURE): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="sensor", device_class=SensorDeviceClass.TEMPERATURE)
        ),
    }


def _tuning_schema(defaults: dict[str, Any]) -> dict[vol.Marker, Any]:
    """Model parameters."""

    def req(key: str) -> vol.Required:
        return vol.Required(key, default=defaults.get(key, DEFAULTS[key]))

    return {
        req(CONF_OPEN_DELAY): _number(0, 600, 5, "s"),
        req(CONF_MIN_GAP): _number(1, 30, 0.5, "%"),
        req(CONF_MARGINAL_RATE): _number(0.05, 2, 0.05, "%/min"),
        req(CONF_MIN_GAP_FRACTION): _number(0, 0.9, 0.05),
        req(CONF_SPIKE_DELTA): _number(0, 50, 1, "%"),
        req(CONF_REMINDER_AFTER): _number(1, 120, 1, "min"),
        req(CONF_SETTLE_MAX): _number(2, 60, 1, "min"),
        req(CONF_COOLDOWN): _number(0, 600, 5, "min"),
    }


def _validate(user_input: dict[str, Any]) -> dict[str, str]:
    errors: dict[str, str] = {}
    if not user_input.get(CONF_WINDOWS):
        errors[CONF_WINDOWS] = "no_windows"
    if not user_input.get(CONF_HUMIDITY):
        errors[CONF_HUMIDITY] = "no_humidity"
    if not user_input.get(CONF_TEMPERATURE):
        errors[CONF_TEMPERATURE] = "no_temperature"
    if not (user_input.get(CONF_WEATHER) or user_input.get(CONF_OUTDOOR_DEW_POINT)):
        errors["base"] = "no_outdoor"
    return errors


class VentilationCoachConfigFlow(ConfigFlow, domain=DOMAIN):
    """Create a ventilation zone."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _validate(user_input)
            if not errors:
                options = {k: user_input[k] for k in ENTITY_KEYS if user_input.get(k)}
                options |= DEFAULTS
                return self.async_create_entry(
                    title=user_input[CONF_ZONE_NAME],
                    data={
                        CONF_ZONE_NAME: user_input[CONF_ZONE_NAME],
                        CONF_INITIAL_TAU: float(user_input[CONF_INITIAL_TAU]),
                        CONF_INITIAL_RUNS: int(user_input[CONF_INITIAL_RUNS]),
                    },
                    options=options,
                )

        defaults = user_input or {}
        schema = vol.Schema(
            {
                vol.Required(CONF_ZONE_NAME, default=defaults.get(CONF_ZONE_NAME, "")): str,
                **_sensors_schema(defaults),
                vol.Required(
                    CONF_INITIAL_TAU, default=defaults.get(CONF_INITIAL_TAU, DEFAULT_TAU)
                ): _number(5, 300, 1, "min"),
                vol.Required(
                    CONF_INITIAL_RUNS, default=defaults.get(CONF_INITIAL_RUNS, 0)
                ): _number(0, 100, 1),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry) -> VentilationCoachOptionsFlow:
        return VentilationCoachOptionsFlow()


class VentilationCoachOptionsFlow(OptionsFlow):
    """Change sensors and tuning of a zone."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _validate(user_input)
            if not errors:
                return self.async_create_entry(
                    data={k: v for k, v in user_input.items() if v not in (None, "", [])}
                )

        defaults = user_input or dict(self.config_entry.options)
        schema = vol.Schema({**_sensors_schema(defaults), **_tuning_schema(defaults)})
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)
