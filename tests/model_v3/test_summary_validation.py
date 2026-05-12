from __future__ import annotations

from pathlib import Path

import pandas as pd

from model_v3.scenarios.summarize_outputs import aggregate_scenario_metrics, build_baseline_comparison
from model_v3.scenarios.summary_contract import REQUIRED_METRIC_COLUMNS, SUMMARY_COLUMNS
from model_v3.scenarios.validate_summaries import validate_summary_outputs


BASELINE_SCENARIO = "baseline_1981_2005__historical__tech_current_stock"
BASELINE_LEAF = f"{BASELINE_SCENARIO}__seed_0000"


def _summary_row(outputs_dir: Path) -> dict:
    row = {column: "" for column in SUMMARY_COLUMNS}
    row.update(
        {
            "scenario_leaf_id": BASELINE_LEAF,
            "scenario_id": BASELINE_SCENARIO,
            "climate_window_id": "baseline_1981_2005",
            "climate_pathway_id": "historical",
            "technology_case_id": "tech_current_stock",
            "realization_id": "seed_0000",
            "seed_index": 0,
            "seed_value": 0,
            "cohort_size": 100,
            "analysis_start": "1981-01-01",
            "analysis_end": "2005-12-31",
            "source_file_window": "1981-2005",
            "run_status": "success",
            "run_attempt_id": "attempt",
            "run_timestamp_utc": "2026-05-09T00:00:00Z",
            "config_hash_sha256": "hash",
            "climate_forcing_file": "climate.csv",
            "technology_inputs_file": "tech.yaml",
            "raw_outputs_dir": str(outputs_dir),
            "missing_metric_count": 0,
            "climate_temperature_column": "T_out_C",
            "climate_solar_column": "I_solar_W_m2",
            "climate_included_years": "1981",
            "climate_includes_2050": False,
        }
    )
    for metric in REQUIRED_METRIC_COLUMNS:
        row[metric] = 1.0
    return row


def test_summary_validator_passes_minimal_valid_outputs(tmp_path: Path) -> None:
    experiment_root = tmp_path / "scenario_tree"
    manifests = experiment_root / "manifests"
    outputs_dir = experiment_root / "runs" / BASELINE_LEAF / "outputs"
    summaries = experiment_root / "summaries"
    manifests.mkdir(parents=True)
    outputs_dir.mkdir(parents=True)

    leaf_index = pd.DataFrame(
        [
            {
                "scenario_leaf_id": BASELINE_LEAF,
                "scenario_id": BASELINE_SCENARIO,
                "climate_window_id": "baseline_1981_2005",
                "climate_pathway_id": "historical",
                "technology_case_id": "tech_current_stock",
                "realization_id": "seed_0000",
                "outputs_dir": str(outputs_dir),
            }
        ]
    )
    leaf_index.to_csv(manifests / "scenario_leaf_index.csv", index=False)
    pd.DataFrame(
        [
            {
                "run_attempt_id": "attempt",
                "scenario_leaf_id": BASELINE_LEAF,
                "timestamp_start_utc": "2026-05-09T00:00:00Z",
                "status": "success",
            }
        ]
    ).to_csv(manifests / "run_registry.csv", index=False)

    summary_df = pd.DataFrame([_summary_row(outputs_dir)], columns=SUMMARY_COLUMNS)
    summary_df.to_csv(outputs_dir / "standardized_leaf_summary.csv", index=False)
    realization_dir = summaries / "realization_level"
    scenario_dir = summaries / "scenario_level"
    comparison_dir = summaries / "comparison_level"
    realization_dir.mkdir(parents=True)
    scenario_dir.mkdir(parents=True)
    comparison_dir.mkdir(parents=True)
    summary_df.to_csv(realization_dir / "scenario_leaf_metrics.csv", index=False)
    aggregate_scenario_metrics(
        summary_df,
        leaf_index_df=leaf_index,
        status_by_leaf={BASELINE_LEAF: "success"},
    ).to_csv(scenario_dir / "scenario_aggregate_metrics.csv", index=False)
    build_baseline_comparison(summary_df).to_csv(comparison_dir / "baseline_comparison_metrics.csv", index=False)

    result = validate_summary_outputs(experiment_root)

    assert result["ok"] is True
    assert result["successful_runs"] == 1
    assert result["per_leaf_summaries"] == 1
    assert (manifests / "summary_validation_report.md").exists()
