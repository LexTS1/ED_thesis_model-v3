"""Independent household parameter sampling for model_v3 cohort runs."""

from __future__ import annotations

from typing import Any, Mapping

import numpy as np

from model_v3.stochastic.household_classifier import DEFAULT_CLASS_PROBABILITIES, sample_household_class
from model_v3.systems.distributed_energy import value_from_range
from model_v3.systems.technology import normalize_technology_type


DEFAULT_HOUSEHOLD_SIZE_PROBABILITIES = {
    1: 0.36,
    2: 0.31,
    3: 0.145,
    4: 0.11,
    5: 0.04,
    6: 0.02,
    7: 0.01,
}
REFERENCE_OCCUPANTS_PER_DWELLING = 2.0


def _clamp(value: float, lower: float, upper: float) -> float:
    """Clamp a sampled value to a physically meaningful range."""

    return float(min(max(value, lower), upper))


def _bounded_probability(value: Any, default: float) -> float:
    """Resolve a probability-like scalar safely."""

    try:
        return float(min(max(float(value), 0.0), 1.0))
    except (TypeError, ValueError):
        return float(default)


def _technology_probabilities_from_belgian_stock(config: Mapping[str, Any]) -> dict[str, float]:
    """Map Belgian carrier stock shares into technology probabilities."""

    technologies_cfg = dict(config.get("technologies", {}))
    heating_cfg = dict(technologies_cfg.get("heating", {}))
    stock_variables = dict(dict(heating_cfg.get("stock_baseline", {})).get("variables", {}))
    main_share_cfg = dict(stock_variables.get("main_space_heating_share", {}))
    region_key = str(dict(config.get("cohort", {})).get("region", "belgium")).strip().lower().replace("-", "_").replace(" ", "_")
    carrier_shares = dict(main_share_cfg.get(region_key, main_share_cfg.get("belgium", {})))
    mapping_assumptions = dict(heating_cfg.get("mapping_assumptions", {}))

    mapped: dict[str, float] = {}
    for carrier, carrier_share in carrier_shares.items():
        share = max(float(carrier_share), 0.0)
        mapping = dict(dict(mapping_assumptions.get(carrier, {})).get("technologies", {}))
        if not mapping:
            mapping = {carrier: 1.0}
        mapping_total = sum(max(float(value), 0.0) for value in mapping.values())
        if mapping_total <= 0.0:
            continue
        for technology_type, technology_share in mapping.items():
            normalized = normalize_technology_type(technology_type)
            mapped[normalized] = mapped.get(normalized, 0.0) + share * max(float(technology_share), 0.0) / mapping_total

    total = sum(mapped.values())
    if total <= 0.0:
        return {}
    return {technology_type: share / total for technology_type, share in sorted(mapped.items())}


def _legacy_electric_technology_probabilities(technology_cfg: Mapping[str, Any]) -> dict[str, float]:
    """Return the previous heat-pump/resistive sampling semantics."""

    heat_pump_share = float(technology_cfg.get("heat_pump_share", 0.6))
    resistive_share = float(technology_cfg.get("resistive_share", 0.4))
    total_share = heat_pump_share + resistive_share
    if total_share <= 0.0:
        return {"air_water": 0.6, "resistive_direct": 0.4}
    return {
        "air_water": heat_pump_share / total_share,
        "resistive_direct": resistive_share / total_share,
    }


def _sample_from_probabilities(probabilities: Mapping[str, float], rng: np.random.Generator) -> str:
    """Sample one label from a probability mapping."""

    labels = tuple(probabilities.keys())
    weights = np.asarray([max(float(probabilities[label]), 0.0) for label in labels], dtype=float)
    total = float(weights.sum())
    if not labels or total <= 0.0:
        return "resistive_direct"
    return str(rng.choice(labels, p=weights / total))


def _household_size_probabilities(behaviour_cfg: Mapping[str, Any]) -> dict[int, float]:
    """Resolve a 1-7 occupant household-size distribution.

    The default is anchored to the current Belgian household-size evidence used
    in the thesis working notes. A config mapping can override it with integer
    keys or string keys such as ``"7"``.
    """

    configured = behaviour_cfg.get("household_size_probabilities", DEFAULT_HOUSEHOLD_SIZE_PROBABILITIES)
    if not isinstance(configured, Mapping):
        configured = DEFAULT_HOUSEHOLD_SIZE_PROBABILITIES

    probabilities: dict[int, float] = {}
    for raw_size, raw_probability in configured.items():
        try:
            size = int(raw_size)
            probability = float(raw_probability)
        except (TypeError, ValueError):
            continue
        if 1 <= size <= 7 and probability > 0.0:
            probabilities[size] = probabilities.get(size, 0.0) + probability

    total = sum(probabilities.values())
    if total <= 0.0:
        probabilities = dict(DEFAULT_HOUSEHOLD_SIZE_PROBABILITIES)
        total = sum(probabilities.values())
    return {size: probability / total for size, probability in sorted(probabilities.items())}


def sample_occupants_per_dwelling(
    behaviour_cfg: Mapping[str, Any],
    rng: np.random.Generator,
) -> int:
    """Sample household occupants from the configured 1-7 person distribution."""

    probabilities = _household_size_probabilities(behaviour_cfg)
    sizes = tuple(probabilities.keys())
    weights = np.asarray([probabilities[size] for size in sizes], dtype=float)
    return int(rng.choice(sizes, p=weights / weights.sum()))


def sample_household_parameters(config: Mapping[str, Any], rng: np.random.Generator) -> dict[str, Any]:
    """Sample structured household uncertainty blocks for cohort simulation."""

    uncertainty_cfg = dict(config.get("uncertainty", {}))
    physical_cfg = dict(uncertainty_cfg.get("physical", {}))
    behaviour_cfg = dict(uncertainty_cfg.get("behaviour", {}))
    technology_cfg = dict(uncertainty_cfg.get("technology", {}))
    ua_sigma = max(float(physical_cfg.get("UA_sigma", 0.25)), 0.25)
    infiltration_sigma = max(float(physical_cfg.get("infiltration_sigma", 0.4)), 0.4)
    dhw_sigma = max(float(behaviour_cfg.get("dhw_sigma", 0.4)), 0.4)
    appliance_sigma = max(float(behaviour_cfg.get("appliance_sigma", 0.5)), 0.5)
    sigma_h = max(float(behaviour_cfg.get("sigma_h", 0.5)), 0.0)
    dryer_probability = _bounded_probability(behaviour_cfg.get("dryer_presence_probability", 0.45), 0.45)
    mobility_ev_cfg = dict(dict(config.get("mobility", {})).get("ev", {}))
    ev_probability = _bounded_probability(
        value_from_range(
            dict(mobility_ev_cfg.get("ownership", {})).get(
                "household_probability",
                behaviour_cfg.get("ev_presence_probability", 0.0),
            ),
            0.0,
        ),
        0.0,
    )
    der_pv_cfg = dict(dict(config.get("der", {})).get("pv", {}))
    pv_probability = _bounded_probability(
        value_from_range(dict(der_pv_cfg.get("adoption", {})).get("household_probability", 0.0), 0.0),
        0.0,
    )
    household_class = sample_household_class(
        rng=rng,
        probabilities=behaviour_cfg.get("household_class_probabilities", DEFAULT_CLASS_PROBABILITIES),
    )
    occupants_per_dwelling = sample_occupants_per_dwelling(behaviour_cfg=behaviour_cfg, rng=rng)
    household_size_activity_scale = _clamp(
        (float(occupants_per_dwelling) / REFERENCE_OCCUPANTS_PER_DWELLING) ** 0.65,
        0.55,
        2.25,
    )

    if bool(technology_cfg.get("use_belgian_stock_baseline", False)):
        technology_probabilities = _technology_probabilities_from_belgian_stock(config)
    else:
        technology_probabilities = _legacy_electric_technology_probabilities(technology_cfg)
    technology_type = _sample_from_probabilities(technology_probabilities, rng)
    schedule_variation_seed = int(rng.integers(0, 2**31 - 1))
    load_variation_seed = int(rng.integers(0, 2**31 - 1))
    occupancy_state_biases = {
        "away": _clamp(float(rng.lognormal(mean=0.0, sigma=0.20)), 0.5, 2.0),
        "awake": _clamp(float(rng.lognormal(mean=0.0, sigma=0.20)), 0.5, 2.0),
        "sleep": _clamp(float(rng.lognormal(mean=0.0, sigma=0.20)), 0.5, 2.0),
    }

    return {
        "physical": {
            "UA_scale_factor": _clamp(
                float(rng.normal(1.0, ua_sigma)),
                0.4,
                2.0,
            ),
            "thermal_mass_scale": _clamp(
                float(rng.normal(1.0, float(physical_cfg.get("thermal_mass_sigma", 0.20)))),
                0.4,
                2.5,
            ),
            "infiltration_rate": _clamp(
                float(rng.lognormal(mean=0.0, sigma=infiltration_sigma)),
                0.3,
                3.5,
            ),
            "cop_scale": _clamp(
                float(rng.normal(1.0, float(physical_cfg.get("cop_sigma", 0.10)))),
                0.5,
                1.5,
            ),
        },
        "behaviour": {
            "occupancy_intensity": _clamp(
                float(rng.normal(1.0, float(behaviour_cfg.get("occupancy_sigma", 0.2)))),
                0.3,
                2.5,
            ),
            "schedule_variation_seed": schedule_variation_seed,
            "load_variation_seed": load_variation_seed,
            "occupancy_time_shift_hours": float(rng.uniform(-2.0, 2.0)),
            "transition_variability_scale": float(rng.uniform(1.2, 1.5)),
            "state_duration_scale": _clamp(float(rng.lognormal(mean=0.0, sigma=0.3)), 0.5, 2.5),
            "occupancy_state_biases": occupancy_state_biases,
            "household_class": household_class.name,
            "occupants_per_dwelling": occupants_per_dwelling,
            "household_size_activity_scale": household_size_activity_scale,
            "household_random_effect_u": float(rng.normal(0.0, sigma_h)),
            "has_dryer": bool(rng.random() < dryer_probability),
            "has_ev": bool(rng.random() < ev_probability),
            "setpoint_shift_C": _clamp(float(rng.normal(0.0, 1.5)), -4.0, 4.0),
            "dhw_intensity_scale": _clamp(
                float(rng.lognormal(mean=0.0, sigma=dhw_sigma)),
                0.25,
                4.0,
            ),
            "dhw_event_frequency_scale": _clamp(float(rng.lognormal(mean=0.0, sigma=0.25)), 0.2, 3.0),
            "dhw_event_volume_sigma": float(behaviour_cfg.get("dhw_event_volume_sigma", 0.4)),
            "dhw_event_lambda": float(behaviour_cfg.get("dhw_event_lambda", 3.0)),
            "dhw_start_jitter_hours": float(behaviour_cfg.get("dhw_start_jitter_hours", 0.75)),
            "appliance_intensity_scale": _clamp(
                float(rng.lognormal(mean=0.0, sigma=appliance_sigma)) * household_size_activity_scale,
                0.2,
                4.0,
            ),
            "appliance_start_jitter_hours": float(behaviour_cfg.get("appliance_start_jitter_hours", 1.0)),
            "high_frequency_sigma": float(behaviour_cfg.get("high_frequency_sigma", 0.08)),
        },
        "technology": {
            "technology_type": technology_type,
            "technology_probability": float(technology_probabilities.get(technology_type, 0.0)),
            "technology_probability_source": (
                "belgian_carrier_stock_mapping"
                if bool(technology_cfg.get("use_belgian_stock_baseline", False))
                else "legacy_heat_pump_resistive_uncertainty"
            ),
            "heating_cop": value_from_range(dict(dict(config.get("technologies", {})).get("heating", {})).get("performance", {}).get("heat_pump", {}).get("air_water_radiators_spf"), 3.0),
            "heating_capacity_scale": _clamp(float(rng.normal(1.0, 0.2)), 0.3, 2.5),
            "has_pv": bool(rng.random() < pv_probability),
            "pv_capacity_kwp": _clamp(
                float(rng.normal(value_from_range(der_pv_cfg.get("system_size_kwp"), 6.0), 1.0)),
                value_from_range(der_pv_cfg.get("system_size_kwp", {}).get("low") if isinstance(der_pv_cfg.get("system_size_kwp"), Mapping) else None, 4.0),
                value_from_range(der_pv_cfg.get("system_size_kwp", {}).get("high") if isinstance(der_pv_cfg.get("system_size_kwp"), Mapping) else None, 10.0),
            ),
            "future_extension_hook": ("hybrid_control", "battery_dispatch"),
        },
    }
