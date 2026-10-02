"""A floor job needs a robot with a floor profile (multi-embodiment spec §9.1).

A classic robot (`robot.mobility is None`) has no body to check a job against,
so a job type registered in JOB_SPECS must be turned away at the gate, never
handed to the planner: named, AUTO on the classic floor, and AUTO on a mixed
fleet. Classic job types keep their behaviour either way."""
import pytest

from backend.digital_twin import DigitalTwin
from backend.jobs import choose_slot
from backend.models import BoxKind, SimulationStatus, TaskStatus
from backend.simulator import Simulator
from backend.task_planner import PlanningError

NEEDS_PROFILE = "has no floor profile; PUTAWAY_PALLET needs a robot on the multi-embodiment floor"


def make_twin(tmp_path, **kwargs):
    return DigitalTwin(log_dir=str(tmp_path / "logs"), data_dir=str(tmp_path / "data"),
                       persist_logs=False, demo=True, demo_tasks=False, **kwargs)


@pytest.fixture
def classic(tmp_path):
    return make_twin(tmp_path)


@pytest.fixture
def floor(tmp_path):
    return make_twin(tmp_path, layout="distribution_center")


def start(twin):
    simulator = Simulator(twin)
    twin.simulation_status = SimulationStatus.RUNNING
    return simulator


def test_an_auto_putaway_on_the_classic_floor_is_rejected_and_wedges_nothing(classic):
    sim = start(classic)
    box = classic.add_box(name="PAL-1", kind="PALLET", sku="SKU-01", quantity=40, weight=600.0, position=(3, 3))
    task = classic.tasks.create_task({"type": "PUTAWAY_PALLET", "box_id": box.id})
    assert task.status is TaskStatus.FAILED
    assert task.error == ("No robot can do this PUTAWAY_PALLET: "
                          f"Robo-01 {NEEDS_PROFILE}; Robo-02 {NEEDS_PROFILE}")
    for _ in range(5):
        sim.tick()                                  # used to raise AttributeError every tick
    assert task.status is TaskStatus.FAILED


def test_a_named_classic_robot_is_refused_a_floor_job(classic):
    box = classic.add_box(name="PAL-1", kind="PALLET", sku="SKU-01", quantity=40, weight=600.0, position=(3, 3))
    task = classic.tasks.create_task({"type": "PUTAWAY_PALLET", "box_id": box.id, "robot_id": "robot_01"})
    assert task.status is TaskStatus.FAILED
    assert task.error == f"Robo-01 {NEEDS_PROFILE}"
    forklift = classic.add_robot(name="PF-C1", robot_class="FORKLIFT", position=(1, 1))
    assert forklift.mobility is None                # allowed by its class, still no body
    named = classic.tasks.create_task({"type": "PUTAWAY_PALLET", "box_id": box.id, "robot_id": forklift.id})
    assert named.error == f"PF-C1 {NEEDS_PROFILE}"


def test_a_mixed_fleet_never_hands_a_floor_job_to_a_robot_without_a_profile(floor):
    sim = start(floor)
    forklift = floor.add_robot(name="PF1200-205", asset_id="AST-000205", position=(7, 4))
    bare = floor.add_robot(name="PF-bare", asset_id="AST-000206", position=(5, 4))
    bare.mobility = None                            # a classic body on the layered floor
    box = floor.add_box(name="PAL-1", kind="PALLET", sku="SKU-01", quantity=40, weight=600.0, position=(5, 3))
    task = floor.tasks.create_task({"type": "PUTAWAY_PALLET", "box_id": box.id})
    assert task.status is TaskStatus.PLANNING       # the forklift can do it
    scored, _ = floor.tasks._score_candidates(task)
    assert [robot.id for _, robot, _ in scored] == [forklift.id]
    for _ in range(1500):
        if task.is_terminal:
            break
        sim.tick()
    assert task.status is TaskStatus.COMPLETED, task.error
    assert task.robot_id == forklift.id


def test_choose_slot_without_a_floor_profile_is_a_planning_error(classic):
    robot = classic.robots["robot_01"]
    box = classic.add_box(name="PAL-1", kind="PALLET", sku="SKU-01", quantity=40, weight=600.0, position=(3, 3))
    task = classic.tasks.create_task({"type": "MOVE_ROBOT", "destination": "dock_1", "robot_id": robot.id})
    with pytest.raises(PlanningError, match="Robo-01 has no floor profile"):
        choose_slot(classic.planner, task, robot, BoxKind.PALLET, box.position)


def test_a_classic_job_is_still_given_to_a_classic_robot(classic):
    """backend/tests.py::test_pick_and_deliver_end_to_end_logs_the_full_story covers a named
    robot; this one covers AUTO selection among the profile-less robots."""
    sim = start(classic)
    task = classic.tasks.create_task({"type": "PICK_AND_DELIVER", "box": "Box-A", "source": "shelf_a",
                                      "destination": "loading_zone"})
    assert task.status is TaskStatus.PLANNING
    for _ in range(800):
        if task.is_terminal:
            break
        sim.tick()
    assert task.status is TaskStatus.COMPLETED, task.error
    assert task.robot_id in classic.robots
