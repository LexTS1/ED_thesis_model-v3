"""Column contract for scenario-tree standardized summary outputs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


REQUIRED_METADATA_COLUMNS = [
    "scenario_leaf_id",
    "scenario_id",
    "climate_window_id",
    "climate_pathway_id",
    "technology_case_id",
    "realization_id",
    "seed_index",
    "seed_value",
    "cohort_size",
    "analysis_start",
    "analysis_end",
    "source_file_window",
    "run_status",
    "run_attempt_id",
    "run_timestamp_utc",
    "config_hash_sha256",
    "climate_forcing_file",
    "technology_inputs_file",
    "raw_outputs_dir",
]

REQUIRED_METRIC_COLUMNS = [
    "annual_electricity_gross_kWh",
    "annual_grid_import_kWh",
    "annual_grid_export_kWh",
    "annual_gas_kWh",
    "annual_useful_heating_kWh",
    "annual_dhw_kWh",
    "peak_grid_import_W",
    "winter_peak_grid_import_W",
    "summer_peak_grid_import_W",
    "pv_generation_kWh",
    "pv_self_consumption_kWh",
    "pv_export_fraction",
    "ev_charging_kWh",
    "mean_T_out_C",
    "winter_mean_T_out_C",
    "summer_mean_T_out_C",
    "HDD_15",
    "HDD_18",
    "CDD_22",
    "mean_solar_W_m2",
]

DIAGNOSTIC_COLUMNS = [
    "missing_metric_count",
    "missing_metrics",
    "pv_metric_policy",
    "ev_metric_policy",
    "gas_metric_policy",
    "heating_metric_policy",
    "climate_temperature_column",
    "climate_solar_column",
    "climate_included_years",
    "climate_includes_2050",
    "raw_output_files_used",
    "raw_output_columns_used",
]

SUMMARY_COLUMNS = REQUIRED_METADATA_COLUMNS + REQUIRED_METRIC_COLUMNS + DIAGNOSTIC_COLUMNS

BASELINE_SCENARIO_ID = "baseline_1981_2005__historical__tech_current_stock"
BASELINE_LEAF_PREFIX = f"{BASELINE_SCENARIO_ID}__"


_UNITS_BY_COLUMN = {
    "annual_electricity_gross_kWh": "kWh",
    "annual_grid_import_kWh": "kWh",
    "annual_grid_export_kWh": "kWh",
    "annual_gas_kWh": "kWh",
    "annual_useful_heating_kWh": "kWh",
    "annual_dhw_kWh": "kWh",
    "peak_grid_import_W": "W",
    "winter_peak_grid_import_W": "W",
    "summer_peak_grid_import_W": "W",
    "pv_generation_kWh": "kWh",
    "pv_self_consumption_kWh": "kWh",
    "pv_export_fraction": "fraction",
    "ev_charging_kWh": "kWh",
    "mean_T_out_C": "C",
    "winter_mean_T_out_C": "C",
    "summer_mean_T_out_C": "C",
    "HDD_15": "degree_days",
    "HDD_18": "degree_days",
    "CDD_22": "degree_days",
    "mean_solar_W_m2": "W/m2",
}

_DESCRIPTIONS_BY_COLUMN = {
    "annual_electricity_gross_kWh": "Gross annual electricity demand before netting PV generation.",
    "annual_grid_import_kWh": "Annual electricity imported from the grid.",
    "annual_grid_export_kWh": "Annual electricity exported to the grid.",
    "annual_gas_kWh": "Annual natural-gas final energy consumption.",
    "annual_useful_heating_kWh": "Useful thermal energy supplied for space heating.",
    "annual_dhw_kWh": "Useful thermal domestic hot-water demand when available.",
    "peak_grid_import_W": "Maximum grid import power over the model output year.",
    "winter_peak_grid_import_W": "Maximum grid import power in December, January, or February.",
    "summer_peak_grid_import_W": "Maximum grid import power in June, July, or August.",
    "pv_generation_kWh": "Annual PV generation.",
    "pv_self_consumption_kWh": "Annual PV generation consumed locally.",
    "pv_export_fraction": "Grid export divided by PV generation when PV generation is positive.",
    "ev_charging_kWh": "Annual EV charging electricity.",
    "mean_T_out_C": "Mean outdoor air temperature over the canonical climate window.",
    "winter_mean_T_out_C": "Mean outdoor air temperature in December, January, and February.",
    "summer_mean_T_out_C": "Mean outdoor air temperature in June, July, and August.",
    "HDD_15": "Heating degree days using a 15 C base and daily mean outdoor temperature.",
    "HDD_18": "Heating degree days using an 18 C base and daily mean outdoor temperature.",
    "CDD_22": "Cooling degree days using a 22 C base and daily mean outdoor temperature.",
    "mean_solar_W_m2": "Mean available solar irradiance over the canonical climate window.",
}


def column_schema() -> list[dict[str, Any]]:
    """Return the machine-readable schema for standardized leaf metrics."""

    schema: list[dict[str, Any]] = []
    for column in SUMMARY_COLUMNS:
        is_metric = column in REQUIRED_METRIC_COLUMNS
        schema.append(
            {
                "name": column,
                "unit": _UNITS_BY_COLUMN.get(column, ""),
                "description": _DESCRIPTIONS_BY_COLUMN.get(column, column.replace("_", " ")),
                "required": column in REQUIRED_METADATA_COLUMNS or column in REQUIRED_METRIC_COLUMNS,
                "aggregation_policy": "numeric_distribution" if is_metric else "grouping_or_diagnostic",
            }
        )
    return schema


def write_schema(path: Path) -> Path:
    """Write the standardized summary schema YAML."""

    payload = {
        "schema_version": "model_v3.scenario_tree.standardized_leaf_metrics.v1",
        "columns": column_schema(),
    }
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with Path(path).open("w", encoding="utf-8") as handle:
        yaml.safe_dump(payload, handle, sort_keys=False)
    return Path(path)
