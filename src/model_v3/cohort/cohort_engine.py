"""Streaming cohort simulation engine for model_v3."""

from __future__ import annotations

import logging
from time import perf_counter
from typing import Any, Mapping

import numpy as np
import pandas as pd

from model_v3.cohort.household_runner import run_single_household
from model_v3.stochastic.sampler import sample_household_parameters
from model_v3.utils.feature_flags import is_module_enabled, require_module_enabled
from model_v3.validation.core.metrics_variance import compute_diurnal_variance
from model_v3.validation.core.metrics_temporal import compute_diversity_factor

LOGGER = logging.getLogger(__name__)

AGGREGATED_POWER_COLUMNS = (
    "P_el_total_W",
    "P_el_gross_actual_W",
    "P_el_grid_import_W",
    "P_el_grid_export_W",
    "P_el_ev_charging_W",
    "P_pv_generation_W",
    "P_gas_total_W",
    "P_oil_total_W",
    "P_biomass_total_W",
    "P_propane_total_W",
    "P_coal_total_W",
    "P_district_heat_total_W",
    "P_gas_space_heating_W",
    "P_gas_dhw_W",
    "P_oil_space_heating_W",
    "P_oil_dhw_W",
    "P_biomass_space_heating_W",
    "P_biomass_dhw_W",
    "P_propane_space_heating_W",
    "P_propane_dhw_W",
    "P_coal_space_heating_W",
    "P_coal_dhw_W",
    "P_district_heat_space_heating_W",
    "P_district_heat_dhw_W",
    "Q_heating_supplied_W",
    "Q_dhw_demand_W",
    "Q_total_thermal_W",
)


def _update_numeric_ranges(target: dict[str, dict[str, float]], prefix: str, values: Mapping[str, Any]) -> None:
    """Track min/max ranges for numeric sampled parameters."""

    for key, value in values.items():
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            tracker = target.setdefault(f"{prefix}.{key}", {"min": float(value), "max": float(value)})
            tracker["min"] = min(tracker["min"], float(value))
            tracker["max"] = max(tracker["max"], float(value))


def _frame_column_or_zeros(frame: pd.DataFrame, column_name: str) -> np.ndarray:
    """Return one numeric dataframe column or a zero vector."""

    if column_name not in frame.columns:
        return np.zeros(len(frame), dtype=float)
    return pd.to_numeric(frame[column_name], errors="coerce").fillna(0.0).to_numpy(dtype=float)


def _distribution_summary(values: list[float] | np.ndarray) -> dict[str, float]:
    """Return compact descriptive statistics for a numeric cohort vector."""

    array = np.asarray(values, dtype=float)
    if array.size == 0:
        return {
            "count": 0.0,
            "sum": 0.0,
            "mean": 0.0,
            "std": 0.0,
            "min": 0.0,
            "p10": 0.0,
            "p50": 0.0,
            "p90": 0.0,
            "max": 0.0,
        }
    return {
        "count": float(array.size),
        "sum": float(np.sum(array)),
        "mean": float(np.mean(array)),
        "std": float(np.std(array, ddof=0)),
        "min": float(np.min(array)),
        "p10": float(np.percentile(array, 10)),
        "p50": float(np.percentile(array, 50)),
        "p90": float(np.percentile(array, 90)),
        "max": float(np.max(array)),
    }


def _sum_numeric_mapping(values: Mapping[str, Any]) -> float:
    """Sum numeric values from a diagnostics mapping."""

    total = 0.0
    for value in values.values():
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            total += float(value)
    return total


def _calibration_stats_by_end_use(
    diagnostics: list[dict[str, Any]],
    group_name: str,
) -> dict[str, dict[str, float]]:
    """Summarise one annual calibration diagnostics group by end use."""

    grouped: dict[str, list[float]] = {}
    for diagnostic in diagnostics:
        for end_use, value in dict(diagnostic.get(group_name, {})).items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                grouped.setdefault(str(end_use), []).append(float(value))
    return {end_use: _distribution_summary(values) for end_use, values in sorted(grouped.items())}


def _scale_factor_stats_by_end_use(diagnostics: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    """Summarise non-null annual calibration scale factors by end use."""

    grouped: dict[str, list[float]] = {}
    for diagnostic in diagnostics:
        for end_use, value in dict(diagnostic.get("scale_factor_by_end_use", {})).items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                grouped.setdefault(str(end_use), []).append(float(value))
    return {end_use: _distribution_summary(values) for end_use, values in sorted(grouped.items())}


def _fallback_counts_by_end_use(diagnostics: list[dict[str, Any]]) -> dict[str, int]:
    """Count annual calibration fallback use by end use."""

    counts: dict[str, int] = {}
    for diagnostic in diagnostics:
        for end_use, fallback_used in dict(diagnostic.get("fallback_used_by_end_use", {})).items():
            if bool(fallback_used):
                counts[str(end_use)] = counts.get(str(end_use), 0) + 1
            else:
                counts.setdefault(str(end_use), counts.get(str(end_use), 0))
    return dict(sorted(counts.items()))


def run_cohort_simulation(config: Mapping[str, Any]) -> dict[str, Any]:
    """Run a streaming cohort simulation by wrapping the deterministic household model."""

    require_module_enabled(config, "cohort", "run_cohort_simulation")
    cohort_cfg = dict(config.get("cohort", {}))
    simulation_cfg = dict(config.get("simulation", {}))
    requested_households = int(cohort_cfg.get("n_households", 30))
    minimum_households = int(cohort_cfg.get("minimum_households", 30))
    n_households = max(requested_households, minimum_households)
    random_seed = int(cohort_cfg.get("random_seed", 42))
    progress_every = max(int(cohort_cfg.get("progress_every_households", 5) or 5), 1)
    reference_year = simulation_cfg.get("reference_year")

    rng = np.random.default_rng(random_seed)
    peak_tracker: list[float] = []
    household_elapsed_seconds: list[float] = []
    sample_preview: list[dict[str, Any]] = []
    parameter_ranges: dict[str, dict[str, float]] = {}
    technology_counts: dict[str, int] = {}
    dhw_technology_counts: dict[str, int] = {}
    household_class_counts: dict[str, int] = {}
    pv_household_count = 0
    ev_household_count = 0
    occupant_count_tracker: list[float] = []
    occupant_count_counts: dict[str, int] = {}
    household_profiles: list[np.ndarray] = []
    aggregate_power_profiles: dict[str, np.ndarray] = {}
    household_event_profiles: dict[str, list[float]] = {}
    household_total_profiles: dict[str, list[float]] = {}
    household_nonthermal_profiles: dict[str, list[float]] = {}
    household_base_profiles: dict[str, list[float]] = {}
    household_lighting_profiles: dict[str, list[float]] = {}
    household_dhw_profiles: dict[str, list[float]] = {}
    household_occupancy_profiles: dict[str, list[float]] = {}
    household_summaries: list[dict[str, Any]] = []
    household_calibration_diagnostics: list[dict[str, Any]] = []
    annual_energy_kwh_tracker: list[float] = []
    raw_annual_energy_kwh_tracker: list[float] = []
    target_annual_energy_kwh_tracker: list[float] = []
    annual_dhw_thermal_kwh_tracker: list[float] = []
    space_heating_thermal_kwh_tracker: list[float] = []
    carrier_energy_trackers: dict[str, list[float]] = {}
    peak_total_thermal_tracker: list[float] = []
    dhw_peak_tracker: list[float] = []
    ua_tracker: list[float] = []
    c_tracker: list[float] = []
    timestamps: list[pd.Timestamp] | None = None
    timestep_seconds: float | None = None
    cohort_start = perf_counter()

    LOGGER.info(
        "cohort.start requested_households=%s effective_households=%s random_seed=%s progress_every=%s",
        requested_households,
        n_households,
        random_seed,
        progress_every,
    )

    for household_index in range(n_households):
        household_start = perf_counter()
        sampled_params = (
            sample_household_parameters(config=config, rng=rng)
            if is_module_enabled(config, "stochastic")
            else {
                "physical": {},
                "behaviour": {
                    "household_class": "low_flat",
                    "occupants_per_dwelling": 2.0,
                    "schedule_variation_seed": random_seed,
                    "load_variation_seed": random_seed,
                },
                "technology": {"technology_type": "resistive_direct"},
            }
        )
        outputs = run_single_household(base_config=config, sampled_params=sampled_params)

        profile_frame = pd.DataFrame(outputs["profile_frame"]).copy()
        household_profile = _frame_column_or_zeros(profile_frame, "P_el_total_W")
        event_profile = _frame_column_or_zeros(profile_frame, "P_events_W")
        nonthermal_profile = _frame_column_or_zeros(profile_frame, "P_nonthermal_W")
        base_profile = _frame_column_or_zeros(profile_frame, "P_base_W")
        lighting_profile = _frame_column_or_zeros(profile_frame, "P_lighting_household_W")
        dhw_profile = _frame_column_or_zeros(profile_frame, "Q_dhw_demand_W")
        for column_name in AGGREGATED_POWER_COLUMNS:
            column_values = _frame_column_or_zeros(profile_frame, column_name)
            if column_name not in aggregate_power_profiles:
                aggregate_power_profiles[column_name] = np.zeros(len(column_values), dtype=float)
            if len(aggregate_power_profiles[column_name]) == len(column_values):
                aggregate_power_profiles[column_name] += column_values
        if timestamps is None:
            timestamps = [pd.Timestamp(value) for value in profile_frame["timestamp"]]
        if timestep_seconds is None and outputs.get("timestep_seconds") is not None:
            timestep_seconds = float(outputs.get("timestep_seconds", 0.0))
        household_profiles.append(household_profile)
        household_id = f"household_{household_index:03d}"
        household_total_profiles[household_id] = household_profile.tolist()
        household_event_profiles[household_id] = event_profile.tolist()
        household_nonthermal_profiles[household_id] = nonthermal_profile.tolist()
        household_base_profiles[household_id] = base_profile.tolist()
        household_lighting_profiles[household_id] = lighting_profile.tolist()
        household_dhw_profiles[household_id] = dhw_profile.tolist()
        stochastic_household = dict(outputs.get("stochastic_household", {}))
        occupancy_profile = list(stochastic_household.get("dhw_occupied_probability", []))
        if len(occupancy_profile) == len(household_profile):
            household_occupancy_profiles[household_id] = [float(value) for value in occupancy_profile]
        calibrated_annual_energy_kwh = float(outputs.get("annual_energy_kWh", 0.0))
        annual_energy_kwh_tracker.append(calibrated_annual_energy_kwh)
        annual_dhw_thermal_kwh_tracker.append(float(outputs.get("dhw_thermal_kWh", 0.0)))
        space_heating_thermal_kwh_tracker.append(float(outputs.get("space_heating_thermal_kWh", 0.0)))
        annual_energy_by_carrier = dict(outputs.get("annual_energy_by_carrier_kWh", {}))
        for carrier_name, carrier_value in annual_energy_by_carrier.items():
            if isinstance(carrier_value, (int, float)) and not isinstance(carrier_value, bool):
                carrier_energy_trackers.setdefault(str(carrier_name), []).append(float(carrier_value))
        peak_total_thermal_tracker.append(float(outputs.get("peak_total_thermal_W", 0.0)))

        household_peak = float(np.max(household_profile)) if len(household_profile) else 0.0
        dhw_peak = float(np.max(dhw_profile)) if len(dhw_profile) else 0.0
        peak_tracker.append(household_peak)
        dhw_peak_tracker.append(dhw_peak)

        physical = dict(sampled_params.get("physical", {}))
        behaviour = dict(sampled_params.get("behaviour", {}))
        technology = dict(sampled_params.get("technology", {}))
        household_class = str(behaviour.get("household_class", "low_flat"))
        occupants_per_dwelling = float(behaviour.get("occupants_per_dwelling", 2.0))
        technology_type = str(technology.get("technology_type", "unknown"))
        dhw_technology_type = str(technology.get("dhw_technology_type", "unknown"))
        has_pv = bool(technology.get("has_pv", False))
        has_ev = bool(technology.get("has_ev", behaviour.get("has_ev", False)))
        electricity_calibration = dict(outputs.get("household_electricity_calibration", outputs.get("electricity_calibration", {})))
        raw_annual_energy_kwh = _sum_numeric_mapping(dict(electricity_calibration.get("raw_annual_kWh_by_end_use", {})))
        target_annual_energy_kwh = _sum_numeric_mapping(dict(electricity_calibration.get("target_annual_kWh_by_end_use", {})))
        calibrated_from_diagnostics_kwh = _sum_numeric_mapping(dict(electricity_calibration.get("calibrated_annual_kWh_by_end_use", {})))
        if electricity_calibration:
            raw_annual_energy_kwh_tracker.append(raw_annual_energy_kwh)
            target_annual_energy_kwh_tracker.append(target_annual_energy_kwh)
            household_calibration_diagnostics.append(
                {
                    "household_id": household_id,
                    "technology_type": technology_type,
                    "dhw_technology_type": dhw_technology_type,
                    "household_class": household_class,
                    "target_annual_kWh_by_end_use": dict(electricity_calibration.get("target_annual_kWh_by_end_use", {})),
                    "raw_annual_kWh_by_end_use": dict(electricity_calibration.get("raw_annual_kWh_by_end_use", {})),
                    "calibrated_annual_kWh_by_end_use": dict(electricity_calibration.get("calibrated_annual_kWh_by_end_use", {})),
                    "scale_factor_by_end_use": dict(electricity_calibration.get("scale_factor_by_end_use", {})),
                    "fallback_used_by_end_use": dict(electricity_calibration.get("fallback_used_by_end_use", {})),
                    "raw_annual_energy_kWh": raw_annual_energy_kwh,
                    "target_annual_energy_kWh": target_annual_energy_kwh,
                    "calibrated_annual_energy_from_end_uses_kWh": calibrated_from_diagnostics_kwh,
                }
            )
        thermal_parameters = dict(outputs.get("household_thermal_parameters", {}))
        ua_h = float(thermal_parameters.get("UA_h_W_per_C", 0.0))
        c_h = float(thermal_parameters.get("C_h_J_per_K", 0.0))
        ua_tracker.append(ua_h)
        c_tracker.append(c_h)
        household_summary = {
            "household_id": household_id,
            "building_archetype_id": physical.get("building_archetype_id"),
            "dwelling_type": physical.get("dwelling_type"),
            "renovation_state": physical.get("renovation_state"),
            "construction_period_id": physical.get("construction_period_id"),
            "u_value_package_id": physical.get("u_value_package_id"),
            "technology_type": technology_type,
            "dhw_technology_type": dhw_technology_type,
            "household_class": household_class,
            "occupants_per_dwelling": occupants_per_dwelling,
            "household_random_effect_u": float(behaviour.get("household_random_effect_u", 0.0)),
            "UA_h_W_per_C": ua_h,
            "C_h_J_per_K": c_h,
            "has_dryer": bool(behaviour.get("has_dryer", False)),
            "has_pv": has_pv,
            "has_ev": has_ev,
            "technology_probability": float(technology.get("technology_probability", 0.0)),
            "dhw_technology_probability": float(technology.get("dhw_technology_probability", 0.0)),
            "technology_probability_source": str(technology.get("technology_probability_source", "")),
            "dhw_technology_probability_source": str(technology.get("dhw_technology_probability_source", "")),
            "annual_energy_kWh": calibrated_annual_energy_kwh,
            "calibrated_annual_energy_kWh": calibrated_annual_energy_kwh,
            "raw_annual_energy_kWh": raw_annual_energy_kwh if electricity_calibration else None,
            "target_annual_energy_kWh": target_annual_energy_kwh if electricity_calibration else None,
            "calibration_fallback_count": int(sum(bool(value) for value in dict(electricity_calibration.get("fallback_used_by_end_use", {})).values())),
            "space_heating_thermal_kWh": float(outputs.get("space_heating_thermal_kWh", 0.0)),
            "annual_dhw_thermal_kWh": float(outputs.get("dhw_thermal_kWh", 0.0)),
            "annual_energy_by_carrier_kWh": annual_energy_by_carrier,
            "peak_demand_W": household_peak,
            "peak_total_thermal_W": float(outputs.get("peak_total_thermal_W", 0.0)),
            "peak_dhw_load_W": dhw_peak,
            "event_peak_W": float(np.max(event_profile)) if len(event_profile) else 0.0,
            "total_event_count": int(dict(outputs.get("household_event_summary", {})).get("total_event_count", 0)),
            "total_dhw_event_count": int(dict(outputs.get("household_dhw_summary", {})).get("total_event_count", 0)),
        }
        household_summaries.append(household_summary)
        _update_numeric_ranges(parameter_ranges, "physical", physical)
        _update_numeric_ranges(parameter_ranges, "behaviour", behaviour)
        _update_numeric_ranges(parameter_ranges, "technology", technology)
        technology_counts[technology_type] = technology_counts.get(technology_type, 0) + 1
        dhw_technology_counts[dhw_technology_type] = dhw_technology_counts.get(dhw_technology_type, 0) + 1
        pv_household_count += int(has_pv)
        ev_household_count += int(has_ev)
        household_class_counts[household_class] = household_class_counts.get(household_class, 0) + 1
        occupant_count_tracker.append(occupants_per_dwelling)
        occupant_count_key = str(int(round(occupants_per_dwelling)))
        occupant_count_counts[occupant_count_key] = occupant_count_counts.get(occupant_count_key, 0) + 1

        household_elapsed_seconds.append(perf_counter() - household_start)
        completed_households = household_index + 1
        if (
            completed_households == 1
            or completed_households % progress_every == 0
            or completed_households == n_households
        ):
            elapsed_seconds = perf_counter() - cohort_start
            mean_household_seconds = float(np.mean(household_elapsed_seconds))
            estimated_remaining_seconds = max(n_households - completed_households, 0) * mean_household_seconds
            LOGGER.info(
                "cohort.progress household=%s/%s elapsed_s=%.1f mean_household_s=%.1f eta_s=%.1f current_peak_W=%.1f current_annual_kWh=%.1f",
                completed_households,
                n_households,
                elapsed_seconds,
                mean_household_seconds,
                estimated_remaining_seconds,
                household_peak,
                float(outputs.get("annual_energy_kWh", 0.0)),
            )

        if household_index < 3:
            sample_preview.append(
                {
                    "household_index": household_index,
                    "technology_type": technology_type,
                    "dhw_technology_type": dhw_technology_type,
                    "has_pv": has_pv,
                    "has_ev": has_ev,
                    "household_class": household_class,
                    "annual_energy_kWh": calibrated_annual_energy_kwh,
                    "raw_annual_energy_kWh": raw_annual_energy_kwh if electricity_calibration else None,
                    "peak_demand_W": household_peak,
                    "peak_dhw_load_W": dhw_peak,
                    "event_peak_W": float(np.max(event_profile)) if len(event_profile) else 0.0,
                    "occupancy_time_shift_hours": float(behaviour.get("occupancy_time_shift_hours", 0.0)),
                }
            )

    if n_households <= 0:
        raise ValueError("cohort.n_households must be positive.")

    profile_matrix = np.vstack(household_profiles) if household_profiles else np.zeros((1, 1), dtype=float)
    dhw_matrix = np.vstack([np.asarray(values, dtype=float) for values in household_dhw_profiles.values()]) if household_dhw_profiles else np.zeros((1, 1), dtype=float)
    aggregate_profile = aggregate_power_profiles.get("P_el_total_W", profile_matrix.sum(axis=0))
    aggregate_dhw_profile = dhw_matrix.sum(axis=0)
    per_household_profile = aggregate_profile / max(n_households, 1)
    per_household_dhw_profile = aggregate_dhw_profile / max(n_households, 1)
    p10_profile_series = np.percentile(profile_matrix, 10, axis=0)
    p50_profile_series = np.percentile(profile_matrix, 50, axis=0)
    p90_profile_series = np.percentile(profile_matrix, 90, axis=0)
    peak_array = np.asarray(peak_tracker, dtype=float)
    thermal_peak_array = np.asarray(peak_total_thermal_tracker, dtype=float)
    dhw_peak_array = np.asarray(dhw_peak_tracker, dtype=float)
    ua_array = np.asarray(ua_tracker, dtype=float)
    c_array = np.asarray(c_tracker, dtype=float)
    space_heating_array = np.asarray(space_heating_thermal_kwh_tracker, dtype=float)
    annual_energy_array = np.asarray(annual_energy_kwh_tracker, dtype=float)
    annual_dhw_thermal_array = np.asarray(annual_dhw_thermal_kwh_tracker, dtype=float)
    aggregated_peak = float(np.max(aggregate_profile)) if len(aggregate_profile) else 0.0
    aggregated_dhw_peak = float(np.max(aggregate_dhw_profile)) if len(aggregate_dhw_profile) else 0.0
    diversity_factor = compute_diversity_factor(profile_matrix, aggregate_profile)
    mean_profile = float(np.mean(per_household_profile))
    std_profile = float(np.std(per_household_profile, ddof=0))
    P10_profile = float(np.mean(p10_profile_series))
    P50_profile = float(np.mean(p50_profile_series))
    P90_profile = float(np.mean(p90_profile_series))
    timestamp_index = pd.to_datetime(timestamps or [pd.Timestamp("2023-01-01")])
    variance_by_hour = compute_diurnal_variance(pd.Series(per_household_profile, index=timestamp_index))
    profile_payload = {
        "timestamp": timestamp_index,
        "aggregate_profile_W": aggregate_profile,
        "per_household_profile_W": per_household_profile,
        "aggregate_dhw_W": aggregate_dhw_profile,
        "per_household_dhw_W": per_household_dhw_profile,
        "P10_W": p10_profile_series,
        "P50_W": p50_profile_series,
        "P90_W": p90_profile_series,
    }
    for column_name, values in sorted(aggregate_power_profiles.items()):
        if len(values) == len(timestamp_index):
            profile_payload[column_name] = values
    profile_frame = pd.DataFrame(profile_payload)
    annual_energy_by_carrier_aggregate = {
        carrier_name: float(np.sum(values))
        for carrier_name, values in sorted(carrier_energy_trackers.items())
    }
    annual_energy_summary = {
        "aggregate_calibrated_electricity_kWh": float(np.sum(annual_energy_array)),
        "per_household_calibrated_electricity_kWh": _distribution_summary(annual_energy_array),
        "per_household_raw_pre_calibration_electricity_kWh": _distribution_summary(raw_annual_energy_kwh_tracker),
        "per_household_target_electricity_kWh": _distribution_summary(target_annual_energy_kwh_tracker),
        "per_household_dhw_thermal_kWh": _distribution_summary(annual_dhw_thermal_array),
        "per_household_space_heating_thermal_kWh": _distribution_summary(space_heating_array),
        "aggregate_dhw_thermal_kWh": float(np.sum(annual_dhw_thermal_array)),
        "aggregate_space_heating_thermal_kWh": float(np.sum(space_heating_array)),
        "carrier_energy_kWh": {
            carrier_name: {
                "aggregate": float(np.sum(values)),
                "per_household": _distribution_summary(values),
            }
            for carrier_name, values in sorted(carrier_energy_trackers.items())
        },
        "note": (
            "Calibrated household electricity is annual-baseline aligned by the annual runner; "
            "raw/pre-calibration diagnostics show the uncalibrated annual totals when available."
        ),
    }
    annual_calibration_summary = {
        "available": bool(household_calibration_diagnostics),
        "household_count_with_diagnostics": int(len(household_calibration_diagnostics)),
        "target_annual_kWh_by_end_use": _calibration_stats_by_end_use(household_calibration_diagnostics, "target_annual_kWh_by_end_use"),
        "raw_annual_kWh_by_end_use": _calibration_stats_by_end_use(household_calibration_diagnostics, "raw_annual_kWh_by_end_use"),
        "calibrated_annual_kWh_by_end_use": _calibration_stats_by_end_use(household_calibration_diagnostics, "calibrated_annual_kWh_by_end_use"),
        "scale_factor_by_end_use": _scale_factor_stats_by_end_use(household_calibration_diagnostics),
        "fallback_counts_by_end_use": _fallback_counts_by_end_use(household_calibration_diagnostics),
    }
    run_metadata = {
        "n_households": int(n_households),
        "requested_households": int(requested_households),
        "minimum_households": int(minimum_households),
        "random_seed": int(random_seed),
        "reference_year": reference_year,
        "n_steps": int(len(per_household_profile)),
        "timestep_seconds": None if timestep_seconds is None else float(timestep_seconds),
        "profile_representation": "per_household",
    }
    sampled_population = {
        "technology_counts": dict(sorted(technology_counts.items())),
        "dhw_technology_counts": dict(sorted(dhw_technology_counts.items())),
        "pv_household_count": int(pv_household_count),
        "ev_household_count": int(ev_household_count),
        "pv_household_share": float(pv_household_count / max(n_households, 1)),
        "ev_household_share": float(ev_household_count / max(n_households, 1)),
        "household_class_counts": dict(sorted(household_class_counts.items())),
        "occupant_count_counts": dict(sorted(occupant_count_counts.items(), key=lambda item: int(item[0]))),
        "occupants_per_dwelling": _distribution_summary(occupant_count_tracker),
        "sample_parameter_ranges": parameter_ranges,
    }

    results = {
        "mean_profile": mean_profile,
        "std_profile": std_profile,
        "P10_profile": P10_profile,
        "P50_profile": P50_profile,
        "P90_profile": P90_profile,
        "diversity_factor": diversity_factor,
        "peak_distribution": {
            "mean_peak_W": float(np.mean(peak_array)),
            "std_peak_W": float(np.std(peak_array)),
            "max_peak_W": float(np.max(peak_array)),
            "min_peak_W": float(np.min(peak_array)),
            "p10_peak_W": float(np.percentile(peak_array, 10)),
            "p50_peak_W": float(np.percentile(peak_array, 50)),
            "p90_peak_W": float(np.percentile(peak_array, 90)),
        },
        "n_households": n_households,
        "household_count": n_households,
        "requested_households": requested_households,
        "minimum_households": minimum_households,
        "random_seed": random_seed,
        "reference_year": reference_year,
        "aggregated_peak_W": aggregated_peak,
        "aggregated_dhw_peak_W": aggregated_dhw_peak,
        "aggregate_profile": aggregate_profile.tolist(),
        "aggregate_dhw_profile": aggregate_dhw_profile.tolist(),
        "per_household_profile": per_household_profile.tolist(),
        "per_household_dhw_profile": per_household_dhw_profile.tolist(),
        "P10_profile_series": p10_profile_series.tolist(),
        "P50_profile_series": p50_profile_series.tolist(),
        "P90_profile_series": p90_profile_series.tolist(),
        "timestamps": [timestamp.isoformat() for timestamp in timestamp_index],
        "profile_frame": profile_frame,
        "n_steps": int(len(per_household_profile)),
        "profile_representation": "per_household",
        "annual_energy_kWh": float(np.sum(annual_energy_array)),
        "annual_energy_by_carrier_kWh": annual_energy_by_carrier_aggregate,
        "annual_grid_import_kWh": annual_energy_by_carrier_aggregate.get("electricity_grid_import", 0.0),
        "annual_grid_export_kWh": annual_energy_by_carrier_aggregate.get("electricity_grid_export", 0.0),
        "annual_pv_generation_kWh": annual_energy_by_carrier_aggregate.get("pv_generation", 0.0),
        "annual_ev_charging_kWh": annual_energy_by_carrier_aggregate.get("ev_charging", 0.0),
        "space_heating_thermal_kWh": float(np.sum(space_heating_array)),
        "dhw_thermal_kWh": float(np.sum(annual_dhw_thermal_array)),
        "mean_peak_demand_W": float(np.mean(peak_array)),
        "annual_energy_kWh_mean": float(np.mean(annual_energy_array)),
        "annual_energy_kWh_std": float(np.std(annual_energy_array, ddof=0)),
        "annual_dhw_thermal_kWh_mean": float(np.mean(annual_dhw_thermal_array)),
        "annual_dhw_thermal_kWh_std": float(np.std(annual_dhw_thermal_array, ddof=0)),
        "run_metadata": run_metadata,
        "sampled_population": sampled_population,
        "annual_energy_summary": annual_energy_summary,
        "annual_calibration_summary": annual_calibration_summary,
        "thermal_parameter_distribution": {
            "UA_h_W_per_C": {
                "mean": float(np.mean(ua_array)),
                "std": float(np.std(ua_array, ddof=0)),
                "min": float(np.min(ua_array)),
                "p10": float(np.percentile(ua_array, 10)),
                "p50": float(np.percentile(ua_array, 50)),
                "p90": float(np.percentile(ua_array, 90)),
                "max": float(np.max(ua_array)),
            },
            "C_h_J_per_K": {
                "mean": float(np.mean(c_array)),
                "std": float(np.std(c_array, ddof=0)),
                "min": float(np.min(c_array)),
                "p10": float(np.percentile(c_array, 10)),
                "p50": float(np.percentile(c_array, 50)),
                "p90": float(np.percentile(c_array, 90)),
                "max": float(np.max(c_array)),
            },
        },
        "thermal_demand_spread": {
            "space_heating_thermal_kWh_mean": float(np.mean(space_heating_array)),
            "space_heating_thermal_kWh_std": float(np.std(space_heating_array, ddof=0)),
            "space_heating_thermal_kWh_p10": float(np.percentile(space_heating_array, 10)),
            "space_heating_thermal_kWh_p50": float(np.percentile(space_heating_array, 50)),
            "space_heating_thermal_kWh_p90": float(np.percentile(space_heating_array, 90)),
            "peak_total_thermal_W_mean": float(np.mean(thermal_peak_array)),
            "peak_total_thermal_W_std": float(np.std(thermal_peak_array, ddof=0)),
            "peak_total_thermal_W_p10": float(np.percentile(thermal_peak_array, 10)),
            "peak_total_thermal_W_p50": float(np.percentile(thermal_peak_array, 50)),
            "peak_total_thermal_W_p90": float(np.percentile(thermal_peak_array, 90)),
        },
        "carrier_energy_summary": {
            carrier_name: {
                "aggregate_kWh": float(np.sum(values)),
                "per_household_kWh": _distribution_summary(values),
            }
            for carrier_name, values in sorted(carrier_energy_trackers.items())
        },
        "variance_by_hour": {str(int(hour)): float(value) for hour, value in variance_by_hour.items()},
        "pipeline_timings_seconds": {
            "mean_household_runtime": float(np.mean(household_elapsed_seconds)),
            "max_household_runtime": float(np.max(household_elapsed_seconds)),
        },
        "sample_preview": sample_preview,
        "sample_parameter_ranges": parameter_ranges,
        "technology_counts": technology_counts,
        "household_class_counts": household_class_counts,
        "household_profiles": household_total_profiles,
        "household_event_profiles": household_event_profiles,
        "household_nonthermal_profiles": household_nonthermal_profiles,
        "household_base_profiles": household_base_profiles,
        "household_lighting_profiles": household_lighting_profiles,
        "household_dhw_profiles": household_dhw_profiles,
        "household_occupancy_profiles": household_occupancy_profiles,
        "household_summaries": household_summaries,
        "household_calibration_diagnostics": household_calibration_diagnostics,
        "peak_dhw_distribution": {
            "mean_peak_W": float(np.mean(dhw_peak_array)),
            "std_peak_W": float(np.std(dhw_peak_array)),
            "max_peak_W": float(np.max(dhw_peak_array)),
            "min_peak_W": float(np.min(dhw_peak_array)),
            "p10_peak_W": float(np.percentile(dhw_peak_array, 10)),
            "p50_peak_W": float(np.percentile(dhw_peak_array, 50)),
            "p90_peak_W": float(np.percentile(dhw_peak_array, 90)),
        },
    }
    LOGGER.info(
        "cohort.end n_households=%s diversity_factor=%.3f mean_peak_W=%.3f variance_hour_18=%.3f",
        n_households,
        diversity_factor,
        results["mean_peak_demand_W"],
        float(variance_by_hour.get(18, 0.0)),
    )
    LOGGER.info("cohort.variance_by_hour %s", {int(hour): round(float(value), 3) for hour, value in variance_by_hour.items()})
    LOGGER.info("cohort.peak_distribution %s", results["peak_distribution"])
    return results
