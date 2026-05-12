from __future__ import annotations

import pandas as pd
import pytest

from model_v3.scenarios.generate_comparisons import generate_comparisons

from tests.model_v3.comparison_test_utils import BASELINE_SCENARIO, DEFINITIONS_PATH, STRESS_SCENARIO, metric_row, write_experiment


def test_combined_stress_case_matches_baseline_seed_and_computes_delta(tmp_path) -> None:
    root = write_experiment(
        tmp_path,
        [
            metric_row(BASELINE_SCENARIO, "seed_0000", annual_grid_import_kWh=100.0),
            metric_row(STRESS_SCENARIO, "seed_0000", annual_grid_import_kWh=160.0),
        ],
    )
    generate_comparisons(experiment_root=root, comparison_definitions=DEFINITIONS_PATH, allow_missing_groups=True)

    delta = pd.read_csv(root / "summaries" / "comparison_level" / "combined_stress_case" / "combined_stress_case_delta_vs_baseline.csv")

    assert delta.loc[0, "stress_scenario_id"] == STRESS_SCENARIO
    assert delta.loc[0, "baseline_scenario_id"] == BASELINE_SCENARIO
    assert delta.loc[0, "baseline_scenario_leaf_id"].endswith("seed_0000")
    assert delta.loc[0, "annual_grid_import_kWh_delta_abs"] == pytest.approx(60.0)
