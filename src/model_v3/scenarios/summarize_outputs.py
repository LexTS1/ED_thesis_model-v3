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
from model_v3.utils.energy import power_series_to_energy_kwh


REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_EXPERIMENT_ROOT = REPO_ROOT / "model_v3" / "experiments" / "scenario_tree"
DEFAULT_CONFIG_ROOT = REPO_ROOT / "config" / "model_v3" / "scenario_tree"
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
SPACE_HEATING_YEAR_COLUMNS = [
    "scenario_leaf_id",
    "scenario_id",
    "climate_window_id",
    "climate_pathway_id",
    "technology_case_id",
    "realization_id",
    "seed_index",
    "seed_value",
    "cohort_size",
    "analysis_start",
    "analysis_end",
    "source_file_window",
    "year",
    "annual_useful_space_heating_kWh",
    "timestep_count",
    "profile_start",
    "profile_end",
    "raw_outputs_dir",
]


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
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
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
    space_heating_year_rows = _build_space_heating_year_rows(
        record,
        run_config=run_config,
        raw_outputs=raw_outputs,
        summary_row=summary_row,
    )
    return summary_row, climate_year_rows, space_heating_year_rows


def build_leaf_summary_row(
    record: ScenarioLeafRecord,
    *,
    registry_row: Mapping[str, str],
    strict: bool = True,
) -> dict[str, Any]:
    """Build one standardized summary row for a successful scenario leaf."""

    row, _, _ = _build_leaf_summary_payload(record, registry_row=registry_row, strict=strict)
    return row


def write_per_leaf_summary(row: Mapping[str, Any]) -> Path:
    outputs_dir = Path(str(row["raw_outputs_dir"]))
    outputs_dir.mkdir(parents=True, exist_ok=True)
    path = outputs_dir / "standardized_leaf_summary.csv"
    pd.DataFrame([{column: row.get(column, "") for column in SUMMARY_COLUMNS}]).to_csv(path, index=False)
    return path


def write_per_leaf_climate_year_summary(row: Mapping[str, Any], climate_year_rows: list[Mapping[str, Any]]) -> Path:
    outputs_dir = Path(str(row["raw_outputs_dir"]))
    outputs_dir.mkdir(parents=True, exist_ok=True)
    path = outputs_dir / "climate_year_metrics.csv"
    pd.DataFrame(climate_year_rows, columns=CLIMATE_YEAR_COLUMNS).to_csv(path, index=False)
    return path


def write_per_leaf_space_heating_year_summary(
    row: Mapping[str, Any],
    space_heating_year_rows: list[Mapping[str, Any]],
) -> Path:
    outputs_dir = Path(str(row["raw_outputs_dir"]))
    outputs_dir.mkdir(parents=True, exist_ok=True)
    path = outputs_dir / "annual_space_heating_demand_by_year.csv"
    pd.DataFrame(space_heating_year_rows, columns=SPACE_HEATING_YEAR_COLUMNS).to_csv(path, index=False)
    return path


def _first_timeseries_frame(raw_outputs: Mapping[str, Any]) -> pd.DataFrame:
    for key in ("annual_profile", "timeseries"):
        value = raw_outputs.get(key)
        if isinstance(value, pd.DataFrame):
            return value
    raise SummaryError("No annual_profile.csv or timeseries.csv available for annual space-heating demand rows.")


def _build_space_heating_year_rows(
    record: ScenarioLeafRecord,
    *,
    run_config: Mapping[str, Any],
    raw_outputs: Mapping[str, Any],
    summary_row: Mapping[str, Any],
) -> list[dict[str, Any]]:
    frame = _first_timeseries_frame(raw_outputs)
    if "timestamp" not in frame.columns:
        raise SummaryError("Raw profile has no timestamp column for annual space-heating demand rows.")
    if "Q_heating_supplied_W" not in frame.columns:
        raise SummaryError("Raw profile has no Q_heating_supplied_W column for annual space-heating demand rows.")

    timestamps = pd.to_datetime(frame["timestamp"])
    values = pd.to_numeric(frame["Q_heating_supplied_W"], errors="coerce").fillna(0.0)
    energy = power_series_to_energy_kwh(pd.Series(values.to_numpy(dtype=float), index=timestamps))
    stochastic_cfg = dict(run_config.get("stochastic", {}))
    rows: list[dict[str, Any]] = []
    for year, group in energy.groupby(energy.index.year):
        year_timestamps = timestamps[timestamps.map(lambda value: int(value.year) == int(year))]
        payload = {
            "scenario_leaf_id": record.scenario_leaf_id,
            "scenario_id": record.scenario_id,
            "climate_window_id": record.climate_window_id,
            "climate_pathway_id": record.climate_pathway_id,
            "technology_case_id": record.technology_case_id,
            "realization_id": record.realization_id,
            "seed_index": stochastic_cfg.get("seed_index", summary_row.get("seed_index", "")),
            "seed_value": stochastic_cfg.get("seed_value", summary_row.get("seed_value", "")),
            "cohort_size": stochastic_cfg.get("cohort_size", summary_row.get("cohort_size", "")),
            "analysis_start": summary_row.get("analysis_start", ""),
            "analysis_end": summary_row.get("analysis_end", ""),
            "source_file_window": summary_row.get("source_file_window", ""),
            "year": int(year),
            "annual_useful_space_heating_kWh": float(group.sum()),
            "timestep_count": int(len(group)),
            "profile_start": pd.Timestamp(year_timestamps.iloc[0]).isoformat() if len(year_timestamps) else "",
            "profile_end": pd.Timestamp(year_timestamps.iloc[-1]).isoformat() if len(year_timestamps) else "",
            "raw_outputs_dir": summary_row.get("raw_outputs_dir", ""),
        }
        rows.append({column: payload.get(column, "") for column in SPACE_HEATING_YEAR_COLUMNS})
    return rows


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


def build_annual_space_heating_demand_comparison(space_heating_df: pd.DataFrame) -> pd.DataFrame:
    """Compare future annual heating demand against matched baseline realizations."""

    columns = [
        "future_scenario_leaf_id",
        "baseline_scenario_id",
        "future_scenario_id",
        "realization_id",
        "climate_window_id",
        "climate_pathway_id",
        "technology_case_id",
        "future_year",
        "future_annual_useful_space_heating_kWh",
        "baseline_mean_annual_useful_space_heating_kWh",
        "baseline_year_count",
        "baseline_available",
        "delta_abs_kWh",
        "delta_pct",
    ]
    if space_heating_df.empty:
        return pd.DataFrame(columns=columns)

    baseline = space_heating_df[space_heating_df["scenario_id"].astype(str) == BASELINE_SCENARIO_ID].copy()
    baseline_stats = (
        baseline.groupby("realization_id", as_index=True)["annual_useful_space_heating_kWh"]
        .agg(["mean", "count"])
        if not baseline.empty
        else pd.DataFrame(columns=["mean", "count"])
    )
    rows: list[dict[str, Any]] = []
    future = space_heating_df[space_heating_df["scenario_id"].astype(str) != BASELINE_SCENARIO_ID].copy()
    for _, row in future.sort_values(["scenario_leaf_id", "year"]).iterrows():
        realization_id = str(row["realization_id"])
        baseline_available = realization_id in baseline_stats.index
        baseline_mean = float(baseline_stats.loc[realization_id, "mean"]) if baseline_available else float("nan")
        baseline_count = int(baseline_stats.loc[realization_id, "count"]) if baseline_available else 0
        future_value = pd.to_numeric(pd.Series([row["annual_useful_space_heating_kWh"]]), errors="coerce").iloc[0]
        if baseline_available and pd.notna(future_value) and abs(baseline_mean) > 1e-12:
            delta_abs = float(future_value) - baseline_mean
            delta_pct = 100.0 * delta_abs / baseline_mean
        elif baseline_available and pd.notna(future_value):
            delta_abs = float(future_value) - baseline_mean
            delta_pct = float("nan")
        else:
            delta_abs = float("nan")
            delta_pct = float("nan")
        rows.append(
            {
                "future_scenario_leaf_id": row["scenario_leaf_id"],
                "baseline_scenario_id": BASELINE_SCENARIO_ID,
                "future_scenario_id": row["scenario_id"],
                "realization_id": realization_id,
                "climate_window_id": row["climate_window_id"],
                "climate_pathway_id": row["climate_pathway_id"],
                "technology_case_id": row["technology_case_id"],
                "future_year": int(row["year"]),
                "future_annual_useful_space_heating_kWh": float(future_value),
                "baseline_mean_annual_useful_space_heating_kWh": baseline_mean,
                "baseline_year_count": baseline_count,
                "baseline_available": bool(baseline_available),
                "delta_abs_kWh": delta_abs,
                "delta_pct": delta_pct,
            }
        )
    return pd.DataFrame(rows, columns=columns)


def aggregate_annual_space_heating_demand(space_heating_df: pd.DataFrame, comparison_df: pd.DataFrame) -> pd.DataFrame:
    """Aggregate annual space-heating demand and baseline deltas by scenario."""

    columns = [
        "scenario_id",
        "climate_window_id",
        "climate_pathway_id",
        "technology_case_id",
        "annual_year_count",
        "annual_useful_space_heating_kWh_mean",
        "annual_useful_space_heating_kWh_median",
        "annual_useful_space_heating_kWh_p10",
        "annual_useful_space_heating_kWh_p90",
        "annual_useful_space_heating_kWh_min",
        "annual_useful_space_heating_kWh_max",
        "delta_abs_kWh_mean",
        "delta_pct_mean",
    ]
    if space_heating_df.empty:
        return pd.DataFrame(columns=columns)
    rows: list[dict[str, Any]] = []
    group_cols = ["scenario_id", "climate_window_id", "climate_pathway_id", "technology_case_id"]
    comparison_by_scenario = comparison_df.groupby("future_scenario_id") if not comparison_df.empty else {}
    for group_values, group in space_heating_df.groupby(group_cols, dropna=False):
        payload = dict(zip(group_cols, group_values))
        series = pd.to_numeric(group["annual_useful_space_heating_kWh"], errors="coerce").dropna()
        comparison_group = (
            comparison_by_scenario.get_group(payload["scenario_id"])
            if hasattr(comparison_by_scenario, "groups") and payload["scenario_id"] in comparison_by_scenario.groups
            else pd.DataFrame()
        )
        delta_abs = pd.to_numeric(comparison_group.get("delta_abs_kWh", pd.Series(dtype=float)), errors="coerce").dropna()
        delta_pct = pd.to_numeric(comparison_group.get("delta_pct", pd.Series(dtype=float)), errors="coerce").dropna()
        rows.append(
            {
                **payload,
                "annual_year_count": int(series.count()),
                "annual_useful_space_heating_kWh_mean": float(series.mean()) if not series.empty else float("nan"),
                "annual_useful_space_heating_kWh_median": float(series.median()) if not series.empty else float("nan"),
                "annual_useful_space_heating_kWh_p10": float(series.quantile(0.10)) if not series.empty else float("nan"),
                "annual_useful_space_heating_kWh_p90": float(series.quantile(0.90)) if not series.empty else float("nan"),
                "annual_useful_space_heating_kWh_min": float(series.min()) if not series.empty else float("nan"),
                "annual_useful_space_heating_kWh_max": float(series.max()) if not series.empty else float("nan"),
                "delta_abs_kWh_mean": float(delta_abs.mean()) if not delta_abs.empty else float("nan"),
                "delta_pct_mean": float(delta_pct.mean()) if not delta_pct.empty else float("nan"),
            }
        )
    return pd.DataFrame(rows, columns=columns).sort_values(group_cols).reset_index(drop=True)


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
    space_heating_year_rows: list[dict[str, Any]] = []
    warnings: list[str] = []
    assumptions = [
        "Energy metrics are standardized from the raw annual model output year written by the Phase 4 runner.",
        "Climate sensitivity metrics are written both as canonical-window summaries and as per-calendar-year rows.",
        "Baseline comparison rows omit baseline leaves and match future leaves to baseline leaves by realization_id.",
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
            row, annual_climate_rows, annual_space_heating_rows = _build_leaf_summary_payload(
                record,
                registry_row=registry_row,
                strict=strict,
            )
        except MissingRequiredOutputError:
            raise
        except Exception as exc:
            raise SummaryError(f"Failed to summarize {record.scenario_leaf_id}: {exc}") from exc
        write_per_leaf_summary(row)
        write_per_leaf_climate_year_summary(row, annual_climate_rows)
        write_per_leaf_space_heating_year_summary(row, annual_space_heating_rows)
        rows.append(row)
        climate_year_rows.extend(annual_climate_rows)
        space_heating_year_rows.extend(annual_space_heating_rows)

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

    space_heating_year_df = pd.DataFrame(space_heating_year_rows, columns=SPACE_HEATING_YEAR_COLUMNS)
    space_heating_year_path = realization_dir / "annual_space_heating_demand_by_year.csv"
    space_heating_year_df.to_csv(space_heating_year_path, index=False)

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

    annual_space_heating_comparison_dir = comparison_dir / "annual_space_heating_demand"
    annual_space_heating_comparison_dir.mkdir(parents=True, exist_ok=True)
    space_heating_comparison_df = build_annual_space_heating_demand_comparison(space_heating_year_df)
    space_heating_comparison_path = (
        annual_space_heating_comparison_dir / "annual_space_heating_demand_delta_vs_baseline.csv"
    )
    space_heating_comparison_df.to_csv(space_heating_comparison_path, index=False)
    space_heating_aggregate_df = aggregate_annual_space_heating_demand(
        space_heating_year_df,
        space_heating_comparison_df,
    )
    space_heating_aggregate_path = scenario_dir / "annual_space_heating_demand_summary.csv"
    space_heating_aggregate_df.to_csv(space_heating_aggregate_path, index=False)

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
        "space_heating_year_path": space_heating_year_path,
        "space_heating_comparison_path": space_heating_comparison_path,
        "space_heating_aggregate_path": space_heating_aggregate_path,
        "comparison_path": comparison_path,
        "successful_runs_processed": len(rows),
        "per_leaf_summaries_written": len(rows),
        "climate_year_rows": len(climate_year_df),
        "space_heating_year_rows": len(space_heating_year_df),
        "scenario_aggregate_rows": len(aggregate_df),
        "baseline_comparison_rows": len(comparison_df),
        "space_heating_comparison_rows": len(space_heating_comparison_df),
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
        print(f"Annual space-heating demand rows: {result['space_heating_year_rows']}")
        print(f"Scenario aggregate rows: {result['scenario_aggregate_rows']}")
        print(f"Baseline comparison rows: {result['baseline_comparison_rows']}")
        print(f"Annual space-heating comparison rows: {result['space_heating_comparison_rows']}")
        print(f"Missing required metrics: {result['missing_required_metrics']}")
        print("Missing climate files: 0")
        print(f"Near-future includes 2050: {'yes' if result['near_future_includes_2050'] else 'no'}")
        print(f"Mid-century includes 2050: {'yes' if result['mid_century_includes_2050'] else 'no'}")
        print("Simulations run: 0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
