"""Orders and their job chains (multi-embodiment spec §11.2).

An Order is a fixed chain of stages for its kind:

    CUSTOMER  per line: TOTE_TO_STATION → PICK_ITEMS or MANUAL_PICK → RETURN_TOTE;
              then PACK_ORDER, and SORT (the carton reaches its lane's dock)
    INBOUND   per pallet: UNLOAD_TRUCK → PUTAWAY_PALLET
    PALLET    RETRIEVE_PALLET → LOAD_TRUCK
    COUNT     CYCLE_COUNT
    RETURN    RETURNS_PUTAWAY

A job stage creates its task once the stages it follows are done — through
twin.tasks.create_task(internal=True), so the gate always applies — and is
done when that task completes. The conveyor stages advance on hand-off
events instead: SORT is done when the sorter hands the order's carton to a
dock, and an order item lost at a hand-off fails the order. A failed or
rejected stage is retried once, ORDER_RETRY_DELAY_S later; after that the
order FAILS with the task's failure or rejection reason. A retry only asks
for what is left: a PICK for the units its failed attempt did not put on the
line, a PACK_ORDER for the items not yet in the carton (the same carton, in
the same cell). A PACK_ORDER that can't be resumed that way fails the order.

A customer order needs a pick station and a pack cell to itself: it waits,
OPEN and holding neither, until both are free and its first line's tote can
be had, so items for one arm never queue behind another order's on the line.
Its PACK_ORDER starts with it, so the arm is waiting when the first item
arrives. Pick 2 is a person's station: an order goes there when someone who
picks there is on the floor and Pick 1 is taken or has no picker robot.

A failed order cancels its live jobs — as the system, giving the reason —
except a RETURN_TOTE already taking a tote home, and sends home every tote it
took out that is not home or on its way. A tote leg that fails sends its tote
home too, before the stage is retried. An order whose own bookkeeping raises
fails alone, with the error as its reason; the other orders still advance.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .. import people
from ..eligibility import box_kind_ok, payload_ok, reach_ok, robot_eligibility
from ..jobs import JOB_SPECS, tote_is_home
from ..models import (ActionType, BoxKind, BoxStatus, EventType, LogCategory, LogLevel, OperatorStatus, TaskStatus,
                      TaskType)

ORDER_KINDS = ("CUSTOMER", "INBOUND", "PALLET", "COUNT", "RETURN")
OPEN, IN_PROGRESS, DONE, FAILED = "OPEN", "IN_PROGRESS", "DONE", "FAILED"
WAITING, ACTIVE = "WAITING", "ACTIVE"

#: Seconds before a failed or rejected stage is tried once more.
ORDER_RETRY_DELAY_S = 10.0
#: The workers who pick at Pick 2 (the demo workforce's Sam and Lee).
PICK_STATION_PEOPLE = ("E-10001", "E-10002")
PICK_STATIONS = ("pick_station_1", "pick_station_2")
PACK_CELLS = ("pack_cell_1", "pack_cell_2")


class OrderError(Exception):
    """A stage can't be built: the reason fails or retries it."""


def order_number(order_id: str) -> int:
    """ORD-0042 -> 42, so orders sort by number, not by their id's text."""
    digits = order_id.rsplit("-", 1)[-1]
    return int(digits) if digits.isdigit() else -1


def _saved_fields(cls: Any, data: Dict[str, Any], required: List[str], what: str) -> None:
    """A saved order or stage names only fields `cls` has, and every required one."""
    known = list(cls.__dataclass_fields__)
    unknown = sorted(set(data) - set(known))
    if unknown:
        raise ValueError(f"Unknown {what} field(s) {unknown} (known: {known})")
    missing = [key for key in required if key not in data]
    if missing:
        raise ValueError(f"A saved {what} is missing {missing}")


@dataclass
class Stage:
    name: str
    task_type: Optional[str]          # None for SORT, which the conveyor finishes
    payload: Dict[str, Any] = field(default_factory=dict)
    after: List[int] = field(default_factory=list)
    status: str = WAITING
    task_id: Optional[str] = None
    attempts: int = 0
    retry_at: Optional[float] = None  # simulation time
    error: Optional[str] = None
    line: Optional[int] = None        # which order line a customer stage serves

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "Stage":
        """A stage from to_dict() (a save); an unknown field or status raises ValueError."""
        _saved_fields(Stage, data, ["name", "task_type"], "stage")
        stage = Stage(**{**data, "payload": dict(data.get("payload") or {}), "after": list(data.get("after") or [])})
        if stage.status not in (WAITING, ACTIVE, DONE, FAILED):
            raise ValueError(f"Stage {stage.name!r} has an unknown status {stage.status!r}")
        return stage


@dataclass
class Order:
    order_id: str
    kind: str
    lines: List[Dict[str, Any]] = field(default_factory=list)
    status: str = OPEN
    stages: List[Stage] = field(default_factory=list)
    current_stage: int = 0
    failure_reason: Optional[str] = None
    created_at: float = 0.0
    completed_at: Optional[float] = None
    lane: Optional[str] = None
    pick_station: Optional[str] = None
    pack_cell: Optional[str] = None
    sorted_to: Optional[str] = None
    lost: List[str] = field(default_factory=list)

    @property
    def is_terminal(self) -> bool:
        return self.status in (DONE, FAILED)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["stages"] = [stage.to_dict() for stage in self.stages]
        return data

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "Order":
        """An order from to_dict() (a save), its stages with it. An unknown
        field, kind or status, or a stage that follows one the order doesn't
        have, raises ValueError."""
        _saved_fields(Order, data, ["order_id", "kind"], "order")
        order = Order(**{**data, "lines": [dict(line) for line in data.get("lines") or []],
                         "stages": [Stage.from_dict(stage) for stage in data.get("stages") or []],
                         "lost": list(data.get("lost") or [])})
        if order.kind not in ORDER_KINDS:
            raise ValueError(f"{order.order_id} has an unknown kind {order.kind!r} (known: {list(ORDER_KINDS)})")
        if order.status not in (OPEN, IN_PROGRESS, DONE, FAILED):
            raise ValueError(f"{order.order_id} has an unknown status {order.status!r}")
        for stage in order.stages:
            if any(not isinstance(index, int) or not 0 <= index < len(order.stages) for index in stage.after):
                raise ValueError(f"{order.order_id}: {stage.name} follows a stage the order doesn't have")
        return order


class OrderBook:
    """Every order of one twin, advanced once a tick by the shift engine."""

    def __init__(self, twin: Any) -> None:
        self.twin = twin
        self.orders: Dict[str, Order] = {}
        self._seq = 0

    # ---- save and load (ShiftEngine.to_state / load_state) --------------- #
    def to_state(self) -> Dict[str, Any]:
        """Every order in the order it was created (advance() goes through them
        in that order), and the id sequence."""
        return {"orders": [order.to_dict() for order in self.orders.values()], "seq": self._seq}

    def load_state(self, data: Dict[str, Any]) -> None:
        """Replace every order with a to_state() snapshot. It is all checked
        first: a damaged order, an id twice or a sequence behind its orders
        raises ValueError and changes nothing."""
        orders: Dict[str, Order] = {}
        for raw in data.get("orders", []):
            order = Order.from_dict(raw)
            if order.order_id in orders:
                raise ValueError(f"The save has {order.order_id} twice")
            orders[order.order_id] = order
        seq = data.get("seq", 0)
        numbers = [int(key.rsplit("-", 1)[-1]) for key in orders if key.rsplit("-", 1)[-1].isdigit()]
        if isinstance(seq, bool) or not isinstance(seq, int) or seq < max(numbers, default=0):
            raise ValueError(f"The order sequence {seq!r} must be a whole number, at least {max(numbers, default=0)}")
        self.orders, self._seq = orders, seq

    # ---- creating orders ------------------------------------------------- #
    def _new(self, kind: str, stages: List[Stage], **fields: Any) -> Order:
        self._seq += 1
        order = Order(f"ORD-{self._seq:04d}", kind, stages=stages, created_at=self.twin.simulation_time, **fields)
        self.orders[order.order_id] = order
        self.twin.events.emit(
            EventType.ORDER_CREATED,
            f"{order.order_id} created: {kind.lower()} order, {len(stages)} stage(s)",
            category=LogCategory.OPERATIONS,
            data={"order_id": order.order_id, "kind": kind, "lines": [dict(line) for line in order.lines],
                  "lane": order.lane,
                  "stages": [stage.name for stage in stages]},
        )
        return order

    def customer(self, lines: List[Dict[str, Any]], lane: str) -> Order:
        """`lines` is [{"sku", "units"}]: per line a tote comes to a pick
        station, units are picked onto the line and the tote goes home; then
        the order is packed and sorted to `lane`."""
        stages: List[Stage] = []
        previous_pick: Optional[int] = None
        for number, line in enumerate(lines):
            tote = len(stages)
            stages.append(Stage(f"TOTE_TO_STATION line {number + 1}", "TOTE_TO_STATION",
                                after=[previous_pick] if previous_pick is not None else [], line=number))
            stages.append(Stage(f"PICK line {number + 1}", "PICK", after=[tote], line=number))
            previous_pick = len(stages) - 1
            stages.append(Stage(f"RETURN_TOTE line {number + 1}", "RETURN_TOTE", after=[previous_pick], line=number))
        pack = len(stages)
        stages.append(Stage("PACK_ORDER", "PACK_ORDER"))
        stages.append(Stage("SORT", None, after=[pack]))
        return self._new("CUSTOMER", stages, lines=[dict(line) for line in lines], lane=lane)

    def inbound(self, dock: str, pallet_ids: List[str]) -> Order:
        """A truck at `dock`: each pallet unloaded to intake, then put away."""
        stages: List[Stage] = []
        for box_id in pallet_ids:
            stages.append(Stage(f"UNLOAD_TRUCK {box_id}", "UNLOAD_TRUCK", {"box_id": box_id}))
            stages.append(Stage(f"PUTAWAY_PALLET {box_id}", "PUTAWAY_PALLET", {"box_id": box_id},
                                after=[len(stages) - 1]))
        return self._new("INBOUND", stages, lines=[{"box_id": box_id, "dock": dock} for box_id in pallet_ids])

    def pallet(self, box_id: str) -> Order:
        """A full pallet out of the racks and onto the outbound truck."""
        return self._new("PALLET", [Stage("RETRIEVE_PALLET", "RETRIEVE_PALLET", {"box_id": box_id}),
                                    Stage("LOAD_TRUCK", "LOAD_TRUCK", {"box_id": box_id}, after=[0])],
                         lines=[{"box_id": box_id}])

    def count(self, face: str) -> Order:
        return self._new("COUNT", [Stage("CYCLE_COUNT", "CYCLE_COUNT", {"face": face})], lines=[{"face": face}])

    def returned(self, box_id: str) -> Order:
        return self._new("RETURN", [Stage("RETURNS_PUTAWAY", "RETURNS_PUTAWAY", {"box_id": box_id})],
                         lines=[{"box_id": box_id}])

    # ---- hand-off events (ShiftEngine passes every event on) ------------- #
    def on_event(self, event: Dict[str, Any]) -> None:
        if event.get("event") != EventType.HANDOFF.value:
            return
        data = event.get("data") or {}
        order = self.orders.get(data.get("order_id"))
        if order is None or order.is_terminal:
            return
        if data.get("from") == "sorter":
            order.sorted_to = data.get("to")
        elif data.get("receiver_observed", {}).get("present") is False or data.get("to") == "sorter_reject":
            order.lost.append(data.get("box_id"))

    # ---- advancing ------------------------------------------------------- #
    def advance(self) -> None:
        for order in list(self.orders.values()):
            if order.is_terminal:
                continue
            try:
                self._advance(order)
            except Exception as exc:  # one broken order fails alone: the others still move on
                self._fail_broken(order, exc)

    def _fail_broken(self, order: Order, exc: Exception) -> None:
        """An order whose own advancing raised fails with the error as its reason."""
        reason = f"The order hit an error: {type(exc).__name__}: {exc}"
        self.twin.logger.error(LogCategory.OPERATIONS, f"{order.order_id}: {reason}",
                               data={"order_id": order.order_id})
        if order.is_terminal:
            return
        try:
            self._fail(order, reason)
        except Exception:  # failing it cleanly raised too: still take it out of the running
            order.status, order.failure_reason = FAILED, reason
            order.completed_at = self.twin.simulation_time

    def _advance(self, order: Order) -> None:
        if order.lost:
            self._fail(order, f"item {order.lost[0]} was lost at a hand-off")
            return
        if order.kind == "CUSTOMER" and order.status == OPEN and not self._acquire(order):
            return
        if order.status == OPEN:
            order.status = IN_PROGRESS
        for index, stage in enumerate(order.stages):
            if order.is_terminal:
                return
            if stage.status == ACTIVE:
                self._watch(order, index, stage)
            elif stage.status == WAITING and all(order.stages[i].status == DONE for i in stage.after):
                if stage.retry_at is None or self.twin.simulation_time >= stage.retry_at:
                    self._start(order, index, stage)
        if not order.is_terminal and all(stage.status == DONE for stage in order.stages):
            order.status = DONE
            order.completed_at = self.twin.simulation_time
            self.twin.events.emit(
                EventType.ORDER_COMPLETED,
                f"{order.order_id} completed ({order.kind.lower()})",
                category=LogCategory.OPERATIONS,
                data={"order_id": order.order_id, "kind": order.kind, "sorted_to": order.sorted_to,
                      "seconds": round(order.completed_at - order.created_at, 1)},
            )

    def _watch(self, order: Order, index: int, stage: Stage) -> None:
        if stage.task_type is None:  # SORT: done when the sorter hands the carton to a dock
            if order.sorted_to is not None:
                self._done(order, index, stage)
            return
        task = self.twin.tasks.get(stage.task_id)
        if task is None or task.status in (TaskStatus.FAILED, TaskStatus.CANCELLED):
            self._stage_failed(order, stage, task.error if task is not None and task.error else
                               f"{stage.task_id} was {task.status.value.lower() if task else 'lost'}")
        elif task.status is TaskStatus.COMPLETED:
            self._done(order, index, stage)

    def _start(self, order: Order, index: int, stage: Stage) -> None:
        if stage.task_type is None:
            stage.status = ACTIVE
            return
        if self._pick_finished(order, stage):  # a failed attempt had already put every unit on the line
            self._done(order, index, stage)
            return
        try:
            payload = self._payload(order, stage)
        except OrderError as exc:
            stage.attempts += 1
            self._stage_failed(order, stage, str(exc))
            return
        if payload is None:
            if stage.attempts:  # its one retry has nothing to run on: fail it, don't leave it waiting
                stage.attempts += 1
                self._stage_failed(order, stage, f"{stage.error} (and no tote was free to try it again with)")
            return  # a first try waits: what it needs is busy (a tote at another station), try next tick
        stage.attempts += 1
        task = self.twin.tasks.create_task(payload, internal=True)
        stage.task_id = task.id
        if task.status is TaskStatus.FAILED:  # the gate rejected it
            self._stage_failed(order, stage, task.error or "rejected")
            return
        stage.status = ACTIVE
        order.current_stage = index
        self.twin.events.emit(
            EventType.ORDER_STAGE_ADVANCED,
            f"{order.order_id}: {stage.name} started as {task.id}",
            category=LogCategory.OPERATIONS,
            task_id=task.id,
            data={"order_id": order.order_id, "stage": stage.name, "index": index, "task_id": task.id,
                  "attempt": stage.attempts},
        )

    def _done(self, order: Order, index: int, stage: Stage) -> None:
        stage.status = DONE
        self.twin.events.emit(
            EventType.ORDER_STAGE_ADVANCED,
            f"{order.order_id}: {stage.name} done",
            category=LogCategory.OPERATIONS,
            task_id=stage.task_id,
            data={"order_id": order.order_id, "stage": stage.name, "index": index, "task_id": stage.task_id,
                  "done": True},
        )

    def _stage_failed(self, order: Order, stage: Stage, reason: str) -> None:
        stage.error = reason
        self._bank_placed_units(order, stage)
        if order.kind == "CUSTOMER" and stage.task_type == "TOTE_TO_STATION":
            # A tote leg that ended early may have set its tote down on the way:
            # it goes home, and a retry takes a tote that is home.
            self._send_tote_home(order.lines[stage.line].get("tote_id"), f"{order.order_id}: {stage.name} failed")
        unresumable = self._cannot_resume_pack(order, stage)
        if unresumable:
            stage.status = FAILED
            self._fail(order, f"{reason}; {unresumable}")
            return
        if stage.attempts < 2:
            stage.status, stage.task_id = WAITING, None
            stage.retry_at = self.twin.simulation_time + ORDER_RETRY_DELAY_S
            self.twin.logger.warning(LogCategory.OPERATIONS, f"{order.order_id}: {stage.name} failed ({reason}) "
                                     f"— retrying in {ORDER_RETRY_DELAY_S:.0f} s")
            return
        stage.status = FAILED
        self._fail(order, reason)

    def _fail(self, order: Order, reason: str) -> None:
        order.status, order.failure_reason = FAILED, reason
        order.completed_at = self.twin.simulation_time
        why = f"order {order.order_id} failed"
        for stage in order.stages:
            task = self.twin.tasks.get(stage.task_id) if stage.task_id else None
            if task is None or task.is_terminal or task.type is TaskType.RETURN_TOTE:
                continue  # a RETURN_TOTE under way is taking its tote home: it finishes
            self.twin.tasks.cancel_task(task.id, reason=why)
        if self.twin.equipment is not None:
            self.twin.equipment.release_order(order.order_id)
        self.twin.events.emit(
            EventType.ORDER_FAILED,
            f"{order.order_id} failed: {reason}",
            category=LogCategory.OPERATIONS,
            level=LogLevel.WARNING,
            data={"order_id": order.order_id, "kind": order.kind, "reason": reason},
        )
        if order.kind == "CUSTOMER":
            for line in order.lines:  # every tote it took out goes home
                self._send_tote_home(line.get("tote_id"), why)

    def _send_tote_home(self, box_id: Optional[str], why: str) -> None:
        """Queue a RETURN_TOTE (through the gate) for a tote an order took out,
        unless it is home already or a job has it (one taking it home, say)."""
        twin = self.twin
        tote = twin.find_box(box_id) if box_id else None
        if tote is None or tote.kind is not BoxKind.TOTE or not tote.slot or tote_is_home(twin, tote):
            return
        if twin.tasks.active_task_for_box(tote.id) is not None:
            return
        task = twin.tasks.create_task({"type": "RETURN_TOTE", "box_id": tote.id}, internal=True)
        if task.status is TaskStatus.FAILED:
            twin.logger.warning(LogCategory.OPERATIONS, f"{tote.name} could not be sent home ({why}): {task.error}",
                                task_id=task.id, box_id=tote.id)
        else:
            twin.logger.info(LogCategory.OPERATIONS, f"{tote.name} sent home as {task.id}: {why}",
                             task_id=task.id, box_id=tote.id)

    # ---- what each stage asks for ---------------------------------------- #
    def _payload(self, order: Order, stage: Stage) -> Optional[Dict[str, Any]]:
        if order.kind != "CUSTOMER":
            return {"type": stage.task_type, **stage.payload}
        if stage.task_type == "PACK_ORDER":
            carton = self._carton_of(order)  # a retry resumes the carton a failed attempt began
            packed = carton.quantity if carton is not None else 0
            return {"type": "PACK_ORDER", "order_id": order.order_id, "quantity": self._order_units(order) - packed,
                    "pack_cell": order.pack_cell, "lane": order.lane}
        line = order.lines[stage.line]
        if stage.task_type == "TOTE_TO_STATION":
            line.pop("tote_id", None)  # a retry may take the tote it chose before: its own promise doesn't block it
            tote = self._choose_tote(line["sku"], int(line["units"]))
            if tote is None:
                return None
            line["tote_id"] = tote.id
            return {"type": "TOTE_TO_STATION", "box_id": tote.id, "station": order.pick_station}
        if stage.task_type == "PICK":
            kind = "PICK_ITEMS" if order.pick_station == PICK_STATIONS[0] else "MANUAL_PICK"
            left = int(line["units"]) - int(line.get("placed", 0))  # a retry picks only what the failed try didn't
            return {"type": kind, "box_id": line["tote_id"], "quantity": left,
                    "order_id": order.order_id, "pack_cell": order.pack_cell, "station": order.pick_station}
        return {"type": "RETURN_TOTE", "box_id": line["tote_id"]}

    # ---- retrying after partial progress --------------------------------- #
    @staticmethod
    def _order_units(order: Order) -> int:
        return sum(int(line["units"]) for line in order.lines)

    @staticmethod
    def _units_placed(task: Any) -> int:
        """The units a pick put on the line: a person's job counts them as it
        goes; a robot's are its PLACE_ON_CONVEYOR actions that finished (an
        item grasped but not placed is not on the line yet)."""
        if task.type is TaskType.MANUAL_PICK:
            return int(task.params.get("picked", 0))
        return sum(1 for action in task.actions if action.type is ActionType.PLACE_ON_CONVEYOR and action.done)

    def _bank_placed_units(self, order: Order, stage: Stage) -> None:
        """Before a failed PICK is forgotten, record on its line how many
        units it did put on the line, so the retry asks for the remainder."""
        task = self.twin.tasks.get(stage.task_id) if stage.task_id else None
        if order.kind == "CUSTOMER" and stage.task_type == "PICK" and task is not None:
            line = order.lines[stage.line]
            line["placed"] = int(line.get("placed", 0)) + self._units_placed(task)

    @staticmethod
    def _pick_finished(order: Order, stage: Stage) -> bool:
        if order.kind != "CUSTOMER" or stage.task_type != "PICK":
            return False
        line = order.lines[stage.line]
        return int(line.get("placed", 0)) >= int(line["units"])

    def _carton_of(self, order: Order) -> Optional[Any]:
        return next((box for box in self.twin.boxes.values()
                     if box.kind is BoxKind.CARTON and box.order_id == order.order_id), None)

    def _cannot_resume_pack(self, order: Order, stage: Stage) -> Optional[str]:
        """Why a failed PACK_ORDER can't be taken up again (None if it can: no
        carton yet, or one in its cell with items still to pack). A carton that
        already holds every item, or has left the cell, can't be packed on
        again without double-packing, so the order fails with this reason."""
        if order.kind != "CUSTOMER" or stage.task_type != "PACK_ORDER":
            return None
        carton = self._carton_of(order)
        if carton is None:
            return None
        arm_cell = tuple(self.twin.warehouse.zones[order.pack_cell].attributes["arm_cell"])  # where it packs
        if carton.status is not BoxStatus.STORED or carton.position != arm_cell:
            return f"{carton.name} has left the pack cell, so packing can't be resumed"
        if carton.quantity >= self._order_units(order):
            return (f"all {carton.quantity} item(s) are already packed in {carton.name}, "
                    f"so packing can't be repeated")
        return None

    def _choose_tote(self, sku: str, units: int) -> Optional[Any]:
        """A tote of `sku` holding `units`, home in its slot, wanted by no job
        right now, and one a robot that can bring it is able to now: a tote
        the AMRs reach before one only the humanoid reaches (it needs its
        supervisor, and stops while he walks). None while every such tote is
        busy, or the only robots that reach it are halted or unsupervised: the
        line waits, spending no retry, as it does for a busy tote. Only when no
        robot on the floor could ever reach any of them is the first free one
        asked for all the same, so the gate rejects it with its reason."""
        twin = self.twin
        stocked = [twin.find_box(location.box_id) for location in twin.stock.locations(sku)
                   if location.recorded_qty >= units]
        stocked = [box for box in stocked if box is not None and box.kind is BoxKind.TOTE]
        if not stocked:
            raise OrderError(f"no tote holds {units} of {sku}")
        free = [box for box in stocked if tote_is_home(twin, box) and twin.tasks.active_task_for_box(box.id) is None
                and not self._promised_tote(box.id)]
        bodies = self._tote_bodies()
        ranked = [(rank, number, box) for number, box in enumerate(free)
                  for rank in (self._tote_rank(box, bodies),) if rank is not None]
        if ranked:
            return min(ranked, key=lambda entry: entry[:2])[2]
        if any(self._reaches(box, profile) for box in stocked for profile, _, _ in bodies):
            return None  # the robots that reach one are halted or unsupervised: wait for them
        return free[0] if free else None

    def _tote_bodies(self) -> List[Tuple[Any, bool, bool]]:
        """(profile, able now, needs a supervisor) for every robot on the
        floor whose body can do TOTE_TO_STATION. Able now: fit for the job
        (not halted, approved firmware, the job allowed) and, for a body that
        needs a supervisor, one is on shift."""
        classes = JOB_SPECS[TaskType.TOTE_TO_STATION].classes
        bodies = []
        for robot in self.twin.robots.values():
            profile = robot.mobility
            if profile is None or profile.embodiment_class not in classes:
                continue
            able = robot_eligibility(robot.status.value, robot.firmware_version, battery=None,
                                     task_type=TaskType.TOTE_TO_STATION.value,
                                     allowed_task_types=robot.allowed_task_types) is None \
                and people.supervision_available(self.twin, robot)[0]
            bodies.append((profile, able, bool(profile.supervision)))
        return bodies

    def _reaches(self, box: Any, profile: Any) -> bool:
        """Can this body take `box` from its slot: its kind, its declared
        weight, and the slot's level (the gate's capability rules)?"""
        slot = self.twin.warehouse.slot(box.slot)
        return (box_kind_ok(box.kind.value, profile.box_kinds)[0]
                and payload_ok(box.declared_weight_kg, profile.max_payload_kg)[0]
                and reach_ok(slot.level if slot is not None else None, profile.max_shelf_level)[0])

    def _tote_rank(self, box: Any, bodies: List[Tuple[Any, bool, bool]]) -> Optional[int]:
        """0 if a body that needs no supervisor (an AMR) able now reaches
        `box`, 1 if only a supervised one (the humanoid) does, None if no body
        able now reaches it."""
        supervised = [needs for profile, now, needs in bodies if now and self._reaches(box, profile)]
        if not supervised:
            return None
        return 1 if all(supervised) else 0

    def _promised_tote(self, box_id: str) -> bool:
        """True while a live customer line holds `box_id`, from the moment it
        is chosen until that line's RETURN_TOTE is done: a tote that has gone
        home is free again for any line, the same order's included (an order
        repeating a SKU only one tote holds would otherwise wait on itself)."""
        for order in self.orders.values():
            if order.is_terminal or order.kind != "CUSTOMER":
                continue
            returned = {stage.line for stage in order.stages if stage.task_type == "RETURN_TOTE" and stage.status == DONE}
            if any(line.get("tote_id") == box_id for number, line in enumerate(order.lines) if number not in returned):
                return True
        return False

    # ---- a customer order's station and pack cell ------------------------ #
    def _busy(self, attribute: str) -> List[str]:
        """The pick stations (or pack cells) running orders still need: a
        station until every stage of every line is done — the last line
        picked and its tote back in its slot (its RETURN_TOTE done) — and a
        pack cell until its PACK_ORDER is done (the carton is on the line)."""
        busy = []
        for order in self.orders.values():
            if order.kind != "CUSTOMER" or order.status != IN_PROGRESS:
                continue
            if attribute == "pick_station":
                needed = any(stage.status != DONE for stage in order.stages if stage.line is not None)
            else:
                needed = any(stage.status != DONE for stage in order.stages if stage.task_type == "PACK_ORDER")
            if needed:
                busy.append(getattr(order, attribute))
        return busy

    def _first_tote_free(self, order: Order) -> bool:
        """Can the order's first line have its tote now? While another line or
        job holds every tote of that SKU the order waits, claiming nothing.
        With no tote holding enough at all, the stage itself fails (retried
        once, then the order fails with the reason)."""
        line = order.lines[0]
        try:
            return self._choose_tote(line["sku"], int(line["units"])) is not None
        except OrderError:
            return True

    def _acquire(self, order: Order) -> bool:
        """Claim a free pick station and pack cell for `order` once its first
        tote can be had, or wait holding neither."""
        twin = self.twin
        arms = {twin.warehouse.fixed_stations.get(robot.position) for robot in twin.robots.values()
                if robot.mobility is not None and robot.mobility.is_fixed and not robot.is_halted}
        packs = [cell for cell in PACK_CELLS if cell in arms and cell not in self._busy("pack_cell")]
        stations = [station for station in PICK_STATIONS if station not in self._busy("pick_station")]
        picker = any(robot.mobility is not None and robot.mobility.embodiment_class == "PICKER"
                     and not robot.is_halted for robot in twin.robots.values())
        people_on = any(o.worker_id in PICK_STATION_PEOPLE and o.status != OperatorStatus.OFF_DUTY
                        and o.zone is not None for o in twin.operators.values())
        usable = [s for s in stations if (s == PICK_STATIONS[0] and picker) or (s == PICK_STATIONS[1] and people_on)]
        if not packs or not usable or not self._first_tote_free(order):
            return False
        order.pick_station, order.pack_cell = usable[0], packs[0]
        self.twin.logger.info(LogCategory.OPERATIONS, f"{order.order_id} picks at {order.pick_station} and "
                              f"packs at {order.pack_cell}", data={"order_id": order.order_id})
        return True

    # ---- reads ----------------------------------------------------------- #
    def get(self, order_id: str) -> Dict[str, Any]:
        """One order, with its stages; KeyError if there is no such order."""
        order = self.orders.get(order_id)
        if order is None:
            raise KeyError(f"Order '{order_id}' does not exist")
        return order.to_dict()

    def list(self, status: Optional[str] = None, kind: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        """The newest orders first, by order number (ORD-10000 after ORD-9999)."""
        orders = sorted(self.orders.values(), key=lambda o: order_number(o.order_id), reverse=True)
        orders = [o for o in orders if (status is None or o.status == status.upper())
                  and (kind is None or o.kind == kind.upper())]
        return [o.to_dict() for o in orders[:limit]]

    def counts(self) -> Dict[str, Dict[str, int]]:
        out: Dict[str, Dict[str, int]] = {kind: {OPEN: 0, IN_PROGRESS: 0, DONE: 0, FAILED: 0} for kind in ORDER_KINDS}
        for order in self.orders.values():
            out[order.kind][order.status] += 1
        return out
