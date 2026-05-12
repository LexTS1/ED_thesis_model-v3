from __future__ import annotations

from pathlib import Path

import pandas as pd

from model_v3.scenarios.summary_contract import REQUIRED_METRIC_COLUMNS


BASELINE_SCENARIO = "baseline_1981_2005__historical__tech_current_stock"
FROZEN_SCENARIO = "near_future_2030_2049__rcp_2_6__tech_frozen_stock"
MODERATE_SCENARIO = "near_future_2030_2049__rcp_2_6__tech_moderate_electrification"
HIGH_SCENARIO = "near_future_2030_2049__rcp_2_6__tech_high_electrification_pv_ev"
STRESS_SCENARIO = "long_term_2080_2100__rcp_8_5__tech_high_electrification_pv_ev"
DEFINITIONS_PATH = Path("config/model_v3/scenario_tree/comparison_definitions.yaml")


def metric_row(scenario_id: str, realization_id: str, **metric_values: float) -> dict:
    window, pathway, technology = scenario_id.split("__")
    row = {
        "scenario_leaf_id": f"{scenario_id}__{realization_id}",
        "scenario_id": scenario_id,
        "climate_window_id": window,
        "climate_pathway_id": pathway,
        "technology_case_id": technology,
        "realization_id": realization_id,
        "climate_includes_2050": window == "mid_century_2050_2070",
    }
    for metric in REQUIRED_METRIC_COLUMNS:
        row[metric] = metric_values.get(metric, 1.0)
    return row


def write_experiment(tmp_path: Path, rows: list[dict]) -> Path:
    root = tmp_path / "scenario_tree"
    realization_dir = root / "summaries" / "realization_level"
    scenario_dir = root / "summaries" / "scenario_level"
    manifests_dir = root / "manifests"
    realization_dir.mkdir(parents=True)
    scenario_dir.mkdir(parents=True)
    manifests_dir.mkdir(parents=True)
    metrics = pd.DataFrame(rows)
    metrics.to_csv(realization_dir / "scenario_leaf_metrics.csv", index=False)
    metrics[["scenario_id", "climate_window_id", "climate_pathway_id", "technology_case_id"]].drop_duplicates().to_csv(
        scenario_dir / "scenario_aggregate_metrics.csv",
        index=False,
    )
    metrics[
        [
            "scenario_leaf_id",
            "scenario_id",
            "climate_window_id",
            "climate_pathway_id",
            "technology_case_id",
            "realization_id",
        ]
    ].to_csv(manifests_dir / "scenario_leaf_index.csv", index=False)
    return root
