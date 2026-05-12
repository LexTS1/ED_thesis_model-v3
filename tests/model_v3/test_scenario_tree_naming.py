"""Tests for scenario-tree identifier naming helpers."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
MODEL_V3_SRC = REPO_ROOT / "src"
if str(MODEL_V3_SRC) not in sys.path:
    sys.path.insert(0, str(MODEL_V3_SRC))

from model_v3.scenario_tree.naming import (  # noqa: E402
    ScenarioTreeNamingError,
    make_scenario_id,
    make_scenario_leaf_id,
    parse_scenario_leaf_id,
    validate_scenario_id,
    validate_scenario_leaf_id,
)


class ScenarioTreeNamingTest(unittest.TestCase):
    def test_valid_scenario_id_passes(self) -> None:
        scenario_id = make_scenario_id(
            "mid_century_2050_2070",
            "rcp_8_5",
            "tech_high_electrification_pv_ev",
        )

        self.assertEqual(
            scenario_id,
            "mid_century_2050_2070__rcp_8_5__tech_high_electrification_pv_ev",
        )

    def test_valid_scenario_leaf_id_passes(self) -> None:
        scenario_id = "baseline_1981_2005__historical__tech_current_stock"
        scenario_leaf_id = make_scenario_leaf_id(scenario_id, "seed_0042")

        self.assertEqual(
            scenario_leaf_id,
            "baseline_1981_2005__historical__tech_current_stock__seed_0042",
        )
        self.assertEqual(parse_scenario_leaf_id(scenario_leaf_id)["realization_id"], "seed_0042")

    def test_uppercase_id_fails(self) -> None:
        with self.assertRaises(ScenarioTreeNamingError):
            validate_scenario_id("mid_century_2050_2070__RCP_8_5__tech_high_electrification_pv_ev")

    def test_space_containing_id_fails(self) -> None:
        with self.assertRaises(ScenarioTreeNamingError):
            validate_scenario_id("mid century_2050_2070__rcp_8_5__tech_high_electrification_pv_ev")

    def test_hyphen_containing_id_fails(self) -> None:
        with self.assertRaises(ScenarioTreeNamingError):
            validate_scenario_id("mid-century_2050_2070__rcp_8_5__tech_high_electrification_pv_ev")

    def test_scenario_id_with_wrong_number_of_dimensions_fails(self) -> None:
        with self.assertRaises(ScenarioTreeNamingError):
            validate_scenario_id("mid_century_2050_2070__rcp_8_5")
        with self.assertRaises(ScenarioTreeNamingError):
            validate_scenario_id("mid_century_2050_2070_rcp_8_5_tech_high_electrification_pv_ev")

    def test_scenario_leaf_id_with_wrong_number_of_dimensions_fails(self) -> None:
        with self.assertRaises(ScenarioTreeNamingError):
            validate_scenario_leaf_id("mid_century_2050_2070__rcp_8_5__tech_high_electrification_pv_ev")

    def test_malformed_seed_id_fails(self) -> None:
        with self.assertRaises(ScenarioTreeNamingError):
            validate_scenario_leaf_id(
                "mid_century_2050_2070__rcp_8_5__tech_high_electrification_pv_ev__seed_42"
            )

    def test_future_scenario_using_current_stock_fails_by_default(self) -> None:
        with self.assertRaises(ScenarioTreeNamingError):
            validate_scenario_id("mid_century_2050_2070__rcp_8_5__tech_current_stock")


if __name__ == "__main__":
    unittest.main()
