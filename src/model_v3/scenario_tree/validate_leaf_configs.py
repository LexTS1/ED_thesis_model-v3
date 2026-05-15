"""Validate generated executable configs for model_v3 scenario-tree leaves."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from . import paths
from .generate_leaf_configs import (
    INPUTS_MANIFEST_SCHEMA_VERSION,
    RUN_CONFIG_SCHEMA_VERSION,
    SEED_CONFIG_SCHEMA_VERSION,
    load_leaf_index,
    path_for_yaml,
    resolve_yaml_path,
    seed_index,
    validate_leaf_index,
)
from .technology_resolver import TechnologyResolutionError, resolve_technology_inputs
from .validate_scenario_tree import (
    DEFAULT_CONFIG_ROOT,
    REPO_ROOT,
    ScenarioLeaf,
    ScenarioTreeValidationError,
    ValidationResult,
    validate_scenario_tree,
)


DEFAULT_EXPERIMENT_ROOT = paths.get_experiment_root(REPO_ROOT)
DEFAULT_CLIMATE_PROCESSED_ROOT = REPO_ROOT / "inputs" / "climate" / "processed"
DEFAULT_BELGIAN_TECHNOLOGY_INPUTS = REPO_ROOT / "config" / "belgian_technology_inputs.yaml"
REQUIRED_TOP_LEVEL_SECTIONS = {
    "schema_version",
    "generated_by",
    "status",
    "scenario_leaf",
    "climate",
    "technology",
    "stochastic",
    "model_options",
    "output",
    "validation",
    "provenance",
}
FUTURE_PATHWAYS = {"rcp_2_6", "rcp_4_5", "rcp_8_5"}


class LeafConfigValidationError(ValueError):
    """Raised when generated scenario-leaf configs are incomplete or invalid."""


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise LeafConfigValidationError(f"Missing YAML file: {path}")
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict):
        raise LeafConfigValidationError(f"YAML file must contain a mapping: {path}")
    return data


def _parse_date(value: Any, label: str, errors: list[str]) -> datetime | None:
    if not isinstance(value, str):
        errors.append(f"{label} must be a YYYY-MM-DD string.")
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        errors.append(f"{label} must use YYYY-MM-DD format, found {value!r}.")
        return None


def _path_exists(path_text: str) -> bool:
    if not path_text:
        return False
    return resolve_yaml_path(path_text).exists()


def _validate_run_config(
    leaf: ScenarioLeaf,
    config: dict[str, Any],
    *,
    expected_path: Path,
    experiment_root: Path,
    result: ValidationResult,
    belgian_technology_inputs: Path,
    errors: list[str],
) -> dict[str, Any]:
    missing_sections = sorted(REQUIRED_TOP_LEVEL_SECTIONS.difference(config))
    if missing_sections:
        errors.append(f"{expected_path} missing top-level section(s): {', '.join(missing_sections)}.")
        return {}

    if config.get("schema_version") != RUN_CONFIG_SCHEMA_VERSION:
        errors.append(f"{expected_path} has unexpected schema_version {config.get('schema_version')!r}.")
    if config.get("status") != "configured_not_run":
        errors.append(f"{expected_path} status must be configured_not_run.")

    scenario_leaf = config.get("scenario_leaf", {})
    if scenario_leaf.get("id") != leaf.scenario_leaf_id:
        errors.append(f"{expected_path} scenario_leaf.id does not match the leaf index.")
    if scenario_leaf.get("scenario_id") != leaf.scenario_id:
        errors.append(f"{expected_path} scenario_leaf.scenario_id does not match the leaf index.")

    climate = config.get("climate", {})
    if climate.get("window_id") != leaf.climate_window_id:
        errors.append(f"{expected_path} climate.window_id does not match the leaf index.")
    if climate.get("pathway_id") != leaf.climate_pathway_id:
        errors.append(f"{expected_path} climate.pathway_id does not match the leaf index.")
    forcing_file = climate.get("forcing_file")
    if not isinstance(forcing_file, str) or not forcing_file:
        errors.append(f"{expected_path} climate.forcing_file must be a non-empty path.")
    else:
        forcing_path = resolve_yaml_path(forcing_file)
        if forcing_path.suffix.lower() != ".csv":
            errors.append(f"{expected_path} climate.forcing_file must reference a CSV: {forcing_file}")
        if not forcing_path.exists():
            errors.append(f"{expected_path} references a missing climate forcing CSV: {forcing_file}")

    if climate.get("source_file_window") != leaf.source_file_window:
        errors.append(f"{expected_path} climate.source_file_window does not match the leaf index.")
    if climate.get("analysis_start") != leaf.canonical_start:
        errors.append(f"{expected_path} climate.analysis_start does not match the leaf index.")
    if climate.get("analysis_end") != leaf.canonical_end:
        errors.append(f"{expected_path} climate.analysis_end does not match the leaf index.")

    technology = config.get("technology", {})
    technology_case_id = technology.get("case_id")
    window = result.metadata.climate_windows["climate_windows"][leaf.climate_window_id]
    try:
        resolve_technology_inputs(
            str(technology_case_id),
            result.metadata.technology_cases,
            belgian_technology_inputs,
            window_type=str(window.get("window_type")),
        )
    except TechnologyResolutionError as exc:
        errors.append(f"{expected_path} has invalid technology inputs: {exc}")

    belgian_path = technology.get("belgian_technology_inputs")
    if belgian_path != path_for_yaml(belgian_technology_inputs):
        errors.append(f"{expected_path} must reference {path_for_yaml(belgian_technology_inputs)}.")
    if not isinstance(belgian_path, str) or not _path_exists(belgian_path):
        errors.append(f"{expected_path} references a missing Belgian technology input YAML: {belgian_path}")

    stochastic = config.get("stochastic", {})
    expected_seed = seed_index(leaf.realization_id)
    if stochastic.get("realization_id") != leaf.realization_id:
        errors.append(f"{expected_path} stochastic.realization_id does not match the leaf index.")
    if stochastic.get("seed_index") != expected_seed or stochastic.get("seed_value") != expected_seed:
        errors.append(f"{expected_path} stochastic seed fields must equal {expected_seed}.")

    model_options = config.get("model_options", {})
    if model_options.get("execute_simulation") is not False:
        errors.append(f"{expected_path} model_options.execute_simulation must be false.")

    expected_paths = paths.paths_for_leaf(experiment_root, leaf.scenario_leaf_id)
    output = config.get("output", {})
    for key, expected_output_path in (
        ("run_dir", expected_paths["run_dir"]),
        ("outputs_dir", expected_paths["outputs_dir"]),
        ("logs_dir", expected_paths["logs_dir"]),
    ):
        value = output.get(key)
        if value != path_for_yaml(expected_output_path):
            errors.append(f"{expected_path} output.{key} must be {path_for_yaml(expected_output_path)!r}.")
        if not isinstance(value, str) or not _path_exists(value):
            errors.append(f"{expected_path} output.{key} does not exist: {value}")

    validation = config.get("validation", {})
    if validation.get("config_complete") is not True:
        errors.append(f"{expected_path} validation.config_complete must be true.")
    if validation.get("missing_required_inputs") != []:
        errors.append(f"{expected_path} validation.missing_required_inputs must be empty.")

    if leaf.climate_window_id == "baseline_1981_2005":
        if climate.get("pathway_id") != "historical":
            errors.append(f"{expected_path} baseline config must use historical climate pathway.")
        if technology.get("case_id") != "tech_current_stock":
            errors.append(f"{expected_path} baseline config must use tech_current_stock.")
    else:
        if climate.get("pathway_id") not in FUTURE_PATHWAYS:
            errors.append(f"{expected_path} future config must use an RCP pathway.")
        if technology.get("case_id") == "tech_current_stock":
            errors.append(f"{expected_path} future config must not use tech_current_stock.")

    if leaf.climate_window_id == "near_future_2030_2049" and climate.get("analysis_end") != "2049-12-31":
        errors.append(f"{expected_path} near-future canonical analysis must exclude 2050.")
    if leaf.climate_window_id == "mid_century_2050_2070" and climate.get("analysis_start") != "2050-01-01":
        errors.append(f"{expected_path} mid-century canonical analysis must include 2050.")

    return config


def _validate_inputs_manifest(
    leaf: ScenarioLeaf,
    manifest: dict[str, Any],
    *,
    expected_path: Path,
    config: dict[str, Any],
    errors: list[str],
) -> None:
    if manifest.get("schema_version") != INPUTS_MANIFEST_SCHEMA_VERSION:
        errors.append(f"{expected_path} has unexpected schema_version {manifest.get('schema_version')!r}.")
    if manifest.get("status") != "configured_not_run":
        errors.append(f"{expected_path} status must be configured_not_run.")
    if manifest.get("scenario_leaf_id") != leaf.scenario_leaf_id:
        errors.append(f"{expected_path} scenario_leaf_id does not match the leaf index.")
    climate = manifest.get("climate_forcing", {})
    if climate.get("file") != config.get("climate", {}).get("forcing_file"):
        errors.append(f"{expected_path} climate forcing file does not match run_config.yaml.")
    if climate.get("exists") is not True:
        errors.append(f"{expected_path} climate_forcing.exists must be true.")
    technology = manifest.get("technology", {})
    if technology.get("belgian_technology_inputs_exists") is not True:
        errors.append(f"{expected_path} technology.belgian_technology_inputs_exists must be true.")
    validation = manifest.get("validation", {})
    if validation.get("config_complete") is not True or validation.get("missing_required_inputs") != []:
        errors.append(f"{expected_path} validation must be complete with no missing inputs.")


def _validate_seed_config(
    leaf: ScenarioLeaf,
    seed_config: dict[str, Any],
    *,
    expected_path: Path,
    experiment_root: Path,
    errors: list[str],
) -> None:
    if seed_config.get("schema_version") != SEED_CONFIG_SCHEMA_VERSION:
        errors.append(f"{expected_path} has unexpected schema_version {seed_config.get('schema_version')!r}.")
    if seed_config.get("status") != "configured_not_run":
        errors.append(f"{expected_path} status must be configured_not_run.")
    expected_paths = paths.paths_for_leaf(experiment_root, leaf.scenario_leaf_id)
    expected_seed = seed_index(leaf.realization_id)
    expected_values = {
        "scenario_id": leaf.scenario_id,
        "scenario_leaf_id": leaf.scenario_leaf_id,
        "realization_id": leaf.realization_id,
        "seed_index": expected_seed,
        "seed_value": expected_seed,
        "run_config": path_for_yaml(expected_paths["run_config_path"]),
        "inputs_manifest": path_for_yaml(expected_paths["inputs_manifest_path"]),
        "output_dir": path_for_yaml(expected_paths["outputs_dir"]),
    }
    for key, expected_value in expected_values.items():
        if seed_config.get(key) != expected_value:
            errors.append(f"{expected_path} {key} must be {expected_value!r}.")


def _validate_canonical_windows(result: ValidationResult, errors: list[str]) -> None:
    windows = result.metadata.climate_windows["climate_windows"]
    intervals: list[tuple[str, datetime, datetime]] = []
    for window_id, window in windows.items():
        start = _parse_date(window.get("canonical_start"), f"{window_id}.canonical_start", errors)
        end = _parse_date(window.get("canonical_end"), f"{window_id}.canonical_end", errors)
        if start and end:
            intervals.append((window_id, start, end))
    for index, (first_id, first_start, first_end) in enumerate(intervals):
        for second_id, second_start, second_end in intervals[index + 1 :]:
            if first_start <= second_end and second_start <= first_end:
                errors.append(f"Canonical analysis windows overlap: {first_id} and {second_id}.")
    near = windows["near_future_2030_2049"]
    mid = windows["mid_century_2050_2070"]
    if near.get("canonical_end") != "2049-12-31":
        errors.append("near_future_2030_2049 must end on 2049-12-31.")
    if mid.get("canonical_start") != "2050-01-01":
        errors.append("mid_century_2050_2070 must start on 2050-01-01.")


def validate_leaf_configs(
    *,
    experiment_root: Path,
    config_root: Path,
    climate_processed_root: Path,
    belgian_technology_inputs: Path,
) -> dict[str, Any]:
    """Validate all generated configs listed in scenario_leaf_index.csv."""

    result = validate_scenario_tree(config_root=config_root)
    rows = load_leaf_index(experiment_root)
    index = validate_leaf_index(rows, result)
    errors: list[str] = []
    run_config_paths: set[Path] = set()
    climate_files: set[str] = set()
    baseline_count = 0
    future_count = 0
    simulations_run = 0

    if not climate_processed_root.exists():
        errors.append(f"Processed climate root does not exist: {climate_processed_root}")
    if not belgian_technology_inputs.exists():
        errors.append(f"Belgian technology input YAML does not exist: {belgian_technology_inputs}")

    for leaf in index.leaves:
        expected_paths = paths.paths_for_leaf(experiment_root, leaf.scenario_leaf_id)
        run_config_path = expected_paths["run_config_path"]
        if run_config_path in run_config_paths:
            errors.append(f"Duplicate run config path: {run_config_path}")
        run_config_paths.add(run_config_path)

        candidate_run_configs = list(expected_paths["run_dir"].glob("run_config.yaml")) if expected_paths["run_dir"].exists() else []
        if len(candidate_run_configs) != 1:
            errors.append(
                f"{expected_paths['run_dir']} must contain exactly one run_config.yaml, found "
                f"{len(candidate_run_configs)}."
            )
            continue

        config = _load_yaml(run_config_path)
        _validate_run_config(
            leaf,
            config,
            expected_path=run_config_path,
            experiment_root=experiment_root,
            result=result,
            belgian_technology_inputs=belgian_technology_inputs,
            errors=errors,
        )
        if config.get("model_options", {}).get("execute_simulation") is True:
            simulations_run += 1
        forcing_file = config.get("climate", {}).get("forcing_file")
        if isinstance(forcing_file, str) and forcing_file:
            climate_files.add(forcing_file)

        inputs_manifest_path = expected_paths["inputs_manifest_path"]
        if not inputs_manifest_path.exists():
            errors.append(f"Missing inputs_manifest.yaml: {inputs_manifest_path}")
        else:
            _validate_inputs_manifest(
                leaf,
                _load_yaml(inputs_manifest_path),
                expected_path=inputs_manifest_path,
                config=config,
                errors=errors,
            )

        seed_config_path = expected_paths["realization_config_path"]
        if not seed_config_path.exists():
            errors.append(f"Missing scenario-level seed config: {seed_config_path}")
        else:
            _validate_seed_config(
                leaf,
                _load_yaml(seed_config_path),
                expected_path=seed_config_path,
                experiment_root=experiment_root,
                errors=errors,
            )

        if leaf.climate_window_id == "baseline_1981_2005":
            baseline_count += 1
        else:
            future_count += 1

    _validate_canonical_windows(result, errors)

    if simulations_run:
        errors.append(f"Generated configs indicate simulations were run: {simulations_run}")

    if errors:
        message = "Scenario-leaf config validation failed:\n" + "\n".join(f" - {error}" for error in errors)
        raise LeafConfigValidationError(message)

    return {
        "scenario_leaves_checked": len(index.leaves),
        "run_configs_found": len(run_config_paths),
        "missing_climate_files": 0,
        "undefined_technology_cases": 0,
        "missing_belgian_technology_inputs": 0,
        "baseline_configs": baseline_count,
        "future_configs": future_count,
        "unique_climate_forcing_files": sorted(climate_files),
        "unique_climate_forcing_file_count": len(climate_files),
        "simulations_run": simulations_run,
        "warnings": index.warnings,
    }


def print_summary(summary: dict[str, Any]) -> None:
    """Print a compact success summary."""

    print("Scenario-leaf config validation passed.")
    print(f"Scenario leaves checked: {summary['scenario_leaves_checked']}")
    print(f"Run configs found: {summary['run_configs_found']}")
    print(f"Missing climate files: {summary['missing_climate_files']}")
    print(f"Undefined technology cases: {summary['undefined_technology_cases']}")
    print(f"Missing Belgian technology inputs: {summary['missing_belgian_technology_inputs']}")
    print(f"Baseline configs: {summary['baseline_configs']}")
    print(f"Future configs: {summary['future_configs']}")
    print(f"Simulations run: {summary['simulations_run']}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment-root", type=Path, default=DEFAULT_EXPERIMENT_ROOT)
    parser.add_argument("--config-root", type=Path, default=DEFAULT_CONFIG_ROOT)
    parser.add_argument("--climate-processed-root", type=Path, default=DEFAULT_CLIMATE_PROCESSED_ROOT)
    parser.add_argument("--belgian-technology-inputs", type=Path, default=DEFAULT_BELGIAN_TECHNOLOGY_INPUTS)
    parser.add_argument("--print-summary", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        summary = validate_leaf_configs(
            experiment_root=args.experiment_root,
            config_root=args.config_root,
            climate_processed_root=args.climate_processed_root,
            belgian_technology_inputs=args.belgian_technology_inputs,
        )
        if args.print_summary:
            print_summary(summary)
    except (LeafConfigValidationError, ScenarioTreeValidationError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
