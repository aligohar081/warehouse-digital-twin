"""Integration hardening for plan 1b (multi-embodiment spec §5.5, §6, §9,
§10.1, §11): the seams between the job, rule and shift tasks that wedged a
fault-free shift within minutes.

- A job that ends early leaves nothing in its robot's hands. A mobile robot
  sets its box down where it stands; an arm puts an order item back on its
  working conveyor cell, or the item fails; and a failed customer order sends
  home every tote it took out.
- Plan-time targets on the new floor ignore where other robots stand, so a
  robot in a one-wide aisle makes a tote job wait instead of failing it.
- A traffic wait with no route counts its ticks and escalates: the robot of
  lower precedence makes way (a sidestep, or a retreat to a clear cell), or
  the robot takes another free cell of its target zone. Goal swaps and
  head-ons in a one-lane aisle resolve, and a boxed-in robot announces its
  wait once.
- An idle ground robot left on a cell jobs need moves to parking_area, and
  one that can't is asked again only every SHIFT_CHECK_EVERY_TICKS.
- The new-floor battery estimate prices loaded legs, lifts and handling.
- A customer order claims its pick station and pack cell only once its first
  tote can be had.
- Order-engine cancels are the system's, the fleet reason lists the job's own
  class first, a request that breaks validation fails cleanly, and one broken
  order doesn't stop the others.

Classic robots (no floor profile) behave exactly as before throughout.
"""
import math

import pytest

from backend import jobs
from backend.digital_twin import DigitalTwin
from backend.embodiment import GROUND
from backend.jobs import tote_is_home
from backend.models import CONFIG, BoxStatus, RobotStatus, SimulationStatus, TaskStatus, TaskType
from backend.simulator import Simulator
from backend.task_planner import PlanningError

CHECK = CONFIG["SHIFT_CHECK_EVERY_TICKS"]


def new_floor(tmp_path, name="dc"):
    return DigitalTwin(log_dir=str(tmp_path / name / "logs"), data_dir=str(tmp_path / name / "data"),
                       persist_logs=False, demo=True, demo_tasks=False, layout="distribution_center")


def classic_floor(tmp_path):
    return DigitalTwin(log_dir=str(tmp_path / "classic" / "logs"), data_dir=str(tmp_path / "classic" / "data"),
                       persist_logs=False, demo=True, demo_tasks=False)


def running(twin):
    simulator = Simulator(twin)
    twin.simulation_status = SimulationStatus.RUNNING
    return simulator


@pytest.fixture
def twin(tmp_path):
    return new_floor(tmp_path)


@pytest.fixture
def sim(twin):
    return running(twin)


@pytest.fixture
def populated(twin):
    """The order tests' floor: forklifts, a hauler, two AMRs, the picker, a
    drone, the scout, both arms, and one tote for each of six SKUs."""
    for name, asset, cell in (("PF1200-205", "AST-000205", (7, 4)), ("PF1200-206", "AST-000206", (7, 8)),
                              ("HH300-207", "AST-000207", (2, 8)), ("TR50-201", "AST-000201", (8, 13)),
                              ("TR50-101", "AST-000101", (8, 15)), ("PK30-203", "AST-000203", (20, 14)),
                              ("IX2-208", "AST-000208", (20, 2)), ("SC1-204", "AST-000204", (7, 6))):
        twin.add_robot(name=name, asset_id=asset, position=cell)
    twin.add_robot(name="CX10-210", asset_id="AST-000210")
    twin.add_robot(name="CX10-211", asset_id="AST-000211")
    for number in range(6):
        twin.add_box(name=f"TOTE-{number}", kind="TOTE", sku=f"SKU-00{number + 1}", quantity=20, weight=21.5,
                     slot=f"TS-{8 + number:02d}-12-0")
    return twin


def ticks(sim, count):
    for _ in range(count):
        sim.tick()


def tick_until(sim, condition, max_ticks=3000):
    for _ in range(max_ticks):
        if condition():
            return
        sim.tick()
    assert condition(), f"not reached within {max_ticks} ticks"


def tasks_of(twin, task_type, robot=None):
    return [task for task in twin.tasks.tasks.values() if task.type.value == task_type
            and (robot is None or task.robot_id == robot.id)]


# --------------------------------------------------------------------------- #
# C1: a job that ends early leaves nothing in its robot's hands
# --------------------------------------------------------------------------- #
def amr_carrying_a_tote(twin, sim):
    amr = twin.add_robot(name="TR50-201", asset_id="AST-000201", position=(8, 13))
    tote = twin.add_box(name="TOTE-1", kind="TOTE", sku="SKU-1", quantity=20, weight=21.5, slot="TS-09-12-0")
    job = twin.tasks.create_task({"type": "TOTE_TO_STATION", "box_id": tote.id})
    tick_until(sim, lambda: amr.carrying_box is not None)
    return amr, tote, job


def test_a_cancelled_tote_job_sets_its_tote_down_where_the_robot_stands(twin, sim):
    amr, tote, job = amr_carrying_a_tote(twin, sim)
    twin.tasks.cancel_task(job.id)
    assert amr.carrying_box is None and amr.current_task is None
    assert tote.status is BoxStatus.STORED and tote.position == amr.position
    assert tote.assigned_robot is None and tote.assigned_task is None
    assert tote.slot == "TS-09-12-0" and twin.stock.box_in("TS-09-12-0") == tote.id   # still its home slot
    home = twin.tasks.create_task({"type": "RETURN_TOTE", "box_id": tote.id})
    tick_until(sim, lambda: home.is_terminal)
    assert home.status is TaskStatus.COMPLETED and tote_is_home(twin, tote)


def test_the_robot_takes_its_next_tote_job_instead_of_failing_it(twin, sim):
    amr, tote, job = amr_carrying_a_tote(twin, sim)
    twin.tasks.cancel_task(job.id)
    other = twin.add_box(name="TOTE-2", kind="TOTE", sku="SKU-2", quantity=20, weight=21.5, slot="TS-11-12-0")
    nxt = twin.tasks.create_task({"type": "TOTE_TO_STATION", "box_id": other.id})
    tick_until(sim, lambda: nxt.is_terminal)
    assert nxt.status is TaskStatus.COMPLETED, nxt.error
    assert nxt.robot_id == amr.id and other.position == (19, 14)


def test_a_robot_whose_job_was_cancelled_mid_carry_goes_to_charge_when_low(twin, sim):
    amr, tote, job = amr_carrying_a_tote(twin, sim)
    twin.tasks.cancel_task(job.id)
    amr.battery = 15.0                                           # below BATTERY_LOW
    ticks(sim, 3)
    assert tasks_of(twin, "CHARGE_ROBOT", amr), "an idle robot with a low battery asks to charge"


def test_a_failed_job_sets_down_the_pallet_its_forklift_carried(twin, sim):
    fork = twin.add_robot(name="PF1200-205", asset_id="AST-000205", position=(7, 4))
    pallet = twin.add_box(name="PAL", kind="PALLET", sku="S", quantity=10, weight=300.0, position=(3, 4))
    task = twin.tasks.create_task({"type": "PUTAWAY_PALLET", "box_id": pallet.id})
    tick_until(sim, lambda: fork.carrying_box == pallet.id and fork.position != (3, 4))
    twin.tasks.fail_task(task, "the forklift lost its way")
    assert fork.carrying_box is None
    assert pallet.status is BoxStatus.STORED and pallet.position == fork.position and pallet.slot is None
    assert pallet.assigned_robot is None and pallet.assigned_task is None


def arm_holding_an_item(twin, sim):
    arm = twin.add_robot(name="CX10-210", asset_id="AST-000210")
    work = twin.equipment.arm_cells["pack_cell_1"]
    pack = twin.tasks.create_task({"type": "PACK_ORDER", "order_id": "ORD-9", "quantity": 2,
                                   "pack_cell": "pack_cell_1", "lane": "dock_4"})
    item = twin.add_box(name="ITEM-1", kind="ITEM", position=arm.position, sku="SKU-1", quantity=1,
                        weight=0.5, order_id="ORD-9")
    twin.equipment.place(item, work, "test", order_id="ORD-9", stop_at=work)   # held on the arm's cell
    tick_until(sim, lambda: arm.carrying_box == item.id)
    return arm, pack, item, work


def test_an_arm_whose_pack_ends_early_puts_the_item_back_on_its_cell(twin, sim):
    arm, pack, item, work = arm_holding_an_item(twin, sim)
    twin.tasks.cancel_task(pack.id)
    assert arm.carrying_box is None
    held = twin.equipment.conveyor.item_at(work)
    assert held is not None and held.box_id == item.id and held.stop_at == work   # waiting there for a retry
    assert item.status is BoxStatus.DELIVERING and item.position == work


def test_an_arm_whose_cell_is_taken_fails_the_item_it_held(twin, sim):
    arm, pack, item, work = arm_holding_an_item(twin, sim)
    other = twin.add_box(name="ITEM-2", kind="ITEM", position=arm.position, sku="SKU-1", quantity=1,
                         weight=0.5, order_id="ORD-9")
    twin.equipment.place(other, work, "test", order_id="ORD-9", stop_at=work)
    twin.tasks.fail_task(pack, "the arm lost power")
    assert arm.carrying_box is None and item.status is BoxStatus.FAILED
    assert twin.equipment.conveyor.item_at(work).box_id == other.id


def test_a_classic_robot_keeps_its_box_when_its_job_is_cancelled(tmp_path):
    classic = classic_floor(tmp_path)
    simulator = running(classic)
    robot = classic.find_robot("Robo-01")
    task = classic.tasks.create_task({"type": "PICK_AND_DELIVER", "robot_id": robot.id, "box_id": "Box-A",
                                      "destination": "loading_zone"})
    tick_until(simulator, lambda: robot.carrying_box is not None, 500)
    classic.tasks.cancel_task(task.id)
    assert robot.carrying_box is not None                        # exactly as before
    assert classic.find_box("Box-A").status is BoxStatus.CARRIED


def test_a_failed_customer_order_sends_its_tote_home_from_the_station(populated, sim):
    twin = populated
    order = twin.shift.orders.customer([{"sku": "SKU-002", "units": 2}], "dock_4")
    tote = twin.find_box("TOTE-1")
    tick_until(sim, lambda: order.stages[1].status == "ACTIVE")
    assert tote.position == (19, 14)                             # on Pick 1's tote drop
    twin.shift.orders._fail(order, "the test failed it")
    back = tasks_of(twin, "RETURN_TOTE")
    assert len(back) == 1 and back[0].box_id == tote.id and back[0].internal
    assert back[0].status is not TaskStatus.FAILED, back[0].error
    tick_until(sim, lambda: tote_is_home(twin, tote))


def test_a_failed_customer_order_returns_a_tote_set_down_mid_carry(populated, sim):
    twin = populated
    order = twin.shift.orders.customer([{"sku": "SKU-002", "units": 1}], "dock_4")
    tote = twin.find_box("TOTE-1")
    tick_until(sim, lambda: tote.status is BoxStatus.CARRIED and tote.position != (9, 13))
    carrier = next(robot for robot in twin.robots.values() if robot.carrying_box == tote.id)
    twin.shift.orders._fail(order, "the test failed it")
    assert carrier.carrying_box is None and tote.status is BoxStatus.STORED and tote.position == carrier.position
    assert [task.box_id for task in tasks_of(twin, "RETURN_TOTE")] == [tote.id]
    tick_until(sim, lambda: tote_is_home(twin, tote))
    assert not [robot.name for robot in twin.robots.values() if robot.current_task is None and robot.carrying_box]


def test_a_failed_order_queues_no_return_for_a_tote_at_home_or_on_its_way(populated, sim):
    twin = populated
    early = twin.shift.orders.customer([{"sku": "SKU-002", "units": 1}], "dock_4")
    twin.shift.orders.advance()                                  # its tote job is created, the tote still home
    twin.shift.orders._fail(early, "failed before the tote left")
    assert not tasks_of(twin, "RETURN_TOTE")
    late = twin.shift.orders.customer([{"sku": "SKU-003", "units": 1}], "dock_5")
    tote = twin.find_box("TOTE-2")
    tick_until(sim, lambda: late.stages[2].status == "ACTIVE")  # its own RETURN_TOTE is under way
    twin.shift.orders._fail(late, "failed while the tote went home")
    back = tasks_of(twin, "RETURN_TOTE")
    assert len(back) == 1 and back[0].box_id == tote.id and not back[0].is_terminal   # left to finish
    tick_until(sim, lambda: back[0].is_terminal)
    assert back[0].status is TaskStatus.COMPLETED and tote_is_home(twin, tote)


# --------------------------------------------------------------------------- #
# C2: plan-time targets ignore where other robots stand
# --------------------------------------------------------------------------- #
def test_robots_in_the_aisle_do_not_fail_a_tote_job_at_plan_time(twin, sim):
    # TS-11-12-0's only face is (11,13) in the one-wide tote_aisle_1; robots at
    # (8,13) and (14,13) cut every route to it while the job is planned.
    amr = twin.add_robot(name="TR50-101", asset_id="AST-000101", position=(19, 16))
    twin.add_robot(name="TR50-201", asset_id="AST-000201", position=(14, 13))
    twin.add_robot(name="Aisle-West", model_code="AC-TR50", position=(8, 13))
    tote = twin.add_box(name="TOTE-2", kind="TOTE", sku="SKU-2", quantity=20, weight=21.5, slot="TS-11-12-0")
    job = twin.tasks.create_task({"type": "TOTE_TO_STATION", "box_id": tote.id, "robot_id": amr.id})
    sim.tick()
    assert job.status is not TaskStatus.FAILED, job.error
    assert job.actions[0].target == (11, 13)
    tick_until(sim, lambda: job.is_terminal)                     # it waits, then goes once the aisle clears
    assert job.status is TaskStatus.COMPLETED, job.error


def test_a_face_the_layout_cannot_reach_still_fails_with_its_reason(twin):
    forklift = twin.add_robot(name="PF1200-205", asset_id="AST-000205", position=(7, 4))
    slot = twin.warehouse.slot("TS-11-12-0")                     # a narrow aisle a wide forklift can't use
    with pytest.raises(PlanningError, match="No face of slot TS-11-12-0 is reachable"):
        jobs.face_cell(twin.planner, slot, forklift.position, forklift.mobility, GROUND)


def test_classic_planning_still_steers_a_drop_around_a_robot(tmp_path):
    classic = classic_floor(tmp_path)
    robot, other = classic.find_robot("Robo-01"), classic.find_robot("Robo-02")
    payload = {"type": "PICK_AND_DELIVER", "robot_id": robot.id, "box_id": "Box-A", "destination": "loading_zone"}
    free = classic.planner.plan(classic.tasks.create_task(payload), robot)[2].target
    other.position = free                                        # stand on the cell it would have chosen
    classic.tasks.cancel_task(next(t.id for t in classic.tasks.tasks.values() if not t.is_terminal))
    steered = classic.planner.plan(classic.tasks.create_task(payload), robot)[2].target
    assert steered != free


# --------------------------------------------------------------------------- #
# C3 (a): a traffic wait with no route escalates
# --------------------------------------------------------------------------- #
def test_two_forklifts_swapping_cells_both_get_there(twin, sim):
    a = twin.add_robot(name="PF1200-205", asset_id="AST-000205", position=(8, 7))
    b = twin.add_robot(name="PF1200-206", asset_id="AST-000206", position=(4, 7))
    to_b = twin.tasks.create_task({"type": "MOVE_ROBOT", "robot_id": a.id, "destination": "4,7"})
    to_a = twin.tasks.create_task({"type": "MOVE_ROBOT", "robot_id": b.id, "destination": "8,7"})
    tick_until(sim, lambda: to_b.is_terminal and to_a.is_terminal, 1500)
    assert (to_b.status, to_a.status) == (TaskStatus.COMPLETED, TaskStatus.COMPLETED), (to_b.error, to_a.error)


def test_a_no_route_wait_counts_its_ticks_against_the_robot_in_the_way(twin, sim):
    a = twin.add_robot(name="PF1200-205", asset_id="AST-000205", position=(8, 7))
    b = twin.add_robot(name="PF1200-206", asset_id="AST-000206", position=(4, 7))
    twin.stop_robot(b.id)                                        # it stays on the goal cell
    twin.tasks.create_task({"type": "MOVE_ROBOT", "robot_id": a.id, "destination": "4,7"})
    ticks(sim, 5)
    assert a.status is RobotStatus.WAITING and a.blocked_by == b.id and a.wait_ticks >= 3


#: Head-on swaps in the one-wide tote_aisle_1: each robot's goal is the cell
#: the other stands on, so neither has a route while the other is there.
HEAD_ON_SWAPS = [((10, 13), (11, 13)),              # side by side, mid-aisle
                 ((9, 13), (14, 13)),               # across the aisle
                 ((15, 13), (16, 13))]              # beside the walkway crossing at (17,13)


@pytest.mark.parametrize("west_first", [True, False], ids=["west robot first", "east robot first"])
@pytest.mark.parametrize("west_cell, east_cell", HEAD_ON_SWAPS)
def test_a_head_on_swap_in_a_one_lane_aisle_resolves(twin, sim, west_first, west_cell, east_cell):
    if west_first:                                               # the robot created first has the lower id
        west = twin.add_robot(name="TR50-201", asset_id="AST-000201", position=west_cell)
        east = twin.add_robot(name="TR50-101", asset_id="AST-000101", position=east_cell)
    else:
        east = twin.add_robot(name="TR50-101", asset_id="AST-000101", position=east_cell)
        west = twin.add_robot(name="TR50-201", asset_id="AST-000201", position=west_cell)
    going_east = twin.tasks.create_task({"type": "MOVE_ROBOT", "robot_id": west.id,
                                         "destination": f"{east_cell[0]},{east_cell[1]}"})
    going_west = twin.tasks.create_task({"type": "MOVE_ROBOT", "robot_id": east.id,
                                         "destination": f"{west_cell[0]},{west_cell[1]}"})
    tick_until(sim, lambda: going_east.is_terminal and going_west.is_terminal, 3000)
    assert (going_east.status, going_west.status) == (TaskStatus.COMPLETED, TaskStatus.COMPLETED), \
        (going_east.error, going_west.error)


def test_when_the_robot_that_should_make_way_cannot_the_other_one_does(twin, sim):
    # A swap at the dead end of pallet_aisle_1: the first robot (the lower id,
    # so it would make way) is boxed into the corner, so the second one steps aside.
    cornered = twin.add_robot(name="PF1200-205", asset_id="AST-000205", position=(16, 3))
    other = twin.add_robot(name="PF1200-206", asset_id="AST-000206", position=(15, 3))
    wall = twin.add_robot(name="Corner", model_code="AC-TR50", position=(16, 4))
    twin.stop_robot(wall.id)
    out = twin.tasks.create_task({"type": "MOVE_ROBOT", "robot_id": cornered.id, "destination": "15,3"})
    into = twin.tasks.create_task({"type": "MOVE_ROBOT", "robot_id": other.id, "destination": "16,3"})
    tick_until(sim, lambda: out.is_terminal and into.is_terminal, 3000)
    assert (out.status, into.status) == (TaskStatus.COMPLETED, TaskStatus.COMPLETED), (out.error, into.error)
    resolved = [e for e in twin.events.query(event_type="DEADLOCK_RESOLVED") if e["robot_id"] == other.id]
    assert resolved, "the robot with room to move made way"


def test_a_drop_cell_held_up_by_a_robot_is_swapped_for_another_free_cell_of_the_zone(twin, sim):
    hauler = twin.add_robot(name="HH300-207", asset_id="AST-000207", position=(2, 8))
    pallet = twin.add_box(name="PAL", kind="PALLET", sku="S", quantity=10, weight=200.0, position=(2, 9))
    task = twin.tasks.create_task({"type": "UNLOAD_TRUCK", "box_id": pallet.id})
    tick_until(sim, lambda: bool(task.actions))
    planned = task.actions[2].target                              # the intake_staging cell it chose
    squatter = twin.add_robot(name="Squatter", model_code="AC-TR50", position=planned)
    twin.stop_robot(squatter.id)                                  # it never leaves
    tick_until(sim, lambda: task.is_terminal, 3000)
    assert task.status is TaskStatus.COMPLETED, task.error
    assert pallet.position != planned and twin.warehouse.zone_of_cell(pallet.position).key == "intake_staging"


def test_a_boxed_in_robot_announces_its_wait_once(twin, sim):
    forklift = twin.add_robot(name="PF1200-205", asset_id="AST-000205", position=(16, 3))   # a dead end
    twin.tasks.create_task({"type": "MOVE_ROBOT", "robot_id": forklift.id, "destination": "9,3"})
    sim.tick()                                                   # its route west is planned
    for name, cell in (("Box-In-1", (15, 3)), ("Box-In-2", (16, 4))):
        robot = twin.add_robot(name=name, model_code="AC-TR50", position=cell)
        twin.stop_robot(robot.id)                                # they never move
    ticks(sim, 10 * CONFIG["DEADLOCK_WAIT_TICKS"])
    mine = lambda kind: [e for e in twin.events.query(event_type=kind, limit=10000) if e["robot_id"] == forklift.id]
    assert forklift.position == (16, 3)
    assert len(mine("COLLISION_AVOIDED")) == 1 and len(mine("ROBOT_WAITING")) == 1


# --------------------------------------------------------------------------- #
# C3 (b): an idle robot moves off a cell jobs need
# --------------------------------------------------------------------------- #
def busy_elsewhere(twin):
    """Work on the floor: a forklift charging at the charging station (a long job)."""
    fork = twin.add_robot(name="PF1200-205", asset_id="AST-000205", position=(2, 13))
    fork.battery = 10.0
    return twin.request_charge(fork.id)


@pytest.mark.parametrize("cell", [(19, 16), (12, 13), (10, 1), (10, 3)],
                         ids=["tote drop", "tote aisle", "one-lane top aisle", "pallet face"])
def test_an_idle_robot_on_a_cell_jobs_need_moves_to_parking(twin, sim, cell):
    busy_elsewhere(twin)
    amr = twin.add_robot(name="TR50-101", asset_id="AST-000101", position=cell)
    ticks(sim, CHECK + 2)
    moves = tasks_of(twin, "MOVE_ROBOT", amr)
    assert len(moves) == 1 and moves[0].internal and moves[0].destination == "parking_area"
    tick_until(sim, lambda: moves[0].is_terminal)
    assert moves[0].status is TaskStatus.COMPLETED, moves[0].error
    assert twin.warehouse.zone_of_cell(amr.position).key == "parking_area"
    ticks(sim, 3 * CHECK)
    assert len(tasks_of(twin, "MOVE_ROBOT", amr)) == 1            # parked: it stays


def test_with_no_job_on_the_floor_an_idle_robot_stays_where_it_is(twin, sim):
    amr = twin.add_robot(name="TR50-101", asset_id="AST-000101", position=(12, 13))
    ticks(sim, 3 * CHECK)
    assert not tasks_of(twin, "MOVE_ROBOT") and amr.position == (12, 13)


def test_robots_off_the_cells_jobs_need_stay_put(twin, sim):
    charge = busy_elsewhere(twin)
    staged = twin.add_robot(name="TR50-101", asset_id="AST-000101", position=(5, 5))       # intake staging
    picker = twin.add_robot(name="PK30-203", asset_id="AST-000203", position=(20, 14))     # its own work cell
    twin.add_robot(name="IX2-208", asset_id="AST-000208", position=(20, 2))                # a drone on its pad
    twin.add_robot(name="CX10-210", asset_id="AST-000210")                                 # an arm
    stopped = twin.add_robot(name="TR50-201", asset_id="AST-000201", position=(10, 13))
    twin.stop_robot(stopped.id)                                  # halted in the aisle: not idle
    ticks(sim, 3 * CHECK)
    assert not charge.is_terminal and not tasks_of(twin, "MOVE_ROBOT")
    assert staged.position == (5, 5) and picker.position == (20, 14) and stopped.position == (10, 13)


def test_a_robot_that_cannot_park_asks_again_only_every_check(twin, sim):
    busy_elsewhere(twin)
    for number, cell in enumerate(twin.warehouse.zones["parking_area"].cells):
        twin.add_robot(name=f"Parked-{number}", model_code="AC-TR50", position=cell)       # parking is full
    amr = twin.add_robot(name="TR50-101", asset_id="AST-000101", position=(12, 13))
    ticks(sim, 5 * CHECK)
    moves = tasks_of(twin, "MOVE_ROBOT", amr)
    assert 1 <= len(moves) <= 5 and all(task.status is TaskStatus.FAILED for task in moves)
    assert amr.position == (12, 13)


def test_classic_robots_never_go_parking(tmp_path):
    classic = classic_floor(tmp_path)
    simulator = running(classic)
    ticks(simulator, 3 * CHECK)
    assert not tasks_of(classic, "MOVE_ROBOT")


# --------------------------------------------------------------------------- #
# I1: the new-floor battery estimate
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("slot, weight", [("PR-08-02-0", 300.0), ("PR-08-02-4", 1100.0), ("PR-16-05-4", 1100.0)])
def test_a_putaway_estimate_is_within_five_percent_of_its_real_drain(twin, sim, slot, weight):
    fork = twin.add_robot(name="PF1200-205", asset_id="AST-000205", position=(7, 4))
    pallet = twin.add_box(name="PAL", kind="PALLET", sku="S", quantity=10, weight=weight, position=(3, 4))
    before = fork.battery
    task = twin.tasks.create_task({"type": "PUTAWAY_PALLET", "box_id": pallet.id, "slot": slot})
    tick_until(sim, lambda: task.is_terminal, 5000)
    assert task.status is TaskStatus.COMPLETED, task.error
    used = before - fork.battery
    estimate = task.battery_estimate - CONFIG["BATTERY_RESERVE"]
    assert estimate == pytest.approx(used, rel=0.05), (estimate, used)


def test_the_classic_estimate_is_unchanged(tmp_path):
    classic = classic_floor(tmp_path)
    robot = classic.find_robot("Robo-01")
    task = classic.tasks.create_task({"type": "PICK_AND_DELIVER", "robot_id": robot.id, "box_id": "Box-A",
                                      "destination": "loading_zone"})
    actions = classic.planner.plan(task, robot)
    nav = classic.navigation
    cells = nav.distance(robot.position, actions[0].target) + nav.distance(actions[0].target, actions[2].target)
    assert task.battery_estimate == float(math.ceil(cells / CONFIG["BATTERY_DRAIN_MOVES"])
                                          + 2 * CONFIG["BATTERY_PICK_COST"] + CONFIG["BATTERY_RESERVE"])


# --------------------------------------------------------------------------- #
# I2: a customer order holds a station only once its first tote can be had
# --------------------------------------------------------------------------- #
def test_an_order_whose_first_tote_is_busy_holds_no_station_or_cell(populated):
    twin = populated
    twin.tasks.create_task({"type": "TOTE_TO_STATION", "box_id": "TOTE-1"})     # another job holds SKU-002's tote
    waiting = twin.shift.orders.customer([{"sku": "SKU-002", "units": 1}], "dock_4")
    ready = twin.shift.orders.customer([{"sku": "SKU-003", "units": 1}], "dock_5")
    twin.shift.orders.advance()
    assert (waiting.status, waiting.pick_station, waiting.pack_cell) == ("OPEN", None, None)
    assert (ready.status, ready.pick_station) == ("IN_PROGRESS", "pick_station_1")


# --------------------------------------------------------------------------- #
# Folded minors
# --------------------------------------------------------------------------- #
def test_an_order_engine_cancel_is_the_systems_and_a_user_cancel_stays_the_users(populated):
    twin = populated
    order = twin.shift.orders.customer([{"sku": "SKU-002", "units": 1}], "dock_4")
    twin.shift.orders.advance()
    leg = twin.tasks.get(order.stages[0].task_id)
    twin.shift.orders._fail(order, "the test failed it")
    event = [e for e in twin.events.query(event_type="TASK_CANCELLED") if e["task_id"] == leg.id][-1]
    assert event["message"] == f"{leg.id} cancelled: order {order.order_id} failed"
    assert event["category"] == "TASK"
    assert leg.history[-1]["message"] == f"Cancelled: order {order.order_id} failed"
    robot = twin.find_robot("TR50-201")
    mine = twin.tasks.create_task({"type": "MOVE_ROBOT", "robot_id": robot.id, "destination": "5,5"})
    twin.tasks.cancel_task(mine.id)
    event = twin.events.query(event_type="TASK_CANCELLED")[-1]
    assert (event["message"], event["category"]) == (f"{mine.id} cancelled by user", "USER")
    assert mine.history[-1]["message"] == "Cancelled by user"
