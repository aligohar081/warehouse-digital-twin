"""Hardening for the multi-embodiment evaluation checks: a box that left on the
outbound truck (SHIPPED) is no longer listed in its destination dock's zone
snapshot, so state_transition must not demand that it still is — while a box
that did not ship, or ended in the wrong zone, still fails it."""
import copy

import pytest

from backend.digital_twin import DigitalTwin
from backend.eval_engine import Verdict, evaluate_events
from backend.models import BoxStatus, SimulationStatus, TaskStatus
from backend.simulator import Simulator


@pytest.fixture
def twin(tmp_path):
    return DigitalTwin(log_dir=str(tmp_path / "logs"), data_dir=str(tmp_path / "data"),
                       persist_logs=False, demo=True, demo_tasks=False, layout="distribution_center")


@pytest.fixture
def sim(twin):
    simulator = Simulator(twin)
    twin.simulation_status = SimulationStatus.RUNNING
    return simulator


def run(sim, task, max_ticks=3000):
    for _ in range(max_ticks):
        if task.is_terminal:
            return
        sim.tick()
    assert task.is_terminal, task.status


def events_of(twin, task):
    return twin.events.query(task_id=task.id, limit=5000)


def state_transition(events, task_id):
    report = evaluate_events(events, task_id=task_id)
    return report, next(check for check in report.checks if check.name == "state_transition")


@pytest.fixture
def loaded(twin, sim):
    """A pallet retrieved from its rack slot and loaded onto the outbound
    truck (the sequence in test_pallet_jobs.py): returns the LOAD_TRUCK task."""
    twin.add_robot(name="PF1200-205", asset_id="AST-000205", position=(7, 4))
    pallet = twin.add_box(name="PAL-1", kind="PALLET", sku="SKU-01", quantity=40, weight=600.0, slot="PR-10-05-2")
    retrieve = twin.tasks.create_task({"type": "RETRIEVE_PALLET", "box_id": pallet.id})
    run(sim, retrieve)
    assert retrieve.status is TaskStatus.COMPLETED, retrieve.error
    load = twin.tasks.create_task({"type": "LOAD_TRUCK", "box_id": pallet.id})
    assert load.destination == "dock_3"
    run(sim, load)
    assert load.status is TaskStatus.COMPLETED and pallet.status is BoxStatus.SHIPPED
    return load


def test_a_shipped_load_truck_job_passes_state_transition(twin, loaded):
    terminal = twin.events.query(task_id=loaded.id, event_type="TASK_COMPLETED")[0]["data"]["state"]
    # The premise: it left with the truck, so its dock no longer lists it ...
    assert terminal["box"]["status"] == "SHIPPED" and terminal["box"]["zone"] == "dock_3"
    assert terminal["destination_zone"]["key"] == "dock_3" and terminal["destination_zone"]["boxes_present"] == []
    # ... and that is a clean delivery, not a failed one.
    report, check = state_transition(events_of(twin, loaded), loaded.id)
    assert check.verdict is Verdict.PASS, check.message
    assert report.verdict is Verdict.PASS, report.reasons


def _completed(events):
    return next(e for e in events if e["event"] == "TASK_COMPLETED")["data"]["state"]


def test_a_box_that_did_not_ship_and_is_missing_from_its_destination_zone_still_fails(twin, loaded):
    events = copy.deepcopy(events_of(twin, loaded))
    state = _completed(events)
    state["box"]["status"] = "DELIVERED"          # in dock_3, but never shipped and not listed there
    report, check = state_transition(events, loaded.id)
    assert check.verdict is Verdict.FAIL
    assert "isn't listed in destination zone 'dock_3' after delivery" in check.message
    assert report.verdict is Verdict.FAIL


def test_a_shipped_box_in_the_wrong_zone_still_fails(twin, loaded):
    events = copy.deepcopy(events_of(twin, loaded))
    _completed(events)["box"]["zone"] = "outbound_staging"   # SHIPPED, but not where the task sent it
    _, check = state_transition(events, loaded.id)
    assert check.verdict is Verdict.FAIL
    assert "ended in zone 'outbound_staging', not the intended destination 'dock_3'" in check.message
