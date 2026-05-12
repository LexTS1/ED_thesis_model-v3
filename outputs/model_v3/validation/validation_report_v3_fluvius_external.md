# Validation Report — Model v3 Fluvius External

## Validation Type

- classification: aggregate validation
- interpretation: External aggregate-profile comparison against representative Fluvius load profiles; not measured feeder validation.

## Runtime Context

- canonical thesis config: `config/model_v3/model_v3_thesis.yaml`
- canonical thesis runtime: reference year `2023`, `30` households, `simulation.max_steps: null`, climate disabled
- report reference year: `2023`
- report cohort households: `30`
- report minimum households: `30`
- report max steps: `null`
- quick mode: `False`
- climate enabled: `False`
- simulated/aligned model steps: `8760`

## Artifact Interpretation

- This is a full-horizon candidate for thesis use, but cite it only with its report metadata and provenance.
- Fluvius profiles are representative profiles, so this report supports aggregate-profile plausibility only.

## Alignment

- model resolution (s): 3600
- data resolution (s): 900
- target resolution (s): 3600
- matched timestamps: 8757

## Unit Interpretation

- Fluvius input files are interpreted as interval energy in `kWh per 15-minute interval`.
- Fluvius representative profiles are converted to `kW` via `E / 0.25 h`.
- Model and external profiles are compared in absolute aggregate `W` after household scaling.

## Normalization / Calibration Caveat

The model profile is scaled from per-household output to an aggregate using the configured household count. Annual electricity calibration remains tied to the Belgian baseline, so this report should not be read as independent feeder-level calibration.

## Scaling Explanation

- comparison mode: absolute aggregate
- model representation before scaling: per_household
- data representation before scaling: representative household profile
- households applied to model: 30
- households applied to Fluvius profile: 30
- weighted profile groups: base, ev, hp, ev_hp

## Fluvius Profile Composition

- base: weight=0.600, profiles=base_no_pv, base_with_pv
- ev: weight=0.200, profiles=ev_no_pv, ev_with_pv
- hp: weight=0.150, profiles=hp_no_pv, hp_with_pv
- ev_hp: weight=0.050, profiles=ev_hp_no_pv, ev_hp_with_pv

## Fluvius Loader Warnings

- base: multiple PV variants present (base_no_pv, base_with_pv); using an unweighted within-category mean because no finer-grained PV split is configured
- ev: multiple PV variants present (ev_no_pv, ev_with_pv); using an unweighted within-category mean because no finer-grained PV split is configured
- hp: multiple PV variants present (hp_no_pv, hp_with_pv); using an unweighted within-category mean because no finer-grained PV split is configured
- ev_hp: multiple PV variants present (ev_hp_no_pv, ev_hp_with_pv); using an unweighted within-category mean because no finer-grained PV split is configured
- fluvius_weighted_profile: irregular sampling detected (modal timestep 900s)
- fluvius_weighted_profile: missing timestamps detected (4)
- dropped 96 timestamps that cannot be mapped into reference year 2023
- collapsed 8 duplicate timestamps after reference-year mapping

## Validation Independence

- dataset_independent: True
- partial_overlap: False
- validation_independence: strong
- implications: Validation dataset appears independent from configured inputs.


## Mean Accuracy

- MBE: 804.153791
- MAE: 5489.103102
- RMSE: 8437.168346
- CVRMSE: 67.214445

## Variance Realism

- variance_model: 50253881.982930
- variance_data: 27567664.538285
- CV_model: 0.530742
- CV_data: 0.418279
- Levene_statistic: 19.743934
- Levene_p_value: 0.000009

## Distribution Realism

- Anderson_Darling_statistic: 386.822759
- P10_error: 1382.699752
- P50_error: 60.897692
- P90_error: -754.426584

## Temporal Structure

- Pearson_correlation: 0.097719
- autocorrelation_difference_mean: 0.600761
- autocorrelation_difference_max: 0.827853
- peak_timing_error_hours: 16.000000

## External Aggregate Metrics

- peak_error_pct: 546.611080
- annual_energy_error_pct: 6.388038
- CVRMSE_absolute_pct: 67.214445
- load_duration_curve_mae_kW: 1.284225

## Visualisations

- Absolute aggregate overlay: ![Overlay](mean_daily_profile_overlay.png)
- Load duration curve: ![LDC](load_duration_curve.png)
- Variance by hour: ![Variance](variance_by_hour.png)

## Limitations

Fluvius profiles are representative, not measured feeder data.
