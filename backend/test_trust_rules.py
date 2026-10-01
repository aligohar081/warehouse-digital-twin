"""The physical trust-layer rules (multi-embodiment spec §10.1) and the fault
switchboard (§10.6). Every rule takes plain values and returns (ok, reason)."""
import random

import pytest

from backend.digital_twin import DigitalTwin
from backend.eligibility import (
    box_kind_ok, cert_scope_ok, clearance_ok, drone_round_trip_ok, no_fly_ok, payload_ok,
    reach_ok, supervision_ok,
)
from backend.faults import FAULT_RISKS, FaultInjector
from backend.models import CONFIG


def test_payload_ok():
    assert payload_ok(600.0, 1200.0) == (True, None)
    assert payload_ok(300.0, 300.0) == (True, None)          # exactly at the limit
    ok, reason = payload_ok(310.5, 300.0)
    assert not ok and reason == "a 310.5 kg load is over its 300 kg payload limit"
    assert payload_ok(None, 50.0) == (True, None) and payload_ok(5.0, None) == (True, None)


def test_box_kind_ok():
    assert box_kind_ok("PALLET", ("PALLET",)) == (True, None)
    ok, reason = box_kind_ok("PALLET", ["TOTE", "ITEM"])
    assert not ok and reason == "cannot handle a PALLET (handles: TOTE, ITEM)"
    assert box_kind_ok("TOTE", []) == (False, "cannot handle a TOTE (handles: nothing)")
    assert box_kind_ok(None, []) == (True, None)


def test_reach_ok():
    assert reach_ok(1, 1) == (True, None) and reach_ok(0, 4) == (True, None)
    assert reach_ok(2, 1) == (False, "level 2 is out of its reach (highest level 1)")
    assert reach_ok(None, 0) == (True, None)


def test_clearance_ok():
    assert clearance_ok(["WIDE", "WIDE", None], "WIDE") == (True, None)
    assert clearance_ok(["WIDE", "NARROW", "NARROW"], "WIDE") == \
        (False, "a wide robot's route uses 2 narrow cell(s)")
    assert clearance_ok(["NARROW"], "NARROW") == (True, None)   # narrow robots go anywhere drivable
    assert clearance_ok(["NARROW"], None) == (True, None)       # drones and arms have no clearance


def test_no_fly_ok():
    assert no_fly_ok([False, False]) == (True, None) and no_fly_ok([]) == (True, None)
    assert no_fly_ok([False, True, True]) == (False, "an air route crosses 2 no-fly cell(s)")


def test_drone_round_trip_ok_keeps_a_quarter_of_capacity_in_reserve():
    assert drone_round_trip_ok(100.0, 60.0, 150.0) == (True, None)   # needs 60 + 37.5
    ok, reason = drone_round_trip_ok(90.0, 60.0, 150.0)
    assert not ok and reason == "has 90.0 Wh but the round trip needs 60.0 Wh plus a 25% reserve (97.5 Wh)"
    assert drone_round_trip_ok(97.5, 60.0, 150.0) == (True, None)


def test_supervision_ok():
    assert supervision_ok(None, False, False, False) == (True, None)   # nothing to supervise
    assert supervision_ok("humanoid_supervision", True, True, True) == (True, None)
    assert supervision_ok("humanoid_supervision", True, False, True)[1] == \
        "no operator holds a valid 'humanoid_supervision' credential"
    assert supervision_ok("humanoid_supervision", False, True, True)[1] == \
        "no operator with a 'humanoid_supervision' credential is on shift"
    assert supervision_ok("humanoid_supervision", True, True, False)[1] == \
        "no on-shift supervisor's 'humanoid_supervision' credential covers this robot and site"


def test_cert_scope_ok():
    scopes = {"robot_cell_access": {"equipment": ["FB-CX10"], "site": []},
              "humanoid_supervision": {"equipment": ["TS-H1"], "site": ["WH-01"]}}
    assert cert_scope_ok(scopes, "robot_cell_access", "FB-CX10", "WH-01") == (True, None)
    assert cert_scope_ok(scopes, "robot_cell_access", None, "WH-02") == (True, None)  # site unrestricted
    assert cert_scope_ok(scopes, "robot_cell_access", "TS-H1")[1] == \
        "has a 'robot_cell_access' credential that does not cover TS-H1 (covers: FB-CX10)"
    assert cert_scope_ok(scopes, "humanoid_supervision", "TS-H1", "WH-02")[1] == \
        "has a 'humanoid_supervision' credential that does not cover site WH-02 (covers: WH-01)"
    assert cert_scope_ok(scopes, "forklift_operator") == \
        (False, "does not hold a valid 'forklift_operator' credential")
    assert cert_scope_ok(None, "robot_cell_access")[0] is False


def test_every_fault_has_a_zero_risk_by_default():
    assert sorted(FAULT_RISKS) == ["conveyor_jam", "grasp_fail", "handoff_loss", "mis_sort",
                                   "misdeclared_weight", "scan_miscount", "wrong_level"]
    assert all(CONFIG[key] == 0.0 for key in FAULT_RISKS.values())


def test_an_armed_fault_fires_once_then_the_risk_decides(monkeypatch):
    faults = FaultInjector()
    state = random.getstate()
    assert not faults.roll("scan_miscount")
    assert random.getstate() == state          # a zero risk draws no random number
    assert faults.arm("scan-miscount") == 1 and faults.arm("scan_miscount") == 2
    assert faults.armed() == {"scan_miscount": 2}
    assert faults.roll("scan_miscount") and faults.roll("scan_miscount")
    assert not faults.roll("scan_miscount") and faults.armed() == {}
    monkeypatch.setitem(CONFIG, "SCAN_MISCOUNT_RISK", 1.0)
    assert faults.roll("scan_miscount", rng=random.Random(1))
    with pytest.raises(ValueError, match="Unknown fault kind"):
        faults.arm("gremlins")
    with pytest.raises(ValueError, match="at least 1"):
        faults.arm("mis_sort", count=0)


def test_the_twin_owns_a_fault_switchboard_that_reset_clears(tmp_path):
    twin = DigitalTwin(log_dir=str(tmp_path / "logs"), data_dir=str(tmp_path / "data"),
                       persist_logs=False, demo=True, demo_tasks=False)
    twin.faults.arm("mis_sort")
    assert twin.faults.armed() == {"mis_sort": 1}
    twin.reset(demo_tasks=False)
    assert twin.faults.armed() == {}
