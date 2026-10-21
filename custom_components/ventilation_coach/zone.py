"""Home Assistant glue for one zone: reads states, drives the engine."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import CALLBACK_TYPE, Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

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
    EVENT_PREFIX,
    SAVE_DELAY_SECONDS,
    STORAGE_VERSION,
    UPDATE_INTERVAL_SECONDS,
)
from .engine import EngineParams, Reading, Snapshot, WindowState, ZoneEngine
from .model import ModelParams

_LOGGER = logging.getLogger(__name__)


def _option(entry: ConfigEntry, key: str) -> Any:
    return entry.options.get(key, DEFAULTS.get(key))


def engine_params(entry: ConfigEntry) -> EngineParams:
    """Build engine parameters from the entry options."""
    return EngineParams(
        model=ModelParams(
            marginal_rate=float(_option(entry, CONF_MARGINAL_RATE)),
            min_gap_fraction=float(_option(entry, CONF_MIN_GAP_FRACTION)),
            min_gap=float(_option(entry, CONF_MIN_GAP)),
        ),
        open_delay=float(_option(entry, CONF_OPEN_DELAY)),
        spike_delta=float(_option(entry, CONF_SPIKE_DELTA)),
        reminder_after=float(_option(entry, CONF_REMINDER_AFTER)),
        settle_max=float(_option(entry, CONF_SETTLE_MAX)),
        cooldown=float(_option(entry, CONF_COOLDOWN)),
    )


class ZoneController:
    """Owns the engine of one zone and keeps it in sync with Home Assistant."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.name: str = entry.data[CONF_ZONE_NAME]
        self.windows: list[str] = list(entry.options.get(CONF_WINDOWS, []))
        self.humidity_sensors: list[str] = list(entry.options.get(CONF_HUMIDITY, []))
        self.temperature_sensors: list[str] = list(entry.options.get(CONF_TEMPERATURE, []))
        self.weather: str | None = entry.options.get(CONF_WEATHER)
        self.outdoor_dew_point: str | None = entry.options.get(CONF_OUTDOOR_DEW_POINT)
        self.outdoor_temperature: str | None = entry.options.get(CONF_OUTDOOR_TEMPERATURE)

        self.engine = ZoneEngine(
            self.name,
            self.windows,
            engine_params(entry),
            float(entry.data.get(CONF_INITIAL_TAU, DEFAULT_TAU)),
            int(entry.data.get(CONF_INITIAL_RUNS, 0)),
        )
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}"
        )
        self._listeners: list[Callable[[], None]] = []
        self._unsubs: list[CALLBACK_TYPE] = []
        self._pending_start: CALLBACK_TYPE | None = None
        self._saved_status: str | None = None

    # ------------------------------------------------------------------ lifecycle

    async def async_start(self) -> None:
        if (data := await self._store.async_load()) is not None:
            try:
                self.engine.load(data)
            except (KeyError, TypeError, ValueError):
                _LOGGER.warning("Stored state of zone %s is invalid, starting fresh", self.name)

        watched = [
            *self.windows,
            *self.humidity_sensors,
            *self.temperature_sensors,
            *(e for e in (self.weather, self.outdoor_dew_point, self.outdoor_temperature) if e),
        ]
        self._unsubs.append(async_track_state_change_event(self.hass, watched, self._on_state_change))
        self._unsubs.append(
            async_track_time_interval(
                self.hass, self._on_tick, timedelta(seconds=UPDATE_INTERVAL_SECONDS)
            )
        )
        self.async_update()

    async def async_stop(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        if self._pending_start is not None:
            self._pending_start()
            self._pending_start = None
        await self._store.async_save(self.engine.to_dict())

    # ------------------------------------------------------------------ listeners

    @callback
    def async_add_listener(self, update: Callable[[], None]) -> CALLBACK_TYPE:
        self._listeners.append(update)

        @callback
        def remove() -> None:
            self._listeners.remove(update)

        return remove

    @callback
    def _on_state_change(self, event: Event[EventStateChangedData]) -> None:
        self.async_update()
        new = event.data["new_state"]
        if event.data["entity_id"] in self.windows and new is not None and new.state == STATE_ON:
            # Start the session exactly when the open delay has passed.
            if self._pending_start is not None:
                self._pending_start()
            self._pending_start = async_call_later(
                self.hass, self.engine.params.open_delay + 0.5, self._on_delay_passed
            )

    @callback
    def _on_delay_passed(self, _now: datetime) -> None:
        self._pending_start = None
        self.async_update()

    @callback
    def _on_tick(self, _now: datetime | None = None) -> None:
        self.async_update()

    # ------------------------------------------------------------------ update

    @callback
    def async_update(self) -> None:
        events = self.engine.update(self._snapshot())
        for event in events:
            self.hass.bus.async_fire(
                f"{EVENT_PREFIX}_{event.type}",
                {"config_entry_id": self.entry.entry_id, **event.data},
            )
        # Persist on transitions only (SD cards on a Raspberry Pi wear out).
        if events or self.engine.status != self._saved_status:
            self._saved_status = self.engine.status
            self._store.async_delay_save(self.engine.to_dict, SAVE_DELAY_SECONDS)
        for update in list(self._listeners):
            update()

    @callback
    def async_save_now(self) -> None:
        self._store.async_delay_save(self.engine.to_dict, 0)

    def _snapshot(self) -> Snapshot:
        return Snapshot(
            now=dt_util.utcnow().timestamp(),
            windows={w: self._window(w) for w in self.windows},
            humidity={s: self._reading(s) for s in self.humidity_sensors},
            temperature={s: self._reading(s) for s in self.temperature_sensors},
            outdoor_dew_point=self._outdoor(self.outdoor_dew_point, "dew_point"),
            outdoor_temperature=self._outdoor(self.outdoor_temperature, "temperature"),
        )

    def _window(self, entity_id: str) -> WindowState | None:
        state = self.hass.states.get(entity_id)
        if state is None or state.state not in (STATE_ON, STATE_OFF):
            return None
        return WindowState(state.state == STATE_ON, state.last_changed.timestamp())

    def _reading(self, entity_id: str) -> Reading | None:
        state = self.hass.states.get(entity_id)
        if state is None:
            return None
        try:
            return Reading(float(state.state), state.last_changed.timestamp())
        except ValueError:
            return None

    def _outdoor(self, sensor: str | None, weather_attribute: str) -> float | None:
        if sensor and (reading := self._reading(sensor)) is not None:
            return reading.value
        if self.weather and (state := self.hass.states.get(self.weather)) is not None:
            value = state.attributes.get(weather_attribute)
            if isinstance(value, (int, float)):
                return float(value)
        return None
