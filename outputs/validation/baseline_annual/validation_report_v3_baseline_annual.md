# Validation Report — Model v3 Annual Baseline

## Validation Type

- classification: baseline/literature annual calibration
- interpretation: Annual comparison against configured Belgian household literature targets and end-use shares.

## Runtime Context

- canonical thesis config: `config/thesis.yaml`
- canonical thesis runtime: reference year `2023`, `30` households, `simulation.max_steps: null`, climate disabled
- report reference year: `2023`
- report cohort households: `30`
- report minimum households: `30`
- report max steps: `24`
- quick mode: `False`
- climate enabled: `True`
- simulated/aligned model steps: `24`

## Artifact Interpretation

- This artifact is horizon-limited by `simulation.max_steps`.
- It covers fewer than a full non-leap-year hourly horizon.
- Thermal annual validation requires a full annual horizon; calibrated electricity totals can match targets even when a truncated thermal run is not thesis-valid.

## Execution Mode

- quick mode: False
- debug only: False
- max steps: 24
- overrides: none

## Annual baseline check

- annual electricity (kWh): 3900.000
- space heating thermal (kWh): 1.846
- DHW thermal (kWh): 9.607

## Baseline vs Literature

| Quantity | Model | Literature range | Status |
| --- | ---: | ---: | --- |
| Annual electricity (kWh) | 3900.000 | [3600, 3900] | PASS |
| Space heating thermal (kWh) | 1.846 | [12000, 16000] | FAIL |
| DHW thermal (kWh) | 9.607 | [2500, 3300] | FAIL |

## End-use shares

| End use | Model share | Literature share | Abs. error |
| --- | ---: | ---: | ---: |
| appliances | 0.529 | 0.529 | 0.000 |
| lighting | 0.073 | 0.073 | 0.000 |
| cooking | 0.068 | 0.068 | 0.000 |
| dhw | 0.192 | 0.192 | 0.000 |
| space_heating | 0.138 | 0.138 | 0.000 |

## Electricity calibration

| End use | Raw kWh | Calibrated kWh | Target kWh | Scale factor |
| --- | ---: | ---: | ---: | ---: |
| appliances | 6.611 | 2064.242 | 2064.242 | 312.258861 |
| lighting | 0.908 | 283.636 | 283.636 | 312.258861 |
| cooking | 0.845 | 263.939 | 263.939 | 312.258861 |
| dhw | 2.397 | 748.485 | 748.485 | 312.258861 |
| space_heating | 0.083 | 539.697 | 539.697 | 6499.188968 |

## Normalization / Calibration Caveat

The annual electricity values in this report are calibrated to the configured literature target split. Use raw kWh and scale factors to interpret pre-calibration behavior. Thermal space-heating and DHW totals are not normalized by this electricity calibration and require a full-horizon run for annual interpretation.

## Validation Independence / Data Role

- dataset_independent: True
- partial_overlap: False
- validation_independence: strong
- implications: Validation dataset appears independent from configured inputs.

## What this script does not validate

This script checks annual totals and end-use shares against literature synthesis only. It does not validate hourly timing, variance realism, or measured-load agreement.
