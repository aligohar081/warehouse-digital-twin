"""Mobility profiles from the catalog, per-profile passability and routing
(multi-embodiment spec §5.1–§5.2)."""
import json

import pytest

from backend.embodiment import (
    AIR, GROUND, MobilityProfile, lift_ticks, seconds_to_ticks, step_ticks,
)
from backend.inventory.catalog_data import ROBOT_MODELS
from backend.models import CellType, manhattan
from backend.navigation import NavigationEngine
from backend.warehouse import Warehouse

MODELS = {model["model_code"]: model for model in ROBOT_MODELS}


def profile(model_code: str) -> MobilityProfile:
    model = MODELS[model_code]
    return MobilityProfile.from_model(model["spec"], model["embodiment_class"])


FORKLIFT, AMR, DRONE, ARM, HUMANOID, HAULER = (
    profile("NW-PF1200"), profile("AC-TR50"), profile("CT-IX2"),
    profile("FB-CX10"), profile("TS-H1"), profile("NW-HH300"),
)


@pytest.fixture(scope="module")
def dc():
    return Warehouse(layout="distribution_center")


@pytest.fixture(scope="module")
def nav(dc):
    return NavigationEngine(dc)


# --------------------------------------------------------------------------- #
# Profiles
# --------------------------------------------------------------------------- #
def test_profiles_come_from_the_catalog_spec():
    assert (FORKLIFT.movement, FORKLIFT.clearance) == ("GROUND", "WIDE")
    assert FORKLIFT.speed_cells_s == pytest.approx(0.8)          # 1.2 m/s over 1.5 m cells
    assert FORKLIFT.slows_when_loaded and FORKLIFT.loaded_speed_cells_s == pytest.approx(0.56)
    assert HAULER.slows_when_loaded and not AMR.slows_when_loaded
    assert (FORKLIFT.max_payload_kg, FORKLIFT.max_shelf_level, FORKLIFT.box_kinds) == (1200.0, 4, ("PALLET",))
    assert (FORKLIFT.lift_speed_mps, FORKLIFT.grasp_s, FORKLIFT.place_s) == (0.3, 3.0, 3.0)
    assert FORKLIFT.battery.capacity_wh == 14400.0 and FORKLIFT.battery.charge_time_h == 8.0
    assert (AMR.clearance, AMR.speed_cells_s, AMR.max_shelf_level) == ("NARROW", 1.0, 1)
    assert (DRONE.movement, DRONE.clearance, DRONE.flight_time_min) == ("AIR", None, 22.0)
    assert (DRONE.takeoff_s, DRONE.land_s, DRONE.scan_s) == (4.0, 5.0, 3.0)
    assert (ARM.movement, ARM.battery, ARM.reach_mm, ARM.max_shelf_level) == ("FIXED", None, 1300.0, 0)
    assert HUMANOID.supervision == "humanoid_supervision" and HUMANOID.max_shelf_level == 2
    assert ARM.is_fixed and DRONE.is_air and AMR.is_ground
    json.dumps(FORKLIFT.to_dict())  # plain data, ready for a snapshot


def test_profile_defaults_and_validation():
    bare = MobilityProfile.from_model({"max_speed_mps": 1.5}, "amr")
    assert (bare.movement, bare.clearance, bare.embodiment_class) == ("GROUND", "NARROW", "AMR")
    assert bare.lift_speed_mps == 0.5 and bare.grasp_s is None and bare.battery is None
    with pytest.raises(ValueError):
        MobilityProfile.from_model({"movement": "SWIM"}, "AMR")


def test_physical_durations_become_whole_ticks():
    assert seconds_to_ticks(3.0) == 20            # not 21: 3.0 / 0.15 is 20.000000000000004
    assert seconds_to_ticks(2.0) == 14 and seconds_to_ticks(0) == 0
    assert lift_ticks(FORKLIFT, 0.0, 4.0) == 89   # 4 m at 0.3 m/s = 13.3 s
    assert lift_ticks(FORKLIFT, 4.0, 0.0) == 89   # lowering takes as long
    assert step_ticks(DRONE, "TAKEOFF") == 27 and step_ticks(DRONE, "LAND") == 34
    assert step_ticks(AMR, "GRASP") == 14 and step_ticks(AMR, "PLACE_ON_CONVEYOR") == 10
    with pytest.raises(ValueError, match="no TAKEOFF timing"):
        step_ticks(FORKLIFT, "TAKEOFF")
    with pytest.raises(ValueError):
        step_ticks(AMR, "DANCE")


# --------------------------------------------------------------------------- #
# Passability (spec §5.2)
# --------------------------------------------------------------------------- #
def test_a_wide_ground_robot_uses_only_wide_cells(dc):
    assert dc.passable((10, 3), FORKLIFT) and dc.passable((2, 3), FORKLIFT)     # aisle, dock 1
    assert dc.passable((17, 10), FORKLIFT) and dc.passable((17, 11), FORKLIFT)  # the wide crossings
    for cell in [(10, 13), (17, 13), (17, 1), (19, 14), (29, 13), (1, 1), (20, 7)]:
        assert not dc.passable(cell, FORKLIFT), cell
    assert all(dc.clearance(c) == "WIDE" for c in dc.passable_cells(FORKLIFT))


def test_a_narrow_ground_robot_uses_every_walkable_cell(dc):
    for cell in [(10, 13), (17, 13), (17, 1), (19, 14), (20, 14), (20, 7), (10, 3), (29, 13)]:
        assert dc.passable(cell, AMR), cell
    for cell in [(17, 5), (23, 13), (22, 16), (20, 2), (28, 7), (10, 2), (20, 15), (20, 16)]:
        assert not dc.passable(cell, AMR), cell
    assert not dc.passable((10, 3), AMR, AIR)
    assert dc.passable_cells(AMR) == dc.walkable_cells()


def test_a_drone_flies_over_racks_but_never_into_no_fly_zones(dc):
    for cell in [(10, 2), (12, 16), (17, 5), (20, 2), (10, 3)]:
        assert dc.passable(cell, DRONE, AIR), cell
    for cell in [(2, 3), (29, 2), (23, 13), (22, 17), (28, 7), (0, 3), (15, 0)]:
        assert not dc.passable(cell, DRONE, AIR), cell
    on_the_ground = dc.passable_cells(DRONE, GROUND)
    assert on_the_ground and all(dc.cell_type(*c).value == "DRONE_PAD" for c in on_the_ground)


def test_an_arm_never_moves_and_no_profile_means_is_walkable(dc):
    assert dc.passable_cells(ARM) == [] and dc.neighbors((23, 14), ARM) == []
    classic = Warehouse()
    for x in range(classic.width):
        for y in range(classic.height):
            assert classic.passable((x, y)) == classic.is_walkable(x, y)
    assert dc.nearest_walkable((10, 3), profile=DRONE) in dc.zones["drone_pad"].cells


# --------------------------------------------------------------------------- #
# Routing (spec §5.2)
# --------------------------------------------------------------------------- #
def test_a_forklift_never_plans_through_a_narrow_cell(dc, nav):
    path = nav.find_path((10, 3), (24, 2), allow_goal_adjacent=False, profile=FORKLIFT)
    assert path and path[-1] == (24, 2)
    assert all(dc.clearance(cell) == "WIDE" for cell in path)
    assert {(17, 10), (17, 11)} & set(path)  # the only way east for a wide robot
    assert nav.find_path((10, 3), (10, 13), allow_goal_adjacent=False, profile=FORKLIFT) is None
    snapped = nav.find_path((10, 3), (10, 13), profile=FORKLIFT)  # goal-adjacent snapping, as ever
    assert snapped[-1] != (10, 13) and dc.clearance(snapped[-1]) == "WIDE"
    assert nav.find_path((10, 3), (10, 13), allow_goal_adjacent=False, profile=AMR)[-1] == (10, 13)
    assert nav.distance((10, 3), (24, 2), profile=FORKLIFT) == len(path)


def test_a_drone_plans_over_racks_but_not_through_no_fly_cells(dc, nav):
    path = nav.find_path((20, 2), (12, 2), allow_goal_adjacent=False, profile=DRONE, layer=AIR)
    assert path and path[-1] == (12, 2)
    assert not any(dc.is_no_fly(cell) for cell in path)
    assert any(not dc.is_walkable(*cell) for cell in path)  # it really flew over something
    assert nav.find_path((20, 2), (2, 3), allow_goal_adjacent=False, profile=DRONE, layer=AIR) is None
    assert nav.find_path((20, 2), (12, 2), allow_goal_adjacent=False, profile=DRONE, layer=GROUND) is None


def test_an_arm_never_plans_a_move(nav):
    assert nav.find_path((23, 14), (23, 13), allow_goal_adjacent=False, profile=ARM) is None


def test_best_cell_in_zone_respects_the_profile(dc, nav):
    cell = nav.best_cell_in_zone(dc.zones["cross_aisle"].cells, (10, 3), profile=FORKLIFT)
    assert dc.clearance(cell) == "WIDE"
    assert nav.best_cell_in_zone(dc.zones["tote_aisle_1"].cells, (10, 3), profile=FORKLIFT) is None
    assert nav.best_cell_in_zone(dc.zones["tote_aisle_1"].cells, (10, 3), profile=AMR) is not None


def _bfs_shortest_path_length(warehouse: Warehouse, start, goal) -> int:
    """Independent BFS oracle using only is_inside and is_walkable.
    Returns the shortest path length (number of cells after start), or -1 if unreachable.
    """
    from collections import deque
    if not warehouse.is_walkable(*start):
        return -1
    if start == goal and warehouse.is_walkable(*goal):
        return 0
    seen = {start}
    queue = deque([(start, 0)])
    while queue:
        (x, y), dist = queue.popleft()
        for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if (nx, ny) in seen or not warehouse.is_inside(nx, ny):
                continue
            if warehouse.is_walkable(nx, ny):
                if (nx, ny) == goal:
                    return dist + 1
                seen.add((nx, ny))
                queue.append(((nx, ny), dist + 1))
    return -1


def test_classic_routing_is_unchanged_without_a_profile():
    """Verify classic routing against an independent BFS oracle built from is_walkable."""
    classic = Warehouse()
    nav = NavigationEngine(classic)

    # Test cases: at least 3 pairs, including one routing around shelving
    test_cases = [
        ((3, 8), (16, 8), "straight path"),      # straight aisle
        ((3, 7), (16, 9), "route around shelf"),  # must navigate around shelving
        ((5, 2), (15, 12), "across warehouse"),   # long path
    ]

    for start, goal, description in test_cases:
        oracle_length = _bfs_shortest_path_length(classic, start, goal)
        assert oracle_length >= 0, f"Oracle: no path for {description}"

        path = nav.find_path(start, goal, allow_goal_adjacent=False, profile=None, layer=GROUND)
        assert path is not None, f"find_path failed for {description}"
        assert path[-1] == goal, f"Path doesn't reach goal for {description}"
        assert len(path) == oracle_length, f"Path length {len(path)} != oracle {oracle_length} for {description}"

        # Check all cells after start are walkable
        for cell in path:
            assert classic.is_walkable(*cell), f"Cell {cell} not walkable in {description}"

        # Check consecutive cells are 4-adjacent
        for i in range(len(path) - 1):
            x1, y1 = path[i]
            x2, y2 = path[i + 1]
            assert abs(x2 - x1) + abs(y2 - y1) == 1, f"Non-adjacent cells {path[i]}, {path[i+1]} in {description}"

    # Verify neighbors() matches plain 4-connected check for several cells
    test_cells = [
        (8, 7),   # interior
        (3, 1),   # near edge
        (9, 6),   # next to shelf
    ]
    for cell in test_cells:
        x, y = cell
        expected = {
            (nx, ny)
            for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1))
            if classic.is_inside(nx, ny) and classic.is_walkable(nx, ny)
        }
        actual = set(classic.neighbors(cell))
        assert actual == expected, f"neighbors({cell}) mismatch: {actual} != {expected}"


# --------------------------------------------------------------------------- #
# The stop rule: the walkway is crossed, never stopped on (spec §5.2)
# --------------------------------------------------------------------------- #
CROSSINGS = [(17, 1), (17, 10), (17, 11), (17, 13), (17, 15), (17, 17)]


def test_no_robot_may_stop_on_a_walkway_cell_but_may_pass_over_it(dc):
    walkway = dc.zones["walkway"].cells
    assert set(CROSSINGS) < set(walkway)
    for cell in CROSSINGS:
        assert dc.passable(cell, AMR) and not dc.may_stop(cell, AMR), cell
        assert dc.passable(cell) and not dc.may_stop(cell), cell  # no profile: narrow-ground semantics
    assert dc.passable((17, 10), FORKLIFT) and not dc.may_stop((17, 10), FORKLIFT)
    for cell in walkway:
        assert dc.passable(cell, DRONE, AIR) and not dc.may_stop(cell, DRONE, AIR), cell
    assert dc.may_stop((16, 10), AMR) and dc.may_stop((18, 5), DRONE, AIR) and dc.may_stop((10, 3), FORKLIFT)
    for profile_, layer in [(AMR, GROUND), (FORKLIFT, GROUND), (DRONE, AIR), (DRONE, GROUND), (None, GROUND)]:
        for cell in dc.passable_cells(profile_, layer):
            assert dc.may_stop(cell, profile_, layer) == (dc.cell_type(*cell) is not CellType.WALKWAY)
        assert not dc.may_stop((2, 2), ARM)  # fixed equipment never stops anywhere it can't stand


def test_a_path_may_cross_the_walkway_but_never_end_on_it(dc, nav):
    over = nav.find_path((16, 10), (18, 10), allow_goal_adjacent=False, profile=AMR)
    assert over == [(17, 10), (18, 10)]
    assert nav.find_path((16, 10), (17, 10), allow_goal_adjacent=False, profile=AMR) is None
    snapped = nav.find_path((16, 10), (17, 10), profile=AMR)
    assert snapped and dc.cell_type(*snapped[-1]) is not CellType.WALKWAY
    assert manhattan(snapped[-1], (17, 10)) == 1
    assert nav.path_exists((16, 10), (17, 10), profile=AMR)  # it snaps, as any goal-adjacent query does
    assert nav.find_path((16, 13), (17, 13), allow_goal_adjacent=False) is None  # no profile: same rule


def test_a_drone_goal_on_the_walkway_snaps_off_it_or_fails(dc, nav):
    snapped = nav.find_path((20, 2), (17, 5), allow_goal_adjacent=True, profile=DRONE, layer=AIR)
    assert snapped and dc.cell_type(*snapped[-1]) is not CellType.WALKWAY
    assert manhattan(snapped[-1], (17, 5)) == 1
    assert nav.find_path((20, 2), (17, 5), allow_goal_adjacent=False, profile=DRONE, layer=AIR) is None
    assert dc.nearest_walkable((17, 5), profile=DRONE, layer=AIR) in {(16, 5), (18, 5), (17, 4), (17, 6)} - set(dc.zones["walkway"].cells)


def test_nearest_walkable_and_best_cell_in_zone_skip_the_walkway(dc, nav):
    assert dc.nearest_walkable((17, 10), profile=AMR) in {(16, 10), (18, 10)}
    assert dc.nearest_walkable((17, 13)) in {(16, 13), (18, 13)}
    assert dc.nearest_walkable((17, 10), profile=FORKLIFT) in {(16, 10), (18, 10)}
    assert dc.nearest_walkable((16, 10), profile=AMR) == (16, 10)
    assert nav.best_cell_in_zone(dc.zones["walkway"].cells, (16, 10), profile=AMR) is None
    assert nav.best_cell_in_zone(dc.zones["walkway"].cells, (17, 10), profile=AMR) is None  # even standing on it
    assert nav.best_cell_in_zone(dc.zones["walkway"].cells, (20, 2), profile=DRONE, layer=AIR) is None
    assert nav.best_cell_in_zone(dc.zones["walkway"].cells, (16, 10)) is None


def test_the_classic_floor_has_no_stop_rule():
    classic = Warehouse()
    classic_nav = NavigationEngine(classic)
    assert not any(classic.cell_type(x, y) is CellType.WALKWAY
                   for x in range(classic.width) for y in range(classic.height))
    drivable = classic.walkable_cells()
    for x in range(classic.width):
        for y in range(classic.height):
            assert classic.may_stop((x, y)) == classic.is_walkable(x, y)
    for goal in [(17, 12), (3, 8), (9, 6), (10, 10), (0, 0), (2, 14)]:
        path = classic_nav.find_path((3, 8), goal, allow_goal_adjacent=False)
        assert (path is not None) == classic.is_walkable(*goal), goal
        snapped = classic_nav.find_path((3, 8), goal)
        assert snapped is not None and classic.is_walkable(*(snapped[-1] if snapped else (3, 8)))
        nearest = classic.nearest_walkable(goal)
        assert classic.is_walkable(*nearest)
        assert manhattan(nearest, goal) == min(manhattan(cell, goal) for cell in drivable)
    for name in ("loading_zone", "charging_station", "shelf_a", "parking_area"):
        zone = classic.zones[name]
        best = classic_nav.best_cell_in_zone(zone.cells, (3, 8))
        usable = [cell for cell in zone.cells if classic.is_walkable(*cell)]
        if usable:
            assert best in usable and manhattan(best, (3, 8)) == min(manhattan(cell, (3, 8)) for cell in usable)
        else:
            assert best is None


# --------------------------------------------------------------------------- #
# Layer names are validated
# --------------------------------------------------------------------------- #
def test_passable_rejects_a_misspelled_layer(dc):
    with pytest.raises(ValueError, match=r"Unknown layer 'air' \(known: GROUND, AIR\)"):
        dc.passable((10, 2), DRONE, "air")
    with pytest.raises(ValueError, match="Unknown layer"):
        dc.passable((10, 3), AMR, "sky")
    with pytest.raises(ValueError, match="Unknown layer"):
        dc.passable((10, 3), None, "ground")
    with pytest.raises(ValueError, match="Unknown layer"):
        dc.may_stop((10, 3), AMR, "ground")
    assert dc.passable((10, 2), DRONE, AIR) and dc.passable((10, 3), AMR, GROUND)
