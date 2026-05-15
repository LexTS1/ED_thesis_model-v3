"""Generate Richardson-style stochastic baseload reference profiles."""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any, Mapping

import numpy as np
import pandas as pd


class RichardsonReferenceError(RuntimeError):
    """Raised when Richardson reference generation cannot be completed."""


@dataclass(frozen=True)
class RichardsonReference:
    """Container for generated reference profiles."""

    profile_frame: pd.DataFrame
    metadata: dict[str, Any]


def _local_naive_timestamp(value: Any) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is not None:
        return timestamp.tz_convert("UTC").tz_localize(None)
    return timestamp


def _target_timestamps(
    config: Mapping[str, Any],
    *,
    timestep_seconds: int,
    n_days: int | None = None,
    target_timestamps: list[Any] | tuple[Any, ...] | pd.DatetimeIndex | None = None,
) -> pd.DatetimeIndex:
    if target_timestamps is not None:
        return pd.DatetimeIndex([_local_naive_timestamp(value) for value in target_timestamps])
    simulation_cfg = dict(config.get("simulation", {}))
    start = pd.Timestamp(simulation_cfg.get("start_timestamp", "2023-01-01T00:00:00+01:00"))
    max_steps = simulation_cfg.get("max_steps")
    if n_days is not None:
        periods = int(max(int(n_days), 1) * 86400 / timestep_seconds)
    elif max_steps not in {None, ""}:
        periods = max(int(max_steps), 1)
    else:
        periods = int(365 * 86400 / timestep_seconds)
    return pd.date_range(start=start, periods=periods, freq=pd.Timedelta(seconds=timestep_seconds))


def _initial_day_for_richardson(config: Mapping[str, Any]) -> int:
    start = pd.Timestamp(dict(config.get("simulation", {})).get("start_timestamp", "2023-01-01T00:00:00+01:00"))
    return int(start.isoweekday())


def _occupants_from_population(
    sampled_population: list[Mapping[str, Any]],
    household_index: int,
) -> int:
    if household_index < len(sampled_population):
        raw = sampled_population[household_index]
        if isinstance(raw, Mapping):
            value = dict(raw).get("occupants_per_dwelling", 2.0)
        else:
            value = 2.0
    else:
        value = 2.0
    return int(min(max(round(float(value)), 1), 5))


def _annual_demand_from_model(
    model_profiles: Mapping[str, list[float]],
    household_id: str,
    timestep_seconds: int,
) -> float:
    values = np.asarray(model_profiles.get(household_id, []), dtype=float)
    if values.size == 0:
        return 0.0
    observed_kwh = float(np.sum(np.clip(values, 0.0, None)) * timestep_seconds / 3_600_000.0)
    observed_seconds = float(values.size * timestep_seconds)
    if observed_seconds <= 0.0:
        return 0.0
    return observed_kwh * (365.0 * 86400.0 / observed_seconds)


def _slice_cyclic(values: np.ndarray, start: int, length: int) -> np.ndarray:
    if values.size == 0:
        return np.zeros(length, dtype=float)
    indices = (np.arange(length, dtype=int) + int(start)) % values.size
    return np.asarray(values[indices], dtype=float)


def _richardson_slice_start(timestamps: pd.DatetimeIndex, timestep_seconds: int, full_year_steps: int) -> int:
    if len(timestamps) == 0 or full_year_steps <= 0:
        return 0
    first = pd.Timestamp(timestamps[0])
    seconds_since_year_start = (
        (int(first.dayofyear) - 1) * 86400
        + int(first.hour) * 3600
        + int(first.minute) * 60
        + int(first.second)
    )
    return int(seconds_since_year_start // timestep_seconds) % int(full_year_steps)


def _initial_day_from_timestamps(timestamps: pd.DatetimeIndex, config: Mapping[str, Any]) -> int:
    if len(timestamps) > 0:
        return int(pd.Timestamp(timestamps[0]).isoweekday())
    return _initial_day_for_richardson(config)


def _fallback_profiles(
    *,
    config: Mapping[str, Any],
    n_households: int,
    timestep_seconds: int,
    seed: int,
    sampled_population: list[Mapping[str, Any]],
    n_days: int | None,
    target_timestamps: list[Any] | tuple[Any, ...] | pd.DatetimeIndex | None,
) -> RichardsonReference:
    """Return deterministic Richardson-like profiles for tests and offline smoke runs."""

    rng = np.random.default_rng(seed)
    timestamps = _target_timestamps(
        config,
        timestep_seconds=timestep_seconds,
        n_days=n_days,
        target_timestamps=target_timestamps,
    )
    hours = np.asarray(timestamps.hour, dtype=float)
    weekday = np.asarray(timestamps.dayofweek < 5, dtype=float)
    daylight = np.clip(np.sin(np.pi * (hours - 6.0) / 12.0), 0.0, None)
    rows: list[dict[str, Any]] = []

    for household_index in range(n_households):
        household_id = f"household_{household_index:03d}"
        occupants = _occupants_from_population(sampled_population, household_index)
        morning = np.exp(-0.5 * ((hours - 7.0) / 1.7) ** 2)
        evening = np.exp(-0.5 * ((hours - 19.0) / 2.2) ** 2)
        midday = np.exp(-0.5 * ((hours - 13.0) / 2.8) ** 2)
        active_probability = np.clip(0.06 + 0.20 * morning + 0.50 * evening + 0.10 * midday + 0.08 * (1.0 - weekday), 0.0, 0.98)
        occupancy = rng.binomial(occupants, active_probability).astype(float)
        appliance_noise = rng.gamma(shape=1.4, scale=1.0, size=len(timestamps))
        appliances = (55.0 + 35.0 * occupants + 80.0 * active_probability) * appliance_noise
        lighting = np.maximum(0.0, (1.0 - daylight) * (30.0 + 35.0 * occupancy) * (0.35 + 0.65 * active_probability))
        total = appliances + lighting
        for timestamp, occ_value, app_value, light_value, total_value in zip(
            timestamps,
            occupancy,
            appliances,
            lighting,
            total,
        ):
            rows.append(
                {
                    "timestamp": timestamp,
                    "household_id": household_id,
                    "occupants": occupants,
                    "occupancy": float(occ_value),
                    "appliances_W": float(app_value),
                    "lighting_W": float(light_value),
                    "total_W": float(total_value),
                    "generator": "fallback_richardson_like",
                }
            )

    return RichardsonReference(
        profile_frame=pd.DataFrame(rows),
        metadata={
            "generator": "fallback_richardson_like",
            "n_households": int(n_households),
            "timestep_seconds": int(timestep_seconds),
            "seed": int(seed),
            "limitations": "Deterministic Richardson-like fallback for tests; not an external validation reference.",
        },
    )


def _generate_with_richardsonpy(
    *,
    config: Mapping[str, Any],
    model_profiles: Mapping[str, list[float]],
    n_households: int,
    timestep_seconds: int,
    seed: int,
    sampled_population: list[Mapping[str, Any]],
    n_days: int | None,
    normalize_to_model_energy: bool,
    target_timestamps: list[Any] | tuple[Any, ...] | pd.DatetimeIndex | None,
) -> RichardsonReference:
    try:
        import richardsonpy.classes.electric_load as eload
        import richardsonpy.classes.occupancy as occ
        import richardsonpy.functions.load_radiation as loadrad
        import richardsonpy.functions.change_resolution as change_resolution
    except Exception as exc:  # pragma: no cover - depends on optional package
        raise RichardsonReferenceError(
            "richardsonpy is not installed or cannot be imported. Install it or run with fallback mode."
        ) from exc

    random.seed(seed)
    np.random.seed(seed)
    timestamps = _target_timestamps(
        config,
        timestep_seconds=timestep_seconds,
        n_days=n_days,
        target_timestamps=target_timestamps,
    )
    required_days = max(int(np.ceil(len(timestamps) * timestep_seconds / 86400.0)), 1)
    initial_day = _initial_day_from_timestamps(timestamps, config)
    q_direct, q_diffuse = loadrad.get_rad_from_try_path()
    q_direct = change_resolution.change_resolution(q_direct, old_res=3600, new_res=timestep_seconds)
    q_diffuse = change_resolution.change_resolution(q_diffuse, old_res=3600, new_res=timestep_seconds)
    q_direct = np.asarray(q_direct, dtype=float)
    q_diffuse = np.asarray(q_diffuse, dtype=float)
    simulation_days = max(int(round(len(q_direct) * timestep_seconds / 86400.0)), 1)
    full_year_steps = len(q_direct)
    slice_start = _richardson_slice_start(timestamps, timestep_seconds, full_year_steps)

    rows: list[dict[str, Any]] = []
    for household_index in range(n_households):
        household_id = f"household_{household_index:03d}"
        occupants = _occupants_from_population(sampled_population, household_index)
        annual_demand = None
        if normalize_to_model_energy:
            model_annual_kwh = _annual_demand_from_model(model_profiles, household_id, timestep_seconds)
            if model_annual_kwh > 1e-9:
                annual_demand = model_annual_kwh
        occ_obj = occ.Occupancy(
            number_occupants=occupants,
            initial_day=initial_day,
            nb_days=simulation_days,
            do_profile=True,
        )
        el_load = eload.ElectricLoad(
            occ_profile=occ_obj.occupancy,
            total_nb_occ=occupants,
            q_direct=q_direct,
            q_diffuse=q_diffuse,
            annual_demand=annual_demand,
            timestep=timestep_seconds,
            initial_day=initial_day,
            do_normalization=normalize_to_model_energy and annual_demand is not None,
            save_app_light=True,
        )
        full_occupancy = change_resolution.change_resolution(
            values=occ_obj.occupancy,
            old_res=600,
            new_res=timestep_seconds,
        )
        total = _slice_cyclic(np.asarray(el_load.loadcurve, dtype=float), slice_start, len(timestamps))
        lighting_source = el_load.light_load if el_load.light_load is not None else np.zeros_like(el_load.loadcurve)
        appliance_source = el_load.app_load if el_load.app_load is not None else np.zeros_like(el_load.loadcurve)
        lighting = _slice_cyclic(np.asarray(lighting_source, dtype=float), slice_start, len(timestamps))
        appliances = _slice_cyclic(np.asarray(appliance_source, dtype=float), slice_start, len(timestamps))
        occupancy = _slice_cyclic(np.asarray(full_occupancy, dtype=float), slice_start, len(timestamps))
        for timestamp, occ_value, app_value, light_value, total_value in zip(
            timestamps,
            occupancy,
            appliances,
            lighting,
            total,
        ):
            rows.append(
                {
                    "timestamp": timestamp,
                    "household_id": household_id,
                    "occupants": occupants,
                    "occupancy": float(occ_value),
                    "appliances_W": float(app_value),
                    "lighting_W": float(light_value),
                    "total_W": float(total_value),
                    "generator": "richardsonpy",
                }
            )

    return RichardsonReference(
        profile_frame=pd.DataFrame(rows),
        metadata={
            "generator": "richardsonpy",
            "n_households": int(n_households),
            "timestep_seconds": int(timestep_seconds),
            "seed": int(seed),
            "initial_day": int(initial_day),
            "n_days": int(required_days),
            "simulation_days": int(simulation_days),
            "slice_start_step": int(slice_start),
            "shape_normalized_to_model_annualized_energy": bool(normalize_to_model_energy),
            "limitations": (
                "Richardsonpy is a synthetic UK-origin stochastic reference. It validates non-thermal profile "
                "structure, not Belgian measured demand or thermal/PV/EV behaviour."
            ),
        },
    )


def generate_richardson_reference(
    *,
    config: Mapping[str, Any],
    model_profiles: Mapping[str, list[float]],
    sampled_population: list[Mapping[str, Any]],
    n_households: int,
    timestep_seconds: int,
    seed: int,
    n_days: int | None = None,
    mode: str = "shape-normalized",
    allow_fallback: bool = False,
    target_timestamps: list[Any] | tuple[Any, ...] | pd.DatetimeIndex | None = None,
) -> RichardsonReference:
    """Generate a Richardson reference population aligned to the model horizon."""

    normalize_to_model_energy = str(mode).strip().lower() in {"shape-normalized", "shape_normalized", "normalized"}
    if allow_fallback:
        return _fallback_profiles(
            config=config,
            n_households=n_households,
            timestep_seconds=timestep_seconds,
            seed=seed,
            sampled_population=sampled_population,
            n_days=n_days,
            target_timestamps=target_timestamps,
        )
    return _generate_with_richardsonpy(
        config=config,
        model_profiles=model_profiles,
        n_households=n_households,
        timestep_seconds=timestep_seconds,
        seed=seed,
        sampled_population=sampled_population,
        n_days=n_days,
        normalize_to_model_energy=normalize_to_model_energy,
        target_timestamps=target_timestamps,
    )
