"""Layers and layer-aware traffic (multi-embodiment spec §5.3): a drone on
AIR over a ground robot is neither a blocker nor a collision."""
import pytest

from backend.ci_engine import CIEngine
from backend.digital_twin import DigitalTwin
from backend.models import RobotStatus, SimulationStatus, TaskStatus
from backend.robot import Robot
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


@pytest.fixture
def drone(twin):
    return twin.add_robot(name="IX2-208", asset_id="AST-000208", position=(20, 2))


@pytest.fixture
def amr(twin):
    return twin.add_robot(name="TR50-201", asset_id="AST-000201", position=(10, 3))


def test_a_drone_takes_off_and_lands_only_on_its_pad(twin, drone):
    twin.set_robot_layer(drone.id, "AIR")
    assert (drone.layer, drone.altitude_m) == ("AIR", 0.5)
    twin.set_robot_layer(drone.id, "air", altitude_m=4.5)  # climbing while airborne
    assert drone.altitude_m == 4.5
    drone.position = (12, 2)  # over a rack
    with pytest.raises(ValueError, match="only land on the drone pad"):
        twin.set_robot_layer(drone.id, "GROUND")
    drone.position = (20, 2)
    twin.set_robot_layer(drone.id, "GROUND")
    assert (drone.layer, drone.altitude_m) == ("GROUND", 0.0)
    with pytest.raises(ValueError, match="at most"):
        twin.set_robot_layer(drone.id, "AIR", altitude_m=40.0)
    with pytest.raises(ValueError, match="Unknown layer"):
        twin.set_robot_layer(drone.id, "SPACE")
    with pytest.raises(KeyError):
        twin.set_robot_layer("ghost", "AIR")


def test_only_a_flying_body_leaves_the_ground(twin, amr, tmp_path):
    with pytest.raises(ValueError, match="cannot fly"):
        twin.set_robot_layer(amr.id, "AIR")
    assert twin.set_robot_layer(amr.id, "GROUND") is amr  # already there: a no-op
    classic = DigitalTwin(log_dir=str(tmp_path / "c" / "logs"), data_dir=str(tmp_path / "c" / "data"),
                          persist_logs=False, demo=True, demo_tasks=False)
    classic_drone = classic.add_robot(name="Drone-1", robot_class="DRONE")
    with pytest.raises(ValueError, match="cannot fly"):  # no profile on classic: it drives as before
        classic.set_robot_layer(classic_drone.id, "AIR")


def test_a_drone_over_an_amr_is_not_a_collision(twin, sim, drone, amr):
    twin.set_robot_layer(drone.id, "AIR")
    drone.position = amr.position
    sim.tick()
    assert twin.statistics["collisions"] == 0
    assert drone.status is not RobotStatus.ERROR and amr.status is not RobotStatus.ERROR
    passed, _, _ = CIEngine(twin)._check_collisions()
    assert passed
    with pytest.raises(ValueError, match="different layers"):
        twin.register_collision(drone.id, amr.id)


def test_two_drones_in_one_air_cell_do_collide(twin, sim, drone):
    other = twin.add_robot(name="IX2-209", asset_id="AST-000209", position=(21, 2))
    twin.set_robot_layer(drone.id, "AIR")
    twin.set_robot_layer(other.id, "AIR")
    passed, message, _ = CIEngine(twin)._check_collisions()
    assert passed
    other.position = drone.position
    passed, message, _ = CIEngine(twin)._check_collisions()
    assert not passed and "on the AIR layer" in message
    sim.tick()
    assert twin.statistics["collisions"] == 1
    assert drone.status is RobotStatus.ERROR and other.status is RobotStatus.ERROR
    event = twin.events.query(event_type="COLLISION_DETECTED")[-1]
    assert event["data"]["layer"] == "AIR"


def test_occupancy_and_blocking_are_per_layer(twin, sim, drone, amr):
    twin.set_robot_layer(drone.id, "AIR")
    drone.position = (11, 3)
    assert (11, 3) not in twin.other_robot_cells(amr.id)
    assert (10, 3) not in twin.other_robot_cells(drone.id)
    assert (11, 3) in twin.other_robot_cells(amr.id, layer="AIR")
    assert sim._blocking_robot(amr, (11, 3)) is None
    assert twin.robot_cells("GROUND") == {(10, 3): amr.id}
    assert twin.robot_cells() == {(10, 3): amr.id, (11, 3): drone.id}
    ground_drone = twin.add_robot(name="IX2-209", asset_id="AST-000209", position=(21, 2))
    amr.position = (18, 2)  # next to the pad, both on the ground
    assert sim._blocking_robot(amr, ground_drone.position) is ground_drone


def test_an_amr_drives_under_a_hovering_drone(twin, sim, drone, amr):
    twin.set_robot_layer(drone.id, "AIR")
    drone.position = (11, 3)  # hovering over the AMR's route
    task = twin.tasks.create_task({"type": "MOVE_ROBOT", "robot_id": amr.id, "destination": "13,3"})
    for _ in range(200):
        if task.is_terminal:
            break
        sim.tick()
    assert task.status is TaskStatus.COMPLETED and amr.position == (13, 3)
    assert not twin.events.query(event_type="COLLISION_AVOIDED")
    assert twin.statistics["collisions"] == 0


def test_a_new_robot_may_spawn_under_a_hovering_drone(twin, drone):
    twin.set_robot_layer(drone.id, "AIR")
    drone.position = (4, 12)  # the first parking cell
    amr = twin.add_robot(name="TR50-201", asset_id="AST-000201")
    assert amr.position == (4, 12) and amr.layer == "GROUND"


def test_reset_lands_a_drone(twin, drone):
    twin.set_robot_layer(drone.id, "AIR", altitude_m=3.0)
    drone.position = (12, 2)
    twin.reset_robot(drone.id)
    assert (drone.layer, drone.altitude_m) == ("GROUND", 0.0)
    assert twin.warehouse.cell_type(*drone.position).value == "DRONE_PAD"


def test_layer_and_altitude_round_trip(twin, drone):
    twin.set_robot_layer(drone.id, "AIR", altitude_m=2.5)
    data = drone.to_dict()
    assert (data["layer"], data["altitude_m"]) == ("AIR", 2.5)
    restored = Robot.from_dict(data)
    assert (restored.layer, restored.altitude_m) == ("AIR", 2.5)
    legacy = {key: value for key, value in data.items() if key not in ("layer", "altitude_m")}
    assert (Robot.from_dict(legacy).layer, Robot.from_dict(legacy).altitude_m) == ("GROUND", 0.0)
