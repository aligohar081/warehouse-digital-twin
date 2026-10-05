"""Pace 1: at tick 20000, who blocks the two forklifts, and how."""
exec(open("soak_probe.py").read().split("sim = Simulator(twin)")[0])
from backend.models import SimulationStatus
from backend.simulator import Simulator
from backend import people
sim = Simulator(twin)
twin.simulation_status = SimulationStatus.RUNNING
twin.shift.configure(pace=1.0)
twin.shift.start()
for i in range(20000):
    sim.tick()
for r in twin.robots.values():
    t = twin.tasks.get(r.current_task) if r.current_task else None
    a = t.current_action if t else None
    print(f"{r.name:10s} {str(r.position):9s} {r.status.value:10s} wait={r.wait_reason} blocked_by={r.blocked_by} "
          f"wt={r.wait_ticks} next={r.next_cell} target={r.target_position} task={t.type.value if t else None}/"
          f"{t.status.value if t else None} act={a.type.value if a else None}->{a.target if a else None}")
print("people:", [(o.name, o.zone, o.transit_to) for o in twin.operators.values()])
for zone in ("intake_staging", "outbound_staging", "dock_1", "dock_2", "dock_3"):
    z = twin.warehouse.zones[zone]
    print(zone, "cells", len(z.cells), "boxes on it", sum(1 for b in twin.boxes.values() if b.position in z.cells and b.status.value != "SHIPPED"))
for r in twin.robots.values():
    if r.name.startswith("PF1200"):
        evs = [e for e in twin.events.query(robot_id=r.id, limit=300000) if e["event"] in ("COLLISION_AVOIDED", "DEADLOCK_RESOLVED", "ROBOT_SAFETY_WAIT", "PATH_RECALCULATED")]
        print(r.name, "recent:", [(e["event"], e["message"][:80]) for e in evs[-6:]])
