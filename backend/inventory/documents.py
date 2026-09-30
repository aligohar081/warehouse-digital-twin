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
