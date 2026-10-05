"""One forklift, one heavy pallet put away high: planner estimate vs real drain."""
import sys, tempfile
sys.path.insert(0, "/Users/bakarmac/projects/physical_ai/warehouse-digital-twin")
from backend.digital_twin import DigitalTwin
from backend.models import SimulationStatus, CONFIG
from backend.simulator import Simulator
for slot, weight in (("PR-08-02-0", 300.0), ("PR-08-02-4", 1100.0), ("PR-16-05-4", 1100.0)):
    tmp = tempfile.mkdtemp()
    twin = DigitalTwin(log_dir=tmp + "/l", data_dir=tmp + "/d", persist_logs=False, demo=True, demo_tasks=False,
                       layout="distribution_center")
    fork = twin.add_robot(name="PF1200-205", asset_id="AST-000205", position=(7, 4))
    pallet = twin.add_box(name="PAL", kind="PALLET", sku="S", quantity=10, weight=weight, position=(3, 4))
    sim = Simulator(twin); twin.simulation_status = SimulationStatus.RUNNING
    before = fork.battery
    task = twin.tasks.create_task({"type": "PUTAWAY_PALLET", "box_id": pallet.id, "slot": slot})
    ticks = 0
    while not task.is_terminal and ticks < 5000:
        sim.tick(); ticks += 1
    used = before - fork.battery
    print(slot, weight, task.status.value, f"estimate(no reserve)={task.battery_estimate - CONFIG['BATTERY_RESERVE']:.2f}%",
          f"actual={used:.2f}%", f"ticks={ticks}")
