# Model v3 Scenario-Tree Methodology

## Purpose of the scenario-tree layer

The model v3 scenario-tree layer provides a reproducible experiment design for separating climate, technology, and stochastic uncertainty in residential energy-demand simulations. A scenario tree is needed because future demand results are not determined by climate forcing alone. They also depend on technology adoption assumptions and on stochastic household or cohort realizations. The scenario-tree layer therefore defines a controlled set of branches before results are interpreted.

In this framework, a scenario is the deterministic parent combination of one climate window, one climate pathway, and one technology case. A realization is a stochastic sampling instance identified by a reproducible seed. A scenario leaf is the executable unit formed by combining one scenario with one realization.

The layer supports the thesis claim that climate projections were organized into a structured scenario tree consisting of a historical baseline and three future climate windows under RCP2.6, RCP4.5, and RCP8.5. Each climate branch was combined with technology adoption assumptions and stochastic household realizations. This allowed climate, technology, and behavioural uncertainty to be separated and compared through consistent output metrics.

## Scenario dimensions

The scenario tree has four explicit dimensions:

| Dimension | Meaning | Source |
|---|---|---|
| `climate_window_id` | Historical or future analysis period | `config/model_v3/scenario_tree/climate_windows.yaml` |
| `climate_pathway_id` | Historical forcing or RCP pathway | `config/model_v3/scenario_tree/scenario_tree_schema.yaml` |
| `technology_case_id` | Residential technology-stock assumption | `config/model_v3/scenario_tree/technology_cases.yaml` |
| `realization_id` | Stochastic seed/cohort realization | `config/model_v3/scenario_tree/realization_policy.yaml` |

Keeping these dimensions explicit prevents climate, technology, and behavioural uncertainty from being mixed in ambiguous filenames or ad hoc run folders.

## Climate-window definition

The canonical climate windows are defined in `climate_windows.yaml`. The historical baseline is `baseline_1981_2005`. The future windows are `near_future_2030_2049`, `mid_century_2050_2070`, and `long_term_2080_2100`. Each window records a canonical inclusive analysis start, canonical inclusive analysis end, source-file window, window type, and allowed climate pathways.

## RCP pathway representation

The baseline uses the `historical` pathway only. Future windows use `rcp_2_6`, `rcp_4_5`, and `rcp_8_5`. The pathway identifier records climate forcing, not technology adoption or stochastic behaviour. Future RCP branches are therefore interpreted only together with their technology case and realization.

## Technology-case representation

Technology cases are encoded in `technology_cases.yaml`. The baseline technology case is `tech_current_stock`, which represents the historical current-stock reference. Future branches use `tech_frozen_stock`, `tech_moderate_electrification`, and `tech_high_electrification_pv_ev`.

Future climate-only comparisons use `tech_frozen_stock`, not future `tech_current_stock`, because `tech_current_stock` is baseline-only in the metadata. The frozen-stock case applies future climate forcing while holding residential technology assumptions fixed relative to the baseline stock, which makes it the appropriate reference for isolating climate effects.

## Stochastic realization policy

The realization policy defines `seed_0000` through `seed_0099`. The integer seed index is the reproducible sampling key. In run configs and summaries, the seed is recorded with `realization_id`, `seed_index`, `seed_value`, and `cohort_size`. This permits outputs to be paired across scenarios by realization ID and supports stochastic robustness summaries.

## Scenario and scenario-leaf identifiers

Stable identifiers are part of the experiment contract:

```text
scenario_id =
  {climate_window_id}__{climate_pathway_id}__{technology_case_id}

scenario_leaf_id =
  {climate_window_id}__{climate_pathway_id}__{technology_case_id}__{realization_id}
```

The double underscore is reserved as the dimension separator. Stable IDs are important because every config, run directory, registry row, output summary, comparison row, and figure metadata entry can be traced back to the same identifier without relying on run order.

## Baseline special case

The baseline scenario is:

```text
baseline_1981_2005__historical__tech_current_stock
```

It is a special case because it combines the historical climate window, the historical pathway, and the current-stock technology case. It must not be combined with RCP pathways or future technology cases. Baseline leaves retain the same realization policy as future leaves so future comparisons can match by `realization_id`.

## Future scenario branches

Future branches combine each future climate window with each RCP pathway and each future-permitted technology case. The combined stress case used by the scenario-tree framework is:

```text
long_term_2080_2100__rcp_8_5__tech_high_electrification_pv_ev
```

The future branch contract preserves separation between climate forcing, technology adoption assumptions, and stochastic realization. A future result is therefore interpretable only through all four dimensions.

## 2050 overlap policy

Raw processed source files may overlap in 2050, but canonical analysis windows do not. The near-future canonical window ends on 2049-12-31. The mid-century canonical window starts on 2050-01-01. Therefore, 2050 is assigned only to the mid-century canonical analysis window for cross-window statistics and comparisons.

This policy preserves already validated processed climate files while preventing double counting of 2050 in cross-window metrics, comparisons, and figures.

## Physical experiment-space layout

The physical experiment space is rooted at `model_v3/experiments/scenario_tree`. The main subdirectories are:

| Path | Purpose |
|---|---|
| `configs/` | Generated leaf-level configuration files |
| `runs/` | Per-leaf run directories with config, inputs manifest, outputs, and logs |
| `manifests/` | Scenario index, run registry, and validation reports |
| `summaries/realization_level/` | Standardized per-leaf metrics |
| `summaries/scenario_level/` | Scenario aggregate metrics |
| `summaries/comparison_level/` | Climate, technology, stress-case, and stochastic comparison outputs |

## Configuration generation

Every run config maps to exactly one scenario leaf. The config records the scenario-leaf dimensions, resolved climate forcing file, technology metadata file, Belgian technology input YAML, stochastic seed/cohort metadata, output directories, and provenance sources. The corresponding inputs manifest records the same resolved input files in a compact audit form.

## Scenario execution and provenance

The run registry `model_v3/experiments/scenario_tree/manifests/run_registry.csv` records execution attempts. For each attempt it stores status, timestamps, config path and hash, input manifest path and hash, climate forcing file and hash, Belgian technology input file and hash, random seed, cohort size, model version, output path, log path, and Git provenance where available.

The latest actual run status per leaf determines whether a leaf is treated as successful, failed, running, or not run. Documentation and reports must not claim full scenario completion unless the registry proves it.

## Output standardization

Raw model outputs are converted into standardized summary rows. The per-leaf table `scenario_leaf_metrics.csv` records consistent metric columns together with scenario metadata, climate forcing, technology input file, seed/cohort metadata, raw output directory, and climate-window inclusion flags. Standardization makes climate, technology, and stochastic comparisons possible without reading heterogeneous raw output files.

## Comparison framework

Comparison definitions are encoded in `comparison_definitions.yaml`. Climate-only comparisons use the historical current-stock baseline and future `tech_frozen_stock` leaves. Technology-only comparisons use `tech_frozen_stock` as the future reference case within the same climate window and RCP pathway. The combined stress comparison targets the long-term RCP8.5 high-electrification PV/EV case.

Where stochastic pairings are required, leaves are matched by `realization_id`. P10/P50/P90 bands summarize the distribution of outcomes across stochastic realizations and represent robustness to behavioural and cohort sampling variation, not an external confidence interval.

## Figure generation workflow

Thesis figures under `figures/scenario_tree/` are generated from scenario-tree manifests, summary outputs, comparison outputs, and config metadata. Figure metadata in `figures/scenario_tree/metadata/figure_metadata.csv` records source data files, metrics, scenario filters, generation script, row counts, and caption IDs. Caption drafts are maintained in `figures/scenario_tree/thesis_caption_drafts.md`.

## Traceability of results

Every result must answer four traceability questions:

1. Which climate forcing was used?
2. Which technology assumptions were active?
3. Which stochastic seed/cohort generated this result?
4. Which exact model/config produced the output?

The audit matrix `reports/scenario_tree_traceability_matrix.csv` answers these questions for every latest-successful scenario leaf by joining registry rows, run configs, inputs manifests, standardized summaries, and file-hash metadata.

## Reproducibility guarantees

The scenario-tree layer provides reproducibility through stable IDs, deterministic seed identifiers, per-leaf run configs, per-leaf inputs manifests, registry hashes, standardized summary outputs, and generated reports. These guarantees support methodological traceability; they do not by themselves establish external empirical validation.

## Limitations

The scenario tree is an experiment-management and traceability layer. It does not claim that every enumerated leaf has been simulated unless the registry shows successful runs. It does not modify raw model outputs or processed climate files. It does not validate model accuracy against external measured demand data. Technology cases encode modelling assumptions used by the existing framework and should not be interpreted as calibrated policy forecasts unless supported by separate evidence.
