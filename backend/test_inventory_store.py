"""Inventory storage foundation: schema, store helpers, document diffing."""
import sqlite3
from datetime import datetime, timezone

import pytest

from backend.inventory.documents import diff_documents, flatten, iso, parse_iso, to_datetime
from backend.inventory.errors import Conflict, NotFound
from backend.inventory.schema import SCHEMA_VERSION, connect, create_schema, is_current
from backend.inventory.store import InventoryStore


@pytest.fixture
def store():
    conn = connect(":memory:")
    create_schema(conn)
    return InventoryStore(conn)


def _worker_row(worker_id):
    return {
        "worker_id": worker_id, "display_name": "Sam", "worker_type": "EMPLOYEE",
        "organization": "Ops", "role_codes": [], "site_codes": [], "employment_status": "ACTIVE",
        "supervisor_id": None,
        "revision": 1, "updated_at": "2026-01-01T00:00:00Z",
    }


def test_schema_creates_every_table_and_meta(store):
    tables = {r["name"] for r in store.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for name in (
        "meta", "manufacturer", "part_model", "robot_model", "software_release", "robot_asset",
        "component", "calibration_record", "work_order", "ota_job", "reported_state", "worker",
        "credential_definition", "worker_credential", "training_completion", "change_log",
    ):
        assert name in tables
    assert store.get_meta("schema_version") == str(SCHEMA_VERSION)
    assert len(store.epoch()) == 8
    assert not is_current(store.conn)
    store.set_meta("seeded", "1")
    assert is_current(store.conn)


def test_json_columns_round_trip_and_plain_columns_stay_plain(store):
    store.insert("manufacturer", {"manufacturer_id": "ACME", "name": "Acme Robotics", "serial_prefix": "ACM"})
    store.insert("part_model", {
        "part_number": "P-1", "manufacturer_id": "ACME", "name": "Lidar", "component_type": "LIDAR",
        "hw_revisions": ["A", "B"], "firmware_capable": True, "calibration_interval_days": 365,
        "spec": {"range_m": 40},
    })
    row = store.get("part_model", "part_number", "P-1")
    assert row["hw_revisions"] == ["A", "B"]
    assert row["spec"] == {"range_m": 40}
    assert row["firmware_capable"] == 1
    store.update("part_model", "part_number", "P-1", {"spec": {"range_m": 60}})
    assert store.get("part_model", "part_number", "P-1")["spec"] == {"range_m": 60}
    assert store.get("part_model", "part_number", "missing") is None
    assert store.exists("part_model", "part_number", "P-1")
    assert store.count("part_model", "component_type = ?", ("LIDAR",)) == 1


def test_transaction_rolls_back_everything_on_error(store):
    with pytest.raises(RuntimeError):
        with store.transaction():
            store.insert("manufacturer", {"manufacturer_id": "A", "name": "A", "serial_prefix": "A"})
            with store.transaction():  # nested joins the outer transaction
                store.insert("manufacturer", {"manufacturer_id": "B", "name": "B", "serial_prefix": "B"})
            raise RuntimeError("boom")
    assert store.count("manufacturer") == 0
    assert store.depth == 0


class _FailFirstCommit:
    """Delegates to a real connection but makes the first COMMIT fail like a locked database."""

    def __init__(self, conn):
        self._conn = conn
        self.commit_attempts = 0

    def execute(self, sql, *args):
        if sql == "COMMIT":
            self.commit_attempts += 1
            if self.commit_attempts == 1:
                raise sqlite3.OperationalError("database is locked")
        return self._conn.execute(sql, *args)

    def __getattr__(self, name):
        return getattr(self._conn, name)


def test_a_failed_commit_rolls_back_instead_of_wedging_the_connection(store):
    real = store.conn
    store.conn = _FailFirstCommit(real)
    with pytest.raises(sqlite3.OperationalError, match="database is locked"):
        with store.transaction():
            store.insert("manufacturer", {"manufacturer_id": "A", "name": "A", "serial_prefix": "A"})
    assert store.depth == 0
    assert not real.in_transaction
    assert store.count("manufacturer") == 0
    with store.transaction():  # the next transaction starts and commits normally
        store.insert("manufacturer", {"manufacturer_id": "B", "name": "B", "serial_prefix": "B"})
    assert store.depth == 0 and not real.in_transaction
    assert [row["manufacturer_id"] for row in store.select("manufacturer")] == ["B"]


def test_file_databases_use_wal_so_outside_readers_never_block_commits(tmp_path):
    path = str(tmp_path / "inventory.sqlite3")
    conn = connect(path)
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert connect(":memory:").execute("PRAGMA journal_mode").fetchone()[0] == "memory"


def test_next_id_skips_ids_that_already_exist(store):
    store.insert("worker", _worker_row("E-10001"))
    assert store.next_id("E-", "worker", "worker_id", 5, start=10001) == "E-10002"
    assert store.next_id("E-", "worker", "worker_id", 5, start=10001) == "E-10003"
    assert store.bump_counter("serial:ACME", start=1001) == 1001
    assert store.bump_counter("serial:ACME", start=1001) == 1002


def test_change_log_append_and_paged_reads(store):
    for i in range(3):
        store.append_change({
            "occurred_at": "2026-01-01T00:00:00Z",
            "aggregate_type": "WORKER" if i == 1 else "ROBOT_ASSET",
            "aggregate_id": f"X-{i}", "revision": 1, "action": "TEST", "subject_id": None,
            "actor_type": "SYSTEM", "actor_id": None, "reason": None,
            "diff": {"a": {"from": None, "to": i}}, "after": {"a": i},
        })
    robot_rows = store.changes(["ROBOT_ASSET"], after_seq=0, limit=10)
    assert [r["aggregate_id"] for r in robot_rows] == ["X-0", "X-2"]
    assert robot_rows[0]["diff"] == {"a": {"from": None, "to": 0}}
    later = store.changes(["ROBOT_ASSET"], after_seq=robot_rows[0]["seq"], limit=10)
    assert [r["aggregate_id"] for r in later] == ["X-2"]
    assert [r["aggregate_id"] for r in store.history(["X-0", "X-1"], limit=10)] == ["X-1", "X-0"]


def test_flatten_keys_lists_of_entities_by_their_id():
    doc = {
        "a": 1,
        "components": [{"component_id": "C1", "fw": "1.0"}, {"component_id": "C2", "fw": "2.0"}],
        "tags": ["x", "y"],
        "empty": {},
    }
    assert flatten(doc) == {
        "a": 1,
        "components[C1].component_id": "C1", "components[C1].fw": "1.0",
        "components[C2].component_id": "C2", "components[C2].fw": "2.0",
        "tags": ["x", "y"],
        "empty": {},
    }


def test_diff_documents_reports_changed_added_and_removed_leaves():
    before = {"status": "IN_SERVICE", "components": [{"component_id": "C1", "fw": "1.0"}], "gone": 1}
    after = {
        "status": "MAINTENANCE",
        "components": [{"component_id": "C1", "fw": "1.1"}, {"component_id": "C2", "fw": "2.0"}],
    }
    diff = diff_documents(before, after)
    assert diff["status"] == {"from": "IN_SERVICE", "to": "MAINTENANCE"}
    assert diff["components[C1].fw"] == {"from": "1.0", "to": "1.1"}
    assert diff["components[C2].fw"] == {"from": None, "to": "2.0"}
    assert diff["gone"] == {"from": 1, "to": None}
    assert "components[C1].component_id" not in diff
    assert diff_documents(None, {"x": None}) == {"x": {"from": None, "to": None}}


def test_time_helpers_are_utc_and_round_trip():
    moment = datetime(2026, 9, 30, 12, 0, 5, tzinfo=timezone.utc)
    assert iso(moment) == "2026-09-30T12:00:05Z"
    assert iso(None) is None
    assert parse_iso("2026-09-30T12:00:05Z") == moment
    assert to_datetime("2026-09-30") == datetime(2026, 9, 30, tzinfo=timezone.utc)
    assert to_datetime(moment) == moment
    assert to_datetime(None) is None
    with pytest.raises(ValueError):
        to_datetime("not a date")


def test_error_types_map_to_builtin_families():
    assert isinstance(NotFound("x"), KeyError)
    assert str(NotFound("Robot 'X' does not exist")) == "Robot 'X' does not exist"
    assert isinstance(Conflict("y"), ValueError)
