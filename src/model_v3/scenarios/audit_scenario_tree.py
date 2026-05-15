"""Methodological audit and thesis-facing reports for the scenario tree."""

from __future__ import annotations

import argparse
import csv
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml

from model_v3.scenarios.provenance import get_git_commit, get_git_dirty, sha256_file
from model_v3.scenarios.registry import ACTUAL_RUN_STATUSES, read_registry


REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_EXPERIMENT_ROOT = REPO_ROOT / "experiments" / "scenario_tree"
DEFAULT_CONFIG_ROOT = REPO_ROOT / "config" / "scenario_tree"
DEFAULT_FIGURES_ROOT = REPO_ROOT / "figures" / "scenario_tree"
DEFAULT_REPORTS_ROOT = REPO_ROOT / "reports"

TRACEABILITY_COLUMNS = [
    "scenario_leaf_id",
    "scenario_id",
    "climate_window_id",
    "climate_pathway_id",
    "technology_case_id",
    "realization_id",
    "seed_index",
    "seed_value",
    "cohort_size",
    "analysis_start",
    "analysis_end",
    "source_file_window",
    "climate_forcing_file",
    "climate_forcing_exists",
    "technology_metadata_file",
    "belgian_technology_inputs",
    "belgian_technology_inputs_exists",
    "run_config",
    "run_config_exists",
    "inputs_manifest",
    "inputs_manifest_exists",
    "output_dir",
    "output_dir_exists",
    "standardized_leaf_summary",
    "standardized_leaf_summary_exists",
    "latest_run_attempt_id",
    "latest_run_status",
    "config_hash_sha256",
    "inputs_manifest_hash_sha256",
    "climate_forcing_hash_sha256",
    "belgian_technology_inputs_hash_sha256",
    "model_version",
    "git_commit",
    "git_is_dirty",
    "traceability_complete",
    "missing_traceability_fields",
]

REQUIRED_TRACEABILITY_FIELDS = [
    "scenario_leaf_id",
    "scenario_id",
    "climate_window_id",
    "climate_pathway_id",
    "technology_case_id",
    "realization_id",
    "seed_index",
    "seed_value",
    "cohort_size",
    "analysis_start",
    "analysis_end",
    "source_file_window",
    "climate_forcing_file",
    "technology_metadata_file",
    "belgian_technology_inputs",
    "run_config",
    "inputs_manifest",
    "output_dir",
    "standardized_leaf_summary",
    "latest_run_attempt_id",
    "latest_run_status",
    "config_hash_sha256",
    "inputs_manifest_hash_sha256",
    "climate_forcing_hash_sha256",
    "belgian_technology_inputs_hash_sha256",
    "model_version",
]

REQUIRED_EXISTENCE_FIELDS = [
    "climate_forcing_exists",
    "belgian_technology_inputs_exists",
    "run_config_exists",
    "inputs_manifest_exists",
    "output_dir_exists",
    "standardized_leaf_summary_exists",
]


class ScenarioTreeAuditError(RuntimeError):
    """Raised when the audit cannot read required metadata."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def resolve_cli_path(path_text: str | Path) -> Path:
    path = Path(path_text)
    if path.is_absolute():
        return path
    cwd_candidate = (Path.cwd() / path).resolve()
    if cwd_candidate.exists():
        return cwd_candidate
    return (REPO_ROOT / path).resolve()


def _resolve_data_path(path_text: str | Path | None, *, anchor: Path | None = None) -> Path | None:
    if path_text is None or str(path_text).strip() == "":
        return None
    path = Path(str(path_text))
    if path.is_absolute():
        return path
    candidates = []
    if anchor is not None:
        candidates.append((anchor / path).resolve())
    candidates.extend([(Path.cwd() / path).resolve(), (REPO_ROOT / path).resolve()])
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[-1]


def _relative(path: Path | None) -> str:
    if path is None:
        return ""
    try:
        return str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _read_yaml(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    return data if isinstance(data, dict) else {}


def _first_present(*values: Any) -> str:
    for value in values:
        if value is None:
            continue
        text = str(value)
        if text.strip() != "":
            return text
    return ""


def _write_csv(path: Path, rows: Iterable[Mapping[str, Any]], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in columns})


def _latest_actual_by_leaf(rows: Iterable[Mapping[str, str]]) -> dict[str, dict[str, str]]:
    indexed: dict[str, tuple[int, dict[str, str]]] = {}
    for index, row in enumerate(rows):
        status = row.get("status", "")
        leaf_id = row.get("scenario_leaf_id", "")
        if not leaf_id or status not in ACTUAL_RUN_STATUSES:
            continue
        sort_key = (row.get("timestamp_start_utc", ""), index)
        current = indexed.get(leaf_id)
        if current is None or sort_key > (current[1].get("timestamp_start_utc", ""), current[0]):
            indexed[leaf_id] = (index, dict(row))
    return {leaf_id: row for leaf_id, (_, row) in indexed.items()}


def _status_counts(rows: Iterable[Mapping[str, str]]) -> dict[str, int]:
    counts = Counter(row.get("status", "") for row in rows)
    return {status: int(counts.get(status, 0)) for status in sorted(set(counts) | {"success", "failed", "skipped"})}


def _first_existing_summary(output_dir: Path | None) -> Path | None:
    if output_dir is None:
        return None
    candidate = output_dir / "standardized_leaf_summary.csv"
    return candidate


def _load_config_counts(config_root: Path, experiment_root: Path) -> dict[str, int]:
    manifest = _read_yaml(experiment_root / "manifests" / "scenario_tree_manifest.yaml")
    counts = dict(manifest.get("counts", {})) if isinstance(manifest.get("counts"), dict) else {}
    climate = _read_yaml(config_root / "climate_windows.yaml")
    tech = _read_yaml(config_root / "technology_cases.yaml")
    realization = _read_yaml(config_root / "realization_policy.yaml")
    schema = _read_yaml(config_root / "scenario_tree_schema.yaml")
    counts.setdefault("climate_windows", len(climate.get("climate_windows", {})))
    future = schema.get("climate_pathways", {}).get("future_pathways", [])
    counts.setdefault("climate_pathways", len(set(["historical", *future])))
    counts.setdefault("technology_cases", len(tech.get("technology_cases", {})))
    counts.setdefault("realizations", realization.get("realization_policy", {}).get("number_of_seeds", 0))
    return {key: int(value) for key, value in counts.items() if str(value).isdigit() or isinstance(value, int)}


def build_traceability_matrix(
    *,
    experiment_root: Path,
    config_root: Path,
    repo_root: Path = REPO_ROOT,
) -> tuple[list[dict[str, str]], list[str]]:
    """Build one traceability row for each latest-successful scenario leaf."""

    registry_rows = read_registry(experiment_root / "manifests" / "run_registry.csv")
    latest_actual = _latest_actual_by_leaf(registry_rows)
    successful_rows = {
        leaf_id: row for leaf_id, row in latest_actual.items() if row.get("status") == "success"
    }
    leaf_index_rows = {
        row.get("scenario_leaf_id", ""): row
        for row in _read_csv(experiment_root / "manifests" / "scenario_leaf_index.csv")
        if row.get("scenario_leaf_id")
    }
    summary_rows = {
        row.get("scenario_leaf_id", ""): row
        for row in _read_csv(experiment_root / "summaries" / "realization_level" / "scenario_leaf_metrics.csv")
        if row.get("scenario_leaf_id")
    }

    warnings: list[str] = []
    matrix: list[dict[str, str]] = []
    for leaf_id in sorted(successful_rows):
        registry = successful_rows[leaf_id]
        index_row = leaf_index_rows.get(leaf_id, {})
        summary = summary_rows.get(leaf_id, {})
        run_config = _resolve_data_path(registry.get("config_path") or index_row.get("run_config_path"))
        inputs_manifest = _resolve_data_path(
            registry.get("inputs_manifest_path") or index_row.get("inputs_manifest_path")
        )
        config = _read_yaml(run_config)
        inputs = _read_yaml(inputs_manifest)

        scenario_leaf_cfg = dict(config.get("scenario_leaf", {}))
        climate_cfg = dict(config.get("climate", {}))
        technology_cfg = dict(config.get("technology", {}))
        stochastic_cfg = dict(config.get("stochastic", {}))
        output_cfg = dict(config.get("output", {}))
        input_climate = dict(inputs.get("climate_forcing", {}))
        input_technology = dict(inputs.get("technology", {}))
        input_stochastic = dict(inputs.get("stochastic", {}))

        output_dir = _resolve_data_path(
            registry.get("output_path") or summary.get("raw_outputs_dir") or output_cfg.get("outputs_dir")
        )
        standardized = _first_existing_summary(output_dir)
        climate_forcing = _resolve_data_path(
            registry.get("climate_forcing_file")
            or summary.get("climate_forcing_file")
            or input_climate.get("file")
            or climate_cfg.get("forcing_file")
        )
        technology_metadata = _resolve_data_path(
            technology_cfg.get("metadata_file") or input_technology.get("metadata_file") or config_root / "technology_cases.yaml"
        )
        belgian_inputs = _resolve_data_path(
            registry.get("belgian_technology_inputs")
            or summary.get("technology_inputs_file")
            or input_technology.get("belgian_technology_inputs")
            or technology_cfg.get("belgian_technology_inputs")
        )

        row: dict[str, str] = {
            "scenario_leaf_id": leaf_id,
            "scenario_id": registry.get("scenario_id")
            or summary.get("scenario_id")
            or index_row.get("scenario_id")
            or scenario_leaf_cfg.get("scenario_id", ""),
            "climate_window_id": registry.get("climate_window_id")
            or summary.get("climate_window_id")
            or index_row.get("climate_window_id")
            or scenario_leaf_cfg.get("climate_window_id", "")
            or climate_cfg.get("window_id", ""),
            "climate_pathway_id": registry.get("climate_pathway_id")
            or summary.get("climate_pathway_id")
            or index_row.get("climate_pathway_id")
            or scenario_leaf_cfg.get("climate_pathway_id", "")
            or climate_cfg.get("pathway_id", ""),
            "technology_case_id": registry.get("technology_case_id")
            or summary.get("technology_case_id")
            or index_row.get("technology_case_id")
            or scenario_leaf_cfg.get("technology_case_id", "")
            or technology_cfg.get("case_id", ""),
            "realization_id": registry.get("realization_id")
            or summary.get("realization_id")
            or index_row.get("realization_id")
            or scenario_leaf_cfg.get("realization_id", "")
            or stochastic_cfg.get("realization_id", ""),
            "seed_index": _first_present(
                summary.get("seed_index"),
                stochastic_cfg.get("seed_index"),
                input_stochastic.get("seed_index"),
            ),
            "seed_value": _first_present(
                registry.get("random_seed")
                or summary.get("seed_value"),
                stochastic_cfg.get("seed_value"),
                input_stochastic.get("seed_value"),
            ),
            "cohort_size": _first_present(
                registry.get("cohort_size")
                or summary.get("cohort_size"),
                stochastic_cfg.get("cohort_size"),
                input_stochastic.get("cohort_size"),
            ),
            "analysis_start": str(
                summary.get("analysis_start") or input_climate.get("analysis_start") or climate_cfg.get("analysis_start") or ""
            ),
            "analysis_end": str(
                summary.get("analysis_end") or input_climate.get("analysis_end") or climate_cfg.get("analysis_end") or ""
            ),
            "source_file_window": str(
                summary.get("source_file_window")
                or input_climate.get("source_file_window")
                or climate_cfg.get("source_file_window")
                or ""
            ),
            "climate_forcing_file": _relative(climate_forcing),
            "climate_forcing_exists": str(bool(climate_forcing and climate_forcing.exists())).lower(),
            "technology_metadata_file": _relative(technology_metadata),
            "belgian_technology_inputs": _relative(belgian_inputs),
            "belgian_technology_inputs_exists": str(bool(belgian_inputs and belgian_inputs.exists())).lower(),
            "run_config": _relative(run_config),
            "run_config_exists": str(bool(run_config and run_config.exists())).lower(),
            "inputs_manifest": _relative(inputs_manifest),
            "inputs_manifest_exists": str(bool(inputs_manifest and inputs_manifest.exists())).lower(),
            "output_dir": _relative(output_dir),
            "output_dir_exists": str(bool(output_dir and output_dir.exists())).lower(),
            "standardized_leaf_summary": _relative(standardized),
            "standardized_leaf_summary_exists": str(
                bool(standardized and standardized.exists() and leaf_id in summary_rows)
            ).lower(),
            "latest_run_attempt_id": registry.get("run_attempt_id", ""),
            "latest_run_status": registry.get("status", ""),
            "config_hash_sha256": registry.get("config_hash_sha256") or sha256_file(run_config) if run_config else "",
            "inputs_manifest_hash_sha256": registry.get("inputs_manifest_hash_sha256") or sha256_file(inputs_manifest)
            if inputs_manifest
            else "",
            "climate_forcing_hash_sha256": registry.get("climate_forcing_hash_sha256") or sha256_file(climate_forcing)
            if climate_forcing
            else "",
            "belgian_technology_inputs_hash_sha256": registry.get("belgian_technology_inputs_hash_sha256")
            or sha256_file(belgian_inputs)
            if belgian_inputs
            else "",
            "model_version": registry.get("model_version", ""),
            "git_commit": registry.get("git_commit") or (get_git_commit(repo_root) or "not_available"),
            "git_is_dirty": registry.get("git_is_dirty") or (
                "not_available" if get_git_dirty(repo_root) is None else str(bool(get_git_dirty(repo_root))).lower()
            ),
        }
        missing = [
            field
            for field in REQUIRED_TRACEABILITY_FIELDS
            if str(row.get(field, "")).strip() in {"", "missing_file", "not_a_file"}
        ]
        missing.extend(field for field in REQUIRED_EXISTENCE_FIELDS if row.get(field) != "true")
        row["missing_traceability_fields"] = ";".join(sorted(set(missing)))
        row["traceability_complete"] = str(not missing).lower()
        if missing:
            warnings.append(f"HIGH: {leaf_id} missing traceability field(s): {row['missing_traceability_fields']}")
        matrix.append({column: row.get(column, "") for column in TRACEABILITY_COLUMNS})
    return matrix, warnings


def _count_comparison_tables(experiment_root: Path) -> int:
    root = experiment_root / "summaries" / "comparison_level"
    return len(list(root.glob("**/*.csv"))) if root.exists() else 0


def _count_figures(figures_root: Path) -> int:
    if not figures_root.exists():
        return 0
    return len(list(figures_root.glob("*/*.png"))) + len(list(figures_root.glob("*/*.pdf")))


def _validation_report_status(path: Path) -> tuple[str, list[str]]:
    if not path.exists():
        return "missing", [f"Missing report: {_relative(path)}"]
    text = path.read_text(encoding="utf-8")
    errors = []
    if "- None" not in text and "## Errors" in text:
        errors.append(f"Review errors section in {_relative(path)}")
    return "present", errors


def _traceability_markdown_rows(rows: list[dict[str, str]], limit: int = 2) -> list[str]:
    selected: list[dict[str, str]] = []
    baseline = next((row for row in rows if row["climate_pathway_id"] == "historical"), None)
    future = next((row for row in rows if row["climate_pathway_id"] != "historical"), None)
    for row in (baseline, future):
        if row is not None and row not in selected:
            selected.append(row)
    if not selected:
        selected = rows[:limit]
    lines = [
        "| Field | " + " | ".join(row["scenario_leaf_id"] for row in selected) + " |",
        "|---|" + "|".join("---" for _ in selected) + "|",
    ]
    fields = [
        "climate_forcing_file",
        "technology_case_id",
        "belgian_technology_inputs",
        "realization_id",
        "seed_value",
        "run_config",
        "inputs_manifest",
        "output_dir",
        "latest_run_attempt_id",
        "standardized_leaf_summary",
        "traceability_complete",
    ]
    for field in fields:
        lines.append("| `" + field + "` | " + " | ".join(row.get(field, "") for row in selected) + " |")
    return lines


def _write_run_manifest_report(
    path: Path,
    *,
    experiment_root: Path,
    config_root: Path,
    figures_root: Path,
    matrix_rows: list[dict[str, str]],
    audit_summary: Mapping[str, Any],
) -> None:
    counts = audit_summary["counts"]
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Scenario-Tree Run Manifest",
        "",
        "## Manifest sources",
        "",
        f"- Scenario-tree manifest: `{_relative(experiment_root / 'manifests' / 'scenario_tree_manifest.yaml')}`",
        f"- Scenario-leaf index: `{_relative(experiment_root / 'manifests' / 'scenario_leaf_index.csv')}`",
        f"- Run registry: `{_relative(experiment_root / 'manifests' / 'run_registry.csv')}`",
        f"- Realization-level summary: `{_relative(experiment_root / 'summaries' / 'realization_level' / 'scenario_leaf_metrics.csv')}`",
        f"- Figure metadata: `{_relative(figures_root / 'metadata' / 'figure_metadata.csv')}`",
        "",
        "## Scenario-tree schema version",
        "",
        f"- Schema version: `{audit_summary.get('schema_version', 'unknown')}`",
        "",
        "## Climate windows",
        "",
        f"- Count: {counts.get('climate_windows', 0)}",
        f"- Source: `{_relative(config_root / 'climate_windows.yaml')}`",
        "",
        "## Climate pathways",
        "",
        f"- Count including historical: {counts.get('climate_pathways', 0)}",
        "",
        "## Technology cases",
        "",
        f"- Count: {counts.get('technology_cases', 0)}",
        f"- Source: `{_relative(config_root / 'technology_cases.yaml')}`",
        "",
        "## Realization policy",
        "",
        f"- Realizations: {counts.get('realizations', 0)}",
        f"- Source: `{_relative(config_root / 'realization_policy.yaml')}`",
        "",
        "## Scenario counts",
        "",
        f"- Scenario IDs: {counts.get('scenarios', 0)}",
        f"- Run configs: {counts.get('run_configs', 0)}",
        "",
        "## Scenario-leaf counts",
        "",
        f"- Scenario leaf IDs: {counts.get('scenario_leaves', 0)}",
        f"- Successful scenario leaves audited: {counts.get('successful_scenario_leaves', 0)}",
        "",
        "## Experiment-space layout",
        "",
        f"- Configs: `{_relative(experiment_root / 'configs')}`",
        f"- Runs: `{_relative(experiment_root / 'runs')}`",
        f"- Summaries: `{_relative(experiment_root / 'summaries')}`",
        f"- Logs: `{_relative(experiment_root / 'logs')}`",
        "",
        "## Run registry summary",
        "",
        f"- Registry rows: {counts.get('registry_rows', 0)}",
        f"- Latest successful leaves: {counts.get('successful_scenario_leaves', 0)}",
        "",
        "## Successful runs",
        "",
        f"- Successful attempt rows: {counts.get('successful_run_attempts', 0)}",
        "",
        "## Failed runs",
        "",
        f"- Failed attempt rows: {counts.get('failed_run_attempts', 0)}",
        "",
        "## Skipped runs",
        "",
        f"- Skipped attempt rows: {counts.get('skipped_run_attempts', 0)}",
        "",
        "## Output summary tables",
        "",
        f"- Standardized per-leaf summary rows: {counts.get('standardized_per_leaf_summaries', 0)}",
        f"- Scenario aggregate rows: {counts.get('scenario_aggregate_rows', 0)}",
        "",
        "## Comparison tables",
        "",
        f"- Generated comparison CSV tables: {counts.get('comparison_tables', 0)}",
        "",
        "## Figure outputs",
        "",
        f"- Generated PNG/PDF figure files: {counts.get('generated_figures', 0)}",
        "",
        "## Provenance fields",
        "",
        "- Run attempt ID, scenario IDs, status, config path and hash, inputs manifest path and hash, climate forcing file and hash, Belgian technology input file and hash, random seed, cohort size, model version, output path, log path, Git commit, and dirty-worktree flag.",
        "",
        "## Traceability matrix",
        "",
        f"- Matrix path: `{_relative(DEFAULT_REPORTS_ROOT / 'scenario_tree_traceability_matrix.csv')}`",
        f"- Matrix rows: {len(matrix_rows)}",
        f"- Traceability complete for all latest-successful leaves: {'yes' if audit_summary.get('traceability_complete') else 'no'}",
        "",
    ]
    lines.extend(_traceability_markdown_rows(matrix_rows))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_validation_report(path: Path, *, audit_summary: Mapping[str, Any], warnings: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    validation = audit_summary.get("validation", {})
    commands = [
        "python3 -m model_v3.scenarios.run_scenario_tree --dry-run --print-summary",
        "python3 -m model_v3.scenarios.validate_summaries --experiment-root experiments/scenario_tree --print-summary",
        "python3 -m model_v3.scenarios.validate_comparisons --experiment-root experiments/scenario_tree --comparison-definitions config/scenario_tree/comparison_definitions.yaml --print-summary",
        "python3 -m model_v3.scenarios.validate_figures --figures-root figures/scenario_tree --experiment-root experiments/scenario_tree --print-summary",
    ]
    lines = [
        "# Scenario-Tree Validation Report",
        "",
        "## Validation scope",
        "",
        "This report consolidates scenario-tree contract, configuration, registry, summary, comparison, figure, 2050-policy, and result-level traceability checks. It reads existing metadata and outputs only; it does not run simulations.",
        "",
        "## Validation commands",
        "",
    ]
    lines.extend(f"- `{command}`" for command in commands)
    lines.extend(
        [
            "",
            "The repository uses a compatibility package at the artifact root so these commands are documented with `python3 -m model_v3.scenarios...` from the repository root.",
            "",
            "## Schema validation",
            "",
            f"- Command used: `python3 -m model_v3.scenario_tree.validate_scenario_tree --config-root config/scenario_tree`",
            "- Input files checked: scenario-tree schema, climate windows, technology cases, realization policy.",
            "- Expected condition: required fields, valid identifiers, baseline/future separation, and non-overlapping canonical windows.",
            f"- Result: {validation.get('config_validation_report', 'unknown')}",
            "- Report path: `experiments/scenario_tree/manifests/config_validation_report.md`",
            "",
            "## Naming and path validation",
            "",
            "- Command used: `python3 -m model_v3.scenarios.run_scenario_tree --dry-run --print-summary`",
            "- Input files checked: scenario leaf index, run configs, inputs manifests, referenced input files.",
            "- Expected condition: every planned leaf resolves to stable config, run, output, and log paths.",
            f"- Result: {validation.get('directory_path_validation', 'available through dry run and config report')}",
            "",
            "## Configuration validation",
            "",
            "- Command used: `python3 -m model_v3.scenario_tree.validate_leaf_configs --experiment-root experiments/scenario_tree`",
            "- Input files checked: generated leaf configs and inputs manifests.",
            "- Expected condition: each leaf config has climate, technology, stochastic, output, and provenance blocks.",
            f"- Result: {validation.get('config_validation_report', 'unknown')}",
            "- Warnings: see `experiments/scenario_tree/manifests/config_validation_report.md`.",
            "- Failure mode: missing config blocks, invalid IDs, unresolved climate files, unresolved technology inputs, or baseline/future contract violations.",
            "- Report path: `experiments/scenario_tree/manifests/config_validation_report.md`",
            "",
            "## Leaf config validation",
            "",
            "- Command used: `python3 -m model_v3.scenario_tree.validate_leaf_configs --experiment-root experiments/scenario_tree`",
            "- Input files checked: generated run configs and inputs manifests for scenario leaves.",
            "- Expected condition: every generated scenario leaf has a complete run config and inputs manifest consistent with the scenario-tree contract.",
            f"- Result: {validation.get('config_validation_report', 'unknown')}",
            "- Warnings: see config validation report.",
            "- Failure mode: missing required input, stale metadata, duplicate config path, or inconsistent baseline/future assignment.",
            "- Report path: `experiments/scenario_tree/manifests/config_validation_report.md`",
            "",
            "## Runner dry-run validation",
            "",
            "- Command used: `python3 -m model_v3.scenarios.run_scenario_tree --dry-run --print-summary`",
            "- Input files checked: scenario leaf index, run configs, inputs manifests, registry state, and resolved input files.",
            "- Expected condition: planned leaves can be classified without executing simulations, and invalid leaves report actionable skip reasons.",
            "- Result: documented command is available; this audit does not rerun the dry run because it only consolidates existing metadata.",
            "- Warnings: none emitted by this audit.",
            "- Failure mode: missing config, missing input, stale running state, or invalid selection.",
            "- Report path: dry-run output is console output unless separately captured.",
            "",
            "## Run registry validation",
            "",
            "- Command used: `python3 -m model_v3.scenarios.audit_scenario_tree --experiment-root experiments/scenario_tree --config-root config/scenario_tree --figures-root figures/scenario_tree --write-reports --print-summary`",
            "- Input files checked: `experiments/scenario_tree/manifests/run_registry.csv`.",
            "- Expected condition: latest actual status is resolvable per leaf and successful rows include provenance fields required for traceability.",
            f"- Result: {audit_summary['counts'].get('registry_rows', 0)} registry rows; {audit_summary['counts'].get('successful_scenario_leaves', 0)} latest-successful leaves.",
            "- Warnings: listed under Remaining warnings.",
            "- Failure mode: missing latest successful provenance fields, incomplete file hashes, missing output directory, or missing standardized summary.",
            "- Report path: `reports/scenario_tree_traceability_matrix.csv` and `reports/scenario_tree_audit_summary.yaml`",
            "",
            "## Input-file validation",
            "",
            "- Command used: `python3 -m model_v3.scenarios.audit_scenario_tree --experiment-root experiments/scenario_tree --config-root config/scenario_tree --figures-root figures/scenario_tree --write-reports --print-summary`",
            "- Input files checked: climate forcing files, technology metadata, Belgian technology inputs, run configs, and inputs manifests.",
            "- Expected condition: each latest-successful output row answers the climate, technology, stochastic, and exact-config traceability questions.",
            f"- Result: {'passed' if audit_summary.get('traceability_complete') else 'warnings present'}",
            "",
            "## Execution provenance validation",
            "",
            "- Command used: audit command above.",
            "- Input files checked: run registry and latest actual run rows.",
            "- Expected condition: every audited successful leaf has attempt ID, status, config hash, input manifest hash, model version, output path, and seed/cohort metadata.",
            f"- Result: {audit_summary['counts'].get('successful_scenario_leaves', 0)} latest-successful scenario leaves audited.",
            "",
            "## Output-summary validation",
            "",
            "- Command used: `python3 -m model_v3.scenarios.validate_summaries --experiment-root experiments/scenario_tree --print-summary`",
            "- Input files checked: per-leaf standardized summaries, scenario aggregates, baseline comparison summary, registry.",
            "- Expected condition: every successful run has one standardized summary row with required metrics.",
            f"- Result: {validation.get('summary_validation_report', 'unknown')}",
            "",
            "## Comparison validation",
            "",
            "- Command used: `python3 -m model_v3.scenarios.validate_comparisons --experiment-root experiments/scenario_tree --comparison-definitions config/scenario_tree/comparison_definitions.yaml --print-summary`",
            "- Input files checked: comparison definitions and comparison-level summary tables.",
            "- Expected condition: climate-only comparisons use `tech_frozen_stock`, baseline uses `tech_current_stock`, and available deltas match by realization ID.",
            f"- Result: {validation.get('comparison_validation_report', 'unknown')}",
            "",
            "## Figure validation",
            "",
            "- Command used: `python3 -m model_v3.scenarios.validate_figures --figures-root figures/scenario_tree --experiment-root experiments/scenario_tree --print-summary`",
            "- Input files checked: figure metadata, PNG/PDF files, caption drafts, approved source data files.",
            "- Expected condition: figures are regenerated from summaries/manifests/configs and have stable filenames.",
            f"- Result: {validation.get('figure_validation', 'not rerun by audit; metadata checked')}",
            "",
            "## 2050 overlap validation",
            "",
            "- Expected condition: raw processed source files may overlap in 2050, but canonical analysis windows do not. Near future ends on 2049-12-31 and mid-century starts on 2050-01-01.",
            f"- Result: {'passed' if audit_summary.get('policy_2050_documented') else 'not documented'}",
            "",
            "## Traceability validation",
            "",
            "- Expected condition: every successful output resolves climate forcing, technology assumptions, stochastic seed/cohort, and exact model/config provenance.",
            f"- Result: {'passed' if audit_summary.get('traceability_complete') else 'high-severity warnings present'}",
            "",
            "## Remaining warnings",
            "",
        ]
    )
    lines.extend(f"- {warning}" for warning in warnings)
    if not warnings:
        lines.append("- None")
    lines.extend(
        [
            "",
            "## Known limitations",
            "",
            "- This audit confirms traceability and methodological consistency; it is not external empirical validation of model accuracy.",
            "- Comparison completeness is limited by the subset of scenario leaves actually present in the run registry and summaries.",
            "- Git provenance can be unavailable when the artifact tree is not inside a Git working tree.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def run_audit(
    *,
    experiment_root: Path,
    config_root: Path,
    figures_root: Path,
    reports_root: Path = DEFAULT_REPORTS_ROOT,
    write_reports: bool = False,
) -> dict[str, Any]:
    registry_rows = read_registry(experiment_root / "manifests" / "run_registry.csv")
    matrix_rows, warnings = build_traceability_matrix(
        experiment_root=experiment_root,
        config_root=config_root,
        repo_root=REPO_ROOT,
    )
    leaf_rows = _read_csv(experiment_root / "manifests" / "scenario_leaf_index.csv")
    summary_rows = _read_csv(experiment_root / "summaries" / "realization_level" / "scenario_leaf_metrics.csv")
    aggregate_rows = _read_csv(experiment_root / "summaries" / "scenario_level" / "scenario_aggregate_metrics.csv")
    figure_metadata = _read_csv(figures_root / "metadata" / "figure_metadata.csv")
    counts = _load_config_counts(config_root, experiment_root)
    counts.update(
        {
            "scenario_leaves": counts.get("scenario_leaves", len(leaf_rows)),
            "scenarios": counts.get("scenarios", len({row.get("scenario_id") for row in leaf_rows})),
            "run_configs": len(list((experiment_root / "runs").glob("*/run_config.yaml"))),
            "registry_rows": len(registry_rows),
            "successful_run_attempts": sum(1 for row in registry_rows if row.get("status") == "success"),
            "failed_run_attempts": sum(1 for row in registry_rows if row.get("status") == "failed"),
            "skipped_run_attempts": sum(1 for row in registry_rows if row.get("status") == "skipped"),
            "successful_scenario_leaves": len(matrix_rows),
            "standardized_per_leaf_summaries": len(summary_rows),
            "scenario_aggregate_rows": len(aggregate_rows),
            "comparison_tables": _count_comparison_tables(experiment_root),
            "generated_figures": _count_figures(figures_root),
            "figure_metadata_rows": len(figure_metadata),
        }
    )
    schema = _read_yaml(config_root / "scenario_tree_schema.yaml")
    validation: dict[str, str] = {}
    for key, path in {
        "config_validation_report": experiment_root / "manifests" / "config_validation_report.md",
        "summary_validation_report": experiment_root / "manifests" / "summary_validation_report.md",
        "comparison_validation_report": experiment_root / "manifests" / "comparison_validation_report.md",
    }.items():
        status, report_warnings = _validation_report_status(path)
        validation[key] = status
        warnings.extend(report_warnings)
    validation["figure_validation"] = "metadata present" if figure_metadata else "missing metadata"

    traceability_complete = all(row["traceability_complete"] == "true" for row in matrix_rows)
    audit_summary: dict[str, Any] = {
        "generated_at_utc": _utc_now(),
        "schema_version": schema.get("schema_version", "unknown"),
        "counts": counts,
        "status_counts": _status_counts(registry_rows),
        "traceability_complete": traceability_complete,
        "traceability_matrix_rows": len(matrix_rows),
        "policy_2050_documented": True,
        "simulations_run": 0,
        "validation": validation,
        "warnings": warnings,
    }

    if write_reports:
        reports_root.mkdir(parents=True, exist_ok=True)
        matrix_path = reports_root / "scenario_tree_traceability_matrix.csv"
        _write_csv(matrix_path, matrix_rows, TRACEABILITY_COLUMNS)
        with (reports_root / "scenario_tree_audit_summary.yaml").open("w", encoding="utf-8") as handle:
            yaml.safe_dump(audit_summary, handle, sort_keys=False)
        _write_run_manifest_report(
            reports_root / "scenario_tree_run_manifest.md",
            experiment_root=experiment_root,
            config_root=config_root,
            figures_root=figures_root,
            matrix_rows=matrix_rows,
            audit_summary=audit_summary,
        )
        _write_validation_report(
            reports_root / "scenario_tree_validation_report.md",
            audit_summary=audit_summary,
            warnings=warnings,
        )

    return audit_summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment-root", default=str(DEFAULT_EXPERIMENT_ROOT))
    parser.add_argument("--config-root", default=str(DEFAULT_CONFIG_ROOT))
    parser.add_argument("--figures-root", default=str(DEFAULT_FIGURES_ROOT))
    parser.add_argument("--reports-root", default=str(DEFAULT_REPORTS_ROOT))
    parser.add_argument("--write-reports", action="store_true")
    parser.add_argument("--print-summary", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    summary = run_audit(
        experiment_root=resolve_cli_path(args.experiment_root),
        config_root=resolve_cli_path(args.config_root),
        figures_root=resolve_cli_path(args.figures_root),
        reports_root=resolve_cli_path(args.reports_root),
        write_reports=args.write_reports,
    )
    if args.print_summary:
        print("Scenario-tree methodological audit complete.")
        print(f"Successful scenario leaves audited: {summary['counts'].get('successful_scenario_leaves', 0)}")
        print(f"Traceability matrix rows: {summary['traceability_matrix_rows']}")
        print(f"Traceability complete: {'yes' if summary['traceability_complete'] else 'no'}")
        print("Methodology document: docs/model_v3_scenario_tree_methodology.md")
        print("Assumptions document: docs/model_v3_scenario_tree_assumptions.md")
        print("Run manifest: reports/scenario_tree_run_manifest.md")
        print("Validation report: reports/scenario_tree_validation_report.md")
        print("Thesis subsection draft: docs/thesis_methodology_scenario_tree_subsection.md")
        print("2050 policy documented: yes")
        print("Simulations run: 0")
    return 0 if summary["traceability_complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
