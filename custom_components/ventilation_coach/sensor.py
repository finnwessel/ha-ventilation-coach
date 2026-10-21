"""Sensors of a ventilation zone."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType
from homeassistant.util import dt as dt_util

from .engine import (
    MIN_LEARNING_RUNS,
    STATUS_AIRING,
    STATUS_CLOSE_NOW,
    STATUS_IDLE,
    STATUS_SETTLING,
    ZoneEngine,
)
from .entity import ZoneEntity

if TYPE_CHECKING:
    from . import VentilationCoachConfigEntry

PARALLEL_UPDATES = 0


def _round(value: float | None, digits: int = 1) -> float | None:
    return None if value is None else round(value, digits)


def _ts(value: float | None) -> datetime | None:
    return None if value is None else dt_util.utc_from_timestamp(value)


def _status_attrs(engine: ZoneEngine) -> dict[str, Any]:
    s = engine.session
    attrs: dict[str, Any] = {
        "kind": "zone_status",
        "zone": engine.name,
        "open_windows": engine.open_windows,
        "humidity": _round(engine.current.humidity),
        "reason": s.reason if s else None,
        "hint": engine.hint,
        "progress": engine.progress,
        "learning": engine.base.runs < MIN_LEARNING_RUNS,
    }
    if s is not None:
        attrs |= {
            "started": _ts(s.started),
            "ends": _ts(s.end),
            "duration": round(s.duration, 1),
            "humidity_start": round(s.h0, 1),
            "humidity_expected": round(s.expected, 1),
            "humidity_min": round(s.min_humidity, 1),
            "learning": s.runs < MIN_LEARNING_RUNS,
        }
    return attrs


def _session_or_plan(engine: ZoneEngine, attr: str) -> float | None:
    if engine.session is not None:
        return getattr(engine.session, attr)
    return getattr(engine.live_plan, attr) if engine.live_plan else None


@dataclass(frozen=True, kw_only=True)
class ZoneSensorDescription(SensorEntityDescription):
    """Describes a zone sensor."""

    value_fn: Callable[[ZoneEngine], StateType | datetime]
    attrs_fn: Callable[[ZoneEngine], dict[str, Any]] | None = None


SENSORS: tuple[ZoneSensorDescription, ...] = (
    ZoneSensorDescription(
        key="status",
        translation_key="status",
        device_class=SensorDeviceClass.ENUM,
        options=[STATUS_IDLE, STATUS_AIRING, STATUS_CLOSE_NOW, STATUS_SETTLING],
        value_fn=lambda e: e.status,
        attrs_fn=_status_attrs,
    ),
    ZoneSensorDescription(
        key="humidity",
        translation_key="humidity",
        device_class=SensorDeviceClass.HUMIDITY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        suggested_display_precision=1,
        value_fn=lambda e: _round(e.current.humidity),
        attrs_fn=lambda e: {"sources": list(e.current.humidity_sources)},
    ),
    ZoneSensorDescription(
        key="equilibrium_humidity",
        translation_key="equilibrium_humidity",
        device_class=SensorDeviceClass.HUMIDITY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        suggested_display_precision=1,
        value_fn=lambda e: _round(e.live_plan.equilibrium) if e.live_plan else None,
    ),
    ZoneSensorDescription(
        key="humidity_gap",
        translation_key="humidity_gap",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        suggested_display_precision=1,
        value_fn=lambda e: _round(e.live_plan.gap) if e.live_plan else None,
    ),
    ZoneSensorDescription(
        key="airing_duration",
        translation_key="airing_duration",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        suggested_display_precision=0,
        value_fn=lambda e: _round(_session_or_plan(e, "duration")),
    ),
    ZoneSensorDescription(
        key="expected_humidity",
        translation_key="expected_humidity",
        device_class=SensorDeviceClass.HUMIDITY,
        native_unit_of_measurement=PERCENTAGE,
        suggested_display_precision=0,
        value_fn=lambda e: _round(_session_or_plan(e, "expected")),
    ),
    ZoneSensorDescription(
        key="airing_end",
        translation_key="airing_end",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda e: (
            _ts(e.session.end) if e.session is not None and e.session.closed is None else None
        ),
    ),
    ZoneSensorDescription(
        key="last_airing",
        translation_key="last_airing",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda e: _ts(e.last_closed),
        attrs_fn=lambda e: dict(e.last_result or {}),
    ),
    ZoneSensorDescription(
        key="time_constant",
        translation_key="time_constant",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        suggested_display_precision=0,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda e: e.base.tau,
        attrs_fn=lambda e: {
            "runs": e.base.runs,
            "profiles": {k: {"time_constant": v.tau, "runs": v.runs} for k, v in e.profiles.items()},
        },
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: VentilationCoachConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    controller = entry.runtime_data
    async_add_entities(ZoneSensor(controller, d) for d in SENSORS)


class ZoneSensor(ZoneEntity, SensorEntity):
    """Sensor showing one value of a zone."""

    entity_description: ZoneSensorDescription

    @property
    def native_value(self) -> StateType | datetime:
        return self.entity_description.value_fn(self.engine)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.entity_description.attrs_fn is None:
            return None
        return self.entity_description.attrs_fn(self.engine)
