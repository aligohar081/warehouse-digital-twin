"""InventoryService — the twin's fleet-manager and workforce source systems.

One object, split by responsibility into mixins (added task by task):
catalog.py (OEM reference data and releases), fleet.py (robot assets and
reported state), servicing.py (work orders, part swaps, calibration),
ota.py (over-the-air updates) and workforce.py (workers, credentials,
training). core.py holds the shared plumbing.
"""
from __future__ import annotations

from .catalog import CatalogMixin
from .core import ServiceCore


class InventoryService(CatalogMixin, ServiceCore):
    pass
