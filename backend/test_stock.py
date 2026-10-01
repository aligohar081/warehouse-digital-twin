"""Goods in the twin (multi-embodiment spec §7): the twin's stock ledger is
the single source of truth, boxes of every kind are placed by their kind, and
the helpers keep each box's slot and quantity in step with the ledger."""
import pytest

from backend import goods
from backend.digital_twin import DigitalTwin
from backend.models import BoxKind, BoxStatus, CONFIG


def make_twin(tmp_path, layout="distribution_center"):
    return DigitalTwin(log_dir=str(tmp_path / "logs"), data_dir=str(tmp_path / "data"),
                       persist_logs=False, demo=True, demo_tasks=False, layout=layout)


@pytest.fixture
def twin(tmp_path):
    return make_twin(tmp_path)


def test_the_twin_owns_one_ledger_and_reset_empties_it(twin):
    assert isinstance(twin.stock, goods.StockLedger) and twin.stock.warehouse is twin.warehouse
    twin.add_box(name="PAL-1", kind="PALLET", sku="SKU-01", quantity=40, weight=600.0, slot="PR-08-02-0")
    assert twin.stock.box_in("PR-08-02-0") is not None
    twin.reset(demo_tasks=False)
    assert twin.stock.locations() == [] and twin.stock.warehouse is twin.warehouse


def test_a_pallet_or_tote_given_a_slot_sits_in_it_and_is_recorded(twin):
    pallet = twin.add_box(name="PAL-1", kind="PALLET", sku="SKU-01", quantity=40, weight=600.0,
                          slot="PR-08-02-3", true_weight_kg=780.0)
    assert (pallet.kind, pallet.position, pallet.slot) == (BoxKind.PALLET, (8, 2), "PR-08-02-3")
    assert (pallet.declared_weight_kg, pallet.true_weight_kg) == (600.0, 780.0)
    location = twin.stock.location("PR-08-02-3")
    assert (location.box_id, location.sku, location.recorded_qty, location.true_qty) == (pallet.id, "SKU-01", 40, 40)
    tote = twin.add_box(name="TOTE-1", kind="tote", sku="SKU-02", quantity=12, weight=9.0, slot="TS-10-12-1")
    assert tote.position == (10, 12) and twin.stock.slot_of(tote.id) == "TS-10-12-1"
    created = twin.events.query(event_type="BOX_CREATED")[-1]
    assert created["data"]["kind"] == "TOTE" and created["data"]["slot"] == "TS-10-12-1"


def test_impossible_slot_placements_create_nothing(twin):
    twin.add_box(name="PAL-1", kind="PALLET", sku="SKU-01", quantity=40, weight=600.0, slot="PR-08-02-0")
    count = len(twin.boxes)
    with pytest.raises(ValueError, match="already holds"):
        twin.add_box(name="PAL-2", kind="PALLET", sku="SKU-01", quantity=10, slot="PR-08-02-0")
    with pytest.raises(ValueError, match="cannot go in"):
        twin.add_box(name="TOTE-9", kind="TOTE", sku="SKU-01", quantity=10, slot="PR-09-02-0")
    with pytest.raises(ValueError, match="Unknown slot"):
        twin.add_box(name="PAL-3", kind="PALLET", slot="PR-99-02-0")
    with pytest.raises(ValueError, match="is at"):
        twin.add_box(name="PAL-4", kind="PALLET", slot="PR-09-02-0", position=(1, 1))
    with pytest.raises(ValueError, match="non-negative"):
        twin.add_box(name="PAL-5", kind="PALLET", slot="PR-10-02-0", quantity=-1)
    assert len(twin.boxes) == count and twin.find_box("PAL-2") is None


def test_items_and_cartons_sit_exactly_where_they_are_put(twin):
    item = twin.add_box(name="ITEM-1", kind="ITEM", sku="SKU-01", quantity=1, weight=0.4, position=(21, 15))
    assert item.position == (21, 15)  # a conveyor cell: never snapped onto Pick 1's work cell
    carton = twin.add_box(name="CTN-1", kind="CARTON", weight=1.1, position=(23, 14))
    assert carton.position == (23, 14) and carton.kind is BoxKind.CARTON
    with pytest.raises(ValueError, match="needs a position"):
        twin.add_box(name="ITEM-2", kind="ITEM")
    with pytest.raises(ValueError, match="outside"):
        twin.add_box(name="ITEM-3", kind="ITEM", position=(40, 40))


def test_a_floor_box_needs_a_position_on_the_new_floor(twin, tmp_path):
    with pytest.raises(ValueError, match="needs a position or a slot"):
        twin.add_box(name="Loose")
    pallet = twin.add_box(name="PAL-D1", kind="PALLET", sku="SKU-03", quantity=20, weight=450.0, position=(2, 3))
    assert pallet.position == (2, 3) and pallet.slot is None and twin.stock.slot_of(pallet.id) is None
    classic = make_twin(tmp_path / "classic", layout="classic")
    box = classic.add_box(name="Box-Z")
    assert box.position == (3, 5) and box.kind is BoxKind.TOTE        # classic's default shelf, as before
    assert classic.events.query(event_type="BOX_CREATED")[-1]["data"] == {}


def test_store_release_and_sync_keep_the_box_and_the_ledger_in_step(twin):
    pallet = twin.add_box(name="PAL-D1", kind="PALLET", sku="SKU-03", quantity=20, weight=450.0, position=(5, 4))
    goods.store(twin, pallet, "PR-12-05-2")
    assert (pallet.slot, pallet.position, pallet.true_slot) == ("PR-12-05-2", (12, 5), None)
    assert twin.stock.location("PR-12-05-2").recorded_qty == 20
    goods.release(twin, pallet)
    assert pallet.slot is None and twin.stock.location("PR-12-05-2") is None
    with pytest.raises(ValueError, match="not in the stock ledger"):
        goods.release(twin, pallet)
    tote = twin.add_box(name="TOTE-1", kind="TOTE", sku="SKU-02", quantity=12, weight=9.0, slot="TS-10-12-1")
    twin.stock.adjust_true("TS-10-12-1", -2)             # two units went missing
    tote.position = (19, 14)                              # away at Pick 1: it keeps its slot
    goods.store(twin, tote, "TS-10-12-1")                 # back home
    location = twin.stock.location("TS-10-12-1")
    assert (location.recorded_qty, location.true_qty, tote.position) == (12, 10, (10, 12))
    goods.store(twin, tote, "TS-11-12-2")                 # moved to another slot: quantities go with it
    moved = twin.stock.location("TS-11-12-2")
    assert (moved.recorded_qty, moved.true_qty, tote.slot) == (12, 10, "TS-11-12-2")
    assert twin.stock.location("TS-10-12-1") is None


def test_a_wrong_level_placement_is_recorded_where_it_was_meant_to_go(twin):
    pallet = twin.add_box(name="PAL-D1", kind="PALLET", sku="SKU-03", quantity=20, weight=450.0, position=(5, 4))
    goods.store(twin, pallet, "PR-12-05-2", true_slot_id="PR-12-05-3")
    assert (pallet.slot, pallet.true_slot) == ("PR-12-05-2", "PR-12-05-3")
    assert twin.stock.location("PR-12-05-2").recorded_qty == 20
    assert goods.physical_qty(twin, "PR-12-05-2") == 0      # nothing really there...
    assert goods.physical_qty(twin, "PR-12-05-3") == 20     # ...it went one level up
    assert "PR-12-05-3" not in [slot.slot_id for slot in goods.free_slots(twin, "PALLET")]


def test_picking_units_out_of_a_tote(twin):
    tote = twin.add_box(name="TOTE-1", kind="TOTE", sku="SKU-02", quantity=10, weight=11.5, slot="TS-10-12-1")
    assert goods.unit_weight_kg(11.5, 10) == 1.0 and goods.unit_weight_kg(5.0, 0) == 0.0
    assert goods.take_units(twin, tote, 2) == (1.0, 1.0)
    location = twin.stock.location("TS-10-12-1")
    assert (location.recorded_qty, location.true_qty, tote.quantity) == (8, 8, 8)
    assert tote.declared_weight_kg == tote.true_weight_kg == 9.5
    twin.stock.adjust_true("TS-10-12-1", -8)                # the tote is really empty
    with pytest.raises(ValueError, match="physically holds only 0"):
        goods.take_units(twin, tote, 1)
    pallet = twin.add_box(name="PAL-1", kind="PALLET", sku="SKU-01", quantity=40, slot="PR-08-02-0")
    with pytest.raises(ValueError, match="not a TOTE"):
        goods.take_units(twin, pallet, 1)
    assert CONFIG["TOTE_TARE_KG"] == 1.5


def test_free_slots_are_empty_within_reach_and_nearest_first(twin):
    twin.add_box(name="PAL-1", kind="PALLET", sku="SKU-01", quantity=40, slot="PR-08-02-0")
    near_intake = goods.free_slots(twin, "PALLET", near=(6, 3))
    assert near_intake[0].slot_id == "PR-08-02-1"           # PR-08-02-0 is taken
    assert all(slot.kind == "PALLET" for slot in near_intake) and len(near_intake) == 179
    low = goods.free_slots(twin, "TOTE", max_level=1, exclude=["TS-08-12-0"])
    assert all(slot.level <= 1 for slot in low) and "TS-08-12-0" not in [s.slot_id for s in low]
    assert len(low) == 71


def test_a_shipped_box_no_longer_occupies_its_cell(twin):
    pallet = twin.add_box(name="PAL-D3", kind="PALLET", sku="SKU-03", quantity=20, weight=450.0, position=(28, 2))
    forklift = twin.add_robot(name="PF1200-205", asset_id="AST-000205", position=(27, 2))
    route = {"profile": forklift.mobility, "layer": forklift.layer}
    assert twin.box_at((28, 2)) is pallet
    assert twin.planner.resolve_target("dock_3", forklift.position, prefer_free=True, **route)[0] != (28, 2)
    pallet.set_status(BoxStatus.SHIPPED)
    assert twin.box_at((28, 2)) is None
    cell, _ = twin.planner.resolve_target("dock_3", forklift.position, prefer_free=True, **route)
    assert cell == (28, 2)  # the nearest dock cell is free again
