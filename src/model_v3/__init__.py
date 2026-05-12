"""Minimal package scaffold for the v2 modelling architecture."""

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ARTIFACT_ROOT = _REPO_ROOT / "model_v3"
for _extra_path in (_REPO_ROOT, _ARTIFACT_ROOT):
    if _extra_path.exists():
        _extra_path_text = str(_extra_path)
        if _extra_path_text not in __path__:
            __path__.append(_extra_path_text)

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
