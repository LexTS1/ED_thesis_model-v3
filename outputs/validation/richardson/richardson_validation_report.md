# Richardson Stochastic Baseload Validation

## Validation Type

- classification: Synthetic structural reference validation
- interpretation: Richardsonpy stochastic occupancy, appliance, and lighting profiles are used as an independent synthetic benchmark for non-thermal household baseload shape, occupancy timing, peakiness, and diversity.

## Runtime Context

- canonical thesis config: `config/thesis.yaml`
- canonical thesis runtime: reference year `2023`, `30` households, `simulation.max_steps: null`, climate disabled
- report reference year: `2023`
- report cohort households: `30`
- report minimum households: `30`
- report max steps: `null`
- quick mode: `False`
- climate enabled: `True`
- simulated/aligned model steps: `8760`

## Reference Generator

- generator: `richardsonpy`
- households: `30`
- timestep seconds: `3600`
- seed: `42`
- shape normalized to model annualized energy: `True`
- limitation: Richardsonpy is a synthetic UK-origin stochastic reference. It validates non-thermal profile structure, not Belgian measured demand or thermal/PV/EV behaviour.

## Key Metrics

| Group | Metric | Model | Richardson | Delta/Error |
| --- | --- | ---: | ---: | ---: |
| Shape | mean diurnal correlation | 0.505 | 1.000 | 0.495 |
| Shape | mean diurnal NMAE | 0.398 | 0.000 | 0.398 |
| Daily/weekly | daily energy CV | 0.088 | 0.097 | -0.009 |
| Peakiness | P95/P50 | 2.040 | 2.400 | -0.359 |
| Peakiness | top-decile LDC NMAE | 0.214 | 0.000 | 0.214 |
| Appliances | mean diurnal correlation | 0.483 | 1.000 | 0.517 |
| Lighting | mean diurnal correlation | 0.591 | 1.000 | 0.409 |
| Diversity | diversity factor | 3.427 | 6.445 | -3.018 |
| Occupancy | active fraction | 0.702 | 0.671 | 0.031 |

## Plots

- load_duration_curve: `richardson_load_duration_curve.png`
- mean_daily_baseload: `richardson_mean_daily_baseload.png`
- mean_daily_occupancy: `richardson_mean_daily_occupancy.png`

## Artifact Interpretation

- This is a cached validation artifact; verify its config and provenance before treating it as thesis evidence.
- This validation treats Richardson as an independent synthetic benchmark. It does not prove Belgian empirical accuracy and should be paired with measured aggregate validation.
