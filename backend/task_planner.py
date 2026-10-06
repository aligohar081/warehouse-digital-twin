"""The task planner.

Turns a high-level instruction ("pick Box-A and deliver it to the loading zone")
into an ordered list of low-level actions the robot controller can execute, and
checks up front whether the robot has the charge to finish the job.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Set, Tuple

from . import energy
from .embodiment import AIR, GROUND, MobilityProfile, lift_ticks, step_ticks
from .models import (
    CONFIG,
    Action,
    ActionType,
    BoxStatus,
    Cell,
    LogCategory,
    LogLevel,
    TaskType,
    manhattan,
)


class PlanningError(Exception):
    """Raised when a task cannot be turned into an executable plan."""


class TaskPlanner:
    def __init__(self, twin: Any) -> None:
        self.twin = twin
        self.plans_created = 0

    # ------------------------------------------------------------------ #
    # Target resolution
    # ------------------------------------------------------------------ #
    @staticmethod
    def _coordinates(spec: Any) -> Optional[Cell]:
        """The cell an explicit coordinate spec names — {"x": .., "y": ..},
        [x, y] or "12,4" — or None if `spec` isn't one."""
        if isinstance(spec, dict) and "x" in spec and "y" in spec:
            return (int(spec["x"]), int(spec["y"]))
        if isinstance(spec, (list, tuple)) and len(spec) == 2:
            return (int(spec[0]), int(spec[1]))
        if isinstance(spec, str) and "," in spec:
            try:
                parts = [int(p.strip()) for p in spec.split(",")]
                return (parts[0], parts[1])
            except (ValueError, IndexError):
                return None
        return None

    def check_location(self, spec: Any) -> None:
        """Raise PlanningError unless `spec` names a place that exists — a cell
        on the floor, a zone or a box — whichever body would go there.
        resolve_target adds the body-dependent part: a cell it can reach."""
        if spec is None:
            raise PlanningError("No destination given")
        warehouse = self.twin.warehouse
        cell = self._coordinates(spec)
        if cell is not None:
            if not warehouse.is_inside(*cell):
                raise PlanningError(f"Coordinates {cell} are outside the warehouse")
        elif warehouse.resolve_zone(str(spec)) is None and self.twin.find_box(str(spec)) is None:
            raise PlanningError(f"Unknown location '{spec}'")

    def resolve_target(
        self,
        spec: Any,
        origin: Cell,
        blocked: Optional[Set[Cell]] = None,
        prefer_free: bool = False,
        profile: Optional[MobilityProfile] = None,
        layer: str = GROUND,
        avoid: Optional[Set[Cell]] = None,
    ) -> Tuple[Cell, str]:
        """Turn a location specification into a concrete grid cell that a robot
        with `profile` can use on `layer` (no profile: any drivable cell).

        `blocked` cells are walls: never the cell chosen and never a way
        through. `avoid` cells (other robots' cells, on the new floor) are
        never the cell chosen either, but a route may pass them: they are
        traffic, which execution waits out or replans around."""
        warehouse = self.twin.warehouse
        nav = self.twin.navigation
        blocked = set(blocked or ())
        avoid = set(avoid or ())

        if spec is None:
            raise PlanningError("No destination given")

        # Explicit coordinates: {"x": .., "y": ..} or "12,4"
        cell = self._coordinates(spec)
        if cell is not None:
            if not warehouse.is_inside(*cell):
                raise PlanningError(f"Coordinates {cell} are outside the warehouse")
            resolved = cell if warehouse.may_stop(cell, profile, layer) \
                else warehouse.nearest_walkable(cell, blocked | avoid, profile=profile, layer=layer)
            if resolved is None:
                raise PlanningError(f"No drivable cell near {cell}")
            return resolved, warehouse.label_for_cell(resolved)

        # A named zone.
        zone = warehouse.resolve_zone(str(spec))
        if zone is not None:
            prefer = None
            if prefer_free:
                # A shipped box has left on a truck: its dock cell is free again.
                occupied = {b.position for b in self.twin.boxes.values() if b.status is not BoxStatus.SHIPPED}
                prefer = {c for c in zone.cells if c not in occupied}
            cells = [c for c in zone.cells if c not in avoid]
            chosen = nav.best_cell_in_zone(cells, origin, blocked=blocked, prefer=prefer,
                                           profile=profile, layer=layer)
            if chosen is None:
                raise PlanningError(f"{zone.label} is not reachable right now")
            return chosen, zone.label

        # A box name or id.
        box = self.twin.find_box(str(spec))
        if box is not None:
            return box.position, box.name

        raise PlanningError(f"Unknown location '{spec}'")

    # ------------------------------------------------------------------ #
    # Battery estimation
    # ------------------------------------------------------------------ #
    def estimate_battery(self, robot: Any, waypoints: List[Cell], handling_ops: int = 0,
                         actions: Optional[List[Action]] = None, task: Optional[Any] = None) -> float:
        """Estimate battery percentage required to drive a route and handle
        boxes: 1% a cell plus a cost per box on classic. On the new floor it is
        the §5.5 energy (backend/energy.py) of the planned `actions` when given
        (_steps_wh: loaded legs, lifts and handling time), else of driving the
        route through `waypoints` at the body's speed."""
        if energy.has_battery(robot.mobility) and actions is not None:
            used = self._steps_wh(robot, actions, task)
            return round(energy.wh_to_pct(robot.mobility, used), 2) + float(CONFIG["BATTERY_RESERVE"])
        nav = self.twin.navigation
        origin = robot.position
        total_cells = 0
        for waypoint in waypoints:
            distance = nav.distance(origin, waypoint, profile=robot.mobility, layer=robot.layer)
            if distance is None:
                distance = abs(origin[0] - waypoint[0]) + abs(origin[1] - waypoint[1])
            total_cells += distance
            origin = waypoint
        if energy.has_battery(robot.mobility):
            used = energy.route_wh(robot.mobility, total_cells)
            return round(energy.wh_to_pct(robot.mobility, used), 2) + float(CONFIG["BATTERY_RESERVE"])
        movement_cost = math.ceil(total_cells / CONFIG["BATTERY_DRAIN_MOVES"])
        handling_cost = handling_ops * CONFIG["BATTERY_PICK_COST"]
        return float(movement_cost + handling_cost + CONFIG["BATTERY_RESERVE"])

    def _steps_wh(self, robot: Any, actions: List[Action], task: Optional[Any]) -> float:
        """The Wh a ground robot's plan takes (spec §5.5), step by step as the
        simulator bills it: each leg at the moving rate and the body's speed —
        a loaded leg at the loaded rate and loaded speed —, each LIFT_TO's
        m × g × Δh for the load (its declared weight, all the planner knows)
        plus the carriage, and every handling step's time (its start tick
        included) at the idle rate."""
        # Deferred: the simulator's module imports reach back to this one.
        from .simulator import DELIVER_TICKS, PICK_TICKS, TIMED_BY_TICKS

        twin, profile = self.twin, robot.mobility
        timed_by_ticks = task is not None and task.type in TIMED_BY_TICKS

        def timed(step: str) -> int:
            try:
                return step_ticks(profile, step)
            except ValueError:  # a body with no timing for it: the step itself will say so
                return 0

        def handling(step: str, classic_ticks: int) -> int:
            seconds = profile.grasp_s if step == "GRASP" else profile.place_s
            return classic_ticks if timed_by_ticks or seconds is None else timed(step)

        here, height = robot.position, robot.lift_height_m
        load = twin.find_box(robot.carrying_box) if robot.carrying_box else None
        carrying = load is not None
        wh, idle_ticks = 0.0, 0
        for action in actions:
            kind = action.type
            if kind is ActionType.NAVIGATE and action.target is not None:
                cells = twin.navigation.distance(here, action.target, allow_goal_adjacent=False,
                                                 profile=profile, layer=robot.layer)
                wh += energy.route_wh(profile, manhattan(here, action.target) if cells is None else cells,
                                      loaded=carrying)
                here = action.target
            elif kind in (ActionType.PICK, ActionType.GRASP):
                ticks = handling("GRASP", PICK_TICKS) if kind is ActionType.PICK else timed("GRASP")
                idle_ticks += 1 + ticks
                load, carrying = twin.find_box(action.box_id) if action.box_id else None, True
            elif kind in (ActionType.DELIVER, ActionType.PLACE, ActionType.PLACE_ON_CONVEYOR):
                ticks = handling("PLACE", DELIVER_TICKS) if kind is ActionType.DELIVER \
                    else timed(kind.value if kind is ActionType.PLACE_ON_CONVEYOR else "PLACE")
                idle_ticks += 1 + ticks
                load, carrying = None, False
            elif kind is ActionType.LIFT_TO:
                slot = twin.warehouse.slot(action.slot_id) if action.slot_id else None
                target = slot.height_m if slot is not None else float(action.params.get("height_m", 0.0))
                idle_ticks += 1 + lift_ticks(profile, height, target)
                wh += energy.lift_wh(profile, load.declared_weight_kg if load is not None else 0.0, target - height)
                height = target
            elif kind is ActionType.LOWER:
                idle_ticks += 1 + lift_ticks(profile, height, 0.0)
                height = 0.0
        # Idle is × 0.3 of the moving rate whether or not the robot holds a load.
        return wh + idle_ticks * energy.ground_wh_per_tick(profile, moving=False, loaded=False)

    # ------------------------------------------------------------------ #
    # Planning
    # ------------------------------------------------------------------ #
    def plan(self, task: Any, robot: Any) -> List[Action]:
        """Build the action list for ``task`` executed by ``robot``."""
        logger = self.twin.logger
        logger.info(
            LogCategory.PLANNER,
            f"Planning {task.type.value} for {robot.name}",
            task_id=task.id,
            robot_id=robot.id,
        )

        # Every target is resolved for this robot's own body and layer. On the
        # new floor the cells other robots stand on are traffic, not walls:
        # execution waits them out, replans around them or sidesteps, so a
        # robot in a one-lane aisle doesn't fail a job at plan time. They are
        # still never chosen as a zone cell to go to (`avoid`). A robot with no
        # floor profile keeps them as `blocked`, as classic planning always has.
        others = self.twin.other_robot_cells(robot.id)
        route: Dict[str, Any] = {"profile": robot.mobility, "layer": robot.layer}
        if robot.mobility is None:
            blocked = others
        else:
            blocked = set()
            route["avoid"] = others
        actions: List[Action] = []
        waypoints: List[Cell] = []
        handling_ops = 0

        if task.type in (TaskType.PICK_AND_DELIVER, TaskType.MOVE_BOX):
            box = self.twin.find_box(task.box_id)
            if box is None:
                raise PlanningError(f"Box '{task.box_id}' does not exist")
            pick_cell, pick_label = self.resolve_target(box.id, robot.position, blocked, **route)
            drop_cell, drop_label = self.resolve_target(
                task.destination, pick_cell, blocked, prefer_free=True, **route
            )
            logger.info(
                LogCategory.PLANNER,
                f"Source {box.name} found at ({pick_cell[0]},{pick_cell[1]})",
                task_id=task.id,
                robot_id=robot.id,
                box_id=box.id,
            )
            logger.info(
                LogCategory.PLANNER,
                f"Destination {drop_label} found at ({drop_cell[0]},{drop_cell[1]})",
                task_id=task.id,
                robot_id=robot.id,
            )
            actions = [
                Action(ActionType.NAVIGATE, f"Navigate to {box.name}", pick_cell, pick_label, box.id),
                Action(ActionType.PICK, f"Pick {box.name}", pick_cell, pick_label, box.id),
                Action(ActionType.NAVIGATE, f"Carry {box.name} to {drop_label}", drop_cell, drop_label, box.id),
                Action(ActionType.DELIVER, f"Deliver {box.name} at {drop_label}", drop_cell, drop_label, box.id),
            ]
            waypoints = [pick_cell, drop_cell]
            handling_ops = 2

        elif task.type == TaskType.BATCH_DELIVER:
            # Same destination, multiple boxes — the robot still only
            # ever carries one at a time (Robot.carrying_box is a single
            # id, not a list), so this is NAVIGATE/PICK/NAVIGATE/DELIVER
            # repeated per box under one task id, not simultaneous
            # carrying. See TaskType.BATCH_DELIVER's docstring in models.py.
            for box_id in task.box_ids:
                box = self.twin.find_box(box_id)
                if box is None:
                    raise PlanningError(f"Box '{box_id}' does not exist")
                pick_cell, pick_label = self.resolve_target(box.id, robot.position, blocked, **route)
                drop_cell, drop_label = self.resolve_target(
                    task.destination, pick_cell, blocked, prefer_free=True, **route
                )
                actions.extend([
                    Action(ActionType.NAVIGATE, f"Navigate to {box.name}", pick_cell, pick_label, box.id),
                    Action(ActionType.PICK, f"Pick {box.name}", pick_cell, pick_label, box.id),
                    Action(ActionType.NAVIGATE, f"Carry {box.name} to {drop_label}", drop_cell, drop_label, box.id),
                    Action(ActionType.DELIVER, f"Deliver {box.name} at {drop_label}", drop_cell, drop_label, box.id),
                ])
                waypoints.extend([pick_cell, drop_cell])
                handling_ops += 2

        elif task.type == TaskType.MOVE_ROBOT:
            cell, label = self.resolve_target(task.destination, robot.position, blocked, **route)
            actions = [Action(ActionType.NAVIGATE, f"Navigate to {label}", cell, label)]
            waypoints = [cell]

        elif task.type == TaskType.MIXED_MAINTENANCE_MISSION:
            # The robot's role in the mission: physically go inspect the
            # zone the agent recommended. The agent's recommendation and
            # the operator's sign-off are logged by TaskManager
            # (start_task/complete_task), not planned as actions here —
            # they're not physical steps a robot executes.
            cell, label = self.resolve_target(task.destination, robot.position, blocked, **route)
            actions = [Action(ActionType.NAVIGATE, f"Inspect {label}", cell, label)]
            waypoints = [cell]

        elif task.type == TaskType.PICK_BOX:
            box = self.twin.find_box(task.box_id)
            if box is None:
                raise PlanningError(f"Box '{task.box_id}' does not exist")
            pick_cell, pick_label = self.resolve_target(box.id, robot.position, blocked, **route)
            actions = [
                Action(ActionType.NAVIGATE, f"Navigate to {box.name}", pick_cell, pick_label, box.id),
                Action(ActionType.PICK, f"Pick {box.name}", pick_cell, pick_label, box.id),
            ]
            waypoints = [pick_cell]
            handling_ops = 1

        elif task.type == TaskType.DELIVER_BOX:
            box = self.twin.find_box(task.box_id)
            if box is None:
                raise PlanningError(f"Box '{task.box_id}' does not exist")
            if robot.carrying_box == box.id:
                drop_cell, drop_label = self.resolve_target(
                    task.destination, robot.position, blocked, prefer_free=True, **route
                )
                actions = [
                    Action(ActionType.NAVIGATE, f"Carry {box.name} to {drop_label}", drop_cell, drop_label, box.id),
                    Action(ActionType.DELIVER, f"Deliver {box.name} at {drop_label}", drop_cell, drop_label, box.id),
                ]
                waypoints = [drop_cell]
                handling_ops = 1
            else:
                pick_cell, pick_label = self.resolve_target(box.id, robot.position, blocked, **route)
                drop_cell, drop_label = self.resolve_target(
                    task.destination, pick_cell, blocked, prefer_free=True, **route
                )
                actions = [
                    Action(ActionType.NAVIGATE, f"Navigate to {box.name}", pick_cell, pick_label, box.id),
                    Action(ActionType.PICK, f"Pick {box.name}", pick_cell, pick_label, box.id),
                    Action(ActionType.NAVIGATE, f"Carry {box.name} to {drop_label}", drop_cell, drop_label, box.id),
                    Action(ActionType.DELIVER, f"Deliver {box.name} at {drop_label}", drop_cell, drop_label, box.id),
                ]
                waypoints = [pick_cell, drop_cell]
                handling_ops = 2

        elif task.type == TaskType.CHARGE_ROBOT:
            actions, waypoints = self._charge_plan(robot, blocked, route)

        else:
            from .jobs import JOB_SPECS  # deferred: jobs.py imports this module

            spec = JOB_SPECS.get(task.type)
            if spec is None or spec.plan is None:
                raise PlanningError(f"{task.type.value} does not need a movement plan")
            # A job's targets (slot faces, station drops, staging and dock
            # cells) get the same `blocked`: empty on the new floor (above).
            actions, waypoints, handling_ops = spec.plan(self, task, robot, blocked)

        # ---- battery-aware planning ---------------------------------- #
        # A mains-powered arm has no battery to plan for, and a drone gets a
        # hard energy gate instead of a detour (spec §5.5, §10.1).
        flies = robot.mobility is not None and robot.mobility.is_air
        if task.type != TaskType.CHARGE_ROBOT and waypoints and not robot.mains_powered and not flies:
            required = self.estimate_battery(robot, waypoints, handling_ops, actions=actions, task=task)
            task.battery_estimate = required
            if robot.battery < required:
                charge_cell, charge_label = self.resolve_target(
                    energy.charger_zone(robot.mobility), robot.position, blocked, **route
                )
                logger.warning(
                    LogCategory.BATTERY,
                    f"{robot.name} has {robot.battery:.0f}% but needs ~{required:.0f}% — "
                    f"routing via {charge_label} first",
                    task_id=task.id,
                    robot_id=robot.id,
                )
                actions = [
                    Action(ActionType.NAVIGATE, f"Detour to {charge_label}", charge_cell, charge_label),
                    Action(ActionType.CHARGE, "Charge before starting the task", charge_cell, charge_label),
                ] + actions
                task.recharged_before_start = True
            else:
                logger.debug(
                    LogCategory.BATTERY,
                    f"{robot.name} battery check passed: {robot.battery:.0f}% available, "
                    f"~{required:.0f}% required",
                    task_id=task.id,
                    robot_id=robot.id,
                )

        actions.append(Action(ActionType.COMPLETE, "Complete task"))
        self.plans_created += 1
        logger.info(
            LogCategory.PLANNER,
            f"Plan for {task.id} contains {len(actions)} actions",
            task_id=task.id,
            robot_id=robot.id,
            data={"actions": [a.description for a in actions]},
        )
        return actions

    def charger_cell(self, robot: Any, blocked: Set[Cell],
                    route: Optional[Dict[str, Any]] = None) -> Tuple[Cell, str]:
        """The charger cell `robot` goes to. A drone lands on a pad cell no
        grounded robot holds (spec §5.5), the nearest such one; a hover above a
        taken cell doesn't count as free, and with none free this raises
        PlanningError."""
        zone = energy.charger_zone(robot.mobility)
        if robot.mobility is not None and robot.mobility.is_air:
            blocked = set(blocked) | set(self.twin.robot_cells(GROUND))
            if robot.layer == GROUND:
                blocked -= {robot.position}  # its own cell is free; a hover above someone else's isn't
        route = route or {"profile": robot.mobility, "layer": robot.layer}
        return self.resolve_target(zone, robot.position, blocked, **route)

    def _charge_plan(self, robot: Any, blocked: Set[Cell], route: Dict[str, Any]) -> Tuple[List[Action], List[Cell]]:
        """CHARGE_ROBOT: to the robot's charger and charge. A drone charges on
        its pad (spec §5.5): it lands on a pad cell no grounded robot holds."""
        cell, label = self.charger_cell(robot, blocked, route)
        actions = [Action(ActionType.NAVIGATE, f"Navigate to {label}", cell, label)]
        if robot.layer == AIR:
            actions.append(Action(ActionType.LAND, f"Land on {label}", cell, label))
        actions.append(Action(ActionType.CHARGE, "Charge to 100%", cell, label))
        return actions, [cell]

    def stats(self) -> Dict[str, int]:
        return {"plans_created": self.plans_created}
