"""Goods and the stock ledger (multi-embodiment spec §7).

Box kinds and their weights live on Box (backend/box.py). This module adds
the StockLedger: which pallet or tote sits in each rack or shelf slot, and
where each SKU is stocked. Every location keeps two numbers — what the
record says (`recorded_qty`) and what is really there (`true_qty`). Only a
fault or a mis-pick makes them differ, and only a count reconciliation
corrects the record. Nothing here emits events: the jobs that pick, count
and reconcile do (plan 1b).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, Iterable, List, Optional

from .models import CONFIG, BoxKind

__all__ = ["BoxKind", "StockLedger", "StockLocation", "carton_weight_kg"]


def carton_weight_kg(item_weights_kg: Iterable[float]) -> float:
    """A packed carton weighs its items plus the carton itself (spec §7.1)."""
    return round(sum(item_weights_kg) + CONFIG["CARTON_TARE_KG"], 3)


@dataclass
class StockLocation:
    slot_id: str
    box_id: str
    sku: Optional[str]
    recorded_qty: int
    true_qty: int
    counted_qty: Optional[int] = None  # the last cycle count's reading
    counted_tick: Optional[int] = None

    @property
    def discrepancy(self) -> int:
        """What is really there minus what the record says."""
        return self.true_qty - self.recorded_qty

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class StockLedger:
    """Slot -> the box in it, and SKU -> its locations, with recorded and
    true quantities. Pass the warehouse to check slot ids and kinds."""

    def __init__(self, warehouse: Optional[Any] = None) -> None:
        self.warehouse = warehouse
        self._locations: Dict[str, StockLocation] = {}
        self._slot_of_box: Dict[str, str] = {}

    # ---- placing and removing ----------------------------------------- #
    def put(self, slot_id: str, box_id: str, sku: Optional[str], quantity: int,
            kind: Optional[str] = None) -> StockLocation:
        """Record a pallet or tote arriving in a slot, with its contents."""
        if self.warehouse is not None:
            slot = self.warehouse.slot(slot_id)
            if slot is None:
                raise ValueError(f"Unknown slot {slot_id!r}")
            if kind is not None and BoxKind(kind).value != slot.kind:
                raise ValueError(f"A {BoxKind(kind).value} cannot go in {slot.kind} slot {slot_id}")
        if slot_id in self._locations:
            raise ValueError(f"Slot {slot_id} already holds {self._locations[slot_id].box_id}")
        if box_id in self._slot_of_box:
            raise ValueError(f"{box_id} is already in slot {self._slot_of_box[box_id]}")
        if int(quantity) < 0:
            raise ValueError("quantity cannot be negative")
        location = StockLocation(slot_id, box_id, sku, int(quantity), int(quantity))
        self._locations[slot_id] = location
        self._slot_of_box[box_id] = slot_id
        return location

    def take(self, slot_id: str) -> StockLocation:
        """Record the box leaving its slot (retrieved, or taken to a station)."""
        location = self._require(slot_id)
        del self._locations[slot_id]
        del self._slot_of_box[location.box_id]
        return location

    # ---- reads ---------------------------------------------------------- #
    def location(self, slot_id: str) -> Optional[StockLocation]:
        return self._locations.get(slot_id)

    def box_in(self, slot_id: str) -> Optional[str]:
        location = self._locations.get(slot_id)
        return location.box_id if location else None

    def slot_of(self, box_id: str) -> Optional[str]:
        return self._slot_of_box.get(box_id)

    def locations(self, sku: Optional[str] = None) -> List[StockLocation]:
        """Every location (or every location of `sku`), by slot id."""
        return [self._locations[key] for key in sorted(self._locations)
                if sku is None or self._locations[key].sku == sku]

    def skus(self) -> List[str]:
        return sorted({loc.sku for loc in self._locations.values() if loc.sku is not None})

    def recorded_qty(self, sku: str) -> int:
        return sum(loc.recorded_qty for loc in self.locations(sku))

    def true_qty(self, sku: str) -> int:
        return sum(loc.true_qty for loc in self.locations(sku))

    def discrepancies(self) -> List[StockLocation]:
        """Locations whose record no longer matches what is really there."""
        return [loc for loc in self.locations() if loc.discrepancy]

    # ---- quantity changes ---------------------------------------------- #
    def consume(self, slot_id: str, quantity: int, true_quantity: Optional[int] = None) -> StockLocation:
        """A pick: the record drops by `quantity`, the shelf by `true_quantity`
        (a mis-pick takes a different number than the record says)."""
        location = self._require(slot_id)
        taken = quantity if true_quantity is None else true_quantity
        if quantity < 0 or taken < 0:
            raise ValueError("quantities cannot be negative")
        if quantity > location.recorded_qty:
            raise ValueError(f"{slot_id} records only {location.recorded_qty} of {location.sku}")
        location.recorded_qty -= int(quantity)
        location.true_qty = max(0, location.true_qty - int(taken))
        location.counted_qty, location.counted_tick = None, None
        return location

    def restock(self, slot_id: str, quantity: int) -> StockLocation:
        """Units put back (a return): both the record and the shelf go up."""
        location = self._require(slot_id)
        if quantity < 0:
            raise ValueError("quantity cannot be negative")
        location.recorded_qty += int(quantity)
        location.true_qty += int(quantity)
        location.counted_qty, location.counted_tick = None, None
        return location

    def adjust_true(self, slot_id: str, delta: int) -> StockLocation:
        """A physical change the record never saw (an injected fault)."""
        location = self._require(slot_id)
        location.true_qty = max(0, location.true_qty + int(delta))
        location.counted_qty, location.counted_tick = None, None
        return location

    # ---- counting ------------------------------------------------------- #
    def record_count(self, slot_id: str, counted_qty: int, tick: Optional[int] = None) -> Dict[str, Any]:
        """A cycle count's reading. It is stored and compared with the record,
        but the record is left alone until reconcile()."""
        location = self._require(slot_id)
        if not isinstance(counted_qty, int) or isinstance(counted_qty, bool) or counted_qty < 0:
            raise ValueError("counted_qty must be a non-negative integer")
        location.counted_qty, location.counted_tick = int(counted_qty), tick
        variance = location.counted_qty - location.recorded_qty
        return {
            "slot_id": slot_id, "box_id": location.box_id, "sku": location.sku,
            "recorded_qty": location.recorded_qty, "counted_qty": location.counted_qty,
            "true_qty": location.true_qty, "variance": variance,
            "auto_reconcile": variance != 0 and abs(variance) <= CONFIG["STOCK_AUTO_RECONCILE_UNITS"],
        }

    def reconcile(self, slot_id: str) -> int:
        """Correct the record to the last count; returns the correction made."""
        location = self._require(slot_id)
        if location.counted_qty is None:
            raise ValueError(f"{slot_id} has not been counted")
        correction = location.counted_qty - location.recorded_qty
        location.recorded_qty = location.counted_qty
        return correction

    # ---- persistence ---------------------------------------------------- #
    def to_dict(self) -> Dict[str, Any]:
        return {"locations": [loc.to_dict() for loc in self.locations()]}

    @classmethod
    def from_dict(cls, data: Dict[str, Any], warehouse: Optional[Any] = None) -> "StockLedger":
        ledger = cls(warehouse)
        for item in data.get("locations", []):
            location = StockLocation(**item)
            ledger._locations[location.slot_id] = location
            ledger._slot_of_box[location.box_id] = location.slot_id
        return ledger

    def _require(self, slot_id: str) -> StockLocation:
        location = self._locations.get(slot_id)
        if location is None:
            raise KeyError(f"Slot {slot_id} is empty")
        return location
