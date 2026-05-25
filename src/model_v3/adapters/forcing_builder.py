"""PreparedForcing builder for the strict layered model_v3 pipeline."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any, Mapping

import numpy as np
import pandas as pd

from model_v3.adapters.load_mapping import map_load_profiles
from model_v3.baseline import annual_average_power_w, normalized_modelled_electricity_split, target_electricity_kwh
from model_v3.control.thermostat import resolve_time_of_day_setpoint
from model_v3.interfaces import InputDataset, PreparedForcing, TimeSeriesData
from model_v3.systems.distributed_energy import (
    annual_ev_home_charging_kwh,
    ev_charging_power_for_timestamp,
    fallback_pv_average_power_w,
    pv_generation_from_irradiance,
    weighted_irradiance,
)
from model_v3.utils.energy import integrate_power_series_kwh

LOGGER = logging.getLogger(__name__)


def _normalise_timestamp(value: str) -> pd.Timestamp:
    """Return a timezone-aware Brussels timestamp."""

    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        return timestamp.tz_localize("Europe/Brussels")
    return timestamp.tz_convert("Europe/Brussels")


def _schedule_timestamp(target_timestamp: pd.Timestamp, metadata: Mapping[str, Any]) -> pd.Timestamp:
    """Return the timestamp used for calendar-dependent occupancy schedules."""

    simulation_cfg = dict(metadata.get("simulation", {}))
    reference_year = simulation_cfg.get("schedule_reference_year")
    if reference_year in {None, ""}:
        return target_timestamp

    year = int(reference_year)
    try:
        return target_timestamp.replace(year=year)
    except ValueError:
        if int(target_timestamp.month) == 2 and int(target_timestamp.day) == 29:
            return target_timestamp.replace(year=year, day=28)
        raise


def _value_at(dataset: TimeSeriesData, column_name: str, index: int = 0, default: float = 0.0) -> float:
    """Read a scalar value from a source dataset with safe fallback."""

    values = dataset.columns.get(column_name)
    if not values or index >= len(values):
        return float(default)

    value = values[index]
    return float(default if value is None else value)


def _timestamp_lookup_cache(dataset: TimeSeriesData) -> dict[str, Any]:
    """Build or reuse a timestamp lookup cache for fast repeated alignment."""

    metadata = dataset.metadata
    cached = metadata.get("_timestamp_lookup_cache")
    if cached is not None:
        return cached

    normalized = [pd.Timestamp(timestamp) for timestamp in dataset.timestamps]
    exact_lookup: dict[pd.Timestamp, int] = {}
    month_day_hour_lookup: dict[tuple[int, int, int], list[tuple[int, int]]] = {}
    hour_lookup: dict[int, list[tuple[int, int]]] = {}
    for index, current in enumerate(normalized):
        exact_lookup[current] = index
        month_day_hour_lookup.setdefault((current.month, current.day, current.hour), []).append((current.minute, index))
        hour_lookup.setdefault(current.hour, []).append((current.minute, index))

    cached = {
        "exact_lookup": exact_lookup,
        "month_day_hour_lookup": month_day_hour_lookup,
        "hour_lookup": hour_lookup,
    }
    metadata["_timestamp_lookup_cache"] = cached
    return cached


def _index_for_timestamp(dataset: TimeSeriesData, target_timestamp: pd.Timestamp) -> int:
    """Resolve a representative source index for the target timestamp."""

    if not dataset.timestamps:
        return 0

    cache = _timestamp_lookup_cache(dataset)
    exact_lookup = cache["exact_lookup"]
    month_day_hour_lookup = cache["month_day_hour_lookup"]
    hour_lookup = cache["hour_lookup"]

    if target_timestamp in exact_lookup:
        return int(exact_lookup[target_timestamp])

    same_day_hour = month_day_hour_lookup.get(
        (target_timestamp.month, target_timestamp.day, target_timestamp.hour),
        [],
    )
    if same_day_hour:
        return min(same_day_hour, key=lambda item: abs(item[0] - target_timestamp.minute))[1]

    same_hour_any_date = hour_lookup.get(target_timestamp.hour, [])
    if same_hour_any_date:
        return min(same_hour_any_date, key=lambda item: abs(item[0] - target_timestamp.minute))[1]
    return 0


def _merge_harmonised_sources(
    sources: dict[str, TimeSeriesData],
    max_preview_rows: int = 24,
) -> dict[str, list[dict[str, float | str]]]:
    """Merge a small preview of harmonised sources by explicit timestamp alignment."""

    source_row_maps: dict[str, dict[object, dict[str, float]]] = {}
    candidate_timestamps = set()
    for source_name, dataset in sources.items():
        row_map: dict[object, dict[str, float]] = {}
        for row_index, timestamp in enumerate(dataset.timestamps[:max_preview_rows]):
            row_map[timestamp] = {
                column_name: float(0.0 if values[row_index] is None else values[row_index])
                for column_name, values in dataset.columns.items()
            }
            candidate_timestamps.add(timestamp)
        source_row_maps[source_name] = row_map

    all_timestamps = sorted(candidate_timestamps)
    merged_rows: list[dict[str, float | str]] = []

    for timestamp in all_timestamps:
        row: dict[str, float | str] = {"timestamp": timestamp.isoformat()}
        for source_name in sources:
            source_values = source_row_maps[source_name].get(timestamp)
            if source_values is None:
                continue
            for column_name, value in source_values.items():
                row[f"{source_name}.{column_name}"] = value
        merged_rows.append(row)

    return {"rows": merged_rows}


def _time_to_minutes(time_string: str) -> int:
    """Convert an ``HH:MM`` string into minutes after midnight."""

    hour, minute = map(int, time_string.split(":"))
    return 60 * hour + minute


def _minutes_to_slot(minutes_after_midnight: int, dt_minutes: int) -> int:
    """Convert minutes after midnight to a discrete occupancy slot index."""

    return minutes_after_midnight // dt_minutes


def _iter_slots_in_window(start_min: int, end_min: int, n_slots: int, dt_minutes: int):
    """Yield slot indices for a possibly wrap-around time window."""

    start_slot = _minutes_to_slot(start_min, dt_minutes)
    end_slot = _minutes_to_slot(end_min, dt_minutes)
    if start_min == end_min:
        return
    if start_min < end_min:
        for slot in range(start_slot, end_slot):
            yield slot
    else:
        for slot in range(start_slot, n_slots):
            yield slot
        for slot in range(0, end_slot):
            yield slot


def _build_occupancy_profiles(spec: dict[str, Any]) -> dict[str, np.ndarray]:
    """Convert the occupancy YAML rules into weekday and weekend state probabilities."""

    cached = spec.get("_compiled_profiles")
    if cached is not None:
        return cached

    states = list(spec["states"])
    dt_minutes = int(spec["dt_minutes"])
    n_slots = int(24 * 60 / dt_minutes)
    n_states = len(states)
    state_index = {state: idx for idx, state in enumerate(states)}

    fallback_weights = dict(spec["fallback_weights"])
    vectors: dict[str, np.ndarray] = {}
    for day_type in ("weekday", "weekend"):
        raw_weights = np.array([fallback_weights[day_type].get(state, 0.0) for state in states], dtype=float)
        vectors[day_type] = raw_weights / raw_weights.sum()

    profiles: dict[str, np.ndarray] = {}
    for day_type in ("weekday", "weekend"):
        fixed = np.zeros((n_slots, n_states), dtype=float)
        for rule in spec.get("rules", {}).get(day_type, []):
            start_min = _time_to_minutes(rule["start"])
            end_min = _time_to_minutes(rule["end"])
            for slot in _iter_slots_in_window(start_min, end_min, n_slots, dt_minutes) or []:
                fixed[slot, state_index[rule["state"]]] += float(rule["p"])
        profile = np.zeros_like(fixed)
        for slot in range(n_slots):
            remainder = 1.0 - fixed[slot].sum()
            profile[slot] = fixed[slot] + remainder * vectors[day_type]
            profile[slot] /= profile[slot].sum()
        profiles[day_type] = profile
    spec["_compiled_profiles"] = profiles
    return profiles


def _mapped_load_profiles(dataset: TimeSeriesData) -> TimeSeriesData:
    """Return a cached mapped load-profile dataset."""

    cached = dataset.metadata.get("_mapped_load_profiles")
    if cached is not None:
        return cached
    mapped = map_load_profiles(dataset)
    dataset.metadata["_mapped_load_profiles"] = mapped
    return mapped


def _annual_profile_energy_kwh(dataset: TimeSeriesData, column_name: str) -> float:
    """Return the annualized energy of a dataset column."""

    cache = dataset.metadata.setdefault("_annual_energy_kwh_cache", {})
    cache_key = (column_name, tuple(dataset.timestamps))
    if cache_key in cache:
        return float(cache[cache_key])
    series = dataset.columns.get(column_name, ())
    energy_kwh = integrate_power_series_kwh(series, timestamps=dataset.timestamps)
    cache[cache_key] = float(energy_kwh)
    return float(energy_kwh)


def _scaled_profile_value(
    dataset: TimeSeriesData,
    column_name: str,
    index: int,
    target_annual_kwh: float,
) -> float:
    """Scale a profile shape to a literature annual target with constant fallback."""

    target_annual_kwh = max(float(target_annual_kwh), 0.0)
    current_value = _value_at(dataset, column_name, index=index, default=0.0)
    current_annual_kwh = _annual_profile_energy_kwh(dataset, column_name)
    if current_annual_kwh > 1e-9:
        return current_value * target_annual_kwh / current_annual_kwh
    return annual_average_power_w(target_annual_kwh)


def _occupancy_snapshot(
    target_timestamp: pd.Timestamp,
    occupancy_spec: dict[str, Any],
    occupants_per_dwelling: float,
    occupant_gains: dict[str, float],
    schedule_variation_seed: int = 0,
    occupancy_time_shift_hours: float = 0.0,
    transition_variability_scale: float = 1.0,
    state_duration_scale: float = 1.0,
    occupancy_state_biases: Mapping[str, float] | None = None,
) -> dict[str, float | str]:
    """Compute the occupancy and schedule state at one explicit target timestamp."""

    if not occupancy_spec:
        return {
            "schedule_state": "away",
            "occupied_probability": 0.0,
            "expected_occupants": 0.0,
            "Q_occ_W": 0.0,
            "prob_away": 1.0,
            "prob_awake": 0.0,
            "prob_sleep": 0.0,
        }

    dt_minutes = int(occupancy_spec["dt_minutes"])
    profiles = _build_occupancy_profiles(occupancy_spec)
    shift_minutes = int(round(float(occupancy_time_shift_hours) * 60.0))
    seed_offset_minutes = int(schedule_variation_seed % max(dt_minutes, 1))
    shifted_timestamp = target_timestamp + timedelta(minutes=shift_minutes + seed_offset_minutes)
    shifted_minutes = (shifted_timestamp.hour * 60 + shifted_timestamp.minute) % (24 * 60)
    effective_minutes = shifted_minutes / max(float(state_duration_scale), 1e-6)
    slot_index = int(effective_minutes // dt_minutes) % max(int(24 * 60 / dt_minutes), 1)
    is_weekend = shifted_timestamp.weekday() >= 5
    day_type = "weekend" if is_weekend else "weekday"
    states = list(occupancy_spec["states"])
    vector = np.asarray(profiles[day_type][slot_index], dtype=float)
    if occupancy_state_biases:
        biases = np.asarray([max(float(occupancy_state_biases.get(state, 1.0)), 1e-6) for state in states], dtype=float)
        vector = np.power(np.maximum(vector * biases, 1e-6), max(float(transition_variability_scale), 1e-6))
        vector = vector / max(vector.sum(), 1e-9)
    probabilities = {state: float(vector[idx]) for idx, state in enumerate(states)}
    dominant_state = max(probabilities.items(), key=lambda item: item[1])[0]
    schedule_state = {"awake": "occupied_day", "sleep": "sleeping", "away": "away"}.get(dominant_state, "away")
    occupied_probability = max(0.0, 1.0 - probabilities.get("away", 0.0))
    expected_occupants = float(occupants_per_dwelling) * occupied_probability
    q_occ = float(occupants_per_dwelling) * sum(
        probabilities.get(state, 0.0) * float(occupant_gains.get(state, 0.0))
        for state in ("away", "awake", "sleep")
    )
    return {
        "schedule_state": schedule_state,
        "occupied_probability": occupied_probability,
        "expected_occupants": expected_occupants,
        "Q_occ_W": q_occ,
        "prob_away": probabilities.get("away", 0.0),
        "prob_awake": probabilities.get("awake", 0.0),
        "prob_sleep": probabilities.get("sleep", 0.0),
    }


def _solar_snapshot(
    solar_dataset: TimeSeriesData,
    target_timestamp: pd.Timestamp,
    floor_area_m2: float,
    glazing_ratio: float,
    frame_fraction: float,
    g_value: float,
    incidence_factor: float,
    dirt_factor: float,
    shading_factor: float,
    orientation_shares: Mapping[str, float],
) -> dict[str, float]:
    """Compute orientation-resolved solar gains at one target timestep."""

    index = _index_for_timestamp(solar_dataset, target_timestamp)
    generic_irradiance = None
    for column_name in ("I_global_W_m2", "I_solar_W_m2"):
        if column_name in solar_dataset.columns:
            generic_irradiance = _value_at(solar_dataset, column_name, index=index, default=0.0)
            break
    if generic_irradiance is not None:
        total_glazing_area_m2 = float(floor_area_m2) * float(glazing_ratio) * float(frame_fraction)
        q_solar_total = (
            float(incidence_factor)
            * float(dirt_factor)
            * float(g_value)
            * float(shading_factor)
            * total_glazing_area_m2
            * max(float(generic_irradiance), 0.0)
        )
        return {
            "I_solar_north_W_per_m2": max(float(generic_irradiance), 0.0),
            "I_solar_east_W_per_m2": max(float(generic_irradiance), 0.0),
            "I_solar_south_W_per_m2": max(float(generic_irradiance), 0.0),
            "I_solar_west_W_per_m2": max(float(generic_irradiance), 0.0),
            "Q_solar_gains_W": max(q_solar_total, 0.0),
        }

    if "Q_solar_gains_W" in solar_dataset.columns:
        q_solar = _value_at(solar_dataset, "Q_solar_gains_W", index=index, default=0.0)
        return {
            "I_solar_north_W_per_m2": 0.0,
            "I_solar_east_W_per_m2": 0.0,
            "I_solar_south_W_per_m2": 0.0,
            "I_solar_west_W_per_m2": 0.0,
            "Q_solar_gains_W": q_solar,
        }

    total_glazing_area_m2 = float(floor_area_m2) * float(glazing_ratio) * float(frame_fraction)
    q_solar_total = 0.0
    resolved: dict[str, float] = {}
    for orientation, share in orientation_shares.items():
        irradiance_column = f"I_{orientation}"
        irradiance = _value_at(solar_dataset, irradiance_column, index=index, default=0.0)
        resolved[f"I_solar_{orientation}_W_per_m2"] = irradiance
        glazing_area = total_glazing_area_m2 * float(share)
        q_solar_total += (
            float(incidence_factor)
            * float(dirt_factor)
            * float(g_value)
            * float(shading_factor)
            * glazing_area
            * irradiance
        )
    resolved["Q_solar_gains_W"] = max(q_solar_total, 0.0)
    return resolved


def _pv_generation_snapshot(
    *,
    solar_snapshot: Mapping[str, float],
    der_cfg: Mapping[str, Any],
    cohort_cfg: Mapping[str, Any],
) -> float:
    """Compute rooftop PV generation for this household and timestep."""

    pv_cfg = dict(der_cfg.get("pv", {}))
    has_pv = bool(pv_cfg.get("enabled", False) or cohort_cfg.get("has_pv", False))
    if not has_pv:
        return 0.0

    has_irradiance_columns = any(
        key.startswith("I_solar_") and key.endswith("_W_per_m2")
        for key in solar_snapshot
    )
    irradiance = weighted_irradiance(solar_snapshot, pv_cfg)
    if has_irradiance_columns:
        return pv_generation_from_irradiance(irradiance, pv_cfg, has_pv=True)
    return fallback_pv_average_power_w(pv_cfg, has_pv=True)


def _ev_charging_snapshot(
    *,
    target_timestamp: pd.Timestamp,
    mapped_loads: TimeSeriesData,
    load_index: int,
    mobility_cfg: Mapping[str, Any],
    cohort_cfg: Mapping[str, Any],
    target_resolution_seconds: int,
) -> float:
    """Resolve EV charging from a profile when present, otherwise from scenario settings."""

    mapped_ev = _value_at(mapped_loads, "P_ev_charging_W", index=load_index, default=0.0)
    if mapped_ev > 0.0 or bool(mapped_loads.metadata.get("has_ev_charging_profile", False)):
        return max(mapped_ev, 0.0)

    ev_cfg = dict(mobility_cfg.get("ev", {}))
    has_ev = bool(ev_cfg.get("enabled", False) or cohort_cfg.get("has_ev", False))
    if has_ev and int(target_resolution_seconds) >= 24 * 3600:
        return annual_ev_home_charging_kwh(ev_cfg) * 1000.0 / 8760.0
    return ev_charging_power_for_timestamp(target_timestamp, ev_cfg, has_ev=has_ev)


def build_prepared_forcing(input_dataset: InputDataset, include_preview: bool = True) -> PreparedForcing:
    """Merge harmonised sources into a single consistent forcing bundle."""

    LOGGER.info(
        "adapter.start source_count=%s target_resolution_seconds=%s",
        len(input_dataset.source_data),
        input_dataset.target_resolution_seconds,
    )

    mapped_loads = _mapped_load_profiles(input_dataset.source_data["load_profiles"])
    aligned_sources = {
        "weather": input_dataset.source_data["weather"],
        "load_profiles": mapped_loads,
        "internal_gains": input_dataset.source_data["internal_gains"],
        "solar": input_dataset.source_data["solar"],
    }
    merged = _merge_harmonised_sources(aligned_sources) if include_preview else {"rows": []}

    target_timestamp = _normalise_timestamp(input_dataset.timestamp)
    weather = aligned_sources["weather"]
    internal_gains = aligned_sources["internal_gains"]
    solar = aligned_sources["solar"]
    weather_index = _index_for_timestamp(weather, target_timestamp)
    load_index = _index_for_timestamp(mapped_loads, target_timestamp)
    internal_index = _index_for_timestamp(internal_gains, target_timestamp)

    occupancy_spec = dict(input_dataset.metadata.get("occupancy_spec", {}))
    baseline_cfg = dict(input_dataset.metadata.get("baseline", {}))
    electricity_split_cfg = dict(input_dataset.metadata.get("electricity_split", {}))
    normalized_split = normalized_modelled_electricity_split(electricity_split_cfg)
    cohort_cfg = dict(input_dataset.metadata.get("cohort", {}))
    der_cfg = dict(input_dataset.metadata.get("der", {}))
    mobility_cfg = dict(input_dataset.metadata.get("mobility", {}))
    schedule_variation_seed = int(cohort_cfg.get("schedule_variation_seed", 0))
    schedule_timestamp = _schedule_timestamp(target_timestamp, input_dataset.metadata)
    occupancy = _occupancy_snapshot(
        target_timestamp=schedule_timestamp,
        occupancy_spec=occupancy_spec,
        occupants_per_dwelling=input_dataset.occupants_per_dwelling,
        occupant_gains={
            "away": input_dataset.occupant_gain_away_W_per_person,
            "awake": input_dataset.occupant_gain_awake_W_per_person,
            "sleep": input_dataset.occupant_gain_sleep_W_per_person,
        },
        schedule_variation_seed=schedule_variation_seed,
        occupancy_time_shift_hours=float(cohort_cfg.get("occupancy_time_shift_hours", 0.0)),
        transition_variability_scale=float(cohort_cfg.get("transition_variability_scale", 1.0)),
        state_duration_scale=float(cohort_cfg.get("state_duration_scale", 1.0)),
        occupancy_state_biases=dict(cohort_cfg.get("occupancy_state_biases", {})),
    )
    setpoints_cfg = dict(input_dataset.metadata.get("setpoints", {}))
    fallback_t_set_c = {
        "occupied_day": float(setpoints_cfg.get("occupied_day", input_dataset.T_set_C)),
        "sleeping": float(setpoints_cfg.get("sleeping", max(input_dataset.T_set_C - 4.0, 0.0))),
        "away": float(setpoints_cfg.get("away", max(input_dataset.T_set_C - 5.0, 0.0))),
    }.get(str(occupancy["schedule_state"]), float(input_dataset.T_set_C))
    control_schedule = dict(input_dataset.metadata.get("control_schedule", {}))
    T_set_C = resolve_time_of_day_setpoint(
        timestamp=schedule_timestamp,
        fallback_setpoint_c=fallback_t_set_c,
        control_schedule_cfg=control_schedule,
    )

    P_appliances_W = _scaled_profile_value(
        mapped_loads,
        "P_appliances_W",
        index=load_index,
        target_annual_kwh=target_electricity_kwh(
            {"baseline": baseline_cfg, "electricity_split": electricity_split_cfg},
            end_use="appliances",
        ),
    )
    P_lighting_W = _scaled_profile_value(
        mapped_loads,
        "P_lighting_W",
        index=load_index,
        target_annual_kwh=target_electricity_kwh(
            {"baseline": baseline_cfg, "electricity_split": electricity_split_cfg},
            end_use="lighting",
        ),
    )
    P_cooking_W = _scaled_profile_value(
        mapped_loads,
        "P_cooking_W",
        index=load_index,
        target_annual_kwh=target_electricity_kwh(
            {"baseline": baseline_cfg, "electricity_split": electricity_split_cfg},
            end_use="cooking",
        ),
    )
    if bool(mapped_loads.metadata.get("stochastic_direct_dhw_profile", False)):
        mapped_dhw = max(_value_at(mapped_loads, "P_dhw_W", index=load_index, default=0.0), 0.0)
    else:
        mapped_dhw = _scaled_profile_value(
            mapped_loads,
            "P_dhw_W",
            index=load_index,
            target_annual_kwh=float(baseline_cfg.get("dhw_kWh", 3000.0)),
        )
    explicit_dhw = input_dataset.Q_dhw_demand_W

    Q_app_W = P_appliances_W * input_dataset.appliance_heat_gain_fraction
    Q_lighting_W = P_lighting_W * input_dataset.lighting_heat_gain_fraction
    Q_cooking_W = P_cooking_W * input_dataset.cooking_heat_gain_fraction
    Q_explicit_internal_W = _value_at(internal_gains, "Q_internal_gains_W", index=internal_index, default=0.0)
    Q_internal_gains_W = occupancy["Q_occ_W"] + Q_app_W + Q_lighting_W + Q_cooking_W + Q_explicit_internal_W

    solar_snapshot = _solar_snapshot(
        solar_dataset=solar,
        target_timestamp=target_timestamp,
        floor_area_m2=float(input_dataset.metadata.get("building_inputs", {}).get("floor_area_m2", 100.0)),
        glazing_ratio=input_dataset.glazing_ratio,
        frame_fraction=input_dataset.frame_fraction,
        g_value=input_dataset.g_value,
        incidence_factor=input_dataset.incidence_factor,
        dirt_factor=input_dataset.dirt_factor,
        shading_factor=input_dataset.shading_factor,
        orientation_shares={
            "north": input_dataset.orientation_share_north,
            "east": input_dataset.orientation_share_east,
            "south": input_dataset.orientation_share_south,
            "west": input_dataset.orientation_share_west,
        },
    )
    Q_dhw_demand_W = explicit_dhw if explicit_dhw > 0.0 else mapped_dhw
    P_el_ev_charging_W = _ev_charging_snapshot(
        target_timestamp=target_timestamp,
        mapped_loads=mapped_loads,
        load_index=load_index,
        mobility_cfg=mobility_cfg,
        cohort_cfg=cohort_cfg,
        target_resolution_seconds=input_dataset.target_resolution_seconds,
    )
    P_pv_generation_W = _pv_generation_snapshot(
        solar_snapshot=solar_snapshot,
        der_cfg=der_cfg,
        cohort_cfg=cohort_cfg,
    )

    prepared = PreparedForcing(
        timeline_label="single-step-reference-physics",
        target_resolution_seconds=input_dataset.target_resolution_seconds,
        timestep_hours=input_dataset.timestep_hours,
        timestamp=target_timestamp.isoformat(),
        archetype_id=input_dataset.archetype_id,
        schedule_state=str(occupancy["schedule_state"]),
        occupied_probability=float(occupancy["occupied_probability"]),
        expected_occupants=float(occupancy["expected_occupants"]),
        T_outdoor_C=_value_at(weather, "T_outdoor_C", index=weather_index, default=input_dataset.T_outdoor_C),
        T_indoor_initial_C=input_dataset.T_indoor_initial_C,
        T_set_C=T_set_C,
        T_min_C=input_dataset.T_min_C,
        T_max_C=input_dataset.T_max_C,
        heat_loss_coefficient_W_per_C=input_dataset.heat_loss_coefficient_W_per_C,
        thermal_mass_Wh_per_C=input_dataset.thermal_mass_Wh_per_C,
        C_J_per_K=input_dataset.C_J_per_K,
        volume_m3=input_dataset.volume_m3,
        ventilation_type=input_dataset.ventilation_type,
        eta_HRV=input_dataset.eta_HRV,
        ACH_inf=input_dataset.ACH_inf,
        ACH_vent_base=input_dataset.ACH_vent_base,
        ACH_vent_occupied=input_dataset.ACH_vent_occupied,
        Q_heating_max_W=input_dataset.Q_heating_max_W,
        heating_cop=input_dataset.heating_cop,
        dhw_cop=input_dataset.dhw_cop,
        Q_dhw_demand_W=Q_dhw_demand_W,
        P_el_appliances_W=P_appliances_W,
        P_el_lighting_W=P_lighting_W,
        P_el_cooking_W=P_cooking_W,
        P_el_ev_charging_W=P_el_ev_charging_W,
        P_pv_generation_W=P_pv_generation_W,
        Q_occ_W=float(occupancy["Q_occ_W"]),
        Q_app_W=Q_app_W,
        Q_lighting_W=Q_lighting_W,
        Q_cooking_W=Q_cooking_W,
        Q_internal_gains_W=Q_internal_gains_W,
        I_solar_north_W_per_m2=solar_snapshot.get("I_solar_north_W_per_m2", 0.0),
        I_solar_east_W_per_m2=solar_snapshot.get("I_solar_east_W_per_m2", 0.0),
        I_solar_south_W_per_m2=solar_snapshot.get("I_solar_south_W_per_m2", 0.0),
        I_solar_west_W_per_m2=solar_snapshot.get("I_solar_west_W_per_m2", 0.0),
        Q_solar_gains_W=solar_snapshot["Q_solar_gains_W"],
        metadata={
            "dataset_id": input_dataset.dataset_id,
            "household_count": input_dataset.household_count,
            "modules": dict(input_dataset.metadata.get("modules", {})),
            "source_provenance": input_dataset.metadata.get("source_provenance", {}),
            "merged_shape": {"rows": len(merged["rows"]), "columns": len(merged["rows"][0]) if merged["rows"] else 0},
            "mapped_load_columns": mapped_loads.column_names,
            "merged_rows_preview": merged["rows"][:1] if include_preview else [],
            "occupancy_probabilities": {
                "away": float(occupancy["prob_away"]),
                "awake": float(occupancy["prob_awake"]),
                "sleep": float(occupancy["prob_sleep"]),
            },
            "internal_gains_source": "occupancy_plus_end_use_recovery_plus_explicit_internal",
            "timestamp_selection": "exact_then_month_day_hour_minute",
            "control_cfg": dict(input_dataset.metadata.get("control", {})),
            "control_schedule": control_schedule,
            "setpoints_cfg": dict(input_dataset.metadata.get("setpoints", {})),
            "model_cfg": dict(input_dataset.metadata.get("model", {})),
            "air_cfg": dict(input_dataset.metadata.get("air", {})),
            "ventilation_cfg": dict(input_dataset.metadata.get("ventilation", {})),
            "baseline": baseline_cfg,
            "electricity_split": electricity_split_cfg,
            "normalized_modelled_electricity_split": normalized_split,
            "technology_baseline": dict(input_dataset.metadata.get("technology_baseline", {})),
            "technology_sources": dict(input_dataset.metadata.get("technology_sources", {})),
            "technologies": dict(input_dataset.metadata.get("technologies", {})),
            "systems": dict(input_dataset.metadata.get("systems", {})),
            "der": der_cfg,
            "mobility": mobility_cfg,
            "heat_pump_cfg": dict(input_dataset.metadata.get("heat_pump", {})),
        },
    )
    LOGGER.info(
        "adapter.end merged_rows=%s mapped_columns=%s timestep_hours=%.3f schedule_state=%s",
        len(merged["rows"]),
        mapped_loads.column_names,
        prepared.timestep_hours,
        prepared.schedule_state,
    )
    return prepared
