"""Configuration helpers for canonical model_v3 settings."""

from __future__ import annotations

import logging
from typing import Any, Mapping


def resolve_household_count(
    config: Mapping[str, Any] | None,
    *,
    default: int = 0,
    logger: logging.Logger | None = None,
) -> int:
    """Resolve the canonical household count, preferring the cohort config."""

    config = config or {}
    cohort_cfg = dict(config.get("cohort", {}))
    simulation_cfg = dict(config.get("simulation", {}))
    cohort_value = cohort_cfg.get("n_households")
    simulation_value = simulation_cfg.get("n_households")

    if cohort_value not in {None, ""}:
        resolved = max(int(cohort_value), 0)
        if simulation_value not in {None, ""} and int(simulation_value) != resolved and logger is not None:
            logger.warning(
                "Deprecated simulation.n_households=%s ignored; using cohort.n_households=%s",
                int(simulation_value),
                resolved,
            )
        return resolved

    if simulation_value not in {None, ""}:
        if logger is not None:
            logger.warning(
                "Deprecated simulation.n_households=%s used as fallback; define cohort.n_households instead",
                int(simulation_value),
            )
        return max(int(simulation_value), 0)

    return max(int(default), 0)
