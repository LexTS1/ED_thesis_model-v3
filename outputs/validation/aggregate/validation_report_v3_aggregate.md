# Validation Report — Model v3

## Validation Type

- classification: internal aggregate diagnostic
- interpretation: Normalized aggregate shape diagnostic against an explicitly configured aggregate reference. Do not use LCL here as thesis-facing validation because LCL is the input load-shape source.

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
- Normalized aggregate metrics cannot support absolute calibration claims; read the threshold table for overall status.

## Alignment

- model resolution (s): 3600
- data resolution (s): 1800
- target resolution (s): 3600
- matched timestamps: 24

## Mean accuracy

- MBE: 0.000000
- MAE: 0.207000
- RMSE: 0.256982
- CVRMSE: 25.698195

## Variance realism

- variance_model: 0.088716
- variance_data: 0.112791
- CV_model: 0.297853
- CV_data: 0.335843
- Levene_statistic: 1.539716
- Levene_p_value: 0.220951

## Distribution realism

- Anderson_Darling_statistic: 0.802568
- P10_error: 0.227425
- P50_error: -0.160263
- P90_error: -0.103575
- LDC plot: ![LDC](load_duration_curve.png)

## Temporal structure

- Pearson_correlation: 0.662769
- autocorrelation_difference_mean: 0.533413
- autocorrelation_difference_max: 1.517057
- peak_timing_error_hours: 1.000000

## Event-based validation

- seasonal_shape_MAE: 0.000000
- seasonal_peak_month_error: 0.000000
- peak_day_error: 0.393097
- peak_MAE_kW: 0.000000
- extreme_condition_error: 0.000000

## Validation vs Literature Thresholds

| Metric | Model | Acceptable | Good | Status |
| --- | ---: | ---: | ---: | --- |
| MBE monthly | 0.000 | <= 0.050 | - | PASS |
| CVRMSE monthly | 0.000 | <= 0.150 | - | PASS |
| MBE hourly | 0.000 | <= 0.100 | <= 0.050 | PASS |
| CVRMSE hourly | 0.257 | <= 0.300 | <= 0.200 | PASS |
| Peak MAE (kW) | 0.000 | <= 0.200 | <= 0.100 | PASS |
| Quantile error P10 (kW) | 0.000 | <= 0.200 | <= 0.100 | PASS |
| Quantile error P90 (kW) | 0.000 | <= 0.200 | <= 0.100 | PASS |
| Overall | PASS | all critical pass | - | PASS |

## Visualisations

- Mean daily profile overlay: ![Overlay](mean_daily_profile_overlay.png)
- Load duration curve: ![LDC](load_duration_curve.png)
- Variance by hour: ![Variance](variance_by_hour.png)
- Uncertainty bands: ![Bands](uncertainty_bands.png)

## Validation Independence / Data Role

- dataset_independent: False
- partial_overlap: True
- validation_independence: weak
- implications: Validation uses the same dataset path as at least one model input, so the result is not independent.

## Normalization / Calibration Caveat

Both model and reference series are divided by their own means before metric calculation. The result is a shape comparison only and does not validate annual electricity totals or household scaling.

## What this script does not validate

This script validates normalized aggregate demand shape, seasonal variation, and peak timing. It does not validate appliance attribution or absolute household totals.
