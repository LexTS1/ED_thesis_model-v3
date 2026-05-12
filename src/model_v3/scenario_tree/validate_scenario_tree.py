"""Validate and enumerate the model_v3 scenario-tree metadata contract."""

from __future__ import annotations

import argparse
import csv
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml


REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG_ROOT = REPO_ROOT / "config" / "model_v3" / "scenario_tree"
REQUIRED_RCP_PATHWAYS = ("rcp_2_6", "rcp_4_5", "rcp_8_5")
BASELINE_WINDOW_ID = "baseline_1981_2005"
BASELINE_PATHWAY_ID = "historical"
BASELINE_TECHNOLOGY_CASE_ID = "tech_current_stock"
YEAR_2050_START = date(2050, 1, 1)
YEAR_2050_END = date(2050, 12, 31)


class ScenarioTreeValidationError(ValueError):
    """Raised when scenario-tree metadata is internally inconsistent."""


@dataclass(frozen=True)
class ScenarioLeaf:
    """Expected executable scenario leaf, before any model run exists."""

    scenario_leaf_id: str
    scenario_id: str
    climate_window_id: str
    climate_pathway_id: str
    technology_case_id: str
    realization_id: str
    canonical_start: str
    canonical_end: str
    source_file_window: str


@dataclass(frozen=True)
class ScenarioTreeMetadata:
    """Loaded scenario-tree metadata files."""

    schema: dict[str, Any]
    climate_windows: dict[str, Any]
    technology_cases: dict[str, Any]
    realization_policy: dict[str, Any]


@dataclass(frozen=True)
class ValidationResult:
    """Validated scenario-tree inventory and summary fields."""

    metadata: ScenarioTreeMetadata
    realization_ids: list[str]
    scenario_leaves: list[ScenarioLeaf]
    raw_source_windows_overlap: bool
    canonical_windows_overlap: bool
    year_2050_assignment: str


def load_yaml(path: Path) -> dict[str, Any]:
    """Load one YAML file and require a mapping at the top level."""

    if not path.exists():
        raise ScenarioTreeValidationError(f"Missing required YAML file: {path}")
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict):
        raise ScenarioTreeValidationError(f"YAML file must contain a mapping: {path}")
    return data


def load_scenario_tree(config_root: Path = DEFAULT_CONFIG_ROOT) -> ScenarioTreeMetadata:
    """Load all scenario-tree YAML files from a config root."""

    return ScenarioTreeMetadata(
        schema=load_yaml(config_root / "scenario_tree_schema.yaml"),
        climate_windows=load_yaml(config_root / "climate_windows.yaml"),
        technology_cases=load_yaml(config_root / "technology_cases.yaml"),
        realization_policy=load_yaml(config_root / "realization_policy.yaml"),
    )


def parse_iso_date(value: Any, field_name: str, errors: list[str]) -> date | None:
    """Parse YYYY-MM-DD metadata values as dates."""

    if not isinstance(value, str):
        errors.append(f"{field_name} must be a YYYY-MM-DD string.")
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        errors.append(f"{field_name} must use YYYY-MM-DD format, found {value!r}.")
        return None


def parse_source_file_window(value: Any, field_name: str, errors: list[str]) -> tuple[date, date] | None:
    """Parse source_file_window values such as 2030-2050 as inclusive year ranges."""

    if not isinstance(value, str):
        errors.append(f"{field_name} must be a string formatted as YYYY-YYYY.")
        return None
    match = re.fullmatch(r"([0-9]{4})-([0-9]{4})", value)
    if not match:
        errors.append(f"{field_name} must be formatted as YYYY-YYYY, found {value!r}.")
        return None
    start_year = int(match.group(1))
    end_year = int(match.group(2))
    if start_year > end_year:
        errors.append(f"{field_name} start year must be <= end year, found {value!r}.")
        return None
    return date(start_year, 1, 1), date(end_year, 12, 31)


def require_mapping(data: dict[str, Any], key: str, context: str, errors: list[str]) -> dict[str, Any]:
    """Return a nested mapping or record a validation error."""

    value = data.get(key)
    if not isinstance(value, dict):
        errors.append(f"{context} must define mapping field {key!r}.")
        return {}
    return value


def require_list(data: dict[str, Any], key: str, context: str, errors: list[str]) -> list[Any]:
    """Return a nested list or record a validation error."""

    value = data.get(key)
    if not isinstance(value, list):
        errors.append(f"{context} must define list field {key!r}.")
        return []
    return value


def validate_id(
    value: Any,
    regex: str,
    field_name: str,
    errors: list[str],
    *,
    allow_reserved_separator: bool = False,
) -> None:
    """Validate an identifier against a regex and the double-underscore policy."""

    if not isinstance(value, str):
        errors.append(f"{field_name} must be a string.")
        return
    if not allow_reserved_separator and "__" in value:
        errors.append(f"{field_name}={value!r} must not contain reserved separator '__'.")
    if not re.fullmatch(regex, value):
        errors.append(f"{field_name}={value!r} does not match expected pattern {regex!r}.")


def check_unique(values: list[str], label: str, errors: list[str]) -> None:
    """Record duplicate values with a useful label."""

    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    if duplicates:
        errors.append(f"Duplicate {label}: {', '.join(sorted(duplicates))}.")


def intervals_overlap(first: tuple[date, date], second: tuple[date, date]) -> bool:
    """Return True when inclusive date intervals overlap."""

    return first[0] <= second[1] and second[0] <= first[1]


def overlapping_years(intervals: list[tuple[str, tuple[date, date]]]) -> set[int]:
    """Return calendar years that appear in more than one inclusive interval."""

    years_by_window = {
        name: set(range(bounds[0].year, bounds[1].year + 1))
        for name, bounds in intervals
    }
    names = list(years_by_window)
    overlaps: set[int] = set()
    for index, first_name in enumerate(names):
        for second_name in names[index + 1 :]:
            overlaps.update(years_by_window[first_name].intersection(years_by_window[second_name]))
    return overlaps


def validate_schema(schema: dict[str, Any], errors: list[str]) -> dict[str, str]:
    """Validate schema-contract fields and return regex conventions."""

    context = "scenario_tree_schema.yaml"
    for key in (
        "schema_version",
        "identifier_conventions",
        "definitions",
        "baseline_rules",
        "future_window_rules",
        "enumeration_rules",
        "validation_rules",
    ):
        if key not in schema:
            errors.append(f"{context} missing required top-level field {key!r}.")

    conventions = require_mapping(schema, "identifier_conventions", context, errors)
    if conventions.get("separator") != "__":
        errors.append("identifier_conventions.separator must be '__'.")
    expected_dimensions = [
        "climate_window_id",
        "climate_pathway_id",
        "technology_case_id",
        "realization_id",
    ]
    dimensions = conventions.get("allowed_dimension_names")
    if dimensions != expected_dimensions:
        errors.append(
            "identifier_conventions.allowed_dimension_names must be "
            f"{expected_dimensions}, found {dimensions!r}."
        )

    grammar = require_mapping(conventions, "id_grammar", "identifier_conventions", errors)
    regex_keys = {
        "climate_window": "climate_window_id_regex",
        "climate_pathway": "climate_pathway_id_regex",
        "technology_case": "technology_case_id_regex",
        "realization": "realization_id_regex",
        "scenario": "scenario_id_regex",
        "scenario_leaf": "scenario_leaf_id_regex",
    }
    regexes: dict[str, str] = {}
    for label, key in regex_keys.items():
        value = grammar.get(key)
        if not isinstance(value, str):
            errors.append(f"identifier_conventions.id_grammar.{key} must be a regex string.")
            continue
        try:
            re.compile(value)
        except re.error as exc:
            errors.append(f"Invalid regex {key}: {exc}.")
            continue
        regexes[label] = value
    return regexes


def validate_climate_windows(
    climate_config: dict[str, Any],
    regexes: dict[str, str],
    errors: list[str],
) -> tuple[dict[str, dict[str, Any]], bool, bool]:
    """Validate climate-window metadata and temporal policy."""

    context = "climate_windows.yaml"
    for key in ("schema_version", "date_semantics", "temporal_window_policy", "climate_windows"):
        if key not in climate_config:
            errors.append(f"{context} missing required top-level field {key!r}.")

    date_semantics = require_mapping(climate_config, "date_semantics", context, errors)
    if date_semantics.get("canonical_start") != "inclusive":
        errors.append("date_semantics.canonical_start must be 'inclusive'.")
    if date_semantics.get("canonical_end") != "inclusive":
        errors.append("date_semantics.canonical_end must be 'inclusive'.")

    temporal_policy = require_mapping(climate_config, "temporal_window_policy", context, errors)
    if temporal_policy.get("raw_processed_files_may_overlap") is not True:
        errors.append("temporal_window_policy.raw_processed_files_may_overlap must be true.")
    if temporal_policy.get("canonical_analysis_windows_must_overlap") is not False:
        errors.append("temporal_window_policy.canonical_analysis_windows_must_overlap must be false.")
    if temporal_policy.get("overlapping_years") != [2050]:
        errors.append("temporal_window_policy.overlapping_years must be [2050].")
    if temporal_policy.get("year_2050_assignment") != "mid_century_2050_2070":
        errors.append("temporal_window_policy.year_2050_assignment must be mid_century_2050_2070.")
    if temporal_policy.get("near_future_excludes_2050") is not True:
        errors.append("temporal_window_policy.near_future_excludes_2050 must be true.")

    windows = require_mapping(climate_config, "climate_windows", context, errors)
    expected_required_windows = {
        "baseline_1981_2005",
        "near_future_2030_2049",
        "mid_century_2050_2070",
        "long_term_2080_2100",
    }
    missing_windows = sorted(expected_required_windows.difference(windows))
    if missing_windows:
        errors.append(f"Missing required climate window(s): {', '.join(missing_windows)}.")

    window_ids: list[str] = []
    canonical_intervals: list[tuple[str, tuple[date, date]]] = []
    source_intervals: list[tuple[str, tuple[date, date]]] = []
    normalized_windows: dict[str, dict[str, Any]] = {}

    required_window_fields = (
        "climate_window_id",
        "canonical_start",
        "canonical_end",
        "source_file_window",
        "window_type",
        "allowed_pathways",
    )
    for key, window in windows.items():
        if not isinstance(window, dict):
            errors.append(f"climate_windows.{key} must be a mapping.")
            continue
        for field_name in required_window_fields:
            if field_name not in window:
                errors.append(f"climate_windows.{key} missing required field {field_name!r}.")

        window_id = window.get("climate_window_id")
        if window_id != key:
            errors.append(f"climate_windows.{key}.climate_window_id must match mapping key.")
        validate_id(window_id, regexes.get("climate_window", r"a^"), f"climate_window_id for {key}", errors)
        if isinstance(window_id, str):
            window_ids.append(window_id)

        canonical_start = parse_iso_date(window.get("canonical_start"), f"{key}.canonical_start", errors)
        canonical_end = parse_iso_date(window.get("canonical_end"), f"{key}.canonical_end", errors)
        if canonical_start and canonical_end:
            if canonical_start > canonical_end:
                errors.append(f"{key} canonical_start must be <= canonical_end.")
            else:
                canonical_intervals.append((key, (canonical_start, canonical_end)))

        source_interval = parse_source_file_window(
            window.get("source_file_window"),
            f"{key}.source_file_window",
            errors,
        )
        if source_interval:
            source_intervals.append((key, source_interval))

        window_type = window.get("window_type")
        if window_type not in {"baseline", "future"}:
            errors.append(f"{key}.window_type must be 'baseline' or 'future'.")

        allowed_pathways = window.get("allowed_pathways")
        if not isinstance(allowed_pathways, list) or not all(isinstance(item, str) for item in allowed_pathways):
            errors.append(f"{key}.allowed_pathways must be a list of strings.")
            allowed_pathways = []
        for pathway in allowed_pathways:
            validate_id(pathway, regexes.get("climate_pathway", r"a^"), f"{key}.allowed_pathways item", errors)

        if window_type == "baseline":
            if allowed_pathways != [BASELINE_PATHWAY_ID]:
                errors.append(f"{key} baseline window must allow only historical.")
        if window_type == "future":
            missing_pathways = sorted(set(REQUIRED_RCP_PATHWAYS).difference(allowed_pathways))
            if missing_pathways:
                errors.append(f"{key} future window missing RCP pathway(s): {', '.join(missing_pathways)}.")
            if BASELINE_PATHWAY_ID in allowed_pathways:
                errors.append(f"{key} future window must not allow historical pathway.")

        normalized = dict(window)
        normalized_windows[key] = normalized

    check_unique(window_ids, "climate_window_id values", errors)

    canonical_windows_overlap = False
    for index, (first_name, first_interval) in enumerate(canonical_intervals):
        for second_name, second_interval in canonical_intervals[index + 1 :]:
            if intervals_overlap(first_interval, second_interval):
                canonical_windows_overlap = True
                errors.append(
                    "Canonical analysis windows must not overlap, but "
                    f"{first_name} overlaps {second_name}."
                )

    raw_source_windows_overlap = False
    for index, (first_name, first_interval) in enumerate(source_intervals):
        for second_name, second_interval in source_intervals[index + 1 :]:
            if intervals_overlap(first_interval, second_interval):
                raw_source_windows_overlap = True
                if temporal_policy.get("raw_processed_files_may_overlap") is not True:
                    errors.append(
                        "Raw/source windows overlap but temporal policy does not permit it: "
                        f"{first_name} overlaps {second_name}."
                    )

    source_overlap_years = overlapping_years(source_intervals)
    declared_overlap_years = set(temporal_policy.get("overlapping_years", []))
    undeclared_overlap_years = source_overlap_years.difference(declared_overlap_years)
    if undeclared_overlap_years:
        errors.append(
            "Raw/source windows overlap in undeclared year(s): "
            f"{', '.join(str(year) for year in sorted(undeclared_overlap_years))}."
        )

    near_future = normalized_windows.get("near_future_2030_2049")
    mid_century = normalized_windows.get("mid_century_2050_2070")
    if near_future:
        near_start = parse_iso_date(near_future.get("canonical_start"), "near_future.canonical_start", errors)
        near_end = parse_iso_date(near_future.get("canonical_end"), "near_future.canonical_end", errors)
        if near_start and near_end and intervals_overlap((near_start, near_end), (YEAR_2050_START, YEAR_2050_END)):
            errors.append("near_future_2030_2049 canonical analysis window must exclude all of 2050.")
    if mid_century:
        mid_start = parse_iso_date(mid_century.get("canonical_start"), "mid_century.canonical_start", errors)
        mid_end = parse_iso_date(mid_century.get("canonical_end"), "mid_century.canonical_end", errors)
        if mid_start and mid_end:
            includes_start = mid_start <= YEAR_2050_START <= mid_end
            includes_end = mid_start <= YEAR_2050_END <= mid_end
            if not (includes_start and includes_end):
                errors.append("mid_century_2050_2070 canonical analysis window must include all of 2050.")

    return normalized_windows, raw_source_windows_overlap, canonical_windows_overlap


def validate_technology_cases(
    technology_config: dict[str, Any],
    regexes: dict[str, str],
    errors: list[str],
) -> dict[str, dict[str, Any]]:
    """Validate technology-case metadata."""

    context = "technology_cases.yaml"
    for key in (
        "schema_version",
        "baseline_technology_case_id",
        "future_allowed_technology_case_ids",
        "technology_cases",
    ):
        if key not in technology_config:
            errors.append(f"{context} missing required top-level field {key!r}.")

    if technology_config.get("baseline_technology_case_id") != BASELINE_TECHNOLOGY_CASE_ID:
        errors.append("baseline_technology_case_id must be tech_current_stock.")
    future_allowed = require_list(technology_config, "future_allowed_technology_case_ids", context, errors)
    missing_future_tech = {
        "tech_frozen_stock",
        "tech_moderate_electrification",
        "tech_high_electrification_pv_ev",
    }.difference(future_allowed)
    if missing_future_tech:
        errors.append(f"future_allowed_technology_case_ids missing: {', '.join(sorted(missing_future_tech))}.")
    if BASELINE_TECHNOLOGY_CASE_ID in future_allowed:
        errors.append("future_allowed_technology_case_ids must not include tech_current_stock.")

    cases = require_mapping(technology_config, "technology_cases", context, errors)
    required_cases = {
        "tech_current_stock",
        "tech_frozen_stock",
        "tech_moderate_electrification",
        "tech_high_electrification_pv_ev",
    }
    missing_cases = sorted(required_cases.difference(cases))
    if missing_cases:
        errors.append(f"Missing required technology case(s): {', '.join(missing_cases)}.")

    case_ids: list[str] = []
    required_fields = (
        "technology_case_id",
        "label",
        "description",
        "applicable_window_types",
        "modelling_interpretation",
        "heat_pump_adoption_assumed",
        "pv_assumed",
        "ev_adoption_assumed",
        "building_envelope_improvement_assumed",
        "allowed_for_baseline",
    )
    normalized_cases: dict[str, dict[str, Any]] = {}
    for key, case in cases.items():
        if not isinstance(case, dict):
            errors.append(f"technology_cases.{key} must be a mapping.")
            continue
        for field_name in required_fields:
            if field_name not in case:
                errors.append(f"technology_cases.{key} missing required field {field_name!r}.")

        case_id = case.get("technology_case_id")
        if case_id != key:
            errors.append(f"technology_cases.{key}.technology_case_id must match mapping key.")
        validate_id(case_id, regexes.get("technology_case", r"a^"), f"technology_case_id for {key}", errors)
        if isinstance(case_id, str):
            case_ids.append(case_id)

        applicable_types = case.get("applicable_window_types")
        if not isinstance(applicable_types, list) or not all(item in {"baseline", "future"} for item in applicable_types):
            errors.append(f"{key}.applicable_window_types must list baseline and/or future.")
            applicable_types = []

        for bool_field in (
            "heat_pump_adoption_assumed",
            "pv_assumed",
            "ev_adoption_assumed",
            "building_envelope_improvement_assumed",
            "allowed_for_baseline",
        ):
            if not isinstance(case.get(bool_field), bool):
                errors.append(f"{key}.{bool_field} must be boolean.")

        if key == BASELINE_TECHNOLOGY_CASE_ID:
            if case.get("allowed_for_baseline") is not True:
                errors.append("tech_current_stock.allowed_for_baseline must be true.")
            if applicable_types != ["baseline"]:
                errors.append("tech_current_stock.applicable_window_types must be [baseline].")
            if case.get("explicitly_permitted_for_future") is True:
                errors.append("tech_current_stock must not be explicitly permitted for future windows.")
        else:
            if case.get("allowed_for_baseline") is True:
                errors.append(f"{key}.allowed_for_baseline must be false.")
            if "future" not in applicable_types:
                errors.append(f"{key}.applicable_window_types must include future.")

        normalized_cases[key] = dict(case)

    check_unique(case_ids, "technology_case_id values", errors)
    for tech_id in future_allowed:
        if isinstance(tech_id, str) and tech_id not in normalized_cases:
            errors.append(f"future_allowed_technology_case_ids references unknown case {tech_id!r}.")
    return normalized_cases


def validate_realization_policy(
    realization_config: dict[str, Any],
    regexes: dict[str, str],
    errors: list[str],
) -> list[str]:
    """Validate realization policy metadata and generate realization IDs."""

    context = "realization_policy.yaml"
    for key in ("schema_version", "realization_policy"):
        if key not in realization_config:
            errors.append(f"{context} missing required top-level field {key!r}.")
    policy = require_mapping(realization_config, "realization_policy", context, errors)

    required_fields = (
        "realization_id_template",
        "seed_padding_length",
        "seed_start_index",
        "seed_stop_index",
        "number_of_seeds",
        "deterministic_reproducibility_rule",
        "cohort_mapping_description",
        "cohorts_generated_in_this_phase",
    )
    for field_name in required_fields:
        if field_name not in policy:
            errors.append(f"realization_policy missing required field {field_name!r}.")

    if policy.get("realization_id_template") != "seed_{seed_index:04d}":
        errors.append("realization_id_template must be 'seed_{seed_index:04d}'.")
    if policy.get("seed_padding_length") != 4:
        errors.append("seed_padding_length must be 4.")
    if policy.get("cohorts_generated_in_this_phase") is not False:
        errors.append("cohorts_generated_in_this_phase must be false.")

    start = policy.get("seed_start_index")
    stop = policy.get("seed_stop_index")
    number_of_seeds = policy.get("number_of_seeds")
    if not isinstance(start, int) or start < 0:
        errors.append("seed_start_index must be a non-negative integer.")
        start = 0
    if not isinstance(stop, int) or stop < start:
        errors.append("seed_stop_index must be an integer >= seed_start_index.")
        stop = start - 1
    expected_count = stop - start + 1
    if number_of_seeds != expected_count:
        errors.append(
            "number_of_seeds must equal inclusive seed range length "
            f"({expected_count}), found {number_of_seeds!r}."
        )

    realization_ids = [f"seed_{seed_index:04d}" for seed_index in range(start, stop + 1)]
    for realization_id in realization_ids:
        validate_id(realization_id, regexes.get("realization", r"a^"), "generated realization_id", errors)
    check_unique(realization_ids, "generated realization IDs", errors)
    return realization_ids


def enumerate_scenario_leaves(
    windows: dict[str, dict[str, Any]],
    technology_cases: dict[str, dict[str, Any]],
    realization_ids: list[str],
    schema: dict[str, Any],
    errors: list[str],
) -> list[ScenarioLeaf]:
    """Enumerate expected scenario leaves without running the model."""

    future_tech_ids = schema.get("future_window_rules", {}).get("default_allowed_technology_case_ids", [])
    if not isinstance(future_tech_ids, list):
        errors.append("future_window_rules.default_allowed_technology_case_ids must be a list.")
        future_tech_ids = []

    leaves: list[ScenarioLeaf] = []
    for window_id, window in windows.items():
        window_type = window.get("window_type")
        if window_type == "baseline":
            pathway_ids = [BASELINE_PATHWAY_ID]
            technology_ids = [BASELINE_TECHNOLOGY_CASE_ID]
            if window_id != BASELINE_WINDOW_ID:
                errors.append(f"Unexpected baseline window {window_id!r}; expected {BASELINE_WINDOW_ID}.")
        elif window_type == "future":
            pathway_ids = list(REQUIRED_RCP_PATHWAYS)
            technology_ids = list(future_tech_ids)
        else:
            continue

        allowed_pathways = set(window.get("allowed_pathways", []))
        for pathway_id in pathway_ids:
            if pathway_id not in allowed_pathways:
                errors.append(f"{window_id} does not allow enumerated pathway {pathway_id}.")

        for pathway_id in pathway_ids:
            for technology_id in technology_ids:
                technology = technology_cases.get(technology_id)
                if technology is None:
                    errors.append(f"Unknown technology case in enumeration: {technology_id}.")
                    continue
                if window_type == "baseline" and technology_id != BASELINE_TECHNOLOGY_CASE_ID:
                    errors.append("Baseline enumeration must use only tech_current_stock.")
                if window_type == "future":
                    if (
                        technology_id == BASELINE_TECHNOLOGY_CASE_ID
                        and technology.get("explicitly_permitted_for_future") is not True
                    ):
                        errors.append("Future enumeration must not use tech_current_stock unless explicitly permitted.")
                    if "future" not in technology.get("applicable_window_types", []):
                        errors.append(
                            f"Future enumeration uses {technology_id}, but it is not applicable to future windows."
                        )

                for realization_id in realization_ids:
                    scenario_id = f"{window_id}__{pathway_id}__{technology_id}"
                    scenario_leaf_id = f"{scenario_id}__{realization_id}"
                    leaves.append(
                        ScenarioLeaf(
                            scenario_leaf_id=scenario_leaf_id,
                            scenario_id=scenario_id,
                            climate_window_id=window_id,
                            climate_pathway_id=pathway_id,
                            technology_case_id=technology_id,
                            realization_id=realization_id,
                            canonical_start=str(window["canonical_start"]),
                            canonical_end=str(window["canonical_end"]),
                            source_file_window=str(window["source_file_window"]),
                        )
                    )
    return leaves


def validate_generated_leaves(
    leaves: list[ScenarioLeaf],
    regexes: dict[str, str],
    errors: list[str],
) -> None:
    """Validate generated scenario and scenario-leaf identifiers."""

    scenario_ids = [leaf.scenario_id for leaf in leaves]
    leaf_ids = [leaf.scenario_leaf_id for leaf in leaves]
    for leaf in leaves:
        validate_id(
            leaf.scenario_id,
            regexes.get("scenario", r"a^"),
            "generated scenario_id",
            errors,
            allow_reserved_separator=True,
        )
        validate_id(
            leaf.scenario_leaf_id,
            regexes.get("scenario_leaf", r"a^"),
            "generated scenario_leaf_id",
            errors,
            allow_reserved_separator=True,
        )
    check_unique(leaf_ids, "scenario_leaf_id values", errors)

    unique_scenarios = set(scenario_ids)
    expected_leaf_count = len(unique_scenarios) * len({leaf.realization_id for leaf in leaves})
    if len(leaves) != expected_leaf_count:
        errors.append(
            "Scenario leaf count does not match scenarios times realizations: "
            f"{len(leaves)} != {len(unique_scenarios)} * "
            f"{len({leaf.realization_id for leaf in leaves})}."
        )


def validate_scenario_tree(config_root: Path = DEFAULT_CONFIG_ROOT) -> ValidationResult:
    """Load, validate, and enumerate the model_v3 scenario tree."""

    errors: list[str] = []
    metadata = load_scenario_tree(config_root)
    regexes = validate_schema(metadata.schema, errors)
    windows, raw_overlap, canonical_overlap = validate_climate_windows(
        metadata.climate_windows,
        regexes,
        errors,
    )
    technology_cases = validate_technology_cases(metadata.technology_cases, regexes, errors)
    realization_ids = validate_realization_policy(metadata.realization_policy, regexes, errors)
    leaves = enumerate_scenario_leaves(
        windows=windows,
        technology_cases=technology_cases,
        realization_ids=realization_ids,
        schema=metadata.schema,
        errors=errors,
    )
    validate_generated_leaves(leaves, regexes, errors)

    if errors:
        message = "Scenario-tree validation failed:\n" + "\n".join(f" - {error}" for error in errors)
        raise ScenarioTreeValidationError(message)

    return ValidationResult(
        metadata=metadata,
        realization_ids=realization_ids,
        scenario_leaves=leaves,
        raw_source_windows_overlap=raw_overlap,
        canonical_windows_overlap=canonical_overlap,
        year_2050_assignment=metadata.climate_windows["temporal_window_policy"]["year_2050_assignment"],
    )


def write_inventory(leaves: list[ScenarioLeaf], output_path: Path) -> None:
    """Write a scenario-leaf inventory CSV."""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "scenario_leaf_id",
        "scenario_id",
        "climate_window_id",
        "climate_pathway_id",
        "technology_case_id",
        "realization_id",
        "canonical_start",
        "canonical_end",
        "source_file_window",
    ]
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for leaf in leaves:
            writer.writerow({field_name: getattr(leaf, field_name) for field_name in fieldnames})


def print_summary(result: ValidationResult) -> None:
    """Print a compact success summary."""

    windows = result.metadata.climate_windows["climate_windows"]
    technology_cases = result.metadata.technology_cases["technology_cases"]
    print("Scenario-tree validation passed.")
    print(f"Climate windows: {len(windows)}")
    print(f"Technology cases: {len(technology_cases)}")
    print(f"Realizations: {len(result.realization_ids)}")
    print(f"Scenario leaves: {len(result.scenario_leaves)}")
    print(f"Canonical windows overlap: {'yes' if result.canonical_windows_overlap else 'no'}")
    print(f"Raw/source window overlap allowed: {'yes' if result.raw_source_windows_overlap else 'no'}")
    print(f"2050 canonical assignment: {result.year_2050_assignment}")


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config-root",
        type=Path,
        default=DEFAULT_CONFIG_ROOT,
        help="Directory containing scenario-tree YAML files.",
    )
    parser.add_argument(
        "--write-inventory",
        type=Path,
        default=None,
        help="Optional CSV path for the enumerated scenario-leaf inventory.",
    )
    parser.add_argument(
        "--print-summary",
        action="store_true",
        help="Print a validation summary after successful validation.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""

    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result = validate_scenario_tree(config_root=args.config_root)
        if args.write_inventory is not None:
            write_inventory(result.scenario_leaves, args.write_inventory)
        if args.print_summary:
            print_summary(result)
    except ScenarioTreeValidationError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
