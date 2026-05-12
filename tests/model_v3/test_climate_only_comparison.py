from __future__ import annotations

import pandas as pd
import pytest

from model_v3.scenarios.generate_comparisons import generate_comparisons

from tests.model_v3.comparison_test_utils import BASELINE_SCENARIO, DEFINITIONS_PATH, FROZEN_SCENARIO, metric_row, write_experiment


def test_climate_only_uses_frozen_stock_matches_seed_and_computes_deltas(tmp_path) -> None:
    root = write_experiment(
        tmp_path,
        [
            metric_row(BASELINE_SCENARIO, "seed_0000", annual_grid_import_kWh=100.0, annual_grid_export_kWh=0.0),
            metric_row(FROZEN_SCENARIO, "seed_0000", annual_grid_import_kWh=150.0, annual_grid_export_kWh=10.0),
        ],
    )
    generate_comparisons(experiment_root=root, comparison_definitions=DEFINITIONS_PATH, allow_missing_groups=True)

    delta = pd.read_csv(root / "summaries" / "comparison_level" / "climate_only" / "climate_only_delta_vs_baseline.csv")
    pct = pd.read_csv(root / "summaries" / "comparison_level" / "climate_only" / "climate_only_percentage_change_vs_baseline.csv")

    assert delta.loc[0, "technology_case_id"] == "tech_frozen_stock"
    assert "tech_current_stock" not in set(delta["technology_case_id"])
    assert delta.loc[0, "baseline_scenario_leaf_id"].endswith("seed_0000")
    assert delta.loc[0, "annual_grid_import_kWh_delta_abs"] == pytest.approx(50.0)
    assert pct.loc[0, "annual_grid_import_kWh_delta_pct"] == pytest.approx(50.0)
    assert pd.isna(pct.loc[0, "annual_grid_export_kWh_delta_pct"])
    assert bool(pct.loc[0, "pct_change_division_by_zero"])
