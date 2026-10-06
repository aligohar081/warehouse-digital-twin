"""A scheduled floor job is refused a field its job does not take, as the
gate refuses it: the schedule's runs are internal and skip that check."""

from __future__ import annotations

import pytest

from backend.digital_twin import DigitalTwin


def test_a_schedule_with_a_stray_slot_on_a_tote_job_is_refused() -> None:
    twin = DigitalTwin(layout="distribution_center")
    with pytest.raises(ValueError, match="takes no 'slot'"):
        twin.scheduler.add({"type": "TOTE_TO_STATION", "slot": "A-01-0", "station": "PICK-1"},
                           interval_ticks=10)
    assert twin.scheduler.schedules == {}


def test_a_classic_schedule_is_unchanged() -> None:
    twin = DigitalTwin()
    schedule = twin.scheduler.add({"type": "CHARGE"}, interval_ticks=10)
    assert schedule.id in twin.scheduler.schedules
