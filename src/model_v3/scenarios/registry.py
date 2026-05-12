"""Persistent run registry for scenario-tree execution attempts."""

from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml


ALLOWED_STATUSES = {"planned", "running", "success", "failed", "skipped"}
ACTUAL_RUN_STATUSES = {"running", "success", "failed"}
REGISTRY_FIELDS = [
    "run_attempt_id",
    "scenario_leaf_id",
    "scenario_id",
    "climate_window_id",
    "climate_pathway_id",
    "technology_case_id",
    "realization_id",
    "timestamp_start_utc",
    "timestamp_end_utc",
    "duration_seconds",
    "status",
    "skip_reason",
    "git_commit",
    "git_is_dirty",
    "config_path",
    "config_hash_sha256",
    "inputs_manifest_path",
    "inputs_manifest_hash_sha256",
    "climate_forcing_file",
    "climate_forcing_hash_sha256",
    "belgian_technology_inputs",
    "belgian_technology_inputs_hash_sha256",
    "random_seed",
    "cohort_size",
    "model_version",
    "output_path",
    "log_path",
    "error_type",
    "error_message",
]


class RunRegistryError(ValueError):
    """Raised when a registry row is malformed."""


def registry_path(experiment_root: Path) -> Path:
    return Path(experiment_root) / "manifests" / "run_registry.csv"


def summary_path(experiment_root: Path) -> Path:
    return Path(experiment_root) / "manifests" / "run_registry_summary.yaml"


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def normalize_row(row: Mapping[str, Any]) -> dict[str, str]:
    """Return a registry row containing exactly the canonical fields."""

    status = _stringify(row.get("status"))
    if status and status not in ALLOWED_STATUSES:
        raise RunRegistryError(f"Invalid run registry status: {status!r}.")
    return {field: _stringify(row.get(field, "")) for field in REGISTRY_FIELDS}


def read_registry(path: Path) -> list[dict[str, str]]:
    """Read registry rows, returning an empty list when no registry exists."""

    if not Path(path).exists():
        return []
    with Path(path).open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = []
        for row in reader:
            rows.append(normalize_row(row))
    return rows


def write_registry(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    """Write registry rows with deterministic field order."""

    registry_file = Path(path)
    registry_file.parent.mkdir(parents=True, exist_ok=True)
    normalized = [normalize_row(row) for row in rows]
    with registry_file.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=REGISTRY_FIELDS)
        writer.writeheader()
        writer.writerows(normalized)


def upsert_registry_row(path: Path, row: Mapping[str, Any]) -> None:
    """Insert or replace one attempt row by ``run_attempt_id``."""

    normalized = normalize_row(row)
    attempt_id = normalized["run_attempt_id"]
    if not attempt_id:
        raise RunRegistryError("run_attempt_id is required.")

    rows = read_registry(path)
    replaced = False
    for index, existing in enumerate(rows):
        if existing["run_attempt_id"] == attempt_id:
            rows[index] = normalized
            replaced = True
            break
    if not replaced:
        rows.append(normalized)
    write_registry(path, rows)
    write_registry_summary(path.parent / "run_registry_summary.yaml", rows)


def append_registry_row(path: Path, row: Mapping[str, Any]) -> None:
    """Append one new attempt row."""

    normalized = normalize_row(row)
    rows = read_registry(path)
    rows.append(normalized)
    write_registry(path, rows)
    write_registry_summary(path.parent / "run_registry_summary.yaml", rows)


def latest_row_for_leaf(rows: Iterable[Mapping[str, str]], scenario_leaf_id: str) -> dict[str, str] | None:
    """Return the latest registry row for a leaf by start timestamp and file order."""

    selected = [dict(row) for row in rows if row.get("scenario_leaf_id") == scenario_leaf_id]
    if not selected:
        return None
    return sorted(
        enumerate(selected),
        key=lambda item: (item[1].get("timestamp_start_utc", ""), item[0]),
    )[-1][1]


def latest_actual_run_for_leaf(rows: Iterable[Mapping[str, str]], scenario_leaf_id: str) -> dict[str, str] | None:
    """Return the latest actual run row, excluding planned/skipped records."""

    selected = [
        dict(row)
        for row in rows
        if row.get("scenario_leaf_id") == scenario_leaf_id and row.get("status") in ACTUAL_RUN_STATUSES
    ]
    if not selected:
        return None
    return sorted(
        enumerate(selected),
        key=lambda item: (item[1].get("timestamp_start_utc", ""), item[0]),
    )[-1][1]


def latest_actual_status(rows: Iterable[Mapping[str, str]], scenario_leaf_id: str) -> str:
    latest = latest_actual_run_for_leaf(rows, scenario_leaf_id)
    return "not_run" if latest is None else str(latest.get("status", "not_run"))


def is_successful(rows: Iterable[Mapping[str, str]], scenario_leaf_id: str) -> bool:
    """Return whether the latest actual run for a leaf succeeded."""

    return latest_actual_status(rows, scenario_leaf_id) == "success"


def status_counts(rows: Iterable[Mapping[str, str]]) -> dict[str, int]:
    """Return counts for all registry statuses."""

    counts = Counter(str(row.get("status", "")) for row in rows)
    return {status: int(counts.get(status, 0)) for status in sorted(ALLOWED_STATUSES)}


def latest_actual_status_counts(rows: Iterable[Mapping[str, str]]) -> dict[str, int]:
    """Return counts of latest actual status per scenario leaf."""

    rows_list = [dict(row) for row in rows]
    leaf_ids = sorted({row.get("scenario_leaf_id", "") for row in rows_list if row.get("scenario_leaf_id")})
    counts = Counter(latest_actual_status(rows_list, leaf_id) for leaf_id in leaf_ids)
    return {status: int(counts.get(status, 0)) for status in ("not_run", "running", "success", "failed")}


def write_registry_summary(path: Path, rows: Iterable[Mapping[str, str]]) -> None:
    """Write a compact machine-readable registry summary."""

    rows_list = [dict(row) for row in rows]
    payload = {
        "registry_rows": len(rows_list),
        "status_counts": status_counts(rows_list),
        "latest_actual_status_counts": latest_actual_status_counts(rows_list),
    }
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with Path(path).open("w", encoding="utf-8") as handle:
        yaml.safe_dump(payload, handle, sort_keys=False)

