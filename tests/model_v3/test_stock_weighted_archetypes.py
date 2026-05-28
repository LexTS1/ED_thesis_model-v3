from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from model_v3.scenarios.stock_weighted_archetypes import (
    StockWeightedArchetypeError,
    load_stock_weighted_archetypes,
)


def test_load_stock_weighted_archetypes_normalizes_positive_weights(tmp_path: Path) -> None:
    table = tmp_path / "archetypes.csv"
    pd.DataFrame(
        {
            "archetype_id": ["a", "b", "zero"],
            "stock_weight": [2.0, 1.0, 0.0],
        }
    ).to_csv(table, index=False)

    loaded = load_stock_weighted_archetypes(
        {"building": {"archetype_source": {"file_path": str(table)}}}
    )

    assert loaded["archetype_id"].tolist() == ["a", "b"]
    assert loaded["normalized_stock_weight"].sum() == pytest.approx(1.0)
    assert loaded.loc[loaded["archetype_id"] == "a", "normalized_stock_weight"].iloc[0] == pytest.approx(2.0 / 3.0)


def test_load_stock_weighted_archetypes_rejects_missing_weight_column(tmp_path: Path) -> None:
    table = tmp_path / "archetypes.csv"
    pd.DataFrame({"archetype_id": ["a"]}).to_csv(table, index=False)

    with pytest.raises(StockWeightedArchetypeError):
        load_stock_weighted_archetypes({"building": {"archetype_source": {"file_path": str(table)}}})
