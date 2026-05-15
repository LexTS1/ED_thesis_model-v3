"""Reconstruct aggregate envelope UA values for model_v3 archetypes.

This module creates an auditable intermediate envelope table from the runtime
archetype table. It does not run the energy model. The reconstruction uses
simple geometry assumptions plus empirically grounded U-value anchors from the
local Belgian archetype evidence note and existing repository config.
"""

from __future__ import annotations

import argparse
import csv
import math
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping


DEFAULT_ARCHETYPE_TABLE = Path("inputs/building/archetype_parameters_merged_v2.csv")
DEFAULT_ENVELOPE_TABLE = Path("inputs/building/envelope_archetypes_v1.csv")
DEFAULT_REPORT = Path("reports/model_v3_envelope_reconstruction_report.md")

EVIDENCE_NOTE = (
    "DeepSearch/Empirical grounding for Belgian residential archetypes.md"
)
SOURCE_NOTE = (
    "Envelope areas reconstructed from floor area, ceiling height, storey count, "
    "glazing ratio, and exposed-wall fractions. U-value anchors use local "
    "Belgian archetype evidence: old/as-is facade and roof values from the "
    "DeepSearch TABULA summary, renovated EPB2010 package values from the "
    "same note, and old floor/window defaults from config/archetypes.yaml."
)


@dataclass(frozen=True)
class GeometryAssumption:
    storeys: float
    exposed_wall_fraction: float
    roof_exposure_fraction: float
    floor_exposure_fraction: float


@dataclass(frozen=True)
class UValueSet:
    wall_W_m2K: float
    roof_W_m2K: float
    floor_W_m2K: float
    window_W_m2K: float
    thermal_bridge_fraction: float
    source: str


GEOMETRY_BY_DWELLING_TYPE: dict[str, GeometryAssumption] = {
    "detached": GeometryAssumption(
        storeys=2.0,
        exposed_wall_fraction=1.00,
        roof_exposure_fraction=1.00,
        floor_exposure_fraction=1.00,
    ),
    "semi_detached": GeometryAssumption(
        storeys=2.0,
        exposed_wall_fraction=0.75,
        roof_exposure_fraction=1.00,
        floor_exposure_fraction=1.00,
    ),
    "terraced": GeometryAssumption(
        storeys=2.0,
        exposed_wall_fraction=0.50,
        roof_exposure_fraction=1.00,
        floor_exposure_fraction=1.00,
    ),
    "apartment": GeometryAssumption(
        storeys=1.0,
        exposed_wall_fraction=0.35,
        roof_exposure_fraction=0.25,
        floor_exposure_fraction=0.25,
    ),
}

U_VALUES_BY_RENOVATION_STATE: dict[str, UValueSet] = {
    "as_is": UValueSet(
        wall_W_m2K=1.65,
        roof_W_m2K=1.94,
        floor_W_m2K=1.04,
        window_W_m2K=3.91,
        thermal_bridge_fraction=0.10,
        source=(
            "as-is wall/roof from local DeepSearch TABULA summary; "
            "floor/window from config/archetypes.yaml old_stock defaults"
        ),
    ),
    "renovated": UValueSet(
        wall_W_m2K=0.40,
        roof_W_m2K=0.30,
        floor_W_m2K=0.40,
        window_W_m2K=2.00,
        thermal_bridge_fraction=0.05,
        source="renovated EPB2010 package from local DeepSearch note",
    ),
}

U_VALUES_BY_PACKAGE: dict[str, UValueSet] = {
    "tabula_current_pre_1946": UValueSet(
        wall_W_m2K=2.20,
        roof_W_m2K=1.70,
        floor_W_m2K=0.85,
        window_W_m2K=5.00,
        thermal_bridge_fraction=0.10,
        source="Belgian TABULA current-state construction-element package for <1946 stock",
    ),
    "tabula_current_1946_1970": UValueSet(
        wall_W_m2K=1.70,
        roof_W_m2K=1.90,
        floor_W_m2K=0.85,
        window_W_m2K=5.00,
        thermal_bridge_fraction=0.10,
        source="Belgian TABULA current-state construction-element package for 1946-1970 stock",
    ),
    "tabula_current_1971_1991": UValueSet(
        wall_W_m2K=1.00,
        roof_W_m2K=0.85,
        floor_W_m2K=0.85,
        window_W_m2K=3.50,
        thermal_bridge_fraction=0.10,
        source="Belgian TABULA current-state construction-element package for 1971-1991 stock",
    ),
    "tabula_current_1992_2011": UValueSet(
        wall_W_m2K=0.60,
        roof_W_m2K=0.60,
        floor_W_m2K=0.70,
        window_W_m2K=3.50,
        thermal_bridge_fraction=0.10,
        source="Belgian TABULA current-state construction-element package mapped to 1992-2011 stock",
    ),
    "tabula_current_2012_plus": UValueSet(
        wall_W_m2K=0.40,
        roof_W_m2K=0.30,
        floor_W_m2K=0.40,
        window_W_m2K=2.00,
        thermal_bridge_fraction=0.05,
        source="Belgian TABULA / EPB2010-equivalent package mapped to 2012+ stock",
    ),
    "current_code_deep_renovation": UValueSet(
        wall_W_m2K=0.24,
        roof_W_m2K=0.24,
        floor_W_m2K=0.24,
        window_W_m2K=1.50,
        thermal_bridge_fraction=0.05,
        source="current-code deep-renovation envelope package from Flemish/Walloon EPB levels",
    ),
}

ENVELOPE_FIELDS = [
    "archetype_id",
    "dwelling_type",
    "renovation_state",
    "construction_period_id",
    "construction_period",
    "u_value_package_id",
    "floor_area_m2",
    "ceiling_height_m",
    "storeys_assumed",
    "footprint_m2",
    "perimeter_m",
    "gross_wall_area_m2",
    "exposed_wall_fraction",
    "exposed_wall_area_m2",
    "glazing_ratio",
    "window_area_m2",
    "opaque_wall_area_m2",
    "roof_area_m2",
    "floor_exposed_area_m2",
    "U_wall_W_m2K",
    "U_roof_W_m2K",
    "U_floor_W_m2K",
    "U_window_W_m2K",
    "UA_wall_W_K",
    "UA_roof_W_K",
    "UA_floor_W_K",
    "UA_window_W_K",
    "UA_base_W_K",
    "thermal_bridge_fraction",
    "UA_thermal_bridge_W_K",
    "UA_total_W_K",
    "geometry_source",
    "u_value_source",
    "caveat",
]


def _float(row: dict[str, str], key: str) -> float:
    value = row.get(key, "")
    if value == "":
        raise ValueError(f"Missing numeric value for {key!r} in {row.get('archetype_id', '<unknown>')}")
    return float(value)


def _fmt(value: float, digits: int = 3) -> str:
    return f"{value:.{digits}f}"


def u_value_set_for_row(row: Mapping[str, str]) -> UValueSet:
    """Resolve the most specific U-value set available for an archetype row."""

    package_id = str(row.get("u_value_package_id", "")).strip()
    if package_id:
        if package_id not in U_VALUES_BY_PACKAGE:
            raise ValueError(f"Unsupported u_value_package_id {package_id!r} in {row.get('archetype_id', '<unknown>')}")
        return U_VALUES_BY_PACKAGE[package_id]

    renovation_state = str(row.get("renovation_state", "")).strip()
    if renovation_state not in U_VALUES_BY_RENOVATION_STATE:
        raise ValueError(f"Unsupported renovation_state {renovation_state!r} in {row.get('archetype_id', '<unknown>')}")
    return U_VALUES_BY_RENOVATION_STATE[renovation_state]


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        rows = [dict(row) for row in reader]
    return fieldnames, rows


def write_csv(path: Path, fieldnames: Iterable[str], rows: Iterable[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fieldnames))
        writer.writeheader()
        writer.writerows(rows)


def reconstruct_envelope_row(row: dict[str, str]) -> dict[str, str]:
    archetype_id = row["archetype_id"]
    dwelling_type = row["dwelling_type"]
    renovation_state = row["renovation_state"]

    if dwelling_type not in GEOMETRY_BY_DWELLING_TYPE:
        raise ValueError(f"Unsupported dwelling_type {dwelling_type!r} in {archetype_id}")

    geometry = GEOMETRY_BY_DWELLING_TYPE[dwelling_type]
    u_values = u_value_set_for_row(row)

    floor_area = _float(row, "floor_area_m2")
    ceiling_height = _float(row, "ceiling_height_m")
    glazing_ratio = _float(row, "glazing_ratio")

    footprint = floor_area / geometry.storeys
    side_length = math.sqrt(footprint)
    perimeter = 4.0 * side_length
    gross_wall_area = perimeter * geometry.storeys * ceiling_height
    exposed_wall_area = gross_wall_area * geometry.exposed_wall_fraction
    requested_window_area = floor_area * glazing_ratio
    window_area = min(requested_window_area, exposed_wall_area * 0.90)
    opaque_wall_area = max(exposed_wall_area - window_area, 0.0)
    roof_area = footprint * geometry.roof_exposure_fraction
    floor_exposed_area = footprint * geometry.floor_exposure_fraction

    ua_wall = opaque_wall_area * u_values.wall_W_m2K
    ua_roof = roof_area * u_values.roof_W_m2K
    ua_floor = floor_exposed_area * u_values.floor_W_m2K
    ua_window = window_area * u_values.window_W_m2K
    ua_base = ua_wall + ua_roof + ua_floor + ua_window
    ua_bridge = ua_base * u_values.thermal_bridge_fraction
    ua_total = ua_base + ua_bridge

    if str(row.get("construction_period_id", "")).strip():
        caveat = (
            "Schematic envelope reconstruction: rectangular footprint, type-level "
            "exposure factors, construction-period U-value packages, and no measured "
            "wall/window areas."
        )
    else:
        caveat = (
            "Schematic envelope reconstruction: rectangular footprint, type-level "
            "exposure factors, no dwelling-age split within renovation state, and "
            "no measured wall/window areas."
        )
    if window_area < requested_window_area:
        caveat += " Window area was capped at 90% of exposed wall area."

    return {
        "archetype_id": archetype_id,
        "dwelling_type": dwelling_type,
        "renovation_state": renovation_state,
        "construction_period_id": str(row.get("construction_period_id", "")),
        "construction_period": str(row.get("construction_period", "")),
        "u_value_package_id": str(row.get("u_value_package_id", "")),
        "floor_area_m2": _fmt(floor_area, 1),
        "ceiling_height_m": _fmt(ceiling_height, 2),
        "storeys_assumed": _fmt(geometry.storeys, 1),
        "footprint_m2": _fmt(footprint),
        "perimeter_m": _fmt(perimeter),
        "gross_wall_area_m2": _fmt(gross_wall_area),
        "exposed_wall_fraction": _fmt(geometry.exposed_wall_fraction, 2),
        "exposed_wall_area_m2": _fmt(exposed_wall_area),
        "glazing_ratio": _fmt(glazing_ratio, 3),
        "window_area_m2": _fmt(window_area),
        "opaque_wall_area_m2": _fmt(opaque_wall_area),
        "roof_area_m2": _fmt(roof_area),
        "floor_exposed_area_m2": _fmt(floor_exposed_area),
        "U_wall_W_m2K": _fmt(u_values.wall_W_m2K, 2),
        "U_roof_W_m2K": _fmt(u_values.roof_W_m2K, 2),
        "U_floor_W_m2K": _fmt(u_values.floor_W_m2K, 2),
        "U_window_W_m2K": _fmt(u_values.window_W_m2K, 2),
        "UA_wall_W_K": _fmt(ua_wall),
        "UA_roof_W_K": _fmt(ua_roof),
        "UA_floor_W_K": _fmt(ua_floor),
        "UA_window_W_K": _fmt(ua_window),
        "UA_base_W_K": _fmt(ua_base),
        "thermal_bridge_fraction": _fmt(u_values.thermal_bridge_fraction, 2),
        "UA_thermal_bridge_W_K": _fmt(ua_bridge),
        "UA_total_W_K": _fmt(ua_total),
        "geometry_source": "rebuild_archetype_envelope.py geometry assumptions",
        "u_value_source": u_values.source,
        "caveat": caveat,
    }


def build_envelope_rows(archetype_rows: Iterable[dict[str, str]]) -> list[dict[str, str]]:
    return [reconstruct_envelope_row(row) for row in archetype_rows]


def update_archetype_rows(
    archetype_rows: Iterable[dict[str, str]],
    envelope_rows: Iterable[dict[str, str]],
    envelope_table_label: str = "inputs/building/envelope_archetypes_v1.csv",
    overwrite_notes: bool = True,
) -> list[dict[str, str]]:
    by_id = {row["archetype_id"]: row for row in envelope_rows}
    updated: list[dict[str, str]] = []
    for row in archetype_rows:
        out = dict(row)
        archetype_id = out["archetype_id"]
        if archetype_id not in by_id:
            raise ValueError(f"No envelope row for {archetype_id}")
        ua = by_id[archetype_id]["UA_total_W_K"]
        out["H_W_per_K"] = ua
        out["UA_W_per_K"] = ua
        if overwrite_notes:
            out["value_source"] = "empirical_grounding_2026_statbel_tabula_belgian_airtightness_envelope_reconstruction_v1"
            out["derivation_note"] = (
                "Stock weights use Census 2021 dwelling-type shares while preserving existing "
                "as-is/renovated split; floor areas use Belgian TABULA representative type "
                "means; ACH50 uses Belgian measured airtightness anchors; UA/H is rebuilt "
                f"from {envelope_table_label} using explicit "
                "envelope-area and U-value assumptions."
            )
            out["uncertainty_note"] = (
                "Renovation-state shares, exact envelope areas, within-age U-value variation, "
                "ACH50-to-natural-infiltration conversion, ventilation prevalence, solar "
                "factors, setpoints, and internal gains remain assumption-based and should "
                "be sensitivity-tested."
            )
        updated.append(out)
    return updated


def _git_commit(repo_root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
        )
    except Exception:
        return "unknown"
    return result.stdout.strip() or "unknown"


def write_report(
    path: Path,
    repo_root: Path,
    old_rows: list[dict[str, str]],
    envelope_rows: list[dict[str, str]],
    archetype_path: Path,
    envelope_path: Path,
) -> None:
    by_id = {row["archetype_id"]: row for row in envelope_rows}
    lines = [
        "# Model v3 envelope reconstruction report",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        f"Git commit: {_git_commit(repo_root)}",
        "",
        "## Purpose",
        "",
        "This report documents the reconstruction of aggregate envelope conductance "
        "`UA_W_per_K` and `H_W_per_K` for the runtime building archetype table. "
        "The previous table used aggregate values inherited from the stock archetype "
        "file. The new values are derived from an explicit intermediate envelope "
        "table so that geometry and U-value assumptions can be inspected.",
        "",
        "## Files",
        "",
        f"- Runtime archetype table: `{archetype_path.as_posix()}`",
        f"- Envelope reconstruction table: `{envelope_path.as_posix()}`",
        f"- Local evidence note: `{EVIDENCE_NOTE}`",
        "",
        "## Reconstruction assumptions",
        "",
        "- Footprint is `floor_area_m2 / storeys_assumed`.",
        "- A square footprint is assumed to estimate perimeter.",
        "- Gross wall area is `perimeter * storeys_assumed * ceiling_height_m`.",
        "- Exposed-wall fractions are detached `1.00`, semi-detached `0.75`, terraced `0.50`, apartment `0.35`.",
        "- Apartment roof and floor exposure fractions are `0.25`; house roof and floor exposure fractions are `1.00`.",
        "- Window area is `floor_area_m2 * glazing_ratio`, capped at 90% of exposed wall area if needed.",
        "- As-is U-values use wall `1.65`, roof `1.94`, floor `1.04`, window `3.91 W/m2K`.",
        "- Renovated U-values use wall `0.40`, roof `0.30`, floor `0.40`, window `2.00 W/m2K`.",
        "- Thermal-bridge adders are 10% for as-is archetypes and 5% for renovated archetypes.",
        "",
        "## UA comparison",
        "",
        "| archetype_id | old_UA_W_per_K | reconstructed_UA_W_per_K | delta_pct |",
        "|---|---:|---:|---:|",
    ]
    for row in old_rows:
        archetype_id = row["archetype_id"]
        old_ua = float(row["UA_W_per_K"])
        new_ua = float(by_id[archetype_id]["UA_total_W_K"])
        delta_pct = 100.0 * (new_ua - old_ua) / old_ua if old_ua else float("nan")
        lines.append(
            f"| `{archetype_id}` | {old_ua:.1f} | {new_ua:.1f} | {delta_pct:+.1f}% |"
        )

    lines.extend(
        [
            "",
            "## Caveats",
            "",
            "- This is still an archetype-level reconstruction, not a measured envelope survey.",
            "- The table does not yet distinguish construction period within the as-is state.",
            "- Exact external wall, party-wall, roof, floor, and glazing areas are not known.",
            "- Apartment exposure is represented by a simple average exposure factor.",
            "- The reconstruction strengthens traceability, but the resulting UA values should still be sensitivity-tested.",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def rebuild_envelope(
    repo_root: Path,
    archetype_table: Path = DEFAULT_ARCHETYPE_TABLE,
    envelope_table: Path = DEFAULT_ENVELOPE_TABLE,
    report_path: Path = DEFAULT_REPORT,
    update_archetypes: bool = True,
    write_report_file: bool = True,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    archetype_path = repo_root / archetype_table
    envelope_path = repo_root / envelope_table
    report_output_path = repo_root / report_path

    fieldnames, archetype_rows = read_csv(archetype_path)
    envelope_rows = build_envelope_rows(archetype_rows)
    write_csv(envelope_path, ENVELOPE_FIELDS, envelope_rows)

    updated_rows = archetype_rows
    if update_archetypes:
        updated_rows = update_archetype_rows(archetype_rows, envelope_rows)
        write_csv(archetype_path, fieldnames, updated_rows)

    if write_report_file:
        write_report(
            report_output_path,
            repo_root,
            archetype_rows,
            envelope_rows,
            archetype_table,
            envelope_table,
        )
    return envelope_rows, updated_rows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--archetype-table", type=Path, default=DEFAULT_ARCHETYPE_TABLE)
    parser.add_argument("--envelope-table", type=Path, default=DEFAULT_ENVELOPE_TABLE)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument(
        "--no-update-archetypes",
        action="store_true",
        help="Only write the envelope table; do not update H_W_per_K/UA_W_per_K.",
    )
    parser.add_argument("--no-report", action="store_true")
    parser.add_argument("--print-summary", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    repo_root = args.repo_root.resolve()
    envelope_rows, updated_rows = rebuild_envelope(
        repo_root=repo_root,
        archetype_table=args.archetype_table,
        envelope_table=args.envelope_table,
        report_path=args.report,
        update_archetypes=not args.no_update_archetypes,
        write_report_file=not args.no_report,
    )
    if args.print_summary:
        ua_values = [float(row["UA_total_W_K"]) for row in envelope_rows]
        print("Envelope reconstruction complete.")
        print(f"Envelope rows: {len(envelope_rows)}")
        print(f"Updated archetype rows: {len(updated_rows)}")
        print(f"UA range W/K: {min(ua_values):.1f} - {max(ua_values):.1f}")
        print(f"Envelope table: {args.envelope_table}")
        if not args.no_report:
            print(f"Report: {args.report}")


if __name__ == "__main__":
    main()
