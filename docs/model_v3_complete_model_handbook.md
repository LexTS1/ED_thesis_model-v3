# Model v3 Complete Handbook: Scenario-Tree Residential Energy Demand Model

Architecture, Inputs, Scenario Design, Outputs, Validation, Caveats, and Usage Guide

Repository: `model_v3`

Generation date UTC: 2026-06-01T05:48:44+00:00

Git commit: 9d239a01d14a4f32c729af8825bd4047a3e2e7e4

Git dirty status: clean

This document is generated from local repository metadata, scripts, configs, manifests, summaries, validation reports, and figure metadata. It is not a literature review and it does not fabricate missing results.


# Executive summary

`model_v3` is a bottom-up residential energy-demand modelling repository with a scenario-tree layer for organizing climate, technology, and stochastic uncertainty. The core model is implemented under `src/model_v3/`; the scenario-tree experiment space is under `experiments/scenario_tree/`; the main scenario-tree configuration files are under `config/scenario_tree/`.

For the thesis, the scenario-tree layer is useful because it separates three sources of variation that would otherwise be mixed together in output filenames: climate forcing, residential technology assumptions, and stochastic household or cohort realizations. The implemented design supports this claim in a careful form:

Climate projections were organized into a structured scenario tree consisting of a historical baseline and three future climate windows under RCP2.6, RCP4.5, and RCP8.5. Each climate branch was combined with technology adoption assumptions and stochastic household realizations. This allowed climate, technology, and behavioural uncertainty to be separated and compared through consistent output metrics.

The repository currently contains a configured scenario tree with 2800 enumerated scenario leaves. The audit/registry evidence available to this handbook supports 37 latest-successful scenario leaves and 37 standardized per-leaf summary rows. Therefore the framework is implemented, but execution coverage is partial. This handbook does not claim that all leaves have run.

Implemented components detected in the repository include scenario-tree schema files, stable scenario IDs, canonical climate windows, an explicit 2050 overlap policy, generated experiment-space manifests, per-leaf configs, a runner/provenance layer, standardized outputs, comparison definitions, generated scenario-tree figures, and audit/validation reports where present. The comparison validation report also records missing comparison groups where successful summary rows are not available.

What can currently be claimed: the repository contains a traceable scenario-tree framework and generated artifacts for a subset of successful leaves. Internal validation and audit reports check schema consistency, input references, summaries, comparisons, figures, 2050 policy handling, and traceability. What cannot be claimed from the scenario-tree reports alone: complete execution of all enumerated leaves, external empirical validation of model accuracy, or calibrated future technology adoption forecasts.

For a supervisor, the short explanation is: `model_v3` simulates residential energy demand from building, climate, technology, and stochastic household assumptions; the scenario tree turns that model into a reproducible experiment design so future climate pathways, technology cases, and behavioural realizations can be compared without losing traceability.

## Implementation status by phase
| phase | expected deliverables | detected files | status | warnings | next action |
| --- | --- | --- | --- | --- | --- |
| Phase 1 - scenario-tree schema | Schema, climate windows, technology cases, realization policy. | config/scenario_tree/scenario_tree_schema.yaml; config/scenario_tree/climate_windows.yaml; config/scenario_tree/technology_cases.yaml; config/scenario_tree/realization_policy.yaml | implemented | none | Validate schema before changing branch dimensions. |
| Phase 2 - directory and naming convention | Experiment space, manifests, stable scenario and leaf paths. | experiments/scenario_tree/manifests/scenario_tree_manifest.yaml; experiments/scenario_tree/manifests/scenario_leaf_index.csv | implemented | none | Regenerate experiment space if the leaf index is stale. |
| Phase 3 - scenario-leaf configs | Per-leaf run_config.yaml and inputs_manifest.yaml files. | experiments/scenario_tree/manifests/config_validation_report.md | implemented | none | Run leaf-config validation after changing inputs or technology files. |
| Phase 4 - runner/orchestration | Scenario-tree runner, provenance, logs, run registry. | src/model_v3/scenarios/run_scenario_tree.py; experiments/scenario_tree/manifests/run_registry.csv | implemented | runner exists, but registry/audit supports only 37 latest-successful leaves out of 2800 enumerated leaves | Run dry-run first; execute a small pair before batch execution. |
| Phase 5 - output standardization | Per-leaf summaries and scenario aggregate metrics. | src/model_v3/scenarios/summarize_outputs.py; experiments/scenario_tree/summaries/realization_level/scenario_leaf_metrics.csv | implemented | none | Regenerate summaries after new successful runs. |
| Phase 6 - comparison framework | Climate-only, technology-only, stress-case, stochastic robustness tables. | config/scenario_tree/comparison_definitions.yaml; experiments/scenario_tree/summaries/comparison_level/comparison_index.csv | implemented | comparison validation reports missing groups where no successful summary rows exist | Regenerate comparisons when more leaves have summaries. |
| Phase 7 - visualisation | Generated figures and figure metadata. | src/model_v3/scenarios/generate_figures.py; figures/scenario_tree/metadata/figure_metadata.yaml | implemented | none | Validate figures and caption metadata before using in thesis text. |
| Phase 8 - documentation/audit | Traceability matrix, audit summary, methodology docs. | reports/scenario_tree_validation_report.md; reports/scenario_tree_audit_summary.yaml; docs/model_v3_scenario_tree_methodology.md | implemented | none | Rerun audit after new outputs or changed figures. |
| Phase 9 - handbook | Generated handbook, briefing, manifest, and handbook validation. | src/model_v3/documentation/build_model_handbook.py; src/model_v3/documentation/validate_model_handbook.py | implemented | none | Run the handbook validator and update this document after regeneration. |


# Chapter 1 - Purpose and thesis context

The thesis goal supported by this repository is to analyse residential energy demand under changing climate conditions and changing household technology assumptions. The model is bottom-up because it starts from dwelling, end-use, weather, technology, and behaviour assumptions rather than only fitting a top-down aggregate annual demand curve. It is stochastic because household/cohort draws and demand-profile variability are represented through reproducible realization seeds. It is physics-informed because the model includes a thermal balance, heat losses, internal gains, solar gains, heating control, carrier conversion, and grid import/export accounting.

Climate uncertainty matters because outdoor temperature and solar forcing affect heating demand, PV generation, and seasonal grid stress. Technology assumptions matter because electrification, heat pumps, PV, and EV charging can change both annual energy carriers and peak electricity demand. A scenario-tree approach is useful because it keeps these dimensions separate: climate branches answer "what forcing was used?", technology branches answer "what equipment/stock assumption was active?", and realization IDs answer "which stochastic draw produced the result?".

Compared with earlier model versions, the v3 repository visible here adds a modular architecture with explicit interface dataclasses in `src/model_v3/interfaces.py`, scenario-tree metadata under `config/scenario_tree/`, reproducible run folders under `experiments/scenario_tree/`, and standardized metrics/comparisons for thesis figures. This handbook distinguishes the general modelling concepts from repository-specific evidence.

Key terms: a bottom-up model builds demand from components; a stochastic model uses controlled random draws; a physics-informed model encodes simplified physical relationships; scenario analysis compares conditional futures; uncertainty propagation follows how input and branch assumptions change output metrics.


# Chapter 2 - High-level model architecture

The main implementation code is under `src/model_v3/`. The core data contract is explicit in `src/model_v3/interfaces.py` and follows this sequence:

`InputDataset -> PreparedForcing -> PhysicsState -> ControlState -> SystemState -> ModelOutputs`

`InputDataset` holds raw or lightly structured model inputs and default fields such as outdoor temperature, setpoint, heat-loss coefficient, thermal mass, airflow rates, DHW demand, appliances, lighting, cooking, EV charging, and PV generation. `PreparedForcing` is the time-aligned forcing bundle ready for physics. `PhysicsState` contains the free-float thermal response, heat losses, internal and solar gains, and heating demand. `ControlState` applies thermostat/deadband/window-opening logic. `SystemState` applies heating/DHW technology conversion, PV netting, grid import/export, comfort, and carrier outputs. `ModelOutputs` is the final public output contract.

The model engine is the data, physics, control, systems, and output code that simulates one configured run. The scenario-tree layer enumerates and manages combinations of climate, technology, and realization IDs. The runner is the operational entrypoint that validates and executes leaves and records provenance. Validators check schemas, configs, summaries, comparisons, figures, and traceability. A manifest records what was generated and from which sources. Summary tables are standardized CSV outputs used for comparisons and figures.

The configuration layer is under `config/`. The input layer includes `inputs/climate/processed/`, `inputs/building/`, weather, solar, load-profile, occupancy, and end-use files where present. Climate preprocessing exists under `src/climate/` and the processed climate products are consumed by scenario leaves. Technology assumptions are encoded both qualitatively in `technology_cases.yaml` and concretely through `config/belgian_technology_inputs.yaml`. Stochastic realization policy is encoded in `realization_policy.yaml`; cohort generation is handled by the model engine and stochastic/cohort modules rather than by the scenario-tree schema alone.

The runner/orchestration layer is implemented under `src/model_v3/scenarios/` and `src/model_v3/scenario_tree/`. The output standardization layer is implemented by `src/model_v3/scenarios/summarize_outputs.py`, `summary_contract.py`, and `output_reader.py`. The comparison layer is `generate_comparisons.py` plus `comparison_definitions.yaml`. The figure/documentation layer includes `generate_figures.py`, figure metadata, and this Phase 9 handbook generator.


# Chapter 3 - Model physics and simulation logic

The implemented physics layer includes a simplified lumped-zone thermal representation. `src/model_v3/physics/thermal_dynamics.py` integrates a single indoor temperature state with envelope loss, airflow loss, capacitance, internal gains, solar gains, and optional heating over a timestep. `src/model_v3/physics/physics_core.py` computes infiltration/ventilation flows, airflow heat loss, passive balance, free-float temperature, and heating demand needed to reach setpoint.

Outdoor temperature forcing appears as `T_outdoor_C`/`T_out_C` fields and climate forcing CSV columns. Heat loss through the envelope is represented by `heat_loss_coefficient_W_per_C`, a UA-like term in watts per degree C. Ventilation and infiltration losses use air changes per hour (`ACH_inf`, `ACH_vent_base`, `ACH_vent_occupied`), dwelling volume, air density, heat capacity, and heat recovery when ventilation type is balanced. Internal gains are represented by occupant, appliance, lighting, and cooking heat gain fields. Solar gains are represented by orientation-specific solar inputs and `Q_solar_gains_W`.

Heating demand is computed as useful thermal demand in watts before conversion to carriers. DHW demand appears as `Q_dhw_demand_W` and standardized `annual_dhw_kWh`. Electricity demand includes appliances, lighting, cooking, space-heating technology electricity, DHW technology electricity, and EV charging. Heat pump/COP conversion and other carrier conversions are handled in `src/model_v3/systems/technology.py`. Gas use is represented through carrier-specific power columns such as `P_gas_space_heating_W` and `P_gas_dhw_W`. PV generation and grid import/export accounting are handled in the systems layer through `P_pv_generation_W`, gross electricity, net grid power, grid import, and grid export.

Peak demand is standardized as `peak_grid_import_W`, `winter_peak_grid_import_W`, and `summer_peak_grid_import_W`. The peak values depend on raw output timestep resolution and the seasonal definitions used by `output_reader.py`: winter is December, January, February; summer is June, July, August.

Physics caveats: the thermal representation is one-zone and simplified; aggregation hides individual dwelling diversity; spatial grid constraints are not represented; UA and thermal mass can be uncertain; occupant behaviour strongly affects loads and internal gains; COP modelling may be simplified; PV self-consumption and EV charging depend on temporal matching; and external calibration against measured Belgian household data must be cited only where validation reports actually prove it.


# Chapter 4 - Inputs and data sources

The handbook generator inspected relevant inputs, configs, reports, summaries, and figure metadata. The input inventory in Appendix A and Appendix D marks missing files explicitly. For each input source, the generated inventory records path, type, purpose, detectable temporal-resolution hints, units if detectable from column names, scenario dimension affected, required/optional status, and validation status.

## Climate inputs

Processed climate forcing files are expected under `inputs/climate/processed/`. The scenario-tree config defines baseline/historical and future RCP branches in `config/scenario_tree/climate_windows.yaml`. Temperature and solar columns are detected by summary code when climate metrics are computed. The required climate metrics are mean temperature, winter mean temperature, summer mean temperature, HDD_15, HDD_18, CDD_22, and mean_solar_W_m2.

| climate window | canonical start | canonical end | source-file window | type | allowed pathways |
| --- | --- | --- | --- | --- | --- |
| baseline_1981_2005 | 1981-01-01 | 2005-12-31 | 1981-2005 | baseline | historical |
| near_future_2030_2049 | 2030-01-01 | 2049-12-31 | 2030-2050 | future | rcp_2_6, rcp_4_5, rcp_8_5 |
| mid_century_2050_2070 | 2050-01-01 | 2070-12-31 | 2050-2070 | future | rcp_2_6, rcp_4_5, rcp_8_5 |
| long_term_2080_2100 | 2080-01-01 | 2100-12-31 | 2080-2100 | future | rcp_2_6, rcp_4_5, rcp_8_5 |

2050 policy: raw processed source files may overlap in 2050, but canonical analysis windows do not. Near-future ends on 2049-12-31. Mid-century starts on 2050-01-01. Therefore 2050 is assigned only to the mid-century canonical analysis window. This policy is encoded in `config/scenario_tree/climate_windows.yaml`.

## Building and archetype inputs

Building and archetype inputs were searched under `inputs/building/` and `config/archetypes.yaml`. The model interface includes floor/volume-like parameters, heat-loss coefficient, thermal mass, ventilation/infiltration, setpoints, glazing/orientation, occupant gains, and heat-gain fractions. Where these values are missing or simplified, the model falls back to configured defaults in the data/interface layer; such defaults should be treated as assumptions, not measurements.

## Technology inputs

Technology cases are defined in `config/scenario_tree/technology_cases.yaml`. `tech_current_stock` is baseline-only unless metadata says otherwise. Future climate-only comparisons use `tech_frozen_stock`, not future `tech_current_stock`. The Belgian technology input YAML is `config/belgian_technology_inputs.yaml`.

| technology case | label | applicable windows | interpretation |
| --- | --- | --- | --- |
| tech_current_stock | Current stock | baseline | Preserve the current-stock technology representation already used by model_v3 for baseline residential demand.<br> |
| tech_frozen_stock | Frozen stock | future | Apply future climate forcing while preserving the baseline technology stock as a climate-only sensitivity case.<br> |
| tech_moderate_electrification | Moderate electrification | future | Represent a future residential stock with heat-pump uptake and some building-envelope improvement. Numerical shares are counterfactual scenario assumptions, not observed future adoption rates.<br> |
| tech_high_electrification_pv_ev | High electrification with PV and EV | future | Represent a strongly electrified future stock including heat pumps, PV, EV charging demand, and building-envelope improvement. Numerical shares are stress-case scenario assumptions, not observed future adoption rates.<br> |

## Stochastic inputs

Realizations are `seed_0000` through `seed_0099` according to `config/scenario_tree/realization_policy.yaml`. The seed controls reproducible stochastic sampling in the model engine. Scenario uncertainty is represented by climate and technology branches; stochastic variability is represented by realization IDs and cohort draws. The policy file states that cohorts were not generated in the scenario-tree metadata phase itself.


# Chapter 5 - Scenario-tree design

A scenario is the deterministic parent combination of `climate_window_id`, `climate_pathway_id`, and `technology_case_id`. A realization is a stochastic seed/cohort instance. A scenario leaf is one scenario plus one realization. Stable identifiers are required because they are the join key between configs, runs, logs, registry rows, summaries, comparison tables, figures, and thesis text.

Canonical ID format:

```text
{climate_window_id}__{climate_pathway_id}__{technology_case_id}__{realization_id}
```

Examples:

```text
baseline_1981_2005__historical__tech_current_stock__seed_0042

mid_century_2050_2070__rcp_8_5__tech_high_electrification_pv_ev__seed_0042
```

Double underscores separate scenario dimensions. Individual dimension names use lowercase tokens and single underscores. Accepted abbreviations such as RCP, PV, and EV are encoded in lowercase IDs.

The scenario tree answers four traceability questions: which climate forcing was used, which technology assumptions were active, which stochastic seed/cohort generated the result, and which exact model/config produced the output. The scenario-tree schema is encoded in `config/scenario_tree/scenario_tree_schema.yaml`.


# Chapter 6 - Directory structure and experiment space

The physical experiment space is rooted at `experiments/scenario_tree/`.

```text
experiments/scenario_tree/
  manifests/
  configs/
  runs/
  summaries/
  logs/
```

`manifests/` stores the scenario-tree manifest, scenario leaf index, run registry, registry summary, and validation reports. `configs/` stores scenario-level seed placeholders and links to leaf-level run configs. `runs/` stores one folder per scenario leaf with `run_config.yaml`, `inputs_manifest.yaml`, `outputs/`, and `logs/`. `outputs/` contains raw model outputs and standardized leaf summaries for successful runs. `logs/` contains per-attempt runner logs. `summaries/` stores realization-level metrics, scenario-level aggregate metrics, and comparison-level tables.

A scenario-level config folder groups seeds under a deterministic scenario ID. A scenario-leaf run folder is the executable unit. The run config holds the exact leaf configuration. The input manifest records resolved input files. The scenario leaf index is the inventory of planned leaves. The run registry is the ledger of attempts and statuses.


# Chapter 7 - Configuration generation

Abstract scenario leaves become executable run configs through `src/model_v3/scenario_tree/generate_leaf_configs.py`. The generated `run_config.yaml` records scenario dimensions, climate forcing path, canonical analysis dates, source file window, technology case, Belgian technology input reference, stochastic seed and cohort size, output directory, model options, validation metadata, and provenance. The paired `inputs_manifest.yaml` records resolved input files and existence checks.

Representative config excerpt:

```yaml
schema_version: model_v3.scenario_leaf_config.v1
generated_by: Phase 3 - scenario leaf config generator
status: configured_not_run
scenario_leaf:
  id: baseline_1981_2005__historical__tech_current_stock__seed_0000
  scenario_id: baseline_1981_2005__historical__tech_current_stock
  climate_window_id: baseline_1981_2005
  climate_pathway_id: historical
  technology_case_id: tech_current_stock
  realization_id: seed_0000
climate:
  window_id: baseline_1981_2005
  window_label: baseline 1981-2005
  pathway_id: historical
  forcing_file: inputs/climate/processed/baseline/weather_baseline_historical_cnrm_cerfacs_cm5_cnrm_aladin63_r1i1p1.csv
  source_file_window: 1981-2005
  analysis_start: '1981-01-01'
  analysis_end: '2005-12-31'
  inclusive_dates: true
  temporal_policy:
    raw_processed_files_may_overlap: true
    canonical_analysis_windows_must_overlap: false
    year_2050_assignment: mid_century_2050_2070
technology:
  case_id: tech_current_stock
  metadata_file: config/scenario_tree/technology_cases.yaml
  belgian_technology_inputs: config/belgian_technology_inputs.yaml
stochastic:
  realization_id: seed_0000
  seed_index: 0
  seed_value: 0
  cohort_size: 100
  cohort_generation: deferred_to_simulation_phase
model_options:
  run_mode: scenario_leaf
  runner_mode: stock_weighted_archetypes
  execute_simulation: false
  use_stochastic_cohort: false
  use_stock_weighted_archetypes: true
  use_climate_forcing: true
  use_technology_case: true
  write_outputs: true
  runner_mode_note: Baseline/current-stock and future frozen-stock leaves use stock_weighted_archetypes
    so annual heating magnitudes are averaged over Belgian archetype stock weights.
    Other technology-stress leaves remain deterministic annual leaves unless promoted
    to stochastic_cohort by a dedicated output runner.
output:
  run_dir: experiments/scenario_tree/runs/baseline_1981_2005__historical__tech_current_stock__seed_0000
  outputs_dir: experiments/scenario_tree/runs/baseline_1981_2005__historical__tech_current_stock__seed_0000/outputs
  logs_dir: experiments/scenario_tree/runs/baseline_1981_2005__historical__tech_current_stock__seed_0000/logs
validation:
  config_complete: true
  missing_required_inputs: []
provenance:
  phase: 3
  scenario_tree_schema: config/scenario_tree/scenario_tree_schema.yaml
  climate_windows: config/scenario_tree/climate_windows.yaml
  technology_cases: config/scenario_tree/technology_cases.yaml
  realization_policy: config/scenario_tree/realization_policy.yaml
  scenario_leaf_index: experiments/scenario_tree/manifests/scenario_leaf_index.csv
  generated_at_utc: '2026-05-28T14:27:49+00:00'
```

Config validation checks required fields, climate file existence, technology case existence, Belgian technology input existence, baseline/future separation, canonical date windows, and the 2050 policy. The latest detected config validation report is `experiments/scenario_tree/manifests/config_validation_report.md` if present.


# Chapter 8 - Running the model

Use `python3` from the repository root.

```bash
python3 -m model_v3.scenarios.run_scenario_tree --dry-run --print-summary
```

```bash
python3 -m model_v3.scenarios.run_scenario_tree \
  --scenario-leaf-id baseline_1981_2005__historical__tech_current_stock__seed_0000 \
  --print-summary
```

```bash
python3 -m model_v3.scenarios.run_scenario_tree \
  --scenario-leaf-id mid_century_2050_2070__rcp_8_5__tech_high_electrification_pv_ev__seed_0000 \
  --print-summary
```

```bash
python3 -m model_v3.scenarios.run_scenario_tree \
  --all \
  --max-workers 1 \
  --continue-on-error \
  --print-summary
```

Dry-run mode validates and plans without simulation. Single-leaf mode executes one leaf. Batch mode iterates through selected leaves. `--max-workers 1` is used first because serial execution is easier to audit and avoids concurrent provenance/logging issues. Failed runs are recorded in the registry and logs. `--force` is used when rerunning a successful leaf intentionally. Logs are stored under each leaf run folder.

Run provenance includes timestamp, git commit when available, dirty working tree status when available, config hash, input hashes, random seed, model version, output path, and status. In this repository snapshot, git provenance is unavailable if the repository root is not a Git working tree.


# Chapter 9 - Outputs and standardized metrics

Raw model outputs for a successful leaf are stored under that leaf's `outputs/` directory, typically including `annual_profile.csv` and `annual_summary.json`. The standardization layer writes per-leaf standardized summaries and scenario-level aggregate metrics.

Detected realization-level summary rows: 37.

Detected scenario-level aggregate rows: 10.

The required standardized metrics are:

| metric | unit | category | definition | source | aggregation | caveats |
| --- | --- | --- | --- | --- | --- | --- |
| annual_electricity_gross_kWh | kWh | energy | Gross annual electricity demand before netting PV generation. | annual_summary.json or annual_profile.csv via src/model_v3/scenarios/output_reader.py | numeric_distribution | Interpret with scenario, technology, and run coverage context. |
| annual_grid_import_kWh | kWh | grid_energy | Annual electricity imported from the grid. | annual_summary.json or annual_profile.csv via src/model_v3/scenarios/output_reader.py | numeric_distribution | Interpret with scenario, technology, and run coverage context. |
| annual_grid_export_kWh | kWh | grid_energy | Annual electricity exported to the grid. | annual_summary.json or annual_profile.csv via src/model_v3/scenarios/output_reader.py | numeric_distribution | Interpret with scenario, technology, and run coverage context. |
| annual_gas_kWh | kWh | fuel | Annual natural-gas final energy consumption. | annual_summary.json or annual_profile.csv via src/model_v3/scenarios/output_reader.py | numeric_distribution | Interpret with scenario, technology, and run coverage context. |
| annual_useful_heating_kWh | kWh | thermal | Useful thermal energy supplied for space heating. | annual_summary.json or annual_profile.csv via src/model_v3/scenarios/output_reader.py | numeric_distribution | Interpret with scenario, technology, and run coverage context. |
| annual_dhw_kWh | kWh | thermal | Useful thermal domestic hot-water demand when available. | annual_summary.json or annual_profile.csv via src/model_v3/scenarios/output_reader.py | numeric_distribution | Interpret with scenario, technology, and run coverage context. |
| peak_grid_import_W | W | grid_power | Maximum grid import power over the model output year. | annual_summary.json or annual_profile.csv via src/model_v3/scenarios/output_reader.py | numeric_distribution | Interpret with scenario, technology, and run coverage context. |
| winter_peak_grid_import_W | W | grid_power | Maximum grid import power in December, January, or February. | annual_summary.json or annual_profile.csv via src/model_v3/scenarios/output_reader.py | numeric_distribution | Interpret with scenario, technology, and run coverage context. |
| summer_peak_grid_import_W | W | grid_power | Maximum grid import power in June, July, or August. | annual_summary.json or annual_profile.csv via src/model_v3/scenarios/output_reader.py | numeric_distribution | Interpret with scenario, technology, and run coverage context. |
| pv_generation_kWh | kWh | distributed_energy | Annual PV generation. | annual_summary.json or annual_profile.csv via src/model_v3/scenarios/output_reader.py | numeric_distribution | Interpret with scenario, technology, and run coverage context. |
| pv_self_consumption_kWh | kWh | distributed_energy | Annual PV generation consumed locally. | annual_summary.json or annual_profile.csv via src/model_v3/scenarios/output_reader.py | numeric_distribution | Interpret with scenario, technology, and run coverage context. |
| pv_export_fraction | fraction | distributed_energy | Grid export divided by PV generation when PV generation is positive. | annual_summary.json or annual_profile.csv via src/model_v3/scenarios/output_reader.py | numeric_distribution | Interpret with scenario, technology, and run coverage context. |
| ev_charging_kWh | kWh | mobility | Annual EV charging electricity. | annual_summary.json or annual_profile.csv via src/model_v3/scenarios/output_reader.py | numeric_distribution | Interpret with scenario, technology, and run coverage context. |
| mean_T_out_C | C | climate | Mean outdoor air temperature over the canonical climate window. | climate forcing file referenced by run config | numeric_distribution | Interpret with scenario, technology, and run coverage context. |
| winter_mean_T_out_C | C | climate | Mean outdoor air temperature in December, January, and February. | climate forcing file referenced by run config | numeric_distribution | Interpret with scenario, technology, and run coverage context. |
| summer_mean_T_out_C | C | climate | Mean outdoor air temperature in June, July, and August. | climate forcing file referenced by run config | numeric_distribution | Interpret with scenario, technology, and run coverage context. |
| HDD_15 | degree_days | climate_degree_days | Heating degree days using a 15 C base and daily mean outdoor temperature. | climate forcing file referenced by run config | numeric_distribution | Interpret with scenario, technology, and run coverage context. |
| HDD_18 | degree_days | climate_degree_days | Heating degree days using an 18 C base and daily mean outdoor temperature. | climate forcing file referenced by run config | numeric_distribution | Interpret with scenario, technology, and run coverage context. |
| CDD_22 | degree_days | climate_degree_days | Cooling degree days using a 22 C base and daily mean outdoor temperature. | climate forcing file referenced by run config | numeric_distribution | Interpret with scenario, technology, and run coverage context. |
| mean_solar_W_m2 | W/m2 | climate_solar | Mean available solar irradiance over the canonical climate window. | climate forcing file referenced by run config | numeric_distribution | Interpret with scenario, technology, and run coverage context. |

Annual sums are energy totals over the model output period and use kWh. Peak power metrics use W. Winter peaks use December, January, and February. Summer peaks use June, July, and August. PV self-consumption is PV generation minus export where values are available and bounded by PV generation. PV export fraction is export divided by PV generation when PV generation is positive. HDD and CDD are degree-day climate metrics computed from daily mean outdoor temperature. W, kW, and kWh must not be mixed: W is instantaneous power, kW is 1000 W, and kWh is energy over time.


# Chapter 10 - Comparison framework

The comparison framework is encoded in `config/scenario_tree/comparison_definitions.yaml`. It consumes standardized Phase 5 summary tables and does not run simulations.

Climate-only effect: the historical current-stock baseline is compared against future RCP pathways under `tech_frozen_stock`, not future `tech_current_stock`. This is necessary because `tech_current_stock` is baseline-only in the scenario metadata. The frozen-stock branch is a counterfactual that varies climate while holding technology assumptions fixed relative to the baseline stock.

Technology-only effect: `tech_frozen_stock`, `tech_moderate_electrification`, and `tech_high_electrification_pv_ev` are compared within the same climate window and RCP pathway. This isolates technology assumptions conditional on climate.

Combined stress case: `baseline_1981_2005__historical__tech_current_stock` is compared with `long_term_2080_2100__rcp_8_5__tech_high_electrification_pv_ev`.

Stochastic robustness: realization-level results are summarized with quantiles such as P10, P50, and P90. These quantiles describe modelled stochastic spread across available successful realizations; they do not represent full climate or epistemic uncertainty.

Delta definitions:

```text
delta_abs = future_value - baseline_value

delta_pct = 100 * (future_value - baseline_value) / baseline_value
```

If the denominator is zero, percentage change is left blank or flagged; absolute delta can still be reported when both values exist.


# Chapter 11 - Validation and quality assurance

Validation layers detected or documented in the repository:

| layer | source | what it checks |
| --- | --- | --- |
| scenario-tree schema | config/scenario_tree/*.yaml | checks IDs, baseline/future rules, 2050 policy |
| naming/path validation | scenario_leaf_index.csv and run folders | checks stable paths and identifiers |
| config validation | config_validation_report.md | checks climate files, technology inputs, required blocks |
| runner dry-run validation | run_scenario_tree --dry-run | plans without executing simulations |
| run registry validation | run_registry.csv | checks statuses and provenance fields |
| output summary validation | summary_validation_report.md | checks standardized metrics and 2050 policy |
| comparison validation | comparison_validation_report.md | checks comparison definitions and available outputs |
| figure validation | validate_figures | checks metadata, stable figure filenames, captions, and sources |
| traceability audit | reports/scenario_tree_traceability_matrix.csv | joins registry, configs, inputs, summaries, hashes |

Validation commands:

```bash
python3 -m model_v3.scenarios.validate_summaries \
  --experiment-root experiments/scenario_tree \
  --print-summary
```

```bash
python3 -m model_v3.scenarios.validate_comparisons \
  --experiment-root experiments/scenario_tree \
  --comparison-definitions config/scenario_tree/comparison_definitions.yaml \
  --print-summary
```

```bash
python3 -m model_v3.scenarios.validate_figures \
  --figures-root figures/scenario_tree \
  --experiment-root experiments/scenario_tree \
  --print-summary
```

Internal consistency validation checks whether files, IDs, metrics, and reports agree. Input validation checks that referenced input files exist and are resolvable. Output validation checks standardized summary structure and metric availability. External empirical validation compares model outputs against independent measured data. Do not claim external validation from scenario-tree consistency reports alone.


# Chapter 12 - Figures and interpretation guide

The detected figure metadata rows are 24. The handbook includes generated schematic figures and existing generated scenario-tree figures where available. Existing figure metadata records source files, metrics, filters, generation scripts, row counts, and warnings. If a data-derived figure is missing, the handbook uses a schematic and states what source would be needed.

## Figures included in this handbook

### Figure 1: Overall model architecture diagram.

![Overall model architecture diagram.](docs/model_v3_handbook_assets/model_architecture.png)

Caption: Overall model architecture diagram.

Explanation: Shows the major layers from configs and inputs through data preparation, physics, control, systems, outputs, scenario-tree orchestration, comparisons, figures, and documentation.

Source data or config: `Schematic generated by build_model_handbook.py from repository module layout.`.

Metrics used: not_applicable.

Figure type: schematic.

### Figure 2: Data-flow diagram from inputs to outputs.

![Data-flow diagram from inputs to outputs.](docs/model_v3_handbook_assets/data_flow_inputs_to_outputs.png)

Caption: Data-flow diagram from inputs to outputs.

Explanation: Shows how file-backed inputs become prepared forcing, physical states, system states, raw outputs, standardized summaries, comparisons, and figures.

Source data or config: `Schematic generated from src/model_v3/data, physics, control, systems, output, and scenarios modules.`.

Metrics used: not_applicable.

Figure type: schematic.

### Figure 3: Climate-window timeline showing the 2050 overlap policy.

![Climate-window timeline showing the 2050 overlap policy.](docs/model_v3_handbook_assets/climate_window_timeline_2050_policy.png)

Caption: Climate-window timeline showing the 2050 overlap policy.

Explanation: Shows that source files may overlap in 2050 while canonical analysis windows do not: near-future ends in 2049 and mid-century starts in 2050.

Source data or config: `config/scenario_tree/climate_windows.yaml`.

Metrics used: not_applicable.

Figure type: schematic.

### Figure 4: Scenario leaf ID decomposition diagram.

![Scenario leaf ID decomposition diagram.](docs/model_v3_handbook_assets/scenario_leaf_id_decomposition.png)

Caption: Scenario leaf ID decomposition diagram.

Explanation: Explains the four fields of a scenario leaf ID and why double underscores are reserved as dimension separators.

Source data or config: `config/scenario_tree/scenario_tree_schema.yaml`.

Metrics used: not_applicable.

Figure type: schematic.

### Figure 5: Runner and provenance workflow.

![Runner and provenance workflow.](docs/model_v3_handbook_assets/runner_provenance_workflow.png)

Caption: Runner and provenance workflow.

Explanation: Shows how generated configs are executed by the runner and recorded in registry, logs, hashes, and output paths.

Source data or config: `src/model_v3/scenarios/run_scenario_tree.py and experiments/scenario_tree/manifests/run_registry.csv`.

Metrics used: not_applicable.

Figure type: schematic.

### Figure 6: Output standardization workflow.

![Output standardization workflow.](docs/model_v3_handbook_assets/output_standardization_workflow.png)

Caption: Output standardization workflow.

Explanation: Shows how raw annual outputs are mapped into required energy, grid, PV/EV, and climate metrics before aggregation.

Source data or config: `src/model_v3/scenarios/summarize_outputs.py and src/model_v3/scenarios/output_reader.py`.

Metrics used: not_applicable.

Figure type: schematic.

### Figure 7: Comparison framework diagram.

![Comparison framework diagram.](docs/model_v3_handbook_assets/comparison_framework.png)

Caption: Comparison framework diagram.

Explanation: Summarizes climate-only, technology-only, combined stress-case, and stochastic robustness comparisons.

Source data or config: `config/scenario_tree/comparison_definitions.yaml`.

Metrics used: not_applicable.

Figure type: schematic.

### Figure 8: Input data inventory chart.

![Input data inventory chart.](docs/model_v3_handbook_assets/input_data_inventory.png)

Caption: Input data inventory chart.

Explanation: Counts detected input, config, summary, and metadata files by file type.

Source data or config: `Repository file inventory under inputs/, config/, and scenario-tree summaries.`.

Metrics used: not_applicable.

Figure type: schematic.

### Figure 9: Metric taxonomy diagram.

![Metric taxonomy diagram.](docs/model_v3_handbook_assets/metric_taxonomy.png)

Caption: Metric taxonomy diagram.

Explanation: Groups standardized metrics into energy totals, grid stress, technology metrics, climate metrics, comparisons, and uncertainty summaries.

Source data or config: `experiments/scenario_tree/summaries/realization_level/scenario_leaf_metrics_schema.yaml`.

Metrics used: not_applicable.

Figure type: schematic.

### Figure 10: Caveats and gaps overview heatmap.

![Caveats and gaps overview heatmap.](docs/model_v3_handbook_assets/caveats_and_gaps_overview.png)

Caption: Caveats and gaps overview heatmap.

Explanation: Gives a schematic severity overview of the main caveat families discussed in Chapter 13.

Source data or config: `Schematic generated from the caveat table in the handbook.`.

Metrics used: not_applicable.

Figure type: schematic.

### Figure 11: Scenario-tree structure diagram.

![Scenario-tree structure diagram.](docs/model_v3_handbook_assets/scenario_tree_structure.png)

Caption: Scenario-tree structure diagram.

Explanation: Data-derived structure figure from the scenario-tree figure workflow.

Source data or config: `figures/scenario_tree/structure/scenario_tree_structure.png`.

Metrics used: scenario_tree_dimensions.

Figure type: data-derived or copied generated figure.

### Figure 12: Temperature by climate window and pathway

![Temperature by climate window and pathway](docs/model_v3_handbook_assets/existing_climate_temperature_by_window_rcp.png)

Caption: Temperature by climate window and pathway

Explanation: Existing generated scenario-tree figure copied into the handbook asset directory for stable inclusion.

Source data or config: `experiments/scenario_tree/summaries/scenario_level/scenario_aggregate_metrics.csv;experiments/scenario_tree/summaries/realization_level/scenario_leaf_metrics.csv`.

Metrics used: mean_T_out_C;winter_mean_T_out_C;summer_mean_T_out_C.

Figure type: data-derived or copied generated figure.

### Figure 13: Heating and cooling degree days by climate window and pathway

![Heating and cooling degree days by climate window and pathway](docs/model_v3_handbook_assets/existing_climate_hdd_cdd_by_window_rcp.png)

Caption: Heating and cooling degree days by climate window and pathway

Explanation: Existing generated scenario-tree figure copied into the handbook asset directory for stable inclusion.

Source data or config: `experiments/scenario_tree/summaries/scenario_level/scenario_aggregate_metrics.csv;experiments/scenario_tree/summaries/realization_level/scenario_leaf_metrics.csv`.

Metrics used: HDD_15;HDD_18;CDD_22.

Figure type: data-derived or copied generated figure.

### Figure 14: Solar forcing by climate window and pathway

![Solar forcing by climate window and pathway](docs/model_v3_handbook_assets/existing_climate_solar_by_window_rcp.png)

Caption: Solar forcing by climate window and pathway

Explanation: Existing generated scenario-tree figure copied into the handbook asset directory for stable inclusion.

Source data or config: `experiments/scenario_tree/summaries/scenario_level/scenario_aggregate_metrics.csv;experiments/scenario_tree/summaries/realization_level/scenario_leaf_metrics.csv`.

Metrics used: mean_solar_W_m2.

Figure type: data-derived or copied generated figure.

### Figure 15: Annual HDD and CDD by Phase 1 climate scenario

![Annual HDD and CDD by Phase 1 climate scenario](docs/model_v3_handbook_assets/existing_phase1_hdd_cdd_dot_interval.png)

Caption: Annual HDD and CDD by Phase 1 climate scenario

Explanation: Existing generated scenario-tree figure copied into the handbook asset directory for stable inclusion.

Source data or config: `experiments/scenario_tree/summaries/comparison_level/annual_climate_degree_day_comparison.csv`.

Metrics used: HDD_18;CDD_22.

Figure type: data-derived or copied generated figure.

### Figure 16: HDD percentage change versus historical baseline

![HDD percentage change versus historical baseline](docs/model_v3_handbook_assets/existing_phase1_hdd_pct_decrease_heatmap.png)

Caption: HDD percentage change versus historical baseline

Explanation: Existing generated scenario-tree figure copied into the handbook asset directory for stable inclusion.

Source data or config: `experiments/scenario_tree/summaries/comparison_level/annual_climate_degree_day_comparison.csv`.

Metrics used: delta_HDD_18_pct.

Figure type: data-derived or copied generated figure.

### Figure 17: CDD absolute change versus historical baseline

![CDD absolute change versus historical baseline](docs/model_v3_handbook_assets/existing_phase1_cdd_abs_increase_heatmap.png)

Caption: CDD absolute change versus historical baseline

Explanation: Existing generated scenario-tree figure copied into the handbook asset directory for stable inclusion.

Source data or config: `experiments/scenario_tree/summaries/comparison_level/annual_climate_degree_day_comparison.csv`.

Metrics used: delta_CDD_22_abs.

Figure type: data-derived or copied generated figure.

### Figure 18: Monthly demand timing by climate scenario

![Monthly demand timing by climate scenario](docs/model_v3_handbook_assets/existing_output2_monthly_demand_stacked.png)

Caption: Monthly demand timing by climate scenario

Explanation: Existing generated scenario-tree figure copied into the handbook asset directory for stable inclusion.

Source data or config: `experiments/scenario_tree/summaries/comparison_level/monthly_demand_shift_comparison.csv;experiments/scenario_tree/summaries/comparison_level/seasonal_demand_shift_comparison.csv`.

Metrics used: monthly_space_heating_useful_kWh;monthly_electricity_gross_kWh;monthly_gas_kWh.

Figure type: data-derived or copied generated figure.

### Figure 19: Seasonal useful heating demand shift

![Seasonal useful heating demand shift](docs/model_v3_handbook_assets/existing_output2_seasonal_heating_shift.png)

Caption: Seasonal useful heating demand shift

Explanation: Existing generated scenario-tree figure copied into the handbook asset directory for stable inclusion.

Source data or config: `experiments/scenario_tree/summaries/comparison_level/monthly_demand_shift_comparison.csv;experiments/scenario_tree/summaries/comparison_level/seasonal_demand_shift_comparison.csv`.

Metrics used: seasonal_space_heating_useful_kWh.

Figure type: data-derived or copied generated figure.

### Figure 20: Monthly cooling-pressure indicators

![Monthly cooling-pressure indicators](docs/model_v3_handbook_assets/existing_output2_monthly_cooling_pressure.png)

Caption: Monthly cooling-pressure indicators

Explanation: Existing generated scenario-tree figure copied into the handbook asset directory for stable inclusion.

Source data or config: `experiments/scenario_tree/summaries/comparison_level/monthly_demand_shift_comparison.csv;experiments/scenario_tree/summaries/comparison_level/seasonal_demand_shift_comparison.csv`.

Metrics used: monthly_CDD_22;monthly_overheating_hours;monthly_indoor_temperature_exceedance_degree_hours.

Figure type: data-derived or copied generated figure.

### Figure 21: Seasonal share of annual useful heating demand

![Seasonal share of annual useful heating demand](docs/model_v3_handbook_assets/existing_output2_seasonal_heating_share.png)

Caption: Seasonal share of annual useful heating demand

Explanation: Existing generated scenario-tree figure copied into the handbook asset directory for stable inclusion.

Source data or config: `experiments/scenario_tree/summaries/comparison_level/monthly_demand_shift_comparison.csv;experiments/scenario_tree/summaries/comparison_level/seasonal_demand_shift_comparison.csv`.

Metrics used: seasonal_heating_share_pct.

Figure type: data-derived or copied generated figure.

### Figure 22: Annual electricity demand by scenario

![Annual electricity demand by scenario](docs/model_v3_handbook_assets/existing_annual_electricity_by_scenario.png)

Caption: Annual electricity demand by scenario

Explanation: Existing generated scenario-tree figure copied into the handbook asset directory for stable inclusion.

Source data or config: `experiments/scenario_tree/summaries/scenario_level/scenario_aggregate_metrics.csv`.

Metrics used: annual_electricity_gross_kWh;annual_grid_import_kWh.

Figure type: data-derived or copied generated figure.

### Figure 23: Annual gas demand by scenario

![Annual gas demand by scenario](docs/model_v3_handbook_assets/existing_annual_gas_by_scenario.png)

Caption: Annual gas demand by scenario

Explanation: Existing generated scenario-tree figure copied into the handbook asset directory for stable inclusion.

Source data or config: `experiments/scenario_tree/summaries/scenario_level/scenario_aggregate_metrics.csv`.

Metrics used: annual_gas_kWh.

Figure type: data-derived or copied generated figure.

### Figure 24: Useful heating and domestic hot-water demand by scenario

![Useful heating and domestic hot-water demand by scenario](docs/model_v3_handbook_assets/existing_annual_heat_dhw_by_scenario.png)

Caption: Useful heating and domestic hot-water demand by scenario

Explanation: Existing generated scenario-tree figure copied into the handbook asset directory for stable inclusion.

Source data or config: `experiments/scenario_tree/summaries/scenario_level/scenario_aggregate_metrics.csv`.

Metrics used: annual_useful_heating_kWh;annual_dhw_kWh.

Figure type: data-derived or copied generated figure.

### Figure 25: Peak grid import by scenario

![Peak grid import by scenario](docs/model_v3_handbook_assets/existing_peak_grid_import_by_scenario.png)

Caption: Peak grid import by scenario

Explanation: Existing generated scenario-tree figure copied into the handbook asset directory for stable inclusion.

Source data or config: `experiments/scenario_tree/summaries/scenario_level/scenario_aggregate_metrics.csv`.

Metrics used: peak_grid_import_W;winter_peak_grid_import_W;summer_peak_grid_import_W.

Figure type: data-derived or copied generated figure.

### Figure 26: Annual grid import and export by scenario

![Annual grid import and export by scenario](docs/model_v3_handbook_assets/existing_grid_import_export_by_scenario.png)

Caption: Annual grid import and export by scenario

Explanation: Existing generated scenario-tree figure copied into the handbook asset directory for stable inclusion.

Source data or config: `experiments/scenario_tree/summaries/scenario_level/scenario_aggregate_metrics.csv`.

Metrics used: annual_grid_import_kWh;annual_grid_export_kWh.

Figure type: data-derived or copied generated figure.

### Figure 27: PV generation, self-consumption, and export by scenario

![PV generation, self-consumption, and export by scenario](docs/model_v3_handbook_assets/existing_pv_self_consumption_export_by_scenario.png)

Caption: PV generation, self-consumption, and export by scenario

Explanation: Existing generated scenario-tree figure copied into the handbook asset directory for stable inclusion.

Source data or config: `experiments/scenario_tree/summaries/scenario_level/scenario_aggregate_metrics.csv`.

Metrics used: pv_generation_kWh;pv_self_consumption_kWh;pv_export_fraction.

Figure type: data-derived or copied generated figure.

### Figure 28: EV charging demand by scenario

![EV charging demand by scenario](docs/model_v3_handbook_assets/existing_ev_charging_by_scenario.png)

Caption: EV charging demand by scenario

Explanation: Existing generated scenario-tree figure copied into the handbook asset directory for stable inclusion.

Source data or config: `experiments/scenario_tree/summaries/scenario_level/scenario_aggregate_metrics.csv`.

Metrics used: ev_charging_kWh.

Figure type: data-derived or copied generated figure.

### Figure 29: Stochastic uncertainty band for annual grid import

![Stochastic uncertainty band for annual grid import](docs/model_v3_handbook_assets/existing_uncertainty_band_grid_import.png)

Caption: Stochastic uncertainty band for annual grid import

Explanation: Existing generated scenario-tree figure copied into the handbook asset directory for stable inclusion.

Source data or config: `experiments/scenario_tree/summaries/comparison_level/stochastic_robustness/stochastic_uncertainty_bands.csv`.

Metrics used: annual_grid_import_kWh.

Figure type: data-derived or copied generated figure.

### Figure 30: Stochastic uncertainty band for peak grid import

![Stochastic uncertainty band for peak grid import](docs/model_v3_handbook_assets/existing_uncertainty_band_peak_import.png)

Caption: Stochastic uncertainty band for peak grid import

Explanation: Existing generated scenario-tree figure copied into the handbook asset directory for stable inclusion.

Source data or config: `experiments/scenario_tree/summaries/comparison_level/stochastic_robustness/stochastic_uncertainty_bands.csv`.

Metrics used: peak_grid_import_W.

Figure type: data-derived or copied generated figure.

### Figure 31: Stochastic uncertainty band for useful heating demand

![Stochastic uncertainty band for useful heating demand](docs/model_v3_handbook_assets/existing_uncertainty_band_useful_heating.png)

Caption: Stochastic uncertainty band for useful heating demand

Explanation: Existing generated scenario-tree figure copied into the handbook asset directory for stable inclusion.

Source data or config: `experiments/scenario_tree/summaries/comparison_level/stochastic_robustness/stochastic_uncertainty_bands.csv`.

Metrics used: annual_useful_heating_kWh.

Figure type: data-derived or copied generated figure.

### Figure 32: Winter peak grid import versus electrification level

![Winter peak grid import versus electrification level](docs/model_v3_handbook_assets/existing_winter_peak_vs_electrification.png)

Caption: Winter peak grid import versus electrification level

Explanation: Existing generated scenario-tree figure copied into the handbook asset directory for stable inclusion.

Source data or config: `experiments/scenario_tree/summaries/scenario_level/scenario_aggregate_metrics.csv`.

Metrics used: winter_peak_grid_import_W.

Figure type: data-derived or copied generated figure.

### Figure 33: Summer peak emergence relative to winter peak

![Summer peak emergence relative to winter peak](docs/model_v3_handbook_assets/existing_summer_peak_emergence.png)

Caption: Summer peak emergence relative to winter peak

Explanation: Existing generated scenario-tree figure copied into the handbook asset directory for stable inclusion.

Source data or config: `experiments/scenario_tree/summaries/scenario_level/scenario_aggregate_metrics.csv`.

Metrics used: summer_peak_grid_import_W;winter_peak_grid_import_W.

Figure type: data-derived or copied generated figure.

### Figure 34: Baseline versus combined stress-case grid peak

![Baseline versus combined stress-case grid peak](docs/model_v3_handbook_assets/existing_combined_stress_case_grid_peak.png)

Caption: Baseline versus combined stress-case grid peak

Explanation: Existing generated scenario-tree figure copied into the handbook asset directory for stable inclusion.

Source data or config: `experiments/scenario_tree/summaries/scenario_level/scenario_aggregate_metrics.csv;experiments/scenario_tree/summaries/comparison_level/combined_stress_case/combined_stress_case_absolute_metrics.csv`.

Metrics used: peak_grid_import_W;winter_peak_grid_import_W;summer_peak_grid_import_W.

Figure type: data-derived or copied generated figure.

Interpretation rule: a figure can show only the data available in its source tables. If most scenario leaves have not produced successful summaries, annual demand, grid impact, uncertainty band, and stress-case figures should be interpreted as available-output diagnostics rather than complete scenario-tree results.


# Chapter 13 - Caveats, gaps, and limitations

This chapter is critical for thesis defensibility. The table distinguishes what the repository can support from what still needs calibration, execution, or validation.

| caveat/gap ID | topic | current limitation | why it matters | severity | affected outputs | how to detect it | suggested workaround | suggested long-term fix | priority |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| CAV-ORCH-001 | Scenario-tree execution | The scenario tree can be fully configured before all leaves have run. | Configured leaves are not results. | high | all scenario summaries and comparisons | Compare scenario_leaf_index.csv with run_registry.csv and scenario_leaf_metrics.csv. | State execution coverage explicitly. | Run planned leaves after dry-run validation. | 1 |
| CAV-ORCH-002 | Dry-run | Dry-run success is not simulation success. | A valid plan may still fail in physics, data loading, or output writing. | medium | run status and registry | Inspect run_registry.csv for latest actual statuses. | Use dry-run only as preflight. | Add CI smoke execution for representative leaves. | 2 |
| CAV-ORCH-003 | Parallelism | The documented runner starts with max-workers 1; broader batch parallelism is deferred or constrained. | Serial execution is easier to audit but slower. | medium | runtime, batch operation | Read runner help and Phase 4 docs. | Run one baseline and one future leaf first. | Add controlled parallel worker implementation with isolated logs. | 3 |
| CAV-CLIM-001 | Climate ensemble | Detected processed climate files appear to use one named model/RCM chain in filenames unless more files are added. | A single chain does not span full climate-model uncertainty. | high | climate metrics and deltas | Inventory inputs/climate/processed. | Describe RCP branches as conditional projections. | Add multiple CORDEX or comparable model chains. | 1 |
| CAV-CLIM-002 | 2050 overlap | Raw processed files may overlap in 2050; canonical windows handle this by assigning 2050 only to mid-century. | Double-counting would bias cross-window summaries. | high | climate metrics, figures, comparisons | Run summary/comparison/figure validation and inspect climate_windows.yaml. | Always cite the canonical window policy. | Add automated tests for every climate metric path. | 1 |
| CAV-CLIM-003 | RCP interpretation | RCP pathways are climate projection branches, not predictions or probabilities. | Thesis wording can overstate forecast certainty. | medium | all future comparisons | Review thesis text for forecast language. | Use scenario/counterfactual wording. | Add scenario weighting only with literature justification. | 2 |
| CAV-PHYS-001 | One-zone physics | The thermal model is a simplified one-zone/lumped representation. | Room-level dynamics and building heterogeneity are not resolved. | high | heating demand, comfort, peaks | Inspect src/model_v3/physics/thermal_dynamics.py. | Frame as bottom-up archetype modelling. | Add multi-zone or calibrated archetype variants. | 2 |
| CAV-PHYS-002 | UA and thermal mass | Envelope heat loss and thermal mass values are uncertain and archetype-dependent. | Heating demand and peak response are sensitive to these parameters. | high | useful heating, peak import | Inspect building inputs and sensitivity tests. | Report assumptions and avoid overprecision. | Run sensitivity analysis on UA and mass. | 1 |
| CAV-PHYS-003 | Ventilation and infiltration | Ventilation/infiltration assumptions are simplified and behaviour-dependent. | Air exchange can strongly affect heat loss. | medium | space heating, indoor temperature | Inspect airflow archetype inputs and physics/control modules. | State ventilation convention clearly. | Calibrate against measured or audited building data. | 2 |
| CAV-TECH-001 | Technology cases | Technology cases are modelling assumptions and not forecasts unless calibrated elsewhere. | Adoption rates drive electricity, gas, PV, and EV outcomes. | high | energy carrier shifts, grid import/export | Inspect technology_cases.yaml and belgian_technology_inputs.yaml. | Call them counterfactual branches. | Calibrate with Belgian statistics and literature. | 1 |
| CAV-TECH-002 | Heat pump COP | COP conversion can be simplified or configured as representative seasonal performance. | Peak electric demand can be sensitive to COP assumptions. | medium | electricity, grid peaks, gas displacement | Inspect systems/technology.py and run configs. | Document COP assumptions. | Add temperature-dependent COP curves. | 2 |
| CAV-TECH-003 | PV/EV behaviour | PV self-consumption and EV charging depend on temporal matching and charging behaviour. | Annual totals can hide stress timing. | medium | PV export, grid peaks, EV demand | Inspect output_reader policies and annual profiles. | Use peak and seasonal metrics with annual values. | Add richer charging and self-consumption models. | 2 |
| CAV-STOCH-001 | Finite seeds | P10/P50/P90 bands depend on the number of successful stochastic realizations. | Small samples can make bands unstable. | high | uncertainty bands | Check n_successful_realizations and stochastic tables. | State sample size with every band. | Add Monte Carlo convergence checks. | 1 |
| CAV-STOCH-002 | Behavioural calibration | Behavioural distributions may not be fully empirically calibrated. | Stochastic spread may understate real behavioural variability. | medium | load profiles, peaks, DHW, EV | Inspect stochastic modules and validation data. | Call bands modelled stochastic spread. | Calibrate against smart-meter or survey data. | 2 |
| CAV-MET-001 | Gross electricity vs grid import | Gross electricity is demand before PV netting; grid import is after local PV netting. | Confusing them changes the interpretation of electrification and PV. | high | electricity demand, grid import/export | Use metric reference table and output_reader mapping. | Explain both metrics in figure captions. | Add unit tests and labels to all plots. | 1 |
| CAV-MET-002 | Power and energy units | W, kW, and kWh are distinct and conversion depends on timestep duration. | Unit errors can distort peak and annual metrics. | high | all energy and peak metrics | Inspect output_reader unit handling and profile timestamps. | State units in tables and axes. | Centralize unit conversion tests. | 1 |
| CAV-VAL-001 | Internal vs external validation | Scenario-tree validation checks consistency and traceability, not empirical accuracy. | A defensible pipeline can still produce biased demand estimates. | high | all results | Inspect validation reports and external validation outputs separately. | Do not claim external validation without a report. | Validate against Fluvius/smart-meter/aggregate load data with criteria. | 1 |
| CAV-THESIS-001 | Scenario interpretation | Scenario comparisons should not be interpreted as forecasts. | The thesis conclusion must separate conditional effects from predictions. | medium | narrative and supervisor discussion | Review executive summary and thesis text. | Use scenario, counterfactual, and conditional wording. | Add a limitations paragraph to every results chapter. | 1 |


# Chapter 14 - Recommended next improvements

## Immediate fixes before supervisor discussion

Check which phases have actually been implemented, run validation commands, generate a dry-run summary, prepare one baseline and one future run explanation, confirm run registry status, open key figures, and prepare a clear explanation of the 2050 policy.

## Prioritized roadmap

| recommendation | expected value | implementation difficulty | thesis relevance | suggested phase/module | risk if not done |
| --- | --- | --- | --- | --- | --- |
| Confirm implemented phases | high | low | high | Phase 1-9 docs and manifests | Supervisor may ask what is done versus planned. |
| Run validation commands | high | low | high | scenario validators | Unvalidated artifacts weaken the discussion. |
| Prepare one baseline and one future run explanation | high | low | high | runner and registry | Cannot demonstrate end-to-end workflow clearly. |
| Confirm run registry status | high | low | high | run_registry.csv | Avoids unsupported completion claims. |
| Explain 2050 policy cleanly | high | low | high | climate_windows.yaml | Double-counting question is likely. |
| External validation against smart-meter or aggregate data | very high | high | very high | validation modules/reports | Accuracy claims remain limited. |
| Technology calibration from Belgian statistics | high | medium | high | technology inputs | Technology scenarios remain qualitative/counterfactual. |
| Cohort size and Monte Carlo convergence sensitivity | high | medium | high | stochastic/cohort modules | P10/P90 robustness may be unstable. |
| Climate ensemble expansion | high | high | high | inputs/climate/processed | Climate uncertainty is underrepresented. |
| Parallel runner | medium | medium | medium | scenario runner | Full execution remains slow. |
| Richer diagnostics and figure styling | medium | low | medium | figures/scenario_tree | Presentation quality and debugging weaker. |
| Grid feeder constraints and spatial modelling | long-term | high | medium | new modules | Grid stress remains aggregate. |
| Dynamic pricing or demand response | long-term | high | medium | control/systems modules | Flexibility analysis remains absent. |


# Chapter 15 - How to use the model

## Full workflow

1. Validate scenario schema.
2. Create experiment space.
3. Generate leaf configs.
4. Dry-run runner.
5. Run one baseline leaf.
6. Run one future leaf.
7. Run batch.
8. Standardize outputs.
9. Generate comparisons.
10. Generate figures.
11. Run methodological audit.
12. Build handbook.

Important commands:

```bash
python3 -m model_v3.scenario_tree.validate_scenario_tree --config-root config/scenario_tree
python3 -m model_v3.scenario_tree.create_scenario_tree_space --config-root config/scenario_tree --experiment-root experiments/scenario_tree --print-summary
python3 -m model_v3.scenario_tree.generate_leaf_configs --config-root config/scenario_tree --experiment-root experiments/scenario_tree --climate-processed-root inputs/climate/processed --belgian-technology-inputs config/belgian_technology_inputs.yaml --cohort-size 100 --write-report --print-summary
python3 -m model_v3.scenario_tree.validate_leaf_configs --experiment-root experiments/scenario_tree --config-root config/scenario_tree --climate-processed-root inputs/climate/processed --belgian-technology-inputs config/belgian_technology_inputs.yaml --print-summary
python3 -m model_v3.scenarios.run_scenario_tree --dry-run --print-summary
python3 -m model_v3.scenarios.summarize_outputs --experiment-root experiments/scenario_tree --config-root config/scenario_tree --only-successful --write-reports --print-summary
python3 -m model_v3.scenarios.generate_comparisons --experiment-root experiments/scenario_tree --comparison-definitions config/scenario_tree/comparison_definitions.yaml --write-reports --print-summary
python3 -m model_v3.scenarios.generate_figures --experiment-root experiments/scenario_tree --figures-root figures/scenario_tree --write-metadata --write-captions --print-summary
python3 -m model_v3.scenarios.audit_scenario_tree --experiment-root experiments/scenario_tree --config-root config/scenario_tree --figures-root figures/scenario_tree --write-reports --print-summary
python3 -m model_v3.documentation.build_model_handbook --repo-root . --output docs/model_v3_complete_model_handbook.pdf --write-source --write-figures --print-summary
```

## Common tasks

List leaves by opening `experiments/scenario_tree/manifests/scenario_leaf_index.csv`. Inspect one leaf by opening its run folder under `experiments/scenario_tree/runs/{scenario_leaf_id}/`. Rerun a failed leaf with the runner and the same `--scenario-leaf-id`. Force rerun a successful leaf only when you intentionally want to replace or add an attempt. Find outputs under the leaf's `outputs/`, logs under `logs/`, metrics under `summaries/`, comparisons under `summaries/comparison_level/`, figures under `figures/scenario_tree/`, and audit traceability under `reports/scenario_tree_traceability_matrix.csv`.

## Troubleshooting

Missing climate forcing file: run leaf-config validation and inspect `inputs_manifest.yaml`. Ambiguous climate forcing file: check filename tokens and sidecar metadata. Missing Belgian technology input YAML: confirm `config/belgian_technology_inputs.yaml`. Invalid scenario ID: validate against `scenario_tree_schema.yaml`. Run already successful and skipped: use registry status and `--force` only if needed. Config validation fails: inspect config validation report. Summary metric missing: inspect raw output files and `output_reader.py` mappings. Figure not generated: validate figures and check source tables. PDF build backend missing: this script uses Matplotlib PDF when Pandoc/WeasyPrint/ReportLab are unavailable.


# Chapter 16 - Supervisor presentation guide

## One-minute explanation

`model_v3` is a bottom-up residential energy-demand model. It uses climate forcing, building and technology assumptions, and stochastic household realizations to simulate energy and grid metrics. The scenario tree makes the experiment reproducible by naming every climate window, RCP pathway, technology case, and seed explicitly.

## Five-minute explanation

The model engine transforms inputs into prepared forcing, physics state, control state, system state, and outputs. The scenario-tree layer wraps the model in a structured experiment design. The historical baseline is 1981-2005 with current stock. Future windows are near future, mid-century, and long term under RCP2.6, RCP4.5, and RCP8.5. Future technology cases include frozen stock, moderate electrification, and high electrification with PV/EV. Seeds represent stochastic household/cohort realizations. Standardized metrics allow climate-only, technology-only, stress-case, and stochastic robustness comparisons.

## Key accomplishments detected

- Scenario-tree schema and stable identifiers are present.
- Canonical climate windows and explicit 2050 policy are present.
- Generated experiment structure and leaf index are present.
- Per-leaf configs and input manifests are present according to config validation artifacts.
- Runner/provenance layer and run registry are present.
- Standardized output summaries exist for the successful subset.
- Comparison framework and validation reports are present.
- Generated figures and metadata are present.
- Documentation/audit reports are present.

## Key figures to show

Show the model architecture, scenario-tree structure, climate-window timeline, output standardization workflow, and one grid-impact or stress-case figure. Be clear that data-derived result figures reflect available successful summary rows, not the full 2800-leaf design.

## Likely supervisor questions and honest answers

Why use a scenario tree? Because it separates climate, technology, and stochastic uncertainty and preserves traceability.

Why RCPs instead of SSPs? The repository currently encodes RCP pathways in `climate_windows.yaml` and `scenario_tree_schema.yaml`; switching to SSPs would require new climate inputs and metadata.

How do you prevent double-counting 2050? Raw source files may overlap, but canonical windows do not: near-future ends on 2049-12-31 and mid-century starts on 2050-01-01.

What does a stochastic realization represent? A reproducible seed/cohort draw, not a climate model member.

How do you know the model is valid? The scenario-tree artifacts are internally validated for consistency and traceability. External empirical validation should be claimed only from separate validation reports.

What is the difference between grid import and electricity demand? Gross electricity is household demand before PV netting; grid import is the portion drawn from the grid after local PV generation is netted.

What are the main limitations? Partial execution coverage, simplified physics, limited climate ensemble, technology calibration uncertainty, finite stochastic realizations, and external validation gaps.


# Terminology

This chapter is a study reference. Each term includes definition, where it appears, why it matters, and a common misunderstanding.

| term | definition | where it appears in model_v3 | why it matters | common misunderstanding |
| --- | --- | --- | --- | --- |
| model engine | The code that turns configured inputs into simulated residential demand outputs. | src/model_v3/data, physics, control, systems, output | It is the numerical core, separate from the scenario-tree manager. | Do not call the scenario tree itself the physics model. |
| runner | The orchestration command that selects scenario leaves, validates configs, executes leaves, and records registry rows. | src/model_v3/scenarios/run_scenario_tree.py | It is the operational bridge between configs and model execution. | A dry-run runner result is not a completed simulation. |
| orchestration layer | The scenario-tree code that manages leaf selection, run folders, logs, registry, summaries, comparisons, and figures. | src/model_v3/scenarios and src/model_v3/scenario_tree | It provides reproducibility and traceability. | It does not prove physical realism by itself. |
| scenario | A deterministic combination of climate window, climate pathway, and technology case. | scenario_tree_schema.yaml and scenario_leaf_index.csv | It is the parent grouping for stochastic leaves. | A scenario is not a single run if it has many seeds. |
| realization | A stochastic sampling instance identified by a reproducible seed. | realization_policy.yaml, run configs, summaries | It allows pairwise comparisons and stochastic spread. | A realization is not a climate model member. |
| scenario leaf | One executable unit: one scenario plus one realization. | scenario_leaf_index.csv and run directories | It is the smallest run and registry unit. | An enumerated leaf is not automatically a successful output. |
| scenario ID | The stable identifier for a scenario without the seed. | scenario_id columns | It groups realization outputs. | Do not confuse with scenario_leaf_id. |
| scenario leaf ID | The full stable identifier including seed. | run directories and registry | It joins configs, outputs, logs, summaries, and figures. | Do not edit IDs after outputs exist. |
| manifest | A metadata file describing generated experiment files and provenance. | scenario_tree_manifest.yaml and handbook manifest | It makes generated artifacts auditable. | It is evidence of file generation, not model validation. |
| input manifest | A per-leaf file listing resolved inputs such as climate and technology files. | runs/*/inputs_manifest.yaml | It answers which inputs a leaf used. | It does not guarantee the input values are scientifically perfect. |
| run registry | A CSV ledger of run attempts, statuses, hashes, paths, seeds, and errors. | manifests/run_registry.csv | It is the source of truth for what has run. | Skipped rows must be interpreted with latest actual status logic. |
| provenance | Information needed to trace a result back to config, input files, code state, seed, and output path. | run registry, audit matrix | It makes supervisor questions answerable. | Provenance is not the same as calibration. |
| config hash | A hash of the run configuration used by an attempt. | run_registry.csv | It detects config drift between runs. | A hash does not describe whether assumptions are appropriate. |
| reproducibility | Ability to regenerate the same documented artifacts from the same inputs and code. | manifests, configs, scripts | It underpins thesis-grade traceability. | Reproducible does not mean externally valid. |
| deterministic path resolver | Code that maps scenario IDs and inputs to stable config, run, output, and log paths. | src/model_v3/scenario_tree/paths.py | It prevents ad hoc output locations. | It cannot recover from manually renamed files. |
| canonical analysis window | The non-overlapping date range used for metrics and comparisons. | climate_windows.yaml | It prevents double-counting 2050. | It can differ from source-file coverage. |
| source-file window | The raw or processed climate file coverage recorded for a file. | climate_windows.yaml and climate CSV paths | It documents input coverage. | It may overlap across files. |
| baseline | The historical reference branch using baseline_1981_2005, historical pathway, and tech_current_stock. | scenario_tree_schema.yaml | It anchors future deltas. | It is not automatically a measured-demand validation. |
| counterfactual | A conditional scenario used to isolate an effect, such as future climate with frozen stock. | comparison_definitions.yaml | It helps separate climate and technology effects. | It is not a forecast. |
| technology case | A branch describing residential technology assumptions. | technology_cases.yaml | It controls heat pumps, PV, EV, gas/electric shifts. | The metadata may be qualitative unless calibrated. |
| stress case | A high-impact comparison branch such as long-term RCP8.5 with high electrification, PV, and EV. | comparison_definitions.yaml | It probes infrastructure stress. | It is not the most likely future. |
| bottom-up model | A model that builds demand from household/building/end-use mechanisms rather than fitting aggregate totals only. | model_v3 architecture | It links assumptions to physical and behavioural drivers. | It still needs calibration. |
| archetype | A representative dwelling or household category with shared parameters. | inputs/building and archetypes.yaml | It reduces complexity while preserving building diversity. | It may hide within-category variation. |
| one-zone thermal model | A lumped building representation with one indoor temperature state. | physics_core.py and thermal_dynamics.py | It enables transparent heat-balance simulation. | It does not model room-by-room dynamics. |
| heat balance | Accounting of heat losses, gains, and supplied heat over a timestep. | physics_core.py | It drives useful heating demand. | Simplified terms may omit detailed dynamics. |
| thermal mass | The effective heat capacity that slows indoor temperature changes. | InputDataset, PreparedForcing, PhysicsState | It affects peaks and comfort. | It is difficult to know precisely for real dwellings. |
| UA value | Overall heat loss coefficient in W per K. | heat_loss_coefficient_W_per_C fields | It determines envelope heat loss. | It can be uncertain by archetype. |
| infiltration | Uncontrolled air exchange with outdoors. | ACH_inf and airflow calculations | It adds heat loss. | Behaviour and leakage vary strongly. |
| ventilation | Controlled or assumed air exchange. | ACH_vent_base, ACH_vent_occupied, eta_HRV | It affects heat losses and indoor conditions. | Schedules and heat recovery may be simplified. |
| internal gains | Heat from occupants, appliances, lighting, and cooking. | data_module.py and PreparedForcing fields | They reduce heating demand and affect free-float temperature. | Occupant behaviour is uncertain. |
| solar gains | Heat entering through windows from solar irradiance. | PreparedForcing Q_solar_gains_W fields | They influence heating and overheating. | Orientation and shading assumptions matter. |
| useful heat | Thermal energy delivered to the space or DHW before carrier conversion. | annual_useful_heating_kWh, annual_dhw_kWh | It separates building demand from system efficiency. | It is not the same as gas or electricity input. |
| final energy | Delivered carrier energy consumed by the household, such as gas or electricity. | annual_gas_kWh and electricity metrics | It matters for emissions and billing. | Do not mix with useful heat. |
| delivered energy | Energy supplied to the dwelling by a carrier. | carrier conversion in systems/technology.py | It connects useful heat to gas/electricity. | PV netting can complicate electricity interpretation. |
| domestic hot water | Useful heat demand for hot water use. | Q_dhw_demand_W and annual_dhw_kWh | It is a non-space-heating thermal load. | Behavioural timing can be uncertain. |
| coefficient of performance | Useful heat delivered per unit electric energy for a heat pump. | heating_cop, dhw_cop, technology performance | It determines electrification impact. | Seasonal/static COP can miss weather dependence. |
| heat pump | Electric heating technology converting electricity to useful heat with COP above one. | technology_cases.yaml and systems/technology.py | It shifts heat demand from gas to electricity. | Uptake and performance are assumptions. |
| PV generation | Electricity generated by photovoltaic panels. | P_pv_generation_W and pv_generation_kWh | It reduces grid import and can create export. | Annual PV generation does not guarantee peak relief. |
| self-consumption | PV generation used locally instead of exported. | pv_self_consumption_kWh | It indicates local matching of supply and demand. | The metric depends on temporal resolution. |
| grid import | Electricity drawn from the external grid after PV netting. | P_el_grid_import_W and annual_grid_import_kWh | It matters for network load. | It is not gross electricity demand. |
| grid export | Electricity sent to the grid when PV exceeds local demand. | P_el_grid_export_W and annual_grid_export_kWh | It affects distribution flows. | It depends on PV and load timing. |
| peak demand | Maximum power over a selected period. | peak_grid_import_W and seasonal peaks | It is critical for grid stress. | It depends on timestep resolution. |
| load profile | Time series of demand or power. | annual_profile.csv and input load profiles | It captures timing, not just annual energy. | Annual aggregation hides profile shape. |
| RCP | Representative Concentration Pathway climate forcing branch. | climate_pathway_id values | It structures future climate uncertainty. | It is not a probability. |
| RCP2.6 | Lower forcing RCP branch encoded as rcp_2_6. | climate_windows.yaml | It represents a low-forcing future branch. | It does not include technology adoption by itself. |
| RCP4.5 | Intermediate forcing RCP branch encoded as rcp_4_5. | climate_windows.yaml | It provides a middle climate branch. | It is not a central forecast. |
| RCP8.5 | Higher forcing RCP branch encoded as rcp_8_5. | climate_windows.yaml | It supports stress-case climate analysis. | It should not automatically be called most likely. |
| climate forcing | Weather or climate input time series used to drive the model. | inputs/climate/processed and run configs | It determines outdoor temperature and solar inputs. | One forcing file is not the full climate ensemble. |
| historical baseline | The 1981-2005 historical climate branch. | baseline_1981_2005 | It anchors future comparisons. | It is climate baseline, not measured demand validation. |
| climate window | A named analysis period such as near future or mid-century. | climate_windows.yaml | It controls which years are summarized. | Source and canonical windows can differ. |
| HDD | Heating degree days: accumulated coldness relative to a base temperature. | HDD_15 and HDD_18 | It explains heating demand pressure. | It is a climate proxy, not the demand model itself. |
| HDD_15 | Heating degree days using 15 C base. | standardized metrics | Useful for climate sensitivity. | Base choice affects magnitude. |
| HDD_18 | Heating degree days using 18 C base. | standardized metrics | Captures stricter heating threshold. | Base choice affects interpretation. |
| CDD | Cooling degree days: accumulated warmth above a base temperature. | CDD_22 | Useful for summer stress analysis. | Cooling model may be absent or simplified. |
| CDD_22 | Cooling degree days using 22 C base. | standardized metrics | Indicates warm-weather exposure. | It does not equal cooling energy unless cooling is modelled. |
| irradiance | Solar power per area, typically W/m2. | mean_solar_W_m2 and climate columns | It drives solar/PV or solar gains. | Column convention must be checked. |
| mean outdoor temperature | Average T_out over canonical window. | mean_T_out_C | Summarizes climate branch warmth. | A mean can hide extremes. |
| winter mean | Mean outdoor temperature for December, January, and February. | winter_mean_T_out_C | Relevant for heating and winter peaks. | Season definition is fixed and simple. |
| summer mean | Mean outdoor temperature for June, July, and August. | summer_mean_T_out_C | Relevant for summer stress. | It does not capture heatwaves alone. |
| stochastic model | A model component using random draws controlled by seeds. | src/model_v3/stochastic and cohort modules | It represents behavioural/cohort variability. | Random spread is conditional on assumed distributions. |
| seed | Integer reproducibility key mapped from realization_id. | realization_policy.yaml and run configs | It allows reruns and pairwise comparisons. | A seed is not a probability weight. |
| cohort | A sampled group of households or profiles represented by a realization. | cohort_size fields and cohort modules | It approximates population variability. | Finite cohort size can create sampling noise. |
| aleatoric uncertainty | Intrinsic variability such as behaviour differences. | stochastic realizations | It motivates P10/P50/P90 bands. | Modelled variability may be narrower than reality. |
| epistemic uncertainty | Uncertainty from limited knowledge, data, or model structure. | caveats and assumptions | It motivates sensitivity and validation work. | More seeds do not remove structural uncertainty. |
| scenario uncertainty | Uncertainty represented by alternative climate and technology branches. | scenario tree | It separates branch assumptions. | Branches are not probabilities unless weighted. |
| uncertainty band | A spread summary across realizations, commonly P10 to P90. | stochastic robustness tables and figures | It communicates robustness. | It is not a measured confidence interval. |
| P10 | 10th percentile of available realization outcomes. | comparison robustness outputs | Shows low-side stochastic outcome. | Unstable with few successful rows. |
| P50 | Median outcome. | comparison robustness outputs | A robust central value. | Not the same as expected value if distributions are skewed. |
| P90 | 90th percentile of available realization outcomes. | comparison robustness outputs | Shows high-side stochastic outcome. | Unstable with few successful rows. |
| quantile | A value below which a given share of observations falls. | aggregate and comparison tables | Summarizes distributions without assuming normality. | Requires enough observations. |
| Monte Carlo | Repeated stochastic sampling using different seeds. | realization policy and stochastic robustness | Estimates variability or convergence. | This only covers modelled stochastic dimensions. |
| convergence | Stability of estimates as more realizations are added. | recommended improvements | It supports robust uncertainty statements. | Not proven by a small number of runs. |
| sensitivity analysis | Systematic variation of assumptions to see output response. | recommended improvements | It identifies influential assumptions. | It is different from validation. |
| RMSE | Root mean squared error between model and reference series. | validation metrics modules | Penalizes large errors. | Scale-dependent and sensitive to outliers. |
| MAE | Mean absolute error. | validation metrics modules | Easy-to-interpret average absolute deviation. | Does not emphasize large errors as much as RMSE. |
| Pearson correlation | Linear association between modelled and reference time series. | validation metrics modules | Checks timing and shape co-movement. | High correlation can still have bias. |
| NMBE | Normalized mean bias error. | validation metrics modules | Shows systematic over- or under-prediction. | Can hide compensating errors. |
| CVRMSE | Coefficient of variation of RMSE. | validation metrics modules | Normalizes RMSE by mean reference level. | Can be unstable when mean is small. |
| coefficient of variation | Standard deviation divided by mean. | aggregate and stochastic tables | Measures relative spread. | Undefined or misleading near zero mean. |
| standard deviation | Average spread around the mean. | aggregate metrics | Describes realization variability. | Assumes finite sample and is outlier-sensitive. |
| median | Middle value of a sorted sample. | aggregate metrics | Robust central tendency. | May differ from mean. |
| percentile | Value at a stated rank of a distribution. | p05, p10, p90, p95 columns | Communicates spread. | Needs enough samples. |
| interquartile range | P75 minus P25. | stochastic robustness diagnostics | Robust spread measure. | Not a full range. |
| diversity factor | Ratio reflecting non-coincident individual peaks versus aggregate peak. | grid analysis concept | Useful for feeder planning. | Only meaningful with compatible profile granularity. |
| load duration curve | Sorted load profile from high to low. | validation output figures | Shows distribution of load magnitudes. | Loses chronological timing. |
| calibration | Adjusting model parameters to match reference data. | validation and recommended improvements | Improves empirical fit. | Can overfit without independent validation. |
| validation | Testing model outputs against internal contracts or external data. | validation reports and validation modules | Supports trust in outputs. | Internal validation is not external empirical validation. |
| internal consistency check | A check that files, IDs, metrics, and policies agree. | scenario-tree validators | Prevents traceability mistakes. | Does not prove accuracy. |
| external validation | Comparison with independent measured data. | validation reports if present | Needed for accuracy claims. | Do not claim it unless a report proves it. |
| baseline comparison | Future leaf compared against historical current-stock baseline. | baseline_comparison_metrics.csv | Quantifies future delta. | Requires matching successful baseline realization. |
| delta | Difference between compared and reference value. | comparison tables | Shows absolute change. | Interpret with units and reference choice. |
| percentage change | Delta divided by reference value times 100. | comparison percentage tables | Normalizes change. | Undefined when reference is zero. |


# Appendix A - File inventory

| path | role | phase | required/optional | exists |
| --- | --- | --- | --- | --- |
| config/ | expected repository artifact | Phase 1-9 | context | yes |
| config/scenario_tree/ | scenario-tree config, artifact, report, or figure | Phase 1-9 | context | yes |
| config/scenario_tree/scenario_tree_schema.yaml | scenario-tree config, artifact, report, or figure | Phase 1-9 | required | yes |
| config/scenario_tree/climate_windows.yaml | scenario-tree config, artifact, report, or figure | Phase 1-9 | required | yes |
| config/scenario_tree/technology_cases.yaml | scenario-tree config, artifact, report, or figure | Phase 1-9 | required | yes |
| config/scenario_tree/realization_policy.yaml | scenario-tree config, artifact, report, or figure | Phase 1-9 | required | yes |
| config/scenario_tree/comparison_definitions.yaml | scenario-tree config, artifact, report, or figure | Phase 1-9 | required | yes |
| config/belgian_technology_inputs.yaml | expected repository artifact | Phase 1-9 | required | yes |
| inputs/ | model input data | Phase 1-9 | context | yes |
| inputs/climate/ | model input data | Phase 1-9 | context | yes |
| inputs/climate/processed/ | model input data | Phase 1-9 | context | yes |
| experiments/scenario_tree/ | scenario-tree config, artifact, report, or figure | Phase 1-9 | context | yes |
| experiments/scenario_tree/manifests/scenario_tree_manifest.yaml | scenario-tree config, artifact, report, or figure | Phase 1-9 | required | yes |
| experiments/scenario_tree/manifests/scenario_leaf_index.csv | scenario-tree config, artifact, report, or figure | Phase 1-9 | required | yes |
| experiments/scenario_tree/manifests/run_registry.csv | scenario-tree config, artifact, report, or figure | Phase 1-9 | required | yes |
| experiments/scenario_tree/manifests/config_validation_report.md | scenario-tree config, artifact, report, or figure | Phase 1-9 | required | yes |
| experiments/scenario_tree/manifests/summary_validation_report.md | scenario-tree config, artifact, report, or figure | Phase 1-9 | required | yes |
| experiments/scenario_tree/manifests/comparison_validation_report.md | scenario-tree config, artifact, report, or figure | Phase 1-9 | required | yes |
| experiments/scenario_tree/summaries/realization_level/scenario_leaf_metrics.csv | scenario-tree config, artifact, report, or figure | Phase 1-9 | required | yes |
| experiments/scenario_tree/summaries/scenario_level/scenario_aggregate_metrics.csv | scenario-tree config, artifact, report, or figure | Phase 1-9 | required | yes |
| experiments/scenario_tree/summaries/comparison_level/comparison_index.csv | scenario-tree config, artifact, report, or figure | Phase 1-9 | required | yes |
| figures/scenario_tree/ | scenario-tree config, artifact, report, or figure | Phase 1-9 | context | yes |
| figures/scenario_tree/metadata/figure_metadata.yaml | scenario-tree config, artifact, report, or figure | Phase 1-9 | required | yes |
| figures/scenario_tree/thesis_caption_drafts.md | scenario-tree config, artifact, report, or figure | Phase 1-9 | required | yes |
| reports/scenario_tree_validation_report.md | scenario-tree config, artifact, report, or figure | Phase 1-9 | required | yes |
| reports/scenario_tree_audit_summary.yaml | scenario-tree config, artifact, report, or figure | Phase 1-9 | required | yes |
| reports/scenario_tree_traceability_matrix.csv | scenario-tree config, artifact, report, or figure | Phase 1-9 | required | yes |
| docs/model_v3_scenario_tree_design.md | scenario-tree config, artifact, report, or figure | Phase 1-9 | required | yes |
| src/model_v3/ | implementation module | Phase 1-9 | context | yes |
| src/model_v3/interfaces.py | implementation module | Phase 1-9 | required | yes |
| src/model_v3/physics/physics_core.py | implementation module | Phase 1-9 | required | yes |
| src/model_v3/control/control_core.py | implementation module | Phase 1-9 | required | yes |
| src/model_v3/systems/system_core.py | implementation module | Phase 1-9 | required | yes |
| src/model_v3/scenarios/run_scenario_tree.py | scenario-tree config, artifact, report, or figure | Phase 1-9 | required | yes |
| src/model_v3/scenarios/summarize_outputs.py | implementation module | Phase 1-9 | required | yes |
| src/model_v3/scenarios/generate_comparisons.py | implementation module | Phase 1-9 | required | yes |
| src/model_v3/scenarios/generate_figures.py | implementation module | Phase 1-9 | required | yes |

## Input inventory excerpt

| path | type | purpose | temporal resolution | units | scenario dimension | required | validation status |
| --- | --- | --- | --- | --- | --- | --- | --- |
| inputs/README.md | md | Repository configuration or metadata. | not_detected | not_detected | not_applicable | optional | see validation reports if present |
| inputs/building/airflow_archetypes_v2.csv | csv | Building or archetype parameters used by the model input layer. | not_detected | floor_area_m2, stock_weight | building/archetype assumptions | optional | see validation reports if present |
| inputs/building/archetype_parameters_merged_v2.csv | csv | Building or archetype parameters used by the model input layer. | not_detected | stock_weight, floor_area_m2 | building/archetype assumptions | optional | see validation reports if present |
| inputs/building/archetype_parameters_merged_v2.md | md | Building or archetype parameters used by the model input layer. | not_detected | not_detected | building/archetype assumptions | optional | see validation reports if present |
| inputs/building/archetype_parameters_merged_v3.csv | csv | Building or archetype parameters used by the model input layer. | not_detected | column names inspected; units not explicit | building/archetype assumptions | optional | see validation reports if present |
| inputs/building/archetype_parameters_merged_v3.md | md | Building or archetype parameters used by the model input layer. | not_detected | not_detected | building/archetype assumptions | optional | see validation reports if present |
| inputs/building/archetype_parameters_v1.csv | csv | Building or archetype parameters used by the model input layer. | timestamped; inspect source for exact step | heating_carrier, heating_system_class | building/archetype assumptions | optional | see validation reports if present |
| inputs/building/envelope_archetypes_v1.csv | csv | Building or archetype parameters used by the model input layer. | not_detected | floor_area_m2 | building/archetype assumptions | optional | see validation reports if present |
| inputs/building/envelope_archetypes_v2.csv | csv | Building or archetype parameters used by the model input layer. | not_detected | column names inspected; units not explicit | building/archetype assumptions | optional | see validation reports if present |
| inputs/building/internal_gains_archetypes_v2.csv | csv | Building or archetype parameters used by the model input layer. | not_detected | floor_area_m2, stock_weight | building/archetype assumptions | optional | see validation reports if present |
| inputs/building/renovation_prevalence_epc_mapping.csv | csv | Building or archetype parameters used by the model input layer. | not_detected | column names inspected; units not explicit | building/archetype assumptions | optional | see validation reports if present |
| inputs/climate/processed/baseline/weather_baseline_historical_cnrm_cerfacs_cm5_cnrm_aladin63_r1i1p1.csv | csv | Processed climate forcing for a canonical scenario-tree branch. | timestamped; inspect source for exact step | T_out_C, I_solar_W_m2 | climate_window_id, climate_pathway_id | required_for_configured_climate_leaves | see validation reports if present |
| inputs/climate/processed/baseline/weather_baseline_historical_cnrm_cerfacs_cm5_cnrm_aladin63_r1i1p1.metadata.json | json | Processed climate forcing for a canonical scenario-tree branch. | not_detected | not_detected | climate_window_id, climate_pathway_id | required_for_configured_climate_leaves | see validation reports if present |
| inputs/climate/processed/long_term/weather_long_term_rcp_2_6_cnrm_cerfacs_cm5_cnrm_aladin63_r1i1p1.csv | csv | Processed climate forcing for a canonical scenario-tree branch. | timestamped; inspect source for exact step | T_out_C, I_solar_W_m2 | climate_window_id, climate_pathway_id | required_for_configured_climate_leaves | see validation reports if present |
| inputs/climate/processed/long_term/weather_long_term_rcp_2_6_cnrm_cerfacs_cm5_cnrm_aladin63_r1i1p1.metadata.json | json | Processed climate forcing for a canonical scenario-tree branch. | not_detected | not_detected | climate_window_id, climate_pathway_id | required_for_configured_climate_leaves | see validation reports if present |
| inputs/climate/processed/long_term/weather_long_term_rcp_4_5_cnrm_cerfacs_cm5_cnrm_aladin63_r1i1p1.csv | csv | Processed climate forcing for a canonical scenario-tree branch. | timestamped; inspect source for exact step | T_out_C, I_solar_W_m2 | climate_window_id, climate_pathway_id | required_for_configured_climate_leaves | see validation reports if present |
| inputs/climate/processed/long_term/weather_long_term_rcp_4_5_cnrm_cerfacs_cm5_cnrm_aladin63_r1i1p1.metadata.json | json | Processed climate forcing for a canonical scenario-tree branch. | not_detected | not_detected | climate_window_id, climate_pathway_id | required_for_configured_climate_leaves | see validation reports if present |
| inputs/climate/processed/long_term/weather_long_term_rcp_8_5_cnrm_cerfacs_cm5_cnrm_aladin63_r1i1p1.csv | csv | Processed climate forcing for a canonical scenario-tree branch. | timestamped; inspect source for exact step | T_out_C, I_solar_W_m2 | climate_window_id, climate_pathway_id | required_for_configured_climate_leaves | see validation reports if present |
| inputs/climate/processed/long_term/weather_long_term_rcp_8_5_cnrm_cerfacs_cm5_cnrm_aladin63_r1i1p1.metadata.json | json | Processed climate forcing for a canonical scenario-tree branch. | not_detected | not_detected | climate_window_id, climate_pathway_id | required_for_configured_climate_leaves | see validation reports if present |
| inputs/climate/processed/mid_century/weather_mid_century_rcp_2_6_cnrm_cerfacs_cm5_cnrm_aladin63_r1i1p1.csv | csv | Processed climate forcing for a canonical scenario-tree branch. | timestamped; inspect source for exact step | T_out_C, I_solar_W_m2 | climate_window_id, climate_pathway_id | required_for_configured_climate_leaves | see validation reports if present |
| inputs/climate/processed/mid_century/weather_mid_century_rcp_2_6_cnrm_cerfacs_cm5_cnrm_aladin63_r1i1p1.metadata.json | json | Processed climate forcing for a canonical scenario-tree branch. | not_detected | not_detected | climate_window_id, climate_pathway_id | required_for_configured_climate_leaves | see validation reports if present |
| inputs/climate/processed/mid_century/weather_mid_century_rcp_4_5_cnrm_cerfacs_cm5_cnrm_aladin63_r1i1p1.csv | csv | Processed climate forcing for a canonical scenario-tree branch. | timestamped; inspect source for exact step | T_out_C, I_solar_W_m2 | climate_window_id, climate_pathway_id | required_for_configured_climate_leaves | see validation reports if present |
| inputs/climate/processed/mid_century/weather_mid_century_rcp_4_5_cnrm_cerfacs_cm5_cnrm_aladin63_r1i1p1.metadata.json | json | Processed climate forcing for a canonical scenario-tree branch. | not_detected | not_detected | climate_window_id, climate_pathway_id | required_for_configured_climate_leaves | see validation reports if present |
| inputs/climate/processed/mid_century/weather_mid_century_rcp_8_5_cnrm_cerfacs_cm5_cnrm_aladin63_r1i1p1.csv | csv | Processed climate forcing for a canonical scenario-tree branch. | timestamped; inspect source for exact step | T_out_C, I_solar_W_m2 | climate_window_id, climate_pathway_id | required_for_configured_climate_leaves | see validation reports if present |
| inputs/climate/processed/mid_century/weather_mid_century_rcp_8_5_cnrm_cerfacs_cm5_cnrm_aladin63_r1i1p1.metadata.json | json | Processed climate forcing for a canonical scenario-tree branch. | not_detected | not_detected | climate_window_id, climate_pathway_id | required_for_configured_climate_leaves | see validation reports if present |
| inputs/climate/processed/near_future/weather_near_future_rcp_2_6_cnrm_cerfacs_cm5_cnrm_aladin63_r1i1p1.csv | csv | Processed climate forcing for a canonical scenario-tree branch. | timestamped; inspect source for exact step | T_out_C, I_solar_W_m2 | climate_window_id, climate_pathway_id | required_for_configured_climate_leaves | see validation reports if present |
| inputs/climate/processed/near_future/weather_near_future_rcp_2_6_cnrm_cerfacs_cm5_cnrm_aladin63_r1i1p1.metadata.json | json | Processed climate forcing for a canonical scenario-tree branch. | not_detected | not_detected | climate_window_id, climate_pathway_id | required_for_configured_climate_leaves | see validation reports if present |
| inputs/climate/processed/near_future/weather_near_future_rcp_4_5_cnrm_cerfacs_cm5_cnrm_aladin63_r1i1p1.csv | csv | Processed climate forcing for a canonical scenario-tree branch. | timestamped; inspect source for exact step | T_out_C, I_solar_W_m2 | climate_window_id, climate_pathway_id | required_for_configured_climate_leaves | see validation reports if present |
| inputs/climate/processed/near_future/weather_near_future_rcp_4_5_cnrm_cerfacs_cm5_cnrm_aladin63_r1i1p1.metadata.json | json | Processed climate forcing for a canonical scenario-tree branch. | not_detected | not_detected | climate_window_id, climate_pathway_id | required_for_configured_climate_leaves | see validation reports if present |
| inputs/climate/processed/near_future/weather_near_future_rcp_8_5_cnrm_cerfacs_cm5_cnrm_aladin63_r1i1p1.csv | csv | Processed climate forcing for a canonical scenario-tree branch. | timestamped; inspect source for exact step | T_out_C, I_solar_W_m2 | climate_window_id, climate_pathway_id | required_for_configured_climate_leaves | see validation reports if present |
| inputs/climate/processed/near_future/weather_near_future_rcp_8_5_cnrm_cerfacs_cm5_cnrm_aladin63_r1i1p1.metadata.json | json | Processed climate forcing for a canonical scenario-tree branch. | not_detected | not_detected | climate_window_id, climate_pathway_id | required_for_configured_climate_leaves | see validation reports if present |
| inputs/end_use/EU27_BE_household_enduse_2019.csv | csv | Repository configuration or metadata. | not_detected | column names inspected; units not explicit | not_applicable | optional | see validation reports if present |
| inputs/load_profiles/LCL_2013.csv | csv | Observed or representative load profile input data. | timestamped; inspect source for exact step | column names inspected; units not explicit | stochastic/end-use behaviour | optional | see validation reports if present |
| inputs/load_profiles/fluvius/P6269_Open_Data_EV_geen_ZP.csv | csv | Observed or representative load profile input data. | not_detected | Volume_Afname_KWh, Volume_Injectie_KWh | stochastic/end-use behaviour | optional | see validation reports if present |
| inputs/load_profiles/fluvius/P6269_Open_Data_EV_met_ZP.csv | csv | Observed or representative load profile input data. | not_detected | Volume_Afname_KWh, Volume_Injectie_KWh | stochastic/end-use behaviour | optional | see validation reports if present |
| inputs/load_profiles/fluvius/P6269_Open_Data_WP_EV_geen_ZP.csv | csv | Observed or representative load profile input data. | not_detected | Volume_Afname_KWh, Volume_Injectie_KWh | stochastic/end-use behaviour | optional | see validation reports if present |
| inputs/load_profiles/fluvius/P6269_Open_Data_WP_EV_met_ZP.csv | csv | Observed or representative load profile input data. | not_detected | Volume_Afname_KWh, Volume_Injectie_KWh | stochastic/end-use behaviour | optional | see validation reports if present |
| inputs/load_profiles/fluvius/P6269_Open_Data_WP_geen_ZP.csv | csv | Observed or representative load profile input data. | not_detected | Volume_Afname_KWh, Volume_Injectie_KWh | stochastic/end-use behaviour | optional | see validation reports if present |
| inputs/load_profiles/fluvius/P6269_Open_Data_WP_met_ZP.csv | csv | Observed or representative load profile input data. | not_detected | Volume_Afname_KWh, Volume_Injectie_KWh | stochastic/end-use behaviour | optional | see validation reports if present |
| inputs/load_profiles/fluvius/P6269_Open_Data_enkel_ZP.csv | csv | Observed or representative load profile input data. | not_detected | Volume_Afname_KWh, Volume_Injectie_KWh | stochastic/end-use behaviour | optional | see validation reports if present |
| inputs/load_profiles/fluvius/P6269_Open_Data_geen_ZP.csv | csv | Observed or representative load profile input data. | not_detected | Volume_Afname_KWh, Volume_Injectie_KWh | stochastic/end-use behaviour | optional | see validation reports if present |
| inputs/load_profiles/kul/house_1/house_1-elec.csv | csv | Observed or representative load profile input data. | timestamped; inspect source for exact step | column names inspected; units not explicit | stochastic/end-use behaviour | optional | see validation reports if present |
| inputs/load_profiles/kul/house_1/house_1-metadata.md | md | Observed or representative load profile input data. | not_detected | not_detected | stochastic/end-use behaviour | optional | see validation reports if present |
| inputs/load_profiles/kul/house_2/house_2-elec.csv | csv | Observed or representative load profile input data. | timestamped; inspect source for exact step | column names inspected; units not explicit | stochastic/end-use behaviour | optional | see validation reports if present |
| inputs/load_profiles/kul/house_2/house_2-metadata.md | md | Observed or representative load profile input data. | not_detected | not_detected | stochastic/end-use behaviour | optional | see validation reports if present |
| inputs/load_profiles/kul/house_3/house_3-elec.csv | csv | Observed or representative load profile input data. | timestamped; inspect source for exact step | column names inspected; units not explicit | stochastic/end-use behaviour | optional | see validation reports if present |
| inputs/load_profiles/kul/house_3/house_3-metadata.md | md | Observed or representative load profile input data. | not_detected | not_detected | stochastic/end-use behaviour | optional | see validation reports if present |
| inputs/occupancy/occupancy_model_spec_v1.yaml | yaml | Occupancy model specification. | not_detected | not_detected | stochastic behaviour | optional | see validation reports if present |
| inputs/solar/TimeseriesEAST_50.830_4.350_SA3_90deg_-90deg_2005_2023.csv | csv | Solar generation or irradiance input data. | not_detected | column names inspected; units not explicit | technology/PV and forcing | optional | see validation reports if present |
| inputs/solar/TimeseriesNORTH_50.830_4.350_SA3_90deg_-179deg_2005_2023.csv | csv | Solar generation or irradiance input data. | not_detected | column names inspected; units not explicit | technology/PV and forcing | optional | see validation reports if present |
| inputs/solar/TimeseriesSOUTH_50.830_4.350_SA3_90deg_0deg_2005_2023.csv | csv | Solar generation or irradiance input data. | not_detected | column names inspected; units not explicit | technology/PV and forcing | optional | see validation reports if present |
| inputs/solar/TimeseriesWEST_50.830_4.350_SA3_90deg_90deg_2005_2023.csv | csv | Solar generation or irradiance input data. | not_detected | column names inspected; units not explicit | technology/PV and forcing | optional | see validation reports if present |
| inputs/solar/raw_inputs/ods001.csv | csv | Solar generation or irradiance input data. | timestamped; inspect source for exact step | column names inspected; units not explicit | technology/PV and forcing | optional | see validation reports if present |
| inputs/solar/raw_inputs/solardata_50.847_4.352_SA3_90deg_-179deg_2023_2023.csv | csv | Solar generation or irradiance input data. | not_detected | column names inspected; units not explicit | technology/PV and forcing | optional | see validation reports if present |
| inputs/solar/raw_inputs/solardata_50.847_4.352_SA3_90deg_-90deg_2023_2023.csv | csv | Solar generation or irradiance input data. | not_detected | column names inspected; units not explicit | technology/PV and forcing | optional | see validation reports if present |
| inputs/solar/raw_inputs/solardata_50.847_4.352_SA3_90deg_0deg_2023_2023.csv | csv | Solar generation or irradiance input data. | not_detected | column names inspected; units not explicit | technology/PV and forcing | optional | see validation reports if present |
| inputs/solar/raw_inputs/solardata_50.847_4.352_SA3_90deg_90deg_2023_2023.csv | csv | Solar generation or irradiance input data. | not_detected | column names inspected; units not explicit | technology/PV and forcing | optional | see validation reports if present |
| inputs/validation/pv/elia/ods032_belgium_pv_2023_pt15m.csv | csv | Repository configuration or metadata. | timestamped; inspect source for exact step | column names inspected; units not explicit | not_applicable | optional | see validation reports if present |
| inputs/validation/pv/elia/ods032_belgium_pv_2024_pt15m.csv | csv | Repository configuration or metadata. | timestamped; inspect source for exact step | column names inspected; units not explicit | not_applicable | optional | see validation reports if present |
| inputs/weather/Timeseries_pvgisWEATHER_50.830_4.350_SA3_0deg_0deg_2005_2023.csv | csv | Repository configuration or metadata. | not_detected | column names inspected; units not explicit | not_applicable | optional | see validation reports if present |
| inputs/weather/aws_1hour_Uccle.csv | csv | Repository configuration or metadata. | timestamped; inspect source for exact step | column names inspected; units not explicit | not_applicable | optional | see validation reports if present |
| config/archetypes.yaml | yaml | Repository configuration or metadata. | not_detected | not_detected | not_applicable | optional | see validation reports if present |
| config/belgian_technology_inputs.yaml | yaml | Belgian residential technology assumptions consumed by run configs. | not_detected | not_detected | technology_case_id | required_for_scenario_runs | see validation reports if present |
| config/model.yaml | yaml | Repository configuration or metadata. | not_detected | not_detected | not_applicable | optional | see validation reports if present |
| config/scenario_tree/climate_windows.yaml | yaml | Scenario-tree contract, dimensions, or comparison definitions. | not_detected | not_detected | scenario tree | required | see validation reports if present |
| config/scenario_tree/comparison_definitions.yaml | yaml | Scenario-tree contract, dimensions, or comparison definitions. | not_detected | not_detected | scenario tree | required | see validation reports if present |
| config/scenario_tree/output5_tariffs.yaml | yaml | Scenario-tree contract, dimensions, or comparison definitions. | not_detected | not_detected | scenario tree | required | see validation reports if present |
| config/scenario_tree/output6_technology_assumptions.yaml | yaml | Scenario-tree contract, dimensions, or comparison definitions. | not_detected | not_detected | scenario tree | required | see validation reports if present |
| config/scenario_tree/output_emissions_factors.yaml | yaml | Scenario-tree contract, dimensions, or comparison definitions. | not_detected | not_detected | scenario tree | required | see validation reports if present |
| config/scenario_tree/realization_policy.yaml | yaml | Scenario-tree contract, dimensions, or comparison definitions. | not_detected | not_detected | scenario tree | required | see validation reports if present |
| config/scenario_tree/scenario_tree_schema.yaml | yaml | Scenario-tree contract, dimensions, or comparison definitions. | not_detected | not_detected | scenario tree | required | see validation reports if present |
| config/scenario_tree/technology_cases.yaml | yaml | Scenario-tree contract, dimensions, or comparison definitions. | not_detected | not_detected | scenario tree | required | see validation reports if present |
| config/thesis.yaml | yaml | Repository configuration or metadata. | not_detected | not_detected | not_applicable | optional | see validation reports if present |
| config/validation/aggregate_fluvius.yaml | yaml | Repository configuration or metadata. | not_detected | not_detected | not_applicable | optional | see validation reports if present |
| config/validation/baseline_annual.yaml | yaml | Repository configuration or metadata. | not_detected | not_detected | not_applicable | optional | see validation reports if present |
| config/validation/kuleuven_high_frequency.yaml | yaml | Repository configuration or metadata. | not_detected | not_detected | not_applicable | optional | see validation reports if present |
| config/validation/richardson.yaml | yaml | Repository configuration or metadata. | not_detected | not_detected | not_applicable | optional | see validation reports if present |
| config/validation/synthetic.yaml | yaml | Repository configuration or metadata. | not_detected | not_detected | not_applicable | optional | see validation reports if present |
| config/validation/technology_ev.yaml | yaml | Repository configuration or metadata. | not_detected | not_detected | not_applicable | optional | see validation reports if present |
| config/validation/technology_pv.yaml | yaml | Repository configuration or metadata. | not_detected | not_detected | not_applicable | optional | see validation reports if present |


# Appendix B - Command reference

```bash
python3 -m model_v3.scenario_tree.validate_scenario_tree --config-root config/scenario_tree
python3 -m model_v3.scenario_tree.create_scenario_tree_space --config-root config/scenario_tree --experiment-root experiments/scenario_tree --print-summary
python3 -m model_v3.scenario_tree.generate_leaf_configs --config-root config/scenario_tree --experiment-root experiments/scenario_tree --climate-processed-root inputs/climate/processed --belgian-technology-inputs config/belgian_technology_inputs.yaml --cohort-size 100 --write-report --print-summary
python3 -m model_v3.scenario_tree.validate_leaf_configs --experiment-root experiments/scenario_tree --config-root config/scenario_tree --climate-processed-root inputs/climate/processed --belgian-technology-inputs config/belgian_technology_inputs.yaml --print-summary
python3 -m model_v3.scenarios.run_scenario_tree --dry-run --print-summary
python3 -m model_v3.scenarios.run_scenario_tree --scenario-leaf-id baseline_1981_2005__historical__tech_current_stock__seed_0000 --print-summary
python3 -m model_v3.scenarios.run_scenario_tree --scenario-leaf-id mid_century_2050_2070__rcp_8_5__tech_high_electrification_pv_ev__seed_0000 --print-summary
python3 -m model_v3.scenarios.run_scenario_tree --all --max-workers 1 --continue-on-error --print-summary
python3 -m model_v3.scenarios.summarize_outputs --experiment-root experiments/scenario_tree --config-root config/scenario_tree --only-successful --write-reports --print-summary
python3 -m model_v3.scenarios.validate_summaries --experiment-root experiments/scenario_tree --print-summary
python3 -m model_v3.scenarios.generate_comparisons --experiment-root experiments/scenario_tree --comparison-definitions config/scenario_tree/comparison_definitions.yaml --write-reports --print-summary
python3 -m model_v3.scenarios.validate_comparisons --experiment-root experiments/scenario_tree --comparison-definitions config/scenario_tree/comparison_definitions.yaml --print-summary
python3 -m model_v3.scenarios.generate_figures --experiment-root experiments/scenario_tree --figures-root figures/scenario_tree --write-metadata --write-captions --print-summary
python3 -m model_v3.scenarios.validate_figures --figures-root figures/scenario_tree --experiment-root experiments/scenario_tree --print-summary
python3 -m model_v3.scenarios.audit_scenario_tree --experiment-root experiments/scenario_tree --config-root config/scenario_tree --figures-root figures/scenario_tree --write-reports --print-summary
python3 -m model_v3.documentation.build_model_handbook --repo-root . --output docs/model_v3_complete_model_handbook.pdf --write-source --write-figures --print-summary
python3 -m model_v3.documentation.validate_model_handbook --handbook docs/model_v3_complete_model_handbook.pdf --source docs/model_v3_complete_model_handbook.md --manifest docs/model_v3_complete_model_handbook_manifest.yaml --print-summary
```


# Appendix C - Metric reference tables

| metric | unit | category | definition | source | aggregation | caveats |
| --- | --- | --- | --- | --- | --- | --- |
| annual_electricity_gross_kWh | kWh | energy | Gross annual electricity demand before netting PV generation. | annual_summary.json or annual_profile.csv via src/model_v3/scenarios/output_reader.py | numeric_distribution | Interpret with scenario, technology, and run coverage context. |
| annual_grid_import_kWh | kWh | grid_energy | Annual electricity imported from the grid. | annual_summary.json or annual_profile.csv via src/model_v3/scenarios/output_reader.py | numeric_distribution | Interpret with scenario, technology, and run coverage context. |
| annual_grid_export_kWh | kWh | grid_energy | Annual electricity exported to the grid. | annual_summary.json or annual_profile.csv via src/model_v3/scenarios/output_reader.py | numeric_distribution | Interpret with scenario, technology, and run coverage context. |
| annual_gas_kWh | kWh | fuel | Annual natural-gas final energy consumption. | annual_summary.json or annual_profile.csv via src/model_v3/scenarios/output_reader.py | numeric_distribution | Interpret with scenario, technology, and run coverage context. |
| annual_useful_heating_kWh | kWh | thermal | Useful thermal energy supplied for space heating. | annual_summary.json or annual_profile.csv via src/model_v3/scenarios/output_reader.py | numeric_distribution | Interpret with scenario, technology, and run coverage context. |
| annual_dhw_kWh | kWh | thermal | Useful thermal domestic hot-water demand when available. | annual_summary.json or annual_profile.csv via src/model_v3/scenarios/output_reader.py | numeric_distribution | Interpret with scenario, technology, and run coverage context. |
| peak_grid_import_W | W | grid_power | Maximum grid import power over the model output year. | annual_summary.json or annual_profile.csv via src/model_v3/scenarios/output_reader.py | numeric_distribution | Interpret with scenario, technology, and run coverage context. |
| winter_peak_grid_import_W | W | grid_power | Maximum grid import power in December, January, or February. | annual_summary.json or annual_profile.csv via src/model_v3/scenarios/output_reader.py | numeric_distribution | Interpret with scenario, technology, and run coverage context. |
| summer_peak_grid_import_W | W | grid_power | Maximum grid import power in June, July, or August. | annual_summary.json or annual_profile.csv via src/model_v3/scenarios/output_reader.py | numeric_distribution | Interpret with scenario, technology, and run coverage context. |
| pv_generation_kWh | kWh | distributed_energy | Annual PV generation. | annual_summary.json or annual_profile.csv via src/model_v3/scenarios/output_reader.py | numeric_distribution | Interpret with scenario, technology, and run coverage context. |
| pv_self_consumption_kWh | kWh | distributed_energy | Annual PV generation consumed locally. | annual_summary.json or annual_profile.csv via src/model_v3/scenarios/output_reader.py | numeric_distribution | Interpret with scenario, technology, and run coverage context. |
| pv_export_fraction | fraction | distributed_energy | Grid export divided by PV generation when PV generation is positive. | annual_summary.json or annual_profile.csv via src/model_v3/scenarios/output_reader.py | numeric_distribution | Interpret with scenario, technology, and run coverage context. |
| ev_charging_kWh | kWh | mobility | Annual EV charging electricity. | annual_summary.json or annual_profile.csv via src/model_v3/scenarios/output_reader.py | numeric_distribution | Interpret with scenario, technology, and run coverage context. |
| mean_T_out_C | C | climate | Mean outdoor air temperature over the canonical climate window. | climate forcing file referenced by run config | numeric_distribution | Interpret with scenario, technology, and run coverage context. |
| winter_mean_T_out_C | C | climate | Mean outdoor air temperature in December, January, and February. | climate forcing file referenced by run config | numeric_distribution | Interpret with scenario, technology, and run coverage context. |
| summer_mean_T_out_C | C | climate | Mean outdoor air temperature in June, July, and August. | climate forcing file referenced by run config | numeric_distribution | Interpret with scenario, technology, and run coverage context. |
| HDD_15 | degree_days | climate_degree_days | Heating degree days using a 15 C base and daily mean outdoor temperature. | climate forcing file referenced by run config | numeric_distribution | Interpret with scenario, technology, and run coverage context. |
| HDD_18 | degree_days | climate_degree_days | Heating degree days using an 18 C base and daily mean outdoor temperature. | climate forcing file referenced by run config | numeric_distribution | Interpret with scenario, technology, and run coverage context. |
| CDD_22 | degree_days | climate_degree_days | Cooling degree days using a 22 C base and daily mean outdoor temperature. | climate forcing file referenced by run config | numeric_distribution | Interpret with scenario, technology, and run coverage context. |
| mean_solar_W_m2 | W/m2 | climate_solar | Mean available solar irradiance over the canonical climate window. | climate forcing file referenced by run config | numeric_distribution | Interpret with scenario, technology, and run coverage context. |


# Appendix D - Known missing items

This appendix lists missing files, missing reports, missing figures, missing validation outputs, or incomplete phases detected from the repository. Missing items are not treated as handbook build failure unless they prevent PDF generation.

| item | status | reason | needed to complete |
| --- | --- | --- | --- |
| none | not_applicable | No expected repository paths from the handbook checklist were missing. | No action required for this checklist. |

Execution coverage gap: the registry/audit evidence supports 37 latest-successful leaves out of 2800 enumerated leaves. This prevents any claim that all scenario leaves have completed.
