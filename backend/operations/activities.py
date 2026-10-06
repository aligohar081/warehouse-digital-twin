"""People's activities and duty on the distribution-centre floor (multi-
embodiment spec §11.3, §11.4).

While the shift runs, the engine moves the people on shift every
SHIFT_CHECK_EVERY_TICKS, by the role their workforce record gives them:
Sam and Lee pick at Pick 2 when it has work and help at intake otherwise;
Ana and Kai go to the dock a truck is at; Jordan follows the humanoid,
heading where it is going (supervisor_zone); Noor and Mateo go to a robot
whose asset has an open work order, else keep to the workshop (their jobs
take them to jams); Riley stays outside the pack cells. Everyone takes a
15-minute break in sw_floor every 2 sim-hours, staggered by 12 minutes per
person — the first after two hours on shift. A person standing in a zone a
forklift or hauler is waiting to enter (PERSON_IN_AISLE) steps aside to the
nearest zone those bodies can't drive in, and stands there YIELD_HOLD_S
before going back.

Duty follows the shift clock: an operator with a shift window is on duty
inside it, and an HR state other than ACTIVE (ON_LEAVE, TERMINATED) is off
duty whatever the clock says. The engine syncs duty before it moves anyone.
The classic floor keeps its wall-clock shifts.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from .. import human_jobs, people
from ..layouts.base import WIDE
from ..models import CONFIG, ActionType, Cell, CellType, EventType, LogCategory, OperatorStatus
from .shift import shift_hour

#: Workforce record -> role on the floor (the demo workforce, spec §4.1).
ROLES: Dict[str, str] = {
    "E-10001": "picker", "E-10002": "picker", "E-10003": "technician", "E-10004": "technician",
    "E-10005": "dock", "E-10006": "supervisor", "E-10007": "dock", "E-10008": "cell_operator",
    "E-10009": "dock", "E-10010": "picker",
}
#: Where each role stands when it has nothing else to do.
HOME_ZONES: Dict[str, str] = {
    "picker": "intake_staging", "technician": "workshop", "dock": "outbound_staging",
    "supervisor": "returns_qc", "cell_operator": "pick_station_1",
}
BREAK_ZONE = "sw_floor"
BREAK_S = 15 * 60
BREAK_EVERY_S = 2 * 3600
BREAK_STAGGER_S = 12 * 60
YIELD_HOLD_S = 30.0
#: Zone types people never stand in (or, for the pack cells' STATION type,
#: aren't sent to rest in: an arm stops while anyone is inside).
_NO_STANDING = {CellType.WALKWAY, CellType.CONVEYOR, CellType.SORTER, CellType.PALLET_RACK, CellType.TOTE_SHELF,
                CellType.DRONE_PAD, CellType.RESTRICTED}


def on_break(index: int, simulation_time: float) -> bool:
    """Is the person with break slot `index` on a break at `simulation_time`?"""
    offset = simulation_time - index * BREAK_STAGGER_S
    cycle = int(offset // BREAK_EVERY_S)
    return cycle >= 1 and offset - cycle * BREAK_EVERY_S < BREAK_S


def refuge_zone(twin: Any, origin: str) -> str:
    """The nearest zone a forklift or hauler can't drive in (no WIDE cell),
    where a person can stand clear: a tote aisle, the top aisle, a station."""
    warehouse = twin.warehouse
    start = warehouse.zones[origin].center
    best, best_distance = origin, None
    for zone in warehouse.zones.values():
        if zone.cell_type in _NO_STANDING or "route" in zone.attributes or zone.attributes.get("fenced"):
            continue
        if any(warehouse.clearance(cell) == WIDE for cell in zone.cells):
            continue
        distance = abs(zone.center[0] - start[0]) + abs(zone.center[1] - start[1])
        if best_distance is None or distance < best_distance:
            best, best_distance = zone.key, distance
    return best


def _wanted_zones(twin: Any) -> set:
    """Zones a forklift or hauler is waiting to drive into."""
    wanted = set()
    for robot in twin.robots.values():
        if robot.wait_reason == "PERSON_IN_AISLE" and robot.wait_cell is not None:
            here = {zone.key for zone in twin.warehouse.zones_of_cell(robot.position)}
            wanted |= {zone.key for zone in twin.warehouse.zones_of_cell(robot.wait_cell)
                       if zone.key not in here}
    return wanted


def _route_ahead(twin: Any, robot: Any) -> List[Cell]:
    """The cells `robot`'s current NAVIGATE still has to cross, its target
    last; [] while it isn't driving one."""
    task = twin.tasks.get(robot.current_task) if robot.current_task else None
    action = task.current_action if task is not None and not task.is_terminal else None
    if action is None or action.type is not ActionType.NAVIGATE or action.target is None:
        return []
    target = tuple(action.target)
    path = [tuple(cell) for cell in robot.current_path] if robot.target_position == target else []
    return path if path and path[-1] == target else path + [target]


def supervisor_zone(twin: Any, humanoid: Any, supervisor: Any) -> Optional[str]:
    """Where the humanoid's supervisor should stand (spec §6, §11.3). While
    he supervises it from where he stands, there: any walk stops it until he
    arrives, since a person walking is in no zone. Otherwise, with it driving
    somewhere, the zone along its route that keeps it supervised the farthest
    towards its destination — the destination's own zone once that is in
    reach, the nearest walk among equals; one out of reach would leave it
    stopped where it is for good. With it standing still, its own zone."""
    warehouse = twin.warehouse
    if supervisor.zone is not None and people.supervisor_nearby(twin, humanoid.position, supervisor):
        return supervisor.zone
    route = _route_ahead(twin, humanoid)
    places = [zone for zone in people.zones_around(warehouse, humanoid.position) if "route" not in zone.attributes]
    here = warehouse.zone_of_cell(humanoid.position) or min(places, key=lambda zone: len(zone.cells), default=None)
    if not route:
        return here.key if here is not None else None
    touching: Dict[Tuple[str, Cell], bool] = {}

    def watches(key: str, cell: Cell) -> bool:  # would someone standing in zone `key` supervise it at `cell`?
        if (key, cell) not in touching:
            touching[key, cell] = any(people.zones_touch(warehouse, key, zone.key)
                                      for zone in people.zones_around(warehouse, cell))
        return touching[key, cell]

    def reach(key: str) -> int:  # how many of the route's cells it drives supervised from zone `key`
        for number, cell in enumerate(route):
            if not watches(key, cell):
                return number
        return len(route)

    candidates: List[str] = []
    for cell in [humanoid.position] + route:
        zone = warehouse.zone_of_cell(cell)
        if (zone is None or zone.key in candidates or zone.cell_type in _NO_STANDING
                or zone.attributes.get("fenced") or not watches(zone.key, humanoid.position)):
            continue
        candidates.append(zone.key)
    if not candidates:
        return here.key if here is not None else None
    start = warehouse.zones[supervisor.zone].center if supervisor.zone is not None else humanoid.position
    return max(candidates, key=lambda key: (reach(key), -(abs(warehouse.zones[key].center[0] - start[0])
                                                          + abs(warehouse.zones[key].center[1] - start[1]))))


def open_work_orders(twin: Any) -> List[Tuple[Any, List[Dict[str, Any]]]]:
    """(robot, its asset's open work orders) for every robot on the floor
    that has any, by robot name — one read of the fleet inventory. If the
    inventory can't answer, there are none: it sends no one anywhere."""
    by_asset: Dict[str, List[Dict[str, Any]]] = {}
    try:
        for work_order in twin.inventory.open_work_orders():
            by_asset.setdefault(work_order["asset_id"], []).append(work_order)
    except Exception:  # noqa: BLE001 - an inventory failure must not stop the shift
        return []
    return [(robot, by_asset[robot.asset_id]) for robot in sorted(twin.robots.values(), key=lambda r: r.name)
            if robot.asset_id in by_asset]


def _work_order_zone(twin: Any, technician: Any, found: List[Tuple[Any, List[Dict[str, Any]]]]) -> Optional[str]:
    """A technician's work order (Plan ruling 12), from open_work_orders: the
    zone of the robot whose open work order names them, else of the first
    robot with one; never a fenced pack cell (an arm stops while anyone is in
    it), but the zone beside it. None with no work order open."""
    robot = next((robot for robot, orders in found
                  if any(wo.get("technician_id") == technician.worker_id for wo in orders)), None)
    if robot is None and found:
        robot = found[0][0]
    zone = twin.warehouse.zone_of_cell(robot.position) if robot is not None else None
    return human_jobs.exit_zone(twin, zone.key) if zone is not None else None


def _role_target(engine: Any, role: str, operator: Any,
                 work_orders: List[Tuple[Any, List[Dict[str, Any]]]]) -> Optional[str]:
    twin, orders = engine.twin, engine.orders.orders.values()
    if role == "picker":
        busy = any(o.status == "IN_PROGRESS" and o.pick_station == "pick_station_2" for o in orders)
        manual = any(t.type.value == "MANUAL_PICK" and not t.is_terminal for t in twin.tasks.tasks.values())
        return "pick_station_2" if busy or manual else HOME_ZONES[role]
    if role == "dock":
        trucks = [o for o in orders if o.kind == "INBOUND" and not o.is_terminal and o.lines]
        return trucks[-1].lines[0]["dock"] if trucks else HOME_ZONES[role]
    if role == "supervisor":
        humanoid = next((r for r in twin.robots.values() if r.mobility is not None and r.mobility.supervision), None)
        zone = supervisor_zone(twin, humanoid, operator) if humanoid is not None else None
        return zone or HOME_ZONES[role]
    if role == "technician":
        return _work_order_zone(twin, operator, work_orders) or HOME_ZONES[role]
    return HOME_ZONES.get(role)


def move_people(engine: Any) -> None:
    """Send everyone on shift where their role, a break or a waiting forklift
    says they should be (ShiftEngine.tick, while the shift runs). Duty is
    synced first, so no one is placed and then taken off the floor in one
    check, and no one coming on duty waits a check to be placed."""
    twin = engine.twin
    sync_duty(twin)
    now = twin.simulation_time
    wanted = _wanted_zones(twin)
    slots = sorted(ROLES)
    work_orders = open_work_orders(twin)
    for operator in sorted(twin.operators.values(), key=lambda o: o.id):
        role = ROLES.get(operator.worker_id)
        if role is None or operator.status != OperatorStatus.AVAILABLE or operator.in_transit:
            continue  # off duty, on a job of their own, walking, or not someone the engine moves
        hold = engine.yield_until.get(operator.id)
        if operator.zone in wanted:
            target = refuge_zone(twin, operator.zone)
            # The hold is YIELD_HOLD_S standing in the refuge: it counts from arrival.
            walk = people.transit_ticks(twin.warehouse, operator.zone, target) * CONFIG["TICK_DT"] \
                if target != operator.zone else 0.0
            engine.yield_until[operator.id] = now + walk + YIELD_HOLD_S
        elif hold is not None and now < hold:
            if now < hold - YIELD_HOLD_S:  # there before the walk would end (put there): it counts from now
                engine.yield_until[operator.id] = now + YIELD_HOLD_S
            continue
        elif on_break(slots.index(operator.worker_id), now):
            target = BREAK_ZONE
        else:
            target = _role_target(engine, role, operator, work_orders)
        if target is None or target == operator.zone:
            continue
        if operator.zone is None:
            people.place(twin, operator, target)  # clocking on: straight to their place
        else:
            people.start_transit(twin, operator, target)


def sync_duty(twin: Any) -> None:
    """On the new floor duty follows the shift clock and the HR state
    (Simulator._check_operator_shifts calls this instead of the wall clock)."""
    hour = shift_hour(twin.simulation_time)
    for operator in twin.operators.values():
        if operator.status == OperatorStatus.ON_TASK:
            continue
        employed = operator.employment_status in (None, "ACTIVE")
        should_be = OperatorStatus.AVAILABLE if employed and operator.is_within_shift(hour) \
            else OperatorStatus.OFF_DUTY
        if operator.status == should_be:
            continue
        previous = operator.set_status(should_be)
        why = f"shift {operator.shift_start_hour}-{operator.shift_end_hour}" if employed \
            else f"HR state {operator.employment_status}"
        twin.events.emit(
            EventType.OPERATOR_SHIFT_CHANGED,
            f"{operator.name} {previous.value} → {should_be.value} ({why}, shift clock {hour:02d}h)",
            category=LogCategory.TASK,
            data={"operator_id": operator.id, "hour": hour, "employment_status": operator.employment_status},
        )
