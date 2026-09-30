"""The warehouse environment: a typed grid plus named zones.

The floor plan lives in backend/layouts as named, structured data, not in
the HTML. Warehouse(layout=name) adopts one; the frontend renders whatever
this module reports, so changing a floor plan only means editing its layout.
"""
from __future__ import annotations

from collections import deque
from typing import Any, Dict, List, Optional, Set

from .embodiment import AIR, GROUND, LAYERS, MobilityProfile
from .layouts import build_layout
from .layouts.base import NARROW, WIDE, Slot, Zone
from .models import Cell, CellType, manhattan

#: Cell types no robot ever occupies or flies through.
_SOLID = {CellType.WALL, CellType.DOCK_DOOR}


class Warehouse:
    def __init__(self, width: Optional[int] = None, height: Optional[int] = None,
                 layout: str = "classic") -> None:
        plan = build_layout(layout, width, height)
        self.layout_name: str = plan.name
        self.width = plan.width
        self.height = plan.height
        self.grid: List[List[CellType]] = plan.grid
        self.zones: Dict[str, Zone] = plan.zones
        self.aliases: Dict[str, str] = plan.aliases
        self.walkable_types: Set[CellType] = plan.walkable_types
        self.walkable_extras: Set[Cell] = plan.walkable_extras
        self.crossings: Dict[Cell, str] = plan.crossings
        self.slots: Dict[str, Slot] = plan.slots
        self.fixed_stations: Dict[Cell, str] = plan.fixed_stations
        self.required_zones = plan.required_zones
        self._index()

    def _index(self) -> None:
        """Per-cell lookups derived once from the zones: which zones hold a
        cell, its clearance class, the no-fly set and the slots at a cell."""
        self._cell_zones: Dict[Cell, List[Zone]] = {}
        for zone in self.zones.values():
            for cell in zone.cells:
                self._cell_zones.setdefault(cell, []).append(zone)
        self._no_fly: Set[Cell] = {
            cell for zone in self.zones.values() if zone.attributes.get("no_fly") for cell in zone.cells
        }
        self._clearance: Dict[Cell, str] = {}
        for y in range(self.height):
            for x in range(self.width):
                if not self.is_walkable(x, y):
                    continue
                cell = (x, y)
                marks = {zone.attributes.get("clearance") for zone in self._cell_zones.get(cell, [])}
                if cell in self.crossings:
                    self._clearance[cell] = self.crossings[cell]
                else:
                    self._clearance[cell] = WIDE if WIDE in marks else NARROW
        self._slots_at: Dict[Cell, List[Slot]] = {}
        for slot in sorted(self.slots.values(), key=lambda s: s.level):
            self._slots_at.setdefault(slot.cell, []).append(slot)

    # ------------------------------------------------------------------ #
    # Queries
    # ------------------------------------------------------------------ #
    def is_inside(self, x: int, y: int) -> bool:
        return 0 <= x < self.width and 0 <= y < self.height

    def cell_type(self, x: int, y: int) -> CellType:
        if not self.is_inside(x, y):
            return CellType.WALL
        return self.grid[y][x]

    def is_walkable(self, x: int, y: int) -> bool:
        """Drivable by a robot with no mobility profile: on classic, exactly
        WALKABLE_CELLS; on a layered floor, what a narrow ground robot may use."""
        return self.cell_type(x, y) in self.walkable_types or (x, y) in self.walkable_extras

    def passable(self, cell: Cell, profile: Optional[MobilityProfile] = None, layer: str = GROUND) -> bool:
        """May a robot with `profile` occupy `cell` on `layer` (spec §5.2)?

        No profile: exactly is_walkable (every classic robot). Ground robots:
        walkable cells, WIDE cells only for a wide robot. Drones: on AIR any
        flyable cell; on GROUND only their pad. Fixed equipment never moves.
        An unknown layer name (say "air") raises ValueError rather than being
        read as GROUND.
        """
        if layer not in LAYERS:
            raise ValueError(f"Unknown layer {layer!r} (known: {', '.join(LAYERS)})")
        x, y = cell
        if profile is None:
            return self.is_walkable(x, y)
        if profile.is_fixed:
            return False
        if profile.is_air:
            if layer == AIR:
                return self.is_flyable(cell)
            return self.cell_type(x, y) is CellType.DRONE_PAD
        if layer != GROUND or not self.is_walkable(x, y):
            return False
        return profile.clearance != WIDE or self._clearance.get(cell) == WIDE

    def may_stop(self, cell: Cell, profile: Optional[MobilityProfile] = None, layer: str = GROUND) -> bool:
        """May a robot with `profile` come to rest on `cell` on `layer`?

        Spec §5.2: walkway cells, crossings included, are pass-through only:
        a robot may cross them but never stops or hovers on one. So this is
        passable() minus the WALKWAY cells. Goals, parking spots and deadlock
        sidesteps use it; the cells in between a route use passable(). The
        classic floor has no walkway, so there it is exactly passable().
        """
        return self.passable(cell, profile, layer) and self.cell_type(*cell) is not CellType.WALKWAY

    def walkable_cells(self) -> List[Cell]:
        return [
            (x, y)
            for y in range(self.height)
            for x in range(self.width)
            if self.is_walkable(x, y)
        ]

    def passable_cells(self, profile: Optional[MobilityProfile] = None, layer: str = GROUND) -> List[Cell]:
        return [
            (x, y)
            for y in range(self.height)
            for x in range(self.width)
            if self.passable((x, y), profile, layer)
        ]

    def neighbors(self, cell: Cell, profile: Optional[MobilityProfile] = None,
                  layer: str = GROUND) -> List[Cell]:
        x, y = cell
        candidates = ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1))
        if profile is None:
            return [(nx, ny) for nx, ny in candidates if self.is_walkable(nx, ny)]
        return [c for c in candidates if self.passable(c, profile, layer)]

    def resolve_zone(self, name: Optional[str]) -> Optional[Zone]:
        if not name:
            return None
        key = self.aliases.get(str(name).strip().lower())
        if key is None:
            key = self.aliases.get(str(name).strip().lower().replace(" ", "_"))
        return self.zones.get(key) if key else None

    def zones_of_cell(self, cell: Cell) -> List[Zone]:
        """Every zone containing `cell`, in declaration order."""
        return list(self._cell_zones.get(cell, ()))

    def zone_of_cell(self, cell: Cell) -> Optional[Zone]:
        """The most specific place containing `cell`: the smallest non-route
        zone by cell count, ties going to the zone declared first. Route zones
        (patrol_loop) are paths, not places, so only zones_of_cell lists them."""
        zones = [zone for zone in self._cell_zones.get(cell, ()) if "route" not in zone.attributes]
        if not zones:
            return None
        return min(zones, key=lambda zone: len(zone.cells))  # min() keeps the first of equals

    def clearance(self, cell: Cell) -> Optional[str]:
        """WIDE or NARROW for a walkable cell (WIDE wins where zones
        overlap; unmarked cells are NARROW); None for anything else."""
        return self._clearance.get(cell)

    def is_crossing(self, cell: Cell) -> bool:
        return cell in self.crossings

    def is_no_fly(self, cell: Cell) -> bool:
        return cell in self._no_fly

    def is_flyable(self, cell: Cell) -> bool:
        """A drone may fly over `cell`: any interior, non-wall cell outside
        the no-fly zones (racks and shelves included)."""
        x, y = cell
        if not (0 < x < self.width - 1 and 0 < y < self.height - 1):
            return False
        return self.grid[y][x] not in _SOLID and cell not in self._no_fly

    def slot(self, slot_id: str) -> Optional[Slot]:
        return self.slots.get(slot_id)

    def slots_at(self, cell: Cell) -> List[Slot]:
        """The slots of one rack or shelf cell, lowest level first."""
        return list(self._slots_at.get(cell, ()))

    def slot_at(self, cell: Cell, level: int) -> Optional[Slot]:
        return next((slot for slot in self._slots_at.get(cell, ()) if slot.level == level), None)

    def label_for_cell(self, cell: Cell) -> str:
        zone = self.zone_of_cell(cell)
        if zone:
            return zone.label
        return f"({cell[0]},{cell[1]})"

    def nearest_walkable(self, cell: Cell, blocked: Optional[Set[Cell]] = None,
                         profile: Optional[MobilityProfile] = None, layer: str = GROUND) -> Optional[Cell]:
        """Breadth-first search outwards for the closest cell `profile` may stop
        on, on `layer` (with no profile: the closest drivable cell)."""
        blocked = blocked or set()
        if self.may_stop(cell, profile, layer) and cell not in blocked:
            return cell
        seen = {cell}
        frontier: deque = deque([cell])
        while frontier:
            current = frontier.popleft()
            x, y = current
            for nxt in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                if nxt in seen or not self.is_inside(*nxt):
                    continue
                seen.add(nxt)
                if self.may_stop(nxt, profile, layer) and nxt not in blocked:
                    return nxt
                frontier.append(nxt)
        return None

    def zone_cells_sorted(self, zone: Zone, origin: Cell) -> List[Cell]:
        return sorted(zone.cells, key=lambda c: manhattan(c, origin))

    # ------------------------------------------------------------------ #
    # Serialisation
    # ------------------------------------------------------------------ #
    def to_dict(self) -> Dict[str, Any]:
        cells = [
            {"x": x, "y": y, "type": self.grid[y][x].value}
            for y in range(self.height)
            for x in range(self.width)
            if self.grid[y][x] is not CellType.EMPTY
        ]
        return {
            "width": self.width,
            "height": self.height,
            "cells": cells,
            "zones": [zone.to_dict() for zone in self.zones.values()],
            "walkable_types": sorted(t.value for t in self.walkable_types),
        }

    # Locations a user can pick as a task source or destination.
    def location_options(self) -> List[Dict[str, str]]:
        return [
            {"key": zone.key, "label": zone.label}
            for zone in self.zones.values()
            if zone.cell_type is not CellType.RESTRICTED
        ]
