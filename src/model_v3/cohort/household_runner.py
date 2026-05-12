"""Single-household wrapper used by the cohort simulation engine."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from typing import Any, Mapping

import pandas as pd

from model_v3.cohort.household_simulator import simulate_household_electricity
from model_v3.data.data_module import load_all_sources
from model_v3.interfaces import InputDataset
from model_v3.simulation.annual_runner import run_annual_simulation_from_input_data
from model_v3.systems.technology import normalize_technology_type
from model_v3.utils.feature_flags import is_module_enabled


def _prepare_household_input_dataset(
    base_config: Mapping[str, Any],
    sampled_params: Mapping[str, Any],
) -> tuple[InputDataset, dict[str, Any]]:
    """Load deterministic sources, then apply household-specific stochastic perturbations."""

    household_config = deepcopy(dict(base_config))
    physical = dict(sampled_params.get("physical", {}))
    behaviour = dict(sampled_params.get("behaviour", {}))
    technology = dict(sampled_params.get("technology", {}))
    building_cfg = household_config.setdefault("building", {})
    forcing_cfg = household_config.setdefault("forcing", {})
    loads_cfg = forcing_cfg.setdefault("electric_loads_W", {})
    dhw_cfg = forcing_cfg.setdefault("dhw", {})
    comfort_cfg = household_config.setdefault("comfort", {})
    setpoints_cfg = household_config.setdefault("setpoints", {})
    systems_cfg = household_config.setdefault("systems", {})
    heating_cfg = systems_cfg.setdefault("heating", {})
    der_cfg = household_config.setdefault("der", {})
    pv_cfg = der_cfg.setdefault("pv", {})

    UA_scale_factor = max(float(physical.get("UA_scale_factor", 1.0)), 0.1)
    thermal_mass_scale = max(float(physical.get("thermal_mass_scale", 1.0)), 0.1)
    infiltration_rate = max(float(physical.get("infiltration_rate", 1.0)), 0.1)
    cop_scale = max(float(physical.get("cop_scale", 1.0)), 0.1)
    occupancy_intensity = max(float(behaviour.get("occupancy_intensity", 1.0)), 0.0)
    appliance_intensity_scale = max(float(behaviour.get("appliance_intensity_scale", 1.0)), 0.0)
    occupants_per_dwelling = max(float(behaviour.get("occupants_per_dwelling", 2.0)), 1.0)
    setpoint_shift_C = float(behaviour.get("setpoint_shift_C", 0.0))
    schedule_variation_seed = int(behaviour.get("schedule_variation_seed", 0))
    heating_capacity_scale = max(float(technology.get("heating_capacity_scale", 1.0)), 0.1)
    technology_type = normalize_technology_type(technology.get("technology_type", "resistive_direct"))

    building_cfg["ua_multiplier"] = float(building_cfg.get("ua_multiplier", 1.0)) * UA_scale_factor
    building_cfg["thermal_mass_multiplier"] = float(building_cfg.get("thermal_mass_multiplier", 1.0)) * thermal_mass_scale
    building_cfg["infiltration_rate_multiplier"] = infiltration_rate
    building_cfg["occupants_per_dwelling"] = occupants_per_dwelling
    forcing_cfg["T_set_C"] = float(forcing_cfg.get("T_set_C", 21.0)) + setpoint_shift_C
    for key, default in (("occupied_day", 21.0), ("sleeping", 17.0), ("away", 16.0)):
        setpoints_cfg[key] = float(setpoints_cfg.get(key, default)) + setpoint_shift_C
    comfort_cfg["T_min_C"] = float(comfort_cfg.get("T_min_C", 18.0)) + setpoint_shift_C
    comfort_cfg["T_max_C"] = float(comfort_cfg.get("T_max_C", 26.0)) + setpoint_shift_C
    heating_cfg["capacity_W"] = float(heating_cfg.get("capacity_W", 8000.0)) * heating_capacity_scale
    heating_cfg["technology_type"] = technology_type
    if technology_type in {"air_water", "air_air", "ground_source"}:
        heating_cfg["cop"] = float(technology.get("heating_cop", heating_cfg.get("cop", 3.0))) * cop_scale
    elif technology_type in {"resistive_direct", "storage_heater"}:
        heating_cfg["cop"] = 1.0
    if bool(technology.get("has_pv", False)):
        pv_cfg["enabled"] = True
        pv_cfg["system_size_kwp"] = {
            "base": float(technology.get("pv_capacity_kwp", pv_cfg.get("system_size_kwp", {}).get("base", 6.0) if isinstance(pv_cfg.get("system_size_kwp"), dict) else 6.0))
        }

    cohort_metadata = household_config.setdefault("cohort", {})
    cohort_metadata["schedule_variation_seed"] = schedule_variation_seed
    cohort_metadata["occupancy_time_shift_hours"] = float(behaviour.get("occupancy_time_shift_hours", 0.0))
    cohort_metadata["transition_variability_scale"] = float(behaviour.get("transition_variability_scale", 1.0))
    cohort_metadata["state_duration_scale"] = float(behaviour.get("state_duration_scale", 1.0))
    cohort_metadata["occupancy_state_biases"] = dict(behaviour.get("occupancy_state_biases", {}))
    cohort_metadata["household_class"] = str(behaviour.get("household_class", "low_flat"))
    cohort_metadata["occupants_per_dwelling"] = occupants_per_dwelling
    cohort_metadata["household_size_activity_scale"] = float(behaviour.get("household_size_activity_scale", 1.0))
    cohort_metadata["household_random_effect_u"] = float(behaviour.get("household_random_effect_u", 0.0))
    cohort_metadata["has_dryer"] = bool(behaviour.get("has_dryer", False))
    cohort_metadata["has_ev"] = bool(behaviour.get("has_ev", False))
    cohort_metadata["has_pv"] = bool(technology.get("has_pv", False))
    cohort_metadata["pv_capacity_kwp"] = float(technology.get("pv_capacity_kwp", 0.0))
    cohort_metadata["sampled_params"] = dict(sampled_params)

    input_data = load_all_sources(config=household_config)
    if is_module_enabled(household_config, "stochastic"):
        stochastic_input_data, stochastic_diagnostics = simulate_household_electricity(
            input_data=input_data,
            config=household_config,
            sampled_params=sampled_params,
        )
    else:
        stochastic_input_data = input_data
        stochastic_diagnostics = {
            "stochastic_enabled": False,
            "message": "modules.stochastic=false; household stochastic profile generation skipped.",
        }

    scaled_internal_gains = replace(
        stochastic_input_data.source_data["internal_gains"],
        columns={
            "Q_internal_gains_W": tuple(
                (0.0 if value is None else float(value) * occupancy_intensity)
                for value in stochastic_input_data.source_data["internal_gains"].columns.get("Q_internal_gains_W", ())
            )
        },
    )
    source_data = dict(stochastic_input_data.source_data)
    source_data["internal_gains"] = scaled_internal_gains
    return replace(
        stochastic_input_data,
        source_data=source_data,
        Q_dhw_demand_W=0.0,
        metadata={
            **stochastic_input_data.metadata,
            "cohort": cohort_metadata,
            "stochastic_load_diagnostics": stochastic_diagnostics,
        },
    ), household_config


def run_single_household(base_config: Mapping[str, Any], sampled_params: Mapping[str, Any]) -> dict[str, Any]:
    """Apply sampled household parameters and run the deterministic annual pipeline."""

    household_input, household_config = _prepare_household_input_dataset(
        base_config=base_config,
        sampled_params=sampled_params,
    )
    outputs = run_annual_simulation_from_input_data(input_data=household_input, config=household_config)

    stochastic_household = dict(household_input.metadata.get("stochastic_household", {}))
    profile_frame = pd.DataFrame(outputs["profile_frame"]).copy()
    for column_name, metadata_key in (
        ("P_base_W", "base_profile_W"),
        ("P_events_W", "event_profile_W"),
        ("P_lighting_household_W", "lighting_profile_W"),
        ("P_nonthermal_W", "nonthermal_profile_W"),
        ("Q_dhw_profile_W", "dhw_profile_W"),
        ("P_el_ev_charging_W", "ev_charging_profile_W"),
    ):
        values = list(stochastic_household.get(metadata_key, ()))
        if len(values) == len(profile_frame):
            profile_frame[column_name] = values
    profile_frame["Q_total_thermal_W"] = (
        pd.to_numeric(profile_frame["Q_heating_supplied_W"], errors="coerce").fillna(0.0)
        + pd.to_numeric(profile_frame["Q_dhw_demand_W"], errors="coerce").fillna(0.0)
    )
    outputs["profile_frame"] = profile_frame
    outputs["household_electricity_calibration"] = dict(outputs.get("electricity_calibration", {}))
    outputs["stochastic_household"] = stochastic_household
    outputs["household_event_summary"] = dict(stochastic_household.get("event_summary", {}))
    outputs["household_event_log"] = list(stochastic_household.get("event_log", []))
    outputs["household_dhw_summary"] = dict(stochastic_household.get("dhw_event_summary", {}))
    outputs["household_dhw_log"] = list(stochastic_household.get("dhw_event_log", []))
    outputs["household_dhw_profile"] = profile_frame["Q_dhw_demand_W"].astype(float).tolist()
    outputs["household_thermal_parameters"] = dict(stochastic_household.get("thermal_parameters", {}))
    outputs["household_control_schedule"] = dict(stochastic_household.get("control_schedule", {}))
    outputs["peak_dhw_load_W"] = float(profile_frame["Q_dhw_demand_W"].max()) if not profile_frame.empty else 0.0
    outputs["peak_total_thermal_W"] = float(profile_frame["Q_total_thermal_W"].max()) if not profile_frame.empty else 0.0
    return outputs
