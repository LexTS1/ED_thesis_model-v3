from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from model_v3.scenarios.summarize_outputs import build_annual_space_heating_demand_comparison

from tests.model_v3.comparison_test_utils import BASELINE_SCENARIO, FROZEN_SCENARIO


def _write_annual_profile(path: Path, annual_values_kwh: dict[int, float]) -> None:
    rows = []
    for year, demand_kwh in annual_values_kwh.items():
        timestamps = pd.date_range(f"{year}-01-01T00:00:00Z", periods=2, freq="1h")
        power_w = demand_kwh * 1000.0 / 2.0
        rows.extend({"timestamp": timestamp.isoformat(), "Q_heating_supplied_W": power_w} for timestamp in timestamps)
    path.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path / "annual_profile.csv", index=False)


def _space_heating_leaf(tmp_path: Path, scenario_id: str, realization_id: str, annual_values_kwh: dict[int, float]) -> dict:
    window, pathway, technology = scenario_id.split("__")
    raw_outputs_dir = tmp_path / f"{scenario_id}__{realization_id}" / "outputs"
    _write_annual_profile(raw_outputs_dir, annual_values_kwh)
    return {
        "scenario_leaf_id": f"{scenario_id}__{realization_id}",
        "scenario_id": scenario_id,
        "climate_window_id": window,
        "climate_pathway_id": pathway,
        "technology_case_id": technology,
        "realization_id": realization_id,
        "raw_outputs_dir": str(raw_outputs_dir),
    }


def test_annual_space_heating_comparison_uses_all_baseline_years(tmp_path: Path) -> None:
    metrics = pd.DataFrame(
        [
            _space_heating_leaf(tmp_path, BASELINE_SCENARIO, "seed_0000", {1981: 1000.0, 1982: 1200.0}),
            _space_heating_leaf(tmp_path, FROZEN_SCENARIO, "seed_0000", {2030: 800.0, 2031: 900.0}),
        ]
    )

    comparison = build_annual_space_heating_demand_comparison(metrics)

    assert len(comparison) == 2
    baseline = comparison[comparison["scenario_id"] == BASELINE_SCENARIO].iloc[0]
    future = comparison[comparison["scenario_id"] == FROZEN_SCENARIO].iloc[0]
    assert baseline["n_annual_samples"] == 2
    assert future["n_annual_samples"] == 2
    assert future["baseline_annual_useful_heating_kWh_mean"] == pytest.approx(1100.0)
    assert future["annual_useful_heating_kWh_mean"] == pytest.approx(850.0)
    assert future["delta_annual_useful_heating_kWh_mean"] == pytest.approx(-250.0)
    assert future["delta_annual_useful_heating_kWh_pct"] == pytest.approx(-22.7272727273)
