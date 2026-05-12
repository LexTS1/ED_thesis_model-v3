"""Variance realism metrics for validation."""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats


def compute_variance_metrics(y_model: np.ndarray, y_data: np.ndarray) -> dict[str, float]:
    """Compare variance structure between modelled and observed loads."""

    model = np.asarray(y_model, dtype=float)
    data = np.asarray(y_data, dtype=float)
    finite_mask = np.isfinite(model) & np.isfinite(data)
    model = model[finite_mask]
    data = data[finite_mask]
    if len(model) == 0:
        return {
            "variance_model": 0.0,
            "variance_data": 0.0,
            "CV_model": 0.0,
            "CV_data": 0.0,
            "Levene_statistic": 0.0,
            "Levene_p_value": 1.0,
        }
    if len(model) > 1 and len(data) > 1 and (np.std(model) >= 1e-9 or np.std(data) >= 1e-9):
        levene_statistic, levene_p_value = stats.levene(model, data, center="median")
    else:
        levene_statistic, levene_p_value = 0.0, 1.0
    mean_model = float(np.mean(model))
    mean_data = float(np.mean(data))
    variance_model = float(np.var(model, ddof=1)) if len(model) > 1 else 0.0
    variance_data = float(np.var(data, ddof=1)) if len(data) > 1 else 0.0

    return {
        "variance_model": variance_model,
        "variance_data": variance_data,
        "CV_model": float(np.sqrt(variance_model) / mean_model) if abs(mean_model) > 1e-9 else 0.0,
        "CV_data": float(np.sqrt(variance_data) / mean_data) if abs(mean_data) > 1e-9 else 0.0,
        "Levene_statistic": float(levene_statistic),
        "Levene_p_value": float(levene_p_value),
    }


def compute_diurnal_variance(y: pd.Series) -> pd.Series:
    """Group a timeseries by hour-of-day and compute variance within each hour."""

    if not isinstance(y.index, pd.DatetimeIndex):
        raise ValueError("compute_diurnal_variance requires a DatetimeIndex.")

    grouped = y.groupby(y.index.hour)
    return grouped.var().fillna(0.0)
