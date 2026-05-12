"""Household behavioural regimes for the stochastic demand layer."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import numpy as np


@dataclass(frozen=True)
class HouseholdBehaviourClass:
    """Simple regime descriptor used by the appliance event model."""

    name: str
    base_load_multiplier: float
    event_intensity_multiplier: float
    occupancy_scaling_factor: float
    peak_sensitivity_factor: float


HOUSEHOLD_CLASSES: dict[str, HouseholdBehaviourClass] = {
    "low_flat": HouseholdBehaviourClass(
        name="low_flat",
        base_load_multiplier=0.7,
        event_intensity_multiplier=0.7,
        occupancy_scaling_factor=0.9,
        peak_sensitivity_factor=0.8,
    ),
    "workday_absent": HouseholdBehaviourClass(
        name="workday_absent",
        base_load_multiplier=0.9,
        event_intensity_multiplier=0.95,
        occupancy_scaling_factor=0.7,
        peak_sensitivity_factor=1.0,
    ),
    "peak_heavy_family": HouseholdBehaviourClass(
        name="peak_heavy_family",
        base_load_multiplier=1.3,
        event_intensity_multiplier=1.5,
        occupancy_scaling_factor=1.2,
        peak_sensitivity_factor=1.45,
    ),
    "daytime_home": HouseholdBehaviourClass(
        name="daytime_home",
        base_load_multiplier=1.1,
        event_intensity_multiplier=1.2,
        occupancy_scaling_factor=1.35,
        peak_sensitivity_factor=1.05,
    ),
}

HOUSEHOLD_CLASS_NAMES: tuple[str, ...] = tuple(HOUSEHOLD_CLASSES.keys())
DEFAULT_CLASS_PROBABILITIES: tuple[float, ...] = (0.25, 0.35, 0.25, 0.15)


def _normalise_probabilities(probabilities: Iterable[float] | None) -> np.ndarray:
    """Return a valid probability vector for the configured regimes."""

    if probabilities is None:
        return np.asarray(DEFAULT_CLASS_PROBABILITIES, dtype=float)

    values = np.asarray(list(probabilities), dtype=float)
    if values.shape != (len(HOUSEHOLD_CLASS_NAMES),):
        return np.asarray(DEFAULT_CLASS_PROBABILITIES, dtype=float)
    values = np.clip(values, 0.0, None)
    total = float(values.sum())
    if total <= 0.0:
        return np.asarray(DEFAULT_CLASS_PROBABILITIES, dtype=float)
    return values / total


def resolve_household_class(name: str) -> HouseholdBehaviourClass:
    """Resolve a configured household regime or fall back safely."""

    resolved = HOUSEHOLD_CLASSES.get(str(name).strip().lower())
    return resolved if resolved is not None else HOUSEHOLD_CLASSES["low_flat"]


def sample_household_class(
    rng: np.random.Generator,
    probabilities: Iterable[float] | None = None,
) -> HouseholdBehaviourClass:
    """Sample one household regime from the configured categorical prior."""

    weights = _normalise_probabilities(probabilities)
    selected = str(rng.choice(HOUSEHOLD_CLASS_NAMES, p=weights))
    return HOUSEHOLD_CLASSES[selected]

