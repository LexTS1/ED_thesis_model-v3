#!/usr/bin/env python3
"""Validate stochastic household baseload structure against Richardson profiles."""

from __future__ import annotations

if __package__ in {None, ""}:
    import sys
    from pathlib import Path as _Path

    sys.path.append(str(_Path(__file__).resolve().parents[3]))

import logging
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
from model_v3.validation.core.metrics_distribution import compute_distribution_metrics, compute_ldc
from model_v3.validation.core.metrics_temporal import compute_diversity_factor, compute_temporal_metrics
from model_v3.validation.core.metrics_variance import compute_diurnal_variance, compute_variance_metrics
from model_v3.validation.reference_generators.richardson import generate_richardson_reference
from model_v3.validation.runners.runner_utils import (
    apply_quick_validation_mode,
    artifact_interpretation_lines,
    build_runner_cli,
    configure_runner_logging,
    format_elapsed_summary,
    runtime_context_lines,
    validation_type_lines,
)
from pipelines.run_model_v3 import load_config, resolve_config_path

LOGGER = logging.getLogger(__name__)


def _local_naive_timestamp(value: Any) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is not None:
        return timestamp.tz_convert("UTC").tz_localize(None)
    return timestamp


def _profile_dict_to_frame(
    profiles: Mapping[str, list[float]],
    timestamps: list[Any],
    *,
    column_name: str,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for household_id, values in sorted(dict(profiles).items()):
        for timestamp, value in zip(timestamps, values):
            rows.append(
                {
                    "timestamp": _local_naive_timestamp(timestamp),
                    "household_id": str(household_id),
                    column_name: float(value),
                }
            )
    return pd.DataFrame(rows)


def _model_baseload_frame(results: Mapping[str, Any]) -> pd.DataFrame:
    timestamps = [_local_naive_timestamp(value) for value in results.get("timestamps", [])]
    total = _profile_dict_to_frame(
        dict(results.get("household_nonthermal_profiles") or results.get("household_profiles", {})),
        timestamps,
        column_name="total_W",
    )
    base = _profile_dict_to_frame(dict(results.get("household_base_profiles", {})), timestamps, column_name="base_W")
    events = _profile_dict_to_frame(dict(results.get("household_event_profiles", {})), timestamps, column_name="events_W")
    lighting = _profile_dict_to_frame(dict(results.get("household_lighting_profiles", {})), timestamps, column_name="lighting_W")
    occupancy = _profile_dict_to_frame(dict(results.get("household_occupancy_profiles", {})), timestamps, column_name="occupancy")
    frame = total
    for other in (base, events, lighting, occupancy):
        if not other.empty:
            frame = frame.merge(other, on=["timestamp", "household_id"], how="left")
    def numeric_column(column_name: str) -> pd.Series:
        if column_name not in frame.columns:
            return pd.Series(0.0, index=frame.index)
        return pd.to_numeric(frame[column_name], errors="coerce").fillna(0.0)

    frame["base_W"] = numeric_column("base_W")
    frame["events_W"] = numeric_column("events_W")
    frame["lighting_W"] = numeric_column("lighting_W")
    frame["appliances_W"] = frame["base_W"] + frame["events_W"]
    if "occupancy" in frame.columns:
        frame["occupancy"] = pd.to_numeric(frame["occupancy"], errors="coerce").fillna(0.0)
    else:
        frame["occupancy"] = 0.0
    frame["generator"] = "model_v3"
    return frame


def _aggregate_series(frame: pd.DataFrame, column_name: str) -> pd.Series:
    grouped = frame.groupby("timestamp")[column_name].mean().sort_index()
    index = pd.DatetimeIndex(grouped.index)
    if index.tz is not None:
        index = index.tz_localize(None)
    grouped.index = index
    return pd.to_numeric(grouped, errors="coerce").fillna(0.0)


def _align_pair(model: pd.Series, reference: pd.Series) -> pd.DataFrame:
    aligned = pd.concat(
        [
            model.rename("model"),
            reference.rename("reference"),
        ],
        axis=1,
        join="inner",
    ).dropna()
    aligned.index.name = "timestamp"
    return aligned.reset_index()


def _autocorr(series: pd.Series, lag_steps: int) -> float:
    values = pd.to_numeric(series, errors="coerce").fillna(0.0).to_numpy(dtype=float)
    if lag_steps <= 0 or lag_steps >= len(values):
        return 0.0
    left = values[:-lag_steps]
    right = values[lag_steps:]
    if np.std(left) < 1e-9 or np.std(right) < 1e-9:
        return 0.0
    return float(np.corrcoef(left, right)[0, 1])


def _daily_weekly_metrics(model: pd.Series, reference: pd.Series, timestep_seconds: int) -> dict[str, float]:
    model_daily = model.resample("D").sum()
    ref_daily = reference.resample("D").sum()
    model_weekly = model.resample("W").sum()
    ref_weekly = reference.resample("W").sum()
    lag_24 = max(int(round(24 * 3600 / max(timestep_seconds, 1))), 1)
    lag_168 = max(int(round(168 * 3600 / max(timestep_seconds, 1))), 1)

    def cv(values: pd.Series) -> float:
        mean = float(values.mean())
        return float(values.std(ddof=0) / mean) if abs(mean) > 1e-9 else 0.0

    def weekday_weekend_ratio(values: pd.Series) -> float:
        weekday = values[values.index.dayofweek < 5]
        weekend = values[values.index.dayofweek >= 5]
        weekend_mean = float(weekend.mean()) if len(weekend) else 0.0
        return float(weekday.mean() / weekend_mean) if abs(weekend_mean) > 1e-9 else 0.0

    return {
        "daily_energy_cv_model": cv(model_daily),
        "daily_energy_cv_reference": cv(ref_daily),
        "daily_energy_cv_delta": cv(model_daily) - cv(ref_daily),
        "weekly_energy_cv_model": cv(model_weekly),
        "weekly_energy_cv_reference": cv(ref_weekly),
        "weekday_weekend_ratio_model": weekday_weekend_ratio(model_daily),
        "weekday_weekend_ratio_reference": weekday_weekend_ratio(ref_daily),
        "autocorrelation_lag_24h_model": _autocorr(model, lag_24),
        "autocorrelation_lag_24h_reference": _autocorr(reference, lag_24),
        "autocorrelation_lag_168h_model": _autocorr(model, lag_168),
        "autocorrelation_lag_168h_reference": _autocorr(reference, lag_168),
    }


def _peakiness_metrics(model: pd.Series, reference: pd.Series) -> dict[str, float]:
    model_values = model.to_numpy(dtype=float)
    reference_values = reference.to_numpy(dtype=float)
    ldc_model = compute_ldc(model_values)
    ldc_reference = compute_ldc(reference_values)
    top_n = max(int(0.10 * min(len(ldc_model), len(ldc_reference))), 1)
    reference_top_mean = float(np.mean(ldc_reference[:top_n])) if top_n else 0.0

    def ratio(values: np.ndarray, high: float, low: float) -> float:
        denominator = float(np.percentile(values, low))
        return float(np.percentile(values, high) / denominator) if abs(denominator) > 1e-9 else 0.0

    return {
        "p95_p50_model": ratio(model_values, 95, 50),
        "p95_p50_reference": ratio(reference_values, 95, 50),
        "p99_p50_model": ratio(model_values, 99, 50),
        "p99_p50_reference": ratio(reference_values, 99, 50),
        "load_factor_model": float(np.mean(model_values) / max(np.max(model_values), 1e-9)),
        "load_factor_reference": float(np.mean(reference_values) / max(np.max(reference_values), 1e-9)),
        "top_decile_ldc_nmae": (
            float(np.mean(np.abs(ldc_model[:top_n] - ldc_reference[:top_n])) / reference_top_mean)
            if abs(reference_top_mean) > 1e-9
            else 0.0
        ),
    }


def _mean_daily_profile(series: pd.Series) -> pd.Series:
    return series.groupby(series.index.hour).mean()


def _profile_shape_metrics(model: pd.Series, reference: pd.Series) -> dict[str, float]:
    model_daily = _mean_daily_profile(model)
    ref_daily = _mean_daily_profile(reference)
    aligned = pd.concat([model_daily.rename("model"), ref_daily.rename("reference")], axis=1).dropna()
    if len(aligned) > 1 and aligned["model"].std() > 1e-9 and aligned["reference"].std() > 1e-9:
        correlation = float(aligned["model"].corr(aligned["reference"]))
    else:
        correlation = 0.0
    ref_mean = float(aligned["reference"].mean()) if len(aligned) else 0.0
    return {
        "mean_diurnal_correlation": correlation,
        "mean_diurnal_nmae": (
            float(np.mean(np.abs(aligned["model"] - aligned["reference"])) / ref_mean)
            if abs(ref_mean) > 1e-9
            else 0.0
        ),
        "evening_morning_ratio_model": _window_ratio(model, evening=(17, 22), morning=(6, 10)),
        "evening_morning_ratio_reference": _window_ratio(reference, evening=(17, 22), morning=(6, 10)),
    }


def _window_ratio(series: pd.Series, *, evening: tuple[int, int], morning: tuple[int, int]) -> float:
    evening_values = series[(series.index.hour >= evening[0]) & (series.index.hour < evening[1])]
    morning_values = series[(series.index.hour >= morning[0]) & (series.index.hour < morning[1])]
    morning_mean = float(morning_values.mean()) if len(morning_values) else 0.0
    return float(evening_values.mean() / morning_mean) if abs(morning_mean) > 1e-9 else 0.0


def _profiles_from_frame(frame: pd.DataFrame, column_name: str) -> list[np.ndarray]:
    return [
        group.sort_values("timestamp")[column_name].to_numpy(dtype=float)
        for _, group in frame.groupby("household_id")
    ]


def _pairwise_correlation_mean(frame: pd.DataFrame, column_name: str) -> float:
    pivot = frame.pivot_table(index="timestamp", columns="household_id", values=column_name, aggfunc="mean")
    if pivot.shape[1] < 2:
        return 0.0
    corr = pivot.corr().to_numpy(dtype=float)
    upper = corr[np.triu_indices_from(corr, k=1)]
    upper = upper[np.isfinite(upper)]
    return float(np.mean(upper)) if len(upper) else 0.0


def _diversity_metrics(model_frame: pd.DataFrame, reference_frame: pd.DataFrame) -> dict[str, float]:
    model_aggregate = model_frame.groupby("timestamp")["total_W"].sum().sort_index().to_numpy(dtype=float)
    ref_aggregate = reference_frame.groupby("timestamp")["total_W"].sum().sort_index().to_numpy(dtype=float)
    model_profiles = _profiles_from_frame(model_frame, "total_W")
    ref_profiles = _profiles_from_frame(reference_frame, "total_W")
    model_individual_peak_sum = float(sum(np.max(profile) for profile in model_profiles)) if model_profiles else 0.0
    ref_individual_peak_sum = float(sum(np.max(profile) for profile in ref_profiles)) if ref_profiles else 0.0
    model_aggregate_peak = float(np.max(model_aggregate)) if len(model_aggregate) else 0.0
    ref_aggregate_peak = float(np.max(ref_aggregate)) if len(ref_aggregate) else 0.0
    return {
        "diversity_factor_model": compute_diversity_factor(model_profiles, model_aggregate),
        "diversity_factor_reference": compute_diversity_factor(ref_profiles, ref_aggregate),
        "coincidence_factor_model": model_aggregate_peak / max(model_individual_peak_sum, 1e-9),
        "coincidence_factor_reference": ref_aggregate_peak / max(ref_individual_peak_sum, 1e-9),
        "pairwise_correlation_mean_model": _pairwise_correlation_mean(model_frame, "total_W"),
        "pairwise_correlation_mean_reference": _pairwise_correlation_mean(reference_frame, "total_W"),
    }


def _component_shape_metrics(model_frame: pd.DataFrame, reference_frame: pd.DataFrame) -> dict[str, dict[str, float]]:
    components: dict[str, dict[str, float]] = {}
    for component_name, column_name in (
        ("appliances", "appliances_W"),
        ("lighting", "lighting_W"),
    ):
        if column_name not in model_frame.columns or column_name not in reference_frame.columns:
            continue
        model_series = _aggregate_series(model_frame, column_name)
        reference_series = _aggregate_series(reference_frame, column_name)
        aligned = _align_pair(model_series, reference_series)
        aligned_model = pd.Series(aligned["model"].to_numpy(dtype=float), index=pd.DatetimeIndex(aligned["timestamp"]))
        aligned_reference = pd.Series(aligned["reference"].to_numpy(dtype=float), index=pd.DatetimeIndex(aligned["timestamp"]))
        components[component_name] = _profile_shape_metrics(aligned_model, aligned_reference)
    return components


def _metrics(model_frame: pd.DataFrame, reference_frame: pd.DataFrame, timestep_seconds: int) -> dict[str, Any]:
    model_total = _aggregate_series(model_frame, "total_W")
    ref_total = _aggregate_series(reference_frame, "total_W")
    aligned = _align_pair(model_total, ref_total)
    aligned_model = pd.Series(aligned["model"].to_numpy(dtype=float), index=pd.DatetimeIndex(aligned["timestamp"]))
    aligned_reference = pd.Series(aligned["reference"].to_numpy(dtype=float), index=pd.DatetimeIndex(aligned["timestamp"]))
    model_occ = _aggregate_series(model_frame, "occupancy")
    ref_occ = _aggregate_series(reference_frame, "occupancy")
    aligned_occ = _align_pair(model_occ, ref_occ)
    occ_model = pd.Series(aligned_occ["model"].to_numpy(dtype=float), index=pd.DatetimeIndex(aligned_occ["timestamp"]))
    occ_ref = pd.Series(aligned_occ["reference"].to_numpy(dtype=float), index=pd.DatetimeIndex(aligned_occ["timestamp"]))

    return {
        "shape": _profile_shape_metrics(aligned_model, aligned_reference),
        "temporal": compute_temporal_metrics(aligned_model, aligned_reference),
        "variance": compute_variance_metrics(aligned_model.to_numpy(dtype=float), aligned_reference.to_numpy(dtype=float)),
        "distribution": compute_distribution_metrics(aligned_model.to_numpy(dtype=float), aligned_reference.to_numpy(dtype=float)),
        "daily_weekly": _daily_weekly_metrics(aligned_model, aligned_reference, timestep_seconds),
        "peakiness_load_duration": _peakiness_metrics(aligned_model, aligned_reference),
        "component_shape": _component_shape_metrics(model_frame, reference_frame),
        "occupancy": {
            **compute_distribution_metrics(occ_model.to_numpy(dtype=float), occ_ref.to_numpy(dtype=float)),
            **{
                f"temporal_{key}": value
                for key, value in compute_temporal_metrics(occ_model, occ_ref).items()
            },
            "mean_occupancy_model": float(occ_model.mean()) if len(occ_model) else 0.0,
            "mean_occupancy_reference": float(occ_ref.mean()) if len(occ_ref) else 0.0,
            "active_fraction_model": float((occ_model >= 0.5).mean()) if len(occ_model) else 0.0,
            "active_fraction_reference": float((occ_ref >= 0.5).mean()) if len(occ_ref) else 0.0,
        },
        "diversity": _diversity_metrics(model_frame, reference_frame),
        "alignment": {
            "aligned_steps": int(len(aligned)),
            "model_households": int(model_frame["household_id"].nunique()),
            "reference_households": int(reference_frame["household_id"].nunique()),
        },
    }


def _write_plots(report_dir: Path, model_frame: pd.DataFrame, reference_frame: pd.DataFrame) -> dict[str, str]:
    report_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, str] = {}
    model_total = _aggregate_series(model_frame, "total_W")
    ref_total = _aggregate_series(reference_frame, "total_W")
    model_occ = _aggregate_series(model_frame, "occupancy")
    ref_occ = _aggregate_series(reference_frame, "occupancy")

    daily_path = report_dir / "richardson_mean_daily_baseload.png"
    plt.figure(figsize=(8, 4))
    plt.plot(_mean_daily_profile(model_total), label="model_v3")
    plt.plot(_mean_daily_profile(ref_total), label="Richardson")
    plt.xlabel("Hour")
    plt.ylabel("Mean non-thermal load (W/household)")
    plt.title("Mean Daily Baseload Shape")
    plt.tight_layout()
    plt.legend()
    plt.savefig(daily_path)
    plt.close()
    paths["mean_daily_baseload"] = daily_path.name

    ldc_path = report_dir / "richardson_load_duration_curve.png"
    plt.figure(figsize=(8, 4))
    plt.plot(compute_ldc(model_total.to_numpy(dtype=float)), label="model_v3")
    plt.plot(compute_ldc(ref_total.to_numpy(dtype=float)), label="Richardson")
    plt.ylabel("Non-thermal load (W/household)")
    plt.title("Load Duration Curve")
    plt.tight_layout()
    plt.legend()
    plt.savefig(ldc_path)
    plt.close()
    paths["load_duration_curve"] = ldc_path.name

    occ_path = report_dir / "richardson_mean_daily_occupancy.png"
    plt.figure(figsize=(8, 4))
    plt.plot(_mean_daily_profile(model_occ), label="model_v3")
    plt.plot(_mean_daily_profile(ref_occ), label="Richardson")
    plt.xlabel("Hour")
    plt.ylabel("Mean active occupancy")
    plt.title("Mean Daily Active Occupancy")
    plt.tight_layout()
    plt.legend()
    plt.savefig(occ_path)
    plt.close()
    paths["mean_daily_occupancy"] = occ_path.name
    return paths


def _write_report(
    report_path: Path,
    *,
    metrics: Mapping[str, Any],
    reference_metadata: Mapping[str, Any],
    plot_paths: Mapping[str, str],
    config: Mapping[str, Any],
    quick_metadata: Mapping[str, Any],
) -> None:
    lines = [
        "# Richardson Stochastic Baseload Validation",
        "",
        *validation_type_lines(
            "Synthetic structural reference validation",
            (
                "Richardsonpy stochastic occupancy, appliance, and lighting profiles are used as an "
                "independent synthetic benchmark for non-thermal household baseload shape, occupancy timing, "
                "peakiness, and diversity."
            ),
        ),
        "",
        *runtime_context_lines(config, quick_metadata=quick_metadata, n_steps=dict(metrics.get("alignment", {})).get("aligned_steps")),
        "",
        "## Reference Generator",
        "",
        f"- generator: `{reference_metadata.get('generator')}`",
        f"- households: `{reference_metadata.get('n_households')}`",
        f"- timestep seconds: `{reference_metadata.get('timestep_seconds')}`",
        f"- seed: `{reference_metadata.get('seed')}`",
        f"- shape normalized to model annualized energy: `{reference_metadata.get('shape_normalized_to_model_annualized_energy', False)}`",
        f"- limitation: {reference_metadata.get('limitations')}",
        "",
        "## Key Metrics",
        "",
        "| Group | Metric | Model | Richardson | Delta/Error |",
        "| --- | --- | ---: | ---: | ---: |",
        f"| Shape | mean diurnal correlation | {metrics['shape']['mean_diurnal_correlation']:.3f} | 1.000 | {1.0 - metrics['shape']['mean_diurnal_correlation']:.3f} |",
        f"| Shape | mean diurnal NMAE | {metrics['shape']['mean_diurnal_nmae']:.3f} | 0.000 | {metrics['shape']['mean_diurnal_nmae']:.3f} |",
        f"| Daily/weekly | daily energy CV | {metrics['daily_weekly']['daily_energy_cv_model']:.3f} | {metrics['daily_weekly']['daily_energy_cv_reference']:.3f} | {metrics['daily_weekly']['daily_energy_cv_delta']:.3f} |",
        f"| Peakiness | P95/P50 | {metrics['peakiness_load_duration']['p95_p50_model']:.3f} | {metrics['peakiness_load_duration']['p95_p50_reference']:.3f} | {metrics['peakiness_load_duration']['p95_p50_model'] - metrics['peakiness_load_duration']['p95_p50_reference']:.3f} |",
        f"| Peakiness | top-decile LDC NMAE | {metrics['peakiness_load_duration']['top_decile_ldc_nmae']:.3f} | 0.000 | {metrics['peakiness_load_duration']['top_decile_ldc_nmae']:.3f} |",
        f"| Appliances | mean diurnal correlation | {metrics.get('component_shape', {}).get('appliances', {}).get('mean_diurnal_correlation', 0.0):.3f} | 1.000 | {1.0 - metrics.get('component_shape', {}).get('appliances', {}).get('mean_diurnal_correlation', 0.0):.3f} |",
        f"| Lighting | mean diurnal correlation | {metrics.get('component_shape', {}).get('lighting', {}).get('mean_diurnal_correlation', 0.0):.3f} | 1.000 | {1.0 - metrics.get('component_shape', {}).get('lighting', {}).get('mean_diurnal_correlation', 0.0):.3f} |",
        f"| Diversity | diversity factor | {metrics['diversity']['diversity_factor_model']:.3f} | {metrics['diversity']['diversity_factor_reference']:.3f} | {metrics['diversity']['diversity_factor_model'] - metrics['diversity']['diversity_factor_reference']:.3f} |",
        f"| Occupancy | active fraction | {metrics['occupancy']['active_fraction_model']:.3f} | {metrics['occupancy']['active_fraction_reference']:.3f} | {metrics['occupancy']['active_fraction_model'] - metrics['occupancy']['active_fraction_reference']:.3f} |",
        "",
        "## Plots",
        "",
    ]
    lines.extend(f"- {name}: `{path}`" for name, path in sorted(plot_paths.items()))
    lines.extend(
        [
            "",
            *artifact_interpretation_lines(
                config,
                quick_metadata=quick_metadata,
                n_steps=dict(metrics.get("alignment", {})).get("aligned_steps"),
                extra=(
                    "This validation treats Richardson as an independent synthetic benchmark. It does not prove "
                    "Belgian empirical accuracy and should be paired with measured aggregate validation."
                ),
            ),
        ]
    )
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_validation(
    *,
    config_path: str | Path | None = None,
    quick: bool = False,
    mode: str = "shape-normalized",
    n_households: int | None = None,
    seed: int | None = None,
    allow_fallback: bool = False,
) -> dict[str, Any]:
    start = perf_counter()
    resolved_config_path = resolve_config_path(config_path)
    config = load_config(config_path=resolved_config_path)
    prepared_config, quick_metadata = apply_quick_validation_mode(config, quick_mode=quick)
    if n_households is not None:
        cohort_cfg = prepared_config.setdefault("cohort", {})
        cohort_cfg["n_households"] = max(int(n_households), 1)
        cohort_cfg["minimum_households"] = max(int(n_households), 1)
    if seed is not None:
        prepared_config.setdefault("cohort", {})["random_seed"] = int(seed)

    results = run_cohort_simulation(config=prepared_config)
    timestep_seconds = int(results.get("timestep_seconds") or dict(prepared_config.get("data", {})).get("target_resolution_seconds", 3600))
    model_frame = _model_baseload_frame(results)
    reference = generate_richardson_reference(
        config=prepared_config,
        model_profiles=dict(results.get("household_nonthermal_profiles", {})),
        sampled_population=list(results.get("household_summaries") or results.get("sampled_population", [])),
        n_households=int(results.get("n_households", model_frame["household_id"].nunique())),
        timestep_seconds=timestep_seconds,
        seed=int(dict(prepared_config.get("cohort", {})).get("random_seed", 42)),
        mode=mode,
        allow_fallback=allow_fallback,
        target_timestamps=sorted(model_frame["timestamp"].drop_duplicates().tolist()),
    )
    reference_frame = reference.profile_frame
    metrics = _metrics(model_frame, reference_frame, timestep_seconds)

    report_dir = validation_output_dir(prepared_config, dataset_name="richardson")
    model_path = write_frame_csv(report_dir / "model_baseload_households.csv", model_frame)
    reference_path = write_frame_csv(report_dir / "richardson_reference_households.csv", reference_frame)
    aligned_path = write_frame_csv(
        report_dir / "aggregate_alignment.csv",
        _align_pair(_aggregate_series(model_frame, "total_W"), _aggregate_series(reference_frame, "total_W")),
    )
    metrics_path = write_json(report_dir / "metrics.json", metrics)
    metadata_path = write_json(report_dir / "reference_metadata.json", reference.metadata)
    plot_paths = _write_plots(report_dir, model_frame, reference_frame)
    report_path = report_dir / "richardson_validation_report.md"
    _write_report(
        report_path,
        metrics=metrics,
        reference_metadata=reference.metadata,
        plot_paths=plot_paths,
        config=prepared_config,
        quick_metadata=quick_metadata,
    )
    elapsed = perf_counter() - start
    result = {
        "metrics": metrics,
        "reference_metadata": reference.metadata,
        "report_path": str(report_path),
        "artifacts": {
            "model_baseload_households": str(model_path),
            "richardson_reference_households": str(reference_path),
            "aggregate_alignment": str(aligned_path),
            "metrics": str(metrics_path),
            "reference_metadata": str(metadata_path),
            **{f"plot_{name}": str(report_dir / path) for name, path in plot_paths.items()},
        },
        "runner_timing": {
            "elapsed_seconds": elapsed,
            "quick_mode": bool(quick_metadata.get("enabled", False)),
            "n_steps": int(metrics["alignment"]["aligned_steps"]),
        },
    }
    LOGGER.info("richardson validation complete report=%s elapsed_s=%.1f", report_path, elapsed)
    return result


def main() -> None:
    parser = build_runner_cli("Validate model_v3 stochastic baseload against Richardson profiles.")
    parser.add_argument("--mode", default="shape-normalized", choices=["shape-normalized", "absolute"])
    parser.add_argument("--n-households", type=int, default=None)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument(
        "--allow-fallback",
        action="store_true",
        help="Use deterministic Richardson-like fallback profiles instead of importing richardsonpy.",
    )
    args = parser.parse_args()
    configure_runner_logging([__name__, "model_v3.cohort.cohort_engine"])
    result = run_validation(
        config_path=args.config,
        quick=args.quick,
        mode=args.mode,
        n_households=args.n_households,
        seed=args.seed,
        allow_fallback=args.allow_fallback,
    )
    print(format_elapsed_summary("richardson", result))


if __name__ == "__main__":
    main()
