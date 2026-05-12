"""Core control execution for model_v3."""

from __future__ import annotations

import logging

from model_v3.control.thermostat import DEFAULT_DEADBAND_C, compute_hysteresis_heating
from model_v3.interfaces import ControlState, PhysicsState
from model_v3.utils.feature_flags import require_module_enabled

LOGGER = logging.getLogger(__name__)


def _compute_window_event(
    enabled: bool,
    occupied: bool,
    schedule_state: str,
    t_in_c: float,
    t_out_c: float,
    t_set_high_c: float,
    trigger_above_setpoint_c: float,
    min_indoor_minus_outdoor_c: float,
) -> bool:
    """Trigger a simple cooling-oriented window-opening event."""

    if not enabled or not occupied:
        return False
    if str(schedule_state) != "occupied_day":
        return False
    return (
        float(t_in_c) > float(t_set_high_c) + float(trigger_above_setpoint_c)
        and float(t_in_c) - float(t_out_c) >= float(min_indoor_minus_outdoor_c)
    )


def _compute_flow_m3_per_s(volume_m3: float, ach: float) -> float:
    """Convert ACH to volumetric airflow."""

    return max(float(ach), 0.0) * max(float(volume_m3), 0.0) / 3600.0


def _effective_ventilation_flow(vdot_inf_m3_per_s: float, vdot_vent_m3_per_s: float, ventilation_type: str, eta_hrv: float) -> float:
    """Apply heat recovery to the mechanical ventilation portion only."""

    if str(ventilation_type).strip().lower() == "balanced":
        return float(vdot_inf_m3_per_s) + (1.0 - max(min(float(eta_hrv), 1.0), 0.0)) * float(vdot_vent_m3_per_s)
    return float(vdot_inf_m3_per_s) + float(vdot_vent_m3_per_s)


def run_control(physics_state: PhysicsState) -> ControlState:
    """Apply the v1.5-style schedule, deadband, and optional window-opening control logic."""

    require_module_enabled(
        {"modules": dict(physics_state.metadata.get("modules", {}))},
        "control",
        "run_control",
    )
    LOGGER.info(
        "control.start timestamp=%s timestep_hours=%.3f",
        physics_state.timestamp,
        physics_state.timestep_hours,
    )
    control_cfg = dict(physics_state.metadata.get("control_cfg", {}))
    model_cfg = dict(physics_state.metadata.get("model_cfg", {}))
    ventilation_cfg = dict(physics_state.metadata.get("ventilation_cfg", {}))
    window_cfg = dict(ventilation_cfg.get("window_opening", {}))
    air_cfg = dict(physics_state.metadata.get("air_cfg", {}))
    control_schedule = dict(physics_state.metadata.get("control_schedule", {}))

    deadband_c = float(control_schedule.get("deadband_c", control_cfg.get("deadband", DEFAULT_DEADBAND_C)))
    previous_heating_on = bool(model_cfg.get("initial_heating_on", False))
    heating_on, t_set_low_c, t_set_high_c = compute_hysteresis_heating(
        t_in_c=physics_state.T_indoor_prev_C,
        t_set_c=physics_state.T_set_C,
        delta_c=deadband_c,
        previous_heating_on=previous_heating_on,
    )
    q_heating_requested_w = (
        max(0.0, float(physics_state.heat_loss_coefficient_W_per_C) * (float(physics_state.T_set_C) - float(physics_state.T_indoor_prev_C)))
        if heating_on
        else 0.0
    )

    occupied = physics_state.occupied_probability >= float(model_cfg.get("occupancy_threshold", 0.5))
    window_event = _compute_window_event(
        enabled=bool(window_cfg.get("enabled", False)),
        occupied=occupied,
        schedule_state=physics_state.schedule_state,
        t_in_c=physics_state.T_indoor_prev_C,
        t_out_c=physics_state.T_outdoor_C,
        t_set_high_c=t_set_high_c,
        trigger_above_setpoint_c=float(window_cfg.get("trigger_above_setpoint_c", 1.0)),
        min_indoor_minus_outdoor_c=float(window_cfg.get("min_indoor_minus_outdoor_c", 1.0)),
    )
    ach_window_extra = float(window_cfg.get("extra_ach", 0.5)) if window_event else 0.0
    ach_vent_main = physics_state.ACH_vent_occupied if occupied else physics_state.ACH_vent_base
    vdot_inf = _compute_flow_m3_per_s(physics_state.volume_m3, physics_state.ACH_inf)
    vdot_vent_main = _compute_flow_m3_per_s(physics_state.volume_m3, ach_vent_main)
    vdot_window_extra = _compute_flow_m3_per_s(physics_state.volume_m3, ach_window_extra)
    effective_vdot = _effective_ventilation_flow(
        vdot_inf_m3_per_s=vdot_inf,
        vdot_vent_m3_per_s=vdot_vent_main,
        ventilation_type=physics_state.ventilation_type,
        eta_hrv=physics_state.eta_HRV,
    ) + vdot_window_extra
    rho_kg_per_m3 = float(air_cfg.get("rho", 1.2))
    cp_j_per_kg_k = float(air_cfg.get("cp", 1000.0))
    h_ve = rho_kg_per_m3 * cp_j_per_kg_k * effective_vdot
    q_air_w = rho_kg_per_m3 * cp_j_per_kg_k * effective_vdot * (physics_state.T_indoor_prev_C - physics_state.T_outdoor_C)
    q_passive_balance_w = (
        physics_state.Q_envelope_exchange_W
        + physics_state.Q_internal_gains_W
        + physics_state.Q_solar_gains_W
        - q_air_w
    )

    control_state = ControlState(
        control_mode="v1_5_deadband_schedule_control",
        target_resolution_seconds=physics_state.target_resolution_seconds,
        timestep_hours=physics_state.timestep_hours,
        timestamp=physics_state.timestamp,
        archetype_id=physics_state.archetype_id,
        schedule_state=physics_state.schedule_state,
        occupied_probability=physics_state.occupied_probability,
        expected_occupants=physics_state.expected_occupants,
        T_outdoor_C=physics_state.T_outdoor_C,
        T_indoor_prev_C=physics_state.T_indoor_prev_C,
        T_indoor_free_float_C=physics_state.T_indoor_free_float_C,
        T_set_C=physics_state.T_set_C,
        t_set_low_c=t_set_low_c,
        t_set_high_c=t_set_high_c,
        T_min_C=physics_state.T_min_C,
        T_max_C=physics_state.T_max_C,
        heat_loss_coefficient_W_per_C=physics_state.heat_loss_coefficient_W_per_C,
        thermal_mass_Wh_per_C=physics_state.thermal_mass_Wh_per_C,
        C_J_per_K=physics_state.C_J_per_K,
        volume_m3=physics_state.volume_m3,
        ventilation_type=physics_state.ventilation_type,
        eta_HRV=physics_state.eta_HRV,
        ACH_inf=physics_state.ACH_inf,
        ACH_vent_base=physics_state.ACH_vent_base,
        ACH_vent_occupied=physics_state.ACH_vent_occupied,
        ACH_window_extra=ach_window_extra,
        Vdot_inf_m3_per_s=vdot_inf,
        Vdot_vent_m3_per_s=vdot_vent_main + vdot_window_extra,
        Vdot_total_m3_per_s=effective_vdot,
        H_ve_W_per_K=h_ve,
        Q_air_W=q_air_w,
        Q_heating_max_W=physics_state.Q_heating_max_W,
        heating_cop=physics_state.heating_cop,
        dhw_cop=physics_state.dhw_cop,
        heating_on=heating_on,
        Q_passive_balance_W=q_passive_balance_w,
        Q_envelope_exchange_W=physics_state.Q_envelope_exchange_W,
        Q_occ_W=physics_state.Q_occ_W,
        Q_app_W=physics_state.Q_app_W,
        Q_lighting_W=physics_state.Q_lighting_W,
        Q_cooking_W=physics_state.Q_cooking_W,
        Q_internal_gains_W=physics_state.Q_internal_gains_W,
        Q_solar_gains_W=physics_state.Q_solar_gains_W,
        Q_heating_requested_W=q_heating_requested_w,
        Q_dhw_demand_W=physics_state.Q_dhw_demand_W,
        P_el_appliances_W=physics_state.P_el_appliances_W,
        P_el_lighting_W=physics_state.P_el_lighting_W,
        P_el_cooking_W=physics_state.P_el_cooking_W,
        P_el_ev_charging_W=physics_state.P_el_ev_charging_W,
        P_pv_generation_W=physics_state.P_pv_generation_W,
        metadata={
            "physics_state_id": physics_state.state_id,
            "modules": dict(physics_state.metadata.get("modules", {})),
            "control_shape": {"rows": 1, "columns": 1},
            "window_event": window_event,
            "control_schedule": control_schedule,
            "baseline": dict(physics_state.metadata.get("baseline", {})),
            "electricity_split": dict(physics_state.metadata.get("electricity_split", {})),
            "technology_baseline": dict(physics_state.metadata.get("technology_baseline", {})),
            "technology_sources": dict(physics_state.metadata.get("technology_sources", {})),
            "technologies": dict(physics_state.metadata.get("technologies", {})),
            "systems": dict(physics_state.metadata.get("systems", {})),
            "der": dict(physics_state.metadata.get("der", {})),
            "mobility": dict(physics_state.metadata.get("mobility", {})),
        },
    )
    LOGGER.info(
        "control.end state_shape=1x1 heating_on=%s heating_request_W=%.3f ach_window_extra=%.3f",
        control_state.heating_on,
        control_state.Q_heating_requested_W,
        control_state.ACH_window_extra,
    )
    return control_state
