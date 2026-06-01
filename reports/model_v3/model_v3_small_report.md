# Model v3 Thesis-Safe Validation Summary

## Purpose

This compact report summarises the current `model_v3` validation position using thesis-safe language. It distinguishes runtime functionality, calibration checks, and external-data comparisons. It does not upgrade cached artifacts into current thesis evidence.

Canonical thesis runtime:

- config: `config/thesis.yaml`
- reference year: `2023`
- cohort size: `30` households
- horizon: `simulation.max_steps: null`
- climate mode: disabled for the household/cohort thesis run

## Validation Taxonomy

- internal consistency: smoke checks, annual accounting sanity, runner flow, report generation, and unit/normalization audits
- baseline/literature annual calibration: comparison with configured Belgian annual electricity, space-heating, DHW, and end-use-share targets
- aggregate validation: Fluvius profile comparisons as the thesis-facing aggregate diagnostic source; the current artifact is weak/failed and must not be cited as a passed external validation
- high-frequency/event realism: KU Leuven case-study checks for spikes, ramps, and daily maxima

## Current Artifact Status

The files under `outputs/validation/` and `reports/model_v3/validation/` are the current local validation artifacts. They should still be cited with their embedded metadata and report context.

Current cached validation artifacts show:

- `baseline_annual`: full-horizon annual baseline validation after the explicit `building.ua_multiplier: 0.80` calibration
- `aggregate`: aggregate-profile diagnostic evidence, with Fluvius treated as the thesis-facing aggregate reference
- `validation_report_v3_fluvius_external.md`: representative Fluvius aggregate-profile comparison, currently weak/failed against simple diagnostic thresholds and not measured feeder validation
- `validation_report_v3_kuleuven_high_freq.md`: three-household high-frequency case study, not a statistical validation claim

## Defensible Findings

The codebase supports an operational deterministic annual model and stochastic cohort workflow. The canonical thesis config uses a complete 2023 weather-driven annual horizon with a 30-household cohort and climate disabled.

The legacy standalone annual and stochastic output folders were removed from the canonical thesis artefact set. Use `experiments/scenario_tree/`, `experiments/scenario_tree_output34/`, and `outputs/validation/baseline_annual/` for current thesis evidence.

The thesis config now applies `building.ua_multiplier: 0.80` as an explicit envelope/UA calibration so the full-horizon baseline space-heating thermal total sits inside the configured literature range.

The technology labels in cohort outputs now come from the Belgian carrier-stock mapping when `uncertainty.technology.use_belgian_stock_baseline` is enabled. The carrier shares are observed, but the appliance-level mapping remains an explicit assumption and should be sensitivity-tested rather than presented as a measured Belgian technology census.

## Caveats For Thesis Use

Do not claim independent external calibration from LCL-based normalized aggregate artifacts. LCL is the configured representative input load-shape source, so any LCL comparison is an internal diagnostic only. The separate Belgian smart-meter validation path has been removed because no reliable independent Belgian smart-meter dataset is expected for this thesis model.

Use Fluvius and KU Leuven as thesis-facing external checks, but keep the wording conservative: Fluvius currently exposes aggregate profile mismatch rather than proving aggregate realism, and KU Leuven provides only high-frequency event/ramp case-study evidence.

Do not cite legacy baseline reports from the removed `outputs/model_v3/` namespace. Use the current `outputs/validation/baseline_annual/` report.

All active runtime inputs are local files under `inputs/`; sibling-repository symlinks and the removed duplicate `inputs/model_v3/` namespace are not part of the core model package.

## Regeneration Commands

Use the canonical config explicitly when regenerating thesis-facing household/cohort outputs:

```bash
PYTHONPATH=src python3 src/pipelines/run_model_v3_stochastic.py --config config/thesis.yaml
```

Validation runners now accept `--config`; pass `config/thesis.yaml` explicitly before citing regenerated outputs as canonical.
