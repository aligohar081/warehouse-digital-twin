"""Robot assets: commissioning, lifecycle, admin edits, robot-reported
state, and the read models (with derived flags) the API and dashboard use.

Declared state (what the fleet manager believes — robot_asset / component
rows) and reported state (what the robot itself last said — reported_state)
are deliberately separate: mismatches between them are exactly what PWA's
passport checks look for.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any, Dict, Iterable, List, Mapping, Optional

from .core import SYSTEM, sha256_text
from .documents import iso, parse_iso
from .errors import Conflict

LIFECYCLE_STATUSES = ("COMMISSIONING", "IN_SERVICE", "MAINTENANCE", "OUT_OF_SERVICE", "DECOMMISSIONED")
LIFECYCLE_TRANSITIONS = {
    "COMMISSIONING": {"IN_SERVICE"},
    "IN_SERVICE": {"MAINTENANCE", "OUT_OF_SERVICE"},
    "MAINTENANCE": {"IN_SERVICE", "OUT_OF_SERVICE"},
    "OUT_OF_SERVICE": {"IN_SERVICE", "MAINTENANCE"},
    "DECOMMISSIONED": set(),
}
ADMIN_EDITABLE_FIELDS = frozenset({
    "asset_tag", "fleet_id", "site_code", "home_zone", "hw_revision", "config_hash", "safety_policy_hash",
})
SIGNIFICANT_REPORT_FIELDS = (
    "software_version", "os_version", "config_hash", "safety_policy_hash", "ai_policy_version",
    "component_firmware", "health_state", "connectivity",
)
REFRESH_ONLY_REPORT_FIELDS = ("operational_mode", "zone")
BATTERY_SIGNIFICANT_KEYS = ("soh_pct", "cycle_count")
HEALTH_STATES = ("OK", "DEGRADED", "FAULT", "UNKNOWN")
CONNECTIVITY_STATES = ("ONLINE", "OFFLINE", "INTERMITTENT")
CALIBRATION_RANK = {"VALID": 0, "DUE_SOON": 1, "MISSING": 2, "EXPIRED": 3, "FAILED": 4}
OTA_ACTIVE_STATES = ("STAGED", "DOWNLOADING", "INSTALLING", "REPORTED")


class FleetMixin:
    # ---- lookups & documents ------------------------------------------ #
    def has_asset(self, asset_id: Optional[str]) -> bool:
        return bool(asset_id) and self.store.exists("robot_asset", "asset_id", asset_id)

    def _asset(self, asset_id: str) -> Dict[str, Any]:
        return self._require("robot_asset", "asset_id", asset_id, "Robot asset")

    def lifecycle_status(self, asset_id: str) -> str:
        return self._asset(asset_id)["lifecycle_status"]

    def _latest_calibration(self, component_id: str) -> Optional[Dict[str, Any]]:
        rows = self.store.select("calibration_record", "component_id = ?", (component_id,),
                                 order="performed_at DESC, rowid DESC", limit=1)
        return rows[0] if rows else None

    def _open_work_orders(self, asset_id: str) -> List[Dict[str, Any]]:
        return self.store.select("work_order", "asset_id = ? AND status = 'OPEN'", (asset_id,))

    def _active_ota_rows(self, asset_id: str) -> List[Dict[str, Any]]:
        marks = ", ".join("?" for _ in OTA_ACTIVE_STATES)
        return self.store.select("ota_job", f"asset_id = ? AND state IN ({marks})",
                                 (asset_id, *OTA_ACTIVE_STATES))

    def _asset_document(self, asset_id: str) -> Dict[str, Any]:
        """The ROBOT_ASSET aggregate as stored in change_log `after` snapshots."""
        asset = self._asset(asset_id)
        components = self.store.select("component", "asset_id = ? AND status != 'REMOVED'", (asset_id,))
        for component in components:
            component["latest_calibration"] = self._latest_calibration(component["component_id"])
        return {
            **asset,
            "components": components,
            "open_work_orders": self._open_work_orders(asset_id),
            "active_ota_jobs": self._active_ota_rows(asset_id),
        }

    def _commit_asset(self, asset_id: str, action: str, before: Optional[dict], actor: Any = None,
                      reason: Optional[str] = None, subject_id: Optional[str] = None) -> Dict[str, Any]:
        revision = self._asset(asset_id)["revision"] + 1
        self.store.update("robot_asset", "asset_id", asset_id, {"revision": revision, "updated_at": self.now_iso()})
        return self._record(aggregate_type="ROBOT_ASSET", aggregate_id=asset_id, revision=revision,
                            action=action, before=before, after=self._asset_document(asset_id),
                            actor=actor, reason=reason, subject_id=subject_id)

    def _new_serial(self, manufacturer: Mapping[str, Any], code: str, component: bool = False) -> str:
        """The next generated serial that no existing asset (or, for a part, component)
        carries — an explicitly supplied serial may already have claimed it."""
        short = code.split("-", 1)[-1]
        while True:
            number = self.store.bump_counter(f"serial:{manufacturer['manufacturer_id']}", start=1001)
            serial = f"{manufacturer['serial_prefix']}-{short}-{self.now().year}-{number:05d}"
            if component:
                taken = self.store.exists("component", "serial", serial)
            else:
                taken = self.store.count("robot_asset", "manufacturer_id = ? AND serial_number = ?",
                                         (manufacturer["manufacturer_id"], serial)) > 0
            if not taken:
                return serial

    def _install_component(self, asset_id: str, slot: str, part_number: str,
                           hw_revision: Optional[str]) -> Dict[str, Any]:
        part = self.get_part(part_number)
        hw = hw_revision or part["hw_revisions"][-1]
        if hw not in part["hw_revisions"]:
            raise ValueError(f"{part_number} has no hardware revision {hw!r} (known: {part['hw_revisions']})")
        release = self.best_firmware(part_number, hw) if part["firmware_capable"] else None
        manufacturer = self._require("manufacturer", "manufacturer_id", part["manufacturer_id"], "Manufacturer")
        row = {
            "component_id": self.store.next_id("CMP-", "component", "component_id", 7),
            "asset_id": asset_id, "slot": slot, "part_number": part_number,
            "serial": self._new_serial(manufacturer, part_number, component=True), "hw_revision": hw,
            "firmware_release_id": release["release_id"] if release else None,
            "installed_at": self.now_iso(), "removed_at": None, "status": "INSTALLED",
        }
        self.store.insert("component", row)
        return {**row, "firmware_version": release["version"] if release else None}

    def _insert_calibration(self, component_id: str, part: Mapping[str, Any], result: str,
                            performed_by: Optional[str], method: str, certificate_ref: Optional[str]) -> str:
        now = self.now()
        calibration_id = self.store.next_id("CAL-", "calibration_record", "calibration_id", 7)
        valid_until = iso(now + timedelta(days=part["calibration_interval_days"])) if result == "PASS" else None
        self.store.insert("calibration_record", {
            "calibration_id": calibration_id, "component_id": component_id, "performed_at": iso(now),
            "performed_by": performed_by, "method": method, "result": result,
            "valid_until": valid_until, "certificate_ref": certificate_ref,
        })
        return calibration_id

    def _ota_view(self, job: Mapping[str, Any]) -> Dict[str, Any]:
        release = self.get_release(job["release_id"])
        slot = None
        if job["component_id"]:
            slot = self._require("component", "component_id", job["component_id"], "Component")["slot"]
        return {**job, "kind": release["kind"], "version": release["version"],
                "target_code": release["target_code"], "slot": slot}

    # ---- commissioning & lifecycle ------------------------------------ #
    def _commission_release(self, model_code: str, hw: str, software_release_id: Optional[str]) -> Dict[str, Any]:
        if software_release_id:
            release = self.get_release(software_release_id)
            if release["kind"] != "ROBOT_SOFTWARE" or release["target_code"] != model_code:
                raise ValueError(f"{software_release_id} is not a {model_code} robot-software release")
            if release["status"] == "RECALLED":
                raise Conflict(f"{software_release_id} has been recalled")
        else:
            release = self.current_release("ROBOT_SOFTWARE", model_code)
            if release is None:
                raise ValueError(f"{model_code} has no current robot-software release")
        if release["min_hw_rev"] and hw < release["min_hw_rev"]:
            raise Conflict(f"{release['release_id']} requires hardware revision {release['min_hw_rev']} or later (asset: {hw})")
        return release

    def commission_robot(self, model_code: str, site_code: str, home_zone: Optional[str] = None, *,
                         asset_id: Optional[str] = None, serial_number: Optional[str] = None,
                         asset_tag: Optional[str] = None, hw_revision: Optional[str] = None,
                         software_release_id: Optional[str] = None, fleet_id: Optional[str] = None,
                         lifecycle_status: str = "IN_SERVICE", actor: Any = None) -> Dict[str, Any]:
        if lifecycle_status not in ("IN_SERVICE", "COMMISSIONING"):
            raise ValueError("A robot is commissioned as IN_SERVICE or COMMISSIONING")
        site_code = str(site_code or "").strip()
        if not site_code:
            raise ValueError("site_code is required")
        with self._tx():
            model = self.get_model(model_code)
            manufacturer = self._require("manufacturer", "manufacturer_id", model["manufacturer_id"], "Manufacturer")
            hw = hw_revision or model["hw_revisions"][-1]
            if hw not in model["hw_revisions"]:
                raise ValueError(f"{model_code} has no hardware revision {hw!r} (known: {model['hw_revisions']})")
            release = self._commission_release(model_code, hw, software_release_id)
            ai_release = self.current_release("AI_POLICY_MODEL", model_code)
            if asset_id:
                if self.has_asset(asset_id):
                    raise Conflict(f"Robot asset '{asset_id}' already exists")
            else:
                asset_id = self.store.next_id("AST-", "robot_asset", "asset_id", 6)
            serial = serial_number or self._new_serial(manufacturer, model_code)
            if self.store.count("robot_asset", "manufacturer_id = ? AND serial_number = ?",
                                (manufacturer["manufacturer_id"], serial)):
                raise Conflict(f"{manufacturer['name']} serial {serial} is already registered")
            tag = asset_tag or f"AT-{asset_id.split('-', 1)[-1]}"
            if self.store.exists("robot_asset", "asset_tag", tag):
                raise Conflict(f"Asset tag {tag} is already in use")
            now = self.now_iso()
            config_hash = sha256_text(f"{model_code}:config:baseline-1")
            safety_hash = sha256_text(f"{model_code}:safety:baseline-1")
            self.store.insert("robot_asset", {
                "asset_id": asset_id, "asset_tag": tag, "serial_number": serial,
                "manufacturer_id": manufacturer["manufacturer_id"], "model_code": model_code,
                "hw_revision": hw, "fleet_id": fleet_id, "site_code": site_code, "home_zone": home_zone,
                "lifecycle_status": lifecycle_status, "commissioned_at": now, "decommissioned_at": None,
                "software_release_id": release["release_id"], "config_hash": config_hash,
                "safety_policy_hash": safety_hash,
                "ai_policy_release_id": ai_release["release_id"] if ai_release else None,
                "revision": 0, "updated_at": now,
            })
            firmware: Dict[str, str] = {}
            for slot in model["component_layout"]:
                component = self._install_component(asset_id, slot["slot"], slot["part_number"], None)
                if component["firmware_version"]:
                    firmware[slot["slot"]] = component["firmware_version"]
                part = self.get_part(slot["part_number"])
                if part["calibration_interval_days"]:
                    self._insert_calibration(component["component_id"], part, "PASS", None, "FACTORY", None)
            change = self._commit_asset(asset_id, "COMMISSIONED", None, actor=actor)
            self.store.insert("reported_state", {
                "asset_id": asset_id, "software_version": release["version"],
                "os_version": self.os_version_for(release["version"]),
                "config_hash": config_hash, "safety_policy_hash": safety_hash,
                "ai_policy_version": ai_release["version"] if ai_release else None,
                "component_firmware": firmware, "health_state": "OK", "connectivity": "ONLINE",
                "operational_mode": "IDLE", "zone": home_zone,
                "battery": ({"soc_pct": 100.0, "soh_pct": 100.0, "cycle_count": 0}
                            if model["spec"].get("battery") else None),
                "reported_at": now, "observation_seq": 1,
            })
            self._record(aggregate_type="ROBOT_OBSERVATION", aggregate_id=asset_id, revision=1,
                         action="STATE_REPORTED", before=None,
                         after=self.store.get("reported_state", "asset_id", asset_id), actor=SYSTEM)
            return change

    def set_lifecycle_status(self, asset_id: str, status: str, reason: str, actor: Any = None) -> Dict[str, Any]:
        status = str(status or "").upper()
        if status not in LIFECYCLE_STATUSES:
            raise ValueError(f"Unknown lifecycle status {status!r} (known: {list(LIFECYCLE_STATUSES)})")
        if status == "DECOMMISSIONED":
            raise ValueError("Use decommission to retire an asset")
        reason = self._reason(reason)
        with self._tx():
            asset = self._asset(asset_id)
            current = asset["lifecycle_status"]
            if status not in LIFECYCLE_TRANSITIONS[current]:
                raise Conflict(f"{asset_id} cannot move from {current} to {status}")
            if status == "IN_SERVICE" and self._open_work_orders(asset_id):
                raise Conflict(f"{asset_id} has open work orders — close them first")
            before = self._asset_document(asset_id)
            self.store.update("robot_asset", "asset_id", asset_id, {"lifecycle_status": status})
            return self._commit_asset(asset_id, "STATUS_CHANGED", before, actor=actor, reason=reason)

    def decommission(self, asset_id: str, reason: str, actor: Any = None) -> Dict[str, Any]:
        reason = self._reason(reason)
        with self._tx():
            asset = self._asset(asset_id)
            if asset["lifecycle_status"] == "DECOMMISSIONED":
                raise Conflict(f"{asset_id} is already decommissioned")
            before = self._asset_document(asset_id)
            now = self.now_iso()
            for job in self._active_ota_rows(asset_id):
                self.store.update("ota_job", "job_id", job["job_id"],
                                  {"state": "FAILED", "updated_at": now, "failure_reason": "asset decommissioned"})
            for work_order in self._open_work_orders(asset_id):
                self.store.update("work_order", "wo_id", work_order["wo_id"],
                                  {"status": "CLOSED", "closed_at": now, "resolution": "asset decommissioned"})
            self.store.update("robot_asset", "asset_id", asset_id,
                              {"lifecycle_status": "DECOMMISSIONED", "decommissioned_at": now})
            return self._commit_asset(asset_id, "DECOMMISSIONED", before, actor=actor, reason=reason)

    def admin_edit(self, asset_id: str, fields: Mapping[str, Any], reason: str, actor: Any = None) -> Dict[str, Any]:
        unknown = sorted(set(fields) - ADMIN_EDITABLE_FIELDS)
        if unknown:
            raise ValueError(f"Field(s) not editable: {unknown} (editable: {sorted(ADMIN_EDITABLE_FIELDS)})")
        reason = self._reason(reason)
        with self._tx():
            asset = self._asset(asset_id)
            if asset["lifecycle_status"] == "DECOMMISSIONED":
                raise Conflict(f"{asset_id} is decommissioned")
            changes = {key: value for key, value in fields.items() if asset[key] != value}
            if not changes:
                raise ValueError("No changes to apply")
            if "hw_revision" in changes:
                revisions = self.get_model(asset["model_code"])["hw_revisions"]
                if changes["hw_revision"] not in revisions:
                    raise ValueError(f"{asset['model_code']} has no hardware revision {changes['hw_revision']!r}")
            if "asset_tag" in changes:
                if not str(changes["asset_tag"] or "").strip():
                    raise ValueError("asset_tag cannot be empty")
                if self.store.exists("robot_asset", "asset_tag", changes["asset_tag"]):
                    raise Conflict(f"Asset tag {changes['asset_tag']} is already in use")
            if "site_code" in changes and not str(changes["site_code"] or "").strip():
                raise ValueError("site_code cannot be empty")
            before = self._asset_document(asset_id)
            self.store.update("robot_asset", "asset_id", asset_id, changes)
            return self._commit_asset(asset_id, "ADMIN_EDIT", before, actor=actor, reason=reason)

    # ---- robot-reported state ----------------------------------------- #
    @staticmethod
    def _check_report_shape(reported: Mapping[str, Any]) -> None:
        """Validate the payload shape of a robot heartbeat before storing."""
        if "component_firmware" in reported:
            cf = reported["component_firmware"]
            if cf is None:
                raise ValueError("component_firmware: None is not allowed (must be a dict of str → str, or omit the field)")
            if not isinstance(cf, dict):
                raise ValueError(f"component_firmware: must be a dict, got {type(cf).__name__}")
            for key, value in cf.items():
                if not isinstance(key, str):
                    raise ValueError(f"component_firmware: all keys must be str, got {type(key).__name__} for key {key!r}")
                if not isinstance(value, str):
                    raise ValueError(f"component_firmware: all values must be str, got {type(value).__name__} for value {value!r}")
        if "battery" in reported:
            battery = reported["battery"]
            if battery is not None:
                if not isinstance(battery, dict):
                    raise ValueError(f"battery: must be a dict or None, got {type(battery).__name__}")
                for key, value in battery.items():
                    if not isinstance(key, str):
                        raise ValueError(f"battery: all keys must be str, got {type(key).__name__} for key {key!r}")
                    if isinstance(value, bool):
                        raise ValueError(f"battery: bool is not allowed (use int or float), got bool for key {key!r}")
                    if not isinstance(value, (int, float)):
                        raise ValueError(f"battery: all values must be int or float, got {type(value).__name__} for key {key!r}")
        for field in ("software_version", "os_version", "config_hash", "safety_policy_hash", "ai_policy_version", "operational_mode", "zone"):
            if field in reported:
                value = reported[field]
                if value is not None and not isinstance(value, str):
                    raise ValueError(f"{field}: must be str or None, got {type(value).__name__}")

    def report_state(self, asset_id: str, reported: Mapping[str, Any], actor: Any = None) -> Optional[Dict[str, Any]]:
        """A robot heartbeat. Always refreshes reported_at; appends a
        ROBOT_OBSERVATION change only when a significant field changed."""
        allowed = set(SIGNIFICANT_REPORT_FIELDS) | set(REFRESH_ONLY_REPORT_FIELDS) | {"battery"}
        unknown = sorted(set(reported) - allowed)
        if unknown:
            raise ValueError(f"Unknown reported field(s): {unknown}")
        if "health_state" in reported and reported["health_state"] not in HEALTH_STATES:
            raise ValueError(f"Unknown health_state {reported['health_state']!r} (known: {list(HEALTH_STATES)})")
        if "connectivity" in reported and reported["connectivity"] not in CONNECTIVITY_STATES:
            raise ValueError(f"Unknown connectivity {reported['connectivity']!r} (known: {list(CONNECTIVITY_STATES)})")
        self._check_report_shape(reported)
        with self._tx():
            self._asset(asset_id)
            current = self._require("reported_state", "asset_id", asset_id, "Reported state")
            updates = {key: reported[key] for key in REFRESH_ONLY_REPORT_FIELDS if key in reported}
            updates["reported_at"] = self.now_iso()
            significant = {key: reported[key] for key in SIGNIFICANT_REPORT_FIELDS
                           if key in reported and reported[key] != current[key]}
            if reported.get("battery") is not None:
                old_battery = current["battery"] or {}
                merged = {**old_battery, **reported["battery"]}
                updates["battery"] = merged
                if any(merged.get(key) != old_battery.get(key) for key in BATTERY_SIGNIFICANT_KEYS):
                    significant["battery"] = merged
            if not significant:
                self.store.update("reported_state", "asset_id", asset_id, updates)
                return None
            seq = current["observation_seq"] + 1
            self.store.update("reported_state", "asset_id", asset_id, {**updates, **significant, "observation_seq": seq})
            return self._record(aggregate_type="ROBOT_OBSERVATION", aggregate_id=asset_id, revision=seq,
                                action="STATE_REPORTED", before=current,
                                after=self.store.get("reported_state", "asset_id", asset_id),
                                actor=actor or SYSTEM)

    def touch_remote_reports(self, exclude: Iterable[str] = (), exclude_sites: Iterable[str] = ()) -> int:
        """Refresh reported_at for every ONLINE, non-decommissioned asset not in
        `exclude` (or at a site in `exclude_sites`) — the robots at other sites
        checking in. Assets on the twin's own floor report for themselves."""
        excluded = set(exclude)
        sites = list(dict.fromkeys(exclude_sites))
        site_filter = f" AND site_code NOT IN ({', '.join('?' for _ in sites)})" if sites else ""
        now = self.now_iso()
        with self._tx():
            rows = self.store.select(
                "reported_state",
                "connectivity = 'ONLINE' AND asset_id IN "
                f"(SELECT asset_id FROM robot_asset WHERE lifecycle_status != 'DECOMMISSIONED'{site_filter})",
                sites)
            touched = 0
            for row in rows:
                if row["asset_id"] in excluded:
                    continue
                self.store.update("reported_state", "asset_id", row["asset_id"], {"reported_at": now})
                touched += 1
            return touched

    # ---- read models --------------------------------------------------- #
    def _calibration_status(self, part: Mapping[str, Any], latest: Optional[Mapping[str, Any]], now) -> Optional[str]:
        if not part["calibration_interval_days"]:
            return None
        if latest is None:
            return "MISSING"
        if latest["result"] == "FAIL":
            return "FAILED"
        until = parse_iso(latest["valid_until"])
        if until <= now:
            return "EXPIRED"
        if until - now <= timedelta(days=self.setting("CALIBRATION_DUE_SOON_DAYS")):
            return "DUE_SOON"
        return "VALID"

    def _component_view(self, component: Mapping[str, Any], reported: Optional[Mapping[str, Any]], now) -> Dict[str, Any]:
        part = self.get_part(component["part_number"])
        declared = self.get_release(component["firmware_release_id"])["version"] if component["firmware_release_id"] else None
        reported_firmware = ((reported or {}).get("component_firmware") or {}).get(component["slot"])
        history = self.store.select("calibration_record", "component_id = ?", (component["component_id"],),
                                    order="performed_at DESC, rowid DESC", limit=5)
        view = {key: value for key, value in component.items() if key != "latest_calibration"}
        view.update({
            "part_name": part["name"], "component_type": part["component_type"],
            "declared_firmware_version": declared, "reported_firmware_version": reported_firmware,
            "calibration_interval_days": part["calibration_interval_days"],
            "calibration_status": self._calibration_status(part, component.get("latest_calibration"), now),
            "calibrations": history,
        })
        return view

    def _robot_flags(self, record: Mapping[str, Any], now) -> Dict[str, Any]:
        reported = record["reported"] or {}
        components = record["components"]
        statuses = [c["calibration_status"] for c in components if c["calibration_status"]]
        running = reported.get("software_version")
        running_release = self.release_by_version("ROBOT_SOFTWARE", record["model_code"], running)
        reported_at = parse_iso(reported.get("reported_at"))
        return {
            "software_mismatch": bool(reported) and running != record["declared_software_version"],
            "ai_policy_mismatch": (record["declared_ai_policy_version"] is not None
                                   and reported.get("ai_policy_version") != record["declared_ai_policy_version"]),
            "component_firmware_mismatches": [
                {"slot": c["slot"], "declared": c["declared_firmware_version"], "reported": c["reported_firmware_version"]}
                for c in components
                if c["declared_firmware_version"] and c["reported_firmware_version"] != c["declared_firmware_version"]
            ],
            "calibration_worst": max(statuses, key=CALIBRATION_RANK.__getitem__) if statuses else None,
            "calibration_issues": [{"slot": c["slot"], "status": c["calibration_status"]}
                                   for c in components if c["calibration_status"] not in (None, "VALID")],
            "report_stale": reported_at is None
                            or (now - reported_at).total_seconds() > self.setting("REPORT_STALE_SECONDS"),
            "running_recalled_release": (record["declared_software_status"] == "RECALLED"
                                         or (running_release is not None and running_release["status"] == "RECALLED")),
            "running_unknown_software": bool(running) and running_release is None,
            "active_ota_job": next((job for job in record["ota_jobs"] if job["state"] in OTA_ACTIVE_STATES), None),
        }

    def get_robot(self, asset_id: str) -> Dict[str, Any]:
        document = self._asset_document(asset_id)
        now = self.now()
        model = self.get_model(document["model_code"])
        reported = self.store.get("reported_state", "asset_id", asset_id)
        declared_software = self.get_release(document["software_release_id"])
        declared_ai = self.get_release(document["ai_policy_release_id"]) if document["ai_policy_release_id"] else None
        record = {key: value for key, value in document.items()
                  if key not in ("components", "open_work_orders", "active_ota_jobs")}
        record.update({
            "manufacturer_name": model["manufacturer_name"],
            "model": model,
            "declared_software_version": declared_software["version"],
            "declared_software_status": declared_software["status"],
            "declared_ai_policy_version": declared_ai["version"] if declared_ai else None,
            "reported": reported,
            "components": [self._component_view(c, reported, now) for c in document["components"]],
            "work_orders": self.store.select("work_order", "asset_id = ?", (asset_id,), order="opened_at DESC, rowid DESC"),
            "ota_jobs": [self._ota_view(job) for job in
                         self.store.select("ota_job", "asset_id = ?", (asset_id,), order="rowid DESC", limit=20)],
        })
        record["flags"] = self._robot_flags(record, now)
        return record

    @staticmethod
    def _robot_summary(record: Mapping[str, Any]) -> Dict[str, Any]:
        reported = record["reported"] or {}
        return {
            "asset_id": record["asset_id"], "asset_tag": record["asset_tag"],
            "serial_number": record["serial_number"], "model_code": record["model_code"],
            "model_name": record["model"]["name"], "embodiment_class": record["model"]["embodiment_class"],
            "manufacturer_name": record["manufacturer_name"], "site_code": record["site_code"],
            "home_zone": record["home_zone"], "lifecycle_status": record["lifecycle_status"],
            "hw_revision": record["hw_revision"], "revision": record["revision"],
            "declared_software_version": record["declared_software_version"],
            "reported_software_version": reported.get("software_version"),
            "connectivity": reported.get("connectivity"), "health_state": reported.get("health_state"),
            "reported_at": reported.get("reported_at"), "flags": record["flags"],
        }

    def list_robots(self, site: Optional[str] = None, status: Optional[str] = None) -> List[Dict[str, Any]]:
        where, params = [], []
        if site:
            where.append("site_code = ?")
            params.append(site)
        if status:
            where.append("lifecycle_status = ?")
            params.append(str(status).upper())
        rows = self.store.select("robot_asset", " AND ".join(where), params, order="asset_id")
        return [self._robot_summary(self.get_robot(row["asset_id"])) for row in rows]

    def robot_history(self, asset_id: str, limit: int = 200) -> List[Dict[str, Any]]:
        self._asset(asset_id)
        return self.history([asset_id], limit)
