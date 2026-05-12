"""Resolve processed climate forcing files for scenario-tree leaves."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from .validate_scenario_tree import BASELINE_PATHWAY_ID, BASELINE_WINDOW_ID, REQUIRED_RCP_PATHWAYS, REPO_ROOT


class ClimateForcingResolutionError(ValueError):
    """Raised when a scenario leaf cannot be mapped to one climate forcing CSV."""


_EXPLICIT_FORCING_KEYS = (
    "climate_forcing_file",
    "forcing_file",
    "processed_climate_file",
    "processed_climate_forcing_file",
    "processed_file",
)


def _window_map(climate_windows: dict[str, Any]) -> dict[str, Any]:
    windows = climate_windows.get("climate_windows", climate_windows)
    if not isinstance(windows, dict):
        raise ClimateForcingResolutionError("climate_windows metadata must contain a mapping.")
    return windows


def get_climate_window(climate_window_id: str, climate_windows: dict[str, Any]) -> dict[str, Any]:
    """Return metadata for one climate window."""

    windows = _window_map(climate_windows)
    window = windows.get(climate_window_id)
    if not isinstance(window, dict):
        raise ClimateForcingResolutionError(f"Unknown climate_window_id {climate_window_id!r}.")
    return window


def window_alias(climate_window_id: str) -> str:
    """Return the processed-file window token used by the climate pipeline."""

    match = re.fullmatch(r"(.+)_([0-9]{4})_([0-9]{4})", climate_window_id)
    if match:
        return match.group(1)
    return climate_window_id


def window_label(climate_window_id: str, window: dict[str, Any]) -> str:
    """Return a compact human-readable climate-window label."""

    explicit = window.get("label")
    if isinstance(explicit, str) and explicit:
        return explicit
    canonical_start = str(window.get("canonical_start", ""))
    canonical_end = str(window.get("canonical_end", ""))
    years = ""
    if len(canonical_start) >= 4 and len(canonical_end) >= 4:
        years = f" {canonical_start[:4]}-{canonical_end[:4]}"
    labels = {
        "baseline": "baseline",
        "near_future": "near-future",
        "mid_century": "mid-century",
        "long_term": "long-term",
    }
    return f"{labels.get(window_alias(climate_window_id), window_alias(climate_window_id).replace('_', ' '))}{years}"


def _pathway_variants(climate_pathway_id: str) -> set[str]:
    variants = {climate_pathway_id}
    match = re.fullmatch(r"rcp_([0-9])_([0-9])", climate_pathway_id)
    if match:
        variants.add(f"rcp{match.group(1)}{match.group(2)}")
        variants.add(f"rcp{match.group(1)}_{match.group(2)}")
    return variants


def _source_window_variants(source_file_window: str) -> set[str]:
    variants = {source_file_window}
    if "-" in source_file_window:
        variants.add(source_file_window.replace("-", "_"))
        variants.add(source_file_window.replace("-", ""))
    return variants


def _relative_to_repo(path: Path) -> Path:
    try:
        return path.resolve().relative_to(REPO_ROOT.resolve())
    except ValueError:
        return path


def _resolve_explicit_path(value: str, processed_root: Path) -> Path:
    candidate = Path(value)
    if candidate.is_absolute():
        return candidate
    repo_candidate = REPO_ROOT / candidate
    if repo_candidate.exists() or value.startswith(("inputs/", "config/", "model_v3/")):
        return repo_candidate
    return processed_root / candidate


def _load_sidecar_metadata(csv_path: Path) -> dict[str, Any]:
    metadata_path = csv_path.with_suffix(".metadata.json")
    if not metadata_path.exists():
        return {}
    try:
        with metadata_path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (json.JSONDecodeError, OSError) as exc:
        raise ClimateForcingResolutionError(f"Could not read climate metadata sidecar {metadata_path}: {exc}.")
    if not isinstance(data, dict):
        raise ClimateForcingResolutionError(f"Climate metadata sidecar must contain a JSON object: {metadata_path}.")
    return data


def _metadata_year(value: Any) -> int | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value).year
    except ValueError:
        try:
            return datetime.strptime(value[:10], "%Y-%m-%d").year
        except ValueError:
            return None


def _source_years(source_file_window: str) -> tuple[int, int] | None:
    match = re.fullmatch(r"([0-9]{4})-([0-9]{4})", source_file_window)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def _metadata_matches_source_window(metadata: dict[str, Any], source_file_window: str) -> bool:
    years = _source_years(source_file_window)
    if years is None:
        return False
    start_year = _metadata_year(metadata.get("time_start"))
    end_year = _metadata_year(metadata.get("time_end"))
    return (start_year, end_year) == years


def _contains_any(text: str, tokens: set[str]) -> bool:
    return any(token.lower() in text for token in tokens)


def _candidate_matches(
    csv_path: Path,
    *,
    climate_window_id: str,
    climate_pathway_id: str,
    source_file_window: str,
) -> bool:
    path_text = csv_path.as_posix().lower()
    metadata = _load_sidecar_metadata(csv_path)
    alias = window_alias(climate_window_id)
    pathway_variants = _pathway_variants(climate_pathway_id)
    source_variants = _source_window_variants(source_file_window)

    pathway_match = _contains_any(path_text, pathway_variants)
    if metadata.get("scenario") == climate_pathway_id:
        pathway_match = True
    if not pathway_match:
        return False

    window_match = alias.lower() in path_text
    if metadata.get("window") == alias:
        window_match = True

    source_match = _contains_any(path_text, {token.lower() for token in source_variants})
    if metadata and _metadata_matches_source_window(metadata, source_file_window):
        source_match = True

    if source_match:
        return True
    if window_match and not metadata:
        return True
    if window_match and metadata and "time_start" not in metadata and "time_end" not in metadata:
        return True
    return False


def _validate_window_pathway(
    *,
    climate_window_id: str,
    climate_pathway_id: str,
    window: dict[str, Any],
) -> None:
    window_type = window.get("window_type")
    allowed_pathways = window.get("allowed_pathways", [])
    if climate_pathway_id not in allowed_pathways:
        raise ClimateForcingResolutionError(
            f"{climate_window_id} does not allow climate_pathway_id {climate_pathway_id!r}."
        )
    if climate_window_id == BASELINE_WINDOW_ID:
        if climate_pathway_id != BASELINE_PATHWAY_ID:
            raise ClimateForcingResolutionError("baseline_1981_2005 must resolve only historical forcing.")
        if window_type != "baseline":
            raise ClimateForcingResolutionError("baseline_1981_2005 must be typed as a baseline climate window.")
    else:
        if climate_pathway_id == BASELINE_PATHWAY_ID:
            raise ClimateForcingResolutionError("Future climate windows must use RCP pathways, not historical.")
        if climate_pathway_id not in REQUIRED_RCP_PATHWAYS:
            raise ClimateForcingResolutionError(
                f"Future climate_pathway_id must be one of {REQUIRED_RCP_PATHWAYS}, found {climate_pathway_id!r}."
            )
        if window_type != "future":
            raise ClimateForcingResolutionError(f"{climate_window_id} must be typed as a future climate window.")

    if climate_window_id == "near_future_2030_2049":
        if window.get("source_file_window") != "2030-2050":
            raise ClimateForcingResolutionError("near_future_2030_2049 must use source_file_window 2030-2050.")
        if window.get("canonical_end") != "2049-12-31":
            raise ClimateForcingResolutionError("near_future_2030_2049 canonical analysis must end at 2049-12-31.")
    if climate_window_id == "mid_century_2050_2070" and window.get("canonical_start") != "2050-01-01":
        raise ClimateForcingResolutionError("mid_century_2050_2070 canonical analysis must start at 2050-01-01.")


def _validate_resolved_csv(path: Path) -> None:
    if path.suffix.lower() != ".csv":
        raise ClimateForcingResolutionError(f"Resolved climate forcing file is not a CSV: {path}")
    if not path.exists():
        raise ClimateForcingResolutionError(f"Resolved climate forcing CSV does not exist: {path}")
    if not path.is_file():
        raise ClimateForcingResolutionError(f"Resolved climate forcing path is not a file: {path}")


def resolve_climate_forcing(
    climate_window_id: str,
    climate_pathway_id: str,
    climate_windows: dict[str, Any],
    processed_root: Path,
) -> Path:
    """Resolve one scenario leaf to exactly one processed climate forcing CSV.

    The returned path is repository-relative when possible so it is stable in
    generated YAML configs.
    """

    window = get_climate_window(climate_window_id, climate_windows)
    _validate_window_pathway(
        climate_window_id=climate_window_id,
        climate_pathway_id=climate_pathway_id,
        window=window,
    )

    for key in _EXPLICIT_FORCING_KEYS:
        explicit = window.get(key)
        if isinstance(explicit, str) and explicit:
            candidate = _resolve_explicit_path(explicit, processed_root)
            _validate_resolved_csv(candidate)
            return _relative_to_repo(candidate)

    if not processed_root.exists():
        raise ClimateForcingResolutionError(f"Processed climate root does not exist: {processed_root}")
    if not processed_root.is_dir():
        raise ClimateForcingResolutionError(f"Processed climate root is not a directory: {processed_root}")

    source_file_window = str(window.get("source_file_window", ""))
    matches = [
        csv_path
        for csv_path in sorted(processed_root.rglob("*.csv"))
        if _candidate_matches(
            csv_path,
            climate_window_id=climate_window_id,
            climate_pathway_id=climate_pathway_id,
            source_file_window=source_file_window,
        )
    ]
    if not matches:
        raise ClimateForcingResolutionError(
            "No processed climate forcing CSV matched "
            f"climate_window_id={climate_window_id!r}, climate_pathway_id={climate_pathway_id!r}, "
            f"source_file_window={source_file_window!r} under {processed_root}."
        )
    if len(matches) > 1:
        candidates = "\n".join(f" - {path.as_posix()}" for path in matches)
        raise ClimateForcingResolutionError(
            "Ambiguous processed climate forcing CSV resolution for "
            f"climate_window_id={climate_window_id!r}, climate_pathway_id={climate_pathway_id!r}, "
            f"source_file_window={source_file_window!r}. Candidates:\n{candidates}"
        )

    _validate_resolved_csv(matches[0])
    return _relative_to_repo(matches[0])
