"""Systems module boundary for future HVAC, DHW, and electrical logic."""

from __future__ import annotations

from model_v3.interfaces import ControlState, SystemState
from model_v3.systems.system_core import run_systems


def run(control_state: ControlState, config: object | None = None) -> SystemState:
    """Compatibility wrapper for the Phase 2 systems core."""

    _ = config
    return run_systems(control_state=control_state)
