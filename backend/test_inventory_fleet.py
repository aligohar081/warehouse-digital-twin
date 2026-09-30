"""Robot assets: commissioning, lifecycle, admin edits, reported state, flags."""
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


def commission(inv, model="AC-TR50", site="WH-01", zone="parking_area", **kwargs):
    return inv.commission_robot(model, site, zone, **kwargs)["aggregate_id"]


def test_commission_creates_asset_components_calibrations_and_first_report(inv):
    change = inv.commission_robot("AC-TR50", "WH-01", "parking_area")
    assert change["action"] == "COMMISSIONED" and change["revision"] == 1
    asset_id = change["aggregate_id"]
    assert asset_id == "AST-000001"
    assert change["diff"]["lifecycle_status"] == {"from": None, "to": "IN_SERVICE"}
    robot = inv.get_robot(asset_id)
    assert robot["lifecycle_status"] == "IN_SERVICE"
    assert robot["serial_number"] == "ACM-TR50-2026-01001"
    assert robot["manufacturer_name"] == "Acme Robotics"
    assert robot["declared_software_version"] == "2.2.0"
    layout = [s["slot"] for s in inv.get_model("AC-TR50")["component_layout"]]
    assert [c["slot"] for c in robot["components"]] == layout
    lidar = next(c for c in robot["components"] if c["slot"] == "lidar")
    assert lidar["calibration_status"] == "VALID" and lidar["declared_firmware_version"] == "2.0.1"
    assert lidar["calibrations"][0]["method"] == "FACTORY"
    assert robot["reported"]["software_version"] == "2.2.0"
    assert robot["reported"]["os_version"] == "linux-rt 6.6.21"
    assert robot["reported"]["component_firmware"]["lidar"] == "2.0.1"
    assert robot["reported"]["battery"] == {"soc_pct": 100.0, "soh_pct": 100.0, "cycle_count": 0}
    assert robot["flags"]["software_mismatch"] is False
    assert robot["flags"]["calibration_worst"] == "VALID"
    assert [c["action"] for c in inv.changes("fleet")["items"]] == ["COMMISSIONED", "STATE_REPORTED"]


def test_commission_validation(inv):
    with pytest.raises(NotFound):
        inv.commission_robot("NOPE", "WH-01")
    with pytest.raises(ValueError):
        inv.commission_robot("AC-TR50", "WH-01", hw_revision="Z")
    with pytest.raises(ValueError):
        inv.commission_robot("AC-TR50", "")
    with pytest.raises(ValueError):
        inv.commission_robot("AC-TR50", "WH-01", software_release_id="NW-PF1200:SW:2.2.0")
    with pytest.raises(Conflict):
        inv.commission_robot("AC-TR50", "WH-01", software_release_id="AC-TR50:SW:2.2.1")  # recalled
    with pytest.raises(Conflict):
        inv.commission_robot("NW-PF1200", "WH-02", hw_revision="A", software_release_id="NW-PF1200:SW:2.2.0")
    inv.commission_robot("AC-TR50", "WH-01", asset_id="AST-000101", serial_number="S-1")
    with pytest.raises(Conflict):
        inv.commission_robot("AC-TR50", "WH-01", asset_id="AST-000101")
    with pytest.raises(Conflict):
        inv.commission_robot("AC-TR50", "WH-01", serial_number="S-1")
    assert inv.store.count("robot_asset") == 1  # failed commissions left nothing behind


def test_an_older_release_can_be_commissioned_and_models_without_battery_report_none(inv):
    asset = commission(inv, software_release_id="AC-TR50:SW:2.1.0")
    assert inv.get_robot(asset)["reported"]["software_version"] == "2.1.0"
    arm = inv.get_robot(commission(inv, model="FB-CX10", zone="pack_cell_1"))
    assert arm["reported"]["battery"] is None
    assert arm["declared_ai_policy_version"] == "grasp-3.2.0"
    assert arm["reported"]["ai_policy_version"] == "grasp-3.2.0"


def test_lifecycle_transitions(inv):
    asset = commission(inv)
    change = inv.set_lifecycle_status(asset, "OUT_OF_SERVICE", "Awaiting dock repair")
    assert change["diff"]["lifecycle_status"] == {"from": "IN_SERVICE", "to": "OUT_OF_SERVICE"}
    assert change["revision"] == 2 and change["reason"] == "Awaiting dock repair"
    with pytest.raises(Conflict):
        inv.set_lifecycle_status(asset, "COMMISSIONING", "x")
    with pytest.raises(ValueError):
        inv.set_lifecycle_status(asset, "DECOMMISSIONED", "x")
    with pytest.raises(ValueError):
        inv.set_lifecycle_status(asset, "IN_SERVICE", "")
    with pytest.raises(ValueError):
        inv.set_lifecycle_status(asset, "BROKEN", "x")
    inv.set_lifecycle_status(asset, "IN_SERVICE", "Repaired")
    assert inv.get_robot(asset)["lifecycle_status"] == "IN_SERVICE"


def test_decommission_is_terminal(inv):
    asset = commission(inv)
    assert inv.decommission(asset, "Written off")["action"] == "DECOMMISSIONED"
    robot = inv.get_robot(asset)
    assert robot["lifecycle_status"] == "DECOMMISSIONED" and robot["decommissioned_at"]
    with pytest.raises(Conflict):
        inv.decommission(asset, "again")
    with pytest.raises(Conflict):
        inv.set_lifecycle_status(asset, "IN_SERVICE", "x")
    with pytest.raises(Conflict):
        inv.admin_edit(asset, {"fleet_id": "F"}, "x")


def test_admin_edit_is_allowlisted_and_requires_a_reason(inv):
    asset = commission(inv)
    change = inv.admin_edit(asset, {"fleet_id": "FLEET-B", "home_zone": "charging_station"}, "Re-homed",
                            actor={"type": "WORKER", "id": "E-1"})
    assert change["action"] == "ADMIN_EDIT" and change["actor"] == {"type": "WORKER", "id": "E-1"}
    assert change["diff"]["fleet_id"] == {"from": None, "to": "FLEET-B"}
    with pytest.raises(ValueError, match="serial_number"):
        inv.admin_edit(asset, {"serial_number": "X"}, "no")
    with pytest.raises(ValueError):
        inv.admin_edit(asset, {"fleet_id": "FLEET-C"}, "")
    with pytest.raises(ValueError):
        inv.admin_edit(asset, {"fleet_id": "FLEET-B"}, "same value")
    with pytest.raises(ValueError):
        inv.admin_edit(asset, {"hw_revision": "Z"}, "bad rev")


def test_report_state_logs_only_significant_changes(inv, clock):
    asset = commission(inv)
    before = inv.store.count("change_log")
    clock.advance(seconds=30)
    assert inv.report_state(asset, {"operational_mode": "MOVING", "zone": "shelf_a", "battery": {"soc_pct": 80.0}}) is None
    assert inv.store.count("change_log") == before
    reported = inv.get_robot(asset)["reported"]
    assert reported["operational_mode"] == "MOVING" and reported["battery"]["soc_pct"] == 80.0
    assert reported["reported_at"] == "2026-09-30T12:00:30Z"
    change = inv.report_state(asset, {"software_version": "0.9.0-beta", "battery": {"cycle_count": 3}})
    assert change["aggregate_type"] == "ROBOT_OBSERVATION" and change["revision"] == 2
    assert change["diff"]["software_version"] == {"from": "2.2.0", "to": "0.9.0-beta"}
    flags = inv.get_robot(asset)["flags"]
    assert flags["software_mismatch"] is True and flags["running_unknown_software"] is True
    with pytest.raises(ValueError):
        inv.report_state(asset, {"firmware": "x"})
    with pytest.raises(ValueError):
        inv.report_state(asset, {"health_state": "SPARKLY"})


def test_recalled_and_stale_flags(inv, clock):
    asset = commission(inv)
    inv.report_state(asset, {"software_version": "2.2.1"})
    assert inv.get_robot(asset)["flags"]["running_recalled_release"] is True
    clock.advance(seconds=301)
    assert inv.get_robot(asset)["flags"]["report_stale"] is True
    assert inv.touch_remote_reports() == 1
    assert inv.get_robot(asset)["flags"]["report_stale"] is False
    inv.report_state(asset, {"connectivity": "OFFLINE"})
    clock.advance(seconds=301)
    assert inv.touch_remote_reports() == 0  # offline robots don't report in
    assert inv.touch_remote_reports(exclude=[asset]) == 0


def test_list_robots_filters_and_summarises(inv):
    a = commission(inv, site="WH-01")
    b = commission(inv, model="NW-PF1200", site="WH-02", zone="pallet_aisle")
    inv.set_lifecycle_status(b, "OUT_OF_SERVICE", "x")
    assert [r["asset_id"] for r in inv.list_robots(site="WH-02")] == [b]
    assert [r["asset_id"] for r in inv.list_robots(status="in_service")] == [a]
    row = inv.list_robots()[0]
    assert row["model_name"] == "TR-50 Tote Runner" and row["embodiment_class"] == "AMR"
    assert row["reported_software_version"] == "2.2.0" and row["flags"]["calibration_worst"] == "VALID"


def test_robot_history_is_newest_first(inv):
    asset = commission(inv)
    inv.admin_edit(asset, {"fleet_id": "F"}, "x")
    assert [c["action"] for c in inv.robot_history(asset)] == ["ADMIN_EDIT", "STATE_REPORTED", "COMMISSIONED"]
    with pytest.raises(NotFound):
        inv.robot_history("AST-404")
