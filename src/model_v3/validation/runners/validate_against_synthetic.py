"""Validation runner against a deterministic synthetic benchmark."""

from __future__ import annotations

if __package__ in {None, ""}:
    import sys
    from pathlib import Path as _Path

    sys.path.append(str(_Path(__file__).resolve().parents[3]))

from datetime import timedelta
from pathlib import Path
from time import perf_counter
from typing import Any, Mapping

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from model_v3.cohort.cohort_engine import run_cohort_simulation
from model_v3.output.persistence import validation_output_dir, write_frame_csv, write_json
from model_v3.validation.acceptance_criteria import check_acceptance
from model_v3.validation.core.metrics_distribution import compute_distribution_metrics, compute_ldc
from model_v3.validation.core.metrics_events import compute_event_metrics, extract_extreme_days
from model_v3.validation.core.metrics_mean import compute_mean_metrics
from model_v3.validation.core.metrics_temporal import compute_temporal_metrics
from model_v3.validation.core.metrics_variance import compute_diurnal_variance, compute_variance_metrics
from model_v3.validation.runners.runner_utils import (
    apply_quick_validation_mode,
    artifact_interpretation_lines,
    build_runner_cli,
    configure_runner_logging,
    format_elapsed_summary,
    runtime_context_lines,
    validation_type_lines,
)
from model_v3.validation.utils.alignment import align_timeseries
from model_v3.validation.utils.independence import assess_validation_independence
from model_v3.validation.utils.preprocessing import scalar_bands_to_profile
from model_v3.utils.energy import power_series_to_energy_kwh


def _normalised_mbe(y_model: pd.Series, y_data: pd.Series) -> float:
    """Return mean bias error normalised by the observed mean."""

    data_mean = float(y_data.mean())
    if abs(data_mean) <= 1e-9:
        return 0.0
    return float((y_model - y_data).mean() / data_mean)


def _resampled_energy_series(series: pd.Series, frequency: str) -> pd.Series:
    """Aggregate a power series to period totals."""

    return power_series_to_energy_kwh(series).resample(frequency).sum()


def build_acceptance_metrics(aligned_model_series: pd.Series, aligned_data_series: pd.Series, metrics: Mapping[str, Mapping[str, float]]) -> dict[str, float]:
    """Build the compact metric set used by the acceptance criteria."""

    monthly_model = _resampled_energy_series(aligned_model_series, "ME")
    monthly_data = _resampled_energy_series(aligned_data_series, "ME")
    monthly_mean_metrics = compute_mean_metrics(monthly_model.to_numpy(dtype=float), monthly_data.to_numpy(dtype=float))
    hourly_mean_metrics = compute_mean_metrics(
        aligned_model_series.to_numpy(dtype=float),
        aligned_data_series.to_numpy(dtype=float),
    )
    return {
        "MBE_monthly": _normalised_mbe(monthly_model, monthly_data),
        "CVRMSE_monthly": float(monthly_mean_metrics["CVRMSE"]) / 100.0,
        "MBE_hourly": _normalised_mbe(aligned_model_series, aligned_data_series),
        "CVRMSE_hourly": float(hourly_mean_metrics["CVRMSE"]) / 100.0,
        "peak_MAE_kW": float(metrics["events"].get("peak_MAE_kW", 0.0)),
        "P10_error_kW": abs(float(metrics["distribution"].get("P10_error", 0.0))) / 1000.0,
        "P90_error_kW": abs(float(metrics["distribution"].get("P90_error", 0.0))) / 1000.0,
    }


def acceptance_table_markdown(acceptance: Mapping[str, Any], metrics: Mapping[str, float]) -> str:
    """Render the literature-threshold table for markdown reports."""

    acceptable = dict(acceptance.get("acceptable_thresholds", {}))
    good = dict(acceptance.get("good_thresholds", {}))
    checks = dict(acceptance.get("checks", {}))
    rows = [
        "| Metric | Model | Acceptable | Good | Status |",
        "| --- | ---: | ---: | ---: | --- |",
        f"| MBE monthly | {metrics['MBE_monthly']:.3f} | <= {acceptable['monthly_MBE_max']:.3f} | - | {'PASS' if checks['monthly_MBE_ok'] else 'FAIL'} |",
        f"| CVRMSE monthly | {metrics['CVRMSE_monthly']:.3f} | <= {acceptable['monthly_CVRMSE_max']:.3f} | - | {'PASS' if checks['monthly_CVRMSE_ok'] else 'FAIL'} |",
        f"| MBE hourly | {metrics['MBE_hourly']:.3f} | <= {acceptable['hourly_MBE_max']:.3f} | <= {good['hourly_MBE_max']:.3f} | {'PASS' if checks['hourly_MBE_ok'] else 'FAIL'} |",
        f"| CVRMSE hourly | {metrics['CVRMSE_hourly']:.3f} | <= {acceptable['hourly_CVRMSE_max']:.3f} | <= {good['hourly_CVRMSE_max']:.3f} | {'PASS' if checks['hourly_CVRMSE_ok'] else 'FAIL'} |",
        f"| Peak MAE (kW) | {metrics['peak_MAE_kW']:.3f} | <= {acceptable['peak_MAE_kW_max']:.3f} | <= {good['peak_MAE_kW_max']:.3f} | {'PASS' if checks['peak_MAE_ok'] else 'FAIL'} |",
        f"| Quantile error P10 (kW) | {metrics['P10_error_kW']:.3f} | <= {acceptable['quantile_error_kW_max']:.3f} | <= {good['quantile_error_kW_max']:.3f} | {'PASS' if checks['quantile_ok'] else 'FAIL'} |",
        f"| Quantile error P90 (kW) | {metrics['P90_error_kW']:.3f} | <= {acceptable['quantile_error_kW_max']:.3f} | <= {good['quantile_error_kW_max']:.3f} | {'PASS' if checks['quantile_ok'] else 'FAIL'} |",
        f"| Overall | {'PASS' if checks['overall'] else 'FAIL'} | all critical pass | - | {'PASS' if checks['overall'] else 'FAIL'} |",
    ]
    return "\n".join(rows)


def _build_model_frame(results: Mapping[str, Any], config: Mapping[str, Any]) -> pd.DataFrame:
    """Project cohort results onto a validation-ready hourly timeseries."""

    if "profile_frame" in results:
        frame = pd.DataFrame(results["profile_frame"]).copy()
        if "per_household_profile_W" in frame.columns:
            frame["value"] = pd.to_numeric(frame["per_household_profile_W"], errors="coerce").fillna(0.0)
            frame["P10_W"] = pd.to_numeric(frame.get("P10_W"), errors="coerce").fillna(frame["value"])
            frame["P50_W"] = pd.to_numeric(frame.get("P50_W"), errors="coerce").fillna(frame["value"])
            frame["P90_W"] = pd.to_numeric(frame.get("P90_W"), errors="coerce").fillna(frame["value"])
            return frame[["timestamp", "value", "P10_W", "P50_W", "P90_W"]]

    start_timestamp = pd.to_datetime(dict(config.get("simulation", {})).get("start_timestamp", "2026-01-01T00:00:00+01:00"))
    aggregate_profile = np.asarray(results.get("aggregate_profile", []), dtype=float)
    timestamps = [start_timestamp + timedelta(hours=int(index)) for index in range(len(aggregate_profile))]
    bands = scalar_bands_to_profile(
        aggregate_profile=aggregate_profile,
        mean_profile=float(results.get("mean_profile", 0.0)),
        p10_value=float(results.get("P10_profile", 0.0)),
        p50_value=float(results.get("P50_profile", 0.0)),
        p90_value=float(results.get("P90_profile", 0.0)),
    )
    model_frame = pd.DataFrame(
        {
            "timestamp": timestamps,
            "value": aggregate_profile,
            "P10_W": bands["P10_W"],
            "P50_W": bands["P50_W"],
            "P90_W": bands["P90_W"],
        }
    )
    return model_frame


def _build_synthetic_reference(model_frame: pd.DataFrame, config: Mapping[str, Any]) -> pd.DataFrame:
    """Create a reproducible synthetic benchmark with realistic bias and timing differences."""

    rng = np.random.default_rng(int(dict(config.get("cohort", {})).get("random_seed", 42)) + 100)
    hours = np.arange(len(model_frame))
    base = model_frame["value"].to_numpy(dtype=float)
    scaled = base * (1.04 + 0.06 * np.sin(2.0 * np.pi * hours / max(len(hours), 1)))
    shifted = np.roll(scaled, 1)
    noise = rng.normal(loc=0.0, scale=max(np.std(base) * 0.05, 1.0), size=len(base))
    temperature = 5.0 - 4.0 * np.cos(2.0 * np.pi * hours / max(len(hours), 1))

    return pd.DataFrame(
        {
            "timestamp": model_frame["timestamp"],
            "value": np.maximum(shifted + noise, 0.0),
            "temperature_C": temperature,
        }
    )


def _generate_plots(
    report_dir: Path,
    aligned: pd.DataFrame,
    model_bands: pd.DataFrame,
    diurnal_model: pd.Series,
    diurnal_data: pd.Series,
) -> dict[str, str]:
    """Generate validation plots and return their relative paths."""

    report_dir.mkdir(parents=True, exist_ok=True)
    plot_paths: dict[str, str] = {}

    overlay_path = report_dir / "mean_daily_profile_overlay.png"
    plt.figure(figsize=(8, 4))
    plt.plot(aligned["timestamp"], aligned["value_model"], label="Model")
    plt.plot(aligned["timestamp"], aligned["value_data"], label="Data")
    plt.xticks(rotation=45, ha="right")
    plt.ylabel("Demand (W)")
    plt.title("Mean Daily Profile Overlay")
    plt.tight_layout()
    plt.legend()
    plt.savefig(overlay_path)
    plt.close()
    plot_paths["mean_daily_profile_overlay"] = overlay_path.name

    ldc_path = report_dir / "load_duration_curve.png"
    plt.figure(figsize=(8, 4))
    plt.plot(compute_ldc(aligned["value_model"].to_numpy()), label="Model")
    plt.plot(compute_ldc(aligned["value_data"].to_numpy()), label="Data")
    plt.ylabel("Demand (W)")
    plt.title("Load Duration Curve")
    plt.tight_layout()
    plt.legend()
    plt.savefig(ldc_path)
    plt.close()
    plot_paths["load_duration_curve"] = ldc_path.name

    variance_path = report_dir / "variance_by_hour.png"
    plt.figure(figsize=(8, 4))
    plt.plot(diurnal_model.index, diurnal_model.values, label="Model")
    plt.plot(diurnal_data.index, diurnal_data.values, label="Data")
    plt.xlabel("Hour of day")
    plt.ylabel("Variance")
    plt.title("Variance by Hour")
    plt.tight_layout()
    plt.legend()
    plt.savefig(variance_path)
    plt.close()
    plot_paths["variance_by_hour"] = variance_path.name

    bands_path = report_dir / "uncertainty_bands.png"
    plt.figure(figsize=(8, 4))
    plt.plot(model_bands["timestamp"], model_bands["value"], label="Mean")
    plt.fill_between(model_bands["timestamp"], model_bands["P10_W"], model_bands["P90_W"], alpha=0.3, label="P10-P90")
    plt.xticks(rotation=45, ha="right")
    plt.ylabel("Demand (W)")
    plt.title("Model Uncertainty Bands")
    plt.tight_layout()
    plt.legend()
    plt.savefig(bands_path)
    plt.close()
    plot_paths["uncertainty_bands"] = bands_path.name

    return plot_paths


def _write_report(
    report_path: Path,
    metrics: Mapping[str, Mapping[str, float]],
    alignment_info: Mapping[str, Any],
    plot_paths: Mapping[str, str],
    acceptance_metrics: Mapping[str, float],
    acceptance: Mapping[str, Any],
    independence: Mapping[str, Any],
    limitations: str,
    *,
    validation_type: str = "internal consistency",
    validation_description: str = "Synthetic-reference comparison for runner and metric consistency; not an external validation claim.",
    config: Mapping[str, Any] | None = None,
    quick_metadata: Mapping[str, Any] | None = None,
    n_steps: int | None = None,
    artifact_note: str | None = None,
    calibration_caveat: str | None = None,
) -> None:
    """Write a markdown validation report."""

    report_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Validation Report — Model v3",
        "",
        *validation_type_lines(validation_type, validation_description),
        "",
    ]
    if config is not None:
        lines.extend(runtime_context_lines(config, quick_metadata=quick_metadata, n_steps=n_steps))
        lines.extend(["", *artifact_interpretation_lines(config, quick_metadata=quick_metadata, n_steps=n_steps, extra=artifact_note), ""])
    lines.extend(
        [
        "## Alignment",
        "",
        f"- model resolution (s): {alignment_info.get('model_resolution_seconds')}",
        f"- data resolution (s): {alignment_info.get('data_resolution_seconds')}",
        f"- target resolution (s): {alignment_info.get('target_resolution_seconds')}",
        f"- matched timestamps: {alignment_info.get('matched_timestamps')}",
        "",
        "## Mean accuracy",
        "",
        ]
    )
    for key, value in metrics["mean"].items():
        lines.append(f"- {key}: {value:.6f}")

    lines.extend(["", "## Variance realism", ""])
    for key, value in metrics["variance"].items():
        lines.append(f"- {key}: {value:.6f}")

    lines.extend(["", "## Distribution realism", ""])
    for key, value in metrics["distribution"].items():
        lines.append(f"- {key}: {value:.6f}")
    lines.append(f"- LDC plot: ![LDC]({plot_paths['load_duration_curve']})")

    lines.extend(["", "## Temporal structure", ""])
    for key, value in metrics["temporal"].items():
        lines.append(f"- {key}: {value:.6f}")

    lines.extend(["", "## Event-based validation", ""])
    for key, value in metrics["events"].items():
        lines.append(f"- {key}: {value:.6f}")

    lines.extend(
        [
            "",
            "## Validation vs Literature Thresholds",
            "",
            acceptance_table_markdown(acceptance=acceptance, metrics=acceptance_metrics),
        ]
    )

    lines.extend(
        [
            "",
            "## Visualisations",
            "",
            f"- Mean daily profile overlay: ![Overlay]({plot_paths['mean_daily_profile_overlay']})",
            f"- Load duration curve: ![LDC]({plot_paths['load_duration_curve']})",
            f"- Variance by hour: ![Variance]({plot_paths['variance_by_hour']})",
            f"- Uncertainty bands: ![Bands]({plot_paths['uncertainty_bands']})",
            "",
            "## Validation Independence / Data Role",
            "",
            f"- dataset_independent: {independence.get('dataset_independent')}",
            f"- partial_overlap: {independence.get('partial_overlap')}",
            f"- validation_independence: {independence.get('validation_independence')}",
            f"- implications: {independence.get('implications')}",
            "",
        ]
    )
    if calibration_caveat:
        lines.extend(["## Normalization / Calibration Caveat", "", calibration_caveat, ""])
    lines.extend(
        [
            "## What this script does not validate",
            "",
            limitations,
            "",
        ]
    )
    report_path.write_text("\n".join(lines), encoding="utf-8")


def _summary_table(metrics: Mapping[str, Mapping[str, float]]) -> str:
    """Build a compact markdown metrics table for console output."""

    rows = ["| Metric Group | Metric | Value |", "| --- | --- | ---: |"]
    for group_name, group_metrics in metrics.items():
        for metric_name, value in group_metrics.items():
            rows.append(f"| {group_name} | {metric_name} | {value:.6f} |")
    return "\n".join(rows)


def validate_against_synthetic(config: Mapping[str, Any], quick_mode: bool | None = None) -> dict[str, Any]:
    """Run the full validation stack against a reproducible synthetic benchmark."""

    runner_started = perf_counter()
    config, quick_metadata = apply_quick_validation_mode(config, quick_mode=quick_mode)
    results = run_cohort_simulation(config=config)
    model_frame = _build_model_frame(results=results, config=config)
    data_frame = _build_synthetic_reference(model_frame=model_frame, config=config)
    aligned, alignment_info = align_timeseries(model_frame[["timestamp", "value"]], data_frame[["timestamp", "value"]], resolution=dict(config.get("validation", {})).get("resolution", "auto"))
    aligned_temperature, _ = align_timeseries(
        model_frame[["timestamp", "value"]],
        data_frame[["timestamp", "temperature_C"]].rename(columns={"temperature_C": "value"}),
        resolution=dict(config.get("validation", {})).get("resolution", "auto"),
    )

    aligned_model_series = pd.Series(aligned["value_model"].to_numpy(dtype=float), index=pd.to_datetime(aligned["timestamp"]))
    aligned_data_series = pd.Series(aligned["value_data"].to_numpy(dtype=float), index=pd.to_datetime(aligned["timestamp"]))
    aligned_temperature_series = pd.Series(
        aligned_temperature["value_data"].to_numpy(dtype=float),
        index=pd.to_datetime(aligned_temperature["timestamp"]),
    )

    mean_metrics = compute_mean_metrics(aligned["value_model"], aligned["value_data"])
    variance_metrics = compute_variance_metrics(aligned["value_model"], aligned["value_data"])
    distribution_metrics = compute_distribution_metrics(aligned["value_model"], aligned["value_data"])
    temporal_metrics = compute_temporal_metrics(aligned_model_series, aligned_data_series)
    event_metrics = compute_event_metrics(aligned_model_series, aligned_data_series)

    diurnal_model = compute_diurnal_variance(aligned_model_series)
    diurnal_data = compute_diurnal_variance(aligned_data_series)
    temporal_metrics["diversity_factor_model"] = float(results.get("diversity_factor", 0.0))

    extreme_days = extract_extreme_days(aligned_data_series, aligned_temperature_series)
    event_metrics["coldest_day_count"] = float(len(extreme_days["coldest_days"]))
    event_metrics["peak_day_count"] = float(len(extreme_days["peak_demand_days"]))

    metrics = {
        "mean": mean_metrics,
        "variance": variance_metrics,
        "distribution": distribution_metrics,
        "temporal": temporal_metrics,
        "events": event_metrics,
    }
    acceptance_metrics = build_acceptance_metrics(aligned_model_series, aligned_data_series, metrics)
    acceptance = check_acceptance(
        acceptance_metrics,
        thresholds=dict(dict(config.get("validation", {})).get("acceptance", {})),
    )
    independence = assess_validation_independence(
        input_sources=(),
        validation_source={
            "path": "synthetic://generated_from_model",
            "data_role": ("validation",),
            "source_name": "synthetic",
        },
    )

    report_dir = validation_output_dir(config=config, dataset_name="synthetic")
    plot_paths = _generate_plots(
        report_dir=report_dir,
        aligned=aligned,
        model_bands=model_frame,
        diurnal_model=diurnal_model,
        diurnal_data=diurnal_data,
    )
    report_path = report_dir / "validation_report_v3_synthetic.md"
    _write_report(
        report_path=report_path,
        metrics=metrics,
        alignment_info=alignment_info,
        plot_paths=plot_paths,
        acceptance_metrics=acceptance_metrics,
        acceptance=acceptance,
        independence=independence,
        config=config,
        quick_metadata=quick_metadata,
        n_steps=len(model_frame),
        limitations=(
            "This script validates the validation pipeline itself against a synthetic benchmark. "
            "It does not validate external measured-load calibration, appliance-level end uses, or long-run seasonal behaviour."
        ),
    )
    write_frame_csv(report_dir / "aligned_timeseries.csv", aligned)
    write_frame_csv(report_dir / "model_bands.csv", model_frame)
    write_json(
        report_dir / "metrics.json",
        {
            "metrics": metrics,
            "acceptance_metrics": acceptance_metrics,
            "acceptance": acceptance,
            "independence": independence,
            "alignment": alignment_info,
            "quick_mode": quick_metadata,
        },
    )

    elapsed_seconds = perf_counter() - runner_started
    runner_timing = {
        "elapsed_seconds": elapsed_seconds,
        "quick_mode": quick_metadata["enabled"],
        "n_steps": int(results.get("n_steps", len(model_frame))),
    }
    summary_table = _summary_table(metrics)
    print(summary_table)
    return {
        "metrics": metrics,
        "alignment": alignment_info,
        "report_path": str(report_path),
        "summary_table": summary_table,
        "acceptance_metrics": acceptance_metrics,
        "acceptance": acceptance,
        "independence": independence,
        "quick_mode": quick_metadata,
        "runner_timing": runner_timing,
    }


if __name__ == "__main__":
    from pipelines.run_model_v3 import load_config

    args = build_runner_cli("Validate model_v3 against a deterministic synthetic benchmark.").parse_args()
    configure_runner_logging(
        (
            __name__,
            "model_v3.cohort.cohort_engine",
            "model_v3.simulation.annual_runner",
        )
    )
    result = validate_against_synthetic(load_config(args.config), quick_mode=args.quick)
    print(format_elapsed_summary("synthetic", result))
