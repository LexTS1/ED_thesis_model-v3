"""Physics module boundary for future thermal and energy balance logic."""

from __future__ import annotations

from model_v3.interfaces import PhysicsState, PreparedForcing
from model_v3.physics.physics_core import run_physics


def run(forcing: PreparedForcing, config: object | None = None) -> PhysicsState:
    """Compatibility wrapper for the Phase 2 physics core."""

    _ = config
    return run_physics(prepared_forcing=forcing)
