"""Hardening for the §5.5 energy model (multi-embodiment spec): a robot that is
travelling pays the moving rate on every tick of the drive, not only on the
ticks it changes cell; a held robot (a safety wait or a traffic block) and a
robot working a step pay idle; and a drone hovering over a taken pad cell
charges on a free pad cell instead of failing to land on the taken one.

Classic robots keep the old battery model: test_energy.py's
test_the_classic_battery_model_is_unchanged covers that (no idle drain, and
exactly 1% a cell while driving)."""
import pytest

from backend import energy, people
from backend.digital_twin import DigitalTwin
from backend.models import Action, ActionType, BoxStatus, RobotStatus, SimulationStatus, TaskStatus, TaskType
from backend.simulator import Simulator
from backend.task_manager import Task


@pytest.fixture
def twin(tmp_path):
    return DigitalTwin(log_dir=str(tmp_path / "logs"), data_dir=str(tmp_path / "data"),
                       persist_logs=False, demo=True, demo_tasks=False, layout="distribution_center")


@pytest.fixture
def sim(twin):
    simulator = Simulator(twin)
    twin.simulation_status = SimulationStatus.RUNNING
    return simulator


def attach(twin, robot, actions):
    """Hand `robot` a task whose plan is exactly `actions` (then COMPLETE)."""
    task = Task(twin.ids.next("task"), TaskType.MOVE_ROBOT, robot_id=robot.id)
    task.actions = list(actions) + [Action(ActionType.COMPLETE, "Complete task")]
    task.record(TaskStatus.ASSIGNED, "Assigned a hand-built plan")
    twin.tasks.tasks[task.id] = task
    robot.current_task = task.id
    return task


def pct(robot, wh):
    return energy.wh_to_pct(robot.mobility, wh)


def used_wh(robot):
    return robot.mobility.battery.capacity_wh - energy.energy_wh(robot)


def planned_charge(twin, sim):
    """Tick until the idle drone has asked for a charge and the planner has dealt with it."""
    for _ in range(5):
        sim.tick()
        asked = [t for t in twin.tasks.tasks.values() if t.type is TaskType.CHARGE_ROBOT]
        if asked and (asked[0].actions or asked[0].status is TaskStatus.FAILED):
            return asked[0]
    raise AssertionError("no CHARGE_ROBOT task was planned")


def tick_and_measure(sim, robot):
    """One tick: the percent the robot's battery fell, and whether it changed cell."""
    battery, position = robot.battery, robot.position
    sim.tick()
    return battery - robot.battery, robot.position != position


@pytest.mark.parametrize("name, asset, start, destination", [
    ("TR50-201", "AST-000201", (8, 13), "20,13"),      # an AMR, 1.0 cells/s
    ("PF1200-205", "AST-000205", (7, 3), "19,3"),      # a forklift, 0.8 cells/s, round the racks
])
def test_a_drive_costs_the_route_energy_the_planner_estimates(twin, sim, name, asset, start, destination):
    robot = twin.add_robot(name=name, asset_id=asset, position=start)
    task = twin.tasks.create_task({"type": "MOVE_ROBOT", "robot_id": robot.id, "destination": destination})
    for _ in range(1000):
        if task.is_terminal:
            break
        sim.tick()
    assert task.status is TaskStatus.COMPLETED
    cells = robot.total_distance
    assert cells >= 12                       # long enough that a missed tick is not a rounding error
    profile = robot.mobility
    moving = energy.ground_wh_per_tick(profile, moving=True, loaded=False)
    idle = energy.ground_wh_per_tick(profile, moving=False, loaded=False)
    # route_wh bills cells / (speed x dt) ticks at the moving rate. The real drive differs by the
    # cell-boundary rounding (up to a tick of moving rate either way) and by the ticks outside the
    # drive (the dispatch tick and the finishing tick bill idle).
    assert used_wh(robot) == pytest.approx(energy.route_wh(profile, cells), abs=2 * moving + 2 * idle)
    # Billing only the ticks that change cell paid about 40% of the estimate (both bodies).
    assert used_wh(robot) > 0.9 * energy.route_wh(profile, cells)


def test_a_robot_held_in_a_safety_wait_bills_idle(twin, sim):
    sam = twin.add_operator(name="Sam", worker_id="E-10001")
    forklift = twin.add_robot(name="PF1200-205", asset_id="AST-000205", position=(7, 8))
    people.place(twin, sam, "pallet_aisle_1")
    twin.tasks.create_task({"type": "MOVE_ROBOT", "robot_id": forklift.id, "destination": "10,3"})
    moving = pct(forklift, energy.ground_wh_per_tick(forklift.mobility, moving=True, loaded=False))
    idle = pct(forklift, energy.ground_wh_per_tick(forklift.mobility, moving=False, loaded=False))
    drove = []
    for _ in range(100):
        drain, moved = tick_and_measure(sim, forklift)
        if forklift.wait_reason:
            break
        drove.append(drain)
    assert forklift.wait_reason == "PERSON_IN_AISLE" and forklift.position == (7, 3)   # held mid-route, 5 cells in
    assert drove.count(pytest.approx(moving)) >= 30                  # it was billed as driving on the way there
    held = [tick_and_measure(sim, forklift) for _ in range(20)]
    assert forklift.wait_reason == "PERSON_IN_AISLE"
    assert all(drain == pytest.approx(idle) and not moved for drain, moved in held)
    people.place(twin, sam, "workshop")                              # the aisle clears: it drives on and pays for it
    resumed = [tick_and_measure(sim, forklift) for _ in range(20)]
    assert forklift.wait_reason is None
    assert any(drain == pytest.approx(moving) for drain, _ in resumed)


def test_a_robot_blocked_by_traffic_bills_idle_on_every_blocked_tick(twin, sim):
    amr = twin.add_robot(name="TR50-201", asset_id="AST-000201", position=(8, 13))
    twin.tasks.create_task({"type": "MOVE_ROBOT", "robot_id": amr.id, "destination": "15,13"})
    idle = pct(amr, energy.ground_wh_per_tick(amr.mobility, moving=False, loaded=False))
    sim.tick()
    sim.tick()
    twin.add_robot(name="Parked", model_code="AC-TR50", position=(11, 13))   # stops in the one-wide aisle ahead
    held, statuses = [], set()
    for _ in range(60):
        drain, moved = tick_and_measure(sim, amr)
        if amr.blocked_by is not None and not moved:
            held.append(drain)
            statuses.add(amr.status)
    # WAITING on the first blocked tick, then MOVING again (the status resets each tick) until the
    # replan: the tick must bill idle whatever its status says.
    assert len(held) >= 3 and statuses == {RobotStatus.WAITING, RobotStatus.MOVING}
    assert all(drain == pytest.approx(idle) for drain in held)


def test_steps_are_not_billed_as_driving(twin, sim):
    forklift = twin.add_robot(name="PF1200-205", asset_id="AST-000205", position=(8, 3))
    pallet = twin.add_box(name="PAL-1", kind="PALLET", sku="SKU-01", quantity=40, weight=600.0, position=(8, 3))
    pallet.set_status(BoxStatus.CARRIED)
    forklift.carrying_box = pallet.id
    task = attach(twin, forklift, [
        Action(ActionType.LIFT_TO, "Lift to level 2", level=2, slot_id="PR-08-02-2"),
        Action(ActionType.PLACE, "Place PAL-1", box_id=pallet.id, slot_id="PR-08-02-2"),
    ])
    idle = pct(forklift, energy.ground_wh_per_tick(forklift.mobility, moving=False, loaded=True))
    placing = []
    while not task.is_terminal:
        drain, _ = tick_and_measure(sim, forklift)
        if forklift.activity == "PLACE":
            placing.append((forklift.status, drain))
    assert len(placing) >= 3 and {status for status, _ in placing} == {RobotStatus.DELIVERING}
    assert all(drain == pytest.approx(idle) for _, drain in placing)

    drone = twin.add_robot(name="IX2-208", asset_id="AST-000208", position=(20, 2))
    flight = pct(drone, energy.flight_wh_per_tick(drone.mobility))
    ground_idle = pct(drone, energy.ground_wh_per_tick(drone.mobility, moving=False, loaded=False))
    task = attach(twin, drone, [Action(ActionType.TAKEOFF, "Take off", params={"altitude_m": 2.0}),
                                Action(ActionType.LAND, "Land")])
    steps = []
    while not task.is_terminal:
        drain, _ = tick_and_measure(sim, drone)
        steps.append((drone.layer, drone.status, drone.activity, drain))
    in_air = [drain for layer, _, _, drain in steps if layer == "AIR"]
    landed = [(status, drain) for layer, status, activity, drain in steps if layer == "GROUND" and activity == "LAND"]
    assert in_air and all(drain == pytest.approx(flight) for drain in in_air)      # flight, hovering or climbing
    # The tick a LAND finishes the drone is back on the ground with its status still MOVING: idle, not driving.
    assert landed == [(RobotStatus.MOVING, pytest.approx(ground_idle))]


def test_a_drone_hovering_over_a_taken_pad_cell_lands_on_a_free_one(twin, sim):
    grounded = twin.add_robot(name="IX2-209", asset_id="AST-000209", position=(20, 2))
    flier = twin.add_robot(name="IX2-208", asset_id="AST-000208", position=(19, 3))
    twin.set_robot_layer(flier.id, "AIR", altitude_m=2.0)
    flier.position = (20, 2)                                           # directly above the grounded drone
    flier.battery = 15.0
    charge = planned_charge(twin, sim)
    target = charge.actions[0].target
    assert target != (20, 2) and twin.warehouse.cell_type(*target).value == "DRONE_PAD"
    for _ in range(300):
        if flier.status is RobotStatus.CHARGING:
            break
        sim.tick()
    assert flier.status is RobotStatus.CHARGING and flier.layer == "GROUND"
    assert flier.position == target and grounded.position == (20, 2)
    assert charge.status is not TaskStatus.FAILED


def test_a_hovering_drone_with_no_free_pad_cell_plans_no_landing(twin, sim):
    grounded = twin.add_robot(name="IX2-209", asset_id="AST-000209", position=(20, 2))
    flier = twin.add_robot(name="IX2-208", asset_id="AST-000208", position=(19, 3))
    twin.set_robot_layer(flier.id, "AIR", altitude_m=2.0)
    flier.position = (20, 2)
    for number, cell in enumerate(twin.warehouse.zones["drone_pad"].cells):
        if cell != grounded.position:
            twin.add_robot(name=f"Pad-{number}", model_code="CT-IX2", position=cell)   # every other pad cell taken
    flier.battery = 15.0
    charge = planned_charge(twin, sim)
    assert charge.status is TaskStatus.FAILED and "not reachable" in charge.error     # no plan, so no failed LAND
    assert not any(a.type is ActionType.LAND for a in charge.actions)
    assert flier.layer == "AIR"
