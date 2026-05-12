"""Resolve technology-case metadata and model input files for scenario leaves."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .validate_scenario_tree import BASELINE_TECHNOLOGY_CASE_ID


class TechnologyResolutionError(ValueError):
    """Raised when a scenario leaf references invalid technology inputs."""


def _technology_case_map(technology_cases: dict[str, Any]) -> dict[str, Any]:
    cases = technology_cases.get("technology_cases", technology_cases)
    if not isinstance(cases, dict):
        raise TechnologyResolutionError("technology_cases metadata must contain a mapping.")
    return cases


def _future_allowed_ids(technology_cases: dict[str, Any]) -> set[str]:
    values = technology_cases.get("future_allowed_technology_case_ids", [])
    if not isinstance(values, list):
        raise TechnologyResolutionError("future_allowed_technology_case_ids must be a list.")
    return {value for value in values if isinstance(value, str)}


def resolve_technology_inputs(
    technology_case_id: str,
    technology_cases: dict[str, Any],
    belgian_technology_inputs_path: Path,
    *,
    window_type: str | None = None,
    allow_missing_technology_inputs: bool = False,
    allow_future_current_stock: bool = False,
) -> dict[str, Any]:
    """Validate a technology case and return references for generated configs."""

    cases = _technology_case_map(technology_cases)
    case = cases.get(technology_case_id)
    if not isinstance(case, dict):
        raise TechnologyResolutionError(f"Undefined technology case: {technology_case_id}")
    if case.get("technology_case_id") != technology_case_id:
        raise TechnologyResolutionError(
            f"technology_cases.{technology_case_id}.technology_case_id must match the mapping key."
        )

    if window_type is not None and window_type not in {"baseline", "future"}:
        raise TechnologyResolutionError(f"window_type must be 'baseline' or 'future', found {window_type!r}.")

    if window_type == "baseline":
        baseline_case_id = technology_cases.get("baseline_technology_case_id", BASELINE_TECHNOLOGY_CASE_ID)
        if technology_case_id != baseline_case_id:
            raise TechnologyResolutionError(
                f"Baseline scenarios must use {baseline_case_id}, found {technology_case_id}."
            )
        if case.get("allowed_for_baseline") is not True:
            raise TechnologyResolutionError(f"{technology_case_id} is not marked allowed_for_baseline.")

    if window_type == "future":
        future_allowed = _future_allowed_ids(technology_cases)
        if technology_case_id == BASELINE_TECHNOLOGY_CASE_ID and not allow_future_current_stock:
            raise TechnologyResolutionError(
                "Future scenarios must not use tech_current_stock unless explicitly permitted by metadata."
            )
        if technology_case_id not in future_allowed and not (
            technology_case_id == BASELINE_TECHNOLOGY_CASE_ID and allow_future_current_stock
        ):
            raise TechnologyResolutionError(
                f"Future scenarios may use only configured future technology cases; found {technology_case_id}."
            )
        applicable_types = case.get("applicable_window_types", [])
        if "future" not in applicable_types:
            raise TechnologyResolutionError(f"{technology_case_id} is not applicable to future climate windows.")

    if not belgian_technology_inputs_path.exists() and not allow_missing_technology_inputs:
        raise TechnologyResolutionError(
            "Missing Belgian technology input YAML: "
            f"{belgian_technology_inputs_path}. Pass --allow-missing-technology-inputs only for explicit "
            "incomplete-config diagnostics."
        )
    if belgian_technology_inputs_path.exists() and belgian_technology_inputs_path.suffix.lower() not in {
        ".yaml",
        ".yml",
    }:
        raise TechnologyResolutionError(
            f"Belgian technology inputs must be a YAML file: {belgian_technology_inputs_path}"
        )

    return {
        "case_id": technology_case_id,
        "case_metadata": dict(case),
        "belgian_technology_inputs": belgian_technology_inputs_path,
        "belgian_technology_inputs_exists": belgian_technology_inputs_path.exists(),
    }
