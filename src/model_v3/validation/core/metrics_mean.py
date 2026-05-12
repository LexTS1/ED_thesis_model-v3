"""Mean error metrics for validation."""

from __future__ import annotations

from typing import Mapping

import numpy as np


def compute_mean_metrics(y_model: Mapping | np.ndarray, y_data: Mapping | np.ndarray) -> dict[str, float]:
    """Compute standard mean accuracy metrics."""

    model = np.asarray(y_model, dtype=float)
    data = np.asarray(y_data, dtype=float)
    finite_mask = np.isfinite(model) & np.isfinite(data)
    model = model[finite_mask]
    data = data[finite_mask]
    if len(model) == 0:
        return {"MBE": 0.0, "MAE": 0.0, "RMSE": 0.0, "CVRMSE": 0.0}
    error = model - data
    data_mean = float(np.mean(data))
    rmse = float(np.sqrt(np.mean(error**2)))

    return {
        "MBE": float(np.mean(error)),
        "MAE": float(np.mean(np.abs(error))),
        "RMSE": rmse,
        "CVRMSE": float((rmse / data_mean) * 100.0) if abs(data_mean) > 1e-9 else 0.0,
    }
