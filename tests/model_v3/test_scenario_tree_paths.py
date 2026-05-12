"""Tests for deterministic scenario-tree path resolution."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
MODEL_V3_SRC = REPO_ROOT / "src"
if str(MODEL_V3_SRC) not in sys.path:
    sys.path.insert(0, str(MODEL_V3_SRC))

from model_v3.scenario_tree import paths  # noqa: E402
from model_v3.scenario_tree.naming import ScenarioTreeNamingError  # noqa: E402


EXPERIMENT_ROOT = Path("model_v3/experiments/scenario_tree")
SCENARIO_ID = "mid_century_2050_2070__rcp_8_5__tech_high_electrification_pv_ev"
SCENARIO_LEAF_ID = f"{SCENARIO_ID}__seed_0042"


class ScenarioTreePathsTest(unittest.TestCase):
    def test_same_scenario_leaf_id_resolves_to_same_path(self) -> None:
        self.assertEqual(
            paths.run_dir(EXPERIMENT_ROOT, SCENARIO_LEAF_ID),
            paths.run_dir(EXPERIMENT_ROOT, SCENARIO_LEAF_ID),
        )

    def test_run_path_uses_full_scenario_leaf_id(self) -> None:
        self.assertEqual(paths.run_dir(EXPERIMENT_ROOT, SCENARIO_LEAF_ID).name, SCENARIO_LEAF_ID)

    def test_scenario_config_path_uses_only_scenario_id(self) -> None:
        self.assertEqual(paths.scenario_config_dir(EXPERIMENT_ROOT, SCENARIO_ID).name, SCENARIO_ID)
        self.assertNotIn("seed_0042", paths.scenario_config_dir(EXPERIMENT_ROOT, SCENARIO_ID).as_posix())

    def test_path_resolver_rejects_invalid_ids(self) -> None:
        with self.assertRaises(ScenarioTreeNamingError):
            paths.run_dir(EXPERIMENT_ROOT, "Mid_Century__rcp_8_5__tech_high_electrification_pv_ev__seed_0042")

    def test_paths_contain_no_spaces_or_uppercase_characters(self) -> None:
        resolved = paths.paths_for_leaf(EXPERIMENT_ROOT, SCENARIO_LEAF_ID)

        for path in resolved.values():
            path_text = path.as_posix()
            self.assertEqual(path_text, path_text.lower())
            self.assertNotIn(" ", path_text)


if __name__ == "__main__":
    unittest.main()
