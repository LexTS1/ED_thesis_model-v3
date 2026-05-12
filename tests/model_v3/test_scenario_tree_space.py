"""Tests for scenario-tree experiment-space generation helpers."""

from __future__ import annotations

import csv
import sys
import tempfile
import unittest
from collections import Counter
from datetime import date, datetime
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
MODEL_V3_SRC = REPO_ROOT / "src"
if str(MODEL_V3_SRC) not in sys.path:
    sys.path.insert(0, str(MODEL_V3_SRC))

from model_v3.scenario_tree import manifest, paths  # noqa: E402
from model_v3.scenario_tree.create_scenario_tree_space import create_experiment_space  # noqa: E402
from model_v3.scenario_tree.validate_scenario_tree import validate_scenario_tree  # noqa: E402


CONFIG_ROOT = REPO_ROOT / "config" / "model_v3" / "scenario_tree"


def _parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


class ScenarioTreeSpaceTest(unittest.TestCase):
    def test_generated_leaf_ids_are_unique(self) -> None:
        result = validate_scenario_tree(CONFIG_ROOT)
        leaf_ids = [leaf.scenario_leaf_id for leaf in result.scenario_leaves]

        self.assertEqual(len(leaf_ids), len(set(leaf_ids)))

    def test_generated_scenario_ids_are_unique_at_scenario_level(self) -> None:
        result = validate_scenario_tree(CONFIG_ROOT)
        scenario_counts = Counter(leaf.scenario_id for leaf in result.scenario_leaves)

        self.assertEqual(len(scenario_counts), 28)
        self.assertTrue(all(count == len(result.realization_ids) for count in scenario_counts.values()))

    def test_manifest_row_count_equals_enumerated_leaf_count(self) -> None:
        result = validate_scenario_tree(CONFIG_ROOT)
        rows = manifest.leaf_index_rows(
            result.scenario_leaves,
            Path("model_v3/experiments/scenario_tree"),
            Path("config/model_v3/scenario_tree"),
        )

        self.assertEqual(len(rows), len(result.scenario_leaves))

    def test_every_scenario_leaf_has_deterministic_paths(self) -> None:
        result = validate_scenario_tree(CONFIG_ROOT)
        experiment_root = Path("model_v3/experiments/scenario_tree")

        for leaf in result.scenario_leaves:
            self.assertEqual(paths.run_dir(experiment_root, leaf.scenario_leaf_id).name, leaf.scenario_leaf_id)
            self.assertEqual(paths.run_config_path(experiment_root, leaf.scenario_leaf_id).name, "run_config.yaml")
            self.assertEqual(
                paths.inputs_manifest_path(experiment_root, leaf.scenario_leaf_id).name,
                "inputs_manifest.yaml",
            )

    def test_baseline_leaves_use_historical_and_future_leaves_use_rcp(self) -> None:
        result = validate_scenario_tree(CONFIG_ROOT)

        for leaf in result.scenario_leaves:
            if leaf.climate_window_id == "baseline_1981_2005":
                self.assertEqual(leaf.climate_pathway_id, "historical")
            else:
                self.assertRegex(leaf.climate_pathway_id, r"^rcp_[0-9]_[0-9]$")

    def test_2050_canonical_window_policy_is_unchanged(self) -> None:
        result = validate_scenario_tree(CONFIG_ROOT)
        windows = result.metadata.climate_windows["climate_windows"]
        near = windows["near_future_2030_2049"]
        mid = windows["mid_century_2050_2070"]
        near_start = _parse_date(near["canonical_start"])
        near_end = _parse_date(near["canonical_end"])
        mid_start = _parse_date(mid["canonical_start"])
        mid_end = _parse_date(mid["canonical_end"])

        self.assertFalse(near_start <= date(2050, 1, 1) <= near_end)
        self.assertFalse(near_start <= date(2050, 12, 31) <= near_end)
        self.assertTrue(mid_start <= date(2050, 1, 1) <= mid_end)
        self.assertTrue(mid_start <= date(2050, 12, 31) <= mid_end)

    def test_create_space_smoke_writes_limited_placeholders_and_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            experiment_root = Path(tmp_dir) / "scenario_tree"
            summary = create_experiment_space(
                config_root=CONFIG_ROOT,
                experiment_root=experiment_root,
                write_manifest=True,
                dry_run=False,
                overwrite_placeholder_configs=False,
                max_leaves=3,
            )
            manifest_path = summary["manifest_path"]
            index_path = summary["index_path"]

            self.assertTrue(manifest_path.exists())
            self.assertTrue(index_path.exists())
            with index_path.open("r", encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 3)
            for row in rows:
                self.assertTrue(Path(row["run_config_path"]).exists())
                self.assertTrue(Path(row["inputs_manifest_path"]).exists())
                self.assertTrue(Path(row["outputs_dir"]).is_dir())
                self.assertTrue(Path(row["logs_dir"]).is_dir())


if __name__ == "__main__":
    unittest.main()
