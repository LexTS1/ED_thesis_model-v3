"""Timeline and semantic adapter entrypoints for model_v3."""

from __future__ import annotations

from model_v3.adapters.forcing_builder import build_prepared_forcing
from model_v3.interfaces import InputDataset, PreparedForcing


def prepare_forcing(dataset: InputDataset, config: object | None = None) -> PreparedForcing:
    """Compatibility wrapper for the Phase 2 forcing builder."""

    _ = config
    return build_prepared_forcing(input_dataset=dataset)
