"""Open, seed and re-seed inventory databases.

Seeding a demo inventory runs a few hundred service actions, so the seeded
database is built once per process into an in-memory *template* and copied
into each new database with sqlite3's backup API (the test suite builds
hundreds of twins). A template older than TEMPLATE_MAX_AGE_SECONDS is
rebuilt so seeded dates ("calibration expires in 10 days") stay relative
to now. Passing a `clock` skips the template and seeds directly against
that clock, which keeps unit tests deterministic.

A file database records the SEED_VERSION it was seeded under; opening one
seeded under another schema or seed re-seeds it, keeping the old file as .bak.
"""
from __future__ import annotations

import hashlib
import json
import logging
import shutil
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional, Tuple

from . import catalog_data
from .schema import SCHEMA_VERSION, connect, create_schema, is_current, new_epoch
from .seed import seed_catalog
from .service import InventoryService
from .store import InventoryStore

logger = logging.getLogger("backend.inventory")

TEMPLATE_MAX_AGE_SECONDS = 60
_CATALOG_TABLES = (
    "MANUFACTURERS", "PART_MODELS", "ROBOT_MODELS", "CREDENTIAL_DEFINITIONS", "CLASS_DEFAULT_MODELS",
    "ROBOT_SOFTWARE_RELEASES", "EXTRA_ROBOT_SOFTWARE_RELEASES", "ROBOT_SOFTWARE_MIN_HW",
    "COMPONENT_FIRMWARE_VERSIONS", "COMPONENT_FIRMWARE_MIN_HW", "AI_POLICY_RELEASES", "OS_VERSIONS",
)
_templates: Dict[bool, Tuple[sqlite3.Connection, float]] = {}
_template_lock = threading.Lock()


def _plain(value: Any) -> Any:
    """`value` with tuple dict keys joined into strings, so json.dumps can take it."""
    if isinstance(value, dict):
        return {":".join(key) if isinstance(key, tuple) else key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def _seed_version() -> str:
    """Fingerprint of everything that decides what a fresh seed contains: the
    schema version, the catalog tables and the demo seed's source. A file seeded
    under a different fingerprint is re-seeded (the old one kept as .bak)."""
    catalog = {name: _plain(getattr(catalog_data, name)) for name in _CATALOG_TABLES}
    demo_source = Path(__file__).with_name("demo_seed.py").read_text(encoding="utf-8")
    text = str(SCHEMA_VERSION) + json.dumps(catalog, sort_keys=True) + demo_source
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


SEED_VERSION = _seed_version()


def _seed_into(conn: sqlite3.Connection, demo: bool, clock: Optional[Callable[[], datetime]] = None) -> None:
    create_schema(conn)
    service = InventoryService(InventoryStore(conn), clock=clock)
    seed_catalog(service)
    if demo:
        from .demo_seed import seed_demo

        seed_demo(service)
    service.store.set_meta("seed_version", SEED_VERSION)
    service.store.set_meta("seeded", "1")


def _wipe(conn: sqlite3.Connection) -> None:
    names = [row["name"] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")]
    conn.execute("PRAGMA foreign_keys = OFF")
    for name in names:
        conn.execute(f'DROP TABLE IF EXISTS "{name}"')
    conn.execute("PRAGMA foreign_keys = ON")


def _stale_reason(conn: sqlite3.Connection) -> Optional[str]:
    """Why `conn`'s database can't be used as it is (None when it is current)."""
    if not is_current(conn):
        return f"it is not a fully seeded schema-version-{SCHEMA_VERSION} inventory"
    row = conn.execute("SELECT value FROM meta WHERE key = 'seed_version'").fetchone()
    if row is None or row["value"] != SEED_VERSION:
        return "the catalog or demo seed has changed since it was seeded"
    return None


def _has_tables(conn: sqlite3.Connection) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' LIMIT 1").fetchone() is not None


def _keep_backup(conn: sqlite3.Connection, path: str, reason: str) -> None:
    backup = path + ".bak"
    logger.warning("Re-seeding inventory database %s because %s; the previous file is kept as %s",
                   path, reason, backup)
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")  # fold the WAL in so the copied file is complete
    shutil.copy2(path, backup)


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
    reason = None if path == ":memory:" else _stale_reason(conn)
    if path == ":memory:" or reason is not None:
        if reason is not None and _has_tables(conn):
            _keep_backup(conn, path, reason)
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
