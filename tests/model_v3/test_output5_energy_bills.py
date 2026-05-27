from __future__ import annotations

from pathlib import Path
import sys

import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MODEL_V3_SRC = REPO_ROOT / "src"
if str(MODEL_V3_SRC) not in sys.path:
    sys.path.insert(0, str(MODEL_V3_SRC))

from model_v3.scenarios.output5_energy_bills import (  # noqa: E402
    build_output5_tables,
    generate_output5_figures,
    validate_output5_results,
)
from model_v3.scenarios.registry import write_registry  # noqa: E402


def _write_leaf(
    root: Path,
    *,
    leaf_id: str,
    scenario_id: str,
    climate_window_id: str,
    climate_pathway_id: str,
    technology_case_id: str,
    import_w: float,
) -> dict[str, str]:
    run_dir = root / "runs" / leaf_id
    outputs = run_dir / "outputs"
    outputs.mkdir(parents=True)
    timestamps = pd.date_range("2050-01-01", periods=8760, freq="h", tz="Europe/Brussels")
    profile = pd.DataFrame(
        {
            "timestamp": timestamps.astype(str),
            "P_el_grid_import_W": import_w,
            "P_el_grid_export_W": 100.0,
            "P_gas_total_W": 1000.0,
            "P_oil_total_W": 200.0,
        }
    )
    profile.to_csv(outputs / "annual_profile.csv", index=False)
    matrix = pd.DataFrame(
        {
            "timestamp": timestamps.astype(str),
            "household_000": 1000.0,
            "household_001": max(import_w - 1000.0, 0.0),
        }
    )
    matrix.to_csv(outputs / "household_grid_import_matrix.csv", index=False)
    household = pd.DataFrame(
        [
            {
                "household_id": "household_000",
                "annual_energy_by_carrier_kWh": {
                    "electricity_grid_import": 8760.0,
                    "electricity_grid_export": 438.0,
                    "natural_gas": 4380.0,
                },
            },
            {
                "household_id": "household_001",
                "annual_energy_by_carrier_kWh": {
                    "electricity_grid_import": max(import_w - 1000.0, 0.0) * 8760.0 / 1000.0,
                    "electricity_grid_export": 438.0,
                    "natural_gas": 4380.0,
                },
            },
        ]
    )
    household.to_csv(outputs / "household_annual_energy.csv", index=False)
    run_config = run_dir / "run_config.yaml"
    run_config.write_text("stochastic:\n  cohort_size: 2\n", encoding="utf-8")
    return {
        "scenario_leaf_id": leaf_id,
        "scenario_id": scenario_id,
        "climate_window_id": climate_window_id,
        "climate_pathway_id": climate_pathway_id,
        "technology_case_id": technology_case_id,
        "design_year_id": "cold_design_year",
        "design_year": "2050",
        "realization_id": "seed_0000",
        "outputs_dir": str(outputs),
        "run_config_path": str(run_config),
    }


def _write_tariffs(path: Path) -> None:
    path.write_text(
        """
metadata:
  price_basis: illustrative_scenario_assumptions
  interpretation_note: scenario test
  source_notes:
    - name: test source
      url: https://example.test
defaults:
  fixed_annual_eur_per_household: 120.0
  capacity_eur_per_kw_year: 12.0
  monthly_peak_floor_kw: 1.0
  timezone: Europe/Brussels
tariff_scenarios:
  - tariff_scenario_id: static_test
    label: Static test
    purpose: formula test
    electricity_import_eur_per_kwh: 0.30
    gas_eur_per_kwh: 0.10
    pv_export_eur_per_kwh: 0.05
    dynamic: false
""",
        encoding="utf-8",
    )


def test_output5_builds_static_bill_tables_and_deltas(tmp_path: Path) -> None:
    root = tmp_path / "experiment"
    leaf_rows = [
        _write_leaf(
            root,
            leaf_id="baseline_1981_2005__historical__tech_current_stock__cold_design_year__seed_0000",
            scenario_id="baseline_1981_2005__historical__tech_current_stock",
            climate_window_id="baseline_1981_2005",
            climate_pathway_id="historical",
            technology_case_id="tech_current_stock",
            import_w=3000.0,
        ),
        _write_leaf(
            root,
            leaf_id="long_term_2080_2100__rcp_8_5__tech_frozen_stock__cold_design_year__seed_0000",
            scenario_id="long_term_2080_2100__rcp_8_5__tech_frozen_stock",
            climate_window_id="long_term_2080_2100",
            climate_pathway_id="rcp_8_5",
            technology_case_id="tech_frozen_stock",
            import_w=4000.0,
        ),
    ]
    manifest = root / "manifests"
    manifest.mkdir(parents=True)
    leaf_index = manifest / "output34_leaf_index.csv"
    pd.DataFrame(leaf_rows).to_csv(leaf_index, index=False)
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
            for idx, row in enumerate(leaf_rows)
        ],
    )
    tariff_config = tmp_path / "tariffs.yaml"
    _write_tariffs(tariff_config)

    result = build_output5_tables(
        experiment_root=root,
        leaf_index=leaf_index,
        run_registry=registry,
        tariff_config=tariff_config,
    )

    annual = pd.read_csv(result["annual_energy_bill_comparison_path"])
    monthly = pd.read_csv(result["monthly_energy_bill_comparison_path"])
    tariffs = pd.read_csv(result["tariff_assumptions_path"])
    baseline = annual[annual["scenario_id"] == "baseline_1981_2005__historical__tech_current_stock"].iloc[0]
    future = annual[annual["scenario_id"] != "baseline_1981_2005__historical__tech_current_stock"].iloc[0]
    monthly_baseline = monthly[monthly["scenario_id"] == "baseline_1981_2005__historical__tech_current_stock"]

    assert baseline["annual_bill_per_household_EUR_mean"] == pytest.approx((26280 * 0.30 + 8760 * 0.10 - 876 * 0.05 + 240 + 36) / 2)
    assert monthly_baseline["monthly_bill_per_household_EUR_mean"].sum() == pytest.approx(
        baseline["annual_bill_per_household_EUR_mean"]
    )
    assert future["delta_annual_bill_per_household_EUR_abs"] > 0.0
    assert baseline["annual_unpriced_non_gas_fuel_kWh_mean"] == pytest.approx(1752.0)
    assert baseline["annual_operational_emissions_kgCO2_per_household_mean"] == pytest.approx(
        (26280 * 0.150 + 8760 * 0.202) / 2
    )
    assert monthly["monthly_bill_per_household_EUR_mean"].notna().all()
    assert set(tariffs["tariff_scenario_id"]) == {"static_test"}
    assert "source_references" in tariffs.columns


def test_output5_validation_and_figures(tmp_path: Path) -> None:
    root = tmp_path / "experiment"
    leaf_rows = [
        _write_leaf(
            root,
            leaf_id="baseline_1981_2005__historical__tech_current_stock__cold_design_year__seed_0000",
            scenario_id="baseline_1981_2005__historical__tech_current_stock",
            climate_window_id="baseline_1981_2005",
            climate_pathway_id="historical",
            technology_case_id="tech_current_stock",
            import_w=3000.0,
        ),
        _write_leaf(
            root,
            leaf_id="long_term_2080_2100__rcp_8_5__tech_frozen_stock__cold_design_year__seed_0000",
            scenario_id="long_term_2080_2100__rcp_8_5__tech_frozen_stock",
            climate_window_id="long_term_2080_2100",
            climate_pathway_id="rcp_8_5",
            technology_case_id="tech_frozen_stock",
            import_w=4000.0,
        ),
    ]
    manifest = root / "manifests"
    manifest.mkdir(parents=True)
    leaf_index = manifest / "output34_leaf_index.csv"
    pd.DataFrame(leaf_rows).to_csv(leaf_index, index=False)
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
            for idx, row in enumerate(leaf_rows)
        ],
    )
    tariff_config = tmp_path / "tariffs.yaml"
    _write_tariffs(tariff_config)
    build_output5_tables(
        experiment_root=root,
        leaf_index=leaf_index,
        run_registry=registry,
        tariff_config=tariff_config,
    )

    validation = validate_output5_results(experiment_root=root, expected_tariff_count=1)
    figures = generate_output5_figures(
        experiment_root=root,
        figures_root=tmp_path / "figures",
        formats=["png"],
        reference_tariff_id="static_test",
    )

    assert validation["annual_rows"] == 2
    assert validation["active_cooling_final_energy_columns_present"] is False
    assert figures["figure_count"] >= 2
