# model_v3 Scenario-Tree Design

## Purpose

The `model_v3` scenario tree defines the complete set of residential energy-demand
simulation leaves that later phases may execute. The tree is metadata-first: it
enumerates climate, technology, and stochastic dimensions before any model run
exists. This is required because the thesis simulations will compare many
combinations of climate analysis window, climate pathway, technology assumption,
and stochastic household realization. Without a stable scenario-tree contract,
outputs from different runs would be difficult to audit, compare, or reproduce.

This phase does not run simulations. It creates the identifiers, rules, and
validation logic needed to know which simulations are expected.

## Concepts

A **scenario** is the deterministic parent combination of:

- `climate_window_id`
- `climate_pathway_id`
- `technology_case_id`

A **realization** is the stochastic sampling instance associated with a
reproducible seed. In later phases, the realization seed will control
household/cohort sampling and stochastic demand profiles. In this phase, no
cohorts are generated; only the seed identifiers are defined.

A **scenario leaf** is the unique executable simulation leaf:

- one scenario
- one realization

Each scenario leaf must have a globally unique `scenario_leaf_id`. Stable
identifiers matter because they become the join key between configuration,
simulation outputs, validation reports, figures, and thesis tables. A leaf ID
must be meaningful without opening an output directory.

## Identifier Format

The canonical leaf ID uses four ordered dimensions separated by double
underscores:

```text
{climate_window_id}__{climate_pathway_id}__{technology_case_id}__{realization_id}
```

The deterministic scenario ID omits the realization:

```text
{climate_window_id}__{climate_pathway_id}__{technology_case_id}
```

Examples:

```text
baseline_1981_2005__historical__tech_current_stock__seed_0042
mid_century_2050_2070__rcp_8_5__tech_high_electrification_pv_ev__seed_0042
```

Double underscores are reserved for separating dimensions. Individual dimension
identifiers use lower-case tokens and single underscores.

## Climate Windows and Pathways

Climate windows identify the analysis period. Climate pathways identify the
forcing pathway within that period.

The baseline has special status. It represents the historical reference period
and must use:

```text
climate_window_id = baseline_1981_2005
climate_pathway_id = historical
technology_case_id = tech_current_stock
```

The baseline must not be combined with RCP pathways.

Future windows use RCP pathways:

- `rcp_2_6`
- `rcp_4_5`
- `rcp_8_5`

The RCP identifiers are encoded directly in the leaf ID to make the climate
pathway visible in every expected output key.

## Technology Cases

Technology cases encode the residential stock assumptions used by scenario
leaves. Current-stock and frozen-stock cases use Belgian stock evidence plus
documented PV/EV proxy assumptions. Future electrification cases include
explicit household-level assignment probabilities for heating, DHW, PV, and EV,
but these are counterfactual scenario assumptions rather than forecasts.

The baseline technology case is:

- `tech_current_stock`

Future windows may use:

- `tech_frozen_stock`
- `tech_moderate_electrification`
- `tech_high_electrification_pv_ev`

The technology metadata records whether heat-pump adoption, PV, EV adoption, and
building-envelope improvement are assumed, and it now also stores the
household-assignment probabilities consumed by the stochastic sampler. These
probabilities are applied inside each 100-household cohort, so a scenario leaf
samples a mix of technologies rather than forcing all households to one system.

## Realizations and Cohorts

Realization IDs follow this template:

```text
seed_{seed_index:04d}
```

The committed policy defines seeds `seed_0000` through `seed_0099`. A seed is a
reproducibility handle. Later cohort-generation code must map a given
`realization_id` to the same integer seed whenever the same scenario leaf is run.
This design lets expected leaves be enumerated and validated before household
draws exist.

## Source Windows vs. Canonical Analysis Windows

The scenario tree distinguishes two concepts:

- `source_file_window`: the raw or processed climate-file coverage already
  validated in the climate pipeline.
- `canonical_analysis_window`: the inclusive date interval used for scenario
  statistics and cross-window comparisons.

This distinction is necessary because existing processed climate files may have
overlapping raw periods. In particular:

- the near-future source file may cover `2030-01-01` to `2050-12-31`
- the mid-century source file may cover `2050-01-01` to `2070-12-31`

The year 2050 therefore appears in both source-file windows.

## 2050 Overlap Policy

Raw processed files may overlap, and the known overlapping year is 2050. The
validated processed files do not need to be regenerated for this phase. However,
canonical analysis windows must not overlap because cross-window statistics
would otherwise double-count 2050.

The adopted policy is:

- `near_future_2030_2049` has canonical dates `2030-01-01` to `2049-12-31`.
- `mid_century_2050_2070` has canonical dates `2050-01-01` to `2070-12-31`.
- 2050 belongs to `mid_century_2050_2070`.
- 2050 is excluded from the near-future canonical analysis window.

All canonical dates use inclusive semantics: a timestamp belongs to a window when
it is greater than or equal to `canonical_start` and less than or equal to
`canonical_end`.

## Enumeration Before Execution

Expected leaves are enumerated as the Cartesian product of valid dimensions:

Baseline leaves:

- climate window: `baseline_1981_2005`
- pathway: `historical`
- technology case: `tech_current_stock`
- realization IDs: `seed_0000` through `seed_0099`

Future leaves:

- climate windows: `near_future_2030_2049`, `mid_century_2050_2070`,
  `long_term_2080_2100`
- pathways: `rcp_2_6`, `rcp_4_5`, `rcp_8_5`
- technology cases: `tech_frozen_stock`, `tech_moderate_electrification`,
  `tech_high_electrification_pv_ev`
- realization IDs: `seed_0000` through `seed_0099`

This gives 100 baseline leaves and 2700 future leaves, for 2800 expected
scenario leaves. The validator can write these leaves to an inventory CSV without
running the model.

## Phase Boundary

This phase intentionally does not:

- run residential demand simulations
- generate household cohorts
- generate populated model output data
- modify existing processed climate files
- calibrate numerical technology adoption rates
- decide output storage layout for executed scenario runs

The deliverable is the scenario-tree contract, metadata schema, and validation
tooling that later execution phases must obey.

## Physical Experiment-Space Layout

Phase 2 materializes the scenario-tree contract as a deterministic filesystem
layout under `model_v3/experiments/scenario_tree/`. The filesystem mirrors the
tree so that every expected scenario and scenario leaf has a stable location
before any residential demand simulation exists. This makes the planned
experiment space auditable: later model outputs can be checked against the
manifest instead of inferred from whatever run folders happen to exist.

Scenario IDs and scenario leaf IDs remain separate because they represent
different levels of the tree. The scenario ID names the deterministic parent
combination of climate window, climate pathway, and technology case. The
scenario leaf ID appends the stochastic realization and identifies one
executable run. This separation avoids duplicating deterministic configuration
for every seed while still giving every executable leaf a globally unique path.

`configs/` stores scenario-level folders named by `scenario_id`. Each folder
contains one seed-level placeholder YAML per realization. These files are
traceability stubs only: they record the realization identity and deterministic
seed rule, but they do not generate stochastic cohorts.

`runs/` stores leaf-level folders named by `scenario_leaf_id`. Each run folder
contains `run_config.yaml`, `inputs_manifest.yaml`, `outputs/`, and `logs/`.
The YAML files record the metadata needed by later phases to bind climate
forcing, technology assumptions, and realization policy to a specific leaf.
`outputs/` and `logs/` are intentionally empty when Phase 2 finishes.

`manifests/` stores `scenario_tree_manifest.yaml` and
`scenario_leaf_index.csv`. The manifest records the source metadata files,
identifier convention, counts, temporal-window policy, 2050 overlap handling,
and the fact that zero simulations were run. The leaf index is the tabular join
key for later reproducibility: it maps every scenario leaf to its scenario
config, run folder, input manifest, run config, output folder, climate forcing
metadata reference, and technology metadata reference.

`summaries/` is reserved for later scenario-level and comparison-level
aggregations. Phase 2 creates `summaries/scenario_level/` and
`summaries/comparison_level/` but does not write analytical results there.

The Phase 2 layout creates folders and metadata placeholders only. It does not
modify processed climate files, execute the energy-demand model, create model
outputs, or change the 2050 overlap policy. The near-future canonical analysis
window still excludes 2050, and the mid-century canonical analysis window still
includes 2050.

Example:

```text
experiments/scenario_tree/
  configs/
    mid_century_2050_2070__rcp_8_5__tech_high_electrification_pv_ev/
      seed_0042.yaml
  runs/
    mid_century_2050_2070__rcp_8_5__tech_high_electrification_pv_ev__seed_0042/
      inputs_manifest.yaml
      run_config.yaml
      outputs/
      logs/
```

## Thesis figure generation and visualisation workflow

Phase 7 generates thesis-ready figures directly from the standardized Phase 5
summary tables and Phase 6 comparison tables. The workflow is script-driven so
that every figure can be regenerated from committed CSV and YAML inputs. Manual
spreadsheet editing is not part of the workflow because it would break the
traceable link between scenario IDs, scenario leaf IDs, validation reports, and
the thesis figures.

Figures are written under:

```text
figures/scenario_tree/
```

The figure root is organized by analytical category:

- `structure/` for the scenario-tree structure diagram.
- `climate/` for temperature, heating degree day, cooling degree day, and solar
  forcing comparisons.
- `annual_demand/` for annual electricity, gas, useful heating, and domestic
  hot-water demand.
- `grid_impact/` for peak import, grid import/export balance, PV
  self-consumption/export, and EV charging demand.
- `uncertainty/` for P10/P50/P90 stochastic robustness bands.
- `infrastructure_stress/` for winter peak, summer peak emergence, and combined
  stress-case figures.
- `metadata/` for figure provenance tables.

The plotting script reads the standardized realization-level and scenario-level
summaries:

```text
model_v3/experiments/scenario_tree/summaries/realization_level/scenario_leaf_metrics.csv
model_v3/experiments/scenario_tree/summaries/scenario_level/scenario_aggregate_metrics.csv
```

It also reads Phase 6 comparison outputs where the figure requires comparison
or stochastic robustness data, especially:

```text
model_v3/experiments/scenario_tree/summaries/comparison_level/stochastic_robustness/stochastic_uncertainty_bands.csv
```

Stable filenames are required because thesis text, captions, and cross
references should not change when figures are regenerated. The scripts therefore
write deterministic names such as `climate_temperature_by_window_rcp.pdf` and
never include timestamps in figure filenames.

Every generated figure is accompanied by provenance metadata in:

```text
figures/scenario_tree/metadata/figure_metadata.csv
```

The metadata records the stable figure ID, source CSV/YAML files, metrics used,
scenario filters, generation timestamp, script name, git state when available,
row count, caption ID, status, and warnings. This makes the thesis figures
auditable without inspecting the plotting code manually.

Draft thesis captions are written to:

```text
figures/scenario_tree/thesis_caption_drafts.md
```

Each caption identifies the figure file, metrics, scenario grouping, whether
values represent means or P10/P50/P90 bands, and an interpretation note. Climate
captions explicitly preserve the canonical 2050 overlap policy: near-future
figures use the 2030-2049 canonical window and exclude 2050, while mid-century
figures use the 2050-2070 canonical window and include 2050.

Regenerate all figures with:

```bash
python3 -m model_v3.scenarios.generate_figures \
  --experiment-root model_v3/experiments/scenario_tree \
  --figures-root figures/scenario_tree \
  --write-metadata \
  --write-captions \
  --print-summary
```

Validate the generated figure set with:

```bash
python3 -m model_v3.scenarios.validate_figures \
  --figures-root figures/scenario_tree \
  --experiment-root model_v3/experiments/scenario_tree \
  --print-summary
```

The validator checks required directories and figure files, stable filenames,
absence of timestamped names, metadata coverage, source-file existence, caption
coverage, absence of manual spreadsheet dependencies, and the 2050 policy. It
does not run simulations and does not modify raw model outputs or processed
climate files.

## Scenario-leaf Executable Configuration Files

Phase 3 converts the abstract scenario leaves from `scenario_leaf_index.csv`
into executable YAML configuration files without running the model. Every row in
the Phase 2 leaf index gets exactly one leaf-level config:

```text
model_v3/experiments/scenario_tree/runs/{scenario_leaf_id}/run_config.yaml
```

The config records the canonical scenario dimensions, the resolved climate
forcing CSV, canonical analysis dates, source file window, technology case,
Belgian technology input YAML, stochastic seed, cohort size, output folders,
model options, validation metadata, and Phase 3 provenance. The paired
`inputs_manifest.yaml` in the same run folder records the input files and their
existence checks. The scenario-level seed config under
`configs/{scenario_id}/{realization_id}.yaml` points to the executable
`run_config.yaml`, the input manifest, and the output directory.

Climate forcing resolution is deterministic. If climate-window metadata records
an explicit processed forcing file, that path is used. Otherwise the resolver
searches `inputs/climate/processed/` for CSV files matching the climate pathway,
window identity, and source-file window. Sidecar `.metadata.json` files are used
when filenames do not contain the full year range. Resolution must produce
exactly one existing CSV; missing or ambiguous matches fail generation.

Technology resolution keeps the scenario-tree case ID separate from the model
technology input YAML. The config stores both the scenario case, such as
`tech_high_electrification_pv_ev`, and the concrete Belgian input file:

```text
config/model_v3/belgian_technology_inputs.yaml
```

Generation fails if the technology case is not defined, if a baseline leaf uses
anything other than `tech_current_stock`, if a future leaf uses
`tech_current_stock` without explicit metadata permission, or if the Belgian
technology YAML is missing.

The stochastic block stores the realization ID, integer seed index, seed value,
and requested cohort size. Cohort generation is still deferred to the simulation
phase. `model_options.execute_simulation` is therefore always `false` in Phase 3;
the configs are executable inputs for a later runner, not evidence that a run
has happened.

Validation checks that no generated config references missing climate or
technology inputs, all expected YAML sections are present, every output and log
directory exists, and every seed-level config points to its leaf-level run
config. It also preserves the baseline/future separation and the 2050 policy:
near-future configs use `2030-01-01` to `2049-12-31`, while mid-century configs
start at `2050-01-01`.

Generate configs with:

```bash
python3 -m src.model_v3.scenario_tree.generate_leaf_configs \
  --config-root config/model_v3/scenario_tree \
  --experiment-root model_v3/experiments/scenario_tree \
  --climate-processed-root inputs/climate/processed \
  --belgian-technology-inputs config/model_v3/belgian_technology_inputs.yaml \
  --cohort-size 100 \
  --write-report \
  --print-summary
```

Validate generated configs with:

```bash
python3 -m src.model_v3.scenario_tree.validate_leaf_configs \
  --experiment-root model_v3/experiments/scenario_tree \
  --config-root config/model_v3/scenario_tree \
  --climate-processed-root inputs/climate/processed \
  --belgian-technology-inputs config/model_v3/belgian_technology_inputs.yaml \
  --print-summary
```

## Scenario-tree Runner and Reproducible Orchestration

Phase 4 adds the runner that connects the Phase 3 leaf configs to the existing
`model_v3` simulation code. The runner is needed because a scenario leaf should
not require manually reconstructing climate files, output paths, stochastic
seeds, or technology assumptions at the command line. Instead, it consumes:

```text
model_v3/experiments/scenario_tree/runs/{scenario_leaf_id}/run_config.yaml
model_v3/experiments/scenario_tree/runs/{scenario_leaf_id}/inputs_manifest.yaml
```

The stable entrypoint is:

```bash
python3 -m model_v3.scenarios.run_scenario_tree
```

The repository keeps implementation code under `src/model_v3`, while
`model_v3/experiments` stores artifacts. A small compatibility package keeps the
documented `model_v3.scenarios...` module path working from the repository root.

The runner supports three modes. A dry-run loads the leaf index, validates every
selected `run_config.yaml` and `inputs_manifest.yaml`, checks climate forcing
and Belgian technology input files, checks output/log paths, applies registry
eligibility rules, and prints a deterministic plan. It does not call the model
and does not write model outputs. Repeated dry-runs over unchanged metadata
produce the same leaf order because selection is sorted by `scenario_leaf_id`.

Run a dry-run with:

```bash
python3 -m model_v3.scenarios.run_scenario_tree --dry-run --print-summary
```

Single-leaf mode validates one leaf, sets the configured seed, translates the
Phase 3 leaf config into the native model config shape, calls the existing
annual simulation engine, and writes outputs only below that leaf's `outputs/`
directory. Per-attempt logs are written under:

```text
model_v3/experiments/scenario_tree/runs/{scenario_leaf_id}/logs/attempts/{run_attempt_id}/
```

Run the baseline leaf with:

```bash
python3 -m model_v3.scenarios.run_scenario_tree \
  --scenario-leaf-id baseline_1981_2005__historical__tech_current_stock__seed_0000 \
  --print-summary
```

Run a future leaf with:

```bash
python3 -m model_v3.scenarios.run_scenario_tree \
  --scenario-leaf-id mid_century_2050_2070__rcp_8_5__tech_high_electrification_pv_ev__seed_0000 \
  --print-summary
```

Batch mode iterates through selected leaves from the leaf index in deterministic
order. Phase 4 is intentionally serial: `--max-workers` defaults to `1`, and
values other than `1` are rejected. Parallelism is deferred so that provenance,
logging, skip semantics, and failure handling are simple and auditable before
concurrent execution is introduced.

Run a full serial batch later with:

```bash
python3 -m model_v3.scenarios.run_scenario_tree \
  --all \
  --max-workers 1 \
  --continue-on-error \
  --print-summary
```

The run registry is persistent:

```text
model_v3/experiments/scenario_tree/manifests/run_registry.csv
model_v3/experiments/scenario_tree/manifests/run_registry_summary.yaml
```

The registry records one row per run attempt. A row includes the scenario leaf
ID, parsed dimensions, start and end timestamps, duration, status, skip reason,
git commit and dirty-tree state when available, config and input hashes, random
seed, cohort size, model version, output path, log path, and any error type or
message. The runner writes a `running` row at attempt start and updates that row
to `success` or `failed` when the attempt finishes. Interrupted attempts can
therefore leave a visible stale `running` row.

Successful leaves are skipped on later non-force runs. Failed leaves remain
eligible for retry. Use `--force` to rerun a successful leaf. If a stale
`running` status is encountered, the runner reports it and requires `--force` or
`--ignore-stale-running` before rerunning that leaf.

Failures are contained to the affected leaf. A failed leaf writes its registry
row, stdout/stderr logs, and `runner_status.yaml`; it does not delete the run
directory or partial outputs. Batch mode stops on the first failure by default.
With `--continue-on-error`, the runner records the failure and continues to the
next selected leaf.

## Standardized outputs and scenario-level summaries

Phase 5 turns raw scenario-leaf outputs into comparable analysis tables. The
Phase 4 runner writes model-native artifacts under each leaf's `outputs/`
directory. Those files are useful for debugging a single run, but they are not
directly convenient for cross-scenario analysis because later plots need one
stable row per successful leaf with identical metric columns, units, and
diagnostics.

The standardization entrypoint is:

```bash
python3 -m model_v3.scenarios.summarize_outputs \
  --experiment-root model_v3/experiments/scenario_tree \
  --config-root config/model_v3/scenario_tree \
  --only-successful \
  --write-reports \
  --print-summary
```

It reads the scenario leaf index, run registry, each successful leaf's
`run_config.yaml`, `inputs_manifest.yaml`, and raw outputs. It does not run the
model, rerun failed leaves, modify processed climate files, or change scenario
IDs. The latest actual registry status is used, so `skipped` rows that only
mean `already_successful` do not hide an earlier successful run.

For each successful leaf the summarizer writes exactly one row to:

```text
model_v3/experiments/scenario_tree/runs/{scenario_leaf_id}/outputs/standardized_leaf_summary.csv
```

The row includes scenario metadata, seed metadata, provenance fields, selected
input paths, and standardized metrics:

```text
annual_electricity_gross_kWh
annual_grid_import_kWh
annual_grid_export_kWh
annual_gas_kWh
annual_useful_heating_kWh
annual_dhw_kWh
peak_grid_import_W
winter_peak_grid_import_W
summer_peak_grid_import_W
pv_generation_kWh
pv_self_consumption_kWh
pv_export_fraction
ev_charging_kWh
mean_T_out_C
winter_mean_T_out_C
summer_mean_T_out_C
HDD_15
HDD_18
CDD_22
mean_solar_W_m2
```

The current model runner writes `annual_profile.csv` and
`annual_summary.json`. Energy and grid metrics are therefore mapped from those
files. Gross electricity, grid import/export, gas, PV, EV, useful heating, and
DHW energy are taken from summary keys when present and otherwise derived by
integrating explicit power columns in the profile. Power metrics use watts; kW
columns are converted to W if encountered. Useful heating prefers
`Q_heating_supplied_W`, and DHW prefers useful thermal demand from
`Q_dhw_demand_W` or `dhw_thermal_kWh`. PV-free and EV-free technology cases
produce valid zero PV/EV metrics with diagnostic policies rather than missing
values.

Climate sensitivity metrics are computed from the climate forcing CSV
referenced by the leaf manifest/config, not from the one-year model output
profile. The calculator detects the timestamp, temperature, and solar columns
and filters rows to the canonical inclusive analysis window. The 2050 overlap
policy is preserved explicitly: near-future metrics use `2030-01-01` through
`2049-12-31`, so 2050 is excluded, while mid-century metrics use
`2050-01-01` through `2070-12-31`, so 2050 is included. Heating and cooling
degree days are computed from daily mean outdoor temperature.

The flat realization-level table is written to:

```text
model_v3/experiments/scenario_tree/summaries/realization_level/scenario_leaf_metrics.csv
model_v3/experiments/scenario_tree/summaries/realization_level/scenario_leaf_metrics_schema.yaml
```

It contains one row per successful scenario leaf and can be grouped by climate
window, climate pathway/RCP, technology case, realization ID, or scenario ID.

The scenario-level aggregate table is written to:

```text
model_v3/experiments/scenario_tree/summaries/scenario_level/scenario_aggregate_metrics.csv
```

It groups by `scenario_id`, `climate_window_id`, `climate_pathway_id`, and
`technology_case_id`. For each numeric metric it reports count, mean, median,
standard deviation, min, max, p05, p10, p90, and p95. It also records successful,
failed, missing, and coverage counts using the run registry and leaf index.

The future-vs-baseline comparison table is written to:

```text
model_v3/experiments/scenario_tree/summaries/comparison_level/baseline_comparison_metrics.csv
```

Baseline rows are omitted from this table. Each successful future leaf is
matched to the baseline leaf with the same `realization_id`; for example,
`seed_0042` is matched to
`baseline_1981_2005__historical__tech_current_stock__seed_0042`. If that
baseline is unavailable, the row is retained with `baseline_available=false`
and no deltas. If the baseline metric is zero, absolute deltas are still
reported and percentage deltas are left blank with a diagnostic flag.

Validate standardized summaries with:

```bash
python3 -m model_v3.scenarios.validate_summaries \
  --experiment-root model_v3/experiments/scenario_tree \
  --print-summary
```

The validator checks that every latest-successful run has exactly one
per-leaf summary, required metadata and metric columns exist, metric columns
are numeric, required columns are not entirely missing, successful leaves are
present in the flat table, aggregate counts match registry status counts,
baseline comparisons use the same realization seed, and near-future and
mid-century rows respect the 2050 policy. Magnitude checks are reported as
warnings unless they indicate a structural problem. The validation report is
written to:

```text
model_v3/experiments/scenario_tree/manifests/summary_validation_report.md
model_v3/experiments/scenario_tree/manifests/summary_validation_report.yaml
```

## Analytical Comparison Framework

The thesis comparisons are defined separately from simulation execution so that
interpretation does not depend on ad hoc spreadsheet joins. Phase 6 adds a
machine-readable comparison contract at:

```text
config/model_v3/scenario_tree/comparison_definitions.yaml
```

The generator consumes the standardized Phase 5 summary tables only. It does not
run simulations, modify raw model outputs, modify processed climate files, or
change any scenario IDs or scenario leaf IDs. Missing comparison groups are not
silently skipped: they are recorded in diagnostics, and strict generation fails
unless missing groups are explicitly allowed.

The climate-only comparison measures climate-window and RCP pathway effects
against the historical baseline. The baseline is always:

```text
baseline_1981_2005__historical__tech_current_stock
```

Future climate-only scenarios use `tech_frozen_stock`, not
`tech_current_stock`. This is necessary because `tech_current_stock` is
baseline-only in the Phase 1-5 metadata. A future frozen-stock case represents a
counterfactual where climate changes while residential technology assumptions
are held fixed relative to the baseline.

The technology-only comparison measures the effect of technology assumptions
conditional on the same future climate window and RCP pathway. The reference
future technology case is `tech_frozen_stock`; compared cases are
`tech_moderate_electrification` and
`tech_high_electrification_pv_ev`. Compared and reference leaves are matched by
the same `realization_id` and must share the same climate window and RCP.

The combined stress-case comparison measures the joint effect of a long-term
high-climate, high-electrification PV/EV case against the historical baseline:

```text
long_term_2080_2100__rcp_8_5__tech_high_electrification_pv_ev
```

Baseline, future, reference, and stress leaves are matched by
`realization_id`. This preserves stochastic pairing: for example,
`seed_0007` in a future scenario is compared with `seed_0007` in the baseline
or frozen-stock reference. Absolute deltas are computed as:

```text
delta_abs = compared_value - reference_value
```

Percentage changes are computed as:

```text
delta_pct = 100 * (compared_value - reference_value) / reference_value
```

If the baseline or reference value is zero, the percentage change is left blank
and the row records a zero-division diagnostic flag. Absolute deltas remain
available wherever both values exist.

Stochastic robustness is quantified within each scenario group across
realizations. For every major metric the spread table reports count, mean,
standard deviation, min, max, P05, P10, P50, P90, P95, IQR, P90-P10, and
coefficient of variation. P10/P50/P90 bands are written for thesis uncertainty
plots. If the mean is zero, coefficient of variation is left blank and the
diagnostic YAML records the flag.

Generated comparison artifacts are written under:

```text
model_v3/experiments/scenario_tree/summaries/comparison_level/climate_only/
model_v3/experiments/scenario_tree/summaries/comparison_level/technology_only/
model_v3/experiments/scenario_tree/summaries/comparison_level/combined_stress_case/
model_v3/experiments/scenario_tree/summaries/comparison_level/stochastic_robustness/
```

The global index files list every output table and row count:

```text
model_v3/experiments/scenario_tree/summaries/comparison_level/comparison_index.csv
model_v3/experiments/scenario_tree/summaries/comparison_level/comparison_index.yaml
```

The comparison validation report is written to:

```text
model_v3/experiments/scenario_tree/manifests/comparison_validation_report.md
model_v3/experiments/scenario_tree/manifests/comparison_validation_report.yaml
```

Run the comparison generator with:

```bash
python3 -m model_v3.scenarios.generate_comparisons \
  --experiment-root model_v3/experiments/scenario_tree \
  --comparison-definitions config/model_v3/scenario_tree/comparison_definitions.yaml \
  --write-reports \
  --print-summary
```

Run the independent comparison validator with:

```bash
python3 -m model_v3.scenarios.validate_comparisons \
  --experiment-root model_v3/experiments/scenario_tree \
  --comparison-definitions config/model_v3/scenario_tree/comparison_definitions.yaml \
  --print-summary
```

Validation checks that comparison definitions are parseable, referenced metrics
exist in `scenario_leaf_metrics.csv`, referenced technology cases and climate
windows exist in Phase 1 metadata, climate pathways are valid for each window,
future comparisons do not misuse `tech_current_stock`, generated output files
exist, delta and percentage tables contain required metadata, uncertainty tables
contain P10/P50/P90 columns, paired rows use the same `realization_id`, duplicate
comparison rows are absent, and the canonical 2050 policy is preserved:
near-future excludes 2050 while mid-century includes it.

## Methodological audit and thesis integration

The scenario-tree layer now includes a methodological audit and thesis
integration layer. This layer is necessary because the scenario tree is not only
an execution mechanism; it is also the evidence chain that supports thesis
claims about climate, technology, and stochastic uncertainty. The audit checks
whether each successful output can answer four result-level questions:

```text
1. Which climate forcing was used?
2. Which technology assumptions were active?
3. Which stochastic seed/cohort generated this result?
4. Which exact model/config produced the output?
```

The audit command is:

```bash
python3 -m model_v3.scenarios.audit_scenario_tree \
  --experiment-root model_v3/experiments/scenario_tree \
  --config-root config/model_v3/scenario_tree \
  --figures-root figures/scenario_tree \
  --write-reports \
  --print-summary
```

The audit writes the thesis-facing run manifest and validation report:

```text
reports/scenario_tree_run_manifest.md
reports/scenario_tree_validation_report.md
```

It also writes machine-readable audit outputs:

```text
reports/scenario_tree_traceability_matrix.csv
reports/scenario_tree_audit_summary.yaml
```

The traceability matrix has one row per latest-successful scenario leaf. It
joins the run registry, run config, inputs manifest, standardized per-leaf
summary, climate forcing file, technology input file, hashes, model version, and
Git provenance where available. If a latest-successful output cannot resolve
climate forcing, technology assumptions, stochastic seed/cohort metadata, or the
exact config/model provenance, the audit marks that row incomplete and reports a
high-severity warning.

Assumptions are documented in:

```text
docs/model_v3_scenario_tree_assumptions.md
```

This document gives each assumption an ID, statement, rationale, encoded source,
affected phases, affected outputs, validation or audit check, and limitation.
The methodology document and thesis subsection are:

```text
docs/model_v3_scenario_tree_methodology.md
docs/thesis_methodology_scenario_tree_subsection.md
docs/thesis_methodology_scenario_tree_subsection.tex
```

The run manifest supports reproducibility by reporting implemented counts from
the scenario-tree manifest, scenario-leaf index, run registry, standardized
summaries, comparison tables, and figure metadata. It distinguishes enumerated
scenario leaves from latest-successful leaves, so the documentation does not
claim that all leaves have run unless the registry proves it.

The validation report supports defensibility by consolidating schema,
directory/path, leaf config, dry-run, registry, summary, comparison, figure,
2050-policy, and traceability validation status. It explicitly states that these
checks validate experiment traceability and methodological consistency, not
external measured-data accuracy.

The 2050 overlap policy is carried through the full pipeline. Raw processed
source files may overlap in 2050, but canonical analysis windows do not:
near-future ends on `2049-12-31`, mid-century starts on `2050-01-01`, and 2050
is assigned only to the mid-century canonical window for cross-window statistics
and comparisons. This policy is documented in the methodology document,
assumptions register, validation report, and thesis subsection draft.

## Complete model handbook

Phase 9 adds a generated complete model handbook for thesis study and supervisor
discussion. The handbook exists to consolidate the architecture, scenario-tree
framework, model physics, inputs, outputs, standardized metrics, comparisons,
validation layers, caveats, terminology, and usage commands in one reproducible
document. It is intentionally repository-grounded: factual statements about
implemented files, run coverage, figures, reports, and validation status are
derived from local configs, manifests, summaries, registry rows, reports, and
figure metadata.

The generator is:

```bash
python3 -m model_v3.documentation.build_model_handbook \
  --repo-root . \
  --output docs/model_v3_complete_model_handbook.pdf \
  --write-source \
  --write-figures \
  --print-summary
```

It writes:

```text
docs/model_v3_complete_model_handbook.pdf
docs/model_v3_complete_model_handbook.md
docs/model_v3_complete_model_handbook.tex
docs/model_v3_complete_model_handbook_manifest.yaml
docs/model_v3_handbook_assets/
docs/model_v3_supervisor_briefing.md
docs/model_v3_supervisor_briefing.pdf
```

The manifest records the generation timestamp, Git state when available, source
files inspected, figures included, summary tables included, reports included,
missing expected files, warnings, and PDF backend. The handbook generator does
not run simulations, does not modify raw model outputs, does not modify
processed climate files, and does not change scenario IDs.

The handbook supports thesis writing by providing study notes, metric reference
tables, terminology, caveats, improvement suggestions, and command references.
It supports supervisor discussion by distinguishing implemented and verified
items from implemented-but-partial execution, missing reports, and future work.
It explicitly preserves the 2050 canonical-window policy and avoids claiming
that all scenario leaves have completed unless the run registry proves it.

Validate the handbook with:

```bash
python3 -m model_v3.documentation.validate_model_handbook \
  --handbook docs/model_v3_complete_model_handbook.pdf \
  --source docs/model_v3_complete_model_handbook.md \
  --manifest docs/model_v3_complete_model_handbook_manifest.yaml \
  --print-summary
```
