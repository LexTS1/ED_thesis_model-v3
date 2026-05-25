from __future__ import annotations

from model_v3.control.control_core import run_control
from model_v3.interfaces import PhysicsState


def test_control_uses_physics_heating_demand_when_heat_is_on() -> None:
    state = PhysicsState(
        T_indoor_prev_C=19.0,
        T_indoor_free_float_C=17.0,
        T_set_C=21.0,
        Q_heating_demand_W=4200.0,
        heat_loss_coefficient_W_per_C=180.0,
        metadata={
            "modules": {"control": True},
            "model_cfg": {"initial_heating_on": False},
            "control_cfg": {"deadband": 0.75},
        },
    )

    control = run_control(state)

    assert control.heating_on is True
    assert control.Q_heating_requested_W == 4200.0

