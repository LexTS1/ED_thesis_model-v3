"""Standardize scenario-tree raw outputs into comparable summary tables."""

from __future__ import annotations

import argparse
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

import pandas as pd
import yaml

from model_v3.scenarios.climate_metrics import compute_climate_metrics
from model_v3.scenarios.monthly_metrics import (
    MONTHLY_COLUMNS,
    aggregate_monthly_metrics,
    compute_monthly_metrics,
    write_monthly_metrics,
)
from model_v3.scenarios.output_reader import (
    MissingRequiredOutputError,
    compute_standardized_output_metrics,
    read_leaf_outputs,
)
from model_v3.scenarios.registry import (
    latest_actual_run_for_leaf,
    latest_actual_status,
    read_registry,
)
from model_v3.scenarios.selection import ScenarioLeafRecord, load_leaf_records
from model_v3.scenarios.summary_contract import (
    BASELINE_LEAF_PREFIX,
    BASELINE_SCENARIO_ID,
    DIAGNOSTIC_COLUMNS,
    REQUIRED_METADATA_COLUMNS,
    REQUIRED_METRIC_COLUMNS,
    SUMMARY_COLUMNS,
    write_schema,
)
from model_v3.utils.energy import infer_step_durations_seconds, integrate_power_series_kwh


REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_EXPERIMENT_ROOT = REPO_ROOT / "experiments" / "scenario_tree"
DEFAULT_CONFIG_ROOT = REPO_ROOT / "config" / "scenario_tree"
DEFAULT_LEAF_INDEX = DEFAULT_EXPERIMENT_ROOT / "manifests" / "scenario_leaf_index.csv"
DEFAULT_RUN_REGISTRY = DEFAULT_EXPERIMENT_ROOT / "manifests" / "run_registry.csv"

STATISTICS = {
    "count": lambda series: float(series.count()),
    "mean": lambda series: float(series.mean()),
    "median": lambda series: float(series.median()),
    "std": lambda series: float(series.std()),
    "min": lambda series: float(series.min()),
    "max": lambda series: float(series.max()),
    "p05": lambda series: float(series.quantile(0.05)),
    "p10": lambda series: float(series.quantile(0.10)),
    "p90": lambda series: float(series.quantile(0.90)),
    "p95": lambda series: float(series.quantile(0.95)),
}
CLIMATE_YEAR_METRIC_COLUMNS = [
    "mean_T_out_C",
    "winter_mean_T_out_C",
    "summer_mean_T_out_C",
    "HDD_15",
    "HDD_18",
    "CDD_22",
    "mean_solar_W_m2",
]
CLIMATE_YEAR_METADATA_COLUMNS = [
    "scenario_leaf_id",
    "scenario_id",
    "climate_window_id",
    "climate_pathway_id",
    "technology_case_id",
    "realization_id",
    "analysis_start",
    "analysis_end",
    "source_file_window",
    "climate_forcing_file",
    "climate_temperature_column",
    "climate_solar_column",
    "year",
]
CLIMATE_YEAR_COLUMNS = CLIMATE_YEAR_METADATA_COLUMNS + CLIMATE_YEAR_METRIC_COLUMNS
ANNUAL_SPACE_HEATING_COMPARISON_COLUMNS = [
    "scenario_id",
    "climate_window_id",
    "climate_pathway_id",
    "technology_case_id",
    "baseline_scenario_id",
    "n_successful_realizations",
    "n_missing_realizations",
    "n_annual_samples",
    "realization_coverage_fraction",
    "annual_useful_heating_kWh_mean",
    "annual_useful_heating_kWh_median",
    "annual_useful_heating_kWh_p50",
    "annual_useful_heating_kWh_p10",
    "annual_useful_heating_kWh_p90",
    "baseline_annual_useful_heating_kWh_mean",
    "delta_annual_useful_heating_kWh_mean",
    "delta_annual_useful_heating_kWh_pct",
]
ANNUAL_CLIMATE_DEGREE_DAY_COMPARISON_COLUMNS = [
    "scenario_id",
    "climate_window_id",
    "climate_pathway_id",
    "technology_case_id",
    "baseline_scenario_id",
    "n_climate_year_samples",
    "HDD_18_mean",
    "HDD_18_p10",
    "HDD_18_p50",
    "HDD_18_p90",
    "CDD_22_mean",
    "CDD_22_p10",
    "CDD_22_p50",
    "CDD_22_p90",
    "baseline_HDD_18_mean",
    "baseline_CDD_22_mean",
    "delta_HDD_18_abs",
    "delta_HDD_18_pct",
    "delta_CDD_22_abs",
    "delta_CDD_22_pct",
]
COOLING_EXPOSURE_COMPARISON_COLUMNS = [
    "scenario_id",
    "climate_window_id",
    "climate_pathway_id",
    "technology_case_id",
    "baseline_scenario_id",
    "n_climate_year_samples",
    "n_overheating_samples",
    "overheating_threshold_C",
    "CDD_22_mean",
    "CDD_22_p10",
    "CDD_22_p50",
    "CDD_22_p90",
    "overheating_hours_mean",
    "overheating_hours_p10",
    "overheating_hours_p50",
    "overheating_hours_p90",
    "excess_heat_kWh_mean",
    "excess_heat_kWh_p10",
    "excess_heat_kWh_p50",
    "excess_heat_kWh_p90",
    "indoor_temperature_exceedance_degree_hours_mean",
    "indoor_temperature_exceedance_degree_hours_p10",
    "indoor_temperature_exceedance_degree_hours_p50",
    "indoor_temperature_exceedance_degree_hours_p90",
    "max_indoor_temperature_C_mean",
    "max_indoor_temperature_C_p90",
    "baseline_CDD_22_mean",
    "baseline_overheating_hours_mean",
    "baseline_excess_heat_kWh_mean",
    "baseline_indoor_temperature_exceedance_degree_hours_mean",
    "delta_CDD_22_abs",
    "delta_CDD_22_pct",
    "delta_overheating_hours_abs",
    "delta_overheating_hours_pct",
    "delta_excess_heat_kWh_abs",
    "delta_excess_heat_kWh_pct",
    "delta_indoor_temperature_exceedance_degree_hours_abs",
    "delta_indoor_temperature_exceedance_degree_hours_pct",
    "active_cooling_final_energy_kWh_included",
    "interpretation_note",
]
DEMAND_SHIFT_METRICS = [
    "space_heating_useful_kWh",
    "electricity_gross_kWh",
    "grid_import_kWh",
    "gas_kWh",
    "total_final_energy_kWh",
    "CDD_22",
    "excess_heat_kWh",
    "overheating_hours",
    "indoor_temperature_exceedance_degree_hours",
    "max_indoor_temperature_C",
]
MONTHLY_DEMAND_SHIFT_COMPARISON_COLUMNS = [
    "scenario_id",
    "climate_window_id",
    "climate_pathway_id",
    "technology_case_id",
    "baseline_scenario_id",
    "month",
    "n_month_samples",
    "active_cooling_final_energy_kWh_included",
    "interpretation_note",
]
for _metric in DEMAND_SHIFT_METRICS:
    MONTHLY_DEMAND_SHIFT_COMPARISON_COLUMNS.extend(
        [
            f"monthly_{_metric}_mean",
            f"monthly_{_metric}_p10",
            f"monthly_{_metric}_p50",
            f"monthly_{_metric}_p90",
            f"baseline_monthly_{_metric}_mean",
            f"delta_monthly_{_metric}_abs",
            f"delta_monthly_{_metric}_pct",
        ]
    )
del _metric
SEASONAL_DEMAND_SHIFT_COMPARISON_COLUMNS = [
    "scenario_id",
    "climate_window_id",
    "climate_pathway_id",
    "technology_case_id",
    "baseline_scenario_id",
    "season",
    "n_season_samples",
    "active_cooling_final_energy_kWh_included",
    "interpretation_note",
]
for _metric in DEMAND_SHIFT_METRICS:
    SEASONAL_DEMAND_SHIFT_COMPARISON_COLUMNS.extend(
        [
            f"seasonal_{_metric}_mean",
            f"seasonal_{_metric}_p10",
            f"seasonal_{_metric}_p50",
            f"seasonal_{_metric}_p90",
            f"baseline_seasonal_{_metric}_mean",
            f"delta_seasonal_{_metric}_abs",
            f"delta_seasonal_{_metric}_pct",
        ]
    )
SEASONAL_DEMAND_SHIFT_COMPARISON_COLUMNS.extend(
    [
        "seasonal_heating_share_pct_mean",
        "seasonal_heating_share_pct_p10",
        "seasonal_heating_share_pct_p50",
        "seasonal_heating_share_pct_p90",
        "baseline_seasonal_heating_share_pct_mean",
        "delta_seasonal_heating_share_pct_abs",
    ]
)
del _metric


class SummaryError(RuntimeError):
    """Raised when standardized summaries cannot be generated."""


def _resolve_cli_path(path_text: str | Path) -> Path:
    path = Path(path_text)
    if path.is_absolute():
        return path
    cwd_candidate = (Path.cwd() / path).resolve()
    if cwd_candidate.exists():
        return cwd_candidate
    return REPO_ROOT / path


def _resolve_repo_path(path_text: str | Path) -> Path:
    path = Path(path_text)
    if path.is_absolute():
        return path
    return REPO_ROOT / path


def _load_yaml(path: Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict):
        raise SummaryError(f"YAML file must contain a mapping: {path}")
    return data


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    return str(value)


def _join_values(values: Iterable[Any]) -> str:
    return ";".join(_safe_text(value) for value in values if _safe_text(value))


def _format_mapping(mapping: Mapping[str, str]) -> str:
    return ";".join(f"{key}={value}" for key, value in sorted(mapping.items()))


def _select_records(
    records: list[ScenarioLeafRecord],
    *,
    scenario_leaf_id: str | None,
    scenario_id: str | None,
    limit: int | None,
) -> list[ScenarioLeafRecord]:
    selected = list(records)
    if scenario_leaf_id:
        selected = [record for record in selected if record.scenario_leaf_id == scenario_leaf_id]
        if not selected:
            raise SummaryError(f"Scenario leaf ID not found in leaf index: {scenario_leaf_id}")
    if scenario_id:
        selected = [record for record in selected if record.scenario_id == scenario_id]
    selected = sorted(selected, key=lambda record: record.scenario_leaf_id)
    if limit is not None:
        selected = selected[: max(int(limit), 0)]
    return selected


def _path_from_row(row: Mapping[str, str], key: str, fallback: Path) -> Path:
    value = row.get(key)
    return _resolve_repo_path(value) if value else fallback


def _climate_file(run_config: Mapping[str, Any], inputs_manifest: Mapping[str, Any], registry_row: Mapping[str, str]) -> Path:
    if registry_row.get("climate_forcing_file"):
        return _resolve_repo_path(str(registry_row["climate_forcing_file"]))
    manifest_climate = dict(inputs_manifest.get("climate_forcing", {}))
    if manifest_climate.get("file"):
        return _resolve_repo_path(str(manifest_climate["file"]))
    return _resolve_repo_path(str(dict(run_config.get("climate", {})).get("forcing_file", "")))


def _technology_inputs_file(
    run_config: Mapping[str, Any],
    inputs_manifest: Mapping[str, Any],
    registry_row: Mapping[str, str],
) -> Path:
    if registry_row.get("belgian_technology_inputs"):
        return _resolve_repo_path(str(registry_row["belgian_technology_inputs"]))
    manifest_technology = dict(inputs_manifest.get("technology", {}))
    if manifest_technology.get("belgian_technology_inputs"):
        return _resolve_repo_path(str(manifest_technology["belgian_technology_inputs"]))
    return _resolve_repo_path(str(dict(run_config.get("technology", {})).get("belgian_technology_inputs", "")))


def _build_leaf_summary_payload(
    record: ScenarioLeafRecord,
    *,
    registry_row: Mapping[str, str],
    strict: bool = True,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Build one standardized summary row and its per-year climate rows."""

    row = record.row
    run_dir = _path_from_row(row, "run_dir", DEFAULT_EXPERIMENT_ROOT / "runs" / record.scenario_leaf_id)
    run_config_path = _path_from_row(row, "run_config_path", run_dir / "run_config.yaml")
    inputs_manifest_path = _path_from_row(row, "inputs_manifest_path", run_dir / "inputs_manifest.yaml")
    outputs_dir = _path_from_row(row, "outputs_dir", run_dir / "outputs")
    if not run_config_path.exists():
        raise SummaryError(f"Missing run_config.yaml for {record.scenario_leaf_id}: {run_config_path}")
    if not inputs_manifest_path.exists():
        raise SummaryError(f"Missing inputs_manifest.yaml for {record.scenario_leaf_id}: {inputs_manifest_path}")
    run_config = _load_yaml(run_config_path)
    inputs_manifest = _load_yaml(inputs_manifest_path)
    raw_outputs = read_leaf_outputs(outputs_dir)
    output_result = compute_standardized_output_metrics(raw_outputs, run_config, strict=strict)

    climate_cfg = dict(run_config.get("climate", {}))
    climate_path = _climate_file(run_config, inputs_manifest, registry_row)
    analysis_start = str(climate_cfg.get("analysis_start") or row.get("canonical_start") or "")
    analysis_end = str(climate_cfg.get("analysis_end") or row.get("canonical_end") or "")
    climate_result = compute_climate_metrics(
        climate_path,
        analysis_start=analysis_start,
        analysis_end=analysis_end,
    )

    stochastic_cfg = dict(run_config.get("stochastic", {}))
    technology_path = _technology_inputs_file(run_config, inputs_manifest, registry_row)
    missing_metrics = sorted(set(output_result.missing_metrics))
    summary = {
        "scenario_leaf_id": record.scenario_leaf_id,
        "scenario_id": record.scenario_id,
        "climate_window_id": record.climate_window_id,
        "climate_pathway_id": record.climate_pathway_id,
        "technology_case_id": record.technology_case_id,
        "realization_id": record.realization_id,
        "seed_index": stochastic_cfg.get("seed_index", registry_row.get("random_seed", "")),
        "seed_value": stochastic_cfg.get("seed_value", registry_row.get("random_seed", "")),
        "cohort_size": stochastic_cfg.get("cohort_size", registry_row.get("cohort_size", "")),
        "analysis_start": analysis_start,
        "analysis_end": analysis_end,
        "source_file_window": climate_cfg.get("source_file_window") or row.get("source_file_window", ""),
        "run_status": registry_row.get("status", ""),
        "run_attempt_id": registry_row.get("run_attempt_id", ""),
        "run_timestamp_utc": registry_row.get("timestamp_start_utc", ""),
        "config_hash_sha256": registry_row.get("config_hash_sha256", ""),
        "climate_forcing_file": str(climate_path),
        "technology_inputs_file": str(technology_path),
        "raw_outputs_dir": str(outputs_dir),
        **output_result.metrics,
        **climate_result.metrics,
        "missing_metric_count": len(missing_metrics),
        "missing_metrics": _join_values(missing_metrics),
        "pv_metric_policy": output_result.policies.get("pv_metric_policy", ""),
        "ev_metric_policy": output_result.policies.get("ev_metric_policy", ""),
        "gas_metric_policy": output_result.policies.get("gas_metric_policy", ""),
        "heating_metric_policy": output_result.policies.get("heating_metric_policy", ""),
        "climate_temperature_column": climate_result.temperature_column,
        "climate_solar_column": climate_result.solar_column,
        "climate_included_years": _join_values(climate_result.included_years),
        "climate_includes_2050": bool(climate_result.includes_2050),
        "raw_output_files_used": _join_values(output_result.raw_output_files_used),
        "raw_output_columns_used": _format_mapping(output_result.raw_output_columns_used),
    }
    summary_row = {column: summary.get(column, "") for column in SUMMARY_COLUMNS}
    climate_year_rows = []
    for annual_metric in climate_result.annual_metrics:
        year_row = {
            "scenario_leaf_id": record.scenario_leaf_id,
            "scenario_id": record.scenario_id,
            "climate_window_id": record.climate_window_id,
            "climate_pathway_id": record.climate_pathway_id,
            "technology_case_id": record.technology_case_id,
            "realization_id": record.realization_id,
            "analysis_start": analysis_start,
            "analysis_end": analysis_end,
            "source_file_window": climate_cfg.get("source_file_window") or row.get("source_file_window", ""),
            "climate_forcing_file": str(climate_path),
            "climate_temperature_column": climate_result.temperature_column,
            "climate_solar_column": climate_result.solar_column,
            **annual_metric,
        }
        climate_year_rows.append({column: year_row.get(column, "") for column in CLIMATE_YEAR_COLUMNS})
    return summary_row, climate_year_rows


def build_leaf_summary_row(
    record: ScenarioLeafRecord,
    *,
    registry_row: Mapping[str, str],
    strict: bool = True,
) -> dict[str, Any]:
    """Build one standardized summary row for a successful scenario leaf."""

    row, _ = _build_leaf_summary_payload(record, registry_row=registry_row, strict=strict)
    return row


def write_per_leaf_summary(row: Mapping[str, Any]) -> Path:
    outputs_dir = Path(str(row["raw_outputs_dir"]))
    outputs_dir.mkdir(parents=True, exist_ok=True)
    path = outputs_dir / "standardized_leaf_summary.csv"
    pd.DataFrame([{column: row.get(column, "") for column in SUMMARY_COLUMNS}]).to_csv(path, index=False)
    return path


def compute_and_write_per_leaf_monthly_metrics(
    record: "ScenarioLeafRecord",
    raw_outputs: Mapping[str, Any],
    outputs_dir: Path,
) -> list[dict[str, Any]]:
    """Compute monthly metrics from raw outputs and write monthly_metrics.csv."""

    frame = None
    for key in ("timeseries", "annual_profile"):
        value = raw_outputs.get(key)
        if isinstance(value, pd.DataFrame):
            frame = value
            break

    if frame is None or frame.empty:
        return []

    try:
        monthly_result = compute_monthly_metrics(
            frame,
            leaf_id=record.scenario_leaf_id,
            scenario_id=record.scenario_id,
            climate_window_id=record.climate_window_id,
            climate_pathway_id=record.climate_pathway_id,
            technology_case_id=record.technology_case_id,
            realization_id=record.realization_id,
            overheating_threshold_C=_comfort_overheating_threshold(outputs_dir),
        )
        write_monthly_metrics(monthly_result.rows, outputs_dir)
        return monthly_result.rows
    except Exception:
        return []


def write_per_leaf_climate_year_summary(row: Mapping[str, Any], climate_year_rows: list[Mapping[str, Any]]) -> Path:
    outputs_dir = Path(str(row["raw_outputs_dir"]))
    outputs_dir.mkdir(parents=True, exist_ok=True)
    path = outputs_dir / "climate_year_metrics.csv"
    pd.DataFrame(climate_year_rows, columns=CLIMATE_YEAR_COLUMNS).to_csv(path, index=False)
    return path


def _latest_status_by_leaf(records: list[ScenarioLeafRecord], registry_rows: list[Mapping[str, str]]) -> dict[str, str]:
    return {
        record.scenario_leaf_id: latest_actual_status(registry_rows, record.scenario_leaf_id)
        for record in records
    }


def aggregate_scenario_metrics(
    metrics_df: pd.DataFrame,
    *,
    leaf_index_df: pd.DataFrame | None = None,
    status_by_leaf: Mapping[str, str] | None = None,
) -> pd.DataFrame:
    """Aggregate realization-level metrics by scenario dimensions."""

    group_cols = ["scenario_id", "climate_window_id", "climate_pathway_id", "technology_case_id"]
    output_rows: list[dict[str, Any]] = []
    if metrics_df.empty:
        return pd.DataFrame(columns=group_cols)

    for group_values, group in metrics_df.groupby(group_cols, dropna=False):
        row = dict(zip(group_cols, group_values))
        scenario_id = str(row["scenario_id"])
        if leaf_index_df is not None and not leaf_index_df.empty:
            scenario_leaf_index = leaf_index_df[leaf_index_df["scenario_id"] == scenario_id]
            total_realizations = int(len(scenario_leaf_index))
            if status_by_leaf:
                statuses = [status_by_leaf.get(str(leaf_id), "not_run") for leaf_id in scenario_leaf_index["scenario_leaf_id"]]
                failed_realizations = int(sum(status == "failed" for status in statuses))
            else:
                failed_realizations = 0
        else:
            total_realizations = int(len(group))
            failed_realizations = 0

        successful_realizations = int(len(group))
        missing_realizations = max(total_realizations - successful_realizations - failed_realizations, 0)
        row.update(
            {
                "n_successful_realizations": successful_realizations,
                "n_failed_realizations": failed_realizations,
                "n_missing_realizations": missing_realizations,
                "realization_coverage_fraction": (
                    successful_realizations / total_realizations if total_realizations else float("nan")
                ),
            }
        )
        for metric in REQUIRED_METRIC_COLUMNS:
            series = pd.to_numeric(group[metric], errors="coerce")
            for stat_name, stat_func in STATISTICS.items():
                row[f"{metric}_{stat_name}"] = stat_func(series) if series.count() else float("nan")
        output_rows.append(row)
    return pd.DataFrame(output_rows).sort_values(group_cols).reset_index(drop=True)


def aggregate_climate_year_metrics(climate_year_df: pd.DataFrame) -> pd.DataFrame:
    """Aggregate per-year climate metrics by scenario and calendar year."""

    group_cols = ["scenario_id", "climate_window_id", "climate_pathway_id", "technology_case_id", "year"]
    output_rows: list[dict[str, Any]] = []
    if climate_year_df.empty:
        return pd.DataFrame(columns=group_cols)
    for group_values, group in climate_year_df.groupby(group_cols, dropna=False):
        row = dict(zip(group_cols, group_values))
        row["n_successful_realizations"] = int(len(group))
        for metric in CLIMATE_YEAR_METRIC_COLUMNS:
            series = pd.to_numeric(group[metric], errors="coerce")
            row[metric] = float(series.mean()) if series.count() else float("nan")
            row[f"{metric}_median"] = float(series.median()) if series.count() else float("nan")
        output_rows.append(row)
    return pd.DataFrame(output_rows).sort_values(group_cols).reset_index(drop=True)


def build_baseline_comparison(metrics_df: pd.DataFrame) -> pd.DataFrame:
    """Build future-vs-baseline leaf comparison rows matched by realization ID."""

    base_cols = [
        "future_scenario_leaf_id",
        "baseline_scenario_leaf_id",
        "future_scenario_id",
        "baseline_scenario_id",
        "realization_id",
        "climate_window_id",
        "climate_pathway_id",
        "technology_case_id",
        "baseline_available",
        "comparison_valid",
        "zero_baseline_delta_pct_metrics",
    ]
    metric_cols: list[str] = []
    for metric in REQUIRED_METRIC_COLUMNS:
        metric_cols.extend(
            [
                f"{metric}_future",
                f"{metric}_baseline",
                f"{metric}_delta_abs",
                f"{metric}_delta_pct",
            ]
        )
    if metrics_df.empty:
        return pd.DataFrame(columns=base_cols + metric_cols)

    by_leaf = metrics_df.set_index("scenario_leaf_id", drop=False)
    output_rows: list[dict[str, Any]] = []
    future_rows = metrics_df[metrics_df["scenario_id"] != BASELINE_SCENARIO_ID]
    for _, future in future_rows.sort_values("scenario_leaf_id").iterrows():
        realization_id = str(future["realization_id"])
        baseline_leaf_id = f"{BASELINE_LEAF_PREFIX}{realization_id}"
        baseline_available = baseline_leaf_id in by_leaf.index
        row: dict[str, Any] = {
            "future_scenario_leaf_id": future["scenario_leaf_id"],
            "baseline_scenario_leaf_id": baseline_leaf_id,
            "future_scenario_id": future["scenario_id"],
            "baseline_scenario_id": BASELINE_SCENARIO_ID,
            "realization_id": realization_id,
            "climate_window_id": future["climate_window_id"],
            "climate_pathway_id": future["climate_pathway_id"],
            "technology_case_id": future["technology_case_id"],
            "baseline_available": bool(baseline_available),
            "comparison_valid": bool(baseline_available),
            "zero_baseline_delta_pct_metrics": "",
        }
        zero_baseline_metrics: list[str] = []
        baseline = by_leaf.loc[baseline_leaf_id] if baseline_available else None
        for metric in REQUIRED_METRIC_COLUMNS:
            future_value = pd.to_numeric(pd.Series([future.get(metric)]), errors="coerce").iloc[0]
            baseline_value = (
                pd.to_numeric(pd.Series([baseline.get(metric)]), errors="coerce").iloc[0]
                if baseline is not None
                else float("nan")
            )
            row[f"{metric}_future"] = future_value
            row[f"{metric}_baseline"] = baseline_value
            if baseline_available and pd.notna(future_value) and pd.notna(baseline_value):
                delta_abs = float(future_value) - float(baseline_value)
                row[f"{metric}_delta_abs"] = delta_abs
                if abs(float(baseline_value)) > 1e-12:
                    row[f"{metric}_delta_pct"] = 100.0 * delta_abs / float(baseline_value)
                else:
                    row[f"{metric}_delta_pct"] = float("nan")
                    zero_baseline_metrics.append(metric)
            else:
                row[f"{metric}_delta_abs"] = float("nan")
                row[f"{metric}_delta_pct"] = float("nan")
        row["zero_baseline_delta_pct_metrics"] = _join_values(zero_baseline_metrics)
        output_rows.append(row)
    return pd.DataFrame(output_rows, columns=base_cols + metric_cols)


def _annual_space_heating_samples(metrics_df: pd.DataFrame) -> pd.DataFrame:
    """Return one annual space-heating sample per leaf and calendar year."""

    rows: list[dict[str, Any]] = []
    if metrics_df.empty:
        return pd.DataFrame()

    for _, leaf in metrics_df.iterrows():
        profile_path = Path(str(leaf["raw_outputs_dir"])) / "annual_profile.csv"
        if not profile_path.exists():
            raise SummaryError(f"Missing annual_profile.csv for annual heating comparison: {profile_path}")
        frame = pd.read_csv(profile_path, usecols=["timestamp", "Q_heating_supplied_W"])
        frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
        for year, group in frame.groupby(frame["timestamp"].dt.year):
            rows.append(
                {
                    "scenario_id": leaf["scenario_id"],
                    "climate_window_id": leaf["climate_window_id"],
                    "climate_pathway_id": leaf["climate_pathway_id"],
                    "technology_case_id": leaf["technology_case_id"],
                    "realization_id": leaf["realization_id"],
                    "year": int(year),
                    "annual_useful_heating_kWh": integrate_power_series_kwh(
                        group["Q_heating_supplied_W"],
                        timestamps=group["timestamp"],
                    ),
                }
            )
    return pd.DataFrame(rows)


def build_annual_space_heating_demand_comparison(metrics_df: pd.DataFrame) -> pd.DataFrame:
    """Build the Phase 1 scenario-level annual space-heating comparison table."""

    samples = _annual_space_heating_samples(metrics_df)
    if samples.empty:
        return pd.DataFrame(columns=ANNUAL_SPACE_HEATING_COMPARISON_COLUMNS)

    baseline_rows = samples[samples["scenario_id"] == BASELINE_SCENARIO_ID]
    baseline_mean = float("nan")
    if not baseline_rows.empty:
        baseline_mean = float(pd.to_numeric(baseline_rows["annual_useful_heating_kWh"], errors="coerce").mean())

    rows: list[dict[str, Any]] = []
    group_cols = ["scenario_id", "climate_window_id", "climate_pathway_id", "technology_case_id"]
    for group_values, scenario in samples.groupby(group_cols, dropna=False):
        values = pd.to_numeric(scenario["annual_useful_heating_kWh"], errors="coerce").dropna()
        scenario_mean = float(values.mean()) if len(values) else float("nan")
        delta = scenario_mean - baseline_mean if pd.notna(scenario_mean) and pd.notna(baseline_mean) else float("nan")
        if pd.notna(delta) and abs(baseline_mean) > 1e-12:
            delta_pct = 100.0 * delta / baseline_mean
        else:
            delta_pct = float("nan")
        scenario_id, climate_window_id, climate_pathway_id, technology_case_id = group_values

        row = {
            "scenario_id": scenario_id,
            "climate_window_id": climate_window_id,
            "climate_pathway_id": climate_pathway_id,
            "technology_case_id": technology_case_id,
            "baseline_scenario_id": BASELINE_SCENARIO_ID,
            "n_successful_realizations": int(scenario["realization_id"].nunique()),
            "n_missing_realizations": 0,
            "n_annual_samples": int(len(values)),
            "realization_coverage_fraction": 1.0,
            "annual_useful_heating_kWh_mean": scenario_mean,
            "annual_useful_heating_kWh_median": float(values.median()) if len(values) else float("nan"),
            "annual_useful_heating_kWh_p50": float(values.quantile(0.50)) if len(values) else float("nan"),
            "annual_useful_heating_kWh_p10": float(values.quantile(0.10)) if len(values) else float("nan"),
            "annual_useful_heating_kWh_p90": float(values.quantile(0.90)) if len(values) else float("nan"),
            "baseline_annual_useful_heating_kWh_mean": baseline_mean,
            "delta_annual_useful_heating_kWh_mean": delta,
            "delta_annual_useful_heating_kWh_pct": delta_pct,
        }
        rows.append(row)
    return (
        pd.DataFrame(rows, columns=ANNUAL_SPACE_HEATING_COMPARISON_COLUMNS)
        .sort_values(["climate_window_id", "climate_pathway_id", "technology_case_id", "scenario_id"])
        .reset_index(drop=True)
    )


def _annual_climate_degree_day_samples(climate_year_df: pd.DataFrame) -> pd.DataFrame:
    """Return unique annual HDD/CDD samples per scenario and calendar year."""

    required = [
        "scenario_id",
        "climate_window_id",
        "climate_pathway_id",
        "technology_case_id",
        "year",
        "HDD_18",
        "CDD_22",
    ]
    if climate_year_df.empty:
        return pd.DataFrame(columns=required)
    missing = [column for column in required if column not in climate_year_df.columns]
    if missing:
        raise SummaryError(f"Climate degree-day comparison missing required column(s): {', '.join(missing)}")

    samples = climate_year_df[required].copy()
    samples["year"] = pd.to_numeric(samples["year"], errors="coerce")
    for metric in ["HDD_18", "CDD_22"]:
        samples[metric] = pd.to_numeric(samples[metric], errors="coerce")
    samples = samples.dropna(subset=["year", "HDD_18", "CDD_22"])

    group_cols = ["scenario_id", "climate_window_id", "climate_pathway_id", "technology_case_id", "year"]
    samples = (
        samples.groupby(group_cols, dropna=False, as_index=False)
        .agg(HDD_18=("HDD_18", "mean"), CDD_22=("CDD_22", "mean"))
        .sort_values(group_cols)
        .reset_index(drop=True)
    )
    samples["year"] = samples["year"].astype(int)
    return samples


def build_annual_climate_degree_day_comparison(climate_year_df: pd.DataFrame) -> pd.DataFrame:
    """Build the Phase 1 annual HDD/CDD comparison table.

    Climate rows are de-duplicated to one sample per scenario and calendar year
    before percentiles are computed, because stochastic seeds reuse the same
    climate forcing.
    """

    samples = _annual_climate_degree_day_samples(climate_year_df)
    if samples.empty:
        return pd.DataFrame(columns=ANNUAL_CLIMATE_DEGREE_DAY_COMPARISON_COLUMNS)

    baseline_rows = samples[samples["scenario_id"] == BASELINE_SCENARIO_ID]
    baseline_means = {
        metric: float(pd.to_numeric(baseline_rows[metric], errors="coerce").mean()) if not baseline_rows.empty else float("nan")
        for metric in ["HDD_18", "CDD_22"]
    }

    rows: list[dict[str, Any]] = []
    group_cols = ["scenario_id", "climate_window_id", "climate_pathway_id", "technology_case_id"]
    for group_values, scenario in samples.groupby(group_cols, dropna=False):
        scenario_id, climate_window_id, climate_pathway_id, technology_case_id = group_values
        row: dict[str, Any] = {
            "scenario_id": scenario_id,
            "climate_window_id": climate_window_id,
            "climate_pathway_id": climate_pathway_id,
            "technology_case_id": technology_case_id,
            "baseline_scenario_id": BASELINE_SCENARIO_ID,
            "n_climate_year_samples": int(scenario["year"].nunique()),
        }

        for metric in ["HDD_18", "CDD_22"]:
            values = pd.to_numeric(scenario[metric], errors="coerce").dropna()
            mean_value = float(values.mean()) if len(values) else float("nan")
            baseline_mean = baseline_means[metric]
            row[f"{metric}_mean"] = mean_value
            row[f"{metric}_p10"] = float(values.quantile(0.10)) if len(values) else float("nan")
            row[f"{metric}_p50"] = float(values.quantile(0.50)) if len(values) else float("nan")
            row[f"{metric}_p90"] = float(values.quantile(0.90)) if len(values) else float("nan")
            row[f"baseline_{metric}_mean"] = baseline_mean

            if scenario_id == BASELINE_SCENARIO_ID and pd.notna(mean_value):
                delta = 0.0
                delta_pct = 0.0
            elif pd.notna(mean_value) and pd.notna(baseline_mean):
                delta = mean_value - baseline_mean
                delta_pct = 100.0 * delta / baseline_mean if abs(baseline_mean) > 1e-12 else float("nan")
            else:
                delta = float("nan")
                delta_pct = float("nan")
            row[f"delta_{metric}_abs"] = delta
            row[f"delta_{metric}_pct"] = delta_pct
        rows.append(row)

    return (
        pd.DataFrame(rows, columns=ANNUAL_CLIMATE_DEGREE_DAY_COMPARISON_COLUMNS)
        .sort_values(["climate_window_id", "climate_pathway_id", "technology_case_id", "scenario_id"])
        .reset_index(drop=True)
    )


def _comfort_overheating_threshold(raw_outputs_dir: Path) -> float:
    run_config_path = raw_outputs_dir.parent / "run_config.yaml"
    if not run_config_path.exists():
        return 26.0
    try:
        run_config = _load_yaml(run_config_path)
    except Exception:
        return 26.0
    comfort_cfg = dict(run_config.get("comfort", {}))
    return float(comfort_cfg.get("T_max_C", 26.0))


def _cooling_exposure_samples(metrics_df: pd.DataFrame) -> pd.DataFrame:
    """Return annual overheating and excess-heat samples from leaf profiles."""

    rows: list[dict[str, Any]] = []
    if metrics_df.empty:
        return pd.DataFrame()

    required_profile_columns = ["timestamp", "T_indoor_next_C", "Q_excess_heat_W"]
    for _, leaf in metrics_df.iterrows():
        raw_outputs_dir = Path(str(leaf["raw_outputs_dir"]))
        profile_path = raw_outputs_dir / "annual_profile.csv"
        if not profile_path.exists():
            raise SummaryError(f"Missing annual_profile.csv for cooling exposure comparison: {profile_path}")
        frame = pd.read_csv(profile_path, usecols=required_profile_columns)
        frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
        frame["T_indoor_next_C"] = pd.to_numeric(frame["T_indoor_next_C"], errors="coerce")
        frame["Q_excess_heat_W"] = pd.to_numeric(frame["Q_excess_heat_W"], errors="coerce").fillna(0.0).clip(lower=0.0)
        threshold_c = _comfort_overheating_threshold(raw_outputs_dir)

        for year, group in frame.groupby(frame["timestamp"].dt.year):
            timestamps = group["timestamp"]
            durations_hours = pd.Series(infer_step_durations_seconds(timestamps), index=group.index, dtype=float) / 3600.0
            exceedance_c = (group["T_indoor_next_C"] - threshold_c).clip(lower=0.0)
            rows.append(
                {
                    "scenario_id": leaf["scenario_id"],
                    "climate_window_id": leaf["climate_window_id"],
                    "climate_pathway_id": leaf["climate_pathway_id"],
                    "technology_case_id": leaf["technology_case_id"],
                    "realization_id": leaf["realization_id"],
                    "year": int(year),
                    "overheating_threshold_C": threshold_c,
                    "overheating_hours": float(durations_hours[exceedance_c > 0.0].sum()),
                    "excess_heat_kWh": integrate_power_series_kwh(group["Q_excess_heat_W"], timestamps=timestamps),
                    "indoor_temperature_exceedance_degree_hours": float((exceedance_c * durations_hours).sum()),
                    "max_indoor_temperature_C": float(group["T_indoor_next_C"].max()),
                }
            )
    return pd.DataFrame(rows)


def _stat_columns(row: dict[str, Any], values: pd.Series, prefix: str) -> None:
    clean = pd.to_numeric(values, errors="coerce").dropna()
    row[f"{prefix}_mean"] = float(clean.mean()) if len(clean) else float("nan")
    row[f"{prefix}_p10"] = float(clean.quantile(0.10)) if len(clean) else float("nan")
    row[f"{prefix}_p50"] = float(clean.quantile(0.50)) if len(clean) else float("nan")
    row[f"{prefix}_p90"] = float(clean.quantile(0.90)) if len(clean) else float("nan")


def _delta_pair(value: float, baseline: float) -> tuple[float, float]:
    if pd.notna(value) and pd.notna(baseline):
        delta = float(value) - float(baseline)
        delta_pct = 100.0 * delta / float(baseline) if abs(float(baseline)) > 1e-12 else float("nan")
        return delta, delta_pct
    return float("nan"), float("nan")


def build_cooling_exposure_overheating_risk_comparison(metrics_df: pd.DataFrame, climate_year_df: pd.DataFrame) -> pd.DataFrame:
    """Build cooling exposure and overheating risk outputs without active cooling demand."""

    climate_samples = _annual_climate_degree_day_samples(climate_year_df)
    overheating_samples = _cooling_exposure_samples(metrics_df)
    if climate_samples.empty and overheating_samples.empty:
        return pd.DataFrame(columns=COOLING_EXPOSURE_COMPARISON_COLUMNS)

    group_cols = ["scenario_id", "climate_window_id", "climate_pathway_id", "technology_case_id"]
    baseline_climate = climate_samples[climate_samples["scenario_id"] == BASELINE_SCENARIO_ID]
    baseline_overheating = overheating_samples[overheating_samples["scenario_id"] == BASELINE_SCENARIO_ID]
    baseline_values = {
        "CDD_22": float(pd.to_numeric(baseline_climate.get("CDD_22", pd.Series(dtype=float)), errors="coerce").mean())
        if not baseline_climate.empty
        else float("nan"),
        "overheating_hours": float(pd.to_numeric(baseline_overheating.get("overheating_hours", pd.Series(dtype=float)), errors="coerce").mean())
        if not baseline_overheating.empty
        else float("nan"),
        "excess_heat_kWh": float(pd.to_numeric(baseline_overheating.get("excess_heat_kWh", pd.Series(dtype=float)), errors="coerce").mean())
        if not baseline_overheating.empty
        else float("nan"),
        "indoor_temperature_exceedance_degree_hours": float(
            pd.to_numeric(baseline_overheating.get("indoor_temperature_exceedance_degree_hours", pd.Series(dtype=float)), errors="coerce").mean()
        )
        if not baseline_overheating.empty
        else float("nan"),
    }

    scenario_keys = pd.concat(
        [
            climate_samples[group_cols] if not climate_samples.empty else pd.DataFrame(columns=group_cols),
            overheating_samples[group_cols] if not overheating_samples.empty else pd.DataFrame(columns=group_cols),
        ],
        ignore_index=True,
    ).drop_duplicates()

    rows: list[dict[str, Any]] = []
    note = (
        "Active cooling final energy is not included; CDD_22, overheating hours, excess heat, and indoor-temperature "
        "exceedance are cooling-pressure indicators rather than cooling electricity consumption."
    )
    for _, key in scenario_keys.sort_values(group_cols).iterrows():
        selector = (climate_samples[group_cols] == key[group_cols]).all(axis=1) if not climate_samples.empty else pd.Series(dtype=bool)
        climate_part = climate_samples[selector] if not climate_samples.empty else pd.DataFrame()
        selector = (overheating_samples[group_cols] == key[group_cols]).all(axis=1) if not overheating_samples.empty else pd.Series(dtype=bool)
        overheating_part = overheating_samples[selector] if not overheating_samples.empty else pd.DataFrame()

        row: dict[str, Any] = {
            **{column: key[column] for column in group_cols},
            "baseline_scenario_id": BASELINE_SCENARIO_ID,
            "n_climate_year_samples": int(climate_part["year"].nunique()) if not climate_part.empty else 0,
            "n_overheating_samples": int(len(overheating_part)),
            "overheating_threshold_C": float(pd.to_numeric(overheating_part.get("overheating_threshold_C", pd.Series([26.0])), errors="coerce").median()),
            "active_cooling_final_energy_kWh_included": False,
            "interpretation_note": note,
        }
        _stat_columns(row, climate_part.get("CDD_22", pd.Series(dtype=float)), "CDD_22")
        for metric in [
            "overheating_hours",
            "excess_heat_kWh",
            "indoor_temperature_exceedance_degree_hours",
            "max_indoor_temperature_C",
        ]:
            _stat_columns(row, overheating_part.get(metric, pd.Series(dtype=float)), metric)
        row["baseline_CDD_22_mean"] = baseline_values["CDD_22"]
        row["baseline_overheating_hours_mean"] = baseline_values["overheating_hours"]
        row["baseline_excess_heat_kWh_mean"] = baseline_values["excess_heat_kWh"]
        row["baseline_indoor_temperature_exceedance_degree_hours_mean"] = baseline_values["indoor_temperature_exceedance_degree_hours"]

        for metric, baseline_metric in [
            ("CDD_22", "CDD_22"),
            ("overheating_hours", "overheating_hours"),
            ("excess_heat_kWh", "excess_heat_kWh"),
            ("indoor_temperature_exceedance_degree_hours", "indoor_temperature_exceedance_degree_hours"),
        ]:
            if row["scenario_id"] == BASELINE_SCENARIO_ID and pd.notna(row.get(f"{metric}_mean")):
                delta, delta_pct = 0.0, 0.0
            else:
                delta, delta_pct = _delta_pair(row.get(f"{metric}_mean", float("nan")), baseline_values[baseline_metric])
            row[f"delta_{metric}_abs"] = delta
            row[f"delta_{metric}_pct"] = delta_pct
        rows.append(row)

    return (
        pd.DataFrame(rows, columns=COOLING_EXPOSURE_COMPARISON_COLUMNS)
        .sort_values(["climate_window_id", "climate_pathway_id", "technology_case_id", "scenario_id"])
        .reset_index(drop=True)
    )


def _season_id(month: int) -> str:
    if int(month) in {12, 1, 2}:
        return "winter"
    if int(month) in {3, 4, 5}:
        return "spring"
    if int(month) in {6, 7, 8}:
        return "summer"
    return "autumn"


def _demand_shift_monthly_samples(monthly_df: pd.DataFrame) -> pd.DataFrame:
    required = [
        "scenario_leaf_id",
        "scenario_id",
        "climate_window_id",
        "climate_pathway_id",
        "technology_case_id",
        "realization_id",
        "year",
        "month",
        "monthly_space_heating_useful_kWh",
        "monthly_electricity_gross_kWh",
        "monthly_grid_import_kWh",
        "monthly_gas_kWh",
        "monthly_CDD_22",
        "monthly_excess_heat_kWh",
        "monthly_overheating_hours",
        "monthly_indoor_temperature_exceedance_degree_hours",
        "monthly_max_indoor_temperature_C",
    ]
    if monthly_df.empty:
        return pd.DataFrame(columns=required + ["monthly_total_final_energy_kWh"])
    missing = [column for column in required if column not in monthly_df.columns]
    if missing:
        raise SummaryError(f"Monthly demand-shift comparison missing required column(s): {', '.join(missing)}")
    samples = monthly_df[required].copy()
    numeric_columns = [column for column in samples.columns if column.startswith("monthly_")] + ["year", "month"]
    for column in numeric_columns:
        samples[column] = pd.to_numeric(samples[column], errors="coerce")
    samples["monthly_total_final_energy_kWh"] = samples["monthly_electricity_gross_kWh"] + samples["monthly_gas_kWh"]
    return samples.dropna(subset=["year", "month"])


def _monthly_metric_column(metric: str) -> str:
    return f"monthly_{metric}"


def _seasonal_metric_column(metric: str) -> str:
    return f"seasonal_{metric}"


def _demand_shift_delta(value: float, baseline: float, metric: str) -> tuple[float, float]:
    delta, delta_pct = _delta_pair(value, baseline)
    if metric == "max_indoor_temperature_C":
        delta_pct = float("nan")
    return delta, delta_pct


def build_monthly_demand_shift_comparison(monthly_df: pd.DataFrame) -> pd.DataFrame:
    """Build Output 2 monthly demand timing and cooling-pressure comparison rows."""

    samples = _demand_shift_monthly_samples(monthly_df)
    if samples.empty:
        return pd.DataFrame(columns=MONTHLY_DEMAND_SHIFT_COMPARISON_COLUMNS)

    group_cols = ["scenario_id", "climate_window_id", "climate_pathway_id", "technology_case_id", "month"]
    baseline = samples[samples["scenario_id"] == BASELINE_SCENARIO_ID]
    baseline_means: dict[tuple[int, str], float] = {}
    for month, group in baseline.groupby("month"):
        for metric in DEMAND_SHIFT_METRICS:
            baseline_means[(int(month), metric)] = float(pd.to_numeric(group[_monthly_metric_column(metric)], errors="coerce").mean())

    note = (
        "Output 2 reports demand timing and cooling-pressure indicators only; active cooling final energy is not included."
    )
    rows: list[dict[str, Any]] = []
    for group_values, group in samples.groupby(group_cols, dropna=False):
        scenario_id, climate_window_id, climate_pathway_id, technology_case_id, month = group_values
        row: dict[str, Any] = {
            "scenario_id": scenario_id,
            "climate_window_id": climate_window_id,
            "climate_pathway_id": climate_pathway_id,
            "technology_case_id": technology_case_id,
            "baseline_scenario_id": BASELINE_SCENARIO_ID,
            "month": int(month),
            "n_month_samples": int(len(group)),
            "active_cooling_final_energy_kWh_included": False,
            "interpretation_note": note,
        }
        for metric in DEMAND_SHIFT_METRICS:
            prefix = _monthly_metric_column(metric)
            _stat_columns(row, group[prefix], prefix)
            baseline_mean = baseline_means.get((int(month), metric), float("nan"))
            row[f"baseline_{prefix}_mean"] = baseline_mean
            if scenario_id == BASELINE_SCENARIO_ID and pd.notna(row.get(f"{prefix}_mean")):
                delta, delta_pct = 0.0, 0.0 if metric != "max_indoor_temperature_C" else float("nan")
            else:
                delta, delta_pct = _demand_shift_delta(row.get(f"{prefix}_mean", float("nan")), baseline_mean, metric)
            row[f"delta_{prefix}_abs"] = delta
            row[f"delta_{prefix}_pct"] = delta_pct
        rows.append(row)
    return (
        pd.DataFrame(rows, columns=MONTHLY_DEMAND_SHIFT_COMPARISON_COLUMNS)
        .sort_values(["climate_window_id", "climate_pathway_id", "technology_case_id", "scenario_id", "month"])
        .reset_index(drop=True)
    )


def _seasonal_demand_shift_samples(monthly_df: pd.DataFrame) -> pd.DataFrame:
    monthly = _demand_shift_monthly_samples(monthly_df)
    if monthly.empty:
        return pd.DataFrame()
    id_cols = ["scenario_leaf_id", "scenario_id", "climate_window_id", "climate_pathway_id", "technology_case_id", "realization_id", "year"]
    monthly["season"] = monthly["month"].map(_season_id)

    def aggregate_season(frame: pd.DataFrame, season: str) -> dict[str, Any]:
        row = {column: frame.iloc[0][column] for column in id_cols}
        row["season"] = season
        row["n_months"] = int(frame["month"].nunique())
        for metric in DEMAND_SHIFT_METRICS:
            monthly_col = _monthly_metric_column(metric)
            seasonal_col = _seasonal_metric_column(metric)
            values = pd.to_numeric(frame[monthly_col], errors="coerce")
            if metric == "max_indoor_temperature_C":
                row[seasonal_col] = float(values.max()) if values.count() else float("nan")
            else:
                row[seasonal_col] = float(values.sum()) if values.count() else float("nan")
        return row

    rows: list[dict[str, Any]] = []
    for group_values, group in monthly.groupby(id_cols, dropna=False):
        for season, season_group in group.groupby("season", dropna=False):
            rows.append(aggregate_season(season_group, str(season)))
        shoulder = group[group["season"].isin(["spring", "autumn"])]
        if not shoulder.empty:
            rows.append(aggregate_season(shoulder, "shoulder"))
        rows.append(aggregate_season(group, "annual"))

    seasonal = pd.DataFrame(rows)
    annual_heating = seasonal[seasonal["season"] == "annual"][
        [*id_cols, "seasonal_space_heating_useful_kWh"]
    ].rename(columns={"seasonal_space_heating_useful_kWh": "annual_space_heating_useful_kWh"})
    seasonal = seasonal.merge(annual_heating, on=id_cols, how="left")
    denominator = pd.to_numeric(seasonal["annual_space_heating_useful_kWh"], errors="coerce")
    numerator = pd.to_numeric(seasonal["seasonal_space_heating_useful_kWh"], errors="coerce")
    seasonal["seasonal_heating_share_pct"] = 100.0 * numerator / denominator.where(denominator.abs() > 1e-12)
    return seasonal


def build_seasonal_demand_shift_comparison(monthly_df: pd.DataFrame) -> pd.DataFrame:
    """Build Output 2 seasonal demand timing and cooling-pressure comparison rows."""

    samples = _seasonal_demand_shift_samples(monthly_df)
    if samples.empty:
        return pd.DataFrame(columns=SEASONAL_DEMAND_SHIFT_COMPARISON_COLUMNS)

    group_cols = ["scenario_id", "climate_window_id", "climate_pathway_id", "technology_case_id", "season"]
    baseline = samples[samples["scenario_id"] == BASELINE_SCENARIO_ID]
    baseline_means: dict[tuple[str, str], float] = {}
    for season, group in baseline.groupby("season"):
        for metric in DEMAND_SHIFT_METRICS:
            baseline_means[(str(season), metric)] = float(pd.to_numeric(group[_seasonal_metric_column(metric)], errors="coerce").mean())
        baseline_means[(str(season), "heating_share_pct")] = float(pd.to_numeric(group["seasonal_heating_share_pct"], errors="coerce").mean())

    note = (
        "Output 2 reports seasonal demand timing and cooling-pressure indicators only; active cooling final energy is not included."
    )
    rows: list[dict[str, Any]] = []
    for group_values, group in samples.groupby(group_cols, dropna=False):
        scenario_id, climate_window_id, climate_pathway_id, technology_case_id, season = group_values
        row: dict[str, Any] = {
            "scenario_id": scenario_id,
            "climate_window_id": climate_window_id,
            "climate_pathway_id": climate_pathway_id,
            "technology_case_id": technology_case_id,
            "baseline_scenario_id": BASELINE_SCENARIO_ID,
            "season": str(season),
            "n_season_samples": int(len(group)),
            "active_cooling_final_energy_kWh_included": False,
            "interpretation_note": note,
        }
        for metric in DEMAND_SHIFT_METRICS:
            prefix = _seasonal_metric_column(metric)
            _stat_columns(row, group[prefix], prefix)
            baseline_mean = baseline_means.get((str(season), metric), float("nan"))
            row[f"baseline_{prefix}_mean"] = baseline_mean
            if scenario_id == BASELINE_SCENARIO_ID and pd.notna(row.get(f"{prefix}_mean")):
                delta, delta_pct = 0.0, 0.0 if metric != "max_indoor_temperature_C" else float("nan")
            else:
                delta, delta_pct = _demand_shift_delta(row.get(f"{prefix}_mean", float("nan")), baseline_mean, metric)
            row[f"delta_{prefix}_abs"] = delta
            row[f"delta_{prefix}_pct"] = delta_pct

        _stat_columns(row, group["seasonal_heating_share_pct"], "seasonal_heating_share_pct")
        baseline_share = baseline_means.get((str(season), "heating_share_pct"), float("nan"))
        row["baseline_seasonal_heating_share_pct_mean"] = baseline_share
        row["delta_seasonal_heating_share_pct_abs"] = (
            row["seasonal_heating_share_pct_mean"] - baseline_share
            if pd.notna(row["seasonal_heating_share_pct_mean"]) and pd.notna(baseline_share)
            else float("nan")
        )
        rows.append(row)

    season_order = {"winter": 0, "spring": 1, "summer": 2, "autumn": 3, "shoulder": 4, "annual": 5}
    out = pd.DataFrame(rows, columns=SEASONAL_DEMAND_SHIFT_COMPARISON_COLUMNS)
    out["_season_order"] = out["season"].map(lambda value: season_order.get(str(value), len(season_order)))
    out = out.sort_values(["climate_window_id", "climate_pathway_id", "technology_case_id", "scenario_id", "_season_order"])
    return out.drop(columns=["_season_order"]).reset_index(drop=True)


COHORT_STRESS_GROUP_COLS = [
    "scenario_id",
    "climate_window_id",
    "climate_pathway_id",
    "technology_case_id",
    "year",
]
COHORT_STRESS_COLUMNS = COHORT_STRESS_GROUP_COLS + [
    "n_realizations",
    "sum_household_peak_grid_import_W",
    "aggregate_peak_grid_import_W",
    "diversity_factor_grid_import",
    "aggregate_p95_grid_import_W",
    "aggregate_p99_grid_import_W",
    "aggregate_grid_import_load_factor",
]


def build_cohort_grid_stress_metrics(metrics_df: pd.DataFrame) -> pd.DataFrame:
    """Compute diversity factor and aggregate grid-stress metrics per scenario per year.

    Each scenario leaf is one household (one stochastic seed). This function groups
    seeds belonging to the same scenario, reconstructs the aggregate load by summing
    per-leaf monthly grid-import totals as a proxy, and derives the diversity factor.

    A per-year approach is used rather than per-day because daily time series across
    multiple seeds are not aligned in memory here — only the monthly summary rows are
    available. The monthly import total is used to reconstruct a coarse aggregate for
    diversity-factor estimation. For a precise per-hour diversity factor, use the
    raw annual_profile.csv files directly.
    """

    if metrics_df.empty:
        return pd.DataFrame(columns=COHORT_STRESS_COLUMNS)

    # Only compute for leaves that have per-leaf monthly files
    scenario_group_cols = ["scenario_id", "climate_window_id", "climate_pathway_id", "technology_case_id"]
    output_rows: list[dict[str, Any]] = []

    leaf_profile_paths: dict[str, Path] = {}
    for _, row in metrics_df.iterrows():
        leaf_id = str(row.get("scenario_leaf_id", ""))
        raw_dir = str(row.get("raw_outputs_dir", ""))
        if leaf_id and raw_dir:
            leaf_profile_paths[leaf_id] = Path(raw_dir) / "annual_profile.csv"

    for group_vals, group in metrics_df.groupby(scenario_group_cols, dropna=False):
        scenario_id, climate_window_id, climate_pathway_id, technology_case_id = group_vals

        # Collect daily grid-import series per leaf for this scenario
        leaf_series: list[tuple[str, pd.Series, pd.Series]] = []  # (leaf_id, timestamps, import_W)
        for _, leaf_row in group.iterrows():
            leaf_id = str(leaf_row.get("scenario_leaf_id", ""))
            profile_path = leaf_profile_paths.get(leaf_id)
            if profile_path is None or not profile_path.exists():
                continue
            try:
                profile = pd.read_csv(profile_path, usecols=["timestamp", "P_el_grid_import_W"])
                timestamps = pd.to_datetime(profile["timestamp"], utc=True)
                import_w = pd.to_numeric(profile["P_el_grid_import_W"], errors="coerce").fillna(0.0)
                leaf_series.append((leaf_id, timestamps, import_w))
            except Exception:
                continue

        if len(leaf_series) < 2:
            continue

        # Align on common timestamps using a shared index, compute per-year metrics
        first_ts = leaf_series[0][1]
        common_years = sorted(set(first_ts.dt.year.unique()))

        for year in common_years:
            year_masks = [ts.dt.year == year for _, ts, _ in leaf_series]
            year_lens = [int(mask.sum()) for mask in year_masks]
            if min(year_lens) == 0:
                continue

            # Per-leaf peak and aggregate series for this year
            household_peaks: list[float] = []
            agg_import: pd.Series | None = None

            for (leaf_id, ts, imp_w), mask in zip(leaf_series, year_masks):
                year_imp = imp_w[mask].reset_index(drop=True)
                household_peaks.append(float(year_imp.max()))
                if agg_import is None:
                    agg_import = year_imp.copy()
                else:
                    n = min(len(agg_import), len(year_imp))
                    agg_import = agg_import.iloc[:n] + year_imp.iloc[:n]

            if agg_import is None or agg_import.empty:
                continue

            agg_peak = float(agg_import.max())
            sum_peaks = float(sum(household_peaks))
            diversity_factor = sum_peaks / agg_peak if agg_peak > 0 else float("nan")
            p95 = float(agg_import.quantile(0.95))
            p99 = float(agg_import.quantile(0.99))
            agg_mean = float(agg_import.mean())
            load_factor = agg_mean / agg_peak if agg_peak > 0 else float("nan")

            output_rows.append({
                "scenario_id": scenario_id,
                "climate_window_id": climate_window_id,
                "climate_pathway_id": climate_pathway_id,
                "technology_case_id": technology_case_id,
                "year": int(year),
                "n_realizations": len(leaf_series),
                "sum_household_peak_grid_import_W": round(sum_peaks, 2),
                "aggregate_peak_grid_import_W": round(agg_peak, 2),
                "diversity_factor_grid_import": round(diversity_factor, 6) if math.isfinite(diversity_factor) else float("nan"),
                "aggregate_p95_grid_import_W": round(p95, 2),
                "aggregate_p99_grid_import_W": round(p99, 2),
                "aggregate_grid_import_load_factor": round(load_factor, 6) if math.isfinite(load_factor) else float("nan"),
            })

    if not output_rows:
        return pd.DataFrame(columns=COHORT_STRESS_COLUMNS)

    return (
        pd.DataFrame(output_rows, columns=COHORT_STRESS_COLUMNS)
        .sort_values(COHORT_STRESS_GROUP_COLS)
        .reset_index(drop=True)
    )


def _climate_window_2050_policy(config_root: Path) -> tuple[bool, bool]:
    windows_path = Path(config_root) / "climate_windows.yaml"
    if not windows_path.exists():
        return False, False
    data = _load_yaml(windows_path)
    windows = dict(data.get("climate_windows", {}))

    def includes_2050(window_id: str) -> bool:
        window = dict(windows.get(window_id, {}))
        start = pd.Timestamp(window.get("canonical_start")).date()
        end = pd.Timestamp(window.get("canonical_end")).date()
        return start <= pd.Timestamp("2050-01-01").date() <= end

    return includes_2050("near_future_2030_2049"), includes_2050("mid_century_2050_2070")


def _write_report(
    experiment_root: Path,
    config_root: Path,
    *,
    successful_runs: int,
    per_leaf_summaries: int,
    climate_year_rows: int,
    aggregate_rows: int,
    comparison_df: pd.DataFrame,
    metrics_df: pd.DataFrame,
    warnings: list[str],
    assumptions: list[str],
) -> dict[str, Any]:
    manifests_dir = experiment_root / "manifests"
    manifests_dir.mkdir(parents=True, exist_ok=True)
    near_includes_2050, mid_includes_2050 = _climate_window_2050_policy(config_root)
    raw_output_columns_used = sorted(
        {
            item
            for text in metrics_df.get("raw_output_columns_used", pd.Series(dtype=str)).dropna().astype(str)
            for item in text.split(";")
            if item
        }
    )
    climate_columns_used = sorted(
        set(metrics_df.get("climate_temperature_column", pd.Series(dtype=str)).dropna().astype(str))
        | set(metrics_df.get("climate_solar_column", pd.Series(dtype=str)).dropna().astype(str))
    )
    missing_metrics = sorted(
        {
            item
            for text in metrics_df.get("missing_metrics", pd.Series(dtype=str)).dropna().astype(str)
            for item in text.split(";")
            if item
        }
    )
    valid_comparisons = int(comparison_df["comparison_valid"].fillna(False).sum()) if "comparison_valid" in comparison_df else 0
    missing_comparisons = int((~comparison_df["comparison_valid"].fillna(False)).sum()) if "comparison_valid" in comparison_df else 0
    payload = {
        "generation_timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "successful_runs_found": int(successful_runs),
        "per_leaf_summaries_generated": int(per_leaf_summaries),
        "per_year_climate_metric_rows": int(climate_year_rows),
        "missing_per_leaf_summaries": max(int(successful_runs) - int(per_leaf_summaries), 0),
        "scenario_level_aggregate_rows": int(aggregate_rows),
        "baseline_comparison_rows": int(len(comparison_df)),
        "required_metrics": REQUIRED_METRIC_COLUMNS,
        "missing_metrics": missing_metrics,
        "raw_output_columns_used": raw_output_columns_used,
        "climate_columns_used": climate_columns_used,
        "future_leaves_with_valid_baseline_comparison": valid_comparisons,
        "future_leaves_missing_baseline_comparison": missing_comparisons,
        "near_future_includes_2050": bool(near_includes_2050),
        "mid_century_includes_2050": bool(mid_includes_2050),
        "simulations_run": 0,
        "warnings": warnings,
        "assumptions": assumptions,
    }
    yaml_path = manifests_dir / "summary_validation_report.yaml"
    with yaml_path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(payload, handle, sort_keys=False)
    md_lines = [
        "# Summary validation report",
        "",
        f"- Generation timestamp UTC: {payload['generation_timestamp_utc']}",
        f"- Successful runs found: {payload['successful_runs_found']}",
        f"- Per-leaf summaries generated: {payload['per_leaf_summaries_generated']}",
        f"- Per-year climate metric rows: {payload['per_year_climate_metric_rows']}",
        f"- Missing per-leaf summaries: {payload['missing_per_leaf_summaries']}",
        f"- Scenario-level aggregate rows: {payload['scenario_level_aggregate_rows']}",
        f"- Baseline comparison rows: {payload['baseline_comparison_rows']}",
        f"- Future leaves with valid baseline comparison: {valid_comparisons}",
        f"- Future leaves missing baseline comparison: {missing_comparisons}",
        f"- Near-future includes 2050: {'yes' if near_includes_2050 else 'no'}",
        f"- Mid-century includes 2050: {'yes' if mid_includes_2050 else 'no'}",
        f"- Simulations run: {payload['simulations_run']}",
        "",
        "## Required metrics",
        "",
        *[f"- `{metric}`" for metric in REQUIRED_METRIC_COLUMNS],
        "",
        "## Missing metrics",
        "",
        *(f"- `{metric}`" for metric in missing_metrics),
    ]
    if not missing_metrics:
        md_lines.append("- None")
    md_lines.extend(
        [
            "",
            "## Raw output columns used",
            "",
            *(f"- `{item}`" for item in raw_output_columns_used),
            "",
            "## Climate columns used",
            "",
            *(f"- `{item}`" for item in climate_columns_used),
            "",
            "## Warnings",
            "",
        ]
    )
    md_lines.extend(f"- {warning}" for warning in warnings)
    if not warnings:
        md_lines.append("- None")
    md_lines.extend(["", "## Assumptions", ""])
    md_lines.extend(f"- {assumption}" for assumption in assumptions)
    if not assumptions:
        md_lines.append("- None")
    (manifests_dir / "summary_validation_report.md").write_text("\n".join(md_lines) + "\n", encoding="utf-8")
    return payload


def generate_summaries(
    *,
    experiment_root: Path,
    config_root: Path,
    leaf_index: Path,
    run_registry: Path,
    scenario_leaf_id: str | None = None,
    scenario_id: str | None = None,
    only_successful: bool = True,
    include_failed: bool = False,
    strict: bool = True,
    limit: int | None = None,
    write_reports: bool = False,
) -> dict[str, Any]:
    """Generate per-leaf and aggregate standardized summary outputs."""

    records = load_leaf_records(leaf_index)
    registry_rows = read_registry(run_registry)
    selected = _select_records(records, scenario_leaf_id=scenario_leaf_id, scenario_id=scenario_id, limit=limit)
    status_by_leaf = _latest_status_by_leaf(records, registry_rows)

    rows: list[dict[str, Any]] = []
    climate_year_rows: list[dict[str, Any]] = []
    all_monthly_rows: list[dict[str, Any]] = []
    warnings: list[str] = []
    assumptions = [
        "Energy metrics are standardized from the raw annual model output year written by the Phase 4 runner.",
        "Climate sensitivity metrics are written both as canonical-window summaries and as per-calendar-year rows.",
        "Baseline comparison rows omit baseline leaves and match future leaves to baseline leaves by realization_id.",
        "Annual space-heating comparison P10/P50/P90 bands are computed across selected climate-window years and scenario realizations, not stochastic households.",
        "Annual climate degree-day comparison P10/P50/P90 bands are computed across unique climate-window years; repeated stochastic seeds are de-duplicated because they reuse the same climate forcing.",
        "Cooling exposure and overheating risk metrics do not include active cooling final energy; they quantify climate and comfort pressure only.",
        "Output 2 monthly and seasonal demand-shift tables report heating and final-energy timing plus cooling-pressure indicators, not active cooling electricity.",
    ]
    for record in selected:
        status = status_by_leaf.get(record.scenario_leaf_id, "not_run")
        if only_successful and status != "success":
            continue
        if status == "failed" and not include_failed:
            continue
        registry_row = latest_actual_run_for_leaf(registry_rows, record.scenario_leaf_id)
        if registry_row is None:
            continue
        try:
            run_dir = _path_from_row(dict(record.row), "run_dir", DEFAULT_EXPERIMENT_ROOT / "runs" / record.scenario_leaf_id)
            outputs_dir_path = _path_from_row(dict(record.row), "outputs_dir", run_dir / "outputs")
            raw_outputs = read_leaf_outputs(outputs_dir_path)
            row, annual_climate_rows = _build_leaf_summary_payload(
                record,
                registry_row=registry_row,
                strict=strict,
            )
        except MissingRequiredOutputError:
            raise
        except Exception as exc:
            raise SummaryError(f"Failed to summarize {record.scenario_leaf_id}: {exc}") from exc
        outputs_dir = Path(str(row["raw_outputs_dir"]))
        write_per_leaf_summary(row)
        write_per_leaf_climate_year_summary(row, annual_climate_rows)
        monthly_rows = compute_and_write_per_leaf_monthly_metrics(record, raw_outputs, outputs_dir)
        rows.append(row)
        climate_year_rows.extend(annual_climate_rows)
        all_monthly_rows.extend(monthly_rows)

    summaries_root = experiment_root / "summaries"
    realization_dir = summaries_root / "realization_level"
    scenario_dir = summaries_root / "scenario_level"
    comparison_dir = summaries_root / "comparison_level"
    realization_dir.mkdir(parents=True, exist_ok=True)
    scenario_dir.mkdir(parents=True, exist_ok=True)
    comparison_dir.mkdir(parents=True, exist_ok=True)

    metrics_df = pd.DataFrame(rows, columns=SUMMARY_COLUMNS)
    metrics_path = realization_dir / "scenario_leaf_metrics.csv"
    metrics_df.to_csv(metrics_path, index=False)
    write_schema(realization_dir / "scenario_leaf_metrics_schema.yaml")

    climate_year_df = pd.DataFrame(climate_year_rows, columns=CLIMATE_YEAR_COLUMNS)
    climate_year_path = realization_dir / "scenario_leaf_climate_year_metrics.csv"
    climate_year_df.to_csv(climate_year_path, index=False)

    leaf_index_df = pd.read_csv(leaf_index)
    aggregate_df = aggregate_scenario_metrics(metrics_df, leaf_index_df=leaf_index_df, status_by_leaf=status_by_leaf)
    aggregate_path = scenario_dir / "scenario_aggregate_metrics.csv"
    aggregate_df.to_csv(aggregate_path, index=False)

    climate_year_aggregate_df = aggregate_climate_year_metrics(climate_year_df)
    climate_year_aggregate_path = scenario_dir / "scenario_climate_year_metrics.csv"
    climate_year_aggregate_df.to_csv(climate_year_aggregate_path, index=False)

    comparison_df = build_baseline_comparison(metrics_df)
    comparison_path = comparison_dir / "baseline_comparison_metrics.csv"
    comparison_df.to_csv(comparison_path, index=False)

    annual_heating_df = build_annual_space_heating_demand_comparison(metrics_df)
    annual_heating_path = comparison_dir / "annual_space_heating_demand_comparison.csv"
    annual_heating_df.to_csv(annual_heating_path, index=False)

    annual_degree_day_df = build_annual_climate_degree_day_comparison(climate_year_aggregate_df)
    annual_degree_day_path = comparison_dir / "annual_climate_degree_day_comparison.csv"
    annual_degree_day_df.to_csv(annual_degree_day_path, index=False)

    cooling_exposure_df = build_cooling_exposure_overheating_risk_comparison(metrics_df, climate_year_aggregate_df)
    cooling_exposure_path = comparison_dir / "cooling_exposure_overheating_risk_comparison.csv"
    cooling_exposure_df.to_csv(cooling_exposure_path, index=False)

    # Monthly metrics — per-leaf CSVs already written above; write scenario-level aggregate
    monthly_df = pd.DataFrame(all_monthly_rows, columns=MONTHLY_COLUMNS) if all_monthly_rows else pd.DataFrame(columns=MONTHLY_COLUMNS)
    monthly_realization_path = realization_dir / "scenario_leaf_monthly_metrics.csv"
    monthly_df.to_csv(monthly_realization_path, index=False)

    monthly_aggregate_df = aggregate_monthly_metrics(all_monthly_rows)
    monthly_aggregate_path = scenario_dir / "scenario_monthly_metrics.csv"
    monthly_aggregate_df.to_csv(monthly_aggregate_path, index=False)

    monthly_shift_df = build_monthly_demand_shift_comparison(monthly_df)
    monthly_shift_path = comparison_dir / "monthly_demand_shift_comparison.csv"
    monthly_shift_df.to_csv(monthly_shift_path, index=False)

    seasonal_shift_df = build_seasonal_demand_shift_comparison(monthly_df)
    seasonal_shift_path = comparison_dir / "seasonal_demand_shift_comparison.csv"
    seasonal_shift_df.to_csv(seasonal_shift_path, index=False)

    # Cohort grid-stress metrics (diversity factor, aggregate peak)
    cohort_stress_df = build_cohort_grid_stress_metrics(metrics_df)
    cohort_stress_path = scenario_dir / "cohort_grid_stress_metrics.csv"
    cohort_stress_df.to_csv(cohort_stress_path, index=False)

    report_payload = None
    if write_reports:
        report_payload = _write_report(
            experiment_root,
            config_root,
            successful_runs=len(rows),
            per_leaf_summaries=len(rows),
            climate_year_rows=len(climate_year_df),
            aggregate_rows=len(aggregate_df),
            comparison_df=comparison_df,
            metrics_df=metrics_df,
            warnings=warnings,
            assumptions=assumptions,
        )

    return {
        "metrics_path": metrics_path,
        "aggregate_path": aggregate_path,
        "climate_year_path": climate_year_path,
        "climate_year_aggregate_path": climate_year_aggregate_path,
        "comparison_path": comparison_path,
        "annual_space_heating_comparison_path": annual_heating_path,
        "annual_climate_degree_day_comparison_path": annual_degree_day_path,
        "cooling_exposure_overheating_risk_comparison_path": cooling_exposure_path,
        "monthly_realization_path": monthly_realization_path,
        "monthly_aggregate_path": monthly_aggregate_path,
        "monthly_demand_shift_comparison_path": monthly_shift_path,
        "seasonal_demand_shift_comparison_path": seasonal_shift_path,
        "cohort_stress_path": cohort_stress_path,
        "successful_runs_processed": len(rows),
        "per_leaf_summaries_written": len(rows),
        "climate_year_rows": len(climate_year_df),
        "scenario_aggregate_rows": len(aggregate_df),
        "baseline_comparison_rows": len(comparison_df),
        "annual_space_heating_comparison_rows": len(annual_heating_df),
        "annual_climate_degree_day_comparison_rows": len(annual_degree_day_df),
        "cooling_exposure_overheating_risk_comparison_rows": len(cooling_exposure_df),
        "monthly_demand_shift_comparison_rows": len(monthly_shift_df),
        "seasonal_demand_shift_comparison_rows": len(seasonal_shift_df),
        "missing_required_metrics": int(metrics_df["missing_metric_count"].fillna(0).astype(int).sum()) if not metrics_df.empty else 0,
        "raw_output_columns_used": sorted(
            {
                item
                for text in metrics_df.get("raw_output_columns_used", pd.Series(dtype=str)).dropna().astype(str)
                for item in text.split(";")
                if item
            }
        ),
        "climate_columns_used": sorted(
            set(metrics_df.get("climate_temperature_column", pd.Series(dtype=str)).dropna().astype(str))
            | set(metrics_df.get("climate_solar_column", pd.Series(dtype=str)).dropna().astype(str))
        ),
        "near_future_includes_2050": _climate_window_2050_policy(config_root)[0],
        "mid_century_includes_2050": _climate_window_2050_policy(config_root)[1],
        "report": report_payload,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment-root", default=str(DEFAULT_EXPERIMENT_ROOT))
    parser.add_argument("--config-root", default=str(DEFAULT_CONFIG_ROOT))
    parser.add_argument("--leaf-index", default=None)
    parser.add_argument("--run-registry", default=None)
    parser.add_argument("--only-successful", action="store_true", default=True)
    parser.add_argument("--include-failed", action="store_true")
    parser.add_argument("--allow-missing-optional-metrics", action="store_true")
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--scenario-leaf-id")
    parser.add_argument("--scenario-id")
    parser.add_argument("--write-reports", action="store_true")
    parser.add_argument("--print-summary", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    experiment_root = _resolve_cli_path(args.experiment_root)
    config_root = _resolve_cli_path(args.config_root)
    leaf_index = _resolve_cli_path(args.leaf_index) if args.leaf_index else experiment_root / "manifests" / "scenario_leaf_index.csv"
    run_registry = (
        _resolve_cli_path(args.run_registry)
        if args.run_registry
        else experiment_root / "manifests" / "run_registry.csv"
    )
    strict = True
    if args.allow_missing_optional_metrics:
        strict = False
    if args.strict:
        strict = True

    result = generate_summaries(
        experiment_root=experiment_root,
        config_root=config_root,
        leaf_index=leaf_index,
        run_registry=run_registry,
        scenario_leaf_id=args.scenario_leaf_id,
        scenario_id=args.scenario_id,
        only_successful=not args.include_failed,
        include_failed=args.include_failed,
        strict=strict,
        limit=args.limit,
        write_reports=args.write_reports,
    )
    if args.print_summary:
        print("Output standardization complete.")
        print(f"Successful runs processed: {result['successful_runs_processed']}")
        print(f"Per-leaf summaries written: {result['per_leaf_summaries_written']}")
        print(f"Per-year climate metric rows: {result['climate_year_rows']}")
        print(f"Scenario aggregate rows: {result['scenario_aggregate_rows']}")
        print(f"Baseline comparison rows: {result['baseline_comparison_rows']}")
        print(f"Annual space-heating comparison rows: {result['annual_space_heating_comparison_rows']}")
        print(f"Annual climate degree-day comparison rows: {result['annual_climate_degree_day_comparison_rows']}")
        print(f"Cooling exposure and overheating risk rows: {result['cooling_exposure_overheating_risk_comparison_rows']}")
        print(f"Monthly demand-shift comparison rows: {result['monthly_demand_shift_comparison_rows']}")
        print(f"Seasonal demand-shift comparison rows: {result['seasonal_demand_shift_comparison_rows']}")
        print(f"Missing required metrics: {result['missing_required_metrics']}")
        print("Missing climate files: 0")
        print(f"Near-future includes 2050: {'yes' if result['near_future_includes_2050'] else 'no'}")
        print(f"Mid-century includes 2050: {'yes' if result['mid_century_includes_2050'] else 'no'}")
        print("Simulations run: 0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
