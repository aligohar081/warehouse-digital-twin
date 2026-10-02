"""People's activities and duty on the distribution-centre floor (multi-
embodiment spec §11.3, §11.4).

While the shift runs, the engine moves the people on shift every
SHIFT_CHECK_EVERY_TICKS, by the role their workforce record gives them:
Sam and Lee pick at Pick 2 when it has work and help at intake otherwise;
Ana and Kai go to the dock a truck is at; Jordan follows the humanoid;
Noor and Mateo keep to the workshop (their jobs take them to jams); Riley
stays outside the pack cells. Everyone takes a 15-minute break in sw_floor
every 2 sim-hours, staggered by 12 minutes per person — the first after two
hours on shift. A person standing in a zone a forklift or hauler is waiting
to enter (PERSON_IN_AISLE) steps aside to the nearest zone those bodies
can't drive in, and stays there YIELD_HOLD_S before going back.

Duty follows the shift clock: an operator with a shift window is on duty
inside it, and an HR state other than ACTIVE (ON_LEAVE, TERMINATED) is off
duty whatever the clock says. The classic floor keeps its wall-clock shifts.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from .. import people
from ..layouts.base import WIDE
from ..models import CellType, EventType, LogCategory, OperatorStatus
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


def _role_target(engine: Any, role: str) -> Optional[str]:
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
        zone = twin.warehouse.zone_of_cell(humanoid.position) if humanoid is not None else None
        return zone.key if zone is not None else HOME_ZONES[role]
    return HOME_ZONES.get(role)


def move_people(engine: Any) -> None:
    """Send everyone on shift where their role, a break or a waiting forklift
    says they should be (ShiftEngine.tick, while the shift runs)."""
    twin = engine.twin
    now = twin.simulation_time
    wanted = _wanted_zones(twin)
    slots = sorted(ROLES)
    for operator in sorted(twin.operators.values(), key=lambda o: o.id):
        role = ROLES.get(operator.worker_id)
        if role is None or operator.status != OperatorStatus.AVAILABLE or operator.in_transit:
            continue  # off duty, on a job of their own, walking, or not someone the engine moves
        hold = engine.yield_until.get(operator.id)
        if operator.zone in wanted:
            target = refuge_zone(twin, operator.zone)
            engine.yield_until[operator.id] = now + YIELD_HOLD_S
        elif hold is not None and now < hold:
            continue
        elif on_break(slots.index(operator.worker_id), now):
            target = BREAK_ZONE
        else:
            target = _role_target(engine, role)
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
