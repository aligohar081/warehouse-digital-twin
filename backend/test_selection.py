"""Capability-filtered robot selection and the physical gate (multi-embodiment
spec §9.1, §10.2): a robot is picked only if its body can do the job — the
right kind of body, box kind, payload, reach, supervision and a route it can
drive to the goal itself — and the same rules re-checked mid-job only flag."""
import pytest

from backend import people
from backend.agent_tools import TASK_TYPE_GUIDE
from backend.digital_twin import DigitalTwin
from backend.embodiment import MobilityProfile
from backend.jobs import JOB_PARAM_KEYS, JOB_SPECS, parse_cell
from backend.models import BoxStatus, SimulationStatus, TaskStatus
from backend.simulator import Simulator
from backend.task_manager import Task


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


def candidates(twin, task):
    scored, _ = twin.tasks._score_candidates(task)
    return [entry[1].name for entry in scored]


def test_a_pallet_goes_to_a_body_that_can_lift_it(twin):
    twin.add_robot(name="TR50-201", asset_id="AST-000201", position=(5, 6))     # right beside it
    forklift = twin.add_robot(name="PF1200-205", asset_id="AST-000205", position=(8, 8))
    hauler = twin.add_robot(name="HH300-207", asset_id="AST-000207", position=(2, 8))
    twin.add_box(name="PAL-1", kind="PALLET", sku="SKU-01", quantity=40, weight=600.0, position=(5, 5))
    task = twin.tasks.create_task({"type": "PICK_AND_DELIVER", "box_id": "PAL-1", "destination": "outbound_staging"})
    assert task.status is TaskStatus.PLANNING, task.error
    assert candidates(twin, task) == ["PF1200-205"]           # the AMR can't take a pallet; it's over 300 kg
    light = twin.add_box(name="PAL-2", kind="PALLET", sku="SKU-02", quantity=10, weight=250.0, position=(4, 7))
    hauled = twin.tasks.create_task({"type": "PICK_AND_DELIVER", "box_id": light.id, "destination": "outbound_staging"})
    assert candidates(twin, hauled)[0] == "HH300-207"         # closer, and 250 kg is within its 300
    assert twin.tasks.select_robot(hauled) is hauler and twin.tasks.select_robot(task) is forklift


def test_a_goal_is_never_snapped_to_a_neighbour_for_scoring(twin):
    forklift = twin.add_robot(name="PF1200-205", asset_id="AST-000205", position=(10, 11))
    amr = twin.add_robot(name="TR50-201", asset_id="AST-000201", position=(4, 13))
    task = twin.tasks.create_task({"type": "MOVE_ROBOT", "destination": "tote_aisle_1"})
    assert task.status is TaskStatus.PLANNING
    assert candidates(twin, task) == ["TR50-201"]             # the forklift could only stop beside the aisle
    nav = twin.navigation
    assert nav.distance(forklift.position, (10, 13), profile=forklift.mobility) == 0  # snapped onto its own cell
    assert nav.distance(forklift.position, (10, 13), profile=forklift.mobility, allow_goal_adjacent=False) is None
    assert not nav.path_exists(forklift.position, (10, 13), profile=forklift.mobility, allow_goal_adjacent=False)
    assert twin.tasks.select_robot(task) is amr


def test_a_named_robot_must_have_the_body_for_the_job(twin, sim):
    amr = twin.add_robot(name="TR50-201", asset_id="AST-000201", position=(5, 6))
    hauler = twin.add_robot(name="HH300-207", asset_id="AST-000207", position=(2, 8))
    arm = twin.add_robot(name="CX10-210", asset_id="AST-000210")
    humanoid = twin.add_robot(name="H1-212", asset_id="AST-000212", position=(22, 7))
    twin.add_box(name="PAL-1", kind="PALLET", sku="SKU-01", quantity=40, weight=600.0, position=(5, 5))
    rejected = {
        amr.id: "TR50-201 can't take PAL-1: cannot handle a PALLET (handles: TOTE, ITEM)",
        hauler.id: "HH300-207 can't take PAL-1: a 600 kg load is over its 300 kg payload limit",
    }
    for robot_id, error in rejected.items():
        task = twin.tasks.create_task({"type": "PICK_AND_DELIVER", "robot_id": robot_id, "box_id": "PAL-1",
                                       "destination": "outbound_staging"})
        assert task.status is TaskStatus.FAILED and task.error == error
    fixed = twin.tasks.create_task({"type": "MOVE_ROBOT", "robot_id": arm.id, "destination": "22,13"})
    assert fixed.error.startswith("CX10-210 is not configured to run MOVE_ROBOT tasks")
    twin.set_robot_capabilities(arm.id, None)                  # even unrestricted, its body can't move
    fixed = twin.tasks.create_task({"type": "MOVE_ROBOT", "robot_id": arm.id, "destination": "22,13"})
    assert fixed.error == "CX10-210 is fixed equipment and can't do MOVE_ROBOT"
    alone = twin.tasks.create_task({"type": "MOVE_ROBOT", "robot_id": humanoid.id, "destination": "20,6"})
    assert alone.error == ("H1-212 can't work unsupervised: no operator holds a valid "
                           "'humanoid_supervision' credential")
    jordan = twin.add_operator(name="Jordan", worker_id="E-10006")
    people.place(twin, jordan, "returns_qc")
    supervised = twin.tasks.create_task({"type": "MOVE_ROBOT", "robot_id": humanoid.id, "destination": "20,6"})
    assert supervised.status is TaskStatus.PLANNING, supervised.error


def test_an_auto_job_no_body_can_do_is_rejected_with_the_reasons(twin):
    twin.add_robot(name="TR50-201", asset_id="AST-000201", position=(5, 6))
    twin.add_robot(name="CX10-210", asset_id="AST-000210")
    twin.add_box(name="PAL-1", kind="PALLET", sku="SKU-01", quantity=40, weight=600.0, position=(5, 5))
    task = twin.tasks.create_task({"type": "PICK_AND_DELIVER", "box_id": "PAL-1", "destination": "outbound_staging"})
    assert task.status is TaskStatus.FAILED
    assert task.error == ("No robot can do this PICK_AND_DELIVER: TR50-201 can't take PAL-1: cannot handle a "
                          "PALLET (handles: TOTE, ITEM); CX10-210 is fixed equipment and can't do PICK_AND_DELIVER")
    drone_only = twin.tasks.create_task({"type": "MOVE_ROBOT", "destination": "drone_pad"})
    assert drone_only.status is TaskStatus.FAILED and "TR50-201 has no route to the job" in drone_only.error


def test_an_arm_is_never_routed(twin):
    arm = twin.add_robot(name="CX10-210", asset_id="AST-000210")
    failures = twin.navigation.failures
    assert twin.navigation.find_path(arm.position, arm.position, profile=arm.mobility) is None
    assert twin.navigation.failures == failures                # an arm not moving is no routing failure


def test_rules_rechecked_mid_job_flag_but_never_halt(twin, sim):
    forklift = twin.add_robot(name="PF1200-205", asset_id="AST-000205", position=(7, 3))
    task = twin.tasks.create_task({"type": "MOVE_ROBOT", "robot_id": forklift.id, "destination": "7,9"})
    pallet = twin.add_box(name="PAL-9", kind="PALLET", sku="SKU-01", quantity=40, weight=1300.0, position=(7, 3))
    pallet.set_status(BoxStatus.CARRIED)
    for _ in range(10):
        sim.tick()
    forklift.carrying_box = pallet.id                           # an overweight load turns up on its forks
    pallet.position = forklift.position
    for _ in range(10):
        sim.tick()
    assert task.authorization_flagged == "is carrying PAL-9: a 1300 kg load is over its 1200 kg payload limit"
    flagged = twin.events.query(task_id=task.id, event_type="TASK_AUTHORIZATION_CHANGED")
    assert len(flagged) == 1 and not task.is_terminal
    humanoid = twin.add_robot(name="H1-212", asset_id="AST-000212", position=(22, 7))
    assert twin.tasks.physical_recheck(humanoid, Task("task_999", task.type)) == \
        "can't work unsupervised: no operator holds a valid 'humanoid_supervision' credential"


def test_the_new_floor_offers_only_real_destinations(twin, tmp_path):
    keys = {option["key"] for option in twin.warehouse.location_options()}
    assert {"patrol_loop", "walkway", "conveyor", "sorter", "drone_pad", "pallet_racks",
            "tote_shelves", "restricted_area"}.isdisjoint(keys)
    assert {"dock_1", "intake_staging", "tote_aisle_1", "outbound_staging", "charging_station"} <= keys
    classic = make_twin(tmp_path / "classic", layout="classic")
    assert len(classic.warehouse.location_options()) == len(classic.warehouse.zones) - 1  # all but restricted


def test_a_catalog_model_is_read_once(twin, monkeypatch):
    calls = []
    real = twin.inventory.get_model
    monkeypatch.setattr(twin.inventory, "get_model", lambda code: calls.append(code) or real(code))
    first = twin.fleet.model_profile("NW-HH300")
    assert twin.fleet.model_profile("NW-HH300") is first and isinstance(first, MobilityProfile)
    assert calls == ["NW-HH300"]


def test_job_parameters_ride_on_the_task(twin):
    twin.add_robot(name="TR50-201", asset_id="AST-000201", position=(5, 6))
    task = twin.tasks.create_task({"type": "MOVE_ROBOT", "destination": "parking_area", "slot": "TS-10-12-1",
                                   "quantity": 3, "order_id": "ORD-7", "bogus": 1})
    assert task.params == {"slot": "TS-10-12-1", "quantity": 3, "order_id": "ORD-7"}
    assert Task.from_dict(task.to_dict()).params == task.params
    created = twin.events.query(task_id=task.id, event_type="TASK_CREATED")[0]["data"]
    assert created["params"] == task.params
    plain = twin.tasks.create_task({"type": "MOVE_ROBOT", "destination": "parking_area"})
    assert "params" not in twin.events.query(task_id=plain.id, event_type="TASK_CREATED")[0]["data"]
    assert set(JOB_PARAM_KEYS) >= {"slot", "station", "face", "segment"}
    assert parse_cell("21,15") == (21, 15) and parse_cell([3, 4]) == (3, 4) and parse_cell("x") is None
    assert all(f"{kind.value}:" in TASK_TYPE_GUIDE for kind in JOB_SPECS)
