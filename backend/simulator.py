"""The simulation engine.

One :meth:`Simulator.tick` advances the whole warehouse by ``TICK_DT`` logical
seconds: queued tasks get dispatched, every robot executes one step of its plan,
collisions are resolved, batteries drain, the digital twin is synchronised and a
frame is pushed to the dashboard.

``tick`` is deliberately callable on its own so tests can drive the world
deterministically without threads.
"""
from __future__ import annotations

import random
import threading
import time
from typing import Any, Dict, List, Optional, Set, Tuple

from datetime import datetime

from . import energy, goods, people
from .eligibility import reach_ok, robot_eligibility
from .embodiment import AIR, GROUND, HOVER_CLEARANCE_M, lift_ticks, seconds_to_ticks, step_ticks
from .goods import carton_weight_kg
from .maintenance import maintenance_reason
from .models import (
    CONFIG,
    ActionType,
    Cell,
    CellType,
    BoxKind,
    BoxStatus,
    EventType,
    LogCategory,
    LogLevel,
    OperatorStatus,
    PRIORITY_RANK,
    Priority,
    RobotStatus,
    SimulationStatus,
    TaskStatus,
    TaskType,
    cell_dict,
    manhattan,
)

PICK_TICKS = 4
DELIVER_TICKS = 4

#: The job types that predate physical durations: their PICK and DELIVER keep
#: PICK_TICKS / DELIVER_TICKS on every floor. The PICK and DELIVER of every
#: newer job type take the body's grasp_s / place_s (spec §5.4).
TIMED_BY_TICKS = frozenset({
    TaskType.PICK_AND_DELIVER, TaskType.PICK_BOX, TaskType.DELIVER_BOX,
    TaskType.MOVE_BOX, TaskType.BATCH_DELIVER,
})

#: Job steps with a physical duration, run by Simulator._act_step.
STEP_ACTIONS = frozenset({
    ActionType.LIFT_TO, ActionType.LOWER, ActionType.TAKEOFF, ActionType.LAND,
    ActionType.GRASP, ActionType.PLACE, ActionType.PLACE_ON_CONVEYOR, ActionType.WAIT_CLEAR,
})

#: The conditions a WAIT_CLEAR step can wait out.
WAIT_CLEAR_REASONS = ("CONVEYOR_OCCUPIED", "AWAITING_ITEM")

#: Bodies whose grasp can miss (GRASP_FAIL_RISK), and how often they retry.
GRASPING_CLASSES = frozenset({"ARM", "PICKER"})
GRASP_RETRIES = 2

#: Bodies that won't drive into a zone with a person in it (PERSON_IN_AISLE).
AISLE_RULE_CLASSES = frozenset({"FORKLIFT", "HEAVY_HAULER"})

#: The actions a supervised body (the humanoid) holds still for when it is
#: unsupervised: everything that moves it or what it holds.
SUPERVISED_ACTIONS = frozenset({ActionType.NAVIGATE, ActionType.PICK, ActionType.DELIVER}) | STEP_ACTIONS


class Simulator:
    def __init__(self, twin: Any, broadcaster: Optional[Any] = None) -> None:
        self.twin = twin
        self.broadcaster = broadcaster
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self.dt = CONFIG["TICK_DT"]
        # Robot id -> the tick before which a new-floor robot's auto-charge
        # isn't requested again (see _auto_charge).
        self._charge_retry: Dict[str, int] = {}

    # ------------------------------------------------------------------ #
    # Thread control
    # ------------------------------------------------------------------ #
    def start_thread(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="simulation-loop", daemon=True)
        self._thread.start()

    def stop_thread(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)

    def _run(self) -> None:
        while not self._stop.is_set():
            started = time.time()
            try:
                if self.twin.simulation_status == SimulationStatus.RUNNING:
                    self.tick()
            except Exception as exc:  # keep the loop alive, but say so loudly
                self.twin.logger.critical(
                    LogCategory.SYSTEM, f"Simulation tick failed: {exc!r}"
                )
            speed = max(0.1, float(self.twin.simulation_speed or 1.0))
            budget = self.dt / speed
            elapsed = time.time() - started
            time.sleep(max(0.005, budget - elapsed))

    # ------------------------------------------------------------------ #
    # Public controls
    # ------------------------------------------------------------------ #
    def start(self) -> None:
        if self.twin.simulation_status == SimulationStatus.EMERGENCY_STOP:
            self.twin.release_emergency_stop()
            return
        self.twin.set_simulation_status(
            SimulationStatus.RUNNING, "Simulation started", EventType.SIMULATION_STARTED
        )

    def pause(self) -> None:
        self.twin.set_simulation_status(
            SimulationStatus.PAUSED, "Simulation paused", EventType.SIMULATION_PAUSED,
            level=LogLevel.WARNING,
        )

    def stop(self) -> None:
        for robot in self.twin.robots.values():
            robot.move_accumulator = 0.0
        self.twin.set_simulation_status(
            SimulationStatus.STOPPED, "Simulation stopped", EventType.SIMULATION_STOPPED,
            level=LogLevel.WARNING,
        )

    def set_speed(self, speed: float) -> float:
        speed = float(speed)
        if speed <= 0:
            raise ValueError("Speed must be greater than zero")
        self.twin.simulation_speed = speed
        self.twin.logger.info(LogCategory.SYSTEM, f"Simulation speed set to {speed}x")
        return speed

    # ------------------------------------------------------------------ #
    # The tick
    # ------------------------------------------------------------------ #
    def tick(self) -> None:
        twin = self.twin
        with twin.lock:
            twin.tick_count += 1
            twin.simulation_time = round(twin.simulation_time + self.dt, 3)

            twin.scheduler.tick()
            people.update_transits(twin)  # walks end before robots decide who is on the walkway
            twin.tasks.dispatch()

            distance_before = {robot.id: robot.total_distance for robot in twin.robots.values()}
            for robot in self._execution_order():
                try:
                    self._tick_robot(robot)
                except Exception as exc:
                    robot.last_error = repr(exc)
                    robot.set_status(RobotStatus.ERROR)
                    twin.events.emit(
                        EventType.ROBOT_ERROR,
                        f"{robot.name} controller error: {exc}",
                        category=LogCategory.ROBOT,
                        level=LogLevel.ERROR,
                        robot_id=robot.id,
                    )
                    task = twin.tasks.get(robot.current_task) if robot.current_task else None
                    if task is not None and not task.is_terminal:
                        twin.tasks.fail_task(task, f"Controller error: {exc}")

            if twin.equipment is not None:
                twin.equipment.tick()  # the conveyor and sorter move after robots place items
            self._detect_collisions()
            self._apply_energy(distance_before)
            self._auto_charge()
            self._check_maintenance()
            self._tick_fleet()
            if twin.tick_count % CONFIG["AUTHORIZATION_CHECK_EVERY_TICKS"] == 0:
                self._check_authorization_changes()
            if twin.tick_count % CONFIG["SHIFT_CHECK_EVERY_TICKS"] == 0:
                self._check_operator_shifts()
            twin.synchronize()
            twin.record_history()

        self._publish()

    def _execution_order(self) -> List[Any]:
        """Higher-priority missions move first, which also decides right of way."""
        twin = self.twin

        def key(robot: Any) -> Tuple[int, str]:
            task = twin.tasks.get(robot.current_task) if robot.current_task else None
            rank = PRIORITY_RANK[task.priority] if task else -1
            return (-rank, robot.id)

        return sorted(twin.robots.values(), key=key)

    # ------------------------------------------------------------------ #
    # Per-robot controller
    # ------------------------------------------------------------------ #
    def _tick_robot(self, robot: Any) -> None:
        twin = self.twin
        if robot.is_halted:
            return

        task = twin.tasks.get(robot.current_task) if robot.current_task else None

        if task is None:
            robot.activity = None
            if robot.status == RobotStatus.CHARGING:
                self._continue_idle_charge(robot)
            elif robot.status != RobotStatus.IDLE:
                robot.set_status(RobotStatus.IDLE)
                robot.clear_path()
            return

        if task.status == TaskStatus.PAUSED:
            return
        if task.is_terminal:
            robot.current_task = None
            robot.activity = None
            robot.clear_path()
            robot.set_status(RobotStatus.IDLE)
            return
        if task.status == TaskStatus.ASSIGNED:
            twin.tasks.start_task(task)

        action = task.current_action
        if action is None:
            twin.tasks.complete_task(task)
            return
        robot.activity = action.type.value
        if action.type in SUPERVISED_ACTIONS and self._supervision_hold(robot, task):
            return

        if action.type == ActionType.NAVIGATE:
            self._act_navigate(robot, task, action)
        elif action.type == ActionType.PICK:
            self._act_pick(robot, task, action)
        elif action.type == ActionType.DELIVER:
            self._act_deliver(robot, task, action)
        elif action.type == ActionType.CHARGE:
            self._act_charge(robot, task, action)
        elif action.type in STEP_ACTIONS:
            self._act_step(robot, task, action)
        elif action.type == ActionType.COMPLETE:
            action.done = True
            twin.tasks.complete_task(task)
        else:  # WAIT
            action.done = True
            self._advance(task)

    def _advance(self, task: Any) -> None:
        current = task.current_action
        if current is not None:
            current.done = True
        task.action_index += 1

    # ---- navigate ----------------------------------------------------- #
    def _act_navigate(self, robot: Any, task: Any, action: Any) -> None:
        twin = self.twin
        target = action.target
        if target is None:
            self._advance(task)
            return

        if robot.position == target:
            robot.clear_path()
            twin.logger.info(
                LogCategory.ROBOT,
                f"{robot.name} reached {action.target_name or 'target'}",
                robot_id=robot.id,
                task_id=task.id,
                position=cell_dict(robot.position),
            )
            self._advance(task)
            return

        if not action.started:
            action.started = True
            twin.tasks.set_status(
                task,
                TaskStatus.TRANSPORTING if robot.carrying_box else TaskStatus.IN_PROGRESS,
                action.description,
            )
            if robot.mobility is not None:
                self._emit_step(robot, task, action)

        if not robot.current_path or robot.target_position != target:
            if not self._plan_path(robot, task, target, action.target_name, replan=False):
                return

        robot.set_status(RobotStatus.DELIVERING if robot.carrying_box else RobotStatus.MOVING)

        next_cell = robot.next_cell
        if next_cell is None:
            robot.clear_path()
            return

        if self._crossing_wait(robot, task, next_cell):
            return
        if self._aisle_wait(robot, task, next_cell):
            return

        blocker = self._blocking_robot(robot, next_cell)
        if blocker is not None:
            risk = CONFIG["COLLISION_RISK"]
            if risk <= 0 or random.random() >= risk:
                self._handle_block(robot, task, blocker, target, action)
                return
            # The risk roll landed — this robot fails to avoid `blocker`
            # this tick (imperfect real-time sensing/timing) and drives
            # straight into the conflict instead of waiting/replanning.
            # If that actually lands both robots on the same cell,
            # _detect_collisions() catches it right after this tick's
            # per-robot loop and applies the real consequence — see
            # DigitalTwin.register_collision.
            twin.logger.warning(
                LogCategory.COLLISION,
                f"{robot.name} failed to avoid {blocker.name} in time — proceeding anyway",
                robot_id=robot.id,
                task_id=task.id,
                position=cell_dict(next_cell),
            )

        robot.wait_ticks = 0
        robot.blocked_by = None
        if not robot.ready_to_step(self.dt):
            return

        previous = robot.position
        robot.step_to(next_cell)
        self._announce_zone_entry(robot, task, previous)
        twin.statistics["distance_travelled"] += 1
        if robot.carrying_box:
            box = twin.find_box(robot.carrying_box)
            if box is not None:
                box.position = robot.position
                box.touch()
        twin.logger.debug(
            LogCategory.ROBOT,
            f"{robot.name} moved from ({previous[0]},{previous[1]}) "
            f"to ({robot.position[0]},{robot.position[1]})",
            robot_id=robot.id,
            task_id=task.id,
            position=cell_dict(robot.position),
        )
        twin.events.emit(
            EventType.ROBOT_MOVED,
            f"{robot.name} moved to ({robot.position[0]},{robot.position[1]})",
            category=LogCategory.NAVIGATION,
            level=LogLevel.DEBUG,
            robot_id=robot.id,
            task_id=task.id,
            position=cell_dict(robot.position),
            log=False,
        )
        self._apply_movement_battery(robot, task)

        if robot.position == target:
            robot.clear_path()
            twin.logger.info(
                LogCategory.ROBOT,
                f"{robot.name} reached {action.target_name or 'target'}",
                robot_id=robot.id,
                task_id=task.id,
                position=cell_dict(robot.position),
            )
            self._advance(task)

    def _plan_path(
        self,
        robot: Any,
        task: Any,
        target: Cell,
        target_name: Optional[str],
        replan: bool,
        extra_blocked: Optional[Set[Cell]] = None,
    ) -> bool:
        twin = self.twin
        blocked = twin.other_robot_cells(robot.id)
        if extra_blocked:
            blocked |= extra_blocked
        path = twin.navigation.find_path(
            robot.position, target, blocked=blocked, allow_goal_adjacent=False, is_replan=replan,
            profile=robot.mobility, layer=robot.layer,
        )
        if path is None:
            # Is the route only blocked by robots, or genuinely impossible?
            static_path = twin.navigation.find_path(
                robot.position, target, allow_goal_adjacent=False,
                profile=robot.mobility, layer=robot.layer,
            )
            if static_path is None:
                twin.events.emit(
                    EventType.PATH_NOT_FOUND,
                    f"No route from ({robot.position[0]},{robot.position[1]}) to "
                    f"({target[0]},{target[1]}) for {robot.name}",
                    category=LogCategory.NAVIGATION,
                    level=LogLevel.ERROR,
                    robot_id=robot.id,
                    task_id=task.id,
                )
                twin.tasks.fail_task(task, "No path available to the target")
                return False
            robot.set_status(RobotStatus.WAITING)
            twin.tasks.set_status(task, TaskStatus.BLOCKED, "Route temporarily blocked by traffic")
            return False

        robot.set_path(path, target_name)
        event = EventType.PATH_RECALCULATED if replan else EventType.PATH_CREATED
        twin.events.emit(
            event,
            f"{'Alternative' if replan else 'A*'} path for {robot.name} → "
            f"{target_name or f'({target[0]},{target[1]})'}: {len(path)} cells",
            category=LogCategory.NAVIGATION,
            robot_id=robot.id,
            task_id=task.id,
            data={"cells": len(path), "target": cell_dict(target)},
        )
        if replan:
            robot.replan_count += 1
            task.replans += 1
        return True

    # ---- traffic ------------------------------------------------------ #
    def _precedence(self, robot: Any) -> Tuple[int, str]:
        task = self.twin.tasks.get(robot.current_task) if robot.current_task else None
        rank = PRIORITY_RANK[task.priority] if task else -1
        return (rank, robot.id)

    def _blocking_robot(self, robot: Any, cell: Cell) -> Optional[Any]:
        mine = self._precedence(robot)
        for other in self.twin.robots.values():
            if other.id == robot.id or other.layer != robot.layer:
                continue  # only a robot on the same layer can be in the way
            if other.position == cell:
                return other
            if other.next_cell == cell and self._precedence(other) > mine:
                return other
        return None

    def _handle_block(self, robot: Any, task: Any, blocker: Any, target: Cell, action: Any) -> None:
        twin = self.twin
        robot.wait_ticks += 1
        robot.blocked_by = blocker.id

        if robot.wait_ticks == 1:
            robot.wait_events += 1
            twin.statistics["collisions_avoided"] += 1
            robot.set_status(RobotStatus.WAITING)
            twin.events.emit(
                EventType.COLLISION_AVOIDED,
                f"Potential collision at ({robot.next_cell[0]},{robot.next_cell[1]}): "
                f"{blocker.name} is in the way of {robot.name}",
                category=LogCategory.COLLISION,
                level=LogLevel.WARNING,
                robot_id=robot.id,
                task_id=task.id,
                position=cell_dict(robot.next_cell),
                data={"blocked_by": blocker.name},
            )
            twin.events.emit(
                EventType.ROBOT_WAITING,
                f"{robot.name} waiting for {blocker.name}",
                category=LogCategory.ROBOT,
                level=LogLevel.WARNING,
                robot_id=robot.id,
                task_id=task.id,
            )
            twin.tasks.set_status(task, TaskStatus.BLOCKED, f"Waiting for {blocker.name}")
            return

        head_on = blocker.next_cell == robot.position and blocker.position == robot.next_cell

        if robot.wait_ticks == CONFIG["REPLAN_AFTER_WAIT_TICKS"] or (head_on and robot.wait_ticks == 2):
            avoid = {blocker.position}
            if blocker.next_cell:
                avoid.add(blocker.next_cell)
            if self._plan_path(robot, task, target, action.target_name, replan=True, extra_blocked=avoid):
                robot.wait_ticks = 0
                robot.set_status(RobotStatus.DELIVERING if robot.carrying_box else RobotStatus.MOVING)
                twin.tasks.set_status(task, TaskStatus.IN_PROGRESS, "Alternative route accepted")
            return

        if robot.wait_ticks >= CONFIG["DEADLOCK_WAIT_TICKS"]:
            self._break_deadlock(robot, task, blocker)

    def _break_deadlock(self, robot: Any, task: Any, blocker: Any) -> None:
        """Step aside so the higher-priority robot can pass. A sidestep is a
        stop, so it never lands on the walkway (spec §5.2). It goes through
        step_to directly, so _crossing_wait and _aisle_wait would not get to
        hold it back: a forklift or hauler instead leaves out any cell that
        enters a zone with a person in it (it simply doesn't sidestep if that
        is every cell), and announces the zone it does enter."""
        twin = self.twin
        occupied = twin.other_robot_cells(robot.id)
        keeps_out = robot.mobility is not None and robot.mobility.embodiment_class in AISLE_RULE_CLASSES
        options = [
            cell
            for cell in twin.warehouse.neighbors(robot.position, robot.mobility, robot.layer)
            if cell not in occupied and cell != blocker.position
            and twin.warehouse.may_stop(cell, robot.mobility, robot.layer)
            and not (keeps_out and self._person_in_entered_zone(robot.position, cell))
        ]
        if not options:
            robot.wait_ticks = 0
            return
        options.sort(key=lambda c: -abs(c[0] - blocker.position[0]) - abs(c[1] - blocker.position[1]))
        sidestep = options[0]
        previous = robot.position
        robot.step_to(sidestep)
        self._announce_zone_entry(robot, task, previous)
        robot.clear_path()
        robot.wait_ticks = 0
        twin.events.emit(
            EventType.DEADLOCK_RESOLVED,
            f"{robot.name} stepped aside to ({sidestep[0]},{sidestep[1]}) to clear a deadlock "
            f"with {blocker.name}",
            category=LogCategory.COLLISION,
            level=LogLevel.WARNING,
            robot_id=robot.id,
            task_id=task.id,
            position=cell_dict(sidestep),
        )

    def _detect_collisions(self) -> None:
        """A real, natural overlap — under normal operation this should
        never actually fire (the traffic system in _handle_block/
        _break_deadlock actively avoids it); see
        DigitalTwin.force_collision for a deliberate way to trigger one
        on demand instead. Robots already in ERROR are excluded from the
        scan: DigitalTwin.register_collision already halted them and
        failed their task the moment they collided, so they'd otherwise
        get re-flagged as a "fresh" collision every single tick forever
        while they sit stacked on the same cell waiting to be Reset."""
        twin = self.twin
        seen: Dict[Tuple[str, int, int], Any] = {}
        for robot in twin.robots.values():
            if robot.status == RobotStatus.ERROR:
                continue
            key = (robot.layer, robot.position[0], robot.position[1])  # a drone over an AMR is no collision
            other = seen.get(key)
            if other is not None:
                twin.register_collision(robot.id, other.id)
            else:
                seen[key] = robot

    # ---- people and safety waits (spec §6, §10.3) --------------------- #
    def _crossing_wait(self, robot: Any, task: Any, next_cell: Cell) -> bool:
        """PERSON_ON_CROSSING: a robot about to step onto the pedestrian
        walkway (a ground robot at a crossing, or a drone about to cross it)
        waits while anyone is on the walkway, i.e. walking a route that crosses
        the strip (people.anyone_on_walkway) — a walk that stays on one side of
        it holds nobody up. True while it waits."""
        warehouse = self.twin.warehouse
        entering = (warehouse.cell_type(*next_cell) is CellType.WALKWAY
                    and warehouse.cell_type(*robot.position) is not CellType.WALKWAY)
        if entering and people.anyone_on_walkway(self.twin):
            self._safety_wait(robot, task, "PERSON_ON_CROSSING", next_cell)
            return True
        if robot.wait_reason == "PERSON_ON_CROSSING":
            self._safety_resume(robot, task)
        return False

    def _aisle_wait(self, robot: Any, task: Any, next_cell: Cell) -> bool:
        """PERSON_IN_AISLE (spec §6): a forklift or heavy hauler won't drive
        into a zone with a person standing in it. Only entering counts:
        someone stepping into the zone it is already in doesn't stop it. Route
        zones (patrol_loop) are paths, not places, so they never count."""
        if robot.mobility is None or robot.mobility.embodiment_class not in AISLE_RULE_CLASSES:
            return False
        if self._person_in_entered_zone(robot.position, next_cell):
            self._safety_wait(robot, task, "PERSON_IN_AISLE", next_cell)
            return True
        if robot.wait_reason == "PERSON_IN_AISLE":
            self._safety_resume(robot, task)
        return False

    def _person_in_entered_zone(self, origin: Cell, cell: Cell) -> bool:
        """Is anyone standing in a zone that stepping from `origin` to `cell` would enter?"""
        return any(people.people_in(self.twin, zone.key) for zone in self._entered_zones(origin, cell))

    def _entered_zones(self, origin: Cell, cell: Cell) -> List[Any]:
        """The places (non-route zones) holding `cell` that `origin` isn't in."""
        warehouse = self.twin.warehouse
        here = {zone.key for zone in warehouse.zones_of_cell(origin)}
        return [zone for zone in warehouse.zones_of_cell(cell)
                if "route" not in zone.attributes and zone.key not in here]

    def _announce_zone_entry(self, robot: Any, task: Any, previous: Cell) -> None:
        """A forklift or hauler entering a zone is a step of its own
        (ROBOT_STEP ENTER_ZONE), with who was in the zones it entered — what
        the evaluation's human_zone_clear reads."""
        if robot.mobility is None or robot.mobility.embodiment_class not in AISLE_RULE_CLASSES:
            return
        entered = self._entered_zones(previous, robot.position)
        if not entered:
            return
        present = [person.id for zone in entered for person in people.people_in(self.twin, zone.key)]
        self._step_event(robot, task, "ENTER_ZONE", f"entered {entered[0].label}",
                         box=self.twin.find_box(robot.carrying_box), zone=entered[0].key, present=present)

    def _supervision_hold(self, robot: Any, task: Any) -> bool:
        """SUPERVISOR_ABSENT (spec §6): a supervised body (the humanoid) moves
        only while a supervisor on shift, holding a valid credential in scope
        for its model and site, is in its zone or one sharing an edge with it.
        Otherwise it pauses where it is."""
        if robot.mobility is None or not robot.mobility.supervision:
            return False
        ok, _ = people.supervision_status(self.twin, robot)
        if not ok:
            self._safety_wait(robot, task, "SUPERVISOR_ABSENT", robot.position)
            return True
        if robot.wait_reason == "SUPERVISOR_ABSENT":
            self._safety_resume(robot, task)
        return False

    def _supervised(self, robot: Any) -> Optional[bool]:
        """ROBOT_STEP's supervision_ok: None for a body that needs no supervisor."""
        if robot.mobility is None or not robot.mobility.supervision:
            return None
        return people.supervision_status(self.twin, robot)[0]

    def _safety_wait(self, robot: Any, task: Any, reason: str, cell: Cell) -> None:
        """A physical wait — a decision, not a rejection: the robot holds
        WAITING with a reason code, announced once when the wait starts. It
        is not a traffic block, so it never replans or sidesteps. A new reason
        closes the previous wait first, so every wait pairs with a resume; a
        wait that outlasts SAFETY_WAIT_ESCALATE_S is escalated once."""
        robot.set_status(RobotStatus.WAITING)
        if robot.wait_reason == reason:
            self._escalate(robot, task)
            return
        if robot.wait_reason is not None:
            self.twin.end_safety_wait(robot, f"superseded by {reason}")
        robot.wait_reason = reason
        robot.wait_started_tick = self.twin.tick_count
        robot.wait_task_id = task.id
        robot.wait_cell = tuple(cell)
        robot.wait_escalated = False
        self.twin.events.emit(
            EventType.ROBOT_SAFETY_WAIT,
            f"{robot.name} waiting before ({cell[0]},{cell[1]}): {reason}",
            category=LogCategory.SAFETY,
            level=LogLevel.WARNING,
            robot_id=robot.id,
            task_id=task.id,
            position=cell_dict(cell),
            data={"reason": reason, "cell": cell_dict(cell)},
        )
        self.twin.tasks.set_status(task, TaskStatus.BLOCKED, f"Safety wait: {reason}")

    def _escalate(self, robot: Any, task: Any) -> None:
        """SAFETY_WAIT_ESCALATED, once, when a wait outlasts SAFETY_WAIT_ESCALATE_S
        (the orders panel shows it — spec §10.3)."""
        if robot.wait_escalated or robot.wait_started_tick is None:
            return
        waited = self.twin.tick_count - robot.wait_started_tick
        if waited < seconds_to_ticks(CONFIG["SAFETY_WAIT_ESCALATE_S"]):
            return
        robot.wait_escalated = True
        cell = robot.wait_cell or robot.position
        self.twin.events.emit(
            EventType.SAFETY_WAIT_ESCALATED,
            f"{robot.name} has waited {waited * self.dt:.0f} s ({robot.wait_reason}) — escalated",
            category=LogCategory.SAFETY,
            level=LogLevel.ERROR,
            robot_id=robot.id,
            task_id=task.id,
            position=cell_dict(cell),
            data={"reason": robot.wait_reason, "waited_s": round(waited * self.dt, 1), "cell": cell_dict(cell)},
        )

    def _safety_resume(self, robot: Any, task: Any) -> None:
        reason = robot.wait_reason
        self.twin.end_safety_wait(robot, "cleared")
        robot.set_status(RobotStatus.DELIVERING if robot.carrying_box else RobotStatus.MOVING)
        self.twin.tasks.set_status(
            task, TaskStatus.TRANSPORTING if robot.carrying_box else TaskStatus.IN_PROGRESS,
            f"{reason} cleared",
        )

    # ---- handling ----------------------------------------------------- #
    def _act_pick(self, robot: Any, task: Any, action: Any) -> None:
        twin = self.twin
        box = twin.find_box(action.box_id or task.box_id)
        if box is None:
            twin.tasks.fail_task(task, f"Box '{action.box_id}' vanished before pickup")
            return
        if robot.position != box.position:
            # The box moved; go and get it.
            action.target = box.position
            if not self._plan_path(robot, task, box.position, box.name, replan=True):
                return
            robot.set_status(RobotStatus.MOVING)
            return

        if not action.started:
            action.started = True
            robot.action_timer = 0
            robot.set_status(RobotStatus.PICKING)
            previous = box.set_status(BoxStatus.PICKING)
            twin.tasks.set_status(task, TaskStatus.PICKING, f"Picking {box.name}")
            twin.logger.info(
                LogCategory.ROBOT,
                f"{robot.name} started picking {box.name} ({previous.value} → PICKING)",
                robot_id=robot.id,
                task_id=task.id,
                box_id=box.id,
                position=cell_dict(robot.position),
            )
            if robot.mobility is not None:
                self._emit_step(robot, task, action)
            return

        robot.action_timer += 1
        if robot.action_timer < self._handling_ticks(robot, task, "GRASP", PICK_TICKS):
            return

        previous = box.set_status(BoxStatus.CARRIED)
        box.assigned_robot = robot.id
        box.assigned_task = task.id
        box.position = robot.position
        # Remembered only so a FALSE_SUCCESS_RISK fault (see _act_deliver)
        # has somewhere real to snap the box back to — not read anywhere
        # else.
        box._picked_from_position = box.position
        box.pick_count += 1
        robot.carrying_box = box.id
        robot.set_status(RobotStatus.CARRYING)
        if robot.mobility is None:  # the classic cost; the new floor pays per tick (spec §5.5)
            robot.consume_battery(CONFIG["BATTERY_PICK_COST"])
        twin.events.emit(
            EventType.BOX_PICKED,
            f"{robot.name} picked {box.name} ({previous.value} → CARRIED)",
            category=LogCategory.BOX,
            robot_id=robot.id,
            task_id=task.id,
            box_id=box.id,
            position=cell_dict(robot.position),
            data=self._load_data(robot, box) if robot.mobility is not None else None,
        )
        self._staging_handoff(robot, task, box, into_robot=True, arrived=True)
        self._check_battery_thresholds(robot, task)
        self._advance(task)

    def _act_deliver(self, robot: Any, task: Any, action: Any) -> None:
        twin = self.twin
        box = twin.find_box(action.box_id or task.box_id)
        if box is None:
            twin.tasks.fail_task(task, f"Box '{action.box_id}' vanished before delivery")
            return
        if robot.carrying_box != box.id:
            twin.tasks.fail_task(task, f"{robot.name} is not carrying {box.name}")
            return

        if not action.started:
            action.started = True
            robot.action_timer = 0
            robot.set_status(RobotStatus.DELIVERING)
            box.set_status(BoxStatus.DELIVERING)
            twin.tasks.set_status(task, TaskStatus.DELIVERING, f"Delivering {box.name}")
            twin.logger.info(
                LogCategory.ROBOT,
                f"{robot.name} started delivering {box.name} at {action.target_name}",
                robot_id=robot.id,
                task_id=task.id,
                box_id=box.id,
                position=cell_dict(robot.position),
            )
            if robot.mobility is not None:
                self._emit_step(robot, task, action)
            return

        robot.action_timer += 1
        if robot.action_timer < self._handling_ticks(robot, task, "PLACE", DELIVER_TICKS):
            return

        previous = box.set_status(BoxStatus.DELIVERED)
        # CONFIG["FALSE_SUCCESS_RISK"] (0.0 by default — see models.py):
        # the payload actually slips off the fork right at the final
        # release and lands back where it was picked up — no independent
        # sensor confirms a real seat at the destination, only the same
        # controller that now believes it succeeded. Everything below
        # this still runs exactly as if the delivery had gone perfectly
        # (status, counters, BOX_DELIVERED, task advancing) — a
        # confidently-wrong success, on purpose, that only eval_engine.
        # check_state_transition's after-the-fact before/after diff can
        # ever catch. A quiet DEBUG breadcrumb is left for anyone reading
        # the full log on purpose; it never raises the log level, so it
        # never reaches the dashboard's WARNING+ notification bell the
        # way a real fault would.
        false_success = random.random() < CONFIG.get("FALSE_SUCCESS_RISK", 0.0)
        if false_success:
            box.position = getattr(box, "_picked_from_position", box.position)
            twin.logger.debug(
                LogCategory.BOX,
                f"{box.name} delivery payload placement went unverified — "
                "internal state will not reflect the reported delivery",
                robot_id=robot.id,
                task_id=task.id,
                box_id=box.id,
            )
        else:
            box.position = robot.position
        box.destination = action.target_name or box.destination
        if action.params.get("ship"):
            box.set_status(BoxStatus.SHIPPED)  # loaded onto the outbound truck (LOAD_TRUCK)
        box.delivery_count += 1
        box.assigned_robot = None
        box.assigned_task = None
        robot.carrying_box = None
        robot.boxes_delivered += 1
        if robot.mobility is None:  # the classic cost; the new floor pays per tick (spec §5.5)
            robot.consume_battery(CONFIG["BATTERY_DELIVER_COST"])
        twin.statistics["boxes_delivered"] += 1
        twin.events.emit(
            EventType.BOX_DELIVERED,
            f"{box.name} delivered to {action.target_name} ({previous.value} → DELIVERED)",
            category=LogCategory.BOX,
            robot_id=robot.id,
            task_id=task.id,
            box_id=box.id,
            position=cell_dict(robot.position),
        )
        self._staging_handoff(robot, task, box, into_robot=False, arrived=not false_success)
        self._check_battery_thresholds(robot, task)
        self._advance(task)

    def _handling_ticks(self, robot: Any, task: Any, step: str, classic_ticks: int) -> int:
        """How long a PICK (step GRASP) or DELIVER (step PLACE) takes: the
        body's grasp_s / place_s for a new job type, PICK_TICKS / DELIVER_TICKS
        for the older ones and for any robot without a floor profile."""
        profile = robot.mobility
        if profile is None or task.type in TIMED_BY_TICKS:
            return classic_ticks
        if (profile.grasp_s if step == "GRASP" else profile.place_s) is None:
            return classic_ticks
        return step_ticks(profile, step)

    def _load_data(self, robot: Any, box: Any) -> Dict[str, Any]:
        """What a pick or lift reports: the load's kind and true and declared
        weights, against the body's limits — what payload_within_limit reads."""
        profile = robot.mobility
        return {
            "kind": box.kind.value, "true_weight_kg": box.true_weight_kg,
            "declared_weight_kg": box.declared_weight_kg, "max_payload_kg": profile.max_payload_kg,
            "max_shelf_level": profile.max_shelf_level, "embodiment_class": profile.embodiment_class,
        }

    def _staging_handoff(self, robot: Any, task: Any, box: Any, into_robot: bool, arrived: bool) -> None:
        """A robot picking from, or dropping at, a staging area hands the box
        over to or from that area (spec §8.3). `arrived` is False when the
        box never reached the receiver (a false-success delivery)."""
        twin = self.twin
        if twin.equipment is None or robot.mobility is None:
            return
        zone = next((z for z in twin.warehouse.zones_of_cell(robot.position)
                     if z.cell_type is CellType.STAGING), None)
        if zone is None:
            return
        observed = {"present": True, "weight_kg": box.true_weight_kg} if arrived else {"present": False}
        giver, receiver = (zone.key, robot.id) if into_robot else (robot.id, zone.key)
        twin.equipment.record(box, giver, receiver, robot.position,
                              {"present": True, "weight_kg": box.declared_weight_kg}, observed, task.id)

    # ---- job steps (spec §5.4) ----------------------------------------- #
    def _act_step(self, robot: Any, task: Any, action: Any) -> None:
        """One tick of a job step: hold for a physical wait; begin (check the
        step can happen, work out how many ticks it takes, announce it);
        wait out the duration; finish (make it so, and say what happened).
        A step that can't happen fails its task with the reason."""
        if self._step_hold(robot, task, action):
            return
        if not action.started:
            try:
                ticks = self._begin_step(robot, task, action)
            except ValueError as exc:
                self.twin.tasks.fail_task(task, f"{robot.name} {action.type.value} failed: {exc}")
                return
            if ticks is None:
                return  # its precondition isn't met yet: it waits
            action.started = True
            action.params["ticks"] = int(ticks)
            robot.action_timer = 0
            self._emit_step(robot, task, action)
            return
        robot.set_status(self._step_status(robot, action))
        robot.action_timer += 1
        if robot.action_timer < action.params.get("ticks", 0):
            return
        try:
            finished = self._finish_step(robot, task, action)
        except ValueError as exc:
            self.twin.tasks.fail_task(task, f"{robot.name} {action.type.value} failed: {exc}")
            return
        if finished:
            self._advance(task)

    def _step_status(self, robot: Any, action: Any) -> RobotStatus:
        if action.type is ActionType.GRASP:
            return RobotStatus.PICKING
        if action.type in (ActionType.PLACE, ActionType.PLACE_ON_CONVEYOR):
            return RobotStatus.DELIVERING
        if action.type is ActionType.WAIT_CLEAR:
            return RobotStatus.WAITING
        if action.type in (ActionType.LIFT_TO, ActionType.LOWER):
            return RobotStatus.CARRYING if robot.carrying_box else RobotStatus.PICKING
        return RobotStatus.MOVING

    def _emit_step(self, robot: Any, task: Any, action: Any) -> None:
        """ROBOT_STEP for `action` starting."""
        box = self.twin.find_box(robot.carrying_box or action.box_id)
        self._step_event(robot, task, action.type.value, action.description, level=action.level, box=box)

    def _step_event(self, robot: Any, task: Any, step: str, description: str, level: Optional[int] = None,
                    box: Optional[Any] = None, zone: Optional[str] = None,
                    present: Optional[List[str]] = None) -> None:
        """ROBOT_STEP: a step starts. It carries what the evaluation's safety
        checks read: who is next to the robot (`present`, default the people
        around it), whether it is supervised, the level, and the load's true
        and declared weights. `zone` defaults to the robot's own."""
        twin = self.twin
        if zone is None:
            here = twin.warehouse.zone_of_cell(robot.position)
            zone = here.key if here else None
        if present is None:
            present = [person.id for person in self._people_near(robot)]
        twin.events.emit(
            EventType.ROBOT_STEP,
            f"{robot.name}: {description}",
            category=LogCategory.ROBOT,
            level=LogLevel.DEBUG,
            robot_id=robot.id,
            task_id=task.id,
            box_id=box.id if box else None,
            position=cell_dict(robot.position),
            data={
                "step": step,
                "zone": zone,
                "people_present": list(present),
                "supervision_ok": self._supervised(robot),
                "level": level,
                "true_weight_kg": box.true_weight_kg if box else None,
                "declared_weight_kg": box.declared_weight_kg if box else None,
                "embodiment_class": robot.mobility.embodiment_class,
                "layer": robot.layer,
            },
        )

    def _people_near(self, robot: Any) -> List[Any]:
        """The people a robot working where it stands is next to: for an arm,
        everyone in its fenced pack cell; for anything else, everyone in a
        zone that holds its cell."""
        station = self.twin.warehouse.fixed_stations.get(robot.position)
        if station is not None and robot.mobility is not None and robot.mobility.is_fixed:
            return people.people_in(self.twin, station)
        return people.people_at(self.twin, robot.position)

    def _step_hold(self, robot: Any, task: Any, action: Any) -> bool:
        """A physical wait before or during a step (spec §6, §10.3): an arm
        pauses while a person is in its fenced pack cell (PERSON_IN_CELL), and
        while the conveyor is jammed at or upstream of its working cell."""
        station = self.twin.warehouse.fixed_stations.get(robot.position)
        if station is not None and robot.mobility is not None and robot.mobility.is_fixed:
            if people.people_in(self.twin, station):
                self._safety_wait(robot, task, "PERSON_IN_CELL", robot.position)
                return True
            if robot.wait_reason == "PERSON_IN_CELL":
                self._safety_resume(robot, task)
        work_cell = self._arm_work_cell(robot)
        if work_cell is not None:
            jam = self.twin.equipment.conveyor.jam_upstream_of(work_cell)
            if jam is not None:
                self._safety_wait(robot, task, "CONVEYOR_JAMMED", jam)
                return True
        if robot.wait_reason == "CONVEYOR_JAMMED":
            self._safety_resume(robot, task)
        return False

    def _arm_work_cell(self, robot: Any) -> Optional[Cell]:
        """The conveyor cell a fixed arm works; None for anything else."""
        twin = self.twin
        if twin.equipment is None or robot.mobility is None or not robot.mobility.is_fixed:
            return None
        station = twin.warehouse.fixed_stations.get(robot.position)
        return twin.equipment.arm_cells.get(station) if station else None

    def _begin_step(self, robot: Any, task: Any, action: Any) -> Optional[int]:
        """Check the step can start; returns its ticks, or None to wait."""
        if robot.mobility is None:
            raise ValueError("it has no physical body on this floor")
        begin = {
            ActionType.LIFT_TO: self._begin_lift, ActionType.LOWER: self._begin_lower,
            ActionType.TAKEOFF: self._begin_takeoff, ActionType.LAND: self._begin_land,
            ActionType.GRASP: self._begin_grasp, ActionType.PLACE: self._begin_place,
            ActionType.PLACE_ON_CONVEYOR: self._begin_place_on_conveyor,
            ActionType.WAIT_CLEAR: self._begin_wait_clear,
        }[action.type]
        ticks = begin(robot, task, action)
        if ticks is None:
            robot.set_status(RobotStatus.WAITING)
            return None
        robot.set_status(self._step_status(robot, action))
        if action.type is ActionType.GRASP:
            status = TaskStatus.PICKING
        elif action.type in (ActionType.PLACE, ActionType.PLACE_ON_CONVEYOR):
            status = TaskStatus.DELIVERING
        else:
            status = TaskStatus.TRANSPORTING if robot.carrying_box else TaskStatus.IN_PROGRESS
        self.twin.tasks.set_status(task, status, action.description)
        return ticks

    def _finish_step(self, robot: Any, task: Any, action: Any) -> bool:
        """Make the step so; False keeps it going (a retry, or still waiting)."""
        finish = {
            ActionType.LIFT_TO: self._finish_lift, ActionType.LOWER: self._finish_lower,
            ActionType.TAKEOFF: self._finish_takeoff, ActionType.LAND: self._finish_land,
            ActionType.GRASP: self._finish_grasp, ActionType.PLACE: self._finish_place,
            ActionType.PLACE_ON_CONVEYOR: self._finish_place_on_conveyor,
            ActionType.WAIT_CLEAR: self._finish_wait_clear,
        }[action.type]
        return finish(robot, task, action)

    def _slot_for(self, action: Any) -> Optional[Any]:
        if not action.slot_id:
            return None
        slot = self.twin.warehouse.slot(action.slot_id)
        if slot is None:
            raise ValueError(f"slot {action.slot_id} does not exist")
        return slot

    def _emit_effect(self, event: EventType, robot: Any, task: Any, message: str,
                     box: Optional[Any] = None, **data: Any) -> None:
        """LIFTED / LOWERED / PLACED: what a step physically did."""
        payload = dict(data)
        profile = robot.mobility
        payload.update(embodiment_class=profile.embodiment_class, max_shelf_level=profile.max_shelf_level,
                       max_payload_kg=profile.max_payload_kg)
        if box is not None:
            payload.update(box_id=box.id, kind=box.kind.value, true_weight_kg=box.true_weight_kg,
                           declared_weight_kg=box.declared_weight_kg)
        self.twin.events.emit(event, message, category=LogCategory.ROBOT, robot_id=robot.id, task_id=task.id,
                              box_id=box.id if box else None, position=cell_dict(robot.position), data=payload)

    # LIFT_TO / LOWER: the height difference ÷ lift_speed_mps
    def _begin_lift(self, robot: Any, task: Any, action: Any) -> int:
        profile = robot.mobility
        slot = self._slot_for(action)
        if slot is not None:
            if action.level is not None and action.level != slot.level:
                raise ValueError(f"level {action.level} does not match slot {slot.slot_id}, "
                                 f"which is on level {slot.level}")
            action.level = slot.level  # the step reports the level it resolved
        ok, reason = reach_ok(action.level, profile.max_shelf_level)
        if not ok:
            raise ValueError(reason)
        height = slot.height_m if slot else float(action.params.get("height_m", 0.0))
        if height > profile.max_lift_m + 1e-9:
            raise ValueError(f"{height:.1f} m is above its {profile.max_lift_m:.1f} m lift")
        action.params["height_m"] = height
        action.params["from_m"] = robot.lift_height_m
        return lift_ticks(profile, robot.lift_height_m, height)

    def _finish_lift(self, robot: Any, task: Any, action: Any) -> bool:
        height = action.params["height_m"]
        robot.lift_height_m = height
        box = self.twin.find_box(robot.carrying_box)
        # Raising the load and carriage costs m × g × Δh (spec §5.5).
        used = energy.lift_wh(robot.mobility, box.true_weight_kg if box else 0.0,
                              height - float(action.params.get("from_m", 0.0)))
        if used:
            robot.use_energy(energy.wh_to_pct(robot.mobility, used))
        self._emit_effect(EventType.LIFTED, robot, task,
                          f"{robot.name} lifted to level {action.level} ({height:.1f} m)",
                          box, level=action.level, height_m=height, slot=action.slot_id,
                          energy_wh=round(used, 3))
        if used:
            self._check_battery_thresholds(robot, task)
        return True

    def _begin_lower(self, robot: Any, task: Any, action: Any) -> int:
        return lift_ticks(robot.mobility, robot.lift_height_m, 0.0)

    def _finish_lower(self, robot: Any, task: Any, action: Any) -> bool:
        robot.lift_height_m = 0.0
        self._emit_effect(EventType.LOWERED, robot, task, f"{robot.name} lowered to the floor",
                          self.twin.find_box(robot.carrying_box), height_m=0.0)
        return True

    # TAKEOFF / LAND: takeoff_s / land_s, and only on the drone pad
    def _begin_takeoff(self, robot: Any, task: Any, action: Any) -> int:
        if not robot.mobility.is_air:
            raise ValueError("it cannot fly")
        if robot.layer != GROUND:
            raise ValueError("it is already flying")
        altitude = float(action.params.get("altitude_m", HOVER_CLEARANCE_M))
        ticks = step_ticks(robot.mobility, "TAKEOFF")  # before the layer changes: no timing leaves it grounded
        self.twin.set_robot_layer(robot.id, AIR, altitude_m=altitude)
        return ticks

    def _finish_takeoff(self, robot: Any, task: Any, action: Any) -> bool:
        return True

    def _begin_land(self, robot: Any, task: Any, action: Any) -> int:
        x, y = robot.position
        if robot.layer != AIR:
            raise ValueError("it is not flying")
        if self.twin.warehouse.cell_type(x, y) is not CellType.DRONE_PAD:
            raise ValueError(f"({x},{y}) is not a drone pad cell")
        if robot.position in self.twin.robot_cells(GROUND):
            raise ValueError(f"the pad cell ({x},{y}) is taken")
        return step_ticks(robot.mobility, "LAND")

    def _finish_land(self, robot: Any, task: Any, action: Any) -> bool:
        self.twin.set_robot_layer(robot.id, GROUND)
        return True

    # GRASP / PLACE: grasp_s / place_s
    def _in_slot(self, box: Any, slot: Any) -> bool:
        """Is `box` physically standing in `slot` right now?"""
        return (box.status in (BoxStatus.STORED, BoxStatus.RESERVED)
                and (box.true_slot or box.slot) == slot.slot_id and box.position == slot.cell)

    def _check_forks(self, robot: Any, slot: Any) -> None:
        """A forklift engages a slot only with its forks at the slot's height."""
        if robot.mobility.embodiment_class == "FORKLIFT" and abs(robot.lift_height_m - slot.height_m) > 0.01:
            raise ValueError(f"its forks are at {robot.lift_height_m:.1f} m but slot {slot.slot_id} "
                             f"is at {slot.height_m:.1f} m")

    def _begin_grasp(self, robot: Any, task: Any, action: Any) -> int:
        twin, params = self.twin, action.params
        if robot.carrying_box:
            raise ValueError(f"it is already holding {robot.carrying_box}")
        if "from_conveyor" in params:
            cell = tuple(params["from_conveyor"])
            if twin.equipment is None or cell not in twin.equipment.conveyor:
                raise ValueError(f"({cell[0]},{cell[1]}) is not a conveyor cell")
            if cell != self._arm_work_cell(robot) and manhattan(cell, robot.position) > 1:
                raise ValueError(f"conveyor cell ({cell[0]},{cell[1]}) is out of reach")
            item = twin.equipment.conveyor.item_at(cell)
            if item is None or (params.get("order_id") and item.order_id != params["order_id"]):
                raise ValueError(f"no item for {params.get('order_id')} on conveyor cell ({cell[0]},{cell[1]})")
            if item.stop_at != cell:
                # An item riding past would be gone before a grasp (or a retry) ends.
                raise ValueError(f"the item on conveyor cell ({cell[0]},{cell[1]}) is not being held there")
            params["item_id"] = item.box_id  # what it set out to take: finish checks it is still the one there
        elif "unit_from" in params:
            tote = twin.find_box(params["unit_from"])
            if tote is None:
                raise ValueError(f"tote {params['unit_from']} does not exist")
            if manhattan(tote.position, robot.position) > 1:
                raise ValueError(f"{tote.name} at ({tote.position[0]},{tote.position[1]}) is out of reach")
        else:
            box = twin.find_box(action.box_id)
            if box is None:
                raise ValueError(f"box {action.box_id} does not exist")
            slot = self._slot_for(action)
            if slot is not None:
                if robot.position not in slot.faces:
                    raise ValueError(f"it is not at a face of slot {slot.slot_id}")
                if not self._in_slot(box, slot):
                    raise ValueError(f"{box.name} is not in slot {slot.slot_id}")
                self._check_forks(robot, slot)
            elif box.position != robot.position:
                raise ValueError(f"{box.name} is at ({box.position[0]},{box.position[1]}), not where it stands")
        return step_ticks(robot.mobility, "GRASP")

    def _finish_grasp(self, robot: Any, task: Any, action: Any) -> bool:
        twin, params = self.twin, action.params
        if "from_conveyor" in params:
            cell = tuple(params["from_conveyor"])
            there = twin.equipment.conveyor.item_at(cell)
            if there is None or there.box_id != params.get("item_id"):
                raise ValueError(f"the item it was grasping is no longer on conveyor cell ({cell[0]},{cell[1]})")
        if robot.mobility.embodiment_class in GRASPING_CLASSES and twin.faults.roll("grasp_fail"):
            misses = int(params.get("misses", 0)) + 1
            params["misses"] = misses
            if misses > GRASP_RETRIES:
                raise ValueError(f"it missed the grasp {misses} times")
            robot.action_timer = 0
            twin.logger.warning(LogCategory.ROBOT, f"{robot.name} missed its grasp — retry {misses} of {GRASP_RETRIES}",
                                robot_id=robot.id, task_id=task.id, position=cell_dict(robot.position))
            return False
        if "from_conveyor" in params:
            box, _ = twin.equipment.take(tuple(params["from_conveyor"]), robot.id, task.id)
        elif "unit_from" in params:
            tote = twin.find_box(params["unit_from"])
            declared, true = goods.take_units(twin, tote, 1)
            box = twin.add_box(kind="ITEM", position=robot.position, sku=tote.sku, quantity=1,
                               weight=declared, true_weight_kg=true, order_id=params.get("order_id"))
        else:
            box = twin.find_box(action.box_id)
            if box.kind is BoxKind.PALLET and box.slot:
                goods.release(twin, box)  # a pallet leaves storage; a tote keeps its slot
        previous = box.set_status(BoxStatus.CARRIED)
        box.assigned_robot, box.assigned_task = robot.id, task.id
        box.position = robot.position
        box._picked_from_position = box.position
        box.pick_count += 1
        robot.carrying_box = box.id
        twin.events.emit(
            EventType.BOX_PICKED,
            f"{robot.name} grasped {box.name} ({previous.value} → CARRIED)",
            category=LogCategory.BOX,
            robot_id=robot.id,
            task_id=task.id,
            box_id=box.id,
            position=cell_dict(robot.position),
            data=self._load_data(robot, box),
        )
        return True

    def _slot_taken(self, slot: Any, box: Any) -> bool:
        holder = self.twin.stock.box_in(slot.slot_id)
        misplaced = any(other.true_slot == slot.slot_id for other in self.twin.boxes.values())
        return (holder is not None and holder != box.id) or misplaced

    def _begin_place(self, robot: Any, task: Any, action: Any) -> int:
        box = self.twin.find_box(robot.carrying_box)
        if box is None:
            raise ValueError("it is not holding anything")
        if "into_carton" in action.params:
            if box.order_id != action.params["into_carton"]:
                raise ValueError(f"{box.name} is for order {box.order_id}, not {action.params['into_carton']}")
        else:
            slot = self._slot_for(action)
            if slot is None:
                raise ValueError("it was given no slot to place into")
            if robot.position not in slot.faces:
                raise ValueError(f"it is not at a face of slot {slot.slot_id}")
            if box.kind.value != slot.kind:
                raise ValueError(f"a {box.kind.value} cannot go in {slot.kind} slot {slot.slot_id}")
            if self._slot_taken(slot, box):
                raise ValueError(f"slot {slot.slot_id} is taken")
            self._check_forks(robot, slot)
        return step_ticks(robot.mobility, "PLACE")

    def _wrong_level_slot(self, slot: Any, box: Any) -> Optional[Any]:
        """Where a WRONG_LEVEL fault puts a pallet: the free slot a level up
        (else down) in the same rack cell, if there is one."""
        for delta in (1, -1):
            other = self.twin.warehouse.slot_at(slot.cell, slot.level + delta)
            if other is not None and not self._slot_taken(other, box):
                return other
        return None

    def _finish_place(self, robot: Any, task: Any, action: Any) -> bool:
        twin = self.twin
        box = twin.find_box(robot.carrying_box)
        if "into_carton" in action.params:
            return self._pack_into_carton(robot, task, action, box)
        slot = self._slot_for(action)
        actual = slot
        if robot.mobility.embodiment_class == "FORKLIFT" and twin.faults.roll("wrong_level"):
            actual = self._wrong_level_slot(slot, box) or slot
        goods.store(twin, box, slot.slot_id, true_slot_id=actual.slot_id if actual is not slot else None)
        box.set_status(BoxStatus.STORED)
        box.assigned_robot = box.assigned_task = None
        robot.carrying_box = None
        robot.boxes_delivered += 1
        twin.statistics["boxes_delivered"] += 1
        # It reports the level it was sent to; true_level is where it really is.
        self._emit_effect(EventType.PLACED, robot, task, f"{robot.name} placed {box.name} in {slot.slot_id}",
                          box, slot=slot.slot_id, level=slot.level, true_level=actual.level)
        return True

    def _pack_into_carton(self, robot: Any, task: Any, action: Any, item: Any) -> bool:
        """An arm puts an order item into the order's carton (spec §7.1): the
        carton weighs its items plus CARTON_TARE_KG, and the item is consumed."""
        twin = self.twin
        order_id = action.params["into_carton"]
        carton = next((box for box in twin.boxes.values()
                       if box.kind is BoxKind.CARTON and box.order_id == order_id
                       and box.status is BoxStatus.STORED and box.position == robot.position), None)
        if carton is None:
            tare = carton_weight_kg([])
            carton = twin.add_box(kind="CARTON", position=robot.position, weight=tare, true_weight_kg=tare,
                                  destination=action.params.get("lane"), order_id=order_id)
        carton.declared_weight_kg = round(carton.declared_weight_kg + item.declared_weight_kg, 3)
        carton.true_weight_kg = round(carton.true_weight_kg + item.true_weight_kg, 3)
        carton.quantity += 1
        carton.touch()
        robot.carrying_box = None
        del twin.boxes[item.id]  # packed: it is inside the carton now
        self._emit_effect(EventType.PLACED, robot, task, f"{robot.name} packed {item.name} into {carton.name}",
                          carton, into=carton.id, items=carton.quantity, order_id=order_id)
        return True

    # PLACE_ON_CONVEYOR(cell): place_s, once the cell is free
    def _conveyor_load(self, robot: Any, action: Any) -> Optional[Any]:
        """What goes on the line: the order's packed carton (an arm), or what
        the robot holds."""
        order_id = action.params.get("carton_for")
        if order_id:
            return next((box for box in self.twin.boxes.values()
                         if box.kind is BoxKind.CARTON and box.order_id == order_id
                         and box.status is BoxStatus.STORED and box.position == robot.position), None)
        return self.twin.find_box(robot.carrying_box)

    def _begin_place_on_conveyor(self, robot: Any, task: Any, action: Any) -> Optional[int]:
        twin, cell = self.twin, action.target
        if cell is None:
            raise ValueError("it was given no conveyor cell to place on")
        if twin.equipment is None or cell not in twin.equipment.conveyor:
            raise ValueError(f"({cell[0]},{cell[1]}) is not a conveyor cell")
        if manhattan(cell, robot.position) > 1:
            raise ValueError(f"conveyor cell ({cell[0]},{cell[1]}) is out of reach")
        if self._conveyor_load(robot, action) is None:
            raise ValueError("it has nothing to put on the conveyor")
        if not twin.equipment.conveyor.is_free(cell):
            return None  # an item is passing: wait for the cell to clear
        return step_ticks(robot.mobility, "PLACE_ON_CONVEYOR")

    def _finish_place_on_conveyor(self, robot: Any, task: Any, action: Any) -> bool:
        twin, cell = self.twin, action.target
        if not twin.equipment.conveyor.is_free(cell):
            robot.set_status(RobotStatus.WAITING)
            return False  # something rode onto the cell meanwhile
        box = self._conveyor_load(robot, action)
        stop = action.params.get("stop_at")
        twin.equipment.place(box, cell, robot.id, task_id=task.id, order_id=box.order_id,
                             stop_at=tuple(stop) if stop else None)
        if robot.carrying_box == box.id:
            robot.carrying_box = None
        return True

    # WAIT_CLEAR(reason): until the condition clears
    def _begin_wait_clear(self, robot: Any, task: Any, action: Any) -> int:
        if action.params.get("reason") not in WAIT_CLEAR_REASONS:
            raise ValueError(f"it can't wait for {action.params.get('reason')!r} "
                             f"(known: {', '.join(WAIT_CLEAR_REASONS)})")
        if self.twin.equipment is None:
            raise ValueError("this floor has no conveyor")
        if not action.params.get("cell"):
            raise ValueError("it was given no conveyor cell to wait on")
        return 0

    def _finish_wait_clear(self, robot: Any, task: Any, action: Any) -> bool:
        conveyor, params = self.twin.equipment.conveyor, action.params
        cell = tuple(params["cell"])
        if params["reason"] == "CONVEYOR_OCCUPIED":
            waiting = not conveyor.is_free(cell)
        else:  # AWAITING_ITEM: until one of the order's items stands on the cell
            item = conveyor.item_at(cell)
            waiting = item is None or item.order_id != params.get("order_id")
        if waiting:
            robot.set_status(RobotStatus.WAITING)
        return not waiting

    # ---- energy ------------------------------------------------------- #
    def _apply_energy(self, distance_before: Dict[str, int]) -> None:
        """The §5.5 model, once a tick, for every battery body on the new
        floor: idle, moving or moving loaded on the ground; flight in the air.
        A charging robot is charging instead, and one in ERROR is powered down.
        Classic robots (no profile) keep paying per cell as they move."""
        twin = self.twin
        for robot in twin.robots.values():
            profile = robot.mobility
            if not energy.has_battery(profile) or robot.status in (RobotStatus.CHARGING, RobotStatus.ERROR):
                continue
            # A cell change is only the tick a cell is crossed: a body slower than a cell a tick
            # (ready_to_step) spends most of a drive between cell changes, so a tick of its
            # NAVIGATE that isn't held counts as moving too.
            moved = (robot.total_distance != distance_before.get(robot.id, robot.total_distance)
                     or self._is_driving(robot))
            used = energy.tick_wh(profile, robot.layer, moved, loaded=bool(robot.carrying_box))
            robot.use_energy(energy.wh_to_pct(profile, used))
            task = twin.tasks.get(robot.current_task) if robot.current_task else None
            self._check_battery_thresholds(robot, task)

    def _is_driving(self, robot: Any) -> bool:
        """Is `robot` driving a NAVIGATE this tick: not held, not stopped, and
        not working some other step? A hold looks like this after the tick:
        a safety wait has wait_reason set and the status WAITING; a traffic
        block has blocked_by set on every blocked tick, but is WAITING only on
        the first (the status goes back to MOVING at the top of each tick), so
        blocked_by is the mark to read; a route the planner can't find a way
        through right now is WAITING; a halted or paused robot is STOPPED.
        Status alone isn't enough the other way: TAKEOFF, LAND and PLACE are
        MOVING or DELIVERING too, so the current action has to be a NAVIGATE."""
        task = self.twin.tasks.get(robot.current_task) if robot.current_task else None
        if task is None or task.is_terminal:
            return False
        action = task.current_action
        if action is None or action.type is not ActionType.NAVIGATE:
            return False
        if robot.status not in (RobotStatus.MOVING, RobotStatus.DELIVERING):
            return False
        return robot.wait_reason is None and robot.blocked_by is None

    def _on_charger(self, robot: Any) -> bool:
        """Is the robot where it can charge: a landed drone on its pad, any
        other robot on a CHARGING cell?"""
        cell = self.twin.warehouse.cell_type(*robot.position)
        if robot.mobility is not None and robot.mobility.is_air:
            return robot.layer == GROUND and cell is CellType.DRONE_PAD
        return cell is CellType.CHARGING

    def _recharge(self, robot: Any) -> None:
        """One tick on the charger: BATTERY_CHARGE_RATE on classic, the
        body's charge rate on the new floor."""
        if energy.has_battery(robot.mobility):
            robot.gain_energy(energy.wh_to_pct(robot.mobility, energy.charge_wh_per_tick(robot.mobility)))
        else:
            robot.charge(CONFIG["BATTERY_CHARGE_RATE"])

    def _act_charge(self, robot: Any, task: Any, action: Any) -> None:
        twin = self.twin
        if not action.started:
            if robot.mobility is not None and not self._on_charger(robot):
                twin.tasks.fail_task(task, f"{robot.name} is not on a charger")
                return
            action.started = True
            if robot.mobility is not None:
                self._emit_step(robot, task, action)
            robot.charging_sessions += 1
            twin.statistics["charging_sessions"] += 1
            robot.set_status(RobotStatus.CHARGING)
            twin.events.emit(
                EventType.ROBOT_CHARGING,
                f"{robot.name} started charging at {robot.battery:.0f}%",
                category=LogCategory.BATTERY,
                robot_id=robot.id,
                task_id=task.id,
                position=cell_dict(robot.position),
            )
            return

        self._recharge(robot)
        if robot.battery >= 100.0:
            robot.low_battery_warned = False
            twin.events.emit(
                EventType.ROBOT_CHARGED,
                f"{robot.name} fully charged (CHARGING → IDLE)",
                category=LogCategory.BATTERY,
                robot_id=robot.id,
                task_id=task.id,
            )
            robot.set_status(RobotStatus.IDLE)
            self._advance(task)

    def _continue_idle_charge(self, robot: Any) -> None:
        self._recharge(robot)
        if robot.battery >= 100.0:
            robot.low_battery_warned = False
            robot.set_status(RobotStatus.IDLE)
            self.twin.events.emit(
                EventType.ROBOT_CHARGED,
                f"{robot.name} fully charged (CHARGING → IDLE)",
                category=LogCategory.BATTERY,
                robot_id=robot.id,
            )

    def _apply_movement_battery(self, robot: Any, task: Any) -> None:
        if robot.mobility is not None:
            return  # the new floor pays per tick instead (_apply_energy)
        before = robot.drain_for_movement()
        if before is None:
            return
        self.twin.logger.debug(
            LogCategory.BATTERY,
            f"{robot.name} battery {before:.0f}% → {robot.battery:.0f}%",
            robot_id=robot.id,
            task_id=task.id if task else None,
        )
        self._check_battery_thresholds(robot, task)

    def _check_battery_thresholds(self, robot: Any, task: Any) -> None:
        twin = self.twin
        if robot.battery <= 0:
            robot.last_error = "Battery depleted"
            robot.set_status(RobotStatus.ERROR)
            twin.events.emit(
                EventType.ROBOT_ERROR,
                f"{robot.name} battery depleted — robot halted",
                category=LogCategory.BATTERY,
                level=LogLevel.CRITICAL,
                robot_id=robot.id,
                task_id=task.id if task else None,
            )
            if task is not None and not task.is_terminal:
                twin.tasks.fail_task(task, "Battery depleted mid-mission")
            return
        if robot.battery <= CONFIG["BATTERY_CRITICAL"] and not robot.low_battery_warned:
            robot.low_battery_warned = True
            twin.events.emit(
                EventType.BATTERY_CRITICAL,
                f"{robot.name} battery critical at {robot.battery:.0f}%",
                category=LogCategory.BATTERY,
                level=LogLevel.CRITICAL,
                robot_id=robot.id,
                task_id=task.id if task else None,
            )
        elif robot.battery <= CONFIG["BATTERY_LOW"] and not robot.low_battery_warned:
            robot.low_battery_warned = True
            twin.events.emit(
                EventType.BATTERY_LOW,
                f"{robot.name} battery low at {robot.battery:.0f}%",
                category=LogCategory.BATTERY,
                level=LogLevel.WARNING,
                robot_id=robot.id,
                task_id=task.id if task else None,
            )

    def _auto_charge(self) -> None:
        """Idle robots with a low battery take themselves to the charger — a
        drone to its pad. A mains-powered arm never needs to. On the new floor
        a robot asks at most every SHIFT_CHECK_EVERY_TICKS, so a charger it
        can't reach right now doesn't get one failing request per tick."""
        twin = self.twin
        for robot in list(twin.robots.values()):
            if robot.is_halted or robot.current_task or robot.carrying_box or robot.mains_powered:
                continue
            if robot.status == RobotStatus.CHARGING or robot.battery > CONFIG["BATTERY_LOW"]:
                continue
            if self._on_charger(robot):
                robot.charging_sessions += 1
                robot.set_status(RobotStatus.CHARGING)
                twin.events.emit(
                    EventType.ROBOT_CHARGING,
                    f"{robot.name} is already on the charger and started charging "
                    f"at {robot.battery:.0f}%",
                    category=LogCategory.BATTERY,
                    robot_id=robot.id,
                )
                continue
            if self._charge_retry.get(robot.id, 0) > twin.tick_count:
                continue
            charger = energy.charger_zone(robot.mobility).replace("_", " ")
            twin.logger.info(
                LogCategory.BATTERY,
                f"{robot.name} is idle at {robot.battery:.0f}% — heading to the {charger}",
                robot_id=robot.id,
            )
            if robot.mobility is not None:
                self._charge_retry[robot.id] = twin.tick_count + CONFIG["SHIFT_CHECK_EVERY_TICKS"]
            try:
                twin.request_charge(robot.id, priority=Priority.HIGH, internal=True)
            except (KeyError, ValueError) as exc:
                twin.logger.error(
                    LogCategory.BATTERY, f"Auto-charge for {robot.name} failed: {exc}",
                    robot_id=robot.id,
                )

    # ---- governance / trust-layer checks ------------------------------ #
    def _check_maintenance(self) -> None:
        """Predictive maintenance (backend/maintenance.py) — flags a
        robot once (not every tick) when it crosses a wear threshold.
        Purely a nudge: doesn't touch the robot's status or task, just a
        WARNING event pointing at scheduling an
        OPERATOR_MAINTENANCE_SIGNOFF task, which is what actually resets
        the counters (see Robot.perform_maintenance)."""
        twin = self.twin
        for robot in twin.robots.values():
            reason = maintenance_reason(robot)
            if reason and not robot.maintenance_alerted:
                robot.maintenance_alerted = True
                twin.events.emit(
                    EventType.MAINTENANCE_ALERT,
                    f"{robot.name} is due for maintenance: {reason}",
                    category=LogCategory.ROBOT,
                    level=LogLevel.WARNING,
                    robot_id=robot.id,
                )

    def _check_authorization_changes(self) -> None:
        """Re-check every ACTIVE task's assigned robot against the exact
        same eligibility rule the pre-execution gate uses (backend/
        eligibility.py) — but here it FLAGS, it never gates: the robot is
        already mid-route, so forcibly killing the task would trade one
        safety problem for another. One flag per task (never re-flags
        the same task twice) — see Task.authorization_flagged and
        TRUST_LAYER.md's 'Authorization-changing events' entry."""
        twin = self.twin
        for task in twin.tasks.active():
            if not task.robot_id or task.authorization_flagged:
                continue
            robot = twin.find_robot(task.robot_id)
            if robot is None:
                continue
            reason = robot_eligibility(
                robot.status.value, robot.firmware_version, battery=None,
                task_type=task.type.value, allowed_task_types=robot.allowed_task_types,
            ) or twin.tasks.physical_recheck(robot, task)  # payload, reach, supervision (spec §10.2)
            if reason:
                task.authorization_flagged = reason
                twin.events.emit(
                    EventType.TASK_AUTHORIZATION_CHANGED,
                    f"{task.id}: {robot.name} {reason} — flagged mid-task, not halted",
                    category=LogCategory.TASK,
                    level=LogLevel.WARNING,
                    task_id=task.id,
                    robot_id=robot.id,
                )

    def _check_operator_shifts(self) -> None:
        """Automatic on/off-duty toggling for operators with a configured
        shift window (Operator.shift_start_hour/shift_end_hour) — real
        wall-clock hour, not simulated time, so a demo genuinely shows an
        off-duty operator outside real working hours. Never touches an
        operator with no shift configured (manual status control only,
        every operator's behaviour before this existed) or one currently
        ON_TASK — only flips between AVAILABLE and OFF_DUTY."""
        twin = self.twin
        hour = datetime.now().hour
        for operator in twin.operators.values():
            if not operator.has_shift or operator.status == OperatorStatus.ON_TASK:
                continue
            should_be = OperatorStatus.AVAILABLE if operator.is_within_shift(hour) else OperatorStatus.OFF_DUTY
            if operator.status != should_be:
                previous = operator.set_status(should_be)
                twin.events.emit(
                    EventType.OPERATOR_SHIFT_CHANGED,
                    f"{operator.name} {previous.value} → {should_be.value} "
                    f"(shift {operator.shift_start_hour}-{operator.shift_end_hour})",
                    category=LogCategory.TASK,
                )

    def _tick_fleet(self) -> None:
        """Inventory runtime (backend/fleet_bridge.py): OTA progress, robot
        heartbeats and credential expiry. An inventory failure must never stop
        the physical simulation, so it is logged rather than raised."""
        try:
            self.twin.fleet.on_tick(self.twin.tick_count)
        except Exception as exc:  # noqa: BLE001 - see docstring
            self.twin.logger.error(LogCategory.FLEET, f"Fleet bridge error: {exc!r}")

    # ------------------------------------------------------------------ #
    # Streaming
    # ------------------------------------------------------------------ #
    def _publish(self) -> None:
        if self.broadcaster is None:
            return
        if self.twin.tick_count % CONFIG["STATE_EMIT_EVERY_TICKS"] != 0:
            return
        self.broadcaster.publish("state", self.twin.snapshot())
