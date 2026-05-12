"""Load PVGIS multi-year weather exports into a clean hourly dataframe."""

from __future__ import annotations

from datetime import timedelta, timezone
from pathlib import Path

import pandas as pd


PVGIS_TIME_PATTERN = r"^\d{8}:\d{4}$"


def _find_header_row(path: Path) -> int:
    """Return the row index of the PVGIS data header."""

    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle):
            if line.lstrip().startswith("time,"):
                return line_number
    raise ValueError(f"No PVGIS header row starting with `time,` found in `{path}`.")


def _parse_pvgis_timestamp(series: pd.Series) -> pd.DatetimeIndex:
    """Parse PVGIS timestamps and preserve the reported local calendar year."""

    parsed = pd.to_datetime(series.astype(str), format="%Y%m%d:%H%M", errors="coerce")
    if parsed.isna().any():
        raise ValueError("PVGIS timestamps contain unparsable values after header/footer filtering.")
    return pd.DatetimeIndex(parsed.dt.tz_localize(timezone(timedelta(hours=1))))


def _validate_hourly_index(index: pd.DatetimeIndex) -> None:
    """Ensure the PVGIS index is strictly hourly."""

    if index.empty:
        raise ValueError("PVGIS weather dataset is empty.")
    if not index.is_monotonic_increasing:
        raise ValueError("PVGIS weather timestamps must be sorted.")
    if index.has_duplicates:
        raise ValueError("PVGIS weather timestamps must be unique.")
    deltas = index.to_series().diff().dropna()
    if not deltas.empty and not deltas.eq(pd.Timedelta(hours=1)).all():
        raise ValueError("PVGIS weather dataset is not hourly.")


def load_pvgis_weather_csv(path: str | Path) -> pd.DataFrame:
    """Load a PVGIS weather CSV and return an hourly forcing-ready dataframe."""

    resolved_path = Path(path).expanduser().resolve()
    header_row = _find_header_row(resolved_path)
    raw = pd.read_csv(resolved_path, skiprows=header_row, low_memory=False)
    data = raw.loc[raw["time"].astype(str).str.fullmatch(PVGIS_TIME_PATTERN, na=False)].copy()
    if data.empty:
        raise ValueError(f"No PVGIS weather rows were found in `{resolved_path}`.")

    required_columns = {"T2m", "Gb(i)", "Gd(i)", "Gr(i)"}
    missing = required_columns.difference(data.columns)
    if missing:
        raise ValueError(f"PVGIS weather file `{resolved_path}` is missing columns: {sorted(missing)}")

    index = _parse_pvgis_timestamp(data["time"])
    result = pd.DataFrame(
        {
            "temperature_C": pd.to_numeric(data["T2m"], errors="coerce").to_numpy(),
            "ghi_Wm2": (
                pd.to_numeric(data["Gb(i)"], errors="coerce")
                + pd.to_numeric(data["Gd(i)"], errors="coerce")
                + pd.to_numeric(data["Gr(i)"], errors="coerce")
            ).to_numpy(),
        },
        index=index,
    )
    if "WS10m" in data.columns:
        result["wind_ms"] = pd.to_numeric(data["WS10m"], errors="coerce").to_numpy()
    else:
        result["wind_ms"] = 0.0

    result = result.sort_index()
    _validate_hourly_index(result.index)
    if result[["temperature_C", "ghi_Wm2", "wind_ms"]].isna().any().any():
        raise ValueError(f"PVGIS weather file `{resolved_path}` contains NaN values in required fields.")
    result.index.name = "timestamp"
    return result[["temperature_C", "ghi_Wm2", "wind_ms"]]
