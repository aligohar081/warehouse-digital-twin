"""Pallet jobs (multi-embodiment spec §9.2): off the inbound truck, into a rack
slot, out again to outbound staging and onto the outbound truck."""
import pytest

from backend.agent_tools import TASK_TYPE_GUIDE
from backend.digital_twin import DigitalTwin
from backend.embodiment import step_ticks
from backend.models import ROBOT_CLASS_PRESETS, BoxStatus, SimulationStatus, TaskStatus
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
def forklift(twin):
    return twin.add_robot(name="PF1200-205", asset_id="AST-000205", position=(7, 4))


def run(sim, task, max_ticks=1500):
    for _ in range(max_ticks):
        if task.is_terminal:
            return
        sim.tick()
    assert task.is_terminal, f"{task.id} still {task.status.value}"


def steps(twin, task):
    return [e["data"]["step"] for e in twin.events.query(task_id=task.id, event_type="ROBOT_STEP")
            if e["data"]["step"] != "ENTER_ZONE"]


def pallet(twin, name="PAL-1", weight=600.0, **where):
    return twin.add_box(name=name, kind="PALLET", sku="SKU-01", quantity=40, weight=weight, **where)


def test_unloading_takes_a_pallet_off_the_truck_to_intake(twin, sim, forklift):
    hauler = twin.add_robot(name="HH300-207", asset_id="AST-000207", position=(2, 8))
    light = pallet(twin, "PAL-1", weight=250.0, position=(2, 7))
    task = twin.tasks.create_task({"type": "UNLOAD_TRUCK", "box_id": light.id})
    assert task.status is TaskStatus.PLANNING and task.destination == "intake_staging"
    run(sim, task)
    assert task.status is TaskStatus.COMPLETED and task.robot_id == hauler.id    # the nearer able body
    assert twin.warehouse.zone_of_cell(light.position).key == "intake_staging"
    assert light.status is BoxStatus.DELIVERED
    assert steps(twin, task) == ["NAVIGATE", "PICK", "NAVIGATE", "DELIVER"]
    handoff = twin.events.query(task_id=task.id, event_type="HANDOFF")[-1]["data"]
    assert (handoff["from"], handoff["to"]) == (hauler.id, "intake_staging")
    heavy = pallet(twin, "PAL-2", weight=800.0, position=(2, 9))
    named = twin.tasks.create_task({"type": "UNLOAD_TRUCK", "box_id": heavy.id, "robot_id": hauler.id})
    assert named.error == "HH300-207 can't take PAL-2: a 800 kg load is over its 300 kg payload limit"
    auto = twin.tasks.create_task({"type": "UNLOAD_TRUCK", "box_id": heavy.id})
    run(sim, auto)
    assert auto.robot_id == forklift.id


def test_a_pallet_is_put_away_in_the_slot_it_was_given(twin, sim, forklift):
    box = pallet(twin, position=(5, 4))
    task = twin.tasks.create_task({"type": "PUTAWAY_PALLET", "box_id": box.id, "slot": "PR-08-02-3"})
    run(sim, task)
    assert task.status is TaskStatus.COMPLETED, task.error
    assert (box.status, box.slot, box.position) == (BoxStatus.STORED, "PR-08-02-3", (8, 2))
    assert twin.stock.location("PR-08-02-3").box_id == box.id
    assert steps(twin, task) == ["NAVIGATE", "PICK", "NAVIGATE", "LIFT_TO", "PLACE", "LOWER"]
    assert twin.events.query(task_id=task.id, event_type="PLACED")[0]["data"]["level"] == 3
    picked = twin.events.query(task_id=task.id, event_type="HANDOFF")[0]["data"]
    assert (picked["from"], picked["to"]) == ("intake_staging", forklift.id)


def test_a_putaway_with_no_slot_takes_the_nearest_free_one_in_reach(twin, sim, forklift):
    pallet(twin, "PAL-0", slot="PR-08-02-0")
    box = pallet(twin, position=(5, 3))
    task = twin.tasks.create_task({"type": "PUTAWAY_PALLET", "box_id": box.id})
    run(sim, task)
    assert task.status is TaskStatus.COMPLETED and task.params["slot"] == "PR-08-02-1" == box.slot


def test_a_putaway_needs_a_free_pallet_slot(twin, forklift):
    pallet(twin, "PAL-0", slot="PR-08-02-0")
    box = pallet(twin, position=(5, 3))
    for slot, error in (("PR-08-02-0", "Slot PR-08-02-0 already holds"), ("TS-10-12-1", "holds TOTEs"),
                        ("PR-99-02-0", "Unknown slot")):
        task = twin.tasks.create_task({"type": "PUTAWAY_PALLET", "box_id": box.id, "slot": slot})
        assert task.status is TaskStatus.FAILED and error in task.error
    other = pallet(twin, "PAL-2", position=(5, 5))
    first = twin.tasks.create_task({"type": "PUTAWAY_PALLET", "box_id": box.id, "slot": "PR-09-02-0"})
    assert first.status is TaskStatus.PLANNING
    second = twin.tasks.create_task({"type": "PUTAWAY_PALLET", "box_id": other.id, "slot": "PR-09-02-0"})
    assert second.error == "Slot PR-09-02-0 is promised to another job"
    tote = twin.add_box(name="TOTE-1", kind="TOTE", sku="SKU-02", quantity=10, weight=8.0, slot="TS-10-12-1")
    wrong = twin.tasks.create_task({"type": "PUTAWAY_PALLET", "box_id": tote.id})
    assert wrong.error == "TOTE-1 is a TOTE, not a PALLET"


def test_a_pallet_is_retrieved_and_loaded_onto_the_truck(twin, sim, forklift):
    box = pallet(twin, slot="PR-10-05-2")
    retrieve = twin.tasks.create_task({"type": "RETRIEVE_PALLET", "box_id": box.id})
    run(sim, retrieve)
    assert retrieve.status is TaskStatus.COMPLETED, retrieve.error
    assert twin.warehouse.zone_of_cell(box.position).key == "outbound_staging"
    assert box.slot is None and twin.stock.location("PR-10-05-2") is None
    assert steps(twin, retrieve) == ["NAVIGATE", "LIFT_TO", "GRASP", "LOWER", "NAVIGATE", "DELIVER"]
    load = twin.tasks.create_task({"type": "LOAD_TRUCK", "box_id": box.id})
    assert load.destination == "dock_3"
    run(sim, load)
    assert load.status is TaskStatus.COMPLETED and box.status is BoxStatus.SHIPPED
    assert twin.warehouse.zone_of_cell(box.position).key == "dock_3"
    assert twin.box_at(box.position) is None                    # gone with the truck


def test_new_job_picks_take_the_forks_engage_time(twin, sim, forklift):
    box = pallet(twin, position=(7, 4))                          # right under the forks
    task = twin.tasks.create_task({"type": "LOAD_TRUCK", "box_id": box.id, "robot_id": forklift.id})
    sim.tick()                                                   # assigned and started: NAVIGATE is done
    started = twin.tick_count
    while box.status is not BoxStatus.CARRIED:
        sim.tick()
    assert twin.tick_count - started == 1 + step_ticks(forklift.mobility, "GRASP")
    assert task.status is not TaskStatus.FAILED


def test_a_misplaced_pallet_cannot_be_retrieved_from_its_record(twin, sim, forklift):
    box = pallet(twin, position=(5, 4))
    twin.faults.arm("wrong_level")
    putaway = twin.tasks.create_task({"type": "PUTAWAY_PALLET", "box_id": box.id, "slot": "PR-08-02-2"})
    run(sim, putaway)
    assert putaway.status is TaskStatus.COMPLETED and box.true_slot == "PR-08-02-3"
    retrieve = twin.tasks.create_task({"type": "RETRIEVE_PALLET", "box_id": box.id})
    run(sim, retrieve)
    assert retrieve.status is TaskStatus.FAILED and "PAL-1 is not in slot PR-08-02-2" in retrieve.error


def test_the_pallet_jobs_are_offered_and_allowed(twin):
    kinds = {option["id"] for option in twin.options()["task_types"]}
    assert {"UNLOAD_TRUCK", "PUTAWAY_PALLET", "RETRIEVE_PALLET", "LOAD_TRUCK"} <= kinds
    assert "PUTAWAY_PALLET:box_id(a staged pallet)" in TASK_TYPE_GUIDE
    assert {"UNLOAD_TRUCK", "LOAD_TRUCK"} <= set(ROBOT_CLASS_PRESETS["FORKLIFT"]["allowed_task_types"])
    assert "UNLOAD_TRUCK" in ROBOT_CLASS_PRESETS["HEAVY_HAULER"]["allowed_task_types"]
    assert "PUTAWAY_PALLET" not in ROBOT_CLASS_PRESETS["HEAVY_HAULER"]["allowed_task_types"]
