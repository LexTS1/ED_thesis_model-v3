"""Per-source loaders and reference-input resolvers for model_v3."""

from __future__ import annotations

from functools import lru_cache
from datetime import datetime
from pathlib import Path
import re
from typing import Any, Mapping

import pandas as pd
import yaml

from model_v3.interfaces import TimeSeriesData


ORIENTATION_COLUMN_MAP = {
    "south": "I_south",
    "east": "I_east",
    "west": "I_west",
    "north": "I_north",
}
TIME_PATTERN = re.compile(r"^\d{8}:\d{4}$")
FILENAME_PATTERN = re.compile(r"_(?P<slope>-?\d+)deg_(?P<azimuth>-?\d+)deg_(?P<year_start>\d{4})_(?P<year_end>\d{4})\.csv$")


def _as_float(value: Any, default: float) -> float:
    """Safely coerce a scalar to float with a deterministic fallback."""

    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _parse_timestamps(raw_timestamps: list[Any] | tuple[Any, ...] | None, fallback_timestamp: str) -> tuple[datetime, ...]:
    """Parse timestamps from config, falling back to a single deterministic timestamp."""

    if not raw_timestamps:
        raw_timestamps = [fallback_timestamp]

    parsed = pd.to_datetime(list(raw_timestamps), errors="coerce")
    return tuple(_normalise_timestamp(timestamp) for timestamp in parsed.to_pydatetime())


def _normalise_timestamp(timestamp: datetime | pd.Timestamp) -> datetime:
    """Normalise timestamps to timezone-aware Europe/Brussels datetimes."""

    pandas_timestamp = pd.Timestamp(timestamp)
    if pandas_timestamp.tzinfo is None:
        pandas_timestamp = pandas_timestamp.tz_localize(
            "Europe/Brussels",
            nonexistent="shift_forward",
            ambiguous=False,
        )
    else:
        pandas_timestamp = pandas_timestamp.tz_convert("Europe/Brussels")
    return pandas_timestamp.to_pydatetime()


def _build_source_dataset(
    source_name: str,
    source_cfg: Mapping[str, Any],
    columns: Mapping[str, list[Any] | tuple[Any, ...] | None],
    fallback_timestamp: str,
    default_resolution_seconds: int,
) -> TimeSeriesData:
    """Construct a structured per-source dataset with raw provenance attached."""

    timestamps = _parse_timestamps(source_cfg.get("timestamps"), fallback_timestamp)
    expected_length = len(timestamps)
    structured_columns: dict[str, tuple[float | None, ...]] = {}

    for column_name, raw_values in columns.items():
        if raw_values is None:
            structured_columns[column_name] = tuple(None for _ in range(expected_length))
            continue

        raw_sequence = list(raw_values)
        values: list[float | None] = []
        for index in range(expected_length):
            if index >= len(raw_sequence):
                values.append(None)
                continue

            raw_value = raw_sequence[index]
            if raw_value is None:
                values.append(None)
            else:
                values.append(_as_float(raw_value, 0.0))

        structured_columns[column_name] = tuple(values)

    original_resolution_seconds = int(source_cfg.get("original_timestep_seconds", default_resolution_seconds))
    return TimeSeriesData(
        timestamps=timestamps,
        columns=structured_columns,
        metadata={
            "source_name": source_name,
            "original_timestep_seconds": original_resolution_seconds,
            "original_resolution": original_resolution_seconds,
            "data_role": tuple(source_cfg.get("data_role", ("input",))),
        },
    )


@lru_cache(maxsize=32)
def _read_csv_cached(path: str) -> pd.DataFrame:
    """Read a CSV file once and reuse it across cohort household runs."""

    return pd.read_csv(path)


@lru_cache(maxsize=8)
def _read_yaml_cached(path: str) -> dict[str, Any]:
    """Read a YAML file once and reuse it across deterministic and cohort runs."""

    with Path(path).open("r", encoding="utf-8") as handle:
        return dict(yaml.safe_load(handle) or {})


def _resolve_path(path_str: str | None) -> Path | None:
    """Resolve a configured input path to an absolute path if present."""

    if not path_str:
        return None
    candidate = Path(path_str)
    if candidate.is_absolute():
        return candidate
    return Path.cwd() / candidate


def _series_from_frame(frame: pd.DataFrame, column_name: str, expected_length: int) -> list[float | None]:
    """Extract a numeric series from a frame with safe truncation and padding."""

    if column_name not in frame.columns:
        return [None for _ in range(expected_length)]

    series = pd.to_numeric(frame[column_name], errors="coerce").tolist()
    values: list[float | None] = []
    for index in range(expected_length):
        if index >= len(series) or pd.isna(series[index]):
            values.append(None)
        else:
            values.append(float(series[index]))
    return values


def _load_timeseries_from_csv(
    source_name: str,
    source_cfg: Mapping[str, Any],
    column_mapping: Mapping[str, str],
    fallback_timestamp: str,
    default_resolution_seconds: int,
) -> TimeSeriesData | None:
    """Load a structured source dataset from a configured CSV file."""

    resolved_path = _resolve_path(str(source_cfg.get("file_path", "")))
    if resolved_path is None or not resolved_path.exists():
        return None

    frame = _read_csv_cached(str(resolved_path)).copy()
    timestamp_column = str(source_cfg.get("timestamp_column", "timestamp"))
    if timestamp_column not in frame.columns:
        raise ValueError(f"{source_name} input is missing timestamp column: {timestamp_column}")

    frame[timestamp_column] = pd.to_datetime(frame[timestamp_column], errors="coerce")
    frame = frame.dropna(subset=[timestamp_column]).sort_values(timestamp_column).reset_index(drop=True)
    timestamps = tuple(_normalise_timestamp(timestamp) for timestamp in frame[timestamp_column].tolist())
    if not timestamps:
        timestamps = _parse_timestamps(None, fallback_timestamp)
        frame = pd.DataFrame({timestamp_column: list(timestamps)})

    expected_length = len(timestamps)
    columns = {
        target_name: tuple(_series_from_frame(frame, raw_name, expected_length))
        for target_name, raw_name in column_mapping.items()
    }

    return TimeSeriesData(
        timestamps=timestamps,
        columns=columns,
        metadata={
            "source_name": source_name,
            "original_timestep_seconds": int(source_cfg.get("original_timestep_seconds", default_resolution_seconds)),
            "original_resolution": int(source_cfg.get("original_timestep_seconds", default_resolution_seconds)),
            "input_file_path": str(resolved_path),
            "data_role": tuple(source_cfg.get("data_role", ("input",))),
        },
    )


def _load_lcl_aggregate_profile(
    source_cfg: Mapping[str, Any],
    default_resolution_seconds: int,
) -> TimeSeriesData | None:
    """Load and aggregate the LCL load-profile dataset into a representative total load profile."""

    resolved_path = _resolve_path(str(source_cfg.get("file_path", "")))
    if resolved_path is None or not resolved_path.exists():
        return None

    frame = _read_csv_cached(str(resolved_path)).copy()
    timestamp_column = str(source_cfg.get("timestamp_column", "DateTime"))
    if timestamp_column not in frame.columns:
        raise ValueError(f"load_profiles input is missing timestamp column: {timestamp_column}")

    frame[timestamp_column] = pd.to_datetime(frame[timestamp_column], errors="coerce")
    frame = frame.dropna(subset=[timestamp_column]).sort_values(timestamp_column).reset_index(drop=True)
    meter_columns = [column for column in frame.columns if column != timestamp_column]
    if not meter_columns:
        raise ValueError("load_profiles input does not contain meter columns.")

    numeric_frame = frame[meter_columns].apply(pd.to_numeric, errors="coerce")
    raw_mean_profile = numeric_frame.mean(axis=1, skipna=True).fillna(0.0)
    source_units = str(source_cfg.get("units", "kWh_per_interval")).strip().lower()
    interval_seconds = int(source_cfg.get("original_timestep_seconds", default_resolution_seconds))
    if source_units == "kwh_per_interval":
        total_load = raw_mean_profile * (3_600_000.0 / max(interval_seconds, 1))
    elif source_units == "w":
        total_load = raw_mean_profile
    else:
        raise ValueError(f"Unsupported load profile units: {source_units}")
    timestamps = tuple(_normalise_timestamp(timestamp) for timestamp in frame[timestamp_column].tolist())
    return TimeSeriesData(
        timestamps=timestamps,
        columns={"total_electricity_W": tuple(float(value) for value in total_load.tolist())},
        metadata={
            "source_name": "load_profiles",
            "original_timestep_seconds": int(source_cfg.get("original_timestep_seconds", default_resolution_seconds)),
            "original_resolution": int(source_cfg.get("original_timestep_seconds", default_resolution_seconds)),
            "input_file_path": str(resolved_path),
            "aggregation_basis": "mean_across_LCL_households",
            "meter_count": len(meter_columns),
            "source_units": source_units,
            "output_units": "W",
            "data_role": tuple(source_cfg.get("data_role", ("input",))),
        },
    )


def _load_end_use_shares(end_use_cfg: Mapping[str, Any]) -> dict[str, float]:
    """Load end-use shares from a legacy CSV export."""

    resolved_path = _resolve_path(str(end_use_cfg.get("file_path", "")))
    if resolved_path is None or not resolved_path.exists():
        return {
            "appliances": 0.45,
            "lighting": 0.08,
            "cooking": 0.07,
            "dhw": 0.15,
        }

    frame = _read_csv_cached(str(resolved_path)).copy()
    parameter_column = str(end_use_cfg.get("parameter_column", "parameter"))
    value_column = str(end_use_cfg.get("value_column", "value"))
    if parameter_column not in frame.columns or value_column not in frame.columns:
        raise ValueError("end_use_shares input is missing required columns.")

    lookup = {
        "Water heating share": "dhw",
        "Lighting and appliances share": "appliances_lighting",
        "Residual (AL+OT) share": "appliances_lighting",
        "Cooking share": "cooking",
    }
    shares = {value: 0.0 for value in lookup.values()}
    for _, row in frame.iterrows():
        mapped = lookup.get(str(row[parameter_column]))
        if mapped is not None:
            shares[mapped] = float(row[value_column]) / 100.0

    appliances_lighting = shares.get("appliances_lighting", 0.53)
    lighting_share_of_combined = float(end_use_cfg.get("lighting_fraction_of_appliance_bucket", 0.18))
    return {
        "appliances": appliances_lighting * (1.0 - lighting_share_of_combined),
        "lighting": appliances_lighting * lighting_share_of_combined,
        "cooking": shares.get("cooking", 0.07),
        "dhw": shares.get("dhw", 0.15),
    }


@lru_cache(maxsize=4)
def _load_archetype_table(path: str) -> pd.DataFrame:
    """Load and cache the merged model_v3 archetype table."""

    return pd.read_csv(path)


def _select_archetype_row(frame: pd.DataFrame, archetype_cfg: Mapping[str, Any]) -> pd.Series:
    """Resolve the selected archetype row using the configured selection mode."""

    selection_mode = str(archetype_cfg.get("selection", "highest_stock_weight"))
    if selection_mode == "archetype_id":
        archetype_id = str(archetype_cfg.get("archetype_id", ""))
        selected = frame.loc[frame["archetype_id"].astype(str) == archetype_id]
        if selected.empty:
            raise ValueError(f"Requested archetype_id not found: {archetype_id}")
        return selected.iloc[0]

    sorted_frame = frame.sort_values("stock_weight", ascending=False)
    return sorted_frame.iloc[0]


def _build_source_from_frame(
    source_name: str,
    frame: pd.DataFrame,
    timestamp_column: str,
    metadata: Mapping[str, Any],
) -> TimeSeriesData:
    """Convert a pandas frame with a timestamp column into TimeSeriesData."""

    working = frame.copy()
    working[timestamp_column] = pd.to_datetime(working[timestamp_column], errors="coerce")
    working = working.dropna(subset=[timestamp_column]).sort_values(timestamp_column).reset_index(drop=True)
    timestamps = tuple(_normalise_timestamp(timestamp) for timestamp in working[timestamp_column].tolist())
    columns = {
        column: tuple(float(value) if pd.notna(value) else None for value in pd.to_numeric(working[column], errors="coerce"))
        for column in working.columns
        if column != timestamp_column
    }
    return TimeSeriesData(
        timestamps=timestamps,
        columns=columns,
        metadata=dict(metadata),
    )


def _orientation_from_azimuth(azimuth_deg: int) -> str:
    """Map a PVGIS azimuth angle to a cardinal facade orientation."""

    if abs(azimuth_deg) <= 5:
        return "south"
    if -95 <= azimuth_deg <= -85:
        return "east"
    if 85 <= azimuth_deg <= 95:
        return "west"
    if abs(abs(azimuth_deg) - 180) <= 5:
        return "north"
    raise ValueError(f"Unsupported PVGIS azimuth `{azimuth_deg}` deg.")


def _detect_orientation_from_filename(path: Path) -> str:
    """Detect orientation from a PVGIS filename using labels or azimuth."""

    name = path.name.lower()
    for orientation in ORIENTATION_COLUMN_MAP:
        if orientation in name:
            return orientation
    match = FILENAME_PATTERN.search(path.name)
    if not match:
        raise ValueError(f"Could not detect PVGIS azimuth from filename `{path.name}`.")
    return _orientation_from_azimuth(int(match.group("azimuth")))


def _find_pvgis_header_row(path: Path) -> int:
    """Find the CSV header row that starts with the PVGIS `time,...` columns."""

    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle):
            if line.lstrip().startswith("time,"):
                return line_number
    raise ValueError(f"No PVGIS data header row starting with `time,` was found in `{path}`.")


@lru_cache(maxsize=4)
def _load_pvgis_unified_frame(raw_dir: str) -> pd.DataFrame:
    """Build a unified orientation-aware PVGIS irradiance frame from the raw directory."""

    root = Path(raw_dir)
    parsed_frames: dict[str, pd.DataFrame] = {}
    for path in sorted(root.glob("solardata*.csv")):
        orientation = _detect_orientation_from_filename(path)
        header_row = _find_pvgis_header_row(path)
        raw = pd.read_csv(path, skiprows=header_row, encoding="utf-8")
        data = raw.loc[raw["time"].astype(str).str.fullmatch(TIME_PATTERN.pattern, na=False)].copy()
        if data.empty:
            continue
        data["timestamp"] = pd.to_datetime(data["time"], format="%Y%m%d:%H%M", utc=True, errors="coerce")
        data = data.dropna(subset=["timestamp"])
        gi_column = "G(i)" if "G(i)" in data.columns else None
        if gi_column is None:
            for required in ("Gb(i)", "Gd(i)", "Gr(i)"):
                if required not in data.columns:
                    raise ValueError(f"PVGIS file `{path.name}` is missing `{required}`.")
            irradiance = (
                pd.to_numeric(data["Gb(i)"], errors="coerce")
                + pd.to_numeric(data["Gd(i)"], errors="coerce")
                + pd.to_numeric(data["Gr(i)"], errors="coerce")
            )
        else:
            irradiance = pd.to_numeric(data[gi_column], errors="coerce")
        parsed_frames[orientation] = pd.DataFrame(
            {
                "timestamp": data["timestamp"],
                ORIENTATION_COLUMN_MAP[orientation]: irradiance.clip(lower=0.0),
            }
        )

    unified: pd.DataFrame | None = None
    for orientation in ("south", "east", "west", "north"):
        frame = parsed_frames.get(orientation)
        if frame is None:
            frame = pd.DataFrame(
                {
                    "timestamp": pd.Series(dtype="datetime64[ns, UTC]"),
                    ORIENTATION_COLUMN_MAP[orientation]: pd.Series(dtype="Float64"),
                }
            )
        unified = frame if unified is None else unified.merge(frame, on="timestamp", how="outer")

    if unified is None:
        return pd.DataFrame({"timestamp": pd.Series(dtype="datetime64[ns, UTC]")})

    return unified.sort_values("timestamp").reset_index(drop=True)


def load_occupancy_spec(config: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Load the occupancy specification YAML used by the v2 physics path."""

    config = config or {}
    sources_cfg = dict(dict(config.get("data", {})).get("sources", {}))
    occupancy_cfg = dict(sources_cfg.get("occupancy", {}))
    resolved_path = _resolve_path(str(occupancy_cfg.get("spec_path", "")))
    if resolved_path is None or not resolved_path.exists():
        return {}
    return _read_yaml_cached(str(resolved_path))


def _load_building_from_archetype(config: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Load building parameters from the authoritative model_v3 archetype file."""

    config = config or {}
    building_cfg = dict(config.get("building", {}))
    archetype_cfg = dict(building_cfg.get("archetype_source", {}))
    resolved_path = _resolve_path(str(archetype_cfg.get("file_path", "")))
    if resolved_path is None or not resolved_path.exists():
        return {}

    frame = _load_archetype_table(str(resolved_path))
    row = _select_archetype_row(frame, archetype_cfg)

    ua_multiplier = float(building_cfg.get("ua_multiplier", 1.0))
    thermal_mass_multiplier = float(building_cfg.get("thermal_mass_multiplier", 1.0))
    infiltration_multiplier = float(building_cfg.get("infiltration_rate_multiplier", 1.0))
    occupancy_multiplier = float(building_cfg.get("occupants_multiplier", 1.0))
    occupants_per_dwelling = float(building_cfg.get("occupants_per_dwelling", row.get("occupants_per_dwelling", 2.0)))
    optional_metadata = {}
    for key in (
        "construction_period_id",
        "construction_period",
        "u_value_package_id",
        "u_value_package_source",
        "stock_weight_source",
    ):
        value = row.get(key)
        if value is not None and pd.notna(value):
            optional_metadata[key] = str(value)

    return {
        "archetype_id": str(row["archetype_id"]),
        "heat_loss_coefficient_W_per_C": float(row["H_W_per_K"]) * ua_multiplier,
        "UA_W_per_K": float(row["UA_W_per_K"]) * ua_multiplier,
        "T_set_C": float(row.get("setpoint_awake_C", building_cfg.get("default_setpoint_C", 21.0))),
        "setpoint_awake_C": float(row.get("setpoint_awake_C", 21.0)),
        "setpoint_sleep_C": float(row.get("setpoint_sleep_C", 18.0)),
        "setpoint_away_C": float(row.get("setpoint_away_C", 16.0)),
        "T_min_C": float(row.get("T_min_C", 15.0)),
        "T_max_C": float(row.get("T_max_C", 25.0)),
        "floor_area_m2": float(row["floor_area_m2"]),
        "volume_m3": float(row["volume_m3"]),
        "C_J_per_K": float(row["C_J_per_K"]) * thermal_mass_multiplier,
        "thermal_mass_kWh_per_K": float(row["thermal_mass_kWh_per_K"]) * thermal_mass_multiplier,
        "thermal_mass_Wh_per_C": float(row["thermal_mass_Wh_per_C"]) * thermal_mass_multiplier,
        "occupants_per_dwelling": occupants_per_dwelling * occupancy_multiplier,
        "occupant_gain_away_W_per_person": float(row.get("occupant_gain_away_W_per_person", 0.0)),
        "occupant_gain_awake_W_per_person": float(row.get("occupant_gain_awake_W_per_person", 70.0)),
        "occupant_gain_sleep_W_per_person": float(row.get("occupant_gain_sleep_W_per_person", 60.0)),
        "appliance_heat_gain_fraction": float(row.get("appliance_heat_gain_fraction", 0.7)),
        "lighting_heat_gain_fraction": float(row.get("lighting_heat_gain_fraction", 0.85)),
        "cooking_heat_gain_fraction": float(row.get("cooking_heat_gain_fraction", 0.5)),
        "internal_gain_placeholder_W_per_m2": float(row.get("internal_gain_placeholder_W_per_m2", 3.0)),
        "glazing_ratio": float(row.get("glazing_ratio", 0.16)),
        "g_value": float(row.get("g_value", 0.63)),
        "frame_fraction": float(row.get("frame_fraction", 1.0)),
        "dirt_factor": float(row.get("dirt_factor", 0.95)),
        "incidence_factor": float(row.get("incidence_factor", 0.9)),
        "shading_factor": float(row.get("shading_factor", 0.77)),
        "orientation_share_north": float(row.get("orientation_share_north", 0.2)),
        "orientation_share_east": float(row.get("orientation_share_east", 0.25)),
        "orientation_share_south": float(row.get("orientation_share_south", 0.35)),
        "orientation_share_west": float(row.get("orientation_share_west", 0.2)),
        "ACH50_reference_h_inv": float(row.get("ACH50_reference_h_inv", 6.4)),
        "N_factor_default": float(row.get("N_factor_default", 20.0)),
        "ACH_inf": float(row.get("ACH_inf_default_h_inv", 0.32)) * infiltration_multiplier,
        "ACH_vent_base": float(row.get("ACH_vent_base_h_inv", 0.2)),
        "ACH_vent_occupied": float(row.get("ACH_vent_occupied_h_inv", 0.3)),
        "ventilation_type": str(row.get("ventilation_type", "mechanical_extract")),
        "eta_HRV": float(row.get("eta_HRV", 0.0)),
        "selected_archetype_id": str(row["archetype_id"]),
        "value_source": str(row.get("value_source", "merged_archetype_table_v2.csv")),
        **optional_metadata,
    }


def load_source_weather(config: Mapping[str, Any] | None = None) -> TimeSeriesData:
    """Load raw outdoor weather data."""

    config = config or {}
    simulation_cfg = dict(config.get("simulation", {}))
    data_cfg = dict(config.get("data", {}))
    sources_cfg = dict(data_cfg.get("sources", {}))
    weather_cfg = dict(sources_cfg.get("weather", {}))
    forcing_cfg = dict(config.get("forcing", {}))
    fallback_timestamp = str(simulation_cfg.get("start_timestamp", "2023-12-01T01:00:00+01:00"))

    file_dataset = _load_timeseries_from_csv(
        source_name="weather",
        source_cfg=weather_cfg,
        column_mapping={"T_outdoor_C": str(weather_cfg.get("column_mapping", {}).get("T_outdoor_C", "temp_dry_shelter_avg"))},
        fallback_timestamp=fallback_timestamp,
        default_resolution_seconds=int(data_cfg.get("target_resolution_seconds", 3600)),
    )
    if file_dataset is not None:
        return file_dataset

    return _build_source_dataset(
        source_name="weather",
        source_cfg=weather_cfg,
        columns={
            "T_outdoor_C": weather_cfg.get("data", {}).get("T_outdoor_C", [forcing_cfg.get("T_outdoor_C", 5.0)]),
        },
        fallback_timestamp=fallback_timestamp,
        default_resolution_seconds=int(data_cfg.get("target_resolution_seconds", 3600)),
    )


def load_source_load_profiles(config: Mapping[str, Any] | None = None) -> TimeSeriesData:
    """Load raw electric end-use profiles before semantic mapping."""

    config = config or {}
    data_cfg = dict(config.get("data", {}))
    sources_cfg = dict(data_cfg.get("sources", {}))
    load_cfg = dict(sources_cfg.get("load_profiles", {}))
    forcing_cfg = dict(config.get("forcing", {}))
    raw_loads_cfg = dict(forcing_cfg.get("electric_loads_W", {}))

    lcl_dataset = _load_lcl_aggregate_profile(
        source_cfg=load_cfg,
        default_resolution_seconds=int(data_cfg.get("target_resolution_seconds", 3600)),
    )
    if lcl_dataset is not None:
        end_use_cfg = dict(sources_cfg.get("end_use_shares", {}))
        shares = _load_end_use_shares(end_use_cfg)
        total_series = lcl_dataset.columns["total_electricity_W"]
        return TimeSeriesData(
            timestamps=lcl_dataset.timestamps,
            columns={
                "appliances": tuple(float(value) * shares["appliances"] for value in total_series),
                "lighting": tuple(float(value) * shares["lighting"] for value in total_series),
                "cooking": tuple(float(value) * shares["cooking"] for value in total_series),
                "dhw": tuple(float(value) * shares["dhw"] for value in total_series),
                "ev_charging": tuple(0.0 for _ in total_series),
            },
            metadata={
                **lcl_dataset.metadata,
                "disaggregation_source": str(_resolve_path(str(end_use_cfg.get("file_path", ""))) or ""),
                "disaggregation_method": "LCL_mean_times_end_use_shares",
            },
        )

    fallback_timestamp = str(dict(config.get("simulation", {})).get("start_timestamp", "2023-12-01T01:00:00+01:00"))
    return _build_source_dataset(
        source_name="load_profiles",
        source_cfg=load_cfg,
        columns={
            "appliances": load_cfg.get("data", {}).get("appliances", [raw_loads_cfg.get("appliances", 0.0)]),
            "lighting": load_cfg.get("data", {}).get("lighting", [raw_loads_cfg.get("lighting", 0.0)]),
            "cooking": load_cfg.get("data", {}).get("cooking", [raw_loads_cfg.get("cooking", 0.0)]),
            "dhw": load_cfg.get("data", {}).get("dhw"),
            "ev_charging": load_cfg.get("data", {}).get("ev_charging"),
        },
        fallback_timestamp=fallback_timestamp,
        default_resolution_seconds=int(data_cfg.get("target_resolution_seconds", 3600)),
    )


def load_source_internal_gains(config: Mapping[str, Any] | None = None) -> TimeSeriesData:
    """Load raw explicit internal gains that are not already part of electric end uses."""

    config = config or {}
    simulation_cfg = dict(config.get("simulation", {}))
    data_cfg = dict(config.get("data", {}))
    sources_cfg = dict(data_cfg.get("sources", {}))
    internal_cfg = dict(sources_cfg.get("internal_gains", {}))
    fallback_timestamp = str(simulation_cfg.get("start_timestamp", "2023-12-01T01:00:00+01:00"))

    file_dataset = _load_timeseries_from_csv(
        source_name="internal_gains",
        source_cfg=internal_cfg,
        column_mapping={
            "Q_internal_gains_W": str(internal_cfg.get("column_mapping", {}).get("Q_internal_gains_W", "Q_internal_gains_W"))
        },
        fallback_timestamp=fallback_timestamp,
        default_resolution_seconds=int(data_cfg.get("target_resolution_seconds", 3600)),
    )
    if file_dataset is not None:
        return file_dataset

    return _build_source_dataset(
        source_name="internal_gains",
        source_cfg=internal_cfg,
        columns={
            "Q_internal_gains_W": internal_cfg.get("data", {}).get("Q_internal_gains_W", [0.0]),
        },
        fallback_timestamp=fallback_timestamp,
        default_resolution_seconds=int(data_cfg.get("target_resolution_seconds", 3600)),
    )


def load_source_solar(config: Mapping[str, Any] | None = None) -> TimeSeriesData:
    """Load raw solar irradiance or raw solar-derived forcing."""

    config = config or {}
    simulation_cfg = dict(config.get("simulation", {}))
    data_cfg = dict(config.get("data", {}))
    sources_cfg = dict(data_cfg.get("sources", {}))
    solar_cfg = dict(sources_cfg.get("solar", {}))
    fallback_timestamp = str(simulation_cfg.get("start_timestamp", "2023-12-01T01:00:00+01:00"))

    file_dataset = _load_timeseries_from_csv(
        source_name="solar",
        source_cfg=solar_cfg,
        column_mapping={
            "Q_solar_gains_W": str(solar_cfg.get("column_mapping", {}).get("Q_solar_gains_W", "short_wave_from_sky_avg")),
            "I_global_W_m2": str(solar_cfg.get("column_mapping", {}).get("I_global_W_m2", "")),
            "I_solar_W_m2": str(solar_cfg.get("column_mapping", {}).get("I_solar_W_m2", "")),
        },
        fallback_timestamp=fallback_timestamp,
        default_resolution_seconds=int(data_cfg.get("target_resolution_seconds", 3600)),
    )
    if file_dataset is not None:
        solar_gain_scale = float(solar_cfg.get("gain_scale", 1.0))
        columns = {
            column_name: tuple(float(value or 0.0) for value in values)
            for column_name, values in file_dataset.columns.items()
            if any(value is not None for value in values)
        }
        if "Q_solar_gains_W" in columns:
            columns["Q_solar_gains_W"] = tuple(float(value * solar_gain_scale) for value in columns["Q_solar_gains_W"])
        return TimeSeriesData(
            timestamps=file_dataset.timestamps,
            columns=columns,
            metadata={**file_dataset.metadata, "solar_gain_scale": solar_gain_scale, "solar_mode": "direct_q_solar"},
        )

    raw_dir = _resolve_path(str(solar_cfg.get("raw_dir", "")))
    if raw_dir is not None and raw_dir.exists():
        frame = _load_pvgis_unified_frame(str(raw_dir))
        return _build_source_from_frame(
            source_name="solar",
            frame=frame,
            timestamp_column="timestamp",
            metadata={
                "source_name": "solar",
                "original_timestep_seconds": int(solar_cfg.get("original_timestep_seconds", 3600)),
                "original_resolution": int(solar_cfg.get("original_timestep_seconds", 3600)),
                "input_file_path": str(raw_dir),
                "solar_mode": "pvgis_raw_facades",
            },
        )

    return _build_source_dataset(
        source_name="solar",
        source_cfg=solar_cfg,
        columns={
            "Q_solar_gains_W": solar_cfg.get("data", {}).get("Q_solar_gains_W", [0.0]),
        },
        fallback_timestamp=fallback_timestamp,
        default_resolution_seconds=int(data_cfg.get("target_resolution_seconds", 3600)),
    )


def load_building_inputs(config: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Load file-backed building inputs when available."""

    return _load_building_from_archetype(config=config)
