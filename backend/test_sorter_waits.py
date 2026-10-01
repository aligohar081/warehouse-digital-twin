"""The sorter decides each carton's dock once (multi-embodiment spec §8): a
carton whose dock is full waits in the sorter without re-rolling the mis-sort
fault, and drops when a truck (`ship`) frees a cell."""
import pytest

from backend.digital_twin import DigitalTwin
from backend.models import CONFIG, BoxStatus, SimulationStatus
from backend.simulator import Simulator

TO_THE_DROP = 28   # 14 ticks along the end of the line, 14 through the sorter


@pytest.fixture
def twin(tmp_path):
    return DigitalTwin(log_dir=str(tmp_path / "logs"), data_dir=str(tmp_path / "data"),
                       persist_logs=False, demo=True, demo_tasks=False, layout="distribution_center")


@pytest.fixture
def sim(twin):
    simulator = Simulator(twin)
    twin.simulation_status = SimulationStatus.RUNNING
    return simulator


def ticks(sim, n):
    for _ in range(n):
        sim.tick()


def fill_dock(twin, dock):
    """Cartons delivered to every cell of `dock`, waiting for their truck."""
    boxes = []
    for i, cell in enumerate(twin.warehouse.zones[dock].cells):
        box = twin.add_box(name=f"{dock}-FILL-{i}", kind="CARTON", weight=2.0, position=cell)
        box.set_status(BoxStatus.DELIVERED)
        boxes.append(box)
    return boxes


def test_a_carton_waits_while_its_dock_is_full_and_drops_after_the_truck_leaves(twin, sim):
    fill_dock(twin, "dock_5")
    carton = twin.add_box(name="CTN-1", kind="CARTON", weight=2.3, position=(24, 15), destination="dock_5")
    ticks(sim, TO_THE_DROP + 60)
    assert twin.equipment.sorter.inside and carton.position == (25, 15)
    assert carton.status is BoxStatus.DELIVERING
    twin.equipment.ship("dock_5")
    sim.tick()
    assert not twin.equipment.sorter.inside
    assert twin.warehouse.zone_of_cell(carton.position).key == "dock_5"
    assert carton.status is BoxStatus.DELIVERED
    last = twin.events.query(event_type="HANDOFF")[-1]["data"]
    assert (last["to"], last["giver_reported"]["lane"], last["receiver_observed"]["dock"]) == \
        ("dock_5", "dock_5", "dock_5")


def test_an_armed_mis_sort_survives_a_full_dock_wait_and_diverts_one_carton(twin, sim):
    fill_dock(twin, "dock_5")                                   # where the mis-sort will send it
    carton = twin.add_box(name="CTN-1", kind="CARTON", weight=2.3, position=(24, 15), destination="dock_4")
    twin.faults.arm("mis_sort")
    ticks(sim, TO_THE_DROP + 60)
    assert twin.equipment.sorter.inside and not twin.faults.armed()   # decided at the first attempt
    assert twin.equipment.sorter.inside[0].routed_to == "dock_5"
    twin.equipment.ship("dock_5")
    sim.tick()
    assert twin.warehouse.zone_of_cell(carton.position).key == "dock_5"
    data = twin.events.query(event_type="HANDOFF")[-1]["data"]
    assert (data["giver_reported"]["lane"], data["receiver_observed"]["dock"]) == ("dock_4", "dock_5")
    follower = twin.add_box(name="CTN-2", kind="CARTON", weight=2.3, position=(24, 15), destination="dock_4")
    ticks(sim, TO_THE_DROP)
    assert twin.warehouse.zone_of_cell(follower.position).key == "dock_4"   # the fault fired once


def test_without_a_fault_a_full_dock_never_diverts_the_carton(twin, sim, monkeypatch):
    monkeypatch.setitem(CONFIG, "MIS_SORT_RISK", 0.0)
    fill_dock(twin, "dock_5")
    carton = twin.add_box(name="CTN-1", kind="CARTON", weight=2.3, position=(24, 15), destination="dock_5")
    ticks(sim, TO_THE_DROP + 200)
    assert carton.position == (25, 15) and twin.equipment.sorter.inside
    assert twin.equipment.sorter.inside[0].routed_to == "dock_5"
    other = set(twin.warehouse.zones["dock_4"].cells)
    assert not [box for box in twin.boxes.values() if box.position in other]


def test_the_mis_sort_risk_is_rolled_once_per_carton_not_once_per_waiting_tick(twin, sim, monkeypatch):
    monkeypatch.setitem(CONFIG, "MIS_SORT_RISK", 0.02)
    rolls = []
    roll = twin.faults.roll
    monkeypatch.setattr(twin.faults, "roll", lambda kind, rng=None: rolls.append(kind) or roll(kind, rng))
    fill_dock(twin, "dock_5")
    twin.add_box(name="CTN-1", kind="CARTON", weight=2.3, position=(24, 15), destination="dock_5")
    ticks(sim, TO_THE_DROP + 100)
    assert rolls.count("mis_sort") == 1


def test_a_one_lane_sorter_has_nowhere_else_to_divert_to(twin, sim):
    twin.equipment.sorter.lanes = ["dock_4"]
    carton = twin.add_box(name="CTN-1", kind="CARTON", weight=2.3, position=(24, 15), destination="dock_4")
    twin.faults.arm("mis_sort")
    ticks(sim, TO_THE_DROP)
    assert twin.warehouse.zone_of_cell(carton.position).key == "dock_4"


def test_shipped_boxes_no_longer_occupy_their_dock_cell(twin):
    fillers = fill_dock(twin, "dock_5")
    assert twin.equipment.free_dock_cell("dock_5") is None
    shipped = twin.equipment.ship("dock_5")
    assert len(shipped) == len(fillers) and all(box.status is BoxStatus.SHIPPED for box in fillers)
    assert twin.equipment.free_dock_cell("dock_5") == twin.warehouse.zones["dock_5"].cells[0]
