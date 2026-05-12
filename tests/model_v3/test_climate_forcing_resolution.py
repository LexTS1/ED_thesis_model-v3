"""Tests for scenario-tree climate forcing resolution."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
MODEL_V3_SRC = REPO_ROOT / "src"
if str(MODEL_V3_SRC) not in sys.path:
    sys.path.insert(0, str(MODEL_V3_SRC))

from model_v3.scenario_tree.climate_forcing import (  # noqa: E402
    ClimateForcingResolutionError,
    get_climate_window,
    resolve_climate_forcing,
)
from model_v3.scenario_tree.validate_scenario_tree import validate_scenario_tree  # noqa: E402


CONFIG_ROOT = REPO_ROOT / "config" / "model_v3" / "scenario_tree"


def _write_csv(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("timestamp,T_out_C,I_solar_W_m2\n", encoding="utf-8")


class ClimateForcingResolutionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.result = validate_scenario_tree(CONFIG_ROOT)
        self.windows = self.result.metadata.climate_windows

    def test_baseline_resolves_to_historical_baseline_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            _write_csv(root / "baseline" / "weather_baseline_historical_1981_2005.csv")

            resolved = resolve_climate_forcing("baseline_1981_2005", "historical", self.windows, root)

            self.assertIn("historical", resolved.name)

    def test_future_rcp_files_resolve_by_pathway(self) -> None:
        cases = [
            ("rcp_2_6", "weather_near_future_rcp_2_6_2030_2050.csv"),
            ("rcp_4_5", "weather_near_future_rcp_4_5_2030_2050.csv"),
            ("rcp_8_5", "weather_near_future_rcp_8_5_2030_2050.csv"),
        ]
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            for _, filename in cases:
                _write_csv(root / "near_future" / filename)

            for pathway, filename in cases:
                resolved = resolve_climate_forcing("near_future_2030_2049", pathway, self.windows, root)
                self.assertEqual(resolved.name, filename)

    def test_missing_climate_file_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            with self.assertRaisesRegex(ClimateForcingResolutionError, "No processed climate forcing CSV matched"):
                resolve_climate_forcing("near_future_2030_2049", "rcp_2_6", self.windows, Path(tmp_dir))

    def test_ambiguous_climate_file_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            _write_csv(root / "near_future" / "weather_near_future_rcp_2_6_2030_2050.csv")
            _write_csv(root / "near_future" / "copy_near_future_rcp_2_6_2030_2050.csv")

            with self.assertRaisesRegex(ClimateForcingResolutionError, "Ambiguous processed climate forcing"):
                resolve_climate_forcing("near_future_2030_2049", "rcp_2_6", self.windows, root)

    def test_canonical_2050_policy_is_preserved(self) -> None:
        near = get_climate_window("near_future_2030_2049", self.windows)
        mid = get_climate_window("mid_century_2050_2070", self.windows)

        self.assertEqual(near["canonical_end"], "2049-12-31")
        self.assertEqual(mid["canonical_start"], "2050-01-01")


if __name__ == "__main__":
    unittest.main()
