"""Classic seed guard: the `classic` inventory seed profile must stay byte for
byte what A's tests were written against. The fingerprint covers every fleet
and workforce table (the catalog tables are excluded, since the catalog is
shared by every profile and gains fields). It was taken before seed profiles
existed; if it changes, the classic seed changed."""
import hashlib
import json
from datetime import datetime, timezone

from backend.inventory import open_inventory

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)
FLEET_AND_WORKFORCE_TABLES = (
    "robot_asset", "component", "calibration_record", "work_order", "ota_job",
    "reported_state", "worker", "worker_credential", "training_completion", "change_log",
)
CLASSIC_FINGERPRINT = "f743841530d9d345f78e8e79b7c3b3a8f33f638a593e3e203deaac33176709f2"


def fingerprint(inventory) -> str:
    digest = hashlib.sha256()
    for table in FLEET_AND_WORKFORCE_TABLES:
        for row in inventory.store.select(table, order="rowid"):
            digest.update(json.dumps(row, sort_keys=True).encode("utf-8"))
    return digest.hexdigest()


def test_the_classic_seed_is_unchanged():
    inventory = open_inventory(":memory:", demo=True, clock=lambda: NOW)
    assert fingerprint(inventory) == CLASSIC_FINGERPRINT
