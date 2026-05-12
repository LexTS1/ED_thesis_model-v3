"""Feature-toggle helpers for model_v3 runtime modules."""

from __future__ import annotations

from typing import Any, Mapping

from model_v3.interfaces import ControlState, PhysicsState, PreparedForcing, SystemState


DEFAULT_MODULES: dict[str, bool] = {
    "physics": True,
    "control": True,
    "systems": True,
    "cohort": True,
    "stochastic": True,
}


class ModuleDisabledError(RuntimeError):
    """Raised when a caller requests a module that is disabled by config."""


def _coerce_bool(value: Any, default: bool = True) -> bool:
    """Coerce common config truthy/falsy values."""

    if value is None:
        return bool(default)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on", "enabled"}:
            return True
        if normalized in {"0", "false", "no", "off", "disabled"}:
            return False
    return bool(value)


def module_flags(config: Mapping[str, Any] | None) -> dict[str, bool]:
    """Return normalized module flags with conservative defaults."""

    configured = dict((config or {}).get("modules", {}))
    flags = {
        name: _coerce_bool(configured.get(name), default)
        for name, default in DEFAULT_MODULES.items()
    }
    for name, value in configured.items():
        flags.setdefault(str(name), _coerce_bool(value, True))
    return flags


def is_module_enabled(config: Mapping[str, Any] | None, module_name: str) -> bool:
    """Return whether one runtime module is enabled."""

    return bool(module_flags(config).get(module_name, True))


def enabled_runtime_modules(config: Mapping[str, Any] | None) -> tuple[str, ...]:
    """Return enabled runtime modules in stable order."""

    flags = module_flags(config)
    ordered = tuple(name for name in DEFAULT_MODULES if flags.get(name, True))
    extras = tuple(sorted(name for name in flags if name not in DEFAULT_MODULES and flags[name]))
    return ordered + extras


def require_module_enabled(config: Mapping[str, Any] | None, module_name: str, operation: str) -> None:
    """Fail clearly when an operation requires a disabled module."""

    if not is_module_enabled(config, module_name):
        raise ModuleDisabledError(
            f"{operation} requires modules.{module_name}=true; "
            f"currently modules.{module_name}=false."
        )


def disabled_physics_state(prepared_forcing: PreparedForcing) -> PhysicsState:
    """Return a pass-through physics state when the physics module is disabled."""

    metadata = dict(prepared_forcing.metadata)
    metadata["module_toggles"] = {**dict(metadata.get("module_toggles", {})), "physics": False}
    return PhysicsState(
        state_id="model-v3-physics-disabled",
        step_label="physics-disabled-passthrough",
        target_resolution_seconds=prepared_forcing.target_resolution_seconds,
        timestep_hours=prepared_forcing.timestep_hours,
        timestamp=prepared_forcing.timestamp,
        archetype_id=prepared_forcing.archetype_id,
        schedule_state=prepared_forcing.schedule_state,
        occupied_probability=prepared_forcing.occupied_probability,
        expected_occupants=prepared_forcing.expected_occupants,
        T_outdoor_C=prepared_forcing.T_outdoor_C,
        T_indoor_prev_C=prepared_forcing.T_indoor_initial_C,
        T_indoor_free_float_C=prepared_forcing.T_indoor_initial_C,
        T_set_C=prepared_forcing.T_set_C,
        T_min_C=prepared_forcing.T_min_C,
        T_max_C=prepared_forcing.T_max_C,
        heat_loss_coefficient_W_per_C=prepared_forcing.heat_loss_coefficient_W_per_C,
        thermal_mass_Wh_per_C=prepared_forcing.thermal_mass_Wh_per_C,
        C_J_per_K=prepared_forcing.C_J_per_K,
        volume_m3=prepared_forcing.volume_m3,
        ventilation_type=prepared_forcing.ventilation_type,
        eta_HRV=prepared_forcing.eta_HRV,
        ACH_inf=prepared_forcing.ACH_inf,
        ACH_vent_base=prepared_forcing.ACH_vent_base,
        ACH_vent_occupied=prepared_forcing.ACH_vent_occupied,
        Q_heating_max_W=prepared_forcing.Q_heating_max_W,
        heating_cop=prepared_forcing.heating_cop,
        dhw_cop=prepared_forcing.dhw_cop,
        Q_occ_W=prepared_forcing.Q_occ_W,
        Q_app_W=prepared_forcing.Q_app_W,
        Q_lighting_W=prepared_forcing.Q_lighting_W,
        Q_cooking_W=prepared_forcing.Q_cooking_W,
        Q_internal_gains_W=prepared_forcing.Q_internal_gains_W,
        Q_solar_gains_W=prepared_forcing.Q_solar_gains_W,
        Q_dhw_demand_W=prepared_forcing.Q_dhw_demand_W,
        P_el_appliances_W=prepared_forcing.P_el_appliances_W,
        P_el_lighting_W=prepared_forcing.P_el_lighting_W,
        P_el_cooking_W=prepared_forcing.P_el_cooking_W,
        P_el_ev_charging_W=prepared_forcing.P_el_ev_charging_W,
        P_pv_generation_W=prepared_forcing.P_pv_generation_W,
        metadata=metadata,
    )


def disabled_control_state(physics_state: PhysicsState) -> ControlState:
    """Return a no-control pass-through state when the control module is disabled."""

    metadata = {
        **dict(physics_state.metadata),
        "physics_state_id": physics_state.state_id,
        "control_shape": {"rows": 1, "columns": 1},
        "module_toggles": {**dict(physics_state.metadata.get("module_toggles", {})), "control": False},
    }
    return ControlState(
        state_id="model-v3-control-disabled",
        control_mode="control-disabled-passthrough",
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
        t_set_low_c=physics_state.T_set_C,
        t_set_high_c=physics_state.T_set_C,
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
        Vdot_inf_m3_per_s=physics_state.Vdot_inf_m3_per_s,
        Vdot_vent_m3_per_s=physics_state.Vdot_vent_m3_per_s,
        Vdot_total_m3_per_s=physics_state.Vdot_total_m3_per_s,
        H_ve_W_per_K=physics_state.H_ve_W_per_K,
        Q_air_W=physics_state.Q_air_W,
        Q_heating_max_W=physics_state.Q_heating_max_W,
        heating_cop=physics_state.heating_cop,
        dhw_cop=physics_state.dhw_cop,
        heating_on=False,
        Q_passive_balance_W=physics_state.Q_passive_balance_W,
        Q_envelope_exchange_W=physics_state.Q_envelope_exchange_W,
        Q_occ_W=physics_state.Q_occ_W,
        Q_app_W=physics_state.Q_app_W,
        Q_lighting_W=physics_state.Q_lighting_W,
        Q_cooking_W=physics_state.Q_cooking_W,
        Q_internal_gains_W=physics_state.Q_internal_gains_W,
        Q_solar_gains_W=physics_state.Q_solar_gains_W,
        Q_heating_requested_W=0.0,
        Q_dhw_demand_W=physics_state.Q_dhw_demand_W,
        P_el_appliances_W=physics_state.P_el_appliances_W,
        P_el_lighting_W=physics_state.P_el_lighting_W,
        P_el_cooking_W=physics_state.P_el_cooking_W,
        P_el_ev_charging_W=physics_state.P_el_ev_charging_W,
        P_pv_generation_W=physics_state.P_pv_generation_W,
        metadata=metadata,
    )


def disabled_system_state(control_state: ControlState, enabled_modules: tuple[str, ...]) -> SystemState:
    """Return a pass-through output state when the systems module is disabled."""

    p_el_total_w = (
        max(control_state.P_el_appliances_W, 0.0)
        + max(control_state.P_el_lighting_W, 0.0)
        + max(control_state.P_el_cooking_W, 0.0)
    )
    p_el_gross_actual_w = (
        p_el_total_w
        + max(control_state.P_el_ev_charging_W, 0.0)
    )
    p_el_net_grid_w = p_el_gross_actual_w - max(control_state.P_pv_generation_W, 0.0)
    return SystemState(
        state_id="model-v3-system-disabled",
        enabled_modules=enabled_modules,
        timestamp=control_state.timestamp,
        archetype_id=control_state.archetype_id,
        schedule_state=control_state.schedule_state,
        T_indoor_prev_C=control_state.T_indoor_prev_C,
        T_indoor_free_float_C=control_state.T_indoor_free_float_C,
        T_indoor_next_C=control_state.T_indoor_free_float_C,
        heating_on=False,
        t_set_low_c=control_state.t_set_low_c,
        t_set_high_c=control_state.t_set_high_c,
        Vdot_inf_m3_per_s=control_state.Vdot_inf_m3_per_s,
        Vdot_vent_m3_per_s=control_state.Vdot_vent_m3_per_s,
        Vdot_total_m3_per_s=control_state.Vdot_total_m3_per_s,
        H_ve_W_per_K=control_state.H_ve_W_per_K,
        Q_air_W=control_state.Q_air_W,
        Q_heating_requested_W=0.0,
        Q_heating_supplied_W=0.0,
        Q_heating_max_W=control_state.Q_heating_max_W,
        P_el_appliances_W=max(control_state.P_el_appliances_W, 0.0),
        P_el_lighting_W=max(control_state.P_el_lighting_W, 0.0),
        P_el_cooking_W=max(control_state.P_el_cooking_W, 0.0),
        P_el_ev_charging_W=max(control_state.P_el_ev_charging_W, 0.0),
        P_el_total_W=p_el_total_w,
        P_pv_generation_W=max(control_state.P_pv_generation_W, 0.0),
        P_el_gross_actual_W=p_el_gross_actual_w,
        P_el_net_grid_W=p_el_net_grid_w,
        P_el_grid_import_W=max(p_el_net_grid_w, 0.0),
        P_el_grid_export_W=max(-p_el_net_grid_w, 0.0),
        metadata={
            **dict(control_state.metadata),
            "control_state_id": control_state.state_id,
            "temperature_clamped": False,
            "system_shape": {"rows": 1, "columns": 1},
            "integration_substeps": 0,
            "module_toggles": {**dict(control_state.metadata.get("module_toggles", {})), "systems": False},
            "heating_technology_type": "systems_disabled",
            "dhw_technology_type": "systems_disabled",
            "technology_sources": dict(control_state.metadata.get("technology_sources", {})),
        },
    )
