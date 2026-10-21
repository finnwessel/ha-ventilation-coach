"""Ventilation Coach: learning airing advice for Home Assistant.

Each config entry is one ventilation *zone* – a connected air volume that can
span several rooms (e.g. living room with open kitchen).
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN
from .services import async_setup_services
from .zone import ZoneController

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BINARY_SENSOR]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type VentilationCoachConfigEntry = ConfigEntry[ZoneController]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the services once."""
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: VentilationCoachConfigEntry) -> bool:
    """Set up one zone."""
    controller = ZoneController(hass, entry)
    await controller.async_start()
    entry.runtime_data = controller

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: VentilationCoachConfigEntry) -> bool:
    """Unload a zone and persist its state."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_stop()
    return unloaded


async def _async_reload(hass: HomeAssistant, entry: VentilationCoachConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
