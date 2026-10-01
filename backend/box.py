"""Boxes: the payloads robots move around the warehouse.

One entity covers every kind of goods (multi-embodiment spec §7.1): a PALLET
of cases, a TOTE of units, a single ITEM, or a packed CARTON. Classic boxes
are TOTEs and use nothing but `weight`, exactly as before.
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Union

from .models import BoxKind, BoxStatus, Cell, cell_dict, cell_tuple, now_iso


class Box:
    def __init__(
        self,
        box_id: str,
        name: str,
        position: Cell,
        weight: float = 1.0,
        source: Optional[str] = None,
        destination: Optional[str] = None,
        status: BoxStatus = BoxStatus.STORED,
        kind: Union[BoxKind, str] = BoxKind.TOTE,
        sku: Optional[str] = None,
        quantity: int = 0,
        true_weight_kg: Optional[float] = None,
        slot: Optional[str] = None,
        order_id: Optional[str] = None,
    ) -> None:
        self.id = box_id
        self.name = name
        self.position: Cell = position
        self.kind = BoxKind(kind)
        # A PALLET holds `quantity` cases of one SKU, a TOTE `quantity` units.
        self.sku = sku
        self.quantity = int(quantity)
        # What the WMS says it weighs (`weight` is this, by its old name) and
        # what it really weighs. Equal unless a fault makes them differ.
        self.declared_weight_kg = float(weight)
        self.true_weight_kg = float(true_weight_kg) if true_weight_kg is not None else self.declared_weight_kg
        # The rack or shelf slot (a Warehouse.slots id) the stock ledger holds
        # it in, if any — backend/goods.py keeps this in step with the ledger.
        # A tote keeps its slot while it is away at a station. `true_slot` is
        # set only when the box physically sits in a different slot than the
        # record says (a wrong-level placement).
        self.slot = slot
        self.true_slot: Optional[str] = None
        self.order_id = order_id
        self.source = source
        self.destination = destination
        self.status = status
        self.assigned_robot: Optional[str] = None
        self.assigned_task: Optional[str] = None
        self.pick_count = 0
        self.delivery_count = 0
        self.created_at = now_iso()
        self.updated_at = now_iso()

    # ------------------------------------------------------------------ #
    @property
    def weight(self) -> float:
        """The declared weight, under the name every classic caller uses."""
        return self.declared_weight_kg

    @weight.setter
    def weight(self, value: float) -> None:
        self.declared_weight_kg = float(value)

    def touch(self) -> None:
        self.updated_at = now_iso()

    def set_status(self, status: BoxStatus) -> BoxStatus:
        previous = self.status
        self.status = status
        self.touch()
        return previous

    @property
    def is_available(self) -> bool:
        return self.status in (BoxStatus.STORED, BoxStatus.DELIVERED) and self.assigned_robot is None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "weight": self.weight,
            "kind": self.kind.value,
            "sku": self.sku,
            "quantity": self.quantity,
            "declared_weight_kg": self.declared_weight_kg,
            "true_weight_kg": self.true_weight_kg,
            "slot": self.slot,
            "true_slot": self.true_slot,
            "order_id": self.order_id,
            "position": cell_dict(self.position),
            "source": self.source,
            "destination": self.destination,
            "status": self.status.value,
            "assigned_robot": self.assigned_robot,
            "assigned_task": self.assigned_task,
            "pick_count": self.pick_count,
            "delivery_count": self.delivery_count,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "Box":
        box = Box(
            box_id=data["id"],
            name=data["name"],
            position=cell_tuple(data["position"]) or (1, 1),
            weight=data.get("declared_weight_kg", data.get("weight", 1.0)),
            source=data.get("source"),
            destination=data.get("destination"),
            status=BoxStatus(data.get("status", "STORED")),
            kind=data.get("kind", BoxKind.TOTE.value),
            sku=data.get("sku"),
            quantity=data.get("quantity", 0),
            true_weight_kg=data.get("true_weight_kg"),
            slot=data.get("slot"),
            order_id=data.get("order_id"),
        )
        box.true_slot = data.get("true_slot")
        box.assigned_robot = data.get("assigned_robot")
        box.assigned_task = data.get("assigned_task")
        box.pick_count = data.get("pick_count", 0)
        box.delivery_count = data.get("delivery_count", 0)
        box.created_at = data.get("created_at", box.created_at)
        box.updated_at = data.get("updated_at", box.updated_at)
        return box
