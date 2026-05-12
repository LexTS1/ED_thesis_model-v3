"""Core physics execution for model_v3."""

from __future__ import annotations

import logging

from model_v3.interfaces import PhysicsState, PreparedForcing
from model_v3.physics.thermal_dynamics import compute_stability_metadata, integrate_zone_temperature
from model_v3.utils.feature_flags import require_module_enabled

LOGGER = logging.getLogger(__name__)


def _compute_ventilation_ach(occupied: bool, ach_vent_base: float, ach_vent_occupied: float) -> float:
    """Resolve the baseline ventilation ACH before any window-opening boost."""

    return float(ach_vent_occupied) if bool(occupied) else float(ach_vent_base)


def _compute_flow_m3_per_s(volume_m3: float, ach: float) -> float:
    """Convert ACH to volumetric flow."""

    return max(float(ach), 0.0) * max(float(volume_m3), 0.0) / 3600.0


def _effective_ventilation_flow(vdot_inf_m3_per_s: float, vdot_vent_m3_per_s: float, ventilation_type: str, eta_hrv: float) -> float:
    """Apply heat recovery to the mechanical ventilation portion only."""

    if str(ventilation_type).strip().lower() == "balanced":
        return float(vdot_inf_m3_per_s) + (1.0 - max(min(float(eta_hrv), 1.0), 0.0)) * float(vdot_vent_m3_per_s)
    return float(vdot_inf_m3_per_s) + float(vdot_vent_m3_per_s)


def _compute_airflow_heat_loss_w(
    t_in_c: float,
    t_out_c: float,
    effective_vdot_total_m3_per_s: float,
    rho_kg_per_m3: float,
    cp_j_per_kg_k: float,
) -> float:
    """Compute sensible heat exchange due to infiltration and ventilation."""

    return float(rho_kg_per_m3) * float(cp_j_per_kg_k) * float(effective_vdot_total_m3_per_s) * (float(t_in_c) - float(t_out_c))


def run_physics(prepared_forcing: PreparedForcing) -> PhysicsState:
    """Run the deterministic no-heat thermal response from prepared forcing only."""

    require_module_enabled(
        {"modules": dict(prepared_forcing.metadata.get("modules", {}))},
        "physics",
        "run_physics",
    )
    LOGGER.info(
        "physics.start timeline=%s timestep_hours=%.3f",
        prepared_forcing.timeline_label,
        prepared_forcing.timestep_hours,
    )
    timestep_hours = max(prepared_forcing.timestep_hours, 1e-9)
    dt_seconds = timestep_hours * 3600.0
    c_j_per_k = max(prepared_forcing.C_J_per_K, 1e-9)
    occupied = prepared_forcing.occupied_probability >= 0.5
    rho_kg_per_m3 = float(prepared_forcing.metadata.get("air_cfg", {}).get("rho", 1.2))
    cp_j_per_kg_k = float(prepared_forcing.metadata.get("air_cfg", {}).get("cp", 1000.0))

    ach_vent = _compute_ventilation_ach(
        occupied=occupied,
        ach_vent_base=prepared_forcing.ACH_vent_base,
        ach_vent_occupied=prepared_forcing.ACH_vent_occupied,
    )
    vdot_inf = _compute_flow_m3_per_s(prepared_forcing.volume_m3, prepared_forcing.ACH_inf)
    vdot_vent = _compute_flow_m3_per_s(prepared_forcing.volume_m3, ach_vent)
    effective_vdot = _effective_ventilation_flow(
        vdot_inf_m3_per_s=vdot_inf,
        vdot_vent_m3_per_s=vdot_vent,
        ventilation_type=prepared_forcing.ventilation_type,
        eta_hrv=prepared_forcing.eta_HRV,
    )
    h_ve = rho_kg_per_m3 * cp_j_per_kg_k * effective_vdot
    stability = compute_stability_metadata(
        total_loss_w_per_k=prepared_forcing.heat_loss_coefficient_W_per_C + h_ve,
        c_j_per_k=c_j_per_k,
        dt_seconds=dt_seconds,
    )

    q_air_w = _compute_airflow_heat_loss_w(
        t_in_c=prepared_forcing.T_indoor_initial_C,
        t_out_c=prepared_forcing.T_outdoor_C,
        effective_vdot_total_m3_per_s=effective_vdot,
        rho_kg_per_m3=rho_kg_per_m3,
        cp_j_per_kg_k=cp_j_per_kg_k,
    )
    q_envelope_exchange_w = prepared_forcing.heat_loss_coefficient_W_per_C * (
        prepared_forcing.T_outdoor_C - prepared_forcing.T_indoor_initial_C
    )
    q_passive_balance_w = (
        q_envelope_exchange_w
        + prepared_forcing.Q_internal_gains_W
        + prepared_forcing.Q_solar_gains_W
        - q_air_w
    )
    integrated = integrate_zone_temperature(
        t_initial_c=prepared_forcing.T_indoor_initial_C,
        t_outdoor_c=prepared_forcing.T_outdoor_C,
        envelope_loss_w_per_k=prepared_forcing.heat_loss_coefficient_W_per_C,
        airflow_loss_w_per_k=h_ve,
        c_j_per_k=c_j_per_k,
        dt_seconds=dt_seconds,
        q_internal_gains_w=prepared_forcing.Q_internal_gains_W,
        q_solar_gains_w=prepared_forcing.Q_solar_gains_W,
    )
    t_indoor_free_float = float(integrated["t_next_c"])
    q_heating_demand_w = max(0.0, (prepared_forcing.T_set_C - t_indoor_free_float) * c_j_per_k / dt_seconds)

    physics_state = PhysicsState(
        step_label="reference-dynamic-no-heat",
        target_resolution_seconds=prepared_forcing.target_resolution_seconds,
        timestep_hours=timestep_hours,
        timestamp=prepared_forcing.timestamp,
        archetype_id=prepared_forcing.archetype_id,
        schedule_state=prepared_forcing.schedule_state,
        occupied_probability=prepared_forcing.occupied_probability,
        expected_occupants=prepared_forcing.expected_occupants,
        T_outdoor_C=prepared_forcing.T_outdoor_C,
        T_indoor_prev_C=prepared_forcing.T_indoor_initial_C,
        T_indoor_free_float_C=t_indoor_free_float,
        T_set_C=prepared_forcing.T_set_C,
        T_min_C=prepared_forcing.T_min_C,
        T_max_C=prepared_forcing.T_max_C,
        heat_loss_coefficient_W_per_C=prepared_forcing.heat_loss_coefficient_W_per_C,
        thermal_mass_Wh_per_C=prepared_forcing.thermal_mass_Wh_per_C,
        C_J_per_K=c_j_per_k,
        volume_m3=prepared_forcing.volume_m3,
        ventilation_type=prepared_forcing.ventilation_type,
        eta_HRV=prepared_forcing.eta_HRV,
        ACH_inf=prepared_forcing.ACH_inf,
        ACH_vent_base=prepared_forcing.ACH_vent_base,
        ACH_vent_occupied=prepared_forcing.ACH_vent_occupied,
        Vdot_inf_m3_per_s=vdot_inf,
        Vdot_vent_m3_per_s=vdot_vent,
        Vdot_total_m3_per_s=effective_vdot,
        H_ve_W_per_K=h_ve,
        Q_air_W=q_air_w,
        Q_heating_max_W=prepared_forcing.Q_heating_max_W,
        heating_cop=prepared_forcing.heating_cop,
        dhw_cop=prepared_forcing.dhw_cop,
        Q_passive_balance_W=q_passive_balance_w,
        Q_envelope_exchange_W=q_envelope_exchange_w,
        Q_occ_W=prepared_forcing.Q_occ_W,
        Q_app_W=prepared_forcing.Q_app_W,
        Q_lighting_W=prepared_forcing.Q_lighting_W,
        Q_cooking_W=prepared_forcing.Q_cooking_W,
        Q_internal_gains_W=prepared_forcing.Q_internal_gains_W,
        Q_solar_gains_W=prepared_forcing.Q_solar_gains_W,
        Q_heating_demand_W=q_heating_demand_w,
        Q_dhw_demand_W=prepared_forcing.Q_dhw_demand_W,
        P_el_appliances_W=prepared_forcing.P_el_appliances_W,
        P_el_lighting_W=prepared_forcing.P_el_lighting_W,
        P_el_cooking_W=prepared_forcing.P_el_cooking_W,
        P_el_ev_charging_W=prepared_forcing.P_el_ev_charging_W,
        P_pv_generation_W=prepared_forcing.P_pv_generation_W,
        metadata={
            "forcing_id": prepared_forcing.forcing_id,
            "modules": dict(prepared_forcing.metadata.get("modules", {})),
            "prepared_shape": prepared_forcing.metadata.get("merged_shape", {}),
            "stability_factor": float(stability["stability_factor"]),
            "substep_stability_factor": float(stability["substep_stability_factor"]),
            "integration_substeps": int(integrated["substeps"]),
            "substep_dt_seconds": float(integrated["substep_dt_seconds"]),
            "control_cfg": dict(prepared_forcing.metadata.get("control_cfg", {})),
            "control_schedule": dict(prepared_forcing.metadata.get("control_schedule", {})),
            "model_cfg": dict(prepared_forcing.metadata.get("model_cfg", {})),
            "ventilation_cfg": dict(prepared_forcing.metadata.get("ventilation_cfg", {})),
            "air_cfg": dict(prepared_forcing.metadata.get("air_cfg", {})),
            "baseline": dict(prepared_forcing.metadata.get("baseline", {})),
            "electricity_split": dict(prepared_forcing.metadata.get("electricity_split", {})),
            "technology_baseline": dict(prepared_forcing.metadata.get("technology_baseline", {})),
            "technology_sources": dict(prepared_forcing.metadata.get("technology_sources", {})),
            "technologies": dict(prepared_forcing.metadata.get("technologies", {})),
            "systems": dict(prepared_forcing.metadata.get("systems", {})),
            "der": dict(prepared_forcing.metadata.get("der", {})),
            "mobility": dict(prepared_forcing.metadata.get("mobility", {})),
        },
    )
    LOGGER.info(
        "physics.end state_shape=1x1 timestamp=%s free_float=%.3f q_air_W=%.3f substeps=%s",
        physics_state.timestamp,
        physics_state.T_indoor_free_float_C,
        physics_state.Q_air_W,
        integrated["substeps"],
    )
    return physics_state
