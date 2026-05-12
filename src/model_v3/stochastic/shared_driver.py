"""Shared daily peak driver for correlated household event rates."""

from __future__ import annotations

from datetime import date
from typing import Any

import numpy as np
import pandas as pd


def sample_daily_peak_driver(
    timestamps: tuple[Any, ...],
    seed: int,
    sigma_peak: float = 0.3,
) -> dict[date, float]:
    """Sample one latent peak factor per day using a shared seed."""

    sigma = max(float(sigma_peak), 0.0)
    if not timestamps or sigma <= 0.0:
        return {}

    ordered_days: list[date] = []
    seen_days: set[date] = set()
    for timestamp in timestamps:
        day = pd.Timestamp(timestamp).date()
        if day in seen_days:
            continue
        seen_days.add(day)
        ordered_days.append(day)

    rng = np.random.default_rng(int(seed))
    draws = rng.normal(loc=0.0, scale=sigma, size=len(ordered_days))
    return {day: float(draw) for day, draw in zip(ordered_days, draws)}

