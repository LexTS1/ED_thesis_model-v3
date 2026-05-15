from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
MODEL_V3_SRC = REPO_ROOT / "src"
if str(MODEL_V3_SRC) not in sys.path:
    sys.path.insert(0, str(MODEL_V3_SRC))

from model_v3.validation.reference_generators.richardson import generate_richardson_reference
from model_v3.validation.runners.validate_against_richardson import _metrics, _model_baseload_frame


def test_richardson_fallback_reference_matches_requested_horizon() -> None:
    timestamps = pd.date_range("2023-01-01", periods=48, freq="h")

    reference = generate_richardson_reference(
        config={"simulation": {"start_timestamp": "2023-01-01T00:00:00+01:00"}},
        model_profiles={"household_000": [100.0] * 48, "household_001": [150.0] * 48},
        sampled_population=[{"occupants_per_dwelling": 1}, {"occupants_per_dwelling": 4}],
        n_households=2,
        timestep_seconds=3600,
        seed=7,
        mode="shape-normalized",
        allow_fallback=True,
        target_timestamps=timestamps,
    )

    frame = reference.profile_frame
    assert len(frame) == 96
    assert set(frame["household_id"]) == {"household_000", "household_001"}
    assert set(frame["occupants"]) == {1, 4}
    assert frame["timestamp"].nunique() == 48
    assert {"occupancy", "appliances_W", "lighting_W", "total_W"}.issubset(frame.columns)
    assert reference.metadata["generator"] == "fallback_richardson_like"


def test_model_baseload_frame_uses_nonthermal_components() -> None:
    timestamps = pd.date_range("2023-01-01", periods=3, freq="h")
    frame = _model_baseload_frame(
        {
            "timestamps": timestamps,
            "household_nonthermal_profiles": {"household_000": [10.0, 20.0, 30.0]},
            "household_base_profiles": {"household_000": [5.0, 5.0, 5.0]},
            "household_event_profiles": {"household_000": [1.0, 2.0, 3.0]},
            "household_lighting_profiles": {"household_000": [4.0, 13.0, 22.0]},
            "household_occupancy_profiles": {"household_000": [0.0, 1.0, 2.0]},
        }
    )

    assert frame["total_W"].tolist() == [10.0, 20.0, 30.0]
    assert frame["appliances_W"].tolist() == [6.0, 7.0, 8.0]
    assert frame["lighting_W"].tolist() == [4.0, 13.0, 22.0]
    assert frame["occupancy"].tolist() == [0.0, 1.0, 2.0]


def test_richardson_metrics_cover_shape_variation_peakiness_occupancy_and_diversity() -> None:
    timestamps = pd.date_range("2023-01-01", periods=168, freq="h")
    hours = np.arange(len(timestamps), dtype=float)

    def frame_for(generator: str, scale: float) -> pd.DataFrame:
        rows = []
        for household_index, offset in enumerate((0.0, 0.5)):
            load = scale * (120.0 + 35.0 * np.sin(2.0 * np.pi * (hours + offset) / 24.0) + 12.0 * (hours % 7))
            occupancy = np.clip(1.0 + np.sin(2.0 * np.pi * (hours - 7.0 + offset) / 24.0), 0.0, None)
            for timestamp, load_value, occ_value in zip(timestamps, load, occupancy):
                rows.append(
                    {
                        "timestamp": timestamp,
                        "household_id": f"household_{household_index:03d}",
                        "total_W": float(load_value),
                        "appliances_W": float(load_value * 0.72),
                        "lighting_W": float(load_value * 0.28),
                        "occupancy": float(occ_value),
                        "generator": generator,
                    }
                )
        return pd.DataFrame(rows)

    metrics = _metrics(frame_for("model_v3", 1.0), frame_for("richardsonpy", 1.08), timestep_seconds=3600)

    assert metrics["alignment"]["aligned_steps"] == 168
    assert metrics["alignment"]["model_households"] == 2
    assert metrics["shape"]["mean_diurnal_correlation"] == pytest.approx(1.0, abs=0.05)
    assert "daily_energy_cv_model" in metrics["daily_weekly"]
    assert "p95_p50_reference" in metrics["peakiness_load_duration"]
    assert "appliances" in metrics["component_shape"]
    assert "lighting" in metrics["component_shape"]
    assert "active_fraction_reference" in metrics["occupancy"]
    assert metrics["diversity"]["diversity_factor_model"] >= 1.0
