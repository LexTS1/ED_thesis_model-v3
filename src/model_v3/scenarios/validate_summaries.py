"""Validate standardized scenario-tree summary outputs."""

from __future__ import annotations

import argparse
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import pandas as pd
import yaml

from model_v3.scenarios.registry import latest_actual_status, read_registry
from model_v3.scenarios.selection import load_leaf_records
from model_v3.scenarios.summary_contract import (
    BASELINE_SCENARIO_ID,
    REQUIRED_METADATA_COLUMNS,
    REQUIRED_METRIC_COLUMNS,
)
from model_v3.scenarios.summarize_outputs import _climate_window_2050_policy


REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_EXPERIMENT_ROOT = REPO_ROOT / "experiments" / "scenario_tree"


class SummaryValidationError(RuntimeError):
    """Raised when summary validation cannot run."""


def _resolve_cli_path(path_text: str | Path) -> Path:
    path = Path(path_text)
    if path.is_absolute():
        return path
    cwd_candidate = (Path.cwd() / path).resolve()
    if cwd_candidate.exists():
        return cwd_candidate
    return REPO_ROOT / path


def _load_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise SummaryValidationError(f"Missing required summary file: {path}")
    return pd.read_csv(path)


def _latest_status_by_leaf(records: list[Any], registry_rows: list[Mapping[str, str]]) -> dict[str, str]:
    return {
        record.scenario_leaf_id: latest_actual_status(registry_rows, record.scenario_leaf_id)
        for record in records
    }


def _is_truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"true", "1", "yes"}


def _metric_numeric_errors(frame: pd.DataFrame, context: str) -> list[str]:
    errors: list[str] = []
    for column in REQUIRED_METRIC_COLUMNS:
        if column not in frame.columns:
            errors.append(f"{context} missing required metric column: {column}")
            continue
        numeric = pd.to_numeric(frame[column], errors="coerce")
        if numeric.isna().any():
            errors.append(f"{context} has non-numeric or missing values in required metric column: {column}")
    return errors


def _magnitude_warnings(metrics_df: pd.DataFrame) -> list[str]:
    warnings: list[str] = []
    nonnegative = [
        "annual_electricity_gross_kWh",
        "annual_grid_import_kWh",
        "annual_grid_export_kWh",
        "annual_gas_kWh",
        "annual_useful_heating_kWh",
        "annual_dhw_kWh",
        "peak_grid_import_W",
        "winter_peak_grid_import_W",
        "summer_peak_grid_import_W",
        "pv_generation_kWh",
        "pv_self_consumption_kWh",
        "ev_charging_kWh",
        "HDD_15",
        "HDD_18",
        "CDD_22",
    ]
    for column in nonnegative:
        if column in metrics_df:
            values = pd.to_numeric(metrics_df[column], errors="coerce")
            if (values < -1e-9).any():
                warnings.append(f"{column} contains negative values.")
    if "pv_export_fraction" in metrics_df:
        frac = pd.to_numeric(metrics_df["pv_export_fraction"], errors="coerce")
        if ((frac < -1e-9) | (frac > 1.0 + 1e-9)).any():
            warnings.append("pv_export_fraction falls outside [0, 1] for at least one row.")
    if "mean_T_out_C" in metrics_df:
        temp = pd.to_numeric(metrics_df["mean_T_out_C"], errors="coerce")
        if ((temp < -30.0) | (temp > 50.0)).any():
            warnings.append("mean_T_out_C falls outside a broad Belgian climate plausibility range.")
    if "peak_grid_import_W" in metrics_df:
        peak = pd.to_numeric(metrics_df["peak_grid_import_W"], errors="coerce")
        suspicious = peak[(peak > 0.0) & (peak < 100.0)]
        if not suspicious.empty:
            warnings.append("Some peak_grid_import_W values are below 100 W and may be unconverted kW values.")
    return warnings


def _write_report(
    experiment_root: Path,
    *,
    successful_count: int,
    per_leaf_count: int,
    aggregate_rows: int,
    comparison_rows: int,
    comparison_df: pd.DataFrame,
    metrics_df: pd.DataFrame,
    errors: list[str],
    warnings: list[str],
) -> None:
    manifests_dir = experiment_root / "manifests"
    manifests_dir.mkdir(parents=True, exist_ok=True)
    config_root = REPO_ROOT / "config" / "scenario_tree"
    near_includes_2050, mid_includes_2050 = _climate_window_2050_policy(config_root)
    required_missing = sorted(
        {
            item
            for text in metrics_df.get("missing_metrics", pd.Series(dtype=str)).dropna().astype(str)
            for item in text.split(";")
            if item
        }
    )
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
    valid_comparisons = (
        int(comparison_df["comparison_valid"].fillna(False).map(_is_truthy).sum())
        if "comparison_valid" in comparison_df
        else 0
    )
    missing_comparisons = (
        int((~comparison_df["comparison_valid"].fillna(False).map(_is_truthy)).sum())
        if "comparison_valid" in comparison_df
        else 0
    )
    payload = {
        "generation_timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "successful_runs_found": int(successful_count),
        "per_leaf_summaries_generated": int(per_leaf_count),
        "missing_per_leaf_summaries": max(int(successful_count) - int(per_leaf_count), 0),
        "scenario_level_aggregate_rows": int(aggregate_rows),
        "baseline_comparison_rows": int(comparison_rows),
        "future_leaves_with_valid_baseline_comparison": valid_comparisons,
        "future_leaves_missing_baseline_comparison": missing_comparisons,
        "required_metrics": REQUIRED_METRIC_COLUMNS,
        "missing_metrics": required_missing,
        "raw_output_columns_used": raw_output_columns_used,
        "climate_columns_used": climate_columns_used,
        "near_future_includes_2050": bool(near_includes_2050),
        "mid_century_includes_2050": bool(mid_includes_2050),
        "simulations_run": 0,
        "errors": errors,
        "warnings": warnings,
        "assumptions": [
            "Baseline comparison rows omit baseline leaves.",
            "Future leaves are matched to baseline leaves by realization_id.",
            "Validation reads only summary artifacts, manifests, and registry state.",
        ],
    }
    with (manifests_dir / "summary_validation_report.yaml").open("w", encoding="utf-8") as handle:
        yaml.safe_dump(payload, handle, sort_keys=False)

    lines = [
        "# Summary validation report",
        "",
        f"- Generation timestamp UTC: {payload['generation_timestamp_utc']}",
        f"- Number of successful runs found: {successful_count}",
        f"- Number of per-leaf summaries generated: {per_leaf_count}",
        f"- Number of missing per-leaf summaries: {payload['missing_per_leaf_summaries']}",
        f"- Number of scenario-level aggregate rows: {aggregate_rows}",
        f"- Number of baseline comparison rows: {comparison_rows}",
        f"- Future leaves with valid baseline comparison: {valid_comparisons}",
        f"- Future leaves missing baseline comparison: {missing_comparisons}",
        f"- Missing required metrics: {len(required_missing)}",
        f"- Near-future includes 2050: {'yes' if near_includes_2050 else 'no'}",
        f"- Mid-century includes 2050: {'yes' if mid_includes_2050 else 'no'}",
        "- No new simulations were run: yes",
        "",
        "## Required metrics",
        "",
        *[f"- `{metric}`" for metric in REQUIRED_METRIC_COLUMNS],
        "",
        "## Missing metrics",
        "",
    ]
    lines.extend(f"- `{metric}`" for metric in required_missing)
    if not required_missing:
        lines.append("- None")
    lines.extend(["", "## Raw output columns used", ""])
    lines.extend(f"- `{item}`" for item in raw_output_columns_used)
    if not raw_output_columns_used:
        lines.append("- None")
    lines.extend(["", "## Climate columns used", ""])
    lines.extend(f"- `{item}`" for item in climate_columns_used)
    if not climate_columns_used:
        lines.append("- None")
    lines.extend(["", "## Errors", ""])
    lines.extend(f"- {error}" for error in errors)
    if not errors:
        lines.append("- None")
    lines.extend(["", "## Warnings", ""])
    lines.extend(f"- {warning}" for warning in warnings)
    if not warnings:
        lines.append("- None")
    lines.extend(["", "## Assumptions", ""])
    lines.extend(f"- {assumption}" for assumption in payload["assumptions"])
    (manifests_dir / "summary_validation_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def validate_summary_outputs(experiment_root: Path) -> dict[str, Any]:
    """Validate scenario-tree summary outputs and return diagnostics."""

    leaf_index = experiment_root / "manifests" / "scenario_leaf_index.csv"
    run_registry = experiment_root / "manifests" / "run_registry.csv"
    records = load_leaf_records(leaf_index)
    registry_rows = read_registry(run_registry)
    status_by_leaf = _latest_status_by_leaf(records, registry_rows)
    successful_leaf_ids = sorted(leaf_id for leaf_id, status in status_by_leaf.items() if status == "success")
    leaf_index_df = pd.read_csv(leaf_index)

    errors: list[str] = []
    warnings: list[str] = []
    per_leaf_count = 0
    per_leaf_frames: dict[str, pd.DataFrame] = {}
    for record in records:
        if record.scenario_leaf_id not in successful_leaf_ids:
            continue
        outputs_dir = Path(record.row.get("outputs_dir", "")) if record.row.get("outputs_dir") else experiment_root / "runs" / record.scenario_leaf_id / "outputs"
        if not outputs_dir.is_absolute():
            outputs_dir = REPO_ROOT / outputs_dir
        summary_path = outputs_dir / "standardized_leaf_summary.csv"
        if not summary_path.exists():
            errors.append(f"Successful run is missing standardized_leaf_summary.csv: {record.scenario_leaf_id}")
            continue
        frame = pd.read_csv(summary_path)
        per_leaf_count += 1
        per_leaf_frames[record.scenario_leaf_id] = frame
        if len(frame) != 1:
            errors.append(f"Per-leaf summary must contain exactly one row: {summary_path}")
        for column in REQUIRED_METADATA_COLUMNS:
            if column not in frame.columns:
                errors.append(f"Per-leaf summary missing required metadata column {column}: {summary_path}")
        errors.extend(_metric_numeric_errors(frame, f"Per-leaf summary {record.scenario_leaf_id}"))

    realization_path = experiment_root / "summaries" / "realization_level" / "scenario_leaf_metrics.csv"
    aggregate_path = experiment_root / "summaries" / "scenario_level" / "scenario_aggregate_metrics.csv"
    comparison_path = experiment_root / "summaries" / "comparison_level" / "baseline_comparison_metrics.csv"
    try:
        metrics_df = _load_csv(realization_path)
    except SummaryValidationError as exc:
        errors.append(str(exc))
        metrics_df = pd.DataFrame()
    try:
        aggregate_df = _load_csv(aggregate_path)
    except SummaryValidationError as exc:
        errors.append(str(exc))
        aggregate_df = pd.DataFrame()
    try:
        comparison_df = _load_csv(comparison_path)
    except SummaryValidationError as exc:
        errors.append(str(exc))
        comparison_df = pd.DataFrame()

    if not metrics_df.empty:
        for column in REQUIRED_METADATA_COLUMNS:
            if column not in metrics_df.columns:
                errors.append(f"scenario_leaf_metrics.csv missing required metadata column: {column}")
        errors.extend(_metric_numeric_errors(metrics_df, "scenario_leaf_metrics.csv"))
        for column in REQUIRED_METRIC_COLUMNS:
            if column in metrics_df.columns and pd.to_numeric(metrics_df[column], errors="coerce").isna().all():
                errors.append(f"Required metric column is entirely missing: {column}")
        metric_leaf_ids = set(metrics_df.get("scenario_leaf_id", pd.Series(dtype=str)).astype(str))
        missing_from_global = sorted(set(successful_leaf_ids) - metric_leaf_ids)
        if missing_from_global:
            errors.append("Successful run(s) absent from scenario_leaf_metrics.csv: " + ", ".join(missing_from_global))
        extra_rows = sorted(metric_leaf_ids - set(successful_leaf_ids))
        if extra_rows:
            warnings.append("scenario_leaf_metrics.csv contains rows not currently latest-successful: " + ", ".join(extra_rows))
        if len(metrics_df) != len(successful_leaf_ids):
            errors.append(
                f"scenario_leaf_metrics.csv row count {len(metrics_df)} does not match successful leaf count {len(successful_leaf_ids)}."
            )
        warnings.extend(_magnitude_warnings(metrics_df))

        near_rows = metrics_df[metrics_df.get("climate_window_id", "") == "near_future_2030_2049"]
        if not near_rows.empty and near_rows["climate_includes_2050"].map(_is_truthy).any():
            errors.append("Near-future climate metrics include 2050.")
        mid_rows = metrics_df[metrics_df.get("climate_window_id", "") == "mid_century_2050_2070"]
        if not mid_rows.empty and not mid_rows["climate_includes_2050"].map(_is_truthy).all():
            errors.append("Mid-century climate metrics do not include 2050 for all mid-century rows.")

    if not aggregate_df.empty and not metrics_df.empty:
        for _, agg in aggregate_df.iterrows():
            scenario_id = str(agg["scenario_id"])
            expected_success = int((metrics_df["scenario_id"].astype(str) == scenario_id).sum())
            actual_success = int(agg.get("n_successful_realizations", -1))
            if expected_success != actual_success:
                errors.append(
                    f"Aggregate success count mismatch for {scenario_id}: expected {expected_success}, got {actual_success}."
                )
            scenario_leaf_ids = set(leaf_index_df.loc[leaf_index_df["scenario_id"] == scenario_id, "scenario_leaf_id"].astype(str))
            expected_failed = sum(status_by_leaf.get(leaf_id) == "failed" for leaf_id in scenario_leaf_ids)
            actual_failed = int(agg.get("n_failed_realizations", -1))
            if expected_failed != actual_failed:
                errors.append(
                    f"Aggregate failed count mismatch for {scenario_id}: expected {expected_failed}, got {actual_failed}."
                )

    if not comparison_df.empty:
        if (comparison_df.get("future_scenario_id", pd.Series(dtype=str)).astype(str) == BASELINE_SCENARIO_ID).any():
            errors.append("Baseline rows are present as future rows in the baseline comparison table.")
        for _, row in comparison_df.iterrows():
            realization_id = str(row.get("realization_id", ""))
            baseline_leaf_id = str(row.get("baseline_scenario_leaf_id", ""))
            if not baseline_leaf_id.endswith(realization_id):
                errors.append(
                    f"Future leaf matched to wrong baseline seed: {row.get('future_scenario_leaf_id')} -> {baseline_leaf_id}"
                )
            if _is_truthy(row.get("baseline_available")) and _is_truthy(row.get("comparison_valid")):
                for metric in REQUIRED_METRIC_COLUMNS:
                    delta_col = f"{metric}_delta_abs"
                    if delta_col not in comparison_df.columns or pd.isna(row.get(delta_col)):
                        errors.append(f"Missing baseline delta for {row.get('future_scenario_leaf_id')} metric {metric}.")
                        break

    _write_report(
        experiment_root,
        successful_count=len(successful_leaf_ids),
        per_leaf_count=per_leaf_count,
        aggregate_rows=len(aggregate_df),
        comparison_rows=len(comparison_df),
        comparison_df=comparison_df,
        metrics_df=metrics_df,
        errors=errors,
        warnings=warnings,
    )
    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "successful_runs": len(successful_leaf_ids),
        "per_leaf_summaries": per_leaf_count,
        "scenario_aggregate_rows": len(aggregate_df),
        "baseline_comparison_rows": len(comparison_df),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment-root", default=str(DEFAULT_EXPERIMENT_ROOT))
    parser.add_argument("--print-summary", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    experiment_root = _resolve_cli_path(args.experiment_root)
    result = validate_summary_outputs(experiment_root)
    if args.print_summary:
        print("Summary validation complete.")
        print(f"Status: {'pass' if result['ok'] else 'fail'}")
        print(f"Successful runs found: {result['successful_runs']}")
        print(f"Per-leaf summaries found: {result['per_leaf_summaries']}")
        print(f"Scenario aggregate rows: {result['scenario_aggregate_rows']}")
        print(f"Baseline comparison rows: {result['baseline_comparison_rows']}")
        print(f"Errors: {len(result['errors'])}")
        print(f"Warnings: {len(result['warnings'])}")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
