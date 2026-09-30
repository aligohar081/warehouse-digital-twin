"""FleetBridge: binding live robots/operators to the inventory, and the
runtime effects of inventory actions on the simulation."""
import json

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


def test_shift_hours_never_reach_the_workforce_source_system(twin):
    operator = twin.add_operator(name="Shifty", certifications=["safety_inspection"],
                                 shift_start_hour=6, shift_end_hour=14)
    assert (operator.shift_start_hour, operator.shift_end_hour) == (6, 14)  # the twin keeps them
    assert "shift" not in json.dumps(twin.inventory.changes("workforce", limit=500))
    assert not [key for key in twin.inventory.get_worker(operator.worker_id) if "shift" in key]


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


def test_boot_survives_a_decommissioned_demo_asset(tmp_path):
    path = str(tmp_path / "inventory.sqlite3")
    first = make_twin(tmp_path, inventory_path=path)
    first.fleet.mutate(first.inventory.decommission, "AST-000101", "scrapped")
    second = make_twin(tmp_path, inventory_path=path)  # must not raise
    robo1 = second.find_robot("Robo-01")
    assert robo1 is not None and robo1.asset_id and robo1.asset_id != "AST-000101"
    assert second.inventory.get_robot(robo1.asset_id)["lifecycle_status"] == "IN_SERVICE"
    assert second.find_robot("Robo-02").asset_id == "AST-000102"  # the other demo robot is untouched
    warnings = [r for r in second.logger.records
                if r["category"] == "FLEET" and r["level"] == "WARNING" and "AST-000101" in r["message"]]
    assert len(warnings) == 1 and "decommissioned" in warnings[0]["message"]


def test_load_state_survives_a_decommissioned_asset(twin, tmp_path):
    path = twin.save_state(str(tmp_path / "state.json"))
    twin.fleet.mutate(twin.inventory.decommission, "AST-000101", "scrapped")
    twin.load_state(path)  # must not raise or leave the twin half-loaded
    robo1 = twin.find_robot("Robo-01")
    assert robo1.asset_id and robo1.asset_id != "AST-000101"
    assert twin.inventory.get_robot(robo1.asset_id)["lifecycle_status"] == "IN_SERVICE"
    assert twin.find_robot("Robo-02").asset_id == "AST-000102"
    assert twin.find_operator("Sam").worker_id == "E-10001"
    assert any(r["category"] == "FLEET" and r["level"] == "WARNING" and "AST-000101" in r["message"]
               for r in twin.logger.records)


# --------------------------------------------------------------------------- #
# Runtime effects (Simulator.tick → FleetBridge.on_tick)
# --------------------------------------------------------------------------- #
import threading
import time
from datetime import datetime, timedelta, timezone

from backend.models import CONFIG, SimulationStatus
from backend.simulator import Simulator


@pytest.fixture
def sim(twin):
    simulator = Simulator(twin)
    twin.simulation_status = SimulationStatus.RUNNING
    return simulator


def ticks(sim, count):
    for _ in range(count):
        sim.tick()


def run_until(sim, predicate, max_ticks=400):
    for _ in range(max_ticks):
        if predicate():
            return
        sim.tick()
    raise AssertionError("condition never became true")


def job_state(twin, job_id):
    return twin.inventory.get_ota_job(job_id)["state"]


def observations(twin, asset_id):
    return [c for c in twin.inventory.robot_history(asset_id) if c["aggregate_type"] == "ROBOT_OBSERVATION"]


def test_heartbeat_reports_runtime_truth_only_when_it_changes(twin, sim):
    every = CONFIG["FLEET_HEARTBEAT_EVERY_TICKS"]
    ticks(sim, every * 3)
    quiet = len(observations(twin, "AST-000101"))
    ticks(sim, every * 3)
    assert len(observations(twin, "AST-000101")) == quiet  # nothing changed, nothing logged
    twin.find_robot("Robo-01").firmware_version = "0.9.0-beta"
    ticks(sim, every)
    record = twin.inventory.get_robot("AST-000101")
    assert record["reported"]["software_version"] == "0.9.0-beta"
    assert record["flags"]["software_mismatch"] and record["flags"]["running_unknown_software"]
    assert record["reported"]["operational_mode"] == "IDLE"
    assert not twin.inventory.get_robot("AST-000201")["flags"]["report_stale"]  # remote sites check in too


def test_ota_runs_through_the_simulator_and_waits_for_an_idle_robot(twin, sim):
    job = twin.fleet.mutate(twin.inventory.start_ota, "AST-000101", "AC-TR50:SW:2.2.0")["subject_id"]
    robot = twin.find_robot("Robo-01")
    twin.tasks.create_task({"type": "MOVE_ROBOT", "robot_id": "Robo-01", "destination": "loading_zone"})
    ticks(sim, CONFIG["OTA_DOWNLOAD_TICKS"] + 3)
    assert robot.current_task is not None and job_state(twin, job) == "DOWNLOADING"  # waits while busy
    run_until(sim, lambda: job_state(twin, job) == "INSTALLING")
    assert robot.current_task is None and robot.ota_installing and not robot.is_available
    assert twin.inventory.get_robot("AST-000101")["reported"]["operational_mode"] == "UPDATING"
    run_until(sim, lambda: job_state(twin, job) == "REPORTED")
    assert robot.firmware_version == "2.2.0" and not robot.ota_installing and robot.is_available
    record = twin.inventory.get_robot("AST-000101")
    assert record["reported"]["software_version"] == "2.2.0" and record["declared_software_version"] == "2.1.0"
    assert record["reported"]["operational_mode"] == "IDLE"  # not stuck on UPDATING until the next heartbeat
    twin.fleet.mutate(twin.inventory.verify_ota, job, verified_by="E-10003")
    assert twin.inventory.get_robot("AST-000101")["flags"]["software_mismatch"] is False
    assert any(e["event"] == "OTA_JOB_UPDATED" for e in twin.events.query(limit=500))


def test_ota_failure_knob(twin, sim, monkeypatch):
    monkeypatch.setitem(CONFIG, "OTA_FAILURE_RISK", 1.0)
    job = twin.fleet.mutate(twin.inventory.start_ota, "AST-000102", "AC-TR50:SW:2.2.0")["subject_id"]
    run_until(sim, lambda: job_state(twin, job) == "FAILED")
    robot = twin.find_robot("Robo-02")
    assert robot.firmware_version == "2.1.0" and not robot.ota_installing
    assert any(e["event"] == "OTA_JOB_UPDATED" and e["level"] == "WARNING" for e in twin.events.query(limit=500))


def test_rollback_restores_the_previous_running_version(twin, sim):
    job = twin.fleet.mutate(twin.inventory.start_ota, "AST-000101", "AC-TR50:SW:2.2.0")["subject_id"]
    run_until(sim, lambda: job_state(twin, job) == "REPORTED")
    twin.fleet.mutate(twin.inventory.rollback_ota, job, "Localisation regression")
    assert twin.find_robot("Robo-01").firmware_version == "2.1.0"
    assert twin.inventory.get_robot("AST-000101")["reported"]["software_version"] == "2.1.0"


def test_off_floor_assets_update_without_a_robot(twin, sim):
    job = twin.fleet.mutate(twin.inventory.start_ota, "AST-000201", "AC-TR50:SW:2.1.1")["subject_id"]
    run_until(sim, lambda: job_state(twin, job) == "REPORTED")
    assert twin.inventory.get_robot("AST-000201")["reported"]["software_version"] == "2.1.1"


def test_lifecycle_changes_stop_and_resume_the_floor_robot(twin):
    robot = twin.find_robot("Robo-01")
    wo = twin.fleet.mutate(twin.inventory.open_work_order, "AST-000101", "CORRECTIVE", "Bumper cracked")["subject_id"]
    assert robot.status == RobotStatus.STOPPED and robot.fleet_hold == "MAINTENANCE"
    twin.fleet.mutate(twin.inventory.swap_component, wo, "lidar", performed_by="E-10003", hw_revision="A")
    assert robot.component_firmware["lidar"] == "1.9.4"  # a rev-A lidar can't run 2.0.1
    assert twin.inventory.get_robot("AST-000101")["reported"]["component_firmware"]["lidar"] == "1.9.4"
    twin.fleet.mutate(twin.inventory.close_work_order, wo, "Bumper and lidar replaced")
    assert robot.status == RobotStatus.IDLE and robot.fleet_hold is None


def test_a_user_stop_is_not_undone_by_the_fleet(twin):
    robot = twin.find_robot("Robo-02")
    twin.stop_robot(robot.id, reason="user")
    wo = twin.fleet.mutate(twin.inventory.open_work_order, "AST-000102", "CORRECTIVE", "x")["subject_id"]
    twin.fleet.mutate(twin.inventory.close_work_order, wo, "done")
    assert robot.status == RobotStatus.STOPPED


def test_revoking_a_credential_removes_it_from_the_operator(twin):
    sam = twin.find_operator("Sam")
    credential = next(c for c in twin.inventory.get_worker("E-10001")["credentials"] if c["code"] == "electrical_safety")
    twin.fleet.mutate(twin.inventory.revoke_credential, credential["credential_id"], "Audit finding")
    assert sam.certifications == ["safety_inspection"]
    changed = [e for e in twin.events.query(limit=200) if e["event"] == "OPERATOR_CERTIFICATIONS_CHANGED"]
    assert changed and changed[-1]["level"] == "WARNING"


def test_credential_expiry_is_picked_up_on_the_shift_check_cadence(twin, sim):
    with twin.inventory.at(datetime.now(timezone.utc) + timedelta(days=400)):
        ticks(sim, CONFIG["SHIFT_CHECK_EVERY_TICKS"])
    assert twin.find_operator("Lee").certifications == []


def test_mutations_racing_the_simulator_thread_neither_deadlock_nor_break_ticks(twin, monkeypatch):
    """API-style mutations (twin.lock → inventory lock) run on a worker thread while the
    simulator thread ticks with a heartbeat and an operator sync on every tick."""
    monkeypatch.setitem(CONFIG, "FLEET_HEARTBEAT_EVERY_TICKS", 1)
    monkeypatch.setitem(CONFIG, "SHIFT_CHECK_EVERY_TICKS", 1)
    heartbeats = []
    real_heartbeat = twin.fleet.heartbeat
    monkeypatch.setattr(twin.fleet, "heartbeat", lambda: (heartbeats.append(1), real_heartbeat())[1])
    simulator = Simulator(twin)
    simulator.dt = 0.002
    errors = []
    job_ids = []

    def hammer():
        try:
            target = twin.tick_count + 30
            job_ids.append(twin.fleet.mutate(twin.inventory.start_ota, "AST-000102",
                                             "AC-TR50:SW:2.2.0")["subject_id"])
            i = 0
            while twin.tick_count < target:
                twin.fleet.mutate(twin.inventory.admin_edit, "AST-000201", {"fleet_id": f"F-{i}"}, "stress")
                twin.fleet.mutate(twin.inventory.report_state, "AST-000201",
                                  {"health_state": "DEGRADED" if i % 2 else "OK"})
                i += 1
        except Exception as exc:  # noqa: BLE001 - surfaced by the assertion below
            errors.append(exc)

    simulator.start()
    simulator.start_thread()
    try:
        started_at = twin.tick_count
        worker = threading.Thread(target=hammer, daemon=True)
        worker.start()
        worker.join(timeout=10)
        assert not worker.is_alive(), "mutations and ticks deadlocked"
        assert errors == []
        assert twin.tick_count - started_at >= 30
        assert len(heartbeats) >= 10  # ticks ran the fleet hooks while mutations were in flight
        assert job_state(twin, job_ids[0]) != "STAGED"
    finally:
        simulator.stop_thread()
    failures = [r for r in twin.logger.records if r["level"] in ("ERROR", "CRITICAL")]
    assert failures == []  # no tick (or fleet hook) failed under the race


def test_overlapping_ota_jobs_keep_the_robot_updating_until_both_finish(twin, sim):
    robot = twin.find_robot("Robo-01")
    software = twin.fleet.mutate(twin.inventory.start_ota, "AST-000101", "AC-TR50:SW:2.2.0")["subject_id"]
    ticks(sim, 3)
    lidar = next(c for c in twin.inventory.get_robot("AST-000101")["components"] if c["slot"] == "lidar")
    firmware = twin.fleet.mutate(twin.inventory.start_ota, "AST-000101", "WT-L360:FW:1.9.4",
                                 component_id=lidar["component_id"])["subject_id"]
    run_until(sim, lambda: job_state(twin, software) == "REPORTED")
    assert job_state(twin, firmware) == "INSTALLING"
    assert robot.ota_installing and not robot.is_available
    run_until(sim, lambda: job_state(twin, firmware) == "REPORTED")
    assert not robot.ota_installing and robot.is_available


def test_a_swap_during_install_clears_updating(twin, sim):
    robot = twin.find_robot("Robo-01")
    wo = twin.fleet.mutate(twin.inventory.open_work_order, "AST-000101", "CORRECTIVE", "Lidar drifting")["subject_id"]
    lidar = next(c for c in twin.inventory.get_robot("AST-000101")["components"] if c["slot"] == "lidar")
    job = twin.fleet.mutate(twin.inventory.start_ota, "AST-000101", "WT-L360:FW:1.9.4",
                            component_id=lidar["component_id"])["subject_id"]
    run_until(sim, lambda: job_state(twin, job) == "INSTALLING")
    assert robot.ota_installing
    twin.fleet.mutate(twin.inventory.swap_component, wo, "lidar")
    assert job_state(twin, job) == "FAILED" and not robot.ota_installing
