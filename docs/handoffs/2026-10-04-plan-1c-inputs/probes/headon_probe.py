"""Head-on variants beyond the fix's tests: pass-through swaps (goals beyond each other),
mid-flight meetings (both had a route when planned), three robots, top aisle, pallet dead end."""
import sys, tempfile
sys.path.insert(0, "/Users/bakarmac/projects/physical_ai/warehouse-digital-twin")
from backend.digital_twin import DigitalTwin
from backend.models import SimulationStatus, TaskStatus
from backend.simulator import Simulator


def floor():
    tmp = tempfile.mkdtemp()
    twin = DigitalTwin(log_dir=tmp + "/l", data_dir=tmp + "/d", persist_logs=False, demo=True, demo_tasks=False,
                       layout="distribution_center")
    sim = Simulator(twin); twin.simulation_status = SimulationStatus.RUNNING
    return twin, sim


def run(label, robots, moves, max_ticks=4000, stagger=None):
    twin, sim = floor()
    made = {}
    for key, kw in robots:
        made[key] = twin.add_robot(**kw)
    tasks = []
    for i, (key, dest) in enumerate(moves):
        if stagger and i in stagger:
            for _ in range(stagger[i]):
                sim.tick()
        tasks.append(twin.tasks.create_task({"type": "MOVE_ROBOT", "robot_id": made[key].id, "destination": dest}))
    for tick in range(max_ticks):
        if all(t.is_terminal for t in tasks):
            break
        sim.tick()
    states = [(t.status.value, t.error) for t in tasks]
    ok = all(t.status is TaskStatus.COMPLETED for t in tasks)
    errs = [r.name for r in twin.robots.values() if r.status.value == "ERROR"]
    print(f"{'OK ' if ok else 'BAD'} {label}: ticks={tick} {states if not ok else ''} errors={errs} "
          f"positions={[ (k, made[k].position, made[k].status.value) for k in made] if not ok else ''}")


AMR1 = dict(name="TR50-201", asset_id="AST-000201")
AMR2 = dict(name="TR50-101", asset_id="AST-000101")
FK1 = dict(name="PF1200-205", asset_id="AST-000205")
FK2 = dict(name="PF1200-206", asset_id="AST-000206")

# pass-through in tote aisle 1, both stationary at start
run("pass-through tote aisle 1 (9,13)->(16,13) vs (15,13)->(8,13)",
    [("a", dict(AMR1, position=(9, 13))), ("b", dict(AMR2, position=(15, 13)))],
    [("a", "16,13"), ("b", "8,13")])
# mid-flight meeting: both enter the aisle from opposite ends with routes planned on an empty aisle
run("mid-flight meet tote aisle 1 (7,13)->(18,13) vs (18,14)->(7,14)",
    [("a", dict(AMR1, position=(7, 13))), ("b", dict(AMR2, position=(18, 14)))],
    [("a", "18,13"), ("b", "7,14")])
run("mid-flight meet tote aisle 2 (7,15)->(18,15) vs (18,16)->(7,15)... b to (6,15)",
    [("a", dict(AMR1, position=(7, 15))), ("b", dict(AMR2, position=(18, 16)))],
    [("a", "18,15"), ("b", "6,15")])
# top aisle
run("top aisle (8,1)->(18,4) vs (18,4)... b at (18,3)->(7,1)",
    [("a", dict(AMR1, position=(8, 1))), ("b", dict(AMR2, position=(18, 3)))],
    [("a", "18,4"), ("b", "7,1")])
# forklifts in the 2-wide pallet aisle, pass-through
run("forklifts pallet aisle 2 (9,7)->(15,8) vs (15,8)->(9,7)",
    [("a", dict(FK1, position=(9, 7))), ("b", dict(FK2, position=(15, 8)))],
    [("a", "15,8"), ("b", "9,7")])
# three robots: two heading east, one west, in tote aisle 1
run("three in tote aisle 1",
    [("a", dict(AMR1, position=(8, 13))), ("b", dict(AMR2, position=(10, 13))),
     ("c", dict(name="Extra", model_code="AC-TR50", position=(16, 13)))],
    [("a", "15,13"), ("b", "16,13"), ("c", "9,13")])
# mid-flight with stagger: b starts later so they meet in the middle
run("staggered mid-flight tote aisle 3 (7,17)->(18,17) vs (18,18)->(7,17)",
    [("a", dict(AMR1, position=(7, 17))), ("b", dict(AMR2, position=(18, 18)))],
    [("a", "18,17"), ("b", "6,15")], stagger={1: 15})
