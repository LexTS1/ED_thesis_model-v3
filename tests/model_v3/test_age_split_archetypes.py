from __future__ import annotations

from pathlib import Path

from model_v3.building.build_age_split_archetypes import (
    AGE_BANDS,
    TYPE_STOCK_SHARES,
    build_age_split_rows,
    read_csv,
)
from model_v3.building.rebuild_archetype_envelope import build_envelope_rows


REPO_ROOT = Path(__file__).resolve().parents[2]
BASE_TABLE = REPO_ROOT / "inputs/building/archetype_parameters_merged_v2.csv"


def test_age_split_generation_creates_expected_row_count_and_normalized_weights() -> None:
    _, base_rows = read_csv(BASE_TABLE)

    rows = build_age_split_rows(base_rows)

    assert len(rows) == 4 * len(AGE_BANDS) + 4
    assert abs(sum(float(row["stock_weight"]) for row in rows) - 1.0) < 1e-6


def test_age_split_type_weights_match_research_shares() -> None:
    _, base_rows = read_csv(BASE_TABLE)

    rows = build_age_split_rows(base_rows)

    for dwelling_type, expected_share in TYPE_STOCK_SHARES.items():
        actual = sum(float(row["stock_weight"]) for row in rows if row["dwelling_type"] == dwelling_type)
        assert abs(actual - expected_share) < 1e-6


def test_as_is_pre_1946_has_higher_ua_than_2012_plus_for_each_type() -> None:
    _, base_rows = read_csv(BASE_TABLE)
    rows = build_age_split_rows(base_rows)
    envelope_rows = build_envelope_rows(rows)
    by_type_period = {
        (row["dwelling_type"], row["construction_period_id"]): float(row["UA_total_W_K"])
        for row in envelope_rows
        if row["renovation_state"] == "as_is"
    }

    for dwelling_type in TYPE_STOCK_SHARES:
        assert by_type_period[(dwelling_type, "pre_1946")] > by_type_period[(dwelling_type, "2012_plus")]


def test_renovated_package_is_current_code_deep_renovation() -> None:
    _, base_rows = read_csv(BASE_TABLE)

    rows = build_age_split_rows(base_rows)

    renovated = [row for row in rows if row["renovation_state"] == "renovated"]
    assert len(renovated) == 4
    assert {row["u_value_package_id"] for row in renovated} == {"current_code_deep_renovation"}
