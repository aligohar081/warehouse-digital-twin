"""The dashboard floor (multi-embodiment spec §12), checked under JavaScriptCore.

frontend/tests/floor_model_test.js tests frontend/floor_model.js against
FIXTURES: real /api/state snapshots of both floors, built here through the
public API (a classic demo twin, and a distribution-centre twin with one robot
of every type, people in a pack cell and on a walk, conveyor items and a jam).
This file also checks that frontend/app.js compiles, that both files stay
ES5, and that the page loads the floor model before the dashboard. Skipped
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

JSC = "/System/Library/Frameworks/JavaScriptCore.framework/Versions/A/Helpers/jsc"
FRONTEND = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend")

pytestmark = pytest.mark.skipif(not os.path.exists(JSC), reason="JavaScriptCore (jsc) is not installed")

#: (twin name, inventory asset, cell): one robot of every type. The arm takes its station.
FLEET = (("TR50-201", "AST-000201", (8, 13)), ("PF1200-205", "AST-000205", (7, 4)),
         ("HH300-207", "AST-000207", (2, 8)), ("PK30-203", "AST-000203", (20, 14)),
         ("SC1-204", "AST-000204", (7, 6)), ("IX2-208", "AST-000208", (20, 2)),
         ("H1-212", "AST-000212", (22, 7)))


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
    for name, asset, cell in FLEET:
        twin.add_robot(name=name, asset_id=asset, position=cell)
    twin.add_robot(name="CX10-210", asset_id="AST-000210")
    drone = twin.find_robot("IX2-208")
    twin.set_robot_layer(drone.id, "AIR", 2.0)
    mateo = twin.add_operator(name="Mateo Silva", worker_id="E-10004")
    riley = twin.add_operator(name="Riley Chen", worker_id="E-10008")
    jordan = twin.add_operator(name="Jordan Blake", worker_id="E-10006")
    twin.add_operator(name="Sasha Ivanova", worker_id="E-10009")  # never placed: off the floor
    people.place(twin, mateo, "pack_cell_1")
    people.place(twin, riley, "pack_cell_1")
    people.place(twin, jordan, "returns_qc")
    people.start_transit(twin, jordan, "tote_aisle_1")
    twin.add_box(name="ITEM-1", kind="ITEM", sku="SKU-001", quantity=1, weight=1.0, position=(20, 15))
    twin.add_box(name="CARTON-1", kind="CARTON", quantity=0, weight=2.0, position=(22, 15))
    twin.add_box(name="PAL-1", kind="PALLET", sku="SKU-002", quantity=40, weight=500.0, slot="PR-10-05-0")
    twin.equipment.jam((23, 15))
    return state_of(twin)


def test_the_floor_model_draws_both_floors(tmp_path):
    fixtures = tmp_path / "fixtures.js"
    fixtures.write_text("var FIXTURES = " + json.dumps({
        "classic": classic_state(tmp_path), "dc": distribution_center_state(tmp_path),
    }) + ";\n")
    result = run_jsc(str(fixtures), os.path.join(FRONTEND, "floor_model.js"),
                     os.path.join(FRONTEND, "tests", "floor_model_test.js"))
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "FAIL" not in output, output
    assert re.search(r"\b\d+ passed, 0 failed\b", output), output


def test_the_dashboard_script_compiles():
    # new Function parses the whole file without running it (it needs a page).
    result = run_jsc("-e", "new Function(readFile(arguments[0]));", "--", os.path.join(FRONTEND, "app.js"))
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("name", ["floor_model.js", "app.js"])
def test_the_dashboard_stays_es5(name):
    source = open(os.path.join(FRONTEND, name), encoding="utf-8").read()
    assert "=>" not in source, f"{name} uses an arrow function"
    assert not re.search(r"\b(let|const)\s+[A-Za-z_$][\w$]*\s*=", source), f"{name} declares with let/const"
    assert not re.search(r"\bclass\s+[A-Za-z_$][\w$]*\s*\{", source), f"{name} declares a class"


def test_the_page_loads_the_floor_model_before_the_dashboard():
    html = open(os.path.join(FRONTEND, "index.html"), encoding="utf-8").read()
    assert '<script src="floor_model.js"></script>' in html
    assert html.index('src="floor_model.js"') < html.index('src="app.js"')
