"""Per-leaf monthly energy and climate metrics for scenario-tree outputs."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd

from model_v3.utils.energy import infer_step_durations_seconds


MONTHLY_METADATA_COLUMNS = [
    "scenario_leaf_id",
    "scenario_id",
    "climate_window_id",
    "climate_pathway_id",
    "technology_case_id",
    "realization_id",
    "year",
    "month",
    "month_hours",
    "n_timesteps",
]

MONTHLY_ENERGY_COLUMNS = [
    "monthly_electricity_gross_kWh",
    "monthly_grid_import_kWh",
    "monthly_grid_export_kWh",
    "monthly_gas_kWh",
    "monthly_space_heating_useful_kWh",
    "monthly_dhw_kWh",
    "monthly_ev_charging_kWh",
    "monthly_pv_generation_kWh",
    "monthly_pv_self_consumption_kWh",
]

MONTHLY_CLIMATE_COLUMNS = [
    "monthly_mean_T_out_C",
    "monthly_HDD_15",
    "monthly_HDD_18",
    "monthly_CDD_22",
    "monthly_excess_heat_kWh",
    "monthly_overheating_hours",
    "monthly_indoor_temperature_exceedance_degree_hours",
    "monthly_max_indoor_temperature_C",
]

MONTHLY_COLUMNS = MONTHLY_METADATA_COLUMNS + MONTHLY_ENERGY_COLUMNS + MONTHLY_CLIMATE_COLUMNS

# Maps output column name → candidate power column names in annual_profile.csv
_POWER_CANDIDATES: dict[str, list[str]] = {
    "monthly_electricity_gross_kWh": ["P_el_gross_actual_W", "gross_electricity_W"],
    "monthly_grid_import_kWh": ["P_el_grid_import_W", "P_grid_import_W", "grid_import_W"],
    "monthly_grid_export_kWh": ["P_el_grid_export_W", "P_grid_export_W", "grid_export_W"],
    "monthly_gas_kWh": ["P_gas_total_W", "P_gas_space_heating_W", "gas_W"],
    "monthly_space_heating_useful_kWh": ["Q_heating_supplied_W", "Q_useful_space_heating_W", "useful_heating_W"],
    "monthly_dhw_kWh": ["Q_dhw_demand_W", "Q_dhw_W", "useful_dhw_W"],
    "monthly_ev_charging_kWh": ["P_el_ev_charging_W", "ev_charging_W", "P_ev_charging_W"],
    "monthly_pv_generation_kWh": ["P_pv_generation_W", "pv_generation_W", "P_solar_generation_W"],
    "monthly_excess_heat_kWh": ["Q_excess_heat_W"],
}


class MonthlyMetricsError(RuntimeError):
    """Raised when monthly metrics cannot be computed."""


@dataclass
class MonthlyMetricResult:
    rows: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _find_column(frame: pd.DataFrame, candidates: list[str]) -> str | None:
    for col in candidates:
        if col in frame.columns:
            return col
        kw = col.replace("_W", "_kW")
        if kw in frame.columns:
            return kw
    return None


def _power_to_kwh(series: pd.Series, col: str, durations_h: pd.Series) -> pd.Series:
    """Convert a power series (W or kW) to kWh using per-row step durations."""
    multiplier = 1000.0 if col.endswith("_kW") or col.endswith("_KW") else 1.0
    return pd.to_numeric(series, errors="coerce").fillna(0.0) * multiplier * durations_h / 1000.0


def compute_monthly_metrics(
    frame: pd.DataFrame,
    *,
    leaf_id: str,
    scenario_id: str,
    climate_window_id: str,
    climate_pathway_id: str,
    technology_case_id: str,
    realization_id: str,
    overheating_threshold_C: float = 26.0,
) -> MonthlyMetricResult:
    """Compute monthly energy and climate metrics from a leaf timeseries DataFrame."""

    result = MonthlyMetricResult()

    if "timestamp" not in frame.columns:
        raise MonthlyMetricsError(f"No 'timestamp' column in timeseries for {leaf_id}")

    timestamps = pd.to_datetime(frame["timestamp"], utc=True)
    durations_s = pd.Series(
        infer_step_durations_seconds(list(timestamps)),
        index=frame.index,
        dtype=float,
    )
    durations_h = durations_s / 3600.0

    years = timestamps.dt.year
    months = timestamps.dt.month

    # Resolve power columns once
    resolved: dict[str, str | None] = {
        metric: _find_column(frame, candidates)
        for metric, candidates in _POWER_CANDIDATES.items()
    }

    # Pre-compute energy series for each metric
    energy_series: dict[str, pd.Series] = {}
    warnings: list[str] = []
    for metric, col in resolved.items():
        if col is None:
            if metric not in ("monthly_ev_charging_kWh", "monthly_pv_generation_kWh", "monthly_excess_heat_kWh"):
                warnings.append(f"{leaf_id}: column missing for {metric}")
            energy_series[metric] = pd.Series(0.0, index=frame.index)
        else:
            energy_series[metric] = _power_to_kwh(frame[col], col, durations_h)

    # PV self-consumption = pv_generation − grid_export, clamped to [0, pv_generation]
    pv_gen = energy_series["monthly_pv_generation_kWh"]
    pv_exp = energy_series["monthly_grid_export_kWh"]
    energy_series["monthly_pv_self_consumption_kWh"] = (pv_gen - pv_exp).clip(lower=0.0)

    # Outdoor temperature column
    t_col = "T_outdoor_C" if "T_outdoor_C" in frame.columns else None
    t_series: pd.Series | None = (
        pd.to_numeric(frame[t_col], errors="coerce") if t_col else None
    )
    t_indoor_series: pd.Series | None = (
        pd.to_numeric(frame["T_indoor_next_C"], errors="coerce") if "T_indoor_next_C" in frame.columns else None
    )

    group_keys = list(zip(years, months))
    unique_groups = sorted(set(group_keys))

    for year, month in unique_groups:
        mask = (years == year) & (months == month)
        sub = frame.index[mask]
        n = int(mask.sum())
        month_h = float(durations_h[mask].sum())

        row: dict[str, Any] = {
            "scenario_leaf_id": leaf_id,
            "scenario_id": scenario_id,
            "climate_window_id": climate_window_id,
            "climate_pathway_id": climate_pathway_id,
            "technology_case_id": technology_case_id,
            "realization_id": realization_id,
            "year": int(year),
            "month": int(month),
            "month_hours": round(month_h, 3),
            "n_timesteps": n,
        }

        for metric in MONTHLY_ENERGY_COLUMNS:
            row[metric] = round(float(energy_series[metric][mask].sum()), 6)

        # Climate metrics — require temperature column
        if t_series is not None:
            t_month = t_series[mask]
            row["monthly_mean_T_out_C"] = round(float(t_month.mean()), 4)
            row["monthly_HDD_15"] = round(float(np.maximum(0.0, 15.0 - t_month).sum()), 3)
            row["monthly_HDD_18"] = round(float(np.maximum(0.0, 18.0 - t_month).sum()), 3)
            row["monthly_CDD_22"] = round(float(np.maximum(0.0, t_month - 22.0).sum()), 3)
        else:
            row["monthly_mean_T_out_C"] = float("nan")
            row["monthly_HDD_15"] = float("nan")
            row["monthly_HDD_18"] = float("nan")
            row["monthly_CDD_22"] = float("nan")

        row["monthly_excess_heat_kWh"] = round(float(energy_series["monthly_excess_heat_kWh"][mask].sum()), 6)
        if t_indoor_series is not None:
            t_indoor_month = t_indoor_series[mask]
            exceedance_c = (t_indoor_month - float(overheating_threshold_C)).clip(lower=0.0)
            row["monthly_overheating_hours"] = round(float(durations_h[mask][exceedance_c > 0.0].sum()), 6)
            row["monthly_indoor_temperature_exceedance_degree_hours"] = round(float((exceedance_c * durations_h[mask]).sum()), 6)
            row["monthly_max_indoor_temperature_C"] = round(float(t_indoor_month.max()), 4)
        else:
            row["monthly_overheating_hours"] = float("nan")
            row["monthly_indoor_temperature_exceedance_degree_hours"] = float("nan")
            row["monthly_max_indoor_temperature_C"] = float("nan")

        result.rows.append({col: row.get(col, float("nan")) for col in MONTHLY_COLUMNS})

    result.warnings = warnings
    return result


def write_monthly_metrics(rows: list[dict[str, Any]], outputs_dir: Path) -> Path:
    """Write monthly metrics CSV for one scenario leaf."""

    path = Path(outputs_dir) / "monthly_metrics.csv"
    pd.DataFrame(rows, columns=MONTHLY_COLUMNS).to_csv(path, index=False)
    return path


def aggregate_monthly_metrics(all_rows: list[dict[str, Any]]) -> pd.DataFrame:
    """Aggregate per-leaf monthly rows to scenario level (mean across realizations)."""

    if not all_rows:
        return pd.DataFrame(columns=MONTHLY_COLUMNS)

    df = pd.DataFrame(all_rows, columns=MONTHLY_COLUMNS)
    group_cols = ["scenario_id", "climate_window_id", "climate_pathway_id", "technology_case_id", "year", "month"]
    agg_rows: list[dict[str, Any]] = []

    for group_vals, group in df.groupby(group_cols, dropna=False):
        row: dict[str, Any] = dict(zip(group_cols, group_vals))
        row["n_realizations"] = int(len(group))
        row["month_hours"] = float(group["month_hours"].mean())

        for col in MONTHLY_ENERGY_COLUMNS + MONTHLY_CLIMATE_COLUMNS:
            series = pd.to_numeric(group[col], errors="coerce")
            row[f"{col}_mean"] = float(series.mean()) if series.count() else float("nan")
            row[f"{col}_p10"] = float(series.quantile(0.10)) if series.count() else float("nan")
            row[f"{col}_p50"] = float(series.quantile(0.50)) if series.count() else float("nan")
            row[f"{col}_p90"] = float(series.quantile(0.90)) if series.count() else float("nan")

        agg_rows.append(row)

    return pd.DataFrame(agg_rows).sort_values(group_cols).reset_index(drop=True)
