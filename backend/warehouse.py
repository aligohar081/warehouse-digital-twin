"""The warehouse environment: a typed grid plus named zones.

The floor plan lives in backend/layouts as named, structured data, not in
the HTML. Warehouse(layout=name) adopts one; the frontend renders whatever
this module reports, so changing a floor plan only means editing its layout.
"""
from __future__ import annotations

from collections import deque
from typing import Any, Dict, List, Optional, Set

from .layouts import build_layout
from .layouts.base import Zone  # noqa: F401  (re-exported; Zone used to live here)
from .models import Cell, CellType, manhattan


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
        self.required_zones = plan.required_zones

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
        return self.cell_type(x, y) in self.walkable_types

    def walkable_cells(self) -> List[Cell]:
        return [
            (x, y)
            for y in range(self.height)
            for x in range(self.width)
            if self.is_walkable(x, y)
        ]

    def neighbors(self, cell: Cell) -> List[Cell]:
        x, y = cell
        return [
            (nx, ny)
            for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1))
            if self.is_walkable(nx, ny)
        ]

    def resolve_zone(self, name: Optional[str]) -> Optional[Zone]:
        if not name:
            return None
        key = self.aliases.get(str(name).strip().lower())
        if key is None:
            key = self.aliases.get(str(name).strip().lower().replace(" ", "_"))
        return self.zones.get(key) if key else None

    def zone_of_cell(self, cell: Cell) -> Optional[Zone]:
        for zone in self.zones.values():
            if cell in zone.cells:
                return zone
        return None

    def label_for_cell(self, cell: Cell) -> str:
        zone = self.zone_of_cell(cell)
        if zone:
            return zone.label
        return f"({cell[0]},{cell[1]})"

    def nearest_walkable(self, cell: Cell, blocked: Optional[Set[Cell]] = None) -> Optional[Cell]:
        """Breadth-first search outwards for the closest drivable cell."""
        blocked = blocked or set()
        if self.is_walkable(*cell) and cell not in blocked:
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
                if self.is_walkable(*nxt) and nxt not in blocked:
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
