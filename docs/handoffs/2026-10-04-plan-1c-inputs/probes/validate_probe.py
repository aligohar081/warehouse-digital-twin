import sys, tempfile
sys.path.insert(0, "/Users/bakarmac/projects/physical_ai/warehouse-digital-twin")
from backend.digital_twin import DigitalTwin
tmp = tempfile.mkdtemp()
twin = DigitalTwin(log_dir=tmp + "/l", data_dir=tmp + "/d", persist_logs=False, demo=True, demo_tasks=False,
                   layout="distribution_center")
twin.add_robot(name="PF1200-205", asset_id="AST-000205", position=(7, 4))
p = twin.add_box(name="PAL", kind="PALLET", sku="S", quantity=10, weight=300.0, position=(3, 4))
for payload in ({"type": "PUTAWAY_PALLET", "box_id": p.id, "slot": ["PR-08-02-0"]},
                {"type": "CYCLE_COUNT", "face": {"x": "a"}},
                {"type": "CLEAR_JAM", "segment": 7}):
    try:
        t = twin.tasks.create_task(payload)
        print("ok", payload["type"], t.status.value, t.error)
    except Exception as exc:
        print("RAISED", payload["type"], type(exc).__name__, exc)
print("left behind:", [(t.id, t.type.value, t.status.value) for t in twin.tasks.tasks.values() if not t.is_terminal])
