# Model v3 age-split archetype report

Generated: 2026-05-28T18:36:08.103621+00:00

## Purpose

This report documents the split of the previous eight runtime archetypes into dwelling-type by construction-period as-is archetypes plus one explicit current-code renovated archetype per dwelling type.

## Source basis

- Local research note: `DeepSearch/BE archetype split and envelope U-values.md`
- Top-level dwelling-type shares: Statbel 2024 four-type R1-R4 dwelling shares from the research note.
- Age shares by type: Statbel 2024 type-specific construction-period mix from the research note.
- As-is envelope U-values: Belgian TABULA current-state construction-element packages.
- Renovated envelope U-values: current-code deep-renovation package, wall/roof/floor/window `0.24/0.24/0.24/1.50 W/m2K`.
- Renovation share: Belgian weighted EPC A/B high-performance proxy (16.0%) from `inputs/building/renovation_prevalence_epc_mapping.csv::belgium_weighted_epc_ab_proxy`.
- Renovation mapping rule: Weighted regional EPC high-performance proxy used as the default v3 renovated-stock share. The same share is applied across dwelling types because no robust Belgian dwelling_type x construction_period x renovation_state matrix is available.

## Generated rows

| archetype_id | stock_weight | period | package | UA_W_per_K |
|---|---:|---|---|---:|
| `BE_RES_DETACHED_PRE_1946_AS_IS_HP_V1` | 0.037816 | <1946 | `tabula_current_pre_1946` | 816.1 |
| `BE_RES_DETACHED_1946_1970_AS_IS_HP_V1` | 0.045601 | 1946-1970 | `tabula_current_1946_1970` | 747.0 |
| `BE_RES_DETACHED_1971_1991_AS_IS_HP_V1` | 0.070293 | 1971-1991 | `tabula_current_1971_1991` | 466.0 |
| `BE_RES_DETACHED_1992_2011_AS_IS_HP_V1` | 0.053165 | 1992-2011 | `tabula_current_1992_2011` | 353.7 |
| `BE_RES_DETACHED_2012_PLUS_AS_IS_HP_V1` | 0.015571 | 2012+ | `tabula_current_2012_plus` | 198.5 |
| `BE_RES_DETACHED_RENOVATED_HP_V1` | 0.042354 | all periods | `current_code_deep_renovation` | 134.3 |
| `BE_RES_SEMI_PRE_1946_AS_IS_HP_V1` | 0.057142 | <1946 | `tabula_current_pre_1946` | 578.0 |
| `BE_RES_SEMI_1946_1970_AS_IS_HP_V1` | 0.042030 | 1946-1970 | `tabula_current_1946_1970` | 536.5 |
| `BE_RES_SEMI_1971_1991_AS_IS_HP_V1` | 0.025974 | 1971-1991 | `tabula_current_1971_1991` | 336.1 |
| `BE_RES_SEMI_1992_2011_AS_IS_HP_V1` | 0.017788 | 1992-2011 | `tabula_current_1992_2011` | 258.9 |
| `BE_RES_SEMI_2012_PLUS_AS_IS_HP_V1` | 0.014325 | 2012+ | `tabula_current_2012_plus` | 144.3 |
| `BE_RES_SEMI_RENOVATED_HP_V1` | 0.029942 | all periods | `current_code_deep_renovation` | 98.3 |
| `BE_RES_TERRACED_PRE_1946_AS_IS_HP_V1` | 0.128354 | <1946 | `tabula_current_pre_1946` | 441.5 |
| `BE_RES_TERRACED_1946_1970_AS_IS_HP_V1` | 0.040877 | 1946-1970 | `tabula_current_1946_1970` | 422.9 |
| `BE_RES_TERRACED_1971_1991_AS_IS_HP_V1` | 0.018599 | 1971-1991 | `tabula_current_1971_1991` | 267.4 |
| `BE_RES_TERRACED_1992_2011_AS_IS_HP_V1` | 0.009402 | 1992-2011 | `tabula_current_1992_2011` | 212.5 |
| `BE_RES_TERRACED_2012_PLUS_AS_IS_HP_V1` | 0.007153 | 2012+ | `tabula_current_2012_plus` | 116.6 |
| `BE_RES_TERRACED_RENOVATED_HP_V1` | 0.038915 | all periods | `current_code_deep_renovation` | 80.6 |
| `BE_RES_APT_PRE_1946_AS_IS_HP_V1` | 0.072182 | <1946 | `tabula_current_pre_1946` | 204.1 |
| `BE_RES_APT_1946_1970_AS_IS_HP_V1` | 0.062967 | 1946-1970 | `tabula_current_1946_1970` | 199.2 |
| `BE_RES_APT_1971_1991_AS_IS_HP_V1` | 0.039674 | 1971-1991 | `tabula_current_1971_1991` | 129.2 |
| `BE_RES_APT_1992_2011_AS_IS_HP_V1` | 0.048633 | 1992-2011 | `tabula_current_1992_2011` | 109.9 |
| `BE_RES_APT_2012_PLUS_AS_IS_HP_V1` | 0.032507 | 2012+ | `tabula_current_2012_plus` | 60.0 |
| `BE_RES_APT_RENOVATED_HP_V1` | 0.048736 | all periods | `current_code_deep_renovation` | 42.6 |

## Caveats

- Apartment construction-period shares use building-count age mix as a proxy for dwelling-count age mix.
- Renovation prevalence is source-backed but still a proxy because Belgian public evidence does not provide the full type-by-age renovation matrix needed by this archetype set.
- TABULA U-values are archetype package values, not measured field observations.
- Thermal bridges are represented by simple adders because the TABULA sub-typology did not include them.
- The current-code renovated state is a scenario/technical package; it should not be described as the measured average renovated Belgian dwelling.
