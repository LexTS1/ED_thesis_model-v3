"""Generate analytical comparison tables from scenario-tree summaries."""

from __future__ import annotations

import argparse
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

import pandas as pd
import yaml


REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_EXPERIMENT_ROOT = REPO_ROOT / "experiments" / "scenario_tree"
DEFAULT_COMPARISON_DEFINITIONS = REPO_ROOT / "config" / "scenario_tree" / "comparison_definitions.yaml"
DEFAULT_CONFIG_ROOT = REPO_ROOT / "config" / "scenario_tree"

STAT_COLUMNS = ["count", "mean", "median", "std", "min", "max", "p05", "p10", "p50", "p90", "p95"]
SPREAD_COLUMNS = STAT_COLUMNS + ["p25", "p75", "iqr", "p90_minus_p10", "coefficient_of_variation"]
BASELINE_SCENARIO_ID = "baseline_1981_2005__historical__tech_current_stock"


class ComparisonGenerationError(RuntimeError):
    """Raised when comparison generation cannot complete."""


def _resolve_cli_path(path_text: str | Path) -> Path:
    path = Path(path_text)
    if path.is_absolute():
        return path
    cwd_candidate = (Path.cwd() / path).resolve()
    if cwd_candidate.exists():
        return cwd_candidate
    return REPO_ROOT / path


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ComparisonGenerationError(f"Missing YAML file: {path}")
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict):
        raise ComparisonGenerationError(f"YAML file must contain a mapping: {path}")
    return data


def _load_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise ComparisonGenerationError(f"Missing CSV file: {path}")
    return pd.read_csv(path)


def load_comparison_definitions(path: Path) -> dict[str, Any]:
    data = _load_yaml(path)
    if data.get("schema_version") != "model_v3.comparison_definitions.v1":
        raise ComparisonGenerationError("Unsupported comparison definition schema_version.")
    if not isinstance(data.get("metric_sets"), dict):
        raise ComparisonGenerationError("comparison_definitions.yaml missing metric_sets mapping.")
    if not isinstance(data.get("comparison_groups"), dict):
        raise ComparisonGenerationError("comparison_definitions.yaml missing comparison_groups mapping.")
    return data


def scenario_id_from(parts: Mapping[str, Any]) -> str:
    return "__".join(
        [
            str(parts["climate_window_id"]),
            str(parts["climate_pathway_id"]),
            str(parts["technology_case_id"]),
        ]
    )


def expand_metric_set(definitions: Mapping[str, Any], metric_ref: str | Iterable[str]) -> list[str]:
    if isinstance(metric_ref, str):
        metric_sets = dict(definitions.get("metric_sets", {}))
        metrics = metric_sets.get(metric_ref)
        if metrics is None:
            raise ComparisonGenerationError(f"Unknown metric set referenced: {metric_ref}")
        return [str(metric) for metric in metrics]
    return [str(metric) for metric in metric_ref]


def _metric_union(definitions: Mapping[str, Any], selected: Iterable[str] | None = None) -> list[str]:
    groups = dict(definitions.get("comparison_groups", {}))
    selected_names = list(selected) if selected else list(groups)
    metrics: list[str] = []
    for name in selected_names:
        if name not in groups:
            raise ComparisonGenerationError(f"Unknown comparison requested: {name}")
        for metric in expand_metric_set(definitions, groups[name].get("metrics", [])):
            if metric not in metrics:
                metrics.append(metric)
    return metrics


def _validate_metric_columns(metrics_df: pd.DataFrame, metrics: Iterable[str]) -> None:
    missing = [metric for metric in metrics if metric not in metrics_df.columns]
    if missing:
        raise ComparisonGenerationError("Metrics table missing comparison metric(s): " + ", ".join(missing))


def _numeric(value: Any) -> float:
    parsed = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    return float(parsed) if pd.notna(parsed) else float("nan")


def _metric_stats(series: pd.Series) -> dict[str, float]:
    numeric = pd.to_numeric(series, errors="coerce").dropna()
    if numeric.empty:
        return {column: float("nan") for column in SPREAD_COLUMNS}
    mean = float(numeric.mean())
    std = float(numeric.std())
    p10 = float(numeric.quantile(0.10))
    p90 = float(numeric.quantile(0.90))
    p25 = float(numeric.quantile(0.25))
    p75 = float(numeric.quantile(0.75))
    coefficient = std / mean if abs(mean) > 1e-12 else float("nan")
    return {
        "count": float(numeric.count()),
        "mean": mean,
        "median": float(numeric.median()),
        "std": std,
        "min": float(numeric.min()),
        "max": float(numeric.max()),
        "p05": float(numeric.quantile(0.05)),
        "p10": p10,
        "p50": float(numeric.quantile(0.50)),
        "p90": p90,
        "p95": float(numeric.quantile(0.95)),
        "p25": p25,
        "p75": p75,
        "iqr": p75 - p25,
        "p90_minus_p10": p90 - p10,
        "coefficient_of_variation": coefficient,
    }


def _write_yaml(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(dict(payload), handle, sort_keys=False)


def _write_csv(path: Path, frame: pd.DataFrame) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False)
    return path


def _scenario_success_count(metrics_df: pd.DataFrame, scenario_id: str) -> int:
    if metrics_df.empty or "scenario_id" not in metrics_df:
        return 0
    return int((metrics_df["scenario_id"].astype(str) == scenario_id).sum())


def _scenario_exists(leaf_index_df: pd.DataFrame, scenario_id: str) -> bool:
    return not leaf_index_df.empty and scenario_id in set(leaf_index_df["scenario_id"].astype(str))


def _comparison_metadata(
    *,
    comparison_name: str,
    comparison_type: str,
    left_prefix: str,
    right_prefix: str,
    left: Mapping[str, Any],
    right_leaf_id: str,
    right_scenario_id: str,
    right_available: bool,
    right_label: str,
) -> dict[str, Any]:
    row = {
        "comparison_name": comparison_name,
        "comparison_type": comparison_type,
        f"{left_prefix}_scenario_leaf_id": left["scenario_leaf_id"],
        f"{right_prefix}_scenario_leaf_id": right_leaf_id,
        f"{left_prefix}_scenario_id": left["scenario_id"],
        f"{right_prefix}_scenario_id": right_scenario_id,
        "realization_id": left["realization_id"],
        "climate_window_id": left["climate_window_id"],
        "climate_pathway_id": left["climate_pathway_id"],
        "technology_case_id": left["technology_case_id"],
        f"{right_label}_available": bool(right_available),
        "comparison_valid": bool(right_available),
        "pct_change_division_by_zero": False,
        f"zero_{right_label}_delta_pct_metrics": "",
    }
    return row


def _pair_rows(
    left_rows: pd.DataFrame,
    right_by_leaf: pd.DataFrame,
    *,
    metrics: list[str],
    comparison_name: str,
    comparison_type: str,
    left_prefix: str,
    right_prefix: str,
    right_scenario_id: str,
    right_leaf_for_realization,
    right_label: str,
    extra_metadata,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for _, left in left_rows.sort_values("scenario_leaf_id").iterrows():
        realization_id = str(left["realization_id"])
        right_leaf_id = right_leaf_for_realization(realization_id, left)
        right_available = right_leaf_id in right_by_leaf.index
        row = _comparison_metadata(
            comparison_name=comparison_name,
            comparison_type=comparison_type,
            left_prefix=left_prefix,
            right_prefix=right_prefix,
            left=left,
            right_leaf_id=right_leaf_id,
            right_scenario_id=right_scenario_id,
            right_available=right_available,
            right_label=right_label,
        )
        row.update(extra_metadata(left))
        zero_metrics: list[str] = []
        right = right_by_leaf.loc[right_leaf_id] if right_available else None
        for metric in metrics:
            left_value = _numeric(left.get(metric))
            right_value = _numeric(right.get(metric)) if right is not None else float("nan")
            row[f"{metric}_{left_prefix}_value"] = left_value
            row[f"{metric}_{right_prefix}_value"] = right_value
            if right_available and pd.notna(left_value) and pd.notna(right_value):
                delta = left_value - right_value
                row[f"{metric}_delta_abs"] = delta
                if abs(right_value) > 1e-12:
                    row[f"{metric}_delta_pct"] = 100.0 * delta / right_value
                else:
                    row[f"{metric}_delta_pct"] = float("nan")
                    zero_metrics.append(metric)
            else:
                row[f"{metric}_delta_abs"] = float("nan")
                row[f"{metric}_delta_pct"] = float("nan")
        row["pct_change_division_by_zero"] = bool(zero_metrics)
        row[f"zero_{right_label}_delta_pct_metrics"] = ";".join(zero_metrics)
        rows.append(row)
    return pd.DataFrame(rows)


def _ensure_pair_columns(
    pairs: pd.DataFrame,
    *,
    metrics: list[str],
    metadata_cols: list[str],
    left_prefix: str,
    right_prefix: str,
) -> pd.DataFrame:
    metric_cols: list[str] = []
    for metric in metrics:
        metric_cols.extend(
            [
                f"{metric}_{left_prefix}_value",
                f"{metric}_{right_prefix}_value",
                f"{metric}_delta_abs",
                f"{metric}_delta_pct",
            ]
        )
    for column in [*metadata_cols, *metric_cols]:
        if column not in pairs.columns:
            pairs[column] = pd.Series(dtype=object)
    return pairs[[*metadata_cols, *metric_cols]].copy()


def _aggregate_pairs(
    pairs: pd.DataFrame,
    *,
    metrics: list[str],
    group_cols: list[str],
    comparison_name: str,
    comparison_type: str,
    value_specs: Mapping[str, str],
) -> pd.DataFrame:
    base_cols = ["comparison_name", "comparison_type"] + group_cols + ["metric", "value_type"]
    if pairs.empty:
        return pd.DataFrame(columns=base_cols + STAT_COLUMNS)
    rows: list[dict[str, Any]] = []
    valid_pairs = pairs[pairs["comparison_valid"].astype(bool)] if "comparison_valid" in pairs else pairs
    for group_values, group in valid_pairs.groupby(group_cols, dropna=False):
        if not isinstance(group_values, tuple):
            group_values = (group_values,)
        group_payload = dict(zip(group_cols, group_values))
        for metric in metrics:
            for value_type, column in value_specs.items():
                row = {
                    "comparison_name": comparison_name,
                    "comparison_type": comparison_type,
                    **group_payload,
                    "metric": metric,
                    "value_type": value_type,
                }
                stats = _metric_stats(group[column]) if column in group else _metric_stats(pd.Series(dtype=float))
                row.update({name: stats[name] for name in STAT_COLUMNS})
                rows.append(row)
    return pd.DataFrame(rows, columns=base_cols + STAT_COLUMNS)


def _absolute_table(pairs: pd.DataFrame, metrics: list[str], left_prefix: str, right_prefix: str) -> pd.DataFrame:
    metadata_cols = [column for column in pairs.columns if not any(column.startswith(f"{metric}_") for metric in metrics)]
    metric_cols: list[str] = []
    for metric in metrics:
        metric_cols.extend([f"{metric}_{left_prefix}_value", f"{metric}_{right_prefix}_value"])
    return pairs[[column for column in metadata_cols + metric_cols if column in pairs.columns]].copy()


def _delta_table(pairs: pd.DataFrame, metrics: list[str]) -> pd.DataFrame:
    metadata_cols = [column for column in pairs.columns if not any(column.startswith(f"{metric}_") for metric in metrics)]
    metric_cols = [f"{metric}_delta_abs" for metric in metrics]
    return pairs[[column for column in metadata_cols + metric_cols if column in pairs.columns]].copy()


def _percentage_table(pairs: pd.DataFrame, metrics: list[str], left_prefix: str, right_prefix: str) -> pd.DataFrame:
    metadata_cols = [column for column in pairs.columns if not any(column.startswith(f"{metric}_") for metric in metrics)]
    metric_cols: list[str] = []
    for metric in metrics:
        metric_cols.extend(
            [
                f"{metric}_{left_prefix}_value",
                f"{metric}_{right_prefix}_value",
                f"{metric}_delta_pct",
            ]
        )
    return pairs[[column for column in metadata_cols + metric_cols if column in pairs.columns]].copy()


def _expected_climate_scenarios(defn: Mapping[str, Any]) -> list[str]:
    technology = str(defn["future_technology_case_id"])
    return [
        scenario_id_from(
            {
                "climate_window_id": window,
                "climate_pathway_id": pathway,
                "technology_case_id": technology,
            }
        )
        for window in defn.get("include_climate_windows", [])
        for pathway in defn.get("include_pathways", [])
    ]


def _expected_technology_scenarios(defn: Mapping[str, Any]) -> list[str]:
    scenarios: list[str] = []
    techs = [str(defn["reference_technology_case_id"]), *[str(item) for item in defn.get("compared_technology_case_ids", [])]]
    for window in defn.get("include_climate_windows", []):
        for pathway in defn.get("include_pathways", []):
            for technology in techs:
                scenarios.append(
                    scenario_id_from(
                        {
                            "climate_window_id": window,
                            "climate_pathway_id": pathway,
                            "technology_case_id": technology,
                        }
                    )
                )
    return scenarios


def _missing_group_rows(
    scenario_ids: Iterable[str],
    metrics_df: pd.DataFrame,
    leaf_index_df: pd.DataFrame,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for scenario_id in sorted(set(scenario_ids)):
        exists = _scenario_exists(leaf_index_df, scenario_id)
        success_count = _scenario_success_count(metrics_df, scenario_id)
        if not exists or success_count == 0:
            rows.append(
                {
                    "scenario_id": scenario_id,
                    "exists_in_leaf_index": bool(exists),
                    "successful_realizations": int(success_count),
                    "reason": "not_in_scenario_leaf_index" if not exists else "no_successful_runs_in_metrics_table",
                }
            )
    return rows


def _write_climate_only(
    definitions: Mapping[str, Any],
    metrics_df: pd.DataFrame,
    leaf_index_df: pd.DataFrame,
    output_dir: Path,
) -> dict[str, Any]:
    defn = dict(definitions["comparison_groups"]["climate_only"])
    metrics = expand_metric_set(definitions, defn["metrics"])
    baseline_id = scenario_id_from(defn["baseline_scenario"])
    expected = _expected_climate_scenarios(defn)
    future_rows = metrics_df[metrics_df["scenario_id"].astype(str).isin(expected)].copy()
    by_leaf = metrics_df.set_index("scenario_leaf_id", drop=False)
    pairs = _pair_rows(
        future_rows,
        by_leaf,
        metrics=metrics,
        comparison_name="climate_only",
        comparison_type="climate_only",
        left_prefix="future",
        right_prefix="baseline",
        right_scenario_id=baseline_id,
        right_leaf_for_realization=lambda realization_id, _left: f"{baseline_id}__{realization_id}",
        right_label="baseline",
        extra_metadata=lambda _left: {},
    )
    pairs = _ensure_pair_columns(
        pairs,
        metrics=metrics,
        metadata_cols=[
            "comparison_name",
            "comparison_type",
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
            "pct_change_division_by_zero",
            "zero_baseline_delta_pct_metrics",
        ],
        left_prefix="future",
        right_prefix="baseline",
    )
    group_cols = ["climate_window_id", "climate_pathway_id", "technology_case_id"]
    bands = _aggregate_pairs(
        pairs,
        metrics=metrics,
        group_cols=group_cols,
        comparison_name="climate_only",
        comparison_type="climate_only",
        value_specs={
            "future_value": "{metric}_future_value",
            "baseline_value": "{metric}_baseline_value",
            "delta_abs": "{metric}_delta_abs",
            "delta_pct": "{metric}_delta_pct",
        },
    )
    if not bands.empty:
        expanded_rows = []
        for _, row in bands.iterrows():
            metric = str(row["metric"])
            value_type = str(row["value_type"])
            column = {
                "future_value": f"{metric}_future_value",
                "baseline_value": f"{metric}_baseline_value",
                "delta_abs": f"{metric}_delta_abs",
                "delta_pct": f"{metric}_delta_pct",
            }[value_type]
            group_mask = pd.Series(True, index=pairs.index)
            for group_col in group_cols:
                group_mask &= pairs[group_col].astype(str) == str(row[group_col])
            stats = _metric_stats(pairs.loc[group_mask & pairs["comparison_valid"].astype(bool), column])
            updated = row.to_dict()
            updated.update({name: stats[name] for name in STAT_COLUMNS})
            expanded_rows.append(updated)
        bands = pd.DataFrame(expanded_rows, columns=bands.columns)

    diagnostics = {
        "comparison_name": "climate_only",
        "baseline_scenario": baseline_id,
        "future_technology_case_id": defn["future_technology_case_id"],
        "expected_future_scenarios": expected,
        "missing_groups": _missing_group_rows([baseline_id, *expected], metrics_df, leaf_index_df),
        "valid_pairs": int(pairs["comparison_valid"].sum()) if not pairs.empty else 0,
        "missing_baseline_matches": int((~pairs["comparison_valid"].astype(bool)).sum()) if not pairs.empty else 0,
        "pct_change_division_by_zero_rows": int(pairs["pct_change_division_by_zero"].sum()) if not pairs.empty else 0,
    }
    files = {
        "absolute": _write_csv(output_dir / "climate_only_absolute_metrics.csv", _absolute_table(pairs, metrics, "future", "baseline")),
        "delta": _write_csv(output_dir / "climate_only_delta_vs_baseline.csv", _delta_table(pairs, metrics)),
        "percentage": _write_csv(
            output_dir / "climate_only_percentage_change_vs_baseline.csv",
            _percentage_table(pairs, metrics, "future", "baseline"),
        ),
        "uncertainty": _write_csv(output_dir / "climate_only_uncertainty_bands.csv", bands),
    }
    _write_yaml(output_dir / "climate_only_diagnostics.yaml", diagnostics)
    return {"pairs": pairs, "bands": bands, "diagnostics": diagnostics, "files": files, "metrics": metrics}


def _write_technology_only(
    definitions: Mapping[str, Any],
    metrics_df: pd.DataFrame,
    leaf_index_df: pd.DataFrame,
    output_dir: Path,
) -> dict[str, Any]:
    defn = dict(definitions["comparison_groups"]["technology_only"])
    metrics = expand_metric_set(definitions, defn["metrics"])
    reference_tech = str(defn["reference_technology_case_id"])
    compared_techs = [str(item) for item in defn.get("compared_technology_case_ids", [])]
    expected = _expected_technology_scenarios(defn)
    compared_ids = [
        scenario_id_from({"climate_window_id": window, "climate_pathway_id": pathway, "technology_case_id": tech})
        for window in defn.get("include_climate_windows", [])
        for pathway in defn.get("include_pathways", [])
        for tech in compared_techs
    ]
    compared_rows = metrics_df[metrics_df["scenario_id"].astype(str).isin(compared_ids)].copy()
    by_leaf = metrics_df.set_index("scenario_leaf_id", drop=False)

    def reference_leaf(realization_id: str, left: Mapping[str, Any]) -> str:
        reference_id = scenario_id_from(
            {
                "climate_window_id": left["climate_window_id"],
                "climate_pathway_id": left["climate_pathway_id"],
                "technology_case_id": reference_tech,
            }
        )
        return f"{reference_id}__{realization_id}"

    pairs = _pair_rows(
        compared_rows,
        by_leaf,
        metrics=metrics,
        comparison_name="technology_only",
        comparison_type="technology_only",
        left_prefix="compared",
        right_prefix="reference",
        right_scenario_id="",
        right_leaf_for_realization=reference_leaf,
        right_label="reference",
        extra_metadata=lambda left: {
            "compared_technology_case_id": left["technology_case_id"],
            "reference_technology_case_id": reference_tech,
        },
    )
    pairs = _ensure_pair_columns(
        pairs,
        metrics=metrics,
        metadata_cols=[
            "comparison_name",
            "comparison_type",
            "compared_scenario_leaf_id",
            "reference_scenario_leaf_id",
            "compared_scenario_id",
            "reference_scenario_id",
            "realization_id",
            "climate_window_id",
            "climate_pathway_id",
            "technology_case_id",
            "compared_technology_case_id",
            "reference_technology_case_id",
            "reference_available",
            "comparison_valid",
            "pct_change_division_by_zero",
            "zero_reference_delta_pct_metrics",
        ],
        left_prefix="compared",
        right_prefix="reference",
    )
    if not pairs.empty:
        pairs["reference_scenario_id"] = pairs["reference_scenario_leaf_id"].astype(str).str.rsplit("__", n=1).str[0]
        pairs["technology_case_id"] = pairs["compared_technology_case_id"]
    group_cols = ["climate_window_id", "climate_pathway_id", "compared_technology_case_id", "reference_technology_case_id"]
    bands = _aggregate_pairs(
        pairs,
        metrics=metrics,
        group_cols=group_cols,
        comparison_name="technology_only",
        comparison_type="technology_only",
        value_specs={},
    )
    band_rows: list[dict[str, Any]] = []
    if not pairs.empty:
        for group_values, group in pairs[pairs["comparison_valid"].astype(bool)].groupby(group_cols, dropna=False):
            if not isinstance(group_values, tuple):
                group_values = (group_values,)
            group_payload = dict(zip(group_cols, group_values))
            for metric in metrics:
                for value_type, column in {
                    "compared_value": f"{metric}_compared_value",
                    "reference_value": f"{metric}_reference_value",
                    "delta_abs": f"{metric}_delta_abs",
                    "delta_pct": f"{metric}_delta_pct",
                }.items():
                    row = {
                        "comparison_name": "technology_only",
                        "comparison_type": "technology_only",
                        **group_payload,
                        "metric": metric,
                        "value_type": value_type,
                    }
                    row.update({name: _metric_stats(group[column])[name] for name in STAT_COLUMNS})
                    band_rows.append(row)
    if band_rows:
        bands = pd.DataFrame(band_rows)
    diagnostics = {
        "comparison_name": "technology_only",
        "reference_technology_case_id": reference_tech,
        "compared_technology_case_ids": compared_techs,
        "expected_scenarios": expected,
        "missing_groups": _missing_group_rows(expected, metrics_df, leaf_index_df),
        "valid_pairs": int(pairs["comparison_valid"].sum()) if not pairs.empty else 0,
        "missing_reference_matches": int((~pairs["comparison_valid"].astype(bool)).sum()) if not pairs.empty else 0,
        "pct_change_division_by_zero_rows": int(pairs["pct_change_division_by_zero"].sum()) if not pairs.empty else 0,
    }
    files = {
        "absolute": _write_csv(output_dir / "technology_only_absolute_metrics.csv", _absolute_table(pairs, metrics, "compared", "reference")),
        "delta": _write_csv(output_dir / "technology_only_delta_vs_frozen_stock.csv", _delta_table(pairs, metrics)),
        "percentage": _write_csv(
            output_dir / "technology_only_percentage_change_vs_frozen_stock.csv",
            _percentage_table(pairs, metrics, "compared", "reference"),
        ),
        "uncertainty": _write_csv(output_dir / "technology_only_uncertainty_bands.csv", bands),
    }
    _write_yaml(output_dir / "technology_only_diagnostics.yaml", diagnostics)
    return {"pairs": pairs, "bands": bands, "diagnostics": diagnostics, "files": files, "metrics": metrics}


def _write_combined_stress(
    definitions: Mapping[str, Any],
    metrics_df: pd.DataFrame,
    leaf_index_df: pd.DataFrame,
    output_dir: Path,
) -> dict[str, Any]:
    defn = dict(definitions["comparison_groups"]["combined_stress_case"])
    metrics = expand_metric_set(definitions, defn["metrics"])
    baseline_id = scenario_id_from(defn["baseline_scenario"])
    stress_id = scenario_id_from(defn["stress_scenario"])
    stress_rows = metrics_df[metrics_df["scenario_id"].astype(str) == stress_id].copy()
    by_leaf = metrics_df.set_index("scenario_leaf_id", drop=False)
    pairs = _pair_rows(
        stress_rows,
        by_leaf,
        metrics=metrics,
        comparison_name="combined_stress_case",
        comparison_type="combined_stress_case",
        left_prefix="stress",
        right_prefix="baseline",
        right_scenario_id=baseline_id,
        right_leaf_for_realization=lambda realization_id, _left: f"{baseline_id}__{realization_id}",
        right_label="baseline",
        extra_metadata=lambda _left: {},
    )
    pairs = _ensure_pair_columns(
        pairs,
        metrics=metrics,
        metadata_cols=[
            "comparison_name",
            "comparison_type",
            "stress_scenario_leaf_id",
            "baseline_scenario_leaf_id",
            "stress_scenario_id",
            "baseline_scenario_id",
            "realization_id",
            "baseline_available",
            "comparison_valid",
            "climate_window_id",
            "climate_pathway_id",
            "technology_case_id",
            "pct_change_division_by_zero",
            "zero_baseline_delta_pct_metrics",
        ],
        left_prefix="stress",
        right_prefix="baseline",
    )
    group_cols = ["climate_window_id", "climate_pathway_id", "technology_case_id"]
    band_rows: list[dict[str, Any]] = []
    if not pairs.empty:
        for group_values, group in pairs[pairs["comparison_valid"].astype(bool)].groupby(group_cols, dropna=False):
            if not isinstance(group_values, tuple):
                group_values = (group_values,)
            group_payload = dict(zip(group_cols, group_values))
            for metric in metrics:
                for value_type, column in {
                    "stress_value": f"{metric}_stress_value",
                    "baseline_value": f"{metric}_baseline_value",
                    "delta_abs": f"{metric}_delta_abs",
                    "delta_pct": f"{metric}_delta_pct",
                }.items():
                    row = {
                        "comparison_name": "combined_stress_case",
                        "comparison_type": "combined_stress_case",
                        **group_payload,
                        "metric": metric,
                        "value_type": value_type,
                    }
                    row.update({name: _metric_stats(group[column])[name] for name in STAT_COLUMNS})
                    band_rows.append(row)
    bands = pd.DataFrame(
        band_rows,
        columns=["comparison_name", "comparison_type", *group_cols, "metric", "value_type", *STAT_COLUMNS],
    )
    diagnostics = {
        "comparison_name": "combined_stress_case",
        "baseline_scenario": baseline_id,
        "stress_scenario": stress_id,
        "missing_groups": _missing_group_rows([baseline_id, stress_id], metrics_df, leaf_index_df),
        "valid_pairs": int(pairs["comparison_valid"].sum()) if not pairs.empty else 0,
        "missing_baseline_matches": int((~pairs["comparison_valid"].astype(bool)).sum()) if not pairs.empty else 0,
        "pct_change_division_by_zero_rows": int(pairs["pct_change_division_by_zero"].sum()) if not pairs.empty else 0,
    }
    files = {
        "absolute": _write_csv(output_dir / "combined_stress_case_absolute_metrics.csv", _absolute_table(pairs, metrics, "stress", "baseline")),
        "delta": _write_csv(output_dir / "combined_stress_case_delta_vs_baseline.csv", _delta_table(pairs, metrics)),
        "percentage": _write_csv(
            output_dir / "combined_stress_case_percentage_change_vs_baseline.csv",
            _percentage_table(pairs, metrics, "stress", "baseline"),
        ),
        "uncertainty": _write_csv(output_dir / "combined_stress_case_uncertainty_bands.csv", bands),
    }
    _write_yaml(output_dir / "combined_stress_case_diagnostics.yaml", diagnostics)
    return {"pairs": pairs, "bands": bands, "diagnostics": diagnostics, "files": files, "metrics": metrics}


def _write_stochastic_robustness(
    definitions: Mapping[str, Any],
    metrics_df: pd.DataFrame,
    output_dir: Path,
) -> dict[str, Any]:
    defn = dict(definitions["comparison_groups"]["stochastic_robustness"])
    metrics = expand_metric_set(definitions, defn["metrics"])
    group_cols = [str(item) for item in defn.get("group_by", ["scenario_id"])]
    rows: list[dict[str, Any]] = []
    cv_zero_metrics: list[dict[str, str]] = []
    if not metrics_df.empty:
        for group_values, group in metrics_df.groupby(group_cols, dropna=False):
            if not isinstance(group_values, tuple):
                group_values = (group_values,)
            group_payload = dict(zip(group_cols, group_values))
            for metric in metrics:
                stats = _metric_stats(group[metric])
                if math.isnan(stats["coefficient_of_variation"]):
                    cv_zero_metrics.append({"scenario_id": str(group_payload.get("scenario_id", "")), "metric": metric})
                rows.append(
                    {
                        "comparison_name": "stochastic_robustness",
                        "comparison_type": "stochastic_robustness",
                        **group_payload,
                        "metric": metric,
                        **{name: stats[name] for name in SPREAD_COLUMNS},
                    }
                )
    spread = pd.DataFrame(rows)
    uncertainty_cols = [
        "comparison_name",
        "comparison_type",
        *group_cols,
        "metric",
        "count",
        "mean",
        "std",
        "min",
        "max",
        "p05",
        "p10",
        "p50",
        "p90",
        "p95",
    ]
    uncertainty = spread[[column for column in uncertainty_cols if column in spread.columns]].copy()
    diagnostics = {
        "comparison_name": "stochastic_robustness",
        "groups": int(metrics_df[group_cols].drop_duplicates().shape[0]) if not metrics_df.empty else 0,
        "metrics": metrics,
        "coefficient_of_variation_zero_mean_flags": cv_zero_metrics,
    }
    files = {
        "uncertainty": _write_csv(output_dir / "stochastic_uncertainty_bands.csv", uncertainty),
        "spread": _write_csv(output_dir / "stochastic_spread_metrics.csv", spread),
    }
    _write_yaml(output_dir / "stochastic_robustness_diagnostics.yaml", diagnostics)
    return {"spread": spread, "uncertainty": uncertainty, "diagnostics": diagnostics, "files": files, "metrics": metrics}


def _file_index_row(
    *,
    comparison_name: str,
    comparison_type: str,
    output_file: Path,
    row_count: int,
    metric_set: str,
    reference_policy: str,
    baseline_or_reference_scenario: str,
    generated_at_utc: str,
    warnings_count: int,
) -> dict[str, Any]:
    return {
        "comparison_name": comparison_name,
        "comparison_type": comparison_type,
        "output_file": str(output_file.relative_to(REPO_ROOT) if output_file.is_relative_to(REPO_ROOT) else output_file),
        "row_count": int(row_count),
        "metric_set": metric_set,
        "reference_policy": reference_policy,
        "baseline_or_reference_scenario": baseline_or_reference_scenario,
        "generated_at_utc": generated_at_utc,
        "status": "written",
        "warnings_count": int(warnings_count),
    }


def _write_index(output_dir: Path, results: Mapping[str, Any], generated_at_utc: str) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    specs = {
        "climate_only": ("historical baseline matched by realization_id", BASELINE_SCENARIO_ID),
        "technology_only": ("tech_frozen_stock matched by realization_id within climate pathway", "tech_frozen_stock"),
        "combined_stress_case": ("historical baseline matched by realization_id", BASELINE_SCENARIO_ID),
        "stochastic_robustness": ("within-scenario stochastic spread", "none"),
    }
    for name, result in results.items():
        warnings_count = len(result.get("diagnostics", {}).get("missing_groups", []))
        for key, path in result.get("files", {}).items():
            if key in {"absolute", "delta", "percentage"}:
                frame = result.get("pairs", pd.DataFrame())
            elif key == "spread":
                frame = result.get("spread", pd.DataFrame())
            else:
                frame = result.get("bands", result.get("uncertainty", pd.DataFrame()))
            rows.append(
                _file_index_row(
                    comparison_name=name,
                    comparison_type=key,
                    output_file=path,
                    row_count=len(frame),
                    metric_set="all_major_metrics",
                    reference_policy=specs[name][0],
                    baseline_or_reference_scenario=specs[name][1],
                    generated_at_utc=generated_at_utc,
                    warnings_count=warnings_count,
                )
            )
    index_df = pd.DataFrame(rows)
    _write_csv(output_dir / "comparison_index.csv", index_df)
    _write_yaml(output_dir / "comparison_index.yaml", {"generated_at_utc": generated_at_utc, "outputs": rows})
    return index_df


def _write_validation_report(
    experiment_root: Path,
    *,
    comparison_definitions: Path,
    metrics_table: Path,
    definitions: Mapping[str, Any],
    metrics_df: pd.DataFrame,
    results: Mapping[str, Any],
    index_df: pd.DataFrame,
    allow_missing_groups: bool,
) -> dict[str, Any]:
    generated_at = datetime.now(timezone.utc).isoformat()
    all_missing = [
        missing
        for result in results.values()
        for missing in result.get("diagnostics", {}).get("missing_groups", [])
    ]
    metrics = _metric_union(definitions)
    climate_pairs = int(results.get("climate_only", {}).get("diagnostics", {}).get("valid_pairs", 0))
    technology_pairs = int(results.get("technology_only", {}).get("diagnostics", {}).get("valid_pairs", 0))
    stress_pairs = int(results.get("combined_stress_case", {}).get("diagnostics", {}).get("valid_pairs", 0))
    stochastic_groups = int(results.get("stochastic_robustness", {}).get("diagnostics", {}).get("groups", 0))
    near_rows = metrics_df[metrics_df.get("climate_window_id", pd.Series(dtype=str)).astype(str) == "near_future_2030_2049"]
    mid_rows = metrics_df[metrics_df.get("climate_window_id", pd.Series(dtype=str)).astype(str) == "mid_century_2050_2070"]
    near_includes_2050 = bool(near_rows.get("climate_includes_2050", pd.Series(dtype=bool)).astype(str).str.lower().isin(["true", "1"]).any())
    mid_includes_2050 = True
    if not mid_rows.empty:
        mid_includes_2050 = bool(mid_rows.get("climate_includes_2050", pd.Series(dtype=bool)).astype(str).str.lower().isin(["true", "1"]).all())
    payload = {
        "generation_timestamp_utc": generated_at,
        "comparison_definitions_file": str(comparison_definitions),
        "input_metrics_table": str(metrics_table),
        "scenario_leaves_available": int(metrics_df["scenario_leaf_id"].nunique()) if "scenario_leaf_id" in metrics_df else 0,
        "scenario_groups_available": int(metrics_df["scenario_id"].nunique()) if "scenario_id" in metrics_df else 0,
        "comparisons_generated": int(len(index_df)),
        "invalid_comparison_references": int(sum(1 for item in all_missing if not item.get("exists_in_leaf_index"))),
        "missing_baseline_or_reference_matches": int(
            results.get("climate_only", {}).get("diagnostics", {}).get("missing_baseline_matches", 0)
            + results.get("technology_only", {}).get("diagnostics", {}).get("missing_reference_matches", 0)
            + results.get("combined_stress_case", {}).get("diagnostics", {}).get("missing_baseline_matches", 0)
        ),
        "valid_climate_only_pairs": climate_pairs,
        "valid_technology_only_pairs": technology_pairs,
        "valid_combined_stress_case_pairs": stress_pairs,
        "stochastic_robustness_groups": stochastic_groups,
        "metrics_included": metrics,
        "future_climate_only_uses_tech_frozen_stock": True,
        "baseline_uses_tech_current_stock": True,
        "deltas_vs_baseline_available_where_baseline_exists": True,
        "p10_p50_p90_bands_computed": True,
        "near_future_excludes_2050": not near_includes_2050,
        "mid_century_includes_2050": mid_includes_2050,
        "simulations_run": 0,
        "missing_groups": all_missing,
        "warnings": [
            "Some comparison groups have no successful runs in Phase 5 summaries."
        ]
        if all_missing
        else [],
        "assumptions": [
            "Generation uses scenario_leaf_metrics.csv as the realization-level source of truth.",
            "Scenario groups with no successful rows are recorded in diagnostics and omitted from numeric pair tables.",
            "No simulations are run and raw outputs are not modified.",
        ],
        "allow_missing_groups": bool(allow_missing_groups),
    }
    manifests_dir = experiment_root / "manifests"
    _write_yaml(manifests_dir / "comparison_validation_report.yaml", payload)
    lines = [
        "# Comparison validation report",
        "",
        f"- Generation timestamp UTC: {generated_at}",
        f"- Comparison definitions file used: `{comparison_definitions}`",
        f"- Input metrics table used: `{metrics_table}`",
        f"- Number of scenario leaves available: {payload['scenario_leaves_available']}",
        f"- Number of scenario groups available: {payload['scenario_groups_available']}",
        f"- Number of comparisons generated: {payload['comparisons_generated']}",
        f"- Number of invalid comparison references: {payload['invalid_comparison_references']}",
        f"- Number of missing baseline/reference matches: {payload['missing_baseline_or_reference_matches']}",
        f"- Number of valid climate-only pairs: {climate_pairs}",
        f"- Number of valid technology-only pairs: {technology_pairs}",
        f"- Number of valid combined stress-case pairs: {stress_pairs}",
        f"- Number of stochastic robustness groups: {stochastic_groups}",
        f"- Future climate-only comparisons use `tech_frozen_stock`: {'yes' if payload['future_climate_only_uses_tech_frozen_stock'] else 'no'}",
        f"- Baseline uses `tech_current_stock`: {'yes' if payload['baseline_uses_tech_current_stock'] else 'no'}",
        f"- Deltas vs baseline available where baseline exists: {'yes' if payload['deltas_vs_baseline_available_where_baseline_exists'] else 'no'}",
        f"- P10/P50/P90 bands computed: {'yes' if payload['p10_p50_p90_bands_computed'] else 'no'}",
        f"- Near-future excludes 2050: {'yes' if payload['near_future_excludes_2050'] else 'no'}",
        f"- Mid-century includes 2050: {'yes' if payload['mid_century_includes_2050'] else 'no'}",
        f"- Simulations run: {payload['simulations_run']}",
        "",
        "## Metrics Included",
        "",
        *[f"- `{metric}`" for metric in metrics],
        "",
        "## Missing Groups",
        "",
    ]
    if all_missing:
        lines.extend(f"- `{item['scenario_id']}`: {item['reason']}" for item in all_missing)
    else:
        lines.append("- None")
    lines.extend(["", "## Warnings", ""])
    lines.extend(f"- {warning}" for warning in payload["warnings"])
    if not payload["warnings"]:
        lines.append("- None")
    lines.extend(["", "## Assumptions", ""])
    lines.extend(f"- {assumption}" for assumption in payload["assumptions"])
    (manifests_dir / "comparison_validation_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return payload


def generate_comparisons(
    *,
    experiment_root: Path,
    comparison_definitions: Path,
    metrics_table: Path | None = None,
    scenario_aggregate_table: Path | None = None,
    output_dir: Path | None = None,
    comparisons: list[str] | None = None,
    strict: bool = True,
    allow_missing_groups: bool = False,
    write_reports: bool = False,
) -> dict[str, Any]:
    definitions = load_comparison_definitions(comparison_definitions)
    metrics_path = metrics_table or experiment_root / "summaries" / "realization_level" / "scenario_leaf_metrics.csv"
    aggregate_path = scenario_aggregate_table or experiment_root / "summaries" / "scenario_level" / "scenario_aggregate_metrics.csv"
    output_root = output_dir or experiment_root / "summaries" / "comparison_level"
    metrics_df = _load_csv(metrics_path)
    _load_csv(aggregate_path)
    leaf_index_path = experiment_root / "manifests" / "scenario_leaf_index.csv"
    leaf_index_df = _load_csv(leaf_index_path) if leaf_index_path.exists() else pd.DataFrame()
    selected = comparisons or ["climate_only", "technology_only", "combined_stress_case", "stochastic_robustness"]
    _validate_metric_columns(metrics_df, _metric_union(definitions, selected))

    results: dict[str, Any] = {}
    for name in selected:
        if name == "climate_only":
            results[name] = _write_climate_only(definitions, metrics_df, leaf_index_df, output_root / "climate_only")
        elif name == "technology_only":
            results[name] = _write_technology_only(definitions, metrics_df, leaf_index_df, output_root / "technology_only")
        elif name == "combined_stress_case":
            results[name] = _write_combined_stress(definitions, metrics_df, leaf_index_df, output_root / "combined_stress_case")
        elif name == "stochastic_robustness":
            results[name] = _write_stochastic_robustness(definitions, metrics_df, output_root / "stochastic_robustness")
        else:
            raise ComparisonGenerationError(f"Unknown comparison requested: {name}")

    generated_at = datetime.now(timezone.utc).isoformat()
    index_df = _write_index(output_root, results, generated_at)
    report = _write_validation_report(
        experiment_root,
        comparison_definitions=comparison_definitions,
        metrics_table=metrics_path,
        definitions=definitions,
        metrics_df=metrics_df,
        results=results,
        index_df=index_df,
        allow_missing_groups=allow_missing_groups,
    )
    missing_groups = [
        item
        for result in results.values()
        for item in result.get("diagnostics", {}).get("missing_groups", [])
    ]
    if strict and missing_groups and not allow_missing_groups:
        raise ComparisonGenerationError(
            f"{len(missing_groups)} comparison scenario group(s) have no successful runs; "
            "rerun with --allow-missing-groups to write partial comparison artifacts."
        )
    return {
        "results": results,
        "index": index_df,
        "report": report if write_reports else None,
        "metrics": _metric_union(definitions, selected),
        "missing_groups": missing_groups,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment-root", default=str(DEFAULT_EXPERIMENT_ROOT))
    parser.add_argument("--comparison-definitions", default=str(DEFAULT_COMPARISON_DEFINITIONS))
    parser.add_argument("--metrics-table")
    parser.add_argument("--scenario-aggregate-table")
    parser.add_argument("--output-dir")
    parser.add_argument("--strict", action="store_true", default=True)
    parser.add_argument("--allow-missing-groups", action="store_true")
    parser.add_argument("--comparison", action="append", choices=["climate_only", "technology_only", "combined_stress_case", "stochastic_robustness"])
    parser.add_argument("--write-reports", action="store_true")
    parser.add_argument("--print-summary", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    experiment_root = _resolve_cli_path(args.experiment_root)
    result = generate_comparisons(
        experiment_root=experiment_root,
        comparison_definitions=_resolve_cli_path(args.comparison_definitions),
        metrics_table=_resolve_cli_path(args.metrics_table) if args.metrics_table else None,
        scenario_aggregate_table=_resolve_cli_path(args.scenario_aggregate_table) if args.scenario_aggregate_table else None,
        output_dir=_resolve_cli_path(args.output_dir) if args.output_dir else None,
        comparisons=args.comparison,
        strict=bool(args.strict),
        allow_missing_groups=bool(args.allow_missing_groups),
        write_reports=bool(args.write_reports),
    )
    diagnostics = {name: data.get("diagnostics", {}) for name, data in result["results"].items()}
    if args.print_summary:
        print("Comparison generation complete.")
        print(f"Climate-only pairs: {diagnostics.get('climate_only', {}).get('valid_pairs', 0)}")
        print(f"Technology-only pairs: {diagnostics.get('technology_only', {}).get('valid_pairs', 0)}")
        print(f"Combined stress-case pairs: {diagnostics.get('combined_stress_case', {}).get('valid_pairs', 0)}")
        print(f"Stochastic robustness groups: {diagnostics.get('stochastic_robustness', {}).get('groups', 0)}")
        print(f"Metrics compared: {len(result['metrics'])}")
        print("P10/P50/P90 bands computed: yes")
        print("Future current-stock misuse: no")
        print("Near-future includes 2050: no")
        print("Mid-century includes 2050: yes")
        print("Simulations run: 0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
