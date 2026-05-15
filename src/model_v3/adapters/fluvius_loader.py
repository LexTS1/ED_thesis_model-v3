"""Load and aggregate external Fluvius representative load profiles."""

from __future__ import annotations

import logging
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping

import pandas as pd


LOGGER = logging.getLogger(__name__)
_BELGIUM_TZ = "Europe/Brussels"
_INTERVAL_HOURS = 0.25


def _resolve_base_path(base_path: str | Path) -> Path:
    """Resolve the configured Fluvius directory with a local fallback."""

    candidate = Path(base_path)
    if candidate.exists():
        return candidate
    fallback = Path(str(candidate).replace("inputs/load_profiles", "inputs/load_profiles"))
    if fallback.exists():
        return fallback
    raise FileNotFoundError(f"Fluvius base path does not exist: {base_path}")


def _profile_key(path: Path) -> tuple[str, str]:
    """Classify a Fluvius filename into technology and PV segments."""

    stem = path.stem.lower()
    if "wp_ev" in stem or ("wp" in stem and "ev" in stem):
        technology = "ev_hp"
    elif "wp" in stem:
        technology = "hp"
    elif "ev" in stem:
        technology = "ev"
    else:
        technology = "base"

    if "geen_zp" in stem:
        pv_variant = "no_pv"
    elif "met_zp" in stem or "enkel_zp" in stem:
        pv_variant = "with_pv"
    else:
        pv_variant = "unspecified_pv"
    return technology, pv_variant


def _detect_timestamp_column(columns: list[str]) -> str:
    """Detect the timestamp column in a Fluvius CSV."""

    preferred = ["Datum_Startuur", "timestamp", "Timestamp", "datetime"]
    for column in preferred:
        if column in columns:
            return column
    for column in columns:
        if "datum" in column.lower() or "time" in column.lower():
            return column
    raise ValueError(f"Could not detect Fluvius timestamp column from: {columns}")


def _detect_value_column(columns: list[str]) -> str:
    """Detect the interval-energy column in a Fluvius CSV."""

    preferred = ["Volume_Afname_KWh", "Afname_kWh", "volume_kwh", "value"]
    for column in preferred:
        if column in columns:
            return column
    for column in columns:
        lowered = column.lower()
        if "kwh" in lowered and ("afname" in lowered or "volume" in lowered or "consum" in lowered):
            return column
    raise ValueError(f"Could not detect Fluvius energy column from: {columns}")


def _quality_checks(series: pd.Series, *, label: str) -> list[str]:
    """Run lightweight timestamp and NaN checks on a loaded profile."""

    warnings: list[str] = []
    if series.empty:
        warnings.append(f"{label}: profile is empty")
        return warnings
    deltas = series.index.to_series().sort_values().diff().dropna().dt.total_seconds()
    if not deltas.empty:
        modal_delta = int(deltas.mode().iloc[0])
        if modal_delta != 900 or (deltas != modal_delta).any():
            warnings.append(f"{label}: irregular sampling detected (modal timestep {modal_delta}s)")
        expected = pd.date_range(series.index.min(), series.index.max(), freq="15min", tz=series.index.tz)
        missing = int(len(expected.difference(series.index)))
        if missing > 0:
            warnings.append(f"{label}: missing timestamps detected ({missing})")
    nan_count = int(series.isna().sum())
    if nan_count > 0:
        warnings.append(f"{label}: NaN propagation detected ({nan_count})")
    return warnings


def load_fluvius_profiles(base_path: str | Path) -> dict[str, pd.Series]:
    """Load all Fluvius CSV files and return representative kW profiles by segment."""

    root = _resolve_base_path(base_path)
    csv_files = sorted(path for path in root.rglob("*.csv") if path.is_file())
    if not csv_files:
        raise FileNotFoundError(f"No Fluvius CSV files found under {root}")

    profiles: dict[str, pd.Series] = {}
    for csv_path in csv_files:
        technology, pv_variant = _profile_key(csv_path)
        profile_name = f"{technology}_{pv_variant}"
        timestamp_sums: defaultdict[pd.Timestamp, float] = defaultdict(float)
        timestamp_counts: defaultdict[pd.Timestamp, int] = defaultdict(int)

        sample = pd.read_csv(csv_path, nrows=5)
        timestamp_column = _detect_timestamp_column(list(sample.columns))
        value_column = _detect_value_column(list(sample.columns))

        for chunk in pd.read_csv(csv_path, usecols=[timestamp_column, value_column], chunksize=200_000):
            timestamps = pd.to_datetime(chunk[timestamp_column], utc=True, errors="coerce")
            values = pd.to_numeric(chunk[value_column], errors="coerce")
            frame = pd.DataFrame(
                {
                    "timestamp": timestamps.dt.tz_convert(_BELGIUM_TZ),
                    "power_kW": values / _INTERVAL_HOURS,
                }
            ).dropna(subset=["timestamp", "power_kW"])
            grouped = frame.groupby("timestamp", sort=True)["power_kW"].agg(["sum", "count"])
            for timestamp, row in grouped.iterrows():
                timestamp_sums[pd.Timestamp(timestamp)] += float(row["sum"])
                timestamp_counts[pd.Timestamp(timestamp)] += int(row["count"])

        if not timestamp_sums:
            LOGGER.warning("fluvius_loader.empty_profile file=%s", csv_path)
            continue

        series = pd.Series(
            {
                timestamp: timestamp_sums[timestamp] / max(timestamp_counts[timestamp], 1)
                for timestamp in timestamp_sums
            },
            dtype=float,
        ).sort_index()
        series.name = profile_name
        for warning in _quality_checks(series, label=profile_name):
            LOGGER.warning("fluvius_loader.warning %s", warning)
        profiles[profile_name] = series

    return profiles


def aggregate_fluvius_profiles(
    profiles: Mapping[str, pd.Series],
    profile_weights: Mapping[str, Any],
) -> tuple[pd.Series, dict[str, Any]]:
    """Build a weighted Fluvius representative profile in kW."""

    category_series: dict[str, pd.Series] = {}
    details: dict[str, Any] = {"category_components": {}, "warnings": []}

    for category_name, raw_weight in profile_weights.items():
        weight = float(raw_weight)
        category_name = str(category_name)
        matching = {
            profile_name: profile
            for profile_name, profile in profiles.items()
            if (
                profile_name == category_name
                or profile_name.split("_with_pv")[0] == category_name
                or profile_name.split("_no_pv")[0] == category_name
                or profile_name.split("_unspecified_pv")[0] == category_name
            )
        }
        if not matching:
            raise ValueError(f"No Fluvius profiles found for weighted category: {category_name}")

        matching_frame = pd.concat(matching.values(), axis=1, join="inner").sort_index()
        if matching_frame.empty:
            raise ValueError(f"Fluvius category {category_name} has no overlapping timestamps")
        if matching_frame.shape[1] > 1:
            warning = (
                f"{category_name}: multiple PV variants present ({', '.join(sorted(matching))}); "
                "using an unweighted within-category mean because no finer-grained PV split is configured"
            )
            LOGGER.warning("fluvius_loader.warning %s", warning)
            details["warnings"].append(warning)
        category_profile = matching_frame.mean(axis=1)
        category_series[category_name] = category_profile
        details["category_components"][category_name] = {
            "weight": weight,
            "profiles": sorted(matching),
        }

    combined_frame = pd.concat(category_series, axis=1, join="inner").sort_index()
    if combined_frame.empty:
        raise ValueError("Could not build a common Fluvius weighted profile across categories")

    total_profile = pd.Series(0.0, index=combined_frame.index, dtype=float)
    for category_name, raw_weight in profile_weights.items():
        total_profile = total_profile.add(combined_frame[str(category_name)] * float(raw_weight), fill_value=0.0)
    total_profile.name = "fluvius_weighted_profile_kW"

    for warning in _quality_checks(total_profile, label="fluvius_weighted_profile"):
        LOGGER.warning("fluvius_loader.warning %s", warning)
        details["warnings"].append(warning)
    return total_profile, details
