"""Job steps check what they grasp and reject bad input cleanly (multi-embodiment
spec §5.4): a conveyor grasp needs a held item in reach, a lift reports the level
it resolved, a take-off needs a grounded drone, and a planner's malformed step
fails its task with a reason — never a controller error."""
import dataclasses

import pytest

from backend.digital_twin import DigitalTwin
from backend.equipment import LineItem
from backend.models import Action, ActionType, BoxStatus, RobotStatus, SimulationStatus, TaskStatus, TaskType
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
def arm(twin):
    return twin.add_robot(name="CX10-210", asset_id="AST-000210")        # works conveyor cell (23, 15)


@pytest.fixture
def forklift(twin):
    return twin.add_robot(name="PF1200-205", asset_id="AST-000205", position=(8, 3))


@pytest.fixture
def picker(twin):
    return twin.add_robot(name="PK30-203", asset_id="AST-000203", position=(20, 14))


def attach(twin, robot, actions):
    """Hand `robot` a task whose plan is exactly `actions` (then COMPLETE)."""
    task = Task(twin.ids.next("task"), TaskType.MOVE_ROBOT, robot_id=robot.id)
    task.actions = list(actions) + [Action(ActionType.COMPLETE, "Complete task")]
    task.record(TaskStatus.ASSIGNED, "Assigned a hand-built plan")
    twin.tasks.tasks[task.id] = task
    robot.current_task = task.id
    return task


def run(sim, task, max_ticks=400):
    for _ in range(max_ticks):
        sim.tick()
        if task.is_terminal:
            return
    raise AssertionError(f"{task.id} still {task.status.value} after {max_ticks} ticks")


def events(twin, task, kind):
    return list(twin.events.query(task_id=task.id, event_type=kind))


def never_errored(twin, robot):
    """The robot's controller never raised: no ERROR status, no error event."""
    return robot.status is not RobotStatus.ERROR and robot.last_error is None \
        and not twin.events.query(robot_id=robot.id, event_type="ROBOT_ERROR")


def order_item(twin, order_id, cell, stop_at=None):
    """An order's item on the line at `cell`; held there when `stop_at` is given."""
    item = twin.add_box(name=f"ITEM-{order_id}", kind="ITEM", sku="SKU-01", quantity=1, weight=0.5,
                        true_weight_kg=0.6, position=cell, order_id=order_id)
    twin.equipment.conveyor.item_at(cell).stop_at = stop_at
    return item


def wait_then_grasp(order_id="ORD-1", cell=(23, 15)):
    """What the arm's pack job does first: wait for the order's item, then take it."""
    cell = list(cell)
    return [
        Action(ActionType.WAIT_CLEAR, "Wait for the item", params={
            "reason": "AWAITING_ITEM", "cell": cell, "order_id": order_id}),
        Action(ActionType.GRASP, "Grasp the item", params={"from_conveyor": cell, "order_id": order_id}),
    ]


# ---- GRASP from the conveyor ------------------------------------------------- #

def test_an_item_that_is_not_held_fails_the_grasp_cleanly(twin, sim, arm):
    """The reviewer's probe: an unheld item rides through the arm's cell, and a
    grasp that would miss once used to outlast it and crash the arm."""
    order_item(twin, "ORD-1", (21, 15))                       # no stop_at: it rides on
    twin.faults.arm("grasp_fail", count=1)
    task = attach(twin, arm, wait_then_grasp())
    run(sim, task)
    assert task.status is TaskStatus.FAILED
    assert "the item on conveyor cell (23,15) is not being held there" in task.error
    sim.tick()
    assert never_errored(twin, arm) and arm.carrying_box is None


def test_a_held_item_waits_out_a_missed_grasp_and_is_taken(twin, sim, arm):
    item = order_item(twin, "ORD-1", (21, 15), stop_at=(23, 15))
    twin.faults.arm("grasp_fail", count=1)                    # the retry outlasts one cell of travel
    task = attach(twin, arm, wait_then_grasp())
    run(sim, task)
    assert task.status is TaskStatus.COMPLETED, task.error
    assert arm.carrying_box == item.id and twin.equipment.conveyor.item_at((23, 15)) is None
    assert len([r for r in twin.logger.query(task_id=task.id) if "missed its grasp" in r["message"]]) == 1
    assert never_errored(twin, arm)


def test_a_grasp_from_a_cell_out_of_reach_is_rejected(twin, sim, arm):
    order_item(twin, "ORD-1", (20, 15), stop_at=(20, 15))     # held, but four cells from the arm
    far = attach(twin, arm, [Action(ActionType.GRASP, "Grasp the item", params={
        "from_conveyor": [20, 15], "order_id": "ORD-1"})])
    run(sim, far)
    assert far.status is TaskStatus.FAILED and "conveyor cell (20,15) is out of reach" in far.error
    assert twin.equipment.conveyor.item_at((20, 15)) is not None          # still on the line
    off_line = attach(twin, arm, [Action(ActionType.GRASP, "Grasp the item", params={
        "from_conveyor": [5, 5], "order_id": "ORD-1"})])
    run(sim, off_line)
    assert off_line.status is TaskStatus.FAILED and "(5,5) is not a conveyor cell" in off_line.error
    assert never_errored(twin, arm)


def test_a_grasp_fails_cleanly_when_its_item_moves_away(twin, sim, arm):
    item = order_item(twin, "ORD-1", (23, 15), stop_at=(23, 15))
    conveyor = twin.equipment.conveyor
    task = attach(twin, arm, [Action(ActionType.GRASP, "Grasp the item", params={
        "from_conveyor": [23, 15], "order_id": "ORD-1"})])
    grasp = task.actions[0]
    while not grasp.started:
        sim.tick()
    assert grasp.params["item_id"] == item.id                 # what it set out to take
    conveyor.load((24, 15), conveyor.unload((23, 15)))        # the line carried it off
    run(sim, task)
    assert task.status is TaskStatus.FAILED and "no longer on conveyor cell (23,15)" in task.error
    assert never_errored(twin, arm) and arm.carrying_box is None and item.status is not BoxStatus.CARRIED


def test_a_grasp_never_takes_a_different_item_from_the_cell(twin, sim, arm):
    first = order_item(twin, "ORD-1", (23, 15), stop_at=(23, 15))
    conveyor = twin.equipment.conveyor
    task = attach(twin, arm, [Action(ActionType.GRASP, "Grasp the item", params={
        "from_conveyor": [23, 15], "order_id": "ORD-1"})])
    grasp = task.actions[0]
    while not grasp.started:
        sim.tick()
    conveyor.load((24, 15), conveyor.unload((23, 15)))
    other = twin.add_box(name="ITEM-ORD-2", kind="ITEM", weight=0.5, position=(23, 15), order_id="ORD-2")
    conveyor.items[(23, 15)] = LineItem(other.id, "ORD-2", None, (23, 15))
    run(sim, task)
    assert task.status is TaskStatus.FAILED and arm.carrying_box is None
    assert conveyor.item_at((23, 15)).box_id == other.id and first.status is not BoxStatus.CARRIED


# ---- LIFT_TO ------------------------------------------------------------------- #

def test_a_lift_to_a_slot_reports_the_slots_level(twin, sim, forklift):
    task = attach(twin, forklift, [Action(ActionType.LIFT_TO, "Lift to the slot", slot_id="PR-08-02-3")])
    run(sim, task)
    assert task.status is TaskStatus.COMPLETED, task.error
    assert task.actions[0].level == 3
    assert events(twin, task, "ROBOT_STEP")[0]["data"]["level"] == 3
    lifted = events(twin, task, "LIFTED")[0]["data"]
    assert (lifted["level"], lifted["height_m"], lifted["slot"]) == (3, 3.0, "PR-08-02-3")


def test_a_level_that_disagrees_with_the_slot_is_rejected(twin, sim, forklift):
    task = attach(twin, forklift, [Action(ActionType.LIFT_TO, "Lift to level 2", level=2, slot_id="PR-08-02-3")])
    run(sim, task)
    assert task.status is TaskStatus.FAILED
    assert "level 2 does not match slot PR-08-02-3, which is on level 3" in task.error
    assert forklift.lift_height_m == 0.0


# ---- TAKEOFF ------------------------------------------------------------------- #

def test_a_take_off_needs_a_grounded_drone(twin, sim):
    drone = twin.add_robot(name="IX2-208", asset_id="AST-000208", position=(20, 2))
    twin.set_robot_layer(drone.id, "AIR", altitude_m=3.0)
    task = attach(twin, drone, [Action(ActionType.TAKEOFF, "Take off", params={"altitude_m": 2.0})])
    run(sim, task)
    assert task.status is TaskStatus.FAILED and "it is already flying" in task.error
    assert (drone.layer, drone.altitude_m) == ("AIR", 3.0)    # not climbed or dropped to the new altitude


def test_a_take_off_without_a_timing_leaves_the_drone_grounded(twin, sim):
    drone = twin.add_robot(name="IX2-208", asset_id="AST-000208", position=(20, 2))
    drone.mobility = dataclasses.replace(drone.mobility, takeoff_s=None)
    task = attach(twin, drone, [Action(ActionType.TAKEOFF, "Take off")])
    run(sim, task)
    assert task.status is TaskStatus.FAILED and "has no TAKEOFF timing" in task.error
    assert (drone.layer, drone.altitude_m) == ("GROUND", 0.0)


# ---- malformed planner input --------------------------------------------------- #

def test_a_conveyor_placement_needs_a_cell_and_something_to_place(twin, sim, picker):
    item = twin.add_box(name="ITEM-1", kind="ITEM", weight=0.4, position=(20, 14))
    item.set_status(BoxStatus.CARRIED)
    picker.carrying_box = item.id
    no_cell = attach(twin, picker, [Action(ActionType.PLACE_ON_CONVEYOR, "Unit on the line")])
    run(sim, no_cell)
    assert no_cell.status is TaskStatus.FAILED and "no conveyor cell to place on" in no_cell.error
    assert picker.carrying_box == item.id
    no_carton = attach(twin, picker, [Action(ActionType.PLACE_ON_CONVEYOR, "Carton on the line", target=(20, 15),
                                             params={"carton_for": "ORD-9"})])
    run(sim, no_carton)
    assert no_carton.status is TaskStatus.FAILED and "nothing to put on the conveyor" in no_carton.error
    assert never_errored(twin, picker)


@pytest.mark.parametrize("reason", ["CONVEYOR_OCCUPIED", "AWAITING_ITEM"])
def test_a_wait_needs_a_cell(twin, sim, arm, reason):
    task = attach(twin, arm, [Action(ActionType.WAIT_CLEAR, "Wait", params={"reason": reason})])
    run(sim, task)
    assert task.status is TaskStatus.FAILED and "no conveyor cell to wait on" in task.error
    assert never_errored(twin, arm)


def test_an_item_is_packed_only_into_its_own_orders_carton(twin, sim, arm):
    item = twin.add_box(name="ITEM-1", kind="ITEM", weight=0.5, position=(23, 14), order_id="ORD-2")
    item.set_status(BoxStatus.CARRIED)
    arm.carrying_box = item.id
    task = attach(twin, arm, [Action(ActionType.PLACE, "Pack it", params={"into_carton": "ORD-1", "lane": "dock_5"})])
    run(sim, task)
    assert task.status is TaskStatus.FAILED and "ITEM-1 is for order ORD-2, not ORD-1" in task.error
    assert arm.carrying_box == item.id and item.id in twin.boxes
    assert not [box for box in twin.boxes.values() if box.kind.value == "CARTON"]   # no carton was started
