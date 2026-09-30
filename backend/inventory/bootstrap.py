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
