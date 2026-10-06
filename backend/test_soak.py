"""The fixed-seed soak (multi-embodiment spec §16; §1's success criteria).

The distribution-centre floor with its full seed (15 robots, 10 workers, the
goods) runs the shift at seed 42 and pace 2 for 20 000 ticks (50 sim-minutes)
with every risk at 0, and finishes with no same-layer collision, no failure of
any new evaluation check over the jobs that finished, at least one completed
order of every kind, no robot in ERROR, no safety escalation (the soak ends
before the first break, at two sim-hours), and a mean tick within the 15 ms
budget.

Why the run is deterministic: the shift engine draws from its own
random.Random, seeded by the shift seed, and run_soak seeds the module-level
random for the run too. With every risk at 0, FaultInjector.roll draws no
number at all. Simulator._act_deliver does draw — `random.random()` for
FALSE_SUCCESS_RISK on every delivery — but against a risk of 0 the answer is
always "no", so the draw never changes what happens. (backend/test_shift_soak.py
says "a zero risk never draws a random number"; that file can't be edited, so
the correct reason is given here.)
"""
import dataclasses
import random

import pytest

from backend.eval_engine import EMBODIMENT_CHECKS
from backend.models import CONFIG
from backend.operations.orders import ORDER_KINDS
from backend.soak import (FAULT_MATRIX_RISK, RISK_KEYS, SOAK_PACE, SOAK_SEED, SOAK_TICKS, TICK_BUDGET_MS, main,
                          run_soak, soak_risks)

NEW_CHECKS = [check.__name__[len("check_"):] for check in EMBODIMENT_CHECKS]


@pytest.fixture(scope="module")
def soak():
    return run_soak()      # spec §16: distribution_center, seed 42, pace 2, 20 000 ticks, faults off


def timeless(result):
    """A result without its wall-clock timings, which differ run to run."""
    data = dataclasses.asdict(result)
    del data["mean_tick_ms"], data["max_tick_ms"]
    return data


def test_the_soak_runs_the_spec_shift_with_faults_off(soak):
    assert (SOAK_TICKS, SOAK_SEED, SOAK_PACE) == (20000, 42, 2.0)
    assert (soak.ticks, soak.seed, soak.pace, soak.risks) == (20000, 42, 2.0, {})
    assert soak.sim_minutes == 50.0                          # 20 000 × TICK_DT 0.15 s
    assert soak.grasp_misses == 0 and soak.failed_jobs == []


def test_no_same_layer_collisions(soak):
    assert soak.collisions == 0
    assert soak.events.get("COLLISION_DETECTED", 0) == 0


def test_no_new_check_fails_and_every_new_check_graded_real_data(soak):
    assert soak.check_failures == {}, soak.check_examples
    # Zero failures only means something if each check read its data: a
    # check that applied to no job would pass whatever the floor did.
    assert sorted(soak.check_applied) == sorted(NEW_CHECKS)
    assert all(soak.check_applied[name] >= 1 for name in NEW_CHECKS), soak.check_applied


def test_orders_of_every_kind_complete(soak):
    assert all(soak.orders_done.get(kind, 0) >= 1 for kind in ORDER_KINDS), soak.orders_done
    assert soak.orders_failed == {}


def test_no_robot_is_left_in_error(soak):
    assert soak.robots_in_error == []


def test_no_safety_escalations(soak):
    assert soak.escalations == {} and soak.escalation_count == 0


def test_the_mean_tick_is_within_the_budget(soak):
    # Spec §1: at most 15 ms with 15 robots on the dev machine (this plan's
    # Measured section records the figure).
    assert 0 < soak.mean_tick_ms <= TICK_BUDGET_MS
    assert soak.max_tick_ms >= soak.mean_tick_ms


def test_the_soak_counts_every_event_not_the_twins_buffer(soak):
    # The twin keeps only the last MAX_EVENTS_IN_MEMORY events; the soak sees
    # nearly four times that. Grading from the buffer (as the plan 1b probe
    # did) would lose the early jobs' events: measured, it grades the scans of
    # 2 of the 10 counts and the sorting of 10 of the 26 customer cartons.
    assert sum(soak.events.values()) > 3 * CONFIG["MAX_EVENTS_IN_MEMORY"]
    assert soak.jobs_graded == sum(soak.events.get(kind, 0)
                                   for kind in ("TASK_COMPLETED", "TASK_FAILED", "TASK_CANCELLED"))
    assert soak.jobs_graded >= 300
    assert soak.check_applied["count_consistent"] >= soak.orders_done["COUNT"]
    assert soak.check_applied["sort_correct"] >= soak.orders_done["CUSTOMER"]


def test_the_same_seed_gives_the_same_run_even_with_a_fault():
    # A risk above 0 draws from the module-level random. run_soak seeds it
    # with the run's seed, so two runs agree in everything but the clock
    # whatever state it was in before; without that seeding they differ.
    random.seed(1)
    first = run_soak(ticks=1500, risks={"handoff_loss": FAULT_MATRIX_RISK})
    random.seed(2)
    second = run_soak(ticks=1500, risks={"handoff_loss": FAULT_MATRIX_RISK})
    assert first.check_failures.get("handoff_consistent", 0) >= 1
    assert timeless(first) == timeless(second)


def test_a_run_puts_config_and_the_random_state_back():
    # Fails if run_soak leaves its risks in CONFIG: every later test would run with faults on.
    before = {key: CONFIG[key] for key in RISK_KEYS}
    random.seed(7)
    state = random.getstate()
    result = run_soak(ticks=10, risks={"MIS_SORT_RISK": 0.5, "grasp-fail": 0.25})
    assert result.risks == {"MIS_SORT_RISK": 0.5, "GRASP_FAIL_RISK": 0.25}
    assert {key: CONFIG[key] for key in RISK_KEYS} == before
    assert random.getstate() == state


def test_risks_are_named_by_fault_kind_or_config_key_and_checked():
    settings = soak_risks({"conveyor_jam": 0.2, "FALSE_SUCCESS_RISK": 0.1})
    assert set(settings) == set(RISK_KEYS) and {"COLLISION_RISK", "OTA_FAILURE_RISK"} <= set(RISK_KEYS)
    assert {key: value for key, value in settings.items() if value} == {"CONVEYOR_JAM_RISK": 0.2,
                                                                        "FALSE_SUCCESS_RISK": 0.1}
    with pytest.raises(ValueError, match="Unknown risk 'gremlins'"):
        soak_risks({"gremlins": 0.2})
    for bad in (1.5, -0.1, "0.2", True):
        with pytest.raises(ValueError, match="must be a number from 0 to 1"):
            soak_risks({"mis_sort": bad})
    with pytest.raises(ValueError, match="ticks must be a whole number"):
        run_soak(ticks=0)
    with pytest.raises(ValueError, match="pace must be a number above 0"):
        run_soak(ticks=10, pace=0)


def test_the_command_line_prints_the_report(capsys):
    assert main(["--ticks", "40", "--risk", "conveyor_jam=0.2"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("Soak: distribution_center, seed 42, pace 2, 40 ticks (0.1 sim-minutes)\n")
    assert "Risks: CONVEYOR_JAM_RISK 0.2" in out and "Mean tick:" in out and "Same-layer collisions: 0" in out
    with pytest.raises(SystemExit) as stop:
        main(["--risk", "conveyor_jam"])
    assert stop.value.code == 2
    assert "expected KIND=CHANCE" in capsys.readouterr().err
