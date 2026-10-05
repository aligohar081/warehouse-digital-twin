"""Re-run the soak and print a timeline of failures, cancels and the robot
that ends up idle while holding a tote."""
import sys
exec(open("soak_probe.py").read().split("sim = Simulator(twin)")[0])
from backend.models import SimulationStatus
from backend.simulator import Simulator
TICKS = int(sys.argv[1]) if len(sys.argv) > 1 else 20000
sim = Simulator(twin)
twin.simulation_status = SimulationStatus.RUNNING
twin.shift.configure(pace=2.0)
twin.shift.start()
flagged = set()
for i in range(TICKS):
    sim.tick()
    for r in twin.robots.values():
        if r.current_task is None and r.carrying_box and r.id not in flagged:
            flagged.add(r.id)
            print(f"t={twin.tick_count} {r.name} idle holding {r.carrying_box} at {r.position}")
keep = ("TASK_FAILED", "TASK_CANCELLED", "ORDER_FAILED")
for e in twin.events.query(limit=200000):
    if e["event"] in keep:
        print(e["seq"], e["time"], e["event"], e.get("robot_id"), e.get("task_id"), e.get("box_id"), e["message"][:150])
