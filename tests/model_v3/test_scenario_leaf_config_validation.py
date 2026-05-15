"""Tests for independent scenario-leaf config validation."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
MODEL_V3_SRC = REPO_ROOT / "src"
if str(MODEL_V3_SRC) not in sys.path:
    sys.path.insert(0, str(MODEL_V3_SRC))

from model_v3.scenario_tree.create_scenario_tree_space import create_experiment_space  # noqa: E402
from model_v3.scenario_tree.generate_leaf_configs import generate_leaf_configs, load_leaf_index  # noqa: E402
from model_v3.scenario_tree.validate_leaf_configs import validate_leaf_configs  # noqa: E402


CONFIG_ROOT = REPO_ROOT / "config" / "scenario_tree"


def _write_csv(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("timestamp,T_out_C,I_solar_W_m2\n", encoding="utf-8")


def _build_valid_limited_space(tmp_dir: str) -> tuple[Path, Path, Path]:
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


class ScenarioLeafConfigValidationTest(unittest.TestCase):
    def test_independent_validator_passes_generated_configs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            experiment_root, climate_root, belgian_inputs = _build_valid_limited_space(tmp_dir)

            summary = validate_leaf_configs(
                experiment_root=experiment_root,
                config_root=CONFIG_ROOT,
                climate_processed_root=climate_root,
                belgian_technology_inputs=belgian_inputs,
            )

            self.assertEqual(summary["scenario_leaves_checked"], 101)
            self.assertEqual(summary["run_configs_found"], 101)
            self.assertEqual(summary["missing_climate_files"], 0)
            self.assertEqual(summary["undefined_technology_cases"], 0)
            self.assertEqual(summary["missing_belgian_technology_inputs"], 0)
            self.assertEqual(summary["baseline_configs"], 100)
            self.assertEqual(summary["future_configs"], 1)
            self.assertEqual(summary["simulations_run"], 0)

    def test_duplicate_config_paths_are_not_created_by_generation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            experiment_root, _, _ = _build_valid_limited_space(tmp_dir)
            rows = load_leaf_index(experiment_root)
            config_paths = [row["run_config_path"] for row in rows]

            self.assertEqual(len(config_paths), len(set(config_paths)))


if __name__ == "__main__":
    unittest.main()
