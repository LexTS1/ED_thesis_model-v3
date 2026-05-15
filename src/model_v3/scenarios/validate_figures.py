"""Validate reproducible scenario-tree thesis figure artifacts."""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Any

import pandas as pd

from model_v3.scenarios.generate_figures import (
    DEFAULT_EXPERIMENT_ROOT,
    DEFAULT_FIGURES_ROOT,
    REPO_ROOT,
    REQUIRED_DIRECTORIES,
    figure_specs,
    resolve_cli_path,
)


TIMESTAMP_PATTERNS = [
    re.compile(r"\d{8}T\d{6}"),
    re.compile(r"\d{4}-\d{2}-\d{2}"),
    re.compile(r"\d{14}"),
]


class FigureValidationError(RuntimeError):
    """Raised when figure artifacts fail validation."""


def _relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path.resolve())


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if pd.isna(value):
        return False
    return str(value).strip().lower() in {"true", "1", "yes"}


def _path_from_metadata(text: str) -> Path:
    path = Path(str(text))
    return path if path.is_absolute() else REPO_ROOT / path


def _has_timestamp(path: Path) -> bool:
    return any(pattern.search(path.name) for pattern in TIMESTAMP_PATTERNS)


def _allowed_source(path: Path, experiment_root: Path) -> bool:
    resolved = path.resolve()
    allowed_roots = [
        (experiment_root / "summaries").resolve(),
        (experiment_root / "manifests").resolve(),
        (REPO_ROOT / "config" / "scenario_tree").resolve(),
    ]
    return any(resolved == root or root in resolved.parents for root in allowed_roots)


def _read_captions(path: Path) -> set[str]:
    if not path.exists():
        raise FigureValidationError(f"Missing caption draft file: {path}")
    ids: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("### "):
            ids.add(line.removeprefix("### ").strip())
    return ids


def _climate_policy_from_summaries(experiment_root: Path) -> tuple[bool, bool]:
    metrics_path = experiment_root / "summaries" / "realization_level" / "scenario_leaf_metrics.csv"
    if not metrics_path.exists():
        raise FigureValidationError(f"Missing realization metrics for climate policy validation: {metrics_path}")
    frame = pd.read_csv(metrics_path)
    if "climate_includes_2050" not in frame.columns:
        raise FigureValidationError("scenario_leaf_metrics.csv missing climate_includes_2050 column.")
    near = frame[frame["climate_window_id"] == "near_future_2030_2049"]
    mid = frame[frame["climate_window_id"] == "mid_century_2050_2070"]
    near_includes = bool(near["climate_includes_2050"].map(_truthy).any()) if not near.empty else False
    mid_includes = bool(mid["climate_includes_2050"].map(_truthy).any()) if not mid.empty else True
    return near_includes, mid_includes


def validate_figures(*, figures_root: Path, experiment_root: Path, require_pdf: bool = True) -> dict[str, Any]:
    errors: list[str] = []

    for directory in REQUIRED_DIRECTORIES:
        path = figures_root / directory
        if not path.exists() or not path.is_dir():
            errors.append(f"Missing required figure directory: {path}")

    specs = figure_specs()
    required_png = [figures_root / spec.category / f"{spec.filename}.png" for spec in specs]
    required_pdf = [figures_root / spec.category / f"{spec.filename}.pdf" for spec in specs]
    all_required = required_png + (required_pdf if require_pdf else [])
    for path in all_required:
        if not path.exists():
            errors.append(f"Missing required figure file: {path}")
        if " " in path.name:
            errors.append(f"Figure filename contains spaces: {path.name}")
        if _has_timestamp(path):
            errors.append(f"Figure filename appears timestamped: {path.name}")

    discovered = list(figures_root.glob("*/*.png")) + list(figures_root.glob("*/*.pdf"))
    for path in discovered:
        if " " in path.name:
            errors.append(f"Generated figure filename contains spaces: {path.name}")
        if _has_timestamp(path):
            errors.append(f"Generated figure filename appears timestamped: {path.name}")

    metadata_path = figures_root / "metadata" / "figure_metadata.csv"
    if not metadata_path.exists():
        errors.append(f"Missing figure metadata CSV: {metadata_path}")
        metadata = pd.DataFrame()
    else:
        metadata = pd.read_csv(metadata_path).fillna("")

    required_columns = [
        "figure_id",
        "figure_file_png",
        "figure_file_pdf",
        "source_data_files",
        "metrics_used",
        "caption_id",
        "script",
        "status",
    ]
    for column in required_columns:
        if not metadata.empty and column not in metadata.columns:
            errors.append(f"Figure metadata missing required column: {column}")

    expected_ids = {spec.figure_id for spec in specs}
    if not metadata.empty and "figure_id" in metadata.columns:
        ids = set(metadata["figure_id"].astype(str))
        missing = sorted(expected_ids - ids)
        extra = sorted(ids - expected_ids)
        duplicate_count = int(metadata["figure_id"].duplicated().sum())
        if missing:
            errors.append(f"Metadata missing figure ID(s): {', '.join(missing)}")
        if extra:
            errors.append(f"Metadata contains unexpected figure ID(s): {', '.join(extra)}")
        if duplicate_count:
            errors.append(f"Metadata contains duplicate figure ID rows: {duplicate_count}")
        if len(metadata) != len(expected_ids):
            errors.append(f"Metadata row count {len(metadata)} does not match required figure count {len(expected_ids)}.")

    if not metadata.empty:
        for _, row in metadata.iterrows():
            for col in ["figure_file_png", "figure_file_pdf"]:
                if not row.get(col):
                    errors.append(f"Metadata row {row.get('figure_id')} has empty {col}.")
                    continue
                path = _path_from_metadata(str(row[col]))
                if not path.exists():
                    errors.append(f"Metadata row {row.get('figure_id')} references missing figure file: {path}")
            source_text = str(row.get("source_data_files", ""))
            if not source_text:
                errors.append(f"Metadata row {row.get('figure_id')} has no source data files.")
            for source in [item for item in source_text.split(";") if item]:
                source_path = _path_from_metadata(source)
                if not source_path.exists():
                    errors.append(f"Metadata row {row.get('figure_id')} references missing source data file: {source_path}")
                elif not _allowed_source(source_path, experiment_root):
                    errors.append(f"Metadata row {row.get('figure_id')} uses source outside approved Phase 5/6/config inputs: {source_path}")
            if str(row.get("script", "")) != "model_v3.scenarios.generate_figures":
                errors.append(f"Metadata row {row.get('figure_id')} has unexpected script: {row.get('script')}")

    captions_path = figures_root / "thesis_caption_drafts.md"
    try:
        caption_ids = _read_captions(captions_path)
        missing_captions = sorted(expected_ids - caption_ids)
        if missing_captions:
            errors.append(f"Caption draft missing figure ID(s): {', '.join(missing_captions)}")
    except FigureValidationError as exc:
        errors.append(str(exc))
        caption_ids = set()

    near_includes, mid_includes = _climate_policy_from_summaries(experiment_root)
    if near_includes:
        errors.append("Near-future climate rows include 2050; expected no.")
    if not mid_includes:
        errors.append("Mid-century climate rows do not include 2050; expected yes.")

    if errors:
        raise FigureValidationError("\n".join(errors))

    return {
        "figures_checked": len(expected_ids),
        "png_files_present": True,
        "pdf_files_present": require_pdf,
        "metadata_rows": int(len(metadata)),
        "caption_drafts": int(len(caption_ids)),
        "manual_spreadsheet_dependencies": 0,
        "near_future_includes_2050": near_includes,
        "mid_century_includes_2050": mid_includes,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--figures-root", default=str(DEFAULT_FIGURES_ROOT))
    parser.add_argument("--experiment-root", default=str(DEFAULT_EXPERIMENT_ROOT))
    parser.add_argument("--no-require-pdf", action="store_true")
    parser.add_argument("--print-summary", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    figures_root = resolve_cli_path(args.figures_root)
    experiment_root = resolve_cli_path(args.experiment_root)
    summary = validate_figures(figures_root=figures_root, experiment_root=experiment_root, require_pdf=not args.no_require_pdf)
    if args.print_summary:
        print("Figure validation passed.")
        print(f"Figures checked: {summary['figures_checked']}")
        print(f"PNG files present: {'yes' if summary['png_files_present'] else 'no'}")
        print(f"PDF files present: {'yes' if summary['pdf_files_present'] else 'no'}")
        print(f"Metadata rows: {summary['metadata_rows']}")
        print(f"Caption drafts: {summary['caption_drafts']}")
        print(f"Manual spreadsheet dependencies: {summary['manual_spreadsheet_dependencies']}")
        print(f"Near-future includes 2050: {'yes' if summary['near_future_includes_2050'] else 'no'}")
        print(f"Mid-century includes 2050: {'yes' if summary['mid_century_includes_2050'] else 'no'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
