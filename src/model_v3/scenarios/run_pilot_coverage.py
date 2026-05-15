"""Run a small fixed-seed pilot coverage set for scenario-tree leaves.

This utility selects the first N realization leaves per deterministic scenario
group. A group is the combination already encoded by ``scenario_id``:
climate window, climate pathway, and technology case.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Iterable

from model_v3.scenarios.registry import read_registry, registry_path, status_counts
from model_v3.scenarios.run_scenario_tree import (
    DEFAULT_BASE_MODEL_CONFIG,
    DEFAULT_EXPERIMENT_ROOT,
    DEFAULT_LEAF_INDEX,
    REPO_ROOT,
    execute_leaf,
)
from model_v3.scenarios.selection import ScenarioLeafRecord, load_leaf_records


def _resolve_cli_path(path_text: str | Path) -> Path:
    path = Path(path_text)
    if path.is_absolute():
        return path
    cwd_candidate = (Path.cwd() / path).resolve()
    if cwd_candidate.exists():
        return cwd_candidate
    return REPO_ROOT / path


def _matches_filter(value: str, allowed: set[str]) -> bool:
    return not allowed or value in allowed


def select_pilot_records(
    records: Iterable[ScenarioLeafRecord],
    *,
    seeds_per_scenario: int,
    climate_window_ids: set[str],
    climate_pathway_ids: set[str],
    technology_case_ids: set[str],
    include_baseline: bool,
) -> list[ScenarioLeafRecord]:
    """Return sorted pilot leaves, with at most N realizations per scenario."""

    grouped: dict[str, list[ScenarioLeafRecord]] = {}
    for record in records:
        is_baseline = record.climate_pathway_id == "historical"
        if is_baseline and not include_baseline:
            continue
        if not _matches_filter(record.climate_window_id, climate_window_ids):
            continue
        if not _matches_filter(record.climate_pathway_id, climate_pathway_ids):
            continue
        if not _matches_filter(record.technology_case_id, technology_case_ids):
            continue
        grouped.setdefault(record.scenario_id, []).append(record)

    selected: list[ScenarioLeafRecord] = []
    for scenario_id in sorted(grouped):
        selected.extend(
            sorted(grouped[scenario_id], key=lambda record: record.realization_id)[: max(int(seeds_per_scenario), 0)]
        )
    return selected


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment-root", type=Path, default=DEFAULT_EXPERIMENT_ROOT)
    parser.add_argument("--leaf-index", type=Path, default=DEFAULT_LEAF_INDEX)
    parser.add_argument("--base-model-config", type=Path, default=DEFAULT_BASE_MODEL_CONFIG)
    parser.add_argument("--seeds-per-scenario", type=int, default=5)
    parser.add_argument("--climate-window-id", action="append", default=[])
    parser.add_argument("--climate-pathway-id", action="append", default=[])
    parser.add_argument("--technology-case-id", action="append", default=[])
    parser.add_argument("--include-baseline", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--continue-on-error", action="store_true")
    parser.add_argument("--ignore-stale-running", action="store_true")
    parser.add_argument("--estimate-seconds-per-leaf", type=float, default=None)
    parser.add_argument("--print-summary", action="store_true")
    return parser


def _print_selection(records: list[ScenarioLeafRecord], *, estimate_seconds_per_leaf: float | None) -> None:
    for record in records:
        print(record.scenario_leaf_id)
    print("Pilot coverage selection summary:")
    print(f"- selected leaves: {len(records)}")
    print(f"- scenario groups: {len({record.scenario_id for record in records})}")
    if estimate_seconds_per_leaf is not None:
        total_seconds = max(float(estimate_seconds_per_leaf), 0.0) * len(records)
        print(f"- estimated serial runtime hours: {total_seconds / 3600.0:.2f}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    experiment_root = _resolve_cli_path(args.experiment_root)
    leaf_index = _resolve_cli_path(args.leaf_index)
    base_model_config_path = _resolve_cli_path(args.base_model_config)
    records = load_leaf_records(leaf_index)
    selected = select_pilot_records(
        records,
        seeds_per_scenario=args.seeds_per_scenario,
        climate_window_ids=set(args.climate_window_id),
        climate_pathway_ids=set(args.climate_pathway_id),
        technology_case_ids=set(args.technology_case_id),
        include_baseline=bool(args.include_baseline),
    )

    if args.dry_run:
        _print_selection(selected, estimate_seconds_per_leaf=args.estimate_seconds_per_leaf)
        return 0

    reg_path = registry_path(experiment_root)
    results: list[dict[str, Any]] = []
    exit_code = 0
    for record in selected:
        result = execute_leaf(
            record,
            experiment_root=experiment_root,
            registry_file=reg_path,
            force=args.force,
            ignore_stale_running=args.ignore_stale_running,
            base_model_config_path=base_model_config_path,
            repo_root=REPO_ROOT,
        )
        results.append(result)
        status = result.get("status")
        print(f"{record.scenario_leaf_id}\t{status}")
        if status == "failed":
            exit_code = 1
            if not args.continue_on_error:
                break

    if args.print_summary:
        counts: dict[str, int] = {}
        for result in results:
            key = str(result.get("status", "unknown"))
            counts[key] = counts.get(key, 0) + 1
        print("Pilot coverage execution summary:")
        for key in sorted(counts):
            print(f"- {key}: {counts[key]}")
        print(f"Registry: {reg_path}")
        print(f"Registry status counts: {status_counts(read_registry(reg_path))}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
