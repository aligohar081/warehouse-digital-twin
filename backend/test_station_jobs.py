"""Station jobs and the jobs people do (multi-embodiment spec §8, §9.2,
§11.3): units picked onto the line, an order packed into a carton and sorted
to its dock, a person picking at Pick 2, and a conveyor jam cleared by
someone whose credential covers the arm beside it."""
import pytest

from backend import people
from backend.digital_twin import DigitalTwin
from backend.models import BoxKind, BoxStatus, OperatorStatus, SimulationStatus, TaskStatus, TaskType
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


def run(sim, *tasks, max_ticks=3000):
    for _ in range(max_ticks):
        if all(task.is_terminal for task in tasks):
            return
        sim.tick()
    assert all(task.is_terminal for task in tasks), [(t.id, t.status.value) for t in tasks]


def tote_at(twin, cell, quantity=6, name="TOTE-1", slot="TS-10-12-1"):
    """A tote that has been brought to a station's tote drop."""
    tote = twin.add_box(name=name, kind="TOTE", sku="SKU-02", quantity=quantity, weight=7.5, slot=slot)
    tote.position = cell
    tote.set_status(BoxStatus.DELIVERED)
    return tote


def test_an_order_is_picked_packed_and_sorted_to_its_dock(twin, sim):
    picker = twin.add_robot(name="PK30-203", asset_id="AST-000203", position=(20, 14))
    arm = twin.add_robot(name="CX10-210", asset_id="AST-000210")
    tote = tote_at(twin, (19, 14))
    pick = twin.tasks.create_task({"type": "PICK_ITEMS", "box_id": tote.id, "quantity": 2, "order_id": "ORD-1",
                                   "pack_cell": "pack_cell_1"})
    pack = twin.tasks.create_task({"type": "PACK_ORDER", "order_id": "ORD-1", "quantity": 2, "lane": "dock_5"})
    assert pick.status is TaskStatus.PLANNING and pack.status is TaskStatus.PLANNING, (pick.error, pack.error)
    run(sim, pick, pack)
    assert pick.status is TaskStatus.COMPLETED and pack.status is TaskStatus.COMPLETED, (pick.error, pack.error)
    assert (pick.robot_id, pack.robot_id) == (picker.id, arm.id)
    assert tote.quantity == 4 and tote.status is BoxStatus.DELIVERED          # still waiting on the tote drop
    carton = next(box for box in twin.boxes.values() if box.kind is BoxKind.CARTON)
    assert carton.quantity == 2 and not [b for b in twin.boxes.values() if b.kind is BoxKind.ITEM]
    ticks(sim, 80)
    assert twin.warehouse.zone_of_cell(carton.position).key == "dock_5" and carton.status is BoxStatus.DELIVERED
    holders = [(e["data"]["from"], e["data"]["to"]) for e in twin.events.query(event_type="HANDOFF")]
    assert holders.count((picker.id, "conveyor")) == 2 and holders.count(("conveyor", arm.id)) == 2
    assert holders[-2:] == [("conveyor", "sorter"), ("sorter", "dock_5")]


def test_station_jobs_check_their_requests(twin):
    twin.add_robot(name="PK30-203", asset_id="AST-000203", position=(20, 14))
    twin.add_robot(name="CX10-210", asset_id="AST-000210")
    other_arm = twin.add_robot(name="CX10-211", asset_id="AST-000211")
    tote = tote_at(twin, (19, 14), quantity=3)
    errors = [
        ({"type": "PICK_ITEMS", "box_id": tote.id, "quantity": 5}, "TOTE-1 holds only 3 of SKU-02"),
        ({"type": "PICK_ITEMS", "box_id": tote.id, "quantity": 0}, "quantity must be a whole number of at least 1"),
        ({"type": "PICK_ITEMS", "box_id": tote.id, "quantity": 1, "station": "pick_station_2"},
         "Pick 2 is not a robot pick station: use MANUAL_PICK"),
        ({"type": "PICK_ITEMS", "box_id": tote.id, "quantity": 1, "pack_cell": "pack_cell_9"},
         "Unknown pack cell 'pack_cell_9'"),
        ({"type": "PACK_ORDER", "quantity": 1}, "PACK_ORDER needs an order_id"),
        ({"type": "PACK_ORDER", "order_id": "ORD-1", "quantity": 1, "lane": "dock_3"},
         "dock_3 is not a sorter lane (lanes: dock_4, dock_5)"),
        ({"type": "PACK_ORDER", "order_id": "ORD-1", "quantity": 1, "robot_id": other_arm.id},
         "No path from CX10-211 to (23,14)"),
        ({"type": "MANUAL_PICK", "box_id": tote.id, "quantity": 1, "station": "pick_station_1"},
         "Pick 1 is not a human pick station: use PICK_ITEMS"),
        ({"type": "CLEAR_JAM", "segment": "21,15"}, "Conveyor cell (21,15) is not jammed"),
    ]
    for payload, error in errors:
        task = twin.tasks.create_task(payload)
        assert task.status is TaskStatus.FAILED and task.error == error, (payload, task.error)
    moved = tote_at(twin, (19, 16), name="TOTE-2", slot="TS-11-12-1")
    elsewhere = twin.tasks.create_task({"type": "PICK_ITEMS", "box_id": moved.id, "quantity": 1})
    assert elsewhere.error == "TOTE-2 is not on Pick 1's tote drop (19,14)"


def test_a_person_picks_at_pick_two(twin, sim):
    sam = twin.add_operator(name="Sam", worker_id="E-10001")
    lee = twin.add_operator(name="Lee", worker_id="E-10002")
    people.place(twin, sam, "workshop")
    people.place(twin, lee, "pick_station_2")
    tote = tote_at(twin, (19, 16))
    task = twin.tasks.create_task({"type": "MANUAL_PICK", "box_id": tote.id, "quantity": 2, "order_id": "ORD-4",
                                   "pack_cell": "pack_cell_2"})
    assert task.status is TaskStatus.IN_PROGRESS and task.operator_id == lee.id   # the nearer of the two
    assert lee.status is OperatorStatus.ON_TASK and task.robot_id is None
    run(sim, task)
    assert task.status is TaskStatus.COMPLETED, task.error
    assert lee.status is OperatorStatus.AVAILABLE and lee.zone == "pick_station_2"
    items = [b for b in twin.boxes.values() if b.kind is BoxKind.ITEM]
    assert len(items) == 2 and tote.quantity == 4
    placed = [e["data"] for e in twin.events.query(task_id=task.id, event_type="HANDOFF")]
    assert [(h["from"], h["to"]) for h in placed] == [(lee.id, "conveyor")] * 2
    assert all(item.order_id == "ORD-4" for item in items)


def test_a_jam_in_a_pack_cell_is_cleared_by_mateo_not_riley(twin, sim):
    twin.add_robot(name="CX10-210", asset_id="AST-000210")
    riley = twin.add_operator(name="Riley", worker_id="E-10008")
    mateo = twin.add_operator(name="Mateo", worker_id="E-10004")
    people.place(twin, riley, "pick_station_1")              # nearer, but her robot_cell_access is revoked
    people.place(twin, mateo, "workshop")
    twin.equipment.jam((23, 15))
    named = twin.tasks.create_task({"type": "CLEAR_JAM", "segment": "23,15", "operator_id": riley.id})
    assert "does not hold the required 'robot_cell_access'" in named.error
    ticks(sim, 20)                                           # the jam asks for someone
    task = next(t for t in twin.tasks.tasks.values() if t.type is TaskType.CLEAR_JAM and not t.is_terminal)
    assert task.operator_id == mateo.id and task.required_certification == "robot_cell_access"
    run(sim, task)
    assert task.status is TaskStatus.COMPLETED, task.error
    assert (23, 15) not in twin.equipment.conveyor.jams and twin.events.query(event_type="CONVEYOR_CLEARED")
    assert mateo.zone == "workshop" and mateo.status is OperatorStatus.AVAILABLE   # walked back out of the cell
    moved = [e["data"] for e in twin.events.query(event_type="PERSON_MOVED") if e["data"]["operator_id"] == mateo.id]
    assert [(m["from"], m["to"]) for m in moved] == [("workshop", "pack_cell_1"), ("pack_cell_1", "workshop")]


def test_a_jam_outside_the_pack_cells_needs_no_cell_access(twin, sim):
    noor = twin.add_operator(name="Noor", worker_id="E-10003")
    people.place(twin, noor, "workshop")
    twin.equipment.jam((20, 15))
    task = twin.tasks.create_task({"type": "CLEAR_JAM", "segment": "20,15"})
    assert task.status is TaskStatus.IN_PROGRESS and task.operator_id == noor.id
    assert task.required_certification is None
    run(sim, task)
    assert task.status is TaskStatus.COMPLETED and (20, 15) not in twin.equipment.conveyor.jams


def test_a_jam_no_one_can_clear_is_asked_for_again_until_someone_can(twin, sim):
    twin.add_robot(name="CX10-210", asset_id="AST-000210")
    riley = twin.add_operator(name="Riley", worker_id="E-10008")
    people.place(twin, riley, "pick_station_1")
    twin.equipment.jam((23, 15))
    ticks(sim, 45)
    asked = [t for t in twin.tasks.tasks.values() if t.type is TaskType.CLEAR_JAM]
    assert len(asked) == 2 and all(t.status is TaskStatus.FAILED for t in asked)
    mateo = twin.add_operator(name="Mateo", worker_id="E-10004")
    people.place(twin, mateo, "workshop")
    ticks(sim, 20)
    assert any(t.operator_id == mateo.id and not t.is_terminal
               for t in twin.tasks.tasks.values() if t.type is TaskType.CLEAR_JAM)


def test_a_credential_must_cover_the_arm_on_the_new_floor(twin):
    twin.add_robot(name="CX10-210", asset_id="AST-000210")
    mateo = twin.add_operator(name="Mateo", worker_id="E-10004")
    people.place(twin, mateo, "workshop")
    mateo.certification_scopes["robot_cell_access"] = {"equipment": ["FB-CX20"], "site": []}
    twin.equipment.jam((23, 15))
    task = twin.tasks.create_task({"type": "CLEAR_JAM", "segment": "23,15", "operator_id": mateo.id})
    assert task.error == ("Mateo has a 'robot_cell_access' credential that does not cover FB-CX10 "
                          "(covers: FB-CX20)")


def test_cancelling_a_persons_job_frees_them(twin, sim):
    noor = twin.add_operator(name="Noor", worker_id="E-10003")
    people.place(twin, noor, "workshop")
    twin.equipment.jam((20, 15))
    task = twin.tasks.create_task({"type": "CLEAR_JAM", "segment": "20,15"})
    ticks(sim, 5)
    twin.tasks.cancel_task(task.id)
    assert noor.status is OperatorStatus.AVAILABLE and noor.current_task is None
