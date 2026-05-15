#!/usr/bin/env python3
"""Stochastic climate-uncertainty pipeline for model_v3."""

from __future__ import annotations

if __package__ in {None, ""}:
    import sys
    from pathlib import Path

    sys.path.append(str(Path(__file__).resolve().parents[1]))

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import argparse
import logging
from typing import Any, Mapping

import numpy as np
import pandas as pd

from model_v3.adapters.pvgis_solar_loader import load_pvgis_solar_csvs
from model_v3.adapters.pvgis_weather_loader import load_pvgis_weather_csv
from model_v3.adapters.forcing_builder import build_prepared_forcing
from model_v3.cohort.cohort_engine import run_cohort_simulation
from model_v3.data.data_module import load_all_sources
from model_v3.interfaces import InputDataset
from model_v3.output.persistence import (
    ensure_dir,
    output_root,
    persist_cohort_results,
    run_output_dir,
    write_frame_parquet,
    write_json,
    write_run_manifest,
)
from model_v3.simulation.annual_runner import _prepare_reference_year_input, _representative_timestep_seconds, _run_step_layers, _step_input_dataset
from model_v3.utils.energy import integrate_power_series_kwh
from model_v3.utils.feature_flags import require_module_enabled
from model_v3.weather.ensemble_generator import generate_weather_ensemble
from model_v3.weather.forcing_builder import PreparedForcing, build_forcing, forcing_to_source_data
from model_v3.weather.year_splitter import split_into_years
from pipelines.run_model_v3 import load_config, resolve_config_path


LOGGER = logging.getLogger(__name__)
NOISY_CLIMATE_LOGGERS = (
    "model_v3.adapters.forcing_builder",
    "model_v3.physics.physics_core",
    "model_v3.control.control_core",
    "model_v3.systems.system_core",
    "model_v3.output.output_core",
)


def _lag1_autocorrelation(series: pd.Series) -> float:
    """Return lag-1 autocorrelation with a deterministic fallback."""

    numeric = pd.to_numeric(series, errors="coerce").astype(float)
    if len(numeric) < 2:
        return 0.0
    autocorr = numeric.autocorr(lag=1)
    return float(0.0 if pd.isna(autocorr) else autocorr)


def _compute_degree_days(temperature: pd.Series, base_temperature_c: float = 18.0) -> tuple[float, float]:
    """Return HDD18 and CDD18 for an hourly temperature series."""

    values = pd.to_numeric(temperature, errors="coerce").astype(float)
    hdd18 = float(np.maximum(float(base_temperature_c) - values, 0.0).sum() / 24.0)
    cdd18 = float(np.maximum(values - float(base_temperature_c), 0.0).sum() / 24.0)
    return hdd18, cdd18


def _summarise_metric(values: pd.Series) -> dict[str, float]:
    """Return compact distribution statistics for one metric."""

    numeric = pd.to_numeric(values, errors="coerce").astype(float)
    return {
        "median": float(numeric.quantile(0.50)),
        "p5": float(numeric.quantile(0.05)),
        "p95": float(numeric.quantile(0.95)),
        "iqr": float(numeric.quantile(0.75) - numeric.quantile(0.25)),
    }


def _validate_hourly_index(index: pd.DatetimeIndex) -> bool:
    """Return whether the forcing timeline is strictly hourly."""

    if index.empty or index.has_duplicates:
        return False
    deltas = index.to_series().diff().dropna()
    return bool(deltas.empty or deltas.eq(pd.Timedelta(hours=1)).all())


def _member_validation(
    member_weather: pd.DataFrame,
    base_weather: pd.DataFrame,
    forcing: PreparedForcing,
) -> dict[str, Any]:
    """Compute the requested climate-forcing validation checks for one member."""

    member_temp = pd.to_numeric(member_weather["temperature_C"], errors="coerce").astype(float)
    base_temp = pd.to_numeric(base_weather["temperature_C"], errors="coerce").astype(float)
    base_std = float(base_temp.std(ddof=0))
    member_std = float(member_temp.std(ddof=0))
    base_lag1 = _lag1_autocorrelation(base_temp)
    member_lag1 = _lag1_autocorrelation(member_temp)
    std_ratio = float(member_std / base_std) if base_std > 1e-9 else 1.0
    mean_delta = float(member_temp.mean() - base_temp.mean())
    lag1_delta = float(member_lag1 - base_lag1)
    no_nan_values = bool(not forcing.frame.isna().any().any())
    no_timestep_drift = _validate_hourly_index(forcing.frame.index)

    return {
        "temperature_mean_delta_C": mean_delta,
        "temperature_std_ratio": std_ratio,
        "temperature_lag1_base": base_lag1,
        "temperature_lag1_member": member_lag1,
        "temperature_lag1_delta": lag1_delta,
        "temperature_distribution_preserved": bool(abs(mean_delta) <= 2.0 and 0.70 <= std_ratio <= 1.30),
        "autocorrelation_realistic": bool(abs(lag1_delta) <= 0.15),
        "no_timestep_drift": no_timestep_drift,
        "no_nan_values": no_nan_values,
    }


def _climate_output_dir(config: Mapping[str, Any], *, final: bool = False) -> Path:
    """Return the climate uncertainty output directory."""

    if final:
        return run_output_dir(config, "climate_uncertainty", final=True)
    return ensure_dir(output_root(config) / "climate_uncertainty")


def _solar_years_from_series(solar_series: dict[str, pd.Series]) -> dict[int, dict[str, pd.Series]]:
    """Split facade irradiance series into aligned yearly orientation bundles."""

    solar_frame = pd.DataFrame({orientation: series for orientation, series in solar_series.items()}).sort_index()
    yearly_frames = split_into_years(solar_frame)
    return {
        year: {
            orientation: yearly_frame[orientation].copy()
            for orientation in ("south", "east", "west", "north")
        }
        for year, yearly_frame in yearly_frames.items()
    }


def _member_seed(climate_cfg: Mapping[str, Any], member_index: int) -> int:
    """Resolve the occupancy schedule seed for one member."""

    mode = str(climate_cfg.get("mode", "climate_only")).strip().lower()
    base_seed = int(climate_cfg.get("occupancy_seed", climate_cfg.get("seed", 42)))
    stride = int(climate_cfg.get("occupancy_seed_stride", 1000))
    if mode == "climate_only":
        return base_seed
    if mode == "joint":
        return base_seed + int(member_index) * stride
    raise ValueError("climate.mode must be either `climate_only` or `joint`.")


def _member_config(config: Mapping[str, Any]) -> dict[str, Any]:
    """Return a run config adjusted for climate-member execution."""

    prepared = deepcopy(dict(config))
    simulation_cfg = prepared.setdefault("simulation", {})
    simulation_cfg["reference_year"] = None
    return prepared


def _member_input_dataset(
    base_input: InputDataset,
    forcing: PreparedForcing,
    member_index: int,
    base_year: int,
    occupancy_seed: int,
) -> InputDataset:
    """Inject member-specific weather/solar forcing into the reusable input dataset."""

    source_data = dict(base_input.source_data)
    source_data.update(forcing_to_source_data(forcing))

    metadata = dict(base_input.metadata)
    cohort_metadata = dict(metadata.get("cohort", {}))
    cohort_metadata["schedule_variation_seed"] = int(occupancy_seed)
    metadata["cohort"] = cohort_metadata
    metadata["climate_member"] = {
        "member_index": int(member_index),
        "base_year": int(base_year),
        "occupancy_seed": int(occupancy_seed),
    }

    first_timestamp = pd.Timestamp(forcing.frame.index[0]).isoformat()
    return replace(
        base_input,
        dataset_id=f"climate-member-{member_index:03d}",
        source_data=source_data,
        timestamp=first_timestamp,
        metadata=metadata,
    )


def _run_climate_member_simulation(input_data: InputDataset, config: Mapping[str, Any]) -> dict[str, Any]:
    """Run the annual sequential loop without the baseline electricity rescaling step."""

    prepared_input = _prepare_reference_year_input(input_data, config=config)
    weather_dataset = prepared_input.source_data["weather"]
    timestamps = tuple(pd.Timestamp(timestamp) for timestamp in weather_dataset.timestamps)

    model_cfg = dict(prepared_input.metadata.get("model", {}))
    indoor_temperature_c = float(model_cfg.get("initial_indoor_temperature_C", prepared_input.T_indoor_initial_C))
    heating_on = bool(model_cfg.get("initial_heating_on", False))

    records: list[dict[str, Any]] = []
    for timestamp in timestamps:
        step_input = _step_input_dataset(
            input_dataset=prepared_input,
            timestamp=timestamp,
            indoor_temperature_c=indoor_temperature_c,
            heating_on=heating_on,
        )
        prepared = build_prepared_forcing(input_dataset=step_input, include_preview=False)
        _, control_state, system_state, outputs = _run_step_layers(prepared, config)

        records.append(
            {
                "timestamp": pd.Timestamp(timestamp),
                "archetype_id": prepared.archetype_id,
                "schedule_state": prepared.schedule_state,
                "occupied_probability": prepared.occupied_probability,
                "T_outdoor_C": prepared.T_outdoor_C,
                "T_set_C": prepared.T_set_C,
                "T_indoor_prev_C": system_state.T_indoor_prev_C,
                "T_indoor_free_float_C": system_state.T_indoor_free_float_C,
                "T_indoor_next_C": system_state.T_indoor_next_C,
                "Q_occ_W": prepared.Q_occ_W,
                "Q_app_W": prepared.Q_app_W,
                "Q_lighting_W": prepared.Q_lighting_W,
                "Q_cooking_W": prepared.Q_cooking_W,
                "Q_internal_gains_W": prepared.Q_internal_gains_W,
                "Q_solar_gains_W": prepared.Q_solar_gains_W,
                "Q_air_W": control_state.Q_air_W,
                "Q_dhw_demand_W": control_state.Q_dhw_demand_W,
                "Q_heating_requested_W": control_state.Q_heating_requested_W,
                "Q_heating_supplied_W": system_state.Q_heating_supplied_W,
                "Q_unmet_heating_W": outputs.Q_unmet_heating_W,
                "Q_excess_heat_W": outputs.Q_excess_heat_W,
                "comfort_violation_degC": outputs.comfort_violation_degC,
                "P_el_total_W": outputs.P_el_total_W,
                "P_el_space_heating_W": outputs.P_el_space_heating_W,
                "P_el_dhw_W": outputs.P_el_dhw_W,
                "P_el_appliances_W": outputs.P_el_appliances_W,
                "P_el_lighting_W": outputs.P_el_lighting_W,
                "P_el_cooking_W": outputs.P_el_cooking_W,
                "heating_on": control_state.heating_on,
                "integration_substeps": int(system_state.metadata.get("integration_substeps", 1)),
            }
        )
        indoor_temperature_c = float(system_state.T_indoor_next_C)
        heating_on = bool(control_state.heating_on)

    frame = pd.DataFrame.from_records(records)
    annual_energy_kwh = integrate_power_series_kwh(frame["P_el_total_W"], timestamps=frame["timestamp"])
    timestep_seconds = _representative_timestep_seconds(frame["timestamp"])
    return {
        "profile_frame": frame,
        "annual_energy_kWh": float(annual_energy_kwh),
        "n_steps": int(len(frame)),
        "timestep_seconds": float(timestep_seconds),
    }


def run_climate_ensemble(
    config: Mapping[str, Any],
    *,
    final: bool = False,
    config_path: str | Path | None = None,
) -> dict[str, Any]:
    """Run the stochastic climate-uncertainty workflow on top of the annual model."""

    require_module_enabled(config, "stochastic", "run_climate_ensemble")
    climate_cfg = dict(config.get("climate", {}))
    climate_inputs = dict(climate_cfg.get("inputs", {}))
    solar_paths = dict(climate_inputs.get("solar_paths", {}))

    weather_df = load_pvgis_weather_csv(climate_inputs["weather_path"])
    solar_series = load_pvgis_solar_csvs(solar_paths)
    weather_years = split_into_years(weather_df)
    solar_years = _solar_years_from_series(solar_series)
    common_years = sorted(set(weather_years).intersection(solar_years))
    if not common_years:
        raise ValueError("No complete PVGIS years are shared between weather and solar inputs.")

    filtered_weather_years = {year: weather_years[year] for year in common_years}
    filtered_solar_years = {year: solar_years[year] for year in common_years}

    members = generate_weather_ensemble(
        year_dict=filtered_weather_years,
        N_members=int(climate_cfg.get("n_members", 50)),
        seed=int(climate_cfg.get("seed", 42)),
        rho=float(climate_cfg.get("rho", 0.8)),
        mu_month=dict(climate_cfg.get("mu_month", {})),
    )

    base_input = load_all_sources(config=config)
    member_rows: list[dict[str, Any]] = []

    for member_index, member_weather in enumerate(members):
        base_year = int(member_weather.attrs["base_year"])
        occupancy_seed = _member_seed(climate_cfg, member_index=member_index)
        forcing = build_forcing(
            weather_df=member_weather,
            solar_dict=filtered_solar_years[base_year],
        )
        member_input = _member_input_dataset(
            base_input=base_input,
            forcing=forcing,
            member_index=member_index,
            base_year=base_year,
            occupancy_seed=occupancy_seed,
        )
        member_results = _run_climate_member_simulation(
            input_data=member_input,
            config=_member_config(config),
        )

        demand_profile = pd.to_numeric(member_results["profile_frame"]["P_el_total_W"], errors="coerce").astype(float)
        hdd18, cdd18 = _compute_degree_days(forcing.temperature_C)
        validation = _member_validation(
            member_weather=member_weather,
            base_weather=filtered_weather_years[base_year],
            forcing=forcing,
        )
        member_rows.append(
            {
                "member_index": int(member_index),
                "base_year": int(base_year),
                "occupancy_seed": int(occupancy_seed),
                "annual_energy_kWh": float(member_results["annual_energy_kWh"]),
                "peak_demand_W": float(demand_profile.max()),
                "p95_demand_W": float(demand_profile.quantile(0.95)),
                "HDD18": float(hdd18),
                "CDD18": float(cdd18),
                **validation,
            }
        )
        LOGGER.info(
            "climate.member member=%s/%s base_year=%s annual_kWh=%.3f peak_W=%.3f",
            member_index + 1,
            len(members),
            base_year,
            float(member_results["annual_energy_kWh"]),
            float(demand_profile.max()),
        )

    member_stats = pd.DataFrame.from_records(member_rows).sort_values("member_index").reset_index(drop=True)
    summary = {
        "member_count": int(len(member_stats)),
        "mode": str(climate_cfg.get("mode", "climate_only")),
        "seed": int(climate_cfg.get("seed", 42)),
        "rho": float(climate_cfg.get("rho", 0.8)),
        "sampled_base_years": member_stats["base_year"].astype(int).tolist(),
        "metrics": {
            "annual_energy_kWh": _summarise_metric(member_stats["annual_energy_kWh"]),
            "peak_demand_W": _summarise_metric(member_stats["peak_demand_W"]),
            "p95_demand_W": _summarise_metric(member_stats["p95_demand_W"]),
            "HDD18": _summarise_metric(member_stats["HDD18"]),
            "CDD18": _summarise_metric(member_stats["CDD18"]),
        },
        "validation": {
            "temperature_distribution_preserved_all": bool(member_stats["temperature_distribution_preserved"].all()),
            "autocorrelation_realistic_all": bool(member_stats["autocorrelation_realistic"].all()),
            "no_timestep_drift_all": bool(member_stats["no_timestep_drift"].all()),
            "no_nan_values_all": bool(member_stats["no_nan_values"].all()),
            "mean_temperature_std_ratio": float(member_stats["temperature_std_ratio"].mean()),
            "mean_temperature_lag1_delta": float(member_stats["temperature_lag1_delta"].mean()),
        },
    }

    output_dir = _climate_output_dir(config, final=final)
    summary_path = write_json(output_dir / "ensemble_summary.json", summary)
    parquet_path = write_frame_parquet(output_dir / "member_stats.parquet", member_stats)
    artifact_paths = {
        "ensemble_summary": str(summary_path),
        "member_stats": str(parquet_path),
    }
    if final:
        manifest_path = write_run_manifest(
            output_dir,
            run_mode="climate_uncertainty",
            config=config,
            artifact_paths=artifact_paths,
            config_path=config_path,
            results=summary,
            extra={
                "member_count": summary["member_count"],
                "sampled_base_years": summary["sampled_base_years"],
            },
        )
        artifact_paths["manifest"] = str(manifest_path)

    print(f"Climate members simulated: {summary['member_count']}")
    print(
        "Annual energy (kWh): "
        f"median={summary['metrics']['annual_energy_kWh']['median']:.3f}, "
        f"P5={summary['metrics']['annual_energy_kWh']['p5']:.3f}, "
        f"P95={summary['metrics']['annual_energy_kWh']['p95']:.3f}, "
        f"IQR={summary['metrics']['annual_energy_kWh']['iqr']:.3f}"
    )
    print(
        "Peak demand (W): "
        f"median={summary['metrics']['peak_demand_W']['median']:.3f}, "
        f"P5={summary['metrics']['peak_demand_W']['p5']:.3f}, "
        f"P95={summary['metrics']['peak_demand_W']['p95']:.3f}, "
        f"IQR={summary['metrics']['peak_demand_W']['iqr']:.3f}"
    )
    print(
        "P95 demand (W): "
        f"median={summary['metrics']['p95_demand_W']['median']:.3f}, "
        f"P5={summary['metrics']['p95_demand_W']['p5']:.3f}, "
        f"P95={summary['metrics']['p95_demand_W']['p95']:.3f}, "
        f"IQR={summary['metrics']['p95_demand_W']['iqr']:.3f}"
    )
    print(
        "Validation checks: "
        f"distribution={summary['validation']['temperature_distribution_preserved_all']}, "
        f"autocorr={summary['validation']['autocorrelation_realistic_all']}, "
        f"drift={summary['validation']['no_timestep_drift_all']}, "
        f"nans={summary['validation']['no_nan_values_all']}"
    )
    print("Saved artifacts:")
    for artifact_name, artifact_path in artifact_paths.items():
        print(f"- {artifact_name}: {artifact_path}")

    return {
        "summary": summary,
        "member_stats": member_stats,
        "artifacts": artifact_paths,
    }


def main(config_path: str | Path | None = None) -> dict[str, object]:
    """Run either the new climate ensemble or the legacy cohort wrapper."""

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    for logger_name in NOISY_CLIMATE_LOGGERS:
        logging.getLogger(logger_name).setLevel(logging.WARNING)
    resolved_config_path = resolve_config_path(config_path)
    config = load_config(config_path=resolved_config_path)
    climate_cfg = dict(config.get("climate", {}))
    if bool(climate_cfg.get("enabled", False)):
        require_module_enabled(config, "stochastic", "run_climate_ensemble")
        return run_climate_ensemble(config=config, final=True, config_path=resolved_config_path)

    require_module_enabled(config, "cohort", "run_model_v3_stochastic cohort branch")
    results = run_cohort_simulation(config=config)
    artifact_paths = persist_cohort_results(
        results=results,
        config=config,
        final=True,
        config_path=resolved_config_path,
    )
    peak_distribution = dict(results.get("peak_distribution", {}))
    dhw_peak_distribution = dict(results.get("peak_dhw_distribution", {}))
    sample_parameter_ranges = dict(results.get("sample_parameter_ranges", {}))

    print(f"N households simulated: {results['n_households']}")
    print(f"Peak diversity factor: {results['diversity_factor']:.3f}")
    print(
        "DHW contribution: "
        f"mean annual thermal={results.get('annual_dhw_thermal_kWh_mean', 0.0):.3f} kWh/household, "
        f"aggregate peak={results.get('aggregated_dhw_peak_W', 0.0):.3f} W"
    )
    print(
        "Uncertainty summary (peak demand): "
        f"mean={peak_distribution.get('mean_peak_W', 0.0):.3f} W, "
        f"std={peak_distribution.get('std_peak_W', 0.0):.3f} W"
    )
    print(
        "DHW peak summary: "
        f"mean={dhw_peak_distribution.get('mean_peak_W', 0.0):.3f} W, "
        f"std={dhw_peak_distribution.get('std_peak_W', 0.0):.3f} W"
    )
    print("Sample parameter ranges:")
    for parameter_name in sorted(sample_parameter_ranges):
        parameter_range = sample_parameter_ranges[parameter_name]
        print(
            f"- {parameter_name}: "
            f"{parameter_range['min']:.3f} to {parameter_range['max']:.3f}"
        )
    print("Saved artifacts:")
    for artifact_name, artifact_path in artifact_paths.items():
        print(f"- {artifact_name}: {artifact_path}")
    print("Physics-finalized stochastic pipeline completed")
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the model_v3 stochastic cohort or climate ensemble pipeline.")
    parser.add_argument("--config", default=None, help="Path to the model_v3 YAML config. Defaults to config/model.yaml.")
    args = parser.parse_args()
    main(config_path=args.config)
