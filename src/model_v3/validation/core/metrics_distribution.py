"""Distribution realism metrics for validation."""

from __future__ import annotations

import numpy as np
from scipy import stats


def compute_distribution_metrics(y_model: np.ndarray, y_data: np.ndarray) -> dict[str, float]:
    """Compare whole-sample distributions and key quantiles."""

    model = np.asarray(y_model, dtype=float)
    data = np.asarray(y_data, dtype=float)
    model = model[np.isfinite(model)]
    data = data[np.isfinite(data)]
    if len(model) == 0 or len(data) == 0:
        return {
            "Anderson_Darling_statistic": 0.0,
            "P10_error": 0.0,
            "P50_error": 0.0,
            "P90_error": 0.0,
        }

    combined = np.concatenate([model, data])
    if len(np.unique(combined)) <= 1:
        ad_statistic = 0.0
    else:
        try:
            ad_statistic = float(stats.anderson_ksamp([model, data]).statistic)
        except ValueError:
            ad_statistic = 0.0

    return {
        "Anderson_Darling_statistic": ad_statistic,
        "P10_error": float(np.percentile(model, 10) - np.percentile(data, 10)),
        "P50_error": float(np.percentile(model, 50) - np.percentile(data, 50)),
        "P90_error": float(np.percentile(model, 90) - np.percentile(data, 90)),
    }


def compute_ldc(y: np.ndarray) -> np.ndarray:
    """Compute a load duration curve."""

    return np.sort(np.asarray(y, dtype=float))[::-1]
