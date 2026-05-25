"""Validation runner against an aggregate demand reference profile."""

from __future__ import annotations

if __package__ in {None, ""}:
    import sys
    from pathlib import Path as _Path

    sys.path.append(str(_Path(__file__).resolve().parents[3]))

import logging
from time import perf_counter
from pathlib import Path
from typing import Any, Mapping

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from model_v3.adapters.fluvius_loader import aggregate_fluvius_profiles, load_fluvius_profiles
from model_v3.output.persistence import validation_output_dir, write_frame_csv, write_json
from model_v3.output.persistence import ensure_dir, output_root
from model_v3.validation.acceptance_criteria import check_acceptance
from model_v3.validation.core.metrics_distribution import compute_distribution_metrics, compute_ldc
from model_v3.validation.core.metrics_mean import compute_mean_metrics
from model_v3.validation.core.metrics_temporal import compute_temporal_metrics
from model_v3.validation.core.metrics_variance import compute_diurnal_variance, compute_variance_metrics
from model_v3.validation.runners.runner_utils import (
    apply_quick_validation_mode,
    artifact_interpretation_lines,
    build_runner_cli,
    configure_runner_logging,
    format_elapsed_summary,
    quick_external_row_cap,
    runtime_context_lines,
    validation_type_lines,
)
from model_v3.validation.runners.model_runner import run_validation_model
from model_v3.validation.runners.validate_against_synthetic import (
    _generate_plots,
    _summary_table,
    _write_report,
    build_acceptance_metrics,
)
from model_v3.validation.utils.alignment import align_timeseries
from model_v3.validation.utils.independence import assess_validation_independence
from model_v3.validation.utils.reference_profile import load_aggregate_reference_profile

LOGGER = logging.getLogger(__name__)

_BELGIUM_TZ = "Europe/Brussels"


def _normalized_series(series: pd.Series) -> pd.Series:
    """Normalize a demand series by its mean power."""

    mean_value = float(series.mean())
    if abs(mean_value) <= 1e-9:
        return pd.Series(np.zeros(len(series)), index=series.index, dtype=float)
    return series / mean_value


def _normalise_to_reference_year(series: pd.Series, reference_year: int) -> tuple[pd.Series, list[str]]:
    """Map an external timeseries onto the model reference year while preserving local calendar position."""

    warnings: list[str] = []
    converted = series.copy()
    if converted.index.tz is None:
        converted.index = pd.DatetimeIndex(converted.index).tz_localize(_BELGIUM_TZ)
    else:
        converted.index = pd.DatetimeIndex(converted.index).tz_convert(_BELGIUM_TZ)

    kept_timestamps: list[pd.Timestamp] = []
    kept_values: list[float] = []
    dropped_invalid = 0
    for timestamp, value in zip(converted.index, converted.to_numpy(dtype=float)):
        try:
            mapped = timestamp.replace(year=int(reference_year))
        except ValueError:
            dropped_invalid += 1
            continue
        kept_timestamps.append(pd.Timestamp(mapped))
        kept_values.append(float(value))
    if dropped_invalid > 0:
        warnings.append(f"dropped {dropped_invalid} timestamps that cannot be mapped into reference year {reference_year}")
    mapped_series = pd.Series(kept_values, index=pd.DatetimeIndex(kept_timestamps), dtype=float).sort_index()
    duplicate_count = int(mapped_series.index.duplicated().sum())
    if duplicate_count > 0:
        warnings.append(f"collapsed {duplicate_count} duplicate timestamps after reference-year mapping")
        mapped_series = mapped_series.groupby(level=0).mean().sort_index()
    return mapped_series, warnings


def _map_aggregate_reference_year(
    aggregate_frame: pd.DataFrame,
    *,
    validation_cfg: Mapping[str, Any],
    reference_year: int,
) -> tuple[pd.DataFrame, list[str], str]:
    """Optionally map aggregate validation timestamps onto the model reference year."""

    mode = str(validation_cfg.get("aggregate_reference_year_mode", "as_is")).strip().lower()
    if mode in {"as_is", "none", "disabled"}:
        return aggregate_frame, [], "as_is"
    if mode != "map_to_model_year":
        raise ValueError(f"Unsupported aggregate_reference_year_mode: {mode}")

    aggregate_series = pd.Series(
        pd.to_numeric(aggregate_frame["value"], errors="coerce").to_numpy(dtype=float),
        index=pd.to_datetime(aggregate_frame["timestamp"]),
    )
    mapped_series, warnings = _normalise_to_reference_year(aggregate_series, reference_year)
    mapped_frame = pd.DataFrame(
        {
            "timestamp": mapped_series.index,
            "value": mapped_series.to_numpy(dtype=float),
        }
    )
    return mapped_frame, warnings, mode


def _power_series_energy_kwh(series_w: pd.Series) -> float:
    """Integrate a W timeseries into kWh using explicit timestamp deltas."""

    ordered = series_w.sort_index()
    if len(ordered) < 2:
        return 0.0
    deltas_hours = ordered.index.to_series().diff().dt.total_seconds().shift(-1).ffill() / 3600.0
    deltas_hours = deltas_hours.fillna(0.0)
    return float(((ordered / 1000.0) * deltas_hours.to_numpy(dtype=float)).sum())


def _fluvius_absolute_metrics(model_series_w: pd.Series, data_series_w: pd.Series) -> dict[str, dict[str, float]]:
    """Compute absolute external Fluvius validation metrics."""

    aligned_model = model_series_w.to_numpy(dtype=float)
    aligned_data = data_series_w.to_numpy(dtype=float)
    peak_error_pct = (
        abs(float(np.max(aligned_model)) - float(np.max(aligned_data))) / max(abs(float(np.max(aligned_data))), 1e-9) * 100.0
    )
    annual_energy_error_pct = (
        abs(_power_series_energy_kwh(model_series_w) - _power_series_energy_kwh(data_series_w))
        / max(abs(_power_series_energy_kwh(data_series_w)), 1e-9)
        * 100.0
    )
    ldc_model_kw = compute_ldc(aligned_model / 1000.0)
    ldc_data_kw = compute_ldc(aligned_data / 1000.0)
    ldc_mae_kw = float(np.mean(np.abs(ldc_model_kw - ldc_data_kw))) if len(ldc_model_kw) else 0.0
    mean_metrics = compute_mean_metrics(aligned_model, aligned_data)
    variance_metrics = compute_variance_metrics(aligned_model, aligned_data)
    temporal_metrics = compute_temporal_metrics(model_series_w, data_series_w)
    distribution_metrics = compute_distribution_metrics(aligned_model, aligned_data)
    events = {
        "peak_error_pct": peak_error_pct,
        "annual_energy_error_pct": annual_energy_error_pct,
        "CVRMSE_absolute_pct": float(mean_metrics["CVRMSE"]),
        "load_duration_curve_mae_kW": ldc_mae_kw,
    }
    return {
        "mean": mean_metrics,
        "variance": variance_metrics,
        "distribution": distribution_metrics,
        "temporal": temporal_metrics,
        "events": events,
    }


def _pearson_or_nan(left: pd.Series, right: pd.Series) -> float:
    aligned = pd.concat([left, right], axis=1, join="inner").dropna()
    if len(aligned) < 2:
        return float("nan")
    return float(aligned.iloc[:, 0].corr(aligned.iloc[:, 1]))


def _clock_profile(series_w: pd.Series) -> pd.Series:
    local = series_w.copy().sort_index()
    if local.index.tz is None:
        local.index = pd.DatetimeIndex(local.index).tz_localize(_BELGIUM_TZ)
    else:
        local.index = pd.DatetimeIndex(local.index).tz_convert(_BELGIUM_TZ)
    minutes = local.index.hour * 60 + local.index.minute
    return pd.Series(local.to_numpy(dtype=float), index=minutes, dtype=float).groupby(level=0).mean().sort_index()


def _best_lag_diagnostic(model_series_w: pd.Series, data_series_w: pd.Series, *, max_lag_hours: int = 24) -> dict[str, float]:
    aligned = pd.concat([model_series_w, data_series_w], axis=1, join="inner").dropna()
    if len(aligned) < 3:
        return {"best_lag_steps": 0.0, "best_lag_hours": 0.0, "best_lag_correlation": float("nan")}
    median_step_hours = float(aligned.index.to_series().diff().dt.total_seconds().dropna().median() / 3600.0)
    if not np.isfinite(median_step_hours) or median_step_hours <= 0.0:
        median_step_hours = 1.0
    max_lag_steps = max(int(round(float(max_lag_hours) / median_step_hours)), 1)
    best_lag = 0
    best_corr = float("-inf")
    for lag_steps in range(-max_lag_steps, max_lag_steps + 1):
        shifted_model = aligned.iloc[:, 0].shift(lag_steps)
        corr = _pearson_or_nan(shifted_model, aligned.iloc[:, 1])
        if np.isfinite(corr) and corr > best_corr:
            best_lag = lag_steps
            best_corr = corr
    return {
        "best_lag_steps": float(best_lag),
        "best_lag_hours": float(best_lag * median_step_hours),
        "best_lag_correlation": best_corr if np.isfinite(best_corr) else float("nan"),
    }


def _fluvius_diagnostics(model_series_w: pd.Series, data_series_w: pd.Series) -> dict[str, float]:
    """Return scale and timing diagnostics before changing any acceptance threshold."""

    model_clock = _clock_profile(model_series_w)
    data_clock = _clock_profile(data_series_w)
    model_month = model_series_w.resample("ME").mean()
    data_month = data_series_w.resample("ME").mean()
    lag = _best_lag_diagnostic(model_series_w, data_series_w)
    model_mean = float(model_series_w.mean())
    data_mean = float(data_series_w.mean())
    return {
        "model_mean_W": model_mean,
        "reference_mean_W": data_mean,
        "reference_to_model_mean_scale": data_mean / max(model_mean, 1e-9),
        "model_peak_clock_hour": float(model_clock.idxmax() / 60.0) if not model_clock.empty else float("nan"),
        "reference_peak_clock_hour": float(data_clock.idxmax() / 60.0) if not data_clock.empty else float("nan"),
        "mean_daily_clock_correlation": _pearson_or_nan(model_clock, data_clock),
        "monthly_mean_correlation": _pearson_or_nan(model_month, data_month),
        **lag,
    }


def _write_fluvius_report(
    report_path: Path,
    *,
    metrics: Mapping[str, Mapping[str, float]],
    alignment_info: Mapping[str, Any],
    scaling_info: Mapping[str, Any],
    limitations: str,
    plot_paths: Mapping[str, str],
    fluvius_details: Mapping[str, Any],
    independence: Mapping[str, Any],
    config: Mapping[str, Any] | None = None,
    quick_metadata: Mapping[str, Any] | None = None,
    n_steps: int | None = None,
) -> None:
    """Write the external Fluvius aggregate validation report."""

    lines = [
        "# Validation Report — Model v3 Fluvius External",
        "",
        *validation_type_lines(
            "aggregate validation",
            "External aggregate-profile comparison against representative Fluvius load profiles; not measured feeder validation.",
        ),
        "",
    ]
    if config is not None:
        lines.extend(runtime_context_lines(config, quick_metadata=quick_metadata, n_steps=n_steps))
        lines.extend(
            [
                "",
                *artifact_interpretation_lines(
                    config,
                    quick_metadata=quick_metadata,
                    n_steps=n_steps,
                    extra="Fluvius profiles are representative profiles, so this report supports aggregate-profile plausibility only.",
                ),
                "",
            ]
        )
    lines.extend(
        [
        "## Alignment",
        "",
        f"- model resolution (s): {alignment_info.get('model_resolution_seconds')}",
        f"- data resolution (s): {alignment_info.get('data_resolution_seconds')}",
        f"- target resolution (s): {alignment_info.get('target_resolution_seconds')}",
        f"- matched timestamps: {alignment_info.get('matched_timestamps')}",
        "",
        "## Unit Interpretation",
        "",
        "- Fluvius input files are interpreted as interval energy in `kWh per 15-minute interval`.",
        "- Fluvius representative profiles are converted to `kW` via `E / 0.25 h`.",
        "- Model and external profiles are compared in absolute aggregate `W` after household scaling.",
        "",
        "## Normalization / Calibration Caveat",
        "",
        "The model profile is scaled from per-household output to an aggregate using the configured household count. "
        "Annual electricity calibration remains tied to the Belgian baseline, so this report should not be read as "
        "independent feeder-level calibration.",
        "",
        "## Scaling Explanation",
        "",
        f"- comparison mode: absolute aggregate",
        f"- model representation before scaling: per_household",
        f"- data representation before scaling: representative household profile",
        f"- households applied to model: {scaling_info.get('households')}",
        f"- households applied to Fluvius profile: {scaling_info.get('households')}",
        f"- weighted profile groups: {', '.join(str(key) for key in scaling_info.get('profile_weights', {}))}",
        "",
        "## Fluvius Profile Composition",
        "",
        ]
    )
    for category_name, detail in dict(fluvius_details.get("category_components", {})).items():
        lines.append(
            f"- {category_name}: weight={float(detail.get('weight', 0.0)):.3f}, profiles={', '.join(detail.get('profiles', []))}"
        )
    if fluvius_details.get("warnings"):
        lines.extend(["", "## Fluvius Loader Warnings", ""])
        for warning in fluvius_details["warnings"]:
            lines.append(f"- {warning}")

    lines.extend(
        [
            "",
            "## Validation Independence",
            "",
            f"- dataset_independent: {independence.get('dataset_independent')}",
            f"- partial_overlap: {independence.get('partial_overlap')}",
            f"- validation_independence: {independence.get('validation_independence')}",
            f"- implications: {independence.get('implications')}",
            "",
        ]
    )

    lines.extend(["", "## Mean Accuracy", ""])
    for key, value in metrics["mean"].items():
        lines.append(f"- {key}: {value:.6f}")
    lines.extend(["", "## Variance Realism", ""])
    for key, value in metrics["variance"].items():
        lines.append(f"- {key}: {value:.6f}")
    lines.extend(["", "## Distribution Realism", ""])
    for key, value in metrics["distribution"].items():
        lines.append(f"- {key}: {value:.6f}")
    lines.extend(["", "## Temporal Structure", ""])
    for key, value in metrics["temporal"].items():
        lines.append(f"- {key}: {value:.6f}")
    lines.extend(["", "## External Aggregate Metrics", ""])
    for key, value in metrics["events"].items():
        lines.append(f"- {key}: {value:.6f}")
    if metrics.get("diagnostics"):
        lines.extend(["", "## Temporal / Scaling Diagnostics", ""])
        for key, value in metrics["diagnostics"].items():
            lines.append(f"- {key}: {float(value):.6f}")
    lines.extend(
        [
            "",
            "## Visualisations",
            "",
            f"- Absolute aggregate overlay: ![Overlay]({plot_paths['mean_daily_profile_overlay']})",
            f"- Load duration curve: ![LDC]({plot_paths['load_duration_curve']})",
            f"- Variance by hour: ![Variance]({plot_paths['variance_by_hour']})",
            "",
            "## Limitations",
            "",
            limitations,
            "",
        ]
    )
    report_path.write_text("\n".join(lines), encoding="utf-8")


def _validate_against_fluvius_external(config: Mapping[str, Any], quick_mode: bool | None = None) -> dict[str, Any]:
    """Validate absolute aggregate demand against external Fluvius representative profiles."""

    runner_started = perf_counter()
    prepared_config, quick_metadata = apply_quick_validation_mode(config=config, quick_mode=quick_mode)
    validation_cfg = dict(prepared_config.get("validation", {}))
    fluvius_cfg = dict(validation_cfg.get("fluvius", {}))
    if not fluvius_cfg.get("enabled", False):
        raise ValueError("validation.fluvius.enabled must be true for fluvius_external mode")

    LOGGER.info(
        "aggregate_validation.fluvius_external.start quick_mode=%s model_source=%s",
        quick_metadata["enabled"],
        validation_cfg.get("model_source", "cohort"),
    )
    model_results, model_frame = run_validation_model(config=prepared_config, validation_cfg=validation_cfg)

    model_series_w = pd.Series(model_frame["value"].to_numpy(dtype=float), index=pd.to_datetime(model_frame["timestamp"]))
    raw_reference_year = dict(prepared_config.get("simulation", {})).get("reference_year")
    if raw_reference_year is None:
        reference_year = int(pd.Series(pd.DatetimeIndex(model_series_w.index).year).mode().iloc[0])
    else:
        reference_year = int(raw_reference_year)
    households = max(int(fluvius_cfg.get("households", model_results.get("household_count", 1)) or 1), 1)
    model_total_w = model_series_w * households

    LOGGER.info("aggregate_validation.fluvius_external.reference_load start")
    fluvius_profiles = load_fluvius_profiles(
        fluvius_cfg.get("base_path", "inputs/load_profiles/fluvius"),
        max_rows_per_file=quick_external_row_cap(quick_metadata, rows_per_step=4),
        pv_variant_policy=str(fluvius_cfg.get("pv_variant_policy", "all")),
    )
    LOGGER.info("aggregate_validation.fluvius_external.reference_load complete profiles=%s", len(fluvius_profiles))
    weighted_profile_kw, fluvius_details = aggregate_fluvius_profiles(
        profiles=fluvius_profiles,
        profile_weights=dict(fluvius_cfg.get("profile_weights", {})),
        pv_variant_policy=str(fluvius_cfg.get("pv_variant_policy", "all")),
    )
    weighted_profile_kw, mapping_warnings = _normalise_to_reference_year(weighted_profile_kw, reference_year)
    fluvius_details = dict(fluvius_details)
    fluvius_details["warnings"] = list(fluvius_details.get("warnings", [])) + mapping_warnings
    fluvius_total_w = weighted_profile_kw * households * 1000.0
    load_source_cfg = dict(dict(dict(prepared_config.get("data", {})).get("sources", {})).get("load_profiles", {}))
    independence = assess_validation_independence(
        input_sources=(
            {
                "path": load_source_cfg.get("file_path"),
                "data_role": tuple(load_source_cfg.get("data_role", ("input",))),
                "source_name": "load_profiles",
            },
        ),
        validation_source={
            "path": fluvius_cfg.get("base_path"),
            "data_role": ("validation",),
            "source_name": "fluvius",
        },
    )

    LOGGER.info("aggregate_validation.fluvius_external.alignment start")
    aligned, alignment_info = align_timeseries(
        pd.DataFrame({"timestamp": model_total_w.index, "value": model_total_w.to_numpy(dtype=float)}),
        pd.DataFrame({"timestamp": fluvius_total_w.index, "value": fluvius_total_w.to_numpy(dtype=float)}),
        resolution=validation_cfg.get("resolution", "auto"),
    )
    aligned_model = pd.Series(aligned["value_model"].to_numpy(dtype=float), index=pd.to_datetime(aligned["timestamp"]))
    aligned_data = pd.Series(aligned["value_data"].to_numpy(dtype=float), index=pd.to_datetime(aligned["timestamp"]))
    metrics = _fluvius_absolute_metrics(aligned_model, aligned_data)
    metrics["diagnostics"] = _fluvius_diagnostics(aligned_model, aligned_data)

    report_dir = validation_output_dir(config=prepared_config, dataset_name="fluvius_external")
    plot_paths = _generate_plots(
        report_dir=report_dir,
        aligned=aligned,
        model_bands=pd.DataFrame(
            {
                "timestamp": aligned["timestamp"],
                "value": aligned["value_model"],
                "P10_W": aligned["value_model"],
                "P50_W": aligned["value_model"],
                "P90_W": aligned["value_model"],
            }
        ),
        diurnal_model=compute_diurnal_variance(aligned_model),
        diurnal_data=compute_diurnal_variance(aligned_data),
    )

    validation_root = ensure_dir(output_root(prepared_config) / "validation")
    report_path = validation_root / "validation_report_v3_fluvius_external.md"
    limitations = "Fluvius profiles are representative, not measured feeder data."
    _write_fluvius_report(
        report_path=report_path,
        metrics=metrics,
        alignment_info=alignment_info,
        scaling_info={
            "households": households,
            "profile_weights": dict(fluvius_cfg.get("profile_weights", {})),
        },
        limitations=limitations,
        plot_paths=plot_paths,
        fluvius_details=fluvius_details,
        independence=independence,
        config=prepared_config,
        quick_metadata=quick_metadata,
        n_steps=int(model_results.get("n_steps", 0)),
    )
    write_frame_csv(report_dir / "aligned_absolute_timeseries.csv", aligned)
    write_json(
        report_dir / "metrics.json",
        {
            "metrics": metrics,
            "alignment": alignment_info,
            "independence": independence,
            "quick_mode": quick_metadata,
            "fluvius_details": fluvius_details,
            "households": households,
        },
    )

    elapsed_seconds = perf_counter() - runner_started
    runner_timing = {
        "elapsed_seconds": elapsed_seconds,
        "quick_mode": quick_metadata["enabled"],
        "n_steps": int(model_results.get("n_steps", 0)),
    }
    summary_table = _summary_table(metrics)
    print(summary_table)
    return {
        "metrics": metrics,
        "independence": independence,
        "alignment": alignment_info,
        "report_path": str(report_path),
        "summary_table": summary_table,
        "quick_mode": quick_metadata,
        "runner_timing": runner_timing,
        "fluvius_details": fluvius_details,
        "households": households,
    }


def validate_against_aggregate(config: Mapping[str, Any], quick_mode: bool | None = None) -> dict[str, Any]:
    """Validate normalized aggregate load shape, seasonal variation, and peak timing."""

    validation_mode = str(dict(config.get("validation", {})).get("aggregate_mode", "normalized_internal")).strip().lower()
    if validation_mode == "fluvius_external":
        return _validate_against_fluvius_external(config=config, quick_mode=quick_mode)

    runner_started = perf_counter()
    prepared_config, quick_metadata = apply_quick_validation_mode(config=config, quick_mode=quick_mode)
    validation_cfg = dict(prepared_config.get("validation", {}))
    LOGGER.info(
        "aggregate_validation.start quick_mode=%s model_source=%s max_steps=%s",
        quick_metadata["enabled"],
        validation_cfg.get("model_source", "cohort"),
        dict(prepared_config.get("simulation", {})).get("max_steps"),
    )
    if quick_metadata["enabled"]:
        LOGGER.info("aggregate_validation.quick_mode overrides=%s", quick_metadata["overrides"])

    model_results, model_frame = run_validation_model(config=prepared_config, validation_cfg=validation_cfg)
    aggregate_validation_cfg = dict(validation_cfg)
    LOGGER.info("aggregate_validation.reference_load start")
    aggregate_frame, _, aggregation_mode = load_aggregate_reference_profile(config=prepared_config, validation_cfg=aggregate_validation_cfg)
    raw_reference_year = dict(prepared_config.get("simulation", {})).get("reference_year")
    if raw_reference_year is None:
        reference_year = int(pd.to_datetime(model_frame["timestamp"]).dt.year.mode().iloc[0])
    else:
        reference_year = int(raw_reference_year)
    aggregate_frame, mapping_warnings, aggregate_reference_year_mode = _map_aggregate_reference_year(
        aggregate_frame,
        validation_cfg=validation_cfg,
        reference_year=reference_year,
    )
    LOGGER.info("aggregate_validation.reference_load complete rows=%s aggregation=%s", len(aggregate_frame), aggregation_mode)

    LOGGER.info("aggregate_validation.alignment start")
    aligned, alignment_info = align_timeseries(
        model_frame[["timestamp", "value"]],
        aggregate_frame[["timestamp", "value"]],
        resolution=validation_cfg.get("resolution", "auto"),
    )
    if len(aligned) < 2:
        raise ValueError(
            "Aggregate validation produced fewer than two aligned timestamps. "
            "Check validation.aggregate_path, simulation.reference_year, and "
            "validation.aggregate_reference_year_mode."
        )
    aligned_model_series = pd.Series(aligned["value_model"].to_numpy(dtype=float), index=pd.to_datetime(aligned["timestamp"]))
    aligned_data_series = pd.Series(aligned["value_data"].to_numpy(dtype=float), index=pd.to_datetime(aligned["timestamp"]))
    normalized_model = _normalized_series(aligned_model_series)
    normalized_data = _normalized_series(aligned_data_series)
    LOGGER.info("aggregate_validation.alignment complete aligned_rows=%s", len(aligned))

    monthly_model = normalized_model.resample("ME").mean()
    monthly_data = normalized_data.resample("ME").mean()
    seasonal_mae = float((monthly_model - monthly_data).abs().mean()) if len(monthly_model) else 0.0
    seasonal_peak_month_mismatch = float(abs(int(monthly_model.idxmax().month) - int(monthly_data.idxmax().month))) if len(monthly_model) else 0.0

    LOGGER.info("aggregate_validation.metrics start")
    metrics = {
        "mean": compute_mean_metrics(normalized_model.to_numpy(dtype=float), normalized_data.to_numpy(dtype=float)),
        "variance": compute_variance_metrics(normalized_model.to_numpy(dtype=float), normalized_data.to_numpy(dtype=float)),
        "distribution": compute_distribution_metrics(normalized_model.to_numpy(dtype=float), normalized_data.to_numpy(dtype=float)),
        "temporal": compute_temporal_metrics(normalized_model, normalized_data),
        "events": {
            "seasonal_shape_MAE": seasonal_mae,
            "seasonal_peak_month_error": seasonal_peak_month_mismatch,
            "peak_day_error": float(abs(normalized_model.max() - normalized_data.max())),
            "peak_MAE_kW": 0.0,
            "extreme_condition_error": seasonal_mae,
        },
    }
    metrics["shape"] = dict(metrics["mean"])
    metrics["seasonal"] = {
        "seasonal_shape_MAE": seasonal_mae,
        "seasonal_peak_month_error": seasonal_peak_month_mismatch,
    }
    acceptance_metrics = build_acceptance_metrics(normalized_model, normalized_data, {"events": {"peak_MAE_kW": 0.0}, "distribution": metrics["distribution"]})
    acceptance = check_acceptance(
        acceptance_metrics,
        thresholds=dict(validation_cfg.get("acceptance", {})),
    )
    LOGGER.info(
        "aggregate_validation.metrics complete cvrmse_hourly=%.3f seasonal_mae=%.3f",
        float(acceptance_metrics.get("CVRMSE_hourly", 0.0)),
        seasonal_mae,
    )
    load_source_cfg = dict(dict(dict(prepared_config.get("data", {})).get("sources", {})).get("load_profiles", {}))
    independence = assess_validation_independence(
        input_sources=(
            {
                "path": load_source_cfg.get("file_path"),
                "data_role": tuple(load_source_cfg.get("data_role", ("input",))),
                "source_name": "load_profiles",
            },
        ),
        validation_source={
            "path": validation_cfg.get("aggregate_path"),
            "data_role": tuple(validation_cfg.get("aggregate_data_role", ("validation",))),
            "source_name": "aggregate",
        },
    )

    report_dir = validation_output_dir(config=prepared_config, dataset_name="aggregate")
    plot_paths = _generate_plots(
        report_dir=report_dir,
        aligned=pd.DataFrame(
            {
                "timestamp": aligned["timestamp"],
                "value_model": normalized_model.to_numpy(dtype=float),
                "value_data": normalized_data.to_numpy(dtype=float),
            }
        ),
        model_bands=pd.DataFrame(
            {
                "timestamp": aligned["timestamp"],
                "value": normalized_model.to_numpy(dtype=float),
                "P10_W": normalized_model.to_numpy(dtype=float),
                "P50_W": normalized_model.to_numpy(dtype=float),
                "P90_W": normalized_model.to_numpy(dtype=float),
            }
        ),
        diurnal_model=compute_diurnal_variance(normalized_model),
        diurnal_data=compute_diurnal_variance(normalized_data),
    )
    report_path = report_dir / "validation_report_v3_aggregate.md"
    _write_report(
        report_path=report_path,
        metrics=metrics,
        alignment_info=alignment_info,
        plot_paths=plot_paths,
        acceptance_metrics=acceptance_metrics,
        acceptance=acceptance,
        independence=independence,
        validation_type="internal aggregate diagnostic",
        validation_description=(
            "Normalized aggregate shape diagnostic against an explicitly configured aggregate reference. "
            "Do not use LCL here as thesis-facing validation because LCL is the input load-shape source."
        ),
        config=prepared_config,
        quick_metadata=quick_metadata,
        n_steps=int(model_results.get("n_steps", 0)),
        artifact_note=(
            "Normalized aggregate metrics cannot support absolute calibration claims; read the threshold table for overall status."
        ),
        calibration_caveat=(
            "Both model and reference series are divided by their own means before metric calculation. "
            "The result is a shape comparison only and does not validate annual electricity totals or household scaling."
        ),
        limitations=(
            "This script validates normalized aggregate demand shape, seasonal variation, and peak timing. "
            "It does not validate appliance attribution or absolute household totals."
        ),
    )
    write_frame_csv(
        report_dir / "aligned_normalized_timeseries.csv",
        pd.DataFrame(
            {
                "timestamp": aligned["timestamp"],
                "value_model": normalized_model.to_numpy(dtype=float),
                "value_data": normalized_data.to_numpy(dtype=float),
            }
        ),
    )
    write_json(
        report_dir / "metrics.json",
        {
            "metrics": metrics,
            "acceptance_metrics": acceptance_metrics,
            "acceptance": acceptance,
            "independence": independence,
            "alignment": alignment_info,
            "aggregate_aggregation": aggregation_mode,
            "aggregate_reference_year_mode": aggregate_reference_year_mode,
            "aggregate_reference_year_warnings": mapping_warnings,
            "quick_mode": quick_metadata,
        },
    )
    elapsed_seconds = perf_counter() - runner_started
    runner_timing = {
        "elapsed_seconds": elapsed_seconds,
        "quick_mode": quick_metadata["enabled"],
        "n_steps": int(model_results.get("n_steps", 0)),
    }
    LOGGER.info(
        "aggregate_validation.complete elapsed_s=%.1f n_steps=%s quick_mode=%s report=%s",
        elapsed_seconds,
        int(model_results.get("n_steps", 0)),
        quick_metadata["enabled"],
        report_path,
    )

    summary_table = _summary_table(metrics)
    print(summary_table)
    return {
        "metrics": metrics,
        "acceptance_metrics": acceptance_metrics,
        "acceptance": acceptance,
        "independence": independence,
        "alignment": alignment_info,
        "report_path": str(report_path),
        "summary_table": summary_table,
        "quick_mode": quick_metadata,
        "runner_timing": runner_timing,
    }


if __name__ == "__main__":
    from pipelines.run_model_v3 import load_config

    args = build_runner_cli("Validate normalized aggregate demand shape for model_v3.").parse_args()
    configure_runner_logging(
        (
            __name__,
            "model_v3.validation.runners.model_runner",
            "model_v3.cohort.cohort_engine",
            "model_v3.simulation.annual_runner",
        )
    )
    result = validate_against_aggregate(load_config(args.config), quick_mode=args.quick)
    print(format_elapsed_summary("aggregate", result))
