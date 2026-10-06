"""The distribution-centre floor's seed (multi-embodiment spec §4.2).

Everything comes from the inventory's distribution_center profile and the
floor's own layout, through the twin's public API:

- one robot per asset at WH-01 that isn't decommissioned (15), named after
  its model family and asset number (TR50-101, PF1200-205, …), bound to the
  asset and placed on the first free cell of its home zone that its body may
  stop on and no job needs (a route zone's cells in route order: the scout's
  patrol_loop) — or, for a working aisle every cell of which a job needs, the
  nearest such cell to it. The picker starts on its station's work cell,
  where its own job wants it, and an arm on its home zone's fixed station
  (DigitalTwin._spawn_cell does that);
- one operator per worker (10), bound by worker id, on a shift window that
  starts with the shift clock (06:00), and standing in their role's home
  zone (operations/activities.HOME_ZONES) — unless their HR state keeps them
  off duty (Sasha is ON_LEAVE, Morgan TERMINATED);
- 60 pallets on the racks at levels 0–4 (150–900 kg) and 80 totes on the
  shelves at levels 0–2 (5–25 kg, one SKU each) over 40 SKUs. Every SKU has
  a tote on level 0 or 1, which the AMRs reach, holding more units than a
  customer-order line asks for (1–3), so the order generator can use every
  SKU;
- the agent Ada.

There are no demo tasks: the shift engine makes the work. The seed is
deterministic: the goods' weights and unit counts come from its own
random.Random(GOODS_SEED).
"""
from __future__ import annotations

import random
from typing import Any, Dict, Optional

from .. import people
from ..fleet_bridge import FLOOR_SITE
from ..models import CONFIG, Cell
from ..operations.activities import HOME_ZONES, ROLES, sync_duty
from ..operations.shift import DEFAULT_SKUS
from .classic import DEMO_AGENTS

GOODS_SEED = 42
SKUS = DEFAULT_SKUS                 # SKU-001 … SKU-040, as the shift engine's streams use
PALLETS, TOTES = 60, 80
#: Totes on the top shelf level (1.2 m), which only the humanoid reaches; the
#: rest sit on levels 0 and 1, which the AMRs reach too.
HIGH_TOTES = 8
PALLET_KG, PALLET_UNITS = (150.0, 900.0), (20, 60)
TOTE_KG, TOTE_UNITS = (5.0, 25.0), (12, 30)
#: Each operator's shift window: SHIFT_HOURS from the shift clock's start.
SHIFT_HOURS = 8


def seed(twin: Any) -> None:
    """Populate an empty distribution-centre twin."""
    _robots(twin)
    _operators(twin)
    _goods(twin)
    for name, model_version in DEMO_AGENTS:
        twin.add_agent(name=name, model_version=model_version)


def robot_name(asset: Dict[str, Any]) -> str:
    """TR50-101 for asset tag AT-000101 of model AC-TR50: the model's family
    (its code without the maker's prefix) and the tag's number."""
    family = asset["model_code"].split("-", 1)[-1]
    number = asset["asset_tag"].rsplit("-", 1)[-1].lstrip("0") or "0"
    return f"{family}-{number}"


def home_cell(twin: Any, asset: Dict[str, Any]) -> Optional[Cell]:
    """Where the asset's robot starts: the first free cell of its home zone
    that its body may stop on and no job needs (Warehouse.cells_jobs_need),
    else the nearest such cell to the zone. A robot the trust layer won't
    move — AST-000202 runs a recalled release, so even a move to parking is
    refused — would otherwise stand for good on a slot face a tote job is
    sent to. The picker starts on its station's work cell. None for an arm (it
    takes its home station) and for a home zone that isn't on this floor
    (add_robot then picks its default spot)."""
    warehouse = twin.warehouse
    zone = warehouse.zones.get(asset["home_zone"] or "")
    body = twin.fleet.model_profile(asset["model_code"])
    if zone is None or body.is_fixed:
        return None
    taken = set(twin.robot_cells())
    work = tuple(zone.attributes.get("work_cell") or ())
    if body.embodiment_class == "PICKER" and work and work not in taken:
        return work
    blocked = taken | warehouse.cells_jobs_need()
    free = next((cell for cell in zone.cells if cell not in blocked and warehouse.may_stop(cell, body)), None)
    return free or warehouse.nearest_walkable(zone.cells[0], blocked, profile=body)


def _robots(twin: Any) -> None:
    for asset in twin.inventory.list_robots(site=FLOOR_SITE):
        if asset["lifecycle_status"] == "DECOMMISSIONED":
            continue
        twin.add_robot(name=robot_name(asset), asset_id=asset["asset_id"],
                       robot_class=asset["embodiment_class"], position=home_cell(twin, asset))


def _operators(twin: Any) -> None:
    start = int(CONFIG["SHIFT_START_HOUR"])
    for worker in twin.inventory.list_workers(site=FLOOR_SITE):
        operator = twin.add_operator(name=worker["display_name"], worker_id=worker["worker_id"],
                                     shift_start_hour=start, shift_end_hour=(start + SHIFT_HOURS) % 24)
        role = ROLES.get(worker["worker_id"])
        if role is not None and operator.employment_status == "ACTIVE":
            people.place(twin, operator, HOME_ZONES[role])
    sync_duty(twin)  # ON_LEAVE and TERMINATED are off duty from the start


def _goods(twin: Any) -> None:
    rng = random.Random(GOODS_SEED)
    slots = list(twin.warehouse.slots.values())
    # Every third pallet slot by id: ids run level by level within a rack
    # cell, and 3 and 5 share no factor, so the 60 spread over all 36 rack
    # cells and all five levels.
    pallet_slots = sorted(slot.slot_id for slot in slots if slot.kind == "PALLET")[::3][:PALLETS]
    for number, slot_id in enumerate(pallet_slots):
        twin.add_box(name=f"PAL-{number + 1:03d}", kind="PALLET", slot=slot_id, sku=SKUS[number % len(SKUS)],
                     quantity=rng.randint(*PALLET_UNITS), weight=round(rng.uniform(*PALLET_KG), 1))
    # Levels 0 and 1 hold two laps of the SKUs in slot-id order, so each SKU
    # has a tote an AMR reaches; the top level of the two east columns holds a
    # second tote of the SKUs the first lap ended on.
    low = sorted(slot.slot_id for slot in slots if slot.kind == "TOTE" and slot.level <= 1)[:TOTES - HIGH_TOTES]
    top = sorted((slot for slot in slots if slot.kind == "TOTE" and slot.level == 2),
                 key=lambda slot: (-slot.cell[0], slot.cell[1]))[:HIGH_TOTES]
    skus = [SKUS[number % len(SKUS)] for number in range(len(low))] + list(SKUS[len(SKUS) - HIGH_TOTES:])
    for number, (slot_id, sku) in enumerate(zip(low + [slot.slot_id for slot in top], skus)):
        twin.add_box(name=f"TOTE-{number + 1:03d}", kind="TOTE", slot=slot_id, sku=sku,
                     quantity=rng.randint(*TOTE_UNITS), weight=round(rng.uniform(*TOTE_KG), 1))
