"""Plan 1b's load-bearing residuals on the distribution-centre floor
(multi-embodiment spec §5.2, §6, §7, §9; plan 1b's final-fix-wave rulings).

- Every cell of a one-lane (NARROW) aisle is a cell jobs need, so an idle
  robot left on the top aisle's x=18 column — the only way to each pick
  station's tote drop — moves to parking instead of wedging the station.
- A load set down by a job that ends early goes to the nearest cell the body
  may stop on, never a walkway crossing, so it can be fetched again; that
  includes a box the robot already held when the job began (a DELIVER_BOX
  after a PICK_BOX).
- A picker's unit goes back into the tote it came from when its pick ends
  early, or fails if that tote can't take it.
- The older job types and the charge detour plan through other robots'
  cells instead of failing on traffic.

Classic robots (no floor profile) behave exactly as before.
"""
import pytest

from backend.digital_twin import DigitalTwin
from backend.jobs import tote_is_home
from backend.models import CONFIG, BoxStatus, SimulationStatus, TaskStatus
from backend.simulator import Simulator
from backend.task_planner import PlanningError

CHECK = CONFIG["SHIFT_CHECK_EVERY_TICKS"]


@pytest.fixture
def twin(tmp_path):
    return DigitalTwin(log_dir=str(tmp_path / "logs"), data_dir=str(tmp_path / "data"),
                       persist_logs=False, demo=True, demo_tasks=False, layout="distribution_center")


@pytest.fixture
def sim(twin):
    simulator = Simulator(twin)
    twin.simulation_status = SimulationStatus.RUNNING
    return simulator


def ticks(sim, count):
    for _ in range(count):
        sim.tick()


def tick_until(sim, condition, max_ticks=3000):
    for _ in range(max_ticks):
        if condition():
            return
        sim.tick()
    assert condition(), f"not reached within {max_ticks} ticks"


def tasks_of(twin, task_type, robot=None):
    return [task for task in twin.tasks.tasks.values() if task.type.value == task_type
            and (robot is None or task.robot_id == robot.id)]


def busy_elsewhere(twin):
    """Work on the floor: a forklift charging at the charging station (a long job)."""
    fork = twin.add_robot(name="PF1200-205", asset_id="AST-000205", position=(2, 13))
    fork.battery = 10.0
    return twin.request_charge(fork.id)


# --------------------------------------------------------------------------- #
# The x=18 gap: every cell of a one-lane aisle is a cell jobs need
# --------------------------------------------------------------------------- #
def test_the_whole_top_aisle_column_and_every_crossing_are_cells_jobs_need(twin):
    need = twin.warehouse.cells_jobs_need()
    # (18,13)-(18,17) have a drivable neighbour on both axes (a station pocket,
    # a crossing), so the one-lane geometry alone missed them.
    assert all((18, y) in need for y in range(12, 19))
    assert all(cell in need for cell in twin.warehouse.crossings)
    # returns_qc is NARROW too, but a place, not an aisle: the humanoid's home.
    assert not [cell for cell in twin.warehouse.zones["returns_qc"].cells if cell in need]


@pytest.mark.parametrize("squat, station, drop", [((18, 14), "pick_station_1", (19, 14)),
                                                  ((18, 16), "pick_station_2", (19, 16))],
                         ids=["Pick 1", "Pick 2"])
def test_an_idle_robot_on_a_stations_only_access_moves_away(twin, sim, squat, station, drop):
    squatter = twin.add_robot(name="TR50-101", asset_id="AST-000101", position=squat)   # its job ended there
    worker = twin.add_robot(name="TR50-201", asset_id="AST-000201", position=(8, 15))
    tote = twin.add_box(name="TOTE-1", kind="TOTE", sku="S1", quantity=20, weight=21.5, slot="TS-11-12-0")
    job = twin.tasks.create_task({"type": "TOTE_TO_STATION", "box_id": tote.id, "robot_id": worker.id,
                                  "station": station})
    # Without the NARROW cells in cells_jobs_need the job waits behind the
    # squatter for good (BLOCKED, no MOVE_ROBOT ever issued).
    tick_until(sim, lambda: job.is_terminal)
    assert job.status is TaskStatus.COMPLETED, job.error
    assert tote.position == drop
    moves = tasks_of(twin, "MOVE_ROBOT", squatter)
    assert moves and moves[0].internal and moves[0].destination == "parking_area"
    assert squatter.position != squat


def test_an_idle_humanoid_at_home_in_returns_qc_stays_there(twin, sim):
    busy_elsewhere(twin)
    humanoid = twin.add_robot(name="H1-212", asset_id="AST-000212", position=(22, 7))
    ticks(sim, 3 * CHECK)
    # Would fail if every NARROW cell (returns_qc's included) were a cell jobs need.
    assert not tasks_of(twin, "MOVE_ROBOT", humanoid) and humanoid.position == (22, 7)


# --------------------------------------------------------------------------- #
# Set-downs: never on a walkway crossing
# --------------------------------------------------------------------------- #
def order_floor(twin):
    """Two AMRs, the picker, both arms and one tote for each of six SKUs."""
    for name, asset, cell in (("TR50-201", "AST-000201", (8, 13)), ("TR50-101", "AST-000101", (8, 15)),
                              ("PK30-203", "AST-000203", (20, 14))):
        twin.add_robot(name=name, asset_id=asset, position=cell)
    twin.add_robot(name="CX10-210", asset_id="AST-000210")
    twin.add_robot(name="CX10-211", asset_id="AST-000211")
    for number in range(6):
        twin.add_box(name=f"TOTE-{number}", kind="TOTE", sku=f"SKU-00{number + 1}", quantity=20, weight=21.5,
                     slot=f"TS-{8 + number:02d}-12-0")


def carrier_of(twin, box):
    return next((robot for robot in twin.robots.values() if robot.carrying_box == box.id), None)


def on_a_crossing(twin, box):
    carrier = carrier_of(twin, box)
    return carrier is not None and twin.warehouse.is_crossing(carrier.position)


def test_a_tote_leg_ended_on_a_crossing_sets_the_tote_down_off_the_walkway(twin, sim):
    order_floor(twin)
    order = twin.shift.orders.customer([{"sku": "SKU-002", "units": 1}], "dock_4")
    tote = twin.find_box("TOTE-1")
    tick_until(sim, lambda: on_a_crossing(twin, tote))
    carrier = carrier_of(twin, tote)
    twin.shift.orders._fail(order, "the test failed it on the crossing")   # cancels the tote leg
    assert carrier.carrying_box is None and tote.status is BoxStatus.STORED
    # Set down where the AMR stood, the tote sat on (17,13): no robot may stop
    # there, so its RETURN_TOTE was refused ("has no route to the job").
    assert not twin.warehouse.is_crossing(tote.position)
    assert twin.warehouse.may_stop(tote.position, carrier.mobility)
    assert tote.slot == "TS-09-12-0" and twin.stock.box_in("TS-09-12-0") == tote.id
    back = tasks_of(twin, "RETURN_TOTE")
    assert len(back) == 1 and back[0].status is not TaskStatus.FAILED, back[0].error
    tick_until(sim, lambda: back[0].is_terminal)
    assert back[0].status is TaskStatus.COMPLETED, back[0].error
    assert tote_is_home(twin, tote)


def test_a_pallet_leg_failed_on_a_crossing_sets_the_pallet_down_off_the_walkway(twin, sim):
    fork = twin.add_robot(name="PF1200-205", asset_id="AST-000205", position=(7, 4))
    pallet = twin.add_box(name="PAL-1", kind="PALLET", sku="SKU-P", quantity=10, weight=300.0, slot="PR-08-02-0")
    job = twin.tasks.create_task({"type": "RETRIEVE_PALLET", "box_id": pallet.id})
    assert job.status is TaskStatus.PLANNING, job.error
    tick_until(sim, lambda: on_a_crossing(twin, pallet))          # (17,10) or (17,11), the wide crossings
    twin.tasks.fail_task(job, "the forklift lost its way")
    assert fork.carrying_box is None and pallet.status is BoxStatus.STORED and pallet.slot is None
    assert not twin.warehouse.is_crossing(pallet.position)
    assert twin.warehouse.may_stop(pallet.position, fork.mobility)
    again = twin.tasks.create_task({"type": "LOAD_TRUCK", "box_id": pallet.id})
    assert again.status is TaskStatus.PLANNING, again.error       # it can be fetched from where it lies


def test_a_cancelled_deliver_after_a_pick_box_sets_the_box_down(twin, sim):
    amr = twin.add_robot(name="TR50-201", asset_id="AST-000201", position=(8, 11))
    box = twin.add_box(name="BOX-1", kind="TOTE", sku="S", quantity=1, weight=5.0, position=(12, 11))
    pick = twin.tasks.create_task({"type": "PICK_BOX", "robot_id": amr.id, "box_id": box.id})
    tick_until(sim, lambda: pick.is_terminal)
    assert pick.status is TaskStatus.COMPLETED and amr.carrying_box == box.id
    deliver = twin.tasks.create_task({"type": "DELIVER_BOX", "robot_id": amr.id, "box_id": box.id,
                                      "destination": "outbound_staging"})
    ticks(sim, 10)
    twin.tasks.cancel_task(deliver.id)
    # The box is still tagged with the completed PICK_BOX; a set-down that
    # only looks at box.assigned_task leaves the AMR holding it for good.
    assert amr.carrying_box is None and box.status is BoxStatus.STORED
    assert box.assigned_robot is None and box.assigned_task is None
    assert box.position == amr.position
    move = twin.tasks.create_task({"type": "PICK_AND_DELIVER", "box_id": box.id, "destination": "intake_staging"})
    tick_until(sim, lambda: move.is_terminal)
    assert move.status is TaskStatus.COMPLETED, move.error


# --------------------------------------------------------------------------- #
# A picker's unit goes back into its tote
# --------------------------------------------------------------------------- #
def picker_holding_a_unit(twin, sim):
    picker = twin.add_robot(name="PK30-203", asset_id="AST-000203", position=(20, 14))
    tote = twin.add_box(name="TOTE-1", kind="TOTE", sku="SKU-02", quantity=6, weight=7.5, slot="TS-10-12-1")
    tote.position = (19, 14)                                     # brought to Pick 1's tote drop
    tote.set_status(BoxStatus.DELIVERED)
    weights = (tote.declared_weight_kg, tote.true_weight_kg)
    pick = twin.tasks.create_task({"type": "PICK_ITEMS", "box_id": tote.id, "quantity": 2})
    assert pick.status is TaskStatus.PLANNING, pick.error
    tick_until(sim, lambda: picker.carrying_box is not None)
    item = twin.find_box(picker.carrying_box)
    assert item.kind.value == "ITEM" and twin.stock.location("TS-10-12-1").recorded_qty == 5
    return picker, tote, pick, item, weights


def test_a_pick_cancelled_with_a_unit_in_hand_puts_it_back_in_its_tote(twin, sim):
    picker, tote, pick, item, weights = picker_holding_a_unit(twin, sim)
    twin.tasks.cancel_task(pick.id)
    # Before the fix the unit lay STORED on the work cell and the tote stayed one short.
    assert picker.carrying_box is None and item.id not in twin.boxes
    location = twin.stock.location("TS-10-12-1")
    assert (location.recorded_qty, location.true_qty, tote.quantity) == (6, 6, 6)
    assert (tote.declared_weight_kg, tote.true_weight_kg) == pytest.approx(weights)


def test_a_unit_whose_tote_has_gone_fails(twin, sim):
    picker, tote, pick, item, _ = picker_holding_a_unit(twin, sim)
    tote.position = (19, 16)                                     # taken away while the picker held the unit
    twin.tasks.fail_task(pick, "the picker lost power")
    assert picker.carrying_box is None and item.status is BoxStatus.FAILED
    assert item.id in twin.boxes and item.position == picker.position
    location = twin.stock.location("TS-10-12-1")
    assert (location.recorded_qty, location.true_qty) == (5, 5)   # the unit is out of the tote for good


def test_only_a_tote_in_the_ledger_takes_units_back(twin):
    tote = twin.add_box(name="TOTE-1", kind="TOTE", sku="SKU-02", quantity=6, weight=7.5, slot="TS-10-12-1")
    loose = twin.add_box(name="TOTE-2", kind="TOTE", sku="SKU-02", quantity=6, weight=7.5, position=(21, 7))
    pallet = twin.add_box(name="PAL-1", kind="PALLET", sku="SKU-P", quantity=10, weight=300.0, slot="PR-08-02-0")
    with pytest.raises(ValueError, match="TOTE-2 is not in the stock ledger"):
        twin.stock.return_units(loose, 1)
    with pytest.raises(ValueError, match="PAL-1 is a PALLET, not a TOTE"):
        twin.stock.return_units(pallet, 1)
    with pytest.raises(ValueError, match="units must be a whole number of at least 1"):
        twin.stock.return_units(tote, 0)
    twin.stock.return_units(tote, 2)
    assert tote.quantity == 8 and twin.stock.location("TS-10-12-1").true_qty == 8


# --------------------------------------------------------------------------- #
# The older job types plan through traffic
# --------------------------------------------------------------------------- #
def pocketed(twin):
    """An AMR in Pick 1's pocket, (19,14), and an idle AMR on (18,14), its only way out."""
    amr = twin.add_robot(name="TR50-201", asset_id="AST-000201", position=(19, 14))
    twin.add_robot(name="TR50-101", asset_id="AST-000101", position=(18, 14))
    return amr


@pytest.mark.parametrize("payload, zone", [
    ({"type": "MOVE_ROBOT", "destination": "parking_area"}, "parking_area"),
    ({"type": "CHARGE_ROBOT"}, "charging_station"),
    ({"type": "PICK_AND_DELIVER", "box_id": "BOX-1", "destination": "intake_staging"}, "intake_staging"),
], ids=["MOVE_ROBOT", "CHARGE_ROBOT", "PICK_AND_DELIVER"])
def test_an_older_job_plans_through_a_robot_in_the_way(twin, sim, payload, zone):
    amr = pocketed(twin)
    twin.add_box(name="BOX-1", kind="TOTE", sku="S", quantity=1, weight=5.0, position=(20, 14))
    job = twin.tasks.create_task({**payload, "robot_id": amr.id})
    sim.tick()
    # With other robots' cells as walls this failed at once: "<zone> is not reachable right now".
    assert job.status is not TaskStatus.FAILED, job.error
    tick_until(sim, lambda: job.is_terminal)                     # the idle AMR parks; then it goes
    assert job.status is TaskStatus.COMPLETED, job.error
    end = job.actions[-2].target                                 # the last step before COMPLETE
    assert twin.warehouse.zone_of_cell(end).key == zone


def test_the_charge_detour_plans_through_a_robot_in_the_way(twin):
    amr = pocketed(twin)
    amr.battery = 3.0                                            # below the reserve: it must charge first
    job = twin.tasks.create_task({"type": "MOVE_ROBOT", "robot_id": amr.id, "destination": "20,14"})
    actions = twin.planner.plan(job, amr)                        # raised "Charging is not reachable right now"
    assert actions[0].description == "Detour to Charging"
    assert twin.warehouse.zone_of_cell(actions[0].target).key == "charging_station"


def test_a_zone_cell_another_robot_stands_on_is_never_the_one_chosen(twin):
    amr = twin.add_robot(name="TR50-201", asset_id="AST-000201", position=(7, 13))
    twin.add_robot(name="TR50-101", asset_id="AST-000101", position=(6, 13))   # parking's nearest cell
    job = twin.tasks.create_task({"type": "MOVE_ROBOT", "robot_id": amr.id, "destination": "parking_area"})
    # Would fail if the planner dropped other robots' cells altogether
    # instead of only letting routes pass them.
    target = twin.planner.plan(job, amr)[0].target
    assert target != (6, 13) and twin.warehouse.zone_of_cell(target).key == "parking_area"


def test_a_full_zone_still_fails_at_plan_time(twin):
    amr = twin.add_robot(name="TR50-201", asset_id="AST-000201", position=(7, 13))
    for number, cell in enumerate(twin.warehouse.zones["parking_area"].cells):
        twin.add_robot(name=f"Parked-{number}", model_code="AC-TR50", position=cell)
    job = twin.tasks.create_task({"type": "MOVE_ROBOT", "robot_id": amr.id, "destination": "parking_area"})
    with pytest.raises(PlanningError, match="Parking is not reachable right now"):
        twin.planner.plan(job, amr)
