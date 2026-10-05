"""Diagnose a RETURN_TOTE left PLANNING (waiting for a robot) while the AMRs idle in parking,
in the 150-min fault-free soak replica. Also: when did it start waiting?"""
import sys, runpy
sys.argv = ["x", "0", "1.0"]
ns = runpy.run_path("/Users/bakarmac/projects/physical_ai/warehouse-digital-twin/.superpowers/sdd/2026-10-01-multi-embodiment-1b-jobs-rules-shift/tools/final-probes/rereview/plateau_probe.py")
twin, sim = ns["twin"], ns["sim"]
first_stuck = None
for tick in range(1, 60001):
    sim.tick()
    if tick % 500 == 0:
        waiting = [t for t in twin.tasks.pending() if t.type.value == "RETURN_TOTE"]
        idle_amrs = [r for r in twin.robots.values() if r.mobility and r.mobility.embodiment_class in ("AMR", "HUMANOID")
                     and r.current_task is None and r.status.value == "IDLE"]
        if waiting and idle_amrs:
            if first_stuck is None:
                first_stuck = (tick, [t.id for t in waiting])
                print("first seen", first_stuck, [(r.name, r.position, round(r.battery, 1)) for r in idle_amrs])
        elif first_stuck and not waiting:
            pass
waiting = [t for t in twin.tasks.pending() if t.type.value == "RETURN_TOTE"]
for task in waiting:
    print("PENDING", task.id, task.box_id, task.history[-3:], "created", task.created_at if hasattr(task, "created_at") else None)
    scored, label = twin.tasks._score_candidates(task)
    print("  scored:", [(c[1].name, c[2]) for c in scored], label)
    for r in twin.robots.values():
        if r.mobility and r.mobility.embodiment_class in ("AMR", "HUMANOID"):
            print("  ", r.name, r.position, r.status.value, r.current_task, round(r.battery, 1), "avail", r.is_available,
                  "cap:", twin.tasks.capability_reason(r, task), "reach:", twin.tasks._reach(task, r))
