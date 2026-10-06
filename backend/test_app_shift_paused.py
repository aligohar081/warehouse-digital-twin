"""The distribution-centre floor runs only the work someone assigns (spec
§11.5, plan 1c ruling 6).

The app boots the new floor with its shift paused, as the classic floor runs
only the tasks it is given: no trucks, customer orders, counts or patrols
appear until someone starts the shift. A reset puts the shift back paused,
even one that had been started.
"""
from __future__ import annotations

import pytest

from backend import app as app_module
from backend.app import build_twin, create_app
from backend.operations.shift import ShiftEngine
from backend.simulator import Simulator

#: 300 sim-seconds at TICK_DT 0.15 s: on a running shift every stream but
#: departures would have fired by then (they first fire 10–150 s in).
TICKS = 2000


def test_the_new_floor_creates_no_work_of_its_own(tmp_path):
    twin = build_twin("distribution_center", str(tmp_path))
    simulator = Simulator(twin)
    for _ in range(TICKS):
        simulator.tick()
    assert twin.shift.status == ShiftEngine.PAUSED
    assert twin.tasks.tasks == {}
    assert twin.shift.orders.orders == {}


@pytest.mark.parametrize("route", ["/api/simulation/reset", "/api/state/reset"])
def test_a_reset_pauses_a_shift_someone_started(tmp_path, monkeypatch, route):
    monkeypatch.setattr(app_module, "BASE_DIR", str(tmp_path))
    app, twin, _sim, _ci = create_app(layout="distribution_center", autostart=False, run_thread=False)
    app.config["TESTING"] = True
    twin.shift.start()
    response = app.test_client().post(route, json={})
    assert response.status_code == 200
    assert twin.shift.status == ShiftEngine.PAUSED
