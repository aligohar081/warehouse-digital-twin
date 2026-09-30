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
import logging
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Callable, Dict, Iterator, List, Mapping, Optional, Sequence

from .documents import diff_documents, iso, utc_now
from .errors import NotFound
from .store import InventoryStore

logger = logging.getLogger("backend.inventory")

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
                try:
                    listener(change)
                except Exception:
                    # One listener's failure must not break delivery to others or compromise
                    # the already-committed write. Log and continue.
                    logger.exception("Inventory listener %r failed on change %s (%s)", listener, change["seq"], change["action"])

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
        cursor_epoch, seq_int = None, 0
        if cursor:
            cursor_epoch, separator, seq_text = str(cursor).partition(":")
            if not separator or not seq_text or not seq_text.isascii() or not seq_text.isdigit():
                raise ValueError(f"Malformed cursor {cursor!r} (expected '<epoch>:<seq>')")
            try:
                seq_int = int(seq_text)
                if seq_int > 2**63 - 1:
                    raise ValueError(f"Malformed cursor {cursor!r} (expected '<epoch>:<seq>')")
            except (ValueError, OverflowError):
                raise ValueError(f"Malformed cursor {cursor!r} (expected '<epoch>:<seq>')") from None
        with self.store.lock:  # one snapshot: a reseed can't slip between the epoch and the rows
            epoch = self.store.epoch()
            resync = cursor_epoch is not None and cursor_epoch != epoch
            after_seq = 0 if resync else seq_int
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
