"""Tests for the model_v3 scenario-tree metadata contract."""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
MODEL_V3_SRC = REPO_ROOT / "src"
if str(MODEL_V3_SRC) not in sys.path:
    sys.path.insert(0, str(MODEL_V3_SRC))

from model_v3.scenario_tree.validate_scenario_tree import (  # noqa: E402
    REQUIRED_RCP_PATHWAYS,
    ScenarioTreeValidationError,
    validate_scenario_tree,
)


CONFIG_ROOT = REPO_ROOT / "config" / "model_v3" / "scenario_tree"


def _parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


class ScenarioTreeValidationTest(unittest.TestCase):
    """Validate committed scenario-tree metadata and core failure modes."""

    def test_validation_passes_with_committed_yaml(self) -> None:
        result = validate_scenario_tree(CONFIG_ROOT)

        self.assertEqual(len(result.scenario_leaves), 2800)
        self.assertEqual(result.year_2050_assignment, "mid_century_2050_2070")

    def test_duplicate_generated_leaf_ids_are_impossible(self) -> None:
        result = validate_scenario_tree(CONFIG_ROOT)
        leaf_ids = [leaf.scenario_leaf_id for leaf in result.scenario_leaves]

        self.assertEqual(len(leaf_ids), len(set(leaf_ids)))

    def test_baseline_does_not_accept_rcp_pathways(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            temp_config = Path(tmp_dir) / "scenario_tree"
            shutil.copytree(CONFIG_ROOT, temp_config)
            climate_path = temp_config / "climate_windows.yaml"
            data = yaml.safe_load(climate_path.read_text(encoding="utf-8"))
            data["climate_windows"]["baseline_1981_2005"]["allowed_pathways"] = [
                "historical",
                "rcp_8_5",
            ]
            climate_path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

            with self.assertRaisesRegex(ScenarioTreeValidationError, "baseline window must allow only historical"):
                validate_scenario_tree(temp_config)

    def test_future_windows_include_required_rcp_pathways(self) -> None:
        result = validate_scenario_tree(CONFIG_ROOT)
        windows = result.metadata.climate_windows["climate_windows"]

        for window in windows.values():
            if window["window_type"] == "future":
                self.assertTrue(set(REQUIRED_RCP_PATHWAYS).issubset(window["allowed_pathways"]))
                self.assertNotIn("historical", window["allowed_pathways"])

    def test_near_future_canonical_window_excludes_2050(self) -> None:
        result = validate_scenario_tree(CONFIG_ROOT)
        near_future = result.metadata.climate_windows["climate_windows"]["near_future_2030_2049"]
        start = _parse_date(near_future["canonical_start"])
        end = _parse_date(near_future["canonical_end"])

        self.assertFalse(start <= date(2050, 1, 1) <= end)
        self.assertFalse(start <= date(2050, 12, 31) <= end)

    def test_mid_century_canonical_window_includes_2050(self) -> None:
        result = validate_scenario_tree(CONFIG_ROOT)
        mid_century = result.metadata.climate_windows["climate_windows"]["mid_century_2050_2070"]
        start = _parse_date(mid_century["canonical_start"])
        end = _parse_date(mid_century["canonical_end"])

        self.assertTrue(start <= date(2050, 1, 1) <= end)
        self.assertTrue(start <= date(2050, 12, 31) <= end)
