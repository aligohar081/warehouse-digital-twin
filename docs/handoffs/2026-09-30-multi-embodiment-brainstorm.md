# Handoff — Multi-embodiment warehouse expansion (brainstorming in progress)

**Date:** 2026-09-30
**Status:** Brainstorming (superpowers:brainstorming, *architectural* path). No code written. No spec written yet.
**Repo:** `physical_ai/warehouse-digital-twin`

## Goal

Expand the warehouse digital twin from "one kind of robot with different presets" into a
realistic multi-embodiment warehouse — forklifts/lifters, robotic arms, drones, humanoids,
conveyors — so it looks and behaves like a real physical warehouse.

**Primary purpose (decided):** *trust-layer demo realism.* Each embodiment must earn its place
by creating distinct, physically grounded eligibility checks and failure modes for the
pre-execution gate (`backend/eligibility.py`) and the eval engine (`backend/eval_engine.py`).
Visuals only need to be clear, not fancy.

## Current state (verified in code)

- `backend/models.py::ROBOT_CLASS_PRESETS` already has `AMR`, `FORKLIFT`, `SCOUT`,
  `HEAVY_HAULER`, `DRONE`, `PICKER` — but these are **only `speed` + `allowed_task_types`
  presets**. Every robot is physically identical: a 1-cell ground vehicle on a 20×15 grid
  using A\* (`backend/navigation.py`), moved only in `Simulator._tick_robot`
  (`backend/simulator.py`).
- No payload/weight, no shelf height, no footprint, no air layer, no fixed stations.
- Eligibility rule lives once in `backend/eligibility.py`, shared by the gate
  (`TaskManager.validate()` / `select_robot()`) and the grade (`check_entities_valid`) —
  keep it that way.
- Mid-task re-checks: `Simulator._check_authorization_changes` (flags, never gates).
- Policy-as-code: `backend/policy.py` mutates `models.py` constants in place from `policies.yaml`.
- Test suite: README claims 253 pytest tests (`backend/tests.py`, `backend/test_eval_engine.py`,
  `backend/test_agent_chat.py`). Not run in this session.

## Decisions made

| # | Question | Decision |
|---|---|---|
| 1 | Purpose of expansion | **A — trust-layer demo realism** |
| 2 | Spatial model | **A — keep the 2D grid, add layers/rules**: ground layer (AMR, forklift, humanoid), **air layer** for drones (fly over racking, respect no-fly cells, don't block ground traffic), **fixed stations** for arms (act on adjacent cells only), **footprint/clearance** for big robots (e.g. forklifts barred from narrow aisles), **shelf levels 0–4** as an attribute (not real 3D). Rejected: continuous 2.5D (rewrite cost, little trust value), pure metadata (drone through racking looks fake). |

## Open question (was on the table when session paused)

**Robot roster.** Recommended: **1–5 + 8**.

| # | Type | Movement | Physical job | Trust-layer case |
|---|---|---|---|---|
| 1 | AMR / tote carrier (existing) | Ground, 1 cell | Totes/boxes ≤ 50 kg, levels 0–1 | Baseline |
| 2 | Forklift / pallet lifter | Ground, 2-cell footprint, wide aisles only | Pallets ≤ 1000 kg, levels 0–4 | Payload/reach exceeded; narrow-aisle block; human-zone exclusion |
| 3 | Robotic arm | Fixed at a station | Item pick tote→carton at packing/induction | Safety cell interlock when a human is inside; cobot rating; firmware |
| 4 | Drone | Air layer, no-fly cells, short battery | Inventory scans, light inspection | No-fly violation; insufficient round-trip battery; scan mismatch → false-success |
| 5 | Humanoid | Ground, narrow aisles, slower | Totes levels 0–2, shared-human areas | Needs certified supervising operator; strictest firmware + AI model baseline |
| 6 | Goods-to-person shelf lifter (Kiva) | Ground, under racks | Brings rack to pick station | Racking itself moves — location integrity |
| 7 | Tugger / tow train | Ground, fixed routes | Carts of multiple boxes | Multi-box arrival consistency |
| 8 | Conveyor / sorter (infrastructure) | Fixed line | Induction → packing → loading | **Hand-off points** where boxes go missing |
| 9 | Inspection quadruped (Spot) | Ground, legged | Patrol, thermal inspection | Restricted-area access; data-capture permission |

Rationale for 8: the conveyor creates cross-embodiment hand-offs
(AMR → conveyor → arm → conveyor → forklift), the realistic source of "said success, didn't happen" failures.

## Proposed decomposition (one spec, built in order)

1. **Embodiment model** — movement mode (ground/air/fixed/legged), payload, reach height, footprint, per-type battery profile.
2. **Warehouse realism** — shelf levels, inbound docks, conveyor lines, pallet vs tote vs item, larger floor, human walkways / narrow vs wide aisles, no-fly cells.
3. **New task types** requiring specific embodiments — arm pick-to-tote, drone inventory scan, forklift put-away at height, humanoid tote handling, multi-robot hand-off chains.
4. **Trust-layer extensions** — per-embodiment eligibility in `eligibility.py` (payload, reach, zone, air/ground, human-proximity, supervision) + new eval checks and `logs/eval_examples/` fixtures.
5. **Dashboard** — distinct glyphs per type, air-layer overlay, station views.

Split into sub-specs if it grows too large.

## Remaining brainstorming steps

1. Get roster answer (above).
2. Remaining clarifying questions, likely: floor size/layout change; item model (pallet/tote/item granularity); how humans are represented spatially (operators currently have no position — needed for arm safety cells, forklift human-zone exclusion, humanoid supervision); backwards compatibility of the existing demo seed and 253 tests.
3. Propose 2–3 implementation approaches with a recommendation.
4. Present design section by section, get approval on each.
5. Write spec to `docs/superpowers/specs/2026-09-30-multi-embodiment-warehouse-design.md`, self-review, user review.
6. Invoke `superpowers:writing-plans`.

## Constraints to carry forward

- `CHARGE_ROBOT` must stay in every class's `allowed_task_types` (auto-recharge is a gated task; see README "Robot classes").
- Critical battery is deliberately **not** hard-gated (planner inserts a recharge detour) — preserve that exception, but drone round-trip range is a candidate new gate.
- Digital twin is the only source of truth; motion only in `Simulator.tick`, never JS.
- Demo seed (`Sam`/`Lee`) intentionally unchanged — some behaviours depend on it.
- `TRUST_LAYER.md` Tier 2 list looks stale vs README (policy-as-code, mid-task auth changes, decision history appear implemented) — reconcile when updating docs.

## Related, parked thread

Separate plan (not started) to build **`enterprise-sim`**: stub ERP / MES / CMMS / HRIS / LMS /
fleet / OT-security systems with their own storage and IDs, feeding `physical-work-assurance`
connectors → identity resolution → unified physical-entity record → task-time checks.
Open decision there: standalone `physical_ai/enterprise-sim` (recommended) vs `sim/` package inside PWA.
The new embodiments from this work would become richer fleet/CMMS entities for that sim.

---

## UPDATE (same day, later session) — program re-scoped

User clarified the real goal: **simulate a real-world physical AI system**, multi-layered —
multi-embodiment ops, a robot + worker inventory with real-world detail stored in a unified DB,
a passport pipeline (change robot info → passport updates), and realistic per-job robot logs
(sensor data + decisions).

**Additional decisions:** roster = 1–5 + conveyor · humans = zone presence · items = one Box +
kind (ITEM/TOTE/PALLET) + real weight + shelf_level · layouts = keep `classic` 20×15 byte-for-byte
(tests) + add `multi_embodiment` ~32×20 · app boots multi-embodiment, `DigitalTwin()` default stays
classic · approach A (embodiment profiles as data, rules as shared primitive-arg functions) ·
forklift footprint = wide-aisle clearance class (assumed, not yet explicitly confirmed) ·
**PWA's Postgres is the single unified DB + passport engine; the twin is the realistic
fleet-manager/workforce/telemetry source.**

**Program decomposition (approved order A → B → C → D → E → F):**
- **A** Fleet & workforce inventory (twin): robot *model catalog* (fictional OEM spec sheets —
  replaces per-class EMBODIMENT_PROFILES), robot instance records (serial, hw rev, per-component
  firmware, calibration, maintenance), worker records within PWA's allowlist, revisioned change feed.
- **B** Twin → PWA ingestion + robot-change → passport re-mint (PWA).
- **C** Multi-embodiment operations (the design above; limits read from A's catalog).
- **D** Runtime telemetry & decision logs (sensor streams w/ noise/drift/faults, decisions).
- **E** Conveyor & hand-off chains.
- **F** Closed trust loop (twin gate consults PWA; eval reported-vs-true).

**Next:** brainstorm sub-project A.
