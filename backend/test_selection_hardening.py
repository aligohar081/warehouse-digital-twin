"""Hardening of capability-filtered selection (review of multi-embodiment Task 7):
an AUTO request on the new floor reports a request that names nothing real as
that — the same words a named robot or the classic floor gets, not "no route";
the reach rule covers the older box-handling jobs too; and the AUTO assignment
log names the winner's own target."""
import pytest

from backend import people
from backend.digital_twin import DigitalTwin
from backend.models import LogCategory, TaskStatus
from backend.task_manager import Task

#: A destination that exists on each floor.
REAL_DESTINATION = {"distribution_center": "outbound_staging", "classic": "packing_area"}

#: Requests that name something that doesn't exist, and what every route in
#: (AUTO on the new floor, a named robot, the classic floor) answers.
MISSING_THINGS = [
    ("unknown box", lambda dest: {"type": "PICK_AND_DELIVER", "box_id": "NOPE", "destination": dest},
     "Box 'NOPE' does not exist"),
    ("no box", lambda dest: {"type": "PICK_AND_DELIVER", "destination": dest},
     "This task type needs a box"),
    ("unknown destination", lambda dest: {"type": "MOVE_ROBOT", "destination": "nowhere_zone"},
     "Unknown location 'nowhere_zone'"),
    ("no destination", lambda dest: {"type": "MOVE_ROBOT"},
     "This task type needs a destination"),
    ("destination off the floor", lambda dest: {"type": "MOVE_ROBOT", "destination": "99,99"},
     "Coordinates (99, 99) are outside the warehouse"),
    ("unknown source", lambda dest: {"type": "MOVE_ROBOT", "destination": dest, "source": "bogus_src"},
     "Invalid source: Unknown location 'bogus_src'"),
]


def make_twin(tmp_path, layout="distribution_center"):
    return DigitalTwin(log_dir=str(tmp_path / layout / "logs"), data_dir=str(tmp_path / layout / "data"),
                       persist_logs=False, demo=True, demo_tasks=False, layout=layout)


@pytest.fixture
def twin(tmp_path):
    return make_twin(tmp_path)


def candidates(twin, task):
    scored, _ = twin.tasks._score_candidates(task)
    return [entry[1].name for entry in scored]


def slotted_tote(twin, slot_id, position=(5, 5)):
    """A tote the job lifts out of `slot_id`, standing where a body can drive
    to it (a real shelf cell isn't drivable, so the route would fail first)."""
    box = twin.add_box(name="T-HIGH", kind="TOTE", sku="SKU-01", quantity=4, weight=5.0, position=position)
    box.slot = slot_id
    return box


@pytest.mark.parametrize("row", MISSING_THINGS, ids=[row[0] for row in MISSING_THINGS])
def test_a_request_naming_nothing_real_says_so_on_every_route(twin, tmp_path, row):
    _, payload, error = row
    twin.add_robot(name="TR50-201", asset_id="AST-000201", position=(5, 6))
    twin.add_robot(name="PF1200-205", asset_id="AST-000205", position=(8, 8))
    auto = twin.tasks.create_task(payload(REAL_DESTINATION["distribution_center"]))
    assert auto.status is TaskStatus.FAILED and auto.error == error
    named = twin.tasks.create_task({**payload(REAL_DESTINATION["distribution_center"]), "robot_id": "TR50-201"})
    assert named.status is TaskStatus.FAILED and named.error == error
    classic = make_twin(tmp_path, layout="classic")
    old = classic.tasks.create_task(payload(REAL_DESTINATION["classic"]))
    assert old.status is TaskStatus.FAILED and old.error == error


def test_a_real_request_no_body_can_do_still_gets_the_fleet_reasons(twin):
    # The existence checks come first, but only to name what is missing: a
    # request that names real things still reaches the capability check.
    twin.add_robot(name="CX10-210", asset_id="AST-000210")
    task = twin.tasks.create_task({"type": "MOVE_ROBOT", "destination": "dock_1", "source": "dock_2"})
    assert task.status is TaskStatus.FAILED
    assert task.error == "No robot can do this MOVE_ROBOT: CX10-210 is fixed equipment and can't do MOVE_ROBOT"


def test_a_slotted_box_above_a_bodys_reach_filters_it_out(twin):
    amr = twin.add_robot(name="TR50-201", asset_id="AST-000201", position=(5, 6))   # highest level 1
    box = slotted_tote(twin, "TS-10-12-2")                                         # level 2
    reach = "can't work there: level 2 is out of its reach (highest level 1)"
    named = twin.tasks.create_task({"type": "PICK_AND_DELIVER", "robot_id": amr.id, "box_id": box.id,
                                    "destination": "outbound_staging"})
    assert named.status is TaskStatus.FAILED and named.error == f"TR50-201 {reach}"
    auto = twin.tasks.create_task({"type": "PICK_AND_DELIVER", "box_id": box.id, "destination": "outbound_staging"})
    assert auto.status is TaskStatus.FAILED
    assert auto.error == f"No robot can do this PICK_AND_DELIVER: TR50-201 {reach}"
    # The mid-job re-check reads the same level for the same job.
    live = Task("task_999", auto.type, box_id=box.id)
    assert twin.tasks.physical_recheck(amr, live) == reach


def test_a_robot_with_enough_reach_gets_the_high_box(twin):
    amr = twin.add_robot(name="TR50-201", asset_id="AST-000201", position=(5, 6))
    humanoid = twin.add_robot(name="H1-212", asset_id="AST-000212", position=(6, 5))   # highest level 2
    jordan = twin.add_operator(name="Jordan", worker_id="E-10006")
    people.place(twin, jordan, "returns_qc")
    high = slotted_tote(twin, "TS-10-12-2")
    task = twin.tasks.create_task({"type": "PICK_AND_DELIVER", "box_id": high.id, "destination": "outbound_staging"})
    assert task.status is TaskStatus.PLANNING, task.error
    assert candidates(twin, task) == ["H1-212"]               # level 2 is out of the AMR's reach
    assert twin.tasks.select_robot(task) is humanoid
    low = twin.add_box(name="T-LOW", kind="TOTE", sku="SKU-02", quantity=4, weight=5.0, position=(4, 5))
    low.slot = "TS-10-12-1"                                   # level 1 is within both bodies' reach
    easy = twin.tasks.create_task({"type": "PICK_AND_DELIVER", "box_id": low.id, "destination": "outbound_staging"})
    assert set(candidates(twin, easy)) == {amr.name, humanoid.name}


def test_the_assignment_log_names_the_winners_own_target(twin):
    # The forklift can't stop in the narrow tote aisle, so it is sent to the
    # cross aisle beside it; the AMR, scanned last, goes to the aisle itself.
    forklift = twin.add_robot(name="PF1200-205", asset_id="AST-000205", position=(10, 11))
    twin.add_robot(name="TR50-201", asset_id="AST-000201", position=(4, 13))
    task = twin.tasks.create_task({"type": "MOVE_ROBOT", "destination": "10,13"})
    assert task.status is TaskStatus.PLANNING, task.error
    assert twin.tasks.select_robot(task) is forklift
    record = twin.logger.query(category=LogCategory.PLANNER.value, task_id=task.id, search="AUTO assignment")[-1]
    assert record["data"]["target"] == "Cross aisle"
    by_robot = {entry["robot"]: entry for entry in record["data"]["candidates"]}
    assert (by_robot["PF1200-205"]["target"], by_robot["PF1200-205"]["target_cell"]) == ("Cross aisle", {"x": 10, "y": 11})
    assert (by_robot["TR50-201"]["target"], by_robot["TR50-201"]["target_cell"]) == ("Tote aisle 1", {"x": 10, "y": 13})
    _, label = twin.tasks._score_candidates(task)
    assert label == "Cross aisle"


def test_the_classic_assignment_log_keeps_its_keys(tmp_path):
    classic = make_twin(tmp_path, layout="classic")
    task = classic.tasks.create_task({"type": "PICK_AND_DELIVER", "robot": "AUTO", "box": "Box-B",
                                      "destination": "packing_area"})
    assert task.status is TaskStatus.PLANNING, task.error
    winner = classic.tasks.select_robot(task)
    record = classic.logger.query(category=LogCategory.PLANNER.value, task_id=task.id, search="AUTO assignment")[-1]
    assert record["robot_id"] == winner.id
    assert set(record["data"]) == {"candidates", "target"} and record["data"]["target"] == "Box-B"
    for entry in record["data"]["candidates"]:
        assert set(entry) == {"robot", "distance", "battery", "workload", "score"}
