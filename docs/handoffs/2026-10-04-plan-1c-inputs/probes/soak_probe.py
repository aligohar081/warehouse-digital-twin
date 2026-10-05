"""Read-only integration probe: a fault-free shift on the new floor (seed 42,
pace 2), the full fleet and crew, then a census of robots, orders, tasks and
evaluation verdicts. Writes nothing into the repo (logs go to a temp dir)."""
import collections
import json
import sys
import tempfile
import time

sys.path.insert(0, "/Users/bakarmac/projects/physical_ai/warehouse-digital-twin")

from backend.ci_engine import CIEngine  # noqa: E402
from backend.digital_twin import DigitalTwin  # noqa: E402
from backend.eval_engine import EMBODIMENT_CHECKS, Verdict, evaluate_events  # noqa: E402
from backend.models import SimulationStatus  # noqa: E402
from backend.simulator import Simulator  # noqa: E402

TICKS = int(sys.argv[1]) if len(sys.argv) > 1 else 20000
PACE = float(sys.argv[2]) if len(sys.argv) > 2 else 2.0

tmp = tempfile.mkdtemp()
twin = DigitalTwin(log_dir=tmp + "/logs", data_dir=tmp + "/data", persist_logs=False, demo=True,
                   demo_tasks=False, layout="distribution_center")
for name, asset, cell in (("PF1200-205", "AST-000205", (7, 4)), ("PF1200-206", "AST-000206", (7, 8)),
                          ("HH300-207", "AST-000207", (2, 8)), ("TR50-201", "AST-000201", (8, 13)),
                          ("TR50-101", "AST-000101", (8, 15)), ("PK30-203", "AST-000203", (20, 14)),
                          ("IX2-208", "AST-000208", (20, 2)), ("IX2-209", "AST-000209", (21, 2)),
                          ("SC1-204", "AST-000204", (7, 6)), ("H1-212", "AST-000212", (22, 7))):
    twin.add_robot(name=name, asset_id=asset, position=cell)
twin.add_robot(name="CX10-210", asset_id="AST-000210")
twin.add_robot(name="CX10-211", asset_id="AST-000211")
for number in range(6):
    twin.add_box(name=f"TOTE-{number}", kind="TOTE", sku=f"SKU-00{number + 1}", quantity=20, weight=21.5,
                 slot=f"TS-{8 + number:02d}-12-0")
for number in range(3):
    twin.add_box(name=f"PAL-{number}", kind="PALLET", sku=f"SKU-00{number + 1}", quantity=40, weight=500.0,
                 slot=f"PR-{10 + number:02d}-05-0")
for number in range(1, 11):
    twin.add_operator(name=f"W{number:02d}", worker_id=f"E-{10000 + number}")

sim = Simulator(twin)
twin.simulation_status = SimulationStatus.RUNNING
twin.shift.configure(pace=PACE)
twin.shift.start()

first_flat = []
t0 = time.time()
for i in range(TICKS):
    sim.tick()
    if i % 500 == 0:
        for robot in twin.robots.values():
            if robot.last_error == "Battery depleted" and robot.id not in [r for r, _ in first_flat]:
                first_flat.append((robot.id, twin.tick_count))
elapsed = time.time() - t0

out = {"ticks": TICKS, "pace": PACE, "sim_minutes": round(twin.simulation_time / 60, 1),
       "mean_tick_ms": round(1000 * elapsed / TICKS, 2)}
out["robots"] = {r.name: {"status": r.status.value, "err": r.last_error, "carrying": r.carrying_box,
                          "battery": round(r.battery, 1), "task": r.current_task,
                          "wait": r.wait_reason, "pos": r.position, "layer": r.layer}
                 for r in twin.robots.values()}
out["first_flat"] = first_flat
idle_carrying = [r.name for r in twin.robots.values() if r.current_task is None and r.carrying_box]
out["idle_carrying"] = idle_carrying
counts = twin.shift.orders.counts()
out["orders"] = counts
fail_reasons = collections.Counter((o.kind, (o.failure_reason or "")[:110]) for o in twin.shift.orders.orders.values()
                                   if o.status == "FAILED")
out["order_failures"] = fail_reasons.most_common(12)
stuck = []
for o in twin.shift.orders.orders.values():
    if o.is_terminal:
        continue
    age = twin.simulation_time - o.created_at
    if age > 900:
        waiting = [(s.name, s.status, s.attempts, s.error and s.error[:80]) for s in o.stages if s.status != "DONE"][:3]
        stuck.append((o.order_id, o.kind, o.status, round(age), o.pick_station, o.pack_cell, waiting))
out["stuck_orders_over_15min"] = stuck[:15]
out["stuck_count"] = len(stuck)
task_fail = collections.Counter((t.type.value, (t.error or "")[:100]) for t in twin.tasks.tasks.values()
                                if t.status.value == "FAILED")
out["task_failures"] = task_fail.most_common(15)
out["escalations"] = len(twin.events.query(event_type="SAFETY_WAIT_ESCALATED", limit=100000))
verdicts = collections.Counter()
examples = {}
for task in twin.tasks.tasks.values():
    if not task.is_terminal:
        continue
    report = evaluate_events(twin.events.query(task_id=task.id, limit=5000), task_id=task.id)
    for check in report.checks:
        if check.verdict is Verdict.FAIL:
            verdicts[check.name] += 1
            examples.setdefault(check.name, (task.id, task.type.value, check.message[:160]))
out["check_fails"] = dict(verdicts)
out["check_fail_examples"] = examples
ci = CIEngine(twin).run()
out["ci_failed"] = {c["name"]: c["message"][:200] for c in ci["checks"] if not c["passed"]}
boxes = collections.Counter((b.kind.value, b.status.value) for b in twin.boxes.values())
out["boxes"] = {f"{k}/{s}": n for (k, s), n in sorted(boxes.items())}
out["stranded_totes"] = [(b.name, b.status.value, b.position, b.slot) for b in twin.boxes.values()
                         if b.kind.value == "TOTE" and b.slot and not (b.position == twin.warehouse.slot(b.slot).cell)
                         and twin.tasks.active_task_for_box(b.id) is None]
print(json.dumps(out, indent=1, default=str))
