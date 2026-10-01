"""The conveyor line, the sorter and hand-off records (multi-embodiment spec
§8): items advance and back up, arms' items wait on their working cells,
cartons are sorted to their order's dock, and every transfer is recorded."""
import pytest

from backend.digital_twin import DigitalTwin
from backend.equipment import Equipment
from backend.models import BoxKind, BoxStatus, SimulationStatus
from backend.simulator import Simulator


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


def test_the_new_floor_has_one_line_and_a_sorter(twin, tmp_path):
    equipment = twin.equipment
    assert equipment.conveyor.cells == [(19, 15), (20, 15), (21, 15), (22, 15), (23, 15), (24, 15)]
    assert equipment.conveyor.advance_ticks == 20          # 1.5 m at 0.5 m/s, 0.15 s ticks
    assert (equipment.sorter.entry, equipment.sorter.lanes) == ((24, 15), ["dock_4", "dock_5"])
    assert equipment.arm_cells == {"pack_cell_1": (23, 15), "pack_cell_2": (22, 15)}
    classic = DigitalTwin(log_dir=str(tmp_path / "c" / "logs"), data_dir=str(tmp_path / "c" / "data"),
                          persist_logs=False, demo=True, demo_tasks=False)
    assert classic.equipment is None and Equipment.for_floor(classic) is None


def test_a_box_added_on_the_line_rides_it(twin):
    item = twin.add_box(name="ITEM-1", kind="ITEM", sku="SKU-01", quantity=1, weight=0.4, position=(20, 15))
    line_item = twin.equipment.conveyor.item_at((20, 15))
    assert line_item.box_id == item.id and item.status is BoxStatus.DELIVERING
    with pytest.raises(ValueError, match="occupied"):
        twin.add_box(name="ITEM-2", kind="ITEM", position=(20, 15))


def test_items_advance_a_cell_every_twenty_ticks_and_back_up(twin, sim):
    first = twin.add_box(name="ITEM-1", kind="ITEM", weight=0.4, position=(21, 15))
    second = twin.add_box(name="ITEM-2", kind="ITEM", weight=0.4, position=(20, 15))
    twin.equipment.conveyor.item_at((21, 15)).stop_at = (22, 15)   # held for arm 2
    ticks(sim, 19)
    assert (first.position, second.position) == ((21, 15), (20, 15))
    sim.tick()
    assert (first.position, second.position) == ((22, 15), (21, 15))
    ticks(sim, 60)
    assert (first.position, second.position) == ((22, 15), (21, 15))  # the second waits behind the first


def test_an_arm_takes_its_item_and_the_hand_off_is_recorded(twin, sim):
    item = twin.add_box(name="ITEM-1", kind="ITEM", weight=0.4, position=(23, 15), order_id="ORD-1")
    twin.equipment.conveyor.item_at((23, 15)).stop_at = (23, 15)
    ticks(sim, 40)
    assert item.position == (23, 15)                            # waiting for its arm
    box, handoff = twin.equipment.take((23, 15), "robot_01", task_id="task_009")
    assert box is item and twin.equipment.conveyor.item_at((23, 15)) is None
    assert (handoff.from_holder, handoff.to_holder, handoff.order_id, handoff.lost) == \
        ("conveyor", "robot_01", "ORD-1", False)
    event = twin.events.query(event_type="HANDOFF")[-1]
    assert event["task_id"] == "task_009" and event["data"]["receiver_observed"]["present"] is True


def test_a_carton_is_sorted_to_its_order_dock(twin, sim):
    carton = twin.add_box(name="CTN-1", kind="CARTON", weight=2.3, position=(24, 15),
                          destination="dock_5", order_id="ORD-1")
    ticks(sim, 14)                                              # 2 s at the end of the line
    assert carton.position == (25, 15) and twin.equipment.sorter.inside
    ticks(sim, 14)                                              # 2 s through the sorter
    assert twin.warehouse.zone_of_cell(carton.position).key == "dock_5"
    assert carton.status is BoxStatus.DELIVERED
    moves = [(e["data"]["from"], e["data"]["to"]) for e in twin.events.query(event_type="HANDOFF")]
    assert moves == [("conveyor", "sorter"), ("sorter", "dock_5")]
    last = twin.events.query(event_type="HANDOFF")[-1]["data"]
    assert last["giver_reported"]["lane"] == last["receiver_observed"]["dock"] == "dock_5"
    assert [box.id for box in twin.equipment.ship("dock_5")] == [carton.id]
    assert carton.status is BoxStatus.SHIPPED


def test_a_mis_sort_sends_a_carton_to_the_other_dock(twin, sim):
    carton = twin.add_box(name="CTN-1", kind="CARTON", weight=2.3, position=(24, 15), destination="dock_4")
    twin.faults.arm("mis_sort")
    ticks(sim, 28)
    assert twin.warehouse.zone_of_cell(carton.position).key == "dock_5"
    data = twin.events.query(event_type="HANDOFF")[-1]["data"]
    assert (data["giver_reported"]["lane"], data["receiver_observed"]["dock"]) == ("dock_4", "dock_5")


def test_anything_but_a_carton_is_rejected_at_the_sorter(twin, sim):
    item = twin.add_box(name="ITEM-1", kind="ITEM", weight=0.4, position=(24, 15))
    ticks(sim, 14)
    assert item.status is BoxStatus.FAILED and not twin.equipment.conveyor.items
    assert twin.events.query(event_type="HANDOFF")[-1]["data"]["to"] == "sorter_reject"


def test_placing_on_the_line_records_a_hand_off_and_a_loss(twin):
    item = twin.add_box(name="ITEM-1", kind="ITEM", weight=0.4, position=(20, 14))
    handoff = twin.equipment.place(item, (20, 15), "robot_03", task_id="task_004", order_id="ORD-2",
                                   stop_at=(23, 15))
    assert not handoff.lost and twin.equipment.conveyor.item_at((20, 15)).stop_at == (23, 15)
    assert item.status is BoxStatus.DELIVERING and item.position == (20, 15)
    with pytest.raises(ValueError, match="not free"):
        twin.equipment.place(item, (20, 15), "robot_03")
    lost = twin.add_box(name="ITEM-2", kind="ITEM", weight=0.4, position=(20, 14))
    twin.faults.arm("handoff_loss")
    handoff = twin.equipment.place(lost, (19, 15), "robot_03", task_id="task_004")
    assert handoff.lost and lost.status is BoxStatus.FAILED and lost.position == (19, 15)
    assert twin.equipment.conveyor.item_at((19, 15)) is None
    event = twin.events.query(event_type="HANDOFF")[-1]
    assert event["level"] == "WARNING" and event["data"]["giver_reported"]["present"] is True
    assert event["data"]["receiver_observed"] == {"present": False}


def test_a_jam_stops_its_segment_and_everything_behind_it(twin, sim):
    head = twin.add_box(name="ITEM-1", kind="ITEM", weight=0.4, position=(20, 15))
    tail = twin.add_box(name="ITEM-2", kind="ITEM", weight=0.4, position=(19, 15))
    twin.faults.arm("conveyor_jam")
    ticks(sim, 20)
    conveyor = twin.equipment.conveyor
    assert (20, 15) in conveyor.jams and head.position == (20, 15) and tail.position == (19, 15)
    assert conveyor.jam_upstream_of((23, 15)) == (20, 15) and conveyor.jam_upstream_of((19, 15)) is None
    jammed = twin.events.query(event_type="CONVEYOR_JAMMED")
    assert len(jammed) == 1 and jammed[0]["data"]["cell"] == {"x": 20, "y": 15}
    ticks(sim, 100)
    assert head.position == (20, 15) and tail.position == (19, 15)
    twin.equipment.clear_jam((20, 15))
    assert twin.events.query(event_type="CONVEYOR_CLEARED")[-1]["data"]["jammed_ticks"] >= 100
    ticks(sim, 20)
    assert head.position == (21, 15) and tail.position == (20, 15)
    with pytest.raises(ValueError, match="not jammed"):
        twin.equipment.clear_jam((20, 15))


def test_a_failed_order_releases_its_held_items(twin):
    twin.add_box(name="ITEM-1", kind="ITEM", weight=0.4, position=(21, 15), order_id="ORD-3")
    twin.equipment.conveyor.item_at((21, 15)).stop_at = (23, 15)
    assert twin.equipment.release_order("ORD-3") == 1
    assert twin.equipment.conveyor.item_at((21, 15)).stop_at is None


def test_reset_rebuilds_an_empty_line(twin):
    twin.add_box(name="ITEM-1", kind="ITEM", weight=0.4, position=(21, 15))
    twin.reset(demo_tasks=False)
    assert twin.equipment is not None and not twin.equipment.conveyor.items
    assert twin.equipment.to_dict()["conveyor"]["advance_ticks"] == 20
