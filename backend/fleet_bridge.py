"""FleetBridge — joins the live simulation to the inventory source systems.

The inventory (backend/inventory) holds the fleet manager's and workforce
system's *records*; this bridge owns everything *physical*: which live
Robot is which asset, the heartbeats a robot sends, an OTA install actually
changing what a robot runs, a robot being stopped because its asset went
into maintenance, and an operator losing a certification the moment it is
revoked. See the design spec §5.

Lock order is always twin.lock → inventory store lock: every mutation goes
through mutate() (or runs in code that already holds twin.lock, like
DigitalTwin.add_robot and Simulator.tick), and the service calls
_on_change only after its transaction has committed.
"""
from __future__ import annotations

import random
from typing import Any, Callable, Dict, Iterable, List, Optional

from .embodiment import MobilityProfile
from .inventory import InventoryService, NotFound, reseed
from .inventory.catalog_data import CLASS_DEFAULT_MODELS
from .inventory.core import SYSTEM
from .inventory.workforce import VALID_CREDENTIAL_STATES
from .models import CONFIG, EventType, LogCategory, LogLevel, RobotStatus

FLOOR_SITE = "WH-01"
WARNING_ACTIONS = frozenset({"OTA_FAILED", "CREDENTIAL_REVOKED"})
LIFECYCLE_ACTIONS = frozenset({"STATUS_CHANGED", "DECOMMISSIONED", "WORK_ORDER_OPENED", "WORK_ORDER_CLOSED"})


def credential_scopes(worker: Dict[str, Any]) -> Dict[str, Dict[str, List[str]]]:
    """{code: {"equipment": [...], "site": [...]}} over a worker record's valid
    credentials. An empty list means unrestricted in that dimension, so two
    credentials with one code merge to the wider scope."""
    scopes: Dict[str, Dict[str, List[str]]] = {}
    for credential in worker["credentials"]:
        if credential["validity"] not in VALID_CREDENTIAL_STATES:
            continue
        fresh = {"equipment": list(credential["equipment_scope"]), "site": list(credential["site_scope"])}
        entry = scopes.get(credential["code"])
        if entry is None:
            scopes[credential["code"]] = fresh
            continue
        for key, values in fresh.items():
            if not entry[key] or not values:
                entry[key] = []
            else:
                entry[key] += [value for value in values if value not in entry[key]]
    return scopes


class FleetBridge:
    def __init__(self, twin: Any, service: InventoryService) -> None:
        self.twin = twin
        self.service = service
        self._ota_ticks: Dict[str, int] = {}
        self._battery_base: Dict[str, Dict[str, float]] = {}
        service.subscribe(self._on_change)

    # ---- mutations ------------------------------------------------------ #
    def mutate(self, action: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Run an inventory action under twin.lock (the only safe lock order)."""
        with self.twin.lock:
            return action(*args, **kwargs)

    def reseed(self, demo: bool = True) -> None:
        with self.twin.lock:
            reseed(self.service, demo=demo)
            self._ota_ticks.clear()
            self._battery_base.clear()

    # ---- embodiment ------------------------------------------------------ #
    def model_profile(self, model_code: str) -> MobilityProfile:
        """The body a robot of catalog model `model_code` has (spec §5.1)."""
        model = self.service.get_model(model_code)
        return MobilityProfile.from_model(model["spec"], model["embodiment_class"])

    def floor_profile(self, model_code: str) -> Optional[MobilityProfile]:
        """The profile a robot of `model_code` moves by on this twin's floor:
        None on classic, where every robot keeps today's behaviour."""
        if self.twin.warehouse.layout_name == "classic":
            return None
        return self.model_profile(model_code)

    # ---- binding -------------------------------------------------------- #
    def robot_for_asset(self, asset_id: Optional[str]) -> Optional[Any]:
        if not asset_id:
            return None
        for robot in self.twin.robots.values():
            if robot.asset_id == asset_id:
                return robot
        return None

    def spare_floor_asset(self, model_code: str, exclude: Iterable[str] = ()) -> Optional[str]:
        """An IN_SERVICE floor asset of `model_code` that no live robot is bound to
        (lowest id first), or None — so a robot whose own asset is unusable takes
        over an orphaned one instead of commissioning yet another."""
        held = {robot.asset_id for robot in self.twin.robots.values() if robot.asset_id}
        skip = held | set(exclude)
        for summary in self.service.list_robots(site=FLOOR_SITE, status="IN_SERVICE"):
            if summary["model_code"] == model_code and summary["asset_id"] not in skip:
                return summary["asset_id"]
        return None

    def bind_new_robot(self, robot: Any, model_code: Optional[str] = None,
                       asset_id: Optional[str] = None, adopt: bool = True) -> None:
        """Attach `robot` to its fleet-manager asset, commissioning one if
        needed. `adopt=True` (a new robot) takes the running versions from the
        asset's last report; `adopt=False` (a restored robot) keeps its own."""
        service = self.service
        if asset_id and service.has_asset(asset_id):
            record = service.get_robot(asset_id)
            if record["lifecycle_status"] == "DECOMMISSIONED":
                raise ValueError(f"Asset {asset_id} is decommissioned")
            asset_class = record["model"]["embodiment_class"]
            if asset_class != robot.robot_class:
                raise ValueError(f"Asset {asset_id} is a {asset_class}, not a {robot.robot_class}")
            bound = self.robot_for_asset(asset_id)
            if bound is not None and bound is not robot:
                raise ValueError(f"Asset {asset_id} is already on the floor as {bound.name}")
        else:
            model = model_code or CLASS_DEFAULT_MODELS.get(robot.robot_class)
            if model is None:
                raise ValueError(f"No catalog model for robot class {robot.robot_class}")
            try:
                model_class = service.model_class(model)
            except NotFound as exc:
                raise ValueError(str(exc)) from None
            if model_class != robot.robot_class:
                raise ValueError(f"{model} is a {model_class}, not a {robot.robot_class}")
            release = service.release_by_version("ROBOT_SOFTWARE", model, robot.firmware_version)
            zone = self.twin.warehouse.zone_of_cell(robot.position)
            change = service.commission_robot(
                model, FLOOR_SITE, zone.key if zone else None, asset_id=asset_id or None,
                software_release_id=release["release_id"] if release else None, actor=SYSTEM,
            )
            record = service.get_robot(change["aggregate_id"])
        robot.asset_id = record["asset_id"]
        robot.mobility = self.floor_profile(record["model_code"])
        reported = record["reported"] or {}
        if adopt:
            robot.firmware_version = reported.get("software_version") or record["declared_software_version"]
            robot.ai_policy_version = reported.get("ai_policy_version") or record["declared_ai_policy_version"]
            robot.component_firmware = dict(reported.get("component_firmware") or {})
        battery = reported.get("battery")
        if battery is not None:
            self._battery_base[robot.asset_id] = {
                "soh": float(battery.get("soh_pct", 100.0)),
                "cycles": int(battery.get("cycle_count", 0)),
                "offset": robot.charging_sessions,
            }
        else:
            self._battery_base.pop(robot.asset_id, None)
        status = record["lifecycle_status"]
        if status != "IN_SERVICE" and robot.status != RobotStatus.STOPPED:
            robot.status_before_stop = robot.status
            robot.set_status(RobotStatus.STOPPED)
            robot.fleet_hold = status

    def bind_new_operator(self, operator: Any, worker_id: Optional[str] = None) -> None:
        """Attach `operator` to its workforce record, registering one (with a
        credential per certification) if needed."""
        service = self.service
        if worker_id and service.has_worker(worker_id):
            operator.worker_id = worker_id
        else:
            with service.transaction():
                change = service.register_worker(
                    operator.name, role_codes=[operator.role] if operator.role else ["WAREHOUSE_OPERATOR"],
                    site_codes=[FLOOR_SITE], worker_id=worker_id or None,
                    actor=SYSTEM,
                )
                operator.worker_id = change["aggregate_id"]
                for code in operator.certifications:
                    service.ensure_credential_definition(code)
                    service.issue_credential(operator.worker_id, code, actor=SYSTEM)
        self._sync_operator(operator, announce=False)

    def rebind_all(self) -> None:
        """Re-attach robots and operators restored by DigitalTwin.load_state."""
        for robot in list(self.twin.robots.values()):
            if robot.asset_id and self.service.has_asset(robot.asset_id):
                try:
                    self.bind_new_robot(robot, asset_id=robot.asset_id, adopt=False)
                except ValueError as exc:
                    self.twin.logger.warning(
                        LogCategory.FLEET,
                        f"{robot.name}: asset {robot.asset_id} can't be re-bound ({exc}) — commissioning a new one")
                    # Robots not yet re-bound still carry their saved asset_id, so none of those is "spare".
                    failed, robot.asset_id = robot.asset_id, None
                    model = self.service.get_robot(failed)["model_code"]
                    if self.service.model_class(model) != robot.robot_class:  # the id now names another class
                        model = CLASS_DEFAULT_MODELS.get(robot.robot_class)
                    spare = self.spare_floor_asset(model) if model else None
                    self.bind_new_robot(robot, asset_id=spare, adopt=False)
                continue
            missing, robot.asset_id = robot.asset_id, None
            self.bind_new_robot(robot, adopt=False)
            if missing:
                self.twin.logger.warning(
                    LogCategory.FLEET,
                    f"{robot.name}: asset {missing} is not in the inventory — commissioned {robot.asset_id}")
        for operator in list(self.twin.operators.values()):
            if operator.worker_id and self.service.has_worker(operator.worker_id):
                self.bind_new_operator(operator, worker_id=operator.worker_id)
                continue
            if operator.worker_id:
                self.twin.logger.warning(
                    LogCategory.FLEET,
                    f"{operator.name}: worker {operator.worker_id} is not in the inventory — registering a new record")
            operator.worker_id = None
            self.bind_new_operator(operator)

    def _sync_operator(self, operator: Any, announce: bool = True) -> bool:
        """Make operator.certifications equal the worker's valid credential
        codes, and operator.certification_scopes their equipment and site
        scopes. Returns True (and announces) only when the codes changed."""
        worker = self.service.get_worker(operator.worker_id)
        codes = worker["valid_credential_codes"]
        operator.certification_scopes = credential_scopes(worker)
        if codes == list(operator.certifications):
            return False
        previous = list(operator.certifications)
        operator.certifications = codes
        operator.touch()
        if announce:
            lost = [c for c in previous if c not in codes]
            gained = [c for c in codes if c not in previous]
            parts = ([f"lost {', '.join(lost)}"] if lost else []) + ([f"gained {', '.join(gained)}"] if gained else [])
            self.twin.events.emit(
                EventType.OPERATOR_CERTIFICATIONS_CHANGED,
                f"{operator.name} certifications {'; '.join(parts)} ({operator.worker_id})",
                category=LogCategory.FLEET,
                level=LogLevel.WARNING if lost else LogLevel.INFO,
                data={"operator_id": operator.id, "worker_id": operator.worker_id,
                      "certifications": codes, "lost": lost, "gained": gained},
            )
        return True

    # ---- inventory change listener -------------------------------------- #
    def _on_change(self, change: Dict[str, Any]) -> None:
        """Announce every inventory change on the twin's event bus, then apply
        its physical effect on the floor (runs after the change committed)."""
        action = change["action"]
        robot = (self.robot_for_asset(change["aggregate_id"])
                 if change["aggregate_type"] in ("ROBOT_ASSET", "ROBOT_OBSERVATION") else None)
        reason = f" ({change['reason']})" if change.get("reason") else ""
        self.twin.events.emit(
            EventType.OTA_JOB_UPDATED if action.startswith("OTA_") else EventType.INVENTORY_CHANGED,
            f"{change['aggregate_id']}: {action.replace('_', ' ').lower()}{reason}",
            category=LogCategory.FLEET,
            level=LogLevel.WARNING if action in WARNING_ACTIONS else LogLevel.INFO,
            robot_id=robot.id if robot else None,
            data={"seq": change["seq"], "aggregate_type": change["aggregate_type"],
                  "aggregate_id": change["aggregate_id"], "action": action,
                  "subject_id": change.get("subject_id")},
        )
        if change["aggregate_type"] == "ROBOT_ASSET":
            if robot is not None:
                # The body follows the asset's catalog model on every asset change.
                robot.mobility = self.floor_profile(change["after"]["model_code"])
                # UPDATING is derived from the jobs, never hand-set per job.
                installing = any(j["state"] == "INSTALLING" for j in change["after"]["active_ota_jobs"])
                if installing != robot.ota_installing:
                    robot.ota_installing = installing
                    self._report_robot(robot)  # the fleet sees UPDATING start and end at once
            if action in LIFECYCLE_ACTIONS and robot is not None:
                self._apply_lifecycle(robot, change["after"]["lifecycle_status"])
            if action == "COMPONENT_SWAPPED":
                new = next((c for c in change["after"]["components"]
                            if c["component_id"] == change["subject_id"]), None)
                if new is not None:
                    version = (self.service.get_release(new["firmware_release_id"])["version"]
                               if new["firmware_release_id"] else None)
                    self._set_component_firmware(change["aggregate_id"], robot, new["slot"], version)
            elif action == "OTA_ROLLED_BACK":
                job = self.service.get_ota_job(change["subject_id"])
                self._set_running_version(job["asset_id"], robot, job["kind"], job["slot"], job["from_version"])
            elif action == "OTA_FAILED":
                self._ota_ticks.pop(change["subject_id"], None)
        elif change["aggregate_type"] == "WORKER":
            for operator in list(self.twin.operators.values()):
                if operator.worker_id == change["aggregate_id"]:
                    self._sync_operator(operator)


    # ---- physical effects ----------------------------------------------- #
    def _apply_lifecycle(self, robot: Any, status: str) -> None:
        """A floor robot whose asset leaves IN_SERVICE is stopped; it is
        resumed on return — but only if the fleet was what stopped it."""
        if status == "IN_SERVICE":
            if robot.fleet_hold is not None:
                robot.fleet_hold = None
                if robot.status == RobotStatus.STOPPED:
                    self.twin.resume_robot(robot.id, reason="asset back in service")
            return
        if robot.fleet_hold is None and robot.status == RobotStatus.STOPPED:
            return  # stopped by someone else — leave it to them
        if robot.fleet_hold != status:
            robot.fleet_hold = status
            if robot.status != RobotStatus.STOPPED:
                self.twin.stop_robot(robot.id, reason=f"asset {status.lower().replace('_', ' ')}")

    def _set_component_firmware(self, asset_id: str, robot: Optional[Any], slot: str,
                                version: Optional[str]) -> None:
        if robot is not None:
            if version is None:
                robot.component_firmware.pop(slot, None)
            else:
                robot.component_firmware[slot] = version
            robot.touch()
            self._report_robot(robot)
            return
        reported = self.service.get_robot(asset_id)["reported"] or {}
        firmware = dict(reported.get("component_firmware") or {})
        if version is None:
            firmware.pop(slot, None)
        else:
            firmware[slot] = version
        self.service.report_state(asset_id, {"component_firmware": firmware})

    def _set_running_version(self, asset_id: str, robot: Optional[Any], kind: str,
                             slot: Optional[str], version: Optional[str]) -> None:
        """What an install (or rollback) physically does: change what runs."""
        if kind == "COMPONENT_FIRMWARE":
            self._set_component_firmware(asset_id, robot, slot, version)
            return
        if robot is not None:
            if kind == "ROBOT_SOFTWARE":
                robot.firmware_version = version
            else:
                robot.ai_policy_version = version
            robot.touch()
            self._report_robot(robot)
            return
        if kind == "ROBOT_SOFTWARE":
            self.service.report_state(asset_id, {"software_version": version,
                                                 "os_version": self.service.os_version_for(version)})
        else:
            self.service.report_state(asset_id, {"ai_policy_version": version})

    # ---- runtime (Simulator.tick, twin.lock held) ----------------------- #
    def on_tick(self, tick: int) -> None:
        self._advance_ota()
        if tick % max(1, int(CONFIG["FLEET_HEARTBEAT_EVERY_TICKS"])) == 0:
            self.heartbeat()
        if tick % max(1, int(CONFIG["SHIFT_CHECK_EVERY_TICKS"])) == 0:
            self.sync_all_operators()

    def heartbeat(self) -> None:
        """Every floor robot reports what it is running; the other sites'
        online robots check in (reported_at only). The robot's hold is
        re-derived from its asset's lifecycle here too, so a missed change
        event (or a hold restored by load_state) can't outlive the next
        heartbeat."""
        bound = set()
        for robot in list(self.twin.robots.values()):
            if robot.asset_id and self.service.has_asset(robot.asset_id):
                bound.add(robot.asset_id)
                self._apply_lifecycle(robot, self.service.lifecycle_status(robot.asset_id))
                self._report_robot(robot)
        # Only the other sites check in on their own; an unbound floor asset has nothing
        # reporting for it, so its report goes stale (and the fleet shows it).
        self.service.touch_remote_reports(exclude=bound, exclude_sites=(FLOOR_SITE,))

    def _report_robot(self, robot: Any) -> None:
        if robot.status == RobotStatus.ERROR:
            health = "FAULT"
        elif robot.maintenance_alerted:
            health = "DEGRADED"
        else:
            health = "OK"
        zone = self.twin.warehouse.zone_of_cell(robot.position)
        reported: Dict[str, Any] = {
            "software_version": robot.firmware_version,
            "os_version": self.service.os_version_for(robot.firmware_version),
            "ai_policy_version": robot.ai_policy_version,
            "component_firmware": dict(robot.component_firmware),
            "health_state": health,
            "connectivity": "ONLINE",
            "operational_mode": "UPDATING" if robot.ota_installing else robot.status.value,
            "zone": zone.key if zone else None,
        }
        base = self._battery_base.get(robot.asset_id)
        if base is not None:
            extra = max(0, robot.charging_sessions - base["offset"])
            reported["battery"] = {
                "soc_pct": round(robot.battery, 1),
                "cycle_count": int(base["cycles"] + extra),
                "soh_pct": round(max(0.0, base["soh"] - 0.02 * extra), 2),
            }
        self.service.report_state(robot.asset_id, reported)

    def _advance_ota(self) -> None:
        """STAGED → DOWNLOADING at once; DOWNLOADING for OTA_DOWNLOAD_TICKS
        (and until a floor robot is idle); INSTALLING for OTA_INSTALL_TICKS,
        during which the robot takes no work; then the new version runs and
        the job is REPORTED (or FAILED, per OTA_FAILURE_RISK)."""
        for job in self.service.active_ota_jobs():
            job_id, state = job["job_id"], job["state"]
            robot = self.robot_for_asset(job["asset_id"])
            if state == "STAGED":
                self.service.transition_ota(job_id, "DOWNLOADING")
                self._ota_ticks[job_id] = 0
                continue
            ticks = self._ota_ticks.get(job_id, 0) + 1
            self._ota_ticks[job_id] = ticks
            if state == "DOWNLOADING":
                if ticks < CONFIG["OTA_DOWNLOAD_TICKS"] or (robot is not None and robot.current_task is not None):
                    continue
                self.service.transition_ota(job_id, "INSTALLING")
                self._ota_ticks[job_id] = 0
            elif state == "INSTALLING":
                if ticks < CONFIG["OTA_INSTALL_TICKS"]:
                    continue
                self._ota_ticks.pop(job_id, None)
                if random.random() < CONFIG.get("OTA_FAILURE_RISK", 0.0):
                    self.service.transition_ota(job_id, "FAILED",
                                                failure_reason="install failed: image signature verification error")
                    continue
                self._set_running_version(job["asset_id"], robot, job["kind"], job["slot"], job["version"])
                self.service.transition_ota(job_id, "REPORTED")
        # A robot is UPDATING exactly while one of its jobs is INSTALLING. Re-derived from a
        # fresh query, so it also repairs a robot restored by load_state mid-install.
        installing = {j["asset_id"] for j in self.service.active_ota_jobs() if j["state"] == "INSTALLING"}
        for robot in self.twin.robots.values():
            if robot.asset_id:
                robot.ota_installing = robot.asset_id in installing

    def sync_all_operators(self) -> None:
        """Catch credentials that expired (or became effective) with time."""
        for operator in list(self.twin.operators.values()):
            if operator.worker_id and self.service.has_worker(operator.worker_id):
                self._sync_operator(operator)
