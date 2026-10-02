"""People on the floor, modelled as zone presence (multi-embodiment spec §6).

People are not grid entities. An operator is in one zone (Operator.zone),
off the floor (zone None), or walking between two zones for as long as the
walk takes. Only a walk that crosses the pedestrian walkway strip (or starts
or ends on it) is "on the walkway" and holds the robot crossings; a walk that
stays on one side of it does not. Robots never collide with people; they obey
physical waits instead (see Simulator._crossing_wait, _aisle_wait and
_supervision_hold). These helpers answer every "who is where" question the
robots' rules ask, including whether a supervised body is supervised.
"""
from __future__ import annotations

from typing import Any, List, Optional, Set, Tuple

from .eligibility import cert_scope_ok, supervision_ok
from .embodiment import seconds_to_ticks
from .models import CONFIG, Cell, CellType, EventType, LogCategory, OperatorStatus, manhattan

#: The site every new-floor credential scope is checked against (spec §4.1).
FLOOR_SITE = "WH-01"


def _zone_key(warehouse: Any, zone: str) -> str:
    resolved = warehouse.resolve_zone(zone)
    if resolved is None:
        raise ValueError(f"Unknown zone {zone!r}")
    return resolved.key


def transit_ticks(warehouse: Any, from_zone: str, to_zone: str) -> int:
    """How long walking between two zones takes: manhattan(centre, centre)
    × CELL_SIZE_M ÷ PERSON_WALK_SPEED_MPS, as whole ticks (at least one)."""
    a, b = warehouse.zones[from_zone].center, warehouse.zones[to_zone].center
    seconds = manhattan(a, b) * CONFIG["CELL_SIZE_M"] / CONFIG["PERSON_WALK_SPEED_MPS"]
    return max(1, seconds_to_ticks(seconds))


def _sides(zone: Any, axis: int, line: int) -> Set[int]:
    """Which sides of the line at `line` (on `axis`) the zone's cells lie on."""
    return {(cell[axis] > line) - (cell[axis] < line) for cell in zone.cells} - {0}


def crosses_walkway(warehouse: Any, from_zone: str, to_zone: str) -> bool:
    """Does a walk from `from_zone` to `to_zone` cross the pedestrian walkway?

    True when either end is a WALKWAY zone, or when the two zones' cells are
    not all on one side of one. A WALKWAY zone whose cells share one x is a
    vertical strip at that x (one sharing a y, a horizontal strip at that y).
    Sides go by the zones' cells, not their centres: a zone with cells on both
    sides of the strip (cross_aisle, top_aisle) may be walked across, so every
    walk to or from it counts. A floor with no WALKWAY zone (classic) has no
    walkway walks.
    """
    a = warehouse.zones[_zone_key(warehouse, from_zone)]
    b = warehouse.zones[_zone_key(warehouse, to_zone)]
    for walkway in warehouse.zones.values():
        if walkway.cell_type is not CellType.WALKWAY:
            continue
        if walkway is a or walkway is b:
            return True
        if len({x for x, _ in walkway.cells}) == 1:
            axis = 0
        elif len({y for _, y in walkway.cells}) == 1:
            axis = 1
        else:
            continue  # not a straight strip: only a walk to or from it counts
        line = walkway.cells[0][axis]
        if len(_sides(a, axis, line) | _sides(b, axis, line)) > 1:
            return True  # opposite sides, or a zone straddling the strip
    return False


def place(twin: Any, operator: Any, zone: Optional[str]) -> None:
    """Put `operator` straight into `zone`, with no walk (clocking in, or a
    seed placing people). `None` takes them off the floor."""
    if zone is not None:
        if operator.status == OperatorStatus.OFF_DUTY:
            raise ValueError(f"{operator.name} is off duty and cannot be on the floor")
        zone = _zone_key(twin.warehouse, zone)
    operator.zone = zone
    operator.transit_to = operator.transit_until_tick = None
    operator.touch()


def start_transit(twin: Any, operator: Any, zone: str) -> int:
    """Send `operator` walking from their zone to `zone`; returns the arrival
    tick. Until then they are in transit between the two and in neither zone."""
    if operator.zone is None:
        raise ValueError(f"{operator.name} is not on the floor")
    if operator.in_transit:
        raise ValueError(f"{operator.name} is already walking to {operator.transit_to}")
    target = _zone_key(twin.warehouse, zone)
    if target == operator.zone:
        return twin.tick_count
    operator.transit_to = target
    operator.transit_until_tick = twin.tick_count + transit_ticks(twin.warehouse, operator.zone, target)
    operator.touch()
    return operator.transit_until_tick


def update_transits(twin: Any) -> List[Any]:
    """Finish every walk that is over (Simulator.tick calls this each tick,
    before robots move). Returns the operators who arrived."""
    arrived = []
    for operator in twin.operators.values():
        if not operator.in_transit or twin.tick_count < operator.transit_until_tick:
            continue
        origin = operator.zone
        operator.zone, operator.transit_to, operator.transit_until_tick = operator.transit_to, None, None
        operator.touch()
        twin.events.emit(
            EventType.PERSON_MOVED,
            f"{operator.name} walked from {origin} to {operator.zone}",
            category=LogCategory.OPERATIONS,
            data={"operator_id": operator.id, "from": origin, "to": operator.zone},
        )
        arrived.append(operator)
    return arrived


def people_in(twin: Any, zone_key: str) -> List[Any]:
    """Everyone standing in `zone_key`. People in transit are in no zone."""
    return [o for o in twin.operators.values() if o.zone == zone_key and not o.in_transit]


def people_at(twin: Any, cell: Cell) -> List[Any]:
    """Everyone standing in any zone that contains `cell` — the people a robot
    entering or working at `cell` would be next to."""
    keys = {zone.key for zone in twin.warehouse.zones_of_cell(cell)}
    return [o for o in twin.operators.values() if o.zone in keys and not o.in_transit]


def in_transit(twin: Any) -> List[Any]:
    """Everyone walking between zones right now, wherever they are headed."""
    return [o for o in twin.operators.values() if o.in_transit]


def anyone_in_transit(twin: Any) -> bool:
    """Is anyone walking between zones right now?"""
    return any(o.in_transit for o in twin.operators.values())


def on_walkway(twin: Any) -> List[Any]:
    """Everyone walking right now whose walk crosses the pedestrian walkway."""
    return [o for o in in_transit(twin) if crosses_walkway(twin.warehouse, o.zone, o.transit_to)]


def anyone_on_walkway(twin: Any) -> bool:
    """Is anyone on the walkway — the question every robot crossing asks?"""
    return bool(on_walkway(twin))


def zones_touch(warehouse: Any, a: str, b: str) -> bool:
    """True when zones `a` and `b` are the same, overlap, or share an edge."""
    if a == b:
        return True
    cells_a, cells_b = set(warehouse.zones[a].cells), set(warehouse.zones[b].cells)
    if cells_a & cells_b:
        return True
    return any((x + dx, y + dy) in cells_b
               for x, y in cells_a for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)))


def supervisor_nearby(twin: Any, robot_cell: Cell, supervisor: Any) -> bool:
    """The proximity half of the humanoid supervision rule (spec §6): the
    supervisor stands in a zone that holds `robot_cell`, or one sharing an
    edge with such a zone. Off the floor or in transit is never nearby."""
    if supervisor.zone is None or supervisor.in_transit:
        return False
    return any(zones_touch(twin.warehouse, zone.key, supervisor.zone)
               for zone in twin.warehouse.zones_of_cell(robot_cell))


# --------------------------------------------------------------------------- #
# Supervision (spec §6): the humanoid works only under a supervisor
# --------------------------------------------------------------------------- #
def supervisors(twin: Any, robot: Any) -> List[Any]:
    """Everyone on shift whose valid credential of the kind `robot`'s body
    requires covers its model and the floor's site."""
    required = robot.mobility.supervision if robot.mobility is not None else None
    if not required:
        return []
    return [o for o in twin.operators.values()
            if o.status != OperatorStatus.OFF_DUTY
            and cert_scope_ok(o.certification_scopes, required, robot.model_code, FLOOR_SITE)[0]]


def supervision_available(twin: Any, robot: Any) -> Tuple[bool, Optional[str]]:
    """Can `robot`'s supervision be satisfied at all? Someone on shift holds
    a valid, in-scope credential of the kind its body requires. This is the
    gate's half (spec §9.1, §10.1); being near it is the runtime half."""
    required = robot.mobility.supervision if robot.mobility is not None else None
    if not required:
        return True, None
    holders = [o for o in twin.operators.values() if required in o.certification_scopes]
    on_shift = [o for o in holders if o.status != OperatorStatus.OFF_DUTY]
    return supervision_ok(required, bool(on_shift), bool(holders), bool(supervisors(twin, robot)))


def supervision_status(twin: Any, robot: Any) -> Tuple[bool, Optional[str]]:
    """Is `robot` supervised right now: is a qualified supervisor on shift in
    its zone or a zone sharing an edge with it?"""
    ok, reason = supervision_available(twin, robot)
    if not ok:
        return ok, reason
    if any(supervisor_nearby(twin, robot.position, person) for person in supervisors(twin, robot)):
        return True, None
    return False, "no supervisor is in or next to its zone"
