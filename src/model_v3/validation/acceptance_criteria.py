"""Acceptance criteria aligned with literature and ASHRAE-style thresholds."""

from __future__ import annotations

from typing import Any, Mapping


def _float_value(mapping: Mapping[str, Any], key: str, default: float) -> float:
    """Read a float threshold with a safe fallback."""

    try:
        return float(mapping.get(key, default))
    except (TypeError, ValueError):
        return float(default)


def check_acceptance(metrics: Mapping[str, float], thresholds: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Evaluate validation metrics against acceptable and good thresholds."""

    thresholds = dict(thresholds or {})
    acceptable = {
        "monthly_MBE_max": _float_value(thresholds, "monthly_MBE_max", 0.05),
        "monthly_CVRMSE_max": _float_value(thresholds, "monthly_CVRMSE_max", 0.15),
        "hourly_MBE_max": _float_value(thresholds, "hourly_MBE_max", 0.10),
        "hourly_CVRMSE_max": _float_value(thresholds, "hourly_CVRMSE_max", 0.30),
        "peak_MAE_kW_max": _float_value(thresholds, "peak_MAE_kW_max", 0.2),
        "quantile_error_kW_max": _float_value(thresholds, "quantile_error_kW_max", 0.2),
    }
    good = {
        "hourly_MBE_max": _float_value(thresholds, "good_hourly_MBE_max", 0.05),
        "hourly_CVRMSE_max": _float_value(thresholds, "good_hourly_CVRMSE_max", 0.20),
        "peak_MAE_kW_max": _float_value(thresholds, "good_peak_MAE_kW_max", 0.1),
        "quantile_error_kW_max": _float_value(thresholds, "good_quantile_error_kW_max", 0.1),
    }

    checks = {
        "monthly_MBE_ok": abs(float(metrics.get("MBE_monthly", 0.0))) <= acceptable["monthly_MBE_max"],
        "monthly_CVRMSE_ok": float(metrics.get("CVRMSE_monthly", 0.0)) <= acceptable["monthly_CVRMSE_max"],
        "hourly_MBE_ok": abs(float(metrics.get("MBE_hourly", 0.0))) <= acceptable["hourly_MBE_max"],
        "hourly_CVRMSE_ok": float(metrics.get("CVRMSE_hourly", 0.0)) <= acceptable["hourly_CVRMSE_max"],
        "peak_MAE_ok": float(metrics.get("peak_MAE_kW", 0.0)) <= acceptable["peak_MAE_kW_max"],
        "quantile_ok": (
            abs(float(metrics.get("P10_error_kW", 0.0))) <= acceptable["quantile_error_kW_max"]
            and abs(float(metrics.get("P90_error_kW", 0.0))) <= acceptable["quantile_error_kW_max"]
        ),
    }
    checks["overall"] = all(checks.values())
    good_checks = {
        "hourly_MBE_good": abs(float(metrics.get("MBE_hourly", 0.0))) <= good["hourly_MBE_max"],
        "hourly_CVRMSE_good": float(metrics.get("CVRMSE_hourly", 0.0)) <= good["hourly_CVRMSE_max"],
        "peak_MAE_good": float(metrics.get("peak_MAE_kW", 0.0)) <= good["peak_MAE_kW_max"],
        "quantile_good": (
            abs(float(metrics.get("P10_error_kW", 0.0))) <= good["quantile_error_kW_max"]
            and abs(float(metrics.get("P90_error_kW", 0.0))) <= good["quantile_error_kW_max"]
        ),
    }

    return {
        "checks": checks,
        "good_checks": good_checks,
        "acceptable_thresholds": acceptable,
        "good_thresholds": good,
    }
