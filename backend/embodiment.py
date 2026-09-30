"""Embodiment: what a robot's body lets it do on the floor (multi-embodiment
spec §5).

A MobilityProfile is read from the inventory's catalog model spec, so
editing a model changes behaviour. On the distribution-centre floor every
robot carries the profile of its bound asset's model; on the classic floor
robots carry none and move exactly as they always have. The timing helpers
turn the catalog's physical durations into whole simulation ticks.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any, Dict, Mapping, Optional, Tuple

from .layouts.base import NARROW, WIDE
from .models import CONFIG

GROUND = "GROUND"
AIR = "AIR"
FIXED = "FIXED"
MOVEMENTS = (GROUND, AIR, FIXED)
#: The layers a robot can occupy. Only drones ever leave GROUND.
LAYERS = (GROUND, AIR)

#: A flying drone holds this far above the level it works at (spec §5.3).
HOVER_CLEARANCE_M = 0.5

#: Classes that travel slower while carrying (× CONFIG["LOADED_SPEED_FACTOR"]).
LOADED_SLOWDOWN_CLASSES = frozenset({"FORKLIFT", "HEAVY_HAULER"})
DEFAULT_LIFT_SPEED_MPS = 0.5


@dataclass(frozen=True)
class BatterySpec:
    chemistry: str
    capacity_wh: float
    runtime_h: float
    charge_time_h: float


def _float(value: Any) -> Optional[float]:
    return None if value is None else float(value)


@dataclass(frozen=True)
class MobilityProfile:
    embodiment_class: str
    movement: str                 # GROUND, AIR or FIXED
    clearance: Optional[str]      # WIDE or NARROW for ground robots; None otherwise
    speed_cells_s: float
    slows_when_loaded: bool
    max_payload_kg: float
    max_shelf_level: int
    box_kinds: Tuple[str, ...]
    max_lift_m: float
    reach_mm: Optional[float]
    supervision: Optional[str]    # the credential a supervisor needs (humanoid), or None
    battery: Optional[BatterySpec]  # None for mains power
    flight_time_min: Optional[float]
    lift_speed_mps: float
    takeoff_s: Optional[float]
    land_s: Optional[float]
    scan_s: Optional[float]
    grasp_s: Optional[float]
    place_s: Optional[float]

    @classmethod
    def from_model(cls, spec: Mapping[str, Any], embodiment_class: str) -> "MobilityProfile":
        """Build the profile from a catalog robot-model `spec` (spec §5.1)."""
        movement = str(spec.get("movement") or GROUND).upper()
        if movement not in MOVEMENTS:
            raise ValueError(f"Unknown movement {movement!r} (known: {list(MOVEMENTS)})")
        clearance = None
        if movement == GROUND:
            clearance = str(spec.get("clearance") or NARROW).upper()
        battery = spec.get("battery")
        return cls(
            embodiment_class=str(embodiment_class).upper(),
            movement=movement,
            clearance=clearance,
            speed_cells_s=float(spec.get("max_speed_mps") or 0.0) / CONFIG["CELL_SIZE_M"],
            slows_when_loaded=str(embodiment_class).upper() in LOADED_SLOWDOWN_CLASSES,
            max_payload_kg=float(spec.get("max_payload_kg") or 0.0),
            max_shelf_level=int(spec.get("max_shelf_level") or 0),
            box_kinds=tuple(spec.get("box_kinds") or ()),
            max_lift_m=float(spec.get("max_lift_m") or 0.0),
            reach_mm=_float(spec.get("reach_mm")),
            supervision=spec.get("supervision"),
            battery=BatterySpec(
                chemistry=str(battery["chemistry"]), capacity_wh=float(battery["capacity_wh"]),
                runtime_h=float(battery["runtime_h"]), charge_time_h=float(battery["charge_time_h"]),
            ) if battery else None,
            flight_time_min=_float(spec.get("flight_time_min")),
            lift_speed_mps=float(spec.get("lift_speed_mps") or DEFAULT_LIFT_SPEED_MPS),
            takeoff_s=_float(spec.get("takeoff_s")),
            land_s=_float(spec.get("land_s")),
            scan_s=_float(spec.get("scan_s")),
            grasp_s=_float(spec.get("grasp_s")),
            place_s=_float(spec.get("place_s")),
        )

    @property
    def is_ground(self) -> bool:
        return self.movement == GROUND

    @property
    def is_air(self) -> bool:
        return self.movement == AIR

    @property
    def is_fixed(self) -> bool:
        return self.movement == FIXED

    @property
    def loaded_speed_cells_s(self) -> float:
        factor = CONFIG["LOADED_SPEED_FACTOR"] if self.slows_when_loaded else 1.0
        return self.speed_cells_s * factor

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["box_kinds"] = list(self.box_kinds)
        return data


# --------------------------------------------------------------------------- #
# Physical durations as simulation ticks (spec §5.4)
# --------------------------------------------------------------------------- #
def seconds_to_ticks(seconds: float) -> int:
    """ceil(seconds / TICK_DT). The quotient is rounded first so float noise
    (3.0 / 0.15 = 20.000000000000004) can't add a phantom tick."""
    if seconds <= 0:
        return 0
    return int(math.ceil(round(seconds / CONFIG["TICK_DT"], 6)))


def lift_ticks(profile: MobilityProfile, from_m: float, to_m: float) -> int:
    """LIFT_TO / LOWER: the height difference ÷ lift_speed_mps."""
    return seconds_to_ticks(abs(to_m - from_m) / profile.lift_speed_mps)


#: Step name -> the profile field holding its duration in seconds.
STEP_TIMINGS = {
    "TAKEOFF": "takeoff_s", "LAND": "land_s", "SCAN": "scan_s",
    "GRASP": "grasp_s", "PLACE": "place_s", "PLACE_ON_CONVEYOR": "place_s",
}


def step_ticks(profile: MobilityProfile, step: str) -> int:
    """Ticks one TAKEOFF / LAND / SCAN (per level) / GRASP / PLACE /
    PLACE_ON_CONVEYOR step takes for this body."""
    field = STEP_TIMINGS.get(step)
    if field is None:
        raise ValueError(f"Unknown timed step {step!r} (known: {sorted(STEP_TIMINGS)})")
    seconds = getattr(profile, field)
    if seconds is None:
        raise ValueError(f"A {profile.embodiment_class} has no {step} timing in its catalog model")
    return seconds_to_ticks(seconds)
