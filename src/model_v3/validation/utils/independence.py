"""Validation independence diagnostics."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Mapping


def _as_roles(value: Any) -> tuple[str, ...]:
    """Normalize data_role metadata to a tuple of role strings."""

    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, Iterable):
        return tuple(str(item) for item in value)
    return (str(value),)


def _source_family(path_or_name: str) -> str:
    """Infer a coarse dataset family tag from a path or identifier."""

    lowered = str(path_or_name).lower()
    if "lcl" in lowered:
        return "lcl"
    if "synthetic" in lowered:
        return "synthetic"
    if "aggregate" in lowered:
        return "aggregate"
    return Path(lowered).stem


def assess_validation_independence(
    input_sources: Iterable[Mapping[str, Any]],
    validation_source: Mapping[str, Any],
) -> dict[str, Any]:
    """Assess whether a validation dataset is independent from input/calibration datasets."""

    validation_path = str(validation_source.get("path", "") or validation_source.get("source_name", ""))
    validation_roles = _as_roles(validation_source.get("data_role"))
    validation_family = _source_family(validation_path or str(validation_source.get("source_name", "")))

    exact_overlap = False
    family_overlap = False
    overlapping_roles: list[str] = []
    input_paths: list[str] = []
    for source in input_sources:
        input_path = str(source.get("path", "") or source.get("source_name", ""))
        input_paths.append(input_path)
        input_roles = _as_roles(source.get("data_role"))
        if validation_path and input_path and Path(validation_path) == Path(input_path):
            exact_overlap = True
        if _source_family(input_path) == validation_family:
            family_overlap = True
        overlapping_roles.extend(role for role in input_roles if role in validation_roles)

    partial_overlap = exact_overlap or family_overlap or bool(overlapping_roles)
    independent = not partial_overlap and "synthetic" not in validation_family
    strength = "strong" if independent else "weak"
    if "synthetic" in validation_family:
        independent = False
        partial_overlap = True
        strength = "weak"

    if exact_overlap:
        implication = "Validation uses the same dataset path as at least one model input, so the result is not independent."
    elif family_overlap:
        implication = "Validation uses the same source family as an input dataset, so interpretation should be limited to partial independence."
    elif overlapping_roles:
        implication = "Dataset roles overlap across input and validation, so the validation is only partially independent."
    elif "synthetic" in validation_family:
        implication = "Synthetic validation is useful for framework checks but not for external realism."
    else:
        implication = "Validation dataset appears independent from configured inputs."

    return {
        "dataset_independent": bool(independent),
        "partial_overlap": bool(partial_overlap),
        "validation_independence": strength,
        "validation_dataset_family": validation_family,
        "validation_data_role": list(validation_roles),
        "input_dataset_paths": input_paths,
        "implications": implication,
    }
