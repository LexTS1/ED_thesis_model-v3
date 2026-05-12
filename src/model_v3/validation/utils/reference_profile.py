"""Generic reference profile loading helpers for validation."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from model_v3.validation.utils.preprocessing import ensure_timeseries_frame


def load_aggregate_reference_profile(
    config: Mapping[str, Any],
    validation_cfg: Mapping[str, Any],
) -> tuple[pd.DataFrame, pd.DataFrame, str]:
    """Load an explicitly configured aggregate reference profile."""

    dataset_path = validation_cfg.get("aggregate_path") or validation_cfg.get("reference_path")
    if not dataset_path:
        raise FileNotFoundError(
            "validation.aggregate_path must point to an explicitly configured aggregate reference csv file."
        )

    raw_frame = pd.read_csv(Path(dataset_path))
    data_sources_cfg = dict(dict(config.get("data", {})).get("sources", {}))
    load_source_cfg = dict(data_sources_cfg.get("load_profiles", {}))
    timestamp_column = str(
        validation_cfg.get(
            "timestamp_column",
            load_source_cfg.get("timestamp_column", "timestamp"),
        )
    )
    if timestamp_column not in raw_frame.columns:
        raise ValueError(f"Aggregate reference csv must include the configured timestamp column: {timestamp_column}")
    raw_frame[timestamp_column] = pd.to_datetime(raw_frame[timestamp_column], utc=False)

    value_column = validation_cfg.get("value_column")
    aggregation_mode = str(validation_cfg.get("aggregate_aggregation", "mean_across_columns"))
    if value_column and str(value_column) in raw_frame.columns:
        prepared = ensure_timeseries_frame(raw_frame, value_column=str(value_column), timestamp_column=timestamp_column)
        return prepared, raw_frame.rename(columns={timestamp_column: "timestamp"}), f"explicit_column:{value_column}"

    numeric_columns = [
        column
        for column in raw_frame.columns
        if column != timestamp_column and pd.api.types.is_numeric_dtype(raw_frame[column])
    ]
    if aggregation_mode != "mean_across_columns" or not numeric_columns:
        raise ValueError(
            "Aggregate validation requires either `validation.value_column` or numeric reference columns for mean aggregation."
        )

    source_units = str(validation_cfg.get("aggregate_units", load_source_cfg.get("units", "kWh_per_interval"))).strip().lower()
    interval_seconds = int(validation_cfg.get("aggregate_interval_seconds", load_source_cfg.get("original_timestep_seconds", 3600)))
    aggregated = raw_frame[numeric_columns].apply(pd.to_numeric, errors="coerce").mean(axis=1, skipna=True).fillna(0.0)
    if source_units == "kwh_per_interval":
        aggregated = aggregated * (3_600_000.0 / max(interval_seconds, 1))
    elif source_units != "w":
        raise ValueError(f"Unsupported aggregate reference units: {source_units}")
    prepared = pd.DataFrame(
        {
            "timestamp": raw_frame[timestamp_column],
            "value": aggregated,
        }
    )
    prepared = ensure_timeseries_frame(prepared, value_column="value", timestamp_column="timestamp")
    return prepared, raw_frame.rename(columns={timestamp_column: "timestamp"}), aggregation_mode

