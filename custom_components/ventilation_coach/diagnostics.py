"""Diagnostics: everything the zone has learned and its current session."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant

if TYPE_CHECKING:
    from . import VentilationCoachConfigEntry


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: VentilationCoachConfigEntry
) -> dict[str, Any]:
    engine = entry.runtime_data.engine
    return {
        "data": dict(entry.data),
        "options": dict(entry.options),
        "status": engine.status,
        "current": {
            "humidity": engine.current.humidity,
            "humidity_sources": engine.current.humidity_sources,
            "temperature": engine.current.temperature,
            "open_windows": engine.open_windows,
        },
        "live_plan": vars(engine.live_plan) if engine.live_plan else None,
        "state": engine.to_dict(),
    }
