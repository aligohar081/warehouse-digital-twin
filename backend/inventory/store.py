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
                    self._rollback()
                raise
            self.depth -= 1
            if self.depth == 0:
                try:
                    self.conn.execute("COMMIT")
                except BaseException:
                    # A failed COMMIT (e.g. "database is locked") can leave the transaction open;
                    # end it so the connection isn't wedged for every later write.
                    self._rollback()
                    raise

    def _rollback(self) -> None:
        # SQLite may already have rolled back on its own; ROLLBACK then would raise and mask
        # the original error.
        if self.conn.in_transaction:
            self.conn.execute("ROLLBACK")

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
