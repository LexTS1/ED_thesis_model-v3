from __future__ import annotations

from pathlib import Path

import pandas as pd

from model_v3.scenarios.summary_contract import BASELINE_SCENARIO_ID, REQUIRED_METRIC_COLUMNS


STRESS_SCENARIO_ID = "mid_century_2050_2070__rcp_8_5__tech_high_electrification_pv_ev"


def aggregate_row(
    scenario_id: str,
    climate_window_id: str,
    climate_pathway_id: str,
    technology_case_id: str,
    value_offset: float,
) -> dict:
    row = {
        "scenario_id": scenario_id,
        "climate_window_id": climate_window_id,
        "climate_pathway_id": climate_pathway_id,
        "technology_case_id": technology_case_id,
        "n_successful_realizations": 2,
    }
    for idx, metric in enumerate(REQUIRED_METRIC_COLUMNS, start=1):
        value = value_offset + float(idx)
        row[f"{metric}_mean"] = value
        row[f"{metric}_median"] = value
        row[f"{metric}_p05"] = value - 0.2
        row[f"{metric}_p10"] = value - 0.1
        row[f"{metric}_p90"] = value + 0.1
        row[f"{metric}_p95"] = value + 0.2
    return row


def realization_row(
    scenario_id: str,
    climate_window_id: str,
    climate_pathway_id: str,
    technology_case_id: str,
    realization_id: str,
    includes_2050: bool,
    value_offset: float,
) -> dict:
    row = {
        "scenario_leaf_id": f"{scenario_id}__{realization_id}",
        "scenario_id": scenario_id,
        "climate_window_id": climate_window_id,
        "climate_pathway_id": climate_pathway_id,
        "technology_case_id": technology_case_id,
        "realization_id": realization_id,
        "climate_includes_2050": includes_2050,
    }
    for idx, metric in enumerate(REQUIRED_METRIC_COLUMNS, start=1):
        row[metric] = value_offset + float(idx)
    return row


def write_minimal_figure_experiment(tmp_path: Path) -> tuple[Path, Path]:
    experiment_root = tmp_path / "scenario_tree"
    figures_root = tmp_path / "figures" / "scenario_tree"
    realization_dir = experiment_root / "summaries" / "realization_level"
    aggregate_dir = experiment_root / "summaries" / "scenario_level"
    bands_dir = experiment_root / "summaries" / "comparison_level" / "stochastic_robustness"
    stress_dir = experiment_root / "summaries" / "comparison_level" / "combined_stress_case"
    manifest_dir = experiment_root / "manifests"
    for directory in [realization_dir, aggregate_dir, bands_dir, stress_dir, manifest_dir]:
        directory.mkdir(parents=True, exist_ok=True)

    aggregate = pd.DataFrame(
        [
            aggregate_row(BASELINE_SCENARIO_ID, "baseline_1981_2005", "historical", "tech_current_stock", 10.0),
            aggregate_row(STRESS_SCENARIO_ID, "mid_century_2050_2070", "rcp_8_5", "tech_high_electrification_pv_ev", 20.0),
        ]
    )
    aggregate.to_csv(aggregate_dir / "scenario_aggregate_metrics.csv", index=False)

    realization = pd.DataFrame(
        [
            realization_row(BASELINE_SCENARIO_ID, "baseline_1981_2005", "historical", "tech_current_stock", "seed_0000", False, 10.0),
            realization_row(STRESS_SCENARIO_ID, "mid_century_2050_2070", "rcp_8_5", "tech_high_electrification_pv_ev", "seed_0000", True, 20.0),
        ]
    )
    realization.to_csv(realization_dir / "scenario_leaf_metrics.csv", index=False)
    realization[["scenario_leaf_id", "scenario_id", "climate_window_id", "climate_pathway_id", "technology_case_id", "realization_id"]].to_csv(
        manifest_dir / "scenario_leaf_index.csv",
        index=False,
    )

    band_rows = []
    for _, scenario in aggregate.iterrows():
        for metric in REQUIRED_METRIC_COLUMNS:
            value = float(scenario[f"{metric}_mean"])
            band_rows.append(
                {
                    "comparison_name": "stochastic_robustness",
                    "comparison_type": "stochastic_robustness",
                    "scenario_id": scenario["scenario_id"],
                    "climate_window_id": scenario["climate_window_id"],
                    "climate_pathway_id": scenario["climate_pathway_id"],
                    "technology_case_id": scenario["technology_case_id"],
                    "metric": metric,
                    "count": 2,
                    "mean": value,
                    "std": 0.1,
                    "min": value - 0.2,
                    "max": value + 0.2,
                    "p05": value - 0.2,
                    "p10": value - 0.1,
                    "p50": value,
                    "p90": value + 0.1,
                    "p95": value + 0.2,
                }
            )
    pd.DataFrame(band_rows).to_csv(bands_dir / "stochastic_uncertainty_bands.csv", index=False)
    pd.DataFrame().to_csv(stress_dir / "combined_stress_case_absolute_metrics.csv", index=False)
    return experiment_root, figures_root
