"""Sequential annual simulation runner for model_v3."""

from __future__ import annotations

from dataclasses import replace
import logging
from time import perf_counter
from typing import Any, Mapping

import pandas as pd

from model_v3.baseline import annual_average_power_w, target_electricity_kwh
from model_v3.adapters.forcing_builder import build_prepared_forcing
from model_v3.control.control_core import run_control
from model_v3.data.data_module import load_all_sources
from model_v3.interfaces import InputDataset, TimeSeriesData
from model_v3.output.output_core import assemble_outputs
from model_v3.physics.physics_core import run_physics
from model_v3.systems.system_core import run_systems
from model_v3.utils.energy import infer_step_durations_seconds, integrate_power_series_kwh
from model_v3.utils.feature_flags import (
    disabled_control_state,
    disabled_physics_state,
    disabled_system_state,
    enabled_runtime_modules,
    is_module_enabled,
)

LOGGER = logging.getLogger(__name__)

ELECTRICITY_END_USE_COLUMNS = {
    "appliances": "P_el_appliances_W",
    "lighting": "P_el_lighting_W",
    "cooking": "P_el_cooking_W",
    "dhw": "P_el_dhw_W",
    "space_heating": "P_el_space_heating_W",
}
TECHNOLOGY_POWER_COLUMNS = (
    "P_el_space_heating_technology_W",
    "P_el_dhw_technology_W",
    "P_el_ev_charging_W",
    "P_pv_generation_W",
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
)
TOTAL_CARRIER_COLUMNS = {
    "P_gas_total_W": ("P_gas_space_heating_W", "P_gas_dhw_W"),
    "P_oil_total_W": ("P_oil_space_heating_W", "P_oil_dhw_W"),
    "P_biomass_total_W": ("P_biomass_space_heating_W", "P_biomass_dhw_W"),
    "P_propane_total_W": ("P_propane_space_heating_W", "P_propane_dhw_W"),
    "P_coal_total_W": ("P_coal_space_heating_W", "P_coal_dhw_W"),
    "P_district_heat_total_W": ("P_district_heat_space_heating_W", "P_district_heat_dhw_W"),
}
WEATHER_COVERAGE_TOLERANCE_HOURS = 24


def _representative_timestep_seconds(timestamps: pd.Series) -> float:
    """Return a representative timestep from an explicit time index."""

    durations = [duration for duration in infer_step_durations_seconds(timestamps) if duration > 0.0]
    if not durations:
        return 0.0
    return float(pd.Series(durations, dtype=float).median())


def _apply_baseline_electricity_targets(
    frame: pd.DataFrame,
    config: Mapping[str, Any],
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Rescale annual electric end uses to the configured literature baseline."""

    adjusted = frame.copy()
    diagnostics: dict[str, Any] = {
        "target_annual_kWh_by_end_use": {},
        "raw_annual_kWh_by_end_use": {},
        "calibrated_annual_kWh_by_end_use": {},
        "scale_factor_by_end_use": {},
        "fallback_used_by_end_use": {},
    }
    for column_name in ELECTRICITY_END_USE_COLUMNS.values():
        raw_column_name = f"raw_{column_name}"
        adjusted[raw_column_name] = pd.to_numeric(adjusted[column_name], errors="coerce").fillna(0.0)
    adjusted["raw_P_el_total_W"] = sum(
        adjusted[f"raw_{column_name}"]
        for column_name in ELECTRICITY_END_USE_COLUMNS.values()
    )
    disabled_end_uses: set[str] = set()
    if not is_module_enabled(config, "systems"):
        disabled_end_uses.update({"space_heating", "dhw"})
    if not is_module_enabled(config, "control"):
        disabled_end_uses.add("space_heating")

    for end_use, column_name in ELECTRICITY_END_USE_COLUMNS.items():
        if end_use in disabled_end_uses:
            raw_column_name = f"raw_{column_name}"
            adjusted[column_name] = pd.to_numeric(adjusted[raw_column_name], errors="coerce").fillna(0.0)
            calibrated_kwh = integrate_power_series_kwh(adjusted[column_name], timestamps=adjusted["timestamp"])
            diagnostics["target_annual_kWh_by_end_use"][end_use] = 0.0
            diagnostics["raw_annual_kWh_by_end_use"][end_use] = float(calibrated_kwh)
            diagnostics["calibrated_annual_kWh_by_end_use"][end_use] = float(calibrated_kwh)
            diagnostics["scale_factor_by_end_use"][end_use] = 1.0
            diagnostics["fallback_used_by_end_use"][end_use] = False
            continue
        target_kwh = target_electricity_kwh(config, end_use=end_use)
        raw_column_name = f"raw_{column_name}"
        current_kwh = integrate_power_series_kwh(
            adjusted[raw_column_name],
            timestamps=adjusted["timestamp"],
        )
        if current_kwh > 1e-9:
            scale_factor = target_kwh / current_kwh
            adjusted[column_name] = adjusted[raw_column_name] * scale_factor
            fallback_used = False
        else:
            scale_factor = None
            adjusted[column_name] = annual_average_power_w(target_kwh)
            fallback_used = True
        calibrated_kwh = integrate_power_series_kwh(
            pd.to_numeric(adjusted[column_name], errors="coerce").fillna(0.0),
            timestamps=adjusted["timestamp"],
        )
        diagnostics["target_annual_kWh_by_end_use"][end_use] = float(target_kwh)
        diagnostics["raw_annual_kWh_by_end_use"][end_use] = float(current_kwh)
        diagnostics["calibrated_annual_kWh_by_end_use"][end_use] = float(calibrated_kwh)
        diagnostics["scale_factor_by_end_use"][end_use] = None if scale_factor is None else float(scale_factor)
        diagnostics["fallback_used_by_end_use"][end_use] = fallback_used
    adjusted["P_el_total_W"] = (
        adjusted["P_el_space_heating_W"]
        + adjusted["P_el_dhw_W"]
        + adjusted["P_el_appliances_W"]
        + adjusted["P_el_lighting_W"]
        + adjusted["P_el_cooking_W"]
    )
    for column_name in TECHNOLOGY_POWER_COLUMNS:
        if column_name not in adjusted:
            adjusted[column_name] = 0.0
        adjusted[column_name] = pd.to_numeric(adjusted[column_name], errors="coerce").fillna(0.0)

    adjusted["P_el_gross_actual_W"] = (
        adjusted["P_el_space_heating_technology_W"]
        + adjusted["P_el_dhw_technology_W"]
        + adjusted["P_el_appliances_W"]
        + adjusted["P_el_lighting_W"]
        + adjusted["P_el_cooking_W"]
        + adjusted["P_el_ev_charging_W"]
    )
    adjusted["P_el_net_grid_W"] = adjusted["P_el_gross_actual_W"] - adjusted["P_pv_generation_W"]
    adjusted["P_el_grid_import_W"] = adjusted["P_el_net_grid_W"].clip(lower=0.0)
    adjusted["P_el_grid_export_W"] = (-adjusted["P_el_net_grid_W"]).clip(lower=0.0)
    for total_column, source_columns in TOTAL_CARRIER_COLUMNS.items():
        adjusted[total_column] = sum(
            pd.to_numeric(adjusted[source_column], errors="coerce").fillna(0.0)
            for source_column in source_columns
        )
    return adjusted, diagnostics


def _integrate_optional_column(frame: pd.DataFrame, column_name: str) -> float:
    """Integrate a power column when present, otherwise return zero."""

    if column_name not in frame.columns:
        return 0.0
    return integrate_power_series_kwh(
        pd.to_numeric(frame[column_name], errors="coerce").fillna(0.0),
        timestamps=frame["timestamp"],
    )


def _annual_energy_by_carrier(frame: pd.DataFrame) -> dict[str, float]:
    """Return annual delivered-energy summaries by carrier and grid flow."""

    return {
        "electricity_legacy_calibrated": _integrate_optional_column(frame, "P_el_total_W"),
        "electricity_gross_actual": _integrate_optional_column(frame, "P_el_gross_actual_W"),
        "electricity_grid_import": _integrate_optional_column(frame, "P_el_grid_import_W"),
        "electricity_grid_export": _integrate_optional_column(frame, "P_el_grid_export_W"),
        "pv_generation": _integrate_optional_column(frame, "P_pv_generation_W"),
        "ev_charging": _integrate_optional_column(frame, "P_el_ev_charging_W"),
        "natural_gas": _integrate_optional_column(frame, "P_gas_total_W"),
        "heating_oil": _integrate_optional_column(frame, "P_oil_total_W"),
        "biomass": _integrate_optional_column(frame, "P_biomass_total_W"),
        "propane_butane": _integrate_optional_column(frame, "P_propane_total_W"),
        "coal": _integrate_optional_column(frame, "P_coal_total_W"),
        "district_heat": _integrate_optional_column(frame, "P_district_heat_total_W"),
    }


def _filter_dataset_to_year(dataset: TimeSeriesData, year: int) -> TimeSeriesData:
    """Return the subset of a timeseries dataset for a selected calendar year."""

    filtered_indices = [index for index, timestamp in enumerate(dataset.timestamps) if pd.Timestamp(timestamp).year == int(year)]
    if not filtered_indices:
        source_path = dataset.metadata.get("input_file_path") or dataset.metadata.get("source_name") or "unknown"
        message = (
            f"Requested year {int(year)} is unavailable for dataset {source_path}; "
            "retaining original representative data."
        )
        print(message)
        metadata = dict(dataset.metadata)
        metadata.setdefault("reference_year_warnings", []).append(message)
        return TimeSeriesData(
            timestamps=tuple(dataset.timestamps),
            columns={column_name: tuple(values) for column_name, values in dataset.columns.items()},
            metadata=metadata,
        )
    return TimeSeriesData(
        timestamps=tuple(dataset.timestamps[index] for index in filtered_indices),
        columns={
            column_name: tuple(values[index] for index in filtered_indices)
            for column_name, values in dataset.columns.items()
        },
        metadata=dict(dataset.metadata),
    )


def _expected_rows_for_year(year: int, target_resolution_seconds: int) -> int:
    """Return the expected full-year row count for a regular cadence."""

    hours = 8784 if pd.Timestamp(year=int(year), month=12, day=31).is_leap_year else 8760
    return int(round(hours * 3600.0 / max(int(target_resolution_seconds), 1)))


def _validate_weather_reference_year_coverage(
    dataset: TimeSeriesData,
    *,
    reference_year: int,
    target_resolution_seconds: int,
) -> None:
    """Fail clearly when the selected weather year is too short for annual use."""

    selected_rows = len(dataset.timestamps)
    expected_rows = _expected_rows_for_year(
        year=reference_year,
        target_resolution_seconds=target_resolution_seconds,
    )
    tolerance_rows = max(
        int(round(WEATHER_COVERAGE_TOLERANCE_HOURS * 3600.0 / max(int(target_resolution_seconds), 1))),
        1,
    )
    lower_bound = expected_rows - tolerance_rows
    upper_bound = expected_rows + tolerance_rows
    if lower_bound <= selected_rows <= upper_bound:
        return

    source_path = dataset.metadata.get("input_file_path") or dataset.metadata.get("source_name") or "unknown"
    if selected_rows == 0:
        message = (
            f"Requested year {int(reference_year)} is unavailable in weather dataset {source_path}; "
            "no rows were found for that year."
        )
        print(message)
        raise ValueError(message)
    raise ValueError(
        "Weather reference-year coverage is incomplete: "
        f"selected {selected_rows} rows for reference year {int(reference_year)}, "
        f"expected near {expected_rows} rows for target_resolution_seconds={int(target_resolution_seconds)} "
        f"(allowed range {lower_bound}-{upper_bound}). "
        f"Source: {source_path}. Check data.sources.weather.file_path, simulation.reference_year, "
        "and symlink/copy integrity; refusing to continue because forward-fill reconstruction could otherwise "
        "turn a short weather fragment into a silent annual artifact."
    )


def _filter_weather_to_reference_year(
    dataset: TimeSeriesData,
    *,
    reference_year: int,
    target_resolution_seconds: int,
) -> TimeSeriesData:
    """Select and validate the weather rows for the configured reference year."""

    filtered_indices = [index for index, timestamp in enumerate(dataset.timestamps) if pd.Timestamp(timestamp).year == int(reference_year)]
    filtered = TimeSeriesData(
        timestamps=tuple(dataset.timestamps[index] for index in filtered_indices),
        columns={
            column_name: tuple(values[index] for index in filtered_indices)
            for column_name, values in dataset.columns.items()
        },
        metadata=dict(dataset.metadata),
    )
    _validate_weather_reference_year_coverage(
        filtered,
        reference_year=reference_year,
        target_resolution_seconds=target_resolution_seconds,
    )
    return filtered


def _prepare_reference_year_input(input_dataset: InputDataset, config: Mapping[str, Any]) -> InputDataset:
    """Prepare an input bundle for sequential simulation over a coherent reference year."""

    simulation_cfg = dict(config.get("simulation", {}))
    data_cfg = dict(config.get("data", {}))
    reference_year = simulation_cfg.get("reference_year")
    max_steps = simulation_cfg.get("max_steps")
    source_data = dict(input_dataset.source_data)
    target_resolution_seconds = int(data_cfg.get("target_resolution_seconds", input_dataset.target_resolution_seconds or 3600))

    if reference_year is not None:
        if "weather" in source_data:
            source_data["weather"] = _filter_weather_to_reference_year(
                source_data["weather"],
                reference_year=int(reference_year),
                target_resolution_seconds=target_resolution_seconds,
            )
        if "load_profiles" in source_data:
            source_data["load_profiles"] = _filter_dataset_to_year(source_data["load_profiles"], int(reference_year))

    weather_timestamps = tuple(source_data.get("weather", TimeSeriesData()).timestamps)
    if not weather_timestamps:
        raise ValueError("Annual simulation requires a weather timeseries with explicit timestamps.")

    if max_steps is not None:
        capped_timestamps = weather_timestamps[: max(int(max_steps), 1)]
        source_data["weather"] = TimeSeriesData(
            timestamps=capped_timestamps,
            columns={
                column_name: tuple(values[: len(capped_timestamps)])
                for column_name, values in source_data["weather"].columns.items()
            },
            metadata=dict(source_data["weather"].metadata),
        )
        weather_timestamps = capped_timestamps

    prepared_metadata = dict(input_dataset.metadata)
    prepared_metadata["simulation"] = {
        "reference_year": reference_year,
        "selected_timestamps": len(weather_timestamps),
    }
    return replace(
        input_dataset,
        source_data=source_data,
        timestamp=pd.Timestamp(weather_timestamps[0]).isoformat(),
        metadata=prepared_metadata,
    )


def _run_step_layers(prepared: Any, config: Mapping[str, Any]):
    """Run enabled physics/control/systems stages for one prepared timestep."""

    physics_state = (
        run_physics(prepared_forcing=prepared)
        if is_module_enabled(config, "physics")
        else disabled_physics_state(prepared)
    )
    control_state = (
        run_control(physics_state=physics_state)
        if is_module_enabled(config, "control")
        else disabled_control_state(physics_state)
    )
    system_state = (
        run_systems(control_state=control_state)
        if is_module_enabled(config, "systems")
        else disabled_system_state(control_state, enabled_runtime_modules(config))
    )
    outputs = assemble_outputs(system_state=system_state)
    return physics_state, control_state, system_state, outputs


def _step_input_dataset(
    input_dataset: InputDataset,
    timestamp: pd.Timestamp,
    indoor_temperature_c: float,
    heating_on: bool,
) -> InputDataset:
    """Inject the carried dynamic state into the next timestep input."""

    metadata = dict(input_dataset.metadata)
    model_cfg = dict(metadata.get("model", {}))
    model_cfg["initial_heating_on"] = bool(heating_on)
    metadata["model"] = model_cfg
    return replace(
        input_dataset,
        timestamp=pd.Timestamp(timestamp).isoformat(),
        T_indoor_initial_C=float(indoor_temperature_c),
        metadata=metadata,
    )


def run_annual_simulation_from_input_data(input_data: InputDataset, config: Mapping[str, Any]) -> dict[str, Any]:
    """Run the annual simulation from a prepared input dataset."""

    timings = dict(input_data.metadata.get("pipeline_timings_seconds", {}))
    input_data = _prepare_reference_year_input(input_data, config=config)
    weather_dataset = input_data.source_data["weather"]
    timestamps = tuple(pd.Timestamp(timestamp) for timestamp in weather_dataset.timestamps)

    model_cfg = dict(input_data.metadata.get("model", {}))
    indoor_temperature_c = float(model_cfg.get("initial_indoor_temperature_C", input_data.T_indoor_initial_C))
    heating_on = bool(model_cfg.get("initial_heating_on", False))

    records: list[dict[str, Any]] = []
    loop_start = perf_counter()
    for step_index, timestamp in enumerate(timestamps):
        step_input = _step_input_dataset(
            input_dataset=input_data,
            timestamp=timestamp,
            indoor_temperature_c=indoor_temperature_c,
            heating_on=heating_on,
        )
        prepared = build_prepared_forcing(input_dataset=step_input, include_preview=False)
        _, control_state, system_state, outputs = _run_step_layers(prepared, config)

        records.append(
            {
                "timestamp": pd.Timestamp(timestamp),
                "archetype_id": prepared.archetype_id,
                "schedule_state": prepared.schedule_state,
                "occupied_probability": prepared.occupied_probability,
                "T_outdoor_C": prepared.T_outdoor_C,
                "T_set_C": prepared.T_set_C,
                "T_indoor_prev_C": system_state.T_indoor_prev_C,
                "T_indoor_free_float_C": system_state.T_indoor_free_float_C,
                "T_indoor_next_C": system_state.T_indoor_next_C,
                "Q_occ_W": prepared.Q_occ_W,
                "Q_app_W": prepared.Q_app_W,
                "Q_lighting_W": prepared.Q_lighting_W,
                "Q_cooking_W": prepared.Q_cooking_W,
                "Q_internal_gains_W": prepared.Q_internal_gains_W,
                "Q_solar_gains_W": prepared.Q_solar_gains_W,
                "Q_air_W": control_state.Q_air_W,
                "Q_dhw_demand_W": control_state.Q_dhw_demand_W,
                "Q_heating_requested_W": control_state.Q_heating_requested_W,
                "Q_heating_supplied_W": system_state.Q_heating_supplied_W,
                "Q_unmet_heating_W": outputs.Q_unmet_heating_W,
                "Q_excess_heat_W": outputs.Q_excess_heat_W,
                "comfort_violation_degC": outputs.comfort_violation_degC,
                "P_el_total_W": outputs.P_el_total_W,
                "P_el_space_heating_W": outputs.P_el_space_heating_W,
                "P_el_dhw_W": outputs.P_el_dhw_W,
                "P_el_space_heating_technology_W": system_state.P_el_space_heating_technology_W,
                "P_el_dhw_technology_W": system_state.P_el_dhw_technology_W,
                "P_el_appliances_W": outputs.P_el_appliances_W,
                "P_el_lighting_W": outputs.P_el_lighting_W,
                "P_el_cooking_W": outputs.P_el_cooking_W,
                "P_el_ev_charging_W": system_state.P_el_ev_charging_W,
                "P_pv_generation_W": system_state.P_pv_generation_W,
                "P_el_gross_actual_W": system_state.P_el_gross_actual_W,
                "P_el_net_grid_W": system_state.P_el_net_grid_W,
                "P_el_grid_import_W": system_state.P_el_grid_import_W,
                "P_el_grid_export_W": system_state.P_el_grid_export_W,
                "P_gas_space_heating_W": system_state.P_gas_space_heating_W,
                "P_gas_dhw_W": system_state.P_gas_dhw_W,
                "P_oil_space_heating_W": system_state.P_oil_space_heating_W,
                "P_oil_dhw_W": system_state.P_oil_dhw_W,
                "P_biomass_space_heating_W": system_state.P_biomass_space_heating_W,
                "P_biomass_dhw_W": system_state.P_biomass_dhw_W,
                "P_propane_space_heating_W": system_state.P_propane_space_heating_W,
                "P_propane_dhw_W": system_state.P_propane_dhw_W,
                "P_coal_space_heating_W": system_state.P_coal_space_heating_W,
                "P_coal_dhw_W": system_state.P_coal_dhw_W,
                "P_district_heat_space_heating_W": system_state.P_district_heat_space_heating_W,
                "P_district_heat_dhw_W": system_state.P_district_heat_dhw_W,
                "heating_technology_type": system_state.metadata.get("heating_technology_type", "legacy_representative"),
                "dhw_technology_type": system_state.metadata.get("dhw_technology_type", "legacy_representative"),
                "heating_on": control_state.heating_on,
                "integration_substeps": int(system_state.metadata.get("integration_substeps", 1)),
            }
        )
        indoor_temperature_c = float(system_state.T_indoor_next_C)
        heating_on = bool(control_state.heating_on)
        if (step_index + 1) % 1000 == 0:
            LOGGER.info("simulation.annual progress=%s/%s", step_index + 1, len(timestamps))

    timings["annual_loop_seconds"] = perf_counter() - loop_start
    frame = pd.DataFrame.from_records(records)
    frame["Q_total_thermal_W"] = (
        pd.to_numeric(frame["Q_heating_supplied_W"], errors="coerce").fillna(0.0)
        + pd.to_numeric(frame["Q_dhw_demand_W"], errors="coerce").fillna(0.0)
    )
    frame, electricity_calibration = _apply_baseline_electricity_targets(frame=frame, config=config)
    annual_energy_kwh = integrate_power_series_kwh(frame["P_el_total_W"], timestamps=frame["timestamp"])
    annual_energy_by_carrier = _annual_energy_by_carrier(frame)
    space_heating_kwh = integrate_power_series_kwh(frame["P_el_space_heating_W"], timestamps=frame["timestamp"])
    dhw_electric_kwh = integrate_power_series_kwh(frame["P_el_dhw_W"], timestamps=frame["timestamp"])
    space_heating_thermal_kwh = integrate_power_series_kwh(frame["Q_heating_supplied_W"], timestamps=frame["timestamp"])
    dhw_thermal_kwh = integrate_power_series_kwh(frame["Q_dhw_demand_W"], timestamps=frame["timestamp"])
    timestep_seconds = _representative_timestep_seconds(frame["timestamp"])

    LOGGER.info(
        "thermal.space_heating mean_power_W=%.3f timestep_s=%.3f annual_energy_kWh=%.3f",
        float(frame["Q_heating_supplied_W"].mean()),
        timestep_seconds,
        space_heating_thermal_kwh,
    )
    LOGGER.info(
        "thermal.dhw mean_power_W=%.3f timestep_s=%.3f annual_energy_kWh=%.3f",
        float(frame["Q_dhw_demand_W"].mean()),
        timestep_seconds,
        dhw_thermal_kwh,
    )

    return {
        "timestamps": [timestamp.isoformat() for timestamp in frame["timestamp"]],
        "profile_frame": frame,
        "aggregate_profile": frame["P_el_total_W"].astype(float).tolist(),
        "mean_profile": float(frame["P_el_total_W"].mean()),
        "std_profile": float(frame["P_el_total_W"].std(ddof=0)),
        "P10_profile": float(frame["P_el_total_W"].quantile(0.10)),
        "P50_profile": float(frame["P_el_total_W"].quantile(0.50)),
        "P90_profile": float(frame["P_el_total_W"].quantile(0.90)),
        "annual_energy_kWh": annual_energy_kwh,
        "annual_energy_by_carrier_kWh": annual_energy_by_carrier,
        "annual_grid_import_kWh": annual_energy_by_carrier["electricity_grid_import"],
        "annual_grid_export_kWh": annual_energy_by_carrier["electricity_grid_export"],
        "annual_pv_generation_kWh": annual_energy_by_carrier["pv_generation"],
        "annual_ev_charging_kWh": annual_energy_by_carrier["ev_charging"],
        "space_heating_energy_kWh": space_heating_kwh,
        "space_heating_electric_kWh": space_heating_kwh,
        "dhw_electric_kWh": dhw_electric_kwh,
        "space_heating_thermal_kWh": space_heating_thermal_kwh,
        "dhw_thermal_kWh": dhw_thermal_kwh,
        "electricity_calibration": electricity_calibration,
        "peak_dhw_thermal_W": float(frame["Q_dhw_demand_W"].max()) if not frame.empty else 0.0,
        "peak_total_thermal_W": float(frame["Q_total_thermal_W"].max()) if not frame.empty else 0.0,
        "n_steps": int(len(frame)),
        "household_count": max(int(input_data.household_count or 1), 1),
        "profile_representation": "per_household",
        "timestep_seconds": timestep_seconds,
        "reference_year": input_data.metadata.get("simulation", {}).get("reference_year"),
        "pipeline_timings_seconds": timings,
        "technology_metadata": {
            "technology_sources": dict(input_data.metadata.get("technology_sources", {})),
            "technologies_present": bool(input_data.metadata.get("technologies", {})),
            "carrier_aware_outputs": True,
        },
    }


def run_annual_simulation(config: Mapping[str, Any]) -> dict[str, Any]:
    """Run the deterministic model_v3 layers sequentially over a reference-year timeline."""

    timings: dict[str, float] = {}
    stage_start = perf_counter()
    raw_input_data = load_all_sources(config=config)
    timings["load_all_sources"] = perf_counter() - stage_start

    input_data = replace(
        raw_input_data,
        metadata={
            **raw_input_data.metadata,
            "pipeline_timings_seconds": timings,
        },
    )
    return run_annual_simulation_from_input_data(input_data=input_data, config=config)
