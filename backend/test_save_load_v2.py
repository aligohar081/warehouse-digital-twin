"""Save and load, version 2 (multi-embodiment spec §4.3; Plan ruling 5): a
save names its floor and carries the stock ledger and the equipment; robots
mid-step or mid-wait, people mid-walk, stock and the conveyor come back
exactly as they were; a load replaces everything it finds; and a save loads
only on the floor it was made on. A version 1 file is a classic save and
still loads on classic. The floors are built through the public API, as
backend/test_shift_soak.py builds them."""
import json

import pytest

from backend import goods, people
from backend.digital_twin import DigitalTwin
from backend.embodiment import seconds_to_ticks
from backend.faults import FAULT_RISKS
from backend.goods import StockLedger
from backend.models import CONFIG, OperatorStatus, RobotStatus, SimulationStatus
from backend.operator import Operator
from backend.robot import Robot
from backend.simulator import Simulator
from backend.warehouse import Warehouse

FLEET = (("PF1200-205", "AST-000205", (7, 4)), ("PF1200-206", "AST-000206", (7, 8)),
         ("HH300-207", "AST-000207", (2, 8)), ("TR50-201", "AST-000201", (8, 13)),
         ("TR50-101", "AST-000101", (8, 15)), ("PK30-203", "AST-000203", (20, 14)),
         ("IX2-208", "AST-000208", (20, 2)), ("IX2-209", "AST-000209", (21, 2)),
         ("SC1-204", "AST-000204", (7, 6)), ("H1-212", "AST-000212", (22, 7)))


@pytest.fixture(autouse=True)
def fault_free(monkeypatch):
    for risk in ("COLLISION_RISK", "FALSE_SUCCESS_RISK", *FAULT_RISKS.values()):
        monkeypatch.setitem(CONFIG, risk, 0.0)


def make_twin(tmp_path, name, layout="distribution_center"):
    return DigitalTwin(log_dir=str(tmp_path / name / "logs"), data_dir=str(tmp_path / name / "data"),
                       persist_logs=False, demo=True, demo_tasks=False, layout=layout)


def populated_floor(tmp_path, name="original"):
    twin = make_twin(tmp_path, name)
    for robot, asset, cell in FLEET:
        twin.add_robot(name=robot, asset_id=asset, position=cell)
    twin.add_robot(name="CX10-210", asset_id="AST-000210")
    twin.add_robot(name="CX10-211", asset_id="AST-000211")
    for number in range(6):
        twin.add_box(name=f"TOTE-{number}", kind="TOTE", sku=f"SKU-00{number + 1}", quantity=20, weight=21.5,
                     slot=f"TS-{8 + number:02d}-12-0")
    for number in range(3):
        twin.add_box(name=f"PAL-{number}", kind="PALLET", sku=f"SKU-00{number + 1}", quantity=40, weight=500.0,
                     slot=f"PR-{10 + number:02d}-05-0")
    for number in range(1, 11):
        twin.add_operator(name=f"W{number:02d}", worker_id=f"E-{10000 + number}")
    return twin


def reload(twin, tmp_path, name="loaded"):
    """Save `twin`, and load the file into a fresh twin of the same floor."""
    path = twin.save_state(str(tmp_path / f"{name}.json"))
    fresh = make_twin(tmp_path, name, layout=twin.layout_name)
    fresh.load_state(path)
    return fresh


def without_saved_at(payload):
    return {key: value for key, value in payload.items() if key != "saved_at"}


# --------------------------------------------------------------------------- #
# The file
# --------------------------------------------------------------------------- #
def test_a_save_is_version_2_and_names_its_floor(tmp_path):
    classic = make_twin(tmp_path, "classic", layout="classic")
    with open(classic.save_state(str(tmp_path / "classic.json")), encoding="utf-8") as handle:
        payload = json.load(handle)
    assert (payload["version"], payload["layout"]) == (2, "classic")
    assert payload["stock"] == {"locations": []} and payload["equipment"] is None
    assert payload["floor_seeded"] is classic.floor_seeded
    for key in ("robots", "boxes", "agents", "operators", "tasks", "schedules", "statistics", "simulation",
                "counters"):
        assert key in payload  # every version 1 section is still there
    floor = populated_floor(tmp_path)
    payload = floor.serialize()
    assert (payload["version"], payload["layout"], payload["floor_seeded"]) == (2, "distribution_center", False)
    assert len(payload["stock"]["locations"]) == 9
    assert payload["equipment"] == floor.equipment.to_state()
    floor.floor_seeded = True
    assert reload(floor, tmp_path).floor_seeded is True


def test_a_version_1_file_is_a_classic_save_and_still_loads(tmp_path):
    classic = make_twin(tmp_path, "classic", layout="classic")
    sim = Simulator(classic)
    classic.tasks.create_task({"type": "PICK_AND_DELIVER", "robot_id": "Robo-01", "box": "Box-A",
                               "destination": "loading_zone"})
    for _ in range(25):
        sim.tick()
    v1 = classic.serialize()
    for key in ("layout", "stock", "equipment", "floor_seeded"):
        del v1[key]
    v1["version"] = 1
    v1["robots"] = [robot.to_dict() for robot in classic.robots.values()]  # what a version 1 file holds
    path = tmp_path / "v1.json"
    path.write_text(json.dumps(v1), encoding="utf-8")
    fresh = make_twin(tmp_path, "fresh", layout="classic")
    seeded = fresh.floor_seeded
    fresh.load_state(str(path))
    robot, restored = classic.find_robot("Robo-01"), fresh.find_robot("Robo-01")
    assert (restored.position, restored.battery, restored.current_task, restored.total_distance) == \
        (robot.position, round(robot.battery, 1), robot.current_task, robot.total_distance)
    assert list(fresh.tasks.tasks) == list(classic.tasks.tasks)
    assert fresh.floor_seeded is seeded  # a version 1 save says nothing about the seed
    assert fresh.equipment is None and fresh.stock.locations() == []


def test_a_save_of_another_floor_is_refused_before_anything_changes(tmp_path):
    classic = make_twin(tmp_path, "classic", layout="classic")
    classic_path = classic.save_state(str(tmp_path / "classic.json"))
    floor = populated_floor(tmp_path)
    before = without_saved_at(floor.serialize())
    with pytest.raises(ValueError, match="of the classic floor, and this twin runs the distribution_center floor"):
        floor.load_state(classic_path)
    with open(classic_path, encoding="utf-8") as handle:
        v1 = json.load(handle)
    del v1["layout"]
    v1["version"] = 1  # a version 1 save is a classic save
    v1_path = tmp_path / "v1.json"
    v1_path.write_text(json.dumps(v1), encoding="utf-8")
    with pytest.raises(ValueError, match="of the classic floor"):
        floor.load_state(str(v1_path))
    v1["version"] = 3
    v1_path.write_text(json.dumps(v1), encoding="utf-8")
    with pytest.raises(ValueError, match="Unknown save version 3"):
        floor.load_state(str(v1_path))
    assert without_saved_at(floor.serialize()) == before
    classic_before = without_saved_at(classic.serialize())
    with pytest.raises(ValueError, match="of the distribution_center floor, and this twin runs the classic floor"):
        classic.load_state(floor.save_state(str(tmp_path / "floor.json")))
    assert without_saved_at(classic.serialize()) == classic_before


# --------------------------------------------------------------------------- #
# The floor comes back as it was
# --------------------------------------------------------------------------- #
def test_robots_people_stock_and_equipment_come_back_as_they_were(tmp_path):
    twin = populated_floor(tmp_path)
    sim = Simulator(twin)
    twin.simulation_status = SimulationStatus.RUNNING
    job = twin.tasks.create_task({"type": "TOTE_TO_STATION", "box_id": twin.find_box("TOTE-0").id})
    for _ in range(37):  # mid-route: part of a cell of travel credit, a step under way
        sim.tick()
    mover = twin.find_robot(job.robot_id)
    assert mover.move_accumulator and mover.activity
    # A safety wait, set by hand: what matters here is that every field of it comes back.
    forklift = twin.find_robot("PF1200-206")
    forklift.wait_reason, forklift.wait_started_tick, forklift.wait_task_id = "PERSON_IN_AISLE", 30, job.id
    forklift.wait_cell, forklift.wait_escalated, forklift.wait_ticks = (7, 9), True, 4
    forklift.battery = 87.123456  # the save keeps the battery unrounded
    # People: one in a zone, one walking between two.
    w01, w02 = twin.find_operator("W01"), twin.find_operator("W02")
    people.place(twin, w01, "pick_station_2")
    people.place(twin, w02, "intake_staging")
    people.start_transit(twin, w02, "workshop")
    # Stock: a count on record, two units gone missing, a pallet on the wrong level.
    twin.stock.record_count("TS-09-12-0", 20, tick=12)
    twin.stock.adjust_true("TS-10-12-0", -2)
    stray = twin.add_box(name="PAL-X", kind="PALLET", sku="SKU-009", quantity=30, weight=420.0, position=(5, 9))
    goods.store(twin, stray, "PR-11-05-1", true_slot_id="PR-11-05-2")
    # The line: an item waiting for pack cell 1's arm, a jam, a carton inside the sorter.
    equipment = twin.equipment
    item = twin.add_box(name="ITEM-1", kind="ITEM", sku="SKU-001", quantity=1, weight=0.8, position=(20, 14))
    equipment.place(item, (20, 15), twin.find_robot("PK30-203").id, order_id="ORD-0001", stop_at=(23, 15))
    equipment.jam((21, 15))
    carton = twin.add_box(name="CTN-1", kind="CARTON", sku=None, quantity=2, weight=1.6, position=(24, 15),
                          order_id="ORD-0002", destination="dock_5")
    for _ in range(seconds_to_ticks(CONFIG["SORTER_TRANSFER_S"]) + 1):
        equipment.tick()
    assert [inside.box_id for inside in equipment.sorter.inside] == [carton.id]

    loaded = reload(twin, tmp_path)
    assert [r.to_state() for r in loaded.robots.values()] == [r.to_state() for r in twin.robots.values()]
    assert [o.to_dict() for o in loaded.operators.values()] == [o.to_dict() for o in twin.operators.values()]
    assert [b.to_dict() for b in loaded.boxes.values()] == [b.to_dict() for b in twin.boxes.values()]
    assert [t.to_dict() for t in loaded.tasks.tasks.values()] == [t.to_dict() for t in twin.tasks.tasks.values()]
    assert loaded.stock.to_dict() == twin.stock.to_dict()
    assert loaded.equipment.to_state() == equipment.to_state()
    # What the version 1 load dropped or rounded:
    restored = loaded.find_robot("PF1200-206")
    assert (restored.wait_reason, restored.wait_started_tick, restored.wait_task_id, restored.wait_cell,
            restored.wait_escalated, restored.wait_ticks) == ("PERSON_IN_AISLE", 30, job.id, (7, 9), True, 4)
    assert restored.battery == 87.123456
    moved = loaded.find_robot(mover.name)
    assert (moved.move_accumulator, moved.action_timer, moved.activity, moved.lift_height_m, moved.model_code) == \
        (mover.move_accumulator, mover.action_timer, mover.activity, mover.lift_height_m, "AC-TR50")
    walker = loaded.find_operator("W02")
    assert (walker.zone, walker.transit_to, walker.transit_until_tick) == \
        ("intake_staging", "workshop", w02.transit_until_tick)
    assert loaded.find_operator("W09").employment_status == "ON_LEAVE"
    assert loaded.find_box("PAL-X").true_slot == "PR-11-05-2" and loaded.stock.location("PR-11-05-1").true_qty == 0
    assert loaded.stock.location("TS-10-12-0").discrepancy == -2
    assert loaded.stock.location("TS-09-12-0").counted_tick == 12
    line = loaded.equipment.conveyor
    assert line.item_at((20, 15)).stop_at == (23, 15) and line.jams == {(21, 15): twin.tick_count}
    assert [(inside.box_id, inside.ticks) for inside in loaded.equipment.sorter.inside] == [(carton.id, 1)]


def test_a_load_leaves_nothing_of_the_floor_it_replaced(tmp_path):
    # Fails if load_state keeps the old twin.stock or twin.equipment (the plan 1b
    # ledger minors), or forgets the hand-off sequence.
    twin = populated_floor(tmp_path)
    picker = twin.find_robot("PK30-203").id
    item = twin.add_box(name="ITEM-1", kind="ITEM", sku="SKU-001", quantity=1, weight=0.8, position=(20, 14))
    twin.equipment.place(item, (20, 15), picker)
    path = twin.save_state(str(tmp_path / "state.json"))
    saved_line = twin.equipment.to_state()
    # After the save: a tote stocked, a jam, two more hand-offs.
    extra = twin.add_box(name="TOTE-X", kind="TOTE", sku="SKU-009", quantity=5, weight=4.0, slot="TS-14-12-0")
    twin.equipment.jam((21, 15))
    other = twin.add_box(name="ITEM-2", kind="ITEM", sku="SKU-002", quantity=1, weight=0.5, position=(20, 14))
    twin.equipment.place(other, (19, 15), picker)
    twin.equipment.place(twin.add_box(name="ITEM-3", kind="ITEM", quantity=1, position=(20, 14)), (22, 15), picker)
    twin.load_state(path)
    assert twin.find_box("TOTE-X") is None
    assert twin.stock.location("TS-14-12-0") is None and twin.stock.slot_of(extra.id) is None
    assert twin.equipment.to_state() == saved_line and not twin.equipment.conveyor.jams
    again = twin.add_box(name="ITEM-4", kind="ITEM", sku="SKU-002", quantity=1, weight=0.5, position=(20, 14))
    assert twin.equipment.place(again, (19, 15), picker).handoff_id == "HO-00002"  # not HO-00004


def test_a_loaded_floor_carries_on_exactly_as_the_original(tmp_path):
    # Fails if a save drops anything a robot's next step depends on: its travel
    # credit (move_accumulator), step timer (action_timer), unrounded battery,
    # a person's walk, or a task's progress.
    twin = populated_floor(tmp_path)
    twin.simulation_status = SimulationStatus.RUNNING
    sim = Simulator(twin)
    for payload in ({"type": "TOTE_TO_STATION", "box_id": twin.find_box("TOTE-1").id},
                    {"type": "RETRIEVE_PALLET", "box_id": twin.find_box("PAL-1").id},
                    {"type": "CYCLE_COUNT", "face": "12,5"},
                    {"type": "PATROL"}):
        assert not twin.tasks.create_task(payload).is_terminal, payload
    people.place(twin, twin.find_operator("W05"), "intake_staging")
    people.start_transit(twin, twin.find_operator("W05"), "pallet_aisle_1")
    for tick in range(1, 401):  # until a robot is part-way through a timed step, after some driving
        sim.tick()
        if tick >= 30 and any(r.activity == "GRASP" and r.action_timer >= 3 for r in twin.robots.values()):
            break
    else:
        pytest.fail("no robot started a GRASP")
    loaded = reload(twin, tmp_path)
    # Both go on with a new Simulator: its retry and idle timers are the
    # loop's own, not the twin's state, so a save doesn't carry them.
    runs = []
    for floor in (twin, loaded):
        seen = []
        floor.events.subscribe(lambda event, seen=seen: seen.append(
            (event["event"], event["robot_id"], event["task_id"], event["box_id"], event["message"])))
        simulator = Simulator(floor)
        for _ in range(400):
            simulator.tick()
        runs.append({
            "events": seen,
            "robots": [(r.name, r.position, r.layer, r.altitude_m, r.battery, r.status, r.current_task, r.activity,
                        r.carrying_box) for r in floor.robots.values()],
            "tasks": [(t.id, t.type, t.status, t.robot_id, t.action_index, t.error)
                      for t in floor.tasks.tasks.values()],
            "boxes": [(b.id, b.position, b.status, b.slot) for b in floor.boxes.values()],
            "people": [(o.name, o.zone, o.transit_to) for o in floor.operators.values()],
            "stock": floor.stock.to_dict(),
        })
    original, restored = runs
    assert any(status.value == "COMPLETED" for _, _, status, *_ in original["tasks"])
    for key in original:
        assert restored[key] == original[key], key


# --------------------------------------------------------------------------- #
# Each part checks what it is given
# --------------------------------------------------------------------------- #
def test_a_robot_comes_back_mid_step_and_mid_wait():
    robot = Robot("robot_01", "TR50-201", (8, 13))
    robot.model_code, robot.activity, robot.lift_height_m = "AC-TR50", "LIFT_TO", 1.2345
    robot.battery, robot.move_accumulator, robot.action_timer, robot.wait_ticks = 64.98765, 0.45, 7, 3
    robot.wait_reason, robot.wait_started_tick, robot.wait_task_id = "PERSON_ON_CROSSING", 120, "task_009"
    robot.wait_cell, robot.wait_escalated, robot.status_before_stop = (17, 10), True, RobotStatus.MOVING
    robot.moves_since_drain, robot.low_battery_warned, robot.blocked_by = 2, True, "robot_02"
    restored = Robot.from_dict(robot.to_state())
    assert restored.to_state() == robot.to_state()
    assert (restored.battery, restored.lift_height_m) == (64.98765, 1.2345)  # unrounded
    old = Robot.from_dict(robot.to_dict())  # a version 1 robot: rounded, no wait start, no timers
    assert (old.battery, old.lift_height_m, old.wait_reason, old.wait_started_tick, old.action_timer) == \
        (65.0, 1.23, "PERSON_ON_CROSSING", None, 0)
    drone = Robot("robot_02", "CX10-210", (12, 2))
    drone.layer, drone.altitude_m = "AIR", 2.345
    assert Robot.from_dict(drone.to_state()).altitude_m == 2.345


def test_a_robot_on_an_impossible_layer_or_altitude_is_refused():
    data = Robot("robot_01", "CX10-210", (12, 2)).to_state()
    for layer, altitude, message in (("WATER", 0.0, "unknown layer 'WATER'"),
                                     ("GROUND", 2.0, "on the GROUND layer, so it must be at 0 m"),
                                     ("AIR", 0.0, "on the AIR layer, so it must be above 0 m"),
                                     ("AIR", float("nan"), "on the AIR layer"),
                                     ("AIR", "high", "altitude must be a number of metres")):
        with pytest.raises(ValueError, match=message):
            Robot.from_dict({**data, "layer": layer, "altitude_m": altitude})
    assert (Robot.from_dict({**data, "layer": "AIR", "altitude_m": 2.5}).altitude_m) == 2.5


def test_an_operator_comes_back_with_their_own_scopes_and_hr_state():
    operator = Operator("operator_001", "Jordan")
    operator.certification_scopes = {"humanoid_supervision": {"equipment": ["TS-H1"], "site": ["WH-01"]}}
    operator.employment_status = "ON_LEAVE"
    data = operator.to_dict()
    restored = Operator.from_dict(data)
    data["certification_scopes"]["humanoid_supervision"]["site"].append("WH-02")  # the save changing later
    assert restored.certification_scopes == {"humanoid_supervision": {"equipment": ["TS-H1"], "site": ["WH-01"]}}
    assert restored.employment_status == "ON_LEAVE"


def test_an_operator_out_of_place_is_refused():
    floor = Warehouse(layout="distribution_center")
    base = Operator("operator_001", "Sam").to_dict()
    for change, message in (({"zone": "intake_staging", "transit_to": "workshop"}, "with no arrival tick"),
                            ({"zone": "intake_staging", "transit_until_tick": 40}, "is walking nowhere"),
                            ({"transit_to": "workshop", "transit_until_tick": 40}, "is not on the floor"),
                            ({"zone": "workshop", "status": OperatorStatus.OFF_DUTY.value}, "is off duty"),
                            ({"zone": "canteen"}, "'canteen', which is not a zone of the distribution_center floor"),
                            ({"zone": "workshop", "transit_to": "moon", "transit_until_tick": 9}, "'moon'")):
        with pytest.raises(ValueError, match=message):
            Operator.from_dict({**base, **change}, floor)
    assert Operator.from_dict({**base, "zone": "canteen"}).zone == "canteen"  # zones are checked only on a floor
    walking = Operator.from_dict({**base, "zone": "workshop", "transit_to": "intake_staging",
                                  "transit_until_tick": 40}, floor)
    assert walking.in_transit


def test_a_ledger_comes_back_through_its_own_checks():
    floor = Warehouse(layout="distribution_center")
    good = {"slot_id": "TS-10-12-1", "box_id": "box_001", "sku": "SKU-001", "recorded_qty": 12, "true_qty": 11,
            "counted_qty": 11, "counted_tick": 40}
    ledger = StockLedger.from_dict({"locations": [good]}, floor)
    assert ledger.to_dict() == {"locations": [good]} and ledger.slot_of("box_001") == "TS-10-12-1"
    other = {**good, "slot_id": "TS-11-12-1", "box_id": "box_002"}
    for locations, message in (([{**good, "colour": "red"}], r"Unknown stock location field\(s\) \['colour'\]"),
                               ([{key: value for key, value in good.items() if key != "true_qty"}], "missing"),
                               ([{**good, "slot_id": "TS-99-99-9"}], "Unknown slot 'TS-99-99-9'"),
                               ([good, {**other, "slot_id": "TS-10-12-1"}], "already holds box_001"),
                               ([good, {**other, "box_id": "box_001"}], "box_001 is already in slot TS-10-12-1"),
                               ([{**good, "true_qty": -1}], "true_qty must be a whole number, 0 or more"),
                               ([{**good, "recorded_qty": 2.5}], "recorded_qty must be a whole number"),
                               ([{**good, "counted_tick": True}], "counted_tick must be a whole number")):
        with pytest.raises(ValueError, match=message):
            StockLedger.from_dict({"locations": locations}, floor)


def test_the_equipment_refuses_a_save_off_its_line_and_keeps_what_it_has(tmp_path):
    twin = populated_floor(tmp_path)
    item = twin.add_box(name="ITEM-1", kind="ITEM", sku="SKU-001", quantity=1, weight=0.8, position=(19, 15))
    before = twin.equipment.to_state()
    entry = {"cell": {"x": 20, "y": 15}, "box_id": item.id}
    for data, message in (({"items": [{**entry, "cell": {"x": 5, "y": 5}}]}, r"\{'x': 5, 'y': 5\}"),
                          ({"items": [entry, entry]}, "two items on conveyor cell \\(20,15\\)"),
                          ({"items": [{**entry, "stop_at": {"x": 23, "y": 14}}]}, "stop in the save"),
                          ({"jams": [{"cell": {"x": 25, "y": 15}, "since_tick": 3}]}, "A jam in the save")):
        with pytest.raises(ValueError, match=message):
            twin.equipment.load_state(data)
    assert twin.equipment.to_state() == before
    twin.equipment.load_state({})  # an empty save empties the line
    assert twin.equipment.to_state() == {"items": [], "jams": [], "sorter": [], "handoffs": [], "handoff_seq": 0}
