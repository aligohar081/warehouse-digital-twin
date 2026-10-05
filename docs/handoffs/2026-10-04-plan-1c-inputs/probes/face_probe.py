"""Stop at the first 'No face of slot' failure and show who stands where."""
import sys
exec(open("soak_probe.py").read().split("sim = Simulator(twin)")[0])
from backend.models import SimulationStatus
from backend.simulator import Simulator
sim = Simulator(twin)
twin.simulation_status = SimulationStatus.RUNNING
twin.shift.configure(pace=2.0)
twin.shift.start()
hit = None
for i in range(5000):
    sim.tick()
    for t in twin.tasks.tasks.values():
        if t.status.value == "FAILED" and t.error and t.error.startswith("No face"):
            hit = t
            break
    if hit:
        break
print("tick", twin.tick_count, hit.id, hit.type.value, hit.error, "robot", hit.robot_id)
robot = twin.find_robot(hit.robot_id)
slot = twin.warehouse.slot(hit.error.split()[4])
print("slot", slot.slot_id, "cell", slot.cell, "faces", slot.faces)
for r in twin.robots.values():
    print(f"  {r.id} {r.name:10s} {r.position} {r.layer} {r.status.value:10s} task={r.current_task} carry={r.carrying_box}")
nav = twin.navigation
blocked = twin.other_robot_cells(robot.id)
for face in slot.faces:
    print("face", face, "free path:", nav.path_exists(robot.position, face, profile=robot.mobility, allow_goal_adjacent=False),
          "with robots blocked:", nav.path_exists(robot.position, face, blocked=blocked, profile=robot.mobility, allow_goal_adjacent=False),
          "face occupied:", face in blocked)
