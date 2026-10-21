"""Physical model of airing a room.

Pure functions without Home Assistant imports, so they can be unit tested
directly. All humidities are relative humidity in percent points, all
temperatures in °C and all durations in minutes.

The core idea: while a window is open, indoor humidity approaches the
*equilibrium humidity* exponentially,

    h(t) = eq + gap · e^(−t/τ)

where ``eq`` is the relative humidity the room would have after a complete
air exchange (outdoor dew point at indoor temperature), ``gap = h0 − eq``
and ``τ`` is the learned exchange time constant of the zone.
"""

from __future__ import annotations

from dataclasses import dataclass
import math

MAGNUS_A = 17.62
MAGNUS_B = 243.12


def saturation_vapor_pressure(temperature: float) -> float:
    """Saturation vapour pressure in hPa (Magnus formula)."""
    return 6.112 * math.exp(MAGNUS_A * temperature / (MAGNUS_B + temperature))


def dew_point(temperature: float, humidity: float) -> float:
    """Dew point in °C for a temperature and relative humidity."""
    humidity = min(max(humidity, 0.1), 100.0)
    gamma = math.log(humidity / 100) + MAGNUS_A * temperature / (MAGNUS_B + temperature)
    return MAGNUS_B * gamma / (MAGNUS_A - gamma)


def equilibrium_humidity(outdoor_dew_point: float, indoor_temperature: float) -> float:
    """Relative humidity after a complete exchange with outdoor air."""
    return (
        100
        * saturation_vapor_pressure(outdoor_dew_point)
        / saturation_vapor_pressure(indoor_temperature)
    )


@dataclass(frozen=True)
class ModelParams:
    """Tunable parameters of the airing model."""

    # Stop when the expected drop falls below this many points per minute.
    marginal_rate: float = 0.3
    # ... but close at least this fraction of the gap (slow windows stay open longer).
    min_gap_fraction: float = 0.2
    min_duration: float = 5.0
    # Below this gap airing hardly lowers humidity; plan fresh-air only.
    min_gap: float = 8.0
    # Upper limit by outdoor temperature: (below °C, minutes), else max_duration.
    duration_limits: tuple[tuple[float, float], ...] = ((0.0, 10.0), (10.0, 20.0), (15.0, 30.0))
    max_duration: float = 45.0
    # Expected drop below this is shown as "airing hardly helps".
    slight_drop: float = 1.5


@dataclass(frozen=True)
class Plan:
    """Planned airing for the current conditions."""

    humidity: float
    equilibrium: float
    gap: float
    tau: float
    duration: float
    expected: float

    @property
    def expected_drop(self) -> float:
        return self.humidity - self.expected

    def slight(self, params: ModelParams) -> bool:
        """Airing will lower humidity only slightly."""
        return self.expected_drop < params.slight_drop


def duration_limit(outdoor_temperature: float | None, params: ModelParams) -> float:
    """Maximum airing duration for the outdoor temperature."""
    if outdoor_temperature is None:
        return params.duration_limits[-1][1] if params.duration_limits else params.max_duration
    for below, minutes in params.duration_limits:
        if outdoor_temperature < below:
            return minutes
    return params.max_duration


def expected_humidity(h0: float, gap: float, tau: float, minutes: float) -> float:
    """Expected humidity after ``minutes`` of airing."""
    return h0 - gap * (1 - math.exp(-max(minutes, 0.0) / tau))


def plan_airing(
    humidity: float,
    indoor_temperature: float,
    outdoor_dew_point: float | None,
    outdoor_temperature: float | None,
    tau: float,
    params: ModelParams,
) -> Plan:
    """Plan duration and expected result of airing now."""
    if outdoor_dew_point is None:
        equilibrium = humidity
    else:
        equilibrium = equilibrium_humidity(outdoor_dew_point, indoor_temperature)
    gap = max(humidity - equilibrium, 0.0)

    if gap >= params.min_gap:
        rate = params.marginal_rate
        optimal = tau * math.log(gap / (rate * tau)) if gap > rate * tau else 0.0
        fraction = -tau * math.log(1 - params.min_gap_fraction)
        duration = min(
            max(optimal, fraction, params.min_duration),
            duration_limit(outdoor_temperature, params),
        )
    else:
        duration = params.min_duration

    return Plan(
        humidity=humidity,
        equilibrium=equilibrium,
        gap=gap,
        tau=tau,
        duration=duration,
        expected=expected_humidity(humidity, gap, tau, duration),
    )


def measured_time_constant(minutes: float, drop: float, gap: float, tau_prior: float) -> float:
    """Time constant implied by a measured drop, limited to 0.5–2 × prior and 10–300 min."""
    fraction = min(max(drop / max(gap, 0.1), 0.01), 0.9)
    raw = -minutes / math.log(1 - fraction)
    return min(max(raw, 10.0, 0.5 * tau_prior), 300.0, 2 * tau_prior)


def blend_time_constant(tau_old: float, tau_measured: float, runs: int) -> float:
    """Running average: equal weights for the first 3 runs, then 30 % new value."""
    weight = 1 / (runs + 1) if runs < 3 else 0.3
    return tau_old * (1 - weight) + tau_measured * weight
