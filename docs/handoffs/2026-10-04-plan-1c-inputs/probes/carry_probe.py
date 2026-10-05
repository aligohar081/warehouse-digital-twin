"""Minimal: cancel an AMR's tote job mid-carry, then see what the floor does."""
import sys, tempfile
sys.path.insert(0, "/Users/bakarmac/projects/physical_ai/warehouse-digital-twin")
from backend.digital_twin import DigitalTwin
from backend.models import SimulationStatus
from backend.simulator import Simulator
tmp = tempfile.mkdtemp()
twin = DigitalTwin(log_dir=tmp + "/l", data_dir=tmp + "/d", persist_logs=False, demo=True, demo_tasks=False,
                   layout="distribution_center")
amr = twin.add_robot(name="TR50-201", asset_id="AST-000201", position=(8, 13))
t1 = twin.add_box(name="TOTE-1", kind="TOTE", sku="SKU-1", quantity=20, weight=21.5, slot="TS-09-12-0")
t2 = twin.add_box(name="TOTE-2", kind="TOTE", sku="SKU-2", quantity=20, weight=21.5, slot="TS-11-12-0")
sim = Simulator(twin); twin.simulation_status = SimulationStatus.RUNNING
job = twin.tasks.create_task({"type": "TOTE_TO_STATION", "box_id": t1.id})
while amr.carrying_box is None:
    sim.tick()
twin.tasks.cancel_task(job.id)          # what OrderBook._fail does to a failed order's live stages
sim.tick()
print("after cancel:", amr.status.value, "holding", amr.carrying_box, "| tote", t1.status.value, t1.position)
amr.battery = 15.0                      # below BATTERY_LOW: an idle robot would normally go and charge
for _ in range(60):
    sim.tick()
print("low battery, 60 ticks later:", amr.status.value, "task", amr.current_task, "battery", round(amr.battery, 2))
nxt = twin.tasks.create_task({"type": "TOTE_TO_STATION", "box_id": t2.id})
for _ in range(400):
    sim.tick()
    if nxt.is_terminal:
        break
print("next tote job:", nxt.status.value, "robot", nxt.robot_id, "error:", nxt.error)
print("tote-1 still stranded:", t1.status.value, "holding robot:", amr.carrying_box)
