"""Tests for scenario-leaf model runner adapter mode handling."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
MODEL_V3_SRC = REPO_ROOT / "src"
if str(MODEL_V3_SRC) not in sys.path:
    sys.path.insert(0, str(MODEL_V3_SRC))

from model_v3.scenarios import model_runner_adapter  # noqa: E402


def test_runner_mode_defaults_to_annual_demand() -> None:
    assert model_runner_adapter._runner_mode({"model_options": {}}) == "annual_demand"


def test_runner_mode_accepts_explicit_cohort() -> None:
    config = {"model_options": {"runner_mode": "stochastic_cohort", "use_stochastic_cohort": False}}

    assert model_runner_adapter._runner_mode(config) == "stochastic_cohort"


def test_runner_mode_rejects_unknown_mode() -> None:
    with pytest.raises(model_runner_adapter.ModelRunnerAdapterError):
        model_runner_adapter._runner_mode({"model_options": {"runner_mode": "representative_year"}})


def test_target_resolution_override_is_explicit() -> None:
    config = {"model_options": {"target_resolution_seconds": 3600}}

    assert model_runner_adapter._target_resolution_seconds(config, 86400) == 3600


def test_target_resolution_defaults_to_original_for_regular_leaves() -> None:
    assert model_runner_adapter._target_resolution_seconds({"model_options": {}}, 86400) == 86400


def test_serialisable_summary_excludes_profile_payloads() -> None:
    summary = model_runner_adapter._serialisable_summary(
        {
            "profile_frame": object(),
            "aggregate_profile": [1.0],
            "timestamps": ["2050-01-01T00:00:00"],
            "annual_energy_kWh": 42.0,
        }
    )

    assert summary == {"annual_energy_kWh": 42.0}
