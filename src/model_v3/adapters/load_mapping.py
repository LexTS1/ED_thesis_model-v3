"""Semantic mapping for raw load-profile inputs."""

from __future__ import annotations

import logging

from model_v3.interfaces import TimeSeriesData

LOGGER = logging.getLogger(__name__)


def map_load_profiles(df: TimeSeriesData) -> TimeSeriesData:
    """Map raw load-profile columns onto explicit end-use electricity fields."""

    source_columns = dict(df.columns)
    column_mapping = {
        "P_appliances_W": "appliances",
        "P_lighting_W": "lighting",
        "P_cooking_W": "cooking",
        "P_dhw_W": "dhw",
        "P_ev_charging_W": "ev_charging",
    }
    mapped_columns: dict[str, tuple[float | None, ...]] = {}
    missing_fields: list[str] = []

    for mapped_name, raw_name in column_mapping.items():
        raw_values = source_columns.get(raw_name)
        if raw_values is None:
            missing_fields.append(mapped_name)
            mapped_columns[mapped_name] = tuple(0.0 for _ in df.timestamps)
        else:
            mapped_columns[mapped_name] = tuple(0.0 if value is None else float(value) for value in raw_values)

    if missing_fields:
        LOGGER.warning("load_mapping.missing_fields fields=%s fallback=zeros", ",".join(missing_fields))

    metadata = dict(df.metadata)
    metadata.update(
        {
            "mapped_columns": tuple(mapped_columns.keys()),
            "unused_columns_removed": tuple(column for column in source_columns if column not in column_mapping.values()),
        }
    )
    return TimeSeriesData(
        timestamps=df.timestamps,
        columns=mapped_columns,
        metadata=metadata,
    )
