"""Load PVGIS facade irradiance CSVs into orientation-resolved hourly series."""

from __future__ import annotations

from datetime import timedelta, timezone
from pathlib import Path

import pandas as pd


PVGIS_TIME_PATTERN = r"^\d{8}:\d{4}$"
ORIENTATIONS = ("south", "east", "west", "north")


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
        raise ValueError("PVGIS solar dataset is empty.")
    if not index.is_monotonic_increasing:
        raise ValueError("PVGIS solar timestamps must be sorted.")
    if index.has_duplicates:
        raise ValueError("PVGIS solar timestamps must be unique.")
    deltas = index.to_series().diff().dropna()
    if not deltas.empty and not deltas.eq(pd.Timedelta(hours=1)).all():
        raise ValueError("PVGIS solar dataset is not hourly.")


def _load_orientation_series(path: str | Path) -> pd.Series:
    """Load one PVGIS facade export and return total irradiance."""

    resolved_path = Path(path).expanduser().resolve()
    header_row = _find_header_row(resolved_path)
    raw = pd.read_csv(resolved_path, skiprows=header_row, low_memory=False)
    data = raw.loc[raw["time"].astype(str).str.fullmatch(PVGIS_TIME_PATTERN, na=False)].copy()
    if data.empty:
        raise ValueError(f"No PVGIS solar rows were found in `{resolved_path}`.")

    required_columns = {"Gb(i)", "Gd(i)", "Gr(i)"}
    missing = required_columns.difference(data.columns)
    if missing:
        raise ValueError(f"PVGIS solar file `{resolved_path}` is missing columns: {sorted(missing)}")

    index = _parse_pvgis_timestamp(data["time"])
    series = pd.Series(
        (
            pd.to_numeric(data["Gb(i)"], errors="coerce")
            + pd.to_numeric(data["Gd(i)"], errors="coerce")
            + pd.to_numeric(data["Gr(i)"], errors="coerce")
        ).to_numpy(),
        index=index,
        name="irradiance_Wm2",
        dtype=float,
    ).sort_index()
    _validate_hourly_index(series.index)
    if series.isna().any():
        raise ValueError(f"PVGIS solar file `{resolved_path}` contains NaN irradiance values.")
    return series


def load_pvgis_solar_csvs(paths: dict[str, str | Path]) -> dict[str, pd.Series]:
    """Load the four PVGIS orientation files into a dict of hourly series."""

    missing_orientations = sorted(set(ORIENTATIONS).difference(paths))
    if missing_orientations:
        raise ValueError(f"Missing PVGIS solar paths for orientations: {missing_orientations}")

    loaded = {orientation: _load_orientation_series(paths[orientation]) for orientation in ORIENTATIONS}
    reference_index = next(iter(loaded.values())).index
    for orientation, series in loaded.items():
        if not series.index.equals(reference_index):
            raise ValueError(f"PVGIS solar timestamps for `{orientation}` do not align with the other orientations.")
    return loaded
