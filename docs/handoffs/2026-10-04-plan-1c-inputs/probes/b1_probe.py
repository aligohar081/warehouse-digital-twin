"""(b1): the one real case a new-floor robot carries a box not tagged with its current job:
PICK_BOX then DELIVER_BOX (older types). Cancel the DELIVER_BOX mid-carry: is the box set down?"""
import sys, tempfile
sys.path.insert(0, "/Users/bakarmac/projects/physical_ai/warehouse-digital-twin")
from backend.digital_twin import DigitalTwin
from backend.models import SimulationStatus
from backend.simulator import Simulator
tmp = tempfile.mkdtemp()
twin = DigitalTwin(log_dir=tmp + "/l", data_dir=tmp + "/d", persist_logs=False, demo=True, demo_tasks=False,
                   layout="distribution_center")
amr = twin.add_robot(name="TR50-201", asset_id="AST-000201", position=(8, 11))
box = twin.add_box(name="B", kind="TOTE", sku="S", quantity=1, weight=5.0, position=(12, 11))
sim = Simulator(twin); twin.simulation_status = SimulationStatus.RUNNING
pick = twin.tasks.create_task({"type": "PICK_BOX", "robot_id": amr.id, "box_id": box.id})
print("PICK_BOX", pick.status.value, pick.error)
for _ in range(400):
    if pick.is_terminal: break
    sim.tick()
print("after PICK_BOX:", pick.status.value, "carrying", amr.carrying_box, "box.assigned_task", box.assigned_task)
deliver = twin.tasks.create_task({"type": "DELIVER_BOX", "robot_id": amr.id, "box_id": box.id, "destination": "outbound_staging"})
print("DELIVER_BOX", deliver.status.value, deliver.error)
for _ in range(10):
    sim.tick()
twin.tasks.cancel_task(deliver.id)
print("after cancel: carrying", amr.carrying_box, "box", box.status.value, box.assigned_task)
