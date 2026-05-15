"""Regression tests for scenario technology-case mapping into model outputs."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np


REPO_ROOT = Path(__file__).resolve().parents[2]
MODEL_V3_SRC = REPO_ROOT / "src"
if str(MODEL_V3_SRC) not in sys.path:
    sys.path.insert(0, str(MODEL_V3_SRC))

from model_v3.scenarios.model_runner_adapter import scenario_leaf_to_model_config  # noqa: E402
from model_v3.simulation.annual_runner import run_annual_simulation  # noqa: E402
from model_v3.stochastic.sampler import sample_household_parameters  # noqa: E402


def _write_daily_climate(path: Path, year: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    start = datetime(year, 1, 1, 12)
    rows = ["timestamp,T_out_C,I_solar_W_m2"]
    for offset in range(365):
        timestamp = start + timedelta(days=offset)
        rows.append(f"{timestamp.isoformat()},2.0,200.0")
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def _run_config(tmp_path: Path, *, leaf_id: str, technology_case_id: str, year: int) -> dict:
    climate_path = tmp_path / f"{technology_case_id}_{year}.csv"
    outputs_dir = tmp_path / "outputs" / leaf_id
    _write_daily_climate(climate_path, year)
    climate_window_id, climate_pathway_id, _, realization_id = leaf_id.split("__")
    return {
        "scenario_leaf": {
            "id": leaf_id,
            "scenario_id": "__".join((climate_window_id, climate_pathway_id, technology_case_id)),
            "climate_window_id": climate_window_id,
            "climate_pathway_id": climate_pathway_id,
            "technology_case_id": technology_case_id,
            "realization_id": realization_id,
        },
        "climate": {
            "forcing_file": str(climate_path),
            "analysis_start": f"{year}-01-01",
            "analysis_end": f"{year}-12-31",
        },
        "technology": {
            "case_id": technology_case_id,
            "metadata_file": str(REPO_ROOT / "config" / "scenario_tree" / "technology_cases.yaml"),
            "belgian_technology_inputs": str(REPO_ROOT / "config" / "belgian_technology_inputs.yaml"),
        },
        "stochastic": {
            "realization_id": realization_id,
            "seed_value": 0,
            "cohort_size": 1,
        },
        "output": {
            "outputs_dir": str(outputs_dir),
        },
    }


def test_current_stock_scenario_maps_to_gas_outputs(tmp_path: Path) -> None:
    run_config = _run_config(
        tmp_path,
        leaf_id="baseline_1981_2005__historical__tech_current_stock__seed_0000",
        technology_case_id="tech_current_stock",
        year=1981,
    )
    model_config = scenario_leaf_to_model_config(run_config)

    assert model_config["systems"]["heating"]["technology_type"] == "gas_boiler"
    assert model_config["systems"]["dhw"]["technology_type"] == "linked_to_space_heating"

    results = run_annual_simulation(config=model_config)

    assert results["annual_energy_by_carrier_kWh"]["natural_gas"] > 0.0
    assert results["profile_frame"]["P_gas_space_heating_W"].max() > 0.0
    assert results["profile_frame"]["P_gas_dhw_W"].max() > 0.0


def test_high_electrification_pv_ev_scenario_maps_to_sampling_probabilities(tmp_path: Path) -> None:
    run_config = _run_config(
        tmp_path,
        leaf_id="mid_century_2050_2070__rcp_8_5__tech_high_electrification_pv_ev__seed_0000",
        technology_case_id="tech_high_electrification_pv_ev",
        year=2050,
    )
    model_config = scenario_leaf_to_model_config(run_config)

    assert model_config["systems"]["heating"]["technology_type"] == "air_water"
    assert model_config["der"]["pv"]["enabled"] is False
    assert model_config["mobility"]["ev"]["enabled"] is False
    assert model_config["uncertainty"]["technology"]["pv_household_probability"] == 0.60
    assert model_config["uncertainty"]["technology"]["ev_household_probability"] == 0.45


def test_high_electrification_case_samples_household_technology_mix(tmp_path: Path) -> None:
    run_config = _run_config(
        tmp_path,
        leaf_id="mid_century_2050_2070__rcp_8_5__tech_high_electrification_pv_ev__seed_0000",
        technology_case_id="tech_high_electrification_pv_ev",
        year=2050,
    )
    model_config = scenario_leaf_to_model_config(run_config)
    rng = np.random.default_rng(0)
    samples = [sample_household_parameters(model_config, rng) for _ in range(100)]

    heating_types = {sample["technology"]["technology_type"] for sample in samples}
    dhw_types = {sample["technology"]["dhw_technology_type"] for sample in samples}

    assert "air_water" in heating_types
    assert "hybrid_hp_gas" in heating_types
    assert "hpwh" in dhw_types
    assert sum(bool(sample["technology"]["has_pv"]) for sample in samples) > 40
    assert sum(bool(sample["technology"]["has_ev"]) for sample in samples) > 25


def test_current_stock_case_samples_belgian_stock_mapping(tmp_path: Path) -> None:
    run_config = _run_config(
        tmp_path,
        leaf_id="baseline_1981_2005__historical__tech_current_stock__seed_0000",
        technology_case_id="tech_current_stock",
        year=1981,
    )
    model_config = scenario_leaf_to_model_config(run_config)
    rng = np.random.default_rng(1)
    samples = [sample_household_parameters(model_config, rng) for _ in range(100)]

    heating_types = {sample["technology"]["technology_type"] for sample in samples}
    sources = {sample["technology"]["technology_probability_source"] for sample in samples}

    assert "gas_boiler" in heating_types
    assert "oil_boiler" in heating_types
    assert sources == {"belgian_carrier_stock_mapping"}
    assert all("dhw_technology_type" in sample["technology"] for sample in samples)
