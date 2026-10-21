"""Base entity for a ventilation zone."""

from __future__ import annotations

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity import Entity, EntityDescription

from .const import DOMAIN
from .zone import ZoneController


class ZoneEntity(Entity):
    """Entity that belongs to one zone device and follows its updates."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, controller: ZoneController, description: EntityDescription) -> None:
        self.controller = controller
        self.engine = controller.engine
        self.entity_description = description
        entry_id = controller.entry.entry_id
        self._attr_unique_id = f"{entry_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            name=controller.name,
            manufacturer="Ventilation Coach",
            model="Ventilation zone",
            entry_type=DeviceEntryType.SERVICE,
        )

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.controller.async_add_listener(self._handle_update))

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()
