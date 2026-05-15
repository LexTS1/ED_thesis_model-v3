# Model v3 age-split archetype report

Generated: 2026-05-13T07:08:16.372163+00:00

## Purpose

This report documents the split of the previous eight runtime archetypes into dwelling-type by construction-period as-is archetypes plus one explicit current-code renovated archetype per dwelling type.

## Source basis

- Local research note: `DeepSearch/BE archetype split and envelope U-values.md`
- Top-level dwelling-type shares: Statbel 2024 four-type R1-R4 dwelling shares from the research note.
- Age shares by type: Statbel 2024 type-specific construction-period mix from the research note.
- As-is envelope U-values: Belgian TABULA current-state construction-element packages.
- Renovated envelope U-values: current-code deep-renovation package, wall/roof/floor/window `0.24/0.24/0.24/1.50 W/m2K`.
- Renovation shares: previous v2 within-type as-is/renovated split preserved because no empirical renovation-share matrix was provided.

## Generated rows

| archetype_id | stock_weight | period | package | UA_W_per_K |
|---|---:|---|---|---:|
| `BE_RES_DETACHED_PRE_1946_AS_IS_HP_V1` | 0.032412 | <1946 | `tabula_current_pre_1946` | 816.1 |
| `BE_RES_DETACHED_1946_1970_AS_IS_HP_V1` | 0.039084 | 1946-1970 | `tabula_current_1946_1970` | 747.0 |
| `BE_RES_DETACHED_1971_1991_AS_IS_HP_V1` | 0.060247 | 1971-1991 | `tabula_current_1971_1991` | 466.0 |
| `BE_RES_DETACHED_1992_2011_AS_IS_HP_V1` | 0.045567 | 1992-2011 | `tabula_current_1992_2011` | 353.7 |
| `BE_RES_DETACHED_2012_PLUS_AS_IS_HP_V1` | 0.013346 | 2012+ | `tabula_current_2012_plus` | 198.5 |
| `BE_RES_DETACHED_RENOVATED_HP_V1` | 0.074144 | all periods | `current_code_deep_renovation` | 134.3 |
| `BE_RES_SEMI_PRE_1946_AS_IS_HP_V1` | 0.043885 | <1946 | `tabula_current_pre_1946` | 578.0 |
| `BE_RES_SEMI_1946_1970_AS_IS_HP_V1` | 0.032279 | 1946-1970 | `tabula_current_1946_1970` | 536.5 |
| `BE_RES_SEMI_1971_1991_AS_IS_HP_V1` | 0.019948 | 1971-1991 | `tabula_current_1971_1991` | 336.1 |
| `BE_RES_SEMI_1992_2011_AS_IS_HP_V1` | 0.013661 | 1992-2011 | `tabula_current_1992_2011` | 258.9 |
| `BE_RES_SEMI_2012_PLUS_AS_IS_HP_V1` | 0.011001 | 2012+ | `tabula_current_2012_plus` | 144.3 |
| `BE_RES_SEMI_RENOVATED_HP_V1` | 0.066426 | all periods | `current_code_deep_renovation` | 98.3 |
| `BE_RES_TERRACED_PRE_1946_AS_IS_HP_V1` | 0.089568 | <1946 | `tabula_current_pre_1946` | 441.5 |
| `BE_RES_TERRACED_1946_1970_AS_IS_HP_V1` | 0.028525 | 1946-1970 | `tabula_current_1946_1970` | 422.9 |
| `BE_RES_TERRACED_1971_1991_AS_IS_HP_V1` | 0.012979 | 1971-1991 | `tabula_current_1971_1991` | 267.4 |
| `BE_RES_TERRACED_1992_2011_AS_IS_HP_V1` | 0.006561 | 1992-2011 | `tabula_current_1992_2011` | 212.5 |
| `BE_RES_TERRACED_2012_PLUS_AS_IS_HP_V1` | 0.004992 | 2012+ | `tabula_current_2012_plus` | 116.6 |
| `BE_RES_TERRACED_RENOVATED_HP_V1` | 0.100676 | all periods | `current_code_deep_renovation` | 80.6 |
| `BE_RES_APT_PRE_1946_AS_IS_HP_V1` | 0.051555 | <1946 | `tabula_current_pre_1946` | 204.1 |
| `BE_RES_APT_1946_1970_AS_IS_HP_V1` | 0.044974 | 1946-1970 | `tabula_current_1946_1970` | 199.2 |
| `BE_RES_APT_1971_1991_AS_IS_HP_V1` | 0.028337 | 1971-1991 | `tabula_current_1971_1991` | 129.2 |
| `BE_RES_APT_1992_2011_AS_IS_HP_V1` | 0.034736 | 1992-2011 | `tabula_current_1992_2011` | 109.9 |
| `BE_RES_APT_2012_PLUS_AS_IS_HP_V1` | 0.023218 | 2012+ | `tabula_current_2012_plus` | 60.0 |
| `BE_RES_APT_RENOVATED_HP_V1` | 0.121880 | all periods | `current_code_deep_renovation` | 42.6 |

## Caveats

- Apartment construction-period shares use building-count age mix as a proxy for dwelling-count age mix.
- Renovation shares are preserved from the earlier runtime table, not newly estimated from Belgian renovation data.
- TABULA U-values are archetype package values, not measured field observations.
- Thermal bridges are represented by simple adders because the TABULA sub-typology did not include them.
- The current-code renovated state is a scenario/technical package; it should not be described as the measured average renovated Belgian dwelling.
