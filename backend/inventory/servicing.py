"""Servicing: work orders, component swaps and calibration records — the
maintenance history a CMMS keeps for every robot."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .errors import Conflict, NotFound

WORK_ORDER_TYPES = ("PREVENTIVE", "CORRECTIVE", "INSPECTION")
CALIBRATION_RESULTS = ("PASS", "FAIL")


class ServicingMixin:
    def open_work_orders(self) -> List[Dict[str, Any]]:
        """Every work order still open, on any asset, oldest first: one read
        for the floor's technicians (a robot's own are in get_robot)."""
        return self.store.select("work_order", "status = 'OPEN'", order="opened_at, rowid")

    def open_work_order(self, asset_id: str, wo_type: str, description: str,
                        technician_id: Optional[str] = None, actor: Any = None) -> Dict[str, Any]:
        wo_type = str(wo_type or "").upper()
        if wo_type not in WORK_ORDER_TYPES:
            raise ValueError(f"Unknown work order type {wo_type!r} (known: {list(WORK_ORDER_TYPES)})")
        description = str(description or "").strip()
        if not description:
            raise ValueError("A description is required")
        with self._tx():
            asset = self._asset(asset_id)
            if asset["lifecycle_status"] == "DECOMMISSIONED":
                raise Conflict(f"{asset_id} is decommissioned")
            if technician_id:
                self._require("worker", "worker_id", technician_id, "Worker")
            before = self._asset_document(asset_id)
            wo_id = self.store.next_id("WO-", "work_order", "wo_id", 6)
            self.store.insert("work_order", {
                "wo_id": wo_id, "asset_id": asset_id, "type": wo_type, "description": description,
                "technician_id": technician_id, "status": "OPEN", "opened_at": self.now_iso(),
                "closed_at": None, "resolution": None,
            })
            if wo_type != "INSPECTION" and asset["lifecycle_status"] != "MAINTENANCE":
                self.store.update("robot_asset", "asset_id", asset_id, {"lifecycle_status": "MAINTENANCE"})
            return self._commit_asset(asset_id, "WORK_ORDER_OPENED", before,
                                      actor=self._worker_actor(technician_id, actor), subject_id=wo_id)

    def close_work_order(self, wo_id: str, resolution: str, actor: Any = None) -> Dict[str, Any]:
        resolution = str(resolution or "").strip()
        if not resolution:
            raise ValueError("A resolution is required")
        with self._tx():
            work_order = self._require("work_order", "wo_id", wo_id, "Work order")
            if work_order["status"] != "OPEN":
                raise Conflict(f"Work order {wo_id} is already closed")
            asset_id = work_order["asset_id"]
            asset = self._asset(asset_id)
            before = self._asset_document(asset_id)
            self.store.update("work_order", "wo_id", wo_id,
                              {"status": "CLOSED", "closed_at": self.now_iso(), "resolution": resolution})
            if asset["lifecycle_status"] == "MAINTENANCE" and not self._open_work_orders(asset_id):
                self.store.update("robot_asset", "asset_id", asset_id, {"lifecycle_status": "IN_SERVICE"})
            return self._commit_asset(asset_id, "WORK_ORDER_CLOSED", before,
                                      actor=self._worker_actor(work_order["technician_id"], actor), subject_id=wo_id)

    def swap_component(self, wo_id: str, slot: str, performed_by: Optional[str] = None,
                       hw_revision: Optional[str] = None, part_number: Optional[str] = None,
                       actor: Any = None) -> Dict[str, Any]:
        with self._tx():
            work_order = self._require("work_order", "wo_id", wo_id, "Work order")
            if work_order["status"] != "OPEN":
                raise Conflict(f"Work order {wo_id} is closed")
            asset_id = work_order["asset_id"]
            asset = self._asset(asset_id)
            if asset["lifecycle_status"] != "MAINTENANCE":
                raise Conflict(f"{asset_id} must be in MAINTENANCE to swap parts (is {asset['lifecycle_status']})")
            installed = self.store.select("component", "asset_id = ? AND slot = ? AND status != 'REMOVED'",
                                          (asset_id, slot))
            if not installed:
                raise NotFound(f"{asset_id} has no installed component in slot {slot!r}")
            old = installed[0]
            new_part = part_number or old["part_number"]
            if self.get_part(new_part)["component_type"] != self.get_part(old["part_number"])["component_type"]:
                raise ValueError(f"{new_part} cannot replace a {self.get_part(old['part_number'])['component_type']}")
            if performed_by:
                self._require("worker", "worker_id", performed_by, "Worker")
            before = self._asset_document(asset_id)
            now = self.now_iso()
            self.store.update("component", "component_id", old["component_id"],
                              {"status": "REMOVED", "removed_at": now})
            for job in self._active_ota_rows(asset_id):
                if job["component_id"] == old["component_id"]:
                    self.store.update("ota_job", "job_id", job["job_id"],
                                      {"state": "FAILED", "updated_at": now, "failure_reason": f"component replaced in work order {wo_id}"})
            new = self._install_component(asset_id, slot, new_part, hw_revision)
            return self._commit_asset(asset_id, "COMPONENT_SWAPPED", before,
                                      actor=self._worker_actor(performed_by, actor),
                                      reason=f"work order {wo_id}: replaced {old['serial']}",
                                      subject_id=new["component_id"])

    def record_calibration(self, component_id: str, result: str, performed_by: Optional[str] = None,
                           method: str = "FIELD", certificate_ref: Optional[str] = None,
                           actor: Any = None) -> Dict[str, Any]:
        result = str(result or "").upper()
        if result not in CALIBRATION_RESULTS:
            raise ValueError(f"Unknown calibration result {result!r} (known: {list(CALIBRATION_RESULTS)})")
        with self._tx():
            component = self._require("component", "component_id", component_id, "Component")
            if component["status"] == "REMOVED":
                raise Conflict(f"{component_id} has been removed from its robot")
            asset = self._asset(component["asset_id"])
            if asset["lifecycle_status"] == "DECOMMISSIONED":
                raise Conflict(f"{asset['asset_id']} is decommissioned")
            part = self.get_part(component["part_number"])
            if not part["calibration_interval_days"]:
                raise ValueError(f"{part['name']} does not require calibration")
            if performed_by:
                self._require("worker", "worker_id", performed_by, "Worker")
            before = self._asset_document(asset["asset_id"])
            calibration_id = self._insert_calibration(component_id, part, result, performed_by,
                                                      method or "FIELD", certificate_ref)
            return self._commit_asset(asset["asset_id"], "CALIBRATION_RECORDED", before,
                                      actor=self._worker_actor(performed_by, actor), subject_id=calibration_id)
