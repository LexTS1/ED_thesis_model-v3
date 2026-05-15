"""Carrier-aware heating and DHW technology conversion."""

from __future__ import annotations

from typing import Any, Mapping

from model_v3.systems.distributed_energy import value_from_range
from model_v3.systems.heat_pump_performance import heat_pump_performance


ELECTRIC_CARRIERS = {"electricity"}
HEAT_PUMP_TECHNOLOGIES = {"air_water", "air_air", "ground_source", "hpwh"}
THERMAL_CARRIER_COLUMNS = (
    "P_gas_space_heating_W",
    "P_oil_space_heating_W",
    "P_biomass_space_heating_W",
    "P_propane_space_heating_W",
    "P_coal_space_heating_W",
    "P_district_heat_space_heating_W",
    "P_gas_dhw_W",
    "P_oil_dhw_W",
    "P_biomass_dhw_W",
    "P_propane_dhw_W",
    "P_coal_dhw_W",
    "P_district_heat_dhw_W",
)

TECHNOLOGY_ALIASES = {
    "gas": "gas_boiler",
    "natural_gas": "gas_boiler",
    "gas_boiler": "gas_boiler",
    "hybrid": "hybrid_hp_gas",
    "hybrid_hp": "hybrid_hp_gas",
    "hybrid_hp_gas": "hybrid_hp_gas",
    "oil": "oil_boiler",
    "heating_oil": "oil_boiler",
    "oil_boiler": "oil_boiler",
    "oil_stove_other": "oil_boiler",
    "resistive": "resistive_direct",
    "resistive_direct": "resistive_direct",
    "direct_electric": "resistive_direct",
    "direct_electric_excl_hp": "resistive_direct",
    "storage_heater": "storage_heater",
    "heat_pump": "air_water",
    "air_water": "air_water",
    "air_air": "air_air",
    "ground_source": "ground_source",
    "biomass": "biomass_stove",
    "wood_pellet": "biomass_stove",
    "biomass_stove": "biomass_stove",
    "biomass_boiler": "biomass_boiler",
    "propane_butane": "propane_boiler",
    "propane": "propane_boiler",
    "propane_boiler": "propane_boiler",
    "coal": "coal_stove",
    "coal_stove": "coal_stove",
    "district_heating": "district_heating",
    "district_heat": "district_heating",
    "electric_storage": "electric_storage",
    "hpwh": "hpwh",
    "heat_pump_water_heater": "hpwh",
    "linked_to_space_heating": "linked_to_space_heating",
}


def normalize_technology_type(value: Any, default: str = "") -> str:
    """Return the canonical internal technology label."""

    raw = str(value if value not in {None, ""} else default).strip().lower()
    return TECHNOLOGY_ALIASES.get(raw, raw)


def _performance_cfg(technologies_cfg: Mapping[str, Any]) -> dict[str, Any]:
    return dict(dict(technologies_cfg.get("heating", {})).get("performance", {}))


def _technology_performance(
    technology_type: str,
    technologies_cfg: Mapping[str, Any],
    systems_cfg: Mapping[str, Any],
) -> tuple[str, float]:
    """Return ``(carrier, useful_heat_per_delivered_energy)`` for a technology."""

    tech = normalize_technology_type(technology_type)
    performance = _performance_cfg(technologies_cfg)
    heating_system_cfg = dict(systems_cfg.get("heating", {}))
    dhw_system_cfg = dict(systems_cfg.get("dhw", {}))

    if tech in {"air_water", "air_air"}:
        hp_cfg = dict(performance.get("heat_pump", {}))
        if "cop" in heating_system_cfg:
            return "electricity", value_from_range(heating_system_cfg.get("cop"), 3.0)
        if tech == "air_air":
            return "electricity", value_from_range(hp_cfg.get("air_water_radiators_spf"), 2.5)
        return "electricity", value_from_range(hp_cfg.get("air_water_radiators_spf"), 2.5)
    if tech == "ground_source":
        hp_cfg = dict(performance.get("heat_pump", {}))
        return "electricity", value_from_range(hp_cfg.get("ground_source_low_temp_spf"), 3.6)
    if tech in {"resistive_direct", "storage_heater", "electric_storage"}:
        return "electricity", value_from_range(heating_system_cfg.get("efficiency"), 1.0)
    if tech == "hpwh":
        return "electricity", value_from_range(dhw_system_cfg.get("cop"), 2.2)
    if tech == "gas_boiler":
        return "gas", value_from_range(dict(performance.get("gas_boiler", {})).get("seasonal_efficiency"), 0.90)
    if tech == "oil_boiler":
        return "oil", value_from_range(dict(performance.get("oil_boiler", {})).get("seasonal_efficiency"), 0.82)
    if tech in {"biomass_stove", "biomass_boiler"}:
        return "biomass", value_from_range(dict(performance.get("biomass", {})).get("efficiency"), 0.75)
    if tech == "propane_boiler":
        return "propane", value_from_range(dict(performance.get("propane_boiler", {})).get("seasonal_efficiency"), 0.88)
    if tech == "coal_stove":
        return "coal", value_from_range(dict(performance.get("coal", {})).get("efficiency"), 0.60)
    if tech == "district_heating":
        return "district_heat", value_from_range(
            dict(performance.get("district_heating", {})).get("dwelling_conversion"),
            1.0,
        )
    return "electricity", value_from_range(heating_system_cfg.get("cop"), 1.0)


def _empty_carrier_result(prefix: str) -> dict[str, float]:
    return {
        f"P_gas_{prefix}_W": 0.0,
        f"P_oil_{prefix}_W": 0.0,
        f"P_biomass_{prefix}_W": 0.0,
        f"P_propane_{prefix}_W": 0.0,
        f"P_coal_{prefix}_W": 0.0,
        f"P_district_heat_{prefix}_W": 0.0,
        f"P_el_{prefix}_technology_W": 0.0,
    }


def _assign_carrier_power(
    result: dict[str, float],
    *,
    prefix: str,
    carrier: str,
    delivered_power_w: float,
) -> None:
    if carrier == "electricity":
        result[f"P_el_{prefix}_technology_W"] += float(delivered_power_w)
        return
    column = f"P_{carrier}_{prefix}_W"
    if column in result:
        result[column] += float(delivered_power_w)


def convert_heat_to_carriers(
    useful_heat_w: float,
    *,
    prefix: str,
    technology_type: str,
    technologies_cfg: Mapping[str, Any],
    systems_cfg: Mapping[str, Any],
    source_temperature_c: float | None = None,
    indoor_setpoint_c: float | None = None,
    capacity_w: float | None = None,
    mode: str = "heating",
) -> dict[str, Any]:
    """Convert useful heat into delivered-energy powers by carrier."""

    result: dict[str, Any] = _empty_carrier_result(prefix)
    useful_heat_w = max(float(useful_heat_w), 0.0)
    tech = normalize_technology_type(technology_type)

    if tech == "hybrid_hp_gas" and prefix == "space_heating":
        hybrid_cfg = dict(dict(_performance_cfg(technologies_cfg).get("hybrid_hp", {})).get("control", {}))
        hp_fraction = value_from_range(hybrid_cfg.get("hp_load_fraction"), 0.65)
        hp_useful = useful_heat_w * min(max(hp_fraction, 0.0), 1.0)
        gas_useful = useful_heat_w - hp_useful
        hp_performance = heat_pump_performance(
            "hybrid_hp_gas",
            systems_cfg=systems_cfg,
            outdoor_temperature_c=float(source_temperature_c if source_temperature_c is not None else 7.0),
            indoor_setpoint_c=float(indoor_setpoint_c if indoor_setpoint_c is not None else 20.0),
            useful_heat_w=hp_useful,
            capacity_w=float(capacity_w if capacity_w is not None else useful_heat_w),
            mode=mode,
        )
        hp_carrier, hp_factor = "electricity", float(hp_performance["cop"])
        gas_carrier, gas_factor = _technology_performance("gas_boiler", technologies_cfg, systems_cfg)
        _assign_carrier_power(
            result,
            prefix=prefix,
            carrier=hp_carrier,
            delivered_power_w=hp_useful / max(hp_factor, 1e-9),
        )
        _assign_carrier_power(
            result,
            prefix=prefix,
            carrier=gas_carrier,
            delivered_power_w=gas_useful / max(gas_factor, 1e-9),
        )
        result.update(
            {
                "technology_type": tech,
                "energy_carrier": "electricity+gas",
                "conversion_factor": None,
                "hybrid_hp_load_fraction": float(min(max(hp_fraction, 0.0), 1.0)),
                "heat_pump_cop": float(hp_performance["cop"]),
                "heat_pump_cop_base": float(hp_performance["cop_base"]),
                "heat_pump_emitter_type": hp_performance["emitter_type"],
                "heat_pump_refrigerant": hp_performance["refrigerant"],
                "heat_pump_source_temperature_C": float(hp_performance["source_temperature_C"]),
                "heat_pump_sink_temperature_C": float(hp_performance["sink_temperature_C"]),
                "heat_pump_defrost_factor": float(hp_performance["defrost_factor"]),
                "heat_pump_part_load_ratio": float(hp_performance["part_load_ratio"]),
                "heat_pump_part_load_factor": float(hp_performance["part_load_factor"]),
                "heat_pump_capacity_available_fraction": float(hp_performance["capacity_available_fraction"]),
            }
        )
        return result

    hp_performance: dict[str, Any] | None = None
    if tech in HEAT_PUMP_TECHNOLOGIES:
        hp_performance = heat_pump_performance(
            tech,
            systems_cfg=systems_cfg,
            outdoor_temperature_c=float(source_temperature_c if source_temperature_c is not None else 7.0),
            indoor_setpoint_c=float(indoor_setpoint_c if indoor_setpoint_c is not None else 20.0),
            useful_heat_w=useful_heat_w,
            capacity_w=float(capacity_w if capacity_w is not None else useful_heat_w),
            mode=mode,
        )
        carrier, factor = "electricity", float(hp_performance["cop"])
    else:
        carrier, factor = _technology_performance(tech, technologies_cfg, systems_cfg)
    delivered_power_w = useful_heat_w / max(float(factor), 1e-9)
    _assign_carrier_power(result, prefix=prefix, carrier=carrier, delivered_power_w=delivered_power_w)
    result.update(
        {
            "technology_type": tech,
            "energy_carrier": carrier,
            "conversion_factor": float(factor),
        }
    )
    if hp_performance is not None:
        result.update(
            {
                "heat_pump_cop": float(hp_performance["cop"]),
                "heat_pump_cop_base": float(hp_performance["cop_base"]),
                "heat_pump_emitter_type": hp_performance["emitter_type"],
                "heat_pump_refrigerant": hp_performance["refrigerant"],
                "heat_pump_source_temperature_C": float(hp_performance["source_temperature_C"]),
                "heat_pump_sink_temperature_C": float(hp_performance["sink_temperature_C"]),
                "heat_pump_defrost_factor": float(hp_performance["defrost_factor"]),
                "heat_pump_part_load_ratio": float(hp_performance["part_load_ratio"]),
                "heat_pump_part_load_factor": float(hp_performance["part_load_factor"]),
                "heat_pump_capacity_available_fraction": float(hp_performance["capacity_available_fraction"]),
            }
        )
    return result


def configured_heating_technology(systems_cfg: Mapping[str, Any]) -> str | None:
    """Return an explicitly configured heating technology, if any."""

    heating_cfg = dict(systems_cfg.get("heating", {}))
    raw = heating_cfg.get("technology_type", heating_cfg.get("type"))
    if raw in {None, ""}:
        return None
    return normalize_technology_type(raw)


def configured_dhw_technology(
    systems_cfg: Mapping[str, Any],
    technologies_cfg: Mapping[str, Any],
    heating_technology_type: str | None,
) -> str | None:
    """Return the DHW technology, optionally linked to space heating."""

    dhw_cfg = dict(systems_cfg.get("dhw", {}))
    raw = dhw_cfg.get("technology_type", dhw_cfg.get("type"))
    if raw in {None, ""}:
        assumptions = dict(dict(technologies_cfg.get("dhw", {})).get("modelling_assumptions", {}))
        if bool(assumptions.get("link_to_space_heating_carrier_by_default", False)) and heating_technology_type:
            if heating_technology_type == "hybrid_hp_gas" and bool(
                dict(dict(_performance_cfg(technologies_cfg).get("hybrid_hp", {})).get("control", {})).get(
                    "dhw_by_boiler",
                    True,
                )
            ):
                return "gas_boiler"
            return heating_technology_type
        return None
    normalized = normalize_technology_type(raw)
    if normalized == "linked_to_space_heating":
        return heating_technology_type
    return normalized
