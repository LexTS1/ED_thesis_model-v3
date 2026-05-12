from __future__ import annotations

import pandas as pd
import pytest

from model_v3.scenarios.generate_comparisons import generate_comparisons

from tests.model_v3.comparison_test_utils import (
    DEFINITIONS_PATH,
    FROZEN_SCENARIO,
    HIGH_SCENARIO,
    MODERATE_SCENARIO,
    metric_row,
    write_experiment,
)


def test_technology_only_uses_frozen_reference_and_computes_deltas(tmp_path) -> None:
    root = write_experiment(
        tmp_path,
        [
            metric_row(FROZEN_SCENARIO, "seed_0000", annual_grid_import_kWh=100.0),
            metric_row(MODERATE_SCENARIO, "seed_0000", annual_grid_import_kWh=80.0),
            metric_row(HIGH_SCENARIO, "seed_0000", annual_grid_import_kWh=60.0),
        ],
    )
    generate_comparisons(experiment_root=root, comparison_definitions=DEFINITIONS_PATH, allow_missing_groups=True)

    delta = pd.read_csv(root / "summaries" / "comparison_level" / "technology_only" / "technology_only_delta_vs_frozen_stock.csv")

    assert set(delta["compared_technology_case_id"]) == {
        "tech_moderate_electrification",
        "tech_high_electrification_pv_ev",
    }
    assert set(delta["reference_technology_case_id"]) == {"tech_frozen_stock"}
    assert all(delta["compared_scenario_leaf_id"].str.endswith("seed_0000"))
    assert all(delta["reference_scenario_leaf_id"].str.endswith("seed_0000"))
    moderate = delta[delta["compared_technology_case_id"] == "tech_moderate_electrification"].iloc[0]
    assert moderate["annual_grid_import_kWh_delta_abs"] == pytest.approx(-20.0)
