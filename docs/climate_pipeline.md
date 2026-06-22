# CORDEX Climate Ingestion Pipeline

This pipeline turns CDS CORDEX ZIP/NetCDF downloads into deterministic forcing CSV
files for `model_v3`.

The configured dataset is `projections-cordex-domains-single-levels` for Europe at
`0_11_degree_x_0_11_degree`, using daily means for:

- `2m_air_temperature`
- `surface_solar_radiation_downwards`

The initial model chain is:

- GCM: `cnrm_cerfacs_cm5`
- RCM: `cnrm_aladin63`
- Ensemble member: `r1i1p1`

The request fields follow the Copernicus CDS CORDEX catalogue and API examples:

- [CORDEX regional climate model data on single levels](https://cds.climate.copernicus.eu/datasets/projections-cordex-domains-single-levels)
- [C3S CORDEX API training example](https://ecmwf-projects.github.io/copernicus-training-c3s/projections-cordex.html)

## Configure CDS API Access

Install the dependencies:

```bash
pip install -r requirements.txt
```

Create `~/.cdsapirc` from the API credentials shown in your CDS profile. Do not
put this file in the repository.

Typical structure:

```yaml
url: https://cds.climate.copernicus.eu/api
key: <your-cds-api-key>
```

Also accept the CORDEX dataset licence in the CDS web interface before the first
download.

## Dry-Run Downloads

Dry-run mode prints every generated CDS request and target path without
downloading data:

```bash
python3 -m src.climate.download_cordex --dry-run
```

Limit to one window/scenario:

```bash
python3 -m src.climate.download_cordex \
  --window baseline \
  --scenario historical \
  --dry-run
```

## Actual Downloads

Download all configured chunks:

```bash
python3 -m src.climate.download_cordex
```

Download one scenario/window:

```bash
python3 -m src.climate.download_cordex \
  --window near_future \
  --scenario rcp_4_5
```

Files are written to:

```text
inputs/climate/raw/{window}/{scenario}/
```

with deterministic names:

```text
cordex_{window}_{scenario}_{gcm}_{rcm}_{ensemble}_{start}_{end}.zip
```

Existing files are skipped unless `--overwrite` is passed.

## Preprocess

After raw ZIP files are present, build forcing CSVs:

```bash
python3 -m src.climate.preprocess_cordex
```

Limit to one window/scenario:

```bash
python3 -m src.climate.preprocess_cordex \
  --window baseline \
  --scenario historical
```

The default spatial extraction method is the nearest CORDEX grid point to
Uccle/Brussels:

```yaml
target_lat: 50.85
target_lon: 4.35
```

Belgium-box averaging is also supported:

```bash
python3 -m src.climate.preprocess_cordex --spatial-method belgium_box_mean
```

using the box configured in `src/climate/climate_config.yaml`:

```yaml
lat_min: 49.5
lat_max: 51.5
lon_min: 2.5
lon_max: 6.5
```

Processed files are written to:

```text
inputs/climate/processed/{window}/
```

with deterministic names:

```text
weather_{window}_{scenario}_{gcm}_{rcm}_{ensemble}.csv
```

A metadata file is written next to each CSV. It records selected source files,
variable names, spatial extraction details, and the solar radiation unit
conversion decision.

## Radiation Units

The preprocessing script checks the units attribute of the solar radiation
variable:

- `W m-2` or equivalent: values are kept as W/m2.
- `J m-2` or equivalent: values are treated as daily accumulated energy and
  divided by `86400` to produce daily mean W/m2.

Unsupported units fail preprocessing for that file instead of silently producing
ambiguous forcing data.

## Validate

Run validation after preprocessing:

```bash
python3 -m src.climate.validate_climate_inputs
```

The report is written to:

```text
reports/climate_input_validation.md
```

Validation checks:

- required columns exist and contain no NaNs
- no duplicated timestamps
- no missing expected daily timestamps
- configured year coverage is present
- Belgium-plausible temperature range, `-35` to `50` degC
- non-negative solar radiation

## Final CSV Schema

Each processed CSV contains:

| Column | Meaning |
| --- | --- |
| `timestamp` | Daily timestamp from the CORDEX time coordinate. |
| `T_out_C` | Outdoor 2 m air temperature converted to degrees Celsius. |
| `I_solar_W_m2` | Downward surface solar radiation as daily mean W/m2. |
| `scenario` | CDS experiment/scenario, e.g. `historical` or `rcp_4_5`. |
| `window` | Climate window, e.g. `baseline` or `mid_century`. |
| `gcm_model` | Driving global climate model. |
| `rcm_model` | Regional climate model. |
| `ensemble_member` | Ensemble member identifier. |
| `source_files` | Semicolon-separated raw ZIP or NetCDF sources used for the CSV. |
