from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
MODEL_V3_SRC = REPO_ROOT / "src"
if str(MODEL_V3_SRC) not in sys.path:
    sys.path.insert(0, str(MODEL_V3_SRC))

from model_v3.validation.technology.pv.run_pv_validation_triangle import run_validation, validate_pvgis_leg


def _write_pvgis(path: Path) -> None:
    rows = [
        "Latitude (decimal degrees):\t50.803",
        "Longitude (decimal degrees):\t4.334",
        "Nominal power of the PV system (c-Si) (kWp):\t1.0",
        "System losses (%):\t14.0",
        "time,P,Gb(i),Gd(i),Gr(i),H_sun,T2m,WS10m,Int",
    ]
    for timestamp in pd.date_range("2024-01-01 00:10", periods=48, freq="h"):
        hour = timestamp.hour
        irradiance = 600.0 if 10 <= hour <= 14 else 0.0
        power = irradiance * 0.84
        rows.append(
            f"{timestamp:%Y%m%d:%H%M},{power:.3f},{irradiance * 0.6:.3f},{irradiance * 0.35:.3f},{irradiance * 0.05:.3f},0,8,2,0"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def _write_elia(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                "datetime;resolutioncode;region;measured;monitoredcapacity;loadfactor",
                "2024-01-01T00:00:00+01:00;PT15M;Belgium;0;1000;0",
                "2024-01-01T00:15:00+01:00;PT15M;Belgium;100;1000;0.1",
                "2024-01-01T00:30:00+01:00;PT15M;Belgium;200;1000;0.2",
                "2024-01-01T00:45:00+01:00;PT15M;Belgium;50;1000;0.05",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def _write_fluvius(path: Path, *, with_pv: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        "EAN_ID,Datum,Datum_Startuur,Volume_Afname_KWh,Volume_Injectie_KWh,Warmtepomp_Indicator,Elektrisch_Voertuig_Indicator,PV_Installatie_Indicator,Contract_Categorie"
    ]
    for meter in ("a", "b"):
        for timestamp in pd.date_range("2024-01-01 00:00", periods=96, freq="15min", tz="Europe/Brussels"):
            hour = timestamp.hour
            imported = 0.10
            exported = 0.0
            if with_pv and 10 <= hour <= 14:
                imported = 0.02
                exported = 0.08
            rows.append(
                f"{meter},2024-01-01,{timestamp.isoformat()},{imported},{exported},False,False,{with_pv},RES"
            )
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def test_pv_validation_triangle_runs_on_synthetic_sources(tmp_path: Path) -> None:
    pvgis = tmp_path / "validation" / "pvgis" / "reference.csv"
    elia = tmp_path / "inputs" / "validation" / "pv" / "elia" / "ods032.csv"
    fluvius_no_pv = tmp_path / "fluvius" / "geen_zp.csv"
    fluvius_pv = tmp_path / "fluvius" / "enkel_zp.csv"
    _write_pvgis(pvgis)
    _write_elia(elia)
    _write_fluvius(fluvius_no_pv, with_pv=False)
    _write_fluvius(fluvius_pv, with_pv=True)

    config = tmp_path / "config.yaml"
    config.write_text(
        f"""
validation:
  technology:
    pv:
      pvgis:
        reference_file: {pvgis}
        capacity_kwp: 1.0
        performance_ratio: 0.86
      elia:
        local_file: {elia}
        download_if_missing: false
      fluvius:
        enabled: true
        no_pv_file: {fluvius_no_pv}
        pv_file: {fluvius_pv}
      outputs:
        report_dir: reports/pv
        figure_dir: figures/pv
""",
        encoding="utf-8",
    )

    result = run_validation(repo_root=tmp_path, config_path=config)

    assert result["legs"]["pvgis_physical_reference"]["status"] == "model_reference_comparison"
    assert result["legs"]["elia_ods032_belgian_generation"]["status"] == "reference_ingested"
    assert result["legs"]["fluvius_residential_signature"]["status"] == "reference_ingested"
    assert result["legs"]["elia_ods032_belgian_generation"]["metrics"]["rows"] == 4
    assert (tmp_path / result["report_path"]).exists()
    assert (tmp_path / result["metrics_path"]).exists()
    assert "annual_specific_yield_error_pct" in result["legs"]["pvgis_physical_reference"]["metrics"]
    assert result["legs"]["fluvius_residential_signature"]["metrics"]["pv_daytime_mean_export_kW"] > 0.0


def test_pvgis_leg_marks_missing_reference(tmp_path: Path) -> None:
    result = validate_pvgis_leg(
        tmp_path,
        {"reference_globs": ["missing/*.csv"]},
        tmp_path / "reports",
        tmp_path / "figures",
    )

    assert result.status == "missing_reference"
    assert result.warnings
