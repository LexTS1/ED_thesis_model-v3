"""Stochastic stage for model_v3."""

from model_v3.stochastic.dhw_generator import generate_dhw_events
from model_v3.stochastic.sampler import sample_household_parameters

__all__ = ["generate_dhw_events", "sample_household_parameters"]
