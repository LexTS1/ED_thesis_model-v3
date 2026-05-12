"""Preprocessing helpers for validation inputs."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd


def ensure_timeseries_frame(
    data: pd.DataFrame | Mapping[str, Any],
    value_column: str = "value",
    timestamp_column: str = "timestamp",
) -> pd.DataFrame:
    """Return a clean timeseries frame with explicit timestamp and numeric value columns."""

    if isinstance(data, pd.DataFrame):
        frame = data.copy()
    else:
        frame = pd.DataFrame(data)

    if timestamp_column not in frame.columns:
        if frame.index.name == timestamp_column or isinstance(frame.index, pd.DatetimeIndex):
            frame = frame.reset_index().rename(columns={frame.columns[0]: timestamp_column})
        else:
            raise ValueError(f"Missing required timestamp column: {timestamp_column}")

    timestamp_values = frame[timestamp_column]
    has_explicit_offset = timestamp_values.astype(str).str.contains(r"(?:Z|[+-]\d{2}:?\d{2})$").any()
    if has_explicit_offset:
        parsed_timestamps = pd.to_datetime(frame[timestamp_column], utc=True).dt.tz_convert("Europe/Brussels")
        timestamp_tz = parsed_timestamps.dt.tz
    else:
        parsed_timestamps = pd.to_datetime(frame[timestamp_column], utc=False)
        timestamp_tz = getattr(parsed_timestamps.dt, "tz", None)

    frame[timestamp_column] = parsed_timestamps
    if timestamp_tz is None:
        frame[timestamp_column] = frame[timestamp_column].dt.tz_localize(
            "Europe/Brussels",
            ambiguous=False,
            nonexistent="shift_forward",
        )
    else:
        frame[timestamp_column] = frame[timestamp_column].dt.tz_convert("Europe/Brussels")
    frame = frame.sort_values(timestamp_column).drop_duplicates(subset=[timestamp_column], keep="last")

    if value_column not in frame.columns:
        numeric_candidates = [column for column in frame.columns if column != timestamp_column and pd.api.types.is_numeric_dtype(frame[column])]
        if not numeric_candidates:
            raise ValueError(f"Missing required value column: {value_column}")
        frame = frame.rename(columns={numeric_candidates[0]: value_column})

    frame[value_column] = pd.to_numeric(frame[value_column], errors="coerce")
    frame = frame.dropna(subset=[value_column]).reset_index(drop=True)
    return frame


def load_csv_timeseries(
    path: str | Path,
    value_column: str = "value",
    timestamp_column: str = "timestamp",
) -> pd.DataFrame:
    """Load a csv timeseries file and normalise it for validation."""

    frame = pd.read_csv(Path(path))
    return ensure_timeseries_frame(frame, value_column=value_column, timestamp_column=timestamp_column)


def scalar_bands_to_profile(
    aggregate_profile: np.ndarray,
    mean_profile: float,
    p10_value: float,
    p50_value: float,
    p90_value: float,
) -> pd.DataFrame:
    """Project scalar uncertainty summaries onto an aggregate profile shape for visualisation."""

    base_mean = mean_profile if abs(mean_profile) > 1e-9 else 1.0
    frame = pd.DataFrame({"mean_W": aggregate_profile.astype(float)})
    frame["P10_W"] = frame["mean_W"] * (p10_value / base_mean)
    frame["P50_W"] = frame["mean_W"] * (p50_value / base_mean)
    frame["P90_W"] = frame["mean_W"] * (p90_value / base_mean)
    return frame
