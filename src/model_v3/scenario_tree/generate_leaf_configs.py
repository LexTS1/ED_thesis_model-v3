"""Generate executable configuration files for model_v3 scenario-tree leaves."""

from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from . import paths
from .climate_forcing import (
    ClimateForcingResolutionError,
    get_climate_window,
    resolve_climate_forcing,
    window_label,
)
from .naming import parse_scenario_leaf_id
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

RUN_CONFIG_SCHEMA_VERSION = "model_v3.scenario_leaf_config.v1"
INPUTS_MANIFEST_SCHEMA_VERSION = "model_v3.inputs_manifest.v1"
SEED_CONFIG_SCHEMA_VERSION = "model_v3.scenario_seed_config.v1"
REPORT_SCHEMA_VERSION = "model_v3.config_validation_report.v1"
GENERATED_BY = "Phase 3 - scenario leaf config generator"
STATUS_CONFIGURED = "configured_not_run"
STATUS_INCOMPLETE = "configured_with_missing_inputs"


class LeafConfigGenerationError(ValueError):
    """Raised when leaf config generation cannot complete safely."""


@dataclass(frozen=True)
class LeafIndex:
    """Validated Phase 2 leaf-index content."""

    rows: list[dict[str, str]]
    leaves: list[ScenarioLeaf]
    warnings: list[str]


@dataclass(frozen=True)
class PreparedLeaf:
    """One fully resolved scenario leaf ready for YAML generation."""

    leaf: ScenarioLeaf
    window: dict[str, Any]
    climate_forcing_file: Path | None
    climate_forcing_exists: bool
    technology: dict[str, Any]
    missing_required_inputs: list[str]


def path_for_yaml(path: Path) -> str:
    """Return a stable path string for generated YAML."""

    if not path.is_absolute():
        return path.as_posix()
    try:
        return path.resolve().relative_to(REPO_ROOT.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def resolve_yaml_path(path_text: str) -> Path:
    """Resolve a generated YAML path string against the repository root."""

    path = Path(path_text)
    if path.is_absolute():
        return path
    return REPO_ROOT / path


def seed_index(realization_id: str) -> int:
    """Return the integer seed index encoded in a realization ID."""

    return int(realization_id.removeprefix("seed_"))


def _parse_bool(value: str | bool | None) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return True
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    raise argparse.ArgumentTypeError(f"Expected a boolean value, found {value!r}.")


def _load_yaml_if_exists(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise LeafConfigGenerationError(f"Existing YAML file must contain a mapping: {path}")
    return data


def _write_yaml(path: Path, data: dict[str, Any], *, dry_run: bool) -> str:
    if dry_run:
        return "would_create" if not path.exists() else "would_update"
    existed = path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(data, handle, sort_keys=False)
    return "updated" if existed else "created"


def _mkdir(path: Path, *, dry_run: bool) -> str:
    if dry_run:
        return "would_exist" if path.exists() else "would_create"
    existed = path.exists()
    path.mkdir(parents=True, exist_ok=True)
    return "exists" if existed else "created"


def _is_phase2_placeholder(data: dict[str, Any] | None, kind: str) -> bool:
    if data is None:
        return True
    if kind == "run_config":
        return data.get("created_by") == "model_v3_scenario_tree_phase_2" and data.get("status") == "not_run"
    if kind == "inputs_manifest":
        return data.get("status") == "metadata_only" and "expected_future_input_hooks" in data
    if kind == "seed_config":
        return data.get("status") == "placeholder" and "deterministic_seed_rule" in data
    return False


def _is_phase3_managed(data: dict[str, Any] | None, kind: str) -> bool:
    if data is None:
        return True
    if kind == "run_config":
        return data.get("schema_version") == RUN_CONFIG_SCHEMA_VERSION and data.get("generated_by") == GENERATED_BY
    if kind == "inputs_manifest":
        return data.get("schema_version") == INPUTS_MANIFEST_SCHEMA_VERSION
    if kind == "seed_config":
        return data.get("schema_version") == SEED_CONFIG_SCHEMA_VERSION
    return False


def _check_write_allowed(path: Path, kind: str, *, overwrite: bool) -> None:
    data = _load_yaml_if_exists(path)
    if overwrite or _is_phase2_placeholder(data, kind) or _is_phase3_managed(data, kind):
        return
    raise LeafConfigGenerationError(
        f"Refusing to overwrite existing {kind.replace('_', ' ')} that is not a recognized Phase 2 "
        f"placeholder or Phase 3 generated file: {path}. Re-run with --overwrite if this is intentional."
    )


def load_leaf_index(experiment_root: Path) -> list[dict[str, str]]:
    """Load Phase 2 scenario_leaf_index.csv rows."""

    index_path = paths.get_manifest_dir(experiment_root) / "scenario_leaf_index.csv"
    if not index_path.exists():
        raise LeafConfigGenerationError(f"Missing Phase 2 scenario leaf index: {index_path}")
    with index_path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise LeafConfigGenerationError(f"Scenario leaf index is empty: {index_path}")
    return rows


def validate_leaf_index(rows: list[dict[str, str]], result: ValidationResult) -> LeafIndex:
    """Validate all Phase 2 leaf-index rows against the scenario-tree contract."""

    required_fields = {
        "scenario_leaf_id",
        "scenario_id",
        "climate_window_id",
        "climate_pathway_id",
        "technology_case_id",
        "realization_id",
        "canonical_start",
        "canonical_end",
        "source_file_window",
        "run_config_path",
        "inputs_manifest_path",
        "outputs_dir",
        "logs_dir",
        "realization_config_path",
    }
    errors: list[str] = []
    warnings: list[str] = []
    expected = {leaf.scenario_leaf_id: leaf for leaf in result.scenario_leaves}
    seen_leaf_ids: set[str] = set()
    seen_config_paths: set[str] = set()
    leaves: list[ScenarioLeaf] = []

    for row_number, row in enumerate(rows, start=2):
        missing_fields = sorted(required_fields.difference(row))
        if missing_fields:
            errors.append(f"Row {row_number} missing required field(s): {', '.join(missing_fields)}.")
            continue

        leaf_id = row["scenario_leaf_id"]
        if leaf_id in seen_leaf_ids:
            errors.append(f"Duplicate scenario_leaf_id in scenario leaf index: {leaf_id}.")
            continue
        seen_leaf_ids.add(leaf_id)

        try:
            parsed = parse_scenario_leaf_id(leaf_id)
        except ValueError as exc:
            errors.append(f"Invalid scenario_leaf_id in row {row_number}: {exc}")
            continue

        for field_name, expected_value in parsed.items():
            if row[field_name] != expected_value:
                errors.append(
                    f"Row {row_number} {field_name}={row[field_name]!r} does not match parsed "
                    f"scenario_leaf_id value {expected_value!r}."
                )

        expected_leaf = expected.get(leaf_id)
        if expected_leaf is None:
            errors.append(f"Scenario leaf index contains leaf not permitted by Phase 1 metadata: {leaf_id}.")
            continue

        for field_name in (
            "scenario_id",
            "climate_window_id",
            "climate_pathway_id",
            "technology_case_id",
            "realization_id",
            "canonical_start",
            "canonical_end",
            "source_file_window",
        ):
            if row[field_name] != getattr(expected_leaf, field_name):
                errors.append(
                    f"Row {row_number} {field_name}={row[field_name]!r} does not match validated "
                    f"metadata value {getattr(expected_leaf, field_name)!r}."
                )

        expected_paths = paths.paths_for_leaf(Path("experiments/scenario_tree"), leaf_id)
        for field_name, expected_path_key in (
            ("run_config_path", "run_config_path"),
            ("inputs_manifest_path", "inputs_manifest_path"),
            ("outputs_dir", "outputs_dir"),
            ("logs_dir", "logs_dir"),
            ("realization_config_path", "realization_config_path"),
        ):
            configured_path = Path(row[field_name])
            expected_suffix = expected_paths[expected_path_key]
            if configured_path.name != expected_suffix.name and field_name.endswith("_path"):
                errors.append(f"Row {row_number} has invalid {field_name}: {row[field_name]!r}.")

        config_path = row["run_config_path"]
        if config_path in seen_config_paths:
            errors.append(f"Duplicate run_config_path in scenario leaf index: {config_path}.")
        seen_config_paths.add(config_path)
        leaves.append(expected_leaf)

    if len(rows) != len(result.scenario_leaves):
        warnings.append(
            f"Scenario leaf index contains {len(rows)} rows while the validated scenario tree contains "
            f"{len(result.scenario_leaves)} leaves; generation will cover the index rows only."
        )

    if errors:
        message = "Scenario leaf index validation failed:\n" + "\n".join(f" - {error}" for error in errors)
        raise LeafConfigGenerationError(message)
    return LeafIndex(rows=rows, leaves=leaves, warnings=warnings)


def _select_leaves(leaves: list[ScenarioLeaf], max_leaves: int | None) -> list[ScenarioLeaf]:
    if max_leaves is None:
        return leaves
    if max_leaves < 0:
        raise LeafConfigGenerationError("--max-leaves must be non-negative.")
    return leaves[:max_leaves]


def prepare_leaf(
    leaf: ScenarioLeaf,
    *,
    result: ValidationResult,
    climate_processed_root: Path,
    belgian_technology_inputs: Path,
    allow_missing_climate: bool,
    allow_missing_technology_inputs: bool,
) -> PreparedLeaf:
    """Resolve required inputs for one scenario leaf."""

    window = get_climate_window(leaf.climate_window_id, result.metadata.climate_windows)
    missing: list[str] = []

    climate_file: Path | None
    climate_exists = False
    try:
        climate_file = resolve_climate_forcing(
            leaf.climate_window_id,
            leaf.climate_pathway_id,
            result.metadata.climate_windows,
            climate_processed_root,
        )
        climate_exists = resolve_yaml_path(path_for_yaml(climate_file)).exists()
    except ClimateForcingResolutionError as exc:
        if not allow_missing_climate:
            raise
        climate_file = None
        missing.append(f"climate_forcing: {exc}")

    technology = resolve_technology_inputs(
        leaf.technology_case_id,
        result.metadata.technology_cases,
        belgian_technology_inputs,
        window_type=str(window.get("window_type")),
        allow_missing_technology_inputs=allow_missing_technology_inputs,
    )
    if not technology["belgian_technology_inputs_exists"]:
        missing.append(f"belgian_technology_inputs: {belgian_technology_inputs}")

    return PreparedLeaf(
        leaf=leaf,
        window=window,
        climate_forcing_file=climate_file,
        climate_forcing_exists=climate_exists,
        technology=technology,
        missing_required_inputs=missing,
    )


def _temporal_policy(result: ValidationResult) -> dict[str, Any]:
    policy = result.metadata.climate_windows["temporal_window_policy"]
    return {
        "raw_processed_files_may_overlap": policy["raw_processed_files_may_overlap"],
        "canonical_analysis_windows_must_overlap": policy["canonical_analysis_windows_must_overlap"],
        "year_2050_assignment": policy["year_2050_assignment"],
    }


def _config_status(prepared: PreparedLeaf) -> str:
    return STATUS_INCOMPLETE if prepared.missing_required_inputs else STATUS_CONFIGURED


def _climate_file_text(prepared: PreparedLeaf) -> str:
    if prepared.climate_forcing_file is None:
        return ""
    return path_for_yaml(prepared.climate_forcing_file)


def run_config_payload(
    prepared: PreparedLeaf,
    *,
    experiment_root: Path,
    config_root: Path,
    belgian_technology_inputs: Path,
    cohort_size: int,
    result: ValidationResult,
    generated_at_utc: str,
) -> dict[str, Any]:
    """Build one executable leaf run config document."""

    leaf = prepared.leaf
    resolved_paths = paths.paths_for_leaf(experiment_root, leaf.scenario_leaf_id)
    seed = seed_index(leaf.realization_id)
    return {
        "schema_version": RUN_CONFIG_SCHEMA_VERSION,
        "generated_by": GENERATED_BY,
        "status": _config_status(prepared),
        "scenario_leaf": {
            "id": leaf.scenario_leaf_id,
            "scenario_id": leaf.scenario_id,
            "climate_window_id": leaf.climate_window_id,
            "climate_pathway_id": leaf.climate_pathway_id,
            "technology_case_id": leaf.technology_case_id,
            "realization_id": leaf.realization_id,
        },
        "climate": {
            "window_id": leaf.climate_window_id,
            "window_label": window_label(leaf.climate_window_id, prepared.window),
            "pathway_id": leaf.climate_pathway_id,
            "forcing_file": _climate_file_text(prepared),
            "source_file_window": leaf.source_file_window,
            "analysis_start": leaf.canonical_start,
            "analysis_end": leaf.canonical_end,
            "inclusive_dates": True,
            "temporal_policy": _temporal_policy(result),
        },
        "technology": {
            "case_id": leaf.technology_case_id,
            "metadata_file": path_for_yaml(config_root / "technology_cases.yaml"),
            "belgian_technology_inputs": path_for_yaml(belgian_technology_inputs),
        },
        "stochastic": {
            "realization_id": leaf.realization_id,
            "seed_index": seed,
            "seed_value": seed,
            "cohort_size": cohort_size,
            "cohort_generation": "deferred_to_simulation_phase",
        },
        "model_options": {
            "run_mode": "scenario_leaf",
            "execute_simulation": False,
            "use_stochastic_cohort": True,
            "use_climate_forcing": True,
            "use_technology_case": True,
            "write_outputs": True,
        },
        "output": {
            "run_dir": path_for_yaml(resolved_paths["run_dir"]),
            "outputs_dir": path_for_yaml(resolved_paths["outputs_dir"]),
            "logs_dir": path_for_yaml(resolved_paths["logs_dir"]),
        },
        "validation": {
            "config_complete": not prepared.missing_required_inputs,
            "missing_required_inputs": list(prepared.missing_required_inputs),
        },
        "provenance": {
            "phase": 3,
            "scenario_tree_schema": path_for_yaml(config_root / "scenario_tree_schema.yaml"),
            "climate_windows": path_for_yaml(config_root / "climate_windows.yaml"),
            "technology_cases": path_for_yaml(config_root / "technology_cases.yaml"),
            "realization_policy": path_for_yaml(config_root / "realization_policy.yaml"),
            "scenario_leaf_index": path_for_yaml(paths.get_manifest_dir(experiment_root) / "scenario_leaf_index.csv"),
            "generated_at_utc": generated_at_utc,
        },
    }


def inputs_manifest_payload(
    prepared: PreparedLeaf,
    *,
    config_root: Path,
    belgian_technology_inputs: Path,
    cohort_size: int,
) -> dict[str, Any]:
    """Build one leaf inputs manifest."""

    leaf = prepared.leaf
    seed = seed_index(leaf.realization_id)
    return {
        "schema_version": INPUTS_MANIFEST_SCHEMA_VERSION,
        "status": _config_status(prepared),
        "scenario_leaf_id": leaf.scenario_leaf_id,
        "scenario_id": leaf.scenario_id,
        "climate_forcing": {
            "file": _climate_file_text(prepared),
            "exists": prepared.climate_forcing_exists,
            "climate_window_id": leaf.climate_window_id,
            "climate_pathway_id": leaf.climate_pathway_id,
            "source_file_window": leaf.source_file_window,
            "analysis_start": leaf.canonical_start,
            "analysis_end": leaf.canonical_end,
        },
        "technology": {
            "case_id": leaf.technology_case_id,
            "metadata_file": path_for_yaml(config_root / "technology_cases.yaml"),
            "belgian_technology_inputs": path_for_yaml(belgian_technology_inputs),
            "belgian_technology_inputs_exists": prepared.technology["belgian_technology_inputs_exists"],
        },
        "stochastic": {
            "realization_id": leaf.realization_id,
            "seed_index": seed,
            "seed_value": seed,
            "cohort_size": cohort_size,
        },
        "validation": {
            "config_complete": not prepared.missing_required_inputs,
            "missing_required_inputs": list(prepared.missing_required_inputs),
        },
    }


def seed_config_payload(prepared: PreparedLeaf, *, experiment_root: Path) -> dict[str, Any]:
    """Build one scenario-level seed config pointing at the executable run config."""

    leaf = prepared.leaf
    resolved_paths = paths.paths_for_leaf(experiment_root, leaf.scenario_leaf_id)
    seed = seed_index(leaf.realization_id)
    return {
        "schema_version": SEED_CONFIG_SCHEMA_VERSION,
        "status": _config_status(prepared),
        "scenario_id": leaf.scenario_id,
        "scenario_leaf_id": leaf.scenario_leaf_id,
        "realization_id": leaf.realization_id,
        "seed_index": seed,
        "seed_value": seed,
        "run_config": path_for_yaml(resolved_paths["run_config_path"]),
        "inputs_manifest": path_for_yaml(resolved_paths["inputs_manifest_path"]),
        "output_dir": path_for_yaml(resolved_paths["outputs_dir"]),
    }


def _preflight_writes(prepared_leaves: list[PreparedLeaf], *, experiment_root: Path, overwrite: bool) -> None:
    for prepared in prepared_leaves:
        resolved_paths = paths.paths_for_leaf(experiment_root, prepared.leaf.scenario_leaf_id)
        _check_write_allowed(resolved_paths["run_config_path"], "run_config", overwrite=overwrite)
        _check_write_allowed(resolved_paths["inputs_manifest_path"], "inputs_manifest", overwrite=overwrite)
        _check_write_allowed(resolved_paths["realization_config_path"], "seed_config", overwrite=overwrite)


def _increment(counter: dict[str, int], key: str) -> None:
    counter[key] = counter.get(key, 0) + 1


def _write_leaf_files(
    prepared_leaves: list[PreparedLeaf],
    *,
    experiment_root: Path,
    config_root: Path,
    belgian_technology_inputs: Path,
    cohort_size: int,
    result: ValidationResult,
    generated_at_utc: str,
    dry_run: bool,
) -> dict[str, dict[str, int]]:
    directory_actions: dict[str, int] = {}
    file_actions: dict[str, int] = {}
    for prepared in prepared_leaves:
        resolved_paths = paths.paths_for_leaf(experiment_root, prepared.leaf.scenario_leaf_id)
        for directory in (
            resolved_paths["run_dir"],
            resolved_paths["outputs_dir"],
            resolved_paths["logs_dir"],
            resolved_paths["scenario_config_dir"],
        ):
            _increment(directory_actions, _mkdir(directory, dry_run=dry_run))

        documents = [
            (
                resolved_paths["run_config_path"],
                run_config_payload(
                    prepared,
                    experiment_root=experiment_root,
                    config_root=config_root,
                    belgian_technology_inputs=belgian_technology_inputs,
                    cohort_size=cohort_size,
                    result=result,
                    generated_at_utc=generated_at_utc,
                ),
            ),
            (
                resolved_paths["inputs_manifest_path"],
                inputs_manifest_payload(
                    prepared,
                    config_root=config_root,
                    belgian_technology_inputs=belgian_technology_inputs,
                    cohort_size=cohort_size,
                ),
            ),
            (
                resolved_paths["realization_config_path"],
                seed_config_payload(prepared, experiment_root=experiment_root),
            ),
        ]
        for path, payload in documents:
            _increment(file_actions, _write_yaml(path, payload, dry_run=dry_run))

    return {"directory_actions": directory_actions, "file_actions": file_actions}


def _report_summary(
    *,
    prepared_leaves: list[PreparedLeaf],
    index: LeafIndex,
    result: ValidationResult,
    generated_at_utc: str,
    write_actions: dict[str, dict[str, int]],
    dry_run: bool,
) -> dict[str, Any]:
    climate_files = sorted({_climate_file_text(prepared) for prepared in prepared_leaves if prepared.climate_forcing_file})
    baseline_count = sum(1 for prepared in prepared_leaves if prepared.window.get("window_type") == "baseline")
    future_count = sum(1 for prepared in prepared_leaves if prepared.window.get("window_type") == "future")
    technology_case_ids = set(result.metadata.technology_cases["technology_cases"])
    undefined_tech = sorted(
        {prepared.leaf.technology_case_id for prepared in prepared_leaves}.difference(technology_case_ids)
    )
    missing_climate = [
        prepared.leaf.scenario_leaf_id for prepared in prepared_leaves if not prepared.climate_forcing_exists
    ]
    missing_technology_inputs = [
        prepared.leaf.scenario_leaf_id
        for prepared in prepared_leaves
        if not prepared.technology["belgian_technology_inputs_exists"]
    ]
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "generation_timestamp_utc": generated_at_utc,
        "dry_run": dry_run,
        "scenario_leaves_checked": len(prepared_leaves),
        "executable_configs_generated": 0 if dry_run else len(prepared_leaves),
        "baseline_configs": baseline_count,
        "future_configs": future_count,
        "unique_climate_forcing_files_referenced": climate_files,
        "unique_climate_forcing_file_count": len(climate_files),
        "checks": {
            "all_referenced_climate_files_exist": not missing_climate,
            "all_technology_cases_defined": not undefined_tech,
            "belgian_technology_input_yaml_exists": not missing_technology_inputs,
            "baseline_and_future_cases_separated": all(
                (
                    prepared.window.get("window_type") == "baseline"
                    and prepared.leaf.climate_pathway_id == "historical"
                    and prepared.leaf.technology_case_id == "tech_current_stock"
                )
                or (
                    prepared.window.get("window_type") == "future"
                    and prepared.leaf.climate_pathway_id in {"rcp_2_6", "rcp_4_5", "rcp_8_5"}
                    and prepared.leaf.technology_case_id != "tech_current_stock"
                )
                for prepared in prepared_leaves
            ),
            "near_future_canonical_excludes_2050": result.metadata.climate_windows["climate_windows"][
                "near_future_2030_2049"
            ]["canonical_end"]
            == "2049-12-31",
            "mid_century_canonical_includes_2050": result.metadata.climate_windows["climate_windows"][
                "mid_century_2050_2070"
            ]["canonical_start"]
            == "2050-01-01",
            "simulations_run": 0,
            "duplicate_config_paths": 0,
        },
        "missing_climate_files": missing_climate,
        "undefined_technology_cases": undefined_tech,
        "missing_belgian_technology_inputs": missing_technology_inputs,
        "directory_actions": write_actions["directory_actions"],
        "file_actions": write_actions["file_actions"],
        "warnings": list(index.warnings),
        "assumptions": [
            "Processed climate forcing files are resolved from explicit metadata when present, otherwise by "
            "pathway/window/source-window tokens and sidecar metadata.",
            "Seed value equals the integer suffix of realization_id.",
            "Household cohorts remain deferred to the simulation phase.",
            "No residential demand simulations are executed by this generator.",
        ],
    }


def _write_report(summary: dict[str, Any], experiment_root: Path, *, dry_run: bool) -> tuple[Path, Path]:
    manifest_dir = paths.get_manifest_dir(experiment_root)
    md_path = manifest_dir / "config_validation_report.md"
    yaml_path = manifest_dir / "config_validation_report.yaml"
    if dry_run:
        return md_path, yaml_path

    manifest_dir.mkdir(parents=True, exist_ok=True)
    climate_lines = "\n".join(f"- `{item}`" for item in summary["unique_climate_forcing_files_referenced"])
    warning_lines = "\n".join(f"- {item}" for item in summary["warnings"]) or "- None"
    assumption_lines = "\n".join(f"- {item}" for item in summary["assumptions"])
    checks = summary["checks"]
    check_lines = "\n".join(
        f"- {key.replace('_', ' ')}: {value}" for key, value in checks.items()
    )
    content = f"""# Scenario-Leaf Config Validation Report

Generated at UTC: `{summary['generation_timestamp_utc']}`

## Counts

- Scenario leaves checked: {summary['scenario_leaves_checked']}
- Executable configs generated: {summary['executable_configs_generated']}
- Baseline configs: {summary['baseline_configs']}
- Future configs: {summary['future_configs']}
- Unique climate forcing files referenced: {summary['unique_climate_forcing_file_count']}

## Climate Forcing Files

{climate_lines}

## Checks

{check_lines}

## Warnings

{warning_lines}

## Assumptions

{assumption_lines}
"""
    md_path.write_text(content, encoding="utf-8")
    with yaml_path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(summary, handle, sort_keys=False)
    return md_path, yaml_path


def generate_leaf_configs(
    *,
    config_root: Path,
    experiment_root: Path,
    climate_processed_root: Path,
    belgian_technology_inputs: Path,
    cohort_size: int,
    write_report: bool,
    dry_run: bool,
    overwrite: bool,
    max_leaves: int | None,
    allow_missing_climate: bool,
    allow_missing_technology_inputs: bool,
) -> dict[str, Any]:
    """Generate executable configs for scenario leaves listed in the Phase 2 index."""

    if cohort_size <= 0:
        raise LeafConfigGenerationError("--cohort-size must be a positive integer.")
    result = validate_scenario_tree(config_root=config_root)
    rows = load_leaf_index(experiment_root)
    index = validate_leaf_index(rows, result)
    leaves = _select_leaves(index.leaves, max_leaves)
    generated_at_utc = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    prepared_leaves = [
        prepare_leaf(
            leaf,
            result=result,
            climate_processed_root=climate_processed_root,
            belgian_technology_inputs=belgian_technology_inputs,
            allow_missing_climate=allow_missing_climate,
            allow_missing_technology_inputs=allow_missing_technology_inputs,
        )
        for leaf in leaves
    ]
    if any(prepared.missing_required_inputs for prepared in prepared_leaves) and not (
        allow_missing_climate or allow_missing_technology_inputs
    ):
        raise LeafConfigGenerationError("Missing required inputs were detected.")

    _preflight_writes(prepared_leaves, experiment_root=experiment_root, overwrite=overwrite)
    write_actions = _write_leaf_files(
        prepared_leaves,
        experiment_root=experiment_root,
        config_root=config_root,
        belgian_technology_inputs=belgian_technology_inputs,
        cohort_size=cohort_size,
        result=result,
        generated_at_utc=generated_at_utc,
        dry_run=dry_run,
    )
    summary = _report_summary(
        prepared_leaves=prepared_leaves,
        index=index,
        result=result,
        generated_at_utc=generated_at_utc,
        write_actions=write_actions,
        dry_run=dry_run,
    )
    report_paths = (None, None)
    if write_report:
        report_paths = _write_report(summary, experiment_root, dry_run=dry_run)
    summary["report_markdown"] = path_for_yaml(report_paths[0]) if report_paths[0] is not None else None
    summary["report_yaml"] = path_for_yaml(report_paths[1]) if report_paths[1] is not None else None
    return summary


def print_summary(summary: dict[str, Any]) -> None:
    """Print a concise generation summary."""

    action = "would be configured" if summary["dry_run"] else "configured"
    print(f"Scenario-leaf configs {action}.")
    print(f"Scenario leaves checked: {summary['scenario_leaves_checked']}")
    print(f"Executable configs generated: {summary['executable_configs_generated']}")
    print(f"Baseline configs: {summary['baseline_configs']}")
    print(f"Future configs: {summary['future_configs']}")
    print(f"Unique climate forcing files: {summary['unique_climate_forcing_file_count']}")
    print(f"All referenced climate files exist: {summary['checks']['all_referenced_climate_files_exist']}")
    print(f"All technology cases defined: {summary['checks']['all_technology_cases_defined']}")
    print(f"Belgian technology inputs exist: {summary['checks']['belgian_technology_input_yaml_exists']}")
    print("Simulations run: 0")
    if summary.get("report_markdown"):
        print(f"Report: {summary['report_markdown']}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-root", type=Path, default=DEFAULT_CONFIG_ROOT)
    parser.add_argument("--experiment-root", type=Path, default=DEFAULT_EXPERIMENT_ROOT)
    parser.add_argument("--climate-processed-root", type=Path, default=DEFAULT_CLIMATE_PROCESSED_ROOT)
    parser.add_argument("--belgian-technology-inputs", type=Path, default=DEFAULT_BELGIAN_TECHNOLOGY_INPUTS)
    parser.add_argument("--cohort-size", type=int, required=True)
    parser.add_argument("--write-report", action="store_true")
    parser.add_argument("--print-summary", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--max-leaves", type=int, default=None)
    parser.add_argument(
        "--allow-missing-climate",
        nargs="?",
        const=True,
        default=False,
        type=_parse_bool,
        help="Allow incomplete configs when climate forcing cannot be resolved. Defaults to false.",
    )
    parser.add_argument(
        "--allow-missing-technology-inputs",
        nargs="?",
        const=True,
        default=False,
        type=_parse_bool,
        help="Allow incomplete configs when Belgian technology inputs are missing. Defaults to false.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        summary = generate_leaf_configs(
            config_root=args.config_root,
            experiment_root=args.experiment_root,
            climate_processed_root=args.climate_processed_root,
            belgian_technology_inputs=args.belgian_technology_inputs,
            cohort_size=args.cohort_size,
            write_report=args.write_report,
            dry_run=args.dry_run,
            overwrite=args.overwrite,
            max_leaves=args.max_leaves,
            allow_missing_climate=args.allow_missing_climate,
            allow_missing_technology_inputs=args.allow_missing_technology_inputs,
        )
        if args.print_summary or args.dry_run:
            print_summary(summary)
    except (
        ClimateForcingResolutionError,
        LeafConfigGenerationError,
        ScenarioTreeValidationError,
        TechnologyResolutionError,
    ) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
