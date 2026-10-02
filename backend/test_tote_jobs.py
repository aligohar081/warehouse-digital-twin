"""Tote jobs (multi-embodiment spec §9.2): a tote from its shelf slot to a
pick station and back again, and a returned tote into a free shelf slot."""
import pytest

from backend import people
from backend.digital_twin import DigitalTwin
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


@pytest.fixture
def amr(twin):
    return twin.add_robot(name="TR50-201", asset_id="AST-000201", position=(8, 13))


def run(sim, task, max_ticks=2000, each_tick=None):
    for _ in range(max_ticks):
        if task.is_terminal:
            return
        sim.tick()
        if each_tick:
            each_tick()
    assert task.is_terminal, f"{task.id} still {task.status.value}"


def steps(twin, task):
    return [e["data"]["step"] for e in twin.events.query(task_id=task.id, event_type="ROBOT_STEP")]


def tote(twin, name="TOTE-1", slot="TS-10-12-1", quantity=12):
    return twin.add_box(name=name, kind="TOTE", sku="SKU-02", quantity=quantity, weight=9.0, slot=slot)


def test_a_tote_goes_to_the_pick_station_and_comes_back(twin, sim, amr):
    box = tote(twin)
    twin.stock.adjust_true("TS-10-12-1", -1)                  # one unit has gone missing
    out = twin.tasks.create_task({"type": "TOTE_TO_STATION", "box_id": box.id})
    assert out.status is TaskStatus.PLANNING and out.params["station"] == "pick_station_1"
    run(sim, out)
    assert out.status is TaskStatus.COMPLETED, out.error
    assert (box.position, box.status, box.slot) == ((19, 14), BoxStatus.DELIVERED, "TS-10-12-1")
    assert twin.stock.location("TS-10-12-1").box_id == box.id   # its slot waits for it
    assert steps(twin, out) == ["NAVIGATE", "LIFT_TO", "GRASP", "LOWER", "NAVIGATE", "DELIVER"]
    back = twin.tasks.create_task({"type": "RETURN_TOTE", "box_id": box.id})
    assert back.params["slot"] == "TS-10-12-1"
    run(sim, back)
    assert back.status is TaskStatus.COMPLETED, back.error
    assert (box.position, box.status) == ((10, 12), BoxStatus.STORED)
    location = twin.stock.location("TS-10-12-1")
    assert (location.recorded_qty, location.true_qty) == (12, 11)   # the variance came home with it
    # It still stands on the tote drop, so its first NAVIGATE is over before it starts.
    assert steps(twin, back) == ["PICK", "NAVIGATE", "LIFT_TO", "PLACE", "LOWER"]


def test_a_tote_out_of_an_amrs_reach_goes_to_the_humanoid(twin, sim, amr):
    humanoid = twin.add_robot(name="H1-212", asset_id="AST-000212", position=(16, 13))
    jordan = twin.add_operator(name="Jordan", worker_id="E-10006")
    people.place(twin, jordan, "tote_aisle_1")
    high = tote(twin, slot="TS-14-12-2")
    named = twin.tasks.create_task({"type": "TOTE_TO_STATION", "box_id": high.id, "robot_id": amr.id})
    assert named.error == "TR50-201 can't work there: level 2 is out of its reach (highest level 1)"
    auto = twin.tasks.create_task({"type": "TOTE_TO_STATION", "box_id": high.id, "station": "pick_station_2"})
    scored, _ = twin.tasks._score_candidates(auto)
    assert [entry[1] for entry in scored] == [humanoid]


def test_a_returned_tote_is_put_away_by_the_supervised_humanoid(twin, sim):
    humanoid = twin.add_robot(name="H1-212", asset_id="AST-000212", position=(22, 7))
    jordan = twin.add_operator(name="Jordan", worker_id="E-10006")
    people.place(twin, jordan, "returns_qc")
    returned = twin.add_box(name="RET-1", kind="TOTE", sku="SKU-05", quantity=8, weight=7.0, position=(21, 7))

    def follow():  # Jordan walks alongside (the shift engine does this in Task 12)
        zone = twin.warehouse.zone_of_cell(humanoid.position)
        if zone is not None and jordan.zone != zone.key:
            people.place(twin, jordan, zone.key)

    task = twin.tasks.create_task({"type": "RETURNS_PUTAWAY", "box_id": returned.id})
    assert task.status is TaskStatus.PLANNING, task.error
    run(sim, task, each_tick=follow)
    assert task.status is TaskStatus.COMPLETED, task.error
    assert task.params["slot"] == returned.slot and twin.warehouse.slot(returned.slot).level <= 2
    assert twin.stock.location(returned.slot).recorded_qty == 8
    assert steps(twin, task) == ["NAVIGATE", "GRASP", "NAVIGATE", "LIFT_TO", "PLACE", "LOWER"]
    assert all(e["data"]["supervision_ok"] for e in twin.events.query(task_id=task.id, event_type="ROBOT_STEP"))


def test_tote_jobs_check_their_requests(twin, amr):
    box = tote(twin)
    box.position = (19, 16)                                     # away at Pick 2
    away = twin.tasks.create_task({"type": "TOTE_TO_STATION", "box_id": box.id})
    assert away.error == "TOTE-1 is not in its slot TS-10-12-1"
    home = tote(twin, "TOTE-2", slot="TS-11-12-1")
    nowhere = twin.tasks.create_task({"type": "TOTE_TO_STATION", "box_id": home.id, "station": "workshop"})
    assert nowhere.error == "workshop is not a pick station"
    already = twin.tasks.create_task({"type": "RETURN_TOTE", "box_id": home.id})
    assert already.error == "TOTE-2 is already in its slot TS-11-12-1"
    shelved = twin.tasks.create_task({"type": "RETURNS_PUTAWAY", "box_id": home.id})
    assert shelved.error == "TOTE-2 already belongs in slot TS-11-12-1"
    returned = twin.add_box(name="RET-1", kind="TOTE", sku="SKU-05", quantity=8, weight=7.0, position=(21, 7))
    by_amr = twin.tasks.create_task({"type": "RETURNS_PUTAWAY", "box_id": returned.id, "robot_id": amr.id})
    assert by_amr.error == "TR50-201 is an AMR; RETURNS_PUTAWAY needs a HUMANOID"
