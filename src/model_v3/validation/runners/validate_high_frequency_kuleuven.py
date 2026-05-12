"""High-frequency case-study validation against KU Leuven household data."""

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
from scipy import stats

from model_v3.adapters.kuleuven_loader import load_kuleuven_profiles
from model_v3.output.persistence import ensure_dir, output_root, write_frame_csv, write_json
from model_v3.validation.runners.runner_utils import (
    apply_quick_validation_mode,
    artifact_interpretation_lines,
    build_runner_cli,
    configure_runner_logging,
    format_elapsed_summary,
    runtime_context_lines,
)
from model_v3.validation.runners.model_runner import run_validation_model
from model_v3.validation.utils.alignment import align_timeseries


LOGGER = logging.getLogger(__name__)
_BELGIUM_TZ = "Europe/Brussels"


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


def _daily_max_summary(series_w: pd.Series) -> dict[str, float]:
    """Summarise daily maxima of a power series."""

    daily = series_w.resample("D").max().dropna()
    if daily.empty:
        return {"count": 0.0, "mean_W": 0.0, "p90_W": 0.0, "max_W": 0.0}
    return {
        "count": float(len(daily)),
        "mean_W": float(daily.mean()),
        "p90_W": float(daily.quantile(0.9)),
        "max_W": float(daily.max()),
    }


def _spike_summary(series_w: pd.Series) -> dict[str, float]:
    """Summarise spikes above mean + 2*std."""

    if series_w.empty:
        return {"threshold_W": 0.0, "count": 0.0, "share": 0.0, "mean_spike_W": 0.0}
    threshold = float(series_w.mean() + 2.0 * series_w.std(ddof=0))
    spikes = series_w[series_w > threshold]
    return {
        "threshold_W": threshold,
        "count": float(len(spikes)),
        "share": float(len(spikes) / max(len(series_w), 1)),
        "mean_spike_W": float(spikes.mean()) if len(spikes) else 0.0,
    }


def _ramp_summary(series_w: pd.Series) -> tuple[pd.Series, dict[str, float]]:
    """Summarise ramp-rate behaviour in W/min."""

    if len(series_w) < 2:
        empty = pd.Series(dtype=float)
        return empty, {"mean_abs_W_per_min": 0.0, "p90_abs_W_per_min": 0.0, "max_abs_W_per_min": 0.0}
    deltas_minutes = series_w.index.to_series().diff().dt.total_seconds().dropna() / 60.0
    deltas_power = series_w.diff().dropna()
    aligned_minutes = deltas_minutes.reindex(deltas_power.index)
    ramps = deltas_power / aligned_minutes.replace(0.0, np.nan)
    ramps = ramps.replace([np.inf, -np.inf], np.nan).dropna()
    abs_ramps = ramps.abs()
    if abs_ramps.empty:
        return ramps, {"mean_abs_W_per_min": 0.0, "p90_abs_W_per_min": 0.0, "max_abs_W_per_min": 0.0}
    return ramps, {
        "mean_abs_W_per_min": float(abs_ramps.mean()),
        "p90_abs_W_per_min": float(abs_ramps.quantile(0.9)),
        "max_abs_W_per_min": float(abs_ramps.max()),
    }


def _moment_summary(series_w: pd.Series) -> dict[str, float]:
    """Compute variance, skewness, and kurtosis."""

    values = series_w.to_numpy(dtype=float)
    if len(values) == 0:
        return {"variance_W2": 0.0, "skewness": 0.0, "kurtosis": 0.0}
    return {
        "variance_W2": float(np.var(values)),
        "skewness": float(stats.skew(values, bias=False)) if len(values) > 2 else 0.0,
        "kurtosis": float(stats.kurtosis(values, fisher=True, bias=False)) if len(values) > 3 else 0.0,
    }


def _comparison_block(model_series_w: pd.Series, house_series_w: pd.Series) -> dict[str, Any]:
    """Compute direct model-vs-house case-study comparison metrics."""

    aligned, alignment_info = align_timeseries(
        pd.DataFrame({"timestamp": model_series_w.index, "value": model_series_w.to_numpy(dtype=float)}),
        pd.DataFrame({"timestamp": house_series_w.index, "value": house_series_w.to_numpy(dtype=float)}),
        resolution="auto",
    )
    aligned_model = pd.Series(aligned["value_model"].to_numpy(dtype=float), index=pd.to_datetime(aligned["timestamp"]))
    aligned_house = pd.Series(aligned["value_data"].to_numpy(dtype=float), index=pd.to_datetime(aligned["timestamp"]))
    model_ramps, model_ramp_summary = _ramp_summary(aligned_model)
    house_ramps, house_ramp_summary = _ramp_summary(aligned_house)
    return {
        "alignment": alignment_info,
        "daily_max_model": _daily_max_summary(aligned_model),
        "daily_max_house": _daily_max_summary(aligned_house),
        "spikes_model": _spike_summary(aligned_model),
        "spikes_house": _spike_summary(aligned_house),
        "ramps_model": model_ramp_summary,
        "ramps_house": house_ramp_summary,
        "moments_model": _moment_summary(aligned_model),
        "moments_house": _moment_summary(aligned_house),
        "aligned_model": aligned_model,
        "aligned_house": aligned_house,
        "model_ramps": model_ramps,
        "house_ramps": house_ramps,
    }


def _select_24h_segment(house_series_kw: pd.Series, model_series_w: pd.Series) -> tuple[pd.Series, pd.Series]:
    """Pick the first contiguous 24h house segment and corresponding model data."""

    if house_series_kw.empty:
        empty = pd.Series(dtype=float)
        return empty, empty
    expected_step = pd.Timedelta(minutes=15)
    timestamps = pd.DatetimeIndex(house_series_kw.index)
    break_positions = np.where((timestamps[1:] - timestamps[:-1]) != expected_step)[0]
    start_idx = 0
    if len(break_positions) > 0:
        start_idx = 0
    end_timestamp = timestamps[start_idx] + pd.Timedelta(hours=24)
    house_segment = house_series_kw[(house_series_kw.index >= timestamps[start_idx]) & (house_series_kw.index < end_timestamp)]
    model_segment = model_series_w[(model_series_w.index >= timestamps[start_idx].floor("h")) & (model_series_w.index < end_timestamp.ceil("h"))]
    return house_segment, model_segment


def _plot_house_outputs(report_dir: Path, house_id: str, house_series_kw: pd.Series, model_series_w: pd.Series, comparison: Mapping[str, Any]) -> dict[str, str]:
    """Generate requested plots for one KU Leuven house."""

    paths: dict[str, str] = {}

    house_segment, model_segment = _select_24h_segment(house_series_kw, model_series_w)
    segment_path = report_dir / f"{house_id}_24h_segment.png"
    plt.figure(figsize=(10, 4))
    if not house_segment.empty:
        plt.plot(house_segment.index, house_segment.to_numpy(dtype=float), label=f"{house_id} 15-min case study", linewidth=0.8)
    if not model_segment.empty:
        plt.step(model_segment.index, model_segment.to_numpy(dtype=float) / 1000.0, where="post", label="Model hourly", linewidth=1.0)
    plt.ylabel("Power (kW)")
    plt.title(f"{house_id} 24h Segment")
    plt.xticks(rotation=45, ha="right")
    plt.tight_layout()
    plt.legend()
    plt.savefig(segment_path)
    plt.close()
    paths["segment"] = segment_path.name

    spike_path = report_dir / f"{house_id}_spike_comparison.png"
    plt.figure(figsize=(6, 4))
    spike_counts = [
        float(comparison["spikes_model"]["count"]),
        float(comparison["spikes_house"]["count"]),
    ]
    plt.bar(["Model", house_id], spike_counts)
    plt.ylabel("Spike count")
    plt.title(f"{house_id} Spike Comparison")
    plt.tight_layout()
    plt.savefig(spike_path)
    plt.close()
    paths["spikes"] = spike_path.name

    ramp_path = report_dir / f"{house_id}_ramp_histogram.png"
    plt.figure(figsize=(8, 4))
    model_ramps = pd.Series(comparison["model_ramps"]).to_numpy(dtype=float) if len(comparison["model_ramps"]) else np.array([])
    house_ramps = pd.Series(comparison["house_ramps"]).to_numpy(dtype=float) if len(comparison["house_ramps"]) else np.array([])
    if len(model_ramps):
        plt.hist(model_ramps, bins=40, alpha=0.5, label="Model")
    if len(house_ramps):
        plt.hist(house_ramps, bins=40, alpha=0.5, label=house_id)
    plt.xlabel("Ramp rate (W/min)")
    plt.ylabel("Count")
    plt.title(f"{house_id} Ramp Rate Histogram")
    plt.tight_layout()
    plt.legend()
    plt.savefig(ramp_path)
    plt.close()
    paths["ramps"] = ramp_path.name

    return paths


def _write_case_study_report(
    report_path: Path,
    *,
    houses: Mapping[str, Mapping[str, Any]],
    reference_year: int,
    limitations: str,
    config: Mapping[str, Any],
    quick_metadata: Mapping[str, Any],
    n_steps: int,
) -> None:
    """Write the KU Leuven high-frequency case-study report."""

    lines = [
        "# Validation Report — Model v3 KU Leuven High-Frequency",
        "",
        "## Validation Type",
        "",
        "- classification: high-frequency/event realism",
        "- interpretation: Three-household high-frequency case-study comparison; not a statistical validation claim.",
        "",
        *runtime_context_lines(config, quick_metadata=quick_metadata, n_steps=n_steps),
        "",
        *artifact_interpretation_lines(
            config,
            quick_metadata=quick_metadata,
            n_steps=n_steps,
            extra="The monitored households are case studies; use this report for event-realism diagnostics only.",
        ),
        "",
        "## Setup",
        "",
        f"- model reference year: {reference_year}",
        "- KU Leuven electricity files are loaded in chunks and reduced to 15-minute mean power profiles.",
        "- Direct model-vs-house comparisons are aligned to the highest common resolution with the model output.",
        "",
    ]

    for house_id, payload in houses.items():
        comparison = dict(payload["comparison"])
        plots = dict(payload["plots"])
        lines.extend(
            [
                f"## {house_id}",
                "",
                f"- aligned timestamps: {comparison['alignment'].get('matched_timestamps')}",
                f"- aligned comparison resolution (s): {comparison['alignment'].get('target_resolution_seconds')}",
                "",
                "### Daily Max Distribution",
                "",
                f"- model mean daily max (W): {float(comparison['daily_max_model']['mean_W']):.6f}",
                f"- house mean daily max (W): {float(comparison['daily_max_house']['mean_W']):.6f}",
                f"- model p90 daily max (W): {float(comparison['daily_max_model']['p90_W']):.6f}",
                f"- house p90 daily max (W): {float(comparison['daily_max_house']['p90_W']):.6f}",
                "",
                "### Spike Detection",
                "",
                f"- model threshold (W): {float(comparison['spikes_model']['threshold_W']):.6f}",
                f"- house threshold (W): {float(comparison['spikes_house']['threshold_W']):.6f}",
                f"- model spike count: {float(comparison['spikes_model']['count']):.6f}",
                f"- house spike count: {float(comparison['spikes_house']['count']):.6f}",
                f"- model spike share: {float(comparison['spikes_model']['share']):.6f}",
                f"- house spike share: {float(comparison['spikes_house']['share']):.6f}",
                "",
                "### Ramp Rates",
                "",
                f"- model mean abs ramp (W/min): {float(comparison['ramps_model']['mean_abs_W_per_min']):.6f}",
                f"- house mean abs ramp (W/min): {float(comparison['ramps_house']['mean_abs_W_per_min']):.6f}",
                f"- model p90 abs ramp (W/min): {float(comparison['ramps_model']['p90_abs_W_per_min']):.6f}",
                f"- house p90 abs ramp (W/min): {float(comparison['ramps_house']['p90_abs_W_per_min']):.6f}",
                f"- model max abs ramp (W/min): {float(comparison['ramps_model']['max_abs_W_per_min']):.6f}",
                f"- house max abs ramp (W/min): {float(comparison['ramps_house']['max_abs_W_per_min']):.6f}",
                "",
                "### Statistical Moments",
                "",
                f"- model variance (W^2): {float(comparison['moments_model']['variance_W2']):.6f}",
                f"- house variance (W^2): {float(comparison['moments_house']['variance_W2']):.6f}",
                f"- model skewness: {float(comparison['moments_model']['skewness']):.6f}",
                f"- house skewness: {float(comparison['moments_house']['skewness']):.6f}",
                f"- model kurtosis: {float(comparison['moments_model']['kurtosis']):.6f}",
                f"- house kurtosis: {float(comparison['moments_house']['kurtosis']):.6f}",
                "",
                "### Visualisations",
                "",
                f"- 24h segment: ![Segment]({plots['segment']})",
                f"- spike comparison: ![Spikes]({plots['spikes']})",
                f"- ramp histogram: ![Ramps]({plots['ramps']})",
                "",
            ]
        )

    lines.extend(["## Limitations", "", limitations, ""])
    report_path.write_text("\n".join(lines), encoding="utf-8")


def validate_high_frequency_kuleuven(config: Mapping[str, Any], quick_mode: bool | None = None) -> dict[str, Any]:
    """Run a KU Leuven high-frequency case-study validation."""

    runner_started = perf_counter()
    prepared_config, quick_metadata = apply_quick_validation_mode(config=config, quick_mode=quick_mode)
    validation_cfg = dict(prepared_config.get("validation", {}))
    kuleuven_cfg = dict(validation_cfg.get("kuleuven", {}))
    if not kuleuven_cfg.get("enabled", False):
        raise ValueError("validation.kuleuven.enabled must be true for KU Leuven validation")

    reference_year = int(dict(prepared_config.get("simulation", {})).get("reference_year", 2013))
    LOGGER.info(
        "kuleuven_validation.start quick_mode=%s model_source=%s reference_year=%s",
        quick_metadata["enabled"],
        validation_cfg.get("model_source", "cohort"),
        reference_year,
    )
    model_results, model_frame = run_validation_model(config=prepared_config, validation_cfg=validation_cfg)
    model_series_w = pd.Series(model_frame["value"].to_numpy(dtype=float), index=pd.to_datetime(model_frame["timestamp"])).sort_index()
    if model_series_w.index.tz is None:
        model_series_w.index = pd.DatetimeIndex(model_series_w.index).tz_localize(_BELGIUM_TZ)
    else:
        model_series_w.index = pd.DatetimeIndex(model_series_w.index).tz_convert(_BELGIUM_TZ)

    houses = load_kuleuven_profiles(kuleuven_cfg.get("base_path", "inputs/load_profiles/kul"))
    report_dir = ensure_dir(output_root(prepared_config) / "validation" / "kuleuven_high_freq")
    report_root = ensure_dir(output_root(prepared_config) / "validation")

    house_payloads: dict[str, Any] = {}
    all_metrics: dict[str, Any] = {}
    for house_id, house_series_kw in houses.items():
        house_series_kw, mapping_warnings = _normalise_to_reference_year(house_series_kw, reference_year)
        for warning in mapping_warnings:
            LOGGER.warning("kuleuven_validation.warning house=%s %s", house_id, warning)
        house_series_w = house_series_kw * 1000.0
        comparison = _comparison_block(model_series_w, house_series_w)
        plots = _plot_house_outputs(report_dir, house_id, house_series_kw, model_series_w, comparison)
        aligned_frame = pd.DataFrame(
            {
                "timestamp": comparison["aligned_house"].index,
                "value_model_W": comparison["aligned_model"].to_numpy(dtype=float),
                "value_house_W": comparison["aligned_house"].to_numpy(dtype=float),
            }
        )
        write_frame_csv(report_dir / f"{house_id}_aligned_timeseries.csv", aligned_frame)
        house_payloads[house_id] = {"comparison": comparison, "plots": plots}
        all_metrics[house_id] = {
            key: value
            for key, value in comparison.items()
            if key not in {"aligned_model", "aligned_house", "model_ramps", "house_ramps"}
        }

    report_path = report_root / "validation_report_v3_kuleuven_high_freq.md"
    _write_case_study_report(
        report_path=report_path,
        houses=house_payloads,
        reference_year=reference_year,
        limitations=(
            "This is a high-frequency case-study validation based on three monitored households. "
            "It is not a statistical validation claim, and the model output remains hourly while the house data is reduced to 15-minute mean power before comparison."
        ),
        config=prepared_config,
        quick_metadata=quick_metadata,
        n_steps=int(model_results.get("n_steps", 0)),
    )
    write_json(
        report_dir / "metrics.json",
        {
            "houses": all_metrics,
            "quick_mode": quick_metadata,
            "reference_year": reference_year,
        },
    )

    elapsed_seconds = perf_counter() - runner_started
    runner_timing = {
        "elapsed_seconds": elapsed_seconds,
        "quick_mode": quick_metadata["enabled"],
        "n_steps": int(model_results.get("n_steps", 0)),
    }
    key_metrics = {
        house_id: {
            "spike_count_house": float(payload["comparison"]["spikes_house"]["count"]),
            "spike_count_model": float(payload["comparison"]["spikes_model"]["count"]),
            "p90_daily_max_house_W": float(payload["comparison"]["daily_max_house"]["p90_W"]),
            "p90_daily_max_model_W": float(payload["comparison"]["daily_max_model"]["p90_W"]),
        }
        for house_id, payload in house_payloads.items()
    }
    return {
        "report_path": str(report_path),
        "metrics_path": str(report_dir / "metrics.json"),
        "houses": all_metrics,
        "key_metrics": key_metrics,
        "runner_timing": runner_timing,
        "quick_mode": quick_metadata,
    }


if __name__ == "__main__":
    from pipelines.run_model_v3 import load_config

    args = build_runner_cli("Run KU Leuven high-frequency case-study validation for model_v3.").parse_args()
    configure_runner_logging(
        (
            __name__,
            "model_v3.cohort.cohort_engine",
            "model_v3.simulation.annual_runner",
            "model_v3.adapters.kuleuven_loader",
        )
    )
    result = validate_high_frequency_kuleuven(load_config(args.config), quick_mode=args.quick)
    print(result)
    print(format_elapsed_summary("kuleuven_high_freq", result))
