from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from model_v3.stochastic.dhw_generator import generate_dhw_events
from model_v3.stochastic.household_classifier import resolve_household_class


def _occupancy_spec() -> dict:
    return {
        "dt_minutes": 60,
        "states": ["away", "awake", "sleep"],
        "fallback_weights": {
            "weekday": {"away": 0.05, "awake": 0.75, "sleep": 0.20},
            "weekend": {"away": 0.05, "awake": 0.75, "sleep": 0.20},
        },
        "rules": {
            "weekday": [
                {"state": "sleep", "start": "23:00", "end": "07:00", "p": 0.85},
                {"state": "awake", "start": "07:00", "end": "23:00", "p": 0.85},
            ],
            "weekend": [
                {"state": "sleep", "start": "23:00", "end": "08:00", "p": 0.85},
                {"state": "awake", "start": "08:00", "end": "23:00", "p": 0.85},
            ],
        },
    }


def _calibration(**overrides: object) -> dict:
    cfg = {
        "enabled": True,
        "daily_useful_kWh_per_person": {"base": 1.2},
        "event_frequency_per_occupant_day": {"base": 2.0},
        "timing_weights": {
            "morning": {"start_hour": 6.0, "end_hour": 9.0, "weight": 0.45},
            "daytime": {"start_hour": 9.0, "end_hour": 18.0, "weight": 0.12},
            "evening": {"start_hour": 18.0, "end_hour": 22.0, "weight": 0.40},
            "night": {"start_hour": 22.0, "end_hour": 6.0, "weight": 0.03},
        },
        "event_type_probabilities": {
            "sink": 0.58,
            "shower": 0.26,
            "dishwashing": 0.13,
            "bath": 0.03,
        },
    }
    cfg.update(overrides)
    return cfg


def _run_dhw(
    *,
    days: int = 30,
    occupants: float = 2.0,
    intensity: float = 1.0,
    seed: int = 42,
    calibration: dict | None = None,
) -> dict:
    timestamps = tuple(pd.date_range("2024-01-01T00:00:00+01:00", periods=days * 24, freq="h"))
    return generate_dhw_events(
        timestamps=timestamps,
        target_resolution_seconds=3600,
        occupancy_spec=_occupancy_spec(),
        occupants_per_dwelling=occupants,
        occupancy_threshold=0.5,
        schedule_variation_seed=0,
        occupancy_time_shift_hours=0.0,
        transition_variability_scale=1.0,
        state_duration_scale=1.0,
        occupancy_state_biases={},
        household_class=resolve_household_class("workday_absent"),
        household_random_effect_u=0.0,
        rng=np.random.default_rng(seed),
        event_frequency_scale=1.0,
        event_intensity_scale=intensity,
        dhw_calibration=_calibration() if calibration is None else calibration,
    )


def _energy_kwh(result: dict) -> float:
    return float(np.asarray(result["output_load_W"], dtype=float).sum() / 1000.0)


def test_calibrated_dhw_useful_heat_matches_per_person_target() -> None:
    result = _run_dhw(days=365, occupants=2.0, seed=11)

    assert _energy_kwh(result) == pytest.approx(1.2 * 2.0 * 365.0, rel=0.01)
    assert result["event_summary"]["dhw_calibration_enabled"] is True


def test_calibrated_dhw_scales_with_occupants() -> None:
    one_person = _run_dhw(days=30, occupants=1.0, seed=12)
    four_person = _run_dhw(days=30, occupants=4.0, seed=12)

    assert _energy_kwh(four_person) / _energy_kwh(one_person) == pytest.approx(4.0, rel=0.01)


def test_calibrated_dhw_has_morning_and_evening_pattern() -> None:
    result = _run_dhw(days=90, occupants=2.0, seed=13)
    timestamps = pd.date_range("2024-01-01T00:00:00+01:00", periods=90 * 24, freq="h")
    profile = pd.Series(result["output_load_W"], index=timestamps, dtype=float)
    mean_by_hour = profile.groupby(profile.index.hour).mean()

    assert mean_by_hour.loc[6:8].mean() > mean_by_hour.loc[0:4].mean()
    assert mean_by_hour.loc[18:21].mean() > mean_by_hour.loc[0:4].mean()


def test_calibrated_dhw_event_mix_can_include_all_configured_types() -> None:
    calibration = _calibration(
        event_frequency_per_occupant_day={"base": 8.0},
        event_type_probabilities={"sink": 0.25, "shower": 0.25, "dishwashing": 0.25, "bath": 0.25},
    )
    result = _run_dhw(days=30, occupants=2.0, seed=14, calibration=calibration)
    counts = result["event_summary"]["event_count_by_type"]

    assert {"sink", "shower", "dishwashing", "bath"}.issubset(counts)
    assert all(event.get("volume_liters") is not None for event in result["event_log"])


def test_calibrated_dhw_intensity_scale_changes_useful_heat() -> None:
    base = _run_dhw(days=30, occupants=2.0, intensity=1.0, seed=15)
    higher = _run_dhw(days=30, occupants=2.0, intensity=1.5, seed=15)

    assert _energy_kwh(higher) / _energy_kwh(base) == pytest.approx(1.5, rel=0.01)


def test_legacy_dhw_generator_path_still_runs_when_calibration_disabled() -> None:
    result = _run_dhw(days=14, occupants=2.0, seed=16, calibration={"enabled": False})

    assert _energy_kwh(result) > 0.0
    assert result["event_summary"]["dhw_calibration_enabled"] is False
    assert all("calibrated_energy_kWh" not in event for event in result["event_log"])
