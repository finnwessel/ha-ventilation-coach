"""Tests for the zone state machine (no Home Assistant needed)."""

from __future__ import annotations

import math

from custom_components.ventilation_coach.engine import (
    EVENT_CLOSE_NOW,
    EVENT_DISCARDED,
    EVENT_FINISHED,
    EVENT_REMINDER,
    EVENT_STARTED,
    REASON_COOLING,
    REASON_TIME,
    STATUS_AIRING,
    STATUS_CLOSE_NOW,
    STATUS_IDLE,
    STATUS_SETTLING,
    EngineParams,
    Reading,
    Snapshot,
    WindowState,
    ZoneEngine,
    aggregate,
)
from custom_components.ventilation_coach.model import dew_point

LIVING = "binary_sensor.window_living_1"
LIVING_2 = "binary_sensor.window_living_2"
KITCHEN = "binary_sensor.window_kitchen"
H_LIVING = "sensor.living_humidity"
H_KITCHEN = "sensor.kitchen_humidity"
T_LIVING = "sensor.living_temperature"

T_IN = 21.0
EQ = 50.0
DEW_OUT = dew_point(T_IN, EQ)  # outdoor dew point giving 50 % equilibrium


class Sim:
    """Simulated zone: humidity follows the exponential model while open."""

    def __init__(self, engine: ZoneEngine, h0=70.0, true_tau=60.0, report_step=1.0):
        self.engine = engine
        self.t = 0.0
        self.h = h0
        self.reported = h0
        self.reported_at = -3600.0
        self.true_tau = true_tau
        self.report_step = report_step  # sensor only reports changes ≥ 1 point
        self.windows = {w: WindowState(False, -7200.0) for w in engine.windows}
        self.temp = T_IN
        self.extra_humidity: dict[str, Reading | None] = {}
        self.events: list = []

    def set_window(self, window, is_open):
        self.windows[window] = WindowState(is_open, self.t)

    def snapshot(self):
        return Snapshot(
            now=self.t,
            windows=dict(self.windows),
            humidity={H_LIVING: Reading(self.reported, self.reported_at), **self.extra_humidity},
            temperature={T_LIVING: Reading(self.temp, 0.0)},
            outdoor_dew_point=DEW_OUT,
            outdoor_temperature=8.0,
        )

    def run(self, minutes, step=15.0):
        end = self.t + minutes * 60
        while self.t < end:
            self.t += step
            if any(w is not None and w.is_open for w in self.windows.values()):
                self.h = EQ + (self.h - EQ) * math.exp(-step / 60 / self.true_tau)
            if abs(self.h - self.reported) >= self.report_step:
                self.reported, self.reported_at = self.h, self.t
            self.events += self.engine.update(self.snapshot())

    def types(self):
        return [e.type for e in self.events]


def make_engine(windows=(LIVING,), tau=60.0, runs=0, **params):
    return ZoneEngine("Living area", list(windows), EngineParams(**params), tau, runs)


def test_full_session_learns_time_constant():
    engine = make_engine(tau=60, runs=0)
    sim = Sim(engine, h0=70, true_tau=40)
    sim.run(1)
    assert engine.status == STATUS_IDLE

    sim.set_window(LIVING, True)
    sim.run(0.25)
    assert engine.status == STATUS_IDLE  # open_delay 30 s
    sim.run(0.5)
    assert engine.status == STATUS_AIRING
    session = engine.session
    assert math.isclose(session.gap, 20, abs_tol=0.01)
    # gap 20 < 0.3·60 → 0.223·60 ≈ 13.4 min
    assert round(session.duration, 1) == 13.4

    sim.run(14)
    assert engine.status == STATUS_CLOSE_NOW
    assert engine.session.reason is not None

    sim.set_window(LIVING, False)
    sim.run(0.5)
    assert engine.status == STATUS_SETTLING
    sim.run(15)
    assert engine.status == STATUS_IDLE
    assert sim.types()[:2] == [EVENT_STARTED, EVENT_CLOSE_NOW]
    finished = [e for e in sim.events if e.type == EVENT_FINISHED][0]
    assert finished.data["learned"]
    # First run replaces the guess; true τ is 40, sensor rounding makes it ≈ 40–50.
    assert 35 <= engine.base.tau <= 52
    assert engine.base.runs == 1
    assert not engine.worthwhile  # cooldown after airing


def test_close_reason_time_reached():
    engine = make_engine(tau=60, runs=5)
    sim = Sim(engine, h0=70, true_tau=60)
    sim.set_window(LIVING, True)
    sim.run(13)
    assert engine.status == STATUS_AIRING
    sim.run(2)
    assert engine.session.reason == REASON_TIME
    assert engine.progress == 100


def test_room_cooling_ends_early_and_reminds():
    engine = make_engine(tau=60, runs=5)
    sim = Sim(engine)
    sim.set_window(LIVING, True)
    sim.run(4)
    sim.temp = 17.5  # 3.5 K colder
    sim.run(0.5)
    assert engine.session.reason == REASON_COOLING
    sim.run(10)
    assert sim.types().count(EVENT_REMINDER) == 1
    sim.run(10)
    assert sim.types().count(EVENT_REMINDER) == 1


def test_no_humidity_report_means_no_learning():
    engine = make_engine(tau=60, runs=2)
    sim = Sim(engine, h0=70, true_tau=40, report_step=100)  # sensor never reports
    sim.set_window(LIVING, True)
    sim.run(12)
    sim.set_window(LIVING, False)
    sim.run(16)  # settle_max
    finished = [e for e in sim.events if e.type == EVENT_FINISHED][0]
    assert not finished.data["learned"]
    assert engine.base.tau == 60 and engine.base.runs == 2


def test_reopen_while_settling_discards_and_restarts():
    engine = make_engine()
    sim = Sim(engine, report_step=100)
    sim.set_window(LIVING, True)
    sim.run(5)
    sim.set_window(LIVING, False)
    sim.run(1)
    assert engine.status == STATUS_SETTLING
    sim.set_window(LIVING, True)
    sim.run(1)
    assert engine.status == STATUS_AIRING
    assert sim.types() == [EVENT_STARTED, EVENT_DISCARDED, EVENT_STARTED]


def test_unknown_window_state_does_not_close_session():
    engine = make_engine()
    sim = Sim(engine)
    sim.set_window(LIVING, True)
    sim.run(2)
    sim.windows[LIVING] = None  # e.g. unavailable after restart
    sim.run(1)
    assert engine.status == STATUS_AIRING


def test_window_profiles_are_learned_separately():
    engine = make_engine(windows=(LIVING, LIVING_2, KITCHEN), tau=60, runs=0)
    sim = Sim(engine, true_tau=30)
    for _ in range(3):
        sim.h = sim.reported = 70.0
        sim.set_window(LIVING, True)
        sim.set_window(KITCHEN, True)
        sim.run(12)
        sim.set_window(LIVING, False)
        sim.set_window(KITCHEN, False)
        sim.run(16)
        sim.run(65)  # beyond cooldown
    key = f"{KITCHEN}+{LIVING}"
    assert engine.profiles[key].runs == 3
    # Cross ventilation profile is now used for that combination ...
    assert engine.time_constant_for({LIVING, KITCHEN}) is engine.profiles[key]
    # ... while other combinations fall back to the zone value.
    assert engine.time_constant_for({LIVING_2}) is engine.base


def test_profile_ignores_window_opened_only_briefly():
    engine = make_engine(windows=(LIVING, KITCHEN))
    sim = Sim(engine, true_tau=30)
    sim.set_window(LIVING, True)
    sim.run(10)
    sim.set_window(KITCHEN, True)
    sim.run(1)
    sim.set_window(KITCHEN, False)
    sim.set_window(LIVING, False)
    sim.run(16)
    finished = [e for e in sim.events if e.type == EVENT_FINISHED][0]
    assert finished.data["windows"] == [LIVING]


def test_spike_guard_ignores_cooking_humidity():
    snap = Snapshot(
        now=0,
        windows={},
        humidity={H_LIVING: Reading(62, 0), H_KITCHEN: Reading(85, 0)},
        temperature={},
        outdoor_dew_point=None,
        outdoor_temperature=None,
    )
    assert aggregate(snap, 10).humidity == 62
    assert aggregate(snap, 0).humidity == 73.5
    snap_normal = Snapshot(0, {}, {H_LIVING: Reading(64, 0), H_KITCHEN: Reading(70, 0)}, {}, None, None)
    assert aggregate(snap_normal, 10).humidity == 67


def test_worthwhile_when_idle_and_gap_large():
    engine = make_engine()
    sim = Sim(engine, h0=70)
    sim.run(1)
    assert engine.worthwhile
    sim.h = sim.reported = 55  # gap 5
    sim.run(1)
    assert not engine.worthwhile


def test_state_survives_restart():
    engine = make_engine(windows=(LIVING, KITCHEN), tau=80, runs=4)
    sim = Sim(engine)
    sim.set_window(LIVING, True)
    sim.run(3)
    data = engine.to_dict()

    restored = make_engine(windows=(LIVING, KITCHEN))
    restored.load(data)
    assert restored.base.tau == 80 and restored.base.runs == 4
    assert restored.session.started == engine.session.started
    sim.engine = restored
    sim.run(20)
    assert restored.status == STATUS_CLOSE_NOW
