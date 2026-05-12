from __future__ import annotations

import pandas as pd
import pytest

from model_v3.scenarios.summarize_outputs import build_baseline_comparison
from model_v3.scenarios.summary_contract import BASELINE_SCENARIO_ID, REQUIRED_METRIC_COLUMNS


def _row(leaf_id: str, scenario_id: str, realization_id: str, grid_import: float, grid_export: float = 0.0) -> dict:
    parts = scenario_id.split("__")
    row = {
        "scenario_leaf_id": leaf_id,
        "scenario_id": scenario_id,
        "climate_window_id": parts[0],
        "climate_pathway_id": parts[1],
        "technology_case_id": parts[2],
        "realization_id": realization_id,
    }
    for metric in REQUIRED_METRIC_COLUMNS:
        row[metric] = 1.0
    row["annual_grid_import_kWh"] = grid_import
    row["annual_grid_export_kWh"] = grid_export
    return row


def test_future_seed_matches_same_baseline_seed_and_computes_deltas() -> None:
    future_scenario = "mid_century_2050_2070__rcp_8_5__tech_frozen_stock"
    metrics = pd.DataFrame(
        [
            _row(f"{BASELINE_SCENARIO_ID}__seed_0000", BASELINE_SCENARIO_ID, "seed_0000", 100.0),
            _row(f"{future_scenario}__seed_0000", future_scenario, "seed_0000", 150.0),
        ]
    )

    comparison = build_baseline_comparison(metrics)
    row = comparison.iloc[0]

    assert row["baseline_scenario_leaf_id"] == f"{BASELINE_SCENARIO_ID}__seed_0000"
    assert row["baseline_available"] is True or row["baseline_available"] == True
    assert row["annual_grid_import_kWh_delta_abs"] == pytest.approx(50.0)
    assert row["annual_grid_import_kWh_delta_pct"] == pytest.approx(50.0)


def test_future_seed_does_not_match_different_seed() -> None:
    future_scenario = "mid_century_2050_2070__rcp_8_5__tech_frozen_stock"
    metrics = pd.DataFrame(
        [
            _row(f"{BASELINE_SCENARIO_ID}__seed_0000", BASELINE_SCENARIO_ID, "seed_0000", 100.0),
            _row(f"{future_scenario}__seed_0001", future_scenario, "seed_0001", 150.0),
        ]
    )

    comparison = build_baseline_comparison(metrics)
    row = comparison.iloc[0]

    assert row["baseline_scenario_leaf_id"] == f"{BASELINE_SCENARIO_ID}__seed_0001"
    assert not bool(row["baseline_available"])
    assert pd.isna(row["annual_grid_import_kWh_delta_abs"])


def test_percentage_delta_avoids_division_by_zero() -> None:
    future_scenario = "mid_century_2050_2070__rcp_8_5__tech_frozen_stock"
    metrics = pd.DataFrame(
        [
            _row(f"{BASELINE_SCENARIO_ID}__seed_0000", BASELINE_SCENARIO_ID, "seed_0000", 100.0, grid_export=0.0),
            _row(f"{future_scenario}__seed_0000", future_scenario, "seed_0000", 150.0, grid_export=10.0),
        ]
    )

    comparison = build_baseline_comparison(metrics)
    row = comparison.iloc[0]

    assert row["annual_grid_export_kWh_delta_abs"] == pytest.approx(10.0)
    assert pd.isna(row["annual_grid_export_kWh_delta_pct"])
    assert "annual_grid_export_kWh" in row["zero_baseline_delta_pct_metrics"]
