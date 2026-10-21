"""State machine of one ventilation zone.

A *zone* is one connected air volume, e.g. a living room with an open
kitchen. It can contain several windows, humidity and temperature sensors.

The engine is pure Python: it is fed snapshots of the sensor states and
returns events. Home Assistant glue lives in ``zone.py``. This keeps the
logic easy to test and to replay.

Lifecycle of a session::

    idle ──window open ≥ open_delay──▶ airing ──reason──▶ close_now
      ▲                                   │                   │
      │                                   └──all closed───────┤
      │                                                       ▼
      └────────── learn τ, "finished" event ◀──────────── settling
                                                (until first humidity report
                                                 after closing, 2–15 min)
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import math
from typing import Any

from .model import (
    ModelParams,
    Plan,
    blend_time_constant,
    dew_point,
    expected_humidity,
    measured_time_constant,
    plan_airing,
)

STATUS_IDLE = "idle"
STATUS_AIRING = "airing"
STATUS_CLOSE_NOW = "close_now"
STATUS_SETTLING = "settling"

REASON_EXCHANGED = "air_exchanged"
REASON_COOLING = "room_cooling"
REASON_STALLED = "humidity_stalled"
REASON_TIME = "time_reached"

HINT_HUMID_OUTSIDE = "humid_outside"
HINT_SLOW_EXCHANGE = "slow_exchange"

EVENT_STARTED = "started"
EVENT_CLOSE_NOW = "close_now"
EVENT_REMINDER = "reminder"
EVENT_FINISHED = "finished"
EVENT_DISCARDED = "discarded"

MIN_LEARNING_RUNS = 3


@dataclass(frozen=True)
class EngineParams:
    """Behaviour of a zone (configurable in the options flow)."""

    model: ModelParams = field(default_factory=ModelParams)
    open_delay: float = 30.0  # s a window must be open before a session starts
    reason_grace: float = 3.0  # min before early-end reasons are evaluated
    exchanged_dew_point_delta: float = 1.0  # K indoor above outdoor dew point
    cooling_drop: float = 3.0  # K below start temperature
    cooling_floor: float = 16.0  # °C
    stalled_after: float = 10.0  # min
    reminder_after: float = 10.0  # min after "close now"
    settle_min: float = 2.0  # min after closing
    settle_max: float = 15.0  # min after closing
    learn_min_duration: float = 8.0  # min
    spike_delta: float = 10.0  # points above the driest sensor; 0 = off
    cooldown: float = 60.0  # min after airing without "worthwhile"


@dataclass(frozen=True)
class Reading:
    """A sensor value and when it last changed (unix seconds)."""

    value: float
    changed: float


@dataclass(frozen=True)
class WindowState:
    """Open state of a contact and when it last changed (unix seconds)."""

    is_open: bool
    changed: float


@dataclass(frozen=True)
class Snapshot:
    """Everything the engine needs to know at one point in time."""

    now: float
    windows: dict[str, WindowState | None]
    humidity: dict[str, Reading | None]
    temperature: dict[str, Reading | None]
    outdoor_dew_point: float | None
    outdoor_temperature: float | None


@dataclass
class TimeConstant:
    """Learned exchange time constant."""

    tau: float
    runs: int = 0


@dataclass
class Session:
    """One airing, from opening until learning is done."""

    started: float
    h0: float
    t0: float
    equilibrium: float
    gap: float
    tau: float
    runs: int
    duration: float
    expected: float
    min_humidity: float
    last_tick: float
    new_reading: bool = False
    closed: float | None = None
    reason: str | None = None
    reason_at: float | None = None
    reminded: bool = False
    open_seconds: dict[str, float] = field(default_factory=dict)

    @property
    def end(self) -> float:
        return self.started + self.duration * 60


@dataclass(frozen=True)
class Event:
    """Something happened that automations may react to."""

    type: str
    data: dict[str, Any]


def profile_key(windows: list[str] | set[str]) -> str:
    """Stable key for a set of windows."""
    return "+".join(sorted(windows))


@dataclass(frozen=True)
class Aggregate:
    """Zone-wide sensor values."""

    humidity: float | None
    humidity_changed: float | None
    humidity_sources: tuple[str, ...]
    temperature: float | None


def aggregate(snapshot: Snapshot, spike_delta: float) -> Aggregate:
    """Average the zone sensors, ignoring humidity spikes (e.g. cooking)."""
    readings = {k: r for k, r in snapshot.humidity.items() if r is not None}
    if readings and spike_delta > 0 and len(readings) >= 2:
        floor = min(r.value for r in readings.values())
        readings = {k: r for k, r in readings.items() if r.value <= floor + spike_delta}
    temps = [r.value for r in snapshot.temperature.values() if r is not None]
    return Aggregate(
        humidity=sum(r.value for r in readings.values()) / len(readings) if readings else None,
        humidity_changed=max(r.changed for r in readings.values()) if readings else None,
        humidity_sources=tuple(sorted(readings)),
        temperature=sum(temps) / len(temps) if temps else None,
    )


class ZoneEngine:
    """Airing logic and learning for one zone."""

    def __init__(
        self,
        name: str,
        windows: list[str],
        params: EngineParams,
        initial_tau: float = 60.0,
        initial_runs: int = 0,
    ) -> None:
        self.name = name
        self.windows = list(windows)
        self.params = params
        self.base = TimeConstant(initial_tau, initial_runs)
        self.profiles: dict[str, TimeConstant] = {}
        self.session: Session | None = None
        self.last_closed: float | None = None
        self.last_result: dict[str, Any] | None = None
        # Latest computed values, for entities.
        self.current: Aggregate = Aggregate(None, None, (), None)
        self.live_plan: Plan | None = None
        self.now: float = 0.0
        self.open_windows: list[str] = []
        self._outdoor_dew_point: float | None = None

    # ------------------------------------------------------------------ learning

    def time_constant_for(self, windows: list[str] | set[str]) -> TimeConstant:
        """τ for a window combination, falling back to the zone value."""
        learned = self.profiles.get(profile_key(windows))
        if learned is not None and learned.runs >= MIN_LEARNING_RUNS:
            return learned
        return self.base

    def set_time_constant(self, tau: float, runs: int | None = None) -> None:
        """Manually correct the zone time constant."""
        self.base = TimeConstant(tau, self.base.runs if runs is None else runs)

    def reset_learning(self, tau: float) -> None:
        """Forget everything learned."""
        self.base = TimeConstant(tau, 0)
        self.profiles = {}

    # ------------------------------------------------------------------ main loop

    def update(self, snap: Snapshot) -> list[Event]:
        """Advance the state machine to ``snap.now``."""
        p = self.params
        events: list[Event] = []
        self.now = snap.now
        self.current = agg = aggregate(snap, p.spike_delta)
        self._outdoor_dew_point = snap.outdoor_dew_point

        known = {w: s for w, s in snap.windows.items() if s is not None}
        open_windows = {w: s for w, s in known.items() if s.is_open}
        unknown = len(known) < len(snap.windows)
        self.open_windows = sorted(open_windows)

        # Live plan for "what if I aired now" (idle) entities.
        self.live_plan = None
        if agg.humidity is not None and agg.temperature is not None:
            tc = self.time_constant_for(set(open_windows) or set(self.windows))
            self.live_plan = plan_airing(
                agg.humidity,
                agg.temperature,
                snap.outdoor_dew_point,
                snap.outdoor_temperature,
                tc.tau,
                p.model,
            )

        session = self.session

        # Start a new session (also when a window is reopened while settling).
        if open_windows and (session is None or session.closed is not None):
            opened = min(s.changed for s in open_windows.values())
            if snap.now - opened >= p.open_delay and self.live_plan is not None:
                if session is not None:
                    events.append(Event(EVENT_DISCARDED, self._result(session, learned=None)))
                session = self.session = self._start(opened, open_windows, snap)
                events.append(Event(EVENT_STARTED, self._session_data(session)))

        if session is None:
            return events

        # Track humidity minimum and whether any sensor reported since opening.
        if agg.humidity is not None:
            session.min_humidity = min(session.min_humidity, agg.humidity)
        if agg.humidity_changed is not None and agg.humidity_changed > session.started:
            session.new_reading = True

        if session.closed is None:
            dt = max(snap.now - session.last_tick, 0.0)
            for window in open_windows:
                session.open_seconds[window] = session.open_seconds.get(window, 0.0) + dt
            session.last_tick = snap.now

            if not open_windows and not unknown:
                session.closed = max(s.changed for s in known.values())
                self.last_closed = session.closed
            else:
                events.extend(self._check_open(session, snap, agg))

        if session.closed is not None:
            settled = (snap.now - session.closed) / 60
            reported = agg.humidity_changed is not None and agg.humidity_changed > session.closed
            if settled >= p.settle_max or (reported and settled >= p.settle_min):
                events.append(self._finish(session))
                self.session = None

        return events

    # ------------------------------------------------------------------ helpers

    def _start(self, opened: float, open_windows: dict[str, WindowState], snap: Snapshot) -> Session:
        tc = self.time_constant_for(set(open_windows))
        plan = plan_airing(
            self.current.humidity,
            self.current.temperature,
            snap.outdoor_dew_point,
            snap.outdoor_temperature,
            tc.tau,
            self.params.model,
        )
        return Session(
            started=opened,
            h0=plan.humidity,
            t0=self.current.temperature,
            equilibrium=plan.equilibrium,
            gap=plan.gap,
            tau=tc.tau,
            runs=tc.runs,
            duration=plan.duration,
            expected=plan.expected,
            min_humidity=plan.humidity,
            last_tick=opened,
        )

    def _check_open(self, session: Session, snap: Snapshot, agg: Aggregate) -> list[Event]:
        p = self.params
        events: list[Event] = []
        minutes = (snap.now - session.started) / 60

        if session.reason is None:
            reason = self._reason(session, snap, agg, minutes)
            if reason is not None:
                session.reason = reason
                session.reason_at = snap.now
                events.append(Event(EVENT_CLOSE_NOW, self._session_data(session)))
        elif (
            not session.reminded
            and session.reason_at is not None
            and snap.now - session.reason_at >= p.reminder_after * 60
        ):
            session.reminded = True
            events.append(Event(EVENT_REMINDER, self._session_data(session)))
        return events

    def _reason(self, session: Session, snap: Snapshot, agg: Aggregate, minutes: float) -> str | None:
        p = self.params
        if minutes < p.reason_grace:
            return None
        h = agg.humidity if agg.humidity is not None else session.min_humidity
        t = agg.temperature if agg.temperature is not None else session.t0
        slight = session.h0 - session.expected < p.model.slight_drop

        if (
            not slight
            and snap.outdoor_dew_point is not None
            and dew_point(t, h) - snap.outdoor_dew_point < p.exchanged_dew_point_delta
        ):
            return REASON_EXCHANGED
        if session.t0 - t >= p.cooling_drop or t < p.cooling_floor:
            return REASON_COOLING
        if minutes >= p.stalled_after and agg.humidity_changed is not None:
            quiet = (snap.now - agg.humidity_changed) / 60
            window = p.stalled_after
            expected_drop = session.gap * (
                math.exp(-max(minutes - window, 0.0) / session.tau) - math.exp(-minutes / session.tau)
            )
            if quiet >= window and expected_drop >= 2:
                return REASON_STALLED
        if minutes >= session.duration:
            return REASON_TIME
        return None

    def _finish(self, session: Session) -> Event:
        p = self.params
        minutes = (session.closed - session.started) / 60
        drop = session.h0 - session.min_humidity
        expected_drop = session.gap * (1 - math.exp(-minutes / session.tau))
        valid = (
            session.new_reading
            and minutes >= p.learn_min_duration
            and session.gap >= p.model.min_gap
            and (drop >= 1 or expected_drop >= 2)
        )
        learned: dict[str, Any] | None = None
        if valid:
            measured = measured_time_constant(minutes, drop, session.gap, session.tau)
            key = profile_key(self._profile_windows(session))
            prior = self.profiles.get(key, TimeConstant(self.base.tau, 0))
            self.profiles[key] = TimeConstant(
                round(blend_time_constant(prior.tau, measured, prior.runs), 1), prior.runs + 1
            )
            self.base = TimeConstant(
                round(blend_time_constant(self.base.tau, measured, self.base.runs), 1),
                self.base.runs + 1,
            )
            learned = {
                "measured": round(measured, 1),
                "profile": key,
                "profile_tau": self.profiles[key].tau,
                "zone_tau": self.base.tau,
                "runs": self.base.runs,
            }
        result = self._result(session, learned)
        self.last_result = result
        return Event(EVENT_FINISHED, result)

    def _profile_windows(self, session: Session) -> list[str]:
        """Windows open for at least half of the session."""
        if not session.open_seconds:
            return list(self.windows)
        longest = max(session.open_seconds.values())
        return [w for w, s in session.open_seconds.items() if s >= 0.5 * longest]

    def _session_data(self, session: Session) -> dict[str, Any]:
        h = self.current.humidity
        return {
            "zone": self.name,
            "status": self.status,
            "reason": session.reason,
            "humidity_start": round(session.h0, 1),
            "humidity": round(h, 1) if h is not None else None,
            "humidity_expected": round(session.expected, 1),
            "duration": round(session.duration, 1),
            "learning": session.runs < MIN_LEARNING_RUNS,
            "hint": self.hint,
        }

    def _result(self, session: Session, learned: dict[str, Any] | None) -> dict[str, Any]:
        closed = session.closed if session.closed is not None else self.now
        minutes = (closed - session.started) / 60
        return {
            "zone": self.name,
            "minutes": round(minutes),
            "humidity_start": round(session.h0, 1),
            "humidity_min": round(session.min_humidity, 1),
            "humidity_model": round(expected_humidity(session.h0, session.gap, session.tau, minutes), 1),
            "windows": self._profile_windows(session),
            "learned": learned is not None,
            "learning": learned,
        }

    # ------------------------------------------------------------------ views

    @property
    def status(self) -> str:
        s = self.session
        if s is None:
            return STATUS_IDLE
        if s.closed is not None:
            return STATUS_SETTLING
        return STATUS_CLOSE_NOW if s.reason else STATUS_AIRING

    @property
    def hint(self) -> str | None:
        """Why airing will hardly help, if it won't."""
        s = self.session
        if s is None or s.h0 - s.expected >= self.params.model.slight_drop:
            return None
        return HINT_HUMID_OUTSIDE if s.gap < self.params.model.min_gap else HINT_SLOW_EXCHANGE

    @property
    def progress(self) -> int | None:
        s = self.session
        if s is None or s.closed is not None:
            return None
        if s.reason:
            return 100
        return min(100, round((self.now - s.started) / 60 / s.duration * 100))

    @property
    def worthwhile(self) -> bool:
        """Airing now would noticeably lower humidity."""
        if self.session is not None or self.live_plan is None:
            return False
        if self.last_closed is not None and self.now - self.last_closed < self.params.cooldown * 60:
            return False
        return self.live_plan.gap >= self.params.model.min_gap

    # ------------------------------------------------------------------ storage

    def to_dict(self) -> dict[str, Any]:
        return {
            "base": asdict(self.base),
            "profiles": {k: asdict(v) for k, v in self.profiles.items()},
            "session": asdict(self.session) if self.session else None,
            "last_closed": self.last_closed,
            "last_result": self.last_result,
        }

    def load(self, data: dict[str, Any]) -> None:
        if base := data.get("base"):
            self.base = TimeConstant(**base)
        self.profiles = {k: TimeConstant(**v) for k, v in data.get("profiles", {}).items()}
        self.session = Session(**data["session"]) if data.get("session") else None
        self.last_closed = data.get("last_closed")
        self.last_result = data.get("last_result")
