"""Shared model execution helpers for validation runners."""

from __future__ import annotations

import logging
from typing import Any, Mapping

import pandas as pd

from model_v3.cohort.cohort_engine import run_cohort_simulation
from model_v3.simulation.annual_runner import run_annual_simulation

LOGGER = logging.getLogger(__name__)


def _model_value_column(validation_cfg: Mapping[str, Any]) -> str | None:
    raw = validation_cfg.get("model_value_column")
    for nested_key in ("fluvius", "kuleuven"):
        if raw not in {None, ""}:
            break
        raw = dict(validation_cfg.get(nested_key, {})).get("model_value_column")
    if raw in {None, ""}:
        return None
    return str(raw)


def _build_model_frame_from_annual(results: Mapping[str, Any], *, value_column: str | None = None) -> pd.DataFrame:
    """Project a deterministic annual simulation result onto a validation-ready frame."""

    frame = pd.DataFrame(results["profile_frame"]).copy()
    selected_column = value_column if value_column in frame.columns else "P_el_total_W"
    frame["value"] = pd.to_numeric(frame[selected_column], errors="coerce").fillna(0.0)
    frame["P10_W"] = frame["value"]
    frame["P50_W"] = frame["value"]
    frame["P90_W"] = frame["value"]
    return frame[["timestamp", "value", "P10_W", "P50_W", "P90_W"]]


def _build_model_frame_from_cohort(results: Mapping[str, Any], *, value_column: str | None = None) -> pd.DataFrame:
    """Project a stochastic cohort result onto a validation-ready per-household frame."""

    frame = pd.DataFrame(results["profile_frame"]).copy()
    selected_column = value_column if value_column in frame.columns else "per_household_profile_W"
    value = pd.to_numeric(frame[selected_column], errors="coerce").fillna(0.0)
    if selected_column != "per_household_profile_W" and not selected_column.startswith("per_household"):
            value = value / max(int(results.get("household_count", results.get("n_households", 1)) or 1), 1)
    frame["value"] = value
    frame["P10_W"] = pd.to_numeric(frame["P10_W"], errors="coerce").fillna(frame["value"]) if "P10_W" in frame else frame["value"]
    frame["P50_W"] = pd.to_numeric(frame["P50_W"], errors="coerce").fillna(frame["value"]) if "P50_W" in frame else frame["value"]
    frame["P90_W"] = pd.to_numeric(frame["P90_W"], errors="coerce").fillna(frame["value"]) if "P90_W" in frame else frame["value"]
    return frame[["timestamp", "value", "P10_W", "P50_W", "P90_W"]]


def run_validation_model(config: Mapping[str, Any], validation_cfg: Mapping[str, Any]) -> tuple[dict[str, Any], pd.DataFrame]:
    """Run the configured model source for validation."""

    model_source = str(validation_cfg.get("model_source", "cohort")).strip().lower()
    value_column = _model_value_column(validation_cfg)
    LOGGER.info("validation_model.start model_source=%s", model_source)
    if model_source == "annual":
        model_results = run_annual_simulation(config=config)
        LOGGER.info(
            "validation_model.complete model_source=%s n_steps=%s household_count=%s",
            model_source,
            int(model_results.get("n_steps", 0)),
            int(model_results.get("household_count", model_results.get("n_households", 1))),
        )
        return model_results, _build_model_frame_from_annual(results=model_results, value_column=value_column)
    if model_source == "cohort":
        model_results = run_cohort_simulation(config=config)
        LOGGER.info(
            "validation_model.complete model_source=%s n_steps=%s household_count=%s diversity_factor=%.3f",
            model_source,
            int(model_results.get("n_steps", 0)),
            int(model_results.get("household_count", model_results.get("n_households", 1))),
            float(model_results.get("diversity_factor", 0.0)),
        )
        return model_results, _build_model_frame_from_cohort(results=model_results, value_column=value_column)
    raise ValueError(f"Unsupported validation model_source: {model_source}")
