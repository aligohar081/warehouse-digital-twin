# Handoff: plan 1c is written and committed — waiting on plan review and the execution choice

**Date:** 2026-10-06 · **Branch:** `feature/multi-embodiment-1c` (from `master` at `67af707`; nothing pushed)
**Read first:** `CLAUDE.md` (rules and commands), then the plan. `ARCHITECTURE.md` maps the code.

## State

- Plan 1c: `docs/superpowers/plans/2026-10-05-multi-embodiment-1c-seed-api-dashboard-soak.md` (12 tasks, 28 Plan rulings, Measured section at the end). Written in `3e99df8`; Task 12 rewritten in `39d14c4` so README, RUN.md and TRUST_LAYER.md come out short (about 165, 100 and 130 lines). Amended for `bb1fb42` (below): Task 2's `_seed_floor` block and every suite count (+3). Verified on `bb1fb42`: all 227 edit blocks apply, and the suite on the result is `2 failed, 923 passed` (the allowed two). Baseline before Task 1: `2 failed, 771 passed`.
- `bb1fb42` — the new floor boots with its shift paused (and a reset pauses it), so it runs only the tasks the user assigns, as classic does; the user asked for this ahead of plan 1c. Until Tasks 5 and 10 land, nothing can start the shift. `test_app_layout.py`'s two running-shift assertions were flipped with the user's OK; new tests in `backend/test_app_shift_paused.py`.
- `bf86fe2` — `CLAUDE.md`, `ARCHITECTURE.md`, `.claude/settings.json` (`autoCompactWindow: 200000`).
- `3466cac` — removed the superseded handoffs, plan 1b's probe scripts and tracked `.DS_Store` files.
- `docs/handoffs/2026-10-04-plan-1c-inputs/` (removed once plan 1c landed; see git history): the plan cites its README for plan 1b's deferred minors.

## Next

1. The user reviews the plan (open points below) and chooses execution: **subagent-driven (recommended)** or inline. Under subagent-driven development the controller runs Task 9's and Task 10's **Browser check (controller)** steps itself, on ports 5070 (new floor) and 5071 (classic) — never 5050, the user's own server. §16 check 4 (humanoid pauses on Jordan's break) needs 2 sim-hours: run at 10×. Check 5 injects `conveyor_jam` until the jam lands on (22,15) or (23,15) (ruling 20).
2. When plan 1c lands: refresh `ARCHITECTURE.md` (fold its section 10 into the rest, and re-verify against the new HEAD) in its own commit, and remove `docs/handoffs/2026-10-04-plan-1c-inputs/`.

## Open points for plan review

- **Ruling 28, tote preference:** a free tote only the humanoid can reach is taken rather than waiting for a busy one the AMRs reach.
- **Task 10's form:** the destination select has no "Default", so UNLOAD_TRUCK and RETRIEVE_PALLET from the dashboard send the picked destination, not their own default.
- **Rulings that depart from the spec:** 2, 5, 14, 19, 26. Task 12 amends the spec only for 2 (§2) and 5 (§4.3) plus §6's walkway wording; it leaves §4.2 spawn placement (ruling 15), §4.3 `reset()` (ruling 4) and §4.3's save key list (`people`/`handoffs` live inside `operators`/`equipment`) as they are. Say if those should be amended too.
- **Task 12 pins `policies.example.yaml` to the built-in policy** (a test makes them match), so a later default change must update the example. It also fixes the example's stale `robot_classes`, which stopped the new floor's seed with "Unknown robot_class 'ARM'".
- **The short docs drop three promptfoo details** with no other home: the 19-row example-logs table, the one-log Groq demo snippet and the Groq timing note. `evals/README.md` is where they would go (outside Task 12's files). `evals/README.md` also still uses `npx promptfoo`, which RUN.md advises against.
- **Tick:** mean 0.57–0.62 ms at the §16 soak (budget 15 ms); 1.75 ms at 150 sim-minutes as the order book grows (ruling 7, no pruning); unexplained single-tick spikes of 30–38 ms.
- **Follow-up, needs the user's OK:** `logs/tasks/task_00*.json` are tracked runtime logs that every run appends to; tests read them, so moving the ones tests need into `logs/eval_examples/` means editing an existing test file.
