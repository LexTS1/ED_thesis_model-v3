"""Minimal heating system model for model_v3 Phase 2."""

from __future__ import annotations

def dispatch_heating(
    Q_heating_requested_W: float,
    Q_heating_max_W: float,
    heating_cop: float = 1.0,
) -> dict[str, float]:
    """Apply a hard capacity limit and convert supplied heat to electricity."""

    heating_cop = max(float(heating_cop), 1e-9)
    Q_heating_supplied_W = min(max(Q_heating_requested_W, 0.0), max(Q_heating_max_W, 0.0))
    Q_unmet_heating_W = max(Q_heating_requested_W - Q_heating_supplied_W, 0.0)
    P_el_space_heating_W = Q_heating_supplied_W / heating_cop

    return {
        "Q_heating_supplied_W": Q_heating_supplied_W,
        "Q_unmet_heating_W": Q_unmet_heating_W,
        "P_el_space_heating_W": P_el_space_heating_W,
    }
