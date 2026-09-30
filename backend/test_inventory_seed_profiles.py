"""Inventory seed profiles (multi-embodiment spec §4.1) and the catalog data
the new floor needs (§5.1, §13.2)."""
import logging
import os
from datetime import datetime, timezone

import pytest

from backend.digital_twin import DigitalTwin
from backend.inventory import open_inventory, reseed
from backend.inventory.bootstrap import SEED_VERSION, SEED_VERSIONS
from backend.warehouse import Warehouse

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)
DC_HOME_ZONES = {
    "AST-000101": "parking_area", "AST-000102": "parking_area", "AST-000103": "workshop",
    "AST-000201": "tote_aisle_1", "AST-000202": "tote_aisle_2", "AST-000203": "pick_station_1",
    "AST-000204": "patrol_loop", "AST-000205": "pallet_aisle_1", "AST-000206": "pallet_aisle_2",
    "AST-000207": "dock_2", "AST-000208": "drone_pad", "AST-000209": "drone_pad",
    "AST-000210": "pack_cell_1", "AST-000211": "pack_cell_2", "AST-000212": "returns_qc",
    "AST-000213": None,
}


@pytest.fixture(scope="module")
def dc():
    return open_inventory(":memory:", demo=True, clock=lambda: NOW, profile="distribution_center")


def test_everything_is_at_wh01_on_the_new_floor(dc):
    robots = dc.list_robots()
    assert len(robots) == 16 and dc.list_robots(site="WH-02") == []
    assert {r["site_code"] for r in robots} == {"WH-01"}
    assert {r["asset_id"]: r["home_zone"] for r in robots} == DC_HOME_ZONES
    for worker in dc.list_workers():
        assert worker["site_codes"] == ["WH-01"], worker["worker_id"]
    jordan = dc.get_worker("E-10006")
    supervision = next(c for c in jordan["credentials"] if c["code"] == "humanoid_supervision")
    assert supervision["site_scope"] == ["WH-01"] and supervision["equipment_scope"] == ["TS-H1"]


def test_every_placed_asset_has_a_real_zone_of_the_new_floor(dc):
    floor = Warehouse(layout="distribution_center")
    for robot in dc.list_robots():
        if robot["lifecycle_status"] != "DECOMMISSIONED":
            assert robot["home_zone"] in floor.zones, robot["asset_id"]


def test_the_new_floor_keeps_a_s_scenarios_and_brings_the_hauler_online(dc):
    flags = {r["asset_id"]: r["flags"] for r in dc.list_robots()}
    assert dc.get_robot("AST-000103")["lifecycle_status"] == "MAINTENANCE"
    assert flags["AST-000202"]["running_recalled_release"]
    assert flags["AST-000203"]["calibration_worst"] == "EXPIRED"
    assert flags["AST-000204"]["calibration_worst"] == "DUE_SOON"
    assert dc.get_robot("AST-000205")["hw_revision"] == "A"
    assert flags["AST-000206"]["active_ota_job"]["state"] == "REPORTED"
    hauler = dc.get_robot("AST-000207")
    assert hauler["reported"]["connectivity"] == "ONLINE" and not flags["AST-000207"]["report_stale"]
    assert dc.get_robot("AST-000208")["reported"]["battery"]["soh_pct"] == 71.0
    assert [m["slot"] for m in flags["AST-000211"]["component_firmware_mismatches"]] == ["force_torque"]
    assert dc.get_robot("AST-000213")["lifecycle_status"] == "DECOMMISSIONED"
    assert "REVOKED" in [c["validity"] for c in dc.get_worker("E-10008")["credentials"]]
    assert dc.get_worker("E-10009")["employment_status"] == "ON_LEAVE"
    assert dc.get_worker("E-10010")["employment_status"] == "TERMINATED"


def test_seed_versions_and_templates_are_per_profile():
    assert set(SEED_VERSIONS) == {"classic", "distribution_center"}
    assert SEED_VERSION == SEED_VERSIONS["classic"] != SEED_VERSIONS["distribution_center"]
    classic, dc = open_inventory(), open_inventory(profile="distribution_center")  # template copies
    assert classic.get_robot("AST-000201")["site_code"] == "WH-02"
    assert dc.get_robot("AST-000201")["site_code"] == "WH-01"
    assert dc.store.get_meta("seed_profile") == "distribution_center"
    assert dc.store.get_meta("seed_version") == SEED_VERSIONS["distribution_center"]
    with pytest.raises(ValueError, match="Unknown inventory seed profile"):
        open_inventory(profile="moon_base")


def test_reseed_keeps_the_profile_it_was_seeded_under():
    templated = open_inventory(profile="distribution_center")
    clocked = open_inventory(":memory:", demo=True, clock=lambda: NOW, profile="distribution_center")
    for inventory in (templated, clocked):
        inventory.decommission("AST-000201", "test")
        reseed(inventory)
        assert inventory.get_robot("AST-000201")["lifecycle_status"] == "IN_SERVICE"
        assert inventory.get_robot("AST-000201")["site_code"] == "WH-01"


def test_a_file_opened_under_the_other_profile_is_reseeded_with_a_backup(tmp_path, caplog):
    path = str(tmp_path / "inv.sqlite3")
    first = open_inventory(path)
    assert first.get_robot("AST-000201")["site_code"] == "WH-02"
    first.store.conn.close()
    with caplog.at_level(logging.WARNING, logger="backend.inventory"):
        again = open_inventory(path, profile="distribution_center")
    assert again.get_robot("AST-000201")["site_code"] == "WH-01"
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1 and "'classic' seed profile" in warnings[0] and path + ".bak" in warnings[0]
    assert os.path.exists(path + ".bak")
    caplog.clear()
    again.store.conn.close()
    with caplog.at_level(logging.WARNING, logger="backend.inventory"):
        open_inventory(path, profile="distribution_center")  # now current: reused silently
    assert not caplog.records


def test_the_twin_opens_its_inventory_under_its_layout(tmp_path):
    def twin(layout):
        return DigitalTwin(log_dir=str(tmp_path / "logs"), data_dir=str(tmp_path / "data"),
                           persist_logs=False, demo=True, demo_tasks=False, layout=layout)

    assert twin("classic").inventory.store.get_meta("seed_profile") == "classic"
    new_floor = twin("distribution_center")
    assert new_floor.inventory.store.get_meta("seed_profile") == "distribution_center"
    assert new_floor.inventory.list_robots(site="WH-02") == []
    new_floor.reset(demo_tasks=False)
    assert new_floor.inventory.store.get_meta("seed_profile") == "distribution_center"


def test_catalog_timings_for_the_new_floor():
    inventory = open_inventory(demo=False)
    specs = {m["model_code"]: m["spec"] for m in inventory.list_models()}
    assert specs["NW-PF1200"]["lift_speed_mps"] == 0.3
    for code, spec in specs.items():
        if code != "NW-PF1200":
            assert spec["lift_speed_mps"] == 0.5, code
    timings = {code: (spec.get("grasp_s"), spec.get("place_s")) for code, spec in specs.items()}
    assert timings == {
        "NW-PF1200": (3.0, 3.0), "NW-HH300": (3.0, 3.0), "AC-TR50": (2.0, 1.5), "AC-PK30": (2.5, 1.5),
        "FB-CX10": (2.5, 1.5), "TS-H1": (3.0, 2.0), "AC-SC1": (None, None), "CT-IX2": (None, None),
    }
    drone = specs["CT-IX2"]
    assert (drone["takeoff_s"], drone["land_s"], drone["scan_s"]) == (4.0, 5.0, 3.0)


def test_release_known_issues():
    inventory = open_inventory(demo=False)
    drift = inventory.get_release("AC-TR50:SW:2.2.1")["known_issues"]
    assert [issue["code"] for issue in drift] == ["ODOMETRY_HEADING_DRIFT"]
    assert drift[0]["effect"] == {"heading_drift_deg_per_m": 0.5} and drift[0]["component_type"] == "DRIVE_UNIT"
    offset = inventory.get_release("FB-FT6:FW:3.2.0")["known_issues"]
    assert offset[0]["code"] == "FORCE_TORQUE_Z_OFFSET" and offset[0]["effect"] == {"force_z_offset_n": 8.0}
    assert inventory.get_release("AC-TR50:SW:2.2.0")["known_issues"] == []
    inventory.publish_release("ROBOT_SOFTWARE", "AC-TR50", "2.3.0")
    assert inventory.get_release("AC-TR50:SW:2.3.0")["known_issues"] == []
