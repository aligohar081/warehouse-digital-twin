"""People and drones over a long shift (multi-embodiment spec §5.5, §6, §9.1,
§10.1, §11.3, §11.4): a drone idle on its pad tops up whenever it isn't full
and can still be sent on a count while it charges; a count no drone can fly
names every drone's energy; a person who steps aside for a forklift stands
in the refuge for the whole hold; Jordan heads where the humanoid is going;
duty is synced before anyone is moved; and a technician with no jam to clear
goes to a robot whose asset has an open work order."""
import pytest

from backend import people
from backend.digital_twin import DigitalTwin
from backend.models import CONFIG, OperatorStatus, RobotStatus, SimulationStatus, TaskStatus, TaskType
from backend.operations import move_people
from backend.operations.activities import YIELD_HOLD_S
from backend.operations.shift import DEFAULT_RATES
from backend.simulator import Simulator

CHECK = 20                                     # CONFIG["SHIFT_CHECK_EVERY_TICKS"]
WORKERS = {"Sam": "E-10001", "Noor": "E-10003", "Mateo": "E-10004", "Jordan": "E-10006"}


@pytest.fixture
def twin(tmp_path):
    return DigitalTwin(log_dir=str(tmp_path / "logs"), data_dir=str(tmp_path / "data"), persist_logs=False,
                       demo=True, demo_tasks=False, layout="distribution_center")


@pytest.fixture
def sim(twin):
    simulator = Simulator(twin)
    twin.simulation_status = SimulationStatus.RUNNING
    return simulator


def crew(twin, *names):
    return [twin.add_operator(name=name, worker_id=WORKERS[name]) for name in names]


def quiet_shift(twin):
    """The shift running — so the engine moves people — with nothing generated."""
    twin.shift.configure(rates={stream: 0.0 for stream in DEFAULT_RATES})
    twin.shift.start()


def events(twin, event_type, robot):
    return [e for e in twin.events.query(event_type=event_type) if e.get("robot_id") == robot.id]


# ---- drones top up on their pads -------------------------------------------- #
def test_a_drone_idle_on_its_pad_tops_up_whenever_it_is_not_full(twin, sim):
    drone = twin.add_robot(name="IX2-208", asset_id="AST-000208", position=(20, 2))
    drone.battery = 70.0                                             # well above BATTERY_LOW
    sim.tick()
    assert drone.status is RobotStatus.CHARGING and not twin.tasks.tasks   # on its pad: no job needed
    for _ in range(1000):
        if drone.battery >= 100.0:
            break
        sim.tick()
    assert drone.battery == 100.0
    for _ in range(300):
        sim.tick()
    # Docked and full it stays on charge: going IDLE at 100% would drain at once and
    # start a new session every other tick.
    assert drone.status is RobotStatus.CHARGING and drone.battery == 100.0
    assert len(events(twin, "ROBOT_CHARGING", drone)) == 1 and len(events(twin, "ROBOT_CHARGED", drone)) == 1
    assert drone.charging_sessions == 1


def test_a_full_drone_back_from_a_flight_charges_on_its_pad(twin, sim):
    drone = twin.add_robot(name="IX2-208", asset_id="AST-000208", position=(20, 2))
    task = twin.tasks.create_task({"type": "CYCLE_COUNT", "face": "12,2"})
    for _ in range(1500):
        if task.is_terminal:
            break
        sim.tick()
    assert task.status is TaskStatus.COMPLETED, task.error
    landed = drone.battery
    assert CONFIG["BATTERY_LOW"] < landed < 100.0                    # BATTERY_LOW alone would never charge it
    sim.tick()
    assert drone.status is RobotStatus.CHARGING
    for _ in range(20):
        sim.tick()
    assert drone.battery > landed


def test_a_charging_drone_with_the_energy_for_a_count_is_still_sent_on_it(twin, sim):
    drone = twin.add_robot(name="IX2-208", asset_id="AST-000208", position=(20, 2))
    drone.battery = 80.0
    sim.tick()
    assert drone.status is RobotStatus.CHARGING
    task = twin.tasks.create_task({"type": "CYCLE_COUNT", "face": "12,2"})
    assert task.status is TaskStatus.PLANNING, task.error
    sim.tick()
    # Dock charging through a job of its own would leave the drone busy, and the
    # count waiting (or rejected) until it was full.
    assert task.robot_id == drone.id and drone.current_task == task.id
    for _ in range(200):
        if drone.layer == "AIR":
            break
        sim.tick()
    assert drone.layer == "AIR" and drone.status is not RobotStatus.CHARGING
    flying = drone.battery
    for _ in range(20):
        sim.tick()
    assert drone.battery < flying                                    # no longer charging


def test_a_count_no_drone_can_fly_names_every_drones_energy(twin):
    for name, asset, cell in (("TR50-201", "AST-000201", (8, 13)), ("TR50-101", "AST-000101", (8, 15)),
                              ("PF1200-205", "AST-000205", (7, 4)), ("SC1-204", "AST-000204", (7, 6))):
        twin.add_robot(name=name, asset_id=asset, position=cell)
    drones = [twin.add_robot(name="IX2-208", asset_id="AST-000208", position=(20, 2)),
              twin.add_robot(name="IX2-209", asset_id="AST-000209", position=(21, 2)),
              twin.add_robot(name="Drone-3", model_code="CT-IX2", position=(19, 2)),
              twin.add_robot(name="Drone-4", model_code="CT-IX2", position=(19, 1))]
    for drone in drones:
        drone.battery = 10.0
    task = twin.tasks.create_task({"type": "CYCLE_COUNT", "face": "12,2"})
    assert task.status is TaskStatus.FAILED
    # Every drone's energy is named in the reason list; the remainder count
    # covers the other robots.
    for drone in drones:
        assert f"{drone.name} can't fly it: has 15.0 Wh but the round trip needs" in task.error, task.error
    assert task.error.endswith("(and 4 more)")


# ---- a person stepping aside stands clear for the whole hold ---------------- #
def test_a_person_who_steps_aside_stands_in_the_refuge_for_the_whole_hold(twin, sim):
    (sam,) = crew(twin, "Sam")
    people.place(twin, sam, "intake_staging")
    forklift = twin.add_robot(name="PF1200-205", asset_id="AST-000205", position=(7, 4))
    quiet_shift(twin)
    twin.tasks.create_task({"type": "MOVE_ROBOT", "robot_id": forklift.id, "destination": "5,4"})
    arrived = left = None
    for _ in range(2000):
        sim.tick()
        if arrived is None and sam.zone not in (None, "intake_staging") and not sam.in_transit:
            arrived = twin.tick_count                                # standing in the refuge
        elif arrived is not None and sam.in_transit:
            left = twin.tick_count                                   # setting off back
            break
    assert arrived is not None and left is not None and sam.transit_to == "intake_staging"
    stood = (left - arrived) * sim.dt
    # Counting the hold from the start of the walk left ~12 s standing here.
    assert YIELD_HOLD_S <= stood <= YIELD_HOLD_S + CHECK * sim.dt


# ---- Jordan heads where the humanoid is going ------------------------------- #
def test_jordan_heads_where_the_humanoid_is_going_not_where_it_is(twin, sim):
    humanoid = twin.add_robot(name="H1-212", asset_id="AST-000212", position=(22, 7))
    (jordan,) = crew(twin, "Jordan")
    people.place(twin, jordan, "returns_qc")
    quiet_shift(twin)
    task = twin.tasks.create_task({"type": "MOVE_ROBOT", "robot_id": humanoid.id, "destination": "12,13"})
    walks = []
    for _ in range(2000):
        if task.is_terminal:
            break
        sim.tick()
        if jordan.in_transit and (not walks or walks[-1] != jordan.transit_to):
            walks.append(jordan.transit_to)
    assert task.status is TaskStatus.COMPLETED, task.error
    # Following the zone it is in, Jordan walked ne_floor, cross_aisle, top_aisle,
    # tote_aisle_1 and the move took 337 ticks, stopped for 202 of them.
    assert walks == ["cross_aisle", "tote_aisle_1"]
    assert twin.tick_count < 300


def test_a_humanoid_on_a_cell_no_zone_covers_is_supervised_from_beside_it(twin, sim):
    # (3,11) lies between the charging station and dock_2, in no zone; with no zone
    # to be near, a humanoid driving over it stopped there for good, Jordan beside it.
    assert not twin.warehouse.zones_of_cell((3, 11))
    humanoid = twin.add_robot(name="H1-212", asset_id="AST-000212", position=(3, 13))
    (jordan,) = crew(twin, "Jordan")
    people.place(twin, jordan, "charging_station")
    humanoid.position = (3, 11)
    assert people.supervision_status(twin, humanoid)[0]
    quiet_shift(twin)
    task = twin.tasks.create_task({"type": "MOVE_ROBOT", "robot_id": humanoid.id, "destination": "5,11"})
    for _ in range(600):
        if task.is_terminal:
            break
        sim.tick()
    assert task.status is TaskStatus.COMPLETED, (task.error, humanoid.wait_reason)


def test_jordan_stays_put_while_he_still_supervises_the_humanoid(twin):
    humanoid = twin.add_robot(name="H1-212", asset_id="AST-000212", position=(22, 7))
    (jordan,) = crew(twin, "Jordan")
    people.place(twin, jordan, "ne_floor")                           # shares an edge with returns_qc
    assert people.supervision_status(twin, humanoid)[0]
    move_people(twin.shift)
    assert jordan.zone == "ne_floor" and not jordan.in_transit       # a walk would only stop it


# ---- duty is synced before anyone is moved ---------------------------------- #
def test_someone_off_duty_is_never_placed_by_the_engine(twin):
    (sam,) = crew(twin, "Sam")
    twin.set_operator_shift(sam.id, 6, 7)
    assert sam.status is OperatorStatus.AVAILABLE and sam.zone is None
    twin.simulation_time = 3600.0                                    # 07:00: his shift is over
    move_people(twin.shift)
    assert sam.status is OperatorStatus.OFF_DUTY and sam.zone is None   # placed, then taken off, before


def test_someone_coming_on_duty_is_placed_at_once(twin):
    (sam,) = crew(twin, "Sam")
    twin.set_operator_shift(sam.id, 7, 15)
    move_people(twin.shift)
    assert sam.status is OperatorStatus.OFF_DUTY and sam.zone is None    # 06:00: not yet
    twin.simulation_time = 3600.0                                    # 07:00
    move_people(twin.shift)
    assert sam.status is OperatorStatus.AVAILABLE and sam.zone == "intake_staging"   # a check later, before


# ---- technicians and work orders -------------------------------------------- #
def test_the_inventory_lists_the_open_work_orders_in_one_read(twin):
    seeded = [wo["asset_id"] for wo in twin.inventory.open_work_orders()]
    assert seeded == ["AST-000103"]                                  # the demo seed's lidar replacement
    change = twin.inventory.open_work_order("AST-000201", "INSPECTION", "Check the lidar")
    assert [wo["asset_id"] for wo in twin.inventory.open_work_orders()] == ["AST-000103", "AST-000201"]
    twin.inventory.close_work_order(change["subject_id"], "Lidar cleaned")
    assert [wo["asset_id"] for wo in twin.inventory.open_work_orders()] == ["AST-000103"]


def test_a_technician_goes_to_a_robot_with_an_open_work_order(twin):
    amr = twin.add_robot(name="TR50-201", asset_id="AST-000201", position=(12, 13))
    noor, mateo = crew(twin, "Noor", "Mateo")
    move_people(twin.shift)
    assert (noor.zone, mateo.zone) == ("workshop", "workshop")
    change = twin.fleet.mutate(twin.inventory.open_work_order, amr.asset_id, "INSPECTION", "Check the lidar")
    move_people(twin.shift)
    assert (noor.transit_to, mateo.transit_to) == ("tote_aisle_1", "tote_aisle_1")
    for operator in (noor, mateo):
        people.place(twin, operator, operator.transit_to)
    twin.fleet.mutate(twin.inventory.close_work_order, change["subject_id"], "Lidar cleaned")
    move_people(twin.shift)
    assert (noor.transit_to, mateo.transit_to) == ("workshop", "workshop")


def test_a_technician_goes_first_to_the_work_order_that_names_them(twin):
    near = twin.add_robot(name="TR50-201", asset_id="AST-000201", position=(12, 13))
    parked = twin.add_robot(name="TR50-101", asset_id="AST-000101", position=(5, 13))
    noor, mateo = crew(twin, "Noor", "Mateo")
    move_people(twin.shift)
    twin.fleet.mutate(twin.inventory.open_work_order, parked.asset_id, "INSPECTION", "Check the bumper")
    twin.fleet.mutate(twin.inventory.open_work_order, near.asset_id, "INSPECTION", "Check the lidar",
                      technician_id=mateo.worker_id)
    move_people(twin.shift)
    assert mateo.transit_to == "tote_aisle_1"                        # his own, though TR50-101 sorts first
    assert noor.transit_to == "parking_area"                         # none of hers: the first robot by name


def test_a_work_order_inside_a_pack_cell_is_worked_from_beside_it(twin):
    arm = twin.add_robot(name="CX10-210", asset_id="AST-000210")
    (noor,) = crew(twin, "Noor")
    move_people(twin.shift)
    twin.fleet.mutate(twin.inventory.open_work_order, arm.asset_id, "INSPECTION", "Check the gripper")
    move_people(twin.shift)
    # Standing in the fenced cell would hold its arm (PERSON_IN_CELL) for as long as the order is open.
    assert noor.transit_to == "pick_station_1"
