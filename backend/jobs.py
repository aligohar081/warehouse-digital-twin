"""The multi-embodiment floor's job types (spec §9.2): who can do each, what
it needs, and how it is planned.

JOB_SPECS maps each new TaskType to a JobSpec — the embodiment classes that can
do it, the label and agent-guide line it shows, and the functions that check a
request (`check`), plan a robot's steps (`plan`) and name the first cell the
robot must reach (`target`), which robot selection measures distance to and
checks a route for. TaskPlanner.plan and TaskManager hand these types to their
spec; every older job type keeps its own code path on both floors. The job
tasks register their specs at the bottom of this module.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, FrozenSet, List, Optional, Set, Tuple

from .models import Cell, TaskType
from .task_planner import PlanningError

#: (planner, task, robot, blocked) -> (actions, waypoints, handling_ops)
PlanFn = Callable[[Any, Any, Any, Set[Cell]], Tuple[List[Any], List[Cell], int]]
#: (planner, task, origin, profile, layer) -> (cell, label)
TargetFn = Callable[[Any, Any, Cell, Any, str], Tuple[Cell, str]]
#: (manager, task) -> a reason the request is invalid, or None
CheckFn = Callable[[Any, Any], Optional[str]]

#: Task payload keys the new job types read, kept on Task.params.
JOB_PARAM_KEYS = ("slot", "quantity", "station", "face", "dock", "lane", "order_id", "pack_cell", "segment")

#: The older job types whose robot lifts the task's box(es).
BOX_HANDLING_TYPES = frozenset({
    TaskType.PICK_AND_DELIVER, TaskType.PICK_BOX, TaskType.DELIVER_BOX, TaskType.MOVE_BOX,
    TaskType.BATCH_DELIVER,
})


@dataclass(frozen=True)
class JobSpec:
    label: str
    guide: str                       # TASK_TYPE_GUIDE's line: what the payload needs
    classes: FrozenSet[str]          # the bodies that can do it (empty: any mobile body)
    check: Optional[CheckFn] = None
    plan: Optional[PlanFn] = None    # None for a human job
    target: Optional[TargetFn] = None
    carries_box: bool = False        # the robot lifts the task's box: payload and kind apply
    air: bool = False                # its targets are on the AIR layer
    human: bool = False              # a person does it (backend/human_jobs.py)


JOB_SPECS: Dict[TaskType, JobSpec] = {}


# --------------------------------------------------------------------------- #
# Helpers the job specs share
# --------------------------------------------------------------------------- #
def task_box(twin: Any, task: Any) -> Any:
    """The task's box, or PlanningError."""
    box = twin.find_box(task.box_id)
    if box is None:
        raise PlanningError(f"Box '{task.box_id}' does not exist")
    return box


def task_slot(twin: Any, task: Any, box: Optional[Any] = None) -> Optional[Any]:
    """The slot a job lifts to or from: its `slot` parameter, else its box's."""
    slot_id = task.params.get("slot") or (box.slot if box is not None else None)
    if not slot_id:
        return None
    slot = twin.warehouse.slot(slot_id)
    if slot is None:
        raise PlanningError(f"Unknown slot {slot_id!r}")
    return slot


def job_levels(twin: Any, task: Any) -> List[int]:
    """The shelf levels a job works at, for the reach rule: its slot's level,
    or every level of the rack face it counts. An older box-handling job names
    no slot: it works at the level of the slot each box it lifts sits in."""
    spec = JOB_SPECS.get(task.type)
    if spec is None:
        if task.type not in BOX_HANDLING_TYPES:
            return []
        ids = task.box_ids if task.type == TaskType.BATCH_DELIVER else [task.box_id]
        boxes = [twin.find_box(box_id) for box_id in ids if box_id]
        slots = [twin.warehouse.slot(box.slot) for box in boxes if box is not None and box.slot]
        return [slot.level for slot in slots if slot is not None]
    slot_id = task.params.get("slot")
    if not slot_id and spec.carries_box and task.box_id:
        box = twin.find_box(task.box_id)
        slot_id = box.slot if box is not None else None
    if slot_id:
        slot = twin.warehouse.slot(slot_id)
        return [slot.level] if slot is not None else []
    face = task.params.get("face")
    if face:
        cell = parse_cell(face)
        return [slot.level for slot in twin.warehouse.slots_at(cell)] if cell else []
    return []


def parse_cell(spec: Any) -> Optional[Cell]:
    """A cell from "x,y", [x, y] or {"x": .., "y": ..}; None if it isn't one."""
    try:
        if isinstance(spec, dict):
            return int(spec["x"]), int(spec["y"])
        if isinstance(spec, (list, tuple)) and len(spec) == 2:
            return int(spec[0]), int(spec[1])
        if isinstance(spec, str) and "," in spec:
            x, y = spec.split(",", 1)
            return int(x), int(y)
    except (KeyError, TypeError, ValueError):
        return None
    return None


def face_cell(planner: Any, slot: Any, origin: Cell, profile: Any, layer: str,
              blocked: Optional[Set[Cell]] = None) -> Cell:
    """The aisle cell in front of `slot` a body stands on to serve it,
    nearest `origin` among those it can reach."""
    cell = planner.twin.navigation.best_cell_in_zone(slot.faces, origin, blocked=blocked,
                                                     profile=profile, layer=layer)
    if cell is None:
        raise PlanningError(f"No face of slot {slot.slot_id} is reachable")
    return cell
