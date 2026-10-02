"""The rules robots obey around people (multi-embodiment spec §6, §10.3):
forklifts and haulers stay out of occupied zones, an arm pauses while someone
is in its cell, the humanoid works only near its supervisor — and every
safety wait pairs with a resume and escalates once if it drags on."""
import pytest

from backend import people
from backend.digital_twin import DigitalTwin
from backend.models import Action, ActionType, OperatorStatus, RobotStatus, SimulationStatus, TaskStatus, TaskType
from backend.simulator import Simulator
from backend.task_manager import Task


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
def sam(twin):
    return twin.add_operator(name="Sam", worker_id="E-10001")


@pytest.fixture
def jordan(twin):
    return twin.add_operator(name="Jordan", worker_id="E-10006")


def attach(twin, robot, actions):
    """Hand `robot` a task whose plan is exactly `actions` (then COMPLETE)."""
    task = Task(twin.ids.next("task"), TaskType.MOVE_ROBOT, robot_id=robot.id)
    task.actions = list(actions) + [Action(ActionType.COMPLETE, "Complete task")]
    task.record(TaskStatus.ASSIGNED, "Assigned a hand-built plan")
    twin.tasks.tasks[task.id] = task
    robot.current_task = task.id
    return task


def ticks(sim, n):
    for _ in range(n):
        sim.tick()


def run(sim, task, max_ticks=600):
    for _ in range(max_ticks):
        if task.is_terminal:
            return
        sim.tick()
    assert task.is_terminal, f"{task.id} still {task.status.value}"


def kinds(twin, task):
    return [(e["event"], e["data"].get("reason"), e["data"].get("cause"))
            for e in twin.events.query(task_id=task.id)
            if e["event"] in ("ROBOT_SAFETY_WAIT", "ROBOT_SAFETY_RESUMED")]


def test_a_forklift_waits_outside_a_zone_with_a_person_in_it(twin, sim, sam):
    forklift = twin.add_robot(name="PF1200-205", asset_id="AST-000205", position=(7, 3))
    people.place(twin, sam, "pallet_aisle_1")
    task = twin.tasks.create_task({"type": "MOVE_ROBOT", "robot_id": forklift.id, "destination": "10,3"})
    ticks(sim, 40)
    assert forklift.position == (7, 3) and forklift.wait_reason == "PERSON_IN_AISLE"
    assert forklift.status is RobotStatus.WAITING and task.status is TaskStatus.BLOCKED
    waits = twin.events.query(task_id=task.id, event_type="ROBOT_SAFETY_WAIT")
    assert len(waits) == 1 and waits[0]["data"]["cell"] == {"x": 8, "y": 3}
    people.place(twin, sam, "workshop")
    run(sim, task)
    assert task.status is TaskStatus.COMPLETED and forklift.position == (10, 3)
    assert kinds(twin, task) == [("ROBOT_SAFETY_WAIT", "PERSON_IN_AISLE", None),
                                 ("ROBOT_SAFETY_RESUMED", "PERSON_IN_AISLE", "cleared")]
    entered = [e["data"] for e in twin.events.query(task_id=task.id, event_type="ROBOT_STEP")
               if e["data"]["step"] == "ENTER_ZONE"]
    assert [(d["zone"], d["people_present"]) for d in entered] == [("pallet_aisle_1", [])]


def test_only_entering_an_occupied_zone_counts_and_only_for_heavy_bodies(twin, sim, sam):
    forklift = twin.add_robot(name="PF1200-205", asset_id="AST-000205", position=(9, 3))
    people.place(twin, sam, "pallet_aisle_1")           # already in the forklift's zone
    task = twin.tasks.create_task({"type": "MOVE_ROBOT", "robot_id": forklift.id, "destination": "12,3"})
    run(sim, task)
    assert task.status is TaskStatus.COMPLETED
    amr = twin.add_robot(name="TR50-201", asset_id="AST-000201", position=(7, 13))
    people.place(twin, sam, "tote_aisle_1")
    tote_run = twin.tasks.create_task({"type": "MOVE_ROBOT", "robot_id": amr.id, "destination": "10,13"})
    run(sim, tote_run)
    assert tote_run.status is TaskStatus.COMPLETED      # the rule is for forklifts and haulers only
    assert not twin.events.query(event_type="ROBOT_SAFETY_WAIT")


def test_an_arm_pauses_while_someone_is_in_its_cell(twin, sim, sam):
    arm = twin.add_robot(name="CX10-210", asset_id="AST-000210")
    twin.add_box(name="ITEM-1", kind="ITEM", weight=0.5, position=(23, 15), order_id="ORD-1")
    twin.equipment.conveyor.item_at((23, 15)).stop_at = (23, 15)
    people.place(twin, sam, "pack_cell_1")
    task = attach(twin, arm, [Action(ActionType.GRASP, "Grasp the item",
                                     params={"from_conveyor": [23, 15], "order_id": "ORD-1"})])
    ticks(sim, 30)
    assert arm.wait_reason == "PERSON_IN_CELL" and arm.carrying_box is None
    assert not twin.events.query(task_id=task.id, event_type="ROBOT_STEP")   # it never started
    people.place(twin, sam, "pick_station_1")           # outside the fence
    run(sim, task)
    assert task.status is TaskStatus.COMPLETED and arm.wait_reason is None
    step = twin.events.query(task_id=task.id, event_type="ROBOT_STEP")[0]["data"]
    assert (step["step"], step["people_present"], step["supervision_ok"]) == ("GRASP", [], None)
    assert kinds(twin, task) == [("ROBOT_SAFETY_WAIT", "PERSON_IN_CELL", None),
                                 ("ROBOT_SAFETY_RESUMED", "PERSON_IN_CELL", "cleared")]


def test_the_humanoid_works_only_with_its_supervisor_near(twin, sim, jordan):
    humanoid = twin.add_robot(name="H1-212", asset_id="AST-000212", position=(22, 7))
    assert humanoid.model_code == "TS-H1"
    people.place(twin, jordan, "returns_qc")
    first = attach(twin, humanoid, [Action(ActionType.NAVIGATE, "Walk to (20,6)", (20, 6))])
    run(sim, first)
    assert first.status is TaskStatus.COMPLETED
    step = twin.events.query(task_id=first.id, event_type="ROBOT_STEP")[0]["data"]
    assert step["supervision_ok"] is True
    people.start_transit(twin, jordan, "sw_floor")      # Jordan walks off on a break
    second = attach(twin, humanoid, [Action(ActionType.NAVIGATE, "Walk to (22,8)", (22, 8))])
    ticks(sim, 30)
    assert humanoid.position == (20, 6) and humanoid.wait_reason == "SUPERVISOR_ABSENT"
    people.place(twin, jordan, "ne_floor")              # back, in a zone next to returns_qc
    run(sim, second)
    assert second.status is TaskStatus.COMPLETED and humanoid.position == (22, 8)
    assert kinds(twin, second) == [("ROBOT_SAFETY_WAIT", "SUPERVISOR_ABSENT", None),
                                   ("ROBOT_SAFETY_RESUMED", "SUPERVISOR_ABSENT", "cleared")]


def test_supervision_needs_a_valid_in_scope_credential_on_shift(twin, jordan):
    humanoid = twin.add_robot(name="H1-212", asset_id="AST-000212", position=(22, 7))
    assert people.supervision_available(twin, humanoid) == (True, None)
    jordan.set_status(OperatorStatus.OFF_DUTY)
    assert people.supervision_available(twin, humanoid)[1] == \
        "no operator with a 'humanoid_supervision' credential is on shift"
    jordan.set_status(OperatorStatus.AVAILABLE)
    jordan.certification_scopes["humanoid_supervision"] = {"equipment": ["TS-H2"], "site": []}
    assert "covers this robot and site" in people.supervision_available(twin, humanoid)[1]
    del jordan.certification_scopes["humanoid_supervision"]
    assert people.supervision_available(twin, humanoid)[1] == \
        "no operator holds a valid 'humanoid_supervision' credential"
    forklift = twin.add_robot(name="PF1200-205", asset_id="AST-000205", position=(7, 3))
    assert people.supervision_available(twin, forklift) == (True, None)   # nobody supervises a forklift
    jordan.certification_scopes["humanoid_supervision"] = {"equipment": ["TS-H1"], "site": ["WH-01"]}
    people.place(twin, jordan, "sw_floor")
    assert people.supervision_status(twin, humanoid) == (False, "no supervisor is in or next to its zone")


def test_a_long_wait_escalates_once(twin, sim, sam):
    arm = twin.add_robot(name="CX10-210", asset_id="AST-000210")
    people.place(twin, sam, "pack_cell_1")
    task = attach(twin, arm, [Action(ActionType.WAIT_CLEAR, "Wait for ORD-1's item", params={
        "reason": "AWAITING_ITEM", "cell": [23, 15], "order_id": "ORD-1"})])
    ticks(sim, 799)
    assert not twin.events.query(event_type="SAFETY_WAIT_ESCALATED")
    ticks(sim, 200)
    escalated = twin.events.query(event_type="SAFETY_WAIT_ESCALATED")
    assert len(escalated) == 1 and escalated[0]["task_id"] == task.id
    assert escalated[0]["data"]["reason"] == "PERSON_IN_CELL" and escalated[0]["data"]["waited_s"] >= 120
    assert escalated[0]["level"] == "ERROR"


def test_a_new_reason_closes_the_previous_wait(twin, sim, sam, jordan):
    humanoid = twin.add_robot(name="H1-212", asset_id="AST-000212", position=(16, 13))
    people.place(twin, jordan, "tote_aisle_1")
    people.place(twin, sam, "pick_station_2")
    people.start_transit(twin, sam, "intake_staging")   # Sam is crossing the walkway
    task = attach(twin, humanoid, [Action(ActionType.NAVIGATE, "Walk to (18,13)", (18, 13))])
    ticks(sim, 10)
    assert humanoid.wait_reason == "PERSON_ON_CROSSING"
    people.place(twin, jordan, None)                    # the supervisor leaves the floor
    ticks(sim, 2)
    assert humanoid.wait_reason == "SUPERVISOR_ABSENT"
    assert kinds(twin, task) == [
        ("ROBOT_SAFETY_WAIT", "PERSON_ON_CROSSING", None),
        ("ROBOT_SAFETY_RESUMED", "PERSON_ON_CROSSING", "superseded by SUPERVISOR_ABSENT"),
        ("ROBOT_SAFETY_WAIT", "SUPERVISOR_ABSENT", None),
    ]


def test_a_wait_its_task_ends_is_closed_too(twin, sim, sam):
    arm = twin.add_robot(name="CX10-210", asset_id="AST-000210")
    people.place(twin, sam, "pack_cell_1")
    task = attach(twin, arm, [Action(ActionType.WAIT_CLEAR, "Wait for ORD-1's item", params={
        "reason": "AWAITING_ITEM", "cell": [23, 15], "order_id": "ORD-1"})])
    ticks(sim, 5)
    twin.tasks.cancel_task(task.id)
    assert arm.wait_reason is None and arm.current_task is None
    assert kinds(twin, task) == [("ROBOT_SAFETY_WAIT", "PERSON_IN_CELL", None),
                                 ("ROBOT_SAFETY_RESUMED", "PERSON_IN_CELL", f"ended: {task.id} cancelled")]


def test_a_walk_to_or_from_a_zone_straddling_the_walkway_crosses_it(twin):
    warehouse = twin.warehouse
    assert people.crosses_walkway(warehouse, "cross_aisle", "pick_station_1")   # centre (17,10), cells both sides
    assert people.crosses_walkway(warehouse, "intake_staging", "top_aisle")
    assert not people.crosses_walkway(warehouse, "intake_staging", "workshop")
    assert not people.crosses_walkway(warehouse, "pick_station_2", "pick_station_1")


def test_every_robot_knows_its_catalog_model(twin, tmp_path):
    forklift = twin.add_robot(name="PF1200-205", asset_id="AST-000205", position=(7, 3))
    assert forklift.model_code == "NW-PF1200" and forklift.to_dict()["model_code"] == "NW-PF1200"
    classic = DigitalTwin(log_dir=str(tmp_path / "c" / "logs"), data_dir=str(tmp_path / "c" / "data"),
                          persist_logs=False, demo=True, demo_tasks=False)
    assert classic.find_robot("Robo-01").model_code == "AC-TR50"
