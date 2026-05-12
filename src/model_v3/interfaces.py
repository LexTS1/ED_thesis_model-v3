"""Core data contracts for the model_v3 pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from datetime import datetime
from typing import Any


@dataclass
class TimeSeriesData:
    """Structured per-source timeseries bundle with explicit provenance metadata."""

    timestamps: tuple[datetime, ...] = ()
    columns: dict[str, tuple[float | None, ...]] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def shape(self) -> tuple[int, int]:
        """Return a dataframe-like shape for lightweight logging."""

        return (len(self.timestamps), len(self.columns))

    @property
    def column_names(self) -> tuple[str, ...]:
        """Return ordered column names."""

        return tuple(self.columns.keys())


@dataclass
class InputDataset:
    """Raw or lightly structured inputs presented to the v3 pipeline."""

    dataset_id: str = "model-v3-inputs"
    household_count: int = 0
    source_data: dict[str, TimeSeriesData] = field(default_factory=dict)
    target_resolution_seconds: int = 3600
    timestep_hours: float = 1.0
    timestamp: str = ""
    archetype_id: str = ""
    T_outdoor_C: float = 5.0
    T_indoor_initial_C: float = 17.0
    T_set_C: float = 21.0
    T_min_C: float = 18.0
    T_max_C: float = 26.0
    heat_loss_coefficient_W_per_C: float = 180.0
    thermal_mass_Wh_per_C: float = 4500.0
    C_J_per_K: float = 16200000.0
    volume_m3: float = 250.0
    occupants_per_dwelling: float = 2.0
    occupant_gain_away_W_per_person: float = 0.0
    occupant_gain_awake_W_per_person: float = 70.0
    occupant_gain_sleep_W_per_person: float = 60.0
    appliance_heat_gain_fraction: float = 0.7
    lighting_heat_gain_fraction: float = 0.85
    cooking_heat_gain_fraction: float = 0.5
    ACH_inf: float = 0.5
    ACH_vent_base: float = 0.2
    ACH_vent_occupied: float = 0.3
    ventilation_type: str = "mechanical_extract"
    eta_HRV: float = 0.0
    glazing_ratio: float = 0.16
    g_value: float = 0.63
    frame_fraction: float = 1.0
    dirt_factor: float = 0.95
    incidence_factor: float = 0.9
    shading_factor: float = 0.77
    orientation_share_north: float = 0.2
    orientation_share_east: float = 0.25
    orientation_share_south: float = 0.35
    orientation_share_west: float = 0.2
    Q_heating_max_W: float = 8000.0
    heating_cop: float = 1.0
    dhw_cop: float = 1.0
    Q_dhw_demand_W: float = 0.0
    P_el_appliances_W: float = 0.0
    P_el_lighting_W: float = 0.0
    P_el_cooking_W: float = 0.0
    P_el_ev_charging_W: float = 0.0
    P_pv_generation_W: float = 0.0
    Q_internal_gains_W: float = 0.0
    Q_solar_gains_W: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class PreparedForcing:
    """Time-aligned, semantically mapped forcing bundle ready for physics."""

    forcing_id: str = "model-v3-forcing"
    timeline_label: str = "unassigned"
    target_resolution_seconds: int = 3600
    timestep_hours: float = 1.0
    timestamp: str = ""
    archetype_id: str = ""
    schedule_state: str = "away"
    occupied_probability: float = 0.0
    expected_occupants: float = 0.0
    T_outdoor_C: float = 5.0
    T_indoor_initial_C: float = 17.0
    T_set_C: float = 21.0
    T_min_C: float = 18.0
    T_max_C: float = 26.0
    heat_loss_coefficient_W_per_C: float = 180.0
    thermal_mass_Wh_per_C: float = 4500.0
    C_J_per_K: float = 16200000.0
    volume_m3: float = 250.0
    ventilation_type: str = "mechanical_extract"
    eta_HRV: float = 0.0
    ACH_inf: float = 0.5
    ACH_vent_base: float = 0.2
    ACH_vent_occupied: float = 0.3
    ACH_window_extra: float = 0.0
    Vdot_inf_m3_per_s: float = 0.0
    Vdot_vent_m3_per_s: float = 0.0
    Vdot_total_m3_per_s: float = 0.0
    H_ve_W_per_K: float = 0.0
    Q_air_W: float = 0.0
    Q_heating_max_W: float = 8000.0
    heating_cop: float = 1.0
    dhw_cop: float = 1.0
    Q_dhw_demand_W: float = 0.0
    P_el_appliances_W: float = 0.0
    P_el_lighting_W: float = 0.0
    P_el_cooking_W: float = 0.0
    P_el_ev_charging_W: float = 0.0
    P_pv_generation_W: float = 0.0
    Q_occ_W: float = 0.0
    Q_app_W: float = 0.0
    Q_lighting_W: float = 0.0
    Q_cooking_W: float = 0.0
    Q_internal_gains_W: float = 0.0
    I_solar_north_W_per_m2: float = 0.0
    I_solar_east_W_per_m2: float = 0.0
    I_solar_south_W_per_m2: float = 0.0
    I_solar_west_W_per_m2: float = 0.0
    Q_solar_gains_W: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class PhysicsState:
    """Container for the physical state emitted by the physics stage."""

    state_id: str = "model-v3-physics"
    step_label: str = "initialized"
    target_resolution_seconds: int = 3600
    timestep_hours: float = 1.0
    timestamp: str = ""
    archetype_id: str = ""
    schedule_state: str = "away"
    occupied_probability: float = 0.0
    expected_occupants: float = 0.0
    T_outdoor_C: float = 5.0
    T_indoor_prev_C: float = 17.0
    T_indoor_free_float_C: float = 17.0
    T_set_C: float = 21.0
    T_min_C: float = 18.0
    T_max_C: float = 26.0
    heat_loss_coefficient_W_per_C: float = 180.0
    thermal_mass_Wh_per_C: float = 4500.0
    C_J_per_K: float = 16200000.0
    volume_m3: float = 250.0
    ventilation_type: str = "mechanical_extract"
    eta_HRV: float = 0.0
    ACH_inf: float = 0.5
    ACH_vent_base: float = 0.2
    ACH_vent_occupied: float = 0.3
    ACH_window_extra: float = 0.0
    Vdot_inf_m3_per_s: float = 0.0
    Vdot_vent_m3_per_s: float = 0.0
    Vdot_total_m3_per_s: float = 0.0
    H_ve_W_per_K: float = 0.0
    Q_air_W: float = 0.0
    Q_heating_max_W: float = 8000.0
    heating_cop: float = 1.0
    dhw_cop: float = 1.0
    Q_passive_balance_W: float = 0.0
    Q_envelope_exchange_W: float = 0.0
    Q_occ_W: float = 0.0
    Q_app_W: float = 0.0
    Q_lighting_W: float = 0.0
    Q_cooking_W: float = 0.0
    Q_internal_gains_W: float = 0.0
    Q_solar_gains_W: float = 0.0
    Q_heating_demand_W: float = 0.0
    Q_dhw_demand_W: float = 0.0
    P_el_appliances_W: float = 0.0
    P_el_lighting_W: float = 0.0
    P_el_cooking_W: float = 0.0
    P_el_ev_charging_W: float = 0.0
    P_pv_generation_W: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ControlState:
    """Container for supervisory or device control decisions over the state."""

    state_id: str = "model-v3-control"
    control_mode: str = "unassigned"
    target_resolution_seconds: int = 3600
    timestep_hours: float = 1.0
    timestamp: str = ""
    archetype_id: str = ""
    schedule_state: str = "away"
    occupied_probability: float = 0.0
    expected_occupants: float = 0.0
    T_outdoor_C: float = 5.0
    T_indoor_prev_C: float = 17.0
    T_indoor_free_float_C: float = 17.0
    T_set_C: float = 21.0
    t_set_low_c: float = 21.0
    t_set_high_c: float = 22.0
    T_min_C: float = 18.0
    T_max_C: float = 26.0
    heat_loss_coefficient_W_per_C: float = 180.0
    thermal_mass_Wh_per_C: float = 4500.0
    C_J_per_K: float = 16200000.0
    volume_m3: float = 250.0
    ventilation_type: str = "mechanical_extract"
    eta_HRV: float = 0.0
    ACH_inf: float = 0.5
    ACH_vent_base: float = 0.2
    ACH_vent_occupied: float = 0.3
    ACH_window_extra: float = 0.0
    Vdot_inf_m3_per_s: float = 0.0
    Vdot_vent_m3_per_s: float = 0.0
    Vdot_total_m3_per_s: float = 0.0
    H_ve_W_per_K: float = 0.0
    Q_air_W: float = 0.0
    Q_heating_max_W: float = 8000.0
    heating_cop: float = 1.0
    dhw_cop: float = 1.0
    heating_on: bool = False
    Q_passive_balance_W: float = 0.0
    Q_envelope_exchange_W: float = 0.0
    Q_occ_W: float = 0.0
    Q_app_W: float = 0.0
    Q_lighting_W: float = 0.0
    Q_cooking_W: float = 0.0
    Q_internal_gains_W: float = 0.0
    Q_solar_gains_W: float = 0.0
    Q_heating_requested_W: float = 0.0
    Q_dhw_demand_W: float = 0.0
    P_el_appliances_W: float = 0.0
    P_el_lighting_W: float = 0.0
    P_el_cooking_W: float = 0.0
    P_el_ev_charging_W: float = 0.0
    P_pv_generation_W: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SystemState:
    """Container for system-level state after applying control and equipment."""

    state_id: str = "model-v3-system"
    enabled_modules: tuple[str, ...] = ()
    timestamp: str = ""
    archetype_id: str = ""
    schedule_state: str = "away"
    T_indoor_prev_C: float = 17.0
    T_indoor_free_float_C: float = 17.0
    T_indoor_next_C: float = 17.0
    heating_on: bool = False
    t_set_low_c: float = 21.0
    t_set_high_c: float = 22.0
    Vdot_inf_m3_per_s: float = 0.0
    Vdot_vent_m3_per_s: float = 0.0
    Vdot_total_m3_per_s: float = 0.0
    H_ve_W_per_K: float = 0.0
    Q_air_W: float = 0.0
    Q_heating_requested_W: float = 0.0
    Q_heating_supplied_W: float = 0.0
    Q_heating_max_W: float = 0.0
    Q_unmet_heating_W: float = 0.0
    Q_excess_heat_W: float = 0.0
    comfort_violation_degC: float = 0.0
    comfort_violation_degree_hours: float = 0.0
    P_el_space_heating_technology_W: float = 0.0
    P_el_dhw_technology_W: float = 0.0
    P_el_space_heating_W: float = 0.0
    P_el_dhw_W: float = 0.0
    P_el_appliances_W: float = 0.0
    P_el_lighting_W: float = 0.0
    P_el_cooking_W: float = 0.0
    P_el_ev_charging_W: float = 0.0
    P_el_total_W: float = 0.0
    P_pv_generation_W: float = 0.0
    P_el_gross_actual_W: float = 0.0
    P_el_net_grid_W: float = 0.0
    P_el_grid_import_W: float = 0.0
    P_el_grid_export_W: float = 0.0
    P_gas_space_heating_W: float = 0.0
    P_gas_dhw_W: float = 0.0
    P_oil_space_heating_W: float = 0.0
    P_oil_dhw_W: float = 0.0
    P_biomass_space_heating_W: float = 0.0
    P_biomass_dhw_W: float = 0.0
    P_propane_space_heating_W: float = 0.0
    P_propane_dhw_W: float = 0.0
    P_coal_space_heating_W: float = 0.0
    P_coal_dhw_W: float = 0.0
    P_district_heat_space_heating_W: float = 0.0
    P_district_heat_dhw_W: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ModelOutputs:
    """Final lightweight representation of artifacts produced by the v2 run."""

    run_id: str = "model-v3-output"
    artifact_labels: tuple[str, ...] = ()
    timestamp: str = ""
    T_indoor_next_C: float = 17.0
    Q_unmet_heating_W: float = 0.0
    Q_excess_heat_W: float = 0.0
    comfort_violation_degC: float = 0.0
    comfort_violation_degree_hours: float = 0.0
    P_el_space_heating_technology_W: float = 0.0
    P_el_dhw_technology_W: float = 0.0
    P_el_total_W: float = 0.0
    P_el_space_heating_W: float = 0.0
    P_el_dhw_W: float = 0.0
    P_el_appliances_W: float = 0.0
    P_el_lighting_W: float = 0.0
    P_el_cooking_W: float = 0.0
    P_el_ev_charging_W: float = 0.0
    P_pv_generation_W: float = 0.0
    P_el_gross_actual_W: float = 0.0
    P_el_net_grid_W: float = 0.0
    P_el_grid_import_W: float = 0.0
    P_el_grid_export_W: float = 0.0
    P_gas_space_heating_W: float = 0.0
    P_gas_dhw_W: float = 0.0
    P_oil_space_heating_W: float = 0.0
    P_oil_dhw_W: float = 0.0
    P_biomass_space_heating_W: float = 0.0
    P_biomass_dhw_W: float = 0.0
    P_propane_space_heating_W: float = 0.0
    P_propane_dhw_W: float = 0.0
    P_coal_space_heating_W: float = 0.0
    P_coal_dhw_W: float = 0.0
    P_district_heat_space_heating_W: float = 0.0
    P_district_heat_dhw_W: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def field_names(cls) -> tuple[str, ...]:
        """Return the declared output field names for reporting and debugging."""

        return tuple(field_.name for field_ in fields(cls))
