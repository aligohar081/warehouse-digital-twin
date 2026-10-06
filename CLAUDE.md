# CLAUDE.md

A Python (Flask) simulator of a warehouse with a plain-JavaScript dashboard: robots of eight embodiments, people, goods, a conveyor, a pre-execution trust gate and after-the-fact grading. This file loads into every session, so it only points to the docs — read just the section you need.

## Folder map

- `backend/` — simulator, Flask app (`app.py`), tests (`tests.py`, `test_*.py`); `ARCHITECTURE.md` §2 lists every module.
- `backend/inventory/` — standalone SQLite fleet/workforce system; imports nothing else from `backend`.
- `backend/layouts/` — the floors: `classic.py`, `distribution_center.py`, built on `base.py`.
- `backend/operations/` — the new floor's shift engine, order book and people's activities.
- `backend/seeds/` — the stopgap distribution-centre seed the app boots.
- `frontend/` — dashboard (`index.html`, `app.js`, `style.css`) and fleet page (`fleet.*`).
- `evals/` — promptfoo suites and their generators; `*.generated.yaml` is generated output.
- `logs/` — runtime output; tracked: `tasks/task_00*.json` (runs rewrite them) and `eval_examples/` (grading fixtures).
- `data/` — runtime SQLite and saved state (git-ignored).
- `docs/` — `superpowers/specs/` designs, `superpowers/plans/` plans, `handoffs/` dated session hand-offs.

## Commands (repo root, project venv, Python 3.14)

```bash
.venv/bin/python -m pytest -o addopts="" -q                                   # full suite (~15 s at master)
.venv/bin/python -m pytest -o addopts="" -q backend/test_station_jobs.py      # one file
.venv/bin/python -m pytest -o addopts="" -q backend/tests.py::test_name       # one test
.venv/bin/python -m pytest -o addopts="" -q -k "collision or battery"         # by keyword
WAREHOUSE_PORT=5055 .venv/bin/python -m backend.app                            # run the app (classic floor)
WAREHOUSE_LAYOUT=distribution_center WAREHOUSE_PORT=5070 .venv/bin/python -m backend.app   # the new floor (its shift starts paused)
.venv/bin/python -m backend.run_evals [--demo | --stats | logs/tasks/task_006.json]        # rule-based grading CLI
```

- Exactly two tests fail and are allowed to: `backend/test_eval_engine.py::test_real_task_006_box_conflict_is_caught` and `backend/tests.py::test_idle_robot_with_a_low_battery_charges_itself`. Any other failure is yours.
- Ports: macOS AirPlay holds 5000; **5050 is the user's own running server — never use it**; Chromium blocks 5060. `../.claude/launch.json` (the parent folder) defines `warehouse-twin` (5055) and `warehouse-twin-new-floor` (5070) for the preview browser.
- No linter; no Node. JavaScript unit tests run on `/System/Library/Frameworks/JavaScriptCore.framework/Versions/A/Helpers/jsc` through a pytest wrapper. Promptfoo needs `npx` (`RUN.md` §6).

## Key concepts

- `DigitalTwin` (`digital_twin.py`) owns all state; `Simulator.tick()` advances it under `twin.lock`; tests call `Simulator(twin).tick()` directly — no threads. Two floors by layout name (`WAREHOUSE_LAYOUT` in the app): `classic` (20×15; the default for `DigitalTwin()`, `Warehouse()` and every existing test) and `distribution_center` (32×20).
- A robot with a floor profile (`robot.mobility`) gets per-body routing, ground/air layers, watt-hour energy and physical job steps; `robot.mobility is None` means classic behaviour everywhere.
- Every task, internal ones too, passes the gate: `create_task` → `validate` → dispatch → `TaskPlanner.plan` or `JobSpec.plan` (the 13 floor jobs in `jobs.py`) → `Simulator` runs the `Action` list. `eligibility.py` is the one rule set, shared by the gate and `eval_engine.py` (which grades a task's log afterwards).

## Rules every change must keep

- **The classic floor must not move:** grid, zones, seed, battery model, wall-clock shifts, AUTO scoring, `PICK_TICKS`/`DELIVER_TICKS`, event data and the classic dashboard stay as they are. Golden tests pin part of this. Guard new-floor behaviour with `robot.mobility is not None` / `twin.equipment is not None`.
- **Never edit an existing test file.** New tests go in new `backend/test_*.py` files; new-floor tests construct `DigitalTwin(..., layout="distribution_center")` and add robots, boxes and people through the public API.
- **Lock order:** `twin.lock` → inventory store lock. Stock, equipment, people and the shift engine take no lock of their own; callers hold `twin.lock`.
- **Dependencies:** Flask, PyYAML, pytest and the standard library only. Frontend: plain ES5 (`var`, `function`, no modules, no build step, no libraries).
- **Style:** `from __future__ import annotations`, typing annotations, a module docstring on every new module, comments that say why, plain-language messages. Bad input raises `ValueError`; an unknown robot, operator, order or task id raises `KeyError`. API errors map to 400 validation, 404 unknown id, 409 state conflict.
- **Staging:** `git add <exact paths>`, never `git add -A`. App and test runs rewrite the tracked `logs/tasks/task_00*.json`, and macOS adds `.DS_Store` files — never stage those.
- **No merges, pushes or PRs** without the user's explicit choice.
- **Keep `ARCHITECTURE.md` true:** when a change adds, moves or removes a unit, a route or a step of the tick, update it in the same commit. Under a plan whose tasks list their exact files, don't touch it mid-plan; refresh it once the plan lands.

## Where to look

- How the code fits together → `ARCHITECTURE.md`: §2 repo map, §3 tick, §4 task lifecycle, §5 trust layer, §6 state, §7 API and frontend, §8 tests, §9 invariants, §10 plan 1c (not yet built).
- Setup, running, troubleshooting → `RUN.md`: §2 setup, §3 run, §4 tests, §5 eval CLI, §6 promptfoo, §7 troubleshooting.
- Trust-layer concepts → `TRUST_LAYER.md`; promptfoo suites → `evals/README.md`.
- Design authority → `docs/superpowers/specs/2026-09-30-multi-embodiment-operations-design.md` (inventory: `2026-09-30-fleet-workforce-inventory-design.md` beside it).
- Current plan → `docs/superpowers/plans/2026-10-05-multi-embodiment-1c-seed-api-dashboard-soak.md`; plans run with the superpowers subagent-driven-development skill.
- Status and next steps → the newest file in `docs/handoffs/` (read only that one).

## Context rules (keep context minimal)

- **Reading:** locate with Grep/Glob first; never read whole folders; read line ranges of large files. Plans (6–12K lines): `grep -n '^### Task' <plan>`, read one task. `README.md` (1.4K lines) and the specs: grep the headings, read one section. Don't re-read a file already read this session unless it changed.
- **Never read unless the user asks:** generated output (`evals/*.generated.yaml`), dependencies (`.venv`), caches, logs (`logs/*.log`, `logs/events.json`, `logs/tasks/`, `logs/distribution_center/`), data and binaries (`data/`), `.git/`, `_archive/`. `.claude/settings.json` deny-reads these and the finished plans (1a, 1b, fleet-workforce); ask before working around it.
- **Exploration:** for broad exploration or research, use a subagent and keep only a short summary.
- **Output:** keep command output short (`-q`, `head`/`tail`, `grep`, `wc -l`); never dump whole logs, diffs, test runs or large JSON. Keep replies brief: don't restate code the user can see; explain at length only when asked. Make targeted edits, not whole-file rewrites.
- **Files:** don't create scratch, backup, test-output or `_v2`/`_old`/copy files unless needed; temporary files go in `tmp/` (git-ignored). Don't create docs or summaries unless asked. Keep this file under ~80 lines: detail goes in `docs/` or the docs above, linked from here.

**Cleanup** — when the user says "wrap up" / "end session", or a task is finished:
1. List the files created or made obsolete this session (temp files, scratch scripts, old versions, unused outputs), with a one-line reason each.
2. Delete only what the user approves; move anything uncertain to `_archive/` instead.
3. Empty `tmp/`.
4. Update this file only if something durable changed (a new folder, command or convention), keeping it short.
5. Tell the user to run `/clear` before starting the next task.

## Compact instructions

When compacting, keep: the current plan and task number with its status, commit SHAs, the latest suite count, file paths of briefs/reports/ledgers in use, rulings made and decisions still waiting on the user. Drop file contents, diffs, test output and agent transcripts — they can be re-read from disk or git.
