# Validation Report — Model v3 KU Leuven High-Frequency

## Validation Type

- classification: high-frequency/event realism
- interpretation: Three-household high-frequency case-study comparison; not a statistical validation claim.

## Runtime Context

- canonical thesis config: `config/model_v3/model_v3_thesis.yaml`
- canonical thesis runtime: reference year `2023`, `30` households, `simulation.max_steps: null`, climate disabled
- report reference year: `2023`
- report cohort households: `5`
- report minimum households: `5`
- report max steps: `168`
- quick mode: `True`
- climate enabled: `False`
- simulated/aligned model steps: `168`

## Artifact Interpretation

- This is a quick/debug artifact and is not thesis-valid evidence.
- This artifact is horizon-limited by `simulation.max_steps`.
- It covers fewer than a full non-leap-year hourly horizon.
- The monitored households are case studies; use this report for event-realism diagnostics only.

## Setup

- model reference year: 2023
- KU Leuven electricity files are loaded in chunks and reduced to 15-minute mean power profiles.
- Direct model-vs-house comparisons are aligned to the highest common resolution with the model output.

## house_1

- aligned timestamps: 168
- aligned comparison resolution (s): 3600

### Daily Max Distribution

- model mean daily max (W): 55777.624244
- house mean daily max (W): 2470.715476
- model p90 daily max (W): 77273.961259
- house p90 daily max (W): 3158.479056

### Spike Detection

- model threshold (W): 43246.701250
- house threshold (W): 2316.683672
- model spike count: 8.000000
- house spike count: 11.000000
- model spike share: 0.047619
- house spike share: 0.065476

### Ramp Rates

- model mean abs ramp (W/min): 83.025463
- house mean abs ramp (W/min): 5.386663
- model p90 abs ramp (W/min): 259.611996
- house p90 abs ramp (W/min): 13.672639
- model max abs ramp (W/min): 1028.170808
- house max abs ramp (W/min): 41.226361

### Statistical Moments

- model variance (W^2): 116352393.028439
- house variance (W^2): 402503.401523
- model skewness: 2.992040
- house skewness: 1.025026
- model kurtosis: 10.251378
- house kurtosis: 1.036325

### Visualisations

- 24h segment: ![Segment](house_1_24h_segment.png)
- spike comparison: ![Spikes](house_1_spike_comparison.png)
- ramp histogram: ![Ramps](house_1_ramp_histogram.png)

## house_2

- aligned timestamps: 0
- aligned comparison resolution (s): 3600

### Daily Max Distribution

- model mean daily max (W): 0.000000
- house mean daily max (W): 0.000000
- model p90 daily max (W): 0.000000
- house p90 daily max (W): 0.000000

### Spike Detection

- model threshold (W): 0.000000
- house threshold (W): 0.000000
- model spike count: 0.000000
- house spike count: 0.000000
- model spike share: 0.000000
- house spike share: 0.000000

### Ramp Rates

- model mean abs ramp (W/min): 0.000000
- house mean abs ramp (W/min): 0.000000
- model p90 abs ramp (W/min): 0.000000
- house p90 abs ramp (W/min): 0.000000
- model max abs ramp (W/min): 0.000000
- house max abs ramp (W/min): 0.000000

### Statistical Moments

- model variance (W^2): 0.000000
- house variance (W^2): 0.000000
- model skewness: 0.000000
- house skewness: 0.000000
- model kurtosis: 0.000000
- house kurtosis: 0.000000

### Visualisations

- 24h segment: ![Segment](house_2_24h_segment.png)
- spike comparison: ![Spikes](house_2_spike_comparison.png)
- ramp histogram: ![Ramps](house_2_ramp_histogram.png)

## house_3

- aligned timestamps: 0
- aligned comparison resolution (s): 3600

### Daily Max Distribution

- model mean daily max (W): 0.000000
- house mean daily max (W): 0.000000
- model p90 daily max (W): 0.000000
- house p90 daily max (W): 0.000000

### Spike Detection

- model threshold (W): 0.000000
- house threshold (W): 0.000000
- model spike count: 0.000000
- house spike count: 0.000000
- model spike share: 0.000000
- house spike share: 0.000000

### Ramp Rates

- model mean abs ramp (W/min): 0.000000
- house mean abs ramp (W/min): 0.000000
- model p90 abs ramp (W/min): 0.000000
- house p90 abs ramp (W/min): 0.000000
- model max abs ramp (W/min): 0.000000
- house max abs ramp (W/min): 0.000000

### Statistical Moments

- model variance (W^2): 0.000000
- house variance (W^2): 0.000000
- model skewness: 0.000000
- house skewness: 0.000000
- model kurtosis: 0.000000
- house kurtosis: 0.000000

### Visualisations

- 24h segment: ![Segment](house_3_24h_segment.png)
- spike comparison: ![Spikes](house_3_spike_comparison.png)
- ramp histogram: ![Ramps](house_3_ramp_histogram.png)

## Limitations

This is a high-frequency case-study validation based on three monitored households. It is not a statistical validation claim, and the model output remains hourly while the house data is reduced to 15-minute mean power before comparison.
