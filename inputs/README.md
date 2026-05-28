# model_v3 Inputs

This folder is the active `model_v3` input namespace.

`model_v3` is packaged with local copies of the active runtime inputs. The core model does not require sibling-repository symlinks for weather, load profiles, solar raw inputs, occupancy, end-use calibration, or building reference data.

## Local Runtime Dependencies

These paths must resolve for the default `model_v3` configuration to run:

- `weather/aws_1hour_Uccle.csv`
  Active runtime weather source for the core loader.
- `load_profiles/LCL_2013.csv`
  Active runtime load-profile source.
- `solar/raw_inputs/`
  Active runtime PVGIS facade irradiance source for the core solar loader.
- `occupancy/occupancy_model_spec_v1.yaml`
  Active occupancy-state and schedule specification.
- `end_use/EU27_BE_household_enduse_2019.csv`
  Active end-use share calibration used to split aggregate electricity.
- `building/archetype_parameters_v1.csv`
  Supporting reference only; the runtime archetype source is the local merged table.

These files are stored as real files/directories inside `inputs/`, not symlinks.

## Active Runtime Sources

- `building/archetype_parameters_merged_v2.csv`
  Previous eight-row archetype table retained for provenance and as the source table for the age-split generator.
- `building/archetype_parameters_merged_v3.csv`
  Active age-split runtime archetype table for stock identity, construction period, geometry, heat loss, thermal mass, comfort defaults, solar defaults, and airflow defaults.
- `building/renovation_prevalence_epc_mapping.csv`
  Source-backed EPC A/B high-performance proxy used to assign the prevalence of the single v3 renovated archetype state.
- `building/envelope_archetypes_v1.csv`
  Reproducible envelope-area reconstruction behind the previous v2 `UA_W_per_K` and `H_W_per_K` values.
- `building/envelope_archetypes_v2.csv`
  Reproducible construction-period envelope reconstruction behind the active v3 `UA_W_per_K` and `H_W_per_K` values.
- `weather/aws_1hour_Uccle.csv`
  Core-loader outdoor-temperature source.
- `load_profiles/LCL_2013.csv`
  Core-loader representative electrical load profile. Raw values are interval energy and are converted to average watts over the interval.
- `end_use/EU27_BE_household_enduse_2019.csv`
  Core-loader end-use split for appliances, lighting, cooking, and DHW.
- `occupancy/occupancy_model_spec_v1.yaml`
  Occupancy-state probabilities, expected occupants, and schedule state.
- `solar/raw_inputs/`
  Core-loader orientation-resolved PVGIS facade irradiance.

## Climate and Validation Inputs

- `weather/Timeseries_pvgisWEATHER_50.830_4.350_SA3_0deg_0deg_2005_2023.csv`
  Local PVGIS weather input used by the stochastic climate-ensemble pipeline.
- `solar/Timeseries{SOUTH,EAST,WEST,NORTH}_50.830_4.350_SA3_90deg_*_2005_2023.csv`
  Local orientation-resolved PVGIS solar inputs used by the stochastic climate-ensemble pipeline.
- `load_profiles/fluvius/`
  Thesis-facing external aggregate validation profiles.
- `load_profiles/kul/`
  Thesis-facing high-frequency KU Leuven validation profiles.

## Supporting Reference Tables

- `building/internal_gains_archetypes_v2.csv`
  PDF-derived internal-gain defaults by archetype.
- `building/airflow_archetypes_v2.csv`
  PDF-derived infiltration and ventilation defaults by archetype.
- `building/envelope_archetypes_v1.csv`
  Generated table with reconstructed wall, window, roof, and floor areas, U-values, component UA terms, and total UA.
- `building/archetype_parameters_merged_v3.md`
  Derivation note for the active age-split archetype table.
- `building/archetype_parameters_merged_v2.md`
  Derivation note for the merged archetype table.
- `building/archetype_parameters_v1.csv`
  Source/reference table retained for provenance.

## Year Coverage

- AWS weather file: covers 2003-2023, but not every year is complete. Complete years observed include 2005, 2019, 2020, 2021, 2022, and 2023. Year 2013 is incomplete and should not be used as the default weather year.
- Local PVGIS weather: complete hourly years 2005-2023.
- LCL load profile file: half-hourly 2013 profile from `2013-01-01 00:30` through `2014-01-01 00:00` with 17,520 rows. It is used as a representative input load-shape source, not as thesis-facing validation evidence.
- Core solar `raw_inputs`: four PVGIS facade files for complete year 2023.
- Local climate solar files: complete hourly facade files for 2005-2023.

## Runtime Notes

- The v3 merged archetype table is the active source of truth for `UA`, `C`, volume, comfort defaults, solar parameters, and airflow defaults.
- Runtime v3 `UA`/`H` values are generated from `building/envelope_archetypes_v2.csv` using `python3 -m model_v3.building.build_age_split_archetypes --repo-root . --print-summary`.
- Occupancy-driven internal gains and occupancy-driven setpoint scheduling are active.
- Explicit infiltration and ventilation airflow are active physical terms in the `model_v3` timestep update.
- The default `simulation.reference_year` is 2023 to avoid incomplete 2013 weather while staying aligned with complete AWS weather and core solar coverage.
- Annual weather runs now fail if the selected reference-year weather rows are not close to a full hourly year: 8760 rows for a non-leap year or 8784 rows for a leap year.

## What This Folder Does Not Contain

This folder contains local copies of the active raw datasets required by the default and thesis configurations. The separate Belgian smart-meter validation path has been removed because no reliable independent Belgian smart-meter dataset is expected for this thesis model.

For final thesis validation, avoid using `load_profiles/LCL_2013.csv` as the validation target because it is already the configured representative input load-shape source. Use Fluvius for independent aggregate profile validation and KU Leuven for independent high-frequency event/ramp validation.

## Persisted Outputs

Top-level `model_v3` runs persist runtime artifacts under `outputs/`.
