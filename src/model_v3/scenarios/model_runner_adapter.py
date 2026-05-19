"""Adapter from Phase 3 scenario-leaf configs to the existing model_v3 engine."""

from __future__ import annotations

import csv
from copy import deepcopy
from pathlib import Path
import random
from typing import Any, Mapping

import numpy as np
import pandas as pd
import yaml

from model_v3.output.persistence import ensure_dir, write_frame_csv, write_json
from model_v3.simulation.annual_runner import run_annual_simulation
from pipelines.run_model_v3 import load_config


REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_BASE_MODEL_CONFIG = REPO_ROOT / "config" / "model_v3" / "model_v3.yaml"


class ModelRunnerAdapterError(RuntimeError):
    """Raised when a scenario leaf cannot be translated to a model run."""


def _load_yaml(path: Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict):
        raise ModelRunnerAdapterError(f"YAML file must contain a mapping: {path}")
    return data


def _resolve_repo_path(path_text: str | Path, repo_root: Path = REPO_ROOT) -> Path:
    path = Path(path_text)
    if path.is_absolute():
        return path
    return repo_root / path


def _deep_merge(defaults: Mapping[str, Any], overrides: Mapping[str, Any]) -> dict[str, Any]:
    merged = deepcopy(dict(defaults))
    for key, value in dict(overrides).items():
        if isinstance(value, Mapping) and isinstance(merged.get(key), Mapping):
            merged[key] = _deep_merge(dict(merged[key]), value)
        else:
            merged[key] = deepcopy(value)
    return merged


def _first_two_timestamps(path: Path) -> tuple[pd.Timestamp | None, pd.Timestamp | None]:
    with Path(path).open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        first = next(reader, None)
        second = next(reader, None)
    first_ts = pd.Timestamp(first["timestamp"]) if first and first.get("timestamp") else None
    second_ts = pd.Timestamp(second["timestamp"]) if second and second.get("timestamp") else None
    return first_ts, second_ts


def _infer_resolution_seconds(path: Path) -> int:
    first, second = _first_two_timestamps(path)
    if first is None or second is None:
        return 3600
    seconds = int(abs((second - first).total_seconds()))
    return seconds if seconds > 0 else 3600


def _load_technology_case(run_config: Mapping[str, Any], repo_root: Path) -> dict[str, Any]:
    technology_cfg = dict(run_config.get("technology", {}))
    metadata_file = technology_cfg.get("metadata_file")
    if not metadata_file:
        return {}
    metadata_path = _resolve_repo_path(str(metadata_file), repo_root)
    metadata = _load_yaml(metadata_path)
    cases = dict(metadata.get("technology_cases", {}))
    return dict(cases.get(str(technology_cfg.get("case_id", "")), {}))


def _technology_overrides(run_config: Mapping[str, Any], repo_root: Path) -> dict[str, Any]:
    case = _load_technology_case(run_config, repo_root)
    case_id = str(case.get("technology_case_id") or dict(run_config.get("technology", {})).get("case_id", ""))
    heat_pump = bool(case.get("heat_pump_adoption_assumed", False))
    pv = bool(case.get("pv_assumed", False))
    ev = bool(case.get("ev_adoption_assumed", False))

    overrides: dict[str, Any] = {
        "scenario_tree": {
            "technology_case": case,
        },
        "der": {
            "pv": {
                "enabled": pv,
            },
        },
        "mobility": {
            "ev": {
                "enabled": ev,
            },
        },
    }
    if heat_pump:
        overrides = _deep_merge(
            overrides,
            {
                "systems": {
                    "heating": {
                        "technology_type": "air_water",
                        "cop": 3.5,
                    },
                    "dhw": {
                        "technology_type": "linked_to_space_heating",
                    },
                },
                "uncertainty": {
                    "technology": {
                        "heat_pump_share": 1.0,
                        "resistive_share": 0.0,
                    },
                },
            },
        )
    elif case_id in {"tech_current_stock", "tech_frozen_stock"}:
        overrides = _deep_merge(
            overrides,
            {
                "systems": {
                    "heating": {
                        "technology_type": "gas_boiler",
                    },
                    "dhw": {
                        "technology_type": "linked_to_space_heating",
                    },
                },
            },
        )
    return overrides


def scenario_leaf_to_model_config(
    run_config: Mapping[str, Any],
    *,
    base_model_config_path: Path = DEFAULT_BASE_MODEL_CONFIG,
    repo_root: Path = REPO_ROOT,
) -> dict[str, Any]:
    """Translate a Phase 3 scenario-leaf config into the native model config."""

    base_config = load_config(config_path=base_model_config_path)
    climate_cfg = dict(run_config.get("climate", {}))
    stochastic_cfg = dict(run_config.get("stochastic", {}))
    output_cfg = dict(run_config.get("output", {}))
    scenario_leaf_cfg = dict(run_config.get("scenario_leaf", {}))

    forcing_file = _resolve_repo_path(str(climate_cfg.get("forcing_file", "")), repo_root)
    if not forcing_file.exists():
        raise ModelRunnerAdapterError(f"Missing climate forcing file: {forcing_file}")
    first_ts, _ = _first_two_timestamps(forcing_file)
    resolution_seconds = _infer_resolution_seconds(forcing_file)
    output_dir = _resolve_repo_path(str(output_cfg.get("outputs_dir", "")), repo_root)
    seed = int(stochastic_cfg.get("seed_value"))
    cohort_size = int(stochastic_cfg.get("cohort_size"))

    overrides = {
        "simulation": {
            "start_timestamp": first_ts.isoformat() if first_ts is not None else str(climate_cfg.get("analysis_start", "2023-01-01")),
            "reference_year": None,
            "max_steps": None,
        },
        "cohort": {
            "n_households": cohort_size,
            "minimum_households": cohort_size,
            "random_seed": seed,
            "schedule_variation_seed": seed,
        },
        "climate": {
            "enabled": False,
            "seed": seed,
            "occupancy_seed": seed,
            "inputs": {
                "weather_path": str(forcing_file),
            },
        },
        "data": {
            "target_resolution_seconds": resolution_seconds,
            "sources": {
                "weather": {
                    "file_path": str(forcing_file),
                    "timestamp_column": "timestamp",
                    "column_mapping": {
                        "T_outdoor_C": "T_out_C",
                    },
                    "original_timestep_seconds": resolution_seconds,
                    "data_role": ["input"],
                },
                "solar": {
                    "file_path": str(forcing_file),
                    "timestamp_column": "timestamp",
                    "column_mapping": {
                        "I_north": "I_solar_W_m2",
                        "I_east": "I_solar_W_m2",
                        "I_south": "I_solar_W_m2",
                        "I_west": "I_solar_W_m2",
                    },
                    "gain_scale": 1.0,
                    "original_timestep_seconds": resolution_seconds,
                    "data_role": ["input"],
                },
            },
        },
        "model": {
            "timestep_seconds": resolution_seconds,
        },
        "outputs": {
            "root_dir": str(output_dir),
        },
        "scenario_tree": {
            "scenario_leaf": dict(scenario_leaf_cfg),
            "climate": dict(climate_cfg),
            "stochastic": dict(stochastic_cfg),
        },
    }
    technology_inputs_path = dict(run_config.get("technology", {})).get("belgian_technology_inputs")
    if technology_inputs_path:
        overrides["technology_inputs_path"] = str(_resolve_repo_path(str(technology_inputs_path), repo_root))
    overrides = _deep_merge(overrides, _technology_overrides(run_config, repo_root))
    return _deep_merge(base_config, overrides)


def _serialisable_summary(results: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in results.items() if key != "profile_frame"}


def run_model_from_config(
    config_path: Path,
    *,
    base_model_config_path: Path = DEFAULT_BASE_MODEL_CONFIG,
    repo_root: Path = REPO_ROOT,
) -> dict[str, Any]:
    """Run the existing annual model from a Phase 3 scenario-leaf config."""

    run_config_path = Path(config_path)
    run_config = _load_yaml(run_config_path)
    model_config = scenario_leaf_to_model_config(
        run_config,
        base_model_config_path=base_model_config_path,
        repo_root=repo_root,
    )
    seed = int(dict(run_config.get("stochastic", {})).get("seed_value"))
    random.seed(seed)
    np.random.seed(seed)

    results = run_annual_simulation(config=model_config)
    output_dir = ensure_dir(_resolve_repo_path(str(dict(run_config.get("output", {})).get("outputs_dir", "")), repo_root))
    profile_path = write_frame_csv(output_dir / "annual_profile.csv", results["profile_frame"])
    summary_path = write_json(output_dir / "annual_summary.json", _serialisable_summary(results))

    return {
        "status": "success",
        "outputs": [str(profile_path), str(summary_path)],
        "metrics": {
            "n_steps": int(results.get("n_steps", 0)),
            "annual_energy_kWh": float(results.get("annual_energy_kWh", 0.0)),
        },
        "message": "Annual scenario-leaf simulation completed.",
    }
