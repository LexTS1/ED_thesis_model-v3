"""Generate stochastic yearly weather members from historical PVGIS data."""

from __future__ import annotations

from collections.abc import Mapping

import numpy as np
import pandas as pd


def _monthly_temperature_sigma(year_dict: Mapping[int, pd.DataFrame]) -> dict[int, float]:
    """Compute historical monthly temperature volatility across all years."""

    historical = pd.concat([frame[["temperature_C"]] for frame in year_dict.values()], axis=0).sort_index()
    sigma = historical.groupby(historical.index.month)["temperature_C"].std(ddof=0).fillna(0.0)
    return {int(month): float(value) for month, value in sigma.items()}


def _normalise_month_mapping(values: Mapping[int, float] | None, default: float = 0.0) -> dict[int, float]:
    """Expand a partial month mapping into all twelve calendar months."""

    source = {int(month): float(value) for month, value in dict(values or {}).items()}
    return {month: float(source.get(month, default)) for month in range(1, 13)}


def _ar1_temperature_perturbation(
    index: pd.DatetimeIndex,
    rng: np.random.Generator,
    rho: float,
    sigma_month: Mapping[int, float],
) -> np.ndarray:
    """Return an AR(1) perturbation with month-dependent stationary volatility."""

    epsilon = np.zeros(len(index), dtype=float)
    previous = 0.0
    innovation_factor = float(np.sqrt(max(1.0 - float(rho) ** 2, 0.0)))
    for position, timestamp in enumerate(index):
        month = int(timestamp.month)
        innovation_sigma = float(sigma_month[month]) * innovation_factor
        innovation = float(rng.normal(0.0, innovation_sigma))
        current = float(rho) * previous + innovation
        epsilon[position] = current
        previous = current
    return epsilon


def generate_weather_ensemble(
    year_dict: Mapping[int, pd.DataFrame],
    N_members: int = 50,
    seed: int = 42,
    rho: float = 0.8,
    mu_month: Mapping[int, float] | None = None,
) -> list[pd.DataFrame]:
    """Sample yearly weather members and perturb temperature with monthly AR(1) noise."""

    if not year_dict:
        raise ValueError("generate_weather_ensemble requires at least one complete yearly weather frame.")

    years = np.asarray(sorted(int(year) for year in year_dict), dtype=int)
    rng = np.random.default_rng(int(seed))
    sigma_month = _monthly_temperature_sigma(year_dict)
    mu_lookup = _normalise_month_mapping(mu_month, default=0.0)
    sampled_years = rng.choice(years, size=max(int(N_members), 1), replace=True)

    members: list[pd.DataFrame] = []
    for member_index, base_year in enumerate(sampled_years):
        base = year_dict[int(base_year)].copy()
        perturbation = _ar1_temperature_perturbation(
            index=base.index,
            rng=rng,
            rho=float(rho),
            sigma_month=sigma_month,
        )
        monthly_shift = np.asarray([mu_lookup[int(timestamp.month)] for timestamp in base.index], dtype=float)
        member = base.copy()
        member["temperature_C"] = member["temperature_C"].to_numpy(dtype=float) + monthly_shift + perturbation
        member.attrs["member_index"] = int(member_index)
        member.attrs["base_year"] = int(base_year)
        member.attrs["rho"] = float(rho)
        member.attrs["sigma_month"] = dict(sigma_month)
        member.attrs["mu_month"] = dict(mu_lookup)
        members.append(member)
    return members
