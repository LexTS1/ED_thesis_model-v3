"""End-use split metrics for representative-baseline checks."""

from __future__ import annotations

from typing import Any, Mapping

import pandas as pd

from model_v3.baseline import MODELLED_END_USE_KEYS, normalized_modelled_electricity_split


def _profile_frame(model_outputs: Mapping[str, Any] | pd.DataFrame) -> pd.DataFrame:
    """Return an annual profile frame from a validation payload."""

    if isinstance(model_outputs, pd.DataFrame):
        return model_outputs.copy()
    if "profile_frame" in model_outputs:
        return pd.DataFrame(model_outputs["profile_frame"]).copy()
    return pd.DataFrame([dict(model_outputs)])


def compute_end_use_split(
    model_outputs: Mapping[str, Any] | pd.DataFrame,
    baseline_split: Mapping[str, float] | None = None,
) -> dict[str, float]:
    """Compute modelled annual end-use shares and their deviation from baseline."""

    frame = _profile_frame(model_outputs)
    component_columns = {
        "appliances": "P_el_appliances_W",
        "lighting": "P_el_lighting_W",
        "cooking": "P_el_cooking_W",
        "dhw": "P_el_dhw_W",
        "space_heating": "P_el_space_heating_W",
    }
    totals: dict[str, float] = {}
    for key, column in component_columns.items():
        if column in frame.columns:
            series = pd.to_numeric(frame[column], errors="coerce").fillna(0.0)
        else:
            series = pd.Series([0.0] * max(len(frame), 1), dtype=float)
        totals[key] = float(series.sum())
    total_energy = sum(totals.values())
    if total_energy <= 0.0:
        shares = {key: 0.0 for key in MODELLED_END_USE_KEYS}
    else:
        shares = {key: value / total_energy for key, value in totals.items()}

    result = {f"{key}_share": float(shares[key]) for key in MODELLED_END_USE_KEYS}
    if baseline_split is not None:
        normalized_baseline = normalized_modelled_electricity_split(baseline_split)
        for key in MODELLED_END_USE_KEYS:
            result[f"{key}_error"] = abs(float(shares[key]) - float(normalized_baseline.get(key, 0.0)))
    return result
