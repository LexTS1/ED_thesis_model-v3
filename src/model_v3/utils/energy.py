"""Energy conversion helpers with explicit timestep handling."""

from __future__ import annotations

from typing import Any, Iterable, Sequence

import pandas as pd


def power_to_energy_kwh(power_W: float, dt_seconds: float) -> float:
    """Convert a power level sustained over a timestep into energy."""

    return float(power_W) * max(float(dt_seconds), 0.0) / 3600.0 / 1000.0


def infer_step_durations_seconds(timestamps: Sequence[Any] | pd.Index | pd.Series) -> list[float]:
    """Infer one timestep duration per timestamp from the explicit time index."""

    if len(timestamps) == 0:
        return []

    normalized = [pd.Timestamp(timestamp) for timestamp in timestamps]
    if len(normalized) == 1:
        return [0.0]

    deltas = [max((current - previous).total_seconds(), 0.0) for previous, current in zip(normalized[:-1], normalized[1:])]
    fallback_dt = next((delta for delta in deltas if delta > 0.0), 0.0)
    return [fallback_dt] + [delta if delta > 0.0 else fallback_dt for delta in deltas]


def integrate_power_series_kwh(
    power_values: Iterable[Any],
    timestamps: Sequence[Any] | pd.Index | pd.Series | None = None,
    dt_seconds: float | None = None,
) -> float:
    """Integrate a power series into kWh using either timestamps or an explicit timestep."""

    values = [0.0 if value is None else float(value) for value in power_values]
    if timestamps is not None:
        step_durations = infer_step_durations_seconds(timestamps)
    elif dt_seconds is not None:
        step_durations = [max(float(dt_seconds), 0.0) for _ in values]
    else:
        raise ValueError("integrate_power_series_kwh requires timestamps or dt_seconds.")

    if len(step_durations) != len(values):
        raise ValueError("Power values and timestep durations must have the same length.")
    return float(sum(power_to_energy_kwh(power_w, duration_s) for power_w, duration_s in zip(values, step_durations)))


def power_series_to_energy_kwh(series: pd.Series) -> pd.Series:
    """Convert an indexed power series into per-timestep energy in kWh."""

    durations = infer_step_durations_seconds(series.index)
    return pd.Series(
        [power_to_energy_kwh(power_w, duration_s) for power_w, duration_s in zip(series.to_numpy(dtype=float), durations)],
        index=series.index,
        dtype=float,
    )
