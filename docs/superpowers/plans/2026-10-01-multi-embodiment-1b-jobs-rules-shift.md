# Multi-Embodiment Plan 1b — Jobs, Rules, Conveyor and Shift Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the distribution-centre floor work: robots run timed job steps (lift, grasp, place, take off, land, scan) under the people rules and the shared physical trust rules, goods live in one stock ledger, the conveyor and sorter carry orders to the outbound docks with a hand-off record per transfer, and a seeded shift engine generates trucks, orders, counts, patrols and returns as job chains — while the classic floor and every existing test stay exactly as they are.

**Architecture:** New self-contained units — `backend/faults.py` (injected faults), `backend/equipment.py` (conveyor, sorter, hand-offs), `backend/energy.py` (the §5.5 Wh model), `backend/jobs.py` (the new job types: their specs, planners and targets), `backend/human_jobs.py` (jobs people do), `backend/operations/` (shift engine and orders) — plug into the existing twin, simulator, planner and task manager. Every new behaviour is keyed on a robot having a floor profile (`robot.mobility`, set only off the classic floor) or on the layout having the equipment, so a classic robot and a classic log never change.

**Tech Stack:** Python 3.14, Flask (untouched), PyYAML, pytest, the standard library.

**Spec:** `docs/superpowers/specs/2026-09-30-multi-embodiment-operations-design.md` — the binding authority. This plan covers §17 step 1, items 8–13: §5.4, §5.5, §6's job-step rules, §7 in the twin, §8 (sub-project E), §9, §10, and §11.1–§11.4 (the engine's Python API). Plan 1a (`docs/superpowers/plans/2026-09-30-multi-embodiment-1a-floor-and-motion.md`) built the floor, motion, people and goods this plan drives; every "To plan 1b" item of its **Carried forward** section is handled here (see **Carried forward from plan 1a**). Plan 1c covers items 14–17.

## Global Constraints

- Work on branch `feature/multi-embodiment-ops`. Commit at the end of every task. Never merge, rebase or push.
- Test command, from the repo root: `.venv/bin/python -m pytest -o addopts="" -q` (`pytest.ini`'s `-q` hides the summary unless addopts is overridden). Baseline before Task 1: `2 failed, 476 passed`.
- The only failures ever allowed are the two pre-existing ones: `backend/test_eval_engine.py::test_real_task_006_box_conflict_is_caught` and `backend/tests.py::test_idle_robot_with_a_low_battery_charges_itself`.
- Dependencies: Flask, PyYAML, pytest and the standard library only. No new dependencies.
- Do not edit any existing test file, including the test files earlier tasks of this plan create. New tests go in the new files each task names.
- The classic floor must not move: `Warehouse()`, `DigitalTwin()` and every existing fixture stay classic, with an unchanged grid, zones, seed, battery model (1% per cell, `BATTERY_PICK_COST`, `BATTERY_CHARGE_RATE`), wall-clock shifts, AUTO scoring, `PICK_TICKS`/`DELIVER_TICKS`, and event data. A robot with no floor profile (`robot.mobility is None`) behaves exactly as today.
- Out of scope (plan 1c): the new-floor twin seed, save/load v2, app boot, the operations HTTP API (including `POST /api/faults/<kind>` — this plan gives it `twin.faults.arm`), the dashboard, the soak test and docs. `backend/app.py`, `frontend/` and `README.md` are not touched.
- Lock order is `twin.lock` → inventory store lock. Inventory listeners run after commit, outside the store lock. The stock ledger, the equipment, the people helpers and the shift engine take no lock of their own: the simulator tick holds `twin.lock`, and plan 1c's API callers must too.
- Spec values used verbatim: `ENERGY_TIME_SCALE` 10; idle × 0.3, moving × 1.0, moving loaded × 1.4 of `capacity_wh / (runtime_h × 3600) × TICK_DT`; drone flight `capacity_wh / (flight_time_min × 60) × TICK_DT`; lift `m × g × Δh / 3600 / 0.6` Wh; charge `capacity_wh / (charge_time_h × 3600) × ENERGY_TIME_SCALE` Wh/s; drone reserve 25% of capacity; `SAFETY_WAIT_ESCALATE_S` 120; `CONVEYOR_SPEED_MPS` 0.5 (20 ticks a cell); sorter 2 s; `CLEAR_JAM` 30 s; `SHIFT_START_HOUR` 6; grasp retried up to 2 times; a failed stage retried once; variances of 2 units or fewer reconciled automatically; shift seed 42, pace 1.0; trucks every 20 sim-min alternating dock_1/dock_2 with 4–8 pallets; 30 customer orders/h of 1–4 lines × 1–3 units to dock_4 or dock_5; 4 pallet orders/h; a count every 10 sim-min round-robin over the 36 rack faces; patrols every 15 sim-min; 6 returns/h; departures from dock_3, dock_4, dock_5 every 30 sim-min; a 15-minute break every 2 sim-hours in `sw_floor`.
- Style: match the surrounding code — `from __future__ import annotations`, `typing` annotations, a module docstring on every new module, comments that say why, plain-language messages. Bad input raises `ValueError`; an unknown robot, operator or task id raises `KeyError`.
- Stage only the files your task lists (`git add <paths>`, never `git add -A`): the working tree has unrelated changes (`logs/tasks/*.json`, `.DS_Store`, `docs/handoffs/`) that are not yours.
- Every commit message ends with exactly this line: `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`
- Edits are given as exact **Replace in** blocks: each block's old text matches the file exactly once at the moment it is applied, in the order given (use the Edit tool with it as `old_string`; a block that ends in an empty line ends at the end of the file). **Create** blocks give a new file's whole content.

## Plan rulings

Choices this plan makes where the spec is ambiguous, silent or self-contradictory. Each can be changed at plan review.

1. **Fixtures.** The new evaluation fixtures go in `logs/eval_examples/multi_embodiment/` (`task_200…217_<check>_<pass|fail>.json`): `test_eval_engine.py`'s expectation table must match every `logs/eval_examples/task_*.json` and may not be edited, and its glob doesn't descend into the subdirectory. `test_trust_checks.py` grades them.
2. **Step timings.** The PICK and DELIVER of the new job types take the body's `grasp_s`/`place_s`; the five older box job types keep `PICK_TICKS`/`DELIVER_TICKS` on both floors (§5.4). `ROBOT_STEP`, the path, pick and snapshot data, and the energy model apply only to robots with a floor profile, so classic logs and batteries don't change.
3. **Stock.** A tote keeps its ledger location (and `Box.slot`) while it is away at a station, so its recorded and true quantities travel with it; a pallet leaves the ledger when it is grasped for retrieval and joins it when put away; `Box.slot`/`Box.quantity` always mirror the ledger. An item weighs its tote's weight less a 1.5 kg tote tare, shared across its units. No weight-edit path exists, and `add_box` sets both weights.
4. **WRONG_LEVEL.** The ledger keeps the requested slot (its true quantity drops to 0) and `Box.true_slot` records where the pallet really went; `PLACED` reports the requested `level` plus `true_level`. The next count of that face flags the variance, and a retrieval from the recorded slot fails — no §10.4 check targets it (see the spec problems in Self-review).
5. **Hand-offs.** `HANDOFF_LOSS_RISK` applies where a box is put onto the conveyor (robot→conveyor, arm→conveyor); a staging hand-off records a false-success delivery as not received. The sorter takes only cartons and rejects anything else (`FAILED`, to `sorter_reject`); a carton waits in the sorter while its dock is full. Shipped boxes stay on their dock cell but no longer occupy it.
6. **Conveyor.** "The arms downstream of a jam" pause while a jam is at or upstream of their working cell. An item tagged for an arm stops on that arm's working cell until the arm takes it. `CLEAR_JAM` needs `robot_cell_access` (scoped to the arm's model) only for a jam on an arm's working cell; a jam elsewhere needs no certification.
7. **People rules.** `PERSON_IN_AISLE` holds a forklift or hauler before it *enters* a place (non-route zone) holding a person; its zone entries log `ROBOT_STEP` `ENTER_ZONE`. Supervision is satisfied by *any* operator who is on shift (not `OFF_DUTY`), holds a valid in-scope credential and stands in or next to the humanoid's zone.
8. **Energy.** The lifted carriage is 10% of the catalog `mass_kg` (the catalog has none); lowering costs nothing; a landed drone idles at the ground rate; drones get the hard round-trip gate and never a charging detour; on the new floor a robot whose charger is unreachable asks again every `SHIFT_CHECK_EVERY_TICKS`.
9. **Selection.** Classic AUTO scoring is untouched. On the new floor each candidate's target is resolved from its own position for its own body, which is how "the origin is the job's source or target" is met without the first robot's position; an AUTO job no body on the floor could do is rejected at the gate with the reasons.
10. **Stations.** `PICK_ITEMS` is the robot picker's job at Pick 1 and `MANUAL_PICK` the person's at Pick 2 (the spec lists both for Pick 2). A picker takes units out of a tote it never lifts, so the tote isn't reserved.
11. **Human jobs.** A person walks to the job's zone, works, and after a jam walks back to where they came from (someone left in a fenced pack cell would stop its arm for good). `MANUAL_PICK` takes 5 s an item (unspecified). AUTO picks the nearest qualified person on the floor.
12. **Orders.** A stage watches its task's status each tick, the polled form of "advances on TASK_COMPLETED". A customer order holds one pick station and one pack cell while it needs them, its `PACK_ORDER` starts with it, and a line's tote comes once the previous line is picked. It goes to Pick 2 when Sam or Lee is on the floor and Pick 1 is taken or has no picker robot. A stage whose tote is busy waits without spending its retry. Patrols are jobs, not orders.
13. **Shift.** Each stream first fires at a fixed offset into the shift (10–150 s; departures after a full 30 minutes). Pausing stops generation; orders in flight carry on. People move only while the shift runs.
14. **People's activities** live in their own module, `backend/operations/activities.py`. Breaks start after the first two sim-hours, staggered 12 minutes. A person a waiting forklift or hauler needs out of a zone steps to the nearest zone those bodies can't drive in for 30 s — without it, Sam and Lee idling at intake and Ana and Kai at the docks would stop unloading for good.
15. **Walkway sides.** `crosses_walkway` decides sides by a zone's cells, so a walk to or from a zone straddling the strip always counts as crossing it.
16. **Patrol report.** "Report anomalies" is the patrol's last step: halted robots and FAILED boxes on the loop, in `task.params["anomalies"]` and a log line (no new event type).
17. **System checks.** A dock door is part of the perimeter wall, and per-class connectivity uses the AC-TR50, NW-PF1200 and CT-IX2 bodies.

**Task count.** Spec item 8 (steps) is Task 4 plus Task 5 (the people rules need the step machinery, and the wait pairing is its own reviewable change); item 9 is split by embodiment into Tasks 7–11 (the filter and gate first, tested on the older job types, then pallets, totes, stations and people's jobs, drones and scouts); item 12 is Tasks 12–13 (orders and generators, then people and duty); items 10 and 13 are Tasks 1 and 14. Goods (Task 2), equipment (Task 3) and energy (Task 6) are the foundations the steps and jobs stand on. That gives fourteen tasks.

## File map

| File | Task | Responsibility |
|---|---|---|
| `backend/eligibility.py` | 1 | The eight physical rules (`payload_ok` … `cert_scope_ok`) |
| `backend/faults.py` | 1 (create) | `FaultInjector`: armed one-shots and the CONFIG risks |
| `backend/goods.py` | 2 | Boxes in and out of the ledger: `store`, `release`, `take_units`, `physical_qty`, `free_slots` |
| `backend/box.py` | 2 | `true_slot` |
| `backend/equipment.py` | 3 (create), 4 | Conveyor, sorter, hand-off records, jams, departures |
| `backend/robot.py` | 4, 5, 6 | `activity`, `lift_height_m`, wait fields, `model_code`, energy methods |
| `backend/simulator.py` | 3–7, 8, 10, 11, 13, 14 | Step framework and handlers, people rules, waits, energy, human jobs, the patrol report, path data |
| `backend/people.py` | 5 | Walkway sides by cells; supervision |
| `backend/energy.py` | 6 (create) | The §5.5 Wh model |
| `backend/embodiment.py` | 6 | `MobilityProfile.mass_kg` |
| `backend/jobs.py` | 7 (create), 8–11 | `JobSpec`, `JOB_SPECS`, the thirteen job types' checks, plans and targets |
| `backend/human_jobs.py` | 10 (create) | `MANUAL_PICK` and `CLEAR_JAM` phases; jams ask for clearing |
| `backend/operations/` | 12 (create), 13 | `orders.py` (order chains), `shift.py` (generators, clock), `activities.py` (people, duty) |
| `backend/task_manager.py` | 5–7, 10, 11, 14 | `Task.params`, capability filter, gate, operators and scopes, snapshot fields |
| `backend/task_planner.py` | 2, 6, 7 | Shipped boxes free their cell; charging by body; job-type delegation |
| `backend/navigation.py` | 7 | `allow_goal_adjacent` on `distance`/`path_exists`; arms never routed |
| `backend/warehouse.py` | 7 | Real destinations on the new floor |
| `backend/fleet_bridge.py` | 5, 7, 13 | `model_code`, profile cache, `employment_status` |
| `backend/digital_twin.py` | 1–7, 12 | `faults`, `stock`, `equipment`, `shift`, `add_box` by kind, `end_safety_wait`, drone fixes |
| `backend/operator.py` | 13 | `employment_status` |
| `backend/models.py` | 1–6, 8–12 | CONFIG keys, `ActionType` steps, `TaskType`s, `EventType`s, `BoxStatus.SHIPPED`, presets, `CERTIFICATION_REQUIREMENTS["CLEAR_JAM"]` |
| `backend/eval_engine.py` | 14 | The nine checks, `applicable` |
| `backend/ci_engine.py` | 14 | Layout-driven system checks |
| `backend/agent_tools.py` | 7 | `TASK_TYPE_GUIDE` gains every job type |
| `logs/eval_examples/multi_embodiment/` | 14 (create) | A pass and a fail fixture per new check |
| `backend/test_*.py` | 1–14 (create) | `test_trust_rules`, `test_stock`, `test_equipment`, `test_job_steps`, `test_people_rules`, `test_energy`, `test_selection`, `test_pallet_jobs`, `test_tote_jobs`, `test_station_jobs`, `test_drone_jobs`, `test_shift`, `test_activities`, `test_trust_checks` |

## Carried forward from plan 1a

Every "To plan 1b" item of plan 1a's **Carried forward** section, and where it is handled:

| Plan 1a item | Task |
|---|---|
| §9.1's route check must not snap a goal: `find_path(..., allow_goal_adjacent=False)`, and `distance()`/`path_exists()` likewise; `_score_candidates` no longer measures from the first robot | 7 |
| `_safety_wait`/`_safety_resume` gain `PERSON_IN_AISLE`, `PERSON_IN_CELL`, `SUPERVISOR_ABSENT`, `ROBOT_STEP` and the `SAFETY_WAIT_ESCALATE_S` escalation (with `wait_started_tick`); people rules test `zones_of_cell` | 4, 5 |
| Safety waits pair up: switching reasons closes the previous wait, and a wait ended by its task, a reset, a layer change or a collision gets its closing event | 5 |
| CI's `_check_environment`, `_check_robot_positions`, `_check_navigation`, `_check_battery` become layout-driven | 14 |
| `CHARGE_ROBOT` sends a drone to its pad; a low drone no longer floods `_auto_charge` | 6 |
| Mains-powered arms skip auto-charge, `request_charge`, selection and the planner; a named arm `CHARGE_ROBOT` is a `validate` rejection | 6 |
| `step_ticks`/`lift_ticks`/`HOVER_CLEARANCE_M`/`set_robot_layer` drive LIFT_TO, TAKEOFF, LAND, SCAN, GRASP, PLACE; TAKEOFF and SCAN fly at the level's height plus the clearance; TAKEOFF and LAND emit `ROBOT_STEP` | 4, 11 |
| `set_robot_layer(AIR)` with no altitude keeps a flying drone's altitude; `reset_robot` with no free pad refuses instead of landing off the pad | 4 |
| A FIXED profile is never routed (no failure counted) | 7 |
| `add_box` gets `kind`/`sku`/`quantity`/`slot`; slot boxes on their slot, ITEM/CARTON never snapped (on a conveyor cell they ride the line); a missing default zone is a `ValueError` | 2, 3 |
| The twin owns the `StockLedger`, the single source of truth, with `Box.slot`/`Box.quantity` kept in step; the count job does the automatic reconcile | 2, 11 |
| `Box.weight` moves only the declared weight: no edit path exists; `add_box` sets both | 2 (Ruling 3) |
| `crosses_walkway`'s zone centres | 5 |
| `zone.attributes["route"]` is tested for presence | 5, 7, 11, 13 |
| `location_options()` filtered on the new floor | 7 |
| `people.place`/`start_transit` and the ledger take no lock: documented for 1c's API | Global Constraints |
| `FleetBridge._on_change` profile rebuild cached per model | 7 |

## Carried forward to plan 1c

- **Seed (§4.2).** Operators need shift windows that include the shift clock's 06:00 start (Task 13: duty follows it), or they boot off duty; arms should be placed by home zone (1a's item still stands). Tote stock must cover the customer-order generator (it only orders SKUs some tote has enough of). Plan 1a's other "To plan 1c" items still stand.
- **Save/load v2 (§4.3).** Beyond 1a's list: the ledger (with `Box.true_slot`), the equipment (line items with `stop_at`, jams, the sorter's cartons, the hand-off sequence), the shift engine (status, seed, pace, rates, `rng.getstate()`, `_next_due`, truck count, face index, `yield_until`) and its orders with their stages, `Robot.activity`/`lift_height_m`/`model_code`/wait fields, `Operator.employment_status`. `Task.params` (human-job phases included) already round-trips. `ShiftEngine` subscribes to `twin.events` once, at construction; a load must reuse it rather than build a second one.
- **Operations API (§11.5, §14).** Every call holds `twin.lock`: `/api/shift` → `twin.shift.status_dict()`, `.start()`, `.pause()`, `.configure(pace, seed, rates)`; `/api/orders` → `twin.shift.orders.list(status, kind, limit)`; `/api/stock` → `twin.stock.locations(sku)`; `/api/equipment` → `twin.equipment.to_dict()`; `/api/faults/<kind>` → `twin.faults.arm(kind)` (kinds: `faults.FAULT_RISKS`). Exceptions for the orders panel: `STOCK_VARIANCE_DETECTED` with `auto_reconciled` false, `SAFETY_WAIT_ESCALATED`, `ORDER_FAILED`.
- **Dashboard (§12).** `snapshot()` doesn't yet carry conveyor items, orders or people; the frontend's `TASK_FIELDS` needs the thirteen job types (`jobs.JOB_SPECS` has their labels and payload guides).
- **Soak (§16).** Measure the tick: on the new floor `_score_candidates` resolves every candidate's target each tick for each pending AUTO job, and `human_jobs.tick`, `promised_slots` and `ensure_jam_jobs` scan all tasks. Breaks begin after two sim-hours, so a 20 000-tick (50 sim-minute) soak sees none; the browser check "the humanoid pauses on Jordan's break" needs two sim-hours or a demo control. `WRONG_LEVEL_RISK`'s "matching check" can only be the variance a later count finds (Ruling 4).

---

### Task 1: The shared physical rules and the fault switchboard

The trust layer's new rules live in `backend/eligibility.py` as functions of plain values returning `(ok, reason)`, so the gate, robot selection, the mid-task re-check and the evaluation can all call the same code (spec §10.1). The injected faults of §10.6 get their CONFIG risks (all 0.0) and one switchboard, `twin.faults`, that both rolls the risks and fires one-shot faults armed on demand (plan 1c's `POST /api/faults/<kind>` will call `arm`). Later tasks make each fault do its damage where it happens.

**Files:**
- Modify: `backend/eligibility.py` (eight rule functions at the bottom)
- Create: `backend/faults.py`
- Modify: `backend/models.py` (seven `CONFIG` risks)
- Modify: `backend/digital_twin.py` (`twin.faults`, cleared on reset)
- Test: `backend/test_trust_rules.py` (create)

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - `backend.eligibility.Rule = Tuple[bool, Optional[str]]`; `WIDE`, `NARROW`; `DRONE_RESERVE_FRACTION = 0.25`.
  - `payload_ok(weight_kg, max_payload_kg) -> Rule`, `box_kind_ok(kind, box_kinds) -> Rule`, `reach_ok(level, max_shelf_level) -> Rule`, `clearance_ok(route_cells_clearance: Sequence[Optional[str]], robot_clearance: Optional[str]) -> Rule`, `no_fly_ok(route_cells_no_fly: Sequence[bool]) -> Rule`, `drone_round_trip_ok(battery_wh, est_wh, capacity_wh, reserve_fraction=0.25) -> Rule`, `supervision_ok(required, supervisor_on_shift: bool, credential_valid: bool, in_scope: bool) -> Rule`, `cert_scope_ok(scopes, code, model_code=None, site=None) -> Rule`. A `None` limit means the body declares none, so the rule holds.
  - `CONFIG` keys `SCAN_MISCOUNT_RISK`, `WRONG_LEVEL_RISK`, `GRASP_FAIL_RISK`, `CONVEYOR_JAM_RISK`, `HANDOFF_LOSS_RISK`, `MIS_SORT_RISK`, `MISDECLARED_WEIGHT_RISK`, all `0.0`.
  - `backend.faults.FAULT_RISKS: Dict[str, str]` (kind → CONFIG key; kinds `scan_miscount`, `wrong_level`, `grasp_fail`, `conveyor_jam`, `handoff_loss`, `mis_sort`, `misdeclared_weight`); `FaultInjector.arm(kind, count=1) -> int`, `.armed() -> Dict[str, int]`, `.roll(kind, rng=None) -> bool` (an armed one-shot first, else the risk; a zero risk never draws a random number), `.clear()`. Kinds accept `-` for `_`; an unknown kind raises `ValueError`.
  - `DigitalTwin.faults: FaultInjector`.

- [ ] **Step 1: Write the failing tests**

**Create** `backend/test_trust_rules.py`:

```python
"""The physical trust-layer rules (multi-embodiment spec §10.1) and the fault
switchboard (§10.6). Every rule takes plain values and returns (ok, reason)."""
import random

import pytest

from backend.digital_twin import DigitalTwin
from backend.eligibility import (
    box_kind_ok, cert_scope_ok, clearance_ok, drone_round_trip_ok, no_fly_ok, payload_ok,
    reach_ok, supervision_ok,
)
from backend.faults import FAULT_RISKS, FaultInjector
from backend.models import CONFIG


def test_payload_ok():
    assert payload_ok(600.0, 1200.0) == (True, None)
    assert payload_ok(300.0, 300.0) == (True, None)          # exactly at the limit
    ok, reason = payload_ok(310.5, 300.0)
    assert not ok and reason == "a 310.5 kg load is over its 300 kg payload limit"
    assert payload_ok(None, 50.0) == (True, None) and payload_ok(5.0, None) == (True, None)


def test_box_kind_ok():
    assert box_kind_ok("PALLET", ("PALLET",)) == (True, None)
    ok, reason = box_kind_ok("PALLET", ["TOTE", "ITEM"])
    assert not ok and reason == "cannot handle a PALLET (handles: TOTE, ITEM)"
    assert box_kind_ok("TOTE", []) == (False, "cannot handle a TOTE (handles: nothing)")
    assert box_kind_ok(None, []) == (True, None)


def test_reach_ok():
    assert reach_ok(1, 1) == (True, None) and reach_ok(0, 4) == (True, None)
    assert reach_ok(2, 1) == (False, "level 2 is out of its reach (highest level 1)")
    assert reach_ok(None, 0) == (True, None)


def test_clearance_ok():
    assert clearance_ok(["WIDE", "WIDE", None], "WIDE") == (True, None)
    assert clearance_ok(["WIDE", "NARROW", "NARROW"], "WIDE") == \
        (False, "a wide robot's route uses 2 narrow cell(s)")
    assert clearance_ok(["NARROW"], "NARROW") == (True, None)   # narrow robots go anywhere drivable
    assert clearance_ok(["NARROW"], None) == (True, None)       # drones and arms have no clearance


def test_no_fly_ok():
    assert no_fly_ok([False, False]) == (True, None) and no_fly_ok([]) == (True, None)
    assert no_fly_ok([False, True, True]) == (False, "an air route crosses 2 no-fly cell(s)")


def test_drone_round_trip_ok_keeps_a_quarter_of_capacity_in_reserve():
    assert drone_round_trip_ok(100.0, 60.0, 150.0) == (True, None)   # needs 60 + 37.5
    ok, reason = drone_round_trip_ok(90.0, 60.0, 150.0)
    assert not ok and reason == "has 90.0 Wh but the round trip needs 60.0 Wh plus a 25% reserve (97.5 Wh)"
    assert drone_round_trip_ok(97.5, 60.0, 150.0) == (True, None)


def test_supervision_ok():
    assert supervision_ok(None, False, False, False) == (True, None)   # nothing to supervise
    assert supervision_ok("humanoid_supervision", True, True, True) == (True, None)
    assert supervision_ok("humanoid_supervision", True, False, True)[1] == \
        "no operator holds a valid 'humanoid_supervision' credential"
    assert supervision_ok("humanoid_supervision", False, True, True)[1] == \
        "no operator with a 'humanoid_supervision' credential is on shift"
    assert supervision_ok("humanoid_supervision", True, True, False)[1] == \
        "no on-shift supervisor's 'humanoid_supervision' credential covers this robot and site"


def test_cert_scope_ok():
    scopes = {"robot_cell_access": {"equipment": ["FB-CX10"], "site": []},
              "humanoid_supervision": {"equipment": ["TS-H1"], "site": ["WH-01"]}}
    assert cert_scope_ok(scopes, "robot_cell_access", "FB-CX10", "WH-01") == (True, None)
    assert cert_scope_ok(scopes, "robot_cell_access", None, "WH-02") == (True, None)  # site unrestricted
    assert cert_scope_ok(scopes, "robot_cell_access", "TS-H1")[1] == \
        "has a 'robot_cell_access' credential that does not cover TS-H1 (covers: FB-CX10)"
    assert cert_scope_ok(scopes, "humanoid_supervision", "TS-H1", "WH-02")[1] == \
        "has a 'humanoid_supervision' credential that does not cover site WH-02 (covers: WH-01)"
    assert cert_scope_ok(scopes, "forklift_operator") == \
        (False, "does not hold a valid 'forklift_operator' credential")
    assert cert_scope_ok(None, "robot_cell_access")[0] is False


def test_every_fault_has_a_zero_risk_by_default():
    assert sorted(FAULT_RISKS) == ["conveyor_jam", "grasp_fail", "handoff_loss", "mis_sort",
                                   "misdeclared_weight", "scan_miscount", "wrong_level"]
    assert all(CONFIG[key] == 0.0 for key in FAULT_RISKS.values())


def test_an_armed_fault_fires_once_then_the_risk_decides(monkeypatch):
    faults = FaultInjector()
    state = random.getstate()
    assert not faults.roll("scan_miscount")
    assert random.getstate() == state          # a zero risk draws no random number
    assert faults.arm("scan-miscount") == 1 and faults.arm("scan_miscount") == 2
    assert faults.armed() == {"scan_miscount": 2}
    assert faults.roll("scan_miscount") and faults.roll("scan_miscount")
    assert not faults.roll("scan_miscount") and faults.armed() == {}
    monkeypatch.setitem(CONFIG, "SCAN_MISCOUNT_RISK", 1.0)
    assert faults.roll("scan_miscount", rng=random.Random(1))
    with pytest.raises(ValueError, match="Unknown fault kind"):
        faults.arm("gremlins")
    with pytest.raises(ValueError, match="at least 1"):
        faults.arm("mis_sort", count=0)


def test_the_twin_owns_a_fault_switchboard_that_reset_clears(tmp_path):
    twin = DigitalTwin(log_dir=str(tmp_path / "logs"), data_dir=str(tmp_path / "data"),
                       persist_logs=False, demo=True, demo_tasks=False)
    twin.faults.arm("mis_sort")
    assert twin.faults.armed() == {"mis_sort": 1}
    twin.reset(demo_tasks=False)
    assert twin.faults.armed() == {}
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/bin/python -m pytest -o addopts="" -q backend/test_trust_rules.py`
Expected: `1 error` — `ImportError: cannot import name 'box_kind_ok' from 'backend.eligibility'`.

- [ ] **Step 3: The rules**

**Replace in** `backend/eligibility.py`:

```python
whatever shape they already have on hand. A return value of ``None`` means
eligible; any other value is a plain-language reason it isn't.
"""
from __future__ import annotations

from typing import Optional, Sequence

try:  # pragma: no cover - defensive fallback only, mirrors eval_engine.py
```

with:

```python
whatever shape they already have on hand. A return value of ``None`` means
eligible; any other value is a plain-language reason it isn't.

The physical rules of the multi-embodiment floor (spec §10.1), at the bottom,
return ``(ok, reason)`` instead: the gate, robot selection, the mid-task
re-check and the evaluation all call them with plain numbers and lists.
"""
from __future__ import annotations

from typing import Mapping, Optional, Sequence, Tuple

try:  # pragma: no cover - defensive fallback only, mirrors eval_engine.py
```

**Replace in** `backend/eligibility.py`:

```python
            f"certification (has: {list(certs)})"
        )
    return None

```

with:

```python
            f"certification (has: {list(certs)})"
        )
    return None


# --------------------------------------------------------------------------- #
# Physical rules (multi-embodiment spec §10.1)
#
# Each returns (True, None) when the rule holds, else (False, reason). A
# missing limit (None) means the body declares none, so the rule holds.
# --------------------------------------------------------------------------- #
Rule = Tuple[bool, Optional[str]]

#: Aisle clearance classes (backend/layouts/base.py), spelled out so this
#: module stays importable on its own, like eval_engine.py.
WIDE, NARROW = "WIDE", "NARROW"

#: A drone must land with this share of its capacity still in the battery.
DRONE_RESERVE_FRACTION = 0.25


def payload_ok(weight_kg: Optional[float], max_payload_kg: Optional[float]) -> Rule:
    """The load is within the body's payload limit."""
    if weight_kg is None or max_payload_kg is None or float(weight_kg) <= float(max_payload_kg):
        return True, None
    return False, f"a {float(weight_kg):g} kg load is over its {float(max_payload_kg):g} kg payload limit"


def box_kind_ok(kind: Optional[str], box_kinds: Optional[Sequence[str]]) -> Rule:
    """The body can handle this kind of box (PALLET, TOTE, ITEM, CARTON)."""
    kinds = list(box_kinds or [])
    if kind is None or kind in kinds:
        return True, None
    return False, f"cannot handle a {kind} (handles: {', '.join(kinds) or 'nothing'})"


def reach_ok(level: Optional[int], max_shelf_level: Optional[int]) -> Rule:
    """The slot level is within the body's reach."""
    if level is None or max_shelf_level is None or int(level) <= int(max_shelf_level):
        return True, None
    return False, f"level {int(level)} is out of its reach (highest level {int(max_shelf_level)})"


def clearance_ok(route_cells_clearance: Sequence[Optional[str]], robot_clearance: Optional[str]) -> Rule:
    """A wide robot only uses WIDE cells (the two wide walkway crossings are
    WIDE). A narrow robot, a drone and fixed equipment always pass. Cells with
    no clearance class (None — not walkable) are ignored."""
    if robot_clearance != WIDE:
        return True, None
    narrow = sum(1 for clearance in route_cells_clearance if clearance == NARROW)
    if not narrow:
        return True, None
    return False, f"a wide robot's route uses {narrow} narrow cell(s)"


def no_fly_ok(route_cells_no_fly: Sequence[bool]) -> Rule:
    """An air route crosses no no-fly cell."""
    crossed = sum(1 for no_fly in route_cells_no_fly if no_fly)
    if not crossed:
        return True, None
    return False, f"an air route crosses {crossed} no-fly cell(s)"


def drone_round_trip_ok(battery_wh: float, est_wh: float, capacity_wh: float,
                        reserve_fraction: float = DRONE_RESERVE_FRACTION) -> Rule:
    """A drone flies only if its battery covers the whole round trip plus a
    reserve of `reserve_fraction` × capacity. A hard gate: unlike a ground
    robot, a drone can't detour to a charger mid-flight."""
    needed = float(est_wh) + reserve_fraction * float(capacity_wh)
    if float(battery_wh) >= needed:
        return True, None
    return False, (
        f"has {float(battery_wh):.1f} Wh but the round trip needs {float(est_wh):.1f} Wh "
        f"plus a {reserve_fraction:.0%} reserve ({needed:.1f} Wh)"
    )


def supervision_ok(required: Optional[str], supervisor_on_shift: bool, credential_valid: bool,
                   in_scope: bool) -> Rule:
    """A supervised body (the humanoid) may work only if a supervisor with a
    valid, in-scope `required` credential is on shift. Proximity is not part
    of this rule: it is a runtime wait (SUPERVISOR_ABSENT, spec §6)."""
    if not required:
        return True, None
    if not credential_valid:
        return False, f"no operator holds a valid {required!r} credential"
    if not supervisor_on_shift:
        return False, f"no operator with a {required!r} credential is on shift"
    if not in_scope:
        return False, f"no on-shift supervisor's {required!r} credential covers this robot and site"
    return True, None


def cert_scope_ok(scopes: Optional[Mapping[str, Mapping[str, Sequence[str]]]], code: str,
                  model_code: Optional[str] = None, site: Optional[str] = None) -> Rule:
    """The operator's valid `code` credential covers the equipment model and
    site involved. `scopes` is Operator.certification_scopes: an empty list
    means unrestricted in that dimension; None for model or site skips it."""
    scope = (scopes or {}).get(code)
    if scope is None:
        return False, f"does not hold a valid {code!r} credential"
    equipment = list(scope.get("equipment") or [])
    if model_code and equipment and model_code not in equipment:
        return False, f"has a {code!r} credential that does not cover {model_code} (covers: {', '.join(equipment)})"
    sites = list(scope.get("site") or [])
    if site and sites and site not in sites:
        return False, f"has a {code!r} credential that does not cover site {site} (covers: {', '.join(sites)})"
    return True, None

```

- [ ] **Step 4: The fault switchboard and its risks**

**Create** `backend/faults.py`:

```python
"""Injected faults (multi-embodiment spec §10.6).

Each fault kind has a CONFIG risk (0.0 by default, settable through
policies.yaml like FALSE_SUCCESS_RISK): the chance the fault happens at each
opportunity. `FaultInjector.arm(kind)` also queues single occurrences on
demand for demos (plan 1c's POST /api/faults/<kind> calls it): an armed fault
fires at the next opportunity whatever the risk.

The code where a fault can happen calls `twin.faults.roll(kind)`; what the
fault then does lives there (the SCAN step, the conveyor, the sorter, ...).
"""
from __future__ import annotations

import random
from typing import Dict, Optional

from .models import CONFIG

#: Fault kind -> the CONFIG risk that sets how often it happens.
FAULT_RISKS: Dict[str, str] = {
    "scan_miscount": "SCAN_MISCOUNT_RISK",
    "wrong_level": "WRONG_LEVEL_RISK",
    "grasp_fail": "GRASP_FAIL_RISK",
    "conveyor_jam": "CONVEYOR_JAM_RISK",
    "handoff_loss": "HANDOFF_LOSS_RISK",
    "mis_sort": "MIS_SORT_RISK",
    "misdeclared_weight": "MISDECLARED_WEIGHT_RISK",
}


def _kind(kind: str) -> str:
    key = str(kind or "").strip().lower().replace("-", "_")
    if key not in FAULT_RISKS:
        raise ValueError(f"Unknown fault kind {kind!r} (known: {sorted(FAULT_RISKS)})")
    return key


class FaultInjector:
    """The twin's fault switchboard: armed one-shots plus the CONFIG risks."""

    def __init__(self) -> None:
        self._armed: Dict[str, int] = {}

    def arm(self, kind: str, count: int = 1) -> int:
        """Queue `count` occurrences of `kind` for its next opportunities.
        Returns how many are now armed."""
        key = _kind(kind)
        if int(count) < 1:
            raise ValueError("count must be at least 1")
        self._armed[key] = self._armed.get(key, 0) + int(count)
        return self._armed[key]

    def armed(self) -> Dict[str, int]:
        return {key: count for key, count in self._armed.items() if count}

    def roll(self, kind: str, rng: Optional[random.Random] = None) -> bool:
        """Should this opportunity fault? An armed occurrence is used up
        first; otherwise the CONFIG risk decides. A zero risk never draws a
        random number, so a fault-free run leaves every random stream alone."""
        key = _kind(kind)
        if self._armed.get(key):
            self._armed[key] -= 1
            return True
        risk = float(CONFIG.get(FAULT_RISKS[key], 0.0) or 0.0)
        if risk <= 0.0:
            return False
        return (rng or random).random() < risk

    def clear(self) -> None:
        self._armed.clear()
```

**Replace in** `backend/models.py`:

```python
    "CARTON_TARE_KG": 0.3,
}
```

with:

```python
    "CARTON_TARE_KG": 0.3,
    # Injected faults (backend/faults.py), all 0.0-1.0 and off by default like
    # FALSE_SUCCESS_RISK: a drone count off by 1-3, a forklift placing a level
    # off, an arm or picker missing a grasp, a conveyor segment jamming per
    # item advance, an item lost at a hand-off, a carton sorted to the wrong
    # dock, and an inbound pallet weighing 1.1-1.6x what it declares.
    "SCAN_MISCOUNT_RISK": 0.0,
    "WRONG_LEVEL_RISK": 0.0,
    "GRASP_FAIL_RISK": 0.0,
    "CONVEYOR_JAM_RISK": 0.0,
    "HANDOFF_LOSS_RISK": 0.0,
    "MIS_SORT_RISK": 0.0,
    "MISDECLARED_WEIGHT_RISK": 0.0,
}
```

**Replace in** `backend/digital_twin.py`:

```python
from .embodiment import AIR, GROUND, HOVER_CLEARANCE_M, LAYERS, MobilityProfile
from .fleet_bridge import FleetBridge
```

with:

```python
from .embodiment import AIR, GROUND, HOVER_CLEARANCE_M, LAYERS, MobilityProfile
from .faults import FaultInjector
from .fleet_bridge import FleetBridge
```

**Replace in** `backend/digital_twin.py`:

```python
        self.last_sync = now_iso()
        self.ci_result: Optional[Dict[str, Any]] = None

        # Rolling samples for the dashboard's trend charts — see
```

with:

```python
        self.last_sync = now_iso()
        self.ci_result: Optional[Dict[str, Any]] = None
        # Armed one-shot faults and the CONFIG risks (backend/faults.py).
        self.faults = FaultInjector()

        # Rolling samples for the dashboard's trend charts — see
```

**Replace in** `backend/digital_twin.py`:

```python
            self.scheduler.clear()
            for key in self.statistics:
```

with:

```python
            self.scheduler.clear()
            self.faults.clear()
            for key in self.statistics:
```

- [ ] **Step 5: Run the new tests**

Run: `.venv/bin/python -m pytest -o addopts="" -q backend/test_trust_rules.py`
Expected: `11 passed`.

- [ ] **Step 6: Run the full suite**

Run: `.venv/bin/python -m pytest -o addopts="" -q`
Expected: `2 failed, 487 passed` (only the two pre-existing failures).

- [ ] **Step 7: Commit**

```bash
git add backend/eligibility.py backend/faults.py backend/models.py backend/digital_twin.py \
        backend/test_trust_rules.py
git commit -m "feat: shared physical trust rules and the fault switchboard

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Goods in the twin — one stock ledger, boxes placed by kind

The twin owns a `StockLedger` (`twin.stock`), the single source of truth for stock (spec §7.2, plan 1a's carry list). `add_box` gains `kind`, `sku`, `quantity`, `slot`, `true_weight_kg` and `order_id`: a PALLET or TOTE given a slot sits in it and is recorded there; an ITEM or CARTON sits exactly where it is put, never snapped; and a floor without classic's default shelf raises `ValueError` instead of `AttributeError` when no position is given. Helpers in `backend/goods.py` move boxes in and out of the ledger and keep `Box.slot` and `Box.quantity` in step with it — the jobs of Tasks 4, 8–11 call them. Boxes gain the `SHIPPED` status and a `true_slot` for a pallet that physically sits somewhere other than its record says (the WRONG_LEVEL fault).

**Files:**
- Modify: `backend/models.py` (`BoxStatus.SHIPPED`; `CONFIG["TOTE_TARE_KG"]`)
- Modify: `backend/box.py` (`true_slot`)
- Modify: `backend/goods.py` (the twin-aware helpers)
- Modify: `backend/digital_twin.py` (`twin.stock`; `add_box`; `_slot_cell`; `box_at` ignores shipped boxes)
- Modify: `backend/task_planner.py` (a shipped box no longer occupies its cell)
- Test: `backend/test_stock.py` (create)

**Interfaces:**
- Consumes: plan 1a's `StockLedger(warehouse)` (`put`, `take`, `location`, `box_in`, `slot_of`, `locations`, `consume`, `adjust_true`, `record_count`, `reconcile`), `Warehouse.slot(slot_id)`, `.slots`, `.slot_at(cell, level)`, `Slot(slot_id, kind, cell, level, height_m, faces)`, `BoxKind`.
- Produces:
  - `BoxStatus.SHIPPED`; `CONFIG["TOTE_TARE_KG"] = 1.5`.
  - `Box.true_slot: Optional[str]` (in `to_dict`/`from_dict`).
  - `DigitalTwin.stock: StockLedger` (a fresh one on reset); `DigitalTwin.add_box(name=None, position=None, weight=1.0, source=None, destination=None, kind=None, sku=None, quantity=0, slot=None, true_weight_kg=None, order_id=None) -> Box`; on a non-classic floor `BOX_CREATED` data carries `kind`, `sku`, `quantity`, `slot`, `declared_weight_kg`, `true_weight_kg` (classic data stays `{}`).
  - `backend.goods`: `sync_box(twin, box)`, `store(twin, box, slot_id, true_slot_id=None) -> StockLocation`, `release(twin, box) -> StockLocation`, `unit_weight_kg(tote_weight_kg, quantity) -> float`, `take_units(twin, tote, count=1) -> Tuple[float, float]` (one unit's declared and true weight), `physical_qty(twin, slot_id) -> int`, `free_slots(twin, kind, max_level=None, near=None, exclude=()) -> List[Slot]`.

- [ ] **Step 1: Write the failing tests**

**Create** `backend/test_stock.py`:

```python
"""Goods in the twin (multi-embodiment spec §7): the twin's stock ledger is
the single source of truth, boxes of every kind are placed by their kind, and
the helpers keep each box's slot and quantity in step with the ledger."""
import pytest

from backend import goods
from backend.digital_twin import DigitalTwin
from backend.models import BoxKind, BoxStatus, CONFIG


def make_twin(tmp_path, layout="distribution_center"):
    return DigitalTwin(log_dir=str(tmp_path / "logs"), data_dir=str(tmp_path / "data"),
                       persist_logs=False, demo=True, demo_tasks=False, layout=layout)


@pytest.fixture
def twin(tmp_path):
    return make_twin(tmp_path)


def test_the_twin_owns_one_ledger_and_reset_empties_it(twin):
    assert isinstance(twin.stock, goods.StockLedger) and twin.stock.warehouse is twin.warehouse
    twin.add_box(name="PAL-1", kind="PALLET", sku="SKU-01", quantity=40, weight=600.0, slot="PR-08-02-0")
    assert twin.stock.box_in("PR-08-02-0") is not None
    twin.reset(demo_tasks=False)
    assert twin.stock.locations() == [] and twin.stock.warehouse is twin.warehouse


def test_a_pallet_or_tote_given_a_slot_sits_in_it_and_is_recorded(twin):
    pallet = twin.add_box(name="PAL-1", kind="PALLET", sku="SKU-01", quantity=40, weight=600.0,
                          slot="PR-08-02-3", true_weight_kg=780.0)
    assert (pallet.kind, pallet.position, pallet.slot) == (BoxKind.PALLET, (8, 2), "PR-08-02-3")
    assert (pallet.declared_weight_kg, pallet.true_weight_kg) == (600.0, 780.0)
    location = twin.stock.location("PR-08-02-3")
    assert (location.box_id, location.sku, location.recorded_qty, location.true_qty) == (pallet.id, "SKU-01", 40, 40)
    tote = twin.add_box(name="TOTE-1", kind="tote", sku="SKU-02", quantity=12, weight=9.0, slot="TS-10-12-1")
    assert tote.position == (10, 12) and twin.stock.slot_of(tote.id) == "TS-10-12-1"
    created = twin.events.query(event_type="BOX_CREATED")[-1]
    assert created["data"]["kind"] == "TOTE" and created["data"]["slot"] == "TS-10-12-1"


def test_impossible_slot_placements_create_nothing(twin):
    twin.add_box(name="PAL-1", kind="PALLET", sku="SKU-01", quantity=40, weight=600.0, slot="PR-08-02-0")
    count = len(twin.boxes)
    with pytest.raises(ValueError, match="already holds"):
        twin.add_box(name="PAL-2", kind="PALLET", sku="SKU-01", quantity=10, slot="PR-08-02-0")
    with pytest.raises(ValueError, match="cannot go in"):
        twin.add_box(name="TOTE-9", kind="TOTE", sku="SKU-01", quantity=10, slot="PR-09-02-0")
    with pytest.raises(ValueError, match="Unknown slot"):
        twin.add_box(name="PAL-3", kind="PALLET", slot="PR-99-02-0")
    with pytest.raises(ValueError, match="is at"):
        twin.add_box(name="PAL-4", kind="PALLET", slot="PR-09-02-0", position=(1, 1))
    with pytest.raises(ValueError, match="non-negative"):
        twin.add_box(name="PAL-5", kind="PALLET", slot="PR-10-02-0", quantity=-1)
    assert len(twin.boxes) == count and twin.find_box("PAL-2") is None


def test_items_and_cartons_sit_exactly_where_they_are_put(twin):
    item = twin.add_box(name="ITEM-1", kind="ITEM", sku="SKU-01", quantity=1, weight=0.4, position=(21, 15))
    assert item.position == (21, 15)  # a conveyor cell: never snapped onto Pick 1's work cell
    carton = twin.add_box(name="CTN-1", kind="CARTON", weight=1.1, position=(23, 14))
    assert carton.position == (23, 14) and carton.kind is BoxKind.CARTON
    with pytest.raises(ValueError, match="needs a position"):
        twin.add_box(name="ITEM-2", kind="ITEM")
    with pytest.raises(ValueError, match="outside"):
        twin.add_box(name="ITEM-3", kind="ITEM", position=(40, 40))


def test_a_floor_box_needs_a_position_on_the_new_floor(twin, tmp_path):
    with pytest.raises(ValueError, match="needs a position or a slot"):
        twin.add_box(name="Loose")
    pallet = twin.add_box(name="PAL-D1", kind="PALLET", sku="SKU-03", quantity=20, weight=450.0, position=(2, 3))
    assert pallet.position == (2, 3) and pallet.slot is None and twin.stock.slot_of(pallet.id) is None
    classic = make_twin(tmp_path / "classic", layout="classic")
    box = classic.add_box(name="Box-Z")
    assert box.position == (3, 5) and box.kind is BoxKind.TOTE        # classic's default shelf, as before
    assert classic.events.query(event_type="BOX_CREATED")[-1]["data"] == {}


def test_store_release_and_sync_keep_the_box_and_the_ledger_in_step(twin):
    pallet = twin.add_box(name="PAL-D1", kind="PALLET", sku="SKU-03", quantity=20, weight=450.0, position=(5, 4))
    goods.store(twin, pallet, "PR-12-05-2")
    assert (pallet.slot, pallet.position, pallet.true_slot) == ("PR-12-05-2", (12, 5), None)
    assert twin.stock.location("PR-12-05-2").recorded_qty == 20
    goods.release(twin, pallet)
    assert pallet.slot is None and twin.stock.location("PR-12-05-2") is None
    with pytest.raises(ValueError, match="not in the stock ledger"):
        goods.release(twin, pallet)
    tote = twin.add_box(name="TOTE-1", kind="TOTE", sku="SKU-02", quantity=12, weight=9.0, slot="TS-10-12-1")
    twin.stock.adjust_true("TS-10-12-1", -2)             # two units went missing
    tote.position = (19, 14)                              # away at Pick 1: it keeps its slot
    goods.store(twin, tote, "TS-10-12-1")                 # back home
    location = twin.stock.location("TS-10-12-1")
    assert (location.recorded_qty, location.true_qty, tote.position) == (12, 10, (10, 12))
    goods.store(twin, tote, "TS-11-12-2")                 # moved to another slot: quantities go with it
    moved = twin.stock.location("TS-11-12-2")
    assert (moved.recorded_qty, moved.true_qty, tote.slot) == (12, 10, "TS-11-12-2")
    assert twin.stock.location("TS-10-12-1") is None


def test_a_wrong_level_placement_is_recorded_where_it_was_meant_to_go(twin):
    pallet = twin.add_box(name="PAL-D1", kind="PALLET", sku="SKU-03", quantity=20, weight=450.0, position=(5, 4))
    goods.store(twin, pallet, "PR-12-05-2", true_slot_id="PR-12-05-3")
    assert (pallet.slot, pallet.true_slot) == ("PR-12-05-2", "PR-12-05-3")
    assert twin.stock.location("PR-12-05-2").recorded_qty == 20
    assert goods.physical_qty(twin, "PR-12-05-2") == 0      # nothing really there...
    assert goods.physical_qty(twin, "PR-12-05-3") == 20     # ...it went one level up
    assert "PR-12-05-3" not in [slot.slot_id for slot in goods.free_slots(twin, "PALLET")]


def test_picking_units_out_of_a_tote(twin):
    tote = twin.add_box(name="TOTE-1", kind="TOTE", sku="SKU-02", quantity=10, weight=11.5, slot="TS-10-12-1")
    assert goods.unit_weight_kg(11.5, 10) == 1.0 and goods.unit_weight_kg(5.0, 0) == 0.0
    assert goods.take_units(twin, tote, 2) == (1.0, 1.0)
    location = twin.stock.location("TS-10-12-1")
    assert (location.recorded_qty, location.true_qty, tote.quantity) == (8, 8, 8)
    assert tote.declared_weight_kg == tote.true_weight_kg == 9.5
    twin.stock.adjust_true("TS-10-12-1", -8)                # the tote is really empty
    with pytest.raises(ValueError, match="physically holds only 0"):
        goods.take_units(twin, tote, 1)
    pallet = twin.add_box(name="PAL-1", kind="PALLET", sku="SKU-01", quantity=40, slot="PR-08-02-0")
    with pytest.raises(ValueError, match="not a TOTE"):
        goods.take_units(twin, pallet, 1)
    assert CONFIG["TOTE_TARE_KG"] == 1.5


def test_free_slots_are_empty_within_reach_and_nearest_first(twin):
    twin.add_box(name="PAL-1", kind="PALLET", sku="SKU-01", quantity=40, slot="PR-08-02-0")
    near_intake = goods.free_slots(twin, "PALLET", near=(6, 3))
    assert near_intake[0].slot_id == "PR-08-02-1"           # PR-08-02-0 is taken
    assert all(slot.kind == "PALLET" for slot in near_intake) and len(near_intake) == 179
    low = goods.free_slots(twin, "TOTE", max_level=1, exclude=["TS-08-12-0"])
    assert all(slot.level <= 1 for slot in low) and "TS-08-12-0" not in [s.slot_id for s in low]
    assert len(low) == 71


def test_a_shipped_box_no_longer_occupies_its_cell(twin):
    pallet = twin.add_box(name="PAL-D3", kind="PALLET", sku="SKU-03", quantity=20, weight=450.0, position=(28, 2))
    forklift = twin.add_robot(name="PF1200-205", asset_id="AST-000205", position=(27, 2))
    route = {"profile": forklift.mobility, "layer": forklift.layer}
    assert twin.box_at((28, 2)) is pallet
    assert twin.planner.resolve_target("dock_3", forklift.position, prefer_free=True, **route)[0] != (28, 2)
    pallet.set_status(BoxStatus.SHIPPED)
    assert twin.box_at((28, 2)) is None
    cell, _ = twin.planner.resolve_target("dock_3", forklift.position, prefer_free=True, **route)
    assert cell == (28, 2)  # the nearest dock cell is free again
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/bin/python -m pytest -o addopts="" -q backend/test_stock.py`
Expected: `10 failed` — mostly `TypeError: DigitalTwin.add_box() got an unexpected keyword argument 'kind'`, and `AttributeError: 'DigitalTwin' object has no attribute 'stock'`.

- [ ] **Step 3: Shipped boxes, the tote tare and a box's true slot**

**Replace in** `backend/models.py`:

```python
    # Goods (backend/goods.py): a cycle-count variance this small or smaller
    # is reconciled automatically; a packed carton weighs its items plus this.
    "STOCK_AUTO_RECONCILE_UNITS": 2,
    "CARTON_TARE_KG": 0.3,
    # Injected faults (backend/faults.py), all 0.0-1.0 and off by default like
```

with:

```python
    # Goods (backend/goods.py): a cycle-count variance this small or smaller
    # is reconciled automatically; a packed carton weighs its items plus this,
    # and an empty tote weighs this (a unit weighs its share of the rest).
    "STOCK_AUTO_RECONCILE_UNITS": 2,
    "CARTON_TARE_KG": 0.3,
    "TOTE_TARE_KG": 1.5,
    # Injected faults (backend/faults.py), all 0.0-1.0 and off by default like
```

**Replace in** `backend/models.py`:

```python
    DELIVERING = "DELIVERING"
    DELIVERED = "DELIVERED"
    FAILED = "FAILED"


class TaskStatus(str, enum.Enum):
```

with:

```python
    DELIVERING = "DELIVERING"
    DELIVERED = "DELIVERED"
    FAILED = "FAILED"
    # Left the building on an outbound truck (multi-embodiment spec §8.2, §9.2).
    SHIPPED = "SHIPPED"


class TaskStatus(str, enum.Enum):
```

**Replace in** `backend/box.py`:

```python
        self.true_weight_kg = float(true_weight_kg) if true_weight_kg is not None else self.declared_weight_kg
        # The rack or shelf slot (a Warehouse.slots id) it sits in, if any.
        self.slot = slot
        self.order_id = order_id
```

with:

```python
        self.true_weight_kg = float(true_weight_kg) if true_weight_kg is not None else self.declared_weight_kg
        # The rack or shelf slot (a Warehouse.slots id) the stock ledger holds
        # it in, if any — backend/goods.py keeps this in step with the ledger.
        # A tote keeps its slot while it is away at a station. `true_slot` is
        # set only when the box physically sits in a different slot than the
        # record says (a wrong-level placement).
        self.slot = slot
        self.true_slot: Optional[str] = None
        self.order_id = order_id
```

**Replace in** `backend/box.py`:

```python
            "slot": self.slot,
            "order_id": self.order_id,
```

with:

```python
            "slot": self.slot,
            "true_slot": self.true_slot,
            "order_id": self.order_id,
```

**Replace in** `backend/box.py`:

```python
        )
        box.assigned_robot = data.get("assigned_robot")
```

with:

```python
        )
        box.true_slot = data.get("true_slot")
        box.assigned_robot = data.get("assigned_robot")
```

- [ ] **Step 4: Keeping boxes and the ledger in step**

**Replace in** `backend/goods.py`:

```python
record says (`recorded_qty`) and what is really there (`true_qty`). Only a
fault or a mis-pick makes them differ, and only a count reconciliation
corrects the record. Nothing here emits events: the jobs that pick, count
and reconcile do (plan 1b).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, Iterable, List, Optional

from .models import CONFIG, BoxKind

__all__ = ["BoxKind", "StockLedger", "StockLocation", "carton_weight_kg"]


def carton_weight_kg(item_weights_kg: Iterable[float]) -> float:
```

with:

```python
record says (`recorded_qty`) and what is really there (`true_qty`). Only a
fault or a mis-pick makes them differ, and only a count reconciliation
corrects the record. Nothing here emits events: the jobs that pick, count
and reconcile do.

The twin owns one ledger (DigitalTwin.stock), the single source of truth for
stock. The helpers at the bottom move boxes in and out of it and keep each
box's `slot` and `quantity` in step with it. Like the ledger itself they take
no lock: callers hold twin.lock (the simulator tick does).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .models import CONFIG, BoxKind, Cell, manhattan

__all__ = [
    "BoxKind", "StockLedger", "StockLocation", "carton_weight_kg", "free_slots", "physical_qty",
    "release", "store", "sync_box", "take_units", "unit_weight_kg",
]


def carton_weight_kg(item_weights_kg: Iterable[float]) -> float:
```

**Replace in** `backend/goods.py`:

```python
            raise KeyError(f"Slot {slot_id} is empty")
        return location

```

with:

```python
            raise KeyError(f"Slot {slot_id} is empty")
        return location


# --------------------------------------------------------------------------- #
# Keeping boxes and the ledger in step
# --------------------------------------------------------------------------- #
def sync_box(twin: Any, box: Any) -> None:
    """Mirror the ledger onto `box`: the slot the ledger holds it in, and its
    recorded quantity there."""
    slot_id = twin.stock.slot_of(box.id)
    box.slot = slot_id
    if slot_id is not None:
        box.quantity = twin.stock.location(slot_id).recorded_qty
    box.touch()


def store(twin: Any, box: Any, slot_id: str, true_slot_id: Optional[str] = None) -> StockLocation:
    """Put a pallet or tote away in `slot_id`, and record it there.

    A tote going back to its own slot keeps its location (and any variance it
    carries); a box moving to another slot takes its quantities with it; a box
    new to storage is recorded with its SKU and quantity. `true_slot_id` is
    where the box physically went when that differs (a wrong-level placement):
    the record keeps the requested slot, whose true quantity drops to 0, and
    the box remembers where it really is."""
    stock = twin.stock
    slot = twin.warehouse.slot(slot_id)
    if slot is None:
        raise ValueError(f"Unknown slot {slot_id!r}")
    current = stock.slot_of(box.id)
    if current == slot_id:
        location = stock.location(slot_id)
    elif current is not None:
        previous = stock.take(current)
        location = stock.put(slot_id, box.id, previous.sku, previous.recorded_qty, kind=box.kind.value)
        location.true_qty = previous.true_qty
    else:
        location = stock.put(slot_id, box.id, box.sku, box.quantity, kind=box.kind.value)
    box.position = slot.cell
    box.true_slot = None
    if true_slot_id and true_slot_id != slot_id:
        actual = twin.warehouse.slot(true_slot_id)
        if actual is None:
            raise ValueError(f"Unknown slot {true_slot_id!r}")
        stock.adjust_true(slot_id, -location.true_qty)
        box.true_slot = true_slot_id
        box.position = actual.cell
    sync_box(twin, box)
    return location


def release(twin: Any, box: Any) -> StockLocation:
    """A pallet leaves storage for good (retrieved to ship): its location goes."""
    slot_id = twin.stock.slot_of(box.id)
    if slot_id is None:
        raise ValueError(f"{box.name} is not in the stock ledger")
    location = twin.stock.take(slot_id)
    box.slot = box.true_slot = None
    box.touch()
    return location


def unit_weight_kg(tote_weight_kg: float, quantity: int) -> float:
    """One unit's weight: the tote's contents (its weight less TOTE_TARE_KG)
    shared across its units."""
    if quantity <= 0:
        return 0.0
    return round(max(0.0, float(tote_weight_kg) - CONFIG["TOTE_TARE_KG"]) / quantity, 3)


def take_units(twin: Any, tote: Any, count: int = 1) -> Tuple[float, float]:
    """Pick `count` units out of a tote. Its recorded and true quantities drop
    by `count` and its weights by the units' weight; returns one unit's
    (declared, true) weight. A tote away at a station keeps its slot, so the
    ledger goes on counting what it holds."""
    if tote.kind is not BoxKind.TOTE:
        raise ValueError(f"{tote.name} is a {tote.kind.value}, not a TOTE")
    slot_id = twin.stock.slot_of(tote.id)
    if slot_id is None:
        raise ValueError(f"{tote.name} is not in the stock ledger")
    location = twin.stock.location(slot_id)
    if location.true_qty < count:
        raise ValueError(f"{tote.name} physically holds only {location.true_qty} of {location.sku}")
    declared_unit = unit_weight_kg(tote.declared_weight_kg, location.recorded_qty)
    true_unit = unit_weight_kg(tote.true_weight_kg, location.true_qty)
    twin.stock.consume(slot_id, count)
    tare = CONFIG["TOTE_TARE_KG"]
    tote.declared_weight_kg = round(max(tare, tote.declared_weight_kg - count * declared_unit), 3)
    tote.true_weight_kg = round(max(tare, tote.true_weight_kg - count * true_unit), 3)
    sync_box(twin, tote)
    return declared_unit, true_unit


def physical_qty(twin: Any, slot_id: str) -> int:
    """What is really in `slot_id`: its location's true quantity, plus any
    misplaced box that physically went there instead of its recorded slot."""
    location = twin.stock.location(slot_id)
    quantity = location.true_qty if location is not None else 0
    return quantity + sum(box.quantity for box in twin.boxes.values() if box.true_slot == slot_id)


def free_slots(twin: Any, kind: str, max_level: Optional[int] = None, near: Optional[Cell] = None,
               exclude: Sequence[str] = ()) -> List[Any]:
    """Empty `kind` slots (PALLET or TOTE) at or below `max_level`, nearest
    first to `near` (by their first face cell), then by slot id. A slot is
    empty when the ledger holds nothing there and no misplaced box sits in
    it; `exclude` names slots already promised to other jobs."""
    taken = set(exclude) | {box.true_slot for box in twin.boxes.values() if box.true_slot}
    slots = [
        slot for slot in twin.warehouse.slots.values()
        if slot.kind == BoxKind(kind).value and slot.slot_id not in taken
        and twin.stock.location(slot.slot_id) is None
        and (max_level is None or slot.level <= max_level)
    ]
    if near is None:
        return sorted(slots, key=lambda slot: slot.slot_id)
    return sorted(slots, key=lambda slot: (manhattan(slot.faces[0], near), slot.slot_id))

```

- [ ] **Step 5: The twin's ledger and `add_box` by kind**

**Replace in** `backend/digital_twin.py`:

```python
from .fleet_bridge import FleetBridge
from .inventory import NotFound as InventoryNotFound
```

with:

```python
from .fleet_bridge import FleetBridge
from .goods import StockLedger
from .inventory import NotFound as InventoryNotFound
```

**Replace in** `backend/digital_twin.py`:

```python
    AgentStatus,
    BoxStatus,
```

with:

```python
    AgentStatus,
    BoxKind,
    BoxStatus,
```

**Replace in** `backend/digital_twin.py`:

```python
        self.boxes: Dict[str, Box] = {}
        self.agents: Dict[str, Agent] = {}
```

with:

```python
        self.boxes: Dict[str, Box] = {}
        # Which pallet or tote sits in each rack and shelf slot, recorded versus
        # true quantities (backend/goods.py) — the single source of truth for
        # stock. Empty on the classic floor, which has no slots.
        self.stock = StockLedger(self.warehouse)
        self.agents: Dict[str, Agent] = {}
```

**Replace in** `backend/digital_twin.py`:

```python
        for box in self.boxes.values():
            if box.position == cell and box.status != BoxStatus.CARRIED:
                return box
```

with:

```python
        for box in self.boxes.values():
            if box.position == cell and box.status not in (BoxStatus.CARRIED, BoxStatus.SHIPPED):
                return box
```

**Replace in** `backend/digital_twin.py`:

```python
        destination: Optional[str] = None,
    ) -> Box:
        with self.lock:
```

with:

```python
        destination: Optional[str] = None,
        kind: Optional[str] = None,
        sku: Optional[str] = None,
        quantity: int = 0,
        slot: Optional[str] = None,
        true_weight_kg: Optional[float] = None,
        order_id: Optional[str] = None,
    ) -> Box:
        """Create a box (spec §7.1). `weight` is its declared weight, and
        `true_weight_kg` what it really weighs (the same unless given).

        A box given a `slot` (a PALLET or a TOTE) sits in that rack or shelf
        slot, and the stock ledger records it there with its SKU and quantity.
        An ITEM or CARTON sits exactly on `position` — a conveyor cell, a pack
        station — and is never snapped. Any other box goes on `position`, or
        classic's default shelf, snapped to the nearest drivable cell as
        always; a floor with no default shelf needs a position or a slot."""
        box_kind = BoxKind(str(kind).upper()) if kind else BoxKind.TOTE
        if isinstance(quantity, bool) or int(quantity) < 0:
            raise ValueError("quantity must be a non-negative whole number")
        with self.lock:
```

**Replace in** `backend/digital_twin.py`:

```python
                raise ValueError(f"A box called '{name}' already exists")

            candidate = position or self.warehouse.resolve_zone("shelf_a").cells[0]
            if not self.warehouse.is_inside(*candidate):
                raise ValueError(f"Position {candidate} is outside the warehouse")
            spot = candidate if self.warehouse.is_walkable(*candidate) \
                else self.warehouse.nearest_walkable(candidate)
            if spot is None:
                raise ValueError("No reachable cell available for a new box")

            zone = self.warehouse.zone_of_cell(spot)
```

with:

```python
                raise ValueError(f"A box called '{name}' already exists")

            if slot:
                spot = self._slot_cell(slot, box_kind, position)
            elif box_kind in (BoxKind.ITEM, BoxKind.CARTON):
                if position is None:
                    raise ValueError(f"An {box_kind.value} box needs a position")
                spot = (int(position[0]), int(position[1]))
                if not self.warehouse.is_inside(*spot):
                    raise ValueError(f"Position {spot} is outside the warehouse")
            else:
                default = self.warehouse.resolve_zone("shelf_a")
                if position is None and default is None:
                    raise ValueError(f"A new box on the {self.layout_name} floor needs a position or a slot")
                candidate = position or default.cells[0]
                if not self.warehouse.is_inside(*candidate):
                    raise ValueError(f"Position {candidate} is outside the warehouse")
                spot = candidate if self.warehouse.is_walkable(*candidate) \
                    else self.warehouse.nearest_walkable(candidate)
                if spot is None:
                    raise ValueError("No reachable cell available for a new box")

            zone = self.warehouse.zone_of_cell(spot)
```

**Replace in** `backend/digital_twin.py`:

```python
                destination=destination,
            )
            self.boxes[box.id] = box

        self.events.emit(
```

with:

```python
                destination=destination,
                kind=box_kind,
                sku=sku,
                quantity=int(quantity),
                true_weight_kg=true_weight_kg,
                order_id=order_id,
            )
            if slot:  # recorded first: a taken slot raises before the box exists
                self.stock.put(slot, box.id, sku, int(quantity), kind=box_kind.value)
                box.slot = slot
            self.boxes[box.id] = box

        data = None
        if self.layout_name != "classic":
            data = {"kind": box.kind.value, "sku": box.sku, "quantity": box.quantity, "slot": box.slot,
                    "declared_weight_kg": box.declared_weight_kg, "true_weight_kg": box.true_weight_kg}
        self.events.emit(
```

**Replace in** `backend/digital_twin.py`:

```python
            box_id=box.id,
            position=cell_dict(spot),
        )
        return box

    def add_agent(self, name: Optional[str] = None, model_version: Optional[str] = None) -> Agent:
```

with:

```python
            box_id=box.id,
            position=cell_dict(spot),
            data=data,
        )
        return box

    def _slot_cell(self, slot_id: str, kind: BoxKind, position: Optional[Cell]) -> Cell:
        """The rack or shelf cell a new box given `slot_id` sits on."""
        slot = self.warehouse.slot(slot_id)
        if slot is None:
            raise ValueError(f"Unknown slot {slot_id!r}")
        if kind.value != slot.kind:
            raise ValueError(f"A {kind.value} cannot go in {slot.kind} slot {slot_id}")
        if position is not None and tuple(position) != slot.cell:
            raise ValueError(f"Slot {slot_id} is at ({slot.cell[0]},{slot.cell[1]}), not {tuple(position)}")
        return slot.cell

    def add_agent(self, name: Optional[str] = None, model_version: Optional[str] = None) -> Agent:
```

**Replace in** `backend/digital_twin.py`:

```python
            self.faults.clear()
            for key in self.statistics:
```

with:

```python
            self.faults.clear()
            self.stock = StockLedger(self.warehouse)
            for key in self.statistics:
```

**Replace in** `backend/task_planner.py`:

```python
    ActionType,
    Cell,
```

with:

```python
    ActionType,
    BoxStatus,
    Cell,
```

**Replace in** `backend/task_planner.py`:

```python
            if prefer_free:
                occupied = {b.position for b in self.twin.boxes.values()}
                prefer = {c for c in zone.cells if c not in occupied}
```

with:

```python
            if prefer_free:
                # A shipped box has left on a truck: its dock cell is free again.
                occupied = {b.position for b in self.twin.boxes.values() if b.status is not BoxStatus.SHIPPED}
                prefer = {c for c in zone.cells if c not in occupied}
```

- [ ] **Step 6: Run the new tests with the goods tests**

Run: `.venv/bin/python -m pytest -o addopts="" -q backend/test_stock.py backend/test_goods.py`
Expected: `24 passed`.

- [ ] **Step 7: Run the full suite**

`test_classic_demo_boxes_are_unchanged` and every classic `add_box` caller keep their behaviour: a box with no kind is a TOTE snapped onto classic's default shelf.

Run: `.venv/bin/python -m pytest -o addopts="" -q`
Expected: `2 failed, 497 passed`.

- [ ] **Step 8: Commit**

```bash
git add backend/models.py backend/box.py backend/goods.py backend/digital_twin.py \
        backend/task_planner.py backend/test_stock.py
git commit -m "feat: the twin owns the stock ledger and places boxes by kind

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: The conveyor, the sorter and hand-off records

Sub-project E's equipment (spec §8) as one unit, `backend/equipment.py`, owned by the twin on any floor whose layout has a `conveyor` and a `sorter` zone (`twin.equipment`, `None` on classic). Items ride the line +x one cell every `ceil(CELL_SIZE_M / CONVEYOR_SPEED_MPS / TICK_DT)` = 20 ticks and back up behind anything that can't move; an item tagged for an arm stops on that arm's working cell until the arm takes it. The sorter takes a carton off (24,15) after 2 s and drops it, 2 s later, on a free cell of its order's lane dock (`box.destination`). Every transfer is a `Handoff` with the giver's claim and the receiver's observation, logged as a `HANDOFF` event. Three faults land here: `CONVEYOR_JAM_RISK` per item advance, `HANDOFF_LOSS_RISK` when a box is put on the line, and `MIS_SORT_RISK` in the sorter. The jam's `CLEAR_JAM` human job comes in Task 10; the arms' pause behind a jam in Task 4.

**Files:**
- Create: `backend/equipment.py`
- Modify: `backend/models.py` (`CONVEYOR_SPEED_MPS`, `SORTER_TRANSFER_S`; `EventType.HANDOFF`, `CONVEYOR_JAMMED`, `CONVEYOR_CLEARED`)
- Modify: `backend/digital_twin.py` (`twin.equipment`, rebuilt on reset; an ITEM or CARTON added on a conveyor cell rides the line)
- Modify: `backend/simulator.py` (the equipment ticks after robots act)
- Test: `backend/test_equipment.py` (create)

**Interfaces:**
- Consumes (Tasks 1–2): `twin.faults.roll(kind)`, `BoxStatus.SHIPPED`, `add_box(kind=...)`; plan 1a's `embodiment.seconds_to_ticks`, the `conveyor` zone (`direction: "+x"`), `sorter` zone (`chutes: ["dock_4", "dock_5"]`), pack-cell zones (`conveyor_cell`).
- Produces:
  - `CONFIG["CONVEYOR_SPEED_MPS"] = 0.5`, `CONFIG["SORTER_TRANSFER_S"] = 2.0`; `EventType.HANDOFF`, `CONVEYOR_JAMMED`, `CONVEYOR_CLEARED` (category `OPERATIONS`).
  - `LineItem(box_id, order_id=None, task_id=None, stop_at: Optional[Cell]=None, ticks=0)`.
  - `Handoff(handoff_id, box_id, order_id, from_holder, to_holder, cell, tick, giver_reported, receiver_observed, task_id=None)` with `.lost` and `.to_dict()` → `{"handoff_id", "box_id", "order_id", "from", "to", "cell": {x, y}, "tick", "giver_reported", "receiver_observed", "task_id"}` — the `HANDOFF` event's data. Holders are robot or operator ids, `"conveyor"`, `"sorter"`, `"sorter_reject"`, or a zone key (`"dock_4"`, `"intake_staging"`).
  - `Conveyor(cells)`: `.cells`, `.items: Dict[Cell, LineItem]`, `.jams: Dict[Cell, int]`, `.advance_ticks`, `cell in conveyor`, `.item_at(cell)`, `.is_free(cell)`, `.next_cell(cell)`, `.load(cell, item)`, `.unload(cell) -> LineItem`, `.jam_upstream_of(cell) -> Optional[Cell]`.
  - `Sorter(entry, cell, lanes)`: `.entry` (24,15), `.cell` (25,15), `.lanes`, `.inside`.
  - `Equipment.for_floor(twin) -> Optional[Equipment]`; `.conveyor`, `.sorter`, `.arm_cells` (`{"pack_cell_1": (23,15), "pack_cell_2": (22,15)}`), `.handoffs`; `.record(box, giver, receiver, cell, giver_reported, receiver_observed, task_id=None, order_id=None) -> Handoff`; `.place(box, cell, giver, task_id=None, order_id=None, stop_at=None) -> Handoff` (a loss leaves the box `FAILED` off the line); `.take(cell, receiver, task_id=None) -> (box, Handoff)`; `.release_order(order_id) -> int`; `.jam(cell)`, `.clear_jam(cell)`; `.free_dock_cell(dock) -> Optional[Cell]`; `.ship(dock) -> List[Box]`; `.tick()`; `.to_dict()`.
  - `DigitalTwin.equipment: Optional[Equipment]`.

- [ ] **Step 1: Write the failing tests**

**Create** `backend/test_equipment.py`:

```python
"""The conveyor line, the sorter and hand-off records (multi-embodiment spec
§8): items advance and back up, arms' items wait on their working cells,
cartons are sorted to their order's dock, and every transfer is recorded."""
import pytest

from backend.digital_twin import DigitalTwin
from backend.equipment import Equipment
from backend.models import BoxKind, BoxStatus, SimulationStatus
from backend.simulator import Simulator


@pytest.fixture
def twin(tmp_path):
    return DigitalTwin(log_dir=str(tmp_path / "logs"), data_dir=str(tmp_path / "data"),
                       persist_logs=False, demo=True, demo_tasks=False, layout="distribution_center")


@pytest.fixture
def sim(twin):
    simulator = Simulator(twin)
    twin.simulation_status = SimulationStatus.RUNNING
    return simulator


def ticks(sim, n):
    for _ in range(n):
        sim.tick()


def test_the_new_floor_has_one_line_and_a_sorter(twin, tmp_path):
    equipment = twin.equipment
    assert equipment.conveyor.cells == [(19, 15), (20, 15), (21, 15), (22, 15), (23, 15), (24, 15)]
    assert equipment.conveyor.advance_ticks == 20          # 1.5 m at 0.5 m/s, 0.15 s ticks
    assert (equipment.sorter.entry, equipment.sorter.lanes) == ((24, 15), ["dock_4", "dock_5"])
    assert equipment.arm_cells == {"pack_cell_1": (23, 15), "pack_cell_2": (22, 15)}
    classic = DigitalTwin(log_dir=str(tmp_path / "c" / "logs"), data_dir=str(tmp_path / "c" / "data"),
                          persist_logs=False, demo=True, demo_tasks=False)
    assert classic.equipment is None and Equipment.for_floor(classic) is None


def test_a_box_added_on_the_line_rides_it(twin):
    item = twin.add_box(name="ITEM-1", kind="ITEM", sku="SKU-01", quantity=1, weight=0.4, position=(20, 15))
    line_item = twin.equipment.conveyor.item_at((20, 15))
    assert line_item.box_id == item.id and item.status is BoxStatus.DELIVERING
    with pytest.raises(ValueError, match="occupied"):
        twin.add_box(name="ITEM-2", kind="ITEM", position=(20, 15))


def test_items_advance_a_cell_every_twenty_ticks_and_back_up(twin, sim):
    first = twin.add_box(name="ITEM-1", kind="ITEM", weight=0.4, position=(21, 15))
    second = twin.add_box(name="ITEM-2", kind="ITEM", weight=0.4, position=(20, 15))
    twin.equipment.conveyor.item_at((21, 15)).stop_at = (22, 15)   # held for arm 2
    ticks(sim, 19)
    assert (first.position, second.position) == ((21, 15), (20, 15))
    sim.tick()
    assert (first.position, second.position) == ((22, 15), (21, 15))
    ticks(sim, 60)
    assert (first.position, second.position) == ((22, 15), (21, 15))  # the second waits behind the first


def test_an_arm_takes_its_item_and_the_hand_off_is_recorded(twin, sim):
    item = twin.add_box(name="ITEM-1", kind="ITEM", weight=0.4, position=(23, 15), order_id="ORD-1")
    twin.equipment.conveyor.item_at((23, 15)).stop_at = (23, 15)
    ticks(sim, 40)
    assert item.position == (23, 15)                            # waiting for its arm
    box, handoff = twin.equipment.take((23, 15), "robot_01", task_id="task_009")
    assert box is item and twin.equipment.conveyor.item_at((23, 15)) is None
    assert (handoff.from_holder, handoff.to_holder, handoff.order_id, handoff.lost) == \
        ("conveyor", "robot_01", "ORD-1", False)
    event = twin.events.query(event_type="HANDOFF")[-1]
    assert event["task_id"] == "task_009" and event["data"]["receiver_observed"]["present"] is True


def test_a_carton_is_sorted_to_its_order_dock(twin, sim):
    carton = twin.add_box(name="CTN-1", kind="CARTON", weight=2.3, position=(24, 15),
                          destination="dock_5", order_id="ORD-1")
    ticks(sim, 14)                                              # 2 s at the end of the line
    assert carton.position == (25, 15) and twin.equipment.sorter.inside
    ticks(sim, 14)                                              # 2 s through the sorter
    assert twin.warehouse.zone_of_cell(carton.position).key == "dock_5"
    assert carton.status is BoxStatus.DELIVERED
    moves = [(e["data"]["from"], e["data"]["to"]) for e in twin.events.query(event_type="HANDOFF")]
    assert moves == [("conveyor", "sorter"), ("sorter", "dock_5")]
    last = twin.events.query(event_type="HANDOFF")[-1]["data"]
    assert last["giver_reported"]["lane"] == last["receiver_observed"]["dock"] == "dock_5"
    assert [box.id for box in twin.equipment.ship("dock_5")] == [carton.id]
    assert carton.status is BoxStatus.SHIPPED


def test_a_mis_sort_sends_a_carton_to_the_other_dock(twin, sim):
    carton = twin.add_box(name="CTN-1", kind="CARTON", weight=2.3, position=(24, 15), destination="dock_4")
    twin.faults.arm("mis_sort")
    ticks(sim, 28)
    assert twin.warehouse.zone_of_cell(carton.position).key == "dock_5"
    data = twin.events.query(event_type="HANDOFF")[-1]["data"]
    assert (data["giver_reported"]["lane"], data["receiver_observed"]["dock"]) == ("dock_4", "dock_5")


def test_anything_but_a_carton_is_rejected_at_the_sorter(twin, sim):
    item = twin.add_box(name="ITEM-1", kind="ITEM", weight=0.4, position=(24, 15))
    ticks(sim, 14)
    assert item.status is BoxStatus.FAILED and not twin.equipment.conveyor.items
    assert twin.events.query(event_type="HANDOFF")[-1]["data"]["to"] == "sorter_reject"


def test_placing_on_the_line_records_a_hand_off_and_a_loss(twin):
    item = twin.add_box(name="ITEM-1", kind="ITEM", weight=0.4, position=(20, 14))
    handoff = twin.equipment.place(item, (20, 15), "robot_03", task_id="task_004", order_id="ORD-2",
                                   stop_at=(23, 15))
    assert not handoff.lost and twin.equipment.conveyor.item_at((20, 15)).stop_at == (23, 15)
    assert item.status is BoxStatus.DELIVERING and item.position == (20, 15)
    with pytest.raises(ValueError, match="not free"):
        twin.equipment.place(item, (20, 15), "robot_03")
    lost = twin.add_box(name="ITEM-2", kind="ITEM", weight=0.4, position=(20, 14))
    twin.faults.arm("handoff_loss")
    handoff = twin.equipment.place(lost, (19, 15), "robot_03", task_id="task_004")
    assert handoff.lost and lost.status is BoxStatus.FAILED and lost.position == (19, 15)
    assert twin.equipment.conveyor.item_at((19, 15)) is None
    event = twin.events.query(event_type="HANDOFF")[-1]
    assert event["level"] == "WARNING" and event["data"]["giver_reported"]["present"] is True
    assert event["data"]["receiver_observed"] == {"present": False}


def test_a_jam_stops_its_segment_and_everything_behind_it(twin, sim):
    head = twin.add_box(name="ITEM-1", kind="ITEM", weight=0.4, position=(20, 15))
    tail = twin.add_box(name="ITEM-2", kind="ITEM", weight=0.4, position=(19, 15))
    twin.faults.arm("conveyor_jam")
    ticks(sim, 20)
    conveyor = twin.equipment.conveyor
    assert (20, 15) in conveyor.jams and head.position == (20, 15) and tail.position == (19, 15)
    assert conveyor.jam_upstream_of((23, 15)) == (20, 15) and conveyor.jam_upstream_of((19, 15)) is None
    jammed = twin.events.query(event_type="CONVEYOR_JAMMED")
    assert len(jammed) == 1 and jammed[0]["data"]["cell"] == {"x": 20, "y": 15}
    ticks(sim, 100)
    assert head.position == (20, 15) and tail.position == (19, 15)
    twin.equipment.clear_jam((20, 15))
    assert twin.events.query(event_type="CONVEYOR_CLEARED")[-1]["data"]["jammed_ticks"] >= 100
    ticks(sim, 20)
    assert head.position == (21, 15) and tail.position == (20, 15)
    with pytest.raises(ValueError, match="not jammed"):
        twin.equipment.clear_jam((20, 15))


def test_a_failed_order_releases_its_held_items(twin):
    twin.add_box(name="ITEM-1", kind="ITEM", weight=0.4, position=(21, 15), order_id="ORD-3")
    twin.equipment.conveyor.item_at((21, 15)).stop_at = (23, 15)
    assert twin.equipment.release_order("ORD-3") == 1
    assert twin.equipment.conveyor.item_at((21, 15)).stop_at is None


def test_reset_rebuilds_an_empty_line(twin):
    twin.add_box(name="ITEM-1", kind="ITEM", weight=0.4, position=(21, 15))
    twin.reset(demo_tasks=False)
    assert twin.equipment is not None and not twin.equipment.conveyor.items
    assert twin.equipment.to_dict()["conveyor"]["advance_ticks"] == 20
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/bin/python -m pytest -o addopts="" -q backend/test_equipment.py`
Expected: `1 error` — `ModuleNotFoundError: No module named 'backend.equipment'`.

- [ ] **Step 3: The equipment**

**Create** `backend/equipment.py`:

```python
"""Floor equipment (multi-embodiment spec §8): the conveyor line, the sorter,
and a hand-off record for every transfer of a box between two holders.

The line runs from Pick's infeed to the sorter, flowing +x. An item moves one
cell every ceil(CELL_SIZE_M / CONVEYOR_SPEED_MPS / TICK_DT) ticks; a cell
holds one item and an item that can't move waits, so the line backs up. An
item tagged for an arm stops on that arm's working cell until the arm takes
it; everything else rides to the last cell. There the sorter takes a carton
in after SORTER_TRANSFER_S and, SORTER_TRANSFER_S later, drops it on a free
cell of its order's dock; anything that isn't a carton is rejected. A jammed
segment stops advancing, and everything behind it backs up.

Each hand-off keeps what the giver claims (`giver_reported`) and what the
receiver saw (`receiver_observed`) and is logged as a HANDOFF event, which is
what the evaluation's hand-off and sort checks read. Nothing here takes a
lock: the simulator tick (and API callers) hold twin.lock.
"""
from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Any, Deque, Dict, List, Optional, Sequence, Tuple

from .embodiment import seconds_to_ticks
from .models import CONFIG, BoxKind, BoxStatus, Cell, EventType, LogCategory, LogLevel, cell_dict


@dataclass
class LineItem:
    """A box riding the line (or passing through the sorter)."""

    box_id: str
    order_id: Optional[str] = None
    task_id: Optional[str] = None
    stop_at: Optional[Cell] = None   # an arm's working cell it waits on
    ticks: int = 0                   # ticks on its current cell (or in the sorter)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["stop_at"] = cell_dict(self.stop_at)
        return data


@dataclass
class Handoff:
    handoff_id: str
    box_id: str
    order_id: Optional[str]
    from_holder: str
    to_holder: str
    cell: Cell
    tick: int
    giver_reported: Dict[str, Any] = field(default_factory=dict)
    receiver_observed: Dict[str, Any] = field(default_factory=dict)
    task_id: Optional[str] = None

    @property
    def lost(self) -> bool:
        """The giver let go, but the receiver never got it."""
        return bool(self.giver_reported.get("present")) and self.receiver_observed.get("present") is False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "handoff_id": self.handoff_id, "box_id": self.box_id, "order_id": self.order_id,
            "from": self.from_holder, "to": self.to_holder, "cell": cell_dict(self.cell), "tick": self.tick,
            "giver_reported": dict(self.giver_reported), "receiver_observed": dict(self.receiver_observed),
            "task_id": self.task_id,
        }


class Conveyor:
    """One line of cells, upstream first."""

    def __init__(self, cells: Sequence[Cell]) -> None:
        self.cells: List[Cell] = list(cells)
        self._index = {cell: i for i, cell in enumerate(self.cells)}
        self.items: Dict[Cell, LineItem] = {}
        self.jams: Dict[Cell, int] = {}  # jammed cell -> the tick it jammed

    @property
    def advance_ticks(self) -> int:
        return seconds_to_ticks(CONFIG["CELL_SIZE_M"] / CONFIG["CONVEYOR_SPEED_MPS"])

    def __contains__(self, cell: Any) -> bool:
        return cell in self._index

    def item_at(self, cell: Cell) -> Optional[LineItem]:
        return self.items.get(cell)

    def is_free(self, cell: Cell) -> bool:
        return cell in self._index and cell not in self.items

    def next_cell(self, cell: Cell) -> Optional[Cell]:
        index = self._index[cell] + 1
        return self.cells[index] if index < len(self.cells) else None

    def load(self, cell: Cell, item: LineItem) -> None:
        if cell not in self._index:
            raise ValueError(f"({cell[0]},{cell[1]}) is not a conveyor cell")
        if cell in self.items:
            raise ValueError(f"Conveyor cell ({cell[0]},{cell[1]}) is occupied")
        item.ticks = 0
        self.items[cell] = item

    def unload(self, cell: Cell) -> LineItem:
        if cell not in self.items:
            raise KeyError(f"Conveyor cell ({cell[0]},{cell[1]}) is empty")
        return self.items.pop(cell)

    def jam_upstream_of(self, cell: Cell) -> Optional[Cell]:
        """A jammed segment at or upstream of `cell`, if any: while there is
        one, nothing new can reach `cell`."""
        limit = self._index.get(cell, len(self.cells) - 1)
        return next((jam for jam in self.cells[:limit + 1] if jam in self.jams), None)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "cells": [cell_dict(cell) for cell in self.cells],
            "items": [{"cell": cell_dict(cell), **item.to_dict()} for cell, item in sorted(self.items.items())],
            "jams": [{"cell": cell_dict(cell), "since_tick": tick} for cell, tick in sorted(self.jams.items())],
            "advance_ticks": self.advance_ticks,
        }


class Sorter:
    """Takes cartons off the end of the line and diverts each to a dock."""

    def __init__(self, entry: Cell, cell: Cell, lanes: Sequence[str]) -> None:
        self.entry = entry            # the last conveyor cell it takes from
        self.cell = cell              # where a carton is while it is inside
        self.lanes: List[str] = list(lanes)
        self.inside: List[LineItem] = []

    def to_dict(self) -> Dict[str, Any]:
        return {"entry": cell_dict(self.entry), "lanes": list(self.lanes),
                "inside": [item.to_dict() for item in self.inside]}


class Equipment:
    """The conveyor, the sorter and the hand-off log of one floor."""

    def __init__(self, twin: Any, conveyor: Conveyor, sorter: Sorter, arm_cells: Dict[str, Cell]) -> None:
        self.twin = twin
        self.conveyor = conveyor
        self.sorter = sorter
        #: Pack-cell zone key -> the conveyor cell its arm works.
        self.arm_cells = dict(arm_cells)
        self.handoffs: Deque[Handoff] = deque(maxlen=CONFIG["MAX_EVENTS_IN_MEMORY"])
        self._handoff_seq = 0

    @classmethod
    def for_floor(cls, twin: Any) -> Optional["Equipment"]:
        """The floor's equipment, from its layout; None on a floor with no
        conveyor and sorter (classic)."""
        zones = twin.warehouse.zones
        line, sorter = zones.get("conveyor"), zones.get("sorter")
        if line is None or sorter is None:
            return None
        cells = sorted(line.cells) if line.attributes.get("direction") == "+x" else list(line.cells)
        entry = cells[-1]
        arm_cells = {key: zone.attributes["conveyor_cell"] for key, zone in zones.items()
                     if "conveyor_cell" in zone.attributes}
        return cls(twin, Conveyor(cells), Sorter(entry, (entry[0] + 1, entry[1]), sorter.attributes["chutes"]),
                   arm_cells)

    # ---- hand-offs ------------------------------------------------------- #
    def record(self, box: Any, giver: str, receiver: str, cell: Cell, giver_reported: Dict[str, Any],
               receiver_observed: Dict[str, Any], task_id: Optional[str] = None,
               order_id: Optional[str] = None) -> Handoff:
        """Record (and announce) one transfer of `box` from `giver` to `receiver`."""
        self._handoff_seq += 1
        handoff = Handoff(
            handoff_id=f"HO-{self._handoff_seq:05d}", box_id=box.id, order_id=order_id or box.order_id,
            from_holder=giver, to_holder=receiver, cell=tuple(cell), tick=self.twin.tick_count,
            giver_reported=dict(giver_reported), receiver_observed=dict(receiver_observed), task_id=task_id,
        )
        self.handoffs.append(handoff)
        note = " — the receiver never got it" if handoff.lost else ""
        self.twin.events.emit(
            EventType.HANDOFF,
            f"{box.name}: {giver} → {receiver} at ({cell[0]},{cell[1]}){note}",
            category=LogCategory.OPERATIONS,
            level=LogLevel.WARNING if handoff.lost else LogLevel.INFO,
            task_id=task_id,
            box_id=box.id,
            position=cell_dict(cell),
            data=handoff.to_dict(),
        )
        return handoff

    def place(self, box: Any, cell: Cell, giver: str, task_id: Optional[str] = None,
              order_id: Optional[str] = None, stop_at: Optional[Cell] = None) -> Handoff:
        """`giver` (a robot or operator id) puts `box` on conveyor `cell`.

        With a HANDOFF_LOSS fault the box never arrives: the giver still
        reports it placed, the conveyor observes nothing, and the box ends
        FAILED on that cell, off the line."""
        if not self.conveyor.is_free(cell):
            raise ValueError(f"Conveyor cell ({cell[0]},{cell[1]}) is not free")
        box.position = tuple(cell)
        box.assigned_robot = None
        reported = {"present": True, "weight_kg": box.declared_weight_kg}
        if self.twin.faults.roll("handoff_loss"):
            box.set_status(BoxStatus.FAILED)
            observed: Dict[str, Any] = {"present": False}
        else:
            self.conveyor.load(tuple(cell), LineItem(box.id, order_id or box.order_id, task_id, stop_at))
            box.set_status(BoxStatus.DELIVERING)
            observed = {"present": True, "weight_kg": box.true_weight_kg}
        return self.record(box, giver, "conveyor", cell, reported, observed, task_id, order_id)

    def take(self, cell: Cell, receiver: str, task_id: Optional[str] = None) -> Tuple[Any, Handoff]:
        """`receiver` (an arm) takes the item on conveyor `cell`."""
        item = self.conveyor.unload(tuple(cell))
        box = self.twin.find_box(item.box_id)
        handoff = self.record(box, "conveyor", receiver, cell, {"present": True, "weight_kg": box.declared_weight_kg},
                              {"present": True, "weight_kg": box.true_weight_kg}, task_id or item.task_id,
                              item.order_id)
        return box, handoff

    def release_order(self, order_id: str) -> int:
        """Stop holding an order's items for its arm (the order failed): they
        ride on to the sorter, which rejects them. Returns how many."""
        released = 0
        for item in self.conveyor.items.values():
            if item.order_id == order_id and item.stop_at is not None:
                item.stop_at = None
                released += 1
        return released

    # ---- jams ------------------------------------------------------------ #
    def jam(self, cell: Cell) -> None:
        cell = tuple(cell)
        if cell not in self.conveyor:
            raise ValueError(f"({cell[0]},{cell[1]}) is not a conveyor cell")
        if cell in self.conveyor.jams:
            return
        self.conveyor.jams[cell] = self.twin.tick_count
        self.twin.events.emit(
            EventType.CONVEYOR_JAMMED,
            f"Conveyor jammed at ({cell[0]},{cell[1]})",
            category=LogCategory.OPERATIONS,
            level=LogLevel.WARNING,
            position=cell_dict(cell),
            data={"cell": cell_dict(cell)},
        )

    def clear_jam(self, cell: Cell) -> None:
        cell = tuple(cell)
        if cell not in self.conveyor.jams:
            raise ValueError(f"Conveyor cell ({cell[0]},{cell[1]}) is not jammed")
        since = self.conveyor.jams.pop(cell)
        for item in self.conveyor.items.values():
            item.ticks = 0  # the line restarts from standstill
        self.twin.events.emit(
            EventType.CONVEYOR_CLEARED,
            f"Conveyor jam at ({cell[0]},{cell[1]}) cleared",
            category=LogCategory.OPERATIONS,
            position=cell_dict(cell),
            data={"cell": cell_dict(cell), "jammed_ticks": self.twin.tick_count - since},
        )

    # ---- docks ----------------------------------------------------------- #
    def free_dock_cell(self, dock: str) -> Optional[Cell]:
        """A cell of `dock` with nothing waiting on it (shipped boxes are gone)."""
        taken = {box.position for box in self.twin.boxes.values() if box.status is not BoxStatus.SHIPPED}
        zone = self.twin.warehouse.zones[dock]
        return next((cell for cell in zone.cells if cell not in taken), None)

    def ship(self, dock: str) -> List[Any]:
        """An outbound truck leaves `dock`: every box delivered there is SHIPPED."""
        cells = set(self.twin.warehouse.zones[dock].cells)
        shipped = [box for box in self.twin.boxes.values()
                   if box.position in cells and box.status is BoxStatus.DELIVERED]
        for box in shipped:
            box.set_status(BoxStatus.SHIPPED)
        if shipped:
            self.twin.logger.info(LogCategory.OPERATIONS, f"Truck left {dock} with {len(shipped)} box(es)",
                                  data={"dock": dock, "box_ids": [box.id for box in shipped]})
        return shipped

    # ---- the tick -------------------------------------------------------- #
    def tick(self) -> None:
        """Advance the line, then the sorter (Simulator.tick, after robots act)."""
        self._advance_line()
        self._run_sorter()

    def _advance_line(self) -> None:
        conveyor = self.conveyor
        for cell in reversed(conveyor.cells):  # head first, so the items behind can follow
            item = conveyor.items.get(cell)
            if item is None:
                continue
            item.ticks += 1
            if cell in conveyor.jams or item.stop_at == cell or item.ticks < conveyor.advance_ticks:
                continue
            nxt = conveyor.next_cell(cell)
            if nxt is None or not conveyor.is_free(nxt):
                continue  # the end of the line (the sorter decides), or backed up
            if self.twin.faults.roll("conveyor_jam"):
                self.jam(cell)
                continue
            conveyor.items[nxt] = conveyor.items.pop(cell)
            item.ticks = 0
            box = self.twin.find_box(item.box_id)
            if box is not None:
                box.position = nxt
                box.touch()

    def _run_sorter(self) -> None:
        twin, sorter, conveyor = self.twin, self.sorter, self.conveyor
        transfer = seconds_to_ticks(CONFIG["SORTER_TRANSFER_S"])
        for item in list(sorter.inside):
            item.ticks += 1
            if item.ticks < transfer:
                continue
            box = twin.find_box(item.box_id)
            lane = box.destination if box.destination in sorter.lanes else sorter.lanes[0]
            dock = lane
            if twin.faults.roll("mis_sort"):
                dock = next(other for other in sorter.lanes if other != lane)
            cell = self.free_dock_cell(dock)
            if cell is None:
                continue  # the dock is full: the carton waits in the sorter
            sorter.inside.remove(item)
            box.position = cell
            box.set_status(BoxStatus.DELIVERED)
            self.record(box, "sorter", dock, cell, {"present": True, "lane": lane},
                        {"present": True, "dock": dock}, item.task_id, item.order_id)
        item = conveyor.items.get(sorter.entry)
        if item is None or sorter.entry in conveyor.jams or item.stop_at == sorter.entry or item.ticks < transfer:
            return
        conveyor.unload(sorter.entry)
        box = twin.find_box(item.box_id)
        if box is None:
            return
        box.position = sorter.cell
        if box.kind is BoxKind.CARTON:
            item.ticks = 0
            sorter.inside.append(item)
            self.record(box, "conveyor", "sorter", sorter.entry, {"present": True},
                        {"present": True, "weight_kg": box.true_weight_kg}, item.task_id, item.order_id)
        else:
            box.set_status(BoxStatus.FAILED)
            self.record(box, "conveyor", "sorter_reject", sorter.entry, {"present": True},
                        {"present": True, "rejected": f"a {box.kind.value} is not a carton"},
                        item.task_id, item.order_id)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "conveyor": self.conveyor.to_dict(),
            "sorter": self.sorter.to_dict(),
            "arm_cells": {key: cell_dict(cell) for key, cell in self.arm_cells.items()},
            "handoffs": len(self.handoffs),
        }
```

- [ ] **Step 4: Its settings and events**

**Replace in** `backend/models.py`:

```python
    "TOTE_TARE_KG": 1.5,
    # Injected faults (backend/faults.py), all 0.0-1.0 and off by default like
```

with:

```python
    "TOTE_TARE_KG": 1.5,
    # The conveyor line and sorter (backend/equipment.py): belt speed, and how
    # long the sorter takes to pull a carton in and to drop it on its dock.
    "CONVEYOR_SPEED_MPS": 0.5,
    "SORTER_TRANSFER_S": 2.0,
    # Injected faults (backend/faults.py), all 0.0-1.0 and off by default like
```

**Replace in** `backend/models.py`:

```python
    ROBOT_SAFETY_RESUMED = "ROBOT_SAFETY_RESUMED"
    # A person finished walking from one zone to another (backend/people.py).
    PERSON_MOVED = "PERSON_MOVED"


class ActionType(str, enum.Enum):
```

with:

```python
    ROBOT_SAFETY_RESUMED = "ROBOT_SAFETY_RESUMED"
    # A person finished walking from one zone to another (backend/people.py).
    PERSON_MOVED = "PERSON_MOVED"
    # Floor equipment (backend/equipment.py): a box changed hands (robot,
    # conveyor, arm, sorter, dock), and a conveyor segment jammed or was cleared.
    HANDOFF = "HANDOFF"
    CONVEYOR_JAMMED = "CONVEYOR_JAMMED"
    CONVEYOR_CLEARED = "CONVEYOR_CLEARED"


class ActionType(str, enum.Enum):
```

- [ ] **Step 5: The twin owns it and the tick runs it**

**Replace in** `backend/digital_twin.py`:

```python
from .embodiment import AIR, GROUND, HOVER_CLEARANCE_M, LAYERS, MobilityProfile
from .faults import FaultInjector
```

with:

```python
from .embodiment import AIR, GROUND, HOVER_CLEARANCE_M, LAYERS, MobilityProfile
from .equipment import Equipment, LineItem
from .faults import FaultInjector
```

**Replace in** `backend/digital_twin.py`:

```python
        self.stock = StockLedger(self.warehouse)
        self.agents: Dict[str, Agent] = {}
```

with:

```python
        self.stock = StockLedger(self.warehouse)
        # The conveyor line, the sorter and their hand-off log (backend/
        # equipment.py); None on a floor without them (classic).
        self.equipment: Optional[Equipment] = Equipment.for_floor(self)
        self.agents: Dict[str, Agent] = {}
```

**Replace in** `backend/digital_twin.py`:

```python
                    raise ValueError(f"Position {spot} is outside the warehouse")
            else:
```

with:

```python
                    raise ValueError(f"Position {spot} is outside the warehouse")
                on_line = self.equipment is not None and spot in self.equipment.conveyor
                if on_line and not self.equipment.conveyor.is_free(spot):
                    raise ValueError(f"Conveyor cell {spot} is occupied")
            else:
```

**Replace in** `backend/digital_twin.py`:

```python
                box.slot = slot
            self.boxes[box.id] = box
```

with:

```python
                box.slot = slot
            elif self.equipment is not None and spot in self.equipment.conveyor:
                self.equipment.conveyor.load(spot, LineItem(box.id, order_id=order_id))  # it rides the line
                box.status = BoxStatus.DELIVERING
            self.boxes[box.id] = box
```

**Replace in** `backend/digital_twin.py`:

```python
            self.stock = StockLedger(self.warehouse)
            for key in self.statistics:
```

with:

```python
            self.stock = StockLedger(self.warehouse)
            self.equipment = Equipment.for_floor(self)
            for key in self.statistics:
```

**Replace in** `backend/simulator.py`:

```python
                        twin.tasks.fail_task(task, f"Controller error: {exc}")

            self._detect_collisions()
            self._auto_charge()
```

with:

```python
                        twin.tasks.fail_task(task, f"Controller error: {exc}")

            if twin.equipment is not None:
                twin.equipment.tick()  # the conveyor and sorter move after robots place items
            self._detect_collisions()
            self._auto_charge()
```

- [ ] **Step 6: Run the new tests**

Run: `.venv/bin/python -m pytest -o addopts="" -q backend/test_equipment.py`
Expected: `11 passed`.

- [ ] **Step 7: Run the full suite**

Run: `.venv/bin/python -m pytest -o addopts="" -q`
Expected: `2 failed, 508 passed`.

- [ ] **Step 8: Commit**

```bash
git add backend/equipment.py backend/models.py backend/digital_twin.py backend/simulator.py \
        backend/test_equipment.py
git commit -m "feat: conveyor, sorter and hand-off records

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Job steps with physical duration

The simulator gains the §5.4 steps — `LIFT_TO`/`LOWER` (height difference ÷ `lift_speed_mps`), `TAKEOFF`/`LAND` (`takeoff_s`/`land_s`), `GRASP`/`PLACE` (`grasp_s`/`place_s`), `PLACE_ON_CONVEYOR` (`place_s`) and `WAIT_CLEAR` (until its condition clears) — built on plan 1a's `step_ticks`/`lift_ticks`, `HOVER_CLEARANCE_M` and `set_robot_layer`. `SCAN` comes with the drone jobs (Task 11). One framework, `Simulator._act_step`, runs every step: hold for a physical wait, begin (check the step can happen and how long it takes, emit `ROBOT_STEP`), wait out the duration, finish (do it and emit `LIFTED`/`LOWERED`/`PLACED`/`BOX_PICKED`/`HANDOFF`). A step that can't physically happen fails its task with the reason. Two faults land here (`GRASP_FAIL_RISK`: an arm or picker retries up to twice; `WRONG_LEVEL_RISK`: a forklift places a level off and reports the level it was sent to), and an arm pauses (`CONVEYOR_JAMMED`) while the line is jammed at or upstream of its working cell. Plan 1a's drone gaps close: a flying drone keeps its altitude when `set_robot_layer(AIR)` gets none, and `reset_robot` refuses to land a drone when no pad cell is free.

On the new floor, the existing `PICK`/`DELIVER` actions also emit `ROBOT_STEP`, carry the load's weights in `BOX_PICKED`, record a hand-off at a staging area, and — for the new job types only — take the body's `grasp_s`/`place_s` instead of `PICK_TICKS`/`DELIVER_TICKS`. Nothing changes for a robot without a floor profile.

**Files:**
- Modify: `backend/models.py` (eight `ActionType` steps; `Action.level`, `.slot_id`, `.params`; `EventType.ROBOT_STEP`, `LIFTED`, `LOWERED`, `PLACED`)
- Modify: `backend/robot.py` (`activity`, `lift_height_m`)
- Modify: `backend/equipment.py` (`place` also clears `assigned_task`)
- Modify: `backend/simulator.py` (the step framework and handlers; `PICK`/`DELIVER` on the new floor)
- Modify: `backend/digital_twin.py` (`set_robot_layer` keeps a flying drone's altitude; `reset_robot` needs a free pad for a drone)
- Test: `backend/test_job_steps.py` (create)

**Interfaces:**
- Consumes (Tasks 1–3): `reach_ok`; `twin.faults.roll`; `goods.release`, `goods.store`, `goods.take_units`, `carton_weight_kg`; `twin.equipment.place/take/record`, `.conveyor.item_at/is_free/jam_upstream_of`, `.arm_cells`; plan 1a's `step_ticks`, `lift_ticks`, `HOVER_CLEARANCE_M`, `DigitalTwin.set_robot_layer`, `Warehouse.fixed_stations`, `people.people_in/people_at`, `Simulator._safety_wait/_safety_resume`.
- Produces:
  - `ActionType.LIFT_TO`, `LOWER`, `TAKEOFF`, `LAND`, `GRASP`, `PLACE`, `PLACE_ON_CONVEYOR`, `WAIT_CLEAR`; `Action(type, description, target=None, target_name=None, box_id=None, started=False, done=False, level=None, slot_id=None, params={})` (all three new fields in `to_dict`/`from_dict`; `params` holds JSON values, cells as `[x, y]`).
  - Step arguments the planners of Tasks 8–11 use: `LIFT_TO(level, slot_id)` (or `params["height_m"]`); `TAKEOFF(params["altitude_m"])`; `GRASP(box_id, slot_id)` from a slot, `GRASP(box_id)` from the robot's cell, `GRASP(params={"unit_from": tote_id, "order_id"})` one unit out of a tote within reach (creates an ITEM), `GRASP(params={"from_conveyor": [x, y], "order_id"})` an order's item off the line; `PLACE(box_id, slot_id)` into a slot, `PLACE(params={"into_carton": order_id, "lane"})` into the order's carton (created at the robot's cell on the first item; the item is consumed); `PLACE_ON_CONVEYOR(target=cell, params={"stop_at": [x, y]})` what it holds, or `params={"carton_for": order_id}` the order's carton; `WAIT_CLEAR(params={"reason": "CONVEYOR_OCCUPIED" | "AWAITING_ITEM", "cell": [x, y], "order_id"})`.
  - `EventType.ROBOT_STEP` (data `{"step", "zone", "people_present": [operator ids], "supervision_ok", "level", "true_weight_kg", "declared_weight_kg", "embodiment_class", "layer"}`), `LIFTED` (`level`, `height_m`, `slot`), `LOWERED`, `PLACED` (`slot`, `level` reported, `true_level` actual; or `into`, `items`, `order_id`) — the last three also carry `embodiment_class`, `max_shelf_level`, `max_payload_kg` and the box's `kind`, `true_weight_kg`, `declared_weight_kg`. `BOX_PICKED` data on the new floor: `kind`, `true_weight_kg`, `declared_weight_kg`, `max_payload_kg`, `max_shelf_level`, `embodiment_class`.
  - `Robot.activity: Optional[str]` (the current action's type), `Robot.lift_height_m: float`.
  - `simulator.TIMED_BY_TICKS`, `STEP_ACTIONS`, `WAIT_CLEAR_REASONS`, `GRASPING_CLASSES`, `GRASP_RETRIES = 2`; `Simulator._act_step`, `_step_hold(robot, task, action) -> bool`, `_emit_step(robot, task, action)`, `_step_event(robot, task, step, description, level=None, box=None, zone=None, present=None)`, `_people_near(robot)`, `_arm_work_cell(robot)`, `_handling_ticks(robot, task, step, classic_ticks)`.

- [ ] **Step 1: Write the failing tests**

**Create** `backend/test_job_steps.py`:

```python
"""Job steps with physical duration (multi-embodiment spec §5.4): lifting,
grasping and placing in slots, cartons and on the conveyor, take-off and
landing, and the events each step leaves in the task log."""
import pytest

from backend import goods
from backend.digital_twin import DigitalTwin
from backend.embodiment import lift_ticks, step_ticks
from backend.models import (
    Action, ActionType, BoxKind, BoxStatus, RobotStatus, SimulationStatus, TaskStatus, TaskType,
)
from backend.simulator import Simulator
from backend.task_manager import Task


@pytest.fixture
def twin(tmp_path):
    return DigitalTwin(log_dir=str(tmp_path / "logs"), data_dir=str(tmp_path / "data"),
                       persist_logs=False, demo=True, demo_tasks=False, layout="distribution_center")


@pytest.fixture
def sim(twin):
    simulator = Simulator(twin)
    twin.simulation_status = SimulationStatus.RUNNING
    return simulator


def attach(twin, robot, actions, task_type=TaskType.MOVE_ROBOT):
    """Hand `robot` a task whose plan is exactly `actions` (then COMPLETE)."""
    task = Task(twin.ids.next("task"), task_type, robot_id=robot.id)
    task.actions = list(actions) + [Action(ActionType.COMPLETE, "Complete task")]
    task.record(TaskStatus.ASSIGNED, "Assigned a hand-built plan")
    twin.tasks.tasks[task.id] = task
    robot.current_task = task.id
    return task


def run(sim, task, max_ticks=1000):
    for tick in range(1, max_ticks + 1):
        sim.tick()
        if task.is_terminal:
            return tick
    raise AssertionError(f"{task.id} still {task.status.value} after {max_ticks} ticks")


def events(twin, task, kind):
    return [e for e in twin.events.query(task_id=task.id, event_type=kind)]


@pytest.fixture
def forklift(twin):
    return twin.add_robot(name="PF1200-205", asset_id="AST-000205", position=(8, 3))


def test_a_forklift_lifts_grasps_a_pallet_and_lowers(twin, sim, forklift):
    pallet = twin.add_box(name="PAL-1", kind="PALLET", sku="SKU-01", quantity=40, weight=600.0,
                          true_weight_kg=640.0, slot="PR-08-02-3")
    task = attach(twin, forklift, [
        Action(ActionType.LIFT_TO, "Lift to level 3", level=3, slot_id="PR-08-02-3"),
        Action(ActionType.GRASP, "Grasp PAL-1", box_id=pallet.id, slot_id="PR-08-02-3"),
        Action(ActionType.LOWER, "Lower"),
    ])
    lift = lift_ticks(forklift.mobility, 0.0, 3.0)
    grasp = step_ticks(forklift.mobility, "GRASP")
    assert (lift, grasp) == (67, 20)                     # 3 m at 0.3 m/s; a 3 s fork engage
    ticks = run(sim, task)
    assert ticks == 3 + lift + grasp + lift + 1          # each step's start tick, its duration, then COMPLETE
    assert task.status is TaskStatus.COMPLETED
    assert forklift.carrying_box == pallet.id and forklift.lift_height_m == 0.0
    assert pallet.status is BoxStatus.CARRIED and pallet.slot is None
    assert twin.stock.location("PR-08-02-3") is None     # the pallet has left storage
    lifted = events(twin, task, "LIFTED")[0]["data"]
    assert (lifted["level"], lifted["height_m"], lifted["max_shelf_level"]) == (3, 3.0, 4)
    picked = events(twin, task, "BOX_PICKED")[0]["data"]
    assert (picked["true_weight_kg"], picked["declared_weight_kg"], picked["max_payload_kg"]) == (640.0, 600.0, 1200.0)
    steps = [e["data"]["step"] for e in events(twin, task, "ROBOT_STEP")]
    assert steps == ["LIFT_TO", "GRASP", "LOWER"]
    step = events(twin, task, "ROBOT_STEP")[1]["data"]
    assert (step["zone"], step["level"], step["embodiment_class"], step["people_present"]) == \
        ("pallet_aisle_1", None, "FORKLIFT", [])
    assert events(twin, task, "LOWERED")[0]["data"]["true_weight_kg"] == 640.0


def test_a_forklift_places_a_pallet_in_a_slot(twin, sim, forklift):
    pallet = twin.add_box(name="PAL-1", kind="PALLET", sku="SKU-01", quantity=40, weight=600.0, position=(8, 3))
    pallet.set_status(BoxStatus.CARRIED)
    forklift.carrying_box = pallet.id
    task = attach(twin, forklift, [
        Action(ActionType.LIFT_TO, "Lift to level 2", level=2, slot_id="PR-08-02-2"),
        Action(ActionType.PLACE, "Place PAL-1", box_id=pallet.id, slot_id="PR-08-02-2"),
        Action(ActionType.LOWER, "Lower"),
    ])
    run(sim, task)
    assert task.status is TaskStatus.COMPLETED and forklift.carrying_box is None
    assert (pallet.status, pallet.slot, pallet.position) == (BoxStatus.STORED, "PR-08-02-2", (8, 2))
    assert twin.stock.location("PR-08-02-2").recorded_qty == 40
    placed = events(twin, task, "PLACED")[0]["data"]
    assert (placed["slot"], placed["level"], placed["true_level"]) == ("PR-08-02-2", 2, 2)


def test_a_wrong_level_placement_reports_the_level_it_was_sent_to(twin, sim, forklift):
    pallet = twin.add_box(name="PAL-1", kind="PALLET", sku="SKU-01", quantity=40, weight=600.0, position=(8, 3))
    pallet.set_status(BoxStatus.CARRIED)
    forklift.carrying_box = pallet.id
    twin.faults.arm("wrong_level")
    task = attach(twin, forklift, [
        Action(ActionType.LIFT_TO, "Lift to level 2", level=2, slot_id="PR-08-02-2"),
        Action(ActionType.PLACE, "Place PAL-1", box_id=pallet.id, slot_id="PR-08-02-2"),
    ])
    run(sim, task)
    placed = events(twin, task, "PLACED")[0]["data"]
    assert (placed["level"], placed["true_level"]) == (2, 3)
    assert (pallet.slot, pallet.true_slot) == ("PR-08-02-2", "PR-08-02-3")
    assert goods.physical_qty(twin, "PR-08-02-2") == 0 and goods.physical_qty(twin, "PR-08-02-3") == 40


def test_steps_fail_their_task_when_the_body_cannot_do_them(twin, sim, forklift):
    amr = twin.add_robot(name="TR50-201", asset_id="AST-000201", position=(10, 13))
    tote = twin.add_box(name="TOTE-1", kind="TOTE", sku="SKU-02", quantity=10, weight=8.0, slot="TS-10-12-2")
    reach = attach(twin, amr, [Action(ActionType.LIFT_TO, "Lift to level 2", level=2, slot_id=tote.slot)])
    run(sim, reach)
    assert reach.status is TaskStatus.FAILED and "level 2 is out of its reach (highest level 1)" in reach.error
    pallet = twin.add_box(name="PAL-1", kind="PALLET", sku="SKU-01", quantity=40, weight=600.0, slot="PR-08-02-3")
    forks = attach(twin, forklift, [Action(ActionType.GRASP, "Grasp PAL-1", box_id=pallet.id, slot_id=pallet.slot)])
    run(sim, forks)
    assert forks.status is TaskStatus.FAILED and "forks are at 0.0 m" in forks.error
    wrong = attach(twin, forklift, [
        Action(ActionType.LIFT_TO, "Lift to level 1", level=1, slot_id="PR-08-02-1"),
        Action(ActionType.GRASP, "Grasp PAL-1", box_id=pallet.id, slot_id="PR-08-02-1"),
    ])
    run(sim, wrong)
    assert wrong.status is TaskStatus.FAILED and "PAL-1 is not in slot PR-08-02-1" in wrong.error
    odd = attach(twin, forklift, [Action(ActionType.WAIT_CLEAR, "Wait", params={"reason": "RAIN", "cell": [20, 15]})])
    run(sim, odd)
    assert odd.status is TaskStatus.FAILED and "can't wait for 'RAIN'" in odd.error


def test_new_job_types_pick_and_deliver_at_their_body_speed(twin, sim, forklift):
    new_kind = Task("task_900", TaskType.MOVE_ROBOT)
    legacy = Task("task_901", TaskType.PICK_AND_DELIVER)
    assert sim._handling_ticks(forklift, new_kind, "GRASP", 4) == 20      # grasp_s 3.0
    assert sim._handling_ticks(forklift, legacy, "GRASP", 4) == 4         # PICK_TICKS, as always
    drone = twin.add_robot(name="IX2-208", asset_id="AST-000208")
    assert sim._handling_ticks(drone, new_kind, "PLACE", 4) == 4          # no place_s: the old timing


def test_an_arm_packs_an_order_and_puts_the_carton_on_the_line(twin, sim):
    arm = twin.add_robot(name="CX10-210", asset_id="AST-000210")
    item = twin.add_box(name="ITEM-1", kind="ITEM", sku="SKU-01", quantity=1, weight=0.5,
                        true_weight_kg=0.6, position=(21, 15), order_id="ORD-1")
    twin.equipment.conveyor.item_at((21, 15)).stop_at = (23, 15)
    task = attach(twin, arm, [
        Action(ActionType.WAIT_CLEAR, "Wait for ORD-1's item", params={
            "reason": "AWAITING_ITEM", "cell": [23, 15], "order_id": "ORD-1"}),
        Action(ActionType.GRASP, "Grasp the item", params={"from_conveyor": [23, 15], "order_id": "ORD-1"}),
        Action(ActionType.PLACE, "Pack it", params={"into_carton": "ORD-1", "lane": "dock_5"}),
        Action(ActionType.WAIT_CLEAR, "Wait for a free cell", params={"reason": "CONVEYOR_OCCUPIED",
                                                                       "cell": [23, 15]}),
        Action(ActionType.PLACE_ON_CONVEYOR, "Carton on the line", target=(23, 15),
               params={"carton_for": "ORD-1"}),
    ], task_type=TaskType.MOVE_ROBOT)
    run(sim, task)
    assert task.status is TaskStatus.COMPLETED, task.error
    assert item.id not in twin.boxes                        # consumed into the carton
    carton = next(box for box in twin.boxes.values() if box.kind is BoxKind.CARTON)
    assert (carton.order_id, carton.destination, carton.quantity) == ("ORD-1", "dock_5", 1)
    assert (carton.declared_weight_kg, carton.true_weight_kg) == (0.8, 0.9)   # the items plus 0.3 kg
    assert twin.equipment.conveyor.item_at((23, 15)).box_id == carton.id
    moves = [(e["data"]["from"], e["data"]["to"]) for e in events(twin, task, "HANDOFF")]
    assert moves == [("conveyor", arm.id), (arm.id, "conveyor")]
    for _ in range(60):
        sim.tick()
    assert twin.warehouse.zone_of_cell(carton.position).key == "dock_5"


def test_a_picker_moves_units_from_a_tote_onto_the_line(twin, sim):
    picker = twin.add_robot(name="PK30-203", asset_id="AST-000203", position=(20, 14))
    tote = twin.add_box(name="TOTE-1", kind="TOTE", sku="SKU-02", quantity=5, weight=6.5, slot="TS-10-12-1")
    tote.position = (19, 14)                                # delivered to Pick 1's tote drop
    tote.set_status(BoxStatus.DELIVERED)
    plan = []
    for _ in range(2):
        plan += [
            Action(ActionType.GRASP, "Pick a unit", params={"unit_from": tote.id, "order_id": "ORD-2"}),
            Action(ActionType.WAIT_CLEAR, "Wait for the infeed", params={"reason": "CONVEYOR_OCCUPIED",
                                                                         "cell": [20, 15]}),
            Action(ActionType.PLACE_ON_CONVEYOR, "Unit on the line", target=(20, 15), params={"stop_at": [23, 15]}),
        ]
    task = attach(twin, picker, plan)
    run(sim, task)
    assert task.status is TaskStatus.COMPLETED, task.error
    items = [box for box in twin.boxes.values() if box.kind is BoxKind.ITEM]
    assert len(items) == 2 and all(item.order_id == "ORD-2" and item.sku == "SKU-02" for item in items)
    assert all(item.declared_weight_kg == 1.0 for item in items)             # (6.5 - 1.5 kg tote) / 5
    assert tote.quantity == 3 and twin.stock.location("TS-10-12-1").true_qty == 3
    placed = [e for e in events(twin, task, "HANDOFF") if e["data"]["to"] == "conveyor"]
    assert len(placed) == 2 and all(e["data"]["from"] == picker.id for e in placed)


def test_a_missed_grasp_is_retried_twice_then_fails(twin, sim):
    picker = twin.add_robot(name="PK30-203", asset_id="AST-000203", position=(20, 14))
    tote = twin.add_box(name="TOTE-1", kind="TOTE", sku="SKU-02", quantity=5, weight=6.5, slot="TS-10-12-1")
    tote.position = (19, 14)
    grasp = [Action(ActionType.GRASP, "Pick a unit", params={"unit_from": tote.id})]
    twin.faults.arm("grasp_fail", count=2)
    retried = attach(twin, picker, grasp)
    run(sim, retried)
    assert retried.status is TaskStatus.COMPLETED and tote.quantity == 4
    assert len([r for r in twin.logger.query(task_id=retried.id) if "missed its grasp" in r["message"]]) == 2
    picker.carrying_box = None
    twin.faults.arm("grasp_fail", count=3)
    failed = attach(twin, picker, [Action(ActionType.GRASP, "Pick a unit", params={"unit_from": tote.id})])
    run(sim, failed)
    assert failed.status is TaskStatus.FAILED and "missed the grasp 3 times" in failed.error
    assert tote.quantity == 4


def test_a_placement_waits_for_its_conveyor_cell(twin, sim):
    picker = twin.add_robot(name="PK30-203", asset_id="AST-000203", position=(20, 14))
    item = twin.add_box(name="ITEM-1", kind="ITEM", weight=0.4, position=(20, 14))
    item.set_status(BoxStatus.CARRIED)
    picker.carrying_box = item.id
    blocker = twin.add_box(name="ITEM-2", kind="ITEM", weight=0.4, position=(20, 15))
    twin.equipment.conveyor.item_at((20, 15)).stop_at = (20, 15)          # parked on the infeed
    task = attach(twin, picker, [Action(ActionType.PLACE_ON_CONVEYOR, "Unit on the line", target=(20, 15))])
    for _ in range(30):
        sim.tick()
    assert picker.status is RobotStatus.WAITING and picker.carrying_box == item.id
    twin.equipment.conveyor.item_at((20, 15)).stop_at = None               # it moves on
    run(sim, task)
    assert task.status is TaskStatus.COMPLETED and blocker.position != (20, 15)


def test_an_arm_pauses_while_the_line_is_jammed_upstream(twin, sim):
    arm = twin.add_robot(name="CX10-210", asset_id="AST-000210")
    twin.equipment.jam((21, 15))
    task = attach(twin, arm, [Action(ActionType.WAIT_CLEAR, "Wait for ORD-1's item", params={
        "reason": "AWAITING_ITEM", "cell": [23, 15], "order_id": "ORD-1"})])
    for _ in range(5):
        sim.tick()
    assert arm.wait_reason == "CONVEYOR_JAMMED" and arm.status is RobotStatus.WAITING
    waits = events(twin, task, "ROBOT_SAFETY_WAIT")
    assert len(waits) == 1 and waits[0]["data"]["cell"] == {"x": 21, "y": 15}
    twin.equipment.clear_jam((21, 15))
    sim.tick()
    assert arm.wait_reason is None and len(events(twin, task, "ROBOT_SAFETY_RESUMED")) == 1
    twin.equipment.jam((24, 15))                              # downstream of the arm: it works on
    sim.tick()
    assert arm.wait_reason is None


def test_a_drone_takes_off_and_lands_on_its_pad(twin, sim):
    drone = twin.add_robot(name="IX2-208", asset_id="AST-000208", position=(20, 2))
    task = attach(twin, drone, [
        Action(ActionType.TAKEOFF, "Take off", params={"altitude_m": 2.0}),
        Action(ActionType.LAND, "Land"),
    ])
    assert (step_ticks(drone.mobility, "TAKEOFF"), step_ticks(drone.mobility, "LAND")) == (27, 34)
    for _ in range(10):
        sim.tick()
    assert (drone.layer, drone.altitude_m, drone.activity) == ("AIR", 2.0, "TAKEOFF")
    run(sim, task)
    assert task.status is TaskStatus.COMPLETED and (drone.layer, drone.altitude_m) == ("GROUND", 0.0)
    assert [e["data"]["step"] for e in events(twin, task, "ROBOT_STEP")] == ["TAKEOFF", "LAND"]
    assert events(twin, task, "ROBOT_STEP")[1]["data"]["layer"] == "AIR"
    drone.position = (18, 2)                                  # not on the pad
    off_pad = attach(twin, drone, [Action(ActionType.TAKEOFF, "Take off")])
    run(sim, off_pad)
    assert off_pad.status is TaskStatus.FAILED and "only take off on the drone pad" in off_pad.error


def test_a_flying_drone_keeps_its_altitude_and_reset_needs_a_free_pad(twin):
    drone = twin.add_robot(name="IX2-208", asset_id="AST-000208", position=(20, 2))
    twin.set_robot_layer(drone.id, "AIR", altitude_m=3.5)
    twin.set_robot_layer(drone.id, "AIR")                     # no altitude given: it keeps its own
    assert drone.altitude_m == 3.5
    drone.position = (12, 2)
    for number, cell in enumerate(twin.warehouse.zones["drone_pad"].cells):
        twin.add_robot(name=f"Pad-{number}", model_code="CT-IX2", position=cell)
    with pytest.raises(ValueError, match="No free drone pad cell"):
        twin.reset_robot(drone.id)
    assert (drone.layer, drone.position) == ("AIR", (12, 2))  # untouched


def test_dropping_at_staging_hands_the_pallet_over(twin, sim):
    forklift = twin.add_robot(name="PF1200-205", asset_id="AST-000205", position=(5, 5))
    pallet = twin.add_box(name="PAL-1", kind="PALLET", sku="SKU-01", quantity=40, weight=600.0, position=(5, 5))
    pallet.set_status(BoxStatus.CARRIED)
    forklift.carrying_box = pallet.id
    task = attach(twin, forklift, [Action(ActionType.DELIVER, "Deliver PAL-1", (5, 5), "Intake staging", pallet.id)])
    ticks = run(sim, task)
    assert ticks == 1 + step_ticks(forklift.mobility, "PLACE") + 1    # a 3 s fork release, not DELIVER_TICKS
    handoff = events(twin, task, "HANDOFF")[0]["data"]
    assert (handoff["from"], handoff["to"], handoff["receiver_observed"]["present"]) == \
        (forklift.id, "intake_staging", True)


def test_classic_robots_log_no_steps(tmp_path):
    classic = DigitalTwin(log_dir=str(tmp_path / "logs"), data_dir=str(tmp_path / "data"),
                          persist_logs=False, demo=True, demo_tasks=False)
    simulator = Simulator(classic)
    classic.simulation_status = SimulationStatus.RUNNING
    task = classic.tasks.create_task({"type": "PICK_AND_DELIVER", "robot_id": "Robo-01", "box_id": "Box-A",
                                      "destination": "loading_zone"})
    run(simulator, task, max_ticks=800)
    assert task.status is TaskStatus.COMPLETED
    assert not classic.events.query(event_type="ROBOT_STEP") and not classic.events.query(event_type="HANDOFF")
    assert classic.events.query(task_id=task.id, event_type="BOX_PICKED")[0]["data"] == {}
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/bin/python -m pytest -o addopts="" -q backend/test_job_steps.py`
Expected: `13 failed, 1 passed` — `AttributeError: type object 'ActionType' has no attribute 'LIFT_TO'` (and `WAIT_CLEAR`, `TAKEOFF`, `DELIVER`'s timing).

- [ ] **Step 3: Step actions, their arguments and events**

**Replace in** `backend/models.py`:

```python
    HANDOFF = "HANDOFF"
    CONVEYOR_JAMMED = "CONVEYOR_JAMMED"
    CONVEYOR_CLEARED = "CONVEYOR_CLEARED"


class ActionType(str, enum.Enum):
```

with:

```python
    HANDOFF = "HANDOFF"
    CONVEYOR_JAMMED = "CONVEYOR_JAMMED"
    CONVEYOR_CLEARED = "CONVEYOR_CLEARED"
    # Job steps (Simulator._act_step): every step's start, with who is near
    # and what the robot carries, then what a lift, a lowering and a
    # placement physically did.
    ROBOT_STEP = "ROBOT_STEP"
    LIFTED = "LIFTED"
    LOWERED = "LOWERED"
    PLACED = "PLACED"


class ActionType(str, enum.Enum):
```

**Replace in** `backend/models.py`:

```python
    CHARGE = "CHARGE"
    WAIT = "WAIT"
    COMPLETE = "COMPLETE"


class SimulationStatus(str, enum.Enum):
```

with:

```python
    CHARGE = "CHARGE"
    WAIT = "WAIT"
    COMPLETE = "COMPLETE"
    # Job steps with a physical duration (multi-embodiment spec §5.4), run by
    # Simulator._act_step: lift or lower forks/platform to a slot level, a
    # drone's take-off and landing, grasping and placing a box
    # (in a slot, a carton or on the conveyor), and waiting until a condition
    # on the floor clears.
    LIFT_TO = "LIFT_TO"
    LOWER = "LOWER"
    TAKEOFF = "TAKEOFF"
    LAND = "LAND"
    GRASP = "GRASP"
    PLACE = "PLACE"
    PLACE_ON_CONVEYOR = "PLACE_ON_CONVEYOR"
    WAIT_CLEAR = "WAIT_CLEAR"


class SimulationStatus(str, enum.Enum):
```

**Replace in** `backend/models.py`:

```python
    started: bool = False
    done: bool = False

    def to_dict(self) -> Dict[str, Any]:
```

with:

```python
    started: bool = False
    done: bool = False
    # A job step's slot and level (LIFT_TO, GRASP, PLACE, SCAN), and its other
    # arguments — plain JSON values, cells as [x, y] (see Simulator._act_step).
    level: Optional[int] = None
    slot_id: Optional[str] = None
    params: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
```

**Replace in** `backend/models.py`:

```python
            "done": self.done,
        }
```

with:

```python
            "done": self.done,
            "level": self.level,
            "slot_id": self.slot_id,
            "params": dict(self.params),
        }
```

**Replace in** `backend/models.py`:

```python
            done=data.get("done", False),
        )
```

with:

```python
            done=data.get("done", False),
            level=data.get("level"),
            slot_id=data.get("slot_id"),
            params=dict(data.get("params") or {}),
        )
```

**Replace in** `backend/robot.py`:

```python
        self.layer: str = GROUND
        self.altitude_m: float = 0.0

        # Statistics
```

with:

```python
        self.layer: str = GROUND
        self.altitude_m: float = 0.0
        # The step the robot is on (an ActionType value, None when idle), and
        # how high its forks or lift platform are raised (LIFT_TO / LOWER).
        self.activity: Optional[str] = None
        self.lift_height_m: float = 0.0

        # Statistics
```

**Replace in** `backend/robot.py`:

```python
            "altitude_m": round(self.altitude_m, 2),
            "home": cell_dict(self.home),
```

with:

```python
            "altitude_m": round(self.altitude_m, 2),
            "activity": self.activity,
            "lift_height_m": round(self.lift_height_m, 2),
            "home": cell_dict(self.home),
```

**Replace in** `backend/robot.py`:

```python
        robot.altitude_m = float(data.get("altitude_m", 0.0))
        robot.home = cell_tuple(data.get("home")) or robot.position
```

with:

```python
        robot.altitude_m = float(data.get("altitude_m", 0.0))
        robot.activity = data.get("activity")
        robot.lift_height_m = float(data.get("lift_height_m", 0.0))
        robot.home = cell_tuple(data.get("home")) or robot.position
```

- [ ] **Step 4: A box put on the line belongs to no task any more**

**Replace in** `backend/equipment.py`:

```python
        box.position = tuple(cell)
        box.assigned_robot = None
        reported = {"present": True, "weight_kg": box.declared_weight_kg}
```

with:

```python
        box.position = tuple(cell)
        box.assigned_robot = box.assigned_task = None
        reported = {"present": True, "weight_kg": box.declared_weight_kg}
```

- [ ] **Step 5: The step framework and handlers**

**Replace in** `backend/simulator.py`:

```python
from datetime import datetime

from . import people
from .eligibility import robot_eligibility
from .maintenance import maintenance_reason
from .models import (
```

with:

```python
from datetime import datetime

from . import goods, people
from .eligibility import reach_ok, robot_eligibility
from .embodiment import AIR, GROUND, HOVER_CLEARANCE_M, lift_ticks, step_ticks
from .goods import carton_weight_kg
from .maintenance import maintenance_reason
from .models import (
```

**Replace in** `backend/simulator.py`:

```python
    CellType,
    BoxStatus,
```

with:

```python
    CellType,
    BoxKind,
    BoxStatus,
```

**Replace in** `backend/simulator.py`:

```python
    TaskStatus,
    TaskType,
    cell_dict,
)

PICK_TICKS = 4
DELIVER_TICKS = 4


class Simulator:
```

with:

```python
    TaskStatus,
    TaskType,
    cell_dict,
    manhattan,
)

PICK_TICKS = 4
DELIVER_TICKS = 4

#: The job types that predate physical durations: their PICK and DELIVER keep
#: PICK_TICKS / DELIVER_TICKS on every floor. The PICK and DELIVER of every
#: newer job type take the body's grasp_s / place_s (spec §5.4).
TIMED_BY_TICKS = frozenset({
    TaskType.PICK_AND_DELIVER, TaskType.PICK_BOX, TaskType.DELIVER_BOX,
    TaskType.MOVE_BOX, TaskType.BATCH_DELIVER,
})

#: Job steps with a physical duration, run by Simulator._act_step.
STEP_ACTIONS = frozenset({
    ActionType.LIFT_TO, ActionType.LOWER, ActionType.TAKEOFF, ActionType.LAND,
    ActionType.GRASP, ActionType.PLACE, ActionType.PLACE_ON_CONVEYOR, ActionType.WAIT_CLEAR,
})

#: The conditions a WAIT_CLEAR step can wait out.
WAIT_CLEAR_REASONS = ("CONVEYOR_OCCUPIED", "AWAITING_ITEM")

#: Bodies whose grasp can miss (GRASP_FAIL_RISK), and how often they retry.
GRASPING_CLASSES = frozenset({"ARM", "PICKER"})
GRASP_RETRIES = 2


class Simulator:
```

**Replace in** `backend/simulator.py`:

```python
        if task is None:
            if robot.status == RobotStatus.CHARGING:
```

with:

```python
        if task is None:
            robot.activity = None
            if robot.status == RobotStatus.CHARGING:
```

**Replace in** `backend/simulator.py`:

```python
            robot.current_task = None
            robot.clear_path()
```

with:

```python
            robot.current_task = None
            robot.activity = None
            robot.clear_path()
```

**Replace in** `backend/simulator.py`:

```python
            twin.tasks.complete_task(task)
            return

        if action.type == ActionType.NAVIGATE:
```

with:

```python
            twin.tasks.complete_task(task)
            return
        robot.activity = action.type.value

        if action.type == ActionType.NAVIGATE:
```

**Replace in** `backend/simulator.py`:

```python
            self._act_charge(robot, task, action)
        elif action.type == ActionType.COMPLETE:
```

with:

```python
            self._act_charge(robot, task, action)
        elif action.type in STEP_ACTIONS:
            self._act_step(robot, task, action)
        elif action.type == ActionType.COMPLETE:
```

**Replace in** `backend/simulator.py`:

```python
                action.description,
            )

        if not robot.current_path or robot.target_position != target:
```

with:

```python
                action.description,
            )
            if robot.mobility is not None:
                self._emit_step(robot, task, action)

        if not robot.current_path or robot.target_position != target:
```

**Replace in** `backend/simulator.py`:

```python
            )
            return

        robot.action_timer += 1
        if robot.action_timer < PICK_TICKS:
            return
```

with:

```python
            )
            if robot.mobility is not None:
                self._emit_step(robot, task, action)
            return

        robot.action_timer += 1
        if robot.action_timer < self._handling_ticks(robot, task, "GRASP", PICK_TICKS):
            return
```

**Replace in** `backend/simulator.py`:

```python
            robot_id=robot.id,
            task_id=task.id,
            box_id=box.id,
            position=cell_dict(robot.position),
        )
        self._check_battery_thresholds(robot, task)
        self._advance(task)

    def _act_deliver(self, robot: Any, task: Any, action: Any) -> None:
```

with:

```python
            robot_id=robot.id,
            task_id=task.id,
            box_id=box.id,
            position=cell_dict(robot.position),
            data=self._load_data(robot, box) if robot.mobility is not None else None,
        )
        self._staging_handoff(robot, task, box, into_robot=True, arrived=True)
        self._check_battery_thresholds(robot, task)
        self._advance(task)

    def _act_deliver(self, robot: Any, task: Any, action: Any) -> None:
```

**Replace in** `backend/simulator.py`:

```python
            )
            return

        robot.action_timer += 1
        if robot.action_timer < DELIVER_TICKS:
            return
```

with:

```python
            )
            if robot.mobility is not None:
                self._emit_step(robot, task, action)
            return

        robot.action_timer += 1
        if robot.action_timer < self._handling_ticks(robot, task, "PLACE", DELIVER_TICKS):
            return
```

**Replace in** `backend/simulator.py`:

```python
            position=cell_dict(robot.position),
        )
        self._check_battery_thresholds(robot, task)
        self._advance(task)

    # ---- energy ------------------------------------------------------- #
```

with:

```python
            position=cell_dict(robot.position),
        )
        self._staging_handoff(robot, task, box, into_robot=False, arrived=not false_success)
        self._check_battery_thresholds(robot, task)
        self._advance(task)

    def _handling_ticks(self, robot: Any, task: Any, step: str, classic_ticks: int) -> int:
        """How long a PICK (step GRASP) or DELIVER (step PLACE) takes: the
        body's grasp_s / place_s for a new job type, PICK_TICKS / DELIVER_TICKS
        for the older ones and for any robot without a floor profile."""
        profile = robot.mobility
        if profile is None or task.type in TIMED_BY_TICKS:
            return classic_ticks
        if (profile.grasp_s if step == "GRASP" else profile.place_s) is None:
            return classic_ticks
        return step_ticks(profile, step)

    def _load_data(self, robot: Any, box: Any) -> Dict[str, Any]:
        """What a pick or lift reports: the load's kind and true and declared
        weights, against the body's limits — what payload_within_limit reads."""
        profile = robot.mobility
        return {
            "kind": box.kind.value, "true_weight_kg": box.true_weight_kg,
            "declared_weight_kg": box.declared_weight_kg, "max_payload_kg": profile.max_payload_kg,
            "max_shelf_level": profile.max_shelf_level, "embodiment_class": profile.embodiment_class,
        }

    def _staging_handoff(self, robot: Any, task: Any, box: Any, into_robot: bool, arrived: bool) -> None:
        """A robot picking from, or dropping at, a staging area hands the box
        over to or from that area (spec §8.3). `arrived` is False when the
        box never reached the receiver (a false-success delivery)."""
        twin = self.twin
        if twin.equipment is None or robot.mobility is None:
            return
        zone = next((z for z in twin.warehouse.zones_of_cell(robot.position)
                     if z.cell_type is CellType.STAGING), None)
        if zone is None:
            return
        observed = {"present": True, "weight_kg": box.true_weight_kg} if arrived else {"present": False}
        giver, receiver = (zone.key, robot.id) if into_robot else (robot.id, zone.key)
        twin.equipment.record(box, giver, receiver, robot.position,
                              {"present": True, "weight_kg": box.declared_weight_kg}, observed, task.id)

    # ---- job steps (spec §5.4) ----------------------------------------- #
    def _act_step(self, robot: Any, task: Any, action: Any) -> None:
        """One tick of a job step: hold for a physical wait; begin (check the
        step can happen, work out how many ticks it takes, announce it);
        wait out the duration; finish (make it so, and say what happened).
        A step that can't happen fails its task with the reason."""
        if self._step_hold(robot, task, action):
            return
        if not action.started:
            try:
                ticks = self._begin_step(robot, task, action)
            except ValueError as exc:
                self.twin.tasks.fail_task(task, f"{robot.name} {action.type.value} failed: {exc}")
                return
            if ticks is None:
                return  # its precondition isn't met yet: it waits
            action.started = True
            action.params["ticks"] = int(ticks)
            robot.action_timer = 0
            self._emit_step(robot, task, action)
            return
        robot.set_status(self._step_status(robot, action))
        robot.action_timer += 1
        if robot.action_timer < action.params.get("ticks", 0):
            return
        try:
            finished = self._finish_step(robot, task, action)
        except ValueError as exc:
            self.twin.tasks.fail_task(task, f"{robot.name} {action.type.value} failed: {exc}")
            return
        if finished:
            self._advance(task)

    def _step_status(self, robot: Any, action: Any) -> RobotStatus:
        if action.type is ActionType.GRASP:
            return RobotStatus.PICKING
        if action.type in (ActionType.PLACE, ActionType.PLACE_ON_CONVEYOR):
            return RobotStatus.DELIVERING
        if action.type is ActionType.WAIT_CLEAR:
            return RobotStatus.WAITING
        if action.type in (ActionType.LIFT_TO, ActionType.LOWER):
            return RobotStatus.CARRYING if robot.carrying_box else RobotStatus.PICKING
        return RobotStatus.MOVING

    def _emit_step(self, robot: Any, task: Any, action: Any) -> None:
        """ROBOT_STEP for `action` starting."""
        box = self.twin.find_box(robot.carrying_box or action.box_id)
        self._step_event(robot, task, action.type.value, action.description, level=action.level, box=box)

    def _step_event(self, robot: Any, task: Any, step: str, description: str, level: Optional[int] = None,
                    box: Optional[Any] = None, zone: Optional[str] = None,
                    present: Optional[List[str]] = None) -> None:
        """ROBOT_STEP: a step starts. It carries what the evaluation's safety
        checks read: who is next to the robot (`present`, default the people
        around it), whether it is supervised, the level, and the load's true
        and declared weights. `zone` defaults to the robot's own."""
        twin = self.twin
        if zone is None:
            here = twin.warehouse.zone_of_cell(robot.position)
            zone = here.key if here else None
        if present is None:
            present = [person.id for person in self._people_near(robot)]
        twin.events.emit(
            EventType.ROBOT_STEP,
            f"{robot.name}: {description}",
            category=LogCategory.ROBOT,
            level=LogLevel.DEBUG,
            robot_id=robot.id,
            task_id=task.id,
            box_id=box.id if box else None,
            position=cell_dict(robot.position),
            data={
                "step": step,
                "zone": zone,
                "people_present": list(present),
                "supervision_ok": None,
                "level": level,
                "true_weight_kg": box.true_weight_kg if box else None,
                "declared_weight_kg": box.declared_weight_kg if box else None,
                "embodiment_class": robot.mobility.embodiment_class,
                "layer": robot.layer,
            },
        )

    def _people_near(self, robot: Any) -> List[Any]:
        """The people a robot working where it stands is next to: for an arm,
        everyone in its fenced pack cell; for anything else, everyone in a
        zone that holds its cell."""
        station = self.twin.warehouse.fixed_stations.get(robot.position)
        if station is not None and robot.mobility is not None and robot.mobility.is_fixed:
            return people.people_in(self.twin, station)
        return people.people_at(self.twin, robot.position)

    def _step_hold(self, robot: Any, task: Any, action: Any) -> bool:
        """A physical wait before or during a step (spec §10.3): an arm pauses
        while the conveyor is jammed at or upstream of its working cell."""
        work_cell = self._arm_work_cell(robot)
        if work_cell is not None:
            jam = self.twin.equipment.conveyor.jam_upstream_of(work_cell)
            if jam is not None:
                self._safety_wait(robot, task, "CONVEYOR_JAMMED", jam)
                return True
        if robot.wait_reason == "CONVEYOR_JAMMED":
            self._safety_resume(robot, task)
        return False

    def _arm_work_cell(self, robot: Any) -> Optional[Cell]:
        """The conveyor cell a fixed arm works; None for anything else."""
        twin = self.twin
        if twin.equipment is None or robot.mobility is None or not robot.mobility.is_fixed:
            return None
        station = twin.warehouse.fixed_stations.get(robot.position)
        return twin.equipment.arm_cells.get(station) if station else None

    def _begin_step(self, robot: Any, task: Any, action: Any) -> Optional[int]:
        """Check the step can start; returns its ticks, or None to wait."""
        if robot.mobility is None:
            raise ValueError("it has no physical body on this floor")
        begin = {
            ActionType.LIFT_TO: self._begin_lift, ActionType.LOWER: self._begin_lower,
            ActionType.TAKEOFF: self._begin_takeoff, ActionType.LAND: self._begin_land,
            ActionType.GRASP: self._begin_grasp, ActionType.PLACE: self._begin_place,
            ActionType.PLACE_ON_CONVEYOR: self._begin_place_on_conveyor,
            ActionType.WAIT_CLEAR: self._begin_wait_clear,
        }[action.type]
        ticks = begin(robot, task, action)
        if ticks is None:
            robot.set_status(RobotStatus.WAITING)
            return None
        robot.set_status(self._step_status(robot, action))
        if action.type is ActionType.GRASP:
            status = TaskStatus.PICKING
        elif action.type in (ActionType.PLACE, ActionType.PLACE_ON_CONVEYOR):
            status = TaskStatus.DELIVERING
        else:
            status = TaskStatus.TRANSPORTING if robot.carrying_box else TaskStatus.IN_PROGRESS
        self.twin.tasks.set_status(task, status, action.description)
        return ticks

    def _finish_step(self, robot: Any, task: Any, action: Any) -> bool:
        """Make the step so; False keeps it going (a retry, or still waiting)."""
        finish = {
            ActionType.LIFT_TO: self._finish_lift, ActionType.LOWER: self._finish_lower,
            ActionType.TAKEOFF: self._finish_takeoff, ActionType.LAND: self._finish_land,
            ActionType.GRASP: self._finish_grasp, ActionType.PLACE: self._finish_place,
            ActionType.PLACE_ON_CONVEYOR: self._finish_place_on_conveyor,
            ActionType.WAIT_CLEAR: self._finish_wait_clear,
        }[action.type]
        return finish(robot, task, action)

    def _slot_for(self, action: Any) -> Optional[Any]:
        if not action.slot_id:
            return None
        slot = self.twin.warehouse.slot(action.slot_id)
        if slot is None:
            raise ValueError(f"slot {action.slot_id} does not exist")
        return slot

    def _emit_effect(self, event: EventType, robot: Any, task: Any, message: str,
                     box: Optional[Any] = None, **data: Any) -> None:
        """LIFTED / LOWERED / PLACED: what a step physically did."""
        payload = dict(data)
        profile = robot.mobility
        payload.update(embodiment_class=profile.embodiment_class, max_shelf_level=profile.max_shelf_level,
                       max_payload_kg=profile.max_payload_kg)
        if box is not None:
            payload.update(box_id=box.id, kind=box.kind.value, true_weight_kg=box.true_weight_kg,
                           declared_weight_kg=box.declared_weight_kg)
        self.twin.events.emit(event, message, category=LogCategory.ROBOT, robot_id=robot.id, task_id=task.id,
                              box_id=box.id if box else None, position=cell_dict(robot.position), data=payload)

    # LIFT_TO / LOWER: the height difference ÷ lift_speed_mps
    def _begin_lift(self, robot: Any, task: Any, action: Any) -> int:
        profile = robot.mobility
        slot = self._slot_for(action)
        level = action.level if action.level is not None else (slot.level if slot else None)
        ok, reason = reach_ok(level, profile.max_shelf_level)
        if not ok:
            raise ValueError(reason)
        height = slot.height_m if slot else float(action.params.get("height_m", 0.0))
        if height > profile.max_lift_m + 1e-9:
            raise ValueError(f"{height:.1f} m is above its {profile.max_lift_m:.1f} m lift")
        action.params["height_m"] = height
        return lift_ticks(profile, robot.lift_height_m, height)

    def _finish_lift(self, robot: Any, task: Any, action: Any) -> bool:
        height = action.params["height_m"]
        robot.lift_height_m = height
        self._emit_effect(EventType.LIFTED, robot, task,
                          f"{robot.name} lifted to level {action.level} ({height:.1f} m)",
                          self.twin.find_box(robot.carrying_box),
                          level=action.level, height_m=height, slot=action.slot_id)
        return True

    def _begin_lower(self, robot: Any, task: Any, action: Any) -> int:
        return lift_ticks(robot.mobility, robot.lift_height_m, 0.0)

    def _finish_lower(self, robot: Any, task: Any, action: Any) -> bool:
        robot.lift_height_m = 0.0
        self._emit_effect(EventType.LOWERED, robot, task, f"{robot.name} lowered to the floor",
                          self.twin.find_box(robot.carrying_box), height_m=0.0)
        return True

    # TAKEOFF / LAND: takeoff_s / land_s, and only on the drone pad
    def _begin_takeoff(self, robot: Any, task: Any, action: Any) -> int:
        if not robot.mobility.is_air:
            raise ValueError("it cannot fly")
        altitude = float(action.params.get("altitude_m", HOVER_CLEARANCE_M))
        self.twin.set_robot_layer(robot.id, AIR, altitude_m=altitude)
        return step_ticks(robot.mobility, "TAKEOFF")

    def _finish_takeoff(self, robot: Any, task: Any, action: Any) -> bool:
        return True

    def _begin_land(self, robot: Any, task: Any, action: Any) -> int:
        x, y = robot.position
        if robot.layer != AIR:
            raise ValueError("it is not flying")
        if self.twin.warehouse.cell_type(x, y) is not CellType.DRONE_PAD:
            raise ValueError(f"({x},{y}) is not a drone pad cell")
        if robot.position in self.twin.robot_cells(GROUND):
            raise ValueError(f"the pad cell ({x},{y}) is taken")
        return step_ticks(robot.mobility, "LAND")

    def _finish_land(self, robot: Any, task: Any, action: Any) -> bool:
        self.twin.set_robot_layer(robot.id, GROUND)
        return True

    # GRASP / PLACE: grasp_s / place_s
    def _in_slot(self, box: Any, slot: Any) -> bool:
        """Is `box` physically standing in `slot` right now?"""
        return (box.status in (BoxStatus.STORED, BoxStatus.RESERVED)
                and (box.true_slot or box.slot) == slot.slot_id and box.position == slot.cell)

    def _check_forks(self, robot: Any, slot: Any) -> None:
        """A forklift engages a slot only with its forks at the slot's height."""
        if robot.mobility.embodiment_class == "FORKLIFT" and abs(robot.lift_height_m - slot.height_m) > 0.01:
            raise ValueError(f"its forks are at {robot.lift_height_m:.1f} m but slot {slot.slot_id} "
                             f"is at {slot.height_m:.1f} m")

    def _begin_grasp(self, robot: Any, task: Any, action: Any) -> int:
        twin, params = self.twin, action.params
        if robot.carrying_box:
            raise ValueError(f"it is already holding {robot.carrying_box}")
        if "from_conveyor" in params:
            cell = tuple(params["from_conveyor"])
            item = twin.equipment.conveyor.item_at(cell) if twin.equipment is not None else None
            if item is None or (params.get("order_id") and item.order_id != params["order_id"]):
                raise ValueError(f"no item for {params.get('order_id')} on conveyor cell ({cell[0]},{cell[1]})")
        elif "unit_from" in params:
            tote = twin.find_box(params["unit_from"])
            if tote is None:
                raise ValueError(f"tote {params['unit_from']} does not exist")
            if manhattan(tote.position, robot.position) > 1:
                raise ValueError(f"{tote.name} at ({tote.position[0]},{tote.position[1]}) is out of reach")
        else:
            box = twin.find_box(action.box_id)
            if box is None:
                raise ValueError(f"box {action.box_id} does not exist")
            slot = self._slot_for(action)
            if slot is not None:
                if robot.position not in slot.faces:
                    raise ValueError(f"it is not at a face of slot {slot.slot_id}")
                if not self._in_slot(box, slot):
                    raise ValueError(f"{box.name} is not in slot {slot.slot_id}")
                self._check_forks(robot, slot)
            elif box.position != robot.position:
                raise ValueError(f"{box.name} is at ({box.position[0]},{box.position[1]}), not where it stands")
        return step_ticks(robot.mobility, "GRASP")

    def _finish_grasp(self, robot: Any, task: Any, action: Any) -> bool:
        twin, params = self.twin, action.params
        if robot.mobility.embodiment_class in GRASPING_CLASSES and twin.faults.roll("grasp_fail"):
            misses = int(params.get("misses", 0)) + 1
            params["misses"] = misses
            if misses > GRASP_RETRIES:
                raise ValueError(f"it missed the grasp {misses} times")
            robot.action_timer = 0
            twin.logger.warning(LogCategory.ROBOT, f"{robot.name} missed its grasp — retry {misses} of {GRASP_RETRIES}",
                                robot_id=robot.id, task_id=task.id, position=cell_dict(robot.position))
            return False
        if "from_conveyor" in params:
            box, _ = twin.equipment.take(tuple(params["from_conveyor"]), robot.id, task.id)
        elif "unit_from" in params:
            tote = twin.find_box(params["unit_from"])
            declared, true = goods.take_units(twin, tote, 1)
            box = twin.add_box(kind="ITEM", position=robot.position, sku=tote.sku, quantity=1,
                               weight=declared, true_weight_kg=true, order_id=params.get("order_id"))
        else:
            box = twin.find_box(action.box_id)
            if box.kind is BoxKind.PALLET and box.slot:
                goods.release(twin, box)  # a pallet leaves storage; a tote keeps its slot
        previous = box.set_status(BoxStatus.CARRIED)
        box.assigned_robot, box.assigned_task = robot.id, task.id
        box.position = robot.position
        box._picked_from_position = box.position
        box.pick_count += 1
        robot.carrying_box = box.id
        twin.events.emit(
            EventType.BOX_PICKED,
            f"{robot.name} grasped {box.name} ({previous.value} → CARRIED)",
            category=LogCategory.BOX,
            robot_id=robot.id,
            task_id=task.id,
            box_id=box.id,
            position=cell_dict(robot.position),
            data=self._load_data(robot, box),
        )
        return True

    def _slot_taken(self, slot: Any, box: Any) -> bool:
        holder = self.twin.stock.box_in(slot.slot_id)
        misplaced = any(other.true_slot == slot.slot_id for other in self.twin.boxes.values())
        return (holder is not None and holder != box.id) or misplaced

    def _begin_place(self, robot: Any, task: Any, action: Any) -> int:
        box = self.twin.find_box(robot.carrying_box)
        if box is None:
            raise ValueError("it is not holding anything")
        if "into_carton" not in action.params:
            slot = self._slot_for(action)
            if slot is None:
                raise ValueError("it was given no slot to place into")
            if robot.position not in slot.faces:
                raise ValueError(f"it is not at a face of slot {slot.slot_id}")
            if box.kind.value != slot.kind:
                raise ValueError(f"a {box.kind.value} cannot go in {slot.kind} slot {slot.slot_id}")
            if self._slot_taken(slot, box):
                raise ValueError(f"slot {slot.slot_id} is taken")
            self._check_forks(robot, slot)
        return step_ticks(robot.mobility, "PLACE")

    def _wrong_level_slot(self, slot: Any, box: Any) -> Optional[Any]:
        """Where a WRONG_LEVEL fault puts a pallet: the free slot a level up
        (else down) in the same rack cell, if there is one."""
        for delta in (1, -1):
            other = self.twin.warehouse.slot_at(slot.cell, slot.level + delta)
            if other is not None and not self._slot_taken(other, box):
                return other
        return None

    def _finish_place(self, robot: Any, task: Any, action: Any) -> bool:
        twin = self.twin
        box = twin.find_box(robot.carrying_box)
        if "into_carton" in action.params:
            return self._pack_into_carton(robot, task, action, box)
        slot = self._slot_for(action)
        actual = slot
        if robot.mobility.embodiment_class == "FORKLIFT" and twin.faults.roll("wrong_level"):
            actual = self._wrong_level_slot(slot, box) or slot
        goods.store(twin, box, slot.slot_id, true_slot_id=actual.slot_id if actual is not slot else None)
        box.set_status(BoxStatus.STORED)
        box.assigned_robot = box.assigned_task = None
        robot.carrying_box = None
        robot.boxes_delivered += 1
        twin.statistics["boxes_delivered"] += 1
        # It reports the level it was sent to; true_level is where it really is.
        self._emit_effect(EventType.PLACED, robot, task, f"{robot.name} placed {box.name} in {slot.slot_id}",
                          box, slot=slot.slot_id, level=slot.level, true_level=actual.level)
        return True

    def _pack_into_carton(self, robot: Any, task: Any, action: Any, item: Any) -> bool:
        """An arm puts an order item into the order's carton (spec §7.1): the
        carton weighs its items plus CARTON_TARE_KG, and the item is consumed."""
        twin = self.twin
        order_id = action.params["into_carton"]
        carton = next((box for box in twin.boxes.values()
                       if box.kind is BoxKind.CARTON and box.order_id == order_id
                       and box.status is BoxStatus.STORED and box.position == robot.position), None)
        if carton is None:
            tare = carton_weight_kg([])
            carton = twin.add_box(kind="CARTON", position=robot.position, weight=tare, true_weight_kg=tare,
                                  destination=action.params.get("lane"), order_id=order_id)
        carton.declared_weight_kg = round(carton.declared_weight_kg + item.declared_weight_kg, 3)
        carton.true_weight_kg = round(carton.true_weight_kg + item.true_weight_kg, 3)
        carton.quantity += 1
        carton.touch()
        robot.carrying_box = None
        del twin.boxes[item.id]  # packed: it is inside the carton now
        self._emit_effect(EventType.PLACED, robot, task, f"{robot.name} packed {item.name} into {carton.name}",
                          carton, into=carton.id, items=carton.quantity, order_id=order_id)
        return True

    # PLACE_ON_CONVEYOR(cell): place_s, once the cell is free
    def _conveyor_load(self, robot: Any, action: Any) -> Optional[Any]:
        """What goes on the line: the order's packed carton (an arm), or what
        the robot holds."""
        order_id = action.params.get("carton_for")
        if order_id:
            return next((box for box in self.twin.boxes.values()
                         if box.kind is BoxKind.CARTON and box.order_id == order_id
                         and box.status is BoxStatus.STORED and box.position == robot.position), None)
        return self.twin.find_box(robot.carrying_box)

    def _begin_place_on_conveyor(self, robot: Any, task: Any, action: Any) -> Optional[int]:
        twin, cell = self.twin, action.target
        if twin.equipment is None or cell not in twin.equipment.conveyor:
            raise ValueError(f"({cell[0]},{cell[1]}) is not a conveyor cell")
        if manhattan(cell, robot.position) > 1:
            raise ValueError(f"conveyor cell ({cell[0]},{cell[1]}) is out of reach")
        if self._conveyor_load(robot, action) is None:
            raise ValueError("it has nothing to put on the conveyor")
        if not twin.equipment.conveyor.is_free(cell):
            return None  # an item is passing: wait for the cell to clear
        return step_ticks(robot.mobility, "PLACE_ON_CONVEYOR")

    def _finish_place_on_conveyor(self, robot: Any, task: Any, action: Any) -> bool:
        twin, cell = self.twin, action.target
        if not twin.equipment.conveyor.is_free(cell):
            robot.set_status(RobotStatus.WAITING)
            return False  # something rode onto the cell meanwhile
        box = self._conveyor_load(robot, action)
        stop = action.params.get("stop_at")
        twin.equipment.place(box, cell, robot.id, task_id=task.id, order_id=box.order_id,
                             stop_at=tuple(stop) if stop else None)
        if robot.carrying_box == box.id:
            robot.carrying_box = None
        return True

    # WAIT_CLEAR(reason): until the condition clears
    def _begin_wait_clear(self, robot: Any, task: Any, action: Any) -> int:
        if action.params.get("reason") not in WAIT_CLEAR_REASONS:
            raise ValueError(f"it can't wait for {action.params.get('reason')!r} "
                             f"(known: {', '.join(WAIT_CLEAR_REASONS)})")
        if self.twin.equipment is None:
            raise ValueError("this floor has no conveyor")
        return 0

    def _finish_wait_clear(self, robot: Any, task: Any, action: Any) -> bool:
        conveyor, params = self.twin.equipment.conveyor, action.params
        cell = tuple(params["cell"])
        if params["reason"] == "CONVEYOR_OCCUPIED":
            waiting = not conveyor.is_free(cell)
        else:  # AWAITING_ITEM: until one of the order's items stands on the cell
            item = conveyor.item_at(cell)
            waiting = item is None or item.order_id != params.get("order_id")
        if waiting:
            robot.set_status(RobotStatus.WAITING)
        return not waiting

    # ---- energy ------------------------------------------------------- #
```

**Replace in** `backend/simulator.py`:

```python
            action.started = True
            robot.charging_sessions += 1
```

with:

```python
            action.started = True
            if robot.mobility is not None:
                self._emit_step(robot, task, action)
            robot.charging_sessions += 1
```

- [ ] **Step 6: A flying drone keeps its altitude; a reset drone needs a free pad**

**Replace in** `backend/digital_twin.py`:

```python
        target layer holds. Instantaneous here; the TAKEOFF / LAND job steps
        add the durations. Airborne altitude defaults to HOVER_CLEARANCE_M."""
        robot = self.find_robot(robot_id)
```

with:

```python
        target layer holds. Instantaneous here; the TAKEOFF / LAND job steps
        add the durations. With no altitude, a take-off climbs to
        HOVER_CLEARANCE_M and a drone already flying keeps its altitude."""
        robot = self.find_robot(robot_id)
```

**Replace in** `backend/digital_twin.py`:

```python
            if layer == AIR:
                altitude = HOVER_CLEARANCE_M if altitude_m is None else float(altitude_m)
                if not 0.0 < altitude <= mobility.max_lift_m:
```

with:

```python
            if layer == AIR:
                if altitude_m is not None:
                    altitude = float(altitude_m)
                else:
                    altitude = robot.altitude_m if robot.layer == AIR else HOVER_CLEARANCE_M
                if not 0.0 < altitude <= mobility.max_lift_m:
```

**Replace in** `backend/digital_twin.py`:

```python
            raise KeyError(f"Robot '{robot_id}' does not exist")
        task = self.tasks.get(robot.current_task) if robot.current_task else None
```

with:

```python
            raise KeyError(f"Robot '{robot_id}' does not exist")
        occupied = {r.position for r in self.robots.values() if r.id != robot.id and r.layer == GROUND}
        home = robot.home if robot.home not in occupied else self.warehouse.nearest_walkable(
            robot.home, occupied, profile=robot.mobility
        )
        if home is None and robot.mobility is not None and robot.mobility.is_air:
            # A drone lands only on its pad: with every pad cell taken there is nowhere to put it.
            raise ValueError(f"No free drone pad cell to land {robot.name} on")
        task = self.tasks.get(robot.current_task) if robot.current_task else None
```

**Replace in** `backend/digital_twin.py`:

```python
            robot.carrying_box = None
        occupied = {r.position for r in self.robots.values() if r.id != robot.id and r.layer == GROUND}
        home = robot.home if robot.home not in occupied else self.warehouse.nearest_walkable(
            robot.home, occupied, profile=robot.mobility
        )
        robot.position = home or robot.position
        robot.layer, robot.altitude_m = GROUND, 0.0  # a reset robot is back on the ground
        robot.battery = 100.0
```

with:

```python
            robot.carrying_box = None
        robot.position = home or robot.position
        robot.layer, robot.altitude_m = GROUND, 0.0  # a reset robot is back on the ground
        robot.lift_height_m, robot.activity = 0.0, None
        robot.battery = 100.0
```

- [ ] **Step 7: Run the new tests with the layer tests**

Run: `.venv/bin/python -m pytest -o addopts="" -q backend/test_job_steps.py backend/test_layers.py`
Expected: `23 passed`.

- [ ] **Step 8: Run the full suite**

`test_layers.py::test_a_drone_takes_off_and_lands_only_on_its_pad` still sees a take-off default to 0.5 m: only a drone already flying keeps its altitude.

Run: `.venv/bin/python -m pytest -o addopts="" -q`
Expected: `2 failed, 522 passed`.

- [ ] **Step 9: Commit**

```bash
git add backend/models.py backend/robot.py backend/equipment.py backend/simulator.py \
        backend/digital_twin.py backend/test_job_steps.py
git commit -m "feat: job steps with physical duration

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: People rules, and safety waits that pair and escalate

The three people rules that need job steps (spec §6), each a physical wait through plan 1a's `_safety_wait`: a forklift or heavy hauler won't drive into a zone with someone standing in it (`PERSON_IN_AISLE`, tested on `zones_of_cell` minus route zones, as 1a's carry list asks); an arm pauses while anyone is in its fenced pack cell (`PERSON_IN_CELL`); the humanoid moves only while a supervisor on shift, with a valid `humanoid_supervision` credential in scope for its model (`TS-H1`) and site (`WH-01`), stands in its zone or one sharing an edge (`SUPERVISOR_ABSENT`). A forklift or hauler entering a zone logs `ROBOT_STEP` `ENTER_ZONE` with who was there, and `ROBOT_STEP` gains `supervision_ok` — what the evaluation's `human_zone_clear` and `supervision_maintained` read (Task 13).

Safety waits now always pair (1a's carry list): `DigitalTwin.end_safety_wait` emits the closing `ROBOT_SAFETY_RESUMED` with a `cause` whenever a wait ends — cleared, superseded by a new reason, or ended by the task completing, failing or being cancelled, a reset, a layer change or a collision. A wait longer than `SAFETY_WAIT_ESCALATE_S` (120 s) emits `SAFETY_WAIT_ESCALATED` once. Robots learn their catalog model (`Robot.model_code`), which credential scopes are checked against. And `people.crosses_walkway` decides sides by a zone's cells, not its centre, so a walk to or from a zone straddling the strip (`cross_aisle`, `top_aisle`) counts as crossing it (1a's carry list).

**Files:**
- Modify: `backend/models.py` (`SAFETY_WAIT_ESCALATE_S`; `EventType.SAFETY_WAIT_ESCALATED`)
- Modify: `backend/robot.py` (`model_code`; `wait_task_id`, `wait_cell`, `wait_escalated`)
- Modify: `backend/fleet_bridge.py` (sets `model_code` on bind and on asset change)
- Modify: `backend/digital_twin.py` (`end_safety_wait`; called on a layer change, reset and collision)
- Modify: `backend/task_manager.py` (completing, failing or cancelling a task closes its robot's wait)
- Modify: `backend/people.py` (`crosses_walkway` by cells; `FLOOR_SITE`; `supervisors`, `supervision_available`, `supervision_status`)
- Modify: `backend/simulator.py` (the three rules; `ENTER_ZONE`; switching, closing and escalating waits)
- Test: `backend/test_people_rules.py` (create)

**Interfaces:**
- Consumes (Tasks 1, 4): `cert_scope_ok`, `supervision_ok`; `STEP_ACTIONS`, `_step_hold`, `_step_event`; plan 1a's `people.people_in`, `supervisor_nearby`, `Operator.certification_scopes`.
- Produces:
  - `CONFIG["SAFETY_WAIT_ESCALATE_S"] = 120`; `EventType.SAFETY_WAIT_ESCALATED` (category `SAFETY`, level `ERROR`, data `{"reason", "waited_s", "cell"}`).
  - `Robot.model_code: Optional[str]` (in `to_dict`/`from_dict`), `Robot.wait_task_id`, `.wait_cell`, `.wait_escalated` (cleared with the wait and by `clear_path`).
  - `DigitalTwin.end_safety_wait(robot, cause: str) -> Optional[int]` — `ROBOT_SAFETY_RESUMED` data is now `{"reason", "waited_ticks", "cause"}`; `cause` is `"cleared"`, `"superseded by <REASON>"`, `"ended: <task_id> completed|failed|cancelled"`, `"ended by a reset"`, `"ended by a layer change"` or `"ended by a collision"`.
  - `backend.people.FLOOR_SITE = "WH-01"`; `supervisors(twin, robot) -> List[Operator]`; `supervision_available(twin, robot) -> Rule` (on shift + valid + in scope: the gate's half); `supervision_status(twin, robot) -> Rule` (and nearby: the runtime half).
  - `simulator.AISLE_RULE_CLASSES = {"FORKLIFT", "HEAVY_HAULER"}`, `SUPERVISED_ACTIONS`; `Simulator._aisle_wait(robot, task, next_cell) -> bool`, `_entered_zones(origin, cell)`, `_announce_zone_entry(robot, task, previous)`, `_supervision_hold(robot, task) -> bool`, `_supervised(robot) -> Optional[bool]`, `_escalate(robot, task)`. Wait reasons now in use: `PERSON_ON_CROSSING`, `PERSON_IN_AISLE`, `PERSON_IN_CELL`, `SUPERVISOR_ABSENT`, `CONVEYOR_JAMMED`.

- [ ] **Step 1: Write the failing tests**

**Create** `backend/test_people_rules.py`:

```python
"""The rules robots obey around people (multi-embodiment spec §6, §10.3):
forklifts and haulers stay out of occupied zones, an arm pauses while someone
is in its cell, the humanoid works only near its supervisor — and every
safety wait pairs with a resume and escalates once if it drags on."""
import pytest

from backend import people
from backend.digital_twin import DigitalTwin
from backend.models import Action, ActionType, OperatorStatus, RobotStatus, SimulationStatus, TaskStatus, TaskType
from backend.simulator import Simulator
from backend.task_manager import Task


@pytest.fixture
def twin(tmp_path):
    return DigitalTwin(log_dir=str(tmp_path / "logs"), data_dir=str(tmp_path / "data"),
                       persist_logs=False, demo=True, demo_tasks=False, layout="distribution_center")


@pytest.fixture
def sim(twin):
    simulator = Simulator(twin)
    twin.simulation_status = SimulationStatus.RUNNING
    return simulator


@pytest.fixture
def sam(twin):
    return twin.add_operator(name="Sam", worker_id="E-10001")


@pytest.fixture
def jordan(twin):
    return twin.add_operator(name="Jordan", worker_id="E-10006")


def attach(twin, robot, actions):
    """Hand `robot` a task whose plan is exactly `actions` (then COMPLETE)."""
    task = Task(twin.ids.next("task"), TaskType.MOVE_ROBOT, robot_id=robot.id)
    task.actions = list(actions) + [Action(ActionType.COMPLETE, "Complete task")]
    task.record(TaskStatus.ASSIGNED, "Assigned a hand-built plan")
    twin.tasks.tasks[task.id] = task
    robot.current_task = task.id
    return task


def ticks(sim, n):
    for _ in range(n):
        sim.tick()


def run(sim, task, max_ticks=600):
    for _ in range(max_ticks):
        if task.is_terminal:
            return
        sim.tick()
    assert task.is_terminal, f"{task.id} still {task.status.value}"


def kinds(twin, task):
    return [(e["event"], e["data"].get("reason"), e["data"].get("cause"))
            for e in twin.events.query(task_id=task.id)
            if e["event"] in ("ROBOT_SAFETY_WAIT", "ROBOT_SAFETY_RESUMED")]


def test_a_forklift_waits_outside_a_zone_with_a_person_in_it(twin, sim, sam):
    forklift = twin.add_robot(name="PF1200-205", asset_id="AST-000205", position=(7, 3))
    people.place(twin, sam, "pallet_aisle_1")
    task = twin.tasks.create_task({"type": "MOVE_ROBOT", "robot_id": forklift.id, "destination": "10,3"})
    ticks(sim, 40)
    assert forklift.position == (7, 3) and forklift.wait_reason == "PERSON_IN_AISLE"
    assert forklift.status is RobotStatus.WAITING and task.status is TaskStatus.BLOCKED
    waits = twin.events.query(task_id=task.id, event_type="ROBOT_SAFETY_WAIT")
    assert len(waits) == 1 and waits[0]["data"]["cell"] == {"x": 8, "y": 3}
    people.place(twin, sam, "workshop")
    run(sim, task)
    assert task.status is TaskStatus.COMPLETED and forklift.position == (10, 3)
    assert kinds(twin, task) == [("ROBOT_SAFETY_WAIT", "PERSON_IN_AISLE", None),
                                 ("ROBOT_SAFETY_RESUMED", "PERSON_IN_AISLE", "cleared")]
    entered = [e["data"] for e in twin.events.query(task_id=task.id, event_type="ROBOT_STEP")
               if e["data"]["step"] == "ENTER_ZONE"]
    assert [(d["zone"], d["people_present"]) for d in entered] == [("pallet_aisle_1", [])]


def test_only_entering_an_occupied_zone_counts_and_only_for_heavy_bodies(twin, sim, sam):
    forklift = twin.add_robot(name="PF1200-205", asset_id="AST-000205", position=(9, 3))
    people.place(twin, sam, "pallet_aisle_1")           # already in the forklift's zone
    task = twin.tasks.create_task({"type": "MOVE_ROBOT", "robot_id": forklift.id, "destination": "12,3"})
    run(sim, task)
    assert task.status is TaskStatus.COMPLETED
    amr = twin.add_robot(name="TR50-201", asset_id="AST-000201", position=(7, 13))
    people.place(twin, sam, "tote_aisle_1")
    tote_run = twin.tasks.create_task({"type": "MOVE_ROBOT", "robot_id": amr.id, "destination": "10,13"})
    run(sim, tote_run)
    assert tote_run.status is TaskStatus.COMPLETED      # the rule is for forklifts and haulers only
    assert not twin.events.query(event_type="ROBOT_SAFETY_WAIT")


def test_an_arm_pauses_while_someone_is_in_its_cell(twin, sim, sam):
    arm = twin.add_robot(name="CX10-210", asset_id="AST-000210")
    twin.add_box(name="ITEM-1", kind="ITEM", weight=0.5, position=(23, 15), order_id="ORD-1")
    twin.equipment.conveyor.item_at((23, 15)).stop_at = (23, 15)
    people.place(twin, sam, "pack_cell_1")
    task = attach(twin, arm, [Action(ActionType.GRASP, "Grasp the item",
                                     params={"from_conveyor": [23, 15], "order_id": "ORD-1"})])
    ticks(sim, 30)
    assert arm.wait_reason == "PERSON_IN_CELL" and arm.carrying_box is None
    assert not twin.events.query(task_id=task.id, event_type="ROBOT_STEP")   # it never started
    people.place(twin, sam, "pick_station_1")           # outside the fence
    run(sim, task)
    assert task.status is TaskStatus.COMPLETED and arm.wait_reason is None
    step = twin.events.query(task_id=task.id, event_type="ROBOT_STEP")[0]["data"]
    assert (step["step"], step["people_present"], step["supervision_ok"]) == ("GRASP", [], None)
    assert kinds(twin, task) == [("ROBOT_SAFETY_WAIT", "PERSON_IN_CELL", None),
                                 ("ROBOT_SAFETY_RESUMED", "PERSON_IN_CELL", "cleared")]


def test_the_humanoid_works_only_with_its_supervisor_near(twin, sim, jordan):
    humanoid = twin.add_robot(name="H1-212", asset_id="AST-000212", position=(22, 7))
    assert humanoid.model_code == "TS-H1"
    people.place(twin, jordan, "returns_qc")
    first = attach(twin, humanoid, [Action(ActionType.NAVIGATE, "Walk to (20,6)", (20, 6))])
    run(sim, first)
    assert first.status is TaskStatus.COMPLETED
    step = twin.events.query(task_id=first.id, event_type="ROBOT_STEP")[0]["data"]
    assert step["supervision_ok"] is True
    people.start_transit(twin, jordan, "sw_floor")      # Jordan walks off on a break
    second = attach(twin, humanoid, [Action(ActionType.NAVIGATE, "Walk to (22,8)", (22, 8))])
    ticks(sim, 30)
    assert humanoid.position == (20, 6) and humanoid.wait_reason == "SUPERVISOR_ABSENT"
    people.place(twin, jordan, "ne_floor")              # back, in a zone next to returns_qc
    run(sim, second)
    assert second.status is TaskStatus.COMPLETED and humanoid.position == (22, 8)
    assert kinds(twin, second) == [("ROBOT_SAFETY_WAIT", "SUPERVISOR_ABSENT", None),
                                   ("ROBOT_SAFETY_RESUMED", "SUPERVISOR_ABSENT", "cleared")]


def test_supervision_needs_a_valid_in_scope_credential_on_shift(twin, jordan):
    humanoid = twin.add_robot(name="H1-212", asset_id="AST-000212", position=(22, 7))
    assert people.supervision_available(twin, humanoid) == (True, None)
    jordan.set_status(OperatorStatus.OFF_DUTY)
    assert people.supervision_available(twin, humanoid)[1] == \
        "no operator with a 'humanoid_supervision' credential is on shift"
    jordan.set_status(OperatorStatus.AVAILABLE)
    jordan.certification_scopes["humanoid_supervision"] = {"equipment": ["TS-H2"], "site": []}
    assert "covers this robot and site" in people.supervision_available(twin, humanoid)[1]
    del jordan.certification_scopes["humanoid_supervision"]
    assert people.supervision_available(twin, humanoid)[1] == \
        "no operator holds a valid 'humanoid_supervision' credential"
    forklift = twin.add_robot(name="PF1200-205", asset_id="AST-000205", position=(7, 3))
    assert people.supervision_available(twin, forklift) == (True, None)   # nobody supervises a forklift
    jordan.certification_scopes["humanoid_supervision"] = {"equipment": ["TS-H1"], "site": ["WH-01"]}
    people.place(twin, jordan, "sw_floor")
    assert people.supervision_status(twin, humanoid) == (False, "no supervisor is in or next to its zone")


def test_a_long_wait_escalates_once(twin, sim, sam):
    arm = twin.add_robot(name="CX10-210", asset_id="AST-000210")
    people.place(twin, sam, "pack_cell_1")
    task = attach(twin, arm, [Action(ActionType.WAIT_CLEAR, "Wait for ORD-1's item", params={
        "reason": "AWAITING_ITEM", "cell": [23, 15], "order_id": "ORD-1"})])
    ticks(sim, 799)
    assert not twin.events.query(event_type="SAFETY_WAIT_ESCALATED")
    ticks(sim, 200)
    escalated = twin.events.query(event_type="SAFETY_WAIT_ESCALATED")
    assert len(escalated) == 1 and escalated[0]["task_id"] == task.id
    assert escalated[0]["data"]["reason"] == "PERSON_IN_CELL" and escalated[0]["data"]["waited_s"] >= 120
    assert escalated[0]["level"] == "ERROR"


def test_a_new_reason_closes_the_previous_wait(twin, sim, sam, jordan):
    humanoid = twin.add_robot(name="H1-212", asset_id="AST-000212", position=(16, 13))
    people.place(twin, jordan, "tote_aisle_1")
    people.place(twin, sam, "pick_station_2")
    people.start_transit(twin, sam, "intake_staging")   # Sam is crossing the walkway
    task = attach(twin, humanoid, [Action(ActionType.NAVIGATE, "Walk to (18,13)", (18, 13))])
    ticks(sim, 10)
    assert humanoid.wait_reason == "PERSON_ON_CROSSING"
    people.place(twin, jordan, None)                    # the supervisor leaves the floor
    ticks(sim, 2)
    assert humanoid.wait_reason == "SUPERVISOR_ABSENT"
    assert kinds(twin, task) == [
        ("ROBOT_SAFETY_WAIT", "PERSON_ON_CROSSING", None),
        ("ROBOT_SAFETY_RESUMED", "PERSON_ON_CROSSING", "superseded by SUPERVISOR_ABSENT"),
        ("ROBOT_SAFETY_WAIT", "SUPERVISOR_ABSENT", None),
    ]


def test_a_wait_its_task_ends_is_closed_too(twin, sim, sam):
    arm = twin.add_robot(name="CX10-210", asset_id="AST-000210")
    people.place(twin, sam, "pack_cell_1")
    task = attach(twin, arm, [Action(ActionType.WAIT_CLEAR, "Wait for ORD-1's item", params={
        "reason": "AWAITING_ITEM", "cell": [23, 15], "order_id": "ORD-1"})])
    ticks(sim, 5)
    twin.tasks.cancel_task(task.id)
    assert arm.wait_reason is None and arm.current_task is None
    assert kinds(twin, task) == [("ROBOT_SAFETY_WAIT", "PERSON_IN_CELL", None),
                                 ("ROBOT_SAFETY_RESUMED", "PERSON_IN_CELL", f"ended: {task.id} cancelled")]


def test_a_walk_to_or_from_a_zone_straddling_the_walkway_crosses_it(twin):
    warehouse = twin.warehouse
    assert people.crosses_walkway(warehouse, "cross_aisle", "pick_station_1")   # centre (17,10), cells both sides
    assert people.crosses_walkway(warehouse, "intake_staging", "top_aisle")
    assert not people.crosses_walkway(warehouse, "intake_staging", "workshop")
    assert not people.crosses_walkway(warehouse, "pick_station_2", "pick_station_1")


def test_every_robot_knows_its_catalog_model(twin, tmp_path):
    forklift = twin.add_robot(name="PF1200-205", asset_id="AST-000205", position=(7, 3))
    assert forklift.model_code == "NW-PF1200" and forklift.to_dict()["model_code"] == "NW-PF1200"
    classic = DigitalTwin(log_dir=str(tmp_path / "c" / "logs"), data_dir=str(tmp_path / "c" / "data"),
                          persist_logs=False, demo=True, demo_tasks=False)
    assert classic.find_robot("Robo-01").model_code == "AC-TR50"
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/bin/python -m pytest -o addopts="" -q backend/test_people_rules.py`
Expected: `9 failed, 1 passed` — a forklift drives into the occupied aisle, the arm grasps with someone in its cell, `AttributeError: 'Robot' object has no attribute 'model_code'`, and no `cause` on resumes.

- [ ] **Step 3: The escalation setting, and what a robot knows about its wait and model**

**Replace in** `backend/models.py`:

```python
    "SORTER_TRANSFER_S": 2.0,
    # Injected faults (backend/faults.py), all 0.0-1.0 and off by default like
```

with:

```python
    "SORTER_TRANSFER_S": 2.0,
    # A physical safety wait (Simulator._safety_wait) longer than this many
    # seconds is escalated once (SAFETY_WAIT_ESCALATED).
    "SAFETY_WAIT_ESCALATE_S": 120,
    # Injected faults (backend/faults.py), all 0.0-1.0 and off by default like
```

**Replace in** `backend/models.py`:

```python
    ROBOT_SAFETY_RESUMED = "ROBOT_SAFETY_RESUMED"
    # A person finished walking from one zone to another (backend/people.py).
```

with:

```python
    ROBOT_SAFETY_RESUMED = "ROBOT_SAFETY_RESUMED"
    # A safety wait outlasted SAFETY_WAIT_ESCALATE_S (once per wait).
    SAFETY_WAIT_ESCALATED = "SAFETY_WAIT_ESCALATED"
    # A person finished walking from one zone to another (backend/people.py).
```

**Replace in** `backend/robot.py`:

```python
        self.fleet_hold: Optional[str] = None
        # What this robot's body lets it do (backend/embodiment.py), from its
```

with:

```python
        self.fleet_hold: Optional[str] = None
        # The catalog model of its bound asset (set by the fleet bridge): the
        # equipment a supervisor's or operator's credential must cover.
        self.model_code: Optional[str] = None
        # What this robot's body lets it do (backend/embodiment.py), from its
```

**Replace in** `backend/robot.py`:

```python
        self.blocked_by: Optional[str] = None
        self.last_error: Optional[str] = None
        # A physical safety wait (e.g. "PERSON_ON_CROSSING") and the tick it
        # began — see Simulator._safety_wait. None when not waiting for safety.
        self.wait_reason: Optional[str] = None
        self.wait_started_tick: Optional[int] = None

        # Predictive maintenance (see backend/maintenance.py): the
```

with:

```python
        self.blocked_by: Optional[str] = None
        self.last_error: Optional[str] = None
        # A physical safety wait (e.g. "PERSON_ON_CROSSING"), the tick it began,
        # the task and cell it holds, and whether it was escalated — see
        # Simulator._safety_wait and DigitalTwin.end_safety_wait. None when not
        # waiting for safety.
        self.wait_reason: Optional[str] = None
        self.wait_started_tick: Optional[int] = None
        self.wait_task_id: Optional[str] = None
        self.wait_cell: Optional[Cell] = None
        self.wait_escalated: bool = False

        # Predictive maintenance (see backend/maintenance.py): the
```

**Replace in** `backend/robot.py`:

```python
        self.target_name = None
        # No route, nothing to wait on: the next route re-checks from scratch.
        self.wait_reason = None
        self.wait_started_tick = None

    def set_path(self, path: List[Cell], target_name: Optional[str] = None) -> None:
```

with:

```python
        self.target_name = None
        # No route, nothing to wait on: the next route re-checks from scratch.
        # (DigitalTwin.end_safety_wait announces a wait's end first.)
        self.wait_reason = None
        self.wait_started_tick = None
        self.wait_task_id = None
        self.wait_cell = None
        self.wait_escalated = False

    def set_path(self, path: List[Cell], target_name: Optional[str] = None) -> None:
```

**Replace in** `backend/robot.py`:

```python
            "asset_id": self.asset_id,
            "ai_policy_version": self.ai_policy_version,
```

with:

```python
            "asset_id": self.asset_id,
            "model_code": self.model_code,
            "ai_policy_version": self.ai_policy_version,
```

**Replace in** `backend/robot.py`:

```python
        robot.asset_id = data.get("asset_id")
        robot.ai_policy_version = data.get("ai_policy_version")
```

with:

```python
        robot.asset_id = data.get("asset_id")
        robot.model_code = data.get("model_code")
        robot.ai_policy_version = data.get("ai_policy_version")
```

**Replace in** `backend/fleet_bridge.py`:

```python
        robot.asset_id = record["asset_id"]
        robot.mobility = self.floor_profile(record["model_code"])
```

with:

```python
        robot.asset_id = record["asset_id"]
        robot.model_code = record["model_code"]
        robot.mobility = self.floor_profile(record["model_code"])
```

**Replace in** `backend/fleet_bridge.py`:

```python
                # The body follows the asset's catalog model on every asset change.
                robot.mobility = self.floor_profile(change["after"]["model_code"])
                # UPDATING is derived from the jobs, never hand-set per job.
```

with:

```python
                # The body follows the asset's catalog model on every asset change.
                robot.model_code = change["after"]["model_code"]
                robot.mobility = self.floor_profile(robot.model_code)
                # UPDATING is derived from the jobs, never hand-set per job.
```

- [ ] **Step 4: Every wait gets its closing event**

**Replace in** `backend/digital_twin.py`:

```python
                    raise ValueError(f"({x},{y}) is already occupied on the {layer} layer")
                robot.clear_path()  # a route planned for the other layer no longer applies
```

with:

```python
                    raise ValueError(f"({x},{y}) is already occupied on the {layer} layer")
                self.end_safety_wait(robot, "ended by a layer change")
                robot.clear_path()  # a route planned for the other layer no longer applies
```

**Replace in** `backend/digital_twin.py`:

```python
        )
        return robot

    def request_charge(self, robot_id: str, priority: Priority = Priority.HIGH,
```

with:

```python
        )
        return robot

    def end_safety_wait(self, robot: Robot, cause: str) -> Optional[int]:
        """Close `robot`'s safety wait, if it is in one, with a
        ROBOT_SAFETY_RESUMED that pairs its ROBOT_SAFETY_WAIT (spec §10.3) —
        whether the condition cleared or the wait ended some other way (its
        task ended, the robot was reset or took off). Returns the ticks waited."""
        reason = robot.wait_reason
        if reason is None:
            return None
        started = robot.wait_started_tick if robot.wait_started_tick is not None else self.tick_count
        waited = self.tick_count - started
        task_id = robot.wait_task_id
        robot.wait_reason = robot.wait_started_tick = robot.wait_task_id = robot.wait_cell = None
        robot.wait_escalated = False
        self.events.emit(
            EventType.ROBOT_SAFETY_RESUMED,
            f"{robot.name} resumed after {waited} ticks ({reason} {cause})",
            category=LogCategory.SAFETY,
            robot_id=robot.id,
            task_id=task_id,
            position=cell_dict(robot.position),
            data={"reason": reason, "waited_ticks": waited, "cause": cause},
        )
        return waited

    def request_charge(self, robot_id: str, priority: Priority = Priority.HIGH,
```

**Replace in** `backend/digital_twin.py`:

```python
        robot.battery = 100.0
        robot.clear_path()
```

with:

```python
        robot.battery = 100.0
        self.end_safety_wait(robot, "ended by a reset")
        robot.clear_path()
```

**Replace in** `backend/digital_twin.py`:

```python
            robot.set_status(RobotStatus.ERROR)
            robot.clear_path()
```

with:

```python
            robot.set_status(RobotStatus.ERROR)
            self.end_safety_wait(robot, "ended by a collision")
            robot.clear_path()
```

**Replace in** `backend/task_manager.py`:

```python
            if robot.current_task == task.id:
                robot.current_task = None
                robot.clear_path()
                if not robot.is_halted and robot.status != twin.RobotStatus.CHARGING:
```

with:

```python
            if robot.current_task == task.id:
                robot.current_task = None
                twin.end_safety_wait(robot, f"ended: {task.id} completed")
                robot.clear_path()
                if not robot.is_halted and robot.status != twin.RobotStatus.CHARGING:
```

**Replace in** `backend/task_manager.py`:

```python
                robot.current_task = None
                robot.clear_path()
```

with:

```python
                robot.current_task = None
                twin.end_safety_wait(robot, f"ended: {task.id} failed")
                robot.clear_path()
```

**Replace in** `backend/task_manager.py`:

```python
            robot.current_task = None
            robot.clear_path()
```

with:

```python
            robot.current_task = None
            twin.end_safety_wait(robot, f"ended: {task.id} cancelled")
            robot.clear_path()
```

- [ ] **Step 5: Walkway sides by cells, and supervision**

**Replace in** `backend/people.py`:

```python
walk takes. Only a walk that crosses the pedestrian walkway strip (or starts
or ends on it) is "on the walkway" and holds the robot crossings; a walk that
stays on one side of it does not. Robots never collide with people; they obey
physical waits instead (see Simulator._crossing_wait). These helpers answer
every "who is where" question the robots' rules ask.
"""
from __future__ import annotations

from typing import Any, List, Optional

from .embodiment import seconds_to_ticks
from .models import CONFIG, Cell, CellType, EventType, LogCategory, OperatorStatus, manhattan


def _zone_key(warehouse: Any, zone: str) -> str:
```

with:

```python
walk takes. Only a walk that crosses the pedestrian walkway strip (or starts
or ends on it) is "on the walkway" and holds the robot crossings; a walk that
stays on one side of it does not. Robots never collide with people; they obey
physical waits instead (see Simulator._crossing_wait, _aisle_wait and
_supervision_hold). These helpers answer every "who is where" question the
robots' rules ask, including whether a supervised body is supervised.
"""
from __future__ import annotations

from typing import Any, List, Optional, Set, Tuple

from .eligibility import cert_scope_ok, supervision_ok
from .embodiment import seconds_to_ticks
from .models import CONFIG, Cell, CellType, EventType, LogCategory, OperatorStatus, manhattan

#: The site every new-floor credential scope is checked against (spec §4.1).
FLOOR_SITE = "WH-01"


def _zone_key(warehouse: Any, zone: str) -> str:
```

**Replace in** `backend/people.py`:

```python
    return max(1, seconds_to_ticks(seconds))


def crosses_walkway(warehouse: Any, from_zone: str, to_zone: str) -> bool:
    """Does a walk from `from_zone` to `to_zone` cross the pedestrian walkway?

    True when either end is a WALKWAY zone, or when the two zone centres lie on
    opposite sides of one. A WALKWAY zone whose cells share one x is a vertical
    strip at that x (one sharing a y, a horizontal strip at that y); the centres
    are on opposite sides when they fall either side of that line. A floor with
    no WALKWAY zone (classic) has no walkway walks.
    """
    a = warehouse.zones[_zone_key(warehouse, from_zone)]
    b = warehouse.zones[_zone_key(warehouse, to_zone)]
```

with:

```python
    return max(1, seconds_to_ticks(seconds))


def _sides(zone: Any, axis: int, line: int) -> Set[int]:
    """Which sides of the line at `line` (on `axis`) the zone's cells lie on."""
    return {(cell[axis] > line) - (cell[axis] < line) for cell in zone.cells} - {0}


def crosses_walkway(warehouse: Any, from_zone: str, to_zone: str) -> bool:
    """Does a walk from `from_zone` to `to_zone` cross the pedestrian walkway?

    True when either end is a WALKWAY zone, or when the two zones' cells are
    not all on one side of one. A WALKWAY zone whose cells share one x is a
    vertical strip at that x (one sharing a y, a horizontal strip at that y).
    Sides go by the zones' cells, not their centres: a zone with cells on both
    sides of the strip (cross_aisle, top_aisle) may be walked across, so every
    walk to or from it counts. A floor with no WALKWAY zone (classic) has no
    walkway walks.
    """
    a = warehouse.zones[_zone_key(warehouse, from_zone)]
    b = warehouse.zones[_zone_key(warehouse, to_zone)]
```

**Replace in** `backend/people.py`:

```python
        line = walkway.cells[0][axis]
        if (a.center[axis] - line) * (b.center[axis] - line) < 0:
            return True
    return False
```

with:

```python
        line = walkway.cells[0][axis]
        if len(_sides(a, axis, line) | _sides(b, axis, line)) > 1:
            return True  # opposite sides, or a zone straddling the strip
    return False
```

**Replace in** `backend/people.py`:

```python
               for zone in twin.warehouse.zones_of_cell(robot_cell))

```

with:

```python
               for zone in twin.warehouse.zones_of_cell(robot_cell))


# --------------------------------------------------------------------------- #
# Supervision (spec §6): the humanoid works only under a supervisor
# --------------------------------------------------------------------------- #
def supervisors(twin: Any, robot: Any) -> List[Any]:
    """Everyone on shift whose valid credential of the kind `robot`'s body
    requires covers its model and the floor's site."""
    required = robot.mobility.supervision if robot.mobility is not None else None
    if not required:
        return []
    return [o for o in twin.operators.values()
            if o.status != OperatorStatus.OFF_DUTY
            and cert_scope_ok(o.certification_scopes, required, robot.model_code, FLOOR_SITE)[0]]


def supervision_available(twin: Any, robot: Any) -> Tuple[bool, Optional[str]]:
    """Can `robot`'s supervision be satisfied at all? Someone on shift holds
    a valid, in-scope credential of the kind its body requires. This is the
    gate's half (spec §9.1, §10.1); being near it is the runtime half."""
    required = robot.mobility.supervision if robot.mobility is not None else None
    if not required:
        return True, None
    holders = [o for o in twin.operators.values() if required in o.certification_scopes]
    on_shift = [o for o in holders if o.status != OperatorStatus.OFF_DUTY]
    return supervision_ok(required, bool(on_shift), bool(holders), bool(supervisors(twin, robot)))


def supervision_status(twin: Any, robot: Any) -> Tuple[bool, Optional[str]]:
    """Is `robot` supervised right now: is a qualified supervisor on shift in
    its zone or a zone sharing an edge with it?"""
    ok, reason = supervision_available(twin, robot)
    if not ok:
        return ok, reason
    if any(supervisor_nearby(twin, robot.position, person) for person in supervisors(twin, robot)):
        return True, None
    return False, "no supervisor is in or next to its zone"

```

- [ ] **Step 6: The rules in the simulator**

**Replace in** `backend/simulator.py`:

```python
from .eligibility import reach_ok, robot_eligibility
from .embodiment import AIR, GROUND, HOVER_CLEARANCE_M, lift_ticks, step_ticks
from .goods import carton_weight_kg
```

with:

```python
from .eligibility import reach_ok, robot_eligibility
from .embodiment import AIR, GROUND, HOVER_CLEARANCE_M, lift_ticks, seconds_to_ticks, step_ticks
from .goods import carton_weight_kg
```

**Replace in** `backend/simulator.py`:

```python
#: Bodies whose grasp can miss (GRASP_FAIL_RISK), and how often they retry.
GRASPING_CLASSES = frozenset({"ARM", "PICKER"})
GRASP_RETRIES = 2


class Simulator:
```

with:

```python
#: Bodies whose grasp can miss (GRASP_FAIL_RISK), and how often they retry.
GRASPING_CLASSES = frozenset({"ARM", "PICKER"})
GRASP_RETRIES = 2

#: Bodies that won't drive into a zone with a person in it (PERSON_IN_AISLE).
AISLE_RULE_CLASSES = frozenset({"FORKLIFT", "HEAVY_HAULER"})

#: The actions a supervised body (the humanoid) holds still for when it is
#: unsupervised: everything that moves it or what it holds.
SUPERVISED_ACTIONS = frozenset({ActionType.NAVIGATE, ActionType.PICK, ActionType.DELIVER}) | STEP_ACTIONS


class Simulator:
```

**Replace in** `backend/simulator.py`:

```python
            return
        robot.activity = action.type.value

        if action.type == ActionType.NAVIGATE:
```

with:

```python
            return
        robot.activity = action.type.value
        if action.type in SUPERVISED_ACTIONS and self._supervision_hold(robot, task):
            return

        if action.type == ActionType.NAVIGATE:
```

**Replace in** `backend/simulator.py`:

```python
        if self._crossing_wait(robot, task, next_cell):
            return

        blocker = self._blocking_robot(robot, next_cell)
```

with:

```python
        if self._crossing_wait(robot, task, next_cell):
            return
        if self._aisle_wait(robot, task, next_cell):
            return

        blocker = self._blocking_robot(robot, next_cell)
```

**Replace in** `backend/simulator.py`:

```python
        robot.step_to(next_cell)
        twin.statistics["distance_travelled"] += 1
```

with:

```python
        robot.step_to(next_cell)
        self._announce_zone_entry(robot, task, previous)
        twin.statistics["distance_travelled"] += 1
```

**Replace in** `backend/simulator.py`:

```python
        return False

    def _safety_wait(self, robot: Any, task: Any, reason: str, cell: Cell) -> None:
        """A physical wait — a decision, not a rejection: the robot holds
        WAITING with a reason code, announced once when the wait starts. It
        is not a traffic block, so it never replans or sidesteps."""
        robot.set_status(RobotStatus.WAITING)
        if robot.wait_reason == reason:
            return
        robot.wait_reason = reason
        robot.wait_started_tick = self.twin.tick_count
        self.twin.events.emit(
            EventType.ROBOT_SAFETY_WAIT,
```

with:

```python
        return False

    def _aisle_wait(self, robot: Any, task: Any, next_cell: Cell) -> bool:
        """PERSON_IN_AISLE (spec §6): a forklift or heavy hauler won't drive
        into a zone with a person standing in it. Only entering counts:
        someone stepping into the zone it is already in doesn't stop it. Route
        zones (patrol_loop) are paths, not places, so they never count."""
        if robot.mobility is None or robot.mobility.embodiment_class not in AISLE_RULE_CLASSES:
            return False
        if any(people.people_in(self.twin, zone.key) for zone in self._entered_zones(robot.position, next_cell)):
            self._safety_wait(robot, task, "PERSON_IN_AISLE", next_cell)
            return True
        if robot.wait_reason == "PERSON_IN_AISLE":
            self._safety_resume(robot, task)
        return False

    def _entered_zones(self, origin: Cell, cell: Cell) -> List[Any]:
        """The places (non-route zones) holding `cell` that `origin` isn't in."""
        warehouse = self.twin.warehouse
        here = {zone.key for zone in warehouse.zones_of_cell(origin)}
        return [zone for zone in warehouse.zones_of_cell(cell)
                if "route" not in zone.attributes and zone.key not in here]

    def _announce_zone_entry(self, robot: Any, task: Any, previous: Cell) -> None:
        """A forklift or hauler entering a zone is a step of its own
        (ROBOT_STEP ENTER_ZONE), with who was in the zones it entered — what
        the evaluation's human_zone_clear reads."""
        if robot.mobility is None or robot.mobility.embodiment_class not in AISLE_RULE_CLASSES:
            return
        entered = self._entered_zones(previous, robot.position)
        if not entered:
            return
        present = [person.id for zone in entered for person in people.people_in(self.twin, zone.key)]
        self._step_event(robot, task, "ENTER_ZONE", f"entered {entered[0].label}",
                         box=self.twin.find_box(robot.carrying_box), zone=entered[0].key, present=present)

    def _supervision_hold(self, robot: Any, task: Any) -> bool:
        """SUPERVISOR_ABSENT (spec §6): a supervised body (the humanoid) moves
        only while a supervisor on shift, holding a valid credential in scope
        for its model and site, is in its zone or one sharing an edge with it.
        Otherwise it pauses where it is."""
        if robot.mobility is None or not robot.mobility.supervision:
            return False
        ok, _ = people.supervision_status(self.twin, robot)
        if not ok:
            self._safety_wait(robot, task, "SUPERVISOR_ABSENT", robot.position)
            return True
        if robot.wait_reason == "SUPERVISOR_ABSENT":
            self._safety_resume(robot, task)
        return False

    def _supervised(self, robot: Any) -> Optional[bool]:
        """ROBOT_STEP's supervision_ok: None for a body that needs no supervisor."""
        if robot.mobility is None or not robot.mobility.supervision:
            return None
        return people.supervision_status(self.twin, robot)[0]

    def _safety_wait(self, robot: Any, task: Any, reason: str, cell: Cell) -> None:
        """A physical wait — a decision, not a rejection: the robot holds
        WAITING with a reason code, announced once when the wait starts. It
        is not a traffic block, so it never replans or sidesteps. A new reason
        closes the previous wait first, so every wait pairs with a resume; a
        wait that outlasts SAFETY_WAIT_ESCALATE_S is escalated once."""
        robot.set_status(RobotStatus.WAITING)
        if robot.wait_reason == reason:
            self._escalate(robot, task)
            return
        if robot.wait_reason is not None:
            self.twin.end_safety_wait(robot, f"superseded by {reason}")
        robot.wait_reason = reason
        robot.wait_started_tick = self.twin.tick_count
        robot.wait_task_id = task.id
        robot.wait_cell = tuple(cell)
        robot.wait_escalated = False
        self.twin.events.emit(
            EventType.ROBOT_SAFETY_WAIT,
```

**Replace in** `backend/simulator.py`:

```python
        self.twin.tasks.set_status(task, TaskStatus.BLOCKED, f"Safety wait: {reason}")

    def _safety_resume(self, robot: Any, task: Any) -> None:
        reason = robot.wait_reason
        started = robot.wait_started_tick if robot.wait_started_tick is not None else self.twin.tick_count
        waited = self.twin.tick_count - started
        robot.wait_reason = None
        robot.wait_started_tick = None
        robot.set_status(RobotStatus.DELIVERING if robot.carrying_box else RobotStatus.MOVING)
        self.twin.events.emit(
            EventType.ROBOT_SAFETY_RESUMED,
            f"{robot.name} resumed after {waited} ticks ({reason} cleared)",
            category=LogCategory.SAFETY,
            robot_id=robot.id,
            task_id=task.id,
            position=cell_dict(robot.position),
            data={"reason": reason, "waited_ticks": waited},
        )
        self.twin.tasks.set_status(
            task, TaskStatus.TRANSPORTING if robot.carrying_box else TaskStatus.IN_PROGRESS,
```

with:

```python
        self.twin.tasks.set_status(task, TaskStatus.BLOCKED, f"Safety wait: {reason}")

    def _escalate(self, robot: Any, task: Any) -> None:
        """SAFETY_WAIT_ESCALATED, once, when a wait outlasts SAFETY_WAIT_ESCALATE_S
        (the orders panel shows it — spec §10.3)."""
        if robot.wait_escalated or robot.wait_started_tick is None:
            return
        waited = self.twin.tick_count - robot.wait_started_tick
        if waited < seconds_to_ticks(CONFIG["SAFETY_WAIT_ESCALATE_S"]):
            return
        robot.wait_escalated = True
        cell = robot.wait_cell or robot.position
        self.twin.events.emit(
            EventType.SAFETY_WAIT_ESCALATED,
            f"{robot.name} has waited {waited * self.dt:.0f} s ({robot.wait_reason}) — escalated",
            category=LogCategory.SAFETY,
            level=LogLevel.ERROR,
            robot_id=robot.id,
            task_id=task.id,
            position=cell_dict(cell),
            data={"reason": robot.wait_reason, "waited_s": round(waited * self.dt, 1), "cell": cell_dict(cell)},
        )

    def _safety_resume(self, robot: Any, task: Any) -> None:
        reason = robot.wait_reason
        self.twin.end_safety_wait(robot, "cleared")
        robot.set_status(RobotStatus.DELIVERING if robot.carrying_box else RobotStatus.MOVING)
        self.twin.tasks.set_status(
            task, TaskStatus.TRANSPORTING if robot.carrying_box else TaskStatus.IN_PROGRESS,
```

**Replace in** `backend/simulator.py`:

```python
                "people_present": list(present),
                "supervision_ok": None,
                "level": level,
```

with:

```python
                "people_present": list(present),
                "supervision_ok": self._supervised(robot),
                "level": level,
```

**Replace in** `backend/simulator.py`:

```python
    def _step_hold(self, robot: Any, task: Any, action: Any) -> bool:
        """A physical wait before or during a step (spec §10.3): an arm pauses
        while the conveyor is jammed at or upstream of its working cell."""
        work_cell = self._arm_work_cell(robot)
```

with:

```python
    def _step_hold(self, robot: Any, task: Any, action: Any) -> bool:
        """A physical wait before or during a step (spec §6, §10.3): an arm
        pauses while a person is in its fenced pack cell (PERSON_IN_CELL), and
        while the conveyor is jammed at or upstream of its working cell."""
        station = self.twin.warehouse.fixed_stations.get(robot.position)
        if station is not None and robot.mobility is not None and robot.mobility.is_fixed:
            if people.people_in(self.twin, station):
                self._safety_wait(robot, task, "PERSON_IN_CELL", robot.position)
                return True
            if robot.wait_reason == "PERSON_IN_CELL":
                self._safety_resume(robot, task)
        work_cell = self._arm_work_cell(robot)
```

- [ ] **Step 7: Run the new tests with plan 1a's people tests**

`test_people.py`'s crossing waits still see exactly one `ROBOT_SAFETY_WAIT` and one `ROBOT_SAFETY_RESUMED` each.

Run: `.venv/bin/python -m pytest -o addopts="" -q backend/test_people_rules.py backend/test_people.py`
Expected: `37 passed`.

- [ ] **Step 8: Run the full suite**

Run: `.venv/bin/python -m pytest -o addopts="" -q`
Expected: `2 failed, 532 passed`.

- [ ] **Step 9: Commit**

```bash
git add backend/models.py backend/robot.py backend/fleet_bridge.py backend/digital_twin.py \
        backend/task_manager.py backend/people.py backend/simulator.py backend/test_people_rules.py
git commit -m "feat: people rules, paired safety waits and escalation

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Energy on the new floor

The §5.5 energy model in its own unit, `backend/energy.py`, applied once a tick to every battery body on the new floor (`Simulator._apply_energy`): idle × 0.3, moving × 1.0 and moving loaded × 1.4 of `capacity_wh / (runtime_h × 3600) × TICK_DT` on the ground, `capacity_wh / (flight_time_min × 60) × TICK_DT` in the air, all × `ENERGY_TIME_SCALE` (10). `LIFT_TO` adds `m × g × Δh / 3600 / 0.6`, `m` being the load plus the carriage. Charging adds `capacity_wh / (charge_time_h × 3600) × ENERGY_TIME_SCALE` Wh a second — at `charging_station` for a ground robot, on its pad for a drone. Battery % is energy left ÷ capacity, drained unrounded (`Robot.use_energy`), since a tick costs thousandths of a percent.

Plan 1a's carry list: a mains-powered arm (`battery` None) skips the drain, auto-charge, `request_charge`, robot selection for `CHARGE_ROBOT` and the planner's battery logic, and a named `CHARGE_ROBOT` on an arm is a plain `validate` rejection. `CHARGE_ROBOT` sends a drone to the pad (landing first if it flies) rather than to `charging_station`, a low drone already on its pad charges in place, and a new-floor robot whose charger is unreachable asks again only every `SHIFT_CHECK_EVERY_TICKS` instead of flooding one failing task per tick. The classic model — 1% per cell, `BATTERY_PICK_COST`/`BATTERY_DELIVER_COST`, `BATTERY_CHARGE_RATE` per tick — is untouched for robots with no profile.

**Files:**
- Create: `backend/energy.py`
- Modify: `backend/embodiment.py` (`MobilityProfile.mass_kg`)
- Modify: `backend/models.py` (`ENERGY_TIME_SCALE`)
- Modify: `backend/robot.py` (`use_energy`, `gain_energy`, `mains_powered`)
- Modify: `backend/simulator.py` (`_apply_energy`, lift energy, charging by body, `_on_charger`, `_auto_charge`)
- Modify: `backend/task_planner.py` (energy estimates; `_charge_plan`; no detour for arms and drones)
- Modify: `backend/task_manager.py` (arms can't be charged; `CHARGE_ROBOT` targets the body's charger)
- Modify: `backend/digital_twin.py` (`request_charge` refuses an arm)
- Test: `backend/test_energy.py` (create)

**Interfaces:**
- Consumes (Task 4): `_finish_lift`, `LIFTED`; plan 1a's `MobilityProfile.battery` (`capacity_wh`, `runtime_h`, `charge_time_h`), `.flight_time_min`, `.speed_cells_s`, `.loaded_speed_cells_s`.
- Produces:
  - `CONFIG["ENERGY_TIME_SCALE"] = 10`; `MobilityProfile.mass_kg: Optional[float] = None` (from the catalog `spec.mass_kg`).
  - `backend.energy`: `IDLE_FACTOR` 0.3, `MOVING_FACTOR` 1.0, `LOADED_FACTOR` 1.4, `GRAVITY_MPS2` 9.81, `LIFT_EFFICIENCY` 0.6, `CARRIAGE_FRACTION` 0.1; `has_battery(profile) -> bool`, `charger_zone(profile) -> str`, `ground_wh_per_tick(profile, moving, loaded)`, `flight_wh_per_tick(profile)`, `tick_wh(profile, layer, moving, loaded)`, `lift_wh(profile, load_kg, delta_h_m)`, `charge_wh_per_tick(profile)`, `wh_to_pct(profile, wh)`, `energy_wh(robot)`, `route_wh(profile, cells, loaded=False, airborne=False)` (all floats, Wh).
  - `Robot.use_energy(pct) -> float`, `Robot.gain_energy(pct) -> float`, `Robot.mains_powered: bool`.
  - `Simulator._apply_energy(distance_before: Dict[str, int])`, `_on_charger(robot) -> bool`, `_recharge(robot)`; `LIFTED` data gains `energy_wh`.
  - `TaskPlanner._charge_plan(robot, blocked, route) -> (actions, waypoints)`; `CHARGE_ROBOT` plans `NAVIGATE → [LAND →] CHARGE`.

- [ ] **Step 1: Write the failing tests**

**Create** `backend/test_energy.py`:

```python
"""Energy on the new floor (multi-embodiment spec §5.5): watt-hours per tick
by body and state, lift energy, drone flight, charging at the station or the
pad, mains-powered arms — and the classic battery model left alone."""
import pytest

from backend import energy
from backend.digital_twin import DigitalTwin
from backend.models import (
    CONFIG, Action, ActionType, BoxStatus, RobotStatus, SimulationStatus, TaskStatus, TaskType,
)
from backend.simulator import Simulator
from backend.task_manager import Task


@pytest.fixture
def twin(tmp_path):
    return DigitalTwin(log_dir=str(tmp_path / "logs"), data_dir=str(tmp_path / "data"),
                       persist_logs=False, demo=True, demo_tasks=False, layout="distribution_center")


@pytest.fixture
def sim(twin):
    simulator = Simulator(twin)
    twin.simulation_status = SimulationStatus.RUNNING
    return simulator


def ticks(sim, n):
    for _ in range(n):
        sim.tick()


def pct(robot, wh):
    return energy.wh_to_pct(robot.mobility, wh)


def test_watt_hours_per_tick_by_body_and_state(twin):
    forklift = twin.add_robot(name="PF1200-205", asset_id="AST-000205", position=(7, 3))
    profile = forklift.mobility
    assert CONFIG["ENERGY_TIME_SCALE"] == 10
    # 14 400 Wh over 7 h is 0.5714 W per second; x 0.15 s ticks x 10.
    assert energy.ground_wh_per_tick(profile, moving=True, loaded=False) == pytest.approx(0.857143, rel=1e-5)
    assert energy.ground_wh_per_tick(profile, moving=False, loaded=False) == pytest.approx(0.257143, rel=1e-5)
    assert energy.ground_wh_per_tick(profile, moving=True, loaded=True) == pytest.approx(1.2, rel=1e-5)
    # 745 kg (a 600 kg pallet and a tenth of the 1450 kg truck) raised 3 m.
    assert energy.lift_wh(profile, 600.0, 3.0) == pytest.approx(745 * 9.81 * 3 / 3600 / 0.6 * 10)
    assert energy.lift_wh(profile, 600.0, -3.0) == 0.0                     # lowering is free
    assert energy.charge_wh_per_tick(profile) == pytest.approx(0.75)        # 14 400 Wh in 8 h, x 10
    drone = twin.add_robot(name="IX2-208", asset_id="AST-000208")
    assert energy.flight_wh_per_tick(drone.mobility) == pytest.approx(150 / (22 * 60) * 0.15 * 10)
    assert energy.tick_wh(drone.mobility, "AIR", moving=False, loaded=False) == \
        energy.flight_wh_per_tick(drone.mobility)                           # hovering costs flight
    arm = twin.add_robot(name="CX10-210", asset_id="AST-000210")
    assert arm.mains_powered and not forklift.mains_powered
    assert energy.tick_wh(arm.mobility, "GROUND", moving=True, loaded=True) == 0.0
    assert (energy.charger_zone(drone.mobility), energy.charger_zone(profile), energy.charger_zone(None)) == \
        ("drone_pad", "charging_station", "charging_station")
    assert energy.energy_wh(forklift) == pytest.approx(14400.0)


def test_each_tick_drains_by_what_the_robot_did(twin, sim):
    amr = twin.add_robot(name="TR50-201", asset_id="AST-000201", position=(8, 13))
    idle = pct(amr, energy.ground_wh_per_tick(amr.mobility, moving=False, loaded=False))
    ticks(sim, 100)
    assert amr.battery == pytest.approx(100.0 - 100 * idle)                 # unrounded: 0.0016% a tick
    before = amr.battery
    sim._apply_energy({amr.id: amr.total_distance - 1})                      # it moved a cell
    assert before - amr.battery == pytest.approx(pct(amr, energy.ground_wh_per_tick(amr.mobility, True, False)))
    amr.carrying_box = "box_999"
    before = amr.battery
    sim._apply_energy({amr.id: amr.total_distance - 1})
    assert before - amr.battery == pytest.approx(pct(amr, energy.ground_wh_per_tick(amr.mobility, True, True)))
    drone = twin.add_robot(name="IX2-208", asset_id="AST-000208")
    twin.set_robot_layer(drone.id, "AIR")
    before = drone.battery
    sim._apply_energy({drone.id: drone.total_distance})
    assert before - drone.battery == pytest.approx(pct(drone, energy.flight_wh_per_tick(drone.mobility)))


def test_lifting_a_load_costs_its_potential_energy(twin, sim):
    forklift = twin.add_robot(name="PF1200-205", asset_id="AST-000205", position=(8, 3))
    pallet = twin.add_box(name="PAL-1", kind="PALLET", sku="SKU-01", quantity=40, weight=600.0, position=(8, 3))
    pallet.set_status(BoxStatus.CARRIED)
    forklift.carrying_box = pallet.id
    task = Task(twin.ids.next("task"), TaskType.MOVE_ROBOT, robot_id=forklift.id)
    task.actions = [Action(ActionType.LIFT_TO, "Lift to level 3", level=3, slot_id="PR-08-02-3"),
                    Action(ActionType.COMPLETE, "Complete task")]
    task.record(TaskStatus.ASSIGNED, "Assigned a hand-built plan")
    twin.tasks.tasks[task.id] = task
    forklift.current_task = task.id
    n = 0
    while not task.is_terminal:
        sim.tick()
        n += 1
    idle = pct(forklift, energy.ground_wh_per_tick(forklift.mobility, moving=False, loaded=True))
    lift = pct(forklift, energy.lift_wh(forklift.mobility, 600.0, 3.0))
    assert 100.0 - forklift.battery == pytest.approx(lift + n * idle)
    lifted = twin.events.query(task_id=task.id, event_type="LIFTED")[0]["data"]
    assert lifted["energy_wh"] == pytest.approx(energy.lift_wh(forklift.mobility, 600.0, 3.0), abs=1e-3)


def test_an_arm_runs_on_mains_power(twin, sim):
    arm = twin.add_robot(name="CX10-210", asset_id="AST-000210")
    arm.battery = 10.0                                                        # nonsense for mains power
    ticks(sim, 30)
    assert arm.battery == 10.0 and not twin.tasks.tasks                      # no drain, no auto-charge
    task = twin.tasks.create_task({"type": "CHARGE_ROBOT", "robot_id": arm.id})
    assert task.status is TaskStatus.FAILED and task.error == "CX10-210 is mains-powered and never needs charging"
    with pytest.raises(ValueError, match="mains-powered"):
        twin.request_charge(arm.id)


def test_a_ground_robot_charges_at_its_body_rate(twin, sim):
    forklift = twin.add_robot(name="PF1200-205", asset_id="AST-000205", position=(5, 12))
    forklift.battery = 50.0
    task = twin.tasks.create_task({"type": "CHARGE_ROBOT", "robot_id": forklift.id})
    ticks(sim, 30)
    assert forklift.status is RobotStatus.CHARGING
    assert twin.warehouse.cell_type(*forklift.position).value == "CHARGING"
    before = forklift.battery
    ticks(sim, 10)
    assert forklift.battery - before == pytest.approx(10 * pct(forklift, 0.75))
    assert task.status is not TaskStatus.FAILED


def test_a_low_drone_charges_on_its_pad_not_at_the_station(twin, sim):
    drone = twin.add_robot(name="IX2-208", asset_id="AST-000208", position=(20, 2))
    drone.battery = 15.0
    sim.tick()
    assert drone.status is RobotStatus.CHARGING and drone.position == (20, 2)   # already on its charger
    assert not twin.tasks.tasks
    before = drone.battery
    ticks(sim, 10)
    assert drone.battery - before == pytest.approx(10 * pct(drone, energy.charge_wh_per_tick(drone.mobility)))


def test_a_flying_drone_lands_on_a_free_pad_cell_to_charge(twin, sim):
    drone = twin.add_robot(name="IX2-208", asset_id="AST-000208", position=(20, 2))
    twin.add_robot(name="IX2-209", asset_id="AST-000209", position=(19, 3))
    twin.set_robot_layer(drone.id, "AIR", altitude_m=2.0)
    drone.position = (18, 3)                                                  # out over the aisle
    drone.battery = 15.0
    sim.tick()
    charge = next(task for task in twin.tasks.tasks.values() if task.type is TaskType.CHARGE_ROBOT)
    for _ in range(200):
        if drone.status is RobotStatus.CHARGING:
            break
        sim.tick()
    assert drone.status is RobotStatus.CHARGING and drone.layer == "GROUND"
    assert twin.warehouse.cell_type(*drone.position).value == "DRONE_PAD" and drone.position != (19, 3)
    assert [a.type.value for a in charge.actions] == ["NAVIGATE", "LAND", "CHARGE", "COMPLETE"]


def test_an_unreachable_charger_is_asked_for_only_every_twenty_ticks(twin, sim):
    for number, cell in enumerate(twin.warehouse.zones["charging_station"].cells):
        twin.add_robot(name=f"Park-{number}", model_code="AC-TR50", position=cell)  # every charger taken
    amr = twin.add_robot(name="TR50-201", asset_id="AST-000201", position=(5, 12))
    amr.battery = 15.0
    ticks(sim, 30)
    asked = [t for t in twin.tasks.tasks.values() if t.type is TaskType.CHARGE_ROBOT and t.robot_id == amr.id]
    assert len(asked) == 2 and all(t.status is TaskStatus.FAILED for t in asked)


def test_the_classic_battery_model_is_unchanged(tmp_path):
    classic = DigitalTwin(log_dir=str(tmp_path / "logs"), data_dir=str(tmp_path / "data"),
                          persist_logs=False, demo=True, demo_tasks=False)
    simulator = Simulator(classic)
    classic.simulation_status = SimulationStatus.RUNNING
    robot = classic.find_robot("Robo-01")
    for _ in range(50):
        simulator.tick()
    assert robot.battery == 100.0                                             # no idle drain on classic
    task = classic.tasks.create_task({"type": "MOVE_ROBOT", "robot_id": "Robo-01", "destination": "6,8"})
    for _ in range(100):
        if task.is_terminal:
            break
        simulator.tick()
    assert task.status is TaskStatus.COMPLETED and robot.battery == 100.0 - robot.total_distance
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/bin/python -m pytest -o addopts="" -q backend/test_energy.py`
Expected: `1 error` — `ImportError: cannot import name 'energy' from 'backend'`.

- [ ] **Step 3: The energy model**

**Create** `backend/energy.py`:

```python
"""Energy on the multi-embodiment floor (spec §5.5).

A robot's battery percentage is the energy left ÷ its catalog capacity. Per
tick a ground robot uses capacity_wh / (runtime_h × 3600) × TICK_DT watt-hours
× 0.3 idle, × 1.0 moving and × 1.4 moving loaded; raising a load adds
m × g × Δh / 3600 / 0.6, m being the load plus the carriage. A drone in the
air uses capacity_wh / (flight_time_min × 60) × TICK_DT. Charging adds
capacity_wh / (charge_time_h × 3600) every second — at charging_station for a
ground robot, on its pad for a drone. Everything is × ENERGY_TIME_SCALE (10 by
default, so charging shows up within a demo; 1 is true to life).

A mains-powered body (an arm: battery None) neither uses nor needs energy.
Robots on the classic floor have no profile and keep the old model: 1% per
cell, a fixed cost per pick and delivery, BATTERY_CHARGE_RATE per tick.
"""
from __future__ import annotations

from typing import Any, Optional

from .embodiment import AIR, MobilityProfile
from .models import CONFIG

IDLE_FACTOR = 0.3
MOVING_FACTOR = 1.0
LOADED_FACTOR = 1.4
GRAVITY_MPS2 = 9.81
LIFT_EFFICIENCY = 0.6
#: The carriage a body raises with its load (forks, mast, lift platform), as a
#: share of its catalog mass — the catalog gives no carriage mass of its own.
CARRIAGE_FRACTION = 0.1


def _scale() -> float:
    return float(CONFIG["ENERGY_TIME_SCALE"])


def has_battery(profile: Optional[MobilityProfile]) -> bool:
    """Does this body run on the §5.5 battery model? (No profile: classic.)"""
    return profile is not None and profile.battery is not None


def charger_zone(profile: Optional[MobilityProfile]) -> str:
    """Where this body charges: a drone on its pad, anything else at the station."""
    return "drone_pad" if profile is not None and profile.is_air else "charging_station"


def ground_wh_per_tick(profile: MobilityProfile, moving: bool, loaded: bool) -> float:
    battery = profile.battery
    base = battery.capacity_wh / (battery.runtime_h * 3600) * CONFIG["TICK_DT"] * _scale()
    if not moving:
        return base * IDLE_FACTOR
    return base * (LOADED_FACTOR if loaded else MOVING_FACTOR)


def flight_wh_per_tick(profile: MobilityProfile) -> float:
    """A drone hovering or flying, loaded or not."""
    return profile.battery.capacity_wh / (profile.flight_time_min * 60) * CONFIG["TICK_DT"] * _scale()


def tick_wh(profile: MobilityProfile, layer: str, moving: bool, loaded: bool) -> float:
    """What one tick costs this body: flight in the air, else the ground rates."""
    if not has_battery(profile):
        return 0.0
    if layer == AIR and profile.flight_time_min:
        return flight_wh_per_tick(profile)
    return ground_wh_per_tick(profile, moving, loaded)


def lift_wh(profile: MobilityProfile, load_kg: float, delta_h_m: float) -> float:
    """Raising `load_kg` (and the carriage) by `delta_h_m`; lowering is free."""
    if not has_battery(profile) or delta_h_m <= 0:
        return 0.0
    mass = float(load_kg) + CARRIAGE_FRACTION * float(profile.mass_kg or 0.0)
    return mass * GRAVITY_MPS2 * delta_h_m / 3600 / LIFT_EFFICIENCY * _scale()


def charge_wh_per_tick(profile: MobilityProfile) -> float:
    battery = profile.battery
    return battery.capacity_wh / (battery.charge_time_h * 3600) * _scale() * CONFIG["TICK_DT"]


def wh_to_pct(profile: MobilityProfile, wh: float) -> float:
    """Watt-hours as a share of this body's capacity, in percent."""
    return 100.0 * wh / profile.battery.capacity_wh


def energy_wh(robot: Any) -> float:
    """What is left in `robot`'s battery (battery % × capacity)."""
    return robot.battery / 100.0 * robot.mobility.battery.capacity_wh


def route_wh(profile: MobilityProfile, cells: int, loaded: bool = False, airborne: bool = False) -> float:
    """What driving (or flying) `cells` cells costs, for planning estimates."""
    if not has_battery(profile) or cells <= 0 or profile.speed_cells_s <= 0:
        return 0.0
    speed = profile.loaded_speed_cells_s if loaded else profile.speed_cells_s
    ticks = cells / (speed * CONFIG["TICK_DT"])
    per_tick = flight_wh_per_tick(profile) if airborne and profile.flight_time_min \
        else ground_wh_per_tick(profile, moving=True, loaded=loaded)
    return ticks * per_tick
```

**Replace in** `backend/embodiment.py`:

```python
    grasp_s: Optional[float]
    place_s: Optional[float]

    @classmethod
```

with:

```python
    grasp_s: Optional[float]
    place_s: Optional[float]
    mass_kg: Optional[float] = None  # the body's own mass (lift energy, spec §5.5)

    @classmethod
```

**Replace in** `backend/embodiment.py`:

```python
            place_s=_float(spec.get("place_s")),
        )
```

with:

```python
            place_s=_float(spec.get("place_s")),
            mass_kg=_float(spec.get("mass_kg")),
        )
```

**Replace in** `backend/models.py`:

```python
    "SAFETY_WAIT_ESCALATE_S": 120,
    # Injected faults (backend/faults.py), all 0.0-1.0 and off by default like
```

with:

```python
    "SAFETY_WAIT_ESCALATE_S": 120,
    # Energy on the new floor (backend/energy.py): every Wh used and charged
    # is multiplied by this, so charging shows up within a demo; 1 is true to life.
    "ENERGY_TIME_SCALE": 10,
    # Injected faults (backend/faults.py), all 0.0-1.0 and off by default like
```

**Replace in** `backend/robot.py`:

```python
        return before

    def drain_for_movement(self) -> Optional[float]:
        """Apply distance-based battery drain. Returns the previous level if drained."""
```

with:

```python
        return before

    def use_energy(self, pct: float) -> float:
        """The §5.5 energy model (backend/energy.py) drains a few thousandths
        of a percent per tick, so unlike consume_battery this doesn't round
        (to_dict still shows one decimal). Returns the previous level."""
        before = self.battery
        self.battery = max(0.0, self.battery - pct)
        self.battery_consumed += before - self.battery
        self.touch()
        return before

    def gain_energy(self, pct: float) -> float:
        """Charging under the §5.5 model, unrounded like use_energy."""
        before = self.battery
        self.battery = min(100.0, self.battery + pct)
        self.touch()
        return before

    @property
    def mains_powered(self) -> bool:
        """A floor body with no battery (an arm): it never drains or charges."""
        return self.mobility is not None and self.mobility.battery is None

    def drain_for_movement(self) -> Optional[float]:
        """Apply distance-based battery drain. Returns the previous level if drained."""
```

- [ ] **Step 4: The simulator drains, lifts and charges by the model**

**Replace in** `backend/simulator.py`:

```python
from datetime import datetime

from . import goods, people
from .eligibility import reach_ok, robot_eligibility
from .embodiment import AIR, GROUND, HOVER_CLEARANCE_M, lift_ticks, seconds_to_ticks, step_ticks
```

with:

```python
from datetime import datetime

from . import energy, goods, people
from .eligibility import reach_ok, robot_eligibility
from .embodiment import AIR, GROUND, HOVER_CLEARANCE_M, lift_ticks, seconds_to_ticks, step_ticks
```

**Replace in** `backend/simulator.py`:

```python
        self._stop = threading.Event()
        self.dt = CONFIG["TICK_DT"]

    # ------------------------------------------------------------------ #
```

with:

```python
        self._stop = threading.Event()
        self.dt = CONFIG["TICK_DT"]
        # Robot id -> the tick before which a new-floor robot's auto-charge
        # isn't requested again (see _auto_charge).
        self._charge_retry: Dict[str, int] = {}

    # ------------------------------------------------------------------ #
```

**Replace in** `backend/simulator.py`:

```python
            twin.tasks.dispatch()

            for robot in self._execution_order():
                try:
```

with:

```python
            twin.tasks.dispatch()

            distance_before = {robot.id: robot.total_distance for robot in twin.robots.values()}
            for robot in self._execution_order():
                try:
```

**Replace in** `backend/simulator.py`:

```python
            self._detect_collisions()
            self._auto_charge()
```

with:

```python
            self._detect_collisions()
            self._apply_energy(distance_before)
            self._auto_charge()
```

**Replace in** `backend/simulator.py`:

```python
        robot.set_status(RobotStatus.CARRYING)
        robot.consume_battery(CONFIG["BATTERY_PICK_COST"])
        twin.events.emit(
```

with:

```python
        robot.set_status(RobotStatus.CARRYING)
        if robot.mobility is None:  # the classic cost; the new floor pays per tick (spec §5.5)
            robot.consume_battery(CONFIG["BATTERY_PICK_COST"])
        twin.events.emit(
```

**Replace in** `backend/simulator.py`:

```python
        robot.boxes_delivered += 1
        robot.consume_battery(CONFIG["BATTERY_DELIVER_COST"])
        twin.statistics["boxes_delivered"] += 1
```

with:

```python
        robot.boxes_delivered += 1
        if robot.mobility is None:  # the classic cost; the new floor pays per tick (spec §5.5)
            robot.consume_battery(CONFIG["BATTERY_DELIVER_COST"])
        twin.statistics["boxes_delivered"] += 1
```

**Replace in** `backend/simulator.py`:

```python
        action.params["height_m"] = height
        return lift_ticks(profile, robot.lift_height_m, height)

    def _finish_lift(self, robot: Any, task: Any, action: Any) -> bool:
        height = action.params["height_m"]
        robot.lift_height_m = height
        self._emit_effect(EventType.LIFTED, robot, task,
                          f"{robot.name} lifted to level {action.level} ({height:.1f} m)",
                          self.twin.find_box(robot.carrying_box),
                          level=action.level, height_m=height, slot=action.slot_id)
        return True
```

with:

```python
        action.params["height_m"] = height
        action.params["from_m"] = robot.lift_height_m
        return lift_ticks(profile, robot.lift_height_m, height)

    def _finish_lift(self, robot: Any, task: Any, action: Any) -> bool:
        height = action.params["height_m"]
        robot.lift_height_m = height
        box = self.twin.find_box(robot.carrying_box)
        # Raising the load and carriage costs m × g × Δh (spec §5.5).
        used = energy.lift_wh(robot.mobility, box.true_weight_kg if box else 0.0,
                              height - float(action.params.get("from_m", 0.0)))
        if used:
            robot.use_energy(energy.wh_to_pct(robot.mobility, used))
        self._emit_effect(EventType.LIFTED, robot, task,
                          f"{robot.name} lifted to level {action.level} ({height:.1f} m)",
                          box, level=action.level, height_m=height, slot=action.slot_id,
                          energy_wh=round(used, 3))
        if used:
            self._check_battery_thresholds(robot, task)
        return True
```

**Replace in** `backend/simulator.py`:

```python
    # ---- energy ------------------------------------------------------- #
    def _act_charge(self, robot: Any, task: Any, action: Any) -> None:
        twin = self.twin
        if not action.started:
            action.started = True
```

with:

```python
    # ---- energy ------------------------------------------------------- #
    def _apply_energy(self, distance_before: Dict[str, int]) -> None:
        """The §5.5 model, once a tick, for every battery body on the new
        floor: idle, moving or moving loaded on the ground; flight in the air.
        A charging robot is charging instead, and one in ERROR is powered down.
        Classic robots (no profile) keep paying per cell as they move."""
        twin = self.twin
        for robot in twin.robots.values():
            profile = robot.mobility
            if not energy.has_battery(profile) or robot.status in (RobotStatus.CHARGING, RobotStatus.ERROR):
                continue
            moved = robot.total_distance != distance_before.get(robot.id, robot.total_distance)
            used = energy.tick_wh(profile, robot.layer, moved, loaded=bool(robot.carrying_box))
            robot.use_energy(energy.wh_to_pct(profile, used))
            task = twin.tasks.get(robot.current_task) if robot.current_task else None
            self._check_battery_thresholds(robot, task)

    def _on_charger(self, robot: Any) -> bool:
        """Is the robot where it can charge: a landed drone on its pad, any
        other robot on a CHARGING cell?"""
        cell = self.twin.warehouse.cell_type(*robot.position)
        if robot.mobility is not None and robot.mobility.is_air:
            return robot.layer == GROUND and cell is CellType.DRONE_PAD
        return cell is CellType.CHARGING

    def _recharge(self, robot: Any) -> None:
        """One tick on the charger: BATTERY_CHARGE_RATE on classic, the
        body's charge rate on the new floor."""
        if energy.has_battery(robot.mobility):
            robot.gain_energy(energy.wh_to_pct(robot.mobility, energy.charge_wh_per_tick(robot.mobility)))
        else:
            robot.charge(CONFIG["BATTERY_CHARGE_RATE"])

    def _act_charge(self, robot: Any, task: Any, action: Any) -> None:
        twin = self.twin
        if not action.started:
            if robot.mobility is not None and not self._on_charger(robot):
                twin.tasks.fail_task(task, f"{robot.name} is not on a charger")
                return
            action.started = True
```

**Replace in** `backend/simulator.py`:

```python
            return

        robot.charge(CONFIG["BATTERY_CHARGE_RATE"])
        if robot.battery >= 100.0:
            robot.low_battery_warned = False
```

with:

```python
            return

        self._recharge(robot)
        if robot.battery >= 100.0:
            robot.low_battery_warned = False
```

**Replace in** `backend/simulator.py`:

```python
    def _continue_idle_charge(self, robot: Any) -> None:
        robot.charge(CONFIG["BATTERY_CHARGE_RATE"])
        if robot.battery >= 100.0:
```

with:

```python
    def _continue_idle_charge(self, robot: Any) -> None:
        self._recharge(robot)
        if robot.battery >= 100.0:
```

**Replace in** `backend/simulator.py`:

```python
    def _apply_movement_battery(self, robot: Any, task: Any) -> None:
        before = robot.drain_for_movement()
```

with:

```python
    def _apply_movement_battery(self, robot: Any, task: Any) -> None:
        if robot.mobility is not None:
            return  # the new floor pays per tick instead (_apply_energy)
        before = robot.drain_for_movement()
```

**Replace in** `backend/simulator.py`:

```python
    def _auto_charge(self) -> None:
        """Idle robots with a low battery take themselves to the charger."""
        twin = self.twin
        for robot in list(twin.robots.values()):
            if robot.is_halted or robot.current_task or robot.carrying_box:
                continue
            if robot.status == RobotStatus.CHARGING or robot.battery > CONFIG["BATTERY_LOW"]:
                continue
            if twin.warehouse.cell_type(*robot.position).value == "CHARGING":
                robot.charging_sessions += 1
```

with:

```python
    def _auto_charge(self) -> None:
        """Idle robots with a low battery take themselves to the charger — a
        drone to its pad. A mains-powered arm never needs to. On the new floor
        a robot asks at most every SHIFT_CHECK_EVERY_TICKS, so a charger it
        can't reach right now doesn't get one failing request per tick."""
        twin = self.twin
        for robot in list(twin.robots.values()):
            if robot.is_halted or robot.current_task or robot.carrying_box or robot.mains_powered:
                continue
            if robot.status == RobotStatus.CHARGING or robot.battery > CONFIG["BATTERY_LOW"]:
                continue
            if self._on_charger(robot):
                robot.charging_sessions += 1
```

**Replace in** `backend/simulator.py`:

```python
                continue
            twin.logger.info(
                LogCategory.BATTERY,
                f"{robot.name} is idle at {robot.battery:.0f}% — heading to the charging station",
                robot_id=robot.id,
            )
            try:
```

with:

```python
                continue
            if self._charge_retry.get(robot.id, 0) > twin.tick_count:
                continue
            charger = energy.charger_zone(robot.mobility).replace("_", " ")
            twin.logger.info(
                LogCategory.BATTERY,
                f"{robot.name} is idle at {robot.battery:.0f}% — heading to the {charger}",
                robot_id=robot.id,
            )
            if robot.mobility is not None:
                self._charge_retry[robot.id] = twin.tick_count + CONFIG["SHIFT_CHECK_EVERY_TICKS"]
            try:
```

- [ ] **Step 5: Planning and the gate know each body's charger**

**Replace in** `backend/task_planner.py`:

```python
from typing import Any, Dict, List, Optional, Set, Tuple

from .embodiment import GROUND, MobilityProfile
from .models import (
    CONFIG,
```

with:

```python
from typing import Any, Dict, List, Optional, Set, Tuple

from . import energy
from .embodiment import AIR, GROUND, MobilityProfile
from .models import (
    CONFIG,
```

**Replace in** `backend/task_planner.py`:

```python
    def estimate_battery(self, robot: Any, waypoints: List[Cell], handling_ops: int = 0) -> float:
        """Estimate battery percentage required to drive a route and handle boxes."""
        nav = self.twin.navigation
```

with:

```python
    def estimate_battery(self, robot: Any, waypoints: List[Cell], handling_ops: int = 0) -> float:
        """Estimate battery percentage required to drive a route and handle
        boxes: 1% a cell plus a cost per box on classic; on the new floor, the
        §5.5 energy of driving the route at the body's speed (backend/energy.py)."""
        nav = self.twin.navigation
```

**Replace in** `backend/task_planner.py`:

```python
            origin = waypoint
        movement_cost = math.ceil(total_cells / CONFIG["BATTERY_DRAIN_MOVES"])
```

with:

```python
            origin = waypoint
        if energy.has_battery(robot.mobility):
            used = energy.route_wh(robot.mobility, total_cells)
            return round(energy.wh_to_pct(robot.mobility, used), 2) + float(CONFIG["BATTERY_RESERVE"])
        movement_cost = math.ceil(total_cells / CONFIG["BATTERY_DRAIN_MOVES"])
```

**Replace in** `backend/task_planner.py`:

```python
        elif task.type == TaskType.CHARGE_ROBOT:
            cell, label = self.resolve_target("charging_station", robot.position, blocked, **route)
            actions = [
                Action(ActionType.NAVIGATE, f"Navigate to {label}", cell, label),
                Action(ActionType.CHARGE, "Charge to 100%", cell, label),
            ]
            waypoints = [cell]

        else:
            raise PlanningError(f"{task.type.value} does not need a movement plan")

        # ---- battery-aware planning ---------------------------------- #
        if task.type != TaskType.CHARGE_ROBOT and waypoints:
            required = self.estimate_battery(robot, waypoints, handling_ops)
            task.battery_estimate = required
            if robot.battery < required:
                charge_cell, charge_label = self.resolve_target(
                    "charging_station", robot.position, blocked, **route
                )
```

with:

```python
        elif task.type == TaskType.CHARGE_ROBOT:
            actions, waypoints = self._charge_plan(robot, blocked, route)

        else:
            raise PlanningError(f"{task.type.value} does not need a movement plan")

        # ---- battery-aware planning ---------------------------------- #
        # A mains-powered arm has no battery to plan for, and a drone gets a
        # hard energy gate instead of a detour (spec §5.5, §10.1).
        flies = robot.mobility is not None and robot.mobility.is_air
        if task.type != TaskType.CHARGE_ROBOT and waypoints and not robot.mains_powered and not flies:
            required = self.estimate_battery(robot, waypoints, handling_ops)
            task.battery_estimate = required
            if robot.battery < required:
                charge_cell, charge_label = self.resolve_target(
                    energy.charger_zone(robot.mobility), robot.position, blocked, **route
                )
```

**Replace in** `backend/task_planner.py`:

```python
        return actions

    def stats(self) -> Dict[str, int]:
        return {"plans_created": self.plans_created}
```

with:

```python
        return actions

    def _charge_plan(self, robot: Any, blocked: Set[Cell], route: Dict[str, Any]) -> Tuple[List[Action], List[Cell]]:
        """CHARGE_ROBOT: to the robot's charger and charge. A drone charges on
        its pad (spec §5.5): it lands on a pad cell no grounded robot holds."""
        zone = energy.charger_zone(robot.mobility)
        if robot.mobility is not None and robot.mobility.is_air:
            blocked = (set(blocked) | set(self.twin.robot_cells(GROUND))) - {robot.position}
        cell, label = self.resolve_target(zone, robot.position, blocked, **route)
        actions = [Action(ActionType.NAVIGATE, f"Navigate to {label}", cell, label)]
        if robot.layer == AIR:
            actions.append(Action(ActionType.LAND, f"Land on {label}", cell, label))
        actions.append(Action(ActionType.CHARGE, "Charge to 100%", cell, label))
        return actions, [cell]

    def stats(self) -> Dict[str, int]:
        return {"plans_created": self.plans_created}
```

**Replace in** `backend/task_manager.py`:

```python
from .embodiment import GROUND
from .llm import narrate
```

with:

```python
from .embodiment import GROUND
from .energy import charger_zone
from .llm import narrate
```

**Replace in** `backend/task_manager.py`:

```python
                return False, f"{robot.name} {reason}"
        elif not twin.robots:
```

with:

```python
                return False, f"{robot.name} {reason}"
            if task.type == TaskType.CHARGE_ROBOT and robot.mains_powered:
                return False, f"{robot.name} is mains-powered and never needs charging"
        elif not twin.robots:
```

**Replace in** `backend/task_manager.py`:

```python
        if task.type == TaskType.CHARGE_ROBOT:
            return planner.resolve_target("charging_station", origin, **route)
        return None, ""
```

with:

```python
        if task.type == TaskType.CHARGE_ROBOT:
            return planner.resolve_target(charger_zone(profile), origin, **route)
        return None, ""
```

**Replace in** `backend/task_manager.py`:

```python
                continue
            if robot_eligibility(
```

with:

```python
                continue
            if task.type == TaskType.CHARGE_ROBOT and robot.mains_powered:
                continue  # an arm has no battery to charge
            if robot_eligibility(
```

**Replace in** `backend/digital_twin.py`:

```python
            raise KeyError(f"Robot '{robot_id}' does not exist")
        existing = self.tasks.task_for_robot(robot.id)
```

with:

```python
            raise KeyError(f"Robot '{robot_id}' does not exist")
        if robot.mains_powered:
            raise ValueError(f"{robot.name} is mains-powered and never needs charging")
        existing = self.tasks.task_for_robot(robot.id)
```

- [ ] **Step 6: Run the new tests with the step and mobility tests**

Run: `.venv/bin/python -m pytest -o addopts="" -q backend/test_energy.py backend/test_job_steps.py backend/test_robot_mobility.py`
Expected: `34 passed`.

- [ ] **Step 7: Run the full suite**

Classic battery tests (`tests.py`'s drain, recharge-detour and charging-station tests) keep passing: their robots have no profile.

Run: `.venv/bin/python -m pytest -o addopts="" -q`
Expected: `2 failed, 541 passed`.

- [ ] **Step 8: Commit**

```bash
git add backend/energy.py backend/embodiment.py backend/models.py backend/robot.py \
        backend/simulator.py backend/task_planner.py backend/task_manager.py backend/digital_twin.py \
        backend/test_energy.py
git commit -m "feat: energy model on the new floor; arms on mains, drones charge on their pad

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Capability-filtered selection, the physical gate and the job framework

Robot choice on the new floor filters by body before it scores (spec §9.1): the body class the job needs, the kind and declared weight of a box it lifts against `box_kinds` and `max_payload_kg`, the slot level against `max_shelf_level`, the job type against `allowed_task_types` (the existing eligibility check), a satisfiable supervision for the humanoid, and a route under its profile *to the goal itself* — `find_path(..., allow_goal_adjacent=False)`, and now `distance()`/`path_exists()` take the same flag, so nothing snaps (1a's carry list). Each candidate's target is resolved for its own body from where it stands, not from the first robot's position. The same `capability_reason` gates a named robot in `validate()`, and an AUTO request no body on the floor could ever do is rejected with the reasons instead of queueing forever. While a job runs, `_check_authorization_changes` also re-checks payload, reach and supervision (§10.2) — flagging, never cancelling. Classic AUTO scoring is byte-for-byte today's.

This task also lays the frame Tasks 8–11 register the new job types in (`backend/jobs.py`): a `JobSpec` per type, delegated to by `TaskPlanner.plan`, `TaskManager.validate`/`_primary_target`, `Task.summary`, `options()` and the agent's `TASK_TYPE_GUIDE`; and `Task.params` for what those jobs need. Three of 1a's carry items close here too: an arm is never routed (`find_path` returns None for a fixed body without counting a failure), `location_options()` offers only real destinations on the new floor, and `FleetBridge.model_profile` reads each catalog model once.

**Files:**
- Create: `backend/jobs.py`
- Modify: `backend/navigation.py` (fixed bodies never routed; `allow_goal_adjacent` on `path_exists` and `distance`)
- Modify: `backend/warehouse.py` (`location_options` on a layered floor)
- Modify: `backend/fleet_bridge.py` (profiles cached per model)
- Modify: `backend/task_planner.py` (new job types plan through their spec)
- Modify: `backend/task_manager.py` (`Task.params`; `capability_reason`, `physical_recheck`, the per-body scoring and the fleet check)
- Modify: `backend/simulator.py` (the mid-job re-check)
- Modify: `backend/digital_twin.py` (`options()` lists the registered job types)
- Modify: `backend/agent_tools.py` (`TASK_TYPE_GUIDE` gains each registered job type)
- Test: `backend/test_selection.py` (create)

**Interfaces:**
- Consumes (Tasks 1, 5, 6): `box_kind_ok`, `payload_ok`, `reach_ok`; `people.supervision_available`; `Robot.mains_powered`; plan 1a's `MobilityProfile.box_kinds`, `.max_payload_kg`, `.max_shelf_level`, `.is_fixed`, `Warehouse.slots_at`.
- Produces:
  - `backend.jobs`: `JobSpec(label, guide, classes: FrozenSet[str], check=None, plan=None, target=None, carries_box=False, air=False, human=False)`; `JOB_SPECS: Dict[TaskType, JobSpec]` (empty until Task 8); `JOB_PARAM_KEYS = ("slot", "quantity", "station", "face", "dock", "lane", "order_id", "pack_cell", "segment")`; `PlanFn(planner, task, robot, blocked) -> (actions, waypoints, handling_ops)`, `TargetFn(planner, task, origin, profile, layer) -> (cell, label)`, `CheckFn(manager, task) -> Optional[str]`; helpers `task_box(twin, task)`, `task_slot(twin, task, box=None)`, `job_levels(twin, task) -> List[int]`, `parse_cell(spec) -> Optional[Cell]`, `face_cell(planner, slot, origin, profile, layer, blocked=None) -> Cell`.
  - `Task(..., params=None)`, `Task.params: Dict[str, Any]` (in `to_dict`/`from_dict`; `TASK_CREATED` data gains `"params"` only when it is non-empty); `create_task` copies `JOB_PARAM_KEYS` from the payload.
  - `task_manager.BOX_HANDLING_TYPES`; `TaskManager.capability_reason(robot, task) -> Optional[str]`, `physical_recheck(robot, task) -> Optional[str]`, `_route(task, robot) -> {"profile", "layer"}`, `_can_reach(robot, cell, route) -> bool`, `_reach(task, robot) -> Optional[(cell, label, distance)]`, `_fleet_reason(task) -> Optional[str]`, `_lifted_boxes(task)`. Messages read after the robot's name: `"can't take PAL-1: <rule reason>"`, `"can't work there: <reach reason>"`, `"can't work unsupervised: <supervision reason>"`, `"is a FORKLIFT; X needs a ..."`, `"is fixed equipment and can't do X"`, `"is carrying PAL-9: <payload reason>"`; the fleet check's `"No robot can do this X: <name reason>; ..."`.
  - `NavigationEngine.path_exists(..., allow_goal_adjacent=True)`, `distance(..., allow_goal_adjacent=True)`.

- [ ] **Step 1: Write the failing tests**

**Create** `backend/test_selection.py`:

```python
"""Capability-filtered robot selection and the physical gate (multi-embodiment
spec §9.1, §10.2): a robot is picked only if its body can do the job — the
right kind of body, box kind, payload, reach, supervision and a route it can
drive to the goal itself — and the same rules re-checked mid-job only flag."""
import pytest

from backend import people
from backend.agent_tools import TASK_TYPE_GUIDE
from backend.digital_twin import DigitalTwin
from backend.embodiment import MobilityProfile
from backend.jobs import JOB_PARAM_KEYS, JOB_SPECS, parse_cell
from backend.models import BoxStatus, SimulationStatus, TaskStatus
from backend.simulator import Simulator
from backend.task_manager import Task


def make_twin(tmp_path, layout="distribution_center"):
    return DigitalTwin(log_dir=str(tmp_path / "logs"), data_dir=str(tmp_path / "data"),
                       persist_logs=False, demo=True, demo_tasks=False, layout=layout)


@pytest.fixture
def twin(tmp_path):
    return make_twin(tmp_path)


@pytest.fixture
def sim(twin):
    simulator = Simulator(twin)
    twin.simulation_status = SimulationStatus.RUNNING
    return simulator


def candidates(twin, task):
    scored, _ = twin.tasks._score_candidates(task)
    return [entry[1].name for entry in scored]


def test_a_pallet_goes_to_a_body_that_can_lift_it(twin):
    twin.add_robot(name="TR50-201", asset_id="AST-000201", position=(5, 6))     # right beside it
    forklift = twin.add_robot(name="PF1200-205", asset_id="AST-000205", position=(8, 8))
    hauler = twin.add_robot(name="HH300-207", asset_id="AST-000207", position=(2, 8))
    twin.add_box(name="PAL-1", kind="PALLET", sku="SKU-01", quantity=40, weight=600.0, position=(5, 5))
    task = twin.tasks.create_task({"type": "PICK_AND_DELIVER", "box_id": "PAL-1", "destination": "outbound_staging"})
    assert task.status is TaskStatus.PLANNING, task.error
    assert candidates(twin, task) == ["PF1200-205"]           # the AMR can't take a pallet; it's over 300 kg
    light = twin.add_box(name="PAL-2", kind="PALLET", sku="SKU-02", quantity=10, weight=250.0, position=(4, 7))
    hauled = twin.tasks.create_task({"type": "PICK_AND_DELIVER", "box_id": light.id, "destination": "outbound_staging"})
    assert candidates(twin, hauled)[0] == "HH300-207"         # closer, and 250 kg is within its 300
    assert twin.tasks.select_robot(hauled) is hauler and twin.tasks.select_robot(task) is forklift


def test_a_goal_is_never_snapped_to_a_neighbour_for_scoring(twin):
    forklift = twin.add_robot(name="PF1200-205", asset_id="AST-000205", position=(10, 11))
    amr = twin.add_robot(name="TR50-201", asset_id="AST-000201", position=(4, 13))
    task = twin.tasks.create_task({"type": "MOVE_ROBOT", "destination": "tote_aisle_1"})
    assert task.status is TaskStatus.PLANNING
    assert candidates(twin, task) == ["TR50-201"]             # the forklift could only stop beside the aisle
    nav = twin.navigation
    assert nav.distance(forklift.position, (10, 13), profile=forklift.mobility) == 0  # snapped onto its own cell
    assert nav.distance(forklift.position, (10, 13), profile=forklift.mobility, allow_goal_adjacent=False) is None
    assert not nav.path_exists(forklift.position, (10, 13), profile=forklift.mobility, allow_goal_adjacent=False)
    assert twin.tasks.select_robot(task) is amr


def test_a_named_robot_must_have_the_body_for_the_job(twin, sim):
    amr = twin.add_robot(name="TR50-201", asset_id="AST-000201", position=(5, 6))
    hauler = twin.add_robot(name="HH300-207", asset_id="AST-000207", position=(2, 8))
    arm = twin.add_robot(name="CX10-210", asset_id="AST-000210")
    humanoid = twin.add_robot(name="H1-212", asset_id="AST-000212", position=(22, 7))
    twin.add_box(name="PAL-1", kind="PALLET", sku="SKU-01", quantity=40, weight=600.0, position=(5, 5))
    rejected = {
        amr.id: "TR50-201 can't take PAL-1: cannot handle a PALLET (handles: TOTE, ITEM)",
        hauler.id: "HH300-207 can't take PAL-1: a 600 kg load is over its 300 kg payload limit",
    }
    for robot_id, error in rejected.items():
        task = twin.tasks.create_task({"type": "PICK_AND_DELIVER", "robot_id": robot_id, "box_id": "PAL-1",
                                       "destination": "outbound_staging"})
        assert task.status is TaskStatus.FAILED and task.error == error
    fixed = twin.tasks.create_task({"type": "MOVE_ROBOT", "robot_id": arm.id, "destination": "22,13"})
    assert fixed.error.startswith("CX10-210 is not configured to run MOVE_ROBOT tasks")
    twin.set_robot_capabilities(arm.id, None)                  # even unrestricted, its body can't move
    fixed = twin.tasks.create_task({"type": "MOVE_ROBOT", "robot_id": arm.id, "destination": "22,13"})
    assert fixed.error == "CX10-210 is fixed equipment and can't do MOVE_ROBOT"
    alone = twin.tasks.create_task({"type": "MOVE_ROBOT", "robot_id": humanoid.id, "destination": "20,6"})
    assert alone.error == ("H1-212 can't work unsupervised: no operator holds a valid "
                           "'humanoid_supervision' credential")
    jordan = twin.add_operator(name="Jordan", worker_id="E-10006")
    people.place(twin, jordan, "returns_qc")
    supervised = twin.tasks.create_task({"type": "MOVE_ROBOT", "robot_id": humanoid.id, "destination": "20,6"})
    assert supervised.status is TaskStatus.PLANNING, supervised.error


def test_an_auto_job_no_body_can_do_is_rejected_with_the_reasons(twin):
    twin.add_robot(name="TR50-201", asset_id="AST-000201", position=(5, 6))
    twin.add_robot(name="CX10-210", asset_id="AST-000210")
    twin.add_box(name="PAL-1", kind="PALLET", sku="SKU-01", quantity=40, weight=600.0, position=(5, 5))
    task = twin.tasks.create_task({"type": "PICK_AND_DELIVER", "box_id": "PAL-1", "destination": "outbound_staging"})
    assert task.status is TaskStatus.FAILED
    assert task.error == ("No robot can do this PICK_AND_DELIVER: TR50-201 can't take PAL-1: cannot handle a "
                          "PALLET (handles: TOTE, ITEM); CX10-210 is fixed equipment and can't do PICK_AND_DELIVER")
    drone_only = twin.tasks.create_task({"type": "MOVE_ROBOT", "destination": "drone_pad"})
    assert drone_only.status is TaskStatus.FAILED and "TR50-201 has no route to the job" in drone_only.error


def test_an_arm_is_never_routed(twin):
    arm = twin.add_robot(name="CX10-210", asset_id="AST-000210")
    failures = twin.navigation.failures
    assert twin.navigation.find_path(arm.position, arm.position, profile=arm.mobility) is None
    assert twin.navigation.failures == failures                # an arm not moving is no routing failure


def test_rules_rechecked_mid_job_flag_but_never_halt(twin, sim):
    forklift = twin.add_robot(name="PF1200-205", asset_id="AST-000205", position=(7, 3))
    task = twin.tasks.create_task({"type": "MOVE_ROBOT", "robot_id": forklift.id, "destination": "7,9"})
    pallet = twin.add_box(name="PAL-9", kind="PALLET", sku="SKU-01", quantity=40, weight=1300.0, position=(7, 3))
    pallet.set_status(BoxStatus.CARRIED)
    for _ in range(10):
        sim.tick()
    forklift.carrying_box = pallet.id                           # an overweight load turns up on its forks
    pallet.position = forklift.position
    for _ in range(10):
        sim.tick()
    assert task.authorization_flagged == "is carrying PAL-9: a 1300 kg load is over its 1200 kg payload limit"
    flagged = twin.events.query(task_id=task.id, event_type="TASK_AUTHORIZATION_CHANGED")
    assert len(flagged) == 1 and not task.is_terminal
    humanoid = twin.add_robot(name="H1-212", asset_id="AST-000212", position=(22, 7))
    assert twin.tasks.physical_recheck(humanoid, Task("task_999", task.type)) == \
        "can't work unsupervised: no operator holds a valid 'humanoid_supervision' credential"


def test_the_new_floor_offers_only_real_destinations(twin, tmp_path):
    keys = {option["key"] for option in twin.warehouse.location_options()}
    assert {"patrol_loop", "walkway", "conveyor", "sorter", "drone_pad", "pallet_racks",
            "tote_shelves", "restricted_area"}.isdisjoint(keys)
    assert {"dock_1", "intake_staging", "tote_aisle_1", "outbound_staging", "charging_station"} <= keys
    classic = make_twin(tmp_path / "classic", layout="classic")
    assert len(classic.warehouse.location_options()) == len(classic.warehouse.zones) - 1  # all but restricted


def test_a_catalog_model_is_read_once(twin, monkeypatch):
    calls = []
    real = twin.inventory.get_model
    monkeypatch.setattr(twin.inventory, "get_model", lambda code: calls.append(code) or real(code))
    first = twin.fleet.model_profile("NW-HH300")
    assert twin.fleet.model_profile("NW-HH300") is first and isinstance(first, MobilityProfile)
    assert calls == ["NW-HH300"]


def test_job_parameters_ride_on_the_task(twin):
    twin.add_robot(name="TR50-201", asset_id="AST-000201", position=(5, 6))
    task = twin.tasks.create_task({"type": "MOVE_ROBOT", "destination": "parking_area", "slot": "TS-10-12-1",
                                   "quantity": 3, "order_id": "ORD-7", "bogus": 1})
    assert task.params == {"slot": "TS-10-12-1", "quantity": 3, "order_id": "ORD-7"}
    assert Task.from_dict(task.to_dict()).params == task.params
    created = twin.events.query(task_id=task.id, event_type="TASK_CREATED")[0]["data"]
    assert created["params"] == task.params
    plain = twin.tasks.create_task({"type": "MOVE_ROBOT", "destination": "parking_area"})
    assert "params" not in twin.events.query(task_id=plain.id, event_type="TASK_CREATED")[0]["data"]
    assert set(JOB_PARAM_KEYS) >= {"slot", "station", "face", "segment"}
    assert parse_cell("21,15") == (21, 15) and parse_cell([3, 4]) == (3, 4) and parse_cell("x") is None
    assert all(f"{kind.value}:" in TASK_TYPE_GUIDE for kind in JOB_SPECS)
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/bin/python -m pytest -o addopts="" -q backend/test_selection.py`
Expected: `1 error` — `ModuleNotFoundError: No module named 'backend.jobs'`.

- [ ] **Step 3: The job framework**

**Create** `backend/jobs.py`:

```python
"""The multi-embodiment floor's job types (spec §9.2): who can do each, what
it needs, and how it is planned.

JOB_SPECS maps each new TaskType to a JobSpec — the embodiment classes that can
do it, the label and agent-guide line it shows, and the functions that check a
request (`check`), plan a robot's steps (`plan`) and name the first cell the
robot must reach (`target`), which robot selection measures distance to and
checks a route for. TaskPlanner.plan and TaskManager hand these types to their
spec; every older job type keeps its own code path on both floors. The job
tasks register their specs at the bottom of this module.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, FrozenSet, List, Optional, Set, Tuple

from .models import Cell, TaskType
from .task_planner import PlanningError

#: (planner, task, robot, blocked) -> (actions, waypoints, handling_ops)
PlanFn = Callable[[Any, Any, Any, Set[Cell]], Tuple[List[Any], List[Cell], int]]
#: (planner, task, origin, profile, layer) -> (cell, label)
TargetFn = Callable[[Any, Any, Cell, Any, str], Tuple[Cell, str]]
#: (manager, task) -> a reason the request is invalid, or None
CheckFn = Callable[[Any, Any], Optional[str]]

#: Task payload keys the new job types read, kept on Task.params.
JOB_PARAM_KEYS = ("slot", "quantity", "station", "face", "dock", "lane", "order_id", "pack_cell", "segment")


@dataclass(frozen=True)
class JobSpec:
    label: str
    guide: str                       # TASK_TYPE_GUIDE's line: what the payload needs
    classes: FrozenSet[str]          # the bodies that can do it (empty: any mobile body)
    check: Optional[CheckFn] = None
    plan: Optional[PlanFn] = None    # None for a human job
    target: Optional[TargetFn] = None
    carries_box: bool = False        # the robot lifts the task's box: payload and kind apply
    air: bool = False                # its targets are on the AIR layer
    human: bool = False              # a person does it (backend/human_jobs.py)


JOB_SPECS: Dict[TaskType, JobSpec] = {}


# --------------------------------------------------------------------------- #
# Helpers the job specs share
# --------------------------------------------------------------------------- #
def task_box(twin: Any, task: Any) -> Any:
    """The task's box, or PlanningError."""
    box = twin.find_box(task.box_id)
    if box is None:
        raise PlanningError(f"Box '{task.box_id}' does not exist")
    return box


def task_slot(twin: Any, task: Any, box: Optional[Any] = None) -> Optional[Any]:
    """The slot a job lifts to or from: its `slot` parameter, else its box's."""
    slot_id = task.params.get("slot") or (box.slot if box is not None else None)
    if not slot_id:
        return None
    slot = twin.warehouse.slot(slot_id)
    if slot is None:
        raise PlanningError(f"Unknown slot {slot_id!r}")
    return slot


def job_levels(twin: Any, task: Any) -> List[int]:
    """The shelf levels a job works at, for the reach rule: its slot's level,
    or every level of the rack face it counts."""
    spec = JOB_SPECS.get(task.type)
    if spec is None:
        return []
    slot_id = task.params.get("slot")
    if not slot_id and spec.carries_box and task.box_id:
        box = twin.find_box(task.box_id)
        slot_id = box.slot if box is not None else None
    if slot_id:
        slot = twin.warehouse.slot(slot_id)
        return [slot.level] if slot is not None else []
    face = task.params.get("face")
    if face:
        cell = parse_cell(face)
        return [slot.level for slot in twin.warehouse.slots_at(cell)] if cell else []
    return []


def parse_cell(spec: Any) -> Optional[Cell]:
    """A cell from "x,y", [x, y] or {"x": .., "y": ..}; None if it isn't one."""
    try:
        if isinstance(spec, dict):
            return int(spec["x"]), int(spec["y"])
        if isinstance(spec, (list, tuple)) and len(spec) == 2:
            return int(spec[0]), int(spec[1])
        if isinstance(spec, str) and "," in spec:
            x, y = spec.split(",", 1)
            return int(x), int(y)
    except (KeyError, TypeError, ValueError):
        return None
    return None


def face_cell(planner: Any, slot: Any, origin: Cell, profile: Any, layer: str,
              blocked: Optional[Set[Cell]] = None) -> Cell:
    """The aisle cell in front of `slot` a body stands on to serve it,
    nearest `origin` among those it can reach."""
    cell = planner.twin.navigation.best_cell_in_zone(slot.faces, origin, blocked=blocked,
                                                     profile=profile, layer=layer)
    if cell is None:
        raise PlanningError(f"No face of slot {slot.slot_id} is reachable")
    return cell
```

- [ ] **Step 4: Routing never snaps when asked not to, and never routes an arm**

**Replace in** `backend/navigation.py`:

```python
        the goal must be a cell the robot may stop on (Warehouse.may_stop): a
        walkway goal is treated like an impassable one.
        """
        blocked_set: Set[Cell] = set(blocked or ())
```

with:

```python
        the goal must be a cell the robot may stop on (Warehouse.may_stop): a
        walkway goal is treated like an impassable one. Fixed equipment (an
        arm) is never routed: that is no routing failure, so none is counted.
        """
        if profile is not None and profile.is_fixed:
            return None
        blocked_set: Set[Cell] = set(blocked or ())
```

**Replace in** `backend/navigation.py`:

```python
    def path_exists(self, start: Cell, goal: Cell, blocked: Optional[Iterable[Cell]] = None,
                    profile: Optional[MobilityProfile] = None, layer: str = GROUND) -> bool:
        return self.find_path(start, goal, blocked=blocked, profile=profile, layer=layer) is not None

    def distance(
        self, start: Cell, goal: Cell, blocked: Optional[Iterable[Cell]] = None,
        profile: Optional[MobilityProfile] = None, layer: str = GROUND,
    ) -> Optional[int]:
        path = self.find_path(start, goal, blocked=blocked, profile=profile, layer=layer)
        return None if path is None else len(path)
```

with:

```python
    def path_exists(self, start: Cell, goal: Cell, blocked: Optional[Iterable[Cell]] = None,
                    profile: Optional[MobilityProfile] = None, layer: str = GROUND,
                    allow_goal_adjacent: bool = True) -> bool:
        """Is there a route? By default a goal the robot can't stop on snaps to
        its nearest neighbour, as always; allow_goal_adjacent=False asks
        whether the robot can reach `goal` itself (spec §9.1's route check)."""
        return self.find_path(start, goal, blocked=blocked, allow_goal_adjacent=allow_goal_adjacent,
                              profile=profile, layer=layer) is not None

    def distance(
        self, start: Cell, goal: Cell, blocked: Optional[Iterable[Cell]] = None,
        profile: Optional[MobilityProfile] = None, layer: str = GROUND,
        allow_goal_adjacent: bool = True,
    ) -> Optional[int]:
        path = self.find_path(start, goal, blocked=blocked, allow_goal_adjacent=allow_goal_adjacent,
                              profile=profile, layer=layer)
        return None if path is None else len(path)
```

- [ ] **Step 5: Real destinations, and each catalog model read once**

**Replace in** `backend/warehouse.py`:

```python
        }

    # Locations a user can pick as a task source or destination.
    def location_options(self) -> List[Dict[str, str]]:
        return [
            {"key": zone.key, "label": zone.label}
            for zone in self.zones.values()
            if zone.cell_type is not CellType.RESTRICTED
        ]

```

with:

```python
        }

    #: Zone types that are never a task's source or destination: places robots
    #: can't stop in, or equipment and storage that are served, not visited.
    _NOT_ENDPOINTS = {
        CellType.RESTRICTED, CellType.WALKWAY, CellType.CONVEYOR, CellType.SORTER,
        CellType.DRONE_PAD, CellType.PALLET_RACK, CellType.TOTE_SHELF,
    }

    # Locations a user can pick as a task source or destination.
    def location_options(self) -> List[Dict[str, str]]:
        if self.layout_name == "classic":
            return [
                {"key": zone.key, "label": zone.label}
                for zone in self.zones.values()
                if zone.cell_type is not CellType.RESTRICTED
            ]
        # On a layered floor, routes (patrol_loop), the walkway, the conveyor,
        # the sorter, the drone pad and the racks are not endpoints.
        return [
            {"key": zone.key, "label": zone.label}
            for zone in self.zones.values()
            if zone.cell_type not in self._NOT_ENDPOINTS and "route" not in zone.attributes
        ]

```

**Replace in** `backend/fleet_bridge.py`:

```python
        self._battery_base: Dict[str, Dict[str, float]] = {}
        service.subscribe(self._on_change)
```

with:

```python
        self._battery_base: Dict[str, Dict[str, float]] = {}
        # Catalog models don't change at runtime, so each body is read once.
        self._profiles: Dict[str, MobilityProfile] = {}
        service.subscribe(self._on_change)
```

**Replace in** `backend/fleet_bridge.py`:

```python
            self._ota_ticks.clear()
            self._battery_base.clear()

    # ---- embodiment ------------------------------------------------------ #
    def model_profile(self, model_code: str) -> MobilityProfile:
        """The body a robot of catalog model `model_code` has (spec §5.1)."""
        model = self.service.get_model(model_code)
        return MobilityProfile.from_model(model["spec"], model["embodiment_class"])

    def floor_profile(self, model_code: str) -> Optional[MobilityProfile]:
```

with:

```python
            self._ota_ticks.clear()
            self._battery_base.clear()
            self._profiles.clear()

    # ---- embodiment ------------------------------------------------------ #
    def model_profile(self, model_code: str) -> MobilityProfile:
        """The body a robot of catalog model `model_code` has (spec §5.1),
        read from the catalog once per model: asset changes rebuild a robot's
        profile on every heartbeat-driven update, and the catalog is fixed."""
        profile = self._profiles.get(model_code)
        if profile is None:
            model = self.service.get_model(model_code)
            profile = MobilityProfile.from_model(model["spec"], model["embodiment_class"])
            self._profiles[model_code] = profile
        return profile

    def floor_profile(self, model_code: str) -> Optional[MobilityProfile]:
```

- [ ] **Step 6: The planner hands new job types to their spec**

**Replace in** `backend/task_planner.py`:

```python

        else:
            raise PlanningError(f"{task.type.value} does not need a movement plan")

        # ---- battery-aware planning ---------------------------------- #
```

with:

```python

        else:
            from .jobs import JOB_SPECS  # deferred: jobs.py imports this module

            spec = JOB_SPECS.get(task.type)
            if spec is None or spec.plan is None:
                raise PlanningError(f"{task.type.value} does not need a movement plan")
            actions, waypoints, handling_ops = spec.plan(self, task, robot, blocked)

        # ---- battery-aware planning ---------------------------------- #
```

- [ ] **Step 7: The capability filter, the gate and the mid-job re-check**

**Replace in** `backend/task_manager.py`:

```python
    now_hms,
    now_iso,
)
from .eligibility import agent_eligibility, operator_eligibility, robot_eligibility
from .embodiment import GROUND
from .energy import charger_zone
from .llm import narrate
from .task_planner import PlanningError


class Task:
```

with:

```python
    now_hms,
    now_iso,
)
from . import people
from .eligibility import (
    agent_eligibility, box_kind_ok, operator_eligibility, payload_ok, reach_ok, robot_eligibility,
)
from .embodiment import AIR, GROUND
from .energy import charger_zone
from .jobs import JOB_PARAM_KEYS, JOB_SPECS, job_levels
from .llm import narrate
from .task_planner import PlanningError

#: The older job types whose robot lifts the task's box(es).
BOX_HANDLING_TYPES = frozenset({
    TaskType.PICK_AND_DELIVER, TaskType.PICK_BOX, TaskType.DELIVER_BOX, TaskType.MOVE_BOX,
    TaskType.BATCH_DELIVER,
})


def _article(word: str) -> str:
    """'an AMR', 'a FORKLIFT'."""
    return f"{'an' if word[:1] in 'AEIOU' else 'a'} {word}"


class Task:
```

**Replace in** `backend/task_manager.py`:

```python
        dual_signoff: bool = False,
    ) -> None:
```

with:

```python
        dual_signoff: bool = False,
        params: Optional[Dict[str, Any]] = None,
    ) -> None:
```

**Replace in** `backend/task_manager.py`:

```python
        self.destination = destination
        self.priority = priority
```

with:

```python
        self.destination = destination
        # What a new-floor job type needs beyond box and destination (its
        # slot, station, quantity, order, ...: jobs.JOB_PARAM_KEYS).
        self.params: Dict[str, Any] = dict(params or {})
        self.priority = priority
```

**Replace in** `backend/task_manager.py`:

```python
    def summary(self) -> str:
        if self.type in (TaskType.PICK_AND_DELIVER, TaskType.MOVE_BOX, TaskType.DELIVER_BOX):
```

with:

```python
    def summary(self) -> str:
        spec = JOB_SPECS.get(self.type)
        if spec is not None:
            subject = (self.box_id or self.params.get("face") or self.params.get("segment")
                       or self.params.get("order_id"))
            return f"{spec.label.lower()} {subject}" if subject else spec.label.lower()
        if self.type in (TaskType.PICK_AND_DELIVER, TaskType.MOVE_BOX, TaskType.DELIVER_BOX):
```

**Replace in** `backend/task_manager.py`:

```python
            "destination": self.destination,
            "priority": self.priority.value,
```

with:

```python
            "destination": self.destination,
            "params": dict(self.params),
            "priority": self.priority.value,
```

**Replace in** `backend/task_manager.py`:

```python
            dual_signoff=data.get("dual_signoff", False),
        )
```

with:

```python
            dual_signoff=data.get("dual_signoff", False),
            params=data.get("params"),
        )
```

**Replace in** `backend/task_manager.py`:

```python
                dual_signoff=bool(payload.get("dual_signoff")),
            )
            self.tasks[task.id] = task

        task.record(TaskStatus.CREATED, f"Task created ({task.type.value}, {priority.value} priority)")
        events.emit(
```

with:

```python
                dual_signoff=bool(payload.get("dual_signoff")),
                params={key: payload[key] for key in JOB_PARAM_KEYS if payload.get(key) is not None},
            )
            self.tasks[task.id] = task

        task.record(TaskStatus.CREATED, f"Task created ({task.type.value}, {priority.value} priority)")
        # The full task definition, not just priority/requested_robot —
        # this is "the task" input an eval reads (see
        # evals/generate_tests_groq.py::_extract_task) so it doesn't
        # have to regex the free-text message to know what was asked
        # for.
        definition = {
            "priority": priority.value,
            "requested_robot": task.requested_robot,
            "requested_agent": task.requested_agent,
            "requested_operator": task.requested_operator,
            "required_certification": task.required_certification,
            "task_type": task.type.value,
            "box_id": task.box_id,
            "box_ids": list(task.box_ids),
            "source": task.source,
            "destination": task.destination,
            "summary": task.summary(),
        }
        if task.params:  # only the new job types carry parameters
            definition["params"] = dict(task.params)
        events.emit(
```

**Replace in** `backend/task_manager.py`:

```python
            box_id=task.box_id,
            # The full task definition, not just priority/requested_robot —
            # this is "the task" input an eval reads (see
            # evals/generate_tests_groq.py::_extract_task) so it doesn't
            # have to regex the free-text message to know what was asked
            # for.
            data={
                "priority": priority.value,
                "requested_robot": task.requested_robot,
                "requested_agent": task.requested_agent,
                "requested_operator": task.requested_operator,
                "required_certification": task.required_certification,
                "task_type": task.type.value,
                "box_id": task.box_id,
                "box_ids": list(task.box_ids),
                "source": task.source,
                "destination": task.destination,
                "summary": task.summary(),
            },
        )
```

with:

```python
            box_id=task.box_id,
            data=definition,
        )
```

**Replace in** `backend/task_manager.py`:

```python
        planner = twin.planner

        # AI agent — existence/availability, AND eligibility (approved
        # model_version) are both gated here. An ineligible agent used to
```

with:

```python
        planner = twin.planner

        # A new-floor job type checks its own request first (its box, slot,
        # station) — before any actor is chosen for it.
        spec = JOB_SPECS.get(task.type)
        if spec is not None and spec.check is not None:
            reason = spec.check(self, task)
            if reason:
                return False, reason

        # AI agent — existence/availability, AND eligibility (approved
        # model_version) are both gated here. An ineligible agent used to
```

**Replace in** `backend/task_manager.py`:

```python
            if task.type == TaskType.CHARGE_ROBOT and robot.mains_powered:
                return False, f"{robot.name} is mains-powered and never needs charging"
        elif not twin.robots:
            return False, "No robots exist in the warehouse"

        # Box
```

with:

```python
            if task.type == TaskType.CHARGE_ROBOT and robot.mains_powered:
                return False, f"{robot.name} is mains-powered and never needs charging"
            reason = self.capability_reason(robot, task)
            if reason:
                return False, f"{robot.name} {reason}"
        elif not twin.robots:
            return False, "No robots exist in the warehouse"
        elif twin.layout_name != "classic":
            # AUTO on a layered floor: some robot's body must be able to do it.
            reason = self._fleet_reason(task)
            if reason:
                return False, reason

        # Box
```

**Replace in** `backend/task_manager.py`:

```python
        # A named robot's targets are resolved for its own body and layer.
        route: Dict[str, Any] = {"profile": robot.mobility, "layer": robot.layer} if robot else {}
        if needs_destination:
            if not task.destination:
                return False, "This task type needs a destination"
            origin = robot.position if robot else self._auto_origin()
            try:
                planner.resolve_target(task.destination, origin, **route)
            except PlanningError as exc:
                return False, str(exc)

        # Source (optional, but if given it must exist)
        if task.source:
            try:
```

with:

```python
        # A named robot's targets are resolved for its own body and layer.
        route: Dict[str, Any] = self._route(task, robot) if robot else {}
        # An AUTO task on a layered floor was resolved robot by robot above.
        resolve = robot is not None or twin.layout_name == "classic"
        if needs_destination:
            if not task.destination:
                return False, "This task type needs a destination"
            origin = robot.position if robot else self._auto_origin()
            try:
                if resolve:
                    planner.resolve_target(task.destination, origin, **route)
            except PlanningError as exc:
                return False, str(exc)

        # Source (optional, but if given it must exist)
        if task.source and resolve:
            try:
```

**Replace in** `backend/task_manager.py`:

```python
            except PlanningError as exc:
                return False, str(exc)
            if target_cell is not None and not twin.navigation.path_exists(robot.position, target_cell, **route):
                return False, f"No path from {robot.name} to ({target_cell[0]},{target_cell[1]})"

        return True, None

    def _auto_origin(self) -> Optional[Cell]:
```

with:

```python
            except PlanningError as exc:
                return False, str(exc)
            if target_cell is not None and not self._can_reach(robot, target_cell, route):
                return False, f"No path from {robot.name} to ({target_cell[0]},{target_cell[1]})"

        return True, None

    # ------------------------------------------------------------------ #
    # The physical rules (spec §9.1, §10.1, §10.2)
    # ------------------------------------------------------------------ #
    def capability_reason(self, robot: Any, task: Task) -> Optional[str]:
        """Why `robot`'s body can't do `task`, or None — the capability filter
        of spec §9.1, shared by the gate and robot selection. Covers the kind
        of body the job needs, the kind and declared weight of the box it
        lifts, the slot level against its reach, and (the humanoid) that
        supervision can be had. The route is checked with the target. A
        classic robot has no body to check."""
        profile = robot.mobility
        if profile is None:
            return None
        spec = JOB_SPECS.get(task.type)
        if spec is not None:
            if spec.classes and profile.embodiment_class not in spec.classes:
                needs = " or ".join(_article(kind) for kind in sorted(spec.classes))
                return f"is {_article(profile.embodiment_class)}; {task.type.value} needs {needs}"
        elif profile.is_fixed and task.type != TaskType.CHARGE_ROBOT:
            return f"is fixed equipment and can't do {task.type.value}"
        for box in self._lifted_boxes(task):
            for ok, reason in (box_kind_ok(box.kind.value, profile.box_kinds),
                               payload_ok(box.declared_weight_kg, profile.max_payload_kg)):
                if not ok:
                    return f"can't take {box.name}: {reason}"
        for level in job_levels(self.twin, task):
            ok, reason = reach_ok(level, profile.max_shelf_level)
            if not ok:
                return f"can't work there: {reason}"
        ok, reason = people.supervision_available(self.twin, robot)
        if not ok:
            return f"can't work unsupervised: {reason}"
        return None

    def physical_recheck(self, robot: Any, task: Task) -> Optional[str]:
        """The rules re-checked while a job runs (spec §10.2): what the robot
        carries against its payload, the job's levels against its reach, and
        (the humanoid) whether supervision can still be had. It only flags."""
        profile = robot.mobility
        if profile is None:
            return None
        box = self.twin.find_box(robot.carrying_box) if robot.carrying_box else None
        if box is not None:
            ok, reason = payload_ok(box.declared_weight_kg, profile.max_payload_kg)
            if not ok:
                return f"is carrying {box.name}: {reason}"
        for level in job_levels(self.twin, task):
            ok, reason = reach_ok(level, profile.max_shelf_level)
            if not ok:
                return f"can't work there: {reason}"
        ok, reason = people.supervision_available(self.twin, robot)
        if not ok:
            return f"can't work unsupervised: {reason}"
        return None

    def _lifted_boxes(self, task: Task) -> List[Any]:
        """The boxes the robot itself lifts for `task` (payload and kind apply)."""
        spec = JOB_SPECS.get(task.type)
        if task.type in BOX_HANDLING_TYPES or (spec is not None and spec.carries_box):
            ids = task.box_ids if task.type == TaskType.BATCH_DELIVER else [task.box_id]
            return [box for box in (self.twin.find_box(box_id) for box_id in ids if box_id) if box is not None]
        return []

    def _route(self, task: Task, robot: Any) -> Dict[str, Any]:
        """The body and layer a robot's targets for `task` resolve on: a
        drone's jobs are flown, so their targets are on AIR."""
        spec = JOB_SPECS.get(task.type)
        layer = AIR if spec is not None and spec.air else robot.layer
        return {"profile": robot.mobility, "layer": layer}

    def _can_reach(self, robot: Any, cell: Cell, route: Dict[str, Any]) -> bool:
        """Can `robot` get to `cell` itself? On a layered floor the goal is
        never snapped to a neighbour (spec §9.1), and an arm reaches only the
        cell it stands on. Classic keeps today's check."""
        if robot.mobility is None:
            return self.twin.navigation.path_exists(robot.position, cell, **route)
        if robot.mobility.is_fixed:
            return cell == robot.position
        return self.twin.navigation.path_exists(robot.position, cell, allow_goal_adjacent=False, **route)

    def _reach(self, task: Task, robot: Any) -> Optional[Tuple[Optional[Cell], str, int]]:
        """(target cell, label, distance) for `robot` doing `task`: the target
        resolved for its own body from where it stands, the distance a route
        it can drive with no goal snapping. None if it can't get there."""
        route = self._route(task, robot)
        try:
            cell, label = self._primary_target(task, robot.position, **route)
        except PlanningError:
            return None
        if cell is None:
            return None, label, 0
        if robot.mobility.is_fixed:
            return (cell, label, 0) if cell == robot.position else None
        distance = self.twin.navigation.distance(robot.position, cell, allow_goal_adjacent=False, **route)
        return None if distance is None else (cell, label, distance)

    def _fleet_reason(self, task: Task) -> Optional[str]:
        """Why no robot on the floor could ever do this AUTO task — busy or
        not — or None if one can. A request no body can do is rejected at
        the gate (its order records why) instead of waiting forever."""
        twin = self.twin
        reasons: List[str] = []
        for robot in twin.robots.values():
            if robot.mobility is None:
                return None
            reason = self.capability_reason(robot, task)
            if reason is None:
                if task.type == TaskType.CHARGE_ROBOT and robot.mains_powered:
                    reason = "is mains-powered"
                elif self._reach(task, robot) is None:
                    reason = "has no route to the job"
                elif (task.destination and task.type not in JOB_SPECS
                      and not self._resolves(task.destination, robot, task)):
                    reason = f"can't reach {task.destination}"  # a job type's spec checks its own
            if reason is None:
                return None
            reasons.append(f"{robot.name} {reason}")
        if not reasons:
            return None
        shown = "; ".join(reasons[:3]) + (f" (and {len(reasons) - 3} more)" if len(reasons) > 3 else "")
        return f"No robot can do this {task.type.value}: {shown}"

    def _resolves(self, spec: str, robot: Any, task: Task) -> bool:
        try:
            self.twin.planner.resolve_target(spec, robot.position, **self._route(task, robot))
        except PlanningError:
            return False
        return True

    def _auto_origin(self) -> Optional[Cell]:
```

**Replace in** `backend/task_manager.py`:

```python
        route = {"profile": profile, "layer": layer}
        if task.type in (TaskType.PICK_AND_DELIVER, TaskType.PICK_BOX, TaskType.MOVE_BOX):
```

with:

```python
        route = {"profile": profile, "layer": layer}
        spec = JOB_SPECS.get(task.type)
        if spec is not None:
            return spec.target(planner, task, origin, profile, layer) if spec.target else (None, "")
        if task.type in (TaskType.PICK_AND_DELIVER, TaskType.PICK_BOX, TaskType.MOVE_BOX):
```

**Replace in** `backend/task_manager.py`:

```python
                continue
            distance = None
            if target_cell is not None:
                distance = twin.navigation.distance(robot.position, target_cell,
                                                    profile=robot.mobility, layer=robot.layer)
                if distance is None:
                    continue
            distance = distance if distance is not None else 0
            battery_penalty = max(0.0, (100.0 - robot.battery) * 0.25)
```

with:

```python
                continue
            if robot.mobility is not None:
                # A layered floor (spec §9.1): the body must be able to do the
                # job, and the distance is to the target resolved for this body
                # from where it stands, along a route it can drive — never to
                # a neighbour the goal was snapped to.
                if self.capability_reason(robot, task) is not None:
                    continue
                reach = self._reach(task, robot)
                if reach is None:
                    continue
                _, target_label, distance = reach
            else:
                distance = None
                if target_cell is not None:
                    distance = twin.navigation.distance(robot.position, target_cell,
                                                        profile=robot.mobility, layer=robot.layer)
                    if distance is None:
                        continue
                distance = distance if distance is not None else 0
            battery_penalty = max(0.0, (100.0 - robot.battery) * 0.25)
```

**Replace in** `backend/simulator.py`:

```python
                task_type=task.type.value, allowed_task_types=robot.allowed_task_types,
            )
            if reason:
```

with:

```python
                task_type=task.type.value, allowed_task_types=robot.allowed_task_types,
            ) or twin.tasks.physical_recheck(robot, task)  # payload, reach, supervision (spec §10.2)
            if reason:
```

- [ ] **Step 8: The registered job types show in the options and the agent's guide**

**Replace in** `backend/digital_twin.py`:

```python
from .inventory.catalog_data import CLASS_DEFAULT_MODELS
from .logger import WarehouseLogger
```

with:

```python
from .inventory.catalog_data import CLASS_DEFAULT_MODELS
from .jobs import JOB_SPECS
from .logger import WarehouseLogger
```

**Replace in** `backend/digital_twin.py`:

```python
                {"id": TaskType.BATCH_DELIVER.value, "label": "Batch deliver (multiple boxes)"},
            ],
            # The task types it actually makes sense to restrict a robot
```

with:

```python
                {"id": TaskType.BATCH_DELIVER.value, "label": "Batch deliver (multiple boxes)"},
            ] + [{"id": kind.value, "label": spec.label} for kind, spec in JOB_SPECS.items()],
            # The task types it actually makes sense to restrict a robot
```

**Replace in** `backend/digital_twin.py`:

```python
                {"id": TaskType.BATCH_DELIVER.value, "label": "Batch deliver (multiple boxes)"},
            ],
            "robot_classes": [
```

with:

```python
                {"id": TaskType.BATCH_DELIVER.value, "label": "Batch deliver (multiple boxes)"},
            ] + [{"id": kind.value, "label": spec.label} for kind, spec in JOB_SPECS.items() if not spec.human],
            "robot_classes": [
```

**Replace in** `backend/agent_tools.py`:

```python
from .eval_engine import evaluate_events
from .models import LogCategory, TaskType, Priority
```

with:

```python
from .eval_engine import evaluate_events
from .jobs import JOB_SPECS
from .models import LogCategory, TaskType, Priority
```

**Replace in** `backend/agent_tools.py`:

```python
    "AGENT_AUDIT:agent_id(reviews recent logs/CI) | "
    "OPERATOR_APPROVAL:operator_id[+robot_id](needs safety_inspection) | "
    "OPERATOR_MAINTENANCE_SIGNOFF:operator_id[+robot_id](needs electrical_safety, resets wear)"
)


# --------------------------------------------------------------------------- #
```

with:

```python
    "AGENT_AUDIT:agent_id(reviews recent logs/CI) | "
    "OPERATOR_APPROVAL:operator_id[+robot_id](needs safety_inspection) | "
    "OPERATOR_MAINTENANCE_SIGNOFF:operator_id[+robot_id](needs electrical_safety, resets wear)"
) + "".join(f" | {kind.value}:{spec.guide}" for kind, spec in JOB_SPECS.items())  # the new-floor jobs


# --------------------------------------------------------------------------- #
```

- [ ] **Step 9: Run the new tests with the mobility and agent tests**

Run: `.venv/bin/python -m pytest -o addopts="" -q backend/test_selection.py backend/test_robot_mobility.py backend/test_agent_chat.py`
Expected: `49 passed`.

- [ ] **Step 10: Run the full suite**

Every classic AUTO test keeps its winner: robots with no profile are scored exactly as before, from the first robot's resolved target.

Run: `.venv/bin/python -m pytest -o addopts="" -q`
Expected: `2 failed, 550 passed`.

- [ ] **Step 11: Commit**

```bash
git add backend/jobs.py backend/navigation.py backend/warehouse.py backend/fleet_bridge.py \
        backend/task_planner.py backend/task_manager.py backend/simulator.py backend/digital_twin.py \
        backend/agent_tools.py backend/test_selection.py
git commit -m "feat: capability-filtered robot selection and the physical gate

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Pallet jobs — unload, put away, retrieve, load

The first four §9.2 job types, registered in `backend/jobs.py`: `UNLOAD_TRUCK` (a heavy hauler up to its 300 kg, or a forklift: NAVIGATE dock → PICK pallet → NAVIGATE intake_staging → DELIVER), `PUTAWAY_PALLET` (forklift: NAVIGATE staging → PICK → NAVIGATE face cell → LIFT_TO(level) → PLACE → LOWER), `RETRIEVE_PALLET` (forklift: NAVIGATE face → LIFT_TO → GRASP → LOWER → NAVIGATE outbound_staging → DELIVER) and `LOAD_TRUCK` (forklift: NAVIGATE outbound_staging → PICK → NAVIGATE dock_3 → DELIVER, and the pallet becomes `SHIPPED`). Each spec checks its request (the box is a pallet, on the floor or in a slot as the job needs, held by no other job; an explicit slot exists, takes pallets, is free and promised to no other job), and a put-away with no slot takes the nearest free pallet slot within the robot's reach and records it on the task. Their PICK and DELIVER take the forks' 3 s `grasp_s`/`place_s` (Task 4), they hand off at the staging areas, and Task 7's filter keeps an over-heavy pallet away from the hauler. A pallet a WRONG_LEVEL fault misplaced can't be retrieved from its recorded slot: the job fails with the reason.

**Files:**
- Modify: `backend/models.py` (`TaskType.UNLOAD_TRUCK`, `PUTAWAY_PALLET`, `RETRIEVE_PALLET`, `LOAD_TRUCK`; the forklift and hauler presets)
- Modify: `backend/simulator.py` (a DELIVER with `ship` leaves the pallet `SHIPPED`)
- Modify: `backend/jobs.py` (shared checks and targets; the four specs)
- Test: `backend/test_pallet_jobs.py` (create)

**Interfaces:**
- Consumes (Tasks 2, 4, 7): `goods.free_slots`; `Action(level=, slot_id=, params=)`, `ActionType.LIFT_TO`/`GRASP`/`PLACE`/`LOWER`; `JobSpec`, `JOB_SPECS`, `task_box`, `task_slot`, `face_cell`; `TaskManager.active_task_for_box`, `twin.tasks.tasks`.
- Produces:
  - `TaskType.UNLOAD_TRUCK`, `PUTAWAY_PALLET`, `RETRIEVE_PALLET`, `LOAD_TRUCK`; FORKLIFT allows all four, HEAVY_HAULER allows `UNLOAD_TRUCK`.
  - Payloads: `UNLOAD_TRUCK {box_id[, destination="intake_staging"]}`, `PUTAWAY_PALLET {box_id[, slot]}`, `RETRIEVE_PALLET {box_id[, destination="outbound_staging"]}`, `LOAD_TRUCK {box_id[, dock="dock_3"]}`.
  - `backend.jobs`: `promised_slots(twin, exclude=None) -> Set[str]`, `check_box(manager, task, kind, in_slot=None)`, `check_slot(manager, task, kind)`, `check_zone(manager, zone)`, `floor_target`, `slot_target`, `drop_cell(planner, zone, origin, blocked, route)`, `lift_steps(slot, face, verb, box) -> List[Action]`, `choose_slot(planner, task, robot, kind, near)`, `FILLS_SLOT: Set[TaskType]` (Task 9 adds the tote jobs to it).
  - `DELIVER` action `params={"ship": True}` marks the box `SHIPPED`.

- [ ] **Step 1: Write the failing tests**

**Create** `backend/test_pallet_jobs.py`:

```python
"""Pallet jobs (multi-embodiment spec §9.2): off the inbound truck, into a rack
slot, out again to outbound staging and onto the outbound truck."""
import pytest

from backend.agent_tools import TASK_TYPE_GUIDE
from backend.digital_twin import DigitalTwin
from backend.embodiment import step_ticks
from backend.models import ROBOT_CLASS_PRESETS, BoxStatus, SimulationStatus, TaskStatus
from backend.simulator import Simulator


@pytest.fixture
def twin(tmp_path):
    return DigitalTwin(log_dir=str(tmp_path / "logs"), data_dir=str(tmp_path / "data"),
                       persist_logs=False, demo=True, demo_tasks=False, layout="distribution_center")


@pytest.fixture
def sim(twin):
    simulator = Simulator(twin)
    twin.simulation_status = SimulationStatus.RUNNING
    return simulator


@pytest.fixture
def forklift(twin):
    return twin.add_robot(name="PF1200-205", asset_id="AST-000205", position=(7, 4))


def run(sim, task, max_ticks=1500):
    for _ in range(max_ticks):
        if task.is_terminal:
            return
        sim.tick()
    assert task.is_terminal, f"{task.id} still {task.status.value}"


def steps(twin, task):
    return [e["data"]["step"] for e in twin.events.query(task_id=task.id, event_type="ROBOT_STEP")
            if e["data"]["step"] != "ENTER_ZONE"]


def pallet(twin, name="PAL-1", weight=600.0, **where):
    return twin.add_box(name=name, kind="PALLET", sku="SKU-01", quantity=40, weight=weight, **where)


def test_unloading_takes_a_pallet_off_the_truck_to_intake(twin, sim, forklift):
    hauler = twin.add_robot(name="HH300-207", asset_id="AST-000207", position=(2, 8))
    light = pallet(twin, "PAL-1", weight=250.0, position=(2, 7))
    task = twin.tasks.create_task({"type": "UNLOAD_TRUCK", "box_id": light.id})
    assert task.status is TaskStatus.PLANNING and task.destination == "intake_staging"
    run(sim, task)
    assert task.status is TaskStatus.COMPLETED and task.robot_id == hauler.id    # the nearer able body
    assert twin.warehouse.zone_of_cell(light.position).key == "intake_staging"
    assert light.status is BoxStatus.DELIVERED
    assert steps(twin, task) == ["NAVIGATE", "PICK", "NAVIGATE", "DELIVER"]
    handoff = twin.events.query(task_id=task.id, event_type="HANDOFF")[-1]["data"]
    assert (handoff["from"], handoff["to"]) == (hauler.id, "intake_staging")
    heavy = pallet(twin, "PAL-2", weight=800.0, position=(2, 9))
    named = twin.tasks.create_task({"type": "UNLOAD_TRUCK", "box_id": heavy.id, "robot_id": hauler.id})
    assert named.error == "HH300-207 can't take PAL-2: a 800 kg load is over its 300 kg payload limit"
    auto = twin.tasks.create_task({"type": "UNLOAD_TRUCK", "box_id": heavy.id})
    run(sim, auto)
    assert auto.robot_id == forklift.id


def test_a_pallet_is_put_away_in_the_slot_it_was_given(twin, sim, forklift):
    box = pallet(twin, position=(5, 4))
    task = twin.tasks.create_task({"type": "PUTAWAY_PALLET", "box_id": box.id, "slot": "PR-08-02-3"})
    run(sim, task)
    assert task.status is TaskStatus.COMPLETED, task.error
    assert (box.status, box.slot, box.position) == (BoxStatus.STORED, "PR-08-02-3", (8, 2))
    assert twin.stock.location("PR-08-02-3").box_id == box.id
    assert steps(twin, task) == ["NAVIGATE", "PICK", "NAVIGATE", "LIFT_TO", "PLACE", "LOWER"]
    assert twin.events.query(task_id=task.id, event_type="PLACED")[0]["data"]["level"] == 3
    picked = twin.events.query(task_id=task.id, event_type="HANDOFF")[0]["data"]
    assert (picked["from"], picked["to"]) == ("intake_staging", forklift.id)


def test_a_putaway_with_no_slot_takes_the_nearest_free_one_in_reach(twin, sim, forklift):
    pallet(twin, "PAL-0", slot="PR-08-02-0")
    box = pallet(twin, position=(5, 3))
    task = twin.tasks.create_task({"type": "PUTAWAY_PALLET", "box_id": box.id})
    run(sim, task)
    assert task.status is TaskStatus.COMPLETED and task.params["slot"] == "PR-08-02-1" == box.slot


def test_a_putaway_needs_a_free_pallet_slot(twin, forklift):
    pallet(twin, "PAL-0", slot="PR-08-02-0")
    box = pallet(twin, position=(5, 3))
    for slot, error in (("PR-08-02-0", "Slot PR-08-02-0 already holds"), ("TS-10-12-1", "holds TOTEs"),
                        ("PR-99-02-0", "Unknown slot")):
        task = twin.tasks.create_task({"type": "PUTAWAY_PALLET", "box_id": box.id, "slot": slot})
        assert task.status is TaskStatus.FAILED and error in task.error
    other = pallet(twin, "PAL-2", position=(5, 5))
    first = twin.tasks.create_task({"type": "PUTAWAY_PALLET", "box_id": box.id, "slot": "PR-09-02-0"})
    assert first.status is TaskStatus.PLANNING
    second = twin.tasks.create_task({"type": "PUTAWAY_PALLET", "box_id": other.id, "slot": "PR-09-02-0"})
    assert second.error == "Slot PR-09-02-0 is promised to another job"
    tote = twin.add_box(name="TOTE-1", kind="TOTE", sku="SKU-02", quantity=10, weight=8.0, slot="TS-10-12-1")
    wrong = twin.tasks.create_task({"type": "PUTAWAY_PALLET", "box_id": tote.id})
    assert wrong.error == "TOTE-1 is a TOTE, not a PALLET"


def test_a_pallet_is_retrieved_and_loaded_onto_the_truck(twin, sim, forklift):
    box = pallet(twin, slot="PR-10-05-2")
    retrieve = twin.tasks.create_task({"type": "RETRIEVE_PALLET", "box_id": box.id})
    run(sim, retrieve)
    assert retrieve.status is TaskStatus.COMPLETED, retrieve.error
    assert twin.warehouse.zone_of_cell(box.position).key == "outbound_staging"
    assert box.slot is None and twin.stock.location("PR-10-05-2") is None
    assert steps(twin, retrieve) == ["NAVIGATE", "LIFT_TO", "GRASP", "LOWER", "NAVIGATE", "DELIVER"]
    load = twin.tasks.create_task({"type": "LOAD_TRUCK", "box_id": box.id})
    assert load.destination == "dock_3"
    run(sim, load)
    assert load.status is TaskStatus.COMPLETED and box.status is BoxStatus.SHIPPED
    assert twin.warehouse.zone_of_cell(box.position).key == "dock_3"
    assert twin.box_at(box.position) is None                    # gone with the truck


def test_new_job_picks_take_the_forks_engage_time(twin, sim, forklift):
    box = pallet(twin, position=(7, 4))                          # right under the forks
    task = twin.tasks.create_task({"type": "LOAD_TRUCK", "box_id": box.id, "robot_id": forklift.id})
    sim.tick()                                                   # assigned and started: NAVIGATE is done
    started = twin.tick_count
    while box.status is not BoxStatus.CARRIED:
        sim.tick()
    assert twin.tick_count - started == 1 + step_ticks(forklift.mobility, "GRASP")
    assert task.status is not TaskStatus.FAILED


def test_a_misplaced_pallet_cannot_be_retrieved_from_its_record(twin, sim, forklift):
    box = pallet(twin, position=(5, 4))
    twin.faults.arm("wrong_level")
    putaway = twin.tasks.create_task({"type": "PUTAWAY_PALLET", "box_id": box.id, "slot": "PR-08-02-2"})
    run(sim, putaway)
    assert putaway.status is TaskStatus.COMPLETED and box.true_slot == "PR-08-02-3"
    retrieve = twin.tasks.create_task({"type": "RETRIEVE_PALLET", "box_id": box.id})
    run(sim, retrieve)
    assert retrieve.status is TaskStatus.FAILED and "PAL-1 is not in slot PR-08-02-2" in retrieve.error


def test_the_pallet_jobs_are_offered_and_allowed(twin):
    kinds = {option["id"] for option in twin.options()["task_types"]}
    assert {"UNLOAD_TRUCK", "PUTAWAY_PALLET", "RETRIEVE_PALLET", "LOAD_TRUCK"} <= kinds
    assert "PUTAWAY_PALLET:box_id(a staged pallet)" in TASK_TYPE_GUIDE
    assert {"UNLOAD_TRUCK", "LOAD_TRUCK"} <= set(ROBOT_CLASS_PRESETS["FORKLIFT"]["allowed_task_types"])
    assert "UNLOAD_TRUCK" in ROBOT_CLASS_PRESETS["HEAVY_HAULER"]["allowed_task_types"]
    assert "PUTAWAY_PALLET" not in ROBOT_CLASS_PRESETS["HEAVY_HAULER"]["allowed_task_types"]
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/bin/python -m pytest -o addopts="" -q backend/test_pallet_jobs.py`
Expected: `8 failed` — `ValueError: Unknown task type 'UNLOAD_TRUCK'` (and the other three).

- [ ] **Step 3: The job types and who may run them**

**Replace in** `backend/models.py`:

```python
        "allowed_task_types": [
            "PICK_AND_DELIVER", "PICK_BOX", "DELIVER_BOX", "MOVE_BOX",
            "MOVE_ROBOT", "CHARGE_ROBOT",
        ],
    },
    "SCOUT": {
```

with:

```python
        "allowed_task_types": [
            "PICK_AND_DELIVER", "PICK_BOX", "DELIVER_BOX", "MOVE_BOX",
            "MOVE_ROBOT", "CHARGE_ROBOT",
            "UNLOAD_TRUCK", "PUTAWAY_PALLET", "RETRIEVE_PALLET", "LOAD_TRUCK",
        ],
    },
    "SCOUT": {
```

**Replace in** `backend/models.py`:

```python
            "PICK_AND_DELIVER", "PICK_BOX", "DELIVER_BOX", "MOVE_BOX",
            "MOVE_ROBOT", "CHARGE_ROBOT", "BATCH_DELIVER",
        ],
```

with:

```python
            "PICK_AND_DELIVER", "PICK_BOX", "DELIVER_BOX", "MOVE_BOX",
            "MOVE_ROBOT", "CHARGE_ROBOT", "BATCH_DELIVER", "UNLOAD_TRUCK",
        ],
```

**Replace in** `backend/models.py`:

```python
    # NAVIGATE/PICK/NAVIGATE/DELIVER repeated per box under one task id
    # instead of one task per box. See `box_ids` on Task.
    BATCH_DELIVER = "BATCH_DELIVER"


class Priority(str, enum.Enum):
```

with:

```python
    # NAVIGATE/PICK/NAVIGATE/DELIVER repeated per box under one task id
    # instead of one task per box. See `box_ids` on Task.
    BATCH_DELIVER = "BATCH_DELIVER"
    # The distribution-centre floor's jobs (multi-embodiment spec §9.2), planned
    # by their JobSpec in backend/jobs.py. Pallets: off an inbound truck to
    # intake staging, into a rack slot, out of one to outbound staging, and
    # onto the outbound truck at dock_3.
    UNLOAD_TRUCK = "UNLOAD_TRUCK"
    PUTAWAY_PALLET = "PUTAWAY_PALLET"
    RETRIEVE_PALLET = "RETRIEVE_PALLET"
    LOAD_TRUCK = "LOAD_TRUCK"


class Priority(str, enum.Enum):
```

- [ ] **Step 4: Loading a truck ships the pallet**

**Replace in** `backend/simulator.py`:

```python
        box.destination = action.target_name or box.destination
        box.delivery_count += 1
```

with:

```python
        box.destination = action.target_name or box.destination
        if action.params.get("ship"):
            box.set_status(BoxStatus.SHIPPED)  # loaded onto the outbound truck (LOAD_TRUCK)
        box.delivery_count += 1
```

- [ ] **Step 5: The pallet job specs**

**Replace in** `backend/jobs.py`:

```python
from typing import Any, Callable, Dict, FrozenSet, List, Optional, Set, Tuple

from .models import Cell, TaskType
from .task_planner import PlanningError

```

with:

```python
from typing import Any, Callable, Dict, FrozenSet, List, Optional, Set, Tuple

from . import goods
from .models import Action, ActionType, BoxKind, BoxStatus, Cell, TaskType
from .task_planner import PlanningError

```

**Replace in** `backend/jobs.py`:

```python
    return cell

```

with:

```python
    return cell


def promised_slots(twin: Any, exclude: Optional[str] = None) -> Set[str]:
    """Slots that jobs not yet finished are going to fill."""
    return {task.params["slot"] for task in twin.tasks.tasks.values()
            if task.id != exclude and not task.is_terminal and task.params.get("slot")
            and task.type in FILLS_SLOT}


def check_box(manager: Any, task: Any, kind: BoxKind, in_slot: Optional[bool] = None) -> Optional[str]:
    """The job's box exists, is a `kind`, is in a slot or not as the job
    needs, and no other job holds it."""
    if not task.box_id:
        return f"{task.type.value} needs a box_id"
    box = manager.twin.find_box(task.box_id)
    if box is None:
        return f"Box '{task.box_id}' does not exist"
    task.box_id = box.id
    if box.kind is not kind:
        return f"{box.name} is a {box.kind.value}, not a {kind.value}"
    if box.status in (BoxStatus.SHIPPED, BoxStatus.FAILED, BoxStatus.CARRIED):
        return f"{box.name} is {box.status.value}"
    if in_slot is True and not box.slot:
        return f"{box.name} is not in a slot"
    if in_slot is False and box.slot and box.kind is BoxKind.PALLET:
        return f"{box.name} is already in slot {box.slot}"
    conflict = manager.active_task_for_box(box.id, exclude=task.id)
    if conflict is not None:
        return f"{box.name} is already reserved by {conflict.id}"
    return None


def check_slot(manager: Any, task: Any, kind: BoxKind) -> Optional[str]:
    """An explicit `slot` exists, takes a `kind` and is free and unpromised."""
    slot_id = task.params.get("slot")
    if not slot_id:
        return None
    twin = manager.twin
    slot = twin.warehouse.slot(slot_id)
    if slot is None:
        return f"Unknown slot {slot_id!r}"
    if slot.kind != kind.value:
        return f"Slot {slot_id} holds {slot.kind}s, not {kind.value}s"
    holder = twin.stock.box_in(slot_id)
    if holder is not None and holder != task.box_id:
        return f"Slot {slot_id} already holds {holder}"
    if slot_id in promised_slots(twin, exclude=task.id):
        return f"Slot {slot_id} is promised to another job"
    return None


def check_zone(manager: Any, zone: Optional[str]) -> Optional[str]:
    if manager.twin.warehouse.resolve_zone(zone) is None:
        return f"Unknown zone {zone!r}"
    return None


def floor_target(planner: Any, task: Any, origin: Cell, profile: Any, layer: str) -> Tuple[Cell, str]:
    """A job that starts by picking its box off the floor: the box's cell."""
    box = task_box(planner.twin, task)
    return box.position, box.name


def slot_target(planner: Any, task: Any, origin: Cell, profile: Any, layer: str) -> Tuple[Cell, str]:
    """A job that starts at its box's slot: the face cell it serves it from."""
    box = task_box(planner.twin, task)
    slot = task_slot(planner.twin, task, box)
    if slot is None:
        raise PlanningError(f"{box.name} is not in a slot")
    return face_cell(planner, slot, origin, profile, layer), f"slot {slot.slot_id}"


def drop_cell(planner: Any, zone: str, origin: Cell, blocked: Set[Cell], route: Dict[str, Any]) -> Tuple[Cell, str]:
    """A free cell of `zone` to put a box down on."""
    return planner.resolve_target(zone, origin, blocked, prefer_free=True, **route)


def lift_steps(slot: Any, face: Cell, verb: ActionType, box: Any) -> List[Any]:
    """Lift to `slot`'s level at `face`, grasp or place the box, lower."""
    level = slot.level
    act = "Grasp" if verb is ActionType.GRASP else "Place"
    return [
        Action(ActionType.LIFT_TO, f"Lift to level {level} of {slot.slot_id}", face, slot.slot_id, box.id,
               level=level, slot_id=slot.slot_id),
        Action(verb, f"{act} {box.name} at {slot.slot_id}", face, slot.slot_id, box.id,
               level=level, slot_id=slot.slot_id),
        Action(ActionType.LOWER, "Lower", face, slot.slot_id, box.id),
    ]


# --------------------------------------------------------------------------- #
# Pallets (spec §9.2): unload, put away, retrieve, load
# --------------------------------------------------------------------------- #
def _check_unload(manager: Any, task: Any) -> Optional[str]:
    task.destination = task.destination or "intake_staging"
    return check_box(manager, task, BoxKind.PALLET, in_slot=False) or check_zone(manager, task.destination)


def _plan_floor_to_zone(planner: Any, task: Any, robot: Any, blocked: Set[Cell],
                        ship: bool = False) -> Tuple[List[Any], List[Cell], int]:
    """NAVIGATE to the box → PICK → NAVIGATE to a free cell of the destination → DELIVER."""
    route = {"profile": robot.mobility, "layer": robot.layer}
    box = task_box(planner.twin, task)
    pick = box.position
    drop, label = drop_cell(planner, task.destination, pick, blocked, route)
    actions = [
        Action(ActionType.NAVIGATE, f"Navigate to {box.name}", pick, box.name, box.id),
        Action(ActionType.PICK, f"Pick {box.name}", pick, box.name, box.id),
        Action(ActionType.NAVIGATE, f"Carry {box.name} to {label}", drop, label, box.id),
        Action(ActionType.DELIVER, f"Deliver {box.name} at {label}", drop, label, box.id,
               params={"ship": True} if ship else {}),
    ]
    return actions, [pick, drop], 2


def _check_putaway(manager: Any, task: Any) -> Optional[str]:
    return check_box(manager, task, BoxKind.PALLET, in_slot=False) or check_slot(manager, task, BoxKind.PALLET)


def choose_slot(planner: Any, task: Any, robot: Any, kind: BoxKind, near: Cell) -> Any:
    """The job's slot: its `slot` parameter, else the nearest free slot of
    `kind` within the robot's reach — recorded on the task, so no other job
    is promised it meanwhile."""
    twin = planner.twin
    slot = task_slot(twin, task)
    if slot is None:
        free = goods.free_slots(twin, kind.value, max_level=robot.mobility.max_shelf_level, near=near,
                                exclude=promised_slots(twin, exclude=task.id))
        if not free:
            raise PlanningError(f"No free {kind.value} slot within {robot.name}'s reach")
        slot = free[0]
        task.params["slot"] = slot.slot_id
    return slot


def _plan_putaway(planner: Any, task: Any, robot: Any, blocked: Set[Cell]) -> Tuple[List[Any], List[Cell], int]:
    """NAVIGATE staging → PICK → NAVIGATE face → LIFT_TO(level) → PLACE → LOWER."""
    route = {"profile": robot.mobility, "layer": robot.layer}
    box = task_box(planner.twin, task)
    slot = choose_slot(planner, task, robot, BoxKind.PALLET, box.position)
    face = face_cell(planner, slot, box.position, blocked=blocked, **route)
    actions = [
        Action(ActionType.NAVIGATE, f"Navigate to {box.name}", box.position, box.name, box.id),
        Action(ActionType.PICK, f"Pick {box.name}", box.position, box.name, box.id),
        Action(ActionType.NAVIGATE, f"Carry {box.name} to slot {slot.slot_id}", face, slot.slot_id, box.id),
    ] + lift_steps(slot, face, ActionType.PLACE, box)
    return actions, [box.position, face], 2


def _check_retrieve(manager: Any, task: Any) -> Optional[str]:
    task.destination = task.destination or "outbound_staging"
    return check_box(manager, task, BoxKind.PALLET, in_slot=True) or check_zone(manager, task.destination)


def _plan_retrieve(planner: Any, task: Any, robot: Any, blocked: Set[Cell]) -> Tuple[List[Any], List[Cell], int]:
    """NAVIGATE face → LIFT_TO → GRASP → LOWER → NAVIGATE outbound_staging → DELIVER."""
    route = {"profile": robot.mobility, "layer": robot.layer}
    box = task_box(planner.twin, task)
    slot = task_slot(planner.twin, task, box)
    face = face_cell(planner, slot, robot.position, blocked=blocked, **route)
    drop, label = drop_cell(planner, task.destination, face, blocked, route)
    actions = [Action(ActionType.NAVIGATE, f"Navigate to slot {slot.slot_id}", face, slot.slot_id, box.id)]
    actions += lift_steps(slot, face, ActionType.GRASP, box)
    actions += [
        Action(ActionType.NAVIGATE, f"Carry {box.name} to {label}", drop, label, box.id),
        Action(ActionType.DELIVER, f"Deliver {box.name} at {label}", drop, label, box.id),
    ]
    return actions, [face, drop], 2


def _check_load(manager: Any, task: Any) -> Optional[str]:
    task.destination = task.params.get("dock") or task.destination or "dock_3"
    return check_box(manager, task, BoxKind.PALLET, in_slot=False) or check_zone(manager, task.destination)


def _plan_load(planner: Any, task: Any, robot: Any, blocked: Set[Cell]) -> Tuple[List[Any], List[Cell], int]:
    """NAVIGATE outbound_staging → PICK → NAVIGATE dock_3 → DELIVER (the pallet ships)."""
    return _plan_floor_to_zone(planner, task, robot, blocked, ship=True)


#: Job types whose plan fills a slot (promised_slots reads these).
FILLS_SLOT = {TaskType.PUTAWAY_PALLET}

JOB_SPECS[TaskType.UNLOAD_TRUCK] = JobSpec(
    label="Unload truck", guide="box_id(a pallet on an inbound dock)[+destination, default intake_staging]",
    classes=frozenset({"FORKLIFT", "HEAVY_HAULER"}), check=_check_unload,
    plan=_plan_floor_to_zone, target=floor_target, carries_box=True)
JOB_SPECS[TaskType.PUTAWAY_PALLET] = JobSpec(
    label="Put away pallet", guide="box_id(a staged pallet)[+slot, default the nearest free one in reach]",
    classes=frozenset({"FORKLIFT"}), check=_check_putaway, plan=_plan_putaway, target=floor_target,
    carries_box=True)
JOB_SPECS[TaskType.RETRIEVE_PALLET] = JobSpec(
    label="Retrieve pallet", guide="box_id(a pallet in a rack slot)[+destination, default outbound_staging]",
    classes=frozenset({"FORKLIFT"}), check=_check_retrieve, plan=_plan_retrieve, target=slot_target,
    carries_box=True)
JOB_SPECS[TaskType.LOAD_TRUCK] = JobSpec(
    label="Load truck", guide="box_id(a pallet at outbound staging)[+dock, default dock_3](it ships)",
    classes=frozenset({"FORKLIFT"}), check=_check_load, plan=_plan_load, target=floor_target,
    carries_box=True)

```

- [ ] **Step 6: Run the new tests**

Run: `.venv/bin/python -m pytest -o addopts="" -q backend/test_pallet_jobs.py`
Expected: `8 passed`.

- [ ] **Step 7: Run the full suite**

Run: `.venv/bin/python -m pytest -o addopts="" -q`
Expected: `2 failed, 558 passed`.

- [ ] **Step 8: Commit**

```bash
git add backend/models.py backend/simulator.py backend/jobs.py backend/test_pallet_jobs.py
git commit -m "feat: pallet jobs — unload, put away, retrieve, load

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Tote jobs — to a station and back, and returns into storage

Three more §9.2 job types: `TOTE_TO_STATION` (an AMR up to level 1 or the humanoid up to level 2: NAVIGATE face → LIFT_TO → GRASP → LOWER → NAVIGATE the station's tote drop → DELIVER), `RETURN_TOTE` (the reverse, to the tote's own slot by default) and `RETURNS_PUTAWAY` (the humanoid: NAVIGATE returns_qc → GRASP tote → NAVIGATE face → PLACE, into the nearest free tote slot in reach unless one is named). A tote keeps its ledger slot while it is away at a station, so its recorded and true quantities — and any variance between them — travel with it and come home with it; a returned tote is new to storage and is recorded where it is put. The humanoid obeys `SUPERVISOR_ABSENT` throughout (Task 5); its supervisor walking alongside is the shift engine's job (Task 12), so the test moves Jordan by hand.

**Files:**
- Modify: `backend/models.py` (`TaskType.TOTE_TO_STATION`, `RETURN_TOTE`, `RETURNS_PUTAWAY`; the humanoid preset)
- Modify: `backend/jobs.py` (`FILLS_SLOT` gains the tote jobs; the three specs)
- Test: `backend/test_tote_jobs.py` (create)

**Interfaces:**
- Consumes (Tasks 2, 7, 8): `goods.store` (via PLACE); `JobSpec`, `check_box`, `check_slot`, `choose_slot`, `lift_steps`, `floor_target`, `slot_target`, `face_cell`, `task_slot`; the pick stations' `tote_drop` attribute.
- Produces:
  - `TaskType.TOTE_TO_STATION`, `RETURN_TOTE`, `RETURNS_PUTAWAY` (AMR is unrestricted; HUMANOID's preset allows all three).
  - Payloads: `TOTE_TO_STATION {box_id[, station="pick_station_1"]}` (the station is also the task's destination), `RETURN_TOTE {box_id[, slot=its own]}`, `RETURNS_PUTAWAY {box_id[, slot]}`.
  - `backend.jobs.tote_home(twin, box) -> Optional[Slot]`, `tote_is_home(twin, box) -> bool`.

- [ ] **Step 1: Write the failing tests**

**Create** `backend/test_tote_jobs.py`:

```python
"""Tote jobs (multi-embodiment spec §9.2): a tote from its shelf slot to a
pick station and back again, and a returned tote into a free shelf slot."""
import pytest

from backend import people
from backend.digital_twin import DigitalTwin
from backend.models import BoxStatus, SimulationStatus, TaskStatus
from backend.simulator import Simulator


@pytest.fixture
def twin(tmp_path):
    return DigitalTwin(log_dir=str(tmp_path / "logs"), data_dir=str(tmp_path / "data"),
                       persist_logs=False, demo=True, demo_tasks=False, layout="distribution_center")


@pytest.fixture
def sim(twin):
    simulator = Simulator(twin)
    twin.simulation_status = SimulationStatus.RUNNING
    return simulator


@pytest.fixture
def amr(twin):
    return twin.add_robot(name="TR50-201", asset_id="AST-000201", position=(8, 13))


def run(sim, task, max_ticks=2000, each_tick=None):
    for _ in range(max_ticks):
        if task.is_terminal:
            return
        sim.tick()
        if each_tick:
            each_tick()
    assert task.is_terminal, f"{task.id} still {task.status.value}"


def steps(twin, task):
    return [e["data"]["step"] for e in twin.events.query(task_id=task.id, event_type="ROBOT_STEP")]


def tote(twin, name="TOTE-1", slot="TS-10-12-1", quantity=12):
    return twin.add_box(name=name, kind="TOTE", sku="SKU-02", quantity=quantity, weight=9.0, slot=slot)


def test_a_tote_goes_to_the_pick_station_and_comes_back(twin, sim, amr):
    box = tote(twin)
    twin.stock.adjust_true("TS-10-12-1", -1)                  # one unit has gone missing
    out = twin.tasks.create_task({"type": "TOTE_TO_STATION", "box_id": box.id})
    assert out.status is TaskStatus.PLANNING and out.params["station"] == "pick_station_1"
    run(sim, out)
    assert out.status is TaskStatus.COMPLETED, out.error
    assert (box.position, box.status, box.slot) == ((19, 14), BoxStatus.DELIVERED, "TS-10-12-1")
    assert twin.stock.location("TS-10-12-1").box_id == box.id   # its slot waits for it
    assert steps(twin, out) == ["NAVIGATE", "LIFT_TO", "GRASP", "LOWER", "NAVIGATE", "DELIVER"]
    back = twin.tasks.create_task({"type": "RETURN_TOTE", "box_id": box.id})
    assert back.params["slot"] == "TS-10-12-1"
    run(sim, back)
    assert back.status is TaskStatus.COMPLETED, back.error
    assert (box.position, box.status) == ((10, 12), BoxStatus.STORED)
    location = twin.stock.location("TS-10-12-1")
    assert (location.recorded_qty, location.true_qty) == (12, 11)   # the variance came home with it
    # It still stands on the tote drop, so its first NAVIGATE is over before it starts.
    assert steps(twin, back) == ["PICK", "NAVIGATE", "LIFT_TO", "PLACE", "LOWER"]


def test_a_tote_out_of_an_amrs_reach_goes_to_the_humanoid(twin, sim, amr):
    humanoid = twin.add_robot(name="H1-212", asset_id="AST-000212", position=(16, 13))
    jordan = twin.add_operator(name="Jordan", worker_id="E-10006")
    people.place(twin, jordan, "tote_aisle_1")
    high = tote(twin, slot="TS-14-12-2")
    named = twin.tasks.create_task({"type": "TOTE_TO_STATION", "box_id": high.id, "robot_id": amr.id})
    assert named.error == "TR50-201 can't work there: level 2 is out of its reach (highest level 1)"
    auto = twin.tasks.create_task({"type": "TOTE_TO_STATION", "box_id": high.id, "station": "pick_station_2"})
    scored, _ = twin.tasks._score_candidates(auto)
    assert [entry[1] for entry in scored] == [humanoid]


def test_a_returned_tote_is_put_away_by_the_supervised_humanoid(twin, sim):
    humanoid = twin.add_robot(name="H1-212", asset_id="AST-000212", position=(22, 7))
    jordan = twin.add_operator(name="Jordan", worker_id="E-10006")
    people.place(twin, jordan, "returns_qc")
    returned = twin.add_box(name="RET-1", kind="TOTE", sku="SKU-05", quantity=8, weight=7.0, position=(21, 7))

    def follow():  # Jordan walks alongside (the shift engine does this in Task 12)
        zone = twin.warehouse.zone_of_cell(humanoid.position)
        if zone is not None and jordan.zone != zone.key:
            people.place(twin, jordan, zone.key)

    task = twin.tasks.create_task({"type": "RETURNS_PUTAWAY", "box_id": returned.id})
    assert task.status is TaskStatus.PLANNING, task.error
    run(sim, task, each_tick=follow)
    assert task.status is TaskStatus.COMPLETED, task.error
    assert task.params["slot"] == returned.slot and twin.warehouse.slot(returned.slot).level <= 2
    assert twin.stock.location(returned.slot).recorded_qty == 8
    assert steps(twin, task) == ["NAVIGATE", "GRASP", "NAVIGATE", "PLACE"]
    assert all(e["data"]["supervision_ok"] for e in twin.events.query(task_id=task.id, event_type="ROBOT_STEP"))


def test_tote_jobs_check_their_requests(twin, amr):
    box = tote(twin)
    box.position = (19, 16)                                     # away at Pick 2
    away = twin.tasks.create_task({"type": "TOTE_TO_STATION", "box_id": box.id})
    assert away.error == "TOTE-1 is not in its slot TS-10-12-1"
    home = tote(twin, "TOTE-2", slot="TS-11-12-1")
    nowhere = twin.tasks.create_task({"type": "TOTE_TO_STATION", "box_id": home.id, "station": "workshop"})
    assert nowhere.error == "workshop is not a pick station"
    already = twin.tasks.create_task({"type": "RETURN_TOTE", "box_id": home.id})
    assert already.error == "TOTE-2 is already in its slot TS-11-12-1"
    shelved = twin.tasks.create_task({"type": "RETURNS_PUTAWAY", "box_id": home.id})
    assert shelved.error == "TOTE-2 already belongs in slot TS-11-12-1"
    returned = twin.add_box(name="RET-1", kind="TOTE", sku="SKU-05", quantity=8, weight=7.0, position=(21, 7))
    by_amr = twin.tasks.create_task({"type": "RETURNS_PUTAWAY", "box_id": returned.id, "robot_id": amr.id})
    assert by_amr.error == "TR50-201 is an AMR; RETURNS_PUTAWAY needs a HUMANOID"
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/bin/python -m pytest -o addopts="" -q backend/test_tote_jobs.py`
Expected: `4 failed` — `ValueError: Unknown task type 'TOTE_TO_STATION'` (and `'RETURNS_PUTAWAY' is not a valid TaskType`).

- [ ] **Step 3: The job types, and the humanoid may run them**

**Replace in** `backend/models.py`:

```python
            "PICK_AND_DELIVER", "PICK_BOX", "DELIVER_BOX", "MOVE_BOX",
            "MOVE_ROBOT", "CHARGE_ROBOT",
        ],
```

with:

```python
            "PICK_AND_DELIVER", "PICK_BOX", "DELIVER_BOX", "MOVE_BOX",
            "MOVE_ROBOT", "CHARGE_ROBOT", "TOTE_TO_STATION", "RETURN_TOTE", "RETURNS_PUTAWAY",
        ],
```

**Replace in** `backend/models.py`:

```python
    PUTAWAY_PALLET = "PUTAWAY_PALLET"
    RETRIEVE_PALLET = "RETRIEVE_PALLET"
    LOAD_TRUCK = "LOAD_TRUCK"


class Priority(str, enum.Enum):
```

with:

```python
    PUTAWAY_PALLET = "PUTAWAY_PALLET"
    RETRIEVE_PALLET = "RETRIEVE_PALLET"
    LOAD_TRUCK = "LOAD_TRUCK"
    # Totes: from their shelf slot to a pick station's tote drop and back,
    # and a returned tote from returns_qc into a free shelf slot.
    TOTE_TO_STATION = "TOTE_TO_STATION"
    RETURN_TOTE = "RETURN_TOTE"
    RETURNS_PUTAWAY = "RETURNS_PUTAWAY"


class Priority(str, enum.Enum):
```

- [ ] **Step 4: The tote job specs**

**Replace in** `backend/jobs.py`:

```python

#: Job types whose plan fills a slot (promised_slots reads these).
FILLS_SLOT = {TaskType.PUTAWAY_PALLET}

JOB_SPECS[TaskType.UNLOAD_TRUCK] = JobSpec(
```

with:

```python

#: Job types whose plan fills a slot (promised_slots reads these).
FILLS_SLOT = {TaskType.PUTAWAY_PALLET, TaskType.RETURN_TOTE, TaskType.RETURNS_PUTAWAY}

JOB_SPECS[TaskType.UNLOAD_TRUCK] = JobSpec(
```

**Replace in** `backend/jobs.py`:

```python
    classes=frozenset({"FORKLIFT"}), check=_check_load, plan=_plan_load, target=floor_target,
    carries_box=True)

```

with:

```python
    classes=frozenset({"FORKLIFT"}), check=_check_load, plan=_plan_load, target=floor_target,
    carries_box=True)


# --------------------------------------------------------------------------- #
# Totes (spec §9.2): to a pick station and back; returns into storage
# --------------------------------------------------------------------------- #
def tote_home(twin: Any, box: Any) -> Optional[Any]:
    """The slot a tote belongs in (it keeps it while away at a station)."""
    return twin.warehouse.slot(box.slot) if box.slot else None


def tote_is_home(twin: Any, box: Any) -> bool:
    home = tote_home(twin, box)
    return home is not None and box.position == home.cell and box.status in (BoxStatus.STORED, BoxStatus.RESERVED)


def _check_tote_to_station(manager: Any, task: Any) -> Optional[str]:
    task.params["station"] = task.params.get("station") or task.destination or "pick_station_1"
    reason = check_box(manager, task, BoxKind.TOTE, in_slot=True)
    if reason:
        return reason
    box = manager.twin.find_box(task.box_id)
    if not tote_is_home(manager.twin, box):
        return f"{box.name} is not in its slot {box.slot}"
    station = manager.twin.warehouse.resolve_zone(task.params["station"])
    if station is None or "tote_drop" not in station.attributes:
        return f"{task.params['station']} is not a pick station"
    task.params["station"] = task.destination = station.key
    return None


def _plan_tote_to_station(planner: Any, task: Any, robot: Any, blocked: Set[Cell]) -> Tuple[List[Any], List[Cell], int]:
    """NAVIGATE face → LIFT_TO → GRASP → LOWER → NAVIGATE station tote drop → DELIVER."""
    route = {"profile": robot.mobility, "layer": robot.layer}
    twin = planner.twin
    box = task_box(twin, task)
    slot = tote_home(twin, box)
    face = face_cell(planner, slot, robot.position, blocked=blocked, **route)
    station = twin.warehouse.zones[task.params["station"]]
    drop = tuple(station.attributes["tote_drop"])
    actions = [Action(ActionType.NAVIGATE, f"Navigate to slot {slot.slot_id}", face, slot.slot_id, box.id)]
    actions += lift_steps(slot, face, ActionType.GRASP, box)
    actions += [
        Action(ActionType.NAVIGATE, f"Carry {box.name} to {station.label}", drop, station.label, box.id),
        Action(ActionType.DELIVER, f"Deliver {box.name} at {station.label}", drop, station.label, box.id),
    ]
    return actions, [face, drop], 2


def _check_return_tote(manager: Any, task: Any) -> Optional[str]:
    reason = check_box(manager, task, BoxKind.TOTE)
    if reason:
        return reason
    box = manager.twin.find_box(task.box_id)
    task.params["slot"] = task.params.get("slot") or box.slot
    if not task.params["slot"]:
        return f"{box.name} has no slot to return to"
    if tote_is_home(manager.twin, box) and task.params["slot"] == box.slot:
        return f"{box.name} is already in its slot {box.slot}"
    return check_slot(manager, task, BoxKind.TOTE)


def _plan_back_to_slot(planner: Any, task: Any, robot: Any, blocked: Set[Cell]) -> Tuple[List[Any], List[Cell], int]:
    """NAVIGATE tote → PICK → NAVIGATE face → LIFT_TO → PLACE → LOWER (the reverse trip)."""
    route = {"profile": robot.mobility, "layer": robot.layer}
    box = task_box(planner.twin, task)
    slot = task_slot(planner.twin, task)
    face = face_cell(planner, slot, box.position, blocked=blocked, **route)
    actions = [
        Action(ActionType.NAVIGATE, f"Navigate to {box.name}", box.position, box.name, box.id),
        Action(ActionType.PICK, f"Pick {box.name}", box.position, box.name, box.id),
        Action(ActionType.NAVIGATE, f"Carry {box.name} to slot {slot.slot_id}", face, slot.slot_id, box.id),
    ] + lift_steps(slot, face, ActionType.PLACE, box)
    return actions, [box.position, face], 2


def _check_returns_putaway(manager: Any, task: Any) -> Optional[str]:
    reason = check_box(manager, task, BoxKind.TOTE)
    if reason:
        return reason
    box = manager.twin.find_box(task.box_id)
    if box.slot:
        return f"{box.name} already belongs in slot {box.slot}"
    return check_slot(manager, task, BoxKind.TOTE)


def _plan_returns_putaway(planner: Any, task: Any, robot: Any,
                          blocked: Set[Cell]) -> Tuple[List[Any], List[Cell], int]:
    """NAVIGATE returns_qc → GRASP tote → NAVIGATE face → PLACE."""
    route = {"profile": robot.mobility, "layer": robot.layer}
    box = task_box(planner.twin, task)
    slot = choose_slot(planner, task, robot, BoxKind.TOTE, box.position)
    face = face_cell(planner, slot, box.position, blocked=blocked, **route)
    actions = [
        Action(ActionType.NAVIGATE, f"Navigate to {box.name}", box.position, box.name, box.id),
        Action(ActionType.GRASP, f"Grasp {box.name}", box.position, box.name, box.id),
        Action(ActionType.NAVIGATE, f"Carry {box.name} to slot {slot.slot_id}", face, slot.slot_id, box.id),
        Action(ActionType.PLACE, f"Place {box.name} in {slot.slot_id}", face, slot.slot_id, box.id,
               level=slot.level, slot_id=slot.slot_id),
    ]
    return actions, [box.position, face], 2


JOB_SPECS[TaskType.TOTE_TO_STATION] = JobSpec(
    label="Tote to station", guide="box_id(a tote in its slot)[+station, default pick_station_1]",
    classes=frozenset({"AMR", "HUMANOID"}), check=_check_tote_to_station, plan=_plan_tote_to_station,
    target=slot_target, carries_box=True)
JOB_SPECS[TaskType.RETURN_TOTE] = JobSpec(
    label="Return tote", guide="box_id(a tote away at a station)[+slot, default its own]",
    classes=frozenset({"AMR", "HUMANOID"}), check=_check_return_tote, plan=_plan_back_to_slot,
    target=floor_target, carries_box=True)
JOB_SPECS[TaskType.RETURNS_PUTAWAY] = JobSpec(
    label="Returns put-away", guide="box_id(a returned tote in returns_qc)[+slot, default the nearest free one]",
    classes=frozenset({"HUMANOID"}), check=_check_returns_putaway, plan=_plan_returns_putaway,
    target=floor_target, carries_box=True)

```

- [ ] **Step 5: Run the new tests**

Run: `.venv/bin/python -m pytest -o addopts="" -q backend/test_tote_jobs.py`
Expected: `4 passed`.

- [ ] **Step 6: Run the full suite**

Run: `.venv/bin/python -m pytest -o addopts="" -q`
Expected: `2 failed, 562 passed`.

- [ ] **Step 7: Commit**

```bash
git add backend/models.py backend/jobs.py backend/test_tote_jobs.py
git commit -m "feat: tote jobs — to a station and back, and returns put-away

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: Station jobs and the jobs people do

`PICK_ITEMS` (the picker at Pick 1's work cell: GRASP a unit × n → PLACE_ON_CONVEYOR × n, waiting for the infeed to clear before each) creates one ITEM per unit, tagged with its order and held on its pack cell's working cell; `PACK_ORDER` (the arm of its pack cell: GRASP each order item from the working cell → PLACE it into the carton, then PLACE_ON_CONVEYOR the carton) makes one CARTON weighing the items plus 0.3 kg, which rides on to the sorter and its lane dock (Task 3). Two jobs are done by people, run by `backend/human_jobs.py`: `MANUAL_PICK` (walk to Pick 2, pick n items onto the conveyor at `MANUAL_PICK_S_PER_ITEM`) and `CLEAR_JAM` (walk to the jammed segment's zone, fix it in `CLEAR_JAM_S` = 30 s, walk back). A jam asks for its `CLEAR_JAM` itself, every `SHIFT_CHECK_EVERY_TICKS`, until someone qualified takes it (§11.3); AUTO picks the nearest qualified person on the floor. `CERTIFICATION_REQUIREMENTS` gains `CLEAR_JAM: robot_cell_access`, needed only for a jam on an arm's working cell, and on the new floor every certification check also compares the credential's scope with the equipment model involved and the site (§9.2) — so Riley (revoked) is refused and the job goes to Mateo, whose credential covers FB-CX10.

**Files:**
- Create: `backend/human_jobs.py`
- Modify: `backend/models.py` (`TaskType.PICK_ITEMS`, `PACK_ORDER`, `MANUAL_PICK`, `CLEAR_JAM`; `CERTIFICATION_REQUIREMENTS["CLEAR_JAM"]`; the picker and arm presets; `CLEAR_JAM_S`, `MANUAL_PICK_S_PER_ITEM`)
- Modify: `backend/jobs.py` (the four specs)
- Modify: `backend/task_manager.py` (people's jobs: choosing, scope rule, starting, releasing; no reservation of a tote a picker only takes units from)
- Modify: `backend/simulator.py` (human jobs tick; jams ask for clearing)
- Test: `backend/test_station_jobs.py` (create)

**Interfaces:**
- Consumes (Tasks 2–5, 7): `goods.take_units`; `Equipment.place`, `.clear_jam`, `.arm_cells`, `.conveyor.jams`, `.sorter.lanes`; the `GRASP`/`PLACE`/`PLACE_ON_CONVEYOR`/`WAIT_CLEAR` step arguments; `cert_scope_ok`, `people.FLOOR_SITE`, `people.start_transit`; `JobSpec(human=True)`, `check_box`, `parse_cell`; the pick stations' `picker`, `work_cell`, `tote_drop`, `conveyor_infeed` and pack cells' `arm_cell`, `conveyor_cell` attributes.
- Produces:
  - `TaskType.PICK_ITEMS`, `PACK_ORDER`, `MANUAL_PICK`, `CLEAR_JAM`; PICKER allows `PICK_ITEMS`, ARM allows `PACK_ORDER`; `CERTIFICATION_REQUIREMENTS["CLEAR_JAM"] = "robot_cell_access"`; `CONFIG["CLEAR_JAM_S"] = 30.0`, `CONFIG["MANUAL_PICK_S_PER_ITEM"] = 5.0`.
  - Payloads: `PICK_ITEMS {box_id (a tote on Pick 1's tote drop), quantity[, order_id, pack_cell, station="pick_station_1"]}`, `PACK_ORDER {order_id, quantity[, pack_cell="pack_cell_1", lane="dock_4"]}` (the lane is the task's destination), `MANUAL_PICK {box_id (a tote on Pick 2's tote drop), quantity[, order_id, pack_cell, operator_id]}`, `CLEAR_JAM {segment "x,y"[, operator_id]}`.
  - `backend.human_jobs`: `HUMAN_JOBS`, `ARM_MODEL = "FB-CX10"`, `jam_cell(task)`, `in_pack_cell(twin, cell)`, `jam_zone(twin, cell)`, `equipment_model(twin, task)`, `target_zone(twin, task)`, `start(twin, task, operator)`, `tick(twin)`, `ensure_jam_jobs(twin) -> List[Task]`. A running human job is `IN_PROGRESS` with no robot; `task.params` holds `phase` (`WALK`, `WORK`, `RETURN`, `DONE`), `until_tick`, `origin_zone`, `picked`. The operator is `ON_TASK` with `current_task` set until it ends.
  - `TaskManager._operator_reason(operator, task)`, `_scope_model(task)`, `_choose_operator(task)`, `_release_operator(task, completed)`, `_run_human(task)`.

- [ ] **Step 1: Write the failing tests**

**Create** `backend/test_station_jobs.py`:

```python
"""Station jobs and the jobs people do (multi-embodiment spec §8, §9.2,
§11.3): units picked onto the line, an order packed into a carton and sorted
to its dock, a person picking at Pick 2, and a conveyor jam cleared by
someone whose credential covers the arm beside it."""
import pytest

from backend import people
from backend.digital_twin import DigitalTwin
from backend.models import BoxKind, BoxStatus, OperatorStatus, SimulationStatus, TaskStatus, TaskType
from backend.simulator import Simulator


@pytest.fixture
def twin(tmp_path):
    return DigitalTwin(log_dir=str(tmp_path / "logs"), data_dir=str(tmp_path / "data"),
                       persist_logs=False, demo=True, demo_tasks=False, layout="distribution_center")


@pytest.fixture
def sim(twin):
    simulator = Simulator(twin)
    twin.simulation_status = SimulationStatus.RUNNING
    return simulator


def ticks(sim, n):
    for _ in range(n):
        sim.tick()


def run(sim, *tasks, max_ticks=3000):
    for _ in range(max_ticks):
        if all(task.is_terminal for task in tasks):
            return
        sim.tick()
    assert all(task.is_terminal for task in tasks), [(t.id, t.status.value) for t in tasks]


def tote_at(twin, cell, quantity=6, name="TOTE-1", slot="TS-10-12-1"):
    """A tote that has been brought to a station's tote drop."""
    tote = twin.add_box(name=name, kind="TOTE", sku="SKU-02", quantity=quantity, weight=7.5, slot=slot)
    tote.position = cell
    tote.set_status(BoxStatus.DELIVERED)
    return tote


def test_an_order_is_picked_packed_and_sorted_to_its_dock(twin, sim):
    picker = twin.add_robot(name="PK30-203", asset_id="AST-000203", position=(20, 14))
    arm = twin.add_robot(name="CX10-210", asset_id="AST-000210")
    tote = tote_at(twin, (19, 14))
    pick = twin.tasks.create_task({"type": "PICK_ITEMS", "box_id": tote.id, "quantity": 2, "order_id": "ORD-1",
                                   "pack_cell": "pack_cell_1"})
    pack = twin.tasks.create_task({"type": "PACK_ORDER", "order_id": "ORD-1", "quantity": 2, "lane": "dock_5"})
    assert pick.status is TaskStatus.PLANNING and pack.status is TaskStatus.PLANNING, (pick.error, pack.error)
    run(sim, pick, pack)
    assert pick.status is TaskStatus.COMPLETED and pack.status is TaskStatus.COMPLETED, (pick.error, pack.error)
    assert (pick.robot_id, pack.robot_id) == (picker.id, arm.id)
    assert tote.quantity == 4 and tote.status is BoxStatus.DELIVERED          # still waiting on the tote drop
    carton = next(box for box in twin.boxes.values() if box.kind is BoxKind.CARTON)
    assert carton.quantity == 2 and not [b for b in twin.boxes.values() if b.kind is BoxKind.ITEM]
    ticks(sim, 80)
    assert twin.warehouse.zone_of_cell(carton.position).key == "dock_5" and carton.status is BoxStatus.DELIVERED
    holders = [(e["data"]["from"], e["data"]["to"]) for e in twin.events.query(event_type="HANDOFF")]
    assert holders.count((picker.id, "conveyor")) == 2 and holders.count(("conveyor", arm.id)) == 2
    assert holders[-2:] == [("conveyor", "sorter"), ("sorter", "dock_5")]


def test_station_jobs_check_their_requests(twin):
    twin.add_robot(name="PK30-203", asset_id="AST-000203", position=(20, 14))
    twin.add_robot(name="CX10-210", asset_id="AST-000210")
    other_arm = twin.add_robot(name="CX10-211", asset_id="AST-000211")
    tote = tote_at(twin, (19, 14), quantity=3)
    errors = [
        ({"type": "PICK_ITEMS", "box_id": tote.id, "quantity": 5}, "TOTE-1 holds only 3 of SKU-02"),
        ({"type": "PICK_ITEMS", "box_id": tote.id, "quantity": 0}, "quantity must be a whole number of at least 1"),
        ({"type": "PICK_ITEMS", "box_id": tote.id, "quantity": 1, "station": "pick_station_2"},
         "Pick 2 is not a robot pick station: use MANUAL_PICK"),
        ({"type": "PICK_ITEMS", "box_id": tote.id, "quantity": 1, "pack_cell": "pack_cell_9"},
         "Unknown pack cell 'pack_cell_9'"),
        ({"type": "PACK_ORDER", "quantity": 1}, "PACK_ORDER needs an order_id"),
        ({"type": "PACK_ORDER", "order_id": "ORD-1", "quantity": 1, "lane": "dock_3"},
         "dock_3 is not a sorter lane (lanes: dock_4, dock_5)"),
        ({"type": "PACK_ORDER", "order_id": "ORD-1", "quantity": 1, "robot_id": other_arm.id},
         "No path from CX10-211 to (23,14)"),
        ({"type": "MANUAL_PICK", "box_id": tote.id, "quantity": 1, "station": "pick_station_1"},
         "Pick 1 is not a human pick station: use PICK_ITEMS"),
        ({"type": "CLEAR_JAM", "segment": "21,15"}, "Conveyor cell (21,15) is not jammed"),
    ]
    for payload, error in errors:
        task = twin.tasks.create_task(payload)
        assert task.status is TaskStatus.FAILED and task.error == error, (payload, task.error)
    moved = tote_at(twin, (19, 16), name="TOTE-2", slot="TS-11-12-1")
    elsewhere = twin.tasks.create_task({"type": "PICK_ITEMS", "box_id": moved.id, "quantity": 1})
    assert elsewhere.error == "TOTE-2 is not on Pick 1's tote drop (19,14)"


def test_a_person_picks_at_pick_two(twin, sim):
    sam = twin.add_operator(name="Sam", worker_id="E-10001")
    lee = twin.add_operator(name="Lee", worker_id="E-10002")
    people.place(twin, sam, "workshop")
    people.place(twin, lee, "pick_station_2")
    tote = tote_at(twin, (19, 16))
    task = twin.tasks.create_task({"type": "MANUAL_PICK", "box_id": tote.id, "quantity": 2, "order_id": "ORD-4",
                                   "pack_cell": "pack_cell_2"})
    assert task.status is TaskStatus.IN_PROGRESS and task.operator_id == lee.id   # the nearer of the two
    assert lee.status is OperatorStatus.ON_TASK and task.robot_id is None
    run(sim, task)
    assert task.status is TaskStatus.COMPLETED, task.error
    assert lee.status is OperatorStatus.AVAILABLE and lee.zone == "pick_station_2"
    items = [b for b in twin.boxes.values() if b.kind is BoxKind.ITEM]
    assert len(items) == 2 and tote.quantity == 4
    placed = [e["data"] for e in twin.events.query(task_id=task.id, event_type="HANDOFF")]
    assert [(h["from"], h["to"]) for h in placed] == [(lee.id, "conveyor")] * 2
    assert all(item.order_id == "ORD-4" for item in items)


def test_a_jam_in_a_pack_cell_is_cleared_by_mateo_not_riley(twin, sim):
    twin.add_robot(name="CX10-210", asset_id="AST-000210")
    riley = twin.add_operator(name="Riley", worker_id="E-10008")
    mateo = twin.add_operator(name="Mateo", worker_id="E-10004")
    people.place(twin, riley, "pick_station_1")              # nearer, but her robot_cell_access is revoked
    people.place(twin, mateo, "workshop")
    twin.equipment.jam((23, 15))
    named = twin.tasks.create_task({"type": "CLEAR_JAM", "segment": "23,15", "operator_id": riley.id})
    assert "does not hold the required 'robot_cell_access'" in named.error
    ticks(sim, 20)                                           # the jam asks for someone
    task = next(t for t in twin.tasks.tasks.values() if t.type is TaskType.CLEAR_JAM and not t.is_terminal)
    assert task.operator_id == mateo.id and task.required_certification == "robot_cell_access"
    run(sim, task)
    assert task.status is TaskStatus.COMPLETED, task.error
    assert (23, 15) not in twin.equipment.conveyor.jams and twin.events.query(event_type="CONVEYOR_CLEARED")
    assert mateo.zone == "workshop" and mateo.status is OperatorStatus.AVAILABLE   # walked back out of the cell
    moved = [e["data"] for e in twin.events.query(event_type="PERSON_MOVED") if e["data"]["operator_id"] == mateo.id]
    assert [(m["from"], m["to"]) for m in moved] == [("workshop", "pack_cell_1"), ("pack_cell_1", "workshop")]


def test_a_jam_outside_the_pack_cells_needs_no_cell_access(twin, sim):
    noor = twin.add_operator(name="Noor", worker_id="E-10003")
    people.place(twin, noor, "workshop")
    twin.equipment.jam((20, 15))
    task = twin.tasks.create_task({"type": "CLEAR_JAM", "segment": "20,15"})
    assert task.status is TaskStatus.IN_PROGRESS and task.operator_id == noor.id
    assert task.required_certification is None
    run(sim, task)
    assert task.status is TaskStatus.COMPLETED and (20, 15) not in twin.equipment.conveyor.jams


def test_a_jam_no_one_can_clear_is_asked_for_again_until_someone_can(twin, sim):
    twin.add_robot(name="CX10-210", asset_id="AST-000210")
    riley = twin.add_operator(name="Riley", worker_id="E-10008")
    people.place(twin, riley, "pick_station_1")
    twin.equipment.jam((23, 15))
    ticks(sim, 45)
    asked = [t for t in twin.tasks.tasks.values() if t.type is TaskType.CLEAR_JAM]
    assert len(asked) == 2 and all(t.status is TaskStatus.FAILED for t in asked)
    mateo = twin.add_operator(name="Mateo", worker_id="E-10004")
    people.place(twin, mateo, "workshop")
    ticks(sim, 20)
    assert any(t.operator_id == mateo.id and not t.is_terminal
               for t in twin.tasks.tasks.values() if t.type is TaskType.CLEAR_JAM)


def test_a_credential_must_cover_the_arm_on_the_new_floor(twin):
    twin.add_robot(name="CX10-210", asset_id="AST-000210")
    mateo = twin.add_operator(name="Mateo", worker_id="E-10004")
    people.place(twin, mateo, "workshop")
    mateo.certification_scopes["robot_cell_access"] = {"equipment": ["FB-CX20"], "site": []}
    twin.equipment.jam((23, 15))
    task = twin.tasks.create_task({"type": "CLEAR_JAM", "segment": "23,15", "operator_id": mateo.id})
    assert task.error == ("Mateo has a 'robot_cell_access' credential that does not cover FB-CX10 "
                          "(covers: FB-CX20)")


def test_cancelling_a_persons_job_frees_them(twin, sim):
    noor = twin.add_operator(name="Noor", worker_id="E-10003")
    people.place(twin, noor, "workshop")
    twin.equipment.jam((20, 15))
    task = twin.tasks.create_task({"type": "CLEAR_JAM", "segment": "20,15"})
    ticks(sim, 5)
    twin.tasks.cancel_task(task.id)
    assert noor.status is OperatorStatus.AVAILABLE and noor.current_task is None
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/bin/python -m pytest -o addopts="" -q backend/test_station_jobs.py`
Expected: `8 failed` — `ValueError: Unknown task type 'PICK_ITEMS'` (and `'MANUAL_PICK'`, `'CLEAR_JAM'`).

- [ ] **Step 3: The job types, the certification and who may run them**

**Replace in** `backend/models.py`:

```python
    "SAFETY_WAIT_ESCALATE_S": 120,
    # Energy on the new floor (backend/energy.py): every Wh used and charged
```

with:

```python
    "SAFETY_WAIT_ESCALATE_S": 120,
    # Jobs people do (backend/human_jobs.py): clearing a conveyor jam, and
    # picking one item from a tote onto the line at Pick 2.
    "CLEAR_JAM_S": 30.0,
    "MANUAL_PICK_S_PER_ITEM": 5.0,
    # Energy on the new floor (backend/energy.py): every Wh used and charged
```

**Replace in** `backend/models.py`:

```python
    "OPERATOR_MAINTENANCE_SIGNOFF": "electrical_safety",
}
```

with:

```python
    "OPERATOR_MAINTENANCE_SIGNOFF": "electrical_safety",
    # Inside a fenced pack cell only; a jam elsewhere on the line needs none
    # (backend/human_jobs.py). Scoped to the arm's model on the new floor.
    "CLEAR_JAM": "robot_cell_access",
}
```

**Replace in** `backend/models.py`:

```python
        "allowed_task_types": [
            "PICK_AND_DELIVER", "PICK_BOX", "DELIVER_BOX", "MOVE_BOX", "CHARGE_ROBOT",
        ],
    },
    # Fixed pack-cell arm: it never drives, and being mains-powered it never
    # actually needs CHARGE_ROBOT — but the rule above still applies. Only a
    # floor with fixed stations (distribution_center) can hold one.
    "ARM": {
        "label": "Arm (fixed pack cell)",
        "speed": 0.7,
        "allowed_task_types": ["CHARGE_ROBOT"],
    },
```

with:

```python
        "allowed_task_types": [
            "PICK_AND_DELIVER", "PICK_BOX", "DELIVER_BOX", "MOVE_BOX", "CHARGE_ROBOT", "PICK_ITEMS",
        ],
    },
    # Fixed pack-cell arm: it never drives, it packs orders, and being
    # mains-powered it never actually needs CHARGE_ROBOT — but the rule above
    # still applies. Only a floor with fixed stations (distribution_center)
    # can hold one.
    "ARM": {
        "label": "Arm (fixed pack cell)",
        "speed": 0.7,
        "allowed_task_types": ["CHARGE_ROBOT", "PACK_ORDER"],
    },
```

**Replace in** `backend/models.py`:

```python
    TOTE_TO_STATION = "TOTE_TO_STATION"
    RETURN_TOTE = "RETURN_TOTE"
    RETURNS_PUTAWAY = "RETURNS_PUTAWAY"


class Priority(str, enum.Enum):
```

with:

```python
    TOTE_TO_STATION = "TOTE_TO_STATION"
    RETURN_TOTE = "RETURN_TOTE"
    RETURNS_PUTAWAY = "RETURNS_PUTAWAY"
    # Stations: the picker moves units from a tote onto the conveyor at Pick 1,
    # an arm packs an order's items into a carton; and two jobs people do —
    # picking at Pick 2 and clearing a conveyor jam (backend/human_jobs.py).
    PICK_ITEMS = "PICK_ITEMS"
    PACK_ORDER = "PACK_ORDER"
    MANUAL_PICK = "MANUAL_PICK"
    CLEAR_JAM = "CLEAR_JAM"


class Priority(str, enum.Enum):
```

- [ ] **Step 4: The station and human job specs**

**Replace in** `backend/jobs.py`:

```python
from . import goods
from .models import Action, ActionType, BoxKind, BoxStatus, Cell, TaskType
from .task_planner import PlanningError
```

with:

```python
from . import goods
from .models import CERTIFICATION_REQUIREMENTS, Action, ActionType, BoxKind, BoxStatus, Cell, TaskType
from .task_planner import PlanningError
```

**Replace in** `backend/jobs.py`:

```python
    classes=frozenset({"HUMANOID"}), check=_check_returns_putaway, plan=_plan_returns_putaway,
    target=floor_target, carries_box=True)

```

with:

```python
    classes=frozenset({"HUMANOID"}), check=_check_returns_putaway, plan=_plan_returns_putaway,
    target=floor_target, carries_box=True)


# --------------------------------------------------------------------------- #
# Stations (spec §9.2): units onto the line, an order packed; and the two jobs
# people do there (backend/human_jobs.py runs those)
# --------------------------------------------------------------------------- #
def _quantity(task: Any) -> Optional[str]:
    try:
        quantity = int(task.params.get("quantity"))
    except (TypeError, ValueError):
        return f"{task.type.value} needs a quantity"
    if isinstance(task.params.get("quantity"), bool) or quantity < 1:
        return "quantity must be a whole number of at least 1"
    task.params["quantity"] = quantity
    return None


def _check_units(manager: Any, task: Any, picker: str, default: str) -> Optional[str]:
    """A pick of `quantity` units out of a tote standing on the station's
    tote drop, at a station where `picker` (ROBOT or HUMAN) picks."""
    twin = manager.twin
    station = twin.warehouse.resolve_zone(task.params.get("station") or default)
    if station is None or "work_cell" not in station.attributes:
        return f"{task.params.get('station')} is not a pick station"
    if station.attributes.get("picker") != picker:
        other = "MANUAL_PICK" if picker == "ROBOT" else "PICK_ITEMS"
        return f"{station.label} is not a {picker.lower()} pick station: use {other}"
    task.params["station"] = task.destination = station.key
    reason = check_box(manager, task, BoxKind.TOTE) or _quantity(task)
    if reason:
        return reason
    box = twin.find_box(task.box_id)
    drop = tuple(station.attributes["tote_drop"])
    if box.position != drop:
        return f"{box.name} is not on {station.label}'s tote drop ({drop[0]},{drop[1]})"
    if task.params["quantity"] > box.quantity:
        return f"{box.name} holds only {box.quantity} of {box.sku}"
    pack = task.params.get("pack_cell")
    if pack and (twin.equipment is None or pack not in twin.equipment.arm_cells):
        return f"Unknown pack cell {pack!r}"
    return None


def _check_pick_items(manager: Any, task: Any) -> Optional[str]:
    return _check_units(manager, task, "ROBOT", "pick_station_1")


def _work_target(planner: Any, task: Any, origin: Cell, profile: Any, layer: str) -> Tuple[Cell, str]:
    station = planner.twin.warehouse.zones[task.params["station"]]
    return tuple(station.attributes["work_cell"]), station.label


def _plan_pick_items(planner: Any, task: Any, robot: Any, blocked: Set[Cell]) -> Tuple[List[Any], List[Cell], int]:
    """NAVIGATE the work cell, then GRASP × n → PLACE_ON_CONVEYOR × n, waiting
    for the infeed to clear before each placement."""
    twin = planner.twin
    station = twin.warehouse.zones[task.params["station"]]
    work, infeed = tuple(station.attributes["work_cell"]), tuple(station.attributes["conveyor_infeed"])
    box = task_box(twin, task)
    order = task.params.get("order_id")
    stop = twin.equipment.arm_cells.get(task.params.get("pack_cell")) if twin.equipment else None
    actions = [Action(ActionType.NAVIGATE, f"Navigate to {station.label}", work, station.label)]
    for number in range(1, task.params["quantity"] + 1):
        actions += [
            Action(ActionType.GRASP, f"Pick unit {number} from {box.name}", work, station.label,
                   params={"unit_from": box.id, "order_id": order}),
            Action(ActionType.WAIT_CLEAR, "Wait for the infeed", infeed, "conveyor",
                   params={"reason": "CONVEYOR_OCCUPIED", "cell": list(infeed)}),
            Action(ActionType.PLACE_ON_CONVEYOR, f"Unit {number} onto the conveyor", infeed, "conveyor",
                   params={"stop_at": list(stop) if stop else None}),
        ]
    return actions, [work], task.params["quantity"]


def _check_pack_order(manager: Any, task: Any) -> Optional[str]:
    twin = manager.twin
    if not task.params.get("order_id"):
        return "PACK_ORDER needs an order_id"
    reason = _quantity(task)
    if reason:
        return reason
    task.params["pack_cell"] = task.params.get("pack_cell") or "pack_cell_1"
    if twin.equipment is None or task.params["pack_cell"] not in twin.equipment.arm_cells:
        return f"Unknown pack cell {task.params['pack_cell']!r}"
    task.params["lane"] = task.params.get("lane") or twin.equipment.sorter.lanes[0]
    if task.params["lane"] not in twin.equipment.sorter.lanes:
        return f"{task.params['lane']} is not a sorter lane (lanes: {', '.join(twin.equipment.sorter.lanes)})"
    task.destination = task.params["lane"]
    return None


def _pack_target(planner: Any, task: Any, origin: Cell, profile: Any, layer: str) -> Tuple[Cell, str]:
    """The pack cell's arm: only the arm standing there can pack in it."""
    zone = planner.twin.warehouse.zones[task.params["pack_cell"]]
    return tuple(zone.attributes["arm_cell"]), zone.label


def _plan_pack_order(planner: Any, task: Any, robot: Any, blocked: Set[Cell]) -> Tuple[List[Any], List[Cell], int]:
    """GRASP each order item from the working conveyor cell → PLACE it into
    the carton; then PLACE_ON_CONVEYOR the carton once the cell is empty."""
    twin = planner.twin
    work = twin.equipment.arm_cells[task.params["pack_cell"]]
    order, lane = task.params["order_id"], task.params["lane"]
    actions: List[Any] = []
    for number in range(1, task.params["quantity"] + 1):
        actions += [
            Action(ActionType.WAIT_CLEAR, f"Wait for item {number} of {order}", work, "conveyor",
                   params={"reason": "AWAITING_ITEM", "cell": list(work), "order_id": order}),
            Action(ActionType.GRASP, f"Grasp item {number} of {order}", work, "conveyor",
                   params={"from_conveyor": list(work), "order_id": order}),
            Action(ActionType.PLACE, f"Pack item {number} into the carton", robot.position, "carton",
                   params={"into_carton": order, "lane": lane}),
        ]
    actions += [
        Action(ActionType.WAIT_CLEAR, "Wait for a free conveyor cell", work, "conveyor",
               params={"reason": "CONVEYOR_OCCUPIED", "cell": list(work)}),
        Action(ActionType.PLACE_ON_CONVEYOR, f"Carton for {order} onto the conveyor", work, "conveyor",
               params={"carton_for": order}),
    ]
    return actions, [], 0


def _check_manual_pick(manager: Any, task: Any) -> Optional[str]:
    return _check_units(manager, task, "HUMAN", "pick_station_2")


def _check_clear_jam(manager: Any, task: Any) -> Optional[str]:
    twin = manager.twin
    cell = parse_cell(task.params.get("segment"))
    if cell is None:
        return "CLEAR_JAM needs a segment (a conveyor cell, as x,y)"
    if twin.equipment is None or cell not in twin.equipment.conveyor.jams:
        return f"Conveyor cell ({cell[0]},{cell[1]}) is not jammed"
    task.params["segment"] = f"{cell[0]},{cell[1]}"
    if cell not in twin.equipment.arm_cells.values() and \
            task.required_certification == CERTIFICATION_REQUIREMENTS.get(TaskType.CLEAR_JAM.value):
        task.required_certification = None  # outside the fenced cells anyone on shift may clear it
    return None


JOB_SPECS[TaskType.PICK_ITEMS] = JobSpec(
    label="Pick items", guide="box_id(a tote on Pick 1's tote drop)+quantity[+order_id+pack_cell]",
    classes=frozenset({"PICKER"}), check=_check_pick_items, plan=_plan_pick_items, target=_work_target)
JOB_SPECS[TaskType.PACK_ORDER] = JobSpec(
    label="Pack order", guide="order_id+quantity[+pack_cell, default pack_cell_1+lane, default dock_4]",
    classes=frozenset({"ARM"}), check=_check_pack_order, plan=_plan_pack_order, target=_pack_target)
JOB_SPECS[TaskType.MANUAL_PICK] = JobSpec(
    label="Manual pick", guide="box_id(a tote on Pick 2's tote drop)+quantity[+order_id+pack_cell+operator_id]",
    classes=frozenset(), check=_check_manual_pick, human=True)
JOB_SPECS[TaskType.CLEAR_JAM] = JobSpec(
    label="Clear jam", guide="segment(x,y)[+operator_id](needs robot_cell_access inside a pack cell)",
    classes=frozenset(), check=_check_clear_jam, human=True)

```

- [ ] **Step 5: The jobs people do**

**Create** `backend/human_jobs.py`:

```python
"""Jobs people do (multi-embodiment spec §9.2): MANUAL_PICK at Pick 2 and
CLEAR_JAM on the conveyor.

A human job has no robot. TaskManager validates it (choosing the nearest
qualified person on the floor for AUTO) and starts it; each Simulator tick
then moves it through its phases, kept on Task.params: WALK to the job's zone
(people.start_transit), WORK there, and for a jam RETURN to where the person
came from — a person left standing in a fenced pack cell would hold its arm
(PERSON_IN_CELL) forever. A conveyor jam gets its CLEAR_JAM job from
ensure_jam_jobs, retried while no one qualified is on the floor (spec §11.3).
"""
from __future__ import annotations

from typing import Any, List, Optional

from . import goods, people
from .embodiment import seconds_to_ticks
from .jobs import parse_cell
from .models import CONFIG, Action, ActionType, Cell, LogCategory, OperatorStatus, TaskStatus, TaskType, cell_dict

HUMAN_JOBS = frozenset({TaskType.MANUAL_PICK, TaskType.CLEAR_JAM})

#: The arm model a jam inside a pack cell needs robot_cell_access scoped to.
ARM_MODEL = "FB-CX10"


def jam_cell(task: Any) -> Optional[Cell]:
    return parse_cell(task.params.get("segment"))


def in_pack_cell(twin: Any, cell: Optional[Cell]) -> bool:
    """A jam on an arm's working cell is inside its fenced pack cell."""
    return twin.equipment is not None and cell in twin.equipment.arm_cells.values()


def jam_zone(twin: Any, cell: Cell) -> str:
    """Where a person stands to clear a jam: the pack cell whose arm works
    `cell`, else the conveyor itself."""
    for key, work_cell in twin.equipment.arm_cells.items():
        if work_cell == cell:
            return key
    return "conveyor"


def equipment_model(twin: Any, task: Any) -> Optional[str]:
    """The equipment model a job's credential scope must cover: the arm a
    jam inside a pack cell is beside, else none."""
    cell = jam_cell(task)
    if task.type is TaskType.CLEAR_JAM and in_pack_cell(twin, cell):
        arm = next((r for r in twin.robots.values()
                    if twin.equipment.arm_cells.get(twin.warehouse.fixed_stations.get(r.position)) == cell), None)
        return arm.model_code if arm is not None and arm.model_code else ARM_MODEL
    return None


def target_zone(twin: Any, task: Any) -> str:
    if task.type is TaskType.CLEAR_JAM:
        return jam_zone(twin, jam_cell(task))
    return task.params.get("station") or "pick_station_2"


def start(twin: Any, task: Any, operator: Any) -> None:
    """Put `operator` on the job and send them walking to it."""
    zone = target_zone(twin, task)
    label = twin.warehouse.zones[zone].label
    if task.type is TaskType.CLEAR_JAM:
        cell = jam_cell(task)
        work = f"Clear the jam at ({cell[0]},{cell[1]})"
        back = [Action(ActionType.WAIT, f"Walk back to {operator.zone}")]
    else:
        work = f"Pick {task.params['quantity']} item(s) onto the conveyor"
        back = []
    task.actions = [Action(ActionType.WAIT, f"Walk to {label}"), Action(ActionType.WAIT, work)] + back + [
        Action(ActionType.COMPLETE, "Complete task")]
    task.action_index = 0
    task.params.update(phase="WALK", origin_zone=operator.zone, picked=0)
    operator.current_task = task.id
    operator.set_status(OperatorStatus.ON_TASK)
    people.start_transit(twin, operator, zone)


def _work_ticks(task: Any) -> int:
    seconds = CONFIG["CLEAR_JAM_S"] if task.type is TaskType.CLEAR_JAM else CONFIG["MANUAL_PICK_S_PER_ITEM"]
    return seconds_to_ticks(seconds)


def _next(task: Any, phase: str) -> None:
    task.params["phase"] = phase
    task.action_index = min(task.action_index + 1, len(task.actions) - 1)


def tick(twin: Any) -> None:
    """Move every running human job on by one tick (Simulator.tick)."""
    for task in list(twin.tasks.tasks.values()):
        if task.type not in HUMAN_JOBS or task.status is not TaskStatus.IN_PROGRESS:
            continue
        operator = twin.find_operator(task.operator_id)
        if operator is None or operator.status is OperatorStatus.OFF_DUTY or operator.zone is None:
            twin.tasks.fail_task(task, f"{operator.name if operator else task.operator_id} left the floor")
            continue
        try:
            _advance(twin, task, operator)
        except ValueError as exc:
            twin.tasks.fail_task(task, f"{operator.name} could not finish: {exc}")


def _advance(twin: Any, task: Any, operator: Any) -> None:
    phase = task.params["phase"]
    if operator.in_transit:
        return
    if phase == "WALK":
        _next(task, "WORK")
        task.params["until_tick"] = twin.tick_count + _work_ticks(task)
        return
    if phase == "RETURN":
        twin.tasks.complete_task(task, f"{operator.name} cleared the jam and is back")
        return
    if twin.tick_count < task.params["until_tick"]:
        return
    if task.type is TaskType.CLEAR_JAM:
        twin.equipment.clear_jam(jam_cell(task))
        _next(task, "RETURN")
        origin = task.params.get("origin_zone")
        if origin and origin != operator.zone:
            people.start_transit(twin, operator, origin)
        return
    _place_one_item(twin, task, operator)


def _place_one_item(twin: Any, task: Any, operator: Any) -> None:
    """MANUAL_PICK: one unit out of the tote onto the infeed, once it's free."""
    station = twin.warehouse.zones[task.params.get("station") or "pick_station_2"]
    infeed = tuple(station.attributes["conveyor_infeed"])
    if not twin.equipment.conveyor.is_free(infeed):
        return  # an item is passing: wait with the unit still in the tote
    tote = twin.find_box(task.box_id)
    declared, true = goods.take_units(twin, tote, 1)
    item = twin.add_box(kind="ITEM", position=tuple(station.attributes["work_cell"]), sku=tote.sku, quantity=1,
                        weight=declared, true_weight_kg=true, order_id=task.params.get("order_id"))
    pack_cell = task.params.get("pack_cell")
    stop = twin.equipment.arm_cells.get(pack_cell) if pack_cell else None
    twin.equipment.place(item, infeed, operator.id, task_id=task.id, order_id=item.order_id, stop_at=stop)
    task.params["picked"] = int(task.params.get("picked", 0)) + 1
    if task.params["picked"] >= int(task.params["quantity"]):
        _next(task, "DONE")
        twin.tasks.complete_task(task, f"{operator.name} picked {task.params['picked']} item(s)")
        return
    task.params["until_tick"] = twin.tick_count + _work_ticks(task)


def ensure_jam_jobs(twin: Any) -> List[Any]:
    """Every jammed segment without a running CLEAR_JAM gets one. When the
    gate rejects it (no one qualified on the floor) it is tried again here
    next time, so the jam is cleared as soon as someone qualifies."""
    if twin.equipment is None:
        return []
    covered = {task.params.get("segment") for task in twin.tasks.tasks.values()
               if task.type is TaskType.CLEAR_JAM and not task.is_terminal}
    created = []
    for cell in list(twin.equipment.conveyor.jams):
        segment = f"{cell[0]},{cell[1]}"
        if segment in covered:
            continue
        task = twin.tasks.create_task({"type": "CLEAR_JAM", "segment": segment, "priority": "HIGH"}, internal=True)
        if task.status is TaskStatus.FAILED:
            twin.logger.warning(LogCategory.OPERATIONS, f"No one can clear the jam at {segment} yet: {task.error}",
                                position=cell_dict(cell))
        created.append(task)
    return created
```

- [ ] **Step 6: Choosing, starting and releasing people; the scope rule**

**Replace in** `backend/task_manager.py`:

```python
    ACTIVE_TASK_STATES,
    Action,
```

with:

```python
    ACTIVE_TASK_STATES,
    OperatorStatus,
    Action,
```

**Replace in** `backend/task_manager.py`:

```python
)
from . import people
from .eligibility import (
    agent_eligibility, box_kind_ok, operator_eligibility, payload_ok, reach_ok, robot_eligibility,
)
```

with:

```python
)
from . import human_jobs, people
from .eligibility import (
    agent_eligibility, box_kind_ok, cert_scope_ok, operator_eligibility, payload_ok, reach_ok,
    robot_eligibility,
)
```

**Replace in** `backend/task_manager.py`:

```python
            self._run_instant(task)
            return task

        task.record(TaskStatus.PLANNING, "Validated — waiting for a robot")
        return task

    def _run_immediate(self, task: Task) -> None:
```

with:

```python
            self._run_instant(task)
            return task
        # A person's job starts at once: there is no robot queue to wait in.
        if task_type in human_jobs.HUMAN_JOBS:
            self._run_human(task)
            return task

        task.record(TaskStatus.PLANNING, "Validated — waiting for a robot")
        return task

    def _run_human(self, task: Task) -> None:
        """MANUAL_PICK / CLEAR_JAM: the chosen person walks off to do it; the
        simulator moves it on each tick (backend/human_jobs.py)."""
        operator = self.twin.find_operator(task.operator_id)
        human_jobs.start(self.twin, task, operator)
        self.start_task(task)

    def _run_immediate(self, task: Task) -> None:
```

**Replace in** `backend/task_manager.py`:

```python
            TaskType.MIXED_MAINTENANCE_MISSION,
        )
        if needs_operator:
            if task.requested_operator != "AUTO":
                operator = twin.find_operator(task.operator_id or task.requested_operator)
                if operator is None:
                    return False, f"Operator '{task.requested_operator}' does not exist"
                reason = operator_eligibility(
                    operator.status.value, operator.certifications, task.required_certification
                )
                if reason:
                    return False, f"{operator.name} {reason}"
            else:
                operator = next(
                    (
                        o for o in twin.operators.values()
                        if o.is_available
                        and operator_eligibility(
                            o.status.value, o.certifications, task.required_certification
                        ) is None
                    ),
                    None,
                )
                if operator is None:
```

with:

```python
            TaskType.MIXED_MAINTENANCE_MISSION,
        ) or task.type in human_jobs.HUMAN_JOBS
        if needs_operator:
            if task.requested_operator != "AUTO":
                operator = twin.find_operator(task.operator_id or task.requested_operator)
                if operator is None:
                    return False, f"Operator '{task.requested_operator}' does not exist"
                reason = self._operator_reason(operator, task)
                if reason:
                    return False, f"{operator.name} {reason}"
            else:
                operator = self._choose_operator(task)
                if operator is None:
```

**Replace in** `backend/task_manager.py`:

```python
                        return False, "The second sign-off must be a different operator from the first"
                    reason = operator_eligibility(
                        second.status.value, second.certifications, task.required_certification
                    )
                    if reason:
```

with:

```python
                        return False, "The second sign-off must be a different operator from the first"
                    reason = self._operator_reason(second, task)
                    if reason:
```

**Replace in** `backend/task_manager.py`:

```python
                            if o.id != operator.id and o.is_available
                            and operator_eligibility(
                                o.status.value, o.certifications, task.required_certification
                            ) is None
                        ),
```

with:

```python
                            if o.id != operator.id and o.is_available
                            and self._operator_reason(o, task) is None
                        ),
```

**Replace in** `backend/task_manager.py`:

```python
        # AGENT_INSPECTION / HUMAN_INSPECTION need nothing else — no
        # robot, no box, no destination.
        if task.type in self.INSTANT:
            return True, None
```

with:

```python
        # AGENT_INSPECTION / HUMAN_INSPECTION need nothing else — no
        # robot, no box, no destination. Nor does a person's own job.
        if task.type in self.INSTANT or task.type in human_jobs.HUMAN_JOBS:
            return True, None
```

**Replace in** `backend/task_manager.py`:

```python

        return True, None

    # ------------------------------------------------------------------ #
```

with:

```python

        return True, None

    # ------------------------------------------------------------------ #
    # Operators (spec §9.2, §11.3)
    # ------------------------------------------------------------------ #
    def _operator_reason(self, operator: Any, task: Task) -> Optional[str]:
        """Why `operator` can't take `task`, or None. On a layered floor a
        certification must also be in scope for the equipment model the job
        involves and the floor's site (spec §9.2); a person's own job also
        needs them on the floor and free. Classic keeps operator_eligibility."""
        reason = operator_eligibility(operator.status.value, operator.certifications, task.required_certification)
        if reason:
            return reason
        if task.type in human_jobs.HUMAN_JOBS:
            if operator.zone is None:
                return "is not on the floor"
            if operator.current_task and operator.current_task != task.id:
                return f"is busy with {operator.current_task}"
        if self.twin.layout_name == "classic" or not task.required_certification:
            return None
        ok, reason = cert_scope_ok(operator.certification_scopes, task.required_certification,
                                   self._scope_model(task), people.FLOOR_SITE)
        return None if ok else reason

    def _scope_model(self, task: Task) -> Optional[str]:
        """The equipment model a job's credential must cover: the arm beside
        a jam, else the robot the job names, else none."""
        model = human_jobs.equipment_model(self.twin, task)
        if model:
            return model
        robot = self.twin.find_robot(task.robot_id or task.requested_robot)
        return robot.model_code if robot is not None else None

    def _choose_operator(self, task: Task) -> Optional[Any]:
        """AUTO: the first eligible, available operator — and for a person's
        own job the nearest one on the floor to where the job is."""
        eligible = [o for o in self.twin.operators.values()
                    if o.is_available and self._operator_reason(o, task) is None]
        if task.type not in human_jobs.HUMAN_JOBS or not eligible:
            return eligible[0] if eligible else None
        warehouse = self.twin.warehouse
        where = warehouse.zones[human_jobs.target_zone(self.twin, task)].center
        return min(eligible, key=lambda o: abs(warehouse.zones[o.zone].center[0] - where[0])
                   + abs(warehouse.zones[o.zone].center[1] - where[1]))

    def _release_operator(self, task: Task, completed: bool) -> None:
        """A person's job ended: they are free again."""
        operator = self.twin.find_operator(task.operator_id) if task.operator_id else None
        if operator is None or operator.current_task != task.id:
            return
        operator.current_task = None
        if completed:
            operator.completed_tasks += 1
        else:
            operator.failed_tasks += 1
        if operator.status == OperatorStatus.ON_TASK:
            operator.set_status(OperatorStatus.AVAILABLE)

    # ------------------------------------------------------------------ #
```

**Replace in** `backend/task_manager.py`:

```python
        reserve_ids = task.box_ids if task.type == TaskType.BATCH_DELIVER else ([task.box_id] if task.box_id else [])
        for box_id in reserve_ids:
```

with:

```python
        reserve_ids = task.box_ids if task.type == TaskType.BATCH_DELIVER else ([task.box_id] if task.box_id else [])
        spec = JOB_SPECS.get(task.type)
        if spec is not None and not spec.carries_box:
            reserve_ids = []  # a picker takes units out of the tote; the tote itself stays put
        for box_id in reserve_ids:
```

**Replace in** `backend/task_manager.py`:

```python
                if not robot.is_halted and robot.status != twin.RobotStatus.CHARGING:
                    robot.set_status(twin.RobotStatus.IDLE)

        # The operator's sign-off — logged, not gating: the robot's
```

with:

```python
                if not robot.is_halted and robot.status != twin.RobotStatus.CHARGING:
                    robot.set_status(twin.RobotStatus.IDLE)
        self._release_operator(task, completed=True)

        # The operator's sign-off — logged, not gating: the robot's
```

**Replace in** `backend/task_manager.py`:

```python
        self._release_boxes(task, allow_status=(twin.BoxStatus.RESERVED,))
        twin.statistics["failed_tasks"] += 1
```

with:

```python
        self._release_boxes(task, allow_status=(twin.BoxStatus.RESERVED,))
        self._release_operator(task, completed=False)
        twin.statistics["failed_tasks"] += 1
```

**Replace in** `backend/task_manager.py`:

```python
        self._release_boxes(task, allow_status=(twin.BoxStatus.RESERVED, twin.BoxStatus.PICKING))
        twin.events.emit(
```

with:

```python
        self._release_boxes(task, allow_status=(twin.BoxStatus.RESERVED, twin.BoxStatus.PICKING))
        self._release_operator(task, completed=False)
        twin.events.emit(
```

**Replace in** `backend/simulator.py`:

```python
from datetime import datetime

from . import energy, goods, people
from .eligibility import reach_ok, robot_eligibility
from .embodiment import AIR, GROUND, HOVER_CLEARANCE_M, lift_ticks, seconds_to_ticks, step_ticks
```

with:

```python
from datetime import datetime

from . import energy, goods, human_jobs, people
from .eligibility import reach_ok, robot_eligibility
from .embodiment import AIR, GROUND, HOVER_CLEARANCE_M, lift_ticks, seconds_to_ticks, step_ticks
```

**Replace in** `backend/simulator.py`:

```python
                        twin.tasks.fail_task(task, f"Controller error: {exc}")

            if twin.equipment is not None:
                twin.equipment.tick()  # the conveyor and sorter move after robots place items
            self._detect_collisions()
            self._apply_energy(distance_before)
```

with:

```python
                        twin.tasks.fail_task(task, f"Controller error: {exc}")

            human_jobs.tick(twin)  # people's jobs, beside the robots
            if twin.equipment is not None:
                twin.equipment.tick()  # the conveyor and sorter move after robots place items
                if twin.tick_count % CONFIG["SHIFT_CHECK_EVERY_TICKS"] == 0:
                    human_jobs.ensure_jam_jobs(twin)  # a jam gets someone to clear it
            self._detect_collisions()
            self._apply_energy(distance_before)
```

- [ ] **Step 7: Run the new tests with the earlier operator tests**

Classic operator tests (`tests.py`'s HUMAN_INSPECTION, sign-off and dual sign-off cases) keep their results: on classic the check is `operator_eligibility` alone.

Run: `.venv/bin/python -m pytest -o addopts="" -q backend/test_station_jobs.py backend/test_people.py backend/tests.py -k "station or operator or signoff or people or inspection"`
Expected: `58 passed, 136 deselected`.

- [ ] **Step 8: Run the full suite**

Run: `.venv/bin/python -m pytest -o addopts="" -q`
Expected: `2 failed, 570 passed`.

- [ ] **Step 9: Commit**

```bash
git add backend/human_jobs.py backend/models.py backend/jobs.py backend/task_manager.py \
        backend/simulator.py backend/test_station_jobs.py
git commit -m "feat: pick and pack jobs, and the jobs people do

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: Drone counts and scout patrols

The last two robot job types and the last step. `CYCLE_COUNT` (a drone: TAKEOFF → NAVIGATE on AIR to the aisle cell in front of a pallet rack face → SCAN × levels → NAVIGATE back to the pad → LAND) counts each slot at its level's height plus `HOVER_CLEARANCE_M` (1a's carry list). `SCAN` takes `scan_s` per level and reports what it counted — the truth, or 1–3 off it under `SCAN_MISCOUNT_RISK` — against the record: a variance emits `STOCK_VARIANCE_DETECTED`, and one of 2 units or fewer is reconciled right there, by the count job, as 1a's carry list asks; a bigger one is left for review. A drone gets the hard energy gate of §10.1: its battery must cover the whole flight plus 25% of capacity (`drone_round_trip_ok`), in the gate and in robot selection. `PATROL` (a scout) goes corner by corner round `patrol_loop` from the corner nearest it and ends by reporting anomalies on the loop — a halted robot, a FAILED box.

**Files:**
- Modify: `backend/models.py` (`ActionType.SCAN`; `TaskType.CYCLE_COUNT`, `PATROL`; `EventType.SCANNED`, `STOCK_VARIANCE_DETECTED`; the drone and scout presets)
- Modify: `backend/simulator.py` (the SCAN step; the patrol report)
- Modify: `backend/jobs.py` (`JobSpec.mission_wh`; the two specs)
- Modify: `backend/task_manager.py` (the drone round-trip gate in `capability_reason`)
- Test: `backend/test_drone_jobs.py` (create)

**Interfaces:**
- Consumes (Tasks 1, 2, 4, 6, 7): `drone_round_trip_ok`; `goods.physical_qty`, `goods.sync_box`, `StockLedger.record_count/reconcile`; the step framework (`_begin_step`/`_finish_step` tables, `_emit_effect`, `_slot_for`); `energy.flight_wh_per_tick`, `energy.energy_wh`; `JobSpec(air=True)`; the `patrol_loop` zone's `route` corners.
- Produces:
  - `ActionType.SCAN` (in `STEP_ACTIONS`): `SCAN(slot_id, level)` from a face cell, flying.
  - `TaskType.CYCLE_COUNT {face "x,y"}`, `TaskType.PATROL {}`; DRONE allows `CYCLE_COUNT`, SCOUT allows `PATROL`.
  - `EventType.SCANNED` (data `slot`, `level`, `reported_qty`, `true_qty`, `recorded_qty`, `sku`, plus `_emit_effect`'s limits), `STOCK_VARIANCE_DETECTED` (category `OPERATIONS`, data `slot`, `sku`, `recorded_qty`, `counted_qty`, `variance`, `auto_reconciled`).
  - `JobSpec.mission_wh: Optional[MissionFn]` — `(manager, task, robot) -> Optional[float]` Wh; `capability_reason` returns `"can't fly it: <drone_round_trip_ok reason>"`.
  - `backend.jobs.face_slots(twin, task)`, `PATROL_LOOP = "patrol_loop"`; a PATROL's `task.params["anomalies"]: List[str]`; a `WAIT` action with `params={"report": True}` runs `Simulator._patrol_report`.

- [ ] **Step 1: Write the failing tests**

**Create** `backend/test_drone_jobs.py`:

```python
"""Drone counts and scout patrols (multi-embodiment spec §7.2, §9.2, §10.1):
a drone counts a rack face level by level and flags variances, a small one
reconciled on the spot; a flight needs the energy for the round trip and a
reserve; a scout patrols the loop and reports what it found."""
import pytest

from backend import energy
from backend.digital_twin import DigitalTwin
from backend.embodiment import HOVER_CLEARANCE_M
from backend.models import BoxStatus, RobotStatus, SimulationStatus, TaskStatus
from backend.simulator import Simulator


@pytest.fixture
def twin(tmp_path):
    return DigitalTwin(log_dir=str(tmp_path / "logs"), data_dir=str(tmp_path / "data"),
                       persist_logs=False, demo=True, demo_tasks=False, layout="distribution_center")


@pytest.fixture
def sim(twin):
    simulator = Simulator(twin)
    twin.simulation_status = SimulationStatus.RUNNING
    return simulator


@pytest.fixture
def drone(twin):
    return twin.add_robot(name="IX2-208", asset_id="AST-000208", position=(20, 2))


def run(sim, task, max_ticks=2000):
    for _ in range(max_ticks):
        if task.is_terminal:
            return
        sim.tick()
    assert task.is_terminal, f"{task.id} still {task.status.value}"


def stock_face(twin):
    for level, quantity in ((0, 40), (1, 30), (2, 20)):
        twin.add_box(name=f"PAL-{level}", kind="PALLET", sku=f"SKU-0{level}", quantity=quantity, weight=500.0,
                     slot=f"PR-12-02-{level}")
    twin.stock.adjust_true("PR-12-02-1", -2)     # two cases short: within the auto-reconcile margin
    twin.stock.adjust_true("PR-12-02-2", -5)     # five short: an exception


def test_a_drone_counts_a_rack_face_and_flags_variances(twin, sim, drone):
    stock_face(twin)
    task = twin.tasks.create_task({"type": "CYCLE_COUNT", "face": "12,2"})
    assert task.status is TaskStatus.PLANNING, task.error
    altitudes = {}

    def watch():
        if drone.activity == "SCAN":
            altitudes[drone.position] = altitudes.get(drone.position, set()) | {drone.altitude_m}

    for _ in range(1500):
        if task.is_terminal:
            break
        sim.tick()
        watch()
    assert task.status is TaskStatus.COMPLETED, task.error
    assert (drone.layer, drone.position) == ("GROUND", (20, 2))
    assert altitudes == {(12, 3): {level + HOVER_CLEARANCE_M for level in range(5)}}
    steps = [e["data"]["step"] for e in twin.events.query(task_id=task.id, event_type="ROBOT_STEP")]
    assert steps == ["TAKEOFF", "NAVIGATE"] + ["SCAN"] * 5 + ["NAVIGATE", "LAND"]
    scans = [e["data"] for e in twin.events.query(task_id=task.id, event_type="SCANNED")]
    assert [(s["level"], s["reported_qty"], s["true_qty"]) for s in scans] == \
        [(0, 40, 40), (1, 28, 28), (2, 15, 15), (3, 0, 0), (4, 0, 0)]
    variances = [e["data"] for e in twin.events.query(task_id=task.id, event_type="STOCK_VARIANCE_DETECTED")]
    assert [(v["slot"], v["variance"], v["auto_reconciled"]) for v in variances] == \
        [("PR-12-02-1", -2, True), ("PR-12-02-2", -5, False)]
    assert twin.stock.location("PR-12-02-1").recorded_qty == 28 == twin.find_box("PAL-1").quantity
    assert twin.stock.location("PR-12-02-2").recorded_qty == 20   # left for review
    assert drone.battery < 100.0


def test_a_miscount_is_reported_as_counted(twin, sim, drone):
    stock_face(twin)
    twin.faults.arm("scan_miscount")
    task = twin.tasks.create_task({"type": "CYCLE_COUNT", "face": "12,2"})
    run(sim, task)
    assert task.status is TaskStatus.COMPLETED                     # the drone believes its count
    first = twin.events.query(task_id=task.id, event_type="SCANNED")[0]["data"]
    assert first["reported_qty"] != first["true_qty"] == 40
    assert 1 <= abs(first["reported_qty"] - 40) <= 3


def test_a_drone_flies_only_with_the_energy_for_the_round_trip(twin, sim, drone):
    other = twin.add_robot(name="IX2-209", asset_id="AST-000209", position=(21, 2))
    drone.battery = 40.0
    named = twin.tasks.create_task({"type": "CYCLE_COUNT", "face": "12,2", "robot_id": drone.id})
    assert named.status is TaskStatus.FAILED and named.error.startswith("IX2-208 can't fly it: has 60.0 Wh")
    assert "plus a 25% reserve" in named.error
    auto = twin.tasks.create_task({"type": "CYCLE_COUNT", "face": "12,2"})
    scored, _ = twin.tasks._score_candidates(auto)
    assert [entry[1] for entry in scored] == [other]
    other.battery = 40.0
    twin.tasks.cancel_task(auto.id)
    grounded = twin.tasks.create_task({"type": "CYCLE_COUNT", "face": "12,2"})
    assert grounded.status is TaskStatus.FAILED and grounded.error.startswith("No robot can do this CYCLE_COUNT")
    flight = energy.flight_wh_per_tick(drone.mobility)
    assert 0.17 < flight < 0.171                                    # 150 Wh over 22 min, x 10, a 0.15 s tick


def test_count_requests_are_checked(twin, drone):
    amr = twin.add_robot(name="TR50-201", asset_id="AST-000201", position=(8, 13))
    assert twin.tasks.create_task({"type": "CYCLE_COUNT", "face": "10,3"}).error == "(10,3) is not a pallet rack face"
    assert twin.tasks.create_task({"type": "CYCLE_COUNT"}).error == \
        "CYCLE_COUNT needs a face (a pallet rack cell, as x,y)"
    by_amr = twin.tasks.create_task({"type": "CYCLE_COUNT", "face": "12,2", "robot_id": amr.id})
    assert by_amr.error == "TR50-201 is an AMR; CYCLE_COUNT needs a DRONE"


def test_a_scout_patrols_the_loop_and_reports_what_it_finds(twin, sim):
    scout = twin.add_robot(name="SC1-204", asset_id="AST-000204", position=(7, 5))
    broken = twin.add_robot(name="TR50-201", asset_id="AST-000201", position=(10, 11))
    broken.set_status(RobotStatus.ERROR)
    dropped = twin.add_box(name="ITEM-9", kind="ITEM", weight=0.4, position=(12, 1))
    dropped.set_status(BoxStatus.FAILED)
    task = twin.tasks.create_task({"type": "PATROL"})
    run(sim, task)
    assert task.status is TaskStatus.COMPLETED and task.robot_id == scout.id, task.error
    corners = [a.target for a in task.actions if a.type.value == "NAVIGATE"]
    assert corners == [(7, 1), (18, 1), (18, 11), (7, 11), (7, 1)]
    assert task.params["anomalies"] == ["TR50-201 is ERROR at (10,11)", "ITEM-9 is FAILED at (12,1)"]
    report = [r for r in twin.logger.query(task_id=task.id) if "patrol report" in r["message"]]
    assert len(report) == 1 and report[0]["level"] == "WARNING"
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/bin/python -m pytest -o addopts="" -q backend/test_drone_jobs.py`
Expected: `5 failed` — `ValueError: Unknown task type 'CYCLE_COUNT'` (and `'PATROL'`).

- [ ] **Step 3: The scan step, the job types and their events**

**Replace in** `backend/models.py`:

```python
        "speed": 3.0,
        "allowed_task_types": ["MOVE_ROBOT", "MIXED_MAINTENANCE_MISSION", "CHARGE_ROBOT"],
    },
```

with:

```python
        "speed": 3.0,
        "allowed_task_types": ["MOVE_ROBOT", "MIXED_MAINTENANCE_MISSION", "CHARGE_ROBOT", "PATROL"],
    },
```

**Replace in** `backend/models.py`:

```python
        "speed": 4.0,
        "allowed_task_types": ["MOVE_ROBOT", "MIXED_MAINTENANCE_MISSION", "CHARGE_ROBOT"],
    },
```

with:

```python
        "speed": 4.0,
        "allowed_task_types": ["MOVE_ROBOT", "MIXED_MAINTENANCE_MISSION", "CHARGE_ROBOT", "CYCLE_COUNT"],
    },
```

**Replace in** `backend/models.py`:

```python
    PACK_ORDER = "PACK_ORDER"
    MANUAL_PICK = "MANUAL_PICK"
    CLEAR_JAM = "CLEAR_JAM"


class Priority(str, enum.Enum):
```

with:

```python
    PACK_ORDER = "PACK_ORDER"
    MANUAL_PICK = "MANUAL_PICK"
    CLEAR_JAM = "CLEAR_JAM"
    # A drone counting a pallet rack face, level by level; a scout patrolling
    # the security loop and reporting what it found.
    CYCLE_COUNT = "CYCLE_COUNT"
    PATROL = "PATROL"


class Priority(str, enum.Enum):
```

**Replace in** `backend/models.py`:

```python
    LIFTED = "LIFTED"
    LOWERED = "LOWERED"
    PLACED = "PLACED"


class ActionType(str, enum.Enum):
```

with:

```python
    LIFTED = "LIFTED"
    LOWERED = "LOWERED"
    PLACED = "PLACED"
    # A drone read a slot (reported against true and recorded quantities), and
    # a count disagreed with the record (spec §7.2).
    SCANNED = "SCANNED"
    STOCK_VARIANCE_DETECTED = "STOCK_VARIANCE_DETECTED"


class ActionType(str, enum.Enum):
```

**Replace in** `backend/models.py`:

```python
    # Simulator._act_step: lift or lower forks/platform to a slot level, a
    # drone's take-off and landing, grasping and placing a box
    # (in a slot, a carton or on the conveyor), and waiting until a condition
```

with:

```python
    # Simulator._act_step: lift or lower forks/platform to a slot level, a
    # drone's take-off, landing and shelf scan, grasping and placing a box
    # (in a slot, a carton or on the conveyor), and waiting until a condition
```

**Replace in** `backend/models.py`:

```python
    LAND = "LAND"
    GRASP = "GRASP"
```

with:

```python
    LAND = "LAND"
    SCAN = "SCAN"
    GRASP = "GRASP"
```

- [ ] **Step 4: SCAN, variances and the patrol report in the simulator**

**Replace in** `backend/simulator.py`:

```python
STEP_ACTIONS = frozenset({
    ActionType.LIFT_TO, ActionType.LOWER, ActionType.TAKEOFF, ActionType.LAND,
    ActionType.GRASP, ActionType.PLACE, ActionType.PLACE_ON_CONVEYOR, ActionType.WAIT_CLEAR,
```

with:

```python
STEP_ACTIONS = frozenset({
    ActionType.LIFT_TO, ActionType.LOWER, ActionType.TAKEOFF, ActionType.LAND, ActionType.SCAN,
    ActionType.GRASP, ActionType.PLACE, ActionType.PLACE_ON_CONVEYOR, ActionType.WAIT_CLEAR,
```

**Replace in** `backend/simulator.py`:

```python
        else:  # WAIT
            action.done = True
```

with:

```python
        else:  # WAIT
            if action.params.get("report"):
                self._patrol_report(robot, task)
            action.done = True
```

**Replace in** `backend/simulator.py`:

```python
            ActionType.WAIT_CLEAR: self._begin_wait_clear,
        }[action.type]
```

with:

```python
            ActionType.WAIT_CLEAR: self._begin_wait_clear,
            ActionType.SCAN: self._begin_scan,
        }[action.type]
```

**Replace in** `backend/simulator.py`:

```python
            ActionType.WAIT_CLEAR: self._finish_wait_clear,
        }[action.type]
```

with:

```python
            ActionType.WAIT_CLEAR: self._finish_wait_clear,
            ActionType.SCAN: self._finish_scan,
        }[action.type]
```

**Replace in** `backend/simulator.py`:

```python
        return not waiting

    # ---- energy ------------------------------------------------------- #
    def _apply_energy(self, distance_before: Dict[str, int]) -> None:
```

with:

```python
        return not waiting

    # SCAN(slot): scan_s per level, hovering at the level's height plus
    # HOVER_CLEARANCE_M in front of the rack face
    def _begin_scan(self, robot: Any, task: Any, action: Any) -> int:
        slot = self._slot_for(action)
        if slot is None:
            raise ValueError("it was given no slot to scan")
        if robot.layer != AIR or robot.position not in slot.faces:
            raise ValueError(f"it is not flying in front of slot {slot.slot_id}")
        ok, reason = reach_ok(slot.level, robot.mobility.max_shelf_level)
        if not ok:
            raise ValueError(reason)
        self.twin.set_robot_layer(robot.id, AIR, altitude_m=slot.height_m + HOVER_CLEARANCE_M)
        return step_ticks(robot.mobility, "SCAN")

    def _finish_scan(self, robot: Any, task: Any, action: Any) -> bool:
        """Read the slot's tags. The drone reports what it counted — which a
        SCAN_MISCOUNT fault puts 1-3 off the truth — and the count is written
        against the record. A variance is flagged; one of STOCK_AUTO_RECONCILE_UNITS
        or fewer is reconciled here, by the count job (spec §7.2)."""
        twin = self.twin
        slot = self._slot_for(action)
        true_qty = goods.physical_qty(twin, slot.slot_id)
        reported = true_qty
        if twin.faults.roll("scan_miscount"):
            reported = max(0, true_qty + random.choice((-1, 1)) * random.randint(1, 3))
            if reported == true_qty:
                reported = true_qty + random.randint(1, 3)
        location = twin.stock.location(slot.slot_id)
        recorded = location.recorded_qty if location is not None else 0
        self._emit_effect(EventType.SCANNED, robot, task, f"{robot.name} counted {reported} in {slot.slot_id}",
                          slot=slot.slot_id, level=slot.level, reported_qty=reported, true_qty=true_qty,
                          recorded_qty=recorded, sku=location.sku if location else None)
        if location is None:
            if reported:
                self._variance(robot, task, slot, None, reported, recorded, auto=False)
            return True
        count = twin.stock.record_count(slot.slot_id, reported, twin.tick_count)
        if count["variance"]:
            if count["auto_reconcile"]:
                twin.stock.reconcile(slot.slot_id)
                box = twin.find_box(location.box_id)
                if box is not None:
                    goods.sync_box(twin, box)
            self._variance(robot, task, slot, location, reported, recorded, auto=count["auto_reconcile"])
        return True

    def _variance(self, robot: Any, task: Any, slot: Any, location: Optional[Any], counted: int,
                  recorded: int, auto: bool) -> None:
        """STOCK_VARIANCE_DETECTED: a count that disagrees with the record. A
        variance too big to fix here is left for an exception (the orders
        panel, plan 1c)."""
        verb = "reconciled" if auto else "left for review"
        self.twin.events.emit(
            EventType.STOCK_VARIANCE_DETECTED,
            f"{slot.slot_id}: counted {counted}, recorded {recorded} — {verb}",
            category=LogCategory.OPERATIONS,
            level=LogLevel.WARNING,
            robot_id=robot.id,
            task_id=task.id,
            box_id=location.box_id if location else None,
            position=cell_dict(slot.cell),
            data={"slot": slot.slot_id, "sku": location.sku if location else None, "recorded_qty": recorded,
                  "counted_qty": counted, "variance": counted - recorded, "auto_reconciled": auto},
        )

    def _patrol_report(self, robot: Any, task: Any) -> None:
        """A patrol's last step: report the anomalies on the loop — a robot
        halted on it, a box left FAILED on it (spec §9.2)."""
        twin = self.twin
        loop = next((zone for zone in twin.warehouse.zones.values() if "route" in zone.attributes), None)
        cells = set(loop.cells) if loop is not None else set()
        anomalies = [f"{other.name} is {other.status.value} at ({other.position[0]},{other.position[1]})"
                     for other in twin.robots.values()
                     if other.id != robot.id and other.position in cells and other.is_halted]
        anomalies += [f"{box.name} is FAILED at ({box.position[0]},{box.position[1]})"
                      for box in twin.boxes.values() if box.position in cells and box.status is BoxStatus.FAILED]
        task.params["anomalies"] = anomalies
        twin.logger.log(
            LogLevel.WARNING if anomalies else LogLevel.INFO,
            LogCategory.ROBOT,
            f"{robot.name} patrol report: " + ("; ".join(anomalies) if anomalies else "nothing to report"),
            robot_id=robot.id,
            task_id=task.id,
            data={"anomalies": anomalies},
        )

    # ---- energy ------------------------------------------------------- #
    def _apply_energy(self, distance_before: Dict[str, int]) -> None:
```

- [ ] **Step 5: The count and patrol specs**

**Replace in** `backend/jobs.py`:

```python
from typing import Any, Callable, Dict, FrozenSet, List, Optional, Set, Tuple

from . import goods
from .models import CERTIFICATION_REQUIREMENTS, Action, ActionType, BoxKind, BoxStatus, Cell, TaskType
from .task_planner import PlanningError

```

with:

```python
from typing import Any, Callable, Dict, FrozenSet, List, Optional, Set, Tuple

from . import energy, goods
from .embodiment import AIR, HOVER_CLEARANCE_M, step_ticks
from .models import CERTIFICATION_REQUIREMENTS, CONFIG, Action, ActionType, BoxKind, BoxStatus, Cell, TaskType
from .task_planner import PlanningError

```

**Replace in** `backend/jobs.py`:

```python
#: (manager, task) -> a reason the request is invalid, or None
CheckFn = Callable[[Any, Any], Optional[str]]

#: Task payload keys the new job types read, kept on Task.params.
```

with:

```python
#: (manager, task) -> a reason the request is invalid, or None
CheckFn = Callable[[Any, Any], Optional[str]]
#: (manager, task, robot) -> the Wh a flight takes, for the drone round-trip gate
MissionFn = Callable[[Any, Any, Any], Optional[float]]

#: Task payload keys the new job types read, kept on Task.params.
```

**Replace in** `backend/jobs.py`:

```python
    carries_box: bool = False        # the robot lifts the task's box: payload and kind apply
    air: bool = False                # its targets are on the AIR layer
    human: bool = False              # a person does it (backend/human_jobs.py)


JOB_SPECS: Dict[TaskType, JobSpec] = {}
```

with:

```python
    carries_box: bool = False        # the robot lifts the task's box: payload and kind apply
    air: bool = False                # its targets are on the AIR layer
    human: bool = False              # a person does it (backend/human_jobs.py)
    mission_wh: Optional[MissionFn] = None  # a flight's energy: drone_round_trip_ok applies


JOB_SPECS: Dict[TaskType, JobSpec] = {}
```

**Replace in** `backend/jobs.py`:

```python
    classes=frozenset(), check=_check_clear_jam, human=True)

```

with:

```python
    classes=frozenset(), check=_check_clear_jam, human=True)


# --------------------------------------------------------------------------- #
# Drones and scouts (spec §9.2): counting a rack face, patrolling the loop
# --------------------------------------------------------------------------- #
def face_slots(twin: Any, task: Any) -> List[Any]:
    """The pallet slots of the rack cell a count covers, lowest level first."""
    cell = parse_cell(task.params.get("face"))
    return [slot for slot in twin.warehouse.slots_at(cell) if slot.kind == BoxKind.PALLET.value] if cell else []


def _check_count(manager: Any, task: Any) -> Optional[str]:
    cell = parse_cell(task.params.get("face"))
    if cell is None:
        return "CYCLE_COUNT needs a face (a pallet rack cell, as x,y)"
    if not face_slots(manager.twin, task):
        return f"({cell[0]},{cell[1]}) is not a pallet rack face"
    task.params["face"] = f"{cell[0]},{cell[1]}"
    return None


def _count_target(planner: Any, task: Any, origin: Cell, profile: Any, layer: str) -> Tuple[Cell, str]:
    """Where the drone hovers to count: the aisle cell in front of the face."""
    slot = face_slots(planner.twin, task)[0]
    return slot.faces[0], f"rack face {task.params['face']}"


def _plan_count(planner: Any, task: Any, robot: Any, blocked: Set[Cell]) -> Tuple[List[Any], List[Cell], int]:
    """TAKEOFF → NAVIGATE (air) face → SCAN × levels → NAVIGATE pad → LAND."""
    slots = face_slots(planner.twin, task)
    hover, label = _count_target(planner, task, robot.position, robot.mobility, AIR)
    pad = robot.position
    actions: List[Any] = []
    if robot.layer != AIR:
        actions.append(Action(ActionType.TAKEOFF, "Take off", pad, "drone pad",
                              params={"altitude_m": slots[0].height_m + HOVER_CLEARANCE_M}))
    actions.append(Action(ActionType.NAVIGATE, f"Fly to {label}", hover, label))
    actions += [Action(ActionType.SCAN, f"Scan {slot.slot_id}", hover, slot.slot_id, level=slot.level,
                       slot_id=slot.slot_id) for slot in slots]
    actions += [
        Action(ActionType.NAVIGATE, "Fly back to the pad", pad, "drone pad"),
        Action(ActionType.LAND, "Land", pad, "drone pad"),
    ]
    return actions, [], len(slots)


def _count_wh(manager: Any, task: Any, robot: Any) -> Optional[float]:
    """The energy a count flight takes: take-off, the flight out and back,
    a scan per level and the landing, all at the flight rate."""
    profile = robot.mobility
    hover, _ = _count_target(manager.twin.planner, task, robot.position, profile, AIR)
    cells = manager.twin.navigation.distance(robot.position, hover, allow_goal_adjacent=False,
                                             profile=profile, layer=AIR)
    if cells is None:
        return None
    flight = 2 * cells / (profile.speed_cells_s * CONFIG["TICK_DT"])
    steps = step_ticks(profile, "TAKEOFF") + step_ticks(profile, "LAND") + \
        step_ticks(profile, "SCAN") * len(face_slots(manager.twin, task))
    return (flight + steps) * energy.flight_wh_per_tick(profile)


PATROL_LOOP = "patrol_loop"


def _loop_corners(twin: Any, origin: Cell) -> List[Cell]:
    """The loop's corners in route order, starting from the one nearest
    `origin`, and back to it."""
    corners = [tuple(corner) for corner in twin.warehouse.zones[PATROL_LOOP].attributes["route"]]
    start = min(range(len(corners)), key=lambda i: abs(corners[i][0] - origin[0]) + abs(corners[i][1] - origin[1]))
    ordered = corners[start:] + corners[:start]
    return ordered + [ordered[0]]


def _check_patrol(manager: Any, task: Any) -> Optional[str]:
    if PATROL_LOOP not in manager.twin.warehouse.zones:
        return f"The {manager.twin.layout_name} floor has no patrol loop"
    return None


def _patrol_target(planner: Any, task: Any, origin: Cell, profile: Any, layer: str) -> Tuple[Cell, str]:
    return _loop_corners(planner.twin, origin)[0], "the patrol loop"


def _plan_patrol(planner: Any, task: Any, robot: Any, blocked: Set[Cell]) -> Tuple[List[Any], List[Cell], int]:
    """NAVIGATE corner by corner round patrol_loop, then report anomalies."""
    corners = _loop_corners(planner.twin, robot.position)
    actions = [Action(ActionType.NAVIGATE, f"Patrol to ({x},{y})", (x, y), "patrol loop") for x, y in corners]
    actions.append(Action(ActionType.WAIT, "Report anomalies", corners[-1], "patrol loop", params={"report": True}))
    return actions, corners, 0


JOB_SPECS[TaskType.CYCLE_COUNT] = JobSpec(
    label="Cycle count", guide="face(a pallet rack cell, x,y)(a drone counts each level)",
    classes=frozenset({"DRONE"}), check=_check_count, plan=_plan_count, target=_count_target, air=True,
    mission_wh=_count_wh)
JOB_SPECS[TaskType.PATROL] = JobSpec(
    label="Patrol", guide="(a scout goes round patrol_loop and reports anomalies)",
    classes=frozenset({"SCOUT"}), check=_check_patrol, plan=_plan_patrol, target=_patrol_target)

```

- [ ] **Step 6: The drone's hard energy gate**

**Replace in** `backend/task_manager.py`:

```python
from .eligibility import (
    agent_eligibility, box_kind_ok, cert_scope_ok, operator_eligibility, payload_ok, reach_ok,
    robot_eligibility,
)
from .embodiment import AIR, GROUND
from .energy import charger_zone
from .jobs import JOB_PARAM_KEYS, JOB_SPECS, job_levels
```

with:

```python
from .eligibility import (
    agent_eligibility, box_kind_ok, cert_scope_ok, drone_round_trip_ok, operator_eligibility, payload_ok,
    reach_ok, robot_eligibility,
)
from .embodiment import AIR, GROUND
from .energy import charger_zone, energy_wh
from .jobs import JOB_PARAM_KEYS, JOB_SPECS, job_levels
```

**Replace in** `backend/task_manager.py`:

```python
        ok, reason = people.supervision_available(self.twin, robot)
        if not ok:
            return f"can't work unsupervised: {reason}"
        return None

    def physical_recheck(self, robot: Any, task: Task) -> Optional[str]:
```

with:

```python
        ok, reason = people.supervision_available(self.twin, robot)
        if not ok:
            return f"can't work unsupervised: {reason}"
        if spec is not None and spec.mission_wh is not None and profile.battery is not None:
            # A drone's hard energy gate (spec §10.1): it can't detour to a charger mid-flight.
            needed = spec.mission_wh(self, task, robot)
            if needed is not None:
                ok, reason = drone_round_trip_ok(energy_wh(robot), needed, profile.battery.capacity_wh)
                if not ok:
                    return f"can't fly it: {reason}"
        return None

    def physical_recheck(self, robot: Any, task: Task) -> Optional[str]:
```

- [ ] **Step 7: Run the new tests**

Run: `.venv/bin/python -m pytest -o addopts="" -q backend/test_drone_jobs.py`
Expected: `5 passed`.

- [ ] **Step 8: Run the full suite**

Run: `.venv/bin/python -m pytest -o addopts="" -q`
Expected: `2 failed, 575 passed`.

- [ ] **Step 9: Commit**

```bash
git add backend/models.py backend/simulator.py backend/jobs.py backend/task_manager.py \
        backend/test_drone_jobs.py
git commit -m "feat: drone cycle counts with variances, and scout patrols

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 12: The shift engine and order chains

`backend/operations/` gives the floor its own work (spec §11.1–§11.2, §11.4). `ShiftEngine` (owned by the twin, `twin.shift`) runs seeded streams at per-hour rates × `pace` — inbound trucks (every 20 sim-min, alternating dock_1/dock_2, 4–8 pallets of 150–900 kg), customer orders (30/h, 1–4 lines × 1–3 units to dock_4 or dock_5), full-pallet orders (4/h), cycle counts (a rack face every 10 sim-min, round-robin over the 36 faces), patrols (every 15 sim-min), returns (6/h, a tote turns up in returns_qc) and departures (dock_3/4/5 every 30 sim-min). Its own `random.Random(seed)` makes the same seed and ticks give the same orders and jobs; `MISDECLARED_WEIGHT_RISK` lands here. The engine starts paused; `start()`/`pause()` control generation (orders in flight carry on), `configure(pace, seed, rates)` changes it (a new seed resets the RNG) — plan 1c's `/api/shift` calls these. `OrderBook` runs each order's fixed chain through the gate (`create_task(internal=True)`), advancing a job stage when its task completes and the conveyor stages on hand-off events; a failed or rejected stage is retried once after 10 s, then the order fails with the gate's or task's reason. A customer order holds one pick station and one pack cell while it needs them, so one arm's items never queue behind another order's. The shift clock reads `SHIFT_START_HOUR` (06:00) plus the simulated time.

**Files:**
- Create: `backend/operations/__init__.py`, `backend/operations/orders.py`, `backend/operations/shift.py`
- Modify: `backend/models.py` (`SHIFT_START_HOUR`; `EventType.ORDER_CREATED`, `ORDER_STAGE_ADVANCED`, `ORDER_COMPLETED`, `ORDER_FAILED`, `SHIFT_STARTED`, `SHIFT_PAUSED`)
- Modify: `backend/digital_twin.py` (`twin.shift`, reset with the twin)
- Modify: `backend/simulator.py` (the engine ticks before dispatch)
- Test: `backend/test_shift.py` (create)

**Interfaces:**
- Consumes (Tasks 2, 3, 7–11): `add_box(kind=...)`, `twin.stock.locations/skus`, `jobs.tote_is_home`, `twin.equipment.ship/release_order`, the `HANDOFF` event data, every new job type's payload, `twin.faults.roll("misdeclared_weight", rng=)`, `TaskManager.create_task/cancel_task/active_task_for_box`.
- Produces:
  - `CONFIG["SHIFT_START_HOUR"] = 6`; the six order and shift `EventType`s (category `OPERATIONS`; data carries `order_id`, and `kind`, `stage`, `task_id`, `reason` as fitting).
  - `backend.operations.orders`: `ORDER_KINDS = ("CUSTOMER", "INBOUND", "PALLET", "COUNT", "RETURN")`, `ORDER_RETRY_DELAY_S = 10.0`, `PICK_STATION_PEOPLE = ("E-10001", "E-10002")`; `Stage(name, task_type, payload, after, status, task_id, attempts, retry_at, error, line)`; `Order(order_id, kind, lines, status, stages, current_stage, failure_reason, created_at, completed_at, lane, pick_station, pack_cell, sorted_to, lost)` with `.is_terminal`, `.to_dict()`; statuses `OPEN`, `IN_PROGRESS`, `DONE`, `FAILED`; `OrderBook(twin)` with `.customer(lines, lane)`, `.inbound(dock, pallet_ids)`, `.pallet(box_id)`, `.count(face)`, `.returned(box_id)` (each `-> Order`), `.advance()`, `.on_event(event)`, `.list(status=None, kind=None, limit=100)`, `.counts()`.
  - `backend.operations.shift`: `DEFAULT_RATES`, `FIRST_AT_S`, `shift_hour(simulation_time) -> int`, `shift_clock(simulation_time) -> "HH:MM"`; `ShiftEngine(twin, seed=42, pace=1.0)` with `.status` (`RUNNING`/`PAUSED`), `.orders`, `.rng`, `.counters`, `.start()`, `.pause()`, `.configure(pace=None, seed=None, rates=None) -> config`, `.config()`, `.clock()`, `.status_dict()`, `.reset()`, `.tick()`.
  - `DigitalTwin.shift: ShiftEngine`.

- [ ] **Step 1: Write the failing tests**

**Create** `backend/test_shift.py`:

```python
"""The shift engine and its order chains (multi-embodiment spec §11.1, §11.2,
§11.4): seeded generators make the work, each order runs its fixed chain of
jobs through the gate, a failed stage is retried once, and the shift clock
reads from 06:00."""
import pytest

from backend import people
from backend.digital_twin import DigitalTwin
from backend.models import BoxKind, BoxStatus, CONFIG, SimulationStatus, TaskStatus
from backend.operations import ORDER_KINDS, shift_clock, shift_hour
from backend.operations.orders import ORDER_RETRY_DELAY_S
from backend.simulator import Simulator


def make_twin(tmp_path, layout="distribution_center"):
    return DigitalTwin(log_dir=str(tmp_path / "logs"), data_dir=str(tmp_path / "data"),
                       persist_logs=False, demo=True, demo_tasks=False, layout=layout)


def populate(twin):
    """A working floor: forklifts, a hauler, AMRs, the picker, both arms, a
    drone and the scout; totes of six SKUs and a few stored pallets."""
    for name, asset, cell in (("PF1200-205", "AST-000205", (7, 4)), ("PF1200-206", "AST-000206", (7, 8)),
                              ("HH300-207", "AST-000207", (2, 8)), ("TR50-201", "AST-000201", (8, 13)),
                              ("TR50-101", "AST-000101", (8, 15)), ("PK30-203", "AST-000203", (20, 14)),
                              ("IX2-208", "AST-000208", (20, 2)), ("SC1-204", "AST-000204", (7, 6))):
        twin.add_robot(name=name, asset_id=asset, position=cell)
    twin.add_robot(name="CX10-210", asset_id="AST-000210")
    twin.add_robot(name="CX10-211", asset_id="AST-000211")
    for number in range(6):
        twin.add_box(name=f"TOTE-{number}", kind="TOTE", sku=f"SKU-00{number + 1}", quantity=20, weight=21.5,
                     slot=f"TS-{8 + number:02d}-12-0")
    for number in range(3):
        twin.add_box(name=f"PAL-{number}", kind="PALLET", sku=f"SKU-00{number + 1}", quantity=40, weight=500.0,
                     slot=f"PR-{10 + number:02d}-05-0")


@pytest.fixture
def twin(tmp_path):
    twin = make_twin(tmp_path)
    populate(twin)
    return twin


@pytest.fixture
def sim(twin):
    simulator = Simulator(twin)
    twin.simulation_status = SimulationStatus.RUNNING
    return simulator


def run(sim, order, max_ticks=4000):
    for _ in range(max_ticks):
        if order.is_terminal:
            return
        sim.tick()
    assert order.is_terminal, (order.order_id, order.status, [(s.name, s.status, s.error) for s in order.stages])


def generate(twin, seconds):
    """Run only the engine, second by second: what it creates, not what robots do."""
    for second in range(int(seconds) + 1):
        twin.simulation_time = float(second)
        twin.shift.tick()


def test_the_shift_starts_paused_and_is_controlled(twin, tmp_path):
    engine = twin.shift
    assert engine.status == "PAUSED" and engine.config() == {"seed": 42, "pace": 1.0, "rates": engine.rates}
    generate(twin, 60)
    assert not engine.orders.orders                              # paused: nothing generated
    engine.start()
    engine.pause()
    assert [e["event"] for e in twin.events.query() if e["event"].startswith("SHIFT_")] == \
        ["SHIFT_STARTED", "SHIFT_PAUSED"]
    with pytest.raises(ValueError, match="pace"):
        engine.configure(pace=0)
    with pytest.raises(ValueError, match="Unknown rate"):
        engine.configure(rates={"aliens": 1})
    assert engine.configure(pace=2, rates={"patrols": 0})["pace"] == 2.0
    classic = make_twin(tmp_path / "classic", layout="classic")
    with pytest.raises(ValueError, match="no shift engine"):
        classic.shift.start()


def test_the_shift_clock_runs_from_six(twin):
    assert CONFIG["SHIFT_START_HOUR"] == 6
    assert (shift_clock(0), shift_clock(90 * 60), shift_clock(20 * 3600 + 59)) == ("06:00", "07:30", "02:00")
    assert (shift_hour(0), shift_hour(18 * 3600)) == (6, 0)
    twin.simulation_time = 3600.0
    assert twin.shift.clock() == "07:00" and twin.shift.status_dict()["clock"] == "07:00"


def test_the_same_seed_makes_the_same_work(tmp_path):
    def work(path, seed):
        twin = make_twin(path)
        populate(twin)
        twin.shift.configure(seed=seed, pace=10)
        twin.shift.start()
        generate(twin, 400)
        orders = [(o.kind, o.lines, o.lane) for o in twin.shift.orders.orders.values()]
        tasks = [(t.type.value, t.box_id, t.params, t.status.value) for t in twin.tasks.tasks.values()]
        return orders, tasks

    first, second = work(tmp_path / "a", 42), work(tmp_path / "b", 42)
    assert first == second and len(first[0]) > 10
    assert work(tmp_path / "c", 7)[0] != first[0]


def test_every_kind_of_work_is_generated(twin):
    twin.shift.configure(pace=10)
    twin.shift.start()
    generate(twin, 200)
    kinds = {order.kind for order in twin.shift.orders.orders.values()}
    assert kinds == set(ORDER_KINDS)
    assert any(task.type.value == "PATROL" for task in twin.tasks.tasks.values())
    inbound = next(o for o in twin.shift.orders.orders.values() if o.kind == "INBOUND")
    pallets = [twin.find_box(line["box_id"]) for line in inbound.lines]
    assert 4 <= len(pallets) <= 8 and all(150 <= p.declared_weight_kg <= 900 for p in pallets)
    assert all(twin.warehouse.zone_of_cell(p.position).key == "dock_1" for p in pallets)
    counts = [o.lines[0]["face"] for o in twin.shift.orders.orders.values() if o.kind == "COUNT"]
    assert counts[:2] == ["8,2", "9,2"]                           # round-robin over the rack faces
    customer = next(o for o in twin.shift.orders.orders.values() if o.kind == "CUSTOMER")
    assert 1 <= len(customer.lines) <= 4 and customer.lane in ("dock_4", "dock_5")
    assert all(1 <= line["units"] <= 3 for line in customer.lines)


def test_a_misdeclared_pallet_is_heavier_than_its_paperwork(twin):
    twin.faults.arm("misdeclared_weight")
    twin.shift._generate_trucks()
    order = next(iter(twin.shift.orders.orders.values()))
    first = twin.find_box(order.lines[0]["box_id"])
    assert 1.1 <= first.true_weight_kg / first.declared_weight_kg <= 1.6
    second = twin.find_box(order.lines[1]["box_id"])
    assert second.true_weight_kg == second.declared_weight_kg


def test_a_customer_order_runs_its_whole_chain(twin, sim):
    order = twin.shift.orders.customer([{"sku": "SKU-002", "units": 2}], "dock_5")
    run(sim, order)
    assert order.status == "DONE", (order.failure_reason, [(s.name, s.status, s.error) for s in order.stages])
    assert (order.pick_station, order.pack_cell, order.sorted_to) == ("pick_station_1", "pack_cell_1", "dock_5")
    assert [s.name for s in order.stages] == ["TOTE_TO_STATION line 1", "PICK line 1", "RETURN_TOTE line 1",
                                              "PACK_ORDER", "SORT"]
    tote = twin.find_box("TOTE-1")
    assert tote.position == (9, 12) and tote.quantity == 18          # picked from, and home again
    carton = next(box for box in twin.boxes.values() if box.kind is BoxKind.CARTON)
    assert carton.order_id == order.order_id and twin.warehouse.zone_of_cell(carton.position).key == "dock_5"
    advanced = [e["data"] for e in twin.events.query(event_type="ORDER_STAGE_ADVANCED")]
    assert sum(1 for data in advanced if data.get("done")) == 5
    assert twin.events.query(event_type="ORDER_COMPLETED")[-1]["data"]["order_id"] == order.order_id


def test_an_order_goes_to_pick_two_when_a_picker_is_on_the_floor(tmp_path):
    twin = make_twin(tmp_path)
    twin.add_robot(name="CX10-210", asset_id="AST-000210")
    sam = twin.add_operator(name="Sam", worker_id="E-10001")
    first = twin.shift.orders.customer([{"sku": "SKU-001", "units": 1}], "dock_4")
    twin.shift.orders.advance()
    assert first.status == "OPEN"                                   # no picker robot, nobody at Pick 2
    people.place(twin, sam, "pick_station_2")
    twin.shift.orders._acquire(first)
    assert (first.pick_station, first.pack_cell) == ("pick_station_2", "pack_cell_1")


def test_an_inbound_truck_is_unloaded_and_put_away(twin, sim):
    pallets = [twin.add_box(name=f"IN-{n}", kind="PALLET", sku="SKU-009", quantity=30, weight=w, position=cell)
               for n, (w, cell) in enumerate(((280.0, (2, 3)), (700.0, (2, 4))))]
    order = twin.shift.orders.inbound("dock_1", [box.id for box in pallets])
    run(sim, order, max_ticks=5000)
    assert order.status == "DONE", order.failure_reason
    assert all(box.slot and box.status is BoxStatus.STORED for box in pallets)
    assert [s.status for s in order.stages] == ["DONE"] * 4


def test_a_pallet_order_ships_a_stored_pallet(twin, sim):
    order = twin.shift.orders.pallet(twin.find_box("PAL-0").id)
    run(sim, order)
    assert order.status == "DONE", order.failure_reason
    assert twin.find_box("PAL-0").status is BoxStatus.SHIPPED


def test_a_rejected_stage_is_retried_once_then_the_order_fails(tmp_path):
    twin = make_twin(tmp_path)
    twin.add_robot(name="TR50-201", asset_id="AST-000201", position=(8, 13))
    order = twin.shift.orders.count("12,2")
    twin.shift.orders.advance()
    stage = order.stages[0]
    assert (order.status, stage.status, stage.attempts) == ("IN_PROGRESS", "WAITING", 1)
    assert stage.error.startswith("No robot can do this CYCLE_COUNT")
    twin.simulation_time += ORDER_RETRY_DELAY_S
    twin.shift.orders.advance()
    assert order.status == "FAILED" and order.failure_reason == stage.error and stage.attempts == 2
    failed = twin.events.query(event_type="ORDER_FAILED")[-1]["data"]
    assert failed["order_id"] == order.order_id and failed["kind"] == "COUNT"


def test_an_item_lost_at_a_hand_off_fails_its_order(twin, sim):
    order = twin.shift.orders.customer([{"sku": "SKU-003", "units": 1}], "dock_4")
    twin.faults.arm("handoff_loss")
    run(sim, order)
    assert order.status == "FAILED" and "was lost at a hand-off" in order.failure_reason
    pack = twin.tasks.get(order.stages[3].task_id)
    assert pack.status is TaskStatus.CANCELLED                     # the arm stops waiting for it


def test_departures_ship_what_reached_the_docks(twin):
    carton = twin.add_box(name="CTN-1", kind="CARTON", weight=2.0, position=(29, 13))
    carton.set_status(BoxStatus.DELIVERED)
    twin.shift._generate_departures()
    assert carton.status is BoxStatus.SHIPPED
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/bin/python -m pytest -o addopts="" -q backend/test_shift.py`
Expected: `1 error` — `ModuleNotFoundError: No module named 'backend.operations'`.

- [ ] **Step 3: Orders and their chains**

**Create** `backend/operations/orders.py`:

```python
"""Orders and their job chains (multi-embodiment spec §11.2).

An Order is a fixed chain of stages for its kind:

    CUSTOMER  per line: TOTE_TO_STATION → PICK_ITEMS or MANUAL_PICK → RETURN_TOTE;
              then PACK_ORDER, and SORT (the carton reaches its lane's dock)
    INBOUND   per pallet: UNLOAD_TRUCK → PUTAWAY_PALLET
    PALLET    RETRIEVE_PALLET → LOAD_TRUCK
    COUNT     CYCLE_COUNT
    RETURN    RETURNS_PUTAWAY

A job stage creates its task once the stages it follows are done — through
twin.tasks.create_task(internal=True), so the gate always applies — and is
done when that task completes. The conveyor stages advance on hand-off
events instead: SORT is done when the sorter hands the order's carton to a
dock, and an order item lost at a hand-off fails the order. A failed or
rejected stage is retried once, ORDER_RETRY_DELAY_S later; after that the
order FAILS with the task's failure or rejection reason.

A customer order needs a pick station and a pack cell to itself: it waits,
OPEN, until both are free, so items for one arm never queue behind another
order's on the line. Its PACK_ORDER starts with it, so the arm is waiting
when the first item arrives. Pick 2 is a person's station: an order goes
there when someone who picks there is on the floor and Pick 1 is taken or
has no picker robot.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from ..jobs import tote_is_home
from ..models import BoxKind, EventType, LogCategory, LogLevel, OperatorStatus, TaskStatus

ORDER_KINDS = ("CUSTOMER", "INBOUND", "PALLET", "COUNT", "RETURN")
OPEN, IN_PROGRESS, DONE, FAILED = "OPEN", "IN_PROGRESS", "DONE", "FAILED"
WAITING, ACTIVE = "WAITING", "ACTIVE"

#: Seconds before a failed or rejected stage is tried once more.
ORDER_RETRY_DELAY_S = 10.0
#: The workers who pick at Pick 2 (the demo workforce's Sam and Lee).
PICK_STATION_PEOPLE = ("E-10001", "E-10002")
PICK_STATIONS = ("pick_station_1", "pick_station_2")
PACK_CELLS = ("pack_cell_1", "pack_cell_2")


class OrderError(Exception):
    """A stage can't be built: the reason fails or retries it."""


@dataclass
class Stage:
    name: str
    task_type: Optional[str]          # None for SORT, which the conveyor finishes
    payload: Dict[str, Any] = field(default_factory=dict)
    after: List[int] = field(default_factory=list)
    status: str = WAITING
    task_id: Optional[str] = None
    attempts: int = 0
    retry_at: Optional[float] = None  # simulation time
    error: Optional[str] = None
    line: Optional[int] = None        # which order line a customer stage serves

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Order:
    order_id: str
    kind: str
    lines: List[Dict[str, Any]] = field(default_factory=list)
    status: str = OPEN
    stages: List[Stage] = field(default_factory=list)
    current_stage: int = 0
    failure_reason: Optional[str] = None
    created_at: float = 0.0
    completed_at: Optional[float] = None
    lane: Optional[str] = None
    pick_station: Optional[str] = None
    pack_cell: Optional[str] = None
    sorted_to: Optional[str] = None
    lost: List[str] = field(default_factory=list)

    @property
    def is_terminal(self) -> bool:
        return self.status in (DONE, FAILED)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["stages"] = [stage.to_dict() for stage in self.stages]
        return data


class OrderBook:
    """Every order of one twin, advanced once a tick by the shift engine."""

    def __init__(self, twin: Any) -> None:
        self.twin = twin
        self.orders: Dict[str, Order] = {}
        self._seq = 0

    # ---- creating orders ------------------------------------------------- #
    def _new(self, kind: str, stages: List[Stage], **fields: Any) -> Order:
        self._seq += 1
        order = Order(f"ORD-{self._seq:04d}", kind, stages=stages, created_at=self.twin.simulation_time, **fields)
        self.orders[order.order_id] = order
        self.twin.events.emit(
            EventType.ORDER_CREATED,
            f"{order.order_id} created: {kind.lower()} order, {len(stages)} stage(s)",
            category=LogCategory.OPERATIONS,
            data={"order_id": order.order_id, "kind": kind, "lines": order.lines, "lane": order.lane,
                  "stages": [stage.name for stage in stages]},
        )
        return order

    def customer(self, lines: List[Dict[str, Any]], lane: str) -> Order:
        """`lines` is [{"sku", "units"}]: per line a tote comes to a pick
        station, units are picked onto the line and the tote goes home; then
        the order is packed and sorted to `lane`."""
        stages: List[Stage] = []
        previous_pick: Optional[int] = None
        for number, line in enumerate(lines):
            tote = len(stages)
            stages.append(Stage(f"TOTE_TO_STATION line {number + 1}", "TOTE_TO_STATION",
                                after=[previous_pick] if previous_pick is not None else [], line=number))
            stages.append(Stage(f"PICK line {number + 1}", "PICK", after=[tote], line=number))
            previous_pick = len(stages) - 1
            stages.append(Stage(f"RETURN_TOTE line {number + 1}", "RETURN_TOTE", after=[previous_pick], line=number))
        pack = len(stages)
        stages.append(Stage("PACK_ORDER", "PACK_ORDER"))
        stages.append(Stage("SORT", None, after=[pack]))
        return self._new("CUSTOMER", stages, lines=[dict(line) for line in lines], lane=lane)

    def inbound(self, dock: str, pallet_ids: List[str]) -> Order:
        """A truck at `dock`: each pallet unloaded to intake, then put away."""
        stages: List[Stage] = []
        for box_id in pallet_ids:
            stages.append(Stage(f"UNLOAD_TRUCK {box_id}", "UNLOAD_TRUCK", {"box_id": box_id}))
            stages.append(Stage(f"PUTAWAY_PALLET {box_id}", "PUTAWAY_PALLET", {"box_id": box_id},
                                after=[len(stages) - 1]))
        return self._new("INBOUND", stages, lines=[{"box_id": box_id, "dock": dock} for box_id in pallet_ids])

    def pallet(self, box_id: str) -> Order:
        """A full pallet out of the racks and onto the outbound truck."""
        return self._new("PALLET", [Stage("RETRIEVE_PALLET", "RETRIEVE_PALLET", {"box_id": box_id}),
                                    Stage("LOAD_TRUCK", "LOAD_TRUCK", {"box_id": box_id}, after=[0])],
                         lines=[{"box_id": box_id}])

    def count(self, face: str) -> Order:
        return self._new("COUNT", [Stage("CYCLE_COUNT", "CYCLE_COUNT", {"face": face})], lines=[{"face": face}])

    def returned(self, box_id: str) -> Order:
        return self._new("RETURN", [Stage("RETURNS_PUTAWAY", "RETURNS_PUTAWAY", {"box_id": box_id})],
                         lines=[{"box_id": box_id}])

    # ---- hand-off events (ShiftEngine passes every event on) ------------- #
    def on_event(self, event: Dict[str, Any]) -> None:
        if event.get("event") != EventType.HANDOFF.value:
            return
        data = event.get("data") or {}
        order = self.orders.get(data.get("order_id"))
        if order is None or order.is_terminal:
            return
        if data.get("from") == "sorter":
            order.sorted_to = data.get("to")
        elif data.get("receiver_observed", {}).get("present") is False or data.get("to") == "sorter_reject":
            order.lost.append(data.get("box_id"))

    # ---- advancing ------------------------------------------------------- #
    def advance(self) -> None:
        for order in list(self.orders.values()):
            if not order.is_terminal:
                self._advance(order)

    def _advance(self, order: Order) -> None:
        if order.lost:
            self._fail(order, f"item {order.lost[0]} was lost at a hand-off")
            return
        if order.kind == "CUSTOMER" and order.status == OPEN and not self._acquire(order):
            return
        if order.status == OPEN:
            order.status = IN_PROGRESS
        for index, stage in enumerate(order.stages):
            if order.is_terminal:
                return
            if stage.status == ACTIVE:
                self._watch(order, index, stage)
            elif stage.status == WAITING and all(order.stages[i].status == DONE for i in stage.after):
                if stage.retry_at is None or self.twin.simulation_time >= stage.retry_at:
                    self._start(order, index, stage)
        if not order.is_terminal and all(stage.status == DONE for stage in order.stages):
            order.status = DONE
            order.completed_at = self.twin.simulation_time
            self.twin.events.emit(
                EventType.ORDER_COMPLETED,
                f"{order.order_id} completed ({order.kind.lower()})",
                category=LogCategory.OPERATIONS,
                data={"order_id": order.order_id, "kind": order.kind, "sorted_to": order.sorted_to,
                      "seconds": round(order.completed_at - order.created_at, 1)},
            )

    def _watch(self, order: Order, index: int, stage: Stage) -> None:
        if stage.task_type is None:  # SORT: done when the sorter hands the carton to a dock
            if order.sorted_to is not None:
                self._done(order, index, stage)
            return
        task = self.twin.tasks.get(stage.task_id)
        if task is None or task.status in (TaskStatus.FAILED, TaskStatus.CANCELLED):
            self._stage_failed(order, stage, task.error if task is not None and task.error else
                               f"{stage.task_id} was {task.status.value.lower() if task else 'lost'}")
        elif task.status is TaskStatus.COMPLETED:
            self._done(order, index, stage)

    def _start(self, order: Order, index: int, stage: Stage) -> None:
        if stage.task_type is None:
            stage.status = ACTIVE
            return
        try:
            payload = self._payload(order, stage)
        except OrderError as exc:
            stage.attempts += 1
            self._stage_failed(order, stage, str(exc))
            return
        if payload is None:
            return  # what it needs is busy (a tote at another station): try again next tick
        stage.attempts += 1
        task = self.twin.tasks.create_task(payload, internal=True)
        stage.task_id = task.id
        if task.status is TaskStatus.FAILED:  # the gate rejected it
            self._stage_failed(order, stage, task.error or "rejected")
            return
        stage.status = ACTIVE
        order.current_stage = index
        self.twin.events.emit(
            EventType.ORDER_STAGE_ADVANCED,
            f"{order.order_id}: {stage.name} started as {task.id}",
            category=LogCategory.OPERATIONS,
            task_id=task.id,
            data={"order_id": order.order_id, "stage": stage.name, "index": index, "task_id": task.id,
                  "attempt": stage.attempts},
        )

    def _done(self, order: Order, index: int, stage: Stage) -> None:
        stage.status = DONE
        self.twin.events.emit(
            EventType.ORDER_STAGE_ADVANCED,
            f"{order.order_id}: {stage.name} done",
            category=LogCategory.OPERATIONS,
            task_id=stage.task_id,
            data={"order_id": order.order_id, "stage": stage.name, "index": index, "task_id": stage.task_id,
                  "done": True},
        )

    def _stage_failed(self, order: Order, stage: Stage, reason: str) -> None:
        stage.error = reason
        if stage.attempts < 2:
            stage.status, stage.task_id = WAITING, None
            stage.retry_at = self.twin.simulation_time + ORDER_RETRY_DELAY_S
            self.twin.logger.warning(LogCategory.OPERATIONS, f"{order.order_id}: {stage.name} failed ({reason}) "
                                     f"— retrying in {ORDER_RETRY_DELAY_S:.0f} s")
            return
        stage.status = FAILED
        self._fail(order, reason)

    def _fail(self, order: Order, reason: str) -> None:
        order.status, order.failure_reason = FAILED, reason
        order.completed_at = self.twin.simulation_time
        for stage in order.stages:
            task = self.twin.tasks.get(stage.task_id) if stage.task_id else None
            if task is not None and not task.is_terminal:
                self.twin.tasks.cancel_task(task.id)
        if self.twin.equipment is not None:
            self.twin.equipment.release_order(order.order_id)
        self.twin.events.emit(
            EventType.ORDER_FAILED,
            f"{order.order_id} failed: {reason}",
            category=LogCategory.OPERATIONS,
            level=LogLevel.WARNING,
            data={"order_id": order.order_id, "kind": order.kind, "reason": reason},
        )

    # ---- what each stage asks for ---------------------------------------- #
    def _payload(self, order: Order, stage: Stage) -> Optional[Dict[str, Any]]:
        if order.kind != "CUSTOMER":
            return {"type": stage.task_type, **stage.payload}
        if stage.task_type == "PACK_ORDER":
            units = sum(int(line["units"]) for line in order.lines)
            return {"type": "PACK_ORDER", "order_id": order.order_id, "quantity": units,
                    "pack_cell": order.pack_cell, "lane": order.lane}
        line = order.lines[stage.line]
        if stage.task_type == "TOTE_TO_STATION":
            tote = self._choose_tote(line["sku"], int(line["units"]))
            if tote is None:
                return None
            line["tote_id"] = tote.id
            return {"type": "TOTE_TO_STATION", "box_id": tote.id, "station": order.pick_station}
        if stage.task_type == "PICK":
            kind = "PICK_ITEMS" if order.pick_station == PICK_STATIONS[0] else "MANUAL_PICK"
            return {"type": kind, "box_id": line["tote_id"], "quantity": int(line["units"]),
                    "order_id": order.order_id, "pack_cell": order.pack_cell, "station": order.pick_station}
        return {"type": "RETURN_TOTE", "box_id": line["tote_id"]}

    def _choose_tote(self, sku: str, units: int) -> Optional[Any]:
        """A tote of `sku` holding `units`, home in its slot and wanted by no
        job right now; None while every such tote is busy."""
        twin = self.twin
        stocked = [twin.find_box(location.box_id) for location in twin.stock.locations(sku)
                   if location.recorded_qty >= units]
        stocked = [box for box in stocked if box is not None and box.kind is BoxKind.TOTE]
        if not stocked:
            raise OrderError(f"no tote holds {units} of {sku}")
        free = [box for box in stocked if tote_is_home(twin, box) and twin.tasks.active_task_for_box(box.id) is None
                and not self._promised_tote(box.id)]
        return free[0] if free else None

    def _promised_tote(self, box_id: str) -> bool:
        return any(line.get("tote_id") == box_id for order in self.orders.values() if not order.is_terminal
                   for line in order.lines if order.kind == "CUSTOMER")

    # ---- a customer order's station and pack cell ------------------------ #
    def _busy(self, attribute: str) -> List[str]:
        """The pick stations (or pack cells) running orders still need: a
        station until the order's last line is picked and its tote is on its
        way home, a pack cell until its carton is on the line."""
        busy = []
        for order in self.orders.values():
            if order.kind != "CUSTOMER" or order.status != IN_PROGRESS:
                continue
            if attribute == "pick_station":
                needed = any(stage.status != DONE for stage in order.stages if stage.line is not None)
            else:
                needed = any(stage.status != DONE for stage in order.stages if stage.task_type == "PACK_ORDER")
            if needed:
                busy.append(getattr(order, attribute))
        return busy

    def _acquire(self, order: Order) -> bool:
        """Claim a free pick station and pack cell for `order`, or wait."""
        twin = self.twin
        arms = {twin.warehouse.fixed_stations.get(robot.position) for robot in twin.robots.values()
                if robot.mobility is not None and robot.mobility.is_fixed and not robot.is_halted}
        packs = [cell for cell in PACK_CELLS if cell in arms and cell not in self._busy("pack_cell")]
        stations = [station for station in PICK_STATIONS if station not in self._busy("pick_station")]
        picker = any(robot.mobility is not None and robot.mobility.embodiment_class == "PICKER"
                     and not robot.is_halted for robot in twin.robots.values())
        people_on = any(o.worker_id in PICK_STATION_PEOPLE and o.status != OperatorStatus.OFF_DUTY
                        and o.zone is not None for o in twin.operators.values())
        usable = [s for s in stations if (s == PICK_STATIONS[0] and picker) or (s == PICK_STATIONS[1] and people_on)]
        if not packs or not usable:
            return False
        order.pick_station, order.pack_cell = usable[0], packs[0]
        self.twin.logger.info(LogCategory.OPERATIONS, f"{order.order_id} picks at {order.pick_station} and "
                              f"packs at {order.pack_cell}", data={"order_id": order.order_id})
        return True

    # ---- reads ----------------------------------------------------------- #
    def list(self, status: Optional[str] = None, kind: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        orders = sorted(self.orders.values(), key=lambda o: o.order_id, reverse=True)
        orders = [o for o in orders if (status is None or o.status == status.upper())
                  and (kind is None or o.kind == kind.upper())]
        return [o.to_dict() for o in orders[:limit]]

    def counts(self) -> Dict[str, Dict[str, int]]:
        out: Dict[str, Dict[str, int]] = {kind: {OPEN: 0, IN_PROGRESS: 0, DONE: 0, FAILED: 0} for kind in ORDER_KINDS}
        for order in self.orders.values():
            out[order.kind][order.status] += 1
        return out
```

- [ ] **Step 4: The engine and the shift clock**

**Create** `backend/operations/shift.py`:

```python
"""The shift engine (multi-embodiment spec §11.1, §11.4): it keeps the
distribution-centre floor busy on its own.

Seeded streams generate the work — inbound trucks, customer orders, full-
pallet orders, cycle counts, patrols, returns and outbound departures — at
per-hour rates × `pace`, each turning into an order chain (orders.py) or a
job created through the gate (twin.tasks.create_task, internal=True). The
engine has its own random.Random(seed), so the same seed and ticks give the
same orders and jobs. It starts PAUSED; start() begins generating and pause()
stops it, while orders already in flight carry on. The shift clock runs from
SHIFT_START_HOUR with the simulation time. Its HTTP controls are plan 1c's.
"""
from __future__ import annotations

import random
from typing import Any, Dict, List, Optional

from ..models import CONFIG, BoxKind, BoxStatus, EventType, LogCategory
from .orders import OrderBook

#: Generated orders and jobs per simulated hour, before × pace (spec §11.1).
DEFAULT_RATES: Dict[str, float] = {
    "trucks": 3.0,            # every 20 sim-min
    "customer_orders": 30.0,
    "pallet_orders": 4.0,
    "cycle_counts": 6.0,      # one rack face every 10 sim-min
    "patrols": 4.0,           # every 15 sim-min
    "returns": 6.0,
    "departures": 2.0,        # each outbound dock every 30 sim-min
}
#: Seconds into the shift each stream first fires (so a demo starts at once;
#: departures wait a full interval, for something to have reached the docks).
FIRST_AT_S: Dict[str, float] = {
    "customer_orders": 10.0, "trucks": 30.0, "cycle_counts": 60.0, "patrols": 90.0,
    "pallet_orders": 120.0, "returns": 150.0, "departures": 1800.0,
}
INBOUND_DOCKS = ("dock_1", "dock_2")
OUTBOUND_DOCKS = ("dock_3", "dock_4", "dock_5")
CARTON_LANES = ("dock_4", "dock_5")
#: The SKUs inbound pallets and returns carry when the floor stocks none yet.
DEFAULT_SKUS = tuple(f"SKU-{number:03d}" for number in range(1, 41))


def shift_hour(simulation_time: float) -> int:
    """The shift clock's hour: SHIFT_START_HOUR plus the simulated time, wrapping at 24."""
    return int((CONFIG["SHIFT_START_HOUR"] * 3600 + simulation_time) // 3600) % 24


def shift_clock(simulation_time: float) -> str:
    """The shift clock as HH:MM."""
    seconds = int(CONFIG["SHIFT_START_HOUR"] * 3600 + simulation_time) % 86400
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}"


class ShiftEngine:
    RUNNING, PAUSED = "RUNNING", "PAUSED"

    def __init__(self, twin: Any, seed: int = 42, pace: float = 1.0) -> None:
        self.twin = twin
        self.seed = int(seed)
        self.pace = float(pace)
        self.rates: Dict[str, float] = dict(DEFAULT_RATES)
        self.reset()
        twin.events.subscribe(self._on_event)

    def reset(self) -> None:
        """A fresh, paused shift: no orders, the RNG back at its seed."""
        self.status = self.PAUSED
        self.rng = random.Random(self.seed)
        self.orders = OrderBook(self.twin)
        self._next_due: Dict[str, float] = {}
        self._truck_count = 0
        self._face_index = 0
        self.counters: Dict[str, int] = {stream: 0 for stream in DEFAULT_RATES}
        self.counters["skipped_orders"] = 0

    def _on_event(self, event: Dict[str, Any]) -> None:
        self.orders.on_event(event)

    # ---- controls (plan 1c's /api/shift calls these) --------------------- #
    def start(self) -> None:
        if self.twin.layout_name == "classic":
            raise ValueError("The classic floor has no shift engine")
        if self.status == self.RUNNING:
            return
        now = self.twin.simulation_time
        for stream in DEFAULT_RATES:
            self._next_due.setdefault(stream, now + FIRST_AT_S[stream])
        self.status = self.RUNNING
        self.twin.events.emit(EventType.SHIFT_STARTED, f"Shift started at {self.clock()} (seed {self.seed}, "
                              f"pace {self.pace:g})", category=LogCategory.OPERATIONS,
                              data={"clock": self.clock(), "seed": self.seed, "pace": self.pace})

    def pause(self) -> None:
        if self.status == self.PAUSED:
            return
        self.status = self.PAUSED
        self.twin.events.emit(EventType.SHIFT_PAUSED, f"Shift paused at {self.clock()}",
                              category=LogCategory.OPERATIONS, data={"clock": self.clock()})

    def configure(self, pace: Optional[float] = None, seed: Optional[int] = None,
                  rates: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
        """Change the pace, the seed (which resets the RNG) or any rate."""
        if pace is not None:
            if isinstance(pace, bool) or float(pace) <= 0:
                raise ValueError("pace must be greater than zero")
            self.pace = float(pace)
        for stream, rate in (rates or {}).items():
            if stream not in DEFAULT_RATES:
                raise ValueError(f"Unknown rate {stream!r} (known: {sorted(DEFAULT_RATES)})")
            if isinstance(rate, bool) or float(rate) < 0:
                raise ValueError(f"{stream} must be zero or more per hour")
            self.rates[stream] = float(rate)
        if seed is not None:
            if isinstance(seed, bool) or not isinstance(seed, int):
                raise ValueError("seed must be a whole number")
            self.seed = seed
            self.rng = random.Random(seed)
        return self.config()

    def config(self) -> Dict[str, Any]:
        return {"seed": self.seed, "pace": self.pace, "rates": dict(self.rates)}

    def clock(self) -> str:
        return shift_clock(self.twin.simulation_time)

    def status_dict(self) -> Dict[str, Any]:
        hours = max(self.twin.simulation_time / 3600.0, 1e-9)
        counts = self.orders.counts()
        done = sum(kind["DONE"] for kind in counts.values())
        return {"status": self.status, "clock": self.clock(), "config": self.config(),
                "counters": dict(self.counters), "orders": counts,
                "throughput_per_hour": round(done / hours, 1)}

    # ---- the tick -------------------------------------------------------- #
    def tick(self) -> None:
        """Simulator.tick: generate what is due (when running), then move
        every order on."""
        if self.status == self.RUNNING:
            now = self.twin.simulation_time
            for stream in DEFAULT_RATES:
                rate = self.rates[stream] * self.pace
                if rate <= 0 or now < self._next_due[stream]:
                    continue
                self._next_due[stream] += 3600.0 / rate
                self.counters[stream] += 1
                getattr(self, f"_generate_{stream}")()
        self.orders.advance()

    def _skus(self) -> List[str]:
        return self.twin.stock.skus() or list(DEFAULT_SKUS)

    def _free_cells(self, zone: str) -> List[Any]:
        taken = {box.position for box in self.twin.boxes.values() if box.status is not BoxStatus.SHIPPED}
        return [cell for cell in self.twin.warehouse.zones[zone].cells if cell not in taken]

    def _generate_trucks(self) -> None:
        """An inbound truck, alternating dock_1 and dock_2, with 4–8 pallets
        (150–900 kg). A MISDECLARED_WEIGHT fault makes a pallet 1.1–1.6× heavier
        than its paperwork says."""
        dock = INBOUND_DOCKS[self._truck_count % len(INBOUND_DOCKS)]
        self._truck_count += 1
        count = self.rng.randint(4, 8)
        pallets = []
        for cell in self._free_cells(dock)[:count]:
            declared = round(self.rng.uniform(150.0, 900.0), 1)
            true = declared
            if self.twin.faults.roll("misdeclared_weight", rng=self.rng):
                true = round(declared * self.rng.uniform(1.1, 1.6), 1)
            box = self.twin.add_box(kind="PALLET", position=cell, sku=self.rng.choice(self._skus()),
                                    quantity=self.rng.randint(20, 60), weight=declared, true_weight_kg=true)
            pallets.append(box.id)
        if pallets:
            self.orders.inbound(dock, pallets)

    def _generate_customer_orders(self) -> None:
        """1–4 lines of 1–3 units, for SKUs a tote has enough of, to dock_4 or dock_5."""
        stocked: Dict[str, int] = {}
        for location in self.twin.stock.locations():
            box = self.twin.find_box(location.box_id)
            if box is not None and box.kind is BoxKind.TOTE:
                stocked[location.sku] = max(stocked.get(location.sku, 0), location.recorded_qty)
        lines = []
        for _ in range(self.rng.randint(1, 4)):
            units = self.rng.randint(1, 3)
            choices = sorted(sku for sku, quantity in stocked.items() if quantity >= units)
            if choices:
                lines.append({"sku": self.rng.choice(choices), "units": units})
        lane = self.rng.choice(CARTON_LANES)
        if not lines:
            self.counters["skipped_orders"] += 1
            return
        self.orders.customer(lines, lane)

    def _generate_pallet_orders(self) -> None:
        """A stored pallet out to the outbound truck."""
        promised = {line.get("box_id") for order in self.orders.orders.values() if not order.is_terminal
                    for line in order.lines}
        stored = sorted(location.box_id for location in self.twin.stock.locations()
                        if location.box_id not in promised and self._stored_pallet(location.box_id))
        if not stored:
            self.counters["skipped_orders"] += 1
            return
        self.orders.pallet(self.rng.choice(stored))

    def _stored_pallet(self, box_id: str) -> bool:
        box = self.twin.find_box(box_id)
        return (box is not None and box.kind is BoxKind.PALLET and box.status is BoxStatus.STORED
                and box.true_slot is None and self.twin.tasks.active_task_for_box(box.id) is None)

    def _generate_cycle_counts(self) -> None:
        """The next of the 36 pallet rack faces, round-robin."""
        faces = sorted({slot.cell for slot in self.twin.warehouse.slots.values() if slot.kind == "PALLET"},
                       key=lambda cell: (cell[1], cell[0]))
        if not faces:
            return
        x, y = faces[self._face_index % len(faces)]
        self._face_index += 1
        self.orders.count(f"{x},{y}")

    def _generate_patrols(self) -> None:
        task = self.twin.tasks.create_task({"type": "PATROL"}, internal=True)
        if task.status.value == "FAILED":
            self.twin.logger.warning(LogCategory.OPERATIONS, f"Patrol not sent: {task.error}")

    def _generate_returns(self) -> None:
        """A returned tote turns up in returns_qc."""
        cells = self._free_cells("returns_qc")
        if not cells:
            self.counters["skipped_orders"] += 1
            return
        quantity = self.rng.randint(5, 20)
        weight = round(CONFIG["TOTE_TARE_KG"] + quantity * self.rng.uniform(0.2, 1.0), 2)
        box = self.twin.add_box(kind="TOTE", position=self.rng.choice(cells), sku=self.rng.choice(self._skus()),
                                quantity=quantity, weight=weight)
        self.orders.returned(box.id)

    def _generate_departures(self) -> None:
        """The outbound trucks leave dock_3, dock_4 and dock_5: what reached
        each is SHIPPED."""
        if self.twin.equipment is None:
            return
        for dock in OUTBOUND_DOCKS:
            self.twin.equipment.ship(dock)

```

**Create** `backend/operations/__init__.py`:

```python
"""Operations on the distribution-centre floor (multi-embodiment spec §11):
the shift engine that generates the work, and the orders it runs as job chains."""
from __future__ import annotations

from .orders import ORDER_KINDS, Order, OrderBook, Stage
from .shift import DEFAULT_RATES, ShiftEngine, shift_clock, shift_hour

__all__ = ["DEFAULT_RATES", "ORDER_KINDS", "Order", "OrderBook", "ShiftEngine", "Stage", "shift_clock", "shift_hour"]
```

- [ ] **Step 5: The clock setting and the events; the twin owns the engine and the tick runs it**

**Replace in** `backend/models.py`:

```python
    "MANUAL_PICK_S_PER_ITEM": 5.0,
    # Energy on the new floor (backend/energy.py): every Wh used and charged
```

with:

```python
    "MANUAL_PICK_S_PER_ITEM": 5.0,
    # The new floor's shift clock (backend/operations/shift.py) reads this
    # hour at simulation time 0.
    "SHIFT_START_HOUR": 6,
    # Energy on the new floor (backend/energy.py): every Wh used and charged
```

**Replace in** `backend/models.py`:

```python
    # a count disagreed with the record (spec §7.2).
    SCANNED = "SCANNED"
    STOCK_VARIANCE_DETECTED = "STOCK_VARIANCE_DETECTED"


class ActionType(str, enum.Enum):
```

with:

```python
    # a count disagreed with the record (spec §7.2).
    SCANNED = "SCANNED"
    STOCK_VARIANCE_DETECTED = "STOCK_VARIANCE_DETECTED"
    # The shift engine and its orders (backend/operations).
    ORDER_CREATED = "ORDER_CREATED"
    ORDER_STAGE_ADVANCED = "ORDER_STAGE_ADVANCED"
    ORDER_COMPLETED = "ORDER_COMPLETED"
    ORDER_FAILED = "ORDER_FAILED"
    SHIFT_STARTED = "SHIFT_STARTED"
    SHIFT_PAUSED = "SHIFT_PAUSED"


class ActionType(str, enum.Enum):
```

**Replace in** `backend/digital_twin.py`:

```python
from .navigation import NavigationEngine
from .operator import Operator
```

with:

```python
from .navigation import NavigationEngine
from .operations import ShiftEngine
from .operator import Operator
```

**Replace in** `backend/digital_twin.py`:

```python

        self.scheduler = Scheduler(self)

        self.statistics: Dict[str, Any] = {
```

with:

```python

        self.scheduler = Scheduler(self)
        # The shift engine and its orders (backend/operations): paused until
        # started, and only startable off the classic floor.
        self.shift = ShiftEngine(self)

        self.statistics: Dict[str, Any] = {
```

**Replace in** `backend/digital_twin.py`:

```python
            self.equipment = Equipment.for_floor(self)
            for key in self.statistics:
```

with:

```python
            self.equipment = Equipment.for_floor(self)
            self.shift.reset()
            for key in self.statistics:
```

**Replace in** `backend/simulator.py`:

```python
            twin.scheduler.tick()
            people.update_transits(twin)  # walks end before robots decide who is on the walkway
```

with:

```python
            twin.scheduler.tick()
            twin.shift.tick()  # new work and order stages, dispatched below in the same tick
            people.update_transits(twin)  # walks end before robots decide who is on the walkway
```

- [ ] **Step 6: Run the new tests**

Run: `.venv/bin/python -m pytest -o addopts="" -q backend/test_shift.py`
Expected: `12 passed`.

- [ ] **Step 7: Run the full suite**

A paused engine generates nothing, so every existing test sees the same twin.

Run: `.venv/bin/python -m pytest -o addopts="" -q`
Expected: `2 failed, 587 passed`.

- [ ] **Step 8: Commit**

```bash
git add backend/operations/__init__.py backend/operations/orders.py backend/operations/shift.py \
        backend/models.py backend/digital_twin.py backend/simulator.py backend/test_shift.py
git commit -m "feat: the shift engine and its order chains

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 13: People's activities, breaks and duty by the shift clock

While the shift runs, the engine moves the people on shift by the role their workforce record gives them (spec §11.3), in `backend/operations/activities.py`: Sam and Lee to Pick 2 when it has work, otherwise to intake; Ana and Kai to the dock a truck is at; Jordan follows the humanoid's zone; Noor and Mateo to the workshop (their jobs take them to jams); Riley outside the pack cells. Everyone takes a 15-minute break in `sw_floor` every 2 sim-hours, staggered 12 minutes apart, the first after two hours on shift — Jordan's makes the humanoid pause, as intended. Since a person standing in a zone stops a forklift or hauler from entering it, someone a waiting forklift wants out of the way steps aside to the nearest zone those bodies can't drive in and stays there 30 s. On the new floor duty follows the simulated shift clock and the HR state (§11.4): inside an operator's shift window on duty, outside it off duty, and an HR state other than ACTIVE (Sasha on leave, Morgan terminated) is always off duty. Classic keeps its wall-clock shifts.

**Files:**
- Create: `backend/operations/activities.py`
- Modify: `backend/operations/shift.py` (the engine moves people every `SHIFT_CHECK_EVERY_TICKS`; `yield_until`)
- Modify: `backend/operations/__init__.py` (exports)
- Modify: `backend/operator.py` (`employment_status`)
- Modify: `backend/fleet_bridge.py` (syncs `employment_status`)
- Modify: `backend/simulator.py` (`_check_operator_shifts` uses the shift clock off the classic floor)
- Test: `backend/test_activities.py` (create)

**Interfaces:**
- Consumes (Tasks 5, 12): `people.place`, `people.start_transit`, `people.zones_of_cell`; `Robot.wait_reason`/`.wait_cell`; `ShiftEngine.orders`, `shift_hour`; `Operator.is_within_shift`, `Warehouse.clearance`.
- Produces:
  - `backend.operations.activities`: `ROLES: Dict[worker_id, role]`, `HOME_ZONES`, `BREAK_ZONE = "sw_floor"`, `BREAK_S` 900, `BREAK_EVERY_S` 7200, `BREAK_STAGGER_S` 720, `YIELD_HOLD_S` 30.0; `on_break(index, simulation_time) -> bool`, `refuge_zone(twin, origin) -> str`, `move_people(engine)`, `sync_duty(twin)` (emits `OPERATOR_SHIFT_CHANGED` with data `{"operator_id", "hour", "employment_status"}`).
  - `ShiftEngine.yield_until: Dict[operator_id, float]`.
  - `Operator.employment_status: Optional[str]` (in `to_dict`/`from_dict`).

- [ ] **Step 1: Write the failing tests**

**Create** `backend/test_activities.py`:

```python
"""People's activities and duty (multi-embodiment spec §11.3, §11.4): the
shift engine moves people by role, sends them on staggered breaks, has them
step aside for a waiting forklift, and duty follows the shift clock and HR."""
import pytest

from backend import people
from backend.digital_twin import DigitalTwin
from backend.models import OperatorStatus, SimulationStatus, TaskStatus
from backend.operations import move_people, on_break, refuge_zone, sync_duty
from backend.simulator import Simulator


@pytest.fixture
def twin(tmp_path):
    return DigitalTwin(log_dir=str(tmp_path / "logs"), data_dir=str(tmp_path / "data"),
                       persist_logs=False, demo=True, demo_tasks=False, layout="distribution_center")


@pytest.fixture
def sim(twin):
    simulator = Simulator(twin)
    twin.simulation_status = SimulationStatus.RUNNING
    return simulator


def crew(twin, *names):
    workers = {"Sam": "E-10001", "Noor": "E-10003", "Mateo": "E-10004", "Ana": "E-10005",
               "Jordan": "E-10006", "Riley": "E-10008", "Sasha": "E-10009", "Morgan": "E-10010"}
    return [twin.add_operator(name=name, worker_id=workers[name]) for name in names]


def settle(twin):
    """Finish every walk at once."""
    for operator in twin.operators.values():
        if operator.in_transit:
            people.place(twin, operator, operator.transit_to)


def test_people_clock_on_at_their_place_by_role(twin):
    sam, mateo, ana, riley = crew(twin, "Sam", "Mateo", "Ana", "Riley")
    move_people(twin.shift)
    assert (sam.zone, mateo.zone, ana.zone, riley.zone) == \
        ("intake_staging", "workshop", "outbound_staging", "pick_station_1")
    assert not any(o.in_transit for o in (sam, mateo, ana, riley))       # clocking on is instant


def test_pickers_go_where_the_work_is_and_dock_hands_meet_the_truck(twin):
    sam, ana = crew(twin, "Sam", "Ana")
    move_people(twin.shift)
    order = twin.shift.orders.customer([{"sku": "SKU-001", "units": 1}], "dock_4")
    order.status, order.pick_station = "IN_PROGRESS", "pick_station_2"
    pallet = twin.add_box(name="IN-1", kind="PALLET", sku="SKU-001", quantity=10, weight=300.0, position=(2, 8))
    twin.shift.orders.inbound("dock_2", [pallet.id])
    move_people(twin.shift)
    assert (sam.transit_to, ana.transit_to) == ("pick_station_2", "dock_2")
    settle(twin)
    order.status = "DONE"
    twin.shift.orders.orders.clear()
    move_people(twin.shift)
    assert (sam.transit_to, ana.transit_to) == ("intake_staging", "outbound_staging")


def test_jordan_follows_the_humanoid(twin):
    (jordan,) = crew(twin, "Jordan")
    humanoid = twin.add_robot(name="H1-212", asset_id="AST-000212", position=(22, 7))
    move_people(twin.shift)
    assert jordan.zone == "returns_qc"
    humanoid.position = (10, 13)
    move_people(twin.shift)
    assert jordan.transit_to == "tote_aisle_1"


def test_breaks_are_every_two_hours_staggered_and_start_after_two_hours():
    assert not on_break(0, 0) and not on_break(0, 3600)                   # nobody breaks in the first two hours
    assert on_break(0, 7200) and on_break(0, 7200 + 899) and not on_break(0, 7200 + 900)
    assert not on_break(1, 7200) and on_break(1, 7200 + 720)              # 12 minutes later for the next person
    assert on_break(0, 4 * 3600 + 60)


def test_a_person_on_break_goes_to_the_south_west_floor(twin):
    sam, = crew(twin, "Sam")
    move_people(twin.shift)
    twin.simulation_time = 7200.0                                          # Sam's slot is the first
    move_people(twin.shift)
    assert sam.transit_to == "sw_floor"
    settle(twin)
    twin.simulation_time = 7200.0 + 900
    move_people(twin.shift)
    assert sam.transit_to == "intake_staging"


def test_a_person_steps_aside_for_a_waiting_forklift(twin, sim):
    sam, = crew(twin, "Sam")
    people.place(twin, sam, "intake_staging")
    forklift = twin.add_robot(name="PF1200-205", asset_id="AST-000205", position=(7, 4))
    task = twin.tasks.create_task({"type": "MOVE_ROBOT", "robot_id": forklift.id, "destination": "5,4"})
    for _ in range(20):
        sim.tick()
    assert forklift.wait_reason == "PERSON_IN_AISLE"
    refuge = refuge_zone(twin, "intake_staging")
    assert all(twin.warehouse.clearance(c) != "WIDE" for c in twin.warehouse.zones[refuge].cells)
    move_people(twin.shift)
    assert sam.transit_to == refuge
    for _ in range(400):
        if task.is_terminal:
            break
        sim.tick()
    assert task.status is TaskStatus.COMPLETED and forklift.wait_reason is None
    settle(twin)
    move_people(twin.shift)
    assert not sam.in_transit                                              # held clear for a while
    twin.simulation_time += 30.0
    move_people(twin.shift)
    assert sam.transit_to == "intake_staging"


def test_duty_follows_the_shift_clock_and_hr_state(twin):
    sam, sasha, morgan = crew(twin, "Sam", "Sasha", "Morgan")
    assert (sasha.employment_status, morgan.employment_status) == ("ON_LEAVE", "TERMINATED")
    twin.set_operator_shift(sam.id, 6, 14)
    sync_duty(twin)
    assert sam.status is OperatorStatus.AVAILABLE                         # 06:00
    assert sasha.status is OperatorStatus.OFF_DUTY and morgan.status is OperatorStatus.OFF_DUTY
    people.place(twin, sam, "intake_staging")
    twin.simulation_time = 8 * 3600.0                                       # 14:00
    sync_duty(twin)
    assert sam.status is OperatorStatus.OFF_DUTY and sam.zone is None
    changed = twin.events.query(event_type="OPERATOR_SHIFT_CHANGED")
    assert any("HR state ON_LEAVE" in e["message"] for e in changed)
    assert any("shift clock 14h" in e["message"] for e in changed)


def test_the_simulator_uses_the_shift_clock_on_the_new_floor(twin, sim):
    sam, = crew(twin, "Sam")
    twin.set_operator_shift(sam.id, 22, 23)                                 # not at 06:00
    for _ in range(20):
        sim.tick()
    assert sam.status is OperatorStatus.OFF_DUTY


def test_the_engine_moves_people_only_while_the_shift_runs(twin, sim):
    sam, = crew(twin, "Sam")
    for _ in range(40):
        sim.tick()
    assert sam.zone is None                                                 # paused: nobody is placed
    twin.shift.start()
    for _ in range(40):
        sim.tick()
    assert sam.zone == "intake_staging"
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/bin/python -m pytest -o addopts="" -q backend/test_activities.py`
Expected: `1 error` — `ImportError: cannot import name 'move_people' from 'backend.operations'`.

- [ ] **Step 3: The activities**

**Create** `backend/operations/activities.py`:

```python
"""People's activities and duty on the distribution-centre floor (multi-
embodiment spec §11.3, §11.4).

While the shift runs, the engine moves the people on shift every
SHIFT_CHECK_EVERY_TICKS, by the role their workforce record gives them:
Sam and Lee pick at Pick 2 when it has work and help at intake otherwise;
Ana and Kai go to the dock a truck is at; Jordan follows the humanoid;
Noor and Mateo keep to the workshop (their jobs take them to jams); Riley
stays outside the pack cells. Everyone takes a 15-minute break in sw_floor
every 2 sim-hours, staggered by 12 minutes per person — the first after two
hours on shift. A person standing in a zone a forklift or hauler is waiting
to enter (PERSON_IN_AISLE) steps aside to the nearest zone those bodies
can't drive in, and stays there YIELD_HOLD_S before going back.

Duty follows the shift clock: an operator with a shift window is on duty
inside it, and an HR state other than ACTIVE (ON_LEAVE, TERMINATED) is off
duty whatever the clock says. The classic floor keeps its wall-clock shifts.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from .. import people
from ..layouts.base import WIDE
from ..models import CellType, EventType, LogCategory, OperatorStatus
from .shift import shift_hour

#: Workforce record -> role on the floor (the demo workforce, spec §4.1).
ROLES: Dict[str, str] = {
    "E-10001": "picker", "E-10002": "picker", "E-10003": "technician", "E-10004": "technician",
    "E-10005": "dock", "E-10006": "supervisor", "E-10007": "dock", "E-10008": "cell_operator",
    "E-10009": "dock", "E-10010": "picker",
}
#: Where each role stands when it has nothing else to do.
HOME_ZONES: Dict[str, str] = {
    "picker": "intake_staging", "technician": "workshop", "dock": "outbound_staging",
    "supervisor": "returns_qc", "cell_operator": "pick_station_1",
}
BREAK_ZONE = "sw_floor"
BREAK_S = 15 * 60
BREAK_EVERY_S = 2 * 3600
BREAK_STAGGER_S = 12 * 60
YIELD_HOLD_S = 30.0
#: Zone types people never stand in (or, for the pack cells' STATION type,
#: aren't sent to rest in: an arm stops while anyone is inside).
_NO_STANDING = {CellType.WALKWAY, CellType.CONVEYOR, CellType.SORTER, CellType.PALLET_RACK, CellType.TOTE_SHELF,
                CellType.DRONE_PAD, CellType.RESTRICTED}


def on_break(index: int, simulation_time: float) -> bool:
    """Is the person with break slot `index` on a break at `simulation_time`?"""
    offset = simulation_time - index * BREAK_STAGGER_S
    cycle = int(offset // BREAK_EVERY_S)
    return cycle >= 1 and offset - cycle * BREAK_EVERY_S < BREAK_S


def refuge_zone(twin: Any, origin: str) -> str:
    """The nearest zone a forklift or hauler can't drive in (no WIDE cell),
    where a person can stand clear: a tote aisle, the top aisle, a station."""
    warehouse = twin.warehouse
    start = warehouse.zones[origin].center
    best, best_distance = origin, None
    for zone in warehouse.zones.values():
        if zone.cell_type in _NO_STANDING or "route" in zone.attributes or zone.attributes.get("fenced"):
            continue
        if any(warehouse.clearance(cell) == WIDE for cell in zone.cells):
            continue
        distance = abs(zone.center[0] - start[0]) + abs(zone.center[1] - start[1])
        if best_distance is None or distance < best_distance:
            best, best_distance = zone.key, distance
    return best


def _wanted_zones(twin: Any) -> set:
    """Zones a forklift or hauler is waiting to drive into."""
    wanted = set()
    for robot in twin.robots.values():
        if robot.wait_reason == "PERSON_IN_AISLE" and robot.wait_cell is not None:
            here = {zone.key for zone in twin.warehouse.zones_of_cell(robot.position)}
            wanted |= {zone.key for zone in twin.warehouse.zones_of_cell(robot.wait_cell)
                       if zone.key not in here}
    return wanted


def _role_target(engine: Any, role: str) -> Optional[str]:
    twin, orders = engine.twin, engine.orders.orders.values()
    if role == "picker":
        busy = any(o.status == "IN_PROGRESS" and o.pick_station == "pick_station_2" for o in orders)
        manual = any(t.type.value == "MANUAL_PICK" and not t.is_terminal for t in twin.tasks.tasks.values())
        return "pick_station_2" if busy or manual else HOME_ZONES[role]
    if role == "dock":
        trucks = [o for o in orders if o.kind == "INBOUND" and not o.is_terminal and o.lines]
        return trucks[-1].lines[0]["dock"] if trucks else HOME_ZONES[role]
    if role == "supervisor":
        humanoid = next((r for r in twin.robots.values() if r.mobility is not None and r.mobility.supervision), None)
        zone = twin.warehouse.zone_of_cell(humanoid.position) if humanoid is not None else None
        return zone.key if zone is not None else HOME_ZONES[role]
    return HOME_ZONES.get(role)


def move_people(engine: Any) -> None:
    """Send everyone on shift where their role, a break or a waiting forklift
    says they should be (ShiftEngine.tick, while the shift runs)."""
    twin = engine.twin
    now = twin.simulation_time
    wanted = _wanted_zones(twin)
    slots = sorted(ROLES)
    for operator in sorted(twin.operators.values(), key=lambda o: o.id):
        role = ROLES.get(operator.worker_id)
        if role is None or operator.status != OperatorStatus.AVAILABLE or operator.in_transit:
            continue  # off duty, on a job of their own, walking, or not someone the engine moves
        hold = engine.yield_until.get(operator.id)
        if operator.zone in wanted:
            target = refuge_zone(twin, operator.zone)
            engine.yield_until[operator.id] = now + YIELD_HOLD_S
        elif hold is not None and now < hold:
            continue
        elif on_break(slots.index(operator.worker_id), now):
            target = BREAK_ZONE
        else:
            target = _role_target(engine, role)
        if target is None or target == operator.zone:
            continue
        if operator.zone is None:
            people.place(twin, operator, target)  # clocking on: straight to their place
        else:
            people.start_transit(twin, operator, target)


def sync_duty(twin: Any) -> None:
    """On the new floor duty follows the shift clock and the HR state
    (Simulator._check_operator_shifts calls this instead of the wall clock)."""
    hour = shift_hour(twin.simulation_time)
    for operator in twin.operators.values():
        if operator.status == OperatorStatus.ON_TASK:
            continue
        employed = operator.employment_status in (None, "ACTIVE")
        should_be = OperatorStatus.AVAILABLE if employed and operator.is_within_shift(hour) \
            else OperatorStatus.OFF_DUTY
        if operator.status == should_be:
            continue
        previous = operator.set_status(should_be)
        why = f"shift {operator.shift_start_hour}-{operator.shift_end_hour}" if employed \
            else f"HR state {operator.employment_status}"
        twin.events.emit(
            EventType.OPERATOR_SHIFT_CHANGED,
            f"{operator.name} {previous.value} → {should_be.value} ({why}, shift clock {hour:02d}h)",
            category=LogCategory.TASK,
            data={"operator_id": operator.id, "hour": hour, "employment_status": operator.employment_status},
        )

```

- [ ] **Step 4: The engine moves people while it runs**

**Replace in** `backend/operations/shift.py`:

```python
"""The shift engine (multi-embodiment spec §11.1, §11.4): it keeps the
distribution-centre floor busy on its own.

Seeded streams generate the work — inbound trucks, customer orders, full-
```

with:

```python
"""The shift engine (multi-embodiment spec §11.1, §11.3, §11.4): it keeps
the distribution-centre floor busy on its own, and moves its people
(activities.py).

Seeded streams generate the work — inbound trucks, customer orders, full-
```

**Replace in** `backend/operations/shift.py`:

```python
        self._face_index = 0
        self.counters: Dict[str, int] = {stream: 0 for stream in DEFAULT_RATES}
```

with:

```python
        self._face_index = 0
        # Operator id -> the simulation time a person who stepped aside for a
        # forklift waits until before going back (activities.move_people).
        self.yield_until: Dict[str, float] = {}
        self.counters: Dict[str, int] = {stream: 0 for stream in DEFAULT_RATES}
```

**Replace in** `backend/operations/shift.py`:

```python
                getattr(self, f"_generate_{stream}")()
        self.orders.advance()
```

with:

```python
                getattr(self, f"_generate_{stream}")()
            if self.twin.tick_count % CONFIG["SHIFT_CHECK_EVERY_TICKS"] == 0:
                from .activities import move_people  # deferred: activities.py imports this module

                move_people(self)
        self.orders.advance()
```

**Replace in** `backend/operations/__init__.py`:

```python
from __future__ import annotations

from .orders import ORDER_KINDS, Order, OrderBook, Stage
from .shift import DEFAULT_RATES, ShiftEngine, shift_clock, shift_hour

__all__ = ["DEFAULT_RATES", "ORDER_KINDS", "Order", "OrderBook", "ShiftEngine", "Stage", "shift_clock", "shift_hour"]

```

with:

```python
from __future__ import annotations

from .activities import move_people, on_break, refuge_zone, sync_duty
from .orders import ORDER_KINDS, Order, OrderBook, Stage
from .shift import DEFAULT_RATES, ShiftEngine, shift_clock, shift_hour

__all__ = ["DEFAULT_RATES", "ORDER_KINDS", "Order", "OrderBook", "ShiftEngine", "Stage", "move_people",
           "on_break", "refuge_zone", "shift_clock", "shift_hour", "sync_duty"]

```

- [ ] **Step 5: Duty from the shift clock and the HR state**

**Replace in** `backend/operator.py`:

```python
        self.certification_scopes: Dict[str, Dict[str, List[str]]] = {}
        self.created_at = now_iso()
```

with:

```python
        self.certification_scopes: Dict[str, Dict[str, List[str]]] = {}
        # The workforce record's HR state (ACTIVE, ON_LEAVE, TERMINATED), synced
        # with the credentials; on the new floor anything but ACTIVE is off duty.
        self.employment_status: Optional[str] = None
        self.created_at = now_iso()
```

**Replace in** `backend/operator.py`:

```python
                                     for code, scope in self.certification_scopes.items()},
            "created_at": self.created_at,
```

with:

```python
                                     for code, scope in self.certification_scopes.items()},
            "employment_status": self.employment_status,
            "created_at": self.created_at,
```

**Replace in** `backend/operator.py`:

```python
        operator.certification_scopes = dict(data.get("certification_scopes") or {})
        return operator
```

with:

```python
        operator.certification_scopes = dict(data.get("certification_scopes") or {})
        operator.employment_status = data.get("employment_status")
        return operator
```

**Replace in** `backend/fleet_bridge.py`:

```python
        operator.certification_scopes = credential_scopes(worker)
        if codes == list(operator.certifications):
```

with:

```python
        operator.certification_scopes = credential_scopes(worker)
        operator.employment_status = worker["employment_status"]
        if codes == list(operator.certifications):
```

**Replace in** `backend/simulator.py`:

```python
from .maintenance import maintenance_reason
from .models import (
```

with:

```python
from .maintenance import maintenance_reason
from .operations.activities import sync_duty
from .models import (
```

**Replace in** `backend/simulator.py`:

```python
        every operator's behaviour before this existed) or one currently
        ON_TASK — only flips between AVAILABLE and OFF_DUTY."""
        twin = self.twin
        hour = datetime.now().hour
```

with:

```python
        every operator's behaviour before this existed) or one currently
        ON_TASK — only flips between AVAILABLE and OFF_DUTY. On a layered
        floor duty follows the simulated shift clock and the HR state instead
        (backend/operations/activities.py)."""
        twin = self.twin
        if twin.layout_name != "classic":
            sync_duty(twin)
            return
        hour = datetime.now().hour
```

- [ ] **Step 6: Run the new tests with the classic shift tests**

`tests.py`'s operator-shift tests still run on the wall clock.

Run: `.venv/bin/python -m pytest -o addopts="" -q backend/test_activities.py backend/tests.py -k "activities or shift"`
Expected: `16 passed, 152 deselected`.

- [ ] **Step 7: Run the full suite**

Run: `.venv/bin/python -m pytest -o addopts="" -q`
Expected: `2 failed, 596 passed`.

- [ ] **Step 8: Commit**

```bash
git add backend/operations/activities.py backend/operations/shift.py backend/operations/__init__.py \
        backend/operator.py backend/fleet_bridge.py backend/simulator.py backend/test_activities.py
git commit -m "feat: people's activities, breaks, and duty by the shift clock

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 14: Evaluation checks, their fixtures, and layout-driven system checks

The nine evaluation checks of spec §10.4 join `DEFAULT_CHECKS`, each grading event data the new floor now writes with the same rule the gate used: `payload_within_limit` (a pick or lift whose load's *true* weight is over the robot's payload), `reach_within_limit` (a lift, scan or placement above the robot's reach — at the level a box really went to), `clearance_respected` (a wide robot's route through a NARROW cell), `no_fly_respected` (a drone's route through a no-fly cell), `human_zone_clear` (a forklift or hauler entering, or an arm moving, with a person there), `supervision_maintained` (a humanoid step unsupervised), `count_consistent` (a count that completed with a number other than what was there), `handoff_consistent` (a hand-off the receiver never saw) and `sort_correct` (a carton on a dock other than its lane). A log with none of a check's data — every classic log — gets a not-applicable PASS (`CheckResult.applicable` False), so existing fixtures grade exactly as before. To give them their data, a new-floor path event carries the robot's clearance, layer and each cell's clearance and no-fly flag, and the TASK_STARTED and terminal snapshots carry the robot's layer, altitude, class and limits and the box's kind, slot and true and declared weights. One pass and one fail fixture per check go under `logs/eval_examples/multi_embodiment/` (Plan ruling 1). The system checks become layout-driven (§10.5): the loaded layout's `required_zones`; connectivity per body class (narrow ground, wide ground, air); an arm on its station and a drone over any flyable cell; no battery check for an arm, and a drone charges on its pad; a dock door is part of the perimeter wall; and a person's job is active without a robot.

**Files:**
- Modify: `backend/eval_engine.py` (`CheckResult.applicable`; the nine checks; `EMBODIMENT_CHECKS`)
- Modify: `backend/simulator.py` (a new-floor path event's per-cell clearance and no-fly flags)
- Modify: `backend/task_manager.py` (the snapshot fields)
- Modify: `backend/ci_engine.py` (layout-driven environment, position, navigation, battery and task checks)
- Create: 18 fixtures, `logs/eval_examples/multi_embodiment/task_200_payload_within_limit_pass.json` … `task_217_sort_correct_fail.json`
- Test: `backend/test_trust_checks.py` (create)

**Interfaces:**
- Consumes (Tasks 1, 3–6, 10, 11): `payload_ok`, `reach_ok`, `clearance_ok`, `no_fly_ok`; the `BOX_PICKED`, `LIFTED`, `SCANNED`, `PLACED`, `ROBOT_STEP` and `HANDOFF` data; `human_jobs.HUMAN_JOBS`; `Robot.mains_powered`; `FleetBridge.model_profile`; `Warehouse.required_zones`, `.passable_cells`, `.fixed_stations`, `.clearance`, `.is_no_fly`.
- Produces:
  - `CheckResult(..., applicable=True)`; `to_dict()` gains `"applicable"`.
  - `check_payload_within_limit`, `check_reach_within_limit`, `check_clearance_respected`, `check_no_fly_respected`, `check_human_zone_clear`, `check_supervision_maintained`, `check_count_consistent`, `check_handoff_consistent`, `check_sort_correct`; `EMBODIMENT_CHECKS` (those nine, in that order) appended to `DEFAULT_CHECKS`.
  - `PATH_CREATED`/`PATH_RECALCULATED` data on the new floor: `layer`, `clearance`, `route_clearance: List[Optional[str]]`, `route_no_fly: List[bool]`.
  - Snapshot `robot` (new floor): `layer`, `altitude_m`, `embodiment_class`, `clearance`, `max_payload_kg`, `max_shelf_level`; snapshot `box` (new floor): `kind`, `slot`, `true_weight_kg`, `declared_weight_kg`.
  - `ci_engine.PROFILE_CLASSES`; `CIEngine._cut_off(cells, neighbors) -> int`, `_allowed_at(robot) -> bool`.

- [ ] **Step 1: Write the failing tests**

**Create** `backend/test_trust_checks.py`:

```python
"""The multi-embodiment evaluation checks (spec §10.4) — graded against a pass
and a fail fixture each, against live runs whose faults they must catch, and
against classic logs they don't apply to — and the layout-driven system
checks (§10.5)."""
import glob
import os
import re

import pytest

from backend import people
from backend.ci_engine import CIEngine
from backend.digital_twin import DigitalTwin
from backend.eval_engine import DEFAULT_CHECKS, EMBODIMENT_CHECKS, Verdict, evaluate_events, evaluate_file
from backend.models import BoxStatus, RobotStatus, SimulationStatus, TaskStatus
from backend.simulator import Simulator

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES = os.path.join(BASE_DIR, "logs", "eval_examples", "multi_embodiment")
NAMES = ["payload_within_limit", "reach_within_limit", "clearance_respected", "no_fly_respected",
         "human_zone_clear", "supervision_maintained", "count_consistent", "handoff_consistent", "sort_correct"]


def make_twin(tmp_path, layout="distribution_center"):
    return DigitalTwin(log_dir=str(tmp_path / "logs"), data_dir=str(tmp_path / "data"),
                       persist_logs=False, demo=True, demo_tasks=False, layout=layout)


@pytest.fixture
def twin(tmp_path):
    return make_twin(tmp_path)


@pytest.fixture
def sim(twin):
    simulator = Simulator(twin)
    twin.simulation_status = SimulationStatus.RUNNING
    return simulator


def run(sim, task, max_ticks=3000):
    for _ in range(max_ticks):
        if task.is_terminal:
            return
        sim.tick()
    assert task.is_terminal, task.status


def grade(twin, task):
    report = evaluate_events(twin.events.query(task_id=task.id, limit=5000), task_id=task.id)
    return report, {check.name: check for check in report.checks}


# --------------------------------------------------------------------------- #
# The checks and their fixtures
# --------------------------------------------------------------------------- #
def test_the_new_checks_follow_the_old_ones():
    assert [check.__name__[len("check_"):] for check in EMBODIMENT_CHECKS] == NAMES
    assert DEFAULT_CHECKS[-len(NAMES):] == EMBODIMENT_CHECKS and len(DEFAULT_CHECKS) == 10 + len(NAMES)


def test_a_classic_log_gets_not_applicable_passes(tmp_path):
    classic = make_twin(tmp_path, layout="classic")
    simulator = Simulator(classic)
    classic.simulation_status = SimulationStatus.RUNNING
    task = classic.tasks.create_task({"type": "PICK_AND_DELIVER", "robot_id": "Robo-01", "box_id": "Box-A",
                                      "destination": "loading_zone"})
    run(simulator, task, max_ticks=800)
    report, checks = grade(classic, task)
    assert report.verdict is Verdict.PASS
    assert all(checks[name].verdict is Verdict.PASS and not checks[name].applicable for name in NAMES)
    assert checks["terminal_state"].to_dict()["applicable"] is True
    started = classic.events.query(task_id=task.id, event_type="TASK_STARTED")[0]["data"]["state"]
    assert "layer" not in started["robot"] and "kind" not in started["box"]   # classic snapshots unchanged


@pytest.mark.parametrize("path", sorted(glob.glob(os.path.join(FIXTURES, "task_*.json"))),
                         ids=lambda path: os.path.basename(path))
def test_each_fixture_grades_as_its_name_says(path):
    name, kind = re.match(r"task_\d+_(\w+)_(pass|fail)\.json", os.path.basename(path)).groups()
    report = evaluate_file(path)
    checks = {check.name: check for check in report.checks}
    assert checks[name].applicable
    if kind == "pass":
        assert report.verdict is Verdict.PASS, report.reasons
    else:
        assert report.verdict is Verdict.FAIL
        assert [c.name for c in report.checks if c.verdict is Verdict.FAIL] == [name]


def test_every_new_check_has_a_pass_and_a_fail_fixture():
    on_disk = {os.path.basename(path) for path in glob.glob(os.path.join(FIXTURES, "task_*.json"))}
    covered = {re.match(r"task_\d+_(\w+)_(pass|fail)\.json", name).groups() for name in on_disk}
    assert covered == {(name, kind) for name in NAMES for kind in ("pass", "fail")}


# --------------------------------------------------------------------------- #
# Live runs: the checks catch what the faults do
# --------------------------------------------------------------------------- #
def test_a_misdeclared_pallet_fails_payload_within_limit(twin, sim):
    twin.add_robot(name="PF1200-205", asset_id="AST-000205", position=(7, 4))
    heavy = twin.add_box(name="PAL-H", kind="PALLET", sku="SKU-01", quantity=40, weight=1000.0,
                         true_weight_kg=1450.0, position=(5, 4))
    task = twin.tasks.create_task({"type": "PUTAWAY_PALLET", "box_id": heavy.id, "slot": "PR-08-02-1"})
    run(sim, task)
    report, checks = grade(twin, task)
    assert task.status is TaskStatus.COMPLETED                        # it lifted what its paperwork allowed
    assert checks["payload_within_limit"].verdict is Verdict.FAIL and report.verdict is Verdict.FAIL
    for name in ("reach_within_limit", "clearance_respected", "human_zone_clear"):
        assert checks[name].applicable and checks[name].verdict is Verdict.PASS, name
    started = twin.events.query(task_id=task.id, event_type="TASK_STARTED")[0]["data"]["state"]
    assert (started["robot"]["embodiment_class"], started["robot"]["max_payload_kg"]) == ("FORKLIFT", 1200.0)
    assert (started["box"]["kind"], started["box"]["true_weight_kg"]) == ("PALLET", 1450.0)


def test_a_miscount_fails_count_consistent(twin, sim):
    twin.add_robot(name="IX2-208", asset_id="AST-000208", position=(20, 2))
    twin.add_box(name="PAL-0", kind="PALLET", sku="SKU-01", quantity=40, weight=500.0, slot="PR-12-02-0")
    twin.faults.arm("scan_miscount")
    task = twin.tasks.create_task({"type": "CYCLE_COUNT", "face": "12,2"})
    run(sim, task)
    _, checks = grade(twin, task)
    assert checks["count_consistent"].verdict is Verdict.FAIL
    assert checks["no_fly_respected"].applicable and checks["no_fly_respected"].verdict is Verdict.PASS


def test_a_lost_item_fails_handoff_consistent_and_a_mis_sort_fails_sort_correct(twin, sim):
    twin.add_robot(name="PK30-203", asset_id="AST-000203", position=(20, 14))
    twin.add_robot(name="CX10-210", asset_id="AST-000210")
    tote = twin.add_box(name="TOTE-1", kind="TOTE", sku="SKU-02", quantity=6, weight=7.5, slot="TS-10-12-1")
    tote.position, tote.status = (19, 14), BoxStatus.DELIVERED
    twin.faults.arm("handoff_loss")
    lost = twin.tasks.create_task({"type": "PICK_ITEMS", "box_id": tote.id, "quantity": 1})
    run(sim, lost)
    assert grade(twin, lost)[1]["handoff_consistent"].verdict is Verdict.FAIL
    pick = twin.tasks.create_task({"type": "PICK_ITEMS", "box_id": tote.id, "quantity": 1, "order_id": "ORD-9",
                                   "pack_cell": "pack_cell_1"})
    pack = twin.tasks.create_task({"type": "PACK_ORDER", "order_id": "ORD-9", "quantity": 1, "lane": "dock_4"})
    twin.faults.arm("mis_sort")
    run(sim, pick)
    run(sim, pack)
    for _ in range(80):
        sim.tick()
    _, checks = grade(twin, pack)
    assert checks["sort_correct"].verdict is Verdict.FAIL
    assert checks["human_zone_clear"].applicable and checks["human_zone_clear"].verdict is Verdict.PASS


def test_a_supervised_job_passes_supervision_maintained(twin, sim):
    humanoid = twin.add_robot(name="H1-212", asset_id="AST-000212", position=(22, 7))
    jordan = twin.add_operator(name="Jordan", worker_id="E-10006")
    people.place(twin, jordan, "returns_qc")
    task = twin.tasks.create_task({"type": "MOVE_ROBOT", "robot_id": humanoid.id, "destination": "20,6"})
    run(sim, task)
    _, checks = grade(twin, task)
    assert checks["supervision_maintained"].applicable and checks["supervision_maintained"].verdict is Verdict.PASS


# --------------------------------------------------------------------------- #
# System checks (spec §10.5)
# --------------------------------------------------------------------------- #
def results(twin):
    return {check["name"]: check for check in CIEngine(twin).run()["checks"]}


def test_the_new_floor_passes_its_system_checks(twin, sim):
    twin.add_robot(name="CX10-210", asset_id="AST-000210")
    drone = twin.add_robot(name="IX2-208", asset_id="AST-000208", position=(20, 2))
    twin.set_robot_layer(drone.id, "AIR", altitude_m=2.0)
    drone.position = (12, 2)                                          # over a rack
    other = twin.add_robot(name="IX2-209", asset_id="AST-000209", position=(21, 2))
    other.battery, other.status = 50.0, RobotStatus.CHARGING          # charging on its pad
    twin.add_robot(name="PF1200-205", asset_id="AST-000205", position=(7, 4))
    noor = twin.add_operator(name="Noor", worker_id="E-10003")
    people.place(twin, noor, "workshop")
    twin.equipment.jam((20, 15))
    twin.tasks.create_task({"type": "CLEAR_JAM", "segment": "20,15"})   # a person's job: active with no robot
    checks = results(twin)
    assert all(check["passed"] for check in checks.values()), {k: v["message"] for k, v in checks.items()
                                                               if not v["passed"]}
    assert checks["Environment validation"]["message"].endswith("connected for narrow, wide and air bodies")


def test_misplaced_robots_fail_the_position_check(twin):
    arm = twin.add_robot(name="CX10-210", asset_id="AST-000210")
    forklift = twin.add_robot(name="PF1200-205", asset_id="AST-000205", position=(7, 4))
    drone = twin.add_robot(name="IX2-208", asset_id="AST-000208", position=(20, 2))
    twin.set_robot_layer(drone.id, "AIR")
    arm.position, forklift.position, drone.position = (22, 13), (10, 13), (2, 3)
    message = results(twin)["Robot position validation"]["message"]
    assert "CX10-210 stands on a STATION cell at (22,13)" in message
    assert "PF1200-205 stands on a EMPTY cell at (10,13)" in message           # a narrow tote aisle
    assert "IX2-208 stands on a DOCK cell at (2,3) on the AIR layer" in message  # a no-fly dock


def test_battery_and_required_zone_checks_read_the_floor(twin):
    arm = twin.add_robot(name="CX10-210", asset_id="AST-000210")
    arm.battery = 0.0                                                   # mains-powered: never flat
    assert results(twin)["Battery validation"]["passed"]
    amr = twin.add_robot(name="TR50-201", asset_id="AST-000201", position=(8, 13))
    amr.status = RobotStatus.CHARGING
    assert not results(twin)["Battery validation"]["passed"]           # no charger in a tote aisle
    del twin.warehouse.zones["sorter"]
    assert "missing zones: sorter" in results(twin)["Environment validation"]["message"]
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/bin/python -m pytest -o addopts="" -q backend/test_trust_checks.py`
Expected: `1 error` — `ImportError: cannot import name 'EMBODIMENT_CHECKS' from 'backend.eval_engine'`.

- [ ] **Step 3: The nine checks**

**Replace in** `backend/eval_engine.py`:

```python
        return None

# Fallback thresholds mirror backend/models.py CONFIG so this module still
# behaves sensibly if it's ever used outside the backend package.
```

with:

```python
        return None

try:  # the physical rules of the multi-embodiment floor (spec §10.1), shared
    # with the gate exactly like the eligibility rules above
    from .eligibility import clearance_ok, no_fly_ok, payload_ok, reach_ok
except Exception:  # pragma: no cover - defensive fallback only

    def payload_ok(weight_kg, max_payload_kg):
        ok = weight_kg is None or max_payload_kg is None or weight_kg <= max_payload_kg
        return ok, None if ok else f"a {weight_kg:g} kg load is over its {max_payload_kg:g} kg payload limit"

    def reach_ok(level, max_shelf_level):
        ok = level is None or max_shelf_level is None or level <= max_shelf_level
        return ok, None if ok else f"level {level} is out of its reach (highest level {max_shelf_level})"

    def clearance_ok(route_cells_clearance, robot_clearance):
        narrow = sum(1 for c in route_cells_clearance if c == "NARROW") if robot_clearance == "WIDE" else 0
        return not narrow, None if not narrow else f"a wide robot's route uses {narrow} narrow cell(s)"

    def no_fly_ok(route_cells_no_fly):
        crossed = sum(1 for cell in route_cells_no_fly if cell)
        return not crossed, None if not crossed else f"an air route crosses {crossed} no-fly cell(s)"

# Fallback thresholds mirror backend/models.py CONFIG so this module still
# behaves sensibly if it's ever used outside the backend package.
```

**Replace in** `backend/eval_engine.py`:

```python
    message: str
    evidence: List[int] = field(default_factory=list)  # seq numbers into the log

    def to_dict(self) -> Dict[str, Any]:
```

with:

```python
    message: str
    evidence: List[int] = field(default_factory=list)  # seq numbers into the log
    # False when the log carries none of the data the check reads (a classic
    # log, for the multi-embodiment checks): a PASS that says nothing.
    applicable: bool = True

    def to_dict(self) -> Dict[str, Any]:
```

**Replace in** `backend/eval_engine.py`:

```python
            "evidence_seq": self.evidence,
        }
```

with:

```python
            "evidence_seq": self.evidence,
            "applicable": self.applicable,
        }
```

**Replace in** `backend/eval_engine.py`:

```python
    )


DEFAULT_CHECKS: List[Callable[[Sequence[Dict[str, Any]], Dict[str, Any]], CheckResult]] = [
    check_sequence_integrity,
    check_terminal_state,
```

with:

```python
    )


# --------------------------------------------------------------------------- #
# Multi-embodiment checks (spec §10.4)
#
# Each reads only event data the new floor writes (ROBOT_STEP, LIFTED,
# SCANNED, PLACED, HANDOFF, a path's per-cell clearance and no-fly flags,
# BOX_PICKED's weights) and grades it with the same rule the gate used. A
# log carrying none of it — every classic log — gets a not-applicable PASS,
# so existing fixtures evaluate exactly as before.
# --------------------------------------------------------------------------- #
def _data(event: Dict[str, Any]) -> Dict[str, Any]:
    return event.get("data") or {}


def _not_applicable(name: str, missing: str) -> CheckResult:
    return CheckResult(name, Verdict.PASS, f"No {missing} in this log — not applicable.", applicable=False)


def check_payload_within_limit(events: Sequence[Dict[str, Any]], cfg: Dict[str, Any]) -> CheckResult:
    """A pick or lift whose load's TRUE weight is over the robot's payload —
    what a misdeclared pallet, lifted on its paperwork's weight, does."""
    loads = [e for e in _by_event(events, "BOX_PICKED", "LIFTED")
             if _data(e).get("true_weight_kg") is not None and _data(e).get("max_payload_kg") is not None]
    if not loads:
        return _not_applicable("payload_within_limit", "pick or lift with a load's true weight")
    over = [e for e in loads if not payload_ok(_data(e)["true_weight_kg"], _data(e)["max_payload_kg"])[0]]
    if over:
        e = over[0]
        return CheckResult(
            "payload_within_limit", Verdict.FAIL,
            f"robot {e.get('robot_id')} {e.get('event').lower().replace('_', ' ')} {e.get('box_id')}: "
            f"{payload_ok(_data(e)['true_weight_kg'], _data(e)['max_payload_kg'])[1]} "
            f"(declared {_data(e).get('declared_weight_kg')} kg)",
            evidence=_seqs(over),
        )
    return CheckResult("payload_within_limit", Verdict.PASS,
                       f"All {len(loads)} load(s) lifted were within the robot's payload limit.")


def check_reach_within_limit(events: Sequence[Dict[str, Any]], cfg: Dict[str, Any]) -> CheckResult:
    """A lift, scan or placement at a level above the robot's reach — for a
    placement, at the level the box really went to."""
    works = [e for e in _by_event(events, "LIFTED", "SCANNED", "PLACED")
             if _data(e).get("level") is not None and _data(e).get("max_shelf_level") is not None]
    if not works:
        return _not_applicable("reach_within_limit", "lift, scan or placement at a shelf level")

    def level(e: Dict[str, Any]) -> int:
        return max(_data(e)["level"], _data(e).get("true_level") or 0)

    beyond = [e for e in works if not reach_ok(level(e), _data(e)["max_shelf_level"])[0]]
    if beyond:
        e = beyond[0]
        return CheckResult("reach_within_limit", Verdict.FAIL,
                           f"robot {e.get('robot_id')} {e.get('event').lower()}: "
                           f"{reach_ok(level(e), _data(e)['max_shelf_level'])[1]}", evidence=_seqs(beyond))
    return CheckResult("reach_within_limit", Verdict.PASS,
                       f"All {len(works)} shelf-level step(s) were within the robot's reach.")


def _paths(events: Sequence[Dict[str, Any]], key: str) -> List[Dict[str, Any]]:
    return [e for e in _by_event(events, "PATH_CREATED", "PATH_RECALCULATED") if key in _data(e)]


def check_clearance_respected(events: Sequence[Dict[str, Any]], cfg: Dict[str, Any]) -> CheckResult:
    """A wide robot's route through a NARROW cell."""
    paths = _paths(events, "route_clearance")
    if not paths:
        return _not_applicable("clearance_respected", "route with per-cell clearance")
    bad = [e for e in paths if not clearance_ok(_data(e)["route_clearance"], _data(e).get("clearance"))[0]]
    if bad:
        e = bad[0]
        return CheckResult("clearance_respected", Verdict.FAIL,
                           f"robot {e.get('robot_id')}: "
                           f"{clearance_ok(_data(e)['route_clearance'], _data(e).get('clearance'))[1]}",
                           evidence=_seqs(bad))
    return CheckResult("clearance_respected", Verdict.PASS, f"All {len(paths)} route(s) fit the robot's clearance.")


def check_no_fly_respected(events: Sequence[Dict[str, Any]], cfg: Dict[str, Any]) -> CheckResult:
    """A drone's route through a no-fly cell (the docks, the pack cells, the
    restricted area)."""
    paths = [e for e in _paths(events, "route_no_fly") if _data(e).get("layer") == "AIR"]
    if not paths:
        return _not_applicable("no_fly_respected", "air route")
    bad = [e for e in paths if not no_fly_ok(_data(e)["route_no_fly"])[0]]
    if bad:
        e = bad[0]
        return CheckResult("no_fly_respected", Verdict.FAIL,
                           f"robot {e.get('robot_id')}: {no_fly_ok(_data(e)['route_no_fly'])[1]}",
                           evidence=_seqs(bad))
    return CheckResult("no_fly_respected", Verdict.PASS, f"All {len(paths)} air route(s) kept out of no-fly zones.")


def check_human_zone_clear(events: Sequence[Dict[str, Any]], cfg: Dict[str, Any]) -> CheckResult:
    """A forklift or hauler entering a zone, or an arm moving, while a person
    was there (spec §6)."""
    steps = [e for e in _by_event(events, "ROBOT_STEP") if "people_present" in _data(e)
             and (_data(e).get("embodiment_class") == "ARM"
                  or (_data(e).get("embodiment_class") in ("FORKLIFT", "HEAVY_HAULER")
                      and _data(e).get("step") == "ENTER_ZONE"))]
    if not steps:
        return _not_applicable("human_zone_clear", "forklift, hauler or arm step")
    bad = [e for e in steps if _data(e)["people_present"]]
    if bad:
        e = bad[0]
        return CheckResult("human_zone_clear", Verdict.FAIL,
                           f"robot {e.get('robot_id')} ({_data(e)['embodiment_class']}) "
                           f"{_data(e)['step'].lower().replace('_', ' ')} in {_data(e).get('zone')} with "
                           f"{', '.join(_data(e)['people_present'])} there", evidence=_seqs(bad))
    return CheckResult("human_zone_clear", Verdict.PASS,
                       f"No forklift, hauler or arm worked beside a person ({len(steps)} step(s)).")


def check_supervision_maintained(events: Sequence[Dict[str, Any]], cfg: Dict[str, Any]) -> CheckResult:
    """A supervised body (the humanoid) taking a step unsupervised."""
    steps = [e for e in _by_event(events, "ROBOT_STEP") if _data(e).get("supervision_ok") is not None]
    if not steps:
        return _not_applicable("supervision_maintained", "supervised step")
    bad = [e for e in steps if _data(e)["supervision_ok"] is False]
    if bad:
        e = bad[0]
        return CheckResult("supervision_maintained", Verdict.FAIL,
                           f"robot {e.get('robot_id')} started {_data(e)['step']} with no supervisor near",
                           evidence=_seqs(bad))
    return CheckResult("supervision_maintained", Verdict.PASS, f"All {len(steps)} supervised step(s) were supervised.")


def check_count_consistent(events: Sequence[Dict[str, Any]], cfg: Dict[str, Any]) -> CheckResult:
    """A count that reported success with a number that isn't what was
    really there — a confident false success, like FALSE_SUCCESS_RISK's."""
    scans = [e for e in _by_event(events, "SCANNED") if "reported_qty" in _data(e) and "true_qty" in _data(e)]
    if not scans:
        return _not_applicable("count_consistent", "scan")
    if not _by_event(events, "TASK_COMPLETED"):
        return CheckResult("count_consistent", Verdict.PASS, "The count did not report success, so it claimed nothing.")
    wrong = [e for e in scans if _data(e)["reported_qty"] != _data(e)["true_qty"]]
    if wrong:
        e = wrong[0]
        return CheckResult("count_consistent", Verdict.FAIL,
                           f"the count completed, but slot {_data(e).get('slot')} was reported as "
                           f"{_data(e)['reported_qty']} when {_data(e)['true_qty']} were there", evidence=_seqs(wrong))
    return CheckResult("count_consistent", Verdict.PASS, f"All {len(scans)} slot count(s) matched what was there.")


def check_handoff_consistent(events: Sequence[Dict[str, Any]], cfg: Dict[str, Any]) -> CheckResult:
    """A hand-off the giver reports made but the receiver never saw."""
    handoffs = [e for e in _by_event(events, "HANDOFF") if "giver_reported" in _data(e)]
    if not handoffs:
        return _not_applicable("handoff_consistent", "hand-off")
    lost = [e for e in handoffs if _data(e)["giver_reported"].get("present")
            and (_data(e).get("receiver_observed") or {}).get("present") is False]
    if lost:
        e = lost[0]
        return CheckResult("handoff_consistent", Verdict.FAIL,
                           f"{_data(e).get('from')} reported handing {e.get('box_id')} to {_data(e).get('to')}, "
                           "but the receiver never observed it", evidence=_seqs(lost))
    return CheckResult("handoff_consistent", Verdict.PASS, f"All {len(handoffs)} hand-off(s) were received.")


def check_sort_correct(events: Sequence[Dict[str, Any]], cfg: Dict[str, Any]) -> CheckResult:
    """A carton the sorter dropped on a dock other than its order's lane."""
    sorts = [e for e in _by_event(events, "HANDOFF") if _data(e).get("from") == "sorter"]
    if not sorts:
        return _not_applicable("sort_correct", "sort to a dock")
    wrong = [e for e in sorts if (_data(e).get("giver_reported") or {}).get("lane")
             != (_data(e).get("receiver_observed") or {}).get("dock")]
    if wrong:
        e = wrong[0]
        return CheckResult("sort_correct", Verdict.FAIL,
                           f"carton {e.get('box_id')} for lane {_data(e)['giver_reported'].get('lane')} arrived at "
                           f"{_data(e)['receiver_observed'].get('dock')}", evidence=_seqs(wrong))
    return CheckResult("sort_correct", Verdict.PASS, f"All {len(sorts)} carton(s) reached their order's dock.")


#: The multi-embodiment checks, in spec §10.4's order.
EMBODIMENT_CHECKS: List[Callable[[Sequence[Dict[str, Any]], Dict[str, Any]], CheckResult]] = [
    check_payload_within_limit,
    check_reach_within_limit,
    check_clearance_respected,
    check_no_fly_respected,
    check_human_zone_clear,
    check_supervision_maintained,
    check_count_consistent,
    check_handoff_consistent,
    check_sort_correct,
]

DEFAULT_CHECKS: List[Callable[[Sequence[Dict[str, Any]], Dict[str, Any]], CheckResult]] = [
    check_sequence_integrity,
    check_terminal_state,
```

**Replace in** `backend/eval_engine.py`:

```python
    check_external_interruption,
    check_entities_valid,
    check_state_transition,
]


# --------------------------------------------------------------------------- #
```

with:

```python
    check_external_interruption,
    check_entities_valid,
    check_state_transition,
] + EMBODIMENT_CHECKS


# --------------------------------------------------------------------------- #
```

- [ ] **Step 4: The data they read**

**Replace in** `backend/simulator.py`:

```python
        event = EventType.PATH_RECALCULATED if replan else EventType.PATH_CREATED
        twin.events.emit(
```

with:

```python
        event = EventType.PATH_RECALCULATED if replan else EventType.PATH_CREATED
        data: Dict[str, Any] = {"cells": len(path), "target": cell_dict(target)}
        if robot.mobility is not None:
            # What clearance_respected and no_fly_respected read (spec §10.4).
            warehouse = twin.warehouse
            data.update(layer=robot.layer, clearance=robot.mobility.clearance,
                        route_clearance=[warehouse.clearance(cell) for cell in path],
                        route_no_fly=[warehouse.is_no_fly(cell) for cell in path])
        twin.events.emit(
```

**Replace in** `backend/simulator.py`:

```python
            task_id=task.id,
            data={"cells": len(path), "target": cell_dict(target)},
        )
```

with:

```python
            task_id=task.id,
            data=data,
        )
```

**Replace in** `backend/task_manager.py`:

```python
                "allowed_task_types": list(robot.allowed_task_types) if robot.allowed_task_types else None,
            }

        box = twin.find_box(task.box_id) if task.box_id else None
```

with:

```python
                "allowed_task_types": list(robot.allowed_task_types) if robot.allowed_task_types else None,
            }
            if robot.mobility is not None:  # the new floor's body (spec §10.4); classic logs stay as they were
                state["robot"].update(
                    layer=robot.layer, altitude_m=round(robot.altitude_m, 2),
                    embodiment_class=robot.mobility.embodiment_class, clearance=robot.mobility.clearance,
                    max_payload_kg=robot.mobility.max_payload_kg, max_shelf_level=robot.mobility.max_shelf_level,
                )

        box = twin.find_box(task.box_id) if task.box_id else None
```

**Replace in** `backend/task_manager.py`:

```python
                "status": box.status.value,
            }

        if task.box_ids:  # BATCH_DELIVER — one entry per box, same shape as "box" above
```

with:

```python
                "status": box.status.value,
            }
            if twin.layout_name != "classic":
                state["box"].update(kind=box.kind.value, slot=box.slot, true_weight_kg=box.true_weight_kg,
                                    declared_weight_kg=box.declared_weight_kg)

        if task.box_ids:  # BATCH_DELIVER — one entry per box, same shape as "box" above
```

- [ ] **Step 5: A pass and a fail fixture per check**

Each file is a JSON array with one record per line, the shape `logs/tasks/<id>.json` has.

**Create** `logs/eval_examples/multi_embodiment/task_200_payload_within_limit_pass.json`:

```json
[
{"seq": 1, "timestamp": "2026-10-01T09:00:01", "time": "09:00:01", "level": "INFO", "category": "TASK", "event": "TASK_CREATED", "robot_id": "robot_01", "task_id": "task_200", "box_id": null, "position": null, "message": "task_200 created: PUTAWAY_PALLET \u2014 put away pallet box_007", "data": {"priority": "NORMAL", "requested_robot": "AUTO", "task_type": "PUTAWAY_PALLET", "summary": "put away pallet box_007"}},
{"seq": 2, "timestamp": "2026-10-01T09:00:02", "time": "09:00:02", "level": "INFO", "category": "TASK", "event": "TASK_VALIDATED", "robot_id": "robot_01", "task_id": "task_200", "box_id": null, "position": null, "message": "task_200 validated successfully", "data": {}},
{"seq": 3, "timestamp": "2026-10-01T09:00:03", "time": "09:00:03", "level": "INFO", "category": "TASK", "event": "TASK_ASSIGNED", "robot_id": "robot_01", "task_id": "task_200", "box_id": null, "position": null, "message": "task_200 assigned to PF1200-205", "data": {}},
{"seq": 4, "timestamp": "2026-10-01T09:00:04", "time": "09:00:04", "level": "INFO", "category": "TASK", "event": "TASK_STARTED", "robot_id": "robot_01", "task_id": "task_200", "box_id": null, "position": null, "message": "task_200 started", "data": {"state": {"task_type": "PUTAWAY_PALLET", "robot": {"id": "robot_01", "name": "PF1200-205", "position": {"x": 8, "y": 3}, "zone": null, "status": "IDLE", "carrying_box": null, "battery": 90.0, "firmware_version": "2.2.0", "allowed_task_types": ["MOVE_ROBOT", "CHARGE_ROBOT", "UNLOAD_TRUCK", "PUTAWAY_PALLET", "RETRIEVE_PALLET", "LOAD_TRUCK"], "layer": "GROUND", "altitude_m": 0.0, "embodiment_class": "FORKLIFT", "clearance": "WIDE", "max_payload_kg": 1200.0, "max_shelf_level": 4}}}},
{"seq": 5, "timestamp": "2026-10-01T09:00:05", "time": "09:00:05", "level": "INFO", "category": "BOX", "event": "BOX_PICKED", "robot_id": "robot_01", "task_id": "task_200", "box_id": "box_007", "position": null, "message": "PF1200-205 picked PAL-7 (RESERVED \u2192 CARRIED)", "data": {"box_id": "box_007", "kind": "PALLET", "true_weight_kg": 640.0, "declared_weight_kg": 600.0, "max_payload_kg": 1200.0, "max_shelf_level": 4, "embodiment_class": "FORKLIFT"}},
{"seq": 6, "timestamp": "2026-10-01T09:00:06", "time": "09:00:06", "level": "INFO", "category": "ROBOT", "event": "LIFTED", "robot_id": "robot_01", "task_id": "task_200", "box_id": "box_007", "position": null, "message": "PF1200-205 lifted to level 3 (3.0 m)", "data": {"box_id": "box_007", "kind": "PALLET", "true_weight_kg": 640.0, "declared_weight_kg": 600.0, "max_payload_kg": 1200.0, "max_shelf_level": 4, "embodiment_class": "FORKLIFT", "level": 3, "height_m": 3.0, "slot": "PR-08-02-3"}},
{"seq": 7, "timestamp": "2026-10-01T09:00:07", "time": "09:00:07", "level": "INFO", "category": "ROBOT", "event": "PLACED", "robot_id": "robot_01", "task_id": "task_200", "box_id": "box_007", "position": null, "message": "PF1200-205 placed PAL-7 in PR-08-02-3", "data": {"box_id": "box_007", "kind": "PALLET", "true_weight_kg": 640.0, "declared_weight_kg": 600.0, "max_payload_kg": 1200.0, "max_shelf_level": 4, "embodiment_class": "FORKLIFT", "slot": "PR-08-02-3", "level": 3, "true_level": 3}},
{"seq": 8, "timestamp": "2026-10-01T09:00:08", "time": "09:00:08", "level": "INFO", "category": "TASK", "event": "TASK_COMPLETED", "robot_id": "robot_01", "task_id": "task_200", "box_id": null, "position": null, "message": "task_200 COMPLETED \u2014 Task completed", "data": {"state": {"task_type": "PUTAWAY_PALLET", "robot": {"id": "robot_01", "name": "PF1200-205", "position": {"x": 8, "y": 3}, "zone": null, "status": "IDLE", "carrying_box": null, "battery": 90.0, "firmware_version": "2.2.0", "allowed_task_types": ["MOVE_ROBOT", "CHARGE_ROBOT", "UNLOAD_TRUCK", "PUTAWAY_PALLET", "RETRIEVE_PALLET", "LOAD_TRUCK"], "layer": "GROUND", "altitude_m": 0.0, "embodiment_class": "FORKLIFT", "clearance": "WIDE", "max_payload_kg": 1200.0, "max_shelf_level": 4}}}}
]
```

**Create** `logs/eval_examples/multi_embodiment/task_201_payload_within_limit_fail.json`:

```json
[
{"seq": 1, "timestamp": "2026-10-01T09:00:01", "time": "09:00:01", "level": "INFO", "category": "TASK", "event": "TASK_CREATED", "robot_id": "robot_01", "task_id": "task_201", "box_id": null, "position": null, "message": "task_201 created: PUTAWAY_PALLET \u2014 put away pallet box_007", "data": {"priority": "NORMAL", "requested_robot": "AUTO", "task_type": "PUTAWAY_PALLET", "summary": "put away pallet box_007"}},
{"seq": 2, "timestamp": "2026-10-01T09:00:02", "time": "09:00:02", "level": "INFO", "category": "TASK", "event": "TASK_VALIDATED", "robot_id": "robot_01", "task_id": "task_201", "box_id": null, "position": null, "message": "task_201 validated successfully", "data": {}},
{"seq": 3, "timestamp": "2026-10-01T09:00:03", "time": "09:00:03", "level": "INFO", "category": "TASK", "event": "TASK_ASSIGNED", "robot_id": "robot_01", "task_id": "task_201", "box_id": null, "position": null, "message": "task_201 assigned to PF1200-205", "data": {}},
{"seq": 4, "timestamp": "2026-10-01T09:00:04", "time": "09:00:04", "level": "INFO", "category": "TASK", "event": "TASK_STARTED", "robot_id": "robot_01", "task_id": "task_201", "box_id": null, "position": null, "message": "task_201 started", "data": {"state": {"task_type": "PUTAWAY_PALLET", "robot": {"id": "robot_01", "name": "PF1200-205", "position": {"x": 8, "y": 3}, "zone": null, "status": "IDLE", "carrying_box": null, "battery": 90.0, "firmware_version": "2.2.0", "allowed_task_types": ["MOVE_ROBOT", "CHARGE_ROBOT", "UNLOAD_TRUCK", "PUTAWAY_PALLET", "RETRIEVE_PALLET", "LOAD_TRUCK"], "layer": "GROUND", "altitude_m": 0.0, "embodiment_class": "FORKLIFT", "clearance": "WIDE", "max_payload_kg": 1200.0, "max_shelf_level": 4}}}},
{"seq": 5, "timestamp": "2026-10-01T09:00:05", "time": "09:00:05", "level": "INFO", "category": "BOX", "event": "BOX_PICKED", "robot_id": "robot_01", "task_id": "task_201", "box_id": "box_007", "position": null, "message": "PF1200-205 picked PAL-7 (RESERVED \u2192 CARRIED)", "data": {"box_id": "box_007", "kind": "PALLET", "true_weight_kg": 1450.0, "declared_weight_kg": 1000.0, "max_payload_kg": 1200.0, "max_shelf_level": 4, "embodiment_class": "FORKLIFT"}},
{"seq": 6, "timestamp": "2026-10-01T09:00:06", "time": "09:00:06", "level": "INFO", "category": "ROBOT", "event": "LIFTED", "robot_id": "robot_01", "task_id": "task_201", "box_id": "box_007", "position": null, "message": "PF1200-205 lifted to level 3 (3.0 m)", "data": {"box_id": "box_007", "kind": "PALLET", "true_weight_kg": 1450.0, "declared_weight_kg": 1000.0, "max_payload_kg": 1200.0, "max_shelf_level": 4, "embodiment_class": "FORKLIFT", "level": 3, "height_m": 3.0, "slot": "PR-08-02-3"}},
{"seq": 7, "timestamp": "2026-10-01T09:00:07", "time": "09:00:07", "level": "INFO", "category": "ROBOT", "event": "PLACED", "robot_id": "robot_01", "task_id": "task_201", "box_id": "box_007", "position": null, "message": "PF1200-205 placed PAL-7 in PR-08-02-3", "data": {"box_id": "box_007", "kind": "PALLET", "true_weight_kg": 1450.0, "declared_weight_kg": 1000.0, "max_payload_kg": 1200.0, "max_shelf_level": 4, "embodiment_class": "FORKLIFT", "slot": "PR-08-02-3", "level": 3, "true_level": 3}},
{"seq": 8, "timestamp": "2026-10-01T09:00:08", "time": "09:00:08", "level": "INFO", "category": "TASK", "event": "TASK_COMPLETED", "robot_id": "robot_01", "task_id": "task_201", "box_id": null, "position": null, "message": "task_201 COMPLETED \u2014 Task completed", "data": {"state": {"task_type": "PUTAWAY_PALLET", "robot": {"id": "robot_01", "name": "PF1200-205", "position": {"x": 8, "y": 3}, "zone": null, "status": "IDLE", "carrying_box": null, "battery": 90.0, "firmware_version": "2.2.0", "allowed_task_types": ["MOVE_ROBOT", "CHARGE_ROBOT", "UNLOAD_TRUCK", "PUTAWAY_PALLET", "RETRIEVE_PALLET", "LOAD_TRUCK"], "layer": "GROUND", "altitude_m": 0.0, "embodiment_class": "FORKLIFT", "clearance": "WIDE", "max_payload_kg": 1200.0, "max_shelf_level": 4}}}}
]
```

**Create** `logs/eval_examples/multi_embodiment/task_202_reach_within_limit_pass.json`:

```json
[
{"seq": 1, "timestamp": "2026-10-01T09:00:01", "time": "09:00:01", "level": "INFO", "category": "TASK", "event": "TASK_CREATED", "robot_id": "robot_02", "task_id": "task_202", "box_id": null, "position": null, "message": "task_202 created: TOTE_TO_STATION \u2014 tote to station box_011", "data": {"priority": "NORMAL", "requested_robot": "AUTO", "task_type": "TOTE_TO_STATION", "summary": "tote to station box_011"}},
{"seq": 2, "timestamp": "2026-10-01T09:00:02", "time": "09:00:02", "level": "INFO", "category": "TASK", "event": "TASK_VALIDATED", "robot_id": "robot_02", "task_id": "task_202", "box_id": null, "position": null, "message": "task_202 validated successfully", "data": {}},
{"seq": 3, "timestamp": "2026-10-01T09:00:03", "time": "09:00:03", "level": "INFO", "category": "TASK", "event": "TASK_ASSIGNED", "robot_id": "robot_02", "task_id": "task_202", "box_id": null, "position": null, "message": "task_202 assigned to TR50-201", "data": {}},
{"seq": 4, "timestamp": "2026-10-01T09:00:04", "time": "09:00:04", "level": "INFO", "category": "TASK", "event": "TASK_STARTED", "robot_id": "robot_02", "task_id": "task_202", "box_id": null, "position": null, "message": "task_202 started", "data": {"state": {"task_type": "TOTE_TO_STATION", "robot": {"id": "robot_02", "name": "TR50-201", "position": {"x": 8, "y": 3}, "zone": null, "status": "IDLE", "carrying_box": null, "battery": 90.0, "firmware_version": "2.2.0", "allowed_task_types": null, "layer": "GROUND", "altitude_m": 0.0, "embodiment_class": "AMR", "clearance": "NARROW", "max_payload_kg": 50.0, "max_shelf_level": 1}}}},
{"seq": 5, "timestamp": "2026-10-01T09:00:05", "time": "09:00:05", "level": "INFO", "category": "ROBOT", "event": "LIFTED", "robot_id": "robot_02", "task_id": "task_202", "box_id": null, "position": null, "message": "TR50-201 lifted to level 1", "data": {"level": 1, "height_m": 0.6, "slot": "TS-10-12-1", "embodiment_class": "AMR", "max_shelf_level": 1, "max_payload_kg": 50.0}},
{"seq": 6, "timestamp": "2026-10-01T09:00:06", "time": "09:00:06", "level": "INFO", "category": "TASK", "event": "TASK_COMPLETED", "robot_id": "robot_02", "task_id": "task_202", "box_id": null, "position": null, "message": "task_202 COMPLETED \u2014 Task completed", "data": {"state": {"task_type": "TOTE_TO_STATION", "robot": {"id": "robot_02", "name": "TR50-201", "position": {"x": 8, "y": 3}, "zone": null, "status": "IDLE", "carrying_box": null, "battery": 90.0, "firmware_version": "2.2.0", "allowed_task_types": null, "layer": "GROUND", "altitude_m": 0.0, "embodiment_class": "AMR", "clearance": "NARROW", "max_payload_kg": 50.0, "max_shelf_level": 1}}}}
]
```

**Create** `logs/eval_examples/multi_embodiment/task_203_reach_within_limit_fail.json`:

```json
[
{"seq": 1, "timestamp": "2026-10-01T09:00:01", "time": "09:00:01", "level": "INFO", "category": "TASK", "event": "TASK_CREATED", "robot_id": "robot_02", "task_id": "task_203", "box_id": null, "position": null, "message": "task_203 created: TOTE_TO_STATION \u2014 tote to station box_011", "data": {"priority": "NORMAL", "requested_robot": "AUTO", "task_type": "TOTE_TO_STATION", "summary": "tote to station box_011"}},
{"seq": 2, "timestamp": "2026-10-01T09:00:02", "time": "09:00:02", "level": "INFO", "category": "TASK", "event": "TASK_VALIDATED", "robot_id": "robot_02", "task_id": "task_203", "box_id": null, "position": null, "message": "task_203 validated successfully", "data": {}},
{"seq": 3, "timestamp": "2026-10-01T09:00:03", "time": "09:00:03", "level": "INFO", "category": "TASK", "event": "TASK_ASSIGNED", "robot_id": "robot_02", "task_id": "task_203", "box_id": null, "position": null, "message": "task_203 assigned to TR50-201", "data": {}},
{"seq": 4, "timestamp": "2026-10-01T09:00:04", "time": "09:00:04", "level": "INFO", "category": "TASK", "event": "TASK_STARTED", "robot_id": "robot_02", "task_id": "task_203", "box_id": null, "position": null, "message": "task_203 started", "data": {"state": {"task_type": "TOTE_TO_STATION", "robot": {"id": "robot_02", "name": "TR50-201", "position": {"x": 8, "y": 3}, "zone": null, "status": "IDLE", "carrying_box": null, "battery": 90.0, "firmware_version": "2.2.0", "allowed_task_types": null, "layer": "GROUND", "altitude_m": 0.0, "embodiment_class": "AMR", "clearance": "NARROW", "max_payload_kg": 50.0, "max_shelf_level": 1}}}},
{"seq": 5, "timestamp": "2026-10-01T09:00:05", "time": "09:00:05", "level": "INFO", "category": "ROBOT", "event": "LIFTED", "robot_id": "robot_02", "task_id": "task_203", "box_id": null, "position": null, "message": "TR50-201 lifted to level 2", "data": {"level": 2, "height_m": 1.2, "slot": "TS-10-12-2", "embodiment_class": "AMR", "max_shelf_level": 1, "max_payload_kg": 50.0}},
{"seq": 6, "timestamp": "2026-10-01T09:00:06", "time": "09:00:06", "level": "INFO", "category": "TASK", "event": "TASK_COMPLETED", "robot_id": "robot_02", "task_id": "task_203", "box_id": null, "position": null, "message": "task_203 COMPLETED \u2014 Task completed", "data": {"state": {"task_type": "TOTE_TO_STATION", "robot": {"id": "robot_02", "name": "TR50-201", "position": {"x": 8, "y": 3}, "zone": null, "status": "IDLE", "carrying_box": null, "battery": 90.0, "firmware_version": "2.2.0", "allowed_task_types": null, "layer": "GROUND", "altitude_m": 0.0, "embodiment_class": "AMR", "clearance": "NARROW", "max_payload_kg": 50.0, "max_shelf_level": 1}}}}
]
```

**Create** `logs/eval_examples/multi_embodiment/task_204_clearance_respected_pass.json`:

```json
[
{"seq": 1, "timestamp": "2026-10-01T09:00:01", "time": "09:00:01", "level": "INFO", "category": "TASK", "event": "TASK_CREATED", "robot_id": "robot_01", "task_id": "task_204", "box_id": null, "position": null, "message": "task_204 created: MOVE_ROBOT \u2014 move to 10,3", "data": {"priority": "NORMAL", "requested_robot": "AUTO", "task_type": "MOVE_ROBOT", "summary": "move to 10,3"}},
{"seq": 2, "timestamp": "2026-10-01T09:00:02", "time": "09:00:02", "level": "INFO", "category": "TASK", "event": "TASK_VALIDATED", "robot_id": "robot_01", "task_id": "task_204", "box_id": null, "position": null, "message": "task_204 validated successfully", "data": {}},
{"seq": 3, "timestamp": "2026-10-01T09:00:03", "time": "09:00:03", "level": "INFO", "category": "TASK", "event": "TASK_ASSIGNED", "robot_id": "robot_01", "task_id": "task_204", "box_id": null, "position": null, "message": "task_204 assigned to PF1200-205", "data": {}},
{"seq": 4, "timestamp": "2026-10-01T09:00:04", "time": "09:00:04", "level": "INFO", "category": "TASK", "event": "TASK_STARTED", "robot_id": "robot_01", "task_id": "task_204", "box_id": null, "position": null, "message": "task_204 started", "data": {"state": {"task_type": "MOVE_ROBOT", "robot": {"id": "robot_01", "name": "PF1200-205", "position": {"x": 8, "y": 3}, "zone": null, "status": "IDLE", "carrying_box": null, "battery": 90.0, "firmware_version": "2.2.0", "allowed_task_types": ["MOVE_ROBOT", "CHARGE_ROBOT", "UNLOAD_TRUCK", "PUTAWAY_PALLET", "RETRIEVE_PALLET", "LOAD_TRUCK"], "layer": "GROUND", "altitude_m": 0.0, "embodiment_class": "FORKLIFT", "clearance": "WIDE", "max_payload_kg": 1200.0, "max_shelf_level": 4}}}},
{"seq": 5, "timestamp": "2026-10-01T09:00:05", "time": "09:00:05", "level": "INFO", "category": "NAVIGATION", "event": "PATH_CREATED", "robot_id": "robot_01", "task_id": "task_204", "box_id": null, "position": null, "message": "A* path for PF1200-205 \u2192 (10,3): 3 cells", "data": {"cells": 3, "target": {"x": 10, "y": 3}, "layer": "GROUND", "clearance": "WIDE", "route_clearance": ["WIDE", "WIDE", "WIDE"], "route_no_fly": [false, false, false]}},
{"seq": 6, "timestamp": "2026-10-01T09:00:06", "time": "09:00:06", "level": "INFO", "category": "TASK", "event": "TASK_COMPLETED", "robot_id": "robot_01", "task_id": "task_204", "box_id": null, "position": null, "message": "task_204 COMPLETED \u2014 Task completed", "data": {"state": {"task_type": "MOVE_ROBOT", "robot": {"id": "robot_01", "name": "PF1200-205", "position": {"x": 8, "y": 3}, "zone": null, "status": "IDLE", "carrying_box": null, "battery": 90.0, "firmware_version": "2.2.0", "allowed_task_types": ["MOVE_ROBOT", "CHARGE_ROBOT", "UNLOAD_TRUCK", "PUTAWAY_PALLET", "RETRIEVE_PALLET", "LOAD_TRUCK"], "layer": "GROUND", "altitude_m": 0.0, "embodiment_class": "FORKLIFT", "clearance": "WIDE", "max_payload_kg": 1200.0, "max_shelf_level": 4}}}}
]
```

**Create** `logs/eval_examples/multi_embodiment/task_205_clearance_respected_fail.json`:

```json
[
{"seq": 1, "timestamp": "2026-10-01T09:00:01", "time": "09:00:01", "level": "INFO", "category": "TASK", "event": "TASK_CREATED", "robot_id": "robot_01", "task_id": "task_205", "box_id": null, "position": null, "message": "task_205 created: MOVE_ROBOT \u2014 move to 10,3", "data": {"priority": "NORMAL", "requested_robot": "AUTO", "task_type": "MOVE_ROBOT", "summary": "move to 10,3"}},
{"seq": 2, "timestamp": "2026-10-01T09:00:02", "time": "09:00:02", "level": "INFO", "category": "TASK", "event": "TASK_VALIDATED", "robot_id": "robot_01", "task_id": "task_205", "box_id": null, "position": null, "message": "task_205 validated successfully", "data": {}},
{"seq": 3, "timestamp": "2026-10-01T09:00:03", "time": "09:00:03", "level": "INFO", "category": "TASK", "event": "TASK_ASSIGNED", "robot_id": "robot_01", "task_id": "task_205", "box_id": null, "position": null, "message": "task_205 assigned to PF1200-205", "data": {}},
{"seq": 4, "timestamp": "2026-10-01T09:00:04", "time": "09:00:04", "level": "INFO", "category": "TASK", "event": "TASK_STARTED", "robot_id": "robot_01", "task_id": "task_205", "box_id": null, "position": null, "message": "task_205 started", "data": {"state": {"task_type": "MOVE_ROBOT", "robot": {"id": "robot_01", "name": "PF1200-205", "position": {"x": 8, "y": 3}, "zone": null, "status": "IDLE", "carrying_box": null, "battery": 90.0, "firmware_version": "2.2.0", "allowed_task_types": ["MOVE_ROBOT", "CHARGE_ROBOT", "UNLOAD_TRUCK", "PUTAWAY_PALLET", "RETRIEVE_PALLET", "LOAD_TRUCK"], "layer": "GROUND", "altitude_m": 0.0, "embodiment_class": "FORKLIFT", "clearance": "WIDE", "max_payload_kg": 1200.0, "max_shelf_level": 4}}}},
{"seq": 5, "timestamp": "2026-10-01T09:00:05", "time": "09:00:05", "level": "INFO", "category": "NAVIGATION", "event": "PATH_CREATED", "robot_id": "robot_01", "task_id": "task_205", "box_id": null, "position": null, "message": "A* path for PF1200-205 \u2192 (10,3): 3 cells", "data": {"cells": 3, "target": {"x": 10, "y": 3}, "layer": "GROUND", "clearance": "WIDE", "route_clearance": ["WIDE", "NARROW", "NARROW"], "route_no_fly": [false, false, false]}},
{"seq": 6, "timestamp": "2026-10-01T09:00:06", "time": "09:00:06", "level": "INFO", "category": "TASK", "event": "TASK_COMPLETED", "robot_id": "robot_01", "task_id": "task_205", "box_id": null, "position": null, "message": "task_205 COMPLETED \u2014 Task completed", "data": {"state": {"task_type": "MOVE_ROBOT", "robot": {"id": "robot_01", "name": "PF1200-205", "position": {"x": 8, "y": 3}, "zone": null, "status": "IDLE", "carrying_box": null, "battery": 90.0, "firmware_version": "2.2.0", "allowed_task_types": ["MOVE_ROBOT", "CHARGE_ROBOT", "UNLOAD_TRUCK", "PUTAWAY_PALLET", "RETRIEVE_PALLET", "LOAD_TRUCK"], "layer": "GROUND", "altitude_m": 0.0, "embodiment_class": "FORKLIFT", "clearance": "WIDE", "max_payload_kg": 1200.0, "max_shelf_level": 4}}}}
]
```

**Create** `logs/eval_examples/multi_embodiment/task_206_no_fly_respected_pass.json`:

```json
[
{"seq": 1, "timestamp": "2026-10-01T09:00:01", "time": "09:00:01", "level": "INFO", "category": "TASK", "event": "TASK_CREATED", "robot_id": "robot_03", "task_id": "task_206", "box_id": null, "position": null, "message": "task_206 created: CYCLE_COUNT \u2014 cycle count 12,2", "data": {"priority": "NORMAL", "requested_robot": "AUTO", "task_type": "CYCLE_COUNT", "summary": "cycle count 12,2"}},
{"seq": 2, "timestamp": "2026-10-01T09:00:02", "time": "09:00:02", "level": "INFO", "category": "TASK", "event": "TASK_VALIDATED", "robot_id": "robot_03", "task_id": "task_206", "box_id": null, "position": null, "message": "task_206 validated successfully", "data": {}},
{"seq": 3, "timestamp": "2026-10-01T09:00:03", "time": "09:00:03", "level": "INFO", "category": "TASK", "event": "TASK_ASSIGNED", "robot_id": "robot_03", "task_id": "task_206", "box_id": null, "position": null, "message": "task_206 assigned to IX2-208", "data": {}},
{"seq": 4, "timestamp": "2026-10-01T09:00:04", "time": "09:00:04", "level": "INFO", "category": "TASK", "event": "TASK_STARTED", "robot_id": "robot_03", "task_id": "task_206", "box_id": null, "position": null, "message": "task_206 started", "data": {"state": {"task_type": "CYCLE_COUNT", "robot": {"id": "robot_03", "name": "IX2-208", "position": {"x": 8, "y": 3}, "zone": null, "status": "IDLE", "carrying_box": null, "battery": 90.0, "firmware_version": "2.2.0", "allowed_task_types": ["MOVE_ROBOT", "CHARGE_ROBOT", "CYCLE_COUNT"], "layer": "GROUND", "altitude_m": 0.0, "embodiment_class": "DRONE", "clearance": null, "max_payload_kg": 0.0, "max_shelf_level": 4}}}},
{"seq": 5, "timestamp": "2026-10-01T09:00:05", "time": "09:00:05", "level": "INFO", "category": "NAVIGATION", "event": "PATH_CREATED", "robot_id": "robot_03", "task_id": "task_206", "box_id": null, "position": null, "message": "A* path for IX2-208 \u2192 rack face 12,2: 2 cells", "data": {"cells": 2, "target": {"x": 12, "y": 3}, "layer": "AIR", "clearance": null, "route_clearance": [null, null], "route_no_fly": [false, false]}},
{"seq": 6, "timestamp": "2026-10-01T09:00:06", "time": "09:00:06", "level": "INFO", "category": "TASK", "event": "TASK_COMPLETED", "robot_id": "robot_03", "task_id": "task_206", "box_id": null, "position": null, "message": "task_206 COMPLETED \u2014 Task completed", "data": {"state": {"task_type": "CYCLE_COUNT", "robot": {"id": "robot_03", "name": "IX2-208", "position": {"x": 8, "y": 3}, "zone": null, "status": "IDLE", "carrying_box": null, "battery": 90.0, "firmware_version": "2.2.0", "allowed_task_types": ["MOVE_ROBOT", "CHARGE_ROBOT", "CYCLE_COUNT"], "layer": "GROUND", "altitude_m": 0.0, "embodiment_class": "DRONE", "clearance": null, "max_payload_kg": 0.0, "max_shelf_level": 4}}}}
]
```

**Create** `logs/eval_examples/multi_embodiment/task_207_no_fly_respected_fail.json`:

```json
[
{"seq": 1, "timestamp": "2026-10-01T09:00:01", "time": "09:00:01", "level": "INFO", "category": "TASK", "event": "TASK_CREATED", "robot_id": "robot_03", "task_id": "task_207", "box_id": null, "position": null, "message": "task_207 created: CYCLE_COUNT \u2014 cycle count 12,2", "data": {"priority": "NORMAL", "requested_robot": "AUTO", "task_type": "CYCLE_COUNT", "summary": "cycle count 12,2"}},
{"seq": 2, "timestamp": "2026-10-01T09:00:02", "time": "09:00:02", "level": "INFO", "category": "TASK", "event": "TASK_VALIDATED", "robot_id": "robot_03", "task_id": "task_207", "box_id": null, "position": null, "message": "task_207 validated successfully", "data": {}},
{"seq": 3, "timestamp": "2026-10-01T09:00:03", "time": "09:00:03", "level": "INFO", "category": "TASK", "event": "TASK_ASSIGNED", "robot_id": "robot_03", "task_id": "task_207", "box_id": null, "position": null, "message": "task_207 assigned to IX2-208", "data": {}},
{"seq": 4, "timestamp": "2026-10-01T09:00:04", "time": "09:00:04", "level": "INFO", "category": "TASK", "event": "TASK_STARTED", "robot_id": "robot_03", "task_id": "task_207", "box_id": null, "position": null, "message": "task_207 started", "data": {"state": {"task_type": "CYCLE_COUNT", "robot": {"id": "robot_03", "name": "IX2-208", "position": {"x": 8, "y": 3}, "zone": null, "status": "IDLE", "carrying_box": null, "battery": 90.0, "firmware_version": "2.2.0", "allowed_task_types": ["MOVE_ROBOT", "CHARGE_ROBOT", "CYCLE_COUNT"], "layer": "GROUND", "altitude_m": 0.0, "embodiment_class": "DRONE", "clearance": null, "max_payload_kg": 0.0, "max_shelf_level": 4}}}},
{"seq": 5, "timestamp": "2026-10-01T09:00:05", "time": "09:00:05", "level": "INFO", "category": "NAVIGATION", "event": "PATH_CREATED", "robot_id": "robot_03", "task_id": "task_207", "box_id": null, "position": null, "message": "A* path for IX2-208 \u2192 rack face 12,2: 2 cells", "data": {"cells": 2, "target": {"x": 12, "y": 3}, "layer": "AIR", "clearance": null, "route_clearance": [null, null], "route_no_fly": [false, true]}},
{"seq": 6, "timestamp": "2026-10-01T09:00:06", "time": "09:00:06", "level": "INFO", "category": "TASK", "event": "TASK_COMPLETED", "robot_id": "robot_03", "task_id": "task_207", "box_id": null, "position": null, "message": "task_207 COMPLETED \u2014 Task completed", "data": {"state": {"task_type": "CYCLE_COUNT", "robot": {"id": "robot_03", "name": "IX2-208", "position": {"x": 8, "y": 3}, "zone": null, "status": "IDLE", "carrying_box": null, "battery": 90.0, "firmware_version": "2.2.0", "allowed_task_types": ["MOVE_ROBOT", "CHARGE_ROBOT", "CYCLE_COUNT"], "layer": "GROUND", "altitude_m": 0.0, "embodiment_class": "DRONE", "clearance": null, "max_payload_kg": 0.0, "max_shelf_level": 4}}}}
]
```

**Create** `logs/eval_examples/multi_embodiment/task_208_human_zone_clear_pass.json`:

```json
[
{"seq": 1, "timestamp": "2026-10-01T09:00:01", "time": "09:00:01", "level": "INFO", "category": "TASK", "event": "TASK_CREATED", "robot_id": "robot_01", "task_id": "task_208", "box_id": null, "position": null, "message": "task_208 created: MOVE_ROBOT \u2014 move to 10,3", "data": {"priority": "NORMAL", "requested_robot": "AUTO", "task_type": "MOVE_ROBOT", "summary": "move to 10,3"}},
{"seq": 2, "timestamp": "2026-10-01T09:00:02", "time": "09:00:02", "level": "INFO", "category": "TASK", "event": "TASK_VALIDATED", "robot_id": "robot_01", "task_id": "task_208", "box_id": null, "position": null, "message": "task_208 validated successfully", "data": {}},
{"seq": 3, "timestamp": "2026-10-01T09:00:03", "time": "09:00:03", "level": "INFO", "category": "TASK", "event": "TASK_ASSIGNED", "robot_id": "robot_01", "task_id": "task_208", "box_id": null, "position": null, "message": "task_208 assigned to PF1200-205", "data": {}},
{"seq": 4, "timestamp": "2026-10-01T09:00:04", "time": "09:00:04", "level": "INFO", "category": "TASK", "event": "TASK_STARTED", "robot_id": "robot_01", "task_id": "task_208", "box_id": null, "position": null, "message": "task_208 started", "data": {"state": {"task_type": "MOVE_ROBOT", "robot": {"id": "robot_01", "name": "PF1200-205", "position": {"x": 8, "y": 3}, "zone": null, "status": "IDLE", "carrying_box": null, "battery": 90.0, "firmware_version": "2.2.0", "allowed_task_types": ["MOVE_ROBOT", "CHARGE_ROBOT", "UNLOAD_TRUCK", "PUTAWAY_PALLET", "RETRIEVE_PALLET", "LOAD_TRUCK"], "layer": "GROUND", "altitude_m": 0.0, "embodiment_class": "FORKLIFT", "clearance": "WIDE", "max_payload_kg": 1200.0, "max_shelf_level": 4}}}},
{"seq": 5, "timestamp": "2026-10-01T09:00:05", "time": "09:00:05", "level": "DEBUG", "category": "ROBOT", "event": "ROBOT_STEP", "robot_id": "robot_01", "task_id": "task_208", "box_id": null, "position": null, "message": "PF1200-205: entered Pallet aisle 1", "data": {"step": "ENTER_ZONE", "zone": "pallet_aisle_1", "people_present": [], "supervision_ok": null, "level": null, "true_weight_kg": null, "declared_weight_kg": null, "embodiment_class": "FORKLIFT", "layer": "GROUND"}},
{"seq": 6, "timestamp": "2026-10-01T09:00:06", "time": "09:00:06", "level": "INFO", "category": "TASK", "event": "TASK_COMPLETED", "robot_id": "robot_01", "task_id": "task_208", "box_id": null, "position": null, "message": "task_208 COMPLETED \u2014 Task completed", "data": {"state": {"task_type": "MOVE_ROBOT", "robot": {"id": "robot_01", "name": "PF1200-205", "position": {"x": 8, "y": 3}, "zone": null, "status": "IDLE", "carrying_box": null, "battery": 90.0, "firmware_version": "2.2.0", "allowed_task_types": ["MOVE_ROBOT", "CHARGE_ROBOT", "UNLOAD_TRUCK", "PUTAWAY_PALLET", "RETRIEVE_PALLET", "LOAD_TRUCK"], "layer": "GROUND", "altitude_m": 0.0, "embodiment_class": "FORKLIFT", "clearance": "WIDE", "max_payload_kg": 1200.0, "max_shelf_level": 4}}}}
]
```

**Create** `logs/eval_examples/multi_embodiment/task_209_human_zone_clear_fail.json`:

```json
[
{"seq": 1, "timestamp": "2026-10-01T09:00:01", "time": "09:00:01", "level": "INFO", "category": "TASK", "event": "TASK_CREATED", "robot_id": "robot_01", "task_id": "task_209", "box_id": null, "position": null, "message": "task_209 created: MOVE_ROBOT \u2014 move to 10,3", "data": {"priority": "NORMAL", "requested_robot": "AUTO", "task_type": "MOVE_ROBOT", "summary": "move to 10,3"}},
{"seq": 2, "timestamp": "2026-10-01T09:00:02", "time": "09:00:02", "level": "INFO", "category": "TASK", "event": "TASK_VALIDATED", "robot_id": "robot_01", "task_id": "task_209", "box_id": null, "position": null, "message": "task_209 validated successfully", "data": {}},
{"seq": 3, "timestamp": "2026-10-01T09:00:03", "time": "09:00:03", "level": "INFO", "category": "TASK", "event": "TASK_ASSIGNED", "robot_id": "robot_01", "task_id": "task_209", "box_id": null, "position": null, "message": "task_209 assigned to PF1200-205", "data": {}},
{"seq": 4, "timestamp": "2026-10-01T09:00:04", "time": "09:00:04", "level": "INFO", "category": "TASK", "event": "TASK_STARTED", "robot_id": "robot_01", "task_id": "task_209", "box_id": null, "position": null, "message": "task_209 started", "data": {"state": {"task_type": "MOVE_ROBOT", "robot": {"id": "robot_01", "name": "PF1200-205", "position": {"x": 8, "y": 3}, "zone": null, "status": "IDLE", "carrying_box": null, "battery": 90.0, "firmware_version": "2.2.0", "allowed_task_types": ["MOVE_ROBOT", "CHARGE_ROBOT", "UNLOAD_TRUCK", "PUTAWAY_PALLET", "RETRIEVE_PALLET", "LOAD_TRUCK"], "layer": "GROUND", "altitude_m": 0.0, "embodiment_class": "FORKLIFT", "clearance": "WIDE", "max_payload_kg": 1200.0, "max_shelf_level": 4}}}},
{"seq": 5, "timestamp": "2026-10-01T09:00:05", "time": "09:00:05", "level": "DEBUG", "category": "ROBOT", "event": "ROBOT_STEP", "robot_id": "robot_01", "task_id": "task_209", "box_id": null, "position": null, "message": "PF1200-205: entered Pallet aisle 1", "data": {"step": "ENTER_ZONE", "zone": "pallet_aisle_1", "people_present": ["operator_01"], "supervision_ok": null, "level": null, "true_weight_kg": null, "declared_weight_kg": null, "embodiment_class": "FORKLIFT", "layer": "GROUND"}},
{"seq": 6, "timestamp": "2026-10-01T09:00:06", "time": "09:00:06", "level": "INFO", "category": "TASK", "event": "TASK_COMPLETED", "robot_id": "robot_01", "task_id": "task_209", "box_id": null, "position": null, "message": "task_209 COMPLETED \u2014 Task completed", "data": {"state": {"task_type": "MOVE_ROBOT", "robot": {"id": "robot_01", "name": "PF1200-205", "position": {"x": 8, "y": 3}, "zone": null, "status": "IDLE", "carrying_box": null, "battery": 90.0, "firmware_version": "2.2.0", "allowed_task_types": ["MOVE_ROBOT", "CHARGE_ROBOT", "UNLOAD_TRUCK", "PUTAWAY_PALLET", "RETRIEVE_PALLET", "LOAD_TRUCK"], "layer": "GROUND", "altitude_m": 0.0, "embodiment_class": "FORKLIFT", "clearance": "WIDE", "max_payload_kg": 1200.0, "max_shelf_level": 4}}}}
]
```

**Create** `logs/eval_examples/multi_embodiment/task_210_supervision_maintained_pass.json`:

```json
[
{"seq": 1, "timestamp": "2026-10-01T09:00:01", "time": "09:00:01", "level": "INFO", "category": "TASK", "event": "TASK_CREATED", "robot_id": "robot_04", "task_id": "task_210", "box_id": null, "position": null, "message": "task_210 created: RETURNS_PUTAWAY \u2014 returns put-away box_021", "data": {"priority": "NORMAL", "requested_robot": "AUTO", "task_type": "RETURNS_PUTAWAY", "summary": "returns put-away box_021"}},
{"seq": 2, "timestamp": "2026-10-01T09:00:02", "time": "09:00:02", "level": "INFO", "category": "TASK", "event": "TASK_VALIDATED", "robot_id": "robot_04", "task_id": "task_210", "box_id": null, "position": null, "message": "task_210 validated successfully", "data": {}},
{"seq": 3, "timestamp": "2026-10-01T09:00:03", "time": "09:00:03", "level": "INFO", "category": "TASK", "event": "TASK_ASSIGNED", "robot_id": "robot_04", "task_id": "task_210", "box_id": null, "position": null, "message": "task_210 assigned to H1-212", "data": {}},
{"seq": 4, "timestamp": "2026-10-01T09:00:04", "time": "09:00:04", "level": "INFO", "category": "TASK", "event": "TASK_STARTED", "robot_id": "robot_04", "task_id": "task_210", "box_id": null, "position": null, "message": "task_210 started", "data": {"state": {"task_type": "RETURNS_PUTAWAY", "robot": {"id": "robot_04", "name": "H1-212", "position": {"x": 8, "y": 3}, "zone": null, "status": "IDLE", "carrying_box": null, "battery": 90.0, "firmware_version": "2.2.0", "allowed_task_types": ["MOVE_ROBOT", "CHARGE_ROBOT", "TOTE_TO_STATION", "RETURN_TOTE", "RETURNS_PUTAWAY"], "layer": "GROUND", "altitude_m": 0.0, "embodiment_class": "HUMANOID", "clearance": "NARROW", "max_payload_kg": 25.0, "max_shelf_level": 2}}}},
{"seq": 5, "timestamp": "2026-10-01T09:00:05", "time": "09:00:05", "level": "DEBUG", "category": "ROBOT", "event": "ROBOT_STEP", "robot_id": "robot_04", "task_id": "task_210", "box_id": null, "position": null, "message": "H1-212: Grasp RET-1", "data": {"step": "GRASP", "zone": "returns_qc", "people_present": [], "supervision_ok": true, "level": null, "true_weight_kg": 7.0, "declared_weight_kg": 7.0, "embodiment_class": "HUMANOID", "layer": "GROUND"}},
{"seq": 6, "timestamp": "2026-10-01T09:00:06", "time": "09:00:06", "level": "INFO", "category": "TASK", "event": "TASK_COMPLETED", "robot_id": "robot_04", "task_id": "task_210", "box_id": null, "position": null, "message": "task_210 COMPLETED \u2014 Task completed", "data": {"state": {"task_type": "RETURNS_PUTAWAY", "robot": {"id": "robot_04", "name": "H1-212", "position": {"x": 8, "y": 3}, "zone": null, "status": "IDLE", "carrying_box": null, "battery": 90.0, "firmware_version": "2.2.0", "allowed_task_types": ["MOVE_ROBOT", "CHARGE_ROBOT", "TOTE_TO_STATION", "RETURN_TOTE", "RETURNS_PUTAWAY"], "layer": "GROUND", "altitude_m": 0.0, "embodiment_class": "HUMANOID", "clearance": "NARROW", "max_payload_kg": 25.0, "max_shelf_level": 2}}}}
]
```

**Create** `logs/eval_examples/multi_embodiment/task_211_supervision_maintained_fail.json`:

```json
[
{"seq": 1, "timestamp": "2026-10-01T09:00:01", "time": "09:00:01", "level": "INFO", "category": "TASK", "event": "TASK_CREATED", "robot_id": "robot_04", "task_id": "task_211", "box_id": null, "position": null, "message": "task_211 created: RETURNS_PUTAWAY \u2014 returns put-away box_021", "data": {"priority": "NORMAL", "requested_robot": "AUTO", "task_type": "RETURNS_PUTAWAY", "summary": "returns put-away box_021"}},
{"seq": 2, "timestamp": "2026-10-01T09:00:02", "time": "09:00:02", "level": "INFO", "category": "TASK", "event": "TASK_VALIDATED", "robot_id": "robot_04", "task_id": "task_211", "box_id": null, "position": null, "message": "task_211 validated successfully", "data": {}},
{"seq": 3, "timestamp": "2026-10-01T09:00:03", "time": "09:00:03", "level": "INFO", "category": "TASK", "event": "TASK_ASSIGNED", "robot_id": "robot_04", "task_id": "task_211", "box_id": null, "position": null, "message": "task_211 assigned to H1-212", "data": {}},
{"seq": 4, "timestamp": "2026-10-01T09:00:04", "time": "09:00:04", "level": "INFO", "category": "TASK", "event": "TASK_STARTED", "robot_id": "robot_04", "task_id": "task_211", "box_id": null, "position": null, "message": "task_211 started", "data": {"state": {"task_type": "RETURNS_PUTAWAY", "robot": {"id": "robot_04", "name": "H1-212", "position": {"x": 8, "y": 3}, "zone": null, "status": "IDLE", "carrying_box": null, "battery": 90.0, "firmware_version": "2.2.0", "allowed_task_types": ["MOVE_ROBOT", "CHARGE_ROBOT", "TOTE_TO_STATION", "RETURN_TOTE", "RETURNS_PUTAWAY"], "layer": "GROUND", "altitude_m": 0.0, "embodiment_class": "HUMANOID", "clearance": "NARROW", "max_payload_kg": 25.0, "max_shelf_level": 2}}}},
{"seq": 5, "timestamp": "2026-10-01T09:00:05", "time": "09:00:05", "level": "DEBUG", "category": "ROBOT", "event": "ROBOT_STEP", "robot_id": "robot_04", "task_id": "task_211", "box_id": null, "position": null, "message": "H1-212: Grasp RET-1", "data": {"step": "GRASP", "zone": "returns_qc", "people_present": [], "supervision_ok": false, "level": null, "true_weight_kg": 7.0, "declared_weight_kg": 7.0, "embodiment_class": "HUMANOID", "layer": "GROUND"}},
{"seq": 6, "timestamp": "2026-10-01T09:00:06", "time": "09:00:06", "level": "INFO", "category": "TASK", "event": "TASK_COMPLETED", "robot_id": "robot_04", "task_id": "task_211", "box_id": null, "position": null, "message": "task_211 COMPLETED \u2014 Task completed", "data": {"state": {"task_type": "RETURNS_PUTAWAY", "robot": {"id": "robot_04", "name": "H1-212", "position": {"x": 8, "y": 3}, "zone": null, "status": "IDLE", "carrying_box": null, "battery": 90.0, "firmware_version": "2.2.0", "allowed_task_types": ["MOVE_ROBOT", "CHARGE_ROBOT", "TOTE_TO_STATION", "RETURN_TOTE", "RETURNS_PUTAWAY"], "layer": "GROUND", "altitude_m": 0.0, "embodiment_class": "HUMANOID", "clearance": "NARROW", "max_payload_kg": 25.0, "max_shelf_level": 2}}}}
]
```

**Create** `logs/eval_examples/multi_embodiment/task_212_count_consistent_pass.json`:

```json
[
{"seq": 1, "timestamp": "2026-10-01T09:00:01", "time": "09:00:01", "level": "INFO", "category": "TASK", "event": "TASK_CREATED", "robot_id": "robot_03", "task_id": "task_212", "box_id": null, "position": null, "message": "task_212 created: CYCLE_COUNT \u2014 cycle count 12,2", "data": {"priority": "NORMAL", "requested_robot": "AUTO", "task_type": "CYCLE_COUNT", "summary": "cycle count 12,2"}},
{"seq": 2, "timestamp": "2026-10-01T09:00:02", "time": "09:00:02", "level": "INFO", "category": "TASK", "event": "TASK_VALIDATED", "robot_id": "robot_03", "task_id": "task_212", "box_id": null, "position": null, "message": "task_212 validated successfully", "data": {}},
{"seq": 3, "timestamp": "2026-10-01T09:00:03", "time": "09:00:03", "level": "INFO", "category": "TASK", "event": "TASK_ASSIGNED", "robot_id": "robot_03", "task_id": "task_212", "box_id": null, "position": null, "message": "task_212 assigned to IX2-208", "data": {}},
{"seq": 4, "timestamp": "2026-10-01T09:00:04", "time": "09:00:04", "level": "INFO", "category": "TASK", "event": "TASK_STARTED", "robot_id": "robot_03", "task_id": "task_212", "box_id": null, "position": null, "message": "task_212 started", "data": {"state": {"task_type": "CYCLE_COUNT", "robot": {"id": "robot_03", "name": "IX2-208", "position": {"x": 8, "y": 3}, "zone": null, "status": "IDLE", "carrying_box": null, "battery": 90.0, "firmware_version": "2.2.0", "allowed_task_types": ["MOVE_ROBOT", "CHARGE_ROBOT", "CYCLE_COUNT"], "layer": "GROUND", "altitude_m": 0.0, "embodiment_class": "DRONE", "clearance": null, "max_payload_kg": 0.0, "max_shelf_level": 4}}}},
{"seq": 5, "timestamp": "2026-10-01T09:00:05", "time": "09:00:05", "level": "INFO", "category": "ROBOT", "event": "SCANNED", "robot_id": "robot_03", "task_id": "task_212", "box_id": null, "position": null, "message": "IX2-208 counted 40 in PR-12-02-0", "data": {"slot": "PR-12-02-0", "level": 0, "reported_qty": 40, "true_qty": 40, "recorded_qty": 40, "sku": "SKU-001", "embodiment_class": "DRONE", "max_shelf_level": 4, "max_payload_kg": 0.0}},
{"seq": 6, "timestamp": "2026-10-01T09:00:06", "time": "09:00:06", "level": "INFO", "category": "TASK", "event": "TASK_COMPLETED", "robot_id": "robot_03", "task_id": "task_212", "box_id": null, "position": null, "message": "task_212 COMPLETED \u2014 Task completed", "data": {"state": {"task_type": "CYCLE_COUNT", "robot": {"id": "robot_03", "name": "IX2-208", "position": {"x": 8, "y": 3}, "zone": null, "status": "IDLE", "carrying_box": null, "battery": 90.0, "firmware_version": "2.2.0", "allowed_task_types": ["MOVE_ROBOT", "CHARGE_ROBOT", "CYCLE_COUNT"], "layer": "GROUND", "altitude_m": 0.0, "embodiment_class": "DRONE", "clearance": null, "max_payload_kg": 0.0, "max_shelf_level": 4}}}}
]
```

**Create** `logs/eval_examples/multi_embodiment/task_213_count_consistent_fail.json`:

```json
[
{"seq": 1, "timestamp": "2026-10-01T09:00:01", "time": "09:00:01", "level": "INFO", "category": "TASK", "event": "TASK_CREATED", "robot_id": "robot_03", "task_id": "task_213", "box_id": null, "position": null, "message": "task_213 created: CYCLE_COUNT \u2014 cycle count 12,2", "data": {"priority": "NORMAL", "requested_robot": "AUTO", "task_type": "CYCLE_COUNT", "summary": "cycle count 12,2"}},
{"seq": 2, "timestamp": "2026-10-01T09:00:02", "time": "09:00:02", "level": "INFO", "category": "TASK", "event": "TASK_VALIDATED", "robot_id": "robot_03", "task_id": "task_213", "box_id": null, "position": null, "message": "task_213 validated successfully", "data": {}},
{"seq": 3, "timestamp": "2026-10-01T09:00:03", "time": "09:00:03", "level": "INFO", "category": "TASK", "event": "TASK_ASSIGNED", "robot_id": "robot_03", "task_id": "task_213", "box_id": null, "position": null, "message": "task_213 assigned to IX2-208", "data": {}},
{"seq": 4, "timestamp": "2026-10-01T09:00:04", "time": "09:00:04", "level": "INFO", "category": "TASK", "event": "TASK_STARTED", "robot_id": "robot_03", "task_id": "task_213", "box_id": null, "position": null, "message": "task_213 started", "data": {"state": {"task_type": "CYCLE_COUNT", "robot": {"id": "robot_03", "name": "IX2-208", "position": {"x": 8, "y": 3}, "zone": null, "status": "IDLE", "carrying_box": null, "battery": 90.0, "firmware_version": "2.2.0", "allowed_task_types": ["MOVE_ROBOT", "CHARGE_ROBOT", "CYCLE_COUNT"], "layer": "GROUND", "altitude_m": 0.0, "embodiment_class": "DRONE", "clearance": null, "max_payload_kg": 0.0, "max_shelf_level": 4}}}},
{"seq": 5, "timestamp": "2026-10-01T09:00:05", "time": "09:00:05", "level": "INFO", "category": "ROBOT", "event": "SCANNED", "robot_id": "robot_03", "task_id": "task_213", "box_id": null, "position": null, "message": "IX2-208 counted 38 in PR-12-02-0", "data": {"slot": "PR-12-02-0", "level": 0, "reported_qty": 38, "true_qty": 40, "recorded_qty": 40, "sku": "SKU-001", "embodiment_class": "DRONE", "max_shelf_level": 4, "max_payload_kg": 0.0}},
{"seq": 6, "timestamp": "2026-10-01T09:00:06", "time": "09:00:06", "level": "INFO", "category": "TASK", "event": "TASK_COMPLETED", "robot_id": "robot_03", "task_id": "task_213", "box_id": null, "position": null, "message": "task_213 COMPLETED \u2014 Task completed", "data": {"state": {"task_type": "CYCLE_COUNT", "robot": {"id": "robot_03", "name": "IX2-208", "position": {"x": 8, "y": 3}, "zone": null, "status": "IDLE", "carrying_box": null, "battery": 90.0, "firmware_version": "2.2.0", "allowed_task_types": ["MOVE_ROBOT", "CHARGE_ROBOT", "CYCLE_COUNT"], "layer": "GROUND", "altitude_m": 0.0, "embodiment_class": "DRONE", "clearance": null, "max_payload_kg": 0.0, "max_shelf_level": 4}}}}
]
```

**Create** `logs/eval_examples/multi_embodiment/task_214_handoff_consistent_pass.json`:

```json
[
{"seq": 1, "timestamp": "2026-10-01T09:00:01", "time": "09:00:01", "level": "INFO", "category": "TASK", "event": "TASK_CREATED", "robot_id": "robot_05", "task_id": "task_214", "box_id": null, "position": null, "message": "task_214 created: PICK_ITEMS \u2014 pick items box_030", "data": {"priority": "NORMAL", "requested_robot": "AUTO", "task_type": "PICK_ITEMS", "summary": "pick items box_030"}},
{"seq": 2, "timestamp": "2026-10-01T09:00:02", "time": "09:00:02", "level": "INFO", "category": "TASK", "event": "TASK_VALIDATED", "robot_id": "robot_05", "task_id": "task_214", "box_id": null, "position": null, "message": "task_214 validated successfully", "data": {}},
{"seq": 3, "timestamp": "2026-10-01T09:00:03", "time": "09:00:03", "level": "INFO", "category": "TASK", "event": "TASK_ASSIGNED", "robot_id": "robot_05", "task_id": "task_214", "box_id": null, "position": null, "message": "task_214 assigned to PK30-203", "data": {}},
{"seq": 4, "timestamp": "2026-10-01T09:00:04", "time": "09:00:04", "level": "INFO", "category": "TASK", "event": "TASK_STARTED", "robot_id": "robot_05", "task_id": "task_214", "box_id": null, "position": null, "message": "task_214 started", "data": {"state": {"task_type": "PICK_ITEMS", "robot": {"id": "robot_05", "name": "PK30-203", "position": {"x": 8, "y": 3}, "zone": null, "status": "IDLE", "carrying_box": null, "battery": 90.0, "firmware_version": "2.2.0", "allowed_task_types": ["CHARGE_ROBOT", "PICK_ITEMS"], "layer": "GROUND", "altitude_m": 0.0, "embodiment_class": "PICKER", "clearance": "NARROW", "max_payload_kg": 30.0, "max_shelf_level": 1}}}},
{"seq": 5, "timestamp": "2026-10-01T09:00:05", "time": "09:00:05", "level": "INFO", "category": "OPERATIONS", "event": "HANDOFF", "robot_id": "robot_05", "task_id": "task_214", "box_id": "box_031", "position": null, "message": "Box-031: robot_05 \u2192 conveyor at (20,15)", "data": {"handoff_id": "HO-00001", "box_id": "box_031", "order_id": "ORD-0001", "from": "robot_05", "to": "conveyor", "cell": {"x": 20, "y": 15}, "tick": 120, "giver_reported": {"present": true, "weight_kg": 0.4}, "receiver_observed": {"present": true, "weight_kg": 0.4}, "task_id": "task_214"}},
{"seq": 6, "timestamp": "2026-10-01T09:00:06", "time": "09:00:06", "level": "INFO", "category": "TASK", "event": "TASK_COMPLETED", "robot_id": "robot_05", "task_id": "task_214", "box_id": null, "position": null, "message": "task_214 COMPLETED \u2014 Task completed", "data": {"state": {"task_type": "PICK_ITEMS", "robot": {"id": "robot_05", "name": "PK30-203", "position": {"x": 8, "y": 3}, "zone": null, "status": "IDLE", "carrying_box": null, "battery": 90.0, "firmware_version": "2.2.0", "allowed_task_types": ["CHARGE_ROBOT", "PICK_ITEMS"], "layer": "GROUND", "altitude_m": 0.0, "embodiment_class": "PICKER", "clearance": "NARROW", "max_payload_kg": 30.0, "max_shelf_level": 1}}}}
]
```

**Create** `logs/eval_examples/multi_embodiment/task_215_handoff_consistent_fail.json`:

```json
[
{"seq": 1, "timestamp": "2026-10-01T09:00:01", "time": "09:00:01", "level": "INFO", "category": "TASK", "event": "TASK_CREATED", "robot_id": "robot_05", "task_id": "task_215", "box_id": null, "position": null, "message": "task_215 created: PICK_ITEMS \u2014 pick items box_030", "data": {"priority": "NORMAL", "requested_robot": "AUTO", "task_type": "PICK_ITEMS", "summary": "pick items box_030"}},
{"seq": 2, "timestamp": "2026-10-01T09:00:02", "time": "09:00:02", "level": "INFO", "category": "TASK", "event": "TASK_VALIDATED", "robot_id": "robot_05", "task_id": "task_215", "box_id": null, "position": null, "message": "task_215 validated successfully", "data": {}},
{"seq": 3, "timestamp": "2026-10-01T09:00:03", "time": "09:00:03", "level": "INFO", "category": "TASK", "event": "TASK_ASSIGNED", "robot_id": "robot_05", "task_id": "task_215", "box_id": null, "position": null, "message": "task_215 assigned to PK30-203", "data": {}},
{"seq": 4, "timestamp": "2026-10-01T09:00:04", "time": "09:00:04", "level": "INFO", "category": "TASK", "event": "TASK_STARTED", "robot_id": "robot_05", "task_id": "task_215", "box_id": null, "position": null, "message": "task_215 started", "data": {"state": {"task_type": "PICK_ITEMS", "robot": {"id": "robot_05", "name": "PK30-203", "position": {"x": 8, "y": 3}, "zone": null, "status": "IDLE", "carrying_box": null, "battery": 90.0, "firmware_version": "2.2.0", "allowed_task_types": ["CHARGE_ROBOT", "PICK_ITEMS"], "layer": "GROUND", "altitude_m": 0.0, "embodiment_class": "PICKER", "clearance": "NARROW", "max_payload_kg": 30.0, "max_shelf_level": 1}}}},
{"seq": 5, "timestamp": "2026-10-01T09:00:05", "time": "09:00:05", "level": "WARNING", "category": "OPERATIONS", "event": "HANDOFF", "robot_id": "robot_05", "task_id": "task_215", "box_id": "box_031", "position": null, "message": "Box-031: robot_05 \u2192 conveyor at (20,15) \u2014 the receiver never got it", "data": {"handoff_id": "HO-00001", "box_id": "box_031", "order_id": "ORD-0001", "from": "robot_05", "to": "conveyor", "cell": {"x": 20, "y": 15}, "tick": 120, "giver_reported": {"present": true, "weight_kg": 0.4}, "receiver_observed": {"present": false}, "task_id": "task_215"}},
{"seq": 6, "timestamp": "2026-10-01T09:00:06", "time": "09:00:06", "level": "INFO", "category": "TASK", "event": "TASK_COMPLETED", "robot_id": "robot_05", "task_id": "task_215", "box_id": null, "position": null, "message": "task_215 COMPLETED \u2014 Task completed", "data": {"state": {"task_type": "PICK_ITEMS", "robot": {"id": "robot_05", "name": "PK30-203", "position": {"x": 8, "y": 3}, "zone": null, "status": "IDLE", "carrying_box": null, "battery": 90.0, "firmware_version": "2.2.0", "allowed_task_types": ["CHARGE_ROBOT", "PICK_ITEMS"], "layer": "GROUND", "altitude_m": 0.0, "embodiment_class": "PICKER", "clearance": "NARROW", "max_payload_kg": 30.0, "max_shelf_level": 1}}}}
]
```

**Create** `logs/eval_examples/multi_embodiment/task_216_sort_correct_pass.json`:

```json
[
{"seq": 1, "timestamp": "2026-10-01T09:00:01", "time": "09:00:01", "level": "INFO", "category": "TASK", "event": "TASK_CREATED", "robot_id": "robot_06", "task_id": "task_216", "box_id": null, "position": null, "message": "task_216 created: PACK_ORDER \u2014 pack order ORD-0002", "data": {"priority": "NORMAL", "requested_robot": "AUTO", "task_type": "PACK_ORDER", "summary": "pack order ORD-0002"}},
{"seq": 2, "timestamp": "2026-10-01T09:00:02", "time": "09:00:02", "level": "INFO", "category": "TASK", "event": "TASK_VALIDATED", "robot_id": "robot_06", "task_id": "task_216", "box_id": null, "position": null, "message": "task_216 validated successfully", "data": {}},
{"seq": 3, "timestamp": "2026-10-01T09:00:03", "time": "09:00:03", "level": "INFO", "category": "TASK", "event": "TASK_ASSIGNED", "robot_id": "robot_06", "task_id": "task_216", "box_id": null, "position": null, "message": "task_216 assigned to CX10-210", "data": {}},
{"seq": 4, "timestamp": "2026-10-01T09:00:04", "time": "09:00:04", "level": "INFO", "category": "TASK", "event": "TASK_STARTED", "robot_id": "robot_06", "task_id": "task_216", "box_id": null, "position": null, "message": "task_216 started", "data": {"state": {"task_type": "PACK_ORDER", "robot": {"id": "robot_06", "name": "CX10-210", "position": {"x": 8, "y": 3}, "zone": null, "status": "IDLE", "carrying_box": null, "battery": 90.0, "firmware_version": "2.2.0", "allowed_task_types": ["CHARGE_ROBOT", "PACK_ORDER"], "layer": "GROUND", "altitude_m": 0.0, "embodiment_class": "ARM", "clearance": null, "max_payload_kg": 10.0, "max_shelf_level": 0}}}},
{"seq": 5, "timestamp": "2026-10-01T09:00:05", "time": "09:00:05", "level": "INFO", "category": "OPERATIONS", "event": "HANDOFF", "robot_id": "robot_06", "task_id": "task_216", "box_id": "box_041", "position": null, "message": "Box-041: sorter \u2192 dock_4 at (28,12)", "data": {"handoff_id": "HO-00002", "box_id": "box_041", "order_id": "ORD-0002", "from": "sorter", "to": "dock_4", "cell": {"x": 28, "y": 12}, "tick": 400, "giver_reported": {"present": true, "lane": "dock_4"}, "receiver_observed": {"present": true, "dock": "dock_4"}, "task_id": "task_216"}},
{"seq": 6, "timestamp": "2026-10-01T09:00:06", "time": "09:00:06", "level": "INFO", "category": "TASK", "event": "TASK_COMPLETED", "robot_id": "robot_06", "task_id": "task_216", "box_id": null, "position": null, "message": "task_216 COMPLETED \u2014 Task completed", "data": {"state": {"task_type": "PACK_ORDER", "robot": {"id": "robot_06", "name": "CX10-210", "position": {"x": 8, "y": 3}, "zone": null, "status": "IDLE", "carrying_box": null, "battery": 90.0, "firmware_version": "2.2.0", "allowed_task_types": ["CHARGE_ROBOT", "PACK_ORDER"], "layer": "GROUND", "altitude_m": 0.0, "embodiment_class": "ARM", "clearance": null, "max_payload_kg": 10.0, "max_shelf_level": 0}}}}
]
```

**Create** `logs/eval_examples/multi_embodiment/task_217_sort_correct_fail.json`:

```json
[
{"seq": 1, "timestamp": "2026-10-01T09:00:01", "time": "09:00:01", "level": "INFO", "category": "TASK", "event": "TASK_CREATED", "robot_id": "robot_06", "task_id": "task_217", "box_id": null, "position": null, "message": "task_217 created: PACK_ORDER \u2014 pack order ORD-0002", "data": {"priority": "NORMAL", "requested_robot": "AUTO", "task_type": "PACK_ORDER", "summary": "pack order ORD-0002"}},
{"seq": 2, "timestamp": "2026-10-01T09:00:02", "time": "09:00:02", "level": "INFO", "category": "TASK", "event": "TASK_VALIDATED", "robot_id": "robot_06", "task_id": "task_217", "box_id": null, "position": null, "message": "task_217 validated successfully", "data": {}},
{"seq": 3, "timestamp": "2026-10-01T09:00:03", "time": "09:00:03", "level": "INFO", "category": "TASK", "event": "TASK_ASSIGNED", "robot_id": "robot_06", "task_id": "task_217", "box_id": null, "position": null, "message": "task_217 assigned to CX10-210", "data": {}},
{"seq": 4, "timestamp": "2026-10-01T09:00:04", "time": "09:00:04", "level": "INFO", "category": "TASK", "event": "TASK_STARTED", "robot_id": "robot_06", "task_id": "task_217", "box_id": null, "position": null, "message": "task_217 started", "data": {"state": {"task_type": "PACK_ORDER", "robot": {"id": "robot_06", "name": "CX10-210", "position": {"x": 8, "y": 3}, "zone": null, "status": "IDLE", "carrying_box": null, "battery": 90.0, "firmware_version": "2.2.0", "allowed_task_types": ["CHARGE_ROBOT", "PACK_ORDER"], "layer": "GROUND", "altitude_m": 0.0, "embodiment_class": "ARM", "clearance": null, "max_payload_kg": 10.0, "max_shelf_level": 0}}}},
{"seq": 5, "timestamp": "2026-10-01T09:00:05", "time": "09:00:05", "level": "INFO", "category": "OPERATIONS", "event": "HANDOFF", "robot_id": "robot_06", "task_id": "task_217", "box_id": "box_041", "position": null, "message": "Box-041: sorter \u2192 dock_5 at (28,12)", "data": {"handoff_id": "HO-00002", "box_id": "box_041", "order_id": "ORD-0002", "from": "sorter", "to": "dock_5", "cell": {"x": 28, "y": 12}, "tick": 400, "giver_reported": {"present": true, "lane": "dock_4"}, "receiver_observed": {"present": true, "dock": "dock_5"}, "task_id": "task_217"}},
{"seq": 6, "timestamp": "2026-10-01T09:00:06", "time": "09:00:06", "level": "INFO", "category": "TASK", "event": "TASK_COMPLETED", "robot_id": "robot_06", "task_id": "task_217", "box_id": null, "position": null, "message": "task_217 COMPLETED \u2014 Task completed", "data": {"state": {"task_type": "PACK_ORDER", "robot": {"id": "robot_06", "name": "CX10-210", "position": {"x": 8, "y": 3}, "zone": null, "status": "IDLE", "carrying_box": null, "battery": 90.0, "firmware_version": "2.2.0", "allowed_task_types": ["CHARGE_ROBOT", "PACK_ORDER"], "layer": "GROUND", "altitude_m": 0.0, "embodiment_class": "ARM", "clearance": null, "max_payload_kg": 10.0, "max_shelf_level": 0}}}}
]
```

- [ ] **Step 6: Layout-driven system checks**

**Replace in** `backend/ci_engine.py`:

```python
from typing import Any, Callable, Dict, List, Optional, Tuple

from .embodiment import GROUND
from .models import (
    ACTIVE_TASK_STATES,
```

with:

```python
from typing import Any, Callable, Dict, List, Optional, Tuple

from .embodiment import AIR, GROUND
from .human_jobs import HUMAN_JOBS
from .models import (
    ACTIVE_TASK_STATES,
```

**Replace in** `backend/ci_engine.py`:

```python
)

CheckResult = Tuple[bool, str, Dict[str, Any]]


class CIEngine:
```

with:

```python
)

CheckResult = Tuple[bool, str, Dict[str, Any]]

#: One catalog body per profile class whose routes must reach every cell it
#: may use on a layered floor (spec §10.5): narrow ground, wide ground, air.
PROFILE_CLASSES = (("narrow ground", "AC-TR50", GROUND), ("wide ground", "NW-PF1200", GROUND), ("air", "CT-IX2", AIR))


class CIEngine:
```

**Replace in** `backend/ci_engine.py`:

```python
            problems.append("grid is too small to operate")
        for x in range(warehouse.width):
            if warehouse.cell_type(x, 0) is not CellType.WALL or \
               warehouse.cell_type(x, warehouse.height - 1) is not CellType.WALL:
                problems.append(f"perimeter breach at column {x}")
                break
        for y in range(warehouse.height):
            if warehouse.cell_type(0, y) is not CellType.WALL or \
               warehouse.cell_type(warehouse.width - 1, y) is not CellType.WALL:
                problems.append(f"perimeter breach at row {y}")
                break
        required = {"charging_station", "loading_zone", "packing_area", "shelf_a"}
        missing = required - set(warehouse.zones)
        if missing:
            problems.append(f"missing zones: {', '.join(sorted(missing))}")

        # Every drivable cell must be reachable from every other drivable cell.
        walkable = warehouse.walkable_cells()
        if walkable:
            seen = {walkable[0]}
            stack = [walkable[0]]
            while stack:
                for neighbor in warehouse.neighbors(stack.pop()):
                    if neighbor not in seen:
                        seen.add(neighbor)
                        stack.append(neighbor)
            unreachable = len(walkable) - len(seen)
            if unreachable:
                problems.append(f"{unreachable} drivable cells are walled off")
        details = {
```

with:

```python
            problems.append("grid is too small to operate")
        solid = (CellType.WALL, CellType.DOCK_DOOR)  # a dock door is part of the wall
        for x in range(warehouse.width):
            if warehouse.cell_type(x, 0) not in solid or \
               warehouse.cell_type(x, warehouse.height - 1) not in solid:
                problems.append(f"perimeter breach at column {x}")
                break
        for y in range(warehouse.height):
            if warehouse.cell_type(0, y) not in solid or \
               warehouse.cell_type(warehouse.width - 1, y) not in solid:
                problems.append(f"perimeter breach at row {y}")
                break
        # The zones the loaded layout says it can't run without (spec §10.5).
        missing = set(warehouse.required_zones) - set(warehouse.zones)
        if missing:
            problems.append(f"missing zones: {', '.join(sorted(missing))}")

        # Every drivable cell must be reachable from every other drivable cell
        # — on a layered floor, for each class of body over the cells it uses.
        walkable = warehouse.walkable_cells()
        if warehouse.layout_name == "classic":
            unreachable = self._cut_off(walkable, warehouse.neighbors)
            if unreachable:
                problems.append(f"{unreachable} drivable cells are walled off")
            connected = "all connected"
        else:
            for label, model, layer in PROFILE_CLASSES:
                profile = self.twin.fleet.model_profile(model)
                cells = warehouse.passable_cells(profile, layer)
                unreachable = self._cut_off(cells, lambda cell: warehouse.neighbors(cell, profile, layer))
                if unreachable:
                    problems.append(f"{unreachable} cells are cut off for a {label} body")
            connected = "connected for narrow, wide and air bodies"
        details = {
```

**Replace in** `backend/ci_engine.py`:

```python
            return False, "; ".join(problems), details
        return True, f"{warehouse.width}x{warehouse.height} grid, {len(warehouse.zones)} zones, " \
                     f"{len(walkable)} drivable cells all connected", details

    def _check_robot_state(self) -> CheckResult:
```

with:

```python
            return False, "; ".join(problems), details
        return True, f"{warehouse.width}x{warehouse.height} grid, {len(warehouse.zones)} zones, " \
                     f"{len(walkable)} drivable cells {connected}", details

    @staticmethod
    def _cut_off(cells: List[tuple], neighbors: Callable[[tuple], List[tuple]]) -> int:
        """How many of `cells` can't be reached from the first of them."""
        if not cells:
            return 0
        seen = {cells[0]}
        stack = [cells[0]]
        while stack:
            for neighbor in neighbors(stack.pop()):
                if neighbor not in seen:
                    seen.add(neighbor)
                    stack.append(neighbor)
        return len(set(cells) - seen)

    def _check_robot_state(self) -> CheckResult:
```

**Replace in** `backend/ci_engine.py`:

```python
        return True, f"{len(twin.robots)} robots hold valid state", details

    def _check_robot_positions(self) -> CheckResult:
        twin = self.twin
```

with:

```python
        return True, f"{len(twin.robots)} robots hold valid state", details

    def _allowed_at(self, robot: Any) -> bool:
        """May `robot` be where it is? Classic: a drivable cell. A layered
        floor (spec §10.5): an arm on its fixed station, a flying drone over
        any flyable cell, anything else on a cell its body may use."""
        warehouse, profile = self.twin.warehouse, robot.mobility
        if profile is None:
            return warehouse.is_walkable(*robot.position)
        if profile.is_fixed:
            return robot.position in warehouse.fixed_stations
        return warehouse.passable(robot.position, profile, robot.layer)

    def _check_robot_positions(self) -> CheckResult:
        twin = self.twin
```

**Replace in** `backend/ci_engine.py`:

```python
                problems.append(f"{robot.name} is outside the warehouse at ({x},{y})")
            elif not twin.warehouse.is_walkable(x, y):
                problems.append(
                    f"{robot.name} stands on a "
                    f"{twin.warehouse.cell_type(x, y).value} cell at ({x},{y})"
                )
```

with:

```python
                problems.append(f"{robot.name} is outside the warehouse at ({x},{y})")
            elif not self._allowed_at(robot):
                where = "" if robot.layer == GROUND else f" on the {robot.layer} layer"
                problems.append(
                    f"{robot.name} stands on a "
                    f"{twin.warehouse.cell_type(x, y).value} cell at ({x},{y}){where}"
                )
```

**Replace in** `backend/ci_engine.py`:

```python
            for cell in path:
                if not twin.warehouse.is_walkable(*cell):
                    problems.append(f"{robot.name} path crosses a blocked cell {cell}")
```

with:

```python
            for cell in path:
                if not twin.warehouse.passable(cell, robot.mobility, robot.layer):
                    problems.append(f"{robot.name} path crosses a blocked cell {cell}")
```

**Replace in** `backend/ci_engine.py`:

```python
        for robot in twin.robots.values():
            if robot.battery < 0 or robot.battery > 100:
                problems.append(f"{robot.name} battery is {robot.battery}")
            if robot.battery == 0 and robot.status != RobotStatus.ERROR:
                problems.append(f"{robot.name} is flat but not in an error state")
            if robot.status == RobotStatus.CHARGING and \
                    twin.warehouse.cell_type(*robot.position) is not CellType.CHARGING:
                problems.append(f"{robot.name} is charging away from the charging station")
```

with:

```python
        for robot in twin.robots.values():
            if robot.mains_powered:
                continue  # an arm runs on mains power: there is no battery to check
            if robot.battery < 0 or robot.battery > 100:
                problems.append(f"{robot.name} battery is {robot.battery}")
            if robot.battery == 0 and robot.status != RobotStatus.ERROR:
                problems.append(f"{robot.name} is flat but not in an error state")
            charger = CellType.DRONE_PAD if robot.mobility is not None and robot.mobility.is_air else CellType.CHARGING
            if robot.status == RobotStatus.CHARGING and twin.warehouse.cell_type(*robot.position) is not charger:
                problems.append(f"{robot.name} is charging away from the charging station")
```

**Replace in** `backend/ci_engine.py`:

```python
                problems.append(f"{task.id} finished without a completion timestamp")
            if task.status in ACTIVE_TASK_STATES and not task.robot_id:
                problems.append(f"{task.id} is active without a robot")
            if task.status == TaskStatus.FAILED and not task.error:
```

with:

```python
                problems.append(f"{task.id} finished without a completion timestamp")
            if task.status in ACTIVE_TASK_STATES and not task.robot_id and task.type not in HUMAN_JOBS:
                problems.append(f"{task.id} is active without a robot")  # a person's job has none
            if task.status == TaskStatus.FAILED and not task.error:
```

- [ ] **Step 7: Run the new tests with the existing evaluation tests**

`test_eval_engine.py`'s expectation table is unchanged: it globs `logs/eval_examples/task_*.json`, not the subdirectory. Its one failure is the pre-existing `test_real_task_006_box_conflict_is_caught`.

Run: `.venv/bin/python -m pytest -o addopts="" -q backend/test_trust_checks.py backend/test_eval_engine.py`
Expected: `1 failed, 97 passed`.

- [ ] **Step 8: Run the full suite**

Run: `.venv/bin/python -m pytest -o addopts="" -q`
Expected: `2 failed, 624 passed`.

- [ ] **Step 9: Commit**

```bash
git add backend/eval_engine.py backend/simulator.py backend/task_manager.py backend/ci_engine.py \
        backend/test_trust_checks.py \
        logs/eval_examples/multi_embodiment/task_200_payload_within_limit_pass.json \
        logs/eval_examples/multi_embodiment/task_201_payload_within_limit_fail.json \
        logs/eval_examples/multi_embodiment/task_202_reach_within_limit_pass.json \
        logs/eval_examples/multi_embodiment/task_203_reach_within_limit_fail.json \
        logs/eval_examples/multi_embodiment/task_204_clearance_respected_pass.json \
        logs/eval_examples/multi_embodiment/task_205_clearance_respected_fail.json \
        logs/eval_examples/multi_embodiment/task_206_no_fly_respected_pass.json \
        logs/eval_examples/multi_embodiment/task_207_no_fly_respected_fail.json \
        logs/eval_examples/multi_embodiment/task_208_human_zone_clear_pass.json \
        logs/eval_examples/multi_embodiment/task_209_human_zone_clear_fail.json \
        logs/eval_examples/multi_embodiment/task_210_supervision_maintained_pass.json \
        logs/eval_examples/multi_embodiment/task_211_supervision_maintained_fail.json \
        logs/eval_examples/multi_embodiment/task_212_count_consistent_pass.json \
        logs/eval_examples/multi_embodiment/task_213_count_consistent_fail.json \
        logs/eval_examples/multi_embodiment/task_214_handoff_consistent_pass.json \
        logs/eval_examples/multi_embodiment/task_215_handoff_consistent_fail.json \
        logs/eval_examples/multi_embodiment/task_216_sort_correct_pass.json \
        logs/eval_examples/multi_embodiment/task_217_sort_correct_fail.json
git commit -m "feat: multi-embodiment evaluation checks and layout-driven system checks

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## Self-review

**Spec coverage (§17 step 1, items 8–13).**

| Spec requirement | Task |
|---|---|
| §5.4 LIFT_TO/LOWER, TAKEOFF/LAND, SCAN, GRASP/PLACE, PLACE_ON_CONVEYOR, WAIT_CLEAR; `ceil(seconds / TICK_DT)`; `PICK_TICKS`/`DELIVER_TICKS` kept for the older types | 4, 11 |
| §5.5 per-model Wh (idle × 0.3, moving × 1.0, loaded × 1.4), lift energy, drone flight drain, `ENERGY_TIME_SCALE` 10, charging at the station or the pad, mains-powered arms, classic unchanged | 6 |
| §6 `PERSON_IN_AISLE`, `PERSON_IN_CELL`, `SUPERVISOR_ABSENT` (on shift, valid credential in scope for TS-H1 and WH-01, same or adjacent zone) | 5 |
| §7 the twin's `StockLedger` as the single source of truth; box kinds placed by kind; PICK_ITEMS makes ITEMs and decrements the tote; PACK_ORDER makes a CARTON of the items plus 0.3 kg; counts record and flag variances, ≤ 2 units reconciled; returns put stock back | 2, 4, 10, 11 |
| §8.1 one line (19,15)→(24,15), 20 ticks a cell, one item a cell, backing up, infeed (20,15), arms' working cells, jams and their `CLEAR_JAM`, arms pausing | 3, 4, 10 |
| §8.2 the sorter: 2 s in, by lane to dock_4/dock_5, accumulating, `SHIPPED` at departure, `MIS_SORT_RISK` | 3, 12 |
| §8.3 hand-off records for every transfer named; `HANDOFF_LOSS_RISK`; `HANDOFF` in the task log | 3, 4, 10 |
| §9.1 capability filter (movement, route without snapping, payload, kind, level, `allowed_task_types`, supervision) before scoring; eligibility still applies; no first-robot origin | 7 |
| §9.2 all thirteen job types and their steps; `CLEAR_JAM: robot_cell_access`; scope rule on every new-floor certification check; `TASK_TYPE_GUIDE` and `options().task_types` | 7–11 |
| §9.3 every new event and `activity`/`wait_reason` | 3–5, 11, 12 |
| §10.1 the eight rules; the ground-robot battery exception kept, the drone's hard gate | 1, 6, 11 |
| §10.2 rules at creation, in selection and re-checked mid-task (payload, reach, supervision) | 7 |
| §10.3 physical waits with `ROBOT_SAFETY_WAIT`/`RESUMED`, in the task log, escalated after 120 s | 4, 5 |
| §10.4 the nine checks, not-applicable on old logs, snapshot fields, a pass and a fail fixture each | 14 |
| §10.5 required zones, per-class connectivity, drone and arm positions, battery skip for arms, collisions | 14 (collisions: plan 1a) |
| §10.6 the seven CONFIG risks, each doing its damage, and on-demand single faults | 1, 3, 4, 11, 12 |
| §11.1 generators, rates × pace, seed 42, `create_task(internal=True)`, rejections recorded on the order | 12 |
| §11.2 `Order` fields and chains, stages advancing on completion and hand-offs, one retry then `FAILED`, picking by station | 12 |
| §11.3 people's activities, breaks, `CLEAR_JAM` to the nearest qualified person, retried until someone qualifies | 10, 13 |
| §11.4 shift clock `SHIFT_START_HOUR` + simulated time, HH:MM, wrapping; duty and HR follow it; classic wall clock | 12, 13 |
| §11.5 the engine's Python API (HTTP in plan 1c) | 12 |

**Spec problems found while planning** (each resolved by a ruling, and worth fixing in the spec):
- §16's soak asserts no safety escalations, but §11.3 has Jordan's 15-minute break pause the humanoid, which escalates after 120 s. Breaks therefore start after two sim-hours (Ruling 14), outside a 50-minute soak.
- §6 has forklifts and haulers never enter a zone with a person in it, while §11.3 parks Sam and Lee at intake staging and Ana and Kai at the docks — exactly where forklifts unload. Without people stepping aside (Ruling 14) unloading deadlocks.
- §16's "rerun with each risk at 0.2 and assert the matching evaluation check catches it" has no §10.4 check for `WRONG_LEVEL_RISK` (Ruling 4).
- §9.2 lists `PICK_ITEMS` for a person at Pick 2 as well as `MANUAL_PICK` (Ruling 10); its `RETURNS_PUTAWAY` steps omit `LIFT_TO` for levels 1–2, which this plan follows literally; §5.5's carriage mass is not in the catalog (Ruling 8).

**Placeholder scan.** Every code step is a **Create** block with the whole file or a **Replace in** block cut from the verified implementation; there is no "TBD", "similar to" or unnamed helper.

**Type consistency.** Names used across tasks: `FaultInjector.roll`/`arm` (1) → `goods.store`/`release`/`take_units`/`physical_qty`/`free_slots` (2) → `Equipment.place`/`take`/`record`/`jam`/`clear_jam`/`ship`, `LineItem.stop_at` (3) → `Action.level`/`slot_id`/`params`, `STEP_ACTIONS`, `_step_hold`, `_step_event` (4) → `end_safety_wait`, `supervision_available`/`supervision_status`, `Robot.model_code` (5) → `energy.*`, `Robot.mains_powered` (6) → `JobSpec`, `JOB_SPECS`, `capability_reason`, `Task.params` (7) → `check_box`/`check_slot`/`choose_slot`/`lift_steps`, `FILLS_SLOT` (8–9) → `human_jobs.HUMAN_JOBS`, `_operator_reason` (10) → `ActionType.SCAN`, `JobSpec.mission_wh` (11) → `OrderBook`, `ShiftEngine`, `shift_hour` (12) → `move_people`, `sync_duty`, `Operator.employment_status` (13) → `EMBODIMENT_CHECKS`, `CheckResult.applicable` (14).

**Dry run.** Every edit block of this plan was applied, in order, to a fresh clone of `feature/multi-embodiment-ops` at `c088d98`, and the suite run after each task. Each task's new tests failed before its code exactly as its Step 2 says, and the full suite gave the stated counts — 487, 497, 508, 522, 532, 541, 550, 558, 562, 570, 575, 587, 596 and 624 passed — with only the two pre-existing failures each time.
