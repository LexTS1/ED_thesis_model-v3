"""Run a minimum defensible EV validation workflow for model_v3.

The runner validates the EV charging submodel as a technology layer. It does
not execute scenario-tree leaves. The primary comparison is the Fluvius
representative EV residential signature, calculated as EV-no-PV minus
no-EV/no-PV import, against the model_v3 EV home-charging profile.
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
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np
import pandas as pd
import yaml

from model_v3.systems.distributed_energy import (
    annual_ev_home_charging_kwh,
    build_ev_charging_profile,
)

try:  # pragma: no cover - exercised indirectly when matplotlib is available.
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
except Exception:  # pragma: no cover - graceful fallback for minimal installs.
    plt = None


BELGIUM_TZ = "Europe/Brussels"
DEFAULT_CONFIG = "config/validation/technology_ev.yaml"
DEFAULT_REPORT_DIR = "reports/model_v3/validation/technology/ev"
DEFAULT_FIGURE_DIR = "figures/model_v3/validation/technology/ev"
FLUVIUS_INTERVAL_HOURS = 0.25


@dataclass(frozen=True)
class EVValidationResult:
    """Structured EV-validation result."""

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


def _read_csv_sniffed(path: Path) -> pd.DataFrame:
    with path.open("r", encoding="utf-8-sig", errors="replace") as handle:
        sample = handle.read(4096)
    try:
        delimiter = csv.Sniffer().sniff(sample, delimiters=",;\t").delimiter
    except csv.Error:
        delimiter = ";" if sample.count(";") > sample.count(",") else ","
    return pd.read_csv(path, sep=delimiter, low_memory=False)


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


def _infer_step_hours(index: pd.DatetimeIndex, *, fallback: float = 1.0) -> float:
    if len(index) < 2:
        return fallback
    deltas = index.to_series().sort_values().diff().dropna().dt.total_seconds() / 3600.0
    if deltas.empty:
        return fallback
    return float(deltas.median())


def _energy_kwh(power_w: pd.Series, *, step_hours: float | None = None, clip_lower: bool = True) -> float:
    ordered = power_w.sort_index().dropna()
    if ordered.empty:
        return 0.0
    hours = _infer_step_hours(pd.DatetimeIndex(ordered.index)) if step_hours is None else float(step_hours)
    values = ordered.clip(lower=0.0) if clip_lower else ordered
    return float((values / 1000.0 * hours).sum())


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


def _bias(model: pd.Series, reference: pd.Series) -> float:
    aligned = pd.concat([model, reference], axis=1, join="inner").dropna()
    if aligned.empty:
        return float("nan")
    return float((aligned.iloc[:, 0] - aligned.iloc[:, 1]).mean())


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
    local = series.copy().sort_index()
    if local.index.tz is None:
        local.index = pd.DatetimeIndex(local.index).tz_localize(BELGIUM_TZ)
    else:
        local.index = pd.DatetimeIndex(local.index).tz_convert(BELGIUM_TZ)
    keys = [pd.DatetimeIndex(local.index).hour, pd.DatetimeIndex(local.index).minute]
    profile = local.groupby(keys).mean()
    profile.index = [f"{hour:02d}:{minute:02d}" for hour, minute in profile.index]
    return profile


def _profile_peak_hour(profile: pd.Series) -> str | None:
    if profile.empty:
        return None
    return str(profile.idxmax())


def _evening_peak_kw(profile: pd.Series) -> float:
    if profile.empty:
        return float("nan")
    evening = profile.loc[
        [
            label
            for label in profile.index
            if 17 <= int(str(label).split(":", maxsplit=1)[0]) <= 22
        ]
    ]
    if evening.empty:
        return float("nan")
    return float(evening.max())


def _load_fluvius_net_import_kw(path: Path) -> tuple[pd.Series, pd.Series, pd.Series]:
    frame = _read_csv_sniffed(path)
    timestamp_column = _find_column(frame.columns, ["Datum_Startuur", "timestamp", "datetime", "DateTime"])
    import_column = _find_column(frame.columns, ["Volume_Afname_KWh", "import_kWh", "afname_kwh"])
    export_column = _find_column(frame.columns, ["Volume_Injectie_KWh", "export_kWh", "injectie_kwh"])
    if timestamp_column is None or import_column is None:
        raise ValueError(f"Could not detect Fluvius timestamp/import columns in `{path}`.")
    timestamps = pd.to_datetime(frame[timestamp_column], utc=True, errors="coerce").dt.tz_convert(BELGIUM_TZ)
    import_kwh = pd.to_numeric(frame[import_column], errors="coerce").fillna(0.0).clip(lower=0.0)
    if export_column is None:
        export_kwh = pd.Series(0.0, index=frame.index)
    else:
        export_kwh = pd.to_numeric(frame[export_column], errors="coerce").fillna(0.0).clip(lower=0.0)
    index = pd.DatetimeIndex(timestamps)
    import_kw = pd.Series(import_kwh.to_numpy(dtype=float) / FLUVIUS_INTERVAL_HOURS, index=index, dtype=float)
    export_kw = pd.Series(export_kwh.to_numpy(dtype=float) / FLUVIUS_INTERVAL_HOURS, index=index, dtype=float)
    net_kw = import_kw - export_kw
    valid = ~net_kw.index.isna()
    import_kw = import_kw.loc[valid].sort_index()
    export_kw = export_kw.loc[valid].sort_index()
    net_kw = net_kw.loc[valid].sort_index()
    if net_kw.index.has_duplicates:
        import_kw = import_kw.groupby(level=0).mean().sort_index()
        export_kw = export_kw.groupby(level=0).mean().sort_index()
        net_kw = net_kw.groupby(level=0).mean().sort_index()
    return net_kw, import_kw, export_kw


def _load_model_ev_profile(path: Path) -> pd.Series:
    frame = _read_csv_sniffed(path)
    timestamp_column = _find_column(frame.columns, ["timestamp", "datetime", "time", "date_time"])
    value_column = _find_column(
        frame.columns,
        [
            "P_el_ev_charging_W",
            "P_ev_charging_W",
            "ev_charging_W",
            "P_el_net_grid_W",
            "P_el_grid_import_W",
        ],
    )
    if timestamp_column is None or value_column is None:
        raise ValueError(f"Could not detect model timestamp/EV columns in `{path}`.")
    timestamps = pd.to_datetime(frame[timestamp_column], utc=True, errors="coerce").dt.tz_convert(BELGIUM_TZ)
    values_w = pd.to_numeric(frame[value_column], errors="coerce").fillna(0.0)
    series = pd.Series(values_w.to_numpy(dtype=float) / 1000.0, index=pd.DatetimeIndex(timestamps), dtype=float)
    return series.loc[~series.index.isna()].dropna().sort_index()


def _technology_ev_config(config: Mapping[str, Any]) -> dict[str, Any]:
    return dict(dict(dict(config.get("validation", {})).get("technology", {})).get("ev", {}))


def _model_ev_cfg(repo_root: Path, model_cfg: Mapping[str, Any]) -> dict[str, Any]:
    configured = dict(model_cfg.get("ev_config", {}))
    if configured:
        return configured

    technology_inputs_file = _resolve_path(repo_root, model_cfg.get("technology_inputs_file"))
    if technology_inputs_file is not None and technology_inputs_file.exists():
        data = _load_yaml(technology_inputs_file)
        ev_cfg = dict(dict(data.get("mobility", {})).get("ev", {}))
        if ev_cfg:
            return ev_cfg

    thesis_config_file = _resolve_path(repo_root, model_cfg.get("model_config_file"))
    if thesis_config_file is not None and thesis_config_file.exists():
        data = _load_yaml(thesis_config_file)
        ev_cfg = dict(dict(data.get("mobility", {})).get("ev", {}))
        if ev_cfg:
            return ev_cfg

    return {
        "annual_use": {
            "km_per_year": {"base": 15000},
            "specific_consumption_kwh_per_100km": {"base": 14.2},
        },
        "charging": {
            "home_charging_probability": {"base": 0.70},
            "charger_power_kw": {"base": 7.4},
            "uncontrolled_arrival_window": {"start_hour": 17, "end_hour": 22},
        },
    }


def _write_model_ev_profile(repo_root: Path, cfg: Mapping[str, Any], destination: Path) -> tuple[pd.Series, Path]:
    cohort_size = max(int(cfg.get("cohort_size", 100) or 100), 1)
    seed = int(cfg.get("seed", 42) or 42)
    reference_year = int(cfg.get("reference_year", 2024) or 2024)
    target_resolution_seconds = int(cfg.get("target_resolution_seconds", 3600) or 3600)
    periods = int(cfg.get("periods", 8760) or 8760)
    freq = pd.to_timedelta(target_resolution_seconds, unit="s")
    timestamps = pd.date_range(
        f"{reference_year}-01-01T00:00:00",
        periods=periods,
        freq=freq,
        tz=BELGIUM_TZ,
    )
    ev_cfg = _model_ev_cfg(repo_root, cfg)
    if bool(cfg.get("jitter_households", False)):
        household_profiles = np.vstack(
            [
                np.asarray(
                    build_ev_charging_profile(
                        timestamps,
                        ev_cfg,
                        has_ev=True,
                        random_seed=seed + household_index,
                    ),
                    dtype=float,
                )
                for household_index in range(cohort_size)
            ]
        )
    else:
        per_ev_w = np.asarray(build_ev_charging_profile(timestamps, ev_cfg, has_ev=True), dtype=float)
        household_profiles = np.tile(per_ev_w, (cohort_size, 1))
    mean_w_per_ev = household_profiles.mean(axis=0)
    output = pd.DataFrame(
        {
            "timestamp": timestamps.astype(str),
            "P_el_ev_charging_W": mean_w_per_ev,
            "P_el_net_grid_W": mean_w_per_ev,
            "P_el_grid_import_W": np.clip(mean_w_per_ev, 0.0, None),
            "P_el_grid_export_W": np.zeros(len(timestamps)),
            "cohort_size": cohort_size,
            "seed": seed,
            "reference_year": reference_year,
            "profile_kind": "technology_ev_increment_per_active_ev_household",
        }
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    output.to_csv(destination, index=False)
    return pd.Series(mean_w_per_ev / 1000.0, index=timestamps, dtype=float), destination


def _write_model_sensitivity_profile(
    path: Path,
    profile_kw: pd.Series,
    *,
    target_annual_kwh: float,
    scale_factor: float,
    label: str,
) -> Path:
    output = pd.DataFrame(
        {
            "timestamp": pd.DatetimeIndex(profile_kw.index).astype(str),
            "P_el_ev_charging_W": profile_kw.to_numpy(dtype=float) * 1000.0,
            "P_el_net_grid_W": profile_kw.to_numpy(dtype=float) * 1000.0,
            "P_el_grid_import_W": np.clip(profile_kw.to_numpy(dtype=float) * 1000.0, 0.0, None),
            "P_el_grid_export_W": np.zeros(len(profile_kw)),
            "target_annual_kWh_per_active_EV": float(target_annual_kwh),
            "scale_factor_vs_base_model": float(scale_factor),
            "profile_kind": f"technology_ev_increment_sensitivity_{label}",
        }
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    output.to_csv(path, index=False)
    return path


def _write_fluvius_signature(path: Path, signature_kw: pd.Series, base_kw: pd.Series, ev_kw: pd.Series) -> Path:
    frame = pd.DataFrame(
        {
            "time_of_day": signature_kw.index,
            "fluvius_ev_increment_kW": signature_kw.to_numpy(dtype=float),
            "fluvius_no_ev_net_import_kW": base_kw.reindex(signature_kw.index).to_numpy(dtype=float),
            "fluvius_ev_net_import_kW": ev_kw.reindex(signature_kw.index).to_numpy(dtype=float),
        }
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False)
    return path


def _write_diversity_table(
    path: Path,
    ev_cfg: Mapping[str, Any],
    timestamps: Sequence[pd.Timestamp],
    *,
    seed: int,
    jitter_households: bool,
) -> Path:
    rows = []
    for count in (1, 5, 10, 25, 50, 100):
        if jitter_households:
            profiles = np.vstack(
                [
                    np.asarray(
                        build_ev_charging_profile(
                            timestamps,
                            ev_cfg,
                            has_ev=True,
                            random_seed=seed + household_index,
                        ),
                        dtype=float,
                    )
                    for household_index in range(count)
                ]
            )
        else:
            per_ev_w = np.asarray(build_ev_charging_profile(timestamps, ev_cfg, has_ev=True), dtype=float)
            profiles = np.tile(per_ev_w, (count, 1))
        individual_peak_sum_kw = float(np.max(profiles, axis=1).sum() / 1000.0)
        aggregate_peak_kw = float(np.max(profiles.sum(axis=0)) / 1000.0)
        rows.append(
            {
                "ev_households": count,
                "sum_individual_peak_kW": individual_peak_sum_kw,
                "aggregate_peak_kW": aggregate_peak_kw,
                "diversity_factor": individual_peak_sum_kw / max(aggregate_peak_kw, 1e-9),
            }
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def _plot_profiles(
    path: Path,
    reference: pd.Series,
    model: pd.Series,
    sensitivity: pd.Series | None = None,
) -> None:
    if plt is None or reference.empty or model.empty:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    series = {
        "Fluvius EV increment kW": reference,
        "model EV charging kW": model,
    }
    if sensitivity is not None and not sensitivity.empty:
        series["model EV charging kW, 2600 kWh/y sensitivity"] = sensitivity
    frame = pd.concat(series, axis=1, join="inner").dropna()
    if frame.empty:
        return
    fig, ax = plt.subplots(figsize=(10, 4))
    frame.plot(ax=ax, linewidth=1.2)
    ax.set_title("EV validation: Fluvius EV signature vs model EV charging")
    ax.set_ylabel("Mean power (kW per active EV household/reference category)")
    ax.set_xlabel("Local time of day")
    ax.tick_params(axis="x", rotation=90)
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)


def _plot_diversity(path: Path, diversity_csv: Path) -> None:
    if plt is None or not diversity_csv.exists():
        return
    frame = pd.read_csv(diversity_csv)
    if frame.empty:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(7, 4))
    ax.plot(frame["ev_households"], frame["diversity_factor"], marker="o")
    ax.set_title("Model EV diversity factor by active EV count")
    ax.set_ylabel("Diversity factor")
    ax.set_xlabel("Active EV households")
    ax.grid(True, alpha=0.25)
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)


def run_validation(
    repo_root: Path,
    config: Mapping[str, Any],
    *,
    generate_model_profiles: bool,
    force_model_profiles: bool = False,
    cohort_size_override: int | None = None,
    seed_override: int | None = None,
    report_dir_override: Path | None = None,
    figure_dir_override: Path | None = None,
) -> EVValidationResult:
    cfg = _technology_ev_config(config)
    fluvius_cfg = dict(cfg.get("fluvius", {}))
    model_cfg = dict(cfg.get("model", {}))
    output_cfg = dict(cfg.get("outputs", {}))
    if cohort_size_override is not None:
        model_cfg["cohort_size"] = int(cohort_size_override)
    if seed_override is not None:
        model_cfg["seed"] = int(seed_override)

    report_dir = report_dir_override or _resolve_path(repo_root, output_cfg.get("report_dir", DEFAULT_REPORT_DIR))
    figure_dir = figure_dir_override or _resolve_path(repo_root, output_cfg.get("figure_dir", DEFAULT_FIGURE_DIR))
    assert report_dir is not None
    assert figure_dir is not None
    report_dir.mkdir(parents=True, exist_ok=True)
    figure_dir.mkdir(parents=True, exist_ok=True)

    warnings: list[str] = []
    output_files: list[str] = []
    source_files: list[str] = []
    base_path = _resolve_path(repo_root, fluvius_cfg.get("base_no_pv_file"))
    ev_path = _resolve_path(repo_root, fluvius_cfg.get("ev_no_pv_file"))
    if base_path is None or not base_path.exists():
        return EVValidationResult(
            status="missing_reference",
            metrics={},
            source_files=[],
            output_files=[],
            warnings=[f"Missing Fluvius no-EV/no-PV reference file: {base_path}"],
        )
    if ev_path is None or not ev_path.exists():
        return EVValidationResult(
            status="missing_reference",
            metrics={},
            source_files=[str(base_path.relative_to(repo_root))],
            output_files=[],
            warnings=[f"Missing Fluvius EV/no-PV reference file: {ev_path}"],
        )

    source_files.extend([str(base_path.relative_to(repo_root)), str(ev_path.relative_to(repo_root))])
    base_net_kw, base_import_kw, _base_export_kw = _load_fluvius_net_import_kw(base_path)
    ev_net_kw, ev_import_kw, _ev_export_kw = _load_fluvius_net_import_kw(ev_path)
    reference_increment_kw_ts = (ev_net_kw - base_net_kw).dropna().sort_index()
    reference_daily = _mean_daily_profile(reference_increment_kw_ts)
    base_daily = _mean_daily_profile(base_net_kw)
    ev_daily = _mean_daily_profile(ev_net_kw)
    signature_path = report_dir / "fluvius_ev_signature_mean_daily.csv"
    output_files.append(str(_write_fluvius_signature(signature_path, reference_daily, base_daily, ev_daily).relative_to(repo_root)))

    model_profile_path = _resolve_path(repo_root, model_cfg.get("ev_profile_file"))
    if model_profile_path is None:
        model_profile_path = report_dir / f"model_ev_charging_profile_{int(model_cfg.get('reference_year', 2024) or 2024)}.csv"

    model_profile_kw = pd.Series(dtype=float)
    model_profile_generated = False
    if model_profile_path.exists() and not force_model_profiles:
        model_profile_kw = _load_model_ev_profile(model_profile_path)
        source_files.append(str(model_profile_path.relative_to(repo_root)))
    elif generate_model_profiles or force_model_profiles:
        model_profile_kw, written_path = _write_model_ev_profile(repo_root, model_cfg, model_profile_path)
        model_profile_generated = True
        output_files.append(str(written_path.relative_to(repo_root)))
    else:
        warnings.append(
            "Model EV profile is missing; runner ingested the Fluvius reference only. "
            "Use --generate-model-profiles to create the model comparison profile."
        )

    ev_cfg = _model_ev_cfg(repo_root, model_cfg)
    annual_kwh_per_ev = annual_ev_home_charging_kwh(ev_cfg)
    model_daily = _mean_daily_profile(model_profile_kw)
    reference_signed_kwh = _energy_kwh(reference_increment_kw_ts * 1000.0, clip_lower=False)
    reference_positive_kwh = _energy_kwh(reference_increment_kw_ts * 1000.0, clip_lower=True)
    model_annual_kwh = _energy_kwh(model_profile_kw * 1000.0, clip_lower=True)
    sensitivity_cfg = dict(model_cfg.get("annual_energy_sensitivity", {}))
    sensitivity_profile_kw = pd.Series(dtype=float)
    sensitivity_daily = pd.Series(dtype=float)
    sensitivity_label = str(sensitivity_cfg.get("label", "annual_energy_sensitivity")).replace(" ", "_")
    sensitivity_target_kwh = float(sensitivity_cfg.get("annual_kWh_per_active_EV", 0.0) or 0.0)
    sensitivity_scale = float("nan")
    if bool(sensitivity_cfg.get("enabled", False)) and not model_profile_kw.empty and model_annual_kwh > 0.0:
        if sensitivity_target_kwh <= 0.0:
            warnings.append("EV annual-energy sensitivity is enabled but target annual_kWh_per_active_EV is not positive.")
        else:
            sensitivity_scale = sensitivity_target_kwh / model_annual_kwh
            sensitivity_profile_kw = model_profile_kw * sensitivity_scale
            sensitivity_daily = _mean_daily_profile(sensitivity_profile_kw)
            reference_year = int(model_cfg.get("reference_year", 2024) or 2024)
            target_token = int(round(sensitivity_target_kwh))
            sensitivity_path = report_dir / f"model_ev_charging_profile_{reference_year}_sensitivity_{target_token}kwh.csv"
            output_files.append(
                str(
                    _write_model_sensitivity_profile(
                        sensitivity_path,
                        sensitivity_profile_kw,
                        target_annual_kwh=sensitivity_target_kwh,
                        scale_factor=sensitivity_scale,
                        label=sensitivity_label,
                    ).relative_to(repo_root)
                )
            )
    diversity_path = report_dir / "ev_diversity_by_count.csv"
    if not model_profile_kw.empty:
        timestamps = tuple(pd.DatetimeIndex(model_profile_kw.index))
        output_files.append(
            str(
                _write_diversity_table(
                    diversity_path,
                    ev_cfg,
                    timestamps,
                    seed=int(model_cfg.get("seed", 42) or 42),
                    jitter_households=bool(model_cfg.get("jitter_households", False)),
                ).relative_to(repo_root)
            )
        )
        diversity_figure = figure_dir / "ev_diversity_by_count.png"
        _plot_diversity(diversity_figure, diversity_path)
        if diversity_figure.exists():
            output_files.append(str(diversity_figure.relative_to(repo_root)))

    figure_path = figure_dir / "model_vs_fluvius_ev_effect_mean_daily.png"
    _plot_profiles(figure_path, reference_daily, model_daily, sensitivity_daily)
    if figure_path.exists():
        output_files.append(str(figure_path.relative_to(repo_root)))

    metrics: dict[str, Any] = {
        "status": "model_reference_comparison" if not model_profile_kw.empty else "reference_ingested",
        "validation_scope": "technology_ev_increment_signature",
        "fluvius_base_rows": int(len(base_net_kw)),
        "fluvius_ev_rows": int(len(ev_net_kw)),
        "fluvius_reference_start": str(reference_increment_kw_ts.index.min()) if not reference_increment_kw_ts.empty else None,
        "fluvius_reference_end": str(reference_increment_kw_ts.index.max()) if not reference_increment_kw_ts.empty else None,
        "reference_ev_effect_signed_kWh_per_meter_year": reference_signed_kwh,
        "reference_ev_effect_positive_kWh_per_meter_year": reference_positive_kwh,
        "reference_daily_ev_effect_signed_kWh_per_meter": reference_signed_kwh / 366.0,
        "reference_daily_ev_effect_positive_kWh_per_meter": reference_positive_kwh / 366.0,
        "reference_peak_charging_hour": _profile_peak_hour(reference_daily),
        "reference_evening_peak_magnitude_kW": _evening_peak_kw(reference_daily),
        "model_ev_profile_file": str(model_profile_path.relative_to(repo_root)),
        "model_profile_generated": model_profile_generated,
        "model_cohort_size_active_ev_households": int(model_cfg.get("cohort_size", 100) or 100),
        "model_annual_kWh_per_active_EV": model_annual_kwh,
        "model_configured_annual_home_charging_kWh_per_active_EV": annual_kwh_per_ev,
        "model_annual_energy_gap_vs_reference_kWh": model_annual_kwh - reference_positive_kwh,
        "model_annual_energy_gap_vs_reference_pct": (
            100.0 * (model_annual_kwh - reference_positive_kwh) / reference_positive_kwh
            if reference_positive_kwh
            else float("nan")
        ),
        "model_annual_energy_formula": "km_per_year * specific_consumption_kwh_per_100km / 100 * home_charging_probability",
        "model_daily_kWh_per_active_EV": model_annual_kwh / 365.0 if model_annual_kwh else 0.0,
        "model_peak_charging_hour": _profile_peak_hour(model_daily),
        "model_evening_peak_magnitude_kW": _evening_peak_kw(model_daily),
        "model_vs_reference_mean_daily_rmse_kW": _rmse(model_daily, reference_daily),
        "model_vs_reference_mean_daily_mae_kW": _mae(model_daily, reference_daily),
        "model_vs_reference_mean_daily_bias_kW": _bias(model_daily, reference_daily),
        "model_vs_reference_mean_daily_correlation": _correlation(model_daily, reference_daily),
        "ku_leuven_status": "secondary_context_only",
    }
    if not sensitivity_profile_kw.empty:
        sensitivity_annual_kwh = _energy_kwh(sensitivity_profile_kw * 1000.0, clip_lower=True)
        metrics.update(
            {
                "sensitivity_enabled": True,
                "sensitivity_label": sensitivity_label,
                "sensitivity_target_annual_kWh_per_active_EV": sensitivity_target_kwh,
                "sensitivity_annual_kWh_per_active_EV": sensitivity_annual_kwh,
                "sensitivity_scale_factor_vs_base_model": sensitivity_scale,
                "sensitivity_daily_kWh_per_active_EV": sensitivity_annual_kwh / 365.0,
                "sensitivity_peak_charging_hour": _profile_peak_hour(sensitivity_daily),
                "sensitivity_evening_peak_magnitude_kW": _evening_peak_kw(sensitivity_daily),
                "sensitivity_vs_reference_mean_daily_rmse_kW": _rmse(sensitivity_daily, reference_daily),
                "sensitivity_vs_reference_mean_daily_mae_kW": _mae(sensitivity_daily, reference_daily),
                "sensitivity_vs_reference_mean_daily_bias_kW": _bias(sensitivity_daily, reference_daily),
                "sensitivity_vs_reference_mean_daily_correlation": _correlation(sensitivity_daily, reference_daily),
                "sensitivity_annual_energy_gap_vs_reference_kWh": sensitivity_annual_kwh - reference_positive_kwh,
                "sensitivity_annual_energy_gap_vs_reference_pct": (
                    100.0 * (sensitivity_annual_kwh - reference_positive_kwh) / reference_positive_kwh
                    if reference_positive_kwh
                    else float("nan")
                ),
            }
        )
    else:
        metrics["sensitivity_enabled"] = False
    if diversity_path.exists():
        diversity = pd.read_csv(diversity_path)
        if not diversity.empty:
            metrics["model_diversity_factor_at_100_active_EVs"] = float(
                diversity.loc[diversity["ev_households"] == 100, "diversity_factor"].iloc[0]
            )

    metrics_path = report_dir / "technology_ev_validation_metrics.json"
    report_path = report_dir / "technology_ev_validation_report.md"
    _write_json(metrics_path, metrics)
    output_files.append(str(metrics_path.relative_to(repo_root)))
    _write_markdown(
        report_path,
        _report_lines(
            metrics=metrics,
            source_files=source_files,
            output_files=output_files,
            warnings=warnings,
            model_cfg=model_cfg,
        ),
    )
    output_files.append(str(report_path.relative_to(repo_root)))
    return EVValidationResult(
        status=str(metrics["status"]),
        metrics=metrics,
        source_files=source_files,
        output_files=output_files,
        warnings=warnings,
    )


def _fmt(value: Any, digits: int = 3) -> str:
    if value is None:
        return "missing"
    try:
        if pd.isna(value):
            return "missing"
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return str(value)


def _report_lines(
    *,
    metrics: Mapping[str, Any],
    source_files: Sequence[str],
    output_files: Sequence[str],
    warnings: Sequence[str],
    model_cfg: Mapping[str, Any],
) -> list[str]:
    lines = [
        "# Model v3 EV Technology Validation",
        "",
        "## Validation Scope",
        "",
        "- classification: technology-level validation",
        "- primary reference: Fluvius representative residential EV/no-PV profile minus Fluvius representative no-EV/no-PV profile",
        "- model object being tested: `model_v3.systems.distributed_energy.build_ev_charging_profile`",
        "- scenario-tree simulations run: `0`",
        "- interpretation: this checks whether the EV home-charging increment has a plausible residential daily shape and annual energy scale. It is not external validation of the full household model.",
        "",
        "## Main Results",
        "",
        f"- status: `{metrics.get('status')}`",
        f"- Fluvius signed EV increment: `{_fmt(metrics.get('reference_ev_effect_signed_kWh_per_meter_year'), 1)}` kWh/reference meter/year",
        f"- Fluvius positive EV increment: `{_fmt(metrics.get('reference_ev_effect_positive_kWh_per_meter_year'), 1)}` kWh/reference meter/year",
        f"- model annual EV charging: `{_fmt(metrics.get('model_annual_kWh_per_active_EV'), 1)}` kWh/active EV/year",
        f"- model configured annual home charging: `{_fmt(metrics.get('model_configured_annual_home_charging_kWh_per_active_EV'), 1)}` kWh/active EV/year",
        f"- model annual-energy gap vs Fluvius positive increment: `{_fmt(metrics.get('model_annual_energy_gap_vs_reference_pct'), 1)}` %",
        f"- Fluvius peak EV-signature hour: `{metrics.get('reference_peak_charging_hour')}`",
        f"- model peak EV-charging hour: `{metrics.get('model_peak_charging_hour')}`",
        f"- mean-daily RMSE: `{_fmt(metrics.get('model_vs_reference_mean_daily_rmse_kW'))}` kW",
        f"- mean-daily correlation: `{_fmt(metrics.get('model_vs_reference_mean_daily_correlation'))}`",
        f"- diversity factor at 100 active EVs: `{_fmt(metrics.get('model_diversity_factor_at_100_active_EVs'))}`",
        "",
        "## Annual Energy Sensitivity",
        "",
        f"- sensitivity enabled: `{metrics.get('sensitivity_enabled')}`",
        f"- sensitivity target: `{_fmt(metrics.get('sensitivity_target_annual_kWh_per_active_EV'), 1)}` kWh/active EV/year",
        f"- sensitivity scale factor vs base model: `{_fmt(metrics.get('sensitivity_scale_factor_vs_base_model'))}`",
        f"- sensitivity annual-energy gap vs Fluvius positive increment: `{_fmt(metrics.get('sensitivity_annual_energy_gap_vs_reference_pct'), 1)}` %",
        f"- sensitivity mean-daily RMSE: `{_fmt(metrics.get('sensitivity_vs_reference_mean_daily_rmse_kW'))}` kW",
        "",
        "This sensitivity keeps the model's current charging timing shape but rescales annual active-EV home-charging energy to approximately 2600 kWh/year. It is useful for checking whether the Fluvius mismatch is mainly an annual-energy calibration issue. It is not a session-level EV model and should not be presented as a completed behavioural calibration.",
        "",
        "## Interpretation",
        "",
        "The Fluvius comparison is a residential signature check. It asks whether adding EV households changes the representative daily load in roughly the right hours and magnitude. It does not prove that individual charging sessions are calibrated, because Fluvius profiles are representative category profiles rather than open session-level charging events. The Fluvius increment should therefore be interpreted as a category-profile difference, not a directly observed kWh-per-individual-EV measurement.",
        "",
        "The current model EV profile is generated from annual EV energy allocated over a configured residential charging window. The minimum viable calibration uses a delayed overnight shape with household-level timing jitter. This improves the daily signature, but it is still not a session-level charging model.",
        "",
        "The annual active-EV home-charging energy is calculated as `km_per_year * specific_consumption_kwh_per_100km / 100 * home_charging_probability`. With the current base assumptions this is `15000 * 14.2 / 100 * 0.70 = 1491` kWh per active EV household per year.",
        "",
        "KU Leuven house 1 is useful context because its metadata lists an EV charger, but the available public file is whole-house import/export and is confounded with PV and heat-pump operation. It is therefore not used as the primary EV reference in this minimum workflow.",
        "",
        "## Configuration",
        "",
        f"- active EV households in generated model profile: `{model_cfg.get('cohort_size', 100)}`",
        f"- reference year for generated timestamps: `{model_cfg.get('reference_year', 2024)}`",
        f"- model profile file: `{metrics.get('model_ev_profile_file')}`",
        "",
        "## Source Files",
        "",
    ]
    lines.extend([f"- `{path}`" for path in source_files] or ["- none"])
    lines.extend(["", "## Output Files", ""])
    lines.extend([f"- `{path}`" for path in output_files] or ["- none"])
    lines.extend(["", "## Warnings", ""])
    lines.extend([f"- {warning}" for warning in warnings] or ["- none"])
    lines.extend(
        [
            "",
            "## Completion Requirements",
            "",
            "To make EV validation stronger, add a session-level or residential EV reference with plug-in time, connection duration, energy per session, and charger power. ElaadNL can be used as a European behavioural proxy; Belgian session-level data would be stronger if available.",
        ]
    )
    return lines


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run model_v3 EV technology validation.")
    parser.add_argument("--repo-root", default=".", help="Repository root. Defaults to current directory.")
    parser.add_argument("--config", default=DEFAULT_CONFIG, help=f"Validation config path. Defaults to {DEFAULT_CONFIG}.")
    parser.add_argument(
        "--generate-model-profiles",
        action="store_true",
        help="Generate the model EV charging profile when the configured profile file is missing.",
    )
    parser.add_argument(
        "--force-model-profiles",
        action="store_true",
        help="Regenerate the model EV charging profile even if the configured CSV already exists.",
    )
    parser.add_argument("--cohort-size", type=int, default=None, help="Override active EV household count.")
    parser.add_argument("--seed", type=int, default=None, help="Override model profile seed.")
    parser.add_argument("--report-dir", default=None, help="Override report directory.")
    parser.add_argument("--figure-dir", default=None, help="Override figure directory.")
    parser.add_argument("--print-summary", action="store_true", help="Print a compact summary.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo_root = _repo_root_from_args(args.repo_root)
    config_path = _resolve_path(repo_root, args.config)
    config = _load_yaml(config_path) if config_path is not None else {}
    report_dir = _resolve_path(repo_root, args.report_dir)
    figure_dir = _resolve_path(repo_root, args.figure_dir)
    result = run_validation(
        repo_root,
        config,
        generate_model_profiles=bool(args.generate_model_profiles),
        force_model_profiles=bool(args.force_model_profiles),
        cohort_size_override=args.cohort_size,
        seed_override=args.seed,
        report_dir_override=report_dir,
        figure_dir_override=figure_dir,
    )
    if args.print_summary:
        print("EV validation complete.")
        print(f"Status: {result.status}")
        print(f"Report: {DEFAULT_REPORT_DIR}/technology_ev_validation_report.md")
        print(f"Figures: {DEFAULT_FIGURE_DIR}/")
        print(f"Scenario simulations run: 0")
        print(f"Warnings: {len(result.warnings)}")
    return 0 if result.status not in {"missing_reference"} else 2


if __name__ == "__main__":  # pragma: no cover - CLI entrypoint.
    raise SystemExit(main())
