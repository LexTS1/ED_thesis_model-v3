"""Household-level stochastic transformations for end-use profiles."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping

import numpy as np
import pandas as pd

from model_v3.interfaces import TimeSeriesData
from model_v3.utils.energy import infer_step_durations_seconds, integrate_power_series_kwh


def _day_slices(timestamps: tuple[Any, ...]) -> list[slice]:
    """Return contiguous slices for each calendar day."""

    if not timestamps:
        return []

    normalized = [pd.Timestamp(timestamp) for timestamp in timestamps]
    slices: list[slice] = []
    start_index = 0
    current_date = normalized[0].date()
    for index, timestamp in enumerate(normalized[1:], start=1):
        if timestamp.date() != current_date:
            slices.append(slice(start_index, index))
            start_index = index
            current_date = timestamp.date()
    slices.append(slice(start_index, len(normalized)))
    return slices


def _representative_step_hours(timestamps: tuple[Any, ...]) -> float:
    """Infer a representative timestep in hours."""

    durations = [duration for duration in infer_step_durations_seconds(timestamps) if duration > 0.0]
    if not durations:
        return 1.0
    return float(np.median(np.asarray(durations, dtype=float))) / 3600.0


def _renormalize_power(values: np.ndarray, timestamps: tuple[Any, ...], target_energy_kwh: float) -> np.ndarray:
    """Rescale a power series so its integrated energy matches the target."""

    clipped = np.clip(np.asarray(values, dtype=float), 0.0, None)
    actual_energy_kwh = integrate_power_series_kwh(clipped.tolist(), timestamps=timestamps)
    if target_energy_kwh <= 1e-9 or actual_energy_kwh <= 1e-9:
        return np.zeros(len(clipped), dtype=float)
    return clipped * (float(target_energy_kwh) / actual_energy_kwh)


def _shift_daily_profile(block: np.ndarray, shift_steps: int) -> np.ndarray:
    """Roll a single-day block while keeping the same day boundary."""

    if len(block) == 0 or shift_steps == 0:
        return block.copy()
    return np.roll(block, int(shift_steps))


def _event_daily_profile(
    day_values: np.ndarray,
    day_timestamps: tuple[Any, ...],
    rng: np.random.Generator,
    jitter_sigma_steps: float,
    duty_range: tuple[float, float],
    hf_sigma: float,
    event_lambda: float,
    volume_sigma: float,
    weight_exponent: float,
    max_duration_steps: int,
) -> np.ndarray:
    """Create an event-driven daily profile while preserving daily energy."""

    target_energy_kwh = integrate_power_series_kwh(day_values.tolist(), timestamps=day_timestamps)
    if target_energy_kwh <= 1e-9:
        return np.zeros(len(day_values), dtype=float)

    base = np.clip(np.asarray(day_values, dtype=float), 0.0, None)
    weights = np.power(np.maximum(base, 1e-6), max(weight_exponent, 1.0))
    if float(weights.sum()) <= 1e-9:
        weights = np.ones(len(base), dtype=float)
    probabilities = weights / weights.sum()

    event_count = max(1, int(rng.poisson(max(event_lambda, 0.5))))
    profile = np.zeros(len(base), dtype=float)
    max_duration = max(1, min(int(max_duration_steps), len(base)))
    for _ in range(event_count):
        center = int(rng.choice(len(base), p=probabilities))
        center = int(np.clip(center + int(np.round(rng.normal(0.0, jitter_sigma_steps))), 0, len(base) - 1))
        duration = int(np.clip(np.round(rng.lognormal(mean=0.0, sigma=0.35) + 0.5), 1, max_duration))
        start = max(0, center - duration // 2)
        stop = min(len(base), start + duration)
        profile[start:stop] += float(rng.lognormal(mean=0.0, sigma=volume_sigma)) * float(rng.uniform(*duty_range))

    profile *= np.clip(1.0 + rng.normal(0.0, hf_sigma, size=len(profile)), 0.0, None)
    if float(profile.sum()) <= 1e-9:
        profile = np.ones(len(base), dtype=float)
    return _renormalize_power(profile, timestamps=day_timestamps, target_energy_kwh=target_energy_kwh)


def apply_household_load_stochasticity(
    dataset: TimeSeriesData,
    behaviour: Mapping[str, Any],
    seed: int,
) -> tuple[TimeSeriesData, dict[str, Any]]:
    """Apply household-level timing and duty-cycle stochasticity to end-use inputs."""

    rng = np.random.default_rng(int(seed))
    timestamps = tuple(dataset.timestamps)
    step_hours = _representative_step_hours(timestamps)
    global_shift_steps = int(np.round(0.5 * float(behaviour.get("occupancy_time_shift_hours", 0.0)) / max(step_hours, 1e-9)))
    day_slices = _day_slices(timestamps)

    generic_config = {
        "jitter_sigma_steps": max(float(behaviour.get("appliance_start_jitter_hours", 1.0)) / max(step_hours, 1e-9), 0.0),
        "duty_range": (0.7, 1.3),
        "hf_sigma": float(behaviour.get("high_frequency_sigma", 0.08)),
    }
    dhw_jitter_sigma_steps = max(float(behaviour.get("dhw_start_jitter_hours", 0.75)) / max(step_hours, 1e-9), 0.0)
    event_config = {
        "appliances": {"event_lambda": 6.0, "volume_sigma": 0.55, "weight_exponent": 2.2, "max_duration_steps": 4},
        "lighting": {"event_lambda": 4.0, "volume_sigma": 0.45, "weight_exponent": 2.5, "max_duration_steps": 3},
        "cooking": {"event_lambda": 2.5, "volume_sigma": 0.40, "weight_exponent": 3.0, "max_duration_steps": 2},
        "dhw": {
            "event_lambda": float(behaviour.get("dhw_event_lambda", 3.0)) * max(float(behaviour.get("dhw_event_frequency_scale", 1.0)), 0.2),
            "volume_sigma": float(behaviour.get("dhw_event_volume_sigma", 0.4)),
            "weight_exponent": 3.0,
            "max_duration_steps": 2,
        },
    }

    transformed_columns: dict[str, tuple[float | None, ...]] = {}
    diagnostics: dict[str, Any] = {"annual_energy_kwh": {}}
    for column_name, raw_values in dataset.columns.items():
        base_values = np.asarray([0.0 if value is None else float(value) for value in raw_values], dtype=float)
        shifted_values = np.roll(base_values, global_shift_steps) if len(base_values) else base_values.copy()
        transformed = np.zeros(len(shifted_values), dtype=float)

        for day_slice in day_slices:
            day_values = shifted_values[day_slice]
            day_timestamps = timestamps[day_slice]
            column_event_config = event_config.get(column_name, event_config["appliances"])
            intensity_scale = (
                max(float(behaviour.get("dhw_intensity_scale", 1.0)), 0.8)
                if column_name == "dhw"
                else max(float(behaviour.get("appliance_intensity_scale", 1.0)), 0.8)
            )
            transformed[day_slice] = _event_daily_profile(
                day_values=day_values,
                day_timestamps=day_timestamps,
                rng=rng,
                jitter_sigma_steps=dhw_jitter_sigma_steps if column_name == "dhw" else generic_config["jitter_sigma_steps"],
                duty_range=generic_config["duty_range"],
                hf_sigma=generic_config["hf_sigma"],
                event_lambda=float(column_event_config["event_lambda"]),
                volume_sigma=float(column_event_config["volume_sigma"]),
                weight_exponent=float(column_event_config["weight_exponent"])
                * max(float(behaviour.get("transition_variability_scale", 1.0)), 1.0)
                * intensity_scale,
                max_duration_steps=int(column_event_config["max_duration_steps"]),
            )

        target_energy_kwh = integrate_power_series_kwh(base_values.tolist(), timestamps=timestamps)
        transformed = _renormalize_power(transformed, timestamps=timestamps, target_energy_kwh=target_energy_kwh)
        transformed_columns[column_name] = tuple(float(value) for value in transformed.tolist())
        diagnostics["annual_energy_kwh"][column_name] = target_energy_kwh

    metadata = dict(dataset.metadata)
    metadata["stochastic_household_seed"] = int(seed)
    metadata["stochastic_global_shift_steps"] = int(global_shift_steps)
    return replace(dataset, columns=transformed_columns, metadata=metadata), diagnostics
