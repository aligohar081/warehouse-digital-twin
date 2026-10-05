# Final-review fix wave: plan 1b

This brief is your requirements. A final whole-branch review of plan 1b (e73dd1a..9215b25) found seams between tasks. Each task was sound alone, but together they wedge a fault-free shift within minutes. The reviewer's probe scripts and soak outputs are in `/private/tmp/claude-501/-Users-bakarmac-projects-physical-ai/13b93541-26bf-46bc-940a-1dd54a63ace9/scratchpad/final/` (`soak_probe.py`, `carry_probe.py`, `face_probe.py`, `block_probe.py`, `flat_fork_probe.py`, `estimate_probe.py`, `validate_probe.py`, `soak_*.json`). Read and run them, because they are your repros. Don't commit them.

Each finding below gives the reviewer's facts, then the **controller ruling**, which is binding. Fix everything listed in this one wave. Don't fix anything not listed.

---

## C1 (Critical): a robot whose job ends early while it holds a box keeps the box forever

**Reviewer's facts:**
- `task_manager.py:1638` (`fail_task`) and `:1669` (`cancel_task`) clear the robot's `current_task` but not `carrying_box`, and the box stays CARRIED.
- `simulator.py:1633` `_auto_charge` skips carrying robots, so the robot drains to 0.
- Selection still offers the robot work. A GRASP job then fails with "already holding" (`simulator.py:1166`), and a PICK job (UNLOAD, PUTAWAY, LOAD, RETURN_TOTE) silently overwrites `carrying_box` and orphans the first box (`:759`).
- `OrderBook._fail` (`operations/orders.py:283-298`) cancels every live stage of a failed order, so this happens routinely.
- Repro (`carry_probe.py`): cancel a TOTE_TO_STATION mid-carry. The AMR stays IDLE holding the tote and does not charge at 15%, and the next tote job fails "GRASP failed: it is already holding box_001".
- In soaks:
  - TR50-201 was stranded and caused 3 customer-order failures.
  - Arm CX10-211 held an item after a cancelled PACK_ORDER and killed pack_cell_2.
  - H1-212 went flat holding TOTE-0 after six RETURNS_PUTAWAY jobs.
- Second path: a customer order that fails while its tote is at a station never sends the tote home (TOTE-2 DELIVERED at (19,16)). The SKU's only tote is then never "home" again, so `_choose_tote` returns None, and the first try waits forever (`orders.py:233-237`) while it holds a pick station and a pack cell. At 50 sim-min, 42 customer orders were OPEN and 1 was DONE.

**Controller ruling:**
- On the new floor (`robot.mobility is not None`), a job that ends FAILED or CANCELLED while its robot carries a box sets the box down at the robot's cell. Mirror what `reset_robot` already does, so box status, position and ledger stay consistent.
- An arm holding an order item puts it back on its working cell if that cell is free. Otherwise the item is marked FAILED with a plain reason.
- `OrderBook._fail` queues a RETURN_TOTE (`create_task(internal=True)`) for every tote the failed order took out and didn't return. A tote that is already home, or already being returned, is skipped.
- Classic robots behave exactly as now.
- Because the robot no longer holds a box once its job ends, `_auto_charge` and selection can treat it normally. No extra backstop is needed.

## C2 (Critical): a robot in the aisle at plan time makes tote and pallet jobs fail outright

**Reviewer's facts:**
- `jobs.py:121-125` (`face_cell`), `task_planner.py:154` (`blocked = other_robot_cells`) and `task_manager.py:1442` (`assign` fails the task on PlanningError) combine like this:
  - Tote slots have a single face on a one-cell-wide aisle; TS-11-12-0's only face is (11,13).
  - Any robot in that aisle when the job is planned raises "No face of slot … is reachable", and the task fails instead of waiting.
  - The order retries once 10 s later, then FAILS and cancels its live carries, which feeds C1.
- Repro (`face_probe.py`): the first failure is at tick 1391 with TR50-201 at (14,13). The path exists with robots ignored and doesn't exist with them blocked.

**Controller ruling:**
- On the new floor, plan-time target resolution (slot faces, station drops, staging and dock drop cells) ignores other robots' current cells. Only the layout, clearance, layer and no-fly count.
- Execution already waits, replans and sidesteps around robots.
- A target the layout truly can't reach still fails with its reason.
- Classic planning, including its `blocked` handling, stays exactly as now.

## C3 (Critical): a no-path traffic wait never ends, and idle robots never move off busy cells

**Reviewer's facts:**
- In `simulator.py:393-430` (`_plan_path`), when no route exists with robots blocked, it sets WAITING/BLOCKED and returns. It never counts wait ticks, so `_break_deadlock` never runs.
- Repro (`block_probe.py`, pace 1): PF1200-205 at (8,7) heads to (4,7) while PF1200-206 at (4,7) heads to (8,7). Both are stuck from tick ~16.5k until both run flat at ~50k (`flat_fork_probe.py`), one holding a pallet. The inbound stream is wedged from ~41 sim-min.
- This code predates plan 1b, but 1a's single-lane aisles plus 1b's jobs make it reachable.
- The Task 5 deferred minor is related: a boxed-in robot re-emits COLLISION_AVOIDED/ROBOT_WAITING each cycle because `_break_deadlock` resets `wait_ticks` to 0.

**Controller ruling (the spec is silent on idle parking):**
- **(a) Escalating no-path waits.** A no-path traffic wait counts its ticks the same way a blocked-by-robot wait does, and escalates:
  - first, a sidestep via `_break_deadlock` against the robot holding the goal cell or the cut;
  - if that isn't possible, a replan, or an alternative free goal cell in the same target zone where the job allows one.

  A head-on swap in a one-lane aisle must resolve. Keep the Task 5 rule: a forklift or hauler never sidesteps into an occupied zone. Fold the boxed-in re-emit loop into this fix, so a robot that can't sidestep doesn't re-emit COLLISION_AVOIDED/ROBOT_WAITING every cycle.
- **(b) Idle parking.** An idle new-floor ground robot is one with no task and not charging, and is not an arm or a drone. Once it has stood `SHIFT_CHECK_EVERY_TICKS` on a cell that jobs need, it takes an internal move to `parking_area`. Cells jobs need are a slot face, a station tote drop or work cell, or any cell of a one-lane aisle; pick a clear predicate from the warehouse data and state it.
  - If parking is full or unreachable, it tries again later, with no task flood. Use the same throttling idea as `_charge_retry`.
  - Robots on chargers, drones, arms and the classic floor are unchanged.

## I1 (Important): the new-floor `estimate_battery` underestimates

**Reviewer's facts:** `task_planner.py:121-147` omits the loaded leg (×1.4 at loaded speed), lift energy and handling time. Measured:

| Put-away | Estimate | Actual |
|---|---|---|
| 1100 kg pallet to PR-08-02-4 | 0.50% | 2.76% |
| 1100 kg pallet to PR-16-05-4 | 0.84% | 3.45% |

**Controller ruling:**
- The new-floor estimate includes:
  - the loaded legs, priced at the loaded rate and the loaded speed;
  - lift energy for LIFT_TO steps, using Task 6's formula with load plus carriage, and the declared weight as the planner knows it;
  - handling-step time at the idle rate.
- Use the `energy` module's functions rather than re-deriving them.
- Classic estimates are unchanged.
- Add a test that a heavy high put-away's estimate is within a stated tolerance of the measured drain.

## I2 (Important): a first-try TOTE_TO_STATION waits forever while holding a pick station and pack cell

**Reviewer's facts:** `orders.py:233-237` and `:418-435`. A customer order claims its pick station and pack cell, then waits on line 1's tote with no clearer.

**Controller ruling:** a customer order claims its pick station and pack cell only once line 1's tote can be secured. Until then it stays OPEN and holds nothing.

## Folded minors (fix these too)

- **M1:** `task_manager.py:1676,1689` record order-engine cancels as "cancelled by user" with LogCategory.USER. Internal cancels (from the order book) must be attributed to the system with a plain reason, such as `cancelled: order ORD-0007 failed`. User cancels stay as they are.
- **M2:** `_fleet_reason` (`task_manager.py:1265`) shows the first 3 robots in insertion order, which truncates the real cause; drones' energy reasons get cut. List robots of the job's required class first.
- **M5:** the `_busy` docstring (`orders.py:402-405`) says the station is held until the tote is "on its way home", but the code holds it until RETURN_TOTE is done. Make the docstring match the code after your I2 change.
- **Task 10 note:** `create_task` registers a task before `validate`, so an exception inside `validate` leaves a non-terminal VALIDATING task. Wrap it so an exception during validation fails the task with a plain reason. Repro: a malformed `slot` list (`validate_probe.py`).
- **Task 12 note:** `OrderBook.advance` has no per-order isolation. Make an exception in one order fail that order with a plain reason, without starving the others.

## The regression test (required)

Add a fault-free shift regression test.
- Seed 42, all risks 0.0, on a populated `distribution_center` floor: the robots and people the soak probe uses, built through the public API the way `soak_probe.py` does.
- Run the shift long enough to prove the floor doesn't wedge. Aim for at least 30 sim-min; choose pace and duration so the test stays under ~15 s wall-clock.
- Assert:
  - customer orders keep completing: more are DONE in the last third of the run than at its start, and at least N in total (choose N from your green run);
  - at the end, no robot is idle while carrying a box;
  - no robot is in ERROR;
  - no task sits in VALIDATING.
- It must be deterministic.

## Process

- Read `implementer-instructions.md` and `global-constraints.md` in this workspace and follow them. That means TDD, staging only your files, the exact trailer, no edits to existing test files, and the classic floor unchanged.
- **Tests go in NEW files only:**
  - `backend/test_integration_hardening.py` for C1, C2, C3, I1, I2 and the minors;
  - `backend/test_shift_soak.py` for the regression test.

  Each test file gets a module docstring.
- Write a RED test for each finding first, using the probes as repros, and show the RED output.
- The suite is now `2 failed, 717 passed`. The only allowed failures are the 2 pre-existing ones. Report the final count.
- Commit in logical commits, for example one per finding group. Each commit message ends with the exact trailer.
- After your last commit, re-run the reviewer's `soak_probe.py` at its own settings and report the before/after numbers: orders DONE/OPEN/FAILED, robots in ERROR, idle robots carrying.
