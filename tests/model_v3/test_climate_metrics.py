from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from model_v3.scenarios.climate_metrics import ClimateMetricsError, compute_climate_metrics


def _write_climate(path: Path, rows: list[dict]) -> Path:
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def test_hdd_cdd_and_seasonal_means_are_computed(tmp_path: Path) -> None:
    path = _write_climate(
        tmp_path / "climate.csv",
        [
            {"timestamp": "2030-01-01T12:00:00", "T_out_C": 10.0, "I_solar_W_m2": 100.0},
            {"timestamp": "2030-01-02T12:00:00", "T_out_C": 20.0, "I_solar_W_m2": 200.0},
            {"timestamp": "2030-06-01T12:00:00", "T_out_C": 25.0, "I_solar_W_m2": 300.0},
            {"timestamp": "2050-01-01T12:00:00", "T_out_C": -10.0, "I_solar_W_m2": 999.0},
        ],
    )

    result = compute_climate_metrics(path, analysis_start="2030-01-01", analysis_end="2030-12-31")

    assert result.metrics["HDD_15"] == pytest.approx(5.0)
    assert result.metrics["HDD_18"] == pytest.approx(8.0)
    assert result.metrics["CDD_22"] == pytest.approx(3.0)
    assert result.metrics["winter_mean_T_out_C"] == pytest.approx(15.0)
    assert result.metrics["summer_mean_T_out_C"] == pytest.approx(25.0)
    assert result.metrics["mean_solar_W_m2"] == pytest.approx(200.0)


def test_near_future_excludes_2050(tmp_path: Path) -> None:
    path = _write_climate(
        tmp_path / "near.csv",
        [
            {"timestamp": "2049-12-31T12:00:00", "T_out_C": 10.0, "I_solar_W_m2": 100.0},
            {"timestamp": "2050-01-01T12:00:00", "T_out_C": 30.0, "I_solar_W_m2": 500.0},
        ],
    )

    result = compute_climate_metrics(path, analysis_start="2030-01-01", analysis_end="2049-12-31")

    assert result.included_years == [2049]
    assert result.includes_2050 is False
    assert result.metrics["mean_T_out_C"] == pytest.approx(10.0)


def test_climate_metrics_include_calendar_year_rows(tmp_path: Path) -> None:
    path = _write_climate(
        tmp_path / "yearly.csv",
        [
            {"timestamp": "2030-01-01T12:00:00", "T_out_C": 10.0, "I_solar_W_m2": 100.0},
            {"timestamp": "2030-07-01T12:00:00", "T_out_C": 25.0, "I_solar_W_m2": 300.0},
            {"timestamp": "2031-01-01T12:00:00", "T_out_C": 5.0, "I_solar_W_m2": 200.0},
            {"timestamp": "2031-07-01T12:00:00", "T_out_C": 30.0, "I_solar_W_m2": 400.0},
        ],
    )

    result = compute_climate_metrics(path, analysis_start="2030-01-01", analysis_end="2031-12-31")

    assert [row["year"] for row in result.annual_metrics] == [2030, 2031]
    assert result.annual_metrics[0]["mean_T_out_C"] == pytest.approx(17.5)
    assert result.annual_metrics[0]["HDD_15"] == pytest.approx(5.0)
    assert result.annual_metrics[0]["CDD_22"] == pytest.approx(3.0)
    assert result.annual_metrics[1]["mean_solar_W_m2"] == pytest.approx(300.0)


def test_mid_century_includes_2050(tmp_path: Path) -> None:
    path = _write_climate(
        tmp_path / "mid.csv",
        [
            {"timestamp": "2049-12-31T12:00:00", "T_out_C": 5.0, "I_solar_W_m2": 100.0},
            {"timestamp": "2050-01-01T12:00:00", "T_out_C": 15.0, "I_solar_W_m2": 200.0},
        ],
    )

    result = compute_climate_metrics(path, analysis_start="2050-01-01", analysis_end="2070-12-31")

    assert result.included_years == [2050]
    assert result.includes_2050 is True
    assert result.metrics["mean_T_out_C"] == pytest.approx(15.0)


def test_ambiguous_temperature_columns_fail(tmp_path: Path) -> None:
    path = _write_climate(
        tmp_path / "ambiguous.csv",
        [
            {
                "timestamp": "2030-01-01T12:00:00",
                "T_out_C": 10.0,
                "temp_C": 11.0,
                "I_solar_W_m2": 100.0,
            }
        ],
    )

    with pytest.raises(ClimateMetricsError, match="Ambiguous climate temperature columns"):
        compute_climate_metrics(path, analysis_start="2030-01-01", analysis_end="2030-12-31")
