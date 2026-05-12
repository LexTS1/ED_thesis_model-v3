"""Alignment helpers for like-for-like validation comparisons."""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from model_v3.validation.utils.preprocessing import ensure_timeseries_frame

LOGGER = logging.getLogger(__name__)


def _infer_resolution_seconds(frame: pd.DataFrame, timestamp_column: str = "timestamp") -> int | None:
    """Infer the modal timestep in seconds from timestamps."""

    if len(frame) < 2:
        return None

    deltas = frame[timestamp_column].sort_values().diff().dropna().dt.total_seconds()
    if deltas.empty:
        return None
    return int(deltas.mode().iloc[0])


def _resample_frame(frame: pd.DataFrame, target_resolution_seconds: int, timestamp_column: str = "timestamp") -> pd.DataFrame:
    """Resample a timeseries frame to a target resolution using explicit aggregation rules."""

    indexed = frame.set_index(timestamp_column).sort_index()
    numeric_columns = indexed.select_dtypes(include="number").columns
    aggregation_rules: dict[str, Any] = {}
    for column in indexed.columns:
        if column in numeric_columns:
            aggregation_rules[column] = "mean"
        else:
            aggregation_rules[column] = "first"

    resampled = indexed.resample(f"{int(target_resolution_seconds)}s").agg(aggregation_rules).dropna(how="all")
    return resampled.reset_index()


def align_timeseries(model_df: pd.DataFrame, data_df: pd.DataFrame, resolution: str | int = "auto") -> tuple[pd.DataFrame, dict[str, Any]]:
    """Align two timeseries frames at the highest common explicit resolution."""

    model_frame = ensure_timeseries_frame(model_df)
    data_frame = ensure_timeseries_frame(data_df)
    model_resolution = _infer_resolution_seconds(model_frame)
    data_resolution = _infer_resolution_seconds(data_frame)

    if resolution == "auto":
        numeric_resolutions = [value for value in (model_resolution, data_resolution) if value is not None]
        target_resolution = max(numeric_resolutions) if numeric_resolutions else None
    else:
        target_resolution = int(resolution)

    decisions = {
        "model_resolution_seconds": model_resolution,
        "data_resolution_seconds": data_resolution,
        "target_resolution_seconds": target_resolution,
        "model_resampled": False,
        "data_resampled": False,
    }

    if target_resolution is not None:
        if model_resolution is not None and model_resolution < target_resolution:
            model_frame = _resample_frame(model_frame, target_resolution)
            decisions["model_resampled"] = True
        if data_resolution is not None and data_resolution < target_resolution:
            data_frame = _resample_frame(data_frame, target_resolution)
            decisions["data_resampled"] = True

    merged = (
        model_frame.rename(columns={"value": "value_model"})
        .merge(
            data_frame.rename(columns={"value": "value_data"}),
            on="timestamp",
            how="inner",
            suffixes=("_model", "_data"),
        )
        .dropna(subset=["value_model", "value_data"])
        .sort_values("timestamp")
        .reset_index(drop=True)
    )
    decisions["matched_timestamps"] = int(len(merged))
    decisions["dropped_model_rows"] = int(len(model_frame) - len(merged))
    decisions["dropped_data_rows"] = int(len(data_frame) - len(merged))

    LOGGER.info(
        "validation.alignment model_res=%s data_res=%s target_res=%s matched=%s",
        model_resolution,
        data_resolution,
        target_resolution,
        decisions["matched_timestamps"],
    )
    return merged, decisions
