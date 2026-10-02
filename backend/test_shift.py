"""The shift engine and its order chains (multi-embodiment spec §11.1, §11.2,
§11.4): seeded generators make the work, each order runs its fixed chain of
jobs through the gate, a failed stage is retried once, and the shift clock
reads from 06:00."""
import pytest

from backend import people
from backend.digital_twin import DigitalTwin
from backend.models import BoxKind, BoxStatus, CONFIG, SimulationStatus, TaskStatus
from backend.operations import ORDER_KINDS, shift_clock, shift_hour
from backend.operations.orders import ORDER_RETRY_DELAY_S
from backend.simulator import Simulator


def make_twin(tmp_path, layout="distribution_center"):
    return DigitalTwin(log_dir=str(tmp_path / "logs"), data_dir=str(tmp_path / "data"),
                       persist_logs=False, demo=True, demo_tasks=False, layout=layout)


def populate(twin):
    """A working floor: forklifts, a hauler, AMRs, the picker, both arms, a
    drone and the scout; totes of six SKUs and a few stored pallets."""
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
    for number in range(3):
        twin.add_box(name=f"PAL-{number}", kind="PALLET", sku=f"SKU-00{number + 1}", quantity=40, weight=500.0,
                     slot=f"PR-{10 + number:02d}-05-0")


@pytest.fixture
def twin(tmp_path):
    twin = make_twin(tmp_path)
    populate(twin)
    return twin


@pytest.fixture
def sim(twin):
    simulator = Simulator(twin)
    twin.simulation_status = SimulationStatus.RUNNING
    return simulator


def run(sim, order, max_ticks=4000):
    for _ in range(max_ticks):
        if order.is_terminal:
            return
        sim.tick()
    assert order.is_terminal, (order.order_id, order.status, [(s.name, s.status, s.error) for s in order.stages])


def generate(twin, seconds):
    """Run only the engine, second by second: what it creates, not what robots do."""
    for second in range(int(seconds) + 1):
        twin.simulation_time = float(second)
        twin.shift.tick()


def test_the_shift_starts_paused_and_is_controlled(twin, tmp_path):
    engine = twin.shift
    assert engine.status == "PAUSED" and engine.config() == {"seed": 42, "pace": 1.0, "rates": engine.rates}
    generate(twin, 60)
    assert not engine.orders.orders                              # paused: nothing generated
    engine.start()
    engine.pause()
    assert [e["event"] for e in twin.events.query() if e["event"].startswith("SHIFT_")] == \
        ["SHIFT_STARTED", "SHIFT_PAUSED"]
    with pytest.raises(ValueError, match="pace"):
        engine.configure(pace=0)
    with pytest.raises(ValueError, match="Unknown rate"):
        engine.configure(rates={"aliens": 1})
    assert engine.configure(pace=2, rates={"patrols": 0})["pace"] == 2.0
    classic = make_twin(tmp_path / "classic", layout="classic")
    with pytest.raises(ValueError, match="no shift engine"):
        classic.shift.start()


def test_the_shift_clock_runs_from_six(twin):
    assert CONFIG["SHIFT_START_HOUR"] == 6
    assert (shift_clock(0), shift_clock(90 * 60), shift_clock(20 * 3600 + 59)) == ("06:00", "07:30", "02:00")
    assert (shift_hour(0), shift_hour(18 * 3600)) == (6, 0)
    twin.simulation_time = 3600.0
    assert twin.shift.clock() == "07:00" and twin.shift.status_dict()["clock"] == "07:00"


def test_the_same_seed_makes_the_same_work(tmp_path):
    def work(path, seed):
        twin = make_twin(path)
        populate(twin)
        twin.shift.configure(seed=seed, pace=10)
        twin.shift.start()
        generate(twin, 400)
        orders = [(o.kind, o.lines, o.lane) for o in twin.shift.orders.orders.values()]
        tasks = [(t.type.value, t.box_id, t.params, t.status.value) for t in twin.tasks.tasks.values()]
        return orders, tasks

    first, second = work(tmp_path / "a", 42), work(tmp_path / "b", 42)
    assert first == second and len(first[0]) > 10
    assert work(tmp_path / "c", 7)[0] != first[0]


def test_every_kind_of_work_is_generated(twin):
    twin.shift.configure(pace=10)
    twin.shift.start()
    generate(twin, 200)
    kinds = {order.kind for order in twin.shift.orders.orders.values()}
    assert kinds == set(ORDER_KINDS)
    assert any(task.type.value == "PATROL" for task in twin.tasks.tasks.values())
    inbound = next(o for o in twin.shift.orders.orders.values() if o.kind == "INBOUND")
    pallets = [twin.find_box(line["box_id"]) for line in inbound.lines]
    assert 4 <= len(pallets) <= 8 and all(150 <= p.declared_weight_kg <= 900 for p in pallets)
    assert all(twin.warehouse.zone_of_cell(p.position).key == "dock_1" for p in pallets)
    counts = [o.lines[0]["face"] for o in twin.shift.orders.orders.values() if o.kind == "COUNT"]
    assert counts[:2] == ["8,2", "9,2"]                           # round-robin over the rack faces
    customer = next(o for o in twin.shift.orders.orders.values() if o.kind == "CUSTOMER")
    assert 1 <= len(customer.lines) <= 4 and customer.lane in ("dock_4", "dock_5")
    assert all(1 <= line["units"] <= 3 for line in customer.lines)


def test_a_misdeclared_pallet_is_heavier_than_its_paperwork(twin):
    twin.faults.arm("misdeclared_weight")
    twin.shift._generate_trucks()
    order = next(iter(twin.shift.orders.orders.values()))
    first = twin.find_box(order.lines[0]["box_id"])
    assert 1.1 <= first.true_weight_kg / first.declared_weight_kg <= 1.6
    second = twin.find_box(order.lines[1]["box_id"])
    assert second.true_weight_kg == second.declared_weight_kg


def test_a_customer_order_runs_its_whole_chain(twin, sim):
    order = twin.shift.orders.customer([{"sku": "SKU-002", "units": 2}], "dock_5")
    run(sim, order)
    assert order.status == "DONE", (order.failure_reason, [(s.name, s.status, s.error) for s in order.stages])
    assert (order.pick_station, order.pack_cell, order.sorted_to) == ("pick_station_1", "pack_cell_1", "dock_5")
    assert [s.name for s in order.stages] == ["TOTE_TO_STATION line 1", "PICK line 1", "RETURN_TOTE line 1",
                                              "PACK_ORDER", "SORT"]
    tote = twin.find_box("TOTE-1")
    assert tote.position == (9, 12) and tote.quantity == 18          # picked from, and home again
    carton = next(box for box in twin.boxes.values() if box.kind is BoxKind.CARTON)
    assert carton.order_id == order.order_id and twin.warehouse.zone_of_cell(carton.position).key == "dock_5"
    advanced = [e["data"] for e in twin.events.query(event_type="ORDER_STAGE_ADVANCED")]
    assert sum(1 for data in advanced if data.get("done")) == 5
    assert twin.events.query(event_type="ORDER_COMPLETED")[-1]["data"]["order_id"] == order.order_id


def test_an_order_goes_to_pick_two_when_a_picker_is_on_the_floor(tmp_path):
    twin = make_twin(tmp_path)
    twin.add_robot(name="CX10-210", asset_id="AST-000210")
    sam = twin.add_operator(name="Sam", worker_id="E-10001")
    first = twin.shift.orders.customer([{"sku": "SKU-001", "units": 1}], "dock_4")
    twin.shift.orders.advance()
    assert first.status == "OPEN"                                   # no picker robot, nobody at Pick 2
    people.place(twin, sam, "pick_station_2")
    twin.shift.orders._acquire(first)
    assert (first.pick_station, first.pack_cell) == ("pick_station_2", "pack_cell_1")


def test_an_inbound_truck_is_unloaded_and_put_away(twin, sim):
    pallets = [twin.add_box(name=f"IN-{n}", kind="PALLET", sku="SKU-009", quantity=30, weight=w, position=cell)
               for n, (w, cell) in enumerate(((280.0, (2, 3)), (700.0, (2, 4))))]
    order = twin.shift.orders.inbound("dock_1", [box.id for box in pallets])
    run(sim, order, max_ticks=5000)
    assert order.status == "DONE", order.failure_reason
    assert all(box.slot and box.status is BoxStatus.STORED for box in pallets)
    assert [s.status for s in order.stages] == ["DONE"] * 4


def test_a_pallet_order_ships_a_stored_pallet(twin, sim):
    order = twin.shift.orders.pallet(twin.find_box("PAL-0").id)
    run(sim, order)
    assert order.status == "DONE", order.failure_reason
    assert twin.find_box("PAL-0").status is BoxStatus.SHIPPED


def test_a_rejected_stage_is_retried_once_then_the_order_fails(tmp_path):
    twin = make_twin(tmp_path)
    twin.add_robot(name="TR50-201", asset_id="AST-000201", position=(8, 13))
    order = twin.shift.orders.count("12,2")
    twin.shift.orders.advance()
    stage = order.stages[0]
    assert (order.status, stage.status, stage.attempts) == ("IN_PROGRESS", "WAITING", 1)
    assert stage.error.startswith("No robot can do this CYCLE_COUNT")
    twin.simulation_time += ORDER_RETRY_DELAY_S
    twin.shift.orders.advance()
    assert order.status == "FAILED" and order.failure_reason == stage.error and stage.attempts == 2
    failed = twin.events.query(event_type="ORDER_FAILED")[-1]["data"]
    assert failed["order_id"] == order.order_id and failed["kind"] == "COUNT"


def test_an_item_lost_at_a_hand_off_fails_its_order(twin, sim):
    order = twin.shift.orders.customer([{"sku": "SKU-003", "units": 1}], "dock_4")
    twin.faults.arm("handoff_loss")
    run(sim, order)
    assert order.status == "FAILED" and "was lost at a hand-off" in order.failure_reason
    pack = twin.tasks.get(order.stages[3].task_id)
    assert pack.status is TaskStatus.CANCELLED                     # the arm stops waiting for it


def test_departures_ship_what_reached_the_docks(twin):
    carton = twin.add_box(name="CTN-1", kind="CARTON", weight=2.0, position=(29, 13))
    carton.set_status(BoxStatus.DELIVERED)
    twin.shift._generate_departures()
    assert carton.status is BoxStatus.SHIPPED
