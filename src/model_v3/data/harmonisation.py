"""Cadence harmonisation helpers for model_v3 source data."""

from __future__ import annotations

from datetime import timedelta

from model_v3.interfaces import TimeSeriesData


def _aggregate(values: list[float], method: str) -> float:
    """Aggregate a list of numeric values according to the requested method."""

    if not values:
        return 0.0
    if method == "sum":
        return float(sum(values))
    return float(sum(values) / len(values))


def harmonise_timeseries(df: TimeSeriesData, target_resolution_seconds: int, method: str) -> TimeSeriesData:
    """Harmonise a single source timeseries before any merge occurs."""

    if method not in {"mean", "sum", "forward_fill"}:
        raise ValueError(f"Unsupported harmonisation method: {method}")

    if not df.timestamps:
        metadata = dict(df.metadata)
        metadata.update(
            {
                "target_resolution": int(target_resolution_seconds),
                "target_timestep_seconds": int(target_resolution_seconds),
                "alignment_method": method,
                "harmonisation_log": f"{df.metadata.get('source_name', 'unknown')}: empty dataset preserved",
            }
        )
        return TimeSeriesData(timestamps=(), columns=dict(df.columns), metadata=metadata)

    start = df.timestamps[0]
    end = df.timestamps[-1]
    interval = timedelta(seconds=int(target_resolution_seconds))

    if method == "forward_fill":
        harmonised_timestamps: list = []
        current = start
        while current <= end:
            harmonised_timestamps.append(current)
            current += interval

        harmonised_columns: dict[str, tuple[float | None, ...]] = {}
        for column_name, values in df.columns.items():
            filled_values: list[float | None] = []
            last_seen: float | None = None
            source_index = 0
            for target_timestamp in harmonised_timestamps:
                while source_index < len(df.timestamps) and df.timestamps[source_index] <= target_timestamp:
                    source_value = values[source_index]
                    if source_value is not None:
                        last_seen = source_value
                    source_index += 1
                filled_values.append(last_seen)
            harmonised_columns[column_name] = tuple(filled_values)
    else:
        grouped_values: dict[int, dict[str, list[float]]] = {}
        for source_index, timestamp in enumerate(df.timestamps):
            bin_index = int((timestamp - start).total_seconds() // int(target_resolution_seconds))
            grouped_values.setdefault(bin_index, {column_name: [] for column_name in df.columns})
            for column_name, values in df.columns.items():
                source_value = values[source_index]
                if source_value is not None:
                    grouped_values[bin_index][column_name].append(source_value)

        harmonised_timestamps = []
        harmonised_columns = {column_name: [] for column_name in df.columns}
        max_bin_index = int((end - start).total_seconds() // int(target_resolution_seconds))
        for bin_index in range(max_bin_index + 1):
            harmonised_timestamps.append(start + timedelta(seconds=bin_index * int(target_resolution_seconds)))
            for column_name in df.columns:
                if bin_index in grouped_values and grouped_values[bin_index][column_name]:
                    harmonised_columns[column_name].append(_aggregate(grouped_values[bin_index][column_name], method))
                else:
                    harmonised_columns[column_name].append(None)
        harmonised_columns = {name: tuple(values) for name, values in harmonised_columns.items()}

    metadata = dict(df.metadata)
    metadata.update(
        {
            "target_resolution": int(target_resolution_seconds),
            "target_timestep_seconds": int(target_resolution_seconds),
            "alignment_method": method,
            "harmonisation_log": (
                f"{metadata.get('source_name', 'unknown')}: {metadata.get('original_timestep_seconds')}s -> "
                f"{int(target_resolution_seconds)}s via {method}"
            ),
        }
    )
    return TimeSeriesData(
        timestamps=tuple(harmonised_timestamps),
        columns=harmonised_columns,
        metadata=metadata,
    )
