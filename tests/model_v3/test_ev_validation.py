from __future__ import annotations

from pathlib import Path

import pandas as pd

from model_v3.validation.technology.ev.run_ev_validation import run_validation


def _write_fluvius_fixture(path: Path, *, ev: bool) -> None:
    timestamps = pd.date_range("2024-01-01T00:00:00Z", periods=96, freq="15min")
    rows = []
    for timestamp in timestamps:
        base_kwh = 0.05
        ev_kwh = 0.0
        if ev and 18 <= timestamp.hour < 22:
            ev_kwh = 0.30
        rows.append(
            {
                "EAN_ID": 1 if not ev else 2,
                "Datum": timestamp.date().isoformat(),
                "Datum_Startuur": timestamp.isoformat().replace("+00:00", ".000Z"),
                "Volume_Afname_KWh": base_kwh + ev_kwh,
                "Volume_Injectie_KWh": 0.0,
                "Warmtepomp_Indicator": 0,
                "Elektrisch_Voertuig_Indicator": 1 if ev else 0,
                "PV_Installatie_Indicator": 0,
                "Contract_Categorie": "Residentieel",
            }
        )
    pd.DataFrame(rows).to_csv(path, index=False)


def _config(repo: Path) -> dict:
    return {
        "validation": {
            "technology": {
                "ev": {
                    "fluvius": {
                        "base_no_pv_file": str(repo / "base.csv"),
                        "ev_no_pv_file": str(repo / "ev.csv"),
                    },
                    "model": {
                        "ev_profile_file": str(repo / "reports" / "model_ev.csv"),
                        "cohort_size": 5,
                        "reference_year": 2024,
                        "periods": 24,
                        "target_resolution_seconds": 3600,
                        "ev_config": {
                            "annual_use": {
                                "km_per_year": {"base": 15000},
                                "specific_consumption_kwh_per_100km": {"base": 14.2},
                            },
                            "charging": {
                                "home_charging_probability": {"base": 0.70},
                                "charger_power_kw": {"base": 7.4},
                                "uncontrolled_arrival_window": {"start_hour": 18, "end_hour": 22},
                            },
                        },
                    },
                    "outputs": {
                        "report_dir": str(repo / "reports"),
                        "figure_dir": str(repo / "figures"),
                    },
                }
            }
        }
    }


def test_ev_validation_generates_model_reference_comparison(tmp_path: Path) -> None:
    _write_fluvius_fixture(tmp_path / "base.csv", ev=False)
    _write_fluvius_fixture(tmp_path / "ev.csv", ev=True)

    result = run_validation(tmp_path, _config(tmp_path), generate_model_profiles=True)

    assert result.status == "model_reference_comparison"
    assert (tmp_path / "reports" / "technology_ev_validation_report.md").exists()
    assert (tmp_path / "reports" / "technology_ev_validation_metrics.json").exists()
    assert (tmp_path / "reports" / "fluvius_ev_signature_mean_daily.csv").exists()
    assert result.metrics["model_annual_kWh_per_active_EV"] > 0
    assert "model_vs_reference_mean_daily_rmse_kW" in result.metrics


def test_ev_validation_reference_ingested_without_model_profile(tmp_path: Path) -> None:
    _write_fluvius_fixture(tmp_path / "base.csv", ev=False)
    _write_fluvius_fixture(tmp_path / "ev.csv", ev=True)

    result = run_validation(tmp_path, _config(tmp_path), generate_model_profiles=False)

    assert result.status == "reference_ingested"
    assert result.warnings
    assert (tmp_path / "reports" / "fluvius_ev_signature_mean_daily.csv").exists()


def test_ev_validation_reports_ku_leuven_as_secondary_context(tmp_path: Path) -> None:
    _write_fluvius_fixture(tmp_path / "base.csv", ev=False)
    _write_fluvius_fixture(tmp_path / "ev.csv", ev=True)

    result = run_validation(tmp_path, _config(tmp_path), generate_model_profiles=True)
    report = (tmp_path / "reports" / "technology_ev_validation_report.md").read_text(encoding="utf-8")

    assert result.metrics["ku_leuven_status"] == "secondary_context_only"
    assert "whole-house import/export" in report

