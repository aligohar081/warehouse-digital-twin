"""The fixed-seed soak (multi-embodiment spec §16, and §1's success criteria).

`run_soak()` boots the distribution-centre floor with its full seed
(`DigitalTwin.seed_floor()`: the 15 robots, the 10 workers, the goods), starts
the shift at the given seed and pace with every risk at 0 except the ones it is
asked for, ticks the simulator, and reports what the shift did:

- same-layer collisions (`COLLISION_DETECTED`: two robots on one cell of one layer);
- the new evaluation checks (spec §10.4, `eval_engine.EMBODIMENT_CHECKS`), graded
  over the events of every job that finished;
- orders completed and failed, per kind;
- robots left in `ERROR`;
- safety escalations (`SAFETY_WAIT_ESCALATED`), per wait reason;
- the grasps the arms and the picker missed (a GRASP_FAIL), and the jobs that failed;
- the mean and the longest tick, in ms.

Everything is counted from the runner's own subscription to `twin.events`, which
sees every event. The twin's in-memory buffer keeps only the last
`MAX_EVENTS_IN_MEMORY` (5 000), and the 20 000-tick soak emits nearly four times that,
so grading from it would lose most of the shift.

The run is repeatable. The shift engine draws from its own `random.Random`,
seeded by `seed`. The module-level `random` is seeded with `seed` too for the
run (and put back afterwards): the fault rolls of a run with a risk draw from
it. With every risk at 0 nothing depends on it — `FaultInjector.roll` draws no
number at all, and the FALSE_SUCCESS draw `_act_deliver` makes on every
delivery always answers "no".

    python -m backend.soak                                # spec §16: seed 42, pace 2, 20 000 ticks
    python -m backend.soak --ticks 6000 --risk grasp_fail=0.2
"""
from __future__ import annotations

import argparse
import math
import os
import random
import tempfile
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .digital_twin import DigitalTwin
from .eval_engine import EMBODIMENT_CHECKS, Verdict, evaluate_events
from .faults import FAULT_RISKS, fault_kind
from .models import CONFIG, ActionType, RobotStatus, SimulationStatus
from .operations.orders import ORDER_KINDS
from .simulator import Simulator

#: Spec §16's soak: the new floor, seed 42, pace 2, 20 000 ticks (50 sim-minutes).
SOAK_LAYOUT = "distribution_center"
SOAK_TICKS = 20000
SOAK_SEED = 42
SOAK_PACE = 2.0
#: Spec §1: a mean tick of at most 15 ms with 15 robots.
TICK_BUDGET_MS = 15.0
#: Spec §16's fault matrix sets each risk to this.
FAULT_MATRIX_RISK = 0.2

#: Every CONFIG risk. "Faults off" is all of them at 0: the injected faults
#: (spec §10.6) and the older COLLISION_RISK, FALSE_SUCCESS_RISK and OTA_FAILURE_RISK.
RISK_KEYS = tuple(sorted(key for key in CONFIG if key.endswith("_RISK")))

#: The events that end a job.
FINISHING_EVENTS = ("TASK_COMPLETED", "TASK_FAILED", "TASK_CANCELLED")


@dataclass
class SoakResult:
    """What one soak run saw."""

    ticks: int
    seed: int
    pace: float
    risks: Dict[str, float]                      # the CONFIG risks the run set above 0
    sim_minutes: float
    collisions: int                              # COLLISION_DETECTED events
    jobs_graded: int                             # finished jobs the new checks graded
    check_applied: Dict[str, int]                # new check -> finished jobs whose events it read
    check_failures: Dict[str, int]               # new check -> finished jobs it failed
    check_examples: Dict[str, str]               # new check -> its first failure's message
    orders_done: Dict[str, int]                  # order kind -> ORDER_COMPLETED
    orders_failed: Dict[str, int]                # order kind -> ORDER_FAILED
    robots_in_error: List[str]                   # robots in ERROR at the end
    escalations: Dict[str, int]                  # wait reason -> SAFETY_WAIT_ESCALATED
    jobs_created: Dict[str, int]                 # job type -> TASK_CREATED
    failed_jobs: List[Dict[str, str]]            # {"task_id", "type", "error"}, each job that ended FAILED
    grasp_misses: int                            # misses recorded on the jobs' GRASP steps
    events: Dict[str, int] = field(default_factory=dict)   # event type -> how many
    mean_tick_ms: float = 0.0
    max_tick_ms: float = 0.0

    @property
    def rule_check_failures(self) -> int:
        """How many (finished job, new check) pairs failed."""
        return sum(self.check_failures.values())

    @property
    def escalation_count(self) -> int:
        return sum(self.escalations.values())

    def report(self) -> str:
        """The run as plain text, for `python -m backend.soak`."""
        risks = ", ".join(f"{key} {value:g}" for key, value in sorted(self.risks.items())) or "none (faults off)"
        lines = [
            f"Soak: {SOAK_LAYOUT}, seed {self.seed}, pace {self.pace:g}, {self.ticks} ticks "
            f"({self.sim_minutes:.1f} sim-minutes)",
            f"Risks: {risks}",
            f"Mean tick: {self.mean_tick_ms:.3f} ms (longest {self.max_tick_ms:.1f} ms; budget {TICK_BUDGET_MS:g} ms)",
            f"Same-layer collisions: {self.collisions}",
            f"New checks: {self.rule_check_failures} failure(s) over {self.jobs_graded} finished job(s)",
        ]
        for name, count in sorted(self.check_failures.items()):
            lines.append(f"  {name}: {count} — e.g. {self.check_examples[name]}")
        lines.append("Orders done: " + ", ".join(f"{kind} {self.orders_done.get(kind, 0)}" for kind in ORDER_KINDS))
        if self.orders_failed:
            lines.append("Orders failed: " + ", ".join(f"{kind} {count}"
                                                       for kind, count in sorted(self.orders_failed.items())))
        lines.append(f"Robots in ERROR: {', '.join(self.robots_in_error) or 'none'}")
        lines.append("Safety escalations: " + (", ".join(f"{reason} {count}" for reason, count
                                                         in sorted(self.escalations.items())) or "none"))
        lines.append(f"Grasp misses: {self.grasp_misses}")
        lines.append(f"Jobs that failed: {len(self.failed_jobs)}")
        for job in self.failed_jobs[:10]:
            lines.append(f"  {job['task_id']} {job['type']}: {job['error']}")
        return "\n".join(lines)


def soak_risks(risks: Optional[Dict[str, float]] = None) -> Dict[str, float]:
    """Every CONFIG risk at 0, then `risks` on top. A key is a fault kind
    (`faults.FAULT_RISKS`, e.g. "grasp_fail") or a CONFIG risk name (e.g.
    "FALSE_SUCCESS_RISK"); a value is a chance between 0 and 1."""
    settings = {key: 0.0 for key in RISK_KEYS}
    for name, value in (risks or {}).items():
        kind = fault_kind(name)
        key = FAULT_RISKS[kind] if kind is not None else str(name).strip().upper()
        if key not in settings:
            raise ValueError(f"Unknown risk {name!r} (fault kinds: {sorted(FAULT_RISKS)}; "
                             f"CONFIG risks: {list(RISK_KEYS)})")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0.0 <= value <= 1.0:
            raise ValueError(f"The {key} risk must be a number from 0 to 1 (got {value!r})")
        settings[key] = float(value)
    return settings


class _Tally:
    """The runner's own event subscription: counts every event and keeps each
    job's events, so the jobs can be graded once the run is over (a carton's
    sorter hand-off is recorded against its PACK_ORDER after that job ends)."""

    def __init__(self) -> None:
        self.events: Counter = Counter()
        self.collisions = 0
        self.escalations: Counter = Counter()
        self.orders_done: Counter = Counter()
        self.orders_failed: Counter = Counter()
        self.jobs_created: Counter = Counter()
        self.job_events: Dict[str, List[Dict[str, Any]]] = {}
        self.finished: Dict[str, str] = {}       # task id -> the event that ended it

    def on_event(self, event: Dict[str, Any]) -> None:
        kind = event.get("event")
        data = event.get("data") or {}
        self.events[kind] += 1
        if kind == "COLLISION_DETECTED":
            self.collisions += 1
        elif kind == "SAFETY_WAIT_ESCALATED":
            self.escalations[str(data.get("reason"))] += 1
        elif kind == "ORDER_COMPLETED":
            self.orders_done[data.get("kind")] += 1
        elif kind == "ORDER_FAILED":
            self.orders_failed[data.get("kind")] += 1
        elif kind == "TASK_CREATED":
            self.jobs_created[data.get("task_type")] += 1
        task_id = event.get("task_id")
        if task_id:
            self.job_events.setdefault(task_id, []).append(event)
            if kind in FINISHING_EVENTS:
                self.finished[task_id] = kind


def _grade(tally: _Tally, checks: Sequence[Any]) -> Dict[str, Any]:
    """Each finished job's events through the new checks."""
    applied: Counter = Counter()
    failures: Counter = Counter()
    examples: Dict[str, str] = {}
    for task_id in tally.finished:
        report = evaluate_events(tally.job_events[task_id], task_id=task_id, checks=checks)
        for check in report.checks:
            if check.applicable:
                applied[check.name] += 1
            if check.verdict is Verdict.FAIL:
                failures[check.name] += 1
                examples.setdefault(check.name, f"{task_id}: {check.message}")
    return {"check_applied": dict(applied), "check_failures": dict(failures), "check_examples": examples}


def run_soak(ticks: int = SOAK_TICKS, seed: int = SOAK_SEED, pace: float = SOAK_PACE,
             risks: Optional[Dict[str, float]] = None) -> SoakResult:
    """Run the shift on the seeded new floor for `ticks` ticks and report it.
    `risks` sets CONFIG risks for this run only (see `soak_risks`); every
    other risk is 0. CONFIG and the module-level random state are put back
    afterwards."""
    if isinstance(ticks, bool) or not isinstance(ticks, int) or ticks < 1:
        raise ValueError("ticks must be a whole number, at least 1")
    if isinstance(pace, bool) or not isinstance(pace, (int, float)) or not math.isfinite(pace) or pace <= 0:
        raise ValueError("pace must be a number above 0")
    settings = soak_risks(risks)
    saved = {key: CONFIG[key] for key in settings}
    random_state = random.getstate()
    try:
        CONFIG.update(settings)
        random.seed(seed)
        with tempfile.TemporaryDirectory(prefix="soak-", ignore_cleanup_errors=True) as work:
            return _run(work, ticks, seed, float(pace), settings)
    finally:
        CONFIG.update(saved)
        random.setstate(random_state)


def _run(work: str, ticks: int, seed: int, pace: float, settings: Dict[str, float]) -> SoakResult:
    twin = DigitalTwin(log_dir=os.path.join(work, "logs"), data_dir=os.path.join(work, "data"),
                       persist_logs=False, demo=True, demo_tasks=False, layout=SOAK_LAYOUT)
    twin.seed_floor()
    tally = _Tally()
    twin.events.subscribe(tally.on_event)
    sim = Simulator(twin)
    twin.simulation_status = SimulationStatus.RUNNING
    twin.shift.configure(seed=seed, pace=pace)
    twin.shift.start()
    total = longest = 0.0
    for _ in range(ticks):
        started = time.perf_counter()
        sim.tick()
        took = time.perf_counter() - started
        total += took
        longest = max(longest, took)
    failed = [{"task_id": task_id, "type": twin.tasks.get(task_id).type.value,
               "error": twin.tasks.get(task_id).error or ""}
              for task_id, how in tally.finished.items() if how == "TASK_FAILED" and twin.tasks.get(task_id)]
    # A missed grasp is retried, so it leaves no event: the GRASP step keeps
    # count of its misses (Simulator._finish_grasp), and the twin keeps every job.
    misses = sum(int(action.params.get("misses", 0)) for task in twin.tasks.tasks.values()
                 for action in task.actions if action.type is ActionType.GRASP)
    return SoakResult(
        ticks=ticks, seed=seed, pace=pace,
        risks={key: value for key, value in settings.items() if value > 0},
        sim_minutes=round(twin.simulation_time / 60.0, 1),
        collisions=tally.collisions,
        jobs_graded=len(tally.finished),
        **_grade(tally, EMBODIMENT_CHECKS),
        orders_done=dict(tally.orders_done),
        orders_failed=dict(tally.orders_failed),
        robots_in_error=sorted(robot.name for robot in twin.robots.values() if robot.status is RobotStatus.ERROR),
        escalations=dict(tally.escalations),
        jobs_created=dict(tally.jobs_created),
        failed_jobs=failed,
        grasp_misses=misses,
        events=dict(tally.events),
        mean_tick_ms=1000.0 * total / ticks,
        max_tick_ms=1000.0 * longest,
    )


def _risk_arg(text: str) -> Tuple[str, float]:
    """`--risk KIND=CHANCE`, e.g. grasp_fail=0.2."""
    name, sep, value = text.partition("=")
    if not sep:
        raise argparse.ArgumentTypeError(f"expected KIND=CHANCE, e.g. grasp_fail=0.2 (got {text!r})")
    try:
        return name.strip(), float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{value!r} is not a number") from None


def main(argv: Optional[Sequence[str]] = None) -> int:
    """`python -m backend.soak`: run the soak and print its report."""
    parser = argparse.ArgumentParser(prog="python -m backend.soak", description=__doc__.splitlines()[0])
    parser.add_argument("--ticks", type=int, default=SOAK_TICKS, help=f"ticks to run (default {SOAK_TICKS})")
    parser.add_argument("--seed", type=int, default=SOAK_SEED, help=f"seed of the shift and the fault rolls (default {SOAK_SEED})")
    parser.add_argument("--pace", type=float, default=SOAK_PACE, help=f"shift pace (default {SOAK_PACE:g})")
    parser.add_argument("--risk", type=_risk_arg, action="append", default=[], metavar="KIND=CHANCE",
                        help="set a risk for this run, e.g. grasp_fail=0.2 (repeatable; default: all 0)")
    args = parser.parse_args(argv)
    try:
        result = run_soak(ticks=args.ticks, seed=args.seed, pace=args.pace, risks=dict(args.risk))
    except ValueError as exc:
        parser.error(str(exc))
    print(result.report())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
