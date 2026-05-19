from __future__ import annotations

import pandas as pd
import pytest

from model_v3.scenarios.summarize_outputs import (
    aggregate_annual_space_heating_demand,
    build_annual_space_heating_demand_comparison,
)

from tests.model_v3.comparison_test_utils import BASELINE_SCENARIO, FROZEN_SCENARIO


def _space_heating_row(scenario_id: str, realization_id: str, year: int, demand_kwh: float) -> dict:
    window, pathway, technology = scenario_id.split("__")
    return {
        "scenario_leaf_id": f"{scenario_id}__{realization_id}",
        "scenario_id": scenario_id,
        "climate_window_id": window,
        "climate_pathway_id": pathway,
        "technology_case_id": technology,
        "realization_id": realization_id,
        "year": year,
        "annual_useful_space_heating_kWh": demand_kwh,
    }


def test_annual_space_heating_comparison_uses_all_baseline_years() -> None:
    annual = pd.DataFrame(
        [
            _space_heating_row(BASELINE_SCENARIO, "seed_0000", 1981, 1000.0),
            _space_heating_row(BASELINE_SCENARIO, "seed_0000", 1982, 1200.0),
            _space_heating_row(FROZEN_SCENARIO, "seed_0000", 2030, 800.0),
            _space_heating_row(FROZEN_SCENARIO, "seed_0000", 2031, 900.0),
        ]
    )

    comparison = build_annual_space_heating_demand_comparison(annual)

    assert len(comparison) == 2
    assert comparison["baseline_year_count"].tolist() == [2, 2]
    assert comparison["baseline_mean_annual_useful_space_heating_kWh"].tolist() == [1100.0, 1100.0]
    assert comparison["future_year"].tolist() == [2030, 2031]
    assert comparison["delta_abs_kWh"].tolist() == [-300.0, -200.0]
    assert comparison.loc[0, "delta_pct"] == pytest.approx(-27.2727272727)
    assert comparison.loc[1, "delta_pct"] == pytest.approx(-18.1818181818)


def test_annual_space_heating_aggregate_keeps_scenario_level_deltas() -> None:
    annual = pd.DataFrame(
        [
            _space_heating_row(BASELINE_SCENARIO, "seed_0000", 1981, 1000.0),
            _space_heating_row(BASELINE_SCENARIO, "seed_0000", 1982, 1200.0),
            _space_heating_row(FROZEN_SCENARIO, "seed_0000", 2030, 800.0),
            _space_heating_row(FROZEN_SCENARIO, "seed_0000", 2031, 900.0),
        ]
    )
    comparison = build_annual_space_heating_demand_comparison(annual)

    aggregate = aggregate_annual_space_heating_demand(annual, comparison)

    future = aggregate[aggregate["scenario_id"] == FROZEN_SCENARIO].iloc[0]
    assert future["annual_year_count"] == 2
    assert future["annual_useful_space_heating_kWh_mean"] == pytest.approx(850.0)
    assert future["delta_abs_kWh_mean"] == pytest.approx(-250.0)
    assert future["delta_pct_mean"] == pytest.approx(-22.7272727273)
