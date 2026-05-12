"""Split hourly multi-year climate frames into complete yearly members."""

from __future__ import annotations

import warnings

import pandas as pd


def _validate_hourly_year(frame: pd.DataFrame) -> bool:
    """Return whether the yearly frame is complete and strictly hourly."""

    if frame.empty:
        return False
    if not isinstance(frame.index, pd.DatetimeIndex):
        raise TypeError("split_into_years expects a dataframe indexed by datetime.")
    ordered = frame.sort_index()
    if ordered.index.has_duplicates:
        return False
    deltas = ordered.index.to_series().diff().dropna()
    if not deltas.empty and not deltas.eq(pd.Timedelta(hours=1)).all():
        return False
    expected_hours = 8784 if ordered.index.is_leap_year.any() else 8760
    return len(ordered) == expected_hours


def split_into_years(df: pd.DataFrame) -> dict[int, pd.DataFrame]:
    """Return a mapping of complete calendar years to hourly dataframes."""

    if not isinstance(df.index, pd.DatetimeIndex):
        raise TypeError("split_into_years expects a dataframe indexed by datetime.")

    years: dict[int, pd.DataFrame] = {}
    for year, group in df.sort_index().groupby(df.sort_index().index.year):
        yearly = group.copy()
        if _validate_hourly_year(yearly):
            years[int(year)] = yearly
            continue
        warnings.warn(
            f"Dropping incomplete PVGIS year {year}: expected a complete hourly year, got {len(yearly)} rows.",
            RuntimeWarning,
            stacklevel=2,
        )
    return years
