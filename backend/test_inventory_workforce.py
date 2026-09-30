"""Workforce: privacy allowlist, credentials, training, validity."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from backend.inventory import Conflict, NotFound, open_inventory

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, **kwargs):
        self.now += timedelta(**kwargs)


@pytest.fixture
def clock():
    return Clock(NOW)


@pytest.fixture
def inv(clock):
    return open_inventory(":memory:", demo=False, clock=clock)


def credential(inv, worker_id, credential_id):
    return next(c for c in inv.get_worker(worker_id)["credentials"] if c["credential_id"] == credential_id)


def test_register_worker_enforces_the_privacy_allowlist(inv):
    change = inv.register_worker("Sam", role_codes=["WAREHOUSE_OPERATOR"], site_codes=["WH-01"])
    assert change["aggregate_id"] == "E-10001" and change["action"] == "WORKER_REGISTERED"
    with pytest.raises(ValueError, match="salary"):
        inv.register_worker("Pat", salary=50000)
    with pytest.raises(ValueError, match="date_of_birth"):
        inv.register_worker("Pat", date_of_birth="1990-01-01")
    assert inv.store.count("worker") == 1
    columns = {row["name"] for row in inv.store.conn.execute("PRAGMA table_info(worker)")}
    assert not columns & {"salary", "compensation", "performance_rating", "health", "date_of_birth",
                          "gender", "ethnicity", "disciplinary"}
    with pytest.raises(ValueError):
        inv.register_worker("Pat", worker_type="ROBOT")
    with pytest.raises(ValueError):
        inv.register_worker(" ")
    with pytest.raises(ValueError, match="shift_start_hour"):
        inv.register_worker("Pat", shift_start_hour=9)  # shift windows stay on the twin's Operator
    with pytest.raises(Conflict):
        inv.register_worker("Sam again", worker_id="E-10001")


def test_credential_lifecycle_and_validity(inv, clock):
    worker = inv.register_worker("Kai", worker_type="CONTRACTOR", organization="Proseware Staffing")["aggregate_id"]
    issued = inv.issue_credential(worker, "forklift_operator", "Proseware Training",
                                  expires_at=NOW + timedelta(days=40), equipment_scope=["NW-PF1200"],
                                  credential_number="FL-99812")
    assert issued["action"] == "CREDENTIAL_ISSUED"
    cred_id = issued["subject_id"]
    cred = credential(inv, worker, cred_id)
    assert cred["validity"] == "VALID" and cred["equipment_scope"] == ["NW-PF1200"]
    assert cred["name"] == "Forklift Operator"
    assert cred["credential_number_hash"].startswith("sha256:")
    assert "FL-99812" not in json.dumps(inv.get_worker(worker))
    assert inv.valid_credential_codes(worker) == ["forklift_operator"]
    clock.advance(days=11)
    assert credential(inv, worker, cred_id)["validity"] == "EXPIRING_SOON"  # 29 days left
    inv.renew_credential(cred_id, expires_at=NOW + timedelta(days=400))
    assert credential(inv, worker, cred_id)["validity"] == "VALID"
    with pytest.raises(ValueError):
        inv.renew_credential(cred_id, expires_at=NOW)  # must extend
    inv.verify_credential(cred_id, "issuer_verified")
    assert credential(inv, worker, cred_id)["verification_status"] == "ISSUER_VERIFIED"
    revoked = inv.revoke_credential(cred_id, "Failed practical assessment")
    assert revoked["action"] == "CREDENTIAL_REVOKED"
    assert credential(inv, worker, cred_id)["validity"] == "REVOKED"
    assert inv.valid_credential_codes(worker) == []
    with pytest.raises(Conflict):
        inv.revoke_credential(cred_id, "again")
    with pytest.raises(Conflict):
        inv.renew_credential(cred_id)
    with pytest.raises(Conflict):
        inv.verify_credential(cred_id, "SOURCE_VERIFIED")


def test_credential_defaults_and_validation(inv):
    worker = inv.register_worker("Lee")["aggregate_id"]
    cred_id = inv.issue_credential(worker, "safety_inspection")["subject_id"]
    cred = credential(inv, worker, cred_id)
    assert cred["effective_from"] == "2026-09-30T12:00:00Z"
    assert cred["expires_at"] == "2027-09-30T12:00:00Z"  # 365-day renewal period
    assert cred["verification_status"] == "SOURCE_VERIFIED" and cred["issuer"] == "Site Training Office"
    with pytest.raises(ValueError):
        inv.issue_credential(worker, "juggling")
    with pytest.raises(ValueError):
        inv.issue_credential(worker, "safety_inspection", verification_status="REVOKED")
    with pytest.raises(ValueError):
        inv.issue_credential(worker, "safety_inspection", effective_from="2026-10-01", expires_at="2026-09-01")
    future = inv.issue_credential(worker, "electrical_safety", effective_from=NOW + timedelta(days=3))["subject_id"]
    assert credential(inv, worker, future)["validity"] == "NOT_YET_EFFECTIVE"
    assert inv.valid_credential_codes(worker) == ["safety_inspection"]
    inv.ensure_credential_definition("crane_rigging")
    assert inv.ensure_credential_definition("crane_rigging")["name"] == "Crane Rigging"  # idempotent
    inv.issue_credential(worker, "crane_rigging")
    assert inv.valid_credential_codes(worker) == ["safety_inspection", "crane_rigging"]


def test_employment_status_gates_validity(inv):
    worker = inv.register_worker("Sasha")["aggregate_id"]
    cred_id = inv.issue_credential(worker, "forklift_operator")["subject_id"]
    change = inv.set_employment_status(worker, "on_leave", "Leave")
    assert change["action"] == "EMPLOYMENT_STATUS_CHANGED"
    assert credential(inv, worker, cred_id)["validity"] == "WORKER_INACTIVE"
    assert inv.valid_credential_codes(worker) == []
    with pytest.raises(Conflict):
        inv.set_employment_status(worker, "ON_LEAVE", "again")
    with pytest.raises(ValueError):
        inv.set_employment_status(worker, "RETIRED", "x")
    inv.set_employment_status(worker, "TERMINATED", "Left")
    with pytest.raises(Conflict):
        inv.issue_credential(worker, "safety_inspection")


def test_update_worker_and_supervision(inv):
    boss = inv.register_worker("Ana")["aggregate_id"]
    worker = inv.register_worker("Kai", supervisor_id=boss)["aggregate_id"]
    change = inv.update_worker(worker, "Moved to WH-02", site_codes=["WH-02"], supervisor_id=None)
    assert change["diff"]["site_codes"] == {"from": [], "to": ["WH-02"]}
    assert change["diff"]["supervisor_id"] == {"from": boss, "to": None}
    with pytest.raises(ValueError, match="employment_status"):
        inv.update_worker(worker, "x", employment_status="ACTIVE")
    with pytest.raises(ValueError):
        inv.update_worker(worker, "x", site_codes=["WH-02"])  # no change
    with pytest.raises(ValueError):
        inv.update_worker(worker, "x", supervisor_id=worker)
    with pytest.raises(NotFound):
        inv.update_worker(worker, "x", supervisor_id="E-404")
    with pytest.raises(NotFound):
        inv.register_worker("Orphan", supervisor_id="E-404")


def test_training_records_and_worker_reads(inv):
    worker = inv.register_worker("Noor", site_codes=["WH-01"])["aggregate_id"]
    change = inv.record_training(worker, "LOTO-101", "3", completed_at=NOW - timedelta(days=10),
                                 expires_at=NOW + timedelta(days=20))
    assert change["action"] == "TRAINING_RECORDED" and change["subject_id"].startswith("TRN-")
    record = inv.get_worker(worker)
    assert record["training"][0]["validity"] == "EXPIRING_SOON"
    with pytest.raises(ValueError):
        inv.record_training(worker, "")
    assert [w["worker_id"] for w in inv.list_workers(site="WH-01")] == [worker]
    assert inv.list_workers(site="WH-09") == []
    assert [w["worker_id"] for w in inv.list_workers(status="active")] == [worker]
    assert [c["action"] for c in inv.worker_history(worker)] == ["TRAINING_RECORDED", "WORKER_REGISTERED"]
    assert all(c["aggregate_type"] == "WORKER" for c in inv.changes("workforce")["items"])
    codes = {d["code"] for d in inv.list_credential_definitions()}
    assert {"forklift_operator", "humanoid_supervision", "robot_cell_access"} <= codes
    with pytest.raises(NotFound):
        inv.get_worker("E-404")


def test_fleet_actions_record_the_worker_who_did_them(inv):
    tech = inv.register_worker("Noor")["aggregate_id"]
    asset = inv.commission_robot("AC-TR50", "WH-01")["aggregate_id"]
    lidar = next(c for c in inv.get_robot(asset)["components"] if c["slot"] == "lidar")
    change = inv.record_calibration(lidar["component_id"], "PASS", performed_by=tech)
    assert change["actor"] == {"type": "WORKER", "id": tech}
    lidar = next(c for c in inv.get_robot(asset)["components"] if c["slot"] == "lidar")
    assert lidar["calibrations"][0]["performed_by"] == tech
    assert inv.open_work_order(asset, "CORRECTIVE", "x", technician_id=tech)["actor"]["id"] == tech
