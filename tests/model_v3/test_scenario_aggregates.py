from __future__ import annotations

import pandas as pd
import pytest

from model_v3.scenarios.summarize_outputs import aggregate_scenario_metrics
from model_v3.scenarios.summary_contract import REQUIRED_METRIC_COLUMNS


def _metric_row(leaf_id: str, scenario_id: str, realization_id: str, grid_import: float) -> dict:
    row = {
        "scenario_leaf_id": leaf_id,
        "scenario_id": scenario_id,
        "climate_window_id": "mid_century_2050_2070",
        "climate_pathway_id": "rcp_8_5",
        "technology_case_id": "tech_frozen_stock",
        "realization_id": realization_id,
    }
    for metric in REQUIRED_METRIC_COLUMNS:
        row[metric] = 1.0
    row["annual_grid_import_kWh"] = grid_import
    return row


def test_scenario_aggregation_groups_by_scenario_and_computes_statistics() -> None:
    scenario_id = "mid_century_2050_2070__rcp_8_5__tech_frozen_stock"
    metrics_df = pd.DataFrame(
        [
            _metric_row(f"{scenario_id}__seed_0000", scenario_id, "seed_0000", 100.0),
            _metric_row(f"{scenario_id}__seed_0001", scenario_id, "seed_0001", 300.0),
        ]
    )
    leaf_index_df = pd.DataFrame(
        [
            {"scenario_leaf_id": f"{scenario_id}__seed_0000", "scenario_id": scenario_id},
            {"scenario_leaf_id": f"{scenario_id}__seed_0001", "scenario_id": scenario_id},
            {"scenario_leaf_id": f"{scenario_id}__seed_0002", "scenario_id": scenario_id},
        ]
    )
    statuses = {
        f"{scenario_id}__seed_0000": "success",
        f"{scenario_id}__seed_0001": "success",
        f"{scenario_id}__seed_0002": "failed",
    }

    aggregate = aggregate_scenario_metrics(metrics_df, leaf_index_df=leaf_index_df, status_by_leaf=statuses)
    row = aggregate.iloc[0]

    assert len(aggregate) == 1
    assert row["scenario_id"] == scenario_id
    assert row["annual_grid_import_kWh_mean"] == pytest.approx(200.0)
    assert row["annual_grid_import_kWh_median"] == pytest.approx(200.0)
    assert row["annual_grid_import_kWh_p10"] == pytest.approx(120.0)
    assert row["annual_grid_import_kWh_p90"] == pytest.approx(280.0)
    assert row["n_successful_realizations"] == 2
    assert row["n_failed_realizations"] == 1
    assert row["realization_coverage_fraction"] == pytest.approx(2 / 3)


def test_failed_runs_are_not_treated_as_successful_metrics() -> None:
    scenario_id = "mid_century_2050_2070__rcp_8_5__tech_frozen_stock"
    metrics_df = pd.DataFrame([_metric_row(f"{scenario_id}__seed_0000", scenario_id, "seed_0000", 100.0)])
    leaf_index_df = pd.DataFrame(
        [
            {"scenario_leaf_id": f"{scenario_id}__seed_0000", "scenario_id": scenario_id},
            {"scenario_leaf_id": f"{scenario_id}__seed_0001", "scenario_id": scenario_id},
        ]
    )
    statuses = {f"{scenario_id}__seed_0000": "success", f"{scenario_id}__seed_0001": "failed"}

    aggregate = aggregate_scenario_metrics(metrics_df, leaf_index_df=leaf_index_df, status_by_leaf=statuses)

    assert aggregate.iloc[0]["n_successful_realizations"] == 1
    assert aggregate.iloc[0]["n_failed_realizations"] == 1
