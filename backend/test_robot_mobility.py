"""Robots move by their body on the new floor: the profile comes from the
bound asset's model, and spawn, planning, routing and stepping all use it
(multi-embodiment spec §5.1–§5.2). Classic robots are untouched."""
import pytest

from backend.digital_twin import DigitalTwin
from backend.models import CONFIG, ROBOT_CLASS_PRESETS, RobotStatus, SimulationStatus, TaskStatus
from backend.simulator import Simulator


def make_twin(tmp_path, layout="distribution_center"):
    return DigitalTwin(log_dir=str(tmp_path / "logs"), data_dir=str(tmp_path / "data"),
                       persist_logs=False, demo=True, demo_tasks=False, layout=layout)


@pytest.fixture
def twin(tmp_path):
    return make_twin(tmp_path)


@pytest.fixture
def sim(twin):
    simulator = Simulator(twin)
    twin.simulation_status = SimulationStatus.RUNNING
    return simulator


def run_until(sim, predicate, max_ticks=2000):
    for _ in range(max_ticks):
        if predicate():
            return
        sim.tick()
    assert predicate(), f"condition not met within {max_ticks} ticks"


def test_the_new_classes_can_still_charge_themselves():
    for robot_class in ("ARM", "HUMANOID"):
        assert "CHARGE_ROBOT" in ROBOT_CLASS_PRESETS[robot_class]["allowed_task_types"]


def test_a_robot_takes_its_profile_and_speed_from_its_asset_model(twin):
    forklift = twin.add_robot(name="PF1200-205", asset_id="AST-000205", position=(10, 3))
    assert forklift.robot_class == "FORKLIFT"
    assert (forklift.mobility.movement, forklift.mobility.clearance) == ("GROUND", "WIDE")
    assert forklift.speed == pytest.approx(0.8)  # 1.2 m/s over 1.5 m cells, not the preset's 1.2
    amr = twin.add_robot(name="TR50-201", asset_id="AST-000201")
    assert amr.mobility.clearance == "NARROW" and amr.speed == pytest.approx(1.0)
    assert twin.warehouse.zone_of_cell(amr.position).key == "parking_area"
    humanoid = twin.add_robot(name="H1-212", asset_id="AST-000212", position=(22, 7))
    assert humanoid.robot_class == "HUMANOID" and humanoid.mobility.supervision == "humanoid_supervision"
    assert twin.add_robot(name="Quick", asset_id="AST-000102", speed=3.0).speed == 3.0  # explicit wins
    assert forklift.to_dict()["mobility"]["clearance"] == "WIDE"


def test_classic_robots_have_no_profile_and_keep_their_preset_speed(tmp_path):
    classic = make_twin(tmp_path, layout="classic")
    assert all(robot.mobility is None for robot in classic.robots.values())
    forklift = classic.add_robot(name="Fork", robot_class="FORKLIFT")
    assert forklift.mobility is None and forklift.speed == 1.2
    assert forklift.to_dict()["mobility"] is None


def test_a_drone_spawns_on_its_pad(twin):
    drone = twin.add_robot(name="IX2-208", asset_id="AST-000208")
    assert twin.warehouse.cell_type(*drone.position).value == "DRONE_PAD"
    assert drone.layer == "GROUND" and drone.mobility.is_air
    elsewhere = twin.add_robot(name="IX2-209", asset_id="AST-000209", position=(10, 3))
    assert elsewhere.position in twin.warehouse.zones["drone_pad"].cells  # snapped to the pad


def test_an_arm_is_placed_on_its_station_and_nowhere_else(twin, tmp_path):
    arm = twin.add_robot(name="CX10-210", asset_id="AST-000210")
    assert arm.position == (23, 14) and arm.mobility.is_fixed
    second = twin.add_robot(name="CX10-211", asset_id="AST-000211", position=(22, 16))
    assert second.position == (22, 16)
    with pytest.raises(ValueError, match="already taken"):
        twin.add_robot(name="Spare", model_code="FB-CX10", position=(23, 14))
    with pytest.raises(ValueError, match="not a fixed station"):
        twin.add_robot(name="Loose", model_code="FB-CX10", position=(10, 3))
    with pytest.raises(ValueError, match="no station"):
        make_twin(tmp_path / "classic", layout="classic").add_robot(name="Arm", model_code="FB-CX10")


def test_a_forklift_drives_only_wide_cells(twin, sim):
    forklift = twin.add_robot(name="PF1200-205", asset_id="AST-000205", position=(10, 3))
    task = twin.tasks.create_task({"type": "MOVE_ROBOT", "robot_id": forklift.id,
                                   "destination": "outbound_staging"})
    visited = set()

    def arrived():
        visited.add(forklift.position)
        return task.is_terminal

    run_until(sim, arrived)
    assert task.status is TaskStatus.COMPLETED, task.error
    assert twin.warehouse.zone_of_cell(forklift.position).key == "outbound_staging"
    assert all(twin.warehouse.clearance(cell) == "WIDE" for cell in visited)
    assert visited & {(17, 10), (17, 11)}


def test_a_forklift_cannot_be_sent_down_a_tote_aisle(twin):
    forklift = twin.add_robot(name="PF1200-205", asset_id="AST-000205", position=(10, 3))
    task = twin.tasks.create_task({"type": "MOVE_ROBOT", "robot_id": forklift.id, "destination": "tote_aisle_1"})
    assert task.status is TaskStatus.FAILED and "not reachable" in task.error
    amr = twin.add_robot(name="TR50-201", asset_id="AST-000201")
    ok = twin.tasks.create_task({"type": "MOVE_ROBOT", "robot_id": amr.id, "destination": "tote_aisle_1"})
    assert ok.status is TaskStatus.PLANNING


def test_a_loaded_forklift_travels_slower(twin):
    forklift = twin.add_robot(name="PF1200-205", asset_id="AST-000205", position=(10, 3))
    forklift.move_accumulator = 0.0
    forklift.ready_to_step(0.5)
    empty = forklift.move_accumulator
    forklift.move_accumulator, forklift.carrying_box = 0.0, "box_999"
    forklift.ready_to_step(0.5)
    assert forklift.move_accumulator == pytest.approx(empty * CONFIG["LOADED_SPEED_FACTOR"])
    amr = twin.add_robot(name="TR50-201", asset_id="AST-000201")
    amr.move_accumulator, amr.carrying_box = 0.0, "box_998"
    amr.ready_to_step(0.5)
    assert amr.move_accumulator == pytest.approx(amr.speed * 0.5)  # only forklifts and haulers slow down


def test_reset_sends_a_robot_home_by_its_own_profile(twin):
    forklift = twin.add_robot(name="PF1200-205", asset_id="AST-000205", position=(7, 1))
    blocker = twin.add_robot(name="PF1200-206", asset_id="AST-000206", position=(7, 5))
    blocker.position = forklift.home  # someone parked on its home cell
    forklift.position = (10, 3)
    twin.reset_robot(forklift.id)
    # (8,1) is the nearest free drivable cell, but it is NARROW; (7,2) is the nearest WIDE one.
    assert forklift.position == (7, 2) and forklift.status is RobotStatus.IDLE


def test_auto_tasks_resolve_their_targets_from_a_robot_that_can_move(twin):
    # An arm sits on an isolated station cell, so nothing is reachable from it:
    # with the arm first in the fleet, AUTO targets must not start from there.
    twin.add_robot(name="CX10-210", asset_id="AST-000210")
    amr = twin.add_robot(name="TR50-201", asset_id="AST-000201")
    task = twin.tasks.create_task({"type": "MOVE_ROBOT", "destination": "outbound_staging"})
    assert task.status is TaskStatus.PLANNING, task.error
    charge = twin.tasks.create_task({"type": "CHARGE_ROBOT"})
    scored, _ = twin.tasks._score_candidates(charge)
    assert [entry[1] for entry in scored] == [amr]  # the arm is never a charging candidate


def test_the_auto_origin_is_the_first_robot_that_can_move(twin):
    assert twin.tasks._auto_origin() is None  # the new floor boots with no robots
    arm = twin.add_robot(name="CX10-210", asset_id="AST-000210")
    assert twin.tasks._auto_origin() == arm.position  # every robot is fixed: fall back to the first
    amr = twin.add_robot(name="TR50-201", asset_id="AST-000201")
    assert twin.tasks._auto_origin() == amr.position
