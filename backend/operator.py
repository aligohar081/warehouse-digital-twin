"""Human operators: the third actor class.

A person who supervises, inspects, and approves work a robot or agent
can't authorize on its own (see TaskType.HUMAN_INSPECTION and the
MIXED_MAINTENANCE_MISSION flow in task_manager.py, which has an operator
sign off once a robot's physical inspection completes). See
models.CERTIFICATION_REQUIREMENTS for which task types need which
certification, and eval_engine.check_entities_valid for how a task is
flagged if the assigned operator didn't actually hold it.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .models import OperatorStatus, now_iso


class Operator:
    def __init__(
        self,
        operator_id: str,
        name: str,
        certifications: Optional[List[str]] = None,
        shift_start_hour: Optional[int] = None,
        shift_end_hour: Optional[int] = None,
        role: Optional[str] = None,
    ) -> None:
        self.id = operator_id
        self.name = name
        self.certifications: List[str] = list(certifications or [])
        # A preset bundle applied at creation time (see
        # models.OPERATOR_ROLE_PRESETS) — purely informational after
        # that; certifications/shift can still be changed independently.
        self.role: Optional[str] = role
        # The workforce-system record this operator is (backend/fleet_bridge.py).
        # When set, `certifications` is kept in sync with that worker's valid
        # credentials — the inventory is the source of truth for qualifications.
        self.worker_id: Optional[str] = None
        self.status: OperatorStatus = OperatorStatus.AVAILABLE
        self.current_task: Optional[str] = None
        self.completed_tasks = 0
        self.failed_tasks = 0
        # Optional shift window, real (wall-clock) hours 0-23. Either both
        # set or neither — None/None means "always available", manual
        # status control only, the only behaviour that existed before
        # this was added. See Simulator._check_operator_shifts, which
        # only ever moves AVAILABLE<->OFF_DUTY automatically, never
        # touches ON_TASK, and never overrides a manual change within the
        # same shift state.
        self.shift_start_hour: Optional[int] = shift_start_hour
        self.shift_end_hour: Optional[int] = shift_end_hour
        # Where the person is on the floor (backend/people.py): a zone key, or
        # None when off the floor. While walking to `transit_to` (until
        # `transit_until_tick`) `zone` still names the zone they left and they
        # are in transit between the two; only a walk that crosses the
        # pedestrian walkway strip counts as "on the walkway" (people.on_walkway).
        self.zone: Optional[str] = None
        self.transit_to: Optional[str] = None
        self.transit_until_tick: Optional[int] = None
        # {code: {"equipment": [...], "site": [...]}} for each valid credential,
        # synced from the workforce record with `certifications`. An empty list
        # means that credential is unrestricted in that dimension.
        self.certification_scopes: Dict[str, Dict[str, List[str]]] = {}
        self.created_at = now_iso()
        self.updated_at = now_iso()

    # ------------------------------------------------------------------ #
    def touch(self) -> None:
        self.updated_at = now_iso()

    def set_status(self, status: OperatorStatus) -> OperatorStatus:
        previous = self.status
        self.status = status
        if status == OperatorStatus.OFF_DUTY:  # an off-duty person is not on the floor
            self.zone = self.transit_to = self.transit_until_tick = None
        self.touch()
        return previous

    @property
    def in_transit(self) -> bool:
        return self.transit_to is not None

    @property
    def is_available(self) -> bool:
        return self.status == OperatorStatus.AVAILABLE and self.current_task is None

    @property
    def has_shift(self) -> bool:
        return self.shift_start_hour is not None and self.shift_end_hour is not None

    def is_within_shift(self, hour: int) -> bool:
        """Is `hour` (0-23) inside this operator's configured shift window?
        Always True if no shift is configured. Handles a shift that wraps
        past midnight (e.g. 22 -> 6)."""
        if not self.has_shift:
            return True
        start, end = self.shift_start_hour, self.shift_end_hour
        if start == end:
            return True  # a zero-width window means "always on shift"
        if start < end:
            return start <= hour < end
        return hour >= start or hour < end  # wraps past midnight

    # ------------------------------------------------------------------ #
    # Serialisation
    # ------------------------------------------------------------------ #
    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "certifications": list(self.certifications),
            "role": self.role,
            "worker_id": self.worker_id,
            "status": self.status.value,
            "current_task": self.current_task,
            "completed_tasks": self.completed_tasks,
            "failed_tasks": self.failed_tasks,
            "shift_start_hour": self.shift_start_hour,
            "shift_end_hour": self.shift_end_hour,
            "zone": self.zone,
            "transit_to": self.transit_to,
            "transit_until_tick": self.transit_until_tick,
            "certification_scopes": {code: {key: list(values) for key, values in scope.items()}
                                     for code, scope in self.certification_scopes.items()},
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "Operator":
        operator = Operator(
            operator_id=data["id"],
            name=data["name"],
            certifications=data.get("certifications"),
            shift_start_hour=data.get("shift_start_hour"),
            shift_end_hour=data.get("shift_end_hour"),
            role=data.get("role"),
        )
        operator.status = OperatorStatus(data.get("status", "AVAILABLE"))
        operator.current_task = data.get("current_task")
        operator.completed_tasks = data.get("completed_tasks", 0)
        operator.failed_tasks = data.get("failed_tasks", 0)
        operator.created_at = data.get("created_at", operator.created_at)
        operator.updated_at = data.get("updated_at", operator.updated_at)
        operator.worker_id = data.get("worker_id")
        operator.zone = data.get("zone")
        operator.transit_to = data.get("transit_to")
        operator.transit_until_tick = data.get("transit_until_tick")
        operator.certification_scopes = dict(data.get("certification_scopes") or {})
        return operator
