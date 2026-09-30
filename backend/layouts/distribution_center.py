"""The 32x20 distribution-centre floor (multi-embodiment spec §3).

1 cell = 1.5 m, so the building is 48 x 30 m. Goods flow west to east:
inbound docks on the west wall, pallet racks and tote shelves in the middle,
a pedestrian walkway down x=17, and pick, pack, sort and outbound docks east
of it. Zones are declared in the spec's table order, which is also the
tie-break order when two overlapping zones have the same size.
"""
from __future__ import annotations

from typing import List, Optional, Sequence

from ..models import Cell, CellType
from .base import NARROW, WIDE, Layout, Slot, rect

NAME = "distribution_center"
WIDTH, HEIGHT = 32, 20

#: Cell types a ground robot may drive on, before clearance and stations.
GROUND_TYPES = {
    CellType.EMPTY, CellType.DOCK, CellType.STAGING, CellType.CHARGING,
    CellType.PARKING, CellType.WORKSHOP,
}

#: Walkway cells robots may cross, and each crossing's clearance class.
CROSSINGS = {
    (17, 1): NARROW, (17, 10): WIDE, (17, 11): WIDE,
    (17, 13): NARROW, (17, 15): NARROW, (17, 17): NARROW,
}

#: Pallet rack rows -> the pallet-aisle row each is served from.
PALLET_RACK_FACES = {2: 3, 5: 4, 6: 7, 9: 8}
PALLET_LEVELS, PALLET_LEVEL_SPACING_M = 5, 1.0
TOTE_SHELF_ROWS = (12, 14, 16, 18)
TOTE_AISLE_ROWS = (13, 15, 17)
TOTE_LEVELS, TOTE_LEVEL_SPACING_M = 3, 0.6
RACK_COLUMNS = range(8, 17)

REQUIRED_ZONES = (
    "charging_station", "parking_area", "drone_pad", "pick_station_1",
    "pack_cell_1", "sorter", "dock_1", "dock_4",
)


def _cells(*parts: Sequence[Cell]) -> List[Cell]:
    """Concatenate rectangles, keeping first-seen order and dropping repeats."""
    return list(dict.fromkeys(cell for part in parts for cell in part))


def _loop(corners: Sequence[Cell]) -> List[Cell]:
    """The cells of a closed rectilinear route through `corners`, in order."""
    cells: List[Cell] = []
    for (x0, y0), (x1, y1) in zip(corners, list(corners[1:]) + [corners[0]]):
        dx = (x1 > x0) - (x1 < x0)
        dy = (y1 > y0) - (y1 < y0)
        x, y = x0, y0
        while (x, y) != (x1, y1):
            cells.append((x, y))
            x, y = x + dx, y + dy
    return cells


def build(width: Optional[int] = None, height: Optional[int] = None) -> Layout:
    if (width or WIDTH, height or HEIGHT) != (WIDTH, HEIGHT):
        raise ValueError(f"The {NAME} floor is fixed at {WIDTH}x{HEIGHT}")
    layout = Layout(NAME, WIDTH, HEIGHT)
    layout.walkable_types = set(GROUND_TYPES)

    def zone(key: str, label: str, cell_type: CellType, cells: Sequence[Cell], **attributes) -> None:
        # Aisle-type (EMPTY) zones only name cells; typed zones also paint them.
        if cell_type is not CellType.EMPTY:
            layout._fill(cells, cell_type)
        layout._add_zone(key, label, cell_type, cells, **attributes)

    # Perimeter walls, with dock doors cut into the west and east walls.
    layout._fill([(x, 0) for x in range(WIDTH)], CellType.WALL)
    layout._fill([(x, HEIGHT - 1) for x in range(WIDTH)], CellType.WALL)
    layout._fill([(0, y) for y in range(HEIGHT)], CellType.WALL)
    layout._fill([(WIDTH - 1, y) for y in range(HEIGHT)], CellType.WALL)
    doors = {
        "dock_1": rect(0, 0, 2, 5), "dock_2": rect(0, 0, 7, 10), "dock_3": rect(31, 31, 1, 4),
        "dock_4": rect(31, 31, 12, 14), "dock_5": rect(31, 31, 16, 18),
    }
    for cells in doors.values():
        layout._fill(cells, CellType.DOCK_DOOR)

    zone("dock_1", "Dock 1", CellType.DOCK, rect(1, 3, 2, 5),
         dock_role="INBOUND", clearance=WIDE, no_fly=True, doors=doors["dock_1"])
    zone("dock_2", "Dock 2", CellType.DOCK, rect(1, 3, 7, 10),
         dock_role="INBOUND", clearance=WIDE, no_fly=True, doors=doors["dock_2"])
    zone("intake_staging", "Intake staging", CellType.STAGING, rect(4, 6, 2, 10),
         clearance=WIDE, staging="INBOUND")
    zone("pallet_aisle_1", "Pallet aisle 1", CellType.EMPTY, rect(8, 16, 3, 4),
         clearance=WIDE, serves_rack_rows=[2, 5])
    zone("pallet_aisle_2", "Pallet aisle 2", CellType.EMPTY, rect(8, 16, 7, 8),
         clearance=WIDE, serves_rack_rows=[6, 9])
    zone("pallet_racks", "Pallet racks", CellType.PALLET_RACK,
         _cells(*(rect(8, 16, y, y) for y in PALLET_RACK_FACES)),
         levels=PALLET_LEVELS, level_spacing_m=PALLET_LEVEL_SPACING_M)
    for number, y in enumerate(TOTE_AISLE_ROWS, start=1):
        zone(f"tote_aisle_{number}", f"Tote aisle {number}", CellType.EMPTY, rect(8, 16, y, y),
             clearance=NARROW)
    zone("tote_shelves", "Tote shelves", CellType.TOTE_SHELF,
         _cells(*(rect(8, 16, y, y) for y in TOTE_SHELF_ROWS)),
         levels=TOTE_LEVELS, level_spacing_m=TOTE_LEVEL_SPACING_M)
    zone("walkway", "Pedestrian walkway", CellType.WALKWAY, rect(17, 17, 1, 18),
         people_only=True, crossings=sorted(CROSSINGS, key=lambda c: c[1]))
    zone("main_aisle", "Main aisle", CellType.EMPTY, rect(7, 7, 1, 18), clearance=WIDE)
    zone("cross_aisle", "Cross aisle", CellType.EMPTY,
         [cell for cell in rect(4, 30, 10, 11) if cell[0] != 17], clearance=WIDE)
    zone("top_aisle", "Top aisle", CellType.EMPTY,
         _cells(rect(8, 16, 1, 1), rect(18, 18, 1, 9), rect(18, 18, 12, 18)), clearance=NARROW)
    zone("pallet_lane", "Pallet lane", CellType.EMPTY,
         _cells(rect(25, 26, 5, 9), rect(18, 30, 5, 5)), clearance=WIDE)
    zone("ne_floor", "North-east floor", CellType.EMPTY,
         _cells(rect(18, 19, 6, 9), rect(19, 30, 9, 9)), clearance=NARROW)
    zone("charging_station", "Charging", CellType.CHARGING, rect(1, 3, 12, 14), clearance=WIDE)
    zone("parking_area", "Parking", CellType.PARKING, rect(4, 6, 12, 14), clearance=WIDE)
    zone("workshop", "Workshop", CellType.WORKSHOP, rect(1, 6, 16, 18),
         clearance=WIDE, maintenance_bay=True)
    zone("sw_floor", "South-west floor", CellType.EMPTY, rect(1, 6, 15, 15), clearance=WIDE)
    zone("drone_pad", "Drone pad", CellType.DRONE_PAD, rect(19, 21, 1, 3), drones_only=True)
    zone("outbound_staging", "Outbound staging", CellType.STAGING, rect(22, 27, 1, 4),
         clearance=WIDE, staging="OUTBOUND_PALLETS")
    zone("dock_3", "Dock 3", CellType.DOCK, rect(28, 30, 1, 4),
         dock_role="OUTBOUND_PALLETS", clearance=WIDE, no_fly=True, doors=doors["dock_3"])
    returns = rect(20, 24, 6, 8)
    zone("returns_qc", "Returns / QC", CellType.STATION, returns,
         clearance=NARROW, robot_cells=returns, home_of="HUMANOID")
    zone("restricted_area", "Restricted (electrical)", CellType.RESTRICTED, rect(27, 30, 6, 8),
         robots_allowed=False, no_fly=True, people_certification="electrical_safety")
    zone("pick_station_1", "Pick 1", CellType.STATION, rect(19, 21, 12, 14),
         picker="ROBOT", tote_drop=(19, 14), work_cell=(20, 14), conveyor_infeed=(20, 15),
         robot_cells=[(19, 14), (20, 14)])
    zone("pick_station_2", "Pick 2", CellType.STATION, rect(19, 21, 16, 18),
         picker="HUMAN", tote_drop=(19, 16), work_cell=(20, 16), conveyor_infeed=(20, 15),
         robot_cells=[(19, 16)])
    zone("conveyor", "Conveyor", CellType.CONVEYOR, rect(19, 24, 15, 15),
         direction="+x", outfeed="sorter")
    zone("pack_cell_1", "Pack 1", CellType.STATION, rect(22, 24, 12, 14),
         fenced=True, no_fly=True, arm_cell=(23, 14), conveyor_cell=(23, 15))
    zone("pack_cell_2", "Pack 2", CellType.STATION, rect(22, 24, 16, 18),
         fenced=True, no_fly=True, arm_cell=(22, 16), conveyor_cell=(22, 15))
    zone("sorter", "Sorter", CellType.SORTER, rect(25, 27, 12, 18), chutes=["dock_4", "dock_5"])
    zone("dock_4", "Dock 4", CellType.DOCK, rect(28, 30, 12, 14),
         dock_role="OUTBOUND_CARTONS", clearance=NARROW, no_fly=True, doors=doors["dock_4"])
    zone("dock_5", "Dock 5", CellType.DOCK, rect(28, 30, 16, 18),
         dock_role="OUTBOUND_CARTONS", clearance=NARROW, no_fly=True, doors=doors["dock_5"])
    corners = [(7, 1), (18, 1), (18, 11), (7, 11)]
    zone("patrol_loop", "Security patrol", CellType.EMPTY, _loop(corners), route=corners)

    layout.crossings = dict(CROSSINGS)
    for key in ("returns_qc", "pick_station_1", "pick_station_2"):
        layout.walkable_extras.update(layout.zones[key].attributes["robot_cells"])
    layout.walkable_extras.update(CROSSINGS)
    for key in ("pack_cell_1", "pack_cell_2"):
        layout.fixed_stations[layout.zones[key].attributes["arm_cell"]] = key

    for rack_y, face_y in PALLET_RACK_FACES.items():
        for x in RACK_COLUMNS:
            for level in range(PALLET_LEVELS):
                layout._add_slot(Slot(f"PR-{x:02d}-{rack_y:02d}-{level}", "PALLET", (x, rack_y), level,
                                      round(level * PALLET_LEVEL_SPACING_M, 2), ((x, face_y),)))
    for shelf_y in TOTE_SHELF_ROWS:
        faces_y = [y for y in (shelf_y - 1, shelf_y + 1) if y in TOTE_AISLE_ROWS]
        for x in RACK_COLUMNS:
            for level in range(TOTE_LEVELS):
                layout._add_slot(Slot(f"TS-{x:02d}-{shelf_y:02d}-{level}", "TOTE", (x, shelf_y), level,
                                      round(level * TOTE_LEVEL_SPACING_M, 2),
                                      tuple((x, y) for y in faces_y)))

    layout.required_zones = REQUIRED_ZONES
    return layout
