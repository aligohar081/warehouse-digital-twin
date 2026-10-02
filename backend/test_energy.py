"""Energy on the new floor (multi-embodiment spec §5.5): watt-hours per tick
by body and state, lift energy, drone flight, charging at the station or the
pad, mains-powered arms — and the classic battery model left alone."""
import pytest

from backend import energy
from backend.digital_twin import DigitalTwin
from backend.models import (
    CONFIG, Action, ActionType, BoxStatus, RobotStatus, SimulationStatus, TaskStatus, TaskType,
)
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


def ticks(sim, n):
    for _ in range(n):
        sim.tick()


def pct(robot, wh):
    return energy.wh_to_pct(robot.mobility, wh)


def test_watt_hours_per_tick_by_body_and_state(twin):
    forklift = twin.add_robot(name="PF1200-205", asset_id="AST-000205", position=(7, 3))
    profile = forklift.mobility
    assert CONFIG["ENERGY_TIME_SCALE"] == 10
    # 14 400 Wh over 7 h is 0.5714 W per second; x 0.15 s ticks x 10.
    assert energy.ground_wh_per_tick(profile, moving=True, loaded=False) == pytest.approx(0.857143, rel=1e-5)
    assert energy.ground_wh_per_tick(profile, moving=False, loaded=False) == pytest.approx(0.257143, rel=1e-5)
    assert energy.ground_wh_per_tick(profile, moving=True, loaded=True) == pytest.approx(1.2, rel=1e-5)
    # 745 kg (a 600 kg pallet and a tenth of the 1450 kg truck) raised 3 m.
    assert energy.lift_wh(profile, 600.0, 3.0) == pytest.approx(745 * 9.81 * 3 / 3600 / 0.6 * 10)
    assert energy.lift_wh(profile, 600.0, -3.0) == 0.0                     # lowering is free
    assert energy.charge_wh_per_tick(profile) == pytest.approx(0.75)        # 14 400 Wh in 8 h, x 10
    drone = twin.add_robot(name="IX2-208", asset_id="AST-000208")
    assert energy.flight_wh_per_tick(drone.mobility) == pytest.approx(150 / (22 * 60) * 0.15 * 10)
    assert energy.tick_wh(drone.mobility, "AIR", moving=False, loaded=False) == \
        energy.flight_wh_per_tick(drone.mobility)                           # hovering costs flight
    arm = twin.add_robot(name="CX10-210", asset_id="AST-000210")
    assert arm.mains_powered and not forklift.mains_powered
    assert energy.tick_wh(arm.mobility, "GROUND", moving=True, loaded=True) == 0.0
    assert (energy.charger_zone(drone.mobility), energy.charger_zone(profile), energy.charger_zone(None)) == \
        ("drone_pad", "charging_station", "charging_station")
    assert energy.energy_wh(forklift) == pytest.approx(14400.0)


def test_each_tick_drains_by_what_the_robot_did(twin, sim):
    amr = twin.add_robot(name="TR50-201", asset_id="AST-000201", position=(8, 13))
    idle = pct(amr, energy.ground_wh_per_tick(amr.mobility, moving=False, loaded=False))
    ticks(sim, 100)
    assert amr.battery == pytest.approx(100.0 - 100 * idle)                 # unrounded: 0.0016% a tick
    before = amr.battery
    sim._apply_energy({amr.id: amr.total_distance - 1})                      # it moved a cell
    assert before - amr.battery == pytest.approx(pct(amr, energy.ground_wh_per_tick(amr.mobility, True, False)))
    amr.carrying_box = "box_999"
    before = amr.battery
    sim._apply_energy({amr.id: amr.total_distance - 1})
    assert before - amr.battery == pytest.approx(pct(amr, energy.ground_wh_per_tick(amr.mobility, True, True)))
    drone = twin.add_robot(name="IX2-208", asset_id="AST-000208")
    twin.set_robot_layer(drone.id, "AIR")
    before = drone.battery
    sim._apply_energy({drone.id: drone.total_distance})
    assert before - drone.battery == pytest.approx(pct(drone, energy.flight_wh_per_tick(drone.mobility)))


def test_lifting_a_load_costs_its_potential_energy(twin, sim):
    forklift = twin.add_robot(name="PF1200-205", asset_id="AST-000205", position=(8, 3))
    pallet = twin.add_box(name="PAL-1", kind="PALLET", sku="SKU-01", quantity=40, weight=600.0, position=(8, 3))
    pallet.set_status(BoxStatus.CARRIED)
    forklift.carrying_box = pallet.id
    task = Task(twin.ids.next("task"), TaskType.MOVE_ROBOT, robot_id=forklift.id)
    task.actions = [Action(ActionType.LIFT_TO, "Lift to level 3", level=3, slot_id="PR-08-02-3"),
                    Action(ActionType.COMPLETE, "Complete task")]
    task.record(TaskStatus.ASSIGNED, "Assigned a hand-built plan")
    twin.tasks.tasks[task.id] = task
    forklift.current_task = task.id
    n = 0
    while not task.is_terminal:
        sim.tick()
        n += 1
    idle = pct(forklift, energy.ground_wh_per_tick(forklift.mobility, moving=False, loaded=True))
    lift = pct(forklift, energy.lift_wh(forklift.mobility, 600.0, 3.0))
    assert 100.0 - forklift.battery == pytest.approx(lift + n * idle)
    lifted = twin.events.query(task_id=task.id, event_type="LIFTED")[0]["data"]
    assert lifted["energy_wh"] == pytest.approx(energy.lift_wh(forklift.mobility, 600.0, 3.0), abs=1e-3)


def test_an_arm_runs_on_mains_power(twin, sim):
    arm = twin.add_robot(name="CX10-210", asset_id="AST-000210")
    arm.battery = 10.0                                                        # nonsense for mains power
    ticks(sim, 30)
    assert arm.battery == 10.0 and not twin.tasks.tasks                      # no drain, no auto-charge
    task = twin.tasks.create_task({"type": "CHARGE_ROBOT", "robot_id": arm.id})
    assert task.status is TaskStatus.FAILED and task.error == "CX10-210 is mains-powered and never needs charging"
    with pytest.raises(ValueError, match="mains-powered"):
        twin.request_charge(arm.id)


def test_a_ground_robot_charges_at_its_body_rate(twin, sim):
    forklift = twin.add_robot(name="PF1200-205", asset_id="AST-000205", position=(5, 12))
    forklift.battery = 50.0
    task = twin.tasks.create_task({"type": "CHARGE_ROBOT", "robot_id": forklift.id})
    ticks(sim, 30)
    assert forklift.status is RobotStatus.CHARGING
    assert twin.warehouse.cell_type(*forklift.position).value == "CHARGING"
    before = forklift.battery
    ticks(sim, 10)
    assert forklift.battery - before == pytest.approx(10 * pct(forklift, 0.75))
    assert task.status is not TaskStatus.FAILED


def test_a_low_drone_charges_on_its_pad_not_at_the_station(twin, sim):
    drone = twin.add_robot(name="IX2-208", asset_id="AST-000208", position=(20, 2))
    drone.battery = 15.0
    sim.tick()
    assert drone.status is RobotStatus.CHARGING and drone.position == (20, 2)   # already on its charger
    assert not twin.tasks.tasks
    before = drone.battery
    ticks(sim, 10)
    assert drone.battery - before == pytest.approx(10 * pct(drone, energy.charge_wh_per_tick(drone.mobility)))


def test_a_flying_drone_lands_on_a_free_pad_cell_to_charge(twin, sim):
    drone = twin.add_robot(name="IX2-208", asset_id="AST-000208", position=(20, 2))
    twin.add_robot(name="IX2-209", asset_id="AST-000209", position=(19, 3))
    twin.set_robot_layer(drone.id, "AIR", altitude_m=2.0)
    drone.position = (18, 3)                                                  # out over the aisle
    drone.battery = 15.0
    sim.tick()
    charge = next(task for task in twin.tasks.tasks.values() if task.type is TaskType.CHARGE_ROBOT)
    for _ in range(200):
        if drone.status is RobotStatus.CHARGING:
            break
        sim.tick()
    assert drone.status is RobotStatus.CHARGING and drone.layer == "GROUND"
    assert twin.warehouse.cell_type(*drone.position).value == "DRONE_PAD" and drone.position != (19, 3)
    assert [a.type.value for a in charge.actions] == ["NAVIGATE", "LAND", "CHARGE", "COMPLETE"]


def test_an_unreachable_charger_is_asked_for_only_every_twenty_ticks(twin, sim):
    for number, cell in enumerate(twin.warehouse.zones["charging_station"].cells):
        twin.add_robot(name=f"Park-{number}", model_code="AC-TR50", position=cell)  # every charger taken
    amr = twin.add_robot(name="TR50-201", asset_id="AST-000201", position=(5, 12))
    amr.battery = 15.0
    ticks(sim, 30)
    asked = [t for t in twin.tasks.tasks.values() if t.type is TaskType.CHARGE_ROBOT and t.robot_id == amr.id]
    assert len(asked) == 2 and all(t.status is TaskStatus.FAILED for t in asked)


def test_the_classic_battery_model_is_unchanged(tmp_path):
    classic = DigitalTwin(log_dir=str(tmp_path / "logs"), data_dir=str(tmp_path / "data"),
                          persist_logs=False, demo=True, demo_tasks=False)
    simulator = Simulator(classic)
    classic.simulation_status = SimulationStatus.RUNNING
    robot = classic.find_robot("Robo-01")
    for _ in range(50):
        simulator.tick()
    assert robot.battery == 100.0                                             # no idle drain on classic
    task = classic.tasks.create_task({"type": "MOVE_ROBOT", "robot_id": "Robo-01", "destination": "6,8"})
    for _ in range(100):
        if task.is_terminal:
            break
        simulator.tick()
    assert task.status is TaskStatus.COMPLETED and robot.battery == 100.0 - robot.total_distance
