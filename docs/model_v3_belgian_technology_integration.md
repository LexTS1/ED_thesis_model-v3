# Belgian Residential Technology Integration

`model_v3` now loads Belgian residential technology inputs from
`config/belgian_technology_inputs.yaml` through the root config key
`technology_inputs_path`.

## Scope

The integration keeps the existing calibrated electricity contract intact:
`P_el_total_W` remains the legacy gross household electricity series aligned to
the configured annual end-use baseline. New technology-resolved outputs are
added beside that contract:

- `P_el_space_heating_technology_W` and `P_el_dhw_technology_W`
- `P_gas_*`, `P_oil_*`, `P_biomass_*`, `P_propane_*`, `P_coal_*`, and
  `P_district_heat_*`
- `P_pv_generation_W`
- `P_el_ev_charging_W`
- `P_el_gross_actual_W`, `P_el_net_grid_W`, `P_el_grid_import_W`, and
  `P_el_grid_export_W`

Annual runs also return `annual_energy_by_carrier_kWh`, plus convenience keys
for PV generation, EV charging, grid import, and grid export.

## Heating

The Belgian stock baseline is carrier-first because that is the strongest
public evidence in the DeepSearch memo. Cohort sampling can map the BE-SILC
carrier shares into active technologies when
`uncertainty.technology.use_belgian_stock_baseline: true`.

The mapping is intentionally explicit in YAML:

- gas to `gas_boiler` and a small assumption-driven `hybrid_hp_gas` share
- heating oil to `oil_boiler`
- direct electricity excluding heat pumps to `resistive_direct` and
  `storage_heater`
- wood/pellets to `biomass_stove` and `biomass_boiler`
- heat pumps to `air_water`, `air_air`, and `ground_source`

Each performance value keeps `low`, `base`, and `high` ranges where the source
or modelling assumption supports one. Rows marked `assumption_flag: true` are
not observed Belgian stock distributions.

## PV And EV

Rooftop PV is disabled by default for deterministic runs. Enable it with:

```yaml
der:
  pv:
    enabled: true
```

The model uses orientation-resolved PVGIS irradiance when available and falls
back to the configured annual specific-yield range only when no irradiance
columns are present.

EV charging is also disabled by default for deterministic runs. Enable it with:

```yaml
mobility:
  ev:
    enabled: true
```

The default charging shape is an explicit scenario assumption: uncontrolled
home charging in the configured evening window, capped by charger power and
annualized from the Belgian km/year and kWh/100 km assumptions.

## Source Discipline

The technology YAML has a `technology_sources` registry with source URLs,
reference years, geography, and suitability notes. The model uses those fields
as run metadata; they are intended to make thesis uncertainty and provenance
auditable rather than to imply that weak assumptions are measured facts.
