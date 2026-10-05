# Final fix wave re-review: plan 1b (9215b25..d5d78cd)

I reviewed the diff in three passes: orders.py and task_manager.py (C1, I2, M1, M2, M5, Task 10, Task 12), then simulator.py, warehouse.py and task_planner.py (C2, C3, I1), then the two new test files. I wrote probes only under `tools/final-probes/rereview/`.

### Finding Verdicts

1. **C1: a job that ends FAILED/CANCELLED sets its load down; an arm puts its item back or fails it; `_fail` sends home every tote the order took out.** ADDRESSED.
   - `TaskManager._set_down_load` (task_manager.py:1647-1678) is called from `fail_task` (:1690) and `cancel_task` (:1726). Both calls come before `_release_boxes` and before the after-snapshot.
   - A mobile robot sets its load down STORED at its own cell (:1675-1676), the same way `reset_robot` does (digital_twin.py:793-800).
   - An arm puts its item back with `equipment.place(..., stop_at=cell)` when the cell is free (:1667-1669). Otherwise the item is marked FAILED with a plain warning (:1670-1674).
   - Classic robots return early (:1657).
   - `OrderBook._fail` cancels live stages with a reason. It leaves a live RETURN_TOTE running (orders.py:316). After ORDER_FAILED it calls `_send_tote_home` for every line's tote (orders.py:328-330).
   - `_send_tote_home` (orders.py:332-347) skips a tote that is home or that a job already holds.
   - No duplicate RETURN_TOTE can arise: a just-created task is PLANNING, and `active_task_for_box` counts PLANNING as active (task_manager.py:1337).
   - Covered by tests at test_integration_hardening.py:114-241.
   - Residual: a tote set down on a walkway crossing can never be sent home. See New Breakage #1.
2. **C2: plan-time target resolution on the new floor ignores other robots' cells.** ADDRESSED.
   - task_planner.py:346-347 passes `spec_blocked = set()` to every JobSpec plan for a robot with a floor profile. That covers slot faces through `face_cell`, drop cells through `drop_cell`, and the station tote drops.
   - Classic keeps `blocked`. The guard test is at test_integration_hardening.py:265.
   - A target the layout can't reach still raises its reason (:258).
   - The drone landing pad still avoids grounded robots, because `_count_pad` uses `other_robot_cells` itself (jobs.py:636).
   - Ruled out of scope by the implementer's reading: the older job types and the charger detour still use `blocked`. See Out-of-Scope #2.
3. **C3(a): a no-route traffic wait counts ticks and escalates.** ADDRESSED.
   - `_act_navigate` calls `_traffic_wait` when `_plan_path` fails on traffic (simulator.py:325-326). It resets `wait_ticks` once a route is found (:328-329).
   - `_traffic_wait` (:589-612) counts `wait_ticks`, sets `blocked_by` through `_route_blocker` (:614-626), and escalates every `DEADLOCK_WAIT_TICKS`:
     - first the make-way step: a `_break_deadlock` sidestep with `avoid=` the blocker's route, else a retreat NAVIGATE (:660-684, `_refuge` :686-706);
     - otherwise `_retarget` to another free cell of a STAGING, DOCK or CHARGING drop zone, or of a named MOVE_ROBOT zone (:708-749).
   - The Task 5 forklift/hauler zone rule is kept in `_sidestep_options` (:575-587) and in `_refuge` (:702).
   - The boxed-in re-announce loop is gone for the new floor: `_handle_block` remembers the last wait it announced (:497-503). Test at :350.
   - The head-on swap tests (:279-348) pass in the report.
   - My `headon_probe.py` added 7 variants and all completed, with no robot in ERROR: a pass-through swap, mid-flight meetings in tote aisles 1, 2 and 3, the top aisle, forklifts in pallet aisle 2, and three robots in one aisle. They took 88 to 341 ticks.
   - A stationary blocker (idle or working) never gets a sidestep. That is sensible, but it means an idle robot on a cut cell depends on C3(b). See finding 4.
4. **C3(b): idle new-floor ground robots on cells jobs need move to `parking_area` after `SHIFT_CHECK_EVERY_TICKS`.** NOT ADDRESSED. The predicate misses cells of a one-lane aisle that the ruling requires, so the original wedge survives at both pick stations.
   - The ruling says "any cell of a one-lane aisle". `Warehouse.cells_jobs_need` (warehouse.py:190-206) treats a cell as one-lane only when its two neighbours along one axis are both undrivable.
   - The layout's own `top_aisle` zone (clearance NARROW) holds (18,1)-(18,18). `grid_probe.py` shows that (18,13), (18,14), (18,15), (18,16) and (18,17) are not in the set. The side pockets (the tote drops) and the walkway crossings give those cells a drivable neighbour, so the geometric test fails.
   - The report says the predicate covers "the x18 column". That is false for those 5 cells.
   - `cut_probe.py` shows that (18,14) and (18,16) are AMR cut vertices: each is the only way to Pick 1's or Pick 2's tote drop and work cell. `neighbors((19,14))` returns only (20,14) and (18,14).
   - `x18_probe.py` reproduces the wedge. An idle AMR stands on (18,14) while another AMR's TOTE_TO_STATION to pick_station_1 is running. After 3000 ticks the job is still BLOCKED and its robot waits at (11,13) holding the tote, with `blocked_by` set to the idle robot. No MOVE_ROBOT is ever issued.
   - Nothing else frees the cell:
     - `_park_idle` adds only NAVIGATE targets to the set (simulator.py:1897, 1911-1919), not cut cells on a route;
     - `_should_yield` is False for a blocker that is not itself held by traffic (simulator.py:639-640);
     - `_retarget` won't move a STATION drop (:718-729).
   - The same gap applies to (18,17), and for forklifts to (7,2) in main_aisle and (19,5)-(22,5) in pallet_lane. Those are WIDE zones but single-file for a forklift.
   - Two possible remedies:
     - add every cell of a NARROW aisle zone, or every cut vertex of the drivable graph, to `cells_jobs_need`;
     - or have `_park_idle` treat an idle robot as needing to move when it is some waiting robot's `blocked_by`, which `_traffic_wait` already sets.
   - Frequency: the robot's job has to end on one of these cells (a cancel or failure mid-route), so this is rare. But once it happens the station is wedged with no recovery.
   - Everything else in C3(b) is implemented: the exclusions (:1880-1886), the CHECK dwell (:1887-1892), throttling through `_park_retry` and the queued-task check (:1893-1895, :1901), and the tests (:375-421).
5. **I1: new-floor `estimate_battery` prices loaded legs, lifts and handling.** ADDRESSED.
   - `_steps_wh` (task_planner.py:148-202) prices with `energy.route_wh(loaded=)`, `energy.lift_wh` (declared weight) and `ground_wh_per_tick(moving=False)`.
   - Handling ticks mirror `_handling_ticks` (simulator.py:1055-1064).
   - `plan()` passes `actions` (task_planner.py:354). Classic and calls without actions are unchanged (test :441).
   - The 5 % tolerance test is at :429.
   - My `estimate_more_probe.py` gave estimate/actual ratios between 0.946 and 1.000:

     | Job | Ratio |
     |---|---|
     | TOTE_TO_STATION (AMR) | 0.985 |
     | RETRIEVE_PALLET, level 4, 1100 kg | 1.000 |
     | RETRIEVE_PALLET, level 0 | 0.999 |
     | MOVE_ROBOT to parking | 0.946 |
6. **I2: a first-try TOTE_TO_STATION doesn't hold a station and pack cell while it waits.** ADDRESSED.
   - `_acquire` claims only when `_first_tote_free` returns True (orders.py:468-477, 492). Until then the order stays OPEN with `pick_station` and `pack_cell` both None.
   - An OrderError (no tote holds enough) still claims, so the stage fails through its normal path.
   - Test at :456.
7. **M1: order-engine cancels are attributed to the system; user cancels are unchanged.** ADDRESSED.
   - `cancel_task(task_id, reason=None)` (task_manager.py:1712).
   - With a reason, the history entry is "Cancelled: <reason>" (:1721) and the event is "<id> cancelled: <reason>" with category TASK (:1735-1736).
   - With no reason, both strings and the USER category are byte-identical to before.
   - `_fail` passes `why` (orders.py:318). The only other callers (app.py:472, agent_tools.py:201) are user cancels.
   - Test at :469.
8. **M2: `_fleet_reason` lists robots of the job's required class first.** ADDRESSED.
   - Stable sort on `embodiment_class in spec.classes` (task_manager.py:1250-1256).
   - The function only returns None or the joined reasons, so the order changes only the message.
   - Order is unchanged for types with no classes or not in `JOB_SPECS`. Test at :487.
9. **M5: the `_busy` docstring matches the code after the I2 change.** ADDRESSED.
   - The docstring at orders.py:451-455 says a station is held until every line's stages, including RETURN_TOTE, are done, and a pack cell until PACK_ORDER is done. That matches :460-463.
   - The module docstring is updated too (orders.py:37-48).
10. **Task 10 note: an exception during validation fails the task with a plain reason.** ADDRESSED.
    - task_manager.py:550-553 wraps `validate` and fails the task with "The request could not be checked: <exc>" via `fail_task`.
    - `validate` raises nothing on purpose; the unknown-type and unknown-priority ValueErrors come earlier and are unchanged.
    - Test at :498.
11. **Task 12 note: per-order isolation in `OrderBook.advance`.** ADDRESSED.
    - Each order is wrapped (orders.py:182-189). `_fail_broken` (:191-202) logs the error, then calls `_fail`, and falls back to a hard FAILED if `_fail` also raises.
    - Test at :506: the COUNT order behind the broken one still makes its attempt.
12. **Regression test: a fault-free shift keeps completing customer orders.** ADDRESSED.
    - test_shift_soak.py uses the same floor as `soak_probe.py`, built through the public API, and the default shift seed 42 (`ShiftEngine(seed=42)`, operations/shift.py:63).
    - All CONFIG risks are zeroed through monkeypatch. The run is 16000 ticks at pace 2 (40 sim-min, about 7.2 s).
    - It asserts: more orders DONE at the end than at two thirds of the run, at least 12 DONE, no idle robot carrying, no robot in ERROR, no task VALIDATING.
    - Determinism check: `soak_det_probe.py` under PYTHONHASHSEED=1 and PYTHONHASHSEED=999 gave the identical DONE trace [0, 3, 7, 9, 11, 13, 15, 18] every 2000 ticks, and identical end states.

### New Breakage in the Fix Diff

1. **Important: a load set down on a walkway crossing is unrecoverable, and a tote stranded there wedges every later order for its SKU.** `_set_down_load` sets the box down at whatever cell the robot is on (task_manager.py:1675-1676). The robot can be on a crossing such as (17,13), (17,15), (17,17), (17,10) or (17,11), and no robot may stop there.
   - `crossing_probe.py` failed a customer order while its AMR was carrying the tote across (17,13). The tote was set down STORED at (17,13).
   - `_send_tote_home`'s RETURN_TOTE was rejected at the gate: "No robot can do this RETURN_TOTE: TR50-201 has no route to the job; TR50-101 has no route to the job; …".
   - 2000 ticks later the tote was still not home, and a new SKU-002 order sat OPEN holding nothing: `_choose_tote` never sees the tote as home.
   - `crossing_probe_h1.py` adds H1-212 and the crew. The result is the same: "H1-212 has no route to the job".
   - Forklifts cross at (17,10) and (17,11), so a pallet can be stranded the same way.
   - This follows the ruling's literal "at the robot's cell", so it likely needs a ruling amendment. A fix would set the load down on the nearest cell the body may stop on (`may_stop`, as `_break_deadlock` already uses) instead of on a walkway cell.
   - The fault-free soak never hits it. It needs a tote leg to be cancelled or failed mid-crossing, which becomes plausible once faults are armed (plan 1c's soak). When it happens it is permanent and silent.
2. **Minor: a picker's unit becomes floor debris and the unit is lost from stock.**
   - When PICK_ITEMS ends early while the picker holds a unit, the unit is set down STORED at the picker's work cell (task_manager.py:1675-1676).
   - `goods.take_units` had already taken that unit out of the tote (simulator.py:1416-1418). Nothing returns it, and the retry picks a fresh unit, so the tote's recorded quantity drifts by one per event and the ITEM stays in `twin.boxes`.
   - This follows the ruling text; the report's concern 3 notes it.
3. **Minor: a park request can go stale.**
   - `_park_idle` creates a named internal MOVE_ROBOT (simulator.py:1908-1909).
   - AUTO selection only penalises a robot with a pending named task (task_manager.py:1411, `workload_penalty`); it doesn't exclude it.
   - So an AUTO job can take the robot first, and the PLANNING MOVE_ROBOT then runs after that job, from wherever the robot ended. This is benign: it takes an extra trip to parking.
4. **Minor (doc): a claim in the soak test's docstring is wrong.**
   - test_shift_soak.py:9 says "a zero risk never draws a random number".
   - `_act_deliver` draws `random.random()` for FALSE_SUCCESS_RISK on every delivery whatever the risk (simulator.py:1018).
   - Determinism still holds, because the draw uses the module-level RNG and its result is constant at risk 0, but the stated reason is wrong.

### Scope Decisions

- **(a1) A failed tote leg sends its tote home before the retry: SOUND.**
  - orders.py:293-295. It closes a real seam: a retry pops the line's `tote_id` (orders.py:360). Without (a1), a tote set down by a failed leg whose retry then fails would not be among "the totes the failed order took out" at `_fail` time, and would be stranded forever.
  - It doesn't change the order's outcome. In both cases the tote isn't home when the retry starts, so `_choose_tote` returns None and the retry fails at `attempts=1` (orders.py:256-259). (a1) only adds that the tote gets home.
  - No duplicate RETURN_TOTE, because a PLANNING task counts as active (task_manager.py:1337).
  - It sits beyond the ruling's text but inside its intent: the tote is one the order took out.
  - It inherits New Breakage #1: on a crossing, its RETURN_TOTE is rejected too.
- **(a2) A cell another robot's NAVIGATE heads for counts as a cell jobs need: SOUND WITH CAVEAT.**
  - simulator.py:1897, 1911-1919. It is needed because C2 now lets a job target a cell where an idle robot stands. Example: a forklift's PICK of the pallet a hauler just dropped on a staging cell. A PICK target can't be retargeted.
  - It sits beyond the ruling's text but inside its intent: that cell is literally one a job needs.
  - Caveats:
    - it covers targets only, not the cut cells on the way to a target. This is the finding-4 gap: (18,14) is never anyone's target.
    - it has no layer filter. This is harmless in practice, because a drone's hover target is a slot face that is already in the set (jobs.py:622-625).
    - a robot already parked whose cell is another robot's MOVE_ROBOT target is sent to parking again. That move is a no-op, and the mover's `_retarget` resolves within `DEADLOCK_WAIT_TICKS`.
- **(b1) Only the box the ending job itself picked up is set down: SOUND WITH CAVEAT.**
  - task_manager.py:1660-1661. Every real PICK and GRASP sets `box.assigned_task = task.id` (simulator.py:946-947, 1424), so every new-floor job's load is covered. The rule also keeps the two existing `test_job_steps_hardening` tests passing.
  - Caveat, verified with `b1_probe.py`: on the new floor, PICK_BOX followed by DELIVER_BOX leaves the box tagged with the completed PICK_BOX. A cancelled DELIVER_BOX then leaves the robot holding the box, CARRIED. That robot never auto-charges and never parks.
  - This matches PICK_BOX's own semantics: a robot idles holding the box after PICK_BOX completes. The order engine and shift never use those types, so the case is reachable only from the API or the agent.
- **(b2) Idle robots park only while another robot has work: SOUND WITH CAVEAT.**
  - simulator.py:1877, 1893. It departs from the ruling's literal text but stays within its intent: with no job on the floor, no job needs the cell.
  - It is forced by an existing test that may not be edited: `test_energy.py::test_each_tick_drains_by_what_the_robot_did`.
  - During a shift, PACK_ORDERs, charges and counts keep `working` non-empty.
  - Selection's `_reach` and JobSpec planning ignore robot cells (task_planner.py:346). So the first job to be assigned starts parking at once, because the idle timer has already run.
  - Residual risk: work that can't be assigned or planned while the idle robot stands where it is would leave nobody "working". I could not construct this for the job types the shift uses.

### Out-of-Scope Observations

1. **A customer order can wait about 30 minutes on a tote only the humanoid can reach.** `_choose_tote` (orders.py:424, unchanged) picks totes without checking which robots can reach them. In the 150-minute soak (`plateau_probe.py` and `stuck_return_probe.py`):
   - ORD-0099 holds pick_station_2 for about 30 minutes. Its RETURN_TOTE for Box-301, a returned tote put away at level 2, sits PLANNING while both AMRs idle in parking.
   - Both AMRs give the same reason: "can't work there: level 2 is out of its reach (highest level 1)".
   - Only H1-212 can do it, and SUPERVISOR_ABSENT waits slow it down.
   - This is behind most of the late plateau, where CUSTOMER DONE goes from 40 to 43 between about 110 and 150 sim-min.
2. **Older job types and the charger detour still resolve targets against `blocked` on the new floor** (task_planner.py:217, 356-358): PICK_AND_DELIVER or MOVE_ROBOT to a zone, and CHARGE_ROBOT. They can still fail at plan time because of traffic. The C2 ruling's list didn't name them, and the shift doesn't use them except for the charge detour.

### Checks Run

- All 7 commits end with exactly `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` (checked with `git log`).
- No existing test file changed (`git diff --stat` touches only the two new test files). app.py, frontend/ and README.md are untouched.
- `soak_det_probe.py 60000 1.0` gave CUSTOMER 43 DONE / 14 FAILED, with no robot in ERROR, no idle robot carrying and no task VALIDATING. This matches the report's 150-minute numbers.
- Classic paths are unchanged:
  - every new path is gated on `robot.mobility is not None`, on `"parking_area"` with a profile, or on a cancel `reason`;
  - the `_handle_block` reordering moves only a side-effect-free `set_status` (robot.py:140-144);
  - the `_break_deadlock` refactor keeps the same options when `avoid` is empty.

### Verdict

**Fix round:** Findings remain open. C3(b) is NOT ADDRESSED: the parking predicate omits the top_aisle's x=18 cells (18,13)-(18,17), including (18,14) and (18,16), the sole access to each pick station, and the station wedge reproduces there. There is also one new Important breakage: a load set down on a walkway crossing can never be returned (New Breakage #1).
