# Canonical Thesis Artefacts

This repository contains code, ignored generated outputs, validation artefacts, and thesis figures. The canonical thesis model code is frozen at tag `thesis-model-freeze-2026-06-01`, which points to commit `9d239a01`. The final expanded selected thesis outputs were rerun cleanly at commit `a1564ed6` with `git_is_dirty=false`.

## Keep And Cite

| Area | Canonical path | Purpose |
| --- | --- | --- |
| Model code | `src/model_v3/` | Thesis model implementation. |
| Model configs | `config/` | Baseline, thesis, validation, scenario-tree, tariff, emissions, and technology assumptions. |
| Core scenario-tree outputs | `experiments/scenario_tree/` | Climate-only / stock-weighted selected thesis runs, summaries, registries, and validation reports. |
| Output 3-6 cohort outputs | `experiments/scenario_tree_output34/` | Selected hourly cohort runs for peak/grid stress, diversity, bills, emissions, and investment/adaptation indicators. |
| Scenario figures | `figures/scenario_tree/` | Core climate, annual demand, seasonal, uncertainty, and infrastructure-stress figures. |
| Output 3-6 figures | `figures/scenario_tree_output34/` | Peak/grid, diversity, bill, emissions, and investment/adaptation figures. |
| Validation outputs | `outputs/validation/` | Latest local validation artefacts and plots. |
| Validation reports | `reports/model_v3/validation/` | Technology and demand validation reports used to support thesis claims. |
| Methodology figures | `figures/thesis_methodology/` | Thesis-facing validation and methodology figures. |
| Handbook and briefing | `docs/model_v3_complete_model_handbook.*`, `docs/model_v3_supervisor_briefing.*` | Generated explanation artefacts. |
| Claims guardrail | `docs/thesis_claims_table.md` | Evidence and caveat table for final writing. |

## Removed As Non-Canonical

| Removed path | Reason |
| --- | --- |
| `outputs/model_v3/` | Legacy duplicate namespace from before the identity cleanup. |
| `outputs/final/` | Older standalone generated outputs, not used by the canonical thesis scenario-tree results. |
| `outputs/annual/`, `outputs/stochastic/`, `outputs/climate_uncertainty/`, `outputs/deterministic/` | Older ad hoc run outputs outside the selected thesis scenario-tree workflow. |
| `experiments/scenario_tree_output34_100hh_sensitivity/` | Pre-audit sensitivity bundle. It was not rerun at the frozen thesis commit and should not be cited as canonical evidence. |
| `figures/scenario_tree_output34_100hh_sensitivity/` | Figures generated from the removed pre-audit sensitivity bundle. |
| `figures/scenario_tree/output3_peak_grid_stress/`, `figures/scenario_tree/output4_distribution_diversity/`, `figures/scenario_tree/output34_metadata/` | Duplicate copies of Output 3-4 figures. The canonical path is `figures/scenario_tree_output34/`. |
| `outputs/validation/debug/` | Temporary validation debug artefacts; canonical validation outputs remain under `outputs/validation/` and `reports/model_v3/validation/`. |
| `.venv/`, `.pytest_cache/`, `__pycache__/`, `.DS_Store` | Local environment/cache/OS artefacts. |

## Submission Rule

Use only the kept canonical paths above for final thesis figures, tables, and claims. If an old path appears in the thesis text, treat it as stale unless it is explicitly listed here as canonical.
