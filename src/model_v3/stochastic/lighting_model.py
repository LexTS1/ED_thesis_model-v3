"""Household lighting demand driven by occupancy and daylight."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def _clean_series(values: tuple[float | None, ...], n_steps: int) -> np.ndarray:
    """Coerce possibly sparse inputs to a numeric vector."""

    series = np.asarray([0.0 if value is None else float(value) for value in values], dtype=float)
    if len(series) == n_steps:
        return np.nan_to_num(series, nan=0.0, posinf=0.0, neginf=0.0)
    padded = np.zeros(n_steps, dtype=float)
    padded[: min(len(series), n_steps)] = series[: min(len(series), n_steps)]
    return np.nan_to_num(padded, nan=0.0, posinf=0.0, neginf=0.0)


def generate_lighting_profile(
    *,
    timestamps: tuple[Any, ...],
    base_lighting_w: tuple[float | None, ...],
    occupancy: tuple[float | None, ...],
    ghi_wm2: tuple[float | None, ...],
    household_class: str,
) -> dict[str, tuple[float, ...] | float]:
    """Generate a lighting profile from occupancy and daylight conditions."""

    n_steps = len(timestamps)
    if n_steps <= 0:
        return {
            "profile_w": tuple(),
            "daylight_factor": tuple(),
            "occupancy_factor": tuple(),
            "time_weight": tuple(),
            "class_scale": 1.0,
        }

    base = np.clip(_clean_series(base_lighting_w, n_steps=n_steps), 0.0, None)
    occupancy_factor = np.clip(_clean_series(occupancy, n_steps=n_steps), 0.0, 1.0)
    ghi = np.clip(_clean_series(ghi_wm2, n_steps=n_steps), 0.0, None)
    daylight_factor = np.clip(1.0 - (ghi / 800.0), 0.0, 1.0)

    resolved_class = str(household_class).strip().lower()
    time_weight = np.ones(n_steps, dtype=float)
    class_scale = np.ones(n_steps, dtype=float)
    for index, timestamp in enumerate(timestamps):
        hour = int(pd.Timestamp(timestamp).hour)
        if 17 <= hour < 23:
            time_weight[index] = 1.35
        elif 8 <= hour < 17:
            time_weight[index] = 0.60
        else:
            time_weight[index] = 0.90

        if resolved_class == "low_flat":
            class_scale[index] *= 0.80
        elif resolved_class == "daytime_home" and 8 <= hour < 17:
            class_scale[index] *= 1.25

    profile = np.clip(base * occupancy_factor * daylight_factor * time_weight * class_scale, 0.0, None)
    return {
        "profile_w": tuple(float(value) for value in profile.tolist()),
        "daylight_factor": tuple(float(value) for value in daylight_factor.tolist()),
        "occupancy_factor": tuple(float(value) for value in occupancy_factor.tolist()),
        "time_weight": tuple(float(value) for value in time_weight.tolist()),
        "class_scale": float(np.mean(class_scale)) if class_scale.size else 1.0,
    }
