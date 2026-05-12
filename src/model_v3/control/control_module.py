"""Control module boundary for future thermostat and constraint logic."""

from __future__ import annotations

from model_v3.interfaces import ControlState, PhysicsState
from model_v3.control.control_core import run_control


def run(physics_state: PhysicsState, config: object | None = None) -> ControlState:
    """Compatibility wrapper for the Phase 2 control core."""

    _ = config
    return run_control(physics_state=physics_state)
