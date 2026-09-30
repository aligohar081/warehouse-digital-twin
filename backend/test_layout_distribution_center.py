"""The 32x20 distribution-centre layout, checked against spec §3."""
import pytest

from backend.digital_twin import DigitalTwin
from backend.layouts.base import rect
from backend.models import WALKABLE_CELLS, CellType
from backend.warehouse import Warehouse


@pytest.fixture(scope="module")
def dc():
    return Warehouse(layout="distribution_center")


#: key -> (cell count, cell type) for every zone in spec §3.2, in table order.
ZONES = {
    "dock_1": (12, "DOCK"), "dock_2": (12, "DOCK"), "intake_staging": (27, "STAGING"),
    "pallet_aisle_1": (18, "EMPTY"), "pallet_aisle_2": (18, "EMPTY"), "pallet_racks": (36, "PALLET_RACK"),
    "tote_aisle_1": (9, "EMPTY"), "tote_aisle_2": (9, "EMPTY"), "tote_aisle_3": (9, "EMPTY"),
    "tote_shelves": (36, "TOTE_SHELF"), "walkway": (18, "WALKWAY"), "main_aisle": (18, "EMPTY"),
    "cross_aisle": (52, "EMPTY"), "top_aisle": (25, "EMPTY"), "pallet_lane": (21, "EMPTY"),
    "ne_floor": (19, "EMPTY"), "charging_station": (9, "CHARGING"), "parking_area": (9, "PARKING"),
    "workshop": (18, "WORKSHOP"), "sw_floor": (6, "EMPTY"), "drone_pad": (9, "DRONE_PAD"),
    "outbound_staging": (24, "STAGING"), "dock_3": (12, "DOCK"), "returns_qc": (15, "STATION"),
    "restricted_area": (12, "RESTRICTED"), "pick_station_1": (9, "STATION"), "pick_station_2": (9, "STATION"),
    "conveyor": (6, "CONVEYOR"), "pack_cell_1": (9, "STATION"), "pack_cell_2": (9, "STATION"),
    "sorter": (21, "SORTER"), "dock_4": (9, "DOCK"), "dock_5": (9, "DOCK"), "patrol_loop": (42, "EMPTY"),
}


def test_the_floor_is_32_by_20_with_dock_doors_in_the_perimeter(dc):
    assert (dc.layout_name, dc.width, dc.height) == ("distribution_center", 32, 20)
    doors = set(rect(0, 0, 2, 5) + rect(0, 0, 7, 10) + rect(31, 31, 1, 4)
                + rect(31, 31, 12, 14) + rect(31, 31, 16, 18))
    perimeter = {(x, y) for x in range(32) for y in (0, 19)} | {(x, y) for x in (0, 31) for y in range(20)}
    for cell in perimeter:
        expected = CellType.DOCK_DOOR if cell in doors else CellType.WALL
        assert dc.cell_type(*cell) is expected, cell
        assert not dc.is_walkable(*cell)
    with pytest.raises(ValueError):
        Warehouse(width=20, height=15, layout="distribution_center")


def test_every_zone_has_its_cells_and_type(dc):
    assert list(dc.zones) == list(ZONES)
    for key, (count, cell_type) in ZONES.items():
        zone = dc.zones[key]
        assert (len(zone.cells), zone.cell_type.value) == (count, cell_type), key
        if cell_type != "EMPTY":
            assert all(dc.cell_type(*cell).value == cell_type for cell in zone.cells), key
    assert dc.resolve_zone("Dock 1").key == "dock_1"
    assert dc.resolve_zone("pick 2").key == "pick_station_2"


def test_zone_attributes(dc):
    attrs = {key: zone.attributes for key, zone in dc.zones.items()}
    for key in ("dock_1", "dock_2", "dock_3", "dock_4", "dock_5", "pack_cell_1", "pack_cell_2", "restricted_area"):
        assert attrs[key]["no_fly"] is True, key
    assert attrs["dock_1"]["dock_role"] == "INBOUND" and attrs["dock_3"]["dock_role"] == "OUTBOUND_PALLETS"
    assert attrs["dock_4"]["dock_role"] == "OUTBOUND_CARTONS"
    assert attrs["dock_1"]["doors"] == rect(0, 0, 2, 5)
    assert attrs["pallet_aisle_1"]["serves_rack_rows"] == [2, 5]
    assert attrs["pallet_racks"]["levels"] == 5 and attrs["pallet_racks"]["level_spacing_m"] == 1.0
    assert attrs["tote_shelves"]["levels"] == 3 and attrs["tote_shelves"]["level_spacing_m"] == 0.6
    assert attrs["restricted_area"]["robots_allowed"] is False
    assert attrs["restricted_area"]["people_certification"] == "electrical_safety"
    assert attrs["pick_station_1"]["tote_drop"] == (19, 14) and attrs["pick_station_1"]["picker"] == "ROBOT"
    assert attrs["pick_station_2"]["work_cell"] == (20, 16) and attrs["pick_station_2"]["picker"] == "HUMAN"
    assert attrs["pack_cell_1"]["arm_cell"] == (23, 14) and attrs["pack_cell_1"]["conveyor_cell"] == (23, 15)
    assert attrs["pack_cell_2"]["arm_cell"] == (22, 16) and attrs["pack_cell_2"]["conveyor_cell"] == (22, 15)
    assert attrs["sorter"]["chutes"] == ["dock_4", "dock_5"]
    assert attrs["returns_qc"]["home_of"] == "HUMANOID"
    assert attrs["patrol_loop"]["route"] == [(7, 1), (18, 1), (18, 11), (7, 11)]
    assert dc.zones["patrol_loop"].cells[:2] == [(7, 1), (8, 1)]
    assert dc.zones["dock_1"].to_dict()["attributes"]["doors"][0] == {"x": 0, "y": 2}
    assert dc.required_zones == ("charging_station", "parking_area", "drone_pad", "pick_station_1",
                                 "pack_cell_1", "sorter", "dock_1", "dock_4")
    assert dc.fixed_stations == {(23, 14): "pack_cell_1", (22, 16): "pack_cell_2"}


def test_crossings_and_the_walkway(dc):
    assert dc.crossings == {(17, 1): "NARROW", (17, 10): "WIDE", (17, 11): "WIDE",
                            (17, 13): "NARROW", (17, 15): "NARROW", (17, 17): "NARROW"}
    for y in range(1, 19):
        cell = (17, y)
        assert dc.is_crossing(cell) == (cell in dc.crossings)
        assert dc.is_walkable(*cell) == (cell in dc.crossings), cell
        if cell in dc.crossings:
            assert dc.clearance(cell) == dc.crossings[cell]


def test_clearance_is_per_cell_wide_wins_and_unmarked_cells_are_narrow(dc):
    assert dc.clearance((10, 3)) == "WIDE"      # pallet aisle
    assert dc.clearance((10, 13)) == "NARROW"   # tote aisle
    assert dc.clearance((18, 5)) == "WIDE"      # top aisle (NARROW) and pallet lane (WIDE)
    assert dc.clearance((29, 13)) == "NARROW"   # dock 4
    for cell in [(x, 1) for x in range(1, 7)] + [(x, 6) for x in range(1, 4)]:
        assert dc.zones_of_cell(cell) == [] and dc.clearance(cell) == "NARROW", cell
    assert dc.clearance((10, 2)) is None        # a rack is not walkable


def test_station_cells_walkable_only_where_the_zone_allows(dc):
    for cell in [(19, 14), (20, 14), (19, 16)] + dc.zones["returns_qc"].cells:
        assert dc.is_walkable(*cell), cell
    assert not dc.is_walkable(20, 16)  # Pick 2's work cell is a person's
    for key in ("pack_cell_1", "pack_cell_2", "conveyor", "sorter", "drone_pad", "restricted_area",
                "pallet_racks", "tote_shelves"):
        assert not any(dc.is_walkable(*cell) for cell in dc.zones[key].cells), key


def test_every_walkable_cell_is_connected(dc):
    walkable = dc.walkable_cells()
    seen, stack = {walkable[0]}, [walkable[0]]
    while stack:
        for neighbor in dc.neighbors(stack.pop()):
            if neighbor not in seen:
                seen.add(neighbor)
                stack.append(neighbor)
    assert len(seen) == len(walkable)


def test_zone_of_cell_is_the_most_specific_zone(dc):
    assert [z.key for z in dc.zones_of_cell((10, 11))] == ["cross_aisle", "patrol_loop"]
    assert dc.zone_of_cell((10, 11)).key == "cross_aisle"   # patrol_loop is a route, not a place
    assert dc.zone_of_cell((7, 1)).key == "main_aisle"
    assert dc.zone_of_cell((5, 10)).key == "intake_staging"
    assert dc.cell_type(5, 10) is CellType.STAGING           # overlapping typed zone wins the paint
    assert dc.zone_of_cell((17, 11)).key == "walkway"
    assert dc.zone_of_cell((1, 1)) is None
    assert dc.label_for_cell((1, 1)) == "(1,1)"


def test_no_fly_and_flyable_cells(dc):
    assert dc.is_no_fly((2, 3)) and dc.is_no_fly((23, 13)) and dc.is_no_fly((28, 7))
    assert not dc.is_flyable((2, 3)) and not dc.is_flyable((23, 13))
    assert dc.is_flyable((10, 2)) and dc.is_flyable((12, 16))  # over a rack and a tote shelf
    assert dc.is_flyable((17, 5)) and dc.is_flyable((20, 2))   # the walkway and the pad
    assert not dc.is_flyable((0, 3)) and not dc.is_flyable((15, 0))


def test_slots_and_their_face_cells(dc):
    pallets = [s for s in dc.slots.values() if s.kind == "PALLET"]
    totes = [s for s in dc.slots.values() if s.kind == "TOTE"]
    assert len(pallets) == 180 and len(totes) == 108
    for rack_y, face_y in {2: 3, 5: 4, 6: 7, 9: 8}.items():
        slot = dc.slot_at((12, rack_y), 4)
        assert slot.faces == ((12, face_y),) and slot.height_m == 4.0
    assert dc.slot_at((12, 12), 2).faces == ((12, 13),)
    assert dc.slot_at((12, 14), 0).faces == ((12, 13), (12, 15))
    assert dc.slot_at((12, 16), 1).faces == ((12, 15), (12, 17))
    assert dc.slot_at((12, 18), 1).faces == ((12, 17),)
    assert [s.height_m for s in dc.slots_at((8, 12))] == [0.0, 0.6, 1.2]
    assert dc.slot("PR-08-02-3").cell == (8, 2) and dc.slot("TS-16-18-2").level == 2
    assert dc.slot("nope") is None and dc.slot_at((12, 12), 3) is None
    for slot in dc.slots.values():
        assert all(dc.is_walkable(*face) for face in slot.faces), slot.slot_id


def test_classic_keeps_its_walkable_set():
    assert WALKABLE_CELLS == {CellType.EMPTY, CellType.STORAGE, CellType.CHARGING, CellType.LOADING,
                              CellType.UNLOADING, CellType.PACKING, CellType.PARKING}
    classic = Warehouse()
    assert classic.walkable_types is WALKABLE_CELLS and not classic.walkable_extras
    assert classic.slots == {} and classic.crossings == {} and classic.fixed_stations == {}


def test_a_twin_on_the_new_floor_boots_empty(tmp_path):
    twin = DigitalTwin(log_dir=str(tmp_path / "logs"), data_dir=str(tmp_path / "data"),
                       persist_logs=False, demo=True, demo_tasks=False, layout="distribution_center")
    assert twin.layout_name == "distribution_center"
    assert not twin.robots and not twin.boxes and not twin.operators
    snapshot = twin.snapshot(include_layout=True)
    assert snapshot["layout_name"] == "distribution_center" and snapshot["warehouse"]["width"] == 32
    twin.reset(demo_tasks=False)
    assert twin.layout_name == "distribution_center" and not twin.robots
