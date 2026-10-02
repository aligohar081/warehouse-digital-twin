"""Shift engine and order hardening (multi-embodiment spec §11.1, §11.2): a
retried stage never waits on itself and the order fails with the reason once
its retry is spent; a pick or pack retried after partial progress asks only for
what is left; pausing the shift stops generation without a burst on resume, and
so does restoring a stopped stream; a bad pace or rate is refused; a failing
shift engine doesn't abort the tick; an order's created event keeps what it
recorded."""
import copy
import math

import pytest

from backend import people
from backend.digital_twin import DigitalTwin
from backend.models import ActionType, BoxKind, SimulationStatus, TaskStatus
from backend.operations.orders import ORDER_RETRY_DELAY_S
from backend.operations.shift import DEFAULT_RATES, FIRST_AT_S
from backend.simulator import Simulator

#: Bounds, well above what each chain takes, so a stall fails a test instead of hanging it.
MAX_TICKS = 4000

ROBOTS = {
    "forklifts": (("PF1200-205", "AST-000205", (7, 4)), ("PF1200-206", "AST-000206", (7, 8))),
    "hauler": (("HH300-207", "AST-000207", (2, 8)),),
    "amrs": (("TR50-201", "AST-000201", (8, 13)), ("TR50-101", "AST-000101", (8, 15))),
    "picker": (("PK30-203", "AST-000203", (20, 14)),),
    "drone": (("IX2-208", "AST-000208", (20, 2)),),
    "scout": (("SC1-204", "AST-000204", (7, 6)),),
}


def make_twin(tmp_path, robots=tuple(ROBOTS)):
    """The distribution centre with the groups of `robots` on it, both arms,
    and one tote of each of six SKUs (SKU-002 is held by TOTE-1 alone)."""
    twin = DigitalTwin(log_dir=str(tmp_path / "logs"), data_dir=str(tmp_path / "data"),
                       persist_logs=False, demo=True, demo_tasks=False, layout="distribution_center")
    for group in robots:
        for name, asset, cell in ROBOTS[group]:
            twin.add_robot(name=name, asset_id=asset, position=cell)
    twin.add_robot(name="CX10-210", asset_id="AST-000210")
    twin.add_robot(name="CX10-211", asset_id="AST-000211")
    for number in range(6):
        twin.add_box(name=f"TOTE-{number}", kind="TOTE", sku=f"SKU-00{number + 1}", quantity=20, weight=21.5,
                     slot=f"TS-{8 + number:02d}-12-0")
    return twin


@pytest.fixture
def twin(tmp_path):
    return make_twin(tmp_path)


@pytest.fixture
def sim(twin):
    simulator = Simulator(twin)
    twin.simulation_status = SimulationStatus.RUNNING
    return simulator


def run(sim, *orders, max_ticks=MAX_TICKS):
    for _ in range(max_ticks):
        if all(order.is_terminal for order in orders):
            return
        sim.tick()
    stalled = [(o.order_id, o.status, [(s.name, s.status, s.attempts, s.error) for s in o.stages
                                       if s.status != "DONE"]) for o in orders if not o.is_terminal]
    assert not stalled, stalled


def tick_until(sim, condition, max_ticks=MAX_TICKS):
    for _ in range(max_ticks):
        if condition():
            return
        sim.tick()
    assert condition(), "never happened"


def tasks_of(twin, task_type):
    return [task for task in twin.tasks.tasks.values() if task.type.value == task_type]


def placed_on_line(task):
    return sum(1 for action in task.actions if action.type is ActionType.PLACE_ON_CONVEYOR and action.done)


def carton_of(twin, order):
    return next((box for box in twin.boxes.values() if box.kind is BoxKind.CARTON and box.order_id == order.order_id),
                None)


TWO_UNITS = [{"sku": "SKU-002", "units": 2}]


# ---- a retried stage never waits on itself ---------------------------------- #
def test_a_tote_stage_with_no_robot_for_it_fails_the_order_after_its_retry(tmp_path):
    twin = make_twin(tmp_path, robots=("picker",))                  # a picker and the arms, but no AMR
    sim = Simulator(twin)
    twin.simulation_status = SimulationStatus.RUNNING
    order = twin.shift.orders.customer(TWO_UNITS, "dock_5")
    run(sim, order, max_ticks=int((ORDER_RETRY_DELAY_S + 5) / sim.dt) + 50)
    stage = order.stages[0]
    assert (order.status, stage.status, stage.attempts) == ("FAILED", "FAILED", 2)
    assert order.failure_reason == stage.error
    assert order.failure_reason.startswith("No robot can do this TOTE_TO_STATION")
    assert twin.shift.orders._busy("pick_station") == [] and twin.shift.orders._busy("pack_cell") == []
    follower = twin.shift.orders.customer(TWO_UNITS, "dock_4")
    assert twin.shift.orders._acquire(follower)                      # the station and the cell are free again


def test_a_failed_tote_stage_is_retried_with_its_own_tote(twin, sim):
    order = twin.shift.orders.customer(TWO_UNITS, "dock_5")
    orders = twin.shift.orders
    orders.advance()
    stage = order.stages[0]
    first = twin.tasks.get(stage.task_id)
    twin.tasks.fail_task(first, "the AMR lost its way")
    orders.advance()
    assert (stage.status, stage.attempts, stage.task_id) == ("WAITING", 1, None)
    twin.simulation_time += ORDER_RETRY_DELAY_S
    orders.advance()
    assert (stage.status, stage.attempts) == ("ACTIVE", 2) and stage.task_id != first.id
    assert twin.tasks.get(stage.task_id).box_id == first.box_id      # the same tote again
    run(sim, order)
    assert order.status == "DONE", order.failure_reason


# ---- a pick or pack retried after partial progress asks for what is left ---- #
def test_a_pick_retried_after_one_unit_asks_for_the_remainder(twin, sim):
    order = twin.shift.orders.customer(TWO_UNITS, "dock_5")
    pick = order.stages[1]

    def one_unit_on_the_line():
        task = twin.tasks.get(pick.task_id) if pick.task_id else None
        return task is not None and placed_on_line(task) == 1

    tick_until(sim, one_unit_on_the_line)
    twin.faults.arm("grasp_fail", 3)                                  # the picker misses unit 2 three times
    run(sim, order)
    assert order.status == "DONE", (order.failure_reason, [(s.name, s.status, s.error) for s in order.stages])
    picks = sorted(tasks_of(twin, "PICK_ITEMS"), key=lambda task: task.created_at)
    assert [(task.status, task.params["quantity"]) for task in picks] == [(TaskStatus.FAILED, 2),
                                                                         (TaskStatus.COMPLETED, 1)]
    assert order.lines[0]["placed"] == 1 and pick.attempts == 2
    assert twin.find_box("TOTE-1").quantity == 18                     # two units left the tote, not three
    carton = carton_of(twin, order)
    assert carton.quantity == 2 and twin.warehouse.zone_of_cell(carton.position).key == "dock_5"
    assert not [box for box in twin.boxes.values() if box.kind is BoxKind.ITEM]    # no surplus on the line


def test_a_pick_that_failed_with_every_unit_placed_is_done_not_repeated(twin, sim):
    order = twin.shift.orders.customer(TWO_UNITS, "dock_5")
    pick = order.stages[1]
    tick_until(sim, lambda: pick.status == "ACTIVE")
    task = twin.tasks.get(pick.task_id)
    for action in task.actions:
        if action.type is ActionType.PLACE_ON_CONVEYOR:
            action.done = True                                        # both units are on the line
    twin.tasks.fail_task(task, "it lost track of the end")
    twin.shift.orders.advance()
    twin.simulation_time += ORDER_RETRY_DELAY_S
    twin.shift.orders.advance()
    assert pick.status == "DONE" and len(tasks_of(twin, "PICK_ITEMS")) == 1    # nothing more asked of the picker
    assert order.stages[2].status == "ACTIVE"                         # the tote goes home


def test_a_manual_pick_retried_after_one_unit_asks_for_the_remainder(tmp_path):
    twin = make_twin(tmp_path, robots=("amrs",))                      # no picker robot: Pick 2, a person's station
    sim = Simulator(twin)
    twin.simulation_status = SimulationStatus.RUNNING
    sam = twin.add_operator(name="Sam", worker_id="E-10001")
    people.place(twin, sam, "pick_station_2")
    order = twin.shift.orders.customer(TWO_UNITS, "dock_5")
    pick = order.stages[1]

    def one_unit_picked():
        task = twin.tasks.get(pick.task_id) if pick.task_id else None
        return task is not None and task.type.value == "MANUAL_PICK" and task.params.get("picked") == 1

    tick_until(sim, one_unit_picked)
    twin.tasks.fail_task(twin.tasks.get(pick.task_id), "Sam was called away")
    run(sim, order)
    assert order.status == "DONE", (order.failure_reason, [(s.name, s.status, s.error) for s in order.stages])
    picks = sorted(tasks_of(twin, "MANUAL_PICK"), key=lambda task: task.created_at)
    assert [task.params["quantity"] for task in picks] == [2, 1]
    assert twin.find_box("TOTE-1").quantity == 18 and carton_of(twin, order).quantity == 2


def test_a_pack_retried_after_one_item_resumes_the_same_carton_with_the_remainder(twin, sim):
    order = twin.shift.orders.customer(TWO_UNITS, "dock_5")
    pick = order.stages[1]

    def one_packed_and_all_picked():
        carton = carton_of(twin, order)
        return carton is not None and carton.quantity == 1 and pick.status == "DONE"

    tick_until(sim, one_packed_and_all_picked)
    first_carton = carton_of(twin, order)
    twin.faults.arm("grasp_fail", 3)                                  # the arm misses item 2 three times
    run(sim, order)
    assert order.status == "DONE", (order.failure_reason, [(s.name, s.status, s.error) for s in order.stages])
    packs = sorted(tasks_of(twin, "PACK_ORDER"), key=lambda task: task.created_at)
    assert [(task.status, task.params["quantity"]) for task in packs] == [(TaskStatus.FAILED, 2),
                                                                         (TaskStatus.COMPLETED, 1)]
    cartons = [box for box in twin.boxes.values() if box.kind is BoxKind.CARTON and box.order_id == order.order_id]
    assert cartons == [first_carton] and first_carton.quantity == 2   # one carton, both items, none twice
    assert order.sorted_to == "dock_5" and not [b for b in twin.boxes.values() if b.kind is BoxKind.ITEM]


def test_a_pack_that_failed_with_everything_packed_fails_the_order_instead_of_repeating(twin, sim):
    order = twin.shift.orders.customer(TWO_UNITS, "dock_5")
    pack = order.stages[3]

    def everything_packed():
        carton = carton_of(twin, order)
        return carton is not None and carton.quantity == 2 and pack.status == "ACTIVE"

    tick_until(sim, everything_packed)
    twin.tasks.fail_task(twin.tasks.get(pack.task_id), "the carton would not go onto the line")
    twin.shift.orders.advance()
    assert order.status == "FAILED" and pack.status == "FAILED" and pack.attempts == 1    # no second PACK_ORDER
    assert "already packed" in order.failure_reason and "the carton would not go onto the line" in order.failure_reason
    assert len(tasks_of(twin, "PACK_ORDER")) == 1
    assert twin.shift.orders._busy("pick_station") == [] and twin.shift.orders._busy("pack_cell") == []


# ---- pausing stops generation, and nothing bursts on the way back ----------- #
def fire_times(twin, start, end):
    """The simulation seconds each stream fired in [start, end], one engine tick a second."""
    fired = {stream: [] for stream in DEFAULT_RATES}
    for second in range(int(start), int(end) + 1):
        before = dict(twin.shift.counters)
        twin.simulation_time = float(second)
        twin.shift.tick()
        for stream in fired:
            fired[stream] += [float(second)] * (twin.shift.counters[stream] - before[stream])
    return fired


def test_resuming_after_a_pause_picks_up_where_it_left_off_with_no_burst(twin):
    engine = twin.shift
    engine.start()
    assert sum(len(times) for times in fire_times(twin, 0, 300).values()) > 0
    due_before = dict(engine._next_due)
    engine.pause()
    twin.simulation_time = 300.0 + 3600.0                              # an hour passes while it is paused
    engine.start()
    assert sum(len(times) for times in fire_times(twin, 3900, 3940).values()) == 0   # nothing came due in a burst
    resumed = fire_times(twin, 3941, 3900 + 700)
    for stream in ("customer_orders", "patrols", "cycle_counts"):
        assert resumed[stream][0] - 3900 == pytest.approx(due_before[stream] - 300, abs=1.0), stream
    customers = resumed["customer_orders"]
    assert len(customers) >= 4 and {b - a for a, b in zip(customers, customers[1:])} == {120.0}   # the normal cadence


def test_a_paused_shift_generates_nothing(twin):
    engine = twin.shift
    engine.start()
    engine.pause()
    counters = dict(engine.counters)
    assert sum(len(times) for times in fire_times(twin, 0, 7200).values()) == 0
    assert engine.counters == counters


def test_a_stream_set_to_zero_and_restored_does_not_burst(twin):
    engine = twin.shift
    engine.start()
    engine.configure(rates={"customer_orders": 0})
    assert fire_times(twin, 0, 1000)["customer_orders"] == []          # it would have fired eight times
    engine.configure(rates={"customer_orders": 30})
    after = fire_times(twin, 1001, 1001 + 600)["customer_orders"]
    assert len(after) >= 4 and after[0] <= 1001 + 1                    # one at the restore, not eight
    assert {b - a for a, b in zip(after, after[1:])} <= {119.0, 120.0}  # then the normal 120 s cadence (one tick's slip)


# ---- configure, the tick, the created event --------------------------------- #
@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_configure_refuses_a_pace_or_rate_that_is_not_finite(twin, bad):
    engine = twin.shift
    with pytest.raises(ValueError, match="pace"):
        engine.configure(pace=bad)
    with pytest.raises(ValueError, match="patrols"):
        engine.configure(rates={"patrols": bad})
    assert engine.config() == {"seed": 42, "pace": 1.0, "rates": DEFAULT_RATES}
    assert math.isfinite(engine.pace)


def test_a_failing_shift_engine_does_not_abort_the_tick(twin, sim):
    def broken():
        raise RuntimeError("the generator broke")

    twin.shift.tick = broken
    scout = next(robot for robot in twin.robots.values() if robot.name == "SC1-204")
    task = twin.tasks.create_task({"type": "PATROL"}, internal=True)
    assert task.status is not TaskStatus.FAILED
    ticks = twin.tick_count
    for _ in range(5):
        sim.tick()                                                     # does not raise
    assert twin.tick_count == ticks + 5
    assert task.status is TaskStatus.IN_PROGRESS and scout.current_task == task.id    # dispatch and robots still ran
    logged = twin.logger.query(category="OPERATIONS", level="ERROR", search="shift engine")
    assert logged and "the generator broke" in logged[0]["message"]


def test_the_order_created_event_keeps_what_it_recorded(twin):
    order = twin.shift.orders.customer([{"sku": "SKU-002", "units": 1}], "dock_5")
    recorded = copy.deepcopy(twin.events.query(event_type="ORDER_CREATED")[-1]["data"]["lines"])
    assert recorded == [{"sku": "SKU-002", "units": 1}]
    twin.shift.orders.advance()                                        # picks a tote: writes tote_id into the line
    assert order.lines[0]["tote_id"] == twin.find_box("TOTE-1").id
    assert twin.events.query(event_type="ORDER_CREATED")[-1]["data"]["lines"] == recorded
