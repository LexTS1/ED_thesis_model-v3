"""Event-based appliance demand generation without state chains."""

from __future__ import annotations

import math
from typing import Any, Mapping

import numpy as np
import pandas as pd

from model_v3.stochastic.household_classifier import HouseholdBehaviourClass


def _hourly_rate_profile(base_rate: float, windows: tuple[tuple[int, int, float], ...]) -> tuple[float, ...]:
    """Build a 24-element hourly rate profile in events per hour."""

    profile = np.full(24, float(base_rate), dtype=float)
    for start_hour, stop_hour, rate in windows:
        profile[start_hour:stop_hour] = float(rate)
    return tuple(float(value) for value in profile.tolist())


_APPLIANCE_RATE_TABLES: dict[str, dict[str, tuple[float, ...]]] = {
    "cooking": {
        "weekday": _hourly_rate_profile(0.002, ((6, 8, 0.05), (12, 14, 0.12), (18, 21, 0.36))),
        "weekend": _hourly_rate_profile(0.003, ((8, 10, 0.05), (12, 14, 0.16), (18, 21, 0.32))),
    },
    "kettle": {
        "weekday": _hourly_rate_profile(0.01, ((6, 9, 0.35), (12, 14, 0.12), (17, 22, 0.26))),
        "weekend": _hourly_rate_profile(0.015, ((7, 11, 0.24), (12, 16, 0.14), (17, 22, 0.22))),
    },
    "washing_machine": {
        "weekday": _hourly_rate_profile(0.001, ((7, 10, 0.02), (18, 22, 0.03))),
        "weekend": _hourly_rate_profile(0.002, ((9, 18, 0.05),)),
    },
    "dishwasher": {
        "weekday": _hourly_rate_profile(0.0005, ((13, 15, 0.01), (19, 23, 0.045))),
        "weekend": _hourly_rate_profile(0.001, ((12, 15, 0.015), (19, 23, 0.05))),
    },
    "dryer": {
        "weekday": _hourly_rate_profile(0.0002, ((11, 14, 0.008), (18, 22, 0.018))),
        "weekend": _hourly_rate_profile(0.0005, ((11, 19, 0.025),)),
    },
    "ev": {
        "weekday": _hourly_rate_profile(0.0, ((17, 23, 0.05),)),
        "weekend": _hourly_rate_profile(0.0, ((10, 14, 0.01), (18, 23, 0.035))),
    },
}

_APPLIANCE_BETAS: dict[str, float] = {
    "cooking": 0.95,
    "kettle": 0.80,
    "washing_machine": 0.20,
    "dishwasher": 0.15,
    "dryer": 0.25,
    "ev": 0.85,
}

_APPLIANCE_TO_OUTPUT_COLUMN: dict[str, str] = {
    "cooking": "cooking",
    "kettle": "cooking",
    "washing_machine": "appliances",
    "dishwasher": "appliances",
    "dryer": "appliances",
    "ev": "appliances",
}

_OPTIONAL_APPLIANCES = frozenset({"dryer", "ev"})


def _effective_substep_seconds(target_resolution_seconds: int) -> int:
    """Use a fixed fine grid when possible so short events span multiple substeps."""

    target = max(int(target_resolution_seconds), 60)
    return max(math.gcd(target, 300), 60)


def _build_substep_timestamps(
    timestamps: tuple[Any, ...],
    target_resolution_seconds: int,
    substep_seconds: int,
) -> tuple[list[pd.Timestamp], int]:
    """Expand output timestamps onto an internal, finer event grid."""

    if not timestamps:
        return [], 1
    substeps_per_step = max(int(target_resolution_seconds // substep_seconds), 1)
    substep_timestamps: list[pd.Timestamp] = []
    for timestamp in timestamps:
        base_timestamp = pd.Timestamp(timestamp)
        for substep_index in range(substeps_per_step):
            substep_timestamps.append(base_timestamp + pd.Timedelta(seconds=substep_index * substep_seconds))
    return substep_timestamps, substeps_per_step


def _lookup_hourly_rate(appliance: str, timestamp: pd.Timestamp) -> float:
    """Return the base hourly start intensity for one appliance and time bin."""

    profile_key = "weekend" if bool(timestamp.dayofweek >= 5) else "weekday"
    return float(_APPLIANCE_RATE_TABLES[appliance][profile_key][int(timestamp.hour)])


def _class_appliance_rate_multiplier(
    appliance: str,
    household_class: HouseholdBehaviourClass,
    timestamp: pd.Timestamp,
) -> float:
    """Apply appliance-specific class effects without changing the event structure."""

    if appliance != "cooking":
        return 1.0

    hour = int(timestamp.hour)
    multiplier = 1.0
    if household_class.name == "peak_heavy_family" and (12 <= hour < 14 or 18 <= hour < 21):
        multiplier *= 1.3
    if household_class.name == "workday_absent" and bool(timestamp.dayofweek < 5) and 12 <= hour < 14:
        multiplier *= 0.45
    return multiplier


def _occupancy_modifier(household_class: HouseholdBehaviourClass, timestamp: pd.Timestamp) -> float:
    """Simple occupancy-style scaling by regime, hour, and weekday/weekend."""

    hour = int(timestamp.hour)
    is_weekend = bool(timestamp.dayofweek >= 5)
    modifier = household_class.occupancy_scaling_factor

    if household_class.name == "low_flat":
        if 17 <= hour < 22:
            modifier *= 0.95
        return modifier

    if household_class.name == "workday_absent":
        if not is_weekend and 9 <= hour < 17:
            modifier *= 0.35
        elif 6 <= hour < 9 or 17 <= hour < 22:
            modifier *= 1.20
        else:
            modifier *= 0.90
        return modifier

    if household_class.name == "peak_heavy_family":
        if 6 <= hour < 9 or 17 <= hour < 22:
            modifier *= 1.35
        elif 12 <= hour < 15:
            modifier *= 1.10
        return modifier

    if household_class.name == "daytime_home":
        if 8 <= hour < 17:
            modifier *= 1.30
        else:
            modifier *= 1.05
        return modifier

    return modifier


def _sample_duration_steps(
    appliance: str,
    rng: np.random.Generator,
    substep_seconds: int,
) -> int:
    """Sample an event duration on the internal timestep."""

    minute_ranges = {
        "cooking": (20.0, 60.0),
        "kettle": (2.0, 5.0),
        "washing_machine": (60.0, 120.0),
        "dishwasher": (60.0, 90.0),
        "dryer": (45.0, 90.0),
        "ev": (120.0, 480.0),
    }
    lower_minutes, upper_minutes = minute_ranges[appliance]
    sampled_minutes = float(rng.uniform(lower_minutes, upper_minutes))
    duration_steps = int(max(np.ceil(sampled_minutes * 60.0 / max(substep_seconds, 1)), 1))
    return max(duration_steps, 1)


def _piecewise_profile(
    duration_steps: int,
    levels_w: tuple[float, ...],
    fractions: tuple[float, ...],
) -> np.ndarray:
    """Build a piecewise-constant appliance cycle."""

    profile = np.zeros(duration_steps, dtype=float)
    start_index = 0
    for segment_index, (level_w, fraction) in enumerate(zip(levels_w, fractions)):
        if segment_index == len(levels_w) - 1:
            stop_index = duration_steps
        else:
            stop_index = min(duration_steps, start_index + max(int(round(duration_steps * fraction)), 1))
        profile[start_index:stop_index] = float(level_w)
        start_index = stop_index
    if start_index < duration_steps:
        profile[start_index:] = float(levels_w[-1])
    return profile


def _sample_power_profile(appliance: str, duration_steps: int, rng: np.random.Generator) -> np.ndarray:
    """Sample a simple but non-smoothed appliance event power profile."""

    if appliance == "kettle":
        return np.full(duration_steps, float(rng.uniform(2000.0, 3000.0)), dtype=float)
    if appliance == "cooking":
        return _piecewise_profile(
            duration_steps=duration_steps,
            levels_w=(
                float(rng.uniform(2000.0, 3000.0)),
                float(rng.uniform(1000.0, 2000.0)),
            ),
            fractions=(0.50, 0.50),
        )
    if appliance == "washing_machine":
        return _piecewise_profile(
            duration_steps=duration_steps,
            levels_w=(
                float(rng.uniform(1400.0, 2000.0)),
                float(rng.uniform(350.0, 650.0)),
                float(rng.uniform(500.0, 900.0)),
            ),
            fractions=(0.20, 0.60, 0.20),
        )
    if appliance == "dishwasher":
        return _piecewise_profile(
            duration_steps=duration_steps,
            levels_w=(
                float(rng.uniform(350.0, 700.0)),
                float(rng.uniform(1000.0, 1800.0)),
                float(rng.uniform(450.0, 850.0)),
            ),
            fractions=(0.20, 0.50, 0.30),
        )
    if appliance == "dryer":
        return _piecewise_profile(
            duration_steps=duration_steps,
            levels_w=(
                float(rng.uniform(1800.0, 2500.0)),
                float(rng.uniform(1200.0, 1800.0)),
            ),
            fractions=(0.60, 0.40),
        )
    if appliance == "ev":
        return np.full(duration_steps, float(rng.uniform(3000.0, 7000.0)), dtype=float)
    return np.zeros(duration_steps, dtype=float)


def _aggregate_substeps_to_output(values: np.ndarray, n_steps: int, substeps_per_step: int) -> np.ndarray:
    """Aggregate the internal event grid back to output-resolution average power."""

    if n_steps <= 0:
        return np.zeros(0, dtype=float)
    reshaped = np.asarray(values, dtype=float).reshape(n_steps, substeps_per_step)
    return np.nan_to_num(reshaped.mean(axis=1), nan=0.0, posinf=0.0, neginf=0.0)


def generate_appliance_events(
    *,
    timestamps: tuple[Any, ...],
    target_resolution_seconds: int,
    household_class: HouseholdBehaviourClass,
    household_random_effect_u: float,
    occupancy_scale: float,
    daily_peak_driver: Mapping[Any, float],
    rng: np.random.Generator,
    event_rate_scale: float = 1.0,
    has_dryer: bool = False,
    has_ev: bool = False,
) -> dict[str, Any]:
    """Generate event-based appliance profiles and event summaries."""

    if not timestamps:
        empty_columns = {"appliances": tuple(), "cooking": tuple()}
        return {
            "output_loads": empty_columns,
            "component_output_loads": {name: tuple() for name in _APPLIANCE_RATE_TABLES},
            "event_profile_total_W": tuple(),
            "event_summary": {"total_event_count": 0, "event_count_by_appliance": {}, "event_peak_W": 0.0},
            "event_log": [],
            "substep_seconds": _effective_substep_seconds(target_resolution_seconds),
        }

    substep_seconds = _effective_substep_seconds(target_resolution_seconds)
    substep_timestamps, substeps_per_step = _build_substep_timestamps(
        timestamps=timestamps,
        target_resolution_seconds=target_resolution_seconds,
        substep_seconds=substep_seconds,
    )
    dt_hours = float(substep_seconds) / 3600.0
    n_output_steps = len(timestamps)
    n_substeps = len(substep_timestamps)
    random_effect_scale = float(np.exp(float(household_random_effect_u)))
    occupancy_scale = max(float(occupancy_scale), 0.0)
    event_rate_scale = max(float(event_rate_scale), 1e-6)

    component_profiles_substeps = {
        appliance: np.zeros(n_substeps, dtype=float)
        for appliance in _APPLIANCE_RATE_TABLES
    }
    event_log: list[dict[str, Any]] = []

    for appliance in _APPLIANCE_RATE_TABLES:
        if appliance == "dryer" and not bool(has_dryer):
            continue
        if appliance == "ev" and not bool(has_ev):
            continue

        active_until = -1
        for substep_index, timestamp in enumerate(substep_timestamps):
            if substep_index < active_until:
                continue

            base_rate = _lookup_hourly_rate(appliance, timestamp)
            if base_rate <= 0.0:
                continue
            occupancy_modifier = _occupancy_modifier(household_class, timestamp)
            daily_driver = float(daily_peak_driver.get(timestamp.date(), 0.0))
            beta = float(_APPLIANCE_BETAS.get(appliance, 0.0)) * household_class.peak_sensitivity_factor
            rate_multiplier = (
                household_class.event_intensity_multiplier
                * occupancy_scale
                * occupancy_modifier
                * _class_appliance_rate_multiplier(appliance, household_class, timestamp)
                * random_effect_scale
                * float(np.exp(beta * daily_driver))
            )
            event_rate = max(base_rate * rate_multiplier * event_rate_scale, 0.0)
            p_start = float(np.clip(event_rate * dt_hours, 0.0, 0.95))
            if float(rng.random()) >= p_start:
                continue

            duration_steps = _sample_duration_steps(appliance=appliance, rng=rng, substep_seconds=substep_seconds)
            power_profile = _sample_power_profile(appliance=appliance, duration_steps=duration_steps, rng=rng)
            if power_profile.size <= 0:
                continue

            stop_index = min(n_substeps, substep_index + len(power_profile))
            applied_profile = np.clip(power_profile[: max(stop_index - substep_index, 0)], 0.0, None)
            if applied_profile.size <= 0:
                continue

            component_profiles_substeps[appliance][substep_index:stop_index] += applied_profile
            active_until = stop_index
            event_log.append(
                {
                    "appliance": appliance,
                    "start_timestamp": timestamp.isoformat(),
                    "end_timestamp": substep_timestamps[stop_index - 1].isoformat(),
                    "duration_minutes": float(applied_profile.size * substep_seconds / 60.0),
                    "mean_power_W": float(applied_profile.mean()),
                    "peak_power_W": float(applied_profile.max()),
                    "energy_kWh": float(applied_profile.sum() * substep_seconds / 3_600_000.0),
                    "daily_peak_driver": daily_driver,
                }
            )

    output_category_profiles = {
        "appliances": np.zeros(n_output_steps, dtype=float),
        "cooking": np.zeros(n_output_steps, dtype=float),
    }
    component_output_loads: dict[str, tuple[float, ...]] = {}
    for appliance, profile_substeps in component_profiles_substeps.items():
        output_profile = _aggregate_substeps_to_output(
            values=profile_substeps,
            n_steps=n_output_steps,
            substeps_per_step=substeps_per_step,
        )
        component_output_loads[appliance] = tuple(float(value) for value in np.clip(output_profile, 0.0, None).tolist())
        output_category_profiles[_APPLIANCE_TO_OUTPUT_COLUMN[appliance]] += output_profile

    total_event_profile = output_category_profiles["appliances"] + output_category_profiles["cooking"]
    event_count_by_appliance: dict[str, int] = {}
    for event in event_log:
        appliance_name = str(event["appliance"])
        event_count_by_appliance[appliance_name] = event_count_by_appliance.get(appliance_name, 0) + 1

    return {
        "output_loads": {
            "appliances": tuple(float(value) for value in np.clip(output_category_profiles["appliances"], 0.0, None).tolist()),
            "cooking": tuple(float(value) for value in np.clip(output_category_profiles["cooking"], 0.0, None).tolist()),
        },
        "component_output_loads": component_output_loads,
        "event_profile_total_W": tuple(float(value) for value in np.clip(total_event_profile, 0.0, None).tolist()),
        "event_summary": {
            "total_event_count": int(len(event_log)),
            "event_count_by_appliance": event_count_by_appliance,
            "event_peak_W": float(np.max(total_event_profile)) if len(total_event_profile) else 0.0,
            "substep_seconds": int(substep_seconds),
            "optional_appliance_presence": {
                appliance: bool((appliance not in _OPTIONAL_APPLIANCES) or (appliance == "dryer" and has_dryer) or (appliance == "ev" and has_ev))
                for appliance in _APPLIANCE_RATE_TABLES
            },
        },
        "event_log": event_log,
        "substep_seconds": int(substep_seconds),
    }
