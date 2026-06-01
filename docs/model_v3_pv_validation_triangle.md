# Model v3 PV Validation Triangle

This note documents the PV validation workflow implemented in
`src/model_v3/validation/technology/pv/run_pv_validation_triangle.py`.
It does not run scenario-tree simulations. It ingests external PV reference
datasets and compares them with model outputs only when a matching model
profile is explicitly configured.

## Validation legs

1. **PVGIS physical reference**

   Source: `outputs/validation/pvgis/Timeseries_50.803_4.334_SA3_1kWp_crystSi_14_35deg_0deg_2005_2023.csv`

   Purpose: validate the model's irradiance-to-PV conversion for a 1 kWp
   system against a PVGIS hourly PV-output export. This is currently a direct
   model-reference comparison because the runner calls
   `model_v3.systems.distributed_energy.pv_generation_from_irradiance`.

2. **Elia ODS032 Belgian PV generation**

   Source cache:
   `inputs/model_v3/validation/pv/elia/ods032_belgium_pv_2024_pt15m.csv`

   Purpose: ingest Belgian 2024 quarter-hourly measured/upscaled PV generation
   and monitored capacity. Until a matched 2024 model capacity-factor profile
   is configured, this leg is `reference_ingested`, not a model validation pass.

3. **Fluvius residential PV signature**

   Sources:
   `inputs/model_v3/load_profiles/fluvius/P6269_Open_Data_geen_ZP.csv` and
   `inputs/model_v3/load_profiles/fluvius/P6269_Open_Data_enkel_ZP.csv`

   Purpose: compare representative no-PV and PV residential net-load signatures.
   Until a matched model household/cohort net-load profile is configured, this
   leg is `reference_ingested`, not a model validation pass.

## Commands

First-time Elia ingestion:

```bash
python3 -m model_v3.validation.technology.pv.run_pv_validation_triangle \
  --repo-root . \
  --config config/model_v3/validation/technology_pv.yaml \
  --download-elia \
  --print-summary
```

Subsequent runs using the cached Elia CSV:

```bash
python3 -m model_v3.validation.technology.pv.run_pv_validation_triangle \
  --repo-root . \
  --config config/model_v3/validation/technology_pv.yaml \
  --print-summary
```

Fast smoke run without loading the large Fluvius files:

```bash
python3 -m model_v3.validation.technology.pv.run_pv_validation_triangle \
  --repo-root . \
  --config config/model_v3/validation/technology_pv.yaml \
  --skip-fluvius \
  --print-summary
```

## Outputs

Main report:
`reports/model_v3/validation/technology/pv/technology_pv_validation_triangle_report.md`

Machine-readable metrics:
`reports/model_v3/validation/technology/pv/technology_pv_validation_triangle_metrics.json`

Reference/alignment tables:

- `reports/model_v3/validation/technology/pv/pvgis_reference_alignment.csv`
- `reports/model_v3/validation/technology/pv/elia_ods032_reference_timeseries.csv`
- `reports/model_v3/validation/technology/pv/fluvius_pv_signature_mean_daily.csv`

Figures:

- `figures/model_v3/validation/technology/pv/pvgis_reference_validation.png`
- `figures/model_v3/validation/technology/pv/elia_ods032_capacity_factor.png`
- `figures/model_v3/validation/technology/pv/fluvius_pv_signature_mean_daily.png`

## Status labels

- `model_reference_comparison`: both a reference and a matched model output were
  available.
- `reference_ingested`: the reference dataset was loaded and summarized, but no
  matched model output was configured.
- `missing_reference`: the configured source file was not available.
- `disabled` or `skipped`: the leg was intentionally not run.

## Current limitation

The PVGIS leg is currently the only true model-reference comparison. Elia and
Fluvius are implemented as ingestion and reference-summary layers until the
model produces matched validation profiles:

- for Elia: a 2024 Belgian PV capacity-factor profile from the model;
- for Fluvius: a household/cohort net-load profile matched to the Fluvius PV
  category and temporal resolution.

Do not claim that the model has passed Elia or Fluvius PV validation until those
matched model profiles are configured and the report shows
`model_reference_comparison` for those legs.
