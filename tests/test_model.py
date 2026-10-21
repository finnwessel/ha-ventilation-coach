"""Tests for the physical model (no Home Assistant needed)."""

import math

from custom_components.ventilation_coach.model import (
    ModelParams,
    blend_time_constant,
    dew_point,
    duration_limit,
    equilibrium_humidity,
    measured_time_constant,
    plan_airing,
)

PARAMS = ModelParams()


def close(a, b, tol=0.15):
    return math.isclose(a, b, abs_tol=tol)


def test_dew_point_roundtrip():
    td = dew_point(21.0, 60.0)
    assert close(td, 12.9, 0.2)
    # Air at its own dew point is saturated.
    assert close(equilibrium_humidity(td, 21.0), 60.0, 0.01)


def test_equilibrium_humidity_colder_outside_is_drier():
    assert equilibrium_humidity(7.9, 21.0) < 50


def test_duration_limits_by_outdoor_temperature():
    assert duration_limit(-3, PARAMS) == 10
    assert duration_limit(5, PARAMS) == 20
    assert duration_limit(12, PARAMS) == 30
    assert duration_limit(20, PARAMS) == 45


def _plan_with_gap(h, gap, tau, outdoor=8.9):
    """Plan where the indoor temperature is chosen so that h − eq = gap."""
    t = 21.0
    td = dew_point(t, h - gap)
    return plan_airing(h, t, td, outdoor, tau, PARAMS)


def test_documented_example_20_october():
    # Di 20.10. 12:55: 71 %, Gleichgewicht 51 %, τ 80 → 18 Min, ≈ 67 %
    plan = _plan_with_gap(71, 20, 80)
    assert close(plan.equilibrium, 51)
    assert round(plan.duration) == 18
    assert round(plan.expected) == 67


def test_large_gap_uses_marginal_rate():
    plan = _plan_with_gap(75, 40, 40)
    # τ·ln(gap/(0.3·τ)) = 40·ln(40/12) ≈ 48 → capped at 20 min for 8.9 °C outside
    assert plan.duration == 20


def test_small_gap_is_fresh_air_only():
    plan = _plan_with_gap(60, 5, 60)
    assert plan.duration == PARAMS.min_duration
    assert plan.slight(PARAMS)


def test_missing_outdoor_dew_point_means_no_gap():
    plan = plan_airing(70, 21, None, 10, 60, PARAMS)
    assert plan.gap == 0


def test_measured_time_constant_is_limited():
    # 10 min, gap 20, drop 4 → raw ≈ 44.8
    assert close(measured_time_constant(10, 4, 20, 60), 44.8, 0.1)
    # No drop at all would be ∞ → limited to 2 × prior
    assert measured_time_constant(10, 0, 20, 60) == 120
    # Huge drop → limited to 0.5 × prior
    assert measured_time_constant(10, 19, 20, 100) == 50


def test_blend_weights():
    assert blend_time_constant(60, 120, 0) == 120  # first run replaces the guess
    assert blend_time_constant(60, 120, 1) == 90
    assert close(blend_time_constant(100, 200, 5), 130, 1e-9)
