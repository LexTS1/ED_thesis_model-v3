"""Run the PV validation triangle for model_v3.

The runner separates three checks:

* PVGIS physical reference: compares the model's irradiance-to-PV conversion
  with a 1 kWp PVGIS hourly PV-output export.
* Elia ODS032 Belgian PV ingestion: loads the measured/upscaled Belgian PV
  production reference and compares it with an optional model capacity-factor
  profile when configured.
* Fluvius residential signature: loads representative no-PV and PV digital
  meter categories and compares them with an optional model net-load profile
  when configured.

No scenario-tree simulations are executed by this module.
"""

from __future__ import annotations

if __package__ in {None, ""}:
    import sys
    from pathlib import Path as _Path

    sys.path.append(str(_Path(__file__).resolve().parents[4]))

import argparse
import csv
import json
from dataclasses import dataclass
from datetime import timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.request import urlopen

import numpy as np
import pandas as pd
import yaml

from model_v3.systems.distributed_energy import pv_generation_from_irradiance

try:  # pragma: no cover - covered indirectly when matplotlib is available.
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
except Exception:  # pragma: no cover - graceful fallback for minimal installs.
    plt = None


BELGIUM_TZ = "Europe/Brussels"
INTERVAL_HOURS_FLUVIUS = 0.25
PVGIS_TIME_PATTERN = r"^\d{8}:\d{4}$"
DEFAULT_CONFIG = "config/validation/technology_pv.yaml"
DEFAULT_ELIA_URL = (
    "https://opendata.elia.be/api/explore/v2.1/catalog/datasets/ods032/exports/csv?"
    "select=datetime,resolutioncode,region,measured,monitoredcapacity,loadfactor"
    "&where=resolutioncode%20%3D%20%22PT15M%22%20AND%20datetime%20%3E%3D%20"
    "date%272024-01-01T00%3A00%3A00%27%20AND%20datetime%20%3C%20"
    "date%272025-01-01T00%3A00%3A00%27"
    "&refine=region%3ABelgium&order_by=datetime%20ASC&timezone=Europe%2FBrussels"
    "&use_labels=false&delimiter=%3B"
)


@dataclass(frozen=True)
class ValidationLegResult:
    """Structured result for one PV-validation leg."""

    status: str
    metrics: dict[str, Any]
    source_files: list[str]
    output_files: list[str]
    warnings: list[str]


def _repo_root_from_args(path: str | Path | None) -> Path:
    return Path(path or ".").expanduser().resolve()


def _resolve_path(repo_root: Path, value: str | Path | None) -> Path | None:
    if value in {None, ""}:
        return None
    path = Path(str(value)).expanduser()
    return path if path.is_absolute() else repo_root / path


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle) or {}
    if not isinstance(loaded, Mapping):
        raise ValueError(f"Expected a YAML mapping in `{path}`.")
    return dict(loaded)


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True, default=str)
        handle.write("\n")


def _write_markdown(path: Path, lines: Iterable[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _float_or_none(value: Any) -> float | None:
    try:
        if pd.isna(value):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalise_column_name(name: str) -> str:
    return "".join(ch for ch in str(name).lower() if ch.isalnum())


def _find_column(columns: Iterable[str], candidates: Iterable[str]) -> str | None:
    by_normalised = {_normalise_column_name(column): column for column in columns}
    for candidate in candidates:
        if candidate in columns:
            return candidate
        normalised = _normalise_column_name(candidate)
        if normalised in by_normalised:
            return by_normalised[normalised]
    return None


def _read_csv_sniffed(path: Path, *, nrows: int | None = None, usecols: list[str] | None = None) -> pd.DataFrame:
    with path.open("r", encoding="utf-8-sig", errors="replace") as handle:
        sample = handle.read(4096)
    try:
        delimiter = csv.Sniffer().sniff(sample, delimiters=",;\t").delimiter
    except csv.Error:
        delimiter = ";" if sample.count(";") > sample.count(",") else ","
    return pd.read_csv(path, sep=delimiter, nrows=nrows, usecols=usecols, low_memory=False)


def _find_pvgis_header_row(path: Path) -> int:
    with path.open("r", encoding="utf-8-sig", errors="replace") as handle:
        for line_number, line in enumerate(handle):
            if line.lstrip().startswith("time,") or line.lstrip().startswith("time;"):
                return line_number
    raise ValueError(f"No PVGIS data header starting with `time` found in `{path}`.")


def _load_pvgis_reference(path: Path) -> pd.DataFrame:
    header_row = _find_pvgis_header_row(path)
    raw = pd.read_csv(path, skiprows=header_row, low_memory=False)
    if "time" not in raw.columns:
        raise ValueError(f"PVGIS file `{path}` does not contain a `time` column.")
    data = raw.loc[raw["time"].astype(str).str.fullmatch(PVGIS_TIME_PATTERN, na=False)].copy()
    if data.empty:
        raise ValueError(f"PVGIS file `{path}` contains no hourly data rows.")
    timestamp = pd.to_datetime(data["time"].astype(str), format="%Y%m%d:%H%M", errors="coerce")
    if timestamp.isna().any():
        raise ValueError(f"PVGIS file `{path}` contains unparsable timestamps.")
    # Match the existing repository PVGIS loaders: PVGIS timestamps are kept on
    # a fixed reported local calendar rather than interpreted through daylight
    # saving transitions.
    data.index = pd.DatetimeIndex(timestamp).tz_localize(timezone(timedelta(hours=1)))
    return data.sort_index()


def _infer_step_hours(index: pd.DatetimeIndex, *, fallback: float = 1.0) -> float:
    if len(index) < 2:
        return fallback
    deltas = index.to_series().sort_values().diff().dropna().dt.total_seconds() / 3600.0
    if deltas.empty:
        return fallback
    return float(deltas.median())


def _energy_kwh(power_w: pd.Series, *, step_hours: float | None = None) -> float:
    ordered = power_w.sort_index()
    if ordered.empty:
        return 0.0
    hours = _infer_step_hours(pd.DatetimeIndex(ordered.index)) if step_hours is None else float(step_hours)
    return float((ordered.clip(lower=0.0) / 1000.0 * hours).sum())


def _monthly_energy_kwh(power_w: pd.Series) -> pd.Series:
    if power_w.empty:
        return pd.Series(dtype=float)
    hours = _infer_step_hours(pd.DatetimeIndex(power_w.index))
    energy = power_w.clip(lower=0.0) / 1000.0 * hours
    index = pd.DatetimeIndex(energy.index)
    if index.tz is not None:
        index = index.tz_localize(None)
    return energy.groupby(index.to_period("M")).sum()


def _yearly_energy_kwh(power_w: pd.Series) -> pd.Series:
    if power_w.empty:
        return pd.Series(dtype=float)
    hours = _infer_step_hours(pd.DatetimeIndex(power_w.index))
    energy = power_w.clip(lower=0.0) / 1000.0 * hours
    return energy.groupby(pd.DatetimeIndex(energy.index).year).sum()


def _rmse(model: pd.Series, reference: pd.Series) -> float:
    aligned = pd.concat([model, reference], axis=1, join="inner").dropna()
    if aligned.empty:
        return float("nan")
    diff = aligned.iloc[:, 0].to_numpy(dtype=float) - aligned.iloc[:, 1].to_numpy(dtype=float)
    return float(np.sqrt(np.mean(diff**2)))


def _mae(model: pd.Series, reference: pd.Series) -> float:
    aligned = pd.concat([model, reference], axis=1, join="inner").dropna()
    if aligned.empty:
        return float("nan")
    diff = aligned.iloc[:, 0].to_numpy(dtype=float) - aligned.iloc[:, 1].to_numpy(dtype=float)
    return float(np.mean(np.abs(diff)))


def _correlation(model: pd.Series, reference: pd.Series) -> float:
    aligned = pd.concat([model, reference], axis=1, join="inner").dropna()
    if len(aligned) < 2:
        return float("nan")
    if aligned.iloc[:, 0].std() == 0 or aligned.iloc[:, 1].std() == 0:
        return float("nan")
    return float(aligned.iloc[:, 0].corr(aligned.iloc[:, 1]))


def _mean_daily_profile(series: pd.Series) -> pd.Series:
    if series.empty:
        return pd.Series(dtype=float)
    local = series.copy()
    if local.index.tz is not None:
        local.index = pd.DatetimeIndex(local.index).tz_convert(BELGIUM_TZ)
    keys = [pd.DatetimeIndex(local.index).hour, pd.DatetimeIndex(local.index).minute]
    profile = local.groupby(keys).mean()
    profile.index = [f"{hour:02d}:{minute:02d}" for hour, minute in profile.index]
    return profile


def _load_power_profile(path: Path, *, value_candidates: Iterable[str]) -> pd.Series:
    frame = _read_csv_sniffed(path)
    timestamp_column = _find_column(frame.columns, ["timestamp", "datetime", "time", "date_time"])
    value_column = _find_column(frame.columns, value_candidates)
    if timestamp_column is None or value_column is None:
        raise ValueError(f"Could not detect timestamp/value columns in `{path}`.")
    timestamps = pd.to_datetime(frame[timestamp_column], utc=True, errors="coerce").dt.tz_convert(BELGIUM_TZ)
    values = pd.to_numeric(frame[value_column], errors="coerce")
    series = pd.Series(values.to_numpy(dtype=float), index=pd.DatetimeIndex(timestamps), dtype=float)
    series = series.loc[~series.index.isna()].dropna().sort_index()
    if series.index.has_duplicates:
        series = series.groupby(level=0).mean().sort_index()
    return series


def _select_existing_path(repo_root: Path, globs: Iterable[str]) -> Path | None:
    candidates: list[Path] = []
    for pattern in globs:
        glob_root = repo_root
        candidates.extend(sorted(glob_root.glob(pattern)))
    files = [path for path in candidates if path.is_file()]
    return files[0] if files else None


def _plot_pvgis(path: Path, reference_w: pd.Series, model_w: pd.Series) -> None:
    if plt is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    sample = pd.concat(
        {
            "PVGIS reference W": reference_w,
            "model formula W": model_w,
        },
        axis=1,
        join="inner",
    ).dropna()
    if sample.empty:
        return
    sample = sample.iloc[: min(len(sample), 24 * 14)]
    fig, ax = plt.subplots(figsize=(10, 4))
    sample.plot(ax=ax, linewidth=1.0)
    ax.set_title("PVGIS 1 kWp Reference vs model_v3 PV conversion")
    ax.set_ylabel("Power (W)")
    ax.set_xlabel("Timestamp")
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)


def _plot_elia(path: Path, capacity_factor: pd.Series) -> None:
    if plt is None or capacity_factor.empty:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    daily = capacity_factor.resample("D").mean()
    if len(daily) < 2:
        return
    fig, ax = plt.subplots(figsize=(10, 4))
    daily.plot(ax=ax, linewidth=1.1)
    ax.set_title("Elia ODS032 Belgian PV daily mean capacity factor")
    ax.set_ylabel("Capacity factor")
    ax.set_xlabel("Date")
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)


def _plot_fluvius(path: Path, no_pv_net_kw: pd.Series, pv_net_kw: pd.Series, pv_export_kw: pd.Series) -> None:
    if plt is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    frame = pd.concat(
        {
            "no PV net import kW": _mean_daily_profile(no_pv_net_kw),
            "PV net import kW": _mean_daily_profile(pv_net_kw),
            "PV export kW": _mean_daily_profile(pv_export_kw),
        },
        axis=1,
    )
    if frame.empty:
        return
    fig, ax = plt.subplots(figsize=(10, 4))
    frame.plot(ax=ax, linewidth=1.1)
    ax.set_title("Fluvius representative residential PV signature")
    ax.set_ylabel("Mean power (kW per represented meter)")
    ax.set_xlabel("Local time of day")
    ax.tick_params(axis="x", rotation=90)
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)


def validate_pvgis_leg(repo_root: Path, cfg: Mapping[str, Any], report_dir: Path, figure_dir: Path) -> ValidationLegResult:
    warnings: list[str] = []
    output_files: list[str] = []
    globs = list(cfg.get("reference_globs", [])) or [
        "model_v3/validation/pvgis/*.csv",
        "validation/pvgis/*.csv",
        "outputs/validation/pvgis/*.csv",
        "inputs/validation/pv/pvgis/*.csv",
    ]
    path = _resolve_path(repo_root, cfg.get("reference_file"))
    if path is None or not path.exists():
        path = _select_existing_path(repo_root, globs)
    if path is None or not path.exists():
        return ValidationLegResult(
            status="missing_reference",
            metrics={},
            source_files=[],
            output_files=[],
            warnings=["No PVGIS hourly PV-output CSV was found."],
        )

    data = _load_pvgis_reference(path)
    power_column = _find_column(data.columns, cfg.get("power_column_candidates", ["P", "pv_power_W", "P_W"]))
    irradiance_columns = [
        column
        for column in ("Gb(i)", "Gd(i)", "Gr(i)")
        if column in data.columns
    ]
    irradiance_column = _find_column(
        data.columns,
        cfg.get("irradiance_column_candidates", ["G(i)", "G_i", "poa_irradiance_W_m2", "global_in_plane_W_m2"]),
    )
    if power_column is None:
        return ValidationLegResult(
            status="missing_reference_power",
            metrics={"rows": int(len(data))},
            source_files=[str(path.relative_to(repo_root))],
            output_files=[],
            warnings=[f"PVGIS file `{path}` has no detectable PV power column."],
        )
    if irradiance_column is None and not irradiance_columns:
        return ValidationLegResult(
            status="missing_irradiance_driver",
            metrics={"rows": int(len(data))},
            source_files=[str(path.relative_to(repo_root))],
            output_files=[],
            warnings=[f"PVGIS file `{path}` has no detectable plane-of-array irradiance column."],
        )

    capacity_kwp = float(cfg.get("capacity_kwp", 1.0))
    performance_ratio = float(cfg.get("performance_ratio", 0.86))
    reference_w = pd.Series(pd.to_numeric(data[power_column], errors="coerce"), index=data.index, dtype=float).fillna(0.0)
    if irradiance_column:
        irradiance = pd.Series(pd.to_numeric(data[irradiance_column], errors="coerce"), index=data.index, dtype=float).fillna(0.0)
    else:
        irradiance = pd.Series(0.0, index=data.index, dtype=float)
        for column in irradiance_columns:
            irradiance = irradiance.add(pd.to_numeric(data[column], errors="coerce").fillna(0.0), fill_value=0.0)
    pv_model_cfg = {
        "system_size_kwp": capacity_kwp,
        "inverter_efficiency": performance_ratio,
    }
    model_w = irradiance.clip(lower=0.0).map(
        lambda value: pv_generation_from_irradiance(value, pv_model_cfg, has_pv=True)
    )

    reference_cf = reference_w / max(capacity_kwp * 1000.0, 1e-9)
    model_cf = model_w / max(capacity_kwp * 1000.0, 1e-9)
    daylight = (reference_w > 5.0) | (irradiance > 20.0)
    monthly_ref = _monthly_energy_kwh(reference_w)
    monthly_model = _monthly_energy_kwh(model_w)
    monthly_aligned = pd.concat([monthly_model, monthly_ref], axis=1, join="inner").dropna()
    if not monthly_aligned.empty:
        monthly_bias_pct = (
            (monthly_aligned.iloc[:, 0] - monthly_aligned.iloc[:, 1]).abs()
            / monthly_aligned.iloc[:, 1].abs().clip(lower=1e-9)
            * 100.0
        )
        mean_abs_monthly_yield_error_pct = float(monthly_bias_pct.mean())
    else:
        mean_abs_monthly_yield_error_pct = float("nan")

    reference_yearly = _yearly_energy_kwh(reference_w)
    model_yearly = _yearly_energy_kwh(model_w)

    metrics = {
        "rows": int(len(data)),
        "start": str(data.index.min()),
        "end": str(data.index.max()),
        "capacity_kwp": capacity_kwp,
        "performance_ratio": performance_ratio,
        "reference_mean_annual_specific_yield_kwh_per_kwp": float(reference_yearly.mean()) / max(capacity_kwp, 1e-9),
        "model_mean_annual_specific_yield_kwh_per_kwp": float(model_yearly.mean()) / max(capacity_kwp, 1e-9),
        "annual_specific_yield_error_pct": (
            (float(model_yearly.mean()) - float(reference_yearly.mean()))
            / max(float(reference_yearly.mean()), 1e-9)
            * 100.0
        ),
        "mean_abs_monthly_yield_error_pct": mean_abs_monthly_yield_error_pct,
        "hourly_capacity_factor_rmse": _rmse(model_cf, reference_cf),
        "hourly_capacity_factor_mae": _mae(model_cf, reference_cf),
        "pearson_correlation": _correlation(model_cf, reference_cf),
        "daylight_capacity_factor_rmse": _rmse(model_cf.loc[daylight], reference_cf.loc[daylight]),
        "power_column": power_column,
        "irradiance_driver": irradiance_column or "+".join(irradiance_columns),
    }
    alignment_path = report_dir / "pvgis_reference_alignment.csv"
    pd.DataFrame(
        {
            "timestamp": reference_w.index.astype(str),
            "pvgis_reference_W": reference_w.to_numpy(dtype=float),
            "model_formula_W": model_w.to_numpy(dtype=float),
            "pvgis_reference_capacity_factor": reference_cf.to_numpy(dtype=float),
            "model_capacity_factor": model_cf.to_numpy(dtype=float),
        }
    ).to_csv(alignment_path, index=False)
    output_files.append(str(alignment_path.relative_to(repo_root)))
    figure_path = figure_dir / "pvgis_reference_validation.png"
    _plot_pvgis(figure_path, reference_w, model_w)
    if figure_path.exists():
        output_files.append(str(figure_path.relative_to(repo_root)))

    return ValidationLegResult(
        status="model_reference_comparison",
        metrics=metrics,
        source_files=[str(path.relative_to(repo_root))],
        output_files=output_files,
        warnings=warnings,
    )


def _download_file(url: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with urlopen(url, timeout=120) as response:
        destination.write_bytes(response.read())


def validate_elia_leg(
    repo_root: Path,
    cfg: Mapping[str, Any],
    report_dir: Path,
    figure_dir: Path,
    *,
    force_download: bool = False,
) -> ValidationLegResult:
    warnings: list[str] = []
    output_files: list[str] = []
    source_files: list[str] = []
    local_file = _resolve_path(repo_root, cfg.get("local_file", "inputs/validation/pv/elia/ods032_belgium_pv_2024_pt15m.csv"))
    assert local_file is not None
    url = str(cfg.get("url") or DEFAULT_ELIA_URL)
    download_if_missing = bool(cfg.get("download_if_missing", False) or force_download)
    if force_download or (download_if_missing and not local_file.exists()):
        _download_file(url, local_file)
    if not local_file.exists():
        return ValidationLegResult(
            status="missing_reference",
            metrics={"url": url},
            source_files=[],
            output_files=[],
            warnings=[
                "Elia ODS032 CSV is not cached. Run this runner with `--download-elia` to fetch the configured URL."
            ],
        )

    frame = _read_csv_sniffed(local_file)
    timestamp_column = _find_column(frame.columns, ["datetime", "timestamp", "time"])
    measured_column = _find_column(frame.columns, ["measured", "measured_MW", "measuredmw"])
    capacity_column = _find_column(frame.columns, ["monitoredcapacity", "monitored_capacity", "monitoredcapacitymw"])
    if timestamp_column is None or measured_column is None or capacity_column is None:
        return ValidationLegResult(
            status="invalid_reference",
            metrics={"columns": list(frame.columns)},
            source_files=[str(local_file.relative_to(repo_root))],
            output_files=[],
            warnings=["Elia ODS032 file is missing datetime, measured, or monitoredcapacity columns."],
        )

    timestamps = pd.to_datetime(frame[timestamp_column], utc=True, errors="coerce").dt.tz_convert(BELGIUM_TZ)
    measured_mw = pd.to_numeric(frame[measured_column], errors="coerce")
    monitored_mw = pd.to_numeric(frame[capacity_column], errors="coerce")
    elia = pd.DataFrame(
        {
            "measured_MW": measured_mw.to_numpy(dtype=float),
            "monitored_capacity_MW": monitored_mw.to_numpy(dtype=float),
        },
        index=pd.DatetimeIndex(timestamps),
    )
    elia = elia.loc[~elia.index.isna()].dropna().sort_index()
    elia = elia.loc[elia["monitored_capacity_MW"] > 0].copy()
    capacity_factor = (elia["measured_MW"] / elia["monitored_capacity_MW"]).clip(lower=0.0)
    capacity_factor.name = "elia_capacity_factor"
    step_hours = _infer_step_hours(pd.DatetimeIndex(elia.index), fallback=0.25)
    annual_generation_gwh = float((elia["measured_MW"].clip(lower=0.0) * step_hours).sum() / 1000.0)
    peak_timestamp = elia["measured_MW"].idxmax() if not elia.empty else None
    metrics: dict[str, Any] = {
        "rows": int(len(elia)),
        "start": str(elia.index.min()) if not elia.empty else None,
        "end": str(elia.index.max()) if not elia.empty else None,
        "step_hours": step_hours,
        "annual_generation_GWh": annual_generation_gwh,
        "mean_monitored_capacity_MW": float(elia["monitored_capacity_MW"].mean()) if not elia.empty else None,
        "mean_capacity_factor": float(capacity_factor.mean()) if not elia.empty else None,
        "p95_capacity_factor": float(capacity_factor.quantile(0.95)) if not elia.empty else None,
        "peak_measured_MW": float(elia["measured_MW"].max()) if not elia.empty else None,
        "peak_timestamp": str(peak_timestamp) if peak_timestamp is not None else None,
    }

    reference_path = report_dir / "elia_ods032_reference_timeseries.csv"
    pd.DataFrame(
        {
            "timestamp": elia.index.astype(str),
            "measured_MW": elia["measured_MW"].to_numpy(dtype=float),
            "monitored_capacity_MW": elia["monitored_capacity_MW"].to_numpy(dtype=float),
            "capacity_factor": capacity_factor.to_numpy(dtype=float),
        }
    ).to_csv(reference_path, index=False)
    output_files.append(str(reference_path.relative_to(repo_root)))
    figure_path = figure_dir / "elia_ods032_capacity_factor.png"
    _plot_elia(figure_path, capacity_factor)
    if figure_path.exists():
        output_files.append(str(figure_path.relative_to(repo_root)))

    model_profile = _resolve_path(repo_root, cfg.get("model_capacity_factor_file"))
    if model_profile and model_profile.exists():
        model_cf = _load_power_profile(model_profile, value_candidates=["capacity_factor", "model_capacity_factor", "pv_capacity_factor"])
        metrics["model_capacity_factor_rmse"] = _rmse(model_cf, capacity_factor)
        metrics["model_capacity_factor_mae"] = _mae(model_cf, capacity_factor)
        metrics["model_capacity_factor_correlation"] = _correlation(model_cf, capacity_factor)
        status = "model_reference_comparison"
        source_files.append(str(model_profile.relative_to(repo_root)))
    else:
        status = "reference_ingested"
        warnings.append(
            "No model capacity-factor profile is configured for 2024; Elia validation is currently ingestion/reference-only."
        )

    source_files.insert(0, str(local_file.relative_to(repo_root)))
    return ValidationLegResult(
        status=status,
        metrics=metrics,
        source_files=source_files,
        output_files=output_files,
        warnings=warnings,
    )


def _load_fluvius_net_components(path: Path) -> pd.DataFrame:
    sample = pd.read_csv(path, nrows=5)
    timestamp_column = _find_column(sample.columns, ["Datum_Startuur", "timestamp", "datetime"])
    import_column = _find_column(sample.columns, ["Volume_Afname_KWh", "afname_kwh", "import_kwh"])
    export_column = _find_column(sample.columns, ["Volume_Injectie_KWh", "injectie_kwh", "export_kwh"])
    if timestamp_column is None or import_column is None:
        raise ValueError(f"Could not detect Fluvius timestamp/import columns in `{path}`.")

    sums: dict[pd.Timestamp, dict[str, float]] = {}
    counts: dict[pd.Timestamp, int] = {}
    usecols = [timestamp_column, import_column]
    if export_column is not None:
        usecols.append(export_column)
    for chunk in pd.read_csv(path, usecols=usecols, chunksize=250_000):
        timestamps = pd.to_datetime(chunk[timestamp_column], utc=True, errors="coerce").dt.tz_convert(BELGIUM_TZ)
        imported_kw = pd.to_numeric(chunk[import_column], errors="coerce") / INTERVAL_HOURS_FLUVIUS
        if export_column is None:
            exported_kw = pd.Series(0.0, index=chunk.index)
        else:
            exported_kw = pd.to_numeric(chunk[export_column], errors="coerce").fillna(0.0) / INTERVAL_HOURS_FLUVIUS
        frame = pd.DataFrame(
            {
                "timestamp": timestamps,
                "import_kW": imported_kw,
                "export_kW": exported_kw,
            }
        ).dropna(subset=["timestamp", "import_kW", "export_kW"])
        grouped = frame.groupby("timestamp", sort=True)[["import_kW", "export_kW"]].agg(["sum", "count"])
        for timestamp, row in grouped.iterrows():
            ts = pd.Timestamp(timestamp)
            bucket = sums.setdefault(ts, {"import_sum": 0.0, "export_sum": 0.0})
            bucket["import_sum"] += float(row[("import_kW", "sum")])
            bucket["export_sum"] += float(row[("export_kW", "sum")])
            counts[ts] = counts.get(ts, 0) + int(row[("import_kW", "count")])

    rows = []
    for timestamp in sorted(sums):
        count = max(counts.get(timestamp, 0), 1)
        import_kw = sums[timestamp]["import_sum"] / count
        export_kw = sums[timestamp]["export_sum"] / count
        rows.append(
            {
                "timestamp": timestamp,
                "import_kW": import_kw,
                "export_kW": export_kw,
                "net_import_kW": import_kw - export_kw,
            }
        )
    if not rows:
        raise ValueError(f"Fluvius file `{path}` yielded no usable rows.")
    frame = pd.DataFrame(rows).set_index("timestamp").sort_index()
    return frame


def validate_fluvius_leg(repo_root: Path, cfg: Mapping[str, Any], report_dir: Path, figure_dir: Path) -> ValidationLegResult:
    warnings: list[str] = []
    output_files: list[str] = []
    enabled = bool(cfg.get("enabled", True))
    if not enabled:
        return ValidationLegResult(
            status="disabled",
            metrics={},
            source_files=[],
            output_files=[],
            warnings=["Fluvius PV-signature validation is disabled in the config."],
        )

    no_pv_file = _resolve_path(repo_root, cfg.get("no_pv_file", "inputs/load_profiles/fluvius/P6269_Open_Data_geen_ZP.csv"))
    pv_file = _resolve_path(repo_root, cfg.get("pv_file", "inputs/load_profiles/fluvius/P6269_Open_Data_enkel_ZP.csv"))
    missing = [str(path.relative_to(repo_root)) for path in (no_pv_file, pv_file) if path is None or not path.exists()]
    if missing:
        return ValidationLegResult(
            status="missing_reference",
            metrics={},
            source_files=[],
            output_files=[],
            warnings=[f"Missing Fluvius reference file(s): {', '.join(missing)}"],
        )
    assert no_pv_file is not None and pv_file is not None
    no_pv = _load_fluvius_net_components(no_pv_file)
    pv = _load_fluvius_net_components(pv_file)
    joined = pd.concat(
        {
            "no_pv_net_kW": no_pv["net_import_kW"],
            "pv_net_kW": pv["net_import_kW"],
            "pv_export_kW": pv["export_kW"],
            "pv_import_kW": pv["import_kW"],
        },
        axis=1,
        join="inner",
    ).dropna()
    if joined.empty:
        return ValidationLegResult(
            status="invalid_reference",
            metrics={},
            source_files=[str(no_pv_file.relative_to(repo_root)), str(pv_file.relative_to(repo_root))],
            output_files=[],
            warnings=["Fluvius no-PV and PV profiles have no overlapping timestamps."],
        )

    daily = pd.concat(
        {
            "no_pv_net_kW": _mean_daily_profile(joined["no_pv_net_kW"]),
            "pv_net_kW": _mean_daily_profile(joined["pv_net_kW"]),
            "pv_export_kW": _mean_daily_profile(joined["pv_export_kW"]),
            "pv_import_kW": _mean_daily_profile(joined["pv_import_kW"]),
        },
        axis=1,
    )
    profile_path = report_dir / "fluvius_pv_signature_mean_daily.csv"
    daily.to_csv(profile_path, index_label="local_time")
    output_files.append(str(profile_path.relative_to(repo_root)))
    figure_path = figure_dir / "fluvius_pv_signature_mean_daily.png"
    _plot_fluvius(figure_path, joined["no_pv_net_kW"], joined["pv_net_kW"], joined["pv_export_kW"])
    if figure_path.exists():
        output_files.append(str(figure_path.relative_to(repo_root)))

    daytime = joined.between_time("09:00", "16:59")
    metrics: dict[str, Any] = {
        "rows": int(len(joined)),
        "start": str(joined.index.min()),
        "end": str(joined.index.max()),
        "no_pv_mean_net_import_kW": float(joined["no_pv_net_kW"].mean()),
        "pv_mean_net_import_kW": float(joined["pv_net_kW"].mean()),
        "pv_minus_no_pv_mean_net_import_kW": float(joined["pv_net_kW"].mean() - joined["no_pv_net_kW"].mean()),
        "pv_daytime_mean_export_kW": float(daytime["pv_export_kW"].mean()) if not daytime.empty else None,
        "pv_p95_export_kW": float(joined["pv_export_kW"].quantile(0.95)),
        "pv_export_positive_fraction": float((joined["pv_export_kW"] > 0.0).mean()),
    }

    model_profile = _resolve_path(repo_root, cfg.get("model_net_load_file"))
    if model_profile and model_profile.exists():
        model_net_w = _load_power_profile(model_profile, value_candidates=["P_el_net_grid_W", "P_el_grid_import_W", "model_net_W", "net_load_W"])
        model_net_kw = model_net_w / 1000.0
        model_daily = _mean_daily_profile(model_net_kw)
        aligned_daily = pd.concat([model_daily, daily["pv_net_kW"]], axis=1, join="inner").dropna()
        if aligned_daily.empty:
            warnings.append("Configured model net-load file has no daily-profile overlap with Fluvius PV reference.")
        else:
            metrics["model_vs_fluvius_pv_mean_daily_rmse_kW"] = _rmse(aligned_daily.iloc[:, 0], aligned_daily.iloc[:, 1])
            metrics["model_vs_fluvius_pv_mean_daily_correlation"] = _correlation(
                aligned_daily.iloc[:, 0],
                aligned_daily.iloc[:, 1],
            )
        status = "model_reference_comparison"
        source_files = [str(no_pv_file.relative_to(repo_root)), str(pv_file.relative_to(repo_root)), str(model_profile.relative_to(repo_root))]
    else:
        status = "reference_ingested"
        source_files = [str(no_pv_file.relative_to(repo_root)), str(pv_file.relative_to(repo_root))]
        warnings.append(
            "No model net-load profile is configured; Fluvius validation is currently residential-signature/reference-only."
        )

    return ValidationLegResult(
        status=status,
        metrics=metrics,
        source_files=source_files,
        output_files=output_files,
        warnings=warnings,
    )


def _report_lines(results: Mapping[str, ValidationLegResult]) -> list[str]:
    lines = [
        "# model_v3 PV Validation Triangle",
        "",
        "This report is generated from local repository files and does not run scenario-tree simulations.",
        "",
        "## Status Summary",
        "",
        "| leg | status | source files | warnings |",
        "| --- | --- | --- | --- |",
    ]
    for name, result in results.items():
        sources = "<br>".join(f"`{path}`" for path in result.source_files) if result.source_files else "none"
        warnings = "<br>".join(result.warnings) if result.warnings else "none"
        lines.append(f"| {name} | `{result.status}` | {sources} | {warnings} |")
    lines.extend(["", "## Metrics", ""])
    for name, result in results.items():
        lines.extend([f"### {name}", ""])
        if not result.metrics:
            lines.append("No metrics were computed.")
        else:
            lines.append("| metric | value |")
            lines.append("| --- | ---: |")
            for key, value in sorted(result.metrics.items()):
                if isinstance(value, float):
                    rendered = "nan" if np.isnan(value) else f"{value:.6g}"
                else:
                    rendered = str(value)
                lines.append(f"| `{key}` | {rendered} |")
        if result.output_files:
            lines.extend(["", "Output files:"])
            lines.extend(f"- `{path}`" for path in result.output_files)
        lines.append("")
    lines.extend(
        [
            "## Interpretation Guardrails",
            "",
            "- `model_reference_comparison` means both a reference and model profile were available for that leg.",
            "- `reference_ingested` means the reference data were loaded and summarized, but no matched model output profile was configured.",
            "- PVGIS validates the physical irradiance-to-PV conversion for a representative 1 kWp system.",
            "- Elia ODS032 validates Belgian aggregate PV generation only after a matched model capacity-factor profile is provided.",
            "- Fluvius validates the residential PV net-load signature only after a matched model household/cohort net-load profile is provided.",
        ]
    )
    return lines


def run_validation(
    *,
    repo_root: Path,
    config_path: Path,
    download_elia: bool = False,
    skip_fluvius: bool = False,
) -> dict[str, Any]:
    config = _load_yaml(config_path)
    pv_cfg = dict(dict(dict(config.get("validation", {})).get("technology", {})).get("pv", config.get("pv", {})))
    outputs_cfg = dict(pv_cfg.get("outputs", {}))
    report_dir = _resolve_path(repo_root, outputs_cfg.get("report_dir", "reports/model_v3/validation/technology/pv"))
    figure_dir = _resolve_path(repo_root, outputs_cfg.get("figure_dir", "figures/model_v3/validation/technology/pv"))
    assert report_dir is not None and figure_dir is not None
    report_dir.mkdir(parents=True, exist_ok=True)
    figure_dir.mkdir(parents=True, exist_ok=True)

    results = {
        "pvgis_physical_reference": validate_pvgis_leg(repo_root, dict(pv_cfg.get("pvgis", {})), report_dir, figure_dir),
        "elia_ods032_belgian_generation": validate_elia_leg(
            repo_root,
            dict(pv_cfg.get("elia", {})),
            report_dir,
            figure_dir,
            force_download=download_elia,
        ),
    }
    if skip_fluvius:
        results["fluvius_residential_signature"] = ValidationLegResult(
            status="skipped",
            metrics={},
            source_files=[],
            output_files=[],
            warnings=["Skipped by CLI option."],
        )
    else:
        results["fluvius_residential_signature"] = validate_fluvius_leg(
            repo_root,
            dict(pv_cfg.get("fluvius", {})),
            report_dir,
            figure_dir,
        )

    serialised = {
        "config_path": str(config_path.relative_to(repo_root)) if config_path.exists() and config_path.is_relative_to(repo_root) else str(config_path),
        "legs": {
            name: {
                "status": result.status,
                "metrics": result.metrics,
                "source_files": result.source_files,
                "output_files": result.output_files,
                "warnings": result.warnings,
            }
            for name, result in results.items()
        },
    }
    metrics_path = report_dir / "technology_pv_validation_triangle_metrics.json"
    report_path = report_dir / "technology_pv_validation_triangle_report.md"
    serialised["metrics_path"] = str(metrics_path.relative_to(repo_root))
    serialised["report_path"] = str(report_path.relative_to(repo_root))
    _write_json(metrics_path, serialised)
    _write_markdown(report_path, _report_lines(results))
    return serialised


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run model_v3 PV validation triangle ingestion and metrics.")
    parser.add_argument("--repo-root", default=".", help="Repository root. Defaults to current directory.")
    parser.add_argument("--config", default=DEFAULT_CONFIG, help=f"PV validation config. Defaults to {DEFAULT_CONFIG}.")
    parser.add_argument("--download-elia", action="store_true", help="Download/cache the configured Elia ODS032 CSV before validation.")
    parser.add_argument("--skip-fluvius", action="store_true", help="Skip loading large Fluvius representative profile files.")
    parser.add_argument("--print-summary", action="store_true", help="Print concise validation status summary.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    repo_root = _repo_root_from_args(args.repo_root)
    config_path = _resolve_path(repo_root, args.config)
    assert config_path is not None
    result = run_validation(
        repo_root=repo_root,
        config_path=config_path,
        download_elia=bool(args.download_elia),
        skip_fluvius=bool(args.skip_fluvius),
    )
    if args.print_summary:
        print("PV validation triangle complete.")
        for name, leg in result["legs"].items():
            print(f"{name}: {leg['status']} warnings={len(leg['warnings'])}")
        print(f"Report: {result['report_path']}")
        print(f"Metrics: {result['metrics_path']}")
        print("Simulations run: 0")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
