# Multi-Embodiment Operations, Telemetry & Hand-offs — Design (Sub-projects C + D + E)

**Date:** 2026-09-30
**Status:** All eight design sections approved in brainstorming. Scope change approved: E (hand-offs) folded into step 1.
**Repo:** `physical_ai/warehouse-digital-twin`
**Builds on:** Sub-project A, the fleet and workforce inventory. See `docs/superpowers/specs/2026-09-30-fleet-workforce-inventory-design.md`.
**Branch:** Built on a new branch cut from wherever A lands (see §17).

---

## 0. Program context and decisions

The program goal is to simulate a real-world physical-AI system:
- multiple robot embodiments doing real warehouse operations;
- a robot and worker inventory, stored in a unified database (PWA) and used as the robot passport;
- realistic per-job robot logs built from sensor data and decisions.

Program order was A → B → C → D → E → F. With A finished, this spec builds three pieces in two steps:
- **Step 1 = C + E:** multi-embodiment operations on a new floor, with the conveyor hand-off chain.
- **Step 2 = D:** per-robot sensor streams, decision logs, job logs, live view and replay.

After this spec: B (twin → PWA ingestion and passport re-mint), then F (the twin's gate consults PWA).

| # | Question | Decision |
|---|---|---|
| 1 | Spatial model | Keep the 2D grid and add layers and rules: ground and air layers, fixed stations, aisle clearance classes, shelf levels as an attribute. No real 3D. |
| 2 | Roster | AMR (tote runner), forklift, heavy hauler, piece picker, inspection scout, drone, arm, humanoid, plus the conveyor and sorter as floor equipment. |
| 3 | Humans | Modelled as zone presence. |
| 4 | Items | One `Box` entity with a kind: `PALLET`, `TOTE`, `ITEM`, `CARTON`. `CARTON` is new; packing produces it. |
| 5 | Sites | One simulated warehouse: a new, detailed 32×20 floor. All robots and workers live at site `WH-01`, and `WH-02` is removed from the app's inventory. |
| 6 | Floor plan | The approved flow-through (I-flow) plan in §3: inbound docks west, outbound docks east. |
| 7 | Build order | Design C and D together, then build in 2 steps (C, then D), each with its own plan and reviews. |
| 8 | Workload | The warehouse runs a shift on its own: trucks, orders, counts, patrols. Manual and agent jobs still work. |
| 9 | Sensor realism | Real-world imperfect: noise, bias, drift, dropouts and faults, tied to inventory state. |
| 10 | Viewing | A live robot panel plus a complete per-job log with replay. |
| 11 | Approach | Extend the existing simulator. Behaviour is driven by the inventory's model catalog, and layouts are named data. |
| 12 | B / E / F | E joins step 1. B and F come next, in that order. |

## 1. Goals, non-goals, success criteria

**Goals**
- The app boots a detailed 32×20 distribution-centre floor. All 15 non-retired inventory robots are on it, and the 14 in service do the work their embodiment does in a real warehouse.
- Robot capability comes from the inventory's model catalog. Editing a model or asset changes behaviour on the floor.
- The trust layer gains physically grounded rules (payload, reach, clearance, no-fly, people, supervision) that are shared by the gate and the evaluation.
- A deterministic shift engine keeps the floor busy, and order chains cross embodiments and conveyor hand-offs.
- Every job produces a complete log: robot-reported telemetry, ground truth, decisions and a summary. That log is what B ingests later.

**Non-goals** (see §18): real 3D, PWA ingestion (B), the gate consulting PWA (F), conveyors as inventory records, decanting pallets into totes.

**Success criteria**
- Every test that runs on the classic floor today still passes. The only exceptions are the 2 failures that already fail on `master`: `test_real_task_006_box_conflict_is_caught` and `test_idle_robot_with_a_low_battery_charges_itself`.
- A fixed-seed soak run on the new floor (§16) finishes with:
  - zero same-layer collisions and zero rule breaches;
  - completed orders of every kind;
  - no robot left in `ERROR`;
  - a mean tick of at most 15 ms with 15 robots on the dev machine. The measured number is recorded in the plan.
- In a browser, a user can:
  - start a shift;
  - watch each robot type work;
  - open any robot and see live sensors and decisions;
  - replay a finished job.

---

## 2. Architecture

New and changed units. Each unit has one job, and the twin imports them. Nothing in `backend/inventory/` changes except the seed (§4.1) and the release rows' new `known_issues` column (§13.2), which bumps the inventory `SCHEMA_VERSION` to 3.

| Unit | Responsibility | Step |
|---|---|---|
| `backend/layouts/__init__.py` | Layout registry: `LAYOUTS = {"classic": ..., "distribution_center": ...}`, `build_layout(name)` | 1 |
| `backend/layouts/classic.py` | Today's `Warehouse._build_layout` moved verbatim. Produces a byte-identical grid and zones. | 1 |
| `backend/layouts/distribution_center.py` | The 32×20 floor (§3): cells, zones, zone attributes, rack and shelf slots, stations, conveyor path, crossings | 1 |
| `backend/warehouse.py` | Gains `layout` (name), zone attributes, per-profile walkability queries, slot lookup. The public queries keep their signatures. | 1 |
| `backend/embodiment.py` | `MobilityProfile` built from a catalog model spec; walkability predicate per profile and layer; timing helpers (speed, lift, takeoff) | 1 |
| `backend/people.py` | Operator zone presence, transit between zones, `people_in(zone)`, supervisor proximity | 1 |
| `backend/goods.py` | Box kinds and weights (declared and true); `StockLedger` (slots → pallets and totes, recorded vs true quantity) | 1 |
| `backend/equipment.py` | `Conveyor` (segments, item flow, jams) and `Sorter` (diverts to dock lanes); hand-off records | 1 |
| `backend/operations/shift.py` | `ShiftEngine`: seeded generators for trucks, orders, counts, patrols, returns and people's activities; shift clock | 1 |
| `backend/operations/orders.py` | `Order` and job-chain orchestration: each stage creates the next job on completion, with a retry and a failure reason | 1 |
| `backend/seeds/classic.py` | Today's `load_demo` moved verbatim | 1 |
| `backend/seeds/distribution_center.py` | New-floor seed: robots bound to all active assets, operators bound to workers, goods, stock | 1 |
| `backend/eligibility.py` | New primitive-argument rule functions (§10.1), shared by the gate and the evaluation | 1 |
| `backend/task_planner.py`, `task_manager.py`, `simulator.py` | New job types and steps; capability filter in robot selection; layer-aware traffic; per-profile routing; safety waits | 1 |
| `backend/eval_engine.py`, `ci_engine.py` | New checks (§10.4); layout-driven system checks (§10.5) | 1 |
| `backend/operations_api.py` | `/api/shift`, `/api/orders`, `/api/stock`, `/api/people`, `/api/equipment`, `/api/faults` | 1 |
| `frontend/app.js`, `style.css`, `index.html` | Layout-driven floor, glyphs, air layer, people, conveyor, robot panel, shift panel | 1 |
| `backend/telemetry/sensors.py` | Sensor models per component type; imperfection model | 2 |
| `backend/telemetry/suites.py` | Builds a robot's sensor suite from its inventory component tree | 2 |
| `backend/telemetry/decisions.py` | `Decision` record and `decide()` helper used by robot behaviour | 2 |
| `backend/telemetry/recorder.py` | Per-run and per-job append-only JSONL writers, summaries, retention, in-memory ring buffers | 2 |
| `backend/telemetry_api.py` | Live SSE per robot, ring-buffer read, runs and jobs listing, replay data | 2 |
| `frontend/app.js` (robot panel) + `frontend/replay.html`, `replay.js`, `replay.css` | Live sensor panel with sparklines and decision feed in the dashboard; replay as its own page, like `fleet.html` | 2 |

**Construction.** `DigitalTwin(..., layout="classic")`:
- `classic` is the default, so every existing test constructs today's twin unchanged.
- `backend/app.py`'s default twin uses `layout="distribution_center"`; `WAREHOUSE_LAYOUT=classic` boots the classic floor instead. Each floor keeps its own folders: the distribution centre's inventory file and state save are in `data/distribution_center/` (`data/distribution_center/inventory.sqlite3`) and its logs in `logs/distribution_center/`, while classic keeps `data/` and `logs/`. Switching floors therefore never reseeds the other floor's inventory file.
- `DigitalTwin.layout_name` is exposed in `snapshot()`.

**Lock order** is unchanged from A: `twin.lock` → inventory store lock. The telemetry recorder does its file I/O outside `twin.lock`. The tick hands samples to a bounded queue, and one writer thread drains it (§13.6).

---

## 3. The distribution-centre layout (32×20)

The grid is x = 0…31, y = 0…19. **1 cell = 1.5 m**, so the building is 48 × 30 m. Perimeter walls sit at x=0, x=31, y=0 and y=19. Goods flow west → east.

### 3.1 Cell types

New `CellType` values:

| Value | Meaning |
|---|---|
| `DOCK` | A dock apron. Ground-walkable. |
| `DOCK_DOOR` | A wall cell drawn as a door. Not walkable. |
| `STAGING` | Staging floor. Ground-walkable. |
| `PALLET_RACK` | Not ground-walkable. Flyable. Has slots at levels 0–4. |
| `TOTE_SHELF` | Not ground-walkable. Flyable. Has slots at levels 0–2. |
| `WALKWAY` | People only. Robots may enter only crossing cells (§5.3). |
| `STATION` | Pick, returns and pack stations. Walkability comes from zone attributes. |
| `CONVEYOR` | Not walkable. Carries items. |
| `SORTER` | Not walkable. Diverts cartons. |
| `WORKSHOP` | Maintenance bay. Ground-walkable. |
| `DRONE_PAD` | Drones take off, land and charge here. Ground robots never enter. |

The existing `SHELF`, `STORAGE`, `PACKING`, `UNLOADING` and `LOADING` values stay and are used only by `classic`. `WALKABLE_CELLS` stays as it is for `classic`. On the new floor, walkability is decided per profile (§5.2).

### 3.2 Zones

Rectangles are given as inclusive x0–x1, y0–y1. "Clear" is the aisle clearance class: `WIDE` cells admit wide-clearance robots, and `NARROW` cells admit narrow-clearance robots only.

| Zone key | Label | Cells | Type | Attributes |
|---|---|---|---|---|
| `dock_1` | Dock 1 | x1–3, y2–5 | DOCK | inbound; clear WIDE; no-fly |
| `dock_2` | Dock 2 | x1–3, y7–10 | DOCK | inbound; clear WIDE; no-fly |
| `intake_staging` | Intake staging | x4–6, y2–10 | STAGING | clear WIDE |
| `pallet_aisle_1` | Pallet aisle 1 | x8–16, y3–4 | EMPTY | clear WIDE; serves racks y2 and y5 |
| `pallet_aisle_2` | Pallet aisle 2 | x8–16, y7–8 | EMPTY | clear WIDE; serves racks y6 and y9 |
| `pallet_racks` | Pallet racks | x8–16 at y2, y5, y6, y9 | PALLET_RACK | levels 0–4, 1.0 m apart (0.0 … 4.0 m) |
| `tote_aisle_1` / `_2` / `_3` | Tote aisle 1 / 2 / 3 | x8–16 at y13 / y15 / y17 | EMPTY | clear NARROW |
| `tote_shelves` | Tote shelves | x8–16 at y12, y14, y16, y18 | TOTE_SHELF | levels 0–2, 0.6 m apart (0.0, 0.6, 1.2 m); a shelf cell is served from any adjacent tote-aisle cell |
| `walkway` | Pedestrian walkway | x17, y1–18 | WALKWAY | people; crossings at (17,1), (17,10), (17,11), (17,13), (17,15), (17,17); crossings (17,10) and (17,11) are clear WIDE, the others NARROW |
| `main_aisle` | Main aisle | x7, y1–18 | EMPTY | clear WIDE |
| `cross_aisle` | Cross aisle | x4–30, y10–11 (excluding x17) | EMPTY | clear WIDE |
| `top_aisle` | Top aisle | x8–16, y1 and x18, y1–9 and y12–18 | EMPTY | clear NARROW |
| `pallet_lane` | Pallet lane | x25–26, y5–9 and x18–30, y5 | EMPTY | clear WIDE |
| `ne_floor` | North-east floor | x18–19, y6–9 and x19–30, y9 | EMPTY | clear NARROW |
| `charging_station` | Charging | x1–3, y12–14 | CHARGING | clear WIDE |
| `parking_area` | Parking | x4–6, y12–14 | PARKING | clear WIDE |
| `workshop` | Workshop | x1–6, y16–18 | WORKSHOP | clear WIDE; maintenance bay |
| `sw_floor` | South-west floor | x1–6, y15 | EMPTY | clear WIDE |
| `drone_pad` | Drone pad | x19–21, y1–3 | DRONE_PAD | drones only |
| `outbound_staging` | Outbound staging | x22–27, y1–4 | STAGING | clear WIDE; outbound pallets |
| `dock_3` | Dock 3 | x28–30, y1–4 | DOCK | outbound pallets; clear WIDE; no-fly |
| `returns_qc` | Returns / QC | x20–24, y6–8 | STATION | clear NARROW; humanoid home |
| `restricted_area` | Restricted (electrical) | x27–30, y6–8 | RESTRICTED | no robots; no-fly; people need `electrical_safety` |
| `pick_station_1` | Pick 1 | x19–21, y12–14 | STATION | robot picker; tote drop (19,14); work cell (20,14); conveyor infeed (20,15) |
| `pick_station_2` | Pick 2 | x19–21, y16–18 | STATION | human pick; tote drop (19,16); work cell (20,16); conveyor infeed (20,15) |
| `conveyor` | Conveyor | x19–24, y15 | CONVEYOR | flows +x into the sorter at x25 |
| `pack_cell_1` | Pack 1 | x22–24, y12–14 | STATION | fenced robot cell; no-fly; arm at (23,14), working conveyor cell (23,15) |
| `pack_cell_2` | Pack 2 | x22–24, y16–18 | STATION | fenced robot cell; no-fly; arm at (22,16), working conveyor cell (22,15) |
| `sorter` | Sorter | x25–27, y12–18 | SORTER | chutes to `dock_4` and `dock_5` |
| `dock_4` / `dock_5` | Dock 4 / 5 | x28–30, y12–14 / y16–18 | DOCK | outbound cartons; clear NARROW; no-fly |
| `patrol_loop` | Security patrol | the cell loop (7,1) → (18,1) → (18,11) → (7,11) → (7,1) | route | scout route |

Dock doors (`DOCK_DOOR`) sit at x0 for y2–5 and y7–10, and at x31 for y1–4, y12–14 and y16–18.

The layout exposes `required_zones`, which the system checks read (§10.5). They are `charging_station`, `parking_area`, `drone_pad`, `pick_station_1`, `pack_cell_1`, `sorter`, `dock_1` and `dock_4`.

**Clearance is a per-cell attribute.** A cell's clearance is `WIDE` if any zone containing it is marked WIDE, otherwise `NARROW` if any zone marks it NARROW. A walkable cell that no zone marks, such as (1..6, 1) or (1..3, 6), defaults to `NARROW`.

**A cell may belong to several zones.** Examples: `cross_aisle` overlaps a crossing cell's neighbours, and `patrol_loop` overlaps the aisles. `patrol_loop` is a route (its `route` attribute holds the loop's corner cells), not a place. `zone_of_cell` returns the most specific non-route zone: the smallest by cell count, with ties broken by declaration order. `zones_of_cell` returns all of them, routes included.

**Slots.** A slot is a (rack or shelf cell, level) pair. There are 4 × 9 × 5 = 180 pallet slots and 4 × 9 × 3 = 108 tote slots. Each slot is served from a face cell:
- pallet racks from the pallet aisle named in the table: rack y2 from y3, y5 from y4, y6 from y7, and y9 from y8;
- tote shelves from any adjacent tote-aisle cell.

---

## 4. The single site, the seeds and startup

### 4.1 Inventory seed profiles

`open_inventory(path, demo, clock, settings, profile="classic")` gains a `profile` argument:
- `"classic"`: today's seed, byte for byte, with WH-01 and WH-02. All of A's tests keep running against it unchanged.
- `"distribution_center"`: the new-floor seed. Every asset and worker is at `WH-01`, and every home zone is a real §3.2 zone key. `WH-02` does not exist.

`SEED_VERSION` and the template cache are per profile. A file opened under one profile but stored under the other is reseeded, with a `.bak` copy and a warning, exactly like A's seed-version mismatch.

Robot scenarios in the distribution-centre profile keep A's flags, adjusted to the physical floor:

| Asset | Model | Home zone | Scenario kept |
|---|---|---|---|
| AST-000101, AST-000102 | AC-TR50 | parking_area | healthy, 2.1.0 |
| AST-000103 | AC-TR50 | workshop | open corrective work order (lidar swap). Stopped by A's lifecycle hold. |
| AST-000201 | AC-TR50 | tote_aisle_1 | healthy baseline |
| AST-000202 | AC-TR50 | tote_aisle_2 | reports recalled 2.2.1. That release carries a known odometry drift in step 2. |
| AST-000203 | AC-PK30 | pick_station_1 | lidar calibration missing |
| AST-000204 | AC-SC1 | patrol_loop | lidar and IMU calibration missing |
| AST-000205 | NW-PF1200 | pallet_aisle_1 | hw rev A, so it can't take 2.2.0 |
| AST-000206 | NW-PF1200 | pallet_aisle_2 | OTA to 2.2.0 in REPORTED, awaiting verify |
| AST-000207 | NW-HH300 | dock_2 | **online.** A's OFFLINE scenario only fit a record-only robot. |
| AST-000208 | CT-IX2 | drone_pad | worn battery (SoH 71%) |
| AST-000209 | CT-IX2 | drone_pad | never calibrated |
| AST-000210 | FB-CX10 | pack_cell_1 | healthy |
| AST-000211 | FB-CX10 | pack_cell_2 | force-torque firmware drift (3.2.0) |
| AST-000212 | TS-H1 | returns_qc | healthy |
| AST-000213 | AC-TR50 | — | decommissioned; not placed |

Workers are the same 10. Their HR state decides whether they're on the floor:

| Worker | Where they work | State |
|---|---|---|
| Sam, Lee | pick_station_2 and intake_staging | on the floor |
| Noor | workshop | on the floor |
| Mateo | workshop; clears arm jams and verifies drone flights | on the floor |
| Ana, Kai | docks and outbound_staging | on the floor |
| Jordan | follows the humanoid | on the floor |
| Riley | outside the pack cells | on the floor. `robot_cell_access` is **revoked**, so Riley may not enter a fenced cell. |
| Sasha | — | `ON_LEAVE`, so bound but `OFF_DUTY` and not on the floor |
| Morgan | — | `TERMINATED`, so bound but `OFF_DUTY` and not on the floor |

### 4.2 Twin seed

`seeds/distribution_center.py`:
- **Robots:** creates one twin robot per non-decommissioned asset. The robot is named after the asset tag's model family, for example `TR50-101` or `PF1200-205`, bound to the asset, and placed at a free cell of its home zone. Arms are placed at their fixed cell. `robot_class` comes from the model's `embodiment_class`.
- **Operators:** creates one twin operator per worker, bound with `worker_id` and placed in a zone by role (§6).
- **Goods:**
  - 60 pallets in racks across levels 0–4 (150–900 kg);
  - 80 totes on shelves across levels 0–2 (5–25 kg, one SKU each);
  - 40 SKUs.
- **Agent:** Ada.

The seed is deterministic and has no demo tasks. The shift engine produces the work.

`seeds/classic.py` is today's `load_demo`, moved verbatim with its call sites unchanged.

### 4.3 Save, load, reset

- **`save_state`** writes version 2: v1 plus `layout`, `shift` (clock, RNG state, config, order table), `stock`, `equipment` (conveyor contents, jams), `people` (zones, transit) and `handoffs`.
- **`load_state`** accepts v1 (treated as `classic`) and v2. A save whose layout differs from the twin's is refused before anything changes, with a `ValueError` naming both floors; it is not rebuilt. The inventory is per floor (§2), so another floor's robots would have no assets to bind to. To load such a save, start the app on the floor it names.
- **`reset()`** keeps the current layout and reruns that layout's seed.

### 4.4 Remote check-ins removed

`FleetBridge.heartbeat` no longer calls `touch_remote_reports` when the inventory profile is `distribution_center`, because every active asset is bound. The call stays for `classic`, so A's tests are unchanged. The inventory method itself stays.

---

## 5. Embodiment and motion

### 5.1 Mobility profile

`MobilityProfile.from_model(spec, embodiment_class)` reads the catalog `spec`:

| Field | Source | Notes |
|---|---|---|
| `movement` | `spec.movement` | GROUND, AIR or FIXED |
| `clearance` | `spec.clearance` | WIDE or NARROW; None for air and fixed |
| `speed_cells_s` | `spec.max_speed_mps / CELL_SIZE_M` | Loaded forklifts and haulers use × `LOADED_SPEED_FACTOR` (0.7) |
| `max_payload_kg`, `max_shelf_level`, `box_kinds` | spec | |
| `max_lift_m`, `reach_mm` | spec | |
| `supervision` | spec | `humanoid_supervision` for TS-H1 |
| `battery` | `spec.battery`, or None for mains power | capacity_wh, runtime_h, charge_time_h |
| `lift_speed_mps` | **new catalog field**; forklift 0.3, others 0.5 | Added to catalog data, so the seed version bumps. |
| `takeoff_s`, `land_s`, `scan_s` | **new catalog fields** on CT-IX2: 4, 5, 3 | |
| `grasp_s`, `place_s` | **new catalog fields** on every model that handles boxes: NW-PF1200 and NW-HH300 3.0 / 3.0 (fork engage), AC-TR50 2.0 / 1.5, AC-PK30 and FB-CX10 2.5 / 1.5, TS-H1 3.0 / 2.0 | |

**Where the profile comes from.**
- On the new floor, a robot's profile comes from its bound asset's model and is rebuilt whenever an inventory change touches that model or asset.
- On `classic`, a robot keeps today's behaviour: ground and narrow, with its preset speed. So classic tests don't move.
- `Robot.speed` stays the stepping speed, set from `speed_cells_s` on the new floor.

**Robot classes.** `ROBOT_CLASS_PRESETS` gains `ARM` and `HUMANOID`, and every class's `allowed_task_types` gains its new job types (§9.2). `CHARGE_ROBOT` stays in every class, per the existing rule. For mains-powered arms it is never triggered.

### 5.2 Walkability per profile

`Warehouse.passable(cell, profile, layer)` decides which cells a robot may use:
- **Ground, wide clearance:** ground-walkable cells whose clearance is `WIDE`. That includes the two wide walkway crossings, (17,10) and (17,11).
- **Ground, narrow clearance:** every ground-walkable cell (WIDE or NARROW), plus all six crossings. `STATION` cells are passable only where the zone allows it: tote drop cells and `returns_qc`. Mobile robots never enter pack cells.
- **Air:** every interior cell except the no-fly zones (docks, pack cells, restricted). Walkway cells may be crossed but not hovered over (§5.3). Takeoff and landing happen only on `drone_pad` cells.
- **Fixed:** never moves.

**Routing uses the profile.** `NavigationEngine.find_path(start, goal, blocked=None, allow_goal_adjacent=True, is_replan=False, profile=None, layer="GROUND")` takes the new optional `profile` and `layer`. With no profile it behaves exactly as today. The profile is also threaded through `nearest_walkable`, `best_cell_in_zone`, `path_exists`, `distance`, `_break_deadlock` and spawn.

### 5.3 Layers, traffic and people at crossings

- **Layers.** Robots gain `layer` (`GROUND` or `AIR`) and `altitude_m`. Blocking, `other_robot_cells`, `_blocking_robot`, `_detect_collisions` and the system collision check all compare `(layer, x, y)`.
- **Drones.** A drone is on `AIR` from takeoff to landing, with `altitude_m` rising to the target level's height plus 0.5 m. It is on `GROUND` (on the pad) otherwise.
- **Crossings.** A ground robot at a crossing, or a drone about to cross a walkway cell, waits while any person is in transit on the walkway (§6). The wait reason is `PERSON_ON_CROSSING`.

### 5.4 Job steps with physical duration

New action kinds for the planner and simulator:

| Step | Duration |
|---|---|
| `LIFT_TO(level)` / `LOWER` | height difference ÷ `lift_speed_mps` |
| `TAKEOFF` / `LAND` | `takeoff_s` / `land_s` |
| `SCAN(face)` | `scan_s` per level |
| `GRASP` / `PLACE` | `grasp_s` / `place_s` |
| `PLACE_ON_CONVEYOR(cell)` | `place_s` |
| `WAIT_CLEAR(reason)` | until the condition clears |

Durations convert to ticks with `ceil(seconds / TICK_DT)`. The existing `PICK_TICKS` and `DELIVER_TICKS` stay for the existing job types.

### 5.5 Energy

**Ground robots.** Energy use in Wh per tick is:
- idle: `capacity_wh / (runtime_h × 3600) × 0.3 × TICK_DT`;
- moving: × 1.0;
- moving loaded: × 1.4.

Lift adds `m × g × Δh / 3600 / 0.6` Wh, where m is the load plus the carriage.

**Drones.** Hovering or flying uses `capacity_wh / (flight_time_min × 60) × TICK_DT` per tick.

**Scaling.** Everything is multiplied by `ENERGY_TIME_SCALE` (default 10), so charging behaviour shows up within a demo. Setting it to 1 is true to life. Battery % = energy remaining ÷ capacity.

**Charging.**
- Ground robots charge at `charging_station`, drones on `drone_pad`.
- The charging rate is `capacity_wh / (charge_time_h × 3600) × ENERGY_TIME_SCALE` Wh per second.
- An arm has no battery: `battery` is None, and the battery check, auto-charge and planner battery logic skip it.
- On `classic`, battery stays exactly as today (1% per cell and so on).

---

## 6. People (zone presence)

`Operator` gains:
- `zone` (the current zone key, or None when off the floor);
- `transit_until_tick` and `transit_to` (walking between zones);
- `certification_scopes`: `{code: {"equipment": [...], "site": [...]}}`, synced from inventory credentials alongside `certifications`.

**Movement.** A move from zone A to zone B takes `manhattan(centre A, centre B) × CELL_SIZE_M ÷ 1.2 m/s`. During the move the person is in neither zone. A move is "in transit on the walkway" only when it crosses the walkway: either end is the walkway zone itself, or the two zones' cells are not all on one side of the walkway strip. Sides are decided by the zones' cells, not their centres, so a walk to or from a zone that straddles the strip (`cross_aisle`, `top_aisle`) always counts as crossing it. Only such a move triggers crossing waits (§5.3). People are not grid entities and don't block robots, except through the rules below.

**Who drives movement.** The shift engine moves people (§11.3), and so do human jobs (§9.2). An `OFF_DUTY` operator has `zone = None`. On the new floor, shift status follows the simulated shift clock (§11.4).

**Rules robots obey.** Each rule is a physical wait, not a gate rejection:
- A forklift or heavy hauler won't enter a zone with a person in it (reason `PERSON_IN_AISLE`).
- An arm pauses while a person is in its pack-cell zone (`PERSON_IN_CELL`).
- The humanoid pauses unless its supervisor meets all of these (`SUPERVISOR_ABSENT`):
  - on shift;
  - holds a valid `humanoid_supervision` credential in scope for model TS-H1 and site WH-01;
  - in the same zone as the humanoid, or a zone adjacent to it (sharing an edge).

  The shift engine keeps Jordan following the humanoid unless an event takes Jordan away, such as a break. In step 2, the proximity test uses the humanoid's own UWB reading instead: `supervisor_range_m` ≤ `SUPERVISION_RANGE_M` (6 m).

---

## 7. Goods and stock

### 7.1 Boxes

`Box` gains:
- `kind` (`PALLET`, `TOTE`, `ITEM` or `CARTON`; default `TOTE` for classic);
- `sku`, `quantity`;
- `declared_weight_kg` (what the WMS says) and `true_weight_kg`. They are equal unless a fault makes them differ. The existing `weight` stays as an alias of `declared_weight_kg`.
- `slot` (rack or shelf cell plus level, or None) and `order_id`.

The rules for each kind:
- A `TOTE` holds `quantity` units of one SKU.
- `PICK_ITEMS` creates `ITEM` boxes on the conveyor, one per unit, and decrements the tote.
- `PACK_ORDER` consumes an order's items and creates one `CARTON` weighing the sum plus 0.3 kg.
- A `PALLET` holds `quantity` cases of one SKU.

### 7.2 Stock ledger

`StockLedger` maps each slot to a box id and each SKU to its locations. For every location it keeps both `recorded_qty` and `true_qty`. They are equal unless a fault or a mis-pick makes them differ.

Cycle counts write a reading and flag the variance with the event `STOCK_VARIANCE_DETECTED`. Recorded stock is only corrected by a count reconciliation, which the shift engine does automatically for variances of 2 units or fewer; larger variances go to the orders panel as exceptions.

Tote stock is sized so it doesn't run out during a demo shift. Decanting pallets into totes is out of scope. Returns put stock back.

---

## 8. Conveyor, sorter and hand-offs (E)

### 8.1 Conveyor

- **Shape.** One line: (19,15) → (24,15), flowing +x into the sorter.
- **Movement.** Items advance one cell every `ceil(CELL_SIZE_M / CONVEYOR_SPEED_MPS / TICK_DT)` ticks. The default `CONVEYOR_SPEED_MPS` is 0.5, which gives 20 ticks. A cell holds one item. An item that can't advance waits, so items back up.
- **Infeed.** Pick 1 and Pick 2 place items on (20,15).
- **Arms.** Each arm picks from and places onto its one working cell: (23,15) for arm 1 and (22,15) for arm 2. An arm takes only items tagged for its assigned order and ignores everything else passing, cartons included. When the order is packed, it places the carton on its working cell once that cell is empty, and the carton flows on through (24,15) into the sorter.
- **Jams.** A jammed segment stops advancing and raises `CONVEYOR_JAMMED`. It creates a `CLEAR_JAM` human job (§9.2), and the arms downstream of it pause.

### 8.2 Sorter

- Takes a carton from the last conveyor cell, (24,15), into the sorter after 2 s, then diverts it to `dock_4` or `dock_5` by the order's carrier lane.
- A carton sitting on a dock accumulates there. A scheduled outbound truck departure marks it `SHIPPED`.
- Fault `MIS_SORT_RISK` diverts a carton to the wrong dock.

### 8.3 Hand-off records

Every transfer of a box between two holders is a `Handoff`, recorded when it happens:
- robot to conveyor;
- conveyor to arm;
- arm to conveyor;
- conveyor to sorter;
- sorter to dock;
- robot to staging and back.

Each record holds: `handoff_id`, `box_id`, `order_id`, `from` and `to` holders, `cell`, `tick`, `giver_reported` (what the giver claims) and `receiver_observed` (what the receiver saw: present or absent, with its weight or count).

Faults:
- `HANDOFF_LOSS_RISK` drops the item at the hand-off. The giver still reports success, but the receiver observes it absent, and the box ends `FAILED` at that cell.
- `MIS_SORT_RISK` is described above.

Hand-offs are written to the task log as a `HANDOFF` event, which is what the evaluation checks read. In step 2 they are also written to the job log.

---

## 9. Jobs

### 9.1 Choosing a robot

`select_robot` first filters candidates by capability, then scores the survivors exactly as today (distance, battery, queue, busy). A candidate must pass every capability check:
- a movement type that can do the job;
- a route under its profile (`path_exists` with the profile);
- `max_payload_kg ≥ declared_weight_kg`;
- the box kind is in `box_kinds`;
- the slot level is at most `max_shelf_level`;
- the job type is in `allowed_task_types`;
- for the humanoid, supervision is satisfiable.

The existing eligibility checks (status, firmware) still apply. `_primary_target` stops assuming the first robot's position: the origin is the job's source or target.

### 9.2 Job types

New `TaskType` values:

| Type | Robot or person | Steps |
|---|---|---|
| `UNLOAD_TRUCK` | heavy hauler (≤ 300 kg) or forklift | NAVIGATE dock → PICK pallet → NAVIGATE intake_staging → DELIVER |
| `PUTAWAY_PALLET` | forklift | NAVIGATE staging → PICK → NAVIGATE face cell → LIFT_TO(level) → PLACE → LOWER |
| `RETRIEVE_PALLET` | forklift | NAVIGATE face → LIFT_TO → GRASP → LOWER → NAVIGATE outbound_staging → DELIVER |
| `LOAD_TRUCK` | forklift | NAVIGATE outbound_staging → PICK → NAVIGATE dock_3 → DELIVER (the pallet becomes `SHIPPED`) |
| `TOTE_TO_STATION` | AMR (level ≤ 1) or humanoid (level ≤ 2) | NAVIGATE face → LIFT_TO → GRASP → LOWER → NAVIGATE station tote drop → DELIVER |
| `RETURN_TOTE` | AMR or humanoid | the reverse, to the tote's slot |
| `PICK_ITEMS` | the robot picker at Pick 1 | GRASP × n → PLACE_ON_CONVEYOR × n |
| `PACK_ORDER` | arm | GRASP each order item from its working conveyor cell → PLACE into the carton → PLACE_ON_CONVEYOR (carton) |
| `CYCLE_COUNT` | drone | TAKEOFF → NAVIGATE (air) face → SCAN × levels → NAVIGATE pad → LAND |
| `PATROL` | scout | NAVIGATE along `patrol_loop` → report anomalies |
| `RETURNS_PUTAWAY` | humanoid | NAVIGATE returns_qc → GRASP tote → NAVIGATE face → LIFT_TO(level) → PLACE → LOWER |
| `MANUAL_PICK` | person (Pick 2) | human job: walk to pick_station_2, pick n items onto the conveyor |
| `CLEAR_JAM` | person | human job: walk to the jammed segment's zone, fix it (30 s). Requires `robot_cell_access` scoped to FB-CX10 when the jam is inside a pack cell. |

**Human job certifications.** `CERTIFICATION_REQUIREMENTS` gains `CLEAR_JAM: robot_cell_access`. Human job checks also compare `certification_scopes` against the robot or equipment model involved, and that scope rule applies to every certification check on the new floor.

**Existing types.** Every existing job type keeps its behaviour on both floors.

**Agent and UI integration.** The agent tool guide (`TASK_TYPE_GUIDE`), `options().task_types` and the frontend's `TASK_FIELDS` gain entries for every new type.

### 9.3 New events

New `EventType` values:
- **Robot steps:** `ROBOT_STEP`, emitted at every step start with `{step, zone, people_present, supervision_ok, level, true_weight_kg, declared_weight_kg}`; `LIFTED`, `LOWERED`, `SCANNED` and `PLACED`.
- **Safety:** `ROBOT_SAFETY_WAIT`, `ROBOT_SAFETY_RESUMED`, `SAFETY_WAIT_ESCALATED`.
- **Equipment and stock:** `HANDOFF`, `CONVEYOR_JAMMED`, `CONVEYOR_CLEARED`, `STOCK_VARIANCE_DETECTED`.
- **Orders and shift:** `ORDER_CREATED`, `ORDER_STAGE_ADVANCED`, `ORDER_COMPLETED`, `ORDER_FAILED`, `SHIFT_STARTED`, `SHIFT_PAUSED`.
- **People:** `PERSON_MOVED`.
- **Step 2:** `ROBOT_DECISION`.

They log under new `LogCategory` values: `OPERATIONS` (orders, shift, stock, equipment), `SAFETY` and `TELEMETRY` (step 2).

**Status.** Robots gain `activity` (the current step's name) and `wait_reason` (None or a reason code). `RobotStatus` values don't change: safety waits use `WAITING`, and steps map onto the existing statuses.

---

## 10. Trust layer

### 10.1 New shared rules (`eligibility.py`)

Each rule is a function of primitive arguments that returns `(ok, reason)`. The gate, robot selection, the mid-task re-check and the evaluation all call the same functions.

| Function | Rule |
|---|---|
| `payload_ok(weight_kg, max_payload_kg)` | weight ≤ max |
| `box_kind_ok(kind, box_kinds)` | kind is in the supported set |
| `reach_ok(level, max_shelf_level)` | level ≤ max |
| `clearance_ok(route_cells_clearance, robot_clearance)` | wide robots only on WIDE cells or wide crossings |
| `no_fly_ok(route_cells_no_fly)` | an air route has no no-fly cell |
| `drone_round_trip_ok(battery_wh, est_wh, capacity_wh)` | battery ≥ estimate + 25% of capacity. A **hard** gate. |
| `supervision_ok(required, supervisor_on_shift, credential_valid, in_scope)` | all must hold (proximity is a runtime wait, §6) |
| `cert_scope_ok(scopes, code, model_code, site)` | the credential's equipment and site scope covers the job |

**The battery exception** is preserved: a critical battery on a ground robot still gives a charging detour, not a rejection. Only drones get a hard energy gate.

### 10.2 Where rules run

- **Job creation:** `TaskManager.validate()`, for explicitly named robots and entities.
- **Robot choice:** `select_robot`, as the capability filter.
- **During a job:** `Simulator._check_authorization_changes`, which keeps flagging and never cancelling. It gains payload, reach and supervision re-checks against current state.

### 10.3 Physical waits are decisions, not rejections

Waits such as `PERSON_IN_AISLE`, `PERSON_IN_CELL`, `SUPERVISOR_ABSENT`, `PERSON_ON_CROSSING` and `CONVEYOR_JAMMED` are handled like this:
- they set `WAITING` with a `wait_reason`;
- they emit `ROBOT_SAFETY_WAIT` and `ROBOT_SAFETY_RESUMED`;
- they are logged to the task log. In step 2 they are also logged as decisions.

A wait longer than `SAFETY_WAIT_ESCALATE_S` (120 s) emits `SAFETY_WAIT_ESCALATED`, and the orders panel shows it.

### 10.4 New evaluation checks

These are added to `DEFAULT_CHECKS`. Each returns "not applicable" (pass, with `applicable: false`) when a log carries none of its data. Existing fixtures therefore evaluate exactly as today, and tests that assert the exact check list are updated to include the new names.

| Check | Fails when |
|---|---|
| `payload_within_limit` | a `BOX_PICKED` or `LIFTED` event's true weight is above the robot's max payload |
| `reach_within_limit` | a `LIFTED`, `SCANNED` or `PLACED` level is above the robot's max |
| `clearance_respected` | a wide robot's path includes a NARROW cell |
| `no_fly_respected` | a drone path includes a no-fly cell |
| `human_zone_clear` | a forklift or hauler entered, or an arm moved, while a person was present (read from `ROBOT_STEP` events carrying `people_present`) |
| `supervision_maintained` | a humanoid step ran while supervision was not satisfied |
| `count_consistent` | a `CYCLE_COUNT` reported success but the reported count ≠ the true count (catches false success) |
| `handoff_consistent` | a `HANDOFF` with `giver_reported` present and `receiver_observed` absent |
| `sort_correct` | a carton arrived at a dock other than its order's lane |
| `placement_level_correct` | a `PLACED` event's `true_level` differs from its requested `level` (catches `WRONG_LEVEL_RISK`) |

The task log's `TASK_STARTED` and terminal snapshots gain `layer`, `altitude_m`, box `kind`, `slot` and the true and declared weights, so these checks have their data. New fixtures go under `logs/eval_examples/`, one pass and one fail case per new check.

### 10.5 System checks (`ci_engine.py`)

- **Required zones:** `_check_environment` requires the loaded layout's `required_zones` instead of four hard-coded keys. Connectivity is checked per profile class: ground-narrow, ground-wide and air.
- **Robot positions:** `_check_robot_positions` accepts a drone on `AIR` over any flyable cell and an arm on its fixed station cell.
- **Battery:** `_check_battery` skips mains-powered robots.
- **Collisions:** `_check_collisions` compares `(layer, x, y)`.

### 10.6 Injected faults

New `CONFIG` risks, all 0.0 by default and settable through the policy file like `FALSE_SUCCESS_RISK`:

| Risk | Effect |
|---|---|
| `SCAN_MISCOUNT_RISK` | the drone reports true ± 1–3 |
| `WRONG_LEVEL_RISK` | the forklift places at level ± 1 but reports the requested level |
| `GRASP_FAIL_RISK` | the arm or picker misses; retried up to 2 times, then the job fails |
| `CONVEYOR_JAM_RISK` | chance per item-advance of jamming a segment |
| `HANDOFF_LOSS_RISK` | see §8.3 |
| `MIS_SORT_RISK` | see §8.2 |
| `MISDECLARED_WEIGHT_RISK` | an inbound pallet's `true_weight_kg` is 1.1–1.6 × declared |

`POST /api/faults/<kind>` injects a single occurrence on demand, for demos.

**Not in this spec:** the gate consulting PWA passports or calibration (F). Calibration state affects step 2's sensors (§13.2).

---

## 11. Shift engine and orders

### 11.1 Generators

`ShiftEngine` is seeded by `shift.seed` (default 42) with its own `random.Random`. Default rates per simulated hour, all multiplied by `pace` (default 1.0):

| Stream | Default |
|---|---|
| Inbound trucks | every 20 sim-min, alternating dock_1 and dock_2, 4–8 pallets each |
| Customer orders | 30 per hour, 1–4 lines × 1–3 units, lane `dock_4` or `dock_5` |
| Full-pallet orders | 4 per hour |
| Cycle counts | one rack face every 10 sim-min, round-robin over the 36 pallet rack faces |
| Patrols | every 15 sim-min |
| Returns | 6 per hour (a tote appears in returns_qc) |
| Outbound truck departures | dock_3, dock_4 and dock_5 each every 30 sim-min |

The engine calls `twin.tasks.create_task(payload, internal=True)`. It never bypasses the gate, and a rejected job is recorded against its order with the gate's reason.

### 11.2 Order chains

`Order` has `order_id`, `kind`, `lines`, `status` (`OPEN`, `IN_PROGRESS`, `DONE`, `FAILED`), `stages`, `current_stage` and `failure_reason`. Each kind is a fixed chain:

| Order kind | Chain |
|---|---|
| Customer order | per line: TOTE_TO_STATION → PICK_ITEMS or MANUAL_PICK → RETURN_TOTE; then conveyor → PACK_ORDER → sorter → dock |
| Inbound truck | UNLOAD_TRUCK × pallets → PUTAWAY_PALLET each (slot = the nearest free slot in reach of an available forklift) |
| Pallet order | RETRIEVE_PALLET → LOAD_TRUCK |
| Cycle count | CYCLE_COUNT |
| Return | RETURNS_PUTAWAY |

**How stages advance.** A stage creates its job when the previous stage completes, on `TASK_COMPLETED` for the job ids it tracks. Conveyor stages advance on hand-off events.

**Failures.** A failed or rejected stage is retried once. After that the order is `FAILED` with the task's failure or rejection reason.

**Picking.** Orders go to Pick 2 (a person) when Sam or Lee is on shift and Pick 1 has more queued lines. Otherwise they go to Pick 1.

### 11.3 People's activities

The engine moves people on shift:
- Sam and Lee go to Pick 2 when it has work, otherwise to intake.
- Ana and Kai go to the dock with a truck.
- Jordan follows the humanoid.
- Noor and Mateo go to the workshop, or to a jam or work order.
- Riley stays outside the pack cells.
- Everyone takes a 15-minute break every 2 sim-hours, at staggered times, in `sw_floor`. Breaks begin after the first two sim-hours of a shift.
- A person in a zone that a waiting forklift or hauler needs to enter steps aside to the nearest zone those bodies can't drive in, for 30 s, so unloading never deadlocks. Jordan's break makes the humanoid pause (`SUPERVISOR_ABSENT`). This is intended.

`CLEAR_JAM` goes to the nearest on-shift person with an in-scope `robot_cell_access`. Riley's credential is revoked, so the gate rejects Riley and the job goes to Mateo. If no one qualified is on shift, the gate rejects the job, the order records the reason, and the job is retried when someone qualifies.

### 11.4 Shift clock

- **New floor:** `shift_clock = SHIFT_START_HOUR (6) + simulation_time`, as HH:MM, and wraps every 24 h. Operator on-duty and off-duty status and HR state (`ON_LEAVE`, `TERMINATED` mean off duty) follow the shift clock.
- **Classic:** keeps today's wall-clock behaviour.

### 11.5 Controls and API

- `GET /api/shift` returns status, clock, config, counters and throughput per hour.
- `POST /api/shift/start`, `/pause` and `/config` (`pace`, `seed`, rates) control it. Changing the seed resets the engine's RNG.
- `GET /api/orders?status=&kind=&limit=` lists orders.
- `GET /api/stock?sku=`, `GET /api/people` and `GET /api/equipment` return stock, people and equipment.
- The shift starts paused. `sim start` runs the simulator, and the shift must be started separately.

---

## 12. Dashboard (step 1)

- **Floor.** Drawn from layout data. `cellFill`, `COLORS` and `LEGEND` come from a `cell_types` table in the snapshot (label and colour role), not from hard-coded constants. The classic rendering looks the same.
- **Robots.** Each robot type has a glyph and a letter:
  - A: AMR
  - F: forklift
  - H: hauler
  - K: picker
  - S: scout
  - D: drone
  - R: arm
  - U: humanoid

  A carried box shows a kind badge. The robot's `wait_reason` shows as an amber ring.
- **Air layer.** Drones draw above everything. An "Air layer" toggle shows or hides drones and the no-fly overlay.
- **Arms.** An arm shows its station and its reachable cells, and turns amber while paused for a person.
- **People.** Small dots with initials in their zone. People in transit are drawn on the walkway.
- **Conveyor.** Items animate along it, and a jammed segment is red.
- **Robot panel.** Clicking a robot opens a panel with:
  - model, asset and limits (payload, reach, clearance);
  - current job, step and wait reason;
  - a link to the inventory record.

  Step 2 adds live sensors, decisions and replay.
- **Shift panel.** Clock, start and pause, pace, orders in flight, throughput, backlog, failed orders with reasons, and safety escalations.
- **Fleet page.** `fleet.html` shows an "On the floor" link per robot that selects it on the dashboard.
- **Phone width.** The floor scales to fit and the panels stack, with no horizontal page scroll.

---

## 13. Step 2 — Sensors, decisions, job logs (D)

### 13.1 Sensor suites from the component tree

A robot's suite is built from its bound asset's installed components: slot, part number, serial, firmware and latest calibration. Each `component_type` maps to a sensor model:

| component_type | Readings (SI units) | Base σ (noise) |
|---|---|---|
| `BATTERY` | soc_pct, voltage_v, current_a, temp_c | 0.2 %, 0.05 V, 0.3 A, 0.3 °C |
| `DRIVE_UNIT` (odometry) | x_m, y_m, heading_deg, speed_mps, motor_temp_c | 0.01 m per m travelled (accumulating), 0.5° |
| `IMU` | accel_mps2[3], gyro_dps[3] | 0.02, 0.1 |
| `LIDAR` | nearest_m, sector_min_m[8] (ray-cast against walls, racks, robots on the same layer, people) | 0.02 m |
| `SAFETY_SCANNER` | protective_field_breached, warning_field_breached, nearest_m | 0.03 m |
| `RGBD_CAMERA` | detection (e.g. pallet pocket, tote, person), confidence | 0.03 confidence |
| `THERMAL_CAMERA` | max_temp_c, hotspot_cell | 0.5 °C |
| `LOAD_CELL` | load_kg | 0.5 % of reading |
| `MAST_ENCODER` | fork_height_m, tilt_deg | 0.001 m, 0.1° |
| `SCANNER` | tags_read, count, read_confidence | count exact unless faulted; confidence 0.02 |
| `FORCE_TORQUE` | force_n[3], torque_nm[3] | 0.5 N, 0.02 N·m |
| `LIGHT_CURTAIN` | beam_broken | — |
| `GRIPPER` | vacuum_kpa or grip_force_n, object_present | 1 kPa, 0.5 N |
| `ACTUATOR` | joint_pos_deg[n], joint_torque_nm[n] (humanoid adds balance_margin_m, foot_pressure_n[L,R]) | 0.05°, 0.2 N·m |
| `ROTOR` | rpm[4] | 20 rpm |
| `COMPUTE` | cpu_pct, temp_c; the drone's `flight_controller` slot (part CT-FC3) adds baro_alt_m, range_down_m, vio_x_m, vio_y_m | 1 %, 0.5 °C, 0.1 m, 0.02 m, 0.05 m |

The humanoid's supervisor distance comes from a UWB tag reading (`supervisor_range_m`, σ 0.1 m). It is modelled on the `COMPUTE` slot, because the catalog has no UWB part.

### 13.2 Imperfection model

`reading = truth + bias + noise + drift`, and each part comes from the robot's state:
- **Noise** is `N(0, σ × noise_factor)`, from a per-robot seeded RNG.
- **Bias** grows with calibration age: `bias = σ × k × (days since calibration ÷ interval)`, where k is fixed per sensor. By calibration status:
  - `VALID`: k is 0.5 or less;
  - `DUE_SOON`: about 1;
  - `EXPIRED`: 3 × age ratio;
  - `MISSING` or `FAILED`: 5, with doubled noise.
- **Battery wear:**
  - voltage sags under load by `(1 − SoH) × 0.5 V` per 10 A;
  - effective capacity is `capacity × SoH`, so drain is faster.

  SoH comes from the asset's last reported battery.
- **Firmware:**
  - a component or robot release with a known-issue flag applies its effect: `AC-TR50` 2.2.1 gives odometry heading drift of 0.5° per metre;
  - `FB-CX10` force-torque firmware 3.2.0 gives an 8 N z-offset.

  Known issues are a new `known_issues` list on the release rows (a catalog data change).
- **Dropouts:** a sample is missing with probability `DROPOUT_RATE` (default 0.002 per sensor per sample), recorded with `quality: "DROPOUT"`.
- **Fault modes**, triggered by the `SENSOR_FAULT_RISK` config or `POST /api/faults/sensor`: `STUCK` (the last value repeats), `SPIKE` (one value ×10) and `OFFSET` (a step bias). The sample is marked `quality: "FAULT"` only if the robot's own self-check detects it: `STUCK` is detected after 20 identical samples, and `OFFSET` is never detected.

### 13.3 Robots decide from readings

On the new floor, robot behaviour reads its sensors, not the truth, at these decision points:
- **Safety stop:** the safety scanner's protective field is breached.
- **Forklift pallet weight:** the load cell's `load_kg` is compared with the payload limit when lifting. An under-reading drifted load cell can lift an overweight pallet, and `payload_within_limit` catches it.
- **Grasp success:** the gripper's `vacuum_kpa` must be at least 55, and `object_present` must be true.
- **Drone count:** the drone reports the scanner's count.
- **Humanoid supervision:** `supervisor_range_m` ≤ `SUPERVISION_RANGE_M` (6 m).
- **Recharge:** triggered on `soc_pct`.

Routing and traffic stay on truth, because the fleet manager knows positions. Classic robots still decide on truth, so classic behaviour is unchanged.

### 13.4 Decision records

`decide(robot, kind, inputs, options, chosen, reason)` appends a decision and emits `ROBOT_DECISION`. The decision kinds are:

`ROUTE_SELECTED`, `REPLAN`, `YIELD`, `SAFETY_STOP`, `SAFETY_RESUME`, `WAIT_FOR_CLEARANCE`, `GRASP_RETRY`, `LIFT_ACCEPT` / `LIFT_REJECT`, `RECHARGE_DETOUR`, `COUNT_REPORTED`, `ABORT`, `SPEED_LIMITED`

The inputs are the readings used, with sensor slot and quality. For example:

`{kind: "GRASP_RETRY", inputs: {"gripper.vacuum_kpa": 38.2}, options: ["RETRY", "ABORT"], chosen: "RETRY", reason: "vacuum 38.2 kPa < 55 kPa threshold"}`

### 13.5 Job logs

Run layout:

```
logs/runs/<run_id>/
  run.json                  # run_id, started_at, layout, seed, config snapshot, git sha if available
  jobs/<task_id>/
    summary.json            # job, robot, asset, model, release, components (slot, part, serial, fw, calibration status),
                            # started/ended, result, decisions count, safety waits, handoffs, claims vs truth
    telemetry.jsonl         # one line per robot per sample: {t, seq, robot_id, asset_id, step, readings:{slot:{type, part, serial, fw, values, units, quality}}}
    truth.jsonl             # one line per sample: {t, seq, pose, layer, altitude_m, carried, load_kg_true, fork_height_true, battery_wh, people_nearby}
    decisions.jsonl         # one line per decision
```

- **Run id.** `run_id` = UTC timestamp plus 4 random hex characters, created on twin start and on `reset()`. Every log record gains a `run_id` field. The change is additive: the `logs/tasks/<id>.json` layout the evaluation reads is otherwise unchanged, and the plan verifies that the evaluation ignores the extra field. This fixes cross-run mixing in the new job logs.
- **Sampling.** Telemetry and truth are sampled at `TELEMETRY_HZ` (default 2 Hz) while a job runs, plus event-triggered samples at every step boundary and decision. Idle robots only feed the in-memory ring buffer.
- **Retention.** Only the newest `TELEMETRY_KEEP_RUNS` (default 5) runs are kept, pruned at run start. `logs/runs/` is git-ignored.
- **Writing.** All files are append-only JSONL, one writer thread per twin, fed through a bounded queue (10 000 items). If the queue is full, samples are dropped and counted in `run.json`. Decisions and summaries are never dropped.

### 13.6 Live view and replay

**Ring buffer.** The newest 60 s of samples and decisions per robot are kept in memory, for idle robots too.

Endpoints:
- `GET /api/robots/<id>/telemetry` returns the ring buffer.
- `GET /api/robots/<id>/telemetry/stream` is a dedicated SSE stream at `TELEMETRY_HZ` with `sample` and `decision` events. The main `/api/stream` state frames don't change.
- `GET /api/runs` and `GET /api/runs/<run_id>/jobs?limit=` list runs and jobs.
- `GET /api/runs/<run_id>/jobs/<task_id>` returns the summary, and `.../<file>` returns one of the three JSONL files, capped at 50 000 lines per response with a `next` cursor.
- `GET /api/tasks/<task_id>/replay` resolves the current run.

**Robot panel.**
- One live card per sensor, with value, unit, quality chip and a 60 s sparkline.
- A decision feed.
- Calibration and firmware chips from the inventory.

**Replay view.**
- A timeline slider over a finished job.
- A mini floor with the path so far.
- Synced sensor charts, with the true value shown as a faint line where it differs.
- The decisions in order.
- The job's hand-offs.
- Keyboard: arrow keys step through the samples.

---

## 14. API summary

**Step 1 (new):**
- `/api/shift` (GET), `/api/shift/start`, `/pause`, `/config` (POST)
- `/api/orders` (GET), `/api/orders/<id>` (GET)
- `/api/stock` (GET)
- `/api/people` (GET)
- `/api/equipment` (GET)
- `/api/faults/<kind>` (POST)

**Step 1 (changed):**
- `/api/state` adds `layout_name`, `cell_types`, robot `layer`, `altitude_m`, `activity`, `wait_reason`, operator `zone`, conveyor items and box kinds.
- `POST /api/tasks` accepts the new job types.

**Step 2 (new):** the telemetry, runs and replay endpoints (§13.6).

**Error mapping:** 404 unknown id, 409 state conflict, 400 validation, which is the existing `ApiError` pattern.

---

## 15. Compatibility and migration

- **Tests.** `DigitalTwin()` and every existing fixture stay on `classic`, with an unchanged grid, seed, battery model, wall-clock shifts and task behaviour. A's inventory tests use the `classic` seed profile unchanged.
- **Classic layout guard.** A golden test pins the classic grid and zones, cell by cell, before the code is moved.
- **Policies.** `policies.yaml` `robot_classes` still replaces presets wholesale. The README warns that a custom block must include `ARM` and `HUMANOID` for the new floor.
- **Tracked log files.** `logs/tasks/task_00*.json` are git-tracked and get rewritten by app runs, which is a known annoyance. This spec doesn't change that. `logs/runs/` is new and git-ignored.
- **Documentation.** README gets new sections: the floor, the robot types, the shift, faults, and telemetry and replay. `TRUST_LAYER.md` is reconciled: it lists the new rules, and its stale Tier 2 list is corrected.

---

## 16. Testing

**Unit tests, step 1:**
- the layout builds as §3 specifies (zones, attributes, slots, crossings);
- the classic golden test;
- each profile's walkability: a forklift never plans through a NARROW cell; a drone plans over racks but not through no-fly cells; an arm never moves;
- traffic is layer-aware: a drone over an AMR is not a collision;
- people waits: forklift, arm and humanoid;
- every new eligibility rule;
- the planner's steps per new job type;
- capability-filtered robot selection;
- conveyor flow, backing up and jams;
- sorter lanes;
- hand-off records and loss;
- the stock ledger and variances;
- order chains, including retry and failure reasons;
- shift engine determinism: the same seed and ticks give the same orders and jobs;
- shift-clock duty;
- save and load v2, and v1 compatibility;
- every new evaluation check, with pass and fail fixtures;
- layout-driven system checks.

**Unit tests, step 2:**
- sensor noise stays within 4σ over 1 000 samples;
- bias ordering follows calibration status (VALID < DUE_SOON < EXPIRED < MISSING);
- battery sag scales with SoH;
- the known-issue firmware effects apply;
- dropout rates fall within bounds;
- faults, and their detection rule;
- decisions use readings: a drifted load cell accepts an overweight pallet, and `payload_within_limit` flags it;
- the recorder writes append-only files with run ids, respects retention, and counts queue drops;
- the SSE telemetry endpoint;
- replay endpoint paging.

**Soak test.** Run `distribution_center` with seed 42 and `pace` 2 for 20 000 ticks, with faults off. Assert:
- zero same-layer collisions;
- zero rule-check failures across all new checks;
- at least one completed order of every kind;
- no robot in `ERROR`;
- no safety escalations (the 50-sim-minute soak ends before the first scheduled break; a longer run would see the intended `SUPERVISOR_ABSENT` escalations during Jordan's break);
- mean tick time within the §1 budget.

Then rerun with each risk at 0.2 and assert that the matching evaluation check catches it.

**API tests** cover every new endpoint: happy path and error mapping.

**Browser check,** at desktop width and phone width (375 px):
1. Start the shift and watch every robot type act.
2. Toggle the air layer.
3. Watch a person walk into a pack cell and see the arm pause.
4. Watch the humanoid pause on Jordan's break.
5. Jam the conveyor and see `CLEAR_JAM` go to Mateo, not Riley.
6. Open a robot's live sensors.
7. Replay a finished job.

**Suite command:** `.venv/bin/python -m pytest -o addopts="" -q`. The only failures allowed are the two pre-existing ones.

---

## 17. Build order

0. **A's residual regression**, on `feature/fleet-inventory`:
   - Problem: `rebind_all` must remember the failed asset id, only take a spare whose model's class matches, and fall back to commissioning on `ValueError`.
   - Add a regression test for the FORKLIFT/AMR asset-id collision.
   - Then the user chooses how A lands (merge, PR or keep), and C/E starts on `feature/multi-embodiment-ops`, cut from there.
1. **Step 1 plan** (C + E), in dependency order:
   1. classic golden test and the layout registry move;
   2. distribution-centre layout;
   3. inventory seed profile and site merge;
   4. mobility profiles and per-profile navigation;
   5. layers and traffic;
   6. people;
   7. goods and stock ledger;
   8. job steps and planner;
   9. job types and selection;
   10. eligibility rules and gate;
   11. conveyor, sorter and hand-offs;
   12. shift engine and order chains;
   13. evaluation and system checks;
   14. new-floor seed, save and load, app boot;
   15. operations API;
   16. dashboard;
   17. soak test and docs.
2. **Step 2 plan** (D):
   1. sensor models and imperfections;
   2. suites from the component tree and known issues;
   3. reading-driven decisions;
   4. recorder, run ids and retention;
   5. telemetry and replay API;
   6. live panel and replay UI;
   7. step-2 soak, fault matrix and docs.

Each plan is executed with subagent-driven development, with a per-task review and a final whole-branch review.

---

## 18. Out of scope

| Item | Where it goes |
|---|---|
| Twin → PWA ingestion of the inventory feed and job logs; passport re-mint | **B** (next) |
| The twin's gate consults PWA passports and calibration before a job | **F** (after B) |
| Real 3D, continuous positions, robot footprints wider than a cell (clearance classes stand in) | none planned |
| Conveyors and sorters as inventory assets | later |
| Decanting pallets into totes; replenishment planning | later |
| Multi-site simulation | removed by decision 5 |

## 19. Rulings made while writing this spec

These are choices I made that weren't asked about in the Q&A. Each can be changed at spec review.

1. **Walkway and drones.** Design section 2, as presented in chat, said drones never fly over the walkway. Here, drones may **cross** the walkway but never hover over it, and they wait while a person is in transit. The drone pad sits east of the walkway and every rack is west of it, so a strict no-fly walkway would ground every drone.
2. **Inventory seed profiles** (`classic` and `distribution_center`), rather than rewriting A's seed. This keeps A's tests and scenarios unchanged, and the app's file gets the new profile.
3. **AST-000207 comes online** on the floor. A record-only OFFLINE scenario has no physical meaning once every robot is simulated.
4. **Workers on the floor.** 8 of the 10 workers are on the floor. Sasha (on leave) and Morgan (terminated) are bound, but their HR state keeps them off duty. This follows the inventory's status, where the design said all 10 are assigned zones.
5. **Pick 2 is a human pick station.** There's only one piece-picking robot, and a mixed human/robot pick area is realistic.
6. **`ENERGY_TIME_SCALE` defaults to 10**, so charging appears within a demo. Set it to 1 for true-to-life runtimes.
7. **`CARTON` joins the box kinds,** because packing produces it.
8. **Catalog data additions:** lift speed, drone and arm step timings, and release `known_issues`. They bump the seed version, which reseeds the file with a `.bak` copy.
9. **Routing and traffic use truth** (the fleet manager's view). Only robot-side decisions use noisy readings.
10. **The shift starts paused** even when the simulator runs, so a user or test decides when work begins.
