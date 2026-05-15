"""Artifact persistence helpers for model_v3 top-level runs."""

from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd


def output_root(config: Mapping[str, Any]) -> Path:
    """Resolve the configured output root directory."""

    outputs_cfg = dict(config.get("outputs", {}))
    return Path(outputs_cfg.get("root_dir", "outputs"))


def run_output_dir(config: Mapping[str, Any], run_mode: str, *, final: bool = False) -> Path:
    """Resolve the output directory for a run mode."""

    root = output_root(config)
    return ensure_dir(root / "final" / run_mode if final else root / run_mode)


def ensure_dir(path: Path) -> Path:
    """Create a directory if needed and return it."""

    path.mkdir(parents=True, exist_ok=True)
    return path


def _to_serialisable(value: Any) -> Any:
    """Convert nested objects into JSON-safe structures."""

    if is_dataclass(value):
        return _to_serialisable(asdict(value))
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _to_serialisable(inner) for key, inner in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_serialisable(inner) for inner in value]
    return value


def write_json(path: Path, payload: Mapping[str, Any]) -> Path:
    """Write a JSON payload with deterministic formatting."""

    ensure_dir(path.parent)
    path.write_text(json.dumps(_to_serialisable(dict(payload)), indent=2), encoding="utf-8")
    return path


def _git_commit_hash() -> str | None:
    """Return the current git commit hash when the workspace exposes one."""

    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=Path.cwd(),
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    commit_hash = completed.stdout.strip()
    if completed.returncode != 0 or not commit_hash:
        return None
    return commit_hash


def _first_present(mapping: Mapping[str, Any], keys: tuple[str, ...]) -> Any:
    """Return the first non-empty value for a list of keys."""

    for key in keys:
        value = mapping.get(key)
        if value is not None and value != "":
            return value
    return None


def _model_identity(config: Mapping[str, Any]) -> dict[str, Any]:
    """Return model identity fields available in the config."""

    model_cfg = dict(config.get("model", {}))
    return {
        "name": _first_present(model_cfg, ("name", "model_name")) or config.get("model_name") or "model_v3",
        "version": (
            _first_present(model_cfg, ("version", "model_version"))
            or config.get("version")
            or config.get("model_version")
        ),
    }


def _collect_input_paths(config: Mapping[str, Any]) -> dict[str, Any]:
    """Collect configured input paths that affect a run."""

    paths: dict[str, Any] = {}
    data_sources = dict(dict(config.get("data", {})).get("sources", {}))
    for source_name, source_cfg_raw in sorted(data_sources.items()):
        source_cfg = dict(source_cfg_raw or {})
        source_paths = {
            key: value
            for key, value in source_cfg.items()
            if key.endswith("_path") or key in {"file_path", "spec_path", "raw_dir", "base_path"}
        }
        if source_paths:
            paths[f"data.sources.{source_name}"] = source_paths

    building_cfg = dict(config.get("building", {}))
    building_paths = {
        key: value
        for key, value in building_cfg.items()
        if key.endswith("_path") or key.endswith("_csv") or key in {"tabula_archetypes_path"}
    }
    archetype_source = dict(building_cfg.get("archetype_source", {}))
    if "file_path" in archetype_source:
        building_paths["archetype_source.file_path"] = archetype_source["file_path"]
    if building_paths:
        paths["building"] = building_paths

    climate_inputs = dict(dict(config.get("climate", {})).get("inputs", {}))
    if climate_inputs:
        paths["climate.inputs"] = climate_inputs

    if config.get("technology_inputs_path"):
        paths["technology_inputs_path"] = config.get("technology_inputs_path")
    technology_inputs_cfg = dict(config.get("technology_inputs", {}))
    if technology_inputs_cfg:
        paths["technology_inputs"] = {
            key: value
            for key, value in technology_inputs_cfg.items()
            if key in {"path", "paths"} or key.endswith("_path")
        }

    validation_cfg = dict(config.get("validation", {}))
    validation_paths = {
        key: value
        for key, value in validation_cfg.items()
        if key.endswith("_path") or key.endswith("_dir") or key == "base_path"
    }
    for nested_name in ("fluvius", "kuleuven"):
        nested_cfg = dict(validation_cfg.get(nested_name, {}))
        for key, value in nested_cfg.items():
            if key.endswith("_path") or key.endswith("_dir") or key == "base_path":
                validation_paths[f"{nested_name}.{key}"] = value
    if validation_paths:
        paths["validation"] = validation_paths

    return paths


def _reference_year(config: Mapping[str, Any], results: Mapping[str, Any] | None) -> Any:
    """Resolve the most specific reference year available."""

    if results:
        run_metadata = dict(results.get("run_metadata", {}))
        for source in (run_metadata, results):
            value = source.get("reference_year")
            if value is not None:
                return value
    return dict(config.get("simulation", {})).get("reference_year")


def _cohort_size(config: Mapping[str, Any], results: Mapping[str, Any] | None) -> Any:
    """Resolve cohort size when relevant to the run."""

    if results:
        run_metadata = dict(results.get("run_metadata", {}))
        for source in (run_metadata, results):
            value = source.get("n_households") or source.get("household_count")
            if value is not None:
                return value
    return dict(config.get("cohort", {})).get("n_households")


def _random_seed(config: Mapping[str, Any], results: Mapping[str, Any] | None) -> Any:
    """Resolve the primary random seed for the run."""

    if results:
        run_metadata = dict(results.get("run_metadata", {}))
        for source in (run_metadata, results):
            value = source.get("random_seed") or source.get("seed")
            if value is not None:
                return value
    climate_cfg = dict(config.get("climate", {}))
    if bool(climate_cfg.get("enabled", False)):
        return climate_cfg.get("seed")
    return dict(config.get("cohort", {})).get("random_seed")


def write_run_manifest(
    output_dir: Path,
    *,
    run_mode: str,
    config: Mapping[str, Any],
    artifact_paths: Mapping[str, str],
    config_path: str | Path | None = None,
    results: Mapping[str, Any] | None = None,
    extra: Mapping[str, Any] | None = None,
) -> Path:
    """Write a reproducibility manifest beside canonical thesis artifacts."""

    climate_cfg = dict(config.get("climate", {}))
    cohort_cfg = dict(config.get("cohort", {}))
    resolved_config_path = None if config_path is None else str(Path(config_path))
    git_hash = _git_commit_hash()
    timestamp_utc = datetime.now(timezone.utc).isoformat()
    payload: dict[str, Any] = {
        "manifest_schema": "model_v3 run manifest v1",
        "model": _model_identity(config),
        "run_mode": run_mode,
        "timestamp_utc": timestamp_utc,
        "created_at_utc": timestamp_utc,
        "config": {
            "path": resolved_config_path,
            "name": None if resolved_config_path is None else Path(resolved_config_path).name,
        },
        "reference_year": _reference_year(config, results),
        "cohort_size": _cohort_size(config, results) if run_mode in {"stochastic", "cohort"} else None,
        "random_seed": _random_seed(config, results),
        "seeds": {
            "cohort_random_seed": cohort_cfg.get("random_seed"),
            "climate_seed": climate_cfg.get("seed"),
            "climate_occupancy_seed": climate_cfg.get("occupancy_seed"),
        },
        "climate": {
            "enabled": bool(climate_cfg.get("enabled", False)),
            "mode": climate_cfg.get("mode"),
            "n_members": climate_cfg.get("n_members"),
        },
        "key_input_paths": _collect_input_paths(config),
        "output_artifact_paths": dict(artifact_paths),
        "git": {
            "commit": git_hash,
            "available": git_hash is not None,
        },
        "artifact_hygiene": {
            "canonical_output_dir": str(output_dir),
            "legacy_outputs_retained": True,
            "note": "Older outputs under outputs/* are not deleted and may come from mixed configs or partial runs.",
        },
    }
    if extra:
        payload["extra"] = dict(extra)
    return write_json(output_dir / "run_manifest.json", payload)


def write_frame_csv(path: Path, frame: pd.DataFrame) -> Path:
    """Write a dataframe to CSV."""

    ensure_dir(path.parent)
    frame.to_csv(path, index=False)
    return path


def write_frame_parquet(path: Path, frame: pd.DataFrame) -> Path:
    """Write a dataframe to parquet when available."""

    ensure_dir(path.parent)
    frame.to_parquet(path, index=False)
    return path


def _cohort_summary_payload(results: Mapping[str, Any], artifact_names: Mapping[str, str]) -> dict[str, Any]:
    """Build a compact thesis-facing cohort JSON summary."""

    payload_keys = (
        "run_metadata",
        "sampled_population",
        "annual_energy_summary",
        "annual_calibration_summary",
        "peak_distribution",
        "peak_dhw_distribution",
        "thermal_parameter_distribution",
        "thermal_demand_spread",
        "carrier_energy_summary",
        "variance_by_hour",
        "pipeline_timings_seconds",
        "sample_preview",
    )
    scalar_keys = (
        "n_households",
        "household_count",
        "requested_households",
        "minimum_households",
        "random_seed",
        "reference_year",
        "n_steps",
        "profile_representation",
        "mean_profile",
        "std_profile",
        "P10_profile",
        "P50_profile",
        "P90_profile",
        "diversity_factor",
        "aggregated_peak_W",
        "aggregated_dhw_peak_W",
        "mean_peak_demand_W",
        "annual_energy_kWh_mean",
        "annual_energy_kWh_std",
        "annual_dhw_thermal_kWh_mean",
        "annual_dhw_thermal_kWh_std",
    )
    payload: dict[str, Any] = {
        "summary_schema": "model_v3 stochastic cohort summary v2",
        "description": (
            "Readable summary of the stochastic cohort run. Full time-series profiles are in "
            "aggregate_profile.csv; per-household annual energy and calibration diagnostics are "
            "persisted as separate artifacts."
        ),
        "artifacts": dict(artifact_names),
    }
    for key in scalar_keys:
        if key in results:
            payload[key] = results[key]
    for key in payload_keys:
        if key in results:
            payload[key] = results[key]
    return payload


def persist_model_outputs(
    outputs: Any,
    config: Mapping[str, Any],
    *,
    final: bool = False,
    config_path: str | Path | None = None,
) -> dict[str, str]:
    """Persist deterministic single-step outputs."""

    base_dir = run_output_dir(config, "deterministic", final=final)
    json_path = write_json(base_dir / "model_outputs.json", outputs.__dict__)
    artifact_paths = {"json": str(json_path)}
    if final:
        manifest_path = write_run_manifest(
            base_dir,
            run_mode="deterministic",
            config=config,
            artifact_paths=artifact_paths,
            config_path=config_path,
        )
        artifact_paths["manifest"] = str(manifest_path)
    return artifact_paths


def persist_annual_results(
    results: Mapping[str, Any],
    config: Mapping[str, Any],
    *,
    final: bool = False,
    config_path: str | Path | None = None,
) -> dict[str, str]:
    """Persist annual profile artifacts and quick-look plots."""

    base_dir = run_output_dir(config, "annual", final=final)
    profile_frame = pd.DataFrame(results["profile_frame"]).copy()
    csv_path = write_frame_csv(base_dir / "annual_profile.csv", profile_frame)
    parquet_path = write_frame_parquet(base_dir / "annual_profile.parquet", profile_frame)
    summary_payload = {
        key: value
        for key, value in results.items()
        if key not in {"profile_frame"}
    }
    json_path = write_json(base_dir / "annual_summary.json", summary_payload)

    demand_plot = base_dir / "annual_demand_profile.png"
    plt.figure(figsize=(10, 4))
    plt.plot(pd.to_datetime(profile_frame["timestamp"]), profile_frame["P_el_total_W"], linewidth=0.8)
    plt.ylabel("Demand (W)")
    plt.title("Annual Electricity Demand")
    plt.tight_layout()
    plt.savefig(demand_plot)
    plt.close()

    temp_plot = base_dir / "annual_indoor_temperature.png"
    plt.figure(figsize=(10, 4))
    plt.plot(pd.to_datetime(profile_frame["timestamp"]), profile_frame["T_indoor_next_C"], linewidth=0.8)
    plt.ylabel("Indoor Temperature (C)")
    plt.title("Annual Indoor Temperature")
    plt.tight_layout()
    plt.savefig(temp_plot)
    plt.close()

    artifact_paths = {
        "csv": str(csv_path),
        "parquet": str(parquet_path),
        "json": str(json_path),
        "demand_plot": str(demand_plot),
        "temperature_plot": str(temp_plot),
    }
    if final:
        manifest_path = write_run_manifest(
            base_dir,
            run_mode="annual",
            config=config,
            artifact_paths=artifact_paths,
            config_path=config_path,
            results=results,
        )
        artifact_paths["manifest"] = str(manifest_path)
    return artifact_paths


def persist_cohort_results(
    results: Mapping[str, Any],
    config: Mapping[str, Any],
    *,
    final: bool = False,
    config_path: str | Path | None = None,
) -> dict[str, str]:
    """Persist cohort summary and aggregate profile artifacts."""

    base_dir = run_output_dir(config, "stochastic", final=final)
    if "profile_frame" in results:
        aggregate_profile = pd.DataFrame(results["profile_frame"]).copy()
    else:
        aggregate_profile = pd.DataFrame(
            {
                "step": range(len(results.get("aggregate_profile", []))),
                "aggregate_profile_W": list(results.get("aggregate_profile", [])),
            }
        )
    csv_path = write_frame_csv(base_dir / "aggregate_profile.csv", aggregate_profile)
    household_energy_frame = pd.DataFrame(list(results.get("household_summaries", [])))
    household_energy_path = write_frame_csv(base_dir / "household_annual_energy.csv", household_energy_frame)
    calibration_diagnostics_path = write_json(
        base_dir / "household_calibration_diagnostics.json",
        {
            "run_metadata": dict(results.get("run_metadata", {})),
            "annual_calibration_summary": dict(results.get("annual_calibration_summary", {})),
            "household_calibration_diagnostics": list(results.get("household_calibration_diagnostics", [])),
        },
    )

    plot_path = base_dir / "aggregate_profile.png"
    plt.figure(figsize=(8, 4))
    x_axis = pd.to_datetime(aggregate_profile["timestamp"]) if "timestamp" in aggregate_profile.columns else aggregate_profile["step"]
    y_axis = (
        aggregate_profile["per_household_profile_W"]
        if "per_household_profile_W" in aggregate_profile.columns
        else aggregate_profile["aggregate_profile_W"]
    )
    plt.plot(x_axis, y_axis, linewidth=1.0)
    plt.xlabel("Time" if "timestamp" in aggregate_profile.columns else "Step")
    plt.ylabel("Aggregate Demand (W)")
    plt.title("Cohort Aggregate Profile")
    plt.tight_layout()
    plt.savefig(plot_path)
    plt.close()

    artifact_names = {
        "aggregate_profile_csv": str(csv_path),
        "household_annual_energy_csv": str(household_energy_path),
        "household_calibration_diagnostics_json": str(calibration_diagnostics_path),
        "aggregate_profile_plot": str(plot_path),
    }
    json_path = write_json(base_dir / "cohort_summary.json", _cohort_summary_payload(results, artifact_names))

    artifact_paths = {
        "csv": str(csv_path),
        "json": str(json_path),
        "household_annual_energy_csv": str(household_energy_path),
        "household_calibration_diagnostics_json": str(calibration_diagnostics_path),
        "plot": str(plot_path),
    }
    if final:
        manifest_path = write_run_manifest(
            base_dir,
            run_mode="stochastic",
            config=config,
            artifact_paths=artifact_paths,
            config_path=config_path,
            results=results,
        )
        artifact_paths["manifest"] = str(manifest_path)
    return artifact_paths


def validation_output_dir(config: Mapping[str, Any], dataset_name: str) -> Path:
    """Return the output directory for validation artifacts."""

    return ensure_dir(output_root(config) / "validation" / dataset_name)
