"""Output 5 scenario-based household energy bill post-processing."""

from __future__ import annotations

import argparse
import ast
import csv
import math
from pathlib import Path
from typing import Any, Iterable, Mapping

import matplotlib

matplotlib.use("Agg", force=True)
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import yaml

from model_v3.scenario_tree.naming import DIMENSION_SEPARATOR
from model_v3.scenarios.registry import latest_actual_status, read_registry
from model_v3.utils.energy import infer_step_durations_seconds


REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_EXPERIMENT_ROOT = REPO_ROOT / "experiments" / "scenario_tree_output34"
DEFAULT_LEAF_INDEX = DEFAULT_EXPERIMENT_ROOT / "manifests" / "output34_leaf_index.csv"
DEFAULT_RUN_REGISTRY = DEFAULT_EXPERIMENT_ROOT / "manifests" / "run_registry.csv"
DEFAULT_TARIFF_CONFIG = REPO_ROOT / "config" / "scenario_tree" / "output5_tariffs.yaml"
DEFAULT_FIGURES_ROOT = REPO_ROOT / "figures" / "scenario_tree_output34"
BASELINE_SCENARIO_ID = "baseline_1981_2005__historical__tech_current_stock"
ACTIVE_COOLING_COLUMN_NAMES = {
    "active_cooling_final_energy_kWh",
    "cooling_final_energy_kWh",
    "cooling_electricity_kWh",
}
OTHER_FUEL_COLUMNS = (
    "P_oil_total_W",
    "P_biomass_total_W",
    "P_propane_total_W",
    "P_coal_total_W",
    "P_district_heat_total_W",
)
GROUP_COLUMNS = [
    "scenario_id",
    "climate_window_id",
    "climate_pathway_id",
    "technology_case_id",
    "design_year_id",
    "design_year",
    "tariff_scenario_id",
    "tariff_scenario_label",
]


class Output5Error(RuntimeError):
    """Raised when Output 5 billing post-processing cannot be completed."""


def _resolve_repo_path(path_text: str | Path) -> Path:
    path = Path(path_text)
    if path.is_absolute():
        return path
    return REPO_ROOT / path


def _load_yaml(path: Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict):
        raise Output5Error(f"YAML file must contain a mapping: {path}")
    return data


def _write_csv(path: Path, rows: list[Mapping[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _durations_hours(timestamps: pd.Series) -> pd.Series:
    durations = infer_step_durations_seconds(list(timestamps))
    return pd.Series(durations, index=timestamps.index, dtype=float) / 3600.0


def _power_to_kwh(frame: pd.DataFrame, column: str, durations_h: pd.Series) -> pd.Series:
    if column not in frame:
        return pd.Series(np.zeros(len(frame)), index=frame.index, dtype=float)
    return pd.to_numeric(frame[column], errors="coerce").fillna(0.0) * durations_h / 1000.0


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _summary_stats(series: pd.Series, prefix: str) -> dict[str, float]:
    numeric = pd.to_numeric(series, errors="coerce").dropna()
    if numeric.empty:
        return {
            f"{prefix}_mean": float("nan"),
            f"{prefix}_p10": float("nan"),
            f"{prefix}_p50": float("nan"),
            f"{prefix}_p90": float("nan"),
        }
    return {
        f"{prefix}_mean": float(numeric.mean()),
        f"{prefix}_p10": float(numeric.quantile(0.10)),
        f"{prefix}_p50": float(numeric.quantile(0.50)),
        f"{prefix}_p90": float(numeric.quantile(0.90)),
    }


def _pct_delta(value: float, baseline: float) -> float:
    if not math.isfinite(value) or not math.isfinite(baseline) or abs(baseline) <= 1e-12:
        return float("nan")
    return (value - baseline) / baseline * 100.0


def _timestamp_local(frame: pd.DataFrame, timezone: str) -> pd.Series:
    if "timestamp" not in frame:
        raise Output5Error("Profile frame must contain a timestamp column.")
    return pd.to_datetime(frame["timestamp"], utc=True).dt.tz_convert(timezone)


def load_tariff_config(path: Path = DEFAULT_TARIFF_CONFIG) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    config = _load_yaml(Path(path))
    defaults = dict(config.get("defaults", {}))
    rows = []
    for raw in config.get("tariff_scenarios", []):
        if not isinstance(raw, Mapping):
            continue
        tariff = {**defaults, **dict(raw)}
        tariff["tariff_scenario_id"] = str(tariff["tariff_scenario_id"])
        tariff["label"] = str(tariff.get("label", tariff["tariff_scenario_id"]))
        tariff["dynamic"] = bool(tariff.get("dynamic", False))
        rows.append(tariff)
    if not rows:
        raise Output5Error(f"No tariff scenarios found in {path}")
    return config, rows


def tariff_assumption_rows(config: Mapping[str, Any], tariffs: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    metadata = dict(config.get("metadata", {}))
    source_notes = metadata.get("source_notes", [])
    source_text = "; ".join(f"{item.get('name')}: {item.get('url')}" for item in source_notes if isinstance(item, Mapping))
    rows = []
    for tariff in tariffs:
        dynamic_profile = dict(tariff.get("dynamic_profile", {}) or {})
        rows.append(
            {
                "tariff_scenario_id": tariff["tariff_scenario_id"],
                "tariff_scenario_label": tariff["label"],
                "purpose": tariff.get("purpose", ""),
                "electricity_import_eur_per_kwh": _safe_float(tariff.get("electricity_import_eur_per_kwh")),
                "gas_eur_per_kwh": _safe_float(tariff.get("gas_eur_per_kwh")),
                "pv_export_eur_per_kwh": _safe_float(tariff.get("pv_export_eur_per_kwh")),
                "fixed_annual_eur_per_household": _safe_float(tariff.get("fixed_annual_eur_per_household")),
                "capacity_eur_per_kw_year": _safe_float(tariff.get("capacity_eur_per_kw_year")),
                "monthly_peak_floor_kw": _safe_float(tariff.get("monthly_peak_floor_kw")),
                "dynamic": bool(tariff.get("dynamic", False)),
                "dynamic_low_eur_per_kwh": _safe_float(dynamic_profile.get("low_eur_per_kwh"), float("nan")),
                "dynamic_shoulder_eur_per_kwh": _safe_float(dynamic_profile.get("shoulder_eur_per_kwh"), float("nan")),
                "dynamic_high_eur_per_kwh": _safe_float(dynamic_profile.get("high_eur_per_kwh"), float("nan")),
                "price_basis": metadata.get("price_basis", ""),
                "source_references": source_text,
                "interpretation_note": metadata.get("interpretation_note", ""),
                "capacity_tariff_note": metadata.get("capacity_tariff_note", ""),
                "non_gas_fuel_note": metadata.get("non_gas_fuel_note", ""),
            }
        )
    return rows


def _dynamic_rates(tariff: Mapping[str, Any], local_timestamps: pd.Series) -> pd.Series:
    if not bool(tariff.get("dynamic", False)):
        return pd.Series(
            np.full(len(local_timestamps), _safe_float(tariff.get("electricity_import_eur_per_kwh"))),
            index=local_timestamps.index,
            dtype=float,
        )
    profile = dict(tariff.get("dynamic_profile", {}) or {})
    low_hours = set(int(hour) for hour in profile.get("low_hours", []))
    high_hours = set(int(hour) for hour in profile.get("high_hours", []))
    low = _safe_float(profile.get("low_eur_per_kwh"), _safe_float(tariff.get("electricity_import_eur_per_kwh")))
    shoulder = _safe_float(profile.get("shoulder_eur_per_kwh"), _safe_float(tariff.get("electricity_import_eur_per_kwh")))
    high = _safe_float(profile.get("high_eur_per_kwh"), _safe_float(tariff.get("electricity_import_eur_per_kwh")))
    rates = []
    for hour in local_timestamps.dt.hour:
        if int(hour) in low_hours:
            rates.append(low)
        elif int(hour) in high_hours:
            rates.append(high)
        else:
            rates.append(shoulder)
    return pd.Series(rates, index=local_timestamps.index, dtype=float)


def _monthly_energy_from_profile(profile: pd.DataFrame, tariff: Mapping[str, Any]) -> pd.DataFrame:
    timezone = str(tariff.get("timezone", "Europe/Brussels"))
    local_ts = _timestamp_local(profile, timezone)
    durations_h = _durations_hours(local_ts)
    import_kwh = _power_to_kwh(profile, "P_el_grid_import_W", durations_h)
    export_kwh = _power_to_kwh(profile, "P_el_grid_export_W", durations_h)
    gas_kwh = _power_to_kwh(profile, "P_gas_total_W", durations_h)
    other_fuel_kwh = sum((_power_to_kwh(profile, column, durations_h) for column in OTHER_FUEL_COLUMNS), pd.Series(0.0, index=profile.index))
    rates = _dynamic_rates(tariff, local_ts)
    import_cost = import_kwh * rates
    month_key = pd.DataFrame({"year": local_ts.dt.year, "month": local_ts.dt.month}, index=profile.index)
    rows = []
    for (year, month), group in month_key.groupby(["year", "month"], sort=True):
        index = group.index
        rows.append(
            {
                "year": int(year),
                "month": int(month),
                "monthly_grid_import_kWh": float(import_kwh.loc[index].sum()),
                "monthly_grid_export_kWh": float(export_kwh.loc[index].sum()),
                "monthly_gas_kWh": float(gas_kwh.loc[index].sum()),
                "monthly_unpriced_non_gas_fuel_kWh": float(other_fuel_kwh.loc[index].sum()),
                "monthly_import_energy_cost_EUR": float(import_cost.loc[index].sum()),
                "monthly_grid_export_credit_EUR": float(export_kwh.loc[index].sum() * _safe_float(tariff.get("pv_export_eur_per_kwh"))),
                "monthly_gas_cost_EUR": float(gas_kwh.loc[index].sum() * _safe_float(tariff.get("gas_eur_per_kwh"))),
                "monthly_peak_grid_import_W": float(pd.to_numeric(profile.loc[index, "P_el_grid_import_W"], errors="coerce").fillna(0.0).max())
                if "P_el_grid_import_W" in profile
                else 0.0,
            }
        )
    return pd.DataFrame(rows)


def _capacity_costs_from_matrix(
    matrix_path: Path,
    *,
    tariff: Mapping[str, Any],
) -> tuple[pd.Series | None, dict[tuple[int, int], float] | None, str]:
    if not matrix_path.exists():
        return None, None, "aggregate_profile_peak_approximation"
    matrix = pd.read_csv(matrix_path)
    if "timestamp" not in matrix:
        return None, None, "aggregate_profile_peak_approximation"
    value_columns = [column for column in matrix.columns if column != "timestamp"]
    if not value_columns:
        return None, None, "aggregate_profile_peak_approximation"
    timezone = str(tariff.get("timezone", "Europe/Brussels"))
    local_ts = pd.to_datetime(matrix["timestamp"], utc=True).dt.tz_convert(timezone)
    values_kw = matrix[value_columns].apply(pd.to_numeric, errors="coerce").fillna(0.0) / 1000.0
    values_kw["year"] = local_ts.dt.year
    values_kw["month"] = local_ts.dt.month
    monthly_peaks = values_kw.groupby(["year", "month"], sort=True)[value_columns].max()
    floor = _safe_float(tariff.get("monthly_peak_floor_kw"))
    rate = _safe_float(tariff.get("capacity_eur_per_kw_year"))
    billing_kw = monthly_peaks.clip(lower=floor)
    annual_capacity_by_household = billing_kw.mean(axis=0) * rate
    monthly_capacity = (billing_kw.sum(axis=1) * rate / 12.0).to_dict()
    return annual_capacity_by_household.astype(float), monthly_capacity, "household_monthly_peak_average_hourly_approximation"


def _capacity_costs_from_aggregate(
    monthly_energy: pd.DataFrame,
    *,
    tariff: Mapping[str, Any],
    n_households: int,
) -> tuple[float, dict[tuple[int, int], float], str]:
    floor = _safe_float(tariff.get("monthly_peak_floor_kw"))
    rate = _safe_float(tariff.get("capacity_eur_per_kw_year"))
    monthly = monthly_energy.copy()
    monthly["per_household_peak_kw"] = monthly["monthly_peak_grid_import_W"] / 1000.0 / max(int(n_households), 1)
    monthly["billing_kw"] = monthly["per_household_peak_kw"].clip(lower=floor)
    monthly_cost = {
        (int(row["year"]), int(row["month"])): float(row["billing_kw"] * rate / 12.0 * max(int(n_households), 1))
        for _, row in monthly.iterrows()
    }
    annual_cost = float(sum(monthly_cost.values()))
    return annual_cost, monthly_cost, "aggregate_profile_peak_per_household_floor_hourly_approximation"


def _parse_carrier_dict(value: Any) -> dict[str, float]:
    if isinstance(value, Mapping):
        raw = value
    else:
        try:
            raw = ast.literal_eval(str(value))
        except (SyntaxError, ValueError):
            raw = {}
    return {str(key): _safe_float(val) for key, val in dict(raw).items()}


def _household_dynamic_import_costs(matrix_path: Path, tariff: Mapping[str, Any]) -> pd.Series | None:
    if not bool(tariff.get("dynamic", False)) or not matrix_path.exists():
        return None
    matrix = pd.read_csv(matrix_path)
    if "timestamp" not in matrix:
        return None
    value_columns = [column for column in matrix.columns if column != "timestamp"]
    if not value_columns:
        return None
    timezone = str(tariff.get("timezone", "Europe/Brussels"))
    local_ts = pd.to_datetime(matrix["timestamp"], utc=True).dt.tz_convert(timezone)
    durations_h = _durations_hours(local_ts)
    rates = _dynamic_rates(tariff, local_ts)
    values = matrix[value_columns].apply(pd.to_numeric, errors="coerce").fillna(0.0)
    costs = values.mul(durations_h * rates / 1000.0, axis=0).sum(axis=0)
    return costs.astype(float)


def _household_bill_stats(
    household_path: Path,
    matrix_path: Path,
    *,
    tariff: Mapping[str, Any],
) -> dict[str, float]:
    if not household_path.exists():
        return {
            "household_bill_EUR_count": 0,
            "household_bill_EUR_mean": float("nan"),
            "household_bill_EUR_p10": float("nan"),
            "household_bill_EUR_p50": float("nan"),
            "household_bill_EUR_p90": float("nan"),
        }
    household = pd.read_csv(household_path)
    capacity, _, _ = _capacity_costs_from_matrix(matrix_path, tariff=tariff)
    if capacity is None:
        capacity = pd.Series(
            np.full(len(household), _safe_float(tariff.get("monthly_peak_floor_kw")) * _safe_float(tariff.get("capacity_eur_per_kw_year"))),
            index=household.index,
            dtype=float,
        )
    dynamic_import_costs = _household_dynamic_import_costs(matrix_path, tariff)
    bills = []
    for index, row in household.iterrows():
        carriers = _parse_carrier_dict(row.get("annual_energy_by_carrier_kWh", {}))
        import_kwh = carriers.get("electricity_grid_import", 0.0)
        export_kwh = carriers.get("electricity_grid_export", 0.0)
        gas_kwh = carriers.get("natural_gas", 0.0)
        household_id = str(row.get("household_id", ""))
        if dynamic_import_costs is not None and household_id in dynamic_import_costs.index:
            import_cost = float(dynamic_import_costs.loc[household_id])
        else:
            import_cost = import_kwh * _safe_float(tariff.get("electricity_import_eur_per_kwh"))
        fixed = _safe_float(tariff.get("fixed_annual_eur_per_household"))
        capacity_cost = float(capacity.iloc[index]) if index < len(capacity) else 0.0
        bills.append(
            import_cost
            + gas_kwh * _safe_float(tariff.get("gas_eur_per_kwh"))
            - export_kwh * _safe_float(tariff.get("pv_export_eur_per_kwh"))
            + fixed
            + capacity_cost
        )
    values = pd.Series(bills, dtype=float).dropna()
    if values.empty:
        return {
            "household_bill_EUR_count": 0,
            "household_bill_EUR_mean": float("nan"),
            "household_bill_EUR_p10": float("nan"),
            "household_bill_EUR_p50": float("nan"),
            "household_bill_EUR_p90": float("nan"),
        }
    return {
        "household_bill_EUR_count": int(len(values)),
        "household_bill_EUR_mean": float(values.mean()),
        "household_bill_EUR_p10": float(values.quantile(0.10)),
        "household_bill_EUR_p50": float(values.quantile(0.50)),
        "household_bill_EUR_p90": float(values.quantile(0.90)),
    }


def _metadata(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "scenario_leaf_id": row["scenario_leaf_id"],
        "scenario_id": row["scenario_id"],
        "climate_window_id": row.get("climate_window_id", ""),
        "climate_pathway_id": row.get("climate_pathway_id", ""),
        "technology_case_id": row.get("technology_case_id", ""),
        "design_year_id": row.get("design_year_id", ""),
        "design_year": int(row.get("design_year", 0) or 0),
        "realization_id": row.get("realization_id", ""),
    }


def _leaf_bill_rows(
    leaf: Mapping[str, Any],
    *,
    tariff: Mapping[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    meta = _metadata(leaf)
    outputs_dir = _resolve_repo_path(str(leaf["outputs_dir"]))
    profile_path = outputs_dir / "annual_profile.csv"
    if not profile_path.exists():
        raise Output5Error(f"Missing annual_profile.csv for Output 5 leaf: {profile_path}")
    profile = pd.read_csv(profile_path)
    n_households = int(meta.get("cohort_size", 0) or 0)
    if n_households <= 0:
        n_households = int(dict(profile.attrs).get("n_households", 0) or 0)
    if n_households <= 0:
        n_households = int(pd.to_numeric(profile.get("aggregate_profile_W", pd.Series(dtype=float)), errors="coerce").count() > 0) or 1
    if "cohort_size" in leaf:
        n_households = int(leaf.get("cohort_size") or n_households)

    monthly = _monthly_energy_from_profile(profile, tariff)
    matrix_path = outputs_dir / "household_grid_import_matrix.csv"
    capacity_by_household, monthly_capacity, capacity_method = _capacity_costs_from_matrix(matrix_path, tariff=tariff)
    if monthly_capacity is None:
        capacity_annual_cost, monthly_capacity, capacity_method = _capacity_costs_from_aggregate(
            monthly,
            tariff=tariff,
            n_households=n_households,
        )
    else:
        capacity_annual_cost = float(capacity_by_household.sum()) if capacity_by_household is not None else 0.0

    fixed_annual = _safe_float(tariff.get("fixed_annual_eur_per_household")) * n_households
    annual_import_cost = float(monthly["monthly_import_energy_cost_EUR"].sum())
    annual_gas_cost = float(monthly["monthly_gas_cost_EUR"].sum())
    annual_export_credit = float(monthly["monthly_grid_export_credit_EUR"].sum())
    total = annual_import_cost + annual_gas_cost - annual_export_credit + fixed_annual + capacity_annual_cost

    annual_row = {
        **meta,
        "tariff_scenario_id": tariff["tariff_scenario_id"],
        "tariff_scenario_label": tariff["label"],
        "tariff_is_dynamic": bool(tariff.get("dynamic", False)),
        "n_households": n_households,
        "billing_scope": "electricity_gas_export_fixed_capacity",
        "capacity_cost_method": capacity_method,
        "active_cooling_final_energy_kWh_included": False,
        "annual_grid_import_kWh": float(monthly["monthly_grid_import_kWh"].sum()),
        "annual_grid_export_kWh": float(monthly["monthly_grid_export_kWh"].sum()),
        "annual_gas_kWh": float(monthly["monthly_gas_kWh"].sum()),
        "annual_unpriced_non_gas_fuel_kWh": float(monthly["monthly_unpriced_non_gas_fuel_kWh"].sum()),
        "annual_import_energy_cost_EUR": annual_import_cost,
        "annual_gas_cost_EUR": annual_gas_cost,
        "annual_grid_export_credit_EUR": annual_export_credit,
        "annual_fixed_cost_EUR": fixed_annual,
        "annual_capacity_cost_EUR": capacity_annual_cost,
        "annual_bill_EUR": total,
        "annual_bill_per_household_EUR": total / max(n_households, 1),
    }
    annual_row.update(
        _household_bill_stats(
            outputs_dir / "household_annual_energy.csv",
            matrix_path,
            tariff=tariff,
        )
    )

    monthly_rows: list[dict[str, Any]] = []
    for _, row in monthly.iterrows():
        key = (int(row["year"]), int(row["month"]))
        capacity_cost = float(monthly_capacity.get(key, 0.0))
        fixed_cost = fixed_annual / 12.0
        monthly_bill = (
            float(row["monthly_import_energy_cost_EUR"])
            + float(row["monthly_gas_cost_EUR"])
            - float(row["monthly_grid_export_credit_EUR"])
            + fixed_cost
            + capacity_cost
        )
        monthly_rows.append(
            {
                **meta,
                "tariff_scenario_id": tariff["tariff_scenario_id"],
                "tariff_scenario_label": tariff["label"],
                "tariff_is_dynamic": bool(tariff.get("dynamic", False)),
                "n_households": n_households,
                "year": int(row["year"]),
                "month": int(row["month"]),
                "billing_scope": "electricity_gas_export_fixed_capacity",
                "capacity_cost_method": capacity_method,
                "active_cooling_final_energy_kWh_included": False,
                "monthly_grid_import_kWh": float(row["monthly_grid_import_kWh"]),
                "monthly_grid_export_kWh": float(row["monthly_grid_export_kWh"]),
                "monthly_gas_kWh": float(row["monthly_gas_kWh"]),
                "monthly_unpriced_non_gas_fuel_kWh": float(row["monthly_unpriced_non_gas_fuel_kWh"]),
                "monthly_import_energy_cost_EUR": float(row["monthly_import_energy_cost_EUR"]),
                "monthly_gas_cost_EUR": float(row["monthly_gas_cost_EUR"]),
                "monthly_grid_export_credit_EUR": float(row["monthly_grid_export_credit_EUR"]),
                "monthly_fixed_cost_EUR": fixed_cost,
                "monthly_capacity_cost_EUR": capacity_cost,
                "monthly_bill_EUR": monthly_bill,
                "monthly_bill_per_household_EUR": monthly_bill / max(n_households, 1),
            }
        )
    return annual_row, monthly_rows


def _load_successful_leaf_index(leaf_index: Path, run_registry: Path) -> pd.DataFrame:
    index = pd.read_csv(leaf_index)
    registry_rows = read_registry(run_registry)
    statuses = {leaf_id: latest_actual_status(registry_rows, str(leaf_id)) for leaf_id in index["scenario_leaf_id"]}
    index["latest_status"] = index["scenario_leaf_id"].map(statuses)
    selected = index[index["latest_status"] == "success"].copy()
    if selected.empty:
        raise Output5Error("No successful leaves found for Output 5 billing.")
    for path_col in ["outputs_dir", "run_config_path"]:
        if path_col not in selected:
            raise Output5Error(f"Leaf index missing required column: {path_col}")
    # Read cohort size from run config when available.
    cohort_sizes = []
    for _, row in selected.iterrows():
        run_config_path = _resolve_repo_path(str(row["run_config_path"]))
        if run_config_path.exists():
            run_config = _load_yaml(run_config_path)
            cohort_sizes.append(int(dict(run_config.get("stochastic", {})).get("cohort_size", 0) or 0))
        else:
            cohort_sizes.append(0)
    selected["cohort_size"] = cohort_sizes
    return selected


def _aggregate_annual(leaf_annual: pd.DataFrame) -> pd.DataFrame:
    metrics = [
        "annual_grid_import_kWh",
        "annual_grid_export_kWh",
        "annual_gas_kWh",
        "annual_unpriced_non_gas_fuel_kWh",
        "annual_import_energy_cost_EUR",
        "annual_gas_cost_EUR",
        "annual_grid_export_credit_EUR",
        "annual_fixed_cost_EUR",
        "annual_capacity_cost_EUR",
        "annual_bill_EUR",
        "annual_bill_per_household_EUR",
        "household_bill_EUR_mean",
        "household_bill_EUR_p10",
        "household_bill_EUR_p50",
        "household_bill_EUR_p90",
    ]
    rows = []
    for values, group in leaf_annual.groupby(GROUP_COLUMNS, dropna=False):
        row = dict(zip(GROUP_COLUMNS, values))
        row["n_successful_runs"] = int(group["scenario_leaf_id"].nunique())
        row["n_households"] = int(pd.to_numeric(group["n_households"], errors="coerce").median())
        row["billing_scope"] = str(group["billing_scope"].iloc[0])
        row["capacity_cost_method"] = str(group["capacity_cost_method"].iloc[0])
        row["active_cooling_final_energy_kWh_included"] = False
        for metric in metrics:
            row.update(_summary_stats(group[metric], metric))
        rows.append(row)
    result = pd.DataFrame(rows).sort_values(GROUP_COLUMNS).reset_index(drop=True)
    baseline_means = {
        (str(row["design_year_id"]), str(row["tariff_scenario_id"])): float(row["annual_bill_per_household_EUR_mean"])
        for _, row in result[result["scenario_id"] == BASELINE_SCENARIO_ID].iterrows()
    }
    for index, row in result.iterrows():
        key = (str(row["design_year_id"]), str(row["tariff_scenario_id"]))
        baseline = baseline_means.get(key, float("nan"))
        value = float(row["annual_bill_per_household_EUR_mean"])
        result.loc[index, "baseline_scenario_id"] = BASELINE_SCENARIO_ID
        result.loc[index, "baseline_annual_bill_per_household_EUR_mean"] = baseline
        result.loc[index, "delta_annual_bill_per_household_EUR_abs"] = value - baseline if math.isfinite(value) and math.isfinite(baseline) else float("nan")
        result.loc[index, "delta_annual_bill_per_household_EUR_pct"] = _pct_delta(value, baseline)
    return result


def _aggregate_components(annual_comparison: pd.DataFrame) -> pd.DataFrame:
    component_metrics = [
        "annual_import_energy_cost_EUR",
        "annual_gas_cost_EUR",
        "annual_grid_export_credit_EUR",
        "annual_fixed_cost_EUR",
        "annual_capacity_cost_EUR",
        "annual_bill_EUR",
        "annual_bill_per_household_EUR",
        "annual_unpriced_non_gas_fuel_kWh",
    ]
    rows = []
    baseline = annual_comparison[annual_comparison["scenario_id"] == BASELINE_SCENARIO_ID].copy()
    baseline_map = {
        (str(row["design_year_id"]), str(row["tariff_scenario_id"]), metric): float(row[f"{metric}_mean"])
        for _, row in baseline.iterrows()
        for metric in component_metrics
    }
    for _, source in annual_comparison.iterrows():
        row = {column: source[column] for column in GROUP_COLUMNS}
        row["baseline_scenario_id"] = BASELINE_SCENARIO_ID
        for metric in component_metrics:
            value = float(source.get(f"{metric}_mean", float("nan")))
            base = baseline_map.get((str(source["design_year_id"]), str(source["tariff_scenario_id"]), metric), float("nan"))
            row[f"{metric}_mean"] = value
            row[f"baseline_{metric}_mean"] = base
            row[f"delta_{metric}_abs"] = value - base if math.isfinite(value) and math.isfinite(base) else float("nan")
        rows.append(row)
    return pd.DataFrame(rows).sort_values(GROUP_COLUMNS).reset_index(drop=True)


def _aggregate_monthly(leaf_monthly: pd.DataFrame) -> pd.DataFrame:
    metrics = [
        "monthly_grid_import_kWh",
        "monthly_grid_export_kWh",
        "monthly_gas_kWh",
        "monthly_unpriced_non_gas_fuel_kWh",
        "monthly_import_energy_cost_EUR",
        "monthly_gas_cost_EUR",
        "monthly_grid_export_credit_EUR",
        "monthly_fixed_cost_EUR",
        "monthly_capacity_cost_EUR",
        "monthly_bill_EUR",
        "monthly_bill_per_household_EUR",
    ]
    group_cols = GROUP_COLUMNS + ["month"]
    rows = []
    for values, group in leaf_monthly.groupby(group_cols, dropna=False):
        row = dict(zip(group_cols, values))
        row["n_month_samples"] = int(len(group))
        row["n_households"] = int(pd.to_numeric(group["n_households"], errors="coerce").median())
        row["billing_scope"] = str(group["billing_scope"].iloc[0])
        row["capacity_cost_method"] = str(group["capacity_cost_method"].iloc[0])
        row["active_cooling_final_energy_kWh_included"] = False
        for metric in metrics:
            row.update(_summary_stats(group[metric], metric))
        rows.append(row)
    result = pd.DataFrame(rows).sort_values(group_cols).reset_index(drop=True)
    baseline = result[result["scenario_id"] == BASELINE_SCENARIO_ID]
    baseline_map = {
        (str(row["design_year_id"]), str(row["tariff_scenario_id"]), int(row["month"])): float(row["monthly_bill_per_household_EUR_mean"])
        for _, row in baseline.iterrows()
    }
    for index, row in result.iterrows():
        key = (str(row["design_year_id"]), str(row["tariff_scenario_id"]), int(row["month"]))
        base = baseline_map.get(key, float("nan"))
        value = float(row["monthly_bill_per_household_EUR_mean"])
        result.loc[index, "baseline_scenario_id"] = BASELINE_SCENARIO_ID
        result.loc[index, "baseline_monthly_bill_per_household_EUR_mean"] = base
        result.loc[index, "delta_monthly_bill_per_household_EUR_abs"] = value - base if math.isfinite(value) and math.isfinite(base) else float("nan")
        result.loc[index, "delta_monthly_bill_per_household_EUR_pct"] = _pct_delta(value, base)
    return result


def build_output5_tables(
    *,
    experiment_root: Path = DEFAULT_EXPERIMENT_ROOT,
    leaf_index: Path = DEFAULT_LEAF_INDEX,
    run_registry: Path = DEFAULT_RUN_REGISTRY,
    tariff_config: Path = DEFAULT_TARIFF_CONFIG,
) -> dict[str, Any]:
    experiment_root = Path(experiment_root)
    config, tariffs = load_tariff_config(Path(tariff_config))
    selected = _load_successful_leaf_index(Path(leaf_index), Path(run_registry))
    annual_rows: list[dict[str, Any]] = []
    monthly_rows: list[dict[str, Any]] = []
    for _, leaf in selected.iterrows():
        for tariff in tariffs:
            annual, monthly = _leaf_bill_rows(dict(leaf), tariff=tariff)
            annual_rows.append(annual)
            monthly_rows.extend(monthly)
    leaf_annual = pd.DataFrame(annual_rows)
    leaf_monthly = pd.DataFrame(monthly_rows)
    annual_comparison = _aggregate_annual(leaf_annual)
    monthly_comparison = _aggregate_monthly(leaf_monthly)
    components = _aggregate_components(annual_comparison)

    realization_dir = experiment_root / "summaries" / "realization_level"
    comparison_dir = experiment_root / "summaries" / "comparison_level"
    realization_dir.mkdir(parents=True, exist_ok=True)
    comparison_dir.mkdir(parents=True, exist_ok=True)
    leaf_annual_path = realization_dir / "output5_leaf_annual_energy_bills.csv"
    leaf_monthly_path = realization_dir / "output5_leaf_monthly_energy_bills.csv"
    annual_path = comparison_dir / "annual_energy_bill_comparison.csv"
    monthly_path = comparison_dir / "monthly_energy_bill_comparison.csv"
    components_path = comparison_dir / "annual_energy_bill_components.csv"
    tariff_path = comparison_dir / "tariff_assumptions.csv"
    leaf_annual.to_csv(leaf_annual_path, index=False)
    leaf_monthly.to_csv(leaf_monthly_path, index=False)
    annual_comparison.to_csv(annual_path, index=False)
    monthly_comparison.to_csv(monthly_path, index=False)
    components.to_csv(components_path, index=False)
    _write_csv(tariff_path, tariff_assumption_rows(config, tariffs), list(tariff_assumption_rows(config, tariffs)[0].keys()))
    return {
        "leaf_annual_bill_path": leaf_annual_path,
        "leaf_monthly_bill_path": leaf_monthly_path,
        "annual_energy_bill_comparison_path": annual_path,
        "monthly_energy_bill_comparison_path": monthly_path,
        "annual_energy_bill_components_path": components_path,
        "tariff_assumptions_path": tariff_path,
        "successful_leaf_count": int(selected["scenario_leaf_id"].nunique()),
        "tariff_scenario_count": len(tariffs),
        "annual_comparison_rows": len(annual_comparison),
        "monthly_comparison_rows": len(monthly_comparison),
    }


def _compact_label(row: Mapping[str, Any]) -> str:
    window = {
        "baseline_1981_2005": "Baseline",
        "near_future_2030_2049": "Near",
        "mid_century_2050_2070": "Mid",
        "long_term_2080_2100": "Long",
    }.get(str(row.get("climate_window_id", "")), str(row.get("climate_window_id", "")))
    pathway = {
        "historical": "Hist",
        "rcp_4_5": "RCP4.5",
        "rcp_8_5": "RCP8.5",
    }.get(str(row.get("climate_pathway_id", "")), str(row.get("climate_pathway_id", "")))
    tech = {
        "tech_current_stock": "Current",
        "tech_frozen_stock": "Frozen",
        "tech_moderate_electrification": "Moderate",
        "tech_high_electrification_pv_ev": "High PV+EV",
    }.get(str(row.get("technology_case_id", "")), str(row.get("technology_case_id", "")))
    design = {"cold_design_year": "Cold", "typical_heating_year": "Typical"}.get(
        str(row.get("design_year_id", "")),
        str(row.get("design_year_id", "")),
    )
    return f"{window} {pathway}\n{tech}\n{design}"


def _plot_save(fig: plt.Figure, output_base: Path, formats: Iterable[str]) -> list[Path]:
    output_base.parent.mkdir(parents=True, exist_ok=True)
    paths = []
    for fmt in formats:
        path = output_base.with_suffix(f".{fmt}")
        fig.savefig(path, dpi=220 if fmt == "png" else None, bbox_inches="tight")
        paths.append(path)
    plt.close(fig)
    return paths


def generate_output5_figures(
    *,
    experiment_root: Path = DEFAULT_EXPERIMENT_ROOT,
    figures_root: Path = DEFAULT_FIGURES_ROOT,
    formats: Iterable[str] = ("png", "pdf"),
    reference_tariff_id: str = "low_zero_export_value",
) -> dict[str, Any]:
    comparison_dir = Path(experiment_root) / "summaries" / "comparison_level"
    annual = pd.read_csv(comparison_dir / "annual_energy_bill_comparison.csv")
    monthly = pd.read_csv(comparison_dir / "monthly_energy_bill_comparison.csv")
    components = pd.read_csv(comparison_dir / "annual_energy_bill_components.csv")
    output_dir = Path(figures_root) / "output5_energy_bills"
    paths: list[Path] = []
    metadata_rows: list[dict[str, Any]] = []

    annual_plot = annual[annual["design_year_id"] == "cold_design_year"].copy()
    annual_plot["label"] = [_compact_label(row) for _, row in annual_plot.iterrows()]
    scenarios = list(dict.fromkeys(annual_plot["scenario_id"].tolist()))
    tariffs = list(dict.fromkeys(annual_plot["tariff_scenario_id"].tolist()))
    fig, ax = plt.subplots(figsize=(13, 5.5))
    width = 0.8 / max(len(tariffs), 1)
    x = np.arange(len(scenarios))
    for index, tariff_id in enumerate(tariffs):
        group = annual_plot[annual_plot["tariff_scenario_id"] == tariff_id].set_index("scenario_id")
        values = [float(group.loc[scenario, "annual_bill_per_household_EUR_mean"]) if scenario in group.index else np.nan for scenario in scenarios]
        label = str(group["tariff_scenario_label"].iloc[0]) if not group.empty else tariff_id
        ax.bar(x + (index - (len(tariffs) - 1) / 2) * width, values, width=width, label=label)
    labels = [
        _compact_label(annual_plot[annual_plot["scenario_id"] == scenario].iloc[0])
        for scenario in scenarios
    ]
    ax.set_xticks(x, labels, fontsize=8)
    ax.set_ylabel("Annual bill per household (EUR/year)")
    ax.set_title("Output 5: annual bill by tariff scenario")
    ax.legend(fontsize=7, ncol=2)
    figure_paths = _plot_save(fig, output_dir / "output5_annual_bill_by_tariff", formats)
    paths.extend(figure_paths)
    metadata_rows.append({"figure_id": "output5_annual_bill_by_tariff", "source_rows": len(annual_plot), "files": ";".join(map(str, figure_paths))})

    monthly_plot = monthly[
        (monthly["design_year_id"] == "cold_design_year")
        & (monthly["tariff_scenario_id"] == reference_tariff_id)
    ].copy()
    fig, ax = plt.subplots(figsize=(11, 5))
    for scenario_id, group in monthly_plot.groupby("scenario_id", sort=False):
        if scenario_id == BASELINE_SCENARIO_ID:
            continue
        label = _compact_label(group.iloc[0]).replace("\n", " / ")
        ax.plot(group["month"], group["delta_monthly_bill_per_household_EUR_abs"], marker="o", linewidth=1.5, label=label)
    ax.axhline(0.0, color="#333333", linewidth=0.8)
    ax.set_xticks(range(1, 13))
    ax.set_xlabel("Month")
    ax.set_ylabel("Monthly bill delta vs baseline (EUR/household)")
    ax.set_title("Output 5: monthly bill difference under low/zero export value")
    ax.legend(fontsize=7, ncol=2)
    figure_paths = _plot_save(fig, output_dir / "output5_monthly_bill_delta", formats)
    paths.extend(figure_paths)
    metadata_rows.append({"figure_id": "output5_monthly_bill_delta", "source_rows": len(monthly_plot), "files": ";".join(map(str, figure_paths))})

    water = components[
        (components["design_year_id"] == "cold_design_year")
        & (components["tariff_scenario_id"] == reference_tariff_id)
        & (components["scenario_id"] != BASELINE_SCENARIO_ID)
    ].copy()
    if not water.empty:
        target = water.iloc[-1]
        labels = ["Import", "Gas", "Export credit", "Fixed", "Capacity"]
        values = [
            float(target["delta_annual_import_energy_cost_EUR_abs"]) / max(float(annual_plot["n_households"].max()), 1.0),
            float(target["delta_annual_gas_cost_EUR_abs"]) / max(float(annual_plot["n_households"].max()), 1.0),
            -float(target["delta_annual_grid_export_credit_EUR_abs"]) / max(float(annual_plot["n_households"].max()), 1.0),
            float(target["delta_annual_fixed_cost_EUR_abs"]) / max(float(annual_plot["n_households"].max()), 1.0),
            float(target["delta_annual_capacity_cost_EUR_abs"]) / max(float(annual_plot["n_households"].max()), 1.0),
        ]
        fig, ax = plt.subplots(figsize=(8, 5))
        colors = ["#2166ac" if value >= 0 else "#b2182b" for value in values]
        ax.bar(labels, values, color=colors)
        ax.axhline(0.0, color="#333333", linewidth=0.8)
        ax.set_ylabel("Bill component delta (EUR/household-year)")
        ax.set_title("Output 5: component waterfall versus baseline")
        figure_paths = _plot_save(fig, output_dir / "output5_bill_component_waterfall", formats)
        paths.extend(figure_paths)
        metadata_rows.append({"figure_id": "output5_bill_component_waterfall", "source_rows": len(water), "files": ";".join(map(str, figure_paths))})

    metadata_path = output_dir / "output5_figure_metadata.csv"
    pd.DataFrame(metadata_rows).to_csv(metadata_path, index=False)
    return {"figure_count": len(metadata_rows), "files": paths, "metadata_path": metadata_path}


def validate_output5_results(
    *,
    experiment_root: Path = DEFAULT_EXPERIMENT_ROOT,
    expected_tariff_count: int = 5,
) -> dict[str, Any]:
    comparison_dir = Path(experiment_root) / "summaries" / "comparison_level"
    annual = pd.read_csv(comparison_dir / "annual_energy_bill_comparison.csv")
    monthly = pd.read_csv(comparison_dir / "monthly_energy_bill_comparison.csv")
    components = pd.read_csv(comparison_dir / "annual_energy_bill_components.csv")
    tariffs = pd.read_csv(comparison_dir / "tariff_assumptions.csv")
    errors = []
    if len(set(tariffs["tariff_scenario_id"])) != int(expected_tariff_count):
        errors.append(f"Expected {expected_tariff_count} tariff scenarios, found {len(set(tariffs['tariff_scenario_id']))}.")
    if any(column in ACTIVE_COOLING_COLUMN_NAMES for column in set(annual.columns) | set(monthly.columns) | set(components.columns)):
        errors.append("Active cooling final-energy columns must not be present in Output 5 tables.")
    future = annual[annual["scenario_id"] != BASELINE_SCENARIO_ID]
    if "delta_annual_bill_per_household_EUR_abs" not in annual or future["delta_annual_bill_per_household_EUR_abs"].isna().any():
        errors.append("Future annual bill deltas must be populated.")
    if monthly.empty or annual.empty or components.empty:
        errors.append("Output 5 comparison tables must not be empty.")
    if "source_references" not in tariffs or tariffs["source_references"].astype(str).str.len().min() == 0:
        errors.append("Tariff assumptions must include source references.")
    if errors:
        raise Output5Error("; ".join(errors))
    return {
        "annual_rows": int(len(annual)),
        "monthly_rows": int(len(monthly)),
        "component_rows": int(len(components)),
        "tariff_rows": int(len(tariffs)),
        "active_cooling_final_energy_columns_present": False,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    tables = subparsers.add_parser("tables", help="Build Output 5 bill-comparison tables.")
    tables.add_argument("--experiment-root", type=Path, default=DEFAULT_EXPERIMENT_ROOT)
    tables.add_argument("--leaf-index", type=Path, default=DEFAULT_LEAF_INDEX)
    tables.add_argument("--run-registry", type=Path, default=DEFAULT_RUN_REGISTRY)
    tables.add_argument("--tariff-config", type=Path, default=DEFAULT_TARIFF_CONFIG)

    figures = subparsers.add_parser("figures", help="Generate Output 5 bill figures.")
    figures.add_argument("--experiment-root", type=Path, default=DEFAULT_EXPERIMENT_ROOT)
    figures.add_argument("--figures-root", type=Path, default=DEFAULT_FIGURES_ROOT)
    figures.add_argument("--formats", nargs="+", default=["png", "pdf"])
    figures.add_argument("--reference-tariff-id", default="low_zero_export_value")

    validate = subparsers.add_parser("validate", help="Validate Output 5 outputs.")
    validate.add_argument("--experiment-root", type=Path, default=DEFAULT_EXPERIMENT_ROOT)
    validate.add_argument("--expected-tariff-count", type=int, default=5)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "tables":
        result = build_output5_tables(
            experiment_root=args.experiment_root,
            leaf_index=args.leaf_index,
            run_registry=args.run_registry,
            tariff_config=args.tariff_config,
        )
    elif args.command == "figures":
        result = generate_output5_figures(
            experiment_root=args.experiment_root,
            figures_root=args.figures_root,
            formats=args.formats,
            reference_tariff_id=args.reference_tariff_id,
        )
    elif args.command == "validate":
        result = validate_output5_results(
            experiment_root=args.experiment_root,
            expected_tariff_count=args.expected_tariff_count,
        )
    else:
        raise AssertionError(args.command)
    for key, value in result.items():
        print(f"{key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
