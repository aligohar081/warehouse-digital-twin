"""Over-the-air updates: the OTA job state machine.

STAGED → DOWNLOADING → INSTALLING → REPORTED → VERIFIED | ROLLED_BACK, with
FAILED reachable from DOWNLOADING / INSTALLING. The simulator (FleetBridge)
drives the timed transitions through transition_ota(); a person verifies or
rolls back. Between REPORTED and VERIFIED the robot runs the new version
while the registry still declares the old one — the realistic "update
landed but was never verified" window.
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional

from .core import SYSTEM, actor_of
from .errors import Conflict

OTA_RUNNING_STATES = ("STAGED", "DOWNLOADING", "INSTALLING")
SYSTEM_TRANSITIONS = {
    "STAGED": {"DOWNLOADING"},
    "DOWNLOADING": {"INSTALLING", "FAILED"},
    "INSTALLING": {"REPORTED", "FAILED"},
}


class OtaMixin:
    def _running_version(self, asset: Mapping[str, Any], release: Mapping[str, Any],
                         component: Optional[Mapping[str, Any]]) -> Optional[str]:
        reported = self.store.get("reported_state", "asset_id", asset["asset_id"]) or {}
        if release["kind"] == "COMPONENT_FIRMWARE":
            declared = (self.get_release(component["firmware_release_id"])["version"]
                        if component["firmware_release_id"] else None)
            return (reported.get("component_firmware") or {}).get(component["slot"], declared)
        if release["kind"] == "AI_POLICY_MODEL":
            declared = (self.get_release(asset["ai_policy_release_id"])["version"]
                        if asset["ai_policy_release_id"] else None)
            return reported.get("ai_policy_version") or declared
        return reported.get("software_version") or self.get_release(asset["software_release_id"])["version"]

    def start_ota(self, asset_id: str, release_id: str, component_id: Optional[str] = None,
                  actor: Any = None) -> Dict[str, Any]:
        with self._tx():
            asset = self._asset(asset_id)
            if asset["lifecycle_status"] not in ("IN_SERVICE", "MAINTENANCE"):
                raise Conflict(f"{asset_id} is {asset['lifecycle_status']} — an update needs IN_SERVICE or MAINTENANCE")
            release = self.get_release(release_id)
            if release["status"] == "RECALLED":
                raise Conflict(f"{release_id} has been recalled")
            component = None
            if release["kind"] == "COMPONENT_FIRMWARE":
                if not component_id:
                    raise ValueError("component_id is required for a component-firmware update")
                component = self._require("component", "component_id", component_id, "Component")
                if component["asset_id"] != asset_id or component["status"] == "REMOVED":
                    raise ValueError(f"{component_id} is not installed on {asset_id}")
                if release["target_code"] != component["part_number"]:
                    raise ValueError(f"{release_id} is not firmware for {component['part_number']}")
                hw = component["hw_revision"]
            else:
                if component_id:
                    raise ValueError("component_id only applies to component-firmware updates")
                if release["target_code"] != asset["model_code"]:
                    raise ValueError(f"{release_id} is not a release for {asset['model_code']}")
                hw = asset["hw_revision"]
            if release["min_hw_rev"] and hw < release["min_hw_rev"]:
                raise Conflict(f"{release_id} requires hardware revision {release['min_hw_rev']} or later (installed: {hw})")
            running = self._running_version(asset, release, component)
            if running == release["version"]:
                raise Conflict(f"{asset_id} is already running {release['version']}")
            for job in self._active_ota_rows(asset_id):
                if job["component_id"] == component_id and self.get_release(job["release_id"])["kind"] == release["kind"]:
                    raise Conflict(f"{job['job_id']} is already updating this target ({job['state']})")
            before = self._asset_document(asset_id)
            job_id = self.store.next_id("OTA-", "ota_job", "job_id", 6)
            now = self.now_iso()
            self.store.insert("ota_job", {
                "job_id": job_id, "asset_id": asset_id, "component_id": component_id,
                "release_id": release_id, "from_version": running, "state": "STAGED",
                "created_at": now, "updated_at": now, "created_by": actor_of(actor)["id"],
                "failure_reason": None,
            })
            return self._commit_asset(asset_id, "OTA_STAGED", before, actor=actor, subject_id=job_id)

    def get_ota_job(self, job_id: str) -> Dict[str, Any]:
        return self._ota_view(self._require("ota_job", "job_id", job_id, "OTA job"))

    def active_ota_jobs(self) -> List[Dict[str, Any]]:
        """Jobs the simulator still has to advance (STAGED / DOWNLOADING / INSTALLING)."""
        marks = ", ".join("?" for _ in OTA_RUNNING_STATES)
        return [self._ota_view(job) for job in self.store.select("ota_job", f"state IN ({marks})", OTA_RUNNING_STATES)]

    def transition_ota(self, job_id: str, new_state: str, failure_reason: Optional[str] = None) -> Dict[str, Any]:
        new_state = str(new_state or "").upper()
        with self._tx():
            job = self._require("ota_job", "job_id", job_id, "OTA job")
            if new_state not in SYSTEM_TRANSITIONS.get(job["state"], set()):
                raise Conflict(f"{job_id} cannot go from {job['state']} to {new_state}")
            reason = (failure_reason or "unspecified failure") if new_state == "FAILED" else None
            before = self._asset_document(job["asset_id"])
            self.store.update("ota_job", "job_id", job_id,
                              {"state": new_state, "updated_at": self.now_iso(), "failure_reason": reason})
            return self._commit_asset(job["asset_id"], f"OTA_{new_state}", before, actor=SYSTEM,
                                      reason=reason, subject_id=job_id)

    def verify_ota(self, job_id: str, verified_by: Optional[str] = None, actor: Any = None) -> Dict[str, Any]:
        with self._tx():
            job = self._require("ota_job", "job_id", job_id, "OTA job")
            if job["state"] != "REPORTED":
                raise Conflict(f"{job_id} is {job['state']} — only a REPORTED update can be verified")
            if verified_by:
                self._require("worker", "worker_id", verified_by, "Worker")
            release = self.get_release(job["release_id"])
            before = self._asset_document(job["asset_id"])
            if release["kind"] == "ROBOT_SOFTWARE":
                self.store.update("robot_asset", "asset_id", job["asset_id"], {"software_release_id": release["release_id"]})
            elif release["kind"] == "AI_POLICY_MODEL":
                self.store.update("robot_asset", "asset_id", job["asset_id"], {"ai_policy_release_id": release["release_id"]})
            else:
                self.store.update("component", "component_id", job["component_id"], {"firmware_release_id": release["release_id"]})
            self.store.update("ota_job", "job_id", job_id, {"state": "VERIFIED", "updated_at": self.now_iso()})
            return self._commit_asset(job["asset_id"], "OTA_VERIFIED", before,
                                      actor=self._worker_actor(verified_by, actor), subject_id=job_id)

    def rollback_ota(self, job_id: str, reason: str, actor: Any = None) -> Dict[str, Any]:
        reason = self._reason(reason)
        with self._tx():
            job = self._require("ota_job", "job_id", job_id, "OTA job")
            if job["state"] != "REPORTED":
                raise Conflict(f"{job_id} is {job['state']} — only a REPORTED update can be rolled back")
            before = self._asset_document(job["asset_id"])
            self.store.update("ota_job", "job_id", job_id,
                              {"state": "ROLLED_BACK", "updated_at": self.now_iso(), "failure_reason": reason})
            return self._commit_asset(job["asset_id"], "OTA_ROLLED_BACK", before, actor=actor,
                                      reason=reason, subject_id=job_id)
