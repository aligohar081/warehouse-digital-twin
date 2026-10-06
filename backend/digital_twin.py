"""The Digital Twin — the single source of truth for the whole system.

Everything the dashboard shows comes from here. The frontend never invents robot
positions or task states; it renders snapshots produced by this object.
"""
from __future__ import annotations

import json
import os
import threading
import time
from collections import deque
from typing import Any, Deque, Dict, List, Optional, Set

from .agent import Agent
from .box import Box
from .event_system import EventSystem
from .embodiment import AIR, GROUND, HOVER_CLEARANCE_M, LAYERS, MobilityProfile
from .equipment import Equipment, LineItem
from .faults import FaultInjector
from .fleet_bridge import FleetBridge
from .goods import StockLedger
from .inventory import NotFound as InventoryNotFound
from .inventory import open_inventory
from .inventory.catalog_data import CLASS_DEFAULT_MODELS
from .jobs import JOB_SPECS
from .logger import WarehouseLogger
from .models import (
    ACTIVE_TASK_STATES,
    CONFIG,
    AgentStatus,
    BoxKind,
    BoxStatus,
    Cell,
    CellType,
    EventType,
    IdFactory,
    KNOWN_CERTIFICATIONS,
    LogCategory,
    LogLevel,
    OPERATOR_ROLE_PRESETS,
    OperatorStatus,
    Priority,
    PRIORITY_RANK,
    ROBOT_CLASS_PRESETS,
    RobotStatus,
    SimulationStatus,
    TaskStatus,
    TaskType,
    cell_dict,
    now_iso,
)
from .navigation import NavigationEngine
from .operations import ShiftEngine
from .operator import Operator
from .robot import Robot
from .seeds import SEEDS
from .seeds import classic as classic_seed
from .task_manager import Task, TaskManager
from .task_planner import TaskPlanner
from .warehouse import Warehouse


class DigitalTwin:
    # Re-exported so collaborators do not need their own enum imports.
    RobotStatus = RobotStatus
    BoxStatus = BoxStatus
    TaskStatus = TaskStatus

    def __init__(
        self,
        log_dir: str = "logs",
        data_dir: str = "data",
        persist_logs: bool = True,
        demo: bool = True,
        demo_tasks: bool = True,
        inventory_path: str = ":memory:",
        layout: str = "classic",
    ) -> None:
        self.lock = threading.RLock()
        self.log_dir = log_dir
        self.data_dir = data_dir
        self.state_path = os.path.join(data_dir, "warehouse_state.json")
        os.makedirs(data_dir, exist_ok=True)

        self.logger = WarehouseLogger(log_dir=log_dir, persist=persist_logs)
        self.events = EventSystem(self.logger)
        self.ids = IdFactory()

        # The named floor plan (backend/layouts). "classic" — today's floor —
        # is the default, so every existing caller builds the same twin.
        self.warehouse = Warehouse(layout=layout)
        self.layout_name = self.warehouse.layout_name
        self.navigation = NavigationEngine(self.warehouse)
        self.planner = TaskPlanner(self)
        self.tasks = TaskManager(self)

        self.robots: Dict[str, Robot] = {}
        self.boxes: Dict[str, Box] = {}
        # Which pallet or tote sits in each rack and shelf slot, recorded versus
        # true quantities (backend/goods.py) — the single source of truth for
        # stock. Empty on the classic floor, which has no slots.
        self.stock = StockLedger(self.warehouse)
        # The conveyor line, the sorter and their hand-off log (backend/
        # equipment.py); None on a floor without them (classic).
        self.equipment: Optional[Equipment] = Equipment.for_floor(self)
        self.agents: Dict[str, Agent] = {}
        self.operators: Dict[str, Operator] = {}

        self.simulation_status = SimulationStatus.STOPPED
        self.simulation_speed = 1.0
        self.tick_count = 0
        self.simulation_time = 0.0
        self.boot_time = time.time()
        self.last_sync = now_iso()
        self.ci_result: Optional[Dict[str, Any]] = None
        # Armed one-shot faults and the CONFIG risks (backend/faults.py).
        self.faults = FaultInjector()

        # Rolling samples for the dashboard's trend charts — see
        # record_history()/history_snapshot() and GET /api/statistics/history.
        self.history: Deque[Dict[str, Any]] = deque(maxlen=CONFIG["HISTORY_MAX_SAMPLES"])

        # Recurring task definitions — see backend/scheduler.py.
        from .scheduler import Scheduler  # deferred: scheduler.py imports nothing from here at module level

        self.scheduler = Scheduler(self)
        # The shift engine and its orders (backend/operations): paused until
        # started, and only startable off the classic floor.
        self.shift = ShiftEngine(self)

        self.statistics: Dict[str, Any] = {
            "completed_tasks": 0,
            "failed_tasks": 0,
            "collisions": 0,
            "collisions_avoided": 0,
            "boxes_delivered": 0,
            "distance_travelled": 0,
            "charging_sessions": 0,
            "emergency_stops": 0,
            "ci_runs": 0,
        }

        self.events.emit(
            EventType.WAREHOUSE_INITIALIZED,
            f"Warehouse initialised: {self.warehouse.width}x{self.warehouse.height} grid, "
            f"{len(self.warehouse.zones)} zones",
            category=LogCategory.WAREHOUSE,
        )

        # Fleet-manager + workforce source systems (backend/inventory) and the
        # bridge that binds every live robot/operator to its record.
        # Seeded under the profile named after the floor: "classic" keeps A's
        # two-site seed, "distribution_center" puts everyone at WH-01.
        self.inventory = open_inventory(inventory_path, demo=demo, settings=lambda: CONFIG,
                                        profile=self.layout_name)
        self.fleet = FleetBridge(self, self.inventory)

        # Whether this floor's seed (backend/seeds) is on it, so a reset puts it
        # back. Classic boots with its demo, as it always has. Any other floor
        # boots empty — tests build their own floors on it — until
        # seed_floor() runs (the app's build_twin calls it).
        self.floor_seeded = False
        if demo and self.layout_name == "classic":
            self.load_demo(create_tasks=demo_tasks)

    # ------------------------------------------------------------------ #
    # Demo / bootstrap
    # ------------------------------------------------------------------ #
    def load_demo(self, create_tasks: bool = True) -> None:
        """The classic demo (backend/seeds/classic.py), with its tasks if asked."""
        classic_seed.seed(self, create_tasks=create_tasks)
        self.floor_seeded = True

    def seed_floor(self) -> None:
        """Put this floor's seed (backend/seeds, spec §4.2) on the empty twin,
        and remember it, so reset() puts it back."""
        with self.lock:
            if self.robots:
                raise ValueError(f"The {self.layout_name} floor already has robots; seed an empty twin")
            SEEDS[self.layout_name](self)
            self.floor_seeded = True

    # ------------------------------------------------------------------ #
    # Lookups
    # ------------------------------------------------------------------ #
    def find_robot(self, spec: Optional[str]) -> Optional[Robot]:
        if not spec:
            return None
        spec = str(spec).strip()
        if spec in self.robots:
            return self.robots[spec]
        low = spec.lower()
        for robot in self.robots.values():
            if robot.name.lower() == low or robot.id.lower() == low:
                return robot
        return None

    def find_box(self, spec: Optional[str]) -> Optional[Box]:
        if not spec:
            return None
        spec = str(spec).strip()
        if spec in self.boxes:
            return self.boxes[spec]
        low = spec.lower()
        for box in self.boxes.values():
            if box.name.lower() == low or box.id.lower() == low:
                return box
        return None

    def find_agent(self, spec: Optional[str]) -> Optional[Agent]:
        if not spec:
            return None
        spec = str(spec).strip()
        if spec in self.agents:
            return self.agents[spec]
        low = spec.lower()
        for agent in self.agents.values():
            if agent.name.lower() == low or agent.id.lower() == low:
                return agent
        return None

    def find_operator(self, spec: Optional[str]) -> Optional[Operator]:
        if not spec:
            return None
        spec = str(spec).strip()
        if spec in self.operators:
            return self.operators[spec]
        low = spec.lower()
        for operator in self.operators.values():
            if operator.name.lower() == low or operator.id.lower() == low:
                return operator
        return None

    def robot_cells(self, layer: Optional[str] = None) -> Dict[Cell, str]:
        """Occupied cell -> robot id, on one layer (default: every layer)."""
        return {r.position: r.id for r in self.robots.values() if layer is None or r.layer == layer}

    def other_robot_cells(self, exclude_id: str, include_reservations: bool = True,
                          layer: Optional[str] = None) -> Set[Cell]:
        """Cells occupied (or about to be occupied) by robots other than one,
        on one layer: `layer`, or by default the excluded robot's own — a
        drone overhead never blocks a ground robot, nor the reverse."""
        if layer is None:
            me = self.robots.get(exclude_id)
            layer = me.layer if me is not None else GROUND
        cells: Set[Cell] = set()
        for robot in self.robots.values():
            if robot.id == exclude_id or robot.layer != layer:
                continue
            cells.add(robot.position)
            if include_reservations and robot.next_cell is not None:
                cells.add(robot.next_cell)
        return cells

    def box_at(self, cell: Cell) -> Optional[Box]:
        for box in self.boxes.values():
            if box.position == cell and box.status not in (BoxStatus.CARRIED, BoxStatus.SHIPPED):
                return box
        return None

    # ------------------------------------------------------------------ #
    # Mutations
    # ------------------------------------------------------------------ #
    def add_robot(
        self,
        name: Optional[str] = None,
        position: Optional[Cell] = None,
        speed: Optional[float] = None,
        allowed_task_types: Optional[List[str]] = None,
        robot_class: Optional[str] = None,
        model_code: Optional[str] = None,
        asset_id: Optional[str] = None,
    ) -> Robot:
        with self.lock:
            robot_id = self.ids.next("robot", 2)
            name = name or f"Robo-{robot_id.split('_')[-1]}"
            if self.find_robot(name) is not None:
                raise ValueError(f"A robot called '{name}' already exists")

            if position is not None and not self.warehouse.is_inside(*position):
                raise ValueError(f"Position {position} is outside the warehouse")

            # A robot class (see models.ROBOT_CLASS_PRESETS) is only a
            # convenience default applied at creation time — explicit
            # `speed`/`allowed_task_types` always win, and either can be
            # changed independently afterwards through the normal
            # endpoints, same as a hand-configured robot.
            normalized_class = (robot_class or "").upper() or None
            if model_code:
                try:
                    model_class = self.inventory.model_class(model_code)
                except InventoryNotFound as exc:
                    raise ValueError(str(exc)) from None
                if normalized_class and normalized_class != model_class:
                    raise ValueError(f"Model {model_code} is a {model_class}, not a {normalized_class}")
                normalized_class = model_class
            elif asset_id and not normalized_class and self.inventory.has_asset(asset_id):
                normalized_class = self.inventory.get_robot(asset_id)["model"]["embodiment_class"]
            normalized_class = normalized_class or "AMR"
            if normalized_class not in ROBOT_CLASS_PRESETS:
                raise ValueError(
                    f"Unknown robot_class '{robot_class}' (known: {sorted(ROBOT_CLASS_PRESETS)})"
                )
            preset = ROBOT_CLASS_PRESETS[normalized_class]

            # The robot's body (backend/embodiment.py), read from its catalog
            # model, decides where it may stand. On a layered floor it also
            # decides how the robot routes and how fast it drives; on classic
            # the robot keeps its preset speed and today's routing.
            model = self._model_for(normalized_class, model_code, asset_id)
            body = self.fleet.model_profile(model) if model else None
            mobility = body if self.layout_name != "classic" else None
            spawn = self._spawn_cell(position, body, mobility, asset_id)

            if speed is not None:
                effective_speed = speed
            else:
                effective_speed = mobility.speed_cells_s if mobility is not None else preset.get("speed")
            effective_allowed = allowed_task_types if allowed_task_types else preset.get("allowed_task_types")

            robot = Robot(
                robot_id, name, spawn, speed=effective_speed,
                allowed_task_types=effective_allowed,
                robot_class=normalized_class,
            )
            self.fleet.bind_new_robot(robot, model_code=model_code, asset_id=asset_id)
            self.robots[robot.id] = robot

        self.events.emit(
            EventType.ROBOT_CREATED,
            f"{robot.name} created at ({spawn[0]},{spawn[1]}) with speed {robot.speed} cells/s",
            category=LogCategory.ROBOT,
            robot_id=robot.id,
            position=cell_dict(spawn),
        )
        return robot

    def _model_for(self, robot_class: str, model_code: Optional[str], asset_id: Optional[str]) -> Optional[str]:
        """The catalog model a new robot will be bound as (see FleetBridge.bind_new_robot)."""
        if model_code:
            return model_code
        if asset_id and self.inventory.has_asset(asset_id):
            return self.inventory.get_robot(asset_id)["model_code"]
        return CLASS_DEFAULT_MODELS.get(robot_class)

    def _spawn_cell(self, position: Optional[Cell], body: Optional[MobilityProfile],
                    mobility: Optional[MobilityProfile], asset_id: Optional[str] = None) -> Cell:
        """Where a new robot starts. Fixed equipment goes on a free station
        cell of its own — its asset's home zone's station when that is free;
        anything else on the requested (or default) cell, or the nearest free
        cell its floor profile may stop on (never a walkway crossing)."""
        occupied = set(self.robot_cells(GROUND))  # every robot starts on the ground
        if body is not None and body.is_fixed:
            stations = self.warehouse.fixed_stations
            if not stations:
                raise ValueError(f"A {body.embodiment_class} is fixed equipment and the "
                                 f"{self.layout_name} floor has no station for it")
            home_zone = (self.inventory.get_robot(asset_id)["home_zone"]
                         if asset_id and self.inventory.has_asset(asset_id) else None)
            free = sorted((c for c in stations if c not in occupied), key=lambda c: stations[c] != home_zone)
            cell = tuple(position) if position else next(iter(free), None)
            if cell is None:
                raise ValueError("Every fixed station is already taken")
            if cell not in stations:
                raise ValueError(f"({cell[0]},{cell[1]}) is not a fixed station (stations: {sorted(stations)})")
            if cell in occupied:
                raise ValueError(f"The station at ({cell[0]},{cell[1]}) is already taken")
            return cell
        home = "drone_pad" if mobility is not None and mobility.is_air else "parking_area"
        candidate = position or self.warehouse.resolve_zone(home).cells[0]
        if self.warehouse.may_stop(candidate, mobility) and candidate not in occupied:
            return candidate
        spawn = self.warehouse.nearest_walkable(candidate, occupied, profile=mobility)
        if spawn is None:
            raise ValueError("No free drivable cell available for a new robot")
        return spawn

    def set_robot_capabilities(self, robot_id: str, allowed_task_types: Optional[List[str]]) -> Robot:
        """(Re)configure which TaskType values `robot_id` may be assigned.

        An empty list or None clears the restriction (unrestricted — the
        default). See backend/eligibility.py's robot_eligibility() for
        where this is actually enforced: TaskManager.validate() (an
        explicitly-named robot outside its own configured task types is
        rejected outright, 422) and select_robot() (AUTO assignment skips
        it the same way it already skips busy/ineligible robots).
        """
        robot = self.find_robot(robot_id)
        if robot is None:
            raise KeyError(f"Robot '{robot_id}' does not exist")
        valid = {t.value for t in TaskType}
        cleaned = sorted({str(t).upper().strip() for t in (allowed_task_types or []) if str(t).strip()})
        unknown = [t for t in cleaned if t not in valid]
        if unknown:
            raise ValueError(f"Unknown task type(s): {unknown}")
        with self.lock:
            robot.allowed_task_types = cleaned or None
            robot.touch()
        self.events.emit(
            EventType.ROBOT_CAPABILITIES_UPDATED,
            f"{robot.name} capabilities set to {cleaned or 'unrestricted'}",
            category=LogCategory.ROBOT,
            robot_id=robot.id,
            data={"allowed_task_types": robot.allowed_task_types},
        )
        return robot

    def add_box(
        self,
        name: Optional[str] = None,
        position: Optional[Cell] = None,
        weight: float = 1.0,
        source: Optional[str] = None,
        destination: Optional[str] = None,
        kind: Optional[str] = None,
        sku: Optional[str] = None,
        quantity: int = 0,
        slot: Optional[str] = None,
        true_weight_kg: Optional[float] = None,
        order_id: Optional[str] = None,
    ) -> Box:
        """Create a box (spec §7.1). `weight` is its declared weight, and
        `true_weight_kg` what it really weighs (the same unless given).

        A box given a `slot` (a PALLET or a TOTE) sits in that rack or shelf
        slot, and the stock ledger records it there with its SKU and quantity.
        An ITEM or CARTON sits exactly on `position` — a conveyor cell, a pack
        station — and is never snapped. Any other box goes on `position`, or
        classic's default shelf, snapped to the nearest drivable cell as
        always; a floor with no default shelf needs a position or a slot."""
        box_kind = BoxKind(str(kind).upper()) if kind else BoxKind.TOTE
        if isinstance(quantity, bool) or int(quantity) < 0:
            raise ValueError("quantity must be a non-negative whole number")
        with self.lock:
            box_id = self.ids.next("box")
            name = name or f"Box-{box_id.split('_')[-1]}"
            if self.find_box(name) is not None:
                raise ValueError(f"A box called '{name}' already exists")

            if slot:
                spot = self._slot_cell(slot, box_kind, position)
            elif box_kind in (BoxKind.ITEM, BoxKind.CARTON):
                if position is None:
                    raise ValueError(f"An {box_kind.value} box needs a position")
                spot = (int(position[0]), int(position[1]))
                if not self.warehouse.is_inside(*spot):
                    raise ValueError(f"Position {spot} is outside the warehouse")
                on_line = self.equipment is not None and spot in self.equipment.conveyor
                if on_line and not self.equipment.conveyor.is_free(spot):
                    raise ValueError(f"Conveyor cell {spot} is occupied")
            else:
                default = self.warehouse.resolve_zone("shelf_a")
                if position is None and default is None:
                    raise ValueError(f"A new box on the {self.layout_name} floor needs a position or a slot")
                candidate = position or default.cells[0]
                if not self.warehouse.is_inside(*candidate):
                    raise ValueError(f"Position {candidate} is outside the warehouse")
                spot = candidate if self.warehouse.is_walkable(*candidate) \
                    else self.warehouse.nearest_walkable(candidate)
                if spot is None:
                    raise ValueError("No reachable cell available for a new box")

            zone = self.warehouse.zone_of_cell(spot)
            box = Box(
                box_id=box_id,
                name=name,
                position=spot,
                weight=weight,
                source=source or (zone.key if zone else None),
                destination=destination,
                kind=box_kind,
                sku=sku,
                quantity=int(quantity),
                true_weight_kg=true_weight_kg,
                order_id=order_id,
            )
            if slot:  # recorded first: a taken slot raises before the box exists
                self.stock.put(slot, box.id, sku, int(quantity), kind=box_kind.value)
                box.slot = slot
            elif self.equipment is not None and spot in self.equipment.conveyor:
                self.equipment.conveyor.load(spot, LineItem(box.id, order_id=order_id))  # it rides the line
                box.status = BoxStatus.DELIVERING
            self.boxes[box.id] = box

        data = None
        if self.layout_name != "classic":
            data = {"kind": box.kind.value, "sku": box.sku, "quantity": box.quantity, "slot": box.slot,
                    "declared_weight_kg": box.declared_weight_kg, "true_weight_kg": box.true_weight_kg}
        self.events.emit(
            EventType.BOX_CREATED,
            f"{box.name} created at ({spot[0]},{spot[1]}), {box.weight} kg",
            category=LogCategory.BOX,
            box_id=box.id,
            position=cell_dict(spot),
            data=data,
        )
        return box

    def _slot_cell(self, slot_id: str, kind: BoxKind, position: Optional[Cell]) -> Cell:
        """The rack or shelf cell a new box given `slot_id` sits on."""
        slot = self.warehouse.slot(slot_id)
        if slot is None:
            raise ValueError(f"Unknown slot {slot_id!r}")
        if kind.value != slot.kind:
            raise ValueError(f"A {kind.value} cannot go in {slot.kind} slot {slot_id}")
        if position is not None and tuple(position) != slot.cell:
            raise ValueError(f"Slot {slot_id} is at ({slot.cell[0]},{slot.cell[1]}), not {tuple(position)}")
        return slot.cell

    def add_agent(self, name: Optional[str] = None, model_version: Optional[str] = None) -> Agent:
        with self.lock:
            agent_id = self.ids.next("agent")
            name = name or f"Agent-{agent_id.split('_')[-1]}"
            if self.find_agent(name) is not None:
                raise ValueError(f"An agent called '{name}' already exists")
            agent = Agent(agent_id, name, model_version=model_version)
            self.agents[agent.id] = agent

        self.events.emit(
            EventType.AGENT_CREATED,
            f"{agent.name} created (model {agent.model_version})",
            category=LogCategory.TASK,
            data={"agent_id": agent.id, "model_version": agent.model_version},
        )
        return agent

    def add_operator(
        self,
        name: Optional[str] = None,
        certifications: Optional[List[str]] = None,
        shift_start_hour: Optional[int] = None,
        shift_end_hour: Optional[int] = None,
        role: Optional[str] = None,
        worker_id: Optional[str] = None,
    ) -> Operator:
        with self.lock:
            operator_id = self.ids.next("operator")
            name = name or f"Operator-{operator_id.split('_')[-1]}"
            if self.find_operator(name) is not None:
                raise ValueError(f"An operator called '{name}' already exists")

            # A role (see models.OPERATOR_ROLE_PRESETS) only supplies
            # default certifications/shift hours — explicit values here
            # always win, and either can be changed independently
            # afterwards, exactly like robot_class does for a robot.
            normalized_role = role.upper() if role else None
            preset: Dict[str, Any] = {}
            if normalized_role:
                if normalized_role not in OPERATOR_ROLE_PRESETS:
                    raise ValueError(
                        f"Unknown operator role '{role}' (known: {sorted(OPERATOR_ROLE_PRESETS)})"
                    )
                preset = OPERATOR_ROLE_PRESETS[normalized_role]
            effective_certs = certifications if certifications else preset.get("certifications")
            effective_start = shift_start_hour if shift_start_hour is not None else preset.get("shift_start_hour")
            effective_end = shift_end_hour if shift_end_hour is not None else preset.get("shift_end_hour")

            operator = Operator(
                operator_id, name, certifications=effective_certs,
                shift_start_hour=effective_start, shift_end_hour=effective_end,
                role=normalized_role,
            )
            self.fleet.bind_new_operator(operator, worker_id=worker_id)
            self.operators[operator.id] = operator

        self.events.emit(
            EventType.OPERATOR_CREATED,
            f"{operator.name} created ({', '.join(operator.certifications) or 'no certifications'})",
            category=LogCategory.TASK,
            data={"operator_id": operator.id, "certifications": list(operator.certifications), "role": normalized_role},
        )
        return operator

    def set_operator_shift(
        self, operator_id: str, shift_start_hour: Optional[int], shift_end_hour: Optional[int]
    ) -> Operator:
        """(Re)configure an operator's automatic shift window (real
        wall-clock hours 0-23) — see Simulator._check_operator_shifts and
        Operator.is_within_shift. Pass both as None to go back to manual
        status control only."""
        operator = self.find_operator(operator_id)
        if operator is None:
            raise KeyError(f"Operator '{operator_id}' does not exist")
        for hour in (shift_start_hour, shift_end_hour):
            if hour is not None and not (0 <= int(hour) <= 23):
                raise ValueError("Shift hours must be between 0 and 23")
        with self.lock:
            operator.shift_start_hour = int(shift_start_hour) if shift_start_hour is not None else None
            operator.shift_end_hour = int(shift_end_hour) if shift_end_hour is not None else None
            operator.touch()
        message = (
            f"{operator.name} shift set to {operator.shift_start_hour}-{operator.shift_end_hour}"
            if operator.has_shift else
            f"{operator.name} shift cleared — manual status control only"
        )
        self.logger.info(LogCategory.TASK, message)
        return operator

    # ---- robot control ------------------------------------------------ #
    def stop_robot(self, robot_id: str, reason: str = "user") -> Robot:
        robot = self.find_robot(robot_id)
        if robot is None:
            raise KeyError(f"Robot '{robot_id}' does not exist")
        if robot.status != RobotStatus.STOPPED:
            robot.status_before_stop = robot.status
        robot.set_status(RobotStatus.STOPPED)
        self.events.emit(
            EventType.ROBOT_STOPPED,
            f"{robot.name} stopped ({reason})",
            category=LogCategory.ROBOT,
            level=LogLevel.WARNING,
            robot_id=robot.id,
            task_id=robot.current_task,
            position=cell_dict(robot.position),
        )
        return robot

    def resume_robot(self, robot_id: str, reason: str = "user") -> Robot:
        robot = self.find_robot(robot_id)
        if robot is None:
            raise KeyError(f"Robot '{robot_id}' does not exist")
        restored = robot.status_before_stop or (
            RobotStatus.PLANNING if robot.current_task else RobotStatus.IDLE
        )
        if restored in (RobotStatus.STOPPED, RobotStatus.ERROR):
            restored = RobotStatus.PLANNING if robot.current_task else RobotStatus.IDLE
        robot.status_before_stop = None
        robot.last_error = None
        robot.set_status(restored)
        task = self.tasks.get(robot.current_task) if robot.current_task else None
        if task is not None and task.status == TaskStatus.PAUSED:
            task.record(TaskStatus.IN_PROGRESS, "Resumed with robot")
        self.events.emit(
            EventType.ROBOT_RESUMED,
            f"{robot.name} resumed ({reason}) → {restored.value}",
            category=LogCategory.ROBOT,
            robot_id=robot.id,
            task_id=robot.current_task,
            position=cell_dict(robot.position),
        )
        return robot

    def set_robot_layer(self, robot_id: str, layer: str, altitude_m: Optional[float] = None) -> Robot:
        """Take a drone off (onto AIR) or land it (back on GROUND), or change
        its altitude while it flies.

        Only a robot whose floor profile flies may leave the ground, and it
        takes off and lands only on a drone-pad cell that no robot on the
        target layer holds. Instantaneous here; the TAKEOFF / LAND job steps
        add the durations. With no altitude, a take-off climbs to
        HOVER_CLEARANCE_M and a drone already flying keeps its altitude."""
        robot = self.find_robot(robot_id)
        if robot is None:
            raise KeyError(f"Robot '{robot_id}' does not exist")
        layer = str(layer or "").upper()
        if layer not in LAYERS:
            raise ValueError(f"Unknown layer {layer!r} (known: {list(LAYERS)})")
        with self.lock:
            if layer == GROUND and robot.layer == GROUND:
                return robot  # already on the ground: nothing to do
            mobility = robot.mobility
            if mobility is None or not mobility.is_air:
                raise ValueError(f"{robot.name} cannot fly")
            altitude = 0.0
            if layer == AIR:
                if altitude_m is not None:
                    altitude = float(altitude_m)
                else:
                    altitude = robot.altitude_m if robot.layer == AIR else HOVER_CLEARANCE_M
                if not 0.0 < altitude <= mobility.max_lift_m:
                    raise ValueError(f"Altitude must be above 0 and at most {mobility.max_lift_m} m")
            if layer != robot.layer:
                x, y = robot.position
                if self.warehouse.cell_type(x, y) is not CellType.DRONE_PAD:
                    verb = "take off" if layer == AIR else "land"
                    raise ValueError(f"{robot.name} can only {verb} on the drone pad, not at ({x},{y})")
                if robot.position in self.robot_cells(layer):
                    raise ValueError(f"({x},{y}) is already occupied on the {layer} layer")
                self.end_safety_wait(robot, "ended by a layer change")
                robot.clear_path()  # a route planned for the other layer no longer applies
            robot.layer, robot.altitude_m = layer, altitude
            robot.touch()
        self.logger.info(
            LogCategory.ROBOT,
            f"{robot.name} is on the {layer} layer at {robot.altitude_m:.1f} m",
            robot_id=robot.id, position=cell_dict(robot.position),
        )
        return robot

    def end_safety_wait(self, robot: Robot, cause: str) -> Optional[int]:
        """Close `robot`'s safety wait, if it is in one, with a
        ROBOT_SAFETY_RESUMED that pairs its ROBOT_SAFETY_WAIT (spec §10.3) —
        whether the condition cleared or the wait ended some other way (its
        task ended, the robot was reset or took off). Returns the ticks waited."""
        reason = robot.wait_reason
        if reason is None:
            return None
        started = robot.wait_started_tick if robot.wait_started_tick is not None else self.tick_count
        waited = self.tick_count - started
        task_id = robot.wait_task_id
        robot.wait_reason = robot.wait_started_tick = robot.wait_task_id = robot.wait_cell = None
        robot.wait_escalated = False
        self.events.emit(
            EventType.ROBOT_SAFETY_RESUMED,
            f"{robot.name} resumed after {waited} ticks ({reason} {cause})",
            category=LogCategory.SAFETY,
            robot_id=robot.id,
            task_id=task_id,
            position=cell_dict(robot.position),
            data={"reason": reason, "waited_ticks": waited, "cause": cause},
        )
        return waited

    def request_charge(self, robot_id: str, priority: Priority = Priority.HIGH,
                      internal: bool = False) -> Task:
        robot = self.find_robot(robot_id)
        if robot is None:
            raise KeyError(f"Robot '{robot_id}' does not exist")
        if robot.mains_powered:
            raise ValueError(f"{robot.name} is mains-powered and never needs charging")
        existing = self.tasks.task_for_robot(robot.id)
        if existing is not None and existing.type == TaskType.CHARGE_ROBOT:
            return existing
        return self.tasks.create_task(
            {"type": "CHARGE_ROBOT", "robot_id": robot.id, "priority": priority.value},
            internal=internal,
        )

    def reset_robot(self, robot_id: str) -> Robot:
        robot = self.find_robot(robot_id)
        if robot is None:
            raise KeyError(f"Robot '{robot_id}' does not exist")
        occupied = {r.position for r in self.robots.values() if r.id != robot.id and r.layer == GROUND}
        home = robot.home if robot.home not in occupied else self.warehouse.nearest_walkable(
            robot.home, occupied, profile=robot.mobility
        )
        if home is None and robot.mobility is not None and robot.mobility.is_air:
            # A drone lands only on its pad: with every pad cell taken there is nowhere to put it.
            raise ValueError(f"No free drone pad cell to land {robot.name} on")
        task = self.tasks.get(robot.current_task) if robot.current_task else None
        if task is not None and not task.is_terminal:
            self.tasks.fail_task(task, f"{robot.name} was reset by the operator")
        if robot.carrying_box:
            box = self.find_box(robot.carrying_box)
            if box is not None:
                box.position = robot.position
                box.set_status(BoxStatus.STORED)
                box.assigned_robot = None
                box.assigned_task = None
            robot.carrying_box = None
        robot.position = home or robot.position
        robot.layer, robot.altitude_m = GROUND, 0.0  # a reset robot is back on the ground
        robot.lift_height_m, robot.activity = 0.0, None
        robot.battery = 100.0
        self.end_safety_wait(robot, "ended by a reset")
        robot.clear_path()
        robot.current_task = None
        robot.moves_since_drain = 0
        robot.move_accumulator = 0.0
        robot.wait_ticks = 0
        robot.status_before_stop = None
        robot.last_error = None
        robot.set_status(RobotStatus.IDLE)
        self.events.emit(
            EventType.ROBOT_RESET,
            f"{robot.name} reset to ({robot.position[0]},{robot.position[1]}) with a full battery",
            category=LogCategory.ROBOT,
            level=LogLevel.WARNING,
            robot_id=robot.id,
            position=cell_dict(robot.position),
        )
        return robot

    def register_collision(self, robot_a_id: str, robot_b_id: str) -> Dict[str, Any]:
        """The one place a real collision (two robots actually occupying
        the same cell) turns into a consequence: both robots halt to
        ERROR and — the "task disconnected" behaviour — whatever task
        either of them was running fails immediately, exactly like a
        battery-depletion or controller-error halt already does (see
        Simulator._check_battery_thresholds / Simulator.tick's exception
        handler). Called by Simulator._detect_collisions whenever a real
        overlap actually happens — see CONFIG["COLLISION_RISK"] for why
        that's rare (0 by default) rather than never. Recovery is the
        existing path: POST /api/robots/{id}/reset sends each robot home
        with a full battery and drops its cargo.
        """
        robot_a = self.find_robot(robot_a_id)
        robot_b = self.find_robot(robot_b_id)
        if robot_a is None or robot_b is None:
            raise KeyError("Both robots must exist")
        if robot_a.id == robot_b.id:
            raise ValueError("A robot cannot collide with itself")
        if robot_a.layer != robot_b.layer:
            raise ValueError(f"{robot_a.name} and {robot_b.name} are on different layers and cannot collide")

        with self.lock:
            self.statistics["collisions"] += 1
            robot_a.collision_count += 1
            robot_b.collision_count += 1

        self.events.emit(
            EventType.COLLISION_DETECTED,
            f"Collision: {robot_a.name} and {robot_b.name} both occupy "
            f"({robot_a.position[0]},{robot_a.position[1]})",
            category=LogCategory.COLLISION,
            level=LogLevel.CRITICAL,
            robot_id=robot_a.id,
            position=cell_dict(robot_a.position),
            data={"robot_a": robot_a.id, "robot_b": robot_b.id, "layer": robot_a.layer},
        )

        disconnected_tasks = []
        for robot, other in ((robot_a, robot_b), (robot_b, robot_a)):
            robot.last_error = f"Collided with {other.name}"
            robot.set_status(RobotStatus.ERROR)
            self.end_safety_wait(robot, "ended by a collision")
            robot.clear_path()
            self.events.emit(
                EventType.ROBOT_ERROR,
                f"{robot.name} halted after colliding with {other.name}",
                category=LogCategory.COLLISION,
                level=LogLevel.CRITICAL,
                robot_id=robot.id,
                task_id=robot.current_task,
                position=cell_dict(robot.position),
            )
            task = self.tasks.get(robot.current_task) if robot.current_task else None
            if task is not None and not task.is_terminal:
                self.tasks.fail_task(task, f"{robot.name} collided with {other.name} — task disconnected")
                disconnected_tasks.append(task.id)

        return {
            "robot_a": robot_a.id, "robot_b": robot_b.id,
            "position": cell_dict(robot_a.position), "disconnected_tasks": disconnected_tasks,
        }

    # ---- simulation control ------------------------------------------ #
    def set_simulation_status(self, status: SimulationStatus, message: str,
                             event: EventType, level: LogLevel = LogLevel.INFO) -> None:
        self.simulation_status = status
        self.events.emit(event, message, category=LogCategory.SYSTEM, level=level)

    def emergency_stop(self) -> None:
        for robot in self.robots.values():
            if robot.status not in (RobotStatus.STOPPED, RobotStatus.ERROR):
                robot.status_before_stop = robot.status
                robot.set_status(RobotStatus.STOPPED)
        self.simulation_status = SimulationStatus.EMERGENCY_STOP
        self.statistics["emergency_stops"] += 1
        self.events.emit(
            EventType.EMERGENCY_STOP,
            "Emergency stop activated by user — all robots halted, tasks preserved",
            category=LogCategory.SYSTEM,
            level=LogLevel.CRITICAL,
        )

    def release_emergency_stop(self) -> None:
        for robot in self.robots.values():
            if robot.status == RobotStatus.STOPPED:
                self.resume_robot(robot.id, reason="emergency stop released")
        self.simulation_status = SimulationStatus.RUNNING
        self.events.emit(
            EventType.SIMULATION_STARTED,
            "Emergency stop released — robots resuming their tasks",
            category=LogCategory.SYSTEM,
            level=LogLevel.WARNING,
        )

    def reset(self, demo_tasks: bool = True) -> None:
        with self.lock:
            self.robots.clear()
            self.boxes.clear()
            self.agents.clear()
            self.operators.clear()
            self.tasks.clear()
            self.events.clear()
            self.ids = IdFactory()
            self.tick_count = 0
            self.simulation_time = 0.0
            self.simulation_status = SimulationStatus.STOPPED
            self.ci_result = None
            self.history.clear()
            self.scheduler.clear()
            self.faults.clear()
            self.stock = StockLedger(self.warehouse)
            self.equipment = Equipment.for_floor(self)
            self.shift.reset()
            for key in self.statistics:
                self.statistics[key] = 0
            self.navigation = NavigationEngine(self.warehouse)
            self.planner = TaskPlanner(self)
            self.fleet.reseed()
        self.events.emit(
            EventType.SIMULATION_RESET,
            "Warehouse reset to the initial demo configuration",
            category=LogCategory.SYSTEM,
            level=LogLevel.WARNING,
        )
        # The floor comes back as its seed left it: classic as its demo, as it
        # always has; another floor only if it was seeded (an empty test floor
        # stays empty).
        if self.layout_name == "classic":
            self.load_demo(create_tasks=demo_tasks)
        elif self.floor_seeded:
            self.seed_floor()

    def synchronize(self) -> None:
        self.last_sync = now_iso()

    # ------------------------------------------------------------------ #
    # Statistics & snapshots
    # ------------------------------------------------------------------ #
    def environment_state(self) -> Dict[str, Any]:
        robots = list(self.robots.values())
        boxes = list(self.boxes.values())
        counts = self.tasks.counts()
        batteries = [r.battery for r in robots] or [0.0]
        return {
            "warehouse_status": "OPERATIONAL" if self.robots else "EMPTY",
            "simulation_status": self.simulation_status.value,
            "simulation_speed": self.simulation_speed,
            "simulation_time": round(self.simulation_time, 2),
            "tick": self.tick_count,
            "robot_count": len(robots),
            "box_count": len(boxes),
            "active_tasks": counts["active"],
            "pending_tasks": counts["pending"],
            "completed_tasks": counts["completed"],
            "failed_tasks": counts["failed"],
            "collision_count": self.statistics["collisions"],
            "collisions_avoided": self.statistics["collisions_avoided"],
            "system_uptime": round(time.time() - self.boot_time, 1),
            "last_sync": self.last_sync,
        }

    def statistics_snapshot(self) -> Dict[str, Any]:
        robots = list(self.robots.values())
        boxes = list(self.boxes.values())
        counts = self.tasks.counts()
        batteries = [r.battery for r in robots]
        return {
            "robots": len(robots),
            "active_robots": sum(
                1
                for r in robots
                if r.status in (RobotStatus.MOVING, RobotStatus.PICKING, RobotStatus.CARRYING,
                                RobotStatus.DELIVERING, RobotStatus.PLANNING, RobotStatus.WAITING)
            ),
            "idle_robots": sum(1 for r in robots if r.status == RobotStatus.IDLE),
            "charging_robots": sum(1 for r in robots if r.status == RobotStatus.CHARGING),
            "stopped_robots": sum(1 for r in robots if r.is_halted),
            "boxes": len(boxes),
            "boxes_stored": sum(1 for b in boxes if b.status == BoxStatus.STORED),
            "boxes_in_transit": sum(
                1 for b in boxes if b.status in (BoxStatus.CARRIED, BoxStatus.PICKING,
                                                 BoxStatus.DELIVERING, BoxStatus.RESERVED)
            ),
            "boxes_delivered": sum(1 for b in boxes if b.status == BoxStatus.DELIVERED),
            "tasks_total": counts["total"],
            "active_tasks": counts["active"],
            "pending_tasks": counts["pending"],
            "completed_tasks": counts["completed"],
            "failed_tasks": counts["failed"],
            "cancelled_tasks": counts["cancelled"],
            "collisions": self.statistics["collisions"],
            "collisions_avoided": self.statistics["collisions_avoided"],
            "average_battery": round(sum(batteries) / len(batteries), 1) if batteries else 0.0,
            "total_distance": sum(r.total_distance for r in robots),
            "charging_sessions": sum(r.charging_sessions for r in robots),
            "emergency_stops": self.statistics["emergency_stops"],
            "ci_runs": self.statistics["ci_runs"],
            "navigation": self.navigation.stats(),
            "planner": self.planner.stats(),
        }

    def record_history(self) -> None:
        """Append one compact sample of live statistics for the
        dashboard's trend charts (see GET /api/statistics/history).
        Called once per tick from Simulator.tick(); bounded by
        CONFIG["HISTORY_MAX_SAMPLES"] — older samples just roll off."""
        stats = self.statistics_snapshot()
        self.history.append({
            "tick": self.tick_count,
            "time": round(self.simulation_time, 1),
            "timestamp": now_iso(),
            "average_battery": stats["average_battery"],
            "active_tasks": stats["active_tasks"],
            "pending_tasks": stats["pending_tasks"],
            "completed_tasks": stats["completed_tasks"],
            "failed_tasks": stats["failed_tasks"],
            "collisions": stats["collisions"],
        })

    def history_snapshot(self) -> List[Dict[str, Any]]:
        return list(self.history)

    def robot_statistics(self) -> List[Dict[str, Any]]:
        out = []
        for robot in self.robots.values():
            task = self.tasks.get(robot.current_task) if robot.current_task else None
            out.append(
                {
                    "id": robot.id,
                    "name": robot.name,
                    "total_distance": robot.total_distance,
                    "tasks_completed": robot.completed_tasks,
                    "tasks_failed": robot.failed_tasks,
                    "boxes_delivered": robot.boxes_delivered,
                    "battery_consumed": round(robot.battery_consumed, 1),
                    "charging_sessions": robot.charging_sessions,
                    "collisions": robot.collision_count,
                    "wait_events": robot.wait_events,
                    "replans": robot.replan_count,
                    "average_speed": robot.speed,
                    "position": cell_dict(robot.position),
                    "current_task": robot.current_task,
                    "current_task_summary": task.summary() if task else None,
                }
            )
        return out

    def options(self) -> Dict[str, Any]:
        return {
            "robots": [{"id": r.id, "label": r.name} for r in self.robots.values()],
            "boxes": [
                {"id": b.id, "label": b.name, "status": b.status.value}
                for b in self.boxes.values()
            ],
            "agents": [
                {"id": a.id, "label": f"{a.name} ({a.model_version})", "status": a.status.value}
                for a in self.agents.values()
            ],
            "operators": [
                {
                    "id": o.id,
                    "label": f"{o.name} ({', '.join(o.certifications) or 'no certs'})",
                    "status": o.status.value,
                    "certifications": list(o.certifications),
                }
                for o in self.operators.values()
            ],
            "locations": self.warehouse.location_options(),
            "task_types": [
                {"id": TaskType.PICK_AND_DELIVER.value, "label": "Pick & deliver"},
                {"id": TaskType.MOVE_ROBOT.value, "label": "Move robot"},
                {"id": TaskType.PICK_BOX.value, "label": "Pick box"},
                {"id": TaskType.DELIVER_BOX.value, "label": "Deliver box"},
                {"id": TaskType.MOVE_BOX.value, "label": "Move box"},
                {"id": TaskType.CHARGE_ROBOT.value, "label": "Charge robot"},
                {"id": TaskType.STOP_ROBOT.value, "label": "Stop robot"},
                {"id": TaskType.RESUME_ROBOT.value, "label": "Resume robot"},
                {"id": TaskType.AGENT_INSPECTION.value, "label": "Agent inspection"},
                {"id": TaskType.HUMAN_INSPECTION.value, "label": "Human inspection"},
                {"id": TaskType.MIXED_MAINTENANCE_MISSION.value, "label": "Mixed maintenance mission"},
                {"id": TaskType.AGENT_REPLAN.value, "label": "Agent replan review"},
                {"id": TaskType.AGENT_AUDIT.value, "label": "Agent audit"},
                {"id": TaskType.OPERATOR_APPROVAL.value, "label": "Operator approval"},
                {"id": TaskType.OPERATOR_MAINTENANCE_SIGNOFF.value, "label": "Operator maintenance sign-off"},
                {"id": TaskType.BATCH_DELIVER.value, "label": "Batch deliver (multiple boxes)"},
            ] + [{"id": kind.value, "label": spec.label} for kind, spec in JOB_SPECS.items()],
            # The task types it actually makes sense to restrict a robot
            # to (i.e. the ones a robot is really dispatched for) — what
            # the dashboard's per-robot capability editor offers as
            # checkboxes. STOP_ROBOT/RESUME_ROBOT are deliberately
            # excluded: they're safety controls, not dispatched work, and
            # never go through robot_eligibility at all (see
            # TaskManager.IMMEDIATE).
            "robot_capability_task_types": [
                {"id": TaskType.PICK_AND_DELIVER.value, "label": "Pick & deliver"},
                {"id": TaskType.MOVE_ROBOT.value, "label": "Move robot"},
                {"id": TaskType.PICK_BOX.value, "label": "Pick box"},
                {"id": TaskType.DELIVER_BOX.value, "label": "Deliver box"},
                {"id": TaskType.MOVE_BOX.value, "label": "Move box"},
                {"id": TaskType.CHARGE_ROBOT.value, "label": "Charge robot"},
                {"id": TaskType.MIXED_MAINTENANCE_MISSION.value, "label": "Mixed maintenance mission"},
                {"id": TaskType.BATCH_DELIVER.value, "label": "Batch deliver (multiple boxes)"},
            ] + [{"id": kind.value, "label": spec.label} for kind, spec in JOB_SPECS.items() if not spec.human],
            "robot_classes": [
                {"id": key, "label": preset.get("label", key)}
                for key, preset in ROBOT_CLASS_PRESETS.items()
            ],
            "operator_roles": [
                {"id": key, "label": preset.get("label", key)}
                for key, preset in OPERATOR_ROLE_PRESETS.items()
            ],
            "known_certifications": list(KNOWN_CERTIFICATIONS),
            "priorities": [p.value for p in Priority],
            "speeds": CONFIG["SIMULATION_SPEEDS"],
        }

    def snapshot(self, include_layout: bool = False, task_limit: int = 60) -> Dict[str, Any]:
        with self.lock:
            state: Dict[str, Any] = {
                "layout_name": self.layout_name,
                "environment": self.environment_state(),
                "robots": [r.to_dict() for r in self.robots.values()],
                "boxes": [b.to_dict() for b in self.boxes.values()],
                "agents": [a.to_dict() for a in self.agents.values()],
                "operators": [o.to_dict() for o in self.operators.values()],
                "tasks": self.tasks.list_tasks(limit=task_limit),
                "statistics": self.statistics_snapshot(),
                "robot_statistics": self.robot_statistics(),
                "ci": self.ci_result,
                "options": self.options(),
                "timestamp": now_iso(),
            }
            if include_layout:
                state["warehouse"] = self.warehouse.to_dict()
                state["config"] = {
                    key: CONFIG[key]
                    for key in (
                        "GRID_WIDTH", "GRID_HEIGHT", "TICK_DT", "BATTERY_LOW",
                        "BATTERY_CRITICAL", "SIMULATION_SPEEDS",
                    )
                }
        return state

    # ------------------------------------------------------------------ #
    # Persistence
    # ------------------------------------------------------------------ #
    #: The save format save_state writes (spec §4.3). Version 1 — no layout,
    #: stock or equipment — is a classic save, and still loads on classic.
    STATE_VERSION = 2

    def serialize(self) -> Dict[str, Any]:
        with self.lock:
            return {
                "version": self.STATE_VERSION,
                "layout": self.layout_name,
                "saved_at": now_iso(),
                "warehouse": {"width": self.warehouse.width, "height": self.warehouse.height},
                "robots": [r.to_state() for r in self.robots.values()],
                "boxes": [b.to_dict() for b in self.boxes.values()],
                "agents": [a.to_dict() for a in self.agents.values()],
                "operators": [o.to_dict() for o in self.operators.values()],
                "tasks": [t.to_dict() for t in self.tasks.tasks.values()],
                "schedules": self.scheduler.to_dict(),
                "statistics": dict(self.statistics),
                "simulation": {
                    "status": self.simulation_status.value,
                    "speed": self.simulation_speed,
                    "tick": self.tick_count,
                    "time": self.simulation_time,
                },
                "counters": {
                    "robot": self.ids.peek("robot"),
                    "box": self.ids.peek("box"),
                    "task": self.ids.peek("task"),
                    "agent": self.ids.peek("agent"),
                    "operator": self.ids.peek("operator"),
                },
                "stock": self.stock.to_dict(),
                "equipment": self.equipment.to_state() if self.equipment is not None else None,
                "floor_seeded": self.floor_seeded,
            }

    def save_state(self, path: Optional[str] = None) -> str:
        path = path or self.state_path
        payload = self.serialize()
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
        self.events.emit(
            EventType.STATE_SAVED,
            f"Digital twin state saved to {path}",
            category=LogCategory.DIGITAL_TWIN,
        )
        return path

    def load_state(self, path: Optional[str] = None) -> None:
        """Restore a save_state file (spec §4.3): version 2, or version 1 (a
        classic save). A save of another floor is refused with a ValueError
        (Plan ruling 5): its robots are bound to another inventory profile's
        assets. The whole file is rebuilt first, so a damaged save changes
        nothing; then robots, boxes, people, tasks, the stock ledger and the
        equipment are all replaced — nothing from before the load survives."""
        path = path or self.state_path
        if not os.path.exists(path):
            raise FileNotFoundError(f"No saved state at {path}")
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        version = payload.get("version", 1)
        if version not in (1, self.STATE_VERSION):
            raise ValueError(f"Unknown save version {version!r} (this twin reads 1 and {self.STATE_VERSION})")
        layout = payload.get("layout", "classic") if version == self.STATE_VERSION else "classic"
        if layout != self.layout_name:
            raise ValueError(f"This save is of the {layout} floor, and this twin runs the {self.layout_name} "
                             f"floor: start the app with WAREHOUSE_LAYOUT={layout} to load it")

        with self.lock:
            robots = [Robot.from_dict(data) for data in payload.get("robots", [])]
            boxes = [Box.from_dict(data) for data in payload.get("boxes", [])]
            agents = [Agent.from_dict(data) for data in payload.get("agents", [])]
            operators = [Operator.from_dict(data, self.warehouse) for data in payload.get("operators", [])]
            tasks = [Task.from_dict(data) for data in payload.get("tasks", [])]
            stock = StockLedger.from_dict(payload.get("stock") or {}, self.warehouse)
            equipment = Equipment.for_floor(self)
            if equipment is not None:
                equipment.load_state(payload.get("equipment") or {})
            held = [location.box_id for location in stock.locations()]
            if equipment is not None:
                held += [item.box_id for item in [*equipment.conveyor.items.values(), *equipment.sorter.inside]]
            unknown = sorted(set(held) - {box.id for box in boxes})
            if unknown:
                raise ValueError(f"The save's stock or conveyor holds boxes it has no record of: {unknown}")

            self.robots.clear()
            self.boxes.clear()
            self.agents.clear()
            self.operators.clear()
            self.tasks.clear()
            self.robots.update((robot.id, robot) for robot in robots)
            self.boxes.update((box.id, box) for box in boxes)
            self.agents.update((agent.id, agent) for agent in agents)
            self.operators.update((operator.id, operator) for operator in operators)
            for sequence, task in enumerate(tasks, start=1):
                task.sequence = sequence
                self.tasks.tasks[task.id] = task
            self.tasks._sequence = len(tasks)
            self.stock = stock
            self.equipment = equipment
            # A version 1 save says nothing about the seed: the twin's own boot decides.
            self.floor_seeded = bool(payload.get("floor_seeded", self.floor_seeded))
            self.fleet.rebind_all()
            self.scheduler.load_dict(payload.get("schedules", {}))
            self.statistics.update(payload.get("statistics", {}))
            simulation = payload.get("simulation", {})
            self.simulation_status = SimulationStatus(simulation.get("status", "STOPPED"))
            self.simulation_speed = simulation.get("speed", 1.0)
            self.tick_count = simulation.get("tick", 0)
            self.simulation_time = simulation.get("time", 0.0)
            counters = payload.get("counters", {})
            for prefix, value in counters.items():
                self.ids.reserve(prefix, int(value or 0))

        self.events.emit(
            EventType.STATE_LOADED,
            f"Digital twin state loaded from {path}: "
            f"{len(self.robots)} robots, {len(self.boxes)} boxes, {len(self.tasks.tasks)} tasks",
            category=LogCategory.DIGITAL_TWIN,
        )
        self.synchronize()
