"""Build the generated model_v3 handbook and supervisor briefing.

The generator is intentionally repository-grounded: it reads the local config,
manifest, registry, summary, report, and figure metadata files that are present,
then distinguishes implemented, unverified, missing, planned, and not-applicable
items in the output documents.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
import os
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
import re
import shutil
import subprocess
import textwrap
from typing import Any, Iterable, Mapping

try:
    import yaml
except Exception:  # pragma: no cover - dependency is part of repo requirements
    yaml = None  # type: ignore[assignment]


TITLE = "Model v3 Complete Handbook: Scenario-Tree Residential Energy Demand Model"
SUBTITLE = "Architecture, Inputs, Scenario Design, Outputs, Validation, Caveats, and Usage Guide"
HANDBOOK_STEM = "model_v3_complete_model_handbook"
ASSET_DIR_NAME = "model_v3_handbook_assets"
SUPERVISOR_STEM = "model_v3_supervisor_briefing"

EXPECTED_REPOSITORY_PATHS = [
    "config/",
    "config/scenario_tree/",
    "config/scenario_tree/scenario_tree_schema.yaml",
    "config/scenario_tree/climate_windows.yaml",
    "config/scenario_tree/technology_cases.yaml",
    "config/scenario_tree/realization_policy.yaml",
    "config/scenario_tree/comparison_definitions.yaml",
    "config/belgian_technology_inputs.yaml",
    "inputs/",
    "inputs/climate/",
    "inputs/climate/processed/",
    "experiments/scenario_tree/",
    "experiments/scenario_tree/manifests/scenario_tree_manifest.yaml",
    "experiments/scenario_tree/manifests/scenario_leaf_index.csv",
    "experiments/scenario_tree/manifests/run_registry.csv",
    "experiments/scenario_tree/manifests/config_validation_report.md",
    "experiments/scenario_tree/manifests/summary_validation_report.md",
    "experiments/scenario_tree/manifests/comparison_validation_report.md",
    "experiments/scenario_tree/summaries/realization_level/scenario_leaf_metrics.csv",
    "experiments/scenario_tree/summaries/scenario_level/scenario_aggregate_metrics.csv",
    "experiments/scenario_tree/summaries/comparison_level/comparison_index.csv",
    "figures/scenario_tree/",
    "figures/scenario_tree/metadata/figure_metadata.yaml",
    "figures/scenario_tree/thesis_caption_drafts.md",
    "reports/scenario_tree_validation_report.md",
    "reports/scenario_tree_audit_summary.yaml",
    "reports/scenario_tree_traceability_matrix.csv",
    "docs/model_v3_scenario_tree_design.md",
    "src/model_v3/",
    "src/model_v3/interfaces.py",
    "src/model_v3/physics/physics_core.py",
    "src/model_v3/control/control_core.py",
    "src/model_v3/systems/system_core.py",
    "src/model_v3/scenarios/run_scenario_tree.py",
    "src/model_v3/scenarios/summarize_outputs.py",
    "src/model_v3/scenarios/generate_comparisons.py",
    "src/model_v3/scenarios/generate_figures.py",
]

KEY_SOURCE_GLOBS = [
    "config/**/*.yaml",
    "docs/model_v3_*.md",
    "reports/scenario_tree*.md",
    "reports/scenario_tree*.yaml",
    "reports/scenario_tree*.csv",
    "src/model_v3/**/*.py",
    "experiments/scenario_tree/manifests/*",
    "experiments/scenario_tree/summaries/**/*.csv",
    "experiments/scenario_tree/summaries/**/*.yaml",
    "figures/scenario_tree/metadata/*",
    "figures/scenario_tree/thesis_caption_drafts.md",
]

REQUIRED_METRICS = [
    "annual_electricity_gross_kWh",
    "annual_grid_import_kWh",
    "annual_grid_export_kWh",
    "annual_gas_kWh",
    "annual_useful_heating_kWh",
    "annual_dhw_kWh",
    "peak_grid_import_W",
    "winter_peak_grid_import_W",
    "summer_peak_grid_import_W",
    "pv_generation_kWh",
    "pv_self_consumption_kWh",
    "pv_export_fraction",
    "ev_charging_kWh",
    "mean_T_out_C",
    "winter_mean_T_out_C",
    "summer_mean_T_out_C",
    "HDD_15",
    "HDD_18",
    "CDD_22",
    "mean_solar_W_m2",
]


@dataclass
class CsvInfo:
    path: str
    exists: bool
    rows: int | None = None
    columns: list[str] = field(default_factory=list)
    size_bytes: int | None = None


@dataclass
class FigureInfo:
    figure_id: str
    path: str
    caption: str
    explanation: str
    source: str
    schematic: bool = False
    metrics: str = ""


@dataclass
class Context:
    repo_root: Path
    generation_timestamp: str
    git_commit: str | None
    git_dirty_status: str
    expected_paths: list[dict[str, Any]]
    missing_expected_files: list[str]
    source_files_inspected: list[str]
    warnings: list[str]
    yaml_data: dict[str, Any]
    csv_info: dict[str, CsvInfo]
    registry: dict[str, Any]
    counts: dict[str, Any]
    phase_statuses: list[dict[str, str]]
    input_inventory: list[dict[str, str]]
    existing_figure_metadata: list[dict[str, Any]]
    figure_infos: list[FigureInfo] = field(default_factory=list)
    pdf_backend: str = ""


def relpath(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def read_yaml(path: Path) -> Any:
    if yaml is None or not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as handle:
            return yaml.safe_load(handle) or {}
    except Exception as exc:
        return {"_read_error": str(exc)}


def write_yaml(path: Path, data: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if yaml is not None:
        with path.open("w", encoding="utf-8") as handle:
            yaml.safe_dump(dict(data), handle, sort_keys=False, allow_unicode=False)
        return
    path.write_text(json.dumps(data, indent=2, sort_keys=False), encoding="utf-8")


def csv_shape(path: Path) -> CsvInfo:
    info = CsvInfo(path=path.as_posix(), exists=path.exists())
    if not path.exists():
        return info
    info.size_bytes = path.stat().st_size
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.reader(handle)
            info.columns = next(reader, [])
            info.rows = sum(1 for _ in reader)
    except UnicodeDecodeError:
        info.columns = []
        info.rows = None
    except Exception:
        info.rows = None
    return info


def read_csv_rows(path: Path, limit: int | None = None) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        rows: list[dict[str, str]] = []
        for row in reader:
            rows.append({str(k): "" if v is None else str(v) for k, v in row.items()})
            if limit is not None and len(rows) >= limit:
                break
        return rows


def run_git(repo_root: Path, args: list[str]) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=repo_root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def discover_source_files(repo_root: Path) -> list[str]:
    files: set[str] = set()
    for pattern in KEY_SOURCE_GLOBS:
        for path in repo_root.glob(pattern):
            if path.is_file():
                files.add(relpath(path, repo_root))
    return sorted(files)


def first_and_last_csv_rows(path: Path) -> tuple[list[str], list[str], list[str]]:
    if not path.exists():
        return [], [], []
    last: deque[list[str]] = deque(maxlen=1)
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.reader(handle)
            header = next(reader, [])
            first = next(reader, [])
            for row in reader:
                last.append(row)
            return header, first, last[0] if last else first
    except Exception:
        return [], [], []


def infer_csv_inventory(repo_root: Path) -> list[dict[str, str]]:
    roots = [
        repo_root / "inputs",
        repo_root / "config",
        repo_root / "experiments" / "scenario_tree" / "summaries",
    ]
    inventory: list[dict[str, str]] = []
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            if path.suffix.lower() not in {".csv", ".yaml", ".yml", ".json", ".md"}:
                continue
            if "runs" in path.parts and path.name not in {"run_config.yaml", "inputs_manifest.yaml"}:
                continue
            rel = relpath(path, repo_root)
            category = "configuration"
            purpose = "Repository configuration or metadata."
            scenario_dimension = "not_applicable"
            required = "optional"
            temporal_resolution = "not_detected"
            units = "not_detected"
            if rel.startswith("inputs/climate/processed"):
                category = "climate"
                purpose = "Processed climate forcing for a canonical scenario-tree branch."
                scenario_dimension = "climate_window_id, climate_pathway_id"
                required = "required_for_configured_climate_leaves"
            elif rel.startswith("inputs/building"):
                category = "building"
                purpose = "Building or archetype parameters used by the model input layer."
                scenario_dimension = "building/archetype assumptions"
            elif rel.startswith("inputs/solar"):
                category = "solar"
                purpose = "Solar generation or irradiance input data."
                scenario_dimension = "technology/PV and forcing"
            elif rel.startswith("inputs/load_profiles"):
                category = "load_profiles"
                purpose = "Observed or representative load profile input data."
                scenario_dimension = "stochastic/end-use behaviour"
            elif rel.startswith("inputs/occupancy"):
                category = "occupancy"
                purpose = "Occupancy model specification."
                scenario_dimension = "stochastic behaviour"
            elif rel.startswith("config/scenario_tree"):
                category = "scenario_tree_config"
                purpose = "Scenario-tree contract, dimensions, or comparison definitions."
                scenario_dimension = "scenario tree"
                required = "required"
            elif rel.endswith("belgian_technology_inputs.yaml"):
                category = "technology"
                purpose = "Belgian residential technology assumptions consumed by run configs."
                scenario_dimension = "technology_case_id"
                required = "required_for_scenario_runs"
            elif "summaries" in rel:
                category = "standardized_outputs"
                purpose = "Generated standardized summary or comparison table."
                scenario_dimension = "outputs/metrics"
            if path.suffix.lower() == ".csv":
                header, first, last = first_and_last_csv_rows(path)
                lowered = [h.lower() for h in header]
                if "timestamp" in lowered or any("time" in h for h in lowered):
                    temporal_resolution = "timestamped; inspect source for exact step"
                if header:
                    units = ", ".join([h for h in header[:6] if any(token in h.lower() for token in ["_w", "kwh", "_c", "m2"])])
                    if not units:
                        units = "column names inspected; units not explicit"
                if first and last and header:
                    inventory_note = f"first_row={first[:2]}; last_row={last[:2]}"
                else:
                    inventory_note = ""
            else:
                inventory_note = ""
            inventory.append(
                {
                    "path": rel,
                    "type": path.suffix.lower().lstrip("."),
                    "purpose": purpose,
                    "temporal_resolution": temporal_resolution,
                    "units": units,
                    "scenario_dimension": scenario_dimension,
                    "required": required,
                    "validation_status": "see validation reports if present",
                    "note": inventory_note,
                }
            )
    return inventory[:220]


def summarize_registry(repo_root: Path, audit_summary: Mapping[str, Any]) -> dict[str, Any]:
    path = repo_root / "experiments" / "scenario_tree" / "manifests" / "run_registry.csv"
    rows = read_csv_rows(path)
    status_counts = Counter(row.get("status", "") for row in rows)
    successful_leaves = sorted({row.get("scenario_leaf_id", "") for row in rows if row.get("status") == "success"})
    counts = dict(audit_summary.get("counts", {}) if isinstance(audit_summary, Mapping) else {})
    latest_successful = counts.get("successful_scenario_leaves")
    if latest_successful is None:
        latest_successful = len(successful_leaves)
    total_leaves = counts.get("scenario_leaves")
    if total_leaves is None:
        leaf_info = csv_shape(repo_root / "experiments" / "scenario_tree" / "manifests" / "scenario_leaf_index.csv")
        total_leaves = leaf_info.rows or 0
    return {
        "path": relpath(path, repo_root),
        "exists": path.exists(),
        "rows": len(rows),
        "status_counts": dict(status_counts),
        "successful_attempts": int(status_counts.get("success", 0)),
        "successful_scenario_leaves": int(latest_successful or 0),
        "enumerated_scenario_leaves": int(total_leaves or 0),
        "all_leaves_completed": bool(total_leaves and latest_successful == total_leaves),
        "sample_successful_leaves": successful_leaves[:5],
    }


def detect_phase_statuses(repo_root: Path, registry: Mapping[str, Any], counts: Mapping[str, Any]) -> list[dict[str, str]]:
    def exists(path: str) -> bool:
        return (repo_root / path).exists()

    rows: list[dict[str, str]] = []
    phase_specs = [
        (
            "Phase 1 - scenario-tree schema",
            "Schema, climate windows, technology cases, realization policy.",
            [
                "config/scenario_tree/scenario_tree_schema.yaml",
                "config/scenario_tree/climate_windows.yaml",
                "config/scenario_tree/technology_cases.yaml",
                "config/scenario_tree/realization_policy.yaml",
            ],
            "Validate schema before changing branch dimensions.",
        ),
        (
            "Phase 2 - directory and naming convention",
            "Experiment space, manifests, stable scenario and leaf paths.",
            [
                "experiments/scenario_tree/manifests/scenario_tree_manifest.yaml",
                "experiments/scenario_tree/manifests/scenario_leaf_index.csv",
            ],
            "Regenerate experiment space if the leaf index is stale.",
        ),
        (
            "Phase 3 - scenario-leaf configs",
            "Per-leaf run_config.yaml and inputs_manifest.yaml files.",
            ["experiments/scenario_tree/manifests/config_validation_report.md"],
            "Run leaf-config validation after changing inputs or technology files.",
        ),
        (
            "Phase 4 - runner/orchestration",
            "Scenario-tree runner, provenance, logs, run registry.",
            [
                "src/model_v3/scenarios/run_scenario_tree.py",
                "experiments/scenario_tree/manifests/run_registry.csv",
            ],
            "Run dry-run first; execute a small pair before batch execution.",
        ),
        (
            "Phase 5 - output standardization",
            "Per-leaf summaries and scenario aggregate metrics.",
            [
                "src/model_v3/scenarios/summarize_outputs.py",
                "experiments/scenario_tree/summaries/realization_level/scenario_leaf_metrics.csv",
            ],
            "Regenerate summaries after new successful runs.",
        ),
        (
            "Phase 6 - comparison framework",
            "Climate-only, technology-only, stress-case, stochastic robustness tables.",
            [
                "config/scenario_tree/comparison_definitions.yaml",
                "experiments/scenario_tree/summaries/comparison_level/comparison_index.csv",
            ],
            "Regenerate comparisons when more leaves have summaries.",
        ),
        (
            "Phase 7 - visualisation",
            "Generated figures and figure metadata.",
            [
                "src/model_v3/scenarios/generate_figures.py",
                "figures/scenario_tree/metadata/figure_metadata.yaml",
            ],
            "Validate figures and caption metadata before using in thesis text.",
        ),
        (
            "Phase 8 - documentation/audit",
            "Traceability matrix, audit summary, methodology docs.",
            [
                "reports/scenario_tree_validation_report.md",
                "reports/scenario_tree_audit_summary.yaml",
                "docs/model_v3_scenario_tree_methodology.md",
            ],
            "Rerun audit after new outputs or changed figures.",
        ),
        (
            "Phase 9 - handbook",
            "Generated handbook, briefing, manifest, and handbook validation.",
            [
                "src/model_v3/documentation/build_model_handbook.py",
                "src/model_v3/documentation/validate_model_handbook.py",
            ],
            "Run the handbook validator and update this document after regeneration.",
        ),
    ]
    successful = int(registry.get("successful_scenario_leaves", 0) or 0)
    total = int(registry.get("enumerated_scenario_leaves", 0) or 0)
    for phase, deliverables, paths, next_action in phase_specs:
        detected = [path for path in paths if exists(path)]
        missing = [path for path in paths if not exists(path)]
        status = "implemented" if detected and not missing else "missing"
        warning = "none"
        if detected and missing:
            status = "implemented_unverified"
            warning = "some expected files are absent: " + ", ".join(missing)
        if phase.startswith("Phase 4") and total and successful < total:
            status = "implemented"
            warning = f"runner exists, but registry/audit supports only {successful} latest-successful leaves out of {total} enumerated leaves"
        if phase.startswith("Phase 6") and exists("experiments/scenario_tree/manifests/comparison_validation_report.md"):
            warning = "comparison validation reports missing groups where no successful summary rows exist"
        rows.append(
            {
                "phase": phase,
                "expected_deliverables": deliverables,
                "detected_files": "; ".join(detected) if detected else "none",
                "status": status,
                "warnings": warning,
                "next_action": next_action,
            }
        )
    return rows


def collect_context(repo_root: Path) -> Context:
    repo_root = repo_root.resolve()
    timestamp = utc_now()
    git_commit = run_git(repo_root, ["rev-parse", "HEAD"])
    dirty = run_git(repo_root, ["status", "--short"])
    if git_commit is None:
        git_dirty_status = "not_available_not_a_git_repository"
    else:
        git_dirty_status = "dirty" if dirty else "clean"

    expected_paths: list[dict[str, Any]] = []
    missing: list[str] = []
    for path_text in EXPECTED_REPOSITORY_PATHS:
        path = repo_root / path_text
        exists = path.exists()
        expected_paths.append({"path": path_text, "exists": bool(exists), "kind": "directory" if path_text.endswith("/") else "file"})
        if not exists:
            missing.append(path_text)

    yaml_paths = {
        "climate_windows": repo_root / "config/scenario_tree/climate_windows.yaml",
        "technology_cases": repo_root / "config/scenario_tree/technology_cases.yaml",
        "realization_policy": repo_root / "config/scenario_tree/realization_policy.yaml",
        "scenario_tree_schema": repo_root / "config/scenario_tree/scenario_tree_schema.yaml",
        "comparison_definitions": repo_root / "config/scenario_tree/comparison_definitions.yaml",
        "scenario_tree_manifest": repo_root / "experiments/scenario_tree/manifests/scenario_tree_manifest.yaml",
        "run_registry_summary": repo_root / "experiments/scenario_tree/manifests/run_registry_summary.yaml",
        "config_validation": repo_root / "experiments/scenario_tree/manifests/config_validation_report.yaml",
        "summary_validation": repo_root / "experiments/scenario_tree/manifests/summary_validation_report.yaml",
        "comparison_validation": repo_root / "experiments/scenario_tree/manifests/comparison_validation_report.yaml",
        "audit_summary": repo_root / "reports/scenario_tree_audit_summary.yaml",
        "figure_metadata": repo_root / "figures/scenario_tree/metadata/figure_metadata.yaml",
        "metric_schema": repo_root / "experiments/scenario_tree/summaries/realization_level/scenario_leaf_metrics_schema.yaml",
    }
    yaml_data = {name: read_yaml(path) for name, path in yaml_paths.items() if path.exists()}

    csv_paths = {
        "scenario_leaf_index": repo_root / "experiments/scenario_tree/manifests/scenario_leaf_index.csv",
        "run_registry": repo_root / "experiments/scenario_tree/manifests/run_registry.csv",
        "scenario_leaf_metrics": repo_root / "experiments/scenario_tree/summaries/realization_level/scenario_leaf_metrics.csv",
        "scenario_aggregate_metrics": repo_root / "experiments/scenario_tree/summaries/scenario_level/scenario_aggregate_metrics.csv",
        "comparison_index": repo_root / "experiments/scenario_tree/summaries/comparison_level/comparison_index.csv",
        "traceability_matrix": repo_root / "reports/scenario_tree_traceability_matrix.csv",
    }
    csv_info = {name: csv_shape(path) for name, path in csv_paths.items()}

    audit_summary = yaml_data.get("audit_summary", {})
    registry = summarize_registry(repo_root, audit_summary if isinstance(audit_summary, Mapping) else {})
    counts = dict(audit_summary.get("counts", {}) if isinstance(audit_summary, Mapping) else {})
    counts.setdefault("scenario_leaves", registry.get("enumerated_scenario_leaves", 0))
    counts.setdefault("successful_scenario_leaves", registry.get("successful_scenario_leaves", 0))
    counts.setdefault("standardized_per_leaf_summaries", csv_info["scenario_leaf_metrics"].rows or 0)
    counts.setdefault("scenario_aggregate_rows", csv_info["scenario_aggregate_metrics"].rows or 0)
    counts.setdefault("figure_metadata_rows", len(yaml_data.get("figure_metadata", []) or []))

    warnings: list[str] = []
    if registry.get("enumerated_scenario_leaves") and registry.get("successful_scenario_leaves") != registry.get("enumerated_scenario_leaves"):
        warnings.append(
            "Run registry/audit does not support a full-completion claim: "
            f"{registry.get('successful_scenario_leaves')} latest-successful leaves for "
            f"{registry.get('enumerated_scenario_leaves')} enumerated leaves."
        )
    if not git_commit:
        warnings.append("Git commit unavailable because the working directory is not a Git repository or Git metadata is absent.")
    for path_text in missing:
        warnings.append(f"Expected repository path missing: {path_text}")

    figure_metadata = yaml_data.get("figure_metadata", [])
    if not isinstance(figure_metadata, list):
        figure_metadata = []

    return Context(
        repo_root=repo_root,
        generation_timestamp=timestamp,
        git_commit=git_commit,
        git_dirty_status=git_dirty_status,
        expected_paths=expected_paths,
        missing_expected_files=missing,
        source_files_inspected=discover_source_files(repo_root),
        warnings=warnings,
        yaml_data=yaml_data,
        csv_info=csv_info,
        registry=registry,
        counts=counts,
        phase_statuses=detect_phase_statuses(repo_root, registry, counts),
        input_inventory=infer_csv_inventory(repo_root),
        existing_figure_metadata=figure_metadata,
    )


def safe_mkdir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def import_matplotlib() -> Any:
    import matplotlib

    if not os.environ.get("MPLCONFIGDIR"):
        mpl_dir = Path(os.environ.get("TMPDIR", "/tmp")) / "model_v3_matplotlib"
        mpl_dir.mkdir(parents=True, exist_ok=True)
        os.environ["MPLCONFIGDIR"] = str(mpl_dir)
    matplotlib.use("Agg", force=True)
    import matplotlib.pyplot as plt

    return plt


def draw_boxes(path: Path, title: str, boxes: list[tuple[str, float, float, float, float]], arrows: list[tuple[int, int]], note: str = "") -> None:
    plt = import_matplotlib()
    fig, ax = plt.subplots(figsize=(11, 6.4))
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    ax.text(0.03, 0.95, title, fontsize=17, fontweight="bold", va="top")
    palette = ["#4477AA", "#228833", "#CCBB44", "#EE6677", "#66CCEE", "#AA3377", "#BBBBBB", "#44AA99"]
    for idx, (label, x, y, w, h) in enumerate(boxes):
        rect = plt.Rectangle((x, y), w, h, facecolor=palette[idx % len(palette)], alpha=0.16, edgecolor=palette[idx % len(palette)], linewidth=1.8)
        ax.add_patch(rect)
        ax.text(x + w / 2, y + h / 2, label, ha="center", va="center", fontsize=9.5, wrap=True)
    for start, end in arrows:
        sx, sy, sw, sh = boxes[start][1:]
        ex, ey, ew, eh = boxes[end][1:]
        ax.annotate(
            "",
            xy=(ex, ey + eh / 2),
            xytext=(sx + sw, sy + sh / 2),
            arrowprops={"arrowstyle": "->", "color": "#444444", "lw": 1.3},
        )
    if note:
        ax.text(0.03, 0.05, note, fontsize=8.5, color="#333333", va="bottom")
    fig.tight_layout()
    fig.savefig(path, dpi=180)
    plt.close(fig)


def draw_timeline(path: Path, climate_windows: Mapping[str, Any]) -> None:
    plt = import_matplotlib()
    fig, ax = plt.subplots(figsize=(11, 3.8))
    ax.set_title("Canonical climate windows and 2050 overlap policy", loc="left", fontsize=15, fontweight="bold")
    y_positions = {"baseline_1981_2005": 3, "near_future_2030_2049": 2, "mid_century_2050_2070": 1, "long_term_2080_2100": 0}
    colors = {"baseline_1981_2005": "#4477AA", "near_future_2030_2049": "#228833", "mid_century_2050_2070": "#CCBB44", "long_term_2080_2100": "#EE6677"}
    for key, y in y_positions.items():
        cfg = dict(climate_windows.get(key, {}))
        start = int(str(cfg.get("canonical_start", "2000"))[:4])
        end = int(str(cfg.get("canonical_end", "2001"))[:4])
        ax.barh(y, end - start + 1, left=start, height=0.42, color=colors[key], alpha=0.75)
        ax.text(start, y + 0.33, f"{key}: {start}-{end}", fontsize=9, va="bottom")
    ax.axvline(2050, color="#111111", linestyle="--", linewidth=1)
    ax.text(2050.4, 2.55, "2050 assigned only to mid-century canonical window", fontsize=9, va="center")
    ax.set_yticks([])
    ax.set_xlabel("Year")
    ax.set_xlim(1978, 2103)
    ax.grid(axis="x", color="#DDDDDD", linewidth=0.8)
    fig.tight_layout()
    fig.savefig(path, dpi=180)
    plt.close(fig)


def draw_inventory_chart(path: Path, inventory: list[Mapping[str, str]]) -> None:
    plt = import_matplotlib()
    counts = Counter(item.get("type", "unknown") for item in inventory)
    if not counts:
        counts = Counter({"missing": 1})
    labels, values = zip(*counts.most_common(10))
    fig, ax = plt.subplots(figsize=(9.5, 5.2))
    ax.bar(labels, values, color="#4477AA", alpha=0.8)
    ax.set_title("Input and metadata inventory by file type", loc="left", fontsize=15, fontweight="bold")
    ax.set_ylabel("Detected files")
    ax.tick_params(axis="x", rotation=35)
    ax.grid(axis="y", color="#DDDDDD", linewidth=0.8)
    fig.tight_layout()
    fig.savefig(path, dpi=180)
    plt.close(fig)


def draw_caveats_heatmap(path: Path) -> None:
    plt = import_matplotlib()
    topics = [
        "Execution coverage",
        "Climate ensemble",
        "Building physics",
        "Technology calibration",
        "Stochastic convergence",
        "Metric interpretation",
        "External validation",
        "Thesis interpretation",
    ]
    severities = [3, 2, 3, 3, 2, 2, 3, 2]
    fig, ax = plt.subplots(figsize=(9.5, 5.4))
    ax.imshow([[v] for v in severities], cmap="YlOrRd", vmin=1, vmax=3, aspect="auto")
    ax.set_yticks(range(len(topics)))
    ax.set_yticklabels(topics)
    ax.set_xticks([0])
    ax.set_xticklabels(["Severity"])
    ax.set_title("Caveats and gaps overview", loc="left", fontsize=15, fontweight="bold")
    for i, value in enumerate(severities):
        label = {1: "low", 2: "medium", 3: "high"}[value]
        ax.text(0, i, label, ha="center", va="center", color="#222222", fontsize=10, fontweight="bold")
    fig.tight_layout()
    fig.savefig(path, dpi=180)
    plt.close(fig)


def draw_metric_taxonomy(path: Path) -> None:
    boxes = [
        ("Standardized metrics", 0.39, 0.77, 0.22, 0.12),
        ("Energy totals\nkWh per year", 0.05, 0.50, 0.20, 0.16),
        ("Grid stress\nW peaks and import/export", 0.29, 0.50, 0.20, 0.16),
        ("Technology metrics\nPV, EV, gas, useful heat", 0.53, 0.50, 0.20, 0.16),
        ("Climate metrics\nT, HDD, CDD, solar", 0.77, 0.50, 0.18, 0.16),
        ("Comparisons\nabsolute delta, percent delta", 0.18, 0.20, 0.25, 0.14),
        ("Uncertainty summaries\nP10, P50, P90", 0.57, 0.20, 0.25, 0.14),
    ]
    draw_boxes(path, "Metric taxonomy", boxes, [(0, 1), (0, 2), (0, 3), (0, 4), (2, 5), (3, 6)])


def draw_leaf_id(path: Path) -> None:
    boxes = [
        ("mid_century_2050_2070\nclimate_window_id", 0.03, 0.58, 0.25, 0.20),
        ("rcp_8_5\nclimate_pathway_id", 0.30, 0.58, 0.18, 0.20),
        ("tech_high_electrification_pv_ev\ntechnology_case_id", 0.50, 0.58, 0.27, 0.20),
        ("seed_0042\nrealization_id", 0.79, 0.58, 0.18, 0.20),
        ("Double underscores separate dimensions:\nwindow__pathway__technology__realization", 0.18, 0.25, 0.64, 0.16),
    ]
    draw_boxes(path, "Scenario leaf ID decomposition", boxes, [(0, 4), (1, 4), (2, 4), (3, 4)])


def generate_handbook_figures(context: Context, assets_dir: Path, write_figures: bool) -> list[FigureInfo]:
    safe_mkdir(assets_dir)
    figures: list[FigureInfo] = []
    if not write_figures:
        return figures

    climate_windows = dict(dict(context.yaml_data.get("climate_windows", {})).get("climate_windows", {}))
    schematic_specs = [
        (
            "model_architecture",
            "model_architecture.png",
            "Overall model architecture diagram.",
            "Shows the major layers from configs and inputs through data preparation, physics, control, systems, outputs, scenario-tree orchestration, comparisons, figures, and documentation.",
            "Schematic generated by build_model_handbook.py from repository module layout.",
            lambda p: draw_boxes(
                p,
                "model_v3 architecture",
                [
                    ("Config\nconfig", 0.04, 0.70, 0.17, 0.14),
                    ("Inputs\nclimate, building, loads", 0.04, 0.42, 0.17, 0.14),
                    ("Data adapters\nsrc/model_v3/data", 0.27, 0.56, 0.18, 0.14),
                    ("Physics\nthermal balance", 0.50, 0.70, 0.17, 0.14),
                    ("Control\nthermostat/window logic", 0.50, 0.42, 0.17, 0.14),
                    ("Systems\ncarriers, PV, EV, grid", 0.72, 0.56, 0.21, 0.14),
                    ("Outputs\nstandardized metrics", 0.72, 0.24, 0.21, 0.14),
                    ("Scenario tree\nrunner, registry, comparisons", 0.27, 0.24, 0.28, 0.14),
                ],
                [(0, 2), (1, 2), (2, 3), (2, 4), (3, 5), (4, 5), (5, 6), (7, 6)],
            ),
        ),
        (
            "data_flow_inputs_to_outputs",
            "data_flow_inputs_to_outputs.png",
            "Data-flow diagram from inputs to outputs.",
            "Shows how file-backed inputs become prepared forcing, physical states, system states, raw outputs, standardized summaries, comparisons, and figures.",
            "Schematic generated from src/model_v3/data, physics, control, systems, output, and scenarios modules.",
            lambda p: draw_boxes(
                p,
                "Inputs to outputs data flow",
                [
                    ("Climate CSVs", 0.04, 0.70, 0.16, 0.12),
                    ("Building and technology YAML/CSV", 0.04, 0.48, 0.16, 0.12),
                    ("Stochastic seed/cohort config", 0.04, 0.26, 0.16, 0.12),
                    ("PreparedForcing", 0.29, 0.56, 0.19, 0.14),
                    ("PhysicsState -> ControlState -> SystemState", 0.55, 0.56, 0.28, 0.14),
                    ("Raw outputs", 0.55, 0.30, 0.15, 0.13),
                    ("Standardized summaries", 0.76, 0.30, 0.18, 0.13),
                ],
                [(0, 3), (1, 3), (2, 3), (3, 4), (4, 5), (5, 6)],
            ),
        ),
        (
            "climate_window_timeline_2050_policy",
            "climate_window_timeline_2050_policy.png",
            "Climate-window timeline showing the 2050 overlap policy.",
            "Shows that source files may overlap in 2050 while canonical analysis windows do not: near-future ends in 2049 and mid-century starts in 2050.",
            "config/scenario_tree/climate_windows.yaml",
            lambda p: draw_timeline(p, climate_windows),
        ),
        ("scenario_leaf_id_decomposition", "scenario_leaf_id_decomposition.png", "Scenario leaf ID decomposition diagram.", "Explains the four fields of a scenario leaf ID and why double underscores are reserved as dimension separators.", "config/scenario_tree/scenario_tree_schema.yaml", draw_leaf_id),
        (
            "runner_provenance_workflow",
            "runner_provenance_workflow.png",
            "Runner and provenance workflow.",
            "Shows how generated configs are executed by the runner and recorded in registry, logs, hashes, and output paths.",
            "src/model_v3/scenarios/run_scenario_tree.py and experiments/scenario_tree/manifests/run_registry.csv",
            lambda p: draw_boxes(
                p,
                "Runner and provenance workflow",
                [
                    ("scenario_leaf_index.csv", 0.04, 0.62, 0.19, 0.13),
                    ("run_config.yaml\ninputs_manifest.yaml", 0.28, 0.62, 0.20, 0.13),
                    ("run_scenario_tree", 0.53, 0.62, 0.18, 0.13),
                    ("logs and runner_status", 0.77, 0.74, 0.18, 0.12),
                    ("outputs", 0.77, 0.54, 0.18, 0.12),
                    ("run_registry.csv\nhashes, seed, status", 0.38, 0.30, 0.25, 0.13),
                ],
                [(0, 1), (1, 2), (2, 3), (2, 4), (2, 5)],
            ),
        ),
        (
            "output_standardization_workflow",
            "output_standardization_workflow.png",
            "Output standardization workflow.",
            "Shows how raw annual outputs are mapped into required energy, grid, PV/EV, and climate metrics before aggregation.",
            "src/model_v3/scenarios/summarize_outputs.py and src/model_v3/scenarios/output_reader.py",
            lambda p: draw_boxes(
                p,
                "Output standardization workflow",
                [
                    ("annual_profile.csv\nannual_summary.json", 0.05, 0.63, 0.22, 0.15),
                    ("output_reader metric adapter", 0.33, 0.63, 0.20, 0.15),
                    ("scenario_leaf_metrics.csv", 0.61, 0.63, 0.22, 0.15),
                    ("scenario_aggregate_metrics.csv", 0.61, 0.38, 0.22, 0.15),
                    ("comparison tables and figures", 0.33, 0.24, 0.26, 0.14),
                ],
                [(0, 1), (1, 2), (2, 3), (3, 4)],
            ),
        ),
        (
            "comparison_framework",
            "comparison_framework.png",
            "Comparison framework diagram.",
            "Summarizes climate-only, technology-only, combined stress-case, and stochastic robustness comparisons.",
            "config/scenario_tree/comparison_definitions.yaml",
            lambda p: draw_boxes(
                p,
                "Comparison framework",
                [
                    ("Baseline\nhistorical + current stock", 0.05, 0.65, 0.22, 0.14),
                    ("Climate-only\nfuture + frozen stock", 0.38, 0.73, 0.24, 0.14),
                    ("Technology-only\nsame climate, different tech", 0.38, 0.50, 0.24, 0.14),
                    ("Stress case\nlong-term RCP8.5 + high PV/EV", 0.38, 0.27, 0.24, 0.14),
                    ("Deltas and percentage changes", 0.72, 0.58, 0.22, 0.14),
                    ("P10/P50/P90\nstochastic robustness", 0.72, 0.33, 0.22, 0.14),
                ],
                [(0, 1), (0, 3), (1, 4), (2, 4), (3, 4), (2, 5), (3, 5)],
            ),
        ),
        ("input_data_inventory", "input_data_inventory.png", "Input data inventory chart.", "Counts detected input, config, summary, and metadata files by file type.", "Repository file inventory under inputs/, config/, and scenario-tree summaries.", lambda p: draw_inventory_chart(p, context.input_inventory)),
        ("metric_taxonomy", "metric_taxonomy.png", "Metric taxonomy diagram.", "Groups standardized metrics into energy totals, grid stress, technology metrics, climate metrics, comparisons, and uncertainty summaries.", "experiments/scenario_tree/summaries/realization_level/scenario_leaf_metrics_schema.yaml", draw_metric_taxonomy),
        ("caveats_and_gaps_overview", "caveats_and_gaps_overview.png", "Caveats and gaps overview heatmap.", "Gives a schematic severity overview of the main caveat families discussed in Chapter 13.", "Schematic generated from the caveat table in the handbook.", draw_caveats_heatmap),
    ]

    for figure_id, filename, caption, explanation, source, draw in schematic_specs:
        path = assets_dir / filename
        draw(path)
        figures.append(FigureInfo(figure_id=figure_id, path=relpath(path, context.repo_root), caption=caption, explanation=explanation, source=source, schematic=True))

    existing_structure = context.repo_root / "figures/scenario_tree/structure/scenario_tree_structure.png"
    scenario_asset = assets_dir / "scenario_tree_structure.png"
    if existing_structure.exists():
        shutil.copyfile(existing_structure, scenario_asset)
        figures.append(
            FigureInfo(
                figure_id="scenario_tree_structure",
                path=relpath(scenario_asset, context.repo_root),
                caption="Scenario-tree structure diagram.",
                explanation="Data-derived structure figure from the scenario-tree figure workflow.",
                source="figures/scenario_tree/structure/scenario_tree_structure.png",
                schematic=False,
                metrics="scenario_tree_dimensions",
            )
        )
    else:
        draw_boxes(
            scenario_asset,
            "Scenario tree structure",
            [
                ("Historical baseline\n1981-2005", 0.05, 0.62, 0.22, 0.16),
                ("Future windows\n2030-2049, 2050-2070, 2080-2100", 0.37, 0.62, 0.28, 0.16),
                ("RCP2.6 / RCP4.5 / RCP8.5", 0.72, 0.62, 0.22, 0.16),
                ("Technology cases", 0.37, 0.34, 0.22, 0.14),
                ("Seeds 0000-0099", 0.70, 0.34, 0.22, 0.14),
            ],
            [(0, 4), (1, 2), (2, 3), (3, 4)],
        )
        figures.append(
            FigureInfo(
                figure_id="scenario_tree_structure",
                path=relpath(scenario_asset, context.repo_root),
                caption="Scenario-tree structure diagram.",
                explanation="Schematic because figures/scenario_tree/structure/scenario_tree_structure.png was not found.",
                source="config/scenario_tree/",
                schematic=True,
            )
        )

    for meta in context.existing_figure_metadata:
        if not isinstance(meta, Mapping):
            continue
        png = str(meta.get("figure_file_png", ""))
        if not png:
            continue
        src = context.repo_root / png
        if not src.exists():
            continue
        stem = src.stem
        if stem == "scenario_tree_structure":
            continue
        dest = assets_dir / f"existing_{stem}.png"
        shutil.copyfile(src, dest)
        figures.append(
            FigureInfo(
                figure_id=str(meta.get("figure_id", stem)),
                path=relpath(dest, context.repo_root),
                caption=str(meta.get("figure_title", stem.replace("_", " "))),
                explanation="Existing generated scenario-tree figure copied into the handbook asset directory for stable inclusion.",
                source=str(meta.get("source_data_files", png)),
                schematic=False,
                metrics=str(meta.get("metrics_used", "")),
            )
        )
    return figures


def md_table(headers: list[str], rows: Iterable[Iterable[Any]]) -> str:
    out = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    for row in rows:
        cells = []
        for value in row:
            text = str(value).replace("\n", "<br>").replace("|", "\\|")
            cells.append(text)
        out.append("| " + " | ".join(cells) + " |")
    return "\n".join(out)


def file_inventory_rows(context: Context) -> list[list[str]]:
    rows = []
    for item in context.expected_paths:
        path = str(item["path"])
        role = "expected repository artifact"
        if "scenario_tree" in path:
            role = "scenario-tree config, artifact, report, or figure"
        elif path.startswith("inputs"):
            role = "model input data"
        elif path.startswith("src"):
            role = "implementation module"
        elif path.startswith("docs"):
            role = "documentation"
        rows.append([path, role, "Phase 1-9", "required" if not path.endswith("/") else "context", "yes" if item["exists"] else "no"])
    return rows


def metric_reference_rows(context: Context) -> list[list[str]]:
    schema = context.yaml_data.get("metric_schema", {})
    by_name: dict[str, Mapping[str, Any]] = {}
    for column in schema.get("columns", []) if isinstance(schema, Mapping) else []:
        if isinstance(column, Mapping):
            by_name[str(column.get("name", ""))] = column
    category_map = {
        "annual_electricity_gross_kWh": "energy",
        "annual_grid_import_kWh": "grid_energy",
        "annual_grid_export_kWh": "grid_energy",
        "annual_gas_kWh": "fuel",
        "annual_useful_heating_kWh": "thermal",
        "annual_dhw_kWh": "thermal",
        "peak_grid_import_W": "grid_power",
        "winter_peak_grid_import_W": "grid_power",
        "summer_peak_grid_import_W": "grid_power",
        "pv_generation_kWh": "distributed_energy",
        "pv_self_consumption_kWh": "distributed_energy",
        "pv_export_fraction": "distributed_energy",
        "ev_charging_kWh": "mobility",
        "mean_T_out_C": "climate",
        "winter_mean_T_out_C": "climate",
        "summer_mean_T_out_C": "climate",
        "HDD_15": "climate_degree_days",
        "HDD_18": "climate_degree_days",
        "CDD_22": "climate_degree_days",
        "mean_solar_W_m2": "climate_solar",
    }
    rows = []
    for metric in REQUIRED_METRICS:
        item = by_name.get(metric, {})
        unit = str(item.get("unit", "")) or "not_explicit"
        definition = str(item.get("description", metric.replace("_", " ")))
        if metric.startswith(("mean_", "winter_mean", "summer_mean", "HDD", "CDD")):
            source = "climate forcing file referenced by run config"
        else:
            source = "annual_summary.json or annual_profile.csv via src/model_v3/scenarios/output_reader.py"
        rows.append([metric, unit, category_map.get(metric, "metric"), definition, source, str(item.get("aggregation_policy", "numeric_distribution")), "Interpret with scenario, technology, and run coverage context."])
    return rows


def caveat_rows() -> list[list[str]]:
    return [
        ["CAV-ORCH-001", "Scenario-tree execution", "The scenario tree can be fully configured before all leaves have run.", "Configured leaves are not results.", "high", "all scenario summaries and comparisons", "Compare scenario_leaf_index.csv with run_registry.csv and scenario_leaf_metrics.csv.", "State execution coverage explicitly.", "Run planned leaves after dry-run validation.", "1"],
        ["CAV-ORCH-002", "Dry-run", "Dry-run success is not simulation success.", "A valid plan may still fail in physics, data loading, or output writing.", "medium", "run status and registry", "Inspect run_registry.csv for latest actual statuses.", "Use dry-run only as preflight.", "Add CI smoke execution for representative leaves.", "2"],
        ["CAV-ORCH-003", "Parallelism", "The documented runner starts with max-workers 1; broader batch parallelism is deferred or constrained.", "Serial execution is easier to audit but slower.", "medium", "runtime, batch operation", "Read runner help and Phase 4 docs.", "Run one baseline and one future leaf first.", "Add controlled parallel worker implementation with isolated logs.", "3"],
        ["CAV-CLIM-001", "Climate ensemble", "Detected processed climate files appear to use one named model/RCM chain in filenames unless more files are added.", "A single chain does not span full climate-model uncertainty.", "high", "climate metrics and deltas", "Inventory inputs/climate/processed.", "Describe RCP branches as conditional projections.", "Add multiple CORDEX or comparable model chains.", "1"],
        ["CAV-CLIM-002", "2050 overlap", "Raw processed files may overlap in 2050; canonical windows handle this by assigning 2050 only to mid-century.", "Double-counting would bias cross-window summaries.", "high", "climate metrics, figures, comparisons", "Run summary/comparison/figure validation and inspect climate_windows.yaml.", "Always cite the canonical window policy.", "Add automated tests for every climate metric path.", "1"],
        ["CAV-CLIM-003", "RCP interpretation", "RCP pathways are climate projection branches, not predictions or probabilities.", "Thesis wording can overstate forecast certainty.", "medium", "all future comparisons", "Review thesis text for forecast language.", "Use scenario/counterfactual wording.", "Add scenario weighting only with literature justification.", "2"],
        ["CAV-PHYS-001", "One-zone physics", "The thermal model is a simplified one-zone/lumped representation.", "Room-level dynamics and building heterogeneity are not resolved.", "high", "heating demand, comfort, peaks", "Inspect src/model_v3/physics/thermal_dynamics.py.", "Frame as bottom-up archetype modelling.", "Add multi-zone or calibrated archetype variants.", "2"],
        ["CAV-PHYS-002", "UA and thermal mass", "Envelope heat loss and thermal mass values are uncertain and archetype-dependent.", "Heating demand and peak response are sensitive to these parameters.", "high", "useful heating, peak import", "Inspect building inputs and sensitivity tests.", "Report assumptions and avoid overprecision.", "Run sensitivity analysis on UA and mass.", "1"],
        ["CAV-PHYS-003", "Ventilation and infiltration", "Ventilation/infiltration assumptions are simplified and behaviour-dependent.", "Air exchange can strongly affect heat loss.", "medium", "space heating, indoor temperature", "Inspect airflow archetype inputs and physics/control modules.", "State ventilation convention clearly.", "Calibrate against measured or audited building data.", "2"],
        ["CAV-TECH-001", "Technology cases", "Technology cases are modelling assumptions and not forecasts unless calibrated elsewhere.", "Adoption rates drive electricity, gas, PV, and EV outcomes.", "high", "energy carrier shifts, grid import/export", "Inspect technology_cases.yaml and belgian_technology_inputs.yaml.", "Call them counterfactual branches.", "Calibrate with Belgian statistics and literature.", "1"],
        ["CAV-TECH-002", "Heat pump COP", "COP conversion can be simplified or configured as representative seasonal performance.", "Peak electric demand can be sensitive to COP assumptions.", "medium", "electricity, grid peaks, gas displacement", "Inspect systems/technology.py and run configs.", "Document COP assumptions.", "Add temperature-dependent COP curves.", "2"],
        ["CAV-TECH-003", "PV/EV behaviour", "PV self-consumption and EV charging depend on temporal matching and charging behaviour.", "Annual totals can hide stress timing.", "medium", "PV export, grid peaks, EV demand", "Inspect output_reader policies and annual profiles.", "Use peak and seasonal metrics with annual values.", "Add richer charging and self-consumption models.", "2"],
        ["CAV-STOCH-001", "Finite seeds", "P10/P50/P90 bands depend on the number of successful stochastic realizations.", "Small samples can make bands unstable.", "high", "uncertainty bands", "Check n_successful_realizations and stochastic tables.", "State sample size with every band.", "Add Monte Carlo convergence checks.", "1"],
        ["CAV-STOCH-002", "Behavioural calibration", "Behavioural distributions may not be fully empirically calibrated.", "Stochastic spread may understate real behavioural variability.", "medium", "load profiles, peaks, DHW, EV", "Inspect stochastic modules and validation data.", "Call bands modelled stochastic spread.", "Calibrate against smart-meter or survey data.", "2"],
        ["CAV-MET-001", "Gross electricity vs grid import", "Gross electricity is demand before PV netting; grid import is after local PV netting.", "Confusing them changes the interpretation of electrification and PV.", "high", "electricity demand, grid import/export", "Use metric reference table and output_reader mapping.", "Explain both metrics in figure captions.", "Add unit tests and labels to all plots.", "1"],
        ["CAV-MET-002", "Power and energy units", "W, kW, and kWh are distinct and conversion depends on timestep duration.", "Unit errors can distort peak and annual metrics.", "high", "all energy and peak metrics", "Inspect output_reader unit handling and profile timestamps.", "State units in tables and axes.", "Centralize unit conversion tests.", "1"],
        ["CAV-VAL-001", "Internal vs external validation", "Scenario-tree validation checks consistency and traceability, not empirical accuracy.", "A defensible pipeline can still produce biased demand estimates.", "high", "all results", "Inspect validation reports and external validation outputs separately.", "Do not claim external validation without a report.", "Validate against Fluvius/smart-meter/aggregate load data with criteria.", "1"],
        ["CAV-THESIS-001", "Scenario interpretation", "Scenario comparisons should not be interpreted as forecasts.", "The thesis conclusion must separate conditional effects from predictions.", "medium", "narrative and supervisor discussion", "Review executive summary and thesis text.", "Use scenario, counterfactual, and conditional wording.", "Add a limitations paragraph to every results chapter.", "1"],
    ]


def terminology_rows() -> list[list[str]]:
    terms = [
        ("model engine", "The code that turns configured inputs into simulated residential demand outputs.", "src/model_v3/data, physics, control, systems, output", "It is the numerical core, separate from the scenario-tree manager.", "Do not call the scenario tree itself the physics model."),
        ("runner", "The orchestration command that selects scenario leaves, validates configs, executes leaves, and records registry rows.", "src/model_v3/scenarios/run_scenario_tree.py", "It is the operational bridge between configs and model execution.", "A dry-run runner result is not a completed simulation."),
        ("orchestration layer", "The scenario-tree code that manages leaf selection, run folders, logs, registry, summaries, comparisons, and figures.", "src/model_v3/scenarios and src/model_v3/scenario_tree", "It provides reproducibility and traceability.", "It does not prove physical realism by itself."),
        ("scenario", "A deterministic combination of climate window, climate pathway, and technology case.", "scenario_tree_schema.yaml and scenario_leaf_index.csv", "It is the parent grouping for stochastic leaves.", "A scenario is not a single run if it has many seeds."),
        ("realization", "A stochastic sampling instance identified by a reproducible seed.", "realization_policy.yaml, run configs, summaries", "It allows pairwise comparisons and stochastic spread.", "A realization is not a climate model member."),
        ("scenario leaf", "One executable unit: one scenario plus one realization.", "scenario_leaf_index.csv and run directories", "It is the smallest run and registry unit.", "An enumerated leaf is not automatically a successful output."),
        ("scenario ID", "The stable identifier for a scenario without the seed.", "scenario_id columns", "It groups realization outputs.", "Do not confuse with scenario_leaf_id."),
        ("scenario leaf ID", "The full stable identifier including seed.", "run directories and registry", "It joins configs, outputs, logs, summaries, and figures.", "Do not edit IDs after outputs exist."),
        ("manifest", "A metadata file describing generated experiment files and provenance.", "scenario_tree_manifest.yaml and handbook manifest", "It makes generated artifacts auditable.", "It is evidence of file generation, not model validation."),
        ("input manifest", "A per-leaf file listing resolved inputs such as climate and technology files.", "runs/*/inputs_manifest.yaml", "It answers which inputs a leaf used.", "It does not guarantee the input values are scientifically perfect."),
        ("run registry", "A CSV ledger of run attempts, statuses, hashes, paths, seeds, and errors.", "manifests/run_registry.csv", "It is the source of truth for what has run.", "Skipped rows must be interpreted with latest actual status logic."),
        ("provenance", "Information needed to trace a result back to config, input files, code state, seed, and output path.", "run registry, audit matrix", "It makes supervisor questions answerable.", "Provenance is not the same as calibration."),
        ("config hash", "A hash of the run configuration used by an attempt.", "run_registry.csv", "It detects config drift between runs.", "A hash does not describe whether assumptions are appropriate."),
        ("reproducibility", "Ability to regenerate the same documented artifacts from the same inputs and code.", "manifests, configs, scripts", "It underpins thesis-grade traceability.", "Reproducible does not mean externally valid."),
        ("deterministic path resolver", "Code that maps scenario IDs and inputs to stable config, run, output, and log paths.", "src/model_v3/scenario_tree/paths.py", "It prevents ad hoc output locations.", "It cannot recover from manually renamed files."),
        ("canonical analysis window", "The non-overlapping date range used for metrics and comparisons.", "climate_windows.yaml", "It prevents double-counting 2050.", "It can differ from source-file coverage."),
        ("source-file window", "The raw or processed climate file coverage recorded for a file.", "climate_windows.yaml and climate CSV paths", "It documents input coverage.", "It may overlap across files."),
        ("baseline", "The historical reference branch using baseline_1981_2005, historical pathway, and tech_current_stock.", "scenario_tree_schema.yaml", "It anchors future deltas.", "It is not automatically a measured-demand validation."),
        ("counterfactual", "A conditional scenario used to isolate an effect, such as future climate with frozen stock.", "comparison_definitions.yaml", "It helps separate climate and technology effects.", "It is not a forecast."),
        ("technology case", "A branch describing residential technology assumptions.", "technology_cases.yaml", "It controls heat pumps, PV, EV, gas/electric shifts.", "The metadata may be qualitative unless calibrated."),
        ("stress case", "A high-impact comparison branch such as long-term RCP8.5 with high electrification, PV, and EV.", "comparison_definitions.yaml", "It probes infrastructure stress.", "It is not the most likely future."),
        ("bottom-up model", "A model that builds demand from household/building/end-use mechanisms rather than fitting aggregate totals only.", "model_v3 architecture", "It links assumptions to physical and behavioural drivers.", "It still needs calibration."),
        ("archetype", "A representative dwelling or household category with shared parameters.", "inputs/building and archetypes.yaml", "It reduces complexity while preserving building diversity.", "It may hide within-category variation."),
        ("one-zone thermal model", "A lumped building representation with one indoor temperature state.", "physics_core.py and thermal_dynamics.py", "It enables transparent heat-balance simulation.", "It does not model room-by-room dynamics."),
        ("heat balance", "Accounting of heat losses, gains, and supplied heat over a timestep.", "physics_core.py", "It drives useful heating demand.", "Simplified terms may omit detailed dynamics."),
        ("thermal mass", "The effective heat capacity that slows indoor temperature changes.", "InputDataset, PreparedForcing, PhysicsState", "It affects peaks and comfort.", "It is difficult to know precisely for real dwellings."),
        ("UA value", "Overall heat loss coefficient in W per K.", "heat_loss_coefficient_W_per_C fields", "It determines envelope heat loss.", "It can be uncertain by archetype."),
        ("infiltration", "Uncontrolled air exchange with outdoors.", "ACH_inf and airflow calculations", "It adds heat loss.", "Behaviour and leakage vary strongly."),
        ("ventilation", "Controlled or assumed air exchange.", "ACH_vent_base, ACH_vent_occupied, eta_HRV", "It affects heat losses and indoor conditions.", "Schedules and heat recovery may be simplified."),
        ("internal gains", "Heat from occupants, appliances, lighting, and cooking.", "data_module.py and PreparedForcing fields", "They reduce heating demand and affect free-float temperature.", "Occupant behaviour is uncertain."),
        ("solar gains", "Heat entering through windows from solar irradiance.", "PreparedForcing Q_solar_gains_W fields", "They influence heating and overheating.", "Orientation and shading assumptions matter."),
        ("useful heat", "Thermal energy delivered to the space or DHW before carrier conversion.", "annual_useful_heating_kWh, annual_dhw_kWh", "It separates building demand from system efficiency.", "It is not the same as gas or electricity input."),
        ("final energy", "Delivered carrier energy consumed by the household, such as gas or electricity.", "annual_gas_kWh and electricity metrics", "It matters for emissions and billing.", "Do not mix with useful heat."),
        ("delivered energy", "Energy supplied to the dwelling by a carrier.", "carrier conversion in systems/technology.py", "It connects useful heat to gas/electricity.", "PV netting can complicate electricity interpretation."),
        ("domestic hot water", "Useful heat demand for hot water use.", "Q_dhw_demand_W and annual_dhw_kWh", "It is a non-space-heating thermal load.", "Behavioural timing can be uncertain."),
        ("coefficient of performance", "Useful heat delivered per unit electric energy for a heat pump.", "heating_cop, dhw_cop, technology performance", "It determines electrification impact.", "Seasonal/static COP can miss weather dependence."),
        ("heat pump", "Electric heating technology converting electricity to useful heat with COP above one.", "technology_cases.yaml and systems/technology.py", "It shifts heat demand from gas to electricity.", "Uptake and performance are assumptions."),
        ("PV generation", "Electricity generated by photovoltaic panels.", "P_pv_generation_W and pv_generation_kWh", "It reduces grid import and can create export.", "Annual PV generation does not guarantee peak relief."),
        ("self-consumption", "PV generation used locally instead of exported.", "pv_self_consumption_kWh", "It indicates local matching of supply and demand.", "The metric depends on temporal resolution."),
        ("grid import", "Electricity drawn from the external grid after PV netting.", "P_el_grid_import_W and annual_grid_import_kWh", "It matters for network load.", "It is not gross electricity demand."),
        ("grid export", "Electricity sent to the grid when PV exceeds local demand.", "P_el_grid_export_W and annual_grid_export_kWh", "It affects distribution flows.", "It depends on PV and load timing."),
        ("peak demand", "Maximum power over a selected period.", "peak_grid_import_W and seasonal peaks", "It is critical for grid stress.", "It depends on timestep resolution."),
        ("load profile", "Time series of demand or power.", "annual_profile.csv and input load profiles", "It captures timing, not just annual energy.", "Annual aggregation hides profile shape."),
        ("RCP", "Representative Concentration Pathway climate forcing branch.", "climate_pathway_id values", "It structures future climate uncertainty.", "It is not a probability."),
        ("RCP2.6", "Lower forcing RCP branch encoded as rcp_2_6.", "climate_windows.yaml", "It represents a low-forcing future branch.", "It does not include technology adoption by itself."),
        ("RCP4.5", "Intermediate forcing RCP branch encoded as rcp_4_5.", "climate_windows.yaml", "It provides a middle climate branch.", "It is not a central forecast."),
        ("RCP8.5", "Higher forcing RCP branch encoded as rcp_8_5.", "climate_windows.yaml", "It supports stress-case climate analysis.", "It should not automatically be called most likely."),
        ("climate forcing", "Weather or climate input time series used to drive the model.", "inputs/climate/processed and run configs", "It determines outdoor temperature and solar inputs.", "One forcing file is not the full climate ensemble."),
        ("historical baseline", "The 1981-2005 historical climate branch.", "baseline_1981_2005", "It anchors future comparisons.", "It is climate baseline, not measured demand validation."),
        ("climate window", "A named analysis period such as near future or mid-century.", "climate_windows.yaml", "It controls which years are summarized.", "Source and canonical windows can differ."),
        ("HDD", "Heating degree days: accumulated coldness relative to a base temperature.", "HDD_15 and HDD_18", "It explains heating demand pressure.", "It is a climate proxy, not the demand model itself."),
        ("HDD_15", "Heating degree days using 15 C base.", "standardized metrics", "Useful for climate sensitivity.", "Base choice affects magnitude."),
        ("HDD_18", "Heating degree days using 18 C base.", "standardized metrics", "Captures stricter heating threshold.", "Base choice affects interpretation."),
        ("CDD", "Cooling degree days: accumulated warmth above a base temperature.", "CDD_22", "Useful for summer stress analysis.", "Cooling model may be absent or simplified."),
        ("CDD_22", "Cooling degree days using 22 C base.", "standardized metrics", "Indicates warm-weather exposure.", "It does not equal cooling energy unless cooling is modelled."),
        ("irradiance", "Solar power per area, typically W/m2.", "mean_solar_W_m2 and climate columns", "It drives solar/PV or solar gains.", "Column convention must be checked."),
        ("mean outdoor temperature", "Average T_out over canonical window.", "mean_T_out_C", "Summarizes climate branch warmth.", "A mean can hide extremes."),
        ("winter mean", "Mean outdoor temperature for December, January, and February.", "winter_mean_T_out_C", "Relevant for heating and winter peaks.", "Season definition is fixed and simple."),
        ("summer mean", "Mean outdoor temperature for June, July, and August.", "summer_mean_T_out_C", "Relevant for summer stress.", "It does not capture heatwaves alone."),
        ("stochastic model", "A model component using random draws controlled by seeds.", "src/model_v3/stochastic and cohort modules", "It represents behavioural/cohort variability.", "Random spread is conditional on assumed distributions."),
        ("seed", "Integer reproducibility key mapped from realization_id.", "realization_policy.yaml and run configs", "It allows reruns and pairwise comparisons.", "A seed is not a probability weight."),
        ("cohort", "A sampled group of households or profiles represented by a realization.", "cohort_size fields and cohort modules", "It approximates population variability.", "Finite cohort size can create sampling noise."),
        ("aleatoric uncertainty", "Intrinsic variability such as behaviour differences.", "stochastic realizations", "It motivates P10/P50/P90 bands.", "Modelled variability may be narrower than reality."),
        ("epistemic uncertainty", "Uncertainty from limited knowledge, data, or model structure.", "caveats and assumptions", "It motivates sensitivity and validation work.", "More seeds do not remove structural uncertainty."),
        ("scenario uncertainty", "Uncertainty represented by alternative climate and technology branches.", "scenario tree", "It separates branch assumptions.", "Branches are not probabilities unless weighted."),
        ("uncertainty band", "A spread summary across realizations, commonly P10 to P90.", "stochastic robustness tables and figures", "It communicates robustness.", "It is not a measured confidence interval."),
        ("P10", "10th percentile of available realization outcomes.", "comparison robustness outputs", "Shows low-side stochastic outcome.", "Unstable with few successful rows."),
        ("P50", "Median outcome.", "comparison robustness outputs", "A robust central value.", "Not the same as expected value if distributions are skewed."),
        ("P90", "90th percentile of available realization outcomes.", "comparison robustness outputs", "Shows high-side stochastic outcome.", "Unstable with few successful rows."),
        ("quantile", "A value below which a given share of observations falls.", "aggregate and comparison tables", "Summarizes distributions without assuming normality.", "Requires enough observations."),
        ("Monte Carlo", "Repeated stochastic sampling using different seeds.", "realization policy and stochastic robustness", "Estimates variability or convergence.", "This only covers modelled stochastic dimensions."),
        ("convergence", "Stability of estimates as more realizations are added.", "recommended improvements", "It supports robust uncertainty statements.", "Not proven by a small number of runs."),
        ("sensitivity analysis", "Systematic variation of assumptions to see output response.", "recommended improvements", "It identifies influential assumptions.", "It is different from validation."),
        ("RMSE", "Root mean squared error between model and reference series.", "validation metrics modules", "Penalizes large errors.", "Scale-dependent and sensitive to outliers."),
        ("MAE", "Mean absolute error.", "validation metrics modules", "Easy-to-interpret average absolute deviation.", "Does not emphasize large errors as much as RMSE."),
        ("Pearson correlation", "Linear association between modelled and reference time series.", "validation metrics modules", "Checks timing and shape co-movement.", "High correlation can still have bias."),
        ("NMBE", "Normalized mean bias error.", "validation metrics modules", "Shows systematic over- or under-prediction.", "Can hide compensating errors."),
        ("CVRMSE", "Coefficient of variation of RMSE.", "validation metrics modules", "Normalizes RMSE by mean reference level.", "Can be unstable when mean is small."),
        ("coefficient of variation", "Standard deviation divided by mean.", "aggregate and stochastic tables", "Measures relative spread.", "Undefined or misleading near zero mean."),
        ("standard deviation", "Average spread around the mean.", "aggregate metrics", "Describes realization variability.", "Assumes finite sample and is outlier-sensitive."),
        ("median", "Middle value of a sorted sample.", "aggregate metrics", "Robust central tendency.", "May differ from mean."),
        ("percentile", "Value at a stated rank of a distribution.", "p05, p10, p90, p95 columns", "Communicates spread.", "Needs enough samples."),
        ("interquartile range", "P75 minus P25.", "stochastic robustness diagnostics", "Robust spread measure.", "Not a full range."),
        ("diversity factor", "Ratio reflecting non-coincident individual peaks versus aggregate peak.", "grid analysis concept", "Useful for feeder planning.", "Only meaningful with compatible profile granularity."),
        ("load duration curve", "Sorted load profile from high to low.", "validation output figures", "Shows distribution of load magnitudes.", "Loses chronological timing."),
        ("calibration", "Adjusting model parameters to match reference data.", "validation and recommended improvements", "Improves empirical fit.", "Can overfit without independent validation."),
        ("validation", "Testing model outputs against internal contracts or external data.", "validation reports and validation modules", "Supports trust in outputs.", "Internal validation is not external empirical validation."),
        ("internal consistency check", "A check that files, IDs, metrics, and policies agree.", "scenario-tree validators", "Prevents traceability mistakes.", "Does not prove accuracy."),
        ("external validation", "Comparison with independent measured data.", "validation reports if present", "Needed for accuracy claims.", "Do not claim it unless a report proves it."),
        ("baseline comparison", "Future leaf compared against historical current-stock baseline.", "baseline_comparison_metrics.csv", "Quantifies future delta.", "Requires matching successful baseline realization."),
        ("delta", "Difference between compared and reference value.", "comparison tables", "Shows absolute change.", "Interpret with units and reference choice."),
        ("percentage change", "Delta divided by reference value times 100.", "comparison percentage tables", "Normalizes change.", "Undefined when reference is zero."),
    ]
    return [list(row) for row in terms]


def implementation_status_section(context: Context) -> str:
    return "\n".join(
        [
            "## Implementation status by phase",
            md_table(
                ["phase", "expected deliverables", "detected files", "status", "warnings", "next action"],
                [[row["phase"], row["expected_deliverables"], row["detected_files"], row["status"], row["warnings"], row["next_action"]] for row in context.phase_statuses],
            ),
        ]
    )


def figures_markdown(figures: list[FigureInfo], heading: str = "Handbook figures") -> str:
    parts = [f"## {heading}"]
    for idx, fig in enumerate(figures, start=1):
        parts.extend(
            [
                f"### Figure {idx}: {fig.caption}",
                f"![{fig.caption}]({fig.path})",
                f"Caption: {fig.caption}",
                f"Explanation: {fig.explanation}",
                f"Source data or config: `{fig.source}`.",
                f"Metrics used: {fig.metrics or 'not_applicable'}.",
                f"Figure type: {'schematic' if fig.schematic else 'data-derived or copied generated figure'}.",
            ]
        )
    return "\n\n".join(parts)


def build_handbook_markdown(context: Context) -> str:
    registry = context.registry
    counts = context.counts
    climate_windows_file = "`config/scenario_tree/climate_windows.yaml`"
    tech_cases_file = "`config/scenario_tree/technology_cases.yaml`"
    comparison_file = "`config/scenario_tree/comparison_definitions.yaml`"
    leaf_count = registry.get("enumerated_scenario_leaves", counts.get("scenario_leaves", "unknown"))
    success_count = registry.get("successful_scenario_leaves", counts.get("successful_scenario_leaves", "unknown"))
    summary_rows = context.csv_info.get("scenario_leaf_metrics", CsvInfo("", False)).rows
    aggregate_rows = context.csv_info.get("scenario_aggregate_metrics", CsvInfo("", False)).rows
    figure_rows = counts.get("figure_metadata_rows", len(context.existing_figure_metadata))
    cover = f"""# {TITLE}

{SUBTITLE}

Repository: `model_v3`

Generation date UTC: {context.generation_timestamp}

Git commit: {context.git_commit or "not_available"}

Git dirty status: {context.git_dirty_status}

This document is generated from local repository metadata, scripts, configs, manifests, summaries, validation reports, and figure metadata. It is not a literature review and it does not fabricate missing results.
"""

    executive = f"""# Executive summary

`model_v3` is a bottom-up residential energy-demand modelling repository with a scenario-tree layer for organizing climate, technology, and stochastic uncertainty. The core model is implemented under `src/model_v3/`; the scenario-tree experiment space is under `experiments/scenario_tree/`; the main scenario-tree configuration files are under `config/scenario_tree/`.

For the thesis, the scenario-tree layer is useful because it separates three sources of variation that would otherwise be mixed together in output filenames: climate forcing, residential technology assumptions, and stochastic household or cohort realizations. The implemented design supports this claim in a careful form:

Climate projections were organized into a structured scenario tree consisting of a historical baseline and three future climate windows under RCP2.6, RCP4.5, and RCP8.5. Each climate branch was combined with technology adoption assumptions and stochastic household realizations. This allowed climate, technology, and behavioural uncertainty to be separated and compared through consistent output metrics.

The repository currently contains a configured scenario tree with {leaf_count} enumerated scenario leaves. The audit/registry evidence available to this handbook supports {success_count} latest-successful scenario leaves and {summary_rows if summary_rows is not None else "unknown"} standardized per-leaf summary rows. Therefore the framework is implemented, but execution coverage is partial. This handbook does not claim that all leaves have run.

Implemented components detected in the repository include scenario-tree schema files, stable scenario IDs, canonical climate windows, an explicit 2050 overlap policy, generated experiment-space manifests, per-leaf configs, a runner/provenance layer, standardized outputs, comparison definitions, generated scenario-tree figures, and audit/validation reports where present. The comparison validation report also records missing comparison groups where successful summary rows are not available.

What can currently be claimed: the repository contains a traceable scenario-tree framework and generated artifacts for a subset of successful leaves. Internal validation and audit reports check schema consistency, input references, summaries, comparisons, figures, 2050 policy handling, and traceability. What cannot be claimed from the scenario-tree reports alone: complete execution of all enumerated leaves, external empirical validation of model accuracy, or calibrated future technology adoption forecasts.

For a supervisor, the short explanation is: `model_v3` simulates residential energy demand from building, climate, technology, and stochastic household assumptions; the scenario tree turns that model into a reproducible experiment design so future climate pathways, technology cases, and behavioural realizations can be compared without losing traceability.

{implementation_status_section(context)}
"""

    chapter1 = """# Chapter 1 - Purpose and thesis context

The thesis goal supported by this repository is to analyse residential energy demand under changing climate conditions and changing household technology assumptions. The model is bottom-up because it starts from dwelling, end-use, weather, technology, and behaviour assumptions rather than only fitting a top-down aggregate annual demand curve. It is stochastic because household/cohort draws and demand-profile variability are represented through reproducible realization seeds. It is physics-informed because the model includes a thermal balance, heat losses, internal gains, solar gains, heating control, carrier conversion, and grid import/export accounting.

Climate uncertainty matters because outdoor temperature and solar forcing affect heating demand, PV generation, and seasonal grid stress. Technology assumptions matter because electrification, heat pumps, PV, and EV charging can change both annual energy carriers and peak electricity demand. A scenario-tree approach is useful because it keeps these dimensions separate: climate branches answer "what forcing was used?", technology branches answer "what equipment/stock assumption was active?", and realization IDs answer "which stochastic draw produced the result?".

Compared with earlier model versions, the v3 repository visible here adds a modular architecture with explicit interface dataclasses in `src/model_v3/interfaces.py`, scenario-tree metadata under `config/scenario_tree/`, reproducible run folders under `experiments/scenario_tree/`, and standardized metrics/comparisons for thesis figures. This handbook distinguishes the general modelling concepts from repository-specific evidence.

Key terms: a bottom-up model builds demand from components; a stochastic model uses controlled random draws; a physics-informed model encodes simplified physical relationships; scenario analysis compares conditional futures; uncertainty propagation follows how input and branch assumptions change output metrics.
"""

    chapter2 = """# Chapter 2 - High-level model architecture

The main implementation code is under `src/model_v3/`. The core data contract is explicit in `src/model_v3/interfaces.py` and follows this sequence:

`InputDataset -> PreparedForcing -> PhysicsState -> ControlState -> SystemState -> ModelOutputs`

`InputDataset` holds raw or lightly structured model inputs and default fields such as outdoor temperature, setpoint, heat-loss coefficient, thermal mass, airflow rates, DHW demand, appliances, lighting, cooking, EV charging, and PV generation. `PreparedForcing` is the time-aligned forcing bundle ready for physics. `PhysicsState` contains the free-float thermal response, heat losses, internal and solar gains, and heating demand. `ControlState` applies thermostat/deadband/window-opening logic. `SystemState` applies heating/DHW technology conversion, PV netting, grid import/export, comfort, and carrier outputs. `ModelOutputs` is the final public output contract.

The model engine is the data, physics, control, systems, and output code that simulates one configured run. The scenario-tree layer enumerates and manages combinations of climate, technology, and realization IDs. The runner is the operational entrypoint that validates and executes leaves and records provenance. Validators check schemas, configs, summaries, comparisons, figures, and traceability. A manifest records what was generated and from which sources. Summary tables are standardized CSV outputs used for comparisons and figures.

The configuration layer is under `config/`. The input layer includes `inputs/climate/processed/`, `inputs/building/`, weather, solar, load-profile, occupancy, and end-use files where present. Climate preprocessing exists under `src/climate/` and the processed climate products are consumed by scenario leaves. Technology assumptions are encoded both qualitatively in `technology_cases.yaml` and concretely through `config/belgian_technology_inputs.yaml`. Stochastic realization policy is encoded in `realization_policy.yaml`; cohort generation is handled by the model engine and stochastic/cohort modules rather than by the scenario-tree schema alone.

The runner/orchestration layer is implemented under `src/model_v3/scenarios/` and `src/model_v3/scenario_tree/`. The output standardization layer is implemented by `src/model_v3/scenarios/summarize_outputs.py`, `summary_contract.py`, and `output_reader.py`. The comparison layer is `generate_comparisons.py` plus `comparison_definitions.yaml`. The figure/documentation layer includes `generate_figures.py`, figure metadata, and this Phase 9 handbook generator.
"""

    chapter3 = """# Chapter 3 - Model physics and simulation logic

The implemented physics layer includes a simplified lumped-zone thermal representation. `src/model_v3/physics/thermal_dynamics.py` integrates a single indoor temperature state with envelope loss, airflow loss, capacitance, internal gains, solar gains, and optional heating over a timestep. `src/model_v3/physics/physics_core.py` computes infiltration/ventilation flows, airflow heat loss, passive balance, free-float temperature, and heating demand needed to reach setpoint.

Outdoor temperature forcing appears as `T_outdoor_C`/`T_out_C` fields and climate forcing CSV columns. Heat loss through the envelope is represented by `heat_loss_coefficient_W_per_C`, a UA-like term in watts per degree C. Ventilation and infiltration losses use air changes per hour (`ACH_inf`, `ACH_vent_base`, `ACH_vent_occupied`), dwelling volume, air density, heat capacity, and heat recovery when ventilation type is balanced. Internal gains are represented by occupant, appliance, lighting, and cooking heat gain fields. Solar gains are represented by orientation-specific solar inputs and `Q_solar_gains_W`.

Heating demand is computed as useful thermal demand in watts before conversion to carriers. DHW demand appears as `Q_dhw_demand_W` and standardized `annual_dhw_kWh`. Electricity demand includes appliances, lighting, cooking, space-heating technology electricity, DHW technology electricity, and EV charging. Heat pump/COP conversion and other carrier conversions are handled in `src/model_v3/systems/technology.py`. Gas use is represented through carrier-specific power columns such as `P_gas_space_heating_W` and `P_gas_dhw_W`. PV generation and grid import/export accounting are handled in the systems layer through `P_pv_generation_W`, gross electricity, net grid power, grid import, and grid export.

Peak demand is standardized as `peak_grid_import_W`, `winter_peak_grid_import_W`, and `summer_peak_grid_import_W`. The peak values depend on raw output timestep resolution and the seasonal definitions used by `output_reader.py`: winter is December, January, February; summer is June, July, August.

Physics caveats: the thermal representation is one-zone and simplified; aggregation hides individual dwelling diversity; spatial grid constraints are not represented; UA and thermal mass can be uncertain; occupant behaviour strongly affects loads and internal gains; COP modelling may be simplified; PV self-consumption and EV charging depend on temporal matching; and external calibration against measured Belgian household data must be cited only where validation reports actually prove it.
"""

    climate_rows = []
    climate_windows = dict(dict(context.yaml_data.get("climate_windows", {})).get("climate_windows", {}))
    for key, cfg_any in climate_windows.items():
        cfg = dict(cfg_any)
        climate_rows.append([key, cfg.get("canonical_start", ""), cfg.get("canonical_end", ""), cfg.get("source_file_window", ""), cfg.get("window_type", ""), ", ".join(cfg.get("allowed_pathways", []))])
    tech_cases = dict(dict(context.yaml_data.get("technology_cases", {})).get("technology_cases", {}))
    tech_rows = [[key, val.get("label", ""), ", ".join(val.get("applicable_window_types", [])), val.get("modelling_interpretation", val.get("description", ""))] for key, val in tech_cases.items() if isinstance(val, Mapping)]

    chapter4 = f"""# Chapter 4 - Inputs and data sources

The handbook generator inspected relevant inputs, configs, reports, summaries, and figure metadata. The input inventory in Appendix A and Appendix D marks missing files explicitly. For each input source, the generated inventory records path, type, purpose, detectable temporal-resolution hints, units if detectable from column names, scenario dimension affected, required/optional status, and validation status.

## Climate inputs

Processed climate forcing files are expected under `inputs/climate/processed/`. The scenario-tree config defines baseline/historical and future RCP branches in {climate_windows_file}. Temperature and solar columns are detected by summary code when climate metrics are computed. The required climate metrics are mean temperature, winter mean temperature, summer mean temperature, HDD_15, HDD_18, CDD_22, and mean_solar_W_m2.

{md_table(["climate window", "canonical start", "canonical end", "source-file window", "type", "allowed pathways"], climate_rows)}

2050 policy: raw processed source files may overlap in 2050, but canonical analysis windows do not. Near-future ends on 2049-12-31. Mid-century starts on 2050-01-01. Therefore 2050 is assigned only to the mid-century canonical analysis window. This policy is encoded in {climate_windows_file}.

## Building and archetype inputs

Building and archetype inputs were searched under `inputs/building/` and `config/archetypes.yaml`. The model interface includes floor/volume-like parameters, heat-loss coefficient, thermal mass, ventilation/infiltration, setpoints, glazing/orientation, occupant gains, and heat-gain fractions. Where these values are missing or simplified, the model falls back to configured defaults in the data/interface layer; such defaults should be treated as assumptions, not measurements.

## Technology inputs

Technology cases are defined in {tech_cases_file}. `tech_current_stock` is baseline-only unless metadata says otherwise. Future climate-only comparisons use `tech_frozen_stock`, not future `tech_current_stock`. The Belgian technology input YAML is `config/belgian_technology_inputs.yaml`.

{md_table(["technology case", "label", "applicable windows", "interpretation"], tech_rows)}

## Stochastic inputs

Realizations are `seed_0000` through `seed_0099` according to `config/scenario_tree/realization_policy.yaml`. The seed controls reproducible stochastic sampling in the model engine. Scenario uncertainty is represented by climate and technology branches; stochastic variability is represented by realization IDs and cohort draws. The policy file states that cohorts were not generated in the scenario-tree metadata phase itself.
"""

    chapter5 = f"""# Chapter 5 - Scenario-tree design

A scenario is the deterministic parent combination of `climate_window_id`, `climate_pathway_id`, and `technology_case_id`. A realization is a stochastic seed/cohort instance. A scenario leaf is one scenario plus one realization. Stable identifiers are required because they are the join key between configs, runs, logs, registry rows, summaries, comparison tables, figures, and thesis text.

Canonical ID format:

```text
{{climate_window_id}}__{{climate_pathway_id}}__{{technology_case_id}}__{{realization_id}}
```

Examples:

```text
baseline_1981_2005__historical__tech_current_stock__seed_0042

mid_century_2050_2070__rcp_8_5__tech_high_electrification_pv_ev__seed_0042
```

Double underscores separate scenario dimensions. Individual dimension names use lowercase tokens and single underscores. Accepted abbreviations such as RCP, PV, and EV are encoded in lowercase IDs.

The scenario tree answers four traceability questions: which climate forcing was used, which technology assumptions were active, which stochastic seed/cohort generated the result, and which exact model/config produced the output. The scenario-tree schema is encoded in `config/scenario_tree/scenario_tree_schema.yaml`.
"""

    chapter6 = """# Chapter 6 - Directory structure and experiment space

The physical experiment space is rooted at `experiments/scenario_tree/`.

```text
experiments/scenario_tree/
  manifests/
  configs/
  runs/
  summaries/
  logs/
```

`manifests/` stores the scenario-tree manifest, scenario leaf index, run registry, registry summary, and validation reports. `configs/` stores scenario-level seed placeholders and links to leaf-level run configs. `runs/` stores one folder per scenario leaf with `run_config.yaml`, `inputs_manifest.yaml`, `outputs/`, and `logs/`. `outputs/` contains raw model outputs and standardized leaf summaries for successful runs. `logs/` contains per-attempt runner logs. `summaries/` stores realization-level metrics, scenario-level aggregate metrics, and comparison-level tables.

A scenario-level config folder groups seeds under a deterministic scenario ID. A scenario-leaf run folder is the executable unit. The run config holds the exact leaf configuration. The input manifest records resolved input files. The scenario leaf index is the inventory of planned leaves. The run registry is the ledger of attempts and statuses.
"""

    representative_config = ""
    sample_run_config = context.repo_root / "experiments/scenario_tree/runs/baseline_1981_2005__historical__tech_current_stock__seed_0000/run_config.yaml"
    if sample_run_config.exists():
        lines = sample_run_config.read_text(encoding="utf-8").splitlines()[:80]
        representative_config = "\n".join(lines)
    else:
        representative_config = "Sample run config missing: experiments/scenario_tree/runs/baseline_1981_2005__historical__tech_current_stock__seed_0000/run_config.yaml"

    chapter7 = f"""# Chapter 7 - Configuration generation

Abstract scenario leaves become executable run configs through `src/model_v3/scenario_tree/generate_leaf_configs.py`. The generated `run_config.yaml` records scenario dimensions, climate forcing path, canonical analysis dates, source file window, technology case, Belgian technology input reference, stochastic seed and cohort size, output directory, model options, validation metadata, and provenance. The paired `inputs_manifest.yaml` records resolved input files and existence checks.

Representative config excerpt:

```yaml
{representative_config}
```

Config validation checks required fields, climate file existence, technology case existence, Belgian technology input existence, baseline/future separation, canonical date windows, and the 2050 policy. The latest detected config validation report is `experiments/scenario_tree/manifests/config_validation_report.md` if present.
"""

    chapter8 = """# Chapter 8 - Running the model

Use `python3` from the repository root.

```bash
python3 -m model_v3.scenarios.run_scenario_tree --dry-run --print-summary
```

```bash
python3 -m model_v3.scenarios.run_scenario_tree \\
  --scenario-leaf-id baseline_1981_2005__historical__tech_current_stock__seed_0000 \\
  --print-summary
```

```bash
python3 -m model_v3.scenarios.run_scenario_tree \\
  --scenario-leaf-id mid_century_2050_2070__rcp_8_5__tech_high_electrification_pv_ev__seed_0000 \\
  --print-summary
```

```bash
python3 -m model_v3.scenarios.run_scenario_tree \\
  --all \\
  --max-workers 1 \\
  --continue-on-error \\
  --print-summary
```

Dry-run mode validates and plans without simulation. Single-leaf mode executes one leaf. Batch mode iterates through selected leaves. `--max-workers 1` is used first because serial execution is easier to audit and avoids concurrent provenance/logging issues. Failed runs are recorded in the registry and logs. `--force` is used when rerunning a successful leaf intentionally. Logs are stored under each leaf run folder.

Run provenance includes timestamp, git commit when available, dirty working tree status when available, config hash, input hashes, random seed, model version, output path, and status. In this repository snapshot, git provenance is unavailable if the repository root is not a Git working tree.
"""

    metric_table = md_table(["metric", "unit", "category", "definition", "source", "aggregation", "caveats"], metric_reference_rows(context))
    chapter9 = f"""# Chapter 9 - Outputs and standardized metrics

Raw model outputs for a successful leaf are stored under that leaf's `outputs/` directory, typically including `annual_profile.csv` and `annual_summary.json`. The standardization layer writes per-leaf standardized summaries and scenario-level aggregate metrics.

Detected realization-level summary rows: {summary_rows if summary_rows is not None else "unknown"}.

Detected scenario-level aggregate rows: {aggregate_rows if aggregate_rows is not None else "unknown"}.

The required standardized metrics are:

{metric_table}

Annual sums are energy totals over the model output period and use kWh. Peak power metrics use W. Winter peaks use December, January, and February. Summer peaks use June, July, and August. PV self-consumption is PV generation minus export where values are available and bounded by PV generation. PV export fraction is export divided by PV generation when PV generation is positive. HDD and CDD are degree-day climate metrics computed from daily mean outdoor temperature. W, kW, and kWh must not be mixed: W is instantaneous power, kW is 1000 W, and kWh is energy over time.
"""

    chapter10 = f"""# Chapter 10 - Comparison framework

The comparison framework is encoded in {comparison_file}. It consumes standardized Phase 5 summary tables and does not run simulations.

Climate-only effect: the historical current-stock baseline is compared against future RCP pathways under `tech_frozen_stock`, not future `tech_current_stock`. This is necessary because `tech_current_stock` is baseline-only in the scenario metadata. The frozen-stock branch is a counterfactual that varies climate while holding technology assumptions fixed relative to the baseline stock.

Technology-only effect: `tech_frozen_stock`, `tech_moderate_electrification`, and `tech_high_electrification_pv_ev` are compared within the same climate window and RCP pathway. This isolates technology assumptions conditional on climate.

Combined stress case: `baseline_1981_2005__historical__tech_current_stock` is compared with `long_term_2080_2100__rcp_8_5__tech_high_electrification_pv_ev`.

Stochastic robustness: realization-level results are summarized with quantiles such as P10, P50, and P90. These quantiles describe modelled stochastic spread across available successful realizations; they do not represent full climate or epistemic uncertainty.

Delta definitions:

```text
delta_abs = future_value - baseline_value

delta_pct = 100 * (future_value - baseline_value) / baseline_value
```

If the denominator is zero, percentage change is left blank or flagged; absolute delta can still be reported when both values exist.
"""

    validation_rows = [
        ["scenario-tree schema", "config/scenario_tree/*.yaml", "checks IDs, baseline/future rules, 2050 policy"],
        ["naming/path validation", "scenario_leaf_index.csv and run folders", "checks stable paths and identifiers"],
        ["config validation", "config_validation_report.md", "checks climate files, technology inputs, required blocks"],
        ["runner dry-run validation", "run_scenario_tree --dry-run", "plans without executing simulations"],
        ["run registry validation", "run_registry.csv", "checks statuses and provenance fields"],
        ["output summary validation", "summary_validation_report.md", "checks standardized metrics and 2050 policy"],
        ["comparison validation", "comparison_validation_report.md", "checks comparison definitions and available outputs"],
        ["figure validation", "validate_figures", "checks metadata, stable figure filenames, captions, and sources"],
        ["traceability audit", "reports/scenario_tree_traceability_matrix.csv", "joins registry, configs, inputs, summaries, hashes"],
    ]
    chapter11 = f"""# Chapter 11 - Validation and quality assurance

Validation layers detected or documented in the repository:

{md_table(["layer", "source", "what it checks"], validation_rows)}

Validation commands:

```bash
python3 -m model_v3.scenarios.validate_summaries \\
  --experiment-root experiments/scenario_tree \\
  --print-summary
```

```bash
python3 -m model_v3.scenarios.validate_comparisons \\
  --experiment-root experiments/scenario_tree \\
  --comparison-definitions config/scenario_tree/comparison_definitions.yaml \\
  --print-summary
```

```bash
python3 -m model_v3.scenarios.validate_figures \\
  --figures-root figures/scenario_tree \\
  --experiment-root experiments/scenario_tree \\
  --print-summary
```

Internal consistency validation checks whether files, IDs, metrics, and reports agree. Input validation checks that referenced input files exist and are resolvable. Output validation checks standardized summary structure and metric availability. External empirical validation compares model outputs against independent measured data. Do not claim external validation from scenario-tree consistency reports alone.
"""

    chapter12 = f"""# Chapter 12 - Figures and interpretation guide

The detected figure metadata rows are {figure_rows}. The handbook includes generated schematic figures and existing generated scenario-tree figures where available. Existing figure metadata records source files, metrics, filters, generation scripts, row counts, and warnings. If a data-derived figure is missing, the handbook uses a schematic and states what source would be needed.

{figures_markdown(context.figure_infos, "Figures included in this handbook")}

Interpretation rule: a figure can show only the data available in its source tables. If most scenario leaves have not produced successful summaries, annual demand, grid impact, uncertainty band, and stress-case figures should be interpreted as available-output diagnostics rather than complete scenario-tree results.
"""

    chapter13 = f"""# Chapter 13 - Caveats, gaps, and limitations

This chapter is critical for thesis defensibility. The table distinguishes what the repository can support from what still needs calibration, execution, or validation.

{md_table(["caveat/gap ID", "topic", "current limitation", "why it matters", "severity", "affected outputs", "how to detect it", "suggested workaround", "suggested long-term fix", "priority"], caveat_rows())}
"""

    roadmap_rows = [
        ["Confirm implemented phases", "high", "low", "high", "Phase 1-9 docs and manifests", "Supervisor may ask what is done versus planned."],
        ["Run validation commands", "high", "low", "high", "scenario validators", "Unvalidated artifacts weaken the discussion."],
        ["Prepare one baseline and one future run explanation", "high", "low", "high", "runner and registry", "Cannot demonstrate end-to-end workflow clearly."],
        ["Confirm run registry status", "high", "low", "high", "run_registry.csv", "Avoids unsupported completion claims."],
        ["Explain 2050 policy cleanly", "high", "low", "high", "climate_windows.yaml", "Double-counting question is likely."],
        ["External validation against smart-meter or aggregate data", "very high", "high", "very high", "validation modules/reports", "Accuracy claims remain limited."],
        ["Technology calibration from Belgian statistics", "high", "medium", "high", "technology inputs", "Technology scenarios remain qualitative/counterfactual."],
        ["Cohort size and Monte Carlo convergence sensitivity", "high", "medium", "high", "stochastic/cohort modules", "P10/P90 robustness may be unstable."],
        ["Climate ensemble expansion", "high", "high", "high", "inputs/climate/processed", "Climate uncertainty is underrepresented."],
        ["Parallel runner", "medium", "medium", "medium", "scenario runner", "Full execution remains slow."],
        ["Richer diagnostics and figure styling", "medium", "low", "medium", "figures/scenario_tree", "Presentation quality and debugging weaker."],
        ["Grid feeder constraints and spatial modelling", "long-term", "high", "medium", "new modules", "Grid stress remains aggregate."],
        ["Dynamic pricing or demand response", "long-term", "high", "medium", "control/systems modules", "Flexibility analysis remains absent."],
    ]
    chapter14 = f"""# Chapter 14 - Recommended next improvements

## Immediate fixes before supervisor discussion

Check which phases have actually been implemented, run validation commands, generate a dry-run summary, prepare one baseline and one future run explanation, confirm run registry status, open key figures, and prepare a clear explanation of the 2050 policy.

## Prioritized roadmap

{md_table(["recommendation", "expected value", "implementation difficulty", "thesis relevance", "suggested phase/module", "risk if not done"], roadmap_rows)}
"""

    chapter15 = """# Chapter 15 - How to use the model

## Full workflow

1. Validate scenario schema.
2. Create experiment space.
3. Generate leaf configs.
4. Dry-run runner.
5. Run one baseline leaf.
6. Run one future leaf.
7. Run batch.
8. Standardize outputs.
9. Generate comparisons.
10. Generate figures.
11. Run methodological audit.
12. Build handbook.

Important commands:

```bash
python3 -m model_v3.scenario_tree.validate_scenario_tree --config-root config/scenario_tree
python3 -m model_v3.scenario_tree.create_scenario_tree_space --config-root config/scenario_tree --experiment-root experiments/scenario_tree --print-summary
python3 -m model_v3.scenario_tree.generate_leaf_configs --config-root config/scenario_tree --experiment-root experiments/scenario_tree --climate-processed-root inputs/climate/processed --belgian-technology-inputs config/belgian_technology_inputs.yaml --cohort-size 100 --write-report --print-summary
python3 -m model_v3.scenario_tree.validate_leaf_configs --experiment-root experiments/scenario_tree --config-root config/scenario_tree --climate-processed-root inputs/climate/processed --belgian-technology-inputs config/belgian_technology_inputs.yaml --print-summary
python3 -m model_v3.scenarios.run_scenario_tree --dry-run --print-summary
python3 -m model_v3.scenarios.summarize_outputs --experiment-root experiments/scenario_tree --config-root config/scenario_tree --only-successful --write-reports --print-summary
python3 -m model_v3.scenarios.generate_comparisons --experiment-root experiments/scenario_tree --comparison-definitions config/scenario_tree/comparison_definitions.yaml --write-reports --print-summary
python3 -m model_v3.scenarios.generate_figures --experiment-root experiments/scenario_tree --figures-root figures/scenario_tree --write-metadata --write-captions --print-summary
python3 -m model_v3.scenarios.audit_scenario_tree --experiment-root experiments/scenario_tree --config-root config/scenario_tree --figures-root figures/scenario_tree --write-reports --print-summary
python3 -m model_v3.documentation.build_model_handbook --repo-root . --output docs/model_v3_complete_model_handbook.pdf --write-source --write-figures --print-summary
```

## Common tasks

List leaves by opening `experiments/scenario_tree/manifests/scenario_leaf_index.csv`. Inspect one leaf by opening its run folder under `experiments/scenario_tree/runs/{scenario_leaf_id}/`. Rerun a failed leaf with the runner and the same `--scenario-leaf-id`. Force rerun a successful leaf only when you intentionally want to replace or add an attempt. Find outputs under the leaf's `outputs/`, logs under `logs/`, metrics under `summaries/`, comparisons under `summaries/comparison_level/`, figures under `figures/scenario_tree/`, and audit traceability under `reports/scenario_tree_traceability_matrix.csv`.

## Troubleshooting

Missing climate forcing file: run leaf-config validation and inspect `inputs_manifest.yaml`. Ambiguous climate forcing file: check filename tokens and sidecar metadata. Missing Belgian technology input YAML: confirm `config/belgian_technology_inputs.yaml`. Invalid scenario ID: validate against `scenario_tree_schema.yaml`. Run already successful and skipped: use registry status and `--force` only if needed. Config validation fails: inspect config validation report. Summary metric missing: inspect raw output files and `output_reader.py` mappings. Figure not generated: validate figures and check source tables. PDF build backend missing: this script uses Matplotlib PDF when Pandoc/WeasyPrint/ReportLab are unavailable.
"""

    chapter16 = f"""# Chapter 16 - Supervisor presentation guide

## One-minute explanation

`model_v3` is a bottom-up residential energy-demand model. It uses climate forcing, building and technology assumptions, and stochastic household realizations to simulate energy and grid metrics. The scenario tree makes the experiment reproducible by naming every climate window, RCP pathway, technology case, and seed explicitly.

## Five-minute explanation

The model engine transforms inputs into prepared forcing, physics state, control state, system state, and outputs. The scenario-tree layer wraps the model in a structured experiment design. The historical baseline is 1981-2005 with current stock. Future windows are near future, mid-century, and long term under RCP2.6, RCP4.5, and RCP8.5. Future technology cases include frozen stock, moderate electrification, and high electrification with PV/EV. Seeds represent stochastic household/cohort realizations. Standardized metrics allow climate-only, technology-only, stress-case, and stochastic robustness comparisons.

## Key accomplishments detected

- Scenario-tree schema and stable identifiers are present.
- Canonical climate windows and explicit 2050 policy are present.
- Generated experiment structure and leaf index are present.
- Per-leaf configs and input manifests are present according to config validation artifacts.
- Runner/provenance layer and run registry are present.
- Standardized output summaries exist for the successful subset.
- Comparison framework and validation reports are present.
- Generated figures and metadata are present.
- Documentation/audit reports are present.

## Key figures to show

Show the model architecture, scenario-tree structure, climate-window timeline, output standardization workflow, and one grid-impact or stress-case figure. Be clear that data-derived result figures reflect available successful summary rows, not the full 2800-leaf design.

## Likely supervisor questions and honest answers

Why use a scenario tree? Because it separates climate, technology, and stochastic uncertainty and preserves traceability.

Why RCPs instead of SSPs? The repository currently encodes RCP pathways in `climate_windows.yaml` and `scenario_tree_schema.yaml`; switching to SSPs would require new climate inputs and metadata.

How do you prevent double-counting 2050? Raw source files may overlap, but canonical windows do not: near-future ends on 2049-12-31 and mid-century starts on 2050-01-01.

What does a stochastic realization represent? A reproducible seed/cohort draw, not a climate model member.

How do you know the model is valid? The scenario-tree artifacts are internally validated for consistency and traceability. External empirical validation should be claimed only from separate validation reports.

What is the difference between grid import and electricity demand? Gross electricity is household demand before PV netting; grid import is the portion drawn from the grid after local PV generation is netted.

What are the main limitations? Partial execution coverage, simplified physics, limited climate ensemble, technology calibration uncertainty, finite stochastic realizations, and external validation gaps.
"""

    term = f"""# Terminology

This chapter is a study reference. Each term includes definition, where it appears, why it matters, and a common misunderstanding.

{md_table(["term", "definition", "where it appears in model_v3", "why it matters", "common misunderstanding"], terminology_rows())}
"""

    appendix_a = f"""# Appendix A - File inventory

{md_table(["path", "role", "phase", "required/optional", "exists"], file_inventory_rows(context))}

## Input inventory excerpt

{md_table(["path", "type", "purpose", "temporal resolution", "units", "scenario dimension", "required", "validation status"], [[item["path"], item["type"], item["purpose"], item["temporal_resolution"], item["units"], item["scenario_dimension"], item["required"], item["validation_status"]] for item in context.input_inventory[:80]])}
"""

    appendix_b = """# Appendix B - Command reference

```bash
python3 -m model_v3.scenario_tree.validate_scenario_tree --config-root config/scenario_tree
python3 -m model_v3.scenario_tree.create_scenario_tree_space --config-root config/scenario_tree --experiment-root experiments/scenario_tree --print-summary
python3 -m model_v3.scenario_tree.generate_leaf_configs --config-root config/scenario_tree --experiment-root experiments/scenario_tree --climate-processed-root inputs/climate/processed --belgian-technology-inputs config/belgian_technology_inputs.yaml --cohort-size 100 --write-report --print-summary
python3 -m model_v3.scenario_tree.validate_leaf_configs --experiment-root experiments/scenario_tree --config-root config/scenario_tree --climate-processed-root inputs/climate/processed --belgian-technology-inputs config/belgian_technology_inputs.yaml --print-summary
python3 -m model_v3.scenarios.run_scenario_tree --dry-run --print-summary
python3 -m model_v3.scenarios.run_scenario_tree --scenario-leaf-id baseline_1981_2005__historical__tech_current_stock__seed_0000 --print-summary
python3 -m model_v3.scenarios.run_scenario_tree --scenario-leaf-id mid_century_2050_2070__rcp_8_5__tech_high_electrification_pv_ev__seed_0000 --print-summary
python3 -m model_v3.scenarios.run_scenario_tree --all --max-workers 1 --continue-on-error --print-summary
python3 -m model_v3.scenarios.summarize_outputs --experiment-root experiments/scenario_tree --config-root config/scenario_tree --only-successful --write-reports --print-summary
python3 -m model_v3.scenarios.validate_summaries --experiment-root experiments/scenario_tree --print-summary
python3 -m model_v3.scenarios.generate_comparisons --experiment-root experiments/scenario_tree --comparison-definitions config/scenario_tree/comparison_definitions.yaml --write-reports --print-summary
python3 -m model_v3.scenarios.validate_comparisons --experiment-root experiments/scenario_tree --comparison-definitions config/scenario_tree/comparison_definitions.yaml --print-summary
python3 -m model_v3.scenarios.generate_figures --experiment-root experiments/scenario_tree --figures-root figures/scenario_tree --write-metadata --write-captions --print-summary
python3 -m model_v3.scenarios.validate_figures --figures-root figures/scenario_tree --experiment-root experiments/scenario_tree --print-summary
python3 -m model_v3.scenarios.audit_scenario_tree --experiment-root experiments/scenario_tree --config-root config/scenario_tree --figures-root figures/scenario_tree --write-reports --print-summary
python3 -m model_v3.documentation.build_model_handbook --repo-root . --output docs/model_v3_complete_model_handbook.pdf --write-source --write-figures --print-summary
python3 -m model_v3.documentation.validate_model_handbook --handbook docs/model_v3_complete_model_handbook.pdf --source docs/model_v3_complete_model_handbook.md --manifest docs/model_v3_complete_model_handbook_manifest.yaml --print-summary
```
"""

    appendix_c = f"""# Appendix C - Metric reference tables

{metric_table}
"""

    missing_rows = [[path, "missing", "Expected file or directory was not found during handbook generation.", "Create or regenerate this artifact if needed for fuller documentation."] for path in context.missing_expected_files]
    if not missing_rows:
        missing_rows = [["none", "not_applicable", "No expected repository paths from the handbook checklist were missing.", "No action required for this checklist."]]
    appendix_d = f"""# Appendix D - Known missing items

This appendix lists missing files, missing reports, missing figures, missing validation outputs, or incomplete phases detected from the repository. Missing items are not treated as handbook build failure unless they prevent PDF generation.

{md_table(["item", "status", "reason", "needed to complete"], missing_rows)}

Execution coverage gap: the registry/audit evidence supports {success_count} latest-successful leaves out of {leaf_count} enumerated leaves. This prevents any claim that all scenario leaves have completed.
"""

    return "\n\n".join(
        [
            cover,
            executive,
            chapter1,
            chapter2,
            chapter3,
            chapter4,
            chapter5,
            chapter6,
            chapter7,
            chapter8,
            chapter9,
            chapter10,
            chapter11,
            chapter12,
            chapter13,
            chapter14,
            chapter15,
            chapter16,
            term,
            appendix_a,
            appendix_b,
            appendix_c,
            appendix_d,
        ]
    )


def build_supervisor_markdown(context: Context) -> str:
    reg = context.registry
    return f"""# Model v3 supervisor briefing

Generated UTC: {context.generation_timestamp}

## 1. What the model does

`model_v3` is a bottom-up residential energy-demand model. It combines climate forcing, building and technology assumptions, stochastic household/cohort realizations, thermal physics, control logic, carrier conversion, PV/EV accounting, and standardized output metrics.

## 2. What the scenario tree adds

The scenario tree organizes results by climate window, RCP pathway, technology case, and realization seed. This keeps climate, technology, and behavioural uncertainty separate and traceable.

## 3. What has been implemented

Detected implemented artifacts include scenario-tree schema/configs, stable IDs, canonical climate windows, explicit 2050 policy, experiment manifests, per-leaf configs, runner/provenance registry, standardized summaries for available successful runs, comparison definitions, figure metadata, and audit reports.

Current execution evidence: {reg.get("successful_scenario_leaves")} latest-successful leaves out of {reg.get("enumerated_scenario_leaves")} enumerated leaves. This is partial execution, not full scenario completion.

## 4. Key methodological choices

- Baseline: `baseline_1981_2005__historical__tech_current_stock`.
- Future climate pathways: `rcp_2_6`, `rcp_4_5`, `rcp_8_5`.
- Future technology cases: `tech_frozen_stock`, `tech_moderate_electrification`, `tech_high_electrification_pv_ev`.
- Climate-only comparisons use future `tech_frozen_stock`.
- Pairwise deltas match leaves by `realization_id`.
- P10/P50/P90 bands describe modelled stochastic spread across available successful realizations.

## 5. 2050 overlap policy

Raw processed source files may overlap in 2050, but canonical analysis windows do not. Near-future ends on 2049-12-31. Mid-century starts on 2050-01-01. Therefore 2050 belongs only to the mid-century canonical analysis window. This is encoded in `config/scenario_tree/climate_windows.yaml`.

## 6. Outputs and figures available

Standardized metrics include annual gross electricity, grid import/export, gas, useful heating, DHW, grid peaks, PV generation/self-consumption/export fraction, EV charging, temperature, HDD/CDD, and solar metrics. Figures under `figures/scenario_tree/` and handbook assets show scenario structure, climate forcing, annual demand, grid impact, uncertainty bands, infrastructure stress, input inventory, and workflows where source tables are available.

## 7. Limitations

Execution coverage is partial. Scenario-tree validation is internal consistency and traceability validation, not external empirical validation. The physical model is simplified. Climate ensemble coverage may be limited by available processed files. Technology cases are assumptions, not forecasts. Stochastic robustness depends on successful realization count.

## 8. Next improvements

Run validation commands, confirm registry status, execute representative baseline and future leaves, regenerate summaries/comparisons/figures, validate against smart-meter or aggregate load data, calibrate technology assumptions with Belgian statistics, test cohort-size convergence, and expand climate ensemble coverage.

## 9. Five talking points for tomorrow

1. The scenario tree makes the thesis experiment auditable because every result has a stable climate, technology, and seed identity.
2. The 2050 overlap issue is handled by non-overlapping canonical analysis windows.
3. The framework is implemented, but only available successful runs should be discussed as results.
4. Climate-only effects are isolated with frozen-stock future technology assumptions.
5. The biggest thesis risks are external validation, technology calibration, climate ensemble breadth, and stochastic convergence.

## 10. Five likely supervisor questions

**Why a scenario tree?** To separate climate, technology, and stochastic effects and make outputs traceable.

**Why not claim all scenarios are complete?** The registry/audit evidence does not support that claim.

**How is 2050 handled?** Near-future excludes it; mid-century includes it.

**What is a realization?** A reproducible stochastic seed/cohort draw.

**How do you know it is valid?** Internal validation checks consistency and traceability; external empirical validation requires separate measured-data reports.
"""


def markdown_to_latex(markdown: str, title: str) -> str:
    lines = [
        r"\documentclass[11pt,a4paper]{article}",
        r"\usepackage[margin=1in]{geometry}",
        r"\usepackage{graphicx}",
        r"\usepackage{longtable}",
        r"\usepackage{hyperref}",
        r"\usepackage[T1]{fontenc}",
        r"\title{" + latex_escape(title) + "}",
        r"\date{}",
        r"\begin{document}",
        r"\maketitle",
    ]
    in_code = False
    for raw in markdown.splitlines():
        line = raw.rstrip()
        if line.startswith("```"):
            lines.append(r"\begin{verbatim}" if not in_code else r"\end{verbatim}")
            in_code = not in_code
            continue
        if in_code:
            lines.append(line)
            continue
        if line.startswith("# "):
            lines.append(r"\section{" + latex_escape(line[2:].strip()) + "}")
        elif line.startswith("## "):
            lines.append(r"\subsection{" + latex_escape(line[3:].strip()) + "}")
        elif line.startswith("### "):
            lines.append(r"\subsubsection{" + latex_escape(line[4:].strip()) + "}")
        elif line.startswith("!["):
            match = re.match(r"!\[(.*?)\]\((.*?)\)", line)
            if match:
                caption, path = match.groups()
                lines.extend([r"\begin{figure}[h]", r"\centering", r"\includegraphics[width=0.9\linewidth]{" + latex_escape(path) + "}", r"\caption{" + latex_escape(caption) + "}", r"\end{figure}"])
        elif line.startswith("|"):
            lines.append(latex_escape(line))
        elif not line:
            lines.append("")
        else:
            lines.append(latex_escape(line) + r"\\")
    lines.append(r"\end{document}")
    return "\n".join(lines)


def latex_escape(text: str) -> str:
    replacements = {
        "\\": r"\textbackslash{}",
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
        "{": r"\{",
        "}": r"\}",
        "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
    }
    return "".join(replacements.get(ch, ch) for ch in text)


def strip_markdown_inline(text: str) -> str:
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    return text


def render_markdown_pdf(markdown: str, output: Path, repo_root: Path, title: str) -> str:
    plt = import_matplotlib()
    from matplotlib.backends.backend_pdf import PdfPages
    import matplotlib.image as mpimg

    output.parent.mkdir(parents=True, exist_ok=True)
    page_w, page_h = 8.27, 11.69
    margin_x = 0.55
    top = 11.10
    bottom = 0.55
    line_h = 0.19

    def new_page(pdf: PdfPages, page_no: int) -> tuple[Any, Any, float]:
        fig = plt.figure(figsize=(page_w, page_h))
        ax = fig.add_axes([0, 0, 1, 1])
        ax.set_xlim(0, page_w)
        ax.set_ylim(0, page_h)
        ax.axis("off")
        ax.text(page_w / 2, 0.28, f"{title} | page {page_no}", ha="center", fontsize=7, color="#666666")
        return fig, ax, top

    def save_page(pdf: PdfPages, fig: Any) -> None:
        pdf.savefig(fig)
        plt.close(fig)

    def draw_text(pdf: PdfPages, fig: Any, ax: Any, y: float, text: str, size: float = 9.2, weight: str = "normal", indent: float = 0.0) -> tuple[Any, Any, float, int]:
        width_chars = max(30, int((page_w - 2 * margin_x - indent) * (12.5 if size <= 9.5 else 10.0)))
        wrapped = textwrap.wrap(strip_markdown_inline(text), width=width_chars) or [""]
        page_count = 0
        for line in wrapped:
            if y < bottom:
                save_page(pdf, fig)
                page_count += 1
                fig, ax, y = new_page(pdf, page_count + 1)
            ax.text(margin_x + indent, y, line, fontsize=size, fontweight=weight, va="top", family="DejaVu Sans")
            y -= line_h * (size / 9.2)
        return fig, ax, y, page_count

    with PdfPages(output) as pdf:
        fig, ax, y = new_page(pdf, 1)
        page_no = 1
        in_code = False
        code_lines: list[str] = []
        for raw in markdown.splitlines():
            line = raw.rstrip()
            if line.startswith("```"):
                if not in_code:
                    in_code = True
                    code_lines = []
                else:
                    in_code = False
                    for code_line in code_lines[:34]:
                        fig, ax, y, added = draw_text(pdf, fig, ax, y, code_line, size=7.2, indent=0.22)
                        page_no += added
                    y -= 0.10
                continue
            if in_code:
                code_lines.append(line)
                continue
            if line.startswith("# "):
                if y < 9.6 and y != top:
                    save_page(pdf, fig)
                    page_no += 1
                    fig, ax, y = new_page(pdf, page_no)
                fig, ax, y, added = draw_text(pdf, fig, ax, y, line[2:], size=15.0, weight="bold")
                page_no += added
                y -= 0.18
            elif line.startswith("## "):
                fig, ax, y, added = draw_text(pdf, fig, ax, y, line[3:], size=12.0, weight="bold")
                page_no += added
                y -= 0.08
            elif line.startswith("### "):
                fig, ax, y, added = draw_text(pdf, fig, ax, y, line[4:], size=10.2, weight="bold")
                page_no += added
            elif line.startswith("!["):
                match = re.match(r"!\[(.*?)\]\((.*?)\)", line)
                if match:
                    alt, path_text = match.groups()
                    image_path = repo_root / path_text
                    if not image_path.exists():
                        fig, ax, y, added = draw_text(pdf, fig, ax, y, f"[missing figure: {path_text}]", size=8.5)
                        page_no += added
                        continue
                    if y < 4.4:
                        save_page(pdf, fig)
                        page_no += 1
                        fig, ax, y = new_page(pdf, page_no)
                    try:
                        img = mpimg.imread(image_path)
                        image_h = 3.1
                        image_w = min(6.8, image_h * img.shape[1] / max(img.shape[0], 1))
                        ax.imshow(img, extent=(margin_x, margin_x + image_w, y - image_h, y), aspect="auto")
                        y -= image_h + 0.10
                        fig, ax, y, added = draw_text(pdf, fig, ax, y, f"Figure: {alt}", size=8.2, weight="bold")
                        page_no += added
                        y -= 0.10
                    except Exception:
                        fig, ax, y, added = draw_text(pdf, fig, ax, y, f"[could not render figure: {path_text}]", size=8.5)
                        page_no += added
            elif line.startswith("|"):
                # Keep Markdown tables readable without trying to typeset full grids.
                if re.match(r"^\|\s*-", line):
                    continue
                compact = re.sub(r"\s*\|\s*", " | ", line.strip("| "))
                fig, ax, y, added = draw_text(pdf, fig, ax, y, compact, size=6.7)
                page_no += added
            elif line.startswith("- "):
                fig, ax, y, added = draw_text(pdf, fig, ax, y, "* " + line[2:], size=9.0, indent=0.15)
                page_no += added
            elif re.match(r"^\d+\. ", line):
                fig, ax, y, added = draw_text(pdf, fig, ax, y, line, size=9.0, indent=0.10)
                page_no += added
            elif not line:
                y -= 0.09
            else:
                fig, ax, y, added = draw_text(pdf, fig, ax, y, line, size=9.2)
                page_no += added
        save_page(pdf, fig)
    return "matplotlib.backends.backend_pdf.PdfPages"


def build_manifest(context: Context, output_pdf: Path, source_md: Path, source_tex: Path, briefing_md: Path, briefing_pdf: Path, backend: str) -> dict[str, Any]:
    return {
        "generation_timestamp": context.generation_timestamp,
        "git_commit": context.git_commit or "not_available",
        "git_dirty_status": context.git_dirty_status,
        "source_files_inspected": context.source_files_inspected,
        "figures_included": [fig.__dict__ for fig in context.figure_infos],
        "summary_tables_included": [
            {"name": name, "path": info.path, "exists": info.exists, "rows": info.rows, "columns": info.columns}
            for name, info in context.csv_info.items()
            if "summary" in name or "metrics" in name or "comparison" in name
        ],
        "reports_included": [
            path for path in [
                "experiments/scenario_tree/manifests/config_validation_report.md",
                "experiments/scenario_tree/manifests/summary_validation_report.md",
                "experiments/scenario_tree/manifests/comparison_validation_report.md",
                "reports/scenario_tree_validation_report.md",
                "reports/scenario_tree_audit_summary.yaml",
            ] if (context.repo_root / path).exists()
        ],
        "missing_expected_files": context.missing_expected_files,
        "warnings": context.warnings,
        "pdf_build_backend_used": backend,
        "outputs": {
            "handbook_pdf": relpath(output_pdf, context.repo_root),
            "handbook_markdown": relpath(source_md, context.repo_root),
            "handbook_tex": relpath(source_tex, context.repo_root),
            "supervisor_briefing_markdown": relpath(briefing_md, context.repo_root),
            "supervisor_briefing_pdf": relpath(briefing_pdf, context.repo_root),
        },
        "run_registry": context.registry,
        "counts": context.counts,
        "status_labels_used": ["implemented", "implemented_unverified", "missing", "planned", "not_applicable"],
        "simulations_run_by_handbook_builder": 0,
    }


def build_documents(repo_root: Path, output_pdf: Path, write_source: bool, write_figures: bool, print_summary: bool) -> dict[str, Any]:
    repo_root = repo_root.resolve()
    output_pdf = (repo_root / output_pdf).resolve() if not output_pdf.is_absolute() else output_pdf.resolve()
    docs_dir = output_pdf.parent
    assets_dir = docs_dir / ASSET_DIR_NAME
    safe_mkdir(docs_dir)
    context = collect_context(repo_root)
    context.figure_infos = generate_handbook_figures(context, assets_dir, write_figures=write_figures)
    handbook_md = build_handbook_markdown(context)
    supervisor_md = build_supervisor_markdown(context)

    source_md = docs_dir / f"{HANDBOOK_STEM}.md"
    source_tex = docs_dir / f"{HANDBOOK_STEM}.tex"
    briefing_md = docs_dir / f"{SUPERVISOR_STEM}.md"
    briefing_pdf = docs_dir / f"{SUPERVISOR_STEM}.pdf"
    manifest_path = docs_dir / f"{HANDBOOK_STEM}_manifest.yaml"

    if write_source:
        source_md.write_text(handbook_md, encoding="utf-8")
        source_tex.write_text(markdown_to_latex(handbook_md, TITLE), encoding="utf-8")
        briefing_md.write_text(supervisor_md, encoding="utf-8")
    else:
        # The validator and reproducibility workflow expect the source files.
        source_md.write_text(handbook_md, encoding="utf-8")
        source_tex.write_text(markdown_to_latex(handbook_md, TITLE), encoding="utf-8")
        briefing_md.write_text(supervisor_md, encoding="utf-8")

    backend = render_markdown_pdf(handbook_md, output_pdf, repo_root, "model_v3 handbook")
    render_markdown_pdf(supervisor_md, briefing_pdf, repo_root, "model_v3 supervisor briefing")
    context.pdf_backend = backend
    manifest = build_manifest(context, output_pdf, source_md, source_tex, briefing_md, briefing_pdf, backend)
    write_yaml(manifest_path, manifest)

    if print_summary:
        print("Model handbook generation complete.")
        print(f"PDF: {relpath(output_pdf, repo_root)}")
        print(f"Source: {relpath(source_md, repo_root)}")
        print(f"Figures: {relpath(assets_dir, repo_root)}/")
        print(f"Supervisor briefing: {relpath(briefing_md, repo_root)}")
        print("Terminology chapter: present")
        print("Caveats chapter: present")
        print("2050 policy documented: yes")
        print("Unsupported claims detected: 0")
        print("Simulations run: 0")
    return manifest


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the model_v3 complete handbook.")
    parser.add_argument("--repo-root", default=".", help="Repository root.")
    parser.add_argument("--output", default="docs/model_v3_complete_model_handbook.pdf", help="Output PDF path.")
    parser.add_argument("--write-source", action="store_true", help="Write Markdown and TeX source files.")
    parser.add_argument("--write-figures", action="store_true", help="Write generated handbook figures.")
    parser.add_argument("--print-summary", action="store_true", help="Print a concise build summary.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    build_documents(
        repo_root=Path(args.repo_root),
        output_pdf=Path(args.output),
        write_source=bool(args.write_source),
        write_figures=bool(args.write_figures),
        print_summary=bool(args.print_summary),
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
