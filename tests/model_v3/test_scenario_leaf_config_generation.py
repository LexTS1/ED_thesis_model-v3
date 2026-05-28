"""Tests for scenario-leaf executable config generation."""

from __future__ import annotations

import csv
import sys
import tempfile
import unittest
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
MODEL_V3_SRC = REPO_ROOT / "src"
if str(MODEL_V3_SRC) not in sys.path:
    sys.path.insert(0, str(MODEL_V3_SRC))

from model_v3.scenario_tree import paths  # noqa: E402
from model_v3.scenario_tree.create_scenario_tree_space import create_experiment_space  # noqa: E402
from model_v3.scenario_tree.generate_leaf_configs import generate_leaf_configs  # noqa: E402


CONFIG_ROOT = REPO_ROOT / "config" / "scenario_tree"


def _write_csv(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("timestamp,T_out_C,I_solar_W_m2\n", encoding="utf-8")


def _build_limited_config_space(tmp_dir: str) -> tuple[Path, Path, Path]:
    root = Path(tmp_dir)
    experiment_root = root / "scenario_tree"
    climate_root = root / "climate_processed"
    belgian_inputs = root / "belgian_technology_inputs.yaml"
    belgian_inputs.write_text("technology_inputs: {}\n", encoding="utf-8")
    _write_csv(climate_root / "baseline" / "weather_baseline_historical_1981_2005.csv")
    _write_csv(climate_root / "near_future" / "weather_near_future_rcp_2_6_2030_2050.csv")
    create_experiment_space(
        config_root=CONFIG_ROOT,
        experiment_root=experiment_root,
        write_manifest=True,
        dry_run=False,
        overwrite_placeholder_configs=False,
        max_leaves=101,
    )
    generate_leaf_configs(
        config_root=CONFIG_ROOT,
        experiment_root=experiment_root,
        climate_processed_root=climate_root,
        belgian_technology_inputs=belgian_inputs,
        cohort_size=100,
        write_report=True,
        dry_run=False,
        overwrite=False,
        max_leaves=None,
        allow_missing_climate=False,
        allow_missing_technology_inputs=False,
    )
    return experiment_root, climate_root, belgian_inputs


class ScenarioLeafConfigGenerationTest(unittest.TestCase):
    def test_every_index_leaf_gets_one_run_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            experiment_root, _, _ = _build_limited_config_space(tmp_dir)
            index_path = paths.get_manifest_dir(experiment_root) / "scenario_leaf_index.csv"
            with index_path.open("r", encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))

            self.assertEqual(len(rows), 101)
            for row in rows:
                run_config = Path(row["run_config_path"])
                self.assertTrue(run_config.exists())
                self.assertEqual(run_config.name, "run_config.yaml")

    def test_generated_config_is_executable_but_not_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            experiment_root, _, _ = _build_limited_config_space(tmp_dir)
            leaf_id = "baseline_1981_2005__historical__tech_current_stock__seed_0000"
            config_path = paths.run_config_path(experiment_root, leaf_id)
            config = yaml.safe_load(config_path.read_text(encoding="utf-8"))

            for section in (
                "scenario_leaf",
                "climate",
                "technology",
                "stochastic",
                "model_options",
                "output",
                "validation",
                "provenance",
            ):
                self.assertIn(section, config)
            self.assertEqual(config["status"], "configured_not_run")
            self.assertFalse(config["model_options"]["execute_simulation"])
            self.assertEqual(config["model_options"]["runner_mode"], "stock_weighted_archetypes")
            self.assertFalse(config["model_options"]["use_stochastic_cohort"])
            self.assertTrue(config["model_options"]["use_stock_weighted_archetypes"])
            self.assertEqual(config["scenario_leaf"]["id"], leaf_id)
            self.assertTrue(Path(config["output"]["outputs_dir"]).is_dir())
            self.assertTrue(Path(config["output"]["logs_dir"]).is_dir())

    def test_generated_config_path_is_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            experiment_root, _, _ = _build_limited_config_space(tmp_dir)
            leaf_id = "near_future_2030_2049__rcp_2_6__tech_frozen_stock__seed_0000"

            self.assertTrue(paths.run_config_path(experiment_root, leaf_id).exists())


if __name__ == "__main__":
    unittest.main()
