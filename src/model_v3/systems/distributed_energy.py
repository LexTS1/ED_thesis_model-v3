"""Rooftop PV and EV charging helpers for Belgian residential scenarios."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd


def value_from_range(value: Any, default: float) -> float:
    """Resolve a scalar or ``{base, low, high}`` range to its base value."""

    if isinstance(value, Mapping):
        for key in ("base", "recommended_value", "value"):
            if key in value:
                return value_from_range(value[key], default)
        return float(default)
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _window_hours(start_hour: int, end_hour: int) -> int:
    """Return the number of whole clock hours in a possibly wrap-around window."""

    start = int(start_hour) % 24
    end = int(end_hour) % 24
    if start == end:
        return 24
    if start < end:
        return max(end - start, 1)
    return max((24 - start) + end, 1)


def _hour_in_window(hour: int, start_hour: int, end_hour: int) -> bool:
    """Return whether an hour falls inside a possibly wrap-around window."""

    hour = int(hour) % 24
    start = int(start_hour) % 24
    end = int(end_hour) % 24
    if start == end:
        return True
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end


def annual_ev_home_charging_kwh(ev_cfg: Mapping[str, Any]) -> float:
    """Return annual home-charged EV electricity for one active EV household."""

    use_cfg = dict(ev_cfg.get("annual_use", {}))
    charging_cfg = dict(ev_cfg.get("charging", {}))
    km_per_year = value_from_range(use_cfg.get("km_per_year"), 15_000.0)
    specific_kwh_per_100km = value_from_range(
        use_cfg.get("specific_consumption_kwh_per_100km"),
        14.2,
    )
    home_probability = value_from_range(charging_cfg.get("home_charging_probability"), 1.0)
    return max(km_per_year * specific_kwh_per_100km / 100.0 * home_probability, 0.0)


def ev_charging_power_for_timestamp(
    timestamp: Any,
    ev_cfg: Mapping[str, Any],
    *,
    has_ev: bool,
) -> float:
    """Return a simple uncontrolled home-charging load for one timestamp."""

    if not bool(has_ev):
        return 0.0

    charging_cfg = dict(ev_cfg.get("charging", {}))
    window_cfg = dict(charging_cfg.get("uncontrolled_arrival_window", {}))
    start_hour = int(window_cfg.get("start_hour", 17))
    end_hour = int(window_cfg.get("end_hour", 22))
    current = pd.Timestamp(timestamp)
    if not _hour_in_window(int(current.hour), start_hour, end_hour):
        return 0.0

    annual_kwh = annual_ev_home_charging_kwh(ev_cfg)
    active_hours_per_year = _window_hours(start_hour, end_hour) * 365.0
    unconstrained_w = annual_kwh * 1000.0 / max(active_hours_per_year, 1.0)
    charger_limit_w = value_from_range(charging_cfg.get("charger_power_kw"), 7.4) * 1000.0
    return float(min(max(unconstrained_w, 0.0), max(charger_limit_w, 0.0)))


def build_ev_charging_profile(
    timestamps: Sequence[Any],
    ev_cfg: Mapping[str, Any],
    *,
    has_ev: bool,
) -> tuple[float, ...]:
    """Build an hourly EV charging profile for a household."""

    return tuple(
        ev_charging_power_for_timestamp(timestamp, ev_cfg, has_ev=has_ev)
        for timestamp in timestamps
    )


def pv_generation_from_irradiance(
    irradiance_w_per_m2: float,
    pv_cfg: Mapping[str, Any],
    *,
    has_pv: bool,
) -> float:
    """Convert plane-of-array irradiance into AC PV generation for one household."""

    if not bool(has_pv):
        return 0.0
    size_kwp = value_from_range(pv_cfg.get("system_size_kwp"), 0.0)
    inverter_efficiency = value_from_range(pv_cfg.get("inverter_efficiency"), 0.97)
    return float(max(irradiance_w_per_m2, 0.0) * max(size_kwp, 0.0) * max(inverter_efficiency, 0.0))


def fallback_pv_average_power_w(pv_cfg: Mapping[str, Any], *, has_pv: bool) -> float:
    """Return annual-average PV power when no irradiance profile is available."""

    if not bool(has_pv):
        return 0.0
    size_kwp = value_from_range(pv_cfg.get("system_size_kwp"), 0.0)
    yield_cfg = dict(pv_cfg.get("yield", {}))
    specific_yield = value_from_range(
        yield_cfg.get("fallback_specific_yield_kwh_per_kwp_year"),
        950.0,
    )
    inverter_efficiency = value_from_range(pv_cfg.get("inverter_efficiency"), 0.97)
    return max(size_kwp, 0.0) * max(specific_yield, 0.0) * max(inverter_efficiency, 0.0) * 1000.0 / 8760.0


def weighted_irradiance(
    columns: Mapping[str, float],
    pv_cfg: Mapping[str, Any],
) -> float:
    """Resolve the PV irradiance driver from orientation columns and config."""

    orientation_weights = dict(pv_cfg.get("orientation_weights", {}))
    if orientation_weights:
        total_weight = sum(max(float(weight), 0.0) for weight in orientation_weights.values())
        if total_weight > 0.0:
            return float(
                sum(
                    max(float(weight), 0.0) * max(float(columns.get(f"I_solar_{orientation}_W_per_m2", 0.0)), 0.0)
                    for orientation, weight in orientation_weights.items()
                )
                / total_weight
            )

    orientation = str(pv_cfg.get("orientation", "south")).strip().lower()
    if orientation in {"south", "east", "west", "north"}:
        return float(max(columns.get(f"I_solar_{orientation}_W_per_m2", 0.0), 0.0))

    available = [
        max(float(value), 0.0)
        for key, value in columns.items()
        if key.startswith("I_solar_") and key.endswith("_W_per_m2")
    ]
    return float(np.mean(available)) if available else 0.0
