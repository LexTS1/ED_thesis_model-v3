# Model v3 PV Validation Triangle

This guide documents the PV validation workflow implemented in
`src/model_v3/validation/technology/pv/run_pv_validation_triangle.py`. It does
not run scenario-tree simulations. Each leg compares a persisted model profile
with a different reference layer; the three legs answer different questions and
must not be collapsed into one pass/fail claim.

## Validation Legs

1. **PVGIS physical reference**

   Reference: `outputs/validation/pvgis/Timeseries_50.803_4.334_SA3_1kWp_crystSi_14_35deg_0deg_2005_2023.csv`.

   Model profile: `outputs/validation/technology_pv/model_capacity_factor_2023.csv`.

   Purpose: check the irradiance-to-PV conversion for a representative 1 kWp
   system. The current canonical report is a `model_reference_comparison`, but
   records that it reused the existing alignment artifact because the raw PVGIS
   file was not resolved during that run.

2. **Elia ODS032 Belgian generation**

   Reference cache: `inputs/validation/pv/elia/ods032_belgium_pv_2023_pt15m.csv`.

   Model profile: `outputs/validation/technology_pv/model_capacity_factor_2023.csv`.

   Purpose: compare normalized model PV production with Belgian aggregate
   measured/upscaled production. The current report has 365 overlapping daily
   values, daily capacity-factor correlation `0.976`, daily RMSE `0.028`, and
   mean absolute monthly capacity-factor error `25.2%`. This is useful aggregate
   shape validation, not proof that every household PV system is calibrated.

3. **Fluvius residential PV signature**

   References: `inputs/load_profiles/fluvius/P6269_Open_Data_geen_ZP.csv` and
   `inputs/load_profiles/fluvius/P6269_Open_Data_enkel_ZP.csv`.

   Model profile: `outputs/validation/technology_pv/model_net_load_2023.csv`.

   Purpose: check whether PV changes residential net load in the expected
   direction and at plausible hours. The current report is a matched
   `model_reference_comparison`, with mean-daily correlation `0.478` and RMSE
   `1.212 kW`. This is weak external agreement and must not be described as a
   successful household-profile validation.

## Command

Run against cached local references and persisted/generated validation profiles:

```bash
PYTHONPATH=src python3 -m model_v3.validation.technology.pv.run_pv_validation_triangle \
  --repo-root . \
  --config config/validation/technology_pv.yaml \
  --print-summary
```

Add `--download-elia` only when the configured Elia cache is missing and
network access is intentionally allowed. Add `--skip-fluvius` for a faster
technology smoke check that deliberately omits the residential-signature leg.

## Outputs

- `reports/model_v3/validation/technology/pv/technology_pv_validation_triangle_report.md`
- `reports/model_v3/validation/technology/pv/technology_pv_validation_triangle_metrics.json`
- `reports/model_v3/validation/technology/pv/pvgis_reference_alignment.csv`
- `reports/model_v3/validation/technology/pv/elia_ods032_reference_timeseries.csv`
- `reports/model_v3/validation/technology/pv/fluvius_pv_signature_mean_daily.csv`
- `figures/model_v3/validation/technology/pv/pvgis_reference_validation.png`
- `figures/model_v3/validation/technology/pv/elia_ods032_capacity_factor.png`
- `figures/model_v3/validation/technology/pv/fluvius_pv_signature_mean_daily.png`

## Status Labels

- `model_reference_comparison`: both a reference and a matched model profile
  were available. It describes comparison availability, not acceptance.
- `reference_ingested`: the reference was loaded, but no matched model profile
  was available.
- `missing_reference`: a configured reference was absent.
- `disabled` or `skipped`: the leg was intentionally omitted.

## Interpretation Limits

The current triangle is fully connected in the sense that all three legs have
matched model/reference artifacts. Its evidence strength is uneven: PVGIS gives
strong physical-shape agreement, Elia gives strong daily aggregate correlation
with material monthly bias, and Fluvius gives only weak residential-profile
agreement. Always report the leg-specific metrics and warnings instead of
claiming that “PV validation passed” as one undifferentiated result.
