"""Core output assembly for model_v3."""

from __future__ import annotations

import logging

from model_v3.interfaces import ModelOutputs, SystemState

LOGGER = logging.getLogger(__name__)


def _run_id_from_timestamp(timestamp: str) -> str:
    """Create a compact run identifier from the output timestamp."""

    cleaned = "".join(character.lower() if character.isalnum() else "-" for character in str(timestamp or "single-step"))
    cleaned = "-".join(part for part in cleaned.split("-") if part)
    return f"model-v3-{cleaned or 'single-step'}"


def assemble_outputs(system_state: SystemState) -> ModelOutputs:
    """Assemble the final public output contract from system state only."""

    LOGGER.info("output.start timestamp=%s", system_state.timestamp)
    outputs = ModelOutputs(
        run_id=_run_id_from_timestamp(system_state.timestamp),
        artifact_labels=(
            "phase-1-contract-correction",
            "phase-2-modular-architecture",
            "phase-3-cohort-engine",
            "phase-4-uncertainty-propagation",
            "physics-finalization-v1-reference-integration",
        ),
        timestamp=system_state.timestamp,
        T_indoor_next_C=system_state.T_indoor_next_C,
        Q_unmet_heating_W=system_state.Q_unmet_heating_W,
        Q_excess_heat_W=system_state.Q_excess_heat_W,
        comfort_violation_degC=system_state.comfort_violation_degC,
        comfort_violation_degree_hours=system_state.comfort_violation_degree_hours,
        P_el_space_heating_technology_W=system_state.P_el_space_heating_technology_W,
        P_el_dhw_technology_W=system_state.P_el_dhw_technology_W,
        P_el_total_W=system_state.P_el_total_W,
        P_el_space_heating_W=system_state.P_el_space_heating_W,
        P_el_dhw_W=system_state.P_el_dhw_W,
        P_el_appliances_W=system_state.P_el_appliances_W,
        P_el_lighting_W=system_state.P_el_lighting_W,
        P_el_cooking_W=system_state.P_el_cooking_W,
        P_el_ev_charging_W=system_state.P_el_ev_charging_W,
        P_pv_generation_W=system_state.P_pv_generation_W,
        P_el_gross_actual_W=system_state.P_el_gross_actual_W,
        P_el_net_grid_W=system_state.P_el_net_grid_W,
        P_el_grid_import_W=system_state.P_el_grid_import_W,
        P_el_grid_export_W=system_state.P_el_grid_export_W,
        P_gas_space_heating_W=system_state.P_gas_space_heating_W,
        P_gas_dhw_W=system_state.P_gas_dhw_W,
        P_oil_space_heating_W=system_state.P_oil_space_heating_W,
        P_oil_dhw_W=system_state.P_oil_dhw_W,
        P_biomass_space_heating_W=system_state.P_biomass_space_heating_W,
        P_biomass_dhw_W=system_state.P_biomass_dhw_W,
        P_propane_space_heating_W=system_state.P_propane_space_heating_W,
        P_propane_dhw_W=system_state.P_propane_dhw_W,
        P_coal_space_heating_W=system_state.P_coal_space_heating_W,
        P_coal_dhw_W=system_state.P_coal_dhw_W,
        P_district_heat_space_heating_W=system_state.P_district_heat_space_heating_W,
        P_district_heat_dhw_W=system_state.P_district_heat_dhw_W,
        metadata={
            "system_state_id": system_state.state_id,
            "enabled_modules": list(system_state.enabled_modules),
            "temperature_clamped": system_state.metadata.get("temperature_clamped", False),
            "integration_substeps": system_state.metadata.get("integration_substeps", 1),
            "heating_technology_type": system_state.metadata.get("heating_technology_type"),
            "dhw_technology_type": system_state.metadata.get("dhw_technology_type"),
            "technology_sources": system_state.metadata.get("technology_sources", {}),
        },
    )
    LOGGER.info("output.end field_count=%s", len(ModelOutputs.field_names()))
    return outputs
