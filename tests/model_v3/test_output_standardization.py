from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest
import yaml

from model_v3.scenarios.output_reader import (
    MissingRequiredOutputError,
    compute_standardized_output_metrics,
)
from model_v3.scenarios.monthly_metrics import compute_monthly_metrics
from model_v3.scenarios.summarize_outputs import (
    build_annual_climate_degree_day_comparison,
    build_annual_space_heating_demand_comparison,
    build_cooling_exposure_overheating_risk_comparison,
    build_monthly_demand_shift_comparison,
    build_seasonal_demand_shift_comparison,
    write_per_leaf_summary,
)
from model_v3.scenarios.summary_contract import REQUIRED_METRIC_COLUMNS, SUMMARY_COLUMNS


def _technology_config(tmp_path: Path, *, pv: bool = False, ev: bool = False, heat_pump: bool = False) -> dict:
    metadata_path = tmp_path / "technology_cases.yaml"
    metadata_path.write_text(
        yaml.safe_dump(
            {
                "technology_cases": {
                    "case": {
                        "pv_assumed": pv,
                        "ev_adoption_assumed": ev,
                        "heat_pump_adoption_assumed": heat_pump,
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    return {"technology": {"metadata_file": str(metadata_path), "case_id": "case"}}


def _raw_outputs(frame: pd.DataFrame, summary: dict | None = None) -> dict:
    raw = {"annual_profile": frame, "files": {"annual_profile": Path("annual_profile.csv")}}
    if summary is not None:
        raw["annual_summary"] = summary
        raw["files"]["annual_summary"] = Path("annual_summary.json")
    return raw


def _profile(**overrides) -> pd.DataFrame:
    data = {
        "timestamp": ["2030-01-01T12:00:00", "2030-01-02T12:00:00", "2030-07-01T12:00:00"],
        "P_el_gross_actual_W": [1000.0, 2000.0, 500.0],
        "P_el_grid_import_W": [800.0, 1500.0, 300.0],
        "P_el_grid_export_W": [0.0, 0.0, 50.0],
        "P_gas_total_W": [0.0, 0.0, 0.0],
        "Q_heating_supplied_W": [5000.0, 1000.0, 0.0],
        "Q_dhw_demand_W": [200.0, 200.0, 200.0],
        "P_pv_generation_W": [0.0, 0.0, 100.0],
        "P_el_ev_charging_W": [0.0, 0.0, 0.0],
    }
    data.update(overrides)
    return pd.DataFrame(data)


def test_standardized_summary_file_has_one_row(tmp_path: Path) -> None:
    row = {column: 0 for column in SUMMARY_COLUMNS}
    row.update({"scenario_leaf_id": "leaf", "raw_outputs_dir": str(tmp_path)})

    path = write_per_leaf_summary(row)
    written = pd.read_csv(path)

    assert path.name == "standardized_leaf_summary.csv"
    assert len(written) == 1


def test_annual_space_heating_comparison_uses_baseline_mean(tmp_path: Path) -> None:
    baseline_dir = tmp_path / "baseline"
    future_dir = tmp_path / "future"
    baseline_dir.mkdir()
    future_dir.mkdir()
    pd.DataFrame(
        {
            "timestamp": ["2030-01-01T00:00:00Z", "2030-01-02T00:00:00Z", "2031-01-01T00:00:00Z"],
            "Q_heating_supplied_W": [1000.0, 1000.0, 1000.0],
        }
    ).to_csv(baseline_dir / "annual_profile.csv", index=False)
    pd.DataFrame(
        {
            "timestamp": ["2030-01-01T00:00:00Z", "2030-01-02T00:00:00Z", "2031-01-01T00:00:00Z"],
            "Q_heating_supplied_W": [850.0, 850.0, 850.0],
        }
    ).to_csv(future_dir / "annual_profile.csv", index=False)
    metrics = pd.DataFrame(
        [
            {
                "scenario_id": "baseline_1981_2005__historical__tech_current_stock",
                "climate_window_id": "baseline_1981_2005",
                "climate_pathway_id": "historical",
                "technology_case_id": "tech_current_stock",
                "realization_id": "seed_0000",
                "raw_outputs_dir": str(baseline_dir),
            },
            {
                "scenario_id": "near_future_2030_2049__rcp_4_5__tech_frozen_stock",
                "climate_window_id": "near_future_2030_2049",
                "climate_pathway_id": "rcp_4_5",
                "technology_case_id": "tech_frozen_stock",
                "realization_id": "seed_0000",
                "raw_outputs_dir": str(future_dir),
            },
        ]
    )

    comparison = build_annual_space_heating_demand_comparison(metrics)
    future = comparison[comparison["scenario_id"].str.contains("near_future")].iloc[0]

    assert future["baseline_annual_useful_heating_kWh_mean"] == pytest.approx(24.0)
    assert future["annual_useful_heating_kWh_p50"] == pytest.approx(20.4)
    assert future["delta_annual_useful_heating_kWh_mean"] == pytest.approx(-3.6)
    assert future["delta_annual_useful_heating_kWh_pct"] == pytest.approx(-15.0)


def test_annual_climate_degree_day_comparison_deduplicates_seed_repeats() -> None:
    climate_years = pd.DataFrame(
        [
            {
                "scenario_id": "baseline_1981_2005__historical__tech_current_stock",
                "climate_window_id": "baseline_1981_2005",
                "climate_pathway_id": "historical",
                "technology_case_id": "tech_current_stock",
                "realization_id": "seed_0000",
                "year": 2000,
                "HDD_18": 3000.0,
                "CDD_22": 10.0,
            },
            {
                "scenario_id": "baseline_1981_2005__historical__tech_current_stock",
                "climate_window_id": "baseline_1981_2005",
                "climate_pathway_id": "historical",
                "technology_case_id": "tech_current_stock",
                "realization_id": "seed_0001",
                "year": 2000,
                "HDD_18": 3000.0,
                "CDD_22": 10.0,
            },
            {
                "scenario_id": "baseline_1981_2005__historical__tech_current_stock",
                "climate_window_id": "baseline_1981_2005",
                "climate_pathway_id": "historical",
                "technology_case_id": "tech_current_stock",
                "realization_id": "seed_0000",
                "year": 2001,
                "HDD_18": 2800.0,
                "CDD_22": 20.0,
            },
            {
                "scenario_id": "near_future_2030_2049__rcp_4_5__tech_frozen_stock",
                "climate_window_id": "near_future_2030_2049",
                "climate_pathway_id": "rcp_4_5",
                "technology_case_id": "tech_frozen_stock",
                "realization_id": "seed_0000",
                "year": 2030,
                "HDD_18": 2400.0,
                "CDD_22": 40.0,
            },
            {
                "scenario_id": "near_future_2030_2049__rcp_4_5__tech_frozen_stock",
                "climate_window_id": "near_future_2030_2049",
                "climate_pathway_id": "rcp_4_5",
                "technology_case_id": "tech_frozen_stock",
                "realization_id": "seed_0001",
                "year": 2030,
                "HDD_18": 2400.0,
                "CDD_22": 40.0,
            },
        ]
    )

    comparison = build_annual_climate_degree_day_comparison(climate_years)
    baseline = comparison[comparison["scenario_id"].str.contains("baseline")].iloc[0]
    future = comparison[comparison["scenario_id"].str.contains("near_future")].iloc[0]

    assert baseline["n_climate_year_samples"] == 2
    assert future["n_climate_year_samples"] == 1
    assert future["baseline_HDD_18_mean"] == pytest.approx(2900.0)
    assert future["delta_HDD_18_abs"] == pytest.approx(-500.0)
    assert future["delta_HDD_18_pct"] == pytest.approx(-17.2413793103)
    assert future["delta_CDD_22_abs"] == pytest.approx(25.0)
    assert future["delta_CDD_22_pct"] == pytest.approx(166.6666666667)


def test_cooling_exposure_comparison_reports_comfort_not_active_cooling(tmp_path: Path) -> None:
    baseline_dir = tmp_path / "baseline" / "outputs"
    future_dir = tmp_path / "future" / "outputs"
    baseline_dir.mkdir(parents=True)
    future_dir.mkdir(parents=True)
    pd.DataFrame(
        {
            "timestamp": ["2030-07-01T00:00:00Z", "2030-07-01T01:00:00Z", "2030-07-01T02:00:00Z"],
            "T_indoor_next_C": [25.0, 26.5, 27.0],
            "Q_excess_heat_W": [0.0, 100.0, 200.0],
        }
    ).to_csv(baseline_dir / "annual_profile.csv", index=False)
    pd.DataFrame(
        {
            "timestamp": ["2030-07-01T00:00:00Z", "2030-07-01T01:00:00Z", "2030-07-01T02:00:00Z"],
            "T_indoor_next_C": [27.0, 28.0, 29.0],
            "Q_excess_heat_W": [100.0, 200.0, 300.0],
        }
    ).to_csv(future_dir / "annual_profile.csv", index=False)
    metrics = pd.DataFrame(
        [
            {
                "scenario_id": "baseline_1981_2005__historical__tech_current_stock",
                "climate_window_id": "baseline_1981_2005",
                "climate_pathway_id": "historical",
                "technology_case_id": "tech_current_stock",
                "realization_id": "seed_0000",
                "raw_outputs_dir": str(baseline_dir),
            },
            {
                "scenario_id": "near_future_2030_2049__rcp_8_5__tech_frozen_stock",
                "climate_window_id": "near_future_2030_2049",
                "climate_pathway_id": "rcp_8_5",
                "technology_case_id": "tech_frozen_stock",
                "realization_id": "seed_0000",
                "raw_outputs_dir": str(future_dir),
            },
        ]
    )
    climate_years = pd.DataFrame(
        [
            {
                "scenario_id": "baseline_1981_2005__historical__tech_current_stock",
                "climate_window_id": "baseline_1981_2005",
                "climate_pathway_id": "historical",
                "technology_case_id": "tech_current_stock",
                "year": 2030,
                "HDD_18": 3000.0,
                "CDD_22": 10.0,
            },
            {
                "scenario_id": "near_future_2030_2049__rcp_8_5__tech_frozen_stock",
                "climate_window_id": "near_future_2030_2049",
                "climate_pathway_id": "rcp_8_5",
                "technology_case_id": "tech_frozen_stock",
                "year": 2030,
                "HDD_18": 2500.0,
                "CDD_22": 40.0,
            },
        ]
    )

    comparison = build_cooling_exposure_overheating_risk_comparison(metrics, climate_years)
    future = comparison[comparison["scenario_id"].str.contains("near_future")].iloc[0]

    assert bool(future["active_cooling_final_energy_kWh_included"]) is False
    assert future["CDD_22_mean"] == pytest.approx(40.0)
    assert future["overheating_hours_mean"] == pytest.approx(3.0)
    assert future["indoor_temperature_exceedance_degree_hours_mean"] == pytest.approx(6.0)
    assert future["excess_heat_kWh_mean"] == pytest.approx(0.6)
    assert future["delta_CDD_22_abs"] == pytest.approx(30.0)
    assert "cooling electricity consumption" in future["interpretation_note"]


def test_monthly_metrics_include_comfort_timing_fields() -> None:
    frame = pd.DataFrame(
        {
            "timestamp": [
                "2030-07-01T00:00:00Z",
                "2030-07-01T01:00:00Z",
                "2030-07-01T02:00:00Z",
            ],
            "T_outdoor_C": [23.0, 24.0, 25.0],
            "T_indoor_next_C": [25.5, 26.5, 28.0],
            "P_el_gross_actual_W": [1000.0, 1000.0, 1000.0],
            "P_el_grid_import_W": [1000.0, 1000.0, 1000.0],
            "P_el_grid_export_W": [0.0, 0.0, 0.0],
            "P_gas_total_W": [0.0, 0.0, 0.0],
            "Q_heating_supplied_W": [0.0, 0.0, 0.0],
            "Q_dhw_demand_W": [0.0, 0.0, 0.0],
            "Q_excess_heat_W": [0.0, 100.0, 200.0],
        }
    )

    result = compute_monthly_metrics(
        frame,
        leaf_id="leaf",
        scenario_id="scenario",
        climate_window_id="window",
        climate_pathway_id="pathway",
        technology_case_id="tech",
        realization_id="seed_0000",
        overheating_threshold_C=26.0,
    )
    row = result.rows[0]

    assert row["monthly_overheating_hours"] == pytest.approx(2.0)
    assert row["monthly_indoor_temperature_exceedance_degree_hours"] == pytest.approx(2.5)
    assert row["monthly_max_indoor_temperature_C"] == pytest.approx(28.0)
    assert row["monthly_excess_heat_kWh"] == pytest.approx(0.3)


def test_monthly_and_seasonal_demand_shift_comparisons_use_baseline_deltas() -> None:
    rows = []
    for scenario_id, window, pathway, tech, heat, cdd in [
        ("baseline_1981_2005__historical__tech_current_stock", "baseline_1981_2005", "historical", "tech_current_stock", 100.0, 1.0),
        ("near_future_2030_2049__rcp_8_5__tech_frozen_stock", "near_future_2030_2049", "rcp_8_5", "tech_frozen_stock", 70.0, 5.0),
    ]:
        for month in range(1, 13):
            rows.append(
                {
                    "scenario_leaf_id": f"{scenario_id}__seed_0000",
                    "scenario_id": scenario_id,
                    "climate_window_id": window,
                    "climate_pathway_id": pathway,
                    "technology_case_id": tech,
                    "realization_id": "seed_0000",
                    "year": 2030,
                    "month": month,
                    "month_hours": 720.0,
                    "n_timesteps": 30,
                    "monthly_electricity_gross_kWh": 10.0,
                    "monthly_grid_import_kWh": 8.0,
                    "monthly_grid_export_kWh": 0.0,
                    "monthly_gas_kWh": 20.0,
                    "monthly_space_heating_useful_kWh": heat if month in {1, 2, 12} else heat / 2.0,
                    "monthly_dhw_kWh": 5.0,
                    "monthly_ev_charging_kWh": 0.0,
                    "monthly_pv_generation_kWh": 0.0,
                    "monthly_pv_self_consumption_kWh": 0.0,
                    "monthly_mean_T_out_C": 10.0,
                    "monthly_HDD_15": 10.0,
                    "monthly_HDD_18": 20.0,
                    "monthly_CDD_22": cdd if month in {6, 7, 8} else 0.0,
                    "monthly_excess_heat_kWh": cdd,
                    "monthly_overheating_hours": cdd * 2.0,
                    "monthly_indoor_temperature_exceedance_degree_hours": cdd * 3.0,
                    "monthly_max_indoor_temperature_C": 26.0 + cdd,
                }
            )
    monthly_df = pd.DataFrame(rows)

    monthly = build_monthly_demand_shift_comparison(monthly_df)
    seasonal = build_seasonal_demand_shift_comparison(monthly_df)
    future_january = monthly[
        (monthly["scenario_id"].str.contains("near_future"))
        & (monthly["month"] == 1)
    ].iloc[0]
    future_winter = seasonal[
        (seasonal["scenario_id"].str.contains("near_future"))
        & (seasonal["season"] == "winter")
    ].iloc[0]
    future_shoulder = seasonal[
        (seasonal["scenario_id"].str.contains("near_future"))
        & (seasonal["season"] == "shoulder")
    ].iloc[0]

    assert len(monthly) == 24
    assert set(seasonal["season"]) == {"winter", "spring", "summer", "autumn", "shoulder", "annual"}
    assert future_january["monthly_total_final_energy_kWh_mean"] == pytest.approx(30.0)
    assert future_january["delta_monthly_space_heating_useful_kWh_abs"] == pytest.approx(-30.0)
    assert future_winter["seasonal_space_heating_useful_kWh_mean"] == pytest.approx(210.0)
    assert future_winter["delta_seasonal_space_heating_useful_kWh_abs"] == pytest.approx(-90.0)
    assert future_shoulder["seasonal_space_heating_useful_kWh_mean"] == pytest.approx(210.0)
    assert bool(future_winter["active_cooling_final_energy_kWh_included"]) is False


def test_required_output_metric_columns_exist(tmp_path: Path) -> None:
    result = compute_standardized_output_metrics(
        _raw_outputs(_profile()),
        _technology_config(tmp_path),
    )

    output_metric_columns = [column for column in REQUIRED_METRIC_COLUMNS if not column.startswith(("mean_", "winter_", "summer_", "HDD_", "CDD_"))]
    assert set(output_metric_columns).issubset(result.metrics)
    assert result.missing_metrics == []


def test_missing_raw_output_column_is_detected(tmp_path: Path) -> None:
    frame = _profile().drop(columns=["P_el_gross_actual_W"])

    with pytest.raises(MissingRequiredOutputError) as exc:
        compute_standardized_output_metrics(_raw_outputs(frame), _technology_config(tmp_path))

    assert "annual_electricity_gross_kWh" in exc.value.missing_metrics


def test_grid_peak_kw_is_converted_to_w(tmp_path: Path) -> None:
    frame = _profile(P_el_grid_import_kW=[1.0, 2.5, 0.4]).drop(columns=["P_el_grid_import_W"])

    result = compute_standardized_output_metrics(
        _raw_outputs(frame),
        _technology_config(tmp_path),
    )

    assert result.metrics["peak_grid_import_W"] == pytest.approx(2500.0)


def test_pv_and_ev_free_cases_get_zero_policy(tmp_path: Path) -> None:
    summary = {
        "annual_energy_by_carrier_kWh": {
            "electricity_gross_actual": 1.0,
            "electricity_grid_import": 1.0,
            "electricity_grid_export": 0.0,
            "natural_gas": 0.0,
            "pv_generation": 0.0,
            "ev_charging": 0.0,
        },
        "space_heating_thermal_kWh": 0.0,
        "dhw_thermal_kWh": 0.0,
        "annual_pv_generation_kWh": 0.0,
        "annual_ev_charging_kWh": 0.0,
    }

    result = compute_standardized_output_metrics(
        _raw_outputs(_profile(), summary),
        _technology_config(tmp_path, pv=False, ev=False),
    )

    assert result.metrics["pv_generation_kWh"] == 0.0
    assert result.metrics["ev_charging_kWh"] == 0.0
    assert result.policies["pv_metric_policy"] == "no_pv_case"
    assert result.policies["ev_metric_policy"] == "no_ev_case"
