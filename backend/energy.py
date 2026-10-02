"""Energy on the multi-embodiment floor (spec §5.5).

A robot's battery percentage is the energy left ÷ its catalog capacity. Per
tick a ground robot uses capacity_wh / (runtime_h × 3600) × TICK_DT watt-hours
× 0.3 idle, × 1.0 moving and × 1.4 moving loaded; raising a load adds
m × g × Δh / 3600 / 0.6, m being the load plus the carriage. A drone in the
air uses capacity_wh / (flight_time_min × 60) × TICK_DT. Charging adds
capacity_wh / (charge_time_h × 3600) every second — at charging_station for a
ground robot, on its pad for a drone. Everything is × ENERGY_TIME_SCALE (10 by
default, so charging shows up within a demo; 1 is true to life).

A mains-powered body (an arm: battery None) neither uses nor needs energy.
Robots on the classic floor have no profile and keep the old model: 1% per
cell, a fixed cost per pick and delivery, BATTERY_CHARGE_RATE per tick.
"""
from __future__ import annotations

from typing import Any, Optional

from .embodiment import AIR, MobilityProfile
from .models import CONFIG

IDLE_FACTOR = 0.3
MOVING_FACTOR = 1.0
LOADED_FACTOR = 1.4
GRAVITY_MPS2 = 9.81
LIFT_EFFICIENCY = 0.6
#: The carriage a body raises with its load (forks, mast, lift platform), as a
#: share of its catalog mass — the catalog gives no carriage mass of its own.
CARRIAGE_FRACTION = 0.1


def _scale() -> float:
    return float(CONFIG["ENERGY_TIME_SCALE"])


def has_battery(profile: Optional[MobilityProfile]) -> bool:
    """Does this body run on the §5.5 battery model? (No profile: classic.)"""
    return profile is not None and profile.battery is not None


def charger_zone(profile: Optional[MobilityProfile]) -> str:
    """Where this body charges: a drone on its pad, anything else at the station."""
    return "drone_pad" if profile is not None and profile.is_air else "charging_station"


def ground_wh_per_tick(profile: MobilityProfile, moving: bool, loaded: bool) -> float:
    battery = profile.battery
    base = battery.capacity_wh / (battery.runtime_h * 3600) * CONFIG["TICK_DT"] * _scale()
    if not moving:
        return base * IDLE_FACTOR
    return base * (LOADED_FACTOR if loaded else MOVING_FACTOR)


def flight_wh_per_tick(profile: MobilityProfile) -> float:
    """A drone hovering or flying, loaded or not."""
    return profile.battery.capacity_wh / (profile.flight_time_min * 60) * CONFIG["TICK_DT"] * _scale()


def tick_wh(profile: MobilityProfile, layer: str, moving: bool, loaded: bool) -> float:
    """What one tick costs this body: flight in the air, else the ground rates."""
    if not has_battery(profile):
        return 0.0
    if layer == AIR and profile.flight_time_min:
        return flight_wh_per_tick(profile)
    return ground_wh_per_tick(profile, moving, loaded)


def lift_wh(profile: MobilityProfile, load_kg: float, delta_h_m: float) -> float:
    """Raising `load_kg` (and the carriage) by `delta_h_m`; lowering is free."""
    if not has_battery(profile) or delta_h_m <= 0:
        return 0.0
    mass = float(load_kg) + CARRIAGE_FRACTION * float(profile.mass_kg or 0.0)
    return mass * GRAVITY_MPS2 * delta_h_m / 3600 / LIFT_EFFICIENCY * _scale()


def charge_wh_per_tick(profile: MobilityProfile) -> float:
    battery = profile.battery
    return battery.capacity_wh / (battery.charge_time_h * 3600) * _scale() * CONFIG["TICK_DT"]


def wh_to_pct(profile: MobilityProfile, wh: float) -> float:
    """Watt-hours as a share of this body's capacity, in percent."""
    return 100.0 * wh / profile.battery.capacity_wh


def energy_wh(robot: Any) -> float:
    """What is left in `robot`'s battery (battery % × capacity)."""
    return robot.battery / 100.0 * robot.mobility.battery.capacity_wh


def route_wh(profile: MobilityProfile, cells: int, loaded: bool = False, airborne: bool = False) -> float:
    """What driving (or flying) `cells` cells costs, for planning estimates."""
    if not has_battery(profile) or cells <= 0 or profile.speed_cells_s <= 0:
        return 0.0
    speed = profile.loaded_speed_cells_s if loaded else profile.speed_cells_s
    ticks = cells / (speed * CONFIG["TICK_DT"])
    per_tick = flight_wh_per_tick(profile) if airborne and profile.flight_time_min \
        else ground_wh_per_tick(profile, moving=True, loaded=loaded)
    return ticks * per_tick
