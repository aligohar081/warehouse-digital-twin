"""Pace 1: follow both forklifts' battery and tasks up to the tick they go flat."""
exec(open("soak_probe.py").read().split("sim = Simulator(twin)")[0])
from backend.models import SimulationStatus
from backend.simulator import Simulator
sim = Simulator(twin)
twin.simulation_status = SimulationStatus.RUNNING
twin.shift.configure(pace=1.0)
twin.shift.start()
watch = [twin.find_robot("PF1200-205"), twin.find_robot("PF1200-206")]
logs = {r.id: [] for r in watch}
last = {r.id: object() for r in watch}
for i in range(52000):
    sim.tick()
    for r in watch:
        key = (r.current_task, r.carrying_box)
        if key != last[r.id]:
            t = twin.tasks.get(r.current_task) if r.current_task else None
            logs[r.id].append((twin.tick_count, round(r.battery, 2), t.type.value if t else None,
                               t.battery_estimate if t else None, r.status.value, r.carrying_box,
                               t.status.value if t else None))
            last[r.id] = key
    if all(r.status.value == "ERROR" for r in watch):
        break
for r in watch:
    print("==", r.name, r.status.value, r.last_error)
    for row in logs[r.id][-14:]:
        print(row)
    for e in twin.events.query(robot_id=r.id, limit=300000):
        if e["event"] in ("BATTERY_LOW", "BATTERY_CRITICAL", "ROBOT_ERROR", "ROBOT_CHARGING", "ROBOT_CHARGED", "TASK_CANCELLED") or \
           (e["event"] == "TASK_FAILED"):
            print("  ", e["seq"], e["event"], e.get("task_id"), e["message"][:130])
for e in twin.events.query(limit=400000):
    if "PF1200" in e["message"] and ("Auto-charge" in e["message"] or "heading to" in e["message"] or "routing via" in e["message"]):
        print("LOG", e["seq"], e["message"][:150])
