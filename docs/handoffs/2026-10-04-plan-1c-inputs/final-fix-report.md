# Final fix wave: plan 1b (report)

**Status:** DONE_WITH_CONCERNS (the concerns are listed at the end; none blocks).
**Branch:** `feature/multi-embodiment-ops`, base 9215b25, 7 commits.
**Suite:** `2 failed, 759 passed`. The two failures are the pre-existing ones
(`test_real_task_006_box_conflict_is_caught`, `test_idle_robot_with_a_low_battery_charges_itself`).
The run adds 42 tests: 41 in `backend/test_integration_hardening.py` and 1 in `backend/test_shift_soak.py`.

## Commits

| SHA | Subject | Findings |
|---|---|---|
| 87eaa39 | fix: a job that ends early sets its load down, and a failed order sends its totes home | C1, M1 |
| 52cfb62 | fix: plan-time job targets on the new floor ignore where other robots stand | C2 |
| 605f7ff | fix: no-route traffic waits escalate, and idle robots move off cells jobs need | C3 (a)(b), the C2 aisle test |
| 052da98 | fix: the new-floor battery estimate prices loaded legs, lifts and handling | I1 |
| 0e2fdbf | fix: a customer order claims its station and pack cell only once its first tote is free | I2, M5 |
| 6343cb4 | fix: fleet reasons name the job's own class first, validation can't hang, orders are isolated | M2, Task 10 note, Task 12 note |
| d5d78cd | test: a fault-free shift keeps completing customer orders | regression test |

- Every commit message ends with exactly `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. I checked this with `git log --format=%(trailers)`.
- Each commit was built as its own tree state. Its focused tests and the full suite were run on that state (always 2 failed, the pre-existing two) before it was committed.
- Only my files were staged. `logs/tasks/*.json`, `.DS_Store`, `docs/.DS_Store`, `docs/handoffs/` and the probes were not staged.

**Order note.** C2 is committed before C3, so the C2 commit carries only its two guard tests. Its RED test, `test_robots_in_the_aisle_do_not_fail_a_tote_job_at_plan_time`, needs C3(b) parking as well: the job waits instead of failing (C2), then completes once the idle robots in the aisle go to parking (C3). That test therefore lands in the C3 commit.

**Files changed:**
- `backend/task_manager.py`
- `backend/operations/orders.py`
- `backend/task_planner.py`
- `backend/simulator.py`
- `backend/warehouse.py`
- new: `backend/test_integration_hardening.py`, `backend/test_shift_soak.py`

No existing test file was edited.

## How RED was shown

RED was shown two ways:
1. **While working.** Each test was run before its fix (`scratchpad/fixwave/red_all.txt`).
2. **Against the baseline.** After the last commit, I extracted the base tree with `git archive 9215b25` into the scratchpad, without touching git state. I then ran the final test files against it:

```
$ cd scratchpad/fixwave/baseline_tree && .venv/bin/python -m pytest -o addopts="" -q -rA \
      backend/test_integration_hardening.py backend/test_shift_soak.py
35 failed, 7 passed in 10.51s
```

The 7 that pass at the baseline are deliberate guards for behaviour that must not change:
- the classic robot keeps its box;
- a face the layout can't reach still fails with its reason;
- classic planning still steers around robots;
- with no job on the floor, an idle robot stays put;
- robots off the cells jobs need stay put;
- classic robots never go to parking;
- the classic estimate is unchanged.

GREEN after the last commit:

```
$ .venv/bin/python -m pytest -o addopts="" -q backend/test_integration_hardening.py backend/test_shift_soak.py
42 passed in 7.93s
$ .venv/bin/python -m pytest -o addopts="" -q | tail -1
2 failed, 759 passed in 13.11s
```

---

## C1: a job that ends early leaves nothing in its robot's hands

**What changed**

`TaskManager._set_down_load(task, robot)` is called by `fail_task` and `cancel_task` when they clear the robot's `current_task`. It runs before `_release_boxes` and before the TASK_FAILED / TASK_CANCELLED snapshot.

On the new floor (`robot.mobility is not None`):
- **A mobile robot** sets the box down on its own cell, mirroring `reset_robot`: the box becomes STORED at the robot's position with `assigned_robot` and `assigned_task` cleared, and `robot.carrying_box` becomes None.
  - A tote keeps its home slot, both in the ledger and in `box.slot`, as it does while it is away at a station.
  - A retrieved pallet was already released from the ledger, with `slot` cleared, by `goods.release`, so it is a plain floor pallet.
- **An arm** puts the item back on its working conveyor cell with `equipment.place(..., stop_at=cell)`, so the item is held there for a retry. If the order failed, `OrderBook._fail` then calls `release_order` after the cancels and the item rides off to the sorter, as before. If that cell is taken, the item is marked FAILED and a plain warning is logged: "ITEM-1 failed: CX10-210 let go of it when task_x ended, and its conveyor cell was taken".
- **Classic robots** are unchanged.

`OrderBook._fail`:
- cancels live stages with `cancel_task(task.id, reason="order ORD-nnnn failed")` (see M1);
- leaves a live **RETURN_TOTE** running, because it is already taking a tote home;
- after ORDER_FAILED, calls `_send_tote_home` for every line's `tote_id`. This queues `create_task({"type": "RETURN_TOTE"}, internal=True)` through the gate. A tote is skipped if it is home, or if a live job already has it (`active_task_for_box`, which covers "already being returned"). A gate rejection is logged with its reason.

**Readings I chose where the ruling left room** (all stay inside the ruling)
- **Refinement 1: only the job's own load is set down**, meaning the box whose `assigned_task` is this task. A real PICK or GRASP always sets that, so every real job's load is set down.
  - Without this, two existing hardening tests broke:
    - `test_job_steps_hardening::test_a_conveyor_placement_needs_a_cell_and_something_to_place`
    - `test_job_steps_hardening::test_an_item_is_packed_only_into_its_own_orders_carton`

    Both hand a robot an item outside any job, run a malformed step, and pin that the robot still holds it afterwards.
  - The ruling's "a job that ends ... while its robot carries a box" reads naturally as the box the job carries, so the tests are respected and nothing real is excluded.
- **A live RETURN_TOTE is not cancelled** when its order fails. The old code cancelled it and then needed a new one, after the robot had dropped the tote mid-aisle. This is what gives "already being returned" its meaning.
- **A failed TOTE_TO_STATION also sends its tote home** (`_stage_failed`), before the retry pops the line's `tote_id`. Without this, a tote set down by a failed leg whose order then retried with another tote would never be among "the totes the failed order took out", because its id is gone from the line. It uses the same `_send_tote_home`, so the same skip rules apply.
- **A robot picker holding a unit** when PICK_ITEMS ends is a mobile robot, so the unit (an ITEM) is set down at its work cell, as the ruling says. See the concerns.

**RED** (baseline):

```
test_a_cancelled_tote_job_sets_its_tote_down_where_the_robot_stands
E       AssertionError: assert ('box_001' is None)          # robot still carrying after the cancel
test_the_robot_takes_its_next_tote_job_instead_of_failing_it
E       AssertionError: TR50-201 GRASP failed: it is already holding box_001
test_a_robot_whose_job_was_cancelled_mid_carry_goes_to_charge_when_low
E       AssertionError: an idle robot with a low battery asks to charge
test_a_failed_job_sets_down_the_pallet_its_forklift_carried
E       AssertionError: assert 'box_001' is None
test_an_arm_whose_pack_ends_early_puts_the_item_back_on_its_cell
E       AssertionError: assert 'box_001' is None
test_an_arm_whose_cell_is_taken_fails_the_item_it_held
E       AssertionError: assert ('box_001' is None)
test_a_failed_customer_order_sends_its_tote_home_from_the_station
E       assert (0 == 1)                                     # no RETURN_TOTE queued
test_a_failed_customer_order_returns_a_tote_set_down_mid_carry
E       AssertionError: assert ('box_002' is None)
test_a_failed_order_queues_no_return_for_a_tote_at_home_or_on_its_way
E           box_003 and not True                            # its own RETURN_TOTE was cancelled
```

**GREEN:** all of the tests above pass, as does the guard `test_a_classic_robot_keeps_its_box_when_its_job_is_cancelled`.

`carry_probe.py` after the fix:

```
after cancel: IDLE holding None | tote STORED (9, 13)
low battery, 60 ticks later: CHARGING task task_002 battery 15.29
next tote job: PLANNING robot None error: None        (waits for the charging robot; no GRASP failure)
```

Before the fix it printed `IDLE holding box_001`, `IDLE task None`, and `next tote job: FAILED ... GRASP failed: it is already holding box_001`.

## C2: a robot in the aisle at plan time no longer fails tote and pallet jobs

**What changed**

In `TaskPlanner.plan`, a JobSpec plan for a robot with a floor profile now gets `spec_blocked = set()` instead of `other_robot_cells`. So slot faces, station tote drops, and staging and dock drop cells are resolved from the layout alone; clearance, layer and no-fly still apply through `best_cell_in_zone` and the profile.
- A robot with no profile keeps `blocked`.
- **Reading:** the ruling lists the job targets (slot faces, station drops, staging and dock drop cells), and those all live in JobSpec plans. So I left these unchanged:
  - the older job types on the new floor;
  - the charger detour and CHARGE_ROBOT cells, which still avoid occupied chargers;
  - the drone pad, which `charger_cell` and `_count_pad` choose against grounded robots, as Task 6 pinned.
- A target the layout truly can't reach still raises `No face of slot ... is reachable`, as the guard test shows.

**RED** (baseline):

```
test_robots_in_the_aisle_do_not_fail_a_tote_job_at_plan_time
E       AssertionError: No face of slot TS-11-12-0 is reachable
```

**GREEN:** the job plans to (11,13), waits, and completes once the idle robots in the aisle go to parking. `face_probe.py` no longer finds any `No face` failure in 5000 ticks; the probe then raises `AttributeError: 'NoneType' object has no attribute 'id'` at its print, because `hit` is None.

## C3 (a): a no-route traffic wait escalates

**What changed** (`simulator.py`, new-floor robots only; classic is untouched)

`_act_navigate` calls `_traffic_wait` when `_plan_path` fails on traffic, and resets `wait_ticks` when a route is found. `_traffic_wait` does the following:
1. **Counts the wait.** It increments `wait_ticks` and sets `blocked_by` to the robot holding the goal, or else the first robot on the route with robots ignored (`_route_blocker`).
2. **Escalates** every `DEADLOCK_WAIT_TICKS`, in this order:
   - **Make way.** If the blocker is itself held up by traffic (a NAVIGATE with `blocked_by` set and no safety wait), the robot of lower precedence makes way:
     - first a `_break_deadlock` sidestep, now with `avoid=`, to a neighbour off the blocker's route;
     - else a retreat: a `NAVIGATE "Make way for X"` inserted before the current one, to the nearest stoppable cell clear of the blocker's route. It is found by a breadth-first search through free cells, so it can cross the walkway when needed.
     - Forklifts and haulers never enter a zone with a person. The sidestep filter is kept, the breadth-first search skips such zones, and a retreat NAVIGATE obeys `_aisle_wait`.
     - If the lower robot has nowhere to go, the higher one makes way instead. This symmetric fallback avoids a dead-end wedge.
   - **Retarget.** Otherwise `_retarget` takes another free cell of the target zone, where the job allows one: a NAVIGATE followed by DELIVER or CHARGE into a STAGING, DOCK or CHARGING zone, or a MOVE_ROBOT to a named zone such as parking. Slot faces, tote drops, box pick-ups and coordinates stay exact.
   - Otherwise it keeps waiting and asks again next round.
   - A make-way NAVIGATE that itself can't route is dropped at its next escalation.

**Boxed-in re-emit:** `_handle_block` keeps a per-robot record of the (blocker, cell, task) it last announced. A new-floor robot still in the same wait doesn't announce COLLISION_AVOIDED / ROBOT_WAITING, or count `wait_events` / `collisions_avoided`, again. The record is cleared once the robot is unblocked. `_break_deadlock` still resets `wait_ticks` to 0 when it can't step, because two existing tests pin that. Classic still re-announces, as the floor constraint requires.

**RED** (baseline):

```
test_two_forklifts_swapping_cells_both_get_there             E  not reached within 1500 ticks
test_a_no_route_wait_counts_its_ticks_against_...            E  ... and None == 'robot_02'   (blocked_by never set)
test_a_head_on_swap_in_a_one_lane_aisle_resolves[x6]         E  not reached within 3000 ticks
test_when_the_robot_that_should_make_way_cannot_the_other_one_does   E  not reached within 3000 ticks
test_a_drop_cell_held_up_by_a_robot_is_swapped_for_...       E  not reached within 3000 ticks
test_a_boxed_in_robot_announces_its_wait_once                E  assert (10 == 1)      (10 COLLISION_AVOIDED in 200 ticks)
```

**GREEN:** all pass. The six head-on swaps (side by side, across the aisle, and beside the (17,13) crossing, each with either robot first) resolve in 161–204 ticks. Most use a sidestep; one uses the walkway retreat (`TR50-101 backs off to (18,13) to let TR50-201 pass`).

`block_probe.py` (pace 1, tick 20000) after the fix: both forklifts are IDLE and nothing waits on a no-route block. Before, they were deadlocked on each other's goal cells from about tick 16.5k. `flat_fork_probe.py` (52000 ticks): both forklifts end IDLE, with 0 ROBOT_ERROR and no battery-depleted forklifts. Before, both went flat around tick 50k.

## C3 (b): idle parking

**What changed**

`Simulator._park_idle()` runs each tick after `_auto_charge`. It sends an idle robot to parking when all of these hold:
- it is a ground robot on the new floor, not fixed (an arm), not airborne (a drone), with no task, no load, status IDLE, and not on a charger;
- it has stood `SHIFT_CHECK_EVERY_TICKS` on a cell jobs need;
- it is allowed to take MOVE_ROBOT.

The move is an internal `MOVE_ROBOT` to `parking_area`. It is throttled with `_park_retry`, the same idea as `_charge_retry`, and skipped while a named task is already queued for the robot.

**The predicate (stated):** `Warehouse.cells_jobs_need()`, computed once, is the union of:
- every slot's face cells, which covers both pallet aisles and all three tote aisles;
- every zone's `tote_drop` and `work_cell`;
- every one-lane cell: a drivable cell whose two neighbours along one axis are both undrivable for ground robots. That covers the tote aisles, the top aisle at y1, the x18 column and the walkway crossings.

Two further rules:
- A cell that another robot's current NAVIGATE is heading for counts too. Without this, a hauler idle on the staging cell holding the pallet a forklift must pick up blocks it until the next truck.
- A picker on a station's work cell stays put; that is where its own job wants it. Robots whose class can't take MOVE_ROBOT (the picker) are never sent.

**Refinement 2: parking only while other robots have work.** At least one other robot must hold a task.
- Without this, `test_energy.py::test_each_tick_drains_by_what_the_robot_did` broke: it pins that a lone idle AMR at (8,13) drains exactly the idle rate for 100 ticks.
- This stays inside the ruling: with no job on the floor, no job needs the cell. Throughout a shift the arms' PACK_ORDERs and the other jobs keep the floor "working", so parking always applies in practice.
- `test_with_no_job_on_the_floor_an_idle_robot_stays_where_it_is` pins this.

**RED** (baseline):

```
test_an_idle_robot_on_a_cell_jobs_need_moves_to_parking[tote drop|tote aisle|one-lane top aisle|pallet face]
E       assert (0 == 1)            # no MOVE_ROBOT
test_a_robot_that_cannot_park_asks_again_only_every_check
E       assert (1 <= 0)
```

**GREEN:** all pass. The robot parks, then stays put in parking. With all nine parking cells full, an AMR in the aisle asks at most 5 times in 100 ticks, each failing "Parking is not reachable right now". Robots on staging, the picker on its work cell, a drone, an arm and a stopped robot stay put, and classic robots never park.

## I1: the new-floor `estimate_battery`

**What changed**

`estimate_battery(robot, waypoints, handling_ops, actions=None, task=None)`. `plan()` now passes the actions. For a battery body, `_steps_wh` walks the actions as the simulator bills them, using the energy module's own functions:
- each NAVIGATE leg is priced with `energy.route_wh(profile, cells, loaded=carrying)`, which gives the loaded rate (×1.4) at the loaded speed after a PICK or GRASP;
- each LIFT_TO is priced with `energy.lift_wh(profile, declared_weight_of_load, Δh)`;
- PICK, GRASP, DELIVER, PLACE, PLACE_ON_CONVEYOR, LIFT_TO and LOWER each add their time plus one start tick at `ground_wh_per_tick(moving=False)`. PICK and DELIVER use the job type's timing, mirroring `_handling_ticks` (PICK_TICKS and DELIVER_TICKS for the older types).

Classic, and the no-actions call, are unchanged. A missing step timing counts as 0, because the step itself reports it at execution, so the planner never raises a non-PlanningError.

**Tolerance (stated):** the estimate without the reserve is within **5 %** of the measured drain.

**RED** (baseline):

```
[PR-08-02-0-300.0]   AssertionError: (0.5, 0.8779761904767298)
[PR-08-02-4-1100.0]  AssertionError: (0.5, 2.76292162698509)
[PR-16-05-4-1100.0]  AssertionError: (0.8399999999999999, 3.454588293652307)
```

**GREEN** (`estimate_probe.py`):

```
PR-08-02-0 300.0 COMPLETED estimate(no reserve)=0.87% actual=0.88% ticks=152
PR-08-02-4 1100.0 COMPLETED estimate(no reserve)=2.76% actual=2.76% ticks=328
PR-16-05-4 1100.0 COMPLETED estimate(no reserve)=3.46% actual=3.45% ticks=411
```

## I2: a first try no longer waits forever while holding a station

**What changed:** `_acquire` claims the pick station and pack cell only if `_first_tote_free(order)`, meaning line 1's `_choose_tote` returns a tote. Until then the order stays OPEN with `pick_station` and `pack_cell` None. **Reading:** if no tote holds enough of the SKU at all (OrderError), the order still claims, and the stage fails through its normal path: retried once, then the order fails with "no tote holds N of SKU". A wait that can never be met is treated as a failure, not a wait.

**RED:** `E  AssertionError: assert ('IN_PROGRESS...'pack_cell_1') == ('OPEN', None, None)`

**GREEN:** the waiting order holds nothing, and the order behind it gets `pick_station_1`.

## Folded minors

- **M1.** `cancel_task(task_id, reason=None)`. With a reason, the history entry is `Cancelled: <reason>`, the event is `task_x cancelled: order ORD-0001 failed`, and the category is `LogCategory.TASK`. A user cancel is exactly as before ("Cancelled by user", "cancelled by user", USER).
  - RED: `assert 'task_001 cancelled by user' == 'task_001 cancelled: order ORD-0001 failed'`. GREEN: passes.
- **M2.** `_fleet_reason` sorts robots of the job's required class first; the sort is stable and the order is unchanged for jobs with no classes.
  - RED: `No robot can do this CYCLE_COUNT: PF1200-205 is a FORKLIFT; ... (and 2 more)`.
  - GREEN: `No robot can do this CYCLE_COUNT: IX2-208 can't fly it: ...`. The soak's COUNT failures now show the drone's real energy reason.
- **M5.** The `_busy` docstring now reads: a station is held until every stage of every line, the last RETURN_TOTE included, is done.
- **Task 10 note.** An exception inside `validate()` fails the task with `The request could not be checked: <exc>`.
  - RED: `TypeError: cannot use 'list' as a dict key` raised out of `create_task`, leaving `task_001` VALIDATING.
  - GREEN (`validate_probe.py`): `ok PUTAWAY_PALLET FAILED The request could not be checked: ...`, `left behind: []`.
- **Task 12 note.** `OrderBook.advance` wraps each order. An order whose advancing raises fails alone with `The order hit an error: KeyError: 'sku'`, via `_fail`, with a hard FAILED fallback if `_fail` raises too.
  - RED: `KeyError: 'sku'` aborted `advance()`.
  - GREEN: the broken order FAILED, and the COUNT order behind it still made its attempt.

## The regression test (`backend/test_shift_soak.py`)

**Setup**
- The soak probe's floor, built through the public API: the 10 mobile robots, both arms, 6 totes, 3 pallets, and W01–W10.
- Seed 42. `COLLISION_RISK`, `FALSE_SUCCESS_RISK` and all seven fault risks are set to 0.0 with monkeypatch.
- Pace 2, **16000 ticks = 40 sim-minutes**, about 7.2 s of wall-clock time.

**Asserts**
- more customer orders are DONE at the end than at two thirds of the run;
- **at least 12** are DONE in total (the green run finishes 18, with 13 by 30 sim-minutes; the wedged baseline finishes 1);
- no robot is idle while carrying a box;
- no robot is in ERROR;
- no task is VALIDATING.

The run is deterministic: three runs gave identical counts (0/3/7/9/11/13/15/18 DONE at each 2000 ticks), and a zero risk draws no random numbers.

- RED (baseline): `AssertionError: no customer order completed in the last third (1 done)`.
- GREEN: `1 passed in 7.19s`.

## `soak_probe.py`: before and after, at its own settings (20000 ticks, pace 2, 50 sim-min)

**Orders by kind (DONE / OPEN / IN_PROGRESS / FAILED)**

| Kind | Before | After |
|---|---|---|
| CUSTOMER | **1** / 42 / 2 / 5 | **20** / 28 / 2 / **0** |
| INBOUND | 1 / 0 / 4 / 0 | 5 / 0 / 0 / 0 |
| PALLET | 2 / 0 / 5 / 0 | 7 / 0 / 0 / 0 |
| COUNT | 5 / 0 / 0 / 5 | 9 / 0 / 0 / 1 |
| RETURN | 1 / 0 / 9 / 0 | 7 / 0 / 3 / 0 |

**Robots, totes and failures**

| Measure | Before | After |
|---|---|---|
| Robots in ERROR | 0 (but 3 at 150 sim-min, see below) | 0 |
| Idle robots carrying | `['TR50-201']` | `[]` |
| Stranded totes | TOTE-1 CARRIED, TOTE-2 DELIVERED at (19,16) | none |
| Orders open > 15 min | 41 | 15 (all waiting for a station or a tote robot: capacity, not a wedge, see below) |
| Task failures | GRASP "already holding" ×6, "No face" ×5, CYCLE_COUNT ×10 | CYCLE_COUNT drone-energy ×2, parking unreachable ×1 |
| Mean tick | 0.62 ms | 0.51 ms |

**The longer run** (`soak_probe.py 60000 1.0`, 150 sim-min), compared with the reviewer's `soak_60k_p1.json`:

| Measure | Before | After |
|---|---|---|
| CUSTOMER DONE / FAILED | 7 / 6 | **43 / 14** |
| Robots in ERROR | PF1200-205, PF1200-206 and H1-212, all battery depleted | none |
| Idle robots carrying | PF1200-206, H1-212 | none |
| INBOUND / PALLET / COUNT DONE | 1 / 2 / 15 | 8 / 10 / 15 |

All 14 failures are "no tote holds N of SKU-00x": the six seeded totes run out of stock over 150 minutes, and nothing restocks them in 1b.

**The probe's `check_fails` count is misleading after the fix.** It rises to 228 ("The log is empty") only because `MAX_EVENTS_IN_MEMORY` is 5000 and the floor now does about 15 times more work, so the early tasks' events are evicted before the probe grades them. With the buffer raised in a scratch copy of the probe (`soak_probe_fullbuffer.py`), the 50-minute run has exactly 3 check FAILs. All are `terminal_state` for tasks that legitimately failed: 2 rejected CYCLE_COUNTs and 1 MOVE_ROBOT whose parking was full. The CI checks pass.

**Other probes**
- `carry_probe`, `estimate_probe`, `validate_probe` and `face_probe` are reported under their findings above.
- `flat_probe` (H1, 27000 ticks): H1 has no ROBOT_ERROR. It ends on a MOVE_ROBOT to parking at 59% battery.

## Concerns

1. **Throughput is now limited by capacity, not by wedges.** About 28 customer orders are still OPEN at 50 sim-min at pace 2 (twice the spec rate). The reasons are:
   - two AMRs charge at the same time in the second half (`ENERGY_TIME_SCALE` 10);
   - the humanoid stops and starts for SUPERVISOR_ABSENT while Jordan walks after it (traced: it is never wedged);
   - each order holds a whole pick station until its last tote is back.

   I2 lets an order whose SKU's only tote is busy be overtaken. One order waited 72 minutes in the 150-minute run: that is starvation, not a wedge. All of this is outside the findings, so I left it alone.
2. **A box set down on a walkway crossing.** If a job is cancelled while its robot is crossing the walkway, the box is set down on the crossing cell, as the ruling's "the robot's cell" says. RETURN_TOTE and RETURNS_PUTAWAY can't stop on a walkway cell, so that tote would stay stranded. This is rare in a fault-free shift (none in any soak), but possible.
3. **A picker's unit stays on the floor.** A PICK_ITEMS cancelled while the picker holds a unit leaves that ITEM STORED on the picker's work cell, per the ruling. It blocks nothing, but it is debris in `twin.boxes`.
4. **Retreats add actions.** A make-way retreat is an inserted NAVIGATE, so `task.actions` and its progress grow by one per retreat. Evaluation reads events, so this is cosmetic.
5. **Event volume.** A busy floor fills the 5000-event ring buffer quickly. Plan 1c's job logs or soak should not grade from the in-memory buffer alone.
6. **Two readings beyond the ruling's text** are deliberate and stated above:
   - a failed tote leg sends its tote home before the retry;
   - a cell another robot's NAVIGATE is heading for counts as a cell jobs need.

   Both close seams the soak showed, and both stay within the rulings' intent.
