"""Create the physical model_v3 scenario-tree experiment space."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import yaml

from . import manifest, paths
from .validate_scenario_tree import (
    DEFAULT_CONFIG_ROOT,
    REPO_ROOT,
    ScenarioLeaf,
    ScenarioTreeValidationError,
    ValidationResult,
    validate_scenario_tree,
)


DEFAULT_EXPERIMENT_ROOT = paths.get_experiment_root(REPO_ROOT)


def _path_text(path: Path) -> str:
    return path.as_posix()


def _seed_index(realization_id: str) -> int:
    return int(realization_id.removeprefix("seed_"))


def _write_yaml_if_allowed(path: Path, data: dict[str, Any], *, dry_run: bool, overwrite: bool) -> str:
    if dry_run:
        return "would_create" if not path.exists() else "would_skip_existing"
    if path.exists() and not overwrite:
        return "skipped_existing"
    existed = path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(data, handle, sort_keys=False)
    return "written" if existed else "created"


def _mkdir(path: Path, *, dry_run: bool) -> str:
    if dry_run:
        return "would_create" if not path.exists() else "would_exist"
    existed = path.exists()
    path.mkdir(parents=True, exist_ok=True)
    return "exists" if existed else "created"


def _run_config_payload(leaf: ScenarioLeaf) -> dict[str, Any]:
    return {
        "scenario_leaf_id": leaf.scenario_leaf_id,
        "scenario_id": leaf.scenario_id,
        "climate_window_id": leaf.climate_window_id,
        "climate_pathway_id": leaf.climate_pathway_id,
        "technology_case_id": leaf.technology_case_id,
        "realization_id": leaf.realization_id,
        "canonical_start": leaf.canonical_start,
        "canonical_end": leaf.canonical_end,
        "source_file_window": leaf.source_file_window,
        "status": "not_run",
        "created_by": "model_v3_scenario_tree_phase_2",
        "note": "Simulations are intentionally not executed in Phase 2.",
    }


def _inputs_manifest_payload(leaf: ScenarioLeaf, config_root: Path) -> dict[str, Any]:
    return {
        "scenario_leaf_id": leaf.scenario_leaf_id,
        "climate_forcing_reference": manifest.climate_forcing_reference(leaf),
        "technology_metadata_reference": manifest.technology_config_reference(leaf, config_root),
        "realization_policy_reference": {
            "realization_id": leaf.realization_id,
            "source_metadata": _path_text(config_root / "realization_policy.yaml"),
        },
        "expected_future_input_hooks": [
            "processed_climate_forcing_file",
            "technology_case_parameters",
            "stochastic_household_cohort",
            "model_execution_settings",
        ],
        "status": "metadata_only",
    }


def _seed_config_payload(leaf: ScenarioLeaf) -> dict[str, Any]:
    seed_index = _seed_index(leaf.realization_id)
    return {
        "scenario_id": leaf.scenario_id,
        "realization_id": leaf.realization_id,
        "scenario_leaf_id": leaf.scenario_leaf_id,
        "seed_index": seed_index,
        "seed_value": seed_index,
        "deterministic_seed_rule": "seed value equals the integer suffix of realization_id.",
        "status": "placeholder",
        "note": "Stochastic cohorts are not generated in Phase 2.",
    }


def _select_leaves(result: ValidationResult, max_leaves: int | None) -> list[ScenarioLeaf]:
    if max_leaves is None:
        return result.scenario_leaves
    if max_leaves < 0:
        raise ValueError("--max-leaves must be non-negative.")
    return result.scenario_leaves[:max_leaves]


def create_experiment_space(
    *,
    config_root: Path,
    experiment_root: Path,
    write_manifest: bool,
    dry_run: bool,
    overwrite_placeholder_configs: bool,
    max_leaves: int | None,
) -> dict[str, Any]:
    """Validate metadata and create deterministic scenario-tree directories."""

    result = validate_scenario_tree(config_root=config_root)
    leaves = _select_leaves(result, max_leaves)

    scenario_ids = sorted({leaf.scenario_id for leaf in leaves})
    dirs = [
        paths.get_manifest_dir(experiment_root),
        paths.get_configs_dir(experiment_root),
        paths.get_runs_dir(experiment_root),
        paths.get_summaries_dir(experiment_root),
        paths.get_summaries_dir(experiment_root) / "scenario_level",
        paths.get_summaries_dir(experiment_root) / "comparison_level",
        paths.get_logs_dir(experiment_root),
    ]

    directory_actions: dict[str, int] = {}
    file_actions: dict[str, int] = {}
    for directory in dirs:
        action = _mkdir(directory, dry_run=dry_run)
        directory_actions[action] = directory_actions.get(action, 0) + 1

    for scenario_id in scenario_ids:
        action = _mkdir(paths.scenario_config_dir(experiment_root, scenario_id), dry_run=dry_run)
        directory_actions[action] = directory_actions.get(action, 0) + 1

    for leaf in leaves:
        resolved = paths.paths_for_leaf(experiment_root, leaf.scenario_leaf_id)
        for directory in (resolved["run_dir"], resolved["outputs_dir"], resolved["logs_dir"]):
            action = _mkdir(directory, dry_run=dry_run)
            directory_actions[action] = directory_actions.get(action, 0) + 1

        write_targets = [
            (resolved["realization_config_path"], _seed_config_payload(leaf)),
            (resolved["run_config_path"], _run_config_payload(leaf)),
            (resolved["inputs_manifest_path"], _inputs_manifest_payload(leaf, config_root)),
        ]
        for path, payload in write_targets:
            action = _write_yaml_if_allowed(
                path,
                payload,
                dry_run=dry_run,
                overwrite=overwrite_placeholder_configs,
            )
            file_actions[action] = file_actions.get(action, 0) + 1

    manifest_path = paths.get_manifest_dir(experiment_root) / manifest.SCENARIO_TREE_MANIFEST
    index_path = paths.get_manifest_dir(experiment_root) / manifest.SCENARIO_LEAF_INDEX
    if write_manifest:
        if dry_run:
            file_actions["would_write_manifest"] = file_actions.get("would_write_manifest", 0) + 2
        else:
            limited_result = result
            if max_leaves is not None:
                limited_result = ValidationResult(
                    metadata=result.metadata,
                    realization_ids=sorted({leaf.realization_id for leaf in leaves}),
                    scenario_leaves=leaves,
                    raw_source_windows_overlap=result.raw_source_windows_overlap,
                    canonical_windows_overlap=result.canonical_windows_overlap,
                    year_2050_assignment=result.year_2050_assignment,
                )
            manifest_path, index_path = manifest.write_manifest(limited_result, experiment_root, config_root)
            file_actions["written_manifest"] = file_actions.get("written_manifest", 0) + 2

    return {
        "result": result,
        "leaves": leaves,
        "scenario_count": len(scenario_ids),
        "realization_count": len({leaf.realization_id for leaf in leaves}),
        "leaf_count": len(leaves),
        "directory_actions": directory_actions,
        "file_actions": file_actions,
        "experiment_root": experiment_root,
        "manifest_path": manifest_path if write_manifest else None,
        "index_path": index_path if write_manifest else None,
        "dry_run": dry_run,
    }


def print_summary(summary: dict[str, Any]) -> None:
    """Print a compact creation summary."""

    action = "would be created" if summary["dry_run"] else "created"
    print(f"Scenario-tree experiment space {action}.")
    print(f"Experiment root: {_path_text(summary['experiment_root'])}")
    print(f"Scenarios: {summary['scenario_count']}")
    print(f"Realizations: {summary['realization_count']}")
    print(f"Scenario leaves: {summary['leaf_count']}")
    if summary["manifest_path"] is not None:
        print(f"Manifest: {_path_text(summary['manifest_path'])}")
    if summary["index_path"] is not None:
        print(f"Leaf index: {_path_text(summary['index_path'])}")
    print("Simulations run: 0")
    print("2050 canonical assignment: mid_century_2050_2070")
    print(f"Directory actions: {summary['directory_actions']}")
    print(f"File actions: {summary['file_actions']}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config-root",
        type=Path,
        default=DEFAULT_CONFIG_ROOT,
        help="Directory containing scenario-tree YAML files.",
    )
    parser.add_argument(
        "--experiment-root",
        type=Path,
        default=DEFAULT_EXPERIMENT_ROOT,
        help="Scenario-tree experiment root to create.",
    )
    parser.add_argument("--write-manifest", action="store_true", help="Write manifest YAML and leaf index CSV.")
    parser.add_argument("--print-summary", action="store_true", help="Print a creation summary.")
    parser.add_argument("--dry-run", action="store_true", help="Validate and summarize without writing files.")
    parser.add_argument(
        "--overwrite-placeholder-configs",
        action="store_true",
        help="Overwrite existing leaf-level placeholder YAML files.",
    )
    parser.add_argument(
        "--max-leaves",
        type=int,
        default=None,
        help="Limit generated leaves for smoke testing. Defaults to the full scenario tree.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        summary = create_experiment_space(
            config_root=args.config_root,
            experiment_root=args.experiment_root,
            write_manifest=args.write_manifest,
            dry_run=args.dry_run,
            overwrite_placeholder_configs=args.overwrite_placeholder_configs,
            max_leaves=args.max_leaves,
        )
        if args.print_summary or args.dry_run:
            print_summary(summary)
    except (ScenarioTreeValidationError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
