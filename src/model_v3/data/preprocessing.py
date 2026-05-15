"""Missing-data handling for harmonised model_v3 source data."""

from __future__ import annotations

from model_v3.interfaces import TimeSeriesData


def reconstruct_missing_data(df: TimeSeriesData, method: str = "zero_fill") -> TimeSeriesData:
    """Reconstruct missing values only after harmonisation has been applied."""

    missing_total = 0
    total_values = 0
    reconstructed_columns: dict[str, tuple[float | None, ...]] = {}

    for column_name, values in df.columns.items():
        last_value = 0.0
        reconstructed_values: list[float | None] = []
        for value in values:
            total_values += 1
            if value is None:
                missing_total += 1
                if method == "forward_fill":
                    reconstructed_values.append(last_value)
                else:
                    reconstructed_values.append(0.0)
            else:
                last_value = float(value)
                reconstructed_values.append(float(value))
        reconstructed_columns[column_name] = tuple(reconstructed_values)

    reconstruction_confidence = 1.0 if total_values == 0 else max(0.0, 1.0 - (missing_total / total_values))
    metadata = dict(df.metadata)
    metadata.update(
        {
            "reconstruction_method": method,
            "reconstruction_confidence": reconstruction_confidence,
        }
    )
    return TimeSeriesData(
        timestamps=df.timestamps,
        columns=reconstructed_columns,
        metadata=metadata,
    )
