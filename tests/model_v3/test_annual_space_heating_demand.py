from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from model_v3.scenarios.summarize_outputs import build_annual_space_heating_demand_comparison

from tests.model_v3.comparison_test_utils import BASELINE_SCENARIO, FROZEN_SCENARIO


def _write_annual_profile(path: Path, annual_values_kwh: dict[int, float]) -> None:
    rows = []
    for year, demand_kwh in annual_values_kwh.items():
        timestamps = pd.date_range(f"{year}-01-01T00:00:00Z", periods=2, freq="1h")
        power_w = demand_kwh * 1000.0 / 2.0
        rows.extend({"timestamp": timestamp.isoformat(), "Q_heating_supplied_W": power_w} for timestamp in timestamps)
    path.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path / "annual_profile.csv", index=False)


def _space_heating_leaf(
    tmp_path: Path,
    scenario_id: str,
    realization_id: str,
    annual_values_kwh: dict[int, float],
    *,
    git_commit: str = "",
    git_is_dirty: str = "",
    belgian_technology_inputs_hash_sha256: str = "",
) -> dict:
    window, pathway, technology = scenario_id.split("__")
    raw_outputs_dir = tmp_path / f"{scenario_id}__{realization_id}" / "outputs"
    _write_annual_profile(raw_outputs_dir, annual_values_kwh)
    return {
        "scenario_leaf_id": f"{scenario_id}__{realization_id}",
        "scenario_id": scenario_id,
        "climate_window_id": window,
        "climate_pathway_id": pathway,
        "technology_case_id": technology,
        "realization_id": realization_id,
        "raw_outputs_dir": str(raw_outputs_dir),
        "git_commit": git_commit,
        "git_is_dirty": git_is_dirty,
        "belgian_technology_inputs_hash_sha256": belgian_technology_inputs_hash_sha256,
    }


def test_annual_space_heating_comparison_uses_all_baseline_years(tmp_path: Path) -> None:
    metrics = pd.DataFrame(
        [
            _space_heating_leaf(tmp_path, BASELINE_SCENARIO, "seed_0000", {1981: 1000.0, 1982: 1200.0}),
            _space_heating_leaf(tmp_path, FROZEN_SCENARIO, "seed_0000", {2030: 800.0, 2031: 900.0}),
        ]
    )

    comparison = build_annual_space_heating_demand_comparison(metrics)

    assert len(comparison) == 2
    baseline = comparison[comparison["scenario_id"] == BASELINE_SCENARIO].iloc[0]
    future = comparison[comparison["scenario_id"] == FROZEN_SCENARIO].iloc[0]
    assert baseline["n_annual_samples"] == 2
    assert future["n_annual_samples"] == 2
    assert future["paired_realization_ids"] == "seed_0000"
    assert future["baseline_annual_useful_heating_kWh_mean"] == pytest.approx(1100.0)
    assert future["annual_useful_heating_kWh_mean"] == pytest.approx(850.0)
    assert future["delta_annual_useful_heating_kWh_mean"] == pytest.approx(-250.0)
    assert future["delta_annual_useful_heating_kWh_pct"] == pytest.approx(-22.7272727273)


def test_annual_space_heating_comparison_excludes_unmatched_baseline_seeds(tmp_path: Path) -> None:
    metrics = pd.DataFrame(
        [
            _space_heating_leaf(tmp_path, BASELINE_SCENARIO, "seed_0000", {1981: 1000.0}),
            _space_heating_leaf(tmp_path, BASELINE_SCENARIO, "seed_0001", {1981: 1000.0}),
            _space_heating_leaf(tmp_path, BASELINE_SCENARIO, "seed_0002", {1981: 1000.0}),
            _space_heating_leaf(tmp_path, BASELINE_SCENARIO, "seed_0003", {1981: 100.0}),
            _space_heating_leaf(tmp_path, FROZEN_SCENARIO, "seed_0000", {2030: 800.0}),
            _space_heating_leaf(tmp_path, FROZEN_SCENARIO, "seed_0001", {2030: 900.0}),
            _space_heating_leaf(tmp_path, FROZEN_SCENARIO, "seed_0002", {2030: 700.0}),
        ]
    )

    comparison = build_annual_space_heating_demand_comparison(metrics)

    baseline = comparison[comparison["scenario_id"] == BASELINE_SCENARIO].iloc[0]
    future = comparison[comparison["scenario_id"] == FROZEN_SCENARIO].iloc[0]
    assert baseline["annual_useful_heating_kWh_mean"] == pytest.approx(1000.0)
    assert "seed_0003" in baseline["excluded_baseline_realization_ids"]
    assert future["paired_realization_ids"] == "seed_0000;seed_0001;seed_0002"
    assert future["baseline_annual_useful_heating_kWh_mean"] == pytest.approx(1000.0)
    assert future["annual_useful_heating_kWh_mean"] == pytest.approx(800.0)
    assert future["delta_annual_useful_heating_kWh_pct"] == pytest.approx(-20.0)


def test_annual_space_heating_comparison_excludes_mixed_provenance_baseline_seed(tmp_path: Path) -> None:
    metrics = pd.DataFrame(
        [
            _space_heating_leaf(
                tmp_path,
                BASELINE_SCENARIO,
                "seed_0000",
                {1981: 1000.0},
                git_commit="clean",
                git_is_dirty="false",
                belgian_technology_inputs_hash_sha256="tech-a",
            ),
            _space_heating_leaf(
                tmp_path,
                BASELINE_SCENARIO,
                "seed_0001",
                {1981: 200.0},
                git_commit="dirty-old",
                git_is_dirty="true",
                belgian_technology_inputs_hash_sha256="tech-old",
            ),
            _space_heating_leaf(
                tmp_path,
                FROZEN_SCENARIO,
                "seed_0000",
                {2030: 800.0},
                git_commit="clean",
                git_is_dirty="false",
                belgian_technology_inputs_hash_sha256="tech-a",
            ),
            _space_heating_leaf(
                tmp_path,
                FROZEN_SCENARIO,
                "seed_0001",
                {2030: 700.0},
                git_commit="clean",
                git_is_dirty="false",
                belgian_technology_inputs_hash_sha256="tech-a",
            ),
        ]
    )

    comparison = build_annual_space_heating_demand_comparison(metrics)

    future = comparison[comparison["scenario_id"] == FROZEN_SCENARIO].iloc[0]
    assert future["comparison_valid"] == False
    assert future["paired_realization_ids"] == "seed_0000"
    assert future["excluded_future_realization_ids"] == "seed_0001"
    assert "Provenance mismatch" in future["comparison_warning"]
    assert future["baseline_annual_useful_heating_kWh_mean"] == pytest.approx(1000.0)
    assert future["annual_useful_heating_kWh_mean"] == pytest.approx(800.0)


def test_annual_space_heating_comparison_marks_no_common_realization_invalid(tmp_path: Path) -> None:
    metrics = pd.DataFrame(
        [
            _space_heating_leaf(tmp_path, BASELINE_SCENARIO, "seed_0000", {1981: 1000.0}),
            _space_heating_leaf(tmp_path, FROZEN_SCENARIO, "seed_0001", {2030: 800.0}),
        ]
    )

    comparison = build_annual_space_heating_demand_comparison(metrics)

    future = comparison[comparison["scenario_id"] == FROZEN_SCENARIO].iloc[0]
    assert future["comparison_valid"] == False
    assert future["paired_realization_ids"] == ""
    assert future["n_missing_realizations"] == 1
    assert pd.isna(future["delta_annual_useful_heating_kWh_pct"])
