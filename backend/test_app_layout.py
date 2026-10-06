"""The app boots the floor WAREHOUSE_LAYOUT names — the distribution centre
unless it names another (spec §2 "Construction", plan 1c ruling 1) — with
that floor's seed (§4.2) and its shift paused (§11.5, ruling 6).

Classic keeps logs/ and data/. Any other floor gets its own logs/<layout>/
and data/<layout>/, so switching floors never reseeds the other floor's
inventory file (ruling 2). Both reset routes put the seed back through
twin.reset(), with the shift paused again.
"""
import pytest

from backend import app as app_module
from backend.app import build_twin, create_app
from backend.models import BoxKind
from backend.operations.shift import ShiftEngine

FLEET_SIZE = 15   # every WH-01 asset but the decommissioned AST-000213
CREW_SIZE = 10
GOODS = {BoxKind.PALLET: 60, BoxKind.TOTE: 80}


def goods(twin):
    counts = {}
    for box in twin.boxes.values():
        counts[box.kind] = counts.get(box.kind, 0) + 1
    return counts


def test_classic_keeps_its_folders_and_demo(tmp_path):
    twin = build_twin("classic", str(tmp_path))
    assert twin.layout_name == "classic"
    assert (tmp_path / "data" / "inventory.sqlite3").exists()
    assert twin.log_dir == str(tmp_path / "logs")
    assert len(twin.robots) == 2 and twin.floor_seeded
    assert twin.shift.status == ShiftEngine.PAUSED


def test_the_new_floor_boots_with_its_seed_and_its_shift_paused(tmp_path):
    twin = build_twin("distribution_center", str(tmp_path))
    assert twin.layout_name == "distribution_center" and twin.floor_seeded
    assert len(twin.robots) == FLEET_SIZE
    assert all(robot.asset_id for robot in twin.robots.values())
    assert len(twin.operators) == CREW_SIZE and all(operator.worker_id for operator in twin.operators.values())
    assert goods(twin) == GOODS
    assert [agent.name for agent in twin.agents.values()] == ["Ada"]
    # Nothing runs until someone starts the shift (POST /api/shift/start, Task 5).
    assert twin.shift.status == ShiftEngine.PAUSED and not twin.tasks.tasks


def test_the_new_floor_never_touches_classic_files(tmp_path):
    twin = build_twin("distribution_center", str(tmp_path))
    assert (tmp_path / "data" / "distribution_center" / "inventory.sqlite3").exists()
    assert twin.log_dir == str(tmp_path / "logs" / "distribution_center")
    assert not (tmp_path / "data" / "inventory.sqlite3").exists()


def test_a_second_boot_reuses_the_saved_inventory(tmp_path):
    first = build_twin("distribution_center", str(tmp_path))
    again = build_twin("distribution_center", str(tmp_path))
    assert again.inventory.store.epoch() == first.inventory.store.epoch()   # the file was reused, not reseeded
    assert len(again.robots) == FLEET_SIZE
    assert {robot.asset_id for robot in again.robots.values()} == {robot.asset_id for robot in first.robots.values()}


def test_an_unknown_layout_is_refused_before_anything_is_written(tmp_path):
    with pytest.raises(ValueError, match="distribution_center"):
        build_twin("warehouse2", str(tmp_path))
    assert not (tmp_path / "data").exists()
    assert not (tmp_path / "logs").exists()


def test_the_app_boots_the_distribution_centre_by_default(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "BASE_DIR", str(tmp_path))
    _app, twin, _sim, _ci = create_app(autostart=False, run_thread=False)
    assert twin.layout_name == "distribution_center" and len(twin.robots) == FLEET_SIZE
    assert (tmp_path / "data" / "distribution_center" / "inventory.sqlite3").exists()


@pytest.mark.parametrize("route", ["/api/simulation/reset", "/api/state/reset"])
def test_a_reset_puts_the_seed_back_with_the_shift_paused(tmp_path, monkeypatch, route):
    monkeypatch.setattr(app_module, "BASE_DIR", str(tmp_path))
    app, twin, _sim, _ci = create_app(layout="distribution_center", autostart=False, run_thread=False)
    app.config["TESTING"] = True
    twin.shift.start()
    twin.add_robot(name="Extra", robot_class="AMR")
    response = app.test_client().post(route, json={})
    assert response.status_code == 200
    assert len(twin.robots) == FLEET_SIZE and twin.find_robot("Extra") is None
    assert len(twin.operators) == CREW_SIZE and goods(twin) == GOODS
    assert twin.shift.status == ShiftEngine.PAUSED
    assert len(response.get_json()["state"]["robots"]) == FLEET_SIZE


def boot_with(monkeypatch, layout):
    """The layout main() hands create_app with WAREHOUSE_LAYOUT set to `layout`
    (None: unset)."""
    booted = {}

    class Stop(Exception):
        pass

    def fake_create_app(**kwargs):
        booted.update(kwargs)
        raise Stop

    if layout is None:
        monkeypatch.delenv("WAREHOUSE_LAYOUT", raising=False)
    else:
        monkeypatch.setenv("WAREHOUSE_LAYOUT", layout)
    monkeypatch.setattr(app_module, "create_app", fake_create_app)
    with pytest.raises(Stop):
        app_module.main()
    return booted["layout"]


def test_main_boots_the_floor_named_by_WAREHOUSE_LAYOUT(monkeypatch):
    assert boot_with(monkeypatch, "classic") == "classic"


@pytest.mark.parametrize("unset", [None, ""])
def test_main_defaults_to_the_distribution_centre(monkeypatch, unset):
    assert boot_with(monkeypatch, unset) == "distribution_center"
