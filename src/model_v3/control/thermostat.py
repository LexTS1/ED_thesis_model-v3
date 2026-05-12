"""Thermostat helpers and compatibility wrapper for model_v3."""

from __future__ import annotations

from typing import Any, Mapping

import pandas as pd

from model_v3.interfaces import ControlState, PhysicsState

DEFAULT_DEADBAND_C = 0.75


def _clamp(value: float, lower: float, upper: float) -> float:
    """Clamp one scalar safely."""

    return float(min(max(float(value), float(lower)), float(upper)))


def compute_hysteresis_heating(
    *,
    t_in_c: float,
    t_set_c: float,
    delta_c: float,
    previous_heating_on: bool,
) -> tuple[bool, float, float]:
    """Apply a symmetric thermostat hysteresis band around the active setpoint."""

    lower_bound_c = float(t_set_c) - max(float(delta_c), 0.0)
    upper_bound_c = float(t_set_c) + max(float(delta_c), 0.0)
    heating_on = bool(previous_heating_on)

    if not heating_on and float(t_in_c) < lower_bound_c:
        heating_on = True
    elif heating_on and float(t_in_c) > upper_bound_c:
        heating_on = False

    return heating_on, lower_bound_c, upper_bound_c


def sample_deadband_c(rng: Any) -> float:
    """Sample a simple household-specific hysteresis half-band."""

    return _clamp(float(rng.uniform(0.5, 1.0)), 0.5, 1.0)


def sample_setpoint_schedule_c(household_class: str, rng: Any) -> dict[str, float]:
    """Sample time-of-day thermostat setpoints with light class effects."""

    resolved_class = str(household_class).strip().lower()
    if resolved_class == "workday_absent":
        day_range = (18.0, 19.0)
    elif resolved_class == "daytime_home":
        day_range = (19.0, 20.0)
    else:
        day_range = (18.0, 20.0)

    return {
        "night": _clamp(float(rng.uniform(16.0, 18.0)), 16.0, 18.0),
        "day": _clamp(float(rng.uniform(*day_range)), 18.0, 20.0),
        "evening": _clamp(float(rng.uniform(20.0, 22.0)), 20.0, 22.0),
    }


def sample_daily_overrides(
    *,
    timestamps: tuple[Any, ...],
    rng: Any,
) -> dict[str, Any]:
    """Sample daily user override events that temporarily shift the schedule."""

    if not timestamps:
        return {"daily_probability": 0.0, "events": {}}

    daily_probability = _clamp(float(rng.uniform(0.10, 0.20)), 0.10, 0.20)
    unique_days = sorted({pd.Timestamp(timestamp).date().isoformat() for timestamp in timestamps})
    events: dict[str, dict[str, float | int]] = {}
    for day_iso in unique_days:
        if float(rng.random()) >= daily_probability:
            continue
        duration_hours = int(rng.integers(3, 7))
        latest_start_hour = max(24 - duration_hours, 6)
        start_hour = int(rng.integers(6, latest_start_hour + 1))
        delta_magnitude_c = float(rng.uniform(1.0, 2.0))
        delta_c = delta_magnitude_c if float(rng.random()) < 0.5 else -delta_magnitude_c
        events[day_iso] = {
            "start_hour": start_hour,
            "end_hour": min(start_hour + duration_hours, 24),
            "delta_c": delta_c,
        }
    return {"daily_probability": daily_probability, "events": events}


def resolve_time_of_day_setpoint(
    *,
    timestamp: Any,
    fallback_setpoint_c: float,
    control_schedule_cfg: Mapping[str, Any] | None = None,
) -> float:
    """Resolve the active setpoint from the sampled household schedule and overrides."""

    control_schedule_cfg = dict(control_schedule_cfg or {})
    schedule = dict(control_schedule_cfg.get("setpoint_schedule_c", {}))
    if not schedule:
        return float(fallback_setpoint_c)

    local_timestamp = pd.Timestamp(timestamp)
    hour = int(local_timestamp.hour)
    if 6 <= hour < 17:
        base_setpoint_c = float(schedule.get("day", fallback_setpoint_c))
    elif 17 <= hour < 23:
        base_setpoint_c = float(schedule.get("evening", fallback_setpoint_c))
    else:
        base_setpoint_c = float(schedule.get("night", fallback_setpoint_c))

    overrides = dict(control_schedule_cfg.get("daily_overrides", {}).get("events", {}))
    active_override = overrides.get(local_timestamp.date().isoformat())
    if active_override:
        start_hour = int(active_override.get("start_hour", 0))
        end_hour = int(active_override.get("end_hour", start_hour))
        if start_hour <= hour < end_hour:
            base_setpoint_c += float(active_override.get("delta_c", 0.0))
    return float(base_setpoint_c)


def run_thermostat(physics_state: PhysicsState, config: object | None = None) -> ControlState:
    """Compatibility wrapper around the core control implementation."""

    _ = config
    from model_v3.control.control_core import run_control

    return run_control(physics_state=physics_state)
