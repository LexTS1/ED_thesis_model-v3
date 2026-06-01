# Model v3 Global Summary

## Purpose

This document is a repository-level interpretation of `model_v3`. It is intended to sit between the very compact small report and full technical documentation: detailed enough to explain how the model actually works, but still short enough to read as a single overview.

## Executive Interpretation

`model_v3` is not just a placeholder skeleton anymore. It is a working household energy simulation framework with four meaningful layers:

- a strict single-step modular contract
- a sequential annual deterministic simulation
- a stochastic cohort wrapper that generates heterogeneous households and aggregates them
- a separate validation stack for internal consistency, literature calibration, aggregate comparisons, synthetic checks, and high-frequency case studies

The codebase is strongest when it is read as an annual household model plus a cohort realism layer built on top of that annual core. The main simulation logic lives in the annual runner and cohort engine, not in the one-step smoke-style pipeline.

At the same time, some scaffold-era design notes remain. The stale `outputs/model_v3/` artefact namespace was removed from the working tree because it contained artifacts from different horizons and run modes rather than one single clean benchmark state.

## What The Model Actually Simulates

At a conceptual level, `model_v3` represents one dwelling as a single thermal zone coupled to explicit electrical end uses and a simple heating/DHW system model. It then scales from one dwelling to many by sampling physical, behavioural, and technology variation across households.

The active layered contract is:

`InputDataset -> PreparedForcing -> PhysicsState -> ControlState -> SystemState -> ModelOutputs`

That contract is not just decorative. It structures the codebase into distinct responsibilities:

- `data` loads, harmonises, and reconstructs source datasets
- `adapters` convert source data into one timestep of semantically meaningful forcing
- `physics` computes the free-floating thermal response
- `control` decides whether heating is requested and whether extra ventilation is triggered
- `systems` applies heating capacity and converts thermal demand into electrical consumption
- `output` assembles and persists results

This is the architectural backbone of the repository.

## The Main Runtime Paths

There are three practical execution modes in the repository.

### 1. Single-step deterministic pipeline

`src/pipelines/run_model_v3.py` runs the strict layered contract once. It is useful for architecture checks, smoke tests, and stage timing, but it is not the main scientific runtime. It produces one `ModelOutputs` object and persists it under `outputs/model_v3/deterministic/`.

### 2. Sequential annual deterministic simulation

`src/pipelines/run_model_v3_annual.py` and `src/model_v3/simulation/annual_runner.py` are the real deterministic core. This path:

- filters inputs to the configured reference year
- steps through the weather timeline sequentially
- carries indoor temperature and thermostat state forward from hour to hour
- records timestep-level thermal and electrical outputs
- rescales annual electrical end uses back to the configured literature baseline

This annual runner is the foundation for both annual validation and the stochastic cohort workflow.

### 3. Stochastic cohort and climate workflow

`src/pipelines/run_model_v3_stochastic.py` has two roles:

- if `climate.enabled` is `true`, it runs the climate uncertainty workflow
- otherwise, it runs the cohort simulation

This is an important operational nuance: the same script is used for two quite different workflows.

## Inputs And Configuration

The default runtime config is `config/model.yaml`. The canonical thesis household/cohort config is `config/thesis.yaml`, with reference year `2023`, `30` households, `simulation.max_steps: null`, and climate disabled. These files define:

- module activation flags
- annual baseline targets
- electricity and thermal splits
- cohort size and stochastic settings
- climate ensemble settings
- source paths and harmonisation settings
- building, comfort, setpoint, and system defaults
- validation settings and acceptance thresholds

The input namespace is `inputs/`. Active runtime inputs are stored locally inside the repository, including weather, load-profile, solar, occupancy, end-use, and building reference files. The removed duplicate `inputs/model_v3/` namespace is not part of the canonical runtime.

The active source families are:

- weather time series
- facade-oriented PVGIS solar time series
- occupancy model specification
- representative electrical load profiles
- Belgian end-use share data
- merged building archetype parameters
- auxiliary airflow and internal-gain reference tables

The data path is more careful than a simple CSV read. `load_all_sources()`:

- loads each source
- harmonises cadence to the target resolution
- reconstructs missing values after harmonisation
- keeps provenance metadata
- resolves building/archetype properties
- packages everything into an `InputDataset`

That means the input layer already contains semantics such as source role, resolution, and reconstruction confidence. Annual weather selection now has an explicit full-year coverage guard: the selected reference-year weather rows must be close to 8760 for a non-leap year or 8784 for a leap year before any quick-run truncation is allowed.

## Deterministic Model Logic

The core physical logic is spread over `forcing_builder`, `physics_core`, `control_core`, and `system_core`.

### Forcing assembly

`build_prepared_forcing()` does more than merge columns. It constructs a physically and behaviourally meaningful timestep:

- maps raw electrical profile columns to explicit end uses
- derives occupancy state probabilities from the occupancy spec
- converts occupancy into schedule state, expected occupants, and occupant heat gains
- resolves time-of-day thermostat setpoints, including household-specific schedules and daily overrides
- scales electrical end-use profiles to the configured annual Belgian baseline
- computes internal gains from occupants, appliances, lighting, cooking, and explicit internal-gain inputs
- computes orientation-resolved solar gains from PVGIS irradiance and glazing parameters

This is where raw datasets become model forcing.

### Thermal physics

`run_physics()` implements a single-zone thermal balance with:

- envelope heat exchange
- infiltration and ventilation losses
- internal gains
- solar gains
- adaptive explicit substepping for stability

The thermal integrator in `thermal_dynamics.py` computes the next free-floating indoor temperature and reports the stability factor and number of substeps. This is a real numerical integration step, not just a static formula evaluation.

### Control

`run_control()` applies:

- hysteresis thermostat logic
- occupancy-aware heating activation
- optional window-opening logic for cooling/ventilation events

It produces the requested heating power, but does not yet apply system constraints.

### Systems and electricity accounting

`run_systems()` applies:

- heating capacity limits
- unmet heating tracking
- excess heat and comfort violation tracking
- conversion of thermal heating and DHW demand into electricity
- aggregation of all electric end uses into total demand

One notable design choice is that thermal-to-electric conversion for space heating and DHW is tied back to the configured annual baseline shares through representative factors, while the annual runner later renormalises annual electricity totals to hit the literature targets exactly. The thesis config also applies `building.ua_multiplier: 0.80` as an explicit envelope/UA calibration for the selected thesis archetype configuration. These are calibrations to literature baselines, not independent predictions of annual electricity or envelope heat loss.

## Stochastic Cohort Logic

The stochastic layer is the main reason `model_v3` is more than a deterministic annual calculator.

### Household sampling

`sample_household_parameters()` draws three uncertainty blocks:

- physical: UA, thermal mass, infiltration, COP
- behaviour: occupancy intensity, time shifts, schedule variability, setpoint shifts, DHW intensity, appliance variability, optional EV/dryer ownership
- technology: heating technology type and capacity scaling

The canonical household count now lives under `cohort.n_households`, with compatibility handling for deprecated `simulation.n_households`.

### Household behaviour generation

`simulate_household_electricity()` creates household-specific profiles by combining:

- a household class regime such as `low_flat`, `workday_absent`, `peak_heavy_family`, or `daytime_home`
- sampled base load
- event-based appliance demand
- occupancy-driven DHW event generation
- lighting shaped by daylight and occupancy
- household-specific thermostat schedules

A key modelling choice is that the stochastic layer redistributes electricity in time but then renormalises annual energy back to the target annual end-use totals. In other words, stochasticity primarily changes temporal realism, not annual calibration.

### Cohort aggregation

`run_cohort_simulation()` streams households one by one, runs each through the full annual deterministic engine, and then aggregates:

- total and per-household profiles
- P10/P50/P90 envelopes
- diversity factor
- peak distributions
- DHW spread
- household summaries
- sampled parameter ranges

This makes the cohort path a true wrapper around the annual simulation, not a separate simplified model.

The cohort output contract separates readable summary material from heavy diagnostics. `cohort_summary.json` is the thesis-facing summary: it includes run metadata, sampled technology and household-class counts, calibrated annual energy statistics, raw/pre-calibration annual calibration summaries, and peak distributions. Full time-series data remains in `aggregate_profile.csv`, while per-household annual energy and calibration details are written to `household_annual_energy.csv` and `household_calibration_diagnostics.json`.

The annual runner calibrates each household back to the configured electricity baseline, so calibrated per-household annual electricity is expected to be tightly aligned. Raw/pre-calibration annual diagnostics are the appropriate place to inspect how much stochastic annual demand existed before calibration. Carrier-aware technology labels now use the Belgian stock mapping when enabled, but only the carrier shares are observed directly; the carrier-to-appliance split remains assumption-driven and should be reported with the YAML source metadata.

## Climate Uncertainty Workflow

The climate path is distinct from the household cohort path.

`run_climate_ensemble()`:

- reads historical PVGIS weather and solar years
- splits them into complete yearly blocks
- samples yearly weather members
- perturbs temperatures with a monthly AR(1) process
- injects member-specific forcing into the annual model
- computes summary statistics for annual demand and peak demand
- validates the synthetic climate members for drift, NaNs, temperature distribution preservation, and lag-1 autocorrelation realism

This makes climate uncertainty a forcing-driven uncertainty layer on top of the annual model rather than a separate demand model.

## Validation Structure

Validation is one of the strongest parts of the repository structure. It is not embedded inside the main simulation path; it is implemented as dedicated runners under `src/model_v3/validation/runners/`.

The validation reports should be read using five explicit categories:

- internal consistency: smoke checks, accounting sanity, runner flow, and normalization audits
- baseline/literature annual calibration: configured Belgian annual targets and end-use shares
- aggregate validation: Fluvius comparisons as the thesis-facing aggregate validation source
- high-frequency/event realism: KU Leuven case-study diagnostics, not statistical validation

The main runners are:

- `validate_baseline_annual.py`
- `validate_against_aggregate.py`
- `validate_high_frequency_kuleuven.py`
- `validate_against_synthetic.py`

The validation core computes several families of metrics:

- mean error
- variance realism
- distribution realism
- temporal correlation and autocorrelation
- event and peak behaviour
- end-use shares
- validation independence

The acceptance criteria are ASHRAE-style threshold checks for monthly and hourly bias/error plus peak and quantile criteria.

## How To Read The Current Output Artifacts

This is the most important practical caveat in the repository.

The codebase is coherent, but historical persisted outputs were not one single synchronized benchmark set. The stale folders under `outputs/model_v3/` were removed from the canonical working tree. A report should be treated as thesis-ready only when its embedded metadata or manifest confirms the canonical thesis config, reference year, cohort size, horizon, and data provenance.

Examples:

- `experiments/scenario_tree/` contains the selected climate-only / stock-weighted scenario-tree runs and summaries used for the current thesis results.
- `experiments/scenario_tree_output34/` contains the selected hourly cohort runs used for peak/grid stress, diversity, bills/emissions, and investment/adaptation outputs.
- `outputs/validation/baseline_annual/validation_report_v3_baseline_annual.md` is the current full-horizon annual baseline validation report.
- The separate Belgian smart-meter validation path has been removed because no reliable independent Belgian smart-meter dataset is expected for this thesis model.
- `outputs/validation/validation_report_v3_fluvius_external.md` compares representative Fluvius profiles; it is not measured feeder validation.
- `outputs/validation/validation_report_v3_kuleuven_high_freq.md` is a three-household high-frequency case study, not a statistical validation claim.

So the right interpretation is:

- trust the code and the run settings first
- do not cite removed legacy folders such as `outputs/model_v3/annual/`, `outputs/model_v3/stochastic/`, and `outputs/model_v3/climate_uncertainty/`
- do not assume every report in `outputs/` reflects the same configuration state
- prefer artifacts with an explicit `run_manifest.json` or report runtime context when identifying thesis-ready outputs

Within that mixed artifact set, legacy LCL normalized aggregate reports should now be read as internal diagnostics only. The thesis-facing validation evidence should come from the Fluvius aggregate-profile comparison and the KU Leuven high-frequency case studies, with their own caveats about representativeness and sample size. The most direct circular-validation issue is avoided by not using LCL or an unreliable Belgian smart-meter path as final validation evidence.

## Current Repository-Level Status

Reading the code and the available artifacts together, `model_v3` is best described as an operational research model in active development rather than a finished, frozen thesis product.

### What is strong

- The layered architecture is clear and defensible.
- The annual simulation path is real, sequential, and physically meaningful.
- The stochastic layer is integrated at household-input level rather than added as output noise.
- Validation is broad and modular.
- Input provenance, harmonisation, and reconstruction are handled explicitly.

### What is still transitional

- The obvious scaffold-era placeholder modules have been removed from the active package surface.
- `ModelOutputs.run_id` now uses a non-placeholder `model-v3-*` identifier derived from the output timestamp.
- The top-level one-step deterministic pipeline is more useful for architecture checking than for substantive analysis.
- LCL validation is now treated as an internal diagnostic only; final validation should rely on Fluvius and KU Leuven to avoid direct input/validation dataset reuse.

## Bottom-Line Interpretation

`model_v3` is a layered Belgian household energy model with a real annual thermal-electrical core, a nontrivial stochastic household/cohort wrapper, and a fairly serious validation framework. The most meaningful execution paths are the annual deterministic runner, the cohort engine, and the dedicated validation runners.

It should not be read as a perfectly clean final package yet. It is better understood as a strong working thesis model whose architecture is already mature, whose stochastic realism layer is substantive, and whose remaining weaknesses are mostly about calibration consistency, validation independence, artifact hygiene, and residual scaffold remnants rather than about missing core structure.
