"""Importable package for the model_v3 energy-demand model."""

from .utils.matplotlib_config import ensure_writable_matplotlib_config_dir

ensure_writable_matplotlib_config_dir()

from .interfaces import (
    ControlState,
    InputDataset,
    ModelOutputs,
    PhysicsState,
    PreparedForcing,
    SystemState,
    TimeSeriesData,
)

__all__ = [
    "ControlState",
    "InputDataset",
    "ModelOutputs",
    "PhysicsState",
    "PreparedForcing",
    "SystemState",
    "TimeSeriesData",
    "ensure_writable_matplotlib_config_dir",
]
