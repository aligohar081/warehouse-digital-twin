# Multi-Embodiment Plan 1c — Seed, Save/Load, Operations API, Dashboard and Soak Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish step 1 (C + E): the app boots the distribution-centre floor with its full seed (all 15 non-retired robots, the 10 workers, 60 pallets, 80 totes), saves and loads it (v2), exposes the shift, orders, stock, people, equipment and faults over HTTP, draws all of it in the dashboard (layout-driven floor, glyphs, air layer, people, conveyor, robot and shift panels), and proves a fixed-seed soak runs clean — after first closing plan 1b's two load-bearing residuals.

**Architecture:** Plan 1c sits on top of plans 1a and 1b and on the `WAREHOUSE_LAYOUT` boot (`backend/app.py` `build_twin`). New units: `backend/seeds/` (classic and distribution-centre seeds), `backend/operations_api.py` (the §11.5/§14 routes), `backend/soak.py` (a reusable soak runner and CLI), `frontend/floor_model.js` (pure, DOM-free view-model helpers the dashboard draws from, unit-tested with JavaScriptCore). Existing units gain save/load v2 (`to_dict`/`from_dict` on the ledger, equipment, shift engine and orders), a `cell_types` table and live equipment/shift data in the snapshot, and the long-shift policies the plan 1b soak deferred. The classic floor stays exactly as it is.

**Tech Stack:** Python 3.14, Flask, PyYAML, pytest and the standard library; the dashboard stays plain ES5 JavaScript with no build step and no libraries; JS unit tests run on macOS's built-in JavaScriptCore (`jsc`).

**Spec:** `docs/superpowers/specs/2026-09-30-multi-embodiment-operations-design.md` — the binding authority. This plan covers §17 step 1, items 14–17: §2 "Construction" (app boot), §4.2–§4.4 (seeds, save/load/reset, remote check-ins), §11.5 (controls), §12 (dashboard), §14 (API), §15 (compatibility and docs) and §16's step-1 soak, fault matrix, API tests and browser check (items 1–5; 6–7 are step 2). It also closes every "To plan 1c" item of plan 1a, every item of plan 1b's **Carried forward to plan 1c**, and the plan-1c inputs in `docs/handoffs/2026-10-04-plan-1c-inputs/` (see **Carried forward**).

## Global Constraints

- Work on branch `feature/multi-embodiment-1c` (cut from `master` at `67af707`). Commit at the end of every task. Never merge, rebase or push.
- Test command, from the repo root: `.venv/bin/python -m pytest -o addopts="" -q`. Baseline before Task 1: `2 failed, 768 passed`.
- The only failures ever allowed are the two pre-existing ones: `backend/test_eval_engine.py::test_real_task_006_box_conflict_is_caught` and `backend/tests.py::test_idle_robot_with_a_low_battery_charges_itself`.
- Dependencies: Flask, PyYAML, pytest and the standard library only. No new dependencies. No Node. JavaScript unit tests run with `/System/Library/Frameworks/JavaScriptCore.framework/Versions/A/Helpers/jsc` through a pytest wrapper that skips when that binary is absent.
- Do not edit any existing test file, including the test files earlier tasks of this plan create — with one exception (Plan ruling 3): Task 2 rewrites `backend/test_app_layout.py`. New tests go in the new files each task names.
- The classic floor must not move: `Warehouse()`, `DigitalTwin()` and every existing fixture stay classic, with an unchanged grid, zones, seed, battery model, wall-clock shifts, AUTO scoring, `PICK_TICKS`/`DELIVER_TICKS`, and event data. A robot with no floor profile (`robot.mobility is None`) behaves exactly as today. The classic dashboard looks the same.
- Lock order is `twin.lock` → inventory store lock. Every HTTP handler that reads or changes the shift, orders, stock, people, equipment or faults holds `twin.lock`.
- Spec values used verbatim: soak `distribution_center`, seed 42, `pace` 2, 20 000 ticks, faults off; mean tick ≤ 15 ms with 15 robots; each risk at 0.2 for the fault matrix; seed goods 60 pallets on racks at levels 0–4 (150–900 kg), 80 totes on shelves at levels 0–2 (5–25 kg, one SKU each), 40 SKUs; 15 non-retired robots (14 in service; AST-000103 is held by its open work order); 10 workers, 8 on the floor (Sasha `ON_LEAVE`, Morgan `TERMINATED`); robot letters A AMR, F forklift, H hauler, K picker, S scout, D drone, R arm, U humanoid; phone width 375 px; API errors 404 unknown id, 409 state conflict, 400 validation.
- Style: match the surrounding code — `from __future__ import annotations`, `typing` annotations, a module docstring on every new module, comments that say why, plain-language messages. Bad input raises `ValueError`; an unknown robot, operator, order or task id raises `KeyError`. Frontend code is ES5 like `frontend/app.js` (`var`, `function`, no modules, no arrow functions).
- Stage only the files your task lists (`git add <paths>`, never `git add -A`): app and test runs rewrite `logs/tasks/task_00*.json`, which are not yours.
- Every commit message ends with exactly this line: `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`
- Edits are given as exact **Replace in** blocks: each block's old text matches the file exactly once at the moment it is applied, in task order (use the Edit tool with it as `old_string`). **Create** blocks give a new file's whole content. **Rewrite** blocks replace a whole existing file.

## Plan rulings

Choices this plan makes where the spec is ambiguous, silent or self-contradictory, or where the code moved since the spec. Each can be changed at plan review.

1. **The app's default floor is `distribution_center`** (§2, §1's first goal). `WAREHOUSE_LAYOUT=classic` still boots the classic floor.
2. **Per-floor data folders stay.** The new floor keeps `data/distribution_center/` (its inventory file and state save) and `logs/distribution_center/`; classic keeps `data/` and `logs/`. §2 names `data/inventory.sqlite3` for the new floor; separate folders mean switching floors never reseeds the other floor's inventory file, which also retires plan 1a's "timestamped backups" item.
3. **`backend/test_app_layout.py` is rewritten by Task 2.** It pinned the pre-plan stopgap (a 12-robot floor, a running shift, a classic default) that §2, §4.2 and §11.5 replace. Its still-true assertions (separate folders, an unknown layout refused before anything is written, a second boot reuses the file) are kept.
4. **`DigitalTwin(layout="distribution_center")` still boots empty.** Plan 1b's test files build their own floors through the public API and can't be edited. The seed runs through `DigitalTwin.seed_floor()` (the app calls it); `reset()` reruns the seed only on a twin that was seeded.
5. **A save from another floor is refused** (`ValueError` naming both floors) instead of rebuilding the floor (§4.3). The inventory profile is per floor, so a classic save's robots have no assets to bind to on the new floor. A v1 save is a classic save.
6. **The shift starts paused** (§11.5, spec ruling 10) from Task 2 on. `POST /api/shift/start` (Task 5) and the shift panel's Start (Task 10) start it.
7. **No order-book pruning or indexing.** Measured on the pre-plan floor (12 robots, seed 42, pace 2): a mean tick of 0.51 ms over 20 000 ticks, against the 15 ms budget. Task 11 records the 15-robot figure.
8. **No order stall timeout.** Plan 1b's ruling stands: every wait has a clearer. Task 7's tote-reach fix removes the one long hold the plan 1b soak found.
9. **The shift panel has an "Inject fault" control** (a kind select and a button calling `POST /api/faults/<kind>`). §16's browser check 5 jams the conveyor from the browser, and §12 lists no control for it.
10. **Robot letters only on robots with a floor profile.** Classic robots draw exactly as today.
11. **GRASP_FAIL and CONVEYOR_JAM have no dedicated §10.4 check.** In the fault matrix (Task 11) their "matching check" is the evidence that already records them: a GRASP_FAIL ends in a FAILED task with the grasp reason, and a jam emits `CONVEYOR_JAMMED` and raises a `CLEAR_JAM`. Task 11 asserts exactly that.
12. **A technician's "work order"** (§11.3) is an open inventory work order on the asset of a robot on the floor. An on-shift technician with no jam to clear walks to that robot's zone.
13. **The plan-1b minors not named in a task stay recorded debt** — see **Not in this plan**.
14. **TR50-202 stays idle by design in step 1.** It reports the recalled release 2.2.1 (§4.1), so the gate refuses every job it is offered — sub-project A's recall scenario working as intended. The seed places it on a cell no job needs; step 2 gives that release its odometry drift.
15. **Seed placement avoids cells jobs need.** A robot starts on the first free cell of its home zone that no job needs (`Warehouse.cells_jobs_need`), else the nearest such cell. The literal "first free cell of its home zone" put TR50-202 on a slot face it could never leave and wedged the shift.
16. **Only aisle zones marked NARROW (and walkway crossings) join the cells idle robots leave**, not every NARROW cell: `returns_qc` is NARROW and is the humanoid's home, which it can't leave without its supervisor.
17. **The save writes `Robot.to_state()`, not `to_dict()`.** `to_dict()` rounds battery, altitude and lift height for display and leaves out the motion and step timers, so a loaded floor wouldn't carry on as the original did.
18. **The simulator's own retry timers are not saved** (`_charge_retry`, `_park_retry`, `_idle_since`, `_announced`): they pace the running loop, which a load keeps. After a load a charge or park retry may come up to `SHIFT_CHECK_EVERY_TICKS` sooner or later.
19. **Armed one-shot faults are not saved; a load clears them, as `reset()` does.** §4.3 doesn't list them, and a fault armed against one floor shouldn't fire on a loaded one. Task 5 adds the clear (it owns `faults.py`), with the shift panel's exceptions list.
20. **No targeted jam.** `POST /api/faults/conveyor_jam` arms a one-shot that jams the cell an item next leaves; it takes no cell. §16's browser check 5 ("CLEAR_JAM goes to Mateo, not Riley") needs the jam on an arm's working cell, (22,15) or (23,15), so the check injects until one lands there.
21. **The robot panel helper takes the snapshot's tasks** (`robotPanel(robot, tasks)`): the job type and step live on the task, not the robot.
22. **A jam nobody can clear is asked for twice, then checked silently.** An existing test (`test_station_jobs.py::test_a_jam_no_one_can_clear_is_asked_for_again_until_someone_can`) pins two rejected CLEAR_JAMs, so the gate is asked once plus its one retry; after that, `ensure_jam_jobs` checks silently until someone qualified is on shift.
23. **A misplaced box carries its real contents** (`Box.true_quantity`, saved with the box): Task 3 pins the recorded slot's true quantity at 0, so `physical_qty` counts the box itself.
24. **Jordan keeps the humanoid supervised rather than walking to its destination.** A person walking is in no zone, so heading straight for the destination left the humanoid unsupervised on every route tried. Jordan stays while he supervises it; otherwise he goes to the zone along its route that keeps it supervised farthest towards its destination.
25. **A cell in no zone counts as next to the zones beside it** (`people.zones_around`) for supervision. Without it a humanoid on such a cell, (3,11) for one, was never supervised and ran flat — a pre-plan bug the 60 000-tick seeded run found.
26. **One read-only inventory query, `open_work_orders()`**, joins `backend/inventory/servicing.py` (§2 says only the seed and `known_issues` change there): reading work orders robot by robot cost about 0.22 ms a tick. No schema change.
27. **A docked drone stays CHARGING at 100 %.** Going IDLE at full charge would start a new charging session every other tick; the dashboard shows docked drones as charging.
28. **Tote preference is among free totes.** A free tote only the humanoid can reach is taken rather than waiting for a busy one the AMRs can reach; a tote no robot could ever reach is still rejected at the gate with its reason (an existing hardening test pins that).


## File map

| File | Task | Responsibility |
|---|---|---|
| `backend/warehouse.py` | 1, 5 | Cells jobs need (NARROW aisles, crossings); the cell-type table and no-fly cells |
| `backend/task_manager.py` | 1, 8 | Set-downs on a stoppable cell, a picker's unit put back; a charging drone's fleet reason |
| `backend/task_planner.py` | 1 | Older job types and the charge detour avoid robots as targets, not as walls |
| `backend/goods.py` | 1, 3, 7 | `StockLedger.return_units`; `from_dict` through the ledger's checks; a misplaced box's true count |
| `backend/seeds/` | 2 (create `classic.py`; rewrite `__init__.py`, `distribution_center.py`) | The classic demo moved verbatim; the new floor's full seed; `SEEDS` |
| `backend/digital_twin.py` | 2–6 | `seed_floor`, `floor_seeded`, spawning by the stop rule; save/load v2 (floor, then shift); the live snapshot; layout-aware options and the job form tables |
| `backend/fleet_bridge.py` | 2 | No remote check-ins on the new floor (§4.4) |
| `backend/app.py` | 2, 5, 6 | The new-floor default, `build_twin` and the seed; the operations routes' registration; the new jobs' fields through `POST /api/tasks` |
| `run.sh` | 2 | Prints the floor it boots |
| `backend/robot.py`, `backend/operator.py` | 3 | `Robot.to_state`; restoring and checking robots and people |
| `backend/equipment.py` | 3, 5, 7 | Equipment state save/load; `Equipment.view`; guards on boxes that left the floor |
| `backend/operations/orders.py` | 4, 5, 7 | Orders save/load; `get`, numeric sort; choosing a reachable tote |
| `backend/operations/shift.py` | 4, 5 | Shift engine save/load; `panel()` and its exceptions; an all-or-nothing `configure` |
| `backend/operations_api.py` | 5 (create) | `/api/shift`, `/api/orders`, `/api/stock`, `/api/people`, `/api/equipment`, `/api/faults/<kind>` |
| `backend/layouts/base.py` | 5 | `CELL_TYPE_STYLES`, `Layout.cell_type_table` |
| `backend/faults.py` | 5 | `fault_kind`; `arm`'s count check |
| `backend/agent_tools.py` | 6 | The new jobs' fields in the agent's create-task tool and guide |
| `backend/human_jobs.py` | 7 | A jam nobody can clear is checked silently |
| `backend/simulator.py` | 7, 8 | `equipment.tick()` contained; dock charging for drones |
| `backend/box.py` | 7 | `Box.true_quantity` |
| `backend/people.py` | 8 | `zones_around` for supervision |
| `backend/inventory/servicing.py` | 8 | `open_work_orders()` (read-only) |
| `backend/operations/activities.py` | 8 | Step-aside timing, Jordan's follow, duty before movement, technicians' work orders |
| `frontend/floor_model.js` | 9 (create), 10 | Pure view-model helpers the dashboard draws from |
| `frontend/app.js`, `index.html`, `style.css` | 9, 10 | Layout-driven floor, glyphs, air layer, arms, people, conveyor; robot and shift panels, job forms, phone width |
| `frontend/fleet.js` | 10 | "On the floor" links |
| `frontend/tests/*.js` | 9, 10 (create) | `jsc` unit tests for `floor_model.js` |
| `backend/soak.py` | 11 (create) | The §16 soak runner and CLI: the seeded floor at a fixed seed, tick timing, and the job checks over the run |
| `README.md`, `RUN.md`, `TRUST_LAYER.md`, `policies.example.yaml` | 12 | Rewritten short (README ~165 lines, RUN.md ~100, TRUST_LAYER.md ~130): the new floor, robot types, shift, faults, one API table, `WAREHOUSE_LAYOUT`, dashboard panels and soak, linking ARCHITECTURE.md instead of repeating it; the example policy matches the built-in one |
| `docs/superpowers/specs/2026-09-30-multi-embodiment-operations-design.md` | 12 | §2 per-floor folders (ruling 2), §4.3 a save from another floor is refused (ruling 5), §6 walkway crossing decided by cells |
| `backend/test_*.py` | 1–12 (create); `test_app_layout.py` rewritten in 2 | One new test file per task, plus the `jsc` wrappers |

## Carried forward

Every item plans 1a and 1b handed to plan 1c, and where it is handled.

**Plan 1a, "To plan 1c":**

| Item | Where |
|---|---|
| Save/load v2 of robots: `wait_reason`, `wait_started_tick`, `layer` and `altitude_m` validation | Task 3 |
| Save/load v2 of operators and stock: deep-copied `certification_scopes`, zone/transit consistency, `StockLocation(**item)` | Task 3 |
| Arm spawning by home zone | Task 2 |
| Explicit spawn positions checked with the stop rule | Task 2 |
| Timestamped inventory backups | Plan ruling 2 (per-floor files: no floor flips one file any more) |
| Layout switching on load; every layout name is a seed profile | Plan ruling 5, Task 3 |

**Plan 1b, "Carried forward to plan 1c":**

| Item | Where |
|---|---|
| Seed: shift windows that include 06:00; arms by home zone; tote stock covering the customer-order generator | Task 2 |
| Save/load v2: ledger with `Box.true_slot`, equipment (items with `stop_at`, jams, sorter cartons, hand-off sequence), shift engine and orders, robot and operator fields; the engine's single event subscription | Tasks 3, 4 |
| Operations API over `status_dict`/`start`/`pause`/`configure`, `orders.list`, `stock.locations`, `equipment.to_dict`, `faults.arm`; the orders panel's exceptions | Task 5 |
| Dashboard: conveyor items, orders and people in the snapshot; the job types' form fields | Tasks 5, 6, 9, 10 |
| Soak: measure the tick; breaks start after two sim-hours (browser check 4 runs at 10× speed); WRONG_LEVEL's matching check | Task 11, Plan ruling 7 |

**Plan 1b close-out (`docs/handoffs/2026-10-04-plan-1c-inputs/`):**

| Item | Where |
|---|---|
| C3(b): the x=18 gap in `cells_jobs_need` (ruling 30) | Task 1 |
| Set-downs on walkway crossings (ruling 31) | Task 1 |
| An early-ended PICK_ITEMS loses a unit (ruling 33) | Task 1 |
| Older job types and the charge detour treat robots as walls at plan time | Task 1 |
| (b1): PICK_BOX then a cancelled DELIVER_BOX leaves the robot holding the box | Task 1 |
| M3: the agent tool lacks the new jobs' fields | Task 6 |
| M4: `physical_qty` of a misplaced box | Task 7 |
| `_choose_tote` ignores reach (the 30-minute hold on a level-2 tote) | Task 7 |
| A FAILED CLEAR_JAM every check while nobody qualified is on shift | Task 7 |
| Sorter and `take()` lack `find_box` guards; `equipment.tick()` errors aren't contained | Task 7 |
| `load_state` leaves `twin.stock` and `twin.equipment` stale; the private `_handoff_seq` | Task 3 |
| The drone energy dead zone (ruling 22: dock charging) | Task 8 |
| The step-aside hold timing; Jordan's reactive follow; `move_people` before `sync_duty` | Task 8 |
| Technicians' work orders (§11.3) | Task 8, Plan ruling 12 |
| Classic `options()` lists the new job types (ruling 16) | Task 6 |
| No order stall timeout (ruling 18) | Plan ruling 8 |
| The order book scanned every tick | Plan ruling 7 |
| GRASP_FAIL and CONVEYOR_JAM have no dedicated check | Task 11, Plan ruling 11 |
| The soak probe's 5000-event buffer | Task 11 |
| `test_shift_soak.py:9`'s wrong reason for determinism (ruling 35) | Task 11 (its own docstring restates the reason) |
| `policies.example.yaml` lacks the new risks and CLEAR_JAM | Task 12 |
| Spec §6 "Movement" still says zone centres decide walkway crossing | Task 12 |

## Not in this plan

Plan 1b's remaining deferred minors are recorded debt, not plan 1c work: they are test gaps, message wording, duplications and edge cases no shift path reaches. The list, verbatim, is "Every deferred or parked minor" in `docs/handoffs/2026-10-04-plan-1c-inputs/README.md`, less the items **Carried forward** names. One parked item is closed as benign by ruling: a park MOVE_ROBOT can run after an AUTO job took the robot first, which costs one extra trip to parking (plan 1b ruling 34).

---

### Task 1: Plan 1b's load-bearing residuals — the x=18 gap, crossing set-downs, a picker's unit, and old jobs in traffic

Plan 1b's final re-review (`docs/handoffs/2026-10-04-plan-1c-inputs/final-rereview-report.md`, finding C3(b), New Breakage #1 and #2, scope decision (b1), out-of-scope #2) left five seams on the distribution-centre floor, each ruled in `ledger-1b.md` for plan 1c. They come first, because Task 11's soak arms faults and each one is permanent and silent when it happens.

1. **The x=18 gap (C3(b)).** `Warehouse.cells_jobs_need` found one-lane cells by geometry only, so the top aisle's (18,13)–(18,17) — which have a drivable neighbour on both axes, a station pocket or a crossing — were left out. (18,14) and (18,16) are the only way to Pick 1's and Pick 2's tote drops (§3.2), so an idle robot left there (its job ended mid-route) wedges the station for good (`probes/x18_probe.py`: the tote job is still BLOCKED after 3000 ticks). Now every cell of an aisle zone the layout marks `NARROW` is a cell jobs need, and so is every walkway crossing (§5.2: no robot may stop on one, and the two WIDE crossings weren't one-lane either). A NARROW *place* — `returns_qc`, where the humanoid lives, and the NARROW docks — is not an aisle and stays out.
2. **Set-downs on a crossing (amends the C1 ruling).** A job that ends early set its load down on the robot's own cell. On a walkway crossing no robot may stop (§5.2), so no job could ever fetch it: a tote stranded at (17,13) refused every RETURN_TOTE and silently blocked every later order for its SKU (`probes/crossing_probe.py`, `crossing_probe_h1.py`); forklifts strand pallets the same way at (17,10)/(17,11). Now the load goes to the nearest cell the body may stop on (`Warehouse.may_stop`, through `nearest_walkable`) that no other robot holds — the robot's own cell everywhere but on a crossing.
3. **A DELIVER_BOX after a PICK_BOX (the (b1) caveat).** The set-down only took a box tagged with the ending job. PICK_BOX then DELIVER_BOX leaves the box tagged with the completed PICK_BOX, so a cancelled DELIVER_BOX left the robot holding it for good — never charging or parking (`probes/b1_probe.py`). Now the job's own box (`box_id`/`box_ids`) counts as its load too.
4. **A picker's unit (New Breakage #2).** An early-ended PICK_ITEMS set its unit down on the work cell, after `goods.take_units` had already taken it out of the tote (§7.2): floor debris, and the tote one short for good. Now the picker puts the unit back into the tote its GRASP took it from (`StockLedger.return_units`: recorded and true quantity back up, weights back), and the ITEM leaves `twin.boxes`; with that tote gone, out of the ledger or out of reach, the ITEM fails where the picker stands.
5. **Older job types in traffic.** C2 made the new job types plan their targets from the layout alone; PICK_AND_DELIVER, MOVE_BOX, BATCH_DELIVER, PICK_BOX, DELIVER_BOX, MOVE_ROBOT, MIXED_MAINTENANCE_MISSION, CHARGE_ROBOT and the battery detour still passed other robots' cells to `resolve_target` as walls, so a robot in a one-lane aisle failed them at plan time ("Parking is not reachable right now"). Now, on the new floor, the planner passes them as `avoid`: never the cell chosen for a zone target, but a route may pass them (execution waits, replans or sidesteps). A full zone still fails at plan time, and classic planning is unchanged.

A robot with no floor profile behaves exactly as before throughout: `_set_down_load` returns at once for it, and the planner keeps its `blocked`.

**Files:**
- Modify: `backend/warehouse.py` (`cells_jobs_need`)
- Modify: `backend/task_manager.py` (the `models` import, `_set_down_load`, new `_put_unit_back`)
- Modify: `backend/goods.py` (the `Box` import, new `StockLedger.return_units`)
- Modify: `backend/task_planner.py` (`resolve_target`'s `avoid`, `plan`'s `blocked`/`route`)
- Test: `backend/test_traffic_residuals.py` (create)

**Interfaces:**
- Consumes (plans 1a/1b): `Warehouse.may_stop`, `Warehouse.nearest_walkable(cell, blocked, profile=, layer=)`, `Warehouse.crossings`, `Zone.cell_type`/`attributes["clearance"]`; `twin.other_robot_cells(robot_id)`; `StockLedger.restock`; the PICK_ITEMS GRASP step's `params["unit_from"]` (the tote's id, `jobs._plan_pick_items`); `Simulator._park_idle` (unchanged: it reads `cells_jobs_need`); `OrderBook._fail` → `_send_tote_home` (unchanged).
- Produces:
  - `Warehouse.cells_jobs_need() -> FrozenSet[Cell]` also holds every cell of an `EMPTY`-typed zone whose `clearance` is `NARROW` (the tote aisles, the top aisle, `ne_floor`) and every walkway crossing.
  - `StockLedger.return_units(tote: Box, units: int) -> None` — `ValueError` for a box that isn't a TOTE, a tote not in the ledger, or `units` that isn't a whole number of 1 or more; sets `tote.quantity` to the new recorded quantity. The caller restores the weights.
  - `TaskPlanner.resolve_target(spec, origin, blocked=None, prefer_free=False, profile=None, layer=GROUND, avoid=None)` — `avoid: Optional[Set[Cell]]`, cells never chosen but routable.
  - `TaskManager._set_down_load(task, robot)` (same signature) and `TaskManager._put_unit_back(task, robot, item, tote) -> None`.

- [ ] **Step 1: Write the failing tests**

**Create** `backend/test_traffic_residuals.py`:

```python
"""Plan 1b's load-bearing residuals on the distribution-centre floor
(multi-embodiment spec §5.2, §6, §7, §9; plan 1b's final-fix-wave rulings).

- Every cell of a one-lane (NARROW) aisle is a cell jobs need, so an idle
  robot left on the top aisle's x=18 column — the only way to each pick
  station's tote drop — moves to parking instead of wedging the station.
- A load set down by a job that ends early goes to the nearest cell the body
  may stop on, never a walkway crossing, so it can be fetched again; that
  includes a box the robot already held when the job began (a DELIVER_BOX
  after a PICK_BOX).
- A picker's unit goes back into the tote it came from when its pick ends
  early, or fails if that tote can't take it.
- The older job types and the charge detour plan through other robots'
  cells instead of failing on traffic.

Classic robots (no floor profile) behave exactly as before.
"""
import pytest

from backend.digital_twin import DigitalTwin
from backend.jobs import tote_is_home
from backend.models import CONFIG, BoxStatus, SimulationStatus, TaskStatus
from backend.simulator import Simulator
from backend.task_planner import PlanningError

CHECK = CONFIG["SHIFT_CHECK_EVERY_TICKS"]


@pytest.fixture
def twin(tmp_path):
    return DigitalTwin(log_dir=str(tmp_path / "logs"), data_dir=str(tmp_path / "data"),
                       persist_logs=False, demo=True, demo_tasks=False, layout="distribution_center")


@pytest.fixture
def sim(twin):
    simulator = Simulator(twin)
    twin.simulation_status = SimulationStatus.RUNNING
    return simulator


def ticks(sim, count):
    for _ in range(count):
        sim.tick()


def tick_until(sim, condition, max_ticks=3000):
    for _ in range(max_ticks):
        if condition():
            return
        sim.tick()
    assert condition(), f"not reached within {max_ticks} ticks"


def tasks_of(twin, task_type, robot=None):
    return [task for task in twin.tasks.tasks.values() if task.type.value == task_type
            and (robot is None or task.robot_id == robot.id)]


def busy_elsewhere(twin):
    """Work on the floor: a forklift charging at the charging station (a long job)."""
    fork = twin.add_robot(name="PF1200-205", asset_id="AST-000205", position=(2, 13))
    fork.battery = 10.0
    return twin.request_charge(fork.id)


# --------------------------------------------------------------------------- #
# The x=18 gap: every cell of a one-lane aisle is a cell jobs need
# --------------------------------------------------------------------------- #
def test_the_whole_top_aisle_column_and_every_crossing_are_cells_jobs_need(twin):
    need = twin.warehouse.cells_jobs_need()
    # (18,13)-(18,17) have a drivable neighbour on both axes (a station pocket,
    # a crossing), so the one-lane geometry alone missed them.
    assert all((18, y) in need for y in range(12, 19))
    assert all(cell in need for cell in twin.warehouse.crossings)
    # returns_qc is NARROW too, but a place, not an aisle: the humanoid's home.
    assert not [cell for cell in twin.warehouse.zones["returns_qc"].cells if cell in need]


@pytest.mark.parametrize("squat, station, drop", [((18, 14), "pick_station_1", (19, 14)),
                                                  ((18, 16), "pick_station_2", (19, 16))],
                         ids=["Pick 1", "Pick 2"])
def test_an_idle_robot_on_a_stations_only_access_moves_away(twin, sim, squat, station, drop):
    squatter = twin.add_robot(name="TR50-101", asset_id="AST-000101", position=squat)   # its job ended there
    worker = twin.add_robot(name="TR50-201", asset_id="AST-000201", position=(8, 15))
    tote = twin.add_box(name="TOTE-1", kind="TOTE", sku="S1", quantity=20, weight=21.5, slot="TS-11-12-0")
    job = twin.tasks.create_task({"type": "TOTE_TO_STATION", "box_id": tote.id, "robot_id": worker.id,
                                  "station": station})
    # Without the NARROW cells in cells_jobs_need the job waits behind the
    # squatter for good (BLOCKED, no MOVE_ROBOT ever issued).
    tick_until(sim, lambda: job.is_terminal)
    assert job.status is TaskStatus.COMPLETED, job.error
    assert tote.position == drop
    moves = tasks_of(twin, "MOVE_ROBOT", squatter)
    assert moves and moves[0].internal and moves[0].destination == "parking_area"
    assert squatter.position != squat


def test_an_idle_humanoid_at_home_in_returns_qc_stays_there(twin, sim):
    busy_elsewhere(twin)
    humanoid = twin.add_robot(name="H1-212", asset_id="AST-000212", position=(22, 7))
    ticks(sim, 3 * CHECK)
    # Would fail if every NARROW cell (returns_qc's included) were a cell jobs need.
    assert not tasks_of(twin, "MOVE_ROBOT", humanoid) and humanoid.position == (22, 7)


# --------------------------------------------------------------------------- #
# Set-downs: never on a walkway crossing
# --------------------------------------------------------------------------- #
def order_floor(twin):
    """Two AMRs, the picker, both arms and one tote for each of six SKUs."""
    for name, asset, cell in (("TR50-201", "AST-000201", (8, 13)), ("TR50-101", "AST-000101", (8, 15)),
                              ("PK30-203", "AST-000203", (20, 14))):
        twin.add_robot(name=name, asset_id=asset, position=cell)
    twin.add_robot(name="CX10-210", asset_id="AST-000210")
    twin.add_robot(name="CX10-211", asset_id="AST-000211")
    for number in range(6):
        twin.add_box(name=f"TOTE-{number}", kind="TOTE", sku=f"SKU-00{number + 1}", quantity=20, weight=21.5,
                     slot=f"TS-{8 + number:02d}-12-0")


def carrier_of(twin, box):
    return next((robot for robot in twin.robots.values() if robot.carrying_box == box.id), None)


def on_a_crossing(twin, box):
    carrier = carrier_of(twin, box)
    return carrier is not None and twin.warehouse.is_crossing(carrier.position)


def test_a_tote_leg_ended_on_a_crossing_sets_the_tote_down_off_the_walkway(twin, sim):
    order_floor(twin)
    order = twin.shift.orders.customer([{"sku": "SKU-002", "units": 1}], "dock_4")
    tote = twin.find_box("TOTE-1")
    tick_until(sim, lambda: on_a_crossing(twin, tote))
    carrier = carrier_of(twin, tote)
    twin.shift.orders._fail(order, "the test failed it on the crossing")   # cancels the tote leg
    assert carrier.carrying_box is None and tote.status is BoxStatus.STORED
    # Set down where the AMR stood, the tote sat on (17,13): no robot may stop
    # there, so its RETURN_TOTE was refused ("has no route to the job").
    assert not twin.warehouse.is_crossing(tote.position)
    assert twin.warehouse.may_stop(tote.position, carrier.mobility)
    assert tote.slot == "TS-09-12-0" and twin.stock.box_in("TS-09-12-0") == tote.id
    back = tasks_of(twin, "RETURN_TOTE")
    assert len(back) == 1 and back[0].status is not TaskStatus.FAILED, back[0].error
    tick_until(sim, lambda: back[0].is_terminal)
    assert back[0].status is TaskStatus.COMPLETED, back[0].error
    assert tote_is_home(twin, tote)


def test_a_pallet_leg_failed_on_a_crossing_sets_the_pallet_down_off_the_walkway(twin, sim):
    fork = twin.add_robot(name="PF1200-205", asset_id="AST-000205", position=(7, 4))
    pallet = twin.add_box(name="PAL-1", kind="PALLET", sku="SKU-P", quantity=10, weight=300.0, slot="PR-08-02-0")
    job = twin.tasks.create_task({"type": "RETRIEVE_PALLET", "box_id": pallet.id})
    assert job.status is TaskStatus.PLANNING, job.error
    tick_until(sim, lambda: on_a_crossing(twin, pallet))          # (17,10) or (17,11), the wide crossings
    twin.tasks.fail_task(job, "the forklift lost its way")
    assert fork.carrying_box is None and pallet.status is BoxStatus.STORED and pallet.slot is None
    assert not twin.warehouse.is_crossing(pallet.position)
    assert twin.warehouse.may_stop(pallet.position, fork.mobility)
    again = twin.tasks.create_task({"type": "LOAD_TRUCK", "box_id": pallet.id})
    assert again.status is TaskStatus.PLANNING, again.error       # it can be fetched from where it lies


def test_a_cancelled_deliver_after_a_pick_box_sets_the_box_down(twin, sim):
    amr = twin.add_robot(name="TR50-201", asset_id="AST-000201", position=(8, 11))
    box = twin.add_box(name="BOX-1", kind="TOTE", sku="S", quantity=1, weight=5.0, position=(12, 11))
    pick = twin.tasks.create_task({"type": "PICK_BOX", "robot_id": amr.id, "box_id": box.id})
    tick_until(sim, lambda: pick.is_terminal)
    assert pick.status is TaskStatus.COMPLETED and amr.carrying_box == box.id
    deliver = twin.tasks.create_task({"type": "DELIVER_BOX", "robot_id": amr.id, "box_id": box.id,
                                      "destination": "outbound_staging"})
    ticks(sim, 10)
    twin.tasks.cancel_task(deliver.id)
    # The box is still tagged with the completed PICK_BOX; a set-down that
    # only looks at box.assigned_task leaves the AMR holding it for good.
    assert amr.carrying_box is None and box.status is BoxStatus.STORED
    assert box.assigned_robot is None and box.assigned_task is None
    assert box.position == amr.position
    move = twin.tasks.create_task({"type": "PICK_AND_DELIVER", "box_id": box.id, "destination": "intake_staging"})
    tick_until(sim, lambda: move.is_terminal)
    assert move.status is TaskStatus.COMPLETED, move.error


# --------------------------------------------------------------------------- #
# A picker's unit goes back into its tote
# --------------------------------------------------------------------------- #
def picker_holding_a_unit(twin, sim):
    picker = twin.add_robot(name="PK30-203", asset_id="AST-000203", position=(20, 14))
    tote = twin.add_box(name="TOTE-1", kind="TOTE", sku="SKU-02", quantity=6, weight=7.5, slot="TS-10-12-1")
    tote.position = (19, 14)                                     # brought to Pick 1's tote drop
    tote.set_status(BoxStatus.DELIVERED)
    weights = (tote.declared_weight_kg, tote.true_weight_kg)
    pick = twin.tasks.create_task({"type": "PICK_ITEMS", "box_id": tote.id, "quantity": 2})
    assert pick.status is TaskStatus.PLANNING, pick.error
    tick_until(sim, lambda: picker.carrying_box is not None)
    item = twin.find_box(picker.carrying_box)
    assert item.kind.value == "ITEM" and twin.stock.location("TS-10-12-1").recorded_qty == 5
    return picker, tote, pick, item, weights


def test_a_pick_cancelled_with_a_unit_in_hand_puts_it_back_in_its_tote(twin, sim):
    picker, tote, pick, item, weights = picker_holding_a_unit(twin, sim)
    twin.tasks.cancel_task(pick.id)
    # Before the fix the unit lay STORED on the work cell and the tote stayed one short.
    assert picker.carrying_box is None and item.id not in twin.boxes
    location = twin.stock.location("TS-10-12-1")
    assert (location.recorded_qty, location.true_qty, tote.quantity) == (6, 6, 6)
    assert (tote.declared_weight_kg, tote.true_weight_kg) == pytest.approx(weights)


def test_a_unit_whose_tote_has_gone_fails(twin, sim):
    picker, tote, pick, item, _ = picker_holding_a_unit(twin, sim)
    tote.position = (19, 16)                                     # taken away while the picker held the unit
    twin.tasks.fail_task(pick, "the picker lost power")
    assert picker.carrying_box is None and item.status is BoxStatus.FAILED
    assert item.id in twin.boxes and item.position == picker.position
    location = twin.stock.location("TS-10-12-1")
    assert (location.recorded_qty, location.true_qty) == (5, 5)   # the unit is out of the tote for good


def test_only_a_tote_in_the_ledger_takes_units_back(twin):
    tote = twin.add_box(name="TOTE-1", kind="TOTE", sku="SKU-02", quantity=6, weight=7.5, slot="TS-10-12-1")
    loose = twin.add_box(name="TOTE-2", kind="TOTE", sku="SKU-02", quantity=6, weight=7.5, position=(21, 7))
    pallet = twin.add_box(name="PAL-1", kind="PALLET", sku="SKU-P", quantity=10, weight=300.0, slot="PR-08-02-0")
    with pytest.raises(ValueError, match="TOTE-2 is not in the stock ledger"):
        twin.stock.return_units(loose, 1)
    with pytest.raises(ValueError, match="PAL-1 is a PALLET, not a TOTE"):
        twin.stock.return_units(pallet, 1)
    with pytest.raises(ValueError, match="units must be a whole number of at least 1"):
        twin.stock.return_units(tote, 0)
    twin.stock.return_units(tote, 2)
    assert tote.quantity == 8 and twin.stock.location("TS-10-12-1").true_qty == 8


# --------------------------------------------------------------------------- #
# The older job types plan through traffic
# --------------------------------------------------------------------------- #
def pocketed(twin):
    """An AMR in Pick 1's pocket, (19,14), and an idle AMR on (18,14), its only way out."""
    amr = twin.add_robot(name="TR50-201", asset_id="AST-000201", position=(19, 14))
    twin.add_robot(name="TR50-101", asset_id="AST-000101", position=(18, 14))
    return amr


@pytest.mark.parametrize("payload, zone", [
    ({"type": "MOVE_ROBOT", "destination": "parking_area"}, "parking_area"),
    ({"type": "CHARGE_ROBOT"}, "charging_station"),
    ({"type": "PICK_AND_DELIVER", "box_id": "BOX-1", "destination": "intake_staging"}, "intake_staging"),
], ids=["MOVE_ROBOT", "CHARGE_ROBOT", "PICK_AND_DELIVER"])
def test_an_older_job_plans_through_a_robot_in_the_way(twin, sim, payload, zone):
    amr = pocketed(twin)
    twin.add_box(name="BOX-1", kind="TOTE", sku="S", quantity=1, weight=5.0, position=(20, 14))
    job = twin.tasks.create_task({**payload, "robot_id": amr.id})
    sim.tick()
    # With other robots' cells as walls this failed at once: "<zone> is not reachable right now".
    assert job.status is not TaskStatus.FAILED, job.error
    tick_until(sim, lambda: job.is_terminal)                     # the idle AMR parks; then it goes
    assert job.status is TaskStatus.COMPLETED, job.error
    end = job.actions[-2].target                                 # the last step before COMPLETE
    assert twin.warehouse.zone_of_cell(end).key == zone


def test_the_charge_detour_plans_through_a_robot_in_the_way(twin):
    amr = pocketed(twin)
    amr.battery = 3.0                                            # below the reserve: it must charge first
    job = twin.tasks.create_task({"type": "MOVE_ROBOT", "robot_id": amr.id, "destination": "20,14"})
    actions = twin.planner.plan(job, amr)                        # raised "Charging is not reachable right now"
    assert actions[0].description == "Detour to Charging"
    assert twin.warehouse.zone_of_cell(actions[0].target).key == "charging_station"


def test_a_zone_cell_another_robot_stands_on_is_never_the_one_chosen(twin):
    amr = twin.add_robot(name="TR50-201", asset_id="AST-000201", position=(7, 13))
    twin.add_robot(name="TR50-101", asset_id="AST-000101", position=(6, 13))   # parking's nearest cell
    job = twin.tasks.create_task({"type": "MOVE_ROBOT", "robot_id": amr.id, "destination": "parking_area"})
    # Would fail if the planner dropped other robots' cells altogether
    # instead of only letting routes pass them.
    target = twin.planner.plan(job, amr)[0].target
    assert target != (6, 13) and twin.warehouse.zone_of_cell(target).key == "parking_area"


def test_a_full_zone_still_fails_at_plan_time(twin):
    amr = twin.add_robot(name="TR50-201", asset_id="AST-000201", position=(7, 13))
    for number, cell in enumerate(twin.warehouse.zones["parking_area"].cells):
        twin.add_robot(name=f"Parked-{number}", model_code="AC-TR50", position=cell)
    job = twin.tasks.create_task({"type": "MOVE_ROBOT", "robot_id": amr.id, "destination": "parking_area"})
    with pytest.raises(PlanningError, match="Parking is not reachable right now"):
        twin.planner.plan(job, amr)
```


- [ ] **Step 2: Run them to see them fail**

Run: `.venv/bin/python -m pytest -o addopts="" -q backend/test_traffic_residuals.py`
Expected: `13 failed, 3 passed` — `assert all(...)` for (18,13)–(18,17); `not reached within 3000 ticks` for both station wedges; `assert not True` (`is_crossing((17, 13))`, `is_crossing((17, 10))`) for the tote and the pallet left on a crossing; `'box_001' is None` for the box still held after the cancelled DELIVER_BOX; the unit still in `twin.boxes` (and STORED, not FAILED); `AttributeError: 'StockLedger' object has no attribute 'return_units'`; and `Parking/Charging/Intake staging is not reachable right now` for the four older-job tests. The three guards that pass (the humanoid stays home, an occupied zone cell is never chosen, a full zone still fails) keep the fix from overreaching.

- [ ] **Step 3: Every cell of a one-lane aisle is a cell jobs need**

**Replace in** `backend/warehouse.py`:

```python
        every slot's face cells, every station's tote drop and work cell, and
        every one-lane cell — a drivable cell whose two neighbours along one
        axis are both off-limits to ground robots, so a robot stopped there
        blocks the lane (the tote aisles, the top aisle, the walkway
        crossings). Worked out once, on first use."""
```

with:

```python
        every slot's face cells, every station's tote drop and work cell, every
        cell of an aisle the layout marks NARROW (a one-lane aisle, side
        pockets and all: the top aisle's x=18 column is the only way to each
        pick station's tote drop), every walkway crossing (no robot may stop
        on one), and every other one-lane cell — a drivable cell whose two
        neighbours along one axis are both off-limits to ground robots, so a
        robot stopped there blocks the lane. A NARROW place that isn't an
        aisle (a station, a dock) is not one: the humanoid lives in
        returns_qc. Worked out once, on first use."""
```

**Replace in** `backend/warehouse.py`:

```python
                             if key in zone.attributes)
            walkable = self.is_walkable
```

with:

```python
                             if key in zone.attributes)
                if zone.cell_type is CellType.EMPTY and zone.attributes.get("clearance") == NARROW:
                    cells.update(zone.cells)
            cells.update(self.crossings)
            walkable = self.is_walkable
```


- [ ] **Step 4: A set-down never lands on a crossing, a job's own box counts, and a unit goes back into its tote**

**Replace in** `backend/goods.py`:

```python

from .models import CONFIG, BoxKind, Cell, manhattan
```

with:

```python

from .box import Box
from .models import CONFIG, BoxKind, Cell, manhattan
```

**Replace in** `backend/goods.py`:

```python
        location.counted_qty, location.counted_tick = None, None
        return location

    def adjust_true(self, slot_id: str, delta: int) -> StockLocation:
```

with:

```python
        location.counted_qty, location.counted_tick = None, None
        return location

    def return_units(self, tote: Box, units: int) -> None:
        """Units put back into the tote they were just taken from (a pick that
        ended before its unit reached the line): the tote's recorded and true
        quantities both go back up by `units`, and the tote's own quantity
        with them. The caller gives the tote back the units' weight."""
        if tote.kind is not BoxKind.TOTE:
            raise ValueError(f"{tote.name} is a {tote.kind.value}, not a TOTE")
        if isinstance(units, bool) or not isinstance(units, int) or units < 1:
            raise ValueError("units must be a whole number of at least 1")
        slot_id = self.slot_of(tote.id)
        if slot_id is None:
            raise ValueError(f"{tote.name} is not in the stock ledger")
        tote.quantity = self.restock(slot_id, units).recorded_qty
        tote.touch()

    def adjust_true(self, slot_id: str, delta: int) -> StockLocation:
```

**Replace in** `backend/task_manager.py`:

```python
    cell_tuple,
    now_hms,
```

with:

```python
    cell_tuple,
    manhattan,
    now_hms,
```

**Replace in** `backend/task_manager.py`:

```python
        box's assigned task). A mobile robot sets it down on its own cell, as
        a reset does: it is STORED there, still recorded in its home slot if it
        is a tote. An arm puts an order item back on its working conveyor
```

with:

```python
        box's assigned task), or the job's own box when the robot already held
        it (a DELIVER_BOX after a PICK_BOX). A mobile robot sets it down STORED
        on the nearest cell its body may stop on (Warehouse.may_stop) that no
        other robot holds: its own cell, unless that is a walkway crossing — no
        robot may stop there, so no job could fetch the load from it. A tote
        stays recorded in its home slot. A picker puts a unit back into
        the tote it took it from; with that tote gone or out of reach, the
        unit fails. An arm puts an order item back on its working conveyor
```

**Replace in** `backend/task_manager.py`:

```python
        if box is None or box.assigned_task != task.id:
```

with:

```python
        if box is None or not (box.assigned_task == task.id or box.id == task.box_id or box.id in task.box_ids):
```

**Replace in** `backend/task_manager.py`:

```python
        box.position = robot.position
        box.set_status(twin.BoxStatus.STORED)
        twin.logger.info(LogCategory.BOX, f"{robot.name} set {box.name} down at ({robot.position[0]},"
                         f"{robot.position[1]}): {task.id} ended", task_id=task.id, robot_id=robot.id, box_id=box.id)
```

with:

```python
        source = next((action.params["unit_from"] for action in task.actions if "unit_from" in action.params), None)
        if box.kind.value == "ITEM" and source is not None:
            self._put_unit_back(task, robot, box, twin.find_box(source))
            return
        cell = twin.warehouse.nearest_walkable(robot.position, twin.other_robot_cells(robot.id),
                                               profile=robot.mobility, layer=robot.layer) or robot.position
        box.position = cell
        box.set_status(twin.BoxStatus.STORED)
        twin.logger.info(LogCategory.BOX, f"{robot.name} set {box.name} down at ({cell[0]},{cell[1]}): "
                         f"{task.id} ended", task_id=task.id, robot_id=robot.id, box_id=box.id)

    def _put_unit_back(self, task: Task, robot: Any, item: Any, tote: Optional[Any]) -> None:
        """A picker's unit goes back into the tote it was taken from: the
        tote's recorded and true quantities and its weights go back up, and
        the unit is gone. With the tote gone, out of the ledger or out of the
        picker's reach, the unit fails where the picker stands."""
        twin = self.twin
        try:
            if tote is None:
                raise ValueError("the tote it came from is gone")
            if manhattan(tote.position, robot.position) > 1:
                raise ValueError(f"{tote.name} is out of reach")
            twin.stock.return_units(tote, item.quantity)
        except ValueError as exc:
            item.position = robot.position
            item.set_status(twin.BoxStatus.FAILED)
            twin.logger.warning(LogCategory.BOX, f"{item.name} failed: {robot.name} could not put it back when "
                                f"{task.id} ended ({exc})", task_id=task.id, robot_id=robot.id, box_id=item.id)
            return
        tote.declared_weight_kg = round(tote.declared_weight_kg + item.declared_weight_kg, 3)
        tote.true_weight_kg = round(tote.true_weight_kg + item.true_weight_kg, 3)
        del twin.boxes[item.id]
        twin.logger.info(LogCategory.BOX, f"{robot.name} put {item.name} back into {tote.name}: {task.id} ended",
                         task_id=task.id, robot_id=robot.id, box_id=tote.id)
```


- [ ] **Step 5: The older job types plan through other robots' cells**

**Replace in** `backend/task_planner.py`:

```python
    ) -> Tuple[Cell, str]:
        """Turn a location specification into a concrete grid cell that a robot
        with `profile` can use on `layer` (no profile: any drivable cell)."""
```

with:

```python
        avoid: Optional[Set[Cell]] = None,
    ) -> Tuple[Cell, str]:
        """Turn a location specification into a concrete grid cell that a robot
        with `profile` can use on `layer` (no profile: any drivable cell).

        `blocked` cells are walls: never the cell chosen and never a way
        through. `avoid` cells (other robots' cells, on the new floor) are
        never the cell chosen either, but a route may pass them: they are
        traffic, which execution waits out or replans around."""
```

**Replace in** `backend/task_planner.py`:

```python
        blocked = set(blocked or ())
```

with:

```python
        blocked = set(blocked or ())
        avoid = set(avoid or ())
```

**Replace in** `backend/task_planner.py`:

```python
                else warehouse.nearest_walkable(cell, blocked, profile=profile, layer=layer)
```

with:

```python
                else warehouse.nearest_walkable(cell, blocked | avoid, profile=profile, layer=layer)
```

**Replace in** `backend/task_planner.py`:

```python
            chosen = nav.best_cell_in_zone(zone.cells, origin, blocked=blocked, prefer=prefer,
```

with:

```python
            cells = [c for c in zone.cells if c not in avoid]
            chosen = nav.best_cell_in_zone(cells, origin, blocked=blocked, prefer=prefer,
```

**Replace in** `backend/task_planner.py`:

```python
        blocked = self.twin.other_robot_cells(robot.id)
        # Every target is resolved for this robot's own body and layer.
        route = {"profile": robot.mobility, "layer": robot.layer}
```

with:

```python
        # Every target is resolved for this robot's own body and layer. On the
        # new floor the cells other robots stand on are traffic, not walls:
        # execution waits them out, replans around them or sidesteps, so a
        # robot in a one-lane aisle doesn't fail a job at plan time. They are
        # still never chosen as a zone cell to go to (`avoid`). A robot with no
        # floor profile keeps them as `blocked`, as classic planning always has.
        others = self.twin.other_robot_cells(robot.id)
        route: Dict[str, Any] = {"profile": robot.mobility, "layer": robot.layer}
        if robot.mobility is None:
            blocked = others
        else:
            blocked = set()
            route["avoid"] = others
```

**Replace in** `backend/task_planner.py`:

```python
            # On the new floor a job's targets (slot faces, station drops,
            # staging and dock cells) are chosen from the layout alone: a robot
            # standing in the way right now is traffic, which execution waits
            # out, replans around or sidesteps. A robot with no floor profile
            # keeps `blocked`, as classic planning always has.
            spec_blocked = set() if robot.mobility is not None else blocked
            actions, waypoints, handling_ops = spec.plan(self, task, robot, spec_blocked)
```

with:

```python
            # A job's targets (slot faces, station drops, staging and dock
            # cells) get the same `blocked`: empty on the new floor (above).
            actions, waypoints, handling_ops = spec.plan(self, task, robot, blocked)
```


- [ ] **Step 6: Run the new tests**

Run: `.venv/bin/python -m pytest -o addopts="" -q backend/test_traffic_residuals.py`
Expected: `16 passed`.

- [ ] **Step 7: Run the full suite**

Run: `.venv/bin/python -m pytest -o addopts="" -q`
Expected: `2 failed, 784 passed` (the two allowed failures).

- [ ] **Step 8: Commit**

```bash
git add backend/warehouse.py backend/task_manager.py backend/goods.py backend/task_planner.py backend/test_traffic_residuals.py
git commit -m "fix: plan 1b residuals — x=18 parking, set-downs off crossings, a picker's unit, old jobs in traffic

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: The new-floor seed, reset and app boot

Since commit `96a015d` the app has booted the new floor on a stopgap: twelve hand-placed robots, six totes, three pallets, and a shift the app started itself. This task replaces it with spec §4.2's seed and makes the new floor the app's default (§2 "Construction", Plan ruling 1). `backend/seeds/` holds both seeds. The classic one is `DigitalTwin.load_demo`'s tables and body moved verbatim; `load_demo` keeps its signature and delegates. The distribution-centre seed puts on the floor:
- every asset of the inventory's `distribution_center` profile that isn't decommissioned (15 robots, named after their model family and asset number);
- the 10 workers, on a 06:00–14:00 shift window, because duty follows the shift clock and the clock starts at 06:00 (plan 1b's carry);
- 60 pallets and 80 totes over 40 SKUs. Every SKU has a tote an AMR can reach holding enough for any customer-order line (plan 1b's other carry);
- the agent Ada.

`DigitalTwin(layout="distribution_center")` still boots empty (Plan ruling 4). `seed_floor()` puts the seed on, and `reset()` puts it back on a seeded twin (§4.3).

A robot starts on the first free cell of its home zone that its body may stop on and that no job needs; if every cell of the zone is needed, it takes the nearest such cell. The tote and pallet aisles are all slot faces, so their robots start on the main aisle beside them. This matters because AST-000202 runs the recalled release 2.2.1, and the gate refuses every job it is given, even the move to parking. Seeded on the first stoppable cell of tote aisle 2, the slot face (8,15), it stopped customer orders from completing after about ten sim-minutes.

Plan 1a's two spawn items land in `_spawn_cell`:
- an arm takes the station of its own pack cell;
- an explicit position must pass the stop rule (`may_stop`), so no robot spawns on a walkway crossing.

`FleetBridge.heartbeat` stops the remote check-ins on the distribution-centre inventory, which has no other site (§4.4). The app builds the seeded floor with its shift paused (§11.5, Plan ruling 6), and both reset routes rely on `twin.reset()` rerunning the seed. Plan ruling 3: this task rewrites `backend/test_app_layout.py`, which pinned the stopgap.

**Files:**
- Create: `backend/seeds/classic.py`
- Rewrite: `backend/seeds/distribution_center.py`, `backend/seeds/__init__.py`
- Modify: `backend/digital_twin.py` (imports; the demo tables leave; `__init__` (`floor_seeded`); `load_demo`; new `seed_floor`; `add_robot`'s spawn call; `_spawn_cell`; the tail of `reset`)
- Modify: `backend/fleet_bridge.py` (`__init__`, `heartbeat`)
- Modify: `backend/app.py` (imports, `DEFAULT_LAYOUT`, `_seed_floor` removed, `build_twin`, `create_app`'s `layout` default, the two reset routes, `main`)
- Modify: `run.sh`
- Rewrite: `backend/test_app_layout.py` (Plan ruling 3)
- Test: `backend/test_floor_seed.py` (create)

**Interfaces:**
- Consumes:
  - The inventory: `list_robots(site)` (`asset_id`, `asset_tag`, `model_code`, `embodiment_class`, `home_zone`, `lifecycle_status`), `list_workers(site)` (`worker_id`, `display_name`), `get_robot(asset_id)["home_zone"]`.
  - `FleetBridge.model_profile`, `fleet_bridge.FLOOR_SITE`.
  - The warehouse: `may_stop`, `cells_jobs_need`, `nearest_walkable`, `fixed_stations`, `slots`.
  - `operations.activities.ROLES`, `HOME_ZONES`, `sync_duty`; `operations.shift.DEFAULT_SKUS`, `shift_hour`; `people.place`; `CONFIG["SHIFT_START_HOUR"]`.
- Produces:
  - `backend.seeds.SEEDS: Dict[str, Callable[[Any], None]] = {"classic": classic.seed, "distribution_center": distribution_center.seed}`.
  - `backend.seeds.classic.seed(twin, create_tasks: bool = True) -> None`, and the `DEMO_AGENTS`, `DEMO_OPERATORS`, `DEMO_ROBOTS`, `DEMO_ASSET_IDS`, `DEMO_WORKER_IDS`, `DEMO_BOXES`, `DEMO_TASKS` tables (moved out of `digital_twin.py`).
  - `backend.seeds.distribution_center.seed(twin) -> None`, plus:
    - `robot_name(asset: Dict[str, Any]) -> str` (`TR50-101`);
    - `home_cell(twin, asset: Dict[str, Any]) -> Optional[Cell]`;
    - `GOODS_SEED = 42`, `SKUS` (SKU-001…SKU-040), `PALLETS = 60`, `TOTES = 80`, `HIGH_TOTES = 8`, `SHIFT_HOURS = 8`.
  - `DigitalTwin.seed_floor() -> None` (`ValueError` if the twin has robots) and `DigitalTwin.floor_seeded: bool` (False at construction; True after `load_demo` or `seed_floor`). `reset()` reruns the seed: on classic always, as before; on another floor only when `floor_seeded`.
  - `DigitalTwin._spawn_cell(position, body, mobility, asset_id: Optional[str] = None) -> Cell`.
  - `FleetBridge.profile: str`: the inventory's seed profile.
  - `backend.app.DEFAULT_LAYOUT = "distribution_center"`; `build_twin(layout=DEFAULT_LAYOUT, base_dir=BASE_DIR)` seeds an unseeded twin; `create_app(..., layout=DEFAULT_LAYOUT)`. `_seed_floor` no longer exists.
  - The seeded floor, which later tasks and the soak run on:
    - robots `TR50-101` (4,12), `TR50-102` (5,12), `TR50-103` (1,16, held STOPPED by its work order), `TR50-201` (7,13), `TR50-202` (7,15), `PK30-203` (20,14), `SC1-204` (7,1), `PF1200-205` (7,3), `PF1200-206` (7,7), `HH300-207` (1,7), `IX2-208` (19,1), `IX2-209` (20,1), `CX10-210` (23,14), `CX10-211` (22,16), `H1-212` (20,6);
    - operators named by their workforce `display_name` ("Sam", "Noor Haddad", …);
    - boxes `PAL-001`…`PAL-060` and `TOTE-001`…`TOTE-080`;
    - the shift PAUSED.

- [ ] **Step 1: Write the failing tests**

**Create** `backend/test_floor_seed.py`:

```python
"""The distribution-centre floor's seed (multi-embodiment spec §4.2), reset
(§4.3), remote check-ins (§4.4), and where the twin spawns robots on that
floor (plan 1a's arm-station and stop-rule items).

DigitalTwin(layout="distribution_center") still boots empty; seed_floor()
puts the seed on it, and reset() puts it back only on a seeded twin.
"""
import pytest

from backend import seeds
from backend.digital_twin import DigitalTwin
from backend.faults import FAULT_RISKS
from backend.models import CONFIG, BoxKind, CellType, OperatorStatus, RobotStatus, SimulationStatus
from backend.operations.activities import HOME_ZONES, ROLES, sync_duty
from backend.operations.shift import ShiftEngine, shift_hour
from backend.seeds import classic, distribution_center
from backend.simulator import Simulator

#: Where the seed puts every robot: the first free cell of its home zone its
#: body may stop on that no job needs, else the nearest such cell (the tote
#: and pallet aisles are all slot faces, so their robots start on the main
#: aisle beside them); the picker on Pick 1's work cell; arms on the station
#: of their own pack cell; drones on their pad.
PLACES = {
    "TR50-101": (4, 12), "TR50-102": (5, 12), "TR50-103": (1, 16), "TR50-201": (7, 13),
    "TR50-202": (7, 15), "PK30-203": (20, 14), "SC1-204": (7, 1), "PF1200-205": (7, 3),
    "PF1200-206": (7, 7), "HH300-207": (1, 7), "IX2-208": (19, 1), "IX2-209": (20, 1),
    "CX10-210": (23, 14), "CX10-211": (22, 16), "H1-212": (20, 6),
}
OFF_DUTY = {"E-10009", "E-10010"}   # Sasha is ON_LEAVE, Morgan TERMINATED
AMR_TOP_LEVEL = 1                   # AC-TR50's max_shelf_level
MOST_UNITS_A_LINE_ASKS = 3          # ShiftEngine._generate_customer_orders: 1–3 units a line


def make_twin(tmp_path, layout="distribution_center", demo=True):
    return DigitalTwin(log_dir=str(tmp_path / "logs"), data_dir=str(tmp_path / "data"),
                       persist_logs=False, demo=demo, demo_tasks=False, layout=layout)


@pytest.fixture
def floor(tmp_path):
    twin = make_twin(tmp_path)
    twin.seed_floor()
    return twin


@pytest.fixture
def fault_free(monkeypatch):
    for risk in ("COLLISION_RISK", "FALSE_SUCCESS_RISK", *FAULT_RISKS.values()):
        monkeypatch.setitem(CONFIG, risk, 0.0)


def boxes_of(twin, kind):
    return [box for box in twin.boxes.values() if box.kind is kind]


def test_every_active_asset_is_on_the_floor_bound_and_named_by_its_model(floor):
    robots = {robot.name: robot for robot in floor.robots.values()}
    assert {name: robot.position for name, robot in robots.items()} == PLACES
    assert {name: robot.asset_id for name, robot in robots.items()} == {
        name: f"AST-000{name.split('-')[1]}" for name in PLACES}
    assert floor.fleet.robot_for_asset("AST-000213") is None          # decommissioned: never placed
    for robot in robots.values():
        assert robot.robot_class == floor.inventory.get_robot(robot.asset_id)["model"]["embodiment_class"]
    held = {robot.name for robot in robots.values() if robot.status is RobotStatus.STOPPED}
    assert held == {"TR50-103"} and robots["TR50-103"].fleet_hold == "MAINTENANCE"   # its open work order


def test_no_robot_starts_where_a_job_will_need_it(floor):
    # TR50-202 runs the recalled release 2.2.1, so the gate refuses even its
    # move to parking: the first cell of its home zone (8,15) is a slot face, and
    # a tote job sent to that face would wait for good. Fails if the seed took
    # the first stoppable cell of the home zone instead.
    needed = floor.warehouse.cells_jobs_need()
    for robot in floor.robots.values():
        if robot.mobility.is_fixed:
            assert floor.warehouse.fixed_stations[robot.position] == \
                floor.inventory.get_robot(robot.asset_id)["home_zone"]
            continue
        assert floor.warehouse.may_stop(robot.position, robot.mobility), robot.name
        if robot.robot_class != "PICKER":                               # the picker works on its work cell
            assert robot.position not in needed, robot.name


def test_the_ten_workers_are_bound_on_a_shift_that_starts_with_the_clock(floor):
    operators = {operator.worker_id: operator for operator in floor.operators.values()}
    assert sorted(operators) == [f"E-{10000 + number}" for number in range(1, 11)]
    for worker_id, operator in operators.items():
        assert operator.name == floor.inventory.get_worker(worker_id)["display_name"]
        assert operator.shift_start_hour == CONFIG["SHIFT_START_HOUR"] == shift_hour(0.0)
        if worker_id in OFF_DUTY:
            assert (operator.status, operator.zone) == (OperatorStatus.OFF_DUTY, None)
        else:
            assert (operator.status, operator.zone) == (OperatorStatus.AVAILABLE, HOME_ZONES[ROLES[worker_id]])
    # Duty follows the shift clock (06:00 at the start): a window that left
    # 06:00 out would send these eight off duty here.
    sync_duty(floor)
    assert sum(operator.status is OperatorStatus.AVAILABLE for operator in floor.operators.values()) == 8


def test_the_racks_hold_60_pallets_and_the_shelves_80_totes_over_40_skus(floor):
    pallets, totes = boxes_of(floor, BoxKind.PALLET), boxes_of(floor, BoxKind.TOTE)
    assert (len(pallets), len(totes), len(floor.boxes)) == (60, 80, 140)
    for box in pallets + totes:
        slot = floor.warehouse.slot(box.slot)
        assert slot.kind == box.kind.value and box.position == slot.cell
        location = floor.stock.location(box.slot)
        assert (location.box_id, location.sku, location.recorded_qty) == (box.id, box.sku, box.quantity)
        assert box.sku and box.quantity > 0                              # one SKU each
    assert {floor.warehouse.slot(box.slot).level for box in pallets} == {0, 1, 2, 3, 4}
    assert all(150.0 <= box.weight <= 900.0 for box in pallets)
    assert {floor.warehouse.slot(box.slot).level for box in totes} == {0, 1, 2}
    assert all(5.0 <= box.weight <= 25.0 for box in totes)
    assert len(floor.stock.skus()) == 40 == len({box.sku for box in totes})


def test_every_sku_has_a_tote_an_amr_reaches_holding_enough_for_any_order_line(floor):
    # The customer-order generator only orders a SKU some tote holds enough
    # of; a seed whose totes held too few units, or kept a SKU only on the
    # top level, would starve it.
    for sku in floor.stock.skus():
        reachable = [location for location in floor.stock.locations(sku)
                     if floor.find_box(location.box_id).kind is BoxKind.TOTE
                     and floor.warehouse.slot(location.slot_id).level <= AMR_TOP_LEVEL]
        assert any(location.recorded_qty >= MOST_UNITS_A_LINE_ASKS for location in reachable), sku
    for _ in range(30):
        floor.shift._generate_customer_orders()
    assert floor.shift.counters["skipped_orders"] == 0
    assert floor.shift.orders.counts()["CUSTOMER"]["OPEN"] == 30


def test_the_seed_is_deterministic_with_ada_and_no_demo_tasks(tmp_path, floor):
    again = make_twin(tmp_path / "again")
    again.seed_floor()

    def picture(twin):
        return ([(robot.name, robot.position) for robot in twin.robots.values()],
                [(box.name, box.slot, box.sku, box.quantity, box.weight) for box in twin.boxes.values()],
                [(operator.name, operator.zone, operator.status) for operator in twin.operators.values()])

    assert picture(again) == picture(floor)
    assert [agent.name for agent in floor.agents.values()] == ["Ada"]
    assert not floor.tasks.tasks and floor.shift.status == ShiftEngine.PAUSED


def test_seed_floor_seeds_an_empty_twin_once(tmp_path):
    twin = make_twin(tmp_path)
    assert twin.floor_seeded is False and not twin.robots                # the new floor still boots empty
    twin.seed_floor()
    assert twin.floor_seeded is True and len(twin.robots) == 15
    with pytest.raises(ValueError, match="already has robots"):
        twin.seed_floor()
    assert make_twin(tmp_path / "classic", layout="classic").floor_seeded is True   # its demo boot
    assert seeds.SEEDS == {"classic": classic.seed, "distribution_center": distribution_center.seed}


def test_reset_puts_the_seed_back_with_the_shift_paused(floor, fault_free):
    sim = Simulator(floor)
    floor.simulation_status = SimulationStatus.RUNNING
    floor.shift.start()
    for _ in range(300):
        sim.tick()
    floor.add_robot(name="Extra", robot_class="AMR")
    assert floor.shift.orders.orders
    floor.reset(demo_tasks=False)
    # Fails if reset() left the floor empty, as it did before seed_floor existed.
    assert {robot.name: robot.position for robot in floor.robots.values()} == PLACES
    assert len(floor.boxes) == 140 and len(floor.operators) == 10
    assert [agent.name for agent in floor.agents.values()] == ["Ada"]
    assert floor.shift.status == ShiftEngine.PAUSED and not floor.shift.orders.orders
    assert floor.floor_seeded is True and floor.tick_count == 0


def test_reset_keeps_an_unseeded_floor_empty_and_classic_as_its_demo(tmp_path):
    bare = make_twin(tmp_path / "bare")
    bare.reset(demo_tasks=False)
    assert not bare.robots and not bare.boxes and bare.floor_seeded is False
    classic_twin = make_twin(tmp_path / "classic", layout="classic", demo=False)
    assert not classic_twin.robots and classic_twin.floor_seeded is False
    classic_twin.reset(demo_tasks=False)                                  # classic resets to its demo, as always
    assert sorted(robot.name for robot in classic_twin.robots.values()) == ["Robo-01", "Robo-02"]


def test_load_demo_runs_the_classic_seed(tmp_path):
    twin = make_twin(tmp_path, layout="classic", demo=False)
    twin.load_demo(create_tasks=True)
    assert [(robot.name, robot.position, robot.asset_id) for robot in twin.robots.values()] == [
        ("Robo-01", (3, 8), "AST-000101"), ("Robo-02", (16, 8), "AST-000102")]
    assert sorted(box.name for box in twin.boxes.values()) == ["Box-A", "Box-B", "Box-C", "Box-D", "Box-E"]
    assert [operator.name for operator in twin.operators.values()] == ["Sam", "Lee"]
    assert [agent.name for agent in twin.agents.values()] == ["Ada"]
    assert len(twin.tasks.tasks) == 2 and twin.floor_seeded is True


def test_an_arm_takes_the_station_of_its_own_pack_cell(tmp_path):
    twin = make_twin(tmp_path)
    # Fails if spawning still took the first free station whatever the home zone:
    # CX10-211 alone would land on Pack 1's (23,14).
    assert twin.add_robot(name="CX10-211", asset_id="AST-000211").position == (22, 16)
    assert twin.add_robot(name="CX10-210", asset_id="AST-000210").position == (23, 14)


def test_a_robot_is_never_spawned_on_the_walkway(tmp_path):
    twin = make_twin(tmp_path)
    for name, asset, crossing in (("TR50-201", "AST-000201", (17, 10)), ("TR50-202", "AST-000202", (17, 13))):
        robot = twin.add_robot(name=name, asset_id=asset, position=crossing)
        # Fails if an explicit position were checked with passable(): a robot
        # may cross a walkway crossing but never stop on one.
        assert robot.position != crossing and twin.warehouse.cell_type(*robot.position) is not CellType.WALKWAY
        assert twin.warehouse.may_stop(robot.position, robot.mobility)
        assert abs(robot.position[0] - crossing[0]) + abs(robot.position[1] - crossing[1]) == 1


def test_only_the_classic_inventory_has_remote_sites_checking_in(tmp_path, monkeypatch):
    for layout, expected in (("distribution_center", 0), ("classic", 1)):
        twin = make_twin(tmp_path / layout, layout=layout)
        calls = []
        monkeypatch.setattr(twin.inventory, "touch_remote_reports", lambda **kwargs: calls.append(kwargs) or 0)
        twin.fleet.heartbeat()
        assert twin.fleet.profile == layout and len(calls) == expected, layout


def test_the_seeded_floor_keeps_completing_customer_orders(floor, fault_free):
    # Fails if the seed wedges the shift: with TR50-202 on the slot face (8,15)
    # customer orders stopped completing after about ten sim-minutes.
    sim = Simulator(floor)
    floor.simulation_status = SimulationStatus.RUNNING
    floor.shift.configure(seed=42, pace=2.0)
    floor.shift.start()
    done = []
    for tick in range(1, 8001):                                           # 8000 × 0.15 s = 20 sim-minutes
        sim.tick()
        if tick % 4000 == 0:
            done.append(floor.shift.orders.counts()["CUSTOMER"]["DONE"])
    assert done[0] >= 1 and done[1] > done[0], done
    assert not [robot.name for robot in floor.robots.values() if robot.status is RobotStatus.ERROR]
```

Plan ruling 3 rewrites the app-boot test. It pinned the stopgap: a 12-robot floor, a running shift and a classic default. Its still-true checks stay: separate folders, an unknown layout refused before anything is written, and a second boot reusing the inventory file.

**Rewrite** `backend/test_app_layout.py`:

```python
"""The app boots the floor WAREHOUSE_LAYOUT names — the distribution centre
unless it names another (spec §2 "Construction", plan 1c ruling 1) — with
that floor's seed (§4.2) and its shift paused (§11.5, ruling 6).

Classic keeps logs/ and data/. Any other floor gets its own logs/<layout>/
and data/<layout>/, so switching floors never reseeds the other floor's
inventory file (ruling 2). Both reset routes put the seed back through
twin.reset(), with the shift paused again.
"""
import pytest

from backend import app as app_module
from backend.app import build_twin, create_app
from backend.models import BoxKind
from backend.operations.shift import ShiftEngine

FLEET_SIZE = 15   # every WH-01 asset but the decommissioned AST-000213
CREW_SIZE = 10
GOODS = {BoxKind.PALLET: 60, BoxKind.TOTE: 80}


def goods(twin):
    counts = {}
    for box in twin.boxes.values():
        counts[box.kind] = counts.get(box.kind, 0) + 1
    return counts


def test_classic_keeps_its_folders_and_demo(tmp_path):
    twin = build_twin("classic", str(tmp_path))
    assert twin.layout_name == "classic"
    assert (tmp_path / "data" / "inventory.sqlite3").exists()
    assert twin.log_dir == str(tmp_path / "logs")
    assert len(twin.robots) == 2 and twin.floor_seeded
    assert twin.shift.status == ShiftEngine.PAUSED


def test_the_new_floor_boots_with_its_seed_and_its_shift_paused(tmp_path):
    twin = build_twin("distribution_center", str(tmp_path))
    assert twin.layout_name == "distribution_center" and twin.floor_seeded
    assert len(twin.robots) == FLEET_SIZE
    assert all(robot.asset_id for robot in twin.robots.values())
    assert len(twin.operators) == CREW_SIZE and all(operator.worker_id for operator in twin.operators.values())
    assert goods(twin) == GOODS
    assert [agent.name for agent in twin.agents.values()] == ["Ada"]
    # The stopgap boot started the shift; now it waits for POST /api/shift/start.
    assert twin.shift.status == ShiftEngine.PAUSED and not twin.tasks.tasks


def test_the_new_floor_never_touches_classic_files(tmp_path):
    twin = build_twin("distribution_center", str(tmp_path))
    assert (tmp_path / "data" / "distribution_center" / "inventory.sqlite3").exists()
    assert twin.log_dir == str(tmp_path / "logs" / "distribution_center")
    assert not (tmp_path / "data" / "inventory.sqlite3").exists()


def test_a_second_boot_reuses_the_saved_inventory(tmp_path):
    first = build_twin("distribution_center", str(tmp_path))
    again = build_twin("distribution_center", str(tmp_path))
    assert again.inventory.store.epoch() == first.inventory.store.epoch()   # the file was reused, not reseeded
    assert len(again.robots) == FLEET_SIZE
    assert {robot.asset_id for robot in again.robots.values()} == {robot.asset_id for robot in first.robots.values()}


def test_an_unknown_layout_is_refused_before_anything_is_written(tmp_path):
    with pytest.raises(ValueError, match="distribution_center"):
        build_twin("warehouse2", str(tmp_path))
    assert not (tmp_path / "data").exists()
    assert not (tmp_path / "logs").exists()


def test_the_app_boots_the_distribution_centre_by_default(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "BASE_DIR", str(tmp_path))
    _app, twin, _sim, _ci = create_app(autostart=False, run_thread=False)
    assert twin.layout_name == "distribution_center" and len(twin.robots) == FLEET_SIZE
    assert (tmp_path / "data" / "distribution_center" / "inventory.sqlite3").exists()


@pytest.mark.parametrize("route", ["/api/simulation/reset", "/api/state/reset"])
def test_a_reset_puts_the_seed_back_with_the_shift_paused(tmp_path, monkeypatch, route):
    monkeypatch.setattr(app_module, "BASE_DIR", str(tmp_path))
    app, twin, _sim, _ci = create_app(layout="distribution_center", autostart=False, run_thread=False)
    app.config["TESTING"] = True
    twin.shift.start()
    twin.add_robot(name="Extra", robot_class="AMR")
    response = app.test_client().post(route, json={})
    assert response.status_code == 200
    assert len(twin.robots) == FLEET_SIZE and twin.find_robot("Extra") is None
    assert len(twin.operators) == CREW_SIZE and goods(twin) == GOODS
    assert twin.shift.status == ShiftEngine.PAUSED
    assert len(response.get_json()["state"]["robots"]) == FLEET_SIZE


def boot_with(monkeypatch, layout):
    """The layout main() hands create_app with WAREHOUSE_LAYOUT set to `layout`
    (None: unset)."""
    booted = {}

    class Stop(Exception):
        pass

    def fake_create_app(**kwargs):
        booted.update(kwargs)
        raise Stop

    if layout is None:
        monkeypatch.delenv("WAREHOUSE_LAYOUT", raising=False)
    else:
        monkeypatch.setenv("WAREHOUSE_LAYOUT", layout)
    monkeypatch.setattr(app_module, "create_app", fake_create_app)
    with pytest.raises(Stop):
        app_module.main()
    return booted["layout"]


def test_main_boots_the_floor_named_by_WAREHOUSE_LAYOUT(monkeypatch):
    assert boot_with(monkeypatch, "classic") == "classic"


@pytest.mark.parametrize("unset", [None, ""])
def test_main_defaults_to_the_distribution_centre(monkeypatch, unset):
    assert boot_with(monkeypatch, unset) == "distribution_center"
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/bin/python -m pytest -o addopts="" -q backend/test_floor_seed.py`
Expected: `1 error` during collection — `ImportError: cannot import name 'classic' from 'backend.seeds'`.

Run: `.venv/bin/python -m pytest -o addopts="" -q backend/test_app_layout.py`
Expected: `8 failed, 3 passed`:
- `assert 12 == 15` (the stopgap's twelve robots);
- `assert 'classic' == 'distribution_center'` (the old default);
- `AttributeError: 'DigitalTwin' object has no attribute 'floor_seeded'`.

- [ ] **Step 3: The seeds package — the classic demo moved verbatim, and the distribution-centre seed**

The classic seed is `DigitalTwin.load_demo`'s tables and body, with `self` read as `twin` (Step 4 takes them out of the twin):

**Create** `backend/seeds/classic.py`:

```python
"""The classic floor's demo (multi-embodiment spec §4.2): two robots, five
boxes, the agent Ada, two operators and, if asked, two demo tasks.

DigitalTwin.load_demo's tables and body, moved here verbatim (the twin's
load_demo now calls seed), so every caller gets exactly the floor it always
got.
"""
from __future__ import annotations

from typing import Any

from ..inventory.catalog_data import CLASS_DEFAULT_MODELS
from ..models import LogCategory

#: One demo agent and two demo operators — one fully certified, one only
#: partially, so HUMAN_INSPECTION / MIXED_MAINTENANCE_MISSION have a
#: realistic mix of eligible and ineligible operators to pick from when
#: assigned "AUTO", the same way DEMO_ROBOTS/DEMO_BOXES seed a realistic
#: starting warehouse.
DEMO_AGENTS = [
    ("Ada", "groq/gpt-oss-20b"),
]

DEMO_OPERATORS = [
    ("Sam", ["safety_inspection", "electrical_safety"]),
    ("Lee", ["safety_inspection"]),
]

DEMO_ROBOTS = [
    ("Robo-01", (3, 8)),
    ("Robo-02", (16, 8)),
]

#: The inventory records (backend/inventory/demo_seed.py) the demo robots
#: and operators are bound to — separate maps so the tuples above stay
#: exactly as they were.
DEMO_ASSET_IDS = {"Robo-01": "AST-000101", "Robo-02": "AST-000102"}
DEMO_WORKER_IDS = {"Sam": "E-10001", "Lee": "E-10002"}

DEMO_BOXES = [
    ("Box-A", (3, 5), 12.5, "shelf_a", "loading_zone"),
    ("Box-B", (9, 5), 8.0, "shelf_b", "packing_area"),
    ("Box-C", (15, 5), 20.0, "shelf_c", "unloading_zone"),
    ("Box-D", (5, 5), 4.5, "shelf_a", "packing_area"),
    ("Box-E", (11, 5), 15.0, "shelf_b", "loading_zone"),
]

DEMO_TASKS = [
    {"type": "PICK_AND_DELIVER", "robot": "Robo-01", "box": "Box-A",
     "source": "shelf_a", "destination": "loading_zone", "priority": "HIGH"},
    {"type": "PICK_AND_DELIVER", "robot": "Robo-02", "box": "Box-B",
     "source": "shelf_b", "destination": "packing_area", "priority": "NORMAL"},
]


def seed(twin: Any, create_tasks: bool = True) -> None:
    """Populate an empty classic twin with the demo (and its tasks)."""
    for name, position in DEMO_ROBOTS:
        try:
            twin.add_robot(name=name, position=position, asset_id=DEMO_ASSET_IDS.get(name))
        except ValueError as exc:
            # A persisted inventory may have retired the demo's asset; the
            # twin must still boot, so take over an orphaned floor asset of
            # the same model (a previous boot's replacement) or commission
            # a fresh one — never grow the inventory on every boot.
            demo_asset = DEMO_ASSET_IDS.get(name)
            model = (twin.inventory.get_robot(demo_asset)["model_code"]
                     if twin.inventory.has_asset(demo_asset) else CLASS_DEFAULT_MODELS["AMR"])
            spare = twin.fleet.spare_floor_asset(model, exclude=DEMO_ASSET_IDS.values())
            twin.logger.warning(
                LogCategory.FLEET,
                f"{name}: demo asset {demo_asset} is unusable ({exc}) — "
                + (f"taking over spare asset {spare}" if spare else "commissioning a new one"))
            twin.add_robot(name=name, position=position, asset_id=spare)
    for name, position, weight, source, destination in DEMO_BOXES:
        twin.add_box(name=name, position=position, weight=weight,
                     source=source, destination=destination)
    for name, model_version in DEMO_AGENTS:
        twin.add_agent(name=name, model_version=model_version)
    for name, certifications in DEMO_OPERATORS:
        twin.add_operator(name=name, certifications=certifications, worker_id=DEMO_WORKER_IDS.get(name))
    if create_tasks:
        for payload in DEMO_TASKS:
            try:
                twin.tasks.create_task(payload)
            except ValueError as exc:
                twin.logger.error(LogCategory.TASK, f"Demo task rejected: {exc}")
```

The distribution-centre seed replaces the stopgap:

**Rewrite** `backend/seeds/distribution_center.py`:

```python
"""The distribution-centre floor's seed (multi-embodiment spec §4.2).

Everything comes from the inventory's distribution_center profile and the
floor's own layout, through the twin's public API:

- one robot per asset at WH-01 that isn't decommissioned (15), named after
  its model family and asset number (TR50-101, PF1200-205, …), bound to the
  asset and placed on the first free cell of its home zone that its body may
  stop on and no job needs (a route zone's cells in route order: the scout's
  patrol_loop) — or, for a working aisle every cell of which a job needs, the
  nearest such cell to it. The picker starts on its station's work cell,
  where its own job wants it, and an arm on its home zone's fixed station
  (DigitalTwin._spawn_cell does that);
- one operator per worker (10), bound by worker id, on a shift window that
  starts with the shift clock (06:00), and standing in their role's home
  zone (operations/activities.HOME_ZONES) — unless their HR state keeps them
  off duty (Sasha is ON_LEAVE, Morgan TERMINATED);
- 60 pallets on the racks at levels 0–4 (150–900 kg) and 80 totes on the
  shelves at levels 0–2 (5–25 kg, one SKU each) over 40 SKUs. Every SKU has
  a tote on level 0 or 1, which the AMRs reach, holding more units than a
  customer-order line asks for (1–3), so the order generator can use every
  SKU;
- the agent Ada.

There are no demo tasks: the shift engine makes the work. The seed is
deterministic: the goods' weights and unit counts come from its own
random.Random(GOODS_SEED).
"""
from __future__ import annotations

import random
from typing import Any, Dict, Optional

from .. import people
from ..fleet_bridge import FLOOR_SITE
from ..models import CONFIG, Cell
from ..operations.activities import HOME_ZONES, ROLES, sync_duty
from ..operations.shift import DEFAULT_SKUS
from .classic import DEMO_AGENTS

GOODS_SEED = 42
SKUS = DEFAULT_SKUS                 # SKU-001 … SKU-040, as the shift engine's streams use
PALLETS, TOTES = 60, 80
#: Totes on the top shelf level (1.2 m), which only the humanoid reaches; the
#: rest sit on levels 0 and 1, which the AMRs reach too.
HIGH_TOTES = 8
PALLET_KG, PALLET_UNITS = (150.0, 900.0), (20, 60)
TOTE_KG, TOTE_UNITS = (5.0, 25.0), (12, 30)
#: Each operator's shift window: SHIFT_HOURS from the shift clock's start.
SHIFT_HOURS = 8


def seed(twin: Any) -> None:
    """Populate an empty distribution-centre twin."""
    _robots(twin)
    _operators(twin)
    _goods(twin)
    for name, model_version in DEMO_AGENTS:
        twin.add_agent(name=name, model_version=model_version)


def robot_name(asset: Dict[str, Any]) -> str:
    """TR50-101 for asset tag AT-000101 of model AC-TR50: the model's family
    (its code without the maker's prefix) and the tag's number."""
    family = asset["model_code"].split("-", 1)[-1]
    number = asset["asset_tag"].rsplit("-", 1)[-1].lstrip("0") or "0"
    return f"{family}-{number}"


def home_cell(twin: Any, asset: Dict[str, Any]) -> Optional[Cell]:
    """Where the asset's robot starts: the first free cell of its home zone
    that its body may stop on and no job needs (Warehouse.cells_jobs_need),
    else the nearest such cell to the zone. A robot the trust layer won't
    move — AST-000202 runs a recalled release, so even a move to parking is
    refused — would otherwise stand for good on a slot face a tote job is
    sent to. The picker starts on its station's work cell. None for an arm (it
    takes its home station) and for a home zone that isn't on this floor
    (add_robot then picks its default spot)."""
    warehouse = twin.warehouse
    zone = warehouse.zones.get(asset["home_zone"] or "")
    body = twin.fleet.model_profile(asset["model_code"])
    if zone is None or body.is_fixed:
        return None
    taken = set(twin.robot_cells())
    work = tuple(zone.attributes.get("work_cell") or ())
    if body.embodiment_class == "PICKER" and work and work not in taken:
        return work
    blocked = taken | warehouse.cells_jobs_need()
    free = next((cell for cell in zone.cells if cell not in blocked and warehouse.may_stop(cell, body)), None)
    return free or warehouse.nearest_walkable(zone.cells[0], blocked, profile=body)


def _robots(twin: Any) -> None:
    for asset in twin.inventory.list_robots(site=FLOOR_SITE):
        if asset["lifecycle_status"] == "DECOMMISSIONED":
            continue
        twin.add_robot(name=robot_name(asset), asset_id=asset["asset_id"],
                       robot_class=asset["embodiment_class"], position=home_cell(twin, asset))


def _operators(twin: Any) -> None:
    start = int(CONFIG["SHIFT_START_HOUR"])
    for worker in twin.inventory.list_workers(site=FLOOR_SITE):
        operator = twin.add_operator(name=worker["display_name"], worker_id=worker["worker_id"],
                                     shift_start_hour=start, shift_end_hour=(start + SHIFT_HOURS) % 24)
        role = ROLES.get(worker["worker_id"])
        if role is not None and operator.employment_status == "ACTIVE":
            people.place(twin, operator, HOME_ZONES[role])
    sync_duty(twin)  # ON_LEAVE and TERMINATED are off duty from the start


def _goods(twin: Any) -> None:
    rng = random.Random(GOODS_SEED)
    slots = list(twin.warehouse.slots.values())
    # Every third pallet slot by id: ids run level by level within a rack
    # cell, and 3 and 5 share no factor, so the 60 spread over all 36 rack
    # cells and all five levels.
    pallet_slots = sorted(slot.slot_id for slot in slots if slot.kind == "PALLET")[::3][:PALLETS]
    for number, slot_id in enumerate(pallet_slots):
        twin.add_box(name=f"PAL-{number + 1:03d}", kind="PALLET", slot=slot_id, sku=SKUS[number % len(SKUS)],
                     quantity=rng.randint(*PALLET_UNITS), weight=round(rng.uniform(*PALLET_KG), 1))
    # Levels 0 and 1 hold two laps of the SKUs in slot-id order, so each SKU
    # has a tote an AMR reaches; the top level of the two east columns holds a
    # second tote of the SKUs the first lap ended on.
    low = sorted(slot.slot_id for slot in slots if slot.kind == "TOTE" and slot.level <= 1)[:TOTES - HIGH_TOTES]
    top = sorted((slot for slot in slots if slot.kind == "TOTE" and slot.level == 2),
                 key=lambda slot: (-slot.cell[0], slot.cell[1]))[:HIGH_TOTES]
    skus = [SKUS[number % len(SKUS)] for number in range(len(low))] + list(SKUS[len(SKUS) - HIGH_TOTES:])
    for number, (slot_id, sku) in enumerate(zip(low + [slot.slot_id for slot in top], skus)):
        twin.add_box(name=f"TOTE-{number + 1:03d}", kind="TOTE", slot=slot_id, sku=sku,
                     quantity=rng.randint(*TOTE_UNITS), weight=round(rng.uniform(*TOTE_KG), 1))
```

The package now names each layout's seed:

**Rewrite** `backend/seeds/__init__.py`:

```python
"""Twin seeds: what a floor holds when it boots or resets (multi-embodiment
spec §4.2). Each seed populates an empty twin through its public API."""
from __future__ import annotations

from typing import Any, Callable, Dict

from . import classic, distribution_center

#: Layout name -> its seed. DigitalTwin.seed_floor runs the twin's own.
SEEDS: Dict[str, Callable[[Any], None]] = {
    "classic": classic.seed,
    "distribution_center": distribution_center.seed,
}

__all__ = ["SEEDS", "classic", "distribution_center"]
```

- [ ] **Step 4: The twin seeds its floor and puts the seed back on reset**

The demo tables leave `digital_twin.py` (they now live in `backend/seeds/classic.py`):

**Replace in** `backend/digital_twin.py`:

```python
from .warehouse import Warehouse

#: One demo agent and two demo operators — one fully certified, one only
#: partially, so HUMAN_INSPECTION / MIXED_MAINTENANCE_MISSION have a
#: realistic mix of eligible and ineligible operators to pick from when
#: assigned "AUTO", the same way DEMO_ROBOTS/DEMO_BOXES seed a realistic
#: starting warehouse.
DEMO_AGENTS = [
    ("Ada", "groq/gpt-oss-20b"),
]

DEMO_OPERATORS = [
    ("Sam", ["safety_inspection", "electrical_safety"]),
    ("Lee", ["safety_inspection"]),
]

DEMO_ROBOTS = [
    ("Robo-01", (3, 8)),
    ("Robo-02", (16, 8)),
]

#: The inventory records (backend/inventory/demo_seed.py) the demo robots
#: and operators are bound to — separate maps so the tuples above stay
#: exactly as they were.
DEMO_ASSET_IDS = {"Robo-01": "AST-000101", "Robo-02": "AST-000102"}
DEMO_WORKER_IDS = {"Sam": "E-10001", "Lee": "E-10002"}

DEMO_BOXES = [
    ("Box-A", (3, 5), 12.5, "shelf_a", "loading_zone"),
    ("Box-B", (9, 5), 8.0, "shelf_b", "packing_area"),
    ("Box-C", (15, 5), 20.0, "shelf_c", "unloading_zone"),
    ("Box-D", (5, 5), 4.5, "shelf_a", "packing_area"),
    ("Box-E", (11, 5), 15.0, "shelf_b", "loading_zone"),
]

DEMO_TASKS = [
    {"type": "PICK_AND_DELIVER", "robot": "Robo-01", "box": "Box-A",
     "source": "shelf_a", "destination": "loading_zone", "priority": "HIGH"},
    {"type": "PICK_AND_DELIVER", "robot": "Robo-02", "box": "Box-B",
     "source": "shelf_b", "destination": "packing_area", "priority": "NORMAL"},
]
```

with:

```python
from .warehouse import Warehouse
```

Import the seeds, and add the `floor_seeded` flag (classic's demo boot sets it):

**Replace in** `backend/digital_twin.py`:

```python
from .robot import Robot
from .task_manager import Task, TaskManager
```

with:

```python
from .robot import Robot
from .seeds import SEEDS
from .seeds import classic as classic_seed
from .task_manager import Task, TaskManager
```

**Replace in** `backend/digital_twin.py`:

```python
        # The classic demo seed only fits the classic floor; the
        # distribution-centre floor has no twin seed yet and boots empty.
```

with:

```python
        # Whether this floor's seed (backend/seeds) is on it, so a reset puts it
        # back. Classic boots with its demo, as it always has. Any other floor
        # boots empty — tests build their own floors on it — until
        # seed_floor() runs (the app's build_twin calls it).
        self.floor_seeded = False
```

`load_demo` delegates to the classic seed, and `seed_floor` runs the twin's own seed:

**Replace in** `backend/digital_twin.py`:

```python
        for name, position in DEMO_ROBOTS:
            try:
                self.add_robot(name=name, position=position, asset_id=DEMO_ASSET_IDS.get(name))
            except ValueError as exc:
                # A persisted inventory may have retired the demo's asset; the
                # twin must still boot, so take over an orphaned floor asset of
                # the same model (a previous boot's replacement) or commission
                # a fresh one — never grow the inventory on every boot.
                demo_asset = DEMO_ASSET_IDS.get(name)
                model = (self.inventory.get_robot(demo_asset)["model_code"]
                         if self.inventory.has_asset(demo_asset) else CLASS_DEFAULT_MODELS["AMR"])
                spare = self.fleet.spare_floor_asset(model, exclude=DEMO_ASSET_IDS.values())
                self.logger.warning(
                    LogCategory.FLEET,
                    f"{name}: demo asset {demo_asset} is unusable ({exc}) — "
                    + (f"taking over spare asset {spare}" if spare else "commissioning a new one"))
                self.add_robot(name=name, position=position, asset_id=spare)
        for name, position, weight, source, destination in DEMO_BOXES:
            self.add_box(name=name, position=position, weight=weight,
                         source=source, destination=destination)
        for name, model_version in DEMO_AGENTS:
            self.add_agent(name=name, model_version=model_version)
        for name, certifications in DEMO_OPERATORS:
            self.add_operator(name=name, certifications=certifications, worker_id=DEMO_WORKER_IDS.get(name))
        if create_tasks:
            for payload in DEMO_TASKS:
                try:
                    self.tasks.create_task(payload)
                except ValueError as exc:
                    self.logger.error(LogCategory.TASK, f"Demo task rejected: {exc}")
```

with:

```python
        """The classic demo (backend/seeds/classic.py), with its tasks if asked."""
        classic_seed.seed(self, create_tasks=create_tasks)
        self.floor_seeded = True

    def seed_floor(self) -> None:
        """Put this floor's seed (backend/seeds, spec §4.2) on the empty twin,
        and remember it, so reset() puts it back."""
        with self.lock:
            if self.robots:
                raise ValueError(f"The {self.layout_name} floor already has robots; seed an empty twin")
            SEEDS[self.layout_name](self)
            self.floor_seeded = True
```

`reset()` puts the seed back: classic's demo as always, and another floor's seed only if it was seeded:

**Replace in** `backend/digital_twin.py`:

```python
        if self.layout_name == "classic":
            self.load_demo(create_tasks=demo_tasks)
```

with:

```python
        # The floor comes back as its seed left it: classic as its demo, as it
        # always has; another floor only if it was seeded (an empty test floor
        # stays empty).
        if self.layout_name == "classic":
            self.load_demo(create_tasks=demo_tasks)
        elif self.floor_seeded:
            self.seed_floor()
```

- [ ] **Step 5: Arms take their own station, and an explicit position must pass the stop rule**

`add_robot` hands `_spawn_cell` the asset, so an arm can find its home zone:

**Replace in** `backend/digital_twin.py`:

```python
            spawn = self._spawn_cell(position, body, mobility)
```

with:

```python
            spawn = self._spawn_cell(position, body, mobility, asset_id)
```

**Replace in** `backend/digital_twin.py`:

```python
                    mobility: Optional[MobilityProfile]) -> Cell:
        """Where a new robot starts. Fixed equipment goes on a free station
        cell of its own; anything else on the requested (or default) cell, or
        the nearest free cell its floor profile may use."""
```

with:

```python
                    mobility: Optional[MobilityProfile], asset_id: Optional[str] = None) -> Cell:
        """Where a new robot starts. Fixed equipment goes on a free station
        cell of its own — its asset's home zone's station when that is free;
        anything else on the requested (or default) cell, or the nearest free
        cell its floor profile may stop on (never a walkway crossing)."""
```

**Replace in** `backend/digital_twin.py`:

```python
            cell = tuple(position) if position else next((c for c in stations if c not in occupied), None)
```

with:

```python
            home_zone = (self.inventory.get_robot(asset_id)["home_zone"]
                         if asset_id and self.inventory.has_asset(asset_id) else None)
            free = sorted((c for c in stations if c not in occupied), key=lambda c: stations[c] != home_zone)
            cell = tuple(position) if position else next(iter(free), None)
```

**Replace in** `backend/digital_twin.py`:

```python
        if self.warehouse.passable(candidate, mobility) and candidate not in occupied:
```

with:

```python
        if self.warehouse.may_stop(candidate, mobility) and candidate not in occupied:
```

- [ ] **Step 6: No remote check-ins on the distribution-centre inventory (§4.4)**

**Replace in** `backend/fleet_bridge.py`:

```python
        self._profiles: Dict[str, MobilityProfile] = {}
        service.subscribe(self._on_change)
```

with:

```python
        self._profiles: Dict[str, MobilityProfile] = {}
        # The seed profile the inventory was seeded under (files from before
        # profiles are classic); a reseed keeps it.
        self.profile: str = service.store.get_meta("seed_profile") or "classic"
        service.subscribe(self._on_change)
```

**Replace in** `backend/fleet_bridge.py`:

```python
        # reporting for it, so its report goes stale (and the fleet shows it).
        self.service.touch_remote_reports(exclude=bound, exclude_sites=(FLOOR_SITE,))
```

with:

```python
        # reporting for it, so its report goes stale (and the fleet shows it). The
        # distribution-centre inventory has no other site — every active asset is on
        # this floor and bound (spec §4.4) — so nothing else checks in there.
        if self.profile != "distribution_center":
            self.service.touch_remote_reports(exclude=bound, exclude_sites=(FLOOR_SITE,))
```

- [ ] **Step 7: The app boots the seeded new floor by default, its shift paused**

The stopgap's import goes:

**Replace in** `backend/app.py`:

```python
from .policy import DEFAULT_POLICY_PATH, effective_policy, load_policies
from .seeds import distribution_center
```

with:

```python
from .policy import DEFAULT_POLICY_PATH, effective_policy, load_policies
```

**Replace in** `backend/app.py`:

```python
FRONTEND_DIR = os.path.join(BASE_DIR, "frontend")
```

with:

```python
FRONTEND_DIR = os.path.join(BASE_DIR, "frontend")
#: The floor the app boots when WAREHOUSE_LAYOUT doesn't name one.
DEFAULT_LAYOUT = "distribution_center"
```

**Replace in** `backend/app.py`:

```python
def _seed_floor(twin: DigitalTwin) -> None:
    """Classic seeds itself (load_demo). The new floor gets its stopgap seed and
    a running shift, since the dashboard has no shift controls yet (plan 1c)."""
    if twin.layout_name == "distribution_center":
        distribution_center.seed(twin)
        twin.shift.start()


def build_twin(layout: str = "classic", base_dir: str = BASE_DIR) -> DigitalTwin:
    """The app's own twin on the floor called `layout`. Classic keeps logs/ and
    data/; any other floor gets logs/<layout>/ and data/<layout>/, so switching
    floors never reseeds classic's inventory file."""
```

with:

```python
def build_twin(layout: str = DEFAULT_LAYOUT, base_dir: str = BASE_DIR) -> DigitalTwin:
    """The app's own twin on the floor called `layout`, with that floor's seed
    (spec §4.2) and its shift paused until someone starts it (§11.5). Classic
    keeps logs/ and data/; any other floor gets logs/<layout>/ and
    data/<layout>/, so switching floors never reseeds the other's inventory file."""
```

**Replace in** `backend/app.py`:

```python
    )
    _seed_floor(twin)
    return twin
```

with:

```python
    )
    if not twin.floor_seeded:  # classic seeded itself with its demo
        twin.seed_floor()
    return twin
```

**Replace in** `backend/app.py`:

```python
    layout: str = "classic",
```

with:

```python
    layout: str = DEFAULT_LAYOUT,
```

**Replace in** `backend/app.py`:

```python
            twin.reset(demo_tasks=bool(data.get("demo_tasks", True)))
            _seed_floor(twin)
```

with:

```python
            twin.reset(demo_tasks=bool(data.get("demo_tasks", True)))  # reruns the floor's seed
```

**Replace in** `backend/app.py`:

```python
            twin.reset(demo_tasks=True)
            _seed_floor(twin)
```

with:

```python
            twin.reset(demo_tasks=True)  # reruns the floor's seed
```

**Replace in** `backend/app.py`:

```python
    app, twin, simulator, _ci = create_app(layout=os.environ.get("WAREHOUSE_LAYOUT", "classic"))
```

with:

```python
    app, twin, simulator, _ci = create_app(layout=os.environ.get("WAREHOUSE_LAYOUT") or DEFAULT_LAYOUT)
```

`run.sh` names the floor it boots:

**Replace in** `run.sh`:

```bash
PORT="${WAREHOUSE_PORT:-5000}"
```

with:

```bash
PORT="${WAREHOUSE_PORT:-5000}"
LAYOUT="${WAREHOUSE_LAYOUT:-distribution_center}"
```

**Replace in** `run.sh`:

```bash
echo "  Dashboard: http://$HOST:$PORT"
echo "  Stop with Ctrl+C"
```

with:

```bash
echo "  Dashboard: http://$HOST:$PORT"
echo "  Floor: $LAYOUT (set WAREHOUSE_LAYOUT=classic for the classic floor)"
echo "  Stop with Ctrl+C"
```

**Replace in** `run.sh`:

```bash
exec env WAREHOUSE_HOST="$HOST" WAREHOUSE_PORT="$PORT" python -m backend.app
```

with:

```bash
exec env WAREHOUSE_HOST="$HOST" WAREHOUSE_PORT="$PORT" WAREHOUSE_LAYOUT="$LAYOUT" python -m backend.app
```

- [ ] **Step 8: Run the new tests**

Run: `.venv/bin/python -m pytest -o addopts="" -q backend/test_floor_seed.py backend/test_app_layout.py`
Expected: `25 passed`.

- [ ] **Step 9: Run the full suite**

Run: `.venv/bin/python -m pytest -o addopts="" -q`
Expected: `2 failed, 800 passed` (Task 1's 784 plus 16: 14 tests in `test_floor_seed.py`, and `test_app_layout.py` goes from 9 tests to 11; the two failures are the allowed ones).

- [ ] **Step 10: Commit**

```bash
git add backend/seeds/__init__.py backend/seeds/classic.py backend/seeds/distribution_center.py \
    backend/digital_twin.py backend/fleet_bridge.py backend/app.py run.sh \
    backend/test_app_layout.py backend/test_floor_seed.py
git commit -m "feat: the distribution-centre seed, and the app boots it by default

The app booted the new floor on a twelve-robot stopgap and started its
shift itself. backend/seeds now holds both floors' seeds: classic's demo,
moved verbatim out of DigitalTwin.load_demo, and the distribution-centre
seed (spec §4.2). That seed brings every active asset, the ten workers on a
shift that starts with the clock, 60 pallets, 80 totes over 40 SKUs, and
Ada. seed_floor() puts it on an empty twin, and reset() puts it back. A
robot starts where no job needs its cell, so the recalled-firmware AMR
can't wedge a tote aisle. Arms take their own station, and no robot spawns
on the walkway. The new floor has no remote check-ins, and the app boots
it by default with the shift paused.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Save and load v2 — the floor

`save_state` writes version 2 (spec §4.3): version 1's sections plus the floor's name (`layout`), the stock ledger, the equipment and `floor_seeded`; and every robot as `Robot.to_state()` — `to_dict` with the battery, altitude and lift height unrounded and the bookkeeping a robot needs to carry on mid-route, mid-step or mid-wait (travel credit, step timer, traffic wait, the whole safety wait). `load_state` reads version 2 and version 1 (a classic save), refuses a save of another floor before anything changes (Plan ruling 5), rebuilds everything from the file before it touches the twin, and then replaces the robots, boxes, people, tasks, ledger and equipment together, so nothing from before the load survives — which closes plan 1b's ledger minors "load_state clears boxes but not twin.stock", "load_state leaves twin.equipment stale" and "hand-off ids use a private `_handoff_seq`". Each part checks what it is given (plan 1a's save/load items): `Robot.from_dict` refuses a layer or altitude no robot can have, `Operator.from_dict` deep-copies the credential scopes and refuses a person out of place, `StockLedger.from_dict` puts every location back through `put()`, and `Equipment.load_state` refuses an item or jam off the line. `Box.true_slot` and `Operator.employment_status` already round-trip; the tests pin them. The shift engine and its orders are Task 4's.

**Files:**
- Modify: `backend/robot.py` (`to_state`; `from_dict` restores and checks)
- Modify: `backend/operator.py` (`from_dict(data, warehouse=None)` checks where the person is)
- Modify: `backend/goods.py` (`StockLedger.from_dict` through the ledger's own checks)
- Modify: `backend/equipment.py` (`LineItem.from_dict`, `Handoff.from_dict`, `Equipment.to_state` / `load_state`)
- Modify: `backend/digital_twin.py` (`serialize`, `load_state`)
- Test: `backend/test_save_load_v2.py` (create)

**Interfaces:**
- Consumes (Task 2): `DigitalTwin.floor_seeded: bool`.
- Produces:
  - `DigitalTwin.STATE_VERSION = 2`. `serialize()` / `save_state()` write `{"version": 2, "layout": <layout name>, …every version 1 key…, "stock": StockLedger.to_dict(), "equipment": Equipment.to_state() or None, "floor_seeded": bool}`, with `"robots"` as `Robot.to_state()` dicts.
  - `DigitalTwin.load_state(path=None)`: accepts version 1 (classic) and 2; an unknown version, or a save whose layout isn't this twin's (`"This save is of the <saved> floor, and this twin runs the <this> floor: …"`), or any damaged part raises `ValueError` with the twin unchanged; `twin.stock` and `twin.equipment` are replaced (a new `Equipment.for_floor(self)` loaded from the save); `floor_seeded` is restored (a version 1 save leaves the twin's own value).
  - `Robot.to_state() -> Dict[str, Any]` — new: `to_dict()` plus the unrounded `battery`, `battery_consumed`, `altitude_m`, `lift_height_m`, and `status_before_stop`, `moves_since_drain`, `move_accumulator`, `wait_ticks`, `action_timer`, `low_battery_warned`, `wait_started_tick`, `wait_task_id`, `wait_cell`, `wait_escalated`. `Robot.from_dict(data)` restores all of them (and `wait_reason`, `blocked_by`, `last_error`, `ota_installing`; `activity`, `lift_height_m` and `model_code` already were) and raises `ValueError` for a layer outside `LAYERS`, a non-numeric altitude, a GROUND robot not at 0 m or an AIR robot not above 0 m.
  - `Operator.from_dict(data, warehouse=None)`: scopes copied all the way down; `ValueError` for a `transit_to` without `transit_until_tick` (or the reverse), a walk with no `zone`, an OFF_DUTY person with a zone, and — given a warehouse — a zone or `transit_to` that isn't one of its zones.
  - `StockLedger.from_dict(data, warehouse=None)` — the existing signature (an existing test calls it this way); `ValueError` for an unknown or missing field, a quantity or tick that isn't a whole number of 0 or more, and everything `put()` refuses (unknown slot, a slot twice, a box in two slots).
  - `Equipment.to_state() -> Dict[str, Any]` (`items` with their cells, `jams`, `sorter`, `handoffs`, `handoff_seq`, items and jams in their own order) and `Equipment.load_state(data) -> None` (replaces all of it; `ValueError` and no change for an item, stop or jam off the line, or two items on one cell). `LineItem.from_dict(data)`, `Handoff.from_dict(data)`.

- [ ] **Step 1: Write the failing tests**

Robots, people and boxes are added through the public API, with the fleet `backend/test_shift_soak.py` uses (no new-floor seed: Plan ruling 4). The carry-on test continues both twins with a new `Simulator`: its retry and idle timers belong to the loop, not to the twin, so a save doesn't carry them.

**Create** `backend/test_save_load_v2.py`:

```python
"""Save and load, version 2 (multi-embodiment spec §4.3; Plan ruling 5): a
save names its floor and carries the stock ledger and the equipment; robots
mid-step or mid-wait, people mid-walk, stock and the conveyor come back
exactly as they were; a load replaces everything it finds; and a save loads
only on the floor it was made on. A version 1 file is a classic save and
still loads on classic. The floors are built through the public API, as
backend/test_shift_soak.py builds them."""
import json

import pytest

from backend import goods, people
from backend.digital_twin import DigitalTwin
from backend.embodiment import seconds_to_ticks
from backend.faults import FAULT_RISKS
from backend.goods import StockLedger
from backend.models import CONFIG, OperatorStatus, RobotStatus, SimulationStatus
from backend.operator import Operator
from backend.robot import Robot
from backend.simulator import Simulator
from backend.warehouse import Warehouse

FLEET = (("PF1200-205", "AST-000205", (7, 4)), ("PF1200-206", "AST-000206", (7, 8)),
         ("HH300-207", "AST-000207", (2, 8)), ("TR50-201", "AST-000201", (8, 13)),
         ("TR50-101", "AST-000101", (8, 15)), ("PK30-203", "AST-000203", (20, 14)),
         ("IX2-208", "AST-000208", (20, 2)), ("IX2-209", "AST-000209", (21, 2)),
         ("SC1-204", "AST-000204", (7, 6)), ("H1-212", "AST-000212", (22, 7)))


@pytest.fixture(autouse=True)
def fault_free(monkeypatch):
    for risk in ("COLLISION_RISK", "FALSE_SUCCESS_RISK", *FAULT_RISKS.values()):
        monkeypatch.setitem(CONFIG, risk, 0.0)


def make_twin(tmp_path, name, layout="distribution_center"):
    return DigitalTwin(log_dir=str(tmp_path / name / "logs"), data_dir=str(tmp_path / name / "data"),
                       persist_logs=False, demo=True, demo_tasks=False, layout=layout)


def populated_floor(tmp_path, name="original"):
    twin = make_twin(tmp_path, name)
    for robot, asset, cell in FLEET:
        twin.add_robot(name=robot, asset_id=asset, position=cell)
    twin.add_robot(name="CX10-210", asset_id="AST-000210")
    twin.add_robot(name="CX10-211", asset_id="AST-000211")
    for number in range(6):
        twin.add_box(name=f"TOTE-{number}", kind="TOTE", sku=f"SKU-00{number + 1}", quantity=20, weight=21.5,
                     slot=f"TS-{8 + number:02d}-12-0")
    for number in range(3):
        twin.add_box(name=f"PAL-{number}", kind="PALLET", sku=f"SKU-00{number + 1}", quantity=40, weight=500.0,
                     slot=f"PR-{10 + number:02d}-05-0")
    for number in range(1, 11):
        twin.add_operator(name=f"W{number:02d}", worker_id=f"E-{10000 + number}")
    return twin


def reload(twin, tmp_path, name="loaded"):
    """Save `twin`, and load the file into a fresh twin of the same floor."""
    path = twin.save_state(str(tmp_path / f"{name}.json"))
    fresh = make_twin(tmp_path, name, layout=twin.layout_name)
    fresh.load_state(path)
    return fresh


def without_saved_at(payload):
    return {key: value for key, value in payload.items() if key != "saved_at"}


# --------------------------------------------------------------------------- #
# The file
# --------------------------------------------------------------------------- #
def test_a_save_is_version_2_and_names_its_floor(tmp_path):
    classic = make_twin(tmp_path, "classic", layout="classic")
    with open(classic.save_state(str(tmp_path / "classic.json")), encoding="utf-8") as handle:
        payload = json.load(handle)
    assert (payload["version"], payload["layout"]) == (2, "classic")
    assert payload["stock"] == {"locations": []} and payload["equipment"] is None
    assert payload["floor_seeded"] is classic.floor_seeded
    for key in ("robots", "boxes", "agents", "operators", "tasks", "schedules", "statistics", "simulation",
                "counters"):
        assert key in payload  # every version 1 section is still there
    floor = populated_floor(tmp_path)
    payload = floor.serialize()
    assert (payload["version"], payload["layout"], payload["floor_seeded"]) == (2, "distribution_center", False)
    assert len(payload["stock"]["locations"]) == 9
    assert payload["equipment"] == floor.equipment.to_state()
    floor.floor_seeded = True
    assert reload(floor, tmp_path).floor_seeded is True


def test_a_version_1_file_is_a_classic_save_and_still_loads(tmp_path):
    classic = make_twin(tmp_path, "classic", layout="classic")
    sim = Simulator(classic)
    classic.tasks.create_task({"type": "PICK_AND_DELIVER", "robot_id": "Robo-01", "box": "Box-A",
                               "destination": "loading_zone"})
    for _ in range(25):
        sim.tick()
    v1 = classic.serialize()
    for key in ("layout", "stock", "equipment", "floor_seeded"):
        del v1[key]
    v1["version"] = 1
    v1["robots"] = [robot.to_dict() for robot in classic.robots.values()]  # what a version 1 file holds
    path = tmp_path / "v1.json"
    path.write_text(json.dumps(v1), encoding="utf-8")
    fresh = make_twin(tmp_path, "fresh", layout="classic")
    seeded = fresh.floor_seeded
    fresh.load_state(str(path))
    robot, restored = classic.find_robot("Robo-01"), fresh.find_robot("Robo-01")
    assert (restored.position, restored.battery, restored.current_task, restored.total_distance) == \
        (robot.position, round(robot.battery, 1), robot.current_task, robot.total_distance)
    assert list(fresh.tasks.tasks) == list(classic.tasks.tasks)
    assert fresh.floor_seeded is seeded  # a version 1 save says nothing about the seed
    assert fresh.equipment is None and fresh.stock.locations() == []


def test_a_save_of_another_floor_is_refused_before_anything_changes(tmp_path):
    classic = make_twin(tmp_path, "classic", layout="classic")
    classic_path = classic.save_state(str(tmp_path / "classic.json"))
    floor = populated_floor(tmp_path)
    before = without_saved_at(floor.serialize())
    with pytest.raises(ValueError, match="of the classic floor, and this twin runs the distribution_center floor"):
        floor.load_state(classic_path)
    with open(classic_path, encoding="utf-8") as handle:
        v1 = json.load(handle)
    del v1["layout"]
    v1["version"] = 1  # a version 1 save is a classic save
    v1_path = tmp_path / "v1.json"
    v1_path.write_text(json.dumps(v1), encoding="utf-8")
    with pytest.raises(ValueError, match="of the classic floor"):
        floor.load_state(str(v1_path))
    v1["version"] = 3
    v1_path.write_text(json.dumps(v1), encoding="utf-8")
    with pytest.raises(ValueError, match="Unknown save version 3"):
        floor.load_state(str(v1_path))
    assert without_saved_at(floor.serialize()) == before
    classic_before = without_saved_at(classic.serialize())
    with pytest.raises(ValueError, match="of the distribution_center floor, and this twin runs the classic floor"):
        classic.load_state(floor.save_state(str(tmp_path / "floor.json")))
    assert without_saved_at(classic.serialize()) == classic_before


# --------------------------------------------------------------------------- #
# The floor comes back as it was
# --------------------------------------------------------------------------- #
def test_robots_people_stock_and_equipment_come_back_as_they_were(tmp_path):
    twin = populated_floor(tmp_path)
    sim = Simulator(twin)
    twin.simulation_status = SimulationStatus.RUNNING
    job = twin.tasks.create_task({"type": "TOTE_TO_STATION", "box_id": twin.find_box("TOTE-0").id})
    for _ in range(37):  # mid-route: part of a cell of travel credit, a step under way
        sim.tick()
    mover = twin.find_robot(job.robot_id)
    assert mover.move_accumulator and mover.activity
    # A safety wait, set by hand: what matters here is that every field of it comes back.
    forklift = twin.find_robot("PF1200-206")
    forklift.wait_reason, forklift.wait_started_tick, forklift.wait_task_id = "PERSON_IN_AISLE", 30, job.id
    forklift.wait_cell, forklift.wait_escalated, forklift.wait_ticks = (7, 9), True, 4
    forklift.battery = 87.123456  # the save keeps the battery unrounded
    # People: one in a zone, one walking between two.
    w01, w02 = twin.find_operator("W01"), twin.find_operator("W02")
    people.place(twin, w01, "pick_station_2")
    people.place(twin, w02, "intake_staging")
    people.start_transit(twin, w02, "workshop")
    # Stock: a count on record, two units gone missing, a pallet on the wrong level.
    twin.stock.record_count("TS-09-12-0", 20, tick=12)
    twin.stock.adjust_true("TS-10-12-0", -2)
    stray = twin.add_box(name="PAL-X", kind="PALLET", sku="SKU-009", quantity=30, weight=420.0, position=(5, 9))
    goods.store(twin, stray, "PR-11-05-1", true_slot_id="PR-11-05-2")
    # The line: an item waiting for pack cell 1's arm, a jam, a carton inside the sorter.
    equipment = twin.equipment
    item = twin.add_box(name="ITEM-1", kind="ITEM", sku="SKU-001", quantity=1, weight=0.8, position=(20, 14))
    equipment.place(item, (20, 15), twin.find_robot("PK30-203").id, order_id="ORD-0001", stop_at=(23, 15))
    equipment.jam((21, 15))
    carton = twin.add_box(name="CTN-1", kind="CARTON", sku=None, quantity=2, weight=1.6, position=(24, 15),
                          order_id="ORD-0002", destination="dock_5")
    for _ in range(seconds_to_ticks(CONFIG["SORTER_TRANSFER_S"]) + 1):
        equipment.tick()
    assert [inside.box_id for inside in equipment.sorter.inside] == [carton.id]

    loaded = reload(twin, tmp_path)
    assert [r.to_state() for r in loaded.robots.values()] == [r.to_state() for r in twin.robots.values()]
    assert [o.to_dict() for o in loaded.operators.values()] == [o.to_dict() for o in twin.operators.values()]
    assert [b.to_dict() for b in loaded.boxes.values()] == [b.to_dict() for b in twin.boxes.values()]
    assert [t.to_dict() for t in loaded.tasks.tasks.values()] == [t.to_dict() for t in twin.tasks.tasks.values()]
    assert loaded.stock.to_dict() == twin.stock.to_dict()
    assert loaded.equipment.to_state() == equipment.to_state()
    # What the version 1 load dropped or rounded:
    restored = loaded.find_robot("PF1200-206")
    assert (restored.wait_reason, restored.wait_started_tick, restored.wait_task_id, restored.wait_cell,
            restored.wait_escalated, restored.wait_ticks) == ("PERSON_IN_AISLE", 30, job.id, (7, 9), True, 4)
    assert restored.battery == 87.123456
    moved = loaded.find_robot(mover.name)
    assert (moved.move_accumulator, moved.action_timer, moved.activity, moved.lift_height_m, moved.model_code) == \
        (mover.move_accumulator, mover.action_timer, mover.activity, mover.lift_height_m, "AC-TR50")
    walker = loaded.find_operator("W02")
    assert (walker.zone, walker.transit_to, walker.transit_until_tick) == \
        ("intake_staging", "workshop", w02.transit_until_tick)
    assert loaded.find_operator("W09").employment_status == "ON_LEAVE"
    assert loaded.find_box("PAL-X").true_slot == "PR-11-05-2" and loaded.stock.location("PR-11-05-1").true_qty == 0
    assert loaded.stock.location("TS-10-12-0").discrepancy == -2
    assert loaded.stock.location("TS-09-12-0").counted_tick == 12
    line = loaded.equipment.conveyor
    assert line.item_at((20, 15)).stop_at == (23, 15) and line.jams == {(21, 15): twin.tick_count}
    assert [(inside.box_id, inside.ticks) for inside in loaded.equipment.sorter.inside] == [(carton.id, 1)]


def test_a_load_leaves_nothing_of_the_floor_it_replaced(tmp_path):
    # Fails if load_state keeps the old twin.stock or twin.equipment (the plan 1b
    # ledger minors), or forgets the hand-off sequence.
    twin = populated_floor(tmp_path)
    picker = twin.find_robot("PK30-203").id
    item = twin.add_box(name="ITEM-1", kind="ITEM", sku="SKU-001", quantity=1, weight=0.8, position=(20, 14))
    twin.equipment.place(item, (20, 15), picker)
    path = twin.save_state(str(tmp_path / "state.json"))
    saved_line = twin.equipment.to_state()
    # After the save: a tote stocked, a jam, two more hand-offs.
    extra = twin.add_box(name="TOTE-X", kind="TOTE", sku="SKU-009", quantity=5, weight=4.0, slot="TS-14-12-0")
    twin.equipment.jam((21, 15))
    other = twin.add_box(name="ITEM-2", kind="ITEM", sku="SKU-002", quantity=1, weight=0.5, position=(20, 14))
    twin.equipment.place(other, (19, 15), picker)
    twin.equipment.place(twin.add_box(name="ITEM-3", kind="ITEM", quantity=1, position=(20, 14)), (22, 15), picker)
    twin.load_state(path)
    assert twin.find_box("TOTE-X") is None
    assert twin.stock.location("TS-14-12-0") is None and twin.stock.slot_of(extra.id) is None
    assert twin.equipment.to_state() == saved_line and not twin.equipment.conveyor.jams
    again = twin.add_box(name="ITEM-4", kind="ITEM", sku="SKU-002", quantity=1, weight=0.5, position=(20, 14))
    assert twin.equipment.place(again, (19, 15), picker).handoff_id == "HO-00002"  # not HO-00004


def test_a_loaded_floor_carries_on_exactly_as_the_original(tmp_path):
    # Fails if a save drops anything a robot's next step depends on: its travel
    # credit (move_accumulator), step timer (action_timer), unrounded battery,
    # a person's walk, or a task's progress.
    twin = populated_floor(tmp_path)
    twin.simulation_status = SimulationStatus.RUNNING
    sim = Simulator(twin)
    for payload in ({"type": "TOTE_TO_STATION", "box_id": twin.find_box("TOTE-1").id},
                    {"type": "RETRIEVE_PALLET", "box_id": twin.find_box("PAL-1").id},
                    {"type": "CYCLE_COUNT", "face": "12,5"},
                    {"type": "PATROL"}):
        assert not twin.tasks.create_task(payload).is_terminal, payload
    people.place(twin, twin.find_operator("W05"), "intake_staging")
    people.start_transit(twin, twin.find_operator("W05"), "pallet_aisle_1")
    for tick in range(1, 401):  # until a robot is part-way through a timed step, after some driving
        sim.tick()
        if tick >= 30 and any(r.activity == "GRASP" and r.action_timer >= 3 for r in twin.robots.values()):
            break
    else:
        pytest.fail("no robot started a GRASP")
    loaded = reload(twin, tmp_path)
    # Both go on with a new Simulator: its retry and idle timers are the
    # loop's own, not the twin's state, so a save doesn't carry them.
    runs = []
    for floor in (twin, loaded):
        seen = []
        floor.events.subscribe(lambda event, seen=seen: seen.append(
            (event["event"], event["robot_id"], event["task_id"], event["box_id"], event["message"])))
        simulator = Simulator(floor)
        for _ in range(400):
            simulator.tick()
        runs.append({
            "events": seen,
            "robots": [(r.name, r.position, r.layer, r.altitude_m, r.battery, r.status, r.current_task, r.activity,
                        r.carrying_box) for r in floor.robots.values()],
            "tasks": [(t.id, t.type, t.status, t.robot_id, t.action_index, t.error)
                      for t in floor.tasks.tasks.values()],
            "boxes": [(b.id, b.position, b.status, b.slot) for b in floor.boxes.values()],
            "people": [(o.name, o.zone, o.transit_to) for o in floor.operators.values()],
            "stock": floor.stock.to_dict(),
        })
    original, restored = runs
    assert any(status.value == "COMPLETED" for _, _, status, *_ in original["tasks"])
    for key in original:
        assert restored[key] == original[key], key


# --------------------------------------------------------------------------- #
# Each part checks what it is given
# --------------------------------------------------------------------------- #
def test_a_robot_comes_back_mid_step_and_mid_wait():
    robot = Robot("robot_01", "TR50-201", (8, 13))
    robot.model_code, robot.activity, robot.lift_height_m = "AC-TR50", "LIFT_TO", 1.2345
    robot.battery, robot.move_accumulator, robot.action_timer, robot.wait_ticks = 64.98765, 0.45, 7, 3
    robot.wait_reason, robot.wait_started_tick, robot.wait_task_id = "PERSON_ON_CROSSING", 120, "task_009"
    robot.wait_cell, robot.wait_escalated, robot.status_before_stop = (17, 10), True, RobotStatus.MOVING
    robot.moves_since_drain, robot.low_battery_warned, robot.blocked_by = 2, True, "robot_02"
    restored = Robot.from_dict(robot.to_state())
    assert restored.to_state() == robot.to_state()
    assert (restored.battery, restored.lift_height_m) == (64.98765, 1.2345)  # unrounded
    old = Robot.from_dict(robot.to_dict())  # a version 1 robot: rounded, no wait start, no timers
    assert (old.battery, old.lift_height_m, old.wait_reason, old.wait_started_tick, old.action_timer) == \
        (65.0, 1.23, "PERSON_ON_CROSSING", None, 0)
    drone = Robot("robot_02", "CX10-210", (12, 2))
    drone.layer, drone.altitude_m = "AIR", 2.345
    assert Robot.from_dict(drone.to_state()).altitude_m == 2.345


def test_a_robot_on_an_impossible_layer_or_altitude_is_refused():
    data = Robot("robot_01", "CX10-210", (12, 2)).to_state()
    for layer, altitude, message in (("WATER", 0.0, "unknown layer 'WATER'"),
                                     ("GROUND", 2.0, "on the GROUND layer, so it must be at 0 m"),
                                     ("AIR", 0.0, "on the AIR layer, so it must be above 0 m"),
                                     ("AIR", float("nan"), "on the AIR layer"),
                                     ("AIR", "high", "altitude must be a number of metres")):
        with pytest.raises(ValueError, match=message):
            Robot.from_dict({**data, "layer": layer, "altitude_m": altitude})
    assert (Robot.from_dict({**data, "layer": "AIR", "altitude_m": 2.5}).altitude_m) == 2.5


def test_an_operator_comes_back_with_their_own_scopes_and_hr_state():
    operator = Operator("operator_001", "Jordan")
    operator.certification_scopes = {"humanoid_supervision": {"equipment": ["TS-H1"], "site": ["WH-01"]}}
    operator.employment_status = "ON_LEAVE"
    data = operator.to_dict()
    restored = Operator.from_dict(data)
    data["certification_scopes"]["humanoid_supervision"]["site"].append("WH-02")  # the save changing later
    assert restored.certification_scopes == {"humanoid_supervision": {"equipment": ["TS-H1"], "site": ["WH-01"]}}
    assert restored.employment_status == "ON_LEAVE"


def test_an_operator_out_of_place_is_refused():
    floor = Warehouse(layout="distribution_center")
    base = Operator("operator_001", "Sam").to_dict()
    for change, message in (({"zone": "intake_staging", "transit_to": "workshop"}, "with no arrival tick"),
                            ({"zone": "intake_staging", "transit_until_tick": 40}, "is walking nowhere"),
                            ({"transit_to": "workshop", "transit_until_tick": 40}, "is not on the floor"),
                            ({"zone": "workshop", "status": OperatorStatus.OFF_DUTY.value}, "is off duty"),
                            ({"zone": "canteen"}, "'canteen', which is not a zone of the distribution_center floor"),
                            ({"zone": "workshop", "transit_to": "moon", "transit_until_tick": 9}, "'moon'")):
        with pytest.raises(ValueError, match=message):
            Operator.from_dict({**base, **change}, floor)
    assert Operator.from_dict({**base, "zone": "canteen"}).zone == "canteen"  # zones are checked only on a floor
    walking = Operator.from_dict({**base, "zone": "workshop", "transit_to": "intake_staging",
                                  "transit_until_tick": 40}, floor)
    assert walking.in_transit


def test_a_ledger_comes_back_through_its_own_checks():
    floor = Warehouse(layout="distribution_center")
    good = {"slot_id": "TS-10-12-1", "box_id": "box_001", "sku": "SKU-001", "recorded_qty": 12, "true_qty": 11,
            "counted_qty": 11, "counted_tick": 40}
    ledger = StockLedger.from_dict({"locations": [good]}, floor)
    assert ledger.to_dict() == {"locations": [good]} and ledger.slot_of("box_001") == "TS-10-12-1"
    other = {**good, "slot_id": "TS-11-12-1", "box_id": "box_002"}
    for locations, message in (([{**good, "colour": "red"}], r"Unknown stock location field\(s\) \['colour'\]"),
                               ([{key: value for key, value in good.items() if key != "true_qty"}], "missing"),
                               ([{**good, "slot_id": "TS-99-99-9"}], "Unknown slot 'TS-99-99-9'"),
                               ([good, {**other, "slot_id": "TS-10-12-1"}], "already holds box_001"),
                               ([good, {**other, "box_id": "box_001"}], "box_001 is already in slot TS-10-12-1"),
                               ([{**good, "true_qty": -1}], "true_qty must be a whole number, 0 or more"),
                               ([{**good, "recorded_qty": 2.5}], "recorded_qty must be a whole number"),
                               ([{**good, "counted_tick": True}], "counted_tick must be a whole number")):
        with pytest.raises(ValueError, match=message):
            StockLedger.from_dict({"locations": locations}, floor)


def test_the_equipment_refuses_a_save_off_its_line_and_keeps_what_it_has(tmp_path):
    twin = populated_floor(tmp_path)
    item = twin.add_box(name="ITEM-1", kind="ITEM", sku="SKU-001", quantity=1, weight=0.8, position=(19, 15))
    before = twin.equipment.to_state()
    entry = {"cell": {"x": 20, "y": 15}, "box_id": item.id}
    for data, message in (({"items": [{**entry, "cell": {"x": 5, "y": 5}}]}, r"\{'x': 5, 'y': 5\}"),
                          ({"items": [entry, entry]}, "two items on conveyor cell \\(20,15\\)"),
                          ({"items": [{**entry, "stop_at": {"x": 23, "y": 14}}]}, "stop in the save"),
                          ({"jams": [{"cell": {"x": 25, "y": 15}, "since_tick": 3}]}, "A jam in the save")):
        with pytest.raises(ValueError, match=message):
            twin.equipment.load_state(data)
    assert twin.equipment.to_state() == before
    twin.equipment.load_state({})  # an empty save empties the line
    assert twin.equipment.to_state() == {"items": [], "jams": [], "sorter": [], "handoffs": [], "handoff_seq": 0}
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/bin/python -m pytest -o addopts="" -q backend/test_save_load_v2.py`
Expected: `12 failed` — `AttributeError: 'Robot' object has no attribute 'to_state'`, `'Equipment' object has no attribute 'to_state'`, `KeyError: 'layout'`, `TypeError: Operator.from_dict() takes 1 positional argument but 2 were given`, `TypeError: StockLocation.__init__() got an unexpected keyword argument 'colour'`, a shared credential-scope list, `DID NOT RAISE`, and the carry-on comparison (`AssertionError: events`).

- [ ] **Step 3: A robot's whole state, and a checked layer**

`to_state` is what a save holds; `to_dict` (the snapshot's view) keeps its rounding. `from_dict` reads either, so a version 1 file still loads.

**Replace in** `backend/robot.py`:

```python
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from .embodiment import GROUND, MobilityProfile
from .models import (
```

with:

```python
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .embodiment import AIR, GROUND, LAYERS, MobilityProfile
from .models import (
```

**Replace in** `backend/robot.py`:

```python
    now_iso,
)


class Robot:
```

with:

```python
    now_iso,
)


def _saved_layer(data: Dict[str, Any]) -> Tuple[str, float]:
    """A saved robot's layer and altitude, checked. DigitalTwin.set_robot_layer
    keeps a robot on the ground at 0 m and one in the air above it, so a save
    that breaks that is damaged and is refused rather than restored."""
    name = data.get("name", "A robot")
    layer = data.get("layer", GROUND)
    if layer not in LAYERS:
        raise ValueError(f"{name} is on an unknown layer {layer!r} (known: {list(LAYERS)})")
    try:
        altitude = float(data.get("altitude_m", 0.0))
    except (TypeError, ValueError):
        raise ValueError(f"{name}'s altitude must be a number of metres, not {data.get('altitude_m')!r}") from None
    if not math.isfinite(altitude) or (altitude != 0.0 if layer == GROUND else altitude <= 0.0):
        where = "at 0 m" if layer == GROUND else "above 0 m"
        raise ValueError(f"{name} is on the {layer} layer, so it must be {where}, not {altitude} m")
    return layer, altitude


class Robot:
```

**Replace in** `backend/robot.py`:

```python
            "updated_at": self.updated_at,
        }

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "Robot":
        robot = Robot(
```

with:

```python
            "updated_at": self.updated_at,
        }

    def to_state(self) -> Dict[str, Any]:
        """What a save holds (DigitalTwin.save_state, spec §4.3): to_dict, with
        the battery, altitude and lift height unrounded, plus the bookkeeping a
        robot needs to carry on exactly where it was — its travel credit, step
        timer, traffic wait, the status a stop saved and its whole safety wait."""
        data = self.to_dict()
        data.update({
            "battery": self.battery,
            "battery_consumed": self.battery_consumed,
            "altitude_m": self.altitude_m,
            "lift_height_m": self.lift_height_m,
            "status_before_stop": self.status_before_stop.value if self.status_before_stop else None,
            "moves_since_drain": self.moves_since_drain,
            "move_accumulator": self.move_accumulator,
            "wait_ticks": self.wait_ticks,
            "action_timer": self.action_timer,
            "low_battery_warned": self.low_battery_warned,
            "wait_started_tick": self.wait_started_tick,
            "wait_task_id": self.wait_task_id,
            "wait_cell": cell_dict(self.wait_cell),
            "wait_escalated": self.wait_escalated,
        })
        return data

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "Robot":
        """A robot from to_state() — or from to_dict(), which is what a version
        1 save holds; what that lacks starts as on a new robot. A layer or
        altitude no robot can have raises ValueError."""
        layer, altitude = _saved_layer(data)
        robot = Robot(
```

**Replace in** `backend/robot.py`:

```python
        robot.fleet_hold = data.get("fleet_hold")
        robot.layer = data.get("layer", GROUND)
        robot.altitude_m = float(data.get("altitude_m", 0.0))
        robot.activity = data.get("activity")
```

with:

```python
        robot.fleet_hold = data.get("fleet_hold")
        robot.layer, robot.altitude_m = layer, altitude
        robot.activity = data.get("activity")
```

**Replace in** `backend/robot.py`:

```python
        robot.replan_count = data.get("replan_count", 0)
        robot.created_at = data.get("created_at", robot.created_at)
```

with:

```python
        robot.replan_count = data.get("replan_count", 0)
        before = data.get("status_before_stop")
        robot.status_before_stop = RobotStatus(before) if before else None
        robot.moves_since_drain = int(data.get("moves_since_drain", 0))
        robot.move_accumulator = float(data.get("move_accumulator", 0.0))
        robot.wait_ticks = int(data.get("wait_ticks", 0))
        robot.action_timer = int(data.get("action_timer", 0))
        robot.low_battery_warned = bool(data.get("low_battery_warned", False))
        robot.blocked_by = data.get("blocked_by")
        robot.last_error = data.get("last_error")
        robot.ota_installing = bool(data.get("ota_installing", False))
        # The safety wait it was in (Simulator._safety_wait): a version 1 save
        # has only the reason, and a wait with no start tick never escalates.
        robot.wait_reason = data.get("wait_reason")
        robot.wait_started_tick = data.get("wait_started_tick")
        robot.wait_task_id = data.get("wait_task_id")
        robot.wait_cell = cell_tuple(data.get("wait_cell"))
        robot.wait_escalated = bool(data.get("wait_escalated", False))
        robot.created_at = data.get("created_at", robot.created_at)
```


- [ ] **Step 4: A person comes back where they can be**

**Replace in** `backend/operator.py`:

```python
    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "Operator":
        operator = Operator(
```

with:

```python
    @staticmethod
    def from_dict(data: Dict[str, Any], warehouse: Optional[Any] = None) -> "Operator":
        """An operator from to_dict(). Where they are must make sense (people.py
        keeps it so): someone walking is walking from a zone, with an arrival
        tick; an off-duty person is off the floor; and, given the `warehouse`,
        both zones exist on it. Anything else raises ValueError."""
        operator = Operator(
```

**Replace in** `backend/operator.py`:

```python
        operator.transit_until_tick = data.get("transit_until_tick")
        operator.certification_scopes = dict(data.get("certification_scopes") or {})
        operator.employment_status = data.get("employment_status")
        return operator
```

with:

```python
        operator.transit_until_tick = data.get("transit_until_tick")
        # A copy all the way down: the restored person never shares a list with the save.
        operator.certification_scopes = {
            code: {key: list(values) for key, values in (scope or {}).items()}
            for code, scope in (data.get("certification_scopes") or {}).items()
        }
        operator.employment_status = data.get("employment_status")
        operator._check_place(warehouse)
        return operator

    def _check_place(self, warehouse: Optional[Any]) -> None:
        name, zone, to, until = self.name, self.zone, self.transit_to, self.transit_until_tick
        if to is not None and until is None:
            raise ValueError(f"{name} is walking to {to} with no arrival tick")
        if until is not None and to is None:
            raise ValueError(f"{name} has an arrival tick ({until}) but is walking nowhere")
        if to is not None and zone is None:
            raise ValueError(f"{name} is walking to {to} but is not on the floor")
        if zone is not None and self.status == OperatorStatus.OFF_DUTY:
            raise ValueError(f"{name} is off duty and cannot be on the floor (in {zone})")
        if warehouse is not None:
            for key in (zone, to):
                if key is not None and key not in warehouse.zones:
                    raise ValueError(f"{name} is in {key!r}, which is not a zone of the {warehouse.layout_name} floor")
```


- [ ] **Step 5: The ledger comes back through its own checks**

The signature stays `from_dict(data, warehouse=None)` (`backend/test_goods.py` calls it that way). `put()` does the slot checks, so an unknown key is a plain `ValueError`, not `StockLocation(**item)`'s `TypeError`.

**Replace in** `backend/goods.py`:

```python
    @classmethod
    def from_dict(cls, data: Dict[str, Any], warehouse: Optional[Any] = None) -> "StockLedger":
        ledger = cls(warehouse)
        for item in data.get("locations", []):
            location = StockLocation(**item)
            ledger._locations[location.slot_id] = location
            ledger._slot_of_box[location.box_id] = location.slot_id
        return ledger
```

with:

```python
    @classmethod
    def from_dict(cls, data: Dict[str, Any], warehouse: Optional[Any] = None) -> "StockLedger":
        """A ledger from to_dict() (a save). Every location goes back in through
        put(), so a save can't name an unknown slot, fill one slot twice or keep
        one box in two slots. A field StockLocation doesn't have, a missing one,
        or a quantity that isn't a whole number of 0 or more raises ValueError."""
        def count(item: Dict[str, Any], key: str) -> Optional[int]:
            """A saved quantity or tick: a whole number, 0 or more (None if absent)."""
            value = item.get(key)
            if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
                raise ValueError(f"{item['slot_id']}: {key} must be a whole number, 0 or more, not {value!r}")
            return value

        ledger = cls(warehouse)
        known = list(StockLocation.__dataclass_fields__)
        for item in data.get("locations", []):
            unknown = sorted(set(item) - set(known))
            if unknown:
                raise ValueError(f"Unknown stock location field(s) {unknown} (known: {known})")
            missing = [key for key in ("slot_id", "box_id", "recorded_qty", "true_qty") if item.get(key) is None]
            if missing:
                raise ValueError(f"A saved stock location is missing {missing}")
            location = ledger.put(item["slot_id"], item["box_id"], item.get("sku"), count(item, "recorded_qty"))
            location.true_qty = count(item, "true_qty")
            location.counted_qty = count(item, "counted_qty")
            location.counted_tick = count(item, "counted_tick")
        return ledger
```


- [ ] **Step 6: The equipment's save and load**

`load_state` sets the line's items directly: `Conveyor.load` would reset each item's ticks.

**Replace in** `backend/equipment.py`:

```python
from .embodiment import seconds_to_ticks
from .models import CONFIG, BoxKind, BoxStatus, Cell, EventType, LogCategory, LogLevel, cell_dict


@dataclass
```

with:

```python
from .embodiment import seconds_to_ticks
from .models import CONFIG, BoxKind, BoxStatus, Cell, EventType, LogCategory, LogLevel, cell_dict, cell_tuple


@dataclass
```

**Replace in** `backend/equipment.py`:

```python
        data["stop_at"] = cell_dict(self.stop_at)
        return data


@dataclass
class Handoff:
```

with:

```python
        data["stop_at"] = cell_dict(self.stop_at)
        return data

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "LineItem":
        return LineItem(data["box_id"], data.get("order_id"), data.get("task_id"), cell_tuple(data.get("stop_at")),
                        int(data.get("ticks", 0)), data.get("routed_to"))


@dataclass
class Handoff:
```

**Replace in** `backend/equipment.py`:

```python
            "task_id": self.task_id,
        }


class Conveyor:
```

with:

```python
            "task_id": self.task_id,
        }

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "Handoff":
        return Handoff(data["handoff_id"], data["box_id"], data.get("order_id"), data["from"], data["to"],
                       cell_tuple(data["cell"]), int(data["tick"]), dict(data.get("giver_reported") or {}),
                       dict(data.get("receiver_observed") or {}), data.get("task_id"))


class Conveyor:
```

**Replace in** `backend/equipment.py`:

```python
                        item.task_id, item.order_id)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "conveyor": self.conveyor.to_dict(),
```

with:

```python
                        item.task_id, item.order_id)

    # ---- save and load (DigitalTwin.save_state / load_state, spec §4.3) --- #
    def to_state(self) -> Dict[str, Any]:
        """Everything a save needs to restart the line exactly: each item on it
        (its stop_at and ticks too), the jams, the cartons inside the sorter,
        the hand-off records and the hand-off sequence. Items and jams keep
        their order (the jam jobs follow it)."""
        return {
            "items": [{"cell": cell_dict(cell), **item.to_dict()} for cell, item in self.conveyor.items.items()],
            "jams": [{"cell": cell_dict(cell), "since_tick": tick} for cell, tick in self.conveyor.jams.items()],
            "sorter": [item.to_dict() for item in self.sorter.inside],
            "handoffs": [handoff.to_dict() for handoff in self.handoffs],
            "handoff_seq": self._handoff_seq,
        }

    def load_state(self, data: Dict[str, Any]) -> None:
        """Replace the line, the sorter and the hand-off log with a to_state()
        snapshot; nothing that was on them before survives. It is all checked
        first: an item or jam off the line, two items on one cell, or a stop
        off the line raises ValueError and changes nothing."""
        items: Dict[Cell, LineItem] = {}
        for raw in data.get("items", []):
            cell = self._saved_cell(raw.get("cell"), "An item")
            if cell in items:
                raise ValueError(f"The save puts two items on conveyor cell ({cell[0]},{cell[1]})")
            items[cell] = LineItem.from_dict(raw)
            if items[cell].stop_at is not None:
                self._saved_cell(raw["stop_at"], f"{items[cell].box_id}'s stop")
        jams = {self._saved_cell(raw.get("cell"), "A jam"): int(raw["since_tick"]) for raw in data.get("jams", [])}
        inside = [LineItem.from_dict(raw) for raw in data.get("sorter", [])]
        handoffs = [Handoff.from_dict(raw) for raw in data.get("handoffs", [])]
        sequence = int(data.get("handoff_seq", 0))
        self.conveyor.items, self.conveyor.jams, self.sorter.inside = items, jams, inside
        self.handoffs.clear()
        self.handoffs.extend(handoffs)
        self._handoff_seq = sequence

    def _saved_cell(self, raw: Optional[Dict[str, int]], what: str) -> Cell:
        cell = cell_tuple(raw)
        if cell is None or cell not in self.conveyor:
            raise ValueError(f"{what} in the save is at {raw}, which is not a conveyor cell")
        return cell

    def to_dict(self) -> Dict[str, Any]:
        return {
            "conveyor": self.conveyor.to_dict(),
```


- [ ] **Step 7: Save version 2, and load it whole or not at all**

**Replace in** `backend/digital_twin.py`:

```python
    # Persistence
    # ------------------------------------------------------------------ #
    def serialize(self) -> Dict[str, Any]:
        with self.lock:
            return {
                "version": 1,
                "saved_at": now_iso(),
                "warehouse": {"width": self.warehouse.width, "height": self.warehouse.height},
                "robots": [r.to_dict() for r in self.robots.values()],
                "boxes": [b.to_dict() for b in self.boxes.values()],
                "agents": [a.to_dict() for a in self.agents.values()],
                "operators": [o.to_dict() for o in self.operators.values()],
                "tasks": [t.to_dict() for t in self.tasks.tasks.values()],
                "schedules": self.scheduler.to_dict(),
                "statistics": dict(self.statistics),
                "simulation": {
                    "status": self.simulation_status.value,
                    "speed": self.simulation_speed,
                    "tick": self.tick_count,
                    "time": self.simulation_time,
                },
                "counters": {
                    "robot": self.ids.peek("robot"),
                    "box": self.ids.peek("box"),
                    "task": self.ids.peek("task"),
                    "agent": self.ids.peek("agent"),
                    "operator": self.ids.peek("operator"),
                },
            }
```

with:

```python
    # Persistence
    # ------------------------------------------------------------------ #
    #: The save format save_state writes (spec §4.3). Version 1 — no layout,
    #: stock or equipment — is a classic save, and still loads on classic.
    STATE_VERSION = 2

    def serialize(self) -> Dict[str, Any]:
        with self.lock:
            return {
                "version": self.STATE_VERSION,
                "layout": self.layout_name,
                "saved_at": now_iso(),
                "warehouse": {"width": self.warehouse.width, "height": self.warehouse.height},
                "robots": [r.to_state() for r in self.robots.values()],
                "boxes": [b.to_dict() for b in self.boxes.values()],
                "agents": [a.to_dict() for a in self.agents.values()],
                "operators": [o.to_dict() for o in self.operators.values()],
                "tasks": [t.to_dict() for t in self.tasks.tasks.values()],
                "schedules": self.scheduler.to_dict(),
                "statistics": dict(self.statistics),
                "simulation": {
                    "status": self.simulation_status.value,
                    "speed": self.simulation_speed,
                    "tick": self.tick_count,
                    "time": self.simulation_time,
                },
                "counters": {
                    "robot": self.ids.peek("robot"),
                    "box": self.ids.peek("box"),
                    "task": self.ids.peek("task"),
                    "agent": self.ids.peek("agent"),
                    "operator": self.ids.peek("operator"),
                },
                "stock": self.stock.to_dict(),
                "equipment": self.equipment.to_state() if self.equipment is not None else None,
                "floor_seeded": self.floor_seeded,
            }
```

**Replace in** `backend/digital_twin.py`:

```python
    def load_state(self, path: Optional[str] = None) -> None:
        path = path or self.state_path
        if not os.path.exists(path):
            raise FileNotFoundError(f"No saved state at {path}")
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)

        with self.lock:
            self.robots.clear()
            self.boxes.clear()
            self.agents.clear()
            self.operators.clear()
            self.tasks.clear()
            for data in payload.get("robots", []):
                robot = Robot.from_dict(data)
                self.robots[robot.id] = robot
            for data in payload.get("boxes", []):
                box = Box.from_dict(data)
                self.boxes[box.id] = box
            for data in payload.get("agents", []):
                agent = Agent.from_dict(data)
                self.agents[agent.id] = agent
            for data in payload.get("operators", []):
                operator = Operator.from_dict(data)
                self.operators[operator.id] = operator
            sequence = 0
            for data in payload.get("tasks", []):
                task = Task.from_dict(data)
                sequence += 1
                task.sequence = sequence
                self.tasks.tasks[task.id] = task
            self.tasks._sequence = sequence
            self.fleet.rebind_all()
```

with:

```python
    def load_state(self, path: Optional[str] = None) -> None:
        """Restore a save_state file (spec §4.3): version 2, or version 1 (a
        classic save). A save of another floor is refused with a ValueError
        (Plan ruling 5): its robots are bound to another inventory profile's
        assets. The whole file is rebuilt first, so a damaged save changes
        nothing; then robots, boxes, people, tasks, the stock ledger and the
        equipment are all replaced — nothing from before the load survives."""
        path = path or self.state_path
        if not os.path.exists(path):
            raise FileNotFoundError(f"No saved state at {path}")
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        version = payload.get("version", 1)
        if version not in (1, self.STATE_VERSION):
            raise ValueError(f"Unknown save version {version!r} (this twin reads 1 and {self.STATE_VERSION})")
        layout = payload.get("layout", "classic") if version == self.STATE_VERSION else "classic"
        if layout != self.layout_name:
            raise ValueError(f"This save is of the {layout} floor, and this twin runs the {self.layout_name} "
                             f"floor: start the app with WAREHOUSE_LAYOUT={layout} to load it")

        with self.lock:
            robots = [Robot.from_dict(data) for data in payload.get("robots", [])]
            boxes = [Box.from_dict(data) for data in payload.get("boxes", [])]
            agents = [Agent.from_dict(data) for data in payload.get("agents", [])]
            operators = [Operator.from_dict(data, self.warehouse) for data in payload.get("operators", [])]
            tasks = [Task.from_dict(data) for data in payload.get("tasks", [])]
            stock = StockLedger.from_dict(payload.get("stock") or {}, self.warehouse)
            equipment = Equipment.for_floor(self)
            if equipment is not None:
                equipment.load_state(payload.get("equipment") or {})
            held = [location.box_id for location in stock.locations()]
            if equipment is not None:
                held += [item.box_id for item in [*equipment.conveyor.items.values(), *equipment.sorter.inside]]
            unknown = sorted(set(held) - {box.id for box in boxes})
            if unknown:
                raise ValueError(f"The save's stock or conveyor holds boxes it has no record of: {unknown}")

            self.robots.clear()
            self.boxes.clear()
            self.agents.clear()
            self.operators.clear()
            self.tasks.clear()
            self.robots.update((robot.id, robot) for robot in robots)
            self.boxes.update((box.id, box) for box in boxes)
            self.agents.update((agent.id, agent) for agent in agents)
            self.operators.update((operator.id, operator) for operator in operators)
            for sequence, task in enumerate(tasks, start=1):
                task.sequence = sequence
                self.tasks.tasks[task.id] = task
            self.tasks._sequence = len(tasks)
            self.stock = stock
            self.equipment = equipment
            # A version 1 save says nothing about the seed: the twin's own boot decides.
            self.floor_seeded = bool(payload.get("floor_seeded", self.floor_seeded))
            self.fleet.rebind_all()
```


- [ ] **Step 8: Run the new tests**

Run: `.venv/bin/python -m pytest -o addopts="" -q backend/test_save_load_v2.py`
Expected: `12 passed`.

- [ ] **Step 9: Run the full suite**

Run: `.venv/bin/python -m pytest -o addopts="" -q`
Expected: `2 failed, 812 passed` (Task 2's count plus this task's 12 tests; the two failures are the allowed ones).

- [ ] **Step 10: Commit**

```bash
git add backend/robot.py backend/operator.py backend/goods.py backend/equipment.py backend/digital_twin.py backend/test_save_load_v2.py
git commit -m "feat: save and load v2 — the floor, its stock and its equipment

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Save and load v2 — the shift engine and orders

The save gains `"shift"` (spec §4.3: "clock, RNG state, config, order table"): the engine's status, seed, pace and rates, its `random.Random` state, when each stream is next due and when it was paused, the truck and rack-face rotations, who is stepping aside until when (`yield_until`), its counters, and every order with its stages and lines in creation order, with the order id sequence. `ShiftEngine.load_state` restores all of it into the twin's own engine — the one that subscribed to `twin.events` once, at construction (plan 1b's carry), so a load never builds a second engine or adds a second listener. Every part is checked before anything changes. A save with no shift (a version 1 classic file) leaves a fresh, paused engine, so no order from before the load survives. The test that matters most: a twin saved mid-shift and loaded into a fresh twin then makes the same orders and jobs as the original, tick for tick.

**Files:**
- Modify: `backend/operations/orders.py` (`Stage.from_dict`, `Order.from_dict`, `OrderBook.to_state` / `load_state`)
- Modify: `backend/operations/shift.py` (`ShiftEngine.to_state` / `load_state`)
- Modify: `backend/digital_twin.py` (`"shift"` in Task 3's `serialize` and `load_state`)
- Test: `backend/test_save_load_shift.py` (create)

**Interfaces:**
- Consumes (Task 3): `DigitalTwin.serialize` / `load_state` (version 2, everything checked before the swap); `Equipment.to_state`.
- Produces:
  - `ShiftEngine.to_state() -> Dict[str, Any]`: `{"status", "seed", "pace", "rates", "rng": [version, [internal state…], gauss_next], "next_due", "paused_at", "truck_count", "face_index", "yield_until", "counters", "orders": OrderBook.to_state()}`.
  - `ShiftEngine.load_state(data) -> None`: restores into `self` (never a new engine); `ValueError` and no change for an unknown status, a seed that isn't a whole number, a pace not above 0, an unknown or bad rate or stream, a RUNNING shift missing a stream's next due time, a damaged RNG state, a negative or non-whole count, or a damaged order. Missing counters start at 0.
  - `OrderBook.to_state() -> Dict[str, Any]` (`{"orders": [Order.to_dict() …], "seq": int}`) and `OrderBook.load_state(data) -> None` (replaces every order; `ValueError` for an id twice or a sequence behind its orders).
  - `Order.from_dict(data) -> Order`, `Stage.from_dict(data) -> Stage`: `ValueError` for an unknown or missing field, an unknown kind or status, or a stage that follows one the order doesn't have.
  - The save's `"shift"`: `ShiftEngine.to_state()` on every floor (a paused, empty engine on classic). `load_state` restores it last among its checks; without one it calls `ShiftEngine.reset()`.

- [ ] **Step 1: Write the failing tests**

The floor is built through the public API with `backend/test_shift_soak.py`'s fleet, and runs at pace 2. The determinism test saves 3000 ticks (7.5 sim-minutes) into the shift, and both twins go on for 2000 ticks with a new `Simulator` each (the loop's own retry and idle timers are not the twin's state).

**Create** `backend/test_save_load_shift.py`:

```python
"""Save and load, version 2 — the shift engine and its orders (multi-
embodiment spec §4.3, §11): a save carries the shift's status, config, RNG
and streams and every order with its stages; a load restores them into the
twin's own engine (the one listening to twin.events, never a second one);
and a twin saved mid-shift and loaded into a fresh twin goes on to make the
same orders and jobs as the original. The floors are built through the
public API, as backend/test_shift_soak.py builds them."""
import json

import pytest

from backend.digital_twin import DigitalTwin
from backend.faults import FAULT_RISKS
from backend.models import CONFIG, SimulationStatus
from backend.operations import Order, Stage
from backend.simulator import Simulator

FLEET = (("PF1200-205", "AST-000205", (7, 4)), ("PF1200-206", "AST-000206", (7, 8)),
         ("HH300-207", "AST-000207", (2, 8)), ("TR50-201", "AST-000201", (8, 13)),
         ("TR50-101", "AST-000101", (8, 15)), ("PK30-203", "AST-000203", (20, 14)),
         ("IX2-208", "AST-000208", (20, 2)), ("IX2-209", "AST-000209", (21, 2)),
         ("SC1-204", "AST-000204", (7, 6)), ("H1-212", "AST-000212", (22, 7)))


@pytest.fixture(autouse=True)
def fault_free(monkeypatch):
    for risk in ("COLLISION_RISK", "FALSE_SUCCESS_RISK", *FAULT_RISKS.values()):
        monkeypatch.setitem(CONFIG, risk, 0.0)


def make_twin(tmp_path, name, layout="distribution_center"):
    return DigitalTwin(log_dir=str(tmp_path / name / "logs"), data_dir=str(tmp_path / name / "data"),
                       persist_logs=False, demo=True, demo_tasks=False, layout=layout)


def populated_floor(tmp_path, name="original"):
    twin = make_twin(tmp_path, name)
    for robot, asset, cell in FLEET:
        twin.add_robot(name=robot, asset_id=asset, position=cell)
    twin.add_robot(name="CX10-210", asset_id="AST-000210")
    twin.add_robot(name="CX10-211", asset_id="AST-000211")
    for number in range(6):
        twin.add_box(name=f"TOTE-{number}", kind="TOTE", sku=f"SKU-00{number + 1}", quantity=20, weight=21.5,
                     slot=f"TS-{8 + number:02d}-12-0")
    for number in range(3):
        twin.add_box(name=f"PAL-{number}", kind="PALLET", sku=f"SKU-00{number + 1}", quantity=40, weight=500.0,
                     slot=f"PR-{10 + number:02d}-05-0")
    for number in range(1, 11):
        twin.add_operator(name=f"W{number:02d}", worker_id=f"E-{10000 + number}")
    return twin


def running_shift(tmp_path, ticks):
    """The populated floor `ticks` into a shift at pace 2, and its simulator."""
    twin = populated_floor(tmp_path)
    sim = Simulator(twin)
    twin.simulation_status = SimulationStatus.RUNNING
    twin.shift.configure(pace=2.0)
    twin.shift.start()
    for _ in range(ticks):
        sim.tick()
    return twin, sim


def without_saved_at(payload):
    return {key: value for key, value in payload.items() if key != "saved_at"}


# --------------------------------------------------------------------------- #
# Orders and stages
# --------------------------------------------------------------------------- #
def test_an_order_and_its_stages_round_trip(tmp_path):
    book = make_twin(tmp_path, "floor").shift.orders
    order = book.customer([{"sku": "SKU-001", "units": 2}, {"sku": "SKU-002", "units": 1}], "dock_4")
    order.status, order.pick_station, order.pack_cell = "IN_PROGRESS", "pick_station_1", "pack_cell_1"
    order.lines[0].update(tote_id="box_001", placed=1)
    stage = order.stages[0]
    stage.status, stage.task_id, stage.attempts, stage.retry_at, stage.error = "ACTIVE", "task_004", 2, 31.5, "busy"
    assert Order.from_dict(order.to_dict()) == order
    assert Stage.from_dict(stage.to_dict()) == stage
    restored = Order.from_dict(json.loads(json.dumps(order.to_dict())))  # through the file, as a save goes
    assert restored == order and restored.stages[1].after == [0]


def test_a_damaged_order_or_stage_is_refused(tmp_path):
    order = make_twin(tmp_path, "floor").shift.orders.count("10,5").to_dict()
    stage = order["stages"][0]
    for data, message in (({**order, "colour": "red"}, r"Unknown order field\(s\) \['colour'\]"),
                          ({key: value for key, value in order.items() if key != "kind"}, "missing \\['kind'\\]"),
                          ({**order, "kind": "PIZZA"}, "unknown kind 'PIZZA'"),
                          ({**order, "status": "LOST"}, "unknown status 'LOST'"),
                          ({**order, "stages": [{**stage, "after": [3]}]}, "follows a stage the order doesn't have"),
                          ({**order, "stages": [{**stage, "status": "SLEEPING"}]}, "unknown status 'SLEEPING'"),
                          ({**order, "stages": [{**stage, "colour": "red"}]}, r"Unknown stage field\(s\)")):
        with pytest.raises(ValueError, match=message):
            Order.from_dict(data)


def test_the_order_book_keeps_its_orders_in_order_and_its_id_sequence(tmp_path):
    book = make_twin(tmp_path, "floor").shift.orders
    book.customer([{"sku": "SKU-001", "units": 2}], "dock_5")
    book.count("10,5")
    book.returned("box_009")
    state = json.loads(json.dumps(book.to_state()))
    other = make_twin(tmp_path, "other").shift.orders
    other.count("11,5")
    other.load_state(state)
    assert list(other.orders) == ["ORD-0001", "ORD-0002", "ORD-0003"]  # advance() goes through them in this order
    assert other.orders["ORD-0001"] == book.orders["ORD-0001"]
    assert other.count("12,5").order_id == "ORD-0004"
    for damaged, message in (({**state, "seq": 2}, "at least 3"),
                             ({**state, "orders": [state["orders"][0]] * 2}, "ORD-0001 twice")):
        with pytest.raises(ValueError, match=message):
            other.load_state(damaged)
    assert list(other.orders) == ["ORD-0001", "ORD-0002", "ORD-0003", "ORD-0004"]  # unchanged
    other.load_state({**state, "seq": 7})  # orders 4-7 have been pruned, say: the ids still go on from 7
    assert other.count("12,5").order_id == "ORD-0008"


# --------------------------------------------------------------------------- #
# The engine
# --------------------------------------------------------------------------- #
def test_the_shift_comes_back_into_the_twins_own_engine(tmp_path):
    twin, _ = running_shift(tmp_path, 600)  # 90 s in: a truck and a count have been generated
    twin.shift.configure(rates={"returns": 9.0})
    twin.shift.yield_until["operator_003"] = 75.5
    assert twin.shift.orders.orders and twin.shift._truck_count and twin.shift._face_index
    path = twin.save_state(str(tmp_path / "state.json"))
    fresh = make_twin(tmp_path, "fresh")
    engine, listeners = fresh.shift, len(fresh.events._subscribers)
    fresh.load_state(path)
    # Fails if load_state builds a new ShiftEngine: the old one stays subscribed to twin.events.
    assert fresh.shift is engine and len(fresh.events._subscribers) == listeners
    assert fresh.shift.to_state() == twin.shift.to_state()
    assert fresh.shift.status == "RUNNING" and fresh.shift.config() == twin.shift.config()
    assert [fresh.shift.rng.random() for _ in range(3)] == [twin.shift.rng.random() for _ in range(3)]
    assert fresh.shift.status_dict() == twin.shift.status_dict()


def test_a_paused_shift_resumes_where_it_stopped(tmp_path):
    # Fails if _paused_at isn't saved: the loaded shift would fire everything
    # that fell due while it was paused in one burst.
    twin, sim = running_shift(tmp_path, 200)
    twin.shift.pause()
    for _ in range(300):
        sim.tick()
    fresh = make_twin(tmp_path, "fresh")
    fresh.load_state(twin.save_state(str(tmp_path / "state.json")))
    assert fresh.shift.status == "PAUSED" and fresh.shift._paused_at == twin.shift._paused_at
    twin.shift.start()
    fresh.shift.start()
    assert fresh.shift._next_due == twin.shift._next_due


def test_a_save_without_a_shift_leaves_a_fresh_paused_one(tmp_path):
    classic = make_twin(tmp_path, "classic", layout="classic")
    path = classic.save_state(str(tmp_path / "classic.json"))
    with open(path, encoding="utf-8") as handle:
        v1 = json.load(handle)
    for key in ("layout", "stock", "equipment", "shift", "floor_seeded"):
        del v1[key]
    v1["version"] = 1  # a version 1 file: a classic save with no shift
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(v1, handle)
    classic.shift.orders.count("1,1")  # an order the save never had
    classic.shift.rng.random()
    classic.load_state(path)
    assert classic.shift.orders.orders == {} and classic.shift.status == "PAUSED"
    assert classic.shift.rng.random() == __import__("random").Random(classic.shift.seed).random()


def test_a_damaged_shift_save_is_refused_and_changes_nothing(tmp_path):
    twin, _ = running_shift(tmp_path, 200)
    path = tmp_path / "state.json"
    twin.save_state(str(path))
    with open(path, encoding="utf-8") as handle:
        good = json.load(handle)
    before = without_saved_at(twin.serialize())
    shift = good["shift"]
    for damaged, message in (({**shift, "status": "LUNCH"}, "Unknown shift status 'LUNCH'"),
                             ({**shift, "seed": "42"}, "seed must be a whole number"),
                             ({**shift, "pace": 0}, "pace must be a finite number greater than zero"),
                             ({**shift, "rates": {"meteors": 1.0}}, "Unknown rate 'meteors'"),
                             ({**shift, "next_due": {"trucks": 30.0}}, "needs a next due time for every stream"),
                             ({**shift, "rng": [3, [1, 2], None]}, "random number state is missing or damaged"),
                             ({**shift, "truck_count": -1}, "truck_count must be a whole number"),
                             ({**shift, "orders": {"orders": [{"order_id": "ORD-0001"}]}}, "missing \\['kind'\\]")):
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({**good, "shift": damaged}, handle)
        with pytest.raises(ValueError, match=message):
            twin.load_state(str(path))
        assert without_saved_at(twin.serialize()) == before


# --------------------------------------------------------------------------- #
# Determinism
# --------------------------------------------------------------------------- #
def test_a_twin_saved_mid_shift_makes_the_same_orders_and_jobs_as_the_original(tmp_path):
    # Fails if a save drops anything the shift's next ticks depend on: the RNG
    # state, a stream's next due time, the truck or face rotation, an order's
    # stages (status, task, retries) or lines (the tote it holds, the units
    # placed), or the id sequences new orders and tasks take.
    twin, _ = running_shift(tmp_path, 3000)  # 7.5 sim-minutes in: orders in flight, items on the line
    at_save = set(twin.shift.orders.orders)
    done = {key for key, order in twin.shift.orders.orders.items() if order.status == "DONE"}
    assert twin.shift.orders.counts()["CUSTOMER"]["IN_PROGRESS"]
    path = twin.save_state(str(tmp_path / "state.json"))
    loaded = make_twin(tmp_path, "loaded")
    loaded.load_state(path)
    # Both go on with a new Simulator: its retry and idle timers are the
    # loop's own, not the twin's state, so a save doesn't carry them.
    runs = []
    for floor in (twin, loaded):
        simulator = Simulator(floor)
        for _ in range(2000):
            simulator.tick()
        runs.append({
            "orders": floor.shift.orders.to_state(),
            "shift": {key: value for key, value in floor.shift.to_state().items() if key != "orders"},
            "jobs": [(t.id, t.type.value, t.status.value, t.robot_id, t.operator_id, t.box_id, t.destination,
                      t.params, t.action_index, t.error) for t in floor.tasks.tasks.values()],
            "robots": [(r.name, r.position, r.layer, r.battery, r.status.value, r.current_task, r.carrying_box)
                       for r in floor.robots.values()],
            "boxes": [(b.id, b.kind.value, b.position, b.status.value, b.slot, b.quantity)
                      for b in floor.boxes.values()],
            "stock": floor.stock.to_dict(),
            "line": floor.equipment.to_state(),
            "people": [(o.name, o.status.value, o.zone, o.transit_to) for o in floor.operators.values()],
        })
    original, restored = runs
    orders = original["orders"]["orders"]
    assert [o for o in orders if o["order_id"] not in at_save]  # the run did something worth comparing:
    assert [o for o in orders if o["status"] == "DONE" and o["order_id"] not in done]  # new orders, finished ones
    for key in original:
        assert restored[key] == original[key], key
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/bin/python -m pytest -o addopts="" -q backend/test_save_load_shift.py`
Expected: `8 failed` — `AttributeError: type object 'Order' has no attribute 'from_dict'`, `'OrderBook' object has no attribute 'to_state'`, `'ShiftEngine' object has no attribute 'to_state'`, `KeyError: 'shift'`, and the paused shift's `_paused_at` (`None == 30.0`).

- [ ] **Step 3: Orders and their stages, to a save and back**

**Replace in** `backend/operations/orders.py`:

```python
class OrderError(Exception):
    """A stage can't be built: the reason fails or retries it."""


@dataclass
class Stage:
```

with:

```python
class OrderError(Exception):
    """A stage can't be built: the reason fails or retries it."""


def _saved_fields(cls: Any, data: Dict[str, Any], required: List[str], what: str) -> None:
    """A saved order or stage names only fields `cls` has, and every required one."""
    known = list(cls.__dataclass_fields__)
    unknown = sorted(set(data) - set(known))
    if unknown:
        raise ValueError(f"Unknown {what} field(s) {unknown} (known: {known})")
    missing = [key for key in required if key not in data]
    if missing:
        raise ValueError(f"A saved {what} is missing {missing}")


@dataclass
class Stage:
```

**Replace in** `backend/operations/orders.py`:

```python
    line: Optional[int] = None        # which order line a customer stage serves

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Order:
```

with:

```python
    line: Optional[int] = None        # which order line a customer stage serves

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "Stage":
        """A stage from to_dict() (a save); an unknown field or status raises ValueError."""
        _saved_fields(Stage, data, ["name", "task_type"], "stage")
        stage = Stage(**{**data, "payload": dict(data.get("payload") or {}), "after": list(data.get("after") or [])})
        if stage.status not in (WAITING, ACTIVE, DONE, FAILED):
            raise ValueError(f"Stage {stage.name!r} has an unknown status {stage.status!r}")
        return stage


@dataclass
class Order:
```

**Replace in** `backend/operations/orders.py`:

```python
        data["stages"] = [stage.to_dict() for stage in self.stages]
        return data


class OrderBook:
```

with:

```python
        data["stages"] = [stage.to_dict() for stage in self.stages]
        return data

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "Order":
        """An order from to_dict() (a save), its stages with it. An unknown
        field, kind or status, or a stage that follows one the order doesn't
        have, raises ValueError."""
        _saved_fields(Order, data, ["order_id", "kind"], "order")
        order = Order(**{**data, "lines": [dict(line) for line in data.get("lines") or []],
                         "stages": [Stage.from_dict(stage) for stage in data.get("stages") or []],
                         "lost": list(data.get("lost") or [])})
        if order.kind not in ORDER_KINDS:
            raise ValueError(f"{order.order_id} has an unknown kind {order.kind!r} (known: {list(ORDER_KINDS)})")
        if order.status not in (OPEN, IN_PROGRESS, DONE, FAILED):
            raise ValueError(f"{order.order_id} has an unknown status {order.status!r}")
        for stage in order.stages:
            if any(not isinstance(index, int) or not 0 <= index < len(order.stages) for index in stage.after):
                raise ValueError(f"{order.order_id}: {stage.name} follows a stage the order doesn't have")
        return order


class OrderBook:
```

**Replace in** `backend/operations/orders.py`:

```python
    """Every order of one twin, advanced once a tick by the shift engine."""

    def __init__(self, twin: Any) -> None:
        self.twin = twin
        self.orders: Dict[str, Order] = {}
        self._seq = 0

    # ---- creating orders ------------------------------------------------- #
```

with:

```python
    """Every order of one twin, advanced once a tick by the shift engine."""

    def __init__(self, twin: Any) -> None:
        self.twin = twin
        self.orders: Dict[str, Order] = {}
        self._seq = 0

    # ---- save and load (ShiftEngine.to_state / load_state) --------------- #
    def to_state(self) -> Dict[str, Any]:
        """Every order in the order it was created (advance() goes through them
        in that order), and the id sequence."""
        return {"orders": [order.to_dict() for order in self.orders.values()], "seq": self._seq}

    def load_state(self, data: Dict[str, Any]) -> None:
        """Replace every order with a to_state() snapshot. It is all checked
        first: a damaged order, an id twice or a sequence behind its orders
        raises ValueError and changes nothing."""
        orders: Dict[str, Order] = {}
        for raw in data.get("orders", []):
            order = Order.from_dict(raw)
            if order.order_id in orders:
                raise ValueError(f"The save has {order.order_id} twice")
            orders[order.order_id] = order
        seq = data.get("seq", 0)
        numbers = [int(key.rsplit("-", 1)[-1]) for key in orders if key.rsplit("-", 1)[-1].isdigit()]
        if isinstance(seq, bool) or not isinstance(seq, int) or seq < max(numbers, default=0):
            raise ValueError(f"The order sequence {seq!r} must be a whole number, at least {max(numbers, default=0)}")
        self.orders, self._seq = orders, seq

    # ---- creating orders ------------------------------------------------- #
```


- [ ] **Step 4: The shift engine, restored into itself**

**Replace in** `backend/operations/shift.py`:

```python
    def _on_event(self, event: Dict[str, Any]) -> None:
        self.orders.on_event(event)

    # ---- controls (plan 1c's /api/shift calls these) --------------------- #
```

with:

```python
    def _on_event(self, event: Dict[str, Any]) -> None:
        self.orders.on_event(event)

    # ---- save and load (DigitalTwin.save_state / load_state, spec §4.3) --- #
    def to_state(self) -> Dict[str, Any]:
        """Everything a save needs to carry the shift on exactly: its status,
        config and RNG state, when each stream is next due (and when it was
        paused), the truck and rack-face rotations, who is stepping aside
        until when, the counters, and every order."""
        version, internal, gauss = self.rng.getstate()
        return {
            "status": self.status, "seed": self.seed, "pace": self.pace, "rates": dict(self.rates),
            "rng": [version, list(internal), gauss],
            "next_due": dict(self._next_due), "paused_at": self._paused_at,
            "truck_count": self._truck_count, "face_index": self._face_index,
            "yield_until": dict(self.yield_until), "counters": dict(self.counters),
            "orders": self.orders.to_state(),
        }

    def load_state(self, data: Dict[str, Any]) -> None:
        """Restore a to_state() snapshot into this engine — the one twin.events
        calls (it subscribed once, at construction), so a load never adds a
        second listener. It is all checked first: a damaged snapshot raises
        ValueError and leaves the engine as it was."""
        status = data.get("status")
        if status not in (self.RUNNING, self.PAUSED):
            raise ValueError(f"Unknown shift status {status!r} (known: {[self.RUNNING, self.PAUSED]})")
        seed = data.get("seed")
        if isinstance(seed, bool) or not isinstance(seed, int):
            raise ValueError(f"The shift seed must be a whole number, not {seed!r}")
        pace = self._saved_number(data.get("pace"), "pace")
        if pace <= 0:
            raise ValueError("pace must be a finite number greater than zero")
        rates = dict(DEFAULT_RATES)
        for stream, rate in (data.get("rates") or {}).items():
            if stream not in DEFAULT_RATES:
                raise ValueError(f"Unknown rate {stream!r} (known: {sorted(DEFAULT_RATES)})")
            rates[stream] = self._saved_number(rate, stream)
        next_due = {}
        for stream, due in (data.get("next_due") or {}).items():
            if stream not in DEFAULT_RATES:
                raise ValueError(f"Unknown stream {stream!r} in the shift's next due times")
            next_due[stream] = self._saved_number(due, stream)
        if status == self.RUNNING and set(next_due) != set(DEFAULT_RATES):
            raise ValueError("A running shift needs a next due time for every stream")
        paused_at = data.get("paused_at")
        paused_at = None if paused_at is None else self._saved_number(paused_at, "paused_at")
        rng = random.Random()
        try:
            version, internal, gauss = data["rng"]
            rng.setstate((version, tuple(internal), gauss))
        except (KeyError, TypeError, ValueError):
            raise ValueError("The shift's random number state is missing or damaged") from None
        counts = {key: data.get(key, 0) for key in ("truck_count", "face_index")}
        counters = {**{stream: 0 for stream in DEFAULT_RATES}, "skipped_orders": 0, **(data.get("counters") or {})}
        for key, value in [*counts.items(), *counters.items()]:
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"The shift's {key} must be a whole number, 0 or more, not {value!r}")
        yield_until = {str(key): self._saved_number(until, f"{key}'s step-aside")
                       for key, until in (data.get("yield_until") or {}).items()}
        orders = OrderBook(self.twin)
        orders.load_state(data.get("orders") or {})
        self.status, self.seed, self.pace, self.rates, self.rng = status, seed, pace, rates, rng
        self._next_due, self._paused_at = next_due, paused_at
        self._truck_count, self._face_index = counts["truck_count"], counts["face_index"]
        self.yield_until, self.counters, self.orders = yield_until, counters, orders

    @staticmethod
    def _saved_number(value: Any, what: str) -> float:
        """A number from a saved shift: finite, and never negative."""
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise ValueError(f"The shift's {what} must be a finite number, 0 or more, not {value!r}")
        return float(value)

    # ---- controls (plan 1c's /api/shift calls these) --------------------- #
```


- [ ] **Step 5: The shift in the save**

**Replace in** `backend/digital_twin.py`:

```python
    #: The save format save_state writes (spec §4.3). Version 1 — no layout,
    #: stock or equipment — is a classic save, and still loads on classic.
    STATE_VERSION = 2
```

with:

```python
    #: The save format save_state writes (spec §4.3). Version 1 — no layout,
    #: stock, equipment or shift — is a classic save, and still loads on classic.
    STATE_VERSION = 2
```

**Replace in** `backend/digital_twin.py`:

```python
                "equipment": self.equipment.to_state() if self.equipment is not None else None,
                "floor_seeded": self.floor_seeded,
```

with:

```python
                "equipment": self.equipment.to_state() if self.equipment is not None else None,
                "shift": self.shift.to_state(),
                "floor_seeded": self.floor_seeded,
```

**Replace in** `backend/digital_twin.py`:

```python
            unknown = sorted(set(held) - {box.id for box in boxes})
            if unknown:
                raise ValueError(f"The save's stock or conveyor holds boxes it has no record of: {unknown}")

            self.robots.clear()
```

with:

```python
            unknown = sorted(set(held) - {box.id for box in boxes})
            if unknown:
                raise ValueError(f"The save's stock or conveyor holds boxes it has no record of: {unknown}")
            # Last of the checks, as it is all-or-nothing itself: into the twin's
            # own engine, which listens to twin.events. A version 1 save has no
            # shift, so the floor gets a fresh, paused one — none of the old orders.
            if payload.get("shift") is not None:
                self.shift.load_state(payload["shift"])
            else:
                self.shift.reset()

            self.robots.clear()
```


- [ ] **Step 6: Run the new tests**

Run: `.venv/bin/python -m pytest -o addopts="" -q backend/test_save_load_shift.py backend/test_save_load_v2.py`
Expected: `20 passed`.

- [ ] **Step 7: Run the full suite**

Run: `.venv/bin/python -m pytest -o addopts="" -q`
Expected: `2 failed, 820 passed` (Task 3's count plus this task's 8 tests; the two failures are the allowed ones).

- [ ] **Step 8: Commit**

```bash
git add backend/operations/orders.py backend/operations/shift.py backend/digital_twin.py backend/test_save_load_shift.py
git commit -m "feat: save and load v2 — the shift engine and its orders

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: The operations API and the live snapshot

The floor's operations go on the wire (spec §11.5, §14). A new `backend/operations_api.py` registers the shift controls (`GET /api/shift`, `POST /api/shift/start`, `/pause` and `/config`), the orders (`GET /api/orders?status=&kind=&limit=` and `/api/orders/<order_id>`), the stock ledger (`GET /api/stock?sku=`), the people (`GET /api/people`), the conveyor and sorter (`GET /api/equipment`) and faults on demand (`POST /api/faults/<kind>`, body `count`, default 1). Every handler holds `twin.lock` (Global Constraints). Errors map to 404 for an unknown order id or fault kind (the kind names the resource in the path), 409 on the classic floor, which has no shift engine and no conveyor, and 400 for bad input. A shift control publishes the new state on the broadcaster, so the dashboard sees it at once. `ShiftEngine.panel()` is `status_dict()` plus the orders in flight, the backlog, the latest 20 failed orders and the latest 20 exceptions: escalated safety waits, failed orders, and stock variances left for review. The engine keeps these from its own event subscription, as they happen, because the 5 000-event buffer forgets them on a long shift. The snapshot gains what the dashboard (Tasks 9 and 10) draws from (§12): a `cell_types` table built from `layouts.base.CELL_TYPE_STYLES` (classic's rows come first, with the labels the classic legend has today, so it doesn't change), the equipment with its item and jammed cells, the shift panel, and the no-fly cells. Three plan 1b minors close here. `OrderBook.list` sorts by order number, not by the id's text (T12). `ShiftEngine.configure` checks every value before it changes any, and turns a number too big for a float into a `ValueError` (T12). `FaultInjector.arm` refuses a count that isn't a whole number of 1 or more (T1).

**Files:**
- Create: `backend/operations_api.py`
- Modify: `backend/app.py` (one import, and one registration line after `register_inventory_routes(app, twin, ApiError)`)
- Modify: `backend/digital_twin.py` (`snapshot`)
- Modify: `backend/layouts/base.py` (`CELL_TYPE_STYLES`, `cell_type_table`, `Layout.cell_type_table`)
- Modify: `backend/warehouse.py` (`cell_type_table`, `no_fly_cells`, right after `to_dict`)
- Modify: `backend/equipment.py` (`Equipment.view`, right after `to_dict`)
- Modify: `backend/faults.py` (`fault_kind`; `arm`'s count check)
- Modify: `backend/operations/shift.py` (`panel`, the exceptions it keeps, `configure`)
- Modify: `backend/operations/orders.py` (`order_number`, `OrderBook.get`, the `list` sort)
- Test: `backend/test_operations_api.py` (create)

**Interfaces:**
- Consumes (plan 1b): `ShiftEngine.status_dict/start/pause/configure` (`start` raises `ValueError` on classic), `OrderBook.list/counts/count/returned`, `Order.to_dict`, `StockLedger.locations/skus`, `StockLocation.discrepancy`, `Equipment.to_dict/handoffs/conveyor/jam/place`, `FaultInjector.arm`, `faults.FAULT_RISKS`, `Operator.to_dict/in_transit`, `Broadcaster.publish`, `app.ApiError`. It uses nothing from Tasks 1–4. The tests build the app with `create_app(twin=..., autostart=False, run_thread=False)` on a `DigitalTwin(layout=...)` they populate themselves.
- Produces:
  - `backend.operations_api.register_operations_routes(app, twin, broadcaster, api_error) -> None`, with these routes. Every response is JSON with `"ok": true`, and every error is `{"ok": false, "error", "field"}`.
    - `GET /api/shift` → `{"shift": panel}`. `POST /api/shift/start` and `/pause` → `{"shift": panel}`, and they publish `"state"`. `POST /api/shift/config` takes any of `pace`, `seed` and `rates`; any other key, or an empty body, is a 400. It answers `{"shift": panel}` and publishes `"state"`. All four answer 409 on classic.
    - `GET /api/orders` → `{"orders": [Order.to_dict()], "counts": OrderBook.counts()}`, newest first. `status` is one of OPEN, IN_PROGRESS, DONE or FAILED, and `kind` is one of `ORDER_KINDS`; both are case-insensitive. `limit` is a whole number of at least 1 (default 100). Anything else is a 400. `GET /api/orders/<order_id>` → `{"order": Order.to_dict()}`, or 404 `"Order '<id>' does not exist"`.
    - `GET /api/stock` → `{"sku", "locations": [StockLocation.to_dict() + "discrepancy", "kind", "level"], "totals": {"recorded_qty", "true_qty"}, "skus"}`.
    - `GET /api/people` → `{"people": [Operator.to_dict() + "in_transit"]}`.
    - `GET /api/equipment` → `{"equipment": Equipment.view(), "handoffs": [Handoff.to_dict()] (the latest 20, newest first)}`, or 409 on classic.
    - `POST /api/faults/<kind>` → `{"kind", "armed"}`. The kind may use any case, `-` or `_`. An unknown kind is a 404, and a bad count is a 400.
  - `ShiftEngine.panel() -> Dict`: `status_dict()` plus `in_flight: int` (orders IN_PROGRESS), `backlog: int` (orders OPEN), `failed_orders: [{"order_id", "kind", "reason"}]` and `exceptions: [{"type", "message", "tick"}]`. Each list holds at most `PANEL_LIST_LIMIT` (20) entries, newest first. `ShiftEngine.exceptions: Deque[Dict]` is cleared by `reset()`. `shift.EXCEPTION_EVENTS` lists the event types it keeps; a `STOCK_VARIANCE_DETECTED` with `auto_reconciled` true is left out.
  - `backend.operations.orders.order_number(order_id) -> int`, and `OrderBook.get(order_id) -> Dict` (`KeyError` if unknown).
  - `backend.faults.fault_kind(kind) -> Optional[str]`. `FaultInjector.arm(kind, count)` raises `ValueError("count must be a whole number, at least 1")` for a bool, a non-int or a count below 1.
  - `backend.layouts.base.CELL_TYPE_STYLES: Tuple[Tuple[CellType, str, str, bool], ...]` and `cell_type_table(grid) -> List[Dict]`, plus `Layout.cell_type_table()` and `Warehouse.cell_type_table()`. Each returns `{"type", "label", "role", "legend"}` for the types in the grid, in legend order. `Warehouse.no_fly_cells() -> List[Cell]` returns them row by row.
  - `Equipment.view() -> Dict`: `to_dict()` plus `item_cells: [{"x", "y", "box_id", "kind", "order_id"}]` and `jammed_cells: [{"x", "y"}]`, both upstream first.
  - `snapshot()` gains `equipment` (`Equipment.view()`, or None on classic) and `shift` (`panel()`, or None on classic) on every snapshot. With `include_layout` it also gains `cell_types` (`Warehouse.cell_type_table()`) and `no_fly_cells` (`[{"x", "y"}]`, empty on classic). Existing keys keep their shape.

- [ ] **Step 1: Write the failing tests**

**Create** `backend/test_operations_api.py`:

```python
"""The operations API and the live snapshot (multi-embodiment spec §11.5, §12,
§14): the shift's controls and panel, its orders, the stock ledger, the
people, the conveyor and on-demand faults over HTTP, and the snapshot's
cell-type table, equipment, shift panel and no-fly cells."""
import json
import random
import threading

import pytest

from backend import people
from backend.app import create_app
from backend.digital_twin import DigitalTwin
from backend.layouts import build_layout
from backend.layouts.base import CELL_TYPE_STYLES
from backend.models import CellType, EventType, SimulationStatus
from backend.operator import Operator
from backend.simulator import Simulator

#: The classic dashboard's legend, in its order, then the two unlisted fills —
#: the table must reproduce it exactly so the classic floor draws as before.
CLASSIC_TABLE = [
    {"type": "SHELF", "label": "Racking", "role": "shelf", "legend": True},
    {"type": "STORAGE", "label": "Pick face", "role": "storage", "legend": True},
    {"type": "CHARGING", "label": "Charging", "role": "charging", "legend": True},
    {"type": "LOADING", "label": "Loading", "role": "loading", "legend": True},
    {"type": "UNLOADING", "label": "Unloading", "role": "unloading", "legend": True},
    {"type": "PACKING", "label": "Packing", "role": "packing", "legend": True},
    {"type": "PARKING", "label": "Parking", "role": "parking", "legend": True},
    {"type": "RESTRICTED", "label": "Restricted", "role": "restricted", "legend": True},
    {"type": "EMPTY", "label": "Floor", "role": "floor", "legend": False},
    {"type": "WALL", "label": "Wall", "role": "wall", "legend": False},
]


def make_twin(path, layout="distribution_center"):
    return DigitalTwin(log_dir=str(path / "logs"), data_dir=str(path / "data"), persist_logs=False,
                       demo=True, demo_tasks=False, layout=layout)


def client_for(twin):
    app, _twin, _sim, _ci = create_app(twin=twin, autostart=False, run_thread=False)
    app.config["TESTING"] = True
    return app, app.test_client()


@pytest.fixture
def twin(tmp_path):
    return make_twin(tmp_path)


@pytest.fixture
def client(twin):
    return client_for(twin)[1]


def frames(stream, name):
    """The `name` frames waiting on a broadcaster client queue, decoded."""
    out = []
    while not stream.empty():
        frame = stream.get_nowait()
        if frame.startswith(f"event: {name}\n"):
            out.append(json.loads(frame.split("data: ", 1)[1]))
    return out


# ---- the shift ------------------------------------------------------------ #
def test_the_shift_is_started_and_paused_over_http(twin):
    app, client = client_for(twin)
    response = client.get("/api/shift")
    assert response.status_code == 200
    shift = response.get_json()["shift"]
    assert (shift["status"], shift["clock"], shift["in_flight"], shift["backlog"]) == ("PAUSED", "06:00", 0, 0)
    assert {"config", "counters", "orders", "throughput_per_hour", "failed_orders", "exceptions"} <= set(shift)
    stream = app.broadcaster.register()
    started = client.post("/api/shift/start")
    assert started.status_code == 200 and started.get_json()["shift"]["status"] == "RUNNING"
    assert twin.shift.status == "RUNNING"
    # The dashboard hears about it at once (no publish: no state frame).
    assert [state["shift"]["status"] for state in frames(stream, "state")] == ["RUNNING"]
    paused = client.post("/api/shift/pause")
    assert paused.status_code == 200 and paused.get_json()["shift"]["status"] == "PAUSED"
    assert twin.shift.status == "PAUSED"
    assert client.get("/api/shift").get_json()["shift"] == twin.snapshot()["shift"]


def test_the_shift_is_configured_and_a_bad_value_changes_nothing(twin, client):
    response = client.post("/api/shift/config", json={"pace": 2, "seed": 7, "rates": {"patrols": 0}})
    assert response.status_code == 200
    config = response.get_json()["shift"]["config"]
    assert (config["pace"], config["seed"], config["rates"]["patrols"]) == (2.0, 7, 0.0)
    assert twin.shift.rng.random() == random.Random(7).random()      # a new seed restarts the RNG
    before = twin.shift.config()
    bad_bodies = [
        {"pace": 0},
        {"pace": 3, "rates": {"aliens": 1}},   # fails if configure applies the pace before checking the rates
        {"pace": 10 ** 400},                   # fails (a 500) if float()'s OverflowError escapes
        {"rates": {"trucks": "lots"}},
        {"rates": [1, 2]},
        {"seed": "seven"},
        {"speed": 3},                          # not something the shift has
        {},
    ]
    for body in bad_bodies:
        response = client.post("/api/shift/config", json=body)
        assert response.status_code == 400, body
        assert response.get_json()["ok"] is False and response.get_json()["error"]
    assert twin.shift.config() == before
    with pytest.raises(ValueError, match="trucks"):
        twin.shift.configure(rates={"trucks": 10 ** 400})
    assert twin.shift.config() == before


# ---- orders --------------------------------------------------------------- #
def test_orders_are_listed_newest_first_by_their_number(twin, client):
    book = twin.shift.orders
    book._seq = 9998                    # a long shift's id sequence: the next ids are ORD-9999, ORD-10000, ...
    book.count("8,2")
    book.count("9,2")
    book.returned("box_1")
    body = client.get("/api/orders").get_json()
    # Fails if list() sorts by the id string, which puts ORD-9999 first.
    assert [order["order_id"] for order in body["orders"]] == ["ORD-10001", "ORD-10000", "ORD-9999"]
    assert body["counts"]["COUNT"]["OPEN"] == 2 and body["counts"]["RETURN"]["OPEN"] == 1
    counts = client.get("/api/orders?kind=count&status=open&limit=1").get_json()["orders"]
    assert [order["order_id"] for order in counts] == ["ORD-10000"]
    one = client.get("/api/orders/ORD-9999")
    assert one.status_code == 200
    assert [stage["task_type"] for stage in one.get_json()["order"]["stages"]] == ["CYCLE_COUNT"]
    missing = client.get("/api/orders/ORD-0001")
    assert missing.status_code == 404 and missing.get_json()["error"] == "Order 'ORD-0001' does not exist"
    for query in ("status=LOST", "kind=PARCEL", "limit=0", "limit=many"):
        assert client.get(f"/api/orders?{query}").status_code == 400, query
    with pytest.raises(KeyError):
        book.get("ORD-0001")


# ---- stock and people ----------------------------------------------------- #
def test_stock_is_listed_by_sku_with_recorded_and_true_quantities(twin, client):
    twin.add_box(name="PAL-1", kind="PALLET", sku="SKU-001", quantity=40, weight=500.0, slot="PR-10-05-0")
    twin.add_box(name="TOTE-1", kind="TOTE", sku="SKU-002", quantity=12, weight=9.0, slot="TS-10-12-1")
    twin.add_box(name="TOTE-2", kind="TOTE", sku="SKU-002", quantity=8, weight=7.0, slot="TS-11-12-0")
    twin.stock.adjust_true("TS-10-12-1", -2)                           # two units went missing
    every = client.get("/api/stock").get_json()
    assert every["skus"] == ["SKU-001", "SKU-002"]
    assert [row["slot_id"] for row in every["locations"]] == ["PR-10-05-0", "TS-10-12-1", "TS-11-12-0"]
    totes = client.get("/api/stock?sku=SKU-002").get_json()
    assert [row["slot_id"] for row in totes["locations"]] == ["TS-10-12-1", "TS-11-12-0"]
    first = totes["locations"][0]
    assert (first["kind"], first["level"], first["recorded_qty"], first["true_qty"], first["discrepancy"]) == \
        ("TOTE", 1, 12, 10, -2)
    assert totes["totals"] == {"recorded_qty": 20, "true_qty": 18}
    assert client.get("/api/stock?sku=SKU-999").get_json()["locations"] == []


def test_people_are_listed_in_their_zone_or_on_their_walk(twin, client):
    sam = twin.add_operator(name="Sam", worker_id="E-10001")
    lee = twin.add_operator(name="Lee", worker_id="E-10002")
    people.place(twin, sam, "pick_station_2")
    people.place(twin, lee, "intake_staging")
    people.start_transit(twin, lee, "workshop")
    listed = {person["name"]: person for person in client.get("/api/people").get_json()["people"]}
    assert (listed["Sam"]["zone"], listed["Sam"]["in_transit"]) == ("pick_station_2", False)
    assert (listed["Lee"]["transit_to"], listed["Lee"]["in_transit"]) == ("workshop", True)
    assert listed["Sam"]["worker_id"] == "E-10001"


# ---- equipment and faults ------------------------------------------------- #
def test_the_equipment_shows_the_items_on_the_line_and_its_jams(twin, client):
    riding = twin.add_box(name="ITEM-1", kind="ITEM", sku="SKU-002", quantity=1, weight=0.5,
                          position=(20, 15), order_id="ORD-7")
    placed = twin.add_box(name="ITEM-2", kind="ITEM", sku="SKU-002", quantity=1, weight=0.5, position=(19, 14))
    twin.equipment.place(placed, (19, 15), giver="E-10001")             # a person puts it on the infeed
    twin.equipment.jam((22, 15))
    body = client.get("/api/equipment").get_json()
    view = body["equipment"]
    assert view["item_cells"] == [
        {"x": 19, "y": 15, "box_id": placed.id, "kind": "ITEM", "order_id": None},
        {"x": 20, "y": 15, "box_id": riding.id, "kind": "ITEM", "order_id": "ORD-7"},
    ]
    assert view["jammed_cells"] == [{"x": 22, "y": 15}]
    assert view["conveyor"]["jams"] == [{"cell": {"x": 22, "y": 15}, "since_tick": 0}]   # to_dict() as it was
    assert view["sorter"]["lanes"] == ["dock_4", "dock_5"]
    assert [(h["box_id"], h["from"], h["to"]) for h in body["handoffs"]] == [(placed.id, "E-10001", "conveyor")]
    assert twin.snapshot()["equipment"] == view


def test_a_fault_is_armed_on_demand(twin, client):
    response = client.post("/api/faults/conveyor-jam", json={"count": 2})
    assert response.status_code == 200
    assert response.get_json() == {"ok": True, "kind": "conveyor_jam", "armed": 2}
    assert client.post("/api/faults/mis_sort").get_json()["armed"] == 1        # one by default
    unknown = client.post("/api/faults/gremlins")
    assert unknown.status_code == 404 and "gremlins" in unknown.get_json()["error"]
    for count in (0, -1, 1.5, "2", True):
        assert client.post("/api/faults/mis_sort", json={"count": count}).status_code == 400, count
    assert twin.faults.armed() == {"conveyor_jam": 2, "mis_sort": 1}
    # Fails if arm() still runs int(count): 2.5 would arm two, None raise TypeError.
    for count in (2.5, None, "3"):
        with pytest.raises(ValueError, match="whole number"):
            twin.faults.arm("mis_sort", count)
    assert twin.faults.armed() == {"conveyor_jam": 2, "mis_sort": 1}


# ---- the classic floor ----------------------------------------------------- #
def test_the_classic_floor_has_no_shift_and_no_equipment(tmp_path):
    twin = make_twin(tmp_path, layout="classic")
    _app, client = client_for(twin)
    refused = [client.get("/api/shift"), client.post("/api/shift/start"), client.post("/api/shift/pause"),
               client.post("/api/shift/config", json={"pace": 2}), client.get("/api/equipment")]
    # /api/shift/start fails as a 400 if the route lets ShiftEngine.start's ValueError through.
    assert [response.status_code for response in refused] == [409] * 5
    assert refused[0].get_json()["error"] == "The classic floor has no shift engine"
    assert twin.shift.status == "PAUSED" and twin.shift.pace == 1.0
    assert client.get("/api/orders").get_json()["orders"] == []
    assert client.get("/api/stock").get_json()["locations"] == []
    assert {person["name"] for person in client.get("/api/people").get_json()["people"]} == {"Sam", "Lee"}


# ---- the shift panel -------------------------------------------------------- #
def test_the_panel_lists_failed_orders_and_exceptions_newest_first(twin):
    sim = Simulator(twin)
    twin.simulation_status = SimulationStatus.RUNNING
    order = twin.shift.orders.count("0,0")              # no rack there: the count is refused, retried, refused
    for _ in range(100):                                # past ORDER_RETRY_DELAY_S (10 s at 0.15 s a tick)
        sim.tick()
        if order.status == "FAILED":
            break
    assert (order.status, order.failure_reason) == ("FAILED", "(0,0) is not a pallet rack face")
    failed_at = twin.tick_count
    panel = twin.shift.panel()
    assert panel["failed_orders"] == [{"order_id": order.order_id, "kind": "COUNT", "reason": order.failure_reason}]
    assert panel["exceptions"] == [{"type": "ORDER_FAILED", "tick": failed_at,
                                    "message": f"{order.order_id} failed: {order.failure_reason}"}]
    # The simulator emits these; the panel keeps them from its own subscription.
    twin.events.emit(EventType.STOCK_VARIANCE_DETECTED, "PR-10-05-0: counted 38, recorded 40 — reconciled",
                     data={"auto_reconciled": True})
    twin.events.emit(EventType.STOCK_VARIANCE_DETECTED, "PR-10-05-1: counted 30, recorded 40 — left for review",
                     data={"auto_reconciled": False})
    panel = twin.shift.panel()
    assert [e["message"] for e in panel["exceptions"]][:2] == [
        "PR-10-05-1: counted 30, recorded 40 — left for review", f"{order.order_id} failed: {order.failure_reason}"]
    for number in range(25):
        twin.events.emit(EventType.SAFETY_WAIT_ESCALATED, f"wait {number} escalated")
    exceptions = twin.shift.panel()["exceptions"]
    assert len(exceptions) == 20 and exceptions[0] == {"type": "SAFETY_WAIT_ESCALATED", "message": "wait 24 escalated",
                                                       "tick": twin.tick_count}
    twin.reset(demo_tasks=False)
    assert twin.shift.panel()["exceptions"] == [] and twin.shift.panel()["failed_orders"] == []


# ---- the snapshot ----------------------------------------------------------- #
def test_every_cell_type_is_styled_once():
    styled = [entry[0] for entry in CELL_TYPE_STYLES]
    assert len(styled) == len(set(styled))
    assert set(styled) == set(CellType) - {CellType.ROBOT, CellType.BOX}   # those two are never drawn as cells
    assert styled[:8] == [CellType.SHELF, CellType.STORAGE, CellType.CHARGING, CellType.LOADING,
                          CellType.UNLOADING, CellType.PACKING, CellType.PARKING, CellType.RESTRICTED]


def test_the_classic_snapshot_draws_its_floor_as_before(tmp_path):
    twin = make_twin(tmp_path, layout="classic")
    snapshot = twin.snapshot(include_layout=True)
    assert snapshot["cell_types"] == CLASSIC_TABLE
    assert twin.warehouse.cell_type_table() == build_layout("classic").cell_type_table() == CLASSIC_TABLE
    assert snapshot["no_fly_cells"] == [] and snapshot["equipment"] is None and snapshot["shift"] is None
    plain = twin.snapshot()
    assert "cell_types" not in plain and "no_fly_cells" not in plain     # layout data comes with the layout
    assert {"environment", "robots", "boxes", "tasks", "statistics", "options"} <= set(plain)


def test_the_new_floor_snapshot_carries_its_cell_types_no_fly_cells_and_operations(twin):
    snapshot = twin.snapshot(include_layout=True)
    assert [row["type"] for row in snapshot["cell_types"]] == [
        "CHARGING", "PARKING", "RESTRICTED", "DOCK", "DOCK_DOOR", "STAGING", "PALLET_RACK", "TOTE_SHELF",
        "WALKWAY", "STATION", "CONVEYOR", "SORTER", "WORKSHOP", "DRONE_PAD", "EMPTY", "WALL"]
    rows = {row["type"]: row for row in snapshot["cell_types"]}
    assert rows["DOCK_DOOR"] == {"type": "DOCK_DOOR", "label": "Dock door", "role": "dockDoor", "legend": True}
    assert rows["DRONE_PAD"]["role"] == "dronePad" and rows["WALL"]["legend"] is False
    no_fly = [(cell["x"], cell["y"]) for cell in snapshot["no_fly_cells"]]
    expected = {cell for zone in twin.warehouse.zones.values() if zone.attributes.get("no_fly") for cell in zone.cells}
    assert set(no_fly) == expected and len(no_fly) == len(expected)
    assert no_fly == sorted(no_fly, key=lambda cell: (cell[1], cell[0]))   # row by row, like the cells
    plain = twin.snapshot()
    assert plain["equipment"]["conveyor"]["cells"][0] == {"x": 19, "y": 15}
    assert plain["equipment"]["item_cells"] == [] and plain["equipment"]["jammed_cells"] == []
    assert plain["shift"] == twin.shift.panel() and plain["shift"]["status"] == "PAUSED"


# ---- locking ----------------------------------------------------------------- #
class WatchedLock:
    """Stands in for twin.lock: a re-entrant lock that knows when it is held."""

    def __init__(self):
        self._lock = threading.RLock()
        self.depth = 0

    def __enter__(self):
        self._lock.acquire()
        self.depth += 1
        return self

    def __exit__(self, *exc):
        self.depth -= 1
        self._lock.release()
        return False


ROUTES = [
    ("get", "/api/shift", None, lambda twin: (twin.shift, "panel")),
    ("post", "/api/shift/start", None, lambda twin: (twin.shift, "start")),
    ("post", "/api/shift/pause", None, lambda twin: (twin.shift, "pause")),
    ("post", "/api/shift/config", {"pace": 2}, lambda twin: (twin.shift, "configure")),
    ("get", "/api/orders", None, lambda twin: (twin.shift.orders, "list")),
    ("get", "/api/orders/ORD-0001", None, lambda twin: (twin.shift.orders, "get")),
    ("get", "/api/stock", None, lambda twin: (twin.stock, "locations")),
    ("get", "/api/people", None, lambda twin: (Operator, "to_dict")),
    ("get", "/api/equipment", None, lambda twin: (twin.equipment, "view")),
    ("post", "/api/faults/mis_sort", None, lambda twin: (twin.faults, "arm")),
]


@pytest.mark.parametrize("method,path,body,target", ROUTES, ids=[f"{m} {p}" for m, p, _b, _t in ROUTES])
def test_every_operations_route_holds_the_twin_lock(twin, client, monkeypatch, method, path, body, target):
    twin.shift.orders.count("8,2")
    twin.add_operator(name="Sam", worker_id="E-10001")
    lock = WatchedLock()
    monkeypatch.setattr(twin, "lock", lock)
    owner, name = target(twin)
    real = getattr(owner, name)
    held = []

    def watched(*args, **kwargs):
        held.append(lock.depth > 0)
        return real(*args, **kwargs)

    monkeypatch.setattr(owner, name, watched)
    response = getattr(client, method)(path, json=body)
    assert response.status_code == 200, response.get_json()
    assert held and all(held), f"{path} called {name} without twin.lock"


def test_a_load_clears_the_panels_exceptions_and_any_armed_fault(twin, tmp_path):
    # Plan ruling 19: neither belongs to the floor being loaded. Without the
    # clears, the escalation and the armed mis-sort survive the load.
    path = str(tmp_path / "save.json")
    twin.save_state(path)
    twin.faults.arm("mis_sort", 2)
    twin.events.emit(EventType.SAFETY_WAIT_ESCALATED, "PF1200-205 has waited 120 s (PERSON_IN_AISLE) — escalated")
    assert twin.shift.panel()["exceptions"]
    twin.load_state(path)
    assert twin.shift.panel()["exceptions"] == []
    assert twin.faults.armed() == {}
```


- [ ] **Step 2: Run them to see them fail**

Run: `.venv/bin/python -m pytest -o addopts="" -q backend/test_operations_api.py`
Expected: `1 error` during collection — `ImportError: cannot import name 'CELL_TYPE_STYLES' from 'backend.layouts.base'`.

- [ ] **Step 3: The cell-type table**

Classic's eight legend rows come first, in today's `LEGEND` order and labels; then the new floor's types; then the floor and wall fills, which are not in the legend. A floor's table lists only the types its grid uses.

**Replace in** `backend/layouts/base.py`:

```python
NARROW = "NARROW"
```

with:

```python
NARROW = "NARROW"

#: How the dashboard draws each cell type (spec §12): (type, label, colour
#: role, shown in the legend), in legend order. Classic's types come first, in
#: the order and with the labels its legend has always had, so the classic
#: floor draws exactly as before; the dashboard maps a colour role to a colour.
#: The floor (EMPTY) and walls are drawn but not listed in the legend.
CELL_TYPE_STYLES: Tuple[Tuple[CellType, str, str, bool], ...] = (
    (CellType.SHELF, "Racking", "shelf", True),
    (CellType.STORAGE, "Pick face", "storage", True),
    (CellType.CHARGING, "Charging", "charging", True),
    (CellType.LOADING, "Loading", "loading", True),
    (CellType.UNLOADING, "Unloading", "unloading", True),
    (CellType.PACKING, "Packing", "packing", True),
    (CellType.PARKING, "Parking", "parking", True),
    (CellType.RESTRICTED, "Restricted", "restricted", True),
    (CellType.DOCK, "Dock", "dock", True),
    (CellType.DOCK_DOOR, "Dock door", "dockDoor", True),
    (CellType.STAGING, "Staging", "staging", True),
    (CellType.PALLET_RACK, "Pallet rack", "palletRack", True),
    (CellType.TOTE_SHELF, "Tote shelf", "toteShelf", True),
    (CellType.WALKWAY, "Walkway", "walkway", True),
    (CellType.STATION, "Station", "station", True),
    (CellType.CONVEYOR, "Conveyor", "conveyor", True),
    (CellType.SORTER, "Sorter", "sorter", True),
    (CellType.WORKSHOP, "Workshop", "workshop", True),
    (CellType.DRONE_PAD, "Drone pad", "dronePad", True),
    (CellType.EMPTY, "Floor", "floor", False),
    (CellType.WALL, "Wall", "wall", False),
)


def cell_type_table(grid: Sequence[Sequence[CellType]]) -> List[Dict[str, Any]]:
    """The styles of the cell types `grid` uses, in legend order: one
    {"type", "label", "role", "legend"} row per type (the snapshot's
    `cell_types`)."""
    present = {cell_type for row in grid for cell_type in row}
    return [{"type": cell_type.value, "label": label, "role": role, "legend": legend}
            for cell_type, label, role, legend in CELL_TYPE_STYLES if cell_type in present]
```

**Replace in** `backend/layouts/base.py`:

```python

    def _fill(self, cells: Iterable[Cell], cell_type: CellType) -> None:
```

with:

```python

    def cell_type_table(self) -> List[Dict[str, Any]]:
        """How the dashboard draws the cell types this floor uses (CELL_TYPE_STYLES)."""
        return cell_type_table(self.grid)

    def _fill(self, cells: Iterable[Cell], cell_type: CellType) -> None:
```

**Replace in** `backend/warehouse.py`:

```python
from .layouts.base import NARROW, WIDE, Slot, Zone
```

with:

```python
from .layouts.base import NARROW, WIDE, Slot, Zone, cell_type_table
```

**Replace in** `backend/warehouse.py`:

```python

    #: Zone types that are never a task's source or destination: places robots
```

with:

```python

    def cell_type_table(self) -> List[Dict[str, Any]]:
        """How the dashboard draws each cell type this floor uses: label,
        colour role and legend row, in legend order (layouts.base.CELL_TYPE_STYLES)."""
        return cell_type_table(self.grid)

    def no_fly_cells(self) -> List[Cell]:
        """Every cell of a no-fly zone, row by row (empty on classic)."""
        return sorted(self._no_fly, key=lambda cell: (cell[1], cell[0]))

    #: Zone types that are never a task's source or destination: places robots
```


- [ ] **Step 4: What the equipment shows, and faults on demand**

`view()` is the equipment's `to_dict()` (unchanged) plus the cells the dashboard draws, in line order. `fault_kind` is the name check `arm` already makes, now public, so the route can answer 404 for an unknown kind before it looks at the count.

**Replace in** `backend/equipment.py`:

```python
            "arm_cells": {key: cell_dict(cell) for key, cell in self.arm_cells.items()},
            "handoffs": len(self.handoffs),
        }
```

with:

```python
            "arm_cells": {key: cell_dict(cell) for key, cell in self.arm_cells.items()},
            "handoffs": len(self.handoffs),
        }

    def view(self) -> Dict[str, Any]:
        """What the dashboard draws (snapshot["equipment"], GET /api/equipment):
        to_dict() plus each item on the line — its cell, box, kind and order —
        and each jammed cell, both upstream first."""
        conveyor = self.conveyor
        data = self.to_dict()
        items = []
        for x, y in conveyor.cells:
            item = conveyor.items.get((x, y))
            if item is None:
                continue
            box = self.twin.find_box(item.box_id)
            items.append({"x": x, "y": y, "box_id": item.box_id, "kind": box.kind.value if box is not None else None,
                          "order_id": item.order_id})
        data["item_cells"] = items
        data["jammed_cells"] = [{"x": x, "y": y} for x, y in conveyor.cells if (x, y) in conveyor.jams]
        return data
```

**Replace in** `backend/faults.py`:

```python
from typing import Dict, Optional
```

with:

```python
from typing import Any, Dict, Optional
```

**Replace in** `backend/faults.py`:

```python
def _kind(kind: str) -> str:
    key = str(kind or "").strip().lower().replace("-", "_")
    if key not in FAULT_RISKS:
```

with:

```python
def fault_kind(kind: Any) -> Optional[str]:
    """The fault kind `kind` names (any case, '-' or '_'), or None if it names none."""
    key = str(kind or "").strip().lower().replace("-", "_")
    return key if key in FAULT_RISKS else None


def _kind(kind: str) -> str:
    key = fault_kind(kind)
    if key is None:
```

**Replace in** `backend/faults.py`:

```python
        if int(count) < 1:
            raise ValueError("count must be at least 1")
        self._armed[key] = self._armed.get(key, 0) + int(count)
```

with:

```python
        # A whole number only: int() would turn 2.5 into 2 and "3" into 3.
        if isinstance(count, bool) or not isinstance(count, int) or count < 1:
            raise ValueError("count must be a whole number, at least 1")
        self._armed[key] = self._armed.get(key, 0) + count
```


- [ ] **Step 5: Orders by number, and one order by id**

**Replace in** `backend/operations/orders.py`:

```python
    """A stage can't be built: the reason fails or retries it."""
```

with:

```python
    """A stage can't be built: the reason fails or retries it."""


def order_number(order_id: str) -> int:
    """ORD-0042 -> 42, so orders sort by number, not by their id's text."""
    digits = order_id.rsplit("-", 1)[-1]
    return int(digits) if digits.isdigit() else -1
```

**Replace in** `backend/operations/orders.py`:

```python
    def list(self, status: Optional[str] = None, kind: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        orders = sorted(self.orders.values(), key=lambda o: o.order_id, reverse=True)
```

with:

```python
    def get(self, order_id: str) -> Dict[str, Any]:
        """One order, with its stages; KeyError if there is no such order."""
        order = self.orders.get(order_id)
        if order is None:
            raise KeyError(f"Order '{order_id}' does not exist")
        return order.to_dict()

    def list(self, status: Optional[str] = None, kind: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        """The newest orders first, by order number (ORD-10000 after ORD-9999)."""
        orders = sorted(self.orders.values(), key=lambda o: order_number(o.order_id), reverse=True)
```


- [ ] **Step 6: The shift panel, and a configure that changes all or nothing**

The engine keeps exceptions from the subscription it already has (`_on_event`). `reset()` clears them along with everything else. `configure` now works out every new value first and changes the engine only when all of them are good.

**Replace in** `backend/operations/shift.py`:

```python
controls are plan 1c's.
```

with:

```python
controls and panel are backend/operations_api.py's.
```

**Replace in** `backend/operations/shift.py`:

```python
from typing import Any, Dict, List, Optional

from ..models import CONFIG, BoxKind, BoxStatus, EventType, LogCategory
from .orders import OrderBook
```

with:

```python
from collections import deque
from typing import Any, Deque, Dict, List, Optional

from ..models import CONFIG, BoxKind, BoxStatus, EventType, LogCategory
from .orders import FAILED, IN_PROGRESS, OPEN, OrderBook, order_number
```

**Replace in** `backend/operations/shift.py`:

```python
DEFAULT_SKUS = tuple(f"SKU-{number:03d}" for number in range(1, 41))
```

with:

```python
DEFAULT_SKUS = tuple(f"SKU-{number:03d}" for number in range(1, 41))
#: How many failed orders and exceptions the shift panel lists (the latest).
PANEL_LIST_LIMIT = 20
#: The events the shift panel lists as exceptions (spec §10.3, §11.5): a
#: safety wait escalated, an order failed, a stock variance too big to
#: reconcile on the spot.
EXCEPTION_EVENTS = (EventType.SAFETY_WAIT_ESCALATED.value, EventType.ORDER_FAILED.value,
                    EventType.STOCK_VARIANCE_DETECTED.value)


def _finite(value: Any, what: str) -> float:
    """`value` as a finite float, else ValueError naming `what` — for a bool, a
    non-number, NaN, an infinity, or an int too big for a float."""
    if isinstance(value, bool):
        raise ValueError(f"{what} must be a number, not {value!r}")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError(f"{what} must be a finite number, not {value!r}") from None
    if not math.isfinite(number):
        raise ValueError(f"{what} must be a finite number, not {value!r}")
    return number
```

**Replace in** `backend/operations/shift.py`:

```python
        self.counters["skipped_orders"] = 0
```

with:

```python
        self.counters["skipped_orders"] = 0
        # The latest exceptions for the shift panel, kept as they happen (the
        # in-memory event buffer forgets them on a long shift).
        self.exceptions: Deque[Dict[str, Any]] = deque(maxlen=PANEL_LIST_LIMIT)
```

**Replace in** `backend/operations/shift.py`:

```python
        self.orders.on_event(event)
```

with:

```python
        self.orders.on_event(event)
        self._note_exception(event)

    def _note_exception(self, event: Dict[str, Any]) -> None:
        kind = event.get("event")
        if kind not in EXCEPTION_EVENTS:
            return
        if kind == EventType.STOCK_VARIANCE_DETECTED.value and (event.get("data") or {}).get("auto_reconciled"):
            return  # corrected on the spot: nothing for anyone to do
        self.exceptions.append({"type": kind, "message": event.get("message"), "tick": self.twin.tick_count})
```

**Replace in** `backend/operations/shift.py`:

```python
        """Change the pace, the seed (which resets the RNG) or any rate."""
        if pace is not None:
            if isinstance(pace, bool) or not math.isfinite(float(pace)) or float(pace) <= 0:
                raise ValueError("pace must be a finite number greater than zero")
            self.pace = float(pace)
```

with:

```python
        """Change the pace, the seed (which resets the RNG) or any rate. Every
        value is checked before anything changes, so a bad one changes nothing."""
        new_pace = None
        if pace is not None:
            new_pace = _finite(pace, "pace")
            if new_pace <= 0:
                raise ValueError("pace must be a finite number greater than zero")
        if rates is not None and not isinstance(rates, dict):
            raise ValueError("rates must map a stream to its rate per hour")
        new_rates: Dict[str, float] = {}
```

**Replace in** `backend/operations/shift.py`:

```python
            if isinstance(rate, bool) or not math.isfinite(float(rate)) or float(rate) < 0:
                raise ValueError(f"{stream} must be a finite number, zero or more per hour")
            self.rates[stream] = float(rate)
        if seed is not None:
            if isinstance(seed, bool) or not isinstance(seed, int):
                raise ValueError("seed must be a whole number")
```

with:

```python
            new_rates[stream] = _finite(rate, stream)
            if new_rates[stream] < 0:
                raise ValueError(f"{stream} must be a finite number, zero or more per hour")
        if seed is not None and (isinstance(seed, bool) or not isinstance(seed, int)):
            raise ValueError("seed must be a whole number")
        if new_pace is not None:
            self.pace = new_pace
        self.rates.update(new_rates)
        if seed is not None:
```

**Replace in** `backend/operations/shift.py`:

```python
                "throughput_per_hour": round(done / hours, 1)}
```

with:

```python
                "throughput_per_hour": round(done / hours, 1)}

    def panel(self) -> Dict[str, Any]:
        """The shift panel (GET /api/shift, snapshot["shift"]): status_dict()
        plus how many orders are in flight and waiting, and the latest failed
        orders and exceptions, newest first."""
        panel = self.status_dict()
        panel["in_flight"] = sum(kind[IN_PROGRESS] for kind in panel["orders"].values())
        panel["backlog"] = sum(kind[OPEN] for kind in panel["orders"].values())
        failed = sorted((order for order in self.orders.orders.values() if order.status == FAILED),
                        key=lambda order: (order.completed_at or 0.0, order_number(order.order_id)), reverse=True)
        panel["failed_orders"] = [{"order_id": order.order_id, "kind": order.kind, "reason": order.failure_reason}
                                  for order in failed[:PANEL_LIST_LIMIT]]
        panel["exceptions"] = [dict(entry) for entry in reversed(self.exceptions)]
        return panel
```


- [ ] **Step 7: The live snapshot**

`equipment` and `shift` go on every snapshot, including the ones the simulator streams. `cell_types` and `no_fly_cells` are layout data, so they come only with `include_layout`, like `warehouse`.

**Replace in** `backend/digital_twin.py`:

```python
                "options": self.options(),
                "timestamp": now_iso(),
            }
```

with:

```python
                "options": self.options(),
                "timestamp": now_iso(),
                # The floor's live operations (spec §12); None on a floor without them (classic).
                "equipment": self.equipment.view() if self.equipment is not None else None,
                "shift": self.shift.panel() if self.layout_name != "classic" else None,
            }
```

**Replace in** `backend/digital_twin.py`:

```python
                state["warehouse"] = self.warehouse.to_dict()
                state["config"] = {
```

with:

```python
                state["warehouse"] = self.warehouse.to_dict()
                state["cell_types"] = self.warehouse.cell_type_table()
                state["no_fly_cells"] = [cell_dict(cell) for cell in self.warehouse.no_fly_cells()]
                state["config"] = {
```


- [ ] **Step 8: The routes**

**Create** `backend/operations_api.py`:

```python
"""REST routes for the floor's operations (multi-embodiment spec §11.5, §14):
the shift and its panel, the orders, the stock ledger, the people, the
conveyor and sorter, and faults injected on demand.

Every handler holds twin.lock while it reads or changes any of them: the
shift engine, the order book, the ledger, the equipment and the people
helpers take no lock of their own (the simulator tick holds it too). Errors
come back in the same JSON shape as the rest of the API: an unknown id or
fault kind → 404, a state conflict (the classic floor has no shift engine and
no conveyor) → 409, bad input → 400. A call that changes the shift publishes
the new state, so the dashboard sees it at once.
"""
from __future__ import annotations

import functools
from typing import Any, Dict, Optional, Sequence

from flask import jsonify, request

from .faults import FAULT_RISKS, fault_kind
from .operations.orders import DONE, FAILED, IN_PROGRESS, OPEN, ORDER_KINDS

ORDER_STATUSES = (OPEN, IN_PROGRESS, DONE, FAILED)
#: What POST /api/shift/config may change (ShiftEngine.configure's arguments).
SHIFT_CONFIG_FIELDS = ("pace", "seed", "rates")
#: How many orders GET /api/orders lists when no limit is given.
DEFAULT_ORDER_LIMIT = 100
#: How many of the latest hand-off records GET /api/equipment lists.
RECENT_HANDOFFS = 20


def register_operations_routes(app: Any, twin: Any, broadcaster: Any, api_error: Any) -> None:
    def guarded(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except api_error:
                raise
            except KeyError as exc:
                raise api_error(str(exc.args[0]) if exc.args else "Not found", status=404)
            except ValueError as exc:
                raise api_error(str(exc))
        return wrapper

    def body() -> Dict[str, Any]:
        data = request.get_json(silent=True)
        if data is None:
            data = request.form.to_dict() or {}
        if not isinstance(data, dict):
            raise api_error("Request body must be a JSON object")
        return data

    def needs_shift() -> None:
        """Only a floor with a shift engine has shift controls (409 on classic)."""
        if twin.layout_name == "classic":
            raise api_error("The classic floor has no shift engine", status=409)

    def needs_equipment() -> None:
        if twin.equipment is None:
            raise api_error(f"The {twin.layout_name} floor has no conveyor or sorter", status=409)

    def published(state: Dict[str, Any]):
        """Publish the state after a shift control, and answer with its panel."""
        broadcaster.publish("state", state)
        return jsonify({"ok": True, "shift": state["shift"]})

    def choice(key: str, allowed: Sequence[str]) -> Optional[str]:
        """An optional query filter that must be one of `allowed` (any case)."""
        value = (request.args.get(key) or "").strip().upper()
        if not value:
            return None
        if value not in allowed:
            raise api_error(f"{key} must be one of {', '.join(allowed)}", field=key)
        return value

    def limit(default: int) -> int:
        raw = (request.args.get("limit") or "").strip()
        if not raw:
            return default
        if not raw.isdigit() or int(raw) < 1:
            raise api_error("limit must be a whole number, at least 1", field="limit")
        return int(raw)

    def stock_row(location: Any) -> Dict[str, Any]:
        slot = twin.warehouse.slot(location.slot_id)
        return {**location.to_dict(), "discrepancy": location.discrepancy,
                "kind": slot.kind if slot is not None else None, "level": slot.level if slot is not None else None}

    # ---- the shift ------------------------------------------------------- #
    @app.get("/api/shift")
    @guarded
    def get_shift():
        with twin.lock:
            needs_shift()
            return jsonify({"ok": True, "shift": twin.shift.panel()})

    @app.post("/api/shift/start")
    @guarded
    def start_shift():
        with twin.lock:
            needs_shift()
            twin.shift.start()
            state = twin.snapshot()
        return published(state)

    @app.post("/api/shift/pause")
    @guarded
    def pause_shift():
        with twin.lock:
            needs_shift()
            twin.shift.pause()
            state = twin.snapshot()
        return published(state)

    @app.post("/api/shift/config")
    @guarded
    def configure_shift():
        data = body()
        with twin.lock:
            needs_shift()
            unknown = sorted(set(data) - set(SHIFT_CONFIG_FIELDS))
            if unknown:
                raise api_error(f"Unknown shift setting(s): {', '.join(unknown)} "
                                f"(known: {', '.join(SHIFT_CONFIG_FIELDS)})", field=unknown[0])
            if not data:
                raise api_error("Provide pace, seed or rates")
            twin.shift.configure(pace=data.get("pace"), seed=data.get("seed"), rates=data.get("rates"))
            state = twin.snapshot()
        return published(state)

    # ---- orders ---------------------------------------------------------- #
    @app.get("/api/orders")
    @guarded
    def list_orders():
        status, kind = choice("status", ORDER_STATUSES), choice("kind", ORDER_KINDS)
        count = limit(DEFAULT_ORDER_LIMIT)
        with twin.lock:
            book = twin.shift.orders
            return jsonify({"ok": True, "orders": book.list(status=status, kind=kind, limit=count),
                            "counts": book.counts()})

    @app.get("/api/orders/<order_id>")
    @guarded
    def get_order(order_id: str):
        with twin.lock:
            return jsonify({"ok": True, "order": twin.shift.orders.get(order_id)})

    # ---- stock, people, equipment ---------------------------------------- #
    @app.get("/api/stock")
    @guarded
    def get_stock():
        sku = (request.args.get("sku") or "").strip() or None
        with twin.lock:
            rows = [stock_row(location) for location in twin.stock.locations(sku)]
            skus = twin.stock.skus()
        totals = {"recorded_qty": sum(row["recorded_qty"] for row in rows),
                  "true_qty": sum(row["true_qty"] for row in rows)}
        return jsonify({"ok": True, "sku": sku, "locations": rows, "totals": totals, "skus": skus})

    @app.get("/api/people")
    @guarded
    def get_people():
        with twin.lock:
            rows = [{**operator.to_dict(), "in_transit": operator.in_transit} for operator in twin.operators.values()]
        return jsonify({"ok": True, "people": rows})

    @app.get("/api/equipment")
    @guarded
    def get_equipment():
        with twin.lock:
            needs_equipment()
            equipment = twin.equipment
            recent = list(equipment.handoffs)[-RECENT_HANDOFFS:]
            return jsonify({"ok": True, "equipment": equipment.view(),
                            "handoffs": [handoff.to_dict() for handoff in reversed(recent)]})

    # ---- faults ---------------------------------------------------------- #
    @app.post("/api/faults/<kind>")
    @guarded
    def inject_fault(kind: str):
        key = fault_kind(kind)
        if key is None:
            raise api_error(f"Unknown fault kind {kind!r} (known: {', '.join(sorted(FAULT_RISKS))})", status=404)
        count = body().get("count")
        with twin.lock:
            armed = twin.faults.arm(key, 1 if count is None else count)
        return jsonify({"ok": True, "kind": key, "armed": armed})
```

**Replace in** `backend/app.py`:

```python
from .models import CONFIG, LogCategory, Priority, SimulationStatus, now_iso
from .policy import DEFAULT_POLICY_PATH, effective_policy, load_policies
```

with:

```python
from .models import CONFIG, LogCategory, Priority, SimulationStatus, now_iso
from .operations_api import register_operations_routes
from .policy import DEFAULT_POLICY_PATH, effective_policy, load_policies
```

**Replace in** `backend/app.py`:

```python
    register_inventory_routes(app, twin, ApiError)
```

with:

```python
    register_inventory_routes(app, twin, ApiError)
    register_operations_routes(app, twin, broadcaster, ApiError)
```


- [ ] **Step 9: A load starts the panel and the faults afresh**

Task 4's `ShiftEngine.load_state` restores the engine without `reset()`, so the panel's exceptions list would keep entries from before the load; and an armed one-shot fault is not part of a save (Plan ruling 19), so a load clears it, as `reset()` does.

**Replace in** `backend/operations/shift.py`:

```python
        self.yield_until, self.counters, self.orders = yield_until, counters, orders
```

with:

```python
        self.yield_until, self.counters, self.orders = yield_until, counters, orders
        self.exceptions.clear()  # the panel's exceptions belong to the shift before the load
```

**Replace in** `backend/digital_twin.py`:

```python
            self.stock = stock
            self.equipment = equipment
```

with:

```python
            self.stock = stock
            self.equipment = equipment
            # Plan ruling 19: a fault armed against the floor before the load mustn't fire on this one.
            self.faults.clear()
```

- [ ] **Step 10: Run the new tests**

Run: `.venv/bin/python -m pytest -o addopts="" -q backend/test_operations_api.py`
Expected: `23 passed`.

- [ ] **Step 11: Run the full suite**

Run: `.venv/bin/python -m pytest -o addopts="" -q`
Expected: `2 failed, 843 passed` (Task 4's count plus this task's 23 tests; the two failures are the allowed ones).

- [ ] **Step 12: Commit**

```bash
git add backend/operations_api.py backend/app.py backend/digital_twin.py backend/layouts/base.py backend/warehouse.py \
    backend/equipment.py backend/faults.py backend/operations/shift.py backend/operations/orders.py \
    backend/test_operations_api.py
git commit -m "feat: the operations API and the live snapshot

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: The new job types through the task API, the agent and the options

`POST /api/tasks` accepts the new job types (spec §14), each with its own fields. Older types still pass through exactly as before. For a new floor job type the route now does three things. A job field left empty (a blank form input) is left out, so its default applies, instead of reaching the task as `""`. A `quantity` must be a whole number of at least 1, and a form's text `"2"` becomes `2`. A job field the type doesn't take is a 400 that names it: a stray `slot` on TOTE_TO_STATION would otherwise skip its reach check (a plan 1b T9 minor). The dashboard (Task 10) builds its task form from `options()["task_types"]`. Each entry gains `fields`, the form fields to show, and `guide`, one line on what the request needs. The older types keep exactly the fields `frontend/app.js` gives them today. A new type's fields are the ones its `JobSpec` guide names, then `priority`, and a test holds the two in step. Plan 1b ruling 16 (its Task 8 ruling) left classic offering the new job types, which it can only reject. Both lists of task types are now layout-aware: classic offers the 16 older types, as before plan 1b, and the new floor adds the 13 `JOB_SPECS` types. The chat agent's `create_task` tool gains a schema property for every job field (`jobs.JOB_PARAM_KEYS`; plan 1b final review M3). Its `TASK_TYPE_GUIDE` now joins the older types' guides from the same `TASK_GUIDES` table the form reads, so the two can't drift apart.

**Files:**
- Modify: `backend/digital_twin.py` (`TASK_FIELDS`, `TASK_GUIDES` and `task_form`, just above `class DigitalTwin`; `options`)
- Modify: `backend/app.py` (two imports; `JOB_TYPES` and `_job_request` after `_float`; the `POST /api/tasks` handler)
- Modify: `backend/agent_tools.py` (`TASK_TYPE_GUIDE`, `JOB_FIELD_SCHEMAS`, the `create_task` schema)
- Test: `backend/test_job_api.py` (create)

**Interfaces:**
- Consumes: `jobs.JOB_SPECS` (`label`, `guide`, `human`), `jobs.JOB_PARAM_KEYS`, and `TaskManager.create_task`. That takes `box`, `operator`, `agent` and `robot` as aliases of the `*_id` keys, and keeps `JOB_PARAM_KEYS` on `Task.params`. The tests use `create_app(twin=..., autostart=False, run_thread=False)`. Nothing comes from Tasks 1–5.
- Produces:
  - `backend.digital_twin.TASK_FIELDS: Dict[str, List[str]]` covers all 29 task types. Each field is a key `POST /api/tasks` takes, from this vocabulary: `box`, `box_ids`, `source`, `destination`, `priority`, `agent`, `operator`, `slot`, `quantity`, `station`, `face`, `dock`, `lane`, `order_id`, `pack_cell`, `segment`. No list names the robot, because the form offers it for every type.
  - `backend.digital_twin.TASK_GUIDES: Dict[str, str]` holds the 16 older types' guides. `task_form(option) -> Dict` adds `fields` and `guide` to an `{"id", "label"}` entry; a new type's guide is `JOB_SPECS[type].guide`.
  - `options()["task_types"]`: `[{"id", "label", "fields": List[str], "guide": str}]`. Classic lists the 16 older types in their current order. Another floor lists those, then every `JOB_SPECS` type in `JOB_SPECS` order. `options()["robot_capability_task_types"]` is layout-aware the same way: the 8 older ones, plus the non-human job types off classic.
  - `POST /api/tasks` for a `JOB_SPECS` type checks fields as above, and refuses an unknown one with `"<TYPE> takes no '<key>' (its fields: a, b, c)"`, `field=<key>`. A bad quantity gets `"quantity must be a whole number of at least 1"`, `field="quantity"`. Both are 400, and nothing is created.
  - `backend.agent_tools.JOB_FIELD_SCHEMAS: Dict[str, Dict]`. The `create_task` tool gains an optional property for each `JOB_PARAM_KEYS` key; `quantity` is an integer. `TASK_TYPE_GUIDE` lists STOP_ROBOT and RESUME_ROBOT as two entries (they were `STOP_ROBOT/RESUME_ROBOT:robot_id`), and lists BATCH_DELIVER last among the older types.

- [ ] **Step 1: Write the failing tests**

**Create** `backend/test_job_api.py`:

```python
"""The new floor's job types through the task API, the chat agent and the
options (multi-embodiment spec §14 "POST /api/tasks accepts the new job
types"): each type's own fields reach create_task, the task types a floor
offers are the ones it runs, each with its form fields and guide, and the
agent's create-task tool takes every job field."""
import re

import pytest

from backend import people
from backend.agent_tools import TASK_TYPE_GUIDE, TOOLS, execute_tool
from backend.app import create_app
from backend.digital_twin import TASK_FIELDS, TASK_GUIDES, DigitalTwin
from backend.jobs import JOB_PARAM_KEYS, JOB_SPECS
from backend.models import BoxStatus, TaskType

OLDER_TYPES = [
    "PICK_AND_DELIVER", "MOVE_ROBOT", "PICK_BOX", "DELIVER_BOX", "MOVE_BOX", "CHARGE_ROBOT", "STOP_ROBOT",
    "RESUME_ROBOT", "AGENT_INSPECTION", "HUMAN_INSPECTION", "MIXED_MAINTENANCE_MISSION", "AGENT_REPLAN",
    "AGENT_AUDIT", "OPERATOR_APPROVAL", "OPERATOR_MAINTENANCE_SIGNOFF", "BATCH_DELIVER",
]
#: frontend/app.js's TASK_FIELDS as it was before plan 1c: the classic task form keeps exactly these.
DASHBOARD_FIELDS = {
    "PICK_AND_DELIVER": ["box", "source", "destination", "priority"],
    "MOVE_BOX": ["box", "source", "destination", "priority"],
    "DELIVER_BOX": ["box", "destination", "priority"],
    "PICK_BOX": ["box", "priority"],
    "MOVE_ROBOT": ["destination", "priority"],
    "CHARGE_ROBOT": ["priority"],
    "STOP_ROBOT": [],
    "RESUME_ROBOT": [],
    "AGENT_INSPECTION": ["agent"],
    "HUMAN_INSPECTION": ["operator"],
    "MIXED_MAINTENANCE_MISSION": ["destination", "agent", "operator", "priority"],
    "AGENT_REPLAN": ["agent"],
    "AGENT_AUDIT": ["agent"],
    "OPERATOR_APPROVAL": ["operator"],
    "OPERATOR_MAINTENANCE_SIGNOFF": ["operator"],
    "BATCH_DELIVER": ["box_ids", "destination", "priority"],
}
#: The words a JobSpec guide uses for each form field.
GUIDE_WORDS = {"box_id": "box", "operator_id": "operator", "destination": "destination",
               **{key: key for key in JOB_PARAM_KEYS}}
FORM_FIELDS = {"box", "box_ids", "source", "destination", "priority", "agent", "operator", *JOB_PARAM_KEYS}


def make_twin(path, layout="distribution_center"):
    return DigitalTwin(log_dir=str(path / "logs"), data_dir=str(path / "data"), persist_logs=False,
                       demo=True, demo_tasks=False, layout=layout)


@pytest.fixture
def twin(tmp_path):
    return make_twin(tmp_path)


@pytest.fixture
def floor(twin):
    """A body for every job type, the people its human jobs and the humanoid
    need, and a box in the state each job starts from."""
    for name, asset, cell in (("TR50-201", "AST-000201", (8, 13)), ("PF1200-205", "AST-000205", (7, 4)),
                              ("PK30-203", "AST-000203", (20, 14)), ("IX2-208", "AST-000208", (20, 2)),
                              ("SC1-204", "AST-000204", (7, 6)), ("H1-212", "AST-000212", (22, 7))):
        twin.add_robot(name=name, asset_id=asset, position=cell)
    twin.add_robot(name="CX10-210", asset_id="AST-000210")                 # the arm takes its station
    for name, worker, zone in (("Sam", "E-10001", "pick_station_2"), ("Jordan", "E-10006", "returns_qc"),
                               ("Noor", "E-10003", "workshop")):
        people.place(twin, twin.add_operator(name=name, worker_id=worker), zone)
    for name, where in (("PAL-DOCK", {"position": (2, 3)}), ("PAL-STAGED", {"position": (5, 3)}),
                        ("PAL-RACK", {"slot": "PR-10-05-0"}), ("PAL-OUT", {"position": (23, 1)})):
        twin.add_box(name=name, kind="PALLET", sku="SKU-001", quantity=40, weight=500.0, **where)
    twin.add_box(name="TOTE-HOME", kind="TOTE", sku="SKU-002", quantity=12, weight=9.0, slot="TS-10-12-1")
    for name, slot, drop in (("TOTE-P1", "TS-11-12-1", (19, 14)), ("TOTE-P2", "TS-12-12-1", (19, 16))):
        tote = twin.add_box(name=name, kind="TOTE", sku="SKU-002", quantity=12, weight=9.0, slot=slot)
        tote.position = drop                                                # brought to the station's tote drop
        tote.set_status(BoxStatus.DELIVERED)
    twin.add_box(name="RET-1", kind="TOTE", sku="SKU-005", quantity=8, weight=7.0, position=(20, 6))
    twin.equipment.jam((20, 15))
    return twin


@pytest.fixture
def client(floor):
    app, _twin, _sim, _ci = create_app(twin=floor, autostart=False, run_thread=False)
    app.config["TESTING"] = True
    return app.test_client()


# ---- the options ------------------------------------------------------------ #
def test_the_classic_floor_offers_only_the_task_types_it_runs(tmp_path):
    options = make_twin(tmp_path, layout="classic").options()
    # Fails while options() offers the JOB_SPECS types on every floor (plan 1b ruling 16's stopgap).
    assert [entry["id"] for entry in options["task_types"]] == OLDER_TYPES
    assert {entry["id"]: entry["fields"] for entry in options["task_types"]} == DASHBOARD_FIELDS
    assert options["task_types"][0] == {"id": "PICK_AND_DELIVER", "label": "Pick & deliver",
                                        "fields": ["box", "source", "destination", "priority"], "guide": "box+dest"}
    assert all(entry["guide"] == TASK_GUIDES[entry["id"]] for entry in options["task_types"])
    assert [entry["id"] for entry in options["robot_capability_task_types"]] == [
        "PICK_AND_DELIVER", "MOVE_ROBOT", "PICK_BOX", "DELIVER_BOX", "MOVE_BOX", "CHARGE_ROBOT",
        "MIXED_MAINTENANCE_MISSION", "BATCH_DELIVER"]


def test_the_new_floor_offers_every_type_with_its_form(twin):
    options = twin.options()
    assert [entry["id"] for entry in options["task_types"]] == OLDER_TYPES + [kind.value for kind in JOB_SPECS]
    entries = {entry["id"]: entry for entry in options["task_types"]}
    assert entries["TOTE_TO_STATION"] == {"id": "TOTE_TO_STATION", "label": "Tote to station",
                                          "fields": ["box", "station", "priority"],
                                          "guide": JOB_SPECS[TaskType.TOTE_TO_STATION].guide}
    assert entries["MANUAL_PICK"]["fields"] == ["box", "quantity", "order_id", "pack_cell", "operator", "priority"]
    assert entries["PATROL"]["fields"] == ["priority"]
    assert all(set(entry["fields"]) <= FORM_FIELDS for entry in options["task_types"])
    capability = [entry["id"] for entry in options["robot_capability_task_types"]]
    assert {"PATROL", "CYCLE_COUNT", "PACK_ORDER"} <= set(capability)
    assert "MANUAL_PICK" not in capability and "CLEAR_JAM" not in capability     # people do those


def test_each_job_types_fields_are_the_ones_its_guide_names():
    assert set(TASK_FIELDS) == set(OLDER_TYPES) | {kind.value for kind in JOB_SPECS}
    for kind, spec in JOB_SPECS.items():
        outside_notes = re.sub(r"\([^)]*\)", "", spec.guide)      # "(a tote in its slot)" describes, names nothing
        named = {GUIDE_WORDS[word] for word in re.findall(r"[a-z_]+", outside_notes) if word in GUIDE_WORDS}
        assert set(TASK_FIELDS[kind.value]) - {"priority"} == named, kind.value


# ---- POST /api/tasks ---------------------------------------------------------- #
CASES = [
    ("UNLOAD_TRUCK", {"box": "PAL-DOCK", "destination": "intake_staging"}, {"destination": "intake_staging"}),
    ("PUTAWAY_PALLET", {"box": "PAL-STAGED", "slot": "PR-08-02-1"}, {"params": {"slot": "PR-08-02-1"}}),
    ("RETRIEVE_PALLET", {"box": "PAL-RACK", "destination": "outbound_staging"}, {"destination": "outbound_staging"}),
    ("LOAD_TRUCK", {"box": "PAL-OUT", "dock": "dock_3"}, {"params": {"dock": "dock_3"}, "destination": "dock_3"}),
    ("TOTE_TO_STATION", {"box": "TOTE-HOME", "station": "pick_station_2"}, {"params": {"station": "pick_station_2"}}),
    ("RETURN_TOTE", {"box": "TOTE-P1", "slot": "TS-11-12-1"}, {"params": {"slot": "TS-11-12-1"}}),
    ("RETURNS_PUTAWAY", {"box": "RET-1", "slot": "TS-13-12-0"}, {"params": {"slot": "TS-13-12-0"}}),
    # A form sends its numbers as text.
    ("PICK_ITEMS", {"box": "TOTE-P1", "quantity": "2", "order_id": "ORD-7", "pack_cell": "pack_cell_1"},
     {"params": {"quantity": 2, "order_id": "ORD-7", "pack_cell": "pack_cell_1"}}),
    ("PACK_ORDER", {"order_id": "ORD-7", "quantity": 2, "pack_cell": "pack_cell_1", "lane": "dock_5"},
     {"params": {"order_id": "ORD-7", "quantity": 2, "pack_cell": "pack_cell_1", "lane": "dock_5"}}),
    ("MANUAL_PICK", {"box": "TOTE-P2", "quantity": 1, "order_id": "ORD-8", "pack_cell": "pack_cell_1",
                     "operator": "Sam"}, {"params": {"quantity": 1, "order_id": "ORD-8"}, "operator": "Sam"}),
    ("CLEAR_JAM", {"segment": "20,15", "operator": "Noor"}, {"params": {"segment": "20,15"}, "operator": "Noor"}),
    ("CYCLE_COUNT", {"face": "12,2"}, {"params": {"face": "12,2"}}),
    ("PATROL", {"priority": "HIGH"}, {"priority": "HIGH"}),
]


@pytest.mark.parametrize("kind,fields,expected", CASES, ids=[case[0] for case in CASES])
def test_each_new_job_type_is_created_through_the_api_with_its_own_fields(floor, client, kind, fields, expected):
    assert set(fields) <= set(TASK_FIELDS[kind])                 # only the fields its form shows
    response = client.post("/api/tasks", json={"type": kind, **fields})
    body = response.get_json()
    assert response.status_code == 201, body["error"]
    task = body["task"]
    assert task["type"] == kind
    if "box" in fields:
        assert task["box_id"] == floor.find_box(fields["box"]).id
    for key, value in expected.get("params", {}).items():
        assert task["params"][key] == value, key
    if "destination" in expected:
        assert task["destination"] == expected["destination"]
    if "operator" in expected:
        assert task["operator_id"] == floor.find_operator(expected["operator"]).id
    if "priority" in expected:
        assert task["priority"] == expected["priority"]


def test_the_api_checks_a_job_types_own_fields(floor, client):
    before = len(floor.tasks.tasks)
    # A stray slot would skip TOTE_TO_STATION's reach check (it plans from the tote's own slot).
    stray = client.post("/api/tasks", json={"type": "TOTE_TO_STATION", "box": "TOTE-HOME", "slot": "TS-10-12-2"})
    assert stray.status_code == 400
    assert stray.get_json()["error"] == "TOTE_TO_STATION takes no 'slot' (its fields: box, station, priority)"
    assert stray.get_json()["field"] == "slot"
    for quantity in (1.5, "two", 0, True):
        response = client.post("/api/tasks", json={"type": "PICK_ITEMS", "box": "TOTE-P1", "quantity": quantity})
        assert response.status_code == 400, quantity
        assert response.get_json()["error"] == "quantity must be a whole number of at least 1"
    assert len(floor.tasks.tasks) == before                      # nothing was created
    # An empty form field is left out, so its default applies (fails if "" reaches the task's params).
    blank = client.post("/api/tasks", json={"type": "pick_items", "box": "TOTE-P1", "quantity": "1",
                                            "order_id": "", "pack_cell": " "})
    assert blank.status_code == 201, blank.get_json()["error"]
    params = blank.get_json()["task"]["params"]
    assert params["quantity"] == 1 and "order_id" not in params and "pack_cell" not in params
    # The older task types are passed on exactly as before.
    older = client.post("/api/tasks", json={"type": "MOVE_ROBOT", "destination": "parking_area", "slot": "TS-10-12-1"})
    assert older.status_code == 201 and older.get_json()["task"]["params"] == {"slot": "TS-10-12-1"}


# ---- the chat agent ------------------------------------------------------------- #
def test_the_agent_can_give_every_job_field(floor):
    schema = next(tool for tool in TOOLS if tool["function"]["name"] == "create_task")["function"]["parameters"]
    assert set(JOB_PARAM_KEYS) <= set(schema["properties"])      # fails before: the schema had none of them
    assert schema["properties"]["quantity"]["type"] == ["integer", "null"]
    assert all("null" in schema["properties"][key]["type"] for key in JOB_PARAM_KEYS)   # optional (see _opt)
    out = execute_tool(floor, "create_task", {"type": "TOTE_TO_STATION", "box_id": "TOTE-HOME",
                                              "station": "pick_station_2", "slot": None, "robot_id": None})
    assert "error" not in out, out
    assert floor.tasks.get(out["task_id"]).params == {"station": "pick_station_2"}
    for kind in OLDER_TYPES:
        assert f"{kind}:{TASK_GUIDES[kind]}" in TASK_TYPE_GUIDE
    for kind, spec in JOB_SPECS.items():
        assert f"{kind.value}:{spec.guide}" in TASK_TYPE_GUIDE
    assert "PUTAWAY_PALLET:box_id(a staged pallet)" in TASK_TYPE_GUIDE
```


- [ ] **Step 2: Run them to see them fail**

Run: `.venv/bin/python -m pytest -o addopts="" -q backend/test_job_api.py`
Expected: `1 error` during collection — `ImportError: cannot import name 'TASK_FIELDS' from 'backend.digital_twin'`.

- [ ] **Step 3: Each task type's form, and options a floor can run**

The tables go just above `class DigitalTwin`. `options()` wraps its existing list of older types in `task_form`, and adds the job types only off the classic floor.

**Replace in** `backend/digital_twin.py`:

```python
class DigitalTwin:
    # Re-exported so collaborators do not need their own enum imports.
```

with:

```python
#: The fields each task type's form shows, in order (options()["task_types"]).
#: Each is a key POST /api/tasks takes: "box", "agent" and "operator" name a
#: box, agent or operator, and the rest are the request's own keys. The robot
#: is offered for every type, so no list names it. The older types' lists are
#: the dashboard's own; a new floor job type's are the fields its JobSpec guide
#: names, then priority.
TASK_FIELDS: Dict[str, List[str]] = {
    "PICK_AND_DELIVER": ["box", "source", "destination", "priority"],
    "MOVE_ROBOT": ["destination", "priority"],
    "PICK_BOX": ["box", "priority"],
    "DELIVER_BOX": ["box", "destination", "priority"],
    "MOVE_BOX": ["box", "source", "destination", "priority"],
    "CHARGE_ROBOT": ["priority"],
    "STOP_ROBOT": [],
    "RESUME_ROBOT": [],
    "AGENT_INSPECTION": ["agent"],
    "HUMAN_INSPECTION": ["operator"],
    "MIXED_MAINTENANCE_MISSION": ["destination", "agent", "operator", "priority"],
    "AGENT_REPLAN": ["agent"],
    "AGENT_AUDIT": ["agent"],
    "OPERATOR_APPROVAL": ["operator"],
    "OPERATOR_MAINTENANCE_SIGNOFF": ["operator"],
    "BATCH_DELIVER": ["box_ids", "destination", "priority"],
    "UNLOAD_TRUCK": ["box", "destination", "priority"],
    "PUTAWAY_PALLET": ["box", "slot", "priority"],
    "RETRIEVE_PALLET": ["box", "destination", "priority"],
    "LOAD_TRUCK": ["box", "dock", "priority"],
    "TOTE_TO_STATION": ["box", "station", "priority"],
    "RETURN_TOTE": ["box", "slot", "priority"],
    "RETURNS_PUTAWAY": ["box", "slot", "priority"],
    "PICK_ITEMS": ["box", "quantity", "order_id", "pack_cell", "priority"],
    "PACK_ORDER": ["order_id", "quantity", "pack_cell", "lane", "priority"],
    "MANUAL_PICK": ["box", "quantity", "order_id", "pack_cell", "operator", "priority"],
    "CLEAR_JAM": ["segment", "operator", "priority"],
    "CYCLE_COUNT": ["face", "priority"],
    "PATROL": ["priority"],
}

#: What a request for each older task type needs, in one line — the new floor's
#: job types keep theirs on their JobSpec. The dashboard's task form and the
#: chat agent's TASK_TYPE_GUIDE both show these.
TASK_GUIDES: Dict[str, str] = {
    "PICK_AND_DELIVER": "box+dest",
    "MOVE_ROBOT": "dest",
    "PICK_BOX": "box",
    "DELIVER_BOX": "box+dest",
    "MOVE_BOX": "box+dest(robot auto-picked)",
    "CHARGE_ROBOT": "robot_id",
    "STOP_ROBOT": "robot_id",
    "RESUME_ROBOT": "robot_id",
    "AGENT_INSPECTION": "agent_id",
    "HUMAN_INSPECTION": "operator_id(needs safety_inspection)",
    "MIXED_MAINTENANCE_MISSION": "dest(agent+robot+operator, needs electrical_safety)",
    "AGENT_REPLAN": "agent_id(reviews queue, doesn't act)",
    "AGENT_AUDIT": "agent_id(reviews recent logs/CI)",
    "OPERATOR_APPROVAL": "operator_id[+robot_id](needs safety_inspection)",
    "OPERATOR_MAINTENANCE_SIGNOFF": "operator_id[+robot_id](needs electrical_safety, resets wear)",
    "BATCH_DELIVER": "box_ids(2+)+dest",
}


def task_form(option: Dict[str, Any]) -> Dict[str, Any]:
    """A task_types entry with its form: the fields to show and the guide."""
    kind = option["id"]
    guide = TASK_GUIDES.get(kind) or JOB_SPECS[TaskType(kind)].guide
    return {**option, "fields": list(TASK_FIELDS[kind]), "guide": guide}


class DigitalTwin:
    # Re-exported so collaborators do not need their own enum imports.
```

**Replace in** `backend/digital_twin.py`:

```python
    def options(self) -> Dict[str, Any]:
        return {
```

with:

```python
    def options(self) -> Dict[str, Any]:
        # The new floor's job types need a robot with a floor profile, which
        # the classic floor never has: only a floor that runs them offers them.
        jobs = list(JOB_SPECS.items()) if self.layout_name != "classic" else []
        return {
```

**Replace in** `backend/digital_twin.py`:

```python
            "task_types": [
                {"id": TaskType.PICK_AND_DELIVER.value, "label": "Pick & deliver"},
```

with:

```python
            "task_types": [task_form(option) for option in [
                {"id": TaskType.PICK_AND_DELIVER.value, "label": "Pick & deliver"},
```

**Replace in** `backend/digital_twin.py`:

```python
            ] + [{"id": kind.value, "label": spec.label} for kind, spec in JOB_SPECS.items()],
```

with:

```python
            ] + [{"id": kind.value, "label": spec.label} for kind, spec in jobs]],
```

**Replace in** `backend/digital_twin.py`:

```python
            ] + [{"id": kind.value, "label": spec.label} for kind, spec in JOB_SPECS.items() if not spec.human],
```

with:

```python
            ] + [{"id": kind.value, "label": spec.label} for kind, spec in jobs if not spec.human],
```


- [ ] **Step 4: `POST /api/tasks` checks a job type's own fields**

**Replace in** `backend/app.py`:

```python
from .digital_twin import DigitalTwin
```

with:

```python
from .digital_twin import TASK_FIELDS, DigitalTwin
```

**Replace in** `backend/app.py`:

```python
from .inventory_api import register_inventory_routes
from .layouts import LAYOUTS
```

with:

```python
from .inventory_api import register_inventory_routes
from .jobs import JOB_PARAM_KEYS, JOB_SPECS
from .layouts import LAYOUTS
```

**Replace in** `backend/app.py`:

```python
        raise ApiError(f"'{key}' must be a number", field=key)
```

with:

```python
        raise ApiError(f"'{key}' must be a number", field=key)


#: The new floor's job types (jobs.JOB_SPECS), whose own fields _job_request checks.
JOB_TYPES = frozenset(kind.value for kind in JOB_SPECS)


def _job_request(data: Dict[str, Any]) -> Dict[str, Any]:
    """A task request, with a new floor job type's own fields checked before
    create_task sees them: an empty one (a form field left blank) is left out,
    so its default applies; a quantity must be a whole number, at least 1 (a
    form sends it as text); and a job field the type doesn't take is refused —
    a stray slot on TOTE_TO_STATION, say, would skip its reach check. Older
    task types pass through unchanged."""
    kind = str(data.get("type", "")).strip().upper()
    if kind not in JOB_TYPES:
        return data
    request_data = {key: value for key, value in data.items()
                    if key not in JOB_PARAM_KEYS or not (value is None or str(value).strip() == "")}
    fields = TASK_FIELDS[kind]
    for key in JOB_PARAM_KEYS:
        if key in request_data and key not in fields:
            raise ApiError(f"{kind} takes no {key!r} (its fields: {', '.join(fields)})", field=key)
    if "quantity" in request_data:
        quantity = request_data["quantity"]
        if isinstance(quantity, str) and quantity.strip().isdigit():
            quantity = int(quantity)
        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 1:
            raise ApiError("quantity must be a whole number of at least 1", field="quantity")
        request_data["quantity"] = quantity
    return request_data
```

**Replace in** `backend/app.py`:

```python
        task = twin.tasks.create_task(data)
```

with:

```python
        task = twin.tasks.create_task(_job_request(data))
```


- [ ] **Step 5: The agent's tool takes every job field**

`agent_tools` now imports `TASK_GUIDES` from `backend.digital_twin`. Nothing that `digital_twin` imports imports `agent_tools`, so there is no cycle. The new schema descriptions stay as short as the rest, because the tool list is resent uncached on every turn of the chat loop.

**Replace in** `backend/agent_tools.py`:

```python
from .eval_engine import evaluate_events
from .jobs import JOB_SPECS
```

with:

```python
from .digital_twin import TASK_GUIDES
from .eval_engine import evaluate_events
from .jobs import JOB_PARAM_KEYS, JOB_SPECS
```

**Replace in** `backend/agent_tools.py`:

```python
# verbose schema is a real reliability problem, not just noise).
TASK_TYPE_GUIDE = (
    "PICK_AND_DELIVER:box+dest | MOVE_ROBOT:dest | PICK_BOX:box | "
    "DELIVER_BOX:box+dest | MOVE_BOX:box+dest(robot auto-picked) | "
    "BATCH_DELIVER:box_ids(2+)+dest | CHARGE_ROBOT:robot_id | "
    "STOP_ROBOT/RESUME_ROBOT:robot_id | AGENT_INSPECTION:agent_id | "
    "HUMAN_INSPECTION:operator_id(needs safety_inspection) | "
    "MIXED_MAINTENANCE_MISSION:dest(agent+robot+operator, needs electrical_safety) | "
    "AGENT_REPLAN:agent_id(reviews queue, doesn't act) | "
    "AGENT_AUDIT:agent_id(reviews recent logs/CI) | "
    "OPERATOR_APPROVAL:operator_id[+robot_id](needs safety_inspection) | "
    "OPERATOR_MAINTENANCE_SIGNOFF:operator_id[+robot_id](needs electrical_safety, resets wear)"
) + "".join(f" | {kind.value}:{spec.guide}" for kind, spec in JOB_SPECS.items())  # the new-floor jobs
```

with:

```python
# verbose schema is a real reliability problem, not just noise). The older
# types' lines are the twin's TASK_GUIDES (the dashboard's task form shows the
# same ones); the new-floor jobs' are their JobSpec guides.
TASK_TYPE_GUIDE = " | ".join(f"{kind}:{guide}" for kind, guide in TASK_GUIDES.items()) + \
    "".join(f" | {kind.value}:{spec.guide}" for kind, spec in JOB_SPECS.items())

#: The create_task tool's schema for each field a new-floor job reads
#: (jobs.JOB_PARAM_KEYS) — kept as short as the rest, for the same reason.
JOB_FIELD_SCHEMAS: Dict[str, Dict[str, Any]] = {
    "slot": {"type": "string", "description": "Rack or shelf slot id, e.g. PR-08-02-1"},
    "quantity": {"type": "integer", "description": "Units to pick or pack"},
    "station": {"type": "string", "description": "Pick station zone"},
    "face": {"type": "string", "description": "Pallet rack cell to count, 'x,y'"},
    "dock": {"type": "string", "description": "Outbound dock zone"},
    "lane": {"type": "string", "description": "Sorter lane (dock_4 or dock_5)"},
    "order_id": {"type": "string", "description": "The order the items are for"},
    "pack_cell": {"type": "string", "description": "Pack cell zone"},
    "segment": {"type": "string", "description": "Jammed conveyor cell, 'x,y'"},
}
```

**Replace in** `backend/agent_tools.py`:

```python
                    "second_operator_id": _opt("string", description="Explicit second signer; omit for AUTO"),
                },
```

with:

```python
                    "second_operator_id": _opt("string", description="Explicit second signer; omit for AUTO"),
                    **{key: _opt(JOB_FIELD_SCHEMAS[key]["type"], description=JOB_FIELD_SCHEMAS[key]["description"])
                       for key in JOB_PARAM_KEYS},
                },
```


- [ ] **Step 6: Run the new tests**

Run: `.venv/bin/python -m pytest -o addopts="" -q backend/test_job_api.py`
Expected: `18 passed`.

- [ ] **Step 7: Run the full suite**

Run: `.venv/bin/python -m pytest -o addopts="" -q`
Expected: `2 failed, 861 passed` (Task 5's count plus this task's 18 tests; the two failures are the allowed ones).

- [ ] **Step 8: Commit**

```bash
git add backend/digital_twin.py backend/app.py backend/agent_tools.py backend/test_job_api.py
git commit -m "feat: the new job types through the task API, the agent and the options

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Orders and stock over a long shift

Four things the plan 1b soak and reviews found that wear a long shift down (spec §7.2, §8, §11.2, §11.3). **A tote only the humanoid can reach.** `OrderBook._choose_tote` took the first free tote of a SKU without asking who could bring it: an order held Pick 2 for half an hour on a level-2 returned tote while both AMRs idled (`final-rereview-report.md`, out-of-scope observation 1). A line now takes a tote a robot able to do TOTE_TO_STATION can bring now — one the AMRs reach before one only the humanoid reaches — and waits, spending no retry, while the only robots that reach it are halted or unsupervised; only when no robot on the floor could ever reach any tote of the SKU is one asked for anyway, so the gate rejects it with its reason (an existing test pins that). **A jam no one can clear.** `ensure_jam_jobs` made a FAILED CLEAR_JAM — a TASK_CREATED, an ERROR TASK_FAILED, a log file and a `failed_tasks` stat — every 20 ticks while nobody qualified was on the floor (~1,200 an hour at 1×). The gate is now asked twice — the ask and, as for any rejected job, its one retry (§11.2); an existing test pins those two rejections — and then the jam waits, logged once: each check runs the gate's own check (`TaskManager.validate`) on a CLEAR_JAM that is never recorded, and asks the gate again only once someone qualifies. **A misplaced box's variance.** A wrong-level placement zeroes the recorded slot's true quantity, so what the box really held was lost, and `physical_qty` counted it in the slot it went to by its recorded quantity (M4). The box now carries what it really holds (`Box.true_quantity`), the count reads that, and putting the box right gives it back to the record. **One bad item stops the floor.** The sorter's drop loop and `Equipment.take` read a box the twin may no longer have, and an exception from `equipment.tick()` aborted `Simulator.tick` (plan 1b Task 3 minor); both guards and the containment follow the shift engine's pattern: log the error, keep the tick going.

**Files:**
- Modify: `backend/operations/orders.py` (the imports; `_choose_tote`, and three helpers after it)
- Modify: `backend/human_jobs.py` (the imports; `JAM_GATE_ASKS`, `someone_can_clear`, `ensure_jam_jobs`)
- Modify: `backend/equipment.py` (`take`; the sorter's drop loop in `_run_sorter`)
- Modify: `backend/simulator.py` (`__init__`'s `_jam_asks`; the `equipment.tick()` call site in `tick`)
- Modify: `backend/box.py` (`Box.true_quantity`)
- Modify: `backend/goods.py` (`store`'s misplaced-box branch; `physical_qty`)
- Test: `backend/test_long_shift_orders.py` (create)

**Interfaces:**
- Consumes: `jobs.JOB_SPECS[TaskType.TOTE_TO_STATION].classes`; `eligibility.box_kind_ok`, `payload_ok`, `reach_ok`, `robot_eligibility`; `people.supervision_available`; `TaskManager.validate(task) -> (ok, error)` and `task_manager.Task`; `Conveyor.jams` (`{cell: the tick it jammed}`).
- Produces:
  - `OrderBook._choose_tote(sku, units) -> Optional[Box]` — unchanged signature, the reach rule above; private helpers `_tote_bodies()`, `_reaches(box, profile)`, `_tote_rank(box, bodies)`.
  - `human_jobs.JAM_GATE_ASKS = 2`; `human_jobs.someone_can_clear(twin, segment: str) -> Tuple[bool, Optional[str]]`; `human_jobs.ensure_jam_jobs(twin, rejected: Optional[Dict[Tuple[Cell, int], int]] = None) -> List[Task]` — `rejected` maps (jammed cell, the tick it jammed) to the gate's rejections since it last had a job; without it every check asks the gate, as before.
  - `Simulator._jam_asks: Dict[Tuple[Cell, int], int]`, passed to `ensure_jam_jobs` (Simulator state: not saved, like `_charge_retry`).
  - `Box.true_quantity: Optional[int]` — what a misplaced box (`true_slot` set) really holds, None otherwise; in `Box.to_dict()` / `Box.from_dict()`, so a save keeps it.
  - `goods.physical_qty(twin, slot_id) -> int` — unchanged signature; a misplaced box counts by `true_quantity`. A misplaced box's recorded location keeps `true_qty == 0`, as before.
  - `Equipment.take(cell, receiver, task_id=None)` raises `ValueError` (after freeing the cell) for an item whose box is gone; the sorter drops such an item.

- [ ] **Step 1: Write the failing tests**

**Create** `backend/test_long_shift_orders.py`:

```python
"""Orders and stock over a long shift (multi-embodiment spec §7.2, §8, §11.2,
§11.3): a customer line takes a tote a robot can bring now — one the AMRs
reach before one only the humanoid reaches — and waits, spending no retry,
while the only robot that reaches it can't work; a jam no one qualified can
clear is asked for through the gate twice, then waits silently until someone
qualifies; a misplaced box is counted by what it really holds; and a box that
vanishes from the conveyor or the sorter, or an error in the equipment's tick,
never stops the floor."""
import pytest

from backend import goods, people
from backend.digital_twin import DigitalTwin
from backend.models import BoxStatus, SimulationStatus, TaskStatus, TaskType
from backend.operations.orders import ORDER_RETRY_DELAY_S
from backend.simulator import Simulator

#: Bounds, well above what each chain takes, so a stall fails a test instead of hanging it.
MAX_TICKS = 4000
CHECK = 20                                     # CONFIG["SHIFT_CHECK_EVERY_TICKS"]

#: The tote robots, the picker and the humanoid of the populated floor (backend/test_shift_soak.py).
FLEET = (("TR50-201", "AST-000201", (8, 13)), ("TR50-101", "AST-000101", (8, 15)),
         ("PK30-203", "AST-000203", (20, 14)), ("H1-212", "AST-000212", (22, 7)))


def make_twin(tmp_path, fleet=FLEET):
    twin = DigitalTwin(log_dir=str(tmp_path / "logs"), data_dir=str(tmp_path / "data"), persist_logs=False,
                       demo=True, demo_tasks=False, layout="distribution_center")
    for name, asset, cell in fleet:
        twin.add_robot(name=name, asset_id=asset, position=cell)
    twin.add_robot(name="CX10-210", asset_id="AST-000210")
    twin.add_robot(name="CX10-211", asset_id="AST-000211")
    return twin


def running(twin):
    simulator = Simulator(twin)
    twin.simulation_status = SimulationStatus.RUNNING
    return simulator


def jordan_on(twin):
    """The humanoid's supervisor, standing beside it in returns_qc."""
    jordan = twin.add_operator(name="Jordan", worker_id="E-10006")
    people.place(twin, jordan, "returns_qc")
    return jordan


def tasks_of(twin, task_type):
    return [task for task in twin.tasks.tasks.values() if task.type is task_type]


def run(sim, *orders, max_ticks=MAX_TICKS):
    for _ in range(max_ticks):
        if all(order.is_terminal for order in orders):
            return
        sim.tick()
    stalled = [(o.order_id, o.status, [(s.name, s.status, s.attempts, s.error) for s in o.stages
                                       if s.status != "DONE"]) for o in orders if not o.is_terminal]
    assert not stalled, stalled


# ---- a customer line takes a tote a robot can bring now ---------------------- #
def test_a_line_takes_the_tote_the_amrs_reach_before_one_only_the_humanoid_reaches(tmp_path):
    twin = make_twin(tmp_path)
    jordan_on(twin)
    # TS-08-12-2 sorts first, so a choice that ignores reach takes the level-2 tote
    # (the AMRs reach level 1): only the humanoid could bring it.
    high = twin.add_box(name="TOTE-HIGH", kind="TOTE", sku="SKU-001", quantity=20, weight=21.5, slot="TS-08-12-2")
    low = twin.add_box(name="TOTE-LOW", kind="TOTE", sku="SKU-001", quantity=20, weight=21.5, slot="TS-09-12-0")
    sim = running(twin)
    order = twin.shift.orders.customer([{"sku": "SKU-001", "units": 1}], "dock_4")
    twin.shift.orders.advance()
    assert order.lines[0]["tote_id"] == low.id                      # fails if _choose_tote ignores reach
    leg = twin.tasks.get(order.stages[0].task_id)
    for _ in range(MAX_TICKS):
        if leg.is_terminal:
            break
        sim.tick()
    assert leg.status is TaskStatus.COMPLETED, leg.error
    assert leg.box_id == low.id and twin.find_robot(leg.robot_id).mobility.embodiment_class == "AMR"
    assert low.position == (19, 14)                                  # on Pick 1's tote drop
    assert high.position == (8, 12) and high.status is BoxStatus.STORED   # never moved


def test_a_tote_only_the_humanoid_reaches_waits_for_its_supervisor_without_spending_a_retry(tmp_path):
    twin = make_twin(tmp_path)                                       # nobody on the floor holds humanoid_supervision
    twin.add_box(name="TOTE-HIGH", kind="TOTE", sku="SKU-001", quantity=20, weight=21.5, slot="TS-08-12-2")
    sim = running(twin)
    order = twin.shift.orders.customer([{"sku": "SKU-001", "units": 1}], "dock_4")
    for _ in range(int(3 * ORDER_RETRY_DELAY_S / sim.dt)):            # three retry delays
        sim.tick()
    # Asking for it anyway, the gate rejects it ("can't work unsupervised"), the retry
    # is spent the same way, and the order fails: a wait must spend nothing.
    assert (order.status, order.stages[0].attempts) == ("OPEN", 0), order.failure_reason
    assert not tasks_of(twin, TaskType.TOTE_TO_STATION)
    jordan_on(twin)                                                  # the supervisor clocks on
    for _ in range(5):
        sim.tick()
    assert order.status == "IN_PROGRESS"
    leg = twin.tasks.get(order.stages[0].task_id)
    assert leg.status is not TaskStatus.FAILED, leg.error
    assert leg.robot_id == twin.find_robot("H1-212").id


def test_a_tote_no_robot_on_the_floor_could_ever_reach_still_fails_the_order_with_the_gates_reason(tmp_path):
    twin = make_twin(tmp_path, fleet=FLEET[:3])                      # AMRs and the picker, no humanoid
    twin.add_box(name="TOTE-HIGH", kind="TOTE", sku="SKU-001", quantity=20, weight=21.5, slot="TS-08-12-2")
    sim = running(twin)
    order = twin.shift.orders.customer([{"sku": "SKU-001", "units": 1}], "dock_4")
    run(sim, order, max_ticks=int((ORDER_RETRY_DELAY_S + 5) / sim.dt))
    # Waiting here would never end: no body on the floor reaches level 2. Returning
    # None for it as for a busy tote would leave the order OPEN for good.
    assert (order.status, order.stages[0].attempts) == ("FAILED", 2)
    assert order.failure_reason.startswith("No robot can do this TOTE_TO_STATION")
    assert "level 2 is out of its reach" in order.failure_reason


# ---- a jam no one qualified can clear --------------------------------------- #
def test_a_jam_no_one_can_clear_is_asked_twice_then_waits_silently_until_someone_qualifies(tmp_path):
    twin = make_twin(tmp_path, fleet=())                             # the arms only
    riley = twin.add_operator(name="Riley", worker_id="E-10008")     # her robot_cell_access is revoked
    people.place(twin, riley, "pick_station_1")
    sim = running(twin)
    twin.equipment.jam((23, 15))                                     # inside pack cell 1
    failed_before = twin.statistics["failed_tasks"]
    for _ in range(12 * CHECK):                                      # twelve checks
        sim.tick()
    asked = tasks_of(twin, TaskType.CLEAR_JAM)
    # Asking the gate at every check makes a FAILED job, a TASK_FAILED and a
    # failed_tasks stat each time: twelve here, ~1,200 an hour at 1x.
    assert len(asked) == 2 and all(task.status is TaskStatus.FAILED for task in asked)
    assert twin.statistics["failed_tasks"] - failed_before == 2
    waits = [r for r in twin.logger.query(search="waiting until someone qualified")]
    assert len(waits) == 1 and "23,15" in waits[0]["message"]
    mateo = twin.add_operator(name="Mateo", worker_id="E-10004")
    people.place(twin, mateo, "workshop")
    for _ in range(CHECK):
        sim.tick()
    asked = tasks_of(twin, TaskType.CLEAR_JAM)
    assert len(asked) == 3 and asked[-1].operator_id == mateo.id and asked[-1].status is not TaskStatus.FAILED
    for _ in range(MAX_TICKS):
        if asked[-1].is_terminal:
            break
        sim.tick()
    assert asked[-1].status is TaskStatus.COMPLETED and (23, 15) not in twin.equipment.conveyor.jams


def test_a_new_jam_after_one_was_cleared_is_asked_for_afresh(tmp_path):
    twin = make_twin(tmp_path, fleet=())
    sim = running(twin)
    twin.equipment.jam((20, 15))                                     # outside the pack cells: anyone on shift
    for _ in range(4 * CHECK):
        sim.tick()
    assert len(tasks_of(twin, TaskType.CLEAR_JAM)) == 2              # nobody on the floor at all
    twin.equipment.clear_jam((20, 15))                               # cleared some other way
    sim.tick()
    twin.equipment.jam((20, 15))
    for _ in range(4 * CHECK):
        sim.tick()
    # A wait remembered past its jam would leave this one never asked for.
    assert len(tasks_of(twin, TaskType.CLEAR_JAM)) == 4


# ---- a misplaced box is counted by what it really holds --------------------- #
def test_a_misplaced_box_is_counted_by_its_true_quantity(tmp_path):
    twin = make_twin(tmp_path, fleet=())
    pallet = twin.add_box(name="PAL-1", kind="PALLET", sku="SKU-01", quantity=40, weight=500.0, slot="PR-12-02-1")
    twin.stock.adjust_true("PR-12-02-1", -3)                         # three cases short before it moves
    goods.store(twin, pallet, "PR-12-02-1", true_slot_id="PR-12-02-2")   # put back a level too high
    assert goods.physical_qty(twin, "PR-12-02-1") == 0
    assert twin.stock.location("PR-12-02-1").true_qty == 0          # the record's slot is really empty
    assert goods.physical_qty(twin, "PR-12-02-2") == 37              # its recorded 40 before the fix
    assert pallet.true_quantity == 37 and pallet.to_dict()["true_quantity"] == 37
    goods.store(twin, pallet, "PR-12-02-1")                          # put right: the variance comes with it
    assert goods.physical_qty(twin, "PR-12-02-1") == 37 and twin.stock.location("PR-12-02-1").discrepancy == -3
    assert (pallet.true_slot, pallet.true_quantity) == (None, None)


def test_a_saved_misplaced_box_keeps_what_it_really_holds(tmp_path):
    twin = make_twin(tmp_path, fleet=())
    pallet = twin.add_box(name="PAL-1", kind="PALLET", sku="SKU-01", quantity=40, weight=500.0, slot="PR-12-02-1")
    twin.stock.adjust_true("PR-12-02-1", -3)
    goods.store(twin, pallet, "PR-12-02-1", true_slot_id="PR-12-02-2")
    restored = type(pallet).from_dict(pallet.to_dict())
    assert (restored.true_slot, restored.true_quantity) == ("PR-12-02-2", 37)


def test_a_drone_counting_the_face_reads_the_misplaced_box_by_its_true_quantity(tmp_path):
    twin = make_twin(tmp_path, fleet=(("IX2-208", "AST-000208", (20, 2)),))
    pallet = twin.add_box(name="PAL-1", kind="PALLET", sku="SKU-01", quantity=40, weight=500.0, slot="PR-12-02-1")
    twin.stock.adjust_true("PR-12-02-1", -3)
    goods.store(twin, pallet, "PR-12-02-1", true_slot_id="PR-12-02-2")
    sim = running(twin)
    task = twin.tasks.create_task({"type": "CYCLE_COUNT", "face": "12,2"})
    for _ in range(1500):
        if task.is_terminal:
            break
        sim.tick()
    assert task.status is TaskStatus.COMPLETED, task.error
    scans = {e["data"]["level"]: e["data"]["true_qty"] for e in twin.events.query(task_id=task.id,
                                                                                event_type="SCANNED")}
    assert (scans[1], scans[2]) == (0, 37)


# ---- one bad item never stops the floor ------------------------------------- #
def carton_in_sorter(twin, sim):
    entry = twin.equipment.sorter.entry
    carton = twin.add_box(name=f"CARTON-{len(twin.boxes) + 1}", kind="CARTON", weight=2.0, position=entry,
                          destination="dock_4")                    # a box made on the line rides it
    for _ in range(200):
        if twin.equipment.sorter.inside:
            return carton
        sim.tick()
    raise AssertionError("the carton never entered the sorter")


def test_a_carton_that_vanishes_inside_the_sorter_is_dropped_from_it(tmp_path):
    twin = make_twin(tmp_path, fleet=())
    sim = running(twin)
    carton = carton_in_sorter(twin, sim)
    del twin.boxes[carton.id]                                        # gone while inside
    before = twin.tick_count
    for _ in range(200):
        sim.tick()                                                   # its drop raised AttributeError every tick
    assert twin.tick_count == before + 200 and not twin.equipment.sorter.inside
    second = carton_in_sorter(twin, sim)                             # the sorter still works
    for _ in range(200):
        sim.tick()
    assert second.status is BoxStatus.DELIVERED and twin.warehouse.zone_of_cell(second.position).key == "dock_4"


def test_taking_an_item_that_vanished_frees_its_cell_and_says_so(tmp_path):
    twin = make_twin(tmp_path, fleet=())
    cell = twin.equipment.arm_cells["pack_cell_1"]
    item = twin.add_box(name="ITEM-1", kind="ITEM", sku="SKU-01", quantity=1, weight=0.4, position=cell)
    del twin.boxes[item.id]
    with pytest.raises(ValueError, match="no longer known"):         # an AttributeError before the guard
        twin.equipment.take(cell, "arm")
    assert twin.equipment.conveyor.is_free(cell)


def test_an_error_in_the_equipment_tick_does_not_stop_the_floor(tmp_path, monkeypatch):
    twin = make_twin(tmp_path, fleet=(("TR50-201", "AST-000201", (8, 13)),))
    amr = twin.find_robot("TR50-201")
    sim = running(twin)
    task = twin.tasks.create_task({"type": "MOVE_ROBOT", "robot_id": amr.id, "destination": "parking_area"})

    def broken():
        raise RuntimeError("the line controller is unavailable")

    monkeypatch.setattr(twin.equipment, "tick", broken)
    for _ in range(400):
        if task.is_terminal:
            break
        sim.tick()                                                   # no exception reaches the caller
    assert task.status is TaskStatus.COMPLETED, task.error           # robots kept moving
    errors = twin.logger.query(search="the line controller is unavailable")
    assert errors and errors[0]["level"] == "ERROR"
```


- [ ] **Step 2: Run them to see them fail**

Run: `.venv/bin/python -m pytest -o addopts="" -q backend/test_long_shift_orders.py`
Expected: `10 failed, 1 passed`. The two tote tests fail on the level-2 tote being chosen (`'box_001' == 'box_002'`) and on the order FAILED by the gate's "can't work unsupervised" (`('FAILED', 2) == ('OPEN', 0)`); the jam tests on twelve and four FAILED CLEAR_JAMs (`12 == 2`, `4 == 2`); the misplaced-box tests on `40 == 37` and `AttributeError: 'Box' object has no attribute 'true_quantity'`; the equipment tests on `AttributeError: 'NoneType' object has no attribute 'destination'`, `... 'declared_weight_kg'` and the `RuntimeError` reaching the caller. `test_a_tote_no_robot_on_the_floor_could_ever_reach_still_fails_the_order_with_the_gates_reason` passes: it pins the behaviour this task keeps.

- [ ] **Step 3: A line takes a tote a robot can bring now**

A body "can bring" a tote by the gate's own capability rules (kind, declared weight, the slot's level); it is "able now" when `robot_eligibility` passes (not halted, approved firmware, the job allowed) and, for the humanoid, supervision is available. Busy, halted and unsupervised bodies are waited for; a SKU no body on the floor could ever reach falls back to today's choice so the gate says why.

**Replace in** `backend/operations/orders.py`:

```python
from typing import Any, Dict, List, Optional

from ..jobs import tote_is_home
```

with:

```python
from typing import Any, Dict, List, Optional, Tuple

from .. import people
from ..eligibility import box_kind_ok, payload_ok, reach_ok, robot_eligibility
from ..jobs import JOB_SPECS, tote_is_home
```

**Replace in** `backend/operations/orders.py`:

```python
        """A tote of `sku` holding `units`, home in its slot and wanted by no
        job right now; None while every such tote is busy."""
```

with:

```python
        """A tote of `sku` holding `units`, home in its slot, wanted by no job
        right now, and one a robot that can bring it is able to now: a tote
        the AMRs reach before one only the humanoid reaches (it needs its
        supervisor, and stops while he walks). None while every such tote is
        busy, or the only robots that reach it are halted or unsupervised: the
        line waits, spending no retry, as it does for a busy tote. Only when no
        robot on the floor could ever reach any of them is the first free one
        asked for all the same, so the gate rejects it with its reason."""
```

**Replace in** `backend/operations/orders.py`:

```python
        return free[0] if free else None
```

with:

```python
        bodies = self._tote_bodies()
        ranked = [(rank, number, box) for number, box in enumerate(free)
                  for rank in (self._tote_rank(box, bodies),) if rank is not None]
        if ranked:
            return min(ranked, key=lambda entry: entry[:2])[2]
        if any(self._reaches(box, profile) for box in stocked for profile, _, _ in bodies):
            return None  # a robot that reaches one is busy, halted or unsupervised: wait for it
        return free[0] if free else None

    def _tote_bodies(self) -> List[Tuple[Any, bool, bool]]:
        """(profile, able now, needs a supervisor) for every robot on the
        floor whose body can do TOTE_TO_STATION. Able now: fit for the job
        (not halted, approved firmware, the job allowed) and, for a body that
        needs a supervisor, one is on shift."""
        classes = JOB_SPECS[TaskType.TOTE_TO_STATION].classes
        bodies = []
        for robot in self.twin.robots.values():
            profile = robot.mobility
            if profile is None or profile.embodiment_class not in classes:
                continue
            able = robot_eligibility(robot.status.value, robot.firmware_version, battery=None,
                                     task_type=TaskType.TOTE_TO_STATION.value,
                                     allowed_task_types=robot.allowed_task_types) is None \
                and people.supervision_available(self.twin, robot)[0]
            bodies.append((profile, able, bool(profile.supervision)))
        return bodies

    def _reaches(self, box: Any, profile: Any) -> bool:
        """Can this body take `box` from its slot: its kind, its declared
        weight, and the slot's level (the gate's capability rules)?"""
        slot = self.twin.warehouse.slot(box.slot)
        return (box_kind_ok(box.kind.value, profile.box_kinds)[0]
                and payload_ok(box.declared_weight_kg, profile.max_payload_kg)[0]
                and reach_ok(slot.level if slot is not None else None, profile.max_shelf_level)[0])

    def _tote_rank(self, box: Any, bodies: List[Tuple[Any, bool, bool]]) -> Optional[int]:
        """0 if a body that needs no supervisor (an AMR) able now reaches
        `box`, 1 if only a supervised one (the humanoid) does, None if no body
        able now reaches it."""
        supervised = [needs for profile, now, needs in bodies if now and self._reaches(box, profile)]
        if not supervised:
            return None
        return 1 if all(supervised) else 0
```


- [ ] **Step 4: A jam no one can clear waits silently after its retry**

The probe is a `Task` that is never registered, put through `TaskManager.validate` — exactly the gate's operator choice (credential, scope, on the floor, not walking, not busy), with no event, log file or stat.

**Replace in** `backend/human_jobs.py`:

```python
CLEAR_JAM job from ensure_jam_jobs, retried while no one qualified is on the
floor (spec §11.3).
```

with:

```python
CLEAR_JAM job from ensure_jam_jobs, asked for again once someone qualified is
on the floor (spec §11.3).
```

**Replace in** `backend/human_jobs.py`:

```python
from typing import Any, List, Optional
```

with:

```python
from typing import Any, Dict, List, Optional, Tuple
```

**Replace in** `backend/human_jobs.py`:

```python
from .models import CONFIG, Action, ActionType, Cell, LogCategory, OperatorStatus, TaskStatus, TaskType, cell_dict

HUMAN_JOBS = frozenset({TaskType.MANUAL_PICK, TaskType.CLEAR_JAM})
```

with:

```python
from .models import (CONFIG, Action, ActionType, Cell, LogCategory, OperatorStatus, Priority, TaskStatus, TaskType,
                     cell_dict)

HUMAN_JOBS = frozenset({TaskType.MANUAL_PICK, TaskType.CLEAR_JAM})

#: How many times a jam is asked for through the gate while no one qualified
#: is on the floor: the ask and, as for any rejected job, one retry (spec
#: §11.2). After that it waits until someone qualified is (ensure_jam_jobs).
JAM_GATE_ASKS = 2
```

**Replace in** `backend/human_jobs.py`:

```python
def ensure_jam_jobs(twin: Any) -> List[Any]:
    """Every jammed segment without a running CLEAR_JAM gets one. When the
    gate rejects it (no one qualified on the floor) it is tried again here
    next time, so the jam is cleared as soon as someone qualifies."""
    if twin.equipment is None:
        return []
```

with:

```python
def someone_can_clear(twin: Any, segment: str) -> Tuple[bool, Optional[str]]:
    """Would the gate find someone to clear the jam at `segment` now? It runs
    the gate's own check (TaskManager.validate) on a CLEAR_JAM that is never
    recorded: no task, event, log file or stat."""
    from .task_manager import Task  # deferred: task_manager imports this module

    probe = Task("CLEAR_JAM-check", TaskType.CLEAR_JAM, priority=Priority.HIGH, internal=True,
                 params={"segment": segment})
    return twin.tasks.validate(probe)


def ensure_jam_jobs(twin: Any, rejected: Optional[Dict[Tuple[Cell, int], int]] = None) -> List[Any]:
    """Every jammed segment without a running CLEAR_JAM gets one. While no
    one qualified is on the floor the gate rejects it, and each rejection is
    a FAILED job that records why; after JAM_GATE_ASKS of them (the ask and
    its one retry) the jam waits, logged once: each check asks silently
    whether someone qualified is on the floor (someone_can_clear) and asks
    the gate again only once someone is, so the jam is cleared as soon as
    someone qualifies without a FAILED job, log file and stat at every check.
    `rejected` ((cell, tick it jammed) -> the gate's rejections since it last
    had a job) is the caller's memory across checks: Simulator keeps one, and
    a new jam on the same cell starts afresh. Without it every check asks
    the gate."""
    if twin.equipment is None:
        return []
    asks = rejected if rejected is not None else {}
    jams = twin.equipment.conveyor.jams
    for key in [key for key in asks if jams.get(key[0]) != key[1]]:
        del asks[key]  # that jam is cleared
```

**Replace in** `backend/human_jobs.py`:

```python
    for cell in list(twin.equipment.conveyor.jams):
        segment = f"{cell[0]},{cell[1]}"
```

with:

```python
    for cell, since in list(jams.items()):
        segment, key = f"{cell[0]},{cell[1]}", (cell, since)
```

**Replace in** `backend/human_jobs.py`:

```python
        try:
            task = twin.tasks.create_task({"type": "CLEAR_JAM", "segment": segment, "priority": "HIGH"}, internal=True)
```

with:

```python
        try:
            if asks.get(key, 0) >= JAM_GATE_ASKS and not someone_can_clear(twin, segment)[0]:
                continue  # still no one qualified: it waits, and nothing is recorded
            task = twin.tasks.create_task({"type": "CLEAR_JAM", "segment": segment, "priority": "HIGH"}, internal=True)
```

**Replace in** `backend/human_jobs.py`:

```python
            twin.logger.warning(LogCategory.OPERATIONS, f"No one can clear the jam at {segment} yet: {task.error}",
                                position=cell_dict(cell))
```

with:

```python
            asks[key] = asks.get(key, 0) + 1
            waiting = " — waiting until someone qualified is on the floor" if asks[key] == JAM_GATE_ASKS else ""
            twin.logger.warning(LogCategory.OPERATIONS,
                                f"No one can clear the jam at {segment} yet: {task.error}{waiting}",
                                position=cell_dict(cell))
        else:
            asks.pop(key, None)
```


- [ ] **Step 5: The conveyor and sorter survive a box that vanished**

**Replace in** `backend/equipment.py`:

```python
        """`receiver` (an arm) takes the item on conveyor `cell`."""
        item = self.conveyor.unload(tuple(cell))
        box = self.twin.find_box(item.box_id)
```

with:

```python
        """`receiver` (an arm) takes the item on conveyor `cell`. An item whose
        box the twin no longer knows comes off the line all the same, freeing
        the cell, and the take raises ValueError: there is nothing to hand over."""
        item = self.conveyor.unload(tuple(cell))
        box = self.twin.find_box(item.box_id)
        if box is None:
            raise ValueError(f"the item on conveyor cell ({cell[0]},{cell[1]}) is no longer known ({item.box_id})")
```

**Replace in** `backend/equipment.py`:

```python
            box = twin.find_box(item.box_id)
            lane = box.destination if box.destination in sorter.lanes else sorter.lanes[0]
```

with:

```python
            box = twin.find_box(item.box_id)
            if box is None:  # removed from the twin while inside: there is nothing left to drop
                sorter.inside.remove(item)
                twin.logger.warning(LogCategory.OPERATIONS, f"{item.box_id} is no longer known — dropped from the sorter",
                                    data={"box_id": item.box_id, "order_id": item.order_id})
                continue
            lane = box.destination if box.destination in sorter.lanes else sorter.lanes[0]
```


- [ ] **Step 6: The simulator keeps the jam asks and contains the equipment's tick**

**Replace in** `backend/simulator.py`:

```python
        self._announced: Dict[str, Tuple[str, Cell, str]] = {}
```

with:

```python
        self._announced: Dict[str, Tuple[str, Cell, str]] = {}
        # (jammed conveyor cell, the tick it jammed) -> how often the gate has
        # rejected its CLEAR_JAM since it last had one (human_jobs.ensure_jam_jobs).
        self._jam_asks: Dict[Tuple[Cell, int], int] = {}
```

**Replace in** `backend/simulator.py`:

```python
                twin.equipment.tick()  # the conveyor and sorter move after robots place items
                if twin.tick_count % CONFIG["SHIFT_CHECK_EVERY_TICKS"] == 0:
                    human_jobs.ensure_jam_jobs(twin)  # a jam gets someone to clear it
```

with:

```python
                try:
                    twin.equipment.tick()  # the conveyor and sorter move after robots place items
                except Exception as exc:  # one bad item on the line must not stop the floor
                    twin.logger.error(LogCategory.OPERATIONS,
                                      f"The conveyor and sorter hit an error and skipped the rest of their turn: {exc}")
                if twin.tick_count % CONFIG["SHIFT_CHECK_EVERY_TICKS"] == 0:
                    human_jobs.ensure_jam_jobs(twin, self._jam_asks)  # a jam gets someone to clear it
```


- [ ] **Step 7: A misplaced box carries what it really holds**

`store` keeps zeroing the recorded slot's true quantity (the slot really is empty — `discrepancies()`, the stock API and Task 3's save test read that), and moves what the box held onto the box.

**Replace in** `backend/box.py`:

```python
        # record says (a wrong-level placement).
        self.slot = slot
        self.true_slot: Optional[str] = None
```

with:

```python
        # record says (a wrong-level placement), and `true_quantity` with it:
        # what it really holds, since its recorded slot's true quantity is 0.
        self.slot = slot
        self.true_slot: Optional[str] = None
        self.true_quantity: Optional[int] = None
```

**Replace in** `backend/box.py`:

```python
            "true_slot": self.true_slot,
            "order_id": self.order_id,
```

with:

```python
            "true_slot": self.true_slot,
            "true_quantity": self.true_quantity,
            "order_id": self.order_id,
```

**Replace in** `backend/box.py`:

```python
        box.true_slot = data.get("true_slot")
        box.assigned_robot = data.get("assigned_robot")
```

with:

```python
        box.true_slot = data.get("true_slot")
        box.true_quantity = data.get("true_quantity")
        box.assigned_robot = data.get("assigned_robot")
```

**Replace in** `backend/goods.py`:

```python
    the box remembers where it really is.
```

with:

```python
    the box remembers where it really is and what it really holds
    (Box.true_quantity), so a variance it carried goes with it. Stored again,
    a misplaced box's record takes back what it really holds.
```

**Replace in** `backend/goods.py`:

```python
            raise ValueError(f"Unknown slot {true_slot_id!r}")
    if current == slot_id:
```

with:

```python
            raise ValueError(f"Unknown slot {true_slot_id!r}")
    carried = box.true_quantity if box.true_slot else None  # a misplaced box: its record's true quantity is 0
    if current == slot_id:
```

**Replace in** `backend/goods.py`:

```python
    box.position = slot.cell
    box.true_slot = None
    if actual is not None:
```

with:

```python
    if carried is not None:
        location.true_qty = carried
    box.position = slot.cell
    box.true_slot = box.true_quantity = None
    if actual is not None:
        box.true_quantity = location.true_qty
```

**Replace in** `backend/goods.py`:

```python
    misplaced box that physically went there instead of its recorded slot."""
    location = twin.stock.location(slot_id)
    quantity = location.true_qty if location is not None else 0
    return quantity + sum(box.quantity for box in twin.boxes.values() if box.true_slot == slot_id)
```

with:

```python
    misplaced box that physically went there instead of its recorded slot —
    by what it really holds (Box.true_quantity), so a variance it carried is
    counted too; by its recorded quantity only when that isn't known."""
    location = twin.stock.location(slot_id)
    quantity = location.true_qty if location is not None else 0
    return quantity + sum(box.quantity if box.true_quantity is None else box.true_quantity
                          for box in twin.boxes.values() if box.true_slot == slot_id)
```


- [ ] **Step 8: Run the new tests**

Run: `.venv/bin/python -m pytest -o addopts="" -q backend/test_long_shift_orders.py`
Expected: `11 passed`.

- [ ] **Step 9: The fault-free shift still keeps completing customer orders**

Run: `.venv/bin/python -m pytest -o addopts="" -q backend/test_shift_soak.py`
Expected: `1 passed`. (Measured on that test's floor, 16 000 ticks at pace 2: 16 customer orders DONE with Tasks 1–6 and 16 with this task too, 12 of them by two thirds of the run both times — it asserts at least 12, and more in the last third.)

- [ ] **Step 10: Run the full suite**

Run: `.venv/bin/python -m pytest -o addopts="" -q`
Expected: `2 failed, 872 passed` (Task 6's count plus this task's 11 tests; the two failures are the allowed ones).

- [ ] **Step 11: Commit**

```bash
git add backend/operations/orders.py backend/human_jobs.py backend/equipment.py backend/simulator.py backend/box.py backend/goods.py backend/test_long_shift_orders.py
git commit -m "fix: orders and stock over a long shift — reachable totes, quiet jam waits, misplaced counts, contained equipment

A customer line takes a tote a robot can bring now (the AMRs before the
humanoid) and waits, spending no retry, while none can; a jam no one can
clear is asked for twice, then checked silently until someone qualifies;
a misplaced box is counted by what it really holds; a box gone from the
line or the sorter, or an error in the equipment's tick, no longer stops
the floor.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: People and drones over a long shift

The people and drone policies the plan 1b soak left for a long shift (spec §5.5, §6, §9.1, §10.1, §11.3, §11.4; plan 1b ruling 22 and the Task 12 ruling on the drone dead zone; the Task 13 minors; Plan ruling 12). **Dock charging.** A drone between BATTERY_LOW (20 %) and its count's energy plus the 25 % reserve could neither charge nor fly, so about a third of long-run counts failed. A drone idle on its pad now charges whenever it isn't full — with no job, so it can still be sent on a count — and once full it stays on charge on its pad (going IDLE at 100 % would drain at once and start a new session every other tick). A count no drone can fly names every drone's energy in full, not just the first three robots'. **Stepping aside.** The 30 s hold counted from when the walk to the refuge started, so a person stood there only ~5–17 s; it now counts from arrival. **Jordan.** Following the humanoid's *current* zone, Jordan always arrived after it had already stopped (SUPERVISOR_ABSENT for ~60 % of a long move). Heading straight for its *destination* zone deadlocks: a person walking is in no zone, and from a far destination he can't supervise it where it stopped (a probe left it stopped for good). So Jordan stays put while he still supervises it, and otherwise goes to the zone along its route that keeps it supervised farthest towards its destination (the destination's own zone once that is in reach). The soak also found a humanoid stopped for good on a walkable cell no zone covers (e.g. (3,11), between the charging station and dock_2): there is no zone to be near, so such a cell now counts as in the zones beside it. **Duty first.** `move_people` ran before `sync_duty`, so someone whose shift had ended was placed and taken off in one tick, and someone coming on waited a check; duty is now synced first. **Work orders.** A technician with no jam to clear goes to the robot whose open inventory work order names them, else to the first robot with one, else the workshop — never into a fenced pack cell, but beside it (Plan ruling 12); the inventory gains one cheap read of every open work order.

**Files:**
- Modify: `backend/simulator.py` (`_continue_idle_charge`; new `_docked`; `_auto_charge`)
- Modify: `backend/task_manager.py` (`_fleet_reason`)
- Modify: `backend/people.py` (new `zones_around`; `supervisor_nearby`)
- Modify: `backend/inventory/servicing.py` (new `ServicingMixin.open_work_orders`)
- Modify: `backend/operations/activities.py` (the docstring and imports; new `_route_ahead`, `supervisor_zone`, `open_work_orders`, `_work_order_zone`; `_role_target`; `move_people`)
- Test: `backend/test_long_shift_people.py` (create)

**Interfaces:**
- Consumes: `Simulator._on_charger`, `_recharge`; `people.supervisor_nearby`, `zones_touch`, `transit_ticks`; `human_jobs.exit_zone`; `ShiftEngine.yield_until` (saved by Task 4); `Robot.current_path`, `target_position`; `twin.inventory` (the `InventoryService`).
- Produces:
  - `Simulator._docked(robot) -> bool` — a drone on the ground on its pad. `_auto_charge` starts an idle charge (no task) for a docked drone below 100 %; `_continue_idle_charge` keeps a docked drone `CHARGING` at 100 % (one `ROBOT_CHARGED` when it gets there). No new saved state: the status is the marker.
  - `TaskManager._fleet_reason` names every robot of the job's own classes, then at most three in all (`"(and N more)"` for the rest).
  - `people.zones_around(warehouse, cell) -> List[Zone]` — the cell's zones, or for a cell no zone covers the zones of the cells beside it; `supervisor_nearby` uses it.
  - `InventoryService.open_work_orders() -> List[Dict[str, Any]]` — every OPEN `work_order` row, oldest first.
  - `activities.supervisor_zone(twin, humanoid, supervisor) -> Optional[str]`; `activities.open_work_orders(twin) -> List[Tuple[Robot, List[Dict[str, Any]]]]` (by robot name); `_role_target(engine, role, operator, work_orders)`; `move_people(engine)` calls `sync_duty(twin)` first. `HOME_ZONES`, `ROLES` and `YIELD_HOLD_S` are unchanged; `yield_until` keeps holding a simulation time (now arrival + `YIELD_HOLD_S`).

- [ ] **Step 1: Write the failing tests**

**Create** `backend/test_long_shift_people.py`:

```python
"""People and drones over a long shift (multi-embodiment spec §5.5, §6, §9.1,
§10.1, §11.3, §11.4): a drone idle on its pad tops up whenever it isn't full
and can still be sent on a count while it charges; a count no drone can fly
names every drone's energy; a person who steps aside for a forklift stands
in the refuge for the whole hold; Jordan heads where the humanoid is going;
duty is synced before anyone is moved; and a technician with no jam to clear
goes to a robot whose asset has an open work order."""
import pytest

from backend import people
from backend.digital_twin import DigitalTwin
from backend.models import CONFIG, OperatorStatus, RobotStatus, SimulationStatus, TaskStatus, TaskType
from backend.operations import move_people
from backend.operations.activities import YIELD_HOLD_S
from backend.operations.shift import DEFAULT_RATES
from backend.simulator import Simulator

CHECK = 20                                     # CONFIG["SHIFT_CHECK_EVERY_TICKS"]
WORKERS = {"Sam": "E-10001", "Noor": "E-10003", "Mateo": "E-10004", "Jordan": "E-10006"}


@pytest.fixture
def twin(tmp_path):
    return DigitalTwin(log_dir=str(tmp_path / "logs"), data_dir=str(tmp_path / "data"), persist_logs=False,
                       demo=True, demo_tasks=False, layout="distribution_center")


@pytest.fixture
def sim(twin):
    simulator = Simulator(twin)
    twin.simulation_status = SimulationStatus.RUNNING
    return simulator


def crew(twin, *names):
    return [twin.add_operator(name=name, worker_id=WORKERS[name]) for name in names]


def quiet_shift(twin):
    """The shift running — so the engine moves people — with nothing generated."""
    twin.shift.configure(rates={stream: 0.0 for stream in DEFAULT_RATES})
    twin.shift.start()


def events(twin, event_type, robot):
    return [e for e in twin.events.query(event_type=event_type) if e.get("robot_id") == robot.id]


# ---- drones top up on their pads -------------------------------------------- #
def test_a_drone_idle_on_its_pad_tops_up_whenever_it_is_not_full(twin, sim):
    drone = twin.add_robot(name="IX2-208", asset_id="AST-000208", position=(20, 2))
    drone.battery = 70.0                                             # well above BATTERY_LOW
    sim.tick()
    assert drone.status is RobotStatus.CHARGING and not twin.tasks.tasks   # on its pad: no job needed
    for _ in range(1000):
        if drone.battery >= 100.0:
            break
        sim.tick()
    assert drone.battery == 100.0
    for _ in range(300):
        sim.tick()
    # Docked and full it stays on charge: going IDLE at 100% would drain at once and
    # start a new session every other tick.
    assert drone.status is RobotStatus.CHARGING and drone.battery == 100.0
    assert len(events(twin, "ROBOT_CHARGING", drone)) == 1 and len(events(twin, "ROBOT_CHARGED", drone)) == 1
    assert drone.charging_sessions == 1


def test_a_full_drone_back_from_a_flight_charges_on_its_pad(twin, sim):
    drone = twin.add_robot(name="IX2-208", asset_id="AST-000208", position=(20, 2))
    task = twin.tasks.create_task({"type": "CYCLE_COUNT", "face": "12,2"})
    for _ in range(1500):
        if task.is_terminal:
            break
        sim.tick()
    assert task.status is TaskStatus.COMPLETED, task.error
    landed = drone.battery
    assert CONFIG["BATTERY_LOW"] < landed < 100.0                    # BATTERY_LOW alone would never charge it
    sim.tick()
    assert drone.status is RobotStatus.CHARGING
    for _ in range(20):
        sim.tick()
    assert drone.battery > landed


def test_a_charging_drone_with_the_energy_for_a_count_is_still_sent_on_it(twin, sim):
    drone = twin.add_robot(name="IX2-208", asset_id="AST-000208", position=(20, 2))
    drone.battery = 80.0
    sim.tick()
    assert drone.status is RobotStatus.CHARGING
    task = twin.tasks.create_task({"type": "CYCLE_COUNT", "face": "12,2"})
    assert task.status is TaskStatus.PLANNING, task.error
    sim.tick()
    # Dock charging through a job of its own would leave the drone busy, and the
    # count waiting (or rejected) until it was full.
    assert task.robot_id == drone.id and drone.current_task == task.id
    for _ in range(200):
        if drone.layer == "AIR":
            break
        sim.tick()
    assert drone.layer == "AIR" and drone.status is not RobotStatus.CHARGING
    flying = drone.battery
    for _ in range(20):
        sim.tick()
    assert drone.battery < flying                                    # no longer charging


def test_a_count_no_drone_can_fly_names_every_drones_energy(twin):
    for name, asset, cell in (("TR50-201", "AST-000201", (8, 13)), ("TR50-101", "AST-000101", (8, 15)),
                              ("PF1200-205", "AST-000205", (7, 4)), ("SC1-204", "AST-000204", (7, 6))):
        twin.add_robot(name=name, asset_id=asset, position=cell)
    drones = [twin.add_robot(name="IX2-208", asset_id="AST-000208", position=(20, 2)),
              twin.add_robot(name="IX2-209", asset_id="AST-000209", position=(21, 2)),
              twin.add_robot(name="Drone-3", model_code="CT-IX2", position=(19, 2)),
              twin.add_robot(name="Drone-4", model_code="CT-IX2", position=(19, 1))]
    for drone in drones:
        drone.battery = 10.0
    task = twin.tasks.create_task({"type": "CYCLE_COUNT", "face": "12,2"})
    assert task.status is TaskStatus.FAILED
    # Cut after three reasons, the fourth drone's energy hides behind "(and N more)".
    for drone in drones:
        assert f"{drone.name} can't fly it: has 15.0 Wh but the round trip needs" in task.error, task.error
    assert task.error.endswith("(and 4 more)")


# ---- a person stepping aside stands clear for the whole hold ---------------- #
def test_a_person_who_steps_aside_stands_in_the_refuge_for_the_whole_hold(twin, sim):
    (sam,) = crew(twin, "Sam")
    people.place(twin, sam, "intake_staging")
    forklift = twin.add_robot(name="PF1200-205", asset_id="AST-000205", position=(7, 4))
    quiet_shift(twin)
    twin.tasks.create_task({"type": "MOVE_ROBOT", "robot_id": forklift.id, "destination": "5,4"})
    arrived = left = None
    for _ in range(2000):
        sim.tick()
        if arrived is None and sam.zone not in (None, "intake_staging") and not sam.in_transit:
            arrived = twin.tick_count                                # standing in the refuge
        elif arrived is not None and sam.in_transit:
            left = twin.tick_count                                   # setting off back
            break
    assert arrived is not None and left is not None and sam.transit_to == "intake_staging"
    stood = (left - arrived) * sim.dt
    # Counting the hold from the start of the walk left ~12 s standing here.
    assert YIELD_HOLD_S <= stood <= YIELD_HOLD_S + CHECK * sim.dt


# ---- Jordan heads where the humanoid is going ------------------------------- #
def test_jordan_heads_where_the_humanoid_is_going_not_where_it_is(twin, sim):
    humanoid = twin.add_robot(name="H1-212", asset_id="AST-000212", position=(22, 7))
    (jordan,) = crew(twin, "Jordan")
    people.place(twin, jordan, "returns_qc")
    quiet_shift(twin)
    task = twin.tasks.create_task({"type": "MOVE_ROBOT", "robot_id": humanoid.id, "destination": "12,13"})
    walks = []
    for _ in range(2000):
        if task.is_terminal:
            break
        sim.tick()
        if jordan.in_transit and (not walks or walks[-1] != jordan.transit_to):
            walks.append(jordan.transit_to)
    assert task.status is TaskStatus.COMPLETED, task.error
    # Following the zone it is in, Jordan walked ne_floor, cross_aisle, top_aisle,
    # tote_aisle_1 and the move took 337 ticks, stopped for 202 of them.
    assert walks == ["cross_aisle", "tote_aisle_1"]
    assert twin.tick_count < 300


def test_a_humanoid_on_a_cell_no_zone_covers_is_supervised_from_beside_it(twin, sim):
    # (3,11) lies between the charging station and dock_2, in no zone; with no zone
    # to be near, a humanoid driving over it stopped there for good, Jordan beside it.
    assert not twin.warehouse.zones_of_cell((3, 11))
    humanoid = twin.add_robot(name="H1-212", asset_id="AST-000212", position=(3, 13))
    (jordan,) = crew(twin, "Jordan")
    people.place(twin, jordan, "charging_station")
    humanoid.position = (3, 11)
    assert people.supervision_status(twin, humanoid)[0]
    quiet_shift(twin)
    task = twin.tasks.create_task({"type": "MOVE_ROBOT", "robot_id": humanoid.id, "destination": "5,11"})
    for _ in range(600):
        if task.is_terminal:
            break
        sim.tick()
    assert task.status is TaskStatus.COMPLETED, (task.error, humanoid.wait_reason)


def test_jordan_stays_put_while_he_still_supervises_the_humanoid(twin):
    humanoid = twin.add_robot(name="H1-212", asset_id="AST-000212", position=(22, 7))
    (jordan,) = crew(twin, "Jordan")
    people.place(twin, jordan, "ne_floor")                           # shares an edge with returns_qc
    assert people.supervision_status(twin, humanoid)[0]
    move_people(twin.shift)
    assert jordan.zone == "ne_floor" and not jordan.in_transit       # a walk would only stop it


# ---- duty is synced before anyone is moved ---------------------------------- #
def test_someone_off_duty_is_never_placed_by_the_engine(twin):
    (sam,) = crew(twin, "Sam")
    twin.set_operator_shift(sam.id, 6, 7)
    assert sam.status is OperatorStatus.AVAILABLE and sam.zone is None
    twin.simulation_time = 3600.0                                    # 07:00: his shift is over
    move_people(twin.shift)
    assert sam.status is OperatorStatus.OFF_DUTY and sam.zone is None   # placed, then taken off, before


def test_someone_coming_on_duty_is_placed_at_once(twin):
    (sam,) = crew(twin, "Sam")
    twin.set_operator_shift(sam.id, 7, 15)
    move_people(twin.shift)
    assert sam.status is OperatorStatus.OFF_DUTY and sam.zone is None    # 06:00: not yet
    twin.simulation_time = 3600.0                                    # 07:00
    move_people(twin.shift)
    assert sam.status is OperatorStatus.AVAILABLE and sam.zone == "intake_staging"   # a check later, before


# ---- technicians and work orders -------------------------------------------- #
def test_the_inventory_lists_the_open_work_orders_in_one_read(twin):
    seeded = [wo["asset_id"] for wo in twin.inventory.open_work_orders()]
    assert seeded == ["AST-000103"]                                  # the demo seed's lidar replacement
    change = twin.inventory.open_work_order("AST-000201", "INSPECTION", "Check the lidar")
    assert [wo["asset_id"] for wo in twin.inventory.open_work_orders()] == ["AST-000103", "AST-000201"]
    twin.inventory.close_work_order(change["subject_id"], "Lidar cleaned")
    assert [wo["asset_id"] for wo in twin.inventory.open_work_orders()] == ["AST-000103"]


def test_a_technician_goes_to_a_robot_with_an_open_work_order(twin):
    amr = twin.add_robot(name="TR50-201", asset_id="AST-000201", position=(12, 13))
    noor, mateo = crew(twin, "Noor", "Mateo")
    move_people(twin.shift)
    assert (noor.zone, mateo.zone) == ("workshop", "workshop")
    change = twin.fleet.mutate(twin.inventory.open_work_order, amr.asset_id, "INSPECTION", "Check the lidar")
    move_people(twin.shift)
    assert (noor.transit_to, mateo.transit_to) == ("tote_aisle_1", "tote_aisle_1")
    for operator in (noor, mateo):
        people.place(twin, operator, operator.transit_to)
    twin.fleet.mutate(twin.inventory.close_work_order, change["subject_id"], "Lidar cleaned")
    move_people(twin.shift)
    assert (noor.transit_to, mateo.transit_to) == ("workshop", "workshop")


def test_a_technician_goes_first_to_the_work_order_that_names_them(twin):
    near = twin.add_robot(name="TR50-201", asset_id="AST-000201", position=(12, 13))
    parked = twin.add_robot(name="TR50-101", asset_id="AST-000101", position=(5, 13))
    noor, mateo = crew(twin, "Noor", "Mateo")
    move_people(twin.shift)
    twin.fleet.mutate(twin.inventory.open_work_order, parked.asset_id, "INSPECTION", "Check the bumper")
    twin.fleet.mutate(twin.inventory.open_work_order, near.asset_id, "INSPECTION", "Check the lidar",
                      technician_id=mateo.worker_id)
    move_people(twin.shift)
    assert mateo.transit_to == "tote_aisle_1"                        # his own, though TR50-101 sorts first
    assert noor.transit_to == "parking_area"                         # none of hers: the first robot by name


def test_a_work_order_inside_a_pack_cell_is_worked_from_beside_it(twin):
    arm = twin.add_robot(name="CX10-210", asset_id="AST-000210")
    (noor,) = crew(twin, "Noor")
    move_people(twin.shift)
    twin.fleet.mutate(twin.inventory.open_work_order, arm.asset_id, "INSPECTION", "Check the gripper")
    move_people(twin.shift)
    # Standing in the fenced cell would hold its arm (PERSON_IN_CELL) for as long as the order is open.
    assert noor.transit_to == "pick_station_1"
```


- [ ] **Step 2: Run them to see them fail**

Run: `.venv/bin/python -m pytest -o addopts="" -q backend/test_long_shift_people.py`
Expected: `14 failed`. The drone tests fail on `IDLE is CHARGING` (a drone above BATTERY_LOW never charges on its pad) and on the fourth drone missing from `... (and 5 more)`; the step-aside test on `30.0 <= 12.45`; the Jordan tests on `['ne_floor', ...]`, on Jordan walking while he supervises, and on `assert False` for the humanoid on (3,11); the duty tests on `AVAILABLE is OFF_DUTY`; the work-order tests on `AttributeError: 'InventoryService' object has no attribute 'open_work_orders'` and on technicians who stay in the workshop (`None == 'tote_aisle_1'`).

- [ ] **Step 3: A drone idle on its pad tops up**

**Replace in** `backend/simulator.py`:

```python
    def _continue_idle_charge(self, robot: Any) -> None:
        self._recharge(robot)
        if robot.battery >= 100.0:
```

with:

```python
    def _continue_idle_charge(self, robot: Any) -> None:
        before = robot.battery
        self._recharge(robot)
        if robot.battery >= 100.0 and self._docked(robot):
            # A drone stays on its pad's charge while it waits there: going IDLE
            # would drain it at once and start a new session every other tick.
            robot.low_battery_warned = False
            if before < 100.0:
                self.twin.events.emit(
                    EventType.ROBOT_CHARGED,
                    f"{robot.name} fully charged (it stays on charge on its pad)",
                    category=LogCategory.BATTERY,
                    robot_id=robot.id,
                )
            return
        if robot.battery >= 100.0:
```

**Replace in** `backend/simulator.py`:

```python
    def _auto_charge(self) -> None:
        """Idle robots with a low battery take themselves to the charger — a
        drone to its pad. A mains-powered arm never needs to. A drone left idle
        in the air (its job failed or was cancelled mid-flight) flies home
        whatever its battery, so its next flight can take off. On the new floor
        a robot asks at most every SHIFT_CHECK_EVERY_TICKS, so a charger it
        can't reach right now doesn't get one failing request per tick."""
```

with:

```python
    def _docked(self, robot: Any) -> bool:
        """A drone on the ground on its pad: it can charge there."""
        return robot.mobility is not None and robot.mobility.is_air and self._on_charger(robot)

    def _auto_charge(self) -> None:
        """Idle robots with a low battery take themselves to the charger — a
        drone to its pad. A drone idle on its pad charges there whenever it
        isn't full (dock charging), so it is ready for the next count's energy
        gate; charging with no job, it can still be sent on one. A
        mains-powered arm never needs to charge. A drone left idle in the air
        (its job failed or was cancelled mid-flight) flies home whatever its
        battery, so its next flight can take off. On the new floor a robot
        asks at most every SHIFT_CHECK_EVERY_TICKS, so a charger it can't
        reach right now doesn't get one failing request per tick."""
```

**Replace in** `backend/simulator.py`:

```python
            if robot.status == RobotStatus.CHARGING or (robot.battery > CONFIG["BATTERY_LOW"] and not adrift):
```

with:

```python
            topping_up = self._docked(robot) and robot.battery < 100.0
            if robot.status == RobotStatus.CHARGING or (robot.battery > CONFIG["BATTERY_LOW"] and not adrift
                                                        and not topping_up):
```


- [ ] **Step 4: A count no drone can fly names every drone**

**Replace in** `backend/task_manager.py`:

```python
        # The bodies the job is for come first, so their reasons (a drone's
        # energy, say) aren't the ones cut off by the "and N more" below.
        robots = sorted(twin.robots.values(), key=lambda robot: not (
            robot.mobility is not None and robot.mobility.embodiment_class in classes))
```

with:

```python
        # The bodies the job is for come first, and every one of them is named
        # in full, so their reasons (a drone's energy, say) are never the ones
        # cut off by the "and N more" below.
        robots = sorted(twin.robots.values(), key=lambda robot: not (
            robot.mobility is not None and robot.mobility.embodiment_class in classes))
        own = sum(1 for robot in robots if robot.mobility is not None and robot.mobility.embodiment_class in classes)
```

**Replace in** `backend/task_manager.py`:

```python
        shown = "; ".join(reasons[:3]) + (f" (and {len(reasons) - 3} more)" if len(reasons) > 3 else "")
```

with:

```python
        keep = max(3, own)
        shown = "; ".join(reasons[:keep]) + (f" (and {len(reasons) - keep} more)" if len(reasons) > keep else "")
```


- [ ] **Step 5: A cell no zone covers is near the zones beside it**

**Replace in** `backend/people.py`:

```python
def supervisor_nearby(twin: Any, robot_cell: Cell, supervisor: Any) -> bool:
    """The proximity half of the humanoid supervision rule (spec §6): the
    supervisor stands in a zone that holds `robot_cell`, or one sharing an
    edge with such a zone. Off the floor or in transit is never nearby."""
```

with:

```python
def zones_around(warehouse: Any, cell: Cell) -> List[Any]:
    """The zones `cell` is in, for "who is near it". A cell no zone covers (a
    strip between two zones) counts as in the zones of the cells beside it,
    so a robot crossing it is still near someone."""
    zones = warehouse.zones_of_cell(cell)
    if zones:
        return zones
    beside = {}
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        for zone in warehouse.zones_of_cell((cell[0] + dx, cell[1] + dy)):
            beside.setdefault(zone.key, zone)
    return list(beside.values())


def supervisor_nearby(twin: Any, robot_cell: Cell, supervisor: Any) -> bool:
    """The proximity half of the humanoid supervision rule (spec §6): the
    supervisor stands in a zone that holds `robot_cell` (zones_around: the
    zones beside a cell no zone covers), or one sharing an edge with such a
    zone. Off the floor or in transit is never nearby."""
```

**Replace in** `backend/people.py`:

```python
               for zone in twin.warehouse.zones_of_cell(robot_cell))
```

with:

```python
               for zone in zones_around(twin.warehouse, robot_cell))
```


- [ ] **Step 6: One read of the open work orders**

**Replace in** `backend/inventory/servicing.py`:

```python
from typing import Any, Dict, Optional
```

with:

```python
from typing import Any, Dict, List, Optional
```

**Replace in** `backend/inventory/servicing.py`:

```python
class ServicingMixin:
    def open_work_order(self, asset_id: str, wo_type: str, description: str,
```

with:

```python
class ServicingMixin:
    def open_work_orders(self) -> List[Dict[str, Any]]:
        """Every work order still open, on any asset, oldest first: one read
        for the floor's technicians (a robot's own are in get_robot)."""
        return self.store.select("work_order", "status = 'OPEN'", order="opened_at, rowid")

    def open_work_order(self, asset_id: str, wo_type: str, description: str,
```


- [ ] **Step 7: Jordan heads where the humanoid is going; technicians go to work orders; the hold counts from arrival; duty first**

The early-arrival rule in the hold (someone put in the refuge before the walk would have ended) keeps the hold 30 s from when they are there; it is what `test_activities.py::test_a_person_steps_aside_for_a_waiting_forklift` does with `settle()`.

**Replace in** `backend/operations/activities.py`:

```python
Ana and Kai go to the dock a truck is at; Jordan follows the humanoid;
Noor and Mateo keep to the workshop (their jobs take them to jams); Riley
stays outside the pack cells. Everyone takes a 15-minute break in sw_floor
every 2 sim-hours, staggered by 12 minutes per person — the first after two
hours on shift. A person standing in a zone a forklift or hauler is waiting
to enter (PERSON_IN_AISLE) steps aside to the nearest zone those bodies
can't drive in, and stays there YIELD_HOLD_S before going back.
```

with:

```python
Ana and Kai go to the dock a truck is at; Jordan follows the humanoid,
heading where it is going (supervisor_zone); Noor and Mateo go to a robot
whose asset has an open work order, else keep to the workshop (their jobs
take them to jams); Riley stays outside the pack cells. Everyone takes a
15-minute break in sw_floor every 2 sim-hours, staggered by 12 minutes per
person — the first after two hours on shift. A person standing in a zone a
forklift or hauler is waiting to enter (PERSON_IN_AISLE) steps aside to the
nearest zone those bodies can't drive in, and stands there YIELD_HOLD_S
before going back.
```

**Replace in** `backend/operations/activities.py`:

```python
duty whatever the clock says. The classic floor keeps its wall-clock shifts.
```

with:

```python
duty whatever the clock says. The engine syncs duty before it moves anyone.
The classic floor keeps its wall-clock shifts.
```

**Replace in** `backend/operations/activities.py`:

```python
from typing import Any, Dict, Optional

from .. import people
from ..layouts.base import WIDE
from ..models import CellType, EventType, LogCategory, OperatorStatus
```

with:

```python
from typing import Any, Dict, List, Optional, Tuple

from .. import human_jobs, people
from ..layouts.base import WIDE
from ..models import CONFIG, ActionType, Cell, CellType, EventType, LogCategory, OperatorStatus
```

**Replace in** `backend/operations/activities.py`:

```python
def _role_target(engine: Any, role: str) -> Optional[str]:
```

with:

```python
def _route_ahead(twin: Any, robot: Any) -> List[Cell]:
    """The cells `robot`'s current NAVIGATE still has to cross, its target
    last; [] while it isn't driving one."""
    task = twin.tasks.get(robot.current_task) if robot.current_task else None
    action = task.current_action if task is not None and not task.is_terminal else None
    if action is None or action.type is not ActionType.NAVIGATE or action.target is None:
        return []
    target = tuple(action.target)
    path = [tuple(cell) for cell in robot.current_path] if robot.target_position == target else []
    return path if path and path[-1] == target else path + [target]


def supervisor_zone(twin: Any, humanoid: Any, supervisor: Any) -> Optional[str]:
    """Where the humanoid's supervisor should stand (spec §6, §11.3). While
    he supervises it from where he stands, there: any walk stops it until he
    arrives, since a person walking is in no zone. Otherwise, with it driving
    somewhere, the zone along its route that keeps it supervised the farthest
    towards its destination — the destination's own zone once that is in
    reach, the nearest walk among equals; one out of reach would leave it
    stopped where it is for good. With it standing still, its own zone."""
    warehouse = twin.warehouse
    if supervisor.zone is not None and people.supervisor_nearby(twin, humanoid.position, supervisor):
        return supervisor.zone
    route = _route_ahead(twin, humanoid)
    places = [zone for zone in people.zones_around(warehouse, humanoid.position) if "route" not in zone.attributes]
    here = warehouse.zone_of_cell(humanoid.position) or min(places, key=lambda zone: len(zone.cells), default=None)
    if not route:
        return here.key if here is not None else None
    touching: Dict[Tuple[str, Cell], bool] = {}

    def watches(key: str, cell: Cell) -> bool:  # would someone standing in zone `key` supervise it at `cell`?
        if (key, cell) not in touching:
            touching[key, cell] = any(people.zones_touch(warehouse, key, zone.key)
                                      for zone in people.zones_around(warehouse, cell))
        return touching[key, cell]

    def reach(key: str) -> int:  # how many of the route's cells it drives supervised from zone `key`
        for number, cell in enumerate(route):
            if not watches(key, cell):
                return number
        return len(route)

    candidates: List[str] = []
    for cell in [humanoid.position] + route:
        zone = warehouse.zone_of_cell(cell)
        if (zone is None or zone.key in candidates or zone.cell_type in _NO_STANDING
                or zone.attributes.get("fenced") or not watches(zone.key, humanoid.position)):
            continue
        candidates.append(zone.key)
    if not candidates:
        return here.key if here is not None else None
    start = warehouse.zones[supervisor.zone].center if supervisor.zone is not None else humanoid.position
    return max(candidates, key=lambda key: (reach(key), -(abs(warehouse.zones[key].center[0] - start[0])
                                                          + abs(warehouse.zones[key].center[1] - start[1]))))


def open_work_orders(twin: Any) -> List[Tuple[Any, List[Dict[str, Any]]]]:
    """(robot, its asset's open work orders) for every robot on the floor
    that has any, by robot name — one read of the fleet inventory. If the
    inventory can't answer, there are none: it sends no one anywhere."""
    by_asset: Dict[str, List[Dict[str, Any]]] = {}
    try:
        for work_order in twin.inventory.open_work_orders():
            by_asset.setdefault(work_order["asset_id"], []).append(work_order)
    except Exception:  # noqa: BLE001 - an inventory failure must not stop the shift
        return []
    return [(robot, by_asset[robot.asset_id]) for robot in sorted(twin.robots.values(), key=lambda r: r.name)
            if robot.asset_id in by_asset]


def _work_order_zone(twin: Any, technician: Any, found: List[Tuple[Any, List[Dict[str, Any]]]]) -> Optional[str]:
    """A technician's work order (Plan ruling 12), from open_work_orders: the
    zone of the robot whose open work order names them, else of the first
    robot with one; never a fenced pack cell (an arm stops while anyone is in
    it), but the zone beside it. None with no work order open."""
    robot = next((robot for robot, orders in found
                  if any(wo.get("technician_id") == technician.worker_id for wo in orders)), None)
    if robot is None and found:
        robot = found[0][0]
    zone = twin.warehouse.zone_of_cell(robot.position) if robot is not None else None
    return human_jobs.exit_zone(twin, zone.key) if zone is not None else None


def _role_target(engine: Any, role: str, operator: Any,
                 work_orders: List[Tuple[Any, List[Dict[str, Any]]]]) -> Optional[str]:
```

**Replace in** `backend/operations/activities.py`:

```python
        zone = twin.warehouse.zone_of_cell(humanoid.position) if humanoid is not None else None
        return zone.key if zone is not None else HOME_ZONES[role]
```

with:

```python
        zone = supervisor_zone(twin, humanoid, operator) if humanoid is not None else None
        return zone or HOME_ZONES[role]
    if role == "technician":
        return _work_order_zone(twin, operator, work_orders) or HOME_ZONES[role]
```

**Replace in** `backend/operations/activities.py`:

```python
    says they should be (ShiftEngine.tick, while the shift runs)."""
    twin = engine.twin
```

with:

```python
    says they should be (ShiftEngine.tick, while the shift runs). Duty is
    synced first, so no one is placed and then taken off the floor in one
    check, and no one coming on duty waits a check to be placed."""
    twin = engine.twin
    sync_duty(twin)
```

**Replace in** `backend/operations/activities.py`:

```python
    slots = sorted(ROLES)
    for operator in sorted(twin.operators.values(), key=lambda o: o.id):
```

with:

```python
    slots = sorted(ROLES)
    work_orders = open_work_orders(twin)
    for operator in sorted(twin.operators.values(), key=lambda o: o.id):
```

**Replace in** `backend/operations/activities.py`:

```python
            engine.yield_until[operator.id] = now + YIELD_HOLD_S
        elif hold is not None and now < hold:
```

with:

```python
            # The hold is YIELD_HOLD_S standing in the refuge: it counts from arrival.
            walk = people.transit_ticks(twin.warehouse, operator.zone, target) * CONFIG["TICK_DT"] \
                if target != operator.zone else 0.0
            engine.yield_until[operator.id] = now + walk + YIELD_HOLD_S
        elif hold is not None and now < hold:
            if now < hold - YIELD_HOLD_S:  # there before the walk would end (put there): it counts from now
                engine.yield_until[operator.id] = now + YIELD_HOLD_S
```

**Replace in** `backend/operations/activities.py`:

```python
            target = _role_target(engine, role)
```

with:

```python
            target = _role_target(engine, role, operator, work_orders)
```


- [ ] **Step 8: Run the new tests**

Run: `.venv/bin/python -m pytest -o addopts="" -q backend/test_long_shift_people.py`
Expected: `14 passed`.

- [ ] **Step 9: The fault-free shift still keeps completing customer orders**

Run: `.venv/bin/python -m pytest -o addopts="" -q backend/test_shift_soak.py`
Expected: `1 passed`. (Measured on that test's floor, 16 000 ticks at pace 2, with Tasks 1–7: 16 customer orders DONE, 12 by two thirds, 1 cycle count FAILED on drone energy, 124 SUPERVISOR_ABSENT waits; with this task: 17 DONE, 11 by two thirds, no count failed, 52 waits.)

- [ ] **Step 10: Run the full suite**

Run: `.venv/bin/python -m pytest -o addopts="" -q`
Expected: `2 failed, 886 passed` (Task 7's count plus this task's 14 tests; the two failures are the allowed ones).

- [ ] **Step 11: Commit**

```bash
git add backend/simulator.py backend/task_manager.py backend/people.py backend/inventory/servicing.py backend/operations/activities.py backend/test_long_shift_people.py
git commit -m "fix: people and drones over a long shift — dock charging, the hold from arrival, Jordan ahead, duty first, work orders

A drone idle on its pad charges whenever it isn't full and can still be
sent on a count; a count no drone can fly names every drone's energy; a
person stepping aside stands in the refuge for the whole hold; Jordan stays
put while he supervises the humanoid, else heads for the zone that keeps it
supervised farthest towards its destination; a cell no zone covers is near
the zones beside it; duty is synced before anyone is moved; a technician
with no jam goes to a robot with an open work order.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: The dashboard floor — layout-driven cells, glyphs, air layer, arms, people, conveyor

The dashboard learns to draw the distribution-centre floor (spec §12 Floor, Robots, Air layer, Arms, People, Conveyor). The pure, DOM-free part goes in a new `frontend/floor_model.js`, exposed as `window.FloorModel` (and as a global when there is no `window`, so JavaScriptCore can run its tests): the cell fills and the legend from the snapshot's `cell_types` table (Task 5), each robot type's letter and glyph, which robots are on the air layer, an arm's station and reach, where each person is drawn, and the conveyor's items and jams. `frontend/app.js` draws from it. The floor's fills, hatching and legend come from `snapshot.cell_types` instead of `COLORS`/`LEGEND`/`cellFill`; the palette moves into the floor model unchanged (its tests pin every colour the classic floor uses), so the classic floor looks the same — it was checked call for call against the old drawing. Robots with a floor profile get their glyph and letter (A AMR, F forklift, H hauler, K picker, S scout, D drone, R arm, U humanoid; Plan ruling 10: classic robots draw exactly as today), a kind badge on the box they carry, and an amber ring while `wait_reason` is set. Drones draw last, above the people, and an "Air layer" switch (shown only on a floor with drones or no-fly cells) hides them, their routes and the no-fly overlay. Arms show their reachable cells (every cell some point of which is within `reach_mm` of the base, and always the conveyor cell they work) and their pack cell framed cyan, or amber while paused. People are small dots with initials in their zone, spread out from its centre; a person in transit stands on the walkway cell nearest the middle of the walk. Conveyor items glide along the line and a jammed cell is red. A shipped box (it left on a truck) is no longer drawn. The tests run the floor model against real `/api/state` snapshots of both floors, built through the public API.

**Files:**
- Create: `frontend/floor_model.js`
- Modify: `frontend/app.js` (state, palette, `cellFill`, `drawFloor`, the new `drawNoFly`/`drawConveyor`/`drawArms`/`drawPeople`, `drawRoutes`, `drawBoxes`, `drawRobots`, `draw`, `renderLegend`, `applyState`, `flushDom`, `wire`)
- Modify: `frontend/index.html` (the "Air layer" switch; `floor_model.js` loads before `app.js`)
- Modify: `frontend/style.css` (the switch)
- Test: `frontend/tests/floor_model_test.js` (create), `backend/test_floor_model_js.py` (create)

**Interfaces:**
- Consumes (Task 5): `snapshot.cell_types` (`[{"type", "label", "role", "legend"}]`) and `snapshot.no_fly_cells` (`[{"x", "y"}]`), both only with `include_layout`; `snapshot.equipment` (`Equipment.view()`, None on classic) — the floor model reads its `to_dict()` part: `conveyor.items[*].cell`/`box_id`, `conveyor.jams[*].cell`, `arm_cells`. Plan 1a/1b shapes: robot `mobility` (`embodiment_class`, `movement`, `reach_mm`), `layer`, `altitude_m`, `wait_reason`, `carrying_box`; box `kind`, `status`; operator `zone`, `transit_to`; zone `attributes.arm_cell`, `attributes.route`. The test builds its floors with `DigitalTwin(...)`, `add_robot`, `set_robot_layer`, `add_operator`, `people.place`, `people.start_transit`, `add_box`, `equipment.jam`.
- Produces: `window.FloorModel` (pure ES5, no DOM):
  - `PALETTE` (every colour `app.js` draws with; `COLORS` is now `FloorModel.PALETTE`), `LETTERS`, `CELL_SIZE_M` (1.5).
  - `cellFills(cellTypes, colors) -> {type: colour}`, `cellRoles(cellTypes) -> {type: role}`, `legendItems(cellTypes, colors) -> [[label, colour]]` (the `legend` rows, then `["Route", colors.amber]`), `cellPattern(role) -> "rack" | "hazard" | null`, `zoneFrames(zones) -> [{key, label, x0, y0, x1, y1, outline, restricted, vertical}]`, `noFlyCells(value) -> [{x, y}]`, `toCell(value) -> {x, y} | null`.
  - `robotLetter(robot) -> string | null` (null without a floor profile), `robotGlyph(robot) -> "square" | "forklift" | "wide" | "diamond" | "rotor" | "arm" | "round"`, `isDrone(robot)`, `robotLayers(robots, showAir) -> {ground, air}`, `kindLetter(kind)`, `kindBadge(robot, box) -> string | null`.
  - `armView(robot, equipment, layout) -> {station, reach: [{x, y}], workCell, paused, reason} | null`.
  - `initials(name)`, `peopleDots(operators, zones, walkwayCells) -> [{id, initials, x, y, inTransit, zone, to}]`.
  - `conveyorView(equipment) -> {items: [{x, y, boxId}], jammed: [{x, y}]}`.
  - In `app.js`: `state.cellTypes`, `state.noFly`, `state.equipment`, `showAir`, `findBox(id)`, `layoutChanged()`, `drawRobots(robots)` (it takes the list to draw), `renderAirToggle()`.

- [ ] **Step 1: Write the failing tests**

The JS tests run under `jsc` with a fixtures file the pytest wrapper writes from real snapshots: `jsc <fixtures.js> frontend/floor_model.js frontend/tests/floor_model_test.js`. `jsc`'s `quit(code)` doesn't set the exit status, so a failing run throws at the end, which does.

**Create** `frontend/tests/floor_model_test.js`:

```javascript
/* Unit tests for frontend/floor_model.js (multi-embodiment spec §12 Floor,
   Robots, Air layer, Arms, People, Conveyor). backend/test_floor_model_js.py
   runs them:  jsc <fixtures.js> frontend/floor_model.js <this file>
   where FIXTURES = {classic, dc} holds real /api/state snapshots of both
   floors. Prints one line per failure, then "N passed, M failed"; any
   failure ends in an exception, so jsc exits non-zero. Plain ES5. */
var passed = 0, failed = 0;

function test(name, body) {
  try {
    body();
    passed++;
  } catch (error) {
    failed++;
    print("FAIL " + name + ": " + error.message);
  }
}

function eq(actual, expected, what) {
  var a = JSON.stringify(actual), b = JSON.stringify(expected);
  if (a !== b) throw new Error((what ? what + ": " : "") + "expected " + b + ", got " + a);
}

function ok(value, what) {
  if (!value) throw new Error(what || "expected a true value");
}

function clone(value) { return JSON.parse(JSON.stringify(value)); }

function named(list, name) {
  for (var i = 0; i < list.length; i++) if (list[i].name === name) return list[i];
  throw new Error("nothing named " + name);
}

function cells(list) {
  return list.map(function (c) { return c.x + "," + c.y; });
}

var FM = FloorModel;
var classic = FIXTURES.classic, dc = FIXTURES.dc;

/* The dashboard's colours, legend and cell fills before plan 1c (frontend/
   app.js COLORS, LEGEND and cellFill): the classic floor must look the same. */
var OLD_COLORS = {
  floor: "#0e1417", grid: "#182126", wall: "#2c3a41", shelf: "#243138", shelfLine: "#33454e",
  storage: "#1d2a30", charging: "#16302f", loading: "#2c2716", unloading: "#252c1a",
  packing: "#231d2e", parking: "#1b2429", restricted: "#2b1e14", amber: "#ffb300",
  cyan: "#31d1c4", ok: "#7ddf64", warn: "#ff9538", fault: "#ff5252", ink: "#e7eef1",
  muted: "#7b8d95", faint: "#4d5f67", box: "#c98b3a", boxCarried: "#ffb300", boxDelivered: "#5f8f4f"
};
var OLD_LEGEND = [
  ["Racking", "#243138"], ["Pick face", "#1d2a30"], ["Charging", "#16302f"], ["Loading", "#2c2716"],
  ["Unloading", "#252c1a"], ["Packing", "#231d2e"], ["Parking", "#1b2429"], ["Restricted", "#2b1e14"],
  ["Route", "#ffb300"]
];
function oldCellFill(type) {
  switch (type) {
    case "WALL": return OLD_COLORS.wall;
    case "SHELF": return OLD_COLORS.shelf;
    case "STORAGE": return OLD_COLORS.storage;
    case "CHARGING": return OLD_COLORS.charging;
    case "LOADING": return OLD_COLORS.loading;
    case "UNLOADING": return OLD_COLORS.unloading;
    case "PACKING": return OLD_COLORS.packing;
    case "PARKING": return OLD_COLORS.parking;
    case "RESTRICTED": return OLD_COLORS.restricted;
    default: return OLD_COLORS.floor;
  }
}

/* --------------------------------------------------------- the classic look */
test("the palette keeps every colour the dashboard has always drawn with", function () {
  Object.keys(OLD_COLORS).forEach(function (key) { eq(FM.PALETTE[key], OLD_COLORS[key], key); });
});

test("every classic cell fills exactly as before", function () {
  // Fails if a classic role's colour changes, or the table maps a classic type to another role.
  var fills = FM.cellFills(classic.cell_types, FM.PALETTE);
  classic.warehouse.cells.forEach(function (cell) {
    eq(fills[cell.type] || FM.PALETTE.floor, oldCellFill(cell.type), cell.type + " at " + cell.x + "," + cell.y);
  });
  eq(fills.EMPTY || FM.PALETTE.floor, OLD_COLORS.floor, "the open floor");
});

test("the classic legend is the one the dashboard has always shown", function () {
  eq(FM.legendItems(classic.cell_types, FM.PALETTE), OLD_LEGEND);
});

test("classic racking keeps its hatching and the restricted area its hazard stripes", function () {
  var roles = FM.cellRoles(classic.cell_types);
  eq(FM.cellPattern(roles.SHELF), "rack");
  eq(FM.cellPattern(roles.RESTRICTED), "hazard");
  ["STORAGE", "CHARGING", "LOADING", "UNLOADING", "PACKING", "PARKING", "WALL"].forEach(function (type) {
    eq(FM.cellPattern(roles[type]), null, type);
  });
});

test("every classic zone keeps its outline and label", function () {
  var frames = FM.zoneFrames(classic.warehouse.zones);
  eq(frames.map(function (f) { return f.key; }), classic.warehouse.zones.map(function (z) { return z.key; }));
  frames.forEach(function (f) {
    ok(f.outline && !f.vertical, f.key + " is outlined, label flat");
    eq(f.restricted, f.key === "restricted_area", f.key + " restricted");
  });
});

test("classic robots carry no letter, badge or arm view and all draw on the ground", function () {
  classic.robots.forEach(function (robot) {
    eq(FM.robotLetter(robot), null, robot.name);
    eq(FM.robotGlyph(robot), "square", robot.name);
    eq(FM.armView(robot, classic.equipment, classic.warehouse), null, robot.name);
    eq(FM.kindBadge(robot, { kind: "TOTE" }), null, robot.name);
  });
  var layers = FM.robotLayers(classic.robots, false);
  eq(layers.ground.length, classic.robots.length);
  eq(layers.air, []);
});

test("the classic floor has no people, conveyor or no-fly cells to draw", function () {
  eq(classic.equipment, null);
  eq(FM.conveyorView(classic.equipment), { items: [], jammed: [] });
  eq(FM.noFlyCells(classic.no_fly_cells), []);
  eq(FM.peopleDots(classic.operators, classic.warehouse.zones, []), []);
});

/* --------------------------------------------- the distribution-centre floor */
test("every distribution-centre cell type has a colour of its own", function () {
  var fills = FM.cellFills(dc.cell_types, FM.PALETTE);
  dc.cell_types.forEach(function (entry) {
    ok(FM.PALETTE[entry.role], "the palette has no colour for role " + entry.role);
  });
  dc.warehouse.cells.forEach(function (cell) {
    ok(fills[cell.type], "no fill for " + cell.type);
  });
  ok(fills.CONVEYOR !== fills.EMPTY && fills.WALKWAY !== fills.EMPTY, "the line and the walkway stand out");
});

test("the new floor's legend follows the cell-type table and ends on Route", function () {
  var expected = dc.cell_types.filter(function (e) { return e.legend; }).map(function (e) { return e.label; });
  expected.push("Route");
  eq(FM.legendItems(dc.cell_types, FM.PALETTE).map(function (item) { return item[0]; }), expected);
  ok(expected.indexOf("Racking") === -1, "classic-only types are not listed");
});

test("pallet racks and tote shelves are hatched like racking", function () {
  var roles = FM.cellRoles(dc.cell_types);
  eq(FM.cellPattern(roles.PALLET_RACK), "rack");
  eq(FM.cellPattern(roles.TOTE_SHELF), "rack");
  eq(FM.cellPattern(roles.RESTRICTED), "hazard");
  eq(FM.cellPattern(roles.CONVEYOR), null);
});

test("routes and plain aisles get no frame; the walkway's label stands upright", function () {
  var frames = {};
  FM.zoneFrames(dc.warehouse.zones).forEach(function (f) { frames[f.key] = f; });
  ["patrol_loop", "main_aisle", "cross_aisle", "top_aisle", "tote_aisle_1"].forEach(function (key) {
    ok(!frames[key], key + " has no frame");
  });
  ok(frames.walkway.vertical && frames.walkway.outline, "walkway");
  ok(!frames.pallet_racks.outline, "the rack rows are labelled but not boxed in");
  ok(frames.pack_cell_1.outline && !frames.pack_cell_1.vertical, "pack cell");
  ok(frames.restricted_area.restricted, "restricted");
});

test("each robot type has its letter and glyph", function () {
  var letters = {}, glyphs = {};
  dc.robots.forEach(function (robot) {
    letters[robot.name] = FM.robotLetter(robot);
    glyphs[robot.name] = FM.robotGlyph(robot);
  });
  eq(letters, {
    "TR50-201": "A", "PF1200-205": "F", "HH300-207": "H", "PK30-203": "K", "SC1-204": "S",
    "IX2-208": "D", "H1-212": "U", "CX10-210": "R"
  });
  eq(glyphs, {
    "TR50-201": "square", "PF1200-205": "forklift", "HH300-207": "wide", "PK30-203": "square",
    "SC1-204": "diamond", "IX2-208": "rotor", "H1-212": "round", "CX10-210": "arm"
  });
});

test("drones are drawn last, and not at all with the air layer hidden", function () {
  var shown = FM.robotLayers(dc.robots, true), hidden = FM.robotLayers(dc.robots, false);
  eq(shown.air.map(function (r) { return r.name; }), ["IX2-208"]);
  eq(named(dc.robots, "IX2-208").layer, "AIR");
  ok(shown.ground.every(function (r) { return !FM.isDrone(r); }), "no drone on the ground list");
  eq(shown.ground.length, dc.robots.length - 1);
  eq(hidden.air, []);
  eq(hidden.ground.length, dc.robots.length - 1);
});

test("a carried box shows its kind", function () {
  var amr = named(dc.robots, "TR50-201");
  var pallet = named(dc.boxes, "PAL-1"), carton = named(dc.boxes, "CARTON-1");
  eq(FM.kindBadge(amr, pallet), "P");
  eq(FM.kindBadge(amr, carton), "C");
  eq(FM.kindBadge(amr, { kind: "TOTE" }), "T");
  eq(FM.kindBadge(amr, { kind: "ITEM" }), "I");
  eq(FM.kindBadge(amr, null), null);
  eq([FM.kindLetter("CARTON"), FM.kindLetter("NOPE")], ["C", null]);
});

test("an arm shows its pack cell, the cells in its reach and its working cell", function () {
  var view = FM.armView(named(dc.robots, "CX10-210"), dc.equipment, dc.warehouse);
  eq(view.station, "pack_cell_1");
  eq(view.workCell, { x: 23, y: 15 });
  // 1300 mm reaches into every neighbouring cell (1.5 m cells), and no further.
  eq(cells(view.reach), ["22,13", "23,13", "24,13", "22,14", "24,14", "22,15", "23,15", "24,15"]);
  eq(view.paused, false);
});

test("an arm turns paused while it waits for a person to leave", function () {
  var arm = clone(named(dc.robots, "CX10-210"));
  arm.wait_reason = "PERSON_IN_CELL";
  var view = FM.armView(arm, dc.equipment, dc.warehouse);
  eq([view.paused, view.reason], [true, "PERSON_IN_CELL"]);
});

test("an arm with no reach on record still reaches the conveyor cell it works", function () {
  var arm = clone(named(dc.robots, "CX10-210"));
  arm.mobility.reach_mm = null;
  eq(FM.armView(arm, dc.equipment, dc.warehouse).reach, [{ x: 23, y: 15 }]);
});

test("only fixed arms have an arm view", function () {
  eq(FM.armView(named(dc.robots, "TR50-201"), dc.equipment, dc.warehouse), null);
  eq(FM.armView(named(dc.robots, "IX2-208"), dc.equipment, dc.warehouse), null);
});

test("the no-fly overlay covers the docks, the pack cells and the restricted area", function () {
  var noFly = cells(FM.noFlyCells(dc.no_fly_cells));
  eq(noFly.length, dc.no_fly_cells.length);
  ["2,3", "23,13", "28,7", "29,17"].forEach(function (key) { ok(noFly.indexOf(key) !== -1, key + " is no-fly"); });
  ["7,4", "20,2", "12,13"].forEach(function (key) { ok(noFly.indexOf(key) === -1, key + " may be flown"); });
  eq(FM.noFlyCells([[1, 2], "3,4", { x: 5, y: 6 }, null, "bad"]), [{ x: 1, y: 2 }, { x: 3, y: 4 }, { x: 5, y: 6 }]);
});

test("people stand in their zone, spread out from its centre", function () {
  var walkway = dc.warehouse.cells.filter(function (c) { return c.type === "WALKWAY"; });
  var dots = {};
  FM.peopleDots(dc.operators, dc.warehouse.zones, walkway).forEach(function (dot) { dots[dot.initials] = dot; });
  eq([dots.MS.x, dots.MS.y, dots.MS.inTransit], [23, 13, false], "Mateo, first in Pack 1, at its centre");
  eq([dots.RC.x, dots.RC.y, dots.RC.inTransit], [23, 12, false], "Riley, next to him");
  ok(!dots.SI, "Sasha is off the floor and not drawn");
});

test("a person on a walk is drawn on the walkway, at the cell nearest the middle of the walk", function () {
  var walkway = dc.warehouse.cells.filter(function (c) { return c.type === "WALKWAY"; });
  var jordan = FM.peopleDots(dc.operators, dc.warehouse.zones, walkway).filter(function (dot) {
    return dot.initials === "JB";
  })[0];
  // returns_qc (centre 22,7) to tote_aisle_1 (centre 12,13): the walk's middle is (17,10).
  eq([jordan.x, jordan.y, jordan.inTransit, jordan.zone, jordan.to], [17, 10, true, "returns_qc", "tote_aisle_1"]);
  var two = clone(dc.operators).filter(function (o) { return o.name === "Jordan Blake"; });
  two.push(clone(two[0]));
  two[1].id = "other";
  var both = FM.peopleDots(two, dc.warehouse.zones, walkway);
  ok(both[0].y !== both[1].y && both[1].x === 17, "two walkers take two walkway cells");
});

test("initials come from the first two names", function () {
  eq([FM.initials("Mateo Silva"), FM.initials("Sam"), FM.initials("  noor   haddad "), FM.initials("")],
    ["MS", "Sa", "NH", "?"]);
});

test("conveyor items and jammed cells come from the snapshot's equipment", function () {
  var view = FM.conveyorView(dc.equipment);
  eq(view.items, [
    { x: 20, y: 15, boxId: named(dc.boxes, "ITEM-1").id },
    { x: 22, y: 15, boxId: named(dc.boxes, "CARTON-1").id }
  ]);
  eq(view.jammed, [{ x: 23, y: 15 }]);
  eq(FM.conveyorView(null), { items: [], jammed: [] });
  eq(FM.conveyorView({}), { items: [], jammed: [] });
});

test("cells are read from every shape the backend uses", function () {
  eq([FM.toCell({ x: 1, y: 2 }), FM.toCell([3, 4]), FM.toCell("5,6"), FM.toCell(null), FM.toCell("x")],
    [{ x: 1, y: 2 }, { x: 3, y: 4 }, { x: 5, y: 6 }, null, null]);
});

print(passed + " passed, " + failed + " failed");
if (failed) throw new Error(failed + " floor model test(s) failed");
```

**Create** `backend/test_floor_model_js.py`:

```python
"""The dashboard floor (multi-embodiment spec §12), checked under JavaScriptCore.

frontend/tests/floor_model_test.js tests frontend/floor_model.js against
FIXTURES: real /api/state snapshots of both floors, built here through the
public API (a classic demo twin, and a distribution-centre twin with one robot
of every type, people in a pack cell and on a walk, conveyor items and a jam).
This file also checks that frontend/app.js compiles, that both files stay
ES5, and that the page loads the floor model before the dashboard. Skipped
where jsc is not installed.
"""
from __future__ import annotations

import json
import os
import re
import subprocess

import pytest

from backend import people
from backend.digital_twin import DigitalTwin

JSC = "/System/Library/Frameworks/JavaScriptCore.framework/Versions/A/Helpers/jsc"
FRONTEND = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend")

pytestmark = pytest.mark.skipif(not os.path.exists(JSC), reason="JavaScriptCore (jsc) is not installed")

#: (twin name, inventory asset, cell): one robot of every type. The arm takes its station.
FLEET = (("TR50-201", "AST-000201", (8, 13)), ("PF1200-205", "AST-000205", (7, 4)),
         ("HH300-207", "AST-000207", (2, 8)), ("PK30-203", "AST-000203", (20, 14)),
         ("SC1-204", "AST-000204", (7, 6)), ("IX2-208", "AST-000208", (20, 2)),
         ("H1-212", "AST-000212", (22, 7)))


def run_jsc(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([JSC, *args], capture_output=True, text=True, timeout=120)


def state_of(twin: DigitalTwin) -> dict:
    """What GET /api/state sends, through JSON as the browser gets it."""
    return json.loads(json.dumps(twin.snapshot(include_layout=True), default=str))


def classic_state(tmp_path) -> dict:
    twin = DigitalTwin(log_dir=str(tmp_path / "classic_logs"), data_dir=str(tmp_path / "classic_data"),
                       persist_logs=False, demo=True, demo_tasks=False)
    return state_of(twin)


def distribution_center_state(tmp_path) -> dict:
    twin = DigitalTwin(log_dir=str(tmp_path / "dc_logs"), data_dir=str(tmp_path / "dc_data"),
                       persist_logs=False, demo=True, demo_tasks=False, layout="distribution_center")
    for name, asset, cell in FLEET:
        twin.add_robot(name=name, asset_id=asset, position=cell)
    twin.add_robot(name="CX10-210", asset_id="AST-000210")
    drone = twin.find_robot("IX2-208")
    twin.set_robot_layer(drone.id, "AIR", 2.0)
    mateo = twin.add_operator(name="Mateo Silva", worker_id="E-10004")
    riley = twin.add_operator(name="Riley Chen", worker_id="E-10008")
    jordan = twin.add_operator(name="Jordan Blake", worker_id="E-10006")
    twin.add_operator(name="Sasha Ivanova", worker_id="E-10009")  # never placed: off the floor
    people.place(twin, mateo, "pack_cell_1")
    people.place(twin, riley, "pack_cell_1")
    people.place(twin, jordan, "returns_qc")
    people.start_transit(twin, jordan, "tote_aisle_1")
    twin.add_box(name="ITEM-1", kind="ITEM", sku="SKU-001", quantity=1, weight=1.0, position=(20, 15))
    twin.add_box(name="CARTON-1", kind="CARTON", quantity=0, weight=2.0, position=(22, 15))
    twin.add_box(name="PAL-1", kind="PALLET", sku="SKU-002", quantity=40, weight=500.0, slot="PR-10-05-0")
    twin.equipment.jam((23, 15))
    return state_of(twin)


def test_the_floor_model_draws_both_floors(tmp_path):
    fixtures = tmp_path / "fixtures.js"
    fixtures.write_text("var FIXTURES = " + json.dumps({
        "classic": classic_state(tmp_path), "dc": distribution_center_state(tmp_path),
    }) + ";\n")
    result = run_jsc(str(fixtures), os.path.join(FRONTEND, "floor_model.js"),
                     os.path.join(FRONTEND, "tests", "floor_model_test.js"))
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "FAIL" not in output, output
    assert re.search(r"\b\d+ passed, 0 failed\b", output), output


def test_the_dashboard_script_compiles():
    # new Function parses the whole file without running it (it needs a page).
    result = run_jsc("-e", "new Function(readFile(arguments[0]));", "--", os.path.join(FRONTEND, "app.js"))
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("name", ["floor_model.js", "app.js"])
def test_the_dashboard_stays_es5(name):
    source = open(os.path.join(FRONTEND, name), encoding="utf-8").read()
    assert "=>" not in source, f"{name} uses an arrow function"
    assert not re.search(r"\b(let|const)\s+[A-Za-z_$][\w$]*\s*=", source), f"{name} declares with let/const"
    assert not re.search(r"\bclass\s+[A-Za-z_$][\w$]*\s*\{", source), f"{name} declares a class"


def test_the_page_loads_the_floor_model_before_the_dashboard():
    html = open(os.path.join(FRONTEND, "index.html"), encoding="utf-8").read()
    assert '<script src="floor_model.js"></script>' in html
    assert html.index('src="floor_model.js"') < html.index('src="app.js"')
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/bin/python -m pytest -o addopts="" -q backend/test_floor_model_js.py`
Expected: `3 failed, 2 passed` — `Could not open file: …/frontend/floor_model.js` (the floor model test), `FileNotFoundError` for `floor_model.js` (its ES5 check), and `index.html` has no `<script src="floor_model.js">`. `app.js` already compiles and is ES5.

- [ ] **Step 3: The floor model**

**Create** `frontend/floor_model.js`:

```javascript
/* ==========================================================================
   Warehouse Digital Twin — floor model
   Pure, DOM-free helpers the dashboard draws the floor from (multi-embodiment
   spec §12): cell fills and the legend from the snapshot's cell_types table,
   robot letters and glyphs, the air layer, arms, people and the conveyor.
   Nothing here touches the page, so frontend/tests/*.js run it under
   JavaScriptCore (where there is no window); app.js reads it as
   window.FloorModel. Like app.js it is plain ES5.
   ========================================================================== */
(function (root) {
  "use strict";

  /* The dashboard palette. The classic floor's colours are exactly the ones
     the dashboard has always drawn it with (frontend/tests pins them); the
     rest colour the distribution-centre floor's cell roles, named as
     backend/layouts/base.py CELL_TYPE_STYLES names them. */
  var PALETTE = {
    floor: "#0e1417",
    grid: "#182126",
    wall: "#2c3a41",
    shelf: "#243138",
    shelfLine: "#33454e",
    storage: "#1d2a30",
    charging: "#16302f",
    loading: "#2c2716",
    unloading: "#252c1a",
    packing: "#231d2e",
    parking: "#1b2429",
    restricted: "#2b1e14",
    amber: "#ffb300",
    cyan: "#31d1c4",
    ok: "#7ddf64",
    warn: "#ff9538",
    fault: "#ff5252",
    ink: "#e7eef1",
    muted: "#7b8d95",
    faint: "#4d5f67",
    box: "#c98b3a",
    boxCarried: "#ffb300",
    boxDelivered: "#5f8f4f",
    dock: "#26301a",
    dockDoor: "#5a4614",
    staging: "#1c2b27",
    palletRack: "#283640",
    toteShelf: "#21303a",
    walkway: "#2e2b12",
    station: "#251f33",
    conveyor: "#2b3236",
    sorter: "#2c2338",
    workshop: "#2a2118",
    dronePad: "#14293a",
    noFly: "#9b8cff"
  };

  /* One letter per robot type (spec §12). */
  var LETTERS = {
    AMR: "A", FORKLIFT: "F", HEAVY_HAULER: "H", PICKER: "K",
    SCOUT: "S", DRONE: "D", ARM: "R", HUMANOID: "U"
  };

  /* The chassis shape app.js draws for each robot type. */
  var GLYPHS = {
    AMR: "square", PICKER: "square", FORKLIFT: "forklift", HEAVY_HAULER: "wide",
    SCOUT: "diamond", DRONE: "rotor", ARM: "arm", HUMANOID: "round"
  };

  var KIND_BADGES = { PALLET: "P", TOTE: "T", ITEM: "I", CARTON: "C" };

  /* Cell roles drawn with the rack hatching, and with the hazard stripes. */
  var RACK_ROLES = { shelf: true, palletRack: true, toteShelf: true };
  var HAZARD_ROLES = { restricted: true };

  /* 1 cell = 1.5 m on every floor (spec §3); an arm's reach is in mm. */
  var CELL_SIZE_M = 1.5;

  /* --------------------------------------------------------------- cells */
  /* {x, y} from a cell as the backend sends it ({x, y}), as a pair [x, y]
     or as "x,y"; null for anything else. */
  function toCell(value) {
    if (value === null || value === undefined) return null;
    if (typeof value === "string") {
      var parts = value.split(",");
      if (parts.length !== 2) return null;
      value = [Number(parts[0]), Number(parts[1])];
    }
    if (Object.prototype.toString.call(value) === "[object Array]") {
      if (value.length !== 2) return null;
      value = { x: value[0], y: value[1] };
    }
    if (typeof value.x !== "number" || typeof value.y !== "number" ||
        isNaN(value.x) || isNaN(value.y)) return null;
    return { x: value.x, y: value.y };
  }

  function sameCell(a, b) {
    return !!a && !!b && a.x === b.x && a.y === b.y;
  }

  function manhattan(a, b) {
    return Math.abs(a.x - b.x) + Math.abs(a.y - b.y);
  }

  /* ---------------------------------------------------------- cell types */
  /* {type: role} from the snapshot's cell_types table. */
  function cellRoles(cellTypes) {
    var roles = {};
    (cellTypes || []).forEach(function (entry) { roles[entry.type] = entry.role; });
    return roles;
  }

  /* {type: colour}: each cell type's role looked up in `colors`; a role the
     palette doesn't know draws as plain floor. */
  function cellFills(cellTypes, colors) {
    var fills = {};
    (cellTypes || []).forEach(function (entry) {
      fills[entry.type] = colors[entry.role] || colors.floor;
    });
    return fills;
  }

  /* [[label, colour]] for the types the table shows in the legend, in its
     order, then the planned-route swatch the dashboard has always ended on. */
  function legendItems(cellTypes, colors) {
    var items = [];
    (cellTypes || []).forEach(function (entry) {
      if (entry.legend) items.push([entry.label, colors[entry.role] || colors.floor]);
    });
    items.push(["Route", colors.amber]);
    return items;
  }

  /* "rack" (hatched shelving), "hazard" (striped) or null, by cell role. */
  function cellPattern(role) {
    if (RACK_ROLES[role]) return "rack";
    if (HAZARD_ROLES[role]) return "hazard";
    return null;
  }

  /* The zone outlines and labels to draw: every zone but routes (a patrol
     loop is a path, not a place) and plain aisles (EMPTY zones only name
     cells). A zone whose cells don't fill its bounding box (the rack rows)
     gets a label but no outline; a one-cell-wide strip (the walkway) gets
     its label turned upright. */
  function zoneFrames(zones) {
    var frames = [];
    (zones || []).forEach(function (zone) {
      if (zone.type === "EMPTY" || (zone.attributes && zone.attributes.route)) return;
      if (!zone.cells || !zone.cells.length) return;
      var xs = zone.cells.map(function (c) { return c.x; });
      var ys = zone.cells.map(function (c) { return c.y; });
      var x0 = Math.min.apply(null, xs), x1 = Math.max.apply(null, xs);
      var y0 = Math.min.apply(null, ys), y1 = Math.max.apply(null, ys);
      frames.push({
        key: zone.key, label: zone.label, x0: x0, y0: y0, x1: x1, y1: y1,
        outline: zone.cells.length === (x1 - x0 + 1) * (y1 - y0 + 1),
        restricted: zone.type === "RESTRICTED",
        vertical: x1 === x0 && y1 > y0 + 1
      });
    });
    return frames;
  }

  /* The no-fly cells the snapshot lists, as {x, y}. */
  function noFlyCells(value) {
    var cells = [];
    (value || []).forEach(function (item) {
      var cell = toCell(item);
      if (cell) cells.push(cell);
    });
    return cells;
  }

  /* -------------------------------------------------------------- robots */
  function embodiment(robot) {
    return robot && robot.mobility ? robot.mobility.embodiment_class : null;
  }

  /* The robot's type letter, or null for a robot with no floor profile (a
     classic robot draws exactly as it always has, Plan ruling 10). */
  function robotLetter(robot) {
    var kind = embodiment(robot);
    return kind ? (LETTERS[kind] || null) : null;
  }

  /* The chassis shape to draw: "square" (classic robots, AMRs, pickers),
     "forklift", "wide", "diamond", "rotor", "arm" or "round". */
  function robotGlyph(robot) {
    var kind = embodiment(robot);
    return (kind && GLYPHS[kind]) || "square";
  }

  /* A flying body (spec §5.1 movement AIR): the air layer's robots. */
  function isDrone(robot) {
    return !!(robot && robot.mobility && robot.mobility.movement === "AIR");
  }

  /* The robots to draw under the people and over them: drones go last, above
     everything, and only while the air layer is shown. */
  function robotLayers(robots, showAir) {
    var ground = [], air = [];
    (robots || []).forEach(function (robot) {
      if (isDrone(robot)) {
        if (showAir) air.push(robot);
      } else {
        ground.push(robot);
      }
    });
    return { ground: ground, air: air };
  }

  /* A box kind's letter: "P" pallet, "T" tote, "I" item, "C" carton. */
  function kindLetter(kind) {
    return KIND_BADGES[kind] || null;
  }

  /* The kind badge on the box a robot carries, or null: classic robots
     carry plain boxes, drawn as they always were. */
  function kindBadge(robot, box) {
    if (!robot || !robot.mobility || !box) return null;
    return kindLetter(box.kind);
  }

  /* ---------------------------------------------------------------- arms */
  /* What an arm shows (spec §12 Arms): its station (the pack-cell zone whose
     arm cell it stands on), the cells within its reach — any cell some point
     of which is within reach_mm of the arm's base, and always the conveyor
     cell it works — and whether it is paused (a safety wait: a person in its
     cell, or a jam upstream). Null for anything that isn't a fixed arm. */
  function armView(robot, equipment, layout) {
    if (!robot || !robot.mobility || robot.mobility.movement !== "FIXED") return null;
    var base = toCell(robot.position);
    if (!base) return null;
    var station = null;
    ((layout && layout.zones) || []).forEach(function (zone) {
      if (station === null && zone.attributes && sameCell(toCell(zone.attributes.arm_cell), base)) {
        station = zone.key;
      }
    });
    var reachM = robot.mobility.reach_mm ? robot.mobility.reach_mm / 1000 : 0;
    var span = Math.ceil(reachM / CELL_SIZE_M + 0.5);
    var width = layout && layout.width, height = layout && layout.height;
    var reach = [];
    for (var dy = -span; dy <= span; dy++) {
      for (var dx = -span; dx <= span; dx++) {
        if (!dx && !dy) continue;
        var x = base.x + dx, y = base.y + dy;
        if (x < 0 || y < 0 || (width && x >= width) || (height && y >= height)) continue;
        var gapX = Math.max(0, Math.abs(dx) * CELL_SIZE_M - CELL_SIZE_M / 2);
        var gapY = Math.max(0, Math.abs(dy) * CELL_SIZE_M - CELL_SIZE_M / 2);
        if (Math.sqrt(gapX * gapX + gapY * gapY) <= reachM) reach.push({ x: x, y: y });
      }
    }
    var work = station && equipment && equipment.arm_cells ? toCell(equipment.arm_cells[station]) : null;
    if (work && !reach.some(function (cell) { return sameCell(cell, work); })) reach.push(work);
    return {
      station: station,
      reach: reach,
      workCell: work,
      paused: !!robot.wait_reason,
      reason: robot.wait_reason || null
    };
  }

  /* -------------------------------------------------------------- people */
  /* Two letters for a person's dot: "Mateo Silva" → "MS", "Sam" → "Sa". */
  function initials(name) {
    var words = String(name || "").replace(/^\s+|\s+$/g, "").split(/\s+/);
    if (!words[0]) return "?";
    if (words.length === 1) {
      return words[0].charAt(0).toUpperCase() + words[0].charAt(1).toLowerCase();
    }
    return (words[0].charAt(0) + words[1].charAt(0)).toUpperCase();
  }

  /* A zone's cells, nearest its centre first (then by row, then column), so
     the people standing in it spread out from the middle. */
  function cellsFromCentre(zone) {
    var centre = toCell(zone.center) || toCell(zone.cells[0]);
    return zone.cells.map(toCell).filter(Boolean).sort(function (a, b) {
      return manhattan(a, centre) - manhattan(b, centre) || a.y - b.y || a.x - b.x;
    });
  }

  /* Where each person on the floor is drawn (spec §12 People): a person in a
     zone stands on one of its cells, the k-th person in a zone on the k-th
     cell from its centre; a person walking between two zones (transit_to
     set) is on the walkway, at the walkway cell nearest the midpoint of the
     walk, one walker a cell. Off the floor (no zone) is not drawn. */
  function peopleDots(operators, zones, walkwayCells) {
    var byKey = {};
    (zones || []).forEach(function (zone) { byKey[zone.key] = zone; });
    var walkway = (walkwayCells || []).map(toCell).filter(Boolean);
    var inZone = {}, taken = {};
    var dots = [];
    (operators || []).forEach(function (person) {
      var from = person.zone ? byKey[person.zone] : null;
      if (!from || !from.cells || !from.cells.length) return;
      if (person.transit_to) {
        var to = byKey[person.transit_to] || from;
        var a = toCell(from.center), b = toCell(to.center);
        var middle = { x: Math.round((a.x + b.x) / 2), y: Math.round((a.y + b.y) / 2) };
        var spot = middle, best = null;
        walkway.forEach(function (cell) {
          var key = cell.x + "," + cell.y;
          var score = manhattan(cell, middle) + (taken[key] ? 1000 : 0);
          if (best === null || score < best) { best = score; spot = cell; }
        });
        taken[spot.x + "," + spot.y] = true;
        dots.push({ id: person.id, initials: initials(person.name), x: spot.x, y: spot.y,
          inTransit: true, zone: person.zone, to: person.transit_to });
        return;
      }
      var cells = cellsFromCentre(from);
      var k = inZone[from.key] || 0;
      inZone[from.key] = k + 1;
      var cell = cells[k % cells.length];
      dots.push({ id: person.id, initials: initials(person.name), x: cell.x, y: cell.y,
        inTransit: false, zone: person.zone, to: null });
    });
    return dots;
  }

  /* ------------------------------------------------------------ conveyor */
  /* The items riding the line and the jammed cells, from the snapshot's
     equipment (twin.equipment.to_dict(): conveyor.items[*].cell / box_id and
     conveyor.jams[*].cell). No equipment (classic) gives an empty line. */
  function conveyorView(equipment) {
    var line = equipment && equipment.conveyor;
    var view = { items: [], jammed: [] };
    if (!line) return view;
    (line.items || []).forEach(function (item) {
      var cell = toCell(item.cell);
      if (cell) view.items.push({ x: cell.x, y: cell.y, boxId: item.box_id });
    });
    (line.jams || []).forEach(function (jam) {
      var cell = toCell(jam.cell);
      if (cell) view.jammed.push(cell);
    });
    return view;
  }

  /* ------------------------------------------------------------- exports */
  root.FloorModel = {
    PALETTE: PALETTE,
    LETTERS: LETTERS,
    CELL_SIZE_M: CELL_SIZE_M,
    toCell: toCell,
    cellRoles: cellRoles,
    cellFills: cellFills,
    legendItems: legendItems,
    cellPattern: cellPattern,
    zoneFrames: zoneFrames,
    noFlyCells: noFlyCells,
    robotLetter: robotLetter,
    robotGlyph: robotGlyph,
    isDrone: isDrone,
    robotLayers: robotLayers,
    kindLetter: kindLetter,
    kindBadge: kindBadge,
    armView: armView,
    initials: initials,
    peopleDots: peopleDots,
    conveyorView: conveyorView
  };
})(typeof window !== "undefined" ? window : this);
```


- [ ] **Step 4: The page loads the floor model, and the air-layer switch**

The switch is a `.toggle`, whose `display: flex` would beat the `hidden` attribute, hence the `[hidden]` rule.

**Replace in** `frontend/index.html`:

```html
        <div class="legend" id="legend"></div>
      </div>
```

with:

```html
        <div class="legend" id="legend"></div>
        <label class="toggle air-toggle" id="airToggleWrap" hidden>
          <input type="checkbox" id="airToggle" checked /><span>Air layer</span>
        </label>
      </div>
```

**Replace in** `frontend/index.html`:

```html

<script src="app.js"></script>
```

with:

```html

<script src="floor_model.js"></script>
<script src="app.js"></script>
```

**Replace in** `frontend/style.css`:

```css
.legend i { width: 9px; height: 9px; border-radius: 1px; display: inline-block; }
```

with:

```css
.legend i { width: 9px; height: 9px; border-radius: 1px; display: inline-block; }
/* The air layer switch (drones and the no-fly overlay); hidden on a floor with none. */
.air-toggle { font-family: var(--display); font-size: 10px; letter-spacing: 0.12em; text-transform: uppercase; }
.air-toggle input { accent-color: var(--violet); }
.air-toggle[hidden] { display: none; }
```


- [ ] **Step 5: The dashboard draws the floor from the floor model**

What changes in `frontend/app.js`, in file order: the state keeps the layout's `cell_types`, its no-fly cells and the live equipment; `COLORS` is the floor model's palette and `LEGEND` goes; `cellFill` reads the fills `layoutChanged` works out from `cell_types` (which also re-renders the legend), and the rack hatching and hazard stripes go by role; zone frames come from `FloorModel.zoneFrames` (a classic zone draws exactly as before; the walkway's label is turned upright); new layers draw the no-fly overlay, the conveyor (red jammed cells, gliding items, guarded so the classic floor issues no extra drawing calls), the arms and the people; hidden drones' routes and shipped boxes aren't drawn; `drawRobots` takes the list to draw and gives each body its chassis glyph, its letter, the amber wait ring and the carried box's kind badge (the square chassis path is today's, so classic robots draw as before); `draw` draws ground robots, then people, then the air layer; the legend comes from the table; `applyState` reads `cell_types`, `no_fly_cells` and `equipment`; `flushDom` shows the switch only on a floor that has an air layer; `wire` wires it.

**Replace in** `frontend/app.js`:

```javascript
    ci: null
```

with:

```javascript
    ci: null,
    cellTypes: [],
    noFly: [],
    equipment: null
```

**Replace in** `frontend/app.js`:

```javascript
  var render = { robots: {}, dash: 0 };
```

with:

```javascript
  var render = { robots: {}, items: {}, dash: 0, fills: {}, roles: {}, walkway: [] };
  var showAir = true;    // the "Air layer" toggle: drones and the no-fly overlay
```

**Replace in** `frontend/app.js`:

```javascript
  var COLORS = {
    floor: "#0e1417",
    grid: "#182126",
    wall: "#2c3a41",
    shelf: "#243138",
    shelfLine: "#33454e",
    storage: "#1d2a30",
    charging: "#16302f",
    loading: "#2c2716",
    unloading: "#252c1a",
    packing: "#231d2e",
    parking: "#1b2429",
    restricted: "#2b1e14",
    amber: "#ffb300",
    cyan: "#31d1c4",
    ok: "#7ddf64",
    warn: "#ff9538",
    fault: "#ff5252",
    ink: "#e7eef1",
    muted: "#7b8d95",
    faint: "#4d5f67",
    box: "#c98b3a",
    boxCarried: "#ffb300",
    boxDelivered: "#5f8f4f"
  };

  var ROBOT_TINT = ["#31d1c4", "#ffb300", "#9b8cff", "#7ddf64", "#ff9538", "#ff5252"];

  var LEGEND = [
    ["Racking", COLORS.shelf],
    ["Pick face", COLORS.storage],
    ["Charging", COLORS.charging],
    ["Loading", COLORS.loading],
    ["Unloading", COLORS.unloading],
    ["Packing", COLORS.packing],
    ["Parking", COLORS.parking],
    ["Restricted", COLORS.restricted],
    ["Route", COLORS.amber]
  ];
```

with:

```javascript
  // Pure floor helpers (frontend/floor_model.js, loaded first by index.html).
  var FM = window.FloorModel;

  // The palette lives in the floor model, so its tests can pin the classic
  // floor's colours; the fills and the legend come from the snapshot's
  // cell_types table (see layoutChanged and renderLegend).
  var COLORS = FM.PALETTE;

  var ROBOT_TINT = ["#31d1c4", "#ffb300", "#9b8cff", "#7ddf64", "#ff9538", "#ff5252"];
```

**Replace in** `frontend/app.js`:

```javascript
    return id || "—";
  }
```

with:

```javascript
    return id || "—";
  }
  function findBox(id) {
    for (var i = 0; i < state.boxes.length; i++) if (state.boxes[i].id === id) return state.boxes[i];
    return null;
  }
```

**Replace in** `frontend/app.js`:

```javascript
    switch (type) {
      case "WALL": return COLORS.wall;
      case "SHELF": return COLORS.shelf;
      case "STORAGE": return COLORS.storage;
      case "CHARGING": return COLORS.charging;
      case "LOADING": return COLORS.loading;
      case "UNLOADING": return COLORS.unloading;
      case "PACKING": return COLORS.packing;
      case "PARKING": return COLORS.parking;
      case "RESTRICTED": return COLORS.restricted;
      default: return COLORS.floor;
    }
```

with:

```javascript
    return render.fills[type] || COLORS.floor;
  }

  // The layout arrived or changed: recompute what is drawn from its
  // cell_types table and cells, and the legend.
  function layoutChanged() {
    render.fills = FM.cellFills(state.cellTypes, COLORS);
    render.roles = FM.cellRoles(state.cellTypes);
    render.walkway = state.layout.cells.filter(function (c) { return c.type === "WALKWAY"; });
    renderLegend();
```

**Replace in** `frontend/app.js`:

```javascript
      if (item.type === "SHELF" && shelfPattern) {
```

with:

```javascript
      var pattern = FM.cellPattern(render.roles[item.type]);
      if (pattern === "rack" && shelfPattern) {
```

**Replace in** `frontend/app.js`:

```javascript
      if (item.type === "RESTRICTED" && hazardPattern) {
```

with:

```javascript
      if (pattern === "hazard" && hazardPattern) {
```

**Replace in** `frontend/app.js`:

```javascript
    layout.zones.forEach(function (zone) {
      var xs = zone.cells.map(function (c) { return c.x; });
      var ys = zone.cells.map(function (c) { return c.y; });
      var x0 = Math.min.apply(null, xs), x1 = Math.max.apply(null, xs);
      var y0 = Math.min.apply(null, ys), y1 = Math.max.apply(null, ys);
      ctx.strokeStyle = zone.type === "RESTRICTED" ? "rgba(255,179,0,0.55)" : "rgba(49,209,196,0.24)";
      ctx.lineWidth = 1;
      ctx.strokeRect(x0 * cell + 1.5, y0 * cell + 1.5, (x1 - x0 + 1) * cell - 3, (y1 - y0 + 1) * cell - 3);
      ctx.fillStyle = "rgba(231,238,241,0.4)";
      ctx.fillText(
        zone.label.toUpperCase(),
        ((x0 + x1 + 1) / 2) * cell,
        ((y0 + y1 + 1) / 2) * cell
      );
```

with:

```javascript
    // Routes and plain aisles get no frame; see FloorModel.zoneFrames.
    FM.zoneFrames(layout.zones).forEach(function (frame) {
      var x0 = frame.x0, x1 = frame.x1, y0 = frame.y0, y1 = frame.y1;
      if (frame.outline) {
        ctx.strokeStyle = frame.restricted ? "rgba(255,179,0,0.55)" : "rgba(49,209,196,0.24)";
        ctx.lineWidth = 1;
        ctx.strokeRect(x0 * cell + 1.5, y0 * cell + 1.5, (x1 - x0 + 1) * cell - 3, (y1 - y0 + 1) * cell - 3);
      }
      ctx.fillStyle = "rgba(231,238,241,0.4)";
      var lx = ((x0 + x1 + 1) / 2) * cell, ly = ((y0 + y1 + 1) / 2) * cell;
      if (frame.vertical) {    // a one-cell strip (the walkway): its label stands upright
        ctx.save();
        ctx.translate(lx, ly);
        ctx.rotate(-Math.PI / 2);
        ctx.fillText(frame.label.toUpperCase(), 0, 0);
        ctx.restore();
      } else {
        ctx.fillText(frame.label.toUpperCase(), lx, ly);
      }
    });
  }

  // The no-fly overlay: part of the air layer, so the toggle hides it too.
  function drawNoFly() {
    if (!showAir || !state.noFly.length) return;
    var cell = geometry.cell;
    ctx.save();
    ctx.fillStyle = "rgba(155,140,255,0.10)";
    ctx.strokeStyle = "rgba(155,140,255,0.30)";
    ctx.lineWidth = 1;
    state.noFly.forEach(function (c) {
      ctx.fillRect(c.x * cell, c.y * cell, cell, cell);
      ctx.beginPath();
      ctx.moveTo(c.x * cell + cell * 0.3, c.y * cell + cell * 0.3);
      ctx.lineTo(c.x * cell + cell * 0.7, c.y * cell + cell * 0.7);
      ctx.moveTo(c.x * cell + cell * 0.7, c.y * cell + cell * 0.3);
      ctx.lineTo(c.x * cell + cell * 0.3, c.y * cell + cell * 0.7);
      ctx.stroke();
    });
    ctx.restore();
  }

  // Jammed conveyor cells in red, and the items riding the line, each
  // gliding to the cell the backend reports it on.
  function drawConveyor() {
    var view = FM.conveyorView(state.equipment);
    if (!view.items.length && !view.jammed.length && !Object.keys(render.items).length) return;
    var cell = geometry.cell;
    ctx.save();
    view.jammed.forEach(function (c) {
      ctx.fillStyle = "rgba(255,82,82,0.42)";
      ctx.fillRect(c.x * cell, c.y * cell, cell, cell);
      ctx.strokeStyle = COLORS.fault;
      ctx.lineWidth = Math.max(1.5, cell * 0.06);
      ctx.strokeRect(c.x * cell + 1.5, c.y * cell + 1.5, cell - 3, cell - 3);
    });
    ctx.restore();
    var seen = {};
    view.items.forEach(function (item) {
      seen[item.boxId] = true;
      var display = render.items[item.boxId];
      if (!display || Math.abs(item.x - display.x) > 2.5 || Math.abs(item.y - display.y) > 2.5) {
        display = render.items[item.boxId] = { x: item.x, y: item.y };
      }
      display.x += (item.x - display.x) * 0.24;
      display.y += (item.y - display.y) * 0.24;
      var box = findBox(item.boxId);
      drawBox((display.x + 0.5) * cell, (display.y + 0.5) * cell, cell * 0.4,
        box && box.kind === "CARTON" ? COLORS.boxDelivered : COLORS.box,
        box ? FM.kindLetter(box.kind) : "");
    });
    Object.keys(render.items).forEach(function (id) { if (!seen[id]) delete render.items[id]; });
  }

  // Each arm's reachable cells, and its pack cell framed: cyan at work,
  // amber while it is paused for a person (or a jam upstream).
  function drawArms() {
    var cell = geometry.cell;
    state.robots.forEach(function (robot) {
      var view = FM.armView(robot, state.equipment, state.layout);
      if (!view) return;
      ctx.save();
      ctx.fillStyle = view.paused ? "rgba(255,179,0,0.16)" : "rgba(49,209,196,0.10)";
      view.reach.forEach(function (c) { ctx.fillRect(c.x * cell + 2, c.y * cell + 2, cell - 4, cell - 4); });
      var frame = view.station ? FM.zoneFrames(state.layout.zones).filter(function (f) {
        return f.key === view.station;
      })[0] : null;
      if (frame) {
        ctx.strokeStyle = view.paused ? COLORS.amber : "rgba(49,209,196,0.7)";
        ctx.lineWidth = Math.max(1.5, cell * 0.06);
        ctx.setLineDash(view.paused ? [cell * 0.2, cell * 0.12] : []);
        ctx.strokeRect(frame.x0 * cell + 3, frame.y0 * cell + 3,
          (frame.x1 - frame.x0 + 1) * cell - 6, (frame.y1 - frame.y0 + 1) * cell - 6);
      }
      ctx.restore();
    });
  }

  // People as small dots with their initials (spec §12 People): in their
  // zone, or on the walkway while they walk between two zones.
  function drawPeople() {
    if (!state.operators.length) return;
    var cell = geometry.cell;
    var radius = Math.max(4, cell * 0.2);
    FM.peopleDots(state.operators, state.layout.zones, render.walkway).forEach(function (dot) {
      var cx = (dot.x + 0.28) * cell, cy = (dot.y + 0.28) * cell;
      ctx.save();
      ctx.fillStyle = dot.inTransit ? COLORS.cyan : COLORS.ink;
      ctx.strokeStyle = "rgba(0,0,0,0.6)";
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.arc(cx, cy, radius, 0, Math.PI * 2);
      ctx.fill();
      ctx.stroke();
      if (cell >= 18) {
        ctx.fillStyle = "#0f1417";
        ctx.font = "700 " + Math.max(7, Math.round(radius * 1.05)) + "px 'Saira Condensed', sans-serif";
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        ctx.fillText(dot.initials, cx, cy + 0.5);
      }
      ctx.restore();
```

**Replace in** `frontend/app.js`:

```javascript
      if (!robot.current_path.length) return;
      var tint = robotTint(robot.id);
```

with:

```javascript
      if (!robot.current_path.length) return;
      if (!showAir && FM.isDrone(robot)) return;
      var tint = robotTint(robot.id);
```

**Replace in** `frontend/app.js`:

```javascript
      if (box.status === "CARRIED" || box.status === "DELIVERING") return; // drawn on the robot
```

with:

```javascript
      if (box.status === "CARRIED" || box.status === "DELIVERING") return; // drawn on the robot or the line
      if (box.status === "SHIPPED") return;  // it left on a truck
```

**Replace in** `frontend/app.js`:

```javascript
  function drawRobots() {
    var cell = geometry.cell;
    state.robots.forEach(function (robot) {
```

with:

```javascript
  function roundedRect(x, y, w, h, r) {
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.arcTo(x + w, y, x + w, y + h, r);
    ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r);
    ctx.arcTo(x, y, x + w, y, r);
    ctx.closePath();
  }

  // A robot type's chassis (FloorModel.robotGlyph), around the origin and
  // facing +x, in the current fill and stroke. Returns where its heading
  // notch goes, or 0 for a body whose front needs no notch.
  function drawChassis(glyph, size) {
    var half = size / 2;
    if (glyph === "diamond") {
      ctx.beginPath();
      ctx.moveTo(half, 0); ctx.lineTo(0, -half); ctx.lineTo(-half, 0); ctx.lineTo(0, half);
      ctx.closePath();
    } else if (glyph === "round" || glyph === "arm" || glyph === "rotor") {
      ctx.beginPath();
      ctx.arc(0, 0, glyph === "round" ? half : glyph === "arm" ? size * 0.42 : size * 0.26, 0, Math.PI * 2);
    } else if (glyph === "wide") {
      roundedRect(-size * 0.575, -size * 0.425, size * 1.15, size * 0.85, size * 0.22);
    } else if (glyph === "forklift") {
      roundedRect(-half, -half, size * 0.7, size, size * 0.18);
    } else {
      roundedRect(-half, -half, size, size, size * 0.22);
    }
    ctx.fill();
    ctx.stroke();
    if (glyph === "forklift") {          // the forks point the way it faces
      ctx.fillStyle = ctx.strokeStyle;
      ctx.fillRect(size * 0.2, -size * 0.3, size * 0.36, size * 0.1);
      ctx.fillRect(size * 0.2, size * 0.2, size * 0.36, size * 0.1);
      return 0;
    }
    if (glyph === "rotor") {             // four rotors round the drone's body
      [[1, 1], [1, -1], [-1, 1], [-1, -1]].forEach(function (s) {
        ctx.beginPath();
        ctx.arc(s[0] * size * 0.34, s[1] * size * 0.34, size * 0.16, 0, Math.PI * 2);
        ctx.stroke();
      });
      return 0;
    }
    if (glyph === "arm") {               // a fixed base: no front
      ctx.fillStyle = ctx.strokeStyle;
      ctx.beginPath();
      ctx.arc(0, 0, size * 0.14, 0, Math.PI * 2);
      ctx.fill();
      return 0;
    }
    return glyph === "wide" ? size * 0.575 : half;
  }

  function drawBadge(x, y, r, text) {
    ctx.save();
    ctx.fillStyle = "#0f1417";
    ctx.strokeStyle = COLORS.amber;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.arc(x, y, r, 0, Math.PI * 2);
    ctx.fill();
    ctx.stroke();
    ctx.fillStyle = COLORS.amber;
    ctx.font = "700 " + Math.max(7, Math.round(r * 1.5)) + "px 'Saira Condensed', sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(text, x, y + 0.5);
    ctx.restore();
  }

  function drawRobots(robots) {
    var cell = geometry.cell;
    robots.forEach(function (robot) {
```

**Replace in** `frontend/app.js`:

```javascript

      ctx.save();
      // shadow
      ctx.fillStyle = "rgba(0,0,0,0.45)";
      ctx.beginPath();
      ctx.ellipse(cx, cy + size * 0.42, size * 0.44, size * 0.16, 0, 0, Math.PI * 2);
```

with:

```javascript
      var flying = robot.layer === "AIR";

      ctx.save();
      // shadow (a flying drone's falls further below it, and fainter)
      var drop = flying ? Math.min(cell * 0.45, (robot.altitude_m || 0) * cell * 0.12) : 0;
      ctx.fillStyle = flying ? "rgba(0,0,0,0.3)" : "rgba(0,0,0,0.45)";
      ctx.beginPath();
      ctx.ellipse(cx, cy + size * 0.42 + drop, size * 0.44, size * 0.16, 0, 0, Math.PI * 2);
```

**Replace in** `frontend/app.js`:

```javascript
      var r = size * 0.22;
      ctx.beginPath();
      ctx.moveTo(-size / 2 + r, -size / 2);
      ctx.arcTo(size / 2, -size / 2, size / 2, size / 2, r);
      ctx.arcTo(size / 2, size / 2, -size / 2, size / 2, r);
      ctx.arcTo(-size / 2, size / 2, -size / 2, -size / 2, r);
      ctx.arcTo(-size / 2, -size / 2, size / 2, -size / 2, r);
      ctx.closePath();
      ctx.fill();
      ctx.stroke();

      // heading notch
      ctx.fillStyle = ctx.strokeStyle;
      ctx.beginPath();
      ctx.moveTo(size * 0.5, 0);
      ctx.lineTo(size * 0.22, -size * 0.18);
      ctx.lineTo(size * 0.22, size * 0.18);
      ctx.closePath();
      ctx.fill();
      ctx.restore();

      // carried box rides on the chassis
```

with:

```javascript
      var front = drawChassis(FM.robotGlyph(robot), size);

      // heading notch
      if (front) {
        ctx.fillStyle = ctx.strokeStyle;
        ctx.beginPath();
        ctx.moveTo(front, 0);
        ctx.lineTo(front - size * 0.28, -size * 0.18);
        ctx.lineTo(front - size * 0.28, size * 0.18);
        ctx.closePath();
        ctx.fill();
      }
      ctx.restore();

      // the type letter, on robots with a floor profile only (Plan ruling 10)
      var letter = FM.robotLetter(robot);
      if (letter) {
        ctx.save();
        ctx.fillStyle = COLORS.ink;
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        if (robot.carrying_box) {        // the box rides on top: the letter moves to a corner
          ctx.font = "700 " + Math.max(7, Math.round(cell * 0.22)) + "px 'Saira Condensed', sans-serif";
          ctx.fillText(letter, cx - size * 0.3, cy + size * 0.28);
        } else {
          ctx.font = "700 " + Math.max(8, Math.round(cell * 0.36)) + "px 'Saira Condensed', sans-serif";
          ctx.fillText(letter, cx, cy + 0.5);
        }
        ctx.restore();
      }

      // an amber ring while it holds a physical wait (robot.wait_reason)
      if (robot.wait_reason) {
        ctx.save();
        ctx.strokeStyle = COLORS.amber;
        ctx.lineWidth = Math.max(1.5, cell * 0.07);
        ctx.beginPath();
        ctx.arc(cx, cy, size * 0.68, 0, Math.PI * 2);
        ctx.stroke();
        ctx.restore();
      }

      // carried box rides on the chassis, with its kind badge on the new floor
```

**Replace in** `frontend/app.js`:

```javascript
          boxLabel(robot.carrying_box).replace(/^Box-/, ""));
      }
```

with:

```javascript
          boxLabel(robot.carrying_box).replace(/^Box-/, ""));
        var badge = FM.kindBadge(robot, findBox(robot.carrying_box));
        if (badge) drawBadge(cx + size * 0.25, cy - size * 0.35, Math.max(4, size * 0.17), badge);
      }
```

**Replace in** `frontend/app.js`:

```javascript
      drawRoutes();
      drawBoxes();
      drawRobots();
```

with:

```javascript
      drawNoFly();
      drawConveyor();
      drawArms();
      drawRoutes();
      drawBoxes();
      var layers = FM.robotLayers(state.robots, showAir);
      drawRobots(layers.ground);
      drawPeople();
      drawRobots(layers.air);   // the air layer draws above everything (spec §12)
```

**Replace in** `frontend/app.js`:

```javascript
    LEGEND.forEach(function (item) {
```

with:

```javascript
    FM.legendItems(state.cellTypes, COLORS).forEach(function (item) {
```

**Replace in** `frontend/app.js`:

```javascript
      node.appendChild(span);
    });
  }
```

with:

```javascript
      node.appendChild(span);
    });
  }

  // The "Air layer" toggle only appears on a floor that has one.
  function renderAirToggle() {
    $("airToggleWrap").hidden = !(state.noFly.length || state.robots.some(FM.isDrone));
  }
```

**Replace in** `frontend/app.js`:

```javascript
      state.layout = snapshot.warehouse;
      if (changed) resizeCanvas();
```

with:

```javascript
      state.layout = snapshot.warehouse;
      state.cellTypes = snapshot.cell_types || [];
      state.noFly = FM.noFlyCells(snapshot.no_fly_cells);
      layoutChanged();
      if (changed) resizeCanvas();
```

**Replace in** `frontend/app.js`:

```javascript
    state.robotStatistics = snapshot.robot_statistics || [];
    state.options = snapshot.options || state.options;
```

with:

```javascript
    state.robotStatistics = snapshot.robot_statistics || [];
    state.equipment = snapshot.equipment || null;   // null on classic: no conveyor
    state.options = snapshot.options || state.options;
```

**Replace in** `frontend/app.js`:

```javascript
      renderCi();
      syncOptions();
```

with:

```javascript
      renderCi();
      renderAirToggle();
      syncOptions();
```

**Replace in** `frontend/app.js`:

```javascript
      $("falseSuccessRiskValue").textContent = this.value + "%";
    });

    $("estopBtn").addEventListener("click", function () {
```

with:

```javascript
      $("falseSuccessRiskValue").textContent = this.value + "%";
    });

    $("airToggle").addEventListener("change", function () { showAir = this.checked; });

    $("estopBtn").addEventListener("click", function () {
```


- [ ] **Step 6: Run the new tests**

Run: `.venv/bin/python -m pytest -o addopts="" -q backend/test_floor_model_js.py`
Expected: `5 passed` (the JS file itself reports `24 passed, 0 failed`).

- [ ] **Step 7: Run the full suite**

Run: `.venv/bin/python -m pytest -o addopts="" -q`
Expected: `2 failed, 891 passed` (Task 8's count plus this task's 5 tests; the two failures are the allowed ones).

- [ ] **Step 8: Commit**

```bash
git add frontend/floor_model.js frontend/app.js frontend/index.html frontend/style.css frontend/tests/floor_model_test.js backend/test_floor_model_js.py
git commit -m "feat: the dashboard draws the floor from its layout — glyphs, air layer, arms, people, conveyor

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 9: Browser check (controller)**

Start the new floor with `WAREHOUSE_PORT=5070 .venv/bin/python -m backend.app` and, separately, the classic floor with `WAREHOUSE_LAYOUT=classic WAREHOUSE_PORT=5071 .venv/bin/python -m backend.app` (not 5050, which is the user's own server, nor 5060, which Chromium blocks). The shift starts paused (Plan ruling 6) and its button comes in Task 10, so start it with `curl -X POST -H 'Content-Type: application/json' -d '{}' http://127.0.0.1:5070/api/shift/start`. At desktop width (≥ 1500 px):

1. Classic (5071): the floor, zone frames and labels, colours and legend (Racking, Pick face, Charging, Loading, Unloading, Packing, Parking, Restricted, Route) look exactly as before Task 9; robots are the plain rounded squares with no letter; there is no "Air layer" switch; no console errors.
2. New floor (5070): the legend lists the new floor's types; pallet racks and tote shelves are hatched, the restricted area striped, the walkway's label reads upright along x=17; aisles and the patrol loop have no frame.
3. Every robot shows its letter: A on the TR50s, F on the PF1200s, H on the HH300, K on the PK30, S on the SC1, D on both IX2s, R on both CX10s, U on the H1, each with its glyph (forklift forks, wide hauler, diamond scout, rotor drone, round humanoid, arm base).
4. With the shift running, a robot carrying a box shows a P/T/I/C badge on it, and a robot in a physical wait (the humanoid's SUPERVISOR_ABSENT, a forklift's PERSON_IN_AISLE, a robot at a crossing) has an amber ring.
5. Drones draw above people and every other robot; violet ✕ cells cover the docks, the pack cells and the restricted area. Untick "Air layer": the drones, their routes and the overlay vanish; tick it: they return.
6. Each pack cell is framed cyan with the arm's eight neighbouring cells tinted; while a person stands in a pack cell during a PACK_ORDER (a jam on the arm's working cell, (23,15) or (22,15), sends Mateo in to clear it), that frame turns amber and dashed and the arm gets an amber ring.
7. People are white dots with initials (Sa, Le, NH, MS, AK, JB, KN, RC) in their zones; a person walking between zones is a cyan dot on the walkway (x=17).
8. Items glide along the conveyor (y=15). `curl -X POST -H 'Content-Type: application/json' -d '{}' http://127.0.0.1:5070/api/faults/conveyor_jam`: within a few seconds a conveyor cell turns red and the line behind it backs up.

At 375 px (device emulation): both floors load and draw with no console errors and the switch can be reached. (The floor only scales down to fit and the panels only stack from Task 10; a floor wider than the screen is expected here.)

---

### Task 10: The dashboard panels — robot panel, shift panel, job forms, fleet link, phone width

The rest of spec §12 on the dashboard, and what §16's browser check items 1–5 need. Clicking a robot on the floor opens a robot panel: model, asset and limits (payload, reach, clearance, from `robot.mobility`), its current job, step and wait reason, and a link to its inventory record on `fleet.html`; the selected robot gets a dashed cyan ring. On the new floor a shift panel shows the clock, Start and Pause (the shift starts paused, Plan ruling 6), the pace, orders in flight, throughput, backlog, failed orders with their reasons and safety escalations, from `snapshot.shift` and the Task 5 routes, plus an "Inject fault" control (Plan ruling 9) for §16's jam check. The task form takes each type's fields from `options().task_types[*].fields` (Task 6) instead of the hard-coded `TASK_FIELDS`, with an input for every job field and the type's guide line, so every new job type can be created from the dashboard; the older types are sent exactly as before. A person still walking back from a job that ended early (plan 1b T10 minor) — or back from a cleared jam — reads RETURNING. `fleet.html` gives each robot on the floor an "On the floor" link, `/?robot=<id>`, which opens the dashboard with that robot's panel, and opens a robot's drawer for `/fleet.html?asset=<id>`. At 375 px the floor scales down to its panel's width, the panels stack in one column and nothing is wider than the screen. The pure parts (`robotPanel`, `shiftPanel`, `taskFields`, `taskPayload`, `fieldChoices`, `personStatus`, `queryParam`, `hitRobot`) go in `floor_model.js` and are tested under `jsc` against real snapshots of both floors.

**Files:**
- Modify: `frontend/floor_model.js` (the panel helpers, before the exports)
- Modify: `frontend/app.js` (state, `JOB_INPUTS` in place of `TASK_FIELDS`, `STATUS_CHIP`, `findRobot`, `syncOptions`, `applyTaskFieldVisibility`, `renderOperators`, `resizeCanvas`, the selection ring in `drawRobots`, the new robot and shift panels, `applyState`, `flushDom`, the task form's submit, `wire`, `boot`)
- Modify: `frontend/index.html` (the shift panel, the task form's guide line and job fields, the robot panel)
- Modify: `frontend/style.css` (the two panels, the guide line, the phone-width rules)
- Modify: `frontend/fleet.js` (the "On the floor" links, `?asset=`)
- Test: `frontend/tests/panels_test.js` (create), `backend/test_panels_js.py` (create)

**Interfaces:**
- Consumes (Task 5): `snapshot.shift` (`ShiftEngine.panel()`: `status`, `clock`, `config.pace`/`seed`, `orders`, `throughput_per_hour`, `in_flight: int`, `backlog: int`, `failed_orders: [{"order_id", "kind", "reason"}]`, `exceptions: [{"type", "message", "tick"}]`; None on classic); `POST /api/shift/start`, `/pause`, `/config` (`{"pace"}`), each answering `{"shift": panel}`; `POST /api/faults/<kind>` (`{"count": 1}`). (Task 6): `options().task_types[*]` = `{"id", "label", "fields", "guide"}`, fields from `box`, `box_ids`, `source`, `destination`, `priority`, `agent`, `operator`, `slot`, `quantity`, `station`, `face`, `dock`, `lane`, `order_id`, `pack_cell`, `segment`; `POST /api/tasks` drops a blank job field, turns `"2"` into 2 and refuses a field the type doesn't take. (Task 9): `FloorModel`, `findBox`, `drawRobots`, `showAir`, `render.robots`. Existing: `GET /api/robots` (`asset_id`), `backend.faults.FAULT_RISKS`, operator `status`/`current_task`/`transit_to`, task `params.phase` (human jobs).
- Produces, on `window.FloorModel`:
  - `robotPanel(robot, tasks) -> {id, name, letter, status, waitReason, identity: [[label, value]], limits: [[label, value]], job: [[label, value]], inventoryUrl}` (`tasks`, the snapshot's, names the job; `inventoryUrl` is `/fleet.html?asset=<asset_id>`, or null).
  - `shiftPanel(shift) -> {status, running, clock, pace, seed, inFlight, backlog, done, throughput, failed: [{id, kind, reason}], escalations: [{message, tick}]} | null`.
  - `FAULT_KINDS` (`[[kind, label]]`, `FAULT_RISKS`' keys), `FORM_FIELDS`, `titleize(value)`.
  - `taskFields(options, type) -> [field]` (Task 6's `fields`, `box_id`/`operator_id`/`agent_id` read as `box`/`operator`/`agent`; the dashboard's old table when `options` names none), `taskGuide(options, type) -> string`, `taskPayload(type, fields, values) -> body`, `fieldChoices(layout, field) -> [{value, label}]` (station, pack_cell, lane, dock).
  - `personStatus(operator, tasks) -> status` (`"RETURNING"` for a person ON_TASK on an ended job or in a job's RETURN phase), `queryParam(search, name) -> string | null`, `hitRobot(points, x, y) -> id | null`.
  - The dashboard reads `/?robot=<robot id or name>`; `fleet.html` reads `?asset=<asset id>`.

- [ ] **Step 1: Write the failing tests**

**Create** `frontend/tests/panels_test.js`:

```javascript
/* Unit tests for the panel helpers of frontend/floor_model.js (multi-
   embodiment spec §12 Robot panel, Shift panel, Fleet page; the task form).
   backend/test_panels_js.py runs them:
     jsc <fixtures.js> frontend/floor_model.js <this file>
   where FIXTURES = {classic, dc, faultKinds, indexHtml}: real /api/state
   snapshots of both floors, backend/faults.py's fault kinds and
   frontend/index.html. Prints one line per failure, then "N passed, M
   failed"; any failure ends in an exception, so jsc exits non-zero. ES5. */
var passed = 0, failed = 0;

function test(name, body) {
  try {
    body();
    passed++;
  } catch (error) {
    failed++;
    print("FAIL " + name + ": " + error.message);
  }
}

function eq(actual, expected, what) {
  var a = JSON.stringify(actual), b = JSON.stringify(expected);
  if (a !== b) throw new Error((what ? what + ": " : "") + "expected " + b + ", got " + a);
}

function ok(value, what) {
  if (!value) throw new Error(what || "expected a true value");
}

function clone(value) { return JSON.parse(JSON.stringify(value)); }

function named(list, name) {
  for (var i = 0; i < list.length; i++) if (list[i].name === name) return list[i];
  throw new Error("nothing named " + name);
}

var FM = FloorModel;
var classic = FIXTURES.classic, dc = FIXTURES.dc;

/* ------------------------------------------------------------ robot panel */
test("the robot panel shows an AMR's model, asset, limits and current job", function () {
  var amr = named(dc.robots, "TR50-201");
  var view = FM.robotPanel(amr, dc.tasks);
  eq([view.name, view.letter, view.status], ["TR50-201", "A", "MOVING"]);
  eq(view.identity, [["Model", "AC-TR50"], ["Asset", "AST-000201"], ["Type", "AMR"], ["Layer", "ground"]]);
  eq(view.limits, [["Payload", "50 kg"], ["Reach", "shelf level 1 (lift 0.6 m)"], ["Clearance", "NARROW"]]);
  eq(view.job, [["Job", amr.current_task + " · Tote to station"], ["Step", "Navigate to slot TS-10-12-1"],
    ["Waiting for", "—"]]);
  eq(view.inventoryUrl, "/fleet.html?asset=AST-000201");
});

test("an arm's reach is its own, and it has no clearance class", function () {
  var view = FM.robotPanel(named(dc.robots, "CX10-210"), dc.tasks);
  eq(view.limits, [["Payload", "10 kg"], ["Reach", "1300 mm"], ["Clearance", "fixed station"]]);
  eq(view.job, [["Job", "none"], ["Step", "—"], ["Waiting for", "—"]]);
});

test("a drone in the air shows its layer and altitude", function () {
  var view = FM.robotPanel(named(dc.robots, "IX2-208"), dc.tasks);
  eq(view.identity[3], ["Layer", "air, 2.5 m up"]);
  eq(view.limits[2], ["Clearance", "flies (air layer)"]);
});

test("a robot holding a physical wait shows why", function () {
  var amr = clone(named(dc.robots, "TR50-201"));
  amr.wait_reason = "PERSON_IN_AISLE";
  var view = FM.robotPanel(amr, dc.tasks);
  eq(view.waitReason, "PERSON_IN_AISLE");
  eq(view.job[2], ["Waiting for", "Person in aisle"]);
});

test("a job the snapshot no longer lists still shows by id, and its step from the robot", function () {
  var amr = clone(named(dc.robots, "TR50-201"));
  eq(FM.robotPanel(amr, []).job.slice(0, 2), [["Job", amr.current_task], ["Step", "Navigate"]]);
});

test("a classic robot has no floor profile to show, and links to its record if it has one", function () {
  var robot = classic.robots[0];
  var view = FM.robotPanel(robot, classic.tasks);
  eq([view.letter, view.limits], [null, []]);
  eq(view.identity[2], ["Type", "AMR"]);
  eq(view.inventoryUrl, robot.asset_id ? "/fleet.html?asset=" + robot.asset_id : null);
  var unbound = clone(robot);
  unbound.asset_id = null;
  eq(FM.robotPanel(unbound, []).inventoryUrl, null);
});

/* ------------------------------------------------------------ shift panel */
test("the classic floor has no shift panel", function () {
  eq(classic.shift, null);
  eq(FM.shiftPanel(classic.shift), null);
});

test("the new floor's shift panel starts paused at 06:00", function () {
  // Plan ruling 6: the shift starts paused; Start (POST /api/shift/start) begins it.
  var view = FM.shiftPanel(dc.shift);
  eq([view.status, view.running, view.clock, view.pace, view.seed], ["PAUSED", false, "06:00", 1, 42]);
  eq([view.inFlight, view.backlog, view.done, view.throughput], [0, 0, 0, 0]);
  eq([view.failed, view.escalations], [[], []]);
});

test("the shift panel lists failed orders with reasons, and only safety escalations", function () {
  var shift = clone(dc.shift);
  shift.status = "RUNNING";
  shift.in_flight = 3;
  shift.backlog = 2;
  shift.throughput_per_hour = 12.5;
  shift.orders.CUSTOMER.DONE = 4;
  shift.orders.INBOUND.DONE = 1;
  shift.failed_orders = [{ order_id: "ORD-0007", kind: "CUSTOMER", reason: "TOTE-3 has no unit left" },
    { order_id: "ORD-0002", kind: "PALLET", reason: null }];
  shift.exceptions = [
    { type: "SAFETY_WAIT_ESCALATED", message: "H1-212 waited 120 s: SUPERVISOR_ABSENT", tick: 900 },
    { type: "ORDER_FAILED", message: "ORD-0007 failed", tick: 880 },
    { type: "STOCK_VARIANCE_DETECTED", message: "PR-10-05-0 is short 3", tick: 600 }];
  var view = FM.shiftPanel(shift);
  eq([view.running, view.inFlight, view.backlog, view.done, view.throughput], [true, 3, 2, 5, 12.5]);
  eq(view.failed, [{ id: "ORD-0007", kind: "CUSTOMER", reason: "TOTE-3 has no unit left" },
    { id: "ORD-0002", kind: "PALLET", reason: "no reason recorded" }]);
  eq(view.escalations, [{ message: "H1-212 waited 120 s: SUPERVISOR_ABSENT", tick: 900 }]);
  shift.in_flight = [{}, {}];
  eq(FM.shiftPanel(shift).inFlight, 2, "a list of orders counts as its length");
});

test("Inject fault offers exactly the backend's fault kinds", function () {
  // Fails if backend/faults.py FAULT_RISKS gains or renames a kind the select doesn't offer.
  eq(FM.FAULT_KINDS.map(function (kind) { return kind[0]; }).sort(), FIXTURES.faultKinds);
});

/* -------------------------------------------------------------- task form */
test("the task form shows each type's fields from options()", function () {
  eq(FM.taskFields(dc.options, "TOTE_TO_STATION"), ["box", "station", "priority"]);
  eq(FM.taskFields(dc.options, "MANUAL_PICK"), ["box", "quantity", "order_id", "pack_cell", "operator", "priority"]);
  eq(FM.taskFields(dc.options, "PATROL"), ["priority"]);
  eq(FM.taskFields(dc.options, "NOT_A_TYPE"), []);
});

test("every field either floor's options name is one the form can show", function () {
  // Fails if options()["task_types"][*].fields names a field index.html has no input for.
  [classic, dc].forEach(function (floor) {
    floor.options.task_types.forEach(function (entry) {
      FM.taskFields(floor.options, entry.id).forEach(function (field) {
        ok(FM.FORM_FIELDS.indexOf(field) !== -1, entry.id + " names an unknown field " + field);
      });
    });
  });
  FM.FORM_FIELDS.forEach(function (field) {
    ok(FIXTURES.indexHtml.indexOf('data-when="' + field + '"') !== -1, "index.html has no " + field + " field");
  });
});

test("the older task types keep the fields the dashboard always showed", function () {
  classic.options.task_types.forEach(function (entry) {
    eq(FM.taskFields(classic.options, entry.id), FM.taskFields(null, entry.id), entry.id);
  });
  eq(FM.taskFields(null, "PICK_AND_DELIVER"), ["box", "source", "destination", "priority"]);
});

test("a type's fields read payload names as form fields and never list the robot", function () {
  var options = { task_types: [{ id: "X", fields: ["box_id", "robot_id", "operator_id", "quantity", "box"] }] };
  eq(FM.taskFields(options, "X"), ["box", "operator", "quantity"]);
});

test("the form shows a type's guide", function () {
  ok(FM.taskGuide(dc.options, "TOTE_TO_STATION").indexOf("station") !== -1, "the tote job's guide");
  eq(FM.taskGuide(null, "TOTE_TO_STATION"), "");
});

test("an older task type is sent exactly as the form always sent it", function () {
  var values = { robot: "AUTO", box: "box_001", source: "", destination: "loading_zone", priority: "NORMAL",
    agent: "AUTO", operator: "AUTO", dual_signoff: false, second_operator: "AUTO", slot: "PR-10-05-0" };
  eq(FM.taskPayload("PICK_AND_DELIVER", FM.taskFields(classic.options, "PICK_AND_DELIVER"), values),
    { type: "PICK_AND_DELIVER", robot_id: "AUTO", box_id: "box_001", destination: "loading_zone", priority: "NORMAL" });
  values.dual_signoff = true;
  values.second_operator = "operator_002";
  eq(FM.taskPayload("HUMAN_INSPECTION", ["operator"], values),
    { type: "HUMAN_INSPECTION", robot_id: "AUTO", operator_id: "AUTO", dual_signoff: true,
      second_operator_id: "operator_002" });
});

test("a job's own fields go only when filled in, a whole quantity as a number", function () {
  var values = { robot: "AUTO", box: "box_009", priority: "HIGH", quantity: " 3 ", order_id: "  ",
    pack_cell: "pack_cell_2", station: "pick_station_1", operator: "AUTO" };
  eq(FM.taskPayload("PICK_ITEMS", FM.taskFields(dc.options, "PICK_ITEMS"), values),
    { type: "PICK_ITEMS", robot_id: "AUTO", box_id: "box_009", priority: "HIGH", quantity: 3, pack_cell: "pack_cell_2" });
  values.quantity = "2.5";   // the API answers "quantity must be a whole number of at least 1"
  eq(FM.taskPayload("PICK_ITEMS", ["quantity"], values).quantity, "2.5");
});

test("a job's station, pack cell, lane and dock are chosen from the floor's zones", function () {
  function keys(field, floor) {
    return FM.fieldChoices(floor.warehouse, field).map(function (c) { return c.value; });
  }
  eq(keys("station", dc), ["pick_station_1", "pick_station_2"]);
  eq(keys("pack_cell", dc), ["pack_cell_1", "pack_cell_2"]);
  eq(FM.fieldChoices(dc.warehouse, "lane"), [{ value: "dock_4", label: "Dock 4" }, { value: "dock_5", label: "Dock 5" }]);
  eq(keys("dock", dc), ["dock_3"]);
  eq(keys("slot", dc), []);
  ["station", "pack_cell", "lane", "dock"].forEach(function (field) { eq(keys(field, classic), [], field); });
});

/* ----------------------------------------------------------------- people */
test("a person walking back out of a job that ended early reads returning", function () {
  // Plan 1b T10 minor: Sam's CLEAR_JAM was cancelled once he got there; he is
  // still ON_TASK on the ended job until he is back.
  var sam = named(dc.operators, "Sam"), lee = named(dc.operators, "Lee");
  eq([sam.status, sam.transit_to], ["ON_TASK", "intake_staging"]);
  eq(FM.personStatus(sam, dc.tasks), "RETURNING");
  eq(FM.personStatus(lee, dc.tasks), "ON_TASK", "Lee is still walking to her jam");
  var tasks = clone(dc.tasks);
  tasks.forEach(function (task) { if (task.id === lee.current_task) task.params.phase = "RETURN"; });
  eq(FM.personStatus(lee, tasks), "RETURNING", "walking back after clearing it");
  classic.operators.forEach(function (o) { eq(FM.personStatus(o, classic.tasks), o.status, o.name); });
});

/* ------------------------------------------------------- links and clicks */
test("the dashboard reads the robot to select from its query string", function () {
  eq(FM.queryParam("?robot=robot_003", "robot"), "robot_003");
  eq(FM.queryParam("?x=1&robot=TR50%2D201", "robot"), "TR50-201");
  eq(FM.queryParam("?asset=AST-000201", "robot"), null);
  eq(FM.queryParam("", "robot"), null);
});

test("a click selects the robot drawn nearest it, within a cell", function () {
  var points = [{ id: "a", x: 3, y: 4 }, { id: "b", x: 3.3, y: 4 }, { id: "c", x: 10, y: 10 }];
  eq(FM.hitRobot(points, 3.2, 4), "b");
  eq(FM.hitRobot(points, 2.8, 4.1), "a");
  eq(FM.hitRobot(points, 6, 6), null);
});

test("the page has the shift panel, the robot panel and the guide line", function () {
  ["shiftPanel", "shiftStartBtn", "shiftPauseBtn", "shiftPace", "shiftPaceBtn", "shiftStats", "shiftFailed",
    "shiftEscalations", "faultKind", "faultBtn", "robotPanel", "robotPanelBody", "robotPanelClose", "taskGuide"]
    .forEach(function (id) { ok(FIXTURES.indexHtml.indexOf('id="' + id + '"') !== -1, "index.html has no #" + id); });
});

print(passed + " passed, " + failed + " failed");
if (failed) throw new Error(failed + " panel test(s) failed");
```

**Create** `backend/test_panels_js.py`:

```python
"""The dashboard panels (multi-embodiment spec §12), checked under JavaScriptCore.

frontend/tests/panels_test.js tests the panel helpers of frontend/floor_model.js
(robot panel, shift panel, the task form's fields and payload, a person
walking back, the fleet link) against FIXTURES: real /api/state snapshots of
both floors built here through the public API — a distribution-centre twin
with a robot on a job, a person walking back out of a cancelled CLEAR_JAM and
another walking to one — plus the page's HTML and the backend's fault kinds.
This file also checks that frontend/fleet.js compiles and stays ES5. Skipped
where jsc is not installed.
"""
from __future__ import annotations

import json
import os
import re
import subprocess

import pytest

from backend import people
from backend.digital_twin import DigitalTwin
from backend.faults import FAULT_RISKS
from backend.models import SimulationStatus
from backend.simulator import Simulator

JSC = "/System/Library/Frameworks/JavaScriptCore.framework/Versions/A/Helpers/jsc"
FRONTEND = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend")

pytestmark = pytest.mark.skipif(not os.path.exists(JSC), reason="JavaScriptCore (jsc) is not installed")


def run_jsc(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([JSC, *args], capture_output=True, text=True, timeout=120)


def state_of(twin: DigitalTwin) -> dict:
    """What GET /api/state sends, through JSON as the browser gets it."""
    return json.loads(json.dumps(twin.snapshot(include_layout=True), default=str))


def classic_state(tmp_path) -> dict:
    twin = DigitalTwin(log_dir=str(tmp_path / "classic_logs"), data_dir=str(tmp_path / "classic_data"),
                       persist_logs=False, demo=True, demo_tasks=False)
    return state_of(twin)


def distribution_center_state(tmp_path) -> dict:
    twin = DigitalTwin(log_dir=str(tmp_path / "dc_logs"), data_dir=str(tmp_path / "dc_data"),
                       persist_logs=False, demo=True, demo_tasks=False, layout="distribution_center")
    twin.add_robot(name="TR50-201", asset_id="AST-000201", position=(8, 13))
    twin.add_robot(name="CX10-210", asset_id="AST-000210")
    drone = twin.add_robot(name="IX2-208", asset_id="AST-000208", position=(20, 2))
    sam = twin.add_operator(name="Sam", worker_id="E-10001")
    lee = twin.add_operator(name="Lee", worker_id="E-10002")
    people.place(twin, sam, "intake_staging")
    people.place(twin, lee, "pick_station_2")
    simulator = Simulator(twin)
    twin.simulation_status = SimulationStatus.RUNNING
    twin.equipment.jam((21, 15))
    jam = twin.tasks.create_task({"type": "CLEAR_JAM", "segment": "21,15", "operator_id": sam.id})
    for _ in range(2000):
        if jam.params["phase"] == "WORK":
            break
        simulator.tick()
    assert jam.params["phase"] == "WORK", "Sam never reached the jam"
    twin.tasks.cancel_task(jam.id)       # Sam walks back out, still ON_TASK on the ended job
    twin.equipment.jam((19, 15))
    twin.tasks.create_task({"type": "CLEAR_JAM", "segment": "19,15", "operator_id": lee.id})
    tote = twin.add_box(name="TOTE-1", kind="TOTE", sku="SKU-001", quantity=12, weight=9.0, slot="TS-10-12-1")
    twin.tasks.create_task({"type": "TOTE_TO_STATION", "box_id": tote.id})
    for _ in range(3):
        simulator.tick()
    twin.set_robot_layer(drone.id, "AIR", 2.5)
    return state_of(twin)


def test_the_panels_read_both_floors(tmp_path):
    fixtures = tmp_path / "fixtures.js"
    fixtures.write_text("var FIXTURES = " + json.dumps({
        "classic": classic_state(tmp_path), "dc": distribution_center_state(tmp_path),
        "faultKinds": sorted(FAULT_RISKS),
        "indexHtml": open(os.path.join(FRONTEND, "index.html"), encoding="utf-8").read(),
    }) + ";\n")
    result = run_jsc(str(fixtures), os.path.join(FRONTEND, "floor_model.js"),
                     os.path.join(FRONTEND, "tests", "panels_test.js"))
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "FAIL" not in output, output
    assert re.search(r"\b\d+ passed, 0 failed\b", output), output


def test_the_fleet_page_script_compiles():
    # new Function parses the whole file without running it (it needs a page).
    result = run_jsc("-e", "new Function(readFile(arguments[0]));", "--", os.path.join(FRONTEND, "fleet.js"))
    assert result.returncode == 0, result.stdout + result.stderr


def test_the_fleet_page_stays_es5():
    source = open(os.path.join(FRONTEND, "fleet.js"), encoding="utf-8").read()
    assert "=>" not in source, "fleet.js uses an arrow function"
    assert not re.search(r"\b(let|const)\s+[A-Za-z_$][\w$]*\s*=", source), "fleet.js declares with let/const"
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/bin/python -m pytest -o addopts="" -q backend/test_panels_js.py`
Expected: `1 failed, 2 passed` — `FAIL the robot panel shows an AMR's model, asset, limits and current job: FM.robotPanel is not a function` (and every other panel test likewise). `fleet.js` already compiles and is ES5.

- [ ] **Step 3: The panel helpers**

They go between the conveyor helper and the exports.

**Replace in** `frontend/floor_model.js`:

```javascript

  /* ------------------------------------------------------------- exports */
```

with:

```javascript

  /* -------------------------------------------------------------- panels */
  var TERMINAL = { COMPLETED: true, FAILED: true, CANCELLED: true };

  /* How the robot panel names each robot type. */
  var CLASS_LABELS = {
    AMR: "AMR", FORKLIFT: "Forklift", HEAVY_HAULER: "Heavy hauler", PICKER: "Picker",
    SCOUT: "Scout", DRONE: "Drone", ARM: "Arm", HUMANOID: "Humanoid"
  };

  /* "TOTE_TO_STATION" → "Tote to station". */
  function titleize(value) {
    var text = String(value || "").replace(/_/g, " ").toLowerCase();
    return text.charAt(0).toUpperCase() + text.slice(1);
  }

  function findById(list, id) {
    for (var i = 0; i < (list || []).length; i++) if (list[i].id === id) return list[i];
    return null;
  }

  /* What the robot panel shows (spec §12 Robot panel): the model and asset,
     the body's limits from its floor profile (payload, reach, clearance),
     the current job, its step and any wait, and the link to the robot's
     inventory record on the fleet page. `tasks` (the snapshot's) names the
     job; a robot with no floor profile (classic) has no limits to show. */
  function robotPanel(robot, tasks) {
    var mobility = robot.mobility;
    var limits = [];
    if (mobility) {
      limits.push(["Payload", mobility.max_payload_kg + " kg"]);
      if (mobility.movement === "FIXED") {
        limits.push(["Reach", mobility.reach_mm ? mobility.reach_mm + " mm" : "—"]);
      } else {
        limits.push(["Reach", "shelf level " + mobility.max_shelf_level + " (lift " + mobility.max_lift_m + " m)"]);
      }
      limits.push(["Clearance", mobility.clearance ||
        (mobility.movement === "AIR" ? "flies (air layer)" : "fixed station")]);
    }
    var task = robot.current_task ? findById(tasks, robot.current_task) : null;
    var job = robot.current_task
      ? robot.current_task + (task ? " · " + titleize(task.type) : "")
      : "none";
    var step = task && task.current_action ? task.current_action : (robot.activity ? titleize(robot.activity) : "—");
    return {
      id: robot.id,
      name: robot.name,
      letter: robotLetter(robot),
      status: robot.status,
      waitReason: robot.wait_reason || null,
      identity: [
        ["Model", robot.model_code || "—"],
        ["Asset", robot.asset_id || "—"],
        ["Type", CLASS_LABELS[mobility ? mobility.embodiment_class : robot.robot_class] ||
          titleize(mobility ? mobility.embodiment_class : robot.robot_class)],
        ["Layer", robot.layer === "AIR" ? "air, " + robot.altitude_m + " m up" : "ground"]
      ],
      limits: limits,
      job: [
        ["Job", job],
        ["Step", step],
        ["Waiting for", robot.wait_reason ? titleize(robot.wait_reason) : "—"]
      ],
      inventoryUrl: robot.asset_id ? "/fleet.html?asset=" + encodeURIComponent(robot.asset_id) : null
    };
  }

  function count(value) {
    if (typeof value === "number") return value;
    return value && value.length ? value.length : 0;
  }

  /* What the shift panel shows (spec §12 Shift panel), from the snapshot's
     shift (ShiftEngine.panel()); null on a floor with no shift (classic).
     in_flight and backlog may be counts or lists of orders. */
  function shiftPanel(shift) {
    if (!shift) return null;
    var done = 0;
    var orders = shift.orders || {};
    Object.keys(orders).forEach(function (kind) { done += orders[kind].DONE || 0; });
    return {
      status: shift.status,
      running: shift.status === "RUNNING",
      clock: shift.clock,
      pace: shift.config ? shift.config.pace : null,
      seed: shift.config ? shift.config.seed : null,
      inFlight: count(shift.in_flight),
      backlog: count(shift.backlog),
      done: done,
      throughput: shift.throughput_per_hour,
      failed: (shift.failed_orders || []).map(function (order) {
        return { id: order.order_id, kind: order.kind, reason: order.reason || "no reason recorded" };
      }),
      escalations: (shift.exceptions || []).filter(function (item) {
        return item.type === "SAFETY_WAIT_ESCALATED";
      }).map(function (item) { return { message: item.message, tick: item.tick }; })
    };
  }

  /* The fault kinds POST /api/faults/<kind> arms (backend/faults.py
     FAULT_RISKS), for the shift panel's "Inject fault" control. */
  var FAULT_KINDS = [
    ["conveyor_jam", "Conveyor jam"], ["grasp_fail", "Grasp failure"], ["handoff_loss", "Hand-off loss"],
    ["mis_sort", "Mis-sort"], ["misdeclared_weight", "Misdeclared weight"], ["scan_miscount", "Scan miscount"],
    ["wrong_level", "Wrong rack level"]
  ];

  /* Every field the task form can show (a data-when element in index.html). */
  var FORM_FIELDS = ["box", "box_ids", "source", "destination", "priority", "agent", "operator",
    "slot", "quantity", "station", "face", "dock", "lane", "order_id", "pack_cell", "segment"];
  /* The new job types' own fields (backend/jobs.py JOB_PARAM_KEYS), sent as typed. */
  var JOB_FIELDS = ["slot", "quantity", "station", "face", "dock", "lane", "order_id", "pack_cell", "segment"];
  /* A field named by its payload key is the form field it fills. */
  var FIELD_ALIASES = { box_id: "box", agent_id: "agent", operator_id: "operator" };
  /* The form's fields for each task type when options() names none (a
     backend from before plan 1c): what the dashboard has always shown. */
  var DEFAULT_TASK_FIELDS = {
    PICK_AND_DELIVER: ["box", "source", "destination", "priority"],
    MOVE_BOX: ["box", "source", "destination", "priority"],
    DELIVER_BOX: ["box", "destination", "priority"],
    PICK_BOX: ["box", "priority"],
    MOVE_ROBOT: ["destination", "priority"],
    CHARGE_ROBOT: ["priority"],
    STOP_ROBOT: [],
    RESUME_ROBOT: [],
    AGENT_INSPECTION: ["agent"],
    HUMAN_INSPECTION: ["operator"],
    MIXED_MAINTENANCE_MISSION: ["destination", "agent", "operator", "priority"],
    AGENT_REPLAN: ["agent"],
    AGENT_AUDIT: ["agent"],
    OPERATOR_APPROVAL: ["operator"],
    OPERATOR_MAINTENANCE_SIGNOFF: ["operator"],
    BATCH_DELIVER: ["box_ids", "destination", "priority"]
  };

  function taskTypeEntry(options, type) {
    var types = (options && options.task_types) || [];
    for (var i = 0; i < types.length; i++) if (types[i].id === type) return types[i];
    return null;
  }

  /* The form fields to show for a task type, from options().task_types[*]
     .fields (Task 6) — payload names (box_id) read as the form's (box), the
     robot selector always shown — else the dashboard's own table. */
  function taskFields(options, type) {
    var entry = taskTypeEntry(options, type);
    var fields = entry && entry.fields ? entry.fields : (DEFAULT_TASK_FIELDS[type] || []);
    var out = [];
    fields.forEach(function (field) {
      var name = FIELD_ALIASES[field] || field;
      if (name === "robot" || name === "robot_id") return;
      if (out.indexOf(name) === -1) out.push(name);
    });
    return out;
  }

  /* The task type's one-line payload guide, or "". */
  function taskGuide(options, type) {
    var entry = taskTypeEntry(options, type);
    return (entry && entry.guide) || "";
  }

  /* The POST /api/tasks body from the form's values (keyed by field name),
     sending only the type's fields. The older fields go exactly as the form
     always sent them; a new job's field goes only when it is filled in (an
     empty one takes the job's default), trimmed, and a whole-number quantity
     as a number (anything else is sent as typed, for the API to refuse). */
  function taskPayload(type, fields, values) {
    function has(field) { return fields.indexOf(field) !== -1; }
    var body = { type: type, robot_id: values.robot };
    if (has("box")) body.box_id = values.box;
    if (has("box_ids")) body.box_ids = values.box_ids || [];
    if (has("source") && values.source) body.source = values.source;
    if (has("destination")) body.destination = values.destination;
    if (has("priority")) body.priority = values.priority;
    if (has("agent")) body.agent_id = values.agent;
    if (has("operator")) {
      body.operator_id = values.operator;
      if (values.dual_signoff) {
        body.dual_signoff = true;
        if (values.second_operator && values.second_operator !== "AUTO") body.second_operator_id = values.second_operator;
      }
    }
    JOB_FIELDS.forEach(function (field) {
      var value = values[field];
      if (!has(field) || value === undefined || value === null || String(value).replace(/\s+/g, "") === "") return;
      var text = String(value).replace(/^\s+|\s+$/g, "");
      body[field] = field === "quantity" && /^\d+$/.test(text) ? Number(text) : text;
    });
    return body;
  }

  /* The choices for a job field's select, from the layout's zones: pick
     stations (a tote drop), pack cells (an arm), sorter lanes (its chutes)
     and the outbound pallet docks. [] for any other field. */
  function fieldChoices(layout, field) {
    var zones = (layout && layout.zones) || [];
    var choices = [];
    zones.forEach(function (zone) {
      var a = zone.attributes || {};
      if ((field === "station" && a.tote_drop) || (field === "pack_cell" && a.arm_cell) ||
          (field === "dock" && a.dock_role === "OUTBOUND_PALLETS")) {
        choices.push({ value: zone.key, label: zone.label });
      }
      if (field === "lane" && a.chutes) {
        a.chutes.forEach(function (key) {
          var dock = zones.filter(function (z) { return z.key === key; })[0];
          choices.push({ value: key, label: dock ? dock.label : key });
        });
      }
    });
    return choices;
  }

  /* An operator's status as the dashboard shows it: a person still walking
     back from a job (it ended early, or a jam is cleared and they are on the
     way back) reads RETURNING rather than ON_TASK. */
  function personStatus(operator, tasks) {
    if (operator.status !== "ON_TASK" || !operator.current_task) return operator.status;
    var task = findById(tasks, operator.current_task);
    if (task && (TERMINAL[task.status] || (task.params && task.params.phase === "RETURN"))) return "RETURNING";
    return operator.status;
  }

  /* The value of `name` in a query string ("?robot=robot_003"), or null. */
  function queryParam(search, name) {
    var pairs = String(search || "").replace(/^\?/, "").split("&");
    for (var i = 0; i < pairs.length; i++) {
      var pair = pairs[i].split("=");
      if (decodeURIComponent(pair[0]) === name && pair.length > 1) {
        return decodeURIComponent(pair.slice(1).join("=").replace(/\+/g, " "));
      }
    }
    return null;
  }

  /* The robot under a click, in cell units ({id, x, y} as drawn): the
     nearest within 0.6 of a cell, the one drawn last on a tie. */
  function hitRobot(points, x, y) {
    var best = null, bestDistance = 0.6;
    (points || []).forEach(function (point) {
      var distance = Math.sqrt((point.x - x) * (point.x - x) + (point.y - y) * (point.y - y));
      if (distance <= bestDistance) { best = point.id; bestDistance = distance; }
    });
    return best;
  }

  /* ------------------------------------------------------------- exports */
```

**Replace in** `frontend/floor_model.js`:

```javascript
    conveyorView: conveyorView
```

with:

```javascript
    conveyorView: conveyorView,
    FAULT_KINDS: FAULT_KINDS,
    FORM_FIELDS: FORM_FIELDS,
    titleize: titleize,
    robotPanel: robotPanel,
    shiftPanel: shiftPanel,
    taskFields: taskFields,
    taskGuide: taskGuide,
    taskPayload: taskPayload,
    fieldChoices: fieldChoices,
    personStatus: personStatus,
    queryParam: queryParam,
    hitRobot: hitRobot
```


- [ ] **Step 4: The page — the shift panel, the job fields, the robot panel, phone width**

The job fields' `data-when` names are Task 6's field names. The shift panel sits under the simulation controls and stays hidden on classic; the robot panel sits under the floor. At 480 px and below every grid drops to one column, a flex row lets its fields shrink (`min-width: 0`), and the notification panel is pinned inside the screen.

**Replace in** `frontend/index.html`:

```html
    </section>

    <section class="panel">
      <h2 class="panel-title">Create task</h2>
```

with:

```html
    </section>

    <!-- The shift engine (new floor only; spec §12 Shift panel). It starts
         paused: Start begins generating trucks, orders, counts and patrols. -->
    <section class="panel" id="shiftPanel" hidden>
      <h2 class="panel-title">Shift <span class="count" id="shiftClock"></span></h2>
      <div class="shift-head">
        <span class="chip" id="shiftStatus">paused</span>
        <div class="btn-row">
          <button class="btn primary small" type="button" id="shiftStartBtn">Start</button>
          <button class="btn small" type="button" id="shiftPauseBtn">Pause</button>
        </div>
      </div>
      <div class="field-row shift-row">
        <label class="field"><span>Pace <i>(&times; the shift's rates)</i></span><input id="shiftPace" type="number" min="0.1" step="0.5" /></label>
        <button class="btn small" type="button" id="shiftPaceBtn">Set pace</button>
      </div>
      <div class="stat-grid shift-stats" id="shiftStats"></div>
      <h3 class="shift-sub">Failed orders</h3>
      <ul class="shift-list" id="shiftFailed"></ul>
      <h3 class="shift-sub">Safety escalations</h3>
      <ul class="shift-list" id="shiftEscalations"></ul>
      <div class="field-row shift-row">
        <label class="field"><span>Inject fault <i>(once, at its next chance)</i></span><select id="faultKind"></select></label>
        <button class="btn small" type="button" id="faultBtn">Inject</button>
      </div>
      <p class="form-note" id="shiftNote" role="status"></p>
    </section>

    <section class="panel">
      <h2 class="panel-title">Create task</h2>
```

**Replace in** `frontend/index.html`:

```html
          <select name="type" id="taskType"></select>
        </label>
        <label class="field">
          <span>Robot</span>
```

with:

```html
          <select name="type" id="taskType"></select>
        </label>
        <p class="form-note task-guide" id="taskGuide"></p>
        <label class="field">
          <span>Robot</span>
```

**Replace in** `frontend/index.html`:

```html
          <select name="destination" id="taskDestination"></select>
        </label>
```

with:

```html
          <select name="destination" id="taskDestination"></select>
        </label>
        <label class="field" data-when="slot">
          <span>Slot <i>(blank = the job's default)</i></span>
          <input name="slot" id="taskSlot" placeholder="PR-10-05-0" />
        </label>
        <label class="field" data-when="station">
          <span>Station</span>
          <select name="station" id="taskStation"></select>
        </label>
        <label class="field" data-when="face">
          <span>Rack face <i>(x,y)</i></span>
          <input name="face" id="taskFace" placeholder="10,5" />
        </label>
        <label class="field" data-when="dock">
          <span>Dock</span>
          <select name="dock" id="taskDock"></select>
        </label>
        <label class="field" data-when="order_id">
          <span>Order <i>(optional)</i></span>
          <input name="order_id" id="taskOrder" placeholder="ORD-0001" />
        </label>
        <label class="field" data-when="quantity">
          <span>Quantity</span>
          <input name="quantity" id="taskQuantity" type="number" min="1" step="1" value="1" />
        </label>
        <label class="field" data-when="pack_cell">
          <span>Pack cell</span>
          <select name="pack_cell" id="taskPackCell"></select>
        </label>
        <label class="field" data-when="lane">
          <span>Sorter lane</span>
          <select name="lane" id="taskLane"></select>
        </label>
        <label class="field" data-when="segment">
          <span>Conveyor cell <i>(x,y)</i></span>
          <input name="segment" id="taskSegment" placeholder="22,15" />
        </label>
```

**Replace in** `frontend/index.html`:

```html
      </p>
    </div>
```

with:

```html
      </p>
    </div>

    <!-- The robot clicked on the floor (spec §12 Robot panel). -->
    <div class="panel robot-panel" id="robotPanel" hidden>
      <div class="map-head">
        <h2 class="panel-title">Robot <span class="count" id="robotPanelName"></span></h2>
        <button class="btn small" type="button" id="robotPanelClose">Close</button>
      </div>
      <div id="robotPanelBody"></div>
    </div>
```

**Replace in** `frontend/style.css`:

```css

/* --------------------------------------------------------------- scrollbars */
```

with:

```css

/* ------------------------------------------------------------- robot panel */
#floor { cursor: pointer; }
.robot-panel-head { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 9px; }
.robot-panel-grid { display: grid; gap: 8px 16px; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); margin-bottom: 9px; }
.robot-panel h4, .shift-sub {
  margin: 0 0 6px;
  font-family: var(--display);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.18em;
  text-transform: uppercase;
  color: var(--ink-2);
}
.robot-panel .kv dd { overflow-wrap: anywhere; }

/* ------------------------------------------------------------- shift panel */
.shift-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-bottom: 10px; }
.shift-row { align-items: flex-end; }
.shift-row .btn { flex: 0 0 auto; margin-bottom: 9px; }
.shift-stats { grid-template-columns: repeat(2, 1fr); margin-bottom: 4px; }
.shift-sub { margin-top: 10px; }
.shift-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 4px; max-height: 160px; overflow-y: auto; font-size: 12px; color: var(--ink-2); }
.shift-list li { padding: 4px 6px; background: var(--panel-2); border-left: 2px solid var(--fault); overflow-wrap: anywhere; }
.shift-list li.empty { background: transparent; border-left: 0; color: var(--faint); padding: 2px 0; }
.shift-list .mono { font-family: var(--mono); font-size: 11px; color: var(--amber); }
.task-guide { margin: -4px 0 9px; font-family: var(--mono); font-size: 11px; min-height: 0; overflow-wrap: anywhere; }

/* --------------------------------------------------------------- scrollbars */
```

**Replace in** `frontend/style.css`:

```css

@media (prefers-reduced-motion: reduce) {
```

with:

```css

/* Phone width (spec §12): the floor scales to fit (resizeCanvas in app.js),
   every panel stacks in one column, and nothing is wider than the screen. */
@media (max-width: 480px) {
  .shell { padding: 8px 8px 32px; gap: 10px; }
  .rail { gap: 10px; }
  .rail-left, .rail-right, .robot-grid, .task-board, .robot-panel-grid { grid-template-columns: minmax(0, 1fr); }
  .topbar { gap: 10px; padding: 8px 10px; }
  .topbar h1 { font-size: 19px; }
  .identity { min-width: 0; }
  .readout-item { flex: 1 1 30%; padding: 5px 8px; }
  .readout-item.wide { flex: 1 1 100%; }
  .field-row .field, .filter-row > * { min-width: 0; }
  .kv dd { overflow-wrap: anywhere; }
  .notif-panel { position: fixed; top: 60px; left: 8px; right: 8px; width: auto; }
  .log-row { grid-template-columns: 52px 40px 64px minmax(0, 1fr); }
}

@media (prefers-reduced-motion: reduce) {
```


- [ ] **Step 5: The dashboard — the panels, the task form, the scaling floor**

What changes in `frontend/app.js`, in file order: the state keeps the shift, and the robot the panel shows; `TASK_FIELDS` goes (its table is `FloorModel`'s fallback) and `JOB_INPUTS` names each job field's input; RETURNING gets a chip colour; `findRobot` finds a robot by id or name; `syncOptions` fills the station, pack-cell, lane and dock selects from the layout (a blank choice takes the job's default) and the fault kinds; `applyTaskFieldVisibility` shows the type's fields and guide; the operators table shows `personStatus`; `resizeCanvas` drops the 320 px and 16 px floors, so the floor scales to whatever width its panel has (at desktop widths nothing changes); the selected robot gets a dashed cyan ring; the robot panel, the canvas hit test and the shift panel and its POSTs are new; `applyState` reads `shift`; `flushDom` renders both panels; the task form's submit sends `FloorModel.taskPayload`; `wire` wires the canvas click, the panels' buttons and the fault control; `boot` opens `/?robot=<id>`'s panel.

**Replace in** `frontend/app.js`:

```javascript
    equipment: null
```

with:

```javascript
    equipment: null,
    shift: null
```

**Replace in** `frontend/app.js`:

```javascript
  var selectedTaskId = null;
  var optionsSignature = "";
```

with:

```javascript
  var selectedTaskId = null;
  var selectedRobotId = null;   // the robot the robot panel shows
  var optionsSignature = "";
```

**Replace in** `frontend/app.js`:

```javascript
  var TASK_FIELDS = {
    PICK_AND_DELIVER: ["box", "source", "destination", "priority"],
    MOVE_BOX: ["box", "source", "destination", "priority"],
    DELIVER_BOX: ["box", "destination", "priority"],
    PICK_BOX: ["box", "priority"],
    MOVE_ROBOT: ["destination", "priority"],
    CHARGE_ROBOT: ["priority"],
    STOP_ROBOT: [],
    RESUME_ROBOT: [],
    // The three actor-class task types (see TRUST_LAYER.md) — "robot" has
    // no data-when in the HTML so it stays visible for these too, but the
    // backend simply ignores it for AGENT_INSPECTION/HUMAN_INSPECTION.
    AGENT_INSPECTION: ["agent"],
    HUMAN_INSPECTION: ["operator"],
    MIXED_MAINTENANCE_MISSION: ["destination", "agent", "operator", "priority"],
    AGENT_REPLAN: ["agent"],
    AGENT_AUDIT: ["agent"],
    OPERATOR_APPROVAL: ["operator"],
    OPERATOR_MAINTENANCE_SIGNOFF: ["operator"],
    BATCH_DELIVER: ["box_ids", "destination", "priority"]
```

with:

```javascript
  // The task form's fields for each type come from options().task_types
  // (FloorModel.taskFields). "robot" has no data-when in the HTML, so it stays
  // visible for every type; the backend ignores it where it doesn't apply.
  // The new job types' own fields and the inputs that hold them:
  var JOB_INPUTS = {
    slot: "taskSlot", quantity: "taskQuantity", station: "taskStation", face: "taskFace", dock: "taskDock",
    lane: "taskLane", order_id: "taskOrder", pack_cell: "taskPackCell", segment: "taskSegment"
```

**Replace in** `frontend/app.js`:

```javascript
    THINKING: "run", AVAILABLE: "idle", ON_TASK: "run", OFF_DUTY: "wait",
```

with:

```javascript
    THINKING: "run", AVAILABLE: "idle", ON_TASK: "run", OFF_DUTY: "wait", RETURNING: "wait",
```

**Replace in** `frontend/app.js`:

```javascript
    for (var i = 0; i < state.boxes.length; i++) if (state.boxes[i].id === id) return state.boxes[i];
    return null;
```

with:

```javascript
    for (var i = 0; i < state.boxes.length; i++) if (state.boxes[i].id === id) return state.boxes[i];
    return null;
  }
  function findRobot(id) {
    for (var i = 0; i < state.robots.length; i++) {
      if (state.robots[i].id === id || state.robots[i].name === id) return state.robots[i];
    }
    return null;
```

**Replace in** `frontend/app.js`:

```javascript
    }), { selected: "NORMAL" });
```

with:

```javascript
    }), { selected: "NORMAL" });
    // A job's station, pack cell, sorter lane or dock: "" takes its default.
    [["station", "taskStation"], ["pack_cell", "taskPackCell"], ["lane", "taskLane"], ["dock", "taskDock"]]
      .forEach(function (pair) {
        fillSelect($(pair[1]), FM.fieldChoices(state.layout, pair[0]), { placeholder: "Default" });
      });
    if ($("faultKind").options.length === 0) {
      fillSelect($("faultKind"), FM.FAULT_KINDS.map(function (k) { return { value: k[0], label: k[1] }; }));
    }
```

**Replace in** `frontend/app.js`:

```javascript
    var allowed = TASK_FIELDS[type] || [];
```

with:

```javascript
    var allowed = FM.taskFields(state.options, type);
```

**Replace in** `frontend/app.js`:

```javascript
      fields[i].hidden = allowed.indexOf(fields[i].dataset.when) === -1;
    }
  }
```

with:

```javascript
      fields[i].hidden = allowed.indexOf(fields[i].dataset.when) === -1;
    }
    $("taskGuide").textContent = FM.taskGuide(state.options, type);
  }
```

**Replace in** `frontend/app.js`:

```javascript
      statusCell.appendChild(chip(operator.status));
```

with:

```javascript
      statusCell.appendChild(chip(FM.personStatus(operator, state.tasks)));
```

**Replace in** `frontend/app.js`:

```javascript
    var available = Math.max(320, wrap.clientWidth - 16);
    var cell = Math.max(16, Math.floor(available / state.layout.width));
```

with:

```javascript
    // Scale to the width there is, down to phone width (spec §12): no
    // minimum that would push the floor wider than its panel.
    var available = Math.max(64, wrap.clientWidth - 16);
    var cell = Math.max(2, Math.floor(available / state.layout.width));
```

**Replace in** `frontend/app.js`:

```javascript

      // carried box rides on the chassis, with its kind badge on the new floor
```

with:

```javascript

      // the robot the robot panel shows
      if (robot.id === selectedRobotId) {
        ctx.save();
        ctx.strokeStyle = COLORS.cyan;
        ctx.lineWidth = Math.max(1.5, cell * 0.05);
        ctx.setLineDash([cell * 0.16, cell * 0.1]);
        ctx.beginPath();
        ctx.arc(cx, cy, size * 0.84, 0, Math.PI * 2);
        ctx.stroke();
        ctx.restore();
      }

      // carried box rides on the chassis, with its kind badge on the new floor
```

**Replace in** `frontend/app.js`:

```javascript

  // The "Air layer" toggle only appears on a floor that has one.
```

with:

```javascript

  /* ------------------------------------------------------------ robot panel */
  function kvList(pairs) {
    var list = el("dl", "kv");
    pairs.forEach(function (pair) {
      list.appendChild(el("dt", null, pair[0]));
      list.appendChild(el("dd", null, String(pair[1])));
    });
    return list;
  }

  // The robot clicked on the floor (spec §12 Robot panel): model, asset and
  // limits, its job, step and wait, and a link to its inventory record.
  function renderRobotPanel() {
    var panel = $("robotPanel");
    var robot = selectedRobotId ? findRobot(selectedRobotId) : null;
    if (!robot) {
      panel.hidden = true;
      panel.dataset.signature = "";
      return;
    }
    var view = FM.robotPanel(robot, state.tasks);
    panel.hidden = false;
    // Rebuild only on a change, so the link isn't swapped out under a click.
    var signature = JSON.stringify(view);
    if (panel.dataset.signature === signature) return;
    panel.dataset.signature = signature;
    $("robotPanelName").textContent = view.name + (view.letter ? " · " + view.letter : "");
    var body = $("robotPanelBody");
    body.innerHTML = "";
    var head = el("div", "robot-panel-head");
    head.appendChild(chip(view.status));
    if (view.waitReason) head.appendChild(el("span", "chip wait", "Waiting: " + FM.titleize(view.waitReason)));
    body.appendChild(head);
    var grid = el("div", "robot-panel-grid");
    [
      ["Identity", view.identity],
      ["Limits", view.limits.length ? view.limits : [["Floor profile", "none (a classic robot)"]]],
      ["Job", view.job]
    ].forEach(function (section) {
      var box = el("div", "robot-panel-section");
      box.appendChild(el("h4", null, section[0]));
      box.appendChild(kvList(section[1]));
      grid.appendChild(box);
    });
    body.appendChild(grid);
    if (view.inventoryUrl) {
      var link = el("a", "btn small", "Inventory record");
      link.href = view.inventoryUrl;
      body.appendChild(link);
    } else {
      body.appendChild(el("p", "form-note", "Not bound to an inventory record."));
    }
  }

  function selectRobot(id) {
    var robot = findRobot(id);
    selectedRobotId = robot ? robot.id : null;
    renderRobotPanel();
    var panel = $("robotPanel");
    if (!panel.hidden && panel.scrollIntoView) panel.scrollIntoView({ block: "nearest" });
  }

  // A click on the floor selects the robot drawn under it.
  function robotAtClick(event) {
    var rect = canvas.getBoundingClientRect();
    var scale = rect.width ? geometry.w / rect.width : 1;
    var x = (event.clientX - rect.left) * scale / geometry.cell - 0.5;
    var y = (event.clientY - rect.top) * scale / geometry.cell - 0.5;
    var layers = FM.robotLayers(state.robots, showAir);
    return FM.hitRobot(layers.ground.concat(layers.air).map(function (robot) {
      var shown = render.robots[robot.id] || robot.position;
      return { id: robot.id, x: shown.x, y: shown.y };
    }), x, y);
  }

  /* ------------------------------------------------------------ shift panel */
  function shiftList(node, items, empty, line) {
    node.innerHTML = "";
    if (!items.length) {
      node.appendChild(el("li", "empty", empty));
      return;
    }
    items.forEach(function (item) { node.appendChild(line(item)); });
  }

  // The shift (spec §12 Shift panel; new floor only): clock, start and pause,
  // pace, orders in flight, throughput, backlog, failed orders and safety
  // escalations, and faults on demand (Plan ruling 9).
  function renderShift() {
    var view = FM.shiftPanel(state.shift);
    var panel = $("shiftPanel");
    panel.hidden = !view;
    if (!view) return;
    $("shiftClock").textContent = view.clock;
    var status = $("shiftStatus");
    status.textContent = view.status;
    status.className = "chip " + (view.running ? "run" : "wait");
    $("shiftStartBtn").disabled = view.running;
    $("shiftPauseBtn").disabled = !view.running;
    var pace = $("shiftPace");
    if (document.activeElement !== pace) pace.value = view.pace;
    var grid = $("shiftStats");
    grid.innerHTML = "";
    [["In flight", view.inFlight, "accent"], ["Backlog", view.backlog, ""], ["Done", view.done, "good"],
      ["Per hour", view.throughput, ""]].forEach(function (item) {
      var cell = el("div", "stat");
      cell.appendChild(el("span", "stat-label", item[0]));
      cell.appendChild(el("div", "stat-value " + item[2], item[1] === undefined || item[1] === null ? "—" : String(item[1])));
      grid.appendChild(cell);
    });
    shiftList($("shiftFailed"), view.failed, "No failed orders.", function (order) {
      var item = el("li");
      item.appendChild(el("span", "mono", order.id));
      item.appendChild(document.createTextNode(" " + FM.titleize(order.kind) + ": " + order.reason));
      return item;
    });
    shiftList($("shiftEscalations"), view.escalations, "No safety escalations.", function (entry) {
      return el("li", null, entry.message);
    });
  }

  // A shift control or fault: POST it, then show the shift it answers with.
  function shiftAction(path, body, message) {
    var note = $("shiftNote");
    api(path, { method: "POST", body: body || {} })
      .then(function (result) {
        if (result && result.shift && result.shift.status) {
          state.shift = result.shift;
          renderShift();
        }
        note.className = "form-note ok";
        note.textContent = message;
      })
      .catch(function (error) {
        note.className = "form-note bad";
        note.textContent = error.message;
      });
  }

  // The "Air layer" toggle only appears on a floor that has one.
```

**Replace in** `frontend/app.js`:

```javascript
    state.equipment = snapshot.equipment || null;   // null on classic: no conveyor
    state.options = snapshot.options || state.options;
```

with:

```javascript
    state.equipment = snapshot.equipment || null;   // null on classic: no conveyor
    state.shift = snapshot.shift || null;           // null on classic: no shift engine
    state.options = snapshot.options || state.options;
```

**Replace in** `frontend/app.js`:

```javascript
      renderAirToggle();
      syncOptions();
```

with:

```javascript
      renderAirToggle();
      renderShift();
      renderRobotPanel();
      syncOptions();
```

**Replace in** `frontend/app.js`:

```javascript

    $("estopBtn").addEventListener("click", function () {
```

with:

```javascript

    canvas.addEventListener("click", function (event) {
      if (!state.layout) return;
      var id = robotAtClick(event);
      if (id) selectRobot(id);
    });
    $("robotPanelClose").addEventListener("click", function () { selectRobot(null); });

    $("shiftStartBtn").addEventListener("click", function () {
      shiftAction("/api/shift/start", {}, "Shift started");
    });
    $("shiftPauseBtn").addEventListener("click", function () {
      shiftAction("/api/shift/pause", {}, "Shift paused: orders in flight carry on");
    });
    $("shiftPaceBtn").addEventListener("click", function () {
      shiftAction("/api/shift/config", { pace: Number($("shiftPace").value) }, "Pace set to " + $("shiftPace").value);
    });
    $("faultBtn").addEventListener("click", function () {
      var kind = $("faultKind").value;
      shiftAction("/api/faults/" + encodeURIComponent(kind), { count: 1 },
        FM.titleize(kind) + " armed: it happens at the next chance");
    });

    $("estopBtn").addEventListener("click", function () {
```

**Replace in** `frontend/app.js`:

```javascript
      var allowed = TASK_FIELDS[$("taskType").value] || [];
      var body = { type: $("taskType").value, robot_id: $("taskRobot").value };
      if (allowed.indexOf("box") !== -1) body.box_id = $("taskBox").value;
      if (allowed.indexOf("box_ids") !== -1) {
        body.box_ids = Array.prototype.slice.call($("taskBoxes").selectedOptions)
          .map(function (o) { return o.value; });
      }
      if (allowed.indexOf("source") !== -1 && $("taskSource").value) body.source = $("taskSource").value;
      if (allowed.indexOf("destination") !== -1) body.destination = $("taskDestination").value;
      if (allowed.indexOf("priority") !== -1) body.priority = $("taskPriority").value;
      if (allowed.indexOf("agent") !== -1) body.agent_id = $("taskAgent").value;
      if (allowed.indexOf("operator") !== -1) {
        body.operator_id = $("taskOperator").value;
        if ($("taskDualSignoff").checked) {
          body.dual_signoff = true;
          if ($("taskSecondOperator").value !== "AUTO") body.second_operator_id = $("taskSecondOperator").value;
        }
      }
```

with:

```javascript
      var type = $("taskType").value;
      var values = {
        robot: $("taskRobot").value,
        box: $("taskBox").value,
        box_ids: Array.prototype.slice.call($("taskBoxes").selectedOptions).map(function (o) { return o.value; }),
        source: $("taskSource").value,
        destination: $("taskDestination").value,
        priority: $("taskPriority").value,
        agent: $("taskAgent").value,
        operator: $("taskOperator").value,
        dual_signoff: $("taskDualSignoff").checked,
        second_operator: $("taskSecondOperator").value
      };
      Object.keys(JOB_INPUTS).forEach(function (field) { values[field] = $(JOB_INPUTS[field]).value; });
      var body = FM.taskPayload(type, FM.taskFields(state.options, type), values);
```

**Replace in** `frontend/app.js`:

```javascript
        resizeCanvas();
        return api("/api/logs?limit=300");
```

with:

```javascript
        resizeCanvas();
        var wanted = FM.queryParam(window.location.search, "robot");
        if (wanted) {
          if (findRobot(wanted)) selectRobot(wanted);
          else toast("Robot " + wanted + " is not on the floor", "bad");
        }
        return api("/api/logs?limit=300");
```


- [ ] **Step 6: The fleet page links each robot on the floor**

`fleet.js` also loads `GET /api/robots` with the robot list, to know which assets have a robot on the floor. A click on the link isn't a click on its row (which opens the drawer).

**Replace in** `frontend/fleet.js`:

```javascript
    robots: [], workers: [], models: [], releases: [], definitions: [],
    drawer: null,
```

with:

```javascript
    robots: [], workers: [], models: [], releases: [], definitions: [],
    floor: {},   // asset id -> its robot on the simulated floor (GET /api/robots)
    drawer: null,
```

**Replace in** `frontend/fleet.js`:

```javascript

  function renderRobots() {
```

with:

```javascript

  /* The dashboard with this asset's floor robot selected (spec §12 Fleet page). */
  function floorLink(robotId) {
    return '<a class="btn small floor-link" href="/?robot=' + encodeURIComponent(robotId) + '">On the floor</a>';
  }

  function renderRobots() {
```

**Replace in** `frontend/fleet.js`:

```javascript
      return '<tr class="clickable" data-asset="' + esc(r.asset_id) + '"><td class="mono">' + esc(r.asset_id) +
```

with:

```javascript
      var floor = state.floor[r.asset_id];
      return '<tr class="clickable" data-asset="' + esc(r.asset_id) + '"><td class="mono">' + esc(r.asset_id) +
        (floor ? "<div>" + floorLink(floor.id) + "</div>" : "") +
```

**Replace in** `frontend/fleet.js`:

```javascript
      ["On this floor as", floor ? floor.name + " — " + (floor.ota_installing ? "UPDATING" : floor.status) : "not on the simulated floor"]
```

with:

```javascript
      ["On this floor as", floor ? { html: esc(floor.name + " — " + (floor.ota_installing ? "UPDATING" : floor.status)) +
        " " + floorLink(floor.id) } : "not on the simulated floor"]
```

**Replace in** `frontend/fleet.js`:

```javascript
    return api("GET", "/api/fleet/robots").then(function (d) { state.robots = d.robots; renderRobots(); });
```

with:

```javascript
    return Promise.all([api("GET", "/api/fleet/robots"), api("GET", "/api/robots")]).then(function (results) {
      state.robots = results[0].robots;
      state.floor = {};
      results[1].robots.forEach(function (robot) { if (robot.asset_id) state.floor[robot.asset_id] = robot; });
      renderRobots();
    });
```

**Replace in** `frontend/fleet.js`:

```javascript
    $("robotTable").addEventListener("click", function (event) {
      var row = event.target.closest("tr[data-asset]");
```

with:

```javascript
    $("robotTable").addEventListener("click", function (event) {
      if (event.target.closest("a")) return;   // the "On the floor" link goes to the dashboard
      var row = event.target.closest("tr[data-asset]");
```

**Replace in** `frontend/fleet.js`:

```javascript
      state.definitions = results[2].definitions;
      return refresh(true);
```

with:

```javascript
      state.definitions = results[2].definitions;
      // /fleet.html?asset=<id> (the dashboard's robot panel links here) opens that record.
      var asset = /[?&]asset=([^&]+)/.exec(window.location.search);
      if (asset) openDrawer("robot", decodeURIComponent(asset[1]));
      return refresh(true);
```


- [ ] **Step 7: Run the new tests**

Run: `.venv/bin/python -m pytest -o addopts="" -q backend/test_panels_js.py backend/test_floor_model_js.py`
Expected: `8 passed` (`panels_test.js` reports `22 passed, 0 failed`).

- [ ] **Step 8: Run the full suite**

Run: `.venv/bin/python -m pytest -o addopts="" -q`
Expected: `2 failed, 894 passed` (Task 9's count plus this task's 3 tests; the two failures are the allowed ones).

- [ ] **Step 9: Commit**

```bash
git add frontend/floor_model.js frontend/app.js frontend/index.html frontend/style.css frontend/fleet.js frontend/tests/panels_test.js backend/test_panels_js.py
git commit -m "feat: robot and shift panels, job forms from options, the fleet link and phone width

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 10: Browser check (controller)**

Start the new floor with `WAREHOUSE_PORT=5070 .venv/bin/python -m backend.app` and the classic floor with `WAREHOUSE_LAYOUT=classic WAREHOUSE_PORT=5071 .venv/bin/python -m backend.app`. At desktop width (≥ 1500 px) on 5070 — this covers §16's browser check items 1–5:

1. The shift panel (left rail, under Simulation) reads PAUSED at 06:00 with Start enabled. Press Start: it reads RUNNING, Start greys out and Pause lights, the clock advances, and orders in flight, backlog, done and per hour fill in as orders arrive. Set the pace to 2 and press "Set pace": the note confirms it. Pause and Start again work. (§16-1: start the shift.)
2. Watch every robot type act (§16-1): click each type on the floor. Its panel opens under the floor with model, asset, type and layer; payload, reach and clearance (a CX10 shows 1300 mm and "fixed station", an IX2 "flies (air layer)" and its altitude in the air); job, step and wait; the robot gets a dashed cyan ring. "Inventory record" opens `/fleet.html?asset=<asset>` with that robot's drawer open; in `fleet.html` each robot row on the floor (and the drawer's "On this floor as" line) has "On the floor", which opens the dashboard with that robot's panel and ring. Close hides the panel.
3. Toggle "Air layer" (§16-2): drones, their routes and the no-fly overlay hide and come back; a drone can only be clicked while shown.
4. A person walks into a pack cell and its arm pauses (§16-3): with an order packing, choose "Conveyor jam" under Inject fault and press Inject (repeat until the red cell is (23,15) or (22,15), an arm's working cell). Mateo's dot walks into that pack cell; the arm's frame turns amber and its panel reads "Waiting for: Person in cell".
5. The humanoid pauses on Jordan's break (§16-4): breaks start two sim-hours in (08:00 on the shift clock, Jordan first). Set the simulation speed to 10× and wait (about 12 minutes); when Jordan's dot goes to the south-west floor, the H1-212 gets an amber ring and its panel reads "Waiting for: Supervisor absent"; the shift panel lists the escalation if the wait passes 120 s.
6. Jam the conveyor (§16-5): after step 4, the task board shows the CLEAR_JAM going to Mateo Silva, not Riley Chen (Riley's cell access is revoked); the shift panel's failed orders and escalations update with reasons. Open that CLEAR_JAM once Mateo is in the cell and Cancel it: in the Operators table Mateo reads RETURNING until he is back, then AVAILABLE.
7. The task form: each new job type shows its own fields and guide (TOTE_TO_STATION: Box, Station, Priority; CLEAR_JAM: Conveyor cell, Operator, Priority; CYCLE_COUNT: Rack face, Priority). Create a CYCLE_COUNT with rack face `10,5`: accepted. A PICK_ITEMS with quantity `2.5` gets "quantity must be a whole number of at least 1".
8. Classic (5071): no shift panel; the task form offers the 16 older types with the fields they always had; Robo-01's panel shows "none (a classic robot)" for limits; the floor and legend look as before.

At 375 px (device emulation, both floors and `fleet.html`): `document.documentElement.scrollWidth` equals `window.innerWidth` (no horizontal scroll) with every panel open, the shift panel and the robot panel included; the floor fits inside its panel (32 cells of 9 px on the new floor); the left rail, the floor and its panels, and the right rail stack in one column; tapping a robot opens its panel; the notification panel fits the screen; the task form, shift controls and fault select are usable. No console errors at either width.

---

### Task 11: The soak, the tick budget and the fault matrix

Spec §16's soak and fault matrix, which close §1's success criteria for step 1. **The soak.** `backend/soak.py` boots the distribution-centre floor with its full seed (`DigitalTwin.seed_floor()`, Task 2: the 15 robots, the 10 workers, the goods), starts the shift at seed 42 and pace 2 with every risk at 0, runs 20 000 ticks (50 sim-minutes) and reports what the shift did: same-layer collisions (`COLLISION_DETECTED`), the ten new evaluation checks (§10.4, `eval_engine.EMBODIMENT_CHECKS`) graded over the events of every job that finished, orders completed per kind, robots left in `ERROR`, safety escalations (`SAFETY_WAIT_ESCALATED`), and the mean and longest tick. It counts everything from its own subscription to `twin.events`, not from the twin's 5 000-event buffer, which the soak outgrows almost four times over and which made the plan 1b probe's counts wrong (the soak-tooling minor); it grades each job once the run is over, because a carton's sorter hand-off is recorded against its PACK_ORDER after that job ends. `python -m backend.soak` prints the report. **The tick budget.** §1 allows a mean tick of 15 ms with 15 robots; the soak measures about 0.6 ms (see **Measured**), so Plan ruling 7 stands: no order-book pruning or indexing. **The fault matrix.** Each injected fault (§10.6, `faults.FAULT_RISKS`) at a risk of 0.2, alone, on the same soak cut to the shortest run in which its evidence shows up twice, is caught by its matching check: `count_consistent`, `placement_level_correct`, `handoff_consistent`, `sort_correct` and `payload_within_limit` for the five faults with a check, and no other new check fails. Plan ruling 11 gives the other two their evidence: a jam emits `CONVEYOR_JAMMED` and raises a `CLEAR_JAM`; a GRASP_FAIL is a missed grasp the job records on its GRASP step and retries — only a third miss in a row fails the job ("missed the grasp 3 times", pinned by `backend/test_job_steps.py`), which at 0.2 is 0.8 % of grasps and never happens in 150 seed-42 sim-minutes, so the matrix asserts the misses and the retries. **Determinism.** The shift engine draws from its own seeded `random.Random`, and `run_soak` seeds the module-level `random` for the run (the fault rolls draw from it) and puts it back after. With every risk at 0, `FaultInjector.roll` draws nothing, and `_act_deliver`'s FALSE_SUCCESS draw on every delivery always answers "no"; `backend/test_soak.py`'s docstring says so, correcting `backend/test_shift_soak.py:9`, which can't be edited (the parked doc-only minor). The same result came out under `PYTHONHASHSEED` 1, 999 and random.

**Files:**
- Create: `backend/soak.py`
- Test: `backend/test_soak.py` (create), `backend/test_fault_matrix.py` (create)

**Interfaces:**
- Consumes: `DigitalTwin(..., layout="distribution_center")` and `DigitalTwin.seed_floor()` (Task 2); `twin.events.subscribe(callback)`; `twin.shift.configure(seed=, pace=)` and `twin.shift.start()`; `Simulator(twin).tick()`; `eval_engine.EMBODIMENT_CHECKS`, `evaluate_events(events, task_id=, checks=)`, `Verdict`, `CheckResult.applicable`; `faults.FAULT_RISKS`, `faults.fault_kind` (Task 5); `operations.orders.ORDER_KINDS`; the event data the runner reads — `ORDER_COMPLETED`/`ORDER_FAILED` `data["kind"]`, `TASK_CREATED` `data["task_type"]`, `SAFETY_WAIT_ESCALATED` `data["reason"]`; a GRASP action's `params["misses"]` (`Simulator._finish_grasp`); `task.actions`, `task.error`, `twin.tasks.tasks`.
- Produces:
  - `backend.soak.run_soak(ticks: int = 20000, seed: int = 42, pace: float = 2.0, risks: Optional[Dict[str, float]] = None) -> SoakResult` — `risks` maps a fault kind (`"grasp_fail"`) or a CONFIG risk name (`"FALSE_SUCCESS_RISK"`) to a chance from 0 to 1; every other `*_RISK` is 0 for the run. CONFIG and the module-level random state are put back afterwards. Bad `ticks`, `pace` or `risks` raise `ValueError`.
  - `backend.soak.SoakResult` (a dataclass): `ticks`, `seed`, `pace`, `risks: Dict[str, float]` (the risks above 0), `sim_minutes`, `collisions: int`, `jobs_graded: int`, `check_applied: Dict[str, int]`, `check_failures: Dict[str, int]`, `check_examples: Dict[str, str]`, `orders_done: Dict[str, int]`, `orders_failed: Dict[str, int]`, `robots_in_error: List[str]`, `escalations: Dict[str, int]` (by wait reason), `jobs_created: Dict[str, int]` (by job type), `failed_jobs: List[Dict[str, str]]` (`task_id`, `type`, `error`), `grasp_misses: int`, `events: Dict[str, int]`, `mean_tick_ms: float`, `max_tick_ms: float`; properties `rule_check_failures` and `escalation_count`; `report() -> str`.
  - `backend.soak.soak_risks(risks=None) -> Dict[str, float]`; `main(argv=None) -> int` (`python -m backend.soak [--ticks N] [--seed N] [--pace X] [--risk KIND=CHANCE ...]`); constants `SOAK_LAYOUT`, `SOAK_TICKS`, `SOAK_SEED`, `SOAK_PACE`, `TICK_BUDGET_MS` (15.0), `FAULT_MATRIX_RISK` (0.2), `RISK_KEYS` (every CONFIG key ending in `_RISK`).

- [ ] **Step 1: Write the failing tests**

**Create** `backend/test_soak.py`:

```python
"""The fixed-seed soak (multi-embodiment spec §16; §1's success criteria).

The distribution-centre floor with its full seed (15 robots, 10 workers, the
goods) runs the shift at seed 42 and pace 2 for 20 000 ticks (50 sim-minutes)
with every risk at 0, and finishes with no same-layer collision, no failure of
any new evaluation check over the jobs that finished, at least one completed
order of every kind, no robot in ERROR, no safety escalation (the soak ends
before the first break, at two sim-hours), and a mean tick within the 15 ms
budget.

Why the run is deterministic: the shift engine draws from its own
random.Random, seeded by the shift seed, and run_soak seeds the module-level
random for the run too. With every risk at 0, FaultInjector.roll draws no
number at all. Simulator._act_deliver does draw — `random.random()` for
FALSE_SUCCESS_RISK on every delivery — but against a risk of 0 the answer is
always "no", so the draw never changes what happens. (backend/test_shift_soak.py
says "a zero risk never draws a random number"; that file can't be edited, so
the correct reason is given here.)
"""
import dataclasses
import random

import pytest

from backend.eval_engine import EMBODIMENT_CHECKS
from backend.models import CONFIG
from backend.operations.orders import ORDER_KINDS
from backend.soak import (FAULT_MATRIX_RISK, RISK_KEYS, SOAK_PACE, SOAK_SEED, SOAK_TICKS, TICK_BUDGET_MS, main,
                          run_soak, soak_risks)

NEW_CHECKS = [check.__name__[len("check_"):] for check in EMBODIMENT_CHECKS]


@pytest.fixture(scope="module")
def soak():
    return run_soak()      # spec §16: distribution_center, seed 42, pace 2, 20 000 ticks, faults off


def timeless(result):
    """A result without its wall-clock timings, which differ run to run."""
    data = dataclasses.asdict(result)
    del data["mean_tick_ms"], data["max_tick_ms"]
    return data


def test_the_soak_runs_the_spec_shift_with_faults_off(soak):
    assert (SOAK_TICKS, SOAK_SEED, SOAK_PACE) == (20000, 42, 2.0)
    assert (soak.ticks, soak.seed, soak.pace, soak.risks) == (20000, 42, 2.0, {})
    assert soak.sim_minutes == 50.0                          # 20 000 × TICK_DT 0.15 s
    assert soak.grasp_misses == 0 and soak.failed_jobs == []


def test_no_same_layer_collisions(soak):
    assert soak.collisions == 0
    assert soak.events.get("COLLISION_DETECTED", 0) == 0


def test_no_new_check_fails_and_every_new_check_graded_real_data(soak):
    assert soak.check_failures == {}, soak.check_examples
    # Zero failures only means something if each check read its data: a
    # check that applied to no job would pass whatever the floor did.
    assert sorted(soak.check_applied) == sorted(NEW_CHECKS)
    assert all(soak.check_applied[name] >= 1 for name in NEW_CHECKS), soak.check_applied


def test_orders_of_every_kind_complete(soak):
    assert all(soak.orders_done.get(kind, 0) >= 1 for kind in ORDER_KINDS), soak.orders_done
    assert soak.orders_failed == {}


def test_no_robot_is_left_in_error(soak):
    assert soak.robots_in_error == []


def test_no_safety_escalations(soak):
    assert soak.escalations == {} and soak.escalation_count == 0


def test_the_mean_tick_is_within_the_budget(soak):
    # Spec §1: at most 15 ms with 15 robots on the dev machine (this plan's
    # Measured section records the figure).
    assert 0 < soak.mean_tick_ms <= TICK_BUDGET_MS
    assert soak.max_tick_ms >= soak.mean_tick_ms


def test_the_soak_counts_every_event_not_the_twins_buffer(soak):
    # The twin keeps only the last MAX_EVENTS_IN_MEMORY events; the soak sees
    # nearly four times that. Grading from the buffer (as the plan 1b probe
    # did) would lose the early jobs' events: measured, it grades the scans of
    # 2 of the 10 counts and the sorting of 10 of the 26 customer cartons.
    assert sum(soak.events.values()) > 3 * CONFIG["MAX_EVENTS_IN_MEMORY"]
    assert soak.jobs_graded == sum(soak.events.get(kind, 0)
                                   for kind in ("TASK_COMPLETED", "TASK_FAILED", "TASK_CANCELLED"))
    assert soak.jobs_graded >= 300
    assert soak.check_applied["count_consistent"] >= soak.orders_done["COUNT"]
    assert soak.check_applied["sort_correct"] >= soak.orders_done["CUSTOMER"]


def test_the_same_seed_gives_the_same_run_even_with_a_fault():
    # A risk above 0 draws from the module-level random. run_soak seeds it
    # with the run's seed, so two runs agree in everything but the clock
    # whatever state it was in before; without that seeding they differ.
    random.seed(1)
    first = run_soak(ticks=1500, risks={"handoff_loss": FAULT_MATRIX_RISK})
    random.seed(2)
    second = run_soak(ticks=1500, risks={"handoff_loss": FAULT_MATRIX_RISK})
    assert first.check_failures.get("handoff_consistent", 0) >= 1
    assert timeless(first) == timeless(second)


def test_a_run_puts_config_and_the_random_state_back():
    # Fails if run_soak leaves its risks in CONFIG: every later test would run with faults on.
    before = {key: CONFIG[key] for key in RISK_KEYS}
    random.seed(7)
    state = random.getstate()
    result = run_soak(ticks=10, risks={"MIS_SORT_RISK": 0.5, "grasp-fail": 0.25})
    assert result.risks == {"MIS_SORT_RISK": 0.5, "GRASP_FAIL_RISK": 0.25}
    assert {key: CONFIG[key] for key in RISK_KEYS} == before
    assert random.getstate() == state


def test_risks_are_named_by_fault_kind_or_config_key_and_checked():
    settings = soak_risks({"conveyor_jam": 0.2, "FALSE_SUCCESS_RISK": 0.1})
    assert set(settings) == set(RISK_KEYS) and {"COLLISION_RISK", "OTA_FAILURE_RISK"} <= set(RISK_KEYS)
    assert {key: value for key, value in settings.items() if value} == {"CONVEYOR_JAM_RISK": 0.2,
                                                                        "FALSE_SUCCESS_RISK": 0.1}
    with pytest.raises(ValueError, match="Unknown risk 'gremlins'"):
        soak_risks({"gremlins": 0.2})
    for bad in (1.5, -0.1, "0.2", True):
        with pytest.raises(ValueError, match="must be a number from 0 to 1"):
            soak_risks({"mis_sort": bad})
    with pytest.raises(ValueError, match="ticks must be a whole number"):
        run_soak(ticks=0)
    with pytest.raises(ValueError, match="pace must be a number above 0"):
        run_soak(ticks=10, pace=0)


def test_the_command_line_prints_the_report(capsys):
    assert main(["--ticks", "40", "--risk", "conveyor_jam=0.2"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("Soak: distribution_center, seed 42, pace 2, 40 ticks (0.1 sim-minutes)\n")
    assert "Risks: CONVEYOR_JAM_RISK 0.2" in out and "Mean tick:" in out and "Same-layer collisions: 0" in out
    with pytest.raises(SystemExit) as stop:
        main(["--risk", "conveyor_jam"])
    assert stop.value.code == 2
    assert "expected KIND=CHANCE" in capsys.readouterr().err
```

**Create** `backend/test_fault_matrix.py`:

```python
"""The fault matrix (multi-embodiment spec §16): each injected fault (§10.6) at a
risk of 0.2 is caught by its matching check.

Each row is the §16 soak — the seeded distribution-centre floor, shift seed 42,
pace 2 — with that one risk at 0.2 and every other risk at 0, cut to the
shortest run in which its evidence shows up at least twice (the ticks it
shows up at are in the comments; the runs are deterministic, see
backend/test_soak.py). Five faults have a §10.4 check of their own. Two don't
(Plan ruling 11): a GRASP_FAIL is a grasp the arm or the picker misses — the
job's GRASP step records the miss and retries it, and only a third miss in a
row fails the job, "missed the grasp 3 times" (backend/test_job_steps.py pins
that; at 0.2 it is 0.8 % of grasps, and the seed-42 shift sees none in 150
sim-minutes) — and a CONVEYOR_JAM emits CONVEYOR_JAMMED and raises a CLEAR_JAM.
A WRONG_LEVEL placement is also flagged later, as a stock variance, when a count
reaches that rack face (plan 1b ruling 4); at seed 42 the first such count
comes about 9 500 ticks after the misplacement, past the run below.
"""
import pytest

from backend.eval_engine import EMBODIMENT_CHECKS
from backend.faults import FAULT_RISKS
from backend.soak import FAULT_MATRIX_RISK, run_soak

#: Fault kind -> the ticks its run takes.
MATRIX = {
    "scan_miscount": 3000,         # count_consistent at ticks 686 and 2 649
    "wrong_level": 20000,          # placement_level_correct at 13 035 and 17 926
    "grasp_fail": 2000,            # misses at ticks 217, 1 323, 1 520 and 1 943
    "conveyor_jam": 1000,          # CONVEYOR_JAMMED at 249 and 544, a CLEAR_JAM at 260 and 560
    "handoff_loss": 2000,          # handoff_consistent at 231, 1 150 and 1 631
    "mis_sort": 7000,              # sort_correct at 1 389 and 6 476
    "misdeclared_weight": 6000,    # payload_within_limit at 4 277 and 5 118
}

#: Fault kind -> its §10.4 check.
CHECKS = {
    "scan_miscount": "count_consistent",
    "wrong_level": "placement_level_correct",
    "handoff_loss": "handoff_consistent",
    "mis_sort": "sort_correct",
    "misdeclared_weight": "payload_within_limit",
}


def faulted_run(kind):
    result = run_soak(ticks=MATRIX[kind], risks={kind: FAULT_MATRIX_RISK})
    assert result.risks == {FAULT_RISKS[kind]: 0.2}          # that one risk, at 0.2, and nothing else
    return result


def test_the_matrix_covers_every_injected_fault():
    assert sorted(MATRIX) == sorted(FAULT_RISKS)
    assert set(CHECKS) | {"grasp_fail", "conveyor_jam"} == set(FAULT_RISKS)
    assert set(CHECKS.values()) <= {check.__name__[len("check_"):] for check in EMBODIMENT_CHECKS}


@pytest.mark.parametrize("kind", sorted(CHECKS))
def test_a_fault_is_caught_by_its_matching_check(kind):
    result = faulted_run(kind)
    check = CHECKS[kind]
    # Fails if the fault stops doing what its check reads (for example the
    # sorter's lane roll, the forklift's level, or the drone's count), or if
    # the check stops reading it.
    assert result.check_failures.get(check, 0) >= 2, (result.check_failures, result.check_examples)
    # And it is that check that catches it: no other new check fails.
    assert set(result.check_failures) == {check}, result.check_examples


def test_a_grasp_fail_is_a_miss_the_job_records_and_retries():
    result = faulted_run("grasp_fail")
    # Fails if a miss stops being recorded on its GRASP step (Simulator._finish_grasp).
    assert result.grasp_misses >= 2
    # The job retries the missed grasp: none failed for anything else, and
    # any that ran out of retries failed with the grasp reason.
    assert all("missed the grasp 3 times" in job["error"] for job in result.failed_jobs), result.failed_jobs
    assert result.check_failures == {}
    assert result.orders_done.get("CUSTOMER", 0) >= 1          # picks and packs still finish


def test_a_conveyor_jam_emits_conveyor_jammed_and_raises_a_clear_jam():
    result = faulted_run("conveyor_jam")
    jams = result.events.get("CONVEYOR_JAMMED", 0)
    assert jams >= 2
    # Fails if a jam stops asking for someone to clear it (human_jobs.ensure_jam_jobs).
    assert result.jobs_created.get("CLEAR_JAM", 0) >= jams
    assert result.events.get("CONVEYOR_CLEARED", 0) >= 1      # and someone did
    assert result.check_failures == {}
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/bin/python -m pytest -o addopts="" -q backend/test_soak.py backend/test_fault_matrix.py`
Expected: `2 errors` — `ModuleNotFoundError: No module named 'backend.soak'` while collecting each file.

- [ ] **Step 3: The soak runner**

**Create** `backend/soak.py`:

```python
"""The fixed-seed soak (multi-embodiment spec §16, and §1's success criteria).

`run_soak()` boots the distribution-centre floor with its full seed
(`DigitalTwin.seed_floor()`: the 15 robots, the 10 workers, the goods), starts
the shift at the given seed and pace with every risk at 0 except the ones it is
asked for, ticks the simulator, and reports what the shift did:

- same-layer collisions (`COLLISION_DETECTED`: two robots on one cell of one layer);
- the new evaluation checks (spec §10.4, `eval_engine.EMBODIMENT_CHECKS`), graded
  over the events of every job that finished;
- orders completed and failed, per kind;
- robots left in `ERROR`;
- safety escalations (`SAFETY_WAIT_ESCALATED`), per wait reason;
- the grasps the arms and the picker missed (a GRASP_FAIL), and the jobs that failed;
- the mean and the longest tick, in ms.

Everything is counted from the runner's own subscription to `twin.events`, which
sees every event. The twin's in-memory buffer keeps only the last
`MAX_EVENTS_IN_MEMORY` (5 000), and the 20 000-tick soak emits nearly four times that,
so grading from it would lose most of the shift.

The run is repeatable. The shift engine draws from its own `random.Random`,
seeded by `seed`. The module-level `random` is seeded with `seed` too for the
run (and put back afterwards): the fault rolls of a run with a risk draw from
it. With every risk at 0 nothing depends on it — `FaultInjector.roll` draws no
number at all, and the FALSE_SUCCESS draw `_act_deliver` makes on every
delivery always answers "no".

    python -m backend.soak                                # spec §16: seed 42, pace 2, 20 000 ticks
    python -m backend.soak --ticks 6000 --risk grasp_fail=0.2
"""
from __future__ import annotations

import argparse
import math
import os
import random
import tempfile
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .digital_twin import DigitalTwin
from .eval_engine import EMBODIMENT_CHECKS, Verdict, evaluate_events
from .faults import FAULT_RISKS, fault_kind
from .models import CONFIG, ActionType, RobotStatus, SimulationStatus
from .operations.orders import ORDER_KINDS
from .simulator import Simulator

#: Spec §16's soak: the new floor, seed 42, pace 2, 20 000 ticks (50 sim-minutes).
SOAK_LAYOUT = "distribution_center"
SOAK_TICKS = 20000
SOAK_SEED = 42
SOAK_PACE = 2.0
#: Spec §1: a mean tick of at most 15 ms with 15 robots.
TICK_BUDGET_MS = 15.0
#: Spec §16's fault matrix sets each risk to this.
FAULT_MATRIX_RISK = 0.2

#: Every CONFIG risk. "Faults off" is all of them at 0: the injected faults
#: (spec §10.6) and the older COLLISION_RISK, FALSE_SUCCESS_RISK and OTA_FAILURE_RISK.
RISK_KEYS = tuple(sorted(key for key in CONFIG if key.endswith("_RISK")))

#: The events that end a job.
FINISHING_EVENTS = ("TASK_COMPLETED", "TASK_FAILED", "TASK_CANCELLED")


@dataclass
class SoakResult:
    """What one soak run saw."""

    ticks: int
    seed: int
    pace: float
    risks: Dict[str, float]                      # the CONFIG risks the run set above 0
    sim_minutes: float
    collisions: int                              # COLLISION_DETECTED events
    jobs_graded: int                             # finished jobs the new checks graded
    check_applied: Dict[str, int]                # new check -> finished jobs whose events it read
    check_failures: Dict[str, int]               # new check -> finished jobs it failed
    check_examples: Dict[str, str]               # new check -> its first failure's message
    orders_done: Dict[str, int]                  # order kind -> ORDER_COMPLETED
    orders_failed: Dict[str, int]                # order kind -> ORDER_FAILED
    robots_in_error: List[str]                   # robots in ERROR at the end
    escalations: Dict[str, int]                  # wait reason -> SAFETY_WAIT_ESCALATED
    jobs_created: Dict[str, int]                 # job type -> TASK_CREATED
    failed_jobs: List[Dict[str, str]]            # {"task_id", "type", "error"}, each job that ended FAILED
    grasp_misses: int                            # misses recorded on the jobs' GRASP steps
    events: Dict[str, int] = field(default_factory=dict)   # event type -> how many
    mean_tick_ms: float = 0.0
    max_tick_ms: float = 0.0

    @property
    def rule_check_failures(self) -> int:
        """How many (finished job, new check) pairs failed."""
        return sum(self.check_failures.values())

    @property
    def escalation_count(self) -> int:
        return sum(self.escalations.values())

    def report(self) -> str:
        """The run as plain text, for `python -m backend.soak`."""
        risks = ", ".join(f"{key} {value:g}" for key, value in sorted(self.risks.items())) or "none (faults off)"
        lines = [
            f"Soak: {SOAK_LAYOUT}, seed {self.seed}, pace {self.pace:g}, {self.ticks} ticks "
            f"({self.sim_minutes:.1f} sim-minutes)",
            f"Risks: {risks}",
            f"Mean tick: {self.mean_tick_ms:.3f} ms (longest {self.max_tick_ms:.1f} ms; budget {TICK_BUDGET_MS:g} ms)",
            f"Same-layer collisions: {self.collisions}",
            f"New checks: {self.rule_check_failures} failure(s) over {self.jobs_graded} finished job(s)",
        ]
        for name, count in sorted(self.check_failures.items()):
            lines.append(f"  {name}: {count} — e.g. {self.check_examples[name]}")
        lines.append("Orders done: " + ", ".join(f"{kind} {self.orders_done.get(kind, 0)}" for kind in ORDER_KINDS))
        if self.orders_failed:
            lines.append("Orders failed: " + ", ".join(f"{kind} {count}"
                                                       for kind, count in sorted(self.orders_failed.items())))
        lines.append(f"Robots in ERROR: {', '.join(self.robots_in_error) or 'none'}")
        lines.append("Safety escalations: " + (", ".join(f"{reason} {count}" for reason, count
                                                         in sorted(self.escalations.items())) or "none"))
        lines.append(f"Grasp misses: {self.grasp_misses}")
        lines.append(f"Jobs that failed: {len(self.failed_jobs)}")
        for job in self.failed_jobs[:10]:
            lines.append(f"  {job['task_id']} {job['type']}: {job['error']}")
        return "\n".join(lines)


def soak_risks(risks: Optional[Dict[str, float]] = None) -> Dict[str, float]:
    """Every CONFIG risk at 0, then `risks` on top. A key is a fault kind
    (`faults.FAULT_RISKS`, e.g. "grasp_fail") or a CONFIG risk name (e.g.
    "FALSE_SUCCESS_RISK"); a value is a chance between 0 and 1."""
    settings = {key: 0.0 for key in RISK_KEYS}
    for name, value in (risks or {}).items():
        kind = fault_kind(name)
        key = FAULT_RISKS[kind] if kind is not None else str(name).strip().upper()
        if key not in settings:
            raise ValueError(f"Unknown risk {name!r} (fault kinds: {sorted(FAULT_RISKS)}; "
                             f"CONFIG risks: {list(RISK_KEYS)})")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0.0 <= value <= 1.0:
            raise ValueError(f"The {key} risk must be a number from 0 to 1 (got {value!r})")
        settings[key] = float(value)
    return settings


class _Tally:
    """The runner's own event subscription: counts every event and keeps each
    job's events, so the jobs can be graded once the run is over (a carton's
    sorter hand-off is recorded against its PACK_ORDER after that job ends)."""

    def __init__(self) -> None:
        self.events: Counter = Counter()
        self.collisions = 0
        self.escalations: Counter = Counter()
        self.orders_done: Counter = Counter()
        self.orders_failed: Counter = Counter()
        self.jobs_created: Counter = Counter()
        self.job_events: Dict[str, List[Dict[str, Any]]] = {}
        self.finished: Dict[str, str] = {}       # task id -> the event that ended it

    def on_event(self, event: Dict[str, Any]) -> None:
        kind = event.get("event")
        data = event.get("data") or {}
        self.events[kind] += 1
        if kind == "COLLISION_DETECTED":
            self.collisions += 1
        elif kind == "SAFETY_WAIT_ESCALATED":
            self.escalations[str(data.get("reason"))] += 1
        elif kind == "ORDER_COMPLETED":
            self.orders_done[data.get("kind")] += 1
        elif kind == "ORDER_FAILED":
            self.orders_failed[data.get("kind")] += 1
        elif kind == "TASK_CREATED":
            self.jobs_created[data.get("task_type")] += 1
        task_id = event.get("task_id")
        if task_id:
            self.job_events.setdefault(task_id, []).append(event)
            if kind in FINISHING_EVENTS:
                self.finished[task_id] = kind


def _grade(tally: _Tally, checks: Sequence[Any]) -> Dict[str, Any]:
    """Each finished job's events through the new checks."""
    applied: Counter = Counter()
    failures: Counter = Counter()
    examples: Dict[str, str] = {}
    for task_id in tally.finished:
        report = evaluate_events(tally.job_events[task_id], task_id=task_id, checks=checks)
        for check in report.checks:
            if check.applicable:
                applied[check.name] += 1
            if check.verdict is Verdict.FAIL:
                failures[check.name] += 1
                examples.setdefault(check.name, f"{task_id}: {check.message}")
    return {"check_applied": dict(applied), "check_failures": dict(failures), "check_examples": examples}


def run_soak(ticks: int = SOAK_TICKS, seed: int = SOAK_SEED, pace: float = SOAK_PACE,
             risks: Optional[Dict[str, float]] = None) -> SoakResult:
    """Run the shift on the seeded new floor for `ticks` ticks and report it.
    `risks` sets CONFIG risks for this run only (see `soak_risks`); every
    other risk is 0. CONFIG and the module-level random state are put back
    afterwards."""
    if isinstance(ticks, bool) or not isinstance(ticks, int) or ticks < 1:
        raise ValueError("ticks must be a whole number, at least 1")
    if isinstance(pace, bool) or not isinstance(pace, (int, float)) or not math.isfinite(pace) or pace <= 0:
        raise ValueError("pace must be a number above 0")
    settings = soak_risks(risks)
    saved = {key: CONFIG[key] for key in settings}
    random_state = random.getstate()
    try:
        CONFIG.update(settings)
        random.seed(seed)
        with tempfile.TemporaryDirectory(prefix="soak-", ignore_cleanup_errors=True) as work:
            return _run(work, ticks, seed, float(pace), settings)
    finally:
        CONFIG.update(saved)
        random.setstate(random_state)


def _run(work: str, ticks: int, seed: int, pace: float, settings: Dict[str, float]) -> SoakResult:
    twin = DigitalTwin(log_dir=os.path.join(work, "logs"), data_dir=os.path.join(work, "data"),
                       persist_logs=False, demo=True, demo_tasks=False, layout=SOAK_LAYOUT)
    twin.seed_floor()
    tally = _Tally()
    twin.events.subscribe(tally.on_event)
    sim = Simulator(twin)
    twin.simulation_status = SimulationStatus.RUNNING
    twin.shift.configure(seed=seed, pace=pace)
    twin.shift.start()
    total = longest = 0.0
    for _ in range(ticks):
        started = time.perf_counter()
        sim.tick()
        took = time.perf_counter() - started
        total += took
        longest = max(longest, took)
    failed = [{"task_id": task_id, "type": twin.tasks.get(task_id).type.value,
               "error": twin.tasks.get(task_id).error or ""}
              for task_id, how in tally.finished.items() if how == "TASK_FAILED" and twin.tasks.get(task_id)]
    # A missed grasp is retried, so it leaves no event: the GRASP step keeps
    # count of its misses (Simulator._finish_grasp), and the twin keeps every job.
    misses = sum(int(action.params.get("misses", 0)) for task in twin.tasks.tasks.values()
                 for action in task.actions if action.type is ActionType.GRASP)
    return SoakResult(
        ticks=ticks, seed=seed, pace=pace,
        risks={key: value for key, value in settings.items() if value > 0},
        sim_minutes=round(twin.simulation_time / 60.0, 1),
        collisions=tally.collisions,
        jobs_graded=len(tally.finished),
        **_grade(tally, EMBODIMENT_CHECKS),
        orders_done=dict(tally.orders_done),
        orders_failed=dict(tally.orders_failed),
        robots_in_error=sorted(robot.name for robot in twin.robots.values() if robot.status is RobotStatus.ERROR),
        escalations=dict(tally.escalations),
        jobs_created=dict(tally.jobs_created),
        failed_jobs=failed,
        grasp_misses=misses,
        events=dict(tally.events),
        mean_tick_ms=1000.0 * total / ticks,
        max_tick_ms=1000.0 * longest,
    )


def _risk_arg(text: str) -> Tuple[str, float]:
    """`--risk KIND=CHANCE`, e.g. grasp_fail=0.2."""
    name, sep, value = text.partition("=")
    if not sep:
        raise argparse.ArgumentTypeError(f"expected KIND=CHANCE, e.g. grasp_fail=0.2 (got {text!r})")
    try:
        return name.strip(), float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{value!r} is not a number") from None


def main(argv: Optional[Sequence[str]] = None) -> int:
    """`python -m backend.soak`: run the soak and print its report."""
    parser = argparse.ArgumentParser(prog="python -m backend.soak", description=__doc__.splitlines()[0])
    parser.add_argument("--ticks", type=int, default=SOAK_TICKS, help=f"ticks to run (default {SOAK_TICKS})")
    parser.add_argument("--seed", type=int, default=SOAK_SEED, help=f"seed of the shift and the fault rolls (default {SOAK_SEED})")
    parser.add_argument("--pace", type=float, default=SOAK_PACE, help=f"shift pace (default {SOAK_PACE:g})")
    parser.add_argument("--risk", type=_risk_arg, action="append", default=[], metavar="KIND=CHANCE",
                        help="set a risk for this run, e.g. grasp_fail=0.2 (repeatable; default: all 0)")
    args = parser.parse_args(argv)
    try:
        result = run_soak(ticks=args.ticks, seed=args.seed, pace=args.pace, risks=dict(args.risk))
    except ValueError as exc:
        parser.error(str(exc))
    print(result.report())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the new tests**

Run: `.venv/bin/python -m pytest -o addopts="" -q backend/test_soak.py backend/test_fault_matrix.py --durations=3`
Expected: `20 passed`, in about 34 s on the dev machine: the 20 000-tick soak (one run, shared by the module's tests) takes about 12 s, the matrix's `wrong_level` row (20 000 ticks) about 12 s, and the other rows and the short runs about 10 s together.

- [ ] **Step 5: Run the report by hand**

Run: `.venv/bin/python -m backend.soak`
Expected (measured with Tasks 1–10 as drafted; the tick times vary run to run):

```
Soak: distribution_center, seed 42, pace 2, 20000 ticks (50.0 sim-minutes)
Risks: none (faults off)
Mean tick: 0.595 ms (longest 6.5 ms; budget 15 ms)
Same-layer collisions: 0
New checks: 0 failure(s) over 436 finished job(s)
Orders done: CUSTOMER 26, INBOUND 5, PALLET 7, COUNT 10, RETURN 8
Robots in ERROR: none
Safety escalations: none
Grasp misses: 0
Jobs that failed: 0
```

And `.venv/bin/python -m backend.soak --ticks 6000 --risk misdeclared_weight=0.2` lists `payload_within_limit: 2` with its first failure (a hauler lifting a pallet whose true weight is over its 300 kg payload).

- [ ] **Step 6: Run the full suite**

Run: `.venv/bin/python -m pytest -o addopts="" -q`
Expected: `2 failed, 914 passed` (Task 10's count plus this task's 20 tests; the two failures are the allowed ones). The suite takes about 35 s longer than before this task (23 s → 58 s on the dev machine).

- [ ] **Step 7: Commit**

```bash
git add backend/soak.py backend/test_soak.py backend/test_fault_matrix.py
git commit -m "test: the fixed-seed soak, the tick budget and the fault matrix

The seeded distribution-centre floor runs the shift at seed 42 and pace 2
for 20 000 ticks with faults off: no same-layer collision, no new check
failing, orders of every kind done, no robot in ERROR, no escalation, and a
mean tick of about 0.6 ms against the 15 ms budget. Each injected fault at
0.2 is caught by its matching check. backend/soak.py counts from its own
event subscription and prints the report (python -m backend.soak).

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 12: Documentation

Spec §15 asks for the documentation of step 1: the README gets the floor, the robot types, the shift and faults, with the warning that a custom `robot_classes` block must include `ARM` and `HUMANOID`; `TRUST_LAYER.md` lists the new rules and has its stale Tier 2 list corrected. Plan 1b carried two more items here: `policies.example.yaml` lacks the seven risks and `CLEAR_JAM`, and the spec's §6 "Movement" still says zone centres decide walkway crossing. This task writes all of it against the code Tasks 1–11 left, and closes these gaps:

- **`README.md`, `RUN.md` and `TRUST_LAYER.md` are rewritten whole, as short documents**: one **Rewrite** block each, not edits to the long files. Hard caps: the README at most 250 lines, `RUN.md` 130 and `TRUST_LAYER.md` 160. Each fact lives in one place and the other files link to it: `ARCHITECTURE.md` and `CLAUDE.md` already hold the module map, the tick, a task's lifecycle and the contributor rules, and this task leaves them alone. There are no curl or JSON samples except two one-liners in `RUN.md` (starting the shift, injecting a fault), and no number that goes stale, such as a test count.
- **The README** holds:
  - what the project is, and a one-line-per-feature overview (trust gate, eval engine, mock CI, fleet and workforce inventory, policies, chat agent, scheduler, reports and the rest);
  - the quick start: `WAREHOUSE_LAYOUT` (default `distribution_center`; `classic` still works) and the per-floor `data/` and `logs/` folders (Plan ruling 2);
  - the two floors in brief, with the robot letters (A AMR, F forklift, H hauler, K picker, S scout, D drone, R arm, U humanoid);
  - the shift, which starts paused (§11.5, Plan ruling 6), and the seven faults with how to inject them (Plan rulings 9 and 20);
  - the dashboard panels (§12, Tasks 9–10: robot panel, shift panel with **Inject fault**, job forms, phone width) and the soak in one line (§16);
  - one API table with every route, old and new, including the operations routes (§14, Task 5) and the error mapping;
  - the warning about custom robot classes, and an index of the other documents.
- **`TRUST_LAYER.md`** holds a concept-to-code table by tier, with the Tier 2 list corrected (three of four built: authorization-changing events, policy-as-code, the decision graph; the Groq grader's assurance record isn't), and "The physical trust layer" (§10.1–§10.6 as built): the eight rules and where each runs, the physical waits, the ten evaluation checks (`placement_level_correct` among them), the layout-driven system checks, and the seven faults with their risk names and what catches each (Plan ruling 11).
- **`RUN.md`** is commands only:
  - prerequisites and setup;
  - running each floor, with the port note (AirPlay holds 5000), starting the shift and injecting a fault;
  - the tests: the full suite, one file, one test, by keyword, and the `jsc` JavaScript tests through their pytest wrappers, which skip without `jsc` (Tasks 9–10);
  - the eval CLI, promptfoo in one paragraph linking `evals/README.md`, the soak (`python -m backend.soak`, Task 11), and a short troubleshooting table.
- **The long reference sections are not carried over.** The task-type, gate, robot-behaviour, logging, Mock CI and eval walk-throughs, the governance subsections, the per-feature API tables, the layout tree and the future-extensions list each survive as a one-line feature entry, an API row or a link. The old statements the code had outgrown (the task-type, check, log-category and box-state counts, the test count, "no polling", the SSE events, the Python version, the requirements, how task logs are written) go with them, and the new text states none of those numbers.
- **`policies.example.yaml`** gains the seven risks, `CLEAR_JAM: robot_cell_access`, and the robot classes as the code has them: `ARM`, `HUMANOID`, and every class's new job types. It also gets notes that `apply_policy` replaces whole tables and ignores unknown `config` keys. Copying it today would have stopped the new floor's seed ("Unknown robot_class 'ARM'"); now copying it changes nothing.
- **The spec** is amended in place, the way plans 1a and 1b amended theirs: the text states the rule as it now is, and the commit message names each amendment and where it comes from.
  - §2: the per-floor folders (Plan ruling 2).
  - §4.3: a save from another floor is refused, not rebuilt (Plan ruling 5).
  - §6 "Movement": walkway crossing is decided by the zones' cells. This text has been stale since plan 1a. The code changed with plan 1b's ruling 15, built in its Task 5, whose review carried the spec wording here.

A new test file guards the docs against drift, comparing exact identifiers taken from the code:

- the example policy loads, sets only real `CONFIG` keys, and holds every fault risk at 0;
- copying the example changes no policy in effect;
- the README's API table lists every route `backend/operations_api.py` registers;
- the README names every fault kind and risk;
- `TRUST_LAYER.md` names every rule, check and risk;
- `RUN.md` names each floor, the soak command and each `jsc` wrapper.

**Files:**
- Rewrite: `README.md`, `TRUST_LAYER.md`, `RUN.md`
- Modify: `policies.example.yaml`
- Modify: `docs/superpowers/specs/2026-09-30-multi-embodiment-operations-design.md` (§2 "Construction", §4.3 `load_state`, §6 "Movement")
- Test: `backend/test_docs_examples.py` (create)

**Interfaces:**
- Consumes:
  - from plan 1b and earlier: `backend.policy.read_policy_file`, `apply_policy`, `effective_policy` and `DEFAULT_POLICY_PATH`; `models.CONFIG`; `faults.FAULT_RISKS`; `eval_engine.EMBODIMENT_CHECKS`; the eight `*_ok` rules of `backend/eligibility.py`; `layouts.LAYOUTS`;
  - Task 5: `app.create_app(twin=, autostart=False, run_thread=False)` with the routes `register_operations_routes` adds;
  - Task 11: `backend/soak.py`'s command line;
  - Tasks 9–10: the `jsc` wrappers `backend/test_floor_model_js.py` and `backend/test_panels_js.py`;
  - throughout, the behaviour of Tasks 1–11 that the docs describe.
- Produces (no code; a documentation contract the new test pins):
  - `policies.example.yaml` is the built-in policy written out: applying it leaves `effective_policy()` unchanged. It sets only `CONFIG` keys, puts every `FAULT_RISKS` risk at `0.0`, and its `certification_requirements` has `CLEAR_JAM: robot_cell_access`. Its `robot_classes` has `ARM` and `HUMANOID`.
  - The README's API table has each operations route as a row of its own, `| METHOD | `path` |`, with a path parameter written `{name}` (`/api/orders/{id}`, `/api/faults/{kind}`); the other routes sit in grouped rows. It also names every fault kind and its risk in backticks.
  - `TRUST_LAYER.md` names, in backticks, the eight rules, the ten `EMBODIMENT_CHECKS` and the seven risks.
  - `RUN.md` contains `WAREHOUSE_LAYOUT=<name>` for each layout, `python -m backend.soak` and `backend/<file>` for each `backend/test_*_js.py`.
  - Sizes: `README.md` at most 250 lines (it is 165), `RUN.md` at most 130 (100), `TRUST_LAYER.md` at most 160 (128).

- [ ] **Step 1: Write the failing tests**

**Create** `backend/test_docs_examples.py`:

```python
"""The documentation stays true to the code (multi-embodiment spec §15).

policies.example.yaml is meant to be copied to policies.yaml as it is, and
apply_policy replaces whole tables (certification_requirements,
robot_classes, operator_roles) and ignores a config key CONFIG doesn't have.
A stale example therefore breaks the new floor quietly: no credential for a
jam inside a pack cell, no ARM or HUMANOID class (the floor's seed then
refuses to start), job types missing from a class's list, a risk that never
takes effect. The README's API tables, TRUST_LAYER.md and RUN.md must also
name what the code serves, checks and runs. Every assertion compares exact
identifiers taken from the code, never prose.
"""
import copy
import glob
import os
import re

import pytest

from backend import eligibility
from backend.app import create_app
from backend.digital_twin import DigitalTwin
from backend.eval_engine import EMBODIMENT_CHECKS
from backend.faults import FAULT_RISKS
from backend.layouts import LAYOUTS
from backend.models import CONFIG
from backend.policy import DEFAULT_POLICY_PATH, apply_policy, effective_policy, read_policy_file

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXAMPLE = os.path.join(ROOT, "policies.example.yaml")


def doc(name):
    with open(os.path.join(ROOT, name), encoding="utf-8") as handle:
        return handle.read()


def example():
    data = read_policy_file(EXAMPLE)
    assert data, "policies.example.yaml did not load (the loader turns a parse error into {})"
    return data


def test_the_policy_example_sets_only_known_keys_and_every_fault_risk_off():
    config = example()["config"]
    # apply_policy skips a key CONFIG doesn't have, so a misspelt risk would never take effect.
    assert sorted(set(config) - set(CONFIG)) == []
    # Fails while the example lacks the seven risks of spec §10.6.
    assert {key: config.get(key) for key in FAULT_RISKS.values()} == {key: 0.0 for key in FAULT_RISKS.values()}


@pytest.mark.skipif(os.path.exists(DEFAULT_POLICY_PATH), reason="a local policies.yaml overrides the built-in policy")
def test_copying_the_policy_example_changes_no_policy_in_effect():
    data = example()
    # Fails while the example's certification table lacks CLEAR_JAM (a jam inside
    # a pack cell would need no credential) or its robot classes lack ARM and
    # HUMANOID or any class's new job types.
    assert data["certification_requirements"].get("CLEAR_JAM") == "robot_cell_access"
    assert {"ARM", "HUMANOID"} <= set(data["robot_classes"])
    saved = effective_policy()
    before = copy.deepcopy(saved)
    try:
        apply_policy(data)
        after = copy.deepcopy(effective_policy())
    finally:
        apply_policy(saved)  # puts the very same objects back
    assert after == before


def test_the_readme_lists_every_operations_route(tmp_path):
    twin = DigitalTwin(log_dir=str(tmp_path / "logs"), data_dir=str(tmp_path / "data"),
                       persist_logs=False, demo=False, demo_tasks=False)
    app = create_app(twin=twin, autostart=False, run_thread=False)[0]
    readme = doc("README.md")
    rules = [rule for rule in app.url_map.iter_rules()
             if app.view_functions[rule.endpoint].__module__ == "backend.operations_api"]
    assert len(rules) >= 10
    missing = []
    for rule in rules:
        # /api/orders/<order_id> is written /api/orders/{id} in the README's tables.
        path = r"\{\w+\}".join(re.escape(part) for part in re.split(r"<[^>]+>", rule.rule))
        for method in sorted(rule.methods - {"HEAD", "OPTIONS"}):
            if not re.search(rf"^\|\s*{method}\s*\|\s*`{path}`\s*\|", readme, re.M):
                missing.append(f"{method} {rule.rule}")
    assert missing == []


def test_the_readme_names_every_fault_kind_and_its_risk():
    readme = doc("README.md")
    names = list(FAULT_RISKS) + list(FAULT_RISKS.values())
    assert [name for name in names if f"`{name}`" not in readme] == []


def test_trust_layer_names_every_new_rule_check_and_risk():
    text = doc("TRUST_LAYER.md")
    rules = sorted(name for name in dir(eligibility) if name.endswith("_ok") and callable(getattr(eligibility, name)))
    checks = [check.__name__[len("check_"):] for check in EMBODIMENT_CHECKS]
    assert len(rules) == 8 and len(checks) == 10
    names = rules + checks + list(FAULT_RISKS.values())
    assert [name for name in names if f"`{name}" not in text] == []


def test_run_md_names_each_floor_the_soak_and_each_javascript_wrapper():
    text = doc("RUN.md")
    wrappers = sorted(os.path.basename(path) for path in glob.glob(os.path.join(ROOT, "backend", "test_*_js.py")))
    assert wrappers
    names = [f"WAREHOUSE_LAYOUT={name}" for name in sorted(LAYOUTS)] + ["python -m backend.soak"] + \
        [f"backend/{name}" for name in wrappers]
    assert [name for name in names if name not in text] == []
```


- [ ] **Step 2: Run them to see them fail**

Run: `.venv/bin/python -m pytest -o addopts="" -q backend/test_docs_examples.py`
Expected: `6 failed`:
- `test_the_policy_example_sets_only_known_keys_and_every_fault_risk_off`: the seven risks read `None`, not `0.0`;
- `test_copying_the_policy_example_changes_no_policy_in_effect`: `assert None == 'robot_cell_access'` (no `CLEAR_JAM`);
- `test_the_readme_lists_every_operations_route`: 10 routes missing, the first `GET /api/shift`;
- `test_the_readme_names_every_fault_kind_and_its_risk`: 14 names missing, the first `scan_miscount`;
- `test_trust_layer_names_every_new_rule_check_and_risk`: 25 names missing, the first `box_kind_ok`;
- `test_run_md_names_each_floor_the_soak_and_each_javascript_wrapper`: 4 missing, the first `WAREHOUSE_LAYOUT=classic`.

- [ ] **Step 3: The policy example — the seven risks, `CLEAR_JAM`, and the robot classes as the code has them**

The example's `robot_classes` must equal `models.ROBOT_CLASS_PRESETS` entry for entry, in the same list order. The test compares the whole policy in effect before and after applying the example.

**Replace in** `policies.example.yaml`:

```yaml
# Every key below is optional — omit anything you don't want to override.
```

with:

```yaml
# Every key below is optional — omit anything you don't want to override.
#
# The values below ARE the built-in defaults, so copying this file as it
# is changes nothing: edit only what you want to change. Two things to
# know before you do:
#   - Under `config:` only keys backend/models.py's CONFIG already has take
#     effect; a misspelt key is silently ignored.
#   - certification_requirements, robot_classes and operator_roles each
#     REPLACE the whole built-in table (apply_policy in backend/policy.py),
#     so a custom table must keep every entry it still needs.
```

**Replace in** `policies.example.yaml`:

```yaml
  FALSE_SUCCESS_RISK: 0.0
```

with:

```yaml
  FALSE_SUCCESS_RISK: 0.0
  # Injected faults on the distribution-centre floor (backend/faults.py),
  # each 0.0-1.0, the chance per opportunity, and off by default. Each
  # does its damage silently and is caught after the fact (TRUST_LAYER.md
  # says by what). For a single occurrence on demand, use
  # POST /api/faults/<kind> or the dashboard's Shift panel (Inject fault).
  SCAN_MISCOUNT_RISK: 0.0        # a drone's count reports the true number +/- 1-3 (count_consistent)
  WRONG_LEVEL_RISK: 0.0          # a forklift places a pallet a level up or down but reports the requested one (placement_level_correct)
  GRASP_FAIL_RISK: 0.0           # an arm or the picker misses a grasp; retried up to twice, a third miss fails the job
  CONVEYOR_JAM_RISK: 0.0         # a conveyor cell jams as an item moves on (CONVEYOR_JAMMED, then a CLEAR_JAM job)
  HANDOFF_LOSS_RISK: 0.0         # an item put onto the conveyor never arrives, though the giver reports it placed (handoff_consistent)
  MIS_SORT_RISK: 0.0             # the sorter drops a carton on the wrong dock (sort_correct)
  MISDECLARED_WEIGHT_RISK: 0.0   # an inbound pallet really weighs 1.1-1.6x what it declares (payload_within_limit)
```

**Replace in** `policies.example.yaml`:

```yaml

certification_requirements:
```

with:

```yaml

# IMPORTANT: apply_policy REPLACES the whole built-in table with this one,
# so a custom table must keep every entry it still needs. Leave CLEAR_JAM
# out and a person clearing a jam inside a fenced pack cell needs no
# robot_cell_access at all: anyone on shift, Riley included, could be sent in.
certification_requirements:
```

**Replace in** `policies.example.yaml`:

```yaml
  OPERATOR_MAINTENANCE_SIGNOFF: electrical_safety
```

with:

```yaml
  OPERATOR_MAINTENANCE_SIGNOFF: electrical_safety
  CLEAR_JAM: robot_cell_access   # inside a pack cell only; scoped to the arm's model
```

**Replace in** `policies.example.yaml`:

```yaml
# in ERROR (see Simulator._auto_charge / DigitalTwin.request_charge).
robot_classes:
```

with:

```yaml
# in ERROR (see Simulator._auto_charge / DigitalTwin.request_charge).
# A custom roster must also include ARM and HUMANOID: the
# distribution-centre floor's seed creates both, and an unknown class
# stops it ("Unknown robot_class 'ARM'"), so the app won't start on that
# floor. Keep each class's distribution-centre job types too (UNLOAD_TRUCK,
# PATROL, PACK_ORDER, ...), or the gate refuses those jobs.
robot_classes:
```

**Replace in** `policies.example.yaml`:

```yaml
      - CHARGE_ROBOT
  SCOUT:
```

with:

```yaml
      - CHARGE_ROBOT
      - UNLOAD_TRUCK
      - PUTAWAY_PALLET
      - RETRIEVE_PALLET
      - LOAD_TRUCK
  SCOUT:
```

**Replace in** `policies.example.yaml`:

```yaml
      - CHARGE_ROBOT
  HEAVY_HAULER:
```

with:

```yaml
      - CHARGE_ROBOT
      - PATROL
  HEAVY_HAULER:
```

**Replace in** `policies.example.yaml`:

```yaml
      - BATCH_DELIVER
  DRONE:
```

with:

```yaml
      - BATCH_DELIVER
      - UNLOAD_TRUCK
  DRONE:
```

**Replace in** `policies.example.yaml`:

```yaml
      - CHARGE_ROBOT
  PICKER:
```

with:

```yaml
      - CHARGE_ROBOT
      - CYCLE_COUNT
  PICKER:
```

**Replace in** `policies.example.yaml`:

```yaml
      - MOVE_BOX
      - CHARGE_ROBOT

# Same "replaces the whole roster if present" rule as robot_classes above.
```

with:

```yaml
      - MOVE_BOX
      - CHARGE_ROBOT
      - PICK_ITEMS
  # The distribution centre's fixed pack-cell arms: mains-powered, so
  # CHARGE_ROBOT never actually runs, but the rule above still applies.
  ARM:
    label: "Arm (fixed pack cell)"
    speed: 0.7
    allowed_task_types:
      - CHARGE_ROBOT
      - PACK_ORDER
  # The distribution centre's humanoid: it works only while a supervisor
  # with humanoid_supervision is near (Jordan on the seeded floor).
  HUMANOID:
    label: "Humanoid (supervised, general purpose)"
    speed: 0.8
    allowed_task_types:
      - PICK_AND_DELIVER
      - PICK_BOX
      - DELIVER_BOX
      - MOVE_BOX
      - MOVE_ROBOT
      - CHARGE_ROBOT
      - TOTE_TO_STATION
      - RETURN_TOTE
      - RETURNS_PUTAWAY

# Same "replaces the whole roster if present" rule as robot_classes above.
```


- [ ] **Step 4: `TRUST_LAYER.md` — rewritten short: concepts to code, Tier 2 as built, the physical trust layer**

Replace the whole file (128 lines). The Tier 2 table is checked against the code: `Simulator._check_authorization_changes`, `backend/policy.py` and `backend/decision_graph.py` exist, and `evals/` records no grader model or prompt version. The new test needs every rule, check and risk name in backticks.

**Rewrite** `TRUST_LAYER.md`:

````markdown
# Trust Layer — mapping from "The Trust Layer for the Physical AI"

The reference document ("The Trust Layer for the Physical AI", Cytex /
AICenturion) describes an enterprise product that checks, before and after a
job, whether a given combination of AI agent, robot and person can be trusted
with it. This project is a single-warehouse simulation, so **the depth doesn't
match**: no real hardware, firmware, employee database or model calling the
shots. What is built is the document's structural idea at toy scale: three
actor classes, each with a toy assurance passport, converging in one task
record that is graded on who was involved and on what happened. This file maps
each concept to its code. On the distribution-centre floor a physical layer
sits on top (below). For the module map see [ARCHITECTURE.md](ARCHITECTURE.md).

## Tier 1 — easy (built, for all three actor classes)

| Concept | Here | Code |
|---|---|---|
| Three actor classes | Robot, AI agent and human operator are each first-class | `backend/robot.py`, `agent.py`, `operator.py` |
| Entities check, per actor | Robot: not `ERROR` or `STOPPED`, approved firmware, not critically low on battery. Agent: not `ERROR`, approved model. Operator: on duty, holds the required certification | `check_entities_valid` in `backend/eval_engine.py` |
| Robot, Agent and Worker Assurance Passports (toy) | Firmware, model version and certifications checked against approved baselines | `models.APPROVED_FIRMWARE_VERSIONS`, `APPROVED_AGENT_MODELS`, `CERTIFICATION_REQUIREMENTS` |
| Mission Authorization Record | One artifact naming who and what was involved in a task and whether it was trustworthy | `build_mission_record` and the `extract_*` helpers in `backend/eval_engine.py` |
| Interaction in the environment | The document's maintenance mission: an agent recommends, a robot really navigates, a human signs off | `TaskType.MIXED_MAINTENANCE_MISSION`; `TaskManager.start_task` and `complete_task` |
| Pre-execution authorization gate | `TaskManager.validate` refuses a task (`422`) when a named robot, agent or operator is ineligible; `AUTO` selection skips them. The rule lives once and the grade shares it, so the two can't disagree. A critical battery is deliberately not gated: the planner prepends a recharge detour | `backend/eligibility.py`, `backend/task_manager.py` |
| Per-entity work authorization | A robot's optional `allowed_task_types` allowlist, checked by the same rule at creation and in `AUTO` scoring | `Robot.allowed_task_types`, `DigitalTwin.set_robot_capabilities` |
| Agent and operator own work | `AGENT_REPLAN`, `AGENT_AUDIT`, `OPERATOR_APPROVAL`, `OPERATOR_MAINTENANCE_SIGNOFF`: instant, gated and graded, each reading live twin state | `TaskManager._run_instant` |
| Evaluation Factory | A repeatable automated evaluation harness | `evals/` ([evals/README.md](evals/README.md)) |
| Metrics | Mission success rate, blocked jobs, coverage | `compute_metrics`, `python -m backend.run_evals --stats` |

## Tier 2 — medium (three of four built)

| Concept | What it means here | Status |
|---|---|---|
| Authorization-changing events | A robot that becomes ineligible mid-task is flagged, not stopped (it may already be mid-route). `Simulator._check_authorization_changes` re-checks every running task every `AUTHORIZATION_CHECK_EVERY_TICKS` ticks, emits one `TASK_AUTHORIZATION_CHANGED` per task, and `entities_valid` grades it `WARN` | Built: `backend/simulator.py` |
| Governance layer (policy-as-code) | An optional `policies.yaml` overrides `CONFIG`, the approved baselines, `CERTIFICATION_REQUIREMENTS`, the robot classes and the operator roles: the tables the gate and the eval engine both read. `POST /api/policies/reload` re-reads it live; `GET /api/policies` shows what is in effect | Built: `backend/policy.py`, `policies.example.yaml` |
| Decision graph (queryable history) | "Every task assigned to Robo-01 while battery < 20 %" answered across all task logs by filtering each log's mission record, with no graph database | Built: `backend/decision_graph.py`, `GET /api/decisions` |
| Agent assurance record for the Groq grader | Record which model and prompt version graded each task, beside the verdict | Not built: `evals/` records neither |

## Tier 3 — complex or not applicable (not planned)

Real SBOMs and cryptographic attestation, real HRIS integration, an enterprise Physical Work Graph, a Decision Twin (counterfactual re-simulation), change-triggered re-evaluation across dependent missions, a cyber-physical security dimension and a human-factors dimension. Each needs something a single-process simulation doesn't have: real hardware, employees, a network boundary or human behaviour to grade.

## The physical trust layer (distribution-centre floor)

The reference document asks whether this agent, robot and person can be trusted with *this* job. The distribution-centre floor ([spec §10](docs/superpowers/specs/2026-09-30-multi-embodiment-operations-design.md)) adds the physical half: can this body lift this load, reach this level, fit this aisle, fly over this cell, and work with these people around. It is checked before the job, re-checked during it and graded from its log afterwards. It applies only to robots with a floor profile (`robot.mobility`); classic robots and logs behave exactly as before.

### The eligibility rules (`backend/eligibility.py`)

Eight functions of plain values, each returning `(ok, reason)`, so the gate, robot selection, the mid-job re-check and the evaluation can't disagree.

| Rule | Holds when | Runs in |
|---|---|---|
| `payload_ok` | the weight is at most the body's payload | gate and selection (declared weight), re-check, `payload_within_limit` (true weight) |
| `box_kind_ok` | the body handles that box kind (`PALLET`, `TOTE`, `ITEM`, `CARTON`) | gate and selection |
| `reach_ok` | the slot level is within the body's reach | gate and selection, re-check, the `LIFT_TO` and `SCAN` steps, `reach_within_limit` |
| `clearance_ok` | a WIDE robot's route stays on WIDE cells | routing (`Warehouse.passable`), then `clearance_respected` |
| `no_fly_ok` | an air route avoids every no-fly cell | routing, then `no_fly_respected` |
| `drone_round_trip_ok` | a drone's battery covers the flight plus a 25 % reserve | gate and selection, drones only; a hard gate |
| `supervision_ok` | someone on shift holds a valid, in-scope credential of the kind the body needs | gate and selection, re-check; being *near* is a runtime wait |
| `cert_scope_ok` | a person's credential covers the equipment model and the site | the gate, for every certification check on the floor |

- **Gate** (`TaskManager.validate`, via `capability_reason`): the body the job needs, the box's kind and declared weight, the slot level, that supervision can be had, a drone's round trip. A job the body can't do is a `422` with the reason.
- **Selection**: `AUTO` keeps only robots whose body passes the same checks and that have a route under their own profile; a job no robot could do is rejected with the robots' reasons.
- **During the job**: `physical_recheck` repeats payload, reach and supervision from `_check_authorization_changes`. Like the rest of that re-check it flags and never cancels.
- **Evaluation**: the checks below grade the log, with the true weight and the level a box really reached.
- **Battery**: a ground robot with a critical battery gets a charging detour, not a rejection; only a drone, which can't detour mid-flight, is refused.

### Physical waits

Rules that depend on where people are hold the robot instead of rejecting the job. It goes `WAITING` with a `wait_reason`, and the log records `ROBOT_SAFETY_WAIT` and `ROBOT_SAFETY_RESUMED`.

| `wait_reason` | When |
|---|---|
| `PERSON_IN_AISLE` | a forklift or hauler would drive into a zone with a person in it |
| `PERSON_IN_CELL` | a person is inside an arm's pack cell |
| `SUPERVISOR_ABSENT` | the humanoid's supervisor is off shift, or not in its zone or one beside it |
| `PERSON_ON_CROSSING` | a robot is at a walkway crossing (or a drone about to cross) while someone walks across |
| `CONVEYOR_JAMMED` | an arm is downstream of a conveyor jam |

A wait longer than `SAFETY_WAIT_ESCALATE_S` emits `SAFETY_WAIT_ESCALATED` once, and the Shift panel lists it.

### The ten physical checks (`backend/eval_engine.py`)

`EMBODIMENT_CHECKS`, part of `DEFAULT_CHECKS`. Each answers "not applicable" (a `PASS` with `applicable: false`) when the log carries none of its data, so classic logs grade as before. Each has a pass and a fail fixture in `logs/eval_examples/multi_embodiment/`, graded by `backend/test_trust_checks.py`.

| Check | Fails when |
|---|---|
| `payload_within_limit` | a pick or lift's load truly weighs more than the robot's payload |
| `reach_within_limit` | a lift, scan or placement is above the robot's reach |
| `clearance_respected` | a WIDE robot's route crosses a NARROW cell |
| `no_fly_respected` | a drone's route crosses a no-fly cell |
| `human_zone_clear` | a forklift or hauler entered a zone, or an arm moved, while a person was there |
| `supervision_maintained` | the humanoid took a step unsupervised |
| `count_consistent` | a cycle count reported success with a count that isn't what was really there |
| `handoff_consistent` | a hand-off the giver reported made never reached the receiver |
| `sort_correct` | the sorter dropped a carton on a dock other than its order's lane |
| `placement_level_correct` | a box went to a different level from the one it was sent to |

### System checks (`backend/ci_engine.py`)

Mock CI follows the floor's layout: the environment check requires the layout's own `required_zones` and that the cells each body class uses (narrow ground, wide ground, air) are connected. A flying drone is accepted over any flyable cell and an arm on its station, mains-powered arms skip the battery check, and collisions compare `(layer, x, y)`, so a drone over a ground robot is not one.

### Injected faults (`backend/faults.py`)

Seven `CONFIG` risks, 0.0 by default. Each fault does its damage silently, and the trust layer catches it afterwards. Fault kinds and how to inject one on demand: [README.md](README.md#faults).

| Risk | What goes wrong | Caught by |
|---|---|---|
| `SCAN_MISCOUNT_RISK` | a drone's count is the true number ± 1–3 | `count_consistent` |
| `WRONG_LEVEL_RISK` | a forklift puts a pallet a level up or down but reports the requested one | `placement_level_correct`; the next count of that face finds the variance |
| `GRASP_FAIL_RISK` | an arm or the picker misses a grasp; two retries, and a third miss fails the job | no check of its own: each miss is on the job's GRASP step, and the third fails the job with that reason |
| `CONVEYOR_JAM_RISK` | a conveyor cell jams as an item moves on and the arms downstream pause | no check of its own: `CONVEYOR_JAMMED`, then a `CLEAR_JAM` job for a qualified person |
| `HANDOFF_LOSS_RISK` | an item put on the conveyor never arrives, though the giver reports it placed | `handoff_consistent` |
| `MIS_SORT_RISK` | the sorter drops a carton on the wrong dock | `sort_correct` |
| `MISDECLARED_WEIGHT_RISK` | an inbound pallet weighs 1.1–1.6 × what it declares | `payload_within_limit` |

`backend/test_fault_matrix.py` runs the seeded floor with each risk alone at 0.2 and asserts that its check (or, for the two without one, its evidence) catches it and no other check fails.

### Where it lives

| File | Role |
|---|---|
| `backend/eligibility.py` | the eight rules, beside the actor rules |
| `backend/embodiment.py` | `MobilityProfile`, a robot's body read from its catalog model |
| `backend/task_manager.py` | `capability_reason` (gate and selection), `physical_recheck` |
| `backend/people.py` | supervision queries, walkway crossings |
| `backend/simulator.py` | the physical waits, `ROBOT_STEP` events, where each fault does its damage |
| `backend/eval_engine.py`, `ci_engine.py` | the ten checks; the layout-driven system checks |
| `backend/faults.py` | `FAULT_RISKS`, `FaultInjector` |
````

- [ ] **Step 5: `README.md` — rewritten short: features, quick start, the two floors, the shift, faults, dashboard, one API table**

Replace the whole file (165 lines). Every route the app serves has a place in the API table; the operations routes each have a `| METHOD | `path` |` row of their own, which the test reads.

**Rewrite** `README.md`:

````markdown
# Warehouse Digital Twin

A Flask simulator of a warehouse with a plain-JavaScript dashboard: robots of
eight body types, people, goods, a conveyor, and a trust layer that checks
every job before it runs and grades it from its own log afterwards. You give
high-level instructions ("pick Box-A and deliver it to the loading zone") and
the robots work out how: they plan, route around each other with A\*, watch
their batteries and report every step as an event. CPU only: no GPU, ROS 2,
Gazebo or Isaac Sim required, though `Simulator._tick_robot` is the one place a
robot moves and `Warehouse.to_dict()` already emits the floor as data, so each
is a clean seam.

It runs one of two floors: the 32×20 **distribution centre** (the default) or
the original 20×15 **classic** floor.

## Features

- **Trust gate**: every task (even the system's own) is checked for robot, agent and operator eligibility, and on the distribution centre for the body and the people around it, before anything moves. A refusal is a `422` with the reason. See [TRUST_LAYER.md](TRUST_LAYER.md).
- **Eval engine**: grades a finished task's JSON log, PASS, WARN or FAIL per check with plain-language reasons (`python -m backend.run_evals`, `GET /api/tasks/{id}/eval`). The core checks cover terminal state, path, battery, collision, stuck or deadlock, controller errors, interruption, log sequence, `entities_valid` and `state_transition`; the distribution centre adds physical checks ([TRUST_LAYER.md](TRUST_LAYER.md)). `logs/eval_examples/` holds one example log per failure case, named for what it shows, with the expected verdicts pinned in `backend/test_eval_engine.py`.
- **Promptfoo suites**: the same grading run through promptfoo, plus a Groq LLM-as-judge suite ([evals/README.md](evals/README.md)).
- **Mock CI**: consistency checks on the live twin (`POST /api/ci/run`).
- **Tasks and navigation**: pick, deliver, move, charge, batch, inspection and sign-off tasks, four priorities, `AUTO` robot selection, per-robot task restrictions, A\* with live replanning and deadlock sidestep, battery-aware plans with recharge detours.
- **Jobs and shift** (distribution centre): pallet, tote, pick, pack, count and patrol jobs (listed in [ARCHITECTURE.md](ARCHITECTURE.md), with their fields in the dashboard's **Create task** form), and a seeded shift engine that turns trucks, orders, counts and returns into chains of them.
- **Faults**: seven injectable faults, each caught by a check or the evidence it leaves.
- **Fleet and workforce inventory**: SQLite source systems (robot catalog, assets, work orders, OTA releases; workers, credentials, training) bound to the live robots and operators, with their own page at `/fleet.html`.
- **Policies**: an optional `policies.yaml` overrides config values, approved baselines, certification requirements, robot classes and operator roles; reload it live.
- **Governance extras**: robot classes and operator roles (`ROBOT_CLASS_PRESETS` and `OPERATOR_ROLE_PRESETS` in `backend/models.py`, written out in `policies.example.yaml`), operator shifts, two-person sign-off, predictive maintenance, mid-task re-checks, collision and false-success risk.
- **Decision history**: search every graded task by robot, agent, operator, type, verdict or battery (`GET /api/decisions`, `python -m backend.decision_graph`).
- **Chat agent**: a Groq tool-use agent that creates, cancels and queries tasks through the same gate (needs `GROQ_API_KEY`); optional live narration of the agent's replan and audit tasks.
- **Scheduler**: recurring tasks on an interval.
- **Reports**: graded mission history as CSV or a printable page.
- **Logging**: structured records with levels and categories, kept in memory and under `logs/` (one JSON file per task), searchable with `GET /api/logs` and exportable.
- **Realtime dashboard**: Server-Sent Events for the floor, tasks, logs, CI and chat; save, load and reset the whole twin as JSON.
- **Tests**: pytest suites that drive the simulation tick by tick, and JavaScript unit tests for the dashboard's floor model under macOS's `jsc` ([RUN.md](RUN.md)).
- **Soak**: `python -m backend.soak` runs a fixed-seed, 20 000-tick shift of the distribution centre and reports collisions, failed checks and tick time ([RUN.md](RUN.md)).

## Quick start

`./run.sh` creates `.venv`, installs `requirements.txt` and starts the app; open <http://127.0.0.1:5000>. Prerequisites, manual setup, ports and tests are in [RUN.md](RUN.md); `WAREHOUSE_HOST` and `WAREHOUSE_PORT` set the address.

`WAREHOUSE_LAYOUT` picks the floor, and an unknown name is refused before anything is written. Each floor keeps its own files, so switching floors never reseeds the other's inventory:

| Floor | `WAREHOUSE_LAYOUT` | Logs | Data (inventory file, state save) |
|---|---|---|---|
| Distribution centre (default) | `distribution_center` | `logs/distribution_center/` | `data/distribution_center/` |
| Classic | `classic` | `logs/` | `data/` |

`WAREHOUSE_LAYOUT=classic ./run.sh` starts the classic floor.

## The two floors

**Distribution centre.** Goods flow west to east: trucks unload at the west docks, forklifts put pallets in the racks, AMRs bring totes to the pick stations, picked items ride the conveyor to the pack arms, and cartons leave through the sorter. It boots with its full seed (robots of all eight types, ten workers, pallets on racks, totes on shelves) and the simulation running. The floor is data (`backend/layouts/distribution_center.py`); [spec §3](docs/superpowers/specs/2026-09-30-multi-embodiment-operations-design.md) describes every zone. Each robot is bound to a fleet-inventory asset whose catalog model sets its body: speed, clearance, payload, reach, step timings, battery. Two robots never work, by design: one is held by an open work order, the other reports a recalled release and the gate refuses it every job. People (the workforce inventory's workers) are zone presence; robots obey them as waits, not rejections ([TRUST_LAYER.md](TRUST_LAYER.md)).

| Letter | Type (`robot_class`) | Does |
|---|---|---|
| A | AMR (`AMR`) | carries totes between shelves and stations; also the classic jobs |
| F | Forklift (`FORKLIFT`) | unloads trucks, puts pallets away, retrieves them, loads trucks |
| H | Heavy hauler (`HEAVY_HAULER`) | unloads light pallets from trucks |
| K | Picker (`PICKER`) | picks items from a tote onto the conveyor |
| S | Scout (`SCOUT`) | patrols a loop and reports halted robots |
| D | Drone (`DRONE`) | counts pallet-rack faces from the air layer |
| R | Arm (`ARM`) | fixed in a pack cell; packs customer orders |
| U | Humanoid (`HUMANOID`) | carries totes and puts returns away, only while its supervisor is near |

**Classic.** A 20×15 grid (racking, pick faces, charging, loading, unloading, packing, parking, a restricted area) seeded with Robo-01 and Robo-02, Box-A to Box-E and two demo tasks, already working. Its grid, seed and behaviour are unchanged by the distribution-centre work.

## The shift

The distribution centre's **shift starts paused**, even though the simulation runs from boot: nothing generates work until you press **Start** on the dashboard's Shift panel or call `POST /api/shift/start` ([RUN.md](RUN.md) has the command). **Pause** stops new work; orders in flight carry on. The engine makes trucks, customer orders, pallet orders, cycle counts, patrols, returns and departures (`rates` keys `trucks`, `customer_orders`, `pallet_orders`, `cycle_counts`, `patrols`, `returns`, `departures`, per sim-hour, all scaled by `pace`), draws from its own seeded generator (`seed`, default 42), and creates every job through the same gate as anyone else. An order is a chain of jobs; a failed stage is retried once, then the order is `FAILED` with the reason and the Shift panel lists it. Change `pace`, `seed` or `rates` live with `POST /api/shift/config`.

## Faults

Seven faults can be injected. Each is a `CONFIG` risk, 0.0 by default; set one in `policies.yaml`'s `config` block, and `GET /api/policies` shows the values in effect.

| Kind (`POST /api/faults/{kind}`) | Risk (`CONFIG`) |
|---|---|
| `scan_miscount` | `SCAN_MISCOUNT_RISK` |
| `wrong_level` | `WRONG_LEVEL_RISK` |
| `grasp_fail` | `GRASP_FAIL_RISK` |
| `conveyor_jam` | `CONVEYOR_JAM_RISK` |
| `handoff_loss` | `HANDOFF_LOSS_RISK` |
| `mis_sort` | `MIS_SORT_RISK` |
| `misdeclared_weight` | `MISDECLARED_WEIGHT_RISK` |

To inject one occurrence on demand, pick a kind under **Inject fault** on the Shift panel and press **Inject**, or `POST /api/faults/{kind}` with an optional `count` ([RUN.md](RUN.md) has the command). An armed fault fires at its next opportunity whatever the risk; a conveyor jam lands wherever an item next leaves (it takes no cell), and a reset or a load clears faults still armed. What each fault does and what catches it: [TRUST_LAYER.md](TRUST_LAYER.md).

## Dashboard

- **Both floors**: the floor canvas with live routes; tasks, robots (with Stop, Resume, Charge, Reset and per-robot capabilities), boxes, fleet load, statistics and trends; Mock CI, the event timeline and live logs; decision search, recurring tasks, policies and the collision-risk slider; mission reports; simulation controls with an **Emergency stop** that freezes everything and keeps tasks intact.
- **Distribution centre**: the floor drawn from its own cell-type table, a glyph and the letter above per robot, an amber ring while a robot waits for a safety reason, an **Air layer** switch for drones and the no-fly overlay, people, and the conveyor with jams in red.
- **Robot panel**: click a robot (or open `/?robot=<id>`) for its model, asset, limits, job, step and wait reason, and a link to its inventory record.
- **Shift panel**: clock and status, Start and Pause, pace, orders in flight and done, failed orders, safety escalations and **Inject fault**. Hidden on classic.
- **Job forms**: **Create task** shows only the fields the chosen type needs, with a one-line guide.
- **Phone width**: at 375 px the floor scales to fit and the panels stack, with no sideways scroll.

The twin is the only source of truth: the frontend renders snapshots and posts commands, and never decides where a robot is.

## API

Errors return `ok: false` with an `error` message and a `field`: `400` validation, `404` unknown id, `409` state conflict (an inventory lifecycle conflict, or a shift or equipment route on the classic floor), `422` a task the gate refused. Every operations route holds the twin's lock.

| Method | Path | Purpose |
|---|---|---|
| GET | `/`, `/{file}` | the dashboard and its static files |
| GET | `/api/stream` | Server-Sent Events: `state`, `log`, `event`, `ci`, `agent-chat` |
| GET | `/api/state`, `/api/warehouse`, `/api/health` | full snapshot with the floor plan and config; floor plan only; liveness |
| GET | `/api/robots`, `/api/robots/{id}`, `/api/boxes`, `/api/agents`, `/api/operators` | the entities |
| GET | `/api/tasks`, `/api/tasks/{id}` | tasks (`?status=&limit=`); one task with its events and logs |
| GET | `/api/logs`, `/api/events`, `/api/logs/export`, `/api/tasks/{id}/logs/export` | logs and events (filters), log export (`?format=txt\|json`) |
| GET | `/api/statistics`, `/api/statistics/history`, `/api/fleet/load`, `/api/maintenance/alerts` | statistics, trend samples, work per robot, wear alerts |
| POST | `/api/robots`, `/api/boxes`, `/api/agents`, `/api/operators` | create an entity (`robot_class` and `role` take presets) |
| POST | `/api/robots/{id}/{capabilities,stop,resume,charge,reset}` | per-robot commands |
| POST | `/api/operators/{id}/shift` | set or clear an operator's shift window |
| POST | `/api/tasks` | create a task; it goes through the gate (`422` on refusal) |
| POST | `/api/tasks/{id}/{cancel,pause,resume}` | task lifecycle |
| GET | `/api/schedules` | recurring tasks |
| POST | `/api/schedules`, `/api/schedules/{id}/toggle` | add one; enable or disable |
| DELETE | `/api/schedules/{id}` | remove one |
| POST | `/api/simulation/{start,pause,stop,reset,emergency-stop,resume,speed,tick}` | simulation control; `tick` advances one tick |
| POST | `/api/state/{save,load,reset}` | save, load or reset the twin; a save of another floor is refused (`400`) |
| GET | `/api/export/{state,tasks}` | whole state or tasks as JSON |
| POST | `/api/logs/clear` | clear logs, memory and files |
| POST | `/api/ci/run` | run Mock CI |
| GET | `/api/ci/status` | last CI run and history |
| GET | `/api/tasks/{id}/eval` | grade one task's persisted log |
| POST | `/api/evals/run` | grade every `logs/tasks/*.json` |
| GET | `/api/decisions` | decision history (`?robot=&agent=&operator=&type=&verdict=&battery_below=`) |
| GET | `/api/reports/missions.{csv,html}` | mission report |
| GET | `/api/policies` | the policy in effect |
| POST | `/api/policies/{reload,llm-narration,collision-risk,false-success-risk}` | reload `policies.yaml`; live, in-memory switches |
| POST | `/api/agent/chat` | the chat agent (`message`, optional `history`) |
| GET | `/api/shift` | the shift panel: status, clock, config, counters, throughput, in flight, backlog, failed orders |
| POST | `/api/shift/start` | start generating work |
| POST | `/api/shift/pause` | stop generating work; orders in flight carry on |
| POST | `/api/shift/config` | change `pace`, `seed` or `rates`; a bad value is a `400` and changes nothing |
| GET | `/api/orders` | orders, newest first (`?status=&kind=&limit=`) |
| GET | `/api/orders/{id}` | one order with its stages, jobs and attempts |
| GET | `/api/stock` | ledger locations (recorded, true and counted quantities) and totals (`?sku=`) |
| GET | `/api/people` | everyone: zone, destination, status |
| GET | `/api/equipment` | the conveyor, the sorter, the arms' cells, recent hand-offs |
| POST | `/api/faults/{kind}` | arm `count` occurrences of a fault (default 1) |
| GET | `/api/fleet/catalog/{models,parts}`, `/api/fleet/releases`, `/api/fleet/releases/{id}/sbom`, `/api/fleet/robots`, `/api/fleet/robots/{asset}`, `/api/fleet/robots/{asset}/history`, `/api/fleet/changes` | fleet manager: catalogs, releases and SBOMs, robot assets and history, change feed |
| POST | `/api/fleet/releases`, `/api/fleet/releases/{id}/recall`, `/api/fleet/robots`, `/api/fleet/robots/{asset}/{status,decommission,ota,work-orders}`, `/api/fleet/ota/{job}/{verify,rollback}`, `/api/fleet/work-orders/{wo}/{swap,close}`, `/api/fleet/components/{id}/calibrations` | publish or recall a release; commission, change, update and service a robot asset; record a calibration |
| PATCH | `/api/fleet/robots/{asset}` | correct a robot asset |
| GET | `/api/workforce/{credential-definitions,changes}`, `/api/workforce/workers`, `/api/workforce/workers/{id}`, `/api/workforce/workers/{id}/history` | workforce system: definitions, change feed, workers and history |
| POST | `/api/workforce/workers`, `/api/workforce/workers/{id}/{credentials,training,employment-status}`, `/api/workforce/credentials/{id}/{renew,verify,revoke}` | add a worker; record credentials, training and status; renew, verify or revoke a credential |
| PATCH | `/api/workforce/workers/{id}` | correct a worker |

## Custom robot classes

A `robot_classes` block in `policies.yaml` replaces the whole built-in roster. **It must include `ARM` and `HUMANOID`** (spec §15): the distribution centre's seed creates both, and an unknown class stops it (`Unknown robot_class 'ARM'`), so the app won't start on that floor. Keep each class's distribution-centre job types too, or the gate refuses those jobs. `certification_requirements` and `operator_roles` replace whole tables the same way, and the first must keep `CLEAR_JAM: robot_cell_access`. `policies.example.yaml` holds the built-in values; copying it as it is changes nothing.

## Documentation

| Document | What it holds |
|---|---|
| [RUN.md](RUN.md) | prerequisites, setup, running each floor, tests, the eval CLI, promptfoo, the soak, troubleshooting |
| [TRUST_LAYER.md](TRUST_LAYER.md) | the trust-layer concepts mapped to code, and the physical trust layer |
| [ARCHITECTURE.md](ARCHITECTURE.md) | the module map, the tick, a task's lifecycle, state and locking |
| [CLAUDE.md](CLAUDE.md) | the rules contributors work by |
| [evals/README.md](evals/README.md) | the promptfoo suites, and how to add an example log |
| [policies.example.yaml](policies.example.yaml) | the built-in policy, written out |
| [Multi-embodiment spec](docs/superpowers/specs/2026-09-30-multi-embodiment-operations-design.md) | the binding design of the distribution centre |
| [Fleet and workforce spec](docs/superpowers/specs/2026-09-30-fleet-workforce-inventory-design.md) | the inventory source systems |
| [Plans](docs/superpowers/plans/) | the implementation plans: [fleet and workforce](docs/superpowers/plans/2026-09-30-fleet-workforce-inventory.md), [1a floor and motion](docs/superpowers/plans/2026-09-30-multi-embodiment-1a-floor-and-motion.md), [1b jobs, rules and shift](docs/superpowers/plans/2026-10-01-multi-embodiment-1b-jobs-rules-shift.md), [1c seed, API, dashboard and soak](docs/superpowers/plans/2026-10-05-multi-embodiment-1c-seed-api-dashboard-soak.md) |
````

- [ ] **Step 6: `RUN.md` — commands only: both floors, the shift, tests with the `jsc` ones, the eval CLI, promptfoo, the soak**

Replace the whole file (100 lines).

**Rewrite** `RUN.md`:

````markdown
# RUN.md — setup and commands

Commands for running the app, its tests, the eval CLI, promptfoo and the soak.
Run them from the project root. What the project is: [README.md](README.md).

## 1. Prerequisites

| Tool | Version | For |
|---|---|---|
| Python | 3.14 (what the project's `.venv` runs) | the app, tests, eval CLI and soak |
| Node.js + npm | `^20.20.0 \|\| >=22.22.0` | promptfoo only |
| Groq API key | — | the Groq-judged promptfoo suite and the chat agent; free at [console.groq.com/keys](https://console.groq.com/keys) |
| JavaScriptCore (`jsc`) | built into macOS | the dashboard's JavaScript tests; they skip without it |

## 2. Setup

```bash
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt  # Flask, pytest and PyYAML
```

`./run.sh` does all of that and starts the app. The commands below assume the venv is active.

## 3. Run the app

```bash
WAREHOUSE_PORT=5055 python -m backend.app                                  # the distribution centre (the default)
WAREHOUSE_LAYOUT=classic WAREHOUSE_PORT=5055 python -m backend.app         # the classic floor
WAREHOUSE_LAYOUT=distribution_center WAREHOUSE_PORT=5055 python -m backend.app   # the default, spelled out
```

The dashboard is at `http://127.0.0.1:<port>`; stop with Ctrl+C. `WAREHOUSE_HOST` and `WAREHOUSE_PORT` default to `127.0.0.1` and `5000`. **On macOS the AirPlay Receiver holds port 5000, so use another port.** Each floor's `logs/` and `data/` folders are listed in the [README's quick start](README.md#quick-start).

The distribution centre's shift starts paused. Press **Start** on the Shift panel, or:

```bash
curl -X POST http://127.0.0.1:5055/api/shift/start
```

Inject one fault on demand from the Shift panel's **Inject fault**, or `curl -X POST http://127.0.0.1:5055/api/faults/conveyor_jam`.

## 4. Tests

```bash
python -m pytest -o addopts="" -q                                # the full suite, with its summary line
python -m pytest -o addopts="" -q backend/test_station_jobs.py   # one file
python -m pytest -o addopts="" -q backend/tests.py::test_name    # one test
python -m pytest -o addopts="" -q -k "collision or battery"      # by keyword
```

`pytest.ini` sets `-q`, so `-o addopts=""` is what lets the summary line print. Two failures are expected (section 8). The dashboard's JavaScript tests, `frontend/tests/*.js`, run under macOS's built-in JavaScriptCore (`/System/Library/Frameworks/JavaScriptCore.framework/Versions/A/Helpers/jsc`) through pytest wrappers that skip when `jsc` is absent; no Node is needed:

```bash
python -m pytest -o addopts="" -q backend/test_floor_model_js.py backend/test_panels_js.py
```

## 5. Eval CLI

Rule-based grading of task logs, no promptfoo and no API (checks: [TRUST_LAYER.md](TRUST_LAYER.md)).

```bash
python -m backend.run_evals                                      # every real logs/tasks/*.json
python -m backend.run_evals logs/tasks/task_006.json             # one file
python -m backend.run_evals --demo                               # the curated logs/eval_examples/
python -m backend.run_evals --stats                              # success rate, assurance coverage, top failing checks
python -m backend.run_evals --out logs/evals/report.json         # also save the full JSON report
python -m backend.run_evals --fail-under 5                       # exit 1 if fewer than 5 tasks pass
python -m backend.run_evals logs/distribution_center/tasks       # the distribution centre's own logs
python -m backend.run_evals logs/eval_examples/multi_embodiment  # the physical checks' pass and fail fixtures
```

## 6. Promptfoo

Three suites (live logs, curated examples, Groq-judged) live in `evals/`; their commands, the Groq key setup and the model choice are in [evals/README.md](evals/README.md). Install once with `npm install -g promptfoo` and call `promptfoo` directly, since `npx promptfoo` re-resolves over the network and can hang for minutes. Run the Groq suite with `--env-file evals/.env -j 1`: its token budget is small, and promptfoo only auto-loads a `.env` from the directory it runs in.

## 7. Soak

```bash
python -m backend.soak                                    # seed 42, pace 2, 20 000 ticks, every risk at 0
python -m backend.soak --ticks 6000 --risk grasp_fail=0.2 # a shorter run with one fault
python -m backend.soak --help                             # --ticks, --seed, --pace, --risk KIND=CHANCE (repeatable)
```

It boots the seeded distribution centre, runs the shift and prints a report: same-layer collisions, failures of the physical checks, orders done per kind, robots in `ERROR`, safety escalations, and the mean and longest tick against the 15 ms budget. `--risk` takes a fault kind or a `CONFIG` risk name ([README.md](README.md#faults)) and a chance from 0 to 1. `backend/test_soak.py` asserts the default run and `backend/test_fault_matrix.py` runs each fault at 0.2.

## 8. Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `test_real_task_006_box_conflict_is_caught` and `test_idle_robot_with_a_low_battery_charges_itself` fail | Known and allowed. The first reads `logs/tasks/task_006.json`, which each fresh run appends to (ids restart at `task_001`; Clear logs deletes the files); the second is pre-existing simulator behaviour: the robot's battery runs out before it reaches the charger. Any other failure is yours. |
| The distribution centre's robots stand still | Its shift starts paused: press **Start** on the Shift panel (section 3). |
| The app stops at start with `Unknown robot_class 'ARM'` | A `robot_classes` block in `policies.yaml` replaces the whole roster and lacks `ARM` or `HUMANOID`; `policies.example.yaml` holds the built-in one. |
| `POST /api/state/load` says "This save is of the classic floor, and this twin runs the distribution_center floor" | Each floor loads only its own saves: start the app with the `WAREHOUSE_LAYOUT` the message names. |
| `test_floor_model_js.py` and `test_panels_js.py` are skipped | `jsc` isn't at its macOS path (Linux, say). Expected; run them on a Mac. |
| `pytest` fails with `ModuleNotFoundError: No module named 'yaml'` inside `launch_testing` | An unrelated ROS pytest plugin is auto-loaded: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -p no:cacheprovider`. |
| A promptfoo command hangs for minutes, or says `Missing GROQ_API_KEY`, `404` or `413` | See section 6 and [evals/README.md](evals/README.md): call `promptfoo` directly, pass `--env-file evals/.env -j 1`, and use a model your key serves. |
| A task went straight to `FAILED` | Open it on the task board: the reason is on its card and in the log at `ERROR` level (a box already reserved, a destination that doesn't resolve, a robot in an error state, or the gate). |
| A robot sits in `WAITING` or `ERROR` | `WAITING`: it yields to another robot or waits on a person (the robot panel shows the reason; [TRUST_LAYER.md](TRUST_LAYER.md#physical-waits)). `ERROR`: its battery hit zero or its controller raised; press **Reset** on the robot. |
| Everything is confused after editing code | `POST /api/state/reset`, or **Reset** in the dashboard, puts the floor back as its seed left it. |
````

- [ ] **Step 7: The spec — §2, §4.3 and §6 "Movement" as the plan built them**

**Replace in** `docs/superpowers/specs/2026-09-30-multi-embodiment-operations-design.md`:

```markdown
- `backend/app.py`'s default twin uses `layout="distribution_center"` with `data/inventory.sqlite3`.
```

with:

```markdown
- `backend/app.py`'s default twin uses `layout="distribution_center"`; `WAREHOUSE_LAYOUT=classic` boots the classic floor instead. Each floor keeps its own folders: the distribution centre's inventory file and state save are in `data/distribution_center/` (`data/distribution_center/inventory.sqlite3`) and its logs in `logs/distribution_center/`, while classic keeps `data/` and `logs/`. Switching floors therefore never reseeds the other floor's inventory file.
```

**Replace in** `docs/superpowers/specs/2026-09-30-multi-embodiment-operations-design.md`:

```markdown
- **`load_state`** accepts v1 (treated as `classic`) and v2. If the saved layout differs from the loaded one, it rebuilds the warehouse, navigation and planner before restoring entities.
```

with:

```markdown
- **`load_state`** accepts v1 (treated as `classic`) and v2. A save whose layout differs from the twin's is refused before anything changes, with a `ValueError` naming both floors; it is not rebuilt. The inventory is per floor (§2), so another floor's robots would have no assets to bind to. To load such a save, start the app on the floor it names.
```

**Replace in** `docs/superpowers/specs/2026-09-30-multi-embodiment-operations-design.md`:

```markdown
**Movement.** A move from zone A to zone B takes `manhattan(centre A, centre B) × CELL_SIZE_M ÷ 1.2 m/s`. During the move the person is in neither zone. A move is "in transit on the walkway" only when it crosses the walkway: the two zone centres lie on opposite sides of the walkway strip, or either end is the walkway zone itself. Only such a move triggers crossing waits (§5.3). People are not grid entities and don't block robots, except through the rules below.
```

with:

```markdown
**Movement.** A move from zone A to zone B takes `manhattan(centre A, centre B) × CELL_SIZE_M ÷ 1.2 m/s`. During the move the person is in neither zone. A move is "in transit on the walkway" only when it crosses the walkway: either end is the walkway zone itself, or the two zones' cells are not all on one side of the walkway strip. Sides are decided by the zones' cells, not their centres, so a walk to or from a zone that straddles the strip (`cross_aisle`, `top_aisle`) always counts as crossing it. Only such a move triggers crossing waits (§5.3). People are not grid entities and don't block robots, except through the rules below.
```


- [ ] **Step 8: Run the new tests**

Run: `.venv/bin/python -m pytest -o addopts="" -q backend/test_docs_examples.py`
Expected: `6 passed`.

Check the sizes: `wc -l README.md RUN.md TRUST_LAYER.md`
Expected: 165, 100 and 128 lines (the caps are 250, 130 and 160).

- [ ] **Step 9: Run the full suite**

Run: `.venv/bin/python -m pytest -o addopts="" -q`
Expected: `2 failed, 920 passed` (Task 11 left 914). The two failures are the allowed ones.

- [ ] **Step 10: Commit**

```bash
git add README.md TRUST_LAYER.md RUN.md policies.example.yaml docs/superpowers/specs/2026-09-30-multi-embodiment-operations-design.md backend/test_docs_examples.py
git commit -m "$(cat <<'MSG'
docs: short README, RUN.md and TRUST_LAYER.md for the distribution-centre floor, and the policy example

README.md, RUN.md and TRUST_LAYER.md are rewritten whole, each short and
linking to the others instead of repeating them. The README covers the
project, a one-line-per-feature overview, the quick start with
WAREHOUSE_LAYOUT and the per-floor folders, both floors and the eight robot
letters, the shift (which starts paused), the seven faults, the dashboard
panels, every route in one table, and the warning that a custom
robot_classes block must include ARM and HUMANOID. RUN.md holds the
commands: setup, both floors, starting the shift, the tests (the jsc ones
included), the eval CLI, promptfoo and the soak. TRUST_LAYER.md maps each
concept to its code, corrects its Tier 2 list, and describes the physical
trust layer. The long reference sections are not carried over, and with them
the counts and claims the code had outgrown. policies.example.yaml gains the
seven risks, CLEAR_JAM and the new classes, so copying it changes nothing.

Spec amendments: §2, the new floor's own data/ and logs/ folders (Plan 1c
ruling 2); §4.3, a save from another floor is refused, not rebuilt (Plan
1c ruling 5); §6 Movement, walkway crossing is decided by the zones' cells,
not their centres (stale since plan 1a; plan 1b ruling 15, carried from
plan 1b's Task 5 review).

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
MSG
)"
```

---

## Measured

**The soak's tick (spec §1, §16; Plan ruling 7).** The §16 soak — `distribution_center` with its full seed (15 robots: 13 working, TR50-202 idle on its recalled release and TR50-103 stopped by its work order), shift seed 42, pace 2, 20 000 ticks (50 sim-minutes), every risk at 0 — measured with Tasks 1–11 on the dev machine (Apple M4 Pro, macOS 26, Python 3.14.6), timing `Simulator.tick()` alone:

| | Mean tick | Longest tick |
|---|---|---|
| Nine runs (`python -m backend.soak` and the tests' runs) | 0.57–0.62 ms | 6.2–8.5 ms, once 30.4 ms |
| Budget (§1) | 15 ms | — |

The mean is about 4 % of the budget. The pre-plan floor (12 robots, Plan ruling 7) measured 0.51 ms. The order book grows as the shift goes on: at 150 sim-minutes (60 000 ticks, same settings) the mean is 1.75 ms and the longest tick 39.5 ms — still well within the budget, so no pruning or indexing is needed in step 1.

**What the soak's shift does.** 436 jobs finish; the ten new checks apply to all of them that carry their data (from 10 jobs for `count_consistent` and `no_fly_respected` to 319 for `clearance_respected`) and none fails. Orders done: 26 customer, 5 inbound, 7 pallet, 10 count, 8 return; none failed. No collision, no robot in ERROR, no escalation, no failed job. The run emits 18 878 events, nearly four times the twin's 5 000-event buffer. The result is identical under `PYTHONHASHSEED` 1, 999 and random.

**The fault matrix (each risk alone at 0.2, seed 42, pace 2).** The ticks at which each fault's evidence first shows up, and the run each row of `backend/test_fault_matrix.py` uses:

| Fault | Caught by | First catches (tick) | Row's run |
|---|---|---|---|
| `scan_miscount` | `count_consistent` | 686, 2 649 | 3 000 ticks |
| `wrong_level` | `placement_level_correct` (and, ~9 500 ticks later, the next count's variance) | 13 035, 17 926 | 20 000 ticks |
| `grasp_fail` | the miss recorded on the GRASP step, retried (Plan ruling 11) | 217, 1 323 | 2 000 ticks |
| `conveyor_jam` | `CONVEYOR_JAMMED`, then a `CLEAR_JAM` (Plan ruling 11) | 249 / 260, 544 / 560 | 1 000 ticks |
| `handoff_loss` | `handoff_consistent` | 231, 1 150 | 2 000 ticks |
| `mis_sort` | `sort_correct` | 1 389, 6 476 | 7 000 ticks |
| `misdeclared_weight` | `payload_within_limit` | 4 277, 5 118 | 6 000 ticks |

Over a full 20 000 ticks at 0.2: `scan_miscount` 7 catches, `wrong_level` 2 (only 2 of its 7 landed rolls found a free slot a level up or down), `handoff_loss` 17 (and 17 customer orders FAILED), `mis_sort` 5, `misdeclared_weight` 4, `grasp_fail` 38 misses (6 of them a second miss, none a third), `conveyor_jam` about one jam a minute (15 customer orders done instead of 26).
