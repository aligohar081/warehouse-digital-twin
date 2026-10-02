"""Drone counts and scout patrols (multi-embodiment spec §7.2, §9.2, §10.1):
a drone counts a rack face level by level and flags variances, a small one
reconciled on the spot; a flight needs the energy for the round trip and a
reserve; a scout patrols the loop and reports what it found."""
import pytest

from backend import energy
from backend.digital_twin import DigitalTwin
from backend.embodiment import AIR, GROUND, HOVER_CLEARANCE_M
from backend.models import CONFIG, BoxStatus, CellType, RobotStatus, SimulationStatus, TaskStatus, TaskType
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


def run(sim, task, max_ticks=2000):
    for _ in range(max_ticks):
        if task.is_terminal:
            return
        sim.tick()
    assert task.is_terminal, f"{task.id} still {task.status.value}"


def stock_face(twin):
    for level, quantity in ((0, 40), (1, 30), (2, 20)):
        twin.add_box(name=f"PAL-{level}", kind="PALLET", sku=f"SKU-0{level}", quantity=quantity, weight=500.0,
                     slot=f"PR-12-02-{level}")
    twin.stock.adjust_true("PR-12-02-1", -2)     # two cases short: within the auto-reconcile margin
    twin.stock.adjust_true("PR-12-02-2", -5)     # five short: an exception


def test_a_drone_counts_a_rack_face_and_flags_variances(twin, sim, drone):
    stock_face(twin)
    task = twin.tasks.create_task({"type": "CYCLE_COUNT", "face": "12,2"})
    assert task.status is TaskStatus.PLANNING, task.error
    altitudes = {}

    def watch():
        if drone.activity == "SCAN":
            altitudes[drone.position] = altitudes.get(drone.position, set()) | {drone.altitude_m}

    for _ in range(1500):
        if task.is_terminal:
            break
        sim.tick()
        watch()
    assert task.status is TaskStatus.COMPLETED, task.error
    assert (drone.layer, drone.position) == ("GROUND", (20, 2))
    assert altitudes == {(12, 3): {level + HOVER_CLEARANCE_M for level in range(5)}}
    steps = [e["data"]["step"] for e in twin.events.query(task_id=task.id, event_type="ROBOT_STEP")]
    assert steps == ["TAKEOFF", "NAVIGATE"] + ["SCAN"] * 5 + ["NAVIGATE", "LAND"]
    scans = [e["data"] for e in twin.events.query(task_id=task.id, event_type="SCANNED")]
    assert [(s["level"], s["reported_qty"], s["true_qty"]) for s in scans] == \
        [(0, 40, 40), (1, 28, 28), (2, 15, 15), (3, 0, 0), (4, 0, 0)]
    variances = [e["data"] for e in twin.events.query(task_id=task.id, event_type="STOCK_VARIANCE_DETECTED")]
    assert [(v["slot"], v["variance"], v["auto_reconciled"]) for v in variances] == \
        [("PR-12-02-1", -2, True), ("PR-12-02-2", -5, False)]
    assert twin.stock.location("PR-12-02-1").recorded_qty == 28 == twin.find_box("PAL-1").quantity
    assert twin.stock.location("PR-12-02-2").recorded_qty == 20   # left for review
    assert drone.battery < 100.0


def test_a_miscount_is_reported_as_counted(twin, sim, drone):
    stock_face(twin)
    twin.faults.arm("scan_miscount")
    task = twin.tasks.create_task({"type": "CYCLE_COUNT", "face": "12,2"})
    run(sim, task)
    assert task.status is TaskStatus.COMPLETED                     # the drone believes its count
    first = twin.events.query(task_id=task.id, event_type="SCANNED")[0]["data"]
    assert first["reported_qty"] != first["true_qty"] == 40
    assert 1 <= abs(first["reported_qty"] - 40) <= 3


def test_a_drone_flies_only_with_the_energy_for_the_round_trip(twin, sim, drone):
    other = twin.add_robot(name="IX2-209", asset_id="AST-000209", position=(21, 2))
    drone.battery = 40.0
    named = twin.tasks.create_task({"type": "CYCLE_COUNT", "face": "12,2", "robot_id": drone.id})
    assert named.status is TaskStatus.FAILED and named.error.startswith("IX2-208 can't fly it: has 60.0 Wh")
    assert "plus a 25% reserve" in named.error
    auto = twin.tasks.create_task({"type": "CYCLE_COUNT", "face": "12,2"})
    scored, _ = twin.tasks._score_candidates(auto)
    assert [entry[1] for entry in scored] == [other]
    other.battery = 40.0
    twin.tasks.cancel_task(auto.id)
    grounded = twin.tasks.create_task({"type": "CYCLE_COUNT", "face": "12,2"})
    assert grounded.status is TaskStatus.FAILED and grounded.error.startswith("No robot can do this CYCLE_COUNT")
    flight = energy.flight_wh_per_tick(drone.mobility)
    assert 0.17 < flight < 0.171                                    # 150 Wh over 22 min, x 10, a 0.15 s tick


def test_count_requests_are_checked(twin, drone):
    amr = twin.add_robot(name="TR50-201", asset_id="AST-000201", position=(8, 13))
    assert twin.tasks.create_task({"type": "CYCLE_COUNT", "face": "10,3"}).error == "(10,3) is not a pallet rack face"
    assert twin.tasks.create_task({"type": "CYCLE_COUNT"}).error == \
        "CYCLE_COUNT needs a face (a pallet rack cell, as x,y)"
    by_amr = twin.tasks.create_task({"type": "CYCLE_COUNT", "face": "12,2", "robot_id": amr.id})
    assert by_amr.error == "TR50-201 is an AMR; CYCLE_COUNT needs a DRONE"


def test_a_scout_patrols_the_loop_and_reports_what_it_finds(twin, sim):
    scout = twin.add_robot(name="SC1-204", asset_id="AST-000204", position=(7, 5))
    broken = twin.add_robot(name="TR50-201", asset_id="AST-000201", position=(10, 11))
    broken.set_status(RobotStatus.ERROR)
    dropped = twin.add_box(name="ITEM-9", kind="ITEM", weight=0.4, position=(12, 1))
    dropped.set_status(BoxStatus.FAILED)
    task = twin.tasks.create_task({"type": "PATROL"})
    run(sim, task)
    assert task.status is TaskStatus.COMPLETED and task.robot_id == scout.id, task.error
    corners = [a.target for a in task.actions if a.type.value == "NAVIGATE"]
    assert corners == [(7, 1), (18, 1), (18, 11), (7, 11), (7, 1)]
    assert task.params["anomalies"] == ["TR50-201 is ERROR at (10,11)", "ITEM-9 is FAILED at (12,1)"]
    report = [r for r in twin.logger.query(task_id=task.id) if "patrol report" in r["message"]]
    assert len(report) == 1 and report[0]["level"] == "WARNING"


def test_a_drone_left_in_the_air_flies_home_and_lands(twin, sim, drone):
    stock_face(twin)
    task = twin.tasks.create_task({"type": "CYCLE_COUNT", "face": "12,2"})
    assert task.status is TaskStatus.PLANNING, task.error
    for _ in range(1500):
        if drone.layer == AIR and drone.position != (20, 2):        # airborne and away from its pad
            break
        sim.tick()
    assert drone.layer == AIR and drone.position != (20, 2), "the drone never got away from its pad"
    twin.tasks.cancel_task(task.id)
    assert task.status is TaskStatus.CANCELLED
    for _ in range(600):
        sim.tick()
        if drone.layer == GROUND:
            break
    homing = [t for t in twin.tasks.tasks.values() if t.type is TaskType.CHARGE_ROBOT and t.robot_id == drone.id]
    assert len(homing) == 1, "the drone was never given a CHARGE_ROBOT task"
    assert drone.layer == GROUND
    assert twin.warehouse.cell_type(*drone.position) is CellType.DRONE_PAD
    assert drone.battery > CONFIG["BATTERY_LOW"]                     # it came home idle, not because it was low
    assert any("idle in the air" in r["message"] for r in twin.logger.query(robot_id=drone.id))
