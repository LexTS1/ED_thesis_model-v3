"""Household-specific residual appliance base load generation."""

from __future__ import annotations

from typing import Any

import numpy as np


_CLASS_SCALE = {
    "low_flat": 0.8,
    "peak_heavy_family": 1.3,
    "daytime_home": 1.1,
}


def _clamp(value: float, lower: float, upper: float) -> float:
    """Clamp one scalar to a safe interval."""

    return float(min(max(float(value), float(lower)), float(upper)))


def generate_base_load_profile(
    *,
    n_steps: int,
    household_class: str,
    rng: Any,
) -> dict[str, float | tuple[float, ...]]:
    """Generate a simple base electrical load with household-specific scaling."""

    if n_steps <= 0:
        return {"base_level_w": 0.0, "class_scale": 1.0, "profile_w": tuple()}

    nominal_level_w = float(rng.uniform(150.0, 250.0))
    sampled_level_w = _clamp(float(rng.normal(nominal_level_w, 30.0)), 75.0, 400.0)
    class_scale = float(_CLASS_SCALE.get(str(household_class).strip().lower(), 1.0))
    base_profile = sampled_level_w + rng.normal(0.0, 10.0, size=n_steps)
    profile = np.clip(base_profile * class_scale, 0.0, None)
    return {
        "base_level_w": float(sampled_level_w),
        "class_scale": class_scale,
        "profile_w": tuple(float(value) for value in profile.tolist()),
    }
