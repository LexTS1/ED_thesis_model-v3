"""Loading and preprocessing entrypoints for model_v3 input data."""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from typing import Any, Mapping

from model_v3.data.harmonisation import harmonise_timeseries
from model_v3.data.loaders import (
    load_building_inputs,
    load_occupancy_spec,
    load_source_internal_gains,
    load_source_load_profiles,
    load_source_solar,
    load_source_weather,
)
from model_v3.data.preprocessing import reconstruct_missing_data
from model_v3.interfaces import InputDataset
from model_v3.utils.config import resolve_household_count

LOGGER = logging.getLogger(__name__)


def _as_float(mapping: Mapping[str, Any], key: str, default: float) -> float:
    """Safely coerce a config value to float with a deterministic fallback."""

    value = mapping.get(key, default)
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _source_alignment_methods(config: Mapping[str, Any]) -> dict[str, str]:
    """Return harmonisation methods per source with conservative defaults."""

    harmonisation_cfg = dict(dict(config.get("data", {})).get("harmonisation", {}))
    configured = dict(harmonisation_cfg.get("methods", {}))
    return {
        "weather": str(configured.get("weather", "forward_fill")),
        "load_profiles": str(configured.get("load_profiles", "mean")),
        "internal_gains": str(configured.get("internal_gains", "mean")),
        "solar": str(configured.get("solar", "mean")),
    }


def _source_reconstruction_methods(config: Mapping[str, Any]) -> dict[str, str]:
    """Return reconstruction methods per source after harmonisation."""

    reconstruction_cfg = dict(dict(config.get("data", {})).get("reconstruction", {}))
    configured = dict(reconstruction_cfg.get("methods", {}))
    return {
        "weather": str(configured.get("weather", "forward_fill")),
        "load_profiles": str(configured.get("load_profiles", "zero_fill")),
        "internal_gains": str(configured.get("internal_gains", "zero_fill")),
        "solar": str(configured.get("solar", "zero_fill")),
    }


def _json_dumps(mapping: Mapping[str, Any]) -> str:
    """Serialise a mapping deterministically for cache keys."""

    return json.dumps(dict(mapping), sort_keys=True)


@lru_cache(maxsize=16)
def _load_cached_processed_source(
    source_name: str,
    source_cfg_json: str,
    end_use_cfg_json: str,
    target_resolution_seconds: int,
    alignment_method: str,
    reconstruction_method: str,
    simulation_start_timestamp: str,
) -> Any:
    """Load, harmonise, and reconstruct a static file-backed source once per config."""

    source_cfg = json.loads(source_cfg_json)
    mini_config: dict[str, Any] = {
        "simulation": {"start_timestamp": simulation_start_timestamp},
        "data": {
            "target_resolution_seconds": target_resolution_seconds,
            "sources": {source_name: source_cfg},
        },
    }
    if source_name == "load_profiles":
        mini_config["data"]["sources"]["end_use_shares"] = json.loads(end_use_cfg_json)

    loader_map = {
        "weather": load_source_weather,
        "load_profiles": load_source_load_profiles,
        "solar": load_source_solar,
    }
    raw_dataset = loader_map[source_name](config=mini_config)
    harmonised = harmonise_timeseries(
        df=raw_dataset,
        target_resolution_seconds=target_resolution_seconds,
        method=alignment_method,
    )
    return reconstruct_missing_data(df=harmonised, method=reconstruction_method)


def load_all_sources(config: Mapping[str, Any] | None = None) -> InputDataset:
    """Load, harmonise, and reconstruct all sources before adapter-layer merging."""

    config = config or {}
    simulation_cfg = dict(config.get("simulation", {}))
    data_cfg = dict(config.get("data", {}))
    forcing_cfg = dict(config.get("forcing", {}))
    building_cfg = dict(config.get("building", {}))
    dhw_cfg = dict(forcing_cfg.get("dhw", {}))
    comfort_cfg = dict(config.get("comfort", {}))
    heating_cfg = dict(dict(config.get("systems", {})).get("heating", {}))
    dhw_system_cfg = dict(dict(config.get("systems", {})).get("dhw", {}))
    target_resolution_seconds = int(data_cfg.get("target_resolution_seconds", 3600))

    LOGGER.info("data.start target_resolution_seconds=%s", target_resolution_seconds)

    building_inputs = load_building_inputs(config=config)
    occupancy_spec = load_occupancy_spec(config=config)
    if building_inputs:
        LOGGER.info(
            "data.building.loaded archetype=%s H_W_per_K=%s",
            building_inputs.get("selected_archetype_id"),
            building_inputs.get("heat_loss_coefficient_W_per_C"),
        )

    source_loaders = {
        "weather": load_source_weather,
        "load_profiles": load_source_load_profiles,
        "internal_gains": load_source_internal_gains,
        "solar": load_source_solar,
    }
    alignment_methods = _source_alignment_methods(config)
    reconstruction_methods = _source_reconstruction_methods(config)
    processed_sources = {}
    provenance = {}
    end_use_cfg_json = _json_dumps(dict(data_cfg.get("sources", {})).get("end_use_shares", {}))

    for source_name, loader in source_loaders.items():
        source_cfg = dict(data_cfg.get("sources", {})).get(source_name, {})
        if source_name in {"weather", "load_profiles", "solar"} and (source_cfg.get("file_path") or source_cfg.get("raw_dir")):
            reconstructed = _load_cached_processed_source(
                source_name=source_name,
                source_cfg_json=_json_dumps(source_cfg),
                end_use_cfg_json=end_use_cfg_json,
                target_resolution_seconds=target_resolution_seconds,
                alignment_method=alignment_methods[source_name],
                reconstruction_method=reconstruction_methods[source_name],
                simulation_start_timestamp=str(simulation_cfg.get("start_timestamp", "2026-01-01T00:00:00+01:00")),
            )
        else:
            dataset = loader(config=config)
            LOGGER.info(
                "data.source.loaded source=%s shape=%s original_timestep_seconds=%s",
                source_name,
                dataset.shape,
                dataset.metadata.get("original_timestep_seconds"),
            )
            harmonised = harmonise_timeseries(
                df=dataset,
                target_resolution_seconds=target_resolution_seconds,
                method=alignment_methods[source_name],
            )
            reconstructed = reconstruct_missing_data(
                df=harmonised,
                method=reconstruction_methods[source_name],
            )

        processed_sources[source_name] = reconstructed
        provenance[source_name] = {
            "source_name": reconstructed.metadata.get("source_name"),
            "original_resolution": reconstructed.metadata.get("original_resolution"),
            "target_resolution": reconstructed.metadata.get("target_resolution"),
            "alignment_method": reconstructed.metadata.get("alignment_method"),
            "reconstruction_method": reconstructed.metadata.get("reconstruction_method"),
            "reconstruction_confidence": reconstructed.metadata.get("reconstruction_confidence"),
        }
        LOGGER.info(
            "data.source.processed source=%s shape=%s target_timestep_seconds=%s reconstruction_method=%s",
            source_name,
            reconstructed.shape,
            reconstructed.metadata.get("target_timestep_seconds"),
            reconstructed.metadata.get("reconstruction_method"),
        )

    timestamp = str(simulation_cfg.get("start_timestamp", "2023-12-01T01:00:00+01:00"))
    household_count = resolve_household_count(config=config, default=0, logger=LOGGER)

    return InputDataset(
        household_count=household_count,
        source_data=processed_sources,
        target_resolution_seconds=target_resolution_seconds,
        timestep_hours=target_resolution_seconds / 3600.0,
        timestamp=timestamp,
        archetype_id=str(building_inputs.get("archetype_id", "")),
        T_outdoor_C=_as_float(forcing_cfg, "T_outdoor_C", 5.0),
        T_indoor_initial_C=_as_float(forcing_cfg, "T_indoor_initial_C", 17.0),
        T_set_C=float(building_inputs.get("T_set_C", _as_float(forcing_cfg, "T_set_C", 21.0))),
        T_min_C=float(building_inputs.get("T_min_C", _as_float(comfort_cfg, "T_min_C", 18.0))),
        T_max_C=float(building_inputs.get("T_max_C", _as_float(comfort_cfg, "T_max_C", 26.0))),
        heat_loss_coefficient_W_per_C=float(
            building_inputs.get(
                "heat_loss_coefficient_W_per_C",
                _as_float(building_cfg, "heat_loss_coefficient_W_per_C", 180.0),
            )
        ),
        thermal_mass_Wh_per_C=float(
            building_inputs.get(
                "thermal_mass_Wh_per_C",
                _as_float(building_cfg, "thermal_mass_Wh_per_C", 4500.0),
            )
        ),
        C_J_per_K=float(
            building_inputs.get(
                "C_J_per_K",
                _as_float(building_cfg, "thermal_mass_Wh_per_C", 4500.0) * 3600.0,
            )
        ),
        volume_m3=float(building_inputs.get("volume_m3", 250.0)),
        occupants_per_dwelling=float(building_inputs.get("occupants_per_dwelling", 2.0)),
        occupant_gain_away_W_per_person=float(building_inputs.get("occupant_gain_away_W_per_person", 0.0)),
        occupant_gain_awake_W_per_person=float(building_inputs.get("occupant_gain_awake_W_per_person", 70.0)),
        occupant_gain_sleep_W_per_person=float(building_inputs.get("occupant_gain_sleep_W_per_person", 60.0)),
        appliance_heat_gain_fraction=float(building_inputs.get("appliance_heat_gain_fraction", 0.7)),
        lighting_heat_gain_fraction=float(building_inputs.get("lighting_heat_gain_fraction", 0.85)),
        cooking_heat_gain_fraction=float(building_inputs.get("cooking_heat_gain_fraction", 0.5)),
        ACH_inf=float(building_inputs.get("ACH_inf", 0.5)),
        ACH_vent_base=float(building_inputs.get("ACH_vent_base", 0.2)),
        ACH_vent_occupied=float(building_inputs.get("ACH_vent_occupied", 0.3)),
        ventilation_type=str(building_inputs.get("ventilation_type", "mechanical_extract")),
        eta_HRV=float(building_inputs.get("eta_HRV", 0.0)),
        glazing_ratio=float(building_inputs.get("glazing_ratio", 0.16)),
        g_value=float(building_inputs.get("g_value", 0.63)),
        frame_fraction=float(building_inputs.get("frame_fraction", 1.0)),
        dirt_factor=float(building_inputs.get("dirt_factor", 0.95)),
        incidence_factor=float(building_inputs.get("incidence_factor", 0.9)),
        shading_factor=float(building_inputs.get("shading_factor", 0.77)),
        orientation_share_north=float(building_inputs.get("orientation_share_north", 0.2)),
        orientation_share_east=float(building_inputs.get("orientation_share_east", 0.25)),
        orientation_share_south=float(building_inputs.get("orientation_share_south", 0.35)),
        orientation_share_west=float(building_inputs.get("orientation_share_west", 0.2)),
        Q_heating_max_W=_as_float(heating_cfg, "capacity_W", 8000.0),
        heating_cop=_as_float(heating_cfg, "cop", 1.0),
        dhw_cop=_as_float(dhw_system_cfg, "cop", 1.0),
        Q_dhw_demand_W=_as_float(dhw_cfg, "demand_W", 0.0),
        metadata={
            "config_keys": sorted((config or {}).keys()),
            "modules": dict(config.get("modules", {})),
            "source_provenance": provenance,
            "dataset_shape": {name: dataset.shape for name, dataset in processed_sources.items()},
            "building_inputs": building_inputs,
            "occupancy_spec": occupancy_spec,
            "control": dict(config.get("control", {})),
            "setpoints": dict(config.get("setpoints", {})),
            "model": dict(config.get("model", {})),
            "air": dict(config.get("air", {})),
            "ventilation": dict(config.get("ventilation", {})),
            "cohort": dict(config.get("cohort", {})),
            "baseline": dict(config.get("baseline", {})),
            "electricity_split": dict(config.get("electricity_split", {})),
            "thermal_split": dict(config.get("thermal_split", {})),
            "systems": dict(config.get("systems", {})),
            "technology_baseline": dict(config.get("technology_baseline", {})),
            "technologies": dict(config.get("technologies", {})),
            "technology_sources": dict(config.get("technology_sources", {})),
            "der": dict(config.get("der", {})),
            "mobility": dict(config.get("mobility", {})),
            "heat_pump": dict(config.get("heat_pump", {})),
            "validation": dict(config.get("validation", {})),
        },
    )


def load_input_dataset(config: Mapping[str, Any] | None = None, cohort_context: Mapping[str, Any] | None = None) -> InputDataset:
    """Compatibility wrapper around the stricter Phase 2 data loading entry point."""

    _ = cohort_context
    return load_all_sources(config=config)
