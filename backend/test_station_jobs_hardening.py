"""Hardening for the jobs people do (multi-embodiment spec §6, §9.2, §11.3):
a person's job that ends early (cancelled or failed) walks them back out
before they take another, a person on a walk is never offered a job, a job
that can't start fails instead of hanging in VALIDATING, and a human job that
raises fails alone without stopping the tick."""
import pytest

from backend import human_jobs, people
from backend.digital_twin import DigitalTwin
from backend.models import BoxStatus, OperatorStatus, SimulationStatus, TaskStatus, TaskType
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


def ticks(sim, n):
    for _ in range(n):
        sim.tick()


def until(sim, condition, max_ticks=3000):
    for _ in range(max_ticks):
        if condition():
            return
        sim.tick()
    assert condition()


def run(sim, *tasks, max_ticks=3000):
    until(sim, lambda: all(task.is_terminal for task in tasks), max_ticks)


def jam_jobs(twin):
    return [t for t in twin.tasks.tasks.values() if t.type is TaskType.CLEAR_JAM]


def running_jam_job(twin):
    return next(t for t in jam_jobs(twin) if not t.is_terminal)


def test_a_job_cancelled_mid_walk_is_asked_for_again_and_the_jam_cleared(twin, sim):
    twin.add_robot(name="CX10-210", asset_id="AST-000210")
    mateo = twin.add_operator(name="Mateo", worker_id="E-10004")
    people.place(twin, mateo, "workshop")
    twin.equipment.jam((23, 15))
    ticks(sim, 20)                                           # the jam asks for someone; he sets off
    first = running_jam_job(twin)
    ticks(sim, 2)
    assert mateo.zone == "workshop" and mateo.in_transit and first.params["phase"] == "WALK"
    twin.tasks.cancel_task(first.id)
    assert mateo.status is OperatorStatus.AVAILABLE and mateo.current_task is None and not mateo.in_transit
    ticks(sim, 25)                                           # the next ask must not trip over a half-finished walk
    second = running_jam_job(twin)
    assert second.id != first.id and second.operator_id == mateo.id and second.status is TaskStatus.IN_PROGRESS
    until(sim, lambda: (23, 15) not in twin.equipment.conveyor.jams)
    until(sim, lambda: second.is_terminal and mateo.is_available)
    assert second.status is TaskStatus.COMPLETED and mateo.zone == "workshop"


def test_cancelling_inside_a_pack_cell_walks_the_person_out_and_frees_the_arm(twin, sim):
    arm = twin.add_robot(name="CX10-210", asset_id="AST-000210")
    picker = twin.add_robot(name="PK30-203", asset_id="AST-000203", position=(20, 14))
    mateo = twin.add_operator(name="Mateo", worker_id="E-10004")
    people.place(twin, mateo, "workshop")
    twin.equipment.jam((23, 15))
    task = twin.tasks.create_task({"type": "CLEAR_JAM", "segment": "23,15", "operator_id": mateo.id})
    until(sim, lambda: task.params["phase"] == "WORK")
    assert people.people_in(twin, "pack_cell_1") == [mateo]
    twin.tasks.cancel_task(task.id)
    assert task.status is TaskStatus.CANCELLED
    # Still on the job's books and walking out, so no new job can take them yet.
    assert mateo.status is OperatorStatus.ON_TASK and mateo.current_task == task.id
    assert mateo.in_transit and mateo.transit_to == "workshop"
    assert people.people_in(twin, "pack_cell_1") == [] and sim._people_near(arm) == []
    until(sim, lambda: mateo.current_task != task.id)         # free on arrival (and maybe asked again that tick)
    assert mateo.zone == "workshop" and mateo.failed_tasks == 1
    moved = [e["data"] for e in twin.events.query(event_type="PERSON_MOVED") if e["data"]["operator_id"] == mateo.id]
    assert [(m["from"], m["to"]) for m in moved] == [("workshop", "pack_cell_1"), ("pack_cell_1", "workshop")]
    # The jam is still there: it is asked for again, cleared, and he walks back out again.
    until(sim, lambda: (23, 15) not in twin.equipment.conveyor.jams)
    until(sim, lambda: all(t.is_terminal for t in jam_jobs(twin)) and mateo.is_available)
    assert people.people_in(twin, "pack_cell_1") == [] and mateo.zone == "workshop"
    # The arm packs an order: nothing holds it in an empty cell.
    tote = twin.add_box(name="TOTE-1", kind="TOTE", sku="SKU-02", quantity=6, weight=7.5, slot="TS-10-12-1")
    tote.position = (19, 14)
    tote.set_status(BoxStatus.DELIVERED)
    pick = twin.tasks.create_task({"type": "PICK_ITEMS", "box_id": tote.id, "quantity": 1, "order_id": "ORD-1",
                                   "pack_cell": "pack_cell_1"})
    pack = twin.tasks.create_task({"type": "PACK_ORDER", "order_id": "ORD-1", "quantity": 1})
    run(sim, pick, pack)
    assert pick.status is TaskStatus.COMPLETED and pack.status is TaskStatus.COMPLETED, (pick.error, pack.error)
    assert (pick.robot_id, pack.robot_id) == (picker.id, arm.id)


def test_a_person_on_a_walk_is_not_offered_a_job(twin):
    noor = twin.add_operator(name="Noor", worker_id="E-10003")
    people.place(twin, noor, "workshop")
    people.start_transit(twin, noor, "pick_station_2")
    twin.equipment.jam((20, 15))
    named = twin.tasks.create_task({"type": "CLEAR_JAM", "segment": "20,15", "operator_id": noor.id})
    assert named.status is TaskStatus.FAILED and named.error == "Noor is walking to pick_station_2"
    auto = twin.tasks.create_task({"type": "CLEAR_JAM", "segment": "20,15"})
    assert auto.status is TaskStatus.FAILED and auto.error == "No eligible operator available to handle this task"
    assert noor.status is OperatorStatus.AVAILABLE and noor.current_task is None and noor.transit_to == "pick_station_2"


def test_a_person_walked_out_of_a_cell_stays_unavailable_until_they_arrive(twin, sim):
    mateo = twin.add_operator(name="Mateo", worker_id="E-10004")
    people.place(twin, mateo, "pack_cell_1")
    twin.equipment.jam((22, 15))                             # the other pack cell's working cell
    task = twin.tasks.create_task({"type": "CLEAR_JAM", "segment": "22,15", "operator_id": mateo.id})
    assert task.status is TaskStatus.IN_PROGRESS and mateo.transit_to == "pack_cell_2"
    assert task.params["origin_zone"] == "pick_station_1"    # out of the fence, never back into it
    twin.tasks.cancel_task(task.id)
    assert mateo.status is OperatorStatus.ON_TASK and mateo.current_task == task.id
    assert mateo.zone == "pack_cell_1" and mateo.transit_to == "pick_station_1"   # redirected, not abandoned
    refused = twin.tasks.create_task({"type": "CLEAR_JAM", "segment": "22,15", "operator_id": mateo.id})
    assert refused.error == "Mateo is walking to pick_station_1"
    until(sim, lambda: mateo.is_available)
    assert mateo.zone == "pick_station_1" and mateo.current_task is None and mateo.failed_tasks == 1


@pytest.mark.parametrize("pack_cell, cell, outside", [
    ("pack_cell_1", (23, 15), "pick_station_1"),
    ("pack_cell_2", (22, 15), "pick_station_2"),
])
def test_a_person_who_starts_inside_a_pack_cell_comes_out_to_the_station_beside_it(twin, sim, pack_cell, cell, outside):
    mateo = twin.add_operator(name="Mateo", worker_id="E-10004")
    people.place(twin, mateo, pack_cell)
    twin.equipment.jam(cell)
    task = twin.tasks.create_task({"type": "CLEAR_JAM", "segment": f"{cell[0]},{cell[1]}", "operator_id": mateo.id})
    assert task.params["origin_zone"] == outside
    run(sim, task)
    assert task.status is TaskStatus.COMPLETED, task.error
    until(sim, lambda: mateo.is_available)
    assert mateo.zone == outside and people.people_in(twin, pack_cell) == []


def test_a_job_that_fails_after_the_person_set_off_walks_them_back(twin, sim):
    noor = twin.add_operator(name="Noor", worker_id="E-10003")
    lee = twin.add_operator(name="Lee", worker_id="E-10002")
    people.place(twin, noor, "workshop")
    people.place(twin, lee, "workshop")
    twin.equipment.jam((20, 15))
    first = twin.tasks.create_task({"type": "CLEAR_JAM", "segment": "20,15", "operator_id": noor.id})
    second = twin.tasks.create_task({"type": "CLEAR_JAM", "segment": "20,15", "operator_id": lee.id})
    assert first.status is second.status is TaskStatus.IN_PROGRESS
    run(sim, first, second)
    assert first.status is TaskStatus.COMPLETED
    assert second.status is TaskStatus.FAILED and "could not finish" in second.error   # someone got there first
    until(sim, lambda: noor.is_available and lee.is_available)
    assert lee.zone == "workshop" and lee.failed_tasks == 1 and noor.zone == "workshop"


def test_a_job_that_cannot_start_fails_instead_of_hanging(twin, monkeypatch):
    noor = twin.add_operator(name="Noor", worker_id="E-10003")
    people.place(twin, noor, "workshop")
    twin.equipment.jam((20, 15))

    def cannot_walk(*_):
        raise ValueError("the doors are locked")

    monkeypatch.setattr(people, "start_transit", cannot_walk)
    task = twin.tasks.create_task({"type": "CLEAR_JAM", "segment": "20,15"})
    assert task.status is TaskStatus.FAILED and task.error == "Noor could not start: the doors are locked"
    assert noor.status is OperatorStatus.AVAILABLE and noor.current_task is None and not noor.in_transit
    assert noor.failed_tasks == 0                            # a job they never began isn't theirs to fail


def test_a_human_job_that_raises_fails_alone_and_the_tick_goes_on(twin, sim):
    lee = twin.add_operator(name="Lee", worker_id="E-10002")
    noor = twin.add_operator(name="Noor", worker_id="E-10003")
    people.place(twin, lee, "pick_station_2")
    people.place(twin, noor, "workshop")
    tote = twin.add_box(name="TOTE-1", kind="TOTE", sku="SKU-02", quantity=6, weight=7.5, slot="TS-10-12-1")
    tote.position = (19, 16)
    tote.set_status(BoxStatus.DELIVERED)
    twin.equipment.jam((20, 15))
    pick = twin.tasks.create_task({"type": "MANUAL_PICK", "box_id": tote.id, "quantity": 2})
    jam = twin.tasks.create_task({"type": "CLEAR_JAM", "segment": "20,15"})
    assert pick.operator_id == lee.id and jam.operator_id == noor.id
    del twin.boxes[tote.id]                                  # the tote vanishes: taking a unit raises AttributeError
    until(sim, lambda: pick.is_terminal)                     # no exception reaches the caller of tick()
    assert pick.status is TaskStatus.FAILED and pick.error.startswith("Controller error:")
    assert lee.status is OperatorStatus.AVAILABLE and lee.current_task is None
    sim.tick()                                               # and the next tick runs
    run(sim, jam)
    assert jam.status is TaskStatus.COMPLETED and (20, 15) not in twin.equipment.conveyor.jams


def test_a_jam_ask_that_raises_does_not_stop_the_tick(twin, sim, monkeypatch):
    twin.equipment.jam((20, 15))

    def broken(*_args, **_kwargs):
        raise RuntimeError("the task list is unavailable")

    monkeypatch.setattr(twin.tasks, "create_task", broken)
    ticks(sim, 45)                                           # two asks, both swallowed
    assert human_jobs.ensure_jam_jobs(twin) == []
