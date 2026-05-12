"""Serial scenario-tree runner for reproducible model_v3 leaf execution."""

from __future__ import annotations

import argparse
import contextlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
import sys
from time import perf_counter
from typing import Any, Mapping

import yaml

from model_v3.scenario_tree import paths as scenario_paths
from model_v3.scenario_tree.naming import parse_scenario_leaf_id
from model_v3.scenarios.model_runner_adapter import DEFAULT_BASE_MODEL_CONFIG, run_model_from_config
from model_v3.scenarios.provenance import (
    get_git_commit,
    get_git_dirty,
    get_model_version,
    sha256_file,
)
from model_v3.scenarios.registry import (
    ALLOWED_STATUSES,
    append_registry_row,
    is_successful,
    latest_actual_status,
    latest_row_for_leaf,
    read_registry,
    registry_path,
    status_counts,
    upsert_registry_row,
)
from model_v3.scenarios.selection import (
    ScenarioLeafRecord,
    ScenarioSelectionError,
    load_leaf_records,
    select_leaf_records,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_EXPERIMENT_ROOT = REPO_ROOT / "model_v3" / "experiments" / "scenario_tree"
DEFAULT_CONFIG_ROOT = REPO_ROOT / "config" / "model_v3" / "scenario_tree"
DEFAULT_LEAF_INDEX = DEFAULT_EXPERIMENT_ROOT / "manifests" / "scenario_leaf_index.csv"
REQUIRED_RUN_CONFIG_SECTIONS = {
    "schema_version",
    "scenario_leaf",
    "climate",
    "technology",
    "stochastic",
    "model_options",
    "output",
    "validation",
    "provenance",
}


@dataclass
class LeafValidation:
    """Validated paths and metadata for one scenario leaf."""

    record: ScenarioLeafRecord
    run_dir: Path
    config_path: Path
    inputs_manifest_path: Path
    outputs_dir: Path
    logs_dir: Path
    config: dict[str, Any] = field(default_factory=dict)
    inputs_manifest: dict[str, Any] = field(default_factory=dict)
    climate_forcing_file: Path | None = None
    belgian_technology_inputs: Path | None = None
    random_seed: int | None = None
    cohort_size: int | None = None
    errors: list[str] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return not self.errors


@dataclass(frozen=True)
class PlanItem:
    """One deterministic dry-run or execution plan row."""

    record: ScenarioLeafRecord
    validation: LeafValidation
    status: str
    skip_reason: str = ""
    error_message: str = ""


def _resolve_repo_path(path_text: str | Path, repo_root: Path = REPO_ROOT) -> Path:
    path = Path(path_text)
    if path.is_absolute():
        return path
    return repo_root / path


def _resolve_cli_path(path: str | Path, repo_root: Path = REPO_ROOT) -> Path:
    raw = Path(path)
    if raw.is_absolute():
        return raw
    cwd_candidate = (Path.cwd() / raw).resolve()
    if cwd_candidate.exists():
        return cwd_candidate
    return repo_root / raw


def _load_yaml(path: Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"YAML file must contain a mapping: {path}")
    return data


def _is_relative_to(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def _classify_validation_errors(errors: list[str]) -> str:
    joined = "\n".join(errors)
    if "Missing run config" in joined:
        return "missing_config"
    if "Missing inputs manifest" in joined or "Missing climate forcing" in joined or "Missing Belgian technology" in joined:
        return "missing_input"
    if "Invalid scenario leaf ID" in joined:
        return "invalid_leaf_id"
    return "invalid_config"


def validate_leaf(
    record: ScenarioLeafRecord,
    *,
    experiment_root: Path,
    repo_root: Path = REPO_ROOT,
    create_dirs: bool = False,
) -> LeafValidation:
    """Validate one leaf before dry-run planning or execution."""

    try:
        leaf_paths = scenario_paths.paths_for_leaf(experiment_root, record.scenario_leaf_id)
    except ValueError as exc:
        parsed = parse_scenario_leaf_id(record.scenario_leaf_id)
        run_dir = scenario_paths.get_runs_dir(experiment_root) / record.scenario_leaf_id
        validation = LeafValidation(
            record=record,
            run_dir=run_dir,
            config_path=run_dir / "run_config.yaml",
            inputs_manifest_path=run_dir / "inputs_manifest.yaml",
            outputs_dir=run_dir / "outputs",
            logs_dir=run_dir / "logs",
        )
        validation.errors.append(f"Invalid scenario leaf ID {parsed.get('scenario_leaf_id', record.scenario_leaf_id)!r}: {exc}")
        return validation

    validation = LeafValidation(
        record=record,
        run_dir=leaf_paths["run_dir"],
        config_path=leaf_paths["run_config_path"],
        inputs_manifest_path=leaf_paths["inputs_manifest_path"],
        outputs_dir=leaf_paths["outputs_dir"],
        logs_dir=leaf_paths["logs_dir"],
    )

    if not validation.config_path.exists():
        validation.errors.append(f"Missing run config: {validation.config_path}")
        return validation
    try:
        validation.config = _load_yaml(validation.config_path)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        validation.errors.append(f"Run config is not parseable YAML: {validation.config_path}: {exc}")
        return validation

    missing_sections = sorted(REQUIRED_RUN_CONFIG_SECTIONS.difference(validation.config))
    if missing_sections:
        validation.errors.append(f"Run config missing top-level section(s): {', '.join(missing_sections)}.")

    scenario_leaf = dict(validation.config.get("scenario_leaf", {}))
    if scenario_leaf.get("id") != record.scenario_leaf_id:
        validation.errors.append(
            f"scenario_leaf.id={scenario_leaf.get('id')!r} does not match requested leaf {record.scenario_leaf_id!r}."
        )
    for field_name in ("scenario_id", "climate_window_id", "climate_pathway_id", "technology_case_id", "realization_id"):
        if scenario_leaf.get(field_name) != getattr(record, field_name):
            validation.errors.append(
                f"scenario_leaf.{field_name}={scenario_leaf.get(field_name)!r} does not match leaf index "
                f"value {getattr(record, field_name)!r}."
            )

    if not validation.inputs_manifest_path.exists():
        validation.errors.append(f"Missing inputs manifest: {validation.inputs_manifest_path}")
    else:
        try:
            validation.inputs_manifest = _load_yaml(validation.inputs_manifest_path)
        except (OSError, ValueError, yaml.YAMLError) as exc:
            validation.errors.append(
                f"Inputs manifest is not parseable YAML: {validation.inputs_manifest_path}: {exc}"
            )

    if validation.inputs_manifest and validation.inputs_manifest.get("scenario_leaf_id") != record.scenario_leaf_id:
        validation.errors.append("inputs_manifest.yaml scenario_leaf_id does not match requested leaf.")

    climate_cfg = dict(validation.config.get("climate", {}))
    forcing_file = climate_cfg.get("forcing_file")
    if not isinstance(forcing_file, str) or not forcing_file:
        validation.errors.append("climate.forcing_file must be a non-empty path.")
    else:
        validation.climate_forcing_file = _resolve_repo_path(forcing_file, repo_root)
        if not validation.climate_forcing_file.exists():
            validation.errors.append(f"Missing climate forcing file: {validation.climate_forcing_file}")

    technology_cfg = dict(validation.config.get("technology", {}))
    belgian_inputs = technology_cfg.get("belgian_technology_inputs")
    if not isinstance(belgian_inputs, str) or not belgian_inputs:
        validation.errors.append("technology.belgian_technology_inputs must be a non-empty path.")
    else:
        validation.belgian_technology_inputs = _resolve_repo_path(belgian_inputs, repo_root)
        if not validation.belgian_technology_inputs.exists():
            validation.errors.append(f"Missing Belgian technology input YAML: {validation.belgian_technology_inputs}")

    stochastic_cfg = dict(validation.config.get("stochastic", {}))
    try:
        validation.random_seed = int(stochastic_cfg["seed_value"])
    except (KeyError, TypeError, ValueError):
        validation.errors.append("stochastic.seed_value must be defined as an integer.")
    try:
        validation.cohort_size = int(stochastic_cfg["cohort_size"])
    except (KeyError, TypeError, ValueError):
        validation.errors.append("stochastic.cohort_size must be defined as an integer.")

    output_cfg = dict(validation.config.get("output", {}))
    configured_run_dir = _resolve_repo_path(str(output_cfg.get("run_dir", "")), repo_root)
    configured_outputs_dir = _resolve_repo_path(str(output_cfg.get("outputs_dir", "")), repo_root)
    configured_logs_dir = _resolve_repo_path(str(output_cfg.get("logs_dir", "")), repo_root)
    if configured_run_dir.resolve() != validation.run_dir.resolve():
        validation.errors.append(f"output.run_dir must be {validation.run_dir}, found {configured_run_dir}.")
    if configured_outputs_dir.resolve() != validation.outputs_dir.resolve():
        validation.errors.append(f"output.outputs_dir must be {validation.outputs_dir}, found {configured_outputs_dir}.")
    if configured_logs_dir.resolve() != validation.logs_dir.resolve():
        validation.errors.append(f"output.logs_dir must be {validation.logs_dir}, found {configured_logs_dir}.")
    if not _is_relative_to(configured_outputs_dir, validation.run_dir):
        validation.errors.append("output.outputs_dir must belong to the leaf run directory.")

    validation_cfg = dict(validation.config.get("validation", {}))
    if validation_cfg.get("config_complete") is not True:
        validation.errors.append("validation.config_complete must be true before execution.")
    missing_required_inputs = validation_cfg.get("missing_required_inputs")
    if missing_required_inputs not in ([], None):
        validation.errors.append(f"validation.missing_required_inputs must be empty, found {missing_required_inputs!r}.")

    if create_dirs:
        validation.outputs_dir.mkdir(parents=True, exist_ok=True)
        validation.logs_dir.mkdir(parents=True, exist_ok=True)
    else:
        if not validation.outputs_dir.exists():
            validation.errors.append(f"Output directory does not exist: {validation.outputs_dir}")
        if not validation.logs_dir.exists():
            validation.errors.append(f"Logs directory does not exist: {validation.logs_dir}")
    return validation


def _matches_only_status(
    record: ScenarioLeafRecord,
    *,
    registry_rows: list[dict[str, str]],
    only_status: str | None,
) -> bool:
    if only_status is None:
        return True
    return latest_actual_status(registry_rows, record.scenario_leaf_id) == only_status


def build_run_plan(
    records: list[ScenarioLeafRecord],
    *,
    experiment_root: Path,
    registry_rows: list[dict[str, str]],
    force: bool = False,
    ignore_stale_running: bool = False,
    only_status: str | None = None,
    repo_root: Path = REPO_ROOT,
) -> list[PlanItem]:
    """Build a deterministic plan for selected leaves."""

    plan: list[PlanItem] = []
    for record in sorted(records, key=lambda item: item.scenario_leaf_id):
        if not _matches_only_status(record, registry_rows=registry_rows, only_status=only_status):
            continue
        validation = validate_leaf(record, experiment_root=experiment_root, repo_root=repo_root, create_dirs=False)
        if not validation.valid:
            plan.append(
                PlanItem(
                    record=record,
                    validation=validation,
                    status="invalid",
                    skip_reason=_classify_validation_errors(validation.errors),
                    error_message="; ".join(validation.errors),
                )
            )
            continue

        latest = latest_row_for_leaf(registry_rows, record.scenario_leaf_id)
        if latest and latest.get("status") == "running" and not (force or ignore_stale_running):
            plan.append(
                PlanItem(
                    record=record,
                    validation=validation,
                    status="skipped",
                    skip_reason="stale_running_requires_force",
                )
            )
            continue
        if is_successful(registry_rows, record.scenario_leaf_id) and not force:
            plan.append(
                PlanItem(
                    record=record,
                    validation=validation,
                    status="skipped",
                    skip_reason="already_successful",
                )
            )
            continue
        plan.append(PlanItem(record=record, validation=validation, status="eligible"))
    return plan


def _run_attempt_id(leaf_id: str, start: datetime, registry_rows: list[dict[str, str]]) -> str:
    stamp = start.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base = f"{leaf_id}__attempt_{stamp}"
    existing = {row.get("run_attempt_id") for row in registry_rows}
    if base not in existing:
        return base
    suffix = 1
    while f"{base}_{suffix:03d}" in existing:
        suffix += 1
    return f"{base}_{suffix:03d}"


def _base_registry_row(
    validation: LeafValidation,
    *,
    run_attempt_id: str,
    timestamp_start_utc: str,
    status: str,
    repo_root: Path,
    log_path: Path | None = None,
) -> dict[str, Any]:
    if status not in ALLOWED_STATUSES:
        raise ValueError(f"Invalid registry status: {status}")
    record = validation.record
    git_dirty = get_git_dirty(repo_root)
    return {
        "run_attempt_id": run_attempt_id,
        "scenario_leaf_id": record.scenario_leaf_id,
        "scenario_id": record.scenario_id,
        "climate_window_id": record.climate_window_id,
        "climate_pathway_id": record.climate_pathway_id,
        "technology_case_id": record.technology_case_id,
        "realization_id": record.realization_id,
        "timestamp_start_utc": timestamp_start_utc,
        "timestamp_end_utc": "",
        "duration_seconds": "",
        "status": status,
        "skip_reason": "",
        "git_commit": get_git_commit(repo_root) or "",
        "git_is_dirty": "" if git_dirty is None else git_dirty,
        "config_path": str(validation.config_path),
        "config_hash_sha256": sha256_file(validation.config_path, max_size_bytes=None),
        "inputs_manifest_path": str(validation.inputs_manifest_path),
        "inputs_manifest_hash_sha256": sha256_file(validation.inputs_manifest_path, max_size_bytes=None),
        "climate_forcing_file": "" if validation.climate_forcing_file is None else str(validation.climate_forcing_file),
        "climate_forcing_hash_sha256": (
            "" if validation.climate_forcing_file is None else sha256_file(validation.climate_forcing_file)
        ),
        "belgian_technology_inputs": (
            "" if validation.belgian_technology_inputs is None else str(validation.belgian_technology_inputs)
        ),
        "belgian_technology_inputs_hash_sha256": (
            "" if validation.belgian_technology_inputs is None else sha256_file(validation.belgian_technology_inputs)
        ),
        "random_seed": "" if validation.random_seed is None else validation.random_seed,
        "cohort_size": "" if validation.cohort_size is None else validation.cohort_size,
        "model_version": get_model_version(),
        "output_path": str(validation.outputs_dir),
        "log_path": "" if log_path is None else str(log_path),
        "error_type": "",
        "error_message": "",
    }


def _finish_row(
    row: Mapping[str, Any],
    *,
    status: str,
    start_perf: float,
    error_type: str = "",
    error_message: str = "",
    skip_reason: str = "",
) -> dict[str, Any]:
    finished = dict(row)
    finished["status"] = status
    finished["timestamp_end_utc"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    finished["duration_seconds"] = f"{perf_counter() - start_perf:.3f}"
    finished["error_type"] = error_type
    finished["error_message"] = error_message
    finished["skip_reason"] = skip_reason
    return finished


def _write_runner_status(path: Path, row: Mapping[str, Any], *, adapter_result: Mapping[str, Any] | None = None) -> None:
    payload = {
        "run_attempt_id": row.get("run_attempt_id", ""),
        "scenario_leaf_id": row.get("scenario_leaf_id", ""),
        "status": row.get("status", ""),
        "start_timestamp": row.get("timestamp_start_utc", ""),
        "end_timestamp": row.get("timestamp_end_utc", ""),
        "duration_seconds": row.get("duration_seconds", ""),
        "config_hash_sha256": row.get("config_hash_sha256", ""),
        "inputs_manifest_hash_sha256": row.get("inputs_manifest_hash_sha256", ""),
        "climate_forcing_hash_sha256": row.get("climate_forcing_hash_sha256", ""),
        "belgian_technology_inputs_hash_sha256": row.get("belgian_technology_inputs_hash_sha256", ""),
        "output_path": row.get("output_path", ""),
        "error_type": row.get("error_type", ""),
        "error_message": row.get("error_message", ""),
    }
    if adapter_result:
        payload["adapter_result"] = dict(adapter_result)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(payload, handle, sort_keys=False)


def _append_skip_row(
    validation: LeafValidation,
    *,
    registry_file: Path,
    registry_rows: list[dict[str, str]],
    skip_reason: str,
    repo_root: Path,
) -> dict[str, Any]:
    start = datetime.now(timezone.utc)
    start_perf = perf_counter()
    row = _base_registry_row(
        validation,
        run_attempt_id=_run_attempt_id(validation.record.scenario_leaf_id, start, registry_rows),
        timestamp_start_utc=start.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        status="skipped",
        repo_root=repo_root,
    )
    row = _finish_row(row, status="skipped", start_perf=start_perf, skip_reason=skip_reason)
    append_registry_row(registry_file, row)
    return row


def execute_leaf(
    record: ScenarioLeafRecord,
    *,
    experiment_root: Path,
    registry_file: Path,
    force: bool,
    ignore_stale_running: bool,
    base_model_config_path: Path,
    repo_root: Path = REPO_ROOT,
) -> dict[str, Any]:
    """Execute one scenario leaf serially and update the run registry."""

    registry_rows = read_registry(registry_file)
    validation = validate_leaf(record, experiment_root=experiment_root, repo_root=repo_root, create_dirs=True)
    latest = latest_row_for_leaf(registry_rows, record.scenario_leaf_id)
    if latest and latest.get("status") == "running" and not (force or ignore_stale_running):
        return {
            "status": "skipped",
            "scenario_leaf_id": record.scenario_leaf_id,
            "skip_reason": "stale_running_requires_force",
            "registry_row": None,
        }
    if validation.valid and is_successful(registry_rows, record.scenario_leaf_id) and not force:
        row = _append_skip_row(
            validation,
            registry_file=registry_file,
            registry_rows=registry_rows,
            skip_reason="already_successful",
            repo_root=repo_root,
        )
        return {
            "status": "skipped",
            "scenario_leaf_id": record.scenario_leaf_id,
            "skip_reason": "already_successful",
            "registry_row": row,
        }

    start = datetime.now(timezone.utc)
    start_text = start.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    start_perf = perf_counter()
    attempt_id = _run_attempt_id(record.scenario_leaf_id, start, registry_rows)
    attempt_log_dir = validation.logs_dir / "attempts" / attempt_id
    attempt_log_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = attempt_log_dir / "run_stdout.log"
    stderr_path = attempt_log_dir / "run_stderr.log"
    status_path = attempt_log_dir / "runner_status.yaml"
    row = _base_registry_row(
        validation,
        run_attempt_id=attempt_id,
        timestamp_start_utc=start_text,
        status="running",
        repo_root=repo_root,
        log_path=attempt_log_dir,
    )
    upsert_registry_row(registry_file, row)

    if not validation.valid:
        stderr_path.write_text("\n".join(validation.errors) + "\n", encoding="utf-8")
        stdout_path.write_text("", encoding="utf-8")
        finished = _finish_row(
            row,
            status="failed",
            start_perf=start_perf,
            error_type="ValidationError",
            error_message="; ".join(validation.errors),
            skip_reason=_classify_validation_errors(validation.errors),
        )
        upsert_registry_row(registry_file, finished)
        _write_runner_status(status_path, finished)
        return {"status": "failed", "scenario_leaf_id": record.scenario_leaf_id, "registry_row": finished}

    adapter_result: dict[str, Any] = {}
    try:
        with stdout_path.open("w", encoding="utf-8") as stdout_handle, stderr_path.open("w", encoding="utf-8") as stderr_handle:
            with contextlib.redirect_stdout(stdout_handle), contextlib.redirect_stderr(stderr_handle):
                adapter_result = run_model_from_config(
                    validation.config_path,
                    base_model_config_path=base_model_config_path,
                    repo_root=repo_root,
                )
        status = str(adapter_result.get("status", "success"))
        if status != "success":
            raise RuntimeError(str(adapter_result.get("message", "Model adapter returned non-success status.")))
        finished = _finish_row(row, status="success", start_perf=start_perf)
    except Exception as exc:
        with stderr_path.open("a", encoding="utf-8") as stderr_handle:
            stderr_handle.write(f"{type(exc).__name__}: {exc}\n")
        finished = _finish_row(
            row,
            status="failed",
            start_perf=start_perf,
            error_type=type(exc).__name__,
            error_message=str(exc),
        )
    upsert_registry_row(registry_file, finished)
    _write_runner_status(status_path, finished, adapter_result=adapter_result)
    return {
        "status": finished["status"],
        "scenario_leaf_id": record.scenario_leaf_id,
        "registry_row": finished,
        "adapter_result": adapter_result,
    }


def _plan_counts(plan: list[PlanItem]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in plan:
        key = item.status if item.status != "skipped" else f"skipped:{item.skip_reason}"
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def print_plan(plan: list[PlanItem], *, print_summary: bool) -> None:
    for item in plan:
        reason = item.skip_reason or item.error_message
        suffix = f" reason={reason}" if reason else ""
        print(f"{item.record.scenario_leaf_id}\t{item.status}{suffix}")
    print("Scenario-tree runner summary:")
    print(f"- selected: {len(plan)}")
    for key, value in _plan_counts(plan).items():
        print(f"- {key}: {value}")


def _write_plan_csv(path: Path, plan: list[PlanItem]) -> None:
    import csv

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["scenario_leaf_id", "status", "skip_reason", "config_path", "output_path", "error_message"],
        )
        writer.writeheader()
        for item in plan:
            writer.writerow(
                {
                    "scenario_leaf_id": item.record.scenario_leaf_id,
                    "status": item.status,
                    "skip_reason": item.skip_reason,
                    "config_path": item.validation.config_path,
                    "output_path": item.validation.outputs_dir,
                    "error_message": item.error_message,
                }
            )


def _validate_only_status(value: str | None) -> str | None:
    if value is None:
        return None
    allowed = set(ALLOWED_STATUSES).union({"not_run"})
    if value not in allowed:
        raise argparse.ArgumentTypeError(f"--only-status must be one of: {', '.join(sorted(allowed))}.")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Plan and validate leaves without running the model.")
    parser.add_argument("--scenario-leaf-id", help="Execute or plan exactly one scenario leaf.")
    parser.add_argument("--all", action="store_true", help="Select all leaves from the scenario leaf index.")
    parser.add_argument("--experiment-root", type=Path, default=DEFAULT_EXPERIMENT_ROOT)
    parser.add_argument("--config-root", type=Path, default=DEFAULT_CONFIG_ROOT)
    parser.add_argument("--leaf-index", type=Path, default=DEFAULT_LEAF_INDEX)
    parser.add_argument("--base-model-config", type=Path, default=DEFAULT_BASE_MODEL_CONFIG)
    parser.add_argument("--only-status", type=_validate_only_status, default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--continue-on-error", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--ignore-stale-running", action="store_true")
    parser.add_argument("--max-workers", type=int, default=1)
    parser.add_argument("--print-summary", action="store_true")
    parser.add_argument("--write-plan", action="store_true", help="Write dry-run plan CSV under manifests/dry_run_plan.csv.")
    parser.add_argument("--climate-window-id")
    parser.add_argument("--climate-pathway-id")
    parser.add_argument("--technology-case-id")
    parser.add_argument("--realization-id")
    return parser


def _prepare_selection(args: argparse.Namespace) -> tuple[Path, Path, list[ScenarioLeafRecord]]:
    experiment_root = _resolve_cli_path(args.experiment_root)
    leaf_index = _resolve_cli_path(args.leaf_index)
    records = load_leaf_records(leaf_index)
    default_all = bool(args.dry_run and not args.scenario_leaf_id and not args.all)
    selected = select_leaf_records(
        records,
        scenario_leaf_id=args.scenario_leaf_id,
        all_leaves=args.all,
        climate_window_id=args.climate_window_id,
        climate_pathway_id=args.climate_pathway_id,
        technology_case_id=args.technology_case_id,
        realization_id=args.realization_id,
        limit=args.limit,
        default_all=default_all,
    )
    return experiment_root, leaf_index, selected


def run_dry_run(args: argparse.Namespace) -> int:
    experiment_root, _, selected = _prepare_selection(args)
    registry_file = registry_path(experiment_root)
    plan = build_run_plan(
        selected,
        experiment_root=experiment_root,
        registry_rows=read_registry(registry_file),
        force=args.force,
        ignore_stale_running=args.ignore_stale_running,
        only_status=args.only_status,
        repo_root=REPO_ROOT,
    )
    print_plan(plan, print_summary=args.print_summary)
    if args.write_plan:
        _write_plan_csv(experiment_root / "manifests" / "dry_run_plan.csv", plan)
    return 1 if any(item.status == "invalid" for item in plan) else 0


def run_execution(args: argparse.Namespace) -> int:
    if args.max_workers != 1:
        print("Only --max-workers 1 is supported in this serial phase.", file=sys.stderr)
        return 2
    experiment_root, _, selected = _prepare_selection(args)
    registry_file = registry_path(experiment_root)
    base_model_config_path = _resolve_cli_path(args.base_model_config)
    results: list[dict[str, Any]] = []
    exit_code = 0
    for record in selected:
        result = execute_leaf(
            record,
            experiment_root=experiment_root,
            registry_file=registry_file,
            force=args.force,
            ignore_stale_running=args.ignore_stale_running,
            base_model_config_path=base_model_config_path,
            repo_root=REPO_ROOT,
        )
        results.append(result)
        status = result.get("status")
        skip_reason = result.get("skip_reason", "")
        if status == "failed":
            exit_code = 1
            print(f"{record.scenario_leaf_id}\tfailed")
            if not args.continue_on_error:
                break
        elif status == "skipped":
            print(f"{record.scenario_leaf_id}\tskipped reason={skip_reason}")
        else:
            print(f"{record.scenario_leaf_id}\t{status}")

    if args.print_summary or args.all:
        counts: dict[str, int] = {}
        for result in results:
            key = str(result.get("status", "unknown"))
            counts[key] = counts.get(key, 0) + 1
        print("Batch summary:")
        for key in sorted(counts):
            print(f"- {key}: {counts[key]}")
        print(f"Registry: {registry_file}")
        print(f"Registry status counts: {status_counts(read_registry(registry_file))}")
    return exit_code


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _ = _resolve_cli_path(args.config_root)

    if not args.dry_run and not args.scenario_leaf_id and not args.all:
        args.dry_run = True
    try:
        if args.dry_run:
            return run_dry_run(args)
        return run_execution(args)
    except ScenarioSelectionError as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
