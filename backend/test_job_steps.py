"""Job steps with physical duration (multi-embodiment spec §5.4): lifting,
grasping and placing in slots, cartons and on the conveyor, take-off and
landing, and the events each step leaves in the task log."""
import pytest

from backend import goods
from backend.digital_twin import DigitalTwin
from backend.embodiment import lift_ticks, step_ticks
from backend.models import (
    Action, ActionType, BoxKind, BoxStatus, RobotStatus, SimulationStatus, TaskStatus, TaskType,
)
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


def attach(twin, robot, actions, task_type=TaskType.MOVE_ROBOT):
    """Hand `robot` a task whose plan is exactly `actions` (then COMPLETE)."""
    task = Task(twin.ids.next("task"), task_type, robot_id=robot.id)
    task.actions = list(actions) + [Action(ActionType.COMPLETE, "Complete task")]
    task.record(TaskStatus.ASSIGNED, "Assigned a hand-built plan")
    twin.tasks.tasks[task.id] = task
    robot.current_task = task.id
    return task


def run(sim, task, max_ticks=1000):
    for tick in range(1, max_ticks + 1):
        sim.tick()
        if task.is_terminal:
            return tick
    raise AssertionError(f"{task.id} still {task.status.value} after {max_ticks} ticks")


def events(twin, task, kind):
    return [e for e in twin.events.query(task_id=task.id, event_type=kind)]


@pytest.fixture
def forklift(twin):
    return twin.add_robot(name="PF1200-205", asset_id="AST-000205", position=(8, 3))


def test_a_forklift_lifts_grasps_a_pallet_and_lowers(twin, sim, forklift):
    pallet = twin.add_box(name="PAL-1", kind="PALLET", sku="SKU-01", quantity=40, weight=600.0,
                          true_weight_kg=640.0, slot="PR-08-02-3")
    task = attach(twin, forklift, [
        Action(ActionType.LIFT_TO, "Lift to level 3", level=3, slot_id="PR-08-02-3"),
        Action(ActionType.GRASP, "Grasp PAL-1", box_id=pallet.id, slot_id="PR-08-02-3"),
        Action(ActionType.LOWER, "Lower"),
    ])
    lift = lift_ticks(forklift.mobility, 0.0, 3.0)
    grasp = step_ticks(forklift.mobility, "GRASP")
    assert (lift, grasp) == (67, 20)                     # 3 m at 0.3 m/s; a 3 s fork engage
    ticks = run(sim, task)
    assert ticks == 3 + lift + grasp + lift + 1          # each step's start tick, its duration, then COMPLETE
    assert task.status is TaskStatus.COMPLETED
    assert forklift.carrying_box == pallet.id and forklift.lift_height_m == 0.0
    assert pallet.status is BoxStatus.CARRIED and pallet.slot is None
    assert twin.stock.location("PR-08-02-3") is None     # the pallet has left storage
    lifted = events(twin, task, "LIFTED")[0]["data"]
    assert (lifted["level"], lifted["height_m"], lifted["max_shelf_level"]) == (3, 3.0, 4)
    picked = events(twin, task, "BOX_PICKED")[0]["data"]
    assert (picked["true_weight_kg"], picked["declared_weight_kg"], picked["max_payload_kg"]) == (640.0, 600.0, 1200.0)
    steps = [e["data"]["step"] for e in events(twin, task, "ROBOT_STEP")]
    assert steps == ["LIFT_TO", "GRASP", "LOWER"]
    step = events(twin, task, "ROBOT_STEP")[1]["data"]
    assert (step["zone"], step["level"], step["embodiment_class"], step["people_present"]) == \
        ("pallet_aisle_1", None, "FORKLIFT", [])
    assert events(twin, task, "LOWERED")[0]["data"]["true_weight_kg"] == 640.0


def test_a_forklift_places_a_pallet_in_a_slot(twin, sim, forklift):
    pallet = twin.add_box(name="PAL-1", kind="PALLET", sku="SKU-01", quantity=40, weight=600.0, position=(8, 3))
    pallet.set_status(BoxStatus.CARRIED)
    forklift.carrying_box = pallet.id
    task = attach(twin, forklift, [
        Action(ActionType.LIFT_TO, "Lift to level 2", level=2, slot_id="PR-08-02-2"),
        Action(ActionType.PLACE, "Place PAL-1", box_id=pallet.id, slot_id="PR-08-02-2"),
        Action(ActionType.LOWER, "Lower"),
    ])
    run(sim, task)
    assert task.status is TaskStatus.COMPLETED and forklift.carrying_box is None
    assert (pallet.status, pallet.slot, pallet.position) == (BoxStatus.STORED, "PR-08-02-2", (8, 2))
    assert twin.stock.location("PR-08-02-2").recorded_qty == 40
    placed = events(twin, task, "PLACED")[0]["data"]
    assert (placed["slot"], placed["level"], placed["true_level"]) == ("PR-08-02-2", 2, 2)


def test_a_wrong_level_placement_reports_the_level_it_was_sent_to(twin, sim, forklift):
    pallet = twin.add_box(name="PAL-1", kind="PALLET", sku="SKU-01", quantity=40, weight=600.0, position=(8, 3))
    pallet.set_status(BoxStatus.CARRIED)
    forklift.carrying_box = pallet.id
    twin.faults.arm("wrong_level")
    task = attach(twin, forklift, [
        Action(ActionType.LIFT_TO, "Lift to level 2", level=2, slot_id="PR-08-02-2"),
        Action(ActionType.PLACE, "Place PAL-1", box_id=pallet.id, slot_id="PR-08-02-2"),
    ])
    run(sim, task)
    placed = events(twin, task, "PLACED")[0]["data"]
    assert (placed["level"], placed["true_level"]) == (2, 3)
    assert (pallet.slot, pallet.true_slot) == ("PR-08-02-2", "PR-08-02-3")
    assert goods.physical_qty(twin, "PR-08-02-2") == 0 and goods.physical_qty(twin, "PR-08-02-3") == 40


def test_steps_fail_their_task_when_the_body_cannot_do_them(twin, sim, forklift):
    amr = twin.add_robot(name="TR50-201", asset_id="AST-000201", position=(10, 13))
    tote = twin.add_box(name="TOTE-1", kind="TOTE", sku="SKU-02", quantity=10, weight=8.0, slot="TS-10-12-2")
    reach = attach(twin, amr, [Action(ActionType.LIFT_TO, "Lift to level 2", level=2, slot_id=tote.slot)])
    run(sim, reach)
    assert reach.status is TaskStatus.FAILED and "level 2 is out of its reach (highest level 1)" in reach.error
    pallet = twin.add_box(name="PAL-1", kind="PALLET", sku="SKU-01", quantity=40, weight=600.0, slot="PR-08-02-3")
    forks = attach(twin, forklift, [Action(ActionType.GRASP, "Grasp PAL-1", box_id=pallet.id, slot_id=pallet.slot)])
    run(sim, forks)
    assert forks.status is TaskStatus.FAILED and "forks are at 0.0 m" in forks.error
    wrong = attach(twin, forklift, [
        Action(ActionType.LIFT_TO, "Lift to level 1", level=1, slot_id="PR-08-02-1"),
        Action(ActionType.GRASP, "Grasp PAL-1", box_id=pallet.id, slot_id="PR-08-02-1"),
    ])
    run(sim, wrong)
    assert wrong.status is TaskStatus.FAILED and "PAL-1 is not in slot PR-08-02-1" in wrong.error
    odd = attach(twin, forklift, [Action(ActionType.WAIT_CLEAR, "Wait", params={"reason": "RAIN", "cell": [20, 15]})])
    run(sim, odd)
    assert odd.status is TaskStatus.FAILED and "can't wait for 'RAIN'" in odd.error


def test_new_job_types_pick_and_deliver_at_their_body_speed(twin, sim, forklift):
    new_kind = Task("task_900", TaskType.MOVE_ROBOT)
    legacy = Task("task_901", TaskType.PICK_AND_DELIVER)
    assert sim._handling_ticks(forklift, new_kind, "GRASP", 4) == 20      # grasp_s 3.0
    assert sim._handling_ticks(forklift, legacy, "GRASP", 4) == 4         # PICK_TICKS, as always
    drone = twin.add_robot(name="IX2-208", asset_id="AST-000208")
    assert sim._handling_ticks(drone, new_kind, "PLACE", 4) == 4          # no place_s: the old timing


def test_an_arm_packs_an_order_and_puts_the_carton_on_the_line(twin, sim):
    arm = twin.add_robot(name="CX10-210", asset_id="AST-000210")
    item = twin.add_box(name="ITEM-1", kind="ITEM", sku="SKU-01", quantity=1, weight=0.5,
                        true_weight_kg=0.6, position=(21, 15), order_id="ORD-1")
    twin.equipment.conveyor.item_at((21, 15)).stop_at = (23, 15)
    task = attach(twin, arm, [
        Action(ActionType.WAIT_CLEAR, "Wait for ORD-1's item", params={
            "reason": "AWAITING_ITEM", "cell": [23, 15], "order_id": "ORD-1"}),
        Action(ActionType.GRASP, "Grasp the item", params={"from_conveyor": [23, 15], "order_id": "ORD-1"}),
        Action(ActionType.PLACE, "Pack it", params={"into_carton": "ORD-1", "lane": "dock_5"}),
        Action(ActionType.WAIT_CLEAR, "Wait for a free cell", params={"reason": "CONVEYOR_OCCUPIED",
                                                                       "cell": [23, 15]}),
        Action(ActionType.PLACE_ON_CONVEYOR, "Carton on the line", target=(23, 15),
               params={"carton_for": "ORD-1"}),
    ], task_type=TaskType.MOVE_ROBOT)
    run(sim, task)
    assert task.status is TaskStatus.COMPLETED, task.error
    assert item.id not in twin.boxes                        # consumed into the carton
    carton = next(box for box in twin.boxes.values() if box.kind is BoxKind.CARTON)
    assert (carton.order_id, carton.destination, carton.quantity) == ("ORD-1", "dock_5", 1)
    assert (carton.declared_weight_kg, carton.true_weight_kg) == (0.8, 0.9)   # the items plus 0.3 kg
    assert twin.equipment.conveyor.item_at((23, 15)).box_id == carton.id
    moves = [(e["data"]["from"], e["data"]["to"]) for e in events(twin, task, "HANDOFF")]
    assert moves == [("conveyor", arm.id), (arm.id, "conveyor")]
    for _ in range(60):
        sim.tick()
    assert twin.warehouse.zone_of_cell(carton.position).key == "dock_5"


def test_a_picker_moves_units_from_a_tote_onto_the_line(twin, sim):
    picker = twin.add_robot(name="PK30-203", asset_id="AST-000203", position=(20, 14))
    tote = twin.add_box(name="TOTE-1", kind="TOTE", sku="SKU-02", quantity=5, weight=6.5, slot="TS-10-12-1")
    tote.position = (19, 14)                                # delivered to Pick 1's tote drop
    tote.set_status(BoxStatus.DELIVERED)
    plan = []
    for _ in range(2):
        plan += [
            Action(ActionType.GRASP, "Pick a unit", params={"unit_from": tote.id, "order_id": "ORD-2"}),
            Action(ActionType.WAIT_CLEAR, "Wait for the infeed", params={"reason": "CONVEYOR_OCCUPIED",
                                                                         "cell": [20, 15]}),
            Action(ActionType.PLACE_ON_CONVEYOR, "Unit on the line", target=(20, 15), params={"stop_at": [23, 15]}),
        ]
    task = attach(twin, picker, plan)
    run(sim, task)
    assert task.status is TaskStatus.COMPLETED, task.error
    items = [box for box in twin.boxes.values() if box.kind is BoxKind.ITEM]
    assert len(items) == 2 and all(item.order_id == "ORD-2" and item.sku == "SKU-02" for item in items)
    assert all(item.declared_weight_kg == 1.0 for item in items)             # (6.5 - 1.5 kg tote) / 5
    assert tote.quantity == 3 and twin.stock.location("TS-10-12-1").true_qty == 3
    placed = [e for e in events(twin, task, "HANDOFF") if e["data"]["to"] == "conveyor"]
    assert len(placed) == 2 and all(e["data"]["from"] == picker.id for e in placed)


def test_a_missed_grasp_is_retried_twice_then_fails(twin, sim):
    picker = twin.add_robot(name="PK30-203", asset_id="AST-000203", position=(20, 14))
    tote = twin.add_box(name="TOTE-1", kind="TOTE", sku="SKU-02", quantity=5, weight=6.5, slot="TS-10-12-1")
    tote.position = (19, 14)
    grasp = [Action(ActionType.GRASP, "Pick a unit", params={"unit_from": tote.id})]
    twin.faults.arm("grasp_fail", count=2)
    retried = attach(twin, picker, grasp)
    run(sim, retried)
    assert retried.status is TaskStatus.COMPLETED and tote.quantity == 4
    assert len([r for r in twin.logger.query(task_id=retried.id) if "missed its grasp" in r["message"]]) == 2
    picker.carrying_box = None
    twin.faults.arm("grasp_fail", count=3)
    failed = attach(twin, picker, [Action(ActionType.GRASP, "Pick a unit", params={"unit_from": tote.id})])
    run(sim, failed)
    assert failed.status is TaskStatus.FAILED and "missed the grasp 3 times" in failed.error
    assert tote.quantity == 4


def test_a_placement_waits_for_its_conveyor_cell(twin, sim):
    picker = twin.add_robot(name="PK30-203", asset_id="AST-000203", position=(20, 14))
    item = twin.add_box(name="ITEM-1", kind="ITEM", weight=0.4, position=(20, 14))
    item.set_status(BoxStatus.CARRIED)
    picker.carrying_box = item.id
    blocker = twin.add_box(name="ITEM-2", kind="ITEM", weight=0.4, position=(20, 15))
    twin.equipment.conveyor.item_at((20, 15)).stop_at = (20, 15)          # parked on the infeed
    task = attach(twin, picker, [Action(ActionType.PLACE_ON_CONVEYOR, "Unit on the line", target=(20, 15))])
    for _ in range(30):
        sim.tick()
    assert picker.status is RobotStatus.WAITING and picker.carrying_box == item.id
    twin.equipment.conveyor.item_at((20, 15)).stop_at = None               # it moves on
    run(sim, task)
    assert task.status is TaskStatus.COMPLETED and blocker.position != (20, 15)


def test_an_arm_pauses_while_the_line_is_jammed_upstream(twin, sim):
    arm = twin.add_robot(name="CX10-210", asset_id="AST-000210")
    twin.equipment.jam((21, 15))
    task = attach(twin, arm, [Action(ActionType.WAIT_CLEAR, "Wait for ORD-1's item", params={
        "reason": "AWAITING_ITEM", "cell": [23, 15], "order_id": "ORD-1"})])
    for _ in range(5):
        sim.tick()
    assert arm.wait_reason == "CONVEYOR_JAMMED" and arm.status is RobotStatus.WAITING
    waits = events(twin, task, "ROBOT_SAFETY_WAIT")
    assert len(waits) == 1 and waits[0]["data"]["cell"] == {"x": 21, "y": 15}
    twin.equipment.clear_jam((21, 15))
    sim.tick()
    assert arm.wait_reason is None and len(events(twin, task, "ROBOT_SAFETY_RESUMED")) == 1
    twin.equipment.jam((24, 15))                              # downstream of the arm: it works on
    sim.tick()
    assert arm.wait_reason is None


def test_a_drone_takes_off_and_lands_on_its_pad(twin, sim):
    drone = twin.add_robot(name="IX2-208", asset_id="AST-000208", position=(20, 2))
    task = attach(twin, drone, [
        Action(ActionType.TAKEOFF, "Take off", params={"altitude_m": 2.0}),
        Action(ActionType.LAND, "Land"),
    ])
    assert (step_ticks(drone.mobility, "TAKEOFF"), step_ticks(drone.mobility, "LAND")) == (27, 34)
    for _ in range(10):
        sim.tick()
    assert (drone.layer, drone.altitude_m, drone.activity) == ("AIR", 2.0, "TAKEOFF")
    run(sim, task)
    assert task.status is TaskStatus.COMPLETED and (drone.layer, drone.altitude_m) == ("GROUND", 0.0)
    assert [e["data"]["step"] for e in events(twin, task, "ROBOT_STEP")] == ["TAKEOFF", "LAND"]
    assert events(twin, task, "ROBOT_STEP")[1]["data"]["layer"] == "AIR"
    drone.position = (18, 2)                                  # not on the pad
    off_pad = attach(twin, drone, [Action(ActionType.TAKEOFF, "Take off")])
    run(sim, off_pad)
    assert off_pad.status is TaskStatus.FAILED and "only take off on the drone pad" in off_pad.error


def test_a_flying_drone_keeps_its_altitude_and_reset_needs_a_free_pad(twin):
    drone = twin.add_robot(name="IX2-208", asset_id="AST-000208", position=(20, 2))
    twin.set_robot_layer(drone.id, "AIR", altitude_m=3.5)
    twin.set_robot_layer(drone.id, "AIR")                     # no altitude given: it keeps its own
    assert drone.altitude_m == 3.5
    drone.position = (12, 2)
    for number, cell in enumerate(twin.warehouse.zones["drone_pad"].cells):
        twin.add_robot(name=f"Pad-{number}", model_code="CT-IX2", position=cell)
    with pytest.raises(ValueError, match="No free drone pad cell"):
        twin.reset_robot(drone.id)
    assert (drone.layer, drone.position) == ("AIR", (12, 2))  # untouched


def test_dropping_at_staging_hands_the_pallet_over(twin, sim):
    forklift = twin.add_robot(name="PF1200-205", asset_id="AST-000205", position=(5, 5))
    pallet = twin.add_box(name="PAL-1", kind="PALLET", sku="SKU-01", quantity=40, weight=600.0, position=(5, 5))
    pallet.set_status(BoxStatus.CARRIED)
    forklift.carrying_box = pallet.id
    task = attach(twin, forklift, [Action(ActionType.DELIVER, "Deliver PAL-1", (5, 5), "Intake staging", pallet.id)])
    ticks = run(sim, task)
    assert ticks == 1 + step_ticks(forklift.mobility, "PLACE") + 1    # a 3 s fork release, not DELIVER_TICKS
    handoff = events(twin, task, "HANDOFF")[0]["data"]
    assert (handoff["from"], handoff["to"], handoff["receiver_observed"]["present"]) == \
        (forklift.id, "intake_staging", True)


def test_classic_robots_log_no_steps(tmp_path):
    classic = DigitalTwin(log_dir=str(tmp_path / "logs"), data_dir=str(tmp_path / "data"),
                          persist_logs=False, demo=True, demo_tasks=False)
    simulator = Simulator(classic)
    classic.simulation_status = SimulationStatus.RUNNING
    task = classic.tasks.create_task({"type": "PICK_AND_DELIVER", "robot_id": "Robo-01", "box_id": "Box-A",
                                      "destination": "loading_zone"})
    run(simulator, task, max_ticks=800)
    assert task.status is TaskStatus.COMPLETED
    assert not classic.events.query(event_type="ROBOT_STEP") and not classic.events.query(event_type="HANDOFF")
    assert classic.events.query(task_id=task.id, event_type="BOX_PICKED")[0]["data"] == {}
