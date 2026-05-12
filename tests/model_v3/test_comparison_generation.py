from __future__ import annotations

from model_v3.scenarios.generate_comparisons import generate_comparisons

from tests.model_v3.comparison_test_utils import BASELINE_SCENARIO, DEFINITIONS_PATH, FROZEN_SCENARIO, metric_row, write_experiment


def test_generate_comparisons_writes_index_and_outputs(tmp_path) -> None:
    root = write_experiment(
        tmp_path,
        [
            metric_row(BASELINE_SCENARIO, "seed_0000", annual_grid_import_kWh=100.0),
            metric_row(FROZEN_SCENARIO, "seed_0000", annual_grid_import_kWh=120.0),
        ],
    )

    result = generate_comparisons(
        experiment_root=root,
        comparison_definitions=DEFINITIONS_PATH,
        allow_missing_groups=True,
    )

    assert (root / "summaries" / "comparison_level" / "comparison_index.csv").exists()
    assert (root / "manifests" / "comparison_validation_report.md").exists()
    assert result["results"]["climate_only"]["diagnostics"]["valid_pairs"] == 1
