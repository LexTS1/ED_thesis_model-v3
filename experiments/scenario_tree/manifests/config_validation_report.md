# Scenario-Leaf Config Validation Report

Generated at UTC: `2026-05-08T22:27:16+00:00`

## Counts

- Scenario leaves checked: 2800
- Executable configs generated: 2800
- Baseline configs: 100
- Future configs: 2700
- Unique climate forcing files referenced: 10

## Climate Forcing Files

- `inputs/climate/processed/baseline/weather_baseline_historical_cnrm_cerfacs_cm5_cnrm_aladin63_r1i1p1.csv`
- `inputs/climate/processed/long_term/weather_long_term_rcp_2_6_cnrm_cerfacs_cm5_cnrm_aladin63_r1i1p1.csv`
- `inputs/climate/processed/long_term/weather_long_term_rcp_4_5_cnrm_cerfacs_cm5_cnrm_aladin63_r1i1p1.csv`
- `inputs/climate/processed/long_term/weather_long_term_rcp_8_5_cnrm_cerfacs_cm5_cnrm_aladin63_r1i1p1.csv`
- `inputs/climate/processed/mid_century/weather_mid_century_rcp_2_6_cnrm_cerfacs_cm5_cnrm_aladin63_r1i1p1.csv`
- `inputs/climate/processed/mid_century/weather_mid_century_rcp_4_5_cnrm_cerfacs_cm5_cnrm_aladin63_r1i1p1.csv`
- `inputs/climate/processed/mid_century/weather_mid_century_rcp_8_5_cnrm_cerfacs_cm5_cnrm_aladin63_r1i1p1.csv`
- `inputs/climate/processed/near_future/weather_near_future_rcp_2_6_cnrm_cerfacs_cm5_cnrm_aladin63_r1i1p1.csv`
- `inputs/climate/processed/near_future/weather_near_future_rcp_4_5_cnrm_cerfacs_cm5_cnrm_aladin63_r1i1p1.csv`
- `inputs/climate/processed/near_future/weather_near_future_rcp_8_5_cnrm_cerfacs_cm5_cnrm_aladin63_r1i1p1.csv`

## Checks

- all referenced climate files exist: True
- all technology cases defined: True
- belgian technology input yaml exists: True
- baseline and future cases separated: True
- near future canonical excludes 2050: True
- mid century canonical includes 2050: True
- simulations run: 0
- duplicate config paths: 0

## Warnings

- None

## Assumptions

- Processed climate forcing files are resolved from explicit metadata when present, otherwise by pathway/window/source-window tokens and sidecar metadata.
- Seed value equals the integer suffix of realization_id.
- Household cohorts remain deferred to the simulation phase.
- No residential demand simulations are executed by this generator.
