# Multi-Embodiment Plan 1a — Floor and Motion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the twin a second, layered floor — the 32×20 distribution centre — with catalog-driven robot bodies, ground and air traffic, people as zone presence, and goods with a stock ledger, while the classic floor and every existing test stay exactly as they are.

**Architecture:** Floor plans become named data in `backend/layouts/` (`classic` is today's `_build_layout`, moved verbatim and pinned by a golden test; `distribution_center` is spec §3). A `MobilityProfile` read from the inventory's catalog model (`backend/embodiment.py`) decides where a robot may be (`Warehouse.passable`) and how it routes (`NavigationEngine` gains optional `profile`/`layer`); with no profile — every classic robot — behaviour is byte-for-byte today's. People (`backend/people.py`) and goods (`backend/goods.py`) are new, self-contained units the job steps of plan 1b will drive.

**Tech Stack:** Python 3.14, sqlite3 (inventory), pytest. Flask is untouched by this plan.

**Spec:** `docs/superpowers/specs/2026-09-30-multi-embodiment-operations-design.md` — the binding authority. This plan covers §17 step 1, items 1–7 (§2, §3, §4.1, §5.1–§5.3, §6, §7, the §13.2 catalog data, §15's classic guard). Plans 1b and 1c cover items 8–17 and are written later, against the code this plan leaves.

## Global Constraints

- Work on branch `feature/multi-embodiment-ops`. Commit at the end of every task (Task 1 and Task 3 commit twice). Never merge, rebase or push.
- Test command, from the repo root: `.venv/bin/python -m pytest -o addopts="" -q` (`pytest.ini`'s `-q` hides the summary unless addopts is overridden). Baseline before Task 1: `2 failed, 367 passed`.
- The only failures ever allowed are the two pre-existing ones: `backend/test_eval_engine.py::test_real_task_006_box_conflict_is_caught` and `backend/tests.py::test_idle_robot_with_a_low_battery_charges_itself`.
- Dependencies: Flask, PyYAML, pytest and the standard library only. No new dependencies.
- Do not edit any existing test file. New tests go in the new files each task names.
- The classic floor must not move: `Warehouse()`, `DigitalTwin()` and every existing fixture stay classic, with an unchanged grid, zones, aliases, inventory seed, battery model, wall-clock shifts and task behaviour. `models.WALKABLE_CELLS` is not edited.
- Out of scope (plans 1b and 1c): new `TaskType`s, planner steps, `eligibility.py` rules, conveyor and sorter, shift engine and orders, evaluation checks, the new-floor twin seed, save/load v2, app boot, the operations API and the dashboard. `backend/app.py` and `frontend/` are not touched.
- Lock order is `twin.lock` → inventory store lock. Inventory listeners run after commit, outside the store lock. Nothing in this plan takes the store lock and then `twin.lock`.
- Spec values used verbatim: 1 cell = `CELL_SIZE_M` 1.5 m; `LOADED_SPEED_FACTOR` 0.7; people walk at 1.2 m/s; ticks = `ceil(seconds / TICK_DT)` with `TICK_DT` 0.15; 180 pallet slots (levels 0–4, 1.0 m apart) and 108 tote slots (levels 0–2, 0.6 m apart); lift speed 0.3 m/s (forklift) and 0.5 m/s (all others); CT-IX2 takeoff/land/scan 4/5/3 s; grasp/place — NW-PF1200 and NW-HH300 3.0/3.0, AC-TR50 2.0/1.5, AC-PK30 and FB-CX10 2.5/1.5, TS-H1 3.0/2.0; known issues — AC-TR50 2.2.1 odometry heading drift 0.5°/m, FB-CX10 force-torque firmware 3.2.0 (part FB-FT6) 8 N z-offset.
- Style: match the surrounding code — `from __future__ import annotations`, `typing` annotations, a module docstring on every new module, comments that say why, plain-language error messages. Bad input raises `ValueError`; an unknown robot or operator id raises `KeyError`.
- Stage only the files your task lists (`git add <paths>`, never `git add -A`): the working tree has unrelated changes (`logs/tasks/*.json`, `.DS_Store`, `docs/handoffs/`) that are not yours.
- Every commit message ends with exactly this line: `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`
- Edits are given as exact **Replace in** blocks: the first block must match the file exactly once (use the Edit tool with that text as `old_string`); **Replace every** blocks name how many occurrences they replace (`replace_all`). **Create** and **Overwrite** blocks give a file's whole content.

## Plan rulings

Choices this plan makes where the spec is ambiguous or silent. Each can be changed at plan review.

1. **Floor.** Walkability without a profile is per layout: classic is exactly `WALKABLE_CELLS`; on the new floor it means "a narrow ground robot" — the ground cell types (EMPTY, DOCK, STAGING, CHARGING, PARKING, WORKSHOP), the six crossings, and the station cells robots may enter. Those station cells are both tote drops, all of `returns_qc`, **and Pick 1's work cell (20,14)**, where the robot picker stands (§5.2 lists only tote drops and returns_qc). Where zones overlap, an aisle-type (EMPTY) zone only names cells and a typed zone paints them, so (4–6,10) are STAGING. The warehouse names its layout in `Warehouse.layout_name` (§2's "`layout` (name)"), like `DigitalTwin.layout_name`. AST-000213 has no home zone on the new floor (§4.1's "—").
2. **Seed.** The catalog is shared by both seed profiles, so the new catalog fields appear under `classic` too. "Classic byte for byte" is therefore pinned on the fleet and workforce tables (a golden fingerprint taken before any seed change), not the catalog tables. `known_issues` becomes a JSON column on `software_release`, which bumps `SCHEMA_VERSION` from 2 to 3; both profiles reseed an old file with a `.bak`, like A's version mismatch.
3. **Robots.** A robot's body is always read from its catalog model, but `robot.mobility` is set only on a non-classic floor, so classic routing and speeds cannot change. An ARM is fixed equipment and spawns only on a free `fixed_stations` cell; classic has none, so `add_robot(model_code="FB-CX10")` on classic still raises `ValueError`, as `test_fleet_bridge.py` requires. `DigitalTwin(layout="distribution_center")` opens its inventory under the same-named seed profile and boots with no twin entities until plan 1c's seed.
4. **People.** Every zone-to-zone walk puts the person "in transit on the walkway": they are in neither zone until they arrive, and any robot about to step onto a walkway cell from off it (a ground robot at a crossing, a drone crossing over) waits with `PERSON_ON_CROSSING`. Clocking on or off the floor (`people.place`) is instant, and an `OFF_DUTY` operator always has `zone = None`.

**Task count.** Spec item 4 is split into Task 4 (the profile, passability and the A\* engine, all testable without a twin) and Task 5 (robots getting and using their profile across the twin, simulator, planner and task manager), because a reviewer can approve the pure routing rules while rejecting the integration, and the integration is where classic regressions would hide. That gives eight tasks.

## File map

| File | Task | Responsibility |
|---|---|---|
| `backend/test_layout_classic_golden.py` | 1 (create) | Classic grid, zones and aliases pinned cell by cell |
| `backend/layouts/__init__.py` | 1 (create), 2 | Registry: `LAYOUTS`, `build_layout(name, width, height)` |
| `backend/layouts/base.py` | 1 (create), 2 | `Zone`, `Slot`, `rect`, the `Layout` canvas |
| `backend/layouts/classic.py` | 1 (create) | `Warehouse._build_layout`, moved verbatim |
| `backend/layouts/distribution_center.py` | 2 (create) | The 32×20 floor of spec §3 |
| `backend/warehouse.py` | 1, 2, 4 | Adopts a layout; per-cell zones, clearance, no-fly, slots; `passable` |
| `backend/test_layouts.py` | 1 (create) | Registry and the twin's layout choice |
| `backend/test_layout_distribution_center.py` | 2 (create) | The new floor against spec §3 |
| `backend/models.py` | 2, 4, 5, 7, 8 | CellTypes, CONFIG keys, presets, EventTypes, LogCategories, `BoxKind` |
| `backend/inventory/catalog_data.py` | 3 | Model timings, `KNOWN_ISSUES` |
| `backend/inventory/schema.py` | 3 | `known_issues` column, `SCHEMA_VERSION` 3 |
| `backend/inventory/catalog.py` | 3 | `make_release_row(..., known_issues)` |
| `backend/inventory/seed.py` | 3 | Seeds known issues onto release rows |
| `backend/inventory/demo_seed.py` | 3 | `classic` and `distribution_center` seed profiles |
| `backend/inventory/bootstrap.py` | 3 | `profile=` on `open_inventory`/`reseed`; per-profile versions and templates |
| `backend/test_inventory_seed_golden.py` | 3 (create) | Classic seed fingerprint |
| `backend/test_inventory_seed_profiles.py` | 3 (create) | Profiles and catalog data |
| `backend/embodiment.py` | 4 (create), 6 | `MobilityProfile`, tick helpers |
| `backend/navigation.py` | 4 | `profile`/`layer` on every query |
| `backend/test_embodiment.py` | 4 (create) | Profiles, passability, routing |
| `backend/robot.py` | 5, 6, 7 | `mobility`, `layer`, `altitude_m`, `wait_reason` |
| `backend/fleet_bridge.py` | 5, 7 | Profile from the bound model; `certification_scopes` |
| `backend/digital_twin.py` | 1, 2, 3, 5, 6 | `layout`, spawn by body, layer-aware occupancy, `set_robot_layer` |
| `backend/simulator.py` | 5, 6, 7 | Per-profile routing, layer-aware traffic, the crossing wait |
| `backend/task_planner.py`, `backend/task_manager.py` | 5 | Targets resolved for the robot's own body |
| `backend/test_robot_mobility.py` | 5 (create) | Robots move by their body |
| `backend/ci_engine.py` | 6 | `_check_collisions` compares `(layer, x, y)` |
| `backend/test_layers.py` | 6 (create) | Layers and traffic |
| `backend/operator.py` | 7 | Zone, transit, `certification_scopes` |
| `backend/people.py` | 7 (create) | Zone presence, walks, proximity |
| `backend/test_people.py` | 7 (create) | People and the crossing wait |
| `backend/box.py` | 8 | Kinds, SKU, quantity, declared and true weights, slot, order |
| `backend/goods.py` | 8 (create) | `StockLedger`, `carton_weight_kg` |
| `backend/test_goods.py` | 8 (create) | Boxes and the ledger |

## Carried forward

### Carried forward to plan 1b

Job steps, selection, eligibility, conveyor, shift and evaluation build on these:
- `NavigationEngine.path_exists` snaps an unreachable goal to its nearest passable neighbour (unchanged behaviour). §9.1's "a route under its profile" capability check must call `find_path(..., allow_goal_adjacent=False, profile=..., layer=...)`, or a forklift will "reach" a tote aisle by stopping beside it.
- `Simulator._safety_wait` / `_safety_resume` are the shared wait machinery; 1b adds `PERSON_IN_AISLE`, `PERSON_IN_CELL`, `SUPERVISOR_ABSENT`, the `SAFETY_WAIT_ESCALATE_S` escalation and `ROBOT_STEP`. `people.people_at`, `people.people_in`, `people.supervisor_nearby` and `Operator.certification_scopes` are the inputs those rules need.
- `zone_of_cell` follows §3.2's "smallest zone" rule, so 11 cross-aisle cells that `patrol_loop` (42 cells) overlaps report `patrol_loop` (cross_aisle has 52) — in heartbeats too. The people rules should test `zones_of_cell`, as `people.people_at` already does.
- CI's `_check_environment`, `_check_robot_positions`, `_check_navigation` and `_check_battery` still assume classic (§10.5 makes them layout-driven). `Warehouse.required_zones` is already set for both floors.
- `TaskManager._score_candidates` still resolves the target from the first robot's position (§9.1 changes that), and CHARGE_ROBOT still targets `charging_station` for drones (§5.5 sends them to the pad).
- `backend/embodiment.step_ticks` / `lift_ticks` and `HOVER_CLEARANCE_M` are ready for LIFT_TO, TAKEOFF, LAND, SCAN, GRASP and PLACE; `DigitalTwin.set_robot_layer` is what TAKEOFF and LAND call.

### Carried forward to plan 1c

New-floor seed, save/load v2 and app boot must deal with these:
- Known pre-existing issue: the commission fallback in `FleetBridge.bind_new_robot` can raise `inventory.Conflict`, not `ValueError`, when a robot's firmware maps to a RECALLED release (AC-TR50 2.2.1). The new-floor seed binds AST-000202, which reports 2.2.1, so 1c must handle it.
- §4.4: `FleetBridge.heartbeat` should stop calling `touch_remote_reports` under the `distribution_center` profile (read it from the inventory meta key `seed_profile`).
- `DigitalTwin` does not yet own a `StockLedger`, `add_box` has no `slot`/`kind` arguments, and `serialize()` is still version 1; `Robot.mobility` is rebuilt on load by `FleetBridge.rebind_all`, but layer, altitude, zones and transit are saved through the entity dicts only.
- `snapshot()["config"]` still reports `GRID_WIDTH`/`GRID_HEIGHT` 20×15 on the new floor; `Warehouse.to_dict()` does not yet expose slots, crossings or clearance for the dashboard.

---

### Task 1: Classic golden test and the layout registry

Pin today's classic floor cell by cell, then move `Warehouse._build_layout` verbatim into `backend/layouts/classic.py` behind a registry, and let the warehouse and the twin name their layout (spec §2 "Construction", §15 "Classic layout guard", §17 step 1 item 1).

**Files:**
- Create: `backend/test_layout_classic_golden.py`
- Create: `backend/layouts/__init__.py`
- Create: `backend/layouts/base.py`
- Create: `backend/layouts/classic.py`
- Overwrite: `backend/warehouse.py` (the layout code leaves; the query methods are unchanged except `is_walkable` and `to_dict`, which now read `self.walkable_types`)
- Modify: `backend/digital_twin.py` (constructor signature, warehouse construction, `snapshot`)
- Test: `backend/test_layouts.py` (create)

**Interfaces:**
- Consumes: nothing new. `models.CONFIG["GRID_WIDTH"]`/`["GRID_HEIGHT"]` (20, 15), `models.CellType`, `models.WALKABLE_CELLS`.
- Produces:
  - `backend.layouts.base.Zone(key: str, label: str, cell_type: CellType, cells: Sequence[Cell])` — moved unchanged from `warehouse.py`; `from backend.warehouse import Zone` keeps working.
  - `backend.layouts.base.rect(x0: int, x1: int, y0: int, y1: int) -> List[Cell]` (inclusive, row by row).
  - `backend.layouts.base.Layout(name: str, width: int, height: int)` — the canvas: `.name`, `.width`, `.height`, `.grid: List[List[CellType]]`, `.zones: Dict[str, Zone]`, `.aliases: Dict[str, str]`, `.walkable_types: Set[CellType]` (defaults to the `WALKABLE_CELLS` object itself), `.required_zones: Tuple[str, ...]`, `.is_inside(x, y) -> bool`, `._fill(cells, cell_type)`, `._add_zone(key, label, cell_type, cells) -> Zone`.
  - `backend.layouts.LAYOUTS: Dict[str, Callable[[Optional[int], Optional[int]], Layout]]` and `build_layout(name: str = "classic", width: Optional[int] = None, height: Optional[int] = None) -> Layout`; an unknown name raises `ValueError("Unknown layout 'x' (known: [...])")`.
  - `backend.layouts.classic.NAME == "classic"`, `classic.build(width=None, height=None) -> Layout`, with `required_zones == ("charging_station", "loading_zone", "packing_area", "shelf_a")`.
  - `Warehouse(width: Optional[int] = None, height: Optional[int] = None, layout: str = "classic")` with `.layout_name: str`, `.walkable_types`, `.required_zones`. `_fill`, `_add_zone` and `_build_layout` no longer exist on `Warehouse`.
  - `DigitalTwin(..., inventory_path=":memory:", layout: str = "classic")`, `DigitalTwin.layout_name: str`, and `snapshot()["layout_name"]`.

- [ ] **Step 1: Write the classic golden test — before moving any code**

The grid rows, zone cells and aliases below were read from today's `Warehouse()`. This test must pass on the unchanged code.

**Create** `backend/test_layout_classic_golden.py`:

```python
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
```

- [ ] **Step 2: Run it against the unchanged code**

Run: `.venv/bin/python -m pytest -o addopts="" -q backend/test_layout_classic_golden.py`
Expected: `3 passed`. If anything fails, stop: the pin is wrong, not the code.

- [ ] **Step 3: Commit the pin on its own**

```bash
git add backend/test_layout_classic_golden.py
git commit -m "test: pin the classic floor cell by cell

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 4: Write the failing registry tests**

**Create** `backend/test_layouts.py`:

```python
"""The layout registry (backend/layouts) and the twin's layout choice."""
import pytest

from backend.digital_twin import DigitalTwin
from backend.layouts import LAYOUTS, build_layout
from backend.warehouse import Warehouse


def make_twin(tmp_path, **kwargs):
    return DigitalTwin(log_dir=str(tmp_path / "logs"), data_dir=str(tmp_path / "data"),
                       persist_logs=False, demo_tasks=False, **kwargs)


def test_the_registry_builds_classic_by_default():
    assert "classic" in LAYOUTS
    layout = build_layout()
    assert layout.name == "classic"
    assert (layout.width, layout.height) == (20, 15)
    assert build_layout("classic", 24, 18).width == 24  # a custom grid size still works


def test_an_unknown_layout_is_rejected():
    with pytest.raises(ValueError, match="Unknown layout 'moon_base'"):
        build_layout("moon_base")
    with pytest.raises(ValueError):
        Warehouse(layout="moon_base")


def test_warehouse_defaults_to_classic():
    default, explicit = Warehouse(), Warehouse(layout="classic")
    assert default.layout_name == explicit.layout_name == "classic"
    assert default.to_dict() == explicit.to_dict()
    assert default.required_zones == ("charging_station", "loading_zone", "packing_area", "shelf_a")


def test_the_twin_defaults_to_classic_and_reports_its_layout(tmp_path):
    twin = make_twin(tmp_path, demo=False)
    assert twin.layout_name == "classic" == twin.warehouse.layout_name
    assert twin.snapshot()["layout_name"] == "classic"
    with pytest.raises(ValueError):
        make_twin(tmp_path, demo=False, layout="moon_base")


def test_reset_keeps_the_layout(tmp_path):
    twin = make_twin(tmp_path, demo=True)
    warehouse = twin.warehouse
    twin.reset(demo_tasks=False)
    assert twin.warehouse is warehouse and twin.layout_name == "classic"
    assert len(twin.robots) == 2
```

- [ ] **Step 5: Run them to see them fail**

Run: `.venv/bin/python -m pytest -o addopts="" -q backend/test_layouts.py`
Expected: a collection error, `ModuleNotFoundError: No module named 'backend.layouts'`.

- [ ] **Step 6: Create the layout building blocks**

**Create** `backend/layouts/base.py`:

```python
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
```

- [ ] **Step 7: Move the classic layout, verbatim**

The body of `build` is today's `Warehouse._build_layout` with `self` renamed to `layout` and nothing else changed; the only addition is the `required_zones` line at the end (the four zones `ci_engine._check_environment` hard-codes today).

**Create** `backend/layouts/classic.py`:

```python
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
```

- [ ] **Step 8: Create the registry**

**Create** `backend/layouts/__init__.py`:

```python
"""Named floor layouts. Each layout module draws a Layout; Warehouse(layout=
name) adopts it. `classic` is today's floor and the default everywhere."""
from __future__ import annotations

from typing import Callable, Dict, Optional

from . import classic
from .base import Layout, Zone, rect

LayoutBuilder = Callable[[Optional[int], Optional[int]], Layout]

LAYOUTS: Dict[str, LayoutBuilder] = {
    classic.NAME: classic.build,
}


def build_layout(name: str = "classic", width: Optional[int] = None, height: Optional[int] = None) -> Layout:
    """Draw the layout called `name` (optionally at a custom grid size)."""
    builder = LAYOUTS.get(name)
    if builder is None:
        raise ValueError(f"Unknown layout {name!r} (known: {sorted(LAYOUTS)})")
    return builder(width, height)


__all__ = ["LAYOUTS", "Layout", "Zone", "build_layout", "rect"]
```

- [ ] **Step 9: Make the warehouse adopt a layout**

**Overwrite** `backend/warehouse.py`:

```python
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
```

- [ ] **Step 10: Let the twin choose its layout**

**Replace in** `backend/digital_twin.py`:

```python
        inventory_path: str = ":memory:",
    ) -> None:
```

with:

```python
        inventory_path: str = ":memory:",
        layout: str = "classic",
    ) -> None:
```

**Replace in** `backend/digital_twin.py`:

```python
        self.warehouse = Warehouse()
        self.navigation = NavigationEngine(self.warehouse)
```

with:

```python
        # The named floor plan (backend/layouts). "classic" — today's floor —
        # is the default, so every existing caller builds the same twin.
        self.warehouse = Warehouse(layout=layout)
        self.layout_name = self.warehouse.layout_name
        self.navigation = NavigationEngine(self.warehouse)
```

**Replace in** `backend/digital_twin.py`:

```python
            state: Dict[str, Any] = {
                "environment": self.environment_state(),
```

with:

```python
            state: Dict[str, Any] = {
                "layout_name": self.layout_name,
                "environment": self.environment_state(),
```

- [ ] **Step 11: Run the new tests and the pin**

Run: `.venv/bin/python -m pytest -o addopts="" -q backend/test_layouts.py backend/test_layout_classic_golden.py`
Expected: `8 passed`.

- [ ] **Step 12: Run the full suite**

Run: `.venv/bin/python -m pytest -o addopts="" -q`
Expected: `2 failed, 375 passed` — the two failures are the pre-existing `test_real_task_006_box_conflict_is_caught` and `test_idle_robot_with_a_low_battery_charges_itself`.

- [ ] **Step 13: Commit**

```bash
git add backend/layouts/__init__.py backend/layouts/base.py backend/layouts/classic.py \
        backend/warehouse.py backend/digital_twin.py backend/test_layouts.py
git commit -m "feat: layout registry with the classic floor moved out of Warehouse

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: The `distribution_center` layout

Build the 32×20 floor exactly as spec §3 describes: the new cell types, every zone with its cells and attributes, per-cell clearance, crossings, dock doors, the 180 pallet and 108 tote slots with their face cells, `required_zones`, and `zone_of_cell` / `zones_of_cell` (§17 step 1 item 2). Classic keeps `WALKABLE_CELLS` and its behaviour.

**Files:**
- Modify: `backend/models.py` (`CellType` gains eleven values)
- Overwrite: `backend/layouts/base.py` (adds zone attributes, `Slot`, clearance constants and the new canvas fields)
- Create: `backend/layouts/distribution_center.py`
- Overwrite: `backend/layouts/__init__.py` (registers the new floor)
- Modify: `backend/warehouse.py` (adopts the new canvas fields; per-cell index; new queries)
- Modify: `backend/digital_twin.py` (the classic demo seed runs only on the classic floor)
- Test: `backend/test_layout_distribution_center.py` (create)

**Interfaces:**
- Consumes (Task 1): `Layout`, `Zone`, `rect`, `build_layout`, `Warehouse(layout=...)`, `DigitalTwin(layout=...)`, `DigitalTwin.layout_name`.
- Produces:
  - `CellType.DOCK, DOCK_DOOR, STAGING, PALLET_RACK, TOTE_SHELF, WALKWAY, STATION, CONVEYOR, SORTER, WORKSHOP, DRONE_PAD` (string values equal their names).
  - `backend.layouts.base.WIDE = "WIDE"`, `NARROW = "NARROW"`.
  - `Zone(key, label, cell_type, cells, attributes: Optional[Dict[str, Any]] = None)`; `.attributes: Dict[str, Any]`; `Zone.cells` drops repeated cells; `Zone.to_dict()` adds an `"attributes"` key (cells as `{"x", "y"}`) only when the zone has attributes, so classic zones serialise exactly as before.
  - `Slot(slot_id: str, kind: str, cell: Cell, level: int, height_m: float, faces: Tuple[Cell, ...])`, frozen, with `to_dict()`. `kind` is `"PALLET"` or `"TOTE"`. Ids: `"PR-{x:02d}-{y:02d}-{level}"` for pallet racks, `"TS-{x:02d}-{y:02d}-{level}"` for tote shelves.
  - `Layout` gains `.walkable_extras: Set[Cell]`, `.crossings: Dict[Cell, str]` (cell → WIDE/NARROW), `.slots: Dict[str, Slot]`, `.fixed_stations: Dict[Cell, str]` (arm cell → pack-cell zone key), `._add_zone(key, label, cell_type, cells, **attributes)`, `._add_slot(slot)`.
  - `backend.layouts.distribution_center`: `NAME = "distribution_center"`, `WIDTH, HEIGHT = 32, 20`, `GROUND_TYPES`, `CROSSINGS`, `REQUIRED_ZONES`, `build(width=None, height=None) -> Layout` (any other size raises `ValueError`).
  - Zone attribute keys (read by plan 1b): `clearance`, `no_fly`, `dock_role` (`INBOUND` / `OUTBOUND_PALLETS` / `OUTBOUND_CARTONS`), `doors`, `staging`, `serves_rack_rows`, `levels`, `level_spacing_m`, `people_only`, `crossings`, `maintenance_bay`, `drones_only`, `robot_cells`, `home_of`, `robots_allowed`, `people_certification`, `picker` (`ROBOT`/`HUMAN`), `tote_drop`, `work_cell`, `conveyor_infeed`, `direction`, `outfeed`, `fenced`, `arm_cell`, `conveyor_cell`, `chutes`, `route`.
  - `Warehouse` gains `.walkable_extras`, `.crossings`, `.slots`, `.fixed_stations`; `is_walkable(x, y)` also accepts `walkable_extras`; `zones_of_cell(cell) -> List[Zone]` (declaration order); `zone_of_cell(cell) -> Optional[Zone]` (smallest zone, ties to the first declared); `clearance(cell) -> Optional[str]` (WIDE/NARROW for walkable cells, else None); `is_crossing(cell)`, `is_no_fly(cell)`, `is_flyable(cell)`; `slot(slot_id) -> Optional[Slot]`, `slots_at(cell) -> List[Slot]` (lowest level first), `slot_at(cell, level) -> Optional[Slot]`. Private `Warehouse._clearance: Dict[Cell, str]` is read by Task 4.
  - `DigitalTwin(layout="distribution_center")` builds with no robots, boxes, agents or operators; `reset()` keeps the layout.

- [ ] **Step 1: Write the failing layout tests**

The zone table is spec §3.2 in its own order; the counts follow from the rectangles (`cross_aisle` is x4–30 × y10–11 minus the two walkway cells = 52; `patrol_loop` is the 42-cell loop).

**Create** `backend/test_layout_distribution_center.py`:

```python
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
    assert dc.zone_of_cell((10, 11)).key == "patrol_loop"   # 42 cells beats 52
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
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/bin/python -m pytest -o addopts="" -q backend/test_layout_distribution_center.py`
Expected: `2 failed, 10 errors` — the module fixture raises `ValueError: Unknown layout 'distribution_center'`.

- [ ] **Step 3: Add the new cell types**

**Replace in** `backend/models.py`:

```python
    PARKING = "PARKING"
    ROBOT = "ROBOT"
    BOX = "BOX"
```

with:

```python
    PARKING = "PARKING"
    ROBOT = "ROBOT"
    BOX = "BOX"
    # The distribution-centre floor (backend/layouts/distribution_center.py).
    # Classic never uses these, and WALKABLE_CELLS below stays classic's set.
    DOCK = "DOCK"
    DOCK_DOOR = "DOCK_DOOR"
    STAGING = "STAGING"
    PALLET_RACK = "PALLET_RACK"
    TOTE_SHELF = "TOTE_SHELF"
    WALKWAY = "WALKWAY"
    STATION = "STATION"
    CONVEYOR = "CONVEYOR"
    SORTER = "SORTER"
    WORKSHOP = "WORKSHOP"
    DRONE_PAD = "DRONE_PAD"
```

- [ ] **Step 4: Extend the building blocks**

**Overwrite** `backend/layouts/base.py`:

```python
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
```

- [ ] **Step 5: Draw the distribution-centre floor**

**Create** `backend/layouts/distribution_center.py`:

```python
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
```

- [ ] **Step 6: Register it**

**Overwrite** `backend/layouts/__init__.py`:

```python
"""Named floor layouts. Each layout module draws a Layout; Warehouse(layout=
name) adopts it. `classic` is today's floor and the default everywhere;
`distribution_center` is the 32x20 multi-embodiment floor."""
from __future__ import annotations

from typing import Callable, Dict, Optional

from . import classic, distribution_center
from .base import NARROW, WIDE, Layout, Slot, Zone, rect

LayoutBuilder = Callable[[Optional[int], Optional[int]], Layout]

LAYOUTS: Dict[str, LayoutBuilder] = {
    classic.NAME: classic.build,
    distribution_center.NAME: distribution_center.build,
}


def build_layout(name: str = "classic", width: Optional[int] = None, height: Optional[int] = None) -> Layout:
    """Draw the layout called `name` (optionally at a custom grid size)."""
    builder = LAYOUTS.get(name)
    if builder is None:
        raise ValueError(f"Unknown layout {name!r} (known: {sorted(LAYOUTS)})")
    return builder(width, height)


__all__ = ["LAYOUTS", "NARROW", "WIDE", "Layout", "Slot", "Zone", "build_layout", "rect"]
```

- [ ] **Step 7: Index the floor in the warehouse**

**Replace in** `backend/warehouse.py`:

```python
from .layouts import build_layout
from .layouts.base import Zone  # noqa: F401  (re-exported; Zone used to live here)
from .models import Cell, CellType, manhattan
```

with:

```python
from .layouts import build_layout
from .layouts.base import NARROW, WIDE, Slot, Zone
from .models import Cell, CellType, manhattan

#: Cell types no robot ever occupies or flies through.
_SOLID = {CellType.WALL, CellType.DOCK_DOOR}
```

**Replace in** `backend/warehouse.py`:

```python
        self.walkable_types: Set[CellType] = plan.walkable_types
        self.required_zones = plan.required_zones
```

with:

```python
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
```

**Replace in** `backend/warehouse.py`:

```python
    def is_walkable(self, x: int, y: int) -> bool:
        return self.cell_type(x, y) in self.walkable_types
```

with:

```python
    def is_walkable(self, x: int, y: int) -> bool:
        """Drivable by a robot with no mobility profile: on classic, exactly
        WALKABLE_CELLS; on a layered floor, what a narrow ground robot may use."""
        return self.cell_type(x, y) in self.walkable_types or (x, y) in self.walkable_extras
```

**Replace in** `backend/warehouse.py`:

```python
    def zone_of_cell(self, cell: Cell) -> Optional[Zone]:
        for zone in self.zones.values():
            if cell in zone.cells:
                return zone
        return None
```

with:

```python
    def zones_of_cell(self, cell: Cell) -> List[Zone]:
        """Every zone containing `cell`, in declaration order."""
        return list(self._cell_zones.get(cell, ()))

    def zone_of_cell(self, cell: Cell) -> Optional[Zone]:
        """The most specific zone containing `cell`: the smallest by cell
        count, ties going to the zone declared first."""
        zones = self._cell_zones.get(cell)
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
```

- [ ] **Step 8: Run the classic demo seed only on the classic floor**

**Replace in** `backend/digital_twin.py`:

```python
        if demo:
            self.load_demo(create_tasks=demo_tasks)
```

with:

```python
        # The classic demo seed only fits the classic floor; the
        # distribution-centre floor has no twin seed yet and boots empty.
        if demo and self.layout_name == "classic":
            self.load_demo(create_tasks=demo_tasks)
```

**Replace in** `backend/digital_twin.py`:

```python
            level=LogLevel.WARNING,
        )
        self.load_demo(create_tasks=demo_tasks)
```

with:

```python
            level=LogLevel.WARNING,
        )
        if self.layout_name == "classic":
            self.load_demo(create_tasks=demo_tasks)
```

- [ ] **Step 9: Run the layout tests and the classic pin**

Run: `.venv/bin/python -m pytest -o addopts="" -q backend/test_layout_distribution_center.py backend/test_layout_classic_golden.py backend/test_layouts.py`
Expected: `20 passed`.

- [ ] **Step 10: Run the full suite**

Run: `.venv/bin/python -m pytest -o addopts="" -q`
Expected: `2 failed, 387 passed` (only the two pre-existing failures).

- [ ] **Step 11: Commit**

```bash
git add backend/models.py backend/layouts/base.py backend/layouts/distribution_center.py \
        backend/layouts/__init__.py backend/warehouse.py backend/digital_twin.py \
        backend/test_layout_distribution_center.py
git commit -m "feat: distribution-centre 32x20 layout

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Inventory seed profiles and the new-floor catalog data

`open_inventory(..., profile="classic"|"distribution_center")` (spec §4.1): `classic` stays A's seed byte for byte; `distribution_center` puts every asset and worker at WH-01 with real §3.2 home zones and AST-000207 online. `SEED_VERSION` and the template cache become per profile. The catalog gains `lift_speed_mps`, `grasp_s`/`place_s`, drone `takeoff_s`/`land_s`/`scan_s` (§5.1) and release `known_issues` (§13.2). The app keeps booting classic (§17 step 1 item 3).

**Files:**
- Create: `backend/test_inventory_seed_golden.py`
- Modify: `backend/inventory/catalog_data.py` (model specs, `KNOWN_ISSUES`)
- Modify: `backend/inventory/schema.py` (`known_issues` column, `SCHEMA_VERSION = 3`)
- Modify: `backend/inventory/catalog.py` (`make_release_row` takes `known_issues`)
- Modify: `backend/inventory/seed.py` (seeds known issues)
- Overwrite: `backend/inventory/demo_seed.py` (two profiles over one scenario list)
- Overwrite: `backend/inventory/bootstrap.py` (profiles, per-profile versions and templates)
- Modify: `backend/digital_twin.py` (opens the inventory under the twin's layout)
- Test: `backend/test_inventory_seed_profiles.py` (create)

**Interfaces:**
- Consumes (Tasks 1–2): `DigitalTwin.layout_name`, `Warehouse(layout="distribution_center").zones`.
- Produces:
  - `open_inventory(path=":memory:", demo=True, clock=None, settings=None, profile: str = "classic") -> InventoryService`; an unknown profile raises `ValueError("Unknown inventory seed profile ...")`.
  - `reseed(service, demo=True, profile: Optional[str] = None)` — `None` reuses the profile stored in the database.
  - `backend.inventory.bootstrap.SEED_VERSIONS: Dict[str, str]` (keys `"classic"`, `"distribution_center"`); `SEED_VERSION == SEED_VERSIONS["classic"]` (A's tests import it). Meta keys: `seed_profile`, `seed_version`.
  - `backend.inventory.demo_seed.SEED_PROFILES = ("classic", "distribution_center")`, `HOME_ZONES: Dict[str, Dict[str, Optional[str]]]`, `seed_demo(service, profile="classic")`.
  - Catalog model `spec` keys: `lift_speed_mps` (every model), `grasp_s` and `place_s` (box-handling models), `takeoff_s`, `land_s`, `scan_s` (CT-IX2).
  - `backend.inventory.catalog_data.KNOWN_ISSUES: Dict[str, List[Dict[str, Any]]]` keyed by release id; each issue has `code`, `component_type`, `summary`, `effect`. Every release row (`get_release`, `list_releases`) now has `known_issues: List[Dict]` (`[]` when none).
  - `make_release_row(kind, target_code, version, released_at, status, min_hw_rev, supplier_name, known_issues=None)`.
  - `DigitalTwin` opens its inventory with `profile=self.layout_name`.

- [ ] **Step 1: Pin the classic seed — before changing any seed code**

The fingerprint hashes every row of the fleet and workforce tables of a classic demo seed built against a fixed clock. It was computed from the unchanged code; the catalog tables are left out because the catalog gains fields in this task (Plan ruling 2).

**Create** `backend/test_inventory_seed_golden.py`:

```python
"""Classic seed guard: the `classic` inventory seed profile must stay byte for
byte what A's tests were written against. The fingerprint covers every fleet
and workforce table (the catalog tables are excluded, since the catalog is
shared by every profile and gains fields). It was taken before seed profiles
existed; if it changes, the classic seed changed."""
import hashlib
import json
from datetime import datetime, timezone

from backend.inventory import open_inventory

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)
FLEET_AND_WORKFORCE_TABLES = (
    "robot_asset", "component", "calibration_record", "work_order", "ota_job",
    "reported_state", "worker", "worker_credential", "training_completion", "change_log",
)
CLASSIC_FINGERPRINT = "f743841530d9d345f78e8e79b7c3b3a8f33f638a593e3e203deaac33176709f2"


def fingerprint(inventory) -> str:
    digest = hashlib.sha256()
    for table in FLEET_AND_WORKFORCE_TABLES:
        for row in inventory.store.select(table, order="rowid"):
            digest.update(json.dumps(row, sort_keys=True).encode("utf-8"))
    return digest.hexdigest()


def test_the_classic_seed_is_unchanged():
    inventory = open_inventory(":memory:", demo=True, clock=lambda: NOW)
    assert fingerprint(inventory) == CLASSIC_FINGERPRINT
```

- [ ] **Step 2: Run it against the unchanged code**

Run: `.venv/bin/python -m pytest -o addopts="" -q backend/test_inventory_seed_golden.py`
Expected: `1 passed`. If it fails, stop and report the fingerprint you get; do not change the constant to make it pass.

- [ ] **Step 3: Commit the pin on its own**

```bash
git add backend/test_inventory_seed_golden.py
git commit -m "test: pin the classic inventory seed

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 4: Write the failing profile tests**

**Create** `backend/test_inventory_seed_profiles.py`:

```python
"""Inventory seed profiles (multi-embodiment spec §4.1) and the catalog data
the new floor needs (§5.1, §13.2)."""
import logging
import os
from datetime import datetime, timezone

import pytest

from backend.digital_twin import DigitalTwin
from backend.inventory import open_inventory, reseed
from backend.inventory.bootstrap import SEED_VERSION, SEED_VERSIONS
from backend.warehouse import Warehouse

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)
DC_HOME_ZONES = {
    "AST-000101": "parking_area", "AST-000102": "parking_area", "AST-000103": "workshop",
    "AST-000201": "tote_aisle_1", "AST-000202": "tote_aisle_2", "AST-000203": "pick_station_1",
    "AST-000204": "patrol_loop", "AST-000205": "pallet_aisle_1", "AST-000206": "pallet_aisle_2",
    "AST-000207": "dock_2", "AST-000208": "drone_pad", "AST-000209": "drone_pad",
    "AST-000210": "pack_cell_1", "AST-000211": "pack_cell_2", "AST-000212": "returns_qc",
    "AST-000213": None,
}


@pytest.fixture(scope="module")
def dc():
    return open_inventory(":memory:", demo=True, clock=lambda: NOW, profile="distribution_center")


def test_everything_is_at_wh01_on_the_new_floor(dc):
    robots = dc.list_robots()
    assert len(robots) == 16 and dc.list_robots(site="WH-02") == []
    assert {r["site_code"] for r in robots} == {"WH-01"}
    assert {r["asset_id"]: r["home_zone"] for r in robots} == DC_HOME_ZONES
    for worker in dc.list_workers():
        assert worker["site_codes"] == ["WH-01"], worker["worker_id"]
    jordan = dc.get_worker("E-10006")
    supervision = next(c for c in jordan["credentials"] if c["code"] == "humanoid_supervision")
    assert supervision["site_scope"] == ["WH-01"] and supervision["equipment_scope"] == ["TS-H1"]


def test_every_placed_asset_has_a_real_zone_of_the_new_floor(dc):
    floor = Warehouse(layout="distribution_center")
    for robot in dc.list_robots():
        if robot["lifecycle_status"] != "DECOMMISSIONED":
            assert robot["home_zone"] in floor.zones, robot["asset_id"]


def test_the_new_floor_keeps_a_s_scenarios_and_brings_the_hauler_online(dc):
    flags = {r["asset_id"]: r["flags"] for r in dc.list_robots()}
    assert dc.get_robot("AST-000103")["lifecycle_status"] == "MAINTENANCE"
    assert flags["AST-000202"]["running_recalled_release"]
    assert flags["AST-000203"]["calibration_worst"] == "EXPIRED"
    assert flags["AST-000204"]["calibration_worst"] == "DUE_SOON"
    assert dc.get_robot("AST-000205")["hw_revision"] == "A"
    assert flags["AST-000206"]["active_ota_job"]["state"] == "REPORTED"
    hauler = dc.get_robot("AST-000207")
    assert hauler["reported"]["connectivity"] == "ONLINE" and not flags["AST-000207"]["report_stale"]
    assert dc.get_robot("AST-000208")["reported"]["battery"]["soh_pct"] == 71.0
    assert [m["slot"] for m in flags["AST-000211"]["component_firmware_mismatches"]] == ["force_torque"]
    assert dc.get_robot("AST-000213")["lifecycle_status"] == "DECOMMISSIONED"
    assert "REVOKED" in [c["validity"] for c in dc.get_worker("E-10008")["credentials"]]
    assert dc.get_worker("E-10009")["employment_status"] == "ON_LEAVE"
    assert dc.get_worker("E-10010")["employment_status"] == "TERMINATED"


def test_seed_versions_and_templates_are_per_profile():
    assert set(SEED_VERSIONS) == {"classic", "distribution_center"}
    assert SEED_VERSION == SEED_VERSIONS["classic"] != SEED_VERSIONS["distribution_center"]
    classic, dc = open_inventory(), open_inventory(profile="distribution_center")  # template copies
    assert classic.get_robot("AST-000201")["site_code"] == "WH-02"
    assert dc.get_robot("AST-000201")["site_code"] == "WH-01"
    assert dc.store.get_meta("seed_profile") == "distribution_center"
    assert dc.store.get_meta("seed_version") == SEED_VERSIONS["distribution_center"]
    with pytest.raises(ValueError, match="Unknown inventory seed profile"):
        open_inventory(profile="moon_base")


def test_reseed_keeps_the_profile_it_was_seeded_under():
    templated = open_inventory(profile="distribution_center")
    clocked = open_inventory(":memory:", demo=True, clock=lambda: NOW, profile="distribution_center")
    for inventory in (templated, clocked):
        inventory.decommission("AST-000201", "test")
        reseed(inventory)
        assert inventory.get_robot("AST-000201")["lifecycle_status"] == "IN_SERVICE"
        assert inventory.get_robot("AST-000201")["site_code"] == "WH-01"


def test_a_file_opened_under_the_other_profile_is_reseeded_with_a_backup(tmp_path, caplog):
    path = str(tmp_path / "inv.sqlite3")
    first = open_inventory(path)
    assert first.get_robot("AST-000201")["site_code"] == "WH-02"
    first.store.conn.close()
    with caplog.at_level(logging.WARNING, logger="backend.inventory"):
        again = open_inventory(path, profile="distribution_center")
    assert again.get_robot("AST-000201")["site_code"] == "WH-01"
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1 and "'classic' seed profile" in warnings[0] and path + ".bak" in warnings[0]
    assert os.path.exists(path + ".bak")
    caplog.clear()
    again.store.conn.close()
    with caplog.at_level(logging.WARNING, logger="backend.inventory"):
        open_inventory(path, profile="distribution_center")  # now current: reused silently
    assert not caplog.records


def test_the_twin_opens_its_inventory_under_its_layout(tmp_path):
    def twin(layout):
        return DigitalTwin(log_dir=str(tmp_path / "logs"), data_dir=str(tmp_path / "data"),
                           persist_logs=False, demo=True, demo_tasks=False, layout=layout)

    assert twin("classic").inventory.store.get_meta("seed_profile") == "classic"
    new_floor = twin("distribution_center")
    assert new_floor.inventory.store.get_meta("seed_profile") == "distribution_center"
    assert new_floor.inventory.list_robots(site="WH-02") == []
    new_floor.reset(demo_tasks=False)
    assert new_floor.inventory.store.get_meta("seed_profile") == "distribution_center"


def test_catalog_timings_for_the_new_floor():
    inventory = open_inventory(demo=False)
    specs = {m["model_code"]: m["spec"] for m in inventory.list_models()}
    assert specs["NW-PF1200"]["lift_speed_mps"] == 0.3
    for code, spec in specs.items():
        if code != "NW-PF1200":
            assert spec["lift_speed_mps"] == 0.5, code
    timings = {code: (spec.get("grasp_s"), spec.get("place_s")) for code, spec in specs.items()}
    assert timings == {
        "NW-PF1200": (3.0, 3.0), "NW-HH300": (3.0, 3.0), "AC-TR50": (2.0, 1.5), "AC-PK30": (2.5, 1.5),
        "FB-CX10": (2.5, 1.5), "TS-H1": (3.0, 2.0), "AC-SC1": (None, None), "CT-IX2": (None, None),
    }
    drone = specs["CT-IX2"]
    assert (drone["takeoff_s"], drone["land_s"], drone["scan_s"]) == (4.0, 5.0, 3.0)


def test_release_known_issues():
    inventory = open_inventory(demo=False)
    drift = inventory.get_release("AC-TR50:SW:2.2.1")["known_issues"]
    assert [issue["code"] for issue in drift] == ["ODOMETRY_HEADING_DRIFT"]
    assert drift[0]["effect"] == {"heading_drift_deg_per_m": 0.5} and drift[0]["component_type"] == "DRIVE_UNIT"
    offset = inventory.get_release("FB-FT6:FW:3.2.0")["known_issues"]
    assert offset[0]["code"] == "FORCE_TORQUE_Z_OFFSET" and offset[0]["effect"] == {"force_z_offset_n": 8.0}
    assert inventory.get_release("AC-TR50:SW:2.2.0")["known_issues"] == []
    inventory.publish_release("ROBOT_SOFTWARE", "AC-TR50", "2.3.0")
    assert inventory.get_release("AC-TR50:SW:2.3.0")["known_issues"] == []
```

- [ ] **Step 5: Run them to see them fail**

Run: `.venv/bin/python -m pytest -o addopts="" -q backend/test_inventory_seed_profiles.py`
Expected: a collection error, `ImportError: cannot import name 'SEED_VERSIONS' from 'backend.inventory.bootstrap'`.

- [ ] **Step 6: Add the catalog timings and known issues**

Eight **Replace in** blocks on `backend/inventory/catalog_data.py`, one per model spec, then one for `KNOWN_ISSUES`.

**Replace in** `backend/inventory/catalog_data.py`:

```python
        "ip_rating": "IP54", "battery": _battery("LFP", 1200, 8, 1.5), "box_kinds": ["TOTE", "ITEM"],
        "supervision": None,
    }, ["ANSI/A3 R15.08-1", "ISO 3691-4"], [
```

with:

```python
        "ip_rating": "IP54", "battery": _battery("LFP", 1200, 8, 1.5), "box_kinds": ["TOTE", "ITEM"],
        "supervision": None, "lift_speed_mps": 0.5, "grasp_s": 2.0, "place_s": 1.5,
    }, ["ANSI/A3 R15.08-1", "ISO 3691-4"], [
```

**Replace in** `backend/inventory/catalog_data.py`:

```python
        "ip_rating": "IP54", "battery": _battery("LFP", 1200, 10, 1.5), "box_kinds": [],
        "supervision": None,
    }, ["ANSI/A3 R15.08-1"], [
```

with:

```python
        "ip_rating": "IP54", "battery": _battery("LFP", 1200, 10, 1.5), "box_kinds": [],
        "supervision": None, "lift_speed_mps": 0.5,
    }, ["ANSI/A3 R15.08-1"], [
```

**Replace in** `backend/inventory/catalog_data.py`:

```python
        "ip_rating": "IP54", "battery": _battery("LFP", 1200, 7, 1.5), "box_kinds": ["TOTE", "ITEM"],
        "supervision": None,
    }, ["ANSI/A3 R15.08-1", "ISO 10218-1"], [
```

with:

```python
        "ip_rating": "IP54", "battery": _battery("LFP", 1200, 7, 1.5), "box_kinds": ["TOTE", "ITEM"],
        "supervision": None, "lift_speed_mps": 0.5, "grasp_s": 2.5, "place_s": 1.5,
    }, ["ANSI/A3 R15.08-1", "ISO 10218-1"], [
```

**Replace in** `backend/inventory/catalog_data.py`:

```python
        "ip_rating": "IP54", "battery": _battery("lead-acid", 14400, 7, 8), "box_kinds": ["PALLET"],
        "supervision": None,
    }, ["ISO 3691-4", "ANSI/ITSDF B56.5"], [
```

with:

```python
        "ip_rating": "IP54", "battery": _battery("lead-acid", 14400, 7, 8), "box_kinds": ["PALLET"],
        "supervision": None, "lift_speed_mps": 0.3, "grasp_s": 3.0, "place_s": 3.0,
    }, ["ISO 3691-4", "ANSI/ITSDF B56.5"], [
```

**Replace in** `backend/inventory/catalog_data.py`:

```python
        "ip_rating": "IP54", "battery": _battery("lead-acid", 14400, 9, 8), "box_kinds": ["TOTE", "PALLET"],
        "supervision": None,
    }, ["ISO 3691-4"], [
```

with:

```python
        "ip_rating": "IP54", "battery": _battery("lead-acid", 14400, 9, 8), "box_kinds": ["TOTE", "PALLET"],
        "supervision": None, "lift_speed_mps": 0.5, "grasp_s": 3.0, "place_s": 3.0,
    }, ["ISO 3691-4"], [
```

**Replace in** `backend/inventory/catalog_data.py`:

```python
        "flight_time_min": 22, "supervision": None,
    }, ["IEC 62133-2"], [
```

with:

```python
        "flight_time_min": 22, "supervision": None, "lift_speed_mps": 0.5,
        "takeoff_s": 4.0, "land_s": 5.0, "scan_s": 3.0,
    }, ["IEC 62133-2"], [
```

**Replace in** `backend/inventory/catalog_data.py`:

```python
        "battery": None, "power": "mains 230 V", "box_kinds": ["ITEM"], "supervision": None,
    }, ["ISO 10218-1", "ISO/TS 15066"], [
```

with:

```python
        "battery": None, "power": "mains 230 V", "box_kinds": ["ITEM"], "supervision": None,
        "lift_speed_mps": 0.5, "grasp_s": 2.5, "place_s": 1.5,
    }, ["ISO 10218-1", "ISO/TS 15066"], [
```

**Replace in** `backend/inventory/catalog_data.py`:

```python
        "supervision": "humanoid_supervision",
    }, ["ISO 12100", "ISO 13849-1"], [
```

with:

```python
        "supervision": "humanoid_supervision", "lift_speed_mps": 0.5, "grasp_s": 3.0, "place_s": 2.0,
    }, ["ISO 12100", "ISO 13849-1"], [
```

**Replace in** `backend/inventory/catalog_data.py`:

```python
AI_POLICY_RELEASES: Dict[str, List[Tuple[str, int, str]]] = {
```

with:

```python
#: Known issues shipped with a release, keyed by release_id. Each effect is
#: what that release does to the readings of one component type (the
#: sensor models apply them — multi-embodiment spec §13.2).
KNOWN_ISSUES: Dict[str, List[Dict[str, Any]]] = {
    "AC-TR50:SW:2.2.1": [{
        "code": "ODOMETRY_HEADING_DRIFT", "component_type": "DRIVE_UNIT",
        "summary": "Wheel-odometry heading drifts 0.5 degrees per metre travelled",
        "effect": {"heading_drift_deg_per_m": 0.5},
    }],
    "FB-FT6:FW:3.2.0": [{
        "code": "FORCE_TORQUE_Z_OFFSET", "component_type": "FORCE_TORQUE",
        "summary": "Force-torque firmware reports an 8 N offset on the z axis",
        "effect": {"force_z_offset_n": 8.0},
    }],
}

AI_POLICY_RELEASES: Dict[str, List[Tuple[str, int, str]]] = {
```

- [ ] **Step 7: Store known issues on release rows**

**Replace in** `backend/inventory/schema.py`:

```python
SCHEMA_VERSION = 2
```

with:

```python
SCHEMA_VERSION = 3
```

**Replace in** `backend/inventory/schema.py`:

```python
    "component_firmware", "battery", "diff", "after",
})
```

with:

```python
    "component_firmware", "battery", "diff", "after", "known_issues",
})
```

**Replace in** `backend/inventory/schema.py`:

```python
    sbom_json   TEXT NOT NULL,
    sbom_sha256 TEXT NOT NULL,
    UNIQUE (kind, target_code, version)
```

with:

```python
    sbom_json   TEXT NOT NULL,
    sbom_sha256 TEXT NOT NULL,
    known_issues TEXT NOT NULL DEFAULT '[]',
    UNIQUE (kind, target_code, version)
```

**Replace in** `backend/inventory/catalog.py`:

```python
def make_release_row(kind: str, target_code: str, version: str, released_at: str, status: str,
                     min_hw_rev: Optional[str], supplier_name: str) -> Dict[str, Any]:
    row = {
```

with:

```python
def make_release_row(kind: str, target_code: str, version: str, released_at: str, status: str,
                     min_hw_rev: Optional[str], supplier_name: str,
                     known_issues: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    row = {
```

**Replace in** `backend/inventory/catalog.py`:

```python
    row["sbom_json"], row["sbom_sha256"] = serialize_sbom(build_sbom(row, supplier_name))
    return row
```

with:

```python
    row["sbom_json"], row["sbom_sha256"] = serialize_sbom(build_sbom(row, supplier_name))
    row["known_issues"] = list(known_issues or [])  # after the SBOM: it is not part of the bill of materials
    return row
```

**Replace in** `backend/inventory/seed.py`:

```python
from .catalog import make_release_row
from .catalog_data import (
    AI_POLICY_RELEASES, COMPONENT_FIRMWARE_MIN_HW, COMPONENT_FIRMWARE_VERSIONS,
    CREDENTIAL_DEFINITIONS, EXTRA_ROBOT_SOFTWARE_RELEASES, MANUFACTURERS, PART_MODELS,
    ROBOT_MODELS, ROBOT_SOFTWARE_MIN_HW, ROBOT_SOFTWARE_RELEASES,
)
```

with:

```python
from .catalog import make_release_row, release_id_for
from .catalog_data import (
    AI_POLICY_RELEASES, COMPONENT_FIRMWARE_MIN_HW, COMPONENT_FIRMWARE_VERSIONS,
    CREDENTIAL_DEFINITIONS, EXTRA_ROBOT_SOFTWARE_RELEASES, KNOWN_ISSUES, MANUFACTURERS, PART_MODELS,
    ROBOT_MODELS, ROBOT_SOFTWARE_MIN_HW, ROBOT_SOFTWARE_RELEASES,
)
```

**Replace in** `backend/inventory/seed.py`:

```python
    def released(days: int) -> str:
        return iso(now - timedelta(days=days))
```

with:

```python
    def released(days: int) -> str:
        return iso(now - timedelta(days=days))

    def issues(kind: str, target_code: str, version: str):
        return KNOWN_ISSUES.get(release_id_for(kind, target_code, version), [])
```

**Replace in** `backend/inventory/seed.py`:

```python
                store.insert("software_release", make_release_row(
                    "ROBOT_SOFTWARE", code, version, released(days), status,
                    ROBOT_SOFTWARE_MIN_HW.get((code, version)), model_supplier[code]))
```

with:

```python
                store.insert("software_release", make_release_row(
                    "ROBOT_SOFTWARE", code, version, released(days), status,
                    ROBOT_SOFTWARE_MIN_HW.get((code, version)), model_supplier[code],
                    issues("ROBOT_SOFTWARE", code, version)))
```

**Replace in** `backend/inventory/seed.py`:

```python
                store.insert("software_release", make_release_row(
                    "COMPONENT_FIRMWARE", part_number, version, released(days), status,
                    COMPONENT_FIRMWARE_MIN_HW.get((part_number, version)), part_supplier[part_number]))
```

with:

```python
                store.insert("software_release", make_release_row(
                    "COMPONENT_FIRMWARE", part_number, version, released(days), status,
                    COMPONENT_FIRMWARE_MIN_HW.get((part_number, version)), part_supplier[part_number],
                    issues("COMPONENT_FIRMWARE", part_number, version)))
```

- [ ] **Step 8: Two seed profiles over one scenario list**

The scenario calls stay in the same order with the same arguments, so the classic seed is unchanged (Step 1's pin proves it). Only the site of WH-02's people and robots, the home zones and AST-000207's OFFLINE report depend on the profile.

**Overwrite** `backend/inventory/demo_seed.py`:

```python
"""Demo fleet and workforce for the inventory (spec §6).

Everything is created through InventoryService actions, backdated with
service.at(), so the seeded change_log history has exactly the shape real
use produces. Deterministic: fixed ids, counters and relative dates.

Two seed profiles share one scenario list:
- "classic": WH-01 is the simulated floor; WH-02 is another site that exists
  only in the inventory, seeded so every trust-relevant flag shows up
  somewhere. A's tests run against it, byte for byte.
- "distribution_center": the same people, robots and flags, but every asset
  and worker is at WH-01, home zones are real zones of the 32x20 floor, and
  the heavy hauler is online (multi-embodiment spec §4.1). WH-02 is absent.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Callable, Dict, Mapping, Optional, Sequence

from .core import SYSTEM

FLOOR_SITE = "WH-01"
REMOTE_SITE = "WH-02"
SEED_PROFILES = ("classic", "distribution_center")

#: Each asset's home zone, per seed profile. AST-000213 is decommissioned and
#: never placed on the new floor, so it has no home zone there.
HOME_ZONES: Dict[str, Dict[str, Optional[str]]] = {
    "classic": {
        "AST-000101": "parking_area", "AST-000102": "parking_area", "AST-000103": "charging_station",
        "AST-000201": "tote_aisle_1", "AST-000202": "tote_aisle_2", "AST-000203": "pick_station_1",
        "AST-000204": "patrol_loop", "AST-000205": "pallet_aisle", "AST-000206": "pallet_aisle",
        "AST-000207": "dock_2", "AST-000208": "drone_pad", "AST-000209": "drone_pad",
        "AST-000210": "pack_cell_1", "AST-000211": "pack_cell_2", "AST-000212": "tote_aisle_1",
        "AST-000213": "tote_aisle_2",
    },
    "distribution_center": {
        "AST-000101": "parking_area", "AST-000102": "parking_area", "AST-000103": "workshop",
        "AST-000201": "tote_aisle_1", "AST-000202": "tote_aisle_2", "AST-000203": "pick_station_1",
        "AST-000204": "patrol_loop", "AST-000205": "pallet_aisle_1", "AST-000206": "pallet_aisle_2",
        "AST-000207": "dock_2", "AST-000208": "drone_pad", "AST-000209": "drone_pad",
        "AST-000210": "pack_cell_1", "AST-000211": "pack_cell_2", "AST-000212": "returns_qc",
        "AST-000213": None,
    },
}


def seed_demo(service, profile: str = "classic") -> None:
    if profile not in SEED_PROFILES:
        raise ValueError(f"Unknown seed profile {profile!r} (known: {list(SEED_PROFILES)})")
    now = service.now()
    # On the new floor there is one site: everything WH-02 held moves to WH-01.
    other_site = REMOTE_SITE if profile == "classic" else FLOOR_SITE

    def ago(days: float) -> datetime:
        return now - timedelta(days=days)

    with service.transaction():
        _seed_workers(service, ago, other_site)
        _seed_fleet(service, ago, other_site, HOME_ZONES[profile], hauler_offline=profile == "classic")
        with service.at(now):
            service.touch_remote_reports()


def _seed_workers(service, ago: Callable[[float], datetime], other_site: str) -> None:
    def hire(worker_id: str, name: str, roles: Sequence[str], sites: Sequence[str], *, days: float,
             worker_type: str = "EMPLOYEE", organization: str = "Warehouse Operations",
             supervisor: Optional[str] = None) -> None:
        with service.at(ago(days)):
            service.register_worker(name, worker_type, organization, roles, sites, worker_id=worker_id,
                                    supervisor_id=supervisor, actor=SYSTEM)

    def credential(worker_id: str, code: str, *, days: float, valid_days: int,
                   issuer: str = "Site Training Office", **scopes: Any) -> str:
        with service.at(ago(days)):
            start = service.now()
            return service.issue_credential(worker_id, code, issuer, effective_from=start,
                                            expires_at=start + timedelta(days=valid_days),
                                            actor=SYSTEM, **scopes)["subject_id"]

    hire("E-10001", "Sam", ["WAREHOUSE_OPERATOR", "SAFETY_INSPECTOR"], [FLOOR_SITE], days=900)
    credential("E-10001", "safety_inspection", days=200, valid_days=365, site_scope=[FLOOR_SITE])
    credential("E-10001", "electrical_safety", days=100, valid_days=365, site_scope=[FLOOR_SITE])
    hire("E-10002", "Lee", ["WAREHOUSE_OPERATOR"], [FLOOR_SITE], days=400)
    credential("E-10002", "safety_inspection", days=60, valid_days=365, site_scope=[FLOOR_SITE])
    hire("E-10003", "Noor Haddad", ["MAINTENANCE_TECH"], [FLOOR_SITE, other_site], days=1200)
    credential("E-10003", "equipment_maintenance", days=500, valid_days=730)
    credential("E-10003", "robot_maintenance", days=300, valid_days=730, equipment_scope=["AC-TR50", "AC-PK30", "AC-SC1"])
    credential("E-10003", "electrical_safety", days=120, valid_days=365)
    with service.at(ago(90)):
        service.record_training("E-10003", "LOTO-101", "3", completed_at=ago(90),
                                expires_at=ago(90) + timedelta(days=365), actor=SYSTEM)
    hire("E-10004", "Mateo Silva", ["ROBOTICS_TECH"], [other_site], days=700)
    credential("E-10004", "drone_operations", days=250, valid_days=730, equipment_scope=["CT-IX2"])
    credential("E-10004", "robot_maintenance", days=200, valid_days=730)
    credential("E-10004", "robot_cell_access", days=150, valid_days=365, equipment_scope=["FB-CX10"])
    hire("E-10005", "Ana Kowalski", ["FORKLIFT_OPERATOR"], [other_site], days=1500)
    credential("E-10005", "forklift_operator", days=400, valid_days=1095, equipment_scope=["NW-PF1200"])
    credential("E-10005", "heavy_equipment", days=400, valid_days=1095)
    hire("E-10006", "Jordan Blake", ["HUMANOID_SUPERVISOR"], [other_site], days=300)
    credential("E-10006", "humanoid_supervision", days=90, valid_days=365, equipment_scope=["TS-H1"],
               site_scope=[other_site])
    credential("E-10006", "safety_inspection", days=90, valid_days=365)
    hire("E-10007", "Kai Nakamura", ["FORKLIFT_OPERATOR"], [other_site], days=358, worker_type="CONTRACTOR",
         organization="Proseware Staffing", supervisor="E-10005")
    credential("E-10007", "forklift_operator", days=358, valid_days=365, issuer="Proseware Staffing Training",
               equipment_scope=["NW-PF1200"])  # expires in 7 days
    hire("E-10008", "Riley Chen", ["ROBOT_CELL_OPERATOR"], [other_site], days=500)
    revoked = credential("E-10008", "robot_cell_access", days=200, valid_days=365, equipment_scope=["FB-CX10"])
    credential("E-10008", "safety_inspection", days=100, valid_days=365)
    with service.at(ago(10)):
        service.revoke_credential(revoked, "Failed recertification audit", actor=SYSTEM)
    hire("E-10009", "Sasha Ivanova", ["FORKLIFT_OPERATOR"], [other_site], days=800)
    credential("E-10009", "forklift_operator", days=300, valid_days=1095, equipment_scope=["NW-PF1200"])
    with service.at(ago(14)):
        service.set_employment_status("E-10009", "ON_LEAVE", "Parental leave", actor=SYSTEM)
    hire("E-10010", "Morgan Patel", ["WAREHOUSE_OPERATOR"], [FLOOR_SITE], days=600)
    credential("E-10010", "safety_inspection", days=250, valid_days=365)
    with service.at(ago(45)):
        service.set_employment_status("E-10010", "TERMINATED", "Left the company", actor=SYSTEM)


def _seed_fleet(service, ago: Callable[[float], datetime], other_site: str,
                home_zones: Mapping[str, Optional[str]], hauler_offline: bool) -> None:
    def commission(asset_id: str, model: str, site: str, *, days: float,
                   hw: Optional[str] = None, version: Optional[str] = None) -> None:
        zone = home_zones[asset_id]
        with service.at(ago(days)):
            service.commission_robot(model, site, zone, asset_id=asset_id, hw_revision=hw,
                                     software_release_id=f"{model}:SW:{version}" if version else None,
                                     fleet_id=f"{site}-FLEET", actor=SYSTEM)

    def recalibrate(asset_id: str, *, days: float, by: str, skip_types: Sequence[str] = ()) -> None:
        with service.at(ago(days)):
            for component in service.get_robot(asset_id)["components"]:
                if component["calibration_interval_days"] and component["component_type"] not in skip_types:
                    service.record_calibration(component["component_id"], "PASS", performed_by=by)

    def report(asset_id: str, days: float = 0, **fields: Any) -> None:
        with service.at(ago(days)):
            service.report_state(asset_id, fields, actor=SYSTEM)

    # ---- WH-01: the simulated floor ------------------------------------ #
    commission("AST-000101", "AC-TR50", FLOOR_SITE, days=380, version="2.1.0")
    recalibrate("AST-000101", days=20, by="E-10003")
    report("AST-000101", battery={"soh_pct": 96.4, "cycle_count": 212})
    commission("AST-000102", "AC-TR50", FLOOR_SITE, days=380, version="2.1.0")
    recalibrate("AST-000102", days=20, by="E-10003")
    report("AST-000102", battery={"soh_pct": 95.1, "cycle_count": 240})
    commission("AST-000103", "AC-TR50", FLOOR_SITE, days=500, hw="C", version="2.2.0")
    recalibrate("AST-000103", days=30, by="E-10003")
    with service.at(ago(1)):
        wo = service.open_work_order("AST-000103", "CORRECTIVE",
                                     "Lidar returns degraded on the east aisle - replace unit",
                                     technician_id="E-10003")["subject_id"]
        service.swap_component(wo, "lidar", performed_by="E-10003")

    # ---- WH-02 (classic) or WH-01 (new floor): one scenario per asset -- #
    commission("AST-000201", "AC-TR50", other_site, days=90)
    commission("AST-000202", "AC-TR50", other_site, days=100)
    report("AST-000202", days=3, software_version="2.2.1", os_version=service.os_version_for("2.2.1"))
    commission("AST-000203", "AC-PK30", other_site, days=400)
    recalibrate("AST-000203", days=60, by="E-10004", skip_types=("LIDAR",))
    commission("AST-000204", "AC-SC1", other_site, days=356)
    recalibrate("AST-000204", days=60, by="E-10004", skip_types=("LIDAR", "IMU"))
    commission("AST-000205", "NW-PF1200", other_site, days=300, hw="A", version="2.1.1")
    recalibrate("AST-000205", days=30, by="E-10004")
    commission("AST-000206", "NW-PF1200", other_site, days=200, hw="B", version="2.1.1")
    recalibrate("AST-000206", days=20, by="E-10004")
    with service.at(ago(1)):
        job = service.start_ota("AST-000206", "NW-PF1200:SW:2.2.0",
                                actor={"type": "WORKER", "id": "E-10004"})["subject_id"]
        service.transition_ota(job, "DOWNLOADING")
        service.transition_ota(job, "INSTALLING")
        service.report_state("AST-000206", {"software_version": "2.2.0",
                                            "os_version": service.os_version_for("2.2.0")}, actor=SYSTEM)
        service.transition_ota(job, "REPORTED")
    commission("AST-000207", "NW-HH300", other_site, days=250)
    recalibrate("AST-000207", days=40, by="E-10004")
    if hauler_offline:  # a record-only scenario; on the new floor the hauler is really there
        report("AST-000207", days=2, connectivity="OFFLINE", health_state="UNKNOWN")
    commission("AST-000208", "CT-IX2", other_site, days=150)
    recalibrate("AST-000208", days=30, by="E-10004")
    report("AST-000208", battery={"soh_pct": 71.0, "cycle_count": 612})
    commission("AST-000209", "CT-IX2", other_site, days=60)
    commission("AST-000210", "FB-CX10", other_site, days=120)
    recalibrate("AST-000210", days=15, by="E-10004")
    commission("AST-000211", "FB-CX10", other_site, days=120)
    recalibrate("AST-000211", days=15, by="E-10004")
    firmware = dict(service.get_robot("AST-000211")["reported"]["component_firmware"])
    report("AST-000211", days=5, component_firmware={**firmware, "force_torque": "3.2.0"})
    commission("AST-000212", "TS-H1", other_site, days=45)
    commission("AST-000213", "AC-TR50", other_site, days=700, version="2.1.0")
    with service.at(ago(30)):
        service.decommission("AST-000213", "Chassis damage - written off", actor=SYSTEM)
```

- [ ] **Step 9: Profiles in bootstrap**

**Overwrite** `backend/inventory/bootstrap.py`:

```python
"""Open, seed and re-seed inventory databases.

Seeding a demo inventory runs a few hundred service actions, so the seeded
database is built once per process into an in-memory *template* and copied
into each new database with sqlite3's backup API (the test suite builds
hundreds of twins). A template older than TEMPLATE_MAX_AGE_SECONDS is
rebuilt so seeded dates ("calibration expires in 10 days") stay relative
to now. Passing a `clock` skips the template and seeds directly against
that clock, which keeps unit tests deterministic.

A database is seeded under one seed *profile* (demo_seed.SEED_PROFILES:
"classic", or "distribution_center" for the 32x20 floor). A file records
the profile and the SEED_VERSIONS entry it was seeded under; opening one
seeded under another schema, seed or profile re-seeds it, keeping the old
file as .bak. Templates are cached per (demo, profile).
"""
from __future__ import annotations

import hashlib
import json
import logging
import shutil
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional, Tuple

from . import catalog_data
from .demo_seed import SEED_PROFILES
from .schema import SCHEMA_VERSION, connect, create_schema, is_current, new_epoch
from .seed import seed_catalog
from .service import InventoryService
from .store import InventoryStore

logger = logging.getLogger("backend.inventory")

TEMPLATE_MAX_AGE_SECONDS = 60
_CATALOG_TABLES = (
    "MANUFACTURERS", "PART_MODELS", "ROBOT_MODELS", "CREDENTIAL_DEFINITIONS", "CLASS_DEFAULT_MODELS",
    "ROBOT_SOFTWARE_RELEASES", "EXTRA_ROBOT_SOFTWARE_RELEASES", "ROBOT_SOFTWARE_MIN_HW",
    "COMPONENT_FIRMWARE_VERSIONS", "COMPONENT_FIRMWARE_MIN_HW", "AI_POLICY_RELEASES", "OS_VERSIONS",
    "KNOWN_ISSUES",
)
_templates: Dict[Tuple[bool, str], Tuple[sqlite3.Connection, float]] = {}
_template_lock = threading.Lock()


def _plain(value: Any) -> Any:
    """`value` with tuple dict keys joined into strings, so json.dumps can take it."""
    if isinstance(value, dict):
        return {":".join(key) if isinstance(key, tuple) else key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def _seed_version(profile: str) -> str:
    """Fingerprint of everything that decides what a fresh seed of `profile`
    contains: the schema version, the catalog tables, the demo seed's source
    and the profile. A file seeded under a different fingerprint is re-seeded
    (the old one kept as .bak)."""
    catalog = {name: _plain(getattr(catalog_data, name)) for name in _CATALOG_TABLES}
    demo_source = Path(__file__).with_name("demo_seed.py").read_text(encoding="utf-8")
    text = str(SCHEMA_VERSION) + json.dumps(catalog, sort_keys=True) + demo_source + profile
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


SEED_VERSIONS: Dict[str, str] = {profile: _seed_version(profile) for profile in SEED_PROFILES}
SEED_VERSION = SEED_VERSIONS["classic"]


def _check_profile(profile: str) -> str:
    if profile not in SEED_VERSIONS:
        raise ValueError(f"Unknown inventory seed profile {profile!r} (known: {list(SEED_PROFILES)})")
    return profile


def _seed_into(conn: sqlite3.Connection, demo: bool, clock: Optional[Callable[[], datetime]] = None,
               profile: str = "classic") -> None:
    create_schema(conn)
    service = InventoryService(InventoryStore(conn), clock=clock)
    seed_catalog(service)
    if demo:
        from .demo_seed import seed_demo

        seed_demo(service, profile)
    service.store.set_meta("seed_profile", profile)
    service.store.set_meta("seed_version", SEED_VERSIONS[profile])
    service.store.set_meta("seeded", "1")


def _wipe(conn: sqlite3.Connection) -> None:
    names = [row["name"] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")]
    conn.execute("PRAGMA foreign_keys = OFF")
    for name in names:
        conn.execute(f'DROP TABLE IF EXISTS "{name}"')
    conn.execute("PRAGMA foreign_keys = ON")


def _meta(conn: sqlite3.Connection, key: str) -> Optional[str]:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def _stale_reason(conn: sqlite3.Connection, profile: str) -> Optional[str]:
    """Why `conn`'s database can't be used as `profile` (None when it is current)."""
    if not is_current(conn):
        return f"it is not a fully seeded schema-version-{SCHEMA_VERSION} inventory"
    stored = _meta(conn, "seed_profile") or "classic"  # files from before profiles are classic
    if stored != profile:
        return f"it was seeded under the {stored!r} seed profile, not {profile!r}"
    if _meta(conn, "seed_version") != SEED_VERSIONS[profile]:
        return "the catalog or demo seed has changed since it was seeded"
    return None


def _has_tables(conn: sqlite3.Connection) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' LIMIT 1").fetchone() is not None


def _keep_backup(conn: sqlite3.Connection, path: str, reason: str) -> None:
    backup = path + ".bak"
    logger.warning("Re-seeding inventory database %s because %s; the previous file is kept as %s",
                   path, reason, backup)
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")  # fold the WAL in so the copied file is complete
    shutil.copy2(path, backup)


def _template(demo: bool, profile: str) -> sqlite3.Connection:
    """Call with _template_lock held."""
    cached = _templates.get((demo, profile))
    if cached is not None and time.monotonic() - cached[1] < TEMPLATE_MAX_AGE_SECONDS:
        return cached[0]
    conn = connect(":memory:")
    _seed_into(conn, demo, profile=profile)
    _templates[(demo, profile)] = (conn, time.monotonic())
    return conn


def _restore(conn: sqlite3.Connection, demo: bool, profile: str) -> None:
    with _template_lock:
        _template(demo, profile).backup(conn)
    conn.execute("UPDATE meta SET value = ? WHERE key = 'epoch'", (new_epoch(),))


def open_inventory(
    path: str = ":memory:",
    demo: bool = True,
    clock: Optional[Callable[[], datetime]] = None,
    settings: Optional[Callable[[], Mapping[str, Any]]] = None,
    profile: str = "classic",
) -> InventoryService:
    """Open (creating and seeding if needed) the inventory at `path`, seeded
    under `profile`."""
    _check_profile(profile)
    conn = connect(path)
    reason = None if path == ":memory:" else _stale_reason(conn, profile)
    if path == ":memory:" or reason is not None:
        if reason is not None and _has_tables(conn):
            _keep_backup(conn, path, reason)
        if clock is not None:
            _wipe(conn)
            _seed_into(conn, demo, clock, profile)
        else:
            _restore(conn, demo, profile)
    return InventoryService(InventoryStore(conn), clock=clock, settings=settings)


def reseed(service: InventoryService, demo: bool = True, profile: Optional[str] = None) -> None:
    """Replace everything in `service`'s database with a fresh seed and a new
    epoch — under `profile`, or the profile it was last seeded under."""
    store = service.store
    profile = _check_profile(profile or store.get_meta("seed_profile") or "classic")
    with store.lock:
        if store.depth:
            raise RuntimeError("Cannot reseed the inventory inside a transaction")
        if service.custom_clock:
            _wipe(store.conn)
            _seed_into(store.conn, demo, service._clock, profile)
        else:
            _restore(store.conn, demo, profile)
```

- [ ] **Step 10: The twin opens its inventory under its layout**

**Replace in** `backend/digital_twin.py`:

```python
        self.inventory = open_inventory(inventory_path, demo=demo, settings=lambda: CONFIG)
```

with:

```python
        # Seeded under the profile named after the floor: "classic" keeps A's
        # two-site seed, "distribution_center" puts everyone at WH-01.
        self.inventory = open_inventory(inventory_path, demo=demo, settings=lambda: CONFIG,
                                        profile=self.layout_name)
```

- [ ] **Step 11: Run the seed tests, the pin and A's catalog and seed tests**

Run: `.venv/bin/python -m pytest -o addopts="" -q backend/test_inventory_seed_profiles.py backend/test_inventory_seed_golden.py backend/test_inventory_catalog.py backend/test_inventory_seed.py`
Expected: `28 passed`.

- [ ] **Step 12: Run the full suite**

Run: `.venv/bin/python -m pytest -o addopts="" -q`
Expected: `2 failed, 397 passed` (only the two pre-existing failures).

- [ ] **Step 13: Commit**

```bash
git add backend/inventory/catalog_data.py backend/inventory/schema.py backend/inventory/catalog.py \
        backend/inventory/seed.py backend/inventory/demo_seed.py backend/inventory/bootstrap.py \
        backend/digital_twin.py backend/test_inventory_seed_profiles.py
git commit -m "feat: inventory seed profiles and new-floor catalog data

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Mobility profiles, per-profile passability and the routing engine

`backend/embodiment.py` with `MobilityProfile.from_model` (spec §5.1) and the physical-duration helpers (§5.4's `ceil(seconds / TICK_DT)`), `Warehouse.passable(cell, profile, layer)` (§5.2), and optional `profile`/`layer` on every `NavigationEngine` query and on `nearest_walkable`/`neighbors`. With no profile, every query behaves exactly as today (§17 step 1 item 4, first half — see "Task count" above).

**Files:**
- Create: `backend/embodiment.py`
- Modify: `backend/models.py` (`CONFIG["CELL_SIZE_M"]`, `CONFIG["LOADED_SPEED_FACTOR"]`)
- Modify: `backend/warehouse.py` (`passable`, `passable_cells`; `neighbors` and `nearest_walkable` take a profile)
- Overwrite: `backend/navigation.py` (`profile`/`layer` on `find_path`, `path_exists`, `distance`, `best_cell_in_zone`)
- Test: `backend/test_embodiment.py` (create)

**Interfaces:**
- Consumes (Tasks 2–3): `Warehouse.is_walkable`, `.is_flyable(cell)`, `._clearance`, `CellType.DRONE_PAD`; `backend.layouts.base.WIDE`/`NARROW`; catalog model specs from `backend.inventory.catalog_data.ROBOT_MODELS` (with Task 3's timing fields).
- Produces:
  - `backend.embodiment`: `GROUND = "GROUND"`, `AIR = "AIR"`, `FIXED = "FIXED"`, `MOVEMENTS`, `LAYERS = (GROUND, AIR)`, re-exported `WIDE`, `NARROW`; `LOADED_SLOWDOWN_CLASSES = {"FORKLIFT", "HEAVY_HAULER"}`; `DEFAULT_LIFT_SPEED_MPS = 0.5`.
  - `BatterySpec(chemistry: str, capacity_wh: float, runtime_h: float, charge_time_h: float)`, frozen.
  - `MobilityProfile`, frozen, fields: `embodiment_class: str`, `movement: str`, `clearance: Optional[str]`, `speed_cells_s: float`, `slows_when_loaded: bool`, `max_payload_kg: float`, `max_shelf_level: int`, `box_kinds: Tuple[str, ...]`, `max_lift_m: float`, `reach_mm: Optional[float]`, `supervision: Optional[str]`, `battery: Optional[BatterySpec]`, `flight_time_min: Optional[float]`, `lift_speed_mps: float`, `takeoff_s`, `land_s`, `scan_s`, `grasp_s`, `place_s: Optional[float]`. `MobilityProfile.from_model(spec: Mapping, embodiment_class: str)` (ground robots default to NARROW; an unknown movement raises `ValueError`); properties `is_ground`, `is_air`, `is_fixed`, `loaded_speed_cells_s`; `to_dict()`.
  - `seconds_to_ticks(seconds: float) -> int`, `lift_ticks(profile, from_m: float, to_m: float) -> int`, `STEP_TIMINGS`, `step_ticks(profile, step: str) -> int` for `TAKEOFF`, `LAND`, `SCAN`, `GRASP`, `PLACE`, `PLACE_ON_CONVEYOR` (`ValueError` if unknown or the body has no such timing).
  - `CONFIG["CELL_SIZE_M"] = 1.5`, `CONFIG["LOADED_SPEED_FACTOR"] = 0.7`.
  - `Warehouse.passable(cell, profile: Optional[MobilityProfile] = None, layer: str = GROUND) -> bool`, `passable_cells(profile=None, layer=GROUND) -> List[Cell]`, `neighbors(cell, profile=None, layer=GROUND)`, `nearest_walkable(cell, blocked=None, profile=None, layer=GROUND)`.
  - `NavigationEngine.find_path(start, goal, blocked=None, allow_goal_adjacent=True, is_replan=False, profile=None, layer=GROUND)`, `path_exists(start, goal, blocked=None, profile=None, layer=GROUND)`, `distance(start, goal, blocked=None, profile=None, layer=GROUND)`, `best_cell_in_zone(zone_cells, origin, blocked=None, prefer=None, profile=None, layer=GROUND)`.

- [ ] **Step 1: Write the failing tests**

**Create** `backend/test_embodiment.py`:

```python
"""Mobility profiles from the catalog, per-profile passability and routing
(multi-embodiment spec §5.1–§5.2)."""
import json

import pytest

from backend.embodiment import (
    AIR, GROUND, MobilityProfile, lift_ticks, seconds_to_ticks, step_ticks,
)
from backend.inventory.catalog_data import ROBOT_MODELS
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


def test_classic_routing_is_unchanged_without_a_profile():
    classic = Warehouse()
    nav = NavigationEngine(classic)
    assert nav.find_path((3, 8), (16, 8)) == nav.find_path((3, 8), (16, 8), profile=None, layer=GROUND)
    assert classic.neighbors((8, 7)) == classic.neighbors((8, 7), None)
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/bin/python -m pytest -o addopts="" -q backend/test_embodiment.py`
Expected: a collection error, `ModuleNotFoundError: No module named 'backend.embodiment'`.

- [ ] **Step 3: Add the two CONFIG keys**

**Replace in** `backend/models.py`:

```python
    "CALIBRATION_DUE_SOON_DAYS": 14,
    "CREDENTIAL_EXPIRING_SOON_DAYS": 30,
    "REPORT_STALE_SECONDS": 300,
}
```

with:

```python
    "CALIBRATION_DUE_SOON_DAYS": 14,
    "CREDENTIAL_EXPIRING_SOON_DAYS": 30,
    "REPORT_STALE_SECONDS": 300,
    # Multi-embodiment floor (backend/embodiment.py): metres per grid cell,
    # and the speed a loaded forklift or heavy hauler keeps (× its top speed).
    "CELL_SIZE_M": 1.5,
    "LOADED_SPEED_FACTOR": 0.7,
}
```

- [ ] **Step 4: Create the embodiment module**

**Create** `backend/embodiment.py`:

```python
"""Embodiment: what a robot's body lets it do on the floor (multi-embodiment
spec §5).

A MobilityProfile is read from the inventory's catalog model spec, so
editing a model changes behaviour. On the distribution-centre floor every
robot carries the profile of its bound asset's model; on the classic floor
robots carry none and move exactly as they always have. The timing helpers
turn the catalog's physical durations into whole simulation ticks.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any, Dict, Mapping, Optional, Tuple

from .layouts.base import NARROW, WIDE
from .models import CONFIG

GROUND = "GROUND"
AIR = "AIR"
FIXED = "FIXED"
MOVEMENTS = (GROUND, AIR, FIXED)
#: The layers a robot can occupy. Only drones ever leave GROUND.
LAYERS = (GROUND, AIR)

#: Classes that travel slower while carrying (× CONFIG["LOADED_SPEED_FACTOR"]).
LOADED_SLOWDOWN_CLASSES = frozenset({"FORKLIFT", "HEAVY_HAULER"})
DEFAULT_LIFT_SPEED_MPS = 0.5


@dataclass(frozen=True)
class BatterySpec:
    chemistry: str
    capacity_wh: float
    runtime_h: float
    charge_time_h: float


def _float(value: Any) -> Optional[float]:
    return None if value is None else float(value)


@dataclass(frozen=True)
class MobilityProfile:
    embodiment_class: str
    movement: str                 # GROUND, AIR or FIXED
    clearance: Optional[str]      # WIDE or NARROW for ground robots; None otherwise
    speed_cells_s: float
    slows_when_loaded: bool
    max_payload_kg: float
    max_shelf_level: int
    box_kinds: Tuple[str, ...]
    max_lift_m: float
    reach_mm: Optional[float]
    supervision: Optional[str]    # the credential a supervisor needs (humanoid), or None
    battery: Optional[BatterySpec]  # None for mains power
    flight_time_min: Optional[float]
    lift_speed_mps: float
    takeoff_s: Optional[float]
    land_s: Optional[float]
    scan_s: Optional[float]
    grasp_s: Optional[float]
    place_s: Optional[float]

    @classmethod
    def from_model(cls, spec: Mapping[str, Any], embodiment_class: str) -> "MobilityProfile":
        """Build the profile from a catalog robot-model `spec` (spec §5.1)."""
        movement = str(spec.get("movement") or GROUND).upper()
        if movement not in MOVEMENTS:
            raise ValueError(f"Unknown movement {movement!r} (known: {list(MOVEMENTS)})")
        clearance = None
        if movement == GROUND:
            clearance = str(spec.get("clearance") or NARROW).upper()
        battery = spec.get("battery")
        return cls(
            embodiment_class=str(embodiment_class).upper(),
            movement=movement,
            clearance=clearance,
            speed_cells_s=float(spec.get("max_speed_mps") or 0.0) / CONFIG["CELL_SIZE_M"],
            slows_when_loaded=str(embodiment_class).upper() in LOADED_SLOWDOWN_CLASSES,
            max_payload_kg=float(spec.get("max_payload_kg") or 0.0),
            max_shelf_level=int(spec.get("max_shelf_level") or 0),
            box_kinds=tuple(spec.get("box_kinds") or ()),
            max_lift_m=float(spec.get("max_lift_m") or 0.0),
            reach_mm=_float(spec.get("reach_mm")),
            supervision=spec.get("supervision"),
            battery=BatterySpec(
                chemistry=str(battery["chemistry"]), capacity_wh=float(battery["capacity_wh"]),
                runtime_h=float(battery["runtime_h"]), charge_time_h=float(battery["charge_time_h"]),
            ) if battery else None,
            flight_time_min=_float(spec.get("flight_time_min")),
            lift_speed_mps=float(spec.get("lift_speed_mps") or DEFAULT_LIFT_SPEED_MPS),
            takeoff_s=_float(spec.get("takeoff_s")),
            land_s=_float(spec.get("land_s")),
            scan_s=_float(spec.get("scan_s")),
            grasp_s=_float(spec.get("grasp_s")),
            place_s=_float(spec.get("place_s")),
        )

    @property
    def is_ground(self) -> bool:
        return self.movement == GROUND

    @property
    def is_air(self) -> bool:
        return self.movement == AIR

    @property
    def is_fixed(self) -> bool:
        return self.movement == FIXED

    @property
    def loaded_speed_cells_s(self) -> float:
        factor = CONFIG["LOADED_SPEED_FACTOR"] if self.slows_when_loaded else 1.0
        return self.speed_cells_s * factor

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["box_kinds"] = list(self.box_kinds)
        return data


# --------------------------------------------------------------------------- #
# Physical durations as simulation ticks (spec §5.4)
# --------------------------------------------------------------------------- #
def seconds_to_ticks(seconds: float) -> int:
    """ceil(seconds / TICK_DT). The quotient is rounded first so float noise
    (3.0 / 0.15 = 20.000000000000004) can't add a phantom tick."""
    if seconds <= 0:
        return 0
    return int(math.ceil(round(seconds / CONFIG["TICK_DT"], 6)))


def lift_ticks(profile: MobilityProfile, from_m: float, to_m: float) -> int:
    """LIFT_TO / LOWER: the height difference ÷ lift_speed_mps."""
    return seconds_to_ticks(abs(to_m - from_m) / profile.lift_speed_mps)


#: Step name -> the profile field holding its duration in seconds.
STEP_TIMINGS = {
    "TAKEOFF": "takeoff_s", "LAND": "land_s", "SCAN": "scan_s",
    "GRASP": "grasp_s", "PLACE": "place_s", "PLACE_ON_CONVEYOR": "place_s",
}


def step_ticks(profile: MobilityProfile, step: str) -> int:
    """Ticks one TAKEOFF / LAND / SCAN (per level) / GRASP / PLACE /
    PLACE_ON_CONVEYOR step takes for this body."""
    field = STEP_TIMINGS.get(step)
    if field is None:
        raise ValueError(f"Unknown timed step {step!r} (known: {sorted(STEP_TIMINGS)})")
    seconds = getattr(profile, field)
    if seconds is None:
        raise ValueError(f"A {profile.embodiment_class} has no {step} timing in its catalog model")
    return seconds_to_ticks(seconds)
```

- [ ] **Step 5: Passability per profile and layer**

**Replace in** `backend/warehouse.py`:

```python
from .layouts import build_layout
from .layouts.base import NARROW, WIDE, Slot, Zone
```

with:

```python
from .embodiment import AIR, GROUND, MobilityProfile
from .layouts import build_layout
from .layouts.base import NARROW, WIDE, Slot, Zone
```

**Replace in** `backend/warehouse.py`:

```python
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
```

with:

```python
    def passable(self, cell: Cell, profile: Optional[MobilityProfile] = None, layer: str = GROUND) -> bool:
        """May a robot with `profile` occupy `cell` on `layer` (spec §5.2)?

        No profile: exactly is_walkable (every classic robot). Ground robots:
        walkable cells, WIDE cells only for a wide robot. Drones: on AIR any
        flyable cell; on GROUND only their pad. Fixed equipment never moves.
        """
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
```

**Replace in** `backend/warehouse.py`:

```python
    def nearest_walkable(self, cell: Cell, blocked: Optional[Set[Cell]] = None) -> Optional[Cell]:
        """Breadth-first search outwards for the closest drivable cell."""
        blocked = blocked or set()
        if self.is_walkable(*cell) and cell not in blocked:
            return cell
```

with:

```python
    def nearest_walkable(self, cell: Cell, blocked: Optional[Set[Cell]] = None,
                         profile: Optional[MobilityProfile] = None, layer: str = GROUND) -> Optional[Cell]:
        """Breadth-first search outwards for the closest cell `profile` may use
        on `layer` (with no profile: the closest drivable cell)."""
        blocked = blocked or set()
        if self.passable(cell, profile, layer) and cell not in blocked:
            return cell
```

**Replace in** `backend/warehouse.py`:

```python
                seen.add(nxt)
                if self.is_walkable(*nxt) and nxt not in blocked:
                    return nxt
```

with:

```python
                seen.add(nxt)
                if self.passable(nxt, profile, layer) and nxt not in blocked:
                    return nxt
```

- [ ] **Step 6: Route by profile**

**Overwrite** `backend/navigation.py`:

```python
"""Navigation: A* pathfinding over the warehouse grid.

The engine is deliberately stateless — it takes the static grid plus a set of
dynamic obstacles (other robots, temporary blockages) and returns a path. That
makes replanning mid-mission a plain function call.

Every query takes an optional mobility `profile` and `layer` (see
backend/embodiment.py): a forklift routes only over WIDE cells, a drone over
racks on the AIR layer. With no profile, routing is exactly as it always was.
"""
from __future__ import annotations

import heapq
from typing import Dict, Iterable, List, Optional, Set, Tuple

from .embodiment import GROUND, MobilityProfile
from .models import Cell, manhattan
from .warehouse import Warehouse


class NavigationEngine:
    def __init__(self, warehouse: Warehouse) -> None:
        self.warehouse = warehouse
        self.paths_computed = 0
        self.replans = 0
        self.failures = 0

    # ------------------------------------------------------------------ #
    # A*
    # ------------------------------------------------------------------ #
    def find_path(
        self,
        start: Cell,
        goal: Cell,
        blocked: Optional[Iterable[Cell]] = None,
        allow_goal_adjacent: bool = True,
        is_replan: bool = False,
        profile: Optional[MobilityProfile] = None,
        layer: str = GROUND,
    ) -> Optional[List[Cell]]:
        """Return the cells to traverse from ``start`` to ``goal``, exclusive of
        ``start``, using only cells ``profile`` may occupy on ``layer``.
        ``None`` means no route exists.
        """
        blocked_set: Set[Cell] = set(blocked or ())
        blocked_set.discard(start)

        target = goal
        if not self.warehouse.passable(goal, profile, layer) or goal in blocked_set:
            if not allow_goal_adjacent:
                self.failures += 1
                return None
            replacement = self.warehouse.nearest_walkable(goal, blocked_set, profile=profile, layer=layer)
            if replacement is None:
                self.failures += 1
                return None
            target = replacement

        if start == target:
            self.paths_computed += 1
            return []

        open_heap: List[Tuple[int, int, Cell]] = [(manhattan(start, target), 0, start)]
        came_from: Dict[Cell, Optional[Cell]] = {start: None}
        g_score: Dict[Cell, int] = {start: 0}
        closed: Set[Cell] = set()

        while open_heap:
            _, cost, current = heapq.heappop(open_heap)
            if current in closed:
                continue
            closed.add(current)
            if current == target:
                path = self._reconstruct(came_from, current)
                self.paths_computed += 1
                if is_replan:
                    self.replans += 1
                return path
            for neighbor in self.warehouse.neighbors(current, profile, layer):
                if neighbor in blocked_set or neighbor in closed:
                    continue
                tentative = cost + 1
                if tentative < g_score.get(neighbor, 1 << 30):
                    g_score[neighbor] = tentative
                    came_from[neighbor] = current
                    heapq.heappush(
                        open_heap,
                        (tentative + manhattan(neighbor, target), tentative, neighbor),
                    )
        self.failures += 1
        return None

    @staticmethod
    def _reconstruct(came_from: Dict[Cell, Optional[Cell]], end: Cell) -> List[Cell]:
        path: List[Cell] = []
        node: Optional[Cell] = end
        while node is not None:
            path.append(node)
            node = came_from[node]
        path.reverse()
        return path[1:]  # drop the start cell

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    def path_exists(self, start: Cell, goal: Cell, blocked: Optional[Iterable[Cell]] = None,
                    profile: Optional[MobilityProfile] = None, layer: str = GROUND) -> bool:
        return self.find_path(start, goal, blocked=blocked, profile=profile, layer=layer) is not None

    def distance(
        self, start: Cell, goal: Cell, blocked: Optional[Iterable[Cell]] = None,
        profile: Optional[MobilityProfile] = None, layer: str = GROUND,
    ) -> Optional[int]:
        path = self.find_path(start, goal, blocked=blocked, profile=profile, layer=layer)
        return None if path is None else len(path)

    def best_cell_in_zone(
        self,
        zone_cells: Iterable[Cell],
        origin: Cell,
        blocked: Optional[Iterable[Cell]] = None,
        prefer: Optional[Set[Cell]] = None,
        profile: Optional[MobilityProfile] = None,
        layer: str = GROUND,
    ) -> Optional[Cell]:
        """Pick the reachable cell of a zone that is cheapest to drive to.

        ``prefer`` cells are considered first (used to avoid stacking two boxes
        on the same drop point).
        """
        blocked_set = set(blocked or ())
        candidates = list(zone_cells)
        if prefer:
            preferred = [c for c in candidates if c in prefer]
            if preferred:
                candidates = preferred
        candidates.sort(key=lambda c: manhattan(c, origin))
        for cell in candidates:
            if cell in blocked_set:
                continue
            if cell == origin or self.find_path(origin, cell, blocked=blocked_set, allow_goal_adjacent=False,
                                                profile=profile, layer=layer) is not None:
                return cell
        return None

    def stats(self) -> Dict[str, int]:
        return {
            "paths_computed": self.paths_computed,
            "replans": self.replans,
            "failures": self.failures,
        }
```

- [ ] **Step 7: Run the new tests with the layout tests**

Run: `.venv/bin/python -m pytest -o addopts="" -q backend/test_embodiment.py backend/test_layout_distribution_center.py backend/test_layouts.py backend/test_layout_classic_golden.py`
Expected: `32 passed`.

- [ ] **Step 8: Run the full suite**

Run: `.venv/bin/python -m pytest -o addopts="" -q`
Expected: `2 failed, 409 passed` (only the two pre-existing failures).

- [ ] **Step 9: Commit**

```bash
git add backend/embodiment.py backend/models.py backend/warehouse.py backend/navigation.py \
        backend/test_embodiment.py
git commit -m "feat: mobility profiles and per-profile routing

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Robots move by their catalog body on the new floor

A robot's profile comes from its bound asset's model on the new floor (spec §5.1 "Where the profile comes from"), and spawn, planning, validation, routing, the deadlock sidestep and stepping all use it. `ROBOT_CLASS_PRESETS` gains `ARM` and `HUMANOID`, each keeping `CHARGE_ROBOT`. On classic, `robot.mobility` stays `None`, so nothing moves (§17 step 1 item 4, second half).

**Files:**
- Modify: `backend/models.py` (`ROBOT_CLASS_PRESETS["ARM"]`, `["HUMANOID"]`)
- Modify: `backend/robot.py` (`mobility`, `layer`; loaded slowdown in `ready_to_step`; `to_dict`)
- Modify: `backend/fleet_bridge.py` (`model_profile`, `floor_profile`; binding and the change listener set `robot.mobility`)
- Modify: `backend/digital_twin.py` (`add_robot` spawns by body; `_model_for`, `_spawn_cell`; `reset_robot`)
- Modify: `backend/simulator.py` (`_plan_path`, `_break_deadlock`)
- Modify: `backend/task_planner.py` (`resolve_target` takes a profile; `plan` and `estimate_battery` pass the robot's)
- Modify: `backend/task_manager.py` (`validate`, `_primary_target`, `_score_candidates`)
- Test: `backend/test_robot_mobility.py` (create)

**Interfaces:**
- Consumes (Task 4): `MobilityProfile`, `MobilityProfile.from_model`, `.is_air`, `.is_fixed`, `.speed_cells_s`, `.slows_when_loaded`, `embodiment.GROUND`; `Warehouse.passable`, `.nearest_walkable(..., profile=, layer=)`, `.neighbors(cell, profile, layer)`, `.fixed_stations` (Task 2); `NavigationEngine` profile/layer parameters; the `distribution_center` inventory profile (Task 3).
- Produces:
  - `ROBOT_CLASS_PRESETS["ARM"]` (speed 0.7, `allowed_task_types == ["CHARGE_ROBOT"]`) and `["HUMANOID"]` (speed 0.8; PICK_AND_DELIVER, PICK_BOX, DELIVER_BOX, MOVE_BOX, MOVE_ROBOT, CHARGE_ROBOT). Plan 1b adds the new job types to every class.
  - `Robot.mobility: Optional[MobilityProfile]` (None on classic) and `Robot.layer: str = "GROUND"` (the layer it occupies and routes on; Task 6 adds take-off). `Robot.to_dict()["mobility"]` is the profile dict or None. A carrying robot whose `mobility.slows_when_loaded` steps at `speed × CONFIG["LOADED_SPEED_FACTOR"]`.
  - `FleetBridge.model_profile(model_code: str) -> MobilityProfile`; `FleetBridge.floor_profile(model_code: str) -> Optional[MobilityProfile]` (None on classic). `bind_new_robot` sets `robot.mobility`; every ROBOT_ASSET change to a bound robot refreshes it.
  - `DigitalTwin.add_robot(...)`: same signature. On a non-classic floor the speed comes from `mobility.speed_cells_s` unless `speed` is given; drones default to `drone_pad`, others to `parking_area`; an ARM goes on a free `fixed_stations` cell (or the one given) and raises `ValueError` on a floor with none ("... has no station for it"), off a station ("... is not a fixed station ...") or on a taken one ("... already taken").
  - `DigitalTwin._model_for(robot_class, model_code, asset_id) -> Optional[str]`; `DigitalTwin._spawn_cell(position, body, mobility) -> Cell`.
  - `TaskPlanner.resolve_target(spec, origin, blocked=None, prefer_free=False, profile=None, layer=GROUND)`.
  - `TaskManager._primary_target(task, origin, profile=None, layer=GROUND)`.

- [ ] **Step 1: Write the failing tests**

**Create** `backend/test_robot_mobility.py`:

```python
"""Robots move by their body on the new floor: the profile comes from the
bound asset's model, and spawn, planning, routing and stepping all use it
(multi-embodiment spec §5.1–§5.2). Classic robots are untouched."""
import pytest

from backend.digital_twin import DigitalTwin
from backend.models import CONFIG, ROBOT_CLASS_PRESETS, RobotStatus, SimulationStatus, TaskStatus
from backend.simulator import Simulator


def make_twin(tmp_path, layout="distribution_center"):
    return DigitalTwin(log_dir=str(tmp_path / "logs"), data_dir=str(tmp_path / "data"),
                       persist_logs=False, demo=True, demo_tasks=False, layout=layout)


@pytest.fixture
def twin(tmp_path):
    return make_twin(tmp_path)


@pytest.fixture
def sim(twin):
    simulator = Simulator(twin)
    twin.simulation_status = SimulationStatus.RUNNING
    return simulator


def run_until(sim, predicate, max_ticks=2000):
    for _ in range(max_ticks):
        if predicate():
            return
        sim.tick()
    assert predicate(), f"condition not met within {max_ticks} ticks"


def test_the_new_classes_can_still_charge_themselves():
    for robot_class in ("ARM", "HUMANOID"):
        assert "CHARGE_ROBOT" in ROBOT_CLASS_PRESETS[robot_class]["allowed_task_types"]


def test_a_robot_takes_its_profile_and_speed_from_its_asset_model(twin):
    forklift = twin.add_robot(name="PF1200-205", asset_id="AST-000205", position=(10, 3))
    assert forklift.robot_class == "FORKLIFT"
    assert (forklift.mobility.movement, forklift.mobility.clearance) == ("GROUND", "WIDE")
    assert forklift.speed == pytest.approx(0.8)  # 1.2 m/s over 1.5 m cells, not the preset's 1.2
    amr = twin.add_robot(name="TR50-201", asset_id="AST-000201")
    assert amr.mobility.clearance == "NARROW" and amr.speed == pytest.approx(1.0)
    assert twin.warehouse.zone_of_cell(amr.position).key == "parking_area"
    humanoid = twin.add_robot(name="H1-212", asset_id="AST-000212", position=(22, 7))
    assert humanoid.robot_class == "HUMANOID" and humanoid.mobility.supervision == "humanoid_supervision"
    assert twin.add_robot(name="Quick", asset_id="AST-000102", speed=3.0).speed == 3.0  # explicit wins
    assert forklift.to_dict()["mobility"]["clearance"] == "WIDE"


def test_classic_robots_have_no_profile_and_keep_their_preset_speed(tmp_path):
    classic = make_twin(tmp_path, layout="classic")
    assert all(robot.mobility is None for robot in classic.robots.values())
    forklift = classic.add_robot(name="Fork", robot_class="FORKLIFT")
    assert forklift.mobility is None and forklift.speed == 1.2
    assert forklift.to_dict()["mobility"] is None


def test_a_drone_spawns_on_its_pad(twin):
    drone = twin.add_robot(name="IX2-208", asset_id="AST-000208")
    assert twin.warehouse.cell_type(*drone.position).value == "DRONE_PAD"
    assert drone.layer == "GROUND" and drone.mobility.is_air
    elsewhere = twin.add_robot(name="IX2-209", asset_id="AST-000209", position=(10, 3))
    assert elsewhere.position in twin.warehouse.zones["drone_pad"].cells  # snapped to the pad


def test_an_arm_is_placed_on_its_station_and_nowhere_else(twin, tmp_path):
    arm = twin.add_robot(name="CX10-210", asset_id="AST-000210")
    assert arm.position == (23, 14) and arm.mobility.is_fixed
    second = twin.add_robot(name="CX10-211", asset_id="AST-000211", position=(22, 16))
    assert second.position == (22, 16)
    with pytest.raises(ValueError, match="already taken"):
        twin.add_robot(name="Spare", model_code="FB-CX10", position=(23, 14))
    with pytest.raises(ValueError, match="not a fixed station"):
        twin.add_robot(name="Loose", model_code="FB-CX10", position=(10, 3))
    with pytest.raises(ValueError, match="no station"):
        make_twin(tmp_path / "classic", layout="classic").add_robot(name="Arm", model_code="FB-CX10")


def test_a_forklift_drives_only_wide_cells(twin, sim):
    forklift = twin.add_robot(name="PF1200-205", asset_id="AST-000205", position=(10, 3))
    task = twin.tasks.create_task({"type": "MOVE_ROBOT", "robot_id": forklift.id,
                                   "destination": "outbound_staging"})
    visited = set()

    def arrived():
        visited.add(forklift.position)
        return task.is_terminal

    run_until(sim, arrived)
    assert task.status is TaskStatus.COMPLETED, task.error
    assert twin.warehouse.zone_of_cell(forklift.position).key == "outbound_staging"
    assert all(twin.warehouse.clearance(cell) == "WIDE" for cell in visited)
    assert visited & {(17, 10), (17, 11)}


def test_a_forklift_cannot_be_sent_down_a_tote_aisle(twin):
    forklift = twin.add_robot(name="PF1200-205", asset_id="AST-000205", position=(10, 3))
    task = twin.tasks.create_task({"type": "MOVE_ROBOT", "robot_id": forklift.id, "destination": "tote_aisle_1"})
    assert task.status is TaskStatus.FAILED and "not reachable" in task.error
    amr = twin.add_robot(name="TR50-201", asset_id="AST-000201")
    ok = twin.tasks.create_task({"type": "MOVE_ROBOT", "robot_id": amr.id, "destination": "tote_aisle_1"})
    assert ok.status is TaskStatus.PLANNING


def test_a_loaded_forklift_travels_slower(twin):
    forklift = twin.add_robot(name="PF1200-205", asset_id="AST-000205", position=(10, 3))
    forklift.move_accumulator = 0.0
    forklift.ready_to_step(0.5)
    empty = forklift.move_accumulator
    forklift.move_accumulator, forklift.carrying_box = 0.0, "box_999"
    forklift.ready_to_step(0.5)
    assert forklift.move_accumulator == pytest.approx(empty * CONFIG["LOADED_SPEED_FACTOR"])
    amr = twin.add_robot(name="TR50-201", asset_id="AST-000201")
    amr.move_accumulator, amr.carrying_box = 0.0, "box_998"
    amr.ready_to_step(0.5)
    assert amr.move_accumulator == pytest.approx(amr.speed * 0.5)  # only forklifts and haulers slow down


def test_reset_sends_a_robot_home_by_its_own_profile(twin):
    forklift = twin.add_robot(name="PF1200-205", asset_id="AST-000205", position=(7, 1))
    blocker = twin.add_robot(name="PF1200-206", asset_id="AST-000206", position=(7, 5))
    blocker.position = forklift.home  # someone parked on its home cell
    forklift.position = (10, 3)
    twin.reset_robot(forklift.id)
    # (8,1) is the nearest free drivable cell, but it is NARROW; (7,2) is the nearest WIDE one.
    assert forklift.position == (7, 2) and forklift.status is RobotStatus.IDLE
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/bin/python -m pytest -o addopts="" -q backend/test_robot_mobility.py`
Expected: `9 failed` — among the errors, `KeyError: 'ARM'` and `AttributeError: 'Robot' object has no attribute 'mobility'`.

- [ ] **Step 3: The two new robot classes**

**Replace in** `backend/models.py`:

```python
    "PICKER": {
        "label": "Picker (fast, pick & deliver only)",
        "speed": 2.5,
        "allowed_task_types": [
            "PICK_AND_DELIVER", "PICK_BOX", "DELIVER_BOX", "MOVE_BOX", "CHARGE_ROBOT",
        ],
    },
}
```

with:

```python
    "PICKER": {
        "label": "Picker (fast, pick & deliver only)",
        "speed": 2.5,
        "allowed_task_types": [
            "PICK_AND_DELIVER", "PICK_BOX", "DELIVER_BOX", "MOVE_BOX", "CHARGE_ROBOT",
        ],
    },
    # Fixed pack-cell arm: it never drives, and being mains-powered it never
    # actually needs CHARGE_ROBOT — but the rule above still applies. Only a
    # floor with fixed stations (distribution_center) can hold one.
    "ARM": {
        "label": "Arm (fixed pack cell)",
        "speed": 0.7,
        "allowed_task_types": ["CHARGE_ROBOT"],
    },
    "HUMANOID": {
        "label": "Humanoid (supervised, general purpose)",
        "speed": 0.8,
        "allowed_task_types": [
            "PICK_AND_DELIVER", "PICK_BOX", "DELIVER_BOX", "MOVE_BOX",
            "MOVE_ROBOT", "CHARGE_ROBOT",
        ],
    },
}
```

- [ ] **Step 4: The robot carries its body**

**Replace in** `backend/robot.py`:

```python
from typing import Any, Dict, List, Optional, Sequence

from .models import (
```

with:

```python
from typing import Any, Dict, List, Optional, Sequence

from .embodiment import GROUND, MobilityProfile
from .models import (
```

**Replace in** `backend/robot.py`:

```python
        # The asset lifecycle status that made the fleet bridge stop this
        # robot (e.g. MAINTENANCE), so it only ever resumes robots it stopped.
        self.fleet_hold: Optional[str] = None
```

with:

```python
        # The asset lifecycle status that made the fleet bridge stop this
        # robot (e.g. MAINTENANCE), so it only ever resumes robots it stopped.
        self.fleet_hold: Optional[str] = None
        # What this robot's body lets it do (backend/embodiment.py), from its
        # bound asset's catalog model. Set by the fleet bridge on a layered
        # floor; None on classic, where every robot drives as it always has.
        self.mobility: Optional[MobilityProfile] = None
        # The layer the robot occupies and routes on. Only drones leave GROUND.
        self.layer: str = GROUND
```

**Replace in** `backend/robot.py`:

```python
    def ready_to_step(self, dt: float) -> bool:
        """Accumulate travel credit; returns True when one cell may be traversed."""
        self.move_accumulator += self.speed * dt
```

with:

```python
    def ready_to_step(self, dt: float) -> bool:
        """Accumulate travel credit; returns True when one cell may be traversed.
        A loaded forklift or heavy hauler travels at LOADED_SPEED_FACTOR."""
        speed = self.speed
        if self.carrying_box and self.mobility is not None and self.mobility.slows_when_loaded:
            speed *= CONFIG["LOADED_SPEED_FACTOR"]
        self.move_accumulator += speed * dt
```

**Replace in** `backend/robot.py`:

```python
            "fleet_hold": self.fleet_hold,
            # Raw baselines, for save/load round-tripping...
```

with:

```python
            "fleet_hold": self.fleet_hold,
            "mobility": self.mobility.to_dict() if self.mobility is not None else None,
            # Raw baselines, for save/load round-tripping...
```

- [ ] **Step 5: The fleet bridge derives the body from the bound model**

**Replace in** `backend/fleet_bridge.py`:

```python
from .inventory import InventoryService, NotFound, reseed
from .inventory.catalog_data import CLASS_DEFAULT_MODELS
```

with:

```python
from .embodiment import MobilityProfile
from .inventory import InventoryService, NotFound, reseed
from .inventory.catalog_data import CLASS_DEFAULT_MODELS
```

**Replace in** `backend/fleet_bridge.py`:

```python
    # ---- binding -------------------------------------------------------- #
    def robot_for_asset(self, asset_id: Optional[str]) -> Optional[Any]:
```

with:

```python
    # ---- embodiment ------------------------------------------------------ #
    def model_profile(self, model_code: str) -> MobilityProfile:
        """The body a robot of catalog model `model_code` has (spec §5.1)."""
        model = self.service.get_model(model_code)
        return MobilityProfile.from_model(model["spec"], model["embodiment_class"])

    def floor_profile(self, model_code: str) -> Optional[MobilityProfile]:
        """The profile a robot of `model_code` moves by on this twin's floor:
        None on classic, where every robot keeps today's behaviour."""
        if self.twin.warehouse.layout_name == "classic":
            return None
        return self.model_profile(model_code)

    # ---- binding -------------------------------------------------------- #
    def robot_for_asset(self, asset_id: Optional[str]) -> Optional[Any]:
```

**Replace in** `backend/fleet_bridge.py`:

```python
        robot.asset_id = record["asset_id"]
        reported = record["reported"] or {}
```

with:

```python
        robot.asset_id = record["asset_id"]
        robot.mobility = self.floor_profile(record["model_code"])
        reported = record["reported"] or {}
```

**Replace in** `backend/fleet_bridge.py`:

```python
        if change["aggregate_type"] == "ROBOT_ASSET":
            if robot is not None:  # UPDATING is derived from the jobs, never hand-set per job
                installing = any(j["state"] == "INSTALLING" for j in change["after"]["active_ota_jobs"])
```

with:

```python
        if change["aggregate_type"] == "ROBOT_ASSET":
            if robot is not None:
                # The body follows the asset's catalog model on every asset change.
                robot.mobility = self.floor_profile(change["after"]["model_code"])
                # UPDATING is derived from the jobs, never hand-set per job.
                installing = any(j["state"] == "INSTALLING" for j in change["after"]["active_ota_jobs"])
```

- [ ] **Step 6: The twin spawns a robot by its body**

`add_robot` now resolves the class before choosing the spawn cell, because the spawn depends on the body. The class resolution itself is unchanged.

**Replace in** `backend/digital_twin.py`:

```python
from .event_system import EventSystem
from .fleet_bridge import FleetBridge
```

with:

```python
from .event_system import EventSystem
from .embodiment import MobilityProfile
from .fleet_bridge import FleetBridge
```

**Replace in** `backend/digital_twin.py`:

```python
            candidate = position or self.warehouse.resolve_zone("parking_area").cells[0]
            if not self.warehouse.is_inside(*candidate):
                raise ValueError(f"Position {candidate} is outside the warehouse")
            occupied = set(self.robot_cells().keys())
            spawn = candidate if (self.warehouse.is_walkable(*candidate) and candidate not in occupied) \
                else self.warehouse.nearest_walkable(candidate, occupied)
            if spawn is None:
                raise ValueError("No free drivable cell available for a new robot")
```

with:

```python
            if position is not None and not self.warehouse.is_inside(*position):
                raise ValueError(f"Position {position} is outside the warehouse")
```

**Replace in** `backend/digital_twin.py`:

```python
            preset = ROBOT_CLASS_PRESETS[normalized_class]
            effective_speed = speed if speed is not None else preset.get("speed")
            effective_allowed = allowed_task_types if allowed_task_types else preset.get("allowed_task_types")
```

with:

```python
            preset = ROBOT_CLASS_PRESETS[normalized_class]

            # The robot's body (backend/embodiment.py), read from its catalog
            # model, decides where it may stand. On a layered floor it also
            # decides how the robot routes and how fast it drives; on classic
            # the robot keeps its preset speed and today's routing.
            model = self._model_for(normalized_class, model_code, asset_id)
            body = self.fleet.model_profile(model) if model else None
            mobility = body if self.layout_name != "classic" else None
            spawn = self._spawn_cell(position, body, mobility)

            if speed is not None:
                effective_speed = speed
            else:
                effective_speed = mobility.speed_cells_s if mobility is not None else preset.get("speed")
            effective_allowed = allowed_task_types if allowed_task_types else preset.get("allowed_task_types")
```

**Replace in** `backend/digital_twin.py`:

```python
    def set_robot_capabilities(self, robot_id: str, allowed_task_types: Optional[List[str]]) -> Robot:
```

with:

```python
    def _model_for(self, robot_class: str, model_code: Optional[str], asset_id: Optional[str]) -> Optional[str]:
        """The catalog model a new robot will be bound as (see FleetBridge.bind_new_robot)."""
        if model_code:
            return model_code
        if asset_id and self.inventory.has_asset(asset_id):
            return self.inventory.get_robot(asset_id)["model_code"]
        return CLASS_DEFAULT_MODELS.get(robot_class)

    def _spawn_cell(self, position: Optional[Cell], body: Optional[MobilityProfile],
                    mobility: Optional[MobilityProfile]) -> Cell:
        """Where a new robot starts. Fixed equipment goes on a free station
        cell of its own; anything else on the requested (or default) cell, or
        the nearest free cell its floor profile may use."""
        occupied = set(self.robot_cells())
        if body is not None and body.is_fixed:
            stations = self.warehouse.fixed_stations
            if not stations:
                raise ValueError(f"A {body.embodiment_class} is fixed equipment and the "
                                 f"{self.layout_name} floor has no station for it")
            cell = tuple(position) if position else next((c for c in stations if c not in occupied), None)
            if cell is None:
                raise ValueError("Every fixed station is already taken")
            if cell not in stations:
                raise ValueError(f"({cell[0]},{cell[1]}) is not a fixed station (stations: {sorted(stations)})")
            if cell in occupied:
                raise ValueError(f"The station at ({cell[0]},{cell[1]}) is already taken")
            return cell
        home = "drone_pad" if mobility is not None and mobility.is_air else "parking_area"
        candidate = position or self.warehouse.resolve_zone(home).cells[0]
        if self.warehouse.passable(candidate, mobility) and candidate not in occupied:
            return candidate
        spawn = self.warehouse.nearest_walkable(candidate, occupied, profile=mobility)
        if spawn is None:
            raise ValueError("No free drivable cell available for a new robot")
        return spawn

    def set_robot_capabilities(self, robot_id: str, allowed_task_types: Optional[List[str]]) -> Robot:
```

**Replace in** `backend/digital_twin.py`:

```python
        home = robot.home if robot.home not in occupied else self.warehouse.nearest_walkable(
            robot.home, occupied
        )
```

with:

```python
        home = robot.home if robot.home not in occupied else self.warehouse.nearest_walkable(
            robot.home, occupied, profile=robot.mobility
        )
```

- [ ] **Step 7: The simulator routes and sidesteps by the robot's body**

**Replace in** `backend/simulator.py`:

```python
        path = twin.navigation.find_path(
            robot.position, target, blocked=blocked, allow_goal_adjacent=False, is_replan=replan
        )
        if path is None:
            # Is the route only blocked by robots, or genuinely impossible?
            static_path = twin.navigation.find_path(
                robot.position, target, allow_goal_adjacent=False
            )
```

with:

```python
        path = twin.navigation.find_path(
            robot.position, target, blocked=blocked, allow_goal_adjacent=False, is_replan=replan,
            profile=robot.mobility, layer=robot.layer,
        )
        if path is None:
            # Is the route only blocked by robots, or genuinely impossible?
            static_path = twin.navigation.find_path(
                robot.position, target, allow_goal_adjacent=False,
                profile=robot.mobility, layer=robot.layer,
            )
```

**Replace in** `backend/simulator.py`:

```python
            for cell in twin.warehouse.neighbors(robot.position)
            if cell not in occupied and cell != blocker.position
```

with:

```python
            for cell in twin.warehouse.neighbors(robot.position, robot.mobility, robot.layer)
            if cell not in occupied and cell != blocker.position
```

- [ ] **Step 8: The planner resolves every target for the robot's body**

**Replace in** `backend/task_planner.py`:

```python
from typing import Any, Dict, List, Optional, Set, Tuple

from .models import (
```

with:

```python
from typing import Any, Dict, List, Optional, Set, Tuple

from .embodiment import GROUND, MobilityProfile
from .models import (
```

**Replace in** `backend/task_planner.py`:

```python
        blocked: Optional[Set[Cell]] = None,
        prefer_free: bool = False,
    ) -> Tuple[Cell, str]:
        """Turn a location specification into a concrete grid cell."""
```

with:

```python
        blocked: Optional[Set[Cell]] = None,
        prefer_free: bool = False,
        profile: Optional[MobilityProfile] = None,
        layer: str = GROUND,
    ) -> Tuple[Cell, str]:
        """Turn a location specification into a concrete grid cell that a robot
        with `profile` can use on `layer` (no profile: any drivable cell)."""
```

**Replace in** `backend/task_planner.py`:

```python
            resolved = cell if warehouse.is_walkable(*cell) else warehouse.nearest_walkable(cell, blocked)
```

with:

```python
            resolved = cell if warehouse.passable(cell, profile, layer) \
                else warehouse.nearest_walkable(cell, blocked, profile=profile, layer=layer)
```

**Replace in** `backend/task_planner.py`:

```python
            chosen = nav.best_cell_in_zone(zone.cells, origin, blocked=blocked, prefer=prefer)
```

with:

```python
            chosen = nav.best_cell_in_zone(zone.cells, origin, blocked=blocked, prefer=prefer,
                                           profile=profile, layer=layer)
```

**Replace in** `backend/task_planner.py`:

```python
            distance = nav.distance(origin, waypoint)
```

with:

```python
            distance = nav.distance(origin, waypoint, profile=robot.mobility, layer=robot.layer)
```

**Replace in** `backend/task_planner.py`:

```python
        blocked = self.twin.other_robot_cells(robot.id)
        actions: List[Action] = []
```

with:

```python
        blocked = self.twin.other_robot_cells(robot.id)
        # Every target is resolved for this robot's own body and layer.
        route = {"profile": robot.mobility, "layer": robot.layer}
        actions: List[Action] = []
```

Now add `**route` to each of the twelve `resolve_target` calls in `plan()`, in six edits:

**Replace every** `backend/task_planner.py` (4 occurrences):

```python
self.resolve_target(box.id, robot.position, blocked)
```

with:

```python
self.resolve_target(box.id, robot.position, blocked, **route)
```

**Replace every** `backend/task_planner.py` (3 occurrences):

```python
task.destination, pick_cell, blocked, prefer_free=True
```

with:

```python
task.destination, pick_cell, blocked, prefer_free=True, **route
```

**Replace every** `backend/task_planner.py` (2 occurrences):

```python
self.resolve_target(task.destination, robot.position, blocked)
```

with:

```python
self.resolve_target(task.destination, robot.position, blocked, **route)
```

**Replace in** `backend/task_planner.py`:

```python
                    task.destination, robot.position, blocked, prefer_free=True
```

with:

```python
                    task.destination, robot.position, blocked, prefer_free=True, **route
```

**Replace in** `backend/task_planner.py`:

```python
            cell, label = self.resolve_target("charging_station", robot.position, blocked)
```

with:

```python
            cell, label = self.resolve_target("charging_station", robot.position, blocked, **route)
```

**Replace in** `backend/task_planner.py`:

```python
                charge_cell, charge_label = self.resolve_target(
                    "charging_station", robot.position, blocked
                )
```

with:

```python
                charge_cell, charge_label = self.resolve_target(
                    "charging_station", robot.position, blocked, **route
                )
```

Check: `grep -c "\*\*route" backend/task_planner.py` prints `12`.

- [ ] **Step 9: The task gate checks a named robot's targets for its body**

**Replace in** `backend/task_manager.py`:

```python
from .eligibility import agent_eligibility, operator_eligibility, robot_eligibility
```

with:

```python
from .eligibility import agent_eligibility, operator_eligibility, robot_eligibility
from .embodiment import GROUND
```

**Replace in** `backend/task_manager.py`:

```python
        if needs_destination:
            if not task.destination:
                return False, "This task type needs a destination"
            origin = robot.position if robot else next(iter(twin.robots.values())).position
            try:
                planner.resolve_target(task.destination, origin)
            except PlanningError as exc:
                return False, str(exc)

        # Source (optional, but if given it must exist)
        if task.source:
            try:
                origin = robot.position if robot else next(iter(twin.robots.values())).position
                planner.resolve_target(task.source, origin)
            except PlanningError as exc:
                return False, f"Invalid source: {exc}"

        # Reachability
        if task.type != TaskType.CHARGE_ROBOT and robot is not None:
            try:
                target_cell, _ = self._primary_target(task, robot.position)
            except PlanningError as exc:
                return False, str(exc)
            if target_cell is not None and not twin.navigation.path_exists(robot.position, target_cell):
```

with:

```python
        # A named robot's targets are resolved for its own body and layer.
        route: Dict[str, Any] = {"profile": robot.mobility, "layer": robot.layer} if robot else {}
        if needs_destination:
            if not task.destination:
                return False, "This task type needs a destination"
            origin = robot.position if robot else next(iter(twin.robots.values())).position
            try:
                planner.resolve_target(task.destination, origin, **route)
            except PlanningError as exc:
                return False, str(exc)

        # Source (optional, but if given it must exist)
        if task.source:
            try:
                origin = robot.position if robot else next(iter(twin.robots.values())).position
                planner.resolve_target(task.source, origin, **route)
            except PlanningError as exc:
                return False, f"Invalid source: {exc}"

        # Reachability
        if task.type != TaskType.CHARGE_ROBOT and robot is not None:
            try:
                target_cell, _ = self._primary_target(task, robot.position, **route)
            except PlanningError as exc:
                return False, str(exc)
            if target_cell is not None and not twin.navigation.path_exists(robot.position, target_cell, **route):
```

**Replace in** `backend/task_manager.py`:

```python
    def _primary_target(self, task: Task, origin: Cell) -> Tuple[Optional[Cell], str]:
        planner = self.twin.planner
        if task.type in (TaskType.PICK_AND_DELIVER, TaskType.PICK_BOX, TaskType.MOVE_BOX):
            return planner.resolve_target(task.box_id, origin)
        if task.type == TaskType.BATCH_DELIVER:
            return planner.resolve_target(task.box_ids[0], origin) if task.box_ids else (None, "")
        if task.type == TaskType.DELIVER_BOX:
            robot = self.twin.find_robot(task.robot_id) if task.robot_id else None
            if robot is not None and robot.carrying_box == task.box_id:
                return planner.resolve_target(task.destination, origin)
            return planner.resolve_target(task.box_id, origin)
        if task.type in (TaskType.MOVE_ROBOT, TaskType.MIXED_MAINTENANCE_MISSION):
            return planner.resolve_target(task.destination, origin)
        if task.type == TaskType.CHARGE_ROBOT:
            return planner.resolve_target("charging_station", origin)
        return None, ""
```

with:

```python
    def _primary_target(self, task: Task, origin: Cell, profile: Optional[Any] = None,
                        layer: str = GROUND) -> Tuple[Optional[Cell], str]:
        planner = self.twin.planner
        route = {"profile": profile, "layer": layer}
        if task.type in (TaskType.PICK_AND_DELIVER, TaskType.PICK_BOX, TaskType.MOVE_BOX):
            return planner.resolve_target(task.box_id, origin, **route)
        if task.type == TaskType.BATCH_DELIVER:
            return planner.resolve_target(task.box_ids[0], origin, **route) if task.box_ids else (None, "")
        if task.type == TaskType.DELIVER_BOX:
            robot = self.twin.find_robot(task.robot_id) if task.robot_id else None
            if robot is not None and robot.carrying_box == task.box_id:
                return planner.resolve_target(task.destination, origin, **route)
            return planner.resolve_target(task.box_id, origin, **route)
        if task.type in (TaskType.MOVE_ROBOT, TaskType.MIXED_MAINTENANCE_MISSION):
            return planner.resolve_target(task.destination, origin, **route)
        if task.type == TaskType.CHARGE_ROBOT:
            return planner.resolve_target("charging_station", origin, **route)
        return None, ""
```

**Replace in** `backend/task_manager.py`:

```python
                distance = twin.navigation.distance(robot.position, target_cell)
```

with:

```python
                distance = twin.navigation.distance(robot.position, target_cell,
                                                    profile=robot.mobility, layer=robot.layer)
```

- [ ] **Step 10: Run the new tests with the fleet-bridge tests**

`test_fleet_bridge.py::test_add_robot_commissions_an_asset_for_its_class` still expects `add_robot(model_code="FB-CX10")` to raise on classic; it now raises because classic has no fixed station (Plan ruling 3).

Run: `.venv/bin/python -m pytest -o addopts="" -q backend/test_robot_mobility.py backend/test_fleet_bridge.py`
Expected: `39 passed`.

- [ ] **Step 11: Run the full suite**

Run: `.venv/bin/python -m pytest -o addopts="" -q`
Expected: `2 failed, 418 passed` (only the two pre-existing failures).

- [ ] **Step 12: Commit**

```bash
git add backend/models.py backend/robot.py backend/fleet_bridge.py backend/digital_twin.py \
        backend/simulator.py backend/task_planner.py backend/task_manager.py backend/test_robot_mobility.py
git commit -m "feat: robots move by their catalog body on the new floor

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Ground and air layers with layer-aware traffic

Robots gain `altitude_m` beside Task 5's `layer`, and every position comparison — `other_robot_cells`, `_blocking_robot`, `_detect_collisions`, `register_collision`'s inputs and CI's `_check_collisions` — compares `(layer, x, y)` (spec §5.3). Drones get a way onto `AIR`: `DigitalTwin.set_robot_layer`, which takes off and lands only on the drone pad (§5.2). The TAKEOFF / LAND job steps with their durations come in plan 1b (§17 step 1 item 5).

**Files:**
- Modify: `backend/embodiment.py` (`HOVER_CLEARANCE_M`)
- Modify: `backend/robot.py` (`altitude_m`; `layer` and `altitude_m` in `to_dict`/`from_dict`)
- Modify: `backend/digital_twin.py` (`robot_cells(layer)`, `other_robot_cells(..., layer)`, ground-only spawn occupancy, `set_robot_layer`, `reset_robot` lands, `register_collision`)
- Modify: `backend/simulator.py` (`_blocking_robot`, `_detect_collisions`)
- Modify: `backend/ci_engine.py` (`_check_collisions`)
- Test: `backend/test_layers.py` (create)

**Interfaces:**
- Consumes (Tasks 4–5): `embodiment.AIR`, `GROUND`, `LAYERS`; `MobilityProfile.is_air`, `.max_lift_m`; `Robot.mobility`, `Robot.layer`; `Warehouse.passable(cell, profile, AIR)`; `CellType.DRONE_PAD`; `DigitalTwin._spawn_cell`.
- Produces:
  - `embodiment.HOVER_CLEARANCE_M = 0.5` (a drone's altitude is its working level's height plus this; the default right after take-off).
  - `Robot.altitude_m: float = 0.0`; `Robot.to_dict()` has `"layer"` and `"altitude_m"`; `Robot.from_dict` reads them (defaults `"GROUND"`, `0.0`).
  - `DigitalTwin.robot_cells(layer: Optional[str] = None) -> Dict[Cell, str]` (None = every layer).
  - `DigitalTwin.other_robot_cells(exclude_id, include_reservations=True, layer: Optional[str] = None) -> Set[Cell]` — only robots on `layer`, defaulting to the excluded robot's own layer.
  - `DigitalTwin.set_robot_layer(robot_id: str, layer: str, altitude_m: Optional[float] = None) -> Robot` — `KeyError` for an unknown robot; `ValueError` for an unknown layer, a body that cannot fly ("... cannot fly"), a take-off or landing off the pad ("... can only take off/land on the drone pad ..."), an occupied target cell, or an altitude outside `(0, max_lift_m]`. Setting GROUND on a grounded robot is a no-op; a layer change clears the robot's path.
  - `DigitalTwin.register_collision(a, b)` raises `ValueError("... are on different layers and cannot collide")` for robots on different layers; its `COLLISION_DETECTED` event data gains `"layer"`.
  - `DigitalTwin.reset_robot` puts the robot back on `GROUND` at altitude 0.
  - CI's collision message for a non-ground clash ends `" on the AIR layer"`.

- [ ] **Step 1: Write the failing tests**

**Create** `backend/test_layers.py`:

```python
"""Layers and layer-aware traffic (multi-embodiment spec §5.3): a drone on
AIR over a ground robot is neither a blocker nor a collision."""
import pytest

from backend.ci_engine import CIEngine
from backend.digital_twin import DigitalTwin
from backend.models import RobotStatus, SimulationStatus, TaskStatus
from backend.robot import Robot
from backend.simulator import Simulator


@pytest.fixture
def twin(tmp_path):
    return DigitalTwin(log_dir=str(tmp_path / "logs"), data_dir=str(tmp_path / "data"),
                       persist_logs=False, demo=True, demo_tasks=False, layout="distribution_center")


@pytest.fixture
def sim(twin):
    simulator = Simulator(twin)
    twin.simulation_status = SimulationStatus.RUNNING
    return simulator


@pytest.fixture
def drone(twin):
    return twin.add_robot(name="IX2-208", asset_id="AST-000208", position=(20, 2))


@pytest.fixture
def amr(twin):
    return twin.add_robot(name="TR50-201", asset_id="AST-000201", position=(10, 3))


def test_a_drone_takes_off_and_lands_only_on_its_pad(twin, drone):
    twin.set_robot_layer(drone.id, "AIR")
    assert (drone.layer, drone.altitude_m) == ("AIR", 0.5)
    twin.set_robot_layer(drone.id, "air", altitude_m=4.5)  # climbing while airborne
    assert drone.altitude_m == 4.5
    drone.position = (12, 2)  # over a rack
    with pytest.raises(ValueError, match="only land on the drone pad"):
        twin.set_robot_layer(drone.id, "GROUND")
    drone.position = (20, 2)
    twin.set_robot_layer(drone.id, "GROUND")
    assert (drone.layer, drone.altitude_m) == ("GROUND", 0.0)
    with pytest.raises(ValueError, match="at most"):
        twin.set_robot_layer(drone.id, "AIR", altitude_m=40.0)
    with pytest.raises(ValueError, match="Unknown layer"):
        twin.set_robot_layer(drone.id, "SPACE")
    with pytest.raises(KeyError):
        twin.set_robot_layer("ghost", "AIR")


def test_only_a_flying_body_leaves_the_ground(twin, amr, tmp_path):
    with pytest.raises(ValueError, match="cannot fly"):
        twin.set_robot_layer(amr.id, "AIR")
    assert twin.set_robot_layer(amr.id, "GROUND") is amr  # already there: a no-op
    classic = DigitalTwin(log_dir=str(tmp_path / "c" / "logs"), data_dir=str(tmp_path / "c" / "data"),
                          persist_logs=False, demo=True, demo_tasks=False)
    classic_drone = classic.add_robot(name="Drone-1", robot_class="DRONE")
    with pytest.raises(ValueError, match="cannot fly"):  # no profile on classic: it drives as before
        classic.set_robot_layer(classic_drone.id, "AIR")


def test_a_drone_over_an_amr_is_not_a_collision(twin, sim, drone, amr):
    twin.set_robot_layer(drone.id, "AIR")
    drone.position = amr.position
    sim.tick()
    assert twin.statistics["collisions"] == 0
    assert drone.status is not RobotStatus.ERROR and amr.status is not RobotStatus.ERROR
    passed, _, _ = CIEngine(twin)._check_collisions()
    assert passed
    with pytest.raises(ValueError, match="different layers"):
        twin.register_collision(drone.id, amr.id)


def test_two_drones_in_one_air_cell_do_collide(twin, sim, drone):
    other = twin.add_robot(name="IX2-209", asset_id="AST-000209", position=(21, 2))
    twin.set_robot_layer(drone.id, "AIR")
    twin.set_robot_layer(other.id, "AIR")
    passed, message, _ = CIEngine(twin)._check_collisions()
    assert passed
    other.position = drone.position
    passed, message, _ = CIEngine(twin)._check_collisions()
    assert not passed and "on the AIR layer" in message
    sim.tick()
    assert twin.statistics["collisions"] == 1
    assert drone.status is RobotStatus.ERROR and other.status is RobotStatus.ERROR
    event = twin.events.query(event_type="COLLISION_DETECTED")[-1]
    assert event["data"]["layer"] == "AIR"


def test_occupancy_and_blocking_are_per_layer(twin, sim, drone, amr):
    twin.set_robot_layer(drone.id, "AIR")
    drone.position = (11, 3)
    assert (11, 3) not in twin.other_robot_cells(amr.id)
    assert (10, 3) not in twin.other_robot_cells(drone.id)
    assert (11, 3) in twin.other_robot_cells(amr.id, layer="AIR")
    assert sim._blocking_robot(amr, (11, 3)) is None
    assert twin.robot_cells("GROUND") == {(10, 3): amr.id}
    assert twin.robot_cells() == {(10, 3): amr.id, (11, 3): drone.id}
    ground_drone = twin.add_robot(name="IX2-209", asset_id="AST-000209", position=(21, 2))
    amr.position = (18, 2)  # next to the pad, both on the ground
    assert sim._blocking_robot(amr, ground_drone.position) is ground_drone


def test_an_amr_drives_under_a_hovering_drone(twin, sim, drone, amr):
    twin.set_robot_layer(drone.id, "AIR")
    drone.position = (11, 3)  # hovering over the AMR's route
    task = twin.tasks.create_task({"type": "MOVE_ROBOT", "robot_id": amr.id, "destination": "13,3"})
    for _ in range(200):
        if task.is_terminal:
            break
        sim.tick()
    assert task.status is TaskStatus.COMPLETED and amr.position == (13, 3)
    assert not twin.events.query(event_type="COLLISION_AVOIDED")
    assert twin.statistics["collisions"] == 0


def test_a_new_robot_may_spawn_under_a_hovering_drone(twin, drone):
    twin.set_robot_layer(drone.id, "AIR")
    drone.position = (4, 12)  # the first parking cell
    amr = twin.add_robot(name="TR50-201", asset_id="AST-000201")
    assert amr.position == (4, 12) and amr.layer == "GROUND"


def test_reset_lands_a_drone(twin, drone):
    twin.set_robot_layer(drone.id, "AIR", altitude_m=3.0)
    drone.position = (12, 2)
    twin.reset_robot(drone.id)
    assert (drone.layer, drone.altitude_m) == ("GROUND", 0.0)
    assert twin.warehouse.cell_type(*drone.position).value == "DRONE_PAD"


def test_layer_and_altitude_round_trip(twin, drone):
    twin.set_robot_layer(drone.id, "AIR", altitude_m=2.5)
    data = drone.to_dict()
    assert (data["layer"], data["altitude_m"]) == ("AIR", 2.5)
    restored = Robot.from_dict(data)
    assert (restored.layer, restored.altitude_m) == ("AIR", 2.5)
    legacy = {key: value for key, value in data.items() if key not in ("layer", "altitude_m")}
    assert (Robot.from_dict(legacy).layer, Robot.from_dict(legacy).altitude_m) == ("GROUND", 0.0)
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/bin/python -m pytest -o addopts="" -q backend/test_layers.py`
Expected: `9 failed` — `AttributeError: 'DigitalTwin' object has no attribute 'set_robot_layer'` (and `'Robot' object has no attribute 'altitude_m'`).

- [ ] **Step 3: Hover clearance**

**Replace in** `backend/embodiment.py`:

```python
#: Classes that travel slower while carrying (× CONFIG["LOADED_SPEED_FACTOR"]).
```

with:

```python
#: A flying drone holds this far above the level it works at (spec §5.3).
HOVER_CLEARANCE_M = 0.5

#: Classes that travel slower while carrying (× CONFIG["LOADED_SPEED_FACTOR"]).
```

- [ ] **Step 4: Altitude on the robot, and both fields saved**

**Replace in** `backend/robot.py`:

```python
        # The layer the robot occupies and routes on. Only drones leave GROUND.
        self.layer: str = GROUND
```

with:

```python
        # The layer the robot occupies and routes on, and its height above the
        # floor. Only drones leave GROUND (DigitalTwin.set_robot_layer); traffic
        # and collisions compare (layer, x, y), so a drone over an AMR is fine.
        self.layer: str = GROUND
        self.altitude_m: float = 0.0
```

**Replace in** `backend/robot.py`:

```python
            "position": cell_dict(self.position),
            "home": cell_dict(self.home),
```

with:

```python
            "position": cell_dict(self.position),
            "layer": self.layer,
            "altitude_m": round(self.altitude_m, 2),
            "home": cell_dict(self.home),
```

**Replace in** `backend/robot.py`:

```python
        robot.fleet_hold = data.get("fleet_hold")
        robot.home = cell_tuple(data.get("home")) or robot.position
```

with:

```python
        robot.fleet_hold = data.get("fleet_hold")
        robot.layer = data.get("layer", GROUND)
        robot.altitude_m = float(data.get("altitude_m", 0.0))
        robot.home = cell_tuple(data.get("home")) or robot.position
```

- [ ] **Step 5: Occupancy per layer, take-off and landing, collisions per layer**

**Replace in** `backend/digital_twin.py`:

```python
from .embodiment import MobilityProfile
```

with:

```python
from .embodiment import AIR, GROUND, HOVER_CLEARANCE_M, LAYERS, MobilityProfile
```

**Replace in** `backend/digital_twin.py`:

```python
    def robot_cells(self) -> Dict[Cell, str]:
        return {r.position: r.id for r in self.robots.values()}

    def other_robot_cells(self, exclude_id: str, include_reservations: bool = True) -> Set[Cell]:
        """Cells occupied (or about to be occupied) by robots other than one."""
        cells: Set[Cell] = set()
        for robot in self.robots.values():
            if robot.id == exclude_id:
                continue
```

with:

```python
    def robot_cells(self, layer: Optional[str] = None) -> Dict[Cell, str]:
        """Occupied cell -> robot id, on one layer (default: every layer)."""
        return {r.position: r.id for r in self.robots.values() if layer is None or r.layer == layer}

    def other_robot_cells(self, exclude_id: str, include_reservations: bool = True,
                          layer: Optional[str] = None) -> Set[Cell]:
        """Cells occupied (or about to be occupied) by robots other than one,
        on one layer: `layer`, or by default the excluded robot's own — a
        drone overhead never blocks a ground robot, nor the reverse."""
        if layer is None:
            me = self.robots.get(exclude_id)
            layer = me.layer if me is not None else GROUND
        cells: Set[Cell] = set()
        for robot in self.robots.values():
            if robot.id == exclude_id or robot.layer != layer:
                continue
```

**Replace in** `backend/digital_twin.py`:

```python
        occupied = set(self.robot_cells())
        if body is not None and body.is_fixed:
```

with:

```python
        occupied = set(self.robot_cells(GROUND))  # every robot starts on the ground
        if body is not None and body.is_fixed:
```

**Replace in** `backend/digital_twin.py`:

```python
    def request_charge(self, robot_id: str, priority: Priority = Priority.HIGH,
```

with:

```python
    def set_robot_layer(self, robot_id: str, layer: str, altitude_m: Optional[float] = None) -> Robot:
        """Take a drone off (onto AIR) or land it (back on GROUND), or change
        its altitude while it flies.

        Only a robot whose floor profile flies may leave the ground, and it
        takes off and lands only on a drone-pad cell that no robot on the
        target layer holds. Instantaneous here; the TAKEOFF / LAND job steps
        add the durations. Airborne altitude defaults to HOVER_CLEARANCE_M."""
        robot = self.find_robot(robot_id)
        if robot is None:
            raise KeyError(f"Robot '{robot_id}' does not exist")
        layer = str(layer or "").upper()
        if layer not in LAYERS:
            raise ValueError(f"Unknown layer {layer!r} (known: {list(LAYERS)})")
        with self.lock:
            if layer == GROUND and robot.layer == GROUND:
                return robot  # already on the ground: nothing to do
            mobility = robot.mobility
            if mobility is None or not mobility.is_air:
                raise ValueError(f"{robot.name} cannot fly")
            altitude = 0.0
            if layer == AIR:
                altitude = HOVER_CLEARANCE_M if altitude_m is None else float(altitude_m)
                if not 0.0 < altitude <= mobility.max_lift_m:
                    raise ValueError(f"Altitude must be above 0 and at most {mobility.max_lift_m} m")
            if layer != robot.layer:
                x, y = robot.position
                if self.warehouse.cell_type(x, y) is not CellType.DRONE_PAD:
                    verb = "take off" if layer == AIR else "land"
                    raise ValueError(f"{robot.name} can only {verb} on the drone pad, not at ({x},{y})")
                if robot.position in self.robot_cells(layer):
                    raise ValueError(f"({x},{y}) is already occupied on the {layer} layer")
                robot.clear_path()  # a route planned for the other layer no longer applies
            robot.layer, robot.altitude_m = layer, altitude
            robot.touch()
        self.logger.info(
            LogCategory.ROBOT,
            f"{robot.name} is on the {layer} layer at {robot.altitude_m:.1f} m",
            robot_id=robot.id, position=cell_dict(robot.position),
        )
        return robot

    def request_charge(self, robot_id: str, priority: Priority = Priority.HIGH,
```

**Replace in** `backend/digital_twin.py`:

```python
        occupied = {r.position for r in self.robots.values() if r.id != robot.id}
        home = robot.home if robot.home not in occupied else self.warehouse.nearest_walkable(
            robot.home, occupied, profile=robot.mobility
        )
        robot.position = home or robot.position
```

with:

```python
        occupied = {r.position for r in self.robots.values() if r.id != robot.id and r.layer == GROUND}
        home = robot.home if robot.home not in occupied else self.warehouse.nearest_walkable(
            robot.home, occupied, profile=robot.mobility
        )
        robot.position = home or robot.position
        robot.layer, robot.altitude_m = GROUND, 0.0  # a reset robot is back on the ground
```

**Replace in** `backend/digital_twin.py`:

```python
        if robot_a.id == robot_b.id:
            raise ValueError("A robot cannot collide with itself")
```

with:

```python
        if robot_a.id == robot_b.id:
            raise ValueError("A robot cannot collide with itself")
        if robot_a.layer != robot_b.layer:
            raise ValueError(f"{robot_a.name} and {robot_b.name} are on different layers and cannot collide")
```

**Replace in** `backend/digital_twin.py`:

```python
            data={"robot_a": robot_a.id, "robot_b": robot_b.id},
```

with:

```python
            data={"robot_a": robot_a.id, "robot_b": robot_b.id, "layer": robot_a.layer},
```

- [ ] **Step 6: Traffic and collisions compare the layer**

**Replace in** `backend/simulator.py`:

```python
        mine = self._precedence(robot)
        for other in self.twin.robots.values():
            if other.id == robot.id:
                continue
```

with:

```python
        mine = self._precedence(robot)
        for other in self.twin.robots.values():
            if other.id == robot.id or other.layer != robot.layer:
                continue  # only a robot on the same layer can be in the way
```

**Replace in** `backend/simulator.py`:

```python
        seen: Dict[Cell, Any] = {}
        for robot in twin.robots.values():
            if robot.status == RobotStatus.ERROR:
                continue
            other = seen.get(robot.position)
            if other is not None:
                twin.register_collision(robot.id, other.id)
            else:
                seen[robot.position] = robot
```

with:

```python
        seen: Dict[Tuple[str, int, int], Any] = {}
        for robot in twin.robots.values():
            if robot.status == RobotStatus.ERROR:
                continue
            key = (robot.layer, robot.position[0], robot.position[1])  # a drone over an AMR is no collision
            other = seen.get(key)
            if other is not None:
                twin.register_collision(robot.id, other.id)
            else:
                seen[key] = robot
```

**Replace in** `backend/ci_engine.py`:

```python
        seen: Dict[tuple, str] = {}
        clashes: List[str] = []
        for robot in twin.robots.values():
            if robot.position in seen:
                clashes.append(
                    f"{robot.name} and {seen[robot.position]} both occupy "
                    f"({robot.position[0]},{robot.position[1]})"
                )
            seen[robot.position] = robot.name
```

with:

```python
        seen: Dict[tuple, str] = {}
        clashes: List[str] = []
        for robot in twin.robots.values():
            key = (robot.layer, robot.position[0], robot.position[1])  # same cell AND same layer
            if key in seen:
                where = f"({robot.position[0]},{robot.position[1]})"
                if robot.layer != "GROUND":
                    where += f" on the {robot.layer} layer"
                clashes.append(f"{robot.name} and {seen[key]} both occupy {where}")
            seen[key] = robot.name
```

- [ ] **Step 7: Run the new tests with the mobility tests**

Run: `.venv/bin/python -m pytest -o addopts="" -q backend/test_layers.py backend/test_robot_mobility.py`
Expected: `18 passed`.

- [ ] **Step 8: Run the full suite**

The classic collision tests (`test_natural_collision_is_flagged_only_once`, `test_ci_detects_two_robots_in_one_cell`, `test_reset_recovers_a_collided_robot`) keep passing: every classic robot is on GROUND.

Run: `.venv/bin/python -m pytest -o addopts="" -q`
Expected: `2 failed, 427 passed` (only the two pre-existing failures).

- [ ] **Step 9: Commit**

```bash
git add backend/embodiment.py backend/robot.py backend/digital_twin.py backend/simulator.py \
        backend/ci_engine.py backend/test_layers.py
git commit -m "feat: ground and air layers with layer-aware traffic

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: People as zone presence, credential scopes and the crossing wait

`Operator` gains `zone`, transit (`transit_to`, `transit_until_tick`) and `certification_scopes` synced from the inventory with `certifications` (spec §6). `backend/people.py` answers who is where: `people_in(zone)`, walks between zones, and the supervisor-proximity half of the humanoid rule. Ground robots at a crossing — and drones about to cross the walkway — wait with `PERSON_ON_CROSSING` while anyone walks (§5.3). The forklift, arm and humanoid waits need job steps and come in plan 1b; `people_at` and `supervisor_nearby` are the helpers they will call (§17 step 1 item 6).

**Files:**
- Modify: `backend/models.py` (`CONFIG["PERSON_WALK_SPEED_MPS"]`; `LogCategory.OPERATIONS`, `SAFETY`; `EventType.ROBOT_SAFETY_WAIT`, `ROBOT_SAFETY_RESUMED`, `PERSON_MOVED`)
- Modify: `backend/operator.py` (zone, transit, scopes; `OFF_DUTY` leaves the floor)
- Modify: `backend/robot.py` (`wait_reason`, `wait_started_tick`)
- Create: `backend/people.py`
- Modify: `backend/fleet_bridge.py` (`credential_scopes`; `_sync_operator` sets the scopes)
- Modify: `backend/simulator.py` (walks finish each tick; the crossing wait; the shared safety-wait helpers)
- Test: `backend/test_people.py` (create)

**Interfaces:**
- Consumes (Tasks 2, 4–6): `Warehouse.resolve_zone`, `.zones`, `.zones_of_cell`, `Zone.center`, `CellType.WALKWAY`; `embodiment.seconds_to_ticks`; `DigitalTwin.set_robot_layer`, `Robot.layer`; `FleetBridge._sync_operator`, `InventoryService.get_worker` (credentials carry `validity`, `equipment_scope`, `site_scope`), `inventory.workforce.VALID_CREDENTIAL_STATES`.
- Produces:
  - `CONFIG["PERSON_WALK_SPEED_MPS"] = 1.2`; `LogCategory.OPERATIONS`, `LogCategory.SAFETY`; `EventType.ROBOT_SAFETY_WAIT`, `ROBOT_SAFETY_RESUMED`, `PERSON_MOVED`.
  - `Operator.zone: Optional[str]`, `.transit_to: Optional[str]`, `.transit_until_tick: Optional[int]`, `.certification_scopes: Dict[str, Dict[str, List[str]]]`, property `.in_transit -> bool`; all four in `to_dict`/`from_dict`. `set_status(OperatorStatus.OFF_DUTY)` clears zone and transit.
  - `Robot.wait_reason: Optional[str]`, `Robot.wait_started_tick: Optional[int]`; `clear_path()` resets both; `to_dict()["wait_reason"]`.
  - `backend.people`: `transit_ticks(warehouse, from_zone, to_zone) -> int`, `place(twin, operator, zone: Optional[str])`, `start_transit(twin, operator, zone) -> int` (arrival tick), `update_transits(twin) -> List[Operator]`, `people_in(twin, zone_key) -> List[Operator]`, `people_at(twin, cell) -> List[Operator]`, `in_transit(twin) -> List[Operator]`, `anyone_in_transit(twin) -> bool`, `zones_touch(warehouse, a, b) -> bool`, `supervisor_nearby(twin, robot_cell, supervisor) -> bool`. Zone names resolve through aliases; unknown ones raise `ValueError("Unknown zone ...")`.
  - `backend.fleet_bridge.credential_scopes(worker: Dict) -> Dict[str, Dict[str, List[str]]]` — empty list means unrestricted; same-code credentials merge to the wider scope.
  - `Simulator._crossing_wait(robot, task, next_cell) -> bool`, `Simulator._safety_wait(robot, task, reason: str, cell: Cell)`, `Simulator._safety_resume(robot, task)`. A wait sets WAITING and the reason every tick, emits one `ROBOT_SAFETY_WAIT` (category SAFETY, data `{"reason", "cell"}`) and sets the task BLOCKED; resuming emits one `ROBOT_SAFETY_RESUMED` (data `{"reason", "waited_ticks"}`). `PERSON_MOVED` data is `{"operator_id", "from", "to"}`, category OPERATIONS.

- [ ] **Step 1: Write the failing tests**

**Create** `backend/test_people.py`:

```python
"""People as zone presence (multi-embodiment spec §6): zones, walks between
them, credential scopes, supervisor proximity and the PERSON_ON_CROSSING wait."""
import pytest

from backend import people
from backend.digital_twin import DigitalTwin
from backend.fleet_bridge import credential_scopes
from backend.models import OperatorStatus, RobotStatus, SimulationStatus, TaskStatus
from backend.operator import Operator
from backend.simulator import Simulator


def make_twin(tmp_path, layout="distribution_center"):
    return DigitalTwin(log_dir=str(tmp_path / "logs"), data_dir=str(tmp_path / "data"),
                       persist_logs=False, demo=True, demo_tasks=False, layout=layout)


@pytest.fixture
def twin(tmp_path):
    return make_twin(tmp_path)


@pytest.fixture
def sim(twin):
    simulator = Simulator(twin)
    twin.simulation_status = SimulationStatus.RUNNING
    return simulator


@pytest.fixture
def sam(twin):
    return twin.add_operator(name="Sam", worker_id="E-10001")


# --------------------------------------------------------------------------- #
# Operator fields
# --------------------------------------------------------------------------- #
def test_operator_zone_fields_round_trip_and_off_duty_leaves_the_floor():
    operator = Operator("operator_001", "Sam")
    assert (operator.zone, operator.transit_to, operator.transit_until_tick) == (None, None, None)
    assert operator.certification_scopes == {} and not operator.in_transit
    operator.zone, operator.transit_to, operator.transit_until_tick = "pick_station_2", "workshop", 40
    operator.certification_scopes = {"safety_inspection": {"equipment": [], "site": ["WH-01"]}}
    restored = Operator.from_dict(operator.to_dict())
    assert (restored.zone, restored.transit_to, restored.transit_until_tick) == ("pick_station_2", "workshop", 40)
    assert restored.certification_scopes == operator.certification_scopes and restored.in_transit
    operator.set_status(OperatorStatus.OFF_DUTY)
    assert (operator.zone, operator.transit_to, operator.transit_until_tick) == (None, None, None)
    legacy = {key: value for key, value in restored.to_dict().items()
              if key not in ("zone", "transit_to", "transit_until_tick", "certification_scopes")}
    assert Operator.from_dict(legacy).zone is None


# --------------------------------------------------------------------------- #
# Credential scopes (synced alongside certifications)
# --------------------------------------------------------------------------- #
def test_classic_operators_carry_their_credential_scopes(tmp_path):
    classic = make_twin(tmp_path, layout="classic")
    sam = classic.find_operator("Sam")
    assert sam.certification_scopes == {
        "safety_inspection": {"equipment": [], "site": ["WH-01"]},
        "electrical_safety": {"equipment": [], "site": ["WH-01"]},
    }
    assert list(sam.certification_scopes) == sam.certifications


def test_scopes_follow_the_workforce_record(twin):
    jordan = twin.add_operator(name="Jordan", worker_id="E-10006")
    assert jordan.certification_scopes["humanoid_supervision"] == {"equipment": ["TS-H1"], "site": ["WH-01"]}
    riley = twin.add_operator(name="Riley", worker_id="E-10008")
    assert "robot_cell_access" not in riley.certification_scopes  # revoked
    assert "safety_inspection" in riley.certification_scopes
    credential = next(c for c in twin.inventory.get_worker("E-10006")["credentials"]
                      if c["code"] == "humanoid_supervision")
    twin.fleet.mutate(twin.inventory.revoke_credential, credential["credential_id"], "Audit")
    assert "humanoid_supervision" not in jordan.certification_scopes
    assert "humanoid_supervision" not in jordan.certifications


def test_two_credentials_with_one_code_merge_to_the_wider_scope():
    def credential(code, equipment, site, validity="VALID"):
        return {"code": code, "equipment_scope": equipment, "site_scope": site, "validity": validity}

    worker = {"credentials": [
        credential("forklift_operator", ["NW-PF1200"], ["WH-01"]),
        credential("forklift_operator", ["NW-HH300"], []),
        credential("robot_cell_access", ["FB-CX10"], ["WH-01"], validity="REVOKED"),
    ]}
    assert credential_scopes(worker) == {
        "forklift_operator": {"equipment": ["NW-PF1200", "NW-HH300"], "site": []},
    }


# --------------------------------------------------------------------------- #
# Zone presence and walking
# --------------------------------------------------------------------------- #
def test_placing_people_in_zones(twin, sam):
    people.place(twin, sam, "Pick 2")  # any alias resolves
    assert sam.zone == "pick_station_2" and people.people_in(twin, "pick_station_2") == [sam]
    assert people.people_at(twin, (20, 17)) == [sam] and people.people_at(twin, (10, 3)) == []
    with pytest.raises(ValueError, match="Unknown zone"):
        people.place(twin, sam, "the moon")
    people.place(twin, sam, None)
    assert sam.zone is None and people.people_in(twin, "pick_station_2") == []
    sam.set_status(OperatorStatus.OFF_DUTY)
    with pytest.raises(ValueError, match="off duty"):
        people.place(twin, sam, "workshop")


def test_a_walk_takes_its_distance_at_walking_pace(twin, sim, sam):
    # Pick 2's centre (20,17) to intake staging's (5,6): 26 cells x 1.5 m / 1.2 m/s = 32.5 s = 217 ticks.
    assert people.transit_ticks(twin.warehouse, "pick_station_2", "intake_staging") == 217
    people.place(twin, sam, "pick_station_2")
    arrival = people.start_transit(twin, sam, "intake_staging")
    assert arrival == twin.tick_count + 217
    assert sam.in_transit and people.in_transit(twin) == [sam] and people.anyone_in_transit(twin)
    assert people.people_in(twin, "pick_station_2") == [] and people.people_in(twin, "intake_staging") == []
    with pytest.raises(ValueError, match="already walking"):
        people.start_transit(twin, sam, "workshop")
    while twin.tick_count < arrival - 1:
        sim.tick()
    assert sam.in_transit
    sim.tick()
    assert not sam.in_transit and sam.zone == "intake_staging"
    moved = twin.events.query(event_type="PERSON_MOVED")
    assert len(moved) == 1 and moved[0]["data"] == {"operator_id": sam.id, "from": "pick_station_2",
                                                    "to": "intake_staging"}
    assert people.start_transit(twin, sam, "intake_staging") == twin.tick_count  # already there
    assert not sam.in_transit
    people.place(twin, sam, None)
    with pytest.raises(ValueError, match="not on the floor"):
        people.start_transit(twin, sam, "workshop")


# --------------------------------------------------------------------------- #
# Supervisor proximity
# --------------------------------------------------------------------------- #
def test_zones_touch_when_equal_overlapping_or_sharing_an_edge(twin):
    warehouse = twin.warehouse
    assert people.zones_touch(warehouse, "returns_qc", "returns_qc")
    assert people.zones_touch(warehouse, "cross_aisle", "patrol_loop")   # overlap
    assert people.zones_touch(warehouse, "returns_qc", "ne_floor")       # shared edge
    assert not people.zones_touch(warehouse, "returns_qc", "sw_floor")


def test_a_supervisor_is_nearby_in_the_same_or_an_adjacent_zone(twin):
    jordan = twin.add_operator(name="Jordan", worker_id="E-10006")
    humanoid_cell = (22, 7)  # returns_qc
    for zone, expected in (("returns_qc", True), ("ne_floor", True), ("pallet_lane", True),
                           ("sw_floor", False), ("workshop", False)):
        people.place(twin, jordan, zone)
        assert people.supervisor_nearby(twin, humanoid_cell, jordan) is expected, zone
    people.place(twin, jordan, "returns_qc")
    people.start_transit(twin, jordan, "sw_floor")
    assert not people.supervisor_nearby(twin, humanoid_cell, jordan)  # walking away
    people.place(twin, jordan, None)
    assert not people.supervisor_nearby(twin, humanoid_cell, jordan)


# --------------------------------------------------------------------------- #
# PERSON_ON_CROSSING (spec §5.3)
# --------------------------------------------------------------------------- #
def test_a_ground_robot_waits_at_a_crossing_while_someone_walks(twin, sim, sam):
    amr = twin.add_robot(name="TR50-201", asset_id="AST-000201", position=(16, 13))
    people.place(twin, sam, "pick_station_2")
    arrival = people.start_transit(twin, sam, "intake_staging")
    task = twin.tasks.create_task({"type": "MOVE_ROBOT", "robot_id": amr.id, "destination": "18,13"})
    for _ in range(20):
        sim.tick()
    assert amr.position == (16, 13) and amr.status is RobotStatus.WAITING
    assert amr.wait_reason == "PERSON_ON_CROSSING" and amr.to_dict()["wait_reason"] == "PERSON_ON_CROSSING"
    assert task.status is TaskStatus.BLOCKED
    waits = twin.events.query(event_type="ROBOT_SAFETY_WAIT")
    assert len(waits) == 1 and waits[0]["data"]["reason"] == "PERSON_ON_CROSSING"
    assert waits[0]["category"] == "SAFETY" and waits[0]["task_id"] == task.id
    while twin.tick_count < arrival:
        sim.tick()
        assert amr.position != (17, 13), "entered the crossing while a person was on the walkway"
    for _ in range(40):
        if task.is_terminal:
            break
        sim.tick()
    assert task.status is TaskStatus.COMPLETED and amr.position == (18, 13) and amr.wait_reason is None
    resumed = twin.events.query(event_type="ROBOT_SAFETY_RESUMED")
    assert len(resumed) == 1 and resumed[0]["data"]["waited_ticks"] > 200


def test_a_drone_about_to_cross_the_walkway_waits_too(twin, sim, sam):
    drone = twin.add_robot(name="IX2-208", asset_id="AST-000208", position=(20, 2))
    twin.set_robot_layer(drone.id, "AIR")
    people.place(twin, sam, "pick_station_2")
    people.start_transit(twin, sam, "intake_staging")
    twin.tasks.create_task({"type": "MOVE_ROBOT", "robot_id": drone.id, "destination": "16,2"})
    for _ in range(60):
        sim.tick()
    assert drone.position == (18, 2) and drone.wait_reason == "PERSON_ON_CROSSING"


def test_robots_away_from_the_walkway_are_not_held_up(twin, sim, sam):
    amr = twin.add_robot(name="TR50-201", asset_id="AST-000201", position=(8, 13))
    people.place(twin, sam, "pick_station_2")
    people.start_transit(twin, sam, "intake_staging")
    task = twin.tasks.create_task({"type": "MOVE_ROBOT", "robot_id": amr.id, "destination": "14,13"})
    for _ in range(80):
        if task.is_terminal:
            break
        sim.tick()
    assert task.status is TaskStatus.COMPLETED and sam.in_transit
    assert not twin.events.query(event_type="ROBOT_SAFETY_WAIT")
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/bin/python -m pytest -o addopts="" -q backend/test_people.py`
Expected: a collection error, `ImportError: cannot import name 'people' from 'backend'`.

- [ ] **Step 3: Config, log categories and events**

**Replace in** `backend/models.py`:

```python
    "CELL_SIZE_M": 1.5,
    "LOADED_SPEED_FACTOR": 0.7,
}
```

with:

```python
    "CELL_SIZE_M": 1.5,
    "LOADED_SPEED_FACTOR": 0.7,
    # People on the new floor (backend/people.py) walk between zones at this pace.
    "PERSON_WALK_SPEED_MPS": 1.2,
}
```

**Replace in** `backend/models.py`:

```python
    DIGITAL_TWIN = "DIGITAL_TWIN"
    FLEET = "FLEET"
```

with:

```python
    DIGITAL_TWIN = "DIGITAL_TWIN"
    FLEET = "FLEET"
    # Multi-embodiment floor: people, orders, stock and equipment; and the
    # physical safety waits robots obey around people.
    OPERATIONS = "OPERATIONS"
    SAFETY = "SAFETY"
```

**Replace in** `backend/models.py`:

```python
    OPERATOR_CERTIFICATIONS_CHANGED = "OPERATOR_CERTIFICATIONS_CHANGED"
```

with:

```python
    OPERATOR_CERTIFICATIONS_CHANGED = "OPERATOR_CERTIFICATIONS_CHANGED"
    # A robot started / stopped a physical safety wait (a WAITING with a
    # wait_reason, e.g. PERSON_ON_CROSSING) — see Simulator._safety_wait.
    ROBOT_SAFETY_WAIT = "ROBOT_SAFETY_WAIT"
    ROBOT_SAFETY_RESUMED = "ROBOT_SAFETY_RESUMED"
    # A person finished walking from one zone to another (backend/people.py).
    PERSON_MOVED = "PERSON_MOVED"
```

- [ ] **Step 4: Where an operator is**

**Replace in** `backend/operator.py`:

```python
        self.shift_start_hour: Optional[int] = shift_start_hour
        self.shift_end_hour: Optional[int] = shift_end_hour
        self.created_at = now_iso()
```

with:

```python
        self.shift_start_hour: Optional[int] = shift_start_hour
        self.shift_end_hour: Optional[int] = shift_end_hour
        # Where the person is on the floor (backend/people.py): a zone key, or
        # None when off the floor. While walking to `transit_to` (until
        # `transit_until_tick`) they are in transit on the walkway, in no zone.
        self.zone: Optional[str] = None
        self.transit_to: Optional[str] = None
        self.transit_until_tick: Optional[int] = None
        # {code: {"equipment": [...], "site": [...]}} for each valid credential,
        # synced from the workforce record with `certifications`. An empty list
        # means that credential is unrestricted in that dimension.
        self.certification_scopes: Dict[str, Dict[str, List[str]]] = {}
        self.created_at = now_iso()
```

**Replace in** `backend/operator.py`:

```python
    def set_status(self, status: OperatorStatus) -> OperatorStatus:
        previous = self.status
        self.status = status
        self.touch()
        return previous
```

with:

```python
    def set_status(self, status: OperatorStatus) -> OperatorStatus:
        previous = self.status
        self.status = status
        if status == OperatorStatus.OFF_DUTY:  # an off-duty person is not on the floor
            self.zone = self.transit_to = self.transit_until_tick = None
        self.touch()
        return previous

    @property
    def in_transit(self) -> bool:
        return self.transit_to is not None
```

**Replace in** `backend/operator.py`:

```python
            "shift_start_hour": self.shift_start_hour,
            "shift_end_hour": self.shift_end_hour,
            "created_at": self.created_at,
```

with:

```python
            "shift_start_hour": self.shift_start_hour,
            "shift_end_hour": self.shift_end_hour,
            "zone": self.zone,
            "transit_to": self.transit_to,
            "transit_until_tick": self.transit_until_tick,
            "certification_scopes": {code: {key: list(values) for key, values in scope.items()}
                                     for code, scope in self.certification_scopes.items()},
            "created_at": self.created_at,
```

**Replace in** `backend/operator.py`:

```python
        operator.worker_id = data.get("worker_id")
        return operator
```

with:

```python
        operator.worker_id = data.get("worker_id")
        operator.zone = data.get("zone")
        operator.transit_to = data.get("transit_to")
        operator.transit_until_tick = data.get("transit_until_tick")
        operator.certification_scopes = dict(data.get("certification_scopes") or {})
        return operator
```

- [ ] **Step 5: The robot's safety-wait reason**

**Replace in** `backend/robot.py`:

```python
        self.blocked_by: Optional[str] = None
        self.last_error: Optional[str] = None
```

with:

```python
        self.blocked_by: Optional[str] = None
        self.last_error: Optional[str] = None
        # A physical safety wait (e.g. "PERSON_ON_CROSSING") and the tick it
        # began — see Simulator._safety_wait. None when not waiting for safety.
        self.wait_reason: Optional[str] = None
        self.wait_started_tick: Optional[int] = None
```

**Replace in** `backend/robot.py`:

```python
        self.target_position = None
        self.target_name = None

    def set_path(self, path: List[Cell], target_name: Optional[str] = None) -> None:
```

with:

```python
        self.target_position = None
        self.target_name = None
        # No route, nothing to wait on: the next route re-checks from scratch.
        self.wait_reason = None
        self.wait_started_tick = None

    def set_path(self, path: List[Cell], target_name: Optional[str] = None) -> None:
```

**Replace in** `backend/robot.py`:

```python
            "blocked_by": self.blocked_by,
            "last_error": self.last_error,
```

with:

```python
            "blocked_by": self.blocked_by,
            "wait_reason": self.wait_reason,
            "last_error": self.last_error,
```

- [ ] **Step 6: Create the people module**

**Create** `backend/people.py`:

```python
"""People on the floor, modelled as zone presence (multi-embodiment spec §6).

People are not grid entities. An operator is in one zone (Operator.zone),
off the floor (zone None), or walking between two zones — "in transit on the
walkway" — for as long as the walk takes. Robots never collide with people;
they obey physical waits instead (see Simulator._crossing_wait). These
helpers answer every "who is where" question the robots' rules ask.
"""
from __future__ import annotations

from typing import Any, List, Optional

from .embodiment import seconds_to_ticks
from .models import CONFIG, Cell, EventType, LogCategory, OperatorStatus, manhattan


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
    tick. Until then they are in transit on the walkway and in neither zone."""
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
    """Everyone walking between zones right now, i.e. on the walkway."""
    return [o for o in twin.operators.values() if o.in_transit]


def anyone_in_transit(twin: Any) -> bool:
    return any(o.in_transit for o in twin.operators.values())


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
```

- [ ] **Step 7: Credential scopes follow the workforce record**

**Replace in** `backend/fleet_bridge.py`:

```python
from typing import Any, Callable, Dict, Iterable, Optional
```

with:

```python
from typing import Any, Callable, Dict, Iterable, List, Optional
```

**Replace in** `backend/fleet_bridge.py`:

```python
from .inventory.core import SYSTEM
from .models import CONFIG, EventType, LogCategory, LogLevel, RobotStatus
```

with:

```python
from .inventory.core import SYSTEM
from .inventory.workforce import VALID_CREDENTIAL_STATES
from .models import CONFIG, EventType, LogCategory, LogLevel, RobotStatus
```

**Replace in** `backend/fleet_bridge.py`:

```python
class FleetBridge:
    def __init__(self, twin: Any, service: InventoryService) -> None:
```

with:

```python
def credential_scopes(worker: Dict[str, Any]) -> Dict[str, Dict[str, List[str]]]:
    """{code: {"equipment": [...], "site": [...]}} over a worker record's valid
    credentials. An empty list means unrestricted in that dimension, so two
    credentials with one code merge to the wider scope."""
    scopes: Dict[str, Dict[str, List[str]]] = {}
    for credential in worker["credentials"]:
        if credential["validity"] not in VALID_CREDENTIAL_STATES:
            continue
        fresh = {"equipment": list(credential["equipment_scope"]), "site": list(credential["site_scope"])}
        entry = scopes.get(credential["code"])
        if entry is None:
            scopes[credential["code"]] = fresh
            continue
        for key, values in fresh.items():
            if not entry[key] or not values:
                entry[key] = []
            else:
                entry[key] += [value for value in values if value not in entry[key]]
    return scopes


class FleetBridge:
    def __init__(self, twin: Any, service: InventoryService) -> None:
```

**Replace in** `backend/fleet_bridge.py`:

```python
    def _sync_operator(self, operator: Any, announce: bool = True) -> bool:
        """Make operator.certifications equal the worker's valid credential codes."""
        codes = self.service.valid_credential_codes(operator.worker_id)
        if codes == list(operator.certifications):
```

with:

```python
    def _sync_operator(self, operator: Any, announce: bool = True) -> bool:
        """Make operator.certifications equal the worker's valid credential
        codes, and operator.certification_scopes their equipment and site
        scopes. Returns True (and announces) only when the codes changed."""
        worker = self.service.get_worker(operator.worker_id)
        codes = worker["valid_credential_codes"]
        operator.certification_scopes = credential_scopes(worker)
        if codes == list(operator.certifications):
```

- [ ] **Step 8: Walks finish each tick; robots wait at crossings**

**Replace in** `backend/simulator.py`:

```python
from .eligibility import robot_eligibility
from .maintenance import maintenance_reason
from .models import (
    CONFIG,
    ActionType,
    Cell,
    BoxStatus,
```

with:

```python
from . import people
from .eligibility import robot_eligibility
from .maintenance import maintenance_reason
from .models import (
    CONFIG,
    ActionType,
    Cell,
    CellType,
    BoxStatus,
```

**Replace in** `backend/simulator.py`:

```python
            twin.scheduler.tick()
            twin.tasks.dispatch()
```

with:

```python
            twin.scheduler.tick()
            people.update_transits(twin)  # walks end before robots decide who is on the walkway
            twin.tasks.dispatch()
```

**Replace in** `backend/simulator.py`:

```python
        next_cell = robot.next_cell
        if next_cell is None:
            robot.clear_path()
            return

        blocker = self._blocking_robot(robot, next_cell)
```

with:

```python
        next_cell = robot.next_cell
        if next_cell is None:
            robot.clear_path()
            return

        if self._crossing_wait(robot, task, next_cell):
            return

        blocker = self._blocking_robot(robot, next_cell)
```

**Replace in** `backend/simulator.py`:

```python
    # ---- handling ----------------------------------------------------- #
    def _act_pick(self, robot: Any, task: Any, action: Any) -> None:
```

with:

```python
    # ---- people and safety waits (spec §6, §10.3) --------------------- #
    def _crossing_wait(self, robot: Any, task: Any, next_cell: Cell) -> bool:
        """PERSON_ON_CROSSING: a robot about to step onto the pedestrian
        walkway (a ground robot at a crossing, or a drone about to cross it)
        waits while anyone is walking between zones. True while it waits."""
        warehouse = self.twin.warehouse
        entering = (warehouse.cell_type(*next_cell) is CellType.WALKWAY
                    and warehouse.cell_type(*robot.position) is not CellType.WALKWAY)
        if entering and people.anyone_in_transit(self.twin):
            self._safety_wait(robot, task, "PERSON_ON_CROSSING", next_cell)
            return True
        if robot.wait_reason == "PERSON_ON_CROSSING":
            self._safety_resume(robot, task)
        return False

    def _safety_wait(self, robot: Any, task: Any, reason: str, cell: Cell) -> None:
        """A physical wait — a decision, not a rejection: the robot holds
        WAITING with a reason code, announced once when the wait starts. It
        is not a traffic block, so it never replans or sidesteps."""
        robot.set_status(RobotStatus.WAITING)
        if robot.wait_reason == reason:
            return
        robot.wait_reason = reason
        robot.wait_started_tick = self.twin.tick_count
        self.twin.events.emit(
            EventType.ROBOT_SAFETY_WAIT,
            f"{robot.name} waiting before ({cell[0]},{cell[1]}): {reason}",
            category=LogCategory.SAFETY,
            level=LogLevel.WARNING,
            robot_id=robot.id,
            task_id=task.id,
            position=cell_dict(cell),
            data={"reason": reason, "cell": cell_dict(cell)},
        )
        self.twin.tasks.set_status(task, TaskStatus.BLOCKED, f"Safety wait: {reason}")

    def _safety_resume(self, robot: Any, task: Any) -> None:
        reason = robot.wait_reason
        started = robot.wait_started_tick if robot.wait_started_tick is not None else self.twin.tick_count
        waited = self.twin.tick_count - started
        robot.wait_reason = None
        robot.wait_started_tick = None
        robot.set_status(RobotStatus.DELIVERING if robot.carrying_box else RobotStatus.MOVING)
        self.twin.events.emit(
            EventType.ROBOT_SAFETY_RESUMED,
            f"{robot.name} resumed after {waited} ticks ({reason} cleared)",
            category=LogCategory.SAFETY,
            robot_id=robot.id,
            task_id=task.id,
            position=cell_dict(robot.position),
            data={"reason": reason, "waited_ticks": waited},
        )
        self.twin.tasks.set_status(
            task, TaskStatus.TRANSPORTING if robot.carrying_box else TaskStatus.IN_PROGRESS,
            f"{reason} cleared",
        )

    # ---- handling ----------------------------------------------------- #
    def _act_pick(self, robot: Any, task: Any, action: Any) -> None:
```

- [ ] **Step 9: Run the new tests with the fleet-bridge tests**

Run: `.venv/bin/python -m pytest -o addopts="" -q backend/test_people.py backend/test_fleet_bridge.py`
Expected: `41 passed`.

- [ ] **Step 10: Run the full suite**

The classic floor has no WALKWAY cells and its operators never walk, so no classic robot ever waits.

Run: `.venv/bin/python -m pytest -o addopts="" -q`
Expected: `2 failed, 438 passed` (only the two pre-existing failures).

- [ ] **Step 11: Commit**

```bash
git add backend/models.py backend/operator.py backend/robot.py backend/people.py \
        backend/fleet_bridge.py backend/simulator.py backend/test_people.py
git commit -m "feat: people as zone presence and the crossing wait

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Box kinds, declared and true weights, and the stock ledger

`Box` gains `kind`, `sku`, `quantity`, `declared_weight_kg` / `true_weight_kg` (with `weight` kept as an alias of the declared weight), `slot` and `order_id`, all serialised (spec §7.1). `backend/goods.py` adds the `StockLedger`: slot → box, SKU → locations, recorded versus true quantity, counts and reconciliation (§7.2). Nothing is wired into the twin yet — the jobs that pick, count and reconcile arrive in plan 1b (§17 step 1 item 7).

**Files:**
- Modify: `backend/models.py` (`BoxKind`; `CONFIG["STOCK_AUTO_RECONCILE_UNITS"]`, `CONFIG["CARTON_TARE_KG"]`)
- Overwrite: `backend/box.py`
- Create: `backend/goods.py`
- Test: `backend/test_goods.py` (create)

**Interfaces:**
- Consumes (Task 2): `Warehouse.slot(slot_id)`, `Slot.kind` (`"PALLET"`/`"TOTE"`), slot ids such as `"PR-08-02-0"` and `"TS-10-12-1"`.
- Produces:
  - `models.BoxKind` (`PALLET`, `TOTE`, `ITEM`, `CARTON`), re-exported by `backend.goods`; `CONFIG["STOCK_AUTO_RECONCILE_UNITS"] = 2`, `CONFIG["CARTON_TARE_KG"] = 0.3`.
  - `Box(box_id, name, position, weight=1.0, source=None, destination=None, status=BoxStatus.STORED, kind=BoxKind.TOTE, sku=None, quantity=0, true_weight_kg=None, slot=None, order_id=None)`; attributes `kind: BoxKind`, `sku`, `quantity: int`, `declared_weight_kg: float`, `true_weight_kg: float` (defaults to declared), `slot: Optional[str]` (a `Warehouse.slots` id), `order_id`; property `weight` reads and writes `declared_weight_kg`. `to_dict()` keeps `"weight"` and adds `"kind"`, `"sku"`, `"quantity"`, `"declared_weight_kg"`, `"true_weight_kg"`, `"slot"`, `"order_id"`; `from_dict` accepts old dicts. An unknown kind raises `ValueError`.
  - `backend.goods.carton_weight_kg(item_weights_kg: Iterable[float]) -> float` (sum + 0.3 kg).
  - `StockLocation(slot_id, box_id, sku, recorded_qty, true_qty, counted_qty=None, counted_tick=None)` dataclass with `.discrepancy` (true − recorded) and `to_dict()`.
  - `StockLedger(warehouse=None)`: `put(slot_id, box_id, sku, quantity, kind=None) -> StockLocation`, `take(slot_id) -> StockLocation`, `location(slot_id)`, `box_in(slot_id)`, `slot_of(box_id)`, `locations(sku=None)`, `skus()`, `recorded_qty(sku)`, `true_qty(sku)`, `discrepancies()`, `consume(slot_id, quantity, true_quantity=None)`, `restock(slot_id, quantity)`, `adjust_true(slot_id, delta)`, `record_count(slot_id, counted_qty, tick=None) -> Dict` (keys `slot_id`, `box_id`, `sku`, `recorded_qty`, `counted_qty`, `true_qty`, `variance`, `auto_reconcile`), `reconcile(slot_id) -> int`, `to_dict()`, `StockLedger.from_dict(data, warehouse=None)`. Bad placements raise `ValueError`; an empty slot raises `KeyError`.

- [ ] **Step 1: Write the failing tests**

**Create** `backend/test_goods.py`:

```python
"""Goods (multi-embodiment spec §7): box kinds and weights, and the stock
ledger's recorded-versus-true quantities."""
import pytest

from backend.box import Box
from backend.digital_twin import DigitalTwin
from backend.goods import BoxKind, StockLedger, carton_weight_kg
from backend.warehouse import Warehouse


@pytest.fixture(scope="module")
def dc():
    return Warehouse(layout="distribution_center")


@pytest.fixture
def ledger(dc):
    stock = StockLedger(dc)
    stock.put("PR-08-02-0", "box_001", "SKU-0001", 40, kind="PALLET")
    stock.put("TS-10-12-1", "box_002", "SKU-0002", 20, kind="TOTE")
    stock.put("TS-11-12-1", "box_003", "SKU-0002", 15, kind="TOTE")
    return stock


# --------------------------------------------------------------------------- #
# Boxes
# --------------------------------------------------------------------------- #
def test_a_classic_box_is_a_tote_whose_weight_is_its_declared_weight():
    box = Box("box_001", "Box-A", (3, 5), weight=12.5)
    assert (box.kind, box.sku, box.quantity, box.slot, box.order_id) == (BoxKind.TOTE, None, 0, None, None)
    assert box.weight == box.declared_weight_kg == box.true_weight_kg == 12.5
    box.weight = 14.0
    assert box.declared_weight_kg == 14.0 and box.true_weight_kg == 12.5  # the alias sets the declared weight
    assert [kind.value for kind in BoxKind] == ["PALLET", "TOTE", "ITEM", "CARTON"]
    with pytest.raises(ValueError):
        Box("box_002", "Bad", (1, 1), kind="CRATE")


def test_box_goods_fields_round_trip():
    pallet = Box("box_007", "PAL-007", (8, 2), weight=600.0, kind="PALLET", sku="SKU-0007", quantity=48,
                 true_weight_kg=780.0, slot="PR-08-02-0", order_id="ORD-0003")
    data = pallet.to_dict()
    assert (data["kind"], data["sku"], data["quantity"], data["slot"], data["order_id"]) == \
        ("PALLET", "SKU-0007", 48, "PR-08-02-0", "ORD-0003")
    assert (data["weight"], data["declared_weight_kg"], data["true_weight_kg"]) == (600.0, 600.0, 780.0)
    restored = Box.from_dict(data)
    assert restored.to_dict() == data
    legacy = {key: value for key, value in data.items()
              if key not in ("kind", "sku", "quantity", "declared_weight_kg", "true_weight_kg", "slot", "order_id")}
    old = Box.from_dict(legacy)
    assert (old.kind, old.weight, old.true_weight_kg, old.slot) == (BoxKind.TOTE, 600.0, 600.0, None)


def test_classic_demo_boxes_are_unchanged(tmp_path):
    twin = DigitalTwin(log_dir=str(tmp_path / "logs"), data_dir=str(tmp_path / "data"),
                       persist_logs=False, demo=True, demo_tasks=False)
    box = twin.find_box("Box-A")
    assert (box.weight, box.kind, box.position) == (12.5, BoxKind.TOTE, (3, 5))


def test_a_carton_weighs_its_items_plus_the_carton():
    assert carton_weight_kg([1.0, 2.5]) == 3.8
    assert carton_weight_kg([]) == 0.3


# --------------------------------------------------------------------------- #
# Stock ledger
# --------------------------------------------------------------------------- #
def test_the_ledger_maps_slots_to_boxes_and_skus_to_locations(ledger):
    assert ledger.box_in("PR-08-02-0") == "box_001" and ledger.slot_of("box_002") == "TS-10-12-1"
    assert [loc.slot_id for loc in ledger.locations("SKU-0002")] == ["TS-10-12-1", "TS-11-12-1"]
    assert ledger.skus() == ["SKU-0001", "SKU-0002"]
    assert ledger.recorded_qty("SKU-0002") == ledger.true_qty("SKU-0002") == 35
    assert ledger.box_in("TS-16-18-2") is None and ledger.slot_of("box_999") is None


def test_the_ledger_rejects_impossible_placements(ledger):
    with pytest.raises(ValueError, match="Unknown slot"):
        ledger.put("PR-99-99-0", "box_010", "SKU-0001", 1)
    with pytest.raises(ValueError, match="cannot go in"):
        ledger.put("TS-12-12-0", "box_010", "SKU-0001", 1, kind="PALLET")
    with pytest.raises(ValueError, match="already holds"):
        ledger.put("PR-08-02-0", "box_010", "SKU-0001", 1)
    with pytest.raises(ValueError, match="already in slot"):
        ledger.put("PR-09-02-0", "box_001", "SKU-0001", 1)
    with pytest.raises(ValueError):
        ledger.put("PR-09-02-0", "box_010", "SKU-0001", -1)
    loose = StockLedger()  # no warehouse: any slot id is accepted
    loose.put("anywhere", "box_010", "SKU-0001", 1, kind="PALLET")


def test_picks_and_mis_picks(ledger):
    ledger.consume("TS-10-12-1", 3)
    location = ledger.location("TS-10-12-1")
    assert (location.recorded_qty, location.true_qty, location.discrepancy) == (17, 17, 0)
    ledger.consume("TS-10-12-1", 2, true_quantity=3)  # a mis-pick took one more than recorded
    assert (location.recorded_qty, location.true_qty, location.discrepancy) == (15, 14, -1)
    assert ledger.discrepancies() == [location]
    with pytest.raises(ValueError, match="records only"):
        ledger.consume("TS-10-12-1", 99)
    with pytest.raises(KeyError):
        ledger.consume("TS-16-18-2", 1)


def test_returns_and_faults(ledger):
    ledger.restock("TS-11-12-1", 5)
    assert (ledger.location("TS-11-12-1").recorded_qty, ledger.location("TS-11-12-1").true_qty) == (20, 20)
    ledger.adjust_true("PR-08-02-0", -4)  # something vanished without the record knowing
    assert ledger.location("PR-08-02-0").discrepancy == -4
    ledger.adjust_true("PR-08-02-0", -100)
    assert ledger.location("PR-08-02-0").true_qty == 0


def test_counts_flag_variance_and_only_reconciliation_corrects_the_record(ledger):
    with pytest.raises(ValueError, match="not been counted"):
        ledger.reconcile("TS-10-12-1")
    ledger.adjust_true("TS-10-12-1", -2)
    small = ledger.record_count("TS-10-12-1", 18, tick=40)
    assert (small["variance"], small["auto_reconcile"], small["recorded_qty"]) == (-2, True, 20)
    assert ledger.location("TS-10-12-1").recorded_qty == 20  # counting alone changes nothing
    assert ledger.reconcile("TS-10-12-1") == -2
    assert ledger.location("TS-10-12-1").recorded_qty == 18 and not ledger.discrepancies()
    ledger.adjust_true("TS-11-12-1", -3)
    large = ledger.record_count("TS-11-12-1", 12)
    assert (large["variance"], large["auto_reconcile"]) == (-3, False)  # an exception for the orders panel
    assert ledger.record_count("PR-08-02-0", 40)["auto_reconcile"] is False  # no variance at all


def test_take_and_persistence(ledger, dc):
    ledger.record_count("TS-10-12-1", 19, tick=7)
    restored = StockLedger.from_dict(ledger.to_dict(), dc)
    assert restored.to_dict() == ledger.to_dict()
    assert restored.location("TS-10-12-1").counted_tick == 7
    taken = ledger.take("PR-08-02-0")
    assert taken.box_id == "box_001" and ledger.box_in("PR-08-02-0") is None
    assert ledger.slot_of("box_001") is None
    with pytest.raises(KeyError):
        ledger.take("PR-08-02-0")
    ledger.put("PR-08-02-0", "box_001", "SKU-0001", 40, kind="PALLET")  # the slot is free again
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/bin/python -m pytest -o addopts="" -q backend/test_goods.py`
Expected: a collection error, `ModuleNotFoundError: No module named 'backend.goods'`.

- [ ] **Step 3: Box kinds and the goods settings**

**Replace in** `backend/models.py`:

```python
    # People on the new floor (backend/people.py) walk between zones at this pace.
    "PERSON_WALK_SPEED_MPS": 1.2,
}
```

with:

```python
    # People on the new floor (backend/people.py) walk between zones at this pace.
    "PERSON_WALK_SPEED_MPS": 1.2,
    # Goods (backend/goods.py): a cycle-count variance this small or smaller
    # is reconciled automatically; a packed carton weighs its items plus this.
    "STOCK_AUTO_RECONCILE_UNITS": 2,
    "CARTON_TARE_KG": 0.3,
}
```

**Replace in** `backend/models.py`:

```python
class BoxStatus(str, enum.Enum):
```

with:

```python
class BoxKind(str, enum.Enum):
    """What a Box is (multi-embodiment spec §7.1). Classic boxes are TOTEs."""
    PALLET = "PALLET"
    TOTE = "TOTE"
    ITEM = "ITEM"
    CARTON = "CARTON"


class BoxStatus(str, enum.Enum):
```

- [ ] **Step 4: The box carries its goods data**

**Overwrite** `backend/box.py`:

```python
"""Boxes: the payloads robots move around the warehouse.

One entity covers every kind of goods (multi-embodiment spec §7.1): a PALLET
of cases, a TOTE of units, a single ITEM, or a packed CARTON. Classic boxes
are TOTEs and use nothing but `weight`, exactly as before.
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Union

from .models import BoxKind, BoxStatus, Cell, cell_dict, cell_tuple, now_iso


class Box:
    def __init__(
        self,
        box_id: str,
        name: str,
        position: Cell,
        weight: float = 1.0,
        source: Optional[str] = None,
        destination: Optional[str] = None,
        status: BoxStatus = BoxStatus.STORED,
        kind: Union[BoxKind, str] = BoxKind.TOTE,
        sku: Optional[str] = None,
        quantity: int = 0,
        true_weight_kg: Optional[float] = None,
        slot: Optional[str] = None,
        order_id: Optional[str] = None,
    ) -> None:
        self.id = box_id
        self.name = name
        self.position: Cell = position
        self.kind = BoxKind(kind)
        # A PALLET holds `quantity` cases of one SKU, a TOTE `quantity` units.
        self.sku = sku
        self.quantity = int(quantity)
        # What the WMS says it weighs (`weight` is this, by its old name) and
        # what it really weighs. Equal unless a fault makes them differ.
        self.declared_weight_kg = float(weight)
        self.true_weight_kg = float(true_weight_kg) if true_weight_kg is not None else self.declared_weight_kg
        # The rack or shelf slot (a Warehouse.slots id) it sits in, if any.
        self.slot = slot
        self.order_id = order_id
        self.source = source
        self.destination = destination
        self.status = status
        self.assigned_robot: Optional[str] = None
        self.assigned_task: Optional[str] = None
        self.pick_count = 0
        self.delivery_count = 0
        self.created_at = now_iso()
        self.updated_at = now_iso()

    # ------------------------------------------------------------------ #
    @property
    def weight(self) -> float:
        """The declared weight, under the name every classic caller uses."""
        return self.declared_weight_kg

    @weight.setter
    def weight(self, value: float) -> None:
        self.declared_weight_kg = float(value)

    def touch(self) -> None:
        self.updated_at = now_iso()

    def set_status(self, status: BoxStatus) -> BoxStatus:
        previous = self.status
        self.status = status
        self.touch()
        return previous

    @property
    def is_available(self) -> bool:
        return self.status in (BoxStatus.STORED, BoxStatus.DELIVERED) and self.assigned_robot is None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "weight": self.weight,
            "kind": self.kind.value,
            "sku": self.sku,
            "quantity": self.quantity,
            "declared_weight_kg": self.declared_weight_kg,
            "true_weight_kg": self.true_weight_kg,
            "slot": self.slot,
            "order_id": self.order_id,
            "position": cell_dict(self.position),
            "source": self.source,
            "destination": self.destination,
            "status": self.status.value,
            "assigned_robot": self.assigned_robot,
            "assigned_task": self.assigned_task,
            "pick_count": self.pick_count,
            "delivery_count": self.delivery_count,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "Box":
        box = Box(
            box_id=data["id"],
            name=data["name"],
            position=cell_tuple(data["position"]) or (1, 1),
            weight=data.get("declared_weight_kg", data.get("weight", 1.0)),
            source=data.get("source"),
            destination=data.get("destination"),
            status=BoxStatus(data.get("status", "STORED")),
            kind=data.get("kind", BoxKind.TOTE.value),
            sku=data.get("sku"),
            quantity=data.get("quantity", 0),
            true_weight_kg=data.get("true_weight_kg"),
            slot=data.get("slot"),
            order_id=data.get("order_id"),
        )
        box.assigned_robot = data.get("assigned_robot")
        box.assigned_task = data.get("assigned_task")
        box.pick_count = data.get("pick_count", 0)
        box.delivery_count = data.get("delivery_count", 0)
        box.created_at = data.get("created_at", box.created_at)
        box.updated_at = data.get("updated_at", box.updated_at)
        return box
```

- [ ] **Step 5: Create the goods module**

**Create** `backend/goods.py`:

```python
"""Goods and the stock ledger (multi-embodiment spec §7).

Box kinds and their weights live on Box (backend/box.py). This module adds
the StockLedger: which pallet or tote sits in each rack or shelf slot, and
where each SKU is stocked. Every location keeps two numbers — what the
record says (`recorded_qty`) and what is really there (`true_qty`). Only a
fault or a mis-pick makes them differ, and only a count reconciliation
corrects the record. Nothing here emits events: the jobs that pick, count
and reconcile do (plan 1b).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, Iterable, List, Optional

from .models import CONFIG, BoxKind

__all__ = ["BoxKind", "StockLedger", "StockLocation", "carton_weight_kg"]


def carton_weight_kg(item_weights_kg: Iterable[float]) -> float:
    """A packed carton weighs its items plus the carton itself (spec §7.1)."""
    return round(sum(item_weights_kg) + CONFIG["CARTON_TARE_KG"], 3)


@dataclass
class StockLocation:
    slot_id: str
    box_id: str
    sku: Optional[str]
    recorded_qty: int
    true_qty: int
    counted_qty: Optional[int] = None  # the last cycle count's reading
    counted_tick: Optional[int] = None

    @property
    def discrepancy(self) -> int:
        """What is really there minus what the record says."""
        return self.true_qty - self.recorded_qty

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class StockLedger:
    """Slot -> the box in it, and SKU -> its locations, with recorded and
    true quantities. Pass the warehouse to check slot ids and kinds."""

    def __init__(self, warehouse: Optional[Any] = None) -> None:
        self.warehouse = warehouse
        self._locations: Dict[str, StockLocation] = {}
        self._slot_of_box: Dict[str, str] = {}

    # ---- placing and removing ----------------------------------------- #
    def put(self, slot_id: str, box_id: str, sku: Optional[str], quantity: int,
            kind: Optional[str] = None) -> StockLocation:
        """Record a pallet or tote arriving in a slot, with its contents."""
        if self.warehouse is not None:
            slot = self.warehouse.slot(slot_id)
            if slot is None:
                raise ValueError(f"Unknown slot {slot_id!r}")
            if kind is not None and BoxKind(kind).value != slot.kind:
                raise ValueError(f"A {BoxKind(kind).value} cannot go in {slot.kind} slot {slot_id}")
        if slot_id in self._locations:
            raise ValueError(f"Slot {slot_id} already holds {self._locations[slot_id].box_id}")
        if box_id in self._slot_of_box:
            raise ValueError(f"{box_id} is already in slot {self._slot_of_box[box_id]}")
        if int(quantity) < 0:
            raise ValueError("quantity cannot be negative")
        location = StockLocation(slot_id, box_id, sku, int(quantity), int(quantity))
        self._locations[slot_id] = location
        self._slot_of_box[box_id] = slot_id
        return location

    def take(self, slot_id: str) -> StockLocation:
        """Record the box leaving its slot (retrieved, or taken to a station)."""
        location = self._require(slot_id)
        del self._locations[slot_id]
        del self._slot_of_box[location.box_id]
        return location

    # ---- reads ---------------------------------------------------------- #
    def location(self, slot_id: str) -> Optional[StockLocation]:
        return self._locations.get(slot_id)

    def box_in(self, slot_id: str) -> Optional[str]:
        location = self._locations.get(slot_id)
        return location.box_id if location else None

    def slot_of(self, box_id: str) -> Optional[str]:
        return self._slot_of_box.get(box_id)

    def locations(self, sku: Optional[str] = None) -> List[StockLocation]:
        """Every location (or every location of `sku`), by slot id."""
        return [self._locations[key] for key in sorted(self._locations)
                if sku is None or self._locations[key].sku == sku]

    def skus(self) -> List[str]:
        return sorted({loc.sku for loc in self._locations.values() if loc.sku is not None})

    def recorded_qty(self, sku: str) -> int:
        return sum(loc.recorded_qty for loc in self.locations(sku))

    def true_qty(self, sku: str) -> int:
        return sum(loc.true_qty for loc in self.locations(sku))

    def discrepancies(self) -> List[StockLocation]:
        """Locations whose record no longer matches what is really there."""
        return [loc for loc in self.locations() if loc.discrepancy]

    # ---- quantity changes ---------------------------------------------- #
    def consume(self, slot_id: str, quantity: int, true_quantity: Optional[int] = None) -> StockLocation:
        """A pick: the record drops by `quantity`, the shelf by `true_quantity`
        (a mis-pick takes a different number than the record says)."""
        location = self._require(slot_id)
        taken = quantity if true_quantity is None else true_quantity
        if quantity < 0 or taken < 0:
            raise ValueError("quantities cannot be negative")
        if quantity > location.recorded_qty:
            raise ValueError(f"{slot_id} records only {location.recorded_qty} of {location.sku}")
        location.recorded_qty -= int(quantity)
        location.true_qty = max(0, location.true_qty - int(taken))
        return location

    def restock(self, slot_id: str, quantity: int) -> StockLocation:
        """Units put back (a return): both the record and the shelf go up."""
        location = self._require(slot_id)
        if quantity < 0:
            raise ValueError("quantity cannot be negative")
        location.recorded_qty += int(quantity)
        location.true_qty += int(quantity)
        return location

    def adjust_true(self, slot_id: str, delta: int) -> StockLocation:
        """A physical change the record never saw (an injected fault)."""
        location = self._require(slot_id)
        location.true_qty = max(0, location.true_qty + int(delta))
        return location

    # ---- counting ------------------------------------------------------- #
    def record_count(self, slot_id: str, counted_qty: int, tick: Optional[int] = None) -> Dict[str, Any]:
        """A cycle count's reading. It is stored and compared with the record,
        but the record is left alone until reconcile()."""
        location = self._require(slot_id)
        location.counted_qty, location.counted_tick = int(counted_qty), tick
        variance = location.counted_qty - location.recorded_qty
        return {
            "slot_id": slot_id, "box_id": location.box_id, "sku": location.sku,
            "recorded_qty": location.recorded_qty, "counted_qty": location.counted_qty,
            "true_qty": location.true_qty, "variance": variance,
            "auto_reconcile": variance != 0 and abs(variance) <= CONFIG["STOCK_AUTO_RECONCILE_UNITS"],
        }

    def reconcile(self, slot_id: str) -> int:
        """Correct the record to the last count; returns the correction made."""
        location = self._require(slot_id)
        if location.counted_qty is None:
            raise ValueError(f"{slot_id} has not been counted")
        correction = location.counted_qty - location.recorded_qty
        location.recorded_qty = location.counted_qty
        return correction

    # ---- persistence ---------------------------------------------------- #
    def to_dict(self) -> Dict[str, Any]:
        return {"locations": [loc.to_dict() for loc in self.locations()]}

    @classmethod
    def from_dict(cls, data: Dict[str, Any], warehouse: Optional[Any] = None) -> "StockLedger":
        ledger = cls(warehouse)
        for item in data.get("locations", []):
            location = StockLocation(**item)
            ledger._locations[location.slot_id] = location
            ledger._slot_of_box[location.box_id] = location.slot_id
        return ledger

    def _require(self, slot_id: str) -> StockLocation:
        location = self._locations.get(slot_id)
        if location is None:
            raise KeyError(f"Slot {slot_id} is empty")
        return location
```

- [ ] **Step 6: Run the new tests**

Run: `.venv/bin/python -m pytest -o addopts="" -q backend/test_goods.py`
Expected: `10 passed`.

- [ ] **Step 7: Run the full suite**

Run: `.venv/bin/python -m pytest -o addopts="" -q`
Expected: `2 failed, 448 passed` (only the two pre-existing failures).

- [ ] **Step 8: Commit**

```bash
git add backend/models.py backend/box.py backend/goods.py backend/test_goods.py
git commit -m "feat: box kinds, declared and true weights, and the stock ledger

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## Self-review

**Spec coverage (§17 step 1, items 1–7).**

| Spec requirement | Task |
|---|---|
| §15 classic golden test before any move; `_build_layout` moved verbatim; `LAYOUTS`, `build_layout(name)` | 1 |
| §2 `DigitalTwin(..., layout="classic")` default; `layout_name` in `snapshot()` | 1 |
| §3.1 new CellTypes; `WALKABLE_CELLS` unchanged | 2 |
| §3.2 every zone, its cells and attributes; dock doors; `required_zones`; per-cell clearance (WIDE wins, unmarked NARROW); crossings (two WIDE, four NARROW); `zone_of_cell` most specific, `zones_of_cell` all | 2 |
| §3.2 slots: 180 pallet, 108 tote, face cells per rack row / adjacent tote aisles | 2 |
| §4.1 `open_inventory(..., profile=)`; classic byte for byte; per-profile `SEED_VERSION` and template cache; `.bak` reseed across profiles; everyone at WH-01; home-zone table; AST-000207 online | 3 |
| §5.1 / §13.2 catalog: `lift_speed_mps`, `grasp_s`/`place_s`, `takeoff_s`/`land_s`/`scan_s`, release `known_issues` | 3 |
| §5.1 `MobilityProfile.from_model` (movement, clearance, speed ÷ `CELL_SIZE_M`, loaded × 0.7, payload, shelf level, box kinds, lift, reach, supervision, battery, timings) | 4 |
| §5.2 `Warehouse.passable(cell, profile, layer)`: wide/narrow ground, crossings, stations, air and no-fly, pad-only ground for drones, fixed never moves | 4 |
| §5.2 `find_path(..., profile=None, layer="GROUND")` through `nearest_walkable`, `best_cell_in_zone`, `path_exists`, `distance` | 4 |
| §5.2 profile threaded through `_break_deadlock` and spawn; §5.1 profile from the bound asset's model; `Robot.speed` from `speed_cells_s`; classic unchanged | 5 |
| §5.1 `ARM` and `HUMANOID` presets with `CHARGE_ROBOT` | 5 |
| §5.3 `Robot.layer`, `altitude_m`; `(layer, x, y)` in `other_robot_cells`, `_blocking_robot`, `_detect_collisions`, `register_collision`, CI `_check_collisions`; `to_dict`/`from_dict`; drones reach AIR with correct passability | 6 |
| §6 `Operator.zone`, transit, `certification_scopes`; `people_in(zone)`; supervisor proximity; §5.3 `PERSON_ON_CROSSING` | 7 |
| §7.1 `Box.kind`, `sku`, `quantity`, declared/true weight (`weight` alias), `slot`, `order_id`, serialisation; §7.2 `StockLedger` | 8 |

Deferred on purpose, and listed under **Carried forward**: TAKEOFF/LAND/LIFT/SCAN/GRASP/PLACE steps, the other people waits and escalation, capability-filtered selection, CI's layout-driven checks, §4.4 heartbeat, save/load v2, the twin seed and ledger wiring.

**Placeholder scan.** Every code step carries the full code or an exact **Replace in** block; there are no "TBD", "similar to" or unnamed helpers. Every **Replace in** block was checked to match its file exactly once at that point in the task order, and each **Replace every** block states its occurrence count.

**Type consistency.** Names used across tasks: `Layout`, `Zone`, `Slot`, `WIDE`/`NARROW` (base, Task 1–2) → `Warehouse._clearance`, `fixed_stations`, `is_flyable` (Task 2) → `MobilityProfile`, `GROUND`/`AIR`, `passable`, `neighbors(cell, profile, layer)` (Task 4) → `Robot.mobility`, `Robot.layer`, `FleetBridge.model_profile`/`floor_profile`, `DigitalTwin._spawn_cell` (Task 5) → `Robot.altitude_m`, `robot_cells(layer)`, `set_robot_layer`, `HOVER_CLEARANCE_M` (Task 6) → `Operator.in_transit`, `Robot.wait_reason`, `people.*`, `credential_scopes`, `Simulator._safety_wait` (Task 7) → `BoxKind`, `StockLedger` (Task 8). The plan was executed end to end on a scratch copy of the branch: after each task the full suite gave exactly the counts stated (375, 387, 397, 409, 418, 427, 438, 448 passed, with only the two pre-existing failures).
