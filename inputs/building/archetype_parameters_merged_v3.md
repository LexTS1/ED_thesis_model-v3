# Archetype Parameters Merged v3

This table is the active runtime archetype table for `model_v3`.

It expands the previous eight-row `archetype_parameters_merged_v2.csv` table into
24 rows:

- four dwelling types;
- five construction-period as-is bands per dwelling type;
- one explicit current-code renovated archetype per dwelling type.

The table is generated reproducibly with:

```bash
python3 -m model_v3.building.build_age_split_archetypes \
  --repo-root . \
  --print-summary
```

The generated envelope table is:

```text
inputs/building/envelope_archetypes_v2.csv
```

## Empirical basis

The local source note is:

```text
DeepSearch/BE archetype split and envelope U-values.md
```

The implementation uses:

- Statbel 2024 four-type R1-R4 dwelling shares for top-level dwelling-type
  weights;
- Statbel 2024 type-specific construction-period shares from the research note
  for as-is age splitting;
- Belgian TABULA current-state construction-element U-values for as-is
  construction-period packages;
- current Flemish/Walloon EPB envelope levels for the single renovated package:
  wall `0.24`, roof `0.24`, floor `0.24`, window `1.50 W/m2K`.

## Important assumption

The research note does not provide an empirical Belgian matrix for:

```text
dwelling_type x construction_period x renovation_state
```

Therefore, v3 preserves the previous v2 within-type as-is/renovated split and
only uses the new evidence to improve:

- dwelling-type shares;
- construction-period shares within as-is stock;
- age-specific as-is U-values;
- the technical meaning of the renovated state.

This should be described as `implemented_unverified` for renovation prevalence:
the current-code renovated package is a scenario/technical state, not evidence
that all renovated Belgian dwellings meet current-code envelope levels.

## Runtime use

The default configs now point to:

```text
inputs/building/archetype_parameters_merged_v3.csv
```

For cohort runs, `uncertainty.physical.sample_building_archetype_by_stock_weight`
enables stock-weighted sampling of the 24 archetypes. Deterministic single-run
config loading still selects one archetype row according to the configured
selection mode.

## Main caveats

- Apartment age shares use a building-count age mix as a proxy for dwelling age
  mix.
- Renovation shares are inherited from v2 and should be replaced if a stronger
  Belgian renovation-share source becomes available.
- TABULA U-values are archetype package values, not measured field observations.
- Thermal bridges are represented by simple adders.
- ACH50, ventilation, thermal mass, setpoints, solar factors, and internal gains
  remain assumptions or broad archetype defaults.
