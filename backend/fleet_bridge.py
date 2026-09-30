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

from typing import Any, Callable, Dict, Optional

from .inventory import InventoryService, NotFound, reseed
from .inventory.catalog_data import CLASS_DEFAULT_MODELS
from .inventory.core import SYSTEM
from .models import EventType, LogCategory, LogLevel, RobotStatus

FLOOR_SITE = "WH-01"
WARNING_ACTIONS = frozenset({"OTA_FAILED", "CREDENTIAL_REVOKED"})


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

    # ---- binding -------------------------------------------------------- #
    def robot_for_asset(self, asset_id: Optional[str]) -> Optional[Any]:
        if not asset_id:
            return None
        for robot in self.twin.robots.values():
            if robot.asset_id == asset_id:
                return robot
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
                    shift_start_hour=operator.shift_start_hour, shift_end_hour=operator.shift_end_hour,
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
                    robot.asset_id = None
                    self.bind_new_robot(robot, adopt=False)
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
        """Make operator.certifications equal the worker's valid credential codes."""
        codes = self.service.valid_credential_codes(operator.worker_id)
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
