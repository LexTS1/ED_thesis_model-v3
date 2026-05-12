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
from model_v3.scenarios.summarize_outputs import write_per_leaf_summary
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
