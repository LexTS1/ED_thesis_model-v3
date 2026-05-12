"""Temporal-structure metrics for validation."""

from __future__ import annotations

from typing import Iterable

import numpy as np
import pandas as pd
from scipy import stats


def _autocorrelation(values: np.ndarray, lag: int) -> float:
    """Compute simple autocorrelation at a fixed lag."""

    if lag >= len(values):
        return 0.0
    head = values[:-lag]
    tail = values[lag:]
    if np.std(head) < 1e-9 or np.std(tail) < 1e-9:
        return 0.0
    return float(np.corrcoef(head, tail)[0, 1])


def compute_temporal_metrics(y_model: pd.Series, y_data: pd.Series) -> dict[str, float]:
    """Compare correlation structure and peak timing."""

    model_series = pd.Series(pd.to_numeric(y_model, errors="coerce"), index=y_model.index)
    data_series = pd.Series(pd.to_numeric(y_data, errors="coerce"), index=y_data.index)
    finite_mask = np.isfinite(model_series.to_numpy(dtype=float)) & np.isfinite(data_series.to_numpy(dtype=float))
    model_series = model_series.loc[finite_mask]
    data_series = data_series.loc[finite_mask]
    model = model_series.to_numpy(dtype=float)
    data = data_series.to_numpy(dtype=float)
    if len(model) != len(data):
        raise ValueError("Temporal metrics require aligned series of equal length.")
    if len(model) == 0:
        return {
            "Pearson_correlation": 0.0,
            "autocorrelation_difference_mean": 0.0,
            "autocorrelation_difference_max": 0.0,
            "peak_timing_error_hours": 0.0,
        }

    if len(model) > 1 and np.std(model) >= 1e-9 and np.std(data) >= 1e-9:
        pearson_correlation = float(stats.pearsonr(model, data).statistic)
    else:
        pearson_correlation = 0.0
    autocorrelation_differences = []
    for lag in range(1, min(24, len(model) - 1) + 1):
        autocorrelation_differences.append(abs(_autocorrelation(model, lag) - _autocorrelation(data, lag)))

    peak_timing_error = abs(
        int(pd.to_datetime(model_series.idxmax()).hour) - int(pd.to_datetime(data_series.idxmax()).hour)
    )

    return {
        "Pearson_correlation": pearson_correlation,
        "autocorrelation_difference_mean": float(np.mean(autocorrelation_differences)) if autocorrelation_differences else 0.0,
        "autocorrelation_difference_max": float(np.max(autocorrelation_differences)) if autocorrelation_differences else 0.0,
        "peak_timing_error_hours": float(peak_timing_error),
    }


def compute_diversity_factor(individual_profiles: Iterable[np.ndarray | pd.Series | float], aggregate_profile: np.ndarray | pd.Series) -> float:
    """Compute diversity factor from individual and aggregate profiles."""

    individual_peaks = []
    for profile in individual_profiles:
        if isinstance(profile, (int, float)):
            individual_peaks.append(float(profile))
        else:
            individual_peaks.append(float(np.max(np.asarray(profile, dtype=float))))

    aggregate_peak = float(np.max(np.asarray(aggregate_profile, dtype=float)))
    if aggregate_peak <= 0.0:
        return 0.0
    return float(sum(individual_peaks) / aggregate_peak)
