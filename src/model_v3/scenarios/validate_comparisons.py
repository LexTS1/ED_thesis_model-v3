"""Validate analytical comparison artifacts for the model_v3 scenario tree."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Mapping

import pandas as pd
import yaml

from model_v3.scenarios.generate_comparisons import (
    DEFAULT_COMPARISON_DEFINITIONS,
    DEFAULT_CONFIG_ROOT,
    DEFAULT_EXPERIMENT_ROOT,
    REPO_ROOT,
    expand_metric_set,
    load_comparison_definitions,
    scenario_id_from,
)


class ComparisonValidationError(RuntimeError):
    """Raised when comparison validation cannot run."""


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
        raise ComparisonValidationError(f"Missing YAML file: {path}")
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict):
        raise ComparisonValidationError(f"YAML file must contain a mapping: {path}")
    return data


def _load_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise ComparisonValidationError(f"Missing CSV file: {path}")
    return pd.read_csv(path)


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if pd.isna(value):
        return False
    return str(value).strip().lower() in {"true", "1", "yes"}


def _technology_cases(config_root: Path) -> dict[str, Any]:
    data = _load_yaml(config_root / "technology_cases.yaml")
    return dict(data.get("technology_cases", {}))


def _climate_windows(config_root: Path) -> dict[str, Any]:
    data = _load_yaml(config_root / "climate_windows.yaml")
    return dict(data.get("climate_windows", {}))


def _all_metrics(definitions: Mapping[str, Any]) -> list[str]:
    metrics: list[str] = []
    for group in dict(definitions.get("comparison_groups", {})).values():
        for metric in expand_metric_set(definitions, group.get("metrics", [])):
            if metric not in metrics:
                metrics.append(metric)
    return metrics


def _validate_definition_references(
    definitions: Mapping[str, Any],
    metrics_df: pd.DataFrame,
    config_root: Path,
) -> list[str]:
    errors: list[str] = []
    for metric in _all_metrics(definitions):
        if metric not in metrics_df.columns:
            errors.append(f"Metric referenced by comparison definitions is absent from scenario_leaf_metrics.csv: {metric}")

    technologies = _technology_cases(config_root)
    windows = _climate_windows(config_root)
    groups = dict(definitions.get("comparison_groups", {}))

    def check_scenario(parts: Mapping[str, Any], context: str) -> None:
        window_id = str(parts.get("climate_window_id", ""))
        pathway_id = str(parts.get("climate_pathway_id", ""))
        technology_id = str(parts.get("technology_case_id", ""))
        if window_id not in windows:
            errors.append(f"{context} references unknown climate window: {window_id}")
        elif pathway_id not in [str(item) for item in dict(windows[window_id]).get("allowed_pathways", [])]:
            errors.append(f"{context} references invalid pathway {pathway_id} for climate window {window_id}.")
        if technology_id not in technologies:
            errors.append(f"{context} references unknown technology case: {technology_id}")

    climate = dict(groups.get("climate_only", {}))
    check_scenario(dict(climate.get("baseline_scenario", {})), "climate_only baseline_scenario")
    future_tech = str(climate.get("future_technology_case_id", ""))
    if future_tech != "tech_frozen_stock":
        errors.append("climate_only future_technology_case_id must be tech_frozen_stock.")
    for window in climate.get("include_climate_windows", []):
        for pathway in climate.get("include_pathways", []):
            check_scenario(
                {"climate_window_id": window, "climate_pathway_id": pathway, "technology_case_id": future_tech},
                "climate_only future_scenario",
            )

    technology = dict(groups.get("technology_only", {}))
    reference_tech = str(technology.get("reference_technology_case_id", ""))
    if reference_tech != "tech_frozen_stock":
        errors.append("technology_only reference_technology_case_id must be tech_frozen_stock.")
    for tech in [reference_tech, *technology.get("compared_technology_case_ids", [])]:
        if str(tech) not in technologies:
            errors.append(f"technology_only references unknown technology case: {tech}")
    for window in technology.get("include_climate_windows", []):
        for pathway in technology.get("include_pathways", []):
            for tech in [reference_tech, *technology.get("compared_technology_case_ids", [])]:
                check_scenario(
                    {"climate_window_id": window, "climate_pathway_id": pathway, "technology_case_id": tech},
                    "technology_only scenario",
                )

    stress = dict(groups.get("combined_stress_case", {}))
    check_scenario(dict(stress.get("baseline_scenario", {})), "combined_stress_case baseline_scenario")
    check_scenario(dict(stress.get("stress_scenario", {})), "combined_stress_case stress_scenario")
    return errors


def _require_columns(frame: pd.DataFrame, columns: list[str], context: str) -> list[str]:
    return [f"{context} missing required column: {column}" for column in columns if column not in frame.columns]


def _p_bands_present(frame: pd.DataFrame, context: str) -> list[str]:
    return _require_columns(frame, ["p10", "p50", "p90"], context)


def _duplicates(frame: pd.DataFrame, subset: list[str], context: str) -> list[str]:
    if frame.empty:
        return []
    missing = [column for column in subset if column not in frame.columns]
    if missing:
        return []
    duplicate_count = int(frame.duplicated(subset=subset).sum())
    if duplicate_count:
        return [f"{context} contains {duplicate_count} duplicate comparison row(s) for {subset}."]
    return []


def _validate_pct_zero_flags(frame: pd.DataFrame, metrics: list[str], left_prefix: str, right_prefix: str, flag_col: str, context: str) -> list[str]:
    errors: list[str] = []
    if frame.empty:
        return errors
    if flag_col not in frame.columns:
        return [f"{context} missing percentage-change zero-division diagnostic column: {flag_col}"]
    for _, row in frame.iterrows():
        zero_division = False
        for metric in metrics:
            right_col = f"{metric}_{right_prefix}_value"
            pct_col = f"{metric}_delta_pct"
            if right_col not in frame.columns or pct_col not in frame.columns:
                continue
            right_value = pd.to_numeric(pd.Series([row.get(right_col)]), errors="coerce").iloc[0]
            pct_value = pd.to_numeric(pd.Series([row.get(pct_col)]), errors="coerce").iloc[0]
            if pd.notna(right_value) and abs(float(right_value)) <= 1e-12 and pd.isna(pct_value):
                zero_division = True
        if zero_division and not _truthy(row.get(flag_col)):
            errors.append(f"{context} has a zero reference percentage delta without diagnostic flag.")
            break
    return errors


def _validate_same_realization(frame: pd.DataFrame, left_col: str, right_col: str, context: str) -> list[str]:
    errors: list[str] = []
    if frame.empty:
        return errors
    for _, row in frame.iterrows():
        realization = str(row.get("realization_id", ""))
        if not str(row.get(left_col, "")).endswith(realization):
            errors.append(f"{context} left leaf does not end with realization_id: {row.get(left_col)}")
            break
        if _truthy(row.get("comparison_valid")) and not str(row.get(right_col, "")).endswith(realization):
            errors.append(f"{context} reference leaf does not end with realization_id: {row.get(right_col)}")
            break
    return errors


def _split_semicolon_values(value: Any) -> set[str]:
    if value is None:
        return set()
    try:
        if pd.isna(value):
            return set()
    except TypeError:
        pass
    return {item.strip() for item in str(value).split(";") if item.strip()}


def _validate_annual_space_heating_comparison(frame: pd.DataFrame) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    required = [
        "scenario_id",
        "climate_window_id",
        "climate_pathway_id",
        "technology_case_id",
        "comparison_valid",
        "paired_realization_ids",
        "excluded_baseline_realization_ids",
        "excluded_future_realization_ids",
        "baseline_n_successful_realizations",
        "future_n_successful_realizations",
        "n_missing_realizations",
        "baseline_annual_useful_heating_kWh_mean",
        "annual_useful_heating_kWh_mean",
        "delta_annual_useful_heating_kWh_pct",
    ]
    errors.extend(_require_columns(frame, required, "annual_space_heating_demand_comparison"))
    if errors or frame.empty:
        return errors, warnings

    future_rows = frame[frame["scenario_id"].astype(str) != "baseline_1981_2005__historical__tech_current_stock"]
    for _, row in future_rows.iterrows():
        scenario_id = str(row.get("scenario_id", ""))
        if not _truthy(row.get("comparison_valid")):
            errors.append(f"Annual space-heating comparison has invalid or unpaired future row: {scenario_id}")
        if not _split_semicolon_values(row.get("paired_realization_ids")):
            errors.append(f"Annual space-heating comparison has no paired realization IDs: {scenario_id}")
        missing = pd.to_numeric(pd.Series([row.get("n_missing_realizations")]), errors="coerce").iloc[0]
        if pd.notna(missing) and int(missing) > 0:
            errors.append(f"Annual space-heating comparison has missing paired realization(s): {scenario_id}")
        if _split_semicolon_values(row.get("excluded_future_realization_ids")):
            errors.append(f"Annual space-heating comparison excluded future realization(s): {scenario_id}")
        if _split_semicolon_values(row.get("excluded_baseline_realization_ids")):
            warnings.append(f"Annual space-heating comparison excluded extra baseline realization(s): {scenario_id}")
    return errors, warnings


def _validate_useful_heating_hdd_sign(
    annual_heating: pd.DataFrame,
    annual_degree_days: pd.DataFrame,
) -> list[str]:
    errors: list[str] = []
    if annual_heating.empty or annual_degree_days.empty:
        return errors
    required_heating = {
        "scenario_id",
        "climate_window_id",
        "climate_pathway_id",
        "technology_case_id",
        "comparison_valid",
        "delta_annual_useful_heating_kWh_pct",
    }
    required_climate = {
        "scenario_id",
        "climate_window_id",
        "climate_pathway_id",
        "technology_case_id",
        "delta_HDD_18_pct",
    }
    if not required_heating.issubset(set(annual_heating.columns)) or not required_climate.issubset(set(annual_degree_days.columns)):
        return errors

    keys = ["scenario_id", "climate_window_id", "climate_pathway_id", "technology_case_id"]
    merged = annual_heating.merge(
        annual_degree_days[keys + ["delta_HDD_18_pct"]],
        on=keys,
        how="inner",
    )
    future = merged[
        (merged["scenario_id"].astype(str) != "baseline_1981_2005__historical__tech_current_stock")
        & (merged["technology_case_id"].astype(str) == "tech_frozen_stock")
        & merged["comparison_valid"].map(_truthy)
    ]
    for _, row in future.iterrows():
        heating_delta = pd.to_numeric(pd.Series([row.get("delta_annual_useful_heating_kWh_pct")]), errors="coerce").iloc[0]
        hdd_delta = pd.to_numeric(pd.Series([row.get("delta_HDD_18_pct")]), errors="coerce").iloc[0]
        if pd.notna(heating_delta) and pd.notna(hdd_delta) and float(hdd_delta) < -1e-6 and float(heating_delta) > 1e-6:
            errors.append(
                "Annual useful-heating delta is positive while HDD_18 decreases for climate-only frozen-stock row: "
                f"{row.get('scenario_id')}"
            )
    return errors


def _diagnostics_missing_groups_reported(output_root: Path) -> tuple[int, list[str]]:
    missing_count = 0
    errors: list[str] = []
    for path in [
        output_root / "climate_only" / "climate_only_diagnostics.yaml",
        output_root / "technology_only" / "technology_only_diagnostics.yaml",
        output_root / "combined_stress_case" / "combined_stress_case_diagnostics.yaml",
    ]:
        data = _load_yaml(path)
        missing = data.get("missing_groups", [])
        if missing is None:
            errors.append(f"Diagnostics file missing missing_groups list: {path}")
            continue
        missing_count += len(missing)
        for item in missing:
            if not isinstance(item, dict) or not item.get("scenario_id") or not item.get("reason"):
                errors.append(f"Malformed missing group diagnostic in {path}: {item}")
    return missing_count, errors


def validate_comparisons(
    *,
    experiment_root: Path,
    comparison_definitions: Path,
    config_root: Path = DEFAULT_CONFIG_ROOT,
) -> dict[str, Any]:
    definitions = load_comparison_definitions(comparison_definitions)
    metrics_path = experiment_root / "summaries" / "realization_level" / "scenario_leaf_metrics.csv"
    metrics_df = _load_csv(metrics_path)
    output_root = experiment_root / "summaries" / "comparison_level"
    metrics = _all_metrics(definitions)
    errors = _validate_definition_references(definitions, metrics_df, config_root)
    warnings: list[str] = []

    files = {
        "climate_abs": output_root / "climate_only" / "climate_only_absolute_metrics.csv",
        "climate_delta": output_root / "climate_only" / "climate_only_delta_vs_baseline.csv",
        "climate_pct": output_root / "climate_only" / "climate_only_percentage_change_vs_baseline.csv",
        "climate_bands": output_root / "climate_only" / "climate_only_uncertainty_bands.csv",
        "tech_abs": output_root / "technology_only" / "technology_only_absolute_metrics.csv",
        "tech_delta": output_root / "technology_only" / "technology_only_delta_vs_frozen_stock.csv",
        "tech_pct": output_root / "technology_only" / "technology_only_percentage_change_vs_frozen_stock.csv",
        "tech_bands": output_root / "technology_only" / "technology_only_uncertainty_bands.csv",
        "stress_abs": output_root / "combined_stress_case" / "combined_stress_case_absolute_metrics.csv",
        "stress_delta": output_root / "combined_stress_case" / "combined_stress_case_delta_vs_baseline.csv",
        "stress_pct": output_root / "combined_stress_case" / "combined_stress_case_percentage_change_vs_baseline.csv",
        "stress_bands": output_root / "combined_stress_case" / "combined_stress_case_uncertainty_bands.csv",
        "stochastic_bands": output_root / "stochastic_robustness" / "stochastic_uncertainty_bands.csv",
        "stochastic_spread": output_root / "stochastic_robustness" / "stochastic_spread_metrics.csv",
        "annual_heating": output_root / "annual_space_heating_demand_comparison.csv",
        "annual_degree_days": output_root / "annual_climate_degree_day_comparison.csv",
        "index_csv": output_root / "comparison_index.csv",
        "index_yaml": output_root / "comparison_index.yaml",
    }
    frames: dict[str, pd.DataFrame] = {}
    for key, path in files.items():
        try:
            if path.suffix == ".csv":
                frames[key] = _load_csv(path)
            else:
                _load_yaml(path)
        except ComparisonValidationError as exc:
            errors.append(str(exc))

    climate_required = [
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
    ]
    tech_required = [
        "comparison_name",
        "comparison_type",
        "compared_scenario_leaf_id",
        "reference_scenario_leaf_id",
        "compared_scenario_id",
        "reference_scenario_id",
        "realization_id",
        "climate_window_id",
        "climate_pathway_id",
        "compared_technology_case_id",
        "reference_technology_case_id",
        "reference_available",
        "comparison_valid",
    ]
    stress_required = [
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
    ]

    for key in ["climate_delta", "climate_pct"]:
        if key in frames:
            errors.extend(_require_columns(frames[key], climate_required, key))
    for key in ["tech_delta", "tech_pct"]:
        if key in frames:
            errors.extend(_require_columns(frames[key], tech_required, key))
    for key in ["stress_delta", "stress_pct"]:
        if key in frames:
            errors.extend(_require_columns(frames[key], stress_required, key))

    for key in ["climate_bands", "tech_bands", "stress_bands", "stochastic_bands"]:
        if key in frames:
            errors.extend(_p_bands_present(frames[key], key))

    if "climate_delta" in frames:
        climate_delta = frames["climate_delta"]
        if not climate_delta.empty and (climate_delta["technology_case_id"].astype(str) == "tech_current_stock").any():
            errors.append("Future climate-only comparison rows use tech_current_stock.")
        errors.extend(_validate_same_realization(climate_delta, "future_scenario_leaf_id", "baseline_scenario_leaf_id", "climate_only"))
        errors.extend(_duplicates(climate_delta, ["future_scenario_leaf_id", "baseline_scenario_leaf_id"], "climate_only"))
    if "tech_delta" in frames:
        tech_delta = frames["tech_delta"]
        if not tech_delta.empty:
            if (tech_delta["reference_technology_case_id"].astype(str) != "tech_frozen_stock").any():
                errors.append("Technology-only rows do not use tech_frozen_stock as reference.")
            mismatch = tech_delta[
                tech_delta["compared_scenario_leaf_id"].astype(str).str.rsplit("__", n=1).str[1]
                != tech_delta["reference_scenario_leaf_id"].astype(str).str.rsplit("__", n=1).str[1]
            ]
            if not mismatch.empty:
                errors.append("Technology-only rows do not match compared/reference by realization_id.")
        errors.extend(_validate_same_realization(tech_delta, "compared_scenario_leaf_id", "reference_scenario_leaf_id", "technology_only"))
        errors.extend(_duplicates(tech_delta, ["compared_scenario_leaf_id", "reference_scenario_leaf_id"], "technology_only"))
    if "stress_delta" in frames:
        stress_delta = frames["stress_delta"]
        errors.extend(_validate_same_realization(stress_delta, "stress_scenario_leaf_id", "baseline_scenario_leaf_id", "combined_stress_case"))
        errors.extend(_duplicates(stress_delta, ["stress_scenario_leaf_id", "baseline_scenario_leaf_id"], "combined_stress_case"))

    if "climate_pct" in frames:
        errors.extend(_validate_pct_zero_flags(frames["climate_pct"], metrics, "future", "baseline", "pct_change_division_by_zero", "climate percentage table"))
    if "tech_pct" in frames:
        errors.extend(_validate_pct_zero_flags(frames["tech_pct"], metrics, "compared", "reference", "pct_change_division_by_zero", "technology percentage table"))
    if "stress_pct" in frames:
        errors.extend(_validate_pct_zero_flags(frames["stress_pct"], metrics, "stress", "baseline", "pct_change_division_by_zero", "stress percentage table"))

    if "stochastic_spread" in frames:
        errors.extend(_require_columns(frames["stochastic_spread"], ["iqr", "p90_minus_p10", "coefficient_of_variation"], "stochastic_spread"))
    if "annual_heating" in frames:
        annual_errors, annual_warnings = _validate_annual_space_heating_comparison(frames["annual_heating"])
        errors.extend(annual_errors)
        warnings.extend(annual_warnings)
    if "annual_heating" in frames and "annual_degree_days" in frames:
        errors.extend(_validate_useful_heating_hdd_sign(frames["annual_heating"], frames["annual_degree_days"]))

    windows = _climate_windows(config_root)
    near = dict(windows.get("near_future_2030_2049", {}))
    mid = dict(windows.get("mid_century_2050_2070", {}))
    if str(near.get("canonical_end")) >= "2050-01-01":
        errors.append("near_future_2030_2049 canonical window includes 2050.")
    if str(mid.get("canonical_start")) > "2050-01-01":
        errors.append("mid_century_2050_2070 canonical window excludes 2050.")

    missing_groups, diagnostic_errors = _diagnostics_missing_groups_reported(output_root)
    errors.extend(diagnostic_errors)

    climate_pairs = int(frames.get("climate_delta", pd.DataFrame()).get("comparison_valid", pd.Series(dtype=bool)).map(_truthy).sum())
    technology_pairs = int(frames.get("tech_delta", pd.DataFrame()).get("comparison_valid", pd.Series(dtype=bool)).map(_truthy).sum())
    stress_pairs = int(frames.get("stress_delta", pd.DataFrame()).get("comparison_valid", pd.Series(dtype=bool)).map(_truthy).sum())
    stochastic_groups = (
        int(frames["stochastic_spread"]["scenario_id"].nunique())
        if "stochastic_spread" in frames and "scenario_id" in frames["stochastic_spread"]
        else 0
    )
    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "climate_only_pairs": climate_pairs,
        "technology_only_pairs": technology_pairs,
        "combined_stress_case_pairs": stress_pairs,
        "stochastic_robustness_groups": stochastic_groups,
        "metrics_checked": len(metrics),
        "missing_comparison_groups": int(missing_groups),
        "p10_p50_p90_bands_present": not any("p10" in error or "p50" in error or "p90" in error for error in errors),
        "future_current_stock_misuse": any("tech_current_stock" in error for error in errors),
        "warning_count": len(warnings),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment-root", default=str(DEFAULT_EXPERIMENT_ROOT))
    parser.add_argument("--comparison-definitions", default=str(DEFAULT_COMPARISON_DEFINITIONS))
    parser.add_argument("--config-root", default=str(DEFAULT_CONFIG_ROOT))
    parser.add_argument("--print-summary", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    result = validate_comparisons(
        experiment_root=_resolve_cli_path(args.experiment_root),
        comparison_definitions=_resolve_cli_path(args.comparison_definitions),
        config_root=_resolve_cli_path(args.config_root),
    )
    if args.print_summary:
        if result["ok"]:
            print("Comparison validation passed.")
            print(f"Climate-only pairs: {result['climate_only_pairs']}")
            print(f"Technology-only pairs: {result['technology_only_pairs']}")
            print(f"Combined stress-case pairs: {result['combined_stress_case_pairs']}")
            print(f"Stochastic robustness groups: {result['stochastic_robustness_groups']}")
            print(f"Metrics checked: {result['metrics_checked']}")
            print(f"Missing comparison groups: {result['missing_comparison_groups']}")
            print(f"P10/P50/P90 bands present: {'yes' if result['p10_p50_p90_bands_present'] else 'no'}")
            print(f"Future current-stock misuse: {'yes' if result['future_current_stock_misuse'] else 'no'}")
            print(f"Warnings: {result['warning_count']}")
            for warning in result.get("warnings", []):
                print(f"- warning: {warning}")
        else:
            print("Comparison validation failed.")
            for error in result["errors"]:
                print(f"- {error}")
            for warning in result.get("warnings", []):
                print(f"- warning: {warning}")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
