"""Jobs people do (multi-embodiment spec §9.2): MANUAL_PICK at Pick 2 and
CLEAR_JAM on the conveyor.

A human job has no robot. TaskManager validates it (choosing the nearest
qualified person on the floor for AUTO) and starts it; each Simulator tick
then moves it through its phases, kept on Task.params: WALK to the job's zone
(people.start_transit), WORK there, and for a jam RETURN to where the person
came from — a person left standing in a fenced pack cell would hold its arm
(PERSON_IN_CELL) forever. For the same reason a job that ends early
(cancelled, failed) walks the person back out (walk_out): they stay on the
job, unavailable, until they arrive, and tick() then frees them. Where they
come back to is never a pack cell (exit_zone). A conveyor jam gets its
CLEAR_JAM job from ensure_jam_jobs, retried while no one qualified is on the
floor (spec §11.3).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from . import goods, people
from .embodiment import seconds_to_ticks
from .jobs import parse_cell
from .models import (CONFIG, Action, ActionType, Cell, LogCategory, OperatorStatus, Priority, TaskStatus, TaskType,
                     cell_dict)

HUMAN_JOBS = frozenset({TaskType.MANUAL_PICK, TaskType.CLEAR_JAM})

#: How many times a jam is asked for through the gate while no one qualified
#: is on the floor: the ask and, as for any rejected job, one retry (spec
#: §11.2). After that it waits until someone qualified is (ensure_jam_jobs).
JAM_GATE_ASKS = 2

#: The arm model a jam inside a pack cell needs robot_cell_access scoped to.
ARM_MODEL = "FB-CX10"


def jam_cell(task: Any) -> Optional[Cell]:
    return parse_cell(task.params.get("segment"))


def in_pack_cell(twin: Any, cell: Optional[Cell]) -> bool:
    """A jam on an arm's working cell is inside its fenced pack cell."""
    return twin.equipment is not None and cell in twin.equipment.arm_cells.values()


def jam_zone(twin: Any, cell: Cell) -> str:
    """Where a person stands to clear a jam: the pack cell whose arm works
    `cell`, else the conveyor itself."""
    for key, work_cell in twin.equipment.arm_cells.items():
        if work_cell == cell:
            return key
    return "conveyor"


def equipment_model(twin: Any, task: Any) -> Optional[str]:
    """The equipment model a job's credential scope must cover: the arm a
    jam inside a pack cell is beside, else none."""
    cell = jam_cell(task)
    if task.type is TaskType.CLEAR_JAM and in_pack_cell(twin, cell):
        arm = next((r for r in twin.robots.values()
                    if twin.equipment.arm_cells.get(twin.warehouse.fixed_stations.get(r.position)) == cell), None)
        return arm.model_code if arm is not None and arm.model_code else ARM_MODEL
    return None


def exit_zone(twin: Any, zone: Optional[str]) -> Optional[str]:
    """Where a person standing in `zone` goes back to when a job ends: the zone
    itself, unless it is a fenced pack cell — leaving someone in there holds
    its arm — and then the nearest zone that shares an edge with the cell and
    isn't a pack cell itself (centre to centre, ties by key): the pick station
    beside it. A floor whose pack cell touches nothing else takes the nearest
    zone that isn't a pack cell."""
    pack_cells = set(twin.equipment.arm_cells) if twin.equipment is not None else set()
    if zone not in pack_cells:
        return zone
    warehouse = twin.warehouse
    where = warehouse.zones[zone].center
    outside = [key for key in warehouse.zones if key not in pack_cells]
    beside = [key for key in outside if people.zones_touch(warehouse, zone, key)]
    return min(beside or outside, key=lambda key: (
        abs(warehouse.zones[key].center[0] - where[0]) + abs(warehouse.zones[key].center[1] - where[1]), key))


def target_zone(twin: Any, task: Any) -> str:
    if task.type is TaskType.CLEAR_JAM:
        return jam_zone(twin, jam_cell(task))
    return task.params.get("station") or "pick_station_2"


def start(twin: Any, task: Any, operator: Any) -> None:
    """Put `operator` on the job and send them walking to it. The walk starts
    first: if it can't (the caller then fails the task) the person and the
    task are exactly as they were."""
    zone = target_zone(twin, task)
    label = twin.warehouse.zones[zone].label
    origin = exit_zone(twin, operator.zone)
    people.start_transit(twin, operator, zone)
    if task.type is TaskType.CLEAR_JAM:
        cell = jam_cell(task)
        work = f"Clear the jam at ({cell[0]},{cell[1]})"
        back = [Action(ActionType.WAIT, f"Walk back to {origin}")]
    else:
        work = f"Pick {task.params['quantity']} item(s) onto the conveyor"
        back = []
    task.actions = [Action(ActionType.WAIT, f"Walk to {label}"), Action(ActionType.WAIT, work)] + back + [
        Action(ActionType.COMPLETE, "Complete task")]
    task.action_index = 0
    task.params.update(phase="WALK", origin_zone=origin, picked=0)
    operator.current_task = task.id
    operator.set_status(OperatorStatus.ON_TASK)


def walk_out(twin: Any, task: Any, operator: Any) -> bool:
    """A job ended without finishing: send the person back to where they
    started (never a pack cell, see exit_zone), turning a walk already under
    way. True while they still have a walk to make; someone off the floor has
    none."""
    origin = task.params.get("origin_zone")
    if origin and operator.zone is not None:
        people.redirect_transit(twin, operator, origin)
    return operator.in_transit


def _work_ticks(task: Any) -> int:
    seconds = CONFIG["CLEAR_JAM_S"] if task.type is TaskType.CLEAR_JAM else CONFIG["MANUAL_PICK_S_PER_ITEM"]
    return seconds_to_ticks(seconds)


def _next(task: Any, phase: str) -> None:
    task.params["phase"] = phase
    task.action_index = min(task.action_index + 1, len(task.actions) - 1)


def tick(twin: Any) -> None:
    """Move every running human job on by one tick (Simulator.tick), then free
    the people who have finished walking out of a job that ended early. A job
    that raises fails on its own: it must not stop the tick for everyone else."""
    for task in list(twin.tasks.tasks.values()):
        if task.type not in HUMAN_JOBS or task.status is not TaskStatus.IN_PROGRESS:
            continue
        try:
            _step(twin, task)
        except Exception as exc:
            if not task.is_terminal:
                _fail(twin, task, exc)
    _free_arrived(twin)


def _step(twin: Any, task: Any) -> None:
    operator = twin.find_operator(task.operator_id)
    if operator is None or operator.status is OperatorStatus.OFF_DUTY or operator.zone is None:
        twin.tasks.fail_task(task, f"{operator.name if operator else task.operator_id} left the floor")
        return
    _advance(twin, task, operator)


def _fail(twin: Any, task: Any, exc: Exception) -> None:
    operator = twin.find_operator(task.operator_id)
    if isinstance(exc, ValueError):
        reason = f"{operator.name if operator else task.operator_id} could not finish: {exc}"
    else:
        reason = f"Controller error: {exc}"  # as for a robot's job
    twin.tasks.fail_task(task, reason)


def _free_arrived(twin: Any) -> None:
    """A person kept on a job that has ended (they were walking out) is free
    once they stand still: they are still ON_TASK, current_task naming it."""
    for operator in twin.operators.values():
        task = twin.tasks.get(operator.current_task) if operator.current_task else None
        if task is None or task.type not in HUMAN_JOBS or not task.is_terminal or operator.in_transit:
            continue
        operator.current_task = None
        if operator.status is OperatorStatus.ON_TASK:
            operator.set_status(OperatorStatus.AVAILABLE)


def _advance(twin: Any, task: Any, operator: Any) -> None:
    phase = task.params["phase"]
    if operator.in_transit:
        return
    if phase == "WALK":
        _next(task, "WORK")
        task.params["until_tick"] = twin.tick_count + _work_ticks(task)
        return
    if phase == "RETURN":
        twin.tasks.complete_task(task, f"{operator.name} cleared the jam and is back")
        return
    if twin.tick_count < task.params["until_tick"]:
        return
    if task.type is TaskType.CLEAR_JAM:
        twin.equipment.clear_jam(jam_cell(task))
        _next(task, "RETURN")
        origin = task.params.get("origin_zone")
        if origin and origin != operator.zone:
            people.start_transit(twin, operator, origin)
        return
    _place_one_item(twin, task, operator)


def _place_one_item(twin: Any, task: Any, operator: Any) -> None:
    """MANUAL_PICK: one unit out of the tote onto the infeed, once it's free."""
    station = twin.warehouse.zones[task.params.get("station") or "pick_station_2"]
    infeed = tuple(station.attributes["conveyor_infeed"])
    if not twin.equipment.conveyor.is_free(infeed):
        return  # an item is passing: wait with the unit still in the tote
    tote = twin.find_box(task.box_id)
    declared, true = goods.take_units(twin, tote, 1)
    item = twin.add_box(kind="ITEM", position=tuple(station.attributes["work_cell"]), sku=tote.sku, quantity=1,
                        weight=declared, true_weight_kg=true, order_id=task.params.get("order_id"))
    pack_cell = task.params.get("pack_cell")
    stop = twin.equipment.arm_cells.get(pack_cell) if pack_cell else None
    twin.equipment.place(item, infeed, operator.id, task_id=task.id, order_id=item.order_id, stop_at=stop)
    task.params["picked"] = int(task.params.get("picked", 0)) + 1
    if task.params["picked"] >= int(task.params["quantity"]):
        _next(task, "DONE")
        twin.tasks.complete_task(task, f"{operator.name} picked {task.params['picked']} item(s)")
        return
    task.params["until_tick"] = twin.tick_count + _work_ticks(task)


def someone_can_clear(twin: Any, segment: str) -> Tuple[bool, Optional[str]]:
    """Would the gate find someone to clear the jam at `segment` now? It runs
    the gate's own check (TaskManager.validate) on a CLEAR_JAM that is never
    recorded: no task, event, log file or stat."""
    from .task_manager import Task  # deferred: task_manager imports this module

    probe = Task("CLEAR_JAM-check", TaskType.CLEAR_JAM, priority=Priority.HIGH, internal=True,
                 params={"segment": segment})
    return twin.tasks.validate(probe)


def ensure_jam_jobs(twin: Any, rejected: Optional[Dict[Tuple[Cell, int], int]] = None) -> List[Any]:
    """Every jammed segment without a running CLEAR_JAM gets one. While no
    one qualified is on the floor the gate rejects it, and each rejection is
    a FAILED job that records why; after JAM_GATE_ASKS of them (the ask and
    its one retry) the jam waits, logged once: each check asks silently
    whether someone qualified is on the floor (someone_can_clear) and asks
    the gate again only once someone is, so the jam is cleared as soon as
    someone qualifies without a FAILED job, log file and stat at every check.
    `rejected` ((cell, tick it jammed) -> the gate's rejections since it last
    had a job) is the caller's memory across checks: Simulator keeps one, and
    a new jam on the same cell starts afresh. Without it every check asks
    the gate."""
    if twin.equipment is None:
        return []
    asks = rejected if rejected is not None else {}
    jams = twin.equipment.conveyor.jams
    for key in [key for key in asks if jams.get(key[0]) != key[1]]:
        del asks[key]  # that jam is cleared
    covered = {task.params.get("segment") for task in twin.tasks.tasks.values()
               if task.type is TaskType.CLEAR_JAM and not task.is_terminal}
    created = []
    for cell, since in list(jams.items()):
        segment, key = f"{cell[0]},{cell[1]}", (cell, since)
        if segment in covered:
            continue
        try:
            if asks.get(key, 0) >= JAM_GATE_ASKS and not someone_can_clear(twin, segment)[0]:
                continue  # still no one qualified: it waits, and nothing is recorded
            task = twin.tasks.create_task({"type": "CLEAR_JAM", "segment": segment, "priority": "HIGH"}, internal=True)
        except Exception as exc:  # the jam is asked for again next time; the tick goes on
            twin.logger.warning(LogCategory.OPERATIONS, f"Could not ask for the jam at {segment} to be cleared: {exc}",
                                position=cell_dict(cell))
            continue
        if task.status is TaskStatus.FAILED:
            asks[key] = asks.get(key, 0) + 1
            waiting = " — waiting until someone qualified is on the floor" if asks[key] == JAM_GATE_ASKS else ""
            twin.logger.warning(LogCategory.OPERATIONS,
                                f"No one can clear the jam at {segment} yet: {task.error}{waiting}",
                                position=cell_dict(cell))
        else:
            asks.pop(key, None)
        created.append(task)
    return created
