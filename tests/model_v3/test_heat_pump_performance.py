"""Regression tests for empirical heat-pump COP handling."""

from __future__ import annotations

import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
MODEL_V3_SRC = REPO_ROOT / "src"
if str(MODEL_V3_SRC) not in sys.path:
    sys.path.insert(0, str(MODEL_V3_SRC))

from model_v3.systems.heat_pump_performance import heat_pump_performance, weather_curve_sink_temperature  # noqa: E402
from model_v3.systems.technology import convert_heat_to_carriers  # noqa: E402


def test_emitter_weather_curve_orders_supply_temperatures() -> None:
    cold_underfloor = weather_curve_sink_temperature("underfloor", -7.0)
    cold_standard_radiators = weather_curve_sink_temperature("standard_radiators", -7.0)
    mild_standard_radiators = weather_curve_sink_temperature("standard_radiators", 15.0)

    assert cold_underfloor == 35.0
    assert cold_standard_radiators == 55.0
    assert mild_standard_radiators == 42.0
    assert cold_underfloor < cold_standard_radiators


def test_ground_source_temperature_accepts_scalar_and_range() -> None:
    scalar = heat_pump_performance(
        "ground_source",
        systems_cfg={
            "heating": {
                "emitter_type": "low_temperature_radiators",
                "ground_source_temperature_C": 6.0,
            }
        },
        outdoor_temperature_c=-5.0,
        indoor_setpoint_c=21.0,
        useful_heat_w=4000.0,
        capacity_w=8000.0,
    )
    ranged = heat_pump_performance(
        "ground_source",
        systems_cfg={
            "heating": {
                "emitter_type": "low_temperature_radiators",
                "ground_source_temperature_C": {"low": 4.0, "base": 8.0, "high": 12.0},
            }
        },
        outdoor_temperature_c=-5.0,
        indoor_setpoint_c=21.0,
        useful_heat_w=4000.0,
        capacity_w=8000.0,
    )

    assert scalar["source_temperature_C"] == 6.0
    assert ranged["source_temperature_C"] == 8.0
    assert ranged["cop"] > scalar["cop"]


def test_air_water_cop_responds_to_emitter_and_weather() -> None:
    underfloor_mild = heat_pump_performance(
        "air_water",
        systems_cfg={"heating": {"emitter_type": "underfloor"}},
        outdoor_temperature_c=7.0,
        indoor_setpoint_c=21.0,
        useful_heat_w=4000.0,
        capacity_w=8000.0,
    )
    radiator_cold = heat_pump_performance(
        "air_water",
        systems_cfg={"heating": {"emitter_type": "standard_radiators"}},
        outdoor_temperature_c=-7.0,
        indoor_setpoint_c=21.0,
        useful_heat_w=4000.0,
        capacity_w=8000.0,
    )

    assert underfloor_mild["sink_temperature_C"] < radiator_cold["sink_temperature_C"]
    assert underfloor_mild["cop"] > radiator_cold["cop"]


def test_household_cop_ref_overrides_type_default_for_stochastic_scaling() -> None:
    type_default = heat_pump_performance(
        "air_water",
        systems_cfg={
            "heating": {"emitter_type": "low_temperature_radiators"},
            "heat_pump_performance": {"types": {"air_water": {"cop_ref": 3.5}}},
        },
        outdoor_temperature_c=7.0,
        indoor_setpoint_c=21.0,
        useful_heat_w=4000.0,
        capacity_w=8000.0,
    )
    household_scaled = heat_pump_performance(
        "air_water",
        systems_cfg={
            "heating": {"emitter_type": "low_temperature_radiators", "cop_ref": 4.5},
            "heat_pump_performance": {"types": {"air_water": {"cop_ref": 3.5}}},
        },
        outdoor_temperature_c=7.0,
        indoor_setpoint_c=21.0,
        useful_heat_w=4000.0,
        capacity_w=8000.0,
    )

    assert household_scaled["cop"] > type_default["cop"]


def test_calibrated_heat_pump_cop_ordering_remains_physical() -> None:
    standard = heat_pump_performance(
        "air_water",
        systems_cfg={"heating": {"emitter_type": "standard_radiators"}},
        outdoor_temperature_c=7.0,
        indoor_setpoint_c=21.0,
        useful_heat_w=4000.0,
        capacity_w=8000.0,
    )
    low_temperature = heat_pump_performance(
        "air_water",
        systems_cfg={"heating": {"emitter_type": "low_temperature_radiators"}},
        outdoor_temperature_c=7.0,
        indoor_setpoint_c=21.0,
        useful_heat_w=4000.0,
        capacity_w=8000.0,
    )
    underfloor = heat_pump_performance(
        "air_water",
        systems_cfg={"heating": {"emitter_type": "underfloor"}},
        outdoor_temperature_c=7.0,
        indoor_setpoint_c=21.0,
        useful_heat_w=4000.0,
        capacity_w=8000.0,
    )
    air_air = heat_pump_performance(
        "air_air",
        systems_cfg={"heating": {}},
        outdoor_temperature_c=7.0,
        indoor_setpoint_c=21.0,
        useful_heat_w=4000.0,
        capacity_w=8000.0,
    )
    ground_source = heat_pump_performance(
        "ground_source",
        systems_cfg={"heating": {"emitter_type": "low_temperature_radiators"}},
        outdoor_temperature_c=7.0,
        indoor_setpoint_c=21.0,
        useful_heat_w=4000.0,
        capacity_w=8000.0,
    )

    assert standard["cop"] < low_temperature["cop"] < underfloor["cop"]
    assert standard["cop"] > 2.0
    assert air_air["cop"] > standard["cop"]
    assert ground_source["cop"] > standard["cop"]


def test_air_source_defrost_and_part_load_penalties_apply() -> None:
    full_load = heat_pump_performance(
        "air_air",
        systems_cfg={"heating": {}},
        outdoor_temperature_c=5.0,
        indoor_setpoint_c=21.0,
        useful_heat_w=5000.0,
        capacity_w=5000.0,
    )
    low_load = heat_pump_performance(
        "air_air",
        systems_cfg={"heating": {}},
        outdoor_temperature_c=5.0,
        indoor_setpoint_c=21.0,
        useful_heat_w=250.0,
        capacity_w=5000.0,
    )

    assert full_load["defrost_factor"] < 1.0
    assert low_load["part_load_factor"] < full_load["part_load_factor"]
    assert low_load["cop"] < full_load["cop"]


def test_heat_pump_labels_use_distinct_conversion_factors() -> None:
    air_water = convert_heat_to_carriers(
        5000.0,
        prefix="space_heating",
        technology_type="air_water",
        technologies_cfg={},
        systems_cfg={"heating": {"emitter_type": "standard_radiators"}},
        source_temperature_c=0.0,
        indoor_setpoint_c=21.0,
        capacity_w=8000.0,
    )
    ground_source = convert_heat_to_carriers(
        5000.0,
        prefix="space_heating",
        technology_type="ground_source",
        technologies_cfg={},
        systems_cfg={"heating": {"emitter_type": "standard_radiators"}},
        source_temperature_c=0.0,
        indoor_setpoint_c=21.0,
        capacity_w=8000.0,
    )
    air_air = convert_heat_to_carriers(
        5000.0,
        prefix="space_heating",
        technology_type="air_air",
        technologies_cfg={},
        systems_cfg={"heating": {}},
        source_temperature_c=0.0,
        indoor_setpoint_c=21.0,
        capacity_w=8000.0,
    )

    assert ground_source["heat_pump_cop"] > air_water["heat_pump_cop"]
    assert air_air["heat_pump_cop"] != air_water["heat_pump_cop"]
    assert ground_source["P_el_space_heating_technology_W"] < air_water["P_el_space_heating_technology_W"]


def _hybrid_technologies_cfg(control: dict[str, float]) -> dict:
    return {
        "heating": {
            "performance": {
                "hybrid_hp": {"control": control},
                "gas_boiler": {"seasonal_efficiency": 0.90},
            }
        }
    }


def test_hybrid_control_uses_heat_pump_when_conditions_are_favourable() -> None:
    result = convert_heat_to_carriers(
        4000.0,
        prefix="space_heating",
        technology_type="hybrid_hp_gas",
        technologies_cfg=_hybrid_technologies_cfg(
            {
                "hp_capacity_fraction": 1.0,
                "hp_min_outdoor_temperature_C": -5.0,
                "hp_min_cop": 2.5,
                "hp_max_sink_temperature_C": 55.0,
            }
        ),
        systems_cfg={"heating": {"emitter_type": "underfloor"}},
        source_temperature_c=7.0,
        indoor_setpoint_c=21.0,
        capacity_w=8000.0,
    )

    assert result["hybrid_dispatch_mode"] == "hp_only"
    assert result["hybrid_hp_useful_heat_W"] == 4000.0
    assert result["hybrid_gas_useful_heat_W"] == 0.0
    assert result["P_el_space_heating_technology_W"] > 0.0
    assert result["P_gas_space_heating_W"] == 0.0


def test_hybrid_control_switches_to_gas_when_cop_is_below_threshold() -> None:
    result = convert_heat_to_carriers(
        4000.0,
        prefix="space_heating",
        technology_type="hybrid_hp_gas",
        technologies_cfg=_hybrid_technologies_cfg(
            {
                "hp_capacity_fraction": 1.0,
                "hp_min_outdoor_temperature_C": -20.0,
                "hp_min_cop": 9.0,
                "hp_max_sink_temperature_C": 55.0,
            }
        ),
        systems_cfg={"heating": {"emitter_type": "standard_radiators"}},
        source_temperature_c=0.0,
        indoor_setpoint_c=21.0,
        capacity_w=8000.0,
    )

    assert result["hybrid_dispatch_mode"] == "gas_only_cop_lockout"
    assert result["hybrid_hp_useful_heat_W"] == 0.0
    assert result["hybrid_gas_useful_heat_W"] == 4000.0
    assert result["P_el_space_heating_technology_W"] == 0.0
    assert result["P_gas_space_heating_W"] > 0.0


def test_hybrid_control_splits_load_when_heat_pump_capacity_is_limited() -> None:
    result = convert_heat_to_carriers(
        10000.0,
        prefix="space_heating",
        technology_type="hybrid_hp_gas",
        technologies_cfg=_hybrid_technologies_cfg(
            {
                "hp_capacity_fraction": 0.5,
                "hp_min_outdoor_temperature_C": -5.0,
                "hp_min_cop": 2.5,
                "hp_max_sink_temperature_C": 55.0,
            }
        ),
        systems_cfg={"heating": {"emitter_type": "standard_radiators"}},
        source_temperature_c=7.0,
        indoor_setpoint_c=21.0,
        capacity_w=8000.0,
    )

    assert result["hybrid_dispatch_mode"] == "parallel"
    assert result["hybrid_hp_useful_heat_W"] == 4000.0
    assert result["hybrid_gas_useful_heat_W"] == 6000.0
    assert result["hybrid_hp_available_capacity_W"] == 4000.0
    assert result["P_el_space_heating_technology_W"] > 0.0
    assert result["P_gas_space_heating_W"] > 0.0
