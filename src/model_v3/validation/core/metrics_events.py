"""Event-based validation metrics."""

from __future__ import annotations

import numpy as np
import pandas as pd


def extract_extreme_days(y: pd.Series, temperature_series: pd.Series, quantile: float = 0.1) -> dict[str, pd.Index]:
    """Identify coldest days and highest-demand days."""

    if not isinstance(y.index, pd.DatetimeIndex) or not isinstance(temperature_series.index, pd.DatetimeIndex):
        raise ValueError("extract_extreme_days requires DatetimeIndex inputs.")

    daily_load = y.resample("D").mean()
    daily_temperature = temperature_series.resample("D").mean()
    cold_threshold = daily_temperature.quantile(quantile)
    peak_threshold = daily_load.quantile(1.0 - quantile)

    return {
        "coldest_days": daily_temperature[daily_temperature <= cold_threshold].index,
        "peak_demand_days": daily_load[daily_load >= peak_threshold].index,
    }


def compute_event_metrics(y_model: pd.Series, y_data: pd.Series) -> dict[str, float]:
    """Compute simple event-based errors on extreme demand behaviour."""

    if not isinstance(y_model.index, pd.DatetimeIndex) or not isinstance(y_data.index, pd.DatetimeIndex):
        raise ValueError("compute_event_metrics requires DatetimeIndex inputs.")

    model_series = pd.Series(pd.to_numeric(y_model, errors="coerce"), index=y_model.index).dropna()
    data_series = pd.Series(pd.to_numeric(y_data, errors="coerce"), index=y_data.index).dropna()
    if len(model_series) == 0 or len(data_series) == 0:
        return {"peak_day_error": 0.0, "peak_MAE_kW": 0.0, "extreme_condition_error": 0.0}

    model_daily = model_series.resample("D").max()
    data_daily = data_series.resample("D").max()
    peak_day_error = float(abs(model_daily.max() - data_daily.max()))
    peak_mae_kw = peak_day_error / 1000.0

    model_extreme_threshold = model_series.quantile(0.95)
    data_extreme_threshold = data_series.quantile(0.95)
    extreme_condition_error = float(
        abs(
            model_series[model_series >= model_extreme_threshold].mean()
            - data_series[data_series >= data_extreme_threshold].mean()
        )
    )

    return {
        "peak_day_error": peak_day_error,
        "peak_MAE_kW": peak_mae_kw,
        "extreme_condition_error": extreme_condition_error,
    }
