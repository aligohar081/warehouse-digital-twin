"""The two fixes from the final review of plan 1c: the gate refuses a job
field the job does not take (a stray slot must not hide a tote's real level
from the reach rule) and a fractional quantity; and a damaged save changes
nothing, however it is damaged."""
import json

import pytest

from backend import people
from backend.agent_tools import execute_tool
from backend.app import create_app
from backend.digital_twin import DigitalTwin
from backend.models import TaskStatus


@pytest.fixture
def twin(tmp_path):
    return DigitalTwin(log_dir=str(tmp_path / "logs"), data_dir=str(tmp_path / "data"),
                       persist_logs=False, demo=True, demo_tasks=False, layout="distribution_center")


def high_tote(twin):
    return twin.add_box(name="TOTE-HI", kind="TOTE", sku="SKU-02", quantity=12, weight=9.0, slot="TS-14-12-2")


def test_a_stray_slot_on_a_tote_job_is_refused_at_the_gate(twin):
    twin.add_robot(name="TR50-201", asset_id="AST-000201", position=(8, 13))   # reach: level 1
    box = high_tote(twin)
    with pytest.raises(ValueError, match="TOTE_TO_STATION takes no 'slot'"):
        twin.tasks.create_task({"type": "TOTE_TO_STATION", "box_id": box.id, "slot": "TS-10-12-1"})


def test_the_same_tote_job_without_the_slot_goes_to_a_reach_two_robot(twin):
    twin.add_robot(name="TR50-201", asset_id="AST-000201", position=(8, 13))
    humanoid = twin.add_robot(name="H1-212", asset_id="AST-000212", position=(16, 13))
    jordan = twin.add_operator(name="Jordan", worker_id="E-10006")
    people.place(twin, jordan, "tote_aisle_1")                 # a humanoid works under supervision
    box = high_tote(twin)
    task = twin.tasks.create_task({"type": "TOTE_TO_STATION", "box_id": box.id, "station": "pick_station_2"})
    scored, _ = twin.tasks._score_candidates(task)
    assert task.status is not TaskStatus.FAILED
    assert [entry[1] for entry in scored] == [humanoid]


def test_the_reach_rule_ignores_a_slot_the_job_does_not_take(twin):
    # Even a task that got past create_task (a saved one, say) is judged at the tote's own level.
    from backend.jobs import job_levels
    box = high_tote(twin)
    task = twin.tasks.create_task({"type": "TOTE_TO_STATION", "box_id": box.id})
    task.params["slot"] = "TS-10-12-1"
    assert job_levels(twin, task) == [twin.warehouse.slot("TS-14-12-2").level] == [2]


def test_the_agent_tool_path_refuses_the_stray_slot_too(twin):
    twin.add_robot(name="TR50-201", asset_id="AST-000201", position=(8, 13))
    box = high_tote(twin)
    before = len(twin.tasks.tasks)
    out = execute_tool(twin, "create_task", {"type": "TOTE_TO_STATION", "box_id": box.id, "slot": "TS-10-12-1"})
    assert "takes no 'slot'" in out["error"] and len(twin.tasks.tasks) == before


@pytest.mark.parametrize("quantity", [2.5, 2.0])
def test_a_fractional_quantity_is_refused_not_truncated(twin, quantity):
    tote = twin.add_box(name="TOTE-1", kind="TOTE", sku="SKU-02", quantity=12, weight=9.0, slot="TS-10-12-1")
    task = twin.tasks.create_task({"type": "MANUAL_PICK", "box_id": tote.id, "quantity": quantity,
                                   "order_id": "ORD-1", "pack_cell": "pack_1"})
    assert task.status is TaskStatus.FAILED and "whole number" in task.error


def world(twin):
    """Everything a damaged load must leave alone."""
    return json.dumps({"robots": sorted(twin.robots), "boxes": sorted(twin.boxes),
                       "tasks": sorted(twin.tasks.tasks), "tick": twin.tick_count,
                       "status": twin.simulation_status.value, "state": twin.serialize()},
                      sort_keys=True, default=str)


def damaged_save(twin, tmp_path, damage):
    twin.add_robot(name="TR50-201", asset_id="AST-000201", position=(8, 13))
    path = str(twin.save_state(str(tmp_path / "save.json")))
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    twin.add_robot(name="H1-212", asset_id="AST-000212", position=(16, 13))   # the twin now differs from the save
    twin.add_box(name="TOTE-X", kind="TOTE", sku="SKU-02", quantity=3, weight=5.0, slot="TS-10-12-1")
    damage(payload)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)
    return path


def test_a_save_with_a_bad_simulation_status_changes_nothing(twin, tmp_path):
    path = damaged_save(twin, tmp_path, lambda p: p.__setitem__("simulation", {"status": "BOGUS"}))
    before = world(twin)
    with pytest.raises(ValueError):
        twin.load_state(path)
    assert world(twin) == before


@pytest.mark.parametrize("damage", [
    lambda p: p.__setitem__("counters", {"task": "many"}),
    lambda p: p.__setitem__("counters", ["task"]),
    lambda p: p.__setitem__("schedules", [1]),
    lambda p: p.__setitem__("statistics", [1]),
    lambda p: p.__setitem__("simulation", {"tick": "soon"}),
    lambda p: p.__setitem__("simulation", [1]),
    lambda p: p.__setitem__("robots", [[1, 2]]),
])
def test_every_kind_of_damaged_tail_changes_nothing(twin, tmp_path, damage):
    path = damaged_save(twin, tmp_path, damage)
    before = world(twin)
    with pytest.raises(ValueError):
        twin.load_state(path)
    assert world(twin) == before


@pytest.mark.parametrize("damage", [
    lambda p: p.__setitem__("robots", {"robot_01": {}}),
    lambda p: p.__setitem__("shift", [1, 2]),
    lambda p: p.__setitem__("stock", [1]),
    lambda p: p.__setitem__("equipment", [1]),
    lambda p: p.__setitem__("tasks", [None]),
])
def test_a_save_of_the_wrong_shape_is_a_400_through_the_api(twin, tmp_path, damage):
    path = damaged_save(twin, tmp_path, damage)
    twin.state_path = path
    app, _twin, _sim, _ci = create_app(twin=twin, autostart=False, run_thread=False)
    before = world(twin)
    response = app.test_client().post("/api/state/load")
    assert response.status_code == 400, response.get_data(as_text=True)[:200]
    assert world(twin) == before
