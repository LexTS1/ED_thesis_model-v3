"""Canonical naming helpers for model_v3 scenario-tree identifiers."""

from __future__ import annotations

import re


DIMENSION_SEPARATOR = "__"
DIMENSION_ID_RE = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")
CLIMATE_PATHWAY_ID_RE = re.compile(r"^(historical|rcp_[0-9]_[0-9])$")
TECHNOLOGY_CASE_ID_RE = re.compile(r"^tech_[a-z0-9]+(?:_[a-z0-9]+)*$")
REALIZATION_ID_RE = re.compile(r"^seed_[0-9]{4}$")


class ScenarioTreeNamingError(ValueError):
    """Raised when a scenario-tree identifier violates the naming contract."""


def _reject_basic_policy_violations(value: str, label: str) -> None:
    if not isinstance(value, str):
        raise ScenarioTreeNamingError(f"{label} must be a string.")
    if value == "":
        raise ScenarioTreeNamingError(f"{label} must not be empty.")
    if value != value.lower():
        raise ScenarioTreeNamingError(f"{label}={value!r} must be lowercase.")
    for forbidden in (" ", "-", ".", "/", "\\"):
        if forbidden in value:
            raise ScenarioTreeNamingError(
                f"{label}={value!r} contains forbidden character {forbidden!r}."
            )


def validate_dimension_id(value: str) -> None:
    """Validate one scenario-tree dimension identifier."""

    _reject_basic_policy_violations(value, "dimension_id")
    if DIMENSION_SEPARATOR in value:
        raise ScenarioTreeNamingError(
            f"dimension_id={value!r} must not contain reserved separator {DIMENSION_SEPARATOR!r}."
        )
    if not (
        DIMENSION_ID_RE.fullmatch(value)
        or CLIMATE_PATHWAY_ID_RE.fullmatch(value)
        or TECHNOLOGY_CASE_ID_RE.fullmatch(value)
        or REALIZATION_ID_RE.fullmatch(value)
    ):
        raise ScenarioTreeNamingError(f"dimension_id={value!r} does not match the identifier grammar.")


def validate_realization_id(value: str) -> None:
    """Validate the Phase 1 realization seed identifier pattern."""

    validate_dimension_id(value)
    if not REALIZATION_ID_RE.fullmatch(value):
        raise ScenarioTreeNamingError(f"realization_id={value!r} must match seed_[0-9]{{4}}.")


def validate_scenario_id(value: str) -> None:
    """Validate a canonical three-dimension scenario ID."""

    _reject_basic_policy_violations(value, "scenario_id")
    parts = value.split(DIMENSION_SEPARATOR)
    if len(parts) != 3:
        raise ScenarioTreeNamingError(
            f"scenario_id={value!r} must contain exactly 3 dimensions separated by {DIMENSION_SEPARATOR!r}."
        )
    climate_window_id, climate_pathway_id, technology_case_id = parts
    for part in parts:
        if part == "":
            raise ScenarioTreeNamingError(f"scenario_id={value!r} contains an empty dimension.")
        validate_dimension_id(part)
    if not CLIMATE_PATHWAY_ID_RE.fullmatch(climate_pathway_id):
        raise ScenarioTreeNamingError(f"climate_pathway_id={climate_pathway_id!r} is invalid.")
    if not TECHNOLOGY_CASE_ID_RE.fullmatch(technology_case_id):
        raise ScenarioTreeNamingError(f"technology_case_id={technology_case_id!r} is invalid.")
    if climate_window_id == "baseline_1981_2005":
        if climate_pathway_id != "historical":
            raise ScenarioTreeNamingError("baseline_1981_2005 scenarios must use historical.")
        if technology_case_id != "tech_current_stock":
            raise ScenarioTreeNamingError("baseline_1981_2005 scenarios must use tech_current_stock.")
    else:
        if climate_pathway_id == "historical":
            raise ScenarioTreeNamingError("future scenarios must use an RCP pathway, not historical.")
        if technology_case_id == "tech_current_stock":
            raise ScenarioTreeNamingError(
                "future scenarios must not use tech_current_stock unless Phase 1 explicitly permits it."
            )


def validate_scenario_leaf_id(value: str) -> None:
    """Validate a canonical four-dimension scenario leaf ID."""

    _reject_basic_policy_violations(value, "scenario_leaf_id")
    parts = value.split(DIMENSION_SEPARATOR)
    if len(parts) != 4:
        raise ScenarioTreeNamingError(
            f"scenario_leaf_id={value!r} must contain exactly 4 dimensions separated by "
            f"{DIMENSION_SEPARATOR!r}."
        )
    scenario_id = DIMENSION_SEPARATOR.join(parts[:3])
    validate_scenario_id(scenario_id)
    validate_realization_id(parts[3])


def make_scenario_id(climate_window_id: str, climate_pathway_id: str, technology_case_id: str) -> str:
    """Build and validate the canonical scenario ID."""

    scenario_id = DIMENSION_SEPARATOR.join((climate_window_id, climate_pathway_id, technology_case_id))
    validate_scenario_id(scenario_id)
    return scenario_id


def make_scenario_leaf_id(scenario_id: str, realization_id: str) -> str:
    """Build and validate the canonical scenario leaf ID."""

    validate_scenario_id(scenario_id)
    validate_realization_id(realization_id)
    scenario_leaf_id = DIMENSION_SEPARATOR.join((scenario_id, realization_id))
    validate_scenario_leaf_id(scenario_leaf_id)
    return scenario_leaf_id


def parse_scenario_id(scenario_id: str) -> dict[str, str]:
    """Parse a scenario ID into named dimensions after validation."""

    validate_scenario_id(scenario_id)
    climate_window_id, climate_pathway_id, technology_case_id = scenario_id.split(DIMENSION_SEPARATOR)
    return {
        "climate_window_id": climate_window_id,
        "climate_pathway_id": climate_pathway_id,
        "technology_case_id": technology_case_id,
    }


def parse_scenario_leaf_id(scenario_leaf_id: str) -> dict[str, str]:
    """Parse a scenario leaf ID into named dimensions after validation."""

    validate_scenario_leaf_id(scenario_leaf_id)
    climate_window_id, climate_pathway_id, technology_case_id, realization_id = scenario_leaf_id.split(
        DIMENSION_SEPARATOR
    )
    scenario_id = DIMENSION_SEPARATOR.join((climate_window_id, climate_pathway_id, technology_case_id))
    return {
        "scenario_leaf_id": scenario_leaf_id,
        "scenario_id": scenario_id,
        "climate_window_id": climate_window_id,
        "climate_pathway_id": climate_pathway_id,
        "technology_case_id": technology_case_id,
        "realization_id": realization_id,
    }


def is_valid_name(value: str) -> bool:
    """Return True if a value is a valid dimension, scenario, or scenario leaf ID."""

    validators = (validate_dimension_id, validate_scenario_id, validate_scenario_leaf_id)
    for validator in validators:
        try:
            validator(value)
        except ScenarioTreeNamingError:
            continue
        return True
    return False
