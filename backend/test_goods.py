"""Goods (multi-embodiment spec §7): box kinds and weights, and the stock
ledger's recorded-versus-true quantities."""
import pytest

from backend.box import Box
from backend.digital_twin import DigitalTwin
from backend.goods import BoxKind, StockLedger, carton_weight_kg
from backend.warehouse import Warehouse


@pytest.fixture(scope="module")
def dc():
    return Warehouse(layout="distribution_center")


@pytest.fixture
def ledger(dc):
    stock = StockLedger(dc)
    stock.put("PR-08-02-0", "box_001", "SKU-0001", 40, kind="PALLET")
    stock.put("TS-10-12-1", "box_002", "SKU-0002", 20, kind="TOTE")
    stock.put("TS-11-12-1", "box_003", "SKU-0002", 15, kind="TOTE")
    return stock


# --------------------------------------------------------------------------- #
# Boxes
# --------------------------------------------------------------------------- #
def test_a_classic_box_is_a_tote_whose_weight_is_its_declared_weight():
    box = Box("box_001", "Box-A", (3, 5), weight=12.5)
    assert (box.kind, box.sku, box.quantity, box.slot, box.order_id) == (BoxKind.TOTE, None, 0, None, None)
    assert box.weight == box.declared_weight_kg == box.true_weight_kg == 12.5
    box.weight = 14.0
    assert box.declared_weight_kg == 14.0 and box.true_weight_kg == 12.5  # the alias sets the declared weight
    assert [kind.value for kind in BoxKind] == ["PALLET", "TOTE", "ITEM", "CARTON"]
    with pytest.raises(ValueError):
        Box("box_002", "Bad", (1, 1), kind="CRATE")


def test_box_goods_fields_round_trip():
    pallet = Box("box_007", "PAL-007", (8, 2), weight=600.0, kind="PALLET", sku="SKU-0007", quantity=48,
                 true_weight_kg=780.0, slot="PR-08-02-0", order_id="ORD-0003")
    data = pallet.to_dict()
    assert (data["kind"], data["sku"], data["quantity"], data["slot"], data["order_id"]) == \
        ("PALLET", "SKU-0007", 48, "PR-08-02-0", "ORD-0003")
    assert (data["weight"], data["declared_weight_kg"], data["true_weight_kg"]) == (600.0, 600.0, 780.0)
    restored = Box.from_dict(data)
    assert restored.to_dict() == data
    legacy = {key: value for key, value in data.items()
              if key not in ("kind", "sku", "quantity", "declared_weight_kg", "true_weight_kg", "slot", "order_id")}
    old = Box.from_dict(legacy)
    assert (old.kind, old.weight, old.true_weight_kg, old.slot) == (BoxKind.TOTE, 600.0, 600.0, None)


def test_classic_demo_boxes_are_unchanged(tmp_path):
    twin = DigitalTwin(log_dir=str(tmp_path / "logs"), data_dir=str(tmp_path / "data"),
                       persist_logs=False, demo=True, demo_tasks=False)
    box = twin.find_box("Box-A")
    assert (box.weight, box.kind, box.position) == (12.5, BoxKind.TOTE, (3, 5))


def test_a_carton_weighs_its_items_plus_the_carton():
    assert carton_weight_kg([1.0, 2.5]) == 3.8
    assert carton_weight_kg([]) == 0.3


# --------------------------------------------------------------------------- #
# Stock ledger
# --------------------------------------------------------------------------- #
def test_the_ledger_maps_slots_to_boxes_and_skus_to_locations(ledger):
    assert ledger.box_in("PR-08-02-0") == "box_001" and ledger.slot_of("box_002") == "TS-10-12-1"
    assert [loc.slot_id for loc in ledger.locations("SKU-0002")] == ["TS-10-12-1", "TS-11-12-1"]
    assert ledger.skus() == ["SKU-0001", "SKU-0002"]
    assert ledger.recorded_qty("SKU-0002") == ledger.true_qty("SKU-0002") == 35
    assert ledger.box_in("TS-16-18-2") is None and ledger.slot_of("box_999") is None


def test_the_ledger_rejects_impossible_placements(ledger):
    with pytest.raises(ValueError, match="Unknown slot"):
        ledger.put("PR-99-99-0", "box_010", "SKU-0001", 1)
    with pytest.raises(ValueError, match="cannot go in"):
        ledger.put("TS-12-12-0", "box_010", "SKU-0001", 1, kind="PALLET")
    with pytest.raises(ValueError, match="already holds"):
        ledger.put("PR-08-02-0", "box_010", "SKU-0001", 1)
    with pytest.raises(ValueError, match="already in slot"):
        ledger.put("PR-09-02-0", "box_001", "SKU-0001", 1)
    with pytest.raises(ValueError):
        ledger.put("PR-09-02-0", "box_010", "SKU-0001", -1)
    loose = StockLedger()  # no warehouse: any slot id is accepted
    loose.put("anywhere", "box_010", "SKU-0001", 1, kind="PALLET")


def test_picks_and_mis_picks(ledger):
    ledger.consume("TS-10-12-1", 3)
    location = ledger.location("TS-10-12-1")
    assert (location.recorded_qty, location.true_qty, location.discrepancy) == (17, 17, 0)
    ledger.consume("TS-10-12-1", 2, true_quantity=3)  # a mis-pick took one more than recorded
    assert (location.recorded_qty, location.true_qty, location.discrepancy) == (15, 14, -1)
    assert ledger.discrepancies() == [location]
    with pytest.raises(ValueError, match="records only"):
        ledger.consume("TS-10-12-1", 99)
    with pytest.raises(KeyError):
        ledger.consume("TS-16-18-2", 1)


def test_returns_and_faults(ledger):
    ledger.restock("TS-11-12-1", 5)
    assert (ledger.location("TS-11-12-1").recorded_qty, ledger.location("TS-11-12-1").true_qty) == (20, 20)
    ledger.adjust_true("PR-08-02-0", -4)  # something vanished without the record knowing
    assert ledger.location("PR-08-02-0").discrepancy == -4
    ledger.adjust_true("PR-08-02-0", -100)
    assert ledger.location("PR-08-02-0").true_qty == 0


def test_counts_flag_variance_and_only_reconciliation_corrects_the_record(ledger):
    with pytest.raises(ValueError, match="not been counted"):
        ledger.reconcile("TS-10-12-1")
    ledger.adjust_true("TS-10-12-1", -2)
    small = ledger.record_count("TS-10-12-1", 18, tick=40)
    assert (small["variance"], small["auto_reconcile"], small["recorded_qty"]) == (-2, True, 20)
    assert ledger.location("TS-10-12-1").recorded_qty == 20  # counting alone changes nothing
    assert ledger.reconcile("TS-10-12-1") == -2
    assert ledger.location("TS-10-12-1").recorded_qty == 18 and not ledger.discrepancies()
    ledger.adjust_true("TS-11-12-1", -3)
    large = ledger.record_count("TS-11-12-1", 12)
    assert (large["variance"], large["auto_reconcile"]) == (-3, False)  # an exception for the orders panel
    assert ledger.record_count("PR-08-02-0", 40)["auto_reconcile"] is False  # no variance at all


def test_take_and_persistence(ledger, dc):
    ledger.record_count("TS-10-12-1", 19, tick=7)
    restored = StockLedger.from_dict(ledger.to_dict(), dc)
    assert restored.to_dict() == ledger.to_dict()
    assert restored.location("TS-10-12-1").counted_tick == 7
    taken = ledger.take("PR-08-02-0")
    assert taken.box_id == "box_001" and ledger.box_in("PR-08-02-0") is None
    assert ledger.slot_of("box_001") is None
    with pytest.raises(KeyError):
        ledger.take("PR-08-02-0")
    ledger.put("PR-08-02-0", "box_001", "SKU-0001", 40, kind="PALLET")  # the slot is free again
