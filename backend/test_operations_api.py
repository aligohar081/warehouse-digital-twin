"""The operations API and the live snapshot (multi-embodiment spec §11.5, §12,
§14): the shift's controls and panel, its orders, the stock ledger, the
people, the conveyor and on-demand faults over HTTP, and the snapshot's
cell-type table, equipment, shift panel and no-fly cells."""
import json
import random
import threading

import pytest

from backend import people
from backend.app import create_app
from backend.digital_twin import DigitalTwin
from backend.layouts import build_layout
from backend.layouts.base import CELL_TYPE_STYLES
from backend.models import CellType, EventType, SimulationStatus
from backend.operator import Operator
from backend.simulator import Simulator

#: The classic dashboard's legend, in its order, then the two unlisted fills —
#: the table must reproduce it exactly so the classic floor draws as before.
CLASSIC_TABLE = [
    {"type": "SHELF", "label": "Racking", "role": "shelf", "legend": True},
    {"type": "STORAGE", "label": "Pick face", "role": "storage", "legend": True},
    {"type": "CHARGING", "label": "Charging", "role": "charging", "legend": True},
    {"type": "LOADING", "label": "Loading", "role": "loading", "legend": True},
    {"type": "UNLOADING", "label": "Unloading", "role": "unloading", "legend": True},
    {"type": "PACKING", "label": "Packing", "role": "packing", "legend": True},
    {"type": "PARKING", "label": "Parking", "role": "parking", "legend": True},
    {"type": "RESTRICTED", "label": "Restricted", "role": "restricted", "legend": True},
    {"type": "EMPTY", "label": "Floor", "role": "floor", "legend": False},
    {"type": "WALL", "label": "Wall", "role": "wall", "legend": False},
]


def make_twin(path, layout="distribution_center"):
    return DigitalTwin(log_dir=str(path / "logs"), data_dir=str(path / "data"), persist_logs=False,
                       demo=True, demo_tasks=False, layout=layout)


def client_for(twin):
    app, _twin, _sim, _ci = create_app(twin=twin, autostart=False, run_thread=False)
    app.config["TESTING"] = True
    return app, app.test_client()


@pytest.fixture
def twin(tmp_path):
    return make_twin(tmp_path)


@pytest.fixture
def client(twin):
    return client_for(twin)[1]


def frames(stream, name):
    """The `name` frames waiting on a broadcaster client queue, decoded."""
    out = []
    while not stream.empty():
        frame = stream.get_nowait()
        if frame.startswith(f"event: {name}\n"):
            out.append(json.loads(frame.split("data: ", 1)[1]))
    return out


# ---- the shift ------------------------------------------------------------ #
def test_the_shift_is_started_and_paused_over_http(twin):
    app, client = client_for(twin)
    response = client.get("/api/shift")
    assert response.status_code == 200
    shift = response.get_json()["shift"]
    assert (shift["status"], shift["clock"], shift["in_flight"], shift["backlog"]) == ("PAUSED", "06:00", 0, 0)
    assert {"config", "counters", "orders", "throughput_per_hour", "failed_orders", "exceptions"} <= set(shift)
    stream = app.broadcaster.register()
    started = client.post("/api/shift/start")
    assert started.status_code == 200 and started.get_json()["shift"]["status"] == "RUNNING"
    assert twin.shift.status == "RUNNING"
    # The dashboard hears about it at once (no publish: no state frame).
    assert [state["shift"]["status"] for state in frames(stream, "state")] == ["RUNNING"]
    paused = client.post("/api/shift/pause")
    assert paused.status_code == 200 and paused.get_json()["shift"]["status"] == "PAUSED"
    assert twin.shift.status == "PAUSED"
    assert client.get("/api/shift").get_json()["shift"] == twin.snapshot()["shift"]


def test_the_shift_is_configured_and_a_bad_value_changes_nothing(twin, client):
    response = client.post("/api/shift/config", json={"pace": 2, "seed": 7, "rates": {"patrols": 0}})
    assert response.status_code == 200
    config = response.get_json()["shift"]["config"]
    assert (config["pace"], config["seed"], config["rates"]["patrols"]) == (2.0, 7, 0.0)
    assert twin.shift.rng.random() == random.Random(7).random()      # a new seed restarts the RNG
    before = twin.shift.config()
    bad_bodies = [
        {"pace": 0},
        {"pace": 3, "rates": {"aliens": 1}},   # fails if configure applies the pace before checking the rates
        {"pace": 10 ** 400},                   # fails (a 500) if float()'s OverflowError escapes
        {"rates": {"trucks": "lots"}},
        {"rates": [1, 2]},
        {"seed": "seven"},
        {"speed": 3},                          # not something the shift has
        {},
    ]
    for body in bad_bodies:
        response = client.post("/api/shift/config", json=body)
        assert response.status_code == 400, body
        assert response.get_json()["ok"] is False and response.get_json()["error"]
    assert twin.shift.config() == before
    with pytest.raises(ValueError, match="trucks"):
        twin.shift.configure(rates={"trucks": 10 ** 400})
    assert twin.shift.config() == before


# ---- orders --------------------------------------------------------------- #
def test_orders_are_listed_newest_first_by_their_number(twin, client):
    book = twin.shift.orders
    book._seq = 9998                    # a long shift's id sequence: the next ids are ORD-9999, ORD-10000, ...
    book.count("8,2")
    book.count("9,2")
    book.returned("box_1")
    body = client.get("/api/orders").get_json()
    # Fails if list() sorts by the id string, which puts ORD-9999 first.
    assert [order["order_id"] for order in body["orders"]] == ["ORD-10001", "ORD-10000", "ORD-9999"]
    assert body["counts"]["COUNT"]["OPEN"] == 2 and body["counts"]["RETURN"]["OPEN"] == 1
    counts = client.get("/api/orders?kind=count&status=open&limit=1").get_json()["orders"]
    assert [order["order_id"] for order in counts] == ["ORD-10000"]
    one = client.get("/api/orders/ORD-9999")
    assert one.status_code == 200
    assert [stage["task_type"] for stage in one.get_json()["order"]["stages"]] == ["CYCLE_COUNT"]
    missing = client.get("/api/orders/ORD-0001")
    assert missing.status_code == 404 and missing.get_json()["error"] == "Order 'ORD-0001' does not exist"
    for query in ("status=LOST", "kind=PARCEL", "limit=0", "limit=many"):
        assert client.get(f"/api/orders?{query}").status_code == 400, query
    with pytest.raises(KeyError):
        book.get("ORD-0001")


# ---- stock and people ----------------------------------------------------- #
def test_stock_is_listed_by_sku_with_recorded_and_true_quantities(twin, client):
    twin.add_box(name="PAL-1", kind="PALLET", sku="SKU-001", quantity=40, weight=500.0, slot="PR-10-05-0")
    twin.add_box(name="TOTE-1", kind="TOTE", sku="SKU-002", quantity=12, weight=9.0, slot="TS-10-12-1")
    twin.add_box(name="TOTE-2", kind="TOTE", sku="SKU-002", quantity=8, weight=7.0, slot="TS-11-12-0")
    twin.stock.adjust_true("TS-10-12-1", -2)                           # two units went missing
    every = client.get("/api/stock").get_json()
    assert every["skus"] == ["SKU-001", "SKU-002"]
    assert [row["slot_id"] for row in every["locations"]] == ["PR-10-05-0", "TS-10-12-1", "TS-11-12-0"]
    totes = client.get("/api/stock?sku=SKU-002").get_json()
    assert [row["slot_id"] for row in totes["locations"]] == ["TS-10-12-1", "TS-11-12-0"]
    first = totes["locations"][0]
    assert (first["kind"], first["level"], first["recorded_qty"], first["true_qty"], first["discrepancy"]) == \
        ("TOTE", 1, 12, 10, -2)
    assert totes["totals"] == {"recorded_qty": 20, "true_qty": 18}
    assert client.get("/api/stock?sku=SKU-999").get_json()["locations"] == []


def test_people_are_listed_in_their_zone_or_on_their_walk(twin, client):
    sam = twin.add_operator(name="Sam", worker_id="E-10001")
    lee = twin.add_operator(name="Lee", worker_id="E-10002")
    people.place(twin, sam, "pick_station_2")
    people.place(twin, lee, "intake_staging")
    people.start_transit(twin, lee, "workshop")
    listed = {person["name"]: person for person in client.get("/api/people").get_json()["people"]}
    assert (listed["Sam"]["zone"], listed["Sam"]["in_transit"]) == ("pick_station_2", False)
    assert (listed["Lee"]["transit_to"], listed["Lee"]["in_transit"]) == ("workshop", True)
    assert listed["Sam"]["worker_id"] == "E-10001"


# ---- equipment and faults ------------------------------------------------- #
def test_the_equipment_shows_the_items_on_the_line_and_its_jams(twin, client):
    riding = twin.add_box(name="ITEM-1", kind="ITEM", sku="SKU-002", quantity=1, weight=0.5,
                          position=(20, 15), order_id="ORD-7")
    placed = twin.add_box(name="ITEM-2", kind="ITEM", sku="SKU-002", quantity=1, weight=0.5, position=(19, 14))
    twin.equipment.place(placed, (19, 15), giver="E-10001")             # a person puts it on the infeed
    twin.equipment.jam((22, 15))
    body = client.get("/api/equipment").get_json()
    view = body["equipment"]
    assert view["item_cells"] == [
        {"x": 19, "y": 15, "box_id": placed.id, "kind": "ITEM", "order_id": None},
        {"x": 20, "y": 15, "box_id": riding.id, "kind": "ITEM", "order_id": "ORD-7"},
    ]
    assert view["jammed_cells"] == [{"x": 22, "y": 15}]
    assert view["conveyor"]["jams"] == [{"cell": {"x": 22, "y": 15}, "since_tick": 0}]   # to_dict() as it was
    assert view["sorter"]["lanes"] == ["dock_4", "dock_5"]
    assert [(h["box_id"], h["from"], h["to"]) for h in body["handoffs"]] == [(placed.id, "E-10001", "conveyor")]
    assert twin.snapshot()["equipment"] == view


def test_a_fault_is_armed_on_demand(twin, client):
    response = client.post("/api/faults/conveyor-jam", json={"count": 2})
    assert response.status_code == 200
    assert response.get_json() == {"ok": True, "kind": "conveyor_jam", "armed": 2}
    assert client.post("/api/faults/mis_sort").get_json()["armed"] == 1        # one by default
    unknown = client.post("/api/faults/gremlins")
    assert unknown.status_code == 404 and "gremlins" in unknown.get_json()["error"]
    for count in (0, -1, 1.5, "2", True):
        assert client.post("/api/faults/mis_sort", json={"count": count}).status_code == 400, count
    assert twin.faults.armed() == {"conveyor_jam": 2, "mis_sort": 1}
    # Fails if arm() still runs int(count): 2.5 would arm two, None raise TypeError.
    for count in (2.5, None, "3"):
        with pytest.raises(ValueError, match="whole number"):
            twin.faults.arm("mis_sort", count)
    assert twin.faults.armed() == {"conveyor_jam": 2, "mis_sort": 1}


# ---- the classic floor ----------------------------------------------------- #
def test_the_classic_floor_has_no_shift_and_no_equipment(tmp_path):
    twin = make_twin(tmp_path, layout="classic")
    _app, client = client_for(twin)
    refused = [client.get("/api/shift"), client.post("/api/shift/start"), client.post("/api/shift/pause"),
               client.post("/api/shift/config", json={"pace": 2}), client.get("/api/equipment")]
    # /api/shift/start fails as a 400 if the route lets ShiftEngine.start's ValueError through.
    assert [response.status_code for response in refused] == [409] * 5
    assert refused[0].get_json()["error"] == "The classic floor has no shift engine"
    assert twin.shift.status == "PAUSED" and twin.shift.pace == 1.0
    assert client.get("/api/orders").get_json()["orders"] == []
    assert client.get("/api/stock").get_json()["locations"] == []
    assert {person["name"] for person in client.get("/api/people").get_json()["people"]} == {"Sam", "Lee"}


# ---- the shift panel -------------------------------------------------------- #
def test_the_panel_lists_failed_orders_and_exceptions_newest_first(twin):
    sim = Simulator(twin)
    twin.simulation_status = SimulationStatus.RUNNING
    order = twin.shift.orders.count("0,0")              # no rack there: the count is refused, retried, refused
    for _ in range(100):                                # past ORDER_RETRY_DELAY_S (10 s at 0.15 s a tick)
        sim.tick()
        if order.status == "FAILED":
            break
    assert (order.status, order.failure_reason) == ("FAILED", "(0,0) is not a pallet rack face")
    failed_at = twin.tick_count
    panel = twin.shift.panel()
    assert panel["failed_orders"] == [{"order_id": order.order_id, "kind": "COUNT", "reason": order.failure_reason}]
    assert panel["exceptions"] == [{"type": "ORDER_FAILED", "tick": failed_at,
                                    "message": f"{order.order_id} failed: {order.failure_reason}"}]
    # The simulator emits these; the panel keeps them from its own subscription.
    twin.events.emit(EventType.STOCK_VARIANCE_DETECTED, "PR-10-05-0: counted 38, recorded 40 — reconciled",
                     data={"auto_reconciled": True})
    twin.events.emit(EventType.STOCK_VARIANCE_DETECTED, "PR-10-05-1: counted 30, recorded 40 — left for review",
                     data={"auto_reconciled": False})
    panel = twin.shift.panel()
    assert [e["message"] for e in panel["exceptions"]][:2] == [
        "PR-10-05-1: counted 30, recorded 40 — left for review", f"{order.order_id} failed: {order.failure_reason}"]
    for number in range(25):
        twin.events.emit(EventType.SAFETY_WAIT_ESCALATED, f"wait {number} escalated")
    exceptions = twin.shift.panel()["exceptions"]
    assert len(exceptions) == 20 and exceptions[0] == {"type": "SAFETY_WAIT_ESCALATED", "message": "wait 24 escalated",
                                                       "tick": twin.tick_count}
    twin.reset(demo_tasks=False)
    assert twin.shift.panel()["exceptions"] == [] and twin.shift.panel()["failed_orders"] == []


# ---- the snapshot ----------------------------------------------------------- #
def test_every_cell_type_is_styled_once():
    styled = [entry[0] for entry in CELL_TYPE_STYLES]
    assert len(styled) == len(set(styled))
    assert set(styled) == set(CellType) - {CellType.ROBOT, CellType.BOX}   # those two are never drawn as cells
    assert styled[:8] == [CellType.SHELF, CellType.STORAGE, CellType.CHARGING, CellType.LOADING,
                          CellType.UNLOADING, CellType.PACKING, CellType.PARKING, CellType.RESTRICTED]


def test_the_classic_snapshot_draws_its_floor_as_before(tmp_path):
    twin = make_twin(tmp_path, layout="classic")
    snapshot = twin.snapshot(include_layout=True)
    assert snapshot["cell_types"] == CLASSIC_TABLE
    assert twin.warehouse.cell_type_table() == build_layout("classic").cell_type_table() == CLASSIC_TABLE
    assert snapshot["no_fly_cells"] == [] and snapshot["equipment"] is None and snapshot["shift"] is None
    plain = twin.snapshot()
    assert "cell_types" not in plain and "no_fly_cells" not in plain     # layout data comes with the layout
    assert {"environment", "robots", "boxes", "tasks", "statistics", "options"} <= set(plain)


def test_the_new_floor_snapshot_carries_its_cell_types_no_fly_cells_and_operations(twin):
    snapshot = twin.snapshot(include_layout=True)
    assert [row["type"] for row in snapshot["cell_types"]] == [
        "CHARGING", "PARKING", "RESTRICTED", "DOCK", "DOCK_DOOR", "STAGING", "PALLET_RACK", "TOTE_SHELF",
        "WALKWAY", "STATION", "CONVEYOR", "SORTER", "WORKSHOP", "DRONE_PAD", "EMPTY", "WALL"]
    rows = {row["type"]: row for row in snapshot["cell_types"]}
    assert rows["DOCK_DOOR"] == {"type": "DOCK_DOOR", "label": "Dock door", "role": "dockDoor", "legend": True}
    assert rows["DRONE_PAD"]["role"] == "dronePad" and rows["WALL"]["legend"] is False
    no_fly = [(cell["x"], cell["y"]) for cell in snapshot["no_fly_cells"]]
    expected = {cell for zone in twin.warehouse.zones.values() if zone.attributes.get("no_fly") for cell in zone.cells}
    assert set(no_fly) == expected and len(no_fly) == len(expected)
    assert no_fly == sorted(no_fly, key=lambda cell: (cell[1], cell[0]))   # row by row, like the cells
    plain = twin.snapshot()
    assert plain["equipment"]["conveyor"]["cells"][0] == {"x": 19, "y": 15}
    assert plain["equipment"]["item_cells"] == [] and plain["equipment"]["jammed_cells"] == []
    assert plain["shift"] == twin.shift.panel() and plain["shift"]["status"] == "PAUSED"


# ---- locking ----------------------------------------------------------------- #
class WatchedLock:
    """Stands in for twin.lock: a re-entrant lock that knows when it is held."""

    def __init__(self):
        self._lock = threading.RLock()
        self.depth = 0

    def __enter__(self):
        self._lock.acquire()
        self.depth += 1
        return self

    def __exit__(self, *exc):
        self.depth -= 1
        self._lock.release()
        return False


ROUTES = [
    ("get", "/api/shift", None, lambda twin: (twin.shift, "panel")),
    ("post", "/api/shift/start", None, lambda twin: (twin.shift, "start")),
    ("post", "/api/shift/pause", None, lambda twin: (twin.shift, "pause")),
    ("post", "/api/shift/config", {"pace": 2}, lambda twin: (twin.shift, "configure")),
    ("get", "/api/orders", None, lambda twin: (twin.shift.orders, "list")),
    ("get", "/api/orders/ORD-0001", None, lambda twin: (twin.shift.orders, "get")),
    ("get", "/api/stock", None, lambda twin: (twin.stock, "locations")),
    ("get", "/api/people", None, lambda twin: (Operator, "to_dict")),
    ("get", "/api/equipment", None, lambda twin: (twin.equipment, "view")),
    ("post", "/api/faults/mis_sort", None, lambda twin: (twin.faults, "arm")),
]


@pytest.mark.parametrize("method,path,body,target", ROUTES, ids=[f"{m} {p}" for m, p, _b, _t in ROUTES])
def test_every_operations_route_holds_the_twin_lock(twin, client, monkeypatch, method, path, body, target):
    twin.shift.orders.count("8,2")
    twin.add_operator(name="Sam", worker_id="E-10001")
    lock = WatchedLock()
    monkeypatch.setattr(twin, "lock", lock)
    owner, name = target(twin)
    real = getattr(owner, name)
    held = []

    def watched(*args, **kwargs):
        held.append(lock.depth > 0)
        return real(*args, **kwargs)

    monkeypatch.setattr(owner, name, watched)
    response = getattr(client, method)(path, json=body)
    assert response.status_code == 200, response.get_json()
    assert held and all(held), f"{path} called {name} without twin.lock"


def test_a_load_clears_the_panels_exceptions_and_any_armed_fault(twin, tmp_path):
    # Plan ruling 19: neither belongs to the floor being loaded. Without the
    # clears, the escalation and the armed mis-sort survive the load.
    path = str(tmp_path / "save.json")
    twin.save_state(path)
    twin.faults.arm("mis_sort", 2)
    twin.events.emit(EventType.SAFETY_WAIT_ESCALATED, "PF1200-205 has waited 120 s (PERSON_IN_AISLE) — escalated")
    assert twin.shift.panel()["exceptions"]
    twin.load_state(path)
    assert twin.shift.panel()["exceptions"] == []
    assert twin.faults.armed() == {}
