from __future__ import annotations

import pandas as pd

from model_v3.scenarios.generate_comparisons import generate_comparisons
from model_v3.scenarios.validate_comparisons import validate_comparisons

from tests.model_v3.comparison_test_utils import BASELINE_SCENARIO, DEFINITIONS_PATH, FROZEN_SCENARIO, metric_row, write_experiment


def test_validator_detects_missing_output_and_duplicate_rows(tmp_path) -> None:
    root = write_experiment(
        tmp_path,
        [
            metric_row(BASELINE_SCENARIO, "seed_0000", annual_grid_import_kWh=100.0),
            metric_row(FROZEN_SCENARIO, "seed_0000", annual_grid_import_kWh=120.0),
        ],
    )
    generate_comparisons(experiment_root=root, comparison_definitions=DEFINITIONS_PATH, allow_missing_groups=True)

    missing_file = root / "summaries" / "comparison_level" / "technology_only" / "technology_only_absolute_metrics.csv"
    missing_file.unlink()
    result = validate_comparisons(experiment_root=root, comparison_definitions=DEFINITIONS_PATH)
    assert result["ok"] is False
    assert any("technology_only_absolute_metrics.csv" in error for error in result["errors"])

    generate_comparisons(experiment_root=root, comparison_definitions=DEFINITIONS_PATH, allow_missing_groups=True)
    delta_path = root / "summaries" / "comparison_level" / "climate_only" / "climate_only_delta_vs_baseline.csv"
    delta = pd.read_csv(delta_path)
    pd.concat([delta, delta], ignore_index=True).to_csv(delta_path, index=False)
    result = validate_comparisons(experiment_root=root, comparison_definitions=DEFINITIONS_PATH)
    assert result["ok"] is False
    assert any("duplicate" in error for error in result["errors"])
