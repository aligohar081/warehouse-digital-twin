# Trust Layer — mapping from "The Trust Layer for the Physical AI"

The reference document ("The Trust Layer for the Physical AI", Cytex /
AICenturion) describes an enterprise product that checks, before and after a
job, whether a given combination of AI agent, robot and person can be trusted
with it. This project is a single-warehouse simulation, so **the depth doesn't
match**: no real hardware, firmware, employee database or model calling the
shots. What is built is the document's structural idea at toy scale: three
actor classes, each with a toy assurance passport, converging in one task
record that is graded on who was involved and on what happened. This file maps
each concept to its code. On the distribution-centre floor a physical layer
sits on top (below). For the module map see [ARCHITECTURE.md](ARCHITECTURE.md).

## Tier 1 — easy (built, for all three actor classes)

| Concept | Here | Code |
|---|---|---|
| Three actor classes | Robot, AI agent and human operator are each first-class | `backend/robot.py`, `agent.py`, `operator.py` |
| Entities check, per actor | Robot: not `ERROR` or `STOPPED`, approved firmware, not critically low on battery. Agent: not `ERROR`, approved model. Operator: on duty, holds the required certification | `check_entities_valid` in `backend/eval_engine.py` |
| Robot, Agent and Worker Assurance Passports (toy) | Firmware, model version and certifications checked against approved baselines | `models.APPROVED_FIRMWARE_VERSIONS`, `APPROVED_AGENT_MODELS`, `CERTIFICATION_REQUIREMENTS` |
| Mission Authorization Record | One artifact naming who and what was involved in a task and whether it was trustworthy | `build_mission_record` and the `extract_*` helpers in `backend/eval_engine.py` |
| Interaction in the environment | The document's maintenance mission: an agent recommends, a robot really navigates, a human signs off | `TaskType.MIXED_MAINTENANCE_MISSION`; `TaskManager.start_task` and `complete_task` |
| Pre-execution authorization gate | `TaskManager.validate` refuses a task (`422`) when a named robot, agent or operator is ineligible; `AUTO` selection skips them. The rule lives once and the grade shares it, so the two can't disagree. A critical battery is deliberately not gated: the planner prepends a recharge detour | `backend/eligibility.py`, `backend/task_manager.py` |
| Per-entity work authorization | A robot's optional `allowed_task_types` allowlist, checked by the same rule at creation and in `AUTO` scoring | `Robot.allowed_task_types`, `DigitalTwin.set_robot_capabilities` |
| Agent and operator own work | `AGENT_REPLAN`, `AGENT_AUDIT`, `OPERATOR_APPROVAL`, `OPERATOR_MAINTENANCE_SIGNOFF`: instant, gated and graded, each reading live twin state | `TaskManager._run_instant` |
| Evaluation Factory | A repeatable automated evaluation harness | `evals/` ([evals/README.md](evals/README.md)) |
| Metrics | Mission success rate, blocked jobs, coverage | `compute_metrics`, `python -m backend.run_evals --stats` |

## Tier 2 — medium (three of four built)

| Concept | What it means here | Status |
|---|---|---|
| Authorization-changing events | A robot that becomes ineligible mid-task is flagged, not stopped (it may already be mid-route). `Simulator._check_authorization_changes` re-checks every running task every `AUTHORIZATION_CHECK_EVERY_TICKS` ticks, emits one `TASK_AUTHORIZATION_CHANGED` per task, and `entities_valid` grades it `WARN` | Built: `backend/simulator.py` |
| Governance layer (policy-as-code) | An optional `policies.yaml` overrides `CONFIG`, the approved baselines, `CERTIFICATION_REQUIREMENTS`, the robot classes and the operator roles: the tables the gate and the eval engine both read. `POST /api/policies/reload` re-reads it live; `GET /api/policies` shows what is in effect | Built: `backend/policy.py`, `policies.example.yaml` |
| Decision graph (queryable history) | "Every task assigned to Robo-01 while battery < 20 %" answered across all task logs by filtering each log's mission record, with no graph database | Built: `backend/decision_graph.py`, `GET /api/decisions` |
| Agent assurance record for the Groq grader | Record which model and prompt version graded each task, beside the verdict | Not built: `evals/` records neither |

## Tier 3 — complex or not applicable (not planned)

Real SBOMs and cryptographic attestation, real HRIS integration, an enterprise Physical Work Graph, a Decision Twin (counterfactual re-simulation), change-triggered re-evaluation across dependent missions, a cyber-physical security dimension and a human-factors dimension. Each needs something a single-process simulation doesn't have: real hardware, employees, a network boundary or human behaviour to grade.

## The physical trust layer (distribution-centre floor)

The reference document asks whether this agent, robot and person can be trusted with *this* job. The distribution-centre floor ([spec §10](docs/superpowers/specs/2026-09-30-multi-embodiment-operations-design.md)) adds the physical half: can this body lift this load, reach this level, fit this aisle, fly over this cell, and work with these people around. It is checked before the job, re-checked during it and graded from its log afterwards. It applies only to robots with a floor profile (`robot.mobility`); classic robots and logs behave exactly as before.

### The eligibility rules (`backend/eligibility.py`)

Eight functions of plain values, each returning `(ok, reason)`, so the gate, robot selection, the mid-job re-check and the evaluation can't disagree.

| Rule | Holds when | Runs in |
|---|---|---|
| `payload_ok` | the weight is at most the body's payload | gate and selection (declared weight), re-check, `payload_within_limit` (true weight) |
| `box_kind_ok` | the body handles that box kind (`PALLET`, `TOTE`, `ITEM`, `CARTON`) | gate and selection |
| `reach_ok` | the slot level is within the body's reach | gate and selection, re-check, the `LIFT_TO` and `SCAN` steps, `reach_within_limit` |
| `clearance_ok` | a WIDE robot's route stays on WIDE cells | routing (`Warehouse.passable`), then `clearance_respected` |
| `no_fly_ok` | an air route avoids every no-fly cell | routing, then `no_fly_respected` |
| `drone_round_trip_ok` | a drone's battery covers the flight plus a 25 % reserve | gate and selection, drones only; a hard gate |
| `supervision_ok` | someone on shift holds a valid, in-scope credential of the kind the body needs | gate and selection, re-check; being *near* is a runtime wait |
| `cert_scope_ok` | a person's credential covers the equipment model and the site | the gate, for every certification check on the floor |

- **Gate** (`TaskManager.validate`, via `capability_reason`): the body the job needs, the box's kind and declared weight, the slot level, that supervision can be had, a drone's round trip. A job the body can't do is a `422` with the reason.
- **Selection**: `AUTO` keeps only robots whose body passes the same checks and that have a route under their own profile; a job no robot could do is rejected with the robots' reasons.
- **During the job**: `physical_recheck` repeats payload, reach and supervision from `_check_authorization_changes`. Like the rest of that re-check it flags and never cancels.
- **Evaluation**: the checks below grade the log, with the true weight and the level a box really reached.
- **Battery**: a ground robot with a critical battery gets a charging detour, not a rejection; only a drone, which can't detour mid-flight, is refused.

### Physical waits

Rules that depend on where people are hold the robot instead of rejecting the job. It goes `WAITING` with a `wait_reason`, and the log records `ROBOT_SAFETY_WAIT` and `ROBOT_SAFETY_RESUMED`.

| `wait_reason` | When |
|---|---|
| `PERSON_IN_AISLE` | a forklift or hauler would drive into a zone with a person in it |
| `PERSON_IN_CELL` | a person is inside an arm's pack cell |
| `SUPERVISOR_ABSENT` | the humanoid's supervisor is off shift, or not in its zone or one beside it |
| `PERSON_ON_CROSSING` | a robot is at a walkway crossing (or a drone about to cross) while someone walks across |
| `CONVEYOR_JAMMED` | an arm is downstream of a conveyor jam |

A wait longer than `SAFETY_WAIT_ESCALATE_S` emits `SAFETY_WAIT_ESCALATED` once, and the Shift panel lists it.

### The ten physical checks (`backend/eval_engine.py`)

`EMBODIMENT_CHECKS`, part of `DEFAULT_CHECKS`. Each answers "not applicable" (a `PASS` with `applicable: false`) when the log carries none of its data, so classic logs grade as before. Each has a pass and a fail fixture in `logs/eval_examples/multi_embodiment/`, graded by `backend/test_trust_checks.py`.

| Check | Fails when |
|---|---|
| `payload_within_limit` | a pick or lift's load truly weighs more than the robot's payload |
| `reach_within_limit` | a lift, scan or placement is above the robot's reach |
| `clearance_respected` | a WIDE robot's route crosses a NARROW cell |
| `no_fly_respected` | a drone's route crosses a no-fly cell |
| `human_zone_clear` | a forklift or hauler entered a zone, or an arm moved, while a person was there |
| `supervision_maintained` | the humanoid took a step unsupervised |
| `count_consistent` | a cycle count reported success with a count that isn't what was really there |
| `handoff_consistent` | a hand-off the giver reported made never reached the receiver |
| `sort_correct` | the sorter dropped a carton on a dock other than its order's lane |
| `placement_level_correct` | a box went to a different level from the one it was sent to |

### System checks (`backend/ci_engine.py`)

Mock CI follows the floor's layout: the environment check requires the layout's own `required_zones` and that the cells each body class uses (narrow ground, wide ground, air) are connected. A flying drone is accepted over any flyable cell and an arm on its station, mains-powered arms skip the battery check, and collisions compare `(layer, x, y)`, so a drone over a ground robot is not one.

### Injected faults (`backend/faults.py`)

Seven `CONFIG` risks, 0.0 by default. Each fault does its damage silently, and the trust layer catches it afterwards. Fault kinds and how to inject one on demand: [README.md](README.md#faults).

| Risk | What goes wrong | Caught by |
|---|---|---|
| `SCAN_MISCOUNT_RISK` | a drone's count is the true number ± 1–3 | `count_consistent` |
| `WRONG_LEVEL_RISK` | a forklift puts a pallet a level up or down but reports the requested one | `placement_level_correct`; the next count of that face finds the variance |
| `GRASP_FAIL_RISK` | an arm or the picker misses a grasp; two retries, and a third miss fails the job | no check of its own: each miss is on the job's GRASP step, and the third fails the job with that reason |
| `CONVEYOR_JAM_RISK` | a conveyor cell jams as an item moves on and the arms downstream pause | no check of its own: `CONVEYOR_JAMMED`, then a `CLEAR_JAM` job for a qualified person |
| `HANDOFF_LOSS_RISK` | an item put on the conveyor never arrives, though the giver reports it placed | `handoff_consistent` |
| `MIS_SORT_RISK` | the sorter drops a carton on the wrong dock | `sort_correct` |
| `MISDECLARED_WEIGHT_RISK` | an inbound pallet weighs 1.1–1.6 × what it declares | `payload_within_limit` |

`backend/test_fault_matrix.py` runs the seeded floor with each risk alone at 0.2 and asserts that its check (or, for the two without one, its evidence) catches it and no other check fails.

### Where it lives

| File | Role |
|---|---|
| `backend/eligibility.py` | the eight rules, beside the actor rules |
| `backend/embodiment.py` | `MobilityProfile`, a robot's body read from its catalog model |
| `backend/task_manager.py` | `capability_reason` (gate and selection), `physical_recheck` |
| `backend/people.py` | supervision queries, walkway crossings |
| `backend/simulator.py` | the physical waits, `ROBOT_STEP` events, where each fault does its damage |
| `backend/eval_engine.py`, `ci_engine.py` | the ten checks; the layout-driven system checks |
| `backend/faults.py` | `FAULT_RISKS`, `FaultInjector` |
