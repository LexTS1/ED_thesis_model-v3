# Heat Pump Implementation in model_v3

This document explains how heat pumps are represented in `model_v3` from two viewpoints. The first part gives the physical and engineering interpretation: what technologies are represented, what COP means in the model, and why COP changes over hours, months, years, and climate pathways. The second part maps the same concepts to the software architecture: where the parameters are configured, where household assignment happens, where hourly COP is calculated, and where diagnostics are written.

The implementation is intentionally empirical and bounded. It is not a detailed manufacturer map, refrigerant cycle simulation, or explicit Carnot-efficiency model. The model uses a source/sink temperature COP curve with calibrated bounds, defrost and part-load corrections, and simple capacity derating. This is defensible for cohort and scenario analysis because the purpose is to capture the first-order dependence of heat-pump performance on climate, emitter temperature, and household-level variation without turning the thesis model into equipment certification software.

## 1. Physical and engineering perspective

### 1.1 What heat-pump types exist in the model

The model distinguishes the following heat-pump-related technologies:

| Internal label | Meaning in the model | Main energy carrier | Main use |
|---|---|---:|---|
| `air_water` | Air-source heat pump feeding a hydronic space-heating system | Electricity | Space heating |
| `air_air` | Air-source heat pump delivering heat through air distribution | Electricity | Space heating |
| `ground_source` | Closed-loop/brine-source hydronic heat pump | Electricity | Space heating |
| `hybrid_hp_gas` | Air-to-water heat pump plus gas boiler backup | Electricity + gas | Space heating |
| `hpwh` | Heat-pump water heater | Electricity | Domestic hot water |

The hybrid heat pump uses the same physical COP curve as `air_water`, because it is modelled as an air-source hydronic heat pump with a gas boiler available for residual or lockout heat.

### 1.2 Useful heat, delivered energy, and COP

The system model works from useful heat demand to delivered energy demand.

For a non-hybrid heat pump:

```text
electric_power_W = useful_heat_W / effective_COP
```

For a boiler:

```text
fuel_power_W = useful_heat_W / efficiency
```

For a hybrid heat pump, useful heat is split first:

```text
useful_heat_W = hp_useful_heat_W + gas_useful_heat_W
electric_power_W = hp_useful_heat_W / effective_COP
gas_power_W = gas_useful_heat_W / gas_boiler_efficiency
```

COP is therefore interpreted as useful heat out per unit electrical energy into the heat pump during that hour. It is not a fixed annual SPF. It is recalculated at each operating point.

### 1.3 The COP curve

For each hourly operating point, the model first computes a base COP from source and sink temperature:

```text
base_COP =
    cop_ref
    + source_slope_per_K * (source_temperature_C - source_ref_C)
    - sink_slope_per_K * (sink_temperature_C - sink_ref_C)
```

Then the base COP is clamped to a technology-specific interval:

```text
base_COP = clamp(base_COP, min_COP, max_COP)
```

After that, the model applies defrost and part-load corrections:

```text
effective_COP = clamp(base_COP * defrost_factor * part_load_factor, min_COP, max_COP)
```

The signs are physically meaningful:

- A warmer source increases COP because the compressor has a smaller temperature lift.
- A hotter sink decreases COP because the heat pump must deliver heat at a higher temperature.
- COP is bounded so the simple linear curve does not produce implausibly low or high values outside the calibration range.

The calibrated default parameters are:

| Type | Source ref | Sink ref | `cop_ref` | `min_cop` | `max_cop` | Source slope | Sink slope | Defrost penalty |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `air_water` | 7 degC | 35 degC | 4.4 | 2.0 | 5.0 | 0.11/K | 0.095/K | 0.12 |
| `air_air` | 7 degC | 20 degC | 4.4 | 2.0 | 5.3 | 0.14/K | 0.03/K | 0.12 |
| `ground_source` | 0 degC | 35 degC | 3.8 | 3.0 | 5.2 | 0.045/K | 0.085/K | 0.00 |
| `hpwh` | 15 degC | 55 degC | 3.1 | 2.0 | 3.5 | 0.05/K | 0.055/K | 0.00 |

The numbers are not claimed as a full validation. They are a bounded alignment with Belgian and nearby-climate monitoring/SPF benchmarks, while retaining a simple hourly curve.

### 1.4 What `clamp()` means physically

`clamp(value, lower, upper)` limits a calculated value to a defensible interval:

```text
clamp(value, lower, upper) = min(max(value, lower), upper)
```

For COP, this means:

- If the linear curve gives a COP below `min_cop`, the model uses `min_cop`.
- If the linear curve gives a COP above `max_cop`, the model uses `max_cop`.
- Otherwise, the model uses the calculated value.

This avoids false precision from a simple empirical curve. For example, an unusually warm source temperature should not make the model invent unrealistically high COP values, and an extreme cold hour should not drive COP below the calibrated lower operating bound.

### 1.5 Source temperature assumptions

The source temperature depends on the technology:

| Type | Source temperature used by model |
|---|---|
| `air_water` | Current outdoor air temperature |
| `air_air` | Current outdoor air temperature |
| `hybrid_hp_gas` | Current outdoor air temperature, because the HP part is treated as air-to-water |
| `ground_source` | Configured ground/brine source temperature, default base 8 degC |
| `hpwh` | Configured DHW ambient source temperature, default 15 degC |

For air-source systems, COP therefore reacts directly to the hourly weather file. Cold hours reduce COP, mild hours raise COP, and the defrost penalty applies in the outdoor/source range from -3 degC to 6 degC.

For ground-source systems, the source side is more stable. The current configuration allows:

```yaml
systems:
  heating:
    ground_source_temperature_C:
      low: 4.0
      base: 8.0
      high: 12.0
```

Runtime uses the `base` value deterministically. The low/base/high values document plausible uncertainty, but the current model does not randomly sample the ground-source temperature. The 8 degC base represents entering brine/source temperature after allowing for the fact that a closed-loop heat exchanger fluid can be cooler than undisturbed Belgian ground temperature.

### 1.6 Sink temperature and emitter assumptions

For hydronic space-heating heat pumps, the sink temperature is the heating supply temperature. The model calculates it with a weather curve unless a fixed `sink_temperature_C` is configured.

The weather curve moves between a mild-weather sink temperature and a design-cold sink temperature:

```text
fraction =
    (mild_outdoor_C - outdoor_temperature_C)
    / (mild_outdoor_C - design_outdoor_C)

fraction = clamp(fraction, 0, 1)

sink_temperature_C =
    sink_low_C + fraction * (sink_high_C - sink_low_C)
```

Default design and mild outdoor temperatures are -7 degC and 15 degC. The calibrated emitter assumptions are:

| Emitter type | Sink at design cold condition | Sink at mild condition |
|---|---:|---:|
| `underfloor` | 35 degC | 27 degC |
| `low_temperature_radiators` | 45 degC | 35 degC |
| `standard_radiators` | 55 degC | 42 degC |
| `high_temperature_radiators` | 60 degC | 45 degC |
| `fan_coils` | 42 degC | 28 degC |

This matters because high-temperature emitters reduce COP. Underfloor and low-temperature radiators generally improve COP because the heat pump can deliver useful heat at a lower supply temperature.

For `air_air`, the sink is the indoor setpoint or configured air sink temperature. For `hpwh`, the sink is the DHW tank or configured DHW sink temperature.

### 1.7 Defrost and part-load corrections

Air-source heat pumps receive a defrost penalty when operating in heating mode and the source temperature is between -3 degC and 6 degC:

```text
defrost_factor = 1 - defrost_penalty_fraction
```

With the calibrated air-source penalty of 0.12, the factor is 0.88 in that temperature band. Outside that band, the factor is 1.0.

The part-load correction represents cycling losses at very low load. The model computes:

```text
part_load_ratio = useful_heat_W / capacity_W
```

If the part-load ratio is above the technology minimum, `part_load_factor` is 1.0. Below that minimum, the model reduces COP toward the technology degradation coefficient. This is a compact way to account for cycling without simulating compressor on/off cycles.

### 1.8 Capacity derating

Air-source heat-pump capacity can fall in very cold conditions. The model uses a simple linear capacity fraction:

- If source temperature is above the reference low-temperature point, available capacity fraction is 1.0.
- If source temperature is below the lower point, available capacity fraction is the configured low-temperature fraction.
- Between those points, the fraction is linearly interpolated.

For `air_water`, capacity is 100 percent at -7 degC and 80 percent at -15 degC. For `air_air`, capacity is 85 percent at -7 degC and 75 percent at -15 degC. Ground-source and `hpwh` currently use no temperature capacity derating beyond their default constant fraction.

Capacity derating matters most for hybrid systems, because the gas boiler supplies any useful heat that the heat pump cannot cover.

### 1.9 Hybrid heat pump control

The hybrid system is modelled as bivalent-parallel control. The heat pump is considered first, but it is only allowed when all of the following are true:

```text
outdoor_temperature_C >= hp_min_outdoor_temperature_C
effective_COP >= hp_min_cop
sink_temperature_C <= hp_max_sink_temperature_C
available_HP_capacity_W > 0
```

The current defaults are:

```yaml
hybrid_hp:
  control:
    dhw_by_boiler: true
    hp_capacity_fraction: 0.65
    hp_min_outdoor_temperature_C: -5.0
    hp_min_cop: 2.5
    hp_max_sink_temperature_C: 55.0
```

The heat pump does not necessarily cover the full heat load. It is limited by:

```text
hp_nominal_capacity_W = system_capacity_W * hp_capacity_fraction
hp_available_capacity_W = hp_nominal_capacity_W * capacity_available_fraction
hp_useful_heat_W = min(useful_heat_W, hp_available_capacity_W)
gas_useful_heat_W = useful_heat_W - hp_useful_heat_W
```

If the heat pump is disallowed by outdoor temperature, COP, sink temperature, or capacity, the gas boiler supplies all useful heat for that hour. If the heat pump is allowed but cannot cover the full load, the system runs in parallel mode.

Price-based dispatch is deliberately out of scope in this version because electricity and gas prices are not part of the model's hourly control inputs. The implemented controls use variables already present in the physical model: outdoor temperature, COP, sink temperature, and capacity.

### 1.10 How COP changes during a day, month, year, and climate pathway

The model recalculates COP every timestep. Therefore, COP is not assigned once for the whole year.

For air-source heat pumps, COP changes mainly because:

- Outdoor/source temperature changes hourly.
- Hydronic sink temperature changes with the weather curve.
- Load changes with building heat demand and indoor conditions.
- Defrost applies only in the source temperature band from -3 degC to 6 degC.
- Part-load factor changes when useful heat is small relative to available capacity.

For ground-source heat pumps, COP is less sensitive to outdoor temperature on the source side because the source temperature is fixed at the configured ground/brine value. It can still change over the year because hydronic sink temperature follows outdoor temperature through the emitter weather curve, and because part-load conditions vary.

For monthly or seasonal interpretation, the correct engineering aggregate is not the arithmetic mean of hourly COP values. The defensible aggregate is heat-weighted:

```text
monthly_effective_COP =
    sum(useful_heat_W over month)
    / sum(electric_heat_pump_input_W over month)
```

This gives cold, high-load hours the correct weight. A mild hour with high COP but little heating demand should not dominate the monthly SPF-like result.

Climate windows and pathways affect heat-pump COP through the weather forcing. A warmer climate pathway generally changes:

- the distribution of outdoor/source temperatures for air-source systems;
- the number of heating hours;
- the weather-curve sink temperatures during heating hours;
- the coincidence of high load with low COP;
- the frequency of defrost-band operation.

The model does not assign a special COP curve to a climate pathway. Instead, the pathway selects a different hourly weather series. That hourly weather series drives the same physical COP formula. This is important methodologically: technology assumptions and climate assumptions remain separable, while their interaction emerges through the hourly simulation.

### 1.11 Different households can have different heat-pump COPs

Two households with the same heat-pump type can have different hourly COPs. The main reasons are:

- They may be assigned different heat-pump technologies.
- They may be assigned different emitter types.
- They may have different sampled `cop_scale` values.
- They may have different heating capacity scaling, changing part-load behaviour.
- They may have different thermal demand profiles because of building and behaviour sampling.
- They may have different indoor setpoint shifts, affecting air-air sink temperature and load.

The base heat-pump type determines the calibrated curve, but the household-specific sampled configuration can shift the reference COP and operating conditions.

### 1.12 Relationship to SCOP/SPF and Carnot

The model calculates hourly effective COP. Seasonal COP, SCOP, or SPF-like quantities are aggregates derived from hourly output and input energy:

```text
seasonal_COP_or_SPF =
    sum(useful_heat_output)
    / sum(electric_input_to_heat_pump)
```

This is why SCOP/SPF is useful for reporting and validation, but less useful as the core runtime variable. A fixed SCOP would hide the hourly interaction between weather, emitter temperature, defrost, part load, capacity limits, and hybrid backup.

Carnot is not enforced as a runtime constraint. The model is empirical and bounded: it uses calibrated min/max COP limits and monitored SPF alignment to avoid implausible values. This is a deliberate scope choice. Adding a Carnot efficiency layer would require additional assumptions about evaporating/condensing temperature approaches and would look more physically detailed than the available calibration supports.

## 2. Software implementation perspective

### 2.1 Main implementation files

The heat-pump implementation is spread across a small number of files:

| File | Responsibility |
|---|---|
| `src/model_v3/systems/heat_pump_performance.py` | COP curve, source/sink temperature, defrost, part-load, capacity derating |
| `src/model_v3/systems/technology.py` | Converts useful heat to electricity/gas/fuel carriers; implements hybrid dispatch |
| `src/model_v3/systems/system_core.py` | Calls technology conversion during the thermal system step and stores diagnostics in metadata |
| `src/model_v3/simulation/annual_runner.py` | Writes hourly annual-profile output columns |
| `src/model_v3/stochastic/sampler.py` | Samples household technology type, emitter type, COP scale, and capacity scale |
| `src/model_v3/cohort/household_runner.py` | Applies sampled household technology parameters to the per-household config |
| `src/model_v3/scenarios/model_runner_adapter.py` | Translates scenario-tree technology cases into model config overrides |
| `config/model.yaml` and `config/thesis.yaml` | Main model heat-pump performance defaults |
| `config/belgian_technology_inputs.yaml` | Belgian technology stock/performance assumptions and hybrid controls |

### 2.2 Technology labels and aliases

Technology labels are normalized in `src/model_v3/systems/technology.py`. For example:

- `heat_pump` maps to `air_water`;
- `hybrid`, `hybrid_hp`, and `hybrid_hp_gas` map to `hybrid_hp_gas`;
- `heat_pump_water_heater` maps to `hpwh`.

This normalization avoids separate branches for equivalent labels in scenario inputs, Belgian stock mappings, or test configs.

The set of heat-pump technologies that use the hourly COP model is:

```python
HEAT_PUMP_TECHNOLOGIES = {"air_water", "air_air", "ground_source", "hpwh"}
```

The hybrid case is handled separately because it needs both heat-pump COP calculation and gas boiler residual dispatch.

### 2.3 COP parameter definitions

The default engineering parameters are stored as dataclass instances in `HEAT_PUMP_SPECS` in `heat_pump_performance.py`. Each `HeatPumpSpec` defines:

- `hp_type`;
- `default_refrigerant`;
- reference source and sink temperatures;
- `cop_ref`;
- `min_cop` and `max_cop`;
- source and sink slopes;
- defrost penalty;
- part-load parameters;
- capacity derating parameters.

Emitter assumptions are stored separately in `EMITTER_SPECS`. This separation is useful because the same heat-pump type can have different performance depending on whether it serves underfloor heating, low-temperature radiators, standard radiators, or high-temperature radiators.

Config-level overrides can be supplied under:

```yaml
systems:
  heat_pump_performance:
    types:
      air_water:
        cop_ref: 4.4
        min_cop: 2.0
        max_cop: 5.0
        defrost_penalty_fraction: 0.12
      air_air:
        cop_ref: 4.4
        min_cop: 2.0
        max_cop: 5.3
        defrost_penalty_fraction: 0.12
      ground_source:
        cop_ref: 3.8
        min_cop: 3.0
        max_cop: 5.2
```

For `cop_ref`, the implementation gives household-level `systems.heating.cop_ref` precedence over the type default. That is important because the stochastic sampler can apply a household-specific `cop_scale`. Type-level config still supplies calibrated min/max bounds and other type parameters.

### 2.4 Runtime COP calculation path

The central function is:

```python
heat_pump_performance(
    hp_type,
    systems_cfg=...,
    outdoor_temperature_c=...,
    indoor_setpoint_c=...,
    useful_heat_w=...,
    capacity_w=...,
    mode="heating",
)
```

The sequence is:

1. Normalize the heat-pump type.
2. Treat `hybrid_hp_gas` as `air_water` for performance calculation.
3. Read `systems.heating`, `systems.dhw`, and `systems.heat_pump_performance`.
4. Resolve source temperature.
5. Resolve sink temperature.
6. Resolve `cop_ref`, `min_cop`, `max_cop`, and slopes.
7. Calculate `base_cop`.
8. Clamp `base_cop`.
9. Apply defrost factor when relevant.
10. Calculate part-load ratio and part-load factor.
11. Clamp final effective COP.
12. Return COP plus diagnostic fields.

The returned diagnostic dictionary includes:

```text
cop
cop_base
hp_type
performance_hp_type
emitter_type
refrigerant
source_temperature_C
sink_temperature_C
defrost_factor
part_load_ratio
part_load_factor
capacity_available_fraction
```

These diagnostics are intentionally exposed so the COP can be audited instead of treated as a hidden constant.

### 2.5 Carrier conversion path

The system does not directly add COP to electricity demand. Instead, `convert_heat_to_carriers()` in `technology.py` converts useful heat to carrier powers.

For a non-hybrid heat pump:

1. The function detects that the normalized technology is in `HEAT_PUMP_TECHNOLOGIES`.
2. It calls `heat_pump_performance(...)`.
3. It sets the carrier to electricity.
4. It writes:

```text
P_el_space_heating_technology_W = useful_heat_W / heat_pump_cop
```

or, for DHW:

```text
P_el_dhw_technology_W = useful_heat_W / heat_pump_cop
```

For non-heat-pump technologies, the same function uses the configured conversion efficiency or SPF-style factor and writes the appropriate gas, oil, biomass, propane, coal, district heat, or electricity column.

### 2.6 Hybrid dispatch path

`hybrid_hp_gas` has a dedicated branch inside `convert_heat_to_carriers()` for `prefix == "space_heating"`.

The code:

1. Reads `technologies.heating.performance.hybrid_hp.control`.
2. Computes the nominal heat-pump capacity as `system_capacity_W * hp_capacity_fraction`.
3. Calls `heat_pump_performance("hybrid_hp_gas", ...)` to get the current COP, sink temperature, and capacity fraction.
4. Computes available heat-pump capacity after temperature derating.
5. Checks outdoor, COP, sink-temperature, and capacity conditions.
6. Assigns useful heat to the heat pump if allowed.
7. Sends all residual heat to the gas boiler.
8. Writes electricity and gas powers to the existing carrier columns.
9. Adds hybrid diagnostics.

The possible dispatch labels are:

| Dispatch mode | Meaning |
|---|---|
| `hp_only` | HP is allowed and can cover the full useful heat demand |
| `parallel` | HP is allowed but capacity-limited; gas supplies residual heat |
| `gas_only_outdoor_lockout` | Outdoor temperature is below the configured HP threshold |
| `gas_only_sink_temperature_lockout` | Required sink temperature exceeds the configured HP threshold |
| `gas_only_cop_lockout` | Current COP is below the configured useful threshold |
| `gas_only_capacity_unavailable` | HP has no useful available capacity |
| `no_heat_demand` | Useful heat demand is zero |

The existing electricity and gas output columns remain unchanged. The hybrid logic only changes how much useful heat is allocated to each carrier.

### 2.7 Household technology assignment

Household assignment is stochastic when the cohort model is enabled. In `sample_household_parameters()`:

1. The model resolves heating technology probabilities.
2. It samples one heating technology label for the household.
3. It samples an emitter type.
4. It samples a physical `cop_scale`.
5. It samples a heating capacity scale.
6. It stores all sampled technology parameters in the household parameter dictionary.

Technology probabilities can come from:

- explicit scenario-case assignment;
- Belgian current stock carrier mapping;
- legacy heat-pump/resistive uncertainty settings.

The household runner then applies these sampled parameters to a copied household config:

```text
systems.heating.technology_type = sampled technology
systems.heating.emitter_type = sampled emitter
systems.heating.capacity_W = base capacity * sampled capacity scale
systems.heating.cop_ref = sampled technology cop_ref * sampled cop_scale
```

This is why COP is household-specific even before hourly weather is considered. Each household can have a different technology, emitter, capacity, and `cop_ref` multiplier.

### 2.8 Scenario-tree technology assignment

Scenario leaves are translated to model configs in `model_runner_adapter.py`. The scenario technology case can provide heating technology probabilities and DHW technology probabilities. These are passed into:

```yaml
uncertainty:
  technology:
    heating_technology_probabilities: ...
    dhw_technology_probabilities: ...
```

If a heat-pump adoption case is active, the adapter sets heat-pump-relevant defaults such as `systems.heating.emitter_type` and `systems.heating.refrigerant`, but it does not force a single generic `cop_ref`. That allows the calibrated type-specific COP defaults under `systems.heat_pump_performance.types` to remain active and still allows household-level stochastic COP scaling.

### 2.9 Climate windows and pathways in software terms

Climate windows and pathways enter the heat-pump calculation through the input weather data, especially hourly `T_outdoor_C`.

At runtime, each annual simulation row has a current outdoor temperature from the loaded forcing data. The annual runner passes that outdoor temperature through the control/system state into the system conversion layer. Heat-pump performance then uses it as:

- the source temperature for `air_water`, `air_air`, and hybrid HP;
- the independent variable for the hydronic sink weather curve;
- the condition for defrost;
- the input for capacity derating.

For a different climate window or pathway, the same heat-pump code runs against a different hourly weather profile. No separate climate-pathway COP table is used. This keeps the architecture simple and physically interpretable: the same technology behaves differently because the operating conditions are different.

### 2.10 Diagnostics in annual outputs

The system metadata records heat-pump diagnostics during each system step. The annual runner writes a subset of those diagnostics into the hourly profile:

```text
heating_heat_pump_cop
heating_heat_pump_source_temperature_C
heating_heat_pump_sink_temperature_C
hybrid_hp_useful_heat_W
hybrid_gas_useful_heat_W
hybrid_hp_available_capacity_W
hybrid_dispatch_mode
```

Other diagnostic fields are available in system metadata, including:

```text
heating_heat_pump_cop_base
heating_heat_pump_emitter_type
heating_heat_pump_refrigerant
heating_heat_pump_defrost_factor
heating_heat_pump_part_load_ratio
heating_heat_pump_part_load_factor
heating_heat_pump_capacity_available_fraction
dhw_heat_pump_cop
dhw_heat_pump_source_temperature_C
dhw_heat_pump_sink_temperature_C
```

These columns make it possible to audit why COP changed in a given month or scenario. For example:

- A low `source_temperature_C` indicates cold outdoor operation for air-source systems.
- A high `sink_temperature_C` indicates high hydronic supply temperature from the weather curve.
- A `defrost_factor` below 1 indicates operation in the defrost temperature band.
- A `hybrid_dispatch_mode` of `parallel` indicates HP capacity was insufficient for the whole load.
- A `gas_only_cop_lockout` row indicates the hybrid controller rejected HP operation because current COP was below the threshold.

### 2.11 Recommended way to calculate monthly or annual COP from outputs

When reporting monthly or annual heat-pump performance, calculate an energy-weighted result from useful heat and electrical input.

For a pure heat-pump household:

```text
effective_COP_period =
    sum(Q_space_heating_useful_W)
    / sum(P_el_space_heating_technology_W)
```

For a hybrid heat pump, use only the heat assigned to the heat pump:

```text
hybrid_HP_COP_period =
    sum(hybrid_hp_useful_heat_W)
    / sum(P_el_space_heating_technology_W)
```

Do not average `heating_heat_pump_cop` arithmetically unless the purpose is only to summarize operating-point diagnostics. An arithmetic mean gives the same weight to a low-demand mild hour and a high-demand cold hour, which is not an SPF-like metric.

### 2.12 What is deliberately not represented

The current implementation does not include:

- manufacturer-specific compressor maps;
- refrigerant thermodynamic cycle simulation;
- explicit Carnot limit enforcement;
- borefield thermal depletion over multiple years;
- water-water/open-loop groundwater hydraulics;
- price-based hybrid dispatch;
- active cooling performance maps.

These omissions are deliberate. The implemented model is a compact, auditable, hourly empirical heat-pump representation suitable for thesis-scale scenario analysis. It captures the dominant drivers of heating COP without requiring detailed equipment data that the rest of the model does not use.

## 3. Practical interpretation checklist

When explaining a model result involving heat pumps, use this chain:

1. Identify which households were assigned heat-pump technologies.
2. Check whether the heat pump is `air_water`, `air_air`, `ground_source`, `hybrid_hp_gas`, or `hpwh`.
3. Check the emitter type and resulting sink temperature.
4. Check the climate window/pathway weather profile, especially outdoor temperature during heating hours.
5. Use the hourly diagnostics to explain COP changes.
6. For monthly or annual performance, compute a heat-weighted COP/SPF-style aggregate from useful heat and electrical input.
7. For hybrids, separate HP useful heat from gas useful heat before interpreting COP.

This gives a defensible thesis narrative: COP is not arbitrary and not fixed. It is assigned from calibrated technology curves, shifted by household-level sampling, and then recalculated hourly from physically meaningful operating conditions.
