"""Scenario leaf selection helpers for the scenario-tree runner."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from model_v3.scenario_tree.naming import parse_scenario_leaf_id, validate_scenario_leaf_id


class ScenarioSelectionError(ValueError):
    """Raised when requested scenario leaves cannot be selected."""


@dataclass(frozen=True)
class ScenarioLeafRecord:
    """One row from the Phase 2 scenario leaf index."""

    scenario_leaf_id: str
    scenario_id: str
    climate_window_id: str
    climate_pathway_id: str
    technology_case_id: str
    realization_id: str
    row: dict[str, str]
    design_year_id: str = ""


def load_leaf_records(leaf_index_path: Path) -> list[ScenarioLeafRecord]:
    """Load and validate the scenario leaf index."""

    path = Path(leaf_index_path)
    if not path.exists():
        raise ScenarioSelectionError(f"Missing scenario leaf index: {path}")
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ScenarioSelectionError(f"Scenario leaf index is empty: {path}")

    records: list[ScenarioLeafRecord] = []
    seen: set[str] = set()
    for row_number, row in enumerate(rows, start=2):
        leaf_id = row.get("scenario_leaf_id", "")
        try:
            parsed = parse_scenario_leaf_id(leaf_id)
        except ValueError as exc:
            raise ScenarioSelectionError(f"Invalid scenario_leaf_id in row {row_number}: {exc}") from exc
        if leaf_id in seen:
            raise ScenarioSelectionError(f"Duplicate scenario_leaf_id in leaf index: {leaf_id}")
        seen.add(leaf_id)
        for field_name, expected_value in parsed.items():
            if row.get(field_name) != expected_value:
                raise ScenarioSelectionError(
                    f"Row {row_number} {field_name}={row.get(field_name)!r} does not match "
                    f"scenario_leaf_id value {expected_value!r}."
                )
        records.append(ScenarioLeafRecord(row=dict(row), **parsed))
    return sorted(records, key=lambda item: item.scenario_leaf_id)


def select_leaf_records(
    records: list[ScenarioLeafRecord],
    *,
    scenario_leaf_id: str | None = None,
    all_leaves: bool = False,
    climate_window_id: str | None = None,
    climate_pathway_id: str | None = None,
    technology_case_id: str | None = None,
    realization_id: str | None = None,
    limit: int | None = None,
    default_all: bool = False,
) -> list[ScenarioLeafRecord]:
    """Select leaves deterministically from the index."""

    if scenario_leaf_id:
        try:
            validate_scenario_leaf_id(scenario_leaf_id)
        except ValueError as exc:
            raise ScenarioSelectionError(f"Invalid scenario leaf ID: {exc}") from exc
        selected = [record for record in records if record.scenario_leaf_id == scenario_leaf_id]
        if not selected:
            raise ScenarioSelectionError(f"Scenario leaf ID not found in index: {scenario_leaf_id}")
    elif all_leaves or default_all:
        selected = list(records)
    else:
        raise ScenarioSelectionError("Pass --scenario-leaf-id for one leaf or --all for batch execution.")

    filters = {
        "climate_window_id": climate_window_id,
        "climate_pathway_id": climate_pathway_id,
        "technology_case_id": technology_case_id,
        "realization_id": realization_id,
    }
    for field_name, expected_value in filters.items():
        if expected_value is not None:
            selected = [record for record in selected if getattr(record, field_name) == expected_value]

    selected = sorted(selected, key=lambda item: item.scenario_leaf_id)
    if limit is not None:
        if limit < 0:
            raise ScenarioSelectionError("--limit must be non-negative.")
        selected = selected[:limit]
    return selected
