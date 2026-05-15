# Model v3 Thesis-Safe Validation Summary

## Purpose

This compact report summarises the current `model_v3` validation position using thesis-safe language. It distinguishes runtime functionality, calibration checks, and external-data comparisons. It does not upgrade cached artifacts into current thesis evidence.

Canonical thesis runtime:

- config: `config/model_v3/model_v3_thesis.yaml`
- reference year: `2023`
- cohort size: `30` households
- horizon: `simulation.max_steps: null`
- climate mode: disabled for the household/cohort thesis run

## Validation Taxonomy

- internal consistency: smoke checks, annual accounting sanity, runner flow, report generation, and unit/normalization audits
- baseline/literature annual calibration: comparison with configured Belgian annual electricity, space-heating, DHW, and end-use-share targets
- aggregate validation: Fluvius profile comparisons as the thesis-facing aggregate validation source
- high-frequency/event realism: KU Leuven case-study checks for spikes, ramps, and daily maxima

## Current Artifact Status

The files under `outputs/model_v3/validation/` are cached validation artifacts. They should be treated as historical evidence unless their embedded metadata proves they were generated from the canonical thesis config and full horizon.

Current cached validation artifacts show:

- `baseline_annual`: `max steps: 24`; not thesis-valid for annual thermal validation
- `aggregate`: legacy LCL-normalized shape comparison only; not part of the thesis-facing validation evidence
- `validation_report_v3_fluvius_external.md`: representative Fluvius aggregate-profile comparison, not measured feeder validation
- `validation_report_v3_kuleuven_high_freq.md`: three-household high-frequency case study, not a statistical validation claim

## Defensible Findings

The codebase supports an operational deterministic annual model and stochastic cohort workflow. The canonical thesis config uses a complete 2023 weather-driven annual horizon with a 30-household cohort and climate disabled.

The cached annual output at `outputs/model_v3/annual/annual_summary.json` records a full 2023 run with `8760` steps, annual electricity near `3900` kWh, space-heating thermal demand near `11079.68` kWh, and DHW thermal demand near `2999.91` kWh. This is useful cached evidence, but it should still be cited with its artifact provenance.

The cached stochastic output at `outputs/model_v3/stochastic/cohort_summary.json` records `30` households and calibrated mean annual electricity near `3900` kWh. Calibrated annual electricity is intentionally baseline-aligned; raw/pre-calibration diagnostics are required to discuss stochastic annual spread. The thesis config also applies `building.ua_multiplier: 1.10` as an explicit envelope/UA calibration so the deterministic annual space-heating thermal total sits inside the configured literature range.

The technology labels in cohort outputs now come from the Belgian carrier-stock mapping when `uncertainty.technology.use_belgian_stock_baseline` is enabled. The carrier shares are observed, but the appliance-level mapping remains an explicit assumption and should be sensitivity-tested rather than presented as a measured Belgian technology census.

## Caveats For Thesis Use

Do not claim independent external calibration from LCL-based normalized aggregate artifacts. LCL is the configured representative input load-shape source, so any LCL comparison is an internal diagnostic only. The separate Belgian smart-meter validation path has been removed because no reliable independent Belgian smart-meter dataset is expected for this thesis model.

Use Fluvius and KU Leuven as the thesis-facing validation sources: Fluvius for independent aggregate profile realism, and KU Leuven for independent high-frequency event/ramp realism.

Do not describe the cached `baseline_annual` report as a valid annual thermal benchmark while it records only `24` steps.

All active runtime inputs are local files under `inputs/model_v3/`; sibling-repository symlinks are not part of the core model package.

## Regeneration Commands

Use the canonical config explicitly when regenerating thesis-facing household/cohort outputs:

```bash
PYTHONPATH=src python3 src/pipelines/run_model_v3_stochastic.py --config config/model_v3/model_v3_thesis.yaml
```

Validation runners now accept `--config`; pass `config/model_v3/model_v3_thesis.yaml` explicitly before citing regenerated outputs as canonical.
