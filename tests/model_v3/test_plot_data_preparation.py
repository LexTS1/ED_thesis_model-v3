from __future__ import annotations

import pandas as pd
import pytest

from model_v3.scenarios.generate_figures import climate_2050_policy, metric_stats_from_aggregates
from model_v3.scenarios.plot_style import (
    climate_pathway_label,
    climate_window_label,
    stable_scenario_sort,
    technology_case_label,
)

from .figure_test_utils import aggregate_row


def test_scenario_ordering_is_stable() -> None:
    rows = [
        {"scenario_id": "b", "climate_window_id": "long_term_2080_2100", "climate_pathway_id": "rcp_8_5", "technology_case_id": "tech_high_electrification_pv_ev"},
        {"scenario_id": "a", "climate_window_id": "baseline_1981_2005", "climate_pathway_id": "historical", "technology_case_id": "tech_current_stock"},
    ]

    ordered = stable_scenario_sort(pd.DataFrame(rows))

    assert ordered.iloc[0]["scenario_id"] == "a"
    assert ordered.iloc[1]["scenario_id"] == "b"


def test_human_readable_labels_are_defined() -> None:
    assert climate_window_label("near_future_2030_2049") == "Near future 2030-2049"
    assert climate_pathway_label("rcp_8_5") == "RCP 8.5"
    assert technology_case_label("tech_high_electrification_pv_ev") == "High electrification + PV/EV"


def test_missing_required_metric_raises_clear_error() -> None:
    aggregate = pd.DataFrame(
        [
            {
                "scenario_id": "baseline_1981_2005__historical__tech_current_stock",
                "climate_window_id": "baseline_1981_2005",
                "climate_pathway_id": "historical",
                "technology_case_id": "tech_current_stock",
            }
        ]
    )

    with pytest.raises(ValueError, match="missing required metric"):
        metric_stats_from_aggregates(aggregate, ["annual_grid_import_kWh"])


def test_2050_policy_flags_are_preserved() -> None:
    frame = pd.DataFrame(
        [
            {"climate_window_id": "near_future_2030_2049", "climate_includes_2050": False},
            {"climate_window_id": "mid_century_2050_2070", "climate_includes_2050": True},
        ]
    )

    near_includes, mid_includes = climate_2050_policy(frame)

    assert near_includes is False
    assert mid_includes is True


def test_metric_stats_from_aggregates_uses_stable_percentiles() -> None:
    aggregate = pd.DataFrame(
        [
            aggregate_row(
                "baseline_1981_2005__historical__tech_current_stock",
                "baseline_1981_2005",
                "historical",
                "tech_current_stock",
                10.0,
            )
        ]
    )

    stats = metric_stats_from_aggregates(aggregate, ["annual_grid_import_kWh"])

    assert list(stats["metric"]) == ["annual_grid_import_kWh"]
    assert stats.iloc[0]["p10"] < stats.iloc[0]["mean"] < stats.iloc[0]["p90"]
