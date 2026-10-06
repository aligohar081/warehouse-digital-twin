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
| GET | `/api/shift` | the shift panel: status, clock, config, counters, throughput, in flight, backlog, failed orders |
| POST | `/api/shift/start` | start generating work |
| POST | `/api/shift/pause` | stop generating work; orders in flight carry on |
| POST | `/api/shift/config` | change `pace`, `seed` or `rates`; a bad value is a `400` and changes nothing |
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
| POST | `/api/faults/{kind}` | arm `count` occurrences of a fault (default 1) |
| POST | `/api/agent/chat` | the chat agent (`message`, optional `history`) |
| GET | `/api/orders` | orders, newest first (`?status=&kind=&limit=`) |
| GET | `/api/orders/{id}` | one order with its stages, jobs and attempts |
| GET | `/api/stock` | ledger locations (recorded, true and counted quantities) and totals (`?sku=`) |
| GET | `/api/people` | everyone: zone, destination, status |
| GET | `/api/equipment` | the conveyor, the sorter, the arms' cells, recent hand-offs |
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
