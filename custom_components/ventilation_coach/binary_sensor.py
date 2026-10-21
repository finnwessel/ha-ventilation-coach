"""Binary sensors of a ventilation zone."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from homeassistant.components.binary_sensor import BinarySensorEntity, BinarySensorEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .engine import STATUS_CLOSE_NOW, ZoneEngine
from .entity import ZoneEntity

if TYPE_CHECKING:
    from . import VentilationCoachConfigEntry

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class ZoneBinarySensorDescription(BinarySensorEntityDescription):
    """Describes a zone binary sensor."""

    is_on_fn: Callable[[ZoneEngine], bool]
    attrs_fn: Callable[[ZoneEngine], dict[str, Any]] | None = None


BINARY_SENSORS: tuple[ZoneBinarySensorDescription, ...] = (
    ZoneBinarySensorDescription(
        key="close_now",
        translation_key="close_now",
        is_on_fn=lambda e: e.status == STATUS_CLOSE_NOW,
        attrs_fn=lambda e: {"reason": e.session.reason if e.session else None},
    ),
    ZoneBinarySensorDescription(
        key="airing_worthwhile",
        translation_key="airing_worthwhile",
        is_on_fn=lambda e: e.worthwhile,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: VentilationCoachConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    controller = entry.runtime_data
    async_add_entities(ZoneBinarySensor(controller, d) for d in BINARY_SENSORS)


class ZoneBinarySensor(ZoneEntity, BinarySensorEntity):
    """Binary sensor of a zone."""

    entity_description: ZoneBinarySensorDescription

    @property
    def is_on(self) -> bool:
        return self.entity_description.is_on_fn(self.engine)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.entity_description.attrs_fn is None:
            return None
        return self.entity_description.attrs_fn(self.engine)
