"""Deterministic path resolution for the model_v3 scenario-tree experiment space."""

from __future__ import annotations

from pathlib import Path

from .naming import parse_scenario_leaf_id, validate_realization_id, validate_scenario_id, validate_scenario_leaf_id


def get_experiment_root(repo_root: Path) -> Path:
    """Return the default scenario-tree experiment root for a repository root."""

    return repo_root / "experiments" / "scenario_tree"


def get_manifest_dir(experiment_root: Path) -> Path:
    return experiment_root / "manifests"


def get_configs_dir(experiment_root: Path) -> Path:
    return experiment_root / "configs"


def get_runs_dir(experiment_root: Path) -> Path:
    return experiment_root / "runs"


def get_summaries_dir(experiment_root: Path) -> Path:
    return experiment_root / "summaries"


def get_logs_dir(experiment_root: Path) -> Path:
    return experiment_root / "logs"


def scenario_config_dir(experiment_root: Path, scenario_id: str) -> Path:
    validate_scenario_id(scenario_id)
    return get_configs_dir(experiment_root) / scenario_id


def realization_config_path(experiment_root: Path, scenario_id: str, realization_id: str) -> Path:
    validate_scenario_id(scenario_id)
    validate_realization_id(realization_id)
    return scenario_config_dir(experiment_root, scenario_id) / f"{realization_id}.yaml"


def run_dir(experiment_root: Path, scenario_leaf_id: str) -> Path:
    validate_scenario_leaf_id(scenario_leaf_id)
    return get_runs_dir(experiment_root) / scenario_leaf_id


def run_config_path(experiment_root: Path, scenario_leaf_id: str) -> Path:
    return run_dir(experiment_root, scenario_leaf_id) / "run_config.yaml"


def inputs_manifest_path(experiment_root: Path, scenario_leaf_id: str) -> Path:
    return run_dir(experiment_root, scenario_leaf_id) / "inputs_manifest.yaml"


def run_outputs_dir(experiment_root: Path, scenario_leaf_id: str) -> Path:
    return run_dir(experiment_root, scenario_leaf_id) / "outputs"


def run_logs_dir(experiment_root: Path, scenario_leaf_id: str) -> Path:
    return run_dir(experiment_root, scenario_leaf_id) / "logs"


def paths_for_leaf(experiment_root: Path, scenario_leaf_id: str) -> dict[str, Path]:
    """Return all deterministic paths for one scenario leaf."""

    parsed = parse_scenario_leaf_id(scenario_leaf_id)
    return {
        "scenario_config_dir": scenario_config_dir(experiment_root, parsed["scenario_id"]),
        "realization_config_path": realization_config_path(
            experiment_root,
            parsed["scenario_id"],
            parsed["realization_id"],
        ),
        "run_dir": run_dir(experiment_root, scenario_leaf_id),
        "run_config_path": run_config_path(experiment_root, scenario_leaf_id),
        "inputs_manifest_path": inputs_manifest_path(experiment_root, scenario_leaf_id),
        "outputs_dir": run_outputs_dir(experiment_root, scenario_leaf_id),
        "logs_dir": run_logs_dir(experiment_root, scenario_leaf_id),
    }
