# ED Thesis Model V3

Python implementation of the third thesis energy-demand model. The runtime covers deterministic annual household simulation, stochastic cohort simulation, climate sensitivity runs, and validation utilities for aggregate and high-frequency profile checks.

## Repository Contents

- `src/model_v3/`: model modules for data loading, forcing construction, physics, control, systems, stochastic sampling, cohort simulation, output persistence, and validation.
- `src/pipelines/`: executable pipeline entry points.
- `config/`: model and thesis-run configuration files.
- `inputs/`: local model inputs and raw input datasets required by the active configurations.
- `docs/` and `reports/`: methodology, architecture, runtime, and validation notes.
- `tests/`: smoke and regression-oriented tests.

Generated outputs are written under `outputs/` and are intentionally ignored by Git. Large raw load-profile CSVs under `inputs/load_profiles/fluvius/` and `inputs/load_profiles/kul/` are also ignored because they are too large for a normal GitHub repository.

## Setup

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
```

Use `PYTHONPATH=src` when running scripts from a checkout.

## Clone-Safe Demo Run

The default config is intentionally runnable from the public repository without the large local load-profile and weather CSVs:

```bash
PYTHONPATH=src python3 src/pipelines/run_model_v3.py --config config/model.yaml
```

## Canonical Thesis Run

```bash
PYTHONPATH=src python3 src/pipelines/run_model_v3_stochastic.py --config config/thesis.yaml
```

The canonical thesis configuration uses reference year `2023`, a 30-household stochastic cohort, full horizon, and climate mode disabled. For full validation and thesis-scale runs, restore the ignored local datasets under `inputs/load_profiles/`, `inputs/weather/`, and other documented raw-input locations before running.

## Useful Commands

Run the deterministic annual path:

```bash
PYTHONPATH=src python3 src/pipelines/run_model_v3_annual.py --config config/thesis.yaml
```

Run the smoke tests:

```bash
PYTHONPATH=src python3 -m unittest tests/test_model_v3_smoke.py
```

Run the focused pytest suite used during cleanup:

```bash
PYTHONPATH=src pytest tests/model_v3 tests/test_model_v3_smoke.py -k "not full_horizon"
```

## Belgian Technology Inputs

The canonical configs include `config/belgian_technology_inputs.yaml`.
That file holds the Belgian carrier and technology metadata from the DeepSearch
memo, including source URLs, reference years, low/base/high ranges, and
assumption flags. See `docs/model_v3_belgian_technology_integration.md` for the
runtime contract.

Carrier-aware annual outputs are added beside the existing calibrated
electricity output. `P_el_total_W` keeps its legacy meaning; technology-specific
electricity, gas, oil, biomass, district heat, PV generation, EV charging, and
grid import/export are exposed as separate columns and annual carrier summaries.

## Data Notes

The active runtime inputs are local files under `inputs/`; no sibling-repository symlinks are required for the core model. The ignored raw Fluvius, KU Leuven, LCL, and weather CSVs should be restored locally, or managed through Git LFS or external data storage, before running validation workflows that depend on them.

Keep repository-wide ignore rules in `.gitignore` so collaborators do not accidentally commit generated outputs or large raw data. Put private machine-specific patterns in `.git/info/exclude` or an ignored `.gitignore.local` file instead of committing them.
