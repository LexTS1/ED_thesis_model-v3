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
    assert mild_standard_radiators == 38.0
    assert cold_underfloor < cold_standard_radiators


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
