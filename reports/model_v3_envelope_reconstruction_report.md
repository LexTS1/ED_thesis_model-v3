# Model v3 envelope reconstruction report

Generated: 2026-05-12T04:50:52.947359+00:00
Git commit: unknown

## Purpose

This report documents the reconstruction of aggregate envelope conductance `UA_W_per_K` and `H_W_per_K` for the runtime building archetype table. The previous table used aggregate values inherited from the stock archetype file. The new values are derived from an explicit intermediate envelope table so that geometry and U-value assumptions can be inspected.

## Files

- Runtime archetype table: `inputs/model_v3/building/archetype_parameters_merged_v2.csv`
- Envelope reconstruction table: `inputs/model_v3/building/envelope_archetypes_v1.csv`
- Local evidence note: `DeepSearch/Empirical grounding for Belgian residential archetypes.md`

## Reconstruction assumptions

- Footprint is `floor_area_m2 / storeys_assumed`.
- A square footprint is assumed to estimate perimeter.
- Gross wall area is `perimeter * storeys_assumed * ceiling_height_m`.
- Exposed-wall fractions are detached `1.00`, semi-detached `0.75`, terraced `0.50`, apartment `0.35`.
- Apartment roof and floor exposure fractions are `0.25`; house roof and floor exposure fractions are `1.00`.
- Window area is `floor_area_m2 * glazing_ratio`, capped at 90% of exposed wall area if needed.
- As-is U-values use wall `1.65`, roof `1.94`, floor `1.04`, window `3.91 W/m2K`.
- Renovated U-values use wall `0.40`, roof `0.30`, floor `0.40`, window `2.00 W/m2K`.
- Thermal-bridge adders are 10% for as-is archetypes and 5% for renovated archetypes.

## UA comparison

| archetype_id | old_UA_W_per_K | reconstructed_UA_W_per_K | delta_pct |
|---|---:|---:|---:|
| `BE_RES_DETACHED_AS_IS_HP_V1` | 961.0 | 725.9 | -24.5% |
| `BE_RES_DETACHED_RENOVATED_HP_V1` | 620.0 | 198.5 | -68.0% |
| `BE_RES_SEMI_AS_IS_HP_V1` | 830.0 | 521.5 | -37.2% |
| `BE_RES_SEMI_RENOVATED_HP_V1` | 540.0 | 144.3 | -73.3% |
| `BE_RES_TERRACED_AS_IS_HP_V1` | 700.0 | 411.1 | -41.3% |
| `BE_RES_TERRACED_RENOVATED_HP_V1` | 460.0 | 116.6 | -74.7% |
| `BE_RES_APT_AS_IS_HP_V1` | 420.0 | 185.3 | -55.9% |
| `BE_RES_APT_RENOVATED_HP_V1` | 280.0 | 60.0 | -78.6% |

## Caveats

- This is still an archetype-level reconstruction, not a measured envelope survey.
- The table does not yet distinguish construction period within the as-is state.
- Exact external wall, party-wall, roof, floor, and glazing areas are not known.
- Apartment exposure is represented by a simple average exposure factor.
- The reconstruction strengthens traceability, but the resulting UA values should still be sensitivity-tested.
