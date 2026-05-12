"""Electricity accounting helpers for model_v3 Phase 1."""

from __future__ import annotations

from typing import Any, Mapping


def _component_value(value: Any) -> float:
    """Return a non-negative electrical component value or zero if missing."""

    try:
        return max(float(value), 0.0)
    except (TypeError, ValueError):
        return 0.0


def compute_electricity_breakdown(
    thermal_system: Mapping[str, Any] | None = None,
    load_profiles: Mapping[str, Any] | None = None,
) -> dict[str, float]:
    """Compute full end-use electricity with safe zero fallbacks."""

    thermal_system = thermal_system or {}
    load_profiles = load_profiles or {}

    P_el_space_heating_W = _component_value(thermal_system.get("P_el_space_heating_W"))
    P_el_dhw_W = _component_value(thermal_system.get("P_el_dhw_W"))
    P_el_appliances_W = _component_value(load_profiles.get("P_el_appliances_W"))
    P_el_lighting_W = _component_value(load_profiles.get("P_el_lighting_W"))
    P_el_cooking_W = _component_value(load_profiles.get("P_el_cooking_W"))
    P_el_ev_charging_W = _component_value(load_profiles.get("P_el_ev_charging_W"))
    P_el_total_W = (
        P_el_space_heating_W
        + P_el_dhw_W
        + P_el_appliances_W
        + P_el_lighting_W
        + P_el_cooking_W
        + P_el_ev_charging_W
    )

    return {
        "P_el_space_heating_W": P_el_space_heating_W,
        "P_el_dhw_W": P_el_dhw_W,
        "P_el_appliances_W": P_el_appliances_W,
        "P_el_lighting_W": P_el_lighting_W,
        "P_el_cooking_W": P_el_cooking_W,
        "P_el_ev_charging_W": P_el_ev_charging_W,
        "P_el_total_W": P_el_total_W,
    }
