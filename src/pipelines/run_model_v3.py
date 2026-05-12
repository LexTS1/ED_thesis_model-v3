#!/usr/bin/env python3
"""Deterministic orchestration pipeline for model_v3."""

from __future__ import annotations

if __package__ in {None, ""}:
    import sys
    from pathlib import Path

    sys.path.append(str(Path(__file__).resolve().parents[1]))

import logging
from time import perf_counter
from pathlib import Path
import argparse
from typing import Any, Mapping

import yaml

from model_v3.adapters.forcing_builder import build_prepared_forcing
from model_v3.control.control_core import run_control
from model_v3.data.data_module import load_all_sources
from model_v3.interfaces import ModelOutputs
from model_v3.output.output_core import assemble_outputs
from model_v3.output.persistence import persist_model_outputs
from model_v3.physics.physics_core import run_physics
from model_v3.systems.system_core import run_systems
from model_v3.utils.feature_flags import (
    disabled_control_state,
    disabled_physics_state,
    disabled_system_state,
    enabled_runtime_modules,
    is_module_enabled,
)


DEFAULT_CONFIG = Path(__file__).resolve().parents[2] / "config" / "model_v3" / "model_v3.yaml"
LOGGER = logging.getLogger(__name__)


def resolve_config_path(config_path: str | Path | None = None) -> Path:
    """Resolve the explicit or default model_v3 config path."""

    return Path(config_path or DEFAULT_CONFIG)


def _deep_merge(defaults: Mapping[str, Any], overrides: Mapping[str, Any]) -> dict[str, Any]:
    """Recursively merge config dictionaries, with overrides taking precedence."""

    merged = dict(defaults)
    for key, value in dict(overrides).items():
        if isinstance(value, Mapping) and isinstance(merged.get(key), Mapping):
            merged[key] = _deep_merge(dict(merged[key]), value)
        else:
            merged[key] = value
    return merged


def _load_yaml_file(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return dict(yaml.safe_load(handle) or {})


def _resolve_include_path(raw_path: str | Path, base_dir: Path) -> Path:
    candidate = Path(raw_path)
    if candidate.is_absolute():
        return candidate
    return (base_dir / candidate).resolve()


def load_config(config_path: str | Path | None = None) -> dict[str, Any]:
    """Load the model_v3 YAML configuration."""

    resolved_path = resolve_config_path(config_path)
    config = _load_yaml_file(resolved_path)
    include_paths: list[Any] = []
    if config.get("technology_inputs_path"):
        include_paths.append(config["technology_inputs_path"])
    technology_inputs_cfg = dict(config.get("technology_inputs", {}))
    if technology_inputs_cfg.get("path"):
        include_paths.append(technology_inputs_cfg["path"])
    include_paths.extend(technology_inputs_cfg.get("paths", []) or [])

    included: dict[str, Any] = {}
    for include_path in include_paths:
        resolved_include = _resolve_include_path(include_path, resolved_path.parent)
        included = _deep_merge(included, _load_yaml_file(resolved_include))
    return _deep_merge(included, config)


def run_pipeline(config: Mapping[str, Any]) -> ModelOutputs:
    """Run the strict layered v3 pipeline and return the output contract."""

    input_data = load_all_sources(config=config)
    prepared = build_prepared_forcing(input_dataset=input_data)
    physics_state = (
        run_physics(prepared_forcing=prepared)
        if is_module_enabled(config, "physics")
        else disabled_physics_state(prepared)
    )
    control_state = (
        run_control(physics_state=physics_state)
        if is_module_enabled(config, "control")
        else disabled_control_state(physics_state)
    )
    system_state = (
        run_systems(control_state=control_state)
        if is_module_enabled(config, "systems")
        else disabled_system_state(control_state, enabled_runtime_modules(config))
    )
    outputs = assemble_outputs(system_state=system_state)
    return outputs


def run_pipeline_with_timings(config: Mapping[str, Any]) -> tuple[ModelOutputs, dict[str, float]]:
    """Run the strict layered pipeline while measuring orchestration timings."""

    timings: dict[str, float] = {}

    stage_start = perf_counter()
    input_data = load_all_sources(config=config)
    timings["load_all_sources"] = perf_counter() - stage_start

    stage_start = perf_counter()
    prepared = build_prepared_forcing(input_dataset=input_data)
    timings["build_prepared_forcing"] = perf_counter() - stage_start

    stage_start = perf_counter()
    physics_state = (
        run_physics(prepared_forcing=prepared)
        if is_module_enabled(config, "physics")
        else disabled_physics_state(prepared)
    )
    timings["run_physics"] = perf_counter() - stage_start

    stage_start = perf_counter()
    control_state = (
        run_control(physics_state=physics_state)
        if is_module_enabled(config, "control")
        else disabled_control_state(physics_state)
    )
    timings["run_control"] = perf_counter() - stage_start

    stage_start = perf_counter()
    system_state = (
        run_systems(control_state=control_state)
        if is_module_enabled(config, "systems")
        else disabled_system_state(control_state, enabled_runtime_modules(config))
    )
    timings["run_systems"] = perf_counter() - stage_start

    stage_start = perf_counter()
    outputs = assemble_outputs(system_state=system_state)
    timings["assemble_outputs"] = perf_counter() - stage_start

    outputs.metadata["pipeline_timings_seconds"] = timings
    return outputs, timings


def main(
    config_path: str | Path | None = None,
    config: Mapping[str, Any] | None = None,
    emit_summary: bool = True,
) -> ModelOutputs:
    """Entrypoint for a deterministic model_v3 run."""

    previous_disable_level = logging.root.manager.disable
    if emit_summary:
        logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    else:
        logging.disable(logging.INFO)
    resolved_config_path = None if config is not None else resolve_config_path(config_path)
    resolved_config = dict(config) if config is not None else load_config(config_path=resolved_config_path)
    try:
        if emit_summary:
            LOGGER.info("pipeline.start")
        outputs, timings = run_pipeline_with_timings(config=resolved_config)
        if emit_summary:
            artifact_paths = persist_model_outputs(
                outputs=outputs,
                config=resolved_config,
                config_path=resolved_config_path,
            )
            print(f"ModelOutputs fields: {', '.join(ModelOutputs.field_names())}")
            print("Pipeline step timings (s):")
            for step_name, elapsed in timings.items():
                print(f"- {step_name}: {elapsed:.6f}")
            print("Saved artifacts:")
            for artifact_name, artifact_path in artifact_paths.items():
                print(f"- {artifact_name}: {artifact_path}")
            print("Physics finalization completed")
        return outputs
    finally:
        logging.disable(previous_disable_level)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the one-step deterministic model_v3 pipeline.")
    parser.add_argument("--config", default=None, help="Path to the model_v3 YAML config. Defaults to config/model_v3/model_v3.yaml.")
    args = parser.parse_args()
    main(config_path=args.config)
