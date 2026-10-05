# Handoff: multi-embodiment operations, plan 1b in progress

**Date:** 2026-10-02
**Repo:** `physical_ai/warehouse-digital-twin`
**Branch:** `feature/multi-embodiment-ops`, HEAD `0f6f0bf`. It is built on top of `feature/fleet-inventory`.
**Status:** Plan 1b is running under subagent-driven development (SDD). Tasks 1–4 of 14 are complete and reviewed. **Task 5 is next and has not been dispatched.**

## Program goal

Simulate a real-world, multi-layer physical-AI warehouse:

- many robot types, operators and operations;
- a robot and worker inventory with real-world detail, stored in one unified database (PWA's Postgres);
- that database serves as each robot's passport, and changing a robot's details re-mints its passport;
- detailed per-job robot logs, as a real robot produces: sensor readings (imperfect, with noise and faults) and the decisions it made.

PWA (`physical-work-assurance`) is the unified database and passport engine. The twin is the source system: it acts as the fleet manager, the workforce record and the telemetry source.

### Sub-projects

| Sub-project | What it is | State |
|---|---|---|
| **A** | Fleet and workforce inventory in the twin: a model catalog, robot instances, workers, and a change feed | **Done** on `feature/fleet-inventory`. It includes the `rebind_all` regression fix (`0250f62`, `31f6837`). **The user has not decided how it lands** (see "Waiting on the user"). |
| **C + E** | Multi-embodiment operations on one 32×20 floor, with all 15 non-retired robots and all workers. E (conveyor and hand-offs) is folded in. | **Step 1 is in progress:** plan 1a done, plan 1b running, plan 1c not written yet. |
| **D** | Sensors, decisions, per-job logs, a live view and replay | Step 2. Its design is in the spec (§13). No plan yet. |
| **B** | Send twin data to PWA, and re-mint passports when a robot changes | After C+D+E. Not designed. |
| **F** | The twin's gate checks passports in PWA | After B. Not designed. |

The user chose:

- one 32×20 floor, `distribution_center`, at 1.5 m per cell;
- WH-02 is dropped;
- the shift runs on its own;
- sensors behave like real, imperfect hardware;
- viewing is a live panel plus a per-job log with replay;
- C and D are designed together and built in two steps.

## Task list: done and remaining

### Done

- [x] **A:** fleet and workforce inventory, on `feature/fleet-inventory`.
- [x] **A's `rebind_all` regression fix:** `load_state` survives an asset id reused by another robot class (`0250f62`, `31f6837`).
- [x] **Spec** for C+D+E: written, approved and amended.
- [x] **Plan 1a**, floor and motion, `fc47519..c088d98`, reviewed:
  - [x] classic golden test, and the layout registry move;
  - [x] distribution-centre 32×20 layout;
  - [x] inventory seed profiles and new-floor catalog data;
  - [x] mobility profiles and per-profile routing;
  - [x] ground and air layers with layer-aware traffic;
  - [x] people as zone presence, with the crossing wait;
  - [x] box kinds, declared and true weights, and the stock ledger.
- [x] **Plan 1b**, Task 1: the shared physical rules and the fault switchboard (`e73dd1a..d6b2f44`).
- [x] **Plan 1b**, Task 2: goods in the twin, with one stock ledger and boxes placed by kind (`d6b2f44..297cf2f`).
- [x] **Plan 1b**, Task 3: the conveyor, the sorter and hand-off records (`297cf2f..35ddb3a`).
- [x] **Plan 1b**, Task 4: job steps with physical duration (`35ddb3a..0f6f0bf`).

### Remaining: plan 1b (jobs, rules, shift)

- [ ] **Task 5:** people rules, and safety waits that pair and escalate. **Next.** Its brief is already extracted.
- [ ] **Task 6:** energy on the new floor.
- [ ] **Task 7:** capability-filtered selection, the physical gate and the job framework. *Reviewer: opus.*
- [ ] **Task 8:** pallet jobs: unload, put away, retrieve, load.
- [ ] **Task 9:** tote jobs: to a station and back, and returns into storage. *Prepend the RETURNS_PUTAWAY lift ruling.*
- [ ] **Task 10:** station jobs and the jobs people do.
- [ ] **Task 11:** drone counts and scout patrols.
- [ ] **Task 12:** the shift engine and order chains. *Reviewer: opus.*
- [ ] **Task 13:** people's activities, breaks and duty by the shift clock.
- [ ] **Task 14:** evaluation checks, their fixtures, and layout-driven system checks. *Prepend the `placement_level_correct` ruling.*
- [ ] **Final review** of the whole branch (opus), then one fix wave.
- [ ] Give the user the "Rulings I made" list, then delete plan 1b's SDD workspace.

### Remaining: plan 1c (switching it on; not written yet)

- [ ] Write plan 1c with `superpowers:writing-plans`, against the real code. Include plan 1a's "To plan 1c" list and the 1c items below.
- [ ] New-floor seed, save and load (v2), and app boot.
- [ ] Operations API.
- [ ] Dashboard.
- [ ] Soak test and docs.
- [ ] Execute it with SDD: per-task reviews, then the final review.

### Remaining: step 2, D (sensors and job logs; spec §13; plan not written)

- [ ] Sensor models and imperfections.
- [ ] Sensor suites built from the component tree and known issues.
- [ ] Decisions driven by sensor readings.
- [ ] A recorder, run ids and retention.
- [ ] Telemetry and replay API.
- [ ] Live panel and replay UI.
- [ ] Step-2 soak test, fault matrix and docs.

### Remaining: after step 2

- [ ] **B:** twin → PWA ingestion of the inventory feed and job logs, and passport re-mint when a robot changes. Needs brainstorming, a spec and a plan.
- [ ] **F:** the twin's gate consults PWA passports and calibration before a job. Needs brainstorming, a spec and a plan.

### Waiting on the user

- [ ] **How A lands:** merge it into master, push it and open a PR, or keep it as-is.

## Key documents

All are committed.

- **Spec (the binding authority):** `docs/superpowers/specs/2026-09-30-multi-embodiment-operations-design.md`, sections §0–§19.
  - It carries amendments from the pre-flight checks of plans 1a and 1b.
- **Plan 1a:** `docs/superpowers/plans/2026-09-30-multi-embodiment-1a-floor-and-motion.md`. Done.
  - Its **"Carried forward"** section lists the items that plans 1b and 1c must pick up. Plan 1c should read "To plan 1c" (around line 121).
- **Plan 1b:** `docs/superpowers/plans/2026-10-01-multi-embodiment-1b-jobs-rules-shift.md`. About 12k lines, 14 tasks and 17 plan rulings.
  - Global Constraints are at lines 15–28. One of them: **do not edit any existing test file**, including test files that earlier tasks of this plan created.
- **A's spec and plan:** `docs/superpowers/specs/2026-09-30-fleet-workforce-inventory-design.md` and `docs/superpowers/plans/2026-09-30-fleet-workforce-inventory.md`.
- **Earlier handoff (brainstorm):** `docs/handoffs/2026-09-30-multi-embodiment-brainstorm.md`.

## Commits on this branch (beyond `feature/fleet-inventory`)

**Plan 1a (floor and motion)**, `fc47519..c088d98`:

- layout registry, with the classic floor pinned cell by cell;
- the 32×20 distribution-centre layout;
- inventory seed profiles;
- mobility profiles and per-profile routing;
- ground and air layers;
- people as zone presence, with the crossing wait;
- box kinds and the stock ledger;
- a walkway rule: robots never stop on it.

**Plan 1b (jobs, rules, shift)**, from `e73dd1a`:

| Task | Commits | What it built |
|---|---|---|
| 1. Rules and faults | `e73dd1a..d6b2f44` | `backend/eligibility.py` (8 shared physical rules) and `backend/faults.py` (`FaultInjector`, with seven CONFIG risks that default to 0.0) |
| 2. Goods in the twin | `d6b2f44..297cf2f` | The twin owns `StockLedger`, which tracks recorded vs true quantities. `store` validates before it mutates. |
| 3. Conveyor, sorter, hand-offs | `297cf2f..35ddb3a` | `backend/equipment.py`: a conveyor at (19,15)→(24,15) taking 20 ticks per cell, a sorter that decides each carton's dock once, and hand-off records |
| 4. Job steps | `35ddb3a..0f6f0bf` | `simulator.py` step framework (GRASP, LIFT_TO, PLACE, TAKEOFF, LAND, SCAN, WAIT_CLEAR, PLACE_ON_CONVEYOR) with physical durations. Fix round 1 hardened it: GRASP checks reach and that the item is held, LIFT_TO stores the resolved level, TAKEOFF requires a grounded drone, and bad inputs are validated when a step begins. |

## How to resume

1. Invoke `superpowers:subagent-driven-development` on plan 1b.
2. **The ledger is the recovery map:** `.superpowers/sdd/2026-10-01-multi-embodiment-1b-jobs-rules-shift/progress.md`.
   - The workspace is git-ignored, so `git clean -fdx` would delete it.
   - Its first line names plan 1b.
   - Tasks 1–4 each have a `Task N: complete` line. **Resume at Task 5.**
3. **The workspace** (same directory) contains:
   - `progress.md`: the ledger, the pre-flight table and every ruling;
   - `global-constraints.md`;
   - `implementer-instructions.md`, `reviewer-instructions.md`, `rereview-instructions.md` and `final-reviewer-instructions.md`;
   - `task-1…4-brief.md` and `task-1…4-report.md`;
   - **`task-5-brief.md`, already extracted** (1047 lines);
   - the `review-*.diff` packages.
4. **Task 5 dispatch:**
   - BASE `0f6f0bf`; implementer on sonnet; reviewer on sonnet.
   - Tell the implementer that the suite is currently **2 failed / 545 passed**, which is **+23 over the plan's expected counts**. Every later count in the plan is 23 low.
   - The brief's own carry list covers the Task 4 items that land in Task 5:
     - `supervision_ok` in `ROBOT_STEP`;
     - wait pairing across task end and reason switch.
5. **Before dispatching Task 9 and Task 14, prepend these controller rulings to their briefs.** The plan text doesn't have them; the ledger's Rulings section does.
   - **Task 9:** RETURNS_PUTAWAY runs NAVIGATE returns_qc → GRASP tote → NAVIGATE face → LIFT_TO(level) → PLACE → LOWER, using the plan's `lift_steps` helper.
   - **Task 14:** add a 10th evaluation check, `placement_level_correct`. It fails when a `PLACED` event's `true_level` differs from its `level`. Add one pass fixture and one fail fixture.
   - `implementer-instructions.md` already says a brief that starts with a **Controller ruling** is amended by it.
6. **Model choice:**
   - Implementers: sonnet for every task.
   - Reviewers: **opus for Tasks 7 and 12**, sonnet for the rest.
   - Re-reviews: sonnet (or haiku for tiny fixes).
   - The final whole-branch review: opus.
7. **After Task 14:**
   - Run the final review (opus) and **one** fix wave.
   - Give the user the full "Rulings I made" list.
   - Only then delete the workspace.
   - Then write plan 1c with `superpowers:writing-plans`, against the real code (rolling wave).

### SDD helper scripts

The skill's scripts are in `subagent-driven-development/scripts/` (`sdd-workspace`, `task-brief`, `review-package`). The last session wrapped them in a scratchpad script, which won't survive into a new session. Its content:

```bash
#!/bin/bash
# usage: sdd1b.sh pkg BASE HEAD | brief N | log "line"
cd /Users/bakarmac/projects/physical_ai/warehouse-digital-twin
S="/Users/bakarmac/Library/Application Support/Claude/local-agent-mode-sessions/b1b01456-dabd-4928-9308-f19a0dfd0865/1cb84334-6902-4367-b13b-342b70406e28/rpm/plugin_019r79DCX69VmRnTS9cmn5n2/skills/subagent-driven-development/scripts"  # if this moved, use the installed skill's scripts/ dir
P=docs/superpowers/plans/2026-10-01-multi-embodiment-1b-jobs-rules-shift.md
W=.superpowers/sdd/2026-10-01-multi-embodiment-1b-jobs-rules-shift
case "$1" in
  pkg) "$S/review-package" $P "$2" "$3" | sed 's#.*/##';;
  brief) "$S/task-brief" $P "$2" | sed 's#.*/##';;
  log) echo "$2" >> $W/progress.md;;
esac
```

Ledger lines start with `- ` (for example `- Task 5: dispatched BASE 0f6f0bf (sonnet)`).

## Items carried to later tasks

The ledger has the full list. These are the load-bearing ones.

- **T8–T11 (faults):** `take_units` with a variance doesn't lower `true_weight_kg`, so the true unit weight drifts.
- **T10:** the arm pauses downstream of a jam; the CLEAR_JAM job.
- **T11:**
  - The Task 11 planner computes the TAKEOFF altitude as level height + 0.5.
  - A drone whose task fails mid-flight stays in the air, idle. It must land or return.
- **T12:**
  - WAIT_CLEAR has no timeout, so orders must handle a wait that never clears.
  - Truck departures call `ship()`.
- **T14:** `task_manager._zone_snapshot` still counts SHIPPED boxes as zone occupants.
- **Plan 1c** (also see plan 1a's "To plan 1c" list):
  - `load_state` restores neither `twin.stock` nor `twin.equipment`.
  - Hand-off ids use a private `_handoff_seq`, which save/load must restore.
  - The sorter drop loop and `take()` lack `find_box` guards.
  - `equipment.tick()` errors aren't contained.
  - `policies.example.yaml` doesn't list the seven new risks.
  - The recalled-firmware Conflict on AST-000202.
  - Heartbeat `touch_remote_reports`.
  - Save/load v2: `wait_reason`, `wait_started_tick`, layer, Operator scopes and `StockLocation`.
  - Arm spawning ignores its home zone.
  - An explicit spawn position may land on a crossing.
  - The `.bak` file gets overwritten.
  - `layout_name` coupling.
- **Minor, from Task 4:**
  - WAIT_CLEAR(CONVEYOR_OCCUPIED) on a cell off the line waits forever.
  - `tuple(params[...])` on a non-sequence raises `TypeError` into the controller-error path.
  - `simulator.py` is about 1430 lines; the step subsystem could become its own module.

## Standing constraints

- **Tests:** run `.venv/bin/python -m pytest -o addopts="" -q`. Only two failures are allowed, both pre-existing: `test_real_task_006_box_conflict_is_caught` and `test_idle_robot_with_a_low_battery_charges_itself`.
- **Commit trailer:** exactly `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- **Staging:**
  - Stage only the files a task lists.
  - Never stage `logs/tasks/task_00{1,2}.json`. The user's own app server (started from their terminal) and test runs rewrite them.
  - Never stage `.DS_Store`, `docs/.DS_Store` or `docs/handoffs/`.
- **No merges, pushes or PRs** without the user's explicit choice.
- **Lock order:** `twin.lock` → inventory store lock, never the reverse.
- **Layouts:**
  - `classic` must stay byte-identical; golden tests pin it.
  - `DigitalTwin()` defaults to classic.
  - Tests for the new floor pass `layout="distribution_center"`.

## Waiting on the user

- **How sub-project A lands.** `feature/fleet-inventory` is 36 commits ahead of `master`. The options:
  1. Merge it into master locally.
  2. Push it and open a PR.
  3. Keep it as-is.

  `feature/multi-embodiment-ops` is built on it, so A must land first, or both land together.

## Environment notes

- Node isn't installed, so the brainstorming visual companion can't start. Use inline SVG widgets instead.
- Usage limits interrupted subagents three times last session. To recover, re-dispatch the same prompt, or resume the agent with SendMessage. The ledger tells you where you were.
