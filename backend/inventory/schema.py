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
