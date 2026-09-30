"""Inventory error types, mapped to HTTP status codes by backend/inventory_api.py."""
from __future__ import annotations


class NotFound(KeyError):
    """The referenced record does not exist (HTTP 404)."""

    def __str__(self) -> str:  # KeyError's default str() wraps the message in quotes
        return str(self.args[0]) if self.args else "Not found"


class Conflict(ValueError):
    """The request is valid but conflicts with the record's current state (HTTP 409)."""
