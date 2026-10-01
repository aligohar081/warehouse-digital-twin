"""Shared eligibility rules for the three actor classes — robot, AI agent,
human operator — against their toy "assurance passport" baselines (see
TRUST_LAYER.md).

This is the single place the rule lives. Two very different callers need
the exact same rule and must never be allowed to drift apart:

    backend/task_manager.py::validate()          — the PRE-execution gate.
                                                     Blocks task creation
                                                     outright (HTTP 422)
                                                     before a robot/agent/
                                                     operator ever touches
                                                     the work.
    backend/eval_engine.py::check_entities_valid  — the POST-execution
                                                     grade. Reads the
                                                     "before" state
                                                     snapshot already
                                                     attached to a
                                                     finished task's log
                                                     and grades it FAIL/
                                                     WARN/PASS.

Each function takes plain primitives (not the live Robot/Agent/Operator
objects, and not the JSON snapshot dicts) so both callers can feed it
whatever shape they already have on hand. A return value of ``None`` means
eligible; any other value is a plain-language reason it isn't.

The physical rules of the multi-embodiment floor (spec §10.1), at the bottom,
return ``(ok, reason)`` instead: the gate, robot selection, the mid-task
re-check and the evaluation all call them with plain numbers and lists.
"""
from __future__ import annotations

from typing import Mapping, Optional, Sequence, Tuple

try:  # pragma: no cover - defensive fallback only, mirrors eval_engine.py
    from .models import APPROVED_AGENT_MODELS, APPROVED_FIRMWARE_VERSIONS, CONFIG
except Exception:  # pragma: no cover
    APPROVED_FIRMWARE_VERSIONS = ["2.1.0", "2.1.1", "2.2.0"]
    APPROVED_AGENT_MODELS = ["groq/gpt-oss-20b", "groq/gpt-oss-120b"]
    CONFIG = {"BATTERY_CRITICAL": 8}


def robot_eligibility(
    status: str,
    firmware_version: Optional[str],
    battery: Optional[float],
    battery_critical: Optional[float] = None,
    task_type: Optional[str] = None,
    allowed_task_types: Optional[Sequence[str]] = None,
) -> Optional[str]:
    """None if the robot may be trusted with a task; else why not.

    Covers ERROR/STOPPED status, unapproved firmware, an out-of-config
    task type, and (only if a ``battery`` value is actually passed) a
    critically-low battery.

    task_manager.py's pre-execution gate deliberately calls this with
    ``battery=None`` — a critical battery already has a real recovery
    path (TaskPlanner prepends a recharge detour), so it must not be
    treated as a hard "never trust this robot" disqualifier the way bad
    firmware or an ERROR status are. eval_engine.check_entities_valid,
    grading a task after it's finished, does pass the real battery value:
    a robot that was still critical once the task actually *started*
    (i.e. the detour didn't happen, or wasn't enough) is worth flagging
    even though it wasn't worth refusing up front.

    ``allowed_task_types`` is a robot's own configured capability list
    (``Robot.allowed_task_types`` — see backend/robot.py). ``None`` or an
    empty sequence means "unrestricted" (the default for every robot
    unless explicitly configured), matching how every robot behaved
    before this existed — so old callers that never pass ``task_type``/
    ``allowed_task_types`` at all keep working unchanged.
    """
    if status in ("ERROR", "STOPPED"):
        return f"is in {status} status"
    if firmware_version and firmware_version not in APPROVED_FIRMWARE_VERSIONS:
        return (
            f"is running unapproved firmware {firmware_version!r} "
            f"(approved: {APPROVED_FIRMWARE_VERSIONS})"
        )
    if task_type and allowed_task_types:
        allowed = list(allowed_task_types)
        if task_type not in allowed:
            return f"is not configured to run {task_type} tasks (allowed: {allowed})"
    threshold = CONFIG.get("BATTERY_CRITICAL", 8) if battery_critical is None else battery_critical
    if isinstance(battery, (int, float)) and battery <= threshold:
        return f"has a critical battery ({battery}%)"
    return None


def agent_eligibility(status: str, model_version: Optional[str]) -> Optional[str]:
    """None if the AI agent may be trusted with a task; else why not."""
    if status == "ERROR":
        return "is in ERROR status"
    if model_version and model_version not in APPROVED_AGENT_MODELS:
        return (
            f"is running an unapproved model {model_version!r} "
            f"(approved: {APPROVED_AGENT_MODELS})"
        )
    return None


def operator_eligibility(
    status: str,
    certifications: Optional[Sequence[str]],
    required_certification: Optional[str],
) -> Optional[str]:
    """None if the human operator may be trusted with a task; else why not."""
    if status == "OFF_DUTY":
        return "is off duty"
    certs = certifications or []
    if required_certification and required_certification not in certs:
        return (
            f"does not hold the required {required_certification!r} "
            f"certification (has: {list(certs)})"
        )
    return None


# --------------------------------------------------------------------------- #
# Physical rules (multi-embodiment spec §10.1)
#
# Each returns (True, None) when the rule holds, else (False, reason). A
# missing limit (None) means the body declares none, so the rule holds.
# --------------------------------------------------------------------------- #
Rule = Tuple[bool, Optional[str]]

#: Aisle clearance classes (backend/layouts/base.py), spelled out so this
#: module stays importable on its own, like eval_engine.py.
WIDE, NARROW = "WIDE", "NARROW"

#: A drone must land with this share of its capacity still in the battery.
DRONE_RESERVE_FRACTION = 0.25


def payload_ok(weight_kg: Optional[float], max_payload_kg: Optional[float]) -> Rule:
    """The load is within the body's payload limit."""
    if weight_kg is None or max_payload_kg is None or float(weight_kg) <= float(max_payload_kg):
        return True, None
    return False, f"a {float(weight_kg):g} kg load is over its {float(max_payload_kg):g} kg payload limit"


def box_kind_ok(kind: Optional[str], box_kinds: Optional[Sequence[str]]) -> Rule:
    """The body can handle this kind of box (PALLET, TOTE, ITEM, CARTON)."""
    kinds = list(box_kinds or [])
    if kind is None or kind in kinds:
        return True, None
    return False, f"cannot handle a {kind} (handles: {', '.join(kinds) or 'nothing'})"


def reach_ok(level: Optional[int], max_shelf_level: Optional[int]) -> Rule:
    """The slot level is within the body's reach."""
    if level is None or max_shelf_level is None or int(level) <= int(max_shelf_level):
        return True, None
    return False, f"level {int(level)} is out of its reach (highest level {int(max_shelf_level)})"


def clearance_ok(route_cells_clearance: Sequence[Optional[str]], robot_clearance: Optional[str]) -> Rule:
    """A wide robot only uses WIDE cells (the two wide walkway crossings are
    WIDE). A narrow robot, a drone and fixed equipment always pass. Cells with
    no clearance class (None — not walkable) are ignored."""
    if robot_clearance != WIDE:
        return True, None
    narrow = sum(1 for clearance in route_cells_clearance if clearance == NARROW)
    if not narrow:
        return True, None
    return False, f"a wide robot's route uses {narrow} narrow cell(s)"


def no_fly_ok(route_cells_no_fly: Sequence[bool]) -> Rule:
    """An air route crosses no no-fly cell."""
    crossed = sum(1 for no_fly in route_cells_no_fly if no_fly)
    if not crossed:
        return True, None
    return False, f"an air route crosses {crossed} no-fly cell(s)"


def drone_round_trip_ok(battery_wh: float, est_wh: float, capacity_wh: float,
                        reserve_fraction: float = DRONE_RESERVE_FRACTION) -> Rule:
    """A drone flies only if its battery covers the whole round trip plus a
    reserve of `reserve_fraction` × capacity. A hard gate: unlike a ground
    robot, a drone can't detour to a charger mid-flight."""
    needed = float(est_wh) + reserve_fraction * float(capacity_wh)
    if float(battery_wh) >= needed:
        return True, None
    return False, (
        f"has {float(battery_wh):.1f} Wh but the round trip needs {float(est_wh):.1f} Wh "
        f"plus a {reserve_fraction:.0%} reserve ({needed:.1f} Wh)"
    )


def supervision_ok(required: Optional[str], supervisor_on_shift: bool, credential_valid: bool,
                   in_scope: bool) -> Rule:
    """A supervised body (the humanoid) may work only if a supervisor with a
    valid, in-scope `required` credential is on shift. Proximity is not part
    of this rule: it is a runtime wait (SUPERVISOR_ABSENT, spec §6)."""
    if not required:
        return True, None
    if not credential_valid:
        return False, f"no operator holds a valid {required!r} credential"
    if not supervisor_on_shift:
        return False, f"no operator with a {required!r} credential is on shift"
    if not in_scope:
        return False, f"no on-shift supervisor's {required!r} credential covers this robot and site"
    return True, None


def cert_scope_ok(scopes: Optional[Mapping[str, Mapping[str, Sequence[str]]]], code: str,
                  model_code: Optional[str] = None, site: Optional[str] = None) -> Rule:
    """The operator's valid `code` credential covers the equipment model and
    site involved. `scopes` is Operator.certification_scopes: an empty list
    means unrestricted in that dimension; None for model or site skips it."""
    scope = (scopes or {}).get(code)
    if scope is None:
        return False, f"does not hold a valid {code!r} credential"
    equipment = list(scope.get("equipment") or [])
    if model_code and equipment and model_code not in equipment:
        return False, f"has a {code!r} credential that does not cover {model_code} (covers: {', '.join(equipment)})"
    sites = list(scope.get("site") or [])
    if site and sites and site not in sites:
        return False, f"has a {code!r} credential that does not cover site {site} (covers: {', '.join(sites)})"
    return True, None
