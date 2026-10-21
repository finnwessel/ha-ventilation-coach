"""Services to correct or reset what a zone has learned."""

from __future__ import annotations

from typing import TYPE_CHECKING

import voluptuous as vol

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv

from .const import (
    ATTR_CONFIG_ENTRY_ID,
    ATTR_RUNS,
    ATTR_TIME_CONSTANT,
    DOMAIN,
    SERVICE_RESET_LEARNING,
    SERVICE_SET_TIME_CONSTANT,
)

if TYPE_CHECKING:
    from .zone import ZoneController

SET_TIME_CONSTANT_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_CONFIG_ENTRY_ID): cv.string,
        vol.Required(ATTR_TIME_CONSTANT): vol.All(vol.Coerce(float), vol.Range(min=5, max=300)),
        vol.Optional(ATTR_RUNS): vol.All(vol.Coerce(int), vol.Range(min=0, max=10000)),
    }
)

RESET_LEARNING_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_CONFIG_ENTRY_ID): cv.string,
        vol.Optional(ATTR_TIME_CONSTANT, default=60): vol.All(
            vol.Coerce(float), vol.Range(min=5, max=300)
        ),
    }
)


def _controller(hass: HomeAssistant, entry_id: str) -> ZoneController:
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != DOMAIN:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="zone_not_found"
        )
    if entry.state is not ConfigEntryState.LOADED:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="zone_not_loaded"
        )
    return entry.runtime_data


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register integration services."""

    async def set_time_constant(call: ServiceCall) -> None:
        controller = _controller(hass, call.data[ATTR_CONFIG_ENTRY_ID])
        controller.engine.set_time_constant(call.data[ATTR_TIME_CONSTANT], call.data.get(ATTR_RUNS))
        controller.async_save_now()
        controller.async_update()

    async def reset_learning(call: ServiceCall) -> None:
        controller = _controller(hass, call.data[ATTR_CONFIG_ENTRY_ID])
        controller.engine.reset_learning(call.data[ATTR_TIME_CONSTANT])
        controller.async_save_now()
        controller.async_update()

    hass.services.async_register(
        DOMAIN, SERVICE_SET_TIME_CONSTANT, set_time_constant, schema=SET_TIME_CONSTANT_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_RESET_LEARNING, reset_learning, schema=RESET_LEARNING_SCHEMA
    )
