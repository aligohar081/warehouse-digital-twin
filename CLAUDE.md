# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

A Python (Flask) simulator of a warehouse with a plain-JavaScript dashboard: robots of eight embodiments, people, goods, a conveyor, a pre-execution trust gate and after-the-fact grading. **Read `ARCHITECTURE.md` before changing code** — it maps the modules, the tick, a task's lifecycle, the trust layer, state and locking. This file stays short on purpose: it is loaded into every session.

## Commands

All commands run from the repo root with the project venv (Python 3.14).

```bash
.venv/bin/python -m pytest -o addopts="" -q                                   # full suite (~15 s at master)
.venv/bin/python -m pytest -o addopts="" -q backend/test_station_jobs.py      # one file
.venv/bin/python -m pytest -o addopts="" -q backend/tests.py::test_name       # one test
.venv/bin/python -m pytest -o addopts="" -q -k "collision or battery"         # by keyword
WAREHOUSE_PORT=5055 .venv/bin/python -m backend.app                            # run the app (classic floor)
WAREHOUSE_LAYOUT=distribution_center WAREHOUSE_PORT=5070 .venv/bin/python -m backend.app   # the new floor
.venv/bin/python -m backend.run_evals [--demo | --stats | logs/tasks/task_006.json]        # rule-based grading CLI
```

- Exactly two tests fail and are allowed to: `backend/test_eval_engine.py::test_real_task_006_box_conflict_is_caught` and `backend/tests.py::test_idle_robot_with_a_low_battery_charges_itself`. Any other failure is yours.
- Ports: macOS AirPlay holds 5000; **5050 is the user's own running server — never use it**; Chromium blocks 5060. `../.claude/launch.json` (the parent folder) defines `warehouse-twin` (5055) and `warehouse-twin-new-floor` (5070) for the preview browser.
- No linter is configured. There is no Node on this machine; JavaScript unit tests (from plan 1c) run on macOS's `jsc` (`/System/Library/Frameworks/JavaScriptCore.framework/Versions/A/Helpers/jsc`) through a pytest wrapper. The promptfoo suites in `evals/` need `npx` (see `RUN.md` §6).

## Big picture

- `DigitalTwin` (`backend/digital_twin.py`) owns all state; `Simulator.tick()` advances it under `twin.lock`; `backend/app.py` serves the API, an SSE stream and `frontend/`. Tests build a twin and call `Simulator(twin).tick()` directly — no threads.
- **Two floors**, picked by layout name: `classic` (20×15, the default for `DigitalTwin()`, `Warehouse()` and every existing test) and `distribution_center` (32×20: docks, racks, tote shelves, walkway, stations, conveyor). The app reads `WAREHOUSE_LAYOUT` and keeps the new floor's files in `data/distribution_center/` and `logs/distribution_center/`.
- **A robot with a floor profile** (`robot.mobility`, built from the inventory catalog) gets per-body routing, layers (ground/air), watt-hour energy and physical job steps. `robot.mobility is None` means classic behaviour everywhere.
- **Tasks:** `TaskManager.create_task` → `validate` (the gate) → `dispatch`/selection → `TaskPlanner.plan` (older types) or `JobSpec.plan` (the 13 floor jobs in `backend/jobs.py`) → `Simulator` runs the `Action` list → complete/fail/cancel. Every task, including internal ones (charging, parking, orders), passes the gate.
- **Trust layer:** `backend/eligibility.py` is the one rule set, used by both the gate and `backend/eval_engine.py` (which grades a task's log file afterwards). `ci_engine.py` runs system checks; `faults.py` injects faults. Concepts: `TRUST_LAYER.md`.
- **Inventory:** `backend/inventory/` is a standalone SQLite fleet/workforce system (it imports nothing else from `backend`); `fleet_bridge.py` binds twin robots and operators to its records.
- **Shift and orders** (`backend/operations/`): a seeded `ShiftEngine` generates trucks, customer orders, counts and patrols; `OrderBook` chains each order's jobs. The shift starts paused.

## Rules every change must keep

- **The classic floor must not move:** grid, zones, seed, battery model, wall-clock shifts, AUTO scoring, `PICK_TICKS`/`DELIVER_TICKS`, event data and the classic dashboard stay as they are. Golden tests pin part of this. Guard new-floor behaviour with `robot.mobility is not None` / `twin.equipment is not None`.
- **Never edit an existing test file.** New tests go in new `backend/test_*.py` files; new-floor tests construct `DigitalTwin(..., layout="distribution_center")` and add robots, boxes and people through the public API.
- **Lock order:** `twin.lock` → inventory store lock. Stock, equipment, people and the shift engine take no lock of their own; callers hold `twin.lock`.
- **Dependencies:** Flask, PyYAML, pytest and the standard library only. Frontend: plain ES5 (`var`, `function`, no modules, no build step, no libraries).
- **Style:** `from __future__ import annotations`, typing annotations, a module docstring on every new module, comments that say why, plain-language messages. Bad input raises `ValueError`; an unknown robot, operator, order or task id raises `KeyError`. API errors map to 400 validation, 404 unknown id, 409 state conflict.
- **Staging:** `git add <exact paths>`, never `git add -A`. App and test runs rewrite the tracked `logs/tasks/task_00*.json`, and macOS adds `.DS_Store` files — never stage those.
- **No merges, pushes or PRs** without the user's explicit choice.

## Where the work stands

- Design authority: `docs/superpowers/specs/2026-09-30-multi-embodiment-operations-design.md`. Plans 1a and 1b are built; **plan 1c** (`docs/superpowers/plans/2026-10-05-multi-embodiment-1c-seed-api-dashboard-soak.md`) is written and not yet executed. Plans are executed with the superpowers subagent-driven-development skill.
- `docs/handoffs/` holds dated session hand-offs; the newest one is the entry point for resuming work. Read only the one you need.
- Keep `ARCHITECTURE.md` true: when a change adds, moves or removes a unit, a route or a step of the tick, update it in the same commit. Under a plan whose tasks list their exact files, don't touch it mid-plan; refresh it once the plan lands.

## Compact instructions

When compacting, keep: the current plan and task number with its status, commit SHAs, the latest suite count, file paths of briefs/reports/ledgers in use, rulings made and decisions still waiting on the user. Drop file contents, diffs, test output and agent transcripts — they can be re-read from disk or git.
