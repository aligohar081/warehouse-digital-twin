"""The fault matrix (multi-embodiment spec §16): each injected fault (§10.6) at a
risk of 0.2 is caught by its matching check.

Each row is the §16 soak — the seeded distribution-centre floor, shift seed 42,
pace 2 — with that one risk at 0.2 and every other risk at 0, cut to the
shortest run in which its evidence shows up at least twice (the ticks it
shows up at are in the comments; the runs are deterministic, see
backend/test_soak.py). Five faults have a §10.4 check of their own. Two don't
(Plan ruling 11): a GRASP_FAIL is a grasp the arm or the picker misses — the
job's GRASP step records the miss and retries it, and only a third miss in a
row fails the job, "missed the grasp 3 times" (backend/test_job_steps.py pins
that; at 0.2 it is 0.8 % of grasps, and the seed-42 shift sees none in 150
sim-minutes) — and a CONVEYOR_JAM emits CONVEYOR_JAMMED and raises a CLEAR_JAM.
A WRONG_LEVEL placement is also flagged later, as a stock variance, when a count
reaches that rack face (plan 1b ruling 4); at seed 42 the first such count
comes about 9 500 ticks after the misplacement, past the run below.
"""
import pytest

from backend.eval_engine import EMBODIMENT_CHECKS
from backend.faults import FAULT_RISKS
from backend.soak import FAULT_MATRIX_RISK, run_soak

#: Fault kind -> the ticks its run takes.
MATRIX = {
    "scan_miscount": 3000,         # count_consistent at ticks 686 and 2 649
    "wrong_level": 20000,          # placement_level_correct at 13 035 and 17 926
    "grasp_fail": 2000,            # misses at ticks 217, 1 323, 1 520 and 1 943
    "conveyor_jam": 1000,          # CONVEYOR_JAMMED at 249 and 544, a CLEAR_JAM at 260 and 560
    "handoff_loss": 2000,          # handoff_consistent at 231, 1 150 and 1 631
    "mis_sort": 7000,              # sort_correct at 1 389 and 6 476
    "misdeclared_weight": 6000,    # payload_within_limit at 4 277 and 5 118
}

#: Fault kind -> its §10.4 check.
CHECKS = {
    "scan_miscount": "count_consistent",
    "wrong_level": "placement_level_correct",
    "handoff_loss": "handoff_consistent",
    "mis_sort": "sort_correct",
    "misdeclared_weight": "payload_within_limit",
}


def faulted_run(kind):
    result = run_soak(ticks=MATRIX[kind], risks={kind: FAULT_MATRIX_RISK})
    assert result.risks == {FAULT_RISKS[kind]: 0.2}          # that one risk, at 0.2, and nothing else
    return result


def test_the_matrix_covers_every_injected_fault():
    assert sorted(MATRIX) == sorted(FAULT_RISKS)
    assert set(CHECKS) | {"grasp_fail", "conveyor_jam"} == set(FAULT_RISKS)
    assert set(CHECKS.values()) <= {check.__name__[len("check_"):] for check in EMBODIMENT_CHECKS}


@pytest.mark.parametrize("kind", sorted(CHECKS))
def test_a_fault_is_caught_by_its_matching_check(kind):
    result = faulted_run(kind)
    check = CHECKS[kind]
    # Fails if the fault stops doing what its check reads (for example the
    # sorter's lane roll, the forklift's level, or the drone's count), or if
    # the check stops reading it.
    assert result.check_failures.get(check, 0) >= 2, (result.check_failures, result.check_examples)
    # And it is that check that catches it: no other new check fails.
    assert set(result.check_failures) == {check}, result.check_examples


def test_a_grasp_fail_is_a_miss_the_job_records_and_retries():
    result = faulted_run("grasp_fail")
    # Fails if a miss stops being recorded on its GRASP step (Simulator._finish_grasp).
    assert result.grasp_misses >= 2
    # The job retries the missed grasp: none failed for anything else, and
    # any that ran out of retries failed with the grasp reason.
    assert all("missed the grasp 3 times" in job["error"] for job in result.failed_jobs), result.failed_jobs
    assert result.check_failures == {}
    assert result.orders_done.get("CUSTOMER", 0) >= 1          # picks and packs still finish


def test_a_conveyor_jam_emits_conveyor_jammed_and_raises_a_clear_jam():
    result = faulted_run("conveyor_jam")
    jams = result.events.get("CONVEYOR_JAMMED", 0)
    assert jams >= 2
    # Fails if a jam stops asking for someone to clear it (human_jobs.ensure_jam_jobs).
    assert result.jobs_created.get("CLEAR_JAM", 0) >= jams
    assert result.events.get("CONVEYOR_CLEARED", 0) >= 1      # and someone did
    assert result.check_failures == {}
