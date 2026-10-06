"""Save and load, version 2 — the shift engine and its orders (multi-
embodiment spec §4.3, §11): a save carries the shift's status, config, RNG
and streams and every order with its stages; a load restores them into the
twin's own engine (the one listening to twin.events, never a second one);
and a twin saved mid-shift and loaded into a fresh twin goes on to make the
same orders and jobs as the original. The floors are built through the
public API, as backend/test_shift_soak.py builds them."""
import json
import random

import pytest

from backend.digital_twin import DigitalTwin
from backend.faults import FAULT_RISKS
from backend.models import CONFIG, SimulationStatus
from backend.operations import Order, Stage
from backend.simulator import Simulator

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


def running_shift(tmp_path, ticks):
    """The populated floor `ticks` into a shift at pace 2, and its simulator."""
    twin = populated_floor(tmp_path)
    sim = Simulator(twin)
    twin.simulation_status = SimulationStatus.RUNNING
    twin.shift.configure(pace=2.0)
    twin.shift.start()
    for _ in range(ticks):
        sim.tick()
    return twin, sim


def without_saved_at(payload):
    return {key: value for key, value in payload.items() if key != "saved_at"}


# --------------------------------------------------------------------------- #
# Orders and stages
# --------------------------------------------------------------------------- #
def test_an_order_and_its_stages_round_trip(tmp_path):
    book = make_twin(tmp_path, "floor").shift.orders
    order = book.customer([{"sku": "SKU-001", "units": 2}, {"sku": "SKU-002", "units": 1}], "dock_4")
    order.status, order.pick_station, order.pack_cell = "IN_PROGRESS", "pick_station_1", "pack_cell_1"
    order.lines[0].update(tote_id="box_001", placed=1)
    stage = order.stages[0]
    stage.status, stage.task_id, stage.attempts, stage.retry_at, stage.error = "ACTIVE", "task_004", 2, 31.5, "busy"
    assert Order.from_dict(order.to_dict()) == order
    assert Stage.from_dict(stage.to_dict()) == stage
    restored = Order.from_dict(json.loads(json.dumps(order.to_dict())))  # through the file, as a save goes
    assert restored == order and restored.stages[1].after == [0]


def test_a_damaged_order_or_stage_is_refused(tmp_path):
    order = make_twin(tmp_path, "floor").shift.orders.count("10,5").to_dict()
    stage = order["stages"][0]
    for data, message in (({**order, "colour": "red"}, r"Unknown order field\(s\) \['colour'\]"),
                          ({key: value for key, value in order.items() if key != "kind"}, "missing \\['kind'\\]"),
                          ({**order, "kind": "PIZZA"}, "unknown kind 'PIZZA'"),
                          ({**order, "status": "LOST"}, "unknown status 'LOST'"),
                          ({**order, "stages": [{**stage, "after": [3]}]}, "follows a stage the order doesn't have"),
                          ({**order, "stages": [{**stage, "status": "SLEEPING"}]}, "unknown status 'SLEEPING'"),
                          ({**order, "stages": [{**stage, "colour": "red"}]}, r"Unknown stage field\(s\)")):
        with pytest.raises(ValueError, match=message):
            Order.from_dict(data)


def test_the_order_book_keeps_its_orders_in_order_and_its_id_sequence(tmp_path):
    book = make_twin(tmp_path, "floor").shift.orders
    book.customer([{"sku": "SKU-001", "units": 2}], "dock_5")
    book.count("10,5")
    book.returned("box_009")
    state = json.loads(json.dumps(book.to_state()))
    other = make_twin(tmp_path, "other").shift.orders
    other.count("11,5")
    other.load_state(state)
    assert list(other.orders) == ["ORD-0001", "ORD-0002", "ORD-0003"]  # advance() goes through them in this order
    assert other.orders["ORD-0001"] == book.orders["ORD-0001"]
    assert other.count("12,5").order_id == "ORD-0004"
    for damaged, message in (({**state, "seq": 2}, "at least 3"),
                             ({**state, "orders": [state["orders"][0]] * 2}, "ORD-0001 twice")):
        with pytest.raises(ValueError, match=message):
            other.load_state(damaged)
    assert list(other.orders) == ["ORD-0001", "ORD-0002", "ORD-0003", "ORD-0004"]  # unchanged
    other.load_state({**state, "seq": 7})  # orders 4-7 have been pruned, say: the ids still go on from 7
    assert other.count("12,5").order_id == "ORD-0008"


# --------------------------------------------------------------------------- #
# The engine
# --------------------------------------------------------------------------- #
def test_the_shift_comes_back_into_the_twins_own_engine(tmp_path):
    twin, _ = running_shift(tmp_path, 600)  # 90 s in: a truck and a count have been generated
    twin.shift.configure(rates={"returns": 9.0})
    twin.shift.yield_until["operator_003"] = 75.5
    assert twin.shift.orders.orders and twin.shift._truck_count and twin.shift._face_index
    path = twin.save_state(str(tmp_path / "state.json"))
    fresh = make_twin(tmp_path, "fresh")
    engine, listeners = fresh.shift, len(fresh.events._subscribers)
    fresh.load_state(path)
    # Fails if load_state builds a new ShiftEngine: the old one stays subscribed to twin.events.
    assert fresh.shift is engine and len(fresh.events._subscribers) == listeners
    assert fresh.shift.to_state() == twin.shift.to_state()
    assert fresh.shift.status == "RUNNING" and fresh.shift.config() == twin.shift.config()
    assert [fresh.shift.rng.random() for _ in range(3)] == [twin.shift.rng.random() for _ in range(3)]
    assert fresh.shift.status_dict() == twin.shift.status_dict()


def test_a_paused_shift_resumes_where_it_stopped(tmp_path):
    # Fails if _paused_at isn't saved: the loaded shift would fire everything
    # that fell due while it was paused in one burst.
    twin, sim = running_shift(tmp_path, 200)
    twin.shift.pause()
    for _ in range(300):
        sim.tick()
    fresh = make_twin(tmp_path, "fresh")
    fresh.load_state(twin.save_state(str(tmp_path / "state.json")))
    assert fresh.shift.status == "PAUSED" and fresh.shift._paused_at == twin.shift._paused_at
    twin.shift.start()
    fresh.shift.start()
    assert fresh.shift._next_due == twin.shift._next_due


def test_a_save_without_a_shift_leaves_a_fresh_paused_one(tmp_path):
    classic = make_twin(tmp_path, "classic", layout="classic")
    path = classic.save_state(str(tmp_path / "classic.json"))
    with open(path, encoding="utf-8") as handle:
        v1 = json.load(handle)
    for key in ("layout", "stock", "equipment", "shift", "floor_seeded"):
        del v1[key]
    v1["version"] = 1  # a version 1 file: a classic save with no shift
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(v1, handle)
    classic.shift.orders.count("1,1")  # an order the save never had
    classic.shift.rng.random()
    classic.load_state(path)
    assert classic.shift.orders.orders == {} and classic.shift.status == "PAUSED"
    assert classic.shift.rng.random() == random.Random(classic.shift.seed).random()


def test_a_damaged_shift_save_is_refused_and_changes_nothing(tmp_path):
    twin, _ = running_shift(tmp_path, 200)
    path = tmp_path / "state.json"
    twin.save_state(str(path))
    with open(path, encoding="utf-8") as handle:
        good = json.load(handle)
    before = without_saved_at(twin.serialize())
    shift = good["shift"]
    bad_rng_internal = list(shift["rng"][1])
    bad_rng_internal[0] = -1  # out-of-range integer for RNG state
    for damaged, message in (({**shift, "status": "LUNCH"}, "Unknown shift status 'LUNCH'"),
                             ({**shift, "seed": "42"}, "seed must be a whole number"),
                             ({**shift, "pace": 0}, "pace must be a finite number greater than zero"),
                             ({**shift, "rates": {"meteors": 1.0}}, "Unknown rate 'meteors'"),
                             ({**shift, "next_due": {"trucks": 30.0}}, "needs a next due time for every stream"),
                             ({**shift, "rng": [3, [1, 2], None]}, "random number state is missing or damaged"),
                             ({**shift, "rng": [3, bad_rng_internal, 0.0]}, "random number state is missing or damaged"),
                             ({**shift, "truck_count": -1}, "truck_count must be a whole number"),
                             ({**shift, "orders": {"orders": [{"order_id": "ORD-0001"}]}}, "missing \\['kind'\\]")):
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({**good, "shift": damaged}, handle)
        with pytest.raises(ValueError, match=message):
            twin.load_state(str(path))
        assert without_saved_at(twin.serialize()) == before


# --------------------------------------------------------------------------- #
# Determinism
# --------------------------------------------------------------------------- #
def test_a_twin_saved_mid_shift_makes_the_same_orders_and_jobs_as_the_original(tmp_path):
    # Fails if a save drops anything the shift's next ticks depend on: the RNG
    # state, a stream's next due time, the truck or face rotation, an order's
    # stages (status, task, retries) or lines (the tote it holds, the units
    # placed), or the id sequences new orders and tasks take.
    twin, _ = running_shift(tmp_path, 3000)  # 7.5 sim-minutes in: orders in flight, items on the line
    at_save = set(twin.shift.orders.orders)
    done = {key for key, order in twin.shift.orders.orders.items() if order.status == "DONE"}
    assert twin.shift.orders.counts()["CUSTOMER"]["IN_PROGRESS"]
    path = twin.save_state(str(tmp_path / "state.json"))
    loaded = make_twin(tmp_path, "loaded")
    loaded.load_state(path)
    # Both go on with a new Simulator: its retry and idle timers are the
    # loop's own, not the twin's state, so a save doesn't carry them.
    runs = []
    for floor in (twin, loaded):
        simulator = Simulator(floor)
        for _ in range(2000):
            simulator.tick()
        runs.append({
            "orders": floor.shift.orders.to_state(),
            "shift": {key: value for key, value in floor.shift.to_state().items() if key != "orders"},
            "jobs": [(t.id, t.type.value, t.status.value, t.robot_id, t.operator_id, t.box_id, t.destination,
                      t.params, t.action_index, t.error) for t in floor.tasks.tasks.values()],
            "robots": [(r.name, r.position, r.layer, r.battery, r.status.value, r.current_task, r.carrying_box)
                       for r in floor.robots.values()],
            "boxes": [(b.id, b.kind.value, b.position, b.status.value, b.slot, b.quantity)
                      for b in floor.boxes.values()],
            "stock": floor.stock.to_dict(),
            "line": floor.equipment.to_state(),
            "people": [(o.name, o.status.value, o.zone, o.transit_to) for o in floor.operators.values()],
        })
    original, restored = runs
    orders = original["orders"]["orders"]
    assert [o for o in orders if o["order_id"] not in at_save]  # the run did something worth comparing:
    assert [o for o in orders if o["status"] == "DONE" and o["order_id"] not in done]  # new orders, finished ones
    for key in original:
        assert restored[key] == original[key], key
