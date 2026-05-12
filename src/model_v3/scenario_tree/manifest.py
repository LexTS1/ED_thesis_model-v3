"""Manifest generation for the model_v3 scenario-tree experiment space."""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from . import paths
from .validate_scenario_tree import ScenarioLeaf, ValidationResult


SCENARIO_TREE_MANIFEST = "scenario_tree_manifest.yaml"
SCENARIO_LEAF_INDEX = "scenario_leaf_index.csv"


def _path_text(path: Path) -> str:
    return path.as_posix()


def source_config_files(config_root: Path) -> dict[str, str]:
    """Return the Phase 1 source metadata files used to generate this space."""

    return {
        "schema": _path_text(config_root / "scenario_tree_schema.yaml"),
        "climate_windows": _path_text(config_root / "climate_windows.yaml"),
        "technology_cases": _path_text(config_root / "technology_cases.yaml"),
        "realization_policy": _path_text(config_root / "realization_policy.yaml"),
    }


def climate_forcing_reference(leaf: ScenarioLeaf) -> dict[str, str]:
    """Build a deterministic metadata reference for climate forcing inputs."""

    return {
        "window_id": leaf.climate_window_id,
        "pathway_id": leaf.climate_pathway_id,
        "source_file_window": leaf.source_file_window,
        "canonical_start": leaf.canonical_start,
        "canonical_end": leaf.canonical_end,
    }


def technology_config_reference(leaf: ScenarioLeaf, config_root: Path) -> dict[str, str]:
    """Build a deterministic metadata reference for technology assumptions."""

    return {
        "technology_case_id": leaf.technology_case_id,
        "source_metadata": _path_text(config_root / "technology_cases.yaml"),
    }


def leaf_index_rows(
    leaves: list[ScenarioLeaf],
    experiment_root: Path,
    config_root: Path,
) -> list[dict[str, str]]:
    """Return deterministic CSV rows for the scenario leaf index."""

    rows: list[dict[str, str]] = []
    for leaf in leaves:
        resolved = paths.paths_for_leaf(experiment_root, leaf.scenario_leaf_id)
        rows.append(
            {
                "scenario_leaf_id": leaf.scenario_leaf_id,
                "scenario_id": leaf.scenario_id,
                "climate_window_id": leaf.climate_window_id,
                "climate_pathway_id": leaf.climate_pathway_id,
                "technology_case_id": leaf.technology_case_id,
                "realization_id": leaf.realization_id,
                "canonical_start": leaf.canonical_start,
                "canonical_end": leaf.canonical_end,
                "source_file_window": leaf.source_file_window,
                "scenario_config_dir": _path_text(resolved["scenario_config_dir"]),
                "realization_config_path": _path_text(resolved["realization_config_path"]),
                "run_dir": _path_text(resolved["run_dir"]),
                "run_config_path": _path_text(resolved["run_config_path"]),
                "inputs_manifest_path": _path_text(resolved["inputs_manifest_path"]),
                "outputs_dir": _path_text(resolved["outputs_dir"]),
                "logs_dir": _path_text(resolved["logs_dir"]),
                "climate_forcing_reference": json.dumps(climate_forcing_reference(leaf), sort_keys=True),
                "technology_config_reference": json.dumps(
                    technology_config_reference(leaf, config_root),
                    sort_keys=True,
                ),
            }
        )
    return rows


def write_leaf_index(
    leaves: list[ScenarioLeaf],
    experiment_root: Path,
    config_root: Path,
) -> Path:
    """Write scenario_leaf_index.csv and return its path."""

    manifest_dir = paths.get_manifest_dir(experiment_root)
    manifest_dir.mkdir(parents=True, exist_ok=True)
    index_path = manifest_dir / SCENARIO_LEAF_INDEX
    rows = leaf_index_rows(leaves, experiment_root, config_root)
    fieldnames = [
        "scenario_leaf_id",
        "scenario_id",
        "climate_window_id",
        "climate_pathway_id",
        "technology_case_id",
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
    with index_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return index_path


def manifest_document(
    result: ValidationResult,
    experiment_root: Path,
    config_root: Path,
    index_path: Path,
) -> dict[str, Any]:
    """Build the top-level scenario-tree manifest document."""

    climate_windows = result.metadata.climate_windows["climate_windows"]
    technology_cases = result.metadata.technology_cases["technology_cases"]
    scenario_ids = sorted({leaf.scenario_id for leaf in result.scenario_leaves})
    temporal_policy = result.metadata.climate_windows["temporal_window_policy"]
    return {
        "schema_version": "1.0.0",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "source_config_files": source_config_files(config_root),
        "experiment_root": _path_text(experiment_root),
        "scenario_tree_id_convention": {
            "scenario_id": "{climate_window_id}__{climate_pathway_id}__{technology_case_id}",
            "scenario_leaf_id": (
                "{climate_window_id}__{climate_pathway_id}__"
                "{technology_case_id}__{realization_id}"
            ),
            "dimension_separator": "__",
            "internal_word_separator": "_",
        },
        "counts": {
            "climate_windows": len(climate_windows),
            "technology_cases": len(technology_cases),
            "realizations": len(result.realization_ids),
            "scenarios": len(scenario_ids),
            "scenario_leaves": len(result.scenario_leaves),
        },
        "temporal_window_policy_summary": {
            "raw_processed_files_may_overlap": temporal_policy["raw_processed_files_may_overlap"],
            "canonical_analysis_windows_must_overlap": temporal_policy[
                "canonical_analysis_windows_must_overlap"
            ],
            "overlapping_years": temporal_policy["overlapping_years"],
            "near_future_excludes_2050": temporal_policy["near_future_excludes_2050"],
        },
        "year_2050_overlap_handling": {
            "canonical_assignment": result.year_2050_assignment,
            "near_future_canonical_window": {
                "window_id": "near_future_2030_2049",
                "canonical_start": climate_windows["near_future_2030_2049"]["canonical_start"],
                "canonical_end": climate_windows["near_future_2030_2049"]["canonical_end"],
            },
            "mid_century_canonical_window": {
                "window_id": "mid_century_2050_2070",
                "canonical_start": climate_windows["mid_century_2050_2070"]["canonical_start"],
                "canonical_end": climate_windows["mid_century_2050_2070"]["canonical_end"],
            },
        },
        "paths": {
            "scenario_leaf_index_csv": _path_text(index_path),
            "configs": _path_text(paths.get_configs_dir(experiment_root)),
            "runs": _path_text(paths.get_runs_dir(experiment_root)),
            "summaries": _path_text(paths.get_summaries_dir(experiment_root)),
            "logs": _path_text(paths.get_logs_dir(experiment_root)),
        },
        "simulation_status": {
            "simulations_run": 0,
            "note": "Phase 2 creates metadata, folders, and placeholders only. No simulations were run.",
        },
    }


def write_manifest(result: ValidationResult, experiment_root: Path, config_root: Path) -> tuple[Path, Path]:
    """Write the scenario-tree manifest YAML and leaf index CSV."""

    manifest_dir = paths.get_manifest_dir(experiment_root)
    manifest_dir.mkdir(parents=True, exist_ok=True)
    index_path = write_leaf_index(result.scenario_leaves, experiment_root, config_root)
    manifest_path = manifest_dir / SCENARIO_TREE_MANIFEST
    document = manifest_document(result, experiment_root, config_root, index_path)
    with manifest_path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(document, handle, sort_keys=False)
    return manifest_path, index_path
