"""The classic 20x15 floor — Warehouse._build_layout moved here verbatim.

Its grid, zones and aliases are pinned cell by cell in
backend/test_layout_classic_golden.py; every existing test runs on it.
"""
from __future__ import annotations

from typing import Optional

from ..models import CONFIG, CellType
from .base import Layout, rect as _rect

NAME = "classic"


def build(width: Optional[int] = None, height: Optional[int] = None) -> Layout:
    layout = Layout(NAME, width or CONFIG["GRID_WIDTH"], height or CONFIG["GRID_HEIGHT"])
    w, h = layout.width, layout.height

    # Perimeter walls.
    layout._fill([(x, 0) for x in range(w)], CellType.WALL)
    layout._fill([(x, h - 1) for x in range(w)], CellType.WALL)
    layout._fill([(0, y) for y in range(h)], CellType.WALL)
    layout._fill([(w - 1, y) for y in range(h)], CellType.WALL)

    # Racking. Each shelf block is solid; the aisle row below it is the
    # pick face where boxes actually sit and robots can drive.
    racks = [
        ("shelf_a", "Shelf-A", 3, 6),
        ("shelf_b", "Shelf-B", 9, 12),
        ("shelf_c", "Shelf-C", 15, 17),
    ]
    for key, label, x0, x1 in racks:
        layout._fill(_rect(x0, x1, 3, 4), CellType.SHELF)
        face = _rect(x0, x1, 5, 5)
        layout._fill(face, CellType.STORAGE)
        layout._add_zone(key, label, CellType.STORAGE, face)
        # Storage-A / Storage-B / Storage-C address the same pick faces.
        layout.aliases[key.replace("shelf", "storage")] = key
        layout.aliases[label.lower().replace("shelf", "storage")] = key

    # Internal partitions, leaving deliberate gaps for aisles.
    layout._fill([(5, 9), (6, 9), (13, 9), (14, 9)], CellType.WALL)

    # No-go area around the electrical cabinet.
    layout._fill(_rect(1, 2, 3, 5), CellType.RESTRICTED)
    layout._add_zone("restricted_area", "Restricted-Area", CellType.RESTRICTED, _rect(1, 2, 3, 5))

    # Service areas.
    charging = _rect(1, 3, 12, 13)
    layout._fill(charging, CellType.CHARGING)
    layout._add_zone("charging_station", "Charging-Station", CellType.CHARGING, charging)

    parking = _rect(1, 2, 8, 9)
    layout._fill(parking, CellType.PARKING)
    layout._add_zone("parking_area", "Parking-Area", CellType.PARKING, parking)

    packing = _rect(7, 10, 11, 13)
    layout._fill(packing, CellType.PACKING)
    layout._add_zone("packing_area", "Packing-Area", CellType.PACKING, packing)

    unloading = _rect(12, 13, 12, 13)
    layout._fill(unloading, CellType.UNLOADING)
    layout._add_zone("unloading_zone", "Unloading-Zone", CellType.UNLOADING, unloading)

    loading = _rect(16, 18, 11, 13)
    layout._fill(loading, CellType.LOADING)
    layout._add_zone("loading_zone", "Loading-Zone", CellType.LOADING, loading)

    # The four zones the system checks have always required of this floor.
    layout.required_zones = ("charging_station", "loading_zone", "packing_area", "shelf_a")
    return layout
