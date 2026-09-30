"""The layout registry (backend/layouts) and the twin's layout choice."""
import pytest

from backend.digital_twin import DigitalTwin
from backend.layouts import LAYOUTS, build_layout
from backend.warehouse import Warehouse


def make_twin(tmp_path, **kwargs):
    return DigitalTwin(log_dir=str(tmp_path / "logs"), data_dir=str(tmp_path / "data"),
                       persist_logs=False, demo_tasks=False, **kwargs)


def test_the_registry_builds_classic_by_default():
    assert "classic" in LAYOUTS
    layout = build_layout()
    assert layout.name == "classic"
    assert (layout.width, layout.height) == (20, 15)
    assert build_layout("classic", 24, 18).width == 24  # a custom grid size still works


def test_an_unknown_layout_is_rejected():
    with pytest.raises(ValueError, match="Unknown layout 'moon_base'"):
        build_layout("moon_base")
    with pytest.raises(ValueError):
        Warehouse(layout="moon_base")


def test_warehouse_defaults_to_classic():
    default, explicit = Warehouse(), Warehouse(layout="classic")
    assert default.layout_name == explicit.layout_name == "classic"
    assert default.to_dict() == explicit.to_dict()
    assert default.required_zones == ("charging_station", "loading_zone", "packing_area", "shelf_a")


def test_the_twin_defaults_to_classic_and_reports_its_layout(tmp_path):
    twin = make_twin(tmp_path, demo=False)
    assert twin.layout_name == "classic" == twin.warehouse.layout_name
    assert twin.snapshot()["layout_name"] == "classic"
    with pytest.raises(ValueError):
        make_twin(tmp_path, demo=False, layout="moon_base")


def test_reset_keeps_the_layout(tmp_path):
    twin = make_twin(tmp_path, demo=True)
    warehouse = twin.warehouse
    twin.reset(demo_tasks=False)
    assert twin.warehouse is warehouse and twin.layout_name == "classic"
    assert len(twin.robots) == 2
