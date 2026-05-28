# Codex brief: useful-heating vs HDD contradiction audit

## Task in one sentence

The `annual_useful_heating_kWh_mean` reported in
`experiments/scenario_tree/summaries/comparison_level/annual_space_heating_demand_comparison.csv`
contradicts the unambiguous HDD$_{18}$ signal in
`annual_climate_degree_day_comparison.csv` for most future windows.
Find the root cause, fix it, and regenerate the comparison so the
direction of the useful-heating response is consistent with the
direction of the HDD response across all future window × pathway
cells.

**Do not change** the model physics, control, or systems layers.
**Do not change** `building.ua_multiplier` or any calibration knob
unless the diagnostic explicitly identifies it as the root cause.
Treat this as a **provenance and regeneration bug**, not a model
bug, until proven otherwise.

---

## Symptom (the contradiction)

From `annual_climate_degree_day_comparison.csv`
(`tech_frozen_stock`, baseline reference
`baseline_1981_2005__historical__tech_current_stock`):

| Window | Pathway | ΔHDD18 (%) | ΔCDD22 (%) |
|---|---|---:|---:|
| mid_century_2050_2070 | rcp_2_6 | -10 | +119 |
| mid_century_2050_2070 | rcp_4_5 | -16 | +212 (approx) |
| mid_century_2050_2070 | rcp_8_5 | -16 | +331 |
| long_term_2080_2100   | rcp_2_6 | -8  | +123 |
| long_term_2080_2100   | rcp_4_5 | -16 | +212 |
| long_term_2080_2100   | rcp_8_5 | -31 | +663 |

From `annual_space_heating_demand_comparison.csv` (same scenarios):

| Window | Pathway | Δuseful_heating (%) | n_real | n_samples |
|---|---|---:|---:|---:|
| baseline_1981_2005 | historical | 0 | 10 | 250 |
| mid_century_2050_2070 | rcp_2_6 | **+49** | 3 | 63 |
| mid_century_2050_2070 | rcp_4_5 | **+36** | 3 | 63 |
| mid_century_2050_2070 | rcp_8_5 | **+23** | 3 | 63 |
| long_term_2080_2100   | rcp_2_6 | **+53** | 3 | 63 |
| long_term_2080_2100   | rcp_4_5 | **+30** | 3 | 63 |
| long_term_2080_2100   | rcp_8_5 |   -3   | 3 | 63 |

**Two red flags are visible in the table alone:**

1. The sign of the useful-heating change is wrong for 6 of 7 future
   cells (warmer climate, more useful heating).
2. The baseline scenario has `n_successful_realizations = 10` while
   every future scenario has `n_successful_realizations = 3`. This
   is the smoking gun for **non-equivalent cohort populations**: the
   baseline summary and the future summaries were generated from
   different realisation sets, and the per-realisation cohort
   composition (archetype draw, heat-pump assignment,
   household-size draw) is therefore not paired.

The absolute mean useful-heating is ~907 kWh/year/household for the
baseline, which is **one order of magnitude below** the configured
Belgian space-heating baseline in `config/thesis.yaml`
(`space_heating_kWh: 12000`, `space_heating_range_kWh: [12000, 16000]`).
That is consistent with the comparison being computed over an
electrified subset (heat-pump households only) rather than over the
full cohort thermal demand.

---

## Hypotheses, ranked

### H1 (most likely): cohort non-equivalence between baseline and future summaries

The baseline summary aggregates 10 realisations × 25 climate-year
samples = 250 samples; future summaries aggregate 3 realisations ×
21 climate-year samples = 63 samples. If those realisations were
drawn from different cohort builds (different archetype mix,
different heat-pump share, different household-size draw), then the
per-leaf `annual_useful_heating_kWh_mean` is not comparable across
windows even at the same realisation_id.

**Likely evidence to look for:**
- different `cohort_size` values across leaves in `run_registry.csv`
- different `realization_id` sets in the per-leaf metrics
  (`scenario_leaf_metrics.csv`) for baseline vs future
- different archetype distributions in the per-leaf run-config files
  under `experiments/scenario_tree/runs/.../run_config.yaml`

### H2 (likely): useful-heating metric is electric-only, not total-thermal

If the comparison computes `annual_useful_heating_kWh` from
`P_el_space_heating_W` (or from `Q_heating_supplied_W` filtered by
heat-pump households), then the metric is dominated by the
**heat-pump household subset**, not the full cohort. The Belgian
current-stock heat-pump share is ~4–6 %; if the future
`tech_frozen_stock` runs happen to sample more heat-pump
households than the baseline (or fewer, depending on the seed),
the per-cohort mean shifts independently of the underlying thermal
demand.

The ~900 kWh/year/household baseline absolute magnitude is
consistent with this hypothesis: only ~6 % of households contribute
non-zero useful_heating, so the cohort mean is small.

**Likely evidence to look for:**
- the column definition for `annual_useful_heating_kWh_mean` in
  `scenario_leaf_metrics_schema.yaml`
- whether the metric is computed from `Q_heating_supplied_W`
  (thermal, all carriers) or from `P_el_space_heating_W` (electric
  only)
- whether the comparison divides by total cohort N or by the
  active-heat-pump subset

### H3 (possible): mixed-provenance summaries

Different summary rows may have been generated from different code
versions or different config versions (different
`building.ua_multiplier`, different archetype table). The
`config_hash_sha256` field in `run_registry.csv` is the canonical
check.

**Likely evidence to look for:**
- different `config_hash_sha256` across the rows that feed the
  baseline vs future summary aggregations
- different `git_commit` per row
- timestamp gaps between baseline rows and future rows large enough
  to span a config change

### H4 (least likely): climate-forcing unit mismatch

The processed CORDEX file is daily-mean temperature (forward-filled
to hourly), while the AWS-Uccle file is hourly. If the daily-mean
representation systematically biases the model heating demand
upward in mild climates (because morning peaks below balance point
get smoothed away), the future-window heating demand could
spuriously rise relative to the baseline.

**Likely evidence to look for:**
- comparable timesteps and units across baseline vs future climate
  forcing files
- whether the baseline scenario uses hourly Uccle data or also uses
  daily-mean CORDEX historical data

---

## Diagnostic plan (do this first, before any rewrite)

Run these in order and write the findings into
`reports/model_v3/audits/useful_heating_audit.md`.

1. **Establish cohort equivalence**

   ```bash
   PYTHONPATH=src python3 -c "
   import pandas as pd
   df = pd.read_csv('experiments/scenario_tree/manifests/run_registry.csv')
   print(df.groupby(['scenario_id'])[['random_seed','cohort_size','config_hash_sha256','git_commit']].nunique())
   "
   ```

   Expected if H1: baseline scenario shows more distinct seeds or a
   different cohort_size than the future scenarios.

2. **Identify the useful-heating metric definition**

   - Read `src/model_v3/scenarios/summarize_outputs.py` and
     `src/model_v3/scenarios/summary_contract.py` for the
     `annual_useful_heating_kWh` definition.
   - Confirm whether it sums `Q_heating_supplied_W` over the year
     (thermal, all carriers) or whether it uses
     `P_el_space_heating_W` (electric only).

   Expected if H2: the metric is computed from the electric channel
   only, so the cohort mean is dominated by heat-pump assignment
   variance.

3. **Verify schema alignment between baseline and futures**

   ```bash
   PYTHONPATH=src python3 -c "
   import pandas as pd
   df = pd.read_csv('experiments/scenario_tree/summaries/realization_level/scenario_leaf_metrics.csv')
   for sid in df['scenario_id'].unique():
       sub = df[df['scenario_id']==sid]
       print(sid, len(sub), 'config_hashes:', sub['config_hash_sha256'].nunique(),
             'git_commits:', sub['git_commit'].nunique() if 'git_commit' in sub else 'n/a')
   "
   ```

   Expected if H3: at least one future scenario has a different
   config hash from the baseline rows.

4. **Spot-check the unit on a single leaf**

   Pick `baseline_1981_2005__historical__tech_current_stock__seed_0000`
   and verify:
   - cohort size in `run_config.yaml`
   - the hourly profile under `runs/.../outputs/`
   - the annual integral of `Q_heating_supplied_W` and of
     `P_el_space_heating_W * heating_cop`
   - whether they agree, and which one feeds the summary
     `annual_useful_heating_kWh_mean`

---

## Fix plan (depends on the diagnostic result)

### If H1 wins (non-equivalent cohorts)

Drop the baseline rows that have no matching future
`realization_id`, and regenerate the comparison only over the
**intersection** of realisations actually present in both branches.
This is the cheapest fix; it does not require re-running the model.

Code site: `src/model_v3/scenarios/generate_comparisons.py` (or
whichever module reads `scenario_leaf_metrics.csv` and produces
`annual_space_heating_demand_comparison.csv`). Add a pre-aggregation
filter:

```python
common_realisations = (
    leaf_df.loc[leaf_df["scenario_id"] == baseline_scenario_id, "realization_id"].unique()
    & leaf_df.loc[leaf_df["scenario_id"] == future_scenario_id, "realization_id"].unique()
)
paired = leaf_df[leaf_df["realization_id"].isin(common_realisations)]
```

Then aggregate from `paired` rather than from `leaf_df`. Re-run the
summariser.

### If H2 wins (electric-only metric)

Re-define the metric to use `Q_heating_supplied_W` integrated
across the year, regardless of carrier. This is the
**physically correct** thermal-demand metric and matches the HDD
indicator's intent.

Code site: `src/model_v3/scenarios/summarize_outputs.py` — find the
column definition for `annual_useful_heating_kWh` and switch its
source column from electric to thermal. Re-run the summariser; the
expected baseline absolute should rise from ~900 to ~10,000–16,000
kWh/year/household (matching the configured Belgian range).

### If H3 wins (mixed provenance)

Drop summary rows whose `config_hash_sha256` does not match the
canonical thesis hash. Document the dropped rows in the audit
report. Re-run only the missing future leaves with the canonical
config, then regenerate the comparison.

### If H4 wins (forcing unit)

This is a model-content fix and is out of scope for the audit pass.
Document it as future work.

---

## Acceptance test (so the chapter can cite the new table)

After the fix, the regenerated
`annual_space_heating_demand_comparison.csv` must satisfy **all**
of the following:

1. **Sign consistency.** For every future
   (window × pathway) cell with
   `tech_frozen_stock`, the sign of
   `delta_annual_useful_heating_kWh_pct` must equal the sign of
   `-delta_HDD_18_pct` (i.e. less HDD → less heating).

2. **Monotonicity within window.** Within each future window, the
   ranking of `delta_annual_useful_heating_kWh_pct` must agree with
   the ranking of `delta_HDD_18_pct` across `rcp_2_6 / rcp_4_5 /
   rcp_8_5` (more forcing → less heating).

3. **Magnitude plausibility.** The absolute baseline
   `annual_useful_heating_kWh_mean` for
   `tech_current_stock` must lie inside the configured range
   `space_heating_range_kWh: [12000, 16000]` from
   `config/thesis.yaml`. A value of ~900 means H2 has not been
   fixed.

4. **Cohort equivalence reported.** The comparison CSV must record
   `n_successful_realizations` equal across baseline and future
   scenarios in the rows it aggregates from (or document
   pairwise-realisation filtering explicitly in
   `comparison_validation_report.md`).

5. **Provenance hashes match.** The
   `comparison_validation_report.md` must list a single canonical
   `config_hash_sha256` covering all rows used in the aggregation,
   or document the audit-driven exclusion of rows that did not
   match.

When the acceptance test passes, update
`docs/thesis_results.tex` to:

- Replace `tab:useful_heating_caveat` with the cleaned table.
- Remove the caveat paragraph
  ("Direction is mixed: long-term RCP\,8.5 ...") and the audit
  flagging.
- Replace `fig:phase1_sh_pct_heatmap` and
  `fig:phase1_sh_hdd_scatter` with the regenerated PNGs (the
  scatter should show future-window points lying **on** the
  historical regression line, not above it).
- Update the §4.2.1 closing paragraph so the useful-heating result
  is presented as a defendable climate-pressure quantification
  rather than as audit-pending evidence.

---

## Out of scope for this audit

- Changing `building.ua_multiplier` away from 1.10
- Adding additional GCM/RCM chains
- Implementing active cooling final energy
- Changing the macro/synthetic/Fluvius/KU Leuven validation layers

These are tracked separately in `docs/limitations.md` (to be
written) and should not be touched while resolving this
contradiction.
