"""Pace 1: follow H1-212's battery and tasks up to the tick it goes flat."""
import sys
exec(open("soak_probe.py").read().split("sim = Simulator(twin)")[0])
from backend.models import SimulationStatus
from backend.simulator import Simulator
sim = Simulator(twin)
twin.simulation_status = SimulationStatus.RUNNING
twin.shift.configure(pace=1.0)
twin.shift.start()
h = twin.find_robot("H1-212")
last_task = None
log = []
for i in range(27000):
    sim.tick()
    if h.current_task != last_task:
        t = twin.tasks.get(h.current_task) if h.current_task else None
        log.append((twin.tick_count, round(h.battery, 2), t.type.value if t else None,
                    t.battery_estimate if t else None, h.status.value))
        last_task = h.current_task
    if h.status.value == "ERROR":
        break
for row in log[-25:]:
    print(row)
for e in twin.events.query(robot_id=h.id, limit=200000):
    if e["event"] in ("BATTERY_LOW", "BATTERY_CRITICAL", "ROBOT_ERROR", "TASK_FAILED", "ROBOT_CHARGING", "ROBOT_CHARGED"):
        print(e["seq"], e["event"], e.get("task_id"), e["message"][:140])
for e in twin.events.query(limit=300000):
    if e["category"] == "BATTERY" and "H1-212" in e["message"] and e["event"] not in ("BATTERY_LOW", "BATTERY_CRITICAL"):
        print("LOG", e["seq"], e["event"], e["message"][:160])
waits = [e for e in twin.events.query(robot_id=h.id, event_type="ROBOT_SAFETY_WAIT", limit=100000)]
print("safety waits", len(waits), [w["data"]["reason"] for w in waits][-10:])
