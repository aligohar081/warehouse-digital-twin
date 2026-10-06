"""The classic floor's demo (multi-embodiment spec §4.2): two robots, five
boxes, the agent Ada, two operators and, if asked, two demo tasks.

DigitalTwin.load_demo's tables and body, moved here verbatim (the twin's
load_demo now calls seed), so every caller gets exactly the floor it always
got.
"""
from __future__ import annotations

from typing import Any

from ..inventory.catalog_data import CLASS_DEFAULT_MODELS
from ..models import LogCategory

#: One demo agent and two demo operators — one fully certified, one only
#: partially, so HUMAN_INSPECTION / MIXED_MAINTENANCE_MISSION have a
#: realistic mix of eligible and ineligible operators to pick from when
#: assigned "AUTO", the same way DEMO_ROBOTS/DEMO_BOXES seed a realistic
#: starting warehouse.
DEMO_AGENTS = [
    ("Ada", "groq/gpt-oss-20b"),
]

DEMO_OPERATORS = [
    ("Sam", ["safety_inspection", "electrical_safety"]),
    ("Lee", ["safety_inspection"]),
]

DEMO_ROBOTS = [
    ("Robo-01", (3, 8)),
    ("Robo-02", (16, 8)),
]

#: The inventory records (backend/inventory/demo_seed.py) the demo robots
#: and operators are bound to — separate maps so the tuples above stay
#: exactly as they were.
DEMO_ASSET_IDS = {"Robo-01": "AST-000101", "Robo-02": "AST-000102"}
DEMO_WORKER_IDS = {"Sam": "E-10001", "Lee": "E-10002"}

DEMO_BOXES = [
    ("Box-A", (3, 5), 12.5, "shelf_a", "loading_zone"),
    ("Box-B", (9, 5), 8.0, "shelf_b", "packing_area"),
    ("Box-C", (15, 5), 20.0, "shelf_c", "unloading_zone"),
    ("Box-D", (5, 5), 4.5, "shelf_a", "packing_area"),
    ("Box-E", (11, 5), 15.0, "shelf_b", "loading_zone"),
]

DEMO_TASKS = [
    {"type": "PICK_AND_DELIVER", "robot": "Robo-01", "box": "Box-A",
     "source": "shelf_a", "destination": "loading_zone", "priority": "HIGH"},
    {"type": "PICK_AND_DELIVER", "robot": "Robo-02", "box": "Box-B",
     "source": "shelf_b", "destination": "packing_area", "priority": "NORMAL"},
]


def seed(twin: Any, create_tasks: bool = True) -> None:
    """Populate an empty classic twin with the demo (and its tasks)."""
    for name, position in DEMO_ROBOTS:
        try:
            twin.add_robot(name=name, position=position, asset_id=DEMO_ASSET_IDS.get(name))
        except ValueError as exc:
            # A persisted inventory may have retired the demo's asset; the
            # twin must still boot, so take over an orphaned floor asset of
            # the same model (a previous boot's replacement) or commission
            # a fresh one — never grow the inventory on every boot.
            demo_asset = DEMO_ASSET_IDS.get(name)
            model = (twin.inventory.get_robot(demo_asset)["model_code"]
                     if twin.inventory.has_asset(demo_asset) else CLASS_DEFAULT_MODELS["AMR"])
            spare = twin.fleet.spare_floor_asset(model, exclude=DEMO_ASSET_IDS.values())
            twin.logger.warning(
                LogCategory.FLEET,
                f"{name}: demo asset {demo_asset} is unusable ({exc}) — "
                + (f"taking over spare asset {spare}" if spare else "commissioning a new one"))
            twin.add_robot(name=name, position=position, asset_id=spare)
    for name, position, weight, source, destination in DEMO_BOXES:
        twin.add_box(name=name, position=position, weight=weight,
                     source=source, destination=destination)
    for name, model_version in DEMO_AGENTS:
        twin.add_agent(name=name, model_version=model_version)
    for name, certifications in DEMO_OPERATORS:
        twin.add_operator(name=name, certifications=certifications, worker_id=DEMO_WORKER_IDS.get(name))
    if create_tasks:
        for payload in DEMO_TASKS:
            try:
                twin.tasks.create_task(payload)
            except ValueError as exc:
                twin.logger.error(LogCategory.TASK, f"Demo task rejected: {exc}")
