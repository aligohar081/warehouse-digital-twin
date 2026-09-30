"""Classic layout guard (multi-embodiment spec §15): today's 20x15 floor,
pinned cell by cell before its layout code moved into backend/layouts.
If any of these fail, the classic floor changed — and it must not."""
from backend.models import CellType
from backend.warehouse import Warehouse

LEGEND = {
    "#": CellType.WALL, ".": CellType.EMPTY, "S": CellType.SHELF, "s": CellType.STORAGE,
    "C": CellType.CHARGING, "L": CellType.LOADING, "U": CellType.UNLOADING,
    "P": CellType.PACKING, "R": CellType.RESTRICTED, "K": CellType.PARKING,
}

CLASSIC_GRID = [
    "####################",
    "#..................#",
    "#..................#",
    "#RRSSSS..SSSS..SSS.#",
    "#RRSSSS..SSSS..SSS.#",
    "#RRssss..ssss..sss.#",
    "#..................#",
    "#..................#",
    "#KK................#",
    "#KK..##......##....#",
    "#..................#",
    "#......PPPP.....LLL#",
    "#CCC...PPPP.UU..LLL#",
    "#CCC...PPPP.UU..LLL#",
    "####################",
]

#: (key, label, type, cells, center), in declaration order.
CLASSIC_ZONES = [
    ("shelf_a", "Shelf-A", "STORAGE", [(3, 5), (4, 5), (5, 5), (6, 5)], (4, 5)),
    ("shelf_b", "Shelf-B", "STORAGE", [(9, 5), (10, 5), (11, 5), (12, 5)], (10, 5)),
    ("shelf_c", "Shelf-C", "STORAGE", [(15, 5), (16, 5), (17, 5)], (16, 5)),
    ("restricted_area", "Restricted-Area", "RESTRICTED",
     [(1, 3), (2, 3), (1, 4), (2, 4), (1, 5), (2, 5)], (1, 4)),
    ("charging_station", "Charging-Station", "CHARGING",
     [(1, 12), (2, 12), (3, 12), (1, 13), (2, 13), (3, 13)], (2, 12)),
    ("parking_area", "Parking-Area", "PARKING", [(1, 8), (2, 8), (1, 9), (2, 9)], (1, 8)),
    ("packing_area", "Packing-Area", "PACKING",
     [(7, 11), (8, 11), (9, 11), (10, 11), (7, 12), (8, 12), (9, 12), (10, 12),
      (7, 13), (8, 13), (9, 13), (10, 13)], (8, 12)),
    ("unloading_zone", "Unloading-Zone", "UNLOADING", [(12, 12), (13, 12), (12, 13), (13, 13)], (12, 12)),
    ("loading_zone", "Loading-Zone", "LOADING",
     [(16, 11), (17, 11), (18, 11), (16, 12), (17, 12), (18, 12), (16, 13), (17, 13), (18, 13)], (17, 12)),
]

CLASSIC_ALIASES = {
    "shelf_a": "shelf_a", "shelf-a": "shelf_a", "shelf a": "shelf_a", "storage_a": "shelf_a", "storage-a": "shelf_a",
    "shelf_b": "shelf_b", "shelf-b": "shelf_b", "shelf b": "shelf_b", "storage_b": "shelf_b", "storage-b": "shelf_b",
    "shelf_c": "shelf_c", "shelf-c": "shelf_c", "shelf c": "shelf_c", "storage_c": "shelf_c", "storage-c": "shelf_c",
    "restricted_area": "restricted_area", "restricted-area": "restricted_area", "restricted area": "restricted_area",
    "charging_station": "charging_station", "charging-station": "charging_station",
    "charging station": "charging_station",
    "parking_area": "parking_area", "parking-area": "parking_area", "parking area": "parking_area",
    "packing_area": "packing_area", "packing-area": "packing_area", "packing area": "packing_area",
    "unloading_zone": "unloading_zone", "unloading-zone": "unloading_zone", "unloading zone": "unloading_zone",
    "loading_zone": "loading_zone", "loading-zone": "loading_zone", "loading zone": "loading_zone",
}


def test_classic_grid_is_pinned_cell_by_cell():
    warehouse = Warehouse()
    assert (warehouse.width, warehouse.height) == (20, 15)
    assert len(CLASSIC_GRID) == warehouse.height
    for y, row in enumerate(CLASSIC_GRID):
        assert len(row) == warehouse.width
        for x, code in enumerate(row):
            assert warehouse.cell_type(x, y) is LEGEND[code], (x, y)


def test_classic_zones_are_pinned_in_declaration_order():
    expected = [
        {
            "key": key, "label": label, "type": cell_type,
            "cells": [{"x": x, "y": y} for x, y in cells],
            "center": {"x": center[0], "y": center[1]},
        }
        for key, label, cell_type, cells, center in CLASSIC_ZONES
    ]
    assert [zone.to_dict() for zone in Warehouse().zones.values()] == expected


def test_classic_aliases_and_walkable_types_are_pinned():
    warehouse = Warehouse()
    assert warehouse.aliases == CLASSIC_ALIASES
    layout = warehouse.to_dict()
    assert set(layout) == {"width", "height", "cells", "zones", "walkable_types"}
    assert layout["walkable_types"] == ["CHARGING", "EMPTY", "LOADING", "PACKING", "PARKING", "STORAGE", "UNLOADING"]
    assert len(layout["cells"]) == sum(1 for row in CLASSIC_GRID for code in row if code != ".")
