"""Independent household parameter sampling for model_v3 cohort runs."""

from __future__ import annotations

import csv
from pathlib import Path
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


def _configured_technology_probabilities(
    probabilities_cfg: Any,
) -> dict[str, float]:
    """Resolve explicit scenario-case technology probabilities."""

    if not isinstance(probabilities_cfg, Mapping):
        return {}
    probabilities: dict[str, float] = {}
    for raw_label, raw_probability in dict(probabilities_cfg).items():
        try:
            probability = max(float(raw_probability), 0.0)
        except (TypeError, ValueError):
            continue
        if probability <= 0.0:
            continue
        label = normalize_technology_type(raw_label)
        probabilities[label] = probabilities.get(label, 0.0) + probability
    total = sum(probabilities.values())
    if total <= 0.0:
        return {}
    return {label: value / total for label, value in sorted(probabilities.items())}


def _sample_from_probabilities(
    probabilities: Mapping[str, float],
    rng: np.random.Generator,
    *,
    default: str = "resistive_direct",
) -> str:
    """Sample one label from a probability mapping."""

    labels = tuple(probabilities.keys())
    weights = np.asarray([max(float(probabilities[label]), 0.0) for label in labels], dtype=float)
    total = float(weights.sum())
    if not labels or total <= 0.0:
        return default
    return str(rng.choice(labels, p=weights / total))


def _heating_probabilities(config: Mapping[str, Any], technology_cfg: Mapping[str, Any]) -> tuple[dict[str, float], str]:
    explicit = _configured_technology_probabilities(technology_cfg.get("heating_technology_probabilities"))
    if explicit:
        return explicit, "scenario_case_assignment"
    if bool(technology_cfg.get("use_belgian_stock_baseline", False)):
        return _technology_probabilities_from_belgian_stock(config), "belgian_carrier_stock_mapping"
    return _legacy_electric_technology_probabilities(technology_cfg), "legacy_heat_pump_resistive_uncertainty"


def _dhw_probabilities(technology_cfg: Mapping[str, Any]) -> tuple[dict[str, float], str]:
    explicit = _configured_technology_probabilities(technology_cfg.get("dhw_technology_probabilities"))
    if explicit:
        return explicit, "scenario_case_assignment"
    return {"linked_to_space_heating": 1.0}, "linked_to_space_heating_default"


def _emitter_probabilities(technology_cfg: Mapping[str, Any]) -> dict[str, float]:
    explicit = _configured_technology_probabilities(technology_cfg.get("emitter_type_probabilities"))
    return explicit or {"standard_radiators": 1.0}


def _default_refrigerant_for_technology(technology_type: str) -> str:
    tech = normalize_technology_type(technology_type)
    if tech == "air_air":
        return "R32"
    if tech in {"air_water", "hybrid_hp_gas", "ground_source"}:
        return "R290"
    if tech == "hpwh":
        return "R744"
    return ""


def _heating_cop_for_technology(technology_type: str, config: Mapping[str, Any]) -> float:
    heat_pump_model_cfg = dict(dict(dict(config.get("systems", {})).get("heat_pump_performance", {})).get("types", {}))
    tech = normalize_technology_type(technology_type)
    configured = dict(heat_pump_model_cfg.get(tech, {}))
    if configured.get("cop_ref") is not None:
        return value_from_range(configured.get("cop_ref"), 5.0)
    if tech == "ground_source":
        return 4.2
    if tech == "air_air":
        return 5.5
    if tech == "air_water":
        return 5.0
    if tech == "hybrid_hp_gas":
        return 5.0
    return 1.0


def _resolve_path(path_str: str | None) -> Path | None:
    if not path_str:
        return None
    path = Path(path_str)
    return path if path.is_absolute() else Path.cwd() / path


def _sample_building_archetype(
    config: Mapping[str, Any],
    rng: np.random.Generator,
    physical_cfg: Mapping[str, Any],
) -> dict[str, Any]:
    """Sample one building archetype ID from stock weights when configured."""

    building_cfg = dict(config.get("building", {}))
    archetype_cfg = dict(building_cfg.get("archetype_source", {}))
    selection_mode = str(archetype_cfg.get("selection", "highest_stock_weight"))
    enabled = bool(physical_cfg.get("sample_building_archetype_by_stock_weight", False)) or selection_mode == "stock_weight_sample"
    if not enabled:
        return {}

    resolved_path = _resolve_path(str(archetype_cfg.get("file_path", "")))
    if resolved_path is None or not resolved_path.exists():
        return {}

    rows: list[dict[str, str]] = []
    with resolved_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            try:
                stock_weight = float(row.get("stock_weight", 0.0))
            except (TypeError, ValueError):
                stock_weight = 0.0
            if row.get("archetype_id") and stock_weight > 0.0:
                row["_stock_weight_float"] = str(stock_weight)
                rows.append(row)

    if not rows:
        return {}

    weights = np.asarray([float(row["_stock_weight_float"]) for row in rows], dtype=float)
    selected_index = int(rng.choice(np.arange(len(rows)), p=weights / weights.sum()))
    selected = rows[selected_index]
    return {
        "building_archetype_id": str(selected["archetype_id"]),
        "building_archetype_stock_weight": float(selected["_stock_weight_float"]),
        "building_archetype_selection_source": "stock_weight_sample",
        "building_archetype_table": str(resolved_path),
        "dwelling_type": str(selected.get("dwelling_type", "")),
        "renovation_state": str(selected.get("renovation_state", "")),
        "construction_period_id": str(selected.get("construction_period_id", "")),
        "u_value_package_id": str(selected.get("u_value_package_id", "")),
    }


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
            technology_cfg.get(
                "ev_household_probability",
                dict(mobility_ev_cfg.get("ownership", {})).get(
                    "household_probability",
                    behaviour_cfg.get("ev_presence_probability", 0.0),
                ),
            ),
            0.0,
        ),
        0.0,
    )
    der_pv_cfg = dict(dict(config.get("der", {})).get("pv", {}))
    pv_probability = _bounded_probability(
        value_from_range(
            technology_cfg.get(
                "pv_household_probability",
                dict(der_pv_cfg.get("adoption", {})).get("household_probability", 0.0),
            ),
            0.0,
        ),
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

    technology_probabilities, technology_probability_source = _heating_probabilities(config, technology_cfg)
    technology_type = _sample_from_probabilities(technology_probabilities, rng)
    emitter_probabilities = _emitter_probabilities(technology_cfg)
    emitter_type = _sample_from_probabilities(emitter_probabilities, rng, default="standard_radiators")
    dhw_technology_probabilities, dhw_probability_source = _dhw_probabilities(technology_cfg)
    dhw_technology_type = _sample_from_probabilities(
        dhw_technology_probabilities,
        rng,
        default="linked_to_space_heating",
    )
    building_archetype_sample = _sample_building_archetype(config=config, rng=rng, physical_cfg=physical_cfg)
    schedule_variation_seed = int(rng.integers(0, 2**31 - 1))
    load_variation_seed = int(rng.integers(0, 2**31 - 1))
    occupancy_state_biases = {
        "away": _clamp(float(rng.lognormal(mean=0.0, sigma=0.20)), 0.5, 2.0),
        "awake": _clamp(float(rng.lognormal(mean=0.0, sigma=0.20)), 0.5, 2.0),
        "sleep": _clamp(float(rng.lognormal(mean=0.0, sigma=0.20)), 0.5, 2.0),
    }
    has_ev = bool(rng.random() < ev_probability)
    has_pv = bool(rng.random() < pv_probability)

    return {
        "physical": {
            **building_archetype_sample,
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
            "has_ev": has_ev,
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
            "technology_probability_source": technology_probability_source,
            "dhw_technology_type": dhw_technology_type,
            "dhw_technology_probability": float(dhw_technology_probabilities.get(dhw_technology_type, 0.0)),
            "dhw_technology_probability_source": dhw_probability_source,
            "heating_cop": _heating_cop_for_technology(technology_type, config),
            "emitter_type": "air_distribution" if technology_type == "air_air" else emitter_type,
            "emitter_type_probability": float(emitter_probabilities.get(emitter_type, 0.0)),
            "refrigerant": _default_refrigerant_for_technology(technology_type),
            "heating_capacity_scale": _clamp(float(rng.normal(1.0, 0.2)), 0.3, 2.5),
            "has_pv": has_pv,
            "pv_household_probability": pv_probability,
            "pv_capacity_kwp": _clamp(
                float(rng.normal(value_from_range(der_pv_cfg.get("system_size_kwp"), 6.0), 1.0)),
                value_from_range(der_pv_cfg.get("system_size_kwp", {}).get("low") if isinstance(der_pv_cfg.get("system_size_kwp"), Mapping) else None, 4.0),
                value_from_range(der_pv_cfg.get("system_size_kwp", {}).get("high") if isinstance(der_pv_cfg.get("system_size_kwp"), Mapping) else None, 10.0),
            ),
            "has_ev": has_ev,
            "ev_household_probability": ev_probability,
            "future_extension_hook": ("hybrid_control", "battery_dispatch"),
        },
    }
