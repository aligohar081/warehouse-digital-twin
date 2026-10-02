"""The multi-embodiment evaluation checks (spec §10.4) — graded against a pass
and a fail fixture each, against live runs whose faults they must catch, and
against classic logs they don't apply to — and the layout-driven system
checks (§10.5)."""
import glob
import os
import re

import pytest

from backend import people
from backend.ci_engine import CIEngine
from backend.digital_twin import DigitalTwin
from backend.eval_engine import DEFAULT_CHECKS, EMBODIMENT_CHECKS, Verdict, evaluate_events, evaluate_file
from backend.models import BoxStatus, RobotStatus, SimulationStatus, TaskStatus
from backend.simulator import Simulator

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES = os.path.join(BASE_DIR, "logs", "eval_examples", "multi_embodiment")
NAMES = ["payload_within_limit", "reach_within_limit", "clearance_respected", "no_fly_respected",
         "human_zone_clear", "supervision_maintained", "count_consistent", "handoff_consistent", "sort_correct",
         "placement_level_correct"]


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


def run(sim, task, max_ticks=3000):
    for _ in range(max_ticks):
        if task.is_terminal:
            return
        sim.tick()
    assert task.is_terminal, task.status


def grade(twin, task):
    report = evaluate_events(twin.events.query(task_id=task.id, limit=5000), task_id=task.id)
    return report, {check.name: check for check in report.checks}


# --------------------------------------------------------------------------- #
# The checks and their fixtures
# --------------------------------------------------------------------------- #
def test_the_new_checks_follow_the_old_ones():
    assert [check.__name__[len("check_"):] for check in EMBODIMENT_CHECKS] == NAMES
    assert DEFAULT_CHECKS[-len(NAMES):] == EMBODIMENT_CHECKS and len(DEFAULT_CHECKS) == 10 + len(NAMES)


def test_a_classic_log_gets_not_applicable_passes(tmp_path):
    classic = make_twin(tmp_path, layout="classic")
    simulator = Simulator(classic)
    classic.simulation_status = SimulationStatus.RUNNING
    task = classic.tasks.create_task({"type": "PICK_AND_DELIVER", "robot_id": "Robo-01", "box_id": "Box-A",
                                      "destination": "loading_zone"})
    run(simulator, task, max_ticks=800)
    report, checks = grade(classic, task)
    assert report.verdict is Verdict.PASS
    assert all(checks[name].verdict is Verdict.PASS and not checks[name].applicable for name in NAMES)
    assert checks["terminal_state"].to_dict()["applicable"] is True
    started = classic.events.query(task_id=task.id, event_type="TASK_STARTED")[0]["data"]["state"]
    assert "layer" not in started["robot"] and "kind" not in started["box"]   # classic snapshots unchanged


@pytest.mark.parametrize("path", sorted(glob.glob(os.path.join(FIXTURES, "task_*.json"))),
                         ids=lambda path: os.path.basename(path))
def test_each_fixture_grades_as_its_name_says(path):
    name, kind = re.match(r"task_\d+_(\w+)_(pass|fail)\.json", os.path.basename(path)).groups()
    report = evaluate_file(path)
    checks = {check.name: check for check in report.checks}
    assert checks[name].applicable
    if kind == "pass":
        assert report.verdict is Verdict.PASS, report.reasons
    else:
        assert report.verdict is Verdict.FAIL
        assert [c.name for c in report.checks if c.verdict is Verdict.FAIL] == [name]


def test_every_new_check_has_a_pass_and_a_fail_fixture():
    on_disk = {os.path.basename(path) for path in glob.glob(os.path.join(FIXTURES, "task_*.json"))}
    covered = {re.match(r"task_\d+_(\w+)_(pass|fail)\.json", name).groups() for name in on_disk}
    assert covered == {(name, kind) for name in NAMES for kind in ("pass", "fail")}


# --------------------------------------------------------------------------- #
# Live runs: the checks catch what the faults do
# --------------------------------------------------------------------------- #
def test_a_misdeclared_pallet_fails_payload_within_limit(twin, sim):
    twin.add_robot(name="PF1200-205", asset_id="AST-000205", position=(7, 4))
    heavy = twin.add_box(name="PAL-H", kind="PALLET", sku="SKU-01", quantity=40, weight=1000.0,
                         true_weight_kg=1450.0, position=(5, 4))
    task = twin.tasks.create_task({"type": "PUTAWAY_PALLET", "box_id": heavy.id, "slot": "PR-08-02-1"})
    run(sim, task)
    report, checks = grade(twin, task)
    assert task.status is TaskStatus.COMPLETED                        # it lifted what its paperwork allowed
    assert checks["payload_within_limit"].verdict is Verdict.FAIL and report.verdict is Verdict.FAIL
    for name in ("reach_within_limit", "clearance_respected", "human_zone_clear"):
        assert checks[name].applicable and checks[name].verdict is Verdict.PASS, name
    started = twin.events.query(task_id=task.id, event_type="TASK_STARTED")[0]["data"]["state"]
    assert (started["robot"]["embodiment_class"], started["robot"]["max_payload_kg"]) == ("FORKLIFT", 1200.0)
    assert (started["box"]["kind"], started["box"]["true_weight_kg"]) == ("PALLET", 1450.0)


def test_a_wrong_level_placement_fails_placement_level_correct(twin, sim):
    twin.add_robot(name="PF1200-205", asset_id="AST-000205", position=(7, 4))
    pallet = twin.add_box(name="PAL-1", kind="PALLET", sku="SKU-01", quantity=40, weight=600.0, position=(5, 4))
    twin.faults.arm("wrong_level")
    task = twin.tasks.create_task({"type": "PUTAWAY_PALLET", "box_id": pallet.id, "slot": "PR-08-02-2"})
    run(sim, task)
    assert task.status is TaskStatus.COMPLETED
    placed = twin.events.query(task_id=task.id, event_type="PLACED")[0]["data"]
    assert (placed["level"], placed["true_level"]) == (2, 3)          # the setup really mis-levels the pallet
    report, checks = grade(twin, task)
    assert checks["placement_level_correct"].applicable and checks["placement_level_correct"].verdict is Verdict.FAIL
    assert checks["placement_level_correct"].message == (
        f"robot {task.robot_id} placed {pallet.id} at level 3, not the requested level 2 (PR-08-02-2)")
    assert report.verdict is Verdict.FAIL
    for name in ("reach_within_limit", "payload_within_limit"):          # level 3 is in the forklift's reach
        assert checks[name].applicable and checks[name].verdict is Verdict.PASS, name


def test_a_miscount_fails_count_consistent(twin, sim):
    twin.add_robot(name="IX2-208", asset_id="AST-000208", position=(20, 2))
    twin.add_box(name="PAL-0", kind="PALLET", sku="SKU-01", quantity=40, weight=500.0, slot="PR-12-02-0")
    twin.faults.arm("scan_miscount")
    task = twin.tasks.create_task({"type": "CYCLE_COUNT", "face": "12,2"})
    run(sim, task)
    _, checks = grade(twin, task)
    assert checks["count_consistent"].verdict is Verdict.FAIL
    assert checks["no_fly_respected"].applicable and checks["no_fly_respected"].verdict is Verdict.PASS


def test_a_lost_item_fails_handoff_consistent_and_a_mis_sort_fails_sort_correct(twin, sim):
    twin.add_robot(name="PK30-203", asset_id="AST-000203", position=(20, 14))
    twin.add_robot(name="CX10-210", asset_id="AST-000210")
    tote = twin.add_box(name="TOTE-1", kind="TOTE", sku="SKU-02", quantity=6, weight=7.5, slot="TS-10-12-1")
    tote.position, tote.status = (19, 14), BoxStatus.DELIVERED
    twin.faults.arm("handoff_loss")
    lost = twin.tasks.create_task({"type": "PICK_ITEMS", "box_id": tote.id, "quantity": 1})
    run(sim, lost)
    assert grade(twin, lost)[1]["handoff_consistent"].verdict is Verdict.FAIL
    pick = twin.tasks.create_task({"type": "PICK_ITEMS", "box_id": tote.id, "quantity": 1, "order_id": "ORD-9",
                                   "pack_cell": "pack_cell_1"})
    pack = twin.tasks.create_task({"type": "PACK_ORDER", "order_id": "ORD-9", "quantity": 1, "lane": "dock_4"})
    twin.faults.arm("mis_sort")
    run(sim, pick)
    run(sim, pack)
    for _ in range(80):
        sim.tick()
    _, checks = grade(twin, pack)
    assert checks["sort_correct"].verdict is Verdict.FAIL
    assert checks["human_zone_clear"].applicable and checks["human_zone_clear"].verdict is Verdict.PASS


def test_a_supervised_job_passes_supervision_maintained(twin, sim):
    humanoid = twin.add_robot(name="H1-212", asset_id="AST-000212", position=(22, 7))
    jordan = twin.add_operator(name="Jordan", worker_id="E-10006")
    people.place(twin, jordan, "returns_qc")
    task = twin.tasks.create_task({"type": "MOVE_ROBOT", "robot_id": humanoid.id, "destination": "20,6"})
    run(sim, task)
    _, checks = grade(twin, task)
    assert checks["supervision_maintained"].applicable and checks["supervision_maintained"].verdict is Verdict.PASS


# --------------------------------------------------------------------------- #
# System checks (spec §10.5)
# --------------------------------------------------------------------------- #
def results(twin):
    return {check["name"]: check for check in CIEngine(twin).run()["checks"]}


def test_the_new_floor_passes_its_system_checks(twin, sim):
    twin.add_robot(name="CX10-210", asset_id="AST-000210")
    drone = twin.add_robot(name="IX2-208", asset_id="AST-000208", position=(20, 2))
    twin.set_robot_layer(drone.id, "AIR", altitude_m=2.0)
    drone.position = (12, 2)                                          # over a rack
    other = twin.add_robot(name="IX2-209", asset_id="AST-000209", position=(21, 2))
    other.battery, other.status = 50.0, RobotStatus.CHARGING          # charging on its pad
    twin.add_robot(name="PF1200-205", asset_id="AST-000205", position=(7, 4))
    noor = twin.add_operator(name="Noor", worker_id="E-10003")
    people.place(twin, noor, "workshop")
    twin.equipment.jam((20, 15))
    twin.tasks.create_task({"type": "CLEAR_JAM", "segment": "20,15"})   # a person's job: active with no robot
    checks = results(twin)
    assert all(check["passed"] for check in checks.values()), {k: v["message"] for k, v in checks.items()
                                                               if not v["passed"]}
    assert checks["Environment validation"]["message"].endswith("connected for narrow, wide and air bodies")


def test_misplaced_robots_fail_the_position_check(twin):
    arm = twin.add_robot(name="CX10-210", asset_id="AST-000210")
    forklift = twin.add_robot(name="PF1200-205", asset_id="AST-000205", position=(7, 4))
    drone = twin.add_robot(name="IX2-208", asset_id="AST-000208", position=(20, 2))
    twin.set_robot_layer(drone.id, "AIR")
    arm.position, forklift.position, drone.position = (22, 13), (10, 13), (2, 3)
    message = results(twin)["Robot position validation"]["message"]
    assert "CX10-210 stands on a STATION cell at (22,13)" in message
    assert "PF1200-205 stands on a EMPTY cell at (10,13)" in message           # a narrow tote aisle
    assert "IX2-208 stands on a DOCK cell at (2,3) on the AIR layer" in message  # a no-fly dock


def test_battery_and_required_zone_checks_read_the_floor(twin):
    arm = twin.add_robot(name="CX10-210", asset_id="AST-000210")
    arm.battery = 0.0                                                   # mains-powered: never flat
    assert results(twin)["Battery validation"]["passed"]
    amr = twin.add_robot(name="TR50-201", asset_id="AST-000201", position=(8, 13))
    amr.status = RobotStatus.CHARGING
    assert not results(twin)["Battery validation"]["passed"]           # no charger in a tote aisle
    del twin.warehouse.zones["sorter"]
    assert "missing zones: sorter" in results(twin)["Environment validation"]["message"]


def test_a_shipped_box_does_not_occupy_its_zone_in_snapshots(twin):
    pallet = twin.add_box(name="PAL-OUT", kind="PALLET", sku="SKU-01", quantity=40, weight=600.0, position=(1, 3))
    pallet.set_status(BoxStatus.DELIVERED)
    before = twin.tasks._zone_snapshot("dock_1")
    assert before["occupied"] and pallet.id in before["boxes_present"]   # waiting on the dock, it occupies it
    pallet.set_status(BoxStatus.SHIPPED)                                 # the truck has left with it
    after = twin.tasks._zone_snapshot("dock_1")
    assert pallet.id not in after["boxes_present"] and not after["occupied"]
