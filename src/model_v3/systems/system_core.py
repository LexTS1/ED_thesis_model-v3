"""Core system execution for model_v3."""

from __future__ import annotations

import logging

from model_v3.baseline import representative_thermal_to_electric_factor
from model_v3.interfaces import ControlState, SystemState
from model_v3.physics.thermal_dynamics import integrate_zone_temperature
from model_v3.systems.electricity import compute_electricity_breakdown
from model_v3.systems.heating_system import dispatch_heating
from model_v3.systems.technology import (
    configured_dhw_technology,
    configured_heating_technology,
    convert_heat_to_carriers,
)
from model_v3.utils.feature_flags import enabled_runtime_modules, require_module_enabled

LOGGER = logging.getLogger(__name__)


def run_systems(control_state: ControlState) -> SystemState:
    """Apply equipment constraints and compute system-level outputs."""

    module_config = {"modules": dict(control_state.metadata.get("modules", {}))}
    require_module_enabled(module_config, "systems", "run_systems")
    LOGGER.info(
        "systems.start timestamp=%s timestep_hours=%.3f",
        control_state.timestamp,
        control_state.timestep_hours,
    )
    timestep_hours = max(control_state.timestep_hours, 1e-9)
    dt_seconds = timestep_hours * 3600.0
    c_j_per_k = max(control_state.C_J_per_K, 1e-9)

    heating_result = dispatch_heating(
        Q_heating_requested_W=control_state.Q_heating_requested_W,
        Q_heating_max_W=control_state.Q_heating_max_W,
        heating_cop=control_state.heating_cop,
    )
    baseline_cfg = dict(control_state.metadata.get("baseline", {}))
    electricity_split_cfg = dict(control_state.metadata.get("electricity_split", {}))
    systems_cfg = dict(control_state.metadata.get("systems", {}))
    technologies_cfg = dict(control_state.metadata.get("technologies", {}))
    representative_config = {
        "baseline": baseline_cfg,
        "electricity_split": electricity_split_cfg,
    }
    heating_electric_factor = representative_thermal_to_electric_factor(representative_config, end_use="space_heating")
    dhw_electric_factor = representative_thermal_to_electric_factor(representative_config, end_use="dhw")
    heating_technology_type = configured_heating_technology(systems_cfg)
    dhw_technology_type = configured_dhw_technology(
        systems_cfg,
        technologies_cfg,
        heating_technology_type,
    )
    active_heating_conversion = heating_technology_type is not None
    active_dhw_conversion = dhw_technology_type is not None

    if active_heating_conversion:
        heating_carriers = convert_heat_to_carriers(
            heating_result["Q_heating_supplied_W"],
            prefix="space_heating",
            technology_type=heating_technology_type or "",
            technologies_cfg=technologies_cfg,
            systems_cfg=systems_cfg,
            source_temperature_c=control_state.T_outdoor_C,
            indoor_setpoint_c=control_state.T_set_C,
            capacity_w=control_state.Q_heating_max_W,
            mode="heating",
        )
        p_el_space_heating_w = float(heating_carriers["P_el_space_heating_technology_W"])
    else:
        heating_carriers = convert_heat_to_carriers(
            heating_result["Q_heating_supplied_W"],
            prefix="space_heating",
            technology_type="resistive_direct",
            technologies_cfg=technologies_cfg,
            systems_cfg=systems_cfg,
            source_temperature_c=control_state.T_outdoor_C,
            indoor_setpoint_c=control_state.T_set_C,
            capacity_w=control_state.Q_heating_max_W,
            mode="heating",
        )
        heating_carriers["P_el_space_heating_technology_W"] = heating_result["P_el_space_heating_W"]
        p_el_space_heating_w = heating_result["Q_heating_supplied_W"] * heating_electric_factor

    if active_dhw_conversion:
        dhw_carriers = convert_heat_to_carriers(
            max(control_state.Q_dhw_demand_W, 0.0),
            prefix="dhw",
            technology_type=dhw_technology_type or "",
            technologies_cfg=technologies_cfg,
            systems_cfg=systems_cfg,
            source_temperature_c=control_state.T_outdoor_C,
            indoor_setpoint_c=control_state.T_set_C,
            capacity_w=max(control_state.Q_dhw_demand_W, 0.0),
            mode="heating",
        )
        p_el_dhw_w = float(dhw_carriers["P_el_dhw_technology_W"])
    else:
        dhw_carriers = convert_heat_to_carriers(
            max(control_state.Q_dhw_demand_W, 0.0),
            prefix="dhw",
            technology_type="electric_storage",
            technologies_cfg=technologies_cfg,
            systems_cfg=systems_cfg,
            source_temperature_c=control_state.T_outdoor_C,
            indoor_setpoint_c=control_state.T_set_C,
            capacity_w=max(control_state.Q_dhw_demand_W, 0.0),
            mode="heating",
        )
        dhw_carriers["P_el_dhw_technology_W"] = max(control_state.Q_dhw_demand_W, 0.0) / max(control_state.dhw_cop, 1e-9)
        p_el_dhw_w = max(control_state.Q_dhw_demand_W, 0.0) * dhw_electric_factor

    p_el_gross_actual_w = (
        float(heating_carriers["P_el_space_heating_technology_W"])
        + float(dhw_carriers["P_el_dhw_technology_W"])
        + max(control_state.P_el_appliances_W, 0.0)
        + max(control_state.P_el_lighting_W, 0.0)
        + max(control_state.P_el_cooking_W, 0.0)
        + max(control_state.P_el_ev_charging_W, 0.0)
    )
    p_el_net_grid_w = p_el_gross_actual_w - max(control_state.P_pv_generation_W, 0.0)
    p_el_grid_import_w = max(p_el_net_grid_w, 0.0)
    p_el_grid_export_w = max(-p_el_net_grid_w, 0.0)
    integrated = integrate_zone_temperature(
        t_initial_c=control_state.T_indoor_prev_C,
        t_outdoor_c=control_state.T_outdoor_C,
        envelope_loss_w_per_k=control_state.heat_loss_coefficient_W_per_C,
        airflow_loss_w_per_k=control_state.H_ve_W_per_K,
        c_j_per_k=c_j_per_k,
        dt_seconds=dt_seconds,
        q_internal_gains_w=control_state.Q_internal_gains_W,
        q_solar_gains_w=control_state.Q_solar_gains_W,
        q_heating_w=heating_result["Q_heating_supplied_W"],
    )
    t_indoor_next_c = float(integrated["t_next_c"])

    heating_shortfall_deg_c = max(control_state.T_min_C - t_indoor_next_c, 0.0)
    excess_heat_deg_c = max(t_indoor_next_c - control_state.T_max_C, 0.0)
    q_excess_heat_w = excess_heat_deg_c * c_j_per_k / dt_seconds
    comfort_violation_deg_c = max(heating_shortfall_deg_c, excess_heat_deg_c)
    comfort_violation_degree_hours = comfort_violation_deg_c * timestep_hours
    electricity = compute_electricity_breakdown(
        thermal_system={
            "P_el_space_heating_W": p_el_space_heating_w,
            "P_el_dhw_W": p_el_dhw_w,
        },
        load_profiles={
            "P_el_appliances_W": control_state.P_el_appliances_W,
            "P_el_lighting_W": control_state.P_el_lighting_W,
            "P_el_cooking_W": control_state.P_el_cooking_W,
            "P_el_ev_charging_W": control_state.P_el_ev_charging_W,
        },
    )

    system_state = SystemState(
        enabled_modules=enabled_runtime_modules(module_config),
        timestamp=control_state.timestamp,
        archetype_id=control_state.archetype_id,
        schedule_state=control_state.schedule_state,
        T_indoor_prev_C=control_state.T_indoor_prev_C,
        T_indoor_free_float_C=control_state.T_indoor_free_float_C,
        T_indoor_next_C=t_indoor_next_c,
        heating_on=control_state.heating_on,
        t_set_low_c=control_state.t_set_low_c,
        t_set_high_c=control_state.t_set_high_c,
        Vdot_inf_m3_per_s=control_state.Vdot_inf_m3_per_s,
        Vdot_vent_m3_per_s=control_state.Vdot_vent_m3_per_s,
        Vdot_total_m3_per_s=control_state.Vdot_total_m3_per_s,
        H_ve_W_per_K=control_state.H_ve_W_per_K,
        Q_air_W=control_state.Q_air_W,
        Q_heating_requested_W=control_state.Q_heating_requested_W,
        Q_heating_supplied_W=heating_result["Q_heating_supplied_W"],
        Q_heating_max_W=control_state.Q_heating_max_W,
        Q_unmet_heating_W=heating_result["Q_unmet_heating_W"],
        Q_excess_heat_W=q_excess_heat_w,
        comfort_violation_degC=comfort_violation_deg_c,
        comfort_violation_degree_hours=comfort_violation_degree_hours,
        P_el_space_heating_technology_W=float(heating_carriers["P_el_space_heating_technology_W"]),
        P_el_dhw_technology_W=float(dhw_carriers["P_el_dhw_technology_W"]),
        P_el_space_heating_W=electricity["P_el_space_heating_W"],
        P_el_dhw_W=electricity["P_el_dhw_W"],
        P_el_appliances_W=electricity["P_el_appliances_W"],
        P_el_lighting_W=electricity["P_el_lighting_W"],
        P_el_cooking_W=electricity["P_el_cooking_W"],
        P_el_ev_charging_W=electricity["P_el_ev_charging_W"],
        P_el_total_W=electricity["P_el_total_W"],
        P_pv_generation_W=max(control_state.P_pv_generation_W, 0.0),
        P_el_gross_actual_W=p_el_gross_actual_w,
        P_el_net_grid_W=p_el_net_grid_w,
        P_el_grid_import_W=p_el_grid_import_w,
        P_el_grid_export_W=p_el_grid_export_w,
        P_gas_space_heating_W=float(heating_carriers["P_gas_space_heating_W"]),
        P_gas_dhw_W=float(dhw_carriers["P_gas_dhw_W"]),
        P_oil_space_heating_W=float(heating_carriers["P_oil_space_heating_W"]),
        P_oil_dhw_W=float(dhw_carriers["P_oil_dhw_W"]),
        P_biomass_space_heating_W=float(heating_carriers["P_biomass_space_heating_W"]),
        P_biomass_dhw_W=float(dhw_carriers["P_biomass_dhw_W"]),
        P_propane_space_heating_W=float(heating_carriers["P_propane_space_heating_W"]),
        P_propane_dhw_W=float(dhw_carriers["P_propane_dhw_W"]),
        P_coal_space_heating_W=float(heating_carriers["P_coal_space_heating_W"]),
        P_coal_dhw_W=float(dhw_carriers["P_coal_dhw_W"]),
        P_district_heat_space_heating_W=float(heating_carriers["P_district_heat_space_heating_W"]),
        P_district_heat_dhw_W=float(dhw_carriers["P_district_heat_dhw_W"]),
        metadata={
            "control_state_id": control_state.state_id,
            "temperature_clamped": False,
            "system_shape": {"rows": 1, "columns": 1},
            "net_passive_balance_W": control_state.Q_passive_balance_W,
            "integration_substeps": int(integrated["substeps"]),
            "substep_stability_factor": float(integrated["substep_stability_factor"]),
            "raw_P_el_space_heating_W": heating_result["P_el_space_heating_W"],
            "raw_P_el_dhw_W": max(control_state.Q_dhw_demand_W, 0.0) / max(control_state.dhw_cop, 1e-9),
            "representative_heating_electric_factor": heating_electric_factor,
            "representative_dhw_electric_factor": dhw_electric_factor,
            "active_heating_conversion": active_heating_conversion,
            "active_dhw_conversion": active_dhw_conversion,
            "heating_technology_type": heating_technology_type or "legacy_representative",
            "dhw_technology_type": dhw_technology_type or "legacy_representative",
            "heating_energy_carrier": heating_carriers.get("energy_carrier"),
            "dhw_energy_carrier": dhw_carriers.get("energy_carrier"),
            "heating_heat_pump_cop": heating_carriers.get("heat_pump_cop"),
            "heating_heat_pump_cop_base": heating_carriers.get("heat_pump_cop_base"),
            "heating_heat_pump_emitter_type": heating_carriers.get("heat_pump_emitter_type"),
            "heating_heat_pump_refrigerant": heating_carriers.get("heat_pump_refrigerant"),
            "heating_heat_pump_source_temperature_C": heating_carriers.get("heat_pump_source_temperature_C"),
            "heating_heat_pump_sink_temperature_C": heating_carriers.get("heat_pump_sink_temperature_C"),
            "heating_heat_pump_defrost_factor": heating_carriers.get("heat_pump_defrost_factor"),
            "heating_heat_pump_part_load_ratio": heating_carriers.get("heat_pump_part_load_ratio"),
            "heating_heat_pump_part_load_factor": heating_carriers.get("heat_pump_part_load_factor"),
            "heating_heat_pump_capacity_available_fraction": heating_carriers.get("heat_pump_capacity_available_fraction"),
            "dhw_heat_pump_cop": dhw_carriers.get("heat_pump_cop"),
            "dhw_heat_pump_refrigerant": dhw_carriers.get("heat_pump_refrigerant"),
            "dhw_heat_pump_source_temperature_C": dhw_carriers.get("heat_pump_source_temperature_C"),
            "dhw_heat_pump_sink_temperature_C": dhw_carriers.get("heat_pump_sink_temperature_C"),
            "technology_sources": dict(control_state.metadata.get("technology_sources", {})),
        },
    )
    LOGGER.info(
        "systems.end state_shape=1x1 T_indoor_next_C=%.3f P_el_total_W=%.3f substeps=%s",
        system_state.T_indoor_next_C,
        system_state.P_el_total_W,
        integrated["substeps"],
    )
    return system_state
