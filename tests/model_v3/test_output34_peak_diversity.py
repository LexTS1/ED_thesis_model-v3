from __future__ import annotations

from pathlib import Path
import sys

import numpy as np
import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MODEL_V3_SRC = REPO_ROOT / "src"
if str(MODEL_V3_SRC) not in sys.path:
    sys.path.insert(0, str(MODEL_V3_SRC))

from model_v3.scenarios.output34_peak_diversity import (
    compute_peak_grid_metrics,
    diversity_by_household_count,
    diversity_factor_from_matrix,
    generate_output34_pilot_configs,
    load_duration_samples,
    select_design_years_from_climate,
    top_fraction_metrics,
    validate_output34_results,
    weighted_percentile,
)
from model_v3.scenarios.registry import write_registry


def test_peak_grid_metrics_are_duration_aware_for_hourly_profile() -> None:
    frame = pd.DataFrame(
        {
            "timestamp": pd.date_range("2050-01-01", periods=100, freq="h"),
            "P_el_grid_import_W": list(range(100)),
            "P_el_gross_actual_W": list(range(100)),
            "P_gas_total_W": [10.0] * 100,
            "Q_heating_supplied_W": [20.0] * 99 + [200.0],
        }
    )

    metrics = compute_peak_grid_metrics(frame, stress_threshold_W=95.0)

    assert metrics["peak_grid_import_W"] == pytest.approx(99.0)
    assert metrics["p95_grid_import_W"] == pytest.approx(94.0)
    assert metrics["p99_grid_import_W"] == pytest.approx(98.0)
    assert metrics["top_1pct_load_hours"] == pytest.approx(1.0)
    assert metrics["top_1pct_grid_import_W_mean"] == pytest.approx(99.0)
    assert metrics["hours_above_grid_stress_threshold"] == pytest.approx(4.0)
    assert metrics["peak_useful_heating_W"] == pytest.approx(200.0)
    assert metrics["peak_total_final_energy_W"] == pytest.approx(109.0)


def test_top_fraction_uses_actual_step_duration() -> None:
    values = [10.0, 50.0, 100.0, 20.0]
    durations = [1800.0, 1800.0, 7200.0, 1800.0]

    result = top_fraction_metrics(values, durations, fraction=0.2)

    assert result["top_fraction_hours"] == pytest.approx(2.0)
    assert result["top_fraction_mean_W"] == pytest.approx(100.0)


def test_weighted_percentile_and_load_duration_samples() -> None:
    values = [0.0, 10.0, 20.0, 30.0]
    weights = [1.0, 1.0, 1.0, 7.0]

    assert weighted_percentile(values, weights, 0.50) == pytest.approx(30.0)

    samples = pd.DataFrame(load_duration_samples(values, exceedance_pcts=[0.0, 50.0, 100.0]))
    assert samples.loc[samples["exceedance_pct"] == 0.0, "grid_import_W"].iloc[0] == pytest.approx(30.0)
    assert samples.loc[samples["exceedance_pct"] == 100.0, "grid_import_W"].iloc[0] == pytest.approx(0.0)


def test_diversity_factor_distinguishes_coincident_and_staggered_peaks() -> None:
    coincident = np.asarray([[10.0, 0.0], [10.0, 0.0]])
    staggered = np.asarray([[10.0, 0.0], [0.0, 10.0]])

    assert diversity_factor_from_matrix(coincident) == pytest.approx(1.0)
    assert diversity_factor_from_matrix(staggered) == pytest.approx(2.0)


def test_diversity_by_household_count_is_deterministic() -> None:
    matrix = np.asarray(
        [
            [10.0, 0.0, 0.0],
            [0.0, 10.0, 0.0],
            [0.0, 0.0, 10.0],
        ]
    )

    first = diversity_by_household_count(matrix, counts=[1, 2, 3], iterations=5, random_seed=7)
    second = diversity_by_household_count(matrix, counts=[1, 2, 3], iterations=5, random_seed=7)

    assert first == second
    assert first[-1]["n_households"] == 3
    assert first[-1]["diversity_factor_p50"] == pytest.approx(3.0)


def test_diversity_by_household_count_includes_100_when_available() -> None:
    matrix = np.eye(100)

    rows = diversity_by_household_count(matrix, counts=[1, 5, 30, 50, 100, 150], iterations=3, random_seed=3)

    assert [row["n_households"] for row in rows] == [1, 5, 30, 50, 100]


def test_design_year_selection_uses_cold_and_median_hdd(tmp_path: Path) -> None:
    rows = []
    for year, temperatures in {
        2030: [0.0] * 3,
        2031: [10.0] * 3,
        2032: [15.0] * 3,
    }.items():
        for day, temperature in enumerate(temperatures, start=1):
            rows.append(
                {
                    "timestamp": f"{year}-01-{day:02d}T12:00:00",
                    "T_out_C": temperature,
                    "I_solar_W_m2": 100.0,
                }
            )
    path = tmp_path / "climate.csv"
    pd.DataFrame(rows).to_csv(path, index=False)

    selected = select_design_years_from_climate(
        path,
        analysis_start="2030-01-01",
        analysis_end="2032-12-31",
    )

    assert selected["cold_design_year"]["year"] == 2030
    assert selected["typical_heating_year"]["year"] == 2031


def test_output34_config_generation_accepts_scenario_and_design_year_subset(tmp_path: Path) -> None:
    result = generate_output34_pilot_configs(
        experiment_root=tmp_path / "output34_subset",
        scenario_ids=["baseline_1981_2005__historical__tech_current_stock"],
        realization_ids=["seed_0000", "seed_0001"],
        design_year_ids=["cold_design_year"],
        cohort_size=100,
        target_resolution_seconds=3600,
    )

    index = pd.read_csv(result["leaf_index_path"])
    assert len(index) == 2
    assert set(index["scenario_id"]) == {"baseline_1981_2005__historical__tech_current_stock"}
    assert set(index["design_year_id"]) == {"cold_design_year"}
    assert set(index["realization_id"]) == {"seed_0000", "seed_0001"}

    import yaml

    run_config = yaml.safe_load(Path(index["run_config_path"].iloc[0]).read_text(encoding="utf-8"))
    assert run_config["stochastic"]["cohort_size"] == 100
    assert run_config["model_options"]["target_resolution_seconds"] == 3600


def test_output34_flexible_validation_checks_sensitivity_subset(tmp_path: Path) -> None:
    experiment_root = tmp_path / "experiment"
    comparison_dir = experiment_root / "summaries" / "comparison_level"
    comparison_dir.mkdir(parents=True)
    run_config_path = tmp_path / "run_config.yaml"
    run_config_path.write_text(
        "stochastic:\n  cohort_size: 100\nmodel_options:\n  runner_mode: stochastic_cohort\n  target_resolution_seconds: 3600\n",
        encoding="utf-8",
    )
    leaf_index_path = experiment_root / "manifests" / "output34_leaf_index.csv"
    leaf_index_path.parent.mkdir(parents=True)
    pd.DataFrame(
        [
            {
                "scenario_leaf_id": "leaf_1",
                "scenario_id": "baseline_1981_2005__historical__tech_current_stock",
                "climate_window_id": "baseline_1981_2005",
                "climate_pathway_id": "historical",
                "technology_case_id": "tech_current_stock",
                "design_year_id": "cold_design_year",
                "design_year": 1982,
                "realization_id": "seed_0000",
                "run_config_path": str(run_config_path),
            }
        ]
    ).to_csv(leaf_index_path, index=False)
    summary = pd.DataFrame(
        [
            {
                "scenario_id": "baseline_1981_2005__historical__tech_current_stock",
                "design_year_id": "cold_design_year",
            }
        ]
    )
    summary.to_csv(comparison_dir / "peak_grid_stress_comparison.csv", index=False)
    summary.to_csv(comparison_dir / "demand_distribution_uncertainty_comparison.csv", index=False)
    summary.to_csv(comparison_dir / "diversity_factor_comparison.csv", index=False)
    registry_path = experiment_root / "manifests" / "run_registry.csv"
    write_registry(
        registry_path,
        [
            {
                "run_attempt_id": "attempt_1",
                "scenario_leaf_id": "leaf_1",
                "status": "success",
                "timestamp_start_utc": "2026-01-01T00:00:00Z",
            }
        ],
    )

    result = validate_output34_results(
        experiment_root=experiment_root,
        leaf_index=leaf_index_path,
        run_registry=registry_path,
        expected_leaf_count=1,
        expected_peak_rows=1,
        expected_design_year_ids=["cold_design_year"],
        expected_realization_ids=["seed_0000"],
        expected_cohort_size=100,
        expected_target_resolution_seconds=3600,
        require_success=True,
    )

    assert result["leaf_index_rows"] == 1
    assert result["required_success"] is True
