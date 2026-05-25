"""Behavioural household stochastic layer for explicit appliance events."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping

import numpy as np

from model_v3.control.thermostat import sample_daily_overrides, sample_deadband_c, sample_setpoint_schedule_c
from model_v3.interfaces import InputDataset, TimeSeriesData
from model_v3.stochastic.base_load import generate_base_load_profile
from model_v3.stochastic.dhw_generator import generate_dhw_events
from model_v3.stochastic.event_generator import generate_appliance_events
from model_v3.stochastic.household_classifier import resolve_household_class
from model_v3.stochastic.lighting_model import generate_lighting_profile
from model_v3.stochastic.shared_driver import sample_daily_peak_driver
from model_v3.systems.distributed_energy import build_ev_charging_profile
from model_v3.utils.energy import integrate_power_series_kwh


def _clean_series(values: tuple[float | None, ...], n_steps: int) -> np.ndarray:
    """Coerce sparse timeseries values to a safe numeric array."""

    series = np.asarray([0.0 if value is None else float(value) for value in values], dtype=float)
    if len(series) == n_steps:
        return np.nan_to_num(series, nan=0.0, posinf=0.0, neginf=0.0)
    if len(series) == 0:
        return np.zeros(n_steps, dtype=float)
    clipped = np.zeros(n_steps, dtype=float)
    clipped[: min(len(series), n_steps)] = series[: min(len(series), n_steps)]
    return np.nan_to_num(clipped, nan=0.0, posinf=0.0, neginf=0.0)


def _renormalize_to_target_energy(
    values: np.ndarray,
    timestamps: tuple[Any, ...],
    target_energy_kwh: float,
) -> np.ndarray:
    """Preserve annual energy while allowing stochastic redistribution in time."""

    clipped = np.clip(np.asarray(values, dtype=float), 0.0, None)
    if clipped.size == 0:
        return clipped
    actual_energy_kwh = integrate_power_series_kwh(clipped.tolist(), timestamps=timestamps)
    if target_energy_kwh <= 1e-9:
        return np.zeros_like(clipped)
    if actual_energy_kwh <= 1e-9:
        return np.full_like(clipped, target_energy_kwh * 1000.0 / max(8760.0, 1.0))
    return clipped * (float(target_energy_kwh) / float(actual_energy_kwh))


def _sample_positive_parameter(
    *,
    mean_value: float,
    sigma: float,
    rng: np.random.Generator,
    lower_fraction: float,
    upper_fraction: float,
) -> float:
    """Sample a positive household parameter and clamp unrealistic extremes."""

    mean_value = max(float(mean_value), 1e-6)
    sampled = float(rng.normal(mean_value, max(float(sigma), 1e-9)))
    lower_bound = max(mean_value * float(lower_fraction), 1e-6)
    upper_bound = max(mean_value * float(upper_fraction), lower_bound)
    return float(np.clip(sampled, lower_bound, upper_bound))


def _resolve_ghi_series(input_data: InputDataset, n_steps: int) -> np.ndarray:
    """Resolve a daylight proxy, preferring measured GHI and falling back to facade irradiance."""

    weather = input_data.source_data.get("weather")
    if weather is not None and "ghi_Wm2" in weather.columns:
        return np.clip(_clean_series(weather.columns.get("ghi_Wm2", ()), n_steps=n_steps), 0.0, None)

    solar = input_data.source_data.get("solar")
    if solar is None:
        return np.zeros(n_steps, dtype=float)

    orientation_columns = []
    for column_name in ("I_north", "I_east", "I_south", "I_west"):
        if column_name in solar.columns:
            orientation_columns.append(np.clip(_clean_series(solar.columns.get(column_name, ()), n_steps=n_steps), 0.0, None))
    if not orientation_columns:
        return np.zeros(n_steps, dtype=float)
    return np.nan_to_num(np.mean(np.vstack(orientation_columns), axis=0), nan=0.0, posinf=0.0, neginf=0.0)


def simulate_household_electricity(
    input_data: InputDataset,
    config: Mapping[str, Any],
    sampled_params: Mapping[str, Any],
) -> tuple[InputDataset, dict[str, Any]]:
    """Apply household class, heterogeneity, appliance events, and DHW events."""

    behaviour = dict(sampled_params.get("behaviour", {}))
    behaviour_cfg = dict(dict(config.get("uncertainty", {})).get("behaviour", {}))
    load_profiles = input_data.source_data["load_profiles"]
    timestamps = tuple(load_profiles.timestamps)
    n_steps = len(timestamps)

    household_class = resolve_household_class(str(behaviour.get("household_class", "low_flat")))
    load_seed = int(behaviour.get("load_variation_seed", 0))
    rng = np.random.default_rng(load_seed)
    UA_h = _sample_positive_parameter(
        mean_value=input_data.heat_loss_coefficient_W_per_C,
        sigma=0.1 * max(input_data.heat_loss_coefficient_W_per_C, 1e-9),
        rng=rng,
        lower_fraction=0.60,
        upper_fraction=1.40,
    )
    C_h = _sample_positive_parameter(
        mean_value=input_data.C_J_per_K,
        sigma=0.15 * max(input_data.C_J_per_K, 1e-9),
        rng=rng,
        lower_fraction=0.55,
        upper_fraction=1.60,
    )
    control_schedule = {
        "deadband_c": sample_deadband_c(rng),
        "setpoint_schedule_c": sample_setpoint_schedule_c(household_class.name, rng),
        "daily_overrides": sample_daily_overrides(timestamps=timestamps, rng=rng),
    }
    sigma_peak = max(float(behaviour_cfg.get("sigma_peak", 0.3)), 0.0)
    event_blend_scale = max(float(behaviour_cfg.get("event_blend_scale", 1.0)), 0.0)
    event_rate_scale = max(float(behaviour_cfg.get("event_rate_scale", 1.0)), 1e-6)
    shared_peak_seed = int(dict(config.get("cohort", {})).get("random_seed", 42))
    daily_peak_driver = sample_daily_peak_driver(
        timestamps=timestamps,
        seed=shared_peak_seed,
        sigma_peak=sigma_peak,
    )

    base_appliances = _clean_series(load_profiles.columns.get("appliances", ()), n_steps=n_steps)
    base_lighting = _clean_series(load_profiles.columns.get("lighting", ()), n_steps=n_steps)
    base_cooking = _clean_series(load_profiles.columns.get("cooking", ()), n_steps=n_steps)
    target_energy_appliances_kwh = integrate_power_series_kwh(base_appliances.tolist(), timestamps=timestamps)
    target_energy_lighting_kwh = integrate_power_series_kwh(base_lighting.tolist(), timestamps=timestamps)
    target_energy_cooking_kwh = integrate_power_series_kwh(base_cooking.tolist(), timestamps=timestamps)
    ghi_wm2 = _resolve_ghi_series(input_data=input_data, n_steps=n_steps)
    base_load_output = generate_base_load_profile(
        n_steps=n_steps,
        household_class=household_class.name,
        rng=rng,
    )
    base_load_profile = np.asarray(base_load_output["profile_w"], dtype=float)

    generated_events = generate_appliance_events(
        timestamps=timestamps,
        target_resolution_seconds=int(input_data.target_resolution_seconds),
        household_class=household_class,
        household_random_effect_u=float(behaviour.get("household_random_effect_u", 0.0)),
        occupancy_scale=float(behaviour.get("occupancy_intensity", 1.0))
        * float(behaviour.get("household_size_activity_scale", 1.0)),
        daily_peak_driver=daily_peak_driver,
        rng=rng,
        event_rate_scale=event_rate_scale,
        has_dryer=bool(behaviour.get("has_dryer", False)),
        has_ev=bool(behaviour.get("has_ev", False)) and bool(behaviour_cfg.get("legacy_ev_events_enabled", False)),
    )
    ev_cfg = dict(dict(config.get("mobility", {})).get("ev", {}))
    ev_charging_profile = np.asarray(
        build_ev_charging_profile(
            timestamps=timestamps,
            ev_cfg=ev_cfg,
            has_ev=bool(behaviour.get("has_ev", False)),
            random_seed=load_seed,
        ),
        dtype=float,
    )
    cohort_metadata = dict(input_data.metadata.get("cohort", {}))
    dhw_calibration_cfg = dict(behaviour_cfg.get("dhw_calibration", {}))
    dhw_calibration_enabled = bool(dhw_calibration_cfg.get("enabled", False))
    generated_dhw = generate_dhw_events(
        timestamps=timestamps,
        target_resolution_seconds=int(input_data.target_resolution_seconds),
        occupancy_spec=dict(input_data.metadata.get("occupancy_spec", {})),
        occupants_per_dwelling=float(input_data.occupants_per_dwelling),
        occupancy_threshold=float(dict(config.get("model", {})).get("occupancy_threshold", 0.5)),
        schedule_variation_seed=int(cohort_metadata.get("schedule_variation_seed", 0)),
        occupancy_time_shift_hours=float(cohort_metadata.get("occupancy_time_shift_hours", 0.0)),
        transition_variability_scale=float(cohort_metadata.get("transition_variability_scale", 1.0)),
        state_duration_scale=float(cohort_metadata.get("state_duration_scale", 1.0)),
        occupancy_state_biases=dict(cohort_metadata.get("occupancy_state_biases", {})),
        household_class=household_class,
        household_random_effect_u=float(behaviour.get("household_random_effect_u", 0.0)),
        rng=rng,
        event_frequency_scale=float(behaviour.get("dhw_event_frequency_scale", 1.0)),
        event_intensity_scale=float(behaviour.get("dhw_intensity_scale", 1.0)),
        dhw_calibration=dhw_calibration_cfg,
    )

    appliance_events = np.asarray(generated_events["output_loads"]["appliances"], dtype=float) * event_blend_scale
    cooking_events = np.asarray(generated_events["output_loads"]["cooking"], dtype=float) * event_blend_scale
    dhw_cohort_scale = 1.0 if dhw_calibration_enabled else max(float(behaviour_cfg.get("dhw_cohort_scale", 1.0)), 0.0)
    dhw_profile = np.clip(np.asarray(generated_dhw["output_load_W"], dtype=float) * dhw_cohort_scale, 0.0, None)
    dhw_component_profiles = {
        name: tuple(
            float(value)
            for value in np.clip(np.asarray(values, dtype=float) * dhw_cohort_scale, 0.0, None).tolist()
        )
        for name, values in dict(generated_dhw["component_output_loads_W"]).items()
    }
    dhw_event_summary = dict(generated_dhw["event_summary"])
    dhw_event_summary["peak_dhw_load_W"] = float(np.max(dhw_profile)) if len(dhw_profile) else 0.0
    dhw_event_summary["aggregate_dhw_energy_kWh"] = integrate_power_series_kwh(dhw_profile.tolist(), timestamps=timestamps)
    lighting_output = generate_lighting_profile(
        timestamps=timestamps,
        base_lighting_w=tuple(float(value) for value in base_lighting.tolist()),
        occupancy=tuple(float(value) for value in generated_dhw["occupied_probability"]),
        ghi_wm2=tuple(float(value) for value in ghi_wm2.tolist()),
        household_class=household_class.name,
    )
    lighting_profile = np.asarray(lighting_output["profile_w"], dtype=float)

    pre_renorm_appliances = np.clip(base_load_profile + appliance_events, 0.0, None)
    pre_renorm_cooking = np.clip(cooking_events, 0.0, None)
    combined_appliances = _renormalize_to_target_energy(
        pre_renorm_appliances,
        timestamps=timestamps,
        target_energy_kwh=target_energy_appliances_kwh,
    )
    combined_lighting = _renormalize_to_target_energy(
        lighting_profile,
        timestamps=timestamps,
        target_energy_kwh=target_energy_lighting_kwh,
    )
    combined_cooking = _renormalize_to_target_energy(
        pre_renorm_cooking,
        timestamps=timestamps,
        target_energy_kwh=target_energy_cooking_kwh,
    )
    appliance_scaling = np.divide(
        combined_appliances,
        np.maximum(pre_renorm_appliances, 1e-9),
        out=np.zeros_like(combined_appliances),
        where=pre_renorm_appliances > 1e-9,
    )
    cooking_scaling = np.divide(
        combined_cooking,
        np.maximum(pre_renorm_cooking, 1e-9),
        out=np.zeros_like(combined_cooking),
        where=pre_renorm_cooking > 1e-9,
    )
    base_profile_total = (
        base_load_profile * appliance_scaling
    )
    event_profile_total = appliance_events * appliance_scaling + cooking_events * cooking_scaling
    nonthermal_profile_total = combined_appliances + combined_lighting + combined_cooking
    scaled_event_component_profiles = {}
    for name, values in dict(generated_events["component_output_loads"]).items():
        raw_values = np.clip(np.asarray(values, dtype=float) * event_blend_scale, 0.0, None)
        scaling = cooking_scaling if name == "cooking" else appliance_scaling
        scaled_event_component_profiles[name] = tuple(float(value) for value in np.clip(raw_values * scaling, 0.0, None).tolist())

    updated_load_profiles = TimeSeriesData(
        timestamps=timestamps,
        columns={
            "appliances": tuple(float(value) for value in combined_appliances.tolist()),
            "lighting": tuple(float(value) for value in combined_lighting.tolist()),
            "cooking": tuple(float(value) for value in combined_cooking.tolist()),
            "dhw": tuple(float(value) for value in np.clip(dhw_profile, 0.0, None).tolist()),
            "ev_charging": tuple(float(value) for value in np.clip(ev_charging_profile, 0.0, None).tolist()),
        },
        metadata={
            **dict(load_profiles.metadata),
            "stochastic_household_class": household_class.name,
            "stochastic_household_random_effect_u": float(behaviour.get("household_random_effect_u", 0.0)),
            "stochastic_shared_peak_seed": shared_peak_seed,
            "stochastic_event_substep_seconds": int(generated_events["substep_seconds"]),
            "stochastic_dhw_substep_seconds": int(generated_dhw["substep_seconds"]),
            "stochastic_direct_dhw_profile": True,
            "has_ev_charging_profile": bool(behaviour.get("has_ev", False)),
        },
    )

    diagnostics = {
        "household_class": household_class.name,
        "base_load_multiplier": float(household_class.base_load_multiplier),
        "event_intensity_multiplier": float(household_class.event_intensity_multiplier),
        "occupancy_scaling_factor": float(household_class.occupancy_scaling_factor),
        "peak_sensitivity_factor": float(household_class.peak_sensitivity_factor),
        "household_random_effect_u": float(behaviour.get("household_random_effect_u", 0.0)),
        "event_blend_scale": float(event_blend_scale),
        "event_rate_scale": float(event_rate_scale),
        "dhw_cohort_scale": float(dhw_cohort_scale),
        "shared_peak_seed": int(shared_peak_seed),
        "thermal_parameters": {
            "UA_h_W_per_C": float(UA_h),
            "C_h_J_per_K": float(C_h),
            "thermal_mass_h_Wh_per_C": float(C_h / 3600.0),
            "UA_mean_W_per_C": float(input_data.heat_loss_coefficient_W_per_C),
            "C_mean_J_per_K": float(input_data.C_J_per_K),
        },
        "control_schedule": control_schedule,
        "base_load_profile": {
            "base_level_w": float(base_load_output["base_level_w"]),
            "class_scale": float(base_load_output["class_scale"]),
        },
        "lighting_profile": {
            "class_scale": float(lighting_output["class_scale"]),
            "daylight_factor": tuple(float(value) for value in lighting_output["daylight_factor"]),
            "occupancy_factor": tuple(float(value) for value in lighting_output["occupancy_factor"]),
            "time_weight": tuple(float(value) for value in lighting_output["time_weight"]),
        },
        "daily_peak_driver": {day.isoformat(): float(value) for day, value in daily_peak_driver.items()},
        "base_profile_W": tuple(float(value) for value in np.clip(base_profile_total, 0.0, None).tolist()),
        "event_profile_W": tuple(float(value) for value in np.clip(event_profile_total, 0.0, None).tolist()),
        "nonthermal_profile_W": tuple(float(value) for value in np.clip(nonthermal_profile_total, 0.0, None).tolist()),
        "lighting_profile_W": tuple(float(value) for value in np.clip(combined_lighting, 0.0, None).tolist()),
        "dhw_profile_W": tuple(float(value) for value in np.clip(dhw_profile, 0.0, None).tolist()),
        "ev_charging_profile_W": tuple(float(value) for value in np.clip(ev_charging_profile, 0.0, None).tolist()),
        "event_component_profiles_W": scaled_event_component_profiles,
        "dhw_component_profiles_W": dhw_component_profiles,
        "event_summary": dict(generated_events["event_summary"]),
        "event_log": list(generated_events["event_log"]),
        "dhw_event_summary": dhw_event_summary,
        "dhw_event_log": list(generated_dhw["event_log"]),
        "dhw_occupancy_active": tuple(float(value) for value in generated_dhw["occupancy_active_W"]),
        "dhw_occupied_probability": tuple(float(value) for value in generated_dhw["occupied_probability"]),
        "dhw_expected_occupants": tuple(float(value) for value in generated_dhw["expected_occupants"]),
    }

    updated_source_data = dict(input_data.source_data)
    updated_source_data["load_profiles"] = updated_load_profiles
    updated_metadata = dict(input_data.metadata)
    updated_metadata["control_schedule"] = control_schedule
    updated_metadata["household_thermal_parameters"] = diagnostics["thermal_parameters"]
    updated_metadata["stochastic_household"] = diagnostics
    return replace(
        input_data,
        source_data=updated_source_data,
        heat_loss_coefficient_W_per_C=float(UA_h),
        thermal_mass_Wh_per_C=float(C_h / 3600.0),
        C_J_per_K=float(C_h),
        metadata=updated_metadata,
    ), diagnostics
