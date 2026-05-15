"""Tests for scenario-tree technology input resolution."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
MODEL_V3_SRC = REPO_ROOT / "src"
if str(MODEL_V3_SRC) not in sys.path:
    sys.path.insert(0, str(MODEL_V3_SRC))

from model_v3.scenario_tree.technology_resolver import (  # noqa: E402
    TechnologyResolutionError,
    resolve_technology_inputs,
)
from model_v3.scenario_tree.validate_scenario_tree import validate_scenario_tree  # noqa: E402


CONFIG_ROOT = REPO_ROOT / "config" / "scenario_tree"


class TechnologyResolutionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.result = validate_scenario_tree(CONFIG_ROOT)
        self.technology_cases = self.result.metadata.technology_cases

    def test_known_future_technology_case_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            belgian_inputs = Path(tmp_dir) / "belgian_technology_inputs.yaml"
            belgian_inputs.write_text("technology_inputs: {}\n", encoding="utf-8")

            resolved = resolve_technology_inputs(
                "tech_high_electrification_pv_ev",
                self.technology_cases,
                belgian_inputs,
                window_type="future",
            )

            self.assertEqual(resolved["case_id"], "tech_high_electrification_pv_ev")
            self.assertTrue(resolved["belgian_technology_inputs_exists"])

    def test_undefined_technology_case_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            belgian_inputs = Path(tmp_dir) / "belgian_technology_inputs.yaml"
            belgian_inputs.write_text("technology_inputs: {}\n", encoding="utf-8")

            with self.assertRaisesRegex(TechnologyResolutionError, "Undefined technology case"):
                resolve_technology_inputs("tech_missing", self.technology_cases, belgian_inputs)

    def test_baseline_with_non_current_stock_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            belgian_inputs = Path(tmp_dir) / "belgian_technology_inputs.yaml"
            belgian_inputs.write_text("technology_inputs: {}\n", encoding="utf-8")

            with self.assertRaisesRegex(TechnologyResolutionError, "Baseline scenarios must use"):
                resolve_technology_inputs(
                    "tech_frozen_stock",
                    self.technology_cases,
                    belgian_inputs,
                    window_type="baseline",
                )

    def test_future_current_stock_fails_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            belgian_inputs = Path(tmp_dir) / "belgian_technology_inputs.yaml"
            belgian_inputs.write_text("technology_inputs: {}\n", encoding="utf-8")

            with self.assertRaisesRegex(TechnologyResolutionError, "must not use tech_current_stock"):
                resolve_technology_inputs(
                    "tech_current_stock",
                    self.technology_cases,
                    belgian_inputs,
                    window_type="future",
                )

    def test_belgian_technology_input_yaml_must_exist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            with self.assertRaisesRegex(TechnologyResolutionError, "Missing Belgian technology input YAML"):
                resolve_technology_inputs(
                    "tech_current_stock",
                    self.technology_cases,
                    Path(tmp_dir) / "missing.yaml",
                    window_type="baseline",
                )


if __name__ == "__main__":
    unittest.main()
