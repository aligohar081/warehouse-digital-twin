"""Catalog: seeded OEM reference data, SBOMs, release management, bootstrap."""
import logging
import os
import sqlite3
from datetime import datetime, timezone

import pytest

from backend.inventory import Conflict, NotFound, open_inventory, reseed
from backend.inventory.bootstrap import SEED_VERSION
from backend.inventory.catalog_data import CLASS_DEFAULT_MODELS, ROBOT_MODELS
from backend.inventory.sbom import build_sbom, serialize_sbom
from backend.models import APPROVED_FIRMWARE_VERSIONS, ROBOT_CLASS_PRESETS

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def inv():
    return open_inventory(":memory:", demo=False, clock=lambda: NOW)


def test_catalog_is_seeded_and_consistent(inv):
    models = {m["model_code"]: m for m in inv.list_models()}
    assert set(models) == {m["model_code"] for m in ROBOT_MODELS}
    parts = {p["part_number"] for p in inv.list_parts()}
    for model in models.values():
        assert model["manufacturer_name"]
        for slot in model["component_layout"]:
            assert slot["part_number"] in parts
    for robot_class in ROBOT_CLASS_PRESETS:
        assert models[CLASS_DEFAULT_MODELS[robot_class]]["embodiment_class"] == robot_class
    assert inv.store.count("worker") == 0 and inv.store.count("robot_asset") == 0
    assert len(inv.list_manufacturers()) == 7


def test_every_model_has_one_current_software_release_covering_the_approved_versions(inv):
    for model in inv.list_models():
        releases = inv.list_releases(target_code=model["model_code"], kind="ROBOT_SOFTWARE")
        assert [r["status"] for r in releases].count("CURRENT") == 1
        assert set(APPROVED_FIRMWARE_VERSIONS) <= {r["version"] for r in releases}


def test_firmware_capable_parts_have_a_current_release(inv):
    for part in inv.list_parts():
        current = inv.current_release("COMPONENT_FIRMWARE", part["part_number"])
        assert (current is not None) == bool(part["firmware_capable"]), part["part_number"]


def test_release_lookup_helpers(inv):
    assert inv.current_release("ROBOT_SOFTWARE", "AC-TR50")["version"] == "2.2.0"
    assert inv.release_by_version("ROBOT_SOFTWARE", "AC-TR50", "2.2.1")["status"] == "RECALLED"
    assert inv.release_by_version("ROBOT_SOFTWARE", "AC-TR50", "9.9.9") is None
    assert inv.best_firmware("WT-L360", "A")["version"] == "1.9.4"  # 2.0.1 needs rev B
    assert inv.best_firmware("WT-L360", "C")["version"] == "2.0.1"
    assert inv.os_version_for("2.2.0") == "linux-rt 6.6.21"
    assert inv.os_version_for("0.9.0-beta") is None
    assert inv.model_class("NW-PF1200") == "FORKLIFT"
    assert inv.has_model("NW-PF1200") and not inv.has_model("NOPE")
    with pytest.raises(NotFound):
        inv.get_model("NOPE")
    with pytest.raises(NotFound):
        inv.get_release("NOPE")


def test_sboms_are_deterministic_cyclonedx(inv):
    release = inv.get_release("AC-TR50:SW:2.2.0")
    assert "sbom_json" not in release
    sbom = inv.get_sbom("AC-TR50:SW:2.2.0")
    assert sbom["bomFormat"] == "CycloneDX" and sbom["specVersion"] == "1.5"
    assert {"openssl", "linux-rt-kernel", "acme-robotics-nav-stack"} <= {c["name"] for c in sbom["components"]}
    rebuilt = build_sbom(
        {k: release[k] for k in ("release_id", "kind", "target_code", "version", "released_at")},
        "Acme Robotics",
    )
    assert serialize_sbom(rebuilt)[1] == release["sbom_sha256"]
    assert inv.get_sbom("FB-CX10:AI:grasp-3.2.0")["metadata"]["component"]["type"] == "machine-learning-model"
    assert inv.get_sbom("WT-L360:FW:2.0.1")["metadata"]["component"]["type"] == "firmware"


def test_publish_release_supersedes_the_previous_current_and_logs_catalog_changes(inv):
    change = inv.publish_release("ROBOT_SOFTWARE", "AC-TR50", "2.3.0")
    assert change["aggregate_type"] == "CATALOG" and change["action"] == "RELEASE_PUBLISHED"
    assert change["subject_id"] == "AC-TR50:SW:2.3.0"
    assert inv.current_release("ROBOT_SOFTWARE", "AC-TR50")["version"] == "2.3.0"
    assert inv.get_release("AC-TR50:SW:2.2.0")["status"] == "SUPERSEDED"
    assert [c["action"] for c in inv.changes("fleet")["items"]] == ["RELEASE_SUPERSEDED", "RELEASE_PUBLISHED"]
    with pytest.raises(Conflict):
        inv.publish_release("ROBOT_SOFTWARE", "AC-TR50", "2.3.0")
    with pytest.raises(ValueError):
        inv.publish_release("COMPONENT_FIRMWARE", "WT-I9", "1.0.0")  # not firmware-capable
    with pytest.raises(ValueError):
        inv.publish_release("ROBOT_SOFTWARE", "AC-TR50", "2.4.0", min_hw_rev="Z")
    with pytest.raises(ValueError):
        inv.publish_release("FIRMWARE", "AC-TR50", "2.4.0")
    with pytest.raises(NotFound):
        inv.publish_release("ROBOT_SOFTWARE", "NOPE", "1.0.0")


def test_recalling_the_current_release_promotes_the_newest_superseded_one(inv):
    change = inv.recall_release("AC-TR50:SW:2.2.0", "Watchdog reset loop")
    assert change["action"] == "RELEASE_RECALLED" and change["reason"] == "Watchdog reset loop"
    assert inv.current_release("ROBOT_SOFTWARE", "AC-TR50")["version"] == "2.1.1"
    assert [c["action"] for c in inv.changes("fleet")["items"]] == ["RELEASE_RECALLED", "RELEASE_PROMOTED"]
    with pytest.raises(Conflict):
        inv.recall_release("AC-TR50:SW:2.2.0", "again")
    with pytest.raises(ValueError):
        inv.recall_release("AC-TR50:SW:2.1.0", "  ")


def test_open_inventory_reuses_a_seeded_file_and_reseed_rotates_the_epoch(tmp_path):
    path = str(tmp_path / "inv.sqlite3")
    first = open_inventory(path, demo=False)
    first.publish_release("ROBOT_SOFTWARE", "AC-TR50", "2.3.0")
    epoch = first.store.epoch()
    first.store.conn.close()
    again = open_inventory(path, demo=False)
    assert again.store.epoch() == epoch
    assert again.current_release("ROBOT_SOFTWARE", "AC-TR50")["version"] == "2.3.0"
    reseed(again, demo=False)
    assert again.store.epoch() != epoch
    assert again.current_release("ROBOT_SOFTWARE", "AC-TR50")["version"] == "2.2.0"


def _set_meta(path, key, value):
    conn = sqlite3.connect(path)
    conn.execute("UPDATE meta SET value = ? WHERE key = ?", (value, key))
    conn.commit()
    conn.close()


@pytest.mark.parametrize("key, reason", [("seed_version", "seed"), ("schema_version", "schema")])
def test_a_file_seeded_under_an_older_version_is_reseeded_and_the_old_file_kept(tmp_path, caplog, key, reason):
    path = str(tmp_path / "inv.sqlite3")
    first = open_inventory(path, demo=False)
    assert first.store.get_meta("seed_version") == SEED_VERSION
    assert not os.path.exists(path + ".bak")  # a brand-new file has nothing to keep
    first.publish_release("ROBOT_SOFTWARE", "AC-TR50", "2.3.0")
    first.store.conn.close()
    _set_meta(path, key, "old")
    with caplog.at_level(logging.WARNING, logger="backend.inventory"):
        again = open_inventory(path, demo=False)
    assert again.current_release("ROBOT_SOFTWARE", "AC-TR50")["version"] == "2.2.0"  # the marker change is gone
    assert again.store.get_meta("seed_version") == SEED_VERSION
    assert again.store.get_meta("schema_version") != "old"
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1 and path in warnings[0] and reason in warnings[0] and path + ".bak" in warnings[0]
    backup = sqlite3.connect(path + ".bak")  # the previous file, marker change included
    assert backup.execute("SELECT COUNT(*) FROM software_release WHERE release_id = 'AC-TR50:SW:2.3.0'").fetchone()[0] == 1
    assert backup.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()[0] == "old"
    backup.close()
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="backend.inventory"):
        open_inventory(path, demo=False)  # now current: reused silently
    assert not caplog.records


def test_in_memory_inventories_are_independent():
    a, b = open_inventory(demo=False), open_inventory(demo=False)
    a.publish_release("ROBOT_SOFTWARE", "AC-TR50", "2.3.0")
    assert b.current_release("ROBOT_SOFTWARE", "AC-TR50")["version"] == "2.2.0"
    assert a.store.epoch() != b.store.epoch()
