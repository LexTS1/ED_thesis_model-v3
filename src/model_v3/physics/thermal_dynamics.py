"""Reusable lumped-zone thermal integration helpers for model_v3."""

from __future__ import annotations

import math


def compute_stability_metadata(
    total_loss_w_per_k: float,
    c_j_per_k: float,
    dt_seconds: float,
    max_stability_factor: float = 0.5,
) -> dict[str, float | int]:
    """Return stability metadata and the required explicit substep count."""

    total_loss = max(float(total_loss_w_per_k), 0.0)
    capacitance = max(float(c_j_per_k), 1e-9)
    dt = max(float(dt_seconds), 1e-9)
    limit = max(float(max_stability_factor), 1e-9)
    stability_factor = total_loss * dt / capacitance
    substeps = max(1, int(math.ceil(stability_factor / limit)))
    substep_dt_seconds = dt / substeps
    substep_stability_factor = total_loss * substep_dt_seconds / capacitance
    return {
        "stability_factor": stability_factor,
        "substeps": substeps,
        "substep_dt_seconds": substep_dt_seconds,
        "substep_stability_factor": substep_stability_factor,
    }


def integrate_zone_temperature(
    t_initial_c: float,
    t_outdoor_c: float,
    envelope_loss_w_per_k: float,
    airflow_loss_w_per_k: float,
    c_j_per_k: float,
    dt_seconds: float,
    q_internal_gains_w: float = 0.0,
    q_solar_gains_w: float = 0.0,
    q_heating_w: float = 0.0,
    max_stability_factor: float = 0.5,
) -> dict[str, float | int]:
    """Integrate a single lumped thermal zone with adaptive explicit substeps."""

    stability = compute_stability_metadata(
        total_loss_w_per_k=float(envelope_loss_w_per_k) + float(airflow_loss_w_per_k),
        c_j_per_k=c_j_per_k,
        dt_seconds=dt_seconds,
        max_stability_factor=max_stability_factor,
    )
    substep_dt_seconds = float(stability["substep_dt_seconds"])
    t_indoor_c = float(t_initial_c)
    total_loss = max(float(envelope_loss_w_per_k), 0.0) + max(float(airflow_loss_w_per_k), 0.0)
    q_constant_w = float(q_internal_gains_w) + float(q_solar_gains_w) + float(q_heating_w)
    capacitance = max(float(c_j_per_k), 1e-9)

    for _ in range(int(stability["substeps"])):
        q_loss_w = total_loss * (t_indoor_c - float(t_outdoor_c))
        t_indoor_c += (substep_dt_seconds / capacitance) * (q_constant_w - q_loss_w)

    return {
        "t_next_c": t_indoor_c,
        "substeps": int(stability["substeps"]),
        "stability_factor": float(stability["stability_factor"]),
        "substep_stability_factor": float(stability["substep_stability_factor"]),
        "substep_dt_seconds": substep_dt_seconds,
    }
