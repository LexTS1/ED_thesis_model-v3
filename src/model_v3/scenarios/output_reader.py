"""Raw scenario-leaf output reader and metric adapter."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

import pandas as pd
import yaml

from model_v3.scenarios.summary_contract import REQUIRED_METRIC_COLUMNS
from model_v3.systems.distributed_energy import value_from_range
from model_v3.utils.energy import infer_step_durations_seconds, integrate_power_series_kwh


REPO_ROOT = Path(__file__).resolve().parents[3]

KNOWN_OUTPUT_FILES = {
    "timeseries": "timeseries.csv",
    "annual_profile": "annual_profile.csv",
    "annual_summary": "annual_summary.json",
    "annual_summary_yaml": "annual_summary.yaml",
    "summary": "summary.yaml",
    "metrics": "metrics.yaml",
    "annual_summary_csv": "annual_summary.csv",
    "end_use_summary": "end_use_summary.csv",
    "grid_summary": "grid_summary.csv",
    "pv_summary": "pv_summary.csv",
    "ev_summary": "ev_summary.csv",
}


class OutputReaderError(RuntimeError):
    """Raised when raw model outputs cannot be standardized."""


class MissingRequiredOutputError(OutputReaderError):
    """Raised when a required standardized metric is missing."""

    def __init__(self, missing_metrics: Iterable[str], details: Iterable[str] | None = None):
        self.missing_metrics = sorted(set(missing_metrics))
        self.details = list(details or [])
        message = "Missing required output metric(s): " + ", ".join(self.missing_metrics)
        if self.details:
            message = f"{message}. Details: {'; '.join(self.details)}"
        super().__init__(message)


@dataclass
class OutputMetricResult:
    """Standardized raw-output metric payload plus diagnostics."""

    metrics: dict[str, float]
    missing_metrics: list[str] = field(default_factory=list)
    policies: dict[str, str] = field(default_factory=dict)
    raw_output_files_used: list[str] = field(default_factory=list)
    raw_output_columns_used: dict[str, str] = field(default_factory=dict)
    assumptions: list[str] = field(default_factory=list)


def _resolve_repo_path(path_text: str | Path, repo_root: Path = REPO_ROOT) -> Path:
    path = Path(path_text)
    if path.is_absolute():
        return path
    return repo_root / path


def _load_yaml(path: Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict):
        raise OutputReaderError(f"YAML output must contain a mapping: {path}")
    return data


def _load_json(path: Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise OutputReaderError(f"JSON output must contain a mapping: {path}")
    return data


def discover_output_files(outputs_dir: Path) -> dict[str, Path]:
    """Return known output files present in one scenario leaf output directory."""

    root = Path(outputs_dir)
    discovered: dict[str, Path] = {}
    for key, file_name in KNOWN_OUTPUT_FILES.items():
        path = root / file_name
        if path.exists():
            discovered[key] = path
    return discovered


def read_leaf_outputs(outputs_dir: Path) -> dict[str, Any]:
    """Read raw output files for one scenario leaf."""

    files = discover_output_files(outputs_dir)
    if not files:
        raise OutputReaderError(f"No recognized raw output files found in {outputs_dir}")

    raw: dict[str, Any] = {"files": files}
    for key in ("timeseries", "annual_profile"):
        if key in files:
            raw[key] = pd.read_csv(files[key])
    for key in ("annual_summary_csv", "end_use_summary", "grid_summary", "pv_summary", "ev_summary"):
        if key in files:
            raw[key] = pd.read_csv(files[key])
    for key in ("annual_summary",):
        if key in files:
            raw[key] = _load_json(files[key])
    for key in ("annual_summary_yaml", "summary", "metrics"):
        if key in files:
            raw[key] = _load_yaml(files[key])
    return raw


def _first_frame(raw_outputs: Mapping[str, Any]) -> pd.DataFrame | None:
    for key in ("timeseries", "annual_profile"):
        value = raw_outputs.get(key)
        if isinstance(value, pd.DataFrame):
            return value
    return None


def _summary_dicts(raw_outputs: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    summaries: list[Mapping[str, Any]] = []
    for key in ("annual_summary", "annual_summary_yaml", "summary", "metrics"):
        value = raw_outputs.get(key)
        if isinstance(value, Mapping):
            summaries.append(value)
    return summaries


def _get_nested(mapping: Mapping[str, Any], path: tuple[str, ...]) -> Any:
    current: Any = mapping
    for key in path:
        if not isinstance(current, Mapping) or key not in current:
            return None
        current = current[key]
    return current


def _summary_value(raw_outputs: Mapping[str, Any], paths: Iterable[tuple[str, ...]]) -> float | None:
    value, _source = _summary_value_with_source(raw_outputs, paths)
    return value


def _summary_value_with_source(
    raw_outputs: Mapping[str, Any],
    paths: Iterable[tuple[str, ...]],
) -> tuple[float | None, str | None]:
    for summary in _summary_dicts(raw_outputs):
        for path in paths:
            value = _get_nested(summary, path)
            if value is None or value == "":
                continue
            try:
                number = float(value)
            except (TypeError, ValueError):
                continue
            if math.isfinite(number):
                return number, ".".join(path)
    return None, None


def _technology_case(run_config: Mapping[str, Any], repo_root: Path = REPO_ROOT) -> dict[str, Any]:
    technology_cfg = dict(run_config.get("technology", {}))
    metadata_file = technology_cfg.get("metadata_file")
    if not metadata_file:
        return {}
    metadata_path = _resolve_repo_path(str(metadata_file), repo_root)
    if not metadata_path.exists():
        return {}
    metadata = _load_yaml(metadata_path)
    cases = dict(metadata.get("technology_cases", {}))
    return dict(cases.get(str(technology_cfg.get("case_id", "")), {}))


def _technology_flags(run_config: Mapping[str, Any]) -> dict[str, bool]:
    case = _technology_case(run_config)
    assignment = dict(case.get("household_assignment", {}))
    pv_probability = value_from_range(dict(assignment.get("pv", {})).get("household_probability"), 0.0)
    ev_probability = value_from_range(dict(assignment.get("ev", {})).get("household_probability"), 0.0)
    return {
        "pv_assumed": bool(case.get("pv_assumed", False)) or pv_probability > 0.0,
        "ev_assumed": bool(case.get("ev_adoption_assumed", False)) or ev_probability > 0.0,
        "heat_pump_assumed": bool(case.get("heat_pump_adoption_assumed", False)),
    }


def _timestamp_values(frame: pd.DataFrame) -> list[pd.Timestamp]:
    if "timestamp" not in frame.columns:
        raise OutputReaderError("Raw output timeseries must contain a timestamp column for integration and seasonal peaks.")
    return [pd.Timestamp(value) for value in frame["timestamp"]]


def _timestamp_months(frame: pd.DataFrame) -> pd.Series:
    if "timestamp" not in frame.columns:
        raise OutputReaderError("Raw output timeseries must contain a timestamp column for seasonal peak metrics.")
    values: list[int] = []
    for value in frame["timestamp"]:
        ts = pd.Timestamp(value)
        values.append(int(ts.month))
    return pd.Series(values, index=frame.index)


def _column_candidates(base_candidates: Iterable[str]) -> list[tuple[str, float, str]]:
    candidates: list[tuple[str, float, str]] = []
    for name in base_candidates:
        candidates.append((name, 1.0, "W"))
        if name.endswith("_W"):
            candidates.append((f"{name[:-2]}_kW", 1000.0, "kW"))
            candidates.append((f"{name[:-2]}_KW", 1000.0, "kW"))
        if name.endswith("_kW"):
            candidates.append((name, 1000.0, "kW"))
    return candidates


def _find_power_column(frame: pd.DataFrame, base_candidates: Iterable[str]) -> tuple[str, float] | None:
    for name, multiplier, _unit in _column_candidates(base_candidates):
        if name in frame.columns:
            return name, multiplier
    return None


def _profile_duration_years(frame: pd.DataFrame) -> float:
    timestamps = _timestamp_values(frame)
    durations = infer_step_durations_seconds(timestamps)
    total_hours = sum(max(float(duration), 0.0) for duration in durations) / 3600.0
    return total_hours / 8760.0 if total_hours > 0.0 else 1.0


def _annualized_power_scale(raw_outputs: Mapping[str, Any], frame: pd.DataFrame) -> float:
    """Return scale needed when an annual-calibrated profile spans a multi-year climate window."""

    representation = ""
    for summary in _summary_dicts(raw_outputs):
        representation = str(summary.get("profile_representation", ""))
        if representation:
            break
    if representation != "stock_weighted_per_household":
        return 1.0
    duration_years = _profile_duration_years(frame)
    return duration_years if duration_years > 1.5 else 1.0


def _integrate_power_column(
    frame: pd.DataFrame,
    base_candidates: Iterable[str],
    metric_name: str,
    columns_used: dict[str, str],
) -> float | None:
    match = _find_power_column(frame, base_candidates)
    if match is None:
        return None
    column, multiplier = match
    values = pd.to_numeric(frame[column], errors="coerce").fillna(0.0) * multiplier
    columns_used[metric_name] = column
    return integrate_power_series_kwh(values, timestamps=_timestamp_values(frame))


def _percentile_power_column(
    frame: pd.DataFrame,
    base_candidates: Iterable[str],
    metric_name: str,
    columns_used: dict[str, str],
    q: float,
) -> float | None:
    """Return the time-weighted q-th percentile (0–100) of a power column."""
    match = _find_power_column(frame, base_candidates)
    if match is None:
        return None
    column, multiplier = match
    values = pd.to_numeric(frame[column], errors="coerce").fillna(0.0) * multiplier
    columns_used[metric_name] = column
    if values.empty:
        return None
    timestamps = _timestamp_values(frame)
    durations = infer_step_durations_seconds(timestamps)
    weights = pd.Series([max(d, 0.0) for d in durations], index=values.index, dtype=float)
    total_weight = float(weights.sum())
    if total_weight <= 0.0:
        return float(values.quantile(q / 100.0))
    order = values.argsort()
    sorted_values = values.iloc[order].values
    sorted_weights = weights.iloc[order].values
    cumulative = sorted_weights.cumsum() / total_weight
    return float(pd.Series(sorted_values).iloc[int((cumulative < q / 100.0).sum())])


def _peak_power_column(
    frame: pd.DataFrame,
    base_candidates: Iterable[str],
    metric_name: str,
    columns_used: dict[str, str],
    months: set[int] | None = None,
) -> float | None:
    match = _find_power_column(frame, base_candidates)
    if match is None:
        energy_columns = [candidate.replace("_W", "_kWh") for candidate in base_candidates if candidate.endswith("_W")]
        for column in energy_columns:
            if column in frame.columns:
                timestamps = _timestamp_values(frame)
                durations = infer_step_durations_seconds(timestamps)
                energy = pd.to_numeric(frame[column], errors="coerce").fillna(0.0)
                power = [
                    0.0 if duration <= 0.0 else float(value) * 1000.0 * 3600.0 / float(duration)
                    for value, duration in zip(energy, durations)
                ]
                series = pd.Series(power, index=frame.index, dtype=float)
                columns_used[metric_name] = column
                if months is not None:
                    mask = _timestamp_months(frame).isin(months)
                    series = series[mask]
                return float(series.max()) if not series.empty else 0.0
        return None

    column, multiplier = match
    series = pd.to_numeric(frame[column], errors="coerce").fillna(0.0) * multiplier
    columns_used[metric_name] = column
    if months is not None:
        mask = _timestamp_months(frame).isin(months)
        series = series[mask]
    return float(series.max()) if not series.empty else 0.0


def _set_metric(
    metrics: dict[str, float],
    missing: list[str],
    metric_name: str,
    value: float | None,
) -> None:
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        metrics[metric_name] = float("nan")
        missing.append(metric_name)
    else:
        metrics[metric_name] = float(value)


def _summary_or_integrated(
    raw_outputs: Mapping[str, Any],
    summary_paths: Iterable[tuple[str, ...]],
    frame: pd.DataFrame,
    power_candidates: Iterable[str],
    metric_name: str,
    columns_used: dict[str, str],
) -> float | None:
    value, source = _summary_value_with_source(raw_outputs, summary_paths)
    if value is not None:
        if source:
            columns_used[metric_name] = source
        return value
    return _integrate_power_column(frame, power_candidates, metric_name, columns_used)


def validate_required_output_columns(raw_outputs: Mapping[str, Any], required_columns: list[str]) -> list[str]:
    """Return missing required columns from the primary raw timeseries output."""

    frame = _first_frame(raw_outputs)
    if frame is None:
        return list(required_columns)
    return [column for column in required_columns if column not in frame.columns]


def compute_energy_metrics(raw_outputs: Mapping[str, Any], run_config: Mapping[str, Any]) -> dict[str, float]:
    """Compute standardized annual energy metrics from raw outputs."""

    result = compute_standardized_output_metrics(raw_outputs, run_config, strict=True)
    return {
        key: result.metrics[key]
        for key in REQUIRED_METRIC_COLUMNS
        if key in result.metrics and not key.endswith("_W") and key not in {"mean_T_out_C", "winter_mean_T_out_C", "summer_mean_T_out_C", "HDD_15", "HDD_18", "CDD_22", "mean_solar_W_m2"}
    }


def compute_peak_metrics(raw_outputs: Mapping[str, Any], run_config: Mapping[str, Any]) -> dict[str, float]:
    """Compute standardized grid peak metrics from raw outputs."""

    result = compute_standardized_output_metrics(raw_outputs, run_config, strict=True)
    return {
        "peak_grid_import_W": result.metrics["peak_grid_import_W"],
        "winter_peak_grid_import_W": result.metrics["winter_peak_grid_import_W"],
        "summer_peak_grid_import_W": result.metrics["summer_peak_grid_import_W"],
        "p95_grid_import_W": result.metrics["p95_grid_import_W"],
        "p99_grid_import_W": result.metrics["p99_grid_import_W"],
        "grid_import_load_factor": result.metrics["grid_import_load_factor"],
    }


def compute_standardized_output_metrics(
    raw_outputs: Mapping[str, Any],
    run_config: Mapping[str, Any],
    *,
    strict: bool = True,
) -> OutputMetricResult:
    """Compute standardized non-climate output metrics for one leaf."""

    frame = _first_frame(raw_outputs)
    if frame is None:
        raise OutputReaderError("No raw timeseries output found. Expected annual_profile.csv or timeseries.csv.")

    columns_used: dict[str, str] = {}
    metrics: dict[str, float] = {}
    missing: list[str] = []
    policies: dict[str, str] = {}
    assumptions: list[str] = []
    flags = _technology_flags(run_config)
    annualized_power_scale = _annualized_power_scale(raw_outputs, frame)

    _set_metric(
        metrics,
        missing,
        "annual_electricity_gross_kWh",
        _summary_or_integrated(
            raw_outputs,
            (
                ("annual_electricity_gross_kWh",),
                ("annual_energy_by_carrier_kWh", "electricity_gross_actual"),
            ),
            frame,
            ("P_el_gross_actual_W", "gross_electricity_W", "electricity_gross_W"),
            "annual_electricity_gross_kWh",
            columns_used,
        ),
    )
    _set_metric(
        metrics,
        missing,
        "annual_grid_import_kWh",
        _summary_or_integrated(
            raw_outputs,
            (
                ("annual_grid_import_kWh",),
                ("annual_energy_by_carrier_kWh", "electricity_grid_import"),
            ),
            frame,
            ("P_el_grid_import_W", "P_grid_import_W", "grid_import_W"),
            "annual_grid_import_kWh",
            columns_used,
        ),
    )
    _set_metric(
        metrics,
        missing,
        "annual_grid_export_kWh",
        _summary_or_integrated(
            raw_outputs,
            (
                ("annual_grid_export_kWh",),
                ("annual_energy_by_carrier_kWh", "electricity_grid_export"),
            ),
            frame,
            ("P_el_grid_export_W", "P_grid_export_W", "grid_export_W"),
            "annual_grid_export_kWh",
            columns_used,
        ),
    )

    gas_value, gas_source = _summary_value_with_source(
        raw_outputs,
        (
            ("annual_gas_kWh",),
            ("annual_energy_by_carrier_kWh", "natural_gas"),
        ),
    )
    if gas_source:
        columns_used["annual_gas_kWh"] = gas_source
    if gas_value is None:
        gas_value = _integrate_power_column(
            frame,
            ("P_gas_total_W", "P_gas_space_heating_W", "gas_W", "natural_gas_W"),
            "annual_gas_kWh",
            columns_used,
        )
    if gas_value is None and flags["heat_pump_assumed"]:
        gas_value = 0.0
        policies["gas_metric_policy"] = "no_gas_case_from_technology_metadata"
    else:
        policies["gas_metric_policy"] = "raw_output_or_summary"
    _set_metric(metrics, missing, "annual_gas_kWh", gas_value)

    heating_value, heating_source = _summary_value_with_source(
        raw_outputs,
        (
            ("annual_useful_heating_kWh",),
            ("space_heating_thermal_kWh",),
        ),
    )
    if heating_source:
        columns_used["annual_useful_heating_kWh"] = heating_source
    if heating_value is None:
        heating_value = _integrate_power_column(
            frame,
            ("Q_heating_supplied_W", "Q_useful_space_heating_W", "useful_heating_W"),
            "annual_useful_heating_kWh",
            columns_used,
        )
    policies["heating_metric_policy"] = "useful_heat_Q_heating_supplied_W"
    _set_metric(metrics, missing, "annual_useful_heating_kWh", heating_value)

    dhw_value, dhw_source = _summary_value_with_source(
        raw_outputs,
        (
            ("annual_dhw_kWh",),
            ("dhw_thermal_kWh",),
        ),
    )
    if dhw_source:
        columns_used["annual_dhw_kWh"] = dhw_source
    if dhw_value is None:
        dhw_value = _integrate_power_column(
            frame,
            ("Q_dhw_demand_W", "Q_dhw_W", "useful_dhw_W"),
            "annual_dhw_kWh",
            columns_used,
        )
    _set_metric(metrics, missing, "annual_dhw_kWh", dhw_value)

    _set_metric(
        metrics,
        missing,
        "peak_grid_import_W",
        None
        if (peak_value := _peak_power_column(
            frame,
            ("P_el_grid_import_W", "P_grid_import_W", "grid_import_W"),
            "peak_grid_import_W",
            columns_used,
        ))
        is None
        else peak_value * annualized_power_scale,
    )
    _set_metric(
        metrics,
        missing,
        "winter_peak_grid_import_W",
        None
        if (winter_peak_value := _peak_power_column(
            frame,
            ("P_el_grid_import_W", "P_grid_import_W", "grid_import_W"),
            "winter_peak_grid_import_W",
            columns_used,
            months={12, 1, 2},
        ))
        is None
        else winter_peak_value * annualized_power_scale,
    )
    _set_metric(
        metrics,
        missing,
        "summer_peak_grid_import_W",
        None
        if (summer_peak_value := _peak_power_column(
            frame,
            ("P_el_grid_import_W", "P_grid_import_W", "grid_import_W"),
            "summer_peak_grid_import_W",
            columns_used,
            months={6, 7, 8},
        ))
        is None
        else summer_peak_value * annualized_power_scale,
    )
    _set_metric(
        metrics,
        missing,
        "p95_grid_import_W",
        None
        if (p95_value := _percentile_power_column(
            frame,
            ("P_el_grid_import_W", "P_grid_import_W", "grid_import_W"),
            "p95_grid_import_W",
            columns_used,
            q=95.0,
        ))
        is None
        else p95_value * annualized_power_scale,
    )
    _set_metric(
        metrics,
        missing,
        "p99_grid_import_W",
        None
        if (p99_value := _percentile_power_column(
            frame,
            ("P_el_grid_import_W", "P_grid_import_W", "grid_import_W"),
            "p99_grid_import_W",
            columns_used,
            q=99.0,
        ))
        is None
        else p99_value * annualized_power_scale,
    )
    if annualized_power_scale != 1.0:
        policies["grid_peak_power_policy"] = (
            "annualized_stock_weighted_profile_scaled_by_climate_window_duration"
        )
    peak_w = metrics.get("peak_grid_import_W", float("nan"))
    mean_import_w = _summary_value(
        raw_outputs, (("mean_grid_import_W",),)
    )
    if mean_import_w is None:
        match = _find_power_column(frame, ("P_el_grid_import_W", "P_grid_import_W", "grid_import_W"))
        if match is not None:
            col, mul = match
            mean_import_w = float(
                pd.to_numeric(frame[col], errors="coerce").fillna(0.0).mean()
                * mul
                * annualized_power_scale
            )
    if mean_import_w is not None and math.isfinite(peak_w) and peak_w > 0.0:
        load_factor: float | None = float(mean_import_w) / float(peak_w)
    else:
        load_factor = None
    _set_metric(metrics, missing, "grid_import_load_factor", load_factor)

    pv_generation, pv_source = _summary_value_with_source(
        raw_outputs,
        (
            ("pv_generation_kWh",),
            ("annual_pv_generation_kWh",),
            ("annual_energy_by_carrier_kWh", "pv_generation"),
        ),
    )
    if pv_source:
        columns_used["pv_generation_kWh"] = pv_source
    if pv_generation is None:
        pv_generation = _integrate_power_column(
            frame,
            ("P_pv_generation_W", "pv_generation_W", "P_solar_generation_W"),
            "pv_generation_kWh",
            columns_used,
        )
    if not flags["pv_assumed"]:
        if pv_generation is None:
            pv_generation = 0.0
        policies["pv_metric_policy"] = "no_pv_case"
    elif pv_generation is None:
        policies["pv_metric_policy"] = "missing_pv_output"
    elif pv_generation <= 0.0:
        policies["pv_metric_policy"] = "no_pv_generation"
    else:
        policies["pv_metric_policy"] = "raw_output_or_summary"
    _set_metric(metrics, missing, "pv_generation_kWh", pv_generation)

    if math.isfinite(metrics.get("pv_generation_kWh", float("nan"))):
        export = metrics.get("annual_grid_export_kWh", float("nan"))
        if math.isfinite(export):
            pv_self_consumption = max(float(metrics["pv_generation_kWh"]) - float(export), 0.0)
            if metrics["pv_generation_kWh"] > 0:
                pv_self_consumption = min(pv_self_consumption, metrics["pv_generation_kWh"])
            metrics["pv_self_consumption_kWh"] = float(pv_self_consumption)
            metrics["pv_export_fraction"] = (
                float(export) / float(metrics["pv_generation_kWh"]) if metrics["pv_generation_kWh"] > 0.0 else 0.0
            )
        else:
            _set_metric(metrics, missing, "pv_self_consumption_kWh", None)
            _set_metric(metrics, missing, "pv_export_fraction", None)
    else:
        _set_metric(metrics, missing, "pv_self_consumption_kWh", None)
        _set_metric(metrics, missing, "pv_export_fraction", None)

    ev_charging, ev_source = _summary_value_with_source(
        raw_outputs,
        (
            ("ev_charging_kWh",),
            ("annual_ev_charging_kWh",),
            ("annual_energy_by_carrier_kWh", "ev_charging"),
        ),
    )
    if ev_source:
        columns_used["ev_charging_kWh"] = ev_source
    if ev_charging is None:
        ev_charging = _integrate_power_column(
            frame,
            ("P_el_ev_charging_W", "ev_charging_W", "P_ev_charging_W"),
            "ev_charging_kWh",
            columns_used,
        )
    if not flags["ev_assumed"]:
        if ev_charging is None:
            ev_charging = 0.0
        policies["ev_metric_policy"] = "no_ev_case"
    elif ev_charging is None:
        policies["ev_metric_policy"] = "missing_ev_output"
    elif ev_charging <= 0.0 and flags["ev_assumed"]:
        policies["ev_metric_policy"] = "ev_case_zero_output"
    else:
        policies["ev_metric_policy"] = "raw_output_or_summary"
    _set_metric(metrics, missing, "ev_charging_kWh", ev_charging)

    files_used = [str(path.name) for path in dict(raw_outputs.get("files", {})).values()]
    result = OutputMetricResult(
        metrics=metrics,
        missing_metrics=sorted(set(missing)),
        policies=policies,
        raw_output_files_used=sorted(files_used),
        raw_output_columns_used=columns_used,
        assumptions=assumptions,
    )
    if strict and result.missing_metrics:
        raise MissingRequiredOutputError(result.missing_metrics)
    return result
