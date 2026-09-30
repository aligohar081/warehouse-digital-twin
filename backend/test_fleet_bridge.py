"""FleetBridge: binding live robots/operators to the inventory, and the
runtime effects of inventory actions on the simulation."""
import pytest

from backend.digital_twin import DigitalTwin
from backend.models import RobotStatus, TaskStatus


def make_twin(tmp_path, **kwargs):
    return DigitalTwin(log_dir=str(tmp_path / "logs"), data_dir=str(tmp_path / "data"),
                       persist_logs=False, demo=True, demo_tasks=False, **kwargs)


@pytest.fixture
def twin(tmp_path):
    return make_twin(tmp_path)


def test_demo_robots_and_operators_bind_to_their_inventory_records(twin):
    robo1 = twin.find_robot("Robo-01")
    assert robo1.asset_id == "AST-000101" and twin.find_robot("Robo-02").asset_id == "AST-000102"
    assert robo1.firmware_version == "2.1.0"  # unchanged from before the inventory existed
    assert robo1.component_firmware["lidar"] == "2.0.1"
    assert robo1.status == RobotStatus.IDLE and robo1.fleet_hold is None
    sam, lee = twin.find_operator("Sam"), twin.find_operator("Lee")
    assert sam.worker_id == "E-10001" and sam.certifications == ["safety_inspection", "electrical_safety"]
    assert lee.worker_id == "E-10002" and lee.certifications == ["safety_inspection"]
    assert robo1.to_dict()["asset_id"] == "AST-000101" and sam.to_dict()["worker_id"] == "E-10001"


def test_add_robot_commissions_an_asset_for_its_class(twin):
    robot = twin.add_robot(name="Lifter", robot_class="FORKLIFT")
    record = twin.inventory.get_robot(robot.asset_id)
    assert record["model_code"] == "NW-PF1200" and record["site_code"] == "WH-01"
    assert robot.firmware_version == "2.1.0" == record["reported"]["software_version"]
    assert twin.fleet.robot_for_asset(robot.asset_id) is robot
    drone = twin.add_robot(name="Drone-1", model_code="CT-IX2")
    assert drone.robot_class == "DRONE"
    with pytest.raises(ValueError):
        twin.add_robot(name="Bad", robot_class="AMR", model_code="NW-PF1200")
    with pytest.raises(ValueError):
        twin.add_robot(name="Arm", model_code="FB-CX10")  # ARM joins the floor in sub-project C
    with pytest.raises(ValueError):
        twin.add_robot(name="Ghost", model_code="NOPE")
    with pytest.raises(ValueError):
        twin.add_robot(name="Twin", asset_id="AST-000101")  # already on the floor
    with pytest.raises(ValueError):
        twin.add_robot(name="Dead", asset_id="AST-000213")  # decommissioned
    assert twin.find_robot("Bad") is None and twin.find_robot("Twin") is None


def test_binding_an_asset_that_is_not_in_service_holds_the_robot(twin):
    robot = twin.add_robot(name="Workshop", asset_id="AST-000103")
    assert robot.status == RobotStatus.STOPPED and robot.fleet_hold == "MAINTENANCE"
    forklift = twin.add_robot(name="Remote", asset_id="AST-000205")
    assert forklift.robot_class == "FORKLIFT" and forklift.firmware_version == "2.1.1"


def test_add_operator_registers_a_worker_with_matching_credentials(twin):
    operator = twin.add_operator(name="Robin", certifications=["safety_inspection", "hazmat_handling"])
    assert operator.worker_id and operator.certifications == ["safety_inspection", "hazmat_handling"]
    worker = twin.inventory.get_worker(operator.worker_id)
    assert worker["display_name"] == "Robin" and worker["site_codes"] == ["WH-01"]
    assert [c["code"] for c in worker["credentials"]] == ["safety_inspection", "hazmat_handling"]
    odd = twin.add_operator(name="Quinn", certifications=["crane_rigging"])  # unknown code gets a definition
    assert odd.certifications == ["crane_rigging"]
    preset = twin.add_operator(name="Tess", role="SAFETY_INSPECTOR")
    assert preset.certifications == ["safety_inspection"]


def test_reset_reseeds_the_inventory_with_a_new_epoch(twin):
    epoch = twin.inventory.store.epoch()
    extra = twin.add_robot(name="Extra")
    twin.reset(demo_tasks=False)
    assert twin.inventory.store.epoch() != epoch
    assert twin.find_robot("Robo-01").asset_id == "AST-000101"
    assert not twin.inventory.has_asset(extra.asset_id)


def test_save_and_load_state_rebinds_by_id(twin, tmp_path):
    twin.find_robot("Robo-01").firmware_version = "0.9.0-beta"
    path = twin.save_state(str(tmp_path / "state.json"))
    twin.load_state(path)
    robo1 = twin.find_robot("Robo-01")
    assert robo1.asset_id == "AST-000101" and robo1.firmware_version == "0.9.0-beta"  # saved truth wins
    assert twin.find_operator("Sam").worker_id == "E-10001"


def test_inventory_file_persists_across_twins(tmp_path):
    path = str(tmp_path / "inventory.sqlite3")
    first = make_twin(tmp_path, inventory_path=path)
    first.fleet.mutate(first.inventory.admin_edit, "AST-000201", {"fleet_id": "FLEET-Z"}, "test")
    second = make_twin(tmp_path, inventory_path=path)
    assert second.inventory.get_robot("AST-000201")["fleet_id"] == "FLEET-Z"
    assert second.find_robot("Robo-01").asset_id == "AST-000101"


def test_the_existing_firmware_gate_still_sees_runtime_firmware(twin):
    twin.find_robot("Robo-01").firmware_version = "0.9.0-beta"
    task = twin.tasks.create_task({"type": "MOVE_ROBOT", "robot_id": "Robo-01", "destination": "loading_zone"})
    assert task.status is TaskStatus.FAILED and "unapproved firmware" in task.error
