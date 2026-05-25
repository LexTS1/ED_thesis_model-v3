from __future__ import annotations

import pandas as pd
import pytest

from model_v3.validation.runners.model_runner import _build_model_frame_from_cohort


def test_cohort_model_frame_uses_configured_aggregate_column_per_household() -> None:
    results = {
        "household_count": 2,
        "profile_frame": pd.DataFrame(
            {
                "timestamp": pd.date_range("2023-01-01", periods=2, freq="h"),
                "per_household_profile_W": [10.0, 20.0],
                "P_el_gross_actual_W": [100.0, 140.0],
            }
        ),
    }

    frame = _build_model_frame_from_cohort(results, value_column="P_el_gross_actual_W")

    assert frame["value"].tolist() == pytest.approx([50.0, 70.0])

