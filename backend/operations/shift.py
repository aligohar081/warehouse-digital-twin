"""The shift engine (multi-embodiment spec §11.1, §11.4): it keeps the
distribution-centre floor busy on its own.

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
controls are plan 1c's.
"""
from __future__ import annotations

import math
import random
from typing import Any, Dict, List, Optional

from ..models import CONFIG, BoxKind, BoxStatus, EventType, LogCategory
from .orders import OrderBook

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
        self.counters: Dict[str, int] = {stream: 0 for stream in DEFAULT_RATES}
        self.counters["skipped_orders"] = 0

    def _on_event(self, event: Dict[str, Any]) -> None:
        self.orders.on_event(event)

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
        """Change the pace, the seed (which resets the RNG) or any rate."""
        if pace is not None:
            if isinstance(pace, bool) or not math.isfinite(float(pace)) or float(pace) <= 0:
                raise ValueError("pace must be a finite number greater than zero")
            self.pace = float(pace)
        for stream, rate in (rates or {}).items():
            if stream not in DEFAULT_RATES:
                raise ValueError(f"Unknown rate {stream!r} (known: {sorted(DEFAULT_RATES)})")
            if isinstance(rate, bool) or not math.isfinite(float(rate)) or float(rate) < 0:
                raise ValueError(f"{stream} must be a finite number, zero or more per hour")
            self.rates[stream] = float(rate)
        if seed is not None:
            if isinstance(seed, bool) or not isinstance(seed, int):
                raise ValueError("seed must be a whole number")
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
