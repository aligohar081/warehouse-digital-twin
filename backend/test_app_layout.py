"""The app boots the floor WAREHOUSE_LAYOUT names (spec §2 "Construction").

Classic stays the default and keeps logs/ and data/. Any other floor gets its
own logs/<layout>/ and data/<layout>/, so switching floors never reseeds
classic's inventory file. The distribution-centre floor boots populated with
the fleet and crew the shift soak runs, and its shift is running, at boot and
after either reset.
"""
import pytest

from backend import app as app_module
from backend.app import build_twin, create_app
from backend.operations.shift import ShiftEngine

FLEET_SIZE = 12   # 10 mobile robots + 2 arms
CREW_SIZE = 10


def test_classic_keeps_its_folders_and_demo(tmp_path):
    twin = build_twin("classic", str(tmp_path))
    assert twin.layout_name == "classic"
    assert (tmp_path / "data" / "inventory.sqlite3").exists()
    assert twin.log_dir == str(tmp_path / "logs")
    assert len(twin.robots) == 2
    assert twin.shift.status == ShiftEngine.PAUSED


def test_the_new_floor_boots_populated_with_its_shift_running(tmp_path):
    twin = build_twin("distribution_center", str(tmp_path))
    assert twin.layout_name == "distribution_center"
    assert len(twin.robots) == FLEET_SIZE
    assert all(robot.asset_id for robot in twin.robots.values())
    assert len(twin.operators) == CREW_SIZE
    assert {box.kind for box in twin.boxes.values()} == {"TOTE", "PALLET"}
    assert twin.shift.status == ShiftEngine.RUNNING


def test_the_new_floor_never_touches_classic_files(tmp_path):
    twin = build_twin("distribution_center", str(tmp_path))
    assert (tmp_path / "data" / "distribution_center" / "inventory.sqlite3").exists()
    assert twin.log_dir == str(tmp_path / "logs" / "distribution_center")
    assert not (tmp_path / "data" / "inventory.sqlite3").exists()


def test_a_second_boot_reuses_the_saved_inventory(tmp_path):
    build_twin("distribution_center", str(tmp_path))
    again = build_twin("distribution_center", str(tmp_path))
    assert len(again.robots) == FLEET_SIZE
    assert all(robot.asset_id for robot in again.robots.values())


def test_an_unknown_layout_is_refused_before_anything_is_written(tmp_path):
    with pytest.raises(ValueError, match="distribution_center"):
        build_twin("warehouse2", str(tmp_path))
    assert not (tmp_path / "data").exists()
    assert not (tmp_path / "logs").exists()


@pytest.mark.parametrize("route", ["/api/simulation/reset", "/api/state/reset"])
def test_a_reset_puts_the_new_floor_back_and_restarts_its_shift(tmp_path, monkeypatch, route):
    monkeypatch.setattr(app_module, "BASE_DIR", str(tmp_path))
    app, twin, _sim, _ci = create_app(layout="distribution_center", autostart=False, run_thread=False)
    app.config["TESTING"] = True
    response = app.test_client().post(route, json={})
    assert response.status_code == 200
    assert len(twin.robots) == FLEET_SIZE
    assert len(twin.operators) == CREW_SIZE
    assert twin.shift.status == ShiftEngine.RUNNING


def test_main_boots_the_floor_named_by_WAREHOUSE_LAYOUT(monkeypatch):
    booted = {}

    class Stop(Exception):
        pass

    def fake_create_app(**kwargs):
        booted.update(kwargs)
        raise Stop

    monkeypatch.setenv("WAREHOUSE_LAYOUT", "distribution_center")
    monkeypatch.setattr(app_module, "create_app", fake_create_app)
    with pytest.raises(Stop):
        app_module.main()
    assert booted["layout"] == "distribution_center"


def test_main_defaults_to_classic(monkeypatch):
    booted = {}

    class Stop(Exception):
        pass

    def fake_create_app(**kwargs):
        booted.update(kwargs)
        raise Stop

    monkeypatch.delenv("WAREHOUSE_LAYOUT", raising=False)
    monkeypatch.setattr(app_module, "create_app", fake_create_app)
    with pytest.raises(Stop):
        app_module.main()
    assert booted["layout"] == "classic"
