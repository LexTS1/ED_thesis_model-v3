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

from model_v3.cohort.cohort_engine import run_cohort_simulation
from model_v3.output.persistence import ensure_dir, write_frame_csv, write_json
from model_v3.scenarios.stock_weighted_archetypes import run_stock_weighted_archetype_simulation
from model_v3.simulation.annual_runner import run_annual_simulation
from model_v3.systems.distributed_energy import value_from_range
from model_v3.systems.technology import normalize_technology_type
from pipelines.run_model_v3 import load_config


REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_BASE_MODEL_CONFIG = REPO_ROOT / "config" / "model.yaml"


class ModelRunnerAdapterError(RuntimeError):
    """Raised when a scenario leaf cannot be translated to a model run."""


ANNUAL_DEMAND_MODE = "annual_demand"
STOCK_WEIGHTED_ARCHETYPE_MODE = "stock_weighted_archetypes"
STOCHASTIC_COHORT_MODE = "stochastic_cohort"
_ANNUAL_MODE_ALIASES = {"annual", "annual_demand", "deterministic_annual"}
_STOCK_WEIGHTED_ARCHETYPE_MODE_ALIASES = {"stock_weighted_archetypes", "stock_weighted_annual", "archetype_stock_weighted"}
_COHORT_MODE_ALIASES = {"cohort", "stochastic_cohort"}
_SUMMARY_OMIT_KEYS = {
    "profile_frame",
    "aggregate_profile",
    "timestamps",
    "household_profiles",
    "household_grid_import_profiles",
    "household_gross_actual_profiles",
    "household_event_profiles",
    "household_nonthermal_profiles",
    "household_base_profiles",
    "household_lighting_profiles",
    "household_dhw_profiles",
    "household_occupancy_profiles",
}


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


def _target_resolution_seconds(run_config: Mapping[str, Any], original_resolution_seconds: int) -> int:
    model_options = dict(run_config.get("model_options", {}))
    raw_value = model_options.get("target_resolution_seconds")
    if raw_value in (None, ""):
        return int(original_resolution_seconds)
    try:
        target = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise ModelRunnerAdapterError("model_options.target_resolution_seconds must be an integer.") from exc
    if target <= 0:
        raise ModelRunnerAdapterError("model_options.target_resolution_seconds must be positive.")
    return target


def _load_technology_case(run_config: Mapping[str, Any], repo_root: Path) -> dict[str, Any]:
    technology_cfg = dict(run_config.get("technology", {}))
    metadata_file = technology_cfg.get("metadata_file")
    if not metadata_file:
        return {}
    metadata_path = _resolve_repo_path(str(metadata_file), repo_root)
    metadata = _load_yaml(metadata_path)
    cases = dict(metadata.get("technology_cases", {}))
    return dict(cases.get(str(technology_cfg.get("case_id", "")), {}))


def _bounded_probability(value: Any, default: float = 0.0) -> float:
    return float(min(max(value_from_range(value, default), 0.0), 1.0))


def _normalised_probabilities(
    values: Mapping[str, Any],
    *,
    normalise_labels: bool = True,
) -> dict[str, float]:
    probabilities: dict[str, float] = {}
    for raw_label, raw_probability in dict(values).items():
        try:
            probability = max(float(raw_probability), 0.0)
        except (TypeError, ValueError):
            continue
        if probability <= 0.0:
            continue
        label = normalize_technology_type(raw_label) if normalise_labels else str(raw_label)
        probabilities[label] = probabilities.get(label, 0.0) + probability

    total = sum(probabilities.values())
    if total <= 0.0:
        return {}
    return {label: probability / total for label, probability in sorted(probabilities.items())}


def _most_likely(probabilities: Mapping[str, float], default: str) -> str:
    if not probabilities:
        return default
    return max(probabilities.items(), key=lambda item: float(item[1]))[0]


def _technology_overrides(run_config: Mapping[str, Any], repo_root: Path) -> dict[str, Any]:
    case = _load_technology_case(run_config, repo_root)
    case_id = str(case.get("technology_case_id") or dict(run_config.get("technology", {})).get("case_id", ""))
    heat_pump = bool(case.get("heat_pump_adoption_assumed", False))
    assignment = dict(case.get("household_assignment", {}))
    heating_assignment = dict(assignment.get("heating", {}))
    dhw_assignment = dict(assignment.get("dhw", {}))
    heating_probabilities = _normalised_probabilities(
        dict(heating_assignment.get("technology_probabilities", {}))
    )
    dhw_probabilities = _normalised_probabilities(
        dict(dhw_assignment.get("technology_probabilities", {}))
    )
    use_belgian_stock = str(heating_assignment.get("mode", "")).strip() == "belgian_current_stock_carrier_mapping"
    pv_probability = _bounded_probability(dict(assignment.get("pv", {})).get("household_probability"), 1.0 if bool(case.get("pv_assumed", False)) else 0.0)
    ev_probability = _bounded_probability(dict(assignment.get("ev", {})).get("household_probability"), 1.0 if bool(case.get("ev_adoption_assumed", False)) else 0.0)
    fallback_heating = "gas_boiler" if use_belgian_stock or case_id in {"tech_current_stock", "tech_frozen_stock"} else "air_water"
    fallback_heating = _most_likely(heating_probabilities, fallback_heating)

    overrides: dict[str, Any] = {
        "scenario_tree": {
            "technology_case": case,
        },
        "systems": {
            "heating": {
                "technology_type": fallback_heating,
            },
            "dhw": {
                "technology_type": _most_likely(dhw_probabilities, "linked_to_space_heating"),
            },
        },
        "der": {
            "pv": {
                "enabled": False,
                "adoption": {
                    "household_probability": pv_probability,
                },
            },
        },
        "mobility": {
            "ev": {
                "enabled": False,
                "ownership": {
                    "household_probability": ev_probability,
                },
            },
        },
        "uncertainty": {
            "technology": {
                "assignment_case_id": case_id,
                "assignment_source": assignment.get("assignment_source", "legacy_case_metadata"),
                "use_belgian_stock_baseline": use_belgian_stock,
                "pv_household_probability": pv_probability,
                "ev_household_probability": ev_probability,
            },
        },
    }
    if heating_probabilities:
        overrides["uncertainty"]["technology"]["heating_technology_probabilities"] = heating_probabilities
    if dhw_probabilities:
        overrides["uncertainty"]["technology"]["dhw_technology_probabilities"] = dhw_probabilities

    if heat_pump:
        overrides = _deep_merge(
            overrides,
            {
                "systems": {
                    "heating": {
                        "technology_type": fallback_heating,
                        "emitter_type": "standard_radiators",
                        "refrigerant": "R290",
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
    original_resolution_seconds = _infer_resolution_seconds(forcing_file)
    target_resolution_seconds = _target_resolution_seconds(run_config, original_resolution_seconds)
    output_dir = _resolve_repo_path(str(output_cfg.get("outputs_dir", "")), repo_root)
    seed = int(stochastic_cfg.get("seed_value"))
    cohort_size = int(stochastic_cfg.get("cohort_size"))
    analysis_start = str(climate_cfg.get("analysis_start", ""))
    analysis_end = str(climate_cfg.get("analysis_end", ""))

    overrides = {
        "simulation": {
            "start_timestamp": first_ts.isoformat() if first_ts is not None else analysis_start or "2023-01-01",
            "reference_year": None,
            "schedule_reference_year": 2001,
            "analysis_start": analysis_start,
            "analysis_end": analysis_end,
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
            "target_resolution_seconds": target_resolution_seconds,
            "sources": {
                "weather": {
                    "file_path": str(forcing_file),
                    "timestamp_column": "timestamp",
                    "column_mapping": {
                        "T_outdoor_C": "T_out_C",
                    },
                    "original_timestep_seconds": original_resolution_seconds,
                    "data_role": ["input"],
                },
                "solar": {
                    "file_path": str(forcing_file),
                    "timestamp_column": "timestamp",
                    "column_mapping": {
                        "I_global_W_m2": "I_solar_W_m2",
                    },
                    "gain_scale": 1.0,
                    "original_timestep_seconds": original_resolution_seconds,
                    "data_role": ["input"],
                },
            },
            "target_resolution_seconds": target_resolution_seconds,
        },
        "model": {
            "timestep_seconds": target_resolution_seconds,
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


def _runner_mode(run_config: Mapping[str, Any]) -> str:
    model_options = dict(run_config.get("model_options", {}))
    raw_mode = str(model_options.get("runner_mode") or ANNUAL_DEMAND_MODE).strip().lower()
    if raw_mode in _ANNUAL_MODE_ALIASES:
        return ANNUAL_DEMAND_MODE
    if raw_mode in _STOCK_WEIGHTED_ARCHETYPE_MODE_ALIASES:
        return STOCK_WEIGHTED_ARCHETYPE_MODE
    if raw_mode in _COHORT_MODE_ALIASES:
        return STOCHASTIC_COHORT_MODE
    raise ModelRunnerAdapterError(
        "Unsupported model_options.runner_mode "
        f"{raw_mode!r}; expected one of {sorted(_ANNUAL_MODE_ALIASES | _STOCK_WEIGHTED_ARCHETYPE_MODE_ALIASES | _COHORT_MODE_ALIASES)}."
    )


def _serialisable_summary(results: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in results.items() if key not in _SUMMARY_OMIT_KEYS}


def _write_household_profile_matrix(
    output_dir: Path,
    *,
    filename: str,
    timestamps: list[Any],
    profiles: Mapping[str, Any],
) -> Path | None:
    if not profiles:
        return None
    frame = pd.DataFrame({"timestamp": [pd.Timestamp(value).isoformat() for value in timestamps]})
    for household_id, values in sorted(dict(profiles).items()):
        series = list(values)
        if len(series) == len(frame):
            frame[str(household_id)] = pd.to_numeric(pd.Series(series), errors="coerce").fillna(0.0)
    if len(frame.columns) <= 1:
        return None
    return write_frame_csv(output_dir / filename, frame)


def _write_cohort_artifacts(output_dir: Path, results: Mapping[str, Any]) -> list[str]:
    timestamps = list(results.get("timestamps", []))
    if not timestamps:
        profile_frame = results.get("profile_frame")
        if isinstance(profile_frame, pd.DataFrame) and "timestamp" in profile_frame:
            timestamps = list(profile_frame["timestamp"])
    written: list[str] = []
    for filename, key in (
        ("household_profile_matrix.csv", "household_profiles"),
        ("household_grid_import_matrix.csv", "household_grid_import_profiles"),
        ("household_gross_actual_matrix.csv", "household_gross_actual_profiles"),
    ):
        path = _write_household_profile_matrix(
            output_dir,
            filename=filename,
            timestamps=timestamps,
            profiles=dict(results.get(key, {})),
        )
        if path is not None:
            written.append(str(path))
    household_summaries = list(results.get("household_summaries", []))
    if household_summaries:
        path = write_frame_csv(output_dir / "household_annual_energy.csv", pd.DataFrame(household_summaries))
        written.append(str(path))
    return written


def _write_stock_weighted_artifacts(output_dir: Path, results: Mapping[str, Any]) -> list[str]:
    payload = dict(results.get("stock_weighted_archetypes", {}))
    summaries = list(payload.get("summary", []))
    if not summaries:
        return []
    path = write_frame_csv(output_dir / "stock_weighted_archetype_summary.csv", pd.DataFrame(summaries))
    return [str(path)]


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

    runner_mode = _runner_mode(run_config)
    if runner_mode == STOCHASTIC_COHORT_MODE:
        results = run_cohort_simulation(config=model_config)
    elif runner_mode == STOCK_WEIGHTED_ARCHETYPE_MODE:
        results = run_stock_weighted_archetype_simulation(config=model_config)
    else:
        results = run_annual_simulation(config=model_config)
    output_dir = ensure_dir(_resolve_repo_path(str(dict(run_config.get("output", {})).get("outputs_dir", "")), repo_root))
    profile_path = write_frame_csv(output_dir / "annual_profile.csv", results["profile_frame"])
    cohort_paths: list[str] = []
    if runner_mode == STOCHASTIC_COHORT_MODE:
        cohort_paths = _write_cohort_artifacts(output_dir, results)
    elif runner_mode == STOCK_WEIGHTED_ARCHETYPE_MODE:
        cohort_paths = _write_stock_weighted_artifacts(output_dir, results)
    summary_path = write_json(output_dir / "annual_summary.json", _serialisable_summary(results))

    return {
        "status": "success",
        "outputs": [str(profile_path), str(summary_path), *cohort_paths],
        "metrics": {
            "n_steps": int(results.get("n_steps", 0)),
            "annual_energy_kWh": float(results.get("annual_energy_kWh", results.get("annual_energy_summary", {}).get("aggregate_calibrated_electricity_kWh", 0.0))),
        },
        "message": f"Scenario-leaf {runner_mode} simulation completed.",
    }
