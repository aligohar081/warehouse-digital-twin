"""Jobs people do (multi-embodiment spec §9.2): MANUAL_PICK at Pick 2 and
CLEAR_JAM on the conveyor.

A human job has no robot. TaskManager validates it (choosing the nearest
qualified person on the floor for AUTO) and starts it; each Simulator tick
then moves it through its phases, kept on Task.params: WALK to the job's zone
(people.start_transit), WORK there, and for a jam RETURN to where the person
came from — a person left standing in a fenced pack cell would hold its arm
(PERSON_IN_CELL) forever. A conveyor jam gets its CLEAR_JAM job from
ensure_jam_jobs, retried while no one qualified is on the floor (spec §11.3).
"""
from __future__ import annotations

from typing import Any, List, Optional

from . import goods, people
from .embodiment import seconds_to_ticks
from .jobs import parse_cell
from .models import CONFIG, Action, ActionType, Cell, LogCategory, OperatorStatus, TaskStatus, TaskType, cell_dict

HUMAN_JOBS = frozenset({TaskType.MANUAL_PICK, TaskType.CLEAR_JAM})

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


def target_zone(twin: Any, task: Any) -> str:
    if task.type is TaskType.CLEAR_JAM:
        return jam_zone(twin, jam_cell(task))
    return task.params.get("station") or "pick_station_2"


def start(twin: Any, task: Any, operator: Any) -> None:
    """Put `operator` on the job and send them walking to it."""
    zone = target_zone(twin, task)
    label = twin.warehouse.zones[zone].label
    if task.type is TaskType.CLEAR_JAM:
        cell = jam_cell(task)
        work = f"Clear the jam at ({cell[0]},{cell[1]})"
        back = [Action(ActionType.WAIT, f"Walk back to {operator.zone}")]
    else:
        work = f"Pick {task.params['quantity']} item(s) onto the conveyor"
        back = []
    task.actions = [Action(ActionType.WAIT, f"Walk to {label}"), Action(ActionType.WAIT, work)] + back + [
        Action(ActionType.COMPLETE, "Complete task")]
    task.action_index = 0
    task.params.update(phase="WALK", origin_zone=operator.zone, picked=0)
    operator.current_task = task.id
    operator.set_status(OperatorStatus.ON_TASK)
    people.start_transit(twin, operator, zone)


def _work_ticks(task: Any) -> int:
    seconds = CONFIG["CLEAR_JAM_S"] if task.type is TaskType.CLEAR_JAM else CONFIG["MANUAL_PICK_S_PER_ITEM"]
    return seconds_to_ticks(seconds)


def _next(task: Any, phase: str) -> None:
    task.params["phase"] = phase
    task.action_index = min(task.action_index + 1, len(task.actions) - 1)


def tick(twin: Any) -> None:
    """Move every running human job on by one tick (Simulator.tick)."""
    for task in list(twin.tasks.tasks.values()):
        if task.type not in HUMAN_JOBS or task.status is not TaskStatus.IN_PROGRESS:
            continue
        operator = twin.find_operator(task.operator_id)
        if operator is None or operator.status is OperatorStatus.OFF_DUTY or operator.zone is None:
            twin.tasks.fail_task(task, f"{operator.name if operator else task.operator_id} left the floor")
            continue
        try:
            _advance(twin, task, operator)
        except ValueError as exc:
            twin.tasks.fail_task(task, f"{operator.name} could not finish: {exc}")


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


def ensure_jam_jobs(twin: Any) -> List[Any]:
    """Every jammed segment without a running CLEAR_JAM gets one. When the
    gate rejects it (no one qualified on the floor) it is tried again here
    next time, so the jam is cleared as soon as someone qualifies."""
    if twin.equipment is None:
        return []
    covered = {task.params.get("segment") for task in twin.tasks.tasks.values()
               if task.type is TaskType.CLEAR_JAM and not task.is_terminal}
    created = []
    for cell in list(twin.equipment.conveyor.jams):
        segment = f"{cell[0]},{cell[1]}"
        if segment in covered:
            continue
        task = twin.tasks.create_task({"type": "CLEAR_JAM", "segment": segment, "priority": "HIGH"}, internal=True)
        if task.status is TaskStatus.FAILED:
            twin.logger.warning(LogCategory.OPERATIONS, f"No one can clear the jam at {segment} yet: {task.error}",
                                position=cell_dict(cell))
        created.append(task)
    return created
