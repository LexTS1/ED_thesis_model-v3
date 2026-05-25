# Cooling Exposure And Overheating Risk

Active cooling is not included as final energy demand in the Phase 1 model outputs.
Belgian residential cooling adoption, cooling setpoints, system efficiencies, and
user behaviour introduce additional uncertainty outside the validated scope of the
current model. Instead, model_v3 quantifies cooling pressure through climate and
comfort indicators. This avoids interpreting cooling exposure as actual electricity
consumption, while still allowing the thesis to identify increasing summer stress
under future climate scenarios.

The dedicated output is:

`experiments/scenario_tree/summaries/comparison_level/cooling_exposure_overheating_risk_comparison.csv`

It reports:

- `CDD_22`: annual cooling degree days using a 22 C outdoor-temperature base.
- `overheating_hours`: hours where simulated indoor temperature exceeds the comfort upper bound.
- `excess_heat_kWh`: thermal excess above the comfort upper bound, expressed as equivalent heat.
- `indoor_temperature_exceedance_degree_hours`: accumulated indoor temperature exceedance above the comfort upper bound.
- `active_cooling_final_energy_kWh_included`: always `False` for this model scope.

Reversible heat pumps can therefore be discussed as an adaptation option whose
cooling value is only partially captured: the current model identifies increasing
cooling pressure, but does not convert that pressure into cooling electricity.

Future-work active cooling module:

For each timestep, active cooling demand could be introduced as:

```text
Q_cool,req = max(0, (T_indoor_free_float - T_cool,set) * C / dt)
Q_cool,delivered = min(Q_cool,req, Q_cool,max)
P_el,cool = Q_cool,delivered / SEER_or_COP_cooling
T_indoor,next = thermal_state_after_heating_and_cooling
```

where `T_cool,set`, adoption probability, installed cooling capacity, control
schedule, and cooling efficiency would be scenario assumptions requiring separate
validation or sensitivity analysis.
