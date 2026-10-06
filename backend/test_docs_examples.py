"""The documentation stays true to the code (multi-embodiment spec §15).

policies.example.yaml is meant to be copied to policies.yaml as it is, and
apply_policy replaces whole tables (certification_requirements,
robot_classes, operator_roles) and ignores a config key CONFIG doesn't have.
A stale example therefore breaks the new floor quietly: no credential for a
jam inside a pack cell, no ARM or HUMANOID class (the floor's seed then
refuses to start), job types missing from a class's list, a risk that never
takes effect. The README's API tables, TRUST_LAYER.md and RUN.md must also
name what the code serves, checks and runs. Every assertion compares exact
identifiers taken from the code, never prose.
"""
import copy
import glob
import os
import re

import pytest

from backend import eligibility
from backend.app import create_app
from backend.digital_twin import DigitalTwin
from backend.eval_engine import EMBODIMENT_CHECKS
from backend.faults import FAULT_RISKS
from backend.layouts import LAYOUTS
from backend.models import CONFIG
from backend.policy import DEFAULT_POLICY_PATH, apply_policy, effective_policy, read_policy_file

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXAMPLE = os.path.join(ROOT, "policies.example.yaml")


def doc(name):
    with open(os.path.join(ROOT, name), encoding="utf-8") as handle:
        return handle.read()


def example():
    data = read_policy_file(EXAMPLE)
    assert data, "policies.example.yaml did not load (the loader turns a parse error into {})"
    return data


def test_the_policy_example_sets_only_known_keys_and_every_fault_risk_off():
    config = example()["config"]
    # apply_policy skips a key CONFIG doesn't have, so a misspelt risk would never take effect.
    assert sorted(set(config) - set(CONFIG)) == []
    # Fails while the example lacks the seven risks of spec §10.6.
    assert {key: config.get(key) for key in FAULT_RISKS.values()} == {key: 0.0 for key in FAULT_RISKS.values()}


@pytest.mark.skipif(os.path.exists(DEFAULT_POLICY_PATH), reason="a local policies.yaml overrides the built-in policy")
def test_copying_the_policy_example_changes_no_policy_in_effect():
    data = example()
    # Fails while the example's certification table lacks CLEAR_JAM (a jam inside
    # a pack cell would need no credential) or its robot classes lack ARM and
    # HUMANOID or any class's new job types.
    assert data["certification_requirements"].get("CLEAR_JAM") == "robot_cell_access"
    assert {"ARM", "HUMANOID"} <= set(data["robot_classes"])
    saved = effective_policy()
    before = copy.deepcopy(saved)
    try:
        apply_policy(data)
        after = copy.deepcopy(effective_policy())
    finally:
        apply_policy(saved)  # puts the very same objects back
    assert after == before


def test_the_readme_lists_every_operations_route(tmp_path):
    twin = DigitalTwin(log_dir=str(tmp_path / "logs"), data_dir=str(tmp_path / "data"),
                       persist_logs=False, demo=False, demo_tasks=False)
    app = create_app(twin=twin, autostart=False, run_thread=False)[0]
    readme = doc("README.md")
    rules = [rule for rule in app.url_map.iter_rules()
             if app.view_functions[rule.endpoint].__module__ == "backend.operations_api"]
    assert len(rules) >= 10
    missing = []
    for rule in rules:
        # /api/orders/<order_id> is written /api/orders/{id} in the README's tables.
        path = r"\{\w+\}".join(re.escape(part) for part in re.split(r"<[^>]+>", rule.rule))
        for method in sorted(rule.methods - {"HEAD", "OPTIONS"}):
            if not re.search(rf"^\|\s*{method}\s*\|\s*`{path}`\s*\|", readme, re.M):
                missing.append(f"{method} {rule.rule}")
    assert missing == []


def test_the_readme_names_every_fault_kind_and_its_risk():
    readme = doc("README.md")
    names = list(FAULT_RISKS) + list(FAULT_RISKS.values())
    assert [name for name in names if f"`{name}`" not in readme] == []


def test_trust_layer_names_every_new_rule_check_and_risk():
    text = doc("TRUST_LAYER.md")
    rules = sorted(name for name in dir(eligibility) if name.endswith("_ok") and callable(getattr(eligibility, name)))
    checks = [check.__name__[len("check_"):] for check in EMBODIMENT_CHECKS]
    assert len(rules) == 8 and len(checks) == 10
    names = rules + checks + list(FAULT_RISKS.values())
    assert [name for name in names if f"`{name}" not in text] == []


def test_run_md_names_each_floor_the_soak_and_each_javascript_wrapper():
    text = doc("RUN.md")
    wrappers = sorted(os.path.basename(path) for path in glob.glob(os.path.join(ROOT, "backend", "test_*_js.py")))
    assert wrappers
    names = [f"WAREHOUSE_LAYOUT={name}" for name in sorted(LAYOUTS)] + ["python -m backend.soak"] + \
        [f"backend/{name}" for name in wrappers]
    assert [name for name in names if name not in text] == []
