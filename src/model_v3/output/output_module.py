"""Output boundary for formatting and exporting model_v3 results."""

from __future__ import annotations

from model_v3.interfaces import ModelOutputs, SystemState
from model_v3.output.output_core import assemble_outputs


def build_outputs(system_state: SystemState, config: object | None = None) -> ModelOutputs:
    """Compatibility wrapper for the Phase 2 output core."""

    _ = config
    return assemble_outputs(system_state=system_state)
