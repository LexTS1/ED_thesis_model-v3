"""Build the age-split Belgian residential archetype table for model_v3."""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from model_v3.building.rebuild_archetype_envelope import (
    ENVELOPE_FIELDS,
    build_envelope_rows,
    update_archetype_rows,
    write_csv,
)


DEFAULT_BASE_TABLE = Path("inputs/building/archetype_parameters_merged_v2.csv")
DEFAULT_OUTPUT_TABLE = Path("inputs/building/archetype_parameters_merged_v3.csv")
DEFAULT_ENVELOPE_TABLE = Path("inputs/building/envelope_archetypes_v2.csv")
DEFAULT_REPORT = Path("reports/model_v3_age_split_archetype_report.md")
DEFAULT_RENOVATION_PREVALENCE_TABLE = Path("inputs/building/renovation_prevalence_epc_mapping.csv")

VALUE_SOURCE = "statbel_2024_tabula_age_split_epc_ab_renovation_proxy_v2"
EVIDENCE_NOTE = "DeepSearch/BE archetype split and envelope U-values.md"

TYPE_STOCK_SHARES = {
    "detached": 0.2648,
    "semi_detached": 0.1872,
    "terraced": 0.2433,
    "apartment": 0.3047,
}

TYPE_LABELS = {
    "detached": "Detached",
    "semi_detached": "Semi-detached",
    "terraced": "Terraced",
    "apartment": "Apartment",
}

TYPE_ID_TOKENS = {
    "detached": "DETACHED",
    "semi_detached": "SEMI",
    "terraced": "TERRACED",
    "apartment": "APT",
}


@dataclass(frozen=True)
class AgeBand:
    period_id: str
    label: str
    id_token: str
    u_value_package_id: str
    shares_by_type: dict[str, float]


AGE_BANDS = [
    AgeBand(
        period_id="pre_1946",
        label="<1946",
        id_token="PRE_1946",
        u_value_package_id="tabula_current_pre_1946",
        shares_by_type={"detached": 0.170, "semi_detached": 0.363, "terraced": 0.628, "apartment": 0.282},
    ),
    AgeBand(
        period_id="1946_1970",
        label="1946-1970",
        id_token="1946_1970",
        u_value_package_id="tabula_current_1946_1970",
        shares_by_type={"detached": 0.205, "semi_detached": 0.267, "terraced": 0.200, "apartment": 0.246},
    ),
    AgeBand(
        period_id="1971_1991",
        label="1971-1991",
        id_token="1971_1991",
        u_value_package_id="tabula_current_1971_1991",
        shares_by_type={"detached": 0.316, "semi_detached": 0.165, "terraced": 0.091, "apartment": 0.155},
    ),
    AgeBand(
        period_id="1992_2011",
        label="1992-2011",
        id_token="1992_2011",
        u_value_package_id="tabula_current_1992_2011",
        shares_by_type={"detached": 0.239, "semi_detached": 0.113, "terraced": 0.046, "apartment": 0.190},
    ),
    AgeBand(
        period_id="2012_plus",
        label="2012+",
        id_token="2012_PLUS",
        u_value_package_id="tabula_current_2012_plus",
        shares_by_type={"detached": 0.070, "semi_detached": 0.091, "terraced": 0.035, "apartment": 0.127},
    ),
]

EXTRA_FIELDS = [
    "construction_period_id",
    "construction_period",
    "u_value_package_id",
    "u_value_package_source",
    "stock_weight_source",
    "renovation_prevalence_source",
    "renovation_prevalence_status",
    "renovation_prevalence_proxy",
    "renovation_mapping_rule",
]


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or []), [dict(row) for row in reader]


@dataclass(frozen=True)
class RenovationPrevalence:
    renovated_share: float
    source: str
    status: str
    mapping_rule: str


def load_renovation_prevalence(path: Path, source_label: str | None = None) -> RenovationPrevalence:
    """Load the active renovation-prevalence proxy from a versioned CSV input."""

    _, rows = read_csv(path)
    active = [row for row in rows if row.get("active_default", "").strip().lower() == "true"]
    if len(active) != 1:
        raise ValueError(f"Expected exactly one active_default row in {path}; found {len(active)}.")
    row = active[0]
    try:
        renovated_share = float(row["renovated_share_proxy"])
    except (KeyError, ValueError) as exc:
        raise ValueError(f"Invalid renovated_share_proxy in active row of {path}.") from exc
    if not 0.0 < renovated_share < 1.0:
        raise ValueError(
            f"Renovated share proxy in {path} must be between 0 and 1; got {renovated_share}."
        )
    return RenovationPrevalence(
        renovated_share=renovated_share,
        source=f"{source_label or path.as_posix()}::{row['row_id']}",
        status=row.get("source_status", "implemented_proxy"),
        mapping_rule=row.get("mapping_rule", ""),
    )


def _format_float(value: float, digits: int = 9) -> str:
    return f"{value:.{digits}f}"


def _normalised_age_share(dwelling_type: str, age_band: AgeBand) -> float:
    total = sum(max(band.shares_by_type[dwelling_type], 0.0) for band in AGE_BANDS)
    if total <= 0.0:
        raise ValueError(f"Age shares for {dwelling_type} sum to zero.")
    return max(age_band.shares_by_type[dwelling_type], 0.0) / total


def _base_rows_by_type_and_state(rows: list[dict[str, str]]) -> dict[tuple[str, str], dict[str, str]]:
    return {(row["dwelling_type"], row["renovation_state"]): row for row in rows}


DEFAULT_RENOVATION_PREVALENCE = RenovationPrevalence(
    renovated_share=0.159946600,
    source="inputs/building/renovation_prevalence_epc_mapping.csv::belgium_weighted_epc_ab_proxy",
    status="implemented_proxy",
    mapping_rule=(
        "Weighted regional EPC high-performance proxy; Flanders/Wallonia A+B and Brussels A+B "
        "mapped to the single current-code/deep-renovation-like model state."
    ),
)


def build_age_split_rows(
    base_rows: list[dict[str, str]],
    renovation_prevalence: RenovationPrevalence = DEFAULT_RENOVATION_PREVALENCE,
) -> list[dict[str, str]]:
    """Expand the eight-row v2 table into age-split as-is rows plus one renovated row per type."""

    lookup = _base_rows_by_type_and_state(base_rows)
    out: list[dict[str, str]] = []

    for dwelling_type, type_share in TYPE_STOCK_SHARES.items():
        as_is_base = lookup[(dwelling_type, "as_is")]
        renovated_base = lookup[(dwelling_type, "renovated")]
        renovated_share = renovation_prevalence.renovated_share
        as_is_share = 1.0 - renovated_share

        for age_band in AGE_BANDS:
            row = dict(as_is_base)
            row["archetype_id"] = f"BE_RES_{TYPE_ID_TOKENS[dwelling_type]}_{age_band.id_token}_AS_IS_HP_V1"
            row["archetype_name"] = f"{TYPE_LABELS[dwelling_type]} as-is {age_band.label} HP"
            row["stock_weight"] = _format_float(type_share * as_is_share * _normalised_age_share(dwelling_type, age_band))
            row["construction_period_id"] = age_band.period_id
            row["construction_period"] = age_band.label
            row["u_value_package_id"] = age_band.u_value_package_id
            row["u_value_package_source"] = "Belgian TABULA current-state construction-element package"
            row["stock_weight_source"] = (
                "Statbel 2024 four-type dwelling share times Statbel 2024 type-specific "
                "construction-period mix times Belgian weighted EPC A/B renovation-prevalence proxy."
            )
            row["renovation_prevalence_source"] = renovation_prevalence.source
            row["renovation_prevalence_status"] = renovation_prevalence.status
            row["renovation_prevalence_proxy"] = _format_float(renovation_prevalence.renovated_share)
            row["renovation_mapping_rule"] = renovation_prevalence.mapping_rule
            row["cluster_reference"] = f"as_is_{age_band.period_id}"
            row["value_source"] = VALUE_SOURCE
            row["derivation_note"] = (
                "Age-split as-is archetype: top-level dwelling-type share from Statbel 2024 "
                "R1-R4 dwelling shares, construction-period share from Statbel 2024 type-specific "
                "building-count age mix, floor area from Belgian TABULA representative type means, "
                "and envelope U-values from Belgian TABULA current-state age packages."
            )
            row["uncertainty_note"] = (
                "Renovation prevalence is mapped from regional EPC A/B distributions to one "
                "Belgium-wide proxy because no robust dwelling_type x construction_period x "
                "renovation_state matrix is available; apartment age mix uses building-count "
                "proxy; ACH50, ventilation, thermal mass, solar factors, and behaviour remain "
                "archetype assumptions."
            )
            out.append(row)

        renovated = dict(renovated_base)
        renovated["stock_weight"] = _format_float(type_share * renovated_share)
        renovated["construction_period_id"] = "all_periods"
        renovated["construction_period"] = "all periods"
        renovated["u_value_package_id"] = "current_code_deep_renovation"
        renovated["u_value_package_source"] = "current Flemish/Walloon EPB envelope levels"
        renovated["stock_weight_source"] = (
            "Statbel 2024 four-type dwelling share times Belgian weighted EPC A/B renovation-prevalence proxy."
        )
        renovated["renovation_prevalence_source"] = renovation_prevalence.source
        renovated["renovation_prevalence_status"] = renovation_prevalence.status
        renovated["renovation_prevalence_proxy"] = _format_float(renovation_prevalence.renovated_share)
        renovated["renovation_mapping_rule"] = renovation_prevalence.mapping_rule
        renovated["cluster_reference"] = "current_code_deep_renovation"
        renovated["value_source"] = VALUE_SOURCE
        renovated["derivation_note"] = (
            "Single explicit renovated archetype: top-level dwelling-type share from Statbel 2024 "
            "R1-R4 dwelling shares, Belgian weighted EPC A/B renovation-prevalence proxy, and "
            "envelope U-values assigned to a current-code deep-renovation package."
        )
        renovated["uncertainty_note"] = (
            "The EPC A/B prevalence proxy is a better documented high-performance-stock proxy than "
            "the previous v2 split, but it is not proof that all mapped dwellings meet the exact "
            "current-code envelope package."
        )
        out.append(renovated)

    total = sum(float(row["stock_weight"]) for row in out)
    if total <= 0.0:
        raise ValueError("Generated stock weights sum to zero.")
    for row in out:
        row["stock_weight"] = _format_float(float(row["stock_weight"]) / total)

    return out


def _fieldnames(base_fieldnames: list[str]) -> list[str]:
    fields = list(base_fieldnames)
    insert_after = "renovation_state"
    insert_at = fields.index(insert_after) + 1 if insert_after in fields else len(fields)
    for field in reversed(EXTRA_FIELDS):
        if field not in fields:
            fields.insert(insert_at, field)
    return fields


def write_report(
    path: Path,
    rows: list[dict[str, str]],
    envelope_rows: list[dict[str, str]],
    renovation_prevalence: RenovationPrevalence,
) -> None:
    envelope_by_id = {row["archetype_id"]: row for row in envelope_rows}
    lines = [
        "# Model v3 age-split archetype report",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        "",
        "## Purpose",
        "",
        "This report documents the split of the previous eight runtime archetypes into "
        "dwelling-type by construction-period as-is archetypes plus one explicit "
        "current-code renovated archetype per dwelling type.",
        "",
        "## Source basis",
        "",
        f"- Local research note: `{EVIDENCE_NOTE}`",
        "- Top-level dwelling-type shares: Statbel 2024 four-type R1-R4 dwelling shares from the research note.",
        "- Age shares by type: Statbel 2024 type-specific construction-period mix from the research note.",
        "- As-is envelope U-values: Belgian TABULA current-state construction-element packages.",
        "- Renovated envelope U-values: current-code deep-renovation package, wall/roof/floor/window `0.24/0.24/0.24/1.50 W/m2K`.",
        (
            "- Renovation share: Belgian weighted EPC A/B high-performance proxy "
            f"({renovation_prevalence.renovated_share:.1%}) from "
            f"`{renovation_prevalence.source}`."
        ),
        f"- Renovation mapping rule: {renovation_prevalence.mapping_rule}",
        "",
        "## Generated rows",
        "",
        "| archetype_id | stock_weight | period | package | UA_W_per_K |",
        "|---|---:|---|---|---:|",
    ]
    for row in rows:
        env = envelope_by_id[row["archetype_id"]]
        lines.append(
            f"| `{row['archetype_id']}` | {float(row['stock_weight']):.6f} | "
            f"{row['construction_period']} | `{row['u_value_package_id']}` | {float(env['UA_total_W_K']):.1f} |"
        )
    lines.extend(
        [
            "",
            "## Caveats",
            "",
            "- Apartment construction-period shares use building-count age mix as a proxy for dwelling-count age mix.",
            "- Renovation prevalence is source-backed but still a proxy because Belgian public evidence does not provide the full type-by-age renovation matrix needed by this archetype set.",
            "- TABULA U-values are archetype package values, not measured field observations.",
            "- Thermal bridges are represented by simple adders because the TABULA sub-typology did not include them.",
            "- The current-code renovated state is a scenario/technical package; it should not be described as the measured average renovated Belgian dwelling.",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_age_split_archetypes(
    repo_root: Path,
    base_table: Path = DEFAULT_BASE_TABLE,
    output_table: Path = DEFAULT_OUTPUT_TABLE,
    envelope_table: Path = DEFAULT_ENVELOPE_TABLE,
    renovation_prevalence_table: Path = DEFAULT_RENOVATION_PREVALENCE_TABLE,
    report_path: Path = DEFAULT_REPORT,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    base_fieldnames, base_rows = read_csv(repo_root / base_table)
    renovation_prevalence = load_renovation_prevalence(
        repo_root / renovation_prevalence_table,
        source_label=renovation_prevalence_table.as_posix(),
    )
    rows = build_age_split_rows(base_rows, renovation_prevalence=renovation_prevalence)
    envelope_rows = build_envelope_rows(rows)
    rows = update_archetype_rows(
        rows,
        envelope_rows,
        envelope_table_label=envelope_table.as_posix(),
        overwrite_notes=False,
    )
    for row in rows:
        row["value_source"] = VALUE_SOURCE
    write_csv(repo_root / envelope_table, ENVELOPE_FIELDS, envelope_rows)
    write_csv(repo_root / output_table, _fieldnames(base_fieldnames), rows)
    write_report(repo_root / report_path, rows, envelope_rows, renovation_prevalence)
    return rows, envelope_rows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--base-table", type=Path, default=DEFAULT_BASE_TABLE)
    parser.add_argument("--output-table", type=Path, default=DEFAULT_OUTPUT_TABLE)
    parser.add_argument("--envelope-table", type=Path, default=DEFAULT_ENVELOPE_TABLE)
    parser.add_argument("--renovation-prevalence-table", type=Path, default=DEFAULT_RENOVATION_PREVALENCE_TABLE)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--print-summary", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    rows, envelope_rows = build_age_split_archetypes(
        repo_root=args.repo_root.resolve(),
        base_table=args.base_table,
        output_table=args.output_table,
        envelope_table=args.envelope_table,
        renovation_prevalence_table=args.renovation_prevalence_table,
        report_path=args.report,
    )
    if args.print_summary:
        total_weight = sum(float(row["stock_weight"]) for row in rows)
        print("Age-split archetype generation complete.")
        print(f"Rows: {len(rows)}")
        print(f"Stock weight sum: {total_weight:.6f}")
        print(f"Envelope rows: {len(envelope_rows)}")
        print(f"Output table: {args.output_table}")
        print(f"Envelope table: {args.envelope_table}")
        print(f"Report: {args.report}")


if __name__ == "__main__":
    main()
