"""Validation runner for literature-based annual baseline checks."""

from __future__ import annotations

if __package__ in {None, ""}:
    import sys
    from pathlib import Path as _Path

    sys.path.append(str(_Path(__file__).resolve().parents[3]))

import logging
from pathlib import Path
from time import perf_counter
from typing import Any, Mapping

import pandas as pd

from model_v3.baseline import normalized_modelled_electricity_split
from model_v3.output.persistence import validation_output_dir, write_frame_csv, write_json
from model_v3.simulation.annual_runner import run_annual_simulation
from model_v3.validation.core.metrics_end_use import compute_end_use_split
from model_v3.validation.runners.runner_utils import (
    apply_quick_validation_mode,
    artifact_interpretation_lines,
    build_runner_cli,
    configure_runner_logging,
    format_elapsed_summary,
    runtime_context_lines,
    validation_type_lines,
)
from model_v3.validation.utils.independence import assess_validation_independence

LOGGER = logging.getLogger(__name__)


def _within_range(value: float, bounds: list[float] | tuple[float, float] | None) -> bool:
    """Return True when a scalar lies inside an inclusive range."""

    if not bounds or len(bounds) != 2:
        return False
    low, high = float(bounds[0]), float(bounds[1])
    tolerance = 1e-6
    return (low - tolerance) <= float(value) <= (high + tolerance)


def _format_float(value: Any, digits: int, none_label: str = "n/a") -> str:
    """Format optional numeric values for markdown tables."""

    if value is None:
        return none_label
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return "n/a"


def validate_baseline_annual(config: Mapping[str, Any], quick_mode: bool | None = None) -> dict[str, Any]:
    """Check the deterministic annual run against literature annual targets."""

    runner_started = perf_counter()
    prepared_config, quick_metadata = apply_quick_validation_mode(config=config, quick_mode=quick_mode)
    baseline_cfg = dict(prepared_config.get("baseline", {}))
    simulation_cfg = dict(prepared_config.get("simulation", {}))
    electricity_split_cfg = dict(prepared_config.get("electricity_split", {}))
    LOGGER.info(
        "baseline_annual.start quick_mode=%s max_steps=%s",
        quick_metadata["enabled"],
        simulation_cfg.get("max_steps"),
    )
    if quick_metadata["enabled"]:
        LOGGER.info("baseline_annual.quick_mode overrides=%s", quick_metadata["overrides"])

    results = run_annual_simulation(config=prepared_config)
    frame = pd.DataFrame(results["profile_frame"]).copy()
    end_use = compute_end_use_split(frame, baseline_split=electricity_split_cfg)
    target_shares = normalized_modelled_electricity_split(electricity_split_cfg)
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
            "path": "literature://belgian_household_baseline",
            "data_role": ("validation",),
            "source_name": "baseline_annual",
        },
    )

    annual_summary = {
        "annual_electricity_kWh": float(results.get("annual_energy_kWh", 0.0)),
        "space_heating_thermal_kWh": float(results.get("space_heating_thermal_kWh", 0.0)),
        "dhw_thermal_kWh": float(results.get("dhw_thermal_kWh", 0.0)),
    }
    electricity_calibration = dict(results.get("electricity_calibration", {}))
    if simulation_cfg.get("max_steps") in {None, ""} and int(results.get("n_steps", 0)) >= 1000:
        assert 5000.0 < annual_summary["space_heating_thermal_kWh"] < 25000.0
        assert 1000.0 < annual_summary["dhw_thermal_kWh"] < 5000.0
    checks = {
        "annual_electricity_ok": _within_range(
            annual_summary["annual_electricity_kWh"],
            baseline_cfg.get("electricity_range_kWh"),
        ),
        "space_heating_thermal_ok": _within_range(
            annual_summary["space_heating_thermal_kWh"],
            baseline_cfg.get("space_heating_range_kWh"),
        ),
        "dhw_thermal_ok": _within_range(
            annual_summary["dhw_thermal_kWh"],
            baseline_cfg.get("dhw_range_kWh"),
        ),
    }
    checks["overall"] = all(checks.values())

    report_dir = validation_output_dir(config=prepared_config, dataset_name="baseline_annual")
    report_path = report_dir / "validation_report_v3_baseline_annual.md"
    lines = [
        "# Validation Report — Model v3 Annual Baseline",
        "",
        *validation_type_lines(
            "baseline/literature annual calibration",
            "Annual comparison against configured Belgian household literature targets and end-use shares.",
        ),
        "",
        *runtime_context_lines(prepared_config, quick_metadata=quick_metadata, n_steps=int(results.get("n_steps", 0))),
        "",
        *artifact_interpretation_lines(
            prepared_config,
            quick_metadata=quick_metadata,
            n_steps=int(results.get("n_steps", 0)),
            extra=(
                "Thermal annual validation requires a full annual horizon; calibrated electricity totals can match "
                "targets even when a truncated thermal run is not thesis-valid."
            ),
        ),
        "",
        "## Execution Mode",
        "",
        f"- quick mode: {quick_metadata['enabled']}",
        f"- debug only: {quick_metadata['debug_only']}",
        f"- max steps: {simulation_cfg.get('max_steps')}",
        f"- overrides: {', '.join(quick_metadata['overrides']) if quick_metadata['overrides'] else 'none'}",
        "",
        "## Annual baseline check",
        "",
        f"- annual electricity (kWh): {annual_summary['annual_electricity_kWh']:.3f}",
        f"- space heating thermal (kWh): {annual_summary['space_heating_thermal_kWh']:.3f}",
        f"- DHW thermal (kWh): {annual_summary['dhw_thermal_kWh']:.3f}",
        "",
        "## Baseline vs Literature",
        "",
        "| Quantity | Model | Literature range | Status |",
        "| --- | ---: | ---: | --- |",
        f"| Annual electricity (kWh) | {annual_summary['annual_electricity_kWh']:.3f} | {baseline_cfg.get('electricity_range_kWh')} | {'PASS' if checks['annual_electricity_ok'] else 'FAIL'} |",
        f"| Space heating thermal (kWh) | {annual_summary['space_heating_thermal_kWh']:.3f} | {baseline_cfg.get('space_heating_range_kWh')} | {'PASS' if checks['space_heating_thermal_ok'] else 'FAIL'} |",
        f"| DHW thermal (kWh) | {annual_summary['dhw_thermal_kWh']:.3f} | {baseline_cfg.get('dhw_range_kWh')} | {'PASS' if checks['dhw_thermal_ok'] else 'FAIL'} |",
        "",
        "## End-use shares",
        "",
        "| End use | Model share | Literature share | Abs. error |",
        "| --- | ---: | ---: | ---: |",
    ]
    for key in ("appliances", "lighting", "cooking", "dhw", "space_heating"):
        lines.append(
            f"| {key} | {end_use[f'{key}_share']:.3f} | {target_shares[key]:.3f} | {end_use[f'{key}_error']:.3f} |"
        )
    lines.extend(
        [
            "",
            "## Electricity calibration",
            "",
            "| End use | Raw kWh | Calibrated kWh | Target kWh | Scale factor |",
            "| --- | ---: | ---: | ---: | ---: |",
        ]
    )
    raw_kwh = dict(electricity_calibration.get("raw_annual_kWh_by_end_use", {}))
    calibrated_kwh = dict(electricity_calibration.get("calibrated_annual_kWh_by_end_use", {}))
    target_kwh = dict(electricity_calibration.get("target_annual_kWh_by_end_use", {}))
    scale_factors = dict(electricity_calibration.get("scale_factor_by_end_use", {}))
    for key in ("appliances", "lighting", "cooking", "dhw", "space_heating"):
        lines.append(
            f"| {key} | {_format_float(raw_kwh.get(key), 3)} | "
            f"{_format_float(calibrated_kwh.get(key), 3)} | "
            f"{_format_float(target_kwh.get(key), 3)} | "
            f"{_format_float(scale_factors.get(key), 6, none_label='fallback')} |"
        )
    lines.extend(
        [
            "",
            "## Normalization / Calibration Caveat",
            "",
            "The annual electricity values in this report are calibrated to the configured literature target split. "
            "Use raw kWh and scale factors to interpret pre-calibration behavior. Thermal space-heating and DHW "
            "totals are not normalized by this electricity calibration and require a full-horizon run for annual interpretation.",
            "",
            "## Validation Independence / Data Role",
            "",
            f"- dataset_independent: {independence.get('dataset_independent')}",
            f"- partial_overlap: {independence.get('partial_overlap')}",
            f"- validation_independence: {independence.get('validation_independence')}",
            f"- implications: {independence.get('implications')}",
            "",
            "## What this script does not validate",
            "",
            "This script checks annual totals and end-use shares against literature synthesis only. "
            "It does not validate hourly timing, variance realism, or measured-load agreement.",
            "",
        ]
    )
    report_path.write_text("\n".join(lines), encoding="utf-8")

    write_frame_csv(report_dir / "annual_profile.csv", frame)
    write_json(
        report_dir / "baseline_metrics.json",
        {
            "annual_summary": annual_summary,
            "end_use": end_use,
            "target_shares": target_shares,
            "electricity_calibration": electricity_calibration,
            "checks": checks,
            "independence": independence,
            "quick_mode": quick_metadata,
        },
    )
    elapsed_seconds = perf_counter() - runner_started
    runner_timing = {
        "elapsed_seconds": elapsed_seconds,
        "quick_mode": quick_metadata["enabled"],
        "n_steps": int(results.get("n_steps", 0)),
    }
    LOGGER.info(
        "baseline_annual.complete elapsed_s=%.1f n_steps=%s quick_mode=%s report=%s",
        elapsed_seconds,
        int(results.get("n_steps", 0)),
        quick_metadata["enabled"],
        report_path,
    )
    return {
        "annual_summary": annual_summary,
        "end_use": end_use,
        "target_shares": target_shares,
        "electricity_calibration": electricity_calibration,
        "checks": checks,
        "independence": independence,
        "report_path": str(report_path),
        "quick_mode": quick_metadata,
        "runner_timing": runner_timing,
    }


if __name__ == "__main__":
    from pipelines.run_model_v3 import load_config

    args = build_runner_cli("Validate annual baseline metrics for model_v3.").parse_args()
    configure_runner_logging((__name__, "model_v3.simulation.annual_runner"))
    result = validate_baseline_annual(load_config(args.config), quick_mode=args.quick)
    print(result)
    print(format_elapsed_summary("baseline_annual", result))
