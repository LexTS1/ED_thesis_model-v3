from __future__ import annotations

from pathlib import Path

import pytest

from model_v3.data.loaders import _load_end_use_shares


def test_residual_appliance_lighting_share_is_split(tmp_path: Path) -> None:
    path = tmp_path / "end_use.csv"
    path.write_text(
        "\n".join(
            [
                "parameter,value",
                "Water heating share,12.0",
                "Cooking share,1.64",
                "Residual (AL+OT) share,13.29",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    shares = _load_end_use_shares(
        {
            "file_path": str(path),
            "parameter_column": "parameter",
            "value_column": "value",
            "lighting_fraction_of_appliance_bucket": 0.18,
        }
    )

    assert shares["appliances"] == pytest.approx(0.1329 * 0.82)
    assert shares["lighting"] == pytest.approx(0.1329 * 0.18)
    assert shares["cooking"] == pytest.approx(0.0164)
    assert shares["dhw"] == pytest.approx(0.12)
