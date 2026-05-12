"""Literature-based baseline helpers for model_v3."""

from __future__ import annotations

from typing import Any, Mapping


MODELLED_END_USE_KEYS = ("appliances", "lighting", "cooking", "dhw", "space_heating")


def _float_value(mapping: Mapping[str, Any], key: str, default: float) -> float:
    """Read a float from a mapping with a safe fallback."""

    try:
        return float(mapping.get(key, default))
    except (TypeError, ValueError):
        return float(default)


def baseline_config(config: Mapping[str, Any]) -> dict[str, float]:
    """Return the configured literature baseline."""

    return dict(config.get("baseline", {}))


def electricity_split_config(config: Mapping[str, Any]) -> dict[str, float]:
    """Return the configured electricity split."""

    return dict(config.get("electricity_split", {}))


def normalized_modelled_electricity_split(config_or_split: Mapping[str, Any]) -> dict[str, float]:
    """Return end-use shares renormalized across modelled end uses only."""

    raw_split = (
        electricity_split_config(config_or_split)
        if "electricity_split" in dict(config_or_split)
        else dict(config_or_split)
    )
    shares = {
        key: max(_float_value(raw_split, key, 0.0), 0.0)
        for key in MODELLED_END_USE_KEYS
    }
    total = sum(shares.values())
    if total <= 0.0:
        equal_share = 1.0 / len(MODELLED_END_USE_KEYS)
        return {key: equal_share for key in MODELLED_END_USE_KEYS}
    return {key: value / total for key, value in shares.items()}


def annual_average_power_w(target_kwh: float) -> float:
    """Convert annual energy to a uniform equivalent average power."""

    return max(float(target_kwh), 0.0) * 1000.0 / 8760.0


def target_electricity_kwh(config: Mapping[str, Any], end_use: str) -> float:
    """Return the modelled annual electricity target for an end use."""

    baseline = baseline_config(config)
    normalized_split = normalized_modelled_electricity_split(config)
    total_kwh = _float_value(baseline, "electricity_annual_kWh", 0.0)
    return total_kwh * float(normalized_split.get(end_use, 0.0))


def representative_thermal_to_electric_factor(config: Mapping[str, Any], end_use: str) -> float:
    """Map thermal demand to representative electricity using literature annual totals."""

    baseline = baseline_config(config)
    electric_target_kwh = target_electricity_kwh(config, end_use=end_use)
    thermal_key = "space_heating_kWh" if end_use == "space_heating" else "dhw_kWh"
    thermal_target_kwh = _float_value(baseline, thermal_key, 0.0)
    if thermal_target_kwh <= 0.0:
        return 0.0
    return electric_target_kwh / thermal_target_kwh
