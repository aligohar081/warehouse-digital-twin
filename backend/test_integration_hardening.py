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
