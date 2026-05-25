# Heat Pump COP Bounded Alignment

## Purpose

This note documents a bounded calibration of the model_v3 heat-pump COP curve. It is not a full validation against metered Belgian household data. The objective is to keep the hourly COP model physically plausible and broadly aligned with published seasonal performance factors (SPF) while avoiding long scenario-tree reruns or overfitting.

## Benchmarks Used

The alignment uses three benchmark anchors:

- Existing Belgian technology inputs in `config/belgian_technology_inputs.yaml`, especially the KU Leuven heat-pump SPF ranges already encoded for Belgian context: air-water radiators 2.3-2.6, low-temperature air-water 2.5-3.0, ground-source low-temperature 3.3-4.0, and high-temperature retrofit 1.8-2.1.
- Belgian monitored air-to-water low-energy house reported by HPT: average building-heating COP around 4.1, DHW COP around 2.4, and whole-system heating-season SPF around 3.3. Source: https://heatpumpingtechnologies.org/publications/performance-of-an-air-to-water-heat-pump-system-in-alow-energy-residential-buildingmonitoring-results-and-modeling/
- Nearby-climate Fraunhofer field trials: air-source heat-pump SPF values around 2.9-3.4 on average, with ground-source systems generally higher, around 3.9-4.1 on average and wider monitored ranges. Sources: https://www.ise.fraunhofer.de/en/research-projects/wp-effizienz.html and https://www.ise.fraunhofer.de/en/research-projects/wp-qs-im-bestand.html
- Belgian shallow ground-temperature context from Brugeo: natural ground temperature around 10-14 degC at 20-30 m depth. The model uses a lower entering-source/brine proxy of 4/8/12 degC to account for closed-loop extraction and source-side losses. Source: https://geothermie.brussels/en/the-principles-of-geothermics/shallow-geothermics

## Method

The comparison uses the processed baseline climate file and a simple heat-degree proxy:

```text
Q_proxy = max(18 degC - T_outdoor, 0)
seasonal_COP = sum(Q_proxy) / sum(Q_proxy / hourly_COP)
```

This isolates the COP curve behavior from cohort sampling, appliance/DHW stochasticity, and scenario-tree runtime. It is therefore suitable for parameter alignment, not for validating final household energy use.

## Parameter Changes

The calibrated defaults are intentionally limited to a few interpretable parameters:

| Technology | Parameter | Previous | Updated |
|---|---:|---:|---:|
| air-water | `cop_ref` | 5.0 | 4.4 |
| air-water | `min_cop` | 2.2 | 2.0 |
| air-water | `max_cop` | 5.8 | 5.0 |
| air-water | `defrost_penalty_fraction` | 0.07 | 0.12 |
| air-air | `cop_ref` | 5.5 | 4.4 |
| air-air | `max_cop` | 6.0 | 5.3 |
| air-air | `defrost_penalty_fraction` | 0.07 | 0.12 |
| ground-source | `cop_ref` | 4.2 | 3.8 |
| ground-source | `max_cop` | 6.0 | 5.2 |
| ground-source | `ground_source_temperature_C` | 0 degC scalar fallback | 4/8/12 degC range, base used |

Emitter assumptions were also adjusted modestly to avoid unrealistically low mild-weather radiator supply temperatures in retrofit cases:

| Emitter | Previous low/high sink degC | Updated low/high sink degC |
|---|---:|---:|
| standard radiators | 38 / 55 | 42 / 55 |
| low-temperature radiators | 32 / 45 | 35 / 45 |
| high-temperature radiators | 40 / 60 | 45 / 60 |
| underfloor | 27 / 35 | unchanged |

## Alignment Result

Heat-degree-weighted seasonal COP values are:

| Technology / emitter | Previous curve | Updated curve | Interpretation |
|---|---:|---:|---|
| air-water, standard radiators | 3.38 | 2.62 | Aligned with Belgian radiator SPF range and nearby-climate field averages. |
| air-water, low-temperature radiators | 4.10 | 3.28 | Lower than previous optimistic curve; still plausible for improved emitters. |
| air-water, underfloor | 4.78 | 4.03 | Close to the Belgian monitored low-energy floor-heating heating COP anchor. |
| air-air | 4.80 | 3.62 | Reduced to a more defensible mild-climate seasonal range. |
| ground-source, standard radiators | 3.28 | 3.11 | Penalized by higher sink temperature, useful for retrofit sensitivity. |
| ground-source, low-temperature radiators | 3.93 | 3.75 | Within the encoded KU Leuven ground-source range. |
| ground-source, underfloor | not reported | 4.52 | Plausible upper bound for low sink temperatures, not used as stock average. |

## Hybrid Control Update

The hybrid heat-pump/gas branch no longer uses a fixed heat-pump load fraction. It now uses a minimum bivalent-parallel rule:

- heat pump is allowed only above `hp_min_outdoor_temperature_C`;
- current effective COP must be at least `hp_min_cop`;
- sink temperature must not exceed `hp_max_sink_temperature_C`;
- heat-pump useful heat is capped by `capacity_W * hp_capacity_fraction * capacity_available_fraction`;
- gas boiler supplies residual or full lockout heat.

Price-based switching is intentionally excluded because energy prices are not part of the model input contract. Outdoor temperature, COP, sink temperature, and capacity are already represented in the existing physical model and are sufficient for a minimum defensible hybrid improvement.

## Caveats

- The calibration uses literature and monitoring ranges as bounding evidence, not household-level Belgian metered validation.
- SPF benchmarks differ in system boundaries. Some include pumps, controls, DHW, storage losses, or auxiliary backup; the model COP is primarily equipment-space-heating conversion.
- The ground-source implementation remains a simple closed-loop/brine-source proxy, not a detailed borefield thermal model.
- Carnot efficiency is not explicitly enforced in this iteration. The lower calibrated reference COPs, caps, and defrost penalty are a limited-scope correction intended to improve thesis defensibility without changing the model class.
