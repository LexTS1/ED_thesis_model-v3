"""Climate sensitivity metrics for scenario-tree standardized summaries."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Iterable

import pandas as pd


DATETIME_COLUMN_CANDIDATES = ("datetime", "timestamp", "time", "date")
TEMPERATURE_COLUMN_CANDIDATES = (
    "T_out_C",
    "temp_C",
    "temperature_C",
    "outdoor_temperature_C",
    "dry_bulb_C",
    "temp_dry_shelter_avg",
    "tas",
)
SOLAR_COLUMN_CANDIDATES = (
    "solar_W_m2",
    "irradiance_W_m2",
    "global_irradiance_W_m2",
    "I_solar_W_m2",
    "G_i",
    "G(i)",
    "ghi",
    "POA_global_W_m2",
)


class ClimateMetricsError(RuntimeError):
    """Raised when climate forcing metrics cannot be computed."""


@dataclass(frozen=True)
class ClimateMetricResult:
    """Climate metric payload plus selected-column diagnostics."""

    metrics: dict[str, float]
    annual_metrics: list[dict[str, float | int]]
    temperature_column: str
    solar_column: str
    datetime_column: str
    included_years: list[int]
    includes_2050: bool
    row_count: int
    first_timestamp: str
    last_timestamp: str


def _matches(columns: Iterable[str], candidates: Iterable[str]) -> list[str]:
    column_set = set(columns)
    return [candidate for candidate in candidates if candidate in column_set]


def detect_datetime_column(columns: Iterable[str]) -> str:
    matches = _matches(columns, DATETIME_COLUMN_CANDIDATES)
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise ClimateMetricsError(
            "Climate forcing file has no detected datetime column. "
            f"Expected one of: {', '.join(DATETIME_COLUMN_CANDIDATES)}."
        )
    raise ClimateMetricsError(f"Ambiguous climate datetime columns: {', '.join(matches)}.")


def detect_climate_column(
    columns: Iterable[str],
    candidates: Iterable[str],
    *,
    explicit_column: str | None = None,
    label: str,
) -> str:
    column_list = list(columns)
    if explicit_column:
        if explicit_column not in column_list:
            raise ClimateMetricsError(f"Configured {label} column is absent from climate forcing file: {explicit_column}")
        return explicit_column
    matches = _matches(column_list, candidates)
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise ClimateMetricsError(
            f"Climate forcing file has no detected {label} column. "
            f"Expected one of: {', '.join(candidates)}."
        )
    raise ClimateMetricsError(
        f"Ambiguous climate {label} columns: {', '.join(matches)}. "
        "Pass an explicit column in the caller/config before computing metrics."
    )


def _parse_date(value: str | date | pd.Timestamp) -> date:
    return pd.Timestamp(value).date()


def _parse_timestamp(value: object) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    if ts.tzinfo is not None:
        return ts.tz_localize(None)
    return ts


def _filter_canonical_window(
    frame: pd.DataFrame,
    datetime_column: str,
    analysis_start: str | date | pd.Timestamp,
    analysis_end: str | date | pd.Timestamp,
) -> pd.DataFrame:
    start_date = _parse_date(analysis_start)
    end_date = _parse_date(analysis_end)
    timestamps = frame[datetime_column].map(_parse_timestamp)
    dates = timestamps.map(lambda value: value.date())
    mask = (dates >= start_date) & (dates <= end_date)
    filtered = frame.loc[mask].copy()
    filtered["_summary_timestamp"] = timestamps.loc[mask].to_list()
    if filtered.empty:
        raise ClimateMetricsError(
            f"No climate forcing rows in canonical analysis window {start_date} to {end_date}."
        )
    return filtered


def _metric_payload(
    *,
    timestamps: pd.Series,
    temp: pd.Series,
    solar: pd.Series,
) -> dict[str, float]:
    months = timestamps.map(lambda value: int(value.month))
    daily = (
        pd.DataFrame({"date": timestamps.map(lambda value: value.date()), "temperature": temp})
        .groupby("date", as_index=True)["temperature"]
        .mean()
    )
    return {
        "mean_T_out_C": float(temp.mean()),
        "winter_mean_T_out_C": float(temp.loc[months.isin({12, 1, 2})].mean()),
        "summer_mean_T_out_C": float(temp.loc[months.isin({6, 7, 8})].mean()),
        "HDD_15": float((15.0 - daily).clip(lower=0.0).sum()),
        "HDD_18": float((18.0 - daily).clip(lower=0.0).sum()),
        "CDD_22": float((daily - 22.0).clip(lower=0.0).sum()),
        "mean_solar_W_m2": float(solar.mean()),
    }


def _annual_metric_rows(
    *,
    timestamps: pd.Series,
    temp: pd.Series,
    solar: pd.Series,
) -> list[dict[str, float | int]]:
    rows: list[dict[str, float | int]] = []
    years = sorted({int(value.year) for value in timestamps})
    for year in years:
        mask = timestamps.map(lambda value: int(value.year) == year)
        payload = _metric_payload(
            timestamps=timestamps.loc[mask],
            temp=temp.loc[mask],
            solar=solar.loc[mask],
        )
        rows.append({"year": int(year), **payload})
    return rows


def compute_climate_metrics(
    climate_forcing_file: Path,
    *,
    analysis_start: str | date | pd.Timestamp,
    analysis_end: str | date | pd.Timestamp,
    temperature_column: str | None = None,
    solar_column: str | None = None,
) -> ClimateMetricResult:
    """Compute climate sensitivity metrics over the canonical analysis window."""

    path = Path(climate_forcing_file)
    if not path.exists():
        raise ClimateMetricsError(f"Missing climate forcing file: {path}")
    frame = pd.read_csv(path)
    if frame.empty:
        raise ClimateMetricsError(f"Climate forcing file is empty: {path}")

    datetime_col = detect_datetime_column(frame.columns)
    temp_col = detect_climate_column(
        frame.columns,
        TEMPERATURE_COLUMN_CANDIDATES,
        explicit_column=temperature_column,
        label="temperature",
    )
    solar_col = detect_climate_column(
        frame.columns,
        SOLAR_COLUMN_CANDIDATES,
        explicit_column=solar_column,
        label="solar irradiance",
    )
    filtered = _filter_canonical_window(frame, datetime_col, analysis_start, analysis_end)
    temp = pd.to_numeric(filtered[temp_col], errors="coerce")
    solar = pd.to_numeric(filtered[solar_col], errors="coerce")
    if temp.isna().all():
        raise ClimateMetricsError(f"Temperature column contains no numeric values: {temp_col}")
    if solar.isna().all():
        raise ClimateMetricsError(f"Solar irradiance column contains no numeric values: {solar_col}")

    timestamps = pd.Series(filtered["_summary_timestamp"].to_list(), index=filtered.index)
    years = sorted({int(value.year) for value in timestamps})
    metrics = _metric_payload(timestamps=timestamps, temp=temp, solar=solar)
    return ClimateMetricResult(
        metrics=metrics,
        annual_metrics=_annual_metric_rows(timestamps=timestamps, temp=temp, solar=solar),
        temperature_column=temp_col,
        solar_column=solar_col,
        datetime_column=datetime_col,
        included_years=years,
        includes_2050=2050 in years,
        row_count=int(len(filtered)),
        first_timestamp=min(timestamps).isoformat(),
        last_timestamp=max(timestamps).isoformat(),
    )
