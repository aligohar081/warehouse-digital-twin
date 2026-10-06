"""Floor equipment (multi-embodiment spec §8): the conveyor line, the sorter,
and a hand-off record for every transfer of a box between two holders.

The line runs from Pick's infeed to the sorter, flowing +x. An item moves one
cell every ceil(CELL_SIZE_M / CONVEYOR_SPEED_MPS / TICK_DT) ticks; a cell
holds one item and an item that can't move waits, so the line backs up. An
item tagged for an arm stops on that arm's working cell until the arm takes
it; everything else rides to the last cell. There the sorter takes a carton
in after SORTER_TRANSFER_S and, SORTER_TRANSFER_S later, drops it on a free
cell of its order's dock; anything that isn't a carton is rejected. A jammed
segment stops advancing, and everything behind it backs up.

Each hand-off keeps what the giver claims (`giver_reported`) and what the
receiver saw (`receiver_observed`) and is logged as a HANDOFF event, which is
what the evaluation's hand-off and sort checks read. Nothing here takes a
lock: the simulator tick (and API callers) hold twin.lock.
"""
from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Any, Deque, Dict, List, Optional, Sequence, Tuple

from .embodiment import seconds_to_ticks
from .models import CONFIG, BoxKind, BoxStatus, Cell, EventType, LogCategory, LogLevel, cell_dict, cell_tuple


@dataclass
class LineItem:
    """A box riding the line (or passing through the sorter)."""

    box_id: str
    order_id: Optional[str] = None
    task_id: Optional[str] = None
    stop_at: Optional[Cell] = None   # an arm's working cell it waits on
    ticks: int = 0                   # ticks on its current cell (or in the sorter)
    routed_to: Optional[str] = None  # the dock the sorter decided on, once, for a carton inside it

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["stop_at"] = cell_dict(self.stop_at)
        return data

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "LineItem":
        return LineItem(data["box_id"], data.get("order_id"), data.get("task_id"), cell_tuple(data.get("stop_at")),
                        int(data.get("ticks", 0)), data.get("routed_to"))


@dataclass
class Handoff:
    handoff_id: str
    box_id: str
    order_id: Optional[str]
    from_holder: str
    to_holder: str
    cell: Cell
    tick: int
    giver_reported: Dict[str, Any] = field(default_factory=dict)
    receiver_observed: Dict[str, Any] = field(default_factory=dict)
    task_id: Optional[str] = None

    @property
    def lost(self) -> bool:
        """The giver let go, but the receiver never got it."""
        return bool(self.giver_reported.get("present")) and self.receiver_observed.get("present") is False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "handoff_id": self.handoff_id, "box_id": self.box_id, "order_id": self.order_id,
            "from": self.from_holder, "to": self.to_holder, "cell": cell_dict(self.cell), "tick": self.tick,
            "giver_reported": dict(self.giver_reported), "receiver_observed": dict(self.receiver_observed),
            "task_id": self.task_id,
        }

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "Handoff":
        return Handoff(data["handoff_id"], data["box_id"], data.get("order_id"), data["from"], data["to"],
                       cell_tuple(data["cell"]), int(data["tick"]), dict(data.get("giver_reported") or {}),
                       dict(data.get("receiver_observed") or {}), data.get("task_id"))


class Conveyor:
    """One line of cells, upstream first."""

    def __init__(self, cells: Sequence[Cell]) -> None:
        self.cells: List[Cell] = list(cells)
        self._index = {cell: i for i, cell in enumerate(self.cells)}
        self.items: Dict[Cell, LineItem] = {}
        self.jams: Dict[Cell, int] = {}  # jammed cell -> the tick it jammed

    @property
    def advance_ticks(self) -> int:
        return seconds_to_ticks(CONFIG["CELL_SIZE_M"] / CONFIG["CONVEYOR_SPEED_MPS"])

    def __contains__(self, cell: Any) -> bool:
        return cell in self._index

    def item_at(self, cell: Cell) -> Optional[LineItem]:
        return self.items.get(cell)

    def is_free(self, cell: Cell) -> bool:
        return cell in self._index and cell not in self.items

    def next_cell(self, cell: Cell) -> Optional[Cell]:
        index = self._index[cell] + 1
        return self.cells[index] if index < len(self.cells) else None

    def load(self, cell: Cell, item: LineItem) -> None:
        if cell not in self._index:
            raise ValueError(f"({cell[0]},{cell[1]}) is not a conveyor cell")
        if cell in self.items:
            raise ValueError(f"Conveyor cell ({cell[0]},{cell[1]}) is occupied")
        item.ticks = 0
        self.items[cell] = item

    def unload(self, cell: Cell) -> LineItem:
        if cell not in self.items:
            raise KeyError(f"Conveyor cell ({cell[0]},{cell[1]}) is empty")
        return self.items.pop(cell)

    def jam_upstream_of(self, cell: Cell) -> Optional[Cell]:
        """A jammed segment at or upstream of `cell`, if any: while there is
        one, nothing new can reach `cell`."""
        limit = self._index.get(cell, len(self.cells) - 1)
        return next((jam for jam in self.cells[:limit + 1] if jam in self.jams), None)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "cells": [cell_dict(cell) for cell in self.cells],
            "items": [{"cell": cell_dict(cell), **item.to_dict()} for cell, item in sorted(self.items.items())],
            "jams": [{"cell": cell_dict(cell), "since_tick": tick} for cell, tick in sorted(self.jams.items())],
            "advance_ticks": self.advance_ticks,
        }


class Sorter:
    """Takes cartons off the end of the line and diverts each to a dock."""

    def __init__(self, entry: Cell, cell: Cell, lanes: Sequence[str]) -> None:
        self.entry = entry            # the last conveyor cell it takes from
        self.cell = cell              # where a carton is while it is inside
        self.lanes: List[str] = list(lanes)
        self.inside: List[LineItem] = []

    def to_dict(self) -> Dict[str, Any]:
        return {"entry": cell_dict(self.entry), "lanes": list(self.lanes),
                "inside": [item.to_dict() for item in self.inside]}


class Equipment:
    """The conveyor, the sorter and the hand-off log of one floor."""

    def __init__(self, twin: Any, conveyor: Conveyor, sorter: Sorter, arm_cells: Dict[str, Cell]) -> None:
        self.twin = twin
        self.conveyor = conveyor
        self.sorter = sorter
        #: Pack-cell zone key -> the conveyor cell its arm works.
        self.arm_cells = dict(arm_cells)
        self.handoffs: Deque[Handoff] = deque(maxlen=CONFIG["MAX_EVENTS_IN_MEMORY"])
        self._handoff_seq = 0

    @classmethod
    def for_floor(cls, twin: Any) -> Optional["Equipment"]:
        """The floor's equipment, from its layout; None on a floor with no
        conveyor and sorter (classic)."""
        zones = twin.warehouse.zones
        line, sorter = zones.get("conveyor"), zones.get("sorter")
        if line is None or sorter is None:
            return None
        cells = sorted(line.cells) if line.attributes.get("direction") == "+x" else list(line.cells)
        entry = cells[-1]
        arm_cells = {key: zone.attributes["conveyor_cell"] for key, zone in zones.items()
                     if "conveyor_cell" in zone.attributes}
        return cls(twin, Conveyor(cells), Sorter(entry, (entry[0] + 1, entry[1]), sorter.attributes["chutes"]),
                   arm_cells)

    # ---- hand-offs ------------------------------------------------------- #
    def record(self, box: Any, giver: str, receiver: str, cell: Cell, giver_reported: Dict[str, Any],
               receiver_observed: Dict[str, Any], task_id: Optional[str] = None,
               order_id: Optional[str] = None) -> Handoff:
        """Record (and announce) one transfer of `box` from `giver` to `receiver`."""
        self._handoff_seq += 1
        handoff = Handoff(
            handoff_id=f"HO-{self._handoff_seq:05d}", box_id=box.id, order_id=order_id or box.order_id,
            from_holder=giver, to_holder=receiver, cell=tuple(cell), tick=self.twin.tick_count,
            giver_reported=dict(giver_reported), receiver_observed=dict(receiver_observed), task_id=task_id,
        )
        self.handoffs.append(handoff)
        note = " — the receiver never got it" if handoff.lost else ""
        self.twin.events.emit(
            EventType.HANDOFF,
            f"{box.name}: {giver} → {receiver} at ({cell[0]},{cell[1]}){note}",
            category=LogCategory.OPERATIONS,
            level=LogLevel.WARNING if handoff.lost else LogLevel.INFO,
            task_id=task_id,
            box_id=box.id,
            position=cell_dict(cell),
            data=handoff.to_dict(),
        )
        return handoff

    def place(self, box: Any, cell: Cell, giver: str, task_id: Optional[str] = None,
              order_id: Optional[str] = None, stop_at: Optional[Cell] = None) -> Handoff:
        """`giver` (a robot or operator id) puts `box` on conveyor `cell`.

        With a HANDOFF_LOSS fault the box never arrives: the giver still
        reports it placed, the conveyor observes nothing, and the box ends
        FAILED on that cell, off the line."""
        if not self.conveyor.is_free(cell):
            raise ValueError(f"Conveyor cell ({cell[0]},{cell[1]}) is not free")
        box.position = tuple(cell)
        box.assigned_robot = box.assigned_task = None
        reported = {"present": True, "weight_kg": box.declared_weight_kg}
        if self.twin.faults.roll("handoff_loss"):
            box.set_status(BoxStatus.FAILED)
            observed: Dict[str, Any] = {"present": False}
        else:
            self.conveyor.load(tuple(cell), LineItem(box.id, order_id or box.order_id, task_id, stop_at))
            box.set_status(BoxStatus.DELIVERING)
            observed = {"present": True, "weight_kg": box.true_weight_kg}
        return self.record(box, giver, "conveyor", cell, reported, observed, task_id, order_id)

    def take(self, cell: Cell, receiver: str, task_id: Optional[str] = None) -> Tuple[Any, Handoff]:
        """`receiver` (an arm) takes the item on conveyor `cell`. An item whose
        box the twin no longer knows comes off the line all the same, freeing
        the cell, and the take raises ValueError: there is nothing to hand over."""
        item = self.conveyor.unload(tuple(cell))
        box = self.twin.find_box(item.box_id)
        if box is None:
            raise ValueError(f"the item on conveyor cell ({cell[0]},{cell[1]}) is no longer known ({item.box_id})")
        handoff = self.record(box, "conveyor", receiver, cell, {"present": True, "weight_kg": box.declared_weight_kg},
                              {"present": True, "weight_kg": box.true_weight_kg}, task_id or item.task_id,
                              item.order_id)
        return box, handoff

    def release_order(self, order_id: str) -> int:
        """Stop holding an order's items for its arm (the order failed): they
        ride on to the sorter, which rejects them. Returns how many."""
        released = 0
        for item in self.conveyor.items.values():
            if item.order_id == order_id and item.stop_at is not None:
                item.stop_at = None
                released += 1
        return released

    # ---- jams ------------------------------------------------------------ #
    def jam(self, cell: Cell) -> None:
        cell = tuple(cell)
        if cell not in self.conveyor:
            raise ValueError(f"({cell[0]},{cell[1]}) is not a conveyor cell")
        if cell in self.conveyor.jams:
            return
        self.conveyor.jams[cell] = self.twin.tick_count
        self.twin.events.emit(
            EventType.CONVEYOR_JAMMED,
            f"Conveyor jammed at ({cell[0]},{cell[1]})",
            category=LogCategory.OPERATIONS,
            level=LogLevel.WARNING,
            position=cell_dict(cell),
            data={"cell": cell_dict(cell)},
        )

    def clear_jam(self, cell: Cell) -> None:
        cell = tuple(cell)
        if cell not in self.conveyor.jams:
            raise ValueError(f"Conveyor cell ({cell[0]},{cell[1]}) is not jammed")
        since = self.conveyor.jams.pop(cell)
        for item in self.conveyor.items.values():
            item.ticks = 0  # the line restarts from standstill
        self.twin.events.emit(
            EventType.CONVEYOR_CLEARED,
            f"Conveyor jam at ({cell[0]},{cell[1]}) cleared",
            category=LogCategory.OPERATIONS,
            position=cell_dict(cell),
            data={"cell": cell_dict(cell), "jammed_ticks": self.twin.tick_count - since},
        )

    # ---- docks ----------------------------------------------------------- #
    def free_dock_cell(self, dock: str) -> Optional[Cell]:
        """A cell of `dock` with nothing waiting on it (shipped boxes are gone)."""
        taken = {box.position for box in self.twin.boxes.values() if box.status is not BoxStatus.SHIPPED}
        zone = self.twin.warehouse.zones[dock]
        return next((cell for cell in zone.cells if cell not in taken), None)

    def ship(self, dock: str) -> List[Any]:
        """An outbound truck leaves `dock`: every box delivered there is SHIPPED."""
        cells = set(self.twin.warehouse.zones[dock].cells)
        shipped = [box for box in self.twin.boxes.values()
                   if box.position in cells and box.status is BoxStatus.DELIVERED]
        for box in shipped:
            box.set_status(BoxStatus.SHIPPED)
        if shipped:
            self.twin.logger.info(LogCategory.OPERATIONS, f"Truck left {dock} with {len(shipped)} box(es)",
                                  data={"dock": dock, "box_ids": [box.id for box in shipped]})
        return shipped

    # ---- the tick -------------------------------------------------------- #
    def tick(self) -> None:
        """Advance the line, then the sorter (Simulator.tick, after robots act)."""
        self._advance_line()
        self._run_sorter()

    def _advance_line(self) -> None:
        conveyor = self.conveyor
        for cell in reversed(conveyor.cells):  # head first, so the items behind can follow
            item = conveyor.items.get(cell)
            if item is None:
                continue
            item.ticks += 1
            if cell in conveyor.jams or item.stop_at == cell or item.ticks < conveyor.advance_ticks:
                continue
            nxt = conveyor.next_cell(cell)
            if nxt is None or not conveyor.is_free(nxt):
                continue  # the end of the line (the sorter decides), or backed up
            if self.twin.faults.roll("conveyor_jam"):
                self.jam(cell)
                continue
            conveyor.items[nxt] = conveyor.items.pop(cell)
            item.ticks = 0
            box = self.twin.find_box(item.box_id)
            if box is not None:
                box.position = nxt
                box.touch()

    def _run_sorter(self) -> None:
        twin, sorter, conveyor = self.twin, self.sorter, self.conveyor
        transfer = seconds_to_ticks(CONFIG["SORTER_TRANSFER_S"])
        for item in list(sorter.inside):
            item.ticks += 1
            if item.ticks < transfer:
                continue
            box = twin.find_box(item.box_id)
            if box is None:  # removed from the twin while inside: there is nothing left to drop
                sorter.inside.remove(item)
                twin.logger.warning(LogCategory.OPERATIONS, f"{item.box_id} is no longer known — dropped from the sorter",
                                    data={"box_id": item.box_id, "order_id": item.order_id})
                continue
            lane = box.destination if box.destination in sorter.lanes else sorter.lanes[0]
            if item.routed_to is None:
                # Decided once per carton, so a full dock doesn't re-roll the
                # fault (and use up an armed one) on every tick it waits.
                item.routed_to = lane
                if twin.faults.roll("mis_sort"):
                    item.routed_to = next((other for other in sorter.lanes if other != lane), lane)
            dock = item.routed_to
            cell = self.free_dock_cell(dock)
            if cell is None:
                continue  # the dock is full: the carton waits in the sorter
            sorter.inside.remove(item)
            box.position = cell
            box.set_status(BoxStatus.DELIVERED)
            self.record(box, "sorter", dock, cell, {"present": True, "lane": lane},
                        {"present": True, "dock": dock}, item.task_id, item.order_id)
        item = conveyor.items.get(sorter.entry)
        if item is None or sorter.entry in conveyor.jams or item.stop_at == sorter.entry or item.ticks < transfer:
            return
        conveyor.unload(sorter.entry)
        box = twin.find_box(item.box_id)
        if box is None:
            return
        box.position = sorter.cell
        if box.kind is BoxKind.CARTON:
            item.ticks = 0
            sorter.inside.append(item)
            self.record(box, "conveyor", "sorter", sorter.entry, {"present": True},
                        {"present": True, "weight_kg": box.true_weight_kg}, item.task_id, item.order_id)
        else:
            box.set_status(BoxStatus.FAILED)
            self.record(box, "conveyor", "sorter_reject", sorter.entry, {"present": True},
                        {"present": True, "rejected": f"a {box.kind.value} is not a carton"},
                        item.task_id, item.order_id)

    # ---- save and load (DigitalTwin.save_state / load_state, spec §4.3) --- #
    def to_state(self) -> Dict[str, Any]:
        """Everything a save needs to restart the line exactly: each item on it
        (its stop_at and ticks too), the jams, the cartons inside the sorter,
        the hand-off records and the hand-off sequence. Items and jams keep
        their order (the jam jobs follow it)."""
        return {
            "items": [{"cell": cell_dict(cell), **item.to_dict()} for cell, item in self.conveyor.items.items()],
            "jams": [{"cell": cell_dict(cell), "since_tick": tick} for cell, tick in self.conveyor.jams.items()],
            "sorter": [item.to_dict() for item in self.sorter.inside],
            "handoffs": [handoff.to_dict() for handoff in self.handoffs],
            "handoff_seq": self._handoff_seq,
        }

    def load_state(self, data: Dict[str, Any]) -> None:
        """Replace the line, the sorter and the hand-off log with a to_state()
        snapshot; nothing that was on them before survives. It is all checked
        first: an item or jam off the line, two items on one cell, or a stop
        off the line raises ValueError and changes nothing."""
        items: Dict[Cell, LineItem] = {}
        for raw in data.get("items", []):
            cell = self._saved_cell(raw.get("cell"), "An item")
            if cell in items:
                raise ValueError(f"The save puts two items on conveyor cell ({cell[0]},{cell[1]})")
            items[cell] = LineItem.from_dict(raw)
            if items[cell].stop_at is not None:
                self._saved_cell(raw["stop_at"], f"{items[cell].box_id}'s stop")
        jams = {self._saved_cell(raw.get("cell"), "A jam"): int(raw["since_tick"]) for raw in data.get("jams", [])}
        inside = [LineItem.from_dict(raw) for raw in data.get("sorter", [])]
        handoffs = [Handoff.from_dict(raw) for raw in data.get("handoffs", [])]
        sequence = int(data.get("handoff_seq", 0))
        self.conveyor.items, self.conveyor.jams, self.sorter.inside = items, jams, inside
        self.handoffs.clear()
        self.handoffs.extend(handoffs)
        self._handoff_seq = sequence

    def _saved_cell(self, raw: Optional[Dict[str, int]], what: str) -> Cell:
        cell = cell_tuple(raw)
        if cell is None or cell not in self.conveyor:
            raise ValueError(f"{what} in the save is at {raw}, which is not a conveyor cell")
        return cell

    def to_dict(self) -> Dict[str, Any]:
        return {
            "conveyor": self.conveyor.to_dict(),
            "sorter": self.sorter.to_dict(),
            "arm_cells": {key: cell_dict(cell) for key, cell in self.arm_cells.items()},
            "handoffs": len(self.handoffs),
        }

    def view(self) -> Dict[str, Any]:
        """What the dashboard draws (snapshot["equipment"], GET /api/equipment):
        to_dict() plus each item on the line — its cell, box, kind and order —
        and each jammed cell, both upstream first."""
        conveyor = self.conveyor
        data = self.to_dict()
        items = []
        for x, y in conveyor.cells:
            item = conveyor.items.get((x, y))
            if item is None:
                continue
            box = self.twin.find_box(item.box_id)
            items.append({"x": x, "y": y, "box_id": item.box_id, "kind": box.kind.value if box is not None else None,
                          "order_id": item.order_id})
        data["item_cells"] = items
        data["jammed_cells"] = [{"x": x, "y": y} for x, y in conveyor.cells if (x, y) in conveyor.jams]
        return data
