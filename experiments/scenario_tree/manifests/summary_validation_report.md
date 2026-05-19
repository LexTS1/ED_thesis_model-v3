# Summary validation report

- Generation timestamp UTC: 2026-05-14T06:54:09.694667+00:00
- Number of successful runs found: 5
- Number of per-leaf summaries generated: 5
- Number of missing per-leaf summaries: 0
- Number of scenario-level aggregate rows: 2
- Number of baseline comparison rows: 1
- Future leaves with valid baseline comparison: 1
- Future leaves missing baseline comparison: 0
- Missing required metrics: 0
- Near-future includes 2050: no
- Mid-century includes 2050: yes
- No new simulations were run: yes

## Required metrics

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

## Missing metrics

- None

## Raw output columns used

- `annual_dhw_kWh=dhw_thermal_kWh`
- `annual_electricity_gross_kWh=annual_energy_by_carrier_kWh.electricity_gross_actual`
- `annual_gas_kWh=annual_energy_by_carrier_kWh.natural_gas`
- `annual_grid_export_kWh=annual_grid_export_kWh`
- `annual_grid_import_kWh=annual_grid_import_kWh`
- `annual_useful_heating_kWh=space_heating_thermal_kWh`
- `ev_charging_kWh=annual_ev_charging_kWh`
- `peak_grid_import_W=P_el_grid_import_W`
- `pv_generation_kWh=annual_pv_generation_kWh`
- `summer_peak_grid_import_W=P_el_grid_import_W`
- `winter_peak_grid_import_W=P_el_grid_import_W`

## Climate columns used

- `I_solar_W_m2`
- `T_out_C`

## Errors

- None

## Warnings

- None

## Assumptions

- Baseline comparison rows omit baseline leaves.
- Future leaves are matched to baseline leaves by realization_id.
- Validation reads only summary artifacts, manifests, and registry state.
