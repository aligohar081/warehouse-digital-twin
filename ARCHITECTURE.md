# Architecture

Last verified against commit 67af707 (the code is unchanged through 3e99df8, which only adds plan 1c). Where code and docs disagree, the code wins. Keep this file true: a change that adds, moves or removes a unit, a route or a step of the tick updates it in the same commit.

## 1. What this is

A Python 3.14 / Flask simulator of a warehouse, with a plain-JavaScript dashboard. One `DigitalTwin` object holds all state; a `Simulator` advances it in fixed ticks (0.15 simulated seconds each). There are two floors, picked by a layout name: `classic` (20x15, two AMR-style robots, five boxes, the default everywhere) and `distribution_center` (32x20: docks, pallet racks, tote shelves, a pedestrian walkway, pick and pack stations, a conveyor and sorter). Robots come in eight embodiments (AMR, FORKLIFT, SCOUT, HEAVY_HAULER, DRONE, PICKER, ARM, HUMANOID) whose bodies come from an SQLite fleet catalog. People (operators) exist as zone presence, not grid entities. Goods are pallets, totes, items and cartons tracked by a stock ledger. Every task goes through a pre-execution gate (eligibility of robot, AI agent and human operator) and is graded after the fact by an eval engine; injected faults and a 12-check "mock CI" test the trust layer. A separate inventory package plays the fleet-manager and workforce source systems, bound to live robots and operators by `FleetBridge`.

## 2. Repo map

| Path | Responsibility |
|---|---|
| `backend/` | The whole Python package. Run as `python -m backend.app`. |
| `frontend/` | Static dashboard served by Flask: `index.html` + `app.js` + `style.css`; `fleet.html` + `fleet.js` + `fleet.css`. |
| `evals/` | promptfoo suites, test generators, `shared/providers` and `shared/asserts`. See `evals/README.md`. |
| `docs/superpowers/specs/`, `docs/superpowers/plans/` | Design specs and implementation plans (history; see section 10). |
| `docs/handoffs/` | Session hand-off notes for the multi-embodiment work. |
| `logs/`, `data/` | Runtime output (section 6). `logs/eval_examples/` holds tracked eval fixtures; `logs/tasks/` holds six tracked sample task logs. |
| `policies.example.yaml` | Copy to `policies.yaml` to override `CONFIG` and the approved baselines (`policies.yaml` itself is absent at HEAD). |
| `README.md`, `RUN.md`, `TRUST_LAYER.md` | User docs. README and TRUST_LAYER predate the multi-embodiment work. |
| `run.sh`, `requirements.txt`, `pytest.ini` | Launcher, deps (Flask, pytest, PyYAML), test config. |

### Backend modules (`backend/`)

| Group | Module | Responsibility |
|---|---|---|
| Root | `digital_twin.py` | `DigitalTwin`: owns everything below; add/find/reset/snapshot/save/load; `lock`. |
| | `simulator.py` | `Simulator`: the tick, per-robot controller, traffic, job steps, energy, charging, parking, SSE publish. |
| | `app.py` | Flask app factory `create_app`, `build_twin`, all routes except inventory. |
| Floor | `layouts/` | `LAYOUTS` registry, `build_layout`; `base.py` (`Layout`, `Zone`, `Slot`, `rect`, WIDE/NARROW), `classic.py`, `distribution_center.py`. |
| | `warehouse.py` | `Warehouse(layout=name)`: grid, zones and aliases, slots, `passable(cell, profile, layer)`, clearance, no-fly, `to_dict`. |
| | `navigation.py` | A* over the grid; profile- and layer-aware. |
| | `embodiment.py` | `MobilityProfile` (built from a catalog model spec), GROUND/AIR layers, seconds-to-ticks helpers. |
| | `energy.py` | Watt-hour battery model for bodies with a profile (classic robots keep the old percent model). |
| Entities | `robot.py`, `operator.py`, `agent.py` | The three actor classes. |
| | `box.py`, `goods.py` | `Box` (kinds PALLET/TOTE/ITEM/CARTON, declared vs true weight); `StockLedger` (recorded vs true quantity per slot) and helpers. |
| | `equipment.py` | `Equipment`: `Conveyor`, `Sorter`, hand-off records. `None` on classic. |
| | `people.py` | Who is in which zone, walks between zones, walkway and supervision queries. |
| Tasks | `task_manager.py` | `Task`, `TaskManager`: create, gate (`validate`), queue, robot selection, assign, lifecycle. |
| | `task_planner.py` | `TaskPlanner`: task to `Action` list, target resolution, battery estimate and recharge detour. |
| | `jobs.py` | `JobSpec` and `JOB_SPECS`: the 13 floor job types (check, plan, target). |
| | `human_jobs.py` | `MANUAL_PICK` and `CLEAR_JAM`: jobs a person does; also `ensure_jam_jobs`. |
| | `operations/` | `shift.py` (`ShiftEngine`, work generators), `orders.py` (`OrderBook`, order chains), `activities.py` (people's movement, duty). |
| | `scheduler.py`, `maintenance.py` | Recurring tasks; wear-based maintenance alerts. |
| Trust | `eligibility.py` | The one shared rule set (gate and grade both call it). |
| | `faults.py` | `FaultInjector`: armed one-shots plus CONFIG risks. |
| | `eval_engine.py`, `run_evals.py`, `decision_graph.py` | Grade a task's log (20 checks); CLI; search graded history. |
| | `ci_engine.py` | `CIEngine`: 12 live-twin consistency checks. |
| | `policy.py` | Loads `policies.yaml` and mutates `models` containers in place. |
| Events | `event_system.py`, `logger.py` | `EventSystem` + SSE `Broadcaster`; `WarehouseLogger` (ring buffer, files, per-task JSON). |
| Fleet | `inventory/` | SQLite fleet-manager and workforce source systems (see section 6). Imports nothing else from `backend`. |
| | `fleet_bridge.py`, `inventory_api.py` | Binds robots and operators to records and applies OTA/lifecycle effects; `/api/fleet/*` and `/api/workforce/*` routes. |
| AI chat | `agent_chat.py`, `agent_tools.py`, `llm.py` | Groq tool-use chat agent (7 tools, always needs a Groq key) and optional one-shot narration (`AGENT_LLM_ENABLED`, off by default). |
| Misc | `models.py` | Enums (`TaskType` has 29 values), `CONFIG`, class and role presets, approved baselines, `Action`, `IdFactory`. Calls `policy.load_policies()` at import. |
| | `seeds/distribution_center.py` | Stopgap seed for the new floor (12 robots, 6 totes, 3 pallets, 10 operators). |

### Module dependencies (arrows are imports; some are deferred to break cycles)

```
 app.py ─┬─► simulator.py ─► eligibility, energy, goods, embodiment, maintenance,
         │                   people, human_jobs, operations.activities
         │                   (drives the twin through `twin.*`; it does not import digital_twin)
         ├─► digital_twin.py ─► task_manager ─► task_planner ◄─► jobs (JOB_SPECS)
         │        │                  └────────► eligibility, human_jobs, people, energy, llm
         │        ├─► warehouse ─► layouts           navigation ─► warehouse
         │        ├─► robot, box, operator, agent, goods, equipment, faults, scheduler
         │        ├─► operations (ShiftEngine, OrderBook, activities) ─► jobs, people
         │        ├─► event_system, logger
         │        └─► fleet_bridge ─► inventory/   (inventory imports nothing else from backend)
         ├─► ci_engine ─► embodiment, human_jobs   (reads the twin it is handed)
         ├─► eval_engine ─► eligibility            (reads log files, not the twin)
         ├─► inventory_api ─► inventory
         └─► agent_chat ─► agent_tools ─► eval_engine, decision_graph, jobs
 models.py ◄─► policy.py;  almost every module imports models.py
```

`app.py` builds the `Simulator` and hands it the twin. Deferred (in-function) imports break cycles: `task_planner` imports `jobs` and `simulator`; `digital_twin` imports `scheduler` inside `__init__`; `operations/shift.py` imports `activities` inside `tick`; `policy.py` imports `models` inside `apply_policy`.

## 3. The tick

`Simulator.tick()` (`backend/simulator.py`) runs everything under `with twin.lock`, then publishes. Order:

1. `tick_count += 1`; `simulation_time += TICK_DT`.
2. `twin.scheduler.tick()`: recurring tasks come due.
3. `twin.shift.tick()` (in try/except; an error is logged and skipped): when the shift is RUNNING, generate due work (trucks, customer orders, ...); every `SHIFT_CHECK_EVERY_TICKS` (20) move people; always `orders.advance()`. A shift is PAUSED until `start()`; the app seeds the new floor and leaves it paused (and a reset pauses it), so the floor runs only assigned tasks; `start()` raises on classic.
4. `people.update_transits(twin)`: finished walks land before robots decide.
5. `twin.tasks.dispatch()`: queued (`PLANNING`) tasks get a robot and a plan.
6. For each robot in `_execution_order()` (higher task priority first, then id): `_tick_robot(robot)`. An exception marks the robot ERROR and fails its task.
7. `human_jobs.tick(twin)`: people's jobs advance (walk, work, return).
8. If the floor has equipment: `equipment.tick()` (conveyor, sorter); every 20 ticks `human_jobs.ensure_jam_jobs`.
9. `_detect_collisions()`.
10. `_apply_energy()`: per-tick watt-hour drain for bodies with a battery profile.
11. `_auto_charge()`: idle low-battery robots go to a charger (a drone idle in the air flies home).
12. `_park_idle()`: a new-floor ground robot idle on a cell jobs need is sent to `parking_area`, but only while other robots have work.
13. `_check_maintenance()`: one-time wear alert per robot.
14. `_tick_fleet()`: OTA progress, heartbeats, operator sync (errors are logged, never raised).
15. Every 5 ticks (`AUTHORIZATION_CHECK_EVERY_TICKS`): `_check_authorization_changes()` (flags, never halts).
16. Every 20 ticks: `_check_operator_shifts()`: classic uses wall-clock hours; other floors call `operations.activities.sync_duty`.
17. `twin.synchronize()`; `twin.record_history()`.
18. Lock released. `_publish()`: every `STATE_EMIT_EVERY_TICKS` (2) ticks, `broadcaster.publish("state", twin.snapshot())`.

Inside `_tick_robot`: halted robots skip; no task means idle (or keep charging); a PAUSED task holds; an `ASSIGNED` task calls `start_task`; then the current `Action` is dispatched: `NAVIGATE`, `PICK`, `DELIVER`, `CHARGE`, any of `STEP_ACTIONS` (via `_act_step`), `COMPLETE`, or `WAIT`. Before a move or step, physical waits can hold the robot (`_crossing_wait`, `_aisle_wait`, `_supervision_hold`, `_step_hold`).

The loop thread (`_run`, started by `create_app(run_thread=True)`) calls `tick()` only while `simulation_status == RUNNING`, paced to `TICK_DT / simulation_speed` seconds of wall time. `create_app(autostart=True)` sets RUNNING. Tests call `tick()` directly. `POST /api/simulation/tick` does the same. Randomness: the shift engine owns a seeded `random.Random`; the simulator uses the module-level `random` for collision risk, false success, scan miscount.

## 4. A task's lifecycle

Statuses (`models.TaskStatus`): CREATED, VALIDATING, PLANNING (queued), ASSIGNED, IN_PROGRESS, PICKING, TRANSPORTING, DELIVERING, COMPLETED, FAILED, CANCELLED, BLOCKED, PAUSED.

1. **Create** (`TaskManager.create_task(payload, internal=False)`): resolves ids and names, builds a `Task`, emits `TASK_CREATED`. `internal=True` is used by auto-charge, auto-park, the scheduler and the order engine; it still goes through the gate. Route: `POST /api/tasks` (201, or 422 if the gate failed).
2. **Gate** (`TaskManager.validate`, status VALIDATING): see section 5. On failure `fail_task` records the reason (FAILED) and nothing moves. `STOP_ROBOT`/`RESUME_ROBOT` skip the gate (`IMMEDIATE`).
3. **Branch on kind** after the gate: `INSTANT` types (agent/operator inspections, replan, audit, approval, sign-off) finish at once in `_run_instant`. Human jobs (`MANUAL_PICK`, `CLEAR_JAM`) start at once in `_run_human`; `human_jobs.tick` moves them. Everything else is set to PLANNING and waits for a robot.
4. **Select** (`dispatch`, then `select_robot` via `_score_candidates`): for `AUTO`, every available robot (IDLE or CHARGING, no task, not mid-OTA, not halted) that passes `robot_eligibility` is scored: route distance + `(100 - battery) * 0.25` + 10 per queued task + 15 if not IDLE; lowest wins. A robot with a floor profile must also pass `capability_reason` and have a route; a classic robot cannot take a floor job. A named robot waits in the queue until it is free and not halted.
5. **Plan** (`TaskManager.assign`, which calls `TaskPlanner.plan`): builds the `Action` list. Older task types are planned inside `plan`; floor job types call `JobSpec.plan`. If the battery estimate exceeds the charge, a detour to the charger plus a `CHARGE` is prepended (not for drones or arms). The list always ends with `COMPLETE`. A `PlanningError` fails the task. On success the task is ASSIGNED, the robot goes PLANNING, and the task's boxes are reserved.
6. **Execute** (`Simulator._tick_robot`): `start_task` (IN_PROGRESS, `TASK_STARTED` with a "before" world snapshot), then one action at a time (section 3).
7. **End**: `complete_task` (after-snapshot, `TASK_COMPLETED`), `fail_task` (battery, collision, controller error, step failure, reset), `cancel_task` (user, or the system with a reason). Fail and cancel put a new-floor robot's load down (`_set_down_load`) and release boxes and operators. `pause_task`/`resume_task` toggle PAUSED.

### Job-step framework and `JobSpec`

`jobs.JOB_SPECS: Dict[TaskType, JobSpec]` is filled at the bottom of `backend/jobs.py`. A `JobSpec` has: `label`, `guide` (payload hint shown to the chat agent), `classes` (embodiment classes allowed; empty means no class restriction), `check(manager, task)` (gate: request is valid), `plan(planner, task, robot, blocked)` returning `(actions, waypoints, handling_ops)`, `target(...)` (first cell to reach, used for distance and routing), and flags `carries_box`, `air`, `human`, `mission_wh` (drone energy gate). Payload keys live on `Task.params` (`JOB_PARAM_KEYS`: slot, quantity, station, face, dock, lane, order_id, pack_cell, segment).

| Group | Types (bodies) |
|---|---|
| Pallets | `UNLOAD_TRUCK` (FORKLIFT, HEAVY_HAULER); `PUTAWAY_PALLET`, `RETRIEVE_PALLET`, `LOAD_TRUCK` (FORKLIFT) |
| Totes | `TOTE_TO_STATION`, `RETURN_TOTE` (AMR, HUMANOID); `RETURNS_PUTAWAY` (HUMANOID) |
| Stations | `PICK_ITEMS` (PICKER); `PACK_ORDER` (ARM) |
| People | `MANUAL_PICK`, `CLEAR_JAM` (`human=True`, no robot) |
| Air and security | `CYCLE_COUNT` (DRONE); `PATROL` (SCOUT) |

The sixteen older types (`PICK_AND_DELIVER` ... `BATCH_DELIVER` and the actor-class tasks) keep their own code paths on both floors. Steps with a physical duration (`LIFT_TO`, `LOWER`, `TAKEOFF`, `LAND`, `SCAN`, `GRASP`, `PLACE`, `PLACE_ON_CONVEYOR`, `WAIT_CLEAR`) run through `Simulator._act_step`: `_begin_<step>` checks the precondition and returns a tick count (or `None` to keep waiting; `ValueError` fails the task), the simulator counts the ticks, then `_finish_<step>` makes it so (True advances). Durations come from the catalog via `embodiment.step_ticks`. The five oldest box jobs keep `PICK_TICKS`/`DELIVER_TICKS` (`TIMED_BY_TICKS`).

To add a job type: add the `TaskType` (`models.py`); register a `JobSpec` in `jobs.py`; add a `CERTIFICATION_REQUIREMENTS` entry if a person needs a credential; add a new `ActionType` step only if needed (then also `STEP_ACTIONS` and `_begin_`/`_finish_` in `simulator.py`). `DigitalTwin.options()` and the chat agent's guide read `JOB_SPECS` automatically; the dashboard's `TASK_FIELDS` in `frontend/app.js` does not (it has no entry for any of the 13).

## 5. The trust layer

Full concept mapping: `TRUST_LAYER.md`. Code map:

| Where it runs | Code | What |
|---|---|---|
| Gate (before any movement) | `TaskManager.validate` | Calls `robot_eligibility` (status ERROR/STOPPED, unapproved firmware, `allowed_task_types`; battery deliberately excluded), `agent_eligibility`, `operator_eligibility`, then `capability_reason` (body class, box kind, declared payload, shelf reach, supervision, drone round-trip energy) and a route check; `cert_scope_ok` also applies to operators on non-classic floors. A `JobSpec.check` runs first. AUTO tasks on a layered floor (or of a `JOB_SPECS` type) get `_fleet_reason`: some body must be able to do it. |
| Robot selection | `_score_candidates` | Same `robot_eligibility` and `capability_reason`, so AUTO skips what the gate would block. |
| Physical waits (decisions, not rejections) | `Simulator` | `PERSON_ON_CROSSING`, `PERSON_IN_AISLE` (forklift, hauler), `SUPERVISOR_ABSENT` (humanoid), `PERSON_IN_CELL` and `CONVEYOR_JAMMED` (arms); `_escalate` after `SAFETY_WAIT_ESCALATE_S`. Clearance and no-fly are enforced by routing (`Warehouse.passable`). |
| Mid-task re-check | `_check_authorization_changes` | `robot_eligibility` plus `physical_recheck`; emits `TASK_AUTHORIZATION_CHANGED` once per task. Flags only. |
| Evaluation (after the fact) | `eval_engine.evaluate_events` | Reads `logs/tasks/<id>.json`. `DEFAULT_CHECKS` = 10 base checks (sequence, terminal state, path, battery, collision, stuck, controller errors, interruption, `entities_valid`, `state_transition`) + 10 `EMBODIMENT_CHECKS` (payload, reach, clearance, no-fly, human zone clear, supervision, count, hand-off, sort, placement level). A check with no matching events is a not-applicable PASS. Calls `GET /api/tasks/<id>/eval`, `POST /api/evals/run`, `python -m backend.run_evals`. |
| System checks | `CIEngine` (`ci_engine.py`) | 12 checks on the live twin, on demand (`POST /api/ci/run`), not per tick. The environment check uses the layout's `required_zones` and per-body connectivity on layered floors. |
| Injected faults | `FaultInjector` (`twin.faults`) | `roll(kind)` is called where a fault can happen: `scan_miscount`, `grasp_fail`, `wrong_level` (simulator); `conveyor_jam`, `handoff_loss`, `mis_sort` (equipment); `misdeclared_weight` (shift engine, inbound pallets). Each has a `CONFIG` risk (default 0.0); `arm(kind)` queues a one-shot. No HTTP route arms a fault yet. |

The gate and the grade share `eligibility.py` on purpose: change a rule there and both change. The physical rules at the bottom of that file return `(ok, reason)`; `eval_engine.py` imports the same ones (with fallbacks). `Operator.certifications` is kept in sync from the workforce record by `FleetBridge`.

## 6. State and persistence

**`DigitalTwin` is the root object** (`digital_twin.py`). It owns `lock`, `logger`, `events`, `ids`, `warehouse`, `navigation`, `planner`, `tasks`, `robots`, `boxes`, `agents`, `operators`, `stock`, `equipment`, `faults`, `scheduler`, `shift`, `statistics`, `history`, `inventory`, `fleet`. The frontend renders only its snapshots. Constructor: `DigitalTwin(log_dir="logs", data_dir="data", persist_logs=True, demo=True, demo_tasks=True, inventory_path=":memory:", layout="classic")`. Only classic loads a twin demo (`load_demo`); a non-classic twin boots empty and the app seeds it (`app._seed_floor`).

**Locks.** `twin.lock` is an `RLock`. The only allowed order is `twin.lock` then the inventory store lock (`InventoryStore.lock`). `FleetBridge.mutate()` takes `twin.lock` and runs an inventory action; the inventory service notifies listeners (`FleetBridge._on_change`) only after the outermost transaction commits, outside the store lock. Other locks (`TaskManager._lock`, `EventSystem._lock`, `WarehouseLogger._lock`, `Broadcaster._lock`) are held briefly and not while calling other components (subscribers and sinks run outside them). `StockLedger`, `Equipment`, `people` and `ShiftEngine` take no lock; the caller must hold `twin.lock`. At HEAD these entry points do not take `twin.lock` themselves: `TaskManager.create_task`/`cancel_task`/`pause_task`/`resume_task`, `stop_robot`, `resume_robot`, `reset_robot`, `request_charge`, `emergency_stop`.

**Events and broadcast.** `twin.events.emit(EventType, message, category, ...)` appends to a ring buffer (`MAX_EVENTS_IN_MEMORY`), writes through the logger, then calls subscribers synchronously: the app's SSE publisher and the `ShiftEngine` (which feeds `OrderBook.on_event`). The logger also has sinks (the app publishes every record as SSE `log`). `Broadcaster` gives each browser a bounded queue and drops frames for slow clients.

**Save and load.** `POST /api/state/save|load` use `serialize()` / `load_state()` (version 1 JSON at `<data_dir>/warehouse_state.json`). They cover robots, boxes, agents, operators, tasks, schedules, statistics, simulation clock and id counters, then `fleet.rebind_all()`. They do not write the stock ledger, equipment, shift engine and orders, armed faults or the layout name, so a floor with stock, a running conveyor or a running shift does not round-trip; plan 1c (Tasks 3 and 4) adds a version 2 format. `GET /api/export/state` returns the same JSON.

**Inventory store.** `inventory/` is a SQLite database (schema version 3; WAL mode for file databases, in-memory by default). `InventoryService` is mixins (`catalog`, `fleet`, `servicing`, `ota`, `workforce`) over `ServiceCore` and `InventoryStore`. `bootstrap.open_inventory(path, demo, profile=layout_name)` seeds from a process-wide in-memory template; a file seeded under another schema, seed or profile is re-seeded and the old one kept as `.bak`. Seed profiles: `classic`, `distribution_center`. Changes append to `change_log`; feeds are `/api/fleet/changes` and `/api/workforce/changes`.

**Folders and `WAREHOUSE_LAYOUT`.** `python -m backend.app` reads `WAREHOUSE_LAYOUT` (default `classic`), plus `WAREHOUSE_HOST` (127.0.0.1) and `WAREHOUSE_PORT` (5000). `build_twin(layout)` refuses an unknown layout before writing anything.

| Floor | Logs | Data |
|---|---|---|
| classic | `logs/` (`warehouse.log`, `events.json` as JSON Lines, `tasks/<task_id>.json`) | `data/` (`inventory.sqlite3`, `.bak`, `warehouse_state.json`) |
| distribution_center | `logs/distribution_center/` | `data/distribution_center/` (also `-wal`, `-shm` files) |

Both `*/distribution_center/` folders are git-ignored, as are `logs/*.log`, `logs/events.json`, `data/warehouse_state.json`, `data/inventory.sqlite3*`. `logs/tasks/*.json` are not ignored. Task ids restart at `task_001` each boot and the logger appends to a task file already on disk, so a fresh run adds to an old file of the same name. `CONFIG` in `models.py` is process-global: `policy.py` and the `/api/policies/*` routes mutate it in place.

## 7. HTTP API and frontend

`backend/app.py` has every route except inventory; `backend/inventory_api.py` adds `/api/fleet/*` and `/api/workforce/*` (`NotFound` 404, `Conflict` 409, `ValueError` 400). Errors are `{"ok": false, "error": ..., "field": ...}`; `guarded` maps `KeyError` to 404 and `ValueError` to 400.

| Group | Routes |
|---|---|
| Static | `GET /`, `GET /<file>` from `frontend/` |
| Reads | `/api/state`, `/api/warehouse`, `/api/robots[/<id>]`, `/api/boxes`, `/api/agents`, `/api/operators`, `/api/tasks[/<id>]`, `/api/logs`, `/api/events`, `/api/statistics[/history]`, `/api/fleet/load`, `/api/maintenance/alerts`, `/api/health` |
| Commands | `POST /api/robots`, `/api/robots/<id>/{capabilities,stop,resume,charge,reset}`, `/api/boxes`, `/api/agents`, `/api/operators`, `/api/operators/<id>/shift`, `/api/tasks`, `/api/tasks/<id>/{cancel,pause,resume}` |
| Simulation | `POST /api/simulation/{start,pause,stop,reset,emergency-stop,resume,speed,tick}` |
| Trust and policy | `GET /api/tasks/<id>/eval`, `POST /api/evals/run`, `GET /api/decisions`, `/api/policies` (+ `reload`, `llm-narration`, `collision-risk`, `false-success-risk`), `/api/reports/missions.{csv,html}`, `POST /api/ci/run`, `GET /api/ci/status` |
| Schedules and chat | `/api/schedules` (GET, POST, toggle, DELETE), `POST /api/agent/chat` |
| Persistence | `/api/state/{save,load,reset}`, `/api/export/{state,tasks}`, `/api/logs/{clear,export}`, `/api/tasks/<id>/logs/export` |
| Stream | `GET /api/stream` (SSE) |

Not present at HEAD: any route for shift, orders, stock, equipment or faults. `POST /api/simulation/reset` and `/api/state/reset` reset the twin under `twin.lock`, then re-run `_seed_floor`.

**Getting state.** `GET /api/state` returns `twin.snapshot(include_layout=True)` (adds `warehouse` and `config`). The stream sends `state` first with the layout, then every 2 ticks a snapshot without it, plus `log`, `event`, `ci` and `agent-chat` frames. A snapshot has `layout_name`, `environment`, `robots`, `boxes`, `agents`, `operators`, `tasks` (latest 60), `statistics`, `robot_statistics`, `ci`, `options`; it carries no stock, conveyor, orders or shift data.

**Pages.** `index.html` + `app.js` (one IIFE, `var`/`function`, no build step, no libraries): loads `/api/state` and `/api/logs`, then opens `EventSource("/api/stream")` for `state`, `log`, `ci`, `agent-chat` (it does not listen for `event`); also polls schedules, fleet load and trends every 5 s. It draws a canvas floor from `snapshot.warehouse`; `cellFill` only knows classic cell types, so new-floor types draw as plain floor, and there are no people, conveyor, air-layer or shift views. `fleet.html` + `fleet.js`: the fleet and workforce source systems; polls every 3 s (`POLL_MS`), no SSE.

## 8. Tests

Pytest, no threads: tests build a twin and call `Simulator.tick()` directly.

- Location and names: `backend/tests.py` (main suite) and 51 `backend/test_*.py` files; 698 `def test_` lines at HEAD (more once parametrised). `pytest.ini`: `testpaths = backend`, `python_files = tests.py test_*.py`, `addopts = -q`. There is no `conftest.py`; each file defines its own fixtures (usually `twin` and `sim`).
- Suite command: `.venv/bin/python -m pytest -o addopts="" -q`.
- Classic fixtures use `DigitalTwin(log_dir=..., data_dir=..., persist_logs=False, demo=True, demo_tasks=False)`. New-floor tests pass `layout="distribution_center"` to the same constructor, then add robots, boxes and operators through the public API (`add_robot(name=, asset_id=, position=)`, `add_box(kind=, slot=, ...)`, `add_operator(name=, worker_id=)`); the twin boots empty. `Warehouse(layout="distribution_center")` is used for layout-only tests.
- Guards that pin the classic floor: `test_layout_classic_golden.py` (grid cell by cell), `test_inventory_seed_golden.py` (fleet and workforce seed fingerprint), `test_layouts.py` (registry and default layout).
- Soak: `test_shift_soak.py` (fault-free shift on the new floor). Trust-layer tests: `test_trust_rules.py`, `test_trust_checks*.py` (grade `logs/eval_examples/multi_embodiment/`), `test_eval_engine.py` (grades `logs/eval_examples/` and the real `logs/tasks/`).
- Two known failures, per `RUN.md` and the plan docs (not re-run for this document): `test_eval_engine.py::test_real_task_006_box_conflict_is_caught` and `tests.py::test_idle_robot_with_a_low_battery_charges_itself`. Tests and the app rewrite files under `logs/tasks/`; do not stage them by accident.

## 9. Invariants a change must keep

- **Classic is untouched.** `DigitalTwin()`, `Warehouse()` and `create_app()` default to `classic`. Its grid, zones, seed, battery model (percent per cell), wall-clock operator shifts, AUTO scoring, `PICK_TICKS`/`DELIVER_TICKS` and event data must not change. `models.WALKABLE_CELLS` is classic's set. The golden tests above enforce part of this.
- **No profile, old behaviour.** `robot.mobility` is set only when `layout_name != "classic"` (`add_robot`, `FleetBridge.floor_profile`). A robot with `mobility is None` keeps classic routing, speed, battery and jobs, and cannot take a `JOB_SPECS` type (`capability_reason`, `no_profile_reason`). Guard every new-floor behaviour with `robot.mobility is not None` or `twin.equipment is not None`.
- **One gate.** Every task, including `internal=True`, goes through `create_task` and `validate` (only `STOP_ROBOT`/`RESUME_ROBOT` skip it). Eligibility rules live once in `eligibility.py`.
- **Lock order** `twin.lock` then inventory store lock; inventory listeners run after commit; new code that touches stock, equipment, people, shift or faults must hold `twin.lock`.
- **The twin is the source of truth.** The frontend never invents positions or states.
- **Dependencies:** Flask, PyYAML, pytest and the standard library only. No new dependencies, no Node, no build step.
- **Keep `inventory/` standalone** (it imports nothing from the rest of `backend`).
- **Style:** `from __future__ import annotations`, module docstrings, plain-language messages; bad input raises `ValueError`, an unknown robot, operator or task id raises `KeyError`.
- **Stage only your files.** Runs rewrite `logs/tasks/task_00*.json` and `.DS_Store` files.

## 10. Planned, not yet built (plan 1c)

Source: the Goal and Architecture paragraphs of `docs/superpowers/plans/2026-10-05-multi-embodiment-1c-seed-api-dashboard-soak.md` (written, not yet executed). None of this exists in the code yet unless marked. When plan 1c lands, fold this section into the sections above.

- **Goal:** the app boots the distribution-centre floor with its full seed (15 non-retired robots, 10 workers, 60 pallets, 80 totes); save and load v2; HTTP for shift, orders, stock, people, equipment and faults; a dashboard that draws the layout-driven floor, glyphs, air layer, people, conveyor, robot and shift panels; a fixed-seed soak that runs clean; and closing two residuals from plan 1b.
- **New units named:** `backend/seeds/` with classic and distribution-centre seeds (planned; only the stopgap `seeds/distribution_center.py` exists); `backend/operations_api.py` (the operations routes); `backend/soak.py` (soak runner and CLI); `frontend/floor_model.js` (DOM-free view helpers, unit-tested with JavaScriptCore).
- **Existing units to change:** `to_dict`/`from_dict` on the ledger, equipment, shift engine and orders (save/load v2); a `cell_types` table and live equipment and shift data in the snapshot; the long-shift policies plan 1b deferred.
- Code comments that point at it: `faults.py` (`POST /api/faults/<kind>`), `operations/shift.py` ("its HTTP controls are plan 1c's"), `app._seed_floor`.

Design history: `docs/superpowers/specs/2026-09-30-multi-embodiment-operations-design.md` (binding spec for the multi-embodiment work), `docs/superpowers/specs/2026-09-30-fleet-workforce-inventory-design.md`, and `docs/superpowers/plans/` (`2026-09-30-fleet-workforce-inventory.md`, `2026-09-30-multi-embodiment-1a-floor-and-motion.md`, `2026-10-01-multi-embodiment-1b-jobs-rules-shift.md`, `2026-10-05-multi-embodiment-1c-seed-api-dashboard-soak.md`).
