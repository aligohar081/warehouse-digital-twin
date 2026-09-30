"""Building blocks for named floor layouts: zones, rack and shelf slots,
rectangles, and the canvas a layout module draws on. Warehouse adopts the
finished canvas."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from ..models import Cell, CellType, WALKABLE_CELLS

#: Aisle clearance classes (multi-embodiment spec §3.2).
WIDE = "WIDE"
NARROW = "NARROW"


def _plain(value: Any) -> Any:
    """`value` with every (x, y) cell turned into {"x", "y"}, for JSON."""
    if isinstance(value, tuple) and len(value) == 2 and all(isinstance(v, int) for v in value):
        return {"x": value[0], "y": value[1]}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    return value


class Zone:
    """A named, addressable area of the warehouse floor.

    `attributes` is what the layout declares about the zone (clearance,
    no-fly, stations, ...). A cell may belong to several zones."""

    def __init__(self, key: str, label: str, cell_type: CellType, cells: Sequence[Cell],
                 attributes: Optional[Dict[str, Any]] = None) -> None:
        self.key = key
        self.label = label
        self.cell_type = cell_type
        self.cells: List[Cell] = list(dict.fromkeys(cells))
        self.attributes: Dict[str, Any] = dict(attributes or {})

    @property
    def center(self) -> Cell:
        xs = [c[0] for c in self.cells]
        ys = [c[1] for c in self.cells]
        return (sum(xs) // len(xs), sum(ys) // len(ys))

    def to_dict(self) -> Dict[str, Any]:
        data = {
            "key": self.key,
            "label": self.label,
            "type": self.cell_type.value,
            "cells": [{"x": x, "y": y} for x, y in self.cells],
            "center": {"x": self.center[0], "y": self.center[1]},
        }
        if self.attributes:  # classic zones declare none, so their shape is unchanged
            data["attributes"] = _plain(self.attributes)
        return data


@dataclass(frozen=True)
class Slot:
    """One storage position: a rack or shelf cell at one level. `faces` are
    the aisle cells a robot stands on to serve it."""

    slot_id: str
    kind: str  # "PALLET" (pallet rack) or "TOTE" (tote shelf)
    cell: Cell
    level: int
    height_m: float
    faces: Tuple[Cell, ...]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "slot_id": self.slot_id, "kind": self.kind, "cell": _plain(self.cell),
            "level": self.level, "height_m": self.height_m, "faces": _plain(list(self.faces)),
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
        #: Cells walkable despite their type (walkway crossings, station drop cells).
        self.walkable_extras: Set[Cell] = set()
        #: Walkway crossing cell -> its clearance class.
        self.crossings: Dict[Cell, str] = {}
        self.slots: Dict[str, Slot] = {}
        #: Fixed-equipment station cell (an arm's base) -> the zone it serves.
        self.fixed_stations: Dict[Cell, str] = {}
        #: Zone keys the system checks require this floor to have.
        self.required_zones: Tuple[str, ...] = ()

    def is_inside(self, x: int, y: int) -> bool:
        return 0 <= x < self.width and 0 <= y < self.height

    def _fill(self, cells: Iterable[Cell], cell_type: CellType) -> None:
        for x, y in cells:
            if self.is_inside(x, y):
                self.grid[y][x] = cell_type

    def _add_zone(self, key: str, label: str, cell_type: CellType, cells: Sequence[Cell],
                  **attributes: Any) -> Zone:
        zone = Zone(key, label, cell_type, cells, attributes)
        self.zones[key] = zone
        self.aliases[key] = key
        self.aliases[label.lower()] = key
        self.aliases[label.lower().replace("-", " ")] = key
        self.aliases[label.lower().replace("-", "_")] = key
        return zone

    def _add_slot(self, slot: Slot) -> None:
        self.slots[slot.slot_id] = slot
