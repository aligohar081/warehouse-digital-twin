"""Seed the inventory catalog: manufacturers, parts, robot models,
credential definitions and every software release with its SBOM."""
from __future__ import annotations

from datetime import timedelta

from .catalog import make_release_row
from .catalog_data import (
    AI_POLICY_RELEASES, COMPONENT_FIRMWARE_MIN_HW, COMPONENT_FIRMWARE_VERSIONS,
    CREDENTIAL_DEFINITIONS, EXTRA_ROBOT_SOFTWARE_RELEASES, MANUFACTURERS, PART_MODELS,
    ROBOT_MODELS, ROBOT_SOFTWARE_MIN_HW, ROBOT_SOFTWARE_RELEASES,
)
from .documents import iso


def seed_catalog(service) -> None:
    store = service.store
    now = service.now()
    suppliers = {m["manufacturer_id"]: m["name"] for m in MANUFACTURERS}
    part_supplier = {p["part_number"]: suppliers[p["manufacturer_id"]] for p in PART_MODELS}
    model_supplier = {m["model_code"]: suppliers[m["manufacturer_id"]] for m in ROBOT_MODELS}

    def released(days: int) -> str:
        return iso(now - timedelta(days=days))

    with service.transaction():
        for row in MANUFACTURERS:
            store.insert("manufacturer", row)
        for row in PART_MODELS:
            store.insert("part_model", row)
        for row in ROBOT_MODELS:
            store.insert("robot_model", row)
        for row in CREDENTIAL_DEFINITIONS:
            store.insert("credential_definition", row)
        for model in ROBOT_MODELS:
            code = model["model_code"]
            for version, days, status in ROBOT_SOFTWARE_RELEASES + EXTRA_ROBOT_SOFTWARE_RELEASES.get(code, []):
                store.insert("software_release", make_release_row(
                    "ROBOT_SOFTWARE", code, version, released(days), status,
                    ROBOT_SOFTWARE_MIN_HW.get((code, version)), model_supplier[code]))
            for version, days, status in AI_POLICY_RELEASES.get(code, []):
                store.insert("software_release", make_release_row(
                    "AI_POLICY_MODEL", code, version, released(days), status, None, model_supplier[code]))
        for part_number, (previous, current) in COMPONENT_FIRMWARE_VERSIONS.items():
            for version, days, status in ((previous, 300, "SUPERSEDED"), (current, 60, "CURRENT")):
                store.insert("software_release", make_release_row(
                    "COMPONENT_FIRMWARE", part_number, version, released(days), status,
                    COMPONENT_FIRMWARE_MIN_HW.get((part_number, version)), part_supplier[part_number]))
