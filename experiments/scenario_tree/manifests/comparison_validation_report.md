# Comparison validation report

- Generation timestamp UTC: 2026-05-11T17:21:41.195152+00:00
- Comparison definitions file used: `/Users/alex/Library/CloudStorage/OneDrive-VrijeUniversiteitBrussel/Documenten/VUB/MA IW/Master Thesis/model_v3/config/scenario_tree/comparison_definitions.yaml`
- Input metrics table used: `/Users/alex/Library/CloudStorage/OneDrive-VrijeUniversiteitBrussel/Documenten/VUB/MA IW/Master Thesis/experiments/scenario_tree/summaries/realization_level/scenario_leaf_metrics.csv`
- Number of scenario leaves available: 4
- Number of scenario groups available: 2
- Number of comparisons generated: 14
- Number of invalid comparison references: 0
- Number of missing baseline/reference matches: 1
- Number of valid climate-only pairs: 0
- Number of valid technology-only pairs: 0
- Number of valid combined stress-case pairs: 0
- Number of stochastic robustness groups: 2
- Future climate-only comparisons use `tech_frozen_stock`: yes
- Baseline uses `tech_current_stock`: yes
- Deltas vs baseline available where baseline exists: yes
- P10/P50/P90 bands computed: yes
- Near-future excludes 2050: yes
- Mid-century includes 2050: yes
- Simulations run: 0

## Metrics Included

- `annual_electricity_gross_kWh`
- `annual_grid_import_kWh`
- `annual_grid_export_kWh`
- `annual_gas_kWh`
- `annual_useful_heating_kWh`
- `annual_dhw_kWh`
- `peak_grid_import_W`
- `winter_peak_grid_import_W`
- `summer_peak_grid_import_W`
- `pv_generation_kWh`
- `pv_self_consumption_kWh`
- `pv_export_fraction`
- `ev_charging_kWh`
- `mean_T_out_C`
- `winter_mean_T_out_C`
- `summer_mean_T_out_C`
- `HDD_15`
- `HDD_18`
- `CDD_22`
- `mean_solar_W_m2`

## Missing Groups

- `long_term_2080_2100__rcp_2_6__tech_frozen_stock`: no_successful_runs_in_metrics_table
- `long_term_2080_2100__rcp_4_5__tech_frozen_stock`: no_successful_runs_in_metrics_table
- `long_term_2080_2100__rcp_8_5__tech_frozen_stock`: no_successful_runs_in_metrics_table
- `mid_century_2050_2070__rcp_2_6__tech_frozen_stock`: no_successful_runs_in_metrics_table
- `mid_century_2050_2070__rcp_4_5__tech_frozen_stock`: no_successful_runs_in_metrics_table
- `mid_century_2050_2070__rcp_8_5__tech_frozen_stock`: no_successful_runs_in_metrics_table
- `near_future_2030_2049__rcp_2_6__tech_frozen_stock`: no_successful_runs_in_metrics_table
- `near_future_2030_2049__rcp_4_5__tech_frozen_stock`: no_successful_runs_in_metrics_table
- `near_future_2030_2049__rcp_8_5__tech_frozen_stock`: no_successful_runs_in_metrics_table
- `long_term_2080_2100__rcp_2_6__tech_frozen_stock`: no_successful_runs_in_metrics_table
- `long_term_2080_2100__rcp_2_6__tech_high_electrification_pv_ev`: no_successful_runs_in_metrics_table
- `long_term_2080_2100__rcp_2_6__tech_moderate_electrification`: no_successful_runs_in_metrics_table
- `long_term_2080_2100__rcp_4_5__tech_frozen_stock`: no_successful_runs_in_metrics_table
- `long_term_2080_2100__rcp_4_5__tech_high_electrification_pv_ev`: no_successful_runs_in_metrics_table
- `long_term_2080_2100__rcp_4_5__tech_moderate_electrification`: no_successful_runs_in_metrics_table
- `long_term_2080_2100__rcp_8_5__tech_frozen_stock`: no_successful_runs_in_metrics_table
- `long_term_2080_2100__rcp_8_5__tech_high_electrification_pv_ev`: no_successful_runs_in_metrics_table
- `long_term_2080_2100__rcp_8_5__tech_moderate_electrification`: no_successful_runs_in_metrics_table
- `mid_century_2050_2070__rcp_2_6__tech_frozen_stock`: no_successful_runs_in_metrics_table
- `mid_century_2050_2070__rcp_2_6__tech_high_electrification_pv_ev`: no_successful_runs_in_metrics_table
- `mid_century_2050_2070__rcp_2_6__tech_moderate_electrification`: no_successful_runs_in_metrics_table
- `mid_century_2050_2070__rcp_4_5__tech_frozen_stock`: no_successful_runs_in_metrics_table
- `mid_century_2050_2070__rcp_4_5__tech_high_electrification_pv_ev`: no_successful_runs_in_metrics_table
- `mid_century_2050_2070__rcp_4_5__tech_moderate_electrification`: no_successful_runs_in_metrics_table
- `mid_century_2050_2070__rcp_8_5__tech_frozen_stock`: no_successful_runs_in_metrics_table
- `mid_century_2050_2070__rcp_8_5__tech_moderate_electrification`: no_successful_runs_in_metrics_table
- `near_future_2030_2049__rcp_2_6__tech_frozen_stock`: no_successful_runs_in_metrics_table
- `near_future_2030_2049__rcp_2_6__tech_high_electrification_pv_ev`: no_successful_runs_in_metrics_table
- `near_future_2030_2049__rcp_2_6__tech_moderate_electrification`: no_successful_runs_in_metrics_table
- `near_future_2030_2049__rcp_4_5__tech_frozen_stock`: no_successful_runs_in_metrics_table
- `near_future_2030_2049__rcp_4_5__tech_high_electrification_pv_ev`: no_successful_runs_in_metrics_table
- `near_future_2030_2049__rcp_4_5__tech_moderate_electrification`: no_successful_runs_in_metrics_table
- `near_future_2030_2049__rcp_8_5__tech_frozen_stock`: no_successful_runs_in_metrics_table
- `near_future_2030_2049__rcp_8_5__tech_high_electrification_pv_ev`: no_successful_runs_in_metrics_table
- `near_future_2030_2049__rcp_8_5__tech_moderate_electrification`: no_successful_runs_in_metrics_table
- `long_term_2080_2100__rcp_8_5__tech_high_electrification_pv_ev`: no_successful_runs_in_metrics_table

## Warnings

- Some comparison groups have no successful runs in Phase 5 summaries.

## Assumptions

- Generation uses scenario_leaf_metrics.csv as the realization-level source of truth.
- Scenario groups with no successful rows are recorded in diagnostics and omitted from numeric pair tables.
- No simulations are run and raw outputs are not modified.
