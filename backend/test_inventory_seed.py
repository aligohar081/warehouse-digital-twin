"""The demo inventory: every seeded scenario shows its flag; seeding is deterministic."""
import time
from datetime import datetime, timezone

import pytest

from backend.inventory import open_inventory

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)
CLEAN = ("AST-000101", "AST-000102", "AST-000201", "AST-000209", "AST-000210", "AST-000212")


@pytest.fixture(scope="module")
def demo():
    return open_inventory(":memory:", demo=True, clock=lambda: NOW)


def test_demo_fleet_matches_the_spec(demo):
    assert [r["asset_id"] for r in demo.list_robots(site="WH-01")] == ["AST-000101", "AST-000102", "AST-000103"]
    assert len(demo.list_robots(site="WH-02")) == 13
    floor = demo.get_robot("AST-000101")
    assert floor["reported"]["software_version"] == "2.1.0"
    assert floor["declared_software_version"] == "2.1.0"
    assert floor["reported"]["battery"]["cycle_count"] == 212


def test_each_seeded_scenario_shows_its_flag(demo):
    flags = {r["asset_id"]: r["flags"] for r in demo.list_robots()}
    assert demo.get_robot("AST-000103")["lifecycle_status"] == "MAINTENANCE"
    assert flags["AST-000103"]["calibration_worst"] == "MISSING"
    assert flags["AST-000202"]["software_mismatch"] and flags["AST-000202"]["running_recalled_release"]
    assert flags["AST-000203"]["calibration_worst"] == "EXPIRED"
    assert flags["AST-000204"]["calibration_worst"] == "DUE_SOON"
    assert demo.get_robot("AST-000205")["hw_revision"] == "A"
    assert flags["AST-000206"]["active_ota_job"]["state"] == "REPORTED"
    assert flags["AST-000206"]["software_mismatch"]
    assert flags["AST-000207"]["report_stale"]
    assert demo.get_robot("AST-000208")["reported"]["battery"]["soh_pct"] == 71.0
    assert [m["slot"] for m in flags["AST-000211"]["component_firmware_mismatches"]] == ["force_torque"]
    assert demo.get_robot("AST-000213")["lifecycle_status"] == "DECOMMISSIONED"
    for asset in CLEAN:
        f = flags[asset]
        assert not f["software_mismatch"], asset
        assert not f["component_firmware_mismatches"], asset
        assert f["calibration_worst"] == "VALID", asset
        assert not f["report_stale"], asset
        assert f["active_ota_job"] is None, asset


def test_demo_workforce(demo):
    assert demo.valid_credential_codes("E-10001") == ["safety_inspection", "electrical_safety"]
    assert demo.valid_credential_codes("E-10002") == ["safety_inspection"]
    kai = demo.get_worker("E-10007")
    assert kai["worker_type"] == "CONTRACTOR" and kai["supervisor_id"] == "E-10005"
    assert [c["validity"] for c in kai["credentials"]] == ["EXPIRING_SOON"]
    assert "REVOKED" in [c["validity"] for c in demo.get_worker("E-10008")["credentials"]]
    assert demo.get_worker("E-10009")["employment_status"] == "ON_LEAVE"
    assert demo.get_worker("E-10010")["employment_status"] == "TERMINATED"
    assert demo.get_worker("E-10003")["training"][0]["course_code"] == "LOTO-101"
    assert len(demo.list_workers()) == 10


def test_each_workers_seeded_history_is_chronological(demo):
    for worker in demo.list_workers():
        history = sorted(demo.worker_history(worker["worker_id"]), key=lambda change: change["revision"])
        stamps = [change["occurred_at"] for change in history]
        assert stamps == sorted(stamps), worker["worker_id"]  # revision order and occurred_at agree
    assert demo.get_worker("E-10003")["training"][0]["completed_at"].startswith("2026-07-02")  # 90 days back
    training = next(c for c in demo.worker_history("E-10003") if c["action"] == "TRAINING_RECORDED")
    assert training["occurred_at"].startswith("2026-07-02")


def test_seeded_history_is_backdated(demo):
    commissioned = demo.robot_history("AST-000213")[-1]
    assert commissioned["action"] == "COMMISSIONED"
    assert commissioned["occurred_at"].startswith("2024-10-")  # 700 days before NOW


def test_seed_is_deterministic():
    a = open_inventory(":memory:", demo=True, clock=lambda: NOW)
    b = open_inventory(":memory:", demo=True, clock=lambda: NOW)
    assert a.get_robot("AST-000206") == b.get_robot("AST-000206")
    assert a.get_worker("E-10007") == b.get_worker("E-10007")
    assert [c["action"] for c in a.changes("fleet", limit=500)["items"]] == \
        [c["action"] for c in b.changes("fleet", limit=500)["items"]]


def test_template_copies_are_fast_and_independent():
    open_inventory()  # warm the template
    started = time.perf_counter()
    a, b = open_inventory(), open_inventory()
    assert time.perf_counter() - started < 0.5
    assert a.store.epoch() != b.store.epoch()
    a.decommission("AST-000201", "test")
    assert b.get_robot("AST-000201")["lifecycle_status"] == "IN_SERVICE"
