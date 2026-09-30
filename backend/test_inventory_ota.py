"""OTA updates: staging, the timed states, verification, rollback."""
from datetime import datetime, timezone

import pytest

from backend.inventory import Conflict, NotFound, open_inventory

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def inv():
    return open_inventory(":memory:", demo=False, clock=lambda: NOW)


def commission(inv, model="AC-TR50", **kwargs):
    return inv.commission_robot(model, "WH-01", "parking_area", **kwargs)["aggregate_id"]


def test_ota_happy_path_keeps_declared_until_verified(inv):
    asset = commission(inv, software_release_id="AC-TR50:SW:2.1.1")
    staged = inv.start_ota(asset, "AC-TR50:SW:2.2.0", actor={"type": "WORKER", "id": "E-9"})
    job = staged["subject_id"]
    assert staged["action"] == "OTA_STAGED"
    view = inv.get_ota_job(job)
    assert view["from_version"] == "2.1.1" and view["created_by"] == "E-9" and view["version"] == "2.2.0"
    assert [j["job_id"] for j in inv.active_ota_jobs()] == [job]
    inv.transition_ota(job, "DOWNLOADING")
    inv.transition_ota(job, "INSTALLING")
    inv.report_state(asset, {"software_version": "2.2.0"})
    assert inv.transition_ota(job, "REPORTED")["action"] == "OTA_REPORTED"
    assert inv.active_ota_jobs() == []  # REPORTED waits for a human
    record = inv.get_robot(asset)
    assert record["flags"]["software_mismatch"] is True  # landed but not yet verified
    assert record["flags"]["active_ota_job"]["state"] == "REPORTED"
    verified = inv.verify_ota(job)
    assert verified["diff"]["software_release_id"] == {"from": "AC-TR50:SW:2.1.1", "to": "AC-TR50:SW:2.2.0"}
    record = inv.get_robot(asset)
    assert record["declared_software_version"] == "2.2.0"
    assert record["flags"]["software_mismatch"] is False and record["flags"]["active_ota_job"] is None


def test_ota_rejections(inv):
    asset = commission(inv)  # already on 2.2.0
    with pytest.raises(Conflict):
        inv.start_ota(asset, "AC-TR50:SW:2.2.0")  # already running it
    with pytest.raises(Conflict):
        inv.start_ota(asset, "AC-TR50:SW:2.2.1")  # recalled
    with pytest.raises(ValueError):
        inv.start_ota(asset, "NW-PF1200:SW:2.1.1")  # another model's release
    with pytest.raises(NotFound):
        inv.start_ota(asset, "AC-TR50:SW:9.9.9")
    forklift = commission(inv, model="NW-PF1200", hw_revision="A", software_release_id="NW-PF1200:SW:2.1.1")
    with pytest.raises(Conflict, match="hardware revision B"):
        inv.start_ota(forklift, "NW-PF1200:SW:2.2.0")
    inv.start_ota(asset, "AC-TR50:SW:2.1.1")  # downgrades are allowed
    with pytest.raises(Conflict):
        inv.start_ota(asset, "AC-TR50:SW:2.1.0")  # one job per target at a time
    inv.set_lifecycle_status(forklift, "OUT_OF_SERVICE", "x")
    with pytest.raises(Conflict):
        inv.start_ota(forklift, "NW-PF1200:SW:2.1.0")


def test_component_firmware_ota_targets_one_component(inv):
    asset = commission(inv)
    lidar = next(c for c in inv.get_robot(asset)["components"] if c["slot"] == "lidar")
    with pytest.raises(ValueError):
        inv.start_ota(asset, "WT-L360:FW:1.9.4")  # component_id required
    with pytest.raises(ValueError):
        inv.start_ota(asset, "AC-TR50:SW:2.1.1", component_id=lidar["component_id"])
    job = inv.start_ota(asset, "WT-L360:FW:1.9.4", component_id=lidar["component_id"])["subject_id"]
    inv.start_ota(asset, "AC-TR50:SW:2.1.1")  # robot software is a different target
    view = inv.get_ota_job(job)
    assert view["slot"] == "lidar" and view["kind"] == "COMPONENT_FIRMWARE" and view["from_version"] == "2.0.1"
    for state in ("DOWNLOADING", "INSTALLING", "REPORTED"):
        inv.transition_ota(job, state)
    inv.verify_ota(job)
    lidar = next(c for c in inv.get_robot(asset)["components"] if c["slot"] == "lidar")
    assert lidar["declared_firmware_version"] == "1.9.4"


def test_ota_state_machine_rejects_illegal_moves(inv):
    asset = commission(inv, software_release_id="AC-TR50:SW:2.1.1")
    job = inv.start_ota(asset, "AC-TR50:SW:2.2.0")["subject_id"]
    with pytest.raises(Conflict):
        inv.transition_ota(job, "REPORTED")  # skipping DOWNLOADING/INSTALLING
    with pytest.raises(Conflict):
        inv.verify_ota(job)
    with pytest.raises(Conflict):
        inv.transition_ota(job, "VERIFIED")  # only verify_ota verifies
    inv.transition_ota(job, "DOWNLOADING")
    failed = inv.transition_ota(job, "FAILED", failure_reason="checksum mismatch")
    assert failed["action"] == "OTA_FAILED" and failed["reason"] == "checksum mismatch"
    with pytest.raises(Conflict):
        inv.transition_ota(job, "INSTALLING")
    assert inv.get_robot(asset)["flags"]["active_ota_job"] is None
    inv.start_ota(asset, "AC-TR50:SW:2.2.0")  # a failed job frees the target


def test_rollback_records_a_reason_and_decommission_fails_active_jobs(inv):
    asset = commission(inv, software_release_id="AC-TR50:SW:2.1.1")
    job = inv.start_ota(asset, "AC-TR50:SW:2.2.0")["subject_id"]
    for state in ("DOWNLOADING", "INSTALLING", "REPORTED"):
        inv.transition_ota(job, state)
    with pytest.raises(ValueError):
        inv.rollback_ota(job, "")
    rolled = inv.rollback_ota(job, "Localisation regression on site")
    assert rolled["action"] == "OTA_ROLLED_BACK" and rolled["reason"] == "Localisation regression on site"
    assert inv.get_ota_job(job)["state"] == "ROLLED_BACK"
    assert inv.get_robot(asset)["declared_software_version"] == "2.1.1"
    second = inv.start_ota(asset, "AC-TR50:SW:2.2.0")["subject_id"]
    inv.decommission(asset, "Scrapped")
    assert inv.get_ota_job(second)["state"] == "FAILED"
