"""Output 3-4 stochastic cohort pilot for peak stress and diversity metrics."""

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

from model_v3.scenario_tree.naming import DIMENSION_SEPARATOR
from model_v3.scenarios.climate_metrics import compute_climate_metrics
from model_v3.scenarios.registry import latest_actual_run_for_leaf, latest_actual_status, read_registry
from model_v3.scenarios.selection import ScenarioLeafRecord, load_leaf_records
from model_v3.utils.energy import infer_step_durations_seconds, integrate_power_series_kwh


REPO_ROOT = Path(__file__).resolve().parents[3]
SOURCE_EXPERIMENT_ROOT = REPO_ROOT / "experiments" / "scenario_tree"
SOURCE_LEAF_INDEX = SOURCE_EXPERIMENT_ROOT / "manifests" / "scenario_leaf_index.csv"
OUTPUT34_EXPERIMENT_ROOT = REPO_ROOT / "experiments" / "scenario_tree_output34"
OUTPUT34_LEAF_INDEX = OUTPUT34_EXPERIMENT_ROOT / "manifests" / "output34_leaf_index.csv"
OUTPUT34_RUN_REGISTRY = OUTPUT34_EXPERIMENT_ROOT / "manifests" / "run_registry.csv"
OUTPUT34_FIGURES_ROOT = REPO_ROOT / "figures" / "scenario_tree"

OUTPUT34_SCENARIO_IDS = (
    "baseline_1981_2005__historical__tech_current_stock",
    "near_future_2030_2049__rcp_4_5__tech_frozen_stock",
    "mid_century_2050_2070__rcp_4_5__tech_frozen_stock",
    "long_term_2080_2100__rcp_8_5__tech_frozen_stock",
    "mid_century_2050_2070__rcp_4_5__tech_moderate_electrification",
    "long_term_2080_2100__rcp_8_5__tech_moderate_electrification",
    "mid_century_2050_2070__rcp_4_5__tech_high_electrification_pv_ev",
    "long_term_2080_2100__rcp_8_5__tech_high_electrification_pv_ev",
)
OUTPUT34_REALIZATION_IDS = ("seed_0000", "seed_0001", "seed_0002")
DESIGN_YEAR_IDS = ("cold_design_year", "typical_heating_year")
BASELINE_SCENARIO_ID = "baseline_1981_2005__historical__tech_current_stock"
LOAD_DURATION_EXCEEDANCE_PCTS = (0.0, 0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 25.0, 50.0, 75.0, 90.0, 95.0, 99.0, 100.0)
DIVERSITY_COUNTS = (1, 5, 10, 20, 30, 50, 100)
ACTIVE_COOLING_COLUMN_NAMES = {
    "active_cooling_final_energy_kWh",
    "cooling_final_energy_kWh",
    "cooling_electricity_kWh",
}


class Output34Error(RuntimeError):
    """Raised when Output 3-4 pilot generation or summarisation fails."""


def _resolve_repo_path(path_text: str | Path) -> Path:
    path = Path(path_text)
    if path.is_absolute():
        return path
    return REPO_ROOT / path


def _path_for_yaml(path: str | Path) -> str:
    path = Path(path)
    try:
        return str(path.resolve().relative_to(REPO_ROOT.resolve()))
    except ValueError:
        return str(path)


def _load_yaml(path: Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict):
        raise Output34Error(f"YAML file must contain a mapping: {path}")
    return data


def _write_yaml(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(dict(payload), handle, sort_keys=False)


def _write_csv(path: Path, rows: list[Mapping[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _scenario_dimensions(scenario_id: str) -> tuple[str, str, str]:
    parts = scenario_id.split(DIMENSION_SEPARATOR)
    if len(parts) != 3:
        raise Output34Error(f"Invalid scenario_id for Output 3-4: {scenario_id}")
    return parts[0], parts[1], parts[2]


def _seed_index(realization_id: str) -> int:
    return int(str(realization_id).split("_")[-1])


def _year_window(year: int) -> tuple[str, str]:
    return f"{int(year)}-01-01", f"{int(year)}-12-31"


def _leaf_id(scenario_id: str, design_year_id: str, realization_id: str) -> str:
    return DIMENSION_SEPARATOR.join((scenario_id, design_year_id, realization_id))


def _records_by_leaf(records: Iterable[ScenarioLeafRecord]) -> dict[str, ScenarioLeafRecord]:
    return {record.scenario_leaf_id: record for record in records}


def select_design_years_from_climate(
    climate_forcing_file: Path,
    *,
    analysis_start: str,
    analysis_end: str,
) -> dict[str, dict[str, float | int]]:
    """Select cold and typical heating design years using annual HDD_18."""

    climate_result = compute_climate_metrics(
        climate_forcing_file,
        analysis_start=analysis_start,
        analysis_end=analysis_end,
    )
    annual = pd.DataFrame(climate_result.annual_metrics)
    if annual.empty or "HDD_18" not in annual:
        raise Output34Error(f"No annual HDD_18 rows available in climate forcing file: {climate_forcing_file}")
    annual["HDD_18"] = pd.to_numeric(annual["HDD_18"], errors="coerce")
    annual = annual.dropna(subset=["HDD_18"]).sort_values("year").reset_index(drop=True)
    if annual.empty:
        raise Output34Error(f"Annual HDD_18 values are empty for climate forcing file: {climate_forcing_file}")

    cold_row = annual.sort_values(["HDD_18", "year"], ascending=[False, True]).iloc[0]
    median_hdd = float(annual["HDD_18"].median())
    typical_row = (
        annual.assign(_distance=(annual["HDD_18"] - median_hdd).abs())
        .sort_values(["_distance", "year"])
        .iloc[0]
    )
    return {
        "cold_design_year": {
            "year": int(cold_row["year"]),
            "HDD_18": float(cold_row["HDD_18"]),
            "selection_rule": "highest annual HDD_18 inside the climate window",
        },
        "typical_heating_year": {
            "year": int(typical_row["year"]),
            "HDD_18": float(typical_row["HDD_18"]),
            "selection_rule": "annual HDD_18 closest to the climate-window median",
        },
    }


def _clone_run_config(
    source_config: Mapping[str, Any],
    *,
    output_leaf_id: str,
    scenario_id: str,
    design_year_id: str,
    design_year: int,
    experiment_root: Path,
    cohort_size: int,
    target_resolution_seconds: int,
) -> dict[str, Any]:
    run_dir = experiment_root / "runs" / output_leaf_id
    analysis_start, analysis_end = _year_window(design_year)
    config = yaml.safe_load(yaml.safe_dump(dict(source_config), sort_keys=False))
    scenario_leaf = dict(config.get("scenario_leaf", {}))
    scenario_leaf["id"] = output_leaf_id
    scenario_leaf["scenario_id"] = scenario_id
    scenario_leaf["design_year_id"] = design_year_id
    scenario_leaf["design_year"] = int(design_year)
    config["scenario_leaf"] = scenario_leaf

    climate_cfg = dict(config.get("climate", {}))
    climate_cfg["analysis_start"] = analysis_start
    climate_cfg["analysis_end"] = analysis_end
    climate_cfg["design_year_id"] = design_year_id
    climate_cfg["design_year"] = int(design_year)
    climate_cfg["inclusive_dates"] = True
    config["climate"] = climate_cfg

    stochastic_cfg = dict(config.get("stochastic", {}))
    stochastic_cfg["cohort_size"] = int(cohort_size)
    config["stochastic"] = stochastic_cfg

    model_options = dict(config.get("model_options", {}))
    model_options["runner_mode"] = "stochastic_cohort"
    model_options["use_stochastic_cohort"] = True
    model_options["target_resolution_seconds"] = int(target_resolution_seconds)
    model_options["climate_forcing_temporal_note"] = (
        "Daily CORDEX forcing is forward-filled to the hourly model timestep; "
        "hourly variation comes from stochastic demand, occupancy, DHW, appliances, PV/EV, and heating control."
    )
    config["model_options"] = model_options

    config["output"] = {
        "run_dir": _path_for_yaml(run_dir),
        "outputs_dir": _path_for_yaml(run_dir / "outputs"),
        "logs_dir": _path_for_yaml(run_dir / "logs"),
    }
    config["validation"] = {"config_complete": True, "missing_required_inputs": []}
    return config


def _clone_inputs_manifest(
    source_manifest: Mapping[str, Any],
    *,
    output_leaf_id: str,
    scenario_id: str,
    design_year_id: str,
    design_year: int,
    cohort_size: int,
) -> dict[str, Any]:
    analysis_start, analysis_end = _year_window(design_year)
    manifest = yaml.safe_load(yaml.safe_dump(dict(source_manifest), sort_keys=False))
    manifest["status"] = "configured_not_run"
    manifest["scenario_leaf_id"] = output_leaf_id
    manifest["scenario_id"] = scenario_id
    manifest["design_year_id"] = design_year_id
    manifest["design_year"] = int(design_year)
    climate = dict(manifest.get("climate_forcing", {}))
    climate["analysis_start"] = analysis_start
    climate["analysis_end"] = analysis_end
    climate["design_year_id"] = design_year_id
    climate["design_year"] = int(design_year)
    manifest["climate_forcing"] = climate
    stochastic = dict(manifest.get("stochastic", {}))
    stochastic["cohort_size"] = int(cohort_size)
    manifest["stochastic"] = stochastic
    manifest["validation"] = {"config_complete": True, "missing_required_inputs": []}
    return manifest


def generate_output34_pilot_configs(
    *,
    source_leaf_index: Path = SOURCE_LEAF_INDEX,
    experiment_root: Path = OUTPUT34_EXPERIMENT_ROOT,
    scenario_ids: Iterable[str] = OUTPUT34_SCENARIO_IDS,
    realization_ids: Iterable[str] = OUTPUT34_REALIZATION_IDS,
    design_year_ids: Iterable[str] = DESIGN_YEAR_IDS,
    cohort_size: int = 30,
    target_resolution_seconds: int = 3600,
) -> dict[str, Any]:
    """Create dedicated Output 3-4 stochastic-cohort design-year run configs."""

    experiment_root = Path(experiment_root)
    source_records = _records_by_leaf(load_leaf_records(Path(source_leaf_index)))
    scenario_ids = tuple(scenario_ids)
    realization_ids = tuple(realization_ids)
    design_year_ids = tuple(design_year_ids)
    unknown_design_years = sorted(set(design_year_ids).difference(DESIGN_YEAR_IDS))
    if unknown_design_years:
        raise Output34Error(f"Unsupported Output 3-4 design-year ids: {', '.join(unknown_design_years)}")
    selected_years: dict[str, dict[str, dict[str, float | int]]] = {}
    output_rows: list[dict[str, Any]] = []
    design_rows: list[dict[str, Any]] = []

    for scenario_id in scenario_ids:
        climate_window_id, climate_pathway_id, technology_case_id = _scenario_dimensions(scenario_id)
        first_source_leaf_id = f"{scenario_id}__{realization_ids[0]}"
        first_record = source_records.get(first_source_leaf_id)
        if first_record is None:
            raise Output34Error(f"Source scenario leaf not found: {first_source_leaf_id}")
        first_config = _load_yaml(_resolve_repo_path(first_record.row["run_config_path"]))
        climate_cfg = dict(first_config.get("climate", {}))
        design_years = select_design_years_from_climate(
            _resolve_repo_path(str(climate_cfg.get("forcing_file", ""))),
            analysis_start=str(climate_cfg.get("analysis_start", "")),
            analysis_end=str(climate_cfg.get("analysis_end", "")),
        )
        selected_years[scenario_id] = design_years
        for design_year_id in design_year_ids:
            payload = design_years[design_year_id]
            design_rows.append(
                {
                    "scenario_id": scenario_id,
                    "climate_window_id": climate_window_id,
                    "climate_pathway_id": climate_pathway_id,
                    "technology_case_id": technology_case_id,
                    "design_year_id": design_year_id,
                    "design_year": int(payload["year"]),
                    "HDD_18": float(payload["HDD_18"]),
                    "selection_rule": str(payload["selection_rule"]),
                }
            )

        for realization_id in realization_ids:
            source_leaf_id = f"{scenario_id}__{realization_id}"
            source_record = source_records.get(source_leaf_id)
            if source_record is None:
                raise Output34Error(f"Source scenario leaf not found: {source_leaf_id}")
            source_config_path = _resolve_repo_path(source_record.row["run_config_path"])
            source_manifest_path = _resolve_repo_path(source_record.row["inputs_manifest_path"])
            source_config = _load_yaml(source_config_path)
            source_manifest = _load_yaml(source_manifest_path)

            for design_year_id in design_year_ids:
                design_year = int(design_years[design_year_id]["year"])
                output_leaf_id = _leaf_id(scenario_id, design_year_id, realization_id)
                run_dir = experiment_root / "runs" / output_leaf_id
                run_config_path = run_dir / "run_config.yaml"
                inputs_manifest_path = run_dir / "inputs_manifest.yaml"
                run_dir.mkdir(parents=True, exist_ok=True)
                (run_dir / "outputs").mkdir(parents=True, exist_ok=True)
                (run_dir / "logs").mkdir(parents=True, exist_ok=True)

                run_config = _clone_run_config(
                    source_config,
                    output_leaf_id=output_leaf_id,
                    scenario_id=scenario_id,
                    design_year_id=design_year_id,
                    design_year=design_year,
                    experiment_root=experiment_root,
                    cohort_size=cohort_size,
                    target_resolution_seconds=target_resolution_seconds,
                )
                inputs_manifest = _clone_inputs_manifest(
                    source_manifest,
                    output_leaf_id=output_leaf_id,
                    scenario_id=scenario_id,
                    design_year_id=design_year_id,
                    design_year=design_year,
                    cohort_size=cohort_size,
                )
                _write_yaml(run_config_path, run_config)
                _write_yaml(inputs_manifest_path, inputs_manifest)

                output_rows.append(
                    {
                        "scenario_leaf_id": output_leaf_id,
                        "scenario_id": scenario_id,
                        "climate_window_id": climate_window_id,
                        "climate_pathway_id": climate_pathway_id,
                        "technology_case_id": technology_case_id,
                        "design_year_id": design_year_id,
                        "design_year": design_year,
                        "realization_id": realization_id,
                        "canonical_start": f"{design_year}-01-01",
                        "canonical_end": f"{design_year}-12-31",
                        "source_file_window": source_record.row.get("source_file_window", ""),
                        "scenario_config_dir": _path_for_yaml(experiment_root / "configs" / scenario_id),
                        "realization_config_path": _path_for_yaml(experiment_root / "configs" / scenario_id / f"{design_year_id}__{realization_id}.yaml"),
                        "run_dir": _path_for_yaml(run_dir),
                        "run_config_path": _path_for_yaml(run_config_path),
                        "inputs_manifest_path": _path_for_yaml(inputs_manifest_path),
                        "outputs_dir": _path_for_yaml(run_dir / "outputs"),
                        "logs_dir": _path_for_yaml(run_dir / "logs"),
                        "climate_forcing_reference": source_record.row.get("climate_forcing_reference", ""),
                        "technology_config_reference": source_record.row.get("technology_config_reference", ""),
                    }
                )

    fieldnames = [
        "scenario_leaf_id",
        "scenario_id",
        "climate_window_id",
        "climate_pathway_id",
        "technology_case_id",
        "design_year_id",
        "design_year",
        "realization_id",
        "canonical_start",
        "canonical_end",
        "source_file_window",
        "scenario_config_dir",
        "realization_config_path",
        "run_dir",
        "run_config_path",
        "inputs_manifest_path",
        "outputs_dir",
        "logs_dir",
        "climate_forcing_reference",
        "technology_config_reference",
    ]
    _write_csv(experiment_root / "manifests" / "output34_leaf_index.csv", output_rows, fieldnames)
    _write_csv(
        experiment_root / "manifests" / "output34_design_year_selection.csv",
        design_rows,
        [
            "scenario_id",
            "climate_window_id",
            "climate_pathway_id",
            "technology_case_id",
            "design_year_id",
            "design_year",
            "HDD_18",
            "selection_rule",
        ],
    )
    return {
        "leaf_index_path": experiment_root / "manifests" / "output34_leaf_index.csv",
        "design_year_selection_path": experiment_root / "manifests" / "output34_design_year_selection.csv",
        "leaf_count": len(output_rows),
        "scenario_count": len(scenario_ids),
        "design_year_count": len(design_year_ids),
        "cohort_size": int(cohort_size),
        "target_resolution_seconds": int(target_resolution_seconds),
    }


def _timestamps(frame: pd.DataFrame) -> pd.Series:
    if "timestamp" not in frame:
        raise Output34Error("Profile frame must contain a timestamp column.")
    return pd.to_datetime(frame["timestamp"], utc=True)


def _durations_seconds(timestamps: Iterable[Any]) -> np.ndarray:
    return np.asarray(infer_step_durations_seconds(list(timestamps)), dtype=float)


def weighted_percentile(values: Iterable[Any], weights: Iterable[Any], q: float) -> float:
    """Return a weighted percentile where q is in [0, 1]."""

    values_array = pd.to_numeric(pd.Series(list(values)), errors="coerce").fillna(0.0).to_numpy(dtype=float)
    weights_array = pd.to_numeric(pd.Series(list(weights)), errors="coerce").fillna(0.0).to_numpy(dtype=float)
    if values_array.size == 0:
        return float("nan")
    if weights_array.size != values_array.size or float(weights_array.sum()) <= 0.0:
        return float(np.percentile(values_array, q * 100.0))
    order = np.argsort(values_array)
    sorted_values = values_array[order]
    sorted_weights = weights_array[order]
    cutoff = float(q) * float(sorted_weights.sum())
    cumulative = np.cumsum(sorted_weights)
    index = int(np.searchsorted(cumulative, cutoff, side="left"))
    index = min(max(index, 0), len(sorted_values) - 1)
    return float(sorted_values[index])


def top_fraction_metrics(values: Iterable[Any], durations_seconds: Iterable[Any], fraction: float = 0.01) -> dict[str, float]:
    values_array = pd.to_numeric(pd.Series(list(values)), errors="coerce").fillna(0.0).to_numpy(dtype=float)
    durations = pd.to_numeric(pd.Series(list(durations_seconds)), errors="coerce").fillna(0.0).to_numpy(dtype=float)
    if values_array.size == 0:
        return {"top_fraction_hours": 0.0, "top_fraction_mean_W": float("nan")}
    if len(durations) != len(values_array) or float(durations.sum()) <= 0.0:
        durations = np.ones(len(values_array), dtype=float) * 3600.0
    target_seconds = max(float(durations.sum()) * float(fraction), 0.0)
    order = np.argsort(values_array)[::-1]
    selected_seconds = 0.0
    weighted_sum = 0.0
    for index in order:
        if selected_seconds >= target_seconds and selected_seconds > 0.0:
            break
        step_seconds = float(durations[index])
        selected_seconds += step_seconds
        weighted_sum += float(values_array[index]) * step_seconds
    mean_w = weighted_sum / selected_seconds if selected_seconds > 0.0 else float("nan")
    return {
        "top_fraction_hours": selected_seconds / 3600.0,
        "top_fraction_mean_W": float(mean_w),
    }


def _column_or_zeros(frame: pd.DataFrame, column: str, *, fallback_column: str | None = None) -> pd.Series:
    if column in frame:
        return pd.to_numeric(frame[column], errors="coerce").fillna(0.0)
    if fallback_column and fallback_column in frame:
        return pd.to_numeric(frame[fallback_column], errors="coerce").fillna(0.0)
    return pd.Series(np.zeros(len(frame)), index=frame.index, dtype=float)


def compute_peak_grid_metrics(
    profile: pd.DataFrame,
    *,
    stress_threshold_W: float | None = None,
) -> dict[str, float]:
    """Compute duration-aware peak and grid-stress metrics for one aggregate profile."""

    timestamps = _timestamps(profile)
    durations = _durations_seconds(timestamps)
    grid = _column_or_zeros(profile, "P_el_grid_import_W", fallback_column="aggregate_profile_W")
    gross = _column_or_zeros(profile, "P_el_gross_actual_W", fallback_column="aggregate_profile_W")
    gas = _column_or_zeros(profile, "P_gas_total_W")
    heating = _column_or_zeros(profile, "Q_heating_supplied_W")
    carrier_columns = [
        column
        for column in (
            "P_gas_total_W",
            "P_oil_total_W",
            "P_biomass_total_W",
            "P_propane_total_W",
            "P_coal_total_W",
            "P_district_heat_total_W",
        )
        if column in profile
    ]
    carrier_total = sum(pd.to_numeric(profile[column], errors="coerce").fillna(0.0) for column in carrier_columns)
    final_power = gross + carrier_total
    top = top_fraction_metrics(grid, durations, fraction=0.01)
    threshold = float(stress_threshold_W) if stress_threshold_W is not None and math.isfinite(float(stress_threshold_W)) else float("nan")
    stress_hours = float(durations[np.asarray(grid > threshold, dtype=bool)].sum() / 3600.0) if math.isfinite(threshold) else float("nan")
    mean_grid = float(np.average(grid, weights=durations)) if float(durations.sum()) > 0.0 else float(grid.mean())
    peak_grid = float(grid.max()) if not grid.empty else float("nan")
    return {
        "n_timesteps": int(len(profile)),
        "total_profile_hours": float(durations.sum() / 3600.0),
        "mean_timestep_hours": float(np.mean(durations) / 3600.0) if len(durations) else float("nan"),
        "annual_grid_import_kWh": integrate_power_series_kwh(grid, timestamps=timestamps),
        "peak_grid_import_W": peak_grid,
        "p95_grid_import_W": weighted_percentile(grid, durations, 0.95),
        "p99_grid_import_W": weighted_percentile(grid, durations, 0.99),
        "top_1pct_load_hours": top["top_fraction_hours"],
        "top_1pct_grid_import_W_mean": top["top_fraction_mean_W"],
        "hours_above_grid_stress_threshold": stress_hours,
        "grid_import_load_factor": mean_grid / peak_grid if peak_grid > 0.0 else float("nan"),
        "peak_useful_heating_W": float(heating.max()) if not heating.empty else float("nan"),
        "peak_gas_W": float(gas.max()) if not gas.empty else float("nan"),
        "peak_total_final_energy_W": float(final_power.max()) if not final_power.empty else float("nan"),
    }


def load_duration_samples(values: Iterable[Any], exceedance_pcts: Iterable[float] = LOAD_DURATION_EXCEEDANCE_PCTS) -> list[dict[str, float]]:
    values_array = pd.to_numeric(pd.Series(list(values)), errors="coerce").fillna(0.0).to_numpy(dtype=float)
    if values_array.size == 0:
        return []
    sorted_values = np.sort(values_array)[::-1]
    rows = []
    for pct in exceedance_pcts:
        if len(sorted_values) == 1:
            value = float(sorted_values[0])
        else:
            rank = (float(pct) / 100.0) * (len(sorted_values) - 1)
            value = float(np.interp(rank, np.arange(len(sorted_values)), sorted_values))
        rows.append({"exceedance_pct": float(pct), "grid_import_W": value})
    return rows


def diversity_factor_from_matrix(matrix: np.ndarray, aggregate_profile: np.ndarray | None = None) -> float:
    matrix = np.asarray(matrix, dtype=float)
    if matrix.ndim != 2 or matrix.size == 0:
        return float("nan")
    aggregate = matrix.sum(axis=0) if aggregate_profile is None else np.asarray(aggregate_profile, dtype=float)
    aggregate_peak = float(np.max(aggregate)) if aggregate.size else 0.0
    if aggregate_peak <= 0.0:
        return float("nan")
    return float(np.max(matrix, axis=1).sum() / aggregate_peak)


def diversity_by_household_count(
    matrix: np.ndarray,
    *,
    counts: Iterable[int] = DIVERSITY_COUNTS,
    iterations: int = 30,
    random_seed: int = 0,
) -> list[dict[str, float]]:
    matrix = np.asarray(matrix, dtype=float)
    if matrix.ndim != 2 or matrix.shape[0] == 0:
        return []
    rng = np.random.default_rng(int(random_seed))
    household_count = int(matrix.shape[0])
    rows: list[dict[str, float]] = []
    for requested_count in counts:
        if int(requested_count) > household_count:
            continue
        count = max(int(requested_count), 1)
        factors: list[float] = []
        sample_iterations = 1 if count == household_count else max(int(iterations), 1)
        for _ in range(sample_iterations):
            indices = np.arange(household_count) if count == household_count else rng.choice(household_count, size=count, replace=False)
            subset = matrix[indices, :]
            factors.append(diversity_factor_from_matrix(subset))
        values = pd.Series(factors, dtype=float).dropna()
        if values.empty:
            continue
        rows.append(
            {
                "n_households": int(count),
                "n_bootstrap_samples": int(len(values)),
                "diversity_factor_mean": float(values.mean()),
                "diversity_factor_p10": float(values.quantile(0.10)),
                "diversity_factor_p50": float(values.quantile(0.50)),
                "diversity_factor_p90": float(values.quantile(0.90)),
            }
        )
    return rows


def _read_matrix(path: Path) -> tuple[pd.Series, np.ndarray]:
    frame = pd.read_csv(path)
    if "timestamp" not in frame:
        raise Output34Error(f"Household matrix file lacks timestamp column: {path}")
    value_columns = [column for column in frame.columns if column != "timestamp"]
    matrix = frame[value_columns].apply(pd.to_numeric, errors="coerce").fillna(0.0).to_numpy(dtype=float).T
    return pd.to_datetime(frame["timestamp"], utc=True), matrix


def _profile_column(frame: pd.DataFrame, column: str) -> pd.Series:
    if column in frame:
        return pd.to_numeric(frame[column], errors="coerce").fillna(0.0)
    return pd.Series(np.zeros(len(frame)), dtype=float)


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


def _metadata_for_record(record: ScenarioLeafRecord) -> dict[str, Any]:
    row = dict(record.row)
    return {
        "scenario_leaf_id": record.scenario_leaf_id,
        "scenario_id": record.scenario_id,
        "climate_window_id": record.climate_window_id,
        "climate_pathway_id": record.climate_pathway_id,
        "technology_case_id": record.technology_case_id,
        "design_year_id": row.get("design_year_id", record.design_year_id),
        "design_year": int(row.get("design_year", 0) or 0),
        "realization_id": record.realization_id,
    }


def build_output34_tables(
    *,
    experiment_root: Path = OUTPUT34_EXPERIMENT_ROOT,
    leaf_index: Path = OUTPUT34_LEAF_INDEX,
    run_registry: Path = OUTPUT34_RUN_REGISTRY,
) -> dict[str, Any]:
    """Build Output 3-4 comparison tables from successful cohort pilot leaves."""

    experiment_root = Path(experiment_root)
    records = load_leaf_records(Path(leaf_index))
    registry_rows = read_registry(Path(run_registry))
    leaf_metric_rows: list[dict[str, Any]] = []
    load_duration_leaf_rows: list[dict[str, Any]] = []
    diversity_leaf_rows: list[dict[str, Any]] = []
    diversity_count_leaf_rows: list[dict[str, Any]] = []

    for record in records:
        if latest_actual_status(registry_rows, record.scenario_leaf_id) != "success":
            continue
        registry_row = latest_actual_run_for_leaf(registry_rows, record.scenario_leaf_id)
        if registry_row is None:
            continue
        meta = _metadata_for_record(record)
        outputs_dir = _resolve_repo_path(str(record.row.get("outputs_dir") or registry_row.get("output_path", "")))
        profile_path = outputs_dir / "annual_profile.csv"
        if not profile_path.exists():
            raise Output34Error(f"Missing annual_profile.csv for successful Output 3-4 leaf: {profile_path}")
        profile = pd.read_csv(profile_path)
        run_config = _load_yaml(_resolve_repo_path(record.row["run_config_path"]))
        model_options = dict(run_config.get("model_options", {}))
        row = {
            **meta,
            "run_status": "success",
            "runner_mode": model_options.get("runner_mode", ""),
            "target_resolution_seconds": int(model_options.get("target_resolution_seconds", 0) or 0),
            "cohort_size": int(dict(run_config.get("stochastic", {})).get("cohort_size", 0) or 0),
            "climate_forcing_temporal_note": model_options.get("climate_forcing_temporal_note", ""),
            "active_cooling_final_energy_kWh_included": False,
        }
        row.update(compute_peak_grid_metrics(profile))
        leaf_metric_rows.append(row)

        for sample in load_duration_samples(_profile_column(profile, "P_el_grid_import_W")):
            load_duration_leaf_rows.append({**meta, **sample})

        matrix_path = outputs_dir / "household_grid_import_matrix.csv"
        if matrix_path.exists():
            _, matrix = _read_matrix(matrix_path)
            aggregate = _profile_column(profile, "P_el_grid_import_W").to_numpy(dtype=float)
            household_peaks = np.max(matrix, axis=1) if matrix.size else np.asarray([], dtype=float)
            diversity = {
                **meta,
                "n_households": int(matrix.shape[0]) if matrix.ndim == 2 else 0,
                "sum_household_peak_grid_import_W": float(household_peaks.sum()) if household_peaks.size else float("nan"),
                "aggregate_peak_grid_import_W": float(np.max(aggregate)) if aggregate.size else float("nan"),
                "diversity_factor_grid_import": diversity_factor_from_matrix(matrix, aggregate),
                "household_peak_grid_import_W_mean": float(np.mean(household_peaks)) if household_peaks.size else float("nan"),
                "household_peak_grid_import_W_p10": float(np.percentile(household_peaks, 10)) if household_peaks.size else float("nan"),
                "household_peak_grid_import_W_p50": float(np.percentile(household_peaks, 50)) if household_peaks.size else float("nan"),
                "household_peak_grid_import_W_p90": float(np.percentile(household_peaks, 90)) if household_peaks.size else float("nan"),
            }
            diversity_leaf_rows.append(diversity)
            seed = _seed_index(record.realization_id)
            for count_row in diversity_by_household_count(matrix, random_seed=seed):
                diversity_count_leaf_rows.append({**meta, **count_row})

    if not leaf_metric_rows:
        raise Output34Error("No successful Output 3-4 cohort leaves were found to summarize.")

    leaf_metrics_df = pd.DataFrame(leaf_metric_rows)
    baseline_cold = leaf_metrics_df[
        (leaf_metrics_df["scenario_id"] == BASELINE_SCENARIO_ID)
        & (leaf_metrics_df["design_year_id"] == "cold_design_year")
    ]
    if baseline_cold.empty:
        raise Output34Error("Cannot define grid-stress threshold: baseline cold-design-year leaves are missing.")
    stress_threshold = float(pd.to_numeric(baseline_cold["p99_grid_import_W"], errors="coerce").mean())
    leaf_metrics_df["stress_threshold_grid_import_W"] = stress_threshold
    # Recompute the threshold-dependent metric deterministically by leaf id.
    threshold_hours: dict[str, float] = {}
    for record in records:
        if record.scenario_leaf_id not in set(leaf_metrics_df["scenario_leaf_id"]):
            continue
        outputs_dir = _resolve_repo_path(str(record.row.get("outputs_dir", "")))
        profile = pd.read_csv(outputs_dir / "annual_profile.csv")
        threshold_hours[record.scenario_leaf_id] = compute_peak_grid_metrics(
            profile,
            stress_threshold_W=stress_threshold,
        )["hours_above_grid_stress_threshold"]
    leaf_metrics_df["hours_above_grid_stress_threshold"] = leaf_metrics_df["scenario_leaf_id"].map(threshold_hours)

    group_cols = ["scenario_id", "climate_window_id", "climate_pathway_id", "technology_case_id", "design_year_id", "design_year"]
    peak_metrics = [
        "peak_grid_import_W",
        "p95_grid_import_W",
        "p99_grid_import_W",
        "top_1pct_load_hours",
        "top_1pct_grid_import_W_mean",
        "hours_above_grid_stress_threshold",
        "grid_import_load_factor",
        "peak_useful_heating_W",
        "peak_gas_W",
        "peak_total_final_energy_W",
    ]
    comparison_rows: list[dict[str, Any]] = []
    grouped_means: dict[tuple[str, str], dict[str, float]] = {}
    for group_values, group in leaf_metrics_df.groupby(group_cols, dropna=False):
        base_row = dict(zip(group_cols, group_values))
        base_row["n_successful_runs"] = int(len(group))
        base_row["n_households"] = int(pd.to_numeric(group["cohort_size"], errors="coerce").median())
        base_row["target_resolution_seconds"] = int(pd.to_numeric(group["target_resolution_seconds"], errors="coerce").median())
        base_row["stress_threshold_grid_import_W"] = stress_threshold
        base_row["active_cooling_final_energy_kWh_included"] = False
        base_row["climate_forcing_temporal_note"] = str(group["climate_forcing_temporal_note"].dropna().iloc[0])
        for metric in peak_metrics:
            base_row.update(_summary_stats(group[metric], metric))
        grouped_means[(str(base_row["scenario_id"]), str(base_row["design_year_id"]))] = {
            metric: float(base_row[f"{metric}_mean"]) for metric in peak_metrics
        }
        comparison_rows.append(base_row)

    for row in comparison_rows:
        baseline = grouped_means.get((BASELINE_SCENARIO_ID, str(row["design_year_id"])), {})
        row["baseline_scenario_id"] = BASELINE_SCENARIO_ID
        for metric in peak_metrics:
            baseline_value = baseline.get(metric, float("nan"))
            value = float(row.get(f"{metric}_mean", float("nan")))
            row[f"baseline_{metric}_mean"] = baseline_value
            row[f"delta_{metric}_abs"] = value - baseline_value if math.isfinite(value) and math.isfinite(baseline_value) else float("nan")
            row[f"delta_{metric}_pct"] = _pct_delta(value, baseline_value)
    peak_comparison_df = pd.DataFrame(comparison_rows).sort_values(group_cols).reset_index(drop=True)

    load_duration_df = pd.DataFrame(load_duration_leaf_rows)
    load_duration_samples_df = (
        load_duration_df.groupby(group_cols + ["exceedance_pct"], dropna=False)["grid_import_W"]
        .agg(
            grid_import_W_mean="mean",
            grid_import_W_p10=lambda series: float(series.quantile(0.10)),
            grid_import_W_p50=lambda series: float(series.quantile(0.50)),
            grid_import_W_p90=lambda series: float(series.quantile(0.90)),
        )
        .reset_index()
        .sort_values(group_cols + ["exceedance_pct"])
    )

    distribution_rows: list[dict[str, Any]] = []
    for group_values, group in leaf_metrics_df.groupby(group_cols, dropna=False):
        base_row = dict(zip(group_cols, group_values))
        base_row["n_successful_runs"] = int(len(group))
        base_row["uncertainty_band_basis"] = "P10/P50/P90 across cohort realizations for each design-year scenario."
        for _, leaf in group.iterrows():
            pass
        distribution_series_rows = []
        for record in records:
            if record.scenario_leaf_id not in set(group["scenario_leaf_id"]):
                continue
            profile = pd.read_csv(_resolve_repo_path(str(record.row.get("outputs_dir", ""))) / "annual_profile.csv")
            timestamps = _timestamps(profile)
            durations = _durations_seconds(timestamps)
            grid = _profile_column(profile, "P_el_grid_import_W")
            mean_w = float(np.average(grid, weights=durations)) if float(durations.sum()) > 0 else float(grid.mean())
            std_w = float(grid.std(ddof=0))
            p10 = weighted_percentile(grid, durations, 0.10)
            p50 = weighted_percentile(grid, durations, 0.50)
            p90 = weighted_percentile(grid, durations, 0.90)
            distribution_series_rows.append(
                {
                    "mean_grid_import_W": mean_w,
                    "std_grid_import_W": std_w,
                    "cv_grid_import": std_w / mean_w if mean_w > 0 else float("nan"),
                    "p10_grid_import_W": p10,
                    "p50_grid_import_W": p50,
                    "p90_grid_import_W": p90,
                    "w90_10_grid_import_W": p90 - p10,
                }
            )
        dist_frame = pd.DataFrame(distribution_series_rows)
        for metric in [
            "mean_grid_import_W",
            "std_grid_import_W",
            "cv_grid_import",
            "p10_grid_import_W",
            "p50_grid_import_W",
            "p90_grid_import_W",
            "w90_10_grid_import_W",
        ]:
            base_row.update(_summary_stats(dist_frame[metric], metric))
        distribution_rows.append(base_row)
    distribution_df = pd.DataFrame(distribution_rows).sort_values(group_cols).reset_index(drop=True)

    diversity_df = pd.DataFrame(diversity_leaf_rows)
    if diversity_df.empty:
        diversity_comparison_df = pd.DataFrame(columns=group_cols)
    else:
        diversity_metrics = [
            "diversity_factor_grid_import",
            "sum_household_peak_grid_import_W",
            "aggregate_peak_grid_import_W",
            "household_peak_grid_import_W_mean",
            "household_peak_grid_import_W_p10",
            "household_peak_grid_import_W_p50",
            "household_peak_grid_import_W_p90",
        ]
        rows = []
        for group_values, group in diversity_df.groupby(group_cols, dropna=False):
            row = dict(zip(group_cols, group_values))
            row["n_successful_runs"] = int(len(group))
            row["n_households"] = int(pd.to_numeric(group["n_households"], errors="coerce").median())
            for metric in diversity_metrics:
                row.update(_summary_stats(group[metric], metric))
            rows.append(row)
        diversity_comparison_df = pd.DataFrame(rows).sort_values(group_cols).reset_index(drop=True)

    diversity_count_df = pd.DataFrame(diversity_count_leaf_rows)
    if diversity_count_df.empty:
        diversity_count_comparison_df = pd.DataFrame(columns=group_cols + ["n_households"])
    else:
        rows = []
        for group_values, group in diversity_count_df.groupby(group_cols + ["n_households"], dropna=False):
            row = dict(zip(group_cols + ["n_households"], group_values))
            row["n_successful_runs"] = int(group["scenario_leaf_id"].nunique())
            for metric in ["diversity_factor_mean", "diversity_factor_p10", "diversity_factor_p50", "diversity_factor_p90"]:
                row.update(_summary_stats(group[metric], metric))
            rows.append(row)
        diversity_count_comparison_df = (
            pd.DataFrame(rows)
            .sort_values(group_cols + ["n_households"])
            .reset_index(drop=True)
        )

    comparison_dir = experiment_root / "summaries" / "comparison_level"
    realization_dir = experiment_root / "summaries" / "realization_level"
    comparison_dir.mkdir(parents=True, exist_ok=True)
    realization_dir.mkdir(parents=True, exist_ok=True)
    leaf_metrics_path = realization_dir / "output34_leaf_peak_diversity_metrics.csv"
    peak_path = comparison_dir / "peak_grid_stress_comparison.csv"
    load_duration_path = comparison_dir / "peak_grid_stress_load_duration_samples.csv"
    distribution_path = comparison_dir / "demand_distribution_uncertainty_comparison.csv"
    diversity_path = comparison_dir / "diversity_factor_comparison.csv"
    diversity_count_path = comparison_dir / "diversity_factor_by_household_count.csv"
    leaf_metrics_df.to_csv(leaf_metrics_path, index=False)
    peak_comparison_df.to_csv(peak_path, index=False)
    load_duration_samples_df.to_csv(load_duration_path, index=False)
    distribution_df.to_csv(distribution_path, index=False)
    diversity_comparison_df.to_csv(diversity_path, index=False)
    diversity_count_comparison_df.to_csv(diversity_count_path, index=False)
    return {
        "leaf_metrics_path": leaf_metrics_path,
        "peak_grid_stress_comparison_path": peak_path,
        "peak_grid_stress_load_duration_samples_path": load_duration_path,
        "demand_distribution_uncertainty_comparison_path": distribution_path,
        "diversity_factor_comparison_path": diversity_path,
        "diversity_factor_by_household_count_path": diversity_count_path,
        "successful_leaf_count": int(len(leaf_metrics_df)),
        "peak_grid_stress_rows": int(len(peak_comparison_df)),
        "stress_threshold_grid_import_W": stress_threshold,
    }


def _compact_label(row: Mapping[str, Any]) -> str:
    window_labels = {
        "baseline_1981_2005": "Baseline",
        "near_future_2030_2049": "Near",
        "mid_century_2050_2070": "Mid",
        "long_term_2080_2100": "Long",
    }
    pathway_labels = {
        "historical": "Hist",
        "rcp_2_6": "RCP2.6",
        "rcp_4_5": "RCP4.5",
        "rcp_8_5": "RCP8.5",
    }
    technology_labels = {
        "tech_current_stock": "Current",
        "tech_frozen_stock": "Frozen",
        "tech_moderate_electrification": "Moderate",
        "tech_high_electrification_pv_ev": "High PV+EV",
    }
    design_labels = {
        "cold_design_year": "Cold",
        "typical_heating_year": "Typical",
    }
    window = window_labels.get(str(row.get("climate_window_id", "")), str(row.get("climate_window_id", "")))
    pathway = pathway_labels.get(str(row.get("climate_pathway_id", "")), str(row.get("climate_pathway_id", "")))
    tech = technology_labels.get(str(row.get("technology_case_id", "")), str(row.get("technology_case_id", "")))
    design = design_labels.get(str(row.get("design_year_id", "")), str(row.get("design_year_id", "")))
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


def generate_output34_figures(
    *,
    experiment_root: Path = OUTPUT34_EXPERIMENT_ROOT,
    figures_root: Path = OUTPUT34_FIGURES_ROOT,
    formats: Iterable[str] = ("png", "pdf"),
) -> dict[str, Any]:
    comparison_dir = Path(experiment_root) / "summaries" / "comparison_level"
    realization_dir = Path(experiment_root) / "summaries" / "realization_level"
    peak = pd.read_csv(comparison_dir / "peak_grid_stress_comparison.csv")
    leaf = pd.read_csv(realization_dir / "output34_leaf_peak_diversity_metrics.csv")
    ldc = pd.read_csv(comparison_dir / "peak_grid_stress_load_duration_samples.csv")
    diversity = pd.read_csv(comparison_dir / "diversity_factor_comparison.csv")
    diversity_count = pd.read_csv(comparison_dir / "diversity_factor_by_household_count.csv")

    paths: list[Path] = []
    metadata_rows: list[dict[str, Any]] = []

    cold_leaf = leaf[leaf["design_year_id"] == "cold_design_year"].copy()
    order = list(dict.fromkeys(cold_leaf["scenario_id"].tolist()))
    data = [cold_leaf.loc[cold_leaf["scenario_id"] == scenario, "peak_grid_import_W"].dropna().to_numpy() for scenario in order]
    labels = [_compact_label(cold_leaf[cold_leaf["scenario_id"] == scenario].iloc[0]) for scenario in order]
    fig, ax = plt.subplots(figsize=(13, 5))
    ax.boxplot(data, tick_labels=labels, showfliers=True)
    ax.set_ylabel("Peak grid import (W)")
    ax.set_title("Output 3: cold-design-year peak grid import")
    ax.tick_params(axis="x", labelrotation=0, labelsize=8)
    figure_paths = _plot_save(fig, Path(figures_root) / "output3_peak_grid_stress" / "output3_peak_grid_import_boxplot", formats)
    paths.extend(figure_paths)
    metadata_rows.append({"figure_id": "output3_peak_grid_import_boxplot", "source_rows": len(cold_leaf), "files": ";".join(map(str, figure_paths))})

    cold_ldc = ldc[(ldc["design_year_id"] == "cold_design_year") & (ldc["exceedance_pct"] <= 10.0)].copy()
    fig, ax = plt.subplots(figsize=(10, 5))
    for scenario_id, group in cold_ldc.groupby("scenario_id", sort=False):
        group = group.sort_values("exceedance_pct")
        label = _compact_label(group.iloc[0]).replace("\n", " / ")
        ax.plot(group["exceedance_pct"], group["grid_import_W_p50"], marker="o", linewidth=1.5, label=label)
    ax.set_xlabel("Load-duration exceedance (%)")
    ax.set_ylabel("Grid import (W)")
    ax.set_title("Output 3: load-duration upper tail")
    ax.legend(fontsize=7, ncol=2)
    figure_paths = _plot_save(fig, Path(figures_root) / "output3_peak_grid_stress" / "output3_load_duration_upper_tail", formats)
    paths.extend(figure_paths)
    metadata_rows.append({"figure_id": "output3_load_duration_upper_tail", "source_rows": len(cold_ldc), "files": ";".join(map(str, figure_paths))})

    cold_peak = peak[peak["design_year_id"] == "cold_design_year"].copy()
    cold_peak["label"] = [_compact_label(row) for _, row in cold_peak.iterrows()]
    fig, ax = plt.subplots(figsize=(13, 5))
    ax.bar(range(len(cold_peak)), cold_peak["hours_above_grid_stress_threshold_mean"])
    ax.set_xticks(range(len(cold_peak)), cold_peak["label"], fontsize=8)
    ax.set_ylabel("Hours above baseline cold P99")
    ax.set_title("Output 3: grid-stress threshold exceedance")
    figure_paths = _plot_save(fig, Path(figures_root) / "output3_peak_grid_stress" / "output3_top1pct_grid_stress_hours", formats)
    paths.extend(figure_paths)
    metadata_rows.append({"figure_id": "output3_top1pct_grid_stress_hours", "source_rows": len(cold_peak), "files": ";".join(map(str, figure_paths))})

    fig, ax = plt.subplots(figsize=(10, 5))
    for scenario_id, group in cold_ldc.groupby("scenario_id", sort=False):
        group = group.sort_values("exceedance_pct")
        label = _compact_label(group.iloc[0]).replace("\n", " / ")
        ax.plot(group["exceedance_pct"], group["grid_import_W_p50"], linewidth=1.5, label=label)
        ax.fill_between(
            group["exceedance_pct"].to_numpy(dtype=float),
            group["grid_import_W_p10"].to_numpy(dtype=float),
            group["grid_import_W_p90"].to_numpy(dtype=float),
            alpha=0.12,
        )
    ax.set_xlabel("Load-duration exceedance (%)")
    ax.set_ylabel("Grid import (W)")
    ax.set_title("Output 4: P10/P50/P90 load-duration band")
    ax.legend(fontsize=7, ncol=2)
    figure_paths = _plot_save(fig, Path(figures_root) / "output4_distribution_diversity" / "output4_load_duration_uncertainty_band", formats)
    paths.extend(figure_paths)
    metadata_rows.append({"figure_id": "output4_load_duration_uncertainty_band", "source_rows": len(cold_ldc), "files": ";".join(map(str, figure_paths))})

    cold_div = diversity[diversity["design_year_id"] == "cold_design_year"].copy()
    cold_div["label"] = [_compact_label(row) for _, row in cold_div.iterrows()]
    fig, ax = plt.subplots(figsize=(13, 5))
    x = np.arange(len(cold_div))
    y = cold_div["diversity_factor_grid_import_p50"].to_numpy(dtype=float)
    yerr = np.vstack([
        y - cold_div["diversity_factor_grid_import_p10"].to_numpy(dtype=float),
        cold_div["diversity_factor_grid_import_p90"].to_numpy(dtype=float) - y,
    ])
    ax.errorbar(x, y, yerr=yerr, fmt="o", capsize=4)
    ax.set_xticks(x, cold_div["label"], fontsize=8)
    ax.set_ylabel("Grid-import diversity factor")
    ax.set_title("Output 4: diversity factor by climate and technology scenario")
    figure_paths = _plot_save(fig, Path(figures_root) / "output4_distribution_diversity" / "output4_diversity_factor_by_scenario", formats)
    paths.extend(figure_paths)
    metadata_rows.append({"figure_id": "output4_diversity_factor_by_scenario", "source_rows": len(cold_div), "files": ";".join(map(str, figure_paths))})

    cold_count = diversity_count[diversity_count["design_year_id"] == "cold_design_year"].copy()
    fig, ax = plt.subplots(figsize=(10, 5))
    for scenario_id, group in cold_count.groupby("scenario_id", sort=False):
        group = group.sort_values("n_households")
        label = _compact_label(group.iloc[0]).replace("\n", " / ")
        ax.plot(group["n_households"], group["diversity_factor_p50_p50"], marker="o", linewidth=1.5, label=label)
    ax.set_xlabel("Aggregated households")
    ax.set_ylabel("Grid-import diversity factor")
    ax.set_title("Output 4: diversity factor versus aggregation size")
    ax.legend(fontsize=7, ncol=2)
    figure_paths = _plot_save(fig, Path(figures_root) / "output4_distribution_diversity" / "output4_diversity_factor_by_household_count", formats)
    paths.extend(figure_paths)
    metadata_rows.append({"figure_id": "output4_diversity_factor_by_household_count", "source_rows": len(cold_count), "files": ";".join(map(str, figure_paths))})

    metadata_dir = Path(figures_root) / "output34_metadata"
    metadata_dir.mkdir(parents=True, exist_ok=True)
    metadata_path = metadata_dir / "output34_figure_metadata.csv"
    pd.DataFrame(metadata_rows).to_csv(metadata_path, index=False)
    return {"figure_count": len(metadata_rows), "files": paths, "metadata_path": metadata_path}


def validate_output34_results(
    *,
    experiment_root: Path = OUTPUT34_EXPERIMENT_ROOT,
    leaf_index: Path = OUTPUT34_LEAF_INDEX,
    run_registry: Path | None = None,
    expected_leaf_count: int = 48,
    expected_peak_rows: int = 16,
    expected_design_year_ids: Iterable[str] = DESIGN_YEAR_IDS,
    expected_realization_ids: Iterable[str] = OUTPUT34_REALIZATION_IDS,
    expected_cohort_size: int | None = None,
    expected_target_resolution_seconds: int | None = None,
    require_success: bool = False,
) -> dict[str, Any]:
    index = pd.read_csv(leaf_index)
    comparison_dir = Path(experiment_root) / "summaries" / "comparison_level"
    peak = pd.read_csv(comparison_dir / "peak_grid_stress_comparison.csv")
    distribution = pd.read_csv(comparison_dir / "demand_distribution_uncertainty_comparison.csv")
    diversity = pd.read_csv(comparison_dir / "diversity_factor_comparison.csv")
    errors: list[str] = []
    expected_design_year_ids = tuple(expected_design_year_ids)
    expected_realization_ids = tuple(expected_realization_ids)
    if len(index) != int(expected_leaf_count):
        errors.append(f"Expected {expected_leaf_count} output34 leaf-index rows, found {len(index)}.")
    if set(index["design_year_id"]) != set(expected_design_year_ids):
        errors.append(f"Output34 leaf index must contain design-year ids: {', '.join(expected_design_year_ids)}.")
    if set(index["realization_id"]) != set(expected_realization_ids):
        errors.append(f"Output34 leaf index must contain realization ids: {', '.join(expected_realization_ids)}.")
    if len(peak) != int(expected_peak_rows):
        errors.append(f"Expected {expected_peak_rows} peak comparison rows, found {len(peak)}.")
    output_columns = set(peak.columns) | set(distribution.columns) | set(diversity.columns)
    if any(column in ACTIVE_COOLING_COLUMN_NAMES for column in output_columns):
        errors.append("Active cooling final-energy columns must not be present in Output 3-4 tables.")
    if expected_cohort_size is not None:
        cohort_sizes = set()
        for _, row in index.iterrows():
            run_config = _load_yaml(_resolve_repo_path(row["run_config_path"]))
            cohort_sizes.add(int(dict(run_config.get("stochastic", {})).get("cohort_size", 0) or 0))
        if cohort_sizes != {int(expected_cohort_size)}:
            errors.append(f"Expected cohort_size {expected_cohort_size}, found {sorted(cohort_sizes)}.")
    if expected_target_resolution_seconds is not None:
        target_resolutions = set()
        runner_modes = set()
        for _, row in index.iterrows():
            run_config = _load_yaml(_resolve_repo_path(row["run_config_path"]))
            model_options = dict(run_config.get("model_options", {}))
            target_resolutions.add(int(model_options.get("target_resolution_seconds", 0) or 0))
            runner_modes.add(str(model_options.get("runner_mode", "")))
        if target_resolutions != {int(expected_target_resolution_seconds)}:
            errors.append(
                f"Expected target_resolution_seconds {expected_target_resolution_seconds}, found {sorted(target_resolutions)}."
            )
        if runner_modes != {"stochastic_cohort"}:
            errors.append(f"Expected runner_mode stochastic_cohort, found {sorted(runner_modes)}.")
    if require_success:
        registry_path = Path(run_registry) if run_registry is not None else Path(experiment_root) / "manifests" / "run_registry.csv"
        registry_rows = read_registry(registry_path)
        statuses = {leaf_id: latest_actual_status(registry_rows, leaf_id) for leaf_id in index["scenario_leaf_id"]}
        failed = {leaf_id: status for leaf_id, status in statuses.items() if status != "success"}
        if failed:
            errors.append(f"Expected all selected leaves to have latest status success; non-success leaves: {failed}.")
    near_years = index.loc[index["climate_window_id"] == "near_future_2030_2049", "design_year"].astype(int)
    if not near_years.empty and bool((near_years == 2050).any()):
        errors.append("Near-future design years must exclude 2050.")
    if errors:
        raise Output34Error("; ".join(errors))
    return {
        "leaf_index_rows": int(len(index)),
        "peak_rows": int(len(peak)),
        "distribution_rows": int(len(distribution)),
        "diversity_rows": int(len(diversity)),
        "near_future_includes_2050": bool((near_years == 2050).any()) if not near_years.empty else False,
        "mid_century_window_includes_2050": True,
        "required_success": bool(require_success),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    gen = subparsers.add_parser("generate-configs", help="Generate Output 3-4 cohort pilot configs.")
    gen.add_argument("--source-leaf-index", type=Path, default=SOURCE_LEAF_INDEX)
    gen.add_argument("--experiment-root", type=Path, default=OUTPUT34_EXPERIMENT_ROOT)
    gen.add_argument("--scenario-id", action="append", dest="scenario_ids")
    gen.add_argument("--design-year-id", action="append", dest="design_year_ids")
    gen.add_argument("--realization-id", action="append", dest="realization_ids")
    gen.add_argument("--cohort-size", type=int, default=30)
    gen.add_argument("--target-resolution-seconds", type=int, default=3600)

    summary = subparsers.add_parser("summarize", help="Build Output 3-4 comparison tables.")
    summary.add_argument("--experiment-root", type=Path, default=OUTPUT34_EXPERIMENT_ROOT)
    summary.add_argument("--leaf-index", type=Path, default=OUTPUT34_LEAF_INDEX)
    summary.add_argument("--run-registry", type=Path, default=OUTPUT34_RUN_REGISTRY)

    figures = subparsers.add_parser("figures", help="Generate Output 3-4 figures.")
    figures.add_argument("--experiment-root", type=Path, default=OUTPUT34_EXPERIMENT_ROOT)
    figures.add_argument("--figures-root", type=Path, default=OUTPUT34_FIGURES_ROOT)
    figures.add_argument("--formats", nargs="+", default=["png", "pdf"])

    validate = subparsers.add_parser("validate", help="Validate Output 3-4 tables.")
    validate.add_argument("--experiment-root", type=Path, default=OUTPUT34_EXPERIMENT_ROOT)
    validate.add_argument("--leaf-index", type=Path, default=OUTPUT34_LEAF_INDEX)
    validate.add_argument("--run-registry", type=Path, default=None)
    validate.add_argument("--expected-leaf-count", type=int, default=48)
    validate.add_argument("--expected-peak-rows", type=int, default=16)
    validate.add_argument("--expected-design-year-id", action="append", dest="expected_design_year_ids")
    validate.add_argument("--expected-realization-id", action="append", dest="expected_realization_ids")
    validate.add_argument("--expected-cohort-size", type=int)
    validate.add_argument("--expected-target-resolution-seconds", type=int)
    validate.add_argument("--require-success", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "generate-configs":
        result = generate_output34_pilot_configs(
            source_leaf_index=args.source_leaf_index,
            experiment_root=args.experiment_root,
            scenario_ids=args.scenario_ids or OUTPUT34_SCENARIO_IDS,
            realization_ids=args.realization_ids or OUTPUT34_REALIZATION_IDS,
            design_year_ids=args.design_year_ids or DESIGN_YEAR_IDS,
            cohort_size=args.cohort_size,
            target_resolution_seconds=args.target_resolution_seconds,
        )
    elif args.command == "summarize":
        result = build_output34_tables(
            experiment_root=args.experiment_root,
            leaf_index=args.leaf_index,
            run_registry=args.run_registry,
        )
    elif args.command == "figures":
        result = generate_output34_figures(
            experiment_root=args.experiment_root,
            figures_root=args.figures_root,
            formats=args.formats,
        )
    elif args.command == "validate":
        result = validate_output34_results(
            experiment_root=args.experiment_root,
            leaf_index=args.leaf_index,
            run_registry=args.run_registry,
            expected_leaf_count=args.expected_leaf_count,
            expected_peak_rows=args.expected_peak_rows,
            expected_design_year_ids=args.expected_design_year_ids or DESIGN_YEAR_IDS,
            expected_realization_ids=args.expected_realization_ids or OUTPUT34_REALIZATION_IDS,
            expected_cohort_size=args.expected_cohort_size,
            expected_target_resolution_seconds=args.expected_target_resolution_seconds,
            require_success=args.require_success,
        )
    else:
        raise AssertionError(args.command)
    for key, value in result.items():
        print(f"{key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
