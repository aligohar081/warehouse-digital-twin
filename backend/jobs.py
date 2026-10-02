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

from . import goods
from .models import Action, ActionType, BoxKind, BoxStatus, Cell, TaskType
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


def no_profile_reason(task: Any) -> str:
    """Why a robot with no floor profile can't do a job type of this module:
    each is checked against a body, and a classic robot has none."""
    return f"has no floor profile; {task.type.value} needs a robot on the multi-embodiment floor"


def promised_slots(twin: Any, exclude: Optional[str] = None) -> Set[str]:
    """Slots that jobs not yet finished are going to fill."""
    return {task.params["slot"] for task in twin.tasks.tasks.values()
            if task.id != exclude and not task.is_terminal and task.params.get("slot")
            and task.type in FILLS_SLOT}


def check_box(manager: Any, task: Any, kind: BoxKind, in_slot: Optional[bool] = None) -> Optional[str]:
    """The job's box exists, is a `kind`, is in a slot or not as the job
    needs, and no other job holds it."""
    if not task.box_id:
        return f"{task.type.value} needs a box_id"
    box = manager.twin.find_box(task.box_id)
    if box is None:
        return f"Box '{task.box_id}' does not exist"
    task.box_id = box.id
    if box.kind is not kind:
        return f"{box.name} is a {box.kind.value}, not a {kind.value}"
    if box.status in (BoxStatus.SHIPPED, BoxStatus.FAILED, BoxStatus.CARRIED):
        return f"{box.name} is {box.status.value}"
    if in_slot is True and not box.slot:
        return f"{box.name} is not in a slot"
    if in_slot is False and box.slot and box.kind is BoxKind.PALLET:
        return f"{box.name} is already in slot {box.slot}"
    conflict = manager.active_task_for_box(box.id, exclude=task.id)
    if conflict is not None:
        return f"{box.name} is already reserved by {conflict.id}"
    return None


def check_slot(manager: Any, task: Any, kind: BoxKind) -> Optional[str]:
    """An explicit `slot` exists, takes a `kind` and is free and unpromised."""
    slot_id = task.params.get("slot")
    if not slot_id:
        return None
    twin = manager.twin
    slot = twin.warehouse.slot(slot_id)
    if slot is None:
        return f"Unknown slot {slot_id!r}"
    if slot.kind != kind.value:
        return f"Slot {slot_id} holds {slot.kind}s, not {kind.value}s"
    holder = twin.stock.box_in(slot_id)
    if holder is not None and holder != task.box_id:
        return f"Slot {slot_id} already holds {holder}"
    if slot_id in promised_slots(twin, exclude=task.id):
        return f"Slot {slot_id} is promised to another job"
    return None


def check_zone(manager: Any, zone: Optional[str]) -> Optional[str]:
    if manager.twin.warehouse.resolve_zone(zone) is None:
        return f"Unknown zone {zone!r}"
    return None


def floor_target(planner: Any, task: Any, origin: Cell, profile: Any, layer: str) -> Tuple[Cell, str]:
    """A job that starts by picking its box off the floor: the box's cell."""
    box = task_box(planner.twin, task)
    return box.position, box.name


def slot_target(planner: Any, task: Any, origin: Cell, profile: Any, layer: str) -> Tuple[Cell, str]:
    """A job that starts at its box's slot: the face cell it serves it from."""
    box = task_box(planner.twin, task)
    slot = task_slot(planner.twin, task, box)
    if slot is None:
        raise PlanningError(f"{box.name} is not in a slot")
    return face_cell(planner, slot, origin, profile, layer), f"slot {slot.slot_id}"


def drop_cell(planner: Any, zone: str, origin: Cell, blocked: Set[Cell], route: Dict[str, Any]) -> Tuple[Cell, str]:
    """A free cell of `zone` to put a box down on."""
    return planner.resolve_target(zone, origin, blocked, prefer_free=True, **route)


def lift_steps(slot: Any, face: Cell, verb: ActionType, box: Any) -> List[Any]:
    """Lift to `slot`'s level at `face`, grasp or place the box, lower."""
    level = slot.level
    act = "Grasp" if verb is ActionType.GRASP else "Place"
    return [
        Action(ActionType.LIFT_TO, f"Lift to level {level} of {slot.slot_id}", face, slot.slot_id, box.id,
               level=level, slot_id=slot.slot_id),
        Action(verb, f"{act} {box.name} at {slot.slot_id}", face, slot.slot_id, box.id,
               level=level, slot_id=slot.slot_id),
        Action(ActionType.LOWER, "Lower", face, slot.slot_id, box.id),
    ]


# --------------------------------------------------------------------------- #
# Pallets (spec §9.2): unload, put away, retrieve, load
# --------------------------------------------------------------------------- #
def _check_unload(manager: Any, task: Any) -> Optional[str]:
    task.destination = task.destination or "intake_staging"
    return check_box(manager, task, BoxKind.PALLET, in_slot=False) or check_zone(manager, task.destination)


def _plan_floor_to_zone(planner: Any, task: Any, robot: Any, blocked: Set[Cell],
                        ship: bool = False) -> Tuple[List[Any], List[Cell], int]:
    """NAVIGATE to the box → PICK → NAVIGATE to a free cell of the destination → DELIVER."""
    route = {"profile": robot.mobility, "layer": robot.layer}
    box = task_box(planner.twin, task)
    pick = box.position
    drop, label = drop_cell(planner, task.destination, pick, blocked, route)
    actions = [
        Action(ActionType.NAVIGATE, f"Navigate to {box.name}", pick, box.name, box.id),
        Action(ActionType.PICK, f"Pick {box.name}", pick, box.name, box.id),
        Action(ActionType.NAVIGATE, f"Carry {box.name} to {label}", drop, label, box.id),
        Action(ActionType.DELIVER, f"Deliver {box.name} at {label}", drop, label, box.id,
               params={"ship": True} if ship else {}),
    ]
    return actions, [pick, drop], 2


def _check_putaway(manager: Any, task: Any) -> Optional[str]:
    return check_box(manager, task, BoxKind.PALLET, in_slot=False) or check_slot(manager, task, BoxKind.PALLET)


def choose_slot(planner: Any, task: Any, robot: Any, kind: BoxKind, near: Cell) -> Any:
    """The job's slot: its `slot` parameter, else the nearest free slot of
    `kind` within the robot's reach — recorded on the task, so no other job
    is promised it meanwhile."""
    twin = planner.twin
    if robot.mobility is None:  # the gate keeps these out; this is the backstop
        raise PlanningError(f"{robot.name} {no_profile_reason(task)}")
    slot = task_slot(twin, task)
    if slot is None:
        free = goods.free_slots(twin, kind.value, max_level=robot.mobility.max_shelf_level, near=near,
                                exclude=promised_slots(twin, exclude=task.id))
        if not free:
            raise PlanningError(f"No free {kind.value} slot within {robot.name}'s reach")
        slot = free[0]
        task.params["slot"] = slot.slot_id
    return slot


def _plan_putaway(planner: Any, task: Any, robot: Any, blocked: Set[Cell]) -> Tuple[List[Any], List[Cell], int]:
    """NAVIGATE staging → PICK → NAVIGATE face → LIFT_TO(level) → PLACE → LOWER."""
    route = {"profile": robot.mobility, "layer": robot.layer}
    box = task_box(planner.twin, task)
    slot = choose_slot(planner, task, robot, BoxKind.PALLET, box.position)
    face = face_cell(planner, slot, box.position, blocked=blocked, **route)
    actions = [
        Action(ActionType.NAVIGATE, f"Navigate to {box.name}", box.position, box.name, box.id),
        Action(ActionType.PICK, f"Pick {box.name}", box.position, box.name, box.id),
        Action(ActionType.NAVIGATE, f"Carry {box.name} to slot {slot.slot_id}", face, slot.slot_id, box.id),
    ] + lift_steps(slot, face, ActionType.PLACE, box)
    return actions, [box.position, face], 2


def _check_retrieve(manager: Any, task: Any) -> Optional[str]:
    task.destination = task.destination or "outbound_staging"
    return check_box(manager, task, BoxKind.PALLET, in_slot=True) or check_zone(manager, task.destination)


def _plan_retrieve(planner: Any, task: Any, robot: Any, blocked: Set[Cell]) -> Tuple[List[Any], List[Cell], int]:
    """NAVIGATE face → LIFT_TO → GRASP → LOWER → NAVIGATE outbound_staging → DELIVER."""
    route = {"profile": robot.mobility, "layer": robot.layer}
    box = task_box(planner.twin, task)
    slot = task_slot(planner.twin, task, box)
    face = face_cell(planner, slot, robot.position, blocked=blocked, **route)
    drop, label = drop_cell(planner, task.destination, face, blocked, route)
    actions = [Action(ActionType.NAVIGATE, f"Navigate to slot {slot.slot_id}", face, slot.slot_id, box.id)]
    actions += lift_steps(slot, face, ActionType.GRASP, box)
    actions += [
        Action(ActionType.NAVIGATE, f"Carry {box.name} to {label}", drop, label, box.id),
        Action(ActionType.DELIVER, f"Deliver {box.name} at {label}", drop, label, box.id),
    ]
    return actions, [face, drop], 2


def _check_load(manager: Any, task: Any) -> Optional[str]:
    task.destination = task.params.get("dock") or task.destination or "dock_3"
    return check_box(manager, task, BoxKind.PALLET, in_slot=False) or check_zone(manager, task.destination)


def _plan_load(planner: Any, task: Any, robot: Any, blocked: Set[Cell]) -> Tuple[List[Any], List[Cell], int]:
    """NAVIGATE outbound_staging → PICK → NAVIGATE dock_3 → DELIVER (the pallet ships)."""
    return _plan_floor_to_zone(planner, task, robot, blocked, ship=True)


#: Job types whose plan fills a slot (promised_slots reads these).
FILLS_SLOT = {TaskType.PUTAWAY_PALLET, TaskType.RETURN_TOTE, TaskType.RETURNS_PUTAWAY}

JOB_SPECS[TaskType.UNLOAD_TRUCK] = JobSpec(
    label="Unload truck", guide="box_id(a pallet on an inbound dock)[+destination, default intake_staging]",
    classes=frozenset({"FORKLIFT", "HEAVY_HAULER"}), check=_check_unload,
    plan=_plan_floor_to_zone, target=floor_target, carries_box=True)
JOB_SPECS[TaskType.PUTAWAY_PALLET] = JobSpec(
    label="Put away pallet", guide="box_id(a staged pallet)[+slot, default the nearest free one in reach]",
    classes=frozenset({"FORKLIFT"}), check=_check_putaway, plan=_plan_putaway, target=floor_target,
    carries_box=True)
JOB_SPECS[TaskType.RETRIEVE_PALLET] = JobSpec(
    label="Retrieve pallet", guide="box_id(a pallet in a rack slot)[+destination, default outbound_staging]",
    classes=frozenset({"FORKLIFT"}), check=_check_retrieve, plan=_plan_retrieve, target=slot_target,
    carries_box=True)
JOB_SPECS[TaskType.LOAD_TRUCK] = JobSpec(
    label="Load truck", guide="box_id(a pallet at outbound staging)[+dock, default dock_3](it ships)",
    classes=frozenset({"FORKLIFT"}), check=_check_load, plan=_plan_load, target=floor_target,
    carries_box=True)


# --------------------------------------------------------------------------- #
# Totes (spec §9.2): to a pick station and back; returns into storage
# --------------------------------------------------------------------------- #
def tote_home(twin: Any, box: Any) -> Optional[Any]:
    """The slot a tote belongs in (it keeps it while away at a station)."""
    return twin.warehouse.slot(box.slot) if box.slot else None


def tote_is_home(twin: Any, box: Any) -> bool:
    home = tote_home(twin, box)
    return home is not None and box.position == home.cell and box.status in (BoxStatus.STORED, BoxStatus.RESERVED)


def _check_tote_to_station(manager: Any, task: Any) -> Optional[str]:
    task.params["station"] = task.params.get("station") or task.destination or "pick_station_1"
    reason = check_box(manager, task, BoxKind.TOTE, in_slot=True)
    if reason:
        return reason
    box = manager.twin.find_box(task.box_id)
    if not tote_is_home(manager.twin, box):
        return f"{box.name} is not in its slot {box.slot}"
    station = manager.twin.warehouse.resolve_zone(task.params["station"])
    if station is None or "tote_drop" not in station.attributes:
        return f"{task.params['station']} is not a pick station"
    task.params["station"] = task.destination = station.key
    return None


def _plan_tote_to_station(planner: Any, task: Any, robot: Any, blocked: Set[Cell]) -> Tuple[List[Any], List[Cell], int]:
    """NAVIGATE face → LIFT_TO → GRASP → LOWER → NAVIGATE station tote drop → DELIVER."""
    route = {"profile": robot.mobility, "layer": robot.layer}
    twin = planner.twin
    box = task_box(twin, task)
    slot = tote_home(twin, box)
    face = face_cell(planner, slot, robot.position, blocked=blocked, **route)
    station = twin.warehouse.zones[task.params["station"]]
    drop = tuple(station.attributes["tote_drop"])
    actions = [Action(ActionType.NAVIGATE, f"Navigate to slot {slot.slot_id}", face, slot.slot_id, box.id)]
    actions += lift_steps(slot, face, ActionType.GRASP, box)
    actions += [
        Action(ActionType.NAVIGATE, f"Carry {box.name} to {station.label}", drop, station.label, box.id),
        Action(ActionType.DELIVER, f"Deliver {box.name} at {station.label}", drop, station.label, box.id),
    ]
    return actions, [face, drop], 2


def _check_return_tote(manager: Any, task: Any) -> Optional[str]:
    reason = check_box(manager, task, BoxKind.TOTE)
    if reason:
        return reason
    box = manager.twin.find_box(task.box_id)
    task.params["slot"] = task.params.get("slot") or box.slot
    if not task.params["slot"]:
        return f"{box.name} has no slot to return to"
    if tote_is_home(manager.twin, box) and task.params["slot"] == box.slot:
        return f"{box.name} is already in its slot {box.slot}"
    return check_slot(manager, task, BoxKind.TOTE)


def _plan_back_to_slot(planner: Any, task: Any, robot: Any, blocked: Set[Cell]) -> Tuple[List[Any], List[Cell], int]:
    """NAVIGATE tote → PICK → NAVIGATE face → LIFT_TO → PLACE → LOWER (the reverse trip)."""
    route = {"profile": robot.mobility, "layer": robot.layer}
    box = task_box(planner.twin, task)
    slot = task_slot(planner.twin, task)
    face = face_cell(planner, slot, box.position, blocked=blocked, **route)
    actions = [
        Action(ActionType.NAVIGATE, f"Navigate to {box.name}", box.position, box.name, box.id),
        Action(ActionType.PICK, f"Pick {box.name}", box.position, box.name, box.id),
        Action(ActionType.NAVIGATE, f"Carry {box.name} to slot {slot.slot_id}", face, slot.slot_id, box.id),
    ] + lift_steps(slot, face, ActionType.PLACE, box)
    return actions, [box.position, face], 2


def _check_returns_putaway(manager: Any, task: Any) -> Optional[str]:
    reason = check_box(manager, task, BoxKind.TOTE)
    if reason:
        return reason
    box = manager.twin.find_box(task.box_id)
    if box.slot:
        return f"{box.name} already belongs in slot {box.slot}"
    return check_slot(manager, task, BoxKind.TOTE)


def _plan_returns_putaway(planner: Any, task: Any, robot: Any,
                          blocked: Set[Cell]) -> Tuple[List[Any], List[Cell], int]:
    """NAVIGATE returns_qc → GRASP tote → NAVIGATE face → LIFT_TO(level) → PLACE → LOWER."""
    route = {"profile": robot.mobility, "layer": robot.layer}
    box = task_box(planner.twin, task)
    slot = choose_slot(planner, task, robot, BoxKind.TOTE, box.position)
    face = face_cell(planner, slot, box.position, blocked=blocked, **route)
    actions = [
        Action(ActionType.NAVIGATE, f"Navigate to {box.name}", box.position, box.name, box.id),
        Action(ActionType.GRASP, f"Grasp {box.name}", box.position, box.name, box.id),
        Action(ActionType.NAVIGATE, f"Carry {box.name} to slot {slot.slot_id}", face, slot.slot_id, box.id),
    ] + lift_steps(slot, face, ActionType.PLACE, box)
    return actions, [box.position, face], 2


JOB_SPECS[TaskType.TOTE_TO_STATION] = JobSpec(
    label="Tote to station", guide="box_id(a tote in its slot)[+station, default pick_station_1]",
    classes=frozenset({"AMR", "HUMANOID"}), check=_check_tote_to_station, plan=_plan_tote_to_station,
    target=slot_target, carries_box=True)
JOB_SPECS[TaskType.RETURN_TOTE] = JobSpec(
    label="Return tote", guide="box_id(a tote away at a station)[+slot, default its own]",
    classes=frozenset({"AMR", "HUMANOID"}), check=_check_return_tote, plan=_plan_back_to_slot,
    target=floor_target, carries_box=True)
JOB_SPECS[TaskType.RETURNS_PUTAWAY] = JobSpec(
    label="Returns put-away", guide="box_id(a returned tote in returns_qc)[+slot, default the nearest free one]",
    classes=frozenset({"HUMANOID"}), check=_check_returns_putaway, plan=_plan_returns_putaway,
    target=floor_target, carries_box=True)
