"""The dashboard panels (multi-embodiment spec §12), checked under JavaScriptCore.

frontend/tests/panels_test.js tests the panel helpers of frontend/floor_model.js
(robot panel, shift panel, the task form's fields and payload, a person
walking back, the fleet link) against FIXTURES: real /api/state snapshots of
both floors built here through the public API — a distribution-centre twin
with a robot on a job, a person walking back out of a cancelled CLEAR_JAM and
another walking to one — plus the page's HTML and the backend's fault kinds.
This file also checks that frontend/fleet.js compiles and stays ES5. Skipped
where jsc is not installed.
"""
from __future__ import annotations

import json
import os
import re
import subprocess

import pytest

from backend import people
from backend.digital_twin import DigitalTwin
from backend.faults import FAULT_RISKS
from backend.models import SimulationStatus
from backend.simulator import Simulator

JSC = "/System/Library/Frameworks/JavaScriptCore.framework/Versions/A/Helpers/jsc"
FRONTEND = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend")

pytestmark = pytest.mark.skipif(not os.path.exists(JSC), reason="JavaScriptCore (jsc) is not installed")


def run_jsc(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([JSC, *args], capture_output=True, text=True, timeout=120)


def state_of(twin: DigitalTwin) -> dict:
    """What GET /api/state sends, through JSON as the browser gets it."""
    return json.loads(json.dumps(twin.snapshot(include_layout=True), default=str))


def classic_state(tmp_path) -> dict:
    twin = DigitalTwin(log_dir=str(tmp_path / "classic_logs"), data_dir=str(tmp_path / "classic_data"),
                       persist_logs=False, demo=True, demo_tasks=False)
    return state_of(twin)


def distribution_center_state(tmp_path) -> dict:
    twin = DigitalTwin(log_dir=str(tmp_path / "dc_logs"), data_dir=str(tmp_path / "dc_data"),
                       persist_logs=False, demo=True, demo_tasks=False, layout="distribution_center")
    twin.add_robot(name="TR50-201", asset_id="AST-000201", position=(8, 13))
    twin.add_robot(name="CX10-210", asset_id="AST-000210")
    drone = twin.add_robot(name="IX2-208", asset_id="AST-000208", position=(20, 2))
    sam = twin.add_operator(name="Sam", worker_id="E-10001")
    lee = twin.add_operator(name="Lee", worker_id="E-10002")
    people.place(twin, sam, "intake_staging")
    people.place(twin, lee, "pick_station_2")
    simulator = Simulator(twin)
    twin.simulation_status = SimulationStatus.RUNNING
    twin.equipment.jam((21, 15))
    jam = twin.tasks.create_task({"type": "CLEAR_JAM", "segment": "21,15", "operator_id": sam.id})
    for _ in range(2000):
        if jam.params["phase"] == "WORK":
            break
        simulator.tick()
    assert jam.params["phase"] == "WORK", "Sam never reached the jam"
    twin.tasks.cancel_task(jam.id)       # Sam walks back out, still ON_TASK on the ended job
    twin.equipment.jam((19, 15))
    twin.tasks.create_task({"type": "CLEAR_JAM", "segment": "19,15", "operator_id": lee.id})
    tote = twin.add_box(name="TOTE-1", kind="TOTE", sku="SKU-001", quantity=12, weight=9.0, slot="TS-10-12-1")
    twin.tasks.create_task({"type": "TOTE_TO_STATION", "box_id": tote.id})
    for _ in range(3):
        simulator.tick()
    twin.set_robot_layer(drone.id, "AIR", 2.5)
    return state_of(twin)


def test_the_panels_read_both_floors(tmp_path):
    fixtures = tmp_path / "fixtures.js"
    fixtures.write_text("var FIXTURES = " + json.dumps({
        "classic": classic_state(tmp_path), "dc": distribution_center_state(tmp_path),
        "faultKinds": sorted(FAULT_RISKS),
        "indexHtml": open(os.path.join(FRONTEND, "index.html"), encoding="utf-8").read(),
    }) + ";\n")
    result = run_jsc(str(fixtures), os.path.join(FRONTEND, "floor_model.js"),
                     os.path.join(FRONTEND, "tests", "panels_test.js"))
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "FAIL" not in output, output
    assert re.search(r"\b\d+ passed, 0 failed\b", output), output


def test_the_fleet_page_script_compiles():
    # new Function parses the whole file without running it (it needs a page).
    result = run_jsc("-e", "new Function(readFile(arguments[0]));", "--", os.path.join(FRONTEND, "fleet.js"))
    assert result.returncode == 0, result.stdout + result.stderr


def test_the_fleet_page_stays_es5():
    source = open(os.path.join(FRONTEND, "fleet.js"), encoding="utf-8").read()
    assert "=>" not in source, "fleet.js uses an arrow function"
    assert not re.search(r"\b(let|const)\s+[A-Za-z_$][\w$]*\s*=", source), "fleet.js declares with let/const"
