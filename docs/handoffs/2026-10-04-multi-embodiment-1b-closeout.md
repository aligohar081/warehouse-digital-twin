# Handoff: plan 1b built — close-out remaining

**Date:** 2026-10-04
**Repo:** `physical_ai/warehouse-digital-twin`
**Branch:** `feature/multi-embodiment-ops`, built on `feature/fleet-inventory`.
**Status:**
- All 14 tasks of plan 1b are implemented, task-reviewed and committed.
- The final whole-branch review ran, and its single fix wave is committed. The fix-wave implementer reported HEAD as **`d5d78cd`**. The controller has **not yet verified the fix wave**: not the suite, not the trailers, not the staging.
- The scoped re-review of the fix wave has **not** run.

Earlier handoffs:
- `docs/handoffs/2026-10-02-multi-embodiment-1b-execution.md`, which has the program goal, the sub-project table (A–F) and the user's choices;
- `docs/handoffs/2026-09-30-multi-embodiment-brainstorm.md`.

## Key documents

All are committed except the workspace.

- **Spec (the binding authority):** `docs/superpowers/specs/2026-09-30-multi-embodiment-operations-design.md`, sections §0–§19.
- **Plan 1b:** `docs/superpowers/plans/2026-10-01-multi-embodiment-1b-jobs-rules-shift.md`. It is about 12k lines. Global Constraints are at lines 15–28.
- **Plan 1a:** `docs/superpowers/plans/2026-09-30-multi-embodiment-1a-floor-and-motion.md`. Its "To plan 1c" list starts around line 121.
- **SDD workspace (git-ignored; `git clean -fdx` deletes it):** `.superpowers/sdd/2026-10-01-multi-embodiment-1b-jobs-rules-shift/`
  - `progress.md` is **the ledger**: 266 lines, holding every dispatch, review, fix round, deferred minor and **all 30 `Ruling:` lines**.
  - `global-constraints.md`, `implementer-instructions.md`, `reviewer-instructions.md`, `rereview-instructions.md`, `final-reviewer-instructions.md`.
  - `task-N-brief.md` / `task-N-report.md` for N = 1–14.
    - Briefs 9, 11 and 14 start with prepended **Controller rulings**.
    - Brief 14 has two rulings.
  - `final-fix-brief.md`: the fix wave's requirements, with the final review's findings and the controller's rulings.
  - `final-fix-report.md`: the fix wave's report.
  - `review-*.diff`: review packages. The whole-branch package is `review-e73dd1a..9215b25.diff`.
  - `tools/`:
    - `sdd1b.sh`, the helper wrapper, described below;
    - `anchors.py`, the edit-block anchor checker. It is no longer needed now that all tasks are done.
    - `final-probes/`, the final reviewer's repro and soak scripts: `soak_probe.py`, `carry_probe.py`, `face_probe.py`, `block_probe.py`, `flat_fork_probe.py`, `estimate_probe.py`, `validate_probe.py` and others.

## What was built in plan 1b

| Task | What | Commits | Fix rounds |
|---|---|---|---|
| 1 | Eligibility rules, `FaultInjector` | `e73dd1a..d6b2f44` | 0 |
| 2 | Stock ledger (recorded vs true) | `d6b2f44..297cf2f` | 1 |
| 3 | Conveyor, sorter, hand-offs | `297cf2f..35ddb3a` | 1 |
| 4 | Job-step framework | `35ddb3a..0f6f0bf` | 1 |
| 5 | People rules; paired, escalating safety waits; `model_code` | `0f6f0bf..0b1e79e` | 1 (deadlock sidestep stays out of occupied zones) |
| 6 | §5.5 energy, mains arms, pad charging | `0b1e79e..efea76b` | 1 (moving rate billed every driving tick; hovering-drone pad) |
| 7 | Capability selection, physical gate, `jobs.py` JobSpec | `efea76b..ec15bac` | 1 (AUTO validation order; reach for box jobs; log target) |
| 8 | Pallet jobs | `ec15bac..6f0c4d4` | 1 (floor jobs never planned for a profile-less robot) |
| 9 | Tote jobs (+ RETURNS_PUTAWAY lift ruling) | `6f0c4d4..08d02c8` | 0 |
| 10 | Station jobs, `human_jobs.py` | `08d02c8..43e3886` | 1 (walking people not offered jobs; walk out of fenced cells; containment) |
| 11 | Drone counts, patrols, drone energy gate (+ fly-home ruling) | `43e3886..22f3826` | 1 (airborne count lands on a free pad) |
| 12 | Shift engine, order chains | `22f3826..8dbb808` | pre-review fix + 1 (tote retries, PICK/PACK remainder, pause, containment) |
| 13 | People's activities, breaks, duty | `8dbb808..a34c29a` | 0 |
| 14 | 10 eval checks + fixtures (+ `placement_level_correct`, SHIPPED snapshot rulings) | `a34c29a..9215b25` | 1 (SHIPPED exempt from `state_transition` listing) |

At `9215b25` the controller ran the suite itself and got **2 failed, 717 passed**. Both failures are the allowed pre-existing ones.

### Final review and fix wave

The final review ran on opus over `e73dd1a..9215b25`. Its verdict was **"With fixes"**: three Critical integration seams that wedge a fault-free shift within minutes.
- **C1:** a robot whose job ends early keeps the box it is carrying forever.
- **C2:** plan-time targets treat other robots as walls, so a robot in a one-lane aisle fails the job.
- **C3:** a no-path traffic wait never escalates, and idle robots loiter on faces and drops.

It also raised I1 (the battery estimate under-prices loaded legs, lifts and handling), I2 (an order holds its station while waiting on a tote), and minors M1, M2 and M5.

The fix wave ran on opus from BASE `9215b25`. The implementer reported 7 commits, each ending in the exact trailer:
- `87eaa39`: a job that ends early sets its load down, and a failed order sends its totes home (C1, M1).
- `52cfb62`: plan-time job targets on the new floor ignore where other robots stand (C2).
- `605f7ff`: no-route waits escalate, and idle robots move off cells jobs need (C3).
- `052da98`: the battery estimate prices loaded legs, lifts and handling (I1).
- `0e2fdbf`: an order claims its station and pack cell only once its first tote is free (I2, M5).
- `6343cb4`: fleet reasons name the job's class first, validation can't hang, and orders are isolated (M2, plus the Task 10 and Task 12 notes).
- `d5d78cd`: a test that a fault-free shift keeps completing customer orders (40 sim-min, about 7 s, deterministic).

The implementer reported **2 failed, 759 passed**, with 42 new tests in `backend/test_integration_hardening.py` and `backend/test_shift_soak.py`.

`soak_probe.py` (20k ticks, pace 2) before and after the fix wave, as the implementer reported it:

| Metric | Before | After |
|---|---|---|
| Customer orders DONE / OPEN / FAILED | 1 / 42 / 5 | 20 / 28 / 0 |
| Idle robots carrying a box | TR50-201 | none |
| Robots in ERROR | 0 | 0 |

Over a 150-sim-min run, robots in ERROR went from 3 to 0.

**Implementer concerns to adjudicate in the re-review:**
- (a) It went beyond the ruling text in two places, and both need a controller view:
  - a failed tote leg sends its tote home before the retry;
  - a cell another robot is heading for counts as "a cell jobs need".
- (b) It added two refinements:
  - only the box the ending job itself picked up is set down;
  - idle robots park only while another robot has work.
- (c) 28 customer orders are still OPEN at 50 sim-min. The implementer attributes this to capacity (AMRs charging, the humanoid waiting for its supervisor) rather than to anything stuck. This is a 1c question.
- (d) A box set down on a walkway crossing can't be fetched back. It never happened in a soak.
- (e) The soak probe's check-fail count is inflated by the 5000-event in-memory buffer.

## How to resume

These are the remaining steps of the SDD skill (`superpowers:subagent-driven-development`).

1. **Verify the fix wave** before trusting it. From the repo root:
   ```bash
   git log --oneline 9215b25..HEAD
   ```
   ```bash
   for c in $(git rev-list 9215b25..HEAD); do git log -1 --format='%h %s' $c; git log -1 --format=%B $c | grep -c '^Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>$'; done
   ```
   ```bash
   git status --short
   ```
   Only `logs/tasks/task_00{1,2}.json`, `.DS_Store`, `docs/.DS_Store` and `docs/handoffs/` may show.
   ```bash
   .venv/bin/python -m pytest -o addopts="" -q | tail -3
   ```
   Expect `2 failed, 759 passed`.
2. **Do one scoped re-review of the fix wave.** Package it with `tools/sdd1b.sh pkg 9215b25 d5d78cd`. Dispatch a reviewer (opus recommended; the fix wave is large and cross-cutting) on `rereview-instructions.md` with these values:
   - [BRIEF_FILE] = `final-fix-brief.md`;
   - [REPORT_FILE] = `final-fix-report.md`;
   - [FIX_BASE_SHA] = 9215b25;
   - [HEAD_SHA] = d5d78cd;
   - [DIFF_FILE] = the printed package;
   - [FINDINGS] = C1, C2, C3 (a and b), I1, I2, M1, M2, M5, the Task 10 `create_task` guard, the Task 12 per-order isolation, and the regression test, all as listed in `final-fix-brief.md` with their rulings.

   Ask it to judge concerns (a) and (b) above.
3. **Adjudicate the residuals.** There is **no second fix wave**. Park each open finding with a `Ruling:` line in the ledger, or rule on the load-bearing ones. Residual load-bearing items go to the user in step 5.
4. **Give the user the full "Rulings I made" list.** Collect every ledger line containing `Ruling:` (30 so far), in order, each with what it costs if wrong. The list must be exhaustive.
5. **Then delete the workspace** (`rm -rf .superpowers/sdd/2026-10-01-multi-embodiment-1b-jobs-rules-shift`) and use `superpowers:finishing-a-development-branch`. No merge, push or PR without the user's explicit choice.
6. **Then write plan 1c** with `superpowers:writing-plans`, against the real code (rolling wave). Its inputs:
   - plan 1a's "To plan 1c" list;
   - every ledger line marked `→ 1c` or `→ 1c soak`;
   - the deferred rulings below.

   Read the ledger before deleting the workspace, or copy the `→ 1c` lines into plan 1c first.

**Subagent IDs from this session don't carry over.** Any fix round now needs a fresh agent, given the brief path and the report path. The report file is the persistent memory.

### `tools/sdd1b.sh`

```bash
tools/sdd1b.sh pkg BASE HEAD   # writes review-BASE..HEAD.diff into the workspace
tools/sdd1b.sh brief N         # extracts task N's brief (all already extracted)
tools/sdd1b.sh log "- line"    # appends a line to progress.md
```

It points at the skill scripts under `~/Library/Application Support/Claude/local-agent-mode-sessions/.../skills/subagent-driven-development/scripts`. If that path moved, edit `S=` to the installed skill's `scripts/` directory. Run it from anywhere: it `cd`s to the repo. Call it with its full path, `.superpowers/sdd/2026-10-01-multi-embodiment-1b-jobs-rules-shift/tools/sdd1b.sh`.

## Rulings that matter beyond 1b

The ledger has them all. These change behaviour or defer work:

- **Spec amendments, already committed in the spec:**
  - RETURNS_PUTAWAY lifts to the slot level (§9.2);
  - the 10th check, `placement_level_correct` (§10.4).
- **An idle airborne drone flies home** (`_auto_charge`), whatever its battery.
- **No order-level stall timeout in 1b.** The final reviewer judged this OK once C1's tote recovery landed. Plan 1c's soak decides.
- **The drone energy "dead zone"** (a drone idle between 20% and the mission energy plus the 25% reserve fails the count gate) is **deferred to 1c**. The recommendation is dock charging: a drone idle on its pad charges whenever it isn't full, and a charging drone that passes the gate is still selectable. The final reviewer agreed.
- **C3 idle parking (the spec is silent):** idle new-floor ground robots on cells jobs need move to `parking_area`. This is a policy 1c may tune.
- **A CHARGE_ROBOT for an unsupervised humanoid is rejected at the gate.** Going to charge counts as moving.
- **Classic `options()` and `TASK_TYPE_GUIDE` list the new job types.** Classic rejects them at validation.

**Notable 1c carry items:**
- order-backlog capacity;
- the order book scanned every tick, O(open × total);
- a FAILED CLEAR_JAM every 20 ticks while nobody qualified is on shift;
- the step-aside hold timing and Jordan's reactive follow;
- `policies.example.yaml` lacks the new risks and CLEAR_JAM;
- save/load v2: stock, equipment, `_handoff_seq`, `wait_*`, layer, scopes, StockLocation;
- the agent tool lacks the new jobs' fields (M3);
- GRASP_FAIL and CONVEYOR_JAM have no dedicated eval check;
- technicians' "work orders";
- `physical_qty` for a misplaced box with a variance (M4).

## Standing constraints

- **Tests:**
  - Run `.venv/bin/python -m pytest -o addopts="" -q`.
  - Only two failures are allowed: `test_real_task_006_box_conflict_is_caught` and `test_idle_robot_with_a_low_battery_charges_itself`.
  - Never edit an existing test file; new tests go in new files.
- **Commit trailer:** exactly `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- **Staging:**
  - Stage only your files.
  - Never stage `logs/tasks/task_00{1,2}.json` (the user's app server and test runs rewrite them), `.DS_Store`, `docs/.DS_Store` or `docs/handoffs/`.
- **No merges, pushes or PRs** without the user's explicit choice.
- **Lock order:** `twin.lock` → inventory store lock.
- **Layouts:**
  - `classic` must stay byte-identical.
  - `DigitalTwin()` defaults to classic.
  - New-floor tests pass `layout="distribution_center"`.

## Waiting on the user

- **How sub-project A lands.** `feature/fleet-inventory` is 36 commits ahead of `master`. The options are:
  1. merge locally;
  2. push and open a PR;
  3. keep it as is.

  `feature/multi-embodiment-ops` is built on it, so A lands first or both land together.

## Environment notes

- Usage limits interrupted subagents twice this session: the Task 11 fix round and the final fix wave. Both were resumed with no loss. In a new chat, re-dispatch a fresh agent with the brief and report paths, and check `git status` and `git log` first.
- Node isn't installed. Use inline SVG widgets for visuals.
