# model_v3 Runtime Contract

## Canonical Thesis Runtime

The canonical thesis run is the 30-household stochastic cohort run with climate disabled:

```bash
PYTHONPATH=src python3 src/pipelines/run_model_v3_stochastic.py --config config/thesis.yaml
```

Validation runners also accept `--config`, so thesis-facing reruns should pass `config/thesis.yaml` explicitly rather than relying on defaults.

The thesis config is `config/thesis.yaml`. It keeps the current calibrated baseline and cohort settings, sets `simulation.reference_year: 2023`, starts at `2023-01-01T00:00:00+01:00`, keeps `simulation.max_steps: null`, sets `climate.enabled: false`, and applies `building.ua_multiplier: 0.80`.

The `building.ua_multiplier` is an explicit envelope/UA calibration factor. It scales the selected archetype heat-loss coefficients so the full-horizon annual space-heating thermal demand sits inside the configured Belgian literature range. This should be cited as thermal baseline calibration, not as an independently validated envelope parameter.

## Meaningful Runtime Paths

- `src/pipelines/run_model_v3.py`: one-step deterministic smoke/runtime-contract check. This is useful for layer integration and timing, but it is not the scientific annual or cohort result.
- `src/pipelines/run_model_v3_annual.py`: sequential annual deterministic household run. This is the annual physics/control/system core and carries indoor temperature and thermostat state through the selected weather-year timeline.
- `src/pipelines/run_model_v3_stochastic.py` with `climate.enabled: false`: stochastic household cohort run. This is the canonical thesis household/cohort path; it samples household parameters, runs each household through the annual runner, and aggregates the cohort.
- `src/pipelines/run_model_v3_stochastic.py` with `climate.enabled: true`: climate ensemble run. This is a separate climate sensitivity workflow using PVGIS weather and solar years; it does not run the household cohort branch.
- `src/model_v3/validation/runners/`: validation workflows for baseline annual checks, Fluvius aggregate comparison, KU Leuven high-frequency checks, and synthetic checks.

## Reference Year

The canonical reference year is 2023.

This is based on the actual configured weather input coverage in `inputs/weather/aws_1hour_Uccle.csv`: 2023 has a complete 8760-hour weather year, while 2013 has only 105 hourly weather rows. Therefore 2013 is not defensible for the weather-driven annual/cohort thesis run.

The annual runner now refuses to simulate a configured weather reference year unless the selected weather rows are close to the expected full-year hourly count: about 8760 rows for a non-leap year or 8784 rows for a leap year. This guard runs before `simulation.max_steps` is applied, so quick/debug runs still work only when the underlying selected weather year is complete.

The configured LCL load file, `inputs/load_profiles/LCL_2013.csv`, remains a representative 2013 load-shape/calibration source. The annual and cohort runners use the selected weather timeline as the simulated year; load profile values are resolved as representative shapes and scaled to the configured annual Belgian household baseline.

LCL is not used as thesis-facing validation evidence in the canonical config. This avoids validating against the same dataset family that supplies the representative input load shape. Thesis-facing validation uses Fluvius for aggregate profile realism and KU Leuven for high-frequency event/ramp realism. The separate Belgian smart-meter validation path has been removed because no reliable independent Belgian smart-meter dataset is expected for this thesis model.

The PVGIS climate weather and solar files cover complete years from 2005 through 2023, including 2013, but those files belong to the separate climate ensemble path.

The active runtime input files are stored locally under `inputs/`. The core model is archival and independently runnable without sibling-repository symlinks or the removed duplicate `inputs/model_v3/` namespace.

## Climate Mode

Climate mode is disabled for the canonical thesis household/cohort run.

This is intentional because `run_model_v3_stochastic.py` branches on `climate.enabled`: when it is `true`, the runner executes `run_climate_ensemble()`; when it is `false`, it executes `run_cohort_simulation()`. Climate uncertainty remains scientifically meaningful as a separate sensitivity workflow, but it is not part of the canonical household/cohort thesis run.

## Expected Outputs

Canonical thesis-facing scenario runs now write under `experiments/`, not the removed legacy `outputs/model_v3/` namespace. Per-leaf run folders include the generated `run_config.yaml`, input manifest, outputs, logs, and run-registry provenance. The registry records run mode, timestamp, config/input hashes, scenario identifiers, cohort metadata where applicable, and the git commit hash when the workspace exposes one.

The final output structure is:

- `experiments/scenario_tree/`: selected climate-only / stock-weighted scenario-tree runs, summaries, registries, and validation reports.
- `experiments/scenario_tree_output34/`: selected hourly cohort runs for peak/grid stress, distribution/diversity, bills/emissions, and technology-investment/adaptation outputs.
- `outputs/validation/`: current local validation artefacts.
- `reports/model_v3/validation/`: thesis-facing validation reports and technology-validation tables.

Older folders such as `outputs/model_v3/`, `outputs/final/`, `outputs/annual/`, `outputs/stochastic/`, and `outputs/climate_uncertainty/` were removed from the working tree because they were cached scaffold-era artefacts from mixed configurations. They must not be treated as canonical thesis outputs.

For the stochastic cohort path, `cohort_summary.json` is intended as the readable thesis-facing summary. It records run metadata, sampled technology and household-class counts, calibrated annual energy summaries, raw/pre-calibration calibration diagnostics summaries, peak distributions, and timing metadata without embedding full household time-series arrays.

Detailed stochastic cohort artifacts are split out:

- `aggregate_profile.csv`: aggregate and per-household cohort profile time series with P10/P50/P90 bands.
- `household_annual_energy.csv`: one row per sampled household, including calibrated annual electricity, raw/pre-calibration annual electricity when available, thermal annual totals, class, technology, peaks, and event counts.
- `household_calibration_diagnostics.json`: per-household annual runner calibration diagnostics by end use, including target, raw, calibrated, scale-factor, and fallback fields.

Calibrated per-household annual electricity is intentionally baseline-aligned by the annual runner. The configured `building.ua_multiplier` also baseline-calibrates envelope heat loss before the annual thermal calculation. Raw/pre-calibration diagnostics are the correct artifact for inspecting stochastic annual spread before calibration.

The sampled technology labels are carrier-stock-mapped simulation semantics, not appliance-census claims. Belgian main-heating carrier shares are loaded from `belgian_technology_inputs.yaml`; the mapping from carriers to gas boilers, oil boilers, heat pumps, resistive systems, biomass systems, and hybrids is explicit and assumption-flagged in that YAML.
