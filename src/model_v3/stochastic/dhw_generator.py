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


def _base_rate_for_timestamp(timestamp: pd.Timestamp) -> float:
    """Return the base DHW event start rate in events per hour."""

    hour = float(timestamp.hour) + float(timestamp.minute) / 60.0
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


def _sample_event_type(rng: np.random.Generator) -> str:
    """Sample one DHW event type from the configured mixture."""

    event_types = [event_type for event_type, _ in _DHW_EVENT_TYPE_WEIGHTS]
    weights = [weight for _, weight in _DHW_EVENT_TYPE_WEIGHTS]
    return str(rng.choice(event_types, p=np.asarray(weights, dtype=float)))


def _sample_event_profile(
    event_type: str,
    rng: np.random.Generator,
    substep_seconds: int,
    intensity_scale: float,
) -> np.ndarray:
    """Sample a discrete constant-power DHW event."""

    parameters = _DHW_EVENT_PARAMETERS[event_type]
    duration_minutes = float(rng.uniform(*parameters["duration_minutes"]))
    duration_steps = max(int(np.ceil(duration_minutes * 60.0 / max(substep_seconds, 1))), 1)
    power_w = float(rng.uniform(*parameters["power_w"])) * max(float(intensity_scale), 0.0)
    return np.full(duration_steps, max(power_w, 0.0), dtype=float)


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
) -> None:
    """Guarantee at least one occupied DHW event when the horizon is active."""

    candidate_indices = np.flatnonzero(np.asarray(occupancy_factor, dtype=float) > 0.0)
    if candidate_indices.size == 0:
        return
    weighted_base_rate = np.asarray(base_rate, dtype=float)[candidate_indices]
    selected_local = int(np.argmax(weighted_base_rate))
    start_index = int(candidate_indices[selected_local])
    event_type = "sink" if timestamps[start_index].hour not in (6, 7, 8, 18, 19, 20, 21) else "shower"
    profile = _sample_event_profile(
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
    event_log.append(
        {
            "event_type": event_type,
            "start_timestamp": timestamps[start_index].isoformat(),
            "end_timestamp": timestamps[stop_index - 1].isoformat(),
            "duration_minutes": float(applied_profile.size * substep_seconds / 60.0),
            "mean_power_W": float(applied_profile.mean()),
            "peak_power_W": float(applied_profile.max()),
            "energy_kWh": float(applied_profile.sum() * substep_seconds / 3_600_000.0),
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
) -> dict[str, Any]:
    """Generate occupancy-driven stochastic DHW demand."""

    if not timestamps:
        empty_profile = tuple()
        return {
            "output_load_W": empty_profile,
            "component_output_loads_W": {event_type: empty_profile for event_type, _ in _DHW_EVENT_TYPE_WEIGHTS},
            "event_summary": {
                "total_event_count": 0,
                "event_count_by_type": {},
                "peak_dhw_load_W": 0.0,
                "aggregate_dhw_energy_kWh": 0.0,
                "active_occupancy_fraction": 0.0,
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
    base_rate = np.asarray([_base_rate_for_timestamp(timestamp) for timestamp in substep_timestamps], dtype=float)
    event_rate = base_rate * occupancy_factor * class_multiplier * random_effect_scale * frequency_scale
    start_probability = np.clip(event_rate * dt_hours, 0.0, 0.95)

    component_profiles_substeps = {
        event_type: np.zeros(n_substeps, dtype=float)
        for event_type, _ in _DHW_EVENT_TYPE_WEIGHTS
    }
    event_log: list[dict[str, Any]] = []

    for substep_index, timestamp in enumerate(substep_timestamps):
        if occupancy_factor[substep_index] <= 0.0:
            continue
        if float(rng.random()) >= float(start_probability[substep_index]):
            continue

        event_type = _sample_event_type(rng=rng)
        profile = _sample_event_profile(
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
        event_log.append(
            {
                "event_type": event_type,
                "start_timestamp": timestamp.isoformat(),
                "end_timestamp": substep_timestamps[stop_index - 1].isoformat(),
                "duration_minutes": float(applied_profile.size * substep_seconds / 60.0),
                "mean_power_W": float(applied_profile.mean()),
                "peak_power_W": float(applied_profile.max()),
                "energy_kWh": float(applied_profile.sum() * substep_seconds / 3_600_000.0),
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
        )

    total_substep_profile = np.zeros(n_substeps, dtype=float)
    component_output_loads: dict[str, tuple[float, ...]] = {}
    for event_type, profile_substeps in component_profiles_substeps.items():
        total_substep_profile += profile_substeps
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
