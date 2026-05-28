"""Stock-weighted archetype runner for scenario-tree annual demand leaves."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from time import perf_counter
from typing import Any, Mapping

import pandas as pd

from model_v3.simulation.annual_runner import run_annual_simulation
from model_v3.utils.energy import infer_step_durations_seconds, integrate_power_series_kwh


REPO_ROOT = Path(__file__).resolve().parents[3]
CARRIER_POWER_COLUMNS = {
    "electricity_legacy_calibrated": "P_el_total_W",
    "electricity_gross_actual": "P_el_gross_actual_W",
    "electricity_grid_import": "P_el_grid_import_W",
    "electricity_grid_export": "P_el_grid_export_W",
    "pv_generation": "P_pv_generation_W",
    "ev_charging": "P_el_ev_charging_W",
    "natural_gas": "P_gas_total_W",
    "heating_oil": "P_oil_total_W",
    "biomass": "P_biomass_total_W",
    "propane_butane": "P_propane_total_W",
    "coal": "P_coal_total_W",
    "district_heat": "P_district_heat_total_W",
}


class StockWeightedArchetypeError(RuntimeError):
    """Raised when stock-weighted archetype simulation cannot be built."""


def _resolve_repo_path(path_text: str | Path, repo_root: Path = REPO_ROOT) -> Path:
    path = Path(path_text)
    if path.is_absolute():
        return path
    return repo_root / path


def load_stock_weighted_archetypes(config: Mapping[str, Any], repo_root: Path = REPO_ROOT) -> pd.DataFrame:
    """Load and normalize positive archetype stock weights from the configured table."""

    building_cfg = dict(config.get("building", {}))
    archetype_cfg = dict(building_cfg.get("archetype_source", {}))
    table_path = _resolve_repo_path(str(archetype_cfg.get("file_path", "")), repo_root)
    if not table_path.exists():
        raise StockWeightedArchetypeError(f"Missing archetype table for stock-weighted mode: {table_path}")

    frame = pd.read_csv(table_path)
    required = {"archetype_id", "stock_weight"}
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise StockWeightedArchetypeError(
            f"Archetype table for stock-weighted mode missing required column(s): {', '.join(missing)}"
        )
    selected = frame[["archetype_id", "stock_weight"]].copy()
    selected["archetype_id"] = selected["archetype_id"].astype(str)
    selected["stock_weight"] = pd.to_numeric(selected["stock_weight"], errors="coerce").fillna(0.0).clip(lower=0.0)
    selected = selected[selected["stock_weight"] > 0.0].reset_index(drop=True)
    total_weight = float(selected["stock_weight"].sum())
    if selected.empty or total_weight <= 0.0:
        raise StockWeightedArchetypeError(f"No positive stock_weight values found in archetype table: {table_path}")
    selected["normalized_stock_weight"] = selected["stock_weight"] / total_weight
    return selected


def _config_for_archetype(config: Mapping[str, Any], archetype_id: str) -> dict[str, Any]:
    archetype_config = deepcopy(dict(config))
    building_cfg = archetype_config.setdefault("building", {})
    archetype_source = building_cfg.setdefault("archetype_source", {})
    archetype_source["selection"] = "archetype_id"
    archetype_source["archetype_id"] = str(archetype_id)
    return archetype_config


def _timestamps_match(left: pd.Series, right: pd.Series) -> bool:
    if len(left) != len(right):
        return False
    return [pd.Timestamp(value) for value in left] == [pd.Timestamp(value) for value in right]


def _weighted_profile(results: list[dict[str, Any]]) -> pd.DataFrame:
    if not results:
        raise StockWeightedArchetypeError("No archetype simulation results available for weighting.")

    base_frame = pd.DataFrame(results[0]["profile_frame"]).copy()
    if "timestamp" not in base_frame.columns:
        raise StockWeightedArchetypeError("Annual profile is missing timestamp column.")

    output = pd.DataFrame({"timestamp": base_frame["timestamp"]})
    numeric_columns = [
        column
        for column in base_frame.columns
        if column != "timestamp" and pd.api.types.is_numeric_dtype(pd.to_numeric(base_frame[column], errors="coerce"))
    ]
    for column in numeric_columns:
        output[column] = 0.0

    for result in results:
        weight = float(result["normalized_stock_weight"])
        frame = pd.DataFrame(result["profile_frame"]).copy()
        if not _timestamps_match(output["timestamp"], frame["timestamp"]):
            raise StockWeightedArchetypeError("Archetype annual profiles have non-matching timestamps.")
        for column in numeric_columns:
            if column in frame.columns:
                output[column] += pd.to_numeric(frame[column], errors="coerce").fillna(0.0) * weight

    output["archetype_id"] = "stock_weighted_archetype_mix"
    output["heating_technology_type"] = "stock_weighted_archetype_mix"
    output["dhw_technology_type"] = "stock_weighted_archetype_mix"
    return output


def _integrate_optional_column(frame: pd.DataFrame, column_name: str) -> float:
    if column_name not in frame.columns:
        return 0.0
    return integrate_power_series_kwh(
        pd.to_numeric(frame[column_name], errors="coerce").fillna(0.0),
        timestamps=frame["timestamp"],
    )


def _annual_energy_by_carrier(frame: pd.DataFrame) -> dict[str, float]:
    return {
        carrier_name: _integrate_optional_column(frame, column_name)
        for carrier_name, column_name in CARRIER_POWER_COLUMNS.items()
    }


def _representative_timestep_seconds(frame: pd.DataFrame) -> float:
    if "timestamp" not in frame.columns or frame.empty:
        return 0.0
    durations = infer_step_durations_seconds([pd.Timestamp(value) for value in frame["timestamp"]])
    return float(pd.Series(durations).median()) if durations else 0.0


def _column_mean(frame: pd.DataFrame, column_name: str) -> float:
    if column_name not in frame.columns:
        return 0.0
    return float(pd.to_numeric(frame[column_name], errors="coerce").fillna(0.0).mean())


def _column_quantile(frame: pd.DataFrame, column_name: str, q: float) -> float:
    if column_name not in frame.columns:
        return 0.0
    return float(pd.to_numeric(frame[column_name], errors="coerce").fillna(0.0).quantile(q))


def run_stock_weighted_archetype_simulation(config: Mapping[str, Any]) -> dict[str, Any]:
    """Run annual demand for each archetype and return a stock-weighted profile."""

    timings: dict[str, float] = {}
    start = perf_counter()
    weights = load_stock_weighted_archetypes(config)
    timings["load_stock_weighted_archetypes"] = perf_counter() - start

    archetype_results: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    loop_start = perf_counter()
    for _, row in weights.iterrows():
        archetype_id = str(row["archetype_id"])
        weight = float(row["normalized_stock_weight"])
        result = run_annual_simulation(_config_for_archetype(config, archetype_id))
        result["archetype_id"] = archetype_id
        result["normalized_stock_weight"] = weight
        archetype_results.append(result)
        summaries.append(
            {
                "archetype_id": archetype_id,
                "stock_weight": float(row["stock_weight"]),
                "normalized_stock_weight": weight,
                "space_heating_thermal_kWh": float(result.get("space_heating_thermal_kWh", 0.0)),
                "dhw_thermal_kWh": float(result.get("dhw_thermal_kWh", 0.0)),
                "annual_energy_kWh": float(result.get("annual_energy_kWh", 0.0)),
                "annual_grid_import_kWh": float(result.get("annual_grid_import_kWh", 0.0)),
                "annual_gas_kWh": float(dict(result.get("annual_energy_by_carrier_kWh", {})).get("natural_gas", 0.0)),
            }
        )
    timings["archetype_loop_seconds"] = perf_counter() - loop_start

    frame = _weighted_profile(archetype_results)
    annual_energy_by_carrier = _annual_energy_by_carrier(frame)
    annual_energy_kwh = _integrate_optional_column(frame, "P_el_total_W")
    space_heating_thermal_kwh = _integrate_optional_column(frame, "Q_heating_supplied_W")
    dhw_thermal_kwh = _integrate_optional_column(frame, "Q_dhw_demand_W")
    space_heating_electric_kwh = _integrate_optional_column(frame, "P_el_space_heating_W")
    dhw_electric_kwh = _integrate_optional_column(frame, "P_el_dhw_W")
    timestep_seconds = _representative_timestep_seconds(frame)

    return {
        "timestamps": [pd.Timestamp(value).isoformat() for value in frame["timestamp"]],
        "profile_frame": frame,
        "aggregate_profile": frame.get("P_el_total_W", pd.Series(dtype=float)).astype(float).tolist(),
        "mean_profile": _column_mean(frame, "P_el_total_W"),
        "std_profile": float(pd.to_numeric(frame.get("P_el_total_W", pd.Series(dtype=float)), errors="coerce").fillna(0.0).std(ddof=0)),
        "P10_profile": _column_quantile(frame, "P_el_total_W", 0.10),
        "P50_profile": _column_quantile(frame, "P_el_total_W", 0.50),
        "P90_profile": _column_quantile(frame, "P_el_total_W", 0.90),
        "annual_energy_kWh": annual_energy_kwh,
        "annual_energy_by_carrier_kWh": annual_energy_by_carrier,
        "annual_grid_import_kWh": annual_energy_by_carrier["electricity_grid_import"],
        "annual_grid_export_kWh": annual_energy_by_carrier["electricity_grid_export"],
        "annual_pv_generation_kWh": annual_energy_by_carrier["pv_generation"],
        "annual_ev_charging_kWh": annual_energy_by_carrier["ev_charging"],
        "space_heating_energy_kWh": space_heating_electric_kwh,
        "space_heating_electric_kWh": space_heating_electric_kwh,
        "dhw_electric_kWh": dhw_electric_kwh,
        "space_heating_thermal_kWh": space_heating_thermal_kwh,
        "dhw_thermal_kWh": dhw_thermal_kwh,
        "peak_dhw_thermal_W": float(pd.to_numeric(frame.get("Q_dhw_demand_W", pd.Series(dtype=float)), errors="coerce").fillna(0.0).max()),
        "peak_total_thermal_W": float(pd.to_numeric(frame.get("Q_total_thermal_W", pd.Series(dtype=float)), errors="coerce").fillna(0.0).max()),
        "n_steps": int(len(frame)),
        "household_count": 1,
        "profile_representation": "stock_weighted_per_household",
        "timestep_seconds": timestep_seconds,
        "reference_year": dict(config.get("simulation", {})).get("reference_year"),
        "stock_weighted_archetypes": {
            "archetype_count": int(len(weights)),
            "weight_column": "stock_weight",
            "weight_sum_before_normalization": float(weights["stock_weight"].sum()),
            "summary": summaries,
            "interpretation": (
                "Per-household annual profile formed as the stock-weighted mean of deterministic annual "
                "runs over all positive-weight Belgian residential archetypes."
            ),
        },
        "pipeline_timings_seconds": timings,
        "technology_metadata": {
            "carrier_aware_outputs": True,
            "stock_weighted_archetypes": True,
        },
    }
