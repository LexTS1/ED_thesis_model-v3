"""Output 6 technology investment and climate-adaptation indicators."""

from __future__ import annotations

import argparse
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

from model_v3.scenarios.registry import latest_actual_status, read_registry
from model_v3.utils.energy import infer_step_durations_seconds, integrate_power_series_kwh


REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_EXPERIMENT_ROOT = REPO_ROOT / "experiments" / "scenario_tree_output34"
DEFAULT_LEAF_INDEX = DEFAULT_EXPERIMENT_ROOT / "manifests" / "output34_leaf_index.csv"
DEFAULT_RUN_REGISTRY = DEFAULT_EXPERIMENT_ROOT / "manifests" / "run_registry.csv"
DEFAULT_ASSUMPTION_CONFIG = REPO_ROOT / "config" / "scenario_tree" / "output6_technology_assumptions.yaml"
DEFAULT_COOLING_COMPARISON = REPO_ROOT / "experiments" / "scenario_tree" / "summaries" / "comparison_level" / "cooling_exposure_overheating_risk_comparison.csv"
DEFAULT_FIGURES_ROOT = REPO_ROOT / "figures" / "scenario_tree_output34"
BASELINE_SCENARIO_ID = "baseline_1981_2005__historical__tech_current_stock"
GROUP_COLUMNS = [
    "scenario_id",
    "climate_window_id",
    "climate_pathway_id",
    "technology_case_id",
    "design_year_id",
    "design_year",
]
ACTIVE_COOLING_COLUMN_NAMES = {
    "active_cooling_final_energy_kWh",
    "cooling_final_energy_kWh",
    "cooling_electricity_kWh",
}


class Output6Error(RuntimeError):
    """Raised when Output 6 post-processing cannot be completed."""


def _resolve_repo_path(path_text: str | Path) -> Path:
    path = Path(path_text)
    if path.is_absolute():
        return path
    return REPO_ROOT / path


def _load_yaml(path: Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict):
        raise Output6Error(f"YAML file must contain a mapping: {path}")
    return data


def _write_csv(path: Path, rows: list[Mapping[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _safe_divide(numerator: float, denominator: float) -> float:
    if not math.isfinite(numerator) or not math.isfinite(denominator) or abs(denominator) <= 1e-12:
        return float("nan")
    return numerator / denominator


def _pct_delta(value: float, baseline: float) -> float:
    ratio = _safe_divide(value - baseline, baseline)
    return ratio * 100.0 if math.isfinite(ratio) else float("nan")


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


def annuity_factor(discount_rate: float, lifetime_years: int) -> float:
    """Return the capital recovery factor for annualizing CAPEX."""

    years = int(lifetime_years)
    if years <= 0:
        return float("nan")
    rate = float(discount_rate)
    if abs(rate) <= 1e-12:
        return 1.0 / years
    return rate * (1.0 + rate) ** years / ((1.0 + rate) ** years - 1.0)


def present_value_annuity(annual_value: float, discount_rate: float, years: int) -> float:
    years = int(years)
    if years <= 0:
        return 0.0
    rate = float(discount_rate)
    if abs(rate) <= 1e-12:
        return float(annual_value) * years
    return float(annual_value) * (1.0 - (1.0 + rate) ** (-years)) / rate


def simple_payback_years(capex_net: float, annual_savings: float) -> float:
    if not math.isfinite(capex_net) or capex_net <= 0.0:
        return float("nan")
    if not math.isfinite(annual_savings) or annual_savings <= 0.0:
        return float("nan")
    return capex_net / annual_savings


def npv_savings(capex_net: float, annual_net_benefit: float, discount_rate: float, years: int) -> float:
    return -float(capex_net) + present_value_annuity(float(annual_net_benefit), float(discount_rate), int(years))


def load_technology_assumptions(path: Path = DEFAULT_ASSUMPTION_CONFIG) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    config = _load_yaml(Path(path))
    defaults = dict(config.get("defaults", {}))
    metadata = dict(config.get("metadata", {}))
    options: list[dict[str, Any]] = []
    for raw in config.get("technology_options", []):
        if not isinstance(raw, Mapping):
            continue
        option = {**defaults, **dict(raw)}
        option["technology_option_id"] = str(option["technology_option_id"])
        option["label"] = str(option.get("label", option["technology_option_id"]))
        option["applies_to_technology_case_ids"] = [str(value) for value in option.get("applies_to_technology_case_ids", [])]
        option["lifetime_years"] = int(option.get("lifetime_years", defaults.get("lifetime_years", metadata.get("analysis_horizon_years", 15))))
        option["reversible_cooling_service"] = bool(option.get("reversible_cooling_service", False))
        option["pv_component"] = bool(option.get("pv_component", False))
        option["smart_control_sensitivity"] = bool(option.get("smart_control_sensitivity", False))
        options.append(option)
    if not options:
        raise Output6Error(f"No technology options found in {path}")
    return config, options


def technology_assumption_rows(config: Mapping[str, Any], options: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    metadata = dict(config.get("metadata", {}))
    discount_rate = _safe_float(metadata.get("discount_rate"), 0.03)
    core_subsidy = bool(metadata.get("core_subsidy_included", False))
    source_notes = metadata.get("source_notes", [])
    source_text = "; ".join(f"{item.get('name')}: {item.get('url')}" for item in source_notes if isinstance(item, Mapping))
    rows = []
    for option in options:
        gross = _safe_float(option.get("capex_gross_eur_per_adopting_household"))
        subsidy = _safe_float(option.get("subsidy_eur_optional"))
        stock_fraction = _safe_float(option.get("stock_penetration_fraction"))
        lifetime = int(option.get("lifetime_years", metadata.get("analysis_horizon_years", 15)))
        net_adopting_core = max(gross - subsidy, 0.0) if core_subsidy else gross
        net_adopting_optional_subsidy = max(gross - subsidy, 0.0)
        rows.append(
            {
                "technology_option_id": option["technology_option_id"],
                "technology_option_label": option["label"],
                "applies_to_technology_case_ids": ";".join(option.get("applies_to_technology_case_ids", [])),
                "role": option.get("role", ""),
                "capex_gross_eur_per_adopting_household": gross,
                "subsidy_eur_optional": subsidy,
                "core_subsidy_included": core_subsidy,
                "capex_net_eur_per_adopting_household_core": net_adopting_core,
                "capex_net_eur_per_adopting_household_optional_subsidy": net_adopting_optional_subsidy,
                "stock_penetration_fraction": stock_fraction,
                "capex_net_eur_per_scenario_household_core": net_adopting_core * stock_fraction,
                "capex_net_eur_per_scenario_household_optional_subsidy": net_adopting_optional_subsidy * stock_fraction,
                "lifetime_years": lifetime,
                "discount_rate": discount_rate,
                "annuity_factor": annuity_factor(discount_rate, lifetime),
                "fixed_om_eur_per_year_per_adopting_household": _safe_float(option.get("fixed_om_eur_per_year")),
                "reversible_cooling_service": bool(option.get("reversible_cooling_service", False)),
                "pv_component": bool(option.get("pv_component", False)),
                "smart_control_sensitivity": bool(option.get("smart_control_sensitivity", False)),
                "bill_savings_multiplier": _safe_float(option.get("bill_savings_multiplier"), 1.0),
                "interpretation_scope": option.get("interpretation_scope", ""),
                "source_references": source_text,
                "interpretation_note": metadata.get("interpretation_note", ""),
                "cooling_proxy_note": metadata.get("cooling_proxy_note", ""),
            }
        )
    return rows


def _timestamps(profile: pd.DataFrame) -> pd.Series:
    if "timestamp" not in profile:
        raise Output6Error("Profile frame must contain a timestamp column.")
    return pd.to_datetime(profile["timestamp"], utc=True)


def _durations_seconds(timestamps: pd.Series) -> np.ndarray:
    return np.asarray(infer_step_durations_seconds(list(timestamps)), dtype=float)


def _profile_column(frame: pd.DataFrame, column: str, *, fallback: str | None = None) -> pd.Series:
    if column in frame:
        return pd.to_numeric(frame[column], errors="coerce").fillna(0.0)
    if fallback and fallback in frame:
        return pd.to_numeric(frame[fallback], errors="coerce").fillna(0.0)
    return pd.Series(np.zeros(len(frame)), index=frame.index, dtype=float)


def _energy_kwh(profile: pd.DataFrame, column: str, timestamps: pd.Series, *, fallback: str | None = None) -> float:
    values = _profile_column(profile, column, fallback=fallback)
    return integrate_power_series_kwh(values, timestamps=timestamps)


def _load_successful_leaf_index(
    *,
    leaf_index: Path,
    run_registry: Path,
    output5_leaf_annual: pd.DataFrame,
) -> pd.DataFrame:
    index = pd.read_csv(leaf_index)
    known_success = set(output5_leaf_annual["scenario_leaf_id"].astype(str))
    if run_registry.exists():
        registry_rows = read_registry(run_registry)
        statuses = {leaf_id: latest_actual_status(registry_rows, str(leaf_id)) for leaf_id in index["scenario_leaf_id"]}
        index["latest_status"] = index["scenario_leaf_id"].map(statuses)
        selected = index[(index["latest_status"] == "success") & (index["scenario_leaf_id"].isin(known_success))].copy()
    else:
        selected = index[index["scenario_leaf_id"].isin(known_success)].copy()
    if selected.empty:
        raise Output6Error("No successful leaves found for Output 6 PV/service indicators.")
    return selected


def _n_households_by_leaf(output5_leaf_annual: pd.DataFrame) -> dict[str, int]:
    rows = output5_leaf_annual.drop_duplicates("scenario_leaf_id")
    return {
        str(row["scenario_leaf_id"]): int(row.get("n_households", 1) or 1)
        for _, row in rows.iterrows()
    }


def build_leaf_service_indicator_rows(
    *,
    experiment_root: Path,
    leaf_index: Path,
    run_registry: Path,
) -> pd.DataFrame:
    realization_dir = Path(experiment_root) / "summaries" / "realization_level"
    output5_leaf_path = realization_dir / "output5_leaf_annual_energy_bills.csv"
    if not output5_leaf_path.exists():
        raise Output6Error(f"Missing Output 5 leaf bill table: {output5_leaf_path}")
    output5_leaf = pd.read_csv(output5_leaf_path)
    selected = _load_successful_leaf_index(
        leaf_index=Path(leaf_index),
        run_registry=Path(run_registry),
        output5_leaf_annual=output5_leaf,
    )
    household_counts = _n_households_by_leaf(output5_leaf)
    rows: list[dict[str, Any]] = []
    for _, leaf in selected.iterrows():
        outputs_dir = _resolve_repo_path(str(leaf["outputs_dir"]))
        profile_path = outputs_dir / "annual_profile.csv"
        if not profile_path.exists():
            raise Output6Error(f"Missing annual_profile.csv for Output 6 leaf: {profile_path}")
        profile = pd.read_csv(profile_path)
        timestamps = _timestamps(profile)
        n_households = max(int(household_counts.get(str(leaf["scenario_leaf_id"]), 1)), 1)
        pv_generation = _energy_kwh(profile, "P_pv_generation_W", timestamps)
        grid_export = _energy_kwh(profile, "P_el_grid_export_W", timestamps)
        gross_load = _energy_kwh(profile, "P_el_gross_actual_W", timestamps, fallback="P_el_total_W")
        useful_heating = _energy_kwh(profile, "Q_heating_supplied_W", timestamps)
        pv_self = max(pv_generation - grid_export, 0.0)
        rows.append(
            {
                "scenario_leaf_id": leaf["scenario_leaf_id"],
                "scenario_id": leaf["scenario_id"],
                "climate_window_id": leaf["climate_window_id"],
                "climate_pathway_id": leaf["climate_pathway_id"],
                "technology_case_id": leaf["technology_case_id"],
                "design_year_id": leaf.get("design_year_id", ""),
                "design_year": int(leaf.get("design_year", 0) or 0),
                "realization_id": leaf.get("realization_id", ""),
                "n_households": n_households,
                "annual_pv_generation_kWh": pv_generation,
                "annual_pv_self_consumed_kWh": pv_self,
                "annual_grid_export_kWh": grid_export,
                "annual_electricity_gross_kWh": gross_load,
                "annual_useful_heating_kWh": useful_heating,
                "annual_pv_generation_kWh_per_household": pv_generation / n_households,
                "annual_pv_self_consumed_kWh_per_household": pv_self / n_households,
                "annual_grid_export_kWh_per_household": grid_export / n_households,
                "annual_electricity_gross_kWh_per_household": gross_load / n_households,
                "annual_useful_heating_kWh_per_household": useful_heating / n_households,
                "pv_self_consumption_ratio": _safe_divide(pv_self, pv_generation),
                "pv_self_sufficiency_ratio": _safe_divide(pv_self, gross_load),
                "active_cooling_final_energy_kWh_included": False,
            }
        )
    return pd.DataFrame(rows)


def _aggregate_service_indicators(leaf_service: pd.DataFrame) -> pd.DataFrame:
    metrics = [
        "annual_pv_generation_kWh",
        "annual_pv_self_consumed_kWh",
        "annual_grid_export_kWh",
        "annual_electricity_gross_kWh",
        "annual_useful_heating_kWh",
        "annual_pv_generation_kWh_per_household",
        "annual_pv_self_consumed_kWh_per_household",
        "annual_grid_export_kWh_per_household",
        "annual_electricity_gross_kWh_per_household",
        "annual_useful_heating_kWh_per_household",
        "pv_self_consumption_ratio",
        "pv_self_sufficiency_ratio",
    ]
    rows: list[dict[str, Any]] = []
    for group_values, group in leaf_service.groupby(GROUP_COLUMNS, dropna=False):
        row = dict(zip(GROUP_COLUMNS, group_values))
        row["n_successful_runs"] = int(group["scenario_leaf_id"].nunique())
        row["n_households"] = int(pd.to_numeric(group["n_households"], errors="coerce").median())
        row["active_cooling_final_energy_kWh_included"] = False
        row["pv_indicator_basis"] = "PV self-consumption is computed from annual_profile PV generation minus grid export."
        for metric in metrics:
            row.update(_summary_stats(group[metric], metric))
        rows.append(row)
    result = pd.DataFrame(rows).sort_values(GROUP_COLUMNS).reset_index(drop=True)
    baseline_heat = {
        str(row["design_year_id"]): float(row["annual_useful_heating_kWh_per_household_mean"])
        for _, row in result[result["scenario_id"] == BASELINE_SCENARIO_ID].iterrows()
    }
    for index, row in result.iterrows():
        baseline = baseline_heat.get(str(row["design_year_id"]), float("nan"))
        value = float(row["annual_useful_heating_kWh_per_household_mean"])
        result.loc[index, "baseline_annual_useful_heating_kWh_per_household_mean"] = baseline
        result.loc[index, "delta_annual_useful_heating_kWh_per_household_abs"] = value - baseline if math.isfinite(value) and math.isfinite(baseline) else float("nan")
        result.loc[index, "delta_annual_useful_heating_kWh_per_household_pct"] = _pct_delta(value, baseline)
    return result


def _cooling_indicator_map(path: Path) -> dict[tuple[str, str], dict[str, Any]]:
    if not Path(path).exists():
        return {}
    cooling = pd.read_csv(path)
    rows: dict[tuple[str, str], dict[str, Any]] = {}
    for _, row in cooling.iterrows():
        key = (str(row.get("climate_window_id", "")), str(row.get("climate_pathway_id", "")))
        if key not in rows:
            rows[key] = {
                "CDD_22_mean": _safe_float(row.get("CDD_22_mean"), float("nan")),
                "overheating_hours_mean": _safe_float(row.get("overheating_hours_mean"), float("nan")),
                "excess_heat_kWh_mean": _safe_float(row.get("excess_heat_kWh_mean"), float("nan")),
                "indoor_temperature_exceedance_degree_hours_mean": _safe_float(
                    row.get("indoor_temperature_exceedance_degree_hours_mean"),
                    float("nan"),
                ),
                "baseline_CDD_22_mean": _safe_float(row.get("baseline_CDD_22_mean"), float("nan")),
                "baseline_overheating_hours_mean": _safe_float(row.get("baseline_overheating_hours_mean"), float("nan")),
                "delta_CDD_22_abs": _safe_float(row.get("delta_CDD_22_abs"), float("nan")),
                "delta_overheating_hours_abs": _safe_float(row.get("delta_overheating_hours_abs"), float("nan")),
                "cooling_indicator_source": str(path),
                "cooling_indicator_basis": str(row.get("interpretation_note", "")),
            }
    return rows


def _options_by_technology_case(options: Iterable[Mapping[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    mapping: dict[str, list[dict[str, Any]]] = {}
    for option in options:
        for technology_case_id in option.get("applies_to_technology_case_ids", []):
            mapping.setdefault(str(technology_case_id), []).append(dict(option))
    return mapping


def _tariff_metadata(annual_bill: pd.DataFrame, comparison_dir: Path) -> dict[str, dict[str, Any]]:
    tariff_path = comparison_dir / "tariff_assumptions.csv"
    if tariff_path.exists():
        tariff_rows = pd.read_csv(tariff_path)
        return {str(row["tariff_scenario_id"]): dict(row) for _, row in tariff_rows.iterrows()}
    rows = annual_bill.drop_duplicates("tariff_scenario_id")
    return {
        str(row["tariff_scenario_id"]): {
            "tariff_scenario_label": row.get("tariff_scenario_label", row["tariff_scenario_id"]),
            "electricity_import_eur_per_kwh": float("nan"),
            "pv_export_eur_per_kwh": float("nan"),
        }
        for _, row in rows.iterrows()
    }


def _source_references(config: Mapping[str, Any]) -> str:
    metadata = dict(config.get("metadata", {}))
    source_notes = metadata.get("source_notes", [])
    return "; ".join(f"{item.get('name')}: {item.get('url')}" for item in source_notes if isinstance(item, Mapping))


def _calculate_investment_row(
    annual_row: Mapping[str, Any],
    service_row: Mapping[str, Any],
    option: Mapping[str, Any],
    *,
    config: Mapping[str, Any],
    cooling: Mapping[str, Any],
) -> dict[str, Any]:
    metadata = dict(config.get("metadata", {}))
    discount_rate = _safe_float(metadata.get("discount_rate"), 0.03)
    horizon = int(metadata.get("analysis_horizon_years", option.get("lifetime_years", 15)))
    lifetime = int(option.get("lifetime_years", horizon))
    stock_fraction = _safe_float(option.get("stock_penetration_fraction"))
    gross_adopting = _safe_float(option.get("capex_gross_eur_per_adopting_household"))
    subsidy_adopting = _safe_float(option.get("subsidy_eur_optional"))
    core_subsidy = bool(metadata.get("core_subsidy_included", False))
    net_adopting = max(gross_adopting - subsidy_adopting, 0.0) if core_subsidy else gross_adopting
    optional_subsidy_net = max(gross_adopting - subsidy_adopting, 0.0)
    capex_net = net_adopting * stock_fraction
    capex_net_optional_subsidy = optional_subsidy_net * stock_fraction
    capex_gross = gross_adopting * stock_fraction
    fixed_om = _safe_float(option.get("fixed_om_eur_per_year")) * stock_fraction
    baseline_bill = _safe_float(annual_row.get("baseline_annual_bill_per_household_EUR_mean"), float("nan"))
    scenario_bill = _safe_float(annual_row.get("annual_bill_per_household_EUR_mean"), float("nan"))
    baseline_emissions = _safe_float(
        annual_row.get("baseline_annual_operational_emissions_kgCO2_per_household_mean"),
        float("nan"),
    )
    scenario_emissions = _safe_float(
        annual_row.get("annual_operational_emissions_kgCO2_per_household_mean"),
        float("nan"),
    )
    emissions_delta = scenario_emissions - baseline_emissions if math.isfinite(scenario_emissions) and math.isfinite(baseline_emissions) else float("nan")
    emissions_reduction = -emissions_delta if math.isfinite(emissions_delta) else float("nan")
    raw_bill_savings = baseline_bill - scenario_bill if math.isfinite(baseline_bill) and math.isfinite(scenario_bill) else float("nan")
    bill_savings = raw_bill_savings * _safe_float(option.get("bill_savings_multiplier"), 1.0) if math.isfinite(raw_bill_savings) else float("nan")
    annual_net_benefit = bill_savings - fixed_om if math.isfinite(bill_savings) else float("nan")
    factor = annuity_factor(discount_rate, lifetime)
    annualized_capex = capex_net * factor if math.isfinite(factor) else float("nan")
    annualized_capex_optional = capex_net_optional_subsidy * factor if math.isfinite(factor) else float("nan")
    annualized_total_cost = scenario_bill + annualized_capex + fixed_om if math.isfinite(scenario_bill) else float("nan")
    annualized_total_cost_optional = scenario_bill + annualized_capex_optional + fixed_om if math.isfinite(scenario_bill) else float("nan")

    useful_heat = _safe_float(service_row.get("annual_useful_heating_kWh_per_household_mean"), float("nan"))
    cdd = _safe_float(cooling.get("CDD_22_mean"), float("nan"))
    overheating = _safe_float(cooling.get("overheating_hours_mean"), float("nan"))
    coverage = _safe_float(option.get("cooling_exposure_coverage_fraction"), 0.0) if bool(option.get("reversible_cooling_service", False)) else 0.0
    cooling_service_proxy = cdd * _safe_float(option.get("cooling_service_proxy_kwh_per_cdd22"), 0.0) * coverage * stock_fraction if math.isfinite(cdd) else float("nan")
    covered_overheating_hours = overheating * coverage * stock_fraction if math.isfinite(overheating) else float("nan")
    comfort_value = covered_overheating_hours * _safe_float(option.get("comfort_value_eur_per_overheating_hour"), 0.0) if math.isfinite(covered_overheating_hours) else float("nan")
    lcoh = _safe_divide(annualized_total_cost, useful_heat)
    lcos = _safe_divide(annualized_total_cost, useful_heat + cooling_service_proxy)

    row = {column: annual_row.get(column, "") for column in GROUP_COLUMNS}
    row.update(
        {
            "technology_option_id": option["technology_option_id"],
            "technology_option_label": option["label"],
            "technology_option_role": option.get("role", ""),
            "tariff_scenario_id": annual_row.get("tariff_scenario_id", ""),
            "tariff_scenario_label": annual_row.get("tariff_scenario_label", ""),
            "n_successful_runs": annual_row.get("n_successful_runs", service_row.get("n_successful_runs", 0)),
            "n_households": annual_row.get("n_households", service_row.get("n_households", 0)),
            "baseline_scenario_id": BASELINE_SCENARIO_ID,
            "baseline_annual_bill_per_household_EUR_mean": baseline_bill,
            "scenario_annual_bill_per_household_EUR_mean": scenario_bill,
            "annual_bill_savings_vs_baseline_EUR_per_household": bill_savings,
            "raw_annual_bill_savings_vs_baseline_EUR_per_household": raw_bill_savings,
            "baseline_annual_operational_emissions_kgCO2_per_household_mean": baseline_emissions,
            "scenario_annual_operational_emissions_kgCO2_per_household_mean": scenario_emissions,
            "delta_annual_operational_emissions_kgCO2_per_household_abs": emissions_delta,
            "delta_annual_operational_emissions_kgCO2_per_household_pct": _pct_delta(scenario_emissions, baseline_emissions),
            "annual_operational_emissions_reduction_kgCO2_per_household": emissions_reduction,
            "annual_bill_savings_per_kgCO2_reduced_EUR_per_kgCO2": _safe_divide(bill_savings, emissions_reduction)
            if math.isfinite(emissions_reduction) and emissions_reduction > 0
            else float("nan"),
            "bill_savings_multiplier": _safe_float(option.get("bill_savings_multiplier"), 1.0),
            "capex_gross_eur_per_adopting_household": gross_adopting,
            "subsidy_eur_optional_per_adopting_household": subsidy_adopting,
            "core_subsidy_included": bool(metadata.get("core_subsidy_included", False)),
            "stock_penetration_fraction": stock_fraction,
            "capex_gross_eur_per_scenario_household": capex_gross,
            "capex_net_eur_per_scenario_household": capex_net,
            "capex_net_eur_per_scenario_household_optional_subsidy": capex_net_optional_subsidy,
            "fixed_om_eur_per_scenario_household_year": fixed_om,
            "discount_rate": discount_rate,
            "analysis_horizon_years": horizon,
            "lifetime_years": lifetime,
            "annuity_factor": factor,
            "annualized_capex_eur_per_scenario_household_year": annualized_capex,
            "annualized_capex_eur_per_scenario_household_year_optional_subsidy": annualized_capex_optional,
            "annual_net_benefit_EUR_per_household_year": annual_net_benefit,
            "simple_payback_years_bill_savings": simple_payback_years(capex_net, bill_savings),
            "simple_payback_years_net_benefit": simple_payback_years(capex_net, annual_net_benefit),
            "npv_savings_EUR_per_scenario_household": npv_savings(capex_net, annual_net_benefit, discount_rate, horizon)
            if math.isfinite(annual_net_benefit)
            else float("nan"),
            "npv_savings_EUR_per_scenario_household_optional_subsidy": npv_savings(
                capex_net_optional_subsidy,
                annual_net_benefit,
                discount_rate,
                horizon,
            )
            if math.isfinite(annual_net_benefit)
            else float("nan"),
            "annualized_total_cost_EUR_per_household_year": annualized_total_cost,
            "annualized_total_cost_EUR_per_household_year_optional_subsidy": annualized_total_cost_optional,
            "annualized_total_cost_delta_vs_baseline_EUR_per_household_year": annualized_total_cost - baseline_bill
            if math.isfinite(annualized_total_cost) and math.isfinite(baseline_bill)
            else float("nan"),
            "annual_useful_heating_kWh_per_household_mean": useful_heat,
            "baseline_annual_useful_heating_kWh_per_household_mean": _safe_float(
                service_row.get("baseline_annual_useful_heating_kWh_per_household_mean"),
                float("nan"),
            ),
            "delta_annual_useful_heating_kWh_per_household_pct": _safe_float(
                service_row.get("delta_annual_useful_heating_kWh_per_household_pct"),
                float("nan"),
            ),
            "lcoh_proxy_EUR_per_kWh_useful_heat": lcoh,
            "cooling_service_proxy_kWh_per_scenario_household": cooling_service_proxy,
            "covered_overheating_hours_proxy_per_scenario_household": covered_overheating_hours,
            "comfort_adaptation_value_EUR_proxy_per_scenario_household": comfort_value,
            "lcos_proxy_EUR_per_kWh_heat_plus_cooling_service": lcos,
            "CDD_22_mean": cdd,
            "overheating_hours_mean": overheating,
            "indoor_temperature_exceedance_degree_hours_mean": _safe_float(
                cooling.get("indoor_temperature_exceedance_degree_hours_mean"),
                float("nan"),
            ),
            "reversible_cooling_service": bool(option.get("reversible_cooling_service", False)),
            "pv_component": bool(option.get("pv_component", False)),
            "smart_control_sensitivity": bool(option.get("smart_control_sensitivity", False)),
            "ev_load_confounder": str(annual_row.get("technology_case_id", "")) == "tech_high_electrification_pv_ev",
            "active_cooling_final_energy_kWh_included": False,
            "cooling_value_basis": metadata.get("cooling_proxy_note", ""),
            "interpretation_scope": option.get("interpretation_scope", ""),
            "source_references": _source_references(config),
        }
    )
    return row


def _build_investment_comparison(
    annual_bill: pd.DataFrame,
    service: pd.DataFrame,
    *,
    config: Mapping[str, Any],
    options: Iterable[Mapping[str, Any]],
    cooling_path: Path,
) -> pd.DataFrame:
    service_lookup = {tuple(row[column] for column in GROUP_COLUMNS): dict(row) for _, row in service.iterrows()}
    cooling_lookup = _cooling_indicator_map(cooling_path)
    options_by_case = _options_by_technology_case(options)
    rows: list[dict[str, Any]] = []
    for _, annual_row in annual_bill.iterrows():
        technology_case_id = str(annual_row["technology_case_id"])
        for option in options_by_case.get(technology_case_id, []):
            key = tuple(annual_row[column] for column in GROUP_COLUMNS)
            service_row = service_lookup.get(key, {})
            cooling = cooling_lookup.get((str(annual_row["climate_window_id"]), str(annual_row["climate_pathway_id"])), {})
            rows.append(
                _calculate_investment_row(
                    dict(annual_row),
                    service_row,
                    option,
                    config=config,
                    cooling=cooling,
                )
            )
    if not rows:
        raise Output6Error("No Output 6 investment rows could be built; check technology option mappings.")
    return pd.DataFrame(rows).sort_values(
        GROUP_COLUMNS + ["technology_option_id", "tariff_scenario_id"]
    ).reset_index(drop=True)


def _build_components(investment: pd.DataFrame) -> pd.DataFrame:
    component_columns = {
        "baseline_bill": "baseline_annual_bill_per_household_EUR_mean",
        "scenario_bill": "scenario_annual_bill_per_household_EUR_mean",
        "bill_savings": "annual_bill_savings_vs_baseline_EUR_per_household",
        "gross_capex": "capex_gross_eur_per_scenario_household",
        "net_capex_core": "capex_net_eur_per_scenario_household",
        "net_capex_optional_subsidy": "capex_net_eur_per_scenario_household_optional_subsidy",
        "annualized_capex_core": "annualized_capex_eur_per_scenario_household_year",
        "fixed_om": "fixed_om_eur_per_scenario_household_year",
        "annual_net_benefit": "annual_net_benefit_EUR_per_household_year",
        "npv_savings": "npv_savings_EUR_per_scenario_household",
        "comfort_adaptation_proxy": "comfort_adaptation_value_EUR_proxy_per_scenario_household",
    }
    rows: list[dict[str, Any]] = []
    id_columns = GROUP_COLUMNS + [
        "technology_option_id",
        "technology_option_label",
        "tariff_scenario_id",
        "tariff_scenario_label",
    ]
    for _, source in investment.iterrows():
        for component, column in component_columns.items():
            row = {name: source.get(name, "") for name in id_columns}
            row["component"] = component
            row["value_EUR_per_household_or_proxy"] = source.get(column, float("nan"))
            row["active_cooling_final_energy_kWh_included"] = False
            rows.append(row)
    return pd.DataFrame(rows)


def _build_emissions_comparison(investment: pd.DataFrame) -> pd.DataFrame:
    columns = GROUP_COLUMNS + [
        "technology_option_id",
        "technology_option_label",
        "technology_option_role",
        "tariff_scenario_id",
        "tariff_scenario_label",
        "n_successful_runs",
        "n_households",
        "baseline_scenario_id",
        "baseline_annual_operational_emissions_kgCO2_per_household_mean",
        "scenario_annual_operational_emissions_kgCO2_per_household_mean",
        "delta_annual_operational_emissions_kgCO2_per_household_abs",
        "delta_annual_operational_emissions_kgCO2_per_household_pct",
        "annual_operational_emissions_reduction_kgCO2_per_household",
        "annual_bill_savings_per_kgCO2_reduced_EUR_per_kgCO2",
        "npv_savings_EUR_per_scenario_household",
        "active_cooling_final_energy_kWh_included",
        "interpretation_scope",
    ]
    present = [column for column in columns if column in investment]
    return investment[present].copy().sort_values(present[: min(len(present), len(GROUP_COLUMNS) + 3)]).reset_index(drop=True)


def _build_pv_value_comparison(
    annual_bill: pd.DataFrame,
    service: pd.DataFrame,
    *,
    tariff_rows: Mapping[str, Mapping[str, Any]],
) -> pd.DataFrame:
    service_lookup = {tuple(row[column] for column in GROUP_COLUMNS): dict(row) for _, row in service.iterrows()}
    rows: list[dict[str, Any]] = []
    for _, annual_row in annual_bill.iterrows():
        key = tuple(annual_row[column] for column in GROUP_COLUMNS)
        service_row = service_lookup.get(key, {})
        tariff = tariff_rows.get(str(annual_row["tariff_scenario_id"]), {})
        n_households = max(int(annual_row.get("n_households", service_row.get("n_households", 1)) or 1), 1)
        import_price = _safe_float(tariff.get("electricity_import_eur_per_kwh"), float("nan"))
        export_price = _safe_float(tariff.get("pv_export_eur_per_kwh"), float("nan"))
        pv_self = _safe_float(service_row.get("annual_pv_self_consumed_kWh_per_household_mean"), float("nan"))
        export_kwh = _safe_float(service_row.get("annual_grid_export_kWh_per_household_mean"), float("nan"))
        export_credit = _safe_float(annual_row.get("annual_grid_export_credit_EUR_mean"), 0.0) / n_households
        row = {column: annual_row.get(column, "") for column in GROUP_COLUMNS}
        row.update(
            {
                "tariff_scenario_id": annual_row.get("tariff_scenario_id", ""),
                "tariff_scenario_label": annual_row.get("tariff_scenario_label", ""),
                "n_successful_runs": annual_row.get("n_successful_runs", service_row.get("n_successful_runs", 0)),
                "n_households": n_households,
                "annual_pv_generation_kWh_per_household_mean": service_row.get("annual_pv_generation_kWh_per_household_mean", float("nan")),
                "annual_pv_self_consumed_kWh_per_household_mean": pv_self,
                "annual_grid_export_kWh_per_household_mean": export_kwh,
                "annual_electricity_gross_kWh_per_household_mean": service_row.get("annual_electricity_gross_kWh_per_household_mean", float("nan")),
                "pv_self_consumption_ratio_mean": service_row.get("pv_self_consumption_ratio_mean", float("nan")),
                "pv_self_consumption_ratio_p10": service_row.get("pv_self_consumption_ratio_p10", float("nan")),
                "pv_self_consumption_ratio_p50": service_row.get("pv_self_consumption_ratio_p50", float("nan")),
                "pv_self_consumption_ratio_p90": service_row.get("pv_self_consumption_ratio_p90", float("nan")),
                "pv_self_sufficiency_ratio_mean": service_row.get("pv_self_sufficiency_ratio_mean", float("nan")),
                "pv_self_sufficiency_ratio_p10": service_row.get("pv_self_sufficiency_ratio_p10", float("nan")),
                "pv_self_sufficiency_ratio_p50": service_row.get("pv_self_sufficiency_ratio_p50", float("nan")),
                "pv_self_sufficiency_ratio_p90": service_row.get("pv_self_sufficiency_ratio_p90", float("nan")),
                "electricity_import_eur_per_kwh_for_self_consumption_value": import_price,
                "pv_export_eur_per_kwh": export_price,
                "pv_self_consumption_import_offset_value_EUR_per_household": pv_self * import_price
                if math.isfinite(pv_self) and math.isfinite(import_price)
                else float("nan"),
                "pv_export_credit_EUR_per_household": export_credit,
                "pv_total_value_EUR_per_household": (pv_self * import_price + export_credit)
                if math.isfinite(pv_self) and math.isfinite(import_price)
                else float("nan"),
                "pv_value_basis": "Self-consumed PV is valued as avoided import; exported PV is valued by the tariff export credit. Dynamic tariffs use the tariff headline import price as a post-processing approximation.",
                "active_cooling_final_energy_kWh_included": False,
            }
        )
        rows.append(row)
    return pd.DataFrame(rows).sort_values(GROUP_COLUMNS + ["tariff_scenario_id"]).reset_index(drop=True)


def build_output6_tables(
    *,
    experiment_root: Path = DEFAULT_EXPERIMENT_ROOT,
    leaf_index: Path = DEFAULT_LEAF_INDEX,
    run_registry: Path = DEFAULT_RUN_REGISTRY,
    assumption_config: Path = DEFAULT_ASSUMPTION_CONFIG,
    cooling_comparison: Path = DEFAULT_COOLING_COMPARISON,
) -> dict[str, Any]:
    experiment_root = Path(experiment_root)
    comparison_dir = experiment_root / "summaries" / "comparison_level"
    annual_bill_path = comparison_dir / "annual_energy_bill_comparison.csv"
    if not annual_bill_path.exists():
        raise Output6Error(f"Missing Output 5 annual bill comparison table: {annual_bill_path}")
    annual_bill = pd.read_csv(annual_bill_path)
    config, options = load_technology_assumptions(Path(assumption_config))
    leaf_service = build_leaf_service_indicator_rows(
        experiment_root=experiment_root,
        leaf_index=Path(leaf_index),
        run_registry=Path(run_registry),
    )
    service = _aggregate_service_indicators(leaf_service)
    tariff_rows = _tariff_metadata(annual_bill, comparison_dir)
    investment = _build_investment_comparison(
        annual_bill,
        service,
        config=config,
        options=options,
        cooling_path=Path(cooling_comparison),
    )
    components = _build_components(investment)
    emissions = _build_emissions_comparison(investment)
    pv_value = _build_pv_value_comparison(annual_bill, service, tariff_rows=tariff_rows)
    assumptions = technology_assumption_rows(config, options)

    realization_dir = experiment_root / "summaries" / "realization_level"
    realization_dir.mkdir(parents=True, exist_ok=True)
    comparison_dir.mkdir(parents=True, exist_ok=True)
    leaf_service_path = realization_dir / "output6_leaf_service_indicators.csv"
    service_path = comparison_dir / "technology_service_indicators.csv"
    investment_path = comparison_dir / "technology_investment_adaptation_comparison.csv"
    components_path = comparison_dir / "technology_investment_components.csv"
    emissions_path = comparison_dir / "technology_emissions_comparison.csv"
    pv_path = comparison_dir / "pv_self_consumption_value_comparison.csv"
    assumptions_path = comparison_dir / "technology_investment_assumptions.csv"
    leaf_service.to_csv(leaf_service_path, index=False)
    service.to_csv(service_path, index=False)
    investment.to_csv(investment_path, index=False)
    components.to_csv(components_path, index=False)
    emissions.to_csv(emissions_path, index=False)
    pv_value.to_csv(pv_path, index=False)
    _write_csv(assumptions_path, assumptions, list(assumptions[0].keys()))
    return {
        "leaf_service_indicators_path": leaf_service_path,
        "technology_service_indicators_path": service_path,
        "technology_investment_adaptation_comparison_path": investment_path,
        "technology_investment_components_path": components_path,
        "technology_emissions_comparison_path": emissions_path,
        "pv_self_consumption_value_comparison_path": pv_path,
        "technology_investment_assumptions_path": assumptions_path,
        "leaf_service_rows": int(len(leaf_service)),
        "investment_rows": int(len(investment)),
        "pv_value_rows": int(len(pv_value)),
        "technology_option_count": int(len(options)),
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


def _option_label(row: Mapping[str, Any]) -> str:
    option = str(row.get("technology_option_label", row.get("technology_option_id", "")))
    return f"{_compact_label(row)}\n{option}"


def _short_label(row: Mapping[str, Any], *, include_option: bool = False) -> str:
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
    label = f"{window} {pathway} | {tech} | {design}"
    if include_option:
        option = {
            "gas_boiler_reference": "Gas ref.",
            "frozen_stock_reference": "Frozen ref.",
            "air_water_heat_pump_heating": "HP",
            "reversible_heat_pump_adaptation": "Reversible HP",
            "heat_pump_pv": "HP+PV",
            "heat_pump_pv_smart_control_sensitivity": "HP+PV smart",
        }.get(str(row.get("technology_option_id", "")), str(row.get("technology_option_label", "")))
        label = f"{label} | {option}"
    return label


def _plot_save(fig: plt.Figure, output_base: Path, formats: Iterable[str]) -> list[Path]:
    output_base.parent.mkdir(parents=True, exist_ok=True)
    paths = []
    for fmt in formats:
        path = output_base.with_suffix(f".{fmt}")
        fig.savefig(path, dpi=220 if fmt == "png" else None, bbox_inches="tight")
        paths.append(path)
    plt.close(fig)
    return paths


def _context_note(frame: pd.DataFrame, *, scope: str) -> str:
    households = "n/a"
    if "n_households" in frame and not frame.empty:
        values = pd.to_numeric(frame["n_households"], errors="coerce").dropna().unique()
        if len(values) == 1:
            households = str(int(values[0]))
        elif len(values) > 1:
            households = f"{int(min(values))}-{int(max(values))}"
    seeds = "n/a"
    if "n_successful_runs" in frame and not frame.empty:
        values = pd.to_numeric(frame["n_successful_runs"], errors="coerce").dropna().unique()
        if len(values) == 1:
            seeds = str(int(values[0]))
        elif len(values) > 1:
            seeds = f"{int(min(values))}-{int(max(values))}"
    return (
        f"{scope}; cohort={households} households; seeds/group={seeds}; "
        "selected coverage only, not full 2800 leaves; no active cooling; tariffs/investments=illustrative assumptions."
    )


def _add_context_note(fig: plt.Figure, note: str) -> None:
    fig.text(0.5, 0.012, note, ha="center", va="bottom", fontsize=7.0, color="#4d4d4d")


def _metadata_entry(figure_id: str, frame: pd.DataFrame, figure_paths: list[Path], *, caption: str, context_note: str) -> dict[str, Any]:
    return {
        "figure_id": figure_id,
        "source_rows": len(frame),
        "files": ";".join(map(str, figure_paths)),
        "caption": caption,
        "context_note": context_note,
    }


def generate_output6_figures(
    *,
    experiment_root: Path = DEFAULT_EXPERIMENT_ROOT,
    figures_root: Path = DEFAULT_FIGURES_ROOT,
    formats: Iterable[str] = ("png", "pdf"),
    reference_tariff_id: str = "low_zero_export_value",
) -> dict[str, Any]:
    comparison_dir = Path(experiment_root) / "summaries" / "comparison_level"
    investment = pd.read_csv(comparison_dir / "technology_investment_adaptation_comparison.csv")
    pv_value = pd.read_csv(comparison_dir / "pv_self_consumption_value_comparison.csv")
    emissions = pd.read_csv(comparison_dir / "technology_emissions_comparison.csv")
    output_dir = Path(figures_root) / "output6_technology_investment_adaptation"
    paths: list[Path] = []
    metadata_rows: list[dict[str, Any]] = []

    cold = investment[
        (investment["design_year_id"] == "cold_design_year")
        & (~investment["technology_option_id"].isin(["gas_boiler_reference", "frozen_stock_reference"]))
    ].copy()
    if not cold.empty:
        options = list(dict.fromkeys(cold["scenario_id"].astype(str) + "__" + cold["technology_option_id"].astype(str)))
        tariffs = list(dict.fromkeys(cold["tariff_scenario_id"].tolist()))
        labels_by_key = {
            str(row["scenario_id"]) + "__" + str(row["technology_option_id"]): _short_label(row, include_option=True)
            for _, row in cold.drop_duplicates(["scenario_id", "technology_option_id"]).iterrows()
        }
        fig, ax = plt.subplots(figsize=(12, max(5.5, 0.48 * len(options))))
        height = 0.8 / max(len(tariffs), 1)
        y = np.arange(len(options))
        for index, tariff_id in enumerate(tariffs):
            group = cold[cold["tariff_scenario_id"] == tariff_id].copy()
            group["_key"] = group["scenario_id"].astype(str) + "__" + group["technology_option_id"].astype(str)
            group = group.set_index("_key")
            values = [float(group.loc[key, "npv_savings_EUR_per_scenario_household"]) if key in group.index else np.nan for key in options]
            label = str(group["tariff_scenario_label"].iloc[0]) if not group.empty else tariff_id
            ax.barh(y + (index - (len(tariffs) - 1) / 2) * height, values, height=height, label=label)
        ax.axvline(0.0, color="#333333", linewidth=0.8)
        ax.set_yticks(y, [labels_by_key[key] for key in options], fontsize=7)
        ax.set_xlabel("NPV of net savings (EUR/scenario household)")
        ax.set_title("Output 6: investment NPV by tariff scenario")
        ax.legend(fontsize=7, ncol=2)
        note = _context_note(cold, scope="technology-stress investment post-processing")
        _add_context_note(fig, note)
        fig.tight_layout(rect=(0, 0.045, 1, 1))
        figure_paths = _plot_save(fig, output_dir / "output6_npv_by_technology_tariff", formats)
        paths.extend(figure_paths)
        metadata_rows.append(
            _metadata_entry(
                "output6_npv_by_technology_tariff",
                cold,
                figure_paths,
                caption=(
                    "Investment NPV by tariff scenario. The figure combines modelled bills with editable CAPEX/O&M assumptions; "
                    "it is a scenario indicator, not a forecast of household retrofit economics."
                ),
                context_note=note,
            )
        )

    cost = investment[
        (investment["design_year_id"] == "cold_design_year")
        & (investment["tariff_scenario_id"] == reference_tariff_id)
    ].copy()
    if not cost.empty:
        cost["label"] = [_short_label(row, include_option=True) for _, row in cost.iterrows()]
        fig, ax = plt.subplots(figsize=(12, max(5.5, 0.42 * len(cost))))
        y = np.arange(len(cost))
        bill = cost["scenario_annual_bill_per_household_EUR_mean"].to_numpy(dtype=float)
        capex = cost["annualized_capex_eur_per_scenario_household_year"].to_numpy(dtype=float)
        om = cost["fixed_om_eur_per_scenario_household_year"].to_numpy(dtype=float)
        ax.barh(y, bill, label="Bill/OPEX")
        ax.barh(y, capex, left=bill, label="Annualized CAPEX")
        ax.barh(y, om, left=bill + capex, label="Fixed O&M")
        ax.set_yticks(y, cost["label"], fontsize=7)
        ax.set_xlabel("Annualized cost (EUR/household-year)")
        ax.set_title("Output 6: annualized bill, CAPEX, and O&M stack")
        ax.legend(fontsize=8)
        note = _context_note(cost, scope="technology-stress investment post-processing")
        _add_context_note(fig, note)
        fig.tight_layout(rect=(0, 0.045, 1, 1))
        figure_paths = _plot_save(fig, output_dir / "output6_annualized_cost_stack", formats)
        paths.extend(figure_paths)
        metadata_rows.append(
            _metadata_entry(
                "output6_annualized_cost_stack",
                cost,
                figure_paths,
                caption=(
                    "Annualized cost stack for bill/OPEX, annualized CAPEX, and fixed O&M under the reference tariff. "
                    "Cooling is represented only as an adaptation-service proxy where applicable."
                ),
                context_note=note,
            )
        )

    pv_plot = pv_value[
        (pv_value["design_year_id"] == "cold_design_year")
        & (pv_value["tariff_scenario_id"] == reference_tariff_id)
    ].copy()
    if not pv_plot.empty:
        pv_plot["label"] = [_short_label(row) for _, row in pv_plot.iterrows()]
        fig, ax = plt.subplots(figsize=(10, max(5.0, 0.42 * len(pv_plot))))
        y = np.arange(len(pv_plot))
        ax.barh(y - 0.18, pv_plot["pv_self_consumption_ratio_mean"], height=0.36, label="SCR")
        ax.barh(y + 0.18, pv_plot["pv_self_sufficiency_ratio_mean"], height=0.36, label="SSR")
        ax.set_yticks(y, pv_plot["label"], fontsize=8)
        ax.set_xlabel("Ratio")
        ax.set_xlim(0.0, max(1.0, float(pd.to_numeric(pv_plot[["pv_self_consumption_ratio_mean", "pv_self_sufficiency_ratio_mean"]].stack(), errors="coerce").max()) * 1.1))
        ax.set_title("Output 6: PV self-consumption and self-sufficiency")
        ax.legend(fontsize=8)
        note = _context_note(pv_plot, scope="technology-stress PV value post-processing")
        _add_context_note(fig, note)
        fig.tight_layout(rect=(0, 0.05, 1, 1))
        figure_paths = _plot_save(fig, output_dir / "output6_pv_scr_ssr", formats)
        paths.extend(figure_paths)
        metadata_rows.append(
            _metadata_entry(
                "output6_pv_scr_ssr",
                pv_plot,
                figure_paths,
                caption=(
                    "PV self-consumption ratio and self-sufficiency ratio. These are annual profile accounting metrics; "
                    "they do not imply optimized PV dispatch or active demand response."
                ),
                context_note=note,
            )
        )

    cooling = investment[
        (investment["design_year_id"] == "cold_design_year")
        & (investment["reversible_cooling_service"].astype(bool))
        & (investment["tariff_scenario_id"] == reference_tariff_id)
    ].copy()
    if not cooling.empty:
        cooling["label"] = [_short_label(row, include_option=True) for _, row in cooling.iterrows()]
        fig, ax = plt.subplots(figsize=(10, max(4.8, 0.42 * len(cooling))))
        y = np.arange(len(cooling))
        ax.barh(y, cooling["covered_overheating_hours_proxy_per_scenario_household"])
        ax.set_yticks(y, cooling["label"], fontsize=8)
        ax.set_xlabel("Covered overheating hours proxy")
        ax.set_title("Output 6: reversible heat-pump adaptation proxy")
        note = _context_note(cooling, scope="technology-stress adaptation-service proxy")
        _add_context_note(fig, note)
        fig.tight_layout(rect=(0, 0.055, 1, 1))
        figure_paths = _plot_save(fig, output_dir / "output6_reversible_hp_adaptation_proxy", formats)
        paths.extend(figure_paths)
        metadata_rows.append(
            _metadata_entry(
                "output6_reversible_hp_adaptation_proxy",
                cooling,
                figure_paths,
                caption=(
                    "Reversible heat-pump adaptation-service proxy. This is based on cooling exposure indicators and does not price or add active cooling electricity."
                ),
                context_note=note,
            )
        )

    scatter = cold[cold["tariff_scenario_id"] == reference_tariff_id].copy()
    payback_values = pd.to_numeric(scatter["simple_payback_years_bill_savings"], errors="coerce")
    if payback_values.notna().any():
        y_column = "simple_payback_years_bill_savings"
        y_label = "Simple payback using bill savings (years)"
    else:
        y_column = "npv_savings_EUR_per_scenario_household"
        y_label = "NPV of net savings (EUR/scenario household)"
    scatter = scatter[pd.to_numeric(scatter[y_column], errors="coerce").notna()]
    if not scatter.empty:
        fig, ax = plt.subplots(figsize=(8, 5.5))
        for _, row in scatter.iterrows():
            x = -float(row["delta_annual_useful_heating_kWh_per_household_pct"])
            y = float(row[y_column])
            ax.scatter(x, y, s=45)
            ax.annotate(str(row["technology_option_label"]), (x, y), fontsize=7, xytext=(4, 4), textcoords="offset points")
        ax.axhline(0.0, color="#333333", linewidth=0.8)
        ax.set_xlabel("Useful heating-demand reduction vs baseline (%)")
        ax.set_ylabel(y_label)
        ax.set_title("Output 6: heating reduction versus technology economics")
        note = _context_note(scatter, scope="technology-stress investment post-processing")
        _add_context_note(fig, note)
        fig.tight_layout(rect=(0, 0.055, 1, 1))
        figure_paths = _plot_save(fig, output_dir / "output6_heating_reduction_vs_economics", formats)
        paths.extend(figure_paths)
        metadata_rows.append(
            _metadata_entry(
                "output6_heating_reduction_vs_economics",
                scatter,
                figure_paths,
                caption=(
                    "Useful heating-demand reduction versus the selected economic indicator. The plot separates physical heating reduction "
                    "from tariff/CAPEX assumptions."
                ),
                context_note=note,
            )
        )

    emissions_plot = emissions[
        (emissions["design_year_id"] == "cold_design_year")
        & (emissions["tariff_scenario_id"] == reference_tariff_id)
        & (~emissions["technology_option_id"].isin(["gas_boiler_reference", "frozen_stock_reference"]))
    ].copy()
    if not emissions_plot.empty:
        emissions_plot["label"] = [_short_label(row, include_option=True) for _, row in emissions_plot.iterrows()]
        fig, ax = plt.subplots(figsize=(9.5, max(5.0, 0.42 * len(emissions_plot))))
        y = np.arange(len(emissions_plot))
        values = emissions_plot["annual_operational_emissions_reduction_kgCO2_per_household"].to_numpy(dtype=float)
        colors = ["#1b7837" if value >= 0 else "#b2182b" for value in values]
        ax.barh(y, values, color=colors)
        ax.axvline(0.0, color="#333333", linewidth=0.8)
        ax.set_yticks(y, emissions_plot["label"], fontsize=7)
        ax.set_xlabel("Operational emissions reduction vs baseline (kgCO2/household-year)")
        ax.set_title("Output 6: emissions effect of technology scenarios")
        note = _context_note(emissions_plot, scope="technology-stress ecological post-processing")
        _add_context_note(fig, note)
        fig.tight_layout(rect=(0, 0.055, 1, 1))
        figure_paths = _plot_save(fig, output_dir / "output6_operational_emissions_reduction", formats)
        paths.extend(figure_paths)
        metadata_rows.append(
            _metadata_entry(
                "output6_operational_emissions_reduction",
                emissions_plot,
                figure_paths,
                caption=(
                    "Operational emissions reduction versus baseline for the technology options. Positive values indicate lower "
                    "post-processed grid-import-plus-gas CO2 than the historical current-stock baseline."
                ),
                context_note=note,
            )
        )

    metadata_path = output_dir / "output6_figure_metadata.csv"
    pd.DataFrame(metadata_rows).to_csv(metadata_path, index=False)
    return {"figure_count": len(metadata_rows), "files": paths, "metadata_path": metadata_path}


def validate_output6_results(
    *,
    experiment_root: Path = DEFAULT_EXPERIMENT_ROOT,
    expected_option_count: int = 6,
) -> dict[str, Any]:
    comparison_dir = Path(experiment_root) / "summaries" / "comparison_level"
    investment = pd.read_csv(comparison_dir / "technology_investment_adaptation_comparison.csv")
    components = pd.read_csv(comparison_dir / "technology_investment_components.csv")
    pv_value = pd.read_csv(comparison_dir / "pv_self_consumption_value_comparison.csv")
    emissions = pd.read_csv(comparison_dir / "technology_emissions_comparison.csv")
    assumptions = pd.read_csv(comparison_dir / "technology_investment_assumptions.csv")
    service = pd.read_csv(comparison_dir / "technology_service_indicators.csv")
    errors: list[str] = []
    output_columns = set(investment.columns) | set(components.columns) | set(pv_value.columns) | set(service.columns)
    if any(column in ACTIVE_COOLING_COLUMN_NAMES for column in output_columns):
        errors.append("Active cooling final-energy columns must not be present in Output 6 tables.")
    if bool(investment.get("active_cooling_final_energy_kWh_included", pd.Series([False])).astype(bool).any()):
        errors.append("Output 6 must keep active_cooling_final_energy_kWh_included false.")
    if len(set(assumptions["technology_option_id"])) != int(expected_option_count):
        errors.append(f"Expected {expected_option_count} technology options, found {len(set(assumptions['technology_option_id']))}.")
    if "source_references" not in assumptions or assumptions["source_references"].astype(str).str.len().min() == 0:
        errors.append("Technology assumptions must include source references.")
    for column in ["pv_self_consumption_ratio_mean", "pv_self_sufficiency_ratio_mean"]:
        if column not in pv_value:
            errors.append(f"PV value table missing required column: {column}.")
    for column in [
        "scenario_annual_operational_emissions_kgCO2_per_household_mean",
        "annual_operational_emissions_reduction_kgCO2_per_household",
    ]:
        if column not in investment or column not in emissions:
            errors.append(f"Output 6 emissions table missing required column: {column}.")
    future = investment[investment["scenario_id"] != BASELINE_SCENARIO_ID]
    if future.empty:
        errors.append("Output 6 investment table must include non-baseline scenario rows.")
    if "core_subsidy_included" in assumptions and assumptions["core_subsidy_included"].astype(bool).any():
        errors.append("Core Output 6 assumption set must be subsidy-free; subsidy is optional sensitivity only.")
    if errors:
        raise Output6Error("; ".join(errors))
    return {
        "investment_rows": int(len(investment)),
        "component_rows": int(len(components)),
        "pv_value_rows": int(len(pv_value)),
        "emissions_rows": int(len(emissions)),
        "assumption_rows": int(len(assumptions)),
        "service_indicator_rows": int(len(service)),
        "active_cooling_final_energy_columns_present": False,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    tables = subparsers.add_parser("tables", help="Build Output 6 investment/adaptation tables.")
    tables.add_argument("--experiment-root", type=Path, default=DEFAULT_EXPERIMENT_ROOT)
    tables.add_argument("--leaf-index", type=Path, default=DEFAULT_LEAF_INDEX)
    tables.add_argument("--run-registry", type=Path, default=DEFAULT_RUN_REGISTRY)
    tables.add_argument("--assumption-config", type=Path, default=DEFAULT_ASSUMPTION_CONFIG)
    tables.add_argument("--cooling-comparison", type=Path, default=DEFAULT_COOLING_COMPARISON)

    figures = subparsers.add_parser("figures", help="Generate Output 6 figures.")
    figures.add_argument("--experiment-root", type=Path, default=DEFAULT_EXPERIMENT_ROOT)
    figures.add_argument("--figures-root", type=Path, default=DEFAULT_FIGURES_ROOT)
    figures.add_argument("--formats", nargs="+", default=["png", "pdf"])
    figures.add_argument("--reference-tariff-id", default="low_zero_export_value")

    validate = subparsers.add_parser("validate", help="Validate Output 6 outputs.")
    validate.add_argument("--experiment-root", type=Path, default=DEFAULT_EXPERIMENT_ROOT)
    validate.add_argument("--expected-option-count", type=int, default=6)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "tables":
        result = build_output6_tables(
            experiment_root=args.experiment_root,
            leaf_index=args.leaf_index,
            run_registry=args.run_registry,
            assumption_config=args.assumption_config,
            cooling_comparison=args.cooling_comparison,
        )
    elif args.command == "figures":
        result = generate_output6_figures(
            experiment_root=args.experiment_root,
            figures_root=args.figures_root,
            formats=args.formats,
            reference_tariff_id=args.reference_tariff_id,
        )
    elif args.command == "validate":
        result = validate_output6_results(
            experiment_root=args.experiment_root,
            expected_option_count=args.expected_option_count,
        )
    else:
        raise AssertionError(args.command)
    for key, value in result.items():
        print(f"{key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
