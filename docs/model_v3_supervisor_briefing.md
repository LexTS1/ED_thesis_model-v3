# Model v3 supervisor briefing

Generated UTC: 2026-06-01T05:48:44+00:00

## 1. What the model does

`model_v3` is a bottom-up residential energy-demand model. It combines climate forcing, building and technology assumptions, stochastic household/cohort realizations, thermal physics, control logic, carrier conversion, PV/EV accounting, and standardized output metrics.

## 2. What the scenario tree adds

The scenario tree organizes results by climate window, RCP pathway, technology case, and realization seed. This keeps climate, technology, and behavioural uncertainty separate and traceable.

## 3. What has been implemented

Detected implemented artifacts include scenario-tree schema/configs, stable IDs, canonical climate windows, explicit 2050 policy, experiment manifests, per-leaf configs, runner/provenance registry, standardized summaries for available successful runs, comparison definitions, figure metadata, and audit reports.

Current execution evidence: 37 latest-successful leaves out of 2800 enumerated leaves. This is partial execution, not full scenario completion.

## 4. Key methodological choices

- Baseline: `baseline_1981_2005__historical__tech_current_stock`.
- Future climate pathways: `rcp_2_6`, `rcp_4_5`, `rcp_8_5`.
- Future technology cases: `tech_frozen_stock`, `tech_moderate_electrification`, `tech_high_electrification_pv_ev`.
- Climate-only comparisons use future `tech_frozen_stock`.
- Pairwise deltas match leaves by `realization_id`.
- P10/P50/P90 bands describe modelled stochastic spread across available successful realizations.

## 5. 2050 overlap policy

Raw processed source files may overlap in 2050, but canonical analysis windows do not. Near-future ends on 2049-12-31. Mid-century starts on 2050-01-01. Therefore 2050 belongs only to the mid-century canonical analysis window. This is encoded in `config/scenario_tree/climate_windows.yaml`.

## 6. Outputs and figures available

Standardized metrics include annual gross electricity, grid import/export, gas, useful heating, DHW, grid peaks, PV generation/self-consumption/export fraction, EV charging, temperature, HDD/CDD, and solar metrics. Figures under `figures/scenario_tree/` and handbook assets show scenario structure, climate forcing, annual demand, grid impact, uncertainty bands, infrastructure stress, input inventory, and workflows where source tables are available.

## 7. Limitations

Execution coverage is partial. Scenario-tree validation is internal consistency and traceability validation, not external empirical validation. The physical model is simplified. Climate ensemble coverage may be limited by available processed files. Technology cases are assumptions, not forecasts. Stochastic robustness depends on successful realization count.

## 8. Next improvements

Run validation commands, confirm registry status, execute representative baseline and future leaves, regenerate summaries/comparisons/figures, validate against smart-meter or aggregate load data, calibrate technology assumptions with Belgian statistics, test cohort-size convergence, and expand climate ensemble coverage.

## 9. Five talking points for tomorrow

1. The scenario tree makes the thesis experiment auditable because every result has a stable climate, technology, and seed identity.
2. The 2050 overlap issue is handled by non-overlapping canonical analysis windows.
3. The framework is implemented, but only available successful runs should be discussed as results.
4. Climate-only effects are isolated with frozen-stock future technology assumptions.
5. The biggest thesis risks are external validation, technology calibration, climate ensemble breadth, and stochastic convergence.

## 10. Five likely supervisor questions

**Why a scenario tree?** To separate climate, technology, and stochastic effects and make outputs traceable.

**Why not claim all scenarios are complete?** The registry/audit evidence does not support that claim.

**How is 2050 handled?** Near-future excludes it; mid-century includes it.

**What is a realization?** A reproducible stochastic seed/cohort draw.

**How do you know it is valid?** Internal validation checks consistency and traceability; external empirical validation requires separate measured-data reports.
