"""Why does the 150-min fault-free soak plateau (CUSTOMER DONE 40->43 over the last ~45 min)?
Dumps the live orders, their stages and tasks, and the robots' waits at the end."""
import sys, tempfile, collections
sys.path.insert(0, "/Users/bakarmac/projects/physical_ai/warehouse-digital-twin")
from backend.digital_twin import DigitalTwin
from backend.faults import FAULT_RISKS
from backend.models import CONFIG, SimulationStatus, BoxKind
from backend.simulator import Simulator
from backend.jobs import tote_is_home
for risk in ("COLLISION_RISK", "FALSE_SUCCESS_RISK", *FAULT_RISKS.values()):
    CONFIG[risk] = 0.0
TICKS = int(sys.argv[1]) if len(sys.argv) > 1 else 60000
PACE = float(sys.argv[2]) if len(sys.argv) > 2 else 1.0
FLEET = (("PF1200-205", "AST-000205", (7, 4)), ("PF1200-206", "AST-000206", (7, 8)),
         ("HH300-207", "AST-000207", (2, 8)), ("TR50-201", "AST-000201", (8, 13)),
         ("TR50-101", "AST-000101", (8, 15)), ("PK30-203", "AST-000203", (20, 14)),
         ("IX2-208", "AST-000208", (20, 2)), ("IX2-209", "AST-000209", (21, 2)),
         ("SC1-204", "AST-000204", (7, 6)), ("H1-212", "AST-000212", (22, 7)))
tmp = tempfile.mkdtemp()
twin = DigitalTwin(log_dir=tmp + "/logs", data_dir=tmp + "/data", persist_logs=False,
                   demo=True, demo_tasks=False, layout="distribution_center")
for name, asset, cell in FLEET:
    twin.add_robot(name=name, asset_id=asset, position=cell)
twin.add_robot(name="CX10-210", asset_id="AST-000210")
twin.add_robot(name="CX10-211", asset_id="AST-000211")
for n in range(6):
    twin.add_box(name=f"TOTE-{n}", kind="TOTE", sku=f"SKU-00{n + 1}", quantity=20, weight=21.5, slot=f"TS-{8 + n:02d}-12-0")
for n in range(3):
    twin.add_box(name=f"PAL-{n}", kind="PALLET", sku=f"SKU-00{n + 1}", quantity=40, weight=500.0, slot=f"PR-{10 + n:02d}-05-0")
for n in range(1, 11):
    twin.add_operator(name=f"W{n:02d}", worker_id=f"E-{10000 + n}")
sim = Simulator(twin); twin.simulation_status = SimulationStatus.RUNNING
twin.shift.configure(pace=PACE); twin.shift.start()
h1 = twin.find_robot("H1-212")
h1_trace = []
for tick in range(1, TICKS + 1):
    sim.tick()
    if tick > TICKS - 12000 and tick % 1000 == 0:
        h1_trace.append((tick, h1.position, h1.status.value, h1.current_task, h1.wait_reason, h1.blocked_by))
print("H1 trace:")
for row in h1_trace:
    print("  ", row)
orders = twin.shift.orders
for o in orders.orders.values():
    if o.kind == "CUSTOMER" and o.status == "IN_PROGRESS":
        print("IN_PROGRESS", o.order_id, o.lines, o.pick_station, o.pack_cell, "age min",
              round((twin.simulation_time - o.created_at) / 60, 1))
        for s in o.stages:
            t = twin.tasks.get(s.task_id) if s.task_id else None
            print("    ", s.name, s.status, s.attempts, t.status.value if t else None, t.robot_id if t else None,
                  (t.history[-1]["message"] if t else ""))
op = [o for o in orders.orders.values() if o.kind == "CUSTOMER" and o.status == "OPEN"]
print("OPEN", len(op), collections.Counter(o.lines[0]["sku"] for o in op))
for sku in sorted({o.lines[0]["sku"] for o in op}):
    locs = [(l.box_id, l.recorded_qty) for l in twin.stock.locations(sku)]
    print("  stock", sku, locs)
print("busy stations", orders._busy("pick_station"), orders._busy("pack_cell"))
print("totes", [(b.name, b.sku, b.quantity, b.status.value, b.position, tote_is_home(twin, b)) for b in twin.boxes.values() if b.kind is BoxKind.TOTE])
print("operators", [(o.name, o.status.value, o.zone) for o in twin.operators.values()])
