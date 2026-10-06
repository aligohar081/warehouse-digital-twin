"""Orders and stock over a long shift (multi-embodiment spec §7.2, §8, §11.2,
§11.3): a customer line takes a tote a robot can bring now — one the AMRs
reach before one only the humanoid reaches — and waits, spending no retry,
while the only robot that reaches it can't work; a jam no one qualified can
clear is asked for through the gate twice, then waits silently until someone
qualifies; a misplaced box is counted by what it really holds; and a box that
vanishes from the conveyor or the sorter, or an error in the equipment's tick,
never stops the floor."""
import pytest

from backend import goods, people
from backend.digital_twin import DigitalTwin
from backend.models import BoxStatus, SimulationStatus, TaskStatus, TaskType
from backend.operations.orders import ORDER_RETRY_DELAY_S
from backend.simulator import Simulator

#: Bounds, well above what each chain takes, so a stall fails a test instead of hanging it.
MAX_TICKS = 4000
CHECK = 20                                     # CONFIG["SHIFT_CHECK_EVERY_TICKS"]

#: The tote robots, the picker and the humanoid of the populated floor (backend/test_shift_soak.py).
FLEET = (("TR50-201", "AST-000201", (8, 13)), ("TR50-101", "AST-000101", (8, 15)),
         ("PK30-203", "AST-000203", (20, 14)), ("H1-212", "AST-000212", (22, 7)))


def make_twin(tmp_path, fleet=FLEET):
    twin = DigitalTwin(log_dir=str(tmp_path / "logs"), data_dir=str(tmp_path / "data"), persist_logs=False,
                       demo=True, demo_tasks=False, layout="distribution_center")
    for name, asset, cell in fleet:
        twin.add_robot(name=name, asset_id=asset, position=cell)
    twin.add_robot(name="CX10-210", asset_id="AST-000210")
    twin.add_robot(name="CX10-211", asset_id="AST-000211")
    return twin


def running(twin):
    simulator = Simulator(twin)
    twin.simulation_status = SimulationStatus.RUNNING
    return simulator


def jordan_on(twin):
    """The humanoid's supervisor, standing beside it in returns_qc."""
    jordan = twin.add_operator(name="Jordan", worker_id="E-10006")
    people.place(twin, jordan, "returns_qc")
    return jordan


def tasks_of(twin, task_type):
    return [task for task in twin.tasks.tasks.values() if task.type is task_type]


def run(sim, *orders, max_ticks=MAX_TICKS):
    for _ in range(max_ticks):
        if all(order.is_terminal for order in orders):
            return
        sim.tick()
    stalled = [(o.order_id, o.status, [(s.name, s.status, s.attempts, s.error) for s in o.stages
                                       if s.status != "DONE"]) for o in orders if not o.is_terminal]
    assert not stalled, stalled


# ---- a customer line takes a tote a robot can bring now ---------------------- #
def test_a_line_takes_the_tote_the_amrs_reach_before_one_only_the_humanoid_reaches(tmp_path):
    twin = make_twin(tmp_path)
    jordan_on(twin)
    # TS-08-12-2 sorts first, so a choice that ignores reach takes the level-2 tote
    # (the AMRs reach level 1): only the humanoid could bring it.
    high = twin.add_box(name="TOTE-HIGH", kind="TOTE", sku="SKU-001", quantity=20, weight=21.5, slot="TS-08-12-2")
    low = twin.add_box(name="TOTE-LOW", kind="TOTE", sku="SKU-001", quantity=20, weight=21.5, slot="TS-09-12-0")
    sim = running(twin)
    order = twin.shift.orders.customer([{"sku": "SKU-001", "units": 1}], "dock_4")
    twin.shift.orders.advance()
    assert order.lines[0]["tote_id"] == low.id                      # fails if _choose_tote ignores reach
    leg = twin.tasks.get(order.stages[0].task_id)
    for _ in range(MAX_TICKS):
        if leg.is_terminal:
            break
        sim.tick()
    assert leg.status is TaskStatus.COMPLETED, leg.error
    assert leg.box_id == low.id and twin.find_robot(leg.robot_id).mobility.embodiment_class == "AMR"
    assert low.position == (19, 14)                                  # on Pick 1's tote drop
    assert high.position == (8, 12) and high.status is BoxStatus.STORED   # never moved


def test_a_tote_only_the_humanoid_reaches_waits_for_its_supervisor_without_spending_a_retry(tmp_path):
    twin = make_twin(tmp_path)                                       # nobody on the floor holds humanoid_supervision
    twin.add_box(name="TOTE-HIGH", kind="TOTE", sku="SKU-001", quantity=20, weight=21.5, slot="TS-08-12-2")
    sim = running(twin)
    order = twin.shift.orders.customer([{"sku": "SKU-001", "units": 1}], "dock_4")
    for _ in range(int(3 * ORDER_RETRY_DELAY_S / sim.dt)):            # three retry delays
        sim.tick()
    # Asking for it anyway, the gate rejects it ("can't work unsupervised"), the retry
    # is spent the same way, and the order fails: a wait must spend nothing.
    assert (order.status, order.stages[0].attempts) == ("OPEN", 0), order.failure_reason
    assert not tasks_of(twin, TaskType.TOTE_TO_STATION)
    jordan_on(twin)                                                  # the supervisor clocks on
    for _ in range(5):
        sim.tick()
    assert order.status == "IN_PROGRESS"
    leg = twin.tasks.get(order.stages[0].task_id)
    assert leg.status is not TaskStatus.FAILED, leg.error
    assert leg.robot_id == twin.find_robot("H1-212").id


def test_a_tote_no_robot_on_the_floor_could_ever_reach_still_fails_the_order_with_the_gates_reason(tmp_path):
    twin = make_twin(tmp_path, fleet=FLEET[:3])                      # AMRs and the picker, no humanoid
    twin.add_box(name="TOTE-HIGH", kind="TOTE", sku="SKU-001", quantity=20, weight=21.5, slot="TS-08-12-2")
    sim = running(twin)
    order = twin.shift.orders.customer([{"sku": "SKU-001", "units": 1}], "dock_4")
    run(sim, order, max_ticks=int((ORDER_RETRY_DELAY_S + 5) / sim.dt))
    # Waiting here would never end: no body on the floor reaches level 2. Returning
    # None for it as for a busy tote would leave the order OPEN for good.
    assert (order.status, order.stages[0].attempts) == ("FAILED", 2)
    assert order.failure_reason.startswith("No robot can do this TOTE_TO_STATION")
    assert "level 2 is out of its reach" in order.failure_reason


# ---- a jam no one qualified can clear --------------------------------------- #
def test_a_jam_no_one_can_clear_is_asked_twice_then_waits_silently_until_someone_qualifies(tmp_path):
    twin = make_twin(tmp_path, fleet=())                             # the arms only
    riley = twin.add_operator(name="Riley", worker_id="E-10008")     # her robot_cell_access is revoked
    people.place(twin, riley, "pick_station_1")
    sim = running(twin)
    twin.equipment.jam((23, 15))                                     # inside pack cell 1
    failed_before = twin.statistics["failed_tasks"]
    for _ in range(12 * CHECK):                                      # twelve checks
        sim.tick()
    asked = tasks_of(twin, TaskType.CLEAR_JAM)
    # Asking the gate at every check makes a FAILED job, a TASK_FAILED and a
    # failed_tasks stat each time: twelve here, ~1,200 an hour at 1x.
    assert len(asked) == 2 and all(task.status is TaskStatus.FAILED for task in asked)
    assert twin.statistics["failed_tasks"] - failed_before == 2
    waits = [r for r in twin.logger.query(search="waiting until someone qualified")]
    assert len(waits) == 1 and "23,15" in waits[0]["message"]
    mateo = twin.add_operator(name="Mateo", worker_id="E-10004")
    people.place(twin, mateo, "workshop")
    for _ in range(CHECK):
        sim.tick()
    asked = tasks_of(twin, TaskType.CLEAR_JAM)
    assert len(asked) == 3 and asked[-1].operator_id == mateo.id and asked[-1].status is not TaskStatus.FAILED
    for _ in range(MAX_TICKS):
        if asked[-1].is_terminal:
            break
        sim.tick()
    assert asked[-1].status is TaskStatus.COMPLETED and (23, 15) not in twin.equipment.conveyor.jams


def test_a_new_jam_after_one_was_cleared_is_asked_for_afresh(tmp_path):
    twin = make_twin(tmp_path, fleet=())
    sim = running(twin)
    twin.equipment.jam((20, 15))                                     # outside the pack cells: anyone on shift
    for _ in range(4 * CHECK):
        sim.tick()
    assert len(tasks_of(twin, TaskType.CLEAR_JAM)) == 2              # nobody on the floor at all
    twin.equipment.clear_jam((20, 15))                               # cleared some other way
    sim.tick()
    twin.equipment.jam((20, 15))
    for _ in range(4 * CHECK):
        sim.tick()
    # A wait remembered past its jam would leave this one never asked for.
    assert len(tasks_of(twin, TaskType.CLEAR_JAM)) == 4


# ---- a misplaced box is counted by what it really holds --------------------- #
def test_a_misplaced_box_is_counted_by_its_true_quantity(tmp_path):
    twin = make_twin(tmp_path, fleet=())
    pallet = twin.add_box(name="PAL-1", kind="PALLET", sku="SKU-01", quantity=40, weight=500.0, slot="PR-12-02-1")
    twin.stock.adjust_true("PR-12-02-1", -3)                         # three cases short before it moves
    goods.store(twin, pallet, "PR-12-02-1", true_slot_id="PR-12-02-2")   # put back a level too high
    assert goods.physical_qty(twin, "PR-12-02-1") == 0
    assert twin.stock.location("PR-12-02-1").true_qty == 0          # the record's slot is really empty
    assert goods.physical_qty(twin, "PR-12-02-2") == 37              # its recorded 40 before the fix
    assert pallet.true_quantity == 37 and pallet.to_dict()["true_quantity"] == 37
    goods.store(twin, pallet, "PR-12-02-1")                          # put right: the variance comes with it
    assert goods.physical_qty(twin, "PR-12-02-1") == 37 and twin.stock.location("PR-12-02-1").discrepancy == -3
    assert (pallet.true_slot, pallet.true_quantity) == (None, None)


def test_a_saved_misplaced_box_keeps_what_it_really_holds(tmp_path):
    twin = make_twin(tmp_path, fleet=())
    pallet = twin.add_box(name="PAL-1", kind="PALLET", sku="SKU-01", quantity=40, weight=500.0, slot="PR-12-02-1")
    twin.stock.adjust_true("PR-12-02-1", -3)
    goods.store(twin, pallet, "PR-12-02-1", true_slot_id="PR-12-02-2")
    restored = type(pallet).from_dict(pallet.to_dict())
    assert (restored.true_slot, restored.true_quantity) == ("PR-12-02-2", 37)


def test_a_drone_counting_the_face_reads_the_misplaced_box_by_its_true_quantity(tmp_path):
    twin = make_twin(tmp_path, fleet=(("IX2-208", "AST-000208", (20, 2)),))
    pallet = twin.add_box(name="PAL-1", kind="PALLET", sku="SKU-01", quantity=40, weight=500.0, slot="PR-12-02-1")
    twin.stock.adjust_true("PR-12-02-1", -3)
    goods.store(twin, pallet, "PR-12-02-1", true_slot_id="PR-12-02-2")
    sim = running(twin)
    task = twin.tasks.create_task({"type": "CYCLE_COUNT", "face": "12,2"})
    for _ in range(1500):
        if task.is_terminal:
            break
        sim.tick()
    assert task.status is TaskStatus.COMPLETED, task.error
    scans = {e["data"]["level"]: e["data"]["true_qty"] for e in twin.events.query(task_id=task.id,
                                                                                event_type="SCANNED")}
    assert (scans[1], scans[2]) == (0, 37)


# ---- one bad item never stops the floor ------------------------------------- #
def carton_in_sorter(twin, sim):
    entry = twin.equipment.sorter.entry
    carton = twin.add_box(name=f"CARTON-{len(twin.boxes) + 1}", kind="CARTON", weight=2.0, position=entry,
                          destination="dock_4")                    # a box made on the line rides it
    for _ in range(200):
        if twin.equipment.sorter.inside:
            return carton
        sim.tick()
    raise AssertionError("the carton never entered the sorter")


def test_a_carton_that_vanishes_inside_the_sorter_is_dropped_from_it(tmp_path):
    twin = make_twin(tmp_path, fleet=())
    sim = running(twin)
    carton = carton_in_sorter(twin, sim)
    del twin.boxes[carton.id]                                        # gone while inside
    before = twin.tick_count
    for _ in range(200):
        sim.tick()                                                   # its drop raised AttributeError every tick
    assert twin.tick_count == before + 200 and not twin.equipment.sorter.inside
    second = carton_in_sorter(twin, sim)                             # the sorter still works
    for _ in range(200):
        sim.tick()
    assert second.status is BoxStatus.DELIVERED and twin.warehouse.zone_of_cell(second.position).key == "dock_4"


def test_taking_an_item_that_vanished_frees_its_cell_and_says_so(tmp_path):
    twin = make_twin(tmp_path, fleet=())
    cell = twin.equipment.arm_cells["pack_cell_1"]
    item = twin.add_box(name="ITEM-1", kind="ITEM", sku="SKU-01", quantity=1, weight=0.4, position=cell)
    del twin.boxes[item.id]
    with pytest.raises(ValueError, match="no longer known"):         # an AttributeError before the guard
        twin.equipment.take(cell, "arm")
    assert twin.equipment.conveyor.is_free(cell)


def test_an_error_in_the_equipment_tick_does_not_stop_the_floor(tmp_path, monkeypatch):
    twin = make_twin(tmp_path, fleet=(("TR50-201", "AST-000201", (8, 13)),))
    amr = twin.find_robot("TR50-201")
    sim = running(twin)
    task = twin.tasks.create_task({"type": "MOVE_ROBOT", "robot_id": amr.id, "destination": "parking_area"})

    def broken():
        raise RuntimeError("the line controller is unavailable")

    monkeypatch.setattr(twin.equipment, "tick", broken)
    for _ in range(400):
        if task.is_terminal:
            break
        sim.tick()                                                   # no exception reaches the caller
    assert task.status is TaskStatus.COMPLETED, task.error           # robots kept moving
    errors = twin.logger.query(search="the line controller is unavailable")
    assert errors and errors[0]["level"] == "ERROR"
