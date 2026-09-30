"""Demo fleet and workforce for the inventory (spec §6).

Everything is created through InventoryService actions, backdated with
service.at(), so the seeded change_log history has exactly the shape real
use produces. Deterministic: fixed ids, counters and relative dates.
WH-01 is the simulated floor; WH-02 is another site that exists only in the
inventory, seeded so every trust-relevant flag shows up somewhere.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Callable, Optional, Sequence

from .core import SYSTEM

FLOOR_SITE = "WH-01"
REMOTE_SITE = "WH-02"


def seed_demo(service) -> None:
    now = service.now()

    def ago(days: float) -> datetime:
        return now - timedelta(days=days)

    with service.transaction():
        _seed_workers(service, ago)
        _seed_fleet(service, ago)
        with service.at(now):
            service.touch_remote_reports()


def _seed_workers(service, ago: Callable[[float], datetime]) -> None:
    def hire(worker_id: str, name: str, roles: Sequence[str], sites: Sequence[str], *, days: float,
             worker_type: str = "EMPLOYEE", organization: str = "Warehouse Operations",
             supervisor: Optional[str] = None) -> None:
        with service.at(ago(days)):
            service.register_worker(name, worker_type, organization, roles, sites, worker_id=worker_id,
                                    supervisor_id=supervisor, actor=SYSTEM)

    def credential(worker_id: str, code: str, *, days: float, valid_days: int,
                   issuer: str = "Site Training Office", **scopes: Any) -> str:
        with service.at(ago(days)):
            start = service.now()
            return service.issue_credential(worker_id, code, issuer, effective_from=start,
                                            expires_at=start + timedelta(days=valid_days),
                                            actor=SYSTEM, **scopes)["subject_id"]

    hire("E-10001", "Sam", ["WAREHOUSE_OPERATOR", "SAFETY_INSPECTOR"], [FLOOR_SITE], days=900)
    credential("E-10001", "safety_inspection", days=200, valid_days=365, site_scope=[FLOOR_SITE])
    credential("E-10001", "electrical_safety", days=100, valid_days=365, site_scope=[FLOOR_SITE])
    hire("E-10002", "Lee", ["WAREHOUSE_OPERATOR"], [FLOOR_SITE], days=400)
    credential("E-10002", "safety_inspection", days=60, valid_days=365, site_scope=[FLOOR_SITE])
    hire("E-10003", "Noor Haddad", ["MAINTENANCE_TECH"], [FLOOR_SITE, REMOTE_SITE], days=1200)
    credential("E-10003", "equipment_maintenance", days=500, valid_days=730)
    credential("E-10003", "robot_maintenance", days=300, valid_days=730, equipment_scope=["AC-TR50", "AC-PK30", "AC-SC1"])
    credential("E-10003", "electrical_safety", days=120, valid_days=365)
    with service.at(ago(90)):
        service.record_training("E-10003", "LOTO-101", "3", completed_at=ago(90),
                                expires_at=ago(90) + timedelta(days=365), actor=SYSTEM)
    hire("E-10004", "Mateo Silva", ["ROBOTICS_TECH"], [REMOTE_SITE], days=700)
    credential("E-10004", "drone_operations", days=250, valid_days=730, equipment_scope=["CT-IX2"])
    credential("E-10004", "robot_maintenance", days=200, valid_days=730)
    credential("E-10004", "robot_cell_access", days=150, valid_days=365, equipment_scope=["FB-CX10"])
    hire("E-10005", "Ana Kowalski", ["FORKLIFT_OPERATOR"], [REMOTE_SITE], days=1500)
    credential("E-10005", "forklift_operator", days=400, valid_days=1095, equipment_scope=["NW-PF1200"])
    credential("E-10005", "heavy_equipment", days=400, valid_days=1095)
    hire("E-10006", "Jordan Blake", ["HUMANOID_SUPERVISOR"], [REMOTE_SITE], days=300)
    credential("E-10006", "humanoid_supervision", days=90, valid_days=365, equipment_scope=["TS-H1"],
               site_scope=[REMOTE_SITE])
    credential("E-10006", "safety_inspection", days=90, valid_days=365)
    hire("E-10007", "Kai Nakamura", ["FORKLIFT_OPERATOR"], [REMOTE_SITE], days=358, worker_type="CONTRACTOR",
         organization="Proseware Staffing", supervisor="E-10005")
    credential("E-10007", "forklift_operator", days=358, valid_days=365, issuer="Proseware Staffing Training",
               equipment_scope=["NW-PF1200"])  # expires in 7 days
    hire("E-10008", "Riley Chen", ["ROBOT_CELL_OPERATOR"], [REMOTE_SITE], days=500)
    revoked = credential("E-10008", "robot_cell_access", days=200, valid_days=365, equipment_scope=["FB-CX10"])
    credential("E-10008", "safety_inspection", days=100, valid_days=365)
    with service.at(ago(10)):
        service.revoke_credential(revoked, "Failed recertification audit", actor=SYSTEM)
    hire("E-10009", "Sasha Ivanova", ["FORKLIFT_OPERATOR"], [REMOTE_SITE], days=800)
    credential("E-10009", "forklift_operator", days=300, valid_days=1095, equipment_scope=["NW-PF1200"])
    with service.at(ago(14)):
        service.set_employment_status("E-10009", "ON_LEAVE", "Parental leave", actor=SYSTEM)
    hire("E-10010", "Morgan Patel", ["WAREHOUSE_OPERATOR"], [FLOOR_SITE], days=600)
    credential("E-10010", "safety_inspection", days=250, valid_days=365)
    with service.at(ago(45)):
        service.set_employment_status("E-10010", "TERMINATED", "Left the company", actor=SYSTEM)


def _seed_fleet(service, ago: Callable[[float], datetime]) -> None:
    def commission(asset_id: str, model: str, site: str, zone: str, *, days: float,
                   hw: Optional[str] = None, version: Optional[str] = None) -> None:
        with service.at(ago(days)):
            service.commission_robot(model, site, zone, asset_id=asset_id, hw_revision=hw,
                                     software_release_id=f"{model}:SW:{version}" if version else None,
                                     fleet_id=f"{site}-FLEET", actor=SYSTEM)

    def recalibrate(asset_id: str, *, days: float, by: str, skip_types: Sequence[str] = ()) -> None:
        with service.at(ago(days)):
            for component in service.get_robot(asset_id)["components"]:
                if component["calibration_interval_days"] and component["component_type"] not in skip_types:
                    service.record_calibration(component["component_id"], "PASS", performed_by=by)

    def report(asset_id: str, days: float = 0, **fields: Any) -> None:
        with service.at(ago(days)):
            service.report_state(asset_id, fields, actor=SYSTEM)

    # ---- WH-01: the simulated floor ------------------------------------ #
    commission("AST-000101", "AC-TR50", FLOOR_SITE, "parking_area", days=380, version="2.1.0")
    recalibrate("AST-000101", days=20, by="E-10003")
    report("AST-000101", battery={"soh_pct": 96.4, "cycle_count": 212})
    commission("AST-000102", "AC-TR50", FLOOR_SITE, "parking_area", days=380, version="2.1.0")
    recalibrate("AST-000102", days=20, by="E-10003")
    report("AST-000102", battery={"soh_pct": 95.1, "cycle_count": 240})
    commission("AST-000103", "AC-TR50", FLOOR_SITE, "charging_station", days=500, hw="C", version="2.2.0")
    recalibrate("AST-000103", days=30, by="E-10003")
    with service.at(ago(1)):
        wo = service.open_work_order("AST-000103", "CORRECTIVE",
                                     "Lidar returns degraded on the east aisle - replace unit",
                                     technician_id="E-10003")["subject_id"]
        service.swap_component(wo, "lidar", performed_by="E-10003")

    # ---- WH-02: inventory-only site, one scenario per asset ------------ #
    commission("AST-000201", "AC-TR50", REMOTE_SITE, "tote_aisle_1", days=90)
    commission("AST-000202", "AC-TR50", REMOTE_SITE, "tote_aisle_2", days=100)
    report("AST-000202", days=3, software_version="2.2.1", os_version=service.os_version_for("2.2.1"))
    commission("AST-000203", "AC-PK30", REMOTE_SITE, "pick_station_1", days=400)
    recalibrate("AST-000203", days=60, by="E-10004", skip_types=("LIDAR",))
    commission("AST-000204", "AC-SC1", REMOTE_SITE, "patrol_loop", days=356)
    recalibrate("AST-000204", days=60, by="E-10004", skip_types=("LIDAR", "IMU"))
    commission("AST-000205", "NW-PF1200", REMOTE_SITE, "pallet_aisle", days=300, hw="A", version="2.1.1")
    recalibrate("AST-000205", days=30, by="E-10004")
    commission("AST-000206", "NW-PF1200", REMOTE_SITE, "pallet_aisle", days=200, hw="B", version="2.1.1")
    recalibrate("AST-000206", days=20, by="E-10004")
    with service.at(ago(1)):
        job = service.start_ota("AST-000206", "NW-PF1200:SW:2.2.0",
                                actor={"type": "WORKER", "id": "E-10004"})["subject_id"]
        service.transition_ota(job, "DOWNLOADING")
        service.transition_ota(job, "INSTALLING")
        service.report_state("AST-000206", {"software_version": "2.2.0",
                                            "os_version": service.os_version_for("2.2.0")}, actor=SYSTEM)
        service.transition_ota(job, "REPORTED")
    commission("AST-000207", "NW-HH300", REMOTE_SITE, "dock_2", days=250)
    recalibrate("AST-000207", days=40, by="E-10004")
    report("AST-000207", days=2, connectivity="OFFLINE", health_state="UNKNOWN")
    commission("AST-000208", "CT-IX2", REMOTE_SITE, "drone_pad", days=150)
    recalibrate("AST-000208", days=30, by="E-10004")
    report("AST-000208", battery={"soh_pct": 71.0, "cycle_count": 612})
    commission("AST-000209", "CT-IX2", REMOTE_SITE, "drone_pad", days=60)
    commission("AST-000210", "FB-CX10", REMOTE_SITE, "pack_cell_1", days=120)
    recalibrate("AST-000210", days=15, by="E-10004")
    commission("AST-000211", "FB-CX10", REMOTE_SITE, "pack_cell_2", days=120)
    recalibrate("AST-000211", days=15, by="E-10004")
    firmware = dict(service.get_robot("AST-000211")["reported"]["component_firmware"])
    report("AST-000211", days=5, component_firmware={**firmware, "force_torque": "3.2.0"})
    commission("AST-000212", "TS-H1", REMOTE_SITE, "tote_aisle_1", days=45)
    commission("AST-000213", "AC-TR50", REMOTE_SITE, "tote_aisle_2", days=700, version="2.1.0")
    with service.at(ago(30)):
        service.decommission("AST-000213", "Chassis damage - written off", actor=SYSTEM)
