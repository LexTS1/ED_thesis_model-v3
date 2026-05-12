from __future__ import annotations

from pathlib import Path

from model_v3.building.rebuild_archetype_envelope import (
    build_envelope_rows,
    read_csv,
    reconstruct_envelope_row,
    update_archetype_rows,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
ARCHETYPE_TABLE = REPO_ROOT / "inputs/model_v3/building/archetype_parameters_merged_v2.csv"


def test_reconstructed_envelope_rows_are_positive() -> None:
    _, archetype_rows = read_csv(ARCHETYPE_TABLE)

    envelope_rows = build_envelope_rows(archetype_rows)

    assert len(envelope_rows) == len(archetype_rows)
    for row in envelope_rows:
        assert float(row["exposed_wall_area_m2"]) > 0
        assert float(row["window_area_m2"]) > 0
        assert float(row["opaque_wall_area_m2"]) >= 0
        assert float(row["roof_area_m2"]) > 0
        assert float(row["floor_exposed_area_m2"]) > 0
        assert float(row["UA_total_W_K"]) > 0


def test_renovated_ua_is_lower_than_as_is_for_each_dwelling_type() -> None:
    _, archetype_rows = read_csv(ARCHETYPE_TABLE)
    envelope_rows = build_envelope_rows(archetype_rows)
    by_type_state = {
        (row["dwelling_type"], row["renovation_state"]): float(row["UA_total_W_K"])
        for row in envelope_rows
    }

    for dwelling_type in ("detached", "semi_detached", "terraced", "apartment"):
        assert by_type_state[(dwelling_type, "renovated")] < by_type_state[(dwelling_type, "as_is")]


def test_archetype_update_uses_reconstructed_ua_for_h_and_ua() -> None:
    _, archetype_rows = read_csv(ARCHETYPE_TABLE)
    envelope_rows = build_envelope_rows(archetype_rows)

    updated_rows = update_archetype_rows(archetype_rows, envelope_rows)

    envelope_by_id = {row["archetype_id"]: row for row in envelope_rows}
    for row in updated_rows:
        expected = envelope_by_id[row["archetype_id"]]["UA_total_W_K"]
        assert row["H_W_per_K"] == expected
        assert row["UA_W_per_K"] == expected
        assert "envelope_archetypes_v1.csv" in row["derivation_note"]


def test_window_area_is_capped_below_exposed_wall_area() -> None:
    row = {
        "archetype_id": "test_apartment",
        "dwelling_type": "apartment",
        "renovation_state": "as_is",
        "floor_area_m2": "100",
        "ceiling_height_m": "2.5",
        "glazing_ratio": "1.0",
    }

    envelope = reconstruct_envelope_row(row)

    assert float(envelope["window_area_m2"]) <= 0.9 * float(envelope["exposed_wall_area_m2"])
    assert "Window area was capped" in envelope["caveat"]
