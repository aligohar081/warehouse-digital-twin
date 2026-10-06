"""The new floor's job types through the task API, the chat agent and the
options (multi-embodiment spec §14 "POST /api/tasks accepts the new job
types"): each type's own fields reach create_task, the task types a floor
offers are the ones it runs, each with its form fields and guide, and the
agent's create-task tool takes every job field."""
import re

import pytest

from backend import people
from backend.agent_tools import TASK_TYPE_GUIDE, TOOLS, execute_tool
from backend.app import create_app
from backend.digital_twin import TASK_FIELDS, TASK_GUIDES, DigitalTwin
from backend.jobs import JOB_PARAM_KEYS, JOB_SPECS
from backend.models import BoxStatus, TaskType

OLDER_TYPES = [
    "PICK_AND_DELIVER", "MOVE_ROBOT", "PICK_BOX", "DELIVER_BOX", "MOVE_BOX", "CHARGE_ROBOT", "STOP_ROBOT",
    "RESUME_ROBOT", "AGENT_INSPECTION", "HUMAN_INSPECTION", "MIXED_MAINTENANCE_MISSION", "AGENT_REPLAN",
    "AGENT_AUDIT", "OPERATOR_APPROVAL", "OPERATOR_MAINTENANCE_SIGNOFF", "BATCH_DELIVER",
]
#: frontend/app.js's TASK_FIELDS as it was before plan 1c: the classic task form keeps exactly these.
DASHBOARD_FIELDS = {
    "PICK_AND_DELIVER": ["box", "source", "destination", "priority"],
    "MOVE_BOX": ["box", "source", "destination", "priority"],
    "DELIVER_BOX": ["box", "destination", "priority"],
    "PICK_BOX": ["box", "priority"],
    "MOVE_ROBOT": ["destination", "priority"],
    "CHARGE_ROBOT": ["priority"],
    "STOP_ROBOT": [],
    "RESUME_ROBOT": [],
    "AGENT_INSPECTION": ["agent"],
    "HUMAN_INSPECTION": ["operator"],
    "MIXED_MAINTENANCE_MISSION": ["destination", "agent", "operator", "priority"],
    "AGENT_REPLAN": ["agent"],
    "AGENT_AUDIT": ["agent"],
    "OPERATOR_APPROVAL": ["operator"],
    "OPERATOR_MAINTENANCE_SIGNOFF": ["operator"],
    "BATCH_DELIVER": ["box_ids", "destination", "priority"],
}
#: The words a JobSpec guide uses for each form field.
GUIDE_WORDS = {"box_id": "box", "operator_id": "operator", "destination": "destination",
               **{key: key for key in JOB_PARAM_KEYS}}
FORM_FIELDS = {"box", "box_ids", "source", "destination", "priority", "agent", "operator", *JOB_PARAM_KEYS}


def make_twin(path, layout="distribution_center"):
    return DigitalTwin(log_dir=str(path / "logs"), data_dir=str(path / "data"), persist_logs=False,
                       demo=True, demo_tasks=False, layout=layout)


@pytest.fixture
def twin(tmp_path):
    return make_twin(tmp_path)


@pytest.fixture
def floor(twin):
    """A body for every job type, the people its human jobs and the humanoid
    need, and a box in the state each job starts from."""
    for name, asset, cell in (("TR50-201", "AST-000201", (8, 13)), ("PF1200-205", "AST-000205", (7, 4)),
                              ("PK30-203", "AST-000203", (20, 14)), ("IX2-208", "AST-000208", (20, 2)),
                              ("SC1-204", "AST-000204", (7, 6)), ("H1-212", "AST-000212", (22, 7))):
        twin.add_robot(name=name, asset_id=asset, position=cell)
    twin.add_robot(name="CX10-210", asset_id="AST-000210")                 # the arm takes its station
    for name, worker, zone in (("Sam", "E-10001", "pick_station_2"), ("Jordan", "E-10006", "returns_qc"),
                               ("Noor", "E-10003", "workshop")):
        people.place(twin, twin.add_operator(name=name, worker_id=worker), zone)
    for name, where in (("PAL-DOCK", {"position": (2, 3)}), ("PAL-STAGED", {"position": (5, 3)}),
                        ("PAL-RACK", {"slot": "PR-10-05-0"}), ("PAL-OUT", {"position": (23, 1)})):
        twin.add_box(name=name, kind="PALLET", sku="SKU-001", quantity=40, weight=500.0, **where)
    twin.add_box(name="TOTE-HOME", kind="TOTE", sku="SKU-002", quantity=12, weight=9.0, slot="TS-10-12-1")
    for name, slot, drop in (("TOTE-P1", "TS-11-12-1", (19, 14)), ("TOTE-P2", "TS-12-12-1", (19, 16))):
        tote = twin.add_box(name=name, kind="TOTE", sku="SKU-002", quantity=12, weight=9.0, slot=slot)
        tote.position = drop                                                # brought to the station's tote drop
        tote.set_status(BoxStatus.DELIVERED)
    twin.add_box(name="RET-1", kind="TOTE", sku="SKU-005", quantity=8, weight=7.0, position=(20, 6))
    twin.equipment.jam((20, 15))
    return twin


@pytest.fixture
def client(floor):
    app, _twin, _sim, _ci = create_app(twin=floor, autostart=False, run_thread=False)
    app.config["TESTING"] = True
    return app.test_client()


# ---- the options ------------------------------------------------------------ #
def test_the_classic_floor_offers_only_the_task_types_it_runs(tmp_path):
    options = make_twin(tmp_path, layout="classic").options()
    # Fails while options() offers the JOB_SPECS types on every floor (plan 1b ruling 16's stopgap).
    assert [entry["id"] for entry in options["task_types"]] == OLDER_TYPES
    assert {entry["id"]: entry["fields"] for entry in options["task_types"]} == DASHBOARD_FIELDS
    assert options["task_types"][0] == {"id": "PICK_AND_DELIVER", "label": "Pick & deliver",
                                        "fields": ["box", "source", "destination", "priority"], "guide": "box+dest"}
    assert all(entry["guide"] == TASK_GUIDES[entry["id"]] for entry in options["task_types"])
    assert [entry["id"] for entry in options["robot_capability_task_types"]] == [
        "PICK_AND_DELIVER", "MOVE_ROBOT", "PICK_BOX", "DELIVER_BOX", "MOVE_BOX", "CHARGE_ROBOT",
        "MIXED_MAINTENANCE_MISSION", "BATCH_DELIVER"]


def test_the_new_floor_offers_every_type_with_its_form(twin):
    options = twin.options()
    assert [entry["id"] for entry in options["task_types"]] == OLDER_TYPES + [kind.value for kind in JOB_SPECS]
    entries = {entry["id"]: entry for entry in options["task_types"]}
    assert entries["TOTE_TO_STATION"] == {"id": "TOTE_TO_STATION", "label": "Tote to station",
                                          "fields": ["box", "station", "priority"],
                                          "guide": JOB_SPECS[TaskType.TOTE_TO_STATION].guide}
    assert entries["MANUAL_PICK"]["fields"] == ["box", "quantity", "order_id", "pack_cell", "operator", "priority"]
    assert entries["PATROL"]["fields"] == ["priority"]
    assert all(set(entry["fields"]) <= FORM_FIELDS for entry in options["task_types"])
    capability = [entry["id"] for entry in options["robot_capability_task_types"]]
    assert {"PATROL", "CYCLE_COUNT", "PACK_ORDER"} <= set(capability)
    assert "MANUAL_PICK" not in capability and "CLEAR_JAM" not in capability     # people do those


def test_each_job_types_fields_are_the_ones_its_guide_names():
    assert set(TASK_FIELDS) == set(OLDER_TYPES) | {kind.value for kind in JOB_SPECS}
    for kind, spec in JOB_SPECS.items():
        outside_notes = re.sub(r"\([^)]*\)", "", spec.guide)      # "(a tote in its slot)" describes, names nothing
        named = {GUIDE_WORDS[word] for word in re.findall(r"[a-z_]+", outside_notes) if word in GUIDE_WORDS}
        assert set(TASK_FIELDS[kind.value]) - {"priority"} == named, kind.value


# ---- POST /api/tasks ---------------------------------------------------------- #
CASES = [
    ("UNLOAD_TRUCK", {"box": "PAL-DOCK", "destination": "intake_staging"}, {"destination": "intake_staging"}),
    ("PUTAWAY_PALLET", {"box": "PAL-STAGED", "slot": "PR-08-02-1"}, {"params": {"slot": "PR-08-02-1"}}),
    ("RETRIEVE_PALLET", {"box": "PAL-RACK", "destination": "outbound_staging"}, {"destination": "outbound_staging"}),
    ("LOAD_TRUCK", {"box": "PAL-OUT", "dock": "dock_3"}, {"params": {"dock": "dock_3"}, "destination": "dock_3"}),
    ("TOTE_TO_STATION", {"box": "TOTE-HOME", "station": "pick_station_2"}, {"params": {"station": "pick_station_2"}}),
    ("RETURN_TOTE", {"box": "TOTE-P1", "slot": "TS-11-12-1"}, {"params": {"slot": "TS-11-12-1"}}),
    ("RETURNS_PUTAWAY", {"box": "RET-1", "slot": "TS-13-12-0"}, {"params": {"slot": "TS-13-12-0"}}),
    # A form sends its numbers as text.
    ("PICK_ITEMS", {"box": "TOTE-P1", "quantity": "2", "order_id": "ORD-7", "pack_cell": "pack_cell_1"},
     {"params": {"quantity": 2, "order_id": "ORD-7", "pack_cell": "pack_cell_1"}}),
    ("PACK_ORDER", {"order_id": "ORD-7", "quantity": 2, "pack_cell": "pack_cell_1", "lane": "dock_5"},
     {"params": {"order_id": "ORD-7", "quantity": 2, "pack_cell": "pack_cell_1", "lane": "dock_5"}}),
    ("MANUAL_PICK", {"box": "TOTE-P2", "quantity": 1, "order_id": "ORD-8", "pack_cell": "pack_cell_1",
                     "operator": "Sam"}, {"params": {"quantity": 1, "order_id": "ORD-8"}, "operator": "Sam"}),
    ("CLEAR_JAM", {"segment": "20,15", "operator": "Noor"}, {"params": {"segment": "20,15"}, "operator": "Noor"}),
    ("CYCLE_COUNT", {"face": "12,2"}, {"params": {"face": "12,2"}}),
    ("PATROL", {"priority": "HIGH"}, {"priority": "HIGH"}),
]


@pytest.mark.parametrize("kind,fields,expected", CASES, ids=[case[0] for case in CASES])
def test_each_new_job_type_is_created_through_the_api_with_its_own_fields(floor, client, kind, fields, expected):
    assert set(fields) <= set(TASK_FIELDS[kind])                 # only the fields its form shows
    response = client.post("/api/tasks", json={"type": kind, **fields})
    body = response.get_json()
    assert response.status_code == 201, body["error"]
    task = body["task"]
    assert task["type"] == kind
    if "box" in fields:
        assert task["box_id"] == floor.find_box(fields["box"]).id
    for key, value in expected.get("params", {}).items():
        assert task["params"][key] == value, key
    if "destination" in expected:
        assert task["destination"] == expected["destination"]
    if "operator" in expected:
        assert task["operator_id"] == floor.find_operator(expected["operator"]).id
    if "priority" in expected:
        assert task["priority"] == expected["priority"]


def test_the_api_checks_a_job_types_own_fields(floor, client):
    before = len(floor.tasks.tasks)
    # A stray slot would skip TOTE_TO_STATION's reach check (it plans from the tote's own slot).
    stray = client.post("/api/tasks", json={"type": "TOTE_TO_STATION", "box": "TOTE-HOME", "slot": "TS-10-12-2"})
    assert stray.status_code == 400
    assert stray.get_json()["error"] == "TOTE_TO_STATION takes no 'slot' (its fields: box, station, priority)"
    assert stray.get_json()["field"] == "slot"
    for quantity in (1.5, "two", 0, True):
        response = client.post("/api/tasks", json={"type": "PICK_ITEMS", "box": "TOTE-P1", "quantity": quantity})
        assert response.status_code == 400, quantity
        assert response.get_json()["error"] == "quantity must be a whole number of at least 1"
    assert len(floor.tasks.tasks) == before                      # nothing was created
    # An empty form field is left out, so its default applies (fails if "" reaches the task's params).
    blank = client.post("/api/tasks", json={"type": "pick_items", "box": "TOTE-P1", "quantity": "1",
                                            "order_id": "", "pack_cell": " "})
    assert blank.status_code == 201, blank.get_json()["error"]
    params = blank.get_json()["task"]["params"]
    assert params["quantity"] == 1 and "order_id" not in params and "pack_cell" not in params
    # The older task types are passed on exactly as before.
    older = client.post("/api/tasks", json={"type": "MOVE_ROBOT", "destination": "parking_area", "slot": "TS-10-12-1"})
    assert older.status_code == 201 and older.get_json()["task"]["params"] == {"slot": "TS-10-12-1"}


# ---- the chat agent ------------------------------------------------------------- #
def test_the_agent_can_give_every_job_field(floor):
    schema = next(tool for tool in TOOLS if tool["function"]["name"] == "create_task")["function"]["parameters"]
    assert set(JOB_PARAM_KEYS) <= set(schema["properties"])      # fails before: the schema had none of them
    assert schema["properties"]["quantity"]["type"] == ["integer", "null"]
    assert all("null" in schema["properties"][key]["type"] for key in JOB_PARAM_KEYS)   # optional (see _opt)
    out = execute_tool(floor, "create_task", {"type": "TOTE_TO_STATION", "box_id": "TOTE-HOME",
                                              "station": "pick_station_2", "slot": None, "robot_id": None})
    assert "error" not in out, out
    assert floor.tasks.get(out["task_id"]).params == {"station": "pick_station_2"}
    for kind in OLDER_TYPES:
        assert f"{kind}:{TASK_GUIDES[kind]}" in TASK_TYPE_GUIDE
    for kind, spec in JOB_SPECS.items():
        assert f"{kind.value}:{spec.guide}" in TASK_TYPE_GUIDE
    assert "PUTAWAY_PALLET:box_id(a staged pallet)" in TASK_TYPE_GUIDE
