# Model v3 Scenario-Tree Assumptions

This register makes the scenario-tree assumptions explicit. Each assumption is traceable to a config file, manifest, validation report, or generated audit output.

## Climate assumptions

| ID | Title | Statement | Rationale | Encoded in | Affected phases | Affected outputs | Validation or audit check | Limitations |
|---|---|---|---|---|---|---|---|---|
| ASSUMP-CLIM-001 | Explicit climate forcing per leaf | Each scenario leaf resolves to one climate forcing file. | Prevents ambiguous climate attribution in output metrics. | `run_config.yaml`, `inputs_manifest.yaml`, `run_registry.csv` | Config, execution, summary, audit | Leaf summaries, aggregates, comparisons, figures | Traceability matrix checks `climate_forcing_file`, existence, and hash. | Does not assess climate-model accuracy. |
| ASSUMP-CLIM-002 | Historical forcing is baseline only | The historical pathway is used only with `baseline_1981_2005`. | Keeps baseline interpretation separate from future RCP projections. | `scenario_tree_schema.yaml`, `climate_windows.yaml` | Enumeration, validation, comparison | Baseline summaries and deltas | Scenario-tree validation and config validation. | Historical period remains modelled through processed climate inputs, not external demand validation. |

## Temporal-window assumptions

| ID | Title | Statement | Rationale | Encoded in | Affected phases | Affected outputs | Validation or audit check | Limitations |
|---|---|---|---|---|---|---|---|---|
| ASSUMP-TEMP-001 | Non-overlapping canonical climate windows | Although processed source files may overlap in 2050, the canonical near-future analysis window excludes 2050 and the mid-century window includes it. | Prevents double-counting 2050 in cross-window statistics while preserving validated processed climate files. | `climate_windows.yaml` | Config, summary, comparison, figure, audit | Climate metrics, scenario aggregates, comparison tables, climate figures | Scenario-tree validation, summary validation, comparison validation, figure validation, audit report. | Source-file coverage can still overlap; only canonical statistics are non-overlapping. |
| ASSUMP-TEMP-002 | Inclusive canonical dates | A timestamp belongs to a canonical window when it is greater than or equal to the start date and less than or equal to the end date. | Gives deterministic year inclusion rules. | `climate_windows.yaml` | Summary, comparison | Climate metrics and window-level figures | Summary climate-included-years checks. | Depends on timestamp parsing in summary code. |

## RCP pathway assumptions

| ID | Title | Statement | Rationale | Encoded in | Affected phases | Affected outputs | Validation or audit check | Limitations |
|---|---|---|---|---|---|---|---|---|
| ASSUMP-RCP-001 | Future pathways are RCP2.6, RCP4.5, and RCP8.5 | Future windows are enumerated under `rcp_2_6`, `rcp_4_5`, and `rcp_8_5`. | Covers low, medium, and high forcing branches in a stable contract. | `scenario_tree_schema.yaml`, `climate_windows.yaml` | Enumeration, config, comparison | Future summaries, climate-only comparisons, climate figures | Scenario-tree validation. | RCP branches are climate pathways, not probabilistic forecasts. |

## Technology-case assumptions

| ID | Title | Statement | Rationale | Encoded in | Affected phases | Affected outputs | Validation or audit check | Limitations |
|---|---|---|---|---|---|---|---|---|
| ASSUMP-TECH-001 | Technology cases are explicit branch metadata | Each leaf records `technology_case_id`, technology metadata file, and Belgian technology input YAML. | Makes technology assumptions traceable for every output. | `technology_cases.yaml`, run configs, inputs manifests | Config, execution, summary, audit | Energy metrics, comparisons, figures | Traceability matrix checks technology fields and hashes. | Technology metadata is qualitative unless calibrated elsewhere. |

## Baseline-stock assumptions

| ID | Title | Statement | Rationale | Encoded in | Affected phases | Affected outputs | Validation or audit check | Limitations |
|---|---|---|---|---|---|---|---|---|
| ASSUMP-BASE-001 | Current stock is baseline-only | `tech_current_stock` is used for the historical baseline and is not a future technology case unless metadata explicitly permits future use. | Prevents accidental future current-stock branches that conflict with the scenario contract. | `technology_cases.yaml`, `scenario_tree_schema.yaml` | Enumeration, config, comparison | Baseline summaries and baseline deltas | Config validation and comparison validation. | Current-stock interpretation depends on model v3 baseline inputs. |

## Frozen-stock future assumption

| ID | Title | Statement | Rationale | Encoded in | Affected phases | Affected outputs | Validation or audit check | Limitations |
|---|---|---|---|---|---|---|---|---|
| ASSUMP-FROZEN-001 | Frozen stock isolates climate effects | Future climate-only comparisons use `tech_frozen_stock` as the future technology case. | Allows future climate forcing to vary while holding technology assumptions fixed relative to the baseline stock. | `technology_cases.yaml`, `comparison_definitions.yaml` | Comparison, figure, thesis interpretation | Climate-only deltas and climate figures | Comparison validation checks future climate-only comparisons use `tech_frozen_stock`. | It is a counterfactual, not a forecast of future technology adoption. |

## Electrification assumptions

| ID | Title | Statement | Rationale | Encoded in | Affected phases | Affected outputs | Validation or audit check | Limitations |
|---|---|---|---|---|---|---|---|---|
| ASSUMP-ELEC-001 | Moderate electrification branch | `tech_moderate_electrification` represents future heat-pump uptake and building-envelope improvement. | Provides a technology-adoption sensitivity branch. | `technology_cases.yaml` | Enumeration, config, comparison | Heating, electricity, grid-import metrics | Scenario-tree and config validation. | Metadata does not by itself prove calibrated adoption rates. |
| ASSUMP-ELEC-002 | High electrification stress branch | `tech_high_electrification_pv_ev` represents heat pumps, PV, EV charging, and envelope improvement. | Supports a combined climate and electrification stress case. | `technology_cases.yaml`, `comparison_definitions.yaml` | Enumeration, config, comparison, figure | Grid impact and stress-case metrics | Comparison validation and figure metadata. | Stress-case interpretation should not be framed as a central forecast. |

## PV and EV assumptions

| ID | Title | Statement | Rationale | Encoded in | Affected phases | Affected outputs | Validation or audit check | Limitations |
|---|---|---|---|---|---|---|---|---|
| ASSUMP-PVEV-001 | PV and EV metrics are case-dependent | PV and EV outputs are interpreted according to the active technology case and metric policy fields. | Avoids treating zero PV or EV outputs in non-PV/EV cases as missing data. | `technology_cases.yaml`, `scenario_leaf_metrics.csv` | Summary, comparison, figure | PV generation, self-consumption, export fraction, EV charging | Summary validation checks required metrics and policy fields. | Zero values can mean not applicable rather than empirical absence. |

## Stochastic realization assumptions

| ID | Title | Statement | Rationale | Encoded in | Affected phases | Affected outputs | Validation or audit check | Limitations |
|---|---|---|---|---|---|---|---|---|
| ASSUMP-STOCH-001 | Seed ID is reproducible | `seed_0000` through `seed_0099` map to integer seed values 0 through 99. | Enables reproducible stochastic sampling and scenario pairing. | `realization_policy.yaml`, run configs, registry | Config, execution, summary, comparison | All per-leaf metrics and stochastic bands | Traceability matrix checks `realization_id`, `seed_index`, and `seed_value`. | Seed identity does not imply independent climate uncertainty. |
| ASSUMP-STOCH-002 | Comparisons match by realization ID | Baseline, reference, and future leaves are paired by `realization_id` where pairwise deltas are computed. | Reduces stochastic mismatch when estimating climate or technology deltas. | `comparison_definitions.yaml` | Comparison | Delta and percentage-change tables | Comparison validation. | Pairing only works where both leaves have successful summaries. |

## Cohort-size assumptions

| ID | Title | Statement | Rationale | Encoded in | Affected phases | Affected outputs | Validation or audit check | Limitations |
|---|---|---|---|---|---|---|---|---|
| ASSUMP-COHORT-001 | Cohort size is provenance | Each successful run records `cohort_size` in config, registry, summary, and audit matrix. | Makes stochastic scale auditable for each output. | Run configs, inputs manifests, registry, summaries | Execution, summary, audit | Per-leaf metrics and stochastic summaries | Traceability matrix checks `cohort_size`. | The audit does not evaluate whether cohort size is statistically sufficient. |

## Output metric assumptions

| ID | Title | Statement | Rationale | Encoded in | Affected phases | Affected outputs | Validation or audit check | Limitations |
|---|---|---|---|---|---|---|---|---|
| ASSUMP-METRIC-001 | Standardized metrics are comparison source | Comparisons and figures consume standardized summary tables rather than raw outputs. | Provides consistent column names and scenario metadata across leaves. | `scenario_leaf_metrics.csv`, `scenario_aggregate_metrics.csv` | Summary, comparison, figure | All comparison and figure outputs | Summary, comparison, and figure validation. | Incorrect raw-to-standard mapping would propagate unless caught by validation. |

## Baseline comparison assumptions

| ID | Title | Statement | Rationale | Encoded in | Affected phases | Affected outputs | Validation or audit check | Limitations |
|---|---|---|---|---|---|---|---|---|
| ASSUMP-COMP-001 | Baseline comparison uses historical current stock | Future-vs-baseline deltas reference `baseline_1981_2005__historical__tech_current_stock`. | Gives a single historical reference for future climate and technology effects. | `comparison_definitions.yaml` | Comparison, thesis interpretation | Baseline deltas, percentage changes, stress-case outputs | Comparison validation. | Missing successful baseline realizations limit available pairwise comparisons. |

## Uncertainty-band assumptions

| ID | Title | Statement | Rationale | Encoded in | Affected phases | Affected outputs | Validation or audit check | Limitations |
|---|---|---|---|---|---|---|---|---|
| ASSUMP-UNC-001 | P10/P50/P90 represent stochastic robustness | P10, P50, and P90 bands summarize spread across successful stochastic realizations within a scenario. | Communicates behavioural/cohort sensitivity without overclaiming external probability. | `comparison_definitions.yaml`, stochastic robustness tables | Comparison, figure, thesis interpretation | Uncertainty-band tables and figures | Comparison validation checks P10/P50/P90 bands. | Bands are conditional on the available successful runs and scenario assumptions. |

## Known limitations and non-claims

| ID | Title | Statement | Rationale | Encoded in | Affected phases | Affected outputs | Validation or audit check | Limitations |
|---|---|---|---|---|---|---|---|---|
| ASSUMP-LIM-001 | No unsupported full-completion claim | Reports must distinguish enumerated leaves from latest-successful leaves. | Prevents overstating executed experiment coverage. | Run registry, audit summary | Reporting, thesis writing | Run manifest, validation report, thesis text | Audit counts registry attempts and latest-successful leaves. | Completion can change after new runs. |
| ASSUMP-LIM-002 | No external validation claim | Scenario-tree validation checks contract, traceability, summaries, comparisons, and figures; it does not validate demand accuracy against external measured data. | Keeps methodology claims aligned with available evidence. | Validation report, methodology docs | Thesis interpretation | Methodology and QA reports | Documentation tests and validation report. | External model validation may exist elsewhere but is not claimed by this phase. |
