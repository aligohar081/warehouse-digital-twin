"""Order hardening (multi-embodiment spec §11.2): a tote promised to a customer
order line is free again once that line's RETURN_TOTE is done, for any later
line, so an order that repeats a SKU only one tote holds can finish, and the
orders queued behind it get their pick station and pack cell."""
import pytest

from backend.digital_twin import DigitalTwin
from backend.models import SimulationStatus
from backend.simulator import Simulator

#: The two orders below finish in about 800 ticks; this is the bound, so a
#: stall fails the test instead of hanging it.
MAX_TICKS = 4000


@pytest.fixture
def twin(tmp_path):
    twin = DigitalTwin(log_dir=str(tmp_path / "logs"), data_dir=str(tmp_path / "data"),
                       persist_logs=False, demo=True, demo_tasks=False, layout="distribution_center")
    for name, asset, cell in (("PF1200-205", "AST-000205", (7, 4)), ("PF1200-206", "AST-000206", (7, 8)),
                              ("HH300-207", "AST-000207", (2, 8)), ("TR50-201", "AST-000201", (8, 13)),
                              ("TR50-101", "AST-000101", (8, 15)), ("PK30-203", "AST-000203", (20, 14)),
                              ("IX2-208", "AST-000208", (20, 2)), ("SC1-204", "AST-000204", (7, 6))):
        twin.add_robot(name=name, asset_id=asset, position=cell)
    twin.add_robot(name="CX10-210", asset_id="AST-000210")
    twin.add_robot(name="CX10-211", asset_id="AST-000211")
    for number in range(6):  # one tote per SKU: SKU-002 is held by TOTE-1 alone
        twin.add_box(name=f"TOTE-{number}", kind="TOTE", sku=f"SKU-00{number + 1}", quantity=20, weight=21.5,
                     slot=f"TS-{8 + number:02d}-12-0")
    return twin


@pytest.fixture
def sim(twin):
    simulator = Simulator(twin)
    twin.simulation_status = SimulationStatus.RUNNING
    return simulator


def run(sim, *orders):
    for _ in range(MAX_TICKS):
        if all(order.is_terminal for order in orders):
            return
        sim.tick()
    stalled = [(o.order_id, o.status, [(s.name, s.status, s.error) for s in o.stages if s.status != "DONE"])
               for o in orders if not o.is_terminal]
    assert not stalled, stalled


def test_an_order_repeating_a_one_tote_sku_uses_the_tote_twice(twin, sim):
    order = twin.shift.orders.customer([{"sku": "SKU-002", "units": 1}, {"sku": "SKU-002", "units": 2}], "dock_5")
    run(sim, order)
    assert order.status == "DONE", (order.failure_reason, [(s.name, s.status, s.error) for s in order.stages])
    tote = twin.find_box("TOTE-1")
    assert [line["tote_id"] for line in order.lines] == [tote.id, tote.id]
    assert tote.position == (9, 12) and tote.quantity == 17          # 3 units picked, and home again
    legs = [twin.tasks.get(stage.task_id) for stage in order.stages if stage.name.startswith("TOTE_TO_STATION")]
    assert len({task.id for task in legs}) == 2 and all(task.box_id == tote.id for task in legs)
    steps = [(e["data"]["stage"], bool(e["data"].get("done"))) for e in twin.events.query(event_type="ORDER_STAGE_ADVANCED")
             if e["data"]["order_id"] == order.order_id]
    assert steps.index(("RETURN_TOTE line 1", True)) < steps.index(("TOTE_TO_STATION line 2", False))  # in turn, not at once


def test_a_second_order_behind_it_is_not_starved(twin, sim):
    first = twin.shift.orders.customer([{"sku": "SKU-002", "units": 1}, {"sku": "SKU-002", "units": 2}], "dock_5")
    second = twin.shift.orders.customer([{"sku": "SKU-003", "units": 1}], "dock_4")
    started = None
    for _ in range(MAX_TICKS):
        if first.is_terminal and second.is_terminal:
            break
        sim.tick()
        if started is None and second.status != "OPEN":
            started = [(s.name, s.status) for s in first.stages if s.line is not None]
    assert (first.status, second.status) == ("DONE", "DONE"), (first.failure_reason, second.failure_reason)
    assert second.pick_station == first.pick_station == "pick_station_1"
    assert started and all(status == "DONE" for _, status in started)   # it waited for the station, then ran
