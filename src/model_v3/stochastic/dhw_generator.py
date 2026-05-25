"""Occupancy-driven stochastic DHW event generation."""

from __future__ import annotations

from datetime import timedelta
import math
from typing import Any, Mapping

import numpy as np
import pandas as pd

from model_v3.adapters.forcing_builder import _build_occupancy_profiles
from model_v3.stochastic.household_classifier import HouseholdBehaviourClass

_DHW_CLASS_MULTIPLIERS: dict[str, float] = {
    "low_flat": 0.7,
    "workday_absent": 0.9,
    "peak_heavy_family": 1.4,
    "daytime_home": 1.2,
}

_DHW_EVENT_TYPE_WEIGHTS: tuple[tuple[str, float], ...] = (
    ("shower", 0.40),
    ("sink", 0.40),
    ("dishwashing", 0.20),
)

_DHW_EVENT_PARAMETERS: dict[str, dict[str, tuple[float, float]]] = {
    "shower": {
        "duration_minutes": (5.0, 10.0),
        "power_w": (3000.0, 6000.0),
    },
    "sink": {
        "duration_minutes": (1.0, 3.0),
        "power_w": (1000.0, 2000.0),
    },
    "dishwashing": {
        "duration_minutes": (20.0, 60.0),
        "power_w": (1000.0, 2500.0),
    },
}

_KWH_PER_LITER_K = 4.186 / 3600.0

DEFAULT_DHW_CALIBRATION: dict[str, Any] = {
    "enabled": False,
    "daily_useful_kWh_per_person": {"low": 0.8, "base": 1.2, "high": 1.5},
    "equivalent_litres_per_person_day_42C": {"base": 32.0},
    "cold_water_temperature_C": 10.0,
    "draw_temperature_C": 42.0,
    "event_frequency_per_occupant_day": {"low": 1.4, "base": 2.0, "high": 3.0},
    "timing_weights": {
        "morning": {"start_hour": 6.0, "end_hour": 9.0, "weight": 0.45},
        "daytime": {"start_hour": 9.0, "end_hour": 18.0, "weight": 0.12},
        "evening": {"start_hour": 18.0, "end_hour": 22.0, "weight": 0.40},
        "night": {"start_hour": 22.0, "end_hour": 6.0, "weight": 0.03},
    },
    "event_type_probabilities": {
        "sink": 0.58,
        "shower": 0.26,
        "dishwashing": 0.13,
        "bath": 0.03,
    },
    "event_volume_liters": {
        "sink": {"low": 1.0, "high": 6.0},
        "shower": {"low": 25.0, "high": 65.0},
        "dishwashing": {"low": 8.0, "high": 18.0},
        "bath": {"low": 80.0, "high": 140.0},
    },
    "event_duration_minutes": {
        "sink": {"low": 1.0, "high": 3.0},
        "shower": {"low": 5.0, "high": 10.0},
        "dishwashing": {"low": 10.0, "high": 30.0},
        "bath": {"low": 10.0, "high": 20.0},
    },
}


def _effective_substep_seconds(target_resolution_seconds: int) -> int:
    """Use a fine internal grid so DHW events stay discrete."""

    target = max(int(target_resolution_seconds), 60)
    return max(math.gcd(target, 60), 60)


def _build_substep_timestamps(
    timestamps: tuple[Any, ...],
    target_resolution_seconds: int,
    substep_seconds: int,
) -> tuple[list[pd.Timestamp], int]:
    """Expand output timestamps onto an internal event grid."""

    if not timestamps:
        return [], 1
    substeps_per_step = max(int(target_resolution_seconds // substep_seconds), 1)
    substep_timestamps: list[pd.Timestamp] = []
    for timestamp in timestamps:
        base_timestamp = pd.Timestamp(timestamp)
        for substep_index in range(substeps_per_step):
            substep_timestamps.append(base_timestamp + pd.Timedelta(seconds=substep_index * substep_seconds))
    return substep_timestamps, substeps_per_step


def _aggregate_substeps_to_output(values: np.ndarray, n_steps: int, substeps_per_step: int) -> np.ndarray:
    """Aggregate internal substeps back to output average power."""

    if n_steps <= 0:
        return np.zeros(0, dtype=float)
    reshaped = np.asarray(values, dtype=float).reshape(n_steps, substeps_per_step)
    return np.nan_to_num(reshaped.mean(axis=1), nan=0.0, posinf=0.0, neginf=0.0)


def _value_from_range(value: Any, default: float) -> float:
    """Resolve a scalar or ``{base, low, high}`` mapping to its base value."""

    if isinstance(value, Mapping):
        for key in ("base", "recommended_value", "value"):
            if key in value:
                return _value_from_range(value[key], default)
        return float(default)
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _calibration_enabled(dhw_calibration: Mapping[str, Any] | None) -> bool:
    return bool(dict(dhw_calibration or {}).get("enabled", False))


def _calibration_value(dhw_calibration: Mapping[str, Any] | None, key: str, default: float) -> float:
    cfg = dict(dhw_calibration or {})
    return _value_from_range(cfg.get(key, DEFAULT_DHW_CALIBRATION.get(key, default)), default)


def _range_pair(value: Any, default: tuple[float, float]) -> tuple[float, float]:
    if isinstance(value, Mapping):
        lower = _value_from_range(value.get("low"), default[0])
        upper = _value_from_range(value.get("high"), default[1])
    elif isinstance(value, (list, tuple)) and len(value) >= 2:
        lower = _value_from_range(value[0], default[0])
        upper = _value_from_range(value[1], default[1])
    else:
        lower, upper = default
    if upper < lower:
        lower, upper = upper, lower
    return float(lower), float(upper)


def _window_duration_hours(start_hour: float, end_hour: float) -> float:
    start = float(start_hour) % 24.0
    end = float(end_hour) % 24.0
    if abs(start - end) < 1e-9:
        return 24.0
    if start < end:
        return max(end - start, 1e-9)
    return max((24.0 - start) + end, 1e-9)


def _hour_in_window(hour: float, start_hour: float, end_hour: float) -> bool:
    hour = float(hour) % 24.0
    start = float(start_hour) % 24.0
    end = float(end_hour) % 24.0
    if abs(start - end) < 1e-9:
        return True
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end


def _timing_windows(dhw_calibration: Mapping[str, Any] | None) -> tuple[dict[str, float], ...]:
    cfg = dict(dhw_calibration or {})
    configured = dict(cfg.get("timing_weights", DEFAULT_DHW_CALIBRATION["timing_weights"]))
    defaults = dict(DEFAULT_DHW_CALIBRATION["timing_weights"])
    windows: list[dict[str, float]] = []
    for name in ("morning", "daytime", "evening", "night"):
        default_window = dict(defaults[name])
        raw_window = configured.get(name, default_window)
        if isinstance(raw_window, Mapping):
            window = dict(raw_window)
            weight = _value_from_range(window.get("weight"), float(default_window["weight"]))
            start_hour = _value_from_range(window.get("start_hour"), float(default_window["start_hour"]))
            end_hour = _value_from_range(window.get("end_hour"), float(default_window["end_hour"]))
        else:
            weight = _value_from_range(raw_window, float(default_window["weight"]))
            start_hour = float(default_window["start_hour"])
            end_hour = float(default_window["end_hour"])
        windows.append(
            {
                "start_hour": float(start_hour),
                "end_hour": float(end_hour),
                "weight": max(float(weight), 0.0),
            }
        )
    total = sum(window["weight"] for window in windows)
    if total <= 0.0:
        return _timing_windows(DEFAULT_DHW_CALIBRATION)
    return tuple({**window, "weight": window["weight"] / total} for window in windows)


def _event_type_weights(dhw_calibration: Mapping[str, Any] | None) -> tuple[tuple[str, float], ...]:
    if not _calibration_enabled(dhw_calibration):
        return _DHW_EVENT_TYPE_WEIGHTS
    cfg = dict(dhw_calibration or {})
    configured = dict(cfg.get("event_type_probabilities", DEFAULT_DHW_CALIBRATION["event_type_probabilities"]))
    defaults = dict(DEFAULT_DHW_CALIBRATION["event_type_probabilities"])
    ordered_types = [event_type for event_type in defaults if event_type in configured]
    ordered_types.extend(event_type for event_type in configured if event_type not in ordered_types)
    weights: list[tuple[str, float]] = []
    for event_type in ordered_types:
        weight = _value_from_range(configured.get(event_type), defaults.get(event_type, 0.0))
        if weight > 0.0:
            weights.append((str(event_type), float(weight)))
    total = sum(weight for _, weight in weights)
    if total <= 0.0:
        return tuple((event_type, weight) for event_type, weight in DEFAULT_DHW_CALIBRATION["event_type_probabilities"].items())
    return tuple((event_type, weight / total) for event_type, weight in weights)


def _base_rate_for_timestamp(timestamp: pd.Timestamp, dhw_calibration: Mapping[str, Any] | None = None) -> float:
    """Return the base DHW event start rate in events per hour."""

    hour = float(timestamp.hour) + float(timestamp.minute) / 60.0
    if _calibration_enabled(dhw_calibration):
        daily_frequency = max(_calibration_value(dhw_calibration, "event_frequency_per_occupant_day", 2.0), 0.0)
        for window in _timing_windows(dhw_calibration):
            if _hour_in_window(hour, window["start_hour"], window["end_hour"]):
                duration = _window_duration_hours(window["start_hour"], window["end_hour"])
                return float(daily_frequency * window["weight"] / duration)
        return 0.0

    if 6.0 <= hour < 9.0:
        return 0.50
    if 18.0 <= hour < 22.0:
        return 0.28
    if 9.0 <= hour < 18.0:
        return 0.06
    if 5.0 <= hour < 6.0 or 22.0 <= hour < 23.0:
        return 0.02
    return 0.001


def _occupancy_factors(
    *,
    timestamps: list[pd.Timestamp],
    occupancy_spec: Mapping[str, Any],
    occupants_per_dwelling: float,
    occupancy_threshold: float,
    schedule_variation_seed: int,
    occupancy_time_shift_hours: float,
    transition_variability_scale: float,
    state_duration_scale: float,
    occupancy_state_biases: Mapping[str, float] | None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return occupancy gating and diagnostics on the internal event grid."""

    if not timestamps or not occupancy_spec:
        zeros = np.zeros(len(timestamps), dtype=float)
        return zeros, zeros, zeros

    profiles = _build_occupancy_profiles(dict(occupancy_spec))
    states = list(occupancy_spec["states"])
    dt_minutes = int(occupancy_spec["dt_minutes"])
    away_index = states.index("away") if "away" in states else 0
    bias_array = None
    if occupancy_state_biases:
        bias_array = np.asarray(
            [max(float(occupancy_state_biases.get(state, 1.0)), 1e-6) for state in states],
            dtype=float,
        )

    slot_probability_cache: dict[tuple[str, int], np.ndarray] = {}
    occupancy_factor = np.zeros(len(timestamps), dtype=float)
    occupied_probability = np.zeros(len(timestamps), dtype=float)
    expected_occupants = np.zeros(len(timestamps), dtype=float)
    shift_minutes = int(round(float(occupancy_time_shift_hours) * 60.0))
    seed_offset_minutes = int(schedule_variation_seed % max(dt_minutes, 1))
    n_slots = max(int(24 * 60 / dt_minutes), 1)

    for index, timestamp in enumerate(timestamps):
        shifted = timestamp + timedelta(minutes=shift_minutes + seed_offset_minutes)
        shifted_minutes = (shifted.hour * 60 + shifted.minute) % (24 * 60)
        effective_minutes = shifted_minutes / max(float(state_duration_scale), 1e-6)
        slot_index = int(effective_minutes // dt_minutes) % n_slots
        day_type = "weekend" if shifted.weekday() >= 5 else "weekday"
        cache_key = (day_type, slot_index)
        vector = slot_probability_cache.get(cache_key)
        if vector is None:
            vector = np.asarray(profiles[day_type][slot_index], dtype=float)
            if bias_array is not None:
                vector = np.power(
                    np.maximum(vector * bias_array, 1e-6),
                    max(float(transition_variability_scale), 1e-6),
                )
                vector = vector / max(float(vector.sum()), 1e-9)
            slot_probability_cache[cache_key] = vector

        occupied_prob = max(0.0, 1.0 - float(vector[away_index]))
        expected_occ = max(float(occupants_per_dwelling) * occupied_prob, 0.0)
        occupied_probability[index] = occupied_prob
        expected_occupants[index] = expected_occ
        if occupied_prob >= float(occupancy_threshold):
            occupancy_factor[index] = max(expected_occ, 1.0)

    return occupancy_factor, occupied_probability, expected_occupants


def _sample_event_type(
    *,
    rng: np.random.Generator,
    event_type_weights: tuple[tuple[str, float], ...],
) -> str:
    """Sample one DHW event type from the configured mixture."""

    event_types = [event_type for event_type, _ in event_type_weights]
    weights = [weight for _, weight in event_type_weights]
    return str(rng.choice(event_types, p=np.asarray(weights, dtype=float)))


def _sample_event_profile(
    event_type: str,
    rng: np.random.Generator,
    substep_seconds: int,
    intensity_scale: float,
) -> tuple[np.ndarray, dict[str, float]]:
    """Sample a discrete constant-power DHW event."""

    metadata: dict[str, float] = {}
    parameters = _DHW_EVENT_PARAMETERS[event_type]
    duration_minutes = float(rng.uniform(*parameters["duration_minutes"]))
    duration_steps = max(int(np.ceil(duration_minutes * 60.0 / max(substep_seconds, 1))), 1)
    power_w = float(rng.uniform(*parameters["power_w"])) * max(float(intensity_scale), 0.0)
    metadata["raw_energy_kWh"] = float(max(power_w, 0.0) * duration_steps * substep_seconds / 3_600_000.0)
    return np.full(duration_steps, max(power_w, 0.0), dtype=float), metadata


def _sample_calibrated_event_profile(
    event_type: str,
    rng: np.random.Generator,
    substep_seconds: int,
    dhw_calibration: Mapping[str, Any] | None,
) -> tuple[np.ndarray, dict[str, float]]:
    """Sample a volume-derived DHW event before annual-energy calibration."""

    cfg = dict(dhw_calibration or {})
    default_volumes = dict(DEFAULT_DHW_CALIBRATION["event_volume_liters"])
    default_durations = dict(DEFAULT_DHW_CALIBRATION["event_duration_minutes"])
    volume_cfg = dict(cfg.get("event_volume_liters", default_volumes))
    duration_cfg = dict(cfg.get("event_duration_minutes", default_durations))
    volume_liters = float(rng.uniform(*_range_pair(volume_cfg.get(event_type), _range_pair(default_volumes.get(event_type), (1.0, 3.0)))))
    duration_minutes = float(
        rng.uniform(*_range_pair(duration_cfg.get(event_type), _range_pair(default_durations.get(event_type), (1.0, 3.0))))
    )
    duration_steps = max(int(np.ceil(duration_minutes * 60.0 / max(substep_seconds, 1))), 1)
    cold_c = _calibration_value(dhw_calibration, "cold_water_temperature_C", 10.0)
    draw_c = _calibration_value(dhw_calibration, "draw_temperature_C", 42.0)
    delta_t = max(draw_c - cold_c, 0.0)
    raw_energy_kwh = max(volume_liters, 0.0) * _KWH_PER_LITER_K * delta_t
    duration_seconds = max(duration_steps * substep_seconds, 1)
    power_w = raw_energy_kwh * 3_600_000.0 / duration_seconds
    return (
        np.full(duration_steps, max(power_w, 0.0), dtype=float),
        {
            "volume_liters": float(max(volume_liters, 0.0)),
            "raw_energy_kWh": float(raw_energy_kwh),
        },
    )


def _inject_fallback_event(
    *,
    timestamps: list[pd.Timestamp],
    occupancy_factor: np.ndarray,
    base_rate: np.ndarray,
    component_profiles_substeps: dict[str, np.ndarray],
    event_log: list[dict[str, Any]],
    rng: np.random.Generator,
    substep_seconds: int,
    intensity_scale: float,
    dhw_calibration: Mapping[str, Any] | None,
    event_type_weights: tuple[tuple[str, float], ...],
) -> None:
    """Guarantee at least one occupied DHW event when the horizon is active."""

    candidate_indices = np.flatnonzero(np.asarray(occupancy_factor, dtype=float) > 0.0)
    if candidate_indices.size == 0:
        return
    weighted_base_rate = np.asarray(base_rate, dtype=float)[candidate_indices]
    selected_local = int(np.argmax(weighted_base_rate))
    start_index = int(candidate_indices[selected_local])
    available_types = {event_type for event_type, _ in event_type_weights}
    preferred_type = "shower" if timestamps[start_index].hour in (6, 7, 8, 18, 19, 20, 21) else "sink"
    event_type = preferred_type if preferred_type in available_types else event_type_weights[0][0]
    if _calibration_enabled(dhw_calibration):
        profile, event_metadata = _sample_calibrated_event_profile(
            event_type=event_type,
            rng=rng,
            substep_seconds=substep_seconds,
            dhw_calibration=dhw_calibration,
        )
    else:
        profile, event_metadata = _sample_event_profile(
            event_type=event_type,
            rng=rng,
            substep_seconds=substep_seconds,
            intensity_scale=intensity_scale,
        )
    stop_index = min(len(timestamps), start_index + len(profile))
    applied_profile = np.clip(profile[: max(stop_index - start_index, 0)], 0.0, None)
    if applied_profile.size <= 0:
        return
    component_profiles_substeps[event_type][start_index:stop_index] += applied_profile
    raw_energy_kwh = float(applied_profile.sum() * substep_seconds / 3_600_000.0)
    event_log.append(
        {
            "event_type": event_type,
            "start_timestamp": timestamps[start_index].isoformat(),
            "end_timestamp": timestamps[stop_index - 1].isoformat(),
            "duration_minutes": float(applied_profile.size * substep_seconds / 60.0),
            "mean_power_W": float(applied_profile.mean()),
            "peak_power_W": float(applied_profile.max()),
            "energy_kWh": raw_energy_kwh,
            "raw_energy_kWh": float(event_metadata.get("raw_energy_kWh", raw_energy_kwh)),
            "volume_liters": event_metadata.get("volume_liters"),
            "base_rate_events_per_hour": float(base_rate[start_index]),
            "fallback_injected": True,
        }
    )


def generate_dhw_events(
    *,
    timestamps: tuple[Any, ...],
    target_resolution_seconds: int,
    occupancy_spec: Mapping[str, Any],
    occupants_per_dwelling: float,
    occupancy_threshold: float,
    schedule_variation_seed: int,
    occupancy_time_shift_hours: float,
    transition_variability_scale: float,
    state_duration_scale: float,
    occupancy_state_biases: Mapping[str, float] | None,
    household_class: HouseholdBehaviourClass,
    household_random_effect_u: float,
    rng: np.random.Generator,
    event_frequency_scale: float = 1.0,
    event_intensity_scale: float = 1.0,
    dhw_calibration: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Generate occupancy-driven stochastic DHW demand."""

    calibrated = _calibration_enabled(dhw_calibration)
    event_type_weights = _event_type_weights(dhw_calibration)
    if not timestamps:
        empty_profile = tuple()
        return {
            "output_load_W": empty_profile,
            "component_output_loads_W": {event_type: empty_profile for event_type, _ in event_type_weights},
            "event_summary": {
                "total_event_count": 0,
                "event_count_by_type": {},
                "peak_dhw_load_W": 0.0,
                "aggregate_dhw_energy_kWh": 0.0,
                "active_occupancy_fraction": 0.0,
                "dhw_calibration_enabled": calibrated,
            },
            "event_log": [],
            "occupancy_active_W": empty_profile,
            "occupied_probability": empty_profile,
            "expected_occupants": empty_profile,
            "substep_seconds": _effective_substep_seconds(target_resolution_seconds),
        }

    substep_seconds = _effective_substep_seconds(target_resolution_seconds)
    substep_timestamps, substeps_per_step = _build_substep_timestamps(
        timestamps=timestamps,
        target_resolution_seconds=target_resolution_seconds,
        substep_seconds=substep_seconds,
    )
    n_output_steps = len(timestamps)
    n_substeps = len(substep_timestamps)
    dt_hours = float(substep_seconds) / 3600.0
    random_effect_scale = float(np.exp(float(household_random_effect_u)))
    class_multiplier = float(_DHW_CLASS_MULTIPLIERS.get(household_class.name, 1.0))
    frequency_scale = max(float(event_frequency_scale), 0.0)
    intensity_scale = max(float(event_intensity_scale), 0.0)

    occupancy_factor, occupied_probability, expected_occupants = _occupancy_factors(
        timestamps=substep_timestamps,
        occupancy_spec=occupancy_spec,
        occupants_per_dwelling=occupants_per_dwelling,
        occupancy_threshold=occupancy_threshold,
        schedule_variation_seed=schedule_variation_seed,
        occupancy_time_shift_hours=occupancy_time_shift_hours,
        transition_variability_scale=transition_variability_scale,
        state_duration_scale=state_duration_scale,
        occupancy_state_biases=occupancy_state_biases,
    )
    base_rate = np.asarray(
        [_base_rate_for_timestamp(timestamp, dhw_calibration=dhw_calibration) for timestamp in substep_timestamps],
        dtype=float,
    )
    event_rate = base_rate * occupancy_factor * class_multiplier * random_effect_scale * frequency_scale
    start_probability = np.clip(event_rate * dt_hours, 0.0, 0.95)

    component_profiles_substeps = {
        event_type: np.zeros(n_substeps, dtype=float)
        for event_type, _ in event_type_weights
    }
    event_log: list[dict[str, Any]] = []

    for substep_index, timestamp in enumerate(substep_timestamps):
        if occupancy_factor[substep_index] <= 0.0:
            continue
        if float(rng.random()) >= float(start_probability[substep_index]):
            continue

        event_type = _sample_event_type(rng=rng, event_type_weights=event_type_weights)
        if calibrated:
            profile, event_metadata = _sample_calibrated_event_profile(
                event_type=event_type,
                rng=rng,
                substep_seconds=substep_seconds,
                dhw_calibration=dhw_calibration,
            )
        else:
            profile, event_metadata = _sample_event_profile(
                event_type=event_type,
                rng=rng,
                substep_seconds=substep_seconds,
                intensity_scale=intensity_scale,
            )
        stop_index = min(n_substeps, substep_index + len(profile))
        applied_profile = np.clip(profile[: max(stop_index - substep_index, 0)], 0.0, None)
        if applied_profile.size <= 0:
            continue

        component_profiles_substeps[event_type][substep_index:stop_index] += applied_profile
        raw_energy_kwh = float(applied_profile.sum() * substep_seconds / 3_600_000.0)
        event_log.append(
            {
                "event_type": event_type,
                "start_timestamp": timestamp.isoformat(),
                "end_timestamp": substep_timestamps[stop_index - 1].isoformat(),
                "duration_minutes": float(applied_profile.size * substep_seconds / 60.0),
                "mean_power_W": float(applied_profile.mean()),
                "peak_power_W": float(applied_profile.max()),
                "energy_kWh": raw_energy_kwh,
                "raw_energy_kWh": float(event_metadata.get("raw_energy_kWh", raw_energy_kwh)),
                "volume_liters": event_metadata.get("volume_liters"),
                "base_rate_events_per_hour": float(base_rate[substep_index]),
                "occupancy_factor": float(occupancy_factor[substep_index]),
                "fallback_injected": False,
            }
        )

    if not event_log:
        _inject_fallback_event(
            timestamps=substep_timestamps,
            occupancy_factor=occupancy_factor,
            base_rate=base_rate,
            component_profiles_substeps=component_profiles_substeps,
            event_log=event_log,
            rng=rng,
            substep_seconds=substep_seconds,
            intensity_scale=max(intensity_scale, 1.0),
            dhw_calibration=dhw_calibration,
            event_type_weights=event_type_weights,
        )

    total_substep_profile = np.zeros(n_substeps, dtype=float)
    for profile_substeps in component_profiles_substeps.values():
        total_substep_profile += profile_substeps
    raw_total_energy_kwh = float(total_substep_profile.sum() * substep_seconds / 3_600_000.0)
    target_energy_kwh: float | None = None
    calibration_scale = 1.0
    if calibrated:
        period_days = float(n_substeps * substep_seconds / 86_400.0)
        daily_useful_kwh_per_person = max(_calibration_value(dhw_calibration, "daily_useful_kWh_per_person", 1.2), 0.0)
        target_energy_kwh = (
            daily_useful_kwh_per_person
            * max(float(occupants_per_dwelling), 0.0)
            * max(period_days, 0.0)
            * max(float(event_intensity_scale), 0.0)
        )
        if raw_total_energy_kwh > 1e-12:
            calibration_scale = target_energy_kwh / raw_total_energy_kwh
        elif target_energy_kwh <= 0.0:
            calibration_scale = 0.0
        for event_type in component_profiles_substeps:
            component_profiles_substeps[event_type] = component_profiles_substeps[event_type] * calibration_scale
        total_substep_profile = total_substep_profile * calibration_scale
        for event in event_log:
            raw_energy = float(event.get("raw_energy_kWh", event.get("energy_kWh", 0.0)) or 0.0)
            event["raw_energy_kWh"] = raw_energy
            event["calibrated_energy_kWh"] = float(raw_energy * calibration_scale)
            event["calibration_scale"] = float(calibration_scale)
            event["energy_kWh"] = float(raw_energy * calibration_scale)
            event["mean_power_W"] = float(event.get("mean_power_W", 0.0) or 0.0) * calibration_scale
            event["peak_power_W"] = float(event.get("peak_power_W", 0.0) or 0.0) * calibration_scale

    component_output_loads: dict[str, tuple[float, ...]] = {}
    for event_type, profile_substeps in component_profiles_substeps.items():
        output_profile = _aggregate_substeps_to_output(
            values=profile_substeps,
            n_steps=n_output_steps,
            substeps_per_step=substeps_per_step,
        )
        component_output_loads[event_type] = tuple(float(value) for value in np.clip(output_profile, 0.0, None).tolist())

    output_profile = _aggregate_substeps_to_output(
        values=total_substep_profile,
        n_steps=n_output_steps,
        substeps_per_step=substeps_per_step,
    )
    occupancy_active_output = _aggregate_substeps_to_output(
        values=(occupancy_factor > 0.0).astype(float),
        n_steps=n_output_steps,
        substeps_per_step=substeps_per_step,
    )
    occupied_probability_output = _aggregate_substeps_to_output(
        values=occupied_probability,
        n_steps=n_output_steps,
        substeps_per_step=substeps_per_step,
    )
    expected_occupants_output = _aggregate_substeps_to_output(
        values=expected_occupants,
        n_steps=n_output_steps,
        substeps_per_step=substeps_per_step,
    )

    event_count_by_type: dict[str, int] = {}
    for event in event_log:
        event_type = str(event["event_type"])
        event_count_by_type[event_type] = event_count_by_type.get(event_type, 0) + 1

    return {
        "output_load_W": tuple(float(value) for value in np.clip(output_profile, 0.0, None).tolist()),
        "component_output_loads_W": component_output_loads,
        "event_summary": {
            "total_event_count": int(len(event_log)),
            "event_count_by_type": event_count_by_type,
            "peak_dhw_load_W": float(np.max(output_profile)) if len(output_profile) else 0.0,
            "aggregate_dhw_energy_kWh": float(total_substep_profile.sum() * substep_seconds / 3_600_000.0),
            "raw_aggregate_dhw_energy_kWh": raw_total_energy_kwh,
            "target_dhw_energy_kWh": target_energy_kwh,
            "calibration_scale": float(calibration_scale),
            "dhw_calibration_enabled": calibrated,
            "daily_useful_kWh_per_person": _calibration_value(dhw_calibration, "daily_useful_kWh_per_person", 1.2)
            if calibrated
            else None,
            "event_frequency_per_occupant_day": _calibration_value(dhw_calibration, "event_frequency_per_occupant_day", 2.0)
            if calibrated
            else None,
            "equivalent_litres_per_person_day_42C": _calibration_value(
                dhw_calibration,
                "equivalent_litres_per_person_day_42C",
                32.0,
            )
            if calibrated
            else None,
            "cold_water_temperature_C": _calibration_value(dhw_calibration, "cold_water_temperature_C", 10.0)
            if calibrated
            else None,
            "draw_temperature_C": _calibration_value(dhw_calibration, "draw_temperature_C", 42.0)
            if calibrated
            else None,
            "active_occupancy_fraction": float(np.mean((occupancy_factor > 0.0).astype(float))) if len(occupancy_factor) else 0.0,
            "substep_seconds": int(substep_seconds),
            "class_multiplier": class_multiplier,
            "random_effect_scale": random_effect_scale,
        },
        "event_log": event_log,
        "occupancy_active_W": tuple(float(value) for value in occupancy_active_output.tolist()),
        "occupied_probability": tuple(float(value) for value in occupied_probability_output.tolist()),
        "expected_occupants": tuple(float(value) for value in expected_occupants_output.tolist()),
        "substep_seconds": int(substep_seconds),
    }
