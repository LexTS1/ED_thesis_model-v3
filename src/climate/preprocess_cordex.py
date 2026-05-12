"""Preprocess downloaded CORDEX NetCDF/ZIP chunks into model forcing CSVs."""

from __future__ import annotations

import argparse
import json
import logging
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    import xarray as xr

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = Path(__file__).with_name("climate_config.yaml")

LOGGER = logging.getLogger("climate.preprocess_cordex")


def setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def repo_path(path_value: str | Path) -> Path:
    path = Path(path_value)
    if path.is_absolute():
        return path
    return REPO_ROOT / path


def relative_path(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def load_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        config = yaml.safe_load(handle)
    if not isinstance(config, dict):
        raise ValueError(f"Climate config is empty or invalid: {path}")
    return config


def configured_groups(
    config: dict[str, Any], window_filter: str | None, scenario_filter: str | None
) -> list[tuple[str, str]]:
    groups: list[tuple[str, str]] = []
    for scenario, scenario_config in config["scenarios"].items():
        if scenario_filter and scenario != scenario_filter:
            continue
        for window in scenario_config.get("windows", []):
            if window_filter and window != window_filter:
                continue
            groups.append((window, scenario))
    return groups


def deterministic_weather_filename(
    window: str,
    scenario: str,
    model_chain: dict[str, str],
) -> str:
    return (
        f"weather_{window}_{scenario}_{model_chain['gcm_model']}_"
        f"{model_chain['rcm_model']}_{model_chain['ensemble_member']}.csv"
    )


def safe_extract_zip(zip_path: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    with zipfile.ZipFile(zip_path) as archive:
        for member in archive.infolist():
            member_path = (destination / member.filename).resolve()
            if root not in member_path.parents and member_path != root:
                raise ValueError(f"Unsafe path in ZIP archive {zip_path}: {member.filename}")
            archive.extract(member, destination)


def is_netcdf_path(path: Path) -> bool:
    return path.suffix.lower() in {".nc", ".nc4", ".cdf", ".netcdf"}


def collect_netcdf_files(raw_dir: Path, extraction_root: Path) -> tuple[list[Path], list[Path]]:
    direct_netcdf = sorted(path for path in raw_dir.rglob("*") if path.is_file() and is_netcdf_path(path))
    zip_files = sorted(raw_dir.glob("*.zip"))
    extracted_netcdf: list[Path] = []

    for zip_path in zip_files:
        destination = extraction_root / zip_path.stem
        existing = sorted(
            path for path in destination.rglob("*") if path.is_file() and is_netcdf_path(path)
        )
        if not existing:
            LOGGER.info("Extracting %s to %s", relative_path(zip_path), relative_path(destination))
            safe_extract_zip(zip_path, destination)
            existing = sorted(
                path for path in destination.rglob("*") if path.is_file() and is_netcdf_path(path)
            )
        if not existing:
            LOGGER.warning("No NetCDF files found after extracting %s", relative_path(zip_path))
        extracted_netcdf.extend(existing)

    netcdf_files = sorted({*direct_netcdf, *extracted_netcdf})
    return netcdf_files, zip_files


def find_time_name(dataset: xr.Dataset) -> str:
    if "time" in dataset.coords or "time" in dataset.dims:
        return "time"
    for name, coord in dataset.coords.items():
        standard_name = str(coord.attrs.get("standard_name", "")).lower()
        axis = str(coord.attrs.get("axis", "")).lower()
        if standard_name == "time" or axis == "t":
            return name
    raise ValueError("Could not identify a time coordinate in the CORDEX dataset.")


def clip_to_window(dataset: xr.Dataset, time_name: str, window_config: dict[str, Any]) -> xr.Dataset:
    start = str(window_config["start"])
    end = str(window_config["end"])
    try:
        clipped = dataset.sel({time_name: slice(start, end)})
    except Exception:
        time = dataset[time_name]
        mask = (time.dt.year >= int(window_config["start_year"])) & (
            time.dt.year <= int(window_config["end_year"])
        )
        clipped = dataset.where(mask, drop=True)

    if clipped.sizes.get(time_name, 0) == 0:
        raise ValueError(f"No time steps remain after clipping to {start} through {end}.")
    return clipped


def find_lat_lon_names(dataset: xr.Dataset) -> tuple[str, str]:
    lat_candidates = ("lat", "latitude", "nav_lat", "y_lat")
    lon_candidates = ("lon", "longitude", "nav_lon", "x_lon")

    lat_name = next((name for name in lat_candidates if name in dataset.variables), None)
    lon_name = next((name for name in lon_candidates if name in dataset.variables), None)

    if lat_name and lon_name:
        return lat_name, lon_name

    for name, variable in dataset.variables.items():
        standard_name = str(variable.attrs.get("standard_name", "")).lower()
        axis = str(variable.attrs.get("axis", "")).lower()
        units = str(variable.attrs.get("units", "")).lower()
        if not lat_name and (
            standard_name == "latitude" or axis == "y" or units in {"degrees_north", "degree_north"}
        ):
            lat_name = name
        if not lon_name and (
            standard_name == "longitude" or axis == "x" or units in {"degrees_east", "degree_east"}
        ):
            lon_name = name

    if not lat_name or not lon_name:
        raise ValueError("Could not identify latitude/longitude coordinates in the dataset.")
    return lat_name, lon_name


def longitude_delta(lon: xr.DataArray, target_lon: float) -> xr.DataArray:
    return ((lon - target_lon + 180.0) % 360.0) - 180.0


def nearest_point_indexers(dataset: xr.Dataset, target_lat: float, target_lon: float) -> dict[str, int]:
    import numpy as np

    lat_name, lon_name = find_lat_lon_names(dataset)
    lat = dataset[lat_name]
    lon = dataset[lon_name]
    distance = (lat - target_lat) ** 2 + longitude_delta(lon, target_lon) ** 2
    distance_values = distance.values

    if not np.isfinite(distance_values).any():
        raise ValueError("Latitude/longitude grid contains no finite points for nearest selection.")

    flat_index = int(np.nanargmin(np.where(np.isfinite(distance_values), distance_values, np.inf)))
    multi_index = np.unravel_index(flat_index, distance_values.shape)
    return {dimension: int(index) for dimension, index in zip(distance.dims, multi_index)}


def extract_nearest_point(
    dataset: xr.Dataset, target_lat: float, target_lon: float
) -> tuple[xr.Dataset, dict[str, Any]]:
    lat_name, lon_name = find_lat_lon_names(dataset)
    lat = dataset[lat_name]
    lon = dataset[lon_name]
    indexers = nearest_point_indexers(dataset, target_lat, target_lon)
    selected = dataset.isel(indexers)

    lat_indexers = {dimension: value for dimension, value in indexers.items() if dimension in lat.dims}
    lon_indexers = {dimension: value for dimension, value in indexers.items() if dimension in lon.dims}
    actual_lat = float(lat.isel(lat_indexers).values)
    actual_lon = float(lon.isel(lon_indexers).values)
    return selected, {
        "method": "nearest_point",
        "target_lat": target_lat,
        "target_lon": target_lon,
        "selected_lat": actual_lat,
        "selected_lon": actual_lon,
        "selected_indices": indexers,
        "lat_name": lat_name,
        "lon_name": lon_name,
    }


def extract_belgium_box_mean(
    dataset: xr.Dataset, box_config: dict[str, float]
) -> tuple[xr.Dataset, dict[str, Any]]:
    lat_name, lon_name = find_lat_lon_names(dataset)
    lat = dataset[lat_name]
    lon = dataset[lon_name]
    mask = (
        (lat >= float(box_config["lat_min"]))
        & (lat <= float(box_config["lat_max"]))
        & (longitude_delta(lon, float(box_config["lon_min"])) >= 0.0)
        & (longitude_delta(lon, float(box_config["lon_max"])) <= 0.0)
    )

    selected_count = int(mask.sum().values)
    if selected_count == 0:
        raise ValueError(f"Belgium box selected no grid cells: {box_config}")

    spatial_dims = list(mask.dims)
    selected = dataset.where(mask).mean(dim=spatial_dims, skipna=True)
    return selected, {
        "method": "belgium_box_mean",
        "box": box_config,
        "selected_grid_cells": selected_count,
        "lat_name": lat_name,
        "lon_name": lon_name,
        "averaged_dims": spatial_dims,
    }


def apply_spatial_extraction(
    dataset: xr.Dataset,
    config: dict[str, Any],
    spatial_method: str | None,
) -> tuple[xr.Dataset, dict[str, Any]]:
    spatial = config["spatial"]
    method = spatial_method or spatial.get("method", "nearest_point")

    if method == "nearest_point":
        return extract_nearest_point(
            dataset,
            target_lat=float(spatial["target_lat"]),
            target_lon=float(spatial["target_lon"]),
        )
    if method == "belgium_box_mean":
        return extract_belgium_box_mean(dataset, spatial["belgium_box"])
    raise ValueError(f"Unsupported spatial extraction method: {method}")


def data_var_names(dataset: xr.Dataset) -> list[str]:
    return list(dataset.data_vars)


def find_variable(dataset: xr.Dataset, aliases: list[str], semantic: str, time_name: str) -> str:
    names = data_var_names(dataset)
    lower_to_name = {name.lower(): name for name in names}

    for alias in aliases:
        match = lower_to_name.get(alias.lower())
        if match and time_name in dataset[match].dims:
            return match

    for name in names:
        variable = dataset[name]
        if time_name not in variable.dims:
            continue
        attrs = {key: str(value).lower() for key, value in variable.attrs.items()}
        standard_name = attrs.get("standard_name", "")
        long_name = attrs.get("long_name", "")
        units = attrs.get("units", "")
        lower_name = name.lower()

        if semantic == "temperature":
            if standard_name == "air_temperature":
                return name
            if "temperature" in long_name and ("2m" in long_name or "air" in long_name):
                return name
            if lower_name.startswith("tas") and units in {"k", "kelvin"}:
                return name

        if semantic == "solar_radiation":
            if standard_name == "surface_downwelling_shortwave_flux_in_air":
                return name
            if ("solar" in long_name and "down" in long_name) or "shortwave" in long_name:
                return name
            if lower_name.startswith("rsds"):
                return name

    raise ValueError(
        f"Could not identify {semantic!r} variable. Available data variables: {names}"
    )


def likely_forcing_data_vars(dataset: xr.Dataset, config: dict[str, Any], time_name: str) -> list[str]:
    aliases = config.get("variable_aliases", {})
    variables: list[str] = []
    for semantic, fallback_aliases in (
        ("temperature", ["tas", "2m_air_temperature"]),
        ("solar_radiation", ["rsds", "surface_solar_radiation_downwards"]),
    ):
        try:
            variable = find_variable(
                dataset,
                aliases.get(semantic, fallback_aliases),
                semantic,
                time_name,
            )
        except ValueError:
            continue
        variables.append(variable)
    return variables


def make_open_preprocessor(
    config: dict[str, Any],
    spatial_method: str | None,
    preselected_indexers: dict[str, int] | None = None,
):
    """Subset each NetCDF file before xarray combines the multi-file dataset."""

    method = spatial_method or config["spatial"].get("method", "nearest_point")
    spatial = config["spatial"]

    def preprocess(dataset: xr.Dataset) -> xr.Dataset:
        time_name = find_time_name(dataset)
        keep_vars = likely_forcing_data_vars(dataset, config, time_name)
        if keep_vars:
            dataset = dataset[keep_vars]

        if method == "nearest_point":
            indexers = preselected_indexers or nearest_point_indexers(
                dataset,
                target_lat=float(spatial["target_lat"]),
                target_lon=float(spatial["target_lon"]),
            )
            usable_indexers = {
                dimension: index
                for dimension, index in indexers.items()
                if dimension in dataset.dims
            }
            return dataset.isel(usable_indexers, drop=True)

        return dataset

    return preprocess


def preselect_spatial_metadata(
    netcdf_file: Path,
    config: dict[str, Any],
    spatial_method: str | None,
) -> tuple[dict[str, int] | None, dict[str, Any] | None]:
    method = spatial_method or config["spatial"].get("method", "nearest_point")
    if method != "nearest_point":
        return None, None

    import xarray as xr

    spatial = config["spatial"]
    with xr.open_dataset(netcdf_file, decode_times=False) as dataset:
        lat_name, lon_name = find_lat_lon_names(dataset)
        lat = dataset[lat_name]
        lon = dataset[lon_name]
        indexers = nearest_point_indexers(
            dataset,
            target_lat=float(spatial["target_lat"]),
            target_lon=float(spatial["target_lon"]),
        )
        lat_indexers = {
            dimension: value for dimension, value in indexers.items() if dimension in lat.dims
        }
        lon_indexers = {
            dimension: value for dimension, value in indexers.items() if dimension in lon.dims
        }
        spatial_info = {
            "method": "nearest_point",
            "target_lat": float(spatial["target_lat"]),
            "target_lon": float(spatial["target_lon"]),
            "selected_lat": float(lat.isel(lat_indexers).values),
            "selected_lon": float(lon.isel(lon_indexers).values),
            "selected_indices": indexers,
            "lat_name": lat_name,
            "lon_name": lon_name,
            "preselected_before_combine": True,
        }
    return indexers, spatial_info


def normalized_units(units: str) -> str:
    replacements = str.maketrans({"²": "2", "−": "-", "⁻": "-", "·": " "})
    return (
        units.lower()
        .translate(replacements)
        .replace(" ", "")
        .replace("_", "")
        .replace("per", "/")
    )


def scalar_stat(data_array: xr.DataArray, stat: str) -> float:
    result = getattr(data_array, stat)(skipna=True)
    return float(result.compute().values)


def convert_temperature_to_celsius(data_array: xr.DataArray) -> tuple[xr.DataArray, dict[str, Any]]:
    units = str(data_array.attrs.get("units", "")).strip()
    norm = normalized_units(units)
    conversion = "unknown_units_kept_as_celsius"

    if norm in {"k", "kelvin"}:
        converted = data_array - 273.15
        conversion = "kelvin_to_celsius"
    elif norm in {"degc", "degreecelsius", "degreescelsius", "c", "celsius"}:
        converted = data_array
        conversion = "already_celsius"
    else:
        median_value = scalar_stat(data_array, "median")
        if median_value > 100.0:
            converted = data_array - 273.15
            conversion = "inferred_kelvin_to_celsius"
            LOGGER.warning(
                "Temperature units %r are not recognized; median %.2f suggests Kelvin.",
                units,
                median_value,
            )
        else:
            converted = data_array
            LOGGER.warning(
                "Temperature units %r are not recognized; keeping values as Celsius.",
                units,
            )

    LOGGER.info("Temperature conversion for %s: %s", data_array.name, conversion)
    return converted, {
        "input_units": units,
        "output_units": "degC",
        "conversion": conversion,
    }


def convert_radiation_to_w_m2(data_array: xr.DataArray) -> tuple[xr.DataArray, dict[str, Any]]:
    units = str(data_array.attrs.get("units", "")).strip()
    norm = normalized_units(units)

    watt_tokens = ("wm-2", "wm^-2", "wm**-2", "w/m2", "w/m^2", "w/m**2", "watt")
    joule_tokens = ("jm-2", "jm^-2", "jm**-2", "j/m2", "j/m^2", "j/m**2", "joule")

    if any(token in norm for token in watt_tokens):
        decision = "kept_w_m2"
        converted = data_array
    elif any(token in norm for token in joule_tokens):
        decision = "daily_accumulation_j_m2_divided_by_86400"
        converted = data_array / 86400.0
    else:
        raise ValueError(
            f"Solar radiation variable {data_array.name!r} has unsupported units {units!r}. "
            "Expected W m-2 or daily accumulated J m-2."
        )

    LOGGER.info(
        "Radiation conversion for %s: units=%r decision=%s",
        data_array.name,
        units,
        decision,
    )
    return converted, {
        "input_units": units,
        "output_units": "W m-2",
        "conversion": decision,
    }


def squeeze_to_time_series(data_array: xr.DataArray, time_name: str, output_name: str) -> xr.DataArray:
    squeezed = data_array.squeeze(drop=True)
    extra_dims = [dimension for dimension in squeezed.dims if dimension != time_name]
    if extra_dims:
        raise ValueError(
            f"Variable {data_array.name!r} still has non-time dimensions after spatial "
            f"extraction: {extra_dims}"
        )
    return squeezed.rename(output_name)


def format_timestamp(value: Any) -> str:
    import numpy as np
    import pandas as pd

    if isinstance(value, np.datetime64):
        return pd.Timestamp(value).isoformat()
    if hasattr(value, "isoformat"):
        return str(value.isoformat())
    if all(hasattr(value, attr) for attr in ("year", "month", "day")):
        hour = getattr(value, "hour", 0)
        minute = getattr(value, "minute", 0)
        second = getattr(value, "second", 0)
        return (
            f"{int(value.year):04d}-{int(value.month):02d}-{int(value.day):02d}"
            f"T{int(hour):02d}:{int(minute):02d}:{int(second):02d}"
        )
    return str(value)


def source_file_value(source_files: list[Path]) -> str:
    return ";".join(relative_path(path) for path in source_files)


def write_csv_and_metadata(
    dataset: xr.Dataset,
    netcdf_files: list[Path],
    source_files: list[Path],
    output_csv: Path,
    config: dict[str, Any],
    window: str,
    scenario: str,
    spatial_info: dict[str, Any],
) -> None:
    import numpy as np
    import pandas as pd

    time_name = find_time_name(dataset)
    aliases = config.get("variable_aliases", {})
    temperature_name = find_variable(
        dataset,
        aliases.get("temperature", ["tas", "2m_air_temperature"]),
        "temperature",
        time_name,
    )
    radiation_name = find_variable(
        dataset,
        aliases.get("solar_radiation", ["rsds", "surface_solar_radiation_downwards"]),
        "solar_radiation",
        time_name,
    )

    temperature_c, temperature_metadata = convert_temperature_to_celsius(dataset[temperature_name])
    radiation_w_m2, radiation_metadata = convert_radiation_to_w_m2(dataset[radiation_name])
    temperature_series = squeeze_to_time_series(temperature_c, time_name, "T_out_C")
    radiation_series = squeeze_to_time_series(radiation_w_m2, time_name, "I_solar_W_m2")

    time_values = dataset[time_name].values
    model_chain = config["model_chain"]
    source_value = source_file_value(source_files)
    frame = pd.DataFrame(
        {
            "timestamp": [format_timestamp(value) for value in time_values],
            "T_out_C": np.asarray(temperature_series.values, dtype=float),
            "I_solar_W_m2": np.asarray(radiation_series.values, dtype=float),
            "scenario": scenario,
            "window": window,
            "gcm_model": model_chain["gcm_model"],
            "rcm_model": model_chain["rcm_model"],
            "ensemble_member": model_chain["ensemble_member"],
            "source_files": source_value,
        }
    )
    frame = frame.sort_values("timestamp").reset_index(drop=True)

    output_csv.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(output_csv, index=False)

    metadata_path = output_csv.with_suffix(".metadata.json")
    metadata = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "dataset": config["dataset"],
        "window": window,
        "scenario": scenario,
        "model_chain": model_chain,
        "row_count": int(len(frame)),
        "time_start": str(frame["timestamp"].iloc[0]) if not frame.empty else None,
        "time_end": str(frame["timestamp"].iloc[-1]) if not frame.empty else None,
        "time_calendar": str(
            dataset[time_name].encoding.get("calendar")
            or dataset[time_name].attrs.get("calendar", "")
        ),
        "spatial_extraction": spatial_info,
        "temperature": {"variable": temperature_name, **temperature_metadata},
        "radiation": {"variable": radiation_name, **radiation_metadata},
        "netcdf_files": [relative_path(path) for path in netcdf_files],
        "source_files": [relative_path(path) for path in source_files],
        "csv_schema": list(frame.columns),
    }
    metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8")
    LOGGER.info("Wrote %s and %s", relative_path(output_csv), relative_path(metadata_path))


def preprocess_group(
    config: dict[str, Any],
    window: str,
    scenario: str,
    spatial_method: str | None,
    overwrite: bool,
) -> bool:
    raw_root = repo_path(config["paths"]["raw_root"])
    extraction_root = repo_path(config["paths"].get("extraction_root", "inputs/climate/raw/_extracted"))
    processed_root = repo_path(config["paths"]["processed_root"])
    model_chain = config["model_chain"]
    raw_dir = raw_root / window / scenario
    output_csv = (
        processed_root
        / window
        / deterministic_weather_filename(window, scenario, model_chain)
    )

    if output_csv.exists() and not overwrite:
        LOGGER.info("Skipping existing processed file: %s", relative_path(output_csv))
        return True

    if not raw_dir.exists():
        LOGGER.debug("Raw directory does not exist: %s", relative_path(raw_dir))
        return False

    netcdf_files, zip_files = collect_netcdf_files(raw_dir, extraction_root / window / scenario)
    if not netcdf_files:
        LOGGER.debug("No raw NetCDF/ZIP files found for %s/%s", window, scenario)
        return False

    source_files = zip_files if zip_files else netcdf_files
    LOGGER.info(
        "Opening %s NetCDF file(s) for %s/%s",
        len(netcdf_files),
        window,
        scenario,
    )

    try:
        import xarray as xr
    except ImportError as exc:
        raise RuntimeError(
            "Missing dependency `xarray`. Install the climate pipeline dependencies with "
            "`pip install -r requirements.txt` before preprocessing raw CORDEX files."
        ) from exc

    preselected_indexers, preselected_spatial_info = preselect_spatial_metadata(
        netcdf_files[0],
        config,
        spatial_method,
    )
    dataset = xr.open_mfdataset(
        [str(path) for path in netcdf_files],
        combine="by_coords",
        preprocess=make_open_preprocessor(config, spatial_method, preselected_indexers),
        data_vars="minimal",
        coords="minimal",
        compat="override",
    )
    try:
        time_name = find_time_name(dataset)
        dataset = dataset.sortby(time_name)
        dataset = clip_to_window(dataset, time_name, config["climate_windows"][window])
        if preselected_spatial_info:
            spatial_info = preselected_spatial_info
        else:
            dataset, spatial_info = apply_spatial_extraction(dataset, config, spatial_method)
        write_csv_and_metadata(
            dataset=dataset,
            netcdf_files=netcdf_files,
            source_files=source_files,
            output_csv=output_csv,
            config=config,
            window=window,
            scenario=scenario,
            spatial_info=spatial_info,
        )
    finally:
        dataset.close()
    return True


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Preprocess downloaded CORDEX climate chunks into forcing CSVs."
    )
    parser.add_argument("--window", help="Limit preprocessing to one climate window.")
    parser.add_argument("--scenario", help="Limit preprocessing to one scenario.")
    parser.add_argument(
        "--spatial-method",
        choices=["nearest_point", "belgium_box_mean"],
        help="Override spatial extraction method from the YAML config.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing processed CSV and metadata files.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=CONFIG_PATH,
        help="Path to the climate YAML config.",
    )
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    setup_logging(args.verbose)

    try:
        config = load_config(args.config)
        groups = configured_groups(config, args.window, args.scenario)
    except Exception as exc:
        LOGGER.error("Failed to load preprocessing config: %s", exc)
        return 2

    if not groups:
        LOGGER.error(
            "No configured window/scenario groups matched filters. window=%r scenario=%r",
            args.window,
            args.scenario,
        )
        return 1

    processed_count = 0
    failed_count = 0
    for window, scenario in groups:
        try:
            if preprocess_group(config, window, scenario, args.spatial_method, args.overwrite):
                processed_count += 1
        except Exception:
            failed_count += 1
            LOGGER.exception("Preprocessing failed for %s/%s", window, scenario)

    if processed_count == 0:
        LOGGER.error(
            "No raw CORDEX ZIP/NetCDF files were found under %s. "
            "Run the downloader first, for example: "
            "python -m src.climate.download_cordex --window baseline --scenario historical",
            relative_path(repo_path(config["paths"]["raw_root"])),
        )
        return 1

    LOGGER.info(
        "Preprocessing finished: %s group(s) processed or skipped, %s failed.",
        processed_count,
        failed_count,
    )
    return 1 if failed_count else 0


if __name__ == "__main__":
    sys.exit(main())
