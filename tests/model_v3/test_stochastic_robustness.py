from __future__ import annotations

import pandas as pd
import pytest

from model_v3.scenarios.generate_comparisons import _metric_stats, generate_comparisons

from tests.model_v3.comparison_test_utils import DEFINITIONS_PATH, FROZEN_SCENARIO, metric_row, write_experiment


def test_stochastic_statistics_quantiles_iqr_and_zero_mean_cv() -> None:
    stats = _metric_stats(pd.Series([1.0, 2.0, 3.0, 4.0]))
    assert stats["p10"] == pytest.approx(1.3)
    assert stats["p50"] == pytest.approx(2.5)
    assert stats["p90"] == pytest.approx(3.7)
    assert stats["iqr"] == pytest.approx(1.5)
    zero_mean = _metric_stats(pd.Series([-1.0, 1.0]))
    assert pd.isna(zero_mean["coefficient_of_variation"])


def test_stochastic_grouping_by_scenario(tmp_path) -> None:
    root = write_experiment(
        tmp_path,
        [
            metric_row(FROZEN_SCENARIO, "seed_0000", annual_grid_import_kWh=100.0),
            metric_row(FROZEN_SCENARIO, "seed_0001", annual_grid_import_kWh=200.0),
        ],
    )
    generate_comparisons(experiment_root=root, comparison_definitions=DEFINITIONS_PATH, allow_missing_groups=True)

    spread = pd.read_csv(root / "summaries" / "comparison_level" / "stochastic_robustness" / "stochastic_spread_metrics.csv")
    row = spread[spread["metric"] == "annual_grid_import_kWh"].iloc[0]
    assert row["scenario_id"] == FROZEN_SCENARIO
    assert row["count"] == pytest.approx(2.0)
    assert row["p50"] == pytest.approx(150.0)
