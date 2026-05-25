"""Empirical heat-pump COP parameterisation for hourly system runs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from model_v3.systems.distributed_energy import value_from_range


@dataclass(frozen=True)
class EmitterSpec:
    """Hydronic emitter weather-curve parameters."""

    sink_high_c: float
    sink_low_c: float


@dataclass(frozen=True)
class HeatPumpSpec:
    """Compact source/sink COP model parameters."""

    hp_type: str
    default_refrigerant: str
    source_ref_c: float
    sink_ref_c: float
    cop_ref: float
    min_cop: float
    max_cop: float
    source_slope_per_k: float
    sink_slope_per_k: float
    defrost_penalty_fraction: float = 0.0
    part_load_min: float = 0.20
    degradation_coefficient: float = 0.95
    capacity_fraction_at_ref: float = 1.0
    capacity_ref_temp_c: float = -7.0
    capacity_fraction_at_low: float = 1.0
    capacity_low_temp_c: float = -15.0


EMITTER_SPECS: dict[str, EmitterSpec] = {
    "underfloor": EmitterSpec(sink_high_c=35.0, sink_low_c=27.0),
    "low_temperature_radiators": EmitterSpec(sink_high_c=45.0, sink_low_c=35.0),
    "standard_radiators": EmitterSpec(sink_high_c=55.0, sink_low_c=42.0),
    "high_temperature_radiators": EmitterSpec(sink_high_c=60.0, sink_low_c=45.0),
    "fan_coils": EmitterSpec(sink_high_c=42.0, sink_low_c=28.0),
}

HEAT_PUMP_SPECS: dict[str, HeatPumpSpec] = {
    "air_water": HeatPumpSpec(
        hp_type="air_water",
        default_refrigerant="R290",
        source_ref_c=7.0,
        sink_ref_c=35.0,
        cop_ref=4.4,
        min_cop=2.0,
        max_cop=5.0,
        source_slope_per_k=0.11,
        sink_slope_per_k=0.095,
        defrost_penalty_fraction=0.12,
        part_load_min=0.20,
        degradation_coefficient=0.95,
        capacity_fraction_at_ref=1.0,
        capacity_ref_temp_c=-7.0,
        capacity_fraction_at_low=0.80,
        capacity_low_temp_c=-15.0,
    ),
    "air_air": HeatPumpSpec(
        hp_type="air_air",
        default_refrigerant="R32",
        source_ref_c=7.0,
        sink_ref_c=20.0,
        cop_ref=4.4,
        min_cop=2.0,
        max_cop=5.3,
        source_slope_per_k=0.14,
        sink_slope_per_k=0.03,
        defrost_penalty_fraction=0.12,
        part_load_min=0.20,
        degradation_coefficient=0.95,
        capacity_fraction_at_ref=0.85,
        capacity_ref_temp_c=-7.0,
        capacity_fraction_at_low=0.75,
        capacity_low_temp_c=-15.0,
    ),
    "ground_source": HeatPumpSpec(
        hp_type="ground_source",
        default_refrigerant="R290",
        source_ref_c=0.0,
        sink_ref_c=35.0,
        cop_ref=3.8,
        min_cop=3.0,
        max_cop=5.2,
        source_slope_per_k=0.045,
        sink_slope_per_k=0.085,
        part_load_min=0.15,
        degradation_coefficient=0.99,
    ),
    "hpwh": HeatPumpSpec(
        hp_type="hpwh",
        default_refrigerant="R744",
        source_ref_c=15.0,
        sink_ref_c=55.0,
        cop_ref=3.1,
        min_cop=2.0,
        max_cop=3.5,
        source_slope_per_k=0.05,
        sink_slope_per_k=0.055,
        part_load_min=0.20,
        degradation_coefficient=0.95,
    ),
}


def _clamp(value: float, lower: float, upper: float) -> float:
    return float(min(max(float(value), float(lower)), float(upper)))


def _as_float(mapping: Mapping[str, Any], key: str, default: float) -> float:
    try:
        return float(mapping.get(key, default))
    except (TypeError, ValueError):
        return float(default)


def _normalise_label(value: Any, default: str) -> str:
    raw = str(value if value not in {None, ""} else default).strip().lower()
    return raw.replace("-", "_").replace(" ", "_")


def weather_curve_sink_temperature(
    emitter_type: str,
    outdoor_temperature_c: float,
    *,
    design_outdoor_c: float = -7.0,
    mild_outdoor_c: float = 15.0,
) -> float:
    """Return the hydronic supply/sink temperature from an emitter weather curve."""

    emitter = EMITTER_SPECS.get(_normalise_label(emitter_type, "standard_radiators"), EMITTER_SPECS["standard_radiators"])
    if mild_outdoor_c <= design_outdoor_c:
        return float(emitter.sink_high_c)
    fraction = (float(mild_outdoor_c) - float(outdoor_temperature_c)) / (float(mild_outdoor_c) - float(design_outdoor_c))
    fraction = _clamp(fraction, 0.0, 1.0)
    return float(emitter.sink_low_c + fraction * (emitter.sink_high_c - emitter.sink_low_c))


def _source_temperature(
    hp_type: str,
    heating_cfg: Mapping[str, Any],
    dhw_cfg: Mapping[str, Any],
    outdoor_temperature_c: float,
) -> float:
    if hp_type == "ground_source":
        return value_from_range(heating_cfg.get("ground_source_temperature_C"), 8.0)
    if hp_type == "hpwh":
        return _as_float(dhw_cfg, "ambient_source_temperature_C", 15.0)
    return float(outdoor_temperature_c)


def _sink_temperature(
    hp_type: str,
    heating_cfg: Mapping[str, Any],
    dhw_cfg: Mapping[str, Any],
    outdoor_temperature_c: float,
    indoor_setpoint_c: float,
) -> tuple[float, str]:
    if hp_type == "air_air":
        return _as_float(heating_cfg, "sink_temperature_C", float(indoor_setpoint_c)), "air_distribution"
    if hp_type == "hpwh":
        return _as_float(dhw_cfg, "sink_temperature_C", _as_float(dhw_cfg, "tank_temperature_C", 55.0)), "dhw_tank"
    if "sink_temperature_C" in heating_cfg:
        return _as_float(heating_cfg, "sink_temperature_C", 45.0), _normalise_label(
            heating_cfg.get("emitter_type"), "fixed_sink"
        )
    emitter_type = _normalise_label(heating_cfg.get("emitter_type"), "standard_radiators")
    design_outdoor_c = _as_float(heating_cfg, "design_outdoor_temperature_C", -7.0)
    mild_outdoor_c = _as_float(heating_cfg, "mild_outdoor_temperature_C", 15.0)
    return weather_curve_sink_temperature(
        emitter_type,
        outdoor_temperature_c,
        design_outdoor_c=design_outdoor_c,
        mild_outdoor_c=mild_outdoor_c,
    ), emitter_type


def _capacity_fraction(spec: HeatPumpSpec, source_temperature_c: float) -> float:
    if source_temperature_c >= spec.capacity_ref_temp_c:
        return 1.0
    if source_temperature_c <= spec.capacity_low_temp_c:
        return float(spec.capacity_fraction_at_low)
    span = spec.capacity_ref_temp_c - spec.capacity_low_temp_c
    if span <= 0.0:
        return float(spec.capacity_fraction_at_low)
    fraction = (source_temperature_c - spec.capacity_low_temp_c) / span
    return float(spec.capacity_fraction_at_low + fraction * (spec.capacity_fraction_at_ref - spec.capacity_fraction_at_low))


def _part_load_factor(useful_heat_w: float, capacity_w: float, spec: HeatPumpSpec) -> tuple[float, float]:
    if capacity_w <= 0.0:
        return 1.0, 1.0
    plr = _clamp(useful_heat_w / max(capacity_w, 1e-9), 0.0, 1.0)
    if plr >= spec.part_load_min or spec.part_load_min <= 0.0:
        return plr, 1.0
    factor = 1.0 - (1.0 - spec.degradation_coefficient) * (1.0 - plr / spec.part_load_min)
    return plr, _clamp(factor, spec.degradation_coefficient, 1.0)


def heat_pump_performance(
    hp_type: str,
    *,
    systems_cfg: Mapping[str, Any],
    outdoor_temperature_c: float,
    indoor_setpoint_c: float,
    useful_heat_w: float,
    capacity_w: float,
    mode: str = "heating",
) -> dict[str, Any]:
    """Return effective COP and diagnostic factors for one heat-pump operating point."""

    raw_hp_type = _normalise_label(hp_type, "air_water")
    model_hp_type = "air_water" if raw_hp_type == "hybrid_hp_gas" else raw_hp_type
    spec = HEAT_PUMP_SPECS.get(model_hp_type, HEAT_PUMP_SPECS["air_water"])
    heating_cfg = dict(dict(systems_cfg).get("heating", {}))
    dhw_cfg = dict(dict(systems_cfg).get("dhw", {}))
    hp_cfg = dict(dict(systems_cfg).get("heat_pump_performance", {}))
    type_overrides = dict(dict(hp_cfg.get("types", {})).get(model_hp_type, {}))

    source_temperature_c = _source_temperature(model_hp_type, heating_cfg, dhw_cfg, outdoor_temperature_c)
    sink_temperature_c, emitter_type = _sink_temperature(
        model_hp_type,
        heating_cfg,
        dhw_cfg,
        outdoor_temperature_c,
        indoor_setpoint_c,
    )
    cop_ref_cfg = dhw_cfg if model_hp_type == "hpwh" else heating_cfg
    cop_ref = _as_float(cop_ref_cfg, "cop_ref", _as_float(type_overrides, "cop_ref", spec.cop_ref))
    min_cop = _as_float(type_overrides, "min_cop", spec.min_cop)
    max_cop = _as_float(type_overrides, "max_cop", spec.max_cop)
    source_slope = _as_float(type_overrides, "source_slope_per_K", spec.source_slope_per_k)
    sink_slope = _as_float(type_overrides, "sink_slope_per_K", spec.sink_slope_per_k)
    base_cop = cop_ref + source_slope * (source_temperature_c - spec.source_ref_c) - sink_slope * (
        sink_temperature_c - spec.sink_ref_c
    )
    base_cop = _clamp(base_cop, min_cop, max_cop)

    defrost_penalty = _as_float(type_overrides, "defrost_penalty_fraction", spec.defrost_penalty_fraction)
    if (
        mode == "heating"
        and model_hp_type in {"air_water", "air_air"}
        and -3.0 <= source_temperature_c <= 6.0
    ):
        defrost_factor = 1.0 - _clamp(defrost_penalty, 0.0, 0.5)
    else:
        defrost_factor = 1.0

    part_load_ratio, part_load_factor = _part_load_factor(
        max(float(useful_heat_w), 0.0),
        max(float(capacity_w), 0.0),
        spec,
    )
    effective_cop = _clamp(base_cop * defrost_factor * part_load_factor, min_cop, max_cop)
    refrigerant = str(type_overrides.get("refrigerant", heating_cfg.get("refrigerant", spec.default_refrigerant)))
    capacity_fraction = _capacity_fraction(spec, source_temperature_c)

    return {
        "cop": float(effective_cop),
        "cop_base": float(base_cop),
        "hp_type": raw_hp_type,
        "performance_hp_type": model_hp_type,
        "emitter_type": emitter_type,
        "refrigerant": refrigerant,
        "source_temperature_C": float(source_temperature_c),
        "sink_temperature_C": float(sink_temperature_c),
        "defrost_factor": float(defrost_factor),
        "part_load_ratio": float(part_load_ratio),
        "part_load_factor": float(part_load_factor),
        "capacity_available_fraction": float(capacity_fraction),
    }
