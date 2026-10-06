"""The shift engine (multi-embodiment spec §11.1, §11.3, §11.4): it keeps
the distribution-centre floor busy on its own, and moves its people
(activities.py).

Seeded streams generate the work — inbound trucks, customer orders, full-
pallet orders, cycle counts, patrols, returns and outbound departures — at
per-hour rates × `pace`, each turning into an order chain (orders.py) or a
job created through the gate (twin.tasks.create_task, internal=True). The
engine has its own random.Random(seed), so the same seed and ticks give the
same orders and jobs. It starts PAUSED; start() begins generating and pause()
stops it, while orders already in flight carry on. Resuming after a pause
carries on from where it stopped: every stream's next due time moves on by the
time spent paused, so nothing that fell due meanwhile fires in a burst. The
shift clock runs from SHIFT_START_HOUR with the simulation time. Its HTTP
controls and panel are backend/operations_api.py's.
"""
from __future__ import annotations

import math
import random
from collections import deque
from typing import Any, Deque, Dict, List, Optional

from ..models import CONFIG, BoxKind, BoxStatus, EventType, LogCategory
from .orders import FAILED, IN_PROGRESS, OPEN, OrderBook, order_number

#: Generated orders and jobs per simulated hour, before × pace (spec §11.1).
DEFAULT_RATES: Dict[str, float] = {
    "trucks": 3.0,            # every 20 sim-min
    "customer_orders": 30.0,
    "pallet_orders": 4.0,
    "cycle_counts": 6.0,      # one rack face every 10 sim-min
    "patrols": 4.0,           # every 15 sim-min
    "returns": 6.0,
    "departures": 2.0,        # each outbound dock every 30 sim-min
}
#: Seconds into the shift each stream first fires (so a demo starts at once;
#: departures wait a full interval, for something to have reached the docks).
FIRST_AT_S: Dict[str, float] = {
    "customer_orders": 10.0, "trucks": 30.0, "cycle_counts": 60.0, "patrols": 90.0,
    "pallet_orders": 120.0, "returns": 150.0, "departures": 1800.0,
}
INBOUND_DOCKS = ("dock_1", "dock_2")
OUTBOUND_DOCKS = ("dock_3", "dock_4", "dock_5")
CARTON_LANES = ("dock_4", "dock_5")
#: The SKUs inbound pallets and returns carry when the floor stocks none yet.
DEFAULT_SKUS = tuple(f"SKU-{number:03d}" for number in range(1, 41))
#: How many failed orders and exceptions the shift panel lists (the latest).
PANEL_LIST_LIMIT = 20
#: The events the shift panel lists as exceptions (spec §10.3, §11.5): a
#: safety wait escalated, an order failed, a stock variance too big to
#: reconcile on the spot.
EXCEPTION_EVENTS = (EventType.SAFETY_WAIT_ESCALATED.value, EventType.ORDER_FAILED.value,
                    EventType.STOCK_VARIANCE_DETECTED.value)


def _finite(value: Any, what: str) -> float:
    """`value` as a finite float, else ValueError naming `what` — for a bool, a
    non-number, NaN, an infinity, or an int too big for a float."""
    if isinstance(value, bool):
        raise ValueError(f"{what} must be a number, not {value!r}")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError(f"{what} must be a finite number, not {value!r}") from None
    if not math.isfinite(number):
        raise ValueError(f"{what} must be a finite number, not {value!r}")
    return number


def shift_hour(simulation_time: float) -> int:
    """The shift clock's hour: SHIFT_START_HOUR plus the simulated time, wrapping at 24."""
    return int((CONFIG["SHIFT_START_HOUR"] * 3600 + simulation_time) // 3600) % 24


def shift_clock(simulation_time: float) -> str:
    """The shift clock as HH:MM."""
    seconds = int(CONFIG["SHIFT_START_HOUR"] * 3600 + simulation_time) % 86400
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}"


class ShiftEngine:
    RUNNING, PAUSED = "RUNNING", "PAUSED"

    def __init__(self, twin: Any, seed: int = 42, pace: float = 1.0) -> None:
        self.twin = twin
        self.seed = int(seed)
        self.pace = float(pace)
        self.rates: Dict[str, float] = dict(DEFAULT_RATES)
        self.reset()
        twin.events.subscribe(self._on_event)

    def reset(self) -> None:
        """A fresh, paused shift: no orders, the RNG back at its seed."""
        self.status = self.PAUSED
        self.rng = random.Random(self.seed)
        self.orders = OrderBook(self.twin)
        self._next_due: Dict[str, float] = {}
        self._paused_at: Optional[float] = None  # simulation time pause() was called
        self._truck_count = 0
        self._face_index = 0
        # Operator id -> the simulation time a person who stepped aside for a
        # forklift waits until before going back (activities.move_people).
        self.yield_until: Dict[str, float] = {}
        self.counters: Dict[str, int] = {stream: 0 for stream in DEFAULT_RATES}
        self.counters["skipped_orders"] = 0
        # The latest exceptions for the shift panel, kept as they happen (the
        # in-memory event buffer forgets them on a long shift).
        self.exceptions: Deque[Dict[str, Any]] = deque(maxlen=PANEL_LIST_LIMIT)

    def _on_event(self, event: Dict[str, Any]) -> None:
        self.orders.on_event(event)
        self._note_exception(event)

    def _note_exception(self, event: Dict[str, Any]) -> None:
        kind = event.get("event")
        if kind not in EXCEPTION_EVENTS:
            return
        if kind == EventType.STOCK_VARIANCE_DETECTED.value and (event.get("data") or {}).get("auto_reconciled"):
            return  # corrected on the spot: nothing for anyone to do
        self.exceptions.append({"type": kind, "message": event.get("message"), "tick": self.twin.tick_count})

    # ---- save and load (DigitalTwin.save_state / load_state, spec §4.3) --- #
    def to_state(self) -> Dict[str, Any]:
        """Everything a save needs to carry the shift on exactly: its status,
        config and RNG state, when each stream is next due (and when it was
        paused), the truck and rack-face rotations, who is stepping aside
        until when, the counters, and every order."""
        version, internal, gauss = self.rng.getstate()
        return {
            "status": self.status, "seed": self.seed, "pace": self.pace, "rates": dict(self.rates),
            "rng": [version, list(internal), gauss],
            "next_due": dict(self._next_due), "paused_at": self._paused_at,
            "truck_count": self._truck_count, "face_index": self._face_index,
            "yield_until": dict(self.yield_until), "counters": dict(self.counters),
            "orders": self.orders.to_state(),
        }

    def load_state(self, data: Dict[str, Any]) -> None:
        """Restore a to_state() snapshot into this engine — the one twin.events
        calls (it subscribed once, at construction), so a load never adds a
        second listener. It is all checked first: a damaged snapshot raises
        ValueError and leaves the engine as it was."""
        status = data.get("status")
        if status not in (self.RUNNING, self.PAUSED):
            raise ValueError(f"Unknown shift status {status!r} (known: {[self.RUNNING, self.PAUSED]})")
        seed = data.get("seed")
        if isinstance(seed, bool) or not isinstance(seed, int):
            raise ValueError(f"The shift seed must be a whole number, not {seed!r}")
        pace = self._saved_number(data.get("pace"), "pace")
        if pace <= 0:
            raise ValueError("pace must be a finite number greater than zero")
        rates = dict(DEFAULT_RATES)
        for stream, rate in (data.get("rates") or {}).items():
            if stream not in DEFAULT_RATES:
                raise ValueError(f"Unknown rate {stream!r} (known: {sorted(DEFAULT_RATES)})")
            rates[stream] = self._saved_number(rate, stream)
        next_due = {}
        for stream, due in (data.get("next_due") or {}).items():
            if stream not in DEFAULT_RATES:
                raise ValueError(f"Unknown stream {stream!r} in the shift's next due times")
            next_due[stream] = self._saved_number(due, stream)
        if status == self.RUNNING and set(next_due) != set(DEFAULT_RATES):
            raise ValueError("A running shift needs a next due time for every stream")
        paused_at = data.get("paused_at")
        paused_at = None if paused_at is None else self._saved_number(paused_at, "paused_at")
        rng = random.Random()
        try:
            version, internal, gauss = data["rng"]
            rng.setstate((version, tuple(internal), gauss))
        except (KeyError, TypeError, ValueError, OverflowError):
            raise ValueError("The shift's random number state is missing or damaged") from None
        counts = {key: data.get(key, 0) for key in ("truck_count", "face_index")}
        counters = {**{stream: 0 for stream in DEFAULT_RATES}, "skipped_orders": 0, **(data.get("counters") or {})}
        for key, value in [*counts.items(), *counters.items()]:
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"The shift's {key} must be a whole number, 0 or more, not {value!r}")
        yield_until = {str(key): self._saved_number(until, f"{key}'s step-aside")
                       for key, until in (data.get("yield_until") or {}).items()}
        orders = OrderBook(self.twin)
        orders.load_state(data.get("orders") or {})
        self.status, self.seed, self.pace, self.rates, self.rng = status, seed, pace, rates, rng
        self._next_due, self._paused_at = next_due, paused_at
        self._truck_count, self._face_index = counts["truck_count"], counts["face_index"]
        self.yield_until, self.counters, self.orders = yield_until, counters, orders
        self.exceptions.clear()  # the panel's exceptions belong to the shift before the load

    @staticmethod
    def _saved_number(value: Any, what: str) -> float:
        """A number from a saved shift: finite, and never negative."""
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise ValueError(f"The shift's {what} must be a finite number, 0 or more, not {value!r}")
        return float(value)

    # ---- controls (plan 1c's /api/shift calls these) --------------------- #
    def start(self) -> None:
        if self.twin.layout_name == "classic":
            raise ValueError("The classic floor has no shift engine")
        if self.status == self.RUNNING:
            return
        now = self.twin.simulation_time
        if self._paused_at is not None:  # resuming: the time paused doesn't count towards what is due
            for stream in self._next_due:
                self._next_due[stream] += now - self._paused_at
            self._paused_at = None
        for stream in DEFAULT_RATES:
            self._next_due.setdefault(stream, now + FIRST_AT_S[stream])
        self.status = self.RUNNING
        self.twin.events.emit(EventType.SHIFT_STARTED, f"Shift started at {self.clock()} (seed {self.seed}, "
                              f"pace {self.pace:g})", category=LogCategory.OPERATIONS,
                              data={"clock": self.clock(), "seed": self.seed, "pace": self.pace})

    def pause(self) -> None:
        if self.status == self.PAUSED:
            return
        self.status = self.PAUSED
        self._paused_at = self.twin.simulation_time
        self.twin.events.emit(EventType.SHIFT_PAUSED, f"Shift paused at {self.clock()}",
                              category=LogCategory.OPERATIONS, data={"clock": self.clock()})

    def configure(self, pace: Optional[float] = None, seed: Optional[int] = None,
                  rates: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
        """Change the pace, the seed (which resets the RNG) or any rate. Every
        value is checked before anything changes, so a bad one changes nothing."""
        new_pace = None
        if pace is not None:
            new_pace = _finite(pace, "pace")
            if new_pace <= 0:
                raise ValueError("pace must be a finite number greater than zero")
        if rates is not None and not isinstance(rates, dict):
            raise ValueError("rates must map a stream to its rate per hour")
        new_rates: Dict[str, float] = {}
        for stream, rate in (rates or {}).items():
            if stream not in DEFAULT_RATES:
                raise ValueError(f"Unknown rate {stream!r} (known: {sorted(DEFAULT_RATES)})")
            new_rates[stream] = _finite(rate, stream)
            if new_rates[stream] < 0:
                raise ValueError(f"{stream} must be a finite number, zero or more per hour")
        if seed is not None and (isinstance(seed, bool) or not isinstance(seed, int)):
            raise ValueError("seed must be a whole number")
        if new_pace is not None:
            self.pace = new_pace
        self.rates.update(new_rates)
        if seed is not None:
            self.seed = seed
            self.rng = random.Random(seed)
        return self.config()

    def config(self) -> Dict[str, Any]:
        return {"seed": self.seed, "pace": self.pace, "rates": dict(self.rates)}

    def clock(self) -> str:
        return shift_clock(self.twin.simulation_time)

    def status_dict(self) -> Dict[str, Any]:
        hours = max(self.twin.simulation_time / 3600.0, 1e-9)
        counts = self.orders.counts()
        done = sum(kind["DONE"] for kind in counts.values())
        return {"status": self.status, "clock": self.clock(), "config": self.config(),
                "counters": dict(self.counters), "orders": counts,
                "throughput_per_hour": round(done / hours, 1)}

    def panel(self) -> Dict[str, Any]:
        """The shift panel (GET /api/shift, snapshot["shift"]): status_dict()
        plus how many orders are in flight and waiting, and the latest failed
        orders and exceptions, newest first."""
        panel = self.status_dict()
        panel["in_flight"] = sum(kind[IN_PROGRESS] for kind in panel["orders"].values())
        panel["backlog"] = sum(kind[OPEN] for kind in panel["orders"].values())
        failed = sorted((order for order in self.orders.orders.values() if order.status == FAILED),
                        key=lambda order: (order.completed_at or 0.0, order_number(order.order_id)), reverse=True)
        panel["failed_orders"] = [{"order_id": order.order_id, "kind": order.kind, "reason": order.failure_reason}
                                  for order in failed[:PANEL_LIST_LIMIT]]
        panel["exceptions"] = [dict(entry) for entry in reversed(self.exceptions)]
        return panel

    # ---- the tick -------------------------------------------------------- #
    def tick(self) -> None:
        """Simulator.tick: generate what is due (when running), then move
        every order on."""
        if self.status == self.RUNNING:
            now = self.twin.simulation_time
            for stream in DEFAULT_RATES:
                rate = self.rates[stream] * self.pace
                if rate <= 0:  # stopped: it falls due from now when restored, not from when it stopped
                    self._next_due[stream] = max(self._next_due[stream], now)
                    continue
                if now < self._next_due[stream]:
                    continue
                self._next_due[stream] += 3600.0 / rate
                self.counters[stream] += 1
                getattr(self, f"_generate_{stream}")()
            if self.twin.tick_count % CONFIG["SHIFT_CHECK_EVERY_TICKS"] == 0:
                from .activities import move_people  # deferred: activities.py imports this module

                move_people(self)
        self.orders.advance()

    def _skus(self) -> List[str]:
        return self.twin.stock.skus() or list(DEFAULT_SKUS)

    def _free_cells(self, zone: str) -> List[Any]:
        taken = {box.position for box in self.twin.boxes.values() if box.status is not BoxStatus.SHIPPED}
        return [cell for cell in self.twin.warehouse.zones[zone].cells if cell not in taken]

    def _generate_trucks(self) -> None:
        """An inbound truck, alternating dock_1 and dock_2, with 4–8 pallets
        (150–900 kg). A MISDECLARED_WEIGHT fault makes a pallet 1.1–1.6× heavier
        than its paperwork says."""
        dock = INBOUND_DOCKS[self._truck_count % len(INBOUND_DOCKS)]
        self._truck_count += 1
        count = self.rng.randint(4, 8)
        pallets = []
        for cell in self._free_cells(dock)[:count]:
            declared = round(self.rng.uniform(150.0, 900.0), 1)
            true = declared
            if self.twin.faults.roll("misdeclared_weight", rng=self.rng):
                true = round(declared * self.rng.uniform(1.1, 1.6), 1)
            box = self.twin.add_box(kind="PALLET", position=cell, sku=self.rng.choice(self._skus()),
                                    quantity=self.rng.randint(20, 60), weight=declared, true_weight_kg=true)
            pallets.append(box.id)
        if pallets:
            self.orders.inbound(dock, pallets)

    def _generate_customer_orders(self) -> None:
        """1–4 lines of 1–3 units, for SKUs a tote has enough of, to dock_4 or dock_5."""
        stocked: Dict[str, int] = {}
        for location in self.twin.stock.locations():
            box = self.twin.find_box(location.box_id)
            if box is not None and box.kind is BoxKind.TOTE:
                stocked[location.sku] = max(stocked.get(location.sku, 0), location.recorded_qty)
        lines = []
        for _ in range(self.rng.randint(1, 4)):
            units = self.rng.randint(1, 3)
            choices = sorted(sku for sku, quantity in stocked.items() if quantity >= units)
            if choices:
                lines.append({"sku": self.rng.choice(choices), "units": units})
        lane = self.rng.choice(CARTON_LANES)
        if not lines:
            self.counters["skipped_orders"] += 1
            return
        self.orders.customer(lines, lane)

    def _generate_pallet_orders(self) -> None:
        """A stored pallet out to the outbound truck."""
        promised = {line.get("box_id") for order in self.orders.orders.values() if not order.is_terminal
                    for line in order.lines}
        stored = sorted(location.box_id for location in self.twin.stock.locations()
                        if location.box_id not in promised and self._stored_pallet(location.box_id))
        if not stored:
            self.counters["skipped_orders"] += 1
            return
        self.orders.pallet(self.rng.choice(stored))

    def _stored_pallet(self, box_id: str) -> bool:
        box = self.twin.find_box(box_id)
        return (box is not None and box.kind is BoxKind.PALLET and box.status is BoxStatus.STORED
                and box.true_slot is None and self.twin.tasks.active_task_for_box(box.id) is None)

    def _generate_cycle_counts(self) -> None:
        """The next of the 36 pallet rack faces, round-robin."""
        faces = sorted({slot.cell for slot in self.twin.warehouse.slots.values() if slot.kind == "PALLET"},
                       key=lambda cell: (cell[1], cell[0]))
        if not faces:
            return
        x, y = faces[self._face_index % len(faces)]
        self._face_index += 1
        self.orders.count(f"{x},{y}")

    def _generate_patrols(self) -> None:
        task = self.twin.tasks.create_task({"type": "PATROL"}, internal=True)
        if task.status.value == "FAILED":
            self.twin.logger.warning(LogCategory.OPERATIONS, f"Patrol not sent: {task.error}")

    def _generate_returns(self) -> None:
        """A returned tote turns up in returns_qc."""
        cells = self._free_cells("returns_qc")
        if not cells:
            self.counters["skipped_orders"] += 1
            return
        quantity = self.rng.randint(5, 20)
        weight = round(CONFIG["TOTE_TARE_KG"] + quantity * self.rng.uniform(0.2, 1.0), 2)
        box = self.twin.add_box(kind="TOTE", position=self.rng.choice(cells), sku=self.rng.choice(self._skus()),
                                quantity=quantity, weight=weight)
        self.orders.returned(box.id)

    def _generate_departures(self) -> None:
        """The outbound trucks leave dock_3, dock_4 and dock_5: what reached
        each is SHIPPED."""
        if self.twin.equipment is None:
            return
        for dock in OUTBOUND_DOCKS:
            self.twin.equipment.ship(dock)
