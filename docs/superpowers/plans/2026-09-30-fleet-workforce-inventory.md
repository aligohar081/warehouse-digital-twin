# Fleet & Workforce Inventory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the warehouse digital twin realistic fleet-manager and workforce *source systems* — a SQLite-backed robot model catalog, serialized robot assets with calibration / maintenance / OTA history, a worker registry with credentials — that every live robot and operator is bound to, with a revisioned change feed PWA can ingest later.

**Architecture:** A new `backend/inventory/` package (imports nothing from the twin) holds the database, the lifecycle-action service and the change log. `backend/fleet_bridge.py` binds live `Robot`/`Operator` objects to inventory records and owns every *physical* effect (heartbeats, OTA installs over simulator ticks, stopping a robot whose asset leaves service, syncing operator certifications). `backend/inventory_api.py` exposes REST routes; `frontend/fleet.html` is the dashboard page.

**Tech Stack:** Python 3 stdlib `sqlite3`, Flask 3, pytest, vanilla JS/CSS (no new dependencies).

**Spec:** `docs/superpowers/specs/2026-09-30-fleet-workforce-inventory-design.md`

## Global Constraints

- No new dependencies: stdlib `sqlite3` only; `requirements.txt` is unchanged.
- `backend/inventory/` must never import from `backend` modules outside the package (it is a standalone "source system").
- Behaviour neutrality: robot speed / allowed task types still come from `ROBOT_CLASS_PRESETS`; eligibility code is untouched; demo robots `Robo-01`/`Robo-02` keep firmware `2.1.0`; demo operators keep their certifications in the same order.
- Lock order is always `twin.lock` → inventory store lock. Every inventory **mutation** from the API or simulator goes through `twin.fleet.mutate(...)` (or runs inside code that already holds `twin.lock`). Service listeners run after the SQLite transaction commits.
- IDs are the source system's own: assets `AST-000123`, components `CMP-0000123`, calibrations `CAL-0000123`, work orders `WO-000123`, OTA jobs `OTA-000123`, workers `E-10001`, credentials `CRD-000123`, training `TRN-000123`; release ids `<target_code>:<SW|FW|AI>:<version>`.
- Timestamps are UTC ISO-8601 with a `Z` suffix (`2026-09-30T12:00:00Z`).
- Privacy: worker records hold only PWA's §26.1 allowlist (no compensation, performance, health, demographic or disciplinary fields); credential numbers are stored only as `sha256:` hashes.
- Existing suite baseline: **256 passed, 2 failed** (`backend/test_eval_engine.py::test_real_task_006_box_conflict_is_caught`, `backend/tests.py::test_idle_robot_with_a_low_battery_charges_itself` — pre-existing, tracked separately). After every task the existing suites must still show exactly those 2 failures and nothing else.
- Run tests with `.venv/bin/python -m pytest` from the repo root (`warehouse-digital-twin/`).
- Commit messages end with the line `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## File Map

| File | Responsibility |
|---|---|
| `backend/inventory/__init__.py` | Package exports (`open_inventory`, `reseed`, `InventoryService`, errors) |
| `backend/inventory/errors.py` | `NotFound(KeyError)` → 404, `Conflict(ValueError)` → 409 |
| `backend/inventory/documents.py` | UTC time helpers, `flatten`, `diff_documents` |
| `backend/inventory/schema.py` | DDL, `connect`, `create_schema`, `is_current`, `new_epoch` |
| `backend/inventory/store.py` | `InventoryStore`: row encode/decode, transactions, id counters, change-log reads/writes |
| `backend/inventory/core.py` | `ServiceCore`: clock, settings, `_tx`, `_record`, listeners, change feed, history |
| `backend/inventory/catalog_data.py` | Fictional manufacturers, part models, robot models, release tables, credential definitions |
| `backend/inventory/sbom.py` | Deterministic CycloneDX 1.5 SBOM builder |
| `backend/inventory/catalog.py` | `CatalogMixin`: catalog reads, `publish_release`, `recall_release` |
| `backend/inventory/fleet.py` | `FleetMixin`: commission, lifecycle, admin edit, reported state, robot read models + flags |
| `backend/inventory/servicing.py` | `ServicingMixin`: work orders, component swaps, calibration |
| `backend/inventory/ota.py` | `OtaMixin`: OTA job state machine |
| `backend/inventory/workforce.py` | `WorkforceMixin`: workers, credentials, training |
| `backend/inventory/service.py` | `InventoryService` composed from the mixins |
| `backend/inventory/seed.py` | `seed_catalog` |
| `backend/inventory/demo_seed.py` | `seed_demo` (demo fleet + workforce) |
| `backend/inventory/bootstrap.py` | `open_inventory`, `reseed`, seeded-template cache |
| `backend/fleet_bridge.py` | `FleetBridge`: binding, heartbeat, OTA runtime, lifecycle/credential effects |
| `backend/inventory_api.py` | `register_inventory_routes(app, twin, api_error)` |
| `frontend/fleet.html`, `frontend/fleet.js`, `frontend/fleet.css` | Fleet & Workforce page |
| Modified: `backend/models.py`, `backend/robot.py`, `backend/operator.py`, `backend/digital_twin.py`, `backend/simulator.py`, `backend/app.py`, `frontend/index.html`, `frontend/app.js`, `frontend/style.css`, `.gitignore`, `README.md` | Wiring |
| Tests: `backend/test_inventory_store.py`, `test_inventory_core.py`, `test_inventory_catalog.py`, `test_inventory_fleet.py`, `test_inventory_servicing.py`, `test_inventory_ota.py`, `test_inventory_workforce.py`, `test_inventory_seed.py`, `test_fleet_bridge.py`, `test_inventory_api.py` | |

---

### Task 1: Inventory storage foundation

**Files:**
- Create: `backend/inventory/__init__.py`, `backend/inventory/errors.py`, `backend/inventory/documents.py`, `backend/inventory/schema.py`, `backend/inventory/store.py`
- Test: `backend/test_inventory_store.py`

**Interfaces:**
- Produces: `NotFound`, `Conflict`; `utc_now() -> datetime`, `iso(dt) -> str|None`, `parse_iso(str) -> datetime|None`, `to_datetime(value) -> datetime|None`, `flatten(doc) -> dict`, `diff_documents(before, after) -> dict`; `SCHEMA_VERSION`, `JSON_COLUMNS`, `connect(path)`, `create_schema(conn)`, `is_current(conn)`, `new_epoch()`; `InventoryStore(conn)` with `.lock`, `.depth`, `transaction()`, `get_meta/set_meta/epoch/rotate_epoch`, `bump_counter(name, start)`, `next_id(prefix, table, key_column, width, start=1)`, `insert/update/get/select/exists/count`, `append_change(entry) -> int`, `changes(types, after_seq, limit)`, `history(ids, limit)`.

- [ ] **Step 1: Write the failing tests** — create `backend/test_inventory_store.py`:

```python
"""Inventory storage foundation: schema, store helpers, document diffing."""
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
        "supervisor_id": None, "shift_start_hour": None, "shift_end_hour": None,
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest backend/test_inventory_store.py -q`
Expected: collection error — `ModuleNotFoundError: No module named 'backend.inventory'`.

- [ ] **Step 3: Implement** — create the five files.

`backend/inventory/__init__.py`:

```python
"""The twin's fleet-manager and workforce *source systems*.

See docs/superpowers/specs/2026-09-30-fleet-workforce-inventory-design.md.
This package imports nothing from the rest of the twin so it can later be
lifted out into a standalone service unchanged.
"""
```

`backend/inventory/errors.py`:

```python
"""Inventory error types, mapped to HTTP status codes by backend/inventory_api.py."""
from __future__ import annotations


class NotFound(KeyError):
    """The referenced record does not exist (HTTP 404)."""

    def __str__(self) -> str:  # KeyError's default str() wraps the message in quotes
        return str(self.args[0]) if self.args else "Not found"


class Conflict(ValueError):
    """The request is valid but conflicts with the record's current state (HTTP 409)."""
```

`backend/inventory/documents.py`:

```python
"""UTC time helpers and document diffing shared by the inventory service.

`diff_documents` flattens two JSON-like documents into dotted leaf paths and
reports every leaf that changed. Lists of entity dicts are keyed by their id
field (component_id, wo_id, ...) so a change reads
``components[CMP-0000012].firmware_release_id`` instead of a list index.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

ID_KEYS = ("component_id", "calibration_id", "wo_id", "job_id", "credential_id", "training_id")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(moment: Optional[datetime]) -> Optional[str]:
    if moment is None:
        return None
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def to_datetime(value: Any) -> Optional[datetime]:
    """Accept a datetime, an ISO-8601 string (date-only is fine) or None."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        return parse_iso(str(value))
    except ValueError:
        raise ValueError(f"Invalid date {value!r} (expected ISO-8601, e.g. 2026-12-31)") from None


def _item_id(item: Any) -> Optional[str]:
    if not isinstance(item, dict):
        return None
    for key in ID_KEYS:
        if item.get(key):
            return str(item[key])
    return None


def flatten(doc: Any, prefix: str = "") -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    if isinstance(doc, dict):
        if not doc and prefix:
            out[prefix] = {}
        for key in sorted(doc):
            path = f"{prefix}.{key}" if prefix else str(key)
            out.update(flatten(doc[key], path))
    elif isinstance(doc, list) and doc and all(_item_id(item) for item in doc):
        for item in doc:
            out.update(flatten(item, f"{prefix}[{_item_id(item)}]"))
    else:
        out[prefix] = doc
    return out


def diff_documents(before: Optional[dict], after: Optional[dict]) -> Dict[str, Dict[str, Any]]:
    old, new = flatten(before or {}), flatten(after or {})
    diff: Dict[str, Dict[str, Any]] = {}
    for path in sorted(set(old) | set(new)):
        if (path in old) != (path in new) or old.get(path) != new.get(path):
            diff[path] = {"from": old.get(path), "to": new.get(path)}
    return diff
```

`backend/inventory/schema.py`:

```python
"""SQLite schema for the twin's fleet-manager and workforce source systems.

These tables are the *source systems* PWA ingests from (sub-project B), so
they use their own IDs and shapes the way a real fleet vendor or HR system
would. Columns named in JSON_COLUMNS hold JSON text and are encoded/decoded
by InventoryStore; everything else is a plain SQLite value.
"""
from __future__ import annotations

import secrets
import sqlite3

SCHEMA_VERSION = 1

JSON_COLUMNS = frozenset({
    "hw_revisions", "spec", "safety_standards", "component_layout",
    "role_codes", "site_codes", "issuer_types", "scope_dimensions",
    "equipment_scope", "task_scope", "site_scope",
    "component_firmware", "battery", "diff", "after",
})

DDL = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS manufacturer (
    manufacturer_id TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    serial_prefix   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS part_model (
    part_number               TEXT PRIMARY KEY,
    manufacturer_id           TEXT NOT NULL REFERENCES manufacturer (manufacturer_id),
    name                      TEXT NOT NULL,
    component_type            TEXT NOT NULL,
    hw_revisions              TEXT NOT NULL,
    firmware_capable          INTEGER NOT NULL,
    calibration_interval_days INTEGER,
    spec                      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS robot_model (
    model_code       TEXT PRIMARY KEY,
    manufacturer_id  TEXT NOT NULL REFERENCES manufacturer (manufacturer_id),
    name             TEXT NOT NULL,
    embodiment_class TEXT NOT NULL,
    hw_revisions     TEXT NOT NULL,
    spec             TEXT NOT NULL,
    safety_standards TEXT NOT NULL,
    component_layout TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS software_release (
    release_id  TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_code TEXT NOT NULL,
    version     TEXT NOT NULL,
    released_at TEXT NOT NULL,
    min_hw_rev  TEXT,
    status      TEXT NOT NULL,
    sbom_json   TEXT NOT NULL,
    sbom_sha256 TEXT NOT NULL,
    UNIQUE (kind, target_code, version)
);
CREATE TABLE IF NOT EXISTS robot_asset (
    asset_id             TEXT PRIMARY KEY,
    asset_tag            TEXT NOT NULL UNIQUE,
    serial_number        TEXT NOT NULL,
    manufacturer_id      TEXT NOT NULL REFERENCES manufacturer (manufacturer_id),
    model_code           TEXT NOT NULL REFERENCES robot_model (model_code),
    hw_revision          TEXT NOT NULL,
    fleet_id             TEXT,
    site_code            TEXT NOT NULL,
    home_zone            TEXT,
    lifecycle_status     TEXT NOT NULL,
    commissioned_at      TEXT NOT NULL,
    decommissioned_at    TEXT,
    software_release_id  TEXT NOT NULL REFERENCES software_release (release_id),
    config_hash          TEXT NOT NULL,
    safety_policy_hash   TEXT NOT NULL,
    ai_policy_release_id TEXT REFERENCES software_release (release_id),
    revision             INTEGER NOT NULL,
    updated_at           TEXT NOT NULL,
    UNIQUE (manufacturer_id, serial_number)
);
CREATE TABLE IF NOT EXISTS component (
    component_id        TEXT PRIMARY KEY,
    asset_id            TEXT NOT NULL REFERENCES robot_asset (asset_id),
    slot                TEXT NOT NULL,
    part_number         TEXT NOT NULL REFERENCES part_model (part_number),
    serial              TEXT NOT NULL,
    hw_revision         TEXT NOT NULL,
    firmware_release_id TEXT REFERENCES software_release (release_id),
    installed_at        TEXT NOT NULL,
    removed_at          TEXT,
    status              TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS calibration_record (
    calibration_id  TEXT PRIMARY KEY,
    component_id    TEXT NOT NULL REFERENCES component (component_id),
    performed_at    TEXT NOT NULL,
    performed_by    TEXT,
    method          TEXT NOT NULL,
    result          TEXT NOT NULL,
    valid_until     TEXT,
    certificate_ref TEXT
);
CREATE TABLE IF NOT EXISTS work_order (
    wo_id         TEXT PRIMARY KEY,
    asset_id      TEXT NOT NULL REFERENCES robot_asset (asset_id),
    type          TEXT NOT NULL,
    description   TEXT NOT NULL,
    technician_id TEXT,
    status        TEXT NOT NULL,
    opened_at     TEXT NOT NULL,
    closed_at     TEXT,
    resolution    TEXT
);
CREATE TABLE IF NOT EXISTS ota_job (
    job_id         TEXT PRIMARY KEY,
    asset_id       TEXT NOT NULL REFERENCES robot_asset (asset_id),
    component_id   TEXT REFERENCES component (component_id),
    release_id     TEXT NOT NULL REFERENCES software_release (release_id),
    from_version   TEXT,
    state          TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    created_by     TEXT,
    failure_reason TEXT
);
CREATE TABLE IF NOT EXISTS reported_state (
    asset_id           TEXT PRIMARY KEY REFERENCES robot_asset (asset_id),
    software_version   TEXT,
    os_version         TEXT,
    config_hash        TEXT,
    safety_policy_hash TEXT,
    ai_policy_version  TEXT,
    component_firmware TEXT NOT NULL,
    health_state       TEXT NOT NULL,
    connectivity       TEXT NOT NULL,
    operational_mode   TEXT,
    zone               TEXT,
    battery            TEXT,
    reported_at        TEXT NOT NULL,
    observation_seq    INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS worker (
    worker_id         TEXT PRIMARY KEY,
    display_name      TEXT NOT NULL,
    worker_type       TEXT NOT NULL,
    organization      TEXT NOT NULL,
    role_codes        TEXT NOT NULL,
    site_codes        TEXT NOT NULL,
    employment_status TEXT NOT NULL,
    supervisor_id     TEXT,
    shift_start_hour  INTEGER,
    shift_end_hour    INTEGER,
    revision          INTEGER NOT NULL,
    updated_at        TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS credential_definition (
    code                TEXT PRIMARY KEY,
    name                TEXT NOT NULL,
    issuer_types        TEXT NOT NULL,
    renewal_period_days INTEGER,
    scope_dimensions    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS worker_credential (
    credential_id           TEXT PRIMARY KEY,
    worker_id               TEXT NOT NULL REFERENCES worker (worker_id),
    code                    TEXT NOT NULL REFERENCES credential_definition (code),
    issuer                  TEXT NOT NULL,
    credential_number_hash  TEXT NOT NULL,
    verification_status     TEXT NOT NULL,
    effective_from          TEXT NOT NULL,
    expires_at              TEXT,
    equipment_scope         TEXT NOT NULL,
    task_scope              TEXT NOT NULL,
    site_scope              TEXT NOT NULL,
    supervision_requirement TEXT,
    revoked_at              TEXT,
    revocation_reason       TEXT
);
CREATE TABLE IF NOT EXISTS training_completion (
    training_id    TEXT PRIMARY KEY,
    worker_id      TEXT NOT NULL REFERENCES worker (worker_id),
    course_code    TEXT NOT NULL,
    course_version TEXT NOT NULL,
    completed_at   TEXT NOT NULL,
    expires_at     TEXT
);
CREATE TABLE IF NOT EXISTS change_log (
    seq            INTEGER PRIMARY KEY AUTOINCREMENT,
    occurred_at    TEXT NOT NULL,
    aggregate_type TEXT NOT NULL,
    aggregate_id   TEXT NOT NULL,
    revision       INTEGER NOT NULL,
    action         TEXT NOT NULL,
    subject_id     TEXT,
    actor_type     TEXT NOT NULL,
    actor_id       TEXT,
    reason         TEXT,
    diff           TEXT NOT NULL,
    after          TEXT
);
CREATE INDEX IF NOT EXISTS change_log_by_type ON change_log (aggregate_type, seq);
CREATE INDEX IF NOT EXISTS change_log_by_aggregate ON change_log (aggregate_id, seq);
CREATE INDEX IF NOT EXISTS component_by_asset ON component (asset_id, status);
CREATE INDEX IF NOT EXISTS calibration_by_component ON calibration_record (component_id, performed_at);
CREATE INDEX IF NOT EXISTS credential_by_worker ON worker_credential (worker_id);
"""


def connect(path: str) -> sqlite3.Connection:
    """Open `path` (":memory:" for an in-process database) in autocommit mode —
    InventoryStore issues its own BEGIN/COMMIT."""
    conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def new_epoch() -> str:
    return secrets.token_hex(4)


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(DDL)
    conn.execute(
        "INSERT OR IGNORE INTO meta (key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),)
    )
    conn.execute("INSERT OR IGNORE INTO meta (key, value) VALUES ('epoch', ?)", (new_epoch(),))


def is_current(conn: sqlite3.Connection) -> bool:
    """True when `conn` already holds a fully seeded database of this schema version."""
    try:
        rows = conn.execute(
            "SELECT key, value FROM meta WHERE key IN ('schema_version', 'seeded')"
        ).fetchall()
    except sqlite3.DatabaseError:
        return False
    values = {row["key"]: row["value"] for row in rows}
    return values.get("schema_version") == str(SCHEMA_VERSION) and values.get("seeded") == "1"
```

`backend/inventory/store.py`:

```python
"""Thin sqlite3 repository for the inventory: row encoding, transactions,
id counters and the append-only change log. Business rules live in the
service mixins, never here. Table and column names are internal constants;
every value is passed as a bound parameter."""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Optional, Sequence

from .schema import JSON_COLUMNS, new_epoch

CHANGE_COLUMNS = (
    "occurred_at", "aggregate_type", "aggregate_id", "revision", "action",
    "subject_id", "actor_type", "actor_id", "reason", "diff", "after",
)


def _encode(column: str, value: Any) -> Any:
    if column in JSON_COLUMNS:
        return None if value is None else json.dumps(value, sort_keys=True)
    if isinstance(value, bool):
        return int(value)
    return value


def _decode(row: Optional[sqlite3.Row]) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    out: Dict[str, Any] = {}
    for key in row.keys():
        value = row[key]
        if key in JSON_COLUMNS and value is not None:
            value = json.loads(value)
        out[key] = value
    return out


class InventoryStore:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.lock = threading.RLock()
        self.depth = 0

    # ---- transactions -------------------------------------------------- #
    @contextmanager
    def transaction(self) -> Iterator[None]:
        """One SQLite transaction; nested calls join the outer one."""
        with self.lock:
            if self.depth == 0:
                self.conn.execute("BEGIN IMMEDIATE")
            self.depth += 1
            try:
                yield
            except BaseException:
                self.depth -= 1
                if self.depth == 0:
                    self.conn.execute("ROLLBACK")
                raise
            self.depth -= 1
            if self.depth == 0:
                self.conn.execute("COMMIT")

    # ---- meta ----------------------------------------------------------- #
    def get_meta(self, key: str) -> Optional[str]:
        with self.lock:
            row = self.conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self.lock:
            self.conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value))

    def epoch(self) -> str:
        return self.get_meta("epoch") or ""

    def rotate_epoch(self) -> str:
        epoch = new_epoch()
        self.set_meta("epoch", epoch)
        return epoch

    def bump_counter(self, name: str, start: int = 1) -> int:
        """Increment and return the named counter; the first call returns `start`."""
        with self.lock:
            current = self.get_meta(f"counter:{name}")
            value = int(current) + 1 if current is not None else start
            self.set_meta(f"counter:{name}", str(value))
            return value

    def next_id(self, prefix: str, table: str, key_column: str, width: int, start: int = 1) -> str:
        """The next unused `prefix` + zero-padded number in `table`."""
        with self.lock:
            while True:
                candidate = f"{prefix}{self.bump_counter(prefix, start):0{width}d}"
                if not self.exists(table, key_column, candidate):
                    return candidate

    # ---- rows ----------------------------------------------------------- #
    def insert(self, table: str, row: Dict[str, Any]) -> None:
        columns = list(row)
        sql = f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})"
        with self.lock:
            self.conn.execute(sql, [_encode(c, row[c]) for c in columns])

    def update(self, table: str, key_column: str, key: Any, fields: Dict[str, Any]) -> None:
        if not fields:
            return
        assignments = ", ".join(f"{column} = ?" for column in fields)
        values = [_encode(c, v) for c, v in fields.items()] + [key]
        with self.lock:
            self.conn.execute(f"UPDATE {table} SET {assignments} WHERE {key_column} = ?", values)

    def get(self, table: str, key_column: str, key: Any) -> Optional[Dict[str, Any]]:
        with self.lock:
            row = self.conn.execute(f"SELECT * FROM {table} WHERE {key_column} = ?", (key,)).fetchone()
        return _decode(row)

    def select(
        self,
        table: str,
        where: str = "",
        params: Sequence[Any] = (),
        order: str = "rowid",
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        sql = f"SELECT * FROM {table}"
        if where:
            sql += f" WHERE {where}"
        if order:
            sql += f" ORDER BY {order}"
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        with self.lock:
            rows = self.conn.execute(sql, list(params)).fetchall()
        return [_decode(row) for row in rows]

    def exists(self, table: str, key_column: str, key: Any) -> bool:
        with self.lock:
            row = self.conn.execute(f"SELECT 1 FROM {table} WHERE {key_column} = ?", (key,)).fetchone()
        return row is not None

    def count(self, table: str, where: str = "", params: Sequence[Any] = ()) -> int:
        sql = f"SELECT COUNT(*) AS n FROM {table}" + (f" WHERE {where}" if where else "")
        with self.lock:
            return int(self.conn.execute(sql, list(params)).fetchone()["n"])

    # ---- change log ----------------------------------------------------- #
    def append_change(self, entry: Dict[str, Any]) -> int:
        sql = (
            f"INSERT INTO change_log ({', '.join(CHANGE_COLUMNS)}) "
            f"VALUES ({', '.join('?' for _ in CHANGE_COLUMNS)})"
        )
        with self.lock:
            cursor = self.conn.execute(sql, [_encode(c, entry.get(c)) for c in CHANGE_COLUMNS])
            return int(cursor.lastrowid)

    def changes(self, aggregate_types: Sequence[str], after_seq: int, limit: int) -> List[Dict[str, Any]]:
        marks = ", ".join("?" for _ in aggregate_types)
        return self.select(
            "change_log", f"aggregate_type IN ({marks}) AND seq > ?",
            [*aggregate_types, int(after_seq)], order="seq", limit=limit,
        )

    def history(self, aggregate_ids: Sequence[str], limit: int) -> List[Dict[str, Any]]:
        marks = ", ".join("?" for _ in aggregate_ids)
        return self.select(
            "change_log", f"aggregate_id IN ({marks})", list(aggregate_ids),
            order="seq DESC", limit=limit,
        )
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest backend/test_inventory_store.py -q`
Expected: `9 passed`.

- [ ] **Step 5: Commit**

```bash
git add backend/inventory backend/test_inventory_store.py
git commit -m "feat(inventory): SQLite storage foundation for fleet/workforce source systems

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Service core — clock, transactions, change recording, change feed

**Files:**
- Create: `backend/inventory/core.py`, `backend/inventory/service.py`
- Test: `backend/test_inventory_core.py`

**Interfaces:**
- Consumes: Task 1 (`InventoryStore`, `diff_documents`, `iso`, `utc_now`, `NotFound`).
- Produces: `DEFAULT_SETTINGS`, `SYSTEM = {"type": "SYSTEM", "id": None}`, `actor_of(actor) -> {"type","id"}`, `sha256_text(text) -> "sha256:<hex>"`, `FEEDS = {"fleet": (...), "workforce": ("WORKER",)}`, `MAX_FEED_LIMIT = 500`, `change_view(row) -> dict`. `ServiceCore(store, clock=None, settings=None)` with `.store`, `.custom_clock: bool`, `now() -> datetime`, `now_iso() -> str`, `at(when)` (context manager), `setting(key)`, `subscribe(listener)`, `transaction()` (public alias of `_tx`), `_tx()`, `_record(*, aggregate_type, aggregate_id, revision, action, before, after, actor=None, reason=None, subject_id=None) -> change dict`, `_require(table, key_column, key, label) -> row`, `_reason(text) -> str`, `_worker_actor(worker_id, actor)`, `changes(feed, cursor=None, limit=100) -> {"epoch","items","next_cursor","has_more","resync_required"}`, `history(aggregate_ids, limit=200) -> [change]`. Change dicts have keys `seq, occurred_at, aggregate_type, aggregate_id, revision, action, subject_id, actor {type,id}, reason, diff, after`. `InventoryService(ServiceCore)` in `service.py` (later tasks add mixins to its bases).

- [ ] **Step 1: Write the failing tests** — create `backend/test_inventory_core.py`:

```python
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest backend/test_inventory_core.py -q`
Expected: `ModuleNotFoundError: No module named 'backend.inventory.service'`.

- [ ] **Step 3: Implement**

`backend/inventory/core.py`:

```python
"""Shared plumbing for InventoryService: clock, settings, transactions,
change recording, listeners and the change feed.

Every lifecycle action runs inside `_tx()` and ends with `_record(...)`,
which appends one row to change_log. Listeners (the twin's FleetBridge)
are notified only after the outermost transaction commits, and outside the
store lock — that ordering is what keeps `twin.lock → store.lock` the only
lock order in the system.
"""
from __future__ import annotations

import hashlib
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Callable, Dict, Iterator, List, Mapping, Optional, Sequence

from .documents import diff_documents, iso, utc_now
from .errors import NotFound
from .store import InventoryStore

DEFAULT_SETTINGS: Dict[str, Any] = {
    "CALIBRATION_DUE_SOON_DAYS": 14,
    "CREDENTIAL_EXPIRING_SOON_DAYS": 30,
    "REPORT_STALE_SECONDS": 300,
}

SYSTEM: Dict[str, Any] = {"type": "SYSTEM", "id": None}
ACTOR_TYPES = ("WORKER", "API", "SYSTEM")

FEEDS: Dict[str, Sequence[str]] = {
    "fleet": ("ROBOT_ASSET", "ROBOT_OBSERVATION", "CATALOG"),
    "workforce": ("WORKER",),
}
MAX_FEED_LIMIT = 500


def actor_of(actor: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    if actor is None:
        return {"type": "API", "id": None}
    kind = str(actor.get("type") or "API").upper()
    if kind not in ACTOR_TYPES:
        raise ValueError(f"Unknown actor type {kind!r} (known: {list(ACTOR_TYPES)})")
    return {"type": kind, "id": actor.get("id")}


def sha256_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def change_view(row: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "seq": row["seq"],
        "occurred_at": row["occurred_at"],
        "aggregate_type": row["aggregate_type"],
        "aggregate_id": row["aggregate_id"],
        "revision": row["revision"],
        "action": row["action"],
        "subject_id": row.get("subject_id"),
        "actor": {"type": row["actor_type"], "id": row["actor_id"]},
        "reason": row["reason"],
        "diff": row["diff"],
        "after": row["after"],
    }


class ServiceCore:
    def __init__(
        self,
        store: InventoryStore,
        clock: Optional[Callable[[], datetime]] = None,
        settings: Optional[Callable[[], Mapping[str, Any]]] = None,
    ) -> None:
        self.store = store
        self.custom_clock = clock is not None
        self._clock = clock or utc_now
        self._settings = settings or (lambda: DEFAULT_SETTINGS)
        self._override: Optional[datetime] = None
        self._listeners: List[Callable[[Dict[str, Any]], None]] = []
        self._pending: List[Dict[str, Any]] = []

    # ---- time & settings ---------------------------------------------- #
    def now(self) -> datetime:
        return self._override or self._clock()

    def now_iso(self) -> str:
        return iso(self.now())

    @contextmanager
    def at(self, when: datetime) -> Iterator[None]:
        """Temporarily pretend it is `when` (used to seed backdated history)."""
        previous = self._override
        self._override = when
        try:
            yield
        finally:
            self._override = previous

    def setting(self, key: str) -> Any:
        value = self._settings().get(key)
        return DEFAULT_SETTINGS.get(key) if value is None else value

    # ---- transactions & listeners ------------------------------------- #
    def subscribe(self, listener: Callable[[Dict[str, Any]], None]) -> None:
        self._listeners.append(listener)

    @contextmanager
    def _tx(self) -> Iterator[None]:
        with self.store.lock:
            outermost = self.store.depth == 0
            try:
                with self.store.transaction():
                    yield
            except BaseException:
                if outermost:
                    self._pending.clear()
                raise
            pending: List[Dict[str, Any]] = []
            if outermost:
                pending, self._pending = self._pending, []
        for change in pending:
            for listener in list(self._listeners):
                listener(change)

    def transaction(self):
        """Group several actions into one transaction (listeners fire once it commits)."""
        return self._tx()

    def _record(
        self,
        *,
        aggregate_type: str,
        aggregate_id: str,
        revision: int,
        action: str,
        before: Optional[dict],
        after: Optional[dict],
        actor: Optional[Mapping[str, Any]] = None,
        reason: Optional[str] = None,
        subject_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        who = actor_of(actor)
        entry = {
            "occurred_at": self.now_iso(),
            "aggregate_type": aggregate_type,
            "aggregate_id": aggregate_id,
            "revision": revision,
            "action": action,
            "subject_id": subject_id,
            "actor_type": who["type"],
            "actor_id": who["id"],
            "reason": reason,
            "diff": diff_documents(before, after),
            "after": after,
        }
        seq = self.store.append_change(entry)
        change = change_view({**entry, "seq": seq})
        self._pending.append(change)
        return change

    # ---- small helpers ------------------------------------------------ #
    def _require(self, table: str, key_column: str, key: Any, label: str) -> Dict[str, Any]:
        row = self.store.get(table, key_column, key)
        if row is None:
            raise NotFound(f"{label} '{key}' does not exist")
        return row

    @staticmethod
    def _reason(reason: Any) -> str:
        text = str(reason or "").strip()
        if not text:
            raise ValueError("A reason is required")
        return text

    @staticmethod
    def _worker_actor(worker_id: Optional[str], actor: Optional[Mapping[str, Any]]) -> Optional[Mapping[str, Any]]:
        if actor is not None:
            return actor
        return {"type": "WORKER", "id": worker_id} if worker_id else None

    # ---- change feed & history ---------------------------------------- #
    def changes(self, feed: str, cursor: Optional[str] = None, limit: Any = 100) -> Dict[str, Any]:
        if feed not in FEEDS:
            raise ValueError(f"Unknown feed {feed!r} (known: {sorted(FEEDS)})")
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            raise ValueError("limit must be an integer") from None
        limit = max(1, min(limit, MAX_FEED_LIMIT))
        epoch = self.store.epoch()
        after_seq, resync = 0, False
        if cursor:
            cursor_epoch, separator, seq_text = str(cursor).partition(":")
            if not separator or not seq_text.isdigit():
                raise ValueError(f"Malformed cursor {cursor!r} (expected '<epoch>:<seq>')")
            if cursor_epoch == epoch:
                after_seq = int(seq_text)
            else:
                resync = True
        rows = self.store.changes(FEEDS[feed], after_seq, limit + 1)
        has_more = len(rows) > limit
        items = [change_view(row) for row in rows[:limit]]
        last_seq = items[-1]["seq"] if items else after_seq
        return {
            "epoch": epoch,
            "items": items,
            "next_cursor": f"{epoch}:{last_seq}",
            "has_more": has_more,
            "resync_required": resync,
        }

    def history(self, aggregate_ids: Sequence[str], limit: int = 200) -> List[Dict[str, Any]]:
        return [change_view(row) for row in self.store.history(list(aggregate_ids), int(limit))]
```

`backend/inventory/service.py`:

```python
"""InventoryService — the twin's fleet-manager and workforce source systems.

One object, split by responsibility into mixins (added task by task):
catalog.py (OEM reference data and releases), fleet.py (robot assets and
reported state), servicing.py (work orders, part swaps, calibration),
ota.py (over-the-air updates) and workforce.py (workers, credentials,
training). core.py holds the shared plumbing.
"""
from __future__ import annotations

from .core import ServiceCore


class InventoryService(ServiceCore):
    pass
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest backend/test_inventory_core.py backend/test_inventory_store.py -q`
Expected: `18 passed`.

- [ ] **Step 5: Commit**

```bash
git add backend/inventory/core.py backend/inventory/service.py backend/test_inventory_core.py
git commit -m "feat(inventory): service core with change log, listeners and cursor feed

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 3: Catalog — OEM reference data, SBOMs, releases, bootstrap

**Files:**
- Create: `backend/inventory/catalog_data.py`, `backend/inventory/sbom.py`, `backend/inventory/catalog.py`, `backend/inventory/seed.py`, `backend/inventory/bootstrap.py`
- Modify: `backend/inventory/service.py`, `backend/inventory/__init__.py`
- Test: `backend/test_inventory_catalog.py`

**Interfaces:**
- Consumes: Tasks 1–2.
- Produces: `catalog_data.MANUFACTURERS / PART_MODELS / ROBOT_MODELS / CREDENTIAL_DEFINITIONS / CLASS_DEFAULT_MODELS / OS_VERSIONS` and release tables; `sbom.build_sbom(release, supplier_name) -> dict`, `sbom.serialize_sbom(sbom) -> (text, sha256_hex)`; `catalog.KIND_TAGS`, `catalog.release_id_for(kind, target_code, version)`, `catalog.make_release_row(kind, target_code, version, released_at, status, min_hw_rev, supplier_name) -> row`, `catalog.release_view(row)`; `CatalogMixin` methods `list_manufacturers()`, `list_models()`, `get_model(code)`, `has_model(code)`, `model_class(code)`, `list_parts()`, `get_part(part_number)`, `list_releases(target_code=None, status=None, kind=None)`, `get_release(release_id)`, `get_sbom(release_id)`, `current_release(kind, target_code)`, `release_by_version(kind, target_code, version)`, `best_firmware(part_number, hw_revision)`, `os_version_for(version)`, `publish_release(kind, target_code, version, min_hw_rev=None, actor=None)`, `recall_release(release_id, reason, actor=None)`; `seed.seed_catalog(service)`; `bootstrap.open_inventory(path=":memory:", demo=True, clock=None, settings=None) -> InventoryService`, `bootstrap.reseed(service, demo=True)`. `open_inventory(demo=True)` imports `backend/inventory/demo_seed.py`, which Task 8 creates — until then only `demo=False` is usable.

- [ ] **Step 1: Write the failing tests** — create `backend/test_inventory_catalog.py`:

```python
"""Catalog: seeded OEM reference data, SBOMs, release management, bootstrap."""
from datetime import datetime, timezone

import pytest

from backend.inventory import Conflict, NotFound, open_inventory, reseed
from backend.inventory.catalog_data import CLASS_DEFAULT_MODELS, ROBOT_MODELS
from backend.inventory.sbom import build_sbom, serialize_sbom
from backend.models import APPROVED_FIRMWARE_VERSIONS, ROBOT_CLASS_PRESETS

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def inv():
    return open_inventory(":memory:", demo=False, clock=lambda: NOW)


def test_catalog_is_seeded_and_consistent(inv):
    models = {m["model_code"]: m for m in inv.list_models()}
    assert set(models) == {m["model_code"] for m in ROBOT_MODELS}
    parts = {p["part_number"] for p in inv.list_parts()}
    for model in models.values():
        assert model["manufacturer_name"]
        for slot in model["component_layout"]:
            assert slot["part_number"] in parts
    for robot_class in ROBOT_CLASS_PRESETS:
        assert models[CLASS_DEFAULT_MODELS[robot_class]]["embodiment_class"] == robot_class
    assert inv.store.count("worker") == 0 and inv.store.count("robot_asset") == 0
    assert len(inv.list_manufacturers()) == 7


def test_every_model_has_one_current_software_release_covering_the_approved_versions(inv):
    for model in inv.list_models():
        releases = inv.list_releases(target_code=model["model_code"], kind="ROBOT_SOFTWARE")
        assert [r["status"] for r in releases].count("CURRENT") == 1
        assert set(APPROVED_FIRMWARE_VERSIONS) <= {r["version"] for r in releases}


def test_firmware_capable_parts_have_a_current_release(inv):
    for part in inv.list_parts():
        current = inv.current_release("COMPONENT_FIRMWARE", part["part_number"])
        assert (current is not None) == bool(part["firmware_capable"]), part["part_number"]


def test_release_lookup_helpers(inv):
    assert inv.current_release("ROBOT_SOFTWARE", "AC-TR50")["version"] == "2.2.0"
    assert inv.release_by_version("ROBOT_SOFTWARE", "AC-TR50", "2.2.1")["status"] == "RECALLED"
    assert inv.release_by_version("ROBOT_SOFTWARE", "AC-TR50", "9.9.9") is None
    assert inv.best_firmware("WT-L360", "A")["version"] == "1.9.4"  # 2.0.1 needs rev B
    assert inv.best_firmware("WT-L360", "C")["version"] == "2.0.1"
    assert inv.os_version_for("2.2.0") == "linux-rt 6.6.21"
    assert inv.os_version_for("0.9.0-beta") is None
    assert inv.model_class("NW-PF1200") == "FORKLIFT"
    assert inv.has_model("NW-PF1200") and not inv.has_model("NOPE")
    with pytest.raises(NotFound):
        inv.get_model("NOPE")
    with pytest.raises(NotFound):
        inv.get_release("NOPE")


def test_sboms_are_deterministic_cyclonedx(inv):
    release = inv.get_release("AC-TR50:SW:2.2.0")
    assert "sbom_json" not in release
    sbom = inv.get_sbom("AC-TR50:SW:2.2.0")
    assert sbom["bomFormat"] == "CycloneDX" and sbom["specVersion"] == "1.5"
    assert {"openssl", "linux-rt-kernel", "acme-robotics-nav-stack"} <= {c["name"] for c in sbom["components"]}
    rebuilt = build_sbom(
        {k: release[k] for k in ("release_id", "kind", "target_code", "version", "released_at")},
        "Acme Robotics",
    )
    assert serialize_sbom(rebuilt)[1] == release["sbom_sha256"]
    assert inv.get_sbom("FB-CX10:AI:grasp-3.2.0")["metadata"]["component"]["type"] == "machine-learning-model"
    assert inv.get_sbom("WT-L360:FW:2.0.1")["metadata"]["component"]["type"] == "firmware"


def test_publish_release_supersedes_the_previous_current_and_logs_catalog_changes(inv):
    change = inv.publish_release("ROBOT_SOFTWARE", "AC-TR50", "2.3.0")
    assert change["aggregate_type"] == "CATALOG" and change["action"] == "RELEASE_PUBLISHED"
    assert change["subject_id"] == "AC-TR50:SW:2.3.0"
    assert inv.current_release("ROBOT_SOFTWARE", "AC-TR50")["version"] == "2.3.0"
    assert inv.get_release("AC-TR50:SW:2.2.0")["status"] == "SUPERSEDED"
    assert [c["action"] for c in inv.changes("fleet")["items"]] == ["RELEASE_SUPERSEDED", "RELEASE_PUBLISHED"]
    with pytest.raises(Conflict):
        inv.publish_release("ROBOT_SOFTWARE", "AC-TR50", "2.3.0")
    with pytest.raises(ValueError):
        inv.publish_release("COMPONENT_FIRMWARE", "WT-I9", "1.0.0")  # not firmware-capable
    with pytest.raises(ValueError):
        inv.publish_release("ROBOT_SOFTWARE", "AC-TR50", "2.4.0", min_hw_rev="Z")
    with pytest.raises(ValueError):
        inv.publish_release("FIRMWARE", "AC-TR50", "2.4.0")
    with pytest.raises(NotFound):
        inv.publish_release("ROBOT_SOFTWARE", "NOPE", "1.0.0")


def test_recalling_the_current_release_promotes_the_newest_superseded_one(inv):
    change = inv.recall_release("AC-TR50:SW:2.2.0", "Watchdog reset loop")
    assert change["action"] == "RELEASE_RECALLED" and change["reason"] == "Watchdog reset loop"
    assert inv.current_release("ROBOT_SOFTWARE", "AC-TR50")["version"] == "2.1.1"
    assert [c["action"] for c in inv.changes("fleet")["items"]] == ["RELEASE_RECALLED", "RELEASE_PROMOTED"]
    with pytest.raises(Conflict):
        inv.recall_release("AC-TR50:SW:2.2.0", "again")
    with pytest.raises(ValueError):
        inv.recall_release("AC-TR50:SW:2.1.0", "  ")


def test_open_inventory_reuses_a_seeded_file_and_reseed_rotates_the_epoch(tmp_path):
    path = str(tmp_path / "inv.sqlite3")
    first = open_inventory(path, demo=False)
    first.publish_release("ROBOT_SOFTWARE", "AC-TR50", "2.3.0")
    epoch = first.store.epoch()
    first.store.conn.close()
    again = open_inventory(path, demo=False)
    assert again.store.epoch() == epoch
    assert again.current_release("ROBOT_SOFTWARE", "AC-TR50")["version"] == "2.3.0"
    reseed(again, demo=False)
    assert again.store.epoch() != epoch
    assert again.current_release("ROBOT_SOFTWARE", "AC-TR50")["version"] == "2.2.0"


def test_in_memory_inventories_are_independent():
    a, b = open_inventory(demo=False), open_inventory(demo=False)
    a.publish_release("ROBOT_SOFTWARE", "AC-TR50", "2.3.0")
    assert b.current_release("ROBOT_SOFTWARE", "AC-TR50")["version"] == "2.2.0"
    assert a.store.epoch() != b.store.epoch()
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest backend/test_inventory_catalog.py -q`
Expected: `ImportError: cannot import name 'Conflict' from 'backend.inventory'`.

- [ ] **Step 3: Implement**

`backend/inventory/catalog_data.py`:

```python
"""Reference data for the inventory's seed catalog (spec §6).

Every company here is fictional (Microsoft-style sample names) so nothing
impersonates a real vendor. Specs, sensor suites, safety standards and
software stacks are modelled on real product categories and value ranges.
"""
from __future__ import annotations

from typing import Any, Dict, List, Sequence, Tuple

MANUFACTURERS: List[Dict[str, Any]] = [
    {"manufacturer_id": "ACME", "name": "Acme Robotics", "serial_prefix": "ACM"},
    {"manufacturer_id": "NORTHWIND", "name": "Northwind Lift Systems", "serial_prefix": "NWL"},
    {"manufacturer_id": "CONTOSO", "name": "Contoso Aerial", "serial_prefix": "CTA"},
    {"manufacturer_id": "FABRIKAM", "name": "Fabrikam Automation", "serial_prefix": "FBA"},
    {"manufacturer_id": "TAILSPIN", "name": "Tailspin Humanoids", "serial_prefix": "TSH"},
    {"manufacturer_id": "WINGTIP", "name": "Wingtip Sensors", "serial_prefix": "WTS"},
    {"manufacturer_id": "LITWARE", "name": "Litware Compute", "serial_prefix": "LWC"},
]


def _part(part_number: str, manufacturer_id: str, name: str, component_type: str, hw_revisions: str,
          firmware_capable: bool, calibration_interval_days: Any, **spec: Any) -> Dict[str, Any]:
    return {
        "part_number": part_number, "manufacturer_id": manufacturer_id, "name": name,
        "component_type": component_type, "hw_revisions": list(hw_revisions),
        "firmware_capable": firmware_capable, "calibration_interval_days": calibration_interval_days,
        "spec": spec,
    }


PART_MODELS: List[Dict[str, Any]] = [
    _part("LW-LC8", "LITWARE", "LC-8 Edge Compute Module", "COMPUTE", "AB", True, None, cpu="8-core ARM", ram_gb=16, accelerator_tops=40),
    _part("LW-LC16", "LITWARE", "LC-16 AI Compute Module", "COMPUTE", "A", True, None, cpu="12-core ARM", ram_gb=32, accelerator_tops=275),
    _part("AC-SC2", "ACME", "SC-2 Safety Controller", "SAFETY_CONTROLLER", "ABC", True, None, sil="SIL 2", performance_level="PL d", channels=2),
    _part("NW-SC4", "NORTHWIND", "SC-4 Truck Safety Controller", "SAFETY_CONTROLLER", "AB", True, None, sil="SIL 2", performance_level="PL d"),
    _part("FB-SC6", "FABRIKAM", "SC-6 Cell Safety Controller", "SAFETY_CONTROLLER", "A", True, None, sil="SIL 3", performance_level="PL e"),
    _part("TS-SC1", "TAILSPIN", "SC-1 Humanoid Safety Controller", "SAFETY_CONTROLLER", "A", True, None, sil="SIL 2", performance_level="PL d"),
    _part("WT-L360", "WINGTIP", "WL-360 3D Lidar", "LIDAR", "ABC", True, 365, range_m=40, channels=32, rate_hz=10),
    _part("WT-SS2", "WINGTIP", "WS-2 Safety Laser Scanner", "SAFETY_SCANNER", "AB", True, 180, protective_field_m=5.5, performance_level="PL d"),
    _part("WT-D3", "WINGTIP", "WD-3 RGB-D Camera", "RGBD_CAMERA", "AB", True, 180, resolution="1280x720", depth_range_m=6),
    _part("WT-T1", "WINGTIP", "WT-1 Thermal Camera", "THERMAL_CAMERA", "A", True, 180, resolution="320x240", range_c=[-20, 400]),
    _part("WT-I9", "WINGTIP", "WI-9 9-Axis IMU", "IMU", "A", False, 365, axes=9, rate_hz=400),
    _part("WT-R1", "WINGTIP", "WR-1 RFID/Barcode Scanner", "SCANNER", "AB", True, 365, rfid="UHF", barcode="1D/2D", read_range_m=8),
    _part("AC-DU1", "ACME", "DU-1 Differential Drive Unit", "DRIVE_UNIT", "AB", True, None, motor_w=400),
    _part("NW-DU5", "NORTHWIND", "DU-5 Traction Drive", "DRIVE_UNIT", "A", True, None, motor_kw=3.5),
    _part("AC-B12", "ACME", "B-12 LFP Battery Pack 1.2 kWh", "BATTERY", "AB", True, None, chemistry="LFP", capacity_wh=1200, nominal_v=48),
    _part("NW-B24", "NORTHWIND", "B-24 Lead-Acid Traction Battery", "BATTERY", "A", False, None, chemistry="lead-acid", capacity_wh=14400, nominal_v=24),
    _part("CT-B150", "CONTOSO", "B-150 LiPo Flight Pack", "BATTERY", "AB", True, None, chemistry="LiPo", capacity_wh=150, nominal_v=22.2),
    _part("TS-B20", "TAILSPIN", "B-20 Li-ion Torso Pack 2 kWh", "BATTERY", "A", True, None, chemistry="NMC", capacity_wh=2000, nominal_v=48),
    _part("NW-LC2T", "NORTHWIND", "LC-2T Fork Load Cell", "LOAD_CELL", "AB", True, 180, max_kg=2000, accuracy_pct=0.5),
    _part("NW-ME5", "NORTHWIND", "ME-5 Mast Height Encoder", "MAST_ENCODER", "A", True, 365, resolution_mm=1),
    _part("NW-F1200", "NORTHWIND", "F-1200 Fork Carriage", "FORKS", "A", False, None, fork_length_mm=1150),
    _part("FB-FT6", "FABRIKAM", "FT-6 Force-Torque Sensor", "FORCE_TORQUE", "AB", True, 180, axes=6, max_force_n=500),
    _part("FB-LC4", "FABRIKAM", "LCU-4 Safety Light Curtain", "LIGHT_CURTAIN", "A", True, 180, resolution_mm=14, performance_level="PL e"),
    _part("FB-G2", "FABRIKAM", "G-2 Adaptive Gripper", "GRIPPER", "AB", True, None, stroke_mm=85, max_kg=10),
    _part("AC-G1", "ACME", "G-1 Suction Gripper", "GRIPPER", "A", True, None, max_kg=30),
    _part("FB-J6", "FABRIKAM", "J-6 Joint Actuator Set", "ACTUATOR", "A", True, None, joints=6),
    _part("TS-A28", "TAILSPIN", "A-28 Whole-Body Actuator Set", "ACTUATOR", "AB", True, None, joints=28),
    _part("CT-R4", "CONTOSO", "R-4 Rotor Set", "ROTOR", "A", False, None, rotors=4),
    _part("CT-FC3", "CONTOSO", "FC-3 Flight Controller", "COMPUTE", "AB", True, None, imu="triple-redundant"),
]


def _model(model_code: str, manufacturer_id: str, name: str, embodiment_class: str, hw_revisions: str,
           spec: Dict[str, Any], safety_standards: Sequence[str],
           layout: Sequence[Tuple[str, str]]) -> Dict[str, Any]:
    return {
        "model_code": model_code, "manufacturer_id": manufacturer_id, "name": name,
        "embodiment_class": embodiment_class, "hw_revisions": list(hw_revisions), "spec": spec,
        "safety_standards": list(safety_standards),
        "component_layout": [{"slot": slot, "part_number": part, "required": True} for slot, part in layout],
    }


def _battery(chemistry: str, capacity_wh: int, runtime_h: float, charge_time_h: float) -> Dict[str, Any]:
    return {"chemistry": chemistry, "capacity_wh": capacity_wh, "runtime_h": runtime_h, "charge_time_h": charge_time_h}


ROBOT_MODELS: List[Dict[str, Any]] = [
    _model("AC-TR50", "ACME", "TR-50 Tote Runner", "AMR", "CD", {
        "movement": "GROUND", "clearance": "NARROW", "max_payload_kg": 50, "max_lift_m": 0.6,
        "max_shelf_level": 1, "max_speed_mps": 1.5, "footprint_mm": [700, 500], "mass_kg": 95,
        "ip_rating": "IP54", "battery": _battery("LFP", 1200, 8, 1.5), "box_kinds": ["TOTE", "ITEM"],
        "supervision": None,
    }, ["ANSI/A3 R15.08-1", "ISO 3691-4"], [
        ("compute", "LW-LC8"), ("safety_controller", "AC-SC2"), ("drive_left", "AC-DU1"),
        ("drive_right", "AC-DU1"), ("battery", "AC-B12"), ("lidar", "WT-L360"),
        ("safety_scanner_front", "WT-SS2"), ("safety_scanner_rear", "WT-SS2"),
        ("camera", "WT-D3"), ("imu", "WT-I9"),
    ]),
    _model("AC-SC1", "ACME", "SC-1 Inspection Scout", "SCOUT", "A", {
        "movement": "GROUND", "clearance": "NARROW", "max_payload_kg": 0, "max_lift_m": 0,
        "max_shelf_level": 0, "max_speed_mps": 2.0, "footprint_mm": [600, 450], "mass_kg": 60,
        "ip_rating": "IP54", "battery": _battery("LFP", 1200, 10, 1.5), "box_kinds": [],
        "supervision": None,
    }, ["ANSI/A3 R15.08-1"], [
        ("compute", "LW-LC8"), ("safety_controller", "AC-SC2"), ("drive_left", "AC-DU1"),
        ("drive_right", "AC-DU1"), ("battery", "AC-B12"), ("lidar", "WT-L360"),
        ("camera", "WT-D3"), ("thermal_camera", "WT-T1"), ("imu", "WT-I9"),
    ]),
    _model("AC-PK30", "ACME", "PK-30 Piece Picker", "PICKER", "B", {
        "movement": "GROUND", "clearance": "NARROW", "max_payload_kg": 30, "max_lift_m": 1.2,
        "max_shelf_level": 1, "max_speed_mps": 1.8, "footprint_mm": [800, 600], "mass_kg": 120,
        "ip_rating": "IP54", "battery": _battery("LFP", 1200, 7, 1.5), "box_kinds": ["TOTE", "ITEM"],
        "supervision": None,
    }, ["ANSI/A3 R15.08-1", "ISO 10218-1"], [
        ("compute", "LW-LC8"), ("safety_controller", "AC-SC2"), ("drive_left", "AC-DU1"),
        ("drive_right", "AC-DU1"), ("battery", "AC-B12"), ("lidar", "WT-L360"),
        ("safety_scanner_front", "WT-SS2"), ("camera", "WT-D3"), ("gripper", "AC-G1"), ("imu", "WT-I9"),
    ]),
    _model("NW-PF1200", "NORTHWIND", "PF-1200 Autonomous Pallet Forklift", "FORKLIFT", "AB", {
        "movement": "GROUND", "clearance": "WIDE", "max_payload_kg": 1200, "max_lift_m": 4.5,
        "max_shelf_level": 4, "max_speed_mps": 1.2, "footprint_mm": [2100, 1100], "mass_kg": 1450,
        "ip_rating": "IP54", "battery": _battery("lead-acid", 14400, 7, 8), "box_kinds": ["PALLET"],
        "supervision": None,
    }, ["ISO 3691-4", "ANSI/ITSDF B56.5"], [
        ("compute", "LW-LC8"), ("safety_controller", "NW-SC4"), ("traction_drive", "NW-DU5"),
        ("battery", "NW-B24"), ("lidar", "WT-L360"), ("safety_scanner_front", "WT-SS2"),
        ("safety_scanner_rear", "WT-SS2"), ("camera", "WT-D3"), ("load_cell", "NW-LC2T"),
        ("mast_encoder", "NW-ME5"), ("forks", "NW-F1200"), ("imu", "WT-I9"),
    ]),
    _model("NW-HH300", "NORTHWIND", "HH-300 Heavy Hauler", "HEAVY_HAULER", "A", {
        "movement": "GROUND", "clearance": "WIDE", "max_payload_kg": 300, "max_lift_m": 0.3,
        "max_shelf_level": 0, "max_speed_mps": 1.0, "footprint_mm": [1400, 900], "mass_kg": 380,
        "ip_rating": "IP54", "battery": _battery("lead-acid", 14400, 9, 8), "box_kinds": ["TOTE", "PALLET"],
        "supervision": None,
    }, ["ISO 3691-4"], [
        ("compute", "LW-LC8"), ("safety_controller", "NW-SC4"), ("traction_drive", "NW-DU5"),
        ("battery", "NW-B24"), ("lidar", "WT-L360"), ("safety_scanner_front", "WT-SS2"),
        ("safety_scanner_rear", "WT-SS2"), ("imu", "WT-I9"),
    ]),
    _model("CT-IX2", "CONTOSO", "IX-2 Indoor Inventory Drone", "DRONE", "AB", {
        "movement": "AIR", "clearance": None, "max_payload_kg": 0, "max_lift_m": 12,
        "max_shelf_level": 4, "max_speed_mps": 3.0, "footprint_mm": [450, 450], "mass_kg": 2.1,
        "ip_rating": "IP43", "battery": _battery("LiPo", 150, 0.37, 1.0), "box_kinds": [],
        "flight_time_min": 22, "supervision": None,
    }, ["IEC 62133-2"], [
        ("flight_controller", "CT-FC3"), ("compute", "LW-LC8"), ("battery", "CT-B150"),
        ("rotors", "CT-R4"), ("scanner", "WT-R1"), ("camera", "WT-D3"), ("imu", "WT-I9"),
    ]),
    _model("FB-CX10", "FABRIKAM", "CX-10 Collaborative Arm", "ARM", "AB", {
        "movement": "FIXED", "clearance": None, "max_payload_kg": 10, "reach_mm": 1300,
        "max_speed_mps": 1.0, "repeatability_mm": 0.05, "mass_kg": 33, "ip_rating": "IP54",
        "battery": None, "power": "mains 230 V", "box_kinds": ["ITEM"], "supervision": None,
    }, ["ISO 10218-1", "ISO/TS 15066"], [
        ("compute", "LW-LC16"), ("safety_controller", "FB-SC6"), ("joints", "FB-J6"),
        ("force_torque", "FB-FT6"), ("wrist_camera", "WT-D3"), ("gripper", "FB-G2"),
        ("light_curtain", "FB-LC4"),
    ]),
    _model("TS-H1", "TAILSPIN", "H-1 General-Purpose Humanoid", "HUMANOID", "A", {
        "movement": "GROUND", "clearance": "NARROW", "max_payload_kg": 25, "max_lift_m": 1.8,
        "max_shelf_level": 2, "max_speed_mps": 1.2, "height_mm": 1750, "mass_kg": 70,
        "ip_rating": "IP44", "battery": _battery("NMC", 2000, 4, 2), "box_kinds": ["TOTE", "ITEM"],
        "supervision": "humanoid_supervision",
    }, ["ISO 12100", "ISO 13849-1"], [
        ("compute", "LW-LC16"), ("safety_controller", "TS-SC1"), ("actuators", "TS-A28"),
        ("battery", "TS-B20"), ("stereo_camera_left", "WT-D3"), ("stereo_camera_right", "WT-D3"),
        ("lidar", "WT-L360"), ("imu", "WT-I9"),
    ]),
]

#: The catalog model a twin robot class is commissioned as when no model is given.
CLASS_DEFAULT_MODELS: Dict[str, str] = {
    "AMR": "AC-TR50", "SCOUT": "AC-SC1", "PICKER": "AC-PK30", "FORKLIFT": "NW-PF1200",
    "HEAVY_HAULER": "NW-HH300", "DRONE": "CT-IX2", "ARM": "FB-CX10", "HUMANOID": "TS-H1",
}

#: Robot-software releases every model ships: (version, days before seeding, status).
#: One version line across vendors keeps the twin's single
#: APPROVED_FIRMWARE_VERSIONS list meaningful (per-model baselines are PWA's job).
ROBOT_SOFTWARE_RELEASES: List[Tuple[str, int, str]] = [
    ("2.0.4", 420, "SUPERSEDED"), ("2.1.0", 300, "SUPERSEDED"),
    ("2.1.1", 200, "SUPERSEDED"), ("2.2.0", 90, "CURRENT"),
]
EXTRA_ROBOT_SOFTWARE_RELEASES: Dict[str, List[Tuple[str, int, str]]] = {
    "AC-TR50": [("2.2.1", 40, "RECALLED")],
}
ROBOT_SOFTWARE_MIN_HW: Dict[Tuple[str, str], str] = {("NW-PF1200", "2.2.0"): "B"}

#: The OS each robot-software version ships with (reported by robots as os_version).
OS_VERSIONS: Dict[str, str] = {
    "2.0.4": "linux-rt 5.15.148", "2.1.0": "linux-rt 6.1.90", "2.1.1": "linux-rt 6.1.90",
    "2.2.0": "linux-rt 6.6.21", "2.2.1": "linux-rt 6.6.21",
}

#: Component firmware per firmware-capable part: (previous, current).
COMPONENT_FIRMWARE_VERSIONS: Dict[str, Tuple[str, str]] = {
    "LW-LC8": ("5.10.2", "5.12.0"), "LW-LC16": ("6.1.0", "6.2.1"), "AC-SC2": ("3.0.8", "3.1.0"),
    "NW-SC4": ("4.2.0", "4.2.3"), "FB-SC6": ("2.7.1", "2.8.0"), "TS-SC1": ("1.2.0", "1.3.0"),
    "WT-L360": ("1.9.4", "2.0.1"), "WT-SS2": ("3.3.0", "3.4.0"), "WT-D3": ("0.18.2", "0.19.0"),
    "WT-T1": ("1.1.0", "1.2.0"), "WT-R1": ("2.2.0", "2.3.5"), "AC-DU1": ("1.0.6", "1.1.0"),
    "NW-DU5": ("7.0.1", "7.1.0"), "AC-B12": ("1.2.0", "1.3.1"), "CT-B150": ("0.9.0", "1.0.0"),
    "TS-B20": ("2.0.0", "2.1.0"), "NW-LC2T": ("1.4.0", "1.5.0"), "NW-ME5": ("1.0.0", "1.0.3"),
    "FB-FT6": ("3.2.0", "3.3.1"), "FB-LC4": ("1.6.0", "1.6.2"), "FB-G2": ("4.0.0", "4.1.0"),
    "AC-G1": ("1.0.0", "1.1.0"), "FB-J6": ("5.4.0", "5.5.0"), "TS-A28": ("0.9.3", "1.0.0"),
    "CT-FC3": ("4.4.0", "4.5.2"),
}
COMPONENT_FIRMWARE_MIN_HW: Dict[Tuple[str, str], str] = {("WT-L360", "2.0.1"): "B"}

AI_POLICY_RELEASES: Dict[str, List[Tuple[str, int, str]]] = {
    "FB-CX10": [("grasp-3.1.0", 200, "SUPERSEDED"), ("grasp-3.2.0", 45, "CURRENT")],
    "TS-H1": [("loco-manip-1.3.0", 150, "SUPERSEDED"), ("loco-manip-1.4.0", 30, "CURRENT")],
}


def _credential(code: str, name: str, renewal_period_days: int, scope_dimensions: Sequence[str]) -> Dict[str, Any]:
    return {
        "code": code, "name": name, "issuer_types": ["INTERNAL", "THIRD_PARTY"],
        "renewal_period_days": renewal_period_days, "scope_dimensions": list(scope_dimensions),
    }


#: Codes match the twin's existing lowercase certification strings.
CREDENTIAL_DEFINITIONS: List[Dict[str, Any]] = [
    _credential("safety_inspection", "Safety Inspection", 365, ["site"]),
    _credential("electrical_safety", "Electrical Safety", 365, ["site"]),
    _credential("equipment_maintenance", "Equipment Maintenance", 730, ["equipment"]),
    _credential("heavy_equipment", "Heavy Equipment Operation", 1095, ["equipment", "site"]),
    _credential("hazmat_handling", "Hazardous Materials Handling", 365, ["site"]),
    _credential("quality_control", "Quality Control", 730, ["task"]),
    _credential("forklift_operator", "Forklift Operator", 1095, ["equipment", "site"]),
    _credential("humanoid_supervision", "Humanoid Robot Supervision", 365, ["equipment", "site", "supervision"]),
    _credential("robot_cell_access", "Robot Cell Access", 365, ["equipment", "site"]),
    _credential("drone_operations", "Indoor Drone Operations", 730, ["equipment", "site"]),
    _credential("robot_maintenance", "Robot Maintenance Technician", 730, ["equipment"]),
]
```

`backend/inventory/sbom.py`:

```python
"""Deterministic synthetic CycloneDX 1.5 SBOMs for catalog releases.

The same release always produces a byte-identical SBOM, so its SHA-256 is a
stable identity a firmware baseline can pin to. Components are realistic
open-source packages plus the (fictional) vendor's own packages.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from typing import Any, Dict, List, Optional, Tuple

_NAMESPACE = uuid.UUID("6f1c9a52-7c1e-4d0b-9a57-2f4b1f0e3c11")

#: Open-source stacks by robot-software release line (major.minor).
_ROBOT_STACKS: Dict[str, List[Tuple[str, str]]] = {
    "2.0": [("linux-rt-kernel", "5.15.148"), ("glibc", "2.35"), ("openssl", "3.0.2"),
            ("rclcpp", "16.0.5"), ("cyclonedds", "0.9.1"), ("protobuf", "3.12.4")],
    "2.1": [("linux-rt-kernel", "6.1.90"), ("glibc", "2.35"), ("openssl", "3.0.13"),
            ("rclcpp", "16.0.8"), ("cyclonedds", "0.10.3"), ("protobuf", "3.21.12")],
    "2.2": [("linux-rt-kernel", "6.6.21"), ("glibc", "2.39"), ("openssl", "3.0.14"),
            ("rclcpp", "28.1.3"), ("cyclonedds", "0.10.4"), ("protobuf", "3.21.12")],
}
_FIRMWARE_STACK: List[Tuple[str, str]] = [("rtos-kernel", "3.5.0"), ("mbedtls", "3.5.2"), ("lwip", "2.2.0")]
_AI_STACK: List[Tuple[str, str]] = [("onnxruntime", "1.17.3"), ("numpy", "1.26.4")]


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _component(release_id: str, name: str, version: str, ctype: str = "library",
               supplier: Optional[str] = None) -> Dict[str, Any]:
    ref = f"{release_id}/{name}@{version}"
    component: Dict[str, Any] = {
        "type": ctype, "bom-ref": ref, "name": name, "version": version,
        "purl": f"pkg:generic/{name}@{version}",
        "hashes": [{"alg": "SHA-256", "content": hashlib.sha256(ref.encode("utf-8")).hexdigest()}],
    }
    if supplier:
        component["supplier"] = {"name": supplier}
    return component


def build_sbom(release: Dict[str, Any], supplier_name: str) -> Dict[str, Any]:
    kind, version, release_id = release["kind"], release["version"], release["release_id"]
    vendor = _slug(supplier_name)
    if kind == "ROBOT_SOFTWARE":
        line = ".".join(version.split(".")[:2])
        components = [_component(release_id, n, v) for n, v in _ROBOT_STACKS.get(line, _ROBOT_STACKS["2.2"])]
        components.append(_component(release_id, f"{vendor}-nav-stack", version, supplier=supplier_name))
        components.append(_component(release_id, f"{vendor}-fleet-agent", version, supplier=supplier_name))
        root_type = "firmware"
    elif kind == "COMPONENT_FIRMWARE":
        components = [_component(release_id, n, v) for n, v in _FIRMWARE_STACK]
        components.append(_component(
            release_id, f"{vendor}-{_slug(release['target_code'])}-fw", version,
            ctype="firmware", supplier=supplier_name,
        ))
        root_type = "firmware"
    else:  # AI_POLICY_MODEL
        components = [_component(release_id, n, v) for n, v in _AI_STACK]
        components.append(_component(
            release_id, f"{_slug(release['target_code'])}-policy-weights", version,
            ctype="data", supplier=supplier_name,
        ))
        root_type = "machine-learning-model"
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "serialNumber": f"urn:uuid:{uuid.uuid5(_NAMESPACE, release_id)}",
        "version": 1,
        "metadata": {
            "timestamp": release["released_at"],
            "supplier": {"name": supplier_name},
            "component": {"type": root_type, "bom-ref": release_id,
                          "name": release["target_code"], "version": version},
        },
        "components": components,
        "dependencies": [{"ref": release_id, "dependsOn": [c["bom-ref"] for c in components]}],
    }


def serialize_sbom(sbom: Dict[str, Any]) -> Tuple[str, str]:
    text = json.dumps(sbom, sort_keys=True, separators=(",", ":"))
    return text, hashlib.sha256(text.encode("utf-8")).hexdigest()
```

`backend/inventory/catalog.py`:

```python
"""Catalog reads and release management — the OEM reference data every
robot asset points at."""
from __future__ import annotations

import json
from typing import Any, Dict, List, Mapping, Optional

from .catalog_data import OS_VERSIONS
from .errors import Conflict
from .sbom import build_sbom, serialize_sbom

KIND_TAGS = {"ROBOT_SOFTWARE": "SW", "COMPONENT_FIRMWARE": "FW", "AI_POLICY_MODEL": "AI"}


def release_id_for(kind: str, target_code: str, version: str) -> str:
    return f"{target_code}:{KIND_TAGS[kind]}:{version}"


def make_release_row(kind: str, target_code: str, version: str, released_at: str, status: str,
                     min_hw_rev: Optional[str], supplier_name: str) -> Dict[str, Any]:
    row = {
        "release_id": release_id_for(kind, target_code, version),
        "kind": kind,
        "target_type": "PART" if kind == "COMPONENT_FIRMWARE" else "MODEL",
        "target_code": target_code,
        "version": version,
        "released_at": released_at,
        "min_hw_rev": min_hw_rev,
        "status": status,
    }
    row["sbom_json"], row["sbom_sha256"] = serialize_sbom(build_sbom(row, supplier_name))
    return row


def release_view(row: Mapping[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in row.items() if k != "sbom_json"}


class CatalogMixin:
    # ---- reads ---------------------------------------------------------- #
    def list_manufacturers(self) -> List[Dict[str, Any]]:
        return self.store.select("manufacturer", order="manufacturer_id")

    def _manufacturer_names(self) -> Dict[str, str]:
        return {m["manufacturer_id"]: m["name"] for m in self.list_manufacturers()}

    def list_models(self) -> List[Dict[str, Any]]:
        names = self._manufacturer_names()
        return [{**m, "manufacturer_name": names[m["manufacturer_id"]]}
                for m in self.store.select("robot_model", order="model_code")]

    def get_model(self, model_code: str) -> Dict[str, Any]:
        model = self._require("robot_model", "model_code", model_code, "Robot model")
        model["manufacturer_name"] = self._manufacturer_names()[model["manufacturer_id"]]
        return model

    def has_model(self, model_code: str) -> bool:
        return bool(model_code) and self.store.exists("robot_model", "model_code", model_code)

    def model_class(self, model_code: str) -> str:
        return self.get_model(model_code)["embodiment_class"]

    def list_parts(self) -> List[Dict[str, Any]]:
        names = self._manufacturer_names()
        return [{**p, "manufacturer_name": names[p["manufacturer_id"]]}
                for p in self.store.select("part_model", order="part_number")]

    def get_part(self, part_number: str) -> Dict[str, Any]:
        return self._require("part_model", "part_number", part_number, "Part")

    def list_releases(self, target_code: Optional[str] = None, status: Optional[str] = None,
                      kind: Optional[str] = None) -> List[Dict[str, Any]]:
        where, params = [], []
        for column, value in (("target_code", target_code), ("status", status), ("kind", kind)):
            if value:
                where.append(f"{column} = ?")
                params.append(str(value).upper() if column != "target_code" else value)
        rows = self.store.select("software_release", " AND ".join(where), params,
                                 order="kind, target_code, released_at")
        return [release_view(row) for row in rows]

    def get_release(self, release_id: str) -> Dict[str, Any]:
        return release_view(self._require("software_release", "release_id", release_id, "Release"))

    def get_sbom(self, release_id: str) -> Dict[str, Any]:
        row = self._require("software_release", "release_id", release_id, "Release")
        return json.loads(row["sbom_json"])

    def current_release(self, kind: str, target_code: str) -> Optional[Dict[str, Any]]:
        rows = self.store.select("software_release", "kind = ? AND target_code = ? AND status = 'CURRENT'",
                                 (kind, target_code), order="released_at DESC", limit=1)
        return release_view(rows[0]) if rows else None

    def release_by_version(self, kind: str, target_code: str, version: Optional[str]) -> Optional[Dict[str, Any]]:
        if not version:
            return None
        rows = self.store.select("software_release", "kind = ? AND target_code = ? AND version = ?",
                                 (kind, target_code, version), limit=1)
        return release_view(rows[0]) if rows else None

    def best_firmware(self, part_number: str, hw_revision: str) -> Optional[Dict[str, Any]]:
        """Newest non-recalled firmware this part revision can run."""
        rows = self.store.select("software_release",
                                 "kind = 'COMPONENT_FIRMWARE' AND target_code = ? AND status != 'RECALLED'",
                                 (part_number,), order="released_at DESC")
        for row in rows:
            if not row["min_hw_rev"] or row["min_hw_rev"] <= hw_revision:
                return release_view(row)
        return None

    @staticmethod
    def os_version_for(software_version: Optional[str]) -> Optional[str]:
        return OS_VERSIONS.get(software_version or "")

    # ---- release management ------------------------------------------- #
    def _catalog_revision(self, release_id: str) -> int:
        return self.store.count("change_log", "aggregate_id = ?", (release_id,)) + 1

    def publish_release(self, kind: str, target_code: str, version: str,
                        min_hw_rev: Optional[str] = None, actor: Any = None) -> Dict[str, Any]:
        kind = str(kind or "").upper()
        if kind not in KIND_TAGS:
            raise ValueError(f"Unknown release kind {kind!r} (known: {sorted(KIND_TAGS)})")
        version = str(version or "").strip()
        if not version:
            raise ValueError("version is required")
        with self._tx():
            if kind == "COMPONENT_FIRMWARE":
                target = self.get_part(target_code)
                if not target["firmware_capable"]:
                    raise ValueError(f"{target_code} does not take firmware updates")
            else:
                target = self.get_model(target_code)
            if min_hw_rev and min_hw_rev not in target["hw_revisions"]:
                raise ValueError(f"{target_code} has no hardware revision {min_hw_rev!r} (known: {target['hw_revisions']})")
            release_id = release_id_for(kind, target_code, version)
            if self.store.exists("software_release", "release_id", release_id):
                raise Conflict(f"Release {release_id} already exists")
            for prior in self.store.select("software_release",
                                           "kind = ? AND target_code = ? AND status = 'CURRENT'",
                                           (kind, target_code)):
                before = release_view(prior)
                self.store.update("software_release", "release_id", prior["release_id"], {"status": "SUPERSEDED"})
                self._record(aggregate_type="CATALOG", aggregate_id=prior["release_id"],
                             revision=self._catalog_revision(prior["release_id"]), action="RELEASE_SUPERSEDED",
                             before=before, after={**before, "status": "SUPERSEDED"}, actor=actor,
                             subject_id=prior["release_id"])
            supplier = self._manufacturer_names()[target["manufacturer_id"]]
            row = make_release_row(kind, target_code, version, self.now_iso(), "CURRENT", min_hw_rev, supplier)
            self.store.insert("software_release", row)
            return self._record(aggregate_type="CATALOG", aggregate_id=release_id,
                                revision=self._catalog_revision(release_id), action="RELEASE_PUBLISHED",
                                before=None, after=release_view(row), actor=actor, subject_id=release_id)

    def recall_release(self, release_id: str, reason: str, actor: Any = None) -> Dict[str, Any]:
        reason = self._reason(reason)
        with self._tx():
            row = self._require("software_release", "release_id", release_id, "Release")
            if row["status"] == "RECALLED":
                raise Conflict(f"Release {release_id} is already recalled")
            before = release_view(row)
            self.store.update("software_release", "release_id", release_id, {"status": "RECALLED"})
            change = self._record(aggregate_type="CATALOG", aggregate_id=release_id,
                                  revision=self._catalog_revision(release_id), action="RELEASE_RECALLED",
                                  before=before, after={**before, "status": "RECALLED"}, actor=actor,
                                  reason=reason, subject_id=release_id)
            if row["status"] == "CURRENT":
                candidates = self.store.select("software_release",
                                               "kind = ? AND target_code = ? AND status = 'SUPERSEDED'",
                                               (row["kind"], row["target_code"]), order="released_at DESC", limit=1)
                if candidates:
                    promoted = release_view(candidates[0])
                    self.store.update("software_release", "release_id", promoted["release_id"], {"status": "CURRENT"})
                    self._record(aggregate_type="CATALOG", aggregate_id=promoted["release_id"],
                                 revision=self._catalog_revision(promoted["release_id"]), action="RELEASE_PROMOTED",
                                 before=promoted, after={**promoted, "status": "CURRENT"}, actor=actor,
                                 reason=f"{release_id} recalled", subject_id=promoted["release_id"])
            return change
```

`backend/inventory/seed.py`:

```python
"""Seed the inventory catalog: manufacturers, parts, robot models,
credential definitions and every software release with its SBOM."""
from __future__ import annotations

from datetime import timedelta

from .catalog import make_release_row
from .catalog_data import (
    AI_POLICY_RELEASES, COMPONENT_FIRMWARE_MIN_HW, COMPONENT_FIRMWARE_VERSIONS,
    CREDENTIAL_DEFINITIONS, EXTRA_ROBOT_SOFTWARE_RELEASES, MANUFACTURERS, PART_MODELS,
    ROBOT_MODELS, ROBOT_SOFTWARE_MIN_HW, ROBOT_SOFTWARE_RELEASES,
)
from .documents import iso


def seed_catalog(service) -> None:
    store = service.store
    now = service.now()
    suppliers = {m["manufacturer_id"]: m["name"] for m in MANUFACTURERS}
    part_supplier = {p["part_number"]: suppliers[p["manufacturer_id"]] for p in PART_MODELS}
    model_supplier = {m["model_code"]: suppliers[m["manufacturer_id"]] for m in ROBOT_MODELS}

    def released(days: int) -> str:
        return iso(now - timedelta(days=days))

    with service.transaction():
        for row in MANUFACTURERS:
            store.insert("manufacturer", row)
        for row in PART_MODELS:
            store.insert("part_model", row)
        for row in ROBOT_MODELS:
            store.insert("robot_model", row)
        for row in CREDENTIAL_DEFINITIONS:
            store.insert("credential_definition", row)
        for model in ROBOT_MODELS:
            code = model["model_code"]
            for version, days, status in ROBOT_SOFTWARE_RELEASES + EXTRA_ROBOT_SOFTWARE_RELEASES.get(code, []):
                store.insert("software_release", make_release_row(
                    "ROBOT_SOFTWARE", code, version, released(days), status,
                    ROBOT_SOFTWARE_MIN_HW.get((code, version)), model_supplier[code]))
            for version, days, status in AI_POLICY_RELEASES.get(code, []):
                store.insert("software_release", make_release_row(
                    "AI_POLICY_MODEL", code, version, released(days), status, None, model_supplier[code]))
        for part_number, (previous, current) in COMPONENT_FIRMWARE_VERSIONS.items():
            for version, days, status in ((previous, 300, "SUPERSEDED"), (current, 60, "CURRENT")):
                store.insert("software_release", make_release_row(
                    "COMPONENT_FIRMWARE", part_number, version, released(days), status,
                    COMPONENT_FIRMWARE_MIN_HW.get((part_number, version)), part_supplier[part_number]))
```

`backend/inventory/bootstrap.py`:

```python
"""Open, seed and re-seed inventory databases.

Seeding a demo inventory runs a few hundred service actions, so the seeded
database is built once per process into an in-memory *template* and copied
into each new database with sqlite3's backup API (the test suite builds
hundreds of twins). A template older than TEMPLATE_MAX_AGE_SECONDS is
rebuilt so seeded dates ("calibration expires in 10 days") stay relative
to now. Passing a `clock` skips the template and seeds directly against
that clock, which keeps unit tests deterministic.
"""
from __future__ import annotations

import sqlite3
import threading
import time
from datetime import datetime
from typing import Any, Callable, Dict, Mapping, Optional, Tuple

from .schema import connect, create_schema, is_current, new_epoch
from .seed import seed_catalog
from .service import InventoryService
from .store import InventoryStore

TEMPLATE_MAX_AGE_SECONDS = 60
_templates: Dict[bool, Tuple[sqlite3.Connection, float]] = {}
_template_lock = threading.Lock()


def _seed_into(conn: sqlite3.Connection, demo: bool, clock: Optional[Callable[[], datetime]] = None) -> None:
    create_schema(conn)
    service = InventoryService(InventoryStore(conn), clock=clock)
    seed_catalog(service)
    if demo:
        from .demo_seed import seed_demo  # deferred: created in a later task

        seed_demo(service)
    service.store.set_meta("seeded", "1")


def _wipe(conn: sqlite3.Connection) -> None:
    names = [row["name"] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")]
    conn.execute("PRAGMA foreign_keys = OFF")
    for name in names:
        conn.execute(f'DROP TABLE IF EXISTS "{name}"')
    conn.execute("PRAGMA foreign_keys = ON")


def _template(demo: bool) -> sqlite3.Connection:
    """Call with _template_lock held."""
    cached = _templates.get(demo)
    if cached is not None and time.monotonic() - cached[1] < TEMPLATE_MAX_AGE_SECONDS:
        return cached[0]
    conn = connect(":memory:")
    _seed_into(conn, demo)
    _templates[demo] = (conn, time.monotonic())
    return conn


def _restore(conn: sqlite3.Connection, demo: bool) -> None:
    with _template_lock:
        _template(demo).backup(conn)
    conn.execute("UPDATE meta SET value = ? WHERE key = 'epoch'", (new_epoch(),))


def open_inventory(
    path: str = ":memory:",
    demo: bool = True,
    clock: Optional[Callable[[], datetime]] = None,
    settings: Optional[Callable[[], Mapping[str, Any]]] = None,
) -> InventoryService:
    """Open (creating and seeding if needed) the inventory at `path`."""
    conn = connect(path)
    if path == ":memory:" or not is_current(conn):
        if clock is not None:
            _wipe(conn)
            _seed_into(conn, demo, clock)
        else:
            _restore(conn, demo)
    return InventoryService(InventoryStore(conn), clock=clock, settings=settings)


def reseed(service: InventoryService, demo: bool = True) -> None:
    """Replace everything in `service`'s database with a fresh seed and a new epoch."""
    store = service.store
    with store.lock:
        if store.depth:
            raise RuntimeError("Cannot reseed the inventory inside a transaction")
        if service.custom_clock:
            _wipe(store.conn)
            _seed_into(store.conn, demo, service._clock)
        else:
            _restore(store.conn, demo)
```

Modify `backend/inventory/service.py` — replace the import and class line:

```python
from .catalog import CatalogMixin
from .core import ServiceCore


class InventoryService(CatalogMixin, ServiceCore):
    pass
```

Modify `backend/inventory/__init__.py` — append after the docstring:

```python
from .bootstrap import open_inventory, reseed
from .errors import Conflict, NotFound
from .service import InventoryService

__all__ = ["Conflict", "InventoryService", "NotFound", "open_inventory", "reseed"]
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest backend/test_inventory_catalog.py backend/test_inventory_core.py backend/test_inventory_store.py -q`
Expected: `27 passed`.

- [ ] **Step 5: Commit**

```bash
git add backend/inventory backend/test_inventory_catalog.py
git commit -m "feat(inventory): fictional OEM catalog with SBOM-backed releases and bootstrap

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 4: Robot assets — commission, lifecycle, admin edit, reported state, read models

**Files:**
- Create: `backend/inventory/fleet.py`
- Modify: `backend/inventory/service.py`
- Test: `backend/test_inventory_fleet.py`

**Interfaces:**
- Consumes: Tasks 1–3 (`get_model`, `get_part`, `get_release`, `current_release`, `release_by_version`, `best_firmware`, `os_version_for`, `_tx`, `_record`, `_require`, `_reason`).
- Produces: constants `LIFECYCLE_STATUSES`, `LIFECYCLE_TRANSITIONS`, `ADMIN_EDITABLE_FIELDS`, `SIGNIFICANT_REPORT_FIELDS`, `REFRESH_ONLY_REPORT_FIELDS`, `HEALTH_STATES`, `CONNECTIVITY_STATES`, `CALIBRATION_RANK`, `OTA_ACTIVE_STATES = ("STAGED","DOWNLOADING","INSTALLING","REPORTED")`. `FleetMixin` methods: `has_asset(id)`, `commission_robot(model_code, site_code, home_zone=None, *, asset_id=None, serial_number=None, asset_tag=None, hw_revision=None, software_release_id=None, fleet_id=None, lifecycle_status="IN_SERVICE", actor=None) -> change`, `set_lifecycle_status(asset_id, status, reason, actor=None) -> change`, `decommission(asset_id, reason, actor=None) -> change`, `admin_edit(asset_id, fields, reason, actor=None) -> change`, `report_state(asset_id, reported, actor=None) -> change|None`, `touch_remote_reports(exclude=()) -> int`, `get_robot(asset_id) -> record`, `list_robots(site=None, status=None) -> [summary]`, `robot_history(asset_id, limit=200)`. Internal helpers later tasks use: `_asset(id)`, `_asset_document(id)`, `_commit_asset(asset_id, action, before, actor=None, reason=None, subject_id=None)`, `_open_work_orders(id)`, `_active_ota_rows(id)`, `_install_component(asset_id, slot, part_number, hw_revision) -> row+{"firmware_version"}`, `_insert_calibration(component_id, part, result, performed_by, method, certificate_ref) -> calibration_id`, `_new_serial(manufacturer_row, code)`, `_ota_view(job_row)`.
- `get_robot` record keys: every `robot_asset` column plus `manufacturer_name`, `model` (full model row), `declared_software_version`, `declared_software_status`, `declared_ai_policy_version`, `reported` (the `reported_state` row), `components` (each: component row + `part_name`, `component_type`, `declared_firmware_version`, `reported_firmware_version`, `calibration_interval_days`, `calibration_status`, `calibrations` [last 5]), `work_orders` (all, newest first), `ota_jobs` (last 20, newest first, `_ota_view` shape), `flags` = `{software_mismatch, ai_policy_mismatch, component_firmware_mismatches:[{slot,declared,reported}], calibration_worst, calibration_issues:[{slot,status}], report_stale, running_recalled_release, running_unknown_software, active_ota_job}`.

- [ ] **Step 1: Write the failing tests** — create `backend/test_inventory_fleet.py`:

```python
"""Robot assets: commissioning, lifecycle, admin edits, reported state, flags."""
from datetime import datetime, timedelta, timezone

import pytest

from backend.inventory import Conflict, NotFound, open_inventory

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, **kwargs):
        self.now += timedelta(**kwargs)


@pytest.fixture
def clock():
    return Clock(NOW)


@pytest.fixture
def inv(clock):
    return open_inventory(":memory:", demo=False, clock=clock)


def commission(inv, model="AC-TR50", site="WH-01", zone="parking_area", **kwargs):
    return inv.commission_robot(model, site, zone, **kwargs)["aggregate_id"]


def test_commission_creates_asset_components_calibrations_and_first_report(inv):
    change = inv.commission_robot("AC-TR50", "WH-01", "parking_area")
    assert change["action"] == "COMMISSIONED" and change["revision"] == 1
    asset_id = change["aggregate_id"]
    assert asset_id == "AST-000001"
    assert change["diff"]["lifecycle_status"] == {"from": None, "to": "IN_SERVICE"}
    robot = inv.get_robot(asset_id)
    assert robot["lifecycle_status"] == "IN_SERVICE"
    assert robot["serial_number"] == "ACM-TR50-2026-01001"
    assert robot["manufacturer_name"] == "Acme Robotics"
    assert robot["declared_software_version"] == "2.2.0"
    layout = [s["slot"] for s in inv.get_model("AC-TR50")["component_layout"]]
    assert [c["slot"] for c in robot["components"]] == layout
    lidar = next(c for c in robot["components"] if c["slot"] == "lidar")
    assert lidar["calibration_status"] == "VALID" and lidar["declared_firmware_version"] == "2.0.1"
    assert lidar["calibrations"][0]["method"] == "FACTORY"
    assert robot["reported"]["software_version"] == "2.2.0"
    assert robot["reported"]["os_version"] == "linux-rt 6.6.21"
    assert robot["reported"]["component_firmware"]["lidar"] == "2.0.1"
    assert robot["reported"]["battery"] == {"soc_pct": 100.0, "soh_pct": 100.0, "cycle_count": 0}
    assert robot["flags"]["software_mismatch"] is False
    assert robot["flags"]["calibration_worst"] == "VALID"
    assert [c["action"] for c in inv.changes("fleet")["items"]] == ["COMMISSIONED", "STATE_REPORTED"]


def test_commission_validation(inv):
    with pytest.raises(NotFound):
        inv.commission_robot("NOPE", "WH-01")
    with pytest.raises(ValueError):
        inv.commission_robot("AC-TR50", "WH-01", hw_revision="Z")
    with pytest.raises(ValueError):
        inv.commission_robot("AC-TR50", "")
    with pytest.raises(ValueError):
        inv.commission_robot("AC-TR50", "WH-01", software_release_id="NW-PF1200:SW:2.2.0")
    with pytest.raises(Conflict):
        inv.commission_robot("AC-TR50", "WH-01", software_release_id="AC-TR50:SW:2.2.1")  # recalled
    with pytest.raises(Conflict):
        inv.commission_robot("NW-PF1200", "WH-02", hw_revision="A", software_release_id="NW-PF1200:SW:2.2.0")
    inv.commission_robot("AC-TR50", "WH-01", asset_id="AST-000101", serial_number="S-1")
    with pytest.raises(Conflict):
        inv.commission_robot("AC-TR50", "WH-01", asset_id="AST-000101")
    with pytest.raises(Conflict):
        inv.commission_robot("AC-TR50", "WH-01", serial_number="S-1")
    assert inv.store.count("robot_asset") == 1  # failed commissions left nothing behind


def test_an_older_release_can_be_commissioned_and_models_without_battery_report_none(inv):
    asset = commission(inv, software_release_id="AC-TR50:SW:2.1.0")
    assert inv.get_robot(asset)["reported"]["software_version"] == "2.1.0"
    arm = inv.get_robot(commission(inv, model="FB-CX10", zone="pack_cell_1"))
    assert arm["reported"]["battery"] is None
    assert arm["declared_ai_policy_version"] == "grasp-3.2.0"
    assert arm["reported"]["ai_policy_version"] == "grasp-3.2.0"


def test_lifecycle_transitions(inv):
    asset = commission(inv)
    change = inv.set_lifecycle_status(asset, "OUT_OF_SERVICE", "Awaiting dock repair")
    assert change["diff"]["lifecycle_status"] == {"from": "IN_SERVICE", "to": "OUT_OF_SERVICE"}
    assert change["revision"] == 2 and change["reason"] == "Awaiting dock repair"
    with pytest.raises(Conflict):
        inv.set_lifecycle_status(asset, "COMMISSIONING", "x")
    with pytest.raises(ValueError):
        inv.set_lifecycle_status(asset, "DECOMMISSIONED", "x")
    with pytest.raises(ValueError):
        inv.set_lifecycle_status(asset, "IN_SERVICE", "")
    with pytest.raises(ValueError):
        inv.set_lifecycle_status(asset, "BROKEN", "x")
    inv.set_lifecycle_status(asset, "IN_SERVICE", "Repaired")
    assert inv.get_robot(asset)["lifecycle_status"] == "IN_SERVICE"


def test_decommission_is_terminal(inv):
    asset = commission(inv)
    assert inv.decommission(asset, "Written off")["action"] == "DECOMMISSIONED"
    robot = inv.get_robot(asset)
    assert robot["lifecycle_status"] == "DECOMMISSIONED" and robot["decommissioned_at"]
    with pytest.raises(Conflict):
        inv.decommission(asset, "again")
    with pytest.raises(Conflict):
        inv.set_lifecycle_status(asset, "IN_SERVICE", "x")
    with pytest.raises(Conflict):
        inv.admin_edit(asset, {"fleet_id": "F"}, "x")


def test_admin_edit_is_allowlisted_and_requires_a_reason(inv):
    asset = commission(inv)
    change = inv.admin_edit(asset, {"fleet_id": "FLEET-B", "home_zone": "charging_station"}, "Re-homed",
                            actor={"type": "WORKER", "id": "E-1"})
    assert change["action"] == "ADMIN_EDIT" and change["actor"] == {"type": "WORKER", "id": "E-1"}
    assert change["diff"]["fleet_id"] == {"from": None, "to": "FLEET-B"}
    with pytest.raises(ValueError, match="serial_number"):
        inv.admin_edit(asset, {"serial_number": "X"}, "no")
    with pytest.raises(ValueError):
        inv.admin_edit(asset, {"fleet_id": "FLEET-C"}, "")
    with pytest.raises(ValueError):
        inv.admin_edit(asset, {"fleet_id": "FLEET-B"}, "same value")
    with pytest.raises(ValueError):
        inv.admin_edit(asset, {"hw_revision": "Z"}, "bad rev")


def test_report_state_logs_only_significant_changes(inv, clock):
    asset = commission(inv)
    before = inv.store.count("change_log")
    clock.advance(seconds=30)
    assert inv.report_state(asset, {"operational_mode": "MOVING", "zone": "shelf_a", "battery": {"soc_pct": 80.0}}) is None
    assert inv.store.count("change_log") == before
    reported = inv.get_robot(asset)["reported"]
    assert reported["operational_mode"] == "MOVING" and reported["battery"]["soc_pct"] == 80.0
    assert reported["reported_at"] == "2026-09-30T12:00:30Z"
    change = inv.report_state(asset, {"software_version": "0.9.0-beta", "battery": {"cycle_count": 3}})
    assert change["aggregate_type"] == "ROBOT_OBSERVATION" and change["revision"] == 2
    assert change["diff"]["software_version"] == {"from": "2.2.0", "to": "0.9.0-beta"}
    flags = inv.get_robot(asset)["flags"]
    assert flags["software_mismatch"] is True and flags["running_unknown_software"] is True
    with pytest.raises(ValueError):
        inv.report_state(asset, {"firmware": "x"})
    with pytest.raises(ValueError):
        inv.report_state(asset, {"health_state": "SPARKLY"})


def test_recalled_and_stale_flags(inv, clock):
    asset = commission(inv)
    inv.report_state(asset, {"software_version": "2.2.1"})
    assert inv.get_robot(asset)["flags"]["running_recalled_release"] is True
    clock.advance(seconds=301)
    assert inv.get_robot(asset)["flags"]["report_stale"] is True
    assert inv.touch_remote_reports() == 1
    assert inv.get_robot(asset)["flags"]["report_stale"] is False
    inv.report_state(asset, {"connectivity": "OFFLINE"})
    clock.advance(seconds=301)
    assert inv.touch_remote_reports() == 0  # offline robots don't report in
    assert inv.touch_remote_reports(exclude=[asset]) == 0


def test_list_robots_filters_and_summarises(inv):
    a = commission(inv, site="WH-01")
    b = commission(inv, model="NW-PF1200", site="WH-02", zone="pallet_aisle")
    inv.set_lifecycle_status(b, "OUT_OF_SERVICE", "x")
    assert [r["asset_id"] for r in inv.list_robots(site="WH-02")] == [b]
    assert [r["asset_id"] for r in inv.list_robots(status="in_service")] == [a]
    row = inv.list_robots()[0]
    assert row["model_name"] == "TR-50 Tote Runner" and row["embodiment_class"] == "AMR"
    assert row["reported_software_version"] == "2.2.0" and row["flags"]["calibration_worst"] == "VALID"


def test_robot_history_is_newest_first(inv):
    asset = commission(inv)
    inv.admin_edit(asset, {"fleet_id": "F"}, "x")
    assert [c["action"] for c in inv.robot_history(asset)] == ["ADMIN_EDIT", "STATE_REPORTED", "COMMISSIONED"]
    with pytest.raises(NotFound):
        inv.robot_history("AST-404")
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest backend/test_inventory_fleet.py -q`
Expected: failures with `AttributeError: 'InventoryService' object has no attribute 'commission_robot'`.

- [ ] **Step 3: Implement** — create `backend/inventory/fleet.py`:

```python
"""Robot assets: commissioning, lifecycle, admin edits, robot-reported
state, and the read models (with derived flags) the API and dashboard use.

Declared state (what the fleet manager believes — robot_asset / component
rows) and reported state (what the robot itself last said — reported_state)
are deliberately separate: mismatches between them are exactly what PWA's
passport checks look for.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any, Dict, Iterable, List, Mapping, Optional

from .core import SYSTEM, sha256_text
from .documents import iso, parse_iso
from .errors import Conflict

LIFECYCLE_STATUSES = ("COMMISSIONING", "IN_SERVICE", "MAINTENANCE", "OUT_OF_SERVICE", "DECOMMISSIONED")
LIFECYCLE_TRANSITIONS = {
    "COMMISSIONING": {"IN_SERVICE"},
    "IN_SERVICE": {"MAINTENANCE", "OUT_OF_SERVICE"},
    "MAINTENANCE": {"IN_SERVICE", "OUT_OF_SERVICE"},
    "OUT_OF_SERVICE": {"IN_SERVICE", "MAINTENANCE"},
    "DECOMMISSIONED": set(),
}
ADMIN_EDITABLE_FIELDS = frozenset({
    "asset_tag", "fleet_id", "site_code", "home_zone", "hw_revision", "config_hash", "safety_policy_hash",
})
SIGNIFICANT_REPORT_FIELDS = (
    "software_version", "os_version", "config_hash", "safety_policy_hash", "ai_policy_version",
    "component_firmware", "health_state", "connectivity",
)
REFRESH_ONLY_REPORT_FIELDS = ("operational_mode", "zone")
BATTERY_SIGNIFICANT_KEYS = ("soh_pct", "cycle_count")
HEALTH_STATES = ("OK", "DEGRADED", "FAULT", "UNKNOWN")
CONNECTIVITY_STATES = ("ONLINE", "OFFLINE", "INTERMITTENT")
CALIBRATION_RANK = {"VALID": 0, "DUE_SOON": 1, "MISSING": 2, "EXPIRED": 3, "FAILED": 4}
OTA_ACTIVE_STATES = ("STAGED", "DOWNLOADING", "INSTALLING", "REPORTED")


class FleetMixin:
    # ---- lookups & documents ------------------------------------------ #
    def has_asset(self, asset_id: Optional[str]) -> bool:
        return bool(asset_id) and self.store.exists("robot_asset", "asset_id", asset_id)

    def _asset(self, asset_id: str) -> Dict[str, Any]:
        return self._require("robot_asset", "asset_id", asset_id, "Robot asset")

    def _latest_calibration(self, component_id: str) -> Optional[Dict[str, Any]]:
        rows = self.store.select("calibration_record", "component_id = ?", (component_id,),
                                 order="performed_at DESC, rowid DESC", limit=1)
        return rows[0] if rows else None

    def _open_work_orders(self, asset_id: str) -> List[Dict[str, Any]]:
        return self.store.select("work_order", "asset_id = ? AND status = 'OPEN'", (asset_id,))

    def _active_ota_rows(self, asset_id: str) -> List[Dict[str, Any]]:
        marks = ", ".join("?" for _ in OTA_ACTIVE_STATES)
        return self.store.select("ota_job", f"asset_id = ? AND state IN ({marks})",
                                 (asset_id, *OTA_ACTIVE_STATES))

    def _asset_document(self, asset_id: str) -> Dict[str, Any]:
        """The ROBOT_ASSET aggregate as stored in change_log `after` snapshots."""
        asset = self._asset(asset_id)
        components = self.store.select("component", "asset_id = ? AND status != 'REMOVED'", (asset_id,))
        for component in components:
            component["latest_calibration"] = self._latest_calibration(component["component_id"])
        return {
            **asset,
            "components": components,
            "open_work_orders": self._open_work_orders(asset_id),
            "active_ota_jobs": self._active_ota_rows(asset_id),
        }

    def _commit_asset(self, asset_id: str, action: str, before: Optional[dict], actor: Any = None,
                      reason: Optional[str] = None, subject_id: Optional[str] = None) -> Dict[str, Any]:
        revision = self._asset(asset_id)["revision"] + 1
        self.store.update("robot_asset", "asset_id", asset_id, {"revision": revision, "updated_at": self.now_iso()})
        return self._record(aggregate_type="ROBOT_ASSET", aggregate_id=asset_id, revision=revision,
                            action=action, before=before, after=self._asset_document(asset_id),
                            actor=actor, reason=reason, subject_id=subject_id)

    def _new_serial(self, manufacturer: Mapping[str, Any], code: str) -> str:
        number = self.store.bump_counter(f"serial:{manufacturer['manufacturer_id']}", start=1001)
        short = code.split("-", 1)[-1]
        return f"{manufacturer['serial_prefix']}-{short}-{self.now().year}-{number:05d}"

    def _install_component(self, asset_id: str, slot: str, part_number: str,
                           hw_revision: Optional[str]) -> Dict[str, Any]:
        part = self.get_part(part_number)
        hw = hw_revision or part["hw_revisions"][-1]
        if hw not in part["hw_revisions"]:
            raise ValueError(f"{part_number} has no hardware revision {hw!r} (known: {part['hw_revisions']})")
        release = self.best_firmware(part_number, hw) if part["firmware_capable"] else None
        manufacturer = self._require("manufacturer", "manufacturer_id", part["manufacturer_id"], "Manufacturer")
        row = {
            "component_id": self.store.next_id("CMP-", "component", "component_id", 7),
            "asset_id": asset_id, "slot": slot, "part_number": part_number,
            "serial": self._new_serial(manufacturer, part_number), "hw_revision": hw,
            "firmware_release_id": release["release_id"] if release else None,
            "installed_at": self.now_iso(), "removed_at": None, "status": "INSTALLED",
        }
        self.store.insert("component", row)
        return {**row, "firmware_version": release["version"] if release else None}

    def _insert_calibration(self, component_id: str, part: Mapping[str, Any], result: str,
                            performed_by: Optional[str], method: str, certificate_ref: Optional[str]) -> str:
        now = self.now()
        calibration_id = self.store.next_id("CAL-", "calibration_record", "calibration_id", 7)
        valid_until = iso(now + timedelta(days=part["calibration_interval_days"])) if result == "PASS" else None
        self.store.insert("calibration_record", {
            "calibration_id": calibration_id, "component_id": component_id, "performed_at": iso(now),
            "performed_by": performed_by, "method": method, "result": result,
            "valid_until": valid_until, "certificate_ref": certificate_ref,
        })
        return calibration_id

    def _ota_view(self, job: Mapping[str, Any]) -> Dict[str, Any]:
        release = self.get_release(job["release_id"])
        slot = None
        if job["component_id"]:
            slot = self._require("component", "component_id", job["component_id"], "Component")["slot"]
        return {**job, "kind": release["kind"], "version": release["version"],
                "target_code": release["target_code"], "slot": slot}

    # ---- commissioning & lifecycle ------------------------------------ #
    def _commission_release(self, model_code: str, hw: str, software_release_id: Optional[str]) -> Dict[str, Any]:
        if software_release_id:
            release = self.get_release(software_release_id)
            if release["kind"] != "ROBOT_SOFTWARE" or release["target_code"] != model_code:
                raise ValueError(f"{software_release_id} is not a {model_code} robot-software release")
            if release["status"] == "RECALLED":
                raise Conflict(f"{software_release_id} has been recalled")
        else:
            release = self.current_release("ROBOT_SOFTWARE", model_code)
            if release is None:
                raise ValueError(f"{model_code} has no current robot-software release")
        if release["min_hw_rev"] and hw < release["min_hw_rev"]:
            raise Conflict(f"{release['release_id']} requires hardware revision {release['min_hw_rev']} or later (asset: {hw})")
        return release

    def commission_robot(self, model_code: str, site_code: str, home_zone: Optional[str] = None, *,
                         asset_id: Optional[str] = None, serial_number: Optional[str] = None,
                         asset_tag: Optional[str] = None, hw_revision: Optional[str] = None,
                         software_release_id: Optional[str] = None, fleet_id: Optional[str] = None,
                         lifecycle_status: str = "IN_SERVICE", actor: Any = None) -> Dict[str, Any]:
        if lifecycle_status not in ("IN_SERVICE", "COMMISSIONING"):
            raise ValueError("A robot is commissioned as IN_SERVICE or COMMISSIONING")
        site_code = str(site_code or "").strip()
        if not site_code:
            raise ValueError("site_code is required")
        with self._tx():
            model = self.get_model(model_code)
            manufacturer = self._require("manufacturer", "manufacturer_id", model["manufacturer_id"], "Manufacturer")
            hw = hw_revision or model["hw_revisions"][-1]
            if hw not in model["hw_revisions"]:
                raise ValueError(f"{model_code} has no hardware revision {hw!r} (known: {model['hw_revisions']})")
            release = self._commission_release(model_code, hw, software_release_id)
            ai_release = self.current_release("AI_POLICY_MODEL", model_code)
            if asset_id:
                if self.has_asset(asset_id):
                    raise Conflict(f"Robot asset '{asset_id}' already exists")
            else:
                asset_id = self.store.next_id("AST-", "robot_asset", "asset_id", 6)
            serial = serial_number or self._new_serial(manufacturer, model_code)
            if self.store.count("robot_asset", "manufacturer_id = ? AND serial_number = ?",
                                (manufacturer["manufacturer_id"], serial)):
                raise Conflict(f"{manufacturer['name']} serial {serial} is already registered")
            tag = asset_tag or f"AT-{asset_id.split('-', 1)[-1]}"
            if self.store.exists("robot_asset", "asset_tag", tag):
                raise Conflict(f"Asset tag {tag} is already in use")
            now = self.now_iso()
            config_hash = sha256_text(f"{model_code}:config:baseline-1")
            safety_hash = sha256_text(f"{model_code}:safety:baseline-1")
            self.store.insert("robot_asset", {
                "asset_id": asset_id, "asset_tag": tag, "serial_number": serial,
                "manufacturer_id": manufacturer["manufacturer_id"], "model_code": model_code,
                "hw_revision": hw, "fleet_id": fleet_id, "site_code": site_code, "home_zone": home_zone,
                "lifecycle_status": lifecycle_status, "commissioned_at": now, "decommissioned_at": None,
                "software_release_id": release["release_id"], "config_hash": config_hash,
                "safety_policy_hash": safety_hash,
                "ai_policy_release_id": ai_release["release_id"] if ai_release else None,
                "revision": 0, "updated_at": now,
            })
            firmware: Dict[str, str] = {}
            for slot in model["component_layout"]:
                component = self._install_component(asset_id, slot["slot"], slot["part_number"], None)
                if component["firmware_version"]:
                    firmware[slot["slot"]] = component["firmware_version"]
                part = self.get_part(slot["part_number"])
                if part["calibration_interval_days"]:
                    self._insert_calibration(component["component_id"], part, "PASS", None, "FACTORY", None)
            change = self._commit_asset(asset_id, "COMMISSIONED", None, actor=actor)
            self.store.insert("reported_state", {
                "asset_id": asset_id, "software_version": release["version"],
                "os_version": self.os_version_for(release["version"]),
                "config_hash": config_hash, "safety_policy_hash": safety_hash,
                "ai_policy_version": ai_release["version"] if ai_release else None,
                "component_firmware": firmware, "health_state": "OK", "connectivity": "ONLINE",
                "operational_mode": "IDLE", "zone": home_zone,
                "battery": ({"soc_pct": 100.0, "soh_pct": 100.0, "cycle_count": 0}
                            if model["spec"].get("battery") else None),
                "reported_at": now, "observation_seq": 1,
            })
            self._record(aggregate_type="ROBOT_OBSERVATION", aggregate_id=asset_id, revision=1,
                         action="STATE_REPORTED", before=None,
                         after=self.store.get("reported_state", "asset_id", asset_id), actor=SYSTEM)
            return change

    def set_lifecycle_status(self, asset_id: str, status: str, reason: str, actor: Any = None) -> Dict[str, Any]:
        status = str(status or "").upper()
        if status not in LIFECYCLE_STATUSES:
            raise ValueError(f"Unknown lifecycle status {status!r} (known: {list(LIFECYCLE_STATUSES)})")
        if status == "DECOMMISSIONED":
            raise ValueError("Use decommission to retire an asset")
        reason = self._reason(reason)
        with self._tx():
            asset = self._asset(asset_id)
            current = asset["lifecycle_status"]
            if status not in LIFECYCLE_TRANSITIONS[current]:
                raise Conflict(f"{asset_id} cannot move from {current} to {status}")
            if status == "IN_SERVICE" and self._open_work_orders(asset_id):
                raise Conflict(f"{asset_id} has open work orders — close them first")
            before = self._asset_document(asset_id)
            self.store.update("robot_asset", "asset_id", asset_id, {"lifecycle_status": status})
            return self._commit_asset(asset_id, "STATUS_CHANGED", before, actor=actor, reason=reason)

    def decommission(self, asset_id: str, reason: str, actor: Any = None) -> Dict[str, Any]:
        reason = self._reason(reason)
        with self._tx():
            asset = self._asset(asset_id)
            if asset["lifecycle_status"] == "DECOMMISSIONED":
                raise Conflict(f"{asset_id} is already decommissioned")
            before = self._asset_document(asset_id)
            now = self.now_iso()
            for job in self._active_ota_rows(asset_id):
                self.store.update("ota_job", "job_id", job["job_id"],
                                  {"state": "FAILED", "updated_at": now, "failure_reason": "asset decommissioned"})
            for work_order in self._open_work_orders(asset_id):
                self.store.update("work_order", "wo_id", work_order["wo_id"],
                                  {"status": "CLOSED", "closed_at": now, "resolution": "asset decommissioned"})
            self.store.update("robot_asset", "asset_id", asset_id,
                              {"lifecycle_status": "DECOMMISSIONED", "decommissioned_at": now})
            return self._commit_asset(asset_id, "DECOMMISSIONED", before, actor=actor, reason=reason)

    def admin_edit(self, asset_id: str, fields: Mapping[str, Any], reason: str, actor: Any = None) -> Dict[str, Any]:
        unknown = sorted(set(fields) - ADMIN_EDITABLE_FIELDS)
        if unknown:
            raise ValueError(f"Field(s) not editable: {unknown} (editable: {sorted(ADMIN_EDITABLE_FIELDS)})")
        reason = self._reason(reason)
        with self._tx():
            asset = self._asset(asset_id)
            if asset["lifecycle_status"] == "DECOMMISSIONED":
                raise Conflict(f"{asset_id} is decommissioned")
            changes = {key: value for key, value in fields.items() if asset[key] != value}
            if not changes:
                raise ValueError("No changes to apply")
            if "hw_revision" in changes:
                revisions = self.get_model(asset["model_code"])["hw_revisions"]
                if changes["hw_revision"] not in revisions:
                    raise ValueError(f"{asset['model_code']} has no hardware revision {changes['hw_revision']!r}")
            if "asset_tag" in changes:
                if not str(changes["asset_tag"] or "").strip():
                    raise ValueError("asset_tag cannot be empty")
                if self.store.exists("robot_asset", "asset_tag", changes["asset_tag"]):
                    raise Conflict(f"Asset tag {changes['asset_tag']} is already in use")
            if "site_code" in changes and not str(changes["site_code"] or "").strip():
                raise ValueError("site_code cannot be empty")
            before = self._asset_document(asset_id)
            self.store.update("robot_asset", "asset_id", asset_id, changes)
            return self._commit_asset(asset_id, "ADMIN_EDIT", before, actor=actor, reason=reason)

    # ---- robot-reported state ----------------------------------------- #
    def report_state(self, asset_id: str, reported: Mapping[str, Any], actor: Any = None) -> Optional[Dict[str, Any]]:
        """A robot heartbeat. Always refreshes reported_at; appends a
        ROBOT_OBSERVATION change only when a significant field changed."""
        allowed = set(SIGNIFICANT_REPORT_FIELDS) | set(REFRESH_ONLY_REPORT_FIELDS) | {"battery"}
        unknown = sorted(set(reported) - allowed)
        if unknown:
            raise ValueError(f"Unknown reported field(s): {unknown}")
        if "health_state" in reported and reported["health_state"] not in HEALTH_STATES:
            raise ValueError(f"Unknown health_state {reported['health_state']!r} (known: {list(HEALTH_STATES)})")
        if "connectivity" in reported and reported["connectivity"] not in CONNECTIVITY_STATES:
            raise ValueError(f"Unknown connectivity {reported['connectivity']!r} (known: {list(CONNECTIVITY_STATES)})")
        with self._tx():
            self._asset(asset_id)
            current = self._require("reported_state", "asset_id", asset_id, "Reported state")
            updates = {key: reported[key] for key in REFRESH_ONLY_REPORT_FIELDS if key in reported}
            updates["reported_at"] = self.now_iso()
            significant = {key: reported[key] for key in SIGNIFICANT_REPORT_FIELDS
                           if key in reported and reported[key] != current[key]}
            if reported.get("battery") is not None:
                old_battery = current["battery"] or {}
                merged = {**old_battery, **reported["battery"]}
                updates["battery"] = merged
                if any(merged.get(key) != old_battery.get(key) for key in BATTERY_SIGNIFICANT_KEYS):
                    significant["battery"] = merged
            if not significant:
                self.store.update("reported_state", "asset_id", asset_id, updates)
                return None
            seq = current["observation_seq"] + 1
            self.store.update("reported_state", "asset_id", asset_id, {**updates, **significant, "observation_seq": seq})
            return self._record(aggregate_type="ROBOT_OBSERVATION", aggregate_id=asset_id, revision=seq,
                                action="STATE_REPORTED", before=current,
                                after=self.store.get("reported_state", "asset_id", asset_id),
                                actor=actor or SYSTEM)

    def touch_remote_reports(self, exclude: Iterable[str] = ()) -> int:
        """Refresh reported_at for every ONLINE, non-decommissioned asset not in
        `exclude` — the robots at other sites checking in."""
        excluded = set(exclude)
        now = self.now_iso()
        with self._tx():
            rows = self.store.select(
                "reported_state",
                "connectivity = 'ONLINE' AND asset_id IN "
                "(SELECT asset_id FROM robot_asset WHERE lifecycle_status != 'DECOMMISSIONED')")
            touched = 0
            for row in rows:
                if row["asset_id"] in excluded:
                    continue
                self.store.update("reported_state", "asset_id", row["asset_id"], {"reported_at": now})
                touched += 1
            return touched

    # ---- read models --------------------------------------------------- #
    def _calibration_status(self, part: Mapping[str, Any], latest: Optional[Mapping[str, Any]], now) -> Optional[str]:
        if not part["calibration_interval_days"]:
            return None
        if latest is None:
            return "MISSING"
        if latest["result"] == "FAIL":
            return "FAILED"
        until = parse_iso(latest["valid_until"])
        if until <= now:
            return "EXPIRED"
        if until - now <= timedelta(days=self.setting("CALIBRATION_DUE_SOON_DAYS")):
            return "DUE_SOON"
        return "VALID"

    def _component_view(self, component: Mapping[str, Any], reported: Optional[Mapping[str, Any]], now) -> Dict[str, Any]:
        part = self.get_part(component["part_number"])
        declared = self.get_release(component["firmware_release_id"])["version"] if component["firmware_release_id"] else None
        reported_firmware = ((reported or {}).get("component_firmware") or {}).get(component["slot"])
        history = self.store.select("calibration_record", "component_id = ?", (component["component_id"],),
                                    order="performed_at DESC, rowid DESC", limit=5)
        view = {key: value for key, value in component.items() if key != "latest_calibration"}
        view.update({
            "part_name": part["name"], "component_type": part["component_type"],
            "declared_firmware_version": declared, "reported_firmware_version": reported_firmware,
            "calibration_interval_days": part["calibration_interval_days"],
            "calibration_status": self._calibration_status(part, component.get("latest_calibration"), now),
            "calibrations": history,
        })
        return view

    def _robot_flags(self, record: Mapping[str, Any], now) -> Dict[str, Any]:
        reported = record["reported"] or {}
        components = record["components"]
        statuses = [c["calibration_status"] for c in components if c["calibration_status"]]
        running = reported.get("software_version")
        running_release = self.release_by_version("ROBOT_SOFTWARE", record["model_code"], running)
        reported_at = parse_iso(reported.get("reported_at"))
        return {
            "software_mismatch": bool(reported) and running != record["declared_software_version"],
            "ai_policy_mismatch": (record["declared_ai_policy_version"] is not None
                                   and reported.get("ai_policy_version") != record["declared_ai_policy_version"]),
            "component_firmware_mismatches": [
                {"slot": c["slot"], "declared": c["declared_firmware_version"], "reported": c["reported_firmware_version"]}
                for c in components
                if c["declared_firmware_version"] and c["reported_firmware_version"] != c["declared_firmware_version"]
            ],
            "calibration_worst": max(statuses, key=CALIBRATION_RANK.__getitem__) if statuses else None,
            "calibration_issues": [{"slot": c["slot"], "status": c["calibration_status"]}
                                   for c in components if c["calibration_status"] not in (None, "VALID")],
            "report_stale": reported_at is None
                            or (now - reported_at).total_seconds() > self.setting("REPORT_STALE_SECONDS"),
            "running_recalled_release": (record["declared_software_status"] == "RECALLED"
                                         or (running_release is not None and running_release["status"] == "RECALLED")),
            "running_unknown_software": bool(running) and running_release is None,
            "active_ota_job": next((job for job in record["ota_jobs"] if job["state"] in OTA_ACTIVE_STATES), None),
        }

    def get_robot(self, asset_id: str) -> Dict[str, Any]:
        document = self._asset_document(asset_id)
        now = self.now()
        model = self.get_model(document["model_code"])
        reported = self.store.get("reported_state", "asset_id", asset_id)
        declared_software = self.get_release(document["software_release_id"])
        declared_ai = self.get_release(document["ai_policy_release_id"]) if document["ai_policy_release_id"] else None
        record = {key: value for key, value in document.items()
                  if key not in ("components", "open_work_orders", "active_ota_jobs")}
        record.update({
            "manufacturer_name": model["manufacturer_name"],
            "model": model,
            "declared_software_version": declared_software["version"],
            "declared_software_status": declared_software["status"],
            "declared_ai_policy_version": declared_ai["version"] if declared_ai else None,
            "reported": reported,
            "components": [self._component_view(c, reported, now) for c in document["components"]],
            "work_orders": self.store.select("work_order", "asset_id = ?", (asset_id,), order="opened_at DESC, rowid DESC"),
            "ota_jobs": [self._ota_view(job) for job in
                         self.store.select("ota_job", "asset_id = ?", (asset_id,), order="rowid DESC", limit=20)],
        })
        record["flags"] = self._robot_flags(record, now)
        return record

    @staticmethod
    def _robot_summary(record: Mapping[str, Any]) -> Dict[str, Any]:
        reported = record["reported"] or {}
        return {
            "asset_id": record["asset_id"], "asset_tag": record["asset_tag"],
            "serial_number": record["serial_number"], "model_code": record["model_code"],
            "model_name": record["model"]["name"], "embodiment_class": record["model"]["embodiment_class"],
            "manufacturer_name": record["manufacturer_name"], "site_code": record["site_code"],
            "home_zone": record["home_zone"], "lifecycle_status": record["lifecycle_status"],
            "hw_revision": record["hw_revision"], "revision": record["revision"],
            "declared_software_version": record["declared_software_version"],
            "reported_software_version": reported.get("software_version"),
            "connectivity": reported.get("connectivity"), "health_state": reported.get("health_state"),
            "reported_at": reported.get("reported_at"), "flags": record["flags"],
        }

    def list_robots(self, site: Optional[str] = None, status: Optional[str] = None) -> List[Dict[str, Any]]:
        where, params = [], []
        if site:
            where.append("site_code = ?")
            params.append(site)
        if status:
            where.append("lifecycle_status = ?")
            params.append(str(status).upper())
        rows = self.store.select("robot_asset", " AND ".join(where), params, order="asset_id")
        return [self._robot_summary(self.get_robot(row["asset_id"])) for row in rows]

    def robot_history(self, asset_id: str, limit: int = 200) -> List[Dict[str, Any]]:
        self._asset(asset_id)
        return self.history([asset_id], limit)
```

Modify `backend/inventory/service.py` — imports and class line become:

```python
from .catalog import CatalogMixin
from .core import ServiceCore
from .fleet import FleetMixin


class InventoryService(FleetMixin, CatalogMixin, ServiceCore):
    pass
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest backend/test_inventory_fleet.py backend/test_inventory_catalog.py -q`
Expected: `19 passed`.

- [ ] **Step 5: Commit**

```bash
git add backend/inventory/fleet.py backend/inventory/service.py backend/test_inventory_fleet.py
git commit -m "feat(inventory): robot assets with declared vs reported state and derived flags

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Servicing — work orders, component swaps, calibration

**Files:**
- Create: `backend/inventory/servicing.py`
- Modify: `backend/inventory/service.py`
- Test: `backend/test_inventory_servicing.py`

**Interfaces:**
- Consumes: Task 4 helpers (`_asset`, `_asset_document`, `_commit_asset`, `_open_work_orders`, `_install_component`, `_insert_calibration`), `_worker_actor`.
- Produces: `WORK_ORDER_TYPES`, `CALIBRATION_RESULTS`; `ServicingMixin.open_work_order(asset_id, wo_type, description, technician_id=None, actor=None) -> change` (`subject_id` = new `wo_id`), `close_work_order(wo_id, resolution, actor=None) -> change`, `swap_component(wo_id, slot, performed_by=None, hw_revision=None, part_number=None, actor=None) -> change` (`subject_id` = new `component_id`), `record_calibration(component_id, result, performed_by=None, method="FIELD", certificate_ref=None, actor=None) -> change` (`subject_id` = `calibration_id`). Worker ids are checked against the `worker` table (Task 7 fills it; `NotFound` until then).

- [ ] **Step 1: Write the failing tests** — create `backend/test_inventory_servicing.py`:

```python
"""Servicing: work orders, component swaps, calibration."""
from datetime import datetime, timedelta, timezone

import pytest

from backend.inventory import Conflict, NotFound, open_inventory

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, **kwargs):
        self.now += timedelta(**kwargs)


@pytest.fixture
def clock():
    return Clock(NOW)


@pytest.fixture
def inv(clock):
    return open_inventory(":memory:", demo=False, clock=clock)


def commission(inv):
    return inv.commission_robot("AC-TR50", "WH-01", "parking_area")["aggregate_id"]


def component(inv, asset, slot):
    return next(c for c in inv.get_robot(asset)["components"] if c["slot"] == slot)


def test_open_work_order_moves_the_asset_to_maintenance(inv):
    asset = commission(inv)
    change = inv.open_work_order(asset, "corrective", "Lidar dirty")
    assert change["action"] == "WORK_ORDER_OPENED" and change["subject_id"] == "WO-000001"
    assert inv.get_robot(asset)["lifecycle_status"] == "MAINTENANCE"
    inspected = commission(inv)
    inv.open_work_order(inspected, "INSPECTION", "Quarterly check")
    assert inv.get_robot(inspected)["lifecycle_status"] == "IN_SERVICE"
    with pytest.raises(ValueError):
        inv.open_work_order(asset, "PAINTING", "x")
    with pytest.raises(ValueError):
        inv.open_work_order(asset, "CORRECTIVE", " ")
    with pytest.raises(NotFound):
        inv.open_work_order(asset, "CORRECTIVE", "x", technician_id="E-404")


def test_swap_component_installs_a_new_serial_without_calibration(inv):
    asset = commission(inv)
    old = component(inv, asset, "lidar")
    wo = inv.open_work_order(asset, "CORRECTIVE", "Replace lidar")["subject_id"]
    change = inv.swap_component(wo, "lidar")
    new = component(inv, asset, "lidar")
    assert new["component_id"] == change["subject_id"] != old["component_id"]
    assert new["serial"] != old["serial"]
    assert new["calibration_status"] == "MISSING"
    assert inv.get_robot(asset)["flags"]["calibration_worst"] == "MISSING"
    assert inv.store.get("component", "component_id", old["component_id"])["status"] == "REMOVED"
    assert f"components[{old['component_id']}].serial" in change["diff"]
    assert change["reason"].startswith(f"work order {wo}")
    with pytest.raises(NotFound):
        inv.swap_component(wo, "jetpack")
    with pytest.raises(ValueError):
        inv.swap_component(wo, "lidar", part_number="WT-D3")  # a camera is not a lidar


def test_swap_needs_an_open_work_order_on_an_asset_in_maintenance(inv):
    asset = commission(inv)
    wo = inv.open_work_order(asset, "INSPECTION", "Look only")["subject_id"]
    with pytest.raises(Conflict):
        inv.swap_component(wo, "lidar")  # asset is still IN_SERVICE
    inv.close_work_order(wo, "Looked fine")
    with pytest.raises(Conflict):
        inv.swap_component(wo, "lidar")  # work order closed


def test_close_work_order_returns_to_service_only_when_none_remain_open(inv):
    asset = commission(inv)
    first = inv.open_work_order(asset, "CORRECTIVE", "A")["subject_id"]
    second = inv.open_work_order(asset, "PREVENTIVE", "B")["subject_id"]
    inv.close_work_order(first, "done")
    assert inv.get_robot(asset)["lifecycle_status"] == "MAINTENANCE"
    with pytest.raises(Conflict):
        inv.set_lifecycle_status(asset, "IN_SERVICE", "early")
    closed = inv.close_work_order(second, "done")
    assert closed["action"] == "WORK_ORDER_CLOSED"
    assert inv.get_robot(asset)["lifecycle_status"] == "IN_SERVICE"
    with pytest.raises(Conflict):
        inv.close_work_order(second, "again")
    with pytest.raises(ValueError):
        inv.close_work_order(first, "")


def test_calibration_status_boundaries(inv, clock):
    asset = commission(inv)  # factory calibration at NOW
    scanner = component(inv, asset, "safety_scanner_front")  # 180-day interval
    clock.advance(days=165)
    assert component(inv, asset, "safety_scanner_front")["calibration_status"] == "VALID"
    clock.advance(days=1)
    assert component(inv, asset, "safety_scanner_front")["calibration_status"] == "DUE_SOON"
    clock.advance(days=14)
    assert component(inv, asset, "safety_scanner_front")["calibration_status"] == "EXPIRED"
    change = inv.record_calibration(scanner["component_id"], "pass")
    assert change["action"] == "CALIBRATION_RECORDED" and change["subject_id"].startswith("CAL-")
    assert component(inv, asset, "safety_scanner_front")["calibration_status"] == "VALID"
    inv.record_calibration(scanner["component_id"], "FAIL")
    assert component(inv, asset, "safety_scanner_front")["calibration_status"] == "FAILED"
    with pytest.raises(ValueError):
        inv.record_calibration(scanner["component_id"], "MAYBE")
    with pytest.raises(ValueError):
        inv.record_calibration(component(inv, asset, "compute")["component_id"], "PASS")  # no interval
    with pytest.raises(NotFound):
        inv.record_calibration(scanner["component_id"], "PASS", performed_by="E-404")
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest backend/test_inventory_servicing.py -q`
Expected: failures with `AttributeError: ... no attribute 'open_work_order'`.

- [ ] **Step 3: Implement** — create `backend/inventory/servicing.py`:

```python
"""Servicing: work orders, component swaps and calibration records — the
maintenance history a CMMS keeps for every robot."""
from __future__ import annotations

from typing import Any, Dict, Optional

from .errors import Conflict, NotFound

WORK_ORDER_TYPES = ("PREVENTIVE", "CORRECTIVE", "INSPECTION")
CALIBRATION_RESULTS = ("PASS", "FAIL")


class ServicingMixin:
    def open_work_order(self, asset_id: str, wo_type: str, description: str,
                        technician_id: Optional[str] = None, actor: Any = None) -> Dict[str, Any]:
        wo_type = str(wo_type or "").upper()
        if wo_type not in WORK_ORDER_TYPES:
            raise ValueError(f"Unknown work order type {wo_type!r} (known: {list(WORK_ORDER_TYPES)})")
        description = str(description or "").strip()
        if not description:
            raise ValueError("A description is required")
        with self._tx():
            asset = self._asset(asset_id)
            if asset["lifecycle_status"] == "DECOMMISSIONED":
                raise Conflict(f"{asset_id} is decommissioned")
            if technician_id:
                self._require("worker", "worker_id", technician_id, "Worker")
            before = self._asset_document(asset_id)
            wo_id = self.store.next_id("WO-", "work_order", "wo_id", 6)
            self.store.insert("work_order", {
                "wo_id": wo_id, "asset_id": asset_id, "type": wo_type, "description": description,
                "technician_id": technician_id, "status": "OPEN", "opened_at": self.now_iso(),
                "closed_at": None, "resolution": None,
            })
            if wo_type != "INSPECTION" and asset["lifecycle_status"] != "MAINTENANCE":
                self.store.update("robot_asset", "asset_id", asset_id, {"lifecycle_status": "MAINTENANCE"})
            return self._commit_asset(asset_id, "WORK_ORDER_OPENED", before,
                                      actor=self._worker_actor(technician_id, actor), subject_id=wo_id)

    def close_work_order(self, wo_id: str, resolution: str, actor: Any = None) -> Dict[str, Any]:
        resolution = str(resolution or "").strip()
        if not resolution:
            raise ValueError("A resolution is required")
        with self._tx():
            work_order = self._require("work_order", "wo_id", wo_id, "Work order")
            if work_order["status"] != "OPEN":
                raise Conflict(f"Work order {wo_id} is already closed")
            asset_id = work_order["asset_id"]
            asset = self._asset(asset_id)
            before = self._asset_document(asset_id)
            self.store.update("work_order", "wo_id", wo_id,
                              {"status": "CLOSED", "closed_at": self.now_iso(), "resolution": resolution})
            if asset["lifecycle_status"] == "MAINTENANCE" and not self._open_work_orders(asset_id):
                self.store.update("robot_asset", "asset_id", asset_id, {"lifecycle_status": "IN_SERVICE"})
            return self._commit_asset(asset_id, "WORK_ORDER_CLOSED", before,
                                      actor=self._worker_actor(work_order["technician_id"], actor), subject_id=wo_id)

    def swap_component(self, wo_id: str, slot: str, performed_by: Optional[str] = None,
                       hw_revision: Optional[str] = None, part_number: Optional[str] = None,
                       actor: Any = None) -> Dict[str, Any]:
        with self._tx():
            work_order = self._require("work_order", "wo_id", wo_id, "Work order")
            if work_order["status"] != "OPEN":
                raise Conflict(f"Work order {wo_id} is closed")
            asset_id = work_order["asset_id"]
            asset = self._asset(asset_id)
            if asset["lifecycle_status"] != "MAINTENANCE":
                raise Conflict(f"{asset_id} must be in MAINTENANCE to swap parts (is {asset['lifecycle_status']})")
            installed = self.store.select("component", "asset_id = ? AND slot = ? AND status != 'REMOVED'",
                                          (asset_id, slot))
            if not installed:
                raise NotFound(f"{asset_id} has no installed component in slot {slot!r}")
            old = installed[0]
            new_part = part_number or old["part_number"]
            if self.get_part(new_part)["component_type"] != self.get_part(old["part_number"])["component_type"]:
                raise ValueError(f"{new_part} cannot replace a {self.get_part(old['part_number'])['component_type']}")
            if performed_by:
                self._require("worker", "worker_id", performed_by, "Worker")
            before = self._asset_document(asset_id)
            self.store.update("component", "component_id", old["component_id"],
                              {"status": "REMOVED", "removed_at": self.now_iso()})
            new = self._install_component(asset_id, slot, new_part, hw_revision)
            return self._commit_asset(asset_id, "COMPONENT_SWAPPED", before,
                                      actor=self._worker_actor(performed_by, actor),
                                      reason=f"work order {wo_id}: replaced {old['serial']}",
                                      subject_id=new["component_id"])

    def record_calibration(self, component_id: str, result: str, performed_by: Optional[str] = None,
                           method: str = "FIELD", certificate_ref: Optional[str] = None,
                           actor: Any = None) -> Dict[str, Any]:
        result = str(result or "").upper()
        if result not in CALIBRATION_RESULTS:
            raise ValueError(f"Unknown calibration result {result!r} (known: {list(CALIBRATION_RESULTS)})")
        with self._tx():
            component = self._require("component", "component_id", component_id, "Component")
            if component["status"] == "REMOVED":
                raise Conflict(f"{component_id} has been removed from its robot")
            asset = self._asset(component["asset_id"])
            if asset["lifecycle_status"] == "DECOMMISSIONED":
                raise Conflict(f"{asset['asset_id']} is decommissioned")
            part = self.get_part(component["part_number"])
            if not part["calibration_interval_days"]:
                raise ValueError(f"{part['name']} does not require calibration")
            if performed_by:
                self._require("worker", "worker_id", performed_by, "Worker")
            before = self._asset_document(asset["asset_id"])
            calibration_id = self._insert_calibration(component_id, part, result, performed_by,
                                                      method or "FIELD", certificate_ref)
            return self._commit_asset(asset["asset_id"], "CALIBRATION_RECORDED", before,
                                      actor=self._worker_actor(performed_by, actor), subject_id=calibration_id)
```

Modify `backend/inventory/service.py`:

```python
from .catalog import CatalogMixin
from .core import ServiceCore
from .fleet import FleetMixin
from .servicing import ServicingMixin


class InventoryService(ServicingMixin, FleetMixin, CatalogMixin, ServiceCore):
    pass
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest backend/test_inventory_servicing.py backend/test_inventory_fleet.py -q`
Expected: `15 passed`.

- [ ] **Step 5: Commit**

```bash
git add backend/inventory/servicing.py backend/inventory/service.py backend/test_inventory_servicing.py
git commit -m "feat(inventory): work orders, component swaps and calibration records

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 6: OTA updates — job state machine

**Files:**
- Create: `backend/inventory/ota.py`
- Modify: `backend/inventory/service.py`
- Test: `backend/test_inventory_ota.py`

**Interfaces:**
- Consumes: Task 4 (`_asset`, `_asset_document`, `_commit_asset`, `_active_ota_rows`, `_ota_view`, `OTA_ACTIVE_STATES`), `actor_of`, `SYSTEM`.
- Produces: `OTA_RUNNING_STATES = ("STAGED","DOWNLOADING","INSTALLING")`, `SYSTEM_TRANSITIONS`; `OtaMixin.start_ota(asset_id, release_id, component_id=None, actor=None) -> change` (`subject_id` = `job_id`), `transition_ota(job_id, new_state, failure_reason=None) -> change` (system-driven: DOWNLOADING / INSTALLING / REPORTED / FAILED; action `OTA_<STATE>`), `verify_ota(job_id, verified_by=None, actor=None)`, `rollback_ota(job_id, reason, actor=None)`, `get_ota_job(job_id) -> view`, `active_ota_jobs() -> [view]` (only STAGED/DOWNLOADING/INSTALLING — the ones the simulator advances). A job view is the `ota_job` row plus `kind`, `version`, `target_code`, `slot`.

- [ ] **Step 1: Write the failing tests** — create `backend/test_inventory_ota.py`:

```python
"""OTA updates: staging, the timed states, verification, rollback."""
from datetime import datetime, timezone

import pytest

from backend.inventory import Conflict, NotFound, open_inventory

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def inv():
    return open_inventory(":memory:", demo=False, clock=lambda: NOW)


def commission(inv, model="AC-TR50", **kwargs):
    return inv.commission_robot(model, "WH-01", "parking_area", **kwargs)["aggregate_id"]


def test_ota_happy_path_keeps_declared_until_verified(inv):
    asset = commission(inv, software_release_id="AC-TR50:SW:2.1.1")
    staged = inv.start_ota(asset, "AC-TR50:SW:2.2.0", actor={"type": "WORKER", "id": "E-9"})
    job = staged["subject_id"]
    assert staged["action"] == "OTA_STAGED"
    view = inv.get_ota_job(job)
    assert view["from_version"] == "2.1.1" and view["created_by"] == "E-9" and view["version"] == "2.2.0"
    assert [j["job_id"] for j in inv.active_ota_jobs()] == [job]
    inv.transition_ota(job, "DOWNLOADING")
    inv.transition_ota(job, "INSTALLING")
    inv.report_state(asset, {"software_version": "2.2.0"})
    assert inv.transition_ota(job, "REPORTED")["action"] == "OTA_REPORTED"
    assert inv.active_ota_jobs() == []  # REPORTED waits for a human
    record = inv.get_robot(asset)
    assert record["flags"]["software_mismatch"] is True  # landed but not yet verified
    assert record["flags"]["active_ota_job"]["state"] == "REPORTED"
    verified = inv.verify_ota(job)
    assert verified["diff"]["software_release_id"] == {"from": "AC-TR50:SW:2.1.1", "to": "AC-TR50:SW:2.2.0"}
    record = inv.get_robot(asset)
    assert record["declared_software_version"] == "2.2.0"
    assert record["flags"]["software_mismatch"] is False and record["flags"]["active_ota_job"] is None


def test_ota_rejections(inv):
    asset = commission(inv)  # already on 2.2.0
    with pytest.raises(Conflict):
        inv.start_ota(asset, "AC-TR50:SW:2.2.0")  # already running it
    with pytest.raises(Conflict):
        inv.start_ota(asset, "AC-TR50:SW:2.2.1")  # recalled
    with pytest.raises(ValueError):
        inv.start_ota(asset, "NW-PF1200:SW:2.1.1")  # another model's release
    with pytest.raises(NotFound):
        inv.start_ota(asset, "AC-TR50:SW:9.9.9")
    forklift = commission(inv, model="NW-PF1200", hw_revision="A", software_release_id="NW-PF1200:SW:2.1.1")
    with pytest.raises(Conflict, match="hardware revision B"):
        inv.start_ota(forklift, "NW-PF1200:SW:2.2.0")
    inv.start_ota(asset, "AC-TR50:SW:2.1.1")  # downgrades are allowed
    with pytest.raises(Conflict):
        inv.start_ota(asset, "AC-TR50:SW:2.1.0")  # one job per target at a time
    inv.set_lifecycle_status(forklift, "OUT_OF_SERVICE", "x")
    with pytest.raises(Conflict):
        inv.start_ota(forklift, "NW-PF1200:SW:2.1.0")


def test_component_firmware_ota_targets_one_component(inv):
    asset = commission(inv)
    lidar = next(c for c in inv.get_robot(asset)["components"] if c["slot"] == "lidar")
    with pytest.raises(ValueError):
        inv.start_ota(asset, "WT-L360:FW:1.9.4")  # component_id required
    with pytest.raises(ValueError):
        inv.start_ota(asset, "AC-TR50:SW:2.1.1", component_id=lidar["component_id"])
    job = inv.start_ota(asset, "WT-L360:FW:1.9.4", component_id=lidar["component_id"])["subject_id"]
    inv.start_ota(asset, "AC-TR50:SW:2.1.1")  # robot software is a different target
    view = inv.get_ota_job(job)
    assert view["slot"] == "lidar" and view["kind"] == "COMPONENT_FIRMWARE" and view["from_version"] == "2.0.1"
    for state in ("DOWNLOADING", "INSTALLING", "REPORTED"):
        inv.transition_ota(job, state)
    inv.verify_ota(job)
    lidar = next(c for c in inv.get_robot(asset)["components"] if c["slot"] == "lidar")
    assert lidar["declared_firmware_version"] == "1.9.4"


def test_ota_state_machine_rejects_illegal_moves(inv):
    asset = commission(inv, software_release_id="AC-TR50:SW:2.1.1")
    job = inv.start_ota(asset, "AC-TR50:SW:2.2.0")["subject_id"]
    with pytest.raises(Conflict):
        inv.transition_ota(job, "REPORTED")  # skipping DOWNLOADING/INSTALLING
    with pytest.raises(Conflict):
        inv.verify_ota(job)
    with pytest.raises(Conflict):
        inv.transition_ota(job, "VERIFIED")  # only verify_ota verifies
    inv.transition_ota(job, "DOWNLOADING")
    failed = inv.transition_ota(job, "FAILED", failure_reason="checksum mismatch")
    assert failed["action"] == "OTA_FAILED" and failed["reason"] == "checksum mismatch"
    with pytest.raises(Conflict):
        inv.transition_ota(job, "INSTALLING")
    assert inv.get_robot(asset)["flags"]["active_ota_job"] is None
    inv.start_ota(asset, "AC-TR50:SW:2.2.0")  # a failed job frees the target


def test_rollback_records_a_reason_and_decommission_fails_active_jobs(inv):
    asset = commission(inv, software_release_id="AC-TR50:SW:2.1.1")
    job = inv.start_ota(asset, "AC-TR50:SW:2.2.0")["subject_id"]
    for state in ("DOWNLOADING", "INSTALLING", "REPORTED"):
        inv.transition_ota(job, state)
    with pytest.raises(ValueError):
        inv.rollback_ota(job, "")
    rolled = inv.rollback_ota(job, "Localisation regression on site")
    assert rolled["action"] == "OTA_ROLLED_BACK" and rolled["reason"] == "Localisation regression on site"
    assert inv.get_ota_job(job)["state"] == "ROLLED_BACK"
    assert inv.get_robot(asset)["declared_software_version"] == "2.1.1"
    second = inv.start_ota(asset, "AC-TR50:SW:2.2.0")["subject_id"]
    inv.decommission(asset, "Scrapped")
    assert inv.get_ota_job(second)["state"] == "FAILED"
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest backend/test_inventory_ota.py -q`
Expected: failures with `AttributeError: ... no attribute 'start_ota'`.

- [ ] **Step 3: Implement** — create `backend/inventory/ota.py`:

```python
"""Over-the-air updates: the OTA job state machine.

STAGED → DOWNLOADING → INSTALLING → REPORTED → VERIFIED | ROLLED_BACK, with
FAILED reachable from DOWNLOADING / INSTALLING. The simulator (FleetBridge)
drives the timed transitions through transition_ota(); a person verifies or
rolls back. Between REPORTED and VERIFIED the robot runs the new version
while the registry still declares the old one — the realistic "update
landed but was never verified" window.
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional

from .core import SYSTEM, actor_of
from .errors import Conflict

OTA_RUNNING_STATES = ("STAGED", "DOWNLOADING", "INSTALLING")
SYSTEM_TRANSITIONS = {
    "STAGED": {"DOWNLOADING"},
    "DOWNLOADING": {"INSTALLING", "FAILED"},
    "INSTALLING": {"REPORTED", "FAILED"},
}


class OtaMixin:
    def _running_version(self, asset: Mapping[str, Any], release: Mapping[str, Any],
                         component: Optional[Mapping[str, Any]]) -> Optional[str]:
        reported = self.store.get("reported_state", "asset_id", asset["asset_id"]) or {}
        if release["kind"] == "COMPONENT_FIRMWARE":
            declared = (self.get_release(component["firmware_release_id"])["version"]
                        if component["firmware_release_id"] else None)
            return (reported.get("component_firmware") or {}).get(component["slot"], declared)
        if release["kind"] == "AI_POLICY_MODEL":
            declared = (self.get_release(asset["ai_policy_release_id"])["version"]
                        if asset["ai_policy_release_id"] else None)
            return reported.get("ai_policy_version") or declared
        return reported.get("software_version") or self.get_release(asset["software_release_id"])["version"]

    def start_ota(self, asset_id: str, release_id: str, component_id: Optional[str] = None,
                  actor: Any = None) -> Dict[str, Any]:
        with self._tx():
            asset = self._asset(asset_id)
            if asset["lifecycle_status"] not in ("IN_SERVICE", "MAINTENANCE"):
                raise Conflict(f"{asset_id} is {asset['lifecycle_status']} — an update needs IN_SERVICE or MAINTENANCE")
            release = self.get_release(release_id)
            if release["status"] == "RECALLED":
                raise Conflict(f"{release_id} has been recalled")
            component = None
            if release["kind"] == "COMPONENT_FIRMWARE":
                if not component_id:
                    raise ValueError("component_id is required for a component-firmware update")
                component = self._require("component", "component_id", component_id, "Component")
                if component["asset_id"] != asset_id or component["status"] == "REMOVED":
                    raise ValueError(f"{component_id} is not installed on {asset_id}")
                if release["target_code"] != component["part_number"]:
                    raise ValueError(f"{release_id} is not firmware for {component['part_number']}")
                hw = component["hw_revision"]
            else:
                if component_id:
                    raise ValueError("component_id only applies to component-firmware updates")
                if release["target_code"] != asset["model_code"]:
                    raise ValueError(f"{release_id} is not a release for {asset['model_code']}")
                hw = asset["hw_revision"]
            if release["min_hw_rev"] and hw < release["min_hw_rev"]:
                raise Conflict(f"{release_id} requires hardware revision {release['min_hw_rev']} or later (installed: {hw})")
            running = self._running_version(asset, release, component)
            if running == release["version"]:
                raise Conflict(f"{asset_id} is already running {release['version']}")
            for job in self._active_ota_rows(asset_id):
                if job["component_id"] == component_id and self.get_release(job["release_id"])["kind"] == release["kind"]:
                    raise Conflict(f"{job['job_id']} is already updating this target ({job['state']})")
            before = self._asset_document(asset_id)
            job_id = self.store.next_id("OTA-", "ota_job", "job_id", 6)
            now = self.now_iso()
            self.store.insert("ota_job", {
                "job_id": job_id, "asset_id": asset_id, "component_id": component_id,
                "release_id": release_id, "from_version": running, "state": "STAGED",
                "created_at": now, "updated_at": now, "created_by": actor_of(actor)["id"],
                "failure_reason": None,
            })
            return self._commit_asset(asset_id, "OTA_STAGED", before, actor=actor, subject_id=job_id)

    def get_ota_job(self, job_id: str) -> Dict[str, Any]:
        return self._ota_view(self._require("ota_job", "job_id", job_id, "OTA job"))

    def active_ota_jobs(self) -> List[Dict[str, Any]]:
        """Jobs the simulator still has to advance (STAGED / DOWNLOADING / INSTALLING)."""
        marks = ", ".join("?" for _ in OTA_RUNNING_STATES)
        return [self._ota_view(job) for job in self.store.select("ota_job", f"state IN ({marks})", OTA_RUNNING_STATES)]

    def transition_ota(self, job_id: str, new_state: str, failure_reason: Optional[str] = None) -> Dict[str, Any]:
        new_state = str(new_state or "").upper()
        with self._tx():
            job = self._require("ota_job", "job_id", job_id, "OTA job")
            if new_state not in SYSTEM_TRANSITIONS.get(job["state"], set()):
                raise Conflict(f"{job_id} cannot go from {job['state']} to {new_state}")
            reason = (failure_reason or "unspecified failure") if new_state == "FAILED" else None
            before = self._asset_document(job["asset_id"])
            self.store.update("ota_job", "job_id", job_id,
                              {"state": new_state, "updated_at": self.now_iso(), "failure_reason": reason})
            return self._commit_asset(job["asset_id"], f"OTA_{new_state}", before, actor=SYSTEM,
                                      reason=reason, subject_id=job_id)

    def verify_ota(self, job_id: str, verified_by: Optional[str] = None, actor: Any = None) -> Dict[str, Any]:
        with self._tx():
            job = self._require("ota_job", "job_id", job_id, "OTA job")
            if job["state"] != "REPORTED":
                raise Conflict(f"{job_id} is {job['state']} — only a REPORTED update can be verified")
            if verified_by:
                self._require("worker", "worker_id", verified_by, "Worker")
            release = self.get_release(job["release_id"])
            before = self._asset_document(job["asset_id"])
            if release["kind"] == "ROBOT_SOFTWARE":
                self.store.update("robot_asset", "asset_id", job["asset_id"], {"software_release_id": release["release_id"]})
            elif release["kind"] == "AI_POLICY_MODEL":
                self.store.update("robot_asset", "asset_id", job["asset_id"], {"ai_policy_release_id": release["release_id"]})
            else:
                self.store.update("component", "component_id", job["component_id"], {"firmware_release_id": release["release_id"]})
            self.store.update("ota_job", "job_id", job_id, {"state": "VERIFIED", "updated_at": self.now_iso()})
            return self._commit_asset(job["asset_id"], "OTA_VERIFIED", before,
                                      actor=self._worker_actor(verified_by, actor), subject_id=job_id)

    def rollback_ota(self, job_id: str, reason: str, actor: Any = None) -> Dict[str, Any]:
        reason = self._reason(reason)
        with self._tx():
            job = self._require("ota_job", "job_id", job_id, "OTA job")
            if job["state"] != "REPORTED":
                raise Conflict(f"{job_id} is {job['state']} — only a REPORTED update can be rolled back")
            before = self._asset_document(job["asset_id"])
            self.store.update("ota_job", "job_id", job_id,
                              {"state": "ROLLED_BACK", "updated_at": self.now_iso(), "failure_reason": reason})
            return self._commit_asset(job["asset_id"], "OTA_ROLLED_BACK", before, actor=actor,
                                      reason=reason, subject_id=job_id)
```

Modify `backend/inventory/service.py`:

```python
from .catalog import CatalogMixin
from .core import ServiceCore
from .fleet import FleetMixin
from .ota import OtaMixin
from .servicing import ServicingMixin


class InventoryService(OtaMixin, ServicingMixin, FleetMixin, CatalogMixin, ServiceCore):
    pass
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest backend/test_inventory_ota.py -q`
Expected: `5 passed`.

- [ ] **Step 5: Commit**

```bash
git add backend/inventory/ota.py backend/inventory/service.py backend/test_inventory_ota.py
git commit -m "feat(inventory): OTA job state machine with verify/rollback window

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Workforce — workers, credentials, training

**Files:**
- Create: `backend/inventory/workforce.py`
- Modify: `backend/inventory/service.py`
- Test: `backend/test_inventory_workforce.py`

**Interfaces:**
- Consumes: Tasks 1–2 (`_tx`, `_record`, `_require`, `_reason`, `sha256_text`, `to_datetime`, `parse_iso`, `iso`).
- Produces: `WORKER_TYPES`, `EMPLOYMENT_STATUSES`, `VERIFICATION_STATUSES`, `VALID_CREDENTIAL_STATES = ("VALID","EXPIRING_SOON")`. `WorkforceMixin` methods: `has_worker(id)`, `register_worker(display_name, worker_type="EMPLOYEE", organization="Warehouse Operations", role_codes=(), site_codes=(), *, worker_id=None, supervisor_id=None, shift_start_hour=None, shift_end_hour=None, employment_status="ACTIVE", actor=None, **extra) -> change` (`aggregate_id` = worker id; any `extra` key → `ValueError` naming it), `update_worker(worker_id, reason, *, role_codes=None, site_codes=None, organization=None, supervisor_id=<unset>, actor=None, **extra)`, `set_employment_status(worker_id, status, reason, actor=None)`, `list_credential_definitions()`, `ensure_credential_definition(code, name=None, renewal_period_days=365) -> row`, `issue_credential(worker_id, code, issuer="Site Training Office", *, effective_from=None, expires_at=None, equipment_scope=(), task_scope=(), site_scope=(), supervision_requirement=None, verification_status="SOURCE_VERIFIED", credential_number=None, actor=None) -> change` (`subject_id` = credential id), `renew_credential(credential_id, expires_at=None, actor=None)`, `verify_credential(credential_id, verification_status, actor=None)`, `revoke_credential(credential_id, reason, actor=None)`, `record_training(worker_id, course_code, course_version="1", completed_at=None, expires_at=None, actor=None)`, `get_worker(worker_id) -> record` (worker row + `credentials` [row + `name`, `validity`] + `training` [row + `validity`] + `valid_credential_codes`), `list_workers(site=None, status=None) -> [summary]`, `worker_history(worker_id, limit=200)`, `valid_credential_codes(worker_id) -> [code]` (issuance order, de-duplicated). Credential validity values: `VALID`, `EXPIRING_SOON`, `EXPIRED`, `REVOKED`, `NOT_YET_EFFECTIVE`, `WORKER_INACTIVE`; training: `VALID`, `EXPIRING_SOON`, `EXPIRED`.

- [ ] **Step 1: Write the failing tests** — create `backend/test_inventory_workforce.py`:

```python
"""Workforce: privacy allowlist, credentials, training, validity."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from backend.inventory import Conflict, NotFound, open_inventory

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, **kwargs):
        self.now += timedelta(**kwargs)


@pytest.fixture
def clock():
    return Clock(NOW)


@pytest.fixture
def inv(clock):
    return open_inventory(":memory:", demo=False, clock=clock)


def credential(inv, worker_id, credential_id):
    return next(c for c in inv.get_worker(worker_id)["credentials"] if c["credential_id"] == credential_id)


def test_register_worker_enforces_the_privacy_allowlist(inv):
    change = inv.register_worker("Sam", role_codes=["WAREHOUSE_OPERATOR"], site_codes=["WH-01"])
    assert change["aggregate_id"] == "E-10001" and change["action"] == "WORKER_REGISTERED"
    with pytest.raises(ValueError, match="salary"):
        inv.register_worker("Pat", salary=50000)
    with pytest.raises(ValueError, match="date_of_birth"):
        inv.register_worker("Pat", date_of_birth="1990-01-01")
    assert inv.store.count("worker") == 1
    columns = {row["name"] for row in inv.store.conn.execute("PRAGMA table_info(worker)")}
    assert not columns & {"salary", "compensation", "performance_rating", "health", "date_of_birth",
                          "gender", "ethnicity", "disciplinary"}
    with pytest.raises(ValueError):
        inv.register_worker("Pat", worker_type="ROBOT")
    with pytest.raises(ValueError):
        inv.register_worker(" ")
    with pytest.raises(ValueError):
        inv.register_worker("Pat", shift_start_hour=25)
    with pytest.raises(Conflict):
        inv.register_worker("Sam again", worker_id="E-10001")


def test_credential_lifecycle_and_validity(inv, clock):
    worker = inv.register_worker("Kai", worker_type="CONTRACTOR", organization="Northstar Staffing")["aggregate_id"]
    issued = inv.issue_credential(worker, "forklift_operator", "Northstar Training",
                                  expires_at=NOW + timedelta(days=40), equipment_scope=["NW-PF1200"],
                                  credential_number="FL-99812")
    assert issued["action"] == "CREDENTIAL_ISSUED"
    cred_id = issued["subject_id"]
    cred = credential(inv, worker, cred_id)
    assert cred["validity"] == "VALID" and cred["equipment_scope"] == ["NW-PF1200"]
    assert cred["name"] == "Forklift Operator"
    assert cred["credential_number_hash"].startswith("sha256:")
    assert "FL-99812" not in json.dumps(inv.get_worker(worker))
    assert inv.valid_credential_codes(worker) == ["forklift_operator"]
    clock.advance(days=11)
    assert credential(inv, worker, cred_id)["validity"] == "EXPIRING_SOON"  # 29 days left
    inv.renew_credential(cred_id, expires_at=NOW + timedelta(days=400))
    assert credential(inv, worker, cred_id)["validity"] == "VALID"
    with pytest.raises(ValueError):
        inv.renew_credential(cred_id, expires_at=NOW)  # must extend
    inv.verify_credential(cred_id, "issuer_verified")
    assert credential(inv, worker, cred_id)["verification_status"] == "ISSUER_VERIFIED"
    revoked = inv.revoke_credential(cred_id, "Failed practical assessment")
    assert revoked["action"] == "CREDENTIAL_REVOKED"
    assert credential(inv, worker, cred_id)["validity"] == "REVOKED"
    assert inv.valid_credential_codes(worker) == []
    with pytest.raises(Conflict):
        inv.revoke_credential(cred_id, "again")
    with pytest.raises(Conflict):
        inv.renew_credential(cred_id)
    with pytest.raises(Conflict):
        inv.verify_credential(cred_id, "SOURCE_VERIFIED")


def test_credential_defaults_and_validation(inv):
    worker = inv.register_worker("Lee")["aggregate_id"]
    cred_id = inv.issue_credential(worker, "safety_inspection")["subject_id"]
    cred = credential(inv, worker, cred_id)
    assert cred["effective_from"] == "2026-09-30T12:00:00Z"
    assert cred["expires_at"] == "2027-09-30T12:00:00Z"  # 365-day renewal period
    assert cred["verification_status"] == "SOURCE_VERIFIED" and cred["issuer"] == "Site Training Office"
    with pytest.raises(ValueError):
        inv.issue_credential(worker, "juggling")
    with pytest.raises(ValueError):
        inv.issue_credential(worker, "safety_inspection", verification_status="REVOKED")
    with pytest.raises(ValueError):
        inv.issue_credential(worker, "safety_inspection", effective_from="2026-10-01", expires_at="2026-09-01")
    future = inv.issue_credential(worker, "electrical_safety", effective_from=NOW + timedelta(days=3))["subject_id"]
    assert credential(inv, worker, future)["validity"] == "NOT_YET_EFFECTIVE"
    assert inv.valid_credential_codes(worker) == ["safety_inspection"]
    inv.ensure_credential_definition("crane_rigging")
    assert inv.ensure_credential_definition("crane_rigging")["name"] == "Crane Rigging"  # idempotent
    inv.issue_credential(worker, "crane_rigging")
    assert inv.valid_credential_codes(worker) == ["safety_inspection", "crane_rigging"]


def test_employment_status_gates_validity(inv):
    worker = inv.register_worker("Sasha")["aggregate_id"]
    cred_id = inv.issue_credential(worker, "forklift_operator")["subject_id"]
    change = inv.set_employment_status(worker, "on_leave", "Leave")
    assert change["action"] == "EMPLOYMENT_STATUS_CHANGED"
    assert credential(inv, worker, cred_id)["validity"] == "WORKER_INACTIVE"
    assert inv.valid_credential_codes(worker) == []
    with pytest.raises(Conflict):
        inv.set_employment_status(worker, "ON_LEAVE", "again")
    with pytest.raises(ValueError):
        inv.set_employment_status(worker, "RETIRED", "x")
    inv.set_employment_status(worker, "TERMINATED", "Left")
    with pytest.raises(Conflict):
        inv.issue_credential(worker, "safety_inspection")


def test_update_worker_and_supervision(inv):
    boss = inv.register_worker("Ana")["aggregate_id"]
    worker = inv.register_worker("Kai", supervisor_id=boss)["aggregate_id"]
    change = inv.update_worker(worker, "Moved to WH-02", site_codes=["WH-02"], supervisor_id=None)
    assert change["diff"]["site_codes"] == {"from": [], "to": ["WH-02"]}
    assert change["diff"]["supervisor_id"] == {"from": boss, "to": None}
    with pytest.raises(ValueError, match="employment_status"):
        inv.update_worker(worker, "x", employment_status="ACTIVE")
    with pytest.raises(ValueError):
        inv.update_worker(worker, "x", site_codes=["WH-02"])  # no change
    with pytest.raises(ValueError):
        inv.update_worker(worker, "x", supervisor_id=worker)
    with pytest.raises(NotFound):
        inv.update_worker(worker, "x", supervisor_id="E-404")
    with pytest.raises(NotFound):
        inv.register_worker("Orphan", supervisor_id="E-404")


def test_training_records_and_worker_reads(inv):
    worker = inv.register_worker("Noor", site_codes=["WH-01"])["aggregate_id"]
    change = inv.record_training(worker, "LOTO-101", "3", completed_at=NOW - timedelta(days=10),
                                 expires_at=NOW + timedelta(days=20))
    assert change["action"] == "TRAINING_RECORDED" and change["subject_id"].startswith("TRN-")
    record = inv.get_worker(worker)
    assert record["training"][0]["validity"] == "EXPIRING_SOON"
    with pytest.raises(ValueError):
        inv.record_training(worker, "")
    assert [w["worker_id"] for w in inv.list_workers(site="WH-01")] == [worker]
    assert inv.list_workers(site="WH-09") == []
    assert [w["worker_id"] for w in inv.list_workers(status="active")] == [worker]
    assert [c["action"] for c in inv.worker_history(worker)] == ["TRAINING_RECORDED", "WORKER_REGISTERED"]
    assert all(c["aggregate_type"] == "WORKER" for c in inv.changes("workforce")["items"])
    codes = {d["code"] for d in inv.list_credential_definitions()}
    assert {"forklift_operator", "humanoid_supervision", "robot_cell_access"} <= codes
    with pytest.raises(NotFound):
        inv.get_worker("E-404")


def test_fleet_actions_record_the_worker_who_did_them(inv):
    tech = inv.register_worker("Noor")["aggregate_id"]
    asset = inv.commission_robot("AC-TR50", "WH-01")["aggregate_id"]
    lidar = next(c for c in inv.get_robot(asset)["components"] if c["slot"] == "lidar")
    change = inv.record_calibration(lidar["component_id"], "PASS", performed_by=tech)
    assert change["actor"] == {"type": "WORKER", "id": tech}
    lidar = next(c for c in inv.get_robot(asset)["components"] if c["slot"] == "lidar")
    assert lidar["calibrations"][0]["performed_by"] == tech
    assert inv.open_work_order(asset, "CORRECTIVE", "x", technician_id=tech)["actor"]["id"] == tech
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest backend/test_inventory_workforce.py -q`
Expected: failures with `AttributeError: ... no attribute 'register_worker'`.

- [ ] **Step 3: Implement** — create `backend/inventory/workforce.py`:

```python
"""Workforce: workers, credentials and training — the HRIS / LMS /
qualification-system view PWA's worker passports are computed from.

Only PWA's §26.1 allowlist is stored: an identity reference, status, worker
type and organisation, role and site codes, credential and training claims,
and a supervisor only when supervision needs one. Any other field passed to
register_worker / update_worker is rejected before it can be stored or
logged. Credential numbers are kept only as sha256 hashes.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any, Dict, List, Mapping, Optional

from .core import sha256_text
from .documents import iso, parse_iso, to_datetime
from .errors import Conflict

WORKER_TYPES = ("EMPLOYEE", "CONTRACTOR", "PARTNER")
EMPLOYMENT_STATUSES = ("ACTIVE", "ON_LEAVE", "TERMINATED")
VERIFICATION_STATUSES = ("UNVERIFIED", "SOURCE_VERIFIED", "ISSUER_VERIFIED")
VALID_CREDENTIAL_STATES = ("VALID", "EXPIRING_SOON")
_UNSET = object()


def _codes(values: Any) -> List[str]:
    if values is None:
        return []
    if isinstance(values, str):
        values = [values]
    return list(dict.fromkeys(str(v).strip() for v in values if str(v).strip()))


def _hour(value: Any, label: str) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        hour = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{label} must be an hour between 0 and 23") from None
    if not 0 <= hour <= 23:
        raise ValueError(f"{label} must be between 0 and 23")
    return hour


def _reject_unlisted(extra: Mapping[str, Any]) -> None:
    if extra:
        raise ValueError(f"Field(s) not allowed in a worker record: {sorted(extra)}")


class WorkforceMixin:
    # ---- lookups & documents ------------------------------------------ #
    def has_worker(self, worker_id: Optional[str]) -> bool:
        return bool(worker_id) and self.store.exists("worker", "worker_id", worker_id)

    def _worker(self, worker_id: str) -> Dict[str, Any]:
        return self._require("worker", "worker_id", worker_id, "Worker")

    def _credential(self, credential_id: str) -> Dict[str, Any]:
        return self._require("worker_credential", "credential_id", credential_id, "Credential")

    def _worker_document(self, worker_id: str) -> Dict[str, Any]:
        """The WORKER aggregate as stored in change_log `after` snapshots."""
        return {
            **self._worker(worker_id),
            "credentials": self.store.select("worker_credential", "worker_id = ?", (worker_id,)),
            "training": self.store.select("training_completion", "worker_id = ?", (worker_id,)),
        }

    def _commit_worker(self, worker_id: str, action: str, before: Optional[dict], actor: Any = None,
                       reason: Optional[str] = None, subject_id: Optional[str] = None) -> Dict[str, Any]:
        revision = self._worker(worker_id)["revision"] + 1
        self.store.update("worker", "worker_id", worker_id, {"revision": revision, "updated_at": self.now_iso()})
        return self._record(aggregate_type="WORKER", aggregate_id=worker_id, revision=revision, action=action,
                            before=before, after=self._worker_document(worker_id), actor=actor,
                            reason=reason, subject_id=subject_id)

    # ---- validity ------------------------------------------------------ #
    def _credential_validity(self, credential: Mapping[str, Any], worker: Mapping[str, Any], now) -> str:
        if credential["verification_status"] == "REVOKED":
            return "REVOKED"
        if worker["employment_status"] != "ACTIVE":
            return "WORKER_INACTIVE"
        start = parse_iso(credential["effective_from"])
        if start is not None and start > now:
            return "NOT_YET_EFFECTIVE"
        end = parse_iso(credential["expires_at"])
        if end is not None:
            if end <= now:
                return "EXPIRED"
            if end - now <= timedelta(days=self.setting("CREDENTIAL_EXPIRING_SOON_DAYS")):
                return "EXPIRING_SOON"
        return "VALID"

    def _training_validity(self, training: Mapping[str, Any], now) -> str:
        end = parse_iso(training["expires_at"])
        if end is None:
            return "VALID"
        if end <= now:
            return "EXPIRED"
        if end - now <= timedelta(days=self.setting("CREDENTIAL_EXPIRING_SOON_DAYS")):
            return "EXPIRING_SOON"
        return "VALID"

    # ---- workers ------------------------------------------------------- #
    def register_worker(self, display_name: str, worker_type: str = "EMPLOYEE",
                        organization: str = "Warehouse Operations", role_codes: Any = (), site_codes: Any = (), *,
                        worker_id: Optional[str] = None, supervisor_id: Optional[str] = None,
                        shift_start_hour: Any = None, shift_end_hour: Any = None,
                        employment_status: str = "ACTIVE", actor: Any = None, **extra: Any) -> Dict[str, Any]:
        _reject_unlisted(extra)
        name = str(display_name or "").strip()
        if not name:
            raise ValueError("display_name is required")
        worker_type = str(worker_type or "").upper()
        if worker_type not in WORKER_TYPES:
            raise ValueError(f"Unknown worker_type {worker_type!r} (known: {list(WORKER_TYPES)})")
        employment_status = str(employment_status or "").upper()
        if employment_status not in EMPLOYMENT_STATUSES:
            raise ValueError(f"Unknown employment_status {employment_status!r} (known: {list(EMPLOYMENT_STATUSES)})")
        organization = str(organization or "").strip() or "Warehouse Operations"
        start_hour = _hour(shift_start_hour, "shift_start_hour")
        end_hour = _hour(shift_end_hour, "shift_end_hour")
        with self._tx():
            if worker_id:
                if self.has_worker(worker_id):
                    raise Conflict(f"Worker '{worker_id}' already exists")
            else:
                worker_id = self.store.next_id("E-", "worker", "worker_id", 5, start=10001)
            if supervisor_id:
                self._worker(supervisor_id)
            self.store.insert("worker", {
                "worker_id": worker_id, "display_name": name, "worker_type": worker_type,
                "organization": organization, "role_codes": _codes(role_codes), "site_codes": _codes(site_codes),
                "employment_status": employment_status, "supervisor_id": supervisor_id or None,
                "shift_start_hour": start_hour, "shift_end_hour": end_hour,
                "revision": 0, "updated_at": self.now_iso(),
            })
            return self._commit_worker(worker_id, "WORKER_REGISTERED", None, actor=actor)

    def update_worker(self, worker_id: str, reason: str, *, role_codes: Any = None, site_codes: Any = None,
                      organization: Optional[str] = None, supervisor_id: Any = _UNSET, actor: Any = None,
                      **extra: Any) -> Dict[str, Any]:
        _reject_unlisted(extra)
        reason = self._reason(reason)
        with self._tx():
            worker = self._worker(worker_id)
            fields: Dict[str, Any] = {}
            if role_codes is not None:
                fields["role_codes"] = _codes(role_codes)
            if site_codes is not None:
                fields["site_codes"] = _codes(site_codes)
            if organization is not None:
                if not str(organization).strip():
                    raise ValueError("organization cannot be empty")
                fields["organization"] = str(organization).strip()
            if supervisor_id is not _UNSET:
                if supervisor_id:
                    if supervisor_id == worker_id:
                        raise ValueError("A worker cannot supervise themselves")
                    self._worker(supervisor_id)
                fields["supervisor_id"] = supervisor_id or None
            fields = {key: value for key, value in fields.items() if worker[key] != value}
            if not fields:
                raise ValueError("No changes to apply")
            before = self._worker_document(worker_id)
            self.store.update("worker", "worker_id", worker_id, fields)
            return self._commit_worker(worker_id, "WORKER_UPDATED", before, actor=actor, reason=reason)

    def set_employment_status(self, worker_id: str, status: str, reason: str, actor: Any = None) -> Dict[str, Any]:
        status = str(status or "").upper()
        if status not in EMPLOYMENT_STATUSES:
            raise ValueError(f"Unknown employment_status {status!r} (known: {list(EMPLOYMENT_STATUSES)})")
        reason = self._reason(reason)
        with self._tx():
            worker = self._worker(worker_id)
            if worker["employment_status"] == status:
                raise Conflict(f"{worker_id} is already {status}")
            before = self._worker_document(worker_id)
            self.store.update("worker", "worker_id", worker_id, {"employment_status": status})
            return self._commit_worker(worker_id, "EMPLOYMENT_STATUS_CHANGED", before, actor=actor, reason=reason)

    # ---- credentials --------------------------------------------------- #
    def list_credential_definitions(self) -> List[Dict[str, Any]]:
        return self.store.select("credential_definition", order="code")

    def ensure_credential_definition(self, code: str, name: Optional[str] = None,
                                     renewal_period_days: Optional[int] = 365) -> Dict[str, Any]:
        code = str(code or "").strip()
        if not code:
            raise ValueError("A credential code is required")
        with self._tx():
            existing = self.store.get("credential_definition", "code", code)
            if existing is not None:
                return existing
            row = {
                "code": code, "name": name or code.replace("_", " ").title(), "issuer_types": ["INTERNAL"],
                "renewal_period_days": renewal_period_days, "scope_dimensions": ["site"],
            }
            self.store.insert("credential_definition", row)
            return row

    def issue_credential(self, worker_id: str, code: str, issuer: str = "Site Training Office", *,
                         effective_from: Any = None, expires_at: Any = None, equipment_scope: Any = (),
                         task_scope: Any = (), site_scope: Any = (), supervision_requirement: Optional[str] = None,
                         verification_status: str = "SOURCE_VERIFIED", credential_number: Optional[str] = None,
                         actor: Any = None) -> Dict[str, Any]:
        verification_status = str(verification_status or "").upper()
        if verification_status not in VERIFICATION_STATUSES:
            raise ValueError(f"verification_status must be one of {list(VERIFICATION_STATUSES)} "
                             "(use revoke_credential to revoke)")
        issuer = str(issuer or "").strip()
        if not issuer:
            raise ValueError("issuer is required")
        with self._tx():
            worker = self._worker(worker_id)
            if worker["employment_status"] == "TERMINATED":
                raise Conflict(f"{worker_id} is terminated")
            definition = self.store.get("credential_definition", "code", code)
            if definition is None:
                raise ValueError(f"Unknown credential code {code!r}")
            start = to_datetime(effective_from) or self.now()
            end = to_datetime(expires_at)
            if end is None and definition["renewal_period_days"]:
                end = start + timedelta(days=definition["renewal_period_days"])
            if end is not None and end <= start:
                raise ValueError("expires_at must be after effective_from")
            before = self._worker_document(worker_id)
            credential_id = self.store.next_id("CRD-", "worker_credential", "credential_id", 6)
            number = credential_number or f"{code.upper()}-{worker_id}-{credential_id}"
            self.store.insert("worker_credential", {
                "credential_id": credential_id, "worker_id": worker_id, "code": code, "issuer": issuer,
                "credential_number_hash": sha256_text(str(number)), "verification_status": verification_status,
                "effective_from": iso(start), "expires_at": iso(end),
                "equipment_scope": _codes(equipment_scope), "task_scope": _codes(task_scope),
                "site_scope": _codes(site_scope), "supervision_requirement": supervision_requirement,
                "revoked_at": None, "revocation_reason": None,
            })
            return self._commit_worker(worker_id, "CREDENTIAL_ISSUED", before, actor=actor, subject_id=credential_id)

    def renew_credential(self, credential_id: str, expires_at: Any = None, actor: Any = None) -> Dict[str, Any]:
        with self._tx():
            credential = self._credential(credential_id)
            if credential["verification_status"] == "REVOKED":
                raise Conflict(f"{credential_id} is revoked — issue a new credential instead")
            definition = self.store.get("credential_definition", "code", credential["code"])
            new_end = to_datetime(expires_at)
            if new_end is None:
                if not definition or not definition["renewal_period_days"]:
                    raise ValueError("expires_at is required for this credential")
                new_end = self.now() + timedelta(days=definition["renewal_period_days"])
            current_end = parse_iso(credential["expires_at"])
            if current_end is not None and new_end <= current_end:
                raise ValueError("A renewal must extend the current expiry")
            before = self._worker_document(credential["worker_id"])
            self.store.update("worker_credential", "credential_id", credential_id, {"expires_at": iso(new_end)})
            return self._commit_worker(credential["worker_id"], "CREDENTIAL_RENEWED", before, actor=actor,
                                       subject_id=credential_id)

    def verify_credential(self, credential_id: str, verification_status: str, actor: Any = None) -> Dict[str, Any]:
        status = str(verification_status or "").upper()
        if status not in VERIFICATION_STATUSES:
            raise ValueError(f"verification_status must be one of {list(VERIFICATION_STATUSES)}")
        with self._tx():
            credential = self._credential(credential_id)
            if credential["verification_status"] == "REVOKED":
                raise Conflict(f"{credential_id} is revoked")
            if credential["verification_status"] == status:
                raise Conflict(f"{credential_id} is already {status}")
            before = self._worker_document(credential["worker_id"])
            self.store.update("worker_credential", "credential_id", credential_id, {"verification_status": status})
            return self._commit_worker(credential["worker_id"], "CREDENTIAL_VERIFIED", before, actor=actor,
                                       subject_id=credential_id)

    def revoke_credential(self, credential_id: str, reason: str, actor: Any = None) -> Dict[str, Any]:
        reason = self._reason(reason)
        with self._tx():
            credential = self._credential(credential_id)
            if credential["verification_status"] == "REVOKED":
                raise Conflict(f"{credential_id} is already revoked")
            before = self._worker_document(credential["worker_id"])
            self.store.update("worker_credential", "credential_id", credential_id, {
                "verification_status": "REVOKED", "revoked_at": self.now_iso(), "revocation_reason": reason,
            })
            return self._commit_worker(credential["worker_id"], "CREDENTIAL_REVOKED", before, actor=actor,
                                       reason=reason, subject_id=credential_id)

    # ---- training ------------------------------------------------------ #
    def record_training(self, worker_id: str, course_code: str, course_version: str = "1",
                        completed_at: Any = None, expires_at: Any = None, actor: Any = None) -> Dict[str, Any]:
        course_code = str(course_code or "").strip()
        if not course_code:
            raise ValueError("course_code is required")
        with self._tx():
            self._worker(worker_id)
            completed = to_datetime(completed_at) or self.now()
            end = to_datetime(expires_at)
            if end is not None and end <= completed:
                raise ValueError("expires_at must be after completed_at")
            before = self._worker_document(worker_id)
            training_id = self.store.next_id("TRN-", "training_completion", "training_id", 6)
            self.store.insert("training_completion", {
                "training_id": training_id, "worker_id": worker_id, "course_code": course_code,
                "course_version": str(course_version or "1"), "completed_at": iso(completed), "expires_at": iso(end),
            })
            return self._commit_worker(worker_id, "TRAINING_RECORDED", before, actor=actor, subject_id=training_id)

    # ---- reads --------------------------------------------------------- #
    def get_worker(self, worker_id: str) -> Dict[str, Any]:
        record = self._worker_document(worker_id)
        now = self.now()
        names = {d["code"]: d["name"] for d in self.list_credential_definitions()}
        record["credentials"] = [
            {**c, "name": names.get(c["code"], c["code"]), "validity": self._credential_validity(c, record, now)}
            for c in record["credentials"]
        ]
        record["training"] = [{**t, "validity": self._training_validity(t, now)} for t in record["training"]]
        record["valid_credential_codes"] = list(dict.fromkeys(
            c["code"] for c in record["credentials"] if c["validity"] in VALID_CREDENTIAL_STATES))
        return record

    @staticmethod
    def _worker_summary(record: Mapping[str, Any]) -> Dict[str, Any]:
        return {
            "worker_id": record["worker_id"], "display_name": record["display_name"],
            "worker_type": record["worker_type"], "organization": record["organization"],
            "role_codes": record["role_codes"], "site_codes": record["site_codes"],
            "employment_status": record["employment_status"], "supervisor_id": record["supervisor_id"],
            "revision": record["revision"], "valid_credential_codes": record["valid_credential_codes"],
            "credentials": [{"credential_id": c["credential_id"], "code": c["code"], "validity": c["validity"]}
                            for c in record["credentials"]],
        }

    def list_workers(self, site: Optional[str] = None, status: Optional[str] = None) -> List[Dict[str, Any]]:
        if status:
            rows = self.store.select("worker", "employment_status = ?", (str(status).upper(),), order="worker_id")
        else:
            rows = self.store.select("worker", order="worker_id")
        if site:
            rows = [row for row in rows if site in row["site_codes"]]
        return [self._worker_summary(self.get_worker(row["worker_id"])) for row in rows]

    def worker_history(self, worker_id: str, limit: int = 200) -> List[Dict[str, Any]]:
        self._worker(worker_id)
        return self.history([worker_id], limit)

    def valid_credential_codes(self, worker_id: str) -> List[str]:
        return self.get_worker(worker_id)["valid_credential_codes"]
```

Modify `backend/inventory/service.py`:

```python
from .catalog import CatalogMixin
from .core import ServiceCore
from .fleet import FleetMixin
from .ota import OtaMixin
from .servicing import ServicingMixin
from .workforce import WorkforceMixin


class InventoryService(OtaMixin, ServicingMixin, FleetMixin, CatalogMixin, WorkforceMixin, ServiceCore):
    pass
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest backend/test_inventory_workforce.py backend/test_inventory_servicing.py backend/test_inventory_ota.py -q`
Expected: `17 passed`.

- [ ] **Step 5: Commit**

```bash
git add backend/inventory/workforce.py backend/inventory/service.py backend/test_inventory_workforce.py
git commit -m "feat(inventory): workforce registry with privacy allowlist and credential validity

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 8: Demo fleet and workforce seed

**Files:**
- Create: `backend/inventory/demo_seed.py`
- Test: `backend/test_inventory_seed.py`

**Interfaces:**
- Consumes: every service action from Tasks 3–7, `service.at()`, `service.transaction()`, `SYSTEM`.
- Produces: `demo_seed.seed_demo(service)`, `FLOOR_SITE = "WH-01"`, `REMOTE_SITE = "WH-02"`. After seeding: assets `AST-000101`/`AST-000102` (floor TR-50s on software `2.1.0`), `AST-000103` (TR-50 in MAINTENANCE, new lidar uncalibrated), `AST-000201`…`AST-000213` at WH-02 (scenarios below); workers `E-10001` Sam (`safety_inspection`, `electrical_safety`), `E-10002` Lee (`safety_inspection`), `E-10003` Noor Haddad (maintenance tech) … `E-10010`. `open_inventory(demo=True)` now works.

Seeded WH-02 scenarios: 201 clean TR-50 · 202 reports recalled `2.2.1` (declared `2.2.0`) · 203 PK-30 lidar calibration EXPIRED · 204 SC-1 lidar/IMU DUE_SOON · 205 PF-1200 rev A on `2.1.1` (cannot take `2.2.0`) · 206 PF-1200 OTA job REPORTED awaiting verification · 207 HH-300 OFFLINE, stale · 208 IX-2 drone battery SoH 71 % · 209 clean IX-2 · 210 clean CX-10 arm · 211 CX-10 force-torque firmware mismatch · 212 clean H-1 humanoid · 213 TR-50 DECOMMISSIONED.

- [ ] **Step 1: Write the failing tests** — create `backend/test_inventory_seed.py`:

```python
"""The demo inventory: every seeded scenario shows its flag; seeding is deterministic."""
import time
from datetime import datetime, timezone

import pytest

from backend.inventory import open_inventory

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)
CLEAN = ("AST-000101", "AST-000102", "AST-000201", "AST-000209", "AST-000210", "AST-000212")


@pytest.fixture(scope="module")
def demo():
    return open_inventory(":memory:", demo=True, clock=lambda: NOW)


def test_demo_fleet_matches_the_spec(demo):
    assert [r["asset_id"] for r in demo.list_robots(site="WH-01")] == ["AST-000101", "AST-000102", "AST-000103"]
    assert len(demo.list_robots(site="WH-02")) == 13
    floor = demo.get_robot("AST-000101")
    assert floor["reported"]["software_version"] == "2.1.0"
    assert floor["declared_software_version"] == "2.1.0"
    assert floor["reported"]["battery"]["cycle_count"] == 212


def test_each_seeded_scenario_shows_its_flag(demo):
    flags = {r["asset_id"]: r["flags"] for r in demo.list_robots()}
    assert demo.get_robot("AST-000103")["lifecycle_status"] == "MAINTENANCE"
    assert flags["AST-000103"]["calibration_worst"] == "MISSING"
    assert flags["AST-000202"]["software_mismatch"] and flags["AST-000202"]["running_recalled_release"]
    assert flags["AST-000203"]["calibration_worst"] == "EXPIRED"
    assert flags["AST-000204"]["calibration_worst"] == "DUE_SOON"
    assert demo.get_robot("AST-000205")["hw_revision"] == "A"
    assert flags["AST-000206"]["active_ota_job"]["state"] == "REPORTED"
    assert flags["AST-000206"]["software_mismatch"]
    assert flags["AST-000207"]["report_stale"]
    assert demo.get_robot("AST-000208")["reported"]["battery"]["soh_pct"] == 71.0
    assert [m["slot"] for m in flags["AST-000211"]["component_firmware_mismatches"]] == ["force_torque"]
    assert demo.get_robot("AST-000213")["lifecycle_status"] == "DECOMMISSIONED"
    for asset in CLEAN:
        f = flags[asset]
        assert not f["software_mismatch"], asset
        assert not f["component_firmware_mismatches"], asset
        assert f["calibration_worst"] == "VALID", asset
        assert not f["report_stale"], asset
        assert f["active_ota_job"] is None, asset


def test_demo_workforce(demo):
    assert demo.valid_credential_codes("E-10001") == ["safety_inspection", "electrical_safety"]
    assert demo.valid_credential_codes("E-10002") == ["safety_inspection"]
    kai = demo.get_worker("E-10007")
    assert kai["worker_type"] == "CONTRACTOR" and kai["supervisor_id"] == "E-10005"
    assert [c["validity"] for c in kai["credentials"]] == ["EXPIRING_SOON"]
    assert "REVOKED" in [c["validity"] for c in demo.get_worker("E-10008")["credentials"]]
    assert demo.get_worker("E-10009")["employment_status"] == "ON_LEAVE"
    assert demo.get_worker("E-10010")["employment_status"] == "TERMINATED"
    assert demo.get_worker("E-10003")["training"][0]["course_code"] == "LOTO-101"
    assert len(demo.list_workers()) == 10


def test_seeded_history_is_backdated(demo):
    commissioned = demo.robot_history("AST-000213")[-1]
    assert commissioned["action"] == "COMMISSIONED"
    assert commissioned["occurred_at"].startswith("2024-10-")  # 700 days before NOW


def test_seed_is_deterministic():
    a = open_inventory(":memory:", demo=True, clock=lambda: NOW)
    b = open_inventory(":memory:", demo=True, clock=lambda: NOW)
    assert a.get_robot("AST-000206") == b.get_robot("AST-000206")
    assert a.get_worker("E-10007") == b.get_worker("E-10007")
    assert [c["action"] for c in a.changes("fleet", limit=500)["items"]] == \
        [c["action"] for c in b.changes("fleet", limit=500)["items"]]


def test_template_copies_are_fast_and_independent():
    open_inventory()  # warm the template
    started = time.perf_counter()
    a, b = open_inventory(), open_inventory()
    assert time.perf_counter() - started < 0.5
    assert a.store.epoch() != b.store.epoch()
    a.decommission("AST-000201", "test")
    assert b.get_robot("AST-000201")["lifecycle_status"] == "IN_SERVICE"
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest backend/test_inventory_seed.py -q`
Expected: errors with `ModuleNotFoundError: No module named 'backend.inventory.demo_seed'`.

- [ ] **Step 3: Implement** — create `backend/inventory/demo_seed.py`:

```python
"""Demo fleet and workforce for the inventory (spec §6).

Everything is created through InventoryService actions, backdated with
service.at(), so the seeded change_log history has exactly the shape real
use produces. Deterministic: fixed ids, counters and relative dates.
WH-01 is the simulated floor; WH-02 is another site that exists only in the
inventory, seeded so every trust-relevant flag shows up somewhere.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Callable, Optional, Sequence

from .core import SYSTEM

FLOOR_SITE = "WH-01"
REMOTE_SITE = "WH-02"


def seed_demo(service) -> None:
    now = service.now()

    def ago(days: float) -> datetime:
        return now - timedelta(days=days)

    with service.transaction():
        _seed_workers(service, ago)
        _seed_fleet(service, ago)
        with service.at(now):
            service.touch_remote_reports()


def _seed_workers(service, ago: Callable[[float], datetime]) -> None:
    def hire(worker_id: str, name: str, roles: Sequence[str], sites: Sequence[str], *, days: float,
             worker_type: str = "EMPLOYEE", organization: str = "Warehouse Operations",
             supervisor: Optional[str] = None) -> None:
        with service.at(ago(days)):
            service.register_worker(name, worker_type, organization, roles, sites, worker_id=worker_id,
                                    supervisor_id=supervisor, actor=SYSTEM)

    def credential(worker_id: str, code: str, *, days: float, valid_days: int,
                   issuer: str = "Site Training Office", **scopes: Any) -> str:
        with service.at(ago(days)):
            start = service.now()
            return service.issue_credential(worker_id, code, issuer, effective_from=start,
                                            expires_at=start + timedelta(days=valid_days),
                                            actor=SYSTEM, **scopes)["subject_id"]

    hire("E-10001", "Sam", ["WAREHOUSE_OPERATOR", "SAFETY_INSPECTOR"], [FLOOR_SITE], days=900)
    credential("E-10001", "safety_inspection", days=200, valid_days=365, site_scope=[FLOOR_SITE])
    credential("E-10001", "electrical_safety", days=100, valid_days=365, site_scope=[FLOOR_SITE])
    hire("E-10002", "Lee", ["WAREHOUSE_OPERATOR"], [FLOOR_SITE], days=400)
    credential("E-10002", "safety_inspection", days=60, valid_days=365, site_scope=[FLOOR_SITE])
    hire("E-10003", "Noor Haddad", ["MAINTENANCE_TECH"], [FLOOR_SITE, REMOTE_SITE], days=1200)
    credential("E-10003", "robot_maintenance", days=300, valid_days=730, equipment_scope=["AC-TR50", "AC-PK30", "AC-SC1"])
    credential("E-10003", "electrical_safety", days=120, valid_days=365)
    credential("E-10003", "equipment_maintenance", days=500, valid_days=730)
    service.record_training("E-10003", "LOTO-101", "3", completed_at=ago(90),
                            expires_at=ago(90) + timedelta(days=365), actor=SYSTEM)
    hire("E-10004", "Mateo Silva", ["ROBOTICS_TECH"], [REMOTE_SITE], days=700)
    credential("E-10004", "robot_maintenance", days=200, valid_days=730)
    credential("E-10004", "robot_cell_access", days=150, valid_days=365, equipment_scope=["FB-CX10"])
    credential("E-10004", "drone_operations", days=250, valid_days=730, equipment_scope=["CT-IX2"])
    hire("E-10005", "Ana Kowalski", ["FORKLIFT_OPERATOR"], [REMOTE_SITE], days=1500)
    credential("E-10005", "forklift_operator", days=400, valid_days=1095, equipment_scope=["NW-PF1200"])
    credential("E-10005", "heavy_equipment", days=400, valid_days=1095)
    hire("E-10006", "Jordan Blake", ["HUMANOID_SUPERVISOR"], [REMOTE_SITE], days=300)
    credential("E-10006", "humanoid_supervision", days=90, valid_days=365, equipment_scope=["TS-H1"],
               site_scope=[REMOTE_SITE])
    credential("E-10006", "safety_inspection", days=90, valid_days=365)
    hire("E-10007", "Kai Nakamura", ["FORKLIFT_OPERATOR"], [REMOTE_SITE], days=358, worker_type="CONTRACTOR",
         organization="Northstar Staffing", supervisor="E-10005")
    credential("E-10007", "forklift_operator", days=358, valid_days=365, issuer="Northstar Staffing Training",
               equipment_scope=["NW-PF1200"])  # expires in 7 days
    hire("E-10008", "Riley Chen", ["ROBOT_CELL_OPERATOR"], [REMOTE_SITE], days=500)
    revoked = credential("E-10008", "robot_cell_access", days=200, valid_days=365, equipment_scope=["FB-CX10"])
    with service.at(ago(10)):
        service.revoke_credential(revoked, "Failed recertification audit", actor=SYSTEM)
    credential("E-10008", "safety_inspection", days=100, valid_days=365)
    hire("E-10009", "Sasha Ivanova", ["FORKLIFT_OPERATOR"], [REMOTE_SITE], days=800)
    credential("E-10009", "forklift_operator", days=300, valid_days=1095, equipment_scope=["NW-PF1200"])
    with service.at(ago(14)):
        service.set_employment_status("E-10009", "ON_LEAVE", "Parental leave", actor=SYSTEM)
    hire("E-10010", "Morgan Patel", ["WAREHOUSE_OPERATOR"], [FLOOR_SITE], days=600)
    credential("E-10010", "safety_inspection", days=250, valid_days=365)
    with service.at(ago(45)):
        service.set_employment_status("E-10010", "TERMINATED", "Left the company", actor=SYSTEM)


def _seed_fleet(service, ago: Callable[[float], datetime]) -> None:
    def commission(asset_id: str, model: str, site: str, zone: str, *, days: float,
                   hw: Optional[str] = None, version: Optional[str] = None) -> None:
        with service.at(ago(days)):
            service.commission_robot(model, site, zone, asset_id=asset_id, hw_revision=hw,
                                     software_release_id=f"{model}:SW:{version}" if version else None,
                                     fleet_id=f"{site}-FLEET", actor=SYSTEM)

    def recalibrate(asset_id: str, *, days: float, by: str, skip_types: Sequence[str] = ()) -> None:
        with service.at(ago(days)):
            for component in service.get_robot(asset_id)["components"]:
                if component["calibration_interval_days"] and component["component_type"] not in skip_types:
                    service.record_calibration(component["component_id"], "PASS", performed_by=by)

    def report(asset_id: str, days: float = 0, **fields: Any) -> None:
        with service.at(ago(days)):
            service.report_state(asset_id, fields, actor=SYSTEM)

    # ---- WH-01: the simulated floor ------------------------------------ #
    commission("AST-000101", "AC-TR50", FLOOR_SITE, "parking_area", days=380, version="2.1.0")
    recalibrate("AST-000101", days=20, by="E-10003")
    report("AST-000101", battery={"soh_pct": 96.4, "cycle_count": 212})
    commission("AST-000102", "AC-TR50", FLOOR_SITE, "parking_area", days=380, version="2.1.0")
    recalibrate("AST-000102", days=20, by="E-10003")
    report("AST-000102", battery={"soh_pct": 95.1, "cycle_count": 240})
    commission("AST-000103", "AC-TR50", FLOOR_SITE, "charging_station", days=500, hw="C", version="2.2.0")
    recalibrate("AST-000103", days=30, by="E-10003")
    with service.at(ago(1)):
        wo = service.open_work_order("AST-000103", "CORRECTIVE",
                                     "Lidar returns degraded on the east aisle - replace unit",
                                     technician_id="E-10003")["subject_id"]
        service.swap_component(wo, "lidar", performed_by="E-10003")

    # ---- WH-02: inventory-only site, one scenario per asset ------------ #
    commission("AST-000201", "AC-TR50", REMOTE_SITE, "tote_aisle_1", days=90)
    commission("AST-000202", "AC-TR50", REMOTE_SITE, "tote_aisle_2", days=100)
    report("AST-000202", days=3, software_version="2.2.1", os_version=service.os_version_for("2.2.1"))
    commission("AST-000203", "AC-PK30", REMOTE_SITE, "pick_station_1", days=400)
    recalibrate("AST-000203", days=60, by="E-10004", skip_types=("LIDAR",))
    commission("AST-000204", "AC-SC1", REMOTE_SITE, "patrol_loop", days=356)
    recalibrate("AST-000204", days=60, by="E-10004", skip_types=("LIDAR", "IMU"))
    commission("AST-000205", "NW-PF1200", REMOTE_SITE, "pallet_aisle", days=300, hw="A", version="2.1.1")
    recalibrate("AST-000205", days=30, by="E-10004")
    commission("AST-000206", "NW-PF1200", REMOTE_SITE, "pallet_aisle", days=200, hw="B", version="2.1.1")
    recalibrate("AST-000206", days=20, by="E-10004")
    with service.at(ago(1)):
        job = service.start_ota("AST-000206", "NW-PF1200:SW:2.2.0",
                                actor={"type": "WORKER", "id": "E-10004"})["subject_id"]
        service.transition_ota(job, "DOWNLOADING")
        service.transition_ota(job, "INSTALLING")
        service.report_state("AST-000206", {"software_version": "2.2.0",
                                            "os_version": service.os_version_for("2.2.0")}, actor=SYSTEM)
        service.transition_ota(job, "REPORTED")
    commission("AST-000207", "NW-HH300", REMOTE_SITE, "dock_2", days=250)
    recalibrate("AST-000207", days=40, by="E-10004")
    report("AST-000207", days=2, connectivity="OFFLINE", health_state="UNKNOWN")
    commission("AST-000208", "CT-IX2", REMOTE_SITE, "drone_pad", days=150)
    recalibrate("AST-000208", days=30, by="E-10004")
    report("AST-000208", battery={"soh_pct": 71.0, "cycle_count": 612})
    commission("AST-000209", "CT-IX2", REMOTE_SITE, "drone_pad", days=60)
    commission("AST-000210", "FB-CX10", REMOTE_SITE, "pack_cell_1", days=120)
    recalibrate("AST-000210", days=15, by="E-10004")
    commission("AST-000211", "FB-CX10", REMOTE_SITE, "pack_cell_2", days=120)
    recalibrate("AST-000211", days=15, by="E-10004")
    firmware = dict(service.get_robot("AST-000211")["reported"]["component_firmware"])
    report("AST-000211", days=5, component_firmware={**firmware, "force_torque": "3.2.0"})
    commission("AST-000212", "TS-H1", REMOTE_SITE, "tote_aisle_1", days=45)
    commission("AST-000213", "AC-TR50", REMOTE_SITE, "tote_aisle_2", days=700, version="2.1.0")
    with service.at(ago(30)):
        service.decommission("AST-000213", "Chassis damage - written off", actor=SYSTEM)
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest backend/test_inventory_seed.py -q`
Expected: `6 passed`.

Then run every inventory test: `.venv/bin/python -m pytest backend/test_inventory_store.py backend/test_inventory_core.py backend/test_inventory_catalog.py backend/test_inventory_fleet.py backend/test_inventory_servicing.py backend/test_inventory_ota.py backend/test_inventory_workforce.py backend/test_inventory_seed.py -q`
Expected: `60 passed`.

- [ ] **Step 5: Commit**

```bash
git add backend/inventory/demo_seed.py backend/test_inventory_seed.py
git commit -m "feat(inventory): demo fleet and workforce with one scenario per trust flag

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Bind the twin's robots and operators to the inventory

**Files:**
- Create: `backend/fleet_bridge.py`
- Modify: `backend/models.py` (CONFIG keys, `LogCategory.FLEET`, three `EventType`s), `backend/robot.py`, `backend/operator.py`, `backend/digital_twin.py`, `backend/app.py:83-86`, `.gitignore`
- Test: `backend/test_fleet_bridge.py`

**Interfaces:**
- Consumes: `open_inventory`, `reseed`, `InventoryService`, `NotFound`, `CLASS_DEFAULT_MODELS`, `SYSTEM`.
- Produces: `DigitalTwin(..., inventory_path=":memory:")` with `twin.inventory: InventoryService` and `twin.fleet: FleetBridge`; `DigitalTwin.add_robot(..., model_code=None, asset_id=None)`; `DigitalTwin.add_operator(..., worker_id=None)`; `Robot.asset_id`, `Robot.ai_policy_version`, `Robot.component_firmware: Dict[str, str]`, `Robot.ota_installing: bool`, `Robot.fleet_hold: Optional[str]` (all in `to_dict`); `Operator.worker_id`. `FleetBridge(twin, service)` with `mutate(action, *args, **kwargs)`, `reseed(demo=True)`, `robot_for_asset(asset_id) -> Robot|None`, `bind_new_robot(robot, model_code=None, asset_id=None, adopt=True)`, `bind_new_operator(operator, worker_id=None)`, `rebind_all()`, `_sync_operator(operator, announce=True) -> bool`, `_on_change(change)`. CONFIG keys `FLEET_HEARTBEAT_EVERY_TICKS`, `OTA_DOWNLOAD_TICKS`, `OTA_INSTALL_TICKS`, `OTA_FAILURE_RISK`, `CALIBRATION_DUE_SOON_DAYS`, `CREDENTIAL_EXPIRING_SOON_DAYS`, `REPORT_STALE_SECONDS`; `EventType.INVENTORY_CHANGED`, `EventType.OTA_JOB_UPDATED`, `EventType.OPERATOR_CERTIFICATIONS_CHANGED`; `LogCategory.FLEET`.

- [ ] **Step 1: Write the failing tests** — create `backend/test_fleet_bridge.py`:

```python
"""FleetBridge: binding live robots/operators to the inventory, and the
runtime effects of inventory actions on the simulation."""
import pytest

from backend.digital_twin import DigitalTwin
from backend.models import RobotStatus, TaskStatus


def make_twin(tmp_path, **kwargs):
    return DigitalTwin(log_dir=str(tmp_path / "logs"), data_dir=str(tmp_path / "data"),
                       persist_logs=False, demo=True, demo_tasks=False, **kwargs)


@pytest.fixture
def twin(tmp_path):
    return make_twin(tmp_path)


def test_demo_robots_and_operators_bind_to_their_inventory_records(twin):
    robo1 = twin.find_robot("Robo-01")
    assert robo1.asset_id == "AST-000101" and twin.find_robot("Robo-02").asset_id == "AST-000102"
    assert robo1.firmware_version == "2.1.0"  # unchanged from before the inventory existed
    assert robo1.component_firmware["lidar"] == "2.0.1"
    assert robo1.status == RobotStatus.IDLE and robo1.fleet_hold is None
    sam, lee = twin.find_operator("Sam"), twin.find_operator("Lee")
    assert sam.worker_id == "E-10001" and sam.certifications == ["safety_inspection", "electrical_safety"]
    assert lee.worker_id == "E-10002" and lee.certifications == ["safety_inspection"]
    assert robo1.to_dict()["asset_id"] == "AST-000101" and sam.to_dict()["worker_id"] == "E-10001"


def test_add_robot_commissions_an_asset_for_its_class(twin):
    robot = twin.add_robot(name="Lifter", robot_class="FORKLIFT")
    record = twin.inventory.get_robot(robot.asset_id)
    assert record["model_code"] == "NW-PF1200" and record["site_code"] == "WH-01"
    assert robot.firmware_version == "2.1.0" == record["reported"]["software_version"]
    assert twin.fleet.robot_for_asset(robot.asset_id) is robot
    drone = twin.add_robot(name="Drone-1", model_code="CT-IX2")
    assert drone.robot_class == "DRONE"
    with pytest.raises(ValueError):
        twin.add_robot(name="Bad", robot_class="AMR", model_code="NW-PF1200")
    with pytest.raises(ValueError):
        twin.add_robot(name="Arm", model_code="FB-CX10")  # ARM joins the floor in sub-project C
    with pytest.raises(ValueError):
        twin.add_robot(name="Ghost", model_code="NOPE")
    with pytest.raises(ValueError):
        twin.add_robot(name="Twin", asset_id="AST-000101")  # already on the floor
    with pytest.raises(ValueError):
        twin.add_robot(name="Dead", asset_id="AST-000213")  # decommissioned
    assert twin.find_robot("Bad") is None and twin.find_robot("Twin") is None


def test_binding_an_asset_that_is_not_in_service_holds_the_robot(twin):
    robot = twin.add_robot(name="Workshop", asset_id="AST-000103")
    assert robot.status == RobotStatus.STOPPED and robot.fleet_hold == "MAINTENANCE"
    forklift = twin.add_robot(name="Remote", asset_id="AST-000205")
    assert forklift.robot_class == "FORKLIFT" and forklift.firmware_version == "2.1.1"


def test_add_operator_registers_a_worker_with_matching_credentials(twin):
    operator = twin.add_operator(name="Robin", certifications=["safety_inspection", "hazmat_handling"])
    assert operator.worker_id and operator.certifications == ["safety_inspection", "hazmat_handling"]
    worker = twin.inventory.get_worker(operator.worker_id)
    assert worker["display_name"] == "Robin" and worker["site_codes"] == ["WH-01"]
    assert [c["code"] for c in worker["credentials"]] == ["safety_inspection", "hazmat_handling"]
    odd = twin.add_operator(name="Quinn", certifications=["crane_rigging"])  # unknown code gets a definition
    assert odd.certifications == ["crane_rigging"]
    preset = twin.add_operator(name="Tess", role="SAFETY_INSPECTOR")
    assert preset.certifications == ["safety_inspection"]


def test_reset_reseeds_the_inventory_with_a_new_epoch(twin):
    epoch = twin.inventory.store.epoch()
    extra = twin.add_robot(name="Extra")
    twin.reset(demo_tasks=False)
    assert twin.inventory.store.epoch() != epoch
    assert twin.find_robot("Robo-01").asset_id == "AST-000101"
    assert not twin.inventory.has_asset(extra.asset_id)


def test_save_and_load_state_rebinds_by_id(twin, tmp_path):
    twin.find_robot("Robo-01").firmware_version = "0.9.0-beta"
    path = twin.save_state(str(tmp_path / "state.json"))
    twin.load_state(path)
    robo1 = twin.find_robot("Robo-01")
    assert robo1.asset_id == "AST-000101" and robo1.firmware_version == "0.9.0-beta"  # saved truth wins
    assert twin.find_operator("Sam").worker_id == "E-10001"


def test_inventory_file_persists_across_twins(tmp_path):
    path = str(tmp_path / "inventory.sqlite3")
    first = make_twin(tmp_path, inventory_path=path)
    first.fleet.mutate(first.inventory.admin_edit, "AST-000201", {"fleet_id": "FLEET-Z"}, "test")
    second = make_twin(tmp_path, inventory_path=path)
    assert second.inventory.get_robot("AST-000201")["fleet_id"] == "FLEET-Z"
    assert second.find_robot("Robo-01").asset_id == "AST-000101"


def test_the_existing_firmware_gate_still_sees_runtime_firmware(twin):
    twin.find_robot("Robo-01").firmware_version = "0.9.0-beta"
    task = twin.tasks.create_task({"type": "MOVE_ROBOT", "robot_id": "Robo-01", "destination": "loading_zone"})
    assert task.status is TaskStatus.FAILED and "unapproved firmware" in task.error
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest backend/test_fleet_bridge.py -q`
Expected: failures — `AttributeError: 'Robot' object has no attribute 'asset_id'`.

- [ ] **Step 3: Implement**

(a) `backend/models.py` — in `CONFIG`, replace the closing lines

```python
    "FALSE_SUCCESS_RISK": 0.0,
}
```

with

```python
    "FALSE_SUCCESS_RISK": 0.0,
    # Fleet & workforce inventory — see backend/inventory and
    # backend/fleet_bridge.py. Heartbeats: how often each floor robot
    # reports its running software/health to the fleet manager. OTA: how
    # many ticks a download and an install take. OTA_FAILURE_RISK (0.0 by
    # default, like the other risk knobs) fails an install at the last step.
    "FLEET_HEARTBEAT_EVERY_TICKS": 10,
    "OTA_DOWNLOAD_TICKS": 5,
    "OTA_INSTALL_TICKS": 8,
    "OTA_FAILURE_RISK": 0.0,
    "CALIBRATION_DUE_SOON_DAYS": 14,
    "CREDENTIAL_EXPIRING_SOON_DAYS": 30,
    "REPORT_STALE_SECONDS": 300,
}
```

In `class LogCategory`, after `DIGITAL_TWIN = "DIGITAL_TWIN"` add `FLEET = "FLEET"`. In `class EventType`, after `CI_COMPLETED = "CI_COMPLETED"` add:

```python
    INVENTORY_CHANGED = "INVENTORY_CHANGED"
    OTA_JOB_UPDATED = "OTA_JOB_UPDATED"
    OPERATOR_CERTIFICATIONS_CHANGED = "OPERATOR_CERTIFICATIONS_CHANGED"
```

(b) `backend/robot.py` — in `Robot.__init__`, right after `self.carrying_box: Optional[str] = None`, add:

```python
        # Inventory binding (backend/fleet_bridge.py): which fleet-manager
        # asset this physical robot is, and what it is actually running
        # beyond its main robot software (firmware_version above).
        self.asset_id: Optional[str] = None
        self.ai_policy_version: Optional[str] = None
        self.component_firmware: Dict[str, str] = {}
        # True while an OTA update installs — the robot can't take work.
        self.ota_installing: bool = False
        # The asset lifecycle status that made the fleet bridge stop this
        # robot (e.g. MAINTENANCE), so it only ever resumes robots it stopped.
        self.fleet_hold: Optional[str] = None
```

Replace `is_available` with:

```python
    @property
    def is_available(self) -> bool:
        return (
            self.status in (RobotStatus.IDLE, RobotStatus.CHARGING)
            and self.current_task is None
            and not self.ota_installing
        )
```

In `to_dict`, after `"robot_class": self.robot_class,` add:

```python
            "asset_id": self.asset_id,
            "ai_policy_version": self.ai_policy_version,
            "component_firmware": dict(self.component_firmware),
            "ota_installing": self.ota_installing,
            "fleet_hold": self.fleet_hold,
```

In `from_dict`, after `robot.maintenance_alerted = data.get("maintenance_alerted", False)` add:

```python
        robot.asset_id = data.get("asset_id")
        robot.ai_policy_version = data.get("ai_policy_version")
        robot.component_firmware = dict(data.get("component_firmware") or {})
        robot.fleet_hold = data.get("fleet_hold")
```

(c) `backend/operator.py` — in `Operator.__init__`, after `self.role: Optional[str] = role` add:

```python
        # The workforce-system record this operator is (backend/fleet_bridge.py).
        # When set, `certifications` is kept in sync with that worker's valid
        # credentials — the inventory is the source of truth for qualifications.
        self.worker_id: Optional[str] = None
```

In `to_dict` add `"worker_id": self.worker_id,` after `"role": self.role,`; in `from_dict` add `operator.worker_id = data.get("worker_id")` before `return operator`.

(d) Create `backend/fleet_bridge.py`:

```python
"""FleetBridge — joins the live simulation to the inventory source systems.

The inventory (backend/inventory) holds the fleet manager's and workforce
system's *records*; this bridge owns everything *physical*: which live
Robot is which asset, the heartbeats a robot sends, an OTA install actually
changing what a robot runs, a robot being stopped because its asset went
into maintenance, and an operator losing a certification the moment it is
revoked. See the design spec §5.

Lock order is always twin.lock → inventory store lock: every mutation goes
through mutate() (or runs in code that already holds twin.lock, like
DigitalTwin.add_robot and Simulator.tick), and the service calls
_on_change only after its transaction has committed.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from .inventory import InventoryService, NotFound, reseed
from .inventory.catalog_data import CLASS_DEFAULT_MODELS
from .inventory.core import SYSTEM
from .models import EventType, LogCategory, LogLevel, RobotStatus

FLOOR_SITE = "WH-01"
WARNING_ACTIONS = frozenset({"OTA_FAILED", "CREDENTIAL_REVOKED"})


class FleetBridge:
    def __init__(self, twin: Any, service: InventoryService) -> None:
        self.twin = twin
        self.service = service
        self._ota_ticks: Dict[str, int] = {}
        self._battery_base: Dict[str, Dict[str, float]] = {}
        service.subscribe(self._on_change)

    # ---- mutations ------------------------------------------------------ #
    def mutate(self, action: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Run an inventory action under twin.lock (the only safe lock order)."""
        with self.twin.lock:
            return action(*args, **kwargs)

    def reseed(self, demo: bool = True) -> None:
        with self.twin.lock:
            reseed(self.service, demo=demo)
            self._ota_ticks.clear()
            self._battery_base.clear()

    # ---- binding -------------------------------------------------------- #
    def robot_for_asset(self, asset_id: Optional[str]) -> Optional[Any]:
        if not asset_id:
            return None
        for robot in self.twin.robots.values():
            if robot.asset_id == asset_id:
                return robot
        return None

    def bind_new_robot(self, robot: Any, model_code: Optional[str] = None,
                       asset_id: Optional[str] = None, adopt: bool = True) -> None:
        """Attach `robot` to its fleet-manager asset, commissioning one if
        needed. `adopt=True` (a new robot) takes the running versions from the
        asset's last report; `adopt=False` (a restored robot) keeps its own."""
        service = self.service
        if asset_id and service.has_asset(asset_id):
            record = service.get_robot(asset_id)
            if record["lifecycle_status"] == "DECOMMISSIONED":
                raise ValueError(f"Asset {asset_id} is decommissioned")
            asset_class = record["model"]["embodiment_class"]
            if asset_class != robot.robot_class:
                raise ValueError(f"Asset {asset_id} is a {asset_class}, not a {robot.robot_class}")
            bound = self.robot_for_asset(asset_id)
            if bound is not None and bound is not robot:
                raise ValueError(f"Asset {asset_id} is already on the floor as {bound.name}")
        else:
            model = model_code or CLASS_DEFAULT_MODELS.get(robot.robot_class)
            if model is None:
                raise ValueError(f"No catalog model for robot class {robot.robot_class}")
            try:
                model_class = service.model_class(model)
            except NotFound as exc:
                raise ValueError(str(exc)) from None
            if model_class != robot.robot_class:
                raise ValueError(f"{model} is a {model_class}, not a {robot.robot_class}")
            release = service.release_by_version("ROBOT_SOFTWARE", model, robot.firmware_version)
            zone = self.twin.warehouse.zone_of_cell(robot.position)
            change = service.commission_robot(
                model, FLOOR_SITE, zone.key if zone else None, asset_id=asset_id or None,
                software_release_id=release["release_id"] if release else None, actor=SYSTEM,
            )
            record = service.get_robot(change["aggregate_id"])
        robot.asset_id = record["asset_id"]
        reported = record["reported"] or {}
        if adopt:
            robot.firmware_version = reported.get("software_version") or record["declared_software_version"]
            robot.ai_policy_version = reported.get("ai_policy_version") or record["declared_ai_policy_version"]
            robot.component_firmware = dict(reported.get("component_firmware") or {})
        battery = reported.get("battery")
        if battery is not None:
            self._battery_base[robot.asset_id] = {
                "soh": float(battery.get("soh_pct", 100.0)),
                "cycles": int(battery.get("cycle_count", 0)),
                "offset": robot.charging_sessions,
            }
        else:
            self._battery_base.pop(robot.asset_id, None)
        status = record["lifecycle_status"]
        if status != "IN_SERVICE" and robot.status != RobotStatus.STOPPED:
            robot.status_before_stop = robot.status
            robot.set_status(RobotStatus.STOPPED)
            robot.fleet_hold = status

    def bind_new_operator(self, operator: Any, worker_id: Optional[str] = None) -> None:
        """Attach `operator` to its workforce record, registering one (with a
        credential per certification) if needed."""
        service = self.service
        if worker_id and service.has_worker(worker_id):
            operator.worker_id = worker_id
        else:
            with service.transaction():
                change = service.register_worker(
                    operator.name, role_codes=[operator.role] if operator.role else ["WAREHOUSE_OPERATOR"],
                    site_codes=[FLOOR_SITE], worker_id=worker_id or None,
                    shift_start_hour=operator.shift_start_hour, shift_end_hour=operator.shift_end_hour,
                    actor=SYSTEM,
                )
                operator.worker_id = change["aggregate_id"]
                for code in operator.certifications:
                    service.ensure_credential_definition(code)
                    service.issue_credential(operator.worker_id, code, actor=SYSTEM)
        self._sync_operator(operator, announce=False)

    def rebind_all(self) -> None:
        """Re-attach robots and operators restored by DigitalTwin.load_state."""
        for robot in list(self.twin.robots.values()):
            if robot.asset_id and self.service.has_asset(robot.asset_id):
                self.bind_new_robot(robot, asset_id=robot.asset_id, adopt=False)
                continue
            missing, robot.asset_id = robot.asset_id, None
            self.bind_new_robot(robot, adopt=False)
            if missing:
                self.twin.logger.warning(
                    LogCategory.FLEET,
                    f"{robot.name}: asset {missing} is not in the inventory — commissioned {robot.asset_id}")
        for operator in list(self.twin.operators.values()):
            if operator.worker_id and self.service.has_worker(operator.worker_id):
                self.bind_new_operator(operator, worker_id=operator.worker_id)
                continue
            if operator.worker_id:
                self.twin.logger.warning(
                    LogCategory.FLEET,
                    f"{operator.name}: worker {operator.worker_id} is not in the inventory — registering a new record")
            operator.worker_id = None
            self.bind_new_operator(operator)

    def _sync_operator(self, operator: Any, announce: bool = True) -> bool:
        """Make operator.certifications equal the worker's valid credential codes."""
        codes = self.service.valid_credential_codes(operator.worker_id)
        if codes == list(operator.certifications):
            return False
        previous = list(operator.certifications)
        operator.certifications = codes
        operator.touch()
        if announce:
            lost = [c for c in previous if c not in codes]
            gained = [c for c in codes if c not in previous]
            parts = ([f"lost {', '.join(lost)}"] if lost else []) + ([f"gained {', '.join(gained)}"] if gained else [])
            self.twin.events.emit(
                EventType.OPERATOR_CERTIFICATIONS_CHANGED,
                f"{operator.name} certifications {'; '.join(parts)} ({operator.worker_id})",
                category=LogCategory.FLEET,
                level=LogLevel.WARNING if lost else LogLevel.INFO,
                data={"operator_id": operator.id, "worker_id": operator.worker_id,
                      "certifications": codes, "lost": lost, "gained": gained},
            )
        return True

    # ---- inventory change listener -------------------------------------- #
    def _on_change(self, change: Dict[str, Any]) -> None:
        action = change["action"]
        robot = (self.robot_for_asset(change["aggregate_id"])
                 if change["aggregate_type"] in ("ROBOT_ASSET", "ROBOT_OBSERVATION") else None)
        reason = f" ({change['reason']})" if change.get("reason") else ""
        self.twin.events.emit(
            EventType.OTA_JOB_UPDATED if action.startswith("OTA_") else EventType.INVENTORY_CHANGED,
            f"{change['aggregate_id']}: {action.replace('_', ' ').lower()}{reason}",
            category=LogCategory.FLEET,
            level=LogLevel.WARNING if action in WARNING_ACTIONS else LogLevel.INFO,
            robot_id=robot.id if robot else None,
            data={"seq": change["seq"], "aggregate_type": change["aggregate_type"],
                  "aggregate_id": change["aggregate_id"], "action": action,
                  "subject_id": change.get("subject_id")},
        )
```

(e) `backend/digital_twin.py`:

1. Add imports after `from .event_system import EventSystem`:

```python
from .fleet_bridge import FleetBridge
from .inventory import NotFound as InventoryNotFound
from .inventory import open_inventory
```

2. After the `DEMO_ROBOTS = [...]` list add:

```python
#: The inventory records (backend/inventory/demo_seed.py) the demo robots
#: and operators are bound to — separate maps so the tuples above stay
#: exactly as they were.
DEMO_ASSET_IDS = {"Robo-01": "AST-000101", "Robo-02": "AST-000102"}
DEMO_WORKER_IDS = {"Sam": "E-10001", "Lee": "E-10002"}
```

3. `__init__`: add the parameter `inventory_path: str = ":memory:",` after `demo_tasks: bool = True,`, and immediately before `if demo:\n            self.load_demo(create_tasks=demo_tasks)` add:

```python
        # Fleet-manager + workforce source systems (backend/inventory) and the
        # bridge that binds every live robot/operator to its record.
        self.inventory = open_inventory(inventory_path, demo=demo, settings=lambda: CONFIG)
        self.fleet = FleetBridge(self, self.inventory)
```

4. `load_demo`: change the two creation lines to

```python
        for name, position in DEMO_ROBOTS:
            self.add_robot(name=name, position=position, asset_id=DEMO_ASSET_IDS.get(name))
```

and

```python
        for name, certifications in DEMO_OPERATORS:
            self.add_operator(name=name, certifications=certifications, worker_id=DEMO_WORKER_IDS.get(name))
```

5. `add_robot`: add parameters `model_code: Optional[str] = None,` and `asset_id: Optional[str] = None,` after `robot_class: Optional[str] = None,`. Replace the line `normalized_class = (robot_class or "AMR").upper()` with:

```python
            normalized_class = (robot_class or "").upper() or None
            if model_code:
                try:
                    model_class = self.inventory.model_class(model_code)
                except InventoryNotFound as exc:
                    raise ValueError(str(exc)) from None
                if normalized_class and normalized_class != model_class:
                    raise ValueError(f"Model {model_code} is a {model_class}, not a {normalized_class}")
                normalized_class = model_class
            elif asset_id and not normalized_class and self.inventory.has_asset(asset_id):
                normalized_class = self.inventory.get_robot(asset_id)["model"]["embodiment_class"]
            normalized_class = normalized_class or "AMR"
```

and replace `self.robots[robot.id] = robot` (inside `add_robot`) with:

```python
            self.fleet.bind_new_robot(robot, model_code=model_code, asset_id=asset_id)
            self.robots[robot.id] = robot
```

6. `add_operator`: add the parameter `worker_id: Optional[str] = None,` after `role: Optional[str] = None,`, and replace `self.operators[operator.id] = operator` (inside `add_operator`) with:

```python
            self.fleet.bind_new_operator(operator, worker_id=worker_id)
            self.operators[operator.id] = operator
```

7. `reset`: inside `with self.lock:`, after `self.planner = TaskPlanner(self)`, add `self.fleet.reseed()`.

8. `load_state`: inside `with self.lock:`, immediately before `self.scheduler.load_dict(payload.get("schedules", {}))`, add `self.fleet.rebind_all()`.

(f) `backend/app.py` — in `create_app`, the default twin becomes:

```python
    twin = twin or DigitalTwin(
        log_dir=os.path.join(BASE_DIR, "logs"),
        data_dir=os.path.join(BASE_DIR, "data"),
        inventory_path=os.path.join(BASE_DIR, "data", "inventory.sqlite3"),
    )
```

(g) `.gitignore` — add the line `data/inventory.sqlite3*`.

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest backend/test_fleet_bridge.py -q`
Expected: `8 passed`.

Then the whole suite: `.venv/bin/python -m pytest -q`
Expected: `324 passed, 2 failed` — the only failures are the two pre-existing ones named in Global Constraints. If any other existing test fails, the change broke behaviour neutrality: fix it before committing.

- [ ] **Step 5: Commit**

```bash
git add backend/fleet_bridge.py backend/models.py backend/robot.py backend/operator.py backend/digital_twin.py backend/app.py .gitignore backend/test_fleet_bridge.py
git commit -m "feat: bind live robots and operators to fleet/workforce inventory records

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 10: Runtime effects — heartbeats, OTA over ticks, lifecycle and credential effects

**Files:**
- Modify: `backend/fleet_bridge.py`, `backend/simulator.py`
- Test: `backend/test_fleet_bridge.py` (append)

**Interfaces:**
- Consumes: Task 9's `FleetBridge`, service `active_ota_jobs`, `transition_ota`, `report_state`, `touch_remote_reports`, `get_ota_job`, `get_release`, `os_version_for`, `valid_credential_codes`; CONFIG keys from Task 9.
- Produces: `FleetBridge.on_tick(tick)`, `heartbeat()`, `sync_all_operators()`, `_report_robot(robot)`, `_advance_ota()`, `_set_running_version(asset_id, robot, kind, slot, version)`, `_set_component_firmware(asset_id, robot, slot, version)`, `_apply_lifecycle(robot, status)`; a new `_on_change` that also applies effects; `Simulator._tick_fleet()` called every tick.

- [ ] **Step 1: Write the failing tests** — append to `backend/test_fleet_bridge.py`:

```python
# --------------------------------------------------------------------------- #
# Runtime effects (Simulator.tick → FleetBridge.on_tick)
# --------------------------------------------------------------------------- #
import time
from datetime import datetime, timedelta, timezone

from backend.models import CONFIG, SimulationStatus
from backend.simulator import Simulator


@pytest.fixture
def sim(twin):
    simulator = Simulator(twin)
    twin.simulation_status = SimulationStatus.RUNNING
    return simulator


def ticks(sim, count):
    for _ in range(count):
        sim.tick()


def run_until(sim, predicate, max_ticks=400):
    for _ in range(max_ticks):
        if predicate():
            return
        sim.tick()
    raise AssertionError("condition never became true")


def job_state(twin, job_id):
    return twin.inventory.get_ota_job(job_id)["state"]


def observations(twin, asset_id):
    return [c for c in twin.inventory.robot_history(asset_id) if c["aggregate_type"] == "ROBOT_OBSERVATION"]


def test_heartbeat_reports_runtime_truth_only_when_it_changes(twin, sim):
    every = CONFIG["FLEET_HEARTBEAT_EVERY_TICKS"]
    ticks(sim, every * 3)
    quiet = len(observations(twin, "AST-000101"))
    ticks(sim, every * 3)
    assert len(observations(twin, "AST-000101")) == quiet  # nothing changed, nothing logged
    twin.find_robot("Robo-01").firmware_version = "0.9.0-beta"
    ticks(sim, every)
    record = twin.inventory.get_robot("AST-000101")
    assert record["reported"]["software_version"] == "0.9.0-beta"
    assert record["flags"]["software_mismatch"] and record["flags"]["running_unknown_software"]
    assert record["reported"]["operational_mode"] == "IDLE"
    assert not twin.inventory.get_robot("AST-000201")["flags"]["report_stale"]  # remote sites check in too


def test_ota_runs_through_the_simulator_and_waits_for_an_idle_robot(twin, sim):
    job = twin.fleet.mutate(twin.inventory.start_ota, "AST-000101", "AC-TR50:SW:2.2.0")["subject_id"]
    robot = twin.find_robot("Robo-01")
    twin.tasks.create_task({"type": "MOVE_ROBOT", "robot_id": "Robo-01", "destination": "loading_zone"})
    ticks(sim, CONFIG["OTA_DOWNLOAD_TICKS"] + 3)
    assert robot.current_task is not None and job_state(twin, job) == "DOWNLOADING"  # waits while busy
    run_until(sim, lambda: job_state(twin, job) == "INSTALLING")
    assert robot.current_task is None and robot.ota_installing and not robot.is_available
    run_until(sim, lambda: job_state(twin, job) == "REPORTED")
    assert robot.firmware_version == "2.2.0" and not robot.ota_installing and robot.is_available
    record = twin.inventory.get_robot("AST-000101")
    assert record["reported"]["software_version"] == "2.2.0" and record["declared_software_version"] == "2.1.0"
    twin.fleet.mutate(twin.inventory.verify_ota, job, verified_by="E-10003")
    assert twin.inventory.get_robot("AST-000101")["flags"]["software_mismatch"] is False
    assert any(e["event"] == "OTA_JOB_UPDATED" for e in twin.events.query(limit=500))


def test_ota_failure_knob(twin, sim, monkeypatch):
    monkeypatch.setitem(CONFIG, "OTA_FAILURE_RISK", 1.0)
    job = twin.fleet.mutate(twin.inventory.start_ota, "AST-000102", "AC-TR50:SW:2.2.0")["subject_id"]
    run_until(sim, lambda: job_state(twin, job) == "FAILED")
    robot = twin.find_robot("Robo-02")
    assert robot.firmware_version == "2.1.0" and not robot.ota_installing
    assert any(e["event"] == "OTA_JOB_UPDATED" and e["level"] == "WARNING" for e in twin.events.query(limit=500))


def test_rollback_restores_the_previous_running_version(twin, sim):
    job = twin.fleet.mutate(twin.inventory.start_ota, "AST-000101", "AC-TR50:SW:2.2.0")["subject_id"]
    run_until(sim, lambda: job_state(twin, job) == "REPORTED")
    twin.fleet.mutate(twin.inventory.rollback_ota, job, "Localisation regression")
    assert twin.find_robot("Robo-01").firmware_version == "2.1.0"
    assert twin.inventory.get_robot("AST-000101")["reported"]["software_version"] == "2.1.0"


def test_off_floor_assets_update_without_a_robot(twin, sim):
    job = twin.fleet.mutate(twin.inventory.start_ota, "AST-000201", "AC-TR50:SW:2.1.1")["subject_id"]
    run_until(sim, lambda: job_state(twin, job) == "REPORTED")
    assert twin.inventory.get_robot("AST-000201")["reported"]["software_version"] == "2.1.1"


def test_lifecycle_changes_stop_and_resume_the_floor_robot(twin):
    robot = twin.find_robot("Robo-01")
    wo = twin.fleet.mutate(twin.inventory.open_work_order, "AST-000101", "CORRECTIVE", "Bumper cracked")["subject_id"]
    assert robot.status == RobotStatus.STOPPED and robot.fleet_hold == "MAINTENANCE"
    twin.fleet.mutate(twin.inventory.swap_component, wo, "lidar", performed_by="E-10003", hw_revision="A")
    assert robot.component_firmware["lidar"] == "1.9.4"  # a rev-A lidar can't run 2.0.1
    assert twin.inventory.get_robot("AST-000101")["reported"]["component_firmware"]["lidar"] == "1.9.4"
    twin.fleet.mutate(twin.inventory.close_work_order, wo, "Bumper and lidar replaced")
    assert robot.status == RobotStatus.IDLE and robot.fleet_hold is None


def test_a_user_stop_is_not_undone_by_the_fleet(twin):
    robot = twin.find_robot("Robo-02")
    twin.stop_robot(robot.id, reason="user")
    wo = twin.fleet.mutate(twin.inventory.open_work_order, "AST-000102", "CORRECTIVE", "x")["subject_id"]
    twin.fleet.mutate(twin.inventory.close_work_order, wo, "done")
    assert robot.status == RobotStatus.STOPPED


def test_revoking_a_credential_removes_it_from_the_operator(twin):
    sam = twin.find_operator("Sam")
    credential = next(c for c in twin.inventory.get_worker("E-10001")["credentials"] if c["code"] == "electrical_safety")
    twin.fleet.mutate(twin.inventory.revoke_credential, credential["credential_id"], "Audit finding")
    assert sam.certifications == ["safety_inspection"]
    changed = [e for e in twin.events.query(limit=200) if e["event"] == "OPERATOR_CERTIFICATIONS_CHANGED"]
    assert changed and changed[-1]["level"] == "WARNING"


def test_credential_expiry_is_picked_up_on_the_shift_check_cadence(twin, sim):
    with twin.inventory.at(datetime.now(timezone.utc) + timedelta(days=400)):
        ticks(sim, CONFIG["SHIFT_CHECK_EVERY_TICKS"])
    assert twin.find_operator("Lee").certifications == []


def test_api_style_mutations_during_a_running_simulator_thread_do_not_deadlock(twin):
    simulator = Simulator(twin)
    simulator.start()
    simulator.start_thread()
    try:
        started = time.time()
        for i in range(20):
            twin.fleet.mutate(twin.inventory.admin_edit, "AST-000201", {"fleet_id": f"F-{i}"}, "stress")
            twin.fleet.mutate(twin.inventory.report_state, "AST-000201",
                              {"health_state": "DEGRADED" if i % 2 else "OK"})
        assert time.time() - started < 5
        time.sleep(0.3)
        assert twin.tick_count > 0
    finally:
        simulator.stop_thread()
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest backend/test_fleet_bridge.py -q`
Expected: the new tests fail (e.g. `AssertionError: condition never became true`, `assert <RobotStatus.IDLE> == <RobotStatus.STOPPED>`); the 8 Task 9 tests still pass.

- [ ] **Step 3: Implement**

(a) `backend/fleet_bridge.py` — change the imports to:

```python
import random
from typing import Any, Callable, Dict, Optional

from .inventory import InventoryService, NotFound, reseed
from .inventory.catalog_data import CLASS_DEFAULT_MODELS
from .inventory.core import SYSTEM
from .models import CONFIG, EventType, LogCategory, LogLevel, RobotStatus
```

add below `WARNING_ACTIONS`:

```python
LIFECYCLE_ACTIONS = frozenset({"STATUS_CHANGED", "DECOMMISSIONED", "WORK_ORDER_OPENED", "WORK_ORDER_CLOSED"})
```

replace the whole `_on_change` method with:

```python
    # ---- inventory change listener -------------------------------------- #
    def _on_change(self, change: Dict[str, Any]) -> None:
        """Announce every inventory change on the twin's event bus, then apply
        its physical effect on the floor (runs after the change committed)."""
        action = change["action"]
        robot = (self.robot_for_asset(change["aggregate_id"])
                 if change["aggregate_type"] in ("ROBOT_ASSET", "ROBOT_OBSERVATION") else None)
        reason = f" ({change['reason']})" if change.get("reason") else ""
        self.twin.events.emit(
            EventType.OTA_JOB_UPDATED if action.startswith("OTA_") else EventType.INVENTORY_CHANGED,
            f"{change['aggregate_id']}: {action.replace('_', ' ').lower()}{reason}",
            category=LogCategory.FLEET,
            level=LogLevel.WARNING if action in WARNING_ACTIONS else LogLevel.INFO,
            robot_id=robot.id if robot else None,
            data={"seq": change["seq"], "aggregate_type": change["aggregate_type"],
                  "aggregate_id": change["aggregate_id"], "action": action,
                  "subject_id": change.get("subject_id")},
        )
        if change["aggregate_type"] == "ROBOT_ASSET":
            if action in LIFECYCLE_ACTIONS and robot is not None:
                self._apply_lifecycle(robot, change["after"]["lifecycle_status"])
            if action == "COMPONENT_SWAPPED":
                new = next((c for c in change["after"]["components"]
                            if c["component_id"] == change["subject_id"]), None)
                if new is not None:
                    version = (self.service.get_release(new["firmware_release_id"])["version"]
                               if new["firmware_release_id"] else None)
                    self._set_component_firmware(change["aggregate_id"], robot, new["slot"], version)
            elif action == "OTA_ROLLED_BACK":
                job = self.service.get_ota_job(change["subject_id"])
                self._set_running_version(job["asset_id"], robot, job["kind"], job["slot"], job["from_version"])
            elif action == "OTA_FAILED":
                self._ota_ticks.pop(change["subject_id"], None)
                if robot is not None:
                    robot.ota_installing = False
        elif change["aggregate_type"] == "WORKER":
            for operator in list(self.twin.operators.values()):
                if operator.worker_id == change["aggregate_id"]:
                    self._sync_operator(operator)

    # ---- physical effects ----------------------------------------------- #
    def _apply_lifecycle(self, robot: Any, status: str) -> None:
        """A floor robot whose asset leaves IN_SERVICE is stopped; it is
        resumed on return — but only if the fleet was what stopped it."""
        if status == "IN_SERVICE":
            if robot.fleet_hold is not None:
                robot.fleet_hold = None
                if robot.status == RobotStatus.STOPPED:
                    self.twin.resume_robot(robot.id, reason="asset back in service")
            return
        if robot.fleet_hold is None and robot.status == RobotStatus.STOPPED:
            return  # stopped by someone else — leave it to them
        if robot.fleet_hold != status:
            robot.fleet_hold = status
            if robot.status != RobotStatus.STOPPED:
                self.twin.stop_robot(robot.id, reason=f"asset {status.lower().replace('_', ' ')}")

    def _set_component_firmware(self, asset_id: str, robot: Optional[Any], slot: str,
                                version: Optional[str]) -> None:
        if robot is not None:
            if version is None:
                robot.component_firmware.pop(slot, None)
            else:
                robot.component_firmware[slot] = version
            robot.touch()
            self._report_robot(robot)
            return
        reported = self.service.get_robot(asset_id)["reported"] or {}
        firmware = dict(reported.get("component_firmware") or {})
        if version is None:
            firmware.pop(slot, None)
        else:
            firmware[slot] = version
        self.service.report_state(asset_id, {"component_firmware": firmware})

    def _set_running_version(self, asset_id: str, robot: Optional[Any], kind: str,
                             slot: Optional[str], version: Optional[str]) -> None:
        """What an install (or rollback) physically does: change what runs."""
        if kind == "COMPONENT_FIRMWARE":
            self._set_component_firmware(asset_id, robot, slot, version)
            return
        if robot is not None:
            if kind == "ROBOT_SOFTWARE":
                robot.firmware_version = version
            else:
                robot.ai_policy_version = version
            robot.touch()
            self._report_robot(robot)
            return
        if kind == "ROBOT_SOFTWARE":
            self.service.report_state(asset_id, {"software_version": version,
                                                 "os_version": self.service.os_version_for(version)})
        else:
            self.service.report_state(asset_id, {"ai_policy_version": version})

    # ---- runtime (Simulator.tick, twin.lock held) ----------------------- #
    def on_tick(self, tick: int) -> None:
        self._advance_ota()
        if tick % max(1, int(CONFIG["FLEET_HEARTBEAT_EVERY_TICKS"])) == 0:
            self.heartbeat()
        if tick % max(1, int(CONFIG["SHIFT_CHECK_EVERY_TICKS"])) == 0:
            self.sync_all_operators()

    def heartbeat(self) -> None:
        """Every floor robot reports what it is running; the other sites'
        online robots check in (reported_at only)."""
        bound = set()
        for robot in list(self.twin.robots.values()):
            if robot.asset_id and self.service.has_asset(robot.asset_id):
                bound.add(robot.asset_id)
                self._report_robot(robot)
        self.service.touch_remote_reports(exclude=bound)

    def _report_robot(self, robot: Any) -> None:
        if robot.status == RobotStatus.ERROR:
            health = "FAULT"
        elif robot.maintenance_alerted:
            health = "DEGRADED"
        else:
            health = "OK"
        zone = self.twin.warehouse.zone_of_cell(robot.position)
        reported: Dict[str, Any] = {
            "software_version": robot.firmware_version,
            "os_version": self.service.os_version_for(robot.firmware_version),
            "ai_policy_version": robot.ai_policy_version,
            "component_firmware": dict(robot.component_firmware),
            "health_state": health,
            "connectivity": "ONLINE",
            "operational_mode": "UPDATING" if robot.ota_installing else robot.status.value,
            "zone": zone.key if zone else None,
        }
        base = self._battery_base.get(robot.asset_id)
        if base is not None:
            extra = max(0, robot.charging_sessions - base["offset"])
            reported["battery"] = {
                "soc_pct": round(robot.battery, 1),
                "cycle_count": int(base["cycles"] + extra),
                "soh_pct": round(max(0.0, base["soh"] - 0.02 * extra), 2),
            }
        self.service.report_state(robot.asset_id, reported)

    def _advance_ota(self) -> None:
        """STAGED → DOWNLOADING at once; DOWNLOADING for OTA_DOWNLOAD_TICKS
        (and until a floor robot is idle); INSTALLING for OTA_INSTALL_TICKS,
        during which the robot takes no work; then the new version runs and
        the job is REPORTED (or FAILED, per OTA_FAILURE_RISK)."""
        for job in self.service.active_ota_jobs():
            job_id, state = job["job_id"], job["state"]
            robot = self.robot_for_asset(job["asset_id"])
            if state == "STAGED":
                self.service.transition_ota(job_id, "DOWNLOADING")
                self._ota_ticks[job_id] = 0
                continue
            ticks = self._ota_ticks.get(job_id, 0) + 1
            self._ota_ticks[job_id] = ticks
            if state == "DOWNLOADING":
                if ticks < CONFIG["OTA_DOWNLOAD_TICKS"] or (robot is not None and robot.current_task is not None):
                    continue
                self.service.transition_ota(job_id, "INSTALLING")
                self._ota_ticks[job_id] = 0
                if robot is not None:
                    robot.ota_installing = True
            elif state == "INSTALLING":
                if ticks < CONFIG["OTA_INSTALL_TICKS"]:
                    continue
                self._ota_ticks.pop(job_id, None)
                if robot is not None:
                    robot.ota_installing = False
                if random.random() < CONFIG.get("OTA_FAILURE_RISK", 0.0):
                    self.service.transition_ota(job_id, "FAILED",
                                                failure_reason="install failed: image signature verification error")
                    continue
                self._set_running_version(job["asset_id"], robot, job["kind"], job["slot"], job["version"])
                self.service.transition_ota(job_id, "REPORTED")

    def sync_all_operators(self) -> None:
        """Catch credentials that expired (or became effective) with time."""
        for operator in list(self.twin.operators.values()):
            if operator.worker_id and self.service.has_worker(operator.worker_id):
                self._sync_operator(operator)
```

(b) `backend/simulator.py` — in `tick()`, right after `self._check_maintenance()`, add `self._tick_fleet()`; and add this method after `_check_operator_shifts`:

```python
    def _tick_fleet(self) -> None:
        """Inventory runtime (backend/fleet_bridge.py): OTA progress, robot
        heartbeats and credential expiry. An inventory failure must never stop
        the physical simulation, so it is logged rather than raised."""
        try:
            self.twin.fleet.on_tick(self.twin.tick_count)
        except Exception as exc:  # noqa: BLE001 - see docstring
            self.twin.logger.error(LogCategory.FLEET, f"Fleet bridge error: {exc!r}")
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest backend/test_fleet_bridge.py -q`
Expected: `18 passed`.

Then the whole suite: `.venv/bin/python -m pytest -q` → `334 passed, 2 failed` (only the two pre-existing failures).

- [ ] **Step 5: Commit**

```bash
git add backend/fleet_bridge.py backend/simulator.py backend/test_fleet_bridge.py
git commit -m "feat: heartbeats, OTA installs over simulator ticks, lifecycle and credential effects

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: REST API for the fleet manager and workforce system

**Files:**
- Create: `backend/inventory_api.py`
- Modify: `backend/app.py` (import, registration, `POST /api/robots` accepts `model_code`/`asset_id`), `README.md`
- Test: `backend/test_inventory_api.py`

**Interfaces:**
- Consumes: `twin.inventory`, `twin.fleet` (`mutate`, `robot_for_asset`), `app.py`'s `ApiError(message, status=400, field=None)`.
- Produces: `register_inventory_routes(app, twin, api_error)`; the routes in spec §7.1. Mutation responses: `{"ok": true, "change": <change>, "robot": <record + floor_robot>}` or `{..., "worker": <record>}` or `{..., "release": <release>}`; create-style actions return 201. `GET /api/workforce/credential-definitions` → `{"ok": true, "definitions": [...]}`.

- [ ] **Step 1: Write the failing tests** — create `backend/test_inventory_api.py`:

```python
"""REST API over the fleet-manager and workforce source systems."""
import pytest

from backend.app import create_app
from backend.digital_twin import DigitalTwin


@pytest.fixture
def client(tmp_path):
    twin = DigitalTwin(log_dir=str(tmp_path / "logs"), data_dir=str(tmp_path / "data"),
                       persist_logs=False, demo=True, demo_tasks=False)
    app, _twin, _sim, _ci = create_app(twin=twin, autostart=False, run_thread=False)
    return app.test_client()


def test_catalog_and_release_endpoints(client):
    models = client.get("/api/fleet/catalog/models").get_json()["models"]
    assert any(m["model_code"] == "NW-PF1200" for m in models)
    assert client.get("/api/fleet/catalog/parts").get_json()["parts"]
    releases = client.get("/api/fleet/releases?target=AC-TR50&kind=ROBOT_SOFTWARE").get_json()["releases"]
    assert {"2.1.0", "2.2.0"} <= {r["version"] for r in releases}
    sbom = client.get("/api/fleet/releases/AC-TR50:SW:2.2.0/sbom")
    assert sbom.status_code == 200 and sbom.get_json()["bomFormat"] == "CycloneDX"
    body = {"kind": "ROBOT_SOFTWARE", "target_code": "AC-TR50", "version": "2.3.0"}
    created = client.post("/api/fleet/releases", json=body)
    assert created.status_code == 201 and created.get_json()["release"]["status"] == "CURRENT"
    assert client.post("/api/fleet/releases", json=body).status_code == 409
    assert client.post("/api/fleet/releases/AC-TR50:SW:2.3.0/recall", json={"reason": "bad"}).status_code == 200
    missing = client.get("/api/fleet/releases/NOPE/sbom")
    assert missing.status_code == 404 and missing.get_json()["ok"] is False


def test_robot_endpoints(client):
    robots = client.get("/api/fleet/robots?site=WH-01").get_json()["robots"]
    assert [r["asset_id"] for r in robots] == ["AST-000101", "AST-000102", "AST-000103"]
    detail = client.get("/api/fleet/robots/AST-000101").get_json()["robot"]
    assert detail["floor_robot"]["name"] == "Robo-01"
    assert client.get("/api/fleet/robots/AST-000201").get_json()["robot"]["floor_robot"] is None
    assert client.get("/api/fleet/robots/AST-404").status_code == 404
    made = client.post("/api/fleet/robots", json={"model_code": "CT-IX2", "site_code": "WH-02", "home_zone": "drone_pad"})
    assert made.status_code == 201
    asset = made.get_json()["robot"]["asset_id"]
    edited = client.patch(f"/api/fleet/robots/{asset}", json={"fleet_id": "AIR", "reason": "grouping"})
    assert edited.get_json()["robot"]["fleet_id"] == "AIR"
    assert client.patch(f"/api/fleet/robots/{asset}", json={"serial_number": "X", "reason": "no"}).status_code == 400
    assert client.post(f"/api/fleet/robots/{asset}/status", json={"status": "OUT_OF_SERVICE", "reason": "x"}).status_code == 200
    assert client.post(f"/api/fleet/robots/{asset}/status", json={"status": "COMMISSIONING", "reason": "x"}).status_code == 409
    history = client.get(f"/api/fleet/robots/{asset}/history").get_json()["history"]
    assert history[0]["action"] == "STATUS_CHANGED"
    assert client.post(f"/api/fleet/robots/{asset}/decommission", json={"reason": "gone"}).status_code == 200


def test_ota_and_maintenance_endpoints(client):
    staged = client.post("/api/fleet/robots/AST-000201/ota", json={"release_id": "AC-TR50:SW:2.1.1"})
    assert staged.status_code == 201
    job_id = staged.get_json()["change"]["subject_id"]
    assert client.post(f"/api/fleet/ota/{job_id}/verify", json={}).status_code == 409  # not REPORTED yet
    assert client.post("/api/fleet/robots/AST-000201/ota", json={"release_id": "AC-TR50:SW:2.2.1"}).status_code == 409
    opened = client.post("/api/fleet/robots/AST-000202/work-orders",
                         json={"type": "CORRECTIVE", "description": "Replace camera", "technician_id": "E-10004"})
    assert opened.status_code == 201
    wo_id = opened.get_json()["change"]["subject_id"]
    swapped = client.post(f"/api/fleet/work-orders/{wo_id}/swap", json={"slot": "camera", "performed_by": "E-10004"})
    assert swapped.status_code == 200
    camera = next(c for c in swapped.get_json()["robot"]["components"] if c["slot"] == "camera")
    assert camera["calibration_status"] == "MISSING"
    calibrated = client.post(f"/api/fleet/components/{camera['component_id']}/calibrations",
                             json={"result": "PASS", "performed_by": "E-10004"})
    assert calibrated.status_code == 201
    closed = client.post(f"/api/fleet/work-orders/{wo_id}/close", json={"resolution": "done"})
    assert closed.get_json()["robot"]["lifecycle_status"] == "IN_SERVICE"


def test_workforce_endpoints(client):
    workers = client.get("/api/workforce/workers?site=WH-01").get_json()["workers"]
    assert {"E-10001", "E-10002"} <= {w["worker_id"] for w in workers}
    made = client.post("/api/workforce/workers",
                       json={"display_name": "Robin", "role_codes": ["WAREHOUSE_OPERATOR"], "site_codes": ["WH-01"]})
    assert made.status_code == 201
    worker_id = made.get_json()["worker"]["worker_id"]
    refused = client.post("/api/workforce/workers", json={"display_name": "Pat", "salary": 1})
    assert refused.status_code == 400 and "salary" in refused.get_json()["error"]
    issued = client.post(f"/api/workforce/workers/{worker_id}/credentials",
                         json={"code": "forklift_operator", "equipment_scope": "NW-PF1200, NW-HH300"})
    assert issued.status_code == 201
    cred_id = issued.get_json()["change"]["subject_id"]
    cred = next(c for c in issued.get_json()["worker"]["credentials"] if c["credential_id"] == cred_id)
    assert cred["equipment_scope"] == ["NW-PF1200", "NW-HH300"]
    assert client.post(f"/api/workforce/credentials/{cred_id}/verify", json={"verification_status": "ISSUER_VERIFIED"}).status_code == 200
    assert client.post(f"/api/workforce/credentials/{cred_id}/renew", json={"expires_at": "2099-01-01"}).status_code == 200
    assert client.post(f"/api/workforce/credentials/{cred_id}/revoke", json={"reason": "audit"}).status_code == 200
    assert client.post(f"/api/workforce/credentials/{cred_id}/revoke", json={"reason": "audit"}).status_code == 409
    assert client.post(f"/api/workforce/workers/{worker_id}/training", json={"course_code": "LOTO-101"}).status_code == 201
    assert client.patch(f"/api/workforce/workers/{worker_id}", json={"reason": "move", "site_codes": ["WH-02"]}).status_code == 200
    assert client.post(f"/api/workforce/workers/{worker_id}/employment-status",
                       json={"status": "ON_LEAVE", "reason": "leave"}).status_code == 200
    history = client.get(f"/api/workforce/workers/{worker_id}/history").get_json()["history"]
    assert history[0]["action"] == "EMPLOYMENT_STATUS_CHANGED"
    assert client.get("/api/workforce/credential-definitions").get_json()["definitions"]
    assert client.get("/api/workforce/workers/E-404").status_code == 404


def test_change_feeds(client):
    first = client.get("/api/fleet/changes?limit=5").get_json()
    assert len(first["items"]) == 5 and first["has_more"]
    following = client.get(f"/api/fleet/changes?cursor={first['next_cursor']}&limit=5").get_json()
    assert following["items"][0]["seq"] > first["items"][-1]["seq"]
    assert client.get("/api/fleet/changes?cursor=bogus").status_code == 400
    workforce = client.get("/api/workforce/changes").get_json()
    assert workforce["items"] and all(i["aggregate_type"] == "WORKER" for i in workforce["items"])


def test_post_api_robots_accepts_model_code(client):
    made = client.post("/api/robots", json={"name": "Lifter-9", "model_code": "NW-PF1200"})
    assert made.status_code == 201
    robot = made.get_json()["robot"]
    assert robot["robot_class"] == "FORKLIFT" and robot["asset_id"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest backend/test_inventory_api.py -q`
Expected: failures — 404s from the catch-all static route (`assert 404 == 200`) and `KeyError: 'models'`.

- [ ] **Step 3: Implement**

Create `backend/inventory_api.py`:

```python
"""REST routes for the fleet-manager and workforce source systems.

Reads go straight to the inventory service; every mutation goes through
twin.fleet.mutate() so it runs under twin.lock (see backend/fleet_bridge.py).
Errors: NotFound → 404, Conflict → 409, any other ValueError/TypeError → 400,
in the same JSON shape as the rest of the API.
"""
from __future__ import annotations

import functools
from typing import Any, Dict, List, Optional

from flask import jsonify, request

from .inventory import Conflict, NotFound


def _list(value: Any) -> List[str]:
    """Accept a JSON list or a comma-separated string."""
    if value is None or value == "":
        return []
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    return list(value)


def _opt(data: Dict[str, Any], key: str) -> Optional[Any]:
    value = data.get(key)
    return None if value == "" else value


def register_inventory_routes(app, twin, api_error) -> None:
    service = twin.inventory
    fleet = twin.fleet

    def guarded(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except api_error:
                raise
            except NotFound as exc:
                raise api_error(str(exc), status=404)
            except Conflict as exc:
                raise api_error(str(exc), status=409)
            except (ValueError, TypeError) as exc:
                raise api_error(str(exc))
        return wrapper

    def body() -> Dict[str, Any]:
        data = request.get_json(silent=True)
        if data is None:
            data = request.form.to_dict() or {}
        if not isinstance(data, dict):
            raise api_error("Request body must be a JSON object")
        data.pop("actor", None)  # the actor is never client-supplied
        return data

    def limit(default: int = 200) -> int:
        try:
            return int(request.args.get("limit", default))
        except (TypeError, ValueError):
            raise api_error("limit must be an integer") from None

    def robot_payload(asset_id: str) -> Dict[str, Any]:
        record = service.get_robot(asset_id)
        floor = fleet.robot_for_asset(asset_id)
        record["floor_robot"] = ({"id": floor.id, "name": floor.name, "status": floor.status.value,
                                  "ota_installing": floor.ota_installing} if floor else None)
        return record

    def robot_response(change: Dict[str, Any], status: int = 200):
        return jsonify({"ok": True, "change": change, "robot": robot_payload(change["aggregate_id"])}), status

    def worker_response(change: Dict[str, Any], status: int = 200):
        return jsonify({"ok": True, "change": change, "worker": service.get_worker(change["aggregate_id"])}), status

    # ---- catalog -------------------------------------------------------- #
    @app.get("/api/fleet/catalog/models")
    @guarded
    def fleet_catalog_models():
        return jsonify({"ok": True, "models": service.list_models()})

    @app.get("/api/fleet/catalog/parts")
    @guarded
    def fleet_catalog_parts():
        return jsonify({"ok": True, "parts": service.list_parts(), "manufacturers": service.list_manufacturers()})

    @app.get("/api/fleet/releases")
    @guarded
    def fleet_releases():
        args = request.args
        return jsonify({"ok": True, "releases": service.list_releases(
            target_code=args.get("target") or None, status=args.get("status") or None,
            kind=args.get("kind") or None)})

    @app.get("/api/fleet/releases/<release_id>/sbom")
    @guarded
    def fleet_release_sbom(release_id: str):
        return jsonify(service.get_sbom(release_id))

    @app.post("/api/fleet/releases")
    @guarded
    def fleet_publish_release():
        data = body()
        change = fleet.mutate(service.publish_release, data.get("kind"), data.get("target_code"),
                              data.get("version"), min_hw_rev=_opt(data, "min_hw_rev"))
        return jsonify({"ok": True, "change": change, "release": service.get_release(change["subject_id"])}), 201

    @app.post("/api/fleet/releases/<release_id>/recall")
    @guarded
    def fleet_recall_release(release_id: str):
        change = fleet.mutate(service.recall_release, release_id, body().get("reason"))
        return jsonify({"ok": True, "change": change, "release": service.get_release(release_id)})

    # ---- robots --------------------------------------------------------- #
    @app.get("/api/fleet/robots")
    @guarded
    def fleet_robots():
        return jsonify({"ok": True, "robots": service.list_robots(
            site=request.args.get("site") or None, status=request.args.get("status") or None)})

    @app.post("/api/fleet/robots")
    @guarded
    def fleet_commission_robot():
        data = body()
        change = fleet.mutate(
            service.commission_robot, data.get("model_code"), data.get("site_code"), _opt(data, "home_zone"),
            serial_number=_opt(data, "serial_number"), asset_tag=_opt(data, "asset_tag"),
            hw_revision=_opt(data, "hw_revision"), software_release_id=_opt(data, "software_release_id"),
            fleet_id=_opt(data, "fleet_id"),
        )
        return robot_response(change, 201)

    @app.get("/api/fleet/robots/<asset_id>")
    @guarded
    def fleet_robot(asset_id: str):
        return jsonify({"ok": True, "robot": robot_payload(asset_id)})

    @app.patch("/api/fleet/robots/<asset_id>")
    @guarded
    def fleet_admin_edit(asset_id: str):
        data = body()
        reason = data.pop("reason", None)
        return robot_response(fleet.mutate(service.admin_edit, asset_id, data, reason))

    @app.get("/api/fleet/robots/<asset_id>/history")
    @guarded
    def fleet_robot_history(asset_id: str):
        return jsonify({"ok": True, "history": service.robot_history(asset_id, limit=limit())})

    @app.post("/api/fleet/robots/<asset_id>/status")
    @guarded
    def fleet_set_status(asset_id: str):
        data = body()
        return robot_response(fleet.mutate(service.set_lifecycle_status, asset_id, data.get("status"), data.get("reason")))

    @app.post("/api/fleet/robots/<asset_id>/decommission")
    @guarded
    def fleet_decommission(asset_id: str):
        return robot_response(fleet.mutate(service.decommission, asset_id, body().get("reason")))

    @app.post("/api/fleet/robots/<asset_id>/ota")
    @guarded
    def fleet_start_ota(asset_id: str):
        data = body()
        change = fleet.mutate(service.start_ota, asset_id, data.get("release_id"),
                              component_id=_opt(data, "component_id"))
        return robot_response(change, 201)

    @app.post("/api/fleet/ota/<job_id>/verify")
    @guarded
    def fleet_verify_ota(job_id: str):
        return robot_response(fleet.mutate(service.verify_ota, job_id, verified_by=_opt(body(), "verified_by")))

    @app.post("/api/fleet/ota/<job_id>/rollback")
    @guarded
    def fleet_rollback_ota(job_id: str):
        return robot_response(fleet.mutate(service.rollback_ota, job_id, body().get("reason")))

    @app.post("/api/fleet/components/<component_id>/calibrations")
    @guarded
    def fleet_record_calibration(component_id: str):
        data = body()
        change = fleet.mutate(service.record_calibration, component_id, data.get("result"),
                              performed_by=_opt(data, "performed_by"), method=_opt(data, "method") or "FIELD",
                              certificate_ref=_opt(data, "certificate_ref"))
        return robot_response(change, 201)

    @app.post("/api/fleet/robots/<asset_id>/work-orders")
    @guarded
    def fleet_open_work_order(asset_id: str):
        data = body()
        change = fleet.mutate(service.open_work_order, asset_id, data.get("type"), data.get("description"),
                              technician_id=_opt(data, "technician_id"))
        return robot_response(change, 201)

    @app.post("/api/fleet/work-orders/<wo_id>/swap")
    @guarded
    def fleet_swap_component(wo_id: str):
        data = body()
        return robot_response(fleet.mutate(
            service.swap_component, wo_id, data.get("slot"), performed_by=_opt(data, "performed_by"),
            hw_revision=_opt(data, "hw_revision"), part_number=_opt(data, "part_number")))

    @app.post("/api/fleet/work-orders/<wo_id>/close")
    @guarded
    def fleet_close_work_order(wo_id: str):
        return robot_response(fleet.mutate(service.close_work_order, wo_id, body().get("resolution")))

    @app.get("/api/fleet/changes")
    @guarded
    def fleet_changes():
        return jsonify({"ok": True, **service.changes(
            "fleet", cursor=request.args.get("cursor") or None, limit=request.args.get("limit", 100))})

    # ---- workforce ------------------------------------------------------ #
    @app.get("/api/workforce/workers")
    @guarded
    def workforce_workers():
        return jsonify({"ok": True, "workers": service.list_workers(
            site=request.args.get("site") or None, status=request.args.get("status") or None)})

    @app.post("/api/workforce/workers")
    @guarded
    def workforce_register_worker():
        data = body()
        display_name = data.pop("display_name", None)
        for key in ("role_codes", "site_codes"):
            if key in data:
                data[key] = _list(data[key])
        return worker_response(fleet.mutate(service.register_worker, display_name, **data), 201)

    @app.get("/api/workforce/workers/<worker_id>")
    @guarded
    def workforce_worker(worker_id: str):
        return jsonify({"ok": True, "worker": service.get_worker(worker_id)})

    @app.patch("/api/workforce/workers/<worker_id>")
    @guarded
    def workforce_update_worker(worker_id: str):
        data = body()
        reason = data.pop("reason", None)
        for key in ("role_codes", "site_codes"):
            if key in data:
                data[key] = _list(data[key])
        return worker_response(fleet.mutate(service.update_worker, worker_id, reason, **data))

    @app.get("/api/workforce/workers/<worker_id>/history")
    @guarded
    def workforce_worker_history(worker_id: str):
        return jsonify({"ok": True, "history": service.worker_history(worker_id, limit=limit())})

    @app.post("/api/workforce/workers/<worker_id>/employment-status")
    @guarded
    def workforce_employment_status(worker_id: str):
        data = body()
        return worker_response(fleet.mutate(service.set_employment_status, worker_id, data.get("status"),
                                            data.get("reason")))

    @app.post("/api/workforce/workers/<worker_id>/credentials")
    @guarded
    def workforce_issue_credential(worker_id: str):
        data = body()
        change = fleet.mutate(
            service.issue_credential, worker_id, data.get("code"), _opt(data, "issuer") or "Site Training Office",
            effective_from=_opt(data, "effective_from"), expires_at=_opt(data, "expires_at"),
            equipment_scope=_list(data.get("equipment_scope")), task_scope=_list(data.get("task_scope")),
            site_scope=_list(data.get("site_scope")), supervision_requirement=_opt(data, "supervision_requirement"),
            verification_status=_opt(data, "verification_status") or "SOURCE_VERIFIED",
            credential_number=_opt(data, "credential_number"),
        )
        return worker_response(change, 201)

    @app.post("/api/workforce/workers/<worker_id>/training")
    @guarded
    def workforce_record_training(worker_id: str):
        data = body()
        change = fleet.mutate(service.record_training, worker_id, data.get("course_code"),
                              _opt(data, "course_version") or "1", completed_at=_opt(data, "completed_at"),
                              expires_at=_opt(data, "expires_at"))
        return worker_response(change, 201)

    @app.get("/api/workforce/credential-definitions")
    @guarded
    def workforce_credential_definitions():
        return jsonify({"ok": True, "definitions": service.list_credential_definitions()})

    @app.post("/api/workforce/credentials/<credential_id>/renew")
    @guarded
    def workforce_renew_credential(credential_id: str):
        return worker_response(fleet.mutate(service.renew_credential, credential_id, _opt(body(), "expires_at")))

    @app.post("/api/workforce/credentials/<credential_id>/verify")
    @guarded
    def workforce_verify_credential(credential_id: str):
        return worker_response(fleet.mutate(service.verify_credential, credential_id,
                                            body().get("verification_status")))

    @app.post("/api/workforce/credentials/<credential_id>/revoke")
    @guarded
    def workforce_revoke_credential(credential_id: str):
        return worker_response(fleet.mutate(service.revoke_credential, credential_id, body().get("reason")))

    @app.get("/api/workforce/changes")
    @guarded
    def workforce_changes():
        return jsonify({"ok": True, **service.changes(
            "workforce", cursor=request.args.get("cursor") or None, limit=request.args.get("limit", 100))})
```

Modify `backend/app.py`:

1. Add the import after `from .event_system import Broadcaster`: `from .inventory_api import register_inventory_routes`.
2. In `create_app`, immediately after the `guarded` helper's `return wrapper` (before the `# Static dashboard` comment block) add: `register_inventory_routes(app, twin, ApiError)`.
3. In `create_robot()`, add two keyword arguments to `twin.add_robot(...)`:

```python
            model_code=data.get("model_code") or None,
            asset_id=data.get("asset_id") or None,
```

Modify `README.md` — add this section after the "Robot classes" section:

```markdown
## Fleet & workforce inventory

The twin also plays the *source systems* a real warehouse has: a **fleet
manager** (robot model catalog, serialized assets with a component tree,
calibration, maintenance work orders and over-the-air updates) and a
**workforce system** (workers, credentials, training). They live in
`backend/inventory/` with their own SQLite database (`data/inventory.sqlite3`;
tests use an in-memory copy) and their own IDs (`AST-000101`, `E-10001`).
Every live robot and operator is bound to its record by
`backend/fleet_bridge.py`. Every change is a lifecycle action that bumps a
revision and appends to a cursor-based change feed — the feed
physical-work-assurance ingests to keep robot and worker passports current.

All manufacturers are fictional; specs, sensors and standards are modelled
on real product categories. Worker records hold only the PWA allowlist
(no pay, performance, health or demographic data) and credential numbers
only as hashes.

Open the **Fleet & workforce** page from the dashboard header (`/fleet.html`).

| Endpoint | What it does |
|---|---|
| `GET /api/fleet/catalog/models`, `/parts`, `GET /api/fleet/releases` | Model catalog, part catalog, software/firmware/AI-policy releases |
| `GET /api/fleet/releases/<id>/sbom` | The release's CycloneDX 1.5 SBOM |
| `POST /api/fleet/releases`, `POST …/<id>/recall` | Publish / recall a release |
| `GET/POST /api/fleet/robots`, `GET/PATCH /api/fleet/robots/<asset>` | List, commission, read, correct a robot asset |
| `POST /api/fleet/robots/<asset>/status`, `/decommission` | Lifecycle changes (stop/resume the floor robot) |
| `POST /api/fleet/robots/<asset>/ota`, `POST /api/fleet/ota/<job>/verify`, `/rollback` | OTA updates (install progresses over simulator ticks) |
| `POST /api/fleet/robots/<asset>/work-orders`, `POST /api/fleet/work-orders/<wo>/swap`, `/close` | Maintenance and part swaps |
| `POST /api/fleet/components/<component>/calibrations` | Record a calibration |
| `GET/POST /api/workforce/workers`, `GET/PATCH /api/workforce/workers/<id>` | Workers |
| `POST /api/workforce/workers/<id>/credentials`, `/training`, `/employment-status` | Credentials, training, status |
| `POST /api/workforce/credentials/<id>/renew`, `/verify`, `/revoke` | Credential lifecycle |
| `GET /api/fleet/changes`, `GET /api/workforce/changes` | Change feeds (`?cursor=<epoch>:<seq>&limit=`) |
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest backend/test_inventory_api.py -q`
Expected: `6 passed`.

Then the whole suite: `.venv/bin/python -m pytest -q` → `340 passed, 2 failed` (only the two pre-existing failures).

- [ ] **Step 5: Commit**

```bash
git add backend/inventory_api.py backend/app.py backend/test_inventory_api.py README.md
git commit -m "feat: REST API for the fleet manager and workforce source systems

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 12: Fleet & Workforce dashboard page

**Files:**
- Create: `frontend/fleet.html`, `frontend/fleet.css`, `frontend/fleet.js`
- Modify: `frontend/index.html` (header link), `frontend/style.css` (one rule), `frontend/app.js:491` (UPDATING chip)
- Test: `backend/test_inventory_api.py` (append one test) + manual browser check

**Interfaces:**
- Consumes: every route from Task 11. The page is served by the existing `/<path:filename>` static route.
- Produces: `/fleet.html` with three views (Robots, Workers, Change feed), a detail drawer per robot/worker with lifecycle-action forms, and a live change feed. Forms are generic: `<form data-method data-url [data-open]>`; inputs with `data-list` are sent as comma-split arrays; empty inputs are omitted.

- [ ] **Step 1: Write the failing test** — append to `backend/test_inventory_api.py`:

```python
def test_fleet_page_is_served_and_linked(client):
    page = client.get("/fleet.html")
    assert page.status_code == 200 and b'src="fleet.js"' in page.data
    assert client.get("/fleet.js").status_code == 200
    assert client.get("/fleet.css").status_code == 200
    assert b'href="/fleet.html"' in client.get("/").data
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest backend/test_inventory_api.py::test_fleet_page_is_served_and_linked -q`
Expected: FAIL — `assert 404 == 200`.

- [ ] **Step 3: Implement**

`frontend/fleet.html`:

```html
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Fleet &amp; Workforce — Warehouse Digital Twin</title>
<link rel="preconnect" href="https://fonts.googleapis.com" />
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
<link href="https://fonts.googleapis.com/css2?family=Saira+Condensed:wght@500;600;700&family=Archivo:wght@400;500;600&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet" />
<link rel="stylesheet" href="style.css" />
<link rel="stylesheet" href="fleet.css" />
</head>
<body class="fleet-page">

<header class="topbar">
  <div class="identity">
    <span class="tape" aria-hidden="true"></span>
    <div>
      <p class="eyebrow">Source systems &middot; fleet manager + workforce</p>
      <h1>Fleet &amp; Workforce</h1>
    </div>
  </div>
  <nav class="fleet-nav" aria-label="Views">
    <button class="btn small tab active" type="button" data-view="robots">Robots <span class="count" id="robotCount"></span></button>
    <button class="btn small tab" type="button" data-view="workers">Workers <span class="count" id="workerCount"></span></button>
    <button class="btn small tab" type="button" data-view="changes">Change feed</button>
    <a class="btn small" href="/">&larr; Control centre</a>
  </nav>
</header>

<main class="fleet-shell">
  <section class="panel fleet-view" id="view-robots">
    <h2 class="panel-title">Robot assets</h2>
    <p class="map-foot">Every robot the fleet manager knows about, on this floor (WH-01) or elsewhere. Declared values are what the registry believes; reported values are what the robot last said.</p>
    <div class="fleet-filters">
      <label class="field"><span>Search</span><input id="robotSearch" placeholder="asset, serial, model" /></label>
      <label class="field"><span>Site</span><select id="robotSite"><option value="">All sites</option></select></label>
      <label class="field"><span>Lifecycle</span><select id="robotStatus"><option value="">Any</option></select></label>
      <span class="spacer"></span>
      <button class="btn small primary" type="button" id="commissionBtn">Commission robot</button>
    </div>
    <div class="table-wrap">
      <table class="data-table" id="robotTable">
        <thead><tr><th>Asset</th><th>Model</th><th>Serial</th><th>Site</th><th>Lifecycle</th><th>Software</th><th>Calibration</th><th>Last report</th></tr></thead>
        <tbody><tr><td colspan="8" class="empty">Loading&hellip;</td></tr></tbody>
      </table>
    </div>
  </section>

  <section class="panel fleet-view" id="view-workers" hidden>
    <h2 class="panel-title">Workers</h2>
    <p class="map-foot">Qualification records only &mdash; no pay, performance, health or demographic data is ever stored.</p>
    <div class="fleet-filters">
      <label class="field"><span>Search</span><input id="workerSearch" placeholder="id, name, role" /></label>
      <label class="field"><span>Status</span><select id="workerStatus"><option value="">Any</option><option>ACTIVE</option><option>ON_LEAVE</option><option>TERMINATED</option></select></label>
      <span class="spacer"></span>
      <button class="btn small primary" type="button" id="registerBtn">Register worker</button>
    </div>
    <div class="table-wrap">
      <table class="data-table" id="workerTable">
        <thead><tr><th>Worker</th><th>Name</th><th>Type</th><th>Roles</th><th>Sites</th><th>Status</th><th>Credentials</th></tr></thead>
        <tbody><tr><td colspan="7" class="empty">Loading&hellip;</td></tr></tbody>
      </table>
    </div>
  </section>

  <section class="panel fleet-view" id="view-changes" hidden>
    <h2 class="panel-title">Change feed</h2>
    <p class="map-foot">The revisioned feed (<code>/api/fleet/changes</code>, <code>/api/workforce/changes</code>) a passport system ingests. Newest first.</p>
    <ul class="change-feed" id="changeFeed"><li class="sub">Loading&hellip;</li></ul>
  </section>
</main>

<aside class="drawer" id="drawer" role="dialog" aria-labelledby="drawerTitle" hidden>
  <div class="drawer-head">
    <h2 id="drawerTitle"></h2>
    <button class="btn small" type="button" id="drawerClose">Close</button>
  </div>
  <div class="drawer-body" id="drawerBody"></div>
</aside>

<div class="toast" id="toast" role="status" hidden></div>

<script src="fleet.js"></script>
</body>
</html>
```

`frontend/fleet.css`:

```css
/* Fleet & Workforce page — layered on style.css (same tokens and components). */
.fleet-page .topbar { align-items: center; }
.fleet-nav { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; margin-left: auto; }
.fleet-nav .tab.active { background: var(--amber); border-color: var(--amber); color: #17120a; }
.fleet-nav .tab.active .count { color: #17120a; }
.fleet-shell { max-width: 1500px; margin: 0 auto; padding: var(--gap); }
.fleet-filters { display: flex; flex-wrap: wrap; gap: 8px; align-items: flex-end; margin: 8px 0 10px; }
.fleet-filters .field { margin-bottom: 0; min-width: 160px; }
.fleet-filters .spacer { flex: 1; }
.data-table tr.clickable { cursor: pointer; }
.data-table tr.clickable:hover td { background: var(--raised); }
.sub { color: var(--muted); font-size: 11px; }
.mono { font-family: var(--mono); font-size: 12px; }
.chips { display: flex; flex-wrap: wrap; gap: 4px; }

.drawer {
  position: fixed; top: 0; right: 0; bottom: 0; z-index: 60;
  width: min(820px, 100vw);
  display: flex; flex-direction: column;
  background: var(--panel); border-left: 1px solid var(--line);
  box-shadow: -12px 0 40px rgba(0, 0, 0, 0.45);
}
.drawer[hidden] { display: none; }
.drawer-head {
  display: flex; align-items: center; justify-content: space-between; gap: 8px;
  padding: 14px 16px; border-bottom: 1px solid var(--line);
}
.drawer-head h2 { margin: 0; font-family: var(--display); font-size: 20px; letter-spacing: 0.04em; }
.drawer-body { overflow-y: auto; padding: 4px 16px 40px; }
.drawer-section { border-top: 1px solid var(--line-soft); padding: 12px 0; }
.drawer-section:first-child { border-top: 0; }
.drawer-section h3 {
  margin: 0 0 8px; font-family: var(--display); font-size: 13px; font-weight: 600;
  letter-spacing: 0.12em; text-transform: uppercase; color: var(--ink-2);
}
.kv { display: grid; grid-template-columns: max-content 1fr; gap: 4px 14px; margin: 0; font-size: 12.5px; }
.kv dt { color: var(--muted); }
.kv dd { margin: 0; color: var(--ink); overflow-wrap: anywhere; }

.inline-form { display: flex; flex-wrap: wrap; gap: 6px; align-items: flex-end; margin-top: 8px; }
.inline-form .field { flex: 1 1 130px; margin-bottom: 0; }
.inline-form .btn { flex: 0 0 auto; }
.row-actions { display: flex; flex-direction: column; gap: 4px; min-width: 220px; }
.row-actions .inline-form { margin-top: 0; flex-wrap: nowrap; }
.row-actions .field { flex: 1 1 80px; }
.row-actions .field > span { display: none; }

.change-feed { list-style: none; margin: 0; padding: 0; font-size: 12.5px; }
.change-feed li {
  display: grid; grid-template-columns: 80px 110px 1fr; gap: 8px;
  padding: 6px 4px; border-bottom: 1px solid var(--line-soft);
}
.change-feed .what { color: var(--ink); }
.change-feed .diff { color: var(--muted); font-family: var(--mono); font-size: 11px; overflow-wrap: anywhere; }

.toast {
  position: fixed; bottom: 18px; left: 50%; transform: translateX(-50%); z-index: 80;
  padding: 8px 14px; border: 1px solid var(--line); border-radius: var(--r);
  background: var(--raised); color: var(--ink); max-width: calc(100vw - 32px);
}
.toast.bad { border-color: var(--fault); color: var(--fault); }

@media (max-width: 720px) {
  .fleet-shell { padding: 16px; }
  .change-feed li { grid-template-columns: 1fr; }
  .kv { grid-template-columns: 1fr; }
  .row-actions .inline-form { flex-wrap: wrap; }
}
```

`frontend/fleet.js`:

```js
/* Fleet & Workforce — the twin's fleet-manager and workforce source systems.
 *
 * Reads /api/fleet/* and /api/workforce/* (backend/inventory_api.py) and polls
 * every few seconds. Every change goes through a lifecycle-action endpoint, so
 * each one lands in the inventory's change feed exactly the way a passport
 * system ingests it. No framework; same styling as the control centre.
 */
(function () {
  "use strict";

  var POLL_MS = 3000;
  var FEED_MAX = 150;
  var LIFECYCLE = ["COMMISSIONING", "IN_SERVICE", "MAINTENANCE", "OUT_OF_SERVICE", "DECOMMISSIONED"];
  var TONES = {
    VALID: "ok", IN_SERVICE: "ok", ONLINE: "ok", OK: "ok", ACTIVE: "ok", VERIFIED: "ok",
    DUE_SOON: "wait", EXPIRING_SOON: "wait", MAINTENANCE: "wait", DEGRADED: "wait", ON_LEAVE: "wait",
    INTERMITTENT: "wait", ROLLED_BACK: "wait",
    STAGED: "run", DOWNLOADING: "run", INSTALLING: "run", REPORTED: "run", OPEN: "run",
    COMMISSIONING: "info", NOT_YET_EFFECTIVE: "info",
    EXPIRED: "bad", MISSING: "bad", FAILED: "bad", REVOKED: "bad", FAULT: "bad", OFFLINE: "bad",
    OUT_OF_SERVICE: "bad",
    DECOMMISSIONED: "idle", TERMINATED: "idle", WORKER_INACTIVE: "idle", CLOSED: "idle", UNKNOWN: "idle"
  };

  var state = {
    view: "robots",
    robots: [], workers: [], models: [], releases: [], definitions: [],
    drawer: null,
    feed: [], cursors: { fleet: null, workforce: null }
  };

  // ---------------------------------------------------------------- helpers
  function $(id) { return document.getElementById(id); }

  function esc(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function api(method, url, body) {
    var options = { method: method, headers: { "Content-Type": "application/json" } };
    if (body !== undefined) options.body = JSON.stringify(body);
    return fetch(url, options).then(function (response) {
      return response.json().catch(function () { return {}; }).then(function (data) {
        if (!response.ok || data.ok === false) throw new Error(data.error || ("HTTP " + response.status));
        return data;
      });
    });
  }

  function toast(message, bad) {
    var node = $("toast");
    node.textContent = message;
    node.className = "toast" + (bad ? " bad" : "");
    node.hidden = false;
    clearTimeout(toast.timer);
    toast.timer = setTimeout(function () { node.hidden = true; }, 3500);
  }

  function ago(iso) {
    if (!iso) return "never";
    var seconds = Math.max(0, Math.round((Date.now() - Date.parse(iso)) / 1000));
    if (seconds < 90) return seconds + "s ago";
    if (seconds < 5400) return Math.round(seconds / 60) + " min ago";
    if (seconds < 172800) return Math.round(seconds / 3600) + " h ago";
    return Math.round(seconds / 86400) + " d ago";
  }

  function day(iso) { return iso ? String(iso).slice(0, 10) : "—"; }
  function words(text) { return String(text || "").replace(/_/g, " ").toLowerCase(); }
  function chip(text, tone) { return '<span class="chip ' + (tone || "idle") + '">' + esc(text) + "</span>"; }

  function statusChip(value) {
    if (!value) return '<span class="sub">—</span>';
    return chip(String(value).replace(/_/g, " "), TONES[value] || "info");
  }

  function shortHash(hash) { return hash ? String(hash).replace("sha256:", "").slice(0, 12) + "…" : "—"; }

  function kv(pairs) {
    return '<dl class="kv">' + pairs.map(function (pair) {
      var value = pair[1];
      var html = value && typeof value === "object" && "html" in value
        ? value.html : esc(value == null || value === "" ? "—" : value);
      return "<dt>" + esc(pair[0]) + "</dt><dd>" + html + "</dd>";
    }).join("") + "</dl>";
  }

  function section(title, body) {
    return '<section class="drawer-section"><h3>' + esc(title) + "</h3>" + body + "</section>";
  }

  function table(headers, rows, empty) {
    var head = headers.map(function (h) { return "<th>" + esc(h) + "</th>"; }).join("");
    var body = rows.length ? rows.join("")
      : '<tr><td colspan="' + headers.length + '" class="empty">' + esc(empty || "None.") + "</td></tr>";
    return '<div class="table-wrap"><table class="data-table"><thead><tr>' + head + "</tr></thead><tbody>" + body + "</tbody></table></div>";
  }

  function options(values, selected) {
    return values.map(function (v) {
      var value = Array.isArray(v) ? v[0] : v;
      var label = Array.isArray(v) ? v[1] : v;
      return '<option value="' + esc(value) + '"' + (value === selected ? " selected" : "") + ">" + esc(label) + "</option>";
    }).join("");
  }

  function input(name, label, attrs) {
    return '<label class="field"><span>' + esc(label) + '</span><input name="' + esc(name) + '" aria-label="' + esc(label) + '" ' + (attrs || "") + " /></label>";
  }

  function select(name, label, opts) {
    return '<label class="field"><span>' + esc(label) + '</span><select name="' + esc(name) + '" aria-label="' + esc(label) + '">' + opts + "</select></label>";
  }

  function hidden(name, value) { return '<input type="hidden" name="' + esc(name) + '" value="' + esc(value) + '" />'; }

  /* A form the generic submit handler sends. `open` = "robot" | "worker" opens the result. */
  function form(method, url, label, fields, tone, open) {
    return '<form class="inline-form" data-method="' + method + '" data-url="' + esc(url) + '"' +
      (open ? ' data-open="' + open + '"' : "") + ">" + fields.join("") +
      '<button class="btn small' + (tone ? " " + tone : "") + '" type="submit">' + esc(label) + "</button></form>";
  }

  function describe(change) {
    return change ? change.aggregate_id + ": " + words(change.action) : "Saved";
  }

  function fmt(value) {
    if (value === null || value === undefined) return "∅";
    if (typeof value === "object") return JSON.stringify(value);
    return String(value);
  }

  function feedItem(change) {
    var keys = Object.keys(change.diff || {}).filter(function (k) {
      return !/(^|\.)(updated_at|revision|reported_at|observation_seq)$/.test(k);
    });
    var summary = keys.slice(0, 3).map(function (k) {
      return k + ": " + fmt(change.diff[k].from) + " → " + fmt(change.diff[k].to);
    }).join("; ") + (keys.length > 3 ? "  (+" + (keys.length - 3) + " more)" : "");
    var who = change.actor && change.actor.id ? ' <span class="sub">by ' + esc(change.actor.id) + "</span>" : "";
    var why = change.reason ? ' <span class="sub">— ' + esc(change.reason) + "</span>" : "";
    return '<li><span class="sub">' + esc(ago(change.occurred_at)) + '</span><span class="mono">' +
      esc(change.aggregate_id) + '</span><span><span class="what">' + esc(words(change.action)) + "</span>" +
      who + why + '<div class="diff">' + esc(summary) + "</div></span></li>";
  }

  function releaseLabel(release) {
    return release.version + (release.status === "CURRENT" ? " (current)" : " (" + words(release.status) + ")");
  }

  // ------------------------------------------------------------------ lists
  function matches(text, needle) { return !needle || String(text).toLowerCase().indexOf(needle) !== -1; }

  function renderRobots() {
    var sites = {};
    state.robots.forEach(function (r) { sites[r.site_code] = true; });
    var siteSelect = $("robotSite");
    var siteList = Object.keys(sites).sort();
    if (siteSelect.dataset.sites !== siteList.join(",")) {
      var current = siteSelect.value;
      siteSelect.innerHTML = '<option value="">All sites</option>' + options(siteList, current);
      siteSelect.dataset.sites = siteList.join(",");
    }
    var needle = $("robotSearch").value.trim().toLowerCase();
    var site = siteSelect.value;
    var status = $("robotStatus").value;
    var rows = state.robots.filter(function (r) {
      return (!site || r.site_code === site) && (!status || r.lifecycle_status === status) &&
        matches(r.asset_id + " " + r.serial_number + " " + r.model_name + " " + r.model_code, needle);
    }).map(function (r) {
      var f = r.flags;
      var software = '<span class="mono">' + esc(r.declared_software_version) + "</span>";
      if (f.software_mismatch) software += " " + chip("reports " + (r.reported_software_version || "?"), "bad");
      if (f.running_recalled_release) software += " " + chip("recalled", "bad");
      if (f.active_ota_job) software += " " + statusChip(f.active_ota_job.state);
      var report = r.lifecycle_status === "DECOMMISSIONED" ? '<span class="sub">retired</span>'
        : (f.report_stale ? chip("stale", "bad") : '<span class="sub">' + esc(ago(r.reported_at)) + "</span>") +
          " " + statusChip(r.connectivity);
      return '<tr class="clickable" data-asset="' + esc(r.asset_id) + '"><td class="mono">' + esc(r.asset_id) +
        "</td><td>" + esc(r.model_name) + '<div class="sub">' + esc(r.manufacturer_name) + " · " + esc(r.embodiment_class) +
        '</div></td><td class="mono">' + esc(r.serial_number) + "</td><td>" + esc(r.site_code) + "</td><td>" +
        statusChip(r.lifecycle_status) + "</td><td>" + software + "</td><td>" + statusChip(f.calibration_worst) +
        "</td><td>" + report + "</td></tr>";
    });
    $("robotTable").querySelector("tbody").innerHTML = rows.join("") ||
      '<tr><td colspan="8" class="empty">No robots match.</td></tr>';
    $("robotCount").textContent = state.robots.length;
  }

  function renderWorkers() {
    var needle = $("workerSearch").value.trim().toLowerCase();
    var status = $("workerStatus").value;
    var rows = state.workers.filter(function (w) {
      return (!status || w.employment_status === status) &&
        matches(w.worker_id + " " + w.display_name + " " + w.role_codes.join(" "), needle);
    }).map(function (w) {
      var credentials = w.credentials.map(function (c) { return chip(words(c.code), TONES[c.validity] || "info"); }).join(" ");
      return '<tr class="clickable" data-worker="' + esc(w.worker_id) + '"><td class="mono">' + esc(w.worker_id) +
        "</td><td>" + esc(w.display_name) + "</td><td>" + esc(words(w.worker_type)) + '<div class="sub">' +
        esc(w.organization) + "</div></td><td>" + esc(w.role_codes.join(", ")) + "</td><td>" + esc(w.site_codes.join(", ")) +
        "</td><td>" + statusChip(w.employment_status) + '</td><td><div class="chips">' + (credentials || '<span class="sub">none</span>') +
        "</div></td></tr>";
    });
    $("workerTable").querySelector("tbody").innerHTML = rows.join("") ||
      '<tr><td colspan="7" class="empty">No workers match.</td></tr>';
    $("workerCount").textContent = state.workers.length;
  }

  function renderFeed() {
    $("changeFeed").innerHTML = state.feed.map(feedItem).join("") || '<li class="sub">No changes yet.</li>';
  }

  // ----------------------------------------------------------- robot drawer
  function robotDrawer(robot, history) {
    var f = robot.flags;
    var reported = robot.reported || {};
    var base = "/api/fleet/robots/" + encodeURIComponent(robot.asset_id);
    var live = robot.lifecycle_status !== "DECOMMISSIONED";
    var floor = robot.floor_robot;

    var identity = kv([
      ["Serial number", robot.serial_number], ["Asset tag", robot.asset_tag],
      ["Model", robot.model.name + " · " + robot.model_code], ["Manufacturer", robot.manufacturer_name],
      ["Class", robot.model.embodiment_class], ["Hardware revision", robot.hw_revision],
      ["Fleet", robot.fleet_id], ["Site / home zone", robot.site_code + " / " + (robot.home_zone || "—")],
      ["Commissioned", day(robot.commissioned_at)], ["Lifecycle", { html: statusChip(robot.lifecycle_status) }],
      ["Record revision", robot.revision],
      ["On this floor as", floor ? floor.name + " — " + (floor.ota_installing ? "UPDATING" : floor.status) : "not on the simulated floor"]
    ]);
    var lifecycle = live ? form("POST", base + "/status", "Set status", [
      select("status", "Lifecycle", options(["IN_SERVICE", "MAINTENANCE", "OUT_OF_SERVICE"].filter(function (s) {
        return s !== robot.lifecycle_status;
      }))),
      input("reason", "Reason", 'required placeholder="why"')
    ]) + form("POST", base + "/decommission", "Decommission", [
      input("reason", "Reason", 'required placeholder="why it is retired"')
    ], "estop") : "";

    var spec = robot.model.spec || {};
    var specPairs = Object.keys(spec).map(function (key) {
      var value = spec[key];
      if (value && typeof value === "object" && !Array.isArray(value)) {
        value = Object.keys(value).map(function (k) { return words(k) + " " + value[k]; }).join(", ");
      } else if (Array.isArray(value)) {
        value = value.join(", ");
      }
      return [words(key), value];
    });
    specPairs.push(["safety standards", (robot.model.safety_standards || []).join(", ")]);

    var mismatch = function (bad) { return bad ? chip("mismatch", "bad") : chip("match", "ok"); };
    var softwareRows = [
      "<tr><td>Robot software</td><td class=\"mono\">" + esc(robot.declared_software_version) + '</td><td class="mono">' +
        esc(reported.software_version) + "</td><td>" + mismatch(f.software_mismatch) +
        (f.running_recalled_release ? " " + chip("recalled", "bad") : "") +
        (f.running_unknown_software ? " " + chip("unknown build", "bad") : "") + "</td></tr>"
    ];
    if (robot.declared_ai_policy_version || reported.ai_policy_version) {
      softwareRows.push("<tr><td>AI policy model</td><td class=\"mono\">" + esc(robot.declared_ai_policy_version) +
        '</td><td class="mono">' + esc(reported.ai_policy_version) + "</td><td>" + mismatch(f.ai_policy_mismatch) + "</td></tr>");
    }
    softwareRows.push('<tr><td>Operating system</td><td class="sub">—</td><td class="mono">' + esc(reported.os_version) + "</td><td></td></tr>");
    softwareRows.push('<tr><td>Config hash</td><td class="mono">' + esc(shortHash(robot.config_hash)) + '</td><td class="mono">' +
      esc(shortHash(reported.config_hash)) + "</td><td>" + mismatch(robot.config_hash !== reported.config_hash) + "</td></tr>");
    softwareRows.push('<tr><td>Safety policy hash</td><td class="mono">' + esc(shortHash(robot.safety_policy_hash)) + '</td><td class="mono">' +
      esc(shortHash(reported.safety_policy_hash)) + "</td><td>" + mismatch(robot.safety_policy_hash !== reported.safety_policy_hash) + "</td></tr>");

    var robotReleases = state.releases.filter(function (r) {
      return r.target_code === robot.model_code && r.status !== "RECALLED";
    }).map(function (r) {
      return [r.release_id, (r.kind === "AI_POLICY_MODEL" ? "AI policy " : "Software ") + releaseLabel(r)];
    });
    var otaForm = live && robotReleases.length ? form("POST", base + "/ota", "Start OTA", [
      select("release_id", "Release", options(robotReleases))
    ]) : "";
    var jobs = robot.ota_jobs.map(function (job) {
      var jobUrl = "/api/fleet/ota/" + encodeURIComponent(job.job_id);
      var actions = job.state !== "REPORTED" ? "" : '<div class="row-actions">' +
        form("POST", jobUrl + "/verify", "Verify", [input("verified_by", "Verified by", 'placeholder="E-10003"')]) +
        form("POST", jobUrl + "/rollback", "Roll back", [input("reason", "Reason", 'required placeholder="reason"')]) + "</div>";
      return '<tr><td class="mono">' + esc(job.job_id) + "</td><td>" + esc(job.slot ? words(job.slot) : words(job.kind)) +
        '</td><td class="mono">' + esc(job.from_version) + " → " + esc(job.version) + "</td><td>" + statusChip(job.state) +
        (job.failure_reason ? '<div class="sub">' + esc(job.failure_reason) + "</div>" : "") + "</td><td>" + actions + "</td></tr>";
    });

    var components = robot.components.map(function (c) {
      var firmware = c.declared_firmware_version
        ? '<span class="mono">' + esc(c.declared_firmware_version) + "</span>" +
          (c.reported_firmware_version !== c.declared_firmware_version ? " " + chip("reports " + (c.reported_firmware_version || "?"), "bad") : "")
        : '<span class="sub">n/a</span>';
      var latest = c.calibrations && c.calibrations[0];
      var calibration = c.calibration_status
        ? statusChip(c.calibration_status) + (latest && latest.valid_until ? '<div class="sub">until ' + esc(day(latest.valid_until)) + "</div>" : "")
        : '<span class="sub">n/a</span>';
      var actions = [];
      if (live && c.calibration_status) {
        actions.push(form("POST", "/api/fleet/components/" + encodeURIComponent(c.component_id) + "/calibrations", "Calibrate", [
          select("result", "Result", options(["PASS", "FAIL"])),
          input("performed_by", "By", 'placeholder="E-10003"')
        ]));
      }
      var partReleases = state.releases.filter(function (r) { return r.target_code === c.part_number && r.status !== "RECALLED"; });
      if (live && partReleases.length) {
        actions.push(form("POST", base + "/ota", "Flash", [
          select("release_id", "Firmware", options(partReleases.map(function (r) { return [r.release_id, releaseLabel(r)]; }))),
          hidden("component_id", c.component_id)
        ]));
      }
      return "<tr><td>" + esc(words(c.slot)) + "</td><td>" + esc(c.part_name) + '<div class="sub mono">' + esc(c.part_number) +
        " · rev " + esc(c.hw_revision) + '</div></td><td class="mono">' + esc(c.serial) + "</td><td>" + firmware +
        "</td><td>" + calibration + '</td><td><div class="row-actions">' + actions.join("") + "</div></td></tr>";
    });

    var slots = robot.components.map(function (c) { return [c.slot, words(c.slot)]; });
    var workOrders = robot.work_orders.map(function (wo) {
      var woUrl = "/api/fleet/work-orders/" + encodeURIComponent(wo.wo_id);
      var actions = wo.status !== "OPEN" ? "" : '<div class="row-actions">' +
        form("POST", woUrl + "/swap", "Swap part", [
          select("slot", "Slot", options(slots)), input("performed_by", "By", 'placeholder="E-10003"'),
          input("hw_revision", "HW rev", 'placeholder="latest"')
        ]) +
        form("POST", woUrl + "/close", "Close", [input("resolution", "Resolution", 'required placeholder="what was done"')]) + "</div>";
      return '<tr><td class="mono">' + esc(wo.wo_id) + "</td><td>" + esc(words(wo.type)) + "</td><td>" + esc(wo.description) +
        '<div class="sub">' + esc(day(wo.opened_at)) + (wo.technician_id ? " · " + esc(wo.technician_id) : "") +
        (wo.resolution ? " · " + esc(wo.resolution) : "") + "</div></td><td>" + statusChip(wo.status) + "</td><td>" + actions + "</td></tr>";
    });
    var openWorkOrder = live ? form("POST", base + "/work-orders", "Open work order", [
      select("type", "Type", options(["CORRECTIVE", "PREVENTIVE", "INSPECTION"])),
      input("description", "Description", 'required placeholder="what needs doing"'),
      input("technician_id", "Technician", 'placeholder="E-10003"')
    ]) : "";

    var battery = reported.battery;
    var reportedState = kv([
      ["Health", { html: statusChip(reported.health_state) }],
      ["Connectivity", { html: statusChip(reported.connectivity) }],
      ["Operational mode", reported.operational_mode], ["Zone", reported.zone],
      ["Battery", battery ? battery.soc_pct + "% charge · " + battery.soh_pct + "% health · " + battery.cycle_count + " cycles" : "mains powered"],
      ["Last report", ago(reported.reported_at) + (f.report_stale ? " (stale)" : "")]
    ]);

    var correction = live ? form("PATCH", base, "Save correction", [
      input("fleet_id", "Fleet", 'placeholder="' + esc(robot.fleet_id || "") + '"'),
      input("home_zone", "Home zone", 'placeholder="' + esc(robot.home_zone || "") + '"'),
      input("asset_tag", "Asset tag", 'placeholder="' + esc(robot.asset_tag) + '"'),
      input("reason", "Reason", 'required placeholder="why"')
    ]) : "";

    return section("Identity", identity + lifecycle) +
      section("Spec sheet", kv(specPairs)) +
      section("Software — declared vs reported", table(["", "Declared", "Reported", ""], softwareRows) + otaForm +
        table(["Job", "Target", "Version", "State", ""], jobs, "No updates yet.")) +
      section("Components", table(["Slot", "Part", "Serial", "Firmware", "Calibration", ""], components)) +
      section("Maintenance", table(["Work order", "Type", "Description", "Status", ""], workOrders, "No work orders.") + openWorkOrder) +
      section("Last report from the robot", reportedState) +
      section("Registry correction", '<p class="sub">Admin edits are logged with their reason.</p>' + correction) +
      section("History", '<ul class="change-feed">' + history.map(feedItem).join("") + "</ul>");
  }

  // ---------------------------------------------------------- worker drawer
  function workerDrawer(worker, history) {
    var base = "/api/workforce/workers/" + encodeURIComponent(worker.worker_id);
    var identity = kv([
      ["Type", words(worker.worker_type)], ["Organisation", worker.organization],
      ["Roles", worker.role_codes.join(", ")], ["Sites", worker.site_codes.join(", ")],
      ["Supervisor", worker.supervisor_id], ["Employment", { html: statusChip(worker.employment_status) }],
      ["Valid credentials", worker.valid_credential_codes.join(", ")], ["Record revision", worker.revision]
    ]);
    var statusForm = form("POST", base + "/employment-status", "Set status", [
      select("status", "Employment", options(["ACTIVE", "ON_LEAVE", "TERMINATED"].filter(function (s) {
        return s !== worker.employment_status;
      }))),
      input("reason", "Reason", 'required placeholder="why"')
    ]);
    var updateForm = form("PATCH", base, "Update", [
      input("role_codes", "Roles", 'data-list="1" placeholder="' + esc(worker.role_codes.join(", ")) + '"'),
      input("site_codes", "Sites", 'data-list="1" placeholder="' + esc(worker.site_codes.join(", ")) + '"'),
      input("supervisor_id", "Supervisor", 'placeholder="' + esc(worker.supervisor_id || "E-…") + '"'),
      input("reason", "Reason", 'required placeholder="why"')
    ]);
    var credentials = worker.credentials.map(function (c) {
      var url = "/api/workforce/credentials/" + encodeURIComponent(c.credential_id);
      var actions = c.verification_status === "REVOKED" ? "" : '<div class="row-actions">' +
        form("POST", url + "/renew", "Renew", [input("expires_at", "New expiry", 'type="date"')]) +
        form("POST", url + "/verify", "Verify", [select("verification_status", "Level", options(
          ["ISSUER_VERIFIED", "SOURCE_VERIFIED", "UNVERIFIED"].filter(function (s) { return s !== c.verification_status; })))]) +
        form("POST", url + "/revoke", "Revoke", [input("reason", "Reason", 'required placeholder="reason"')], "estop") + "</div>";
      var scope = [].concat(c.equipment_scope || [], c.site_scope || [], c.task_scope || []).join(", ");
      return "<tr><td>" + esc(c.name) + '<div class="sub mono">' + esc(c.credential_id) + "</div></td><td>" + esc(c.issuer) +
        '<div class="sub">' + esc(words(c.verification_status)) + "</div></td><td>" + statusChip(c.validity) +
        '<div class="sub">' + esc(day(c.effective_from)) + " → " + esc(day(c.expires_at)) + "</div></td><td>" +
        esc(scope || "any") + "</td><td>" + actions + "</td></tr>";
    });
    var issue = form("POST", base + "/credentials", "Issue credential", [
      select("code", "Credential", options(state.definitions.map(function (d) { return [d.code, d.name]; }))),
      input("issuer", "Issuer", 'placeholder="Site Training Office"'),
      input("expires_at", "Expires", 'type="date"'),
      input("equipment_scope", "Equipment", 'data-list="1" placeholder="NW-PF1200"'),
      input("site_scope", "Sites", 'data-list="1" placeholder="WH-01"')
    ]);
    var training = worker.training.map(function (t) {
      return "<tr><td>" + esc(t.course_code) + " v" + esc(t.course_version) + "</td><td>" + esc(day(t.completed_at)) +
        "</td><td>" + statusChip(t.validity) + '<div class="sub">until ' + esc(day(t.expires_at)) + "</div></td></tr>";
    });
    var trainingForm = form("POST", base + "/training", "Record training", [
      input("course_code", "Course", 'required placeholder="LOTO-101"'),
      input("course_version", "Version", 'placeholder="1"'),
      input("expires_at", "Expires", 'type="date"')
    ]);
    return section("Identity", identity + statusForm + updateForm) +
      section("Credentials", table(["Credential", "Issuer", "Validity", "Scope", ""], credentials, "No credentials.") + issue) +
      section("Training", table(["Course", "Completed", "Validity"], training, "No training records.") + trainingForm) +
      section("History", '<ul class="change-feed">' + history.map(feedItem).join("") + "</ul>");
  }

  function commissionDrawer() {
    return section("Commission a robot",
      '<p class="sub">Registers a new asset from a catalog model: its components, factory calibrations and first report are created the way a real commissioning would.</p>' +
      form("POST", "/api/fleet/robots", "Commission", [
        select("model_code", "Model", options(state.models.map(function (m) {
          return [m.model_code, m.manufacturer_name + " " + m.name + " (" + m.embodiment_class + ")"];
        }))),
        input("site_code", "Site", 'required value="WH-02"'),
        input("home_zone", "Home zone", 'placeholder="dock_1"'),
        input("fleet_id", "Fleet", 'placeholder="WH-02-FLEET"')
      ], "primary", "robot"));
  }

  function registerDrawer() {
    return section("Register a worker",
      '<p class="sub">Only qualification-relevant fields exist: anything else is refused by the API.</p>' +
      form("POST", "/api/workforce/workers", "Register", [
        input("display_name", "Name", "required"),
        select("worker_type", "Type", options(["EMPLOYEE", "CONTRACTOR", "PARTNER"])),
        input("organization", "Organisation", 'placeholder="Warehouse Operations"'),
        input("role_codes", "Roles", 'data-list="1" placeholder="WAREHOUSE_OPERATOR"'),
        input("site_codes", "Sites", 'data-list="1" placeholder="WH-01"')
      ], "primary", "worker"));
  }

  // ---------------------------------------------------------------- drawer
  function show(title, html) {
    var body = $("drawerBody");
    var scroll = body.scrollTop;
    $("drawerTitle").textContent = title;
    body.innerHTML = html;
    body.scrollTop = scroll;
    $("drawer").hidden = false;
  }

  function drawerDirty() {
    var drawer = $("drawer");
    if (drawer.hidden) return false;
    if (drawer.contains(document.activeElement) && /INPUT|SELECT|TEXTAREA/.test(document.activeElement.tagName)) return true;
    return Array.prototype.some.call(drawer.querySelectorAll("input:not([type=hidden])"), function (el) {
      return el.value !== el.defaultValue;
    });
  }

  function renderDrawer() {
    var d = state.drawer;
    if (!d) return Promise.resolve();
    if (d.kind === "commission") { show("Commission robot", commissionDrawer()); return Promise.resolve(); }
    if (d.kind === "register") { show("Register worker", registerDrawer()); return Promise.resolve(); }
    var id = encodeURIComponent(d.id);
    var urls = d.kind === "robot"
      ? ["/api/fleet/robots/" + id, "/api/fleet/robots/" + id + "/history?limit=30"]
      : ["/api/workforce/workers/" + id, "/api/workforce/workers/" + id + "/history?limit=30"];
    return Promise.all(urls.map(function (url) { return api("GET", url); })).then(function (results) {
      if (!state.drawer || state.drawer.id !== d.id) return;
      if (d.kind === "robot") {
        var robot = results[0].robot;
        show(robot.asset_id + " · " + robot.model.name, robotDrawer(robot, results[1].history));
      } else {
        var worker = results[0].worker;
        show(worker.worker_id + " · " + worker.display_name, workerDrawer(worker, results[1].history));
      }
    });
  }

  function openDrawer(kind, id) {
    state.drawer = { kind: kind, id: id || null };
    $("drawerBody").scrollTop = 0;
    renderDrawer().catch(function (error) { toast(error.message, true); });
  }

  function closeDrawer() {
    state.drawer = null;
    $("drawer").hidden = true;
  }

  // --------------------------------------------------------------- actions
  function formPayload(formNode) {
    var payload = {};
    Array.prototype.forEach.call(formNode.elements, function (el) {
      if (!el.name) return;
      var value = String(el.value || "").trim();
      if (value === "") return;
      payload[el.name] = el.dataset.list
        ? value.split(",").map(function (part) { return part.trim(); }).filter(Boolean)
        : value;
    });
    return payload;
  }

  document.addEventListener("submit", function (event) {
    var formNode = event.target;
    if (!formNode.dataset || !formNode.dataset.url) return;
    event.preventDefault();
    var button = formNode.querySelector("button[type=submit]");
    if (button) button.disabled = true;
    api(formNode.dataset.method || "POST", formNode.dataset.url, formPayload(formNode)).then(function (data) {
      toast(describe(data.change));
      if (formNode.dataset.open === "robot" && data.robot) state.drawer = { kind: "robot", id: data.robot.asset_id };
      if (formNode.dataset.open === "worker" && data.worker) state.drawer = { kind: "worker", id: data.worker.worker_id };
      return refresh(true);
    }).catch(function (error) {
      toast(error.message, true);
    }).then(function () {
      if (button) button.disabled = false;
    });
  });

  // ----------------------------------------------------------------- data
  function loadFeedPage(feed, pages) {
    var cursor = state.cursors[feed];
    var url = "/api/" + feed + "/changes?limit=200" + (cursor ? "&cursor=" + encodeURIComponent(cursor) : "");
    return api("GET", url).then(function (page) {
      if (page.resync_required) state.feed = state.feed.filter(function (c) { return c.feed !== feed; });
      page.items.forEach(function (c) { c.feed = feed; state.feed.push(c); });
      state.cursors[feed] = page.next_cursor;
      if (page.has_more && pages < 20) return loadFeedPage(feed, pages + 1);
    });
  }

  function loadFeed() {
    return Promise.all([loadFeedPage("fleet", 0), loadFeedPage("workforce", 0)]).then(function () {
      state.feed.sort(function (a, b) {
        return String(b.occurred_at).localeCompare(String(a.occurred_at)) || b.seq - a.seq;
      });
      if (state.feed.length > FEED_MAX) state.feed.length = FEED_MAX;
      renderFeed();
    });
  }

  function loadList() {
    if (state.view === "workers") {
      return api("GET", "/api/workforce/workers").then(function (d) { state.workers = d.workers; renderWorkers(); });
    }
    return api("GET", "/api/fleet/robots").then(function (d) { state.robots = d.robots; renderRobots(); });
  }

  function refresh(force) {
    var jobs = [loadList(), loadFeed()];
    if (state.drawer && (force === true || !drawerDirty())) jobs.push(renderDrawer());
    return Promise.all(jobs).catch(function (error) { toast(error.message, true); });
  }

  function setView(view) {
    state.view = view;
    Array.prototype.forEach.call(document.querySelectorAll(".fleet-nav .tab"), function (tab) {
      tab.classList.toggle("active", tab.dataset.view === view);
    });
    ["robots", "workers", "changes"].forEach(function (name) { $("view-" + name).hidden = name !== view; });
    loadList().catch(function (error) { toast(error.message, true); });
  }

  // ----------------------------------------------------------------- init
  function init() {
    $("robotStatus").innerHTML = '<option value="">Any</option>' + options(LIFECYCLE);
    Array.prototype.forEach.call(document.querySelectorAll(".fleet-nav .tab"), function (tab) {
      tab.addEventListener("click", function () { setView(tab.dataset.view); });
    });
    $("robotTable").addEventListener("click", function (event) {
      var row = event.target.closest("tr[data-asset]");
      if (row) openDrawer("robot", row.dataset.asset);
    });
    $("workerTable").addEventListener("click", function (event) {
      var row = event.target.closest("tr[data-worker]");
      if (row) openDrawer("worker", row.dataset.worker);
    });
    ["robotSearch", "robotSite", "robotStatus"].forEach(function (id) { $(id).addEventListener("input", renderRobots); });
    ["workerSearch", "workerStatus"].forEach(function (id) { $(id).addEventListener("input", renderWorkers); });
    $("commissionBtn").addEventListener("click", function () { openDrawer("commission"); });
    $("registerBtn").addEventListener("click", function () { openDrawer("register"); });
    $("drawerClose").addEventListener("click", closeDrawer);
    document.addEventListener("keydown", function (event) { if (event.key === "Escape") closeDrawer(); });

    Promise.all([
      api("GET", "/api/fleet/catalog/models"),
      api("GET", "/api/fleet/releases"),
      api("GET", "/api/workforce/credential-definitions")
    ]).then(function (results) {
      state.models = results[0].models;
      state.releases = results[1].releases;
      state.definitions = results[2].definitions;
      return refresh(true);
    }).catch(function (error) {
      toast(error.message, true);
    }).then(function () {
      setInterval(refresh, POLL_MS);
    });
  }

  init();
})();
```

Modify `frontend/index.html` — inside `<header class="topbar">`, immediately before `<div class="notif-wrap">`, add:

```html
  <a class="btn small fleet-link" href="/fleet.html" title="Robot and worker inventory — the fleet-manager and workforce source systems">Fleet &amp; workforce</a>
```

Modify `frontend/style.css` — after the `.btn-row.tight` rule add:

```css
.fleet-link { align-self: center; white-space: nowrap; }
```

Modify `frontend/app.js:491` — replace `head.appendChild(chip(robot.status));` with:

```js
      head.appendChild(chip(robot.ota_installing ? "UPDATING" : robot.status));
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest backend/test_inventory_api.py -q`
Expected: `7 passed`.

- [ ] **Step 5: Manual browser check (controller, not a subagent)**

Create `.claude/launch.json` in the repo root (not committed — it is local tooling):

```json
{
  "version": "0.0.1",
  "configurations": [
    {"name": "warehouse-twin", "runtimeExecutable": ".venv/bin/python", "runtimeArgs": ["-m", "backend.app"], "port": 5000}
  ]
}
```

Start it with the browser preview, open `/fleet.html`, and check with no console errors:
1. The robots table lists 16 assets; WH-02 rows show the seeded chips (recalled, EXPIRED, DUE SOON, stale, REPORTED).
2. Open `AST-000206` → Verify the REPORTED OTA job → mismatch chip clears; the change feed shows `ota verified`.
3. Open `AST-000101` → Start OTA to `2.2.0` → on `/` the Robo-01 card shows UPDATING, then the job reaches REPORTED.
4. Open `AST-000101` → Open a CORRECTIVE work order → Robo-01 goes STOPPED on the main dashboard → Swap the lidar → the new lidar shows MISSING calibration → Calibrate PASS → Close → Robo-01 is IDLE again.
5. Workers → `E-10001` Sam → Revoke `electrical_safety` → on `/`, Sam's certifications show only `safety_inspection`.
6. Check the page at 375 px width: no horizontal page scroll; the drawer fills the screen.

Stop the preview. Delete `data/inventory.sqlite3` so the next run seeds fresh (it is git-ignored).

- [ ] **Step 6: Commit**

```bash
git add frontend/fleet.html frontend/fleet.css frontend/fleet.js frontend/index.html frontend/style.css frontend/app.js backend/test_inventory_api.py
git commit -m "feat(frontend): Fleet & Workforce page with lifecycle actions and live change feed

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## Final verification (after Task 12)

- [ ] Run `.venv/bin/python -m pytest -q` → `341 passed, 2 failed` — the only failures are `test_real_task_006_box_conflict_is_caught` and `test_idle_robot_with_a_low_battery_charges_itself` (pre-existing).
- [ ] `grep -rn "from \.\.\|from backend\|import backend" backend/inventory/` returns nothing (the package stays standalone).
- [ ] `git status` is clean apart from untracked local files (`.claude/`, `docs/handoffs/`, `.DS_Store`).
