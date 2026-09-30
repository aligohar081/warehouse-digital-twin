"""The twin's fleet-manager and workforce *source systems*.

See docs/superpowers/specs/2026-09-30-fleet-workforce-inventory-design.md.
This package imports nothing from the rest of the twin so it can later be
lifted out into a standalone service unchanged.
"""

from .bootstrap import open_inventory, reseed
from .errors import Conflict, NotFound
from .service import InventoryService

__all__ = ["Conflict", "InventoryService", "NotFound", "open_inventory", "reseed"]
