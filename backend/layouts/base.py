"""Building blocks for named floor layouts: zones, rectangles and the
canvas a layout module draws on. Warehouse adopts the finished canvas."""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Sequence, Set, Tuple

from ..models import Cell, CellType, WALKABLE_CELLS


class Zone:
    """A named, addressable area of the warehouse floor."""

    def __init__(self, key: str, label: str, cell_type: CellType, cells: Sequence[Cell]) -> None:
        self.key = key
        self.label = label
        self.cell_type = cell_type
        self.cells: List[Cell] = list(cells)

    @property
    def center(self) -> Cell:
        xs = [c[0] for c in self.cells]
        ys = [c[1] for c in self.cells]
        return (sum(xs) // len(xs), sum(ys) // len(ys))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "type": self.cell_type.value,
            "cells": [{"x": x, "y": y} for x, y in self.cells],
            "center": {"x": self.center[0], "y": self.center[1]},
        }


def rect(x0: int, x1: int, y0: int, y1: int) -> List[Cell]:
    """Every cell of the inclusive rectangle x0..x1 × y0..y1, row by row."""
    return [(x, y) for y in range(y0, y1 + 1) for x in range(x0, x1 + 1)]


class Layout:
    """A floor plan as a layout module draws it: the typed grid, the named
    zones and the aliases they resolve by, plus what the floor declares
    about itself."""

    def __init__(self, name: str, width: int, height: int) -> None:
        self.name = name
        self.width = width
        self.height = height
        self.grid: List[List[CellType]] = [
            [CellType.EMPTY for _ in range(width)] for _ in range(height)
        ]
        self.zones: Dict[str, Zone] = {}
        self.aliases: Dict[str, str] = {}
        #: Cell types a robot without a mobility profile may drive through.
        self.walkable_types: Set[CellType] = WALKABLE_CELLS
        #: Zone keys the system checks require this floor to have.
        self.required_zones: Tuple[str, ...] = ()

    def is_inside(self, x: int, y: int) -> bool:
        return 0 <= x < self.width and 0 <= y < self.height

    def _fill(self, cells: Iterable[Cell], cell_type: CellType) -> None:
        for x, y in cells:
            if self.is_inside(x, y):
                self.grid[y][x] = cell_type

    def _add_zone(self, key: str, label: str, cell_type: CellType, cells: Sequence[Cell]) -> Zone:
        zone = Zone(key, label, cell_type, cells)
        self.zones[key] = zone
        self.aliases[key] = key
        self.aliases[label.lower()] = key
        self.aliases[label.lower().replace("-", " ")] = key
        self.aliases[label.lower().replace("-", "_")] = key
        return zone
