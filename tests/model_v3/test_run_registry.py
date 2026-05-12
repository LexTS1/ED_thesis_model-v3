"""Tests for scenario-tree run registry semantics."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
MODEL_V3_SRC = REPO_ROOT / "src"
if str(MODEL_V3_SRC) not in sys.path:
    sys.path.insert(0, str(MODEL_V3_SRC))

from model_v3.scenarios.registry import (  # noqa: E402
    RunRegistryError,
    append_registry_row,
    is_successful,
    latest_actual_status,
    read_registry,
    status_counts,
    upsert_registry_row,
)


LEAF_ID = "baseline_1981_2005__historical__tech_current_stock__seed_0000"


def _row(attempt: str, status: str) -> dict[str, object]:
    return {
        "run_attempt_id": attempt,
        "scenario_leaf_id": LEAF_ID,
        "scenario_id": "baseline_1981_2005__historical__tech_current_stock",
        "climate_window_id": "baseline_1981_2005",
        "climate_pathway_id": "historical",
        "technology_case_id": "tech_current_stock",
        "realization_id": "seed_0000",
        "timestamp_start_utc": f"2026-05-09T00:00:0{attempt[-1]}Z",
        "timestamp_end_utc": f"2026-05-09T00:00:1{attempt[-1]}Z",
        "duration_seconds": "1.000",
        "status": status,
    }


def test_registry_appends_attempts_instead_of_overwriting(tmp_path: Path) -> None:
    registry_file = tmp_path / "run_registry.csv"
    append_registry_row(registry_file, _row("attempt_1", "failed"))
    append_registry_row(registry_file, _row("attempt_2", "success"))

    rows = read_registry(registry_file)

    assert len(rows) == 2
    assert [row["status"] for row in rows] == ["failed", "success"]
    assert is_successful(rows, LEAF_ID)


def test_upsert_updates_one_running_attempt_without_erasing_other_attempts(tmp_path: Path) -> None:
    registry_file = tmp_path / "run_registry.csv"
    append_registry_row(registry_file, _row("attempt_1", "failed"))
    running = _row("attempt_2", "running")
    upsert_registry_row(registry_file, running)
    finished = dict(running)
    finished["status"] = "success"
    upsert_registry_row(registry_file, finished)

    rows = read_registry(registry_file)

    assert len(rows) == 2
    assert rows[-1]["status"] == "success"


def test_failed_latest_run_remains_eligible_for_rerun(tmp_path: Path) -> None:
    registry_file = tmp_path / "run_registry.csv"
    append_registry_row(registry_file, _row("attempt_1", "success"))
    append_registry_row(registry_file, _row("attempt_2", "failed"))

    rows = read_registry(registry_file)

    assert latest_actual_status(rows, LEAF_ID) == "failed"
    assert not is_successful(rows, LEAF_ID)


def test_status_values_are_restricted(tmp_path: Path) -> None:
    registry_file = tmp_path / "run_registry.csv"
    bad = _row("attempt_1", "done")

    with pytest.raises(RunRegistryError):
        append_registry_row(registry_file, bad)


def test_status_counts_include_allowed_statuses(tmp_path: Path) -> None:
    registry_file = tmp_path / "run_registry.csv"
    append_registry_row(registry_file, _row("attempt_1", "failed"))
    append_registry_row(registry_file, _row("attempt_2", "success"))
    append_registry_row(registry_file, _row("attempt_3", "skipped"))

    counts = status_counts(read_registry(registry_file))

    assert counts["failed"] == 1
    assert counts["success"] == 1
    assert counts["skipped"] == 1
    assert counts["planned"] == 0

