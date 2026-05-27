from __future__ import annotations

from pathlib import Path
import sys

import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MODEL_V3_SRC = REPO_ROOT / "src"
if str(MODEL_V3_SRC) not in sys.path:
    sys.path.insert(0, str(MODEL_V3_SRC))

from model_v3.scenarios.output6_technology_investment import (  # noqa: E402
    annuity_factor,
    build_output6_tables,
    generate_output6_figures,
    npv_savings,
    simple_payback_years,
    validate_output6_results,
)
from model_v3.scenarios.registry import write_registry  # noqa: E402


def _write_profile(path: Path, *, pv_w: float, export_w: float, gross_w: float, heating_w: float) -> None:
    path.mkdir(parents=True, exist_ok=True)
    timestamps = pd.date_range("2050-01-01", periods=24, freq="h", tz="Europe/Brussels")
    pd.DataFrame(
        {
            "timestamp": timestamps.astype(str),
            "P_pv_generation_W": pv_w,
            "P_el_grid_export_W": export_w,
            "P_el_gross_actual_W": gross_w,
            "Q_heating_supplied_W": heating_w,
        }
    ).to_csv(path / "annual_profile.csv", index=False)


def _leaf(
    root: Path,
    *,
    scenario_id: str,
    climate_window_id: str,
    climate_pathway_id: str,
    technology_case_id: str,
    pv_w: float,
    export_w: float,
    gross_w: float,
    heating_w: float,
) -> dict[str, str | int]:
    leaf_id = f"{scenario_id}__cold_design_year__seed_0000"
    outputs_dir = root / "runs" / leaf_id / "outputs"
    _write_profile(outputs_dir, pv_w=pv_w, export_w=export_w, gross_w=gross_w, heating_w=heating_w)
    run_config = root / "runs" / leaf_id / "run_config.yaml"
    run_config.write_text("stochastic:\n  cohort_size: 2\nmodel_options:\n  runner_mode: stochastic_cohort\n", encoding="utf-8")
    return {
        "scenario_leaf_id": leaf_id,
        "scenario_id": scenario_id,
        "climate_window_id": climate_window_id,
        "climate_pathway_id": climate_pathway_id,
        "technology_case_id": technology_case_id,
        "design_year_id": "cold_design_year",
        "design_year": 2050,
        "realization_id": "seed_0000",
        "outputs_dir": str(outputs_dir),
        "run_config_path": str(run_config),
    }


def _write_fixture(root: Path) -> tuple[Path, Path, Path]:
    leaves = [
        _leaf(
            root,
            scenario_id="baseline_1981_2005__historical__tech_current_stock",
            climate_window_id="baseline_1981_2005",
            climate_pathway_id="historical",
            technology_case_id="tech_current_stock",
            pv_w=0.0,
            export_w=0.0,
            gross_w=2000.0,
            heating_w=1800.0,
        ),
        _leaf(
            root,
            scenario_id="long_term_2080_2100__rcp_8_5__tech_frozen_stock",
            climate_window_id="long_term_2080_2100",
            climate_pathway_id="rcp_8_5",
            technology_case_id="tech_frozen_stock",
            pv_w=0.0,
            export_w=0.0,
            gross_w=1900.0,
            heating_w=1400.0,
        ),
        _leaf(
            root,
            scenario_id="mid_century_2050_2070__rcp_4_5__tech_moderate_electrification",
            climate_window_id="mid_century_2050_2070",
            climate_pathway_id="rcp_4_5",
            technology_case_id="tech_moderate_electrification",
            pv_w=200.0,
            export_w=50.0,
            gross_w=2400.0,
            heating_w=1300.0,
        ),
        _leaf(
            root,
            scenario_id="mid_century_2050_2070__rcp_4_5__tech_high_electrification_pv_ev",
            climate_window_id="mid_century_2050_2070",
            climate_pathway_id="rcp_4_5",
            technology_case_id="tech_high_electrification_pv_ev",
            pv_w=1000.0,
            export_w=200.0,
            gross_w=3000.0,
            heating_w=1200.0,
        ),
    ]
    manifest = root / "manifests"
    manifest.mkdir(parents=True)
    leaf_index = manifest / "output34_leaf_index.csv"
    pd.DataFrame(leaves).to_csv(leaf_index, index=False)
    registry = manifest / "run_registry.csv"
    write_registry(
        registry,
        [
            {
                "run_attempt_id": f"attempt_{idx}",
                "scenario_leaf_id": row["scenario_leaf_id"],
                "status": "success",
                "timestamp_start_utc": f"2050-01-0{idx + 1}T00:00:00Z",
            }
            for idx, row in enumerate(leaves)
        ],
    )

    comparison = root / "summaries" / "comparison_level"
    realization = root / "summaries" / "realization_level"
    comparison.mkdir(parents=True)
    realization.mkdir(parents=True)

    bill_rows = []
    leaf_bill_rows = []
    bills = {
        "baseline_1981_2005__historical__tech_current_stock": 1000.0,
        "long_term_2080_2100__rcp_8_5__tech_frozen_stock": 900.0,
        "mid_century_2050_2070__rcp_4_5__tech_moderate_electrification": 780.0,
        "mid_century_2050_2070__rcp_4_5__tech_high_electrification_pv_ev": 700.0,
    }
    for leaf in leaves:
        scenario_id = str(leaf["scenario_id"])
        bill_rows.append(
            {
                "scenario_id": scenario_id,
                "climate_window_id": leaf["climate_window_id"],
                "climate_pathway_id": leaf["climate_pathway_id"],
                "technology_case_id": leaf["technology_case_id"],
                "design_year_id": leaf["design_year_id"],
                "design_year": leaf["design_year"],
                "tariff_scenario_id": "static_test",
                "tariff_scenario_label": "Static test",
                "n_successful_runs": 1,
                "n_households": 2,
                "annual_bill_per_household_EUR_mean": bills[scenario_id],
                "baseline_annual_bill_per_household_EUR_mean": 1000.0,
                "annual_grid_export_credit_EUR_mean": 20.0 if "pv_ev" in scenario_id else 0.0,
            }
        )
        leaf_bill_rows.append(
            {
                "scenario_leaf_id": leaf["scenario_leaf_id"],
                "scenario_id": scenario_id,
                "tariff_scenario_id": "static_test",
                "n_households": 2,
            }
        )
    pd.DataFrame(bill_rows).to_csv(comparison / "annual_energy_bill_comparison.csv", index=False)
    pd.DataFrame(leaf_bill_rows).to_csv(realization / "output5_leaf_annual_energy_bills.csv", index=False)
    pd.DataFrame(
        [
            {
                "tariff_scenario_id": "static_test",
                "tariff_scenario_label": "Static test",
                "electricity_import_eur_per_kwh": 0.30,
                "pv_export_eur_per_kwh": 0.05,
                "source_references": "test",
            }
        ]
    ).to_csv(comparison / "tariff_assumptions.csv", index=False)
    cooling = tmp_path_cooling = root / "cooling.csv"
    pd.DataFrame(
        [
            {
                "scenario_id": "baseline_1981_2005__historical__tech_current_stock",
                "climate_window_id": "baseline_1981_2005",
                "climate_pathway_id": "historical",
                "CDD_22_mean": 10.0,
                "overheating_hours_mean": 20.0,
                "indoor_temperature_exceedance_degree_hours_mean": 100.0,
                "interpretation_note": "cooling exposure only",
            },
            {
                "scenario_id": "mid_century_2050_2070__rcp_4_5__tech_frozen_stock",
                "climate_window_id": "mid_century_2050_2070",
                "climate_pathway_id": "rcp_4_5",
                "CDD_22_mean": 30.0,
                "overheating_hours_mean": 50.0,
                "indoor_temperature_exceedance_degree_hours_mean": 200.0,
                "interpretation_note": "cooling exposure only",
            },
            {
                "scenario_id": "long_term_2080_2100__rcp_8_5__tech_frozen_stock",
                "climate_window_id": "long_term_2080_2100",
                "climate_pathway_id": "rcp_8_5",
                "CDD_22_mean": 60.0,
                "overheating_hours_mean": 90.0,
                "indoor_temperature_exceedance_degree_hours_mean": 400.0,
                "interpretation_note": "cooling exposure only",
            },
        ]
    ).to_csv(cooling, index=False)
    return leaf_index, registry, tmp_path_cooling


def test_output6_financial_formula_helpers() -> None:
    assert annuity_factor(0.0, 10) == pytest.approx(0.1)
    assert simple_payback_years(1000.0, 250.0) == pytest.approx(4.0)
    assert math_is_nan(simple_payback_years(1000.0, -1.0))
    assert npv_savings(1000.0, 250.0, 0.0, 5) == pytest.approx(250.0)


def math_is_nan(value: float) -> bool:
    return value != value


def test_output6_builds_tables_figures_and_validates_scope(tmp_path: Path) -> None:
    root = tmp_path / "experiment"
    leaf_index, registry, cooling = _write_fixture(root)

    result = build_output6_tables(
        experiment_root=root,
        leaf_index=leaf_index,
        run_registry=registry,
        cooling_comparison=cooling,
    )
    validation = validate_output6_results(experiment_root=root, expected_option_count=6)
    figures = generate_output6_figures(
        experiment_root=root,
        figures_root=tmp_path / "figures",
        formats=["png"],
        reference_tariff_id="static_test",
    )

    investment = pd.read_csv(result["technology_investment_adaptation_comparison_path"])
    pv = pd.read_csv(result["pv_self_consumption_value_comparison_path"])
    assumptions = pd.read_csv(result["technology_investment_assumptions_path"])
    reversible = investment[investment["technology_option_id"] == "reversible_heat_pump_adaptation"].iloc[0]
    high_pv = pv[pv["technology_case_id"] == "tech_high_electrification_pv_ev"].iloc[0]
    hp_assumption = assumptions[assumptions["technology_option_id"] == "air_water_heat_pump_heating"].iloc[0]

    assert validation["active_cooling_final_energy_columns_present"] is False
    assert figures["figure_count"] >= 5
    assert result["investment_rows"] == 6
    assert high_pv["pv_self_consumption_ratio_mean"] == pytest.approx(0.8)
    assert high_pv["pv_self_sufficiency_ratio_mean"] == pytest.approx(800.0 / 3000.0)
    assert bool(reversible["active_cooling_final_energy_kWh_included"]) is False
    assert "annual_operational_emissions_reduction_kgCO2_per_household" in investment.columns
    assert reversible["covered_overheating_hours_proxy_per_scenario_household"] > 0.0
    assert bool(hp_assumption["core_subsidy_included"]) is False
    assert hp_assumption["capex_net_eur_per_scenario_household_optional_subsidy"] < hp_assumption["capex_net_eur_per_scenario_household_core"]
