"""The distribution-centre floor's seed (multi-embodiment spec §4.2), reset
(§4.3), remote check-ins (§4.4), and where the twin spawns robots on that
floor (plan 1a's arm-station and stop-rule items).

DigitalTwin(layout="distribution_center") still boots empty; seed_floor()
puts the seed on it, and reset() puts it back only on a seeded twin.
"""
import pytest

from backend import seeds
from backend.digital_twin import DigitalTwin
from backend.faults import FAULT_RISKS
from backend.models import CONFIG, BoxKind, CellType, OperatorStatus, RobotStatus, SimulationStatus
from backend.operations.activities import HOME_ZONES, ROLES, sync_duty
from backend.operations.shift import ShiftEngine, shift_hour
from backend.seeds import classic, distribution_center
from backend.simulator import Simulator

#: Where the seed puts every robot: the first free cell of its home zone its
#: body may stop on that no job needs, else the nearest such cell (the tote
#: and pallet aisles are all slot faces, so their robots start on the main
#: aisle beside them); the picker on Pick 1's work cell; arms on the station
#: of their own pack cell; drones on their pad.
PLACES = {
    "TR50-101": (4, 12), "TR50-102": (5, 12), "TR50-103": (1, 16), "TR50-201": (7, 13),
    "TR50-202": (7, 15), "PK30-203": (20, 14), "SC1-204": (7, 1), "PF1200-205": (7, 3),
    "PF1200-206": (7, 7), "HH300-207": (1, 7), "IX2-208": (19, 1), "IX2-209": (20, 1),
    "CX10-210": (23, 14), "CX10-211": (22, 16), "H1-212": (20, 6),
}
OFF_DUTY = {"E-10009", "E-10010"}   # Sasha is ON_LEAVE, Morgan TERMINATED
AMR_TOP_LEVEL = 1                   # AC-TR50's max_shelf_level
MOST_UNITS_A_LINE_ASKS = 3          # ShiftEngine._generate_customer_orders: 1–3 units a line


def make_twin(tmp_path, layout="distribution_center", demo=True):
    return DigitalTwin(log_dir=str(tmp_path / "logs"), data_dir=str(tmp_path / "data"),
                       persist_logs=False, demo=demo, demo_tasks=False, layout=layout)


@pytest.fixture
def floor(tmp_path):
    twin = make_twin(tmp_path)
    twin.seed_floor()
    return twin


@pytest.fixture
def fault_free(monkeypatch):
    for risk in ("COLLISION_RISK", "FALSE_SUCCESS_RISK", *FAULT_RISKS.values()):
        monkeypatch.setitem(CONFIG, risk, 0.0)


def boxes_of(twin, kind):
    return [box for box in twin.boxes.values() if box.kind is kind]


def test_every_active_asset_is_on_the_floor_bound_and_named_by_its_model(floor):
    robots = {robot.name: robot for robot in floor.robots.values()}
    assert {name: robot.position for name, robot in robots.items()} == PLACES
    assert {name: robot.asset_id for name, robot in robots.items()} == {
        name: f"AST-000{name.split('-')[1]}" for name in PLACES}
    assert floor.fleet.robot_for_asset("AST-000213") is None          # decommissioned: never placed
    for robot in robots.values():
        assert robot.robot_class == floor.inventory.get_robot(robot.asset_id)["model"]["embodiment_class"]
    held = {robot.name for robot in robots.values() if robot.status is RobotStatus.STOPPED}
    assert held == {"TR50-103"} and robots["TR50-103"].fleet_hold == "MAINTENANCE"   # its open work order


def test_no_robot_starts_where_a_job_will_need_it(floor):
    # TR50-202 runs the recalled release 2.2.1, so the gate refuses even its
    # move to parking: the first cell of its home zone (8,15) is a slot face, and
    # a tote job sent to that face would wait for good. Fails if the seed took
    # the first stoppable cell of the home zone instead.
    needed = floor.warehouse.cells_jobs_need()
    for robot in floor.robots.values():
        if robot.mobility.is_fixed:
            assert floor.warehouse.fixed_stations[robot.position] == \
                floor.inventory.get_robot(robot.asset_id)["home_zone"]
            continue
        assert floor.warehouse.may_stop(robot.position, robot.mobility), robot.name
        if robot.robot_class != "PICKER":                               # the picker works on its work cell
            assert robot.position not in needed, robot.name


def test_the_ten_workers_are_bound_on_a_shift_that_starts_with_the_clock(floor):
    operators = {operator.worker_id: operator for operator in floor.operators.values()}
    assert sorted(operators) == [f"E-{10000 + number}" for number in range(1, 11)]
    for worker_id, operator in operators.items():
        assert operator.name == floor.inventory.get_worker(worker_id)["display_name"]
        assert operator.shift_start_hour == CONFIG["SHIFT_START_HOUR"] == shift_hour(0.0)
        if worker_id in OFF_DUTY:
            assert (operator.status, operator.zone) == (OperatorStatus.OFF_DUTY, None)
        else:
            assert (operator.status, operator.zone) == (OperatorStatus.AVAILABLE, HOME_ZONES[ROLES[worker_id]])
    # Duty follows the shift clock (06:00 at the start): a window that left
    # 06:00 out would send these eight off duty here.
    sync_duty(floor)
    assert sum(operator.status is OperatorStatus.AVAILABLE for operator in floor.operators.values()) == 8


def test_the_racks_hold_60_pallets_and_the_shelves_80_totes_over_40_skus(floor):
    pallets, totes = boxes_of(floor, BoxKind.PALLET), boxes_of(floor, BoxKind.TOTE)
    assert (len(pallets), len(totes), len(floor.boxes)) == (60, 80, 140)
    for box in pallets + totes:
        slot = floor.warehouse.slot(box.slot)
        assert slot.kind == box.kind.value and box.position == slot.cell
        location = floor.stock.location(box.slot)
        assert (location.box_id, location.sku, location.recorded_qty) == (box.id, box.sku, box.quantity)
        assert box.sku and box.quantity > 0                              # one SKU each
    assert {floor.warehouse.slot(box.slot).level for box in pallets} == {0, 1, 2, 3, 4}
    assert all(150.0 <= box.weight <= 900.0 for box in pallets)
    assert {floor.warehouse.slot(box.slot).level for box in totes} == {0, 1, 2}
    assert all(5.0 <= box.weight <= 25.0 for box in totes)
    assert len(floor.stock.skus()) == 40 == len({box.sku for box in totes})


def test_every_sku_has_a_tote_an_amr_reaches_holding_enough_for_any_order_line(floor):
    # The customer-order generator only orders a SKU some tote holds enough
    # of; a seed whose totes held too few units, or kept a SKU only on the
    # top level, would starve it.
    for sku in floor.stock.skus():
        reachable = [location for location in floor.stock.locations(sku)
                     if floor.find_box(location.box_id).kind is BoxKind.TOTE
                     and floor.warehouse.slot(location.slot_id).level <= AMR_TOP_LEVEL]
        assert any(location.recorded_qty >= MOST_UNITS_A_LINE_ASKS for location in reachable), sku
    for _ in range(30):
        floor.shift._generate_customer_orders()
    assert floor.shift.counters["skipped_orders"] == 0
    assert floor.shift.orders.counts()["CUSTOMER"]["OPEN"] == 30


def test_the_seed_is_deterministic_with_ada_and_no_demo_tasks(tmp_path, floor):
    again = make_twin(tmp_path / "again")
    again.seed_floor()

    def picture(twin):
        return ([(robot.name, robot.position) for robot in twin.robots.values()],
                [(box.name, box.slot, box.sku, box.quantity, box.weight) for box in twin.boxes.values()],
                [(operator.name, operator.zone, operator.status) for operator in twin.operators.values()])

    assert picture(again) == picture(floor)
    assert [agent.name for agent in floor.agents.values()] == ["Ada"]
    assert not floor.tasks.tasks and floor.shift.status == ShiftEngine.PAUSED


def test_seed_floor_seeds_an_empty_twin_once(tmp_path):
    twin = make_twin(tmp_path)
    assert twin.floor_seeded is False and not twin.robots                # the new floor still boots empty
    twin.seed_floor()
    assert twin.floor_seeded is True and len(twin.robots) == 15
    with pytest.raises(ValueError, match="already has robots"):
        twin.seed_floor()
    assert make_twin(tmp_path / "classic", layout="classic").floor_seeded is True   # its demo boot
    assert seeds.SEEDS == {"classic": classic.seed, "distribution_center": distribution_center.seed}


def test_reset_puts_the_seed_back_with_the_shift_paused(floor, fault_free):
    sim = Simulator(floor)
    floor.simulation_status = SimulationStatus.RUNNING
    floor.shift.start()
    for _ in range(300):
        sim.tick()
    floor.add_robot(name="Extra", robot_class="AMR")
    assert floor.shift.orders.orders
    floor.reset(demo_tasks=False)
    # Fails if reset() left the floor empty, as it did before seed_floor existed.
    assert {robot.name: robot.position for robot in floor.robots.values()} == PLACES
    assert len(floor.boxes) == 140 and len(floor.operators) == 10
    assert [agent.name for agent in floor.agents.values()] == ["Ada"]
    assert floor.shift.status == ShiftEngine.PAUSED and not floor.shift.orders.orders
    assert floor.floor_seeded is True and floor.tick_count == 0


def test_reset_keeps_an_unseeded_floor_empty_and_classic_as_its_demo(tmp_path):
    bare = make_twin(tmp_path / "bare")
    bare.reset(demo_tasks=False)
    assert not bare.robots and not bare.boxes and bare.floor_seeded is False
    classic_twin = make_twin(tmp_path / "classic", layout="classic", demo=False)
    assert not classic_twin.robots and classic_twin.floor_seeded is False
    classic_twin.reset(demo_tasks=False)                                  # classic resets to its demo, as always
    assert sorted(robot.name for robot in classic_twin.robots.values()) == ["Robo-01", "Robo-02"]


def test_load_demo_runs_the_classic_seed(tmp_path):
    twin = make_twin(tmp_path, layout="classic", demo=False)
    twin.load_demo(create_tasks=True)
    assert [(robot.name, robot.position, robot.asset_id) for robot in twin.robots.values()] == [
        ("Robo-01", (3, 8), "AST-000101"), ("Robo-02", (16, 8), "AST-000102")]
    assert sorted(box.name for box in twin.boxes.values()) == ["Box-A", "Box-B", "Box-C", "Box-D", "Box-E"]
    assert [operator.name for operator in twin.operators.values()] == ["Sam", "Lee"]
    assert [agent.name for agent in twin.agents.values()] == ["Ada"]
    assert len(twin.tasks.tasks) == 2 and twin.floor_seeded is True


def test_an_arm_takes_the_station_of_its_own_pack_cell(tmp_path):
    twin = make_twin(tmp_path)
    # Fails if spawning still took the first free station whatever the home zone:
    # CX10-211 alone would land on Pack 1's (23,14).
    assert twin.add_robot(name="CX10-211", asset_id="AST-000211").position == (22, 16)
    assert twin.add_robot(name="CX10-210", asset_id="AST-000210").position == (23, 14)


def test_a_robot_is_never_spawned_on_the_walkway(tmp_path):
    twin = make_twin(tmp_path)
    for name, asset, crossing in (("TR50-201", "AST-000201", (17, 10)), ("TR50-202", "AST-000202", (17, 13))):
        robot = twin.add_robot(name=name, asset_id=asset, position=crossing)
        # Fails if an explicit position were checked with passable(): a robot
        # may cross a walkway crossing but never stop on one.
        assert robot.position != crossing and twin.warehouse.cell_type(*robot.position) is not CellType.WALKWAY
        assert twin.warehouse.may_stop(robot.position, robot.mobility)
        assert abs(robot.position[0] - crossing[0]) + abs(robot.position[1] - crossing[1]) == 1


def test_only_the_classic_inventory_has_remote_sites_checking_in(tmp_path, monkeypatch):
    for layout, expected in (("distribution_center", 0), ("classic", 1)):
        twin = make_twin(tmp_path / layout, layout=layout)
        calls = []
        monkeypatch.setattr(twin.inventory, "touch_remote_reports", lambda **kwargs: calls.append(kwargs) or 0)
        twin.fleet.heartbeat()
        assert twin.fleet.profile == layout and len(calls) == expected, layout


def test_the_seeded_floor_keeps_completing_customer_orders(floor, fault_free):
    # Fails if the seed wedges the shift: with TR50-202 on the slot face (8,15)
    # customer orders stopped completing after about ten sim-minutes.
    sim = Simulator(floor)
    floor.simulation_status = SimulationStatus.RUNNING
    floor.shift.configure(seed=42, pace=2.0)
    floor.shift.start()
    done = []
    for tick in range(1, 8001):                                           # 8000 × 0.15 s = 20 sim-minutes
        sim.tick()
        if tick % 4000 == 0:
            done.append(floor.shift.orders.counts()["CUSTOMER"]["DONE"])
    assert done[0] >= 1 and done[1] > done[0], done
    assert not [robot.name for robot in floor.robots.values() if robot.status is RobotStatus.ERROR]
