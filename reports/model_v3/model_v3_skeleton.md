# model_v3 Architecture And Validation Status

## Purpose

This document describes the current `model_v3` implementation and validation boundaries in thesis-safe terms. It avoids treating cached validation outputs as fresh canonical results.

## Core Runtime

The deterministic execution contract remains:

`InputDataset -> PreparedForcing -> PhysicsState -> ControlState -> SystemState -> ModelOutputs`

The scientific runtime is not the one-step smoke pipeline. The meaningful model paths are:

- annual deterministic simulation: sequential hourly household simulation over the configured reference year
- stochastic cohort simulation: sampled households, annual household runs, and aggregate cohort outputs
- climate uncertainty workflow: separate climate sensitivity branch when `climate.enabled: true`

The canonical thesis household/cohort run is defined by `config/thesis.yaml`: reference year `2023`, `30` households, full horizon, and climate disabled.

## Validation Categories

- internal consistency: smoke tests, accounting checks, runner flow, and unit/normalization audits
- baseline/literature annual calibration: annual Belgian household targets and end-use shares
- aggregate validation: Fluvius comparisons as the thesis-facing aggregate diagnostic source; the current artifact is weak/failed and is not a passed external validation
- high-frequency/event realism: KU Leuven case-study diagnostics for event structure

## Implementation Status

The architecture is operational rather than skeletal. The annual runner carries thermal state and control state through time, uses explicit timestamp-based timestep handling, and records electricity and thermal outputs. The cohort workflow wraps annual household runs and aggregates per-household profiles.

Annual electricity is calibrated to the configured literature baseline. That is a modelling and calibration choice, not an independent validation result. Raw/pre-calibration diagnostics are needed when discussing stochastic annual demand variation.

## Current Validation Reading

The current persisted validation artifacts are mixed cached outputs:

- `outputs/model_v3/validation/baseline_annual/validation_report_v3_baseline_annual.md` records `max steps: 24`; it is truncated and not valid as an annual thermal benchmark.
- The separate Belgian smart-meter validation path has been removed because no reliable independent Belgian smart-meter dataset is expected for this thesis model.
- `outputs/model_v3/validation/aggregate/validation_report_v3_aggregate.md` is a legacy LCL-normalized shape diagnostic, not thesis-facing validation evidence.
- `outputs/model_v3/validation/validation_report_v3_fluvius_external.md` compares against representative Fluvius profiles, currently fails simple aggregate-profile diagnostic thresholds, and is not measured feeder data.
- `outputs/model_v3/validation/validation_report_v3_kuleuven_high_freq.md` is a three-household high-frequency case study, not a statistical validation claim.

## Claims Not Supported

The current artifacts do not support claims of fully independent external calibration, exact measured-distribution equivalence, or appliance-level attribution validity. The final validation stance should use LCL only as an input load-shape source/internal diagnostic, Fluvius as a weak/failed aggregate external diagnostic that identifies mismatch to fix, and KU Leuven as the high-frequency event/ramp validation source.

The defensible statement is narrower: `model_v3` has a working annual/cohort runtime, explicit calibration and normalization machinery, broad validation runners, and cached validation evidence that still requires careful provenance checks before thesis citation.
