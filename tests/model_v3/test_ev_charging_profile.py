from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from model_v3.systems.distributed_energy import annual_ev_home_charging_kwh, build_ev_charging_profile


def _overnight_ev_config() -> dict:
    return {
        "annual_use": {
            "km_per_year": {"base": 15000},
            "specific_consumption_kwh_per_100km": {"base": 14.2},
        },
        "charging": {
            "home_charging_probability": {"base": 0.70},
            "charger_power_kw": {"base": 7.4},
            "charging_strategy": "delayed_overnight_home",
            "charging_window": {"start_hour": 22, "end_hour": 6},
            "charging_shape": "gaussian",
            "peak_hour": 1,
            "profile_spread_hours": {"base": 1.8},
            "peak_jitter_sigma_hours": {"base": 1.25},
            "peak_jitter_max_hours": {"base": 3.0},
        },
    }


def test_delayed_overnight_ev_profile_preserves_annual_energy() -> None:
    timestamps = pd.date_range("2024-01-01T00:00:00+01:00", periods=8760, freq="h")
    ev_cfg = _overnight_ev_config()

    profile_w = np.asarray(build_ev_charging_profile(timestamps, ev_cfg, has_ev=True), dtype=float)
    annual_kwh = profile_w.sum() / 1000.0

    assert annual_kwh == pytest.approx(annual_ev_home_charging_kwh(ev_cfg), rel=0.01)


def test_delayed_overnight_ev_profile_peaks_overnight() -> None:
    timestamps = pd.date_range("2024-01-01T00:00:00+01:00", periods=8760, freq="h")
    ev_cfg = _overnight_ev_config()

    profile = pd.Series(build_ev_charging_profile(timestamps, ev_cfg, has_ev=True), index=timestamps)
    mean_by_hour = profile.groupby(profile.index.hour).mean()

    assert int(mean_by_hour.idxmax()) in {0, 1, 2}
    assert mean_by_hour.loc[1] > mean_by_hour.loc[17]


def test_seeded_ev_profiles_create_some_diversity() -> None:
    timestamps = pd.date_range("2024-01-01T00:00:00+01:00", periods=8760, freq="h")
    ev_cfg = _overnight_ev_config()
    profiles = np.vstack(
        [
            np.asarray(build_ev_charging_profile(timestamps, ev_cfg, has_ev=True, random_seed=42 + i), dtype=float)
            for i in range(20)
        ]
    )

    diversity_factor = np.max(profiles, axis=1).sum() / np.max(profiles.sum(axis=0))

    assert diversity_factor > 1.05
