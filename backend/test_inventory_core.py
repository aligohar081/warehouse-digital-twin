"""ServiceCore: clock, transactions, change recording, listeners, change feed."""
from datetime import datetime, timedelta, timezone

import pytest

from backend.inventory.schema import connect, create_schema
from backend.inventory.service import InventoryService
from backend.inventory.store import InventoryStore

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def service():
    conn = connect(":memory:")
    create_schema(conn)
    return InventoryService(InventoryStore(conn), clock=lambda: NOW)


def _write(service, aggregate_type="ROBOT_ASSET", aggregate_id="AST-1", action="TEST", before=None, after=None, actor=None):
    with service.transaction():
        return service._record(
            aggregate_type=aggregate_type, aggregate_id=aggregate_id, revision=1, action=action,
            before=before, after=after if after is not None else {"v": 1}, actor=actor,
        )


def test_record_builds_a_change_with_diff_actor_and_timestamp(service):
    change = _write(service, before={"v": 0}, after={"v": 1})
    assert change["action"] == "TEST"
    assert change["diff"] == {"v": {"from": 0, "to": 1}}
    assert change["actor"] == {"type": "API", "id": None}
    assert change["occurred_at"] == "2026-09-30T12:00:00Z"
    assert change["seq"] == 1
    assert change["subject_id"] is None
    assert service.custom_clock is True


def test_listeners_fire_after_commit_and_not_on_rollback(service):
    seen = []
    service.subscribe(lambda change: seen.append((change["action"], service.store.depth)))
    _write(service, action="ONE")
    assert seen == [("ONE", 0)]  # delivered outside the transaction
    with pytest.raises(RuntimeError):
        with service.transaction():
            service._record(aggregate_type="WORKER", aggregate_id="E-1", revision=1, action="TWO", before=None, after={})
            raise RuntimeError("boom")
    assert seen == [("ONE", 0)]
    assert service.store.count("change_log") == 1


def test_at_backdates_the_clock_temporarily(service):
    earlier = NOW - timedelta(days=10)
    with service.at(earlier):
        assert service.now() == earlier
        change = _write(service)
    assert change["occurred_at"] == "2026-09-20T12:00:00Z"
    assert service.now() == NOW


def test_actor_validation_and_reasons(service):
    with pytest.raises(ValueError):
        _write(service, actor={"type": "ROBOT"})
    assert _write(service, actor={"type": "worker", "id": "E-1"})["actor"] == {"type": "WORKER", "id": "E-1"}
    assert service._reason("  why ") == "why"
    with pytest.raises(ValueError):
        service._reason("   ")


def test_feed_pages_with_cursor_and_filters_by_feed(service):
    for i in range(5):
        _write(service, aggregate_id=f"AST-{i}")
    _write(service, aggregate_type="WORKER", aggregate_id="E-1")
    page = service.changes("fleet", limit=2)
    assert [i["aggregate_id"] for i in page["items"]] == ["AST-0", "AST-1"]
    assert page["has_more"] and not page["resync_required"]
    page2 = service.changes("fleet", cursor=page["next_cursor"], limit=10)
    assert [i["aggregate_id"] for i in page2["items"]] == ["AST-2", "AST-3", "AST-4"]
    assert not page2["has_more"]
    empty = service.changes("fleet", cursor=page2["next_cursor"])
    assert empty["items"] == [] and empty["next_cursor"] == page2["next_cursor"]
    assert [i["aggregate_id"] for i in service.changes("workforce")["items"]] == ["E-1"]


def test_cursor_from_another_epoch_requires_resync(service):
    _write(service)
    stale = service.changes("fleet")["next_cursor"]
    service.store.rotate_epoch()
    page = service.changes("fleet", cursor=stale)
    assert page["resync_required"] is True
    assert len(page["items"]) == 1  # from the start of the current epoch
    assert page["next_cursor"].startswith(service.store.epoch() + ":")


def test_bad_feed_arguments_are_value_errors(service):
    with pytest.raises(ValueError):
        service.changes("payroll")
    with pytest.raises(ValueError):
        service.changes("fleet", cursor="garbage")
    with pytest.raises(ValueError):
        service.changes("fleet", limit="many")
    assert service.changes("fleet", limit=10_000)["items"] == []  # capped, not an error


def test_history_is_newest_first(service):
    _write(service, action="A")
    _write(service, action="B")
    _write(service, aggregate_id="AST-2", action="C")
    assert [c["action"] for c in service.history(["AST-1"])] == ["B", "A"]


def test_settings_fall_back_to_defaults(service):
    assert service.setting("CALIBRATION_DUE_SOON_DAYS") == 14
    custom = InventoryService(service.store, settings=lambda: {"CALIBRATION_DUE_SOON_DAYS": 3})
    assert custom.setting("CALIBRATION_DUE_SOON_DAYS") == 3
    assert custom.setting("REPORT_STALE_SECONDS") == 300
    assert custom.custom_clock is False
