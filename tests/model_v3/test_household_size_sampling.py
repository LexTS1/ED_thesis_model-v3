"""Tests for stochastic household-size sampling."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np


REPO_ROOT = Path(__file__).resolve().parents[2]
MODEL_V3_SRC = REPO_ROOT / "src"
if str(MODEL_V3_SRC) not in sys.path:
    sys.path.insert(0, str(MODEL_V3_SRC))

from model_v3.stochastic.sampler import (  # noqa: E402
    DEFAULT_HOUSEHOLD_SIZE_PROBABILITIES,
    sample_household_parameters,
    sample_occupants_per_dwelling,
)


class HouseholdSizeSamplingTest(unittest.TestCase):
    def test_default_household_size_distribution_is_normalized_after_sampling(self) -> None:
        rng = np.random.default_rng(123)
        draws = [sample_occupants_per_dwelling({}, rng) for _ in range(20_000)]

        self.assertGreaterEqual(min(draws), 1)
        self.assertLessEqual(max(draws), 7)
        self.assertAlmostEqual(float(np.mean(draws)), 2.25, delta=0.05)

    def test_sampled_household_parameters_include_occupants_and_activity_scale(self) -> None:
        rng = np.random.default_rng(42)
        sample = sample_household_parameters(config={}, rng=rng)
        behaviour = sample["behaviour"]

        self.assertIn("occupants_per_dwelling", behaviour)
        self.assertIn("household_size_activity_scale", behaviour)
        self.assertGreaterEqual(behaviour["occupants_per_dwelling"], 1)
        self.assertLessEqual(behaviour["occupants_per_dwelling"], 7)
        self.assertGreater(behaviour["household_size_activity_scale"], 0.0)

    def test_custom_distribution_can_force_single_size(self) -> None:
        rng = np.random.default_rng(1)
        behaviour_cfg = {"household_size_probabilities": {4: 1.0}}

        self.assertEqual(sample_occupants_per_dwelling(behaviour_cfg, rng), 4)

    def test_default_probability_mass_matches_thesis_working_distribution(self) -> None:
        self.assertAlmostEqual(sum(DEFAULT_HOUSEHOLD_SIZE_PROBABILITIES.values()), 0.995)
        self.assertEqual(set(DEFAULT_HOUSEHOLD_SIZE_PROBABILITIES), {1, 2, 3, 4, 5, 6, 7})


if __name__ == "__main__":
    unittest.main()
