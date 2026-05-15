"""Chunked loading for KU Leuven high-frequency case-study data."""

from __future__ import annotations

import logging
from collections import defaultdict
from pathlib import Path
from typing import Any

import pandas as pd


LOGGER = logging.getLogger(__name__)
_BELGIUM_TZ = "Europe/Brussels"


def _resolve_base_path(base_path: str | Path) -> Path:
    """Resolve the configured KU Leuven directory with a local fallback."""

    candidate = Path(base_path)
    if candidate.exists():
        return candidate
    fallback = Path(str(candidate).replace("inputs/load_profiles", "inputs/load_profiles"))
    if fallback.exists():
        return fallback
    raise FileNotFoundError(f"KU Leuven base path does not exist: {base_path}")


def _detect_timestamp_column(columns: list[str]) -> str:
    """Detect the timestamp column."""

    preferred = ["timestamp", "Timestamp", "datetime", "time"]
    for column in preferred:
        if column in columns:
            return column
    raise ValueError(f"Could not detect KU Leuven timestamp column from: {columns}")


def _detect_signal_column(columns: list[str]) -> tuple[str, str]:
    """Detect whether the dataset carries direct power or cumulative energy."""

    power_preferred = ["P+", "P_total", "power_kW", "power", "active_power"]
    for column in power_preferred:
        if column in columns:
            return column, "power"
    for column in columns:
        lowered = column.lower()
        if lowered.startswith("p") or "power" in lowered:
            return column, "power"
    for column in columns:
        lowered = column.lower()
        if "kwh" in lowered or "wh" in lowered or "energy" in lowered or "cumulative" in lowered:
            return column, "cumulative"
    raise ValueError(f"Could not detect KU Leuven power/cumulative column from: {columns}")


def _coerce_power_to_kw(series: pd.Series, *, column_name: str) -> pd.Series:
    """Convert a direct power signal to kW when needed."""

    numeric = pd.to_numeric(series, errors="coerce")
    finite = numeric[pd.notna(numeric)]
    if finite.empty:
        return numeric
    lowered = column_name.lower()
    if "kw" in lowered:
        return numeric
    if "w" in lowered and "kw" not in lowered:
        return numeric / 1000.0
    if float(finite.quantile(0.95)) > 50.0:
        LOGGER.warning("kuleuven_loader.warning assuming power column %s is in W and converting to kW", column_name)
        return numeric / 1000.0
    return numeric


def _quality_checks(series: pd.Series, *, label: str) -> list[str]:
    """Run lightweight timestamp and NaN checks on a case-study profile."""

    warnings: list[str] = []
    if series.empty:
        warnings.append(f"{label}: profile is empty")
        return warnings
    deltas = series.index.to_series().sort_values().diff().dropna().dt.total_seconds()
    if not deltas.empty:
        modal_delta = int(deltas.mode().iloc[0])
        if (deltas != modal_delta).any():
            warnings.append(f"{label}: irregular sampling detected (modal timestep {modal_delta}s)")
        gaps = int((deltas > modal_delta).sum())
        if gaps > 0:
            warnings.append(f"{label}: missing timestamps / gaps detected ({gaps})")
    nan_count = int(series.isna().sum())
    if nan_count > 0:
        warnings.append(f"{label}: NaN propagation detected ({nan_count})")
    return warnings


def _load_house_profile(csv_path: Path, *, chunksize: int = 200_000) -> pd.Series:
    """Load one KU Leuven household file into a 15-minute kW profile."""

    sample = pd.read_csv(csv_path, nrows=10)
    timestamp_column = _detect_timestamp_column(list(sample.columns))
    signal_column, signal_kind = _detect_signal_column([column for column in sample.columns if column != timestamp_column])

    sums: defaultdict[pd.Timestamp, float] = defaultdict(float)
    counts: defaultdict[pd.Timestamp, int] = defaultdict(int)
    previous_timestamp: pd.Timestamp | None = None
    previous_value: float | None = None

    for chunk in pd.read_csv(csv_path, usecols=[timestamp_column, signal_column], chunksize=chunksize):
        timestamps = pd.to_datetime(chunk[timestamp_column], utc=True, errors="coerce")
        raw_signal = pd.to_numeric(chunk[signal_column], errors="coerce")

        if signal_kind == "power":
            power_kw = _coerce_power_to_kw(raw_signal, column_name=signal_column)
            frame = pd.DataFrame({"timestamp": timestamps, "power_kW": power_kw}).dropna(subset=["timestamp", "power_kW"])
        else:
            chunk_frame = pd.DataFrame({"timestamp": timestamps, "signal": raw_signal}).dropna(subset=["timestamp", "signal"])
            power_rows: list[tuple[pd.Timestamp, float]] = []
            for timestamp, value in zip(chunk_frame["timestamp"], chunk_frame["signal"]):
                if previous_timestamp is None or previous_value is None:
                    previous_timestamp = pd.Timestamp(timestamp)
                    previous_value = float(value)
                    continue
                delta_hours = (pd.Timestamp(timestamp) - previous_timestamp).total_seconds() / 3600.0
                if delta_hours <= 0.0:
                    previous_timestamp = pd.Timestamp(timestamp)
                    previous_value = float(value)
                    continue
                delta_value = float(value) - previous_value
                lowered = signal_column.lower()
                if "wh" in lowered and "kwh" not in lowered:
                    delta_value = delta_value / 1000.0
                power_rows.append((pd.Timestamp(timestamp), max(delta_value / delta_hours, 0.0)))
                previous_timestamp = pd.Timestamp(timestamp)
                previous_value = float(value)
            frame = pd.DataFrame(power_rows, columns=["timestamp", "power_kW"])

        if frame.empty:
            continue
        frame["bucket"] = frame["timestamp"].dt.floor("15min").dt.tz_convert(_BELGIUM_TZ)
        grouped = frame.groupby("bucket", sort=True)["power_kW"].agg(["sum", "count"])
        for timestamp, row in grouped.iterrows():
            sums[pd.Timestamp(timestamp)] += float(row["sum"])
            counts[pd.Timestamp(timestamp)] += int(row["count"])

    series = pd.Series(
        {
            timestamp: sums[timestamp] / max(counts[timestamp], 1)
            for timestamp in sums
        },
        dtype=float,
    ).sort_index()
    series.name = csv_path.parent.name
    for warning in _quality_checks(series, label=csv_path.parent.name):
        LOGGER.warning("kuleuven_loader.warning %s", warning)
    return series


def load_kuleuven_profiles(base_path: str | Path) -> dict[str, pd.Series]:
    """Load all KU Leuven house case-study electricity files into 15-minute kW profiles."""

    root = _resolve_base_path(base_path)
    house_files = sorted(root.glob("house_*/*-elec.csv"))
    if not house_files:
        raise FileNotFoundError(f"No KU Leuven electricity files found under {root}")

    profiles: dict[str, pd.Series] = {}
    for csv_path in house_files:
        house_id = csv_path.parent.name
        profiles[house_id] = _load_house_profile(csv_path)
    return profiles
