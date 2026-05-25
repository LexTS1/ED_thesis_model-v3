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


def _hour_float_in_window(hour: float, start_hour: float, end_hour: float) -> bool:
    """Return whether a fractional hour falls inside a possibly wrap-around window."""

    hour = float(hour) % 24.0
    start = float(start_hour) % 24.0
    end = float(end_hour) % 24.0
    if abs(start - end) < 1e-9:
        return True
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end


def _circular_hour_distance(hour: float, target_hour: float) -> float:
    """Return shortest absolute distance between two clock hours."""

    raw = abs((float(hour) % 24.0) - (float(target_hour) % 24.0))
    return min(raw, 24.0 - raw)


def _ev_charging_window(charging_cfg: Mapping[str, Any]) -> tuple[float, float]:
    """Resolve the EV charging window with backward-compatible defaults."""

    if "charging_window" in charging_cfg:
        window_cfg = dict(charging_cfg.get("charging_window", {}))
    else:
        window_cfg = dict(charging_cfg.get("uncontrolled_arrival_window", {}))
    strategy = str(charging_cfg.get("charging_strategy", "")).strip().lower()
    default_start = 22 if strategy in {"delayed_overnight", "delayed_overnight_home"} else 17
    default_end = 6 if strategy in {"delayed_overnight", "delayed_overnight_home"} else 22
    return (
        float(window_cfg.get("start_hour", default_start)),
        float(window_cfg.get("end_hour", default_end)),
    )


def _ev_charging_peak_hour(charging_cfg: Mapping[str, Any], start_hour: float, end_hour: float) -> float:
    """Resolve the nominal EV charging peak hour."""

    if charging_cfg.get("peak_hour") is not None:
        return float(charging_cfg["peak_hour"]) % 24.0
    strategy = str(charging_cfg.get("charging_strategy", "")).strip().lower()
    if strategy in {"delayed_overnight", "delayed_overnight_home"}:
        return 1.0
    if start_hour == end_hour:
        return 0.0
    if start_hour < end_hour:
        return ((start_hour + end_hour) / 2.0) % 24.0
    return ((start_hour + ((24.0 - start_hour + end_hour) / 2.0)) % 24.0)


def _ev_hour_weight(hour: float, charging_cfg: Mapping[str, Any], *, peak_jitter_hours: float = 0.0) -> float:
    """Return the unnormalised EV charging weight for a clock hour."""

    start_hour, end_hour = _ev_charging_window(charging_cfg)
    peak_hour = (_ev_charging_peak_hour(charging_cfg, start_hour, end_hour) + float(peak_jitter_hours)) % 24.0
    shifted_start = (start_hour + float(peak_jitter_hours)) % 24.0
    shifted_end = (end_hour + float(peak_jitter_hours)) % 24.0
    if not _hour_float_in_window(hour, shifted_start, shifted_end):
        return 0.0

    shape = str(charging_cfg.get("charging_shape", "")).strip().lower()
    if shape in {"", "flat", "block"}:
        return 1.0

    spread = max(value_from_range(charging_cfg.get("profile_spread_hours"), 1.5), 0.25)
    distance = _circular_hour_distance(hour, peak_hour)
    return float(np.exp(-0.5 * (distance / spread) ** 2))


def _ev_daily_weight_sum(charging_cfg: Mapping[str, Any], *, peak_jitter_hours: float = 0.0) -> float:
    """Return the hourly daily weight sum for snapshot EV calculations."""

    weights = [
        _ev_hour_weight(float(hour), charging_cfg, peak_jitter_hours=peak_jitter_hours)
        for hour in range(24)
    ]
    return max(float(sum(weights)), 1e-9)


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
    peak_jitter_hours: float = 0.0,
) -> float:
    """Return a simple home-charging load for one timestamp."""

    if not bool(has_ev):
        return 0.0

    charging_cfg = dict(ev_cfg.get("charging", {}))
    current = pd.Timestamp(timestamp)
    hour = float(current.hour) + float(current.minute) / 60.0 + float(current.second) / 3600.0
    weight = _ev_hour_weight(hour, charging_cfg, peak_jitter_hours=peak_jitter_hours)
    if weight <= 0.0:
        return 0.0

    annual_kwh = annual_ev_home_charging_kwh(ev_cfg)
    daily_weight_sum = _ev_daily_weight_sum(charging_cfg, peak_jitter_hours=peak_jitter_hours)
    unconstrained_w = (annual_kwh / 365.0) * 1000.0 * weight / daily_weight_sum
    charger_limit_w = value_from_range(charging_cfg.get("charger_power_kw"), 7.4) * 1000.0
    return float(min(max(unconstrained_w, 0.0), max(charger_limit_w, 0.0)))


def build_ev_charging_profile(
    timestamps: Sequence[Any],
    ev_cfg: Mapping[str, Any],
    *,
    has_ev: bool,
    random_seed: int | None = None,
) -> tuple[float, ...]:
    """Build an EV charging profile for a household."""

    if not bool(has_ev):
        return tuple(0.0 for _ in timestamps)
    if len(timestamps) == 0:
        return ()

    charging_cfg = dict(ev_cfg.get("charging", {}))
    jitter_sigma = max(value_from_range(charging_cfg.get("peak_jitter_sigma_hours"), 0.0), 0.0)
    peak_jitter_hours = 0.0
    if random_seed is not None and jitter_sigma > 0.0:
        max_jitter = max(value_from_range(charging_cfg.get("peak_jitter_max_hours"), 3.0), 0.0)
        rng = np.random.default_rng(int(random_seed))
        peak_jitter_hours = float(np.clip(rng.normal(0.0, jitter_sigma), -max_jitter, max_jitter))

    weights = []
    for timestamp in timestamps:
        current = pd.Timestamp(timestamp)
        hour = float(current.hour) + float(current.minute) / 60.0 + float(current.second) / 3600.0
        weights.append(_ev_hour_weight(hour, charging_cfg, peak_jitter_hours=peak_jitter_hours))
    weights_array = np.asarray(weights, dtype=float)
    if not np.any(weights_array > 0.0):
        return tuple(0.0 for _ in timestamps)

    index = pd.DatetimeIndex(pd.to_datetime(list(timestamps)))
    if len(index) >= 2:
        deltas = index.to_series().sort_values().diff().dropna().dt.total_seconds() / 3600.0
        timestep_hours = float(deltas.median()) if not deltas.empty else 1.0
    else:
        timestep_hours = 1.0

    annual_kwh = annual_ev_home_charging_kwh(ev_cfg)
    raw_energy_weight = float(np.sum(weights_array * timestep_hours))
    unconstrained_w = weights_array * (annual_kwh * 1000.0 / max(raw_energy_weight, 1e-9))
    charger_limit_w = value_from_range(charging_cfg.get("charger_power_kw"), 7.4) * 1000.0
    return tuple(float(value) for value in np.clip(unconstrained_w, 0.0, max(charger_limit_w, 0.0)).tolist())


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
