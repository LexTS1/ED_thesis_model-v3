#!/usr/bin/env python3
"""Sequential annual deterministic pipeline for model_v3."""

from __future__ import annotations

if __package__ in {None, ""}:
    import sys
    from pathlib import Path

    sys.path.append(str(Path(__file__).resolve().parents[1]))

from pathlib import Path
import argparse

from model_v3.output.persistence import persist_annual_results
from model_v3.simulation.annual_runner import run_annual_simulation
from pipelines.run_model_v3 import load_config, resolve_config_path


def main(config_path: str | Path | None = None) -> dict[str, object]:
    """Run the sequential annual deterministic pipeline."""

    resolved_config_path = resolve_config_path(config_path)
    config = load_config(config_path=resolved_config_path)
    results = run_annual_simulation(config=config)
    artifact_paths = persist_annual_results(
        results=results,
        config=config,
        final=True,
        config_path=resolved_config_path,
    )
    print(f"Reference year: {results.get('reference_year')}")
    print(f"Timesteps simulated: {results['n_steps']}")
    print(f"Annual electricity: {results['annual_energy_kWh']:.3f} kWh")
    print(f"Space heating thermal: {results['space_heating_thermal_kWh']:.3f} kWh")
    print(f"DHW thermal: {results['dhw_thermal_kWh']:.3f} kWh")
    print(f"Mean hourly demand: {results['mean_profile']:.3f} W")
    print("Saved artifacts:")
    for artifact_name, artifact_path in artifact_paths.items():
        print(f"- {artifact_name}: {artifact_path}")
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the sequential annual deterministic model_v3 pipeline.")
    parser.add_argument("--config", default=None, help="Path to the model_v3 YAML config. Defaults to config/model.yaml.")
    args = parser.parse_args()
    main(config_path=args.config)
