"""Shared CLI and execution helpers for validation runners."""

from __future__ import annotations

import argparse
import logging
from copy import deepcopy
from typing import Any, Mapping, Sequence

CANONICAL_THESIS_CONFIG = "config/thesis.yaml"
CANONICAL_REFERENCE_YEAR = 2023
CANONICAL_COHORT_HOUSEHOLDS = 30
DEFAULT_PROGRESS_LOGGERS = (
    "model_v3.data.data_module",
    "model_v3.data.loaders",
    "model_v3.adapters.fluvius_loader",
    "model_v3.adapters.kuleuven_loader",
)


def build_runner_cli(description: str, *, include_quick: bool = True) -> argparse.ArgumentParser:
    """Create a CLI parser with standard validation-runner switches."""

    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--config", default=None, help="Path to the model_v3 YAML config. Defaults to config/model.yaml.")
    if include_quick:
        parser.add_argument(
            "--quick",
            action="store_true",
            help="Run a shorter debug validation horizon using validation.quick_mode overrides.",
        )
    return parser


def configure_runner_logging(logger_names: Sequence[str]) -> None:
    """Configure concise CLI logging for selected runner loggers."""

    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%H:%M:%S",
    )
    for logger_name in (*DEFAULT_PROGRESS_LOGGERS, *logger_names):
        logging.getLogger(logger_name).setLevel(logging.INFO)


def quick_external_row_cap(
    quick_metadata: Mapping[str, Any],
    *,
    rows_per_step: int,
    safety_steps: int = 8,
) -> int | None:
    """Return a row cap for large external validation CSVs in quick mode."""

    if not bool(quick_metadata.get("enabled", False)):
        return None
    max_steps = quick_metadata.get("max_steps")
    if max_steps in {None, ""}:
        return None
    return (max(int(max_steps), 1) + max(int(safety_steps), 1)) * max(int(rows_per_step), 1)


def apply_quick_validation_mode(
    config: Mapping[str, Any],
    *,
    quick_mode: bool | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Clone config and apply optional quick-validation overrides."""

    prepared = deepcopy(dict(config))
    validation_cfg = prepared.setdefault("validation", {})
    quick_cfg = dict(validation_cfg.get("quick_mode", {}))
    enabled = bool(quick_cfg.get("enabled", False)) if quick_mode is None else bool(quick_mode)

    quick_metadata: dict[str, Any] = {
        "enabled": enabled,
        "max_steps": None,
        "cohort_households": None,
        "minimum_households": None,
        "overrides": [],
        "debug_only": enabled,
    }
    if not enabled:
        return prepared, quick_metadata

    simulation_cfg = prepared.setdefault("simulation", {})
    max_steps = max(int(quick_cfg.get("max_steps", 168) or 168), 1)
    current_max_steps = simulation_cfg.get("max_steps")
    if current_max_steps in {None, ""}:
        simulation_cfg["max_steps"] = max_steps
        quick_metadata["overrides"].append(f"simulation.max_steps:{max_steps}")
    else:
        simulation_cfg["max_steps"] = min(max(int(current_max_steps), 1), max_steps)
        if int(simulation_cfg["max_steps"]) != int(current_max_steps):
            quick_metadata["overrides"].append(f"simulation.max_steps:{simulation_cfg['max_steps']}")
    quick_metadata["max_steps"] = int(simulation_cfg["max_steps"])

    cohort_households = quick_cfg.get("cohort_households")
    if cohort_households is not None:
        household_count = max(int(cohort_households), 1)
        minimum_households = max(int(quick_cfg.get("minimum_households", household_count) or household_count), 1)
        cohort_cfg = prepared.setdefault("cohort", {})
        cohort_cfg["n_households"] = household_count
        cohort_cfg["minimum_households"] = minimum_households
        quick_metadata["cohort_households"] = household_count
        quick_metadata["minimum_households"] = minimum_households
        quick_metadata["overrides"].append(f"cohort.n_households:{household_count}")
        quick_metadata["overrides"].append(f"cohort.minimum_households:{minimum_households}")

    validation_cfg["quick_mode"] = {**quick_cfg, "enabled": True}
    return prepared, quick_metadata


def format_elapsed_summary(runner_name: str, result: Mapping[str, Any]) -> str:
    """Format a compact CLI summary line for a completed runner."""

    timing = dict(result.get("runner_timing", {}))
    elapsed_seconds = float(timing.get("elapsed_seconds", 0.0))
    quick_mode = bool(timing.get("quick_mode", False))
    report_path = result.get("report_path", "")
    n_steps = timing.get("n_steps")
    step_suffix = f" steps={n_steps}" if n_steps is not None else ""
    return (
        f"[{runner_name}] elapsed_s={elapsed_seconds:.1f} "
        f"quick_mode={quick_mode}{step_suffix} report={report_path}"
    )


def _format_config_value(value: Any) -> str:
    """Format config values for report metadata."""

    if value in {None, ""}:
        return "null"
    return str(value)


def runtime_context_lines(
    config: Mapping[str, Any],
    *,
    quick_metadata: Mapping[str, Any] | None = None,
    n_steps: int | None = None,
) -> list[str]:
    """Return a standard runtime-context block for validation reports."""

    simulation_cfg = dict(config.get("simulation", {}))
    cohort_cfg = dict(config.get("cohort", {}))
    climate_cfg = dict(config.get("climate", {}))
    quick = dict(quick_metadata or {})
    lines = [
        "## Runtime Context",
        "",
        f"- canonical thesis config: `{CANONICAL_THESIS_CONFIG}`",
        f"- canonical thesis runtime: reference year `{CANONICAL_REFERENCE_YEAR}`, "
        f"`{CANONICAL_COHORT_HOUSEHOLDS}` households, `simulation.max_steps: null`, climate disabled",
        f"- report reference year: `{_format_config_value(simulation_cfg.get('reference_year'))}`",
        f"- report cohort households: `{_format_config_value(cohort_cfg.get('n_households'))}`",
        f"- report minimum households: `{_format_config_value(cohort_cfg.get('minimum_households'))}`",
        f"- report max steps: `{_format_config_value(simulation_cfg.get('max_steps'))}`",
        f"- quick mode: `{bool(quick.get('enabled', False))}`",
        f"- climate enabled: `{bool(climate_cfg.get('enabled', False))}`",
    ]
    if n_steps is not None:
        lines.append(f"- simulated/aligned model steps: `{int(n_steps)}`")
    return lines


def artifact_interpretation_lines(
    config: Mapping[str, Any],
    *,
    quick_metadata: Mapping[str, Any] | None = None,
    n_steps: int | None = None,
    extra: str | None = None,
) -> list[str]:
    """Return a standard artifact-status block for thesis-safe interpretation."""

    simulation_cfg = dict(config.get("simulation", {}))
    cohort_cfg = dict(config.get("cohort", {}))
    climate_cfg = dict(config.get("climate", {}))
    quick = dict(quick_metadata or {})
    max_steps = simulation_cfg.get("max_steps")
    reference_year = simulation_cfg.get("reference_year")
    cohort_households = cohort_cfg.get("n_households")

    status_parts: list[str] = []
    if bool(quick.get("enabled", False)):
        status_parts.append("This is a quick/debug artifact and is not thesis-valid evidence.")
    if max_steps not in {None, ""}:
        status_parts.append("This artifact is horizon-limited by `simulation.max_steps`.")
    if n_steps is not None and int(n_steps) < 8000:
        status_parts.append("It covers fewer than a full non-leap-year hourly horizon.")

    is_canonical_shape = (
        reference_year == CANONICAL_REFERENCE_YEAR
        and cohort_households in {None, CANONICAL_COHORT_HOUSEHOLDS}
        and max_steps in {None, ""}
        and not bool(climate_cfg.get("enabled", False))
        and not bool(quick.get("enabled", False))
    )
    if not status_parts and is_canonical_shape:
        status_parts.append(
            "This is a full-horizon candidate for thesis use, but cite it only with its report metadata and provenance."
        )
    elif not status_parts:
        status_parts.append(
            "This is a cached validation artifact; verify its config and provenance before treating it as thesis evidence."
        )

    if extra:
        status_parts.append(extra)

    return ["## Artifact Interpretation", "", *[f"- {part}" for part in status_parts]]


def validation_type_lines(label: str, description: str) -> list[str]:
    """Return a standard validation-type block."""

    return ["## Validation Type", "", f"- classification: {label}", f"- interpretation: {description}"]
