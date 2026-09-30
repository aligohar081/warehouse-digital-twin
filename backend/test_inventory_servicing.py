"""Servicing: work orders, component swaps, calibration."""
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


def commission(inv):
    return inv.commission_robot("AC-TR50", "WH-01", "parking_area")["aggregate_id"]


def component(inv, asset, slot):
    return next(c for c in inv.get_robot(asset)["components"] if c["slot"] == slot)


def test_open_work_order_moves_the_asset_to_maintenance(inv):
    asset = commission(inv)
    change = inv.open_work_order(asset, "corrective", "Lidar dirty")
    assert change["action"] == "WORK_ORDER_OPENED" and change["subject_id"] == "WO-000001"
    assert inv.get_robot(asset)["lifecycle_status"] == "MAINTENANCE"
    inspected = commission(inv)
    inv.open_work_order(inspected, "INSPECTION", "Quarterly check")
    assert inv.get_robot(inspected)["lifecycle_status"] == "IN_SERVICE"
    with pytest.raises(ValueError):
        inv.open_work_order(asset, "PAINTING", "x")
    with pytest.raises(ValueError):
        inv.open_work_order(asset, "CORRECTIVE", " ")
    with pytest.raises(NotFound):
        inv.open_work_order(asset, "CORRECTIVE", "x", technician_id="E-404")


def test_swap_component_installs_a_new_serial_without_calibration(inv):
    asset = commission(inv)
    old = component(inv, asset, "lidar")
    wo = inv.open_work_order(asset, "CORRECTIVE", "Replace lidar")["subject_id"]
    change = inv.swap_component(wo, "lidar")
    new = component(inv, asset, "lidar")
    assert new["component_id"] == change["subject_id"] != old["component_id"]
    assert new["serial"] != old["serial"]
    assert new["calibration_status"] == "MISSING"
    assert inv.get_robot(asset)["flags"]["calibration_worst"] == "MISSING"
    assert inv.store.get("component", "component_id", old["component_id"])["status"] == "REMOVED"
    assert f"components[{old['component_id']}].serial" in change["diff"]
    assert change["reason"].startswith(f"work order {wo}")
    with pytest.raises(NotFound):
        inv.swap_component(wo, "jetpack")
    with pytest.raises(ValueError):
        inv.swap_component(wo, "lidar", part_number="WT-D3")  # a camera is not a lidar


def test_swap_needs_an_open_work_order_on_an_asset_in_maintenance(inv):
    asset = commission(inv)
    wo = inv.open_work_order(asset, "INSPECTION", "Look only")["subject_id"]
    with pytest.raises(Conflict):
        inv.swap_component(wo, "lidar")  # asset is still IN_SERVICE
    inv.close_work_order(wo, "Looked fine")
    with pytest.raises(Conflict):
        inv.swap_component(wo, "lidar")  # work order closed


def test_close_work_order_returns_to_service_only_when_none_remain_open(inv):
    asset = commission(inv)
    first = inv.open_work_order(asset, "CORRECTIVE", "A")["subject_id"]
    second = inv.open_work_order(asset, "PREVENTIVE", "B")["subject_id"]
    inv.close_work_order(first, "done")
    assert inv.get_robot(asset)["lifecycle_status"] == "MAINTENANCE"
    with pytest.raises(Conflict):
        inv.set_lifecycle_status(asset, "IN_SERVICE", "early")
    closed = inv.close_work_order(second, "done")
    assert closed["action"] == "WORK_ORDER_CLOSED"
    assert inv.get_robot(asset)["lifecycle_status"] == "IN_SERVICE"
    with pytest.raises(Conflict):
        inv.close_work_order(second, "again")
    with pytest.raises(ValueError):
        inv.close_work_order(first, "")


def test_calibration_status_boundaries(inv, clock):
    asset = commission(inv)  # factory calibration at NOW
    scanner = component(inv, asset, "safety_scanner_front")  # 180-day interval
    clock.advance(days=165)
    assert component(inv, asset, "safety_scanner_front")["calibration_status"] == "VALID"
    clock.advance(days=1)
    assert component(inv, asset, "safety_scanner_front")["calibration_status"] == "DUE_SOON"
    clock.advance(days=14)
    assert component(inv, asset, "safety_scanner_front")["calibration_status"] == "EXPIRED"
    change = inv.record_calibration(scanner["component_id"], "pass")
    assert change["action"] == "CALIBRATION_RECORDED" and change["subject_id"].startswith("CAL-")
    assert component(inv, asset, "safety_scanner_front")["calibration_status"] == "VALID"
    inv.record_calibration(scanner["component_id"], "FAIL")
    assert component(inv, asset, "safety_scanner_front")["calibration_status"] == "FAILED"
    with pytest.raises(ValueError):
        inv.record_calibration(scanner["component_id"], "MAYBE")
    with pytest.raises(ValueError):
        inv.record_calibration(component(inv, asset, "compute")["component_id"], "PASS")  # no interval
    with pytest.raises(NotFound):
        inv.record_calibration(scanner["component_id"], "PASS", performed_by="E-404")
