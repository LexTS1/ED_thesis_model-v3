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
    comparison_dir = experiment_root / "summaries" / "comparison_level"
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
    pd.DataFrame(
        [
            {
                "scenario_id": BASELINE_SCENARIO_ID,
                "climate_window_id": "baseline_1981_2005",
                "climate_pathway_id": "historical",
                "technology_case_id": "tech_current_stock",
                "baseline_scenario_id": BASELINE_SCENARIO_ID,
                "n_climate_year_samples": 2,
                "HDD_18_mean": 3000.0,
                "HDD_18_p10": 2900.0,
                "HDD_18_p50": 3000.0,
                "HDD_18_p90": 3100.0,
                "CDD_22_mean": 10.0,
                "CDD_22_p10": 5.0,
                "CDD_22_p50": 10.0,
                "CDD_22_p90": 15.0,
                "baseline_HDD_18_mean": 3000.0,
                "baseline_CDD_22_mean": 10.0,
                "delta_HDD_18_abs": 0.0,
                "delta_HDD_18_pct": 0.0,
                "delta_CDD_22_abs": 0.0,
                "delta_CDD_22_pct": 0.0,
            },
            {
                "scenario_id": "near_future_2030_2049__rcp_8_5__tech_frozen_stock",
                "climate_window_id": "near_future_2030_2049",
                "climate_pathway_id": "rcp_8_5",
                "technology_case_id": "tech_frozen_stock",
                "baseline_scenario_id": BASELINE_SCENARIO_ID,
                "n_climate_year_samples": 2,
                "HDD_18_mean": 2500.0,
                "HDD_18_p10": 2400.0,
                "HDD_18_p50": 2500.0,
                "HDD_18_p90": 2600.0,
                "CDD_22_mean": 35.0,
                "CDD_22_p10": 25.0,
                "CDD_22_p50": 35.0,
                "CDD_22_p90": 45.0,
                "baseline_HDD_18_mean": 3000.0,
                "baseline_CDD_22_mean": 10.0,
                "delta_HDD_18_abs": -500.0,
                "delta_HDD_18_pct": -16.6666667,
                "delta_CDD_22_abs": 25.0,
                "delta_CDD_22_pct": 250.0,
            },
        ]
    ).to_csv(comparison_dir / "annual_climate_degree_day_comparison.csv", index=False)
    monthly_shift_rows = []
    for scenario_id, window, pathway, tech, heat, cdd in [
        (BASELINE_SCENARIO_ID, "baseline_1981_2005", "historical", "tech_current_stock", 100.0, 1.0),
        ("near_future_2030_2049__rcp_8_5__tech_frozen_stock", "near_future_2030_2049", "rcp_8_5", "tech_frozen_stock", 70.0, 5.0),
    ]:
        for month in range(1, 13):
            row = {
                "scenario_id": scenario_id,
                "climate_window_id": window,
                "climate_pathway_id": pathway,
                "technology_case_id": tech,
                "baseline_scenario_id": BASELINE_SCENARIO_ID,
                "month": month,
                "n_month_samples": 1,
                "active_cooling_final_energy_kWh_included": False,
                "interpretation_note": "cooling pressure only",
            }
            for metric, value in {
                "space_heating_useful_kWh": heat if month in {1, 2, 12} else heat / 2.0,
                "electricity_gross_kWh": 10.0,
                "grid_import_kWh": 8.0,
                "gas_kWh": 20.0,
                "total_final_energy_kWh": 30.0,
                "CDD_22": cdd if month in {6, 7, 8} else 0.0,
                "excess_heat_kWh": cdd,
                "overheating_hours": cdd * 2.0,
                "indoor_temperature_exceedance_degree_hours": cdd * 3.0,
                "max_indoor_temperature_C": 26.0 + cdd,
            }.items():
                prefix = f"monthly_{metric}"
                row[f"{prefix}_mean"] = value
                row[f"{prefix}_p10"] = value
                row[f"{prefix}_p50"] = value
                row[f"{prefix}_p90"] = value
                row[f"baseline_{prefix}_mean"] = value
                row[f"delta_{prefix}_abs"] = 0.0
                row[f"delta_{prefix}_pct"] = 0.0
            monthly_shift_rows.append(row)
    pd.DataFrame(monthly_shift_rows).to_csv(comparison_dir / "monthly_demand_shift_comparison.csv", index=False)

    seasonal_shift_rows = []
    for scenario_id, window, pathway, tech, heat in [
        (BASELINE_SCENARIO_ID, "baseline_1981_2005", "historical", "tech_current_stock", 100.0),
        ("near_future_2030_2049__rcp_8_5__tech_frozen_stock", "near_future_2030_2049", "rcp_8_5", "tech_frozen_stock", 70.0),
    ]:
        seasonal_heating = {
            "winter": heat * 3.0,
            "spring": heat * 1.5,
            "summer": heat * 1.5,
            "autumn": heat * 1.5,
            "shoulder": heat * 3.0,
            "annual": heat * 7.5,
        }
        for season, value in seasonal_heating.items():
            row = {
                "scenario_id": scenario_id,
                "climate_window_id": window,
                "climate_pathway_id": pathway,
                "technology_case_id": tech,
                "baseline_scenario_id": BASELINE_SCENARIO_ID,
                "season": season,
                "n_season_samples": 1,
                "active_cooling_final_energy_kWh_included": False,
                "interpretation_note": "cooling pressure only",
            }
            for metric, metric_value in {
                "space_heating_useful_kWh": value,
                "electricity_gross_kWh": 30.0,
                "grid_import_kWh": 24.0,
                "gas_kWh": 60.0,
                "total_final_energy_kWh": 90.0,
                "CDD_22": 15.0 if season == "summer" else 0.0,
                "excess_heat_kWh": 5.0,
                "overheating_hours": 10.0,
                "indoor_temperature_exceedance_degree_hours": 20.0,
                "max_indoor_temperature_C": 30.0,
            }.items():
                prefix = f"seasonal_{metric}"
                row[f"{prefix}_mean"] = metric_value
                row[f"{prefix}_p10"] = metric_value
                row[f"{prefix}_p50"] = metric_value
                row[f"{prefix}_p90"] = metric_value
                row[f"baseline_{prefix}_mean"] = metric_value
                row[f"delta_{prefix}_abs"] = 0.0
                row[f"delta_{prefix}_pct"] = 0.0
            share = 100.0 if season == "annual" else value / seasonal_heating["annual"] * 100.0
            row["seasonal_heating_share_pct_mean"] = share
            row["seasonal_heating_share_pct_p10"] = share
            row["seasonal_heating_share_pct_p50"] = share
            row["seasonal_heating_share_pct_p90"] = share
            row["baseline_seasonal_heating_share_pct_mean"] = share
            row["delta_seasonal_heating_share_pct_abs"] = 0.0
            seasonal_shift_rows.append(row)
    pd.DataFrame(seasonal_shift_rows).to_csv(comparison_dir / "seasonal_demand_shift_comparison.csv", index=False)
    pd.DataFrame().to_csv(stress_dir / "combined_stress_case_absolute_metrics.csv", index=False)
    return experiment_root, figures_root
