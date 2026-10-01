"""Goods and the stock ledger (multi-embodiment spec §7).

Box kinds and their weights live on Box (backend/box.py). This module adds
the StockLedger: which pallet or tote sits in each rack or shelf slot, and
where each SKU is stocked. Every location keeps two numbers — what the
record says (`recorded_qty`) and what is really there (`true_qty`). Only a
fault or a mis-pick makes them differ, and only a count reconciliation
corrects the record. Nothing here emits events: the jobs that pick, count
and reconcile do.

The twin owns one ledger (DigitalTwin.stock), the single source of truth for
stock. The helpers at the bottom move boxes in and out of it and keep each
box's `slot` and `quantity` in step with it. Like the ledger itself they take
no lock: callers hold twin.lock (the simulator tick does).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .models import CONFIG, BoxKind, Cell, manhattan

__all__ = [
    "BoxKind", "StockLedger", "StockLocation", "carton_weight_kg", "free_slots", "physical_qty",
    "release", "store", "sync_box", "take_units", "unit_weight_kg",
]


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
        self.check_put(slot_id, kind)
        if box_id in self._slot_of_box:
            raise ValueError(f"{box_id} is already in slot {self._slot_of_box[box_id]}")
        if int(quantity) < 0:
            raise ValueError("quantity cannot be negative")
        location = StockLocation(slot_id, box_id, sku, int(quantity), int(quantity))
        self._locations[slot_id] = location
        self._slot_of_box[box_id] = slot_id
        return location

    def check_put(self, slot_id: str, kind: Optional[str] = None) -> None:
        """Raise ValueError if a box of `kind` cannot be put in `slot_id` right
        now: the slot is unknown, is for the other kind, or is already taken.
        `put` runs this itself; a caller that must not change anything unless
        the put will succeed (goods.store) runs it first."""
        if self.warehouse is not None:
            slot = self.warehouse.slot(slot_id)
            if slot is None:
                raise ValueError(f"Unknown slot {slot_id!r}")
            if kind is not None and BoxKind(kind).value != slot.kind:
                raise ValueError(f"A {BoxKind(kind).value} cannot go in {slot.kind} slot {slot_id}")
        if slot_id in self._locations:
            raise ValueError(f"Slot {slot_id} already holds {self._locations[slot_id].box_id}")

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


# --------------------------------------------------------------------------- #
# Keeping boxes and the ledger in step
# --------------------------------------------------------------------------- #
def sync_box(twin: Any, box: Any) -> None:
    """Mirror the ledger onto `box`: the slot the ledger holds it in, and its
    recorded quantity there."""
    slot_id = twin.stock.slot_of(box.id)
    box.slot = slot_id
    if slot_id is not None:
        box.quantity = twin.stock.location(slot_id).recorded_qty
    box.touch()


def store(twin: Any, box: Any, slot_id: str, true_slot_id: Optional[str] = None) -> StockLocation:
    """Put a pallet or tote away in `slot_id`, and record it there.

    A tote going back to its own slot keeps its location (and any variance it
    carries); a box moving to another slot takes its quantities with it; a box
    new to storage is recorded with its SKU and quantity. `true_slot_id` is
    where the box physically went when that differs (a wrong-level placement):
    the record keeps the requested slot, whose true quantity drops to 0, and
    the box remembers where it really is.

    A store that cannot happen (an unknown, taken or wrong-kind slot, or an
    unknown `true_slot_id`) raises ValueError before anything changes, so the
    ledger and the box are left exactly as they were."""
    stock = twin.stock
    current = stock.slot_of(box.id)
    if current != slot_id:  # a box already in `slot_id` is simply being put back
        stock.check_put(slot_id, box.kind.value)
    slot = twin.warehouse.slot(slot_id)
    actual = None
    if true_slot_id and true_slot_id != slot_id:
        actual = twin.warehouse.slot(true_slot_id)
        if actual is None:
            raise ValueError(f"Unknown slot {true_slot_id!r}")
    if current == slot_id:
        location = stock.location(slot_id)
    elif current is not None:
        previous = stock.take(current)
        location = stock.put(slot_id, box.id, previous.sku, previous.recorded_qty, kind=box.kind.value)
        location.true_qty = previous.true_qty
    else:
        location = stock.put(slot_id, box.id, box.sku, box.quantity, kind=box.kind.value)
    box.position = slot.cell
    box.true_slot = None
    if actual is not None:
        stock.adjust_true(slot_id, -location.true_qty)
        box.true_slot = true_slot_id
        box.position = actual.cell
    sync_box(twin, box)
    return location


def release(twin: Any, box: Any) -> StockLocation:
    """A pallet leaves storage for good (retrieved to ship): its location goes."""
    slot_id = twin.stock.slot_of(box.id)
    if slot_id is None:
        raise ValueError(f"{box.name} is not in the stock ledger")
    location = twin.stock.take(slot_id)
    box.slot = box.true_slot = None
    box.touch()
    return location


def unit_weight_kg(tote_weight_kg: float, quantity: int) -> float:
    """One unit's weight: the tote's contents (its weight less TOTE_TARE_KG)
    shared across its units."""
    if quantity <= 0:
        return 0.0
    return round(max(0.0, float(tote_weight_kg) - CONFIG["TOTE_TARE_KG"]) / quantity, 3)


def take_units(twin: Any, tote: Any, count: int = 1) -> Tuple[float, float]:
    """Pick `count` units out of a tote. Its recorded and true quantities drop
    by `count` and its weights by the units' weight; returns one unit's
    (declared, true) weight. A tote away at a station keeps its slot, so the
    ledger goes on counting what it holds."""
    if tote.kind is not BoxKind.TOTE:
        raise ValueError(f"{tote.name} is a {tote.kind.value}, not a TOTE")
    slot_id = twin.stock.slot_of(tote.id)
    if slot_id is None:
        raise ValueError(f"{tote.name} is not in the stock ledger")
    location = twin.stock.location(slot_id)
    if location.true_qty < count:
        raise ValueError(f"{tote.name} physically holds only {location.true_qty} of {location.sku}")
    declared_unit = unit_weight_kg(tote.declared_weight_kg, location.recorded_qty)
    true_unit = unit_weight_kg(tote.true_weight_kg, location.true_qty)
    twin.stock.consume(slot_id, count)
    tare = CONFIG["TOTE_TARE_KG"]
    tote.declared_weight_kg = round(max(tare, tote.declared_weight_kg - count * declared_unit), 3)
    tote.true_weight_kg = round(max(tare, tote.true_weight_kg - count * true_unit), 3)
    sync_box(twin, tote)
    return declared_unit, true_unit


def physical_qty(twin: Any, slot_id: str) -> int:
    """What is really in `slot_id`: its location's true quantity, plus any
    misplaced box that physically went there instead of its recorded slot."""
    location = twin.stock.location(slot_id)
    quantity = location.true_qty if location is not None else 0
    return quantity + sum(box.quantity for box in twin.boxes.values() if box.true_slot == slot_id)


def free_slots(twin: Any, kind: str, max_level: Optional[int] = None, near: Optional[Cell] = None,
               exclude: Sequence[str] = ()) -> List[Any]:
    """Empty `kind` slots (PALLET or TOTE) at or below `max_level`, nearest
    first to `near` (by their first face cell), then by slot id. A slot is
    empty when the ledger holds nothing there and no misplaced box sits in
    it; `exclude` names slots already promised to other jobs."""
    taken = set(exclude) | {box.true_slot for box in twin.boxes.values() if box.true_slot}
    slots = [
        slot for slot in twin.warehouse.slots.values()
        if slot.kind == BoxKind(kind).value and slot.slot_id not in taken
        and twin.stock.location(slot.slot_id) is None
        and (max_level is None or slot.level <= max_level)
    ]
    if near is None:
        return sorted(slots, key=lambda slot: slot.slot_id)
    return sorted(slots, key=lambda slot: (manhattan(slot.faces[0], near), slot.slot_id))
